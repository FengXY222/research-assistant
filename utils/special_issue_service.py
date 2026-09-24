"""Normalization, admission and deduplication for journal collection calls."""

from __future__ import annotations

import hashlib
import ipaddress
import inspect
import json
import re
import unicodedata
from copy import deepcopy
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from html import unescape
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request

from utils.api_rate_limit import rate_limited_urlopen as urlopen

from utils.evidence_cache import EvidenceCache
from utils.journal_quality import journal_quality_snapshot
from utils.publisher_utils import canonical_publisher
from utils.special_issue_policy import aggregator_is_fresh, local_datetime, open_eligibility


_TYPE_ALIASES = {
    "special issue": "special_issue",
    "special_issue": "special_issue",
    "topical collection": "topical_collection",
    "topical_collection": "topical_collection",
    "topical issue": "topical_collection",
    "research topic": "research_topic",
    "research_topic": "research_topic",
    "article collection": "article_collection",
    "article_collection": "article_collection",
}
_REJECT_PATTERNS = (
    r"\bconference\b",
    r"\bworkshop\b",
    r"\bbook\s+chapter",
    r"\bchapter\s+proposal",
    r"\bspecial\s+session\b",
    r"\bproceedings\b",
    r"仅限邀请",
    r"by\s+invitation\s+only",
    r"invitation[- ]only",
)

_PAGE_CODE_MARKERS = (
    "window.nreum",
    "licensekey",
    ".batch_articles",
    "#main-content",
    ":hover",
    "@media",
    "function(",
    "webpack",
)


def special_issue_scope_is_corrupted(value: Any) -> bool:
    text = str(value or "").casefold()
    marker_hits = sum(marker in text for marker in _PAGE_CODE_MARKERS)
    css_density = text.count("{") + text.count("}")
    return marker_hits >= 1 or css_density >= 8


def clean_special_issue_scope(value: Any) -> str:
    """Return visible call text while dropping executable and styling payloads."""
    raw = unescape(str(value or ""))
    raw = re.sub(r"<!--.*?-->", " ", raw, flags=re.S)
    raw = re.sub(
        r"<(script|style|noscript|template|svg)\b[^>]*>.*?</\1\s*>",
        " ",
        raw,
        flags=re.I | re.S,
    )
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = re.sub(r"\b(?:window\.)?NREUM\b.*", " ", raw, flags=re.I | re.S)
    return " ".join(unicodedata.normalize("NFKC", raw).split())


def _official_scope_sections(page: str) -> str:
    """Extract semantic content blocks instead of treating the whole publisher page as scope."""
    cleaned_page = re.sub(
        r"<(script|style|noscript|template|svg)\b[^>]*>.*?</\1\s*>",
        " ",
        str(page or ""),
        flags=re.I | re.S,
    )
    candidates: list[str] = []
    explicit = re.search(r'<(?:section|div)\b[^>]*(?:id|class|data-test)=[\"\'][^\"\']*(?:collection-description|aims-and-scope|scope)[^\"\']*[\"\'][^>]*>(.*?)</(?:section|div)>', cleaned_page, re.I | re.S)
    if explicit:
        from utils.special_issue_sources import _paragraphs
        text = '\n\n'.join(_paragraphs(explicit.group(1)))
        if len(text) >= 80:
            return text
    mdpi = re.search(
        r"<h2[^>]*>\s*(?:<a[^>]*></a>)?\s*Special Issue Information\s*</h2>(.*?)(?=<h2[^>]*>\s*(?:<a[^>]*(?:name|id)=[\"']keywords|Keywords\b)|\Z)",
        cleaned_page,
        flags=re.I | re.S,
    )
    if mdpi:
        mdpi_text = clean_special_issue_scope(mdpi.group(1))
        if len(mdpi_text) >= 80:
            candidates.append(mdpi_text)
    for tag in ("article", "section", "main"):
        for match in re.finditer(rf"<{tag}\b[^>]*>(.*?)</{tag}\s*>", cleaned_page, flags=re.I | re.S):
            text = clean_special_issue_scope(match.group(1))
            if len(text) >= 45 and not re.fullmatch(r"(?:submission )?deadline\s*:?.*", text, flags=re.I):
                candidates.append(text)
    def score(value: str) -> tuple[int, int]:
        folded = value.casefold()
        quality = min(len(value), 6000)
        if any(marker in folded for marker in ("dear colleagues", "background", "about this research topic", "we welcome", "this special issue")):
            quality += 8000
        if "journals active journals find a journal" in folded or folded.count(" journal ") > 35:
            quality -= 12000
        return quality, len(value)

    candidates.sort(key=score, reverse=True)
    return candidates[0] if candidates and score(candidates[0])[0] > 0 else ""


def _text(value: Any) -> str:
    raw = clean_special_issue_scope(value)
    return " ".join(unicodedata.normalize("NFKC", raw).split())


_PUBLISHER_DOMAINS = {
    "Elsevier": ("elsevier.com", "sciencedirect.com", "cell.com"),
    "Springer Nature": ("springer.com", "springernature.com", "nature.com", "biomedcentral.com"),
    "Taylor & Francis": ("tandfonline.com", "taylorandfrancis.com", "routledge.com"),
    "Wiley": ("wiley.com", "onlinelibrary.wiley.com", "wiley-vch.de"),
}
_OTHER_OFFICIAL_DOMAINS = ('frontiersin.org', 'mdpi.com')


def _trusted_official_url(url: str, item: dict[str, Any]) -> bool:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or '').casefold().rstrip('.')
    except ValueError:
        return False
    if parts.scheme not in {'http', 'https'} or parts.username or parts.password:
        return False
    domains = [domain for group in _PUBLISHER_DOMAINS.values() for domain in group] + list(_OTHER_OFFICIAL_DOMAINS)
    if item.get('journal_library_id') and item.get('journal_official_url'):
        library_host = urlsplit(str(item['journal_official_url'])).hostname
        if library_host:
            domains.append(library_host.casefold())
    if any(host == domain or host.endswith('.' + domain) for domain in domains):
        return True
    canonical_url = _canonical_url(url)
    evidence = item.get("source_evidence", [])
    return any(
        isinstance(row, dict)
        and not bool(row.get("is_aggregator"))
        and _canonical_url(row.get("url", "")) == canonical_url
        for row in (evidence if isinstance(evidence, list) else [])
    )


def infer_special_issue_publisher(value: Any, *urls: Any, source: str = "") -> str:
    """Infer the four priority publishers from parent brands, imprints and official domains."""
    raw = _text(value)
    canonical = canonical_publisher(raw)
    if canonical in _PUBLISHER_DOMAINS:
        return canonical
    key = raw.casefold()
    imprint_aliases = {
        "Elsevier": ("academic press", "pergamon", "cell press", "woodhead", "churchill livingstone"),
        "Springer Nature": ("springer-verlag", "nature portfolio", "biomed central", "bmc", "palgrave", "springeropen"),
        "Taylor & Francis": ("taylor & francis", "taylor and francis", "informa", "routledge", "crc press"),
        "Wiley": ("john wiley", "wiley-vch", "blackwell", "hindawi"),
    }
    for publisher, aliases in imprint_aliases.items():
        if any(alias in key for alias in aliases):
            return publisher
    combined_urls = [str(url or "").strip() for url in urls]
    combined_urls.append(str(source or "").strip())
    for raw_url in combined_urls:
        folded = raw_url.casefold()
        try:
            host = (urlsplit(raw_url).hostname or "").casefold()
        except ValueError:
            host = ""
        for publisher, domains in _PUBLISHER_DOMAINS.items():
            if any(host == domain or host.endswith("." + domain) for domain in domains):
                return publisher
    return canonical or "unknown"


def _canonical(value: Any) -> str:
    text = _text(value).casefold().replace("&", " and ")
    return " ".join(re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).split())


def _list_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,;|]", value)
    values = value if isinstance(value, list) else []
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _text(item).upper()
        match = re.search(r"\b\d{4}-[\dX]{4}\b", text)
        text = match.group(0) if match else text
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _parse_deadline(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", text, flags=re.I)
    candidates = [text]
    patterns = (
        r"\b(20\d{2}-\d{1,2}-\d{1,2})\b",
        r"\b([0-3]?\d\s+[A-Za-z]+\s+20\d{2})\b",
        r"\b([A-Za-z]+\s+[0-3]?\d,?\s+20\d{2})\b",
        r"\b(20\d{2}年\d{1,2}月\d{1,2}日)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            candidates.insert(0, match.group(1))
    for candidate in candidates:
        for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y", "%Y年%m月%d日"):
            try:
                return datetime.strptime(candidate.replace(",", ""), fmt).date().isoformat()
            except ValueError:
                continue
    return ""


def _collection_type(value: Any, title: str) -> str:
    canonical = _canonical(value)
    for alias, normalized in _TYPE_ALIASES.items():
        if alias in canonical:
            return normalized
    title_key = _canonical(title)
    for alias, normalized in _TYPE_ALIASES.items():
        if alias in title_key:
            return normalized
    return "special_issue"


def _canonical_url(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return text.rstrip("/")
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return text.rstrip("/")
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
             if not key.casefold().startswith('utm_') and key.casefold() not in {'fbclid', 'gclid', 'mc_cid', 'mc_eid'}]
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), parts.path.rstrip("/"), urlencode(query), ""))


def normalize_special_issue(
    raw: Any,
    *,
    source: str,
    fetched_at: str,
    today: date | None = None,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    title = _text(raw.get("title", raw.get("name", raw.get("headline", ""))))
    scope = _text(raw.get("scope_text", raw.get("scope", raw.get("description", raw.get("abstract", "")))))
    journal = _text(raw.get("journal", raw.get("container", raw.get("periodical", ""))))
    combined = " ".join((title, scope, _text(raw.get("type", "")))).casefold()
    if not title or any(re.search(pattern, combined, flags=re.I) for pattern in _REJECT_PATTERNS):
        return None
    if "rolling call" in combined or "open year-round" in combined or "常年征稿" in combined:
        return None
    deadline = _parse_deadline(raw.get("deadline", raw.get("date_expires", raw.get("end_date", ""))))
    reference_day = today or date.today()
    official_url = _canonical_url(raw.get("official_url", raw.get("url", "")))
    discovery_url = _canonical_url(raw.get("discovery_url", raw.get("source_url", official_url)))
    is_aggregator = bool(raw.get("is_aggregator")) or str(source).casefold() == "aggregator"
    if is_aggregator and official_url and urlsplit(official_url).hostname == urlsplit(discovery_url).hostname:
        official_url = ''
    publisher = infer_special_issue_publisher(
        raw.get("publisher", ""), official_url, discovery_url, source=source
    )
    item = {
        "title": title,
        "type": _collection_type(raw.get("type", ""), title),
        "journal": journal or "unknown",
        "issns": _list_strings(raw.get("issns", raw.get("issn", []))),
        "publisher": publisher,
        "scope_text": scope,
        "scope_is_complete": bool(raw.get('scope_is_complete', False)),
        "scope_status": raw.get('scope_status', 'snippet' if scope else 'missing'),
        "scope_paragraphs": deepcopy(raw.get('scope_paragraphs', [])),
        "source_record_id": str(raw.get('source_record_id', '')),
        "source_id": str(raw.get('source_id', source)),
        "deadline": deadline,
        "official_url": official_url,
        "discovery_urls": [value for value in (discovery_url,) if value],
        "source_evidence": [
            {
                "source": str(source).strip(),
                "url": discovery_url,
                "fetched_at": str(fetched_at).strip(),
                "is_aggregator": is_aggregator,
                "published_at": str(raw.get('published_at', '')),
                "updated_at": str(raw.get('updated_at', '')),
                "source_record_id": str(raw.get('source_record_id', '')),
            }
        ],
        "fee_mode": _text(raw.get("fee_mode", "")).casefold() or "unknown",
        "jcr": deepcopy(raw.get("jcr")) if isinstance(raw.get("jcr"), dict) else {},
        "cas": deepcopy(raw.get("cas")) if isinstance(raw.get("cas"), dict) else {},
        "verification_status": "aggregator_unverified" if is_aggregator else "pending_official",
        "fetched_at": str(fetched_at).strip(),
        "official_checked_at": _text(raw.get("official_checked_at", "")),
        "status": _text(raw.get("status", "unread")).casefold() or "unread",
    }
    item["dedupe_key"] = special_issue_dedupe_key(item)
    item["id"] = str(raw.get("id", "")).strip() or "si-" + hashlib.sha1(item["dedupe_key"].encode("utf-8")).hexdigest()[:20]
    return item


def special_issue_dedupe_key(item: dict[str, Any]) -> str:
    issns = sorted(_list_strings(item.get("issns", item.get("issn", []))))
    title = _canonical(item.get("title", ""))
    url = _canonical_url(item.get("official_url", item.get("url", "")))
    if url:
        return "url:" + url
    if item.get('source_record_id'):
        return 'source:' + str(item.get('source_id', '')) + ':' + str(item['source_record_id'])
    discovery = item.get('discovery_urls', [])
    return "record:" + hashlib.sha1("|".join((title, _canonical(item.get("journal", "")), str(discovery), str(item.get('edition', '')))).encode("utf-8")).hexdigest()


def _unique_dicts(values: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        key = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            result.append(deepcopy(value))
    return result


def _merge_record(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(left)
    if right.get('id') and right.get('id') != left.get('id'):
        result['id_aliases'] = list(dict.fromkeys([*left.get('id_aliases', []), *right.get('id_aliases', []), right['id']]))
    incoming_deadline = _parse_deadline(right.get('deadline'))
    if incoming_deadline and incoming_deadline != result.get('deadline'):
        result['deadline_candidates'] = _unique_dicts([*result.get('deadline_candidates', []),
            {'deadline': incoming_deadline, 'source_evidence': deepcopy(right.get('source_evidence', [])), 'fetched_at': right.get('fetched_at', '')}])
    for field in ("title", "journal", "publisher", "fee_mode", "official_url", "official_checked_at"):
        current = str(result.get(field, "")).strip()
        incoming = str(right.get(field, "")).strip()
        if incoming and (not current or current == "unknown"):
            result[field] = incoming
    if len(str(right.get("scope_text", ""))) > len(str(result.get("scope_text", ""))):
        result["scope_text"] = str(right.get("scope_text", ""))
        for field in ('scope_is_complete', 'scope_status', 'scope_paragraphs'):
            if field in right:
                result[field] = deepcopy(right[field])
    for field in ("jcr", "cas"):
        if isinstance(right.get(field), dict) and right[field] and not result.get(field):
            result[field] = deepcopy(right[field])
    left_issns, right_issns = set(result.get('issns', [])), set(right.get('issns', []))
    if left_issns and right_issns and not left_issns.intersection(right_issns):
        result['identity_status'] = 'conflict'
        result['identity_conflict_evidence'] = {'left': sorted(left_issns), 'right': sorted(right_issns)}
    else:
        result["issns"] = list(dict.fromkeys([*result.get("issns", []), *right.get("issns", [])]))
    result["discovery_urls"] = list(dict.fromkeys([*result.get("discovery_urls", []), *right.get("discovery_urls", [])]))
    result["source_evidence"] = _unique_dicts([*result.get("source_evidence", []), *right.get("source_evidence", [])])
    priority = {"pending_official": 0, "aggregator_unverified": 1, "temporarily_unavailable": 2, "official_verified": 3, "conflict": 4, "closed": 5, "expired": 5}
    statuses = (str(result.get("verification_status", "")), str(right.get("verification_status", "")))
    result["verification_status"] = max(statuses, key=lambda value: priority.get(value, 0))
    result["fetched_at"] = max(str(result.get("fetched_at", "")), str(right.get("fetched_at", "")))
    result["dedupe_key"] = special_issue_dedupe_key(result)
    result["id"] = str(result.get("id", "")) or str(right.get("id", ""))
    return result


def merge_special_issue_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    by_key: dict[str, int] = {}
    by_url: dict[str, int] = {}
    by_identity: dict[str, int] = {}

    def identity_key(value: dict[str, Any]) -> str:
        issns = sorted(_list_strings(value.get("issns", value.get("issn", []))))
        title = _canonical(value.get("title", ""))
        journal = _canonical(value.get("journal", ""))
        return "|".join((",".join(issns), title, journal)) if issns and title and journal else ""

    for raw in records:
        if not isinstance(raw, dict) or not str(raw.get("title", "")).strip():
            continue
        row = deepcopy(raw)
        key = special_issue_dedupe_key(row)
        url = _canonical_url(row.get("official_url", ""))
        position = by_key.get(key)
        if position is None and url:
            position = by_url.get(url)
        discovery_urls = [
            _canonical_url(value)
            for value in row.get("discovery_urls", [])
            if _canonical_url(value)
        ]
        if position is None:
            position = next((by_url[value] for value in discovery_urls if value in by_url), None)
        identity = identity_key(row)
        if position is None and identity:
            candidate = by_identity.get(identity)
            if candidate is not None:
                left_source_id = str(result[candidate].get("source_record_id", "")).strip()
                right_source_id = str(row.get("source_record_id", "")).strip()
                left_url = _canonical_url(result[candidate].get("official_url", ""))
                left_parts = urlsplit(left_url)
                right_parts = urlsplit(url)
                distinct_same_listing = bool(
                    left_url
                    and url
                    and left_url != url
                    and left_parts.netloc == right_parts.netloc
                    and left_parts.path == right_parts.path
                )
                if not distinct_same_listing and (not left_source_id or not right_source_id or left_source_id == right_source_id):
                    position = candidate
        if position is None:
            position = len(result)
            result.append(row)
        else:
            result[position] = _merge_record(result[position], row)
        by_key[key] = position
        merged_url = _canonical_url(result[position].get("official_url", ""))
        if url:
            by_url[url] = position
        for discovery_url in discovery_urls:
            by_url[discovery_url] = position
        if merged_url:
            by_url[merged_url] = position
        merged_identity = identity_key(result[position])
        if merged_identity:
            by_identity[merged_identity] = position
    return result


_READ_ONLY_OFFICIAL_HOSTS = (
    "elsevier.com",
    "sciencedirect.com",
    "wiley.com",
    "onlinelibrary.wiley.com",
    "wiley-vch.de",
)


def _request_public_page(url: str) -> str:
    request = Request(
        url,
        headers={
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8",
            "User-Agent": "ResearchAssistant/13.0 (personal academic verification)",
        },
    )
    with urlopen(request, timeout=20) as response:  # noqa: S310 - URL is validated by the caller
        return response.read().decode("utf-8", errors="replace")


def _official_reader_url(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or "").casefold().rstrip(".")
    if not any(host == domain or host.endswith("." + domain) for domain in _READ_ONLY_OFFICIAL_HOSTS):
        return ""
    target = urlunsplit(("http", parts.netloc, parts.path, parts.query, ""))
    return "https://r.jina.ai/" + target


def _fetch_official_page(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("无效的特刊官网地址")
    host = parts.hostname.casefold()
    if host in {"localhost", "localhost.localdomain"}:
        raise ValueError("不允许访问本机地址")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (address.is_private or address.is_loopback or address.is_link_local):
        raise ValueError("不允许访问私有网络地址")
    try:
        return _request_public_page(url)
    except Exception as direct_error:  # noqa: BLE001 - selected publishers expose a read-only mirror path
        reader_url = _official_reader_url(url)
        if not reader_url:
            raise
        try:
            page = _request_public_page(reader_url)
        except Exception as reader_error:  # noqa: BLE001 - preserve both transport failures for diagnostics
            raise OSError(f"官网直连失败：{direct_error}；只读备用入口失败：{reader_error}") from reader_error
        if not str(page).strip():
            raise OSError(f"官网直连失败：{direct_error}；只读备用入口返回空内容") from direct_error
        return page


def _page_deadline(page_text: str) -> str:
    match = re.search(
        r"(?:submission|manuscript|full\s+paper)?\s*deadline\s*[:：]?\s*([^|;\n]{6,45})",
        page_text,
        flags=re.I,
    )
    return _parse_deadline(match.group(1) if match else page_text)


def _content_matches(needle: str, haystack: str, *, minimum: float = 0.6) -> bool:
    wanted = _canonical(needle)
    content = _canonical(haystack)
    if not wanted:
        return False
    if wanted in content:
        return True
    tokens = {token for token in wanted.split() if len(token) >= 3}
    if not tokens:
        return wanted in content
    return len(tokens.intersection(content.split())) / len(tokens) >= minimum


def _aggregator_is_fresh(item: dict[str, Any], now: datetime) -> bool:
    return aggregator_is_fresh(item, now)


def _append_status_history(item: dict[str, Any], previous: str, current: str, checked_at: str) -> None:
    if previous == current:
        return
    history = [dict(row) for row in item.get("verification_history", []) if isinstance(row, dict)]
    history.append({"previous": previous, "current": current, "checked_at": checked_at})
    item["verification_history"] = history[-120:]


def verify_special_issue(
    item: dict[str, Any],
    *,
    now: datetime,
    cache: EvidenceCache,
) -> dict[str, Any]:
    """Conservatively verify one call against its official page."""
    result = deepcopy(item)
    checked_at = now.isoformat(timespec="seconds")
    previous_status = str(result.get("verification_status", "pending_official"))
    deadline = _parse_deadline(result.get("deadline", ""))
    official_url = _canonical_url(result.get("official_url", ""))
    try:
        if not official_url or not _trusted_official_url(official_url, result):
            raise OSError("尚未找到可信的期刊或出版社官网链接")
        page = _fetch_official_page(official_url)
    except Exception as error:  # noqa: BLE001 - daily retry retains fresh aggregator discoveries
        status = "aggregator_unverified" if _aggregator_is_fresh(result, now) and deadline else "temporarily_unavailable"
        if previous_status in {'closed', 'expired', 'conflict'}:
            status = previous_status
        elif deadline and deadline < now.date().isoformat():
            status = 'expired'
        elif previous_status == 'official_verified' and open_eligibility(result, now)[0]:
            result['official_verified_at'] = result.get('official_verified_at') or result.get('official_checked_at', '')
            status = 'official_verified'
        result["verification_status"] = status
        result["official_checked_at"] = checked_at
        result["official_retry_after"] = (now + timedelta(days=1)).isoformat(timespec="seconds")
        result["verification_error"] = str(error)[:240]
        _append_status_history(result, previous_status, status, checked_at)
        cache.put_special_issue_verification(
            str(result.get("id", result.get("dedupe_key", ""))),
            {"status": status, "checked_at": checked_at, "retry_after": result["official_retry_after"]},
        )
        return result

    visible = clean_special_issue_scope(page)
    page_hash = hashlib.sha256(page.encode("utf-8", errors="replace")).hexdigest()
    official_deadline = _page_deadline(visible)
    title_matches = _content_matches(str(result.get("title", "")), visible)
    journal = str(result.get("journal", ""))
    journal_matches = journal not in {"", "unknown"} and _content_matches(journal, visible, minimum=0.75)
    issn_matches = any(issn in visible.upper() for issn in _list_strings(result.get("issns", [])))
    closed = bool(re.search(r"submission(?:s)?\s+(?:is\s+)?closed|closed\s+for\s+submissions|no\s+longer\s+accepting", visible, re.I))
    identity_matches = title_matches and (journal_matches or issn_matches)
    if identity_matches and official_deadline and official_deadline != deadline:
        history = [dict(row) for row in result.get("deadline_history", []) if isinstance(row, dict)]
        history.append({"previous": deadline, "current": official_deadline, "checked_at": checked_at, 'source_url': official_url})
        result["deadline_history"] = history[-120:]
        result["deadline"] = official_deadline
        deadline = official_deadline
    if not identity_matches:
        status = 'conflict'
    elif closed:
        status = "closed"
        result["call_status"] = "closed"
    elif deadline and deadline < now.date().isoformat():
        status = "expired"
        result["call_status"] = "closed"
    elif identity_matches and deadline and official_deadline:
        status = "official_verified"
        result['call_status'] = 'open'
        result['official_verified_at'] = checked_at
    else:
        status = "conflict"
    result["verification_status"] = status
    result["official_checked_at"] = checked_at
    result["official_page_hash"] = page_hash
    raw_current_scope = str(result.get("scope_text", ""))
    current_scope = clean_special_issue_scope(raw_current_scope)
    if special_issue_scope_is_corrupted(raw_current_scope):
        current_scope = ""
    official_scope = _official_scope_sections(page)
    if official_scope and identity_matches:
        from utils.special_issue_sources import _scope_fields
        result.update(_scope_fields(official_scope, complete=True))
    elif current_scope:
        result["scope_text"] = current_scope
    result.pop('verification_error', None)
    _append_status_history(result, previous_status, status, checked_at)
    cache.put_special_issue_verification(
        str(result.get("id", result.get("dedupe_key", ""))),
        {
            "status": status,
            "checked_at": checked_at,
            "page_hash": page_hash,
            "deadline": deadline,
            "title_matches": title_matches,
            "journal_matches": journal_matches,
            "issn_matches": issn_matches,
        },
    )
    return result


def _local_journal(item: dict[str, Any], library: list[dict[str, Any]]) -> dict[str, Any]:
    wanted_issns = set(_list_strings(item.get("issns", [])))
    wanted_name = _canonical(item.get("journal", ""))
    best: tuple[float, dict[str, Any]] | None = None
    for journal in library:
        if not isinstance(journal, dict):
            continue
        local_issns = set(_list_strings(journal.get("issn", journal.get("issns", []))))
        if wanted_issns and local_issns and wanted_issns.intersection(local_issns):
            return journal
        if wanted_issns and local_issns:
            continue
        local_name = _canonical(journal.get("name", ""))
        if wanted_name and wanted_name == local_name:
            best = (1.0, journal)
    return best[1] if best else {}


def enrich_special_issue_journal(
    item: dict[str, Any],
    journal_library: list[dict[str, Any]],
    *,
    easyscholar_ready: bool,
) -> dict[str, Any]:
    result = deepcopy(item)
    local = _local_journal(result, journal_library)
    if local:
        if str(result.get("journal", "")) in {"", "unknown"}:
            result["journal"] = str(local.get("name", "unknown"))
        if str(result.get("publisher", "")) in {"", "unknown"}:
            result["publisher"] = str(local.get("publisher", "unknown"))
        if str(result.get("fee_mode", "")) in {"", "unknown"}:
            result["fee_mode"] = str(local.get("fee_mode", "unknown"))
        if not result.get("jcr") and isinstance(local.get("jcr"), dict):
            result["jcr"] = deepcopy(local["jcr"])
        if not result.get("cas") and isinstance(local.get("easyscholar"), dict):
            result["cas"] = deepcopy(local["easyscholar"])
        result["issns"] = list(dict.fromkeys([*result.get("issns", []), *_list_strings(local.get("issn", local.get("issns", [])))]))
        result["journal_library_id"] = str(local.get("id", ""))
        result['journal_official_url'] = str(local.get('homepage', local.get('url', local.get('homepage_url', ''))))
        result['identity_status'] = 'library_matched'
        result["journal_evidence"] = {
            "source": "journal_library",
            "journal_id": str(local.get("id", "")),
            "checked_at": str(local.get("metadata_updated_at", local.get("easyscholar", {}).get("checked_at", ""))),
        }
    current_snapshot = journal_quality_snapshot(
        {
            "jcr": result.get("jcr", {}),
            "easyscholar": result.get("cas", {}),
        }
    )
    needs_jcr = not bool(current_snapshot.get("jcr_quartile"))
    needs_cas = not bool(current_snapshot.get("cas_upgrade") or current_snapshot.get("cas_basic"))
    journal_name = str(result.get("journal", "")).strip()
    if easyscholar_ready and journal_name.casefold() not in {"", "unknown", "未知期刊"} and (needs_jcr or needs_cas):
        try:
            from utils.easyscholar_service import fetch_easyscholar_metrics, merge_easyscholar_patch

            journal = {
                "id": str(result.get("journal_library_id", result.get("id", ""))),
                "name": str(result.get("journal", "")),
                "issn": result.get("issns", []),
                "publisher": str(result.get("publisher", "")),
                "jcr": deepcopy(result.get("jcr", {})),
                "easyscholar": deepcopy(result.get("cas", {})),
            }
            enriched = merge_easyscholar_patch(journal, fetch_easyscholar_metrics(journal), query_signature="special_issue")
            result["jcr"] = deepcopy(enriched.get("jcr", result.get("jcr", {})))
            result["cas"] = deepcopy(enriched.get("easyscholar", result.get("cas", {})))
            result["journal_evidence"] = {"source": "EasyScholar", "checked_at": str(result.get("cas", {}).get("checked_at", ""))}
        except Exception as error:  # noqa: BLE001 - unknown metadata remains visible
            evidence = result.get("journal_evidence", {}) if isinstance(result.get("journal_evidence"), dict) else {}
            result["journal_evidence"] = {**evidence, "easyscholar_error": str(error)[:200]}
    snapshot = journal_quality_snapshot(
        {
            "jcr": result.get("jcr", {}),
            "easyscholar": result.get("cas", {}),
        }
    )
    result.update(
        {
            "jcr_status": snapshot.get("jcr_status", "pending"),
            "jcr_quartile": snapshot.get("jcr_quartile", ""),
            "cas_upgrade": snapshot.get("cas_upgrade", ""),
            "cas_basic": snapshot.get("cas_basic", ""),
            "journal_metric_line": snapshot.get("metric_line", ""),
            "quality_checked_at": snapshot.get("checked_at", ""),
            "quality_source": snapshot.get("source", ""),
        }
    )
    return result


def special_issue_refresh_due(last_checked_at: str | dict[str, Any], *, now: datetime) -> bool:
    """Return whether the normal 24-hour check or a failed-source retry is due."""
    payload = last_checked_at if isinstance(last_checked_at, dict) else {}
    if payload:
        retry_times: list[datetime] = []
        checkpoints = payload.get("source_checkpoints", {})
        if isinstance(checkpoints, dict):
            for checkpoint in checkpoints.values():
                if not isinstance(checkpoint, dict) or str(checkpoint.get("status", "")) not in {"failed", "partial"}:
                    continue
                retry_text = str(checkpoint.get("next_retry_at", "")).strip().replace("Z", "+00:00")
                try:
                    retry_times.append(datetime.fromisoformat(retry_text))
                except ValueError:
                    continue
        if retry_times:
            retry_at = min(retry_times)
            comparison_now = now
            if retry_at.tzinfo is not None and comparison_now.tzinfo is None:
                comparison_now = comparison_now.astimezone()
            elif retry_at.tzinfo is None and comparison_now.tzinfo is not None:
                retry_at = retry_at.replace(tzinfo=comparison_now.tzinfo)
            return comparison_now >= retry_at
    text = str(payload.get("last_checked_at", "") if payload else last_checked_at or "").strip().replace("Z", "+00:00")
    if not text:
        return True
    try:
        checked = datetime.fromisoformat(text)
    except ValueError:
        return True
    comparison_now = now
    if checked.tzinfo is not None and comparison_now.tzinfo is None:
        comparison_now = comparison_now.astimezone()
    elif checked.tzinfo is None and comparison_now.tzinfo is not None:
        checked = checked.replace(tzinfo=comparison_now.tzinfo)
    return comparison_now - checked >= timedelta(hours=24)


def build_special_issue_notifications(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    today: date,
) -> list[dict[str, Any]]:
    """Apply the confirmed high-match, deadline and closure notification policy."""
    from utils.special_issue_policy import evaluate_special_issue
    from utils.submission_reminders import collect_special_issue_reminders

    before_items = {
        str(value.get("id", "")): value
        for value in (before.get("items", []) if isinstance(before, dict) else [])
        if isinstance(value, dict) and str(value.get("id", ""))
    }
    after_items = {
        str(value.get("id", "")): value
        for value in (after.get("items", []) if isinstance(after, dict) else [])
        if isinstance(value, dict) and str(value.get("id", ""))
    }
    logged = {
        str(value.get("id", ""))
        for value in (after.get("notification_log", []) if isinstance(after, dict) else [])
        if isinstance(value, dict) and str(value.get("id", ""))
    }
    logged.update(
        str(value.get("id", ""))
        for value in (after.get("notification_outbox", []) if isinstance(after, dict) else [])
        if isinstance(value, dict) and str(value.get("id", ""))
    )
    result: list[dict[str, Any]] = []

    def add(row: dict[str, Any]) -> None:
        if str(row.get("id", "")) not in logged:
            result.append(row)

    for issue_id, item in after_items.items():
        previous = before_items.get(issue_id)
        title = str(item.get("title", "未命名特刊")).strip() or "未命名特刊"
        match = item.get("match", {}) if isinstance(item.get("match"), dict) else {}
        score = int(match.get("score", 0) or 0)
        verification = str(item.get("verification_status", "")).casefold()
        call_status = str(item.get("call_status", "")).casefold()
        is_closed = verification in {"closed", "expired"} or call_status in {"closed", "expired"}
        is_v13 = str(item.get("scoring_version", "")).startswith("special-issue-dual-axis-13")
        v13_view = evaluate_special_issue(item, now=datetime.combine(today, datetime.min.time())) if is_v13 else {}
        high_match = (
            bool(v13_view.get("recommended")) and int(v13_view.get("score", 0) or 0) >= 80
            if is_v13
            else bool(match.get("formal", False)) and score >= 80
        )
        display_score = int(v13_view.get("score", 0) or 0) if is_v13 else score
        if previous is None and not is_closed and high_match:
            add(
                {
                    "id": f"special-issue|new-high|{issue_id}",
                    "kind": "new_high_match",
                    "issue_id": issue_id,
                    "title": "发现高匹配特刊",
                    "body": f"{title} · 双轴综合 {display_score}",
                    "score": display_score,
                }
            )
            continue
        if previous is None:
            continue
        old_deadline = str(previous.get("deadline", "")).strip()[:10]
        new_deadline = str(item.get("deadline", "")).strip()[:10]
        if old_deadline and new_deadline and old_deadline != new_deadline:
            add(
                {
                    "id": f"special-issue|deadline-changed|{issue_id}|{old_deadline}|{new_deadline}",
                    "kind": "deadline_changed",
                    "issue_id": issue_id,
                    "title": "特刊截止日期已变化",
                    "body": f"{title}：{old_deadline} → {new_deadline}",
                }
            )
        old_status = str(previous.get("verification_status", "")).casefold()
        new_status = str(item.get("verification_status", "")).casefold()
        if new_status in {"closed", "expired"} and old_status not in {"closed", "expired"}:
            add(
                {
                    "id": f"special-issue|closed|{issue_id}|{new_deadline}|{new_status}",
                    "kind": "closed",
                    "issue_id": issue_id,
                    "title": "特刊征稿状态已关闭",
                    "body": title,
                }
            )

    for reminder in collect_special_issue_reminders(after, today=today):
        row = dict(reminder)
        if row.get("kind") != "deadline":
            continue
        row.update(
            {
                "title": f"特刊截止还有 {row.get('days_remaining')} 天",
                "body": str(row.get("title", "未命名特刊")),
            }
        )
        add(row)
    order = {"new_high_match": 0, "deadline_changed": 1, "closed": 2, "deadline": 3}
    result.sort(key=lambda value: (order.get(str(value.get("kind")), 9), str(value.get("id", ""))))
    return result


def _complete_special_issue_ai_axes(value: Any) -> bool:
    """Return whether a stored v13 match contains both validated AI axes.

    Older releases persisted a single 0-100 match score.  That score is still
    useful evidence, but it must not make the v13 dual-axis pipeline think AI
    has already supplied its bounded 34-point contribution.
    """

    if not isinstance(value, dict):
        return False
    payload = value.get("ai_axis_payload", value)
    payload = payload if isinstance(payload, dict) else {}
    axes = payload.get("axes", {}) if isinstance(payload.get("axes"), dict) else {}
    for name in ("relevance", "opportunity"):
        axis = axes.get(name, {}) if isinstance(axes.get(name), dict) else {}
        try:
            adjustment = int(round(float(axis.get("adjustment"))))
        except (TypeError, ValueError):
            return False
        if not 0 <= adjustment <= 34:
            return False
        if str(axis.get("confidence", "")).casefold() not in {"medium", "high"}:
            return False
        if not [str(item).strip() for item in axis.get("evidence_refs", []) if str(item).strip()]:
            return False
    return True


def _special_issue_needs_ai_backfill(item: dict[str, Any]) -> bool:
    """Identify an open v13 record whose legacy match has no dual-axis AI."""

    if not str(item.get("scoring_version", "")).startswith("special-issue-dual-axis-13"):
        return False
    if str(item.get("verification_status", "")).casefold() in {"closed", "expired", "conflict"}:
        return False
    if not str(item.get("title", "")).strip():
        return False
    match = item.get("match") if isinstance(item.get("match"), dict) else {}
    payload = match.get("ai_axis_payload") if isinstance(match.get("ai_axis_payload"), dict) else item.get("ai_axis_payload")
    return not _complete_special_issue_ai_axes(payload)


def refresh_special_issues(
    *,
    sources: list[Any] | None = None,
    now: datetime | None = None,
    progress: Any = None,
    store: dict[str, Any] | None = None,
    journal_library: list[dict[str, Any]] | None = None,
    research_profile: dict[str, Any] | None = None,
    papers: list[dict[str, Any]] | None = None,
    cache: EvidenceCache | None = None,
    ai_matcher: Any = None,
    verifier: Any = None,
    enricher: Any = None,
    easyscholar_ready: bool | None = None,
    persist: bool = True,
    candidate_limit: int = 0,
    translate_scopes: bool | None = None,
    force: bool = False,
    cancelled: Any = None,
) -> dict[str, Any]:
    """Run discovery, official verification, metadata enrichment and AI fit."""
    from utils import file_manager
    from utils.special_issue_matching import apply_publisher_priority, build_special_issue_profiles, match_special_issue
    from utils.special_issue_repository import (
        load_special_issue_store,
        normalize_special_issue_store,
        save_special_issue_store,
        begin_special_issue_refresh,
        commit_special_issue_refresh,
    )
    from utils.special_issue_sources import default_special_issue_sources

    now = now or datetime.now()
    last_progress_value = 0

    def is_cancelled() -> bool:
        return bool(cancelled and cancelled())

    def emit(message: str, value: int) -> None:
        nonlocal last_progress_value
        if progress is None:
            return
        bounded = max(last_progress_value, max(0, min(100, int(value))))
        last_progress_value = bounded
        try:
            progress(message, bounded)
        except TypeError:
            progress(message)

    refresh_token = begin_special_issue_refresh() if persist else None
    before = normalize_special_issue_store(store if store is not None else refresh_token["store"] if refresh_token else load_special_issue_store())
    library = journal_library if journal_library is not None else file_manager.load_journal_library()
    paper_rows = papers if papers is not None else file_manager.load_papers()
    if research_profile is None:
        from utils.research_profile_repository import load_research_profile

        research_profile = load_research_profile()
    discovery_terms: list[str] = []
    for value in (research_profile or {}).get("terms", []):
        if isinstance(value, dict):
            term = str(value.get("canonical_en", value.get("text", ""))).strip()
            if term:
                discovery_terms.append(term)
    for paper in paper_rows:
        for keyword in paper.get("keywords", []) if isinstance(paper.get("keywords"), list) else []:
            term = str(keyword.get("canonical_en", keyword.get("text", ""))).strip() if isinstance(keyword, dict) else str(keyword).strip()
            if term:
                discovery_terms.append(term)
    discovery_terms = list(dict.fromkeys(discovery_terms))[:40]
    if cache is None:
        cache = EvidenceCache(file_manager.RESEARCH_INTELLIGENCE_CACHE_FILE)
    cache.initialize()
    source_rows = sources if sources is not None else default_special_issue_sources()
    if sources is None:
        from utils.source_registry import normalize_source_settings

        source_settings = normalize_source_settings(file_manager.load_app_settings().get("data_sources"))
        source_rows = [
            source
            for source in source_rows
            if bool(source_settings.get(str(getattr(source, "source_id", "")), {}).get("enabled", True))
        ]
    source_checkpoints = deepcopy(before.get("source_checkpoints", {}))
    from utils.source_registry import SOURCE_REGISTRY

    for retired_id, source_spec in SOURCE_REGISTRY.items():
        if not source_spec.get("retired"):
            continue
        retired_checkpoint = deepcopy(source_checkpoints.get(retired_id, {}))
        retired_checkpoint.update(
            {
                "status": "retired",
                "retired_at": now.isoformat(timespec="seconds"),
                "retired_reason": "official_source_disabled_in_favor_of_third_party_discovery",
                "consecutive_failures": 0,
            }
        )
        retired_checkpoint.pop("next_retry_at", None)
        retired_checkpoint.pop("last_error", None)
        source_checkpoints[retired_id] = retired_checkpoint
    retry_states = {"failed", "partial", "cancelled", "pending", "running"}
    failed_source_ids = {
        str(source_id)
        for source_id, checkpoint_value in source_checkpoints.items()
        if isinstance(checkpoint_value, dict)
        and str(checkpoint_value.get("status", "")).casefold() in retry_states
    }
    # A source may be retired between releases (Elsevier's official scraper is
    # now one such source).  Its historical failed checkpoint must not turn a
    # later retry into an empty source run.
    available_source_ids = {
        str(getattr(source, "source_id", "source"))
        for source in source_rows
    }
    failed_source_ids.intersection_update(available_source_ids)
    retrying_failed_sources = bool(
        failed_source_ids and str(before.get("last_refresh_status", "never")).casefold() != "success"
    )
    if retrying_failed_sources:
        source_rows = [
            source
            for source in source_rows
            if str(getattr(source, "source_id", "source")) in failed_source_ids
        ]
    try:
        since = datetime.fromisoformat(str(before.get("last_checked_at", "")))
    except ValueError:
        since = now - timedelta(days=7)
    emit("正在从出版社与聚合来源发现征稿…", 2)
    discovered: list[dict[str, Any]] = []
    fetched_raw = 0
    source_outcomes: list[str] = []
    attempted_sources = 0
    attempted_source_ids: set[str] = set()
    planned_source_ids = {
        str(getattr(source, "source_id", "source"))
        for source in source_rows
    }
    for index, source in enumerate(source_rows):
        if is_cancelled():
            break
        base = 3 + int(index * 19 / max(1, len(source_rows)))
        source_id = str(getattr(source, "source_id", "source"))
        previous_checkpoint = source_checkpoints.get(source_id, {})
        retry_text = str(previous_checkpoint.get("next_retry_at", "")).strip().replace("Z", "+00:00")
        try:
            retry_at = datetime.fromisoformat(retry_text)
        except ValueError:
            retry_at = None
        if retry_at is not None:
            comparison_now = now
            if retry_at.tzinfo is not None and comparison_now.tzinfo is None:
                comparison_now = comparison_now.astimezone()
            elif retry_at.tzinfo is None and comparison_now.tzinfo is not None:
                retry_at = retry_at.replace(tzinfo=comparison_now.tzinfo)
            if comparison_now < retry_at and not force:
                continue
        attempted_sources += 1
        attempted_source_ids.add(source_id)

        def source_progress(message: str, value: int = 0, *, _base=base) -> None:
            emit(message, _base + int(max(0, value) * 3 / 100))

        source_error = ""

        def commit_shard(
            *,
            shard_id: str,
            file_name: str,
            rows: list[dict[str, Any]],
            status: str,
            error: str = "",
        ) -> None:
            """Persist one completed shard without publishing it as verified."""

            nonlocal before
            current_checkpoint = deepcopy(source_checkpoints.get(source_id, previous_checkpoint))
            shards = deepcopy(current_checkpoint.get("shards", {})) if isinstance(current_checkpoint.get("shards"), dict) else {}
            shard_status = str(status).casefold()
            shards[str(shard_id)] = {
                "status": shard_status,
                "file": str(file_name),
                "row_count": len(rows),
                "updated_at": now.isoformat(timespec="seconds"),
                "error": str(error)[:240] if shard_status != "success" else "",
            }
            current_checkpoint["shards"] = shards
            current_checkpoint["status"] = "running" if shard_status == "success" else "partial"
            current_checkpoint["last_attempt_at"] = now.isoformat(timespec="seconds")
            source_checkpoints[source_id] = current_checkpoint
            normalized_rows: list[dict[str, Any]] = []
            cache_rows: list[tuple[str, dict[str, Any]]] = []
            for raw in rows:
                if not isinstance(raw, dict):
                    continue
                normalized = normalize_special_issue(
                    raw,
                    source=str(raw.get("source", source_id)),
                    fetched_at=now.isoformat(timespec="seconds"),
                    today=now.date(),
                )
                if normalized is None:
                    continue
                normalized_rows.append(normalized)
                cache_rows.append((str(normalized.get("dedupe_key", normalized["id"])), normalized))
            cache.put_special_issue_discoveries(cache_rows)
            if persist:
                shard_patch = normalize_special_issue_store(
                    {
                        **before,
                        "items": normalized_rows,
                        "source_checkpoints": source_checkpoints,
                        "last_refresh_status": "running",
                    }
                )
                before = commit_special_issue_refresh(shard_patch, token=refresh_token)

        shard_callback_enabled = False
        try:
            parameters = inspect.signature(source.fetch).parameters
            kwargs = {"since": since, "progress": source_progress}
            if "keywords" in parameters:
                kwargs["keywords"] = discovery_terms
            if "today" in parameters:
                kwargs["today"] = now.date()
            if "cancelled" in parameters:
                kwargs["cancelled"] = is_cancelled
            if "shard_checkpoints" in parameters:
                kwargs["shard_checkpoints"] = deepcopy(previous_checkpoint.get("shards", {}))
            if "retry_failed_only" in parameters:
                kwargs["retry_failed_only"] = str(previous_checkpoint.get("status", "")).casefold() in retry_states
            if "on_shard" in parameters:
                kwargs["on_shard"] = commit_shard
                shard_callback_enabled = True
            raw_rows = source.fetch(**kwargs)
        except Exception as error:  # noqa: BLE001 - source failures are isolated twice
            emit(f"一个征稿来源暂不可用，已继续：{error}", base)
            raw_rows = []
            source_status = "failed"
            source_error = str(error)[:240]
        else:
            source_status = str(getattr(raw_rows, "status", "success")).casefold()
            if source_status not in {"success", "partial", "failed"}:
                source_status = "success"
            errors = getattr(raw_rows, "errors", [])
            if errors:
                source_error = "; ".join(str(value.get("error", "")) for value in errors if isinstance(value, dict))[:240]
        previous_checkpoint = deepcopy(source_checkpoints.get(source_id, previous_checkpoint))
        source_outcomes.append(source_status)
        checkpoint = deepcopy(previous_checkpoint)
        checkpoint["status"] = source_status
        checkpoint["last_attempt_at"] = now.isoformat(timespec="seconds")
        if source_status == "success":
            checkpoint["last_success_at"] = now.isoformat(timespec="seconds")
            checkpoint["consecutive_failures"] = 0
            checkpoint.pop("next_retry_at", None)
            checkpoint.pop("last_error", None)
        else:
            failures = int(previous_checkpoint.get("consecutive_failures", 0) or 0) + 1
            retry_minutes = (15, 60, 360)[min(failures - 1, 2)]
            checkpoint["consecutive_failures"] = failures
            checkpoint["next_retry_at"] = (now + timedelta(minutes=retry_minutes)).isoformat(timespec="seconds")
            if source_error:
                checkpoint["last_error"] = source_error
            if source_status == "partial":
                checkpoint["last_success_at"] = now.isoformat(timespec="seconds")
        source_checkpoints[source_id] = checkpoint
        from utils.source_registry import record_source_outcome

        health = record_source_outcome(
            previous_checkpoint.get("health"),
            at=now,
            success=source_status == "success",
            result_count=len(raw_rows) if isinstance(raw_rows, list) else 0,
            error_type="partial" if source_status == "partial" else "source_failure" if source_status == "failed" else "",
            error=source_error,
            successful_watermark=now.isoformat(timespec="seconds") if source_status == "success" else "",
        )
        checkpoint["health"] = health
        checkpoint["health_state"] = "DEGRADED" if source_status == "partial" else health["state"]
        source_cache_rows: list[tuple[str, dict[str, Any]]] = []
        for raw in raw_rows if isinstance(raw_rows, list) else []:
            if not isinstance(raw, dict):
                continue
            fetched_raw += 1
            normalized = normalize_special_issue(
                raw,
                source=str(raw.get("source", source_id)),
                fetched_at=now.isoformat(timespec="seconds"),
                today=now.date(),
            )
            if normalized is not None:
                discovered.append(normalized)
                if not shard_callback_enabled:
                    source_cache_rows.append((str(normalized.get("dedupe_key", normalized["id"])), normalized))
        cache.put_special_issue_discoveries(source_cache_rows)
    if is_cancelled():
        for source_id in planned_source_ids - attempted_source_ids:
            checkpoint_value = deepcopy(source_checkpoints.get(source_id, {}))
            checkpoint_value["status"] = "pending"
            source_checkpoints[source_id] = checkpoint_value
        cancelled_store = normalize_special_issue_store(
            {**before, "source_checkpoints": source_checkpoints, "last_refresh_status": "cancelled"}
        )
        if persist:
            cancelled_store = commit_special_issue_refresh(cancelled_store, token=refresh_token)
        return {
            "store": cancelled_store,
            "notifications": [],
            "stats": {"raw": fetched_raw, "discovered": len(discovered), "items": 0, "eligible": 0},
        }
    if source_rows and attempted_sources == 0:
        paused_store = normalize_special_issue_store({**before, "source_checkpoints": source_checkpoints})
        if persist:
            paused_store = commit_special_issue_refresh(paused_store, token=refresh_token)
        return {
            "store": paused_store,
            "notifications": [],
            "stats": {"raw": 0, "discovered": 0, "items": 0, "eligible": 0},
        }
    all_attempted_failed = bool(source_outcomes and all(value == "failed" for value in source_outcomes))
    has_prior_results = bool(before.get("items"))
    has_prior_successful_source = any(
        isinstance(value, dict) and str(value.get("status", "")).casefold() == "success"
        for source_id, value in source_checkpoints.items()
        if source_id not in attempted_source_ids
    )
    refresh_status = (
        "partial"
        if all_attempted_failed and (has_prior_results or has_prior_successful_source)
        else "failed"
        if all_attempted_failed
        else "partial"
        if any(value in {"failed", "partial"} for value in source_outcomes)
        else "success"
    )
    needs_ai_backfill = any(
        _special_issue_needs_ai_backfill(value)
        for value in before.get("items", [])
        if isinstance(value, dict)
    )
    if all_attempted_failed and refresh_status == "partial" and not needs_ai_backfill:
        from utils.special_issue_policy import evaluate_special_issue

        retained_store = normalize_special_issue_store(
            {**before, "source_checkpoints": source_checkpoints, "last_refresh_status": "partial"}
        )
        if persist:
            retained_store = commit_special_issue_refresh(retained_store, token=refresh_token)
        retained_items = [value for value in retained_store.get("items", []) if isinstance(value, dict)]
        retained_visible = sum(
            bool(evaluate_special_issue(value, now=now, view="recommended").get("visible"))
            for value in retained_items
        )
        cache.put_job_checkpoint(
            "special_issue_refresh",
            {"checked_at": now.isoformat(timespec="seconds"), "status": "partial", "discovered": 0},
        )
        return {
            "store": retained_store,
            "notifications": [],
            "stats": {"raw": fetched_raw, "discovered": 0, "items": len(retained_items), "eligible": retained_visible},
        }
    backfill_only = bool(all_attempted_failed and refresh_status == "partial" and needs_ai_backfill)
    if refresh_status == "failed":
        failed_store = normalize_special_issue_store(
            {**before, "source_checkpoints": source_checkpoints, "last_refresh_status": "failed"}
        )
        if persist:
            failed_store = commit_special_issue_refresh(failed_store, token=refresh_token)
        cache.put_job_checkpoint(
            "special_issue_refresh",
            {"checked_at": now.isoformat(timespec="seconds"), "status": "failed", "discovered": len(discovered)},
        )
        return {
            "store": failed_store,
            "notifications": [],
            "stats": {"raw": fetched_raw, "discovered": len(discovered), "items": 0, "eligible": 0},
        }
    merged = merge_special_issue_records([*before.get("items", []), *discovered])
    all_merged = deepcopy(merged)
    if candidate_limit > 0 and len(merged) > candidate_limit:
        # Preserve explicit feedback, while allowing fresh high-relevance calls
        # to replace stale unread rows when the discovery cache is full.
        retained = [
            value
            for value in merged
            if str(value.get("status", "")).casefold() in {"saved", "ignored"}
        ]
        retained_ids = {str(value.get("id", "")) for value in retained}
        remaining = [value for value in merged if str(value.get("id", "")) not in retained_ids]
        term_tokens = [
            (term.casefold(), {token for token in re.findall(r"[a-z0-9]+", term.casefold()) if len(token) >= 4})
            for term in discovery_terms
        ]

        def relevance(value: dict[str, Any]) -> int:
            text = " ".join(
                str(value.get(field, ""))
                for field in ("title", "scope_text", "journal")
            ).casefold()
            normalized_text = " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())
            padded_text = f" {normalized_text} "
            text_tokens = set(normalized_text.split())
            score = 0
            for phrase, tokens in term_tokens:
                normalized_phrase = " ".join(re.sub(r"[^a-z0-9]+", " ", phrase).split())
                if normalized_phrase and f" {normalized_phrase} " in padded_text:
                    score += 12
                score += sum(1 for token in tokens if token in text_tokens)
            return score

        remaining.sort(key=lambda value: (-relevance(value), str(value.get("deadline", "9999-99-99")), str(value.get("title", "")).casefold()))
        budget = max(0, candidate_limit - len(retained))
        relevant = [value for value in remaining if relevance(value) > 0]
        exploratory = [value for value in remaining if relevance(value) == 0]
        exploration_budget = min(12, budget)
        ranked = [*relevant, *exploratory]
        publisher_groups: dict[str, list[dict[str, Any]]] = {}
        for value in ranked:
            publisher = canonical_publisher(value.get("publisher", "")) or "unknown"
            publisher_groups.setdefault(publisher, []).append(value)
        selected_ids = {str(value.get("id", "")) for value in retained}
        while budget > 0 and any(publisher_groups.values()):
            for publisher in list(publisher_groups):
                group = publisher_groups[publisher]
                while group and str(group[0].get("id", "")) in selected_ids:
                    group.pop(0)
                if not group:
                    continue
                value = group.pop(0)
                retained.append(value)
                selected_ids.add(str(value.get("id", "")))
                budget -= 1
                if budget <= 0:
                    break
        merged = retained
    processed_ids = {str(value.get("id", "")) for value in merged}
    deferred = [value for value in all_merged if str(value.get("id", "")) not in processed_ids]
    if retrying_failed_sources:
        # A successful retry belongs only to the sources that previously
        # failed.  Re-check newly returned/changed records plus local v13 AI
        # backfills, while carrying every healthy historical row forward
        # untouched.  This avoids a minutes-long full-library verification on
        # every small source recovery.
        retry_keys = {
            special_issue_dedupe_key(value)
            for value in discovered
            if isinstance(value, dict)
        }
        retry_items: list[dict[str, Any]] = []
        retry_deferred = list(deferred)
        for value in merged:
            if special_issue_dedupe_key(value) in retry_keys or _special_issue_needs_ai_backfill(value):
                retry_items.append(value)
            else:
                retry_deferred.append(value)
        merged = retry_items
        deferred = retry_deferred
    emit("正在逐项检查官网、截止日期与征稿状态…", 25)
    verifier = verifier or verify_special_issue
    enricher = enricher or enrich_special_issue_journal
    if easyscholar_ready is None:
        try:
            from utils.easyscholar_service import is_easyscholar_ready

            easyscholar_ready = bool(is_easyscholar_ready())
        except Exception:
            easyscholar_ready = False
    verified: list[dict[str, Any]] = []
    if backfill_only:
        # Failed publisher retries must not re-fetch every healthy official
        # page.  They may still finish a one-time local v13 AI-score backfill
        # for records already present in the store.
        emit("来源补跑失败，正在补齐历史特刊的双轴 AI 评分…", 25)
        verified = [deepcopy(item) for item in merged]
    else:
        for index, item in enumerate(merged):
            if is_cancelled():
                break
            emit(f"正在核验征稿 {index + 1}/{len(merged)}…", 25 + int((index + 1) * 25 / max(1, len(merged))))
            try:
                checked = verifier(item, now=now, cache=cache)
            except Exception as error:  # noqa: BLE001 - one broken page stays pending
                checked = deepcopy(item)
                checked["verification_status"] = "temporarily_unavailable"
                checked["verification_error"] = str(error)[:240]
            try:
                checked = enricher(checked, library, easyscholar_ready=bool(easyscholar_ready))
            except Exception as error:  # noqa: BLE001 - unknown facts remain visible
                checked["journal_enrichment_error"] = str(error)[:240]
            verified.append(checked)
    if is_cancelled():
        cancelled_store = normalize_special_issue_store(
            {**before, "source_checkpoints": source_checkpoints, "last_refresh_status": "cancelled"}
        )
        if persist:
            cancelled_store = commit_special_issue_refresh(cancelled_store, token=refresh_token)
        return {
            "store": cancelled_store,
            "notifications": [],
            "stats": {"raw": fetched_raw, "discovered": len(discovered), "items": 0, "eligible": 0},
        }
    should_translate = bool(persist) if translate_scopes is None else bool(translate_scopes)
    if should_translate:
        from utils.local_translation import translate_special_issue_scope

        translatable = [
            value
            for value in verified
            if str(value.get("scope_text", "")).strip()
            and (
                not str(value.get("scope_text_zh", "")).strip()
                or str(value.get("scope_translation_signature", ""))
                != hashlib.sha256(str(value.get("scope_text", "")).encode("utf-8")).hexdigest()
            )
        ]
        for index, item in enumerate(translatable, start=1):
            if is_cancelled():
                break
            emit(
                f"正在离线翻译征稿范围 {index}/{len(translatable)}…",
                51 + int(index * 4 / max(1, len(translatable))),
            )
            try:
                item["scope_text_zh"] = translate_special_issue_scope(str(item.get("scope_text", "")))
                item["scope_translation_signature"] = hashlib.sha256(
                    str(item.get("scope_text", "")).encode("utf-8")
                ).hexdigest()
                item.pop("scope_translation_error", None)
            except Exception as error:  # noqa: BLE001 - source scope remains visible
                item["scope_translation_error"] = str(error)[:240]
    emit("正在用综合画像与逐篇论文画像计算匹配…", 55)
    profiles = build_special_issue_profiles(research_profile or {}, paper_rows)
    profile_signature = hashlib.sha256(
        json.dumps(profiles, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    matched: list[dict[str, Any]] = []
    eligible = [
        value
        for value in verified
        if str(value.get("verification_status", "")) not in {"closed", "expired", "conflict"}
        and str(value.get("title", "")).strip()
    ]
    eligible_ids = {str(value.get("id", "")) for value in eligible}
    completed = 0
    for item in verified:
        if is_cancelled():
            break
        result = deepcopy(item)
        if backfill_only and not _special_issue_needs_ai_backfill(result):
            matched.append(result)
            continue
        if str(item.get("id", "")) in eligible_ids:
            completed += 1
            scope_signature = hashlib.sha256(
                json.dumps(
                    {
                        key: item.get(key)
                        for key in ("title", "journal", "publisher", "keywords", "scope_text", "scope_status", "deadline", "rolling")
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
            previous_match = result.get("match") if isinstance(result.get("match"), dict) else None
            requires_v13_axes = str(result.get("scoring_version", "")).startswith("special-issue-dual-axis-13")
            stored_ai_payload = (
                previous_match.get("ai_axis_payload")
                if isinstance(previous_match, dict) and isinstance(previous_match.get("ai_axis_payload"), dict)
                else result.get("ai_axis_payload")
            )
            reusable = (
                previous_match is not None
                and str(result.get("match_profile_signature", "")) == profile_signature
                and str(result.get("match_scope_signature", "")) == scope_signature
                and str(previous_match.get("status", "")) not in {"ai_unavailable", "awaiting_scope"}
                and (not requires_v13_axes or _complete_special_issue_ai_axes(stored_ai_payload))
            )

            def match_progress(message: str, value: int = 0, *, _position=completed) -> None:
                overall = 55 + int(((_position - 1) + max(0, min(100, value)) / 100) * 39 / max(1, len(eligible)))
                emit(message, overall)

            if reusable:
                match_progress("画像与征稿未变化，已复用上次 AI 评分", 100)
            else:
                try:
                    refreshed_match = match_special_issue(
                        item,
                        profiles,
                        ai_matcher=ai_matcher,
                        progress=match_progress,
                    )
                    if str(refreshed_match.get("status", "")).casefold() in {"ai_unavailable", "invalid_response"}:
                        result["ai_review_status"] = "failed"
                        result["ai_review_error"] = str(refreshed_match.get("reason", "AI 双轴评分暂不可用"))[:240]
                        if previous_match is None:
                            result["match"] = refreshed_match
                    else:
                        result["match"] = refreshed_match
                        result["match_profile_signature"] = profile_signature
                        result["match_scope_signature"] = scope_signature
                        result["ai_review_status"] = "success"
                        result.pop("ai_review_error", None)
                        result.pop("match_error", None)
                except Exception as error:  # noqa: BLE001 - preserve the last successful match
                    result["match_error"] = str(error)[:240]
                    result["ai_review_status"] = "failed"
                    result["ai_review_error"] = str(error)[:240]
                    if not isinstance(result.get("match"), dict):
                        result["match"] = {
                            "score": None,
                            "formal": False,
                            "status": "ai_unavailable",
                            "reason": "AI 匹配暂不可用，已保留征稿并等待下次重试。",
                            "matched_papers": [],
                        }
        elif not isinstance(result.get("match"), dict):
            result["match"] = {
                "score": None,
                "formal": False,
                "status": "awaiting_scope" if not str(result.get("scope_text", "")).strip() else "not_admitted",
                "reason": "等待完整征稿范围或官网核验。",
                "matched_papers": [],
            }
        current_match = result.get("match") if isinstance(result.get("match"), dict) else None
        if current_match is not None and isinstance(current_match.get("ai_axis_payload"), dict):
            result["ai_axis_payload"] = deepcopy(current_match["ai_axis_payload"])
        if current_match is not None and str(result.get("title", "")).strip():
            raw_match_score = current_match.get("raw_score", current_match.get("score"))
            try:
                base_score = int(raw_match_score) if raw_match_score is not None else None
            except (TypeError, ValueError):
                base_score = None
            if base_score is None:
                current_match["raw_score"] = None
                current_match["score"] = None
                current_match["rank_score"] = None
                current_match["publisher_multiplier"] = None
            else:
                rank_score, publisher_multiplier = apply_publisher_priority(
                    base_score, result.get("publisher", "")
                )
                current_match["raw_score"] = base_score
                current_match["score"] = base_score
                current_match["rank_score"] = rank_score
                current_match["publisher_multiplier"] = publisher_multiplier
            current_match["content_qualified"] = bool(
                current_match.get("content_qualified", current_match.get("formal", False))
            )
            current_match["formal"] = bool(current_match.get("formal", current_match["content_qualified"]))
        from utils.v13_policy import score_special_issue, special_issue_display_decision

        local_journal = _local_journal(result, library)
        result = score_special_issue(
            result,
            research_profile or {},
            local_journal,
            today=now.date(),
            ai_payload=result.get("ai_axis_payload"),
        )
        decision = special_issue_display_decision(result, today=now.date())
        result["candidate_state"] = decision["state"]
        result["display_reason"] = decision["reason"]
        result["display_missing_fields"] = decision["missing_fields"]
        evidence = result.get("source_evidence", []) if isinstance(result.get("source_evidence"), list) else []
        is_third_party = bool(result.get("is_aggregator")) or any(
            bool(value.get("is_aggregator")) for value in evidence if isinstance(value, dict)
        )
        if is_third_party and str(result.get("verification_status", "")).casefold() != "official_verified":
            result["verification_label"] = "官网暂未核验"
        matched.append(result)
    if is_cancelled():
        cancelled_store = normalize_special_issue_store(
            {**before, "source_checkpoints": source_checkpoints, "last_refresh_status": "cancelled"}
        )
        if persist:
            cancelled_store = commit_special_issue_refresh(cancelled_store, token=refresh_token)
        return {
            "store": cancelled_store,
            "notifications": [],
            "stats": {"raw": fetched_raw, "discovered": len(discovered), "items": 0, "eligible": 0},
        }
    after = normalize_special_issue_store(
        {
            **before,
            "items": [*matched, *deferred],
            "last_checked_at": now.isoformat(timespec="seconds"),
            "last_refresh_status": refresh_status,
            "source_checkpoints": source_checkpoints,
        }
    )
    from utils.special_issue_policy import evaluate_special_issue

    priority_publishers = {"Elsevier", "Springer Nature", "Taylor & Francis", "Wiley"}

    def display_sort_key(value: dict[str, Any]) -> tuple[Any, ...]:
        decision = evaluate_special_issue(value, now=now, view="recommended")
        visible = bool(decision.get("visible"))
        publisher = canonical_publisher(value.get("publisher", ""))
        priority = publisher in priority_publishers
        tier = {"A": 0, "B": 1, "C": 2}.get(str(value.get("pyramid_level", "")), 3)
        relevance = int(value.get("relevance_score", 0) or 0)
        opportunity = int(value.get("opportunity_score", 0) or 0)
        return (0 if visible else 1, 0 if priority and visible else 1, tier, -relevance, -opportunity, str(value.get("deadline", "9999-99-99")), str(value.get("title", "")).casefold())

    after["items"] = sorted(after.get("items", []), key=display_sort_key)
    recommended_count = sum(
        bool(evaluate_special_issue(value, now=now, view="recommended").get("visible"))
        for value in after.get("items", [])
        if isinstance(value, dict)
    )
    notifications = build_special_issue_notifications(before, after, today=now.date())
    at = now.isoformat(timespec="seconds")
    outbox = [dict(value) for value in after.get("notification_outbox", []) if isinstance(value, dict)]
    queued_ids = {str(value.get("id", "")) for value in outbox}
    for notification in notifications:
        if str(notification.get("id", "")) not in queued_ids:
            outbox.append({**notification, "state": "pending", "created_at": at})
    after["notification_outbox"] = outbox[-500:]
    if persist:
        after = commit_special_issue_refresh(after, token=refresh_token)
    cache.put_job_checkpoint(
        "special_issue_refresh",
        {"checked_at": at, "discovered": len(discovered), "retained": len(matched), "notifications": len(notifications)},
    )
    emit("特刊发现、官网核验与画像匹配完成", 100)
    return {
        "store": after,
        "notifications": notifications,
        "stats": {
            "raw": fetched_raw,
            "discovered": len(discovered),
            "items": len(after.get("items", [])),
            "eligible": recommended_count,
        },
    }
