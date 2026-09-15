"""Structured-data-first discovery adapters for journal collection calls."""

from __future__ import annotations

import json
import re
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from functools import wraps
from threading import Lock
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen


Fetcher = Callable[[str], str]


def _request_text(url: str) -> str:
    if not _safe_url(url):
        raise ValueError("Only public HTTP(S) source URLs are supported")
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/ld+json,application/json;q=0.9,*/*;q=0.5",
            "Accept-Language": "en-US,en;q=0.8",
            "User-Agent": "ResearchAssistant/12.1 (public call-for-papers discovery)",
        },
    )
    with urlopen(request, timeout=20) as response:  # noqa: S310 - fixed public publisher URLs
        return response.read().decode("utf-8", errors="replace")


def _fetch_resilient(fetcher: Fetcher, url: str, *, minimum_length: int = 500) -> str:
    """Read once; access restrictions are evidence of failure, never a retry route."""
    del minimum_length  # A short valid structured response is not a transport failure.
    if not _safe_url(url):
        raise ValueError("Only HTTP(S) source URLs without credentials are supported")
    text = fetcher(url)
    folded = text[:12000].casefold()
    blocked = any(
        marker in folded
        for marker in (
            "just a moment",
            "verify you are human",
            "g-recaptcha",
            "hcaptcha",
            "cookies are not supported",
            "problem providing the content",
        )
    )
    if blocked:
        raise OSError("Source access challenge; automated access is unavailable")
    if not text.strip():
        raise OSError("Source returned an empty response")
    return text


def _safe_url(value: Any, base_url: str = "") -> str:
    try:
        raw = unescape(str(value or "")).strip()
        if not raw:
            return ""
        url = urljoin(base_url, raw)
        parsed = urlsplit(url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return ""
        return url
    except ValueError:
        return ""


def _domain_is(url: str, *domains: str) -> bool:
    if not _safe_url(url):
        return False
    host = (urlsplit(url).hostname or "").casefold().rstrip(".")
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _identity_url(url: str) -> str:
    if not _safe_url(url):
        return ""
    parsed = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
             if not key.casefold().startswith("utm_") and key.casefold() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}]
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, urlencode(query), ""))


class _TextParser(HTMLParser):
    excluded = {"script", "style", "noscript", "template", "svg", "nav", "footer", "header", "aside"}
    blocks = {"p", "div", "section", "article", "h1", "h2", "h3", "h4", "li", "br"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.excluded:
            self.hidden.append(tag)
        elif not self.hidden and tag in self.blocks:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
        elif tag in self.blocks:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def _paragraphs(value: Any) -> list[str]:
    parser = _TextParser()
    parser.feed(str(value or ""))
    return [cleaned for line in "".join(parser.parts).splitlines() if (cleaned := " ".join(line.split()))]


def _clean_html(value: Any) -> str:
    return " ".join(_paragraphs(value))


def _scope_fields(value: Any, *, complete: bool = False) -> dict[str, Any]:
    paragraphs = _paragraphs(value)
    scope = "\n\n".join(paragraphs)
    return {"scope_text": scope, "scope_is_complete": bool(scope and complete),
            "scope_status": "full" if scope and complete else "snippet" if scope else "missing",
            "scope_version": hashlib.sha256(scope.encode("utf-8")).hexdigest() if scope else "",
            "scope_paragraphs": [{"id": f"p{index + 1}", "text": text} for index, text in enumerate(paragraphs)]}


def _element_inner_html(text: str, marker: str) -> str:
    """Return a marked element's complete inner HTML, including nested same-name tags."""
    opening = re.search(
        rf'<(?P<tag>section|div)\b[^>]*(?:data-test|id|class)=["\'][^"\']*(?:{marker})[^"\']*["\'][^>]*>',
        str(text or ""),
        re.I | re.S,
    )
    if not opening:
        return ""
    tag = opening.group("tag")
    depth = 1
    for token in re.finditer(rf'</?{tag}\b[^>]*>', text[opening.end():], re.I | re.S):
        value = token.group(0)
        if value.startswith("</"):
            depth -= 1
            if depth == 0:
                return text[opening.end(): opening.end() + token.start()]
        elif not value.rstrip().endswith("/>"):
            depth += 1
    return ""


def _original_metadata(node: dict[str, Any]) -> dict[str, str]:
    identifier = _first(node, "source_record_id", "identifier", "contentId", "id", "@id")
    if isinstance(identifier, dict):
        identifier = _first(identifier, "value", "@id")
    return {"source_record_id": str(identifier or ""),
            "published_at": str(_first(node, "published_at", "datePublished", "date", "published") or ""),
            "updated_at": str(_first(node, "updated_at", "dateModified", "modified", "updated") or "")}


class SourceFetchResult(list):
    """List-compatible discovery batch with explicit endpoint outcomes."""

    def __init__(self, rows: list[dict[str, Any]], requests: list[dict[str, Any]]) -> None:
        super().__init__(rows)
        self.attempted_requests = len(requests)
        self.successful_requests = sum(item["status"] == "success" for item in requests)
        self.errors = [{key: item[key] for key in ("url", "stage", "error")} for item in requests if item["status"] == "failed"]
        self.status = "partial" if self.errors and (rows or self.successful_requests) else "failed" if self.errors else "success"


def _source_batch(function):
    @wraps(function)
    def fetch(self, *args, **kwargs):
        outer = not getattr(self, '_batch_depth', 0)
        if outer:
            self._requests = []
        self._batch_depth = getattr(self, '_batch_depth', 0) + 1
        try:
            rows = function(self, *args, **kwargs)
        except Exception as error:
            self._record_error('', 'parse', error)
            rows = []
        finally:
            self._batch_depth -= 1
        return SourceFetchResult(rows, self._requests) if outer else rows
    return fetch


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _json_payloads(text: str) -> list[Any]:
    payloads: list[Any] = []
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            payloads.append(json.loads(stripped))
        except json.JSONDecodeError:
            pass
    for match in re.finditer(
        r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        try:
            payloads.append(json.loads(unescape(match.group(1)).strip()))
        except json.JSONDecodeError:
            continue
    return payloads


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], {}):
            return value
    return ""


def _first_list_value(mapping: dict[str, Any], *keys: str) -> Any:
    value = _first(mapping, *keys)
    if isinstance(value, list):
        return value[0] if value else ""
    return value


def _json_document(text: str) -> Any:
    try:
        return json.loads(str(text or "").lstrip())
    except (json.JSONDecodeError, TypeError):
        return None


def _discovery_queries(keywords: list[str] | None, *, limit: int = 6) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in keywords or []:
        value = " ".join(str(raw or "").split()).strip()
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value[:120])
        if len(result) >= limit:
            break
    return result


def _expanded_discovery_queries(keywords: list[str] | None, *, limit: int = 6) -> list[str]:
    phrases = _discovery_queries(keywords, limit=min(3, limit))
    result = list(phrases)
    seen = {value.casefold() for value in result}
    for phrase in _discovery_queries(keywords, limit=12):
        for token in re.findall(r"[a-z0-9]+", phrase.casefold()):
            if len(token) < 4 or token in seen:
                continue
            seen.add(token)
            result.append(token)
            if len(result) >= limit:
                return result
    return result


def _keyword_relevance(row: dict[str, Any], keywords: list[str] | None) -> int:
    haystack = " ".join(
        str(row.get(field, "")) for field in ("title", "scope_text", "journal")
    ).casefold()
    canonical = " ".join(re.sub(r"[^a-z0-9]+", " ", haystack).split())
    canonical_tokens = set(canonical.split())
    padded_canonical = f" {canonical} "
    score = 0
    for raw in keywords or []:
        phrase = " ".join(re.sub(r"[^a-z0-9]+", " ", str(raw).casefold()).split())
        if not phrase:
            continue
        if f" {phrase} " in padded_canonical:
            score += 12
        tokens = {token for token in phrase.split() if len(token) >= 3}
        score += sum(2 for token in tokens if token in canonical_tokens)
    return score


def _rank_relevant(
    rows: list[dict[str, Any]],
    keywords: list[str] | None,
    *,
    limit: int,
    exploratory: int = 0,
    minimum_score: int = 1,
) -> list[dict[str, Any]]:
    ranked = sorted(
        rows,
        key=lambda row: (
            -_keyword_relevance(row, keywords),
            str(row.get("deadline", "9999-99-99")),
            str(row.get("title", "")).casefold(),
        ),
    )
    if not keywords:
        return ranked[:limit]
    relevant = [row for row in ranked if _keyword_relevance(row, keywords) >= minimum_score]
    unrelated = [row for row in ranked if _keyword_relevance(row, keywords) < minimum_score]
    return [*relevant[:limit], *unrelated[: max(0, exploratory)]][: limit + max(0, exploratory)]


def _nested_name(value: Any) -> str:
    if isinstance(value, dict):
        return _clean_html(_first(value, "name", "title", "headline"))
    if isinstance(value, list):
        return _nested_name(value[0]) if value else ""
    return _clean_html(value)


def _structured_records(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    deadline_keys = ("dateExpires", "submissionDeadline", "deadline", "endDate", "dateEnd")
    for payload in _json_payloads(text):
        for node in _walk(payload):
            deadline = _first(node, *deadline_keys)
            title = _first(node, "name", "headline", "title")
            if not title or not deadline:
                continue
            journal = _nested_name(_first(node, "isPartOf", "periodical", "journal", "container"))
            rows.append(
                {
                    **_original_metadata(node),
                    "title": _clean_html(title),
                    **_scope_fields(_first(node, "description", "abstract", "scope", "text")),
                    "deadline": _clean_html(deadline),
                    "official_url": _clean_html(_first(node, "url", "sameAs", "mainEntityOfPage")),
                    "journal": journal,
                    "issn": _first(node, "issn", "ISSN"),
                    "publisher": _nested_name(node.get("publisher")),
                    "type": _clean_html(_first(node, "collectionType", "@type", "type")),
                }
            )
    return rows


def _fallback_records(text: str, base_url: str) -> list[dict[str, Any]]:
    """Best-effort fallback for publisher listings without JSON-LD."""
    rows: list[dict[str, Any]] = []
    date_pattern = r"(?:submission\s+)?deadline\s*[:：]?\s*([0-3]?\d\s+[A-Za-z]+\s+20\d{2}|[A-Za-z]+\s+[0-3]?\d(?:st|nd|rd|th)?,?\s+20\d{2}|20\d{2}-\d{1,2}-\d{1,2})"
    anchors = list(re.finditer(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", text, re.I | re.S))
    for anchor in anchors:
        title = _clean_html(anchor.group(2))
        if len(title) < 12:
            continue
        nearby = text[anchor.end() : anchor.end() + 1800]
        deadline = re.search(date_pattern, _clean_html(nearby), re.I)
        if not deadline:
            continue
        url = unescape(anchor.group(1)).strip()
        if url.startswith("/"):
            match = re.match(r"(https?://[^/]+)", base_url)
            url = (match.group(1) if match else base_url.rstrip("/")) + url
        rows.append({"title": title, "deadline": deadline.group(1), "official_url": url, "scope_text": ""})
    return rows


class _BaseSource:
    source_id = "source"
    publisher = ""
    default_type = "special_issue"
    urls: tuple[str, ...] = ()
    is_aggregator = False

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if 'fetch' in cls.__dict__:
            cls.fetch = _source_batch(cls.fetch)

    def __init__(self, *, fetcher: Fetcher | None = None, urls: list[str] | None = None) -> None:
        self._transport = fetcher or _request_text
        self._request_lock = Lock()
        self._requests: list[dict[str, Any]] = []
        self._fetcher = self._tracked_fetch
        self._urls = tuple(urls) if urls is not None else self.urls

    def _record_error(self, url: str, stage: str, error: Any) -> None:
        with self._request_lock:
            for row in reversed(self._requests):
                if row['url'] == url and row['status'] == 'success':
                    row.update(status='failed', stage=stage, error=str(error)[:240])
                    return
            self._requests.append({'url': url, 'status': 'failed', 'stage': stage, 'error': str(error)[:240]})

    def _tracked_fetch(self, url: str) -> str:
        try:
            text = _fetch_resilient(self._transport, url)
        except Exception as error:
            self._record_error(url, 'request', error)
            raise
        with self._request_lock:
            self._requests.append({'url': url, 'status': 'success', 'stage': 'request'})
        return text

    def _decorate(self, discovered: list[dict[str, Any]], url: str, response_text: str = "") -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for raw in discovered:
            row = dict(raw)
            row["source"] = self.source_id
            row["source_id"] = self.source_id
            row.setdefault("source_url", url)
            row.setdefault("discovery_url", url)
            row.update({key: value for key, value in _original_metadata(row).items() if key not in row})
            row["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec='seconds')
            official = _identity_url(_safe_url(row.get('official_url'), url))
            if self.is_aggregator and (not official or urlsplit(official).hostname == urlsplit(url).hostname):
                row['discovery_url'] = official or row.get('discovery_url', url)
                official = ''
            row['official_url'] = official
            if response_text:
                row["response_sha256"] = hashlib.sha256(response_text.encode()).hexdigest()
            if 'scope_is_complete' not in row:
                row.update(_scope_fields(row.get('scope_text', '')))
            row['provenance'] = [{'source_id': self.source_id, 'source_record_id': row.get('source_record_id', ''),
                                  'url': row['discovery_url'],
                                  'published_at': row.get('published_at', ''), 'updated_at': row.get('updated_at', ''),
                                  'fetched_at': row['fetched_at']}]
            row["publisher"] = str(row.get("publisher", "")).strip() or self.publisher
            raw_type = str(row.get("type", "")).casefold()
            if not any(token in raw_type for token in ("special", "topical", "research topic", "article collection")):
                row["type"] = self.default_type
            row["is_aggregator"] = bool(row.get("is_aggregator", self.is_aggregator))
            rows.append(row)
        return rows

    @staticmethod
    def _dedupe(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            key = (_identity_url(row.get("official_url") or row.get('discovery_url', '')).rstrip('/'), str(row.get("title", "")).casefold())
            if key not in seen:
                seen.add(key)
                unique.append(row)
        return unique

    @_source_batch
    def fetch(
        self,
        *,
        since: datetime,
        progress: Callable[..., None] | None = None,
        keywords: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        del since  # Publisher pages expose current open calls; checkpointing is handled by the cache layer.
        del keywords
        rows: list[dict[str, Any]] = []
        pending = list(self._urls)
        visited = set()
        for index, url in enumerate(pending, start=1):
            if url in visited or len(visited) >= 6:
                continue
            visited.add(url)
            if progress:
                progress(f"正在检查 {self.source_id}（{index}/{len(self._urls)}）…", int(index * 100 / max(1, len(self._urls))))
            try:
                text = self._fetcher(url)
            except Exception as error:  # noqa: BLE001 - one source must not abort the refresh
                if progress:
                    progress(f"{self.source_id} 来源失败，已继续：{error}", 0)
                continue
            discovered = _structured_records(text) or _fallback_records(text, url)
            if not discovered and text.lstrip().startswith("<"):
                self._record_error(url, "parse", "Publisher page did not contain recognizable call records")
            rows.extend(self._decorate(discovered, url, text))
            for anchor in re.findall(r'<a\b[^>]*>', text, re.I):
                if not re.search(r'rel=[\"\']next[\"\']', anchor, re.I):
                    continue
                link = re.search(r'href=[\"\']([^\"\']+)', anchor, re.I)
                next_url = _safe_url(link.group(1), url) if link else ''
                if next_url and urlsplit(next_url).netloc == urlsplit(url).netloc and next_url not in visited:
                    pending.append(next_url)
                elif next_url and urlsplit(next_url).netloc != urlsplit(url).netloc:
                    self._record_error(next_url, "pagination", "Cross-origin pagination link was not followed")
        return self._dedupe(rows)


class FrontiersResearchTopicsSource(_BaseSource):
    source_id = "frontiers"
    publisher = "Frontiers"
    default_type = "research_topic"
    urls = (
        "https://www.frontiersin.org/journals/soil-science/research-topics",
        "https://www.frontiersin.org/journals/environmental-science/research-topics",
        "https://www.frontiersin.org/journals/agronomy/research-topics",
    )

    @staticmethod
    def _listing_topics(text: str) -> list[tuple[str, str]]:
        topics: list[tuple[str, str]] = []
        for block in re.findall(r'<article[^>]*class="[^"]*CardResearchTopic[^"]*"[^>]*>(.*?)</article>', text, re.I | re.S):
            if "Submission open" not in block:
                continue
            link = re.search(r'<a[^>]+href="(https://www\.frontiersin\.org/research-topics/\d+/[^"]+)"', block, re.I)
            title = re.search(r'CardResearchTopic__title[^>]*>(.*?)</h2>', block, re.I | re.S)
            if link and title:
                topics.append((link.group(1), _clean_html(title.group(1))))
        return topics

    @staticmethod
    def _keyword_score(title: str, keywords: list[str]) -> int:
        canonical = " ".join(re.sub(r"[^a-z0-9]+", " ", title.casefold()).split())
        score = 0
        for keyword in keywords:
            term = " ".join(re.sub(r"[^a-z0-9]+", " ", str(keyword).casefold()).split())
            if not term:
                continue
            if term in canonical:
                score += 8
            score += sum(1 for token in term.split() if len(token) >= 4 and token in canonical)
        return score

    def _topic_detail(self, url: str) -> dict[str, Any] | None:
        text = self._fetcher(url)
        if "currently accepting articles" not in text:
            return None
        title_match = re.search(r"<title>\s*Frontiers\s*\|\s*(.*?)</title>", text, re.I | re.S)
        deadline_match = re.search(r"Manuscript Submission Deadline\s+([0-3]?\d\s+[A-Za-z]+\s+20\d{2})", _clean_html(text), re.I)
        if not title_match or not deadline_match:
            return None
        journal_match = re.search(r'/journals/[^"\']+"\s+aria-label="(Frontiers in [^"]+)"', text, re.I)
        background_match = re.search(
            r'RTOverviewBackground__title[^>]*>\s*Background\s*</h3>(.*?)(?:RTOverviewKeywords|Topic editors|RTOverviewEditors)',
            text,
            re.I | re.S,
        )
        scope = _clean_html(background_match.group(1)) if background_match else ""
        return {
            "title": _clean_html(title_match.group(1)),
            "deadline": deadline_match.group(1),
            "official_url": url,
            **_scope_fields(background_match.group(1) if background_match else scope, complete=bool(background_match)),
            "journal": _clean_html(journal_match.group(1)) if journal_match else "",
            "publisher": "Frontiers",
            "fee_mode": "apc",
            "type": "research_topic",
        }

    def fetch(self, *, since: datetime, progress: Callable[..., None] | None = None, keywords: list[str] | None = None) -> list[dict[str, Any]]:
        del since
        keywords = [str(value) for value in (keywords or []) if str(value).strip()]
        selected: list[tuple[str, str, str]] = []
        direct: list[dict[str, Any]] = []
        for index, url in enumerate(self._urls, start=1):
            if progress:
                progress(f"正在读取 Frontiers 专题目录（{index}/{len(self._urls)}）…", int(index * 25 / max(1, len(self._urls))))
            try:
                text = self._fetcher(url)
            except Exception as error:
                if progress:
                    progress(f"Frontiers 目录暂不可用，已继续：{error}", 0)
                continue
            structured = _structured_records(text)
            if structured:
                direct.extend(self._decorate(structured, url))
                continue
            topics = self._listing_topics(text)
            topics.sort(key=lambda value: (-self._keyword_score(value[1], keywords), value[1].casefold()))
            positive = [value for value in topics if self._keyword_score(value[1], keywords) > 0]
            chosen = (positive[:10] if positive else topics[:5])
            selected.extend((detail_url, title, url) for detail_url, title in chosen)
        if direct:
            return self._dedupe(direct)
        rows: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=6) as executor:
            jobs = {executor.submit(self._topic_detail, url): (url, listing_url) for url, _title, listing_url in selected}
            for completed, future in enumerate(as_completed(jobs), start=1):
                url, listing_url = jobs[future]
                try:
                    row = future.result()
                except Exception as error:
                    if progress:
                        progress(f"Frontiers 专题详情暂不可用，已继续：{error}", 0)
                    continue
                if row:
                    decorated = self._decorate([row], listing_url)[0]
                    decorated["official_url"] = url
                    rows.append(decorated)
                if progress:
                    progress(f"正在读取 Frontiers 专题详情（{completed}/{len(jobs)}）…", 25 + int(completed * 75 / max(1, len(jobs))))
        return self._dedupe(rows)


class ElsevierCallsSource(_BaseSource):
    source_id = "elsevier"
    publisher = "Elsevier"
    urls = ("https://www.sciencedirect.com/browse/calls-for-papers",)

    reader_url = "https://r.jina.ai/http://www.sciencedirect.com/browse/calls-for-papers"

    @staticmethod
    def _initial_state_records(text: str, base_url: str) -> list[dict[str, Any]]:
        marker = "window.INITIAL_STATE"
        offset = text.find(marker)
        if offset < 0:
            return []
        equals = text.find("=", offset + len(marker))
        if equals < 0:
            return []
        try:
            payload, _end = json.JSONDecoder().raw_decode(text[equals + 1 :].lstrip())
        except (json.JSONDecodeError, TypeError):
            return []
        cfp_list: list[Any] = []
        for node in _walk(payload):
            candidate = node.get("cfpList")
            if isinstance(candidate, list):
                cfp_list = candidate
                break
        rows: list[dict[str, Any]] = []
        for item in cfp_list:
            if not isinstance(item, dict):
                continue
            title = _clean_html(item.get("title", ""))
            deadline = _clean_html(item.get("submissionDeadline", item.get("expiryDate", "")))
            if not title or not deadline:
                continue
            journal = item.get("journal", {}) if isinstance(item.get("journal"), dict) else {}
            path = str(item.get("url", "")).strip()
            content_id = str(item.get("contentId", "")).strip()
            if content_id and path and not path.startswith(("http://", "https://")):
                official_url = urljoin(base_url, f"/special-issue/{content_id}/{path.lstrip('/')}")
            else:
                official_url = urljoin(base_url, path) if path else base_url
            rows.append(
                {
                    **_original_metadata(item),
                    "title": title,
                    "deadline": deadline,
                    "official_url": official_url,
                    "scope_text": _clean_html(item.get("summary", "")),
                    "journal": _clean_html(_first(journal, "displayName", "title", "name")),
                    "issn": _first(journal, "issn", "issnL"),
                    "publisher": "Elsevier",
                    "type": "special_issue",
                }
            )
        return rows

    @staticmethod
    def _reader_records(text: str, official_url: str) -> list[dict[str, Any]]:
        lines = [_clean_html(line) for line in str(text or "").splitlines()]
        lines = [line for line in lines if line]
        start = next(
            (index + 1 for index, line in enumerate(lines) if re.match(r"Browse\s+\d+\s+calls for papers", line, re.I)),
            0,
        )
        rows: list[dict[str, Any]] = []
        block: list[str] = []
        deadline_pattern = re.compile(r"Submission deadline\s*[:：]\s*(.+)$", re.I)
        controls = re.compile(
            r"^(?:filter|refine|select|all subject|all secondary|selected$|skip to|journals? & books|help$|search$|my account|sign in)",
            re.I,
        )
        for line in lines[start:]:
            deadline_match = deadline_pattern.search(line)
            if not deadline_match:
                block.append(line)
                continue
            deadline = deadline_match.group(1).strip()
            candidates = [value for value in block if not controls.search(value)]
            metric_index = next(
                (
                    index
                    for index, value in enumerate(candidates)
                    if re.search(r"(?:•|[-–—])\s*(?:Impact Factor|CiteScore)\b", value, re.I)
                ),
                -1,
            )
            title = next(
                (
                    value
                    for value in candidates
                    if not re.match(r"Guest editors?\s*[:：]", value, re.I)
                    and not re.search(r"(?:Impact Factor|CiteScore)\b", value, re.I)
                ),
                "",
            )
            journal = ""
            if metric_index >= 0:
                journal = re.split(
                    r"\s*(?:•|[-–—])\s*(?=(?:Impact Factor|CiteScore)\b)",
                    candidates[metric_index],
                    maxsplit=1,
                    flags=re.I,
                )[0].strip()
            if title and journal:
                scope_lines = [
                    value
                    for value in candidates
                    if value not in {title, candidates[metric_index]}
                    and not re.match(r"Guest editors?\s*[:：]", value, re.I)
                ]
                rows.append(
                    {
                        "title": title,
                        "deadline": deadline,
                        "official_url": official_url,
                        "discovery_url": ElsevierCallsSource.reader_url,
                        "scope_text": " ".join(scope_lines),
                        "journal": journal,
                        "publisher": "Elsevier",
                        "type": "special_issue",
                        "is_aggregator": True,
                    }
                )
            block = []
        return rows

    def fetch(
        self,
        *,
        since: datetime,
        progress: Callable[..., None] | None = None,
        keywords: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        del since
        official_url = self._urls[0]
        rows: list[dict[str, Any]] = []
        if progress:
            progress("正在读取 Elsevier 征稿目录…", 15)
        pending = [official_url]
        visited: set[str] = set()
        while pending and len(visited) < 6:
            current_url = pending.pop(0)
            if current_url in visited:
                continue
            visited.add(current_url)
            try:
                text = _fetch_resilient(self._fetcher, current_url, minimum_length=10000)
            except Exception:
                continue
            discovered = _structured_records(text) or self._initial_state_records(text, official_url)
            if not discovered:
                self._record_error(current_url, "parse", "ScienceDirect page did not contain recognizable call records")
            rows.extend(self._decorate(discovered, current_url, text))
            for anchor in re.findall(r'<a\b[^>]*>', text, re.I):
                if not re.search(r'rel=["\']next["\']', anchor, re.I):
                    continue
                link = re.search(r'href=["\']([^"\']+)', anchor, re.I)
                next_url = _safe_url(link.group(1), current_url) if link else ""
                if next_url and urlsplit(next_url).netloc == urlsplit(current_url).netloc and next_url not in visited:
                    pending.append(next_url)
                elif next_url:
                    self._record_error(next_url, "pagination", "Cross-origin pagination link was not followed")
        if rows:
            return self._dedupe(_rank_relevant(rows, keywords, limit=18))
        return []


class SpringerCollectionsSource(_BaseSource):
    source_id = "springer_nature"
    publisher = "Springer Nature"
    default_type = "topical_collection"
    urls = ("https://link.springer.com/collections",)

    openalex_api = "https://api.openalex.org"

    def _openalex_journals(self, keywords: list[str] | None) -> list[dict[str, Any]]:
        queries = _discovery_queries(keywords, limit=4)
        if not queries:
            return []
        source_counts: dict[str, int] = {}
        source_names: dict[str, str] = {}

        def fetch_groups(query: str) -> Any:
            url = f"{self.openalex_api}/works?{urlencode({'search': query, 'group_by': 'primary_location.source.id', 'per-page': 40})}"
            payload = _json_document(self._fetcher(url))
            if not isinstance(payload, dict) or not isinstance(payload.get("group_by"), list):
                self._record_error(url, "parse", "OpenAlex grouping response is invalid")
                return None
            return payload

        payloads: list[Any] = []
        with ThreadPoolExecutor(max_workers=min(4, len(queries))) as executor:
            jobs = [executor.submit(fetch_groups, query) for query in queries]
            for future in as_completed(jobs):
                try:
                    payloads.append(future.result())
                except Exception:
                    continue
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            for group in payload.get("group_by", []) if isinstance(payload.get("group_by"), list) else []:
                if not isinstance(group, dict):
                    continue
                source_id = str(group.get("key", "")).rsplit("/", 1)[-1]
                name = str(group.get("key_display_name", "")).strip()
                if not re.fullmatch(r"S\d+", source_id) or any(
                    token in name.casefold() for token in ("arxiv", "biorxiv", "preprint", "research square", "zenodo")
                ):
                    continue
                source_counts[source_id] = source_counts.get(source_id, 0) + int(group.get("count", 0) or 0)
                source_names[source_id] = name
        source_ids = sorted(source_counts, key=lambda value: (-source_counts[value], source_names.get(value, "").casefold()))[:50]
        if not source_ids:
            return []
        filter_value = "openalex_id:" + "|".join(source_ids)
        url = f"{self.openalex_api}/sources?{urlencode({'filter': filter_value, 'per-page': 50})}"
        payload = _json_document(self._fetcher(url))
        rows = payload.get("results", []) if isinstance(payload, dict) else []
        result: list[dict[str, Any]] = []
        for raw in rows if isinstance(rows, list) else []:
            if not isinstance(raw, dict) or str(raw.get("type", "")).casefold() != "journal":
                continue
            publisher = str(raw.get("host_organization_name", ""))
            homepage = str(raw.get("homepage_url", ""))
            combined = f"{publisher} {homepage}".casefold()
            if not any(token in combined for token in ("springer", "nature.com", "biomed", "bmc")):
                continue
            row = dict(raw)
            source_id = str(raw.get("id", "")).rsplit("/", 1)[-1]
            row["topic_count"] = source_counts.get(source_id, 0)
            result.append(row)
        result.sort(key=lambda row: (-int(row.get("topic_count", 0) or 0), str(row.get("display_name", "")).casefold()))
        return result[:10]

    @staticmethod
    def _route(journal: dict[str, Any]) -> tuple[str, str] | None:
        homepage = str(journal.get("homepage_url", "")).strip()
        if not _domain_is(homepage, 'springer.com', 'nature.com', 'biomedcentral.com', 'springernature.com'):
            return None
        folded = homepage.casefold()
        springer_id = re.search(r"/journal/(\d+)", folded)
        if springer_id:
            return f"https://link.springer.com/journal/{springer_id.group(1)}/collections?filter=Open", "springer"
        try:
            parts = [part for part in urlsplit(homepage).path.split("/") if part]
        except ValueError:
            parts = []
        if _domain_is(homepage, 'nature.com') and parts:
            short_name = parts[1] if parts[0].casefold() == "journals" and len(parts) > 1 else parts[0]
            if short_name.casefold() not in {"index.html", "articles", "search"}:
                return f"https://www.nature.com/{short_name}/collections", "nature"
        return None

    @staticmethod
    def _listing_records(
        text: str,
        listing_url: str,
        journal: dict[str, Any],
        route_kind: str,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        journal_name = _clean_html(journal.get("display_name", ""))
        issn = str(journal.get("issn_l", "")).strip()
        for payload in _json_payloads(text):
            for node in _walk(payload):
                if str(node.get("@type", "")).casefold() != "listitem":
                    continue
                official_url = str(node.get("url", "")).strip()
                if "/collections/" not in official_url:
                    continue
                title = _clean_html(_first(node, "name", "headline", "title"))
                if title:
                    rows.append(
                        {
                            "title": title,
                            "scope_text": _clean_html(node.get("description", "")),
                            "official_url": urljoin(listing_url, official_url),
                            "discovery_url": listing_url,
                            "journal": journal_name,
                            "issn": issn,
                            "publisher": "Springer Nature",
                            "type": "topical_collection",
                        }
                    )
        if rows or route_kind != "nature":
            return rows
        for match in re.finditer(
            r"<h3\b[^>]*>.*?<a\b[^>]*href=[\"']([^\"']*/collections/[^\"']+)[\"'][^>]*>(.*?)</a>.*?</h3>",
            text,
            re.I | re.S,
        ):
            title = _clean_html(match.group(2))
            if title:
                rows.append(
                    {
                        "title": title,
                        "scope_text": "",
                        "official_url": urljoin(listing_url, unescape(match.group(1))),
                        "discovery_url": listing_url,
                        "journal": journal_name,
                        "issn": issn,
                        "publisher": "Springer Nature",
                        "type": "article_collection",
                    }
                )
        return rows

    @staticmethod
    def _detail_record(text: str, seed: dict[str, Any]) -> dict[str, Any] | None:
        deadline_match = re.search(
            r"data-test=[\"']submission-deadline[\"'][^>]*>.*?<p\b[^>]*>(.*?)</p>",
            text,
            re.I | re.S,
        )
        if not deadline_match:
            deadline_match = re.search(
                r"(?:submission\s+deadline|submit\s+by|closing\s+date)\s*[:：]?\s*</?[^>]*>?\s*([0-3]?\d\s+[A-Za-z]+\s+20\d{2})",
                text,
                re.I | re.S,
            )
        deadline = _clean_html(deadline_match.group(1)) if deadline_match else ""
        if deadline.casefold() in {"ongoing", "open", "rolling"}:
            deadline = ""
        best: dict[str, Any] = {}
        for payload in _json_payloads(text):
            for node in _walk(payload):
                if str(node.get("@type", "")).casefold() == "collectionpage" and (
                    node.get("headline") or node.get("description")
                ):
                    best = node
                    break
            if best:
                break
        result = dict(seed)
        if best:
            result.update(_original_metadata(best))
            result["title"] = _clean_html(_first(best, "headline", "name", "title")) or result.get("title", "")
            result["scope_text"] = _clean_html(_first(best, "description", "abstract")) or result.get("scope_text", "")
            result["official_url"] = str(_first(best, "url", "sameAs")) or result.get("official_url", "")
            periodicals = best.get("isPartOf", [])
            periodicals = periodicals if isinstance(periodicals, list) else [periodicals]
            journal_name = next(
                (
                    _clean_html(value.get("name", ""))
                    for value in periodicals
                    if isinstance(value, dict) and _clean_html(value.get("name", ""))
                ),
                "",
            )
            if journal_name:
                result["journal"] = journal_name
        result["deadline"] = deadline
        scope = _element_inner_html(text, "collection-description|aims-and-scope|collection-scope")
        if scope:
            result.update(_scope_fields(scope, complete=True))
        else:
            result.update(_scope_fields(result.get('scope_text', '')))
        result["publisher"] = "Springer Nature"
        result["is_aggregator"] = False
        return result

    def fetch(
        self,
        *,
        since: datetime,
        progress: Callable[..., None] | None = None,
        keywords: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if not _discovery_queries(keywords, limit=1):
            return super().fetch(since=since, progress=progress, keywords=keywords)
        try:
            if progress:
                progress("正在用 OpenAlex 定位 Springer Nature 相关期刊…", 10)
            journals = self._openalex_journals(keywords)
        except Exception as error:
            if progress:
                progress(f"Springer Nature 期刊定位失败，已继续：{error}", 0)
            journals = []
        if not journals:
            return super().fetch(since=since, progress=progress, keywords=keywords)
        routes = [(journal, route) for journal in journals if (route := self._route(journal)) is not None]
        seeds: list[dict[str, Any]] = []

        def fetch_listing(journal: dict[str, Any], route: tuple[str, str]) -> tuple[dict[str, Any], str, str, str]:
            listing_url, route_kind = route
            listing = _fetch_resilient(self._fetcher, listing_url, minimum_length=8000)
            return journal, listing_url, route_kind, listing

        with ThreadPoolExecutor(max_workers=min(6, max(1, len(routes)))) as executor:
            jobs = [executor.submit(fetch_listing, journal, route) for journal, route in routes]
            for index, future in enumerate(as_completed(jobs), start=1):
                if progress:
                    progress(f"正在读取 Springer Nature 期刊专题页（{index}/{len(routes)}）…", 15 + int(index * 35 / max(1, len(routes))))
                try:
                    journal, listing_url, route_kind, listing = future.result()
                except Exception:
                    continue
                seeds.extend(self._listing_records(listing, listing_url, journal, route_kind))
        seeds = _rank_relevant(self._dedupe(seeds), keywords, limit=10)
        rows: list[dict[str, Any]] = []

        def fetch_detail(seed: dict[str, Any]) -> tuple[dict[str, Any], str]:
            detail = _fetch_resilient(
                self._fetcher,
                str(seed.get("official_url", "")),
                minimum_length=8000,
            )
            return seed, detail

        with ThreadPoolExecutor(max_workers=min(6, max(1, len(seeds)))) as executor:
            jobs = [executor.submit(fetch_detail, seed) for seed in seeds]
            for index, future in enumerate(as_completed(jobs), start=1):
                if progress:
                    progress(f"正在核对 Springer Nature 征稿截止日期（{index}/{len(seeds)}）…", 50 + int(index * 50 / max(1, len(seeds))))
                try:
                    seed, detail = future.result()
                except Exception:
                    continue
                row = self._detail_record(detail, seed)
                if row:
                    rows.append(row)
        return self._dedupe(self._decorate(rows, self.openalex_api))


class WileyCallsSource(_BaseSource):
    source_id = "wiley"
    publisher = "Wiley"
    urls = ("https://onlinelibrary.wiley.com/call-for-papers",)


class TaylorFrancisCallsSource(_BaseSource):
    source_id = "taylor_francis"
    publisher = "Taylor & Francis"
    urls: tuple[str, ...] = ()

    api_url = "https://think.taylorandfrancis.com/wp-json/wp/v2/special_issues"

    @staticmethod
    def _api_rows(payload: Any, discovery_url: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in payload if isinstance(payload, list) else []:
            if not isinstance(item, dict):
                continue
            fields = item.get("special_issues", {}) if isinstance(item.get("special_issues"), dict) else {}
            meta = item.get("meta", {}) if isinstance(item.get("meta"), dict) else {}
            title_value = item.get("title", {})
            title = _clean_html(title_value.get("rendered", "")) if isinstance(title_value, dict) else _clean_html(title_value)
            deadline = _clean_html(
                _first_list_value(fields, "_special_issues_deadline", "_special_issues_deadline2")
                or meta.get("meta-page-expiry-date", "")
            )
            journal = _clean_html(_first_list_value(fields, "_special_issues_journal_title"))
            scope = "\n".join(
                value
                for value in (
                    str(_first_list_value(fields, "_special_issues_copy") or ''),
                    str(_first_list_value(fields, "_special_issues_submissions_instructions") or ''),
                )
                if value
            )
            official_url = str(item.get("link", "")).strip()
            if not title or not deadline or not official_url:
                continue
            open_access = str(_first_list_value(fields, "_open_access")).strip().casefold()
            if open_access in {"1", "true", "yes"}:
                fee_mode = "apc"
            elif open_access in {"0", "false", "no"}:
                fee_mode = "hybrid"
            else:
                fee_mode = "unknown"
            rows.append(
                {
                    **_original_metadata(item),
                    "id": f"tandf-{item.get('id', '')}",
                    "title": title,
                    "deadline": deadline,
                    "official_url": official_url,
                    "discovery_url": discovery_url,
                    **_scope_fields(scope, complete=bool(_first_list_value(fields, '_special_issues_copy'))),
                    "journal": journal,
                    "publisher": "Taylor & Francis",
                    "fee_mode": fee_mode,
                    "submission_url": str(
                        _first_list_value(
                            fields,
                            "_special_issues_submissions_submit",
                            "_special_issues_submissions_link",
                        )
                    ).strip(),
                    "type": "special_issue",
                    "is_aggregator": False,
                }
            )
        return rows

    def fetch(
        self,
        *,
        since: datetime,
        progress: Callable[..., None] | None = None,
        keywords: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        del since
        available = _discovery_queries(keywords, limit=40) or [""]
        start = int(getattr(self, "_query_offset", 0)) % len(available)
        query_count = min(6, len(available))
        queries = [available[(start + index) % len(available)] for index in range(query_count)]
        self._query_offset = (start + query_count) % len(available)
        rows: list[dict[str, Any]] = []

        def request(query: str) -> tuple[list[dict[str, Any]], Exception | None]:
            found: list[dict[str, Any]] = []
            for page in range(1, 4):
                params = {
                    "search": query,
                    "page": page,
                    "per_page": 20,
                    "orderby": "modified",
                    "order": "desc",
                    "_fields": "id,date,modified,link,title,meta,special_issues",
                }
                request_url = f"{self.api_url}?{urlencode(params)}"
                try:
                    payload = _json_document(
                        _fetch_resilient(self._fetcher, request_url, minimum_length=2)
                    )
                    if not isinstance(payload, list):
                        self._record_error(request_url, "parse", "Expected a special-issue JSON list")
                        return found, ValueError("Invalid source JSON")
                except Exception as error:
                    return found, error
                found.extend(self._api_rows(payload, request_url))
                if len(payload) < 20:
                    break
            return found, None

        with ThreadPoolExecutor(max_workers=min(4, len(queries))) as executor:
            jobs = {executor.submit(request, query): query for query in queries}
            for index, future in enumerate(as_completed(jobs), start=1):
                batch_rows, error = future.result()
                rows.extend(batch_rows)
                if error is not None:
                    if progress:
                        progress(f"Taylor & Francis 来源暂不可用，已继续：{error}", 0)
                if progress:
                    progress(
                        f"正在读取 Taylor & Francis 官方征稿（{index}/{len(queries)}）…",
                        int(index * 100 / max(1, len(queries))),
                    )
        return self._dedupe(
            _rank_relevant(
                self._decorate(rows, self.api_url),
                _discovery_queries(keywords, limit=5),
                limit=24,
                minimum_score=4,
            )
        )


class MdpiSpecialIssuesSource(_BaseSource):
    source_id = "mdpi"
    publisher = "MDPI"
    urls = (
        "https://www.mdpi.com/journal/land/special_issues",
        "https://www.mdpi.com/journal/soilsystems/special_issues",
        "https://www.mdpi.com/journal/remotesensing/special_issues",
        "https://www.mdpi.com/journal/agronomy/special_issues",
        "https://www.mdpi.com/journal/agriculture/special_issues",
    )

    @staticmethod
    def _mdpi_rows(text: str, base_url: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        journal_slug = urlsplit(base_url).path.split("/")[2] if len(urlsplit(base_url).path.split("/")) > 2 else ""
        journal_names = {
            "land": "Land",
            "soilsystems": "Soil Systems",
            "remotesensing": "Remote Sensing",
            "agronomy": "Agronomy",
            "agriculture": "Agriculture",
        }
        blocks = re.split(r'(?=<div class="generic-item article-item">)', text, flags=re.I)
        for block in blocks:
            title = re.search(r'<a[^>]*class="title-link"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.I | re.S)
            deadline = re.search(r"submission\s+deadline\s*<strong>(.*?)</strong>", block, re.I | re.S)
            if not title or not deadline or "Submission Open" not in block:
                continue
            keyword_match = re.search(r"<em>Keywords:</em>(.*?)(?:</div>|<div)", block, re.I | re.S)
            section_match = re.search(r"This special issue belongs to the Section\s*<a[^>]*>(.*?)</a>", block, re.I | re.S)
            scope = "; ".join(
                value
                for value in (
                    _clean_html(keyword_match.group(1)) if keyword_match else "",
                    _clean_html(section_match.group(1)) if section_match else "",
                )
                if value
            )
            rows.append(
                {
                    "title": _clean_html(title.group(2)),
                    "deadline": _clean_html(deadline.group(1)),
                    "official_url": urljoin(base_url, unescape(title.group(1))),
                    "scope_text": scope,
                    "journal": journal_names.get(journal_slug.casefold(), journal_slug.replace("-", " ").title()),
                    "publisher": "MDPI",
                    "fee_mode": "apc",
                    "type": "special_issue",
                }
            )
        return rows

    def fetch(self, *, since: datetime, progress: Callable[..., None] | None = None, keywords: list[str] | None = None) -> list[dict[str, Any]]:
        del since, keywords
        rows: list[dict[str, Any]] = []
        for index, url in enumerate(self._urls, start=1):
            if progress:
                progress(f"正在读取 MDPI 开放特刊（{index}/{len(self._urls)}）…", int(index * 100 / max(1, len(self._urls))))
            try:
                text = self._fetcher(url)
            except Exception as error:
                if progress:
                    progress(f"MDPI 来源暂不可用，已继续：{error}", 0)
                continue
            discovered = _structured_records(text) or self._mdpi_rows(text, url)
            rows.extend(self._decorate(discovered, url))
        return self._dedupe(rows)


class AggregatorDiscoverySource(_BaseSource):
    source_id = "aggregator"
    is_aggregator = True
    urls = ("https://call-for-papers.sas.upenn.edu/category/journals-and-collections-of-essays",)

    @staticmethod
    def _aggregator_rows(text: str, base_url: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for block in re.findall(r"<article[^>]*class=\"[^\"]*node-teaser[^\"]*\"[^>]*>(.*?)</article>", text, re.I | re.S):
            combined = _clean_html(block)
            if not re.search(r"\b(special (?:journal )?issue|topical collection|article collection|research topic|thematic issue)\b", combined, re.I):
                continue
            if re.search(r"\b(call for chapters|edited volume|book chapter|conference|workshop)\b", combined, re.I):
                continue
            title = re.search(r'<h2[^>]*class="node-title"[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.I | re.S)
            due = re.search(r'field-name-field-cfp-due-date.*?date-display-single[^>]*>(.*?)</span>', block, re.I | re.S)
            if not title or not due:
                continue
            source_url = urljoin(base_url, unescape(title.group(1)))
            published = re.search(
                r'<time\b[^>]*(?:property=["\'][^"\']*(?:dc:date|dc:created)[^"\']*["\'])?[^>]*datetime=["\']([^"\']+)',
                block,
                re.I | re.S,
            )
            source_id = re.search(r"/node/(\d+)(?:/|$|[?#])", source_url)
            rows.append(
                {
                    "title": _clean_html(title.group(2)),
                    "deadline": re.sub(r"^[A-Za-z]+,\s*", "", _clean_html(due.group(1))),
                    "official_url": source_url,
                    "source_record_id": source_id.group(1) if source_id else "",
                    "published_at": published.group(1) if published else "",
                    "scope_text": combined,
                    "type": "special_issue",
                }
            )
        return rows

    def fetch(self, *, since: datetime, progress: Callable[..., None] | None = None, keywords: list[str] | None = None) -> list[dict[str, Any]]:
        del since, keywords
        rows: list[dict[str, Any]] = []
        for index, url in enumerate(self._urls, start=1):
            try:
                text = self._fetcher(url)
            except Exception as error:
                if progress:
                    progress(f"聚合来源失败，已继续：{error}", 0)
                continue
            structured = _structured_records(text)
            discovered = structured or self._aggregator_rows(text, url)
            rows.extend(self._decorate(discovered, url))
            if progress:
                progress(f"正在读取聚合征稿（{index}/{len(self._urls)}）…", int(index * 100 / max(1, len(self._urls))))
        return self._dedupe(rows)


def default_special_issue_sources() -> list[_BaseSource]:
    return [
        FrontiersResearchTopicsSource(),
        ElsevierCallsSource(),
        SpringerCollectionsSource(),
        WileyCallsSource(),
        TaylorFrancisCallsSource(),
        MdpiSpecialIssuesSource(),
        AggregatorDiscoverySource(),
    ]
