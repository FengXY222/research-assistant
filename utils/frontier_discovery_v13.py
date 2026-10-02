"""Six-source, six-strategy Daily Frontier discovery for version 13."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request

from utils.api_rate_limit import rate_limited_urlopen as urlopen

from utils.evidence_cache import EvidenceCache
from utils.source_registry import (
    RECALL_STRATEGIES,
    SOURCE_REGISTRY,
    normalize_health,
    recall_outcome,
    record_source_outcome,
    runtime_source_config,
    source_due,
)
from utils.v13_policy import merge_work_families, normalized_doi


EUROPE_PMC_API = "https://www.ebi.ac.uk/europepmc/webservices/rest"
OPENALEX_API = "https://api.openalex.org"
SEMANTIC_API = "https://api.semanticscholar.org/graph/v1"
SEMANTIC_RECOMMENDATIONS_API = "https://api.semanticscholar.org/recommendations/v1"
CROSSREF_API = "https://api.crossref.org"
USER_AGENT = "ResearchAssistant/13.0 (personal desktop research tool; contact configured in Settings)"


def normalize_issns(value: Any) -> list[str]:
    """Extract canonical ISSNs from heterogeneous API/library payloads."""

    candidates: list[Any] = []

    def collect(raw: Any, *, issn_context: bool = False) -> None:
        if isinstance(raw, dict):
            preferred = (
                "issn", "issns", "ISSN", "issn_l", "print_issn", "electronic_issn",
                "pissn", "eissn", "pISSN", "eISSN",
            )
            found = False
            for key in preferred:
                if key in raw:
                    found = True
                    collect(raw.get(key), issn_context=True)
            if issn_context and not found:
                for child in raw.values():
                    collect(child, issn_context=True)
            return
        if isinstance(raw, (list, tuple, set)):
            for child in raw:
                collect(child, issn_context=issn_context)
            return
        candidates.append(raw)

    collect(value, issn_context=not isinstance(value, dict))
    result: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        text = unicodedata.normalize("NFKC", str(candidate or "")).upper()
        for match in re.finditer(r"(?<!\d)(\d{4})[-\s]?(\d{3}[\dX])(?!\d)", text):
            issn = f"{match.group(1)}-{match.group(2)}"
            if issn not in seen:
                seen.add(issn)
                result.append(issn)
    return result


def _request_json(url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    from PySide6.QtCore import QThread
    if QThread.currentThread().isInterruptionRequested():
        raise InterruptedError("发现任务已暂停")
    query = urlencode({key: value for key, value in (params or {}).items() if value not in {None, ""}})
    address = f"{url}?{query}" if query else url
    request_headers = {"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})}
    request = Request(address, headers=request_headers)
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - public fixed scholarly APIs
            value = json.loads(response.read().decode("utf-8"))
        if QThread.currentThread().isInterruptionRequested():
            raise InterruptedError("发现任务已暂停")
    except HTTPError as error:
        kind = "authentication" if error.code in {401, 403} else "rate_limit" if error.code == 429 else "http"
        raise RuntimeError(f"{kind}: HTTP {error.code}") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError(f"network: {error}") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("data: 来源返回了无法解析的 JSON") from error
    if not isinstance(value, dict):
        raise RuntimeError("data: 来源返回格式异常")
    return value


def _request_bytes(url: str, params: dict[str, Any] | None = None) -> bytes:
    from PySide6.QtCore import QThread
    if QThread.currentThread().isInterruptionRequested():
        raise InterruptedError("发现任务已暂停")
    query = urlencode({key: value for key, value in (params or {}).items() if value not in {None, ""}})
    address = f"{url}?{query}" if query else url
    request = Request(address, headers={"Accept": "application/atom+xml,application/rss+xml", "User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=20) as response:  # noqa: S310 - user-owned journal feed or fixed public API
            value = response.read()
        if QThread.currentThread().isInterruptionRequested():
            raise InterruptedError("发现任务已暂停")
        return value
    except (HTTPError, URLError, TimeoutError) as error:
        raise RuntimeError(f"network: {error}") from error


def _clean(value: Any) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", str(value or "")).split())


def _date(value: Any) -> str:
    text = str(value or "").strip()
    match = re.search(r"(?:19|20)\d{2}(?:-\d{1,2}(?:-\d{1,2})?)?", text)
    if not match:
        return ""
    parts = match.group(0).split("-")
    try:
        return date(int(parts[0]), int(parts[1]) if len(parts) > 1 else 1, int(parts[2]) if len(parts) > 2 else 1).isoformat()
    except ValueError:
        return ""


def _terms(profile: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for value in profile.get("active_terms", profile.get("terms", [])):
        if isinstance(value, dict):
            term = str(value.get("canonical_en", value.get("text", value.get("title", "")))).strip()
        else:
            term = str(value).strip()
        if len(term) >= 3 and term.casefold() not in {entry.casefold() for entry in result}:
            result.append(term)
    if not result:
        for field in ("primary_keywords", "secondary_keywords"):
            for value in profile.get(field, []) if isinstance(profile.get(field), list) else []:
                term = str(value).strip()
                if term and term.casefold() not in {entry.casefold() for entry in result}:
                    result.append(term)
    return result


def _seed_identity(seed: dict[str, Any]) -> dict[str, str]:
    ids = seed.get("source_ids", {}) if isinstance(seed.get("source_ids"), dict) else {}
    return {
        "doi": normalized_doi(seed.get("doi")),
        "openalex_id": str(seed.get("openalex_id", ids.get("openalex", ""))).strip(),
        "semantic_scholar_id": str(seed.get("semantic_scholar_id", ids.get("semantic_scholar", ""))).strip(),
        "title": str(seed.get("title", "")).strip(),
        "id": str(seed.get("id", "")).strip(),
    }


def _trusted_paper_seeds(profile: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    values = [
        *profile.get("positive_seeds", []),
        *profile.get("semantic_seeds", []),
        *profile.get("citation_seeds", []),
    ]
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        kind = str(value.get("kind", "")).strip().casefold()
        confirmed = bool(value.get("confirmed") or value.get("locked") or kind in {"authored_paper", "favorite", "saved", "explicit_positive"})
        if not confirmed:
            continue
        seed = _seed_identity(value)
        stable = seed["doi"] or seed["openalex_id"] or seed["semantic_scholar_id"]
        if not stable or stable.casefold() in seen:
            continue
        seen.add(stable.casefold())
        result.append(seed)
    return result


def _watched_journals(journals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for journal in journals:
        if not isinstance(journal, dict):
            continue
        priority = str(journal.get("frontier_priority", "")).strip()
        try:
            score = int(journal.get("frontier_score", 0) or 0)
        except (TypeError, ValueError):
            score = 0
        if priority not in {"必看", "关注"} and score < 80:
            continue
        issns = normalize_issns(journal)
        result.append(
            {
                "id": str(journal.get("id", "")),
                "name": str(journal.get("name", "")),
                "issns": issns,
                "openalex_id": str(journal.get("openalex_id", "")),
                "feed_url": str(journal.get("feed_url", journal.get("toc_rss", ""))).strip(),
                "priority": priority,
            }
        )
    return result


def plan_recall(profile: dict[str, Any], journals: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Build six genuinely different lanes; no lane is a renamed title search."""

    terms = _terms(profile)
    seeds = _trusted_paper_seeds(profile)
    authors = [value for value in profile.get("confirmed_authors", []) if isinstance(value, dict)]
    watched = _watched_journals(journals)
    confirmed_topics = [value for value in profile.get("confirmed_topics", []) if isinstance(value, (dict, str))]
    topic_seeds = confirmed_topics or terms
    return {
        "keyword": [{"query": value, "seed": value} for value in terms],
        "semantic_related": [{"seed": deepcopy(value), "query": value.get("doi") or value.get("openalex_id") or value.get("semantic_scholar_id")} for value in seeds],
        "citation_network": [{"seed": deepcopy(value), "query": value.get("doi") or value.get("openalex_id") or value.get("semantic_scholar_id")} for value in seeds],
        "confirmed_author_team": [
            {
                "seed": {
                    "id": str(value.get("id", "")),
                    "name": str(value.get("name", "")),
                    "openalex_id": str(value.get("openalex_id", "")),
                    "semantic_scholar_id": str(value.get("semantic_scholar_id", "")),
                    "orcid": str(value.get("orcid", "")),
                },
                "query": str(value.get("openalex_id", value.get("semantic_scholar_id", value.get("orcid", "")))),
            }
            for value in authors
            if value.get("confirmed", True) and any(str(value.get(field, "")).strip() for field in ("openalex_id", "semantic_scholar_id", "orcid"))
        ],
        "watched_journal": [{"seed": deepcopy(value), "query": value.get("issns", [value.get("name", "")])[0] if value.get("issns") else value.get("name", "")} for value in watched],
        "topic_expansion": [
            {
                "seed": deepcopy(value) if isinstance(value, dict) else {"name": str(value)},
                "query": str(value.get("openalex_topic_id", value.get("mesh", value.get("name", "")))) if isinstance(value, dict) else str(value),
            }
            for value in topic_seeds
            if str(value.get("openalex_topic_id", value.get("mesh", value.get("name", ""))) if isinstance(value, dict) else value).strip()
        ],
    }


def _openalex_record(work: dict[str, Any]) -> dict[str, Any]:
    location = work.get("primary_location", {}) if isinstance(work.get("primary_location"), dict) else {}
    source = location.get("source", {}) if isinstance(location.get("source"), dict) else {}
    abstract_index = work.get("abstract_inverted_index", {}) if isinstance(work.get("abstract_inverted_index"), dict) else {}
    positions: dict[int, str] = {}
    for word, indexes in abstract_index.items():
        for index in indexes if isinstance(indexes, list) else []:
            if isinstance(index, int):
                positions[index] = str(word)
    return {
        "source_id": str(work.get("id", "")),
        "title": str(work.get("display_name", work.get("title", ""))),
        "abstract": " ".join(positions[index] for index in sorted(positions)),
        "authors": [
            str(value.get("author", {}).get("display_name", ""))
            for value in work.get("authorships", []) if isinstance(value, dict) and isinstance(value.get("author"), dict)
        ],
        "doi": str(work.get("doi", "")),
        "journal": str(source.get("display_name", "")),
        "issn": normalize_issns(source),
        "publisher": str(source.get("host_organization_name", "")),
        "published_date": _date(work.get("publication_date", "")),
        "url": str(location.get("landing_page_url", "")) or str(work.get("doi", "")),
        "topic_ids": [str(value.get("id", "")) for value in work.get("topics", []) if isinstance(value, dict)],
    }


def _semantic_record(paper: dict[str, Any]) -> dict[str, Any]:
    ids = paper.get("externalIds", {}) if isinstance(paper.get("externalIds"), dict) else {}
    journal = paper.get("journal", {}) if isinstance(paper.get("journal"), dict) else {}
    return {
        "source_id": str(paper.get("paperId", "")),
        "title": str(paper.get("title", "")),
        "abstract": str(paper.get("abstract", "")),
        "authors": [str(value.get("name", "")) for value in paper.get("authors", []) if isinstance(value, dict)],
        "doi": str(ids.get("DOI", "")),
        "journal": str(paper.get("venue", journal.get("name", ""))),
        "published_date": _date(paper.get("publicationDate", paper.get("year", ""))),
        "url": str(paper.get("url", "")),
    }


def _europe_pmc_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result_list = payload.get("resultList", {}) if isinstance(payload.get("resultList"), dict) else {}
    rows = result_list.get("result", []) if isinstance(result_list.get("result"), list) else []
    result: list[dict[str, Any]] = []
    for value in rows:
        if not isinstance(value, dict):
            continue
        result.append(
            {
                "source_id": str(value.get("id", value.get("pmcid", value.get("pmid", "")))),
                "title": str(value.get("title", "")),
                "abstract": str(value.get("abstractText", "")),
                "authors": [part.strip() for part in str(value.get("authorString", "")).split(",") if part.strip()],
                "doi": str(value.get("doi", "")),
                "journal": str(value.get("journalTitle", value.get("journalInfo", {}).get("journal", {}).get("title", "") if isinstance(value.get("journalInfo"), dict) else "")),
                "published_date": _date(value.get("firstPublicationDate", value.get("journalInfo", {}).get("printPublicationDate", "") if isinstance(value.get("journalInfo"), dict) else "")),
                "url": f"https://europepmc.org/article/{value.get('source', 'MED')}/{value.get('id', '')}",
                "is_preprint": str(value.get("pubType", "")).casefold() == "preprint",
                "author_keywords": [str(entry).strip() for entry in value.get("keywordList", {}).get("keyword", [])] if isinstance(value.get("keywordList"), dict) else [],
            }
        )
    return result


def _fetch_europe_pmc(query: str, start: date, today: date) -> list[dict[str, Any]]:
    period = f" AND FIRST_PDATE:[{start.isoformat()} TO {today.isoformat()}]"
    payload = _request_json(
        f"{EUROPE_PMC_API}/search",
        {"query": f"({query}){period}", "format": "json", "pageSize": 100, "resultType": "core"},
    )
    return _europe_pmc_records(payload)


def _resolve_openalex(seed: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    identifier = str(seed.get("openalex_id", "")).strip()
    if not identifier and seed.get("doi"):
        identifier = "https://doi.org/" + str(seed["doi"])
    if not identifier:
        return {}
    params = {"api_key": config.get("api_key", "")} if config.get("api_key") else {}
    return _request_json(f"{OPENALEX_API}/works/{quote(identifier, safe=':/')}", params)


def _resolve_semantic_id(seed: dict[str, Any], config: dict[str, Any]) -> str:
    identifier = str(seed.get("semantic_scholar_id", "")).strip()
    if identifier:
        return identifier
    if not seed.get("doi"):
        return ""
    headers = {"x-api-key": config["api_key"]} if config.get("api_key") else {}
    payload = _request_json(
        f"{SEMANTIC_API}/paper/DOI:{quote(str(seed['doi']), safe='')}",
        {"fields": "paperId"},
        headers,
    )
    return str(payload.get("paperId", ""))


def _fetch_keyword(source: str, query: str, start: date, today: date, config: dict[str, Any]) -> list[dict[str, Any]]:
    if source == "europe_pmc":
        return _fetch_europe_pmc(query, start, today)
    from utils.frontier_service import _fetch_v12_source

    return _fetch_v12_source(source, query, start, today, str(config.get("api_key", "")))


def _fetch_semantic_related(source: str, seed: dict[str, Any], start: date, today: date, config: dict[str, Any]) -> list[dict[str, Any]]:
    if source == "openalex":
        work = _resolve_openalex(seed, config)
        ids = [str(value) for value in work.get("related_works", []) if str(value)][:100]
        if not ids:
            return []
        payload = _request_json(
            f"{OPENALEX_API}/works",
            {"filter": "openalex_id:" + "|".join(ids), "per-page": 100, "api_key": config.get("api_key", "")},
        )
        return [_openalex_record(value) for value in payload.get("results", []) if isinstance(value, dict)]
    if source == "semantic_scholar":
        paper_id = _resolve_semantic_id(seed, config)
        if not paper_id:
            return []
        headers = {"x-api-key": config["api_key"]} if config.get("api_key") else {}
        payload = _request_json(
            f"{SEMANTIC_RECOMMENDATIONS_API}/papers/forpaper/{quote(paper_id, safe='')}",
            {"limit": 100, "fields": "title,abstract,venue,publicationDate,externalIds,url,authors,journal"},
            headers,
        )
        return [_semantic_record(value) for value in payload.get("recommendedPapers", []) if isinstance(value, dict)]
    return []


def _fetch_citations(source: str, seed: dict[str, Any], start: date, today: date, config: dict[str, Any]) -> list[dict[str, Any]]:
    if source == "openalex":
        work = _resolve_openalex(seed, config)
        work_id = str(work.get("id", ""))
        rows: list[dict[str, Any]] = []
        if work_id:
            payload = _request_json(
                f"{OPENALEX_API}/works",
                {"filter": f"cites:{work_id}", "per-page": 100, "api_key": config.get("api_key", "")},
            )
            rows.extend(_openalex_record(value) for value in payload.get("results", []) if isinstance(value, dict))
        reference_ids = [str(value) for value in work.get("referenced_works", []) if str(value)][:100]
        if reference_ids:
            payload = _request_json(
                f"{OPENALEX_API}/works",
                {"filter": "openalex_id:" + "|".join(reference_ids), "per-page": 100, "api_key": config.get("api_key", "")},
            )
            rows.extend(_openalex_record(value) for value in payload.get("results", []) if isinstance(value, dict))
        return rows
    if source == "semantic_scholar":
        paper_id = _resolve_semantic_id(seed, config)
        if not paper_id:
            return []
        headers = {"x-api-key": config["api_key"]} if config.get("api_key") else {}
        fields = "title,abstract,venue,publicationDate,externalIds,url,authors,journal"
        rows: list[dict[str, Any]] = []
        for relation in ("citations", "references"):
            payload = _request_json(
                f"{SEMANTIC_API}/paper/{quote(paper_id, safe='')}/{relation}",
                {"limit": 100, "fields": fields},
                headers,
            )
            side = "citingPaper" if relation == "citations" else "citedPaper"
            rows.extend(_semantic_record(value[side]) for value in payload.get("data", []) if isinstance(value, dict) and isinstance(value.get(side), dict))
        return rows
    return []


def _fetch_author(source: str, seed: dict[str, Any], start: date, today: date, config: dict[str, Any]) -> list[dict[str, Any]]:
    if source == "openalex":
        author_id = str(seed.get("openalex_id", "")).strip()
        orcid = str(seed.get("orcid", "")).strip()
        filter_value = f"authorships.author.id:{author_id}" if author_id else f"authorships.author.orcid:https://orcid.org/{orcid}" if orcid else ""
        if not filter_value:
            return []
        payload = _request_json(
            f"{OPENALEX_API}/works",
            {"filter": f"{filter_value},from_publication_date:{start.isoformat()}", "per-page": 100, "api_key": config.get("api_key", "")},
        )
        return [_openalex_record(value) for value in payload.get("results", []) if isinstance(value, dict)]
    if source == "semantic_scholar" and seed.get("semantic_scholar_id"):
        headers = {"x-api-key": config["api_key"]} if config.get("api_key") else {}
        payload = _request_json(
            f"{SEMANTIC_API}/author/{quote(str(seed['semantic_scholar_id']), safe='')}/papers",
            {"limit": 100, "fields": "title,abstract,venue,publicationDate,externalIds,url,authors,journal"},
            headers,
        )
        return [_semantic_record(value) for value in payload.get("data", []) if isinstance(value, dict)]
    if source == "europe_pmc" and seed.get("orcid"):
        return _fetch_europe_pmc(f"AUTHORID:{seed['orcid']}", start, today)
    return []


def _crossref_record(value: dict[str, Any]) -> dict[str, Any]:
    title = value.get("title", []) if isinstance(value.get("title"), list) else []
    venue = value.get("container-title", []) if isinstance(value.get("container-title"), list) else []
    author_values = value.get("author", []) if isinstance(value.get("author"), list) else []
    return {
        "source_id": str(value.get("DOI", "")),
        "title": str(title[0]) if title else "",
        "abstract": _clean(value.get("abstract", "")),
        "authors": [" ".join(part for part in (str(author.get("given", "")), str(author.get("family", ""))) if part).strip() for author in author_values if isinstance(author, dict)],
        "doi": str(value.get("DOI", "")),
        "journal": str(venue[0]) if venue else "",
        "issn": value.get("ISSN", []),
        "publisher": str(value.get("publisher", "")),
        "published_date": _date(json.dumps(value.get("published", value.get("created", {})))),
        "url": str(value.get("URL", "")),
    }


def _fetch_journal(source: str, seed: dict[str, Any], start: date, today: date, config: dict[str, Any]) -> list[dict[str, Any]]:
    issns = seed.get("issns", []) if isinstance(seed.get("issns"), list) else []
    if source == "crossref" and issns:
        headers = {}
        if config.get("contact_email"):
            headers["User-Agent"] = f"{USER_AGENT}; mailto:{config['contact_email']}"
        payload = _request_json(
            f"{CROSSREF_API}/journals/{quote(str(issns[0]), safe='')}/works",
            {"filter": f"from-pub-date:{start.isoformat()},until-pub-date:{today.isoformat()}", "rows": 100, "sort": "published", "order": "desc"},
            headers,
        )
        message = payload.get("message", {}) if isinstance(payload.get("message"), dict) else {}
        return [_crossref_record(value) for value in message.get("items", []) if isinstance(value, dict)]
    if source == "openalex" and (issns or seed.get("openalex_id")):
        source_filter = f"primary_location.source.id:{seed['openalex_id']}" if seed.get("openalex_id") else f"primary_location.source.issn:{issns[0]}"
        payload = _request_json(
            f"{OPENALEX_API}/works",
            {"filter": f"{source_filter},from_publication_date:{start.isoformat()}", "per-page": 100, "api_key": config.get("api_key", "")},
        )
        return [_openalex_record(value) for value in payload.get("results", []) if isinstance(value, dict)]
    if source == "publisher_toc" and seed.get("feed_url"):
        raw = _request_bytes(str(seed["feed_url"]))
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as error:
            raise RuntimeError("data: 期刊 TOC/RSS 无法解析") from error
        rows = []
        for entry in [*root.findall(".//item"), *root.findall(".//{http://www.w3.org/2005/Atom}entry")]:
            title = entry.findtext("title") or entry.findtext("{http://www.w3.org/2005/Atom}title") or ""
            link = entry.findtext("link") or entry.findtext("{http://www.w3.org/2005/Atom}id") or ""
            if not link:
                atom_link = entry.find("{http://www.w3.org/2005/Atom}link")
                link = atom_link.get("href", "") if atom_link is not None else ""
            published = entry.findtext("pubDate") or entry.findtext("{http://www.w3.org/2005/Atom}published") or entry.findtext("{http://www.w3.org/2005/Atom}updated") or ""
            rows.append({"source_id": link, "title": _clean(title), "journal": seed.get("name", ""), "published_date": _date(published), "url": link, "abstract": _clean(entry.findtext("description") or entry.findtext("{http://www.w3.org/2005/Atom}summary") or ""), "official_toc": True})
        return rows
    return []


def _fetch_topic(source: str, seed: dict[str, Any], query: str, start: date, today: date, config: dict[str, Any]) -> list[dict[str, Any]]:
    if source == "openalex":
        topic_id = str(seed.get("openalex_topic_id", "")).strip()
        if not topic_id:
            topics = _request_json(f"{OPENALEX_API}/topics", {"search": query, "per-page": 5, "api_key": config.get("api_key", "")})
            top = next((value for value in topics.get("results", []) if isinstance(value, dict) and value.get("id")), {})
            topic_id = str(top.get("id", ""))
        if not topic_id:
            return []
        payload = _request_json(
            f"{OPENALEX_API}/works",
            {"filter": f"topics.id:{topic_id},from_publication_date:{start.isoformat()}", "per-page": 100, "api_key": config.get("api_key", "")},
        )
        return [_openalex_record(value) for value in payload.get("results", []) if isinstance(value, dict)]
    if source == "europe_pmc":
        mesh = str(seed.get("mesh", query)).strip()
        return _fetch_europe_pmc(f'MESH:"{mesh}"', start, today)
    return []


def _error_type(error: Exception) -> str:
    text = str(error).casefold()
    if text.startswith("authentication"):
        return "authentication"
    if text.startswith("rate_limit"):
        return "rate_limit"
    if text.startswith("network"):
        return "network"
    if text.startswith("data"):
        return "data"
    return "service"


def _date_range(source_health: dict[str, Any], source: str, now: datetime, *, weekly_backfill: bool) -> tuple[date, date]:
    today = now.date()
    if weekly_backfill:
        return today - timedelta(days=89), today
    health = normalize_health(source_health.get(source, {}))
    watermark = str(health.get("successful_watermark", ""))[:10]
    try:
        last = date.fromisoformat(watermark)
    except ValueError:
        return today - timedelta(days=29), today
    return min(last, today) - timedelta(days=13), today


def _sources_for_strategy(strategy: str) -> tuple[str, ...]:
    return {
        "keyword": ("openalex", "crossref", "semantic_scholar", "europe_pmc", "doaj", "arxiv"),
        "semantic_related": ("openalex", "semantic_scholar"),
        "citation_network": ("openalex", "semantic_scholar"),
        "confirmed_author_team": ("openalex", "semantic_scholar", "europe_pmc"),
        "watched_journal": ("publisher_toc", "crossref", "openalex"),
        "topic_expansion": ("openalex", "europe_pmc"),
    }[strategy]


def _normalize_work(
    raw: dict[str, Any],
    *,
    source: str,
    strategy: str,
    seed: Any,
    query: str,
    fetched_at: str,
) -> dict[str, Any] | None:
    title = _clean(raw.get("title", raw.get("display_name", "")))
    if not title:
        return None
    doi = normalized_doi(raw.get("doi", raw.get("DOI", "")))
    source_id = str(raw.get("source_id", raw.get("id", ""))).strip()
    url = str(raw.get("url", raw.get("URL", ""))).strip() or (f"https://doi.org/{doi}" if doi else "")
    item_id = doi or source_id or hashlib.sha256((source + "|" + title.casefold()).encode("utf-8")).hexdigest()[:28]
    seed_id = ""
    if isinstance(seed, dict):
        seed_id = str(seed.get("id", seed.get("doi", seed.get("name", ""))))
    else:
        seed_id = str(seed or "")
    evidence = {
        "source": source,
        "source_id": source_id,
        "recall_strategy": strategy,
        "seed": deepcopy(seed),
        "query": query,
        "fetched_at": fetched_at,
        "url": url,
    }
    journal = str(raw.get("journal", raw.get("venue", ""))).strip()
    is_preprint = bool(raw.get("is_preprint")) or source == "arxiv" or any(value in journal.casefold() for value in ("arxiv", "biorxiv", "medrxiv", "research square", "egusphere"))
    return {
        "id": item_id,
        "source": source,
        "source_id": source_id,
        "doi": doi,
        "title": title,
        "abstract": _clean(raw.get("abstract", raw.get("abstractText", ""))),
        "authors": [str(value).strip() for value in raw.get("authors", []) if str(value).strip()] if isinstance(raw.get("authors"), list) else [],
        "author_keywords": [str(value).strip() for value in raw.get("author_keywords", []) if str(value).strip()] if isinstance(raw.get("author_keywords"), list) else [],
        "published_date": _date(raw.get("published_date", raw.get("publication_date", ""))),
        "online_published_date": _date(raw.get("online_published_date", "")),
        "source_updated_date": _date(raw.get("source_updated_date", "")),
        "journal": journal,
        "issn": normalize_issns(raw),
        "publisher": str(raw.get("publisher", "")),
        "url": url,
        "is_preprint": is_preprint,
        "official_toc": bool(raw.get("official_toc")),
        "watched_journal": strategy == "watched_journal",
        "fetched_at": fetched_at,
        "first_seen_at": fetched_at,
        "source_names": [source],
        "source_evidence": [evidence],
        "recall_strategy": strategy,
        "recall_strategies": [strategy],
        "seed_id": seed_id,
        "query": query,
        "candidate_state": "raw",
        "status": "new",
        "ai_adjustment": None,
    }


def discover_frontier_candidates_v13(
    profile: dict[str, Any],
    journals: list[dict[str, Any]],
    *,
    cache: EvidenceCache,
    source_settings: dict[str, Any],
    source_health: dict[str, Any] | None = None,
    now: datetime | None = None,
    weekly_backfill: bool | None = None,
    source_filter: list[str] | None = None,
    progress: Any = None,
    cancelled: Any = None,
    fetcher: Callable[[str, str, dict[str, Any], str, date, date, dict[str, Any]], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Execute every seeded lane and report failures independently."""

    now = now or datetime.now()
    weekly_backfill = now.weekday() == 0 if weekly_backfill is None else bool(weekly_backfill)
    health = deepcopy(source_health if isinstance(source_health, dict) else {})
    plan = plan_recall(profile, journals)
    all_rows: list[dict[str, Any]] = []
    outcomes: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, str]] = []
    source_results: dict[str, dict[str, Any]] = {}
    jobs: list[tuple[str, dict[str, Any], str]] = []
    for strategy in RECALL_STRATEGIES:
        seeds = plan.get(strategy, [])
        if not seeds:
            outcomes[strategy] = recall_outcome(has_seed=False, succeeded=True)
            continue
        for seed_job in seeds:
            for source in _sources_for_strategy(strategy):
                jobs.append((strategy, seed_job, source))
    total = max(1, len(jobs))

    def emit(message: str, value: int) -> None:
        if progress is None:
            return
        try:
            progress(message, value)
        except TypeError:
            progress(message)

    def default_fetch(strategy: str, source: str, seed: dict[str, Any], query: str, start: date, today: date, config: dict[str, Any]) -> list[dict[str, Any]]:
        payload = seed.get("seed", seed)
        payload = payload if isinstance(payload, dict) else {"name": str(payload)}
        if strategy == "keyword":
            return _fetch_keyword(source, query, start, today, config)
        if strategy == "semantic_related":
            return _fetch_semantic_related(source, payload, start, today, config)
        if strategy == "citation_network":
            return _fetch_citations(source, payload, start, today, config)
        if strategy == "confirmed_author_team":
            return _fetch_author(source, payload, start, today, config)
        if strategy == "watched_journal":
            return _fetch_journal(source, payload, start, today, config)
        if strategy == "topic_expansion":
            return _fetch_topic(source, payload, query, start, today, config)
        return []

    adapter = fetcher or default_fetch
    fetched_at = now.isoformat(timespec="seconds")
    failure_streaks: dict[str, int] = {}
    blocked_sources: set[str] = set()
    attempted_strategies: set[str] = set()
    for index, (strategy, seed_job, source) in enumerate(jobs, start=1):
        if cancelled and cancelled():
            break
        if source_filter is not None and source not in source_filter:
            continue
        if source in blocked_sources:
            continue
        if source != "publisher_toc":
            if source not in SOURCE_REGISTRY:
                continue
            config = runtime_source_config(source_settings, source)
            if not config["enabled"]:
                continue
            if not source_due(health.get(source, {}), at=now):
                if source_filter is not None and source in source_filter:
                    message = "来源仍在退避期，已保留为待补跑"
                    previous = source_results.setdefault(source, {"succeeded": True, "count": 0, "errors": []})
                    previous["succeeded"] = False
                    previous["deferred"] = True
                    if message not in previous["errors"]:
                        previous["errors"].append(message)
                        errors.append({"source": source, "strategy": strategy, "type": "backoff", "error": message, "query": ""})
                    blocked_sources.add(source)
                continue
        else:
            config = {"id": source, "name": "出版社 TOC/RSS", "enabled": True, "api_key": "", "contact_email": ""}
            payload = seed_job.get("seed", {})
            if not isinstance(payload, dict) or not payload.get("feed_url"):
                continue
        query = str(seed_job.get("query", "")).strip()
        attempted_strategies.add(strategy)
        start, today_value = _date_range(health, source, now, weekly_backfill=weekly_backfill)
        emit(f"{strategy} · 正在检查 {config['name']}…", int(index * 88 / total))
        cache_key = hashlib.sha256(
            json.dumps({"v": 13, "strategy": strategy, "source": source, "seed": seed_job, "start": start.isoformat(), "end": today_value.isoformat()}, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:40]
        cached = cache.get_source_response(source, cache_key, now=fetched_at)
        raw_rows: list[dict[str, Any]]
        if cached is not None:
            raw_rows = [dict(value) for value in cached.get("results", []) if isinstance(value, dict)]
            succeeded = True
            failure_streaks[source] = 0
        else:
            try:
                raw_rows = adapter(strategy, source, seed_job, query, start, today_value, config)
                raw_rows = [dict(value) for value in raw_rows if isinstance(value, dict)]
                succeeded = True
                failure_streaks[source] = 0
                cache.put_source_response(
                    source,
                    cache_key,
                    {"results": raw_rows},
                    fetched_at,
                    (now + timedelta(hours=24)).isoformat(timespec="seconds"),
                )
            except Exception as error:  # noqa: BLE001 - one source/lane must not mask the rest
                succeeded = False
                raw_rows = []
                kind = _error_type(error)
                errors.append({"source": source, "strategy": strategy, "type": kind, "error": str(error)[:240], "query": query})
                previous = source_results.setdefault(source, {"succeeded": True, "count": 0, "errors": []})
                previous["succeeded"] = False
                previous["errors"].append(str(error)[:240])
                failure_streaks[source] = failure_streaks.get(source, 0) + 1
                threshold = 1 if kind in {"authentication", "rate_limit"} else 2
                if kind in {"authentication", "rate_limit", "network", "service"} and failure_streaks[source] >= threshold:
                    blocked_sources.add(source)
                    emit(f"{config['name']} 连续失败，本次已暂停其余检索词，等待下次按来源补跑。", int(index * 88 / total))
        source_entry = source_results.setdefault(source, {"succeeded": True, "count": 0, "errors": []})
        source_entry["succeeded"] = bool(source_entry["succeeded"] and succeeded)
        source_entry["count"] += len(raw_rows)
        for raw in raw_rows:
            item = _normalize_work(raw, source=source, strategy=strategy, seed=seed_job.get("seed", seed_job.get("query", "")), query=query, fetched_at=fetched_at)
            if item is not None:
                all_rows.append(item)

    for source, result in source_results.items():
        if result.get("deferred"):
            continue
        health[source] = record_source_outcome(
            health.get(source, {}),
            at=now,
            success=bool(result["succeeded"]),
            result_count=int(result["count"]),
            error_type=_error_type(RuntimeError(result["errors"][0])) if result["errors"] else "",
            error=result["errors"][0] if result["errors"] else "",
            successful_watermark=now.date().isoformat() if result["succeeded"] else "",
        )
    for strategy in RECALL_STRATEGIES:
        if strategy in outcomes:
            continue
        strategy_jobs = [job for job in jobs if job[0] == strategy and (source_filter is None or job[2] in source_filter)]
        attempted_sources = {job[2] for job in strategy_jobs if job[2] in source_results}
        successes = [source for source in attempted_sources if source_results[source]["succeeded"]]
        count = sum(1 for value in all_rows if value.get("recall_strategy") == strategy)
        outcomes[strategy] = recall_outcome(
            has_seed=bool(plan.get(strategy)),
            succeeded=bool(successes),
            count=count,
            error="; ".join(value["error"] for value in errors if value["strategy"] == strategy)[:240],
        )
    items = merge_work_families(all_rows)
    cache.upsert_works(items)
    emit(f"六路发现完成：{len(items)} 个去重成果", 100)
    return {
        "items": items,
        "recall_outcomes": outcomes,
        "source_health": health,
        "errors": errors,
        "attempted_sources": sorted(source_results),
        "successful_sources": sorted(
            source for source, value in source_results.items() if value.get("succeeded")
        ),
        "failed_sources": sorted(
            source for source, value in source_results.items() if not value.get("succeeded")
        ),
        "attempted_strategies": sorted(attempted_strategies),
        "weekly_backfill": weekly_backfill,
        "raw_count": len(all_rows),
    }
