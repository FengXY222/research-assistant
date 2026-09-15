"""No-key daily literature discovery built on Crossref's public metadata API."""

from __future__ import annotations

import html
import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from utils.evidence_cache import EvidenceCache
from utils.frontier_scoring import (
    apply_frontier_ranking,
    canonical_text,
    deduplicate_frontier_items,
    evaluate_frontier_candidate,
    frontier_profile_signature_v11,
    profile_query_terms_v11,
    select_daily_mix,
    select_daily_recommendations_v11,
)
from utils.file_manager import RESEARCH_INTELLIGENCE_CACHE_FILE
from utils.easyscholar_service import is_easyscholar_ready
from utils.journal_quality import journal_quality_snapshot
from utils.research_signal_service import build_profile_view
from utils.frontier_admission import plan_frontier_queries
from utils.frontier_review_service import review_frontier_content


CROSSREF_API = "https://api.crossref.org"
OPENALEX_API = "https://api.openalex.org"
DOAJ_API = "https://doaj.org/api"
SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1"
ARXIV_API = "https://export.arxiv.org/api/query"
USER_AGENT = "ResearchAssistant/0.7 (personal desktop research tool)"
# Persisting this signature lets the UI distinguish an old cached digest from
# results evaluated under the current keyword/priority logic.
FRONTIER_ALGORITHM_VERSION = 13
# Journal priority is deliberately a small bonus. Relevance to the research
# profile is now a hard admission requirement rather than a side effect of a
# favourite journal.
NON_JOURNAL_VENUES = {
    "zenodo",
    "figshare",
    "dryad",
    "dataverse",
    "mendeley data",
    "osf",
    "open science framework",
    "github",
}
SOURCE_LABELS = {
    "crossref": "Crossref",
    "openalex": "OpenAlex",
    "doaj": "DOAJ",
    "semantic_scholar": "Semantic Scholar",
    "arxiv": "arXiv",
}


_RESET_FRONTIER_STATUSES = {"saved", "read", "deprioritized", "dismissed"}


def reset_frontier_history(store: dict[str, Any]) -> dict[str, Any]:
    """Remove the four user-requested reading buckets without touching likes/new items."""
    result = deepcopy(store if isinstance(store, dict) else {})
    items = result.get("items", []) if isinstance(result.get("items"), list) else []
    removed_ids = {
        str(item.get("id", "")).strip()
        for item in items
        if isinstance(item, dict) and str(item.get("status", "")).strip().casefold() in _RESET_FRONTIER_STATUSES
    }
    result["items"] = [
        deepcopy(item)
        for item in items
        if isinstance(item, dict) and str(item.get("id", "")).strip() not in removed_ids
    ]
    for field in ("feedback_events", "signals", "history"):
        values = result.get(field)
        if not isinstance(values, list):
            continue
        result[field] = [
            deepcopy(value)
            for value in values
            if not isinstance(value, dict)
            or str(value.get("item_id", value.get("frontier_id", value.get("paper_id", "")))).strip() not in removed_ids
        ]
    return result

TERM_ZH = {
    "soil organic carbon": "土壤有机碳",
    "soil carbon": "土壤碳",
    "carbon sequestration": "碳固存",
    "remote sensing": "遥感",
    "machine learning": "机器学习",
    "deep learning": "深度学习",
    "digital soil mapping": "数字土壤制图",
    "soil mapping": "土壤制图",
    "land use": "土地利用",
    "land cover": "土地覆盖",
    "climate change": "气候变化",
    "maoc": "矿物结合态有机碳（MAOC）",
    "poc": "颗粒有机碳（POC）",
    "soc": "土壤有机碳（SOC）",
    "random forest": "随机森林",
    "xgboost": "XGBoost",
    "transformer": "Transformer",
    "geospatial": "地理空间",
    "spatial": "空间分析",
    "soil organic matter": "土壤有机质",
    "mineral-associated organic carbon": "矿物结合态有机碳（MAOC）",
    "particulate organic carbon": "颗粒有机碳（POC）",
    "soil aggregate": "土壤团聚体",
    "carbon stabilization": "碳稳定化",
    "land use change": "土地利用变化",
    "land-use change": "土地利用变化",
    "cropland conversion": "耕地转换",
    "land transfer": "土地流转",
    "spatial prediction": "空间预测",
}
METHOD_TERMS = {
    "machine learning": "采用机器学习方法",
    "deep learning": "采用深度学习方法",
    "random forest": "采用随机森林方法",
    "xgboost": "采用 XGBoost 方法",
    "remote sensing": "结合遥感数据",
    "mapping": "开展空间制图分析",
    "prediction": "开展预测建模",
    "model": "开展模型分析",
}
AUTO_DIRECTION_TERMS = {
    "soil organic carbon": ("soil organic carbon", "土壤有机碳", "soc"),
    "remote sensing": ("remote sensing", "遥感"),
    "machine learning": ("machine learning", "机器学习", "随机森林", "random forest", "xgboost"),
    "digital soil mapping": ("digital soil mapping", "soil mapping", "土壤制图"),
    "land use": ("land use", "土地利用", "土地流转"),
    "carbon sequestration": ("carbon sequestration", "碳固存"),
    "MAOC": ("maoc", "矿物结合态有机碳"),
    "POC": ("poc", "颗粒有机碳"),
    "climate change": ("climate change", "气候变化"),
    "deep learning": ("deep learning", "深度学习"),
}


class FrontierNetworkError(RuntimeError):
    pass


def _request_json(path: str, params: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    query = urlencode({key: value for key, value in params.items() if value not in {None, ""}})
    url = f"{CROSSREF_API}{path}?{query}" if query else f"{CROSSREF_API}{path}"
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    request_headers.update(headers or {})
    request = Request(url, headers=request_headers)
    try:
        with urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
        raise FrontierNetworkError(str(error)) from error


def _request_external_json(url: str, params: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    """Fetch JSON from an enabled public source without changing app state."""
    query = urlencode({key: value for key, value in params.items() if value not in {None, ""}})
    address = f"{url}?{query}" if query else url
    request_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    request_headers.update(headers or {})
    request = Request(address, headers=request_headers)
    try:
        with urlopen(request, timeout=14) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FrontierNetworkError(str(error)) from error
    if not isinstance(payload, dict):
        raise FrontierNetworkError("返回的元数据格式异常")
    return payload


def _request_external_text(url: str, params: dict[str, Any], accept: str = "application/atom+xml") -> bytes:
    query = urlencode({key: value for key, value in params.items() if value not in {None, ""}})
    address = f"{url}?{query}" if query else url
    request = Request(address, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with urlopen(request, timeout=14) as response:
            return response.read()
    except (HTTPError, URLError, TimeoutError) as error:
        raise FrontierNetworkError(str(error)) from error


def _normalized(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def _strip_markup(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _date_from_parts(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    parts = value.get("date-parts", [])
    if not isinstance(parts, list) or not parts or not isinstance(parts[0], list) or not parts[0]:
        return ""
    values = list(parts[0]) + [1, 1]
    try:
        return date(int(values[0]), int(values[1]), int(values[2])).isoformat()
    except (TypeError, ValueError):
        return ""


def _published_date(work: dict[str, Any]) -> str:
    for key in ("published-online", "published-print", "published", "issued", "created"):
        value = _date_from_parts(work.get(key))
        if value:
            return value
    return ""


def _authors(work: dict[str, Any]) -> list[str]:
    result: list[str] = []
    for author in work.get("author", []) if isinstance(work.get("author"), list) else []:
        if not isinstance(author, dict):
            continue
        name = " ".join(part for part in (str(author.get("given", "")).strip(), str(author.get("family", "")).strip()) if part)
        if name:
            result.append(name)
    return result[:4]


def _plain_list(value: Any, limit: int = 20) -> list[str]:
    values = value if isinstance(value, list) else []
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        item = str(raw).strip()
        key = item.casefold()
        if item and key not in seen:
            result.append(item)
            seen.add(key)
    return result[:limit]


def _date_from_text(value: Any) -> str:
    match = re.search(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", str(value or ""))
    if not match:
        return ""
    year, month, day = match.groups()
    try:
        return date(int(year), int(month or 1), int(day or 1)).isoformat()
    except ValueError:
        return f"{year}-01-01"


def _is_in_range(value: str, start: date, today: date) -> bool:
    if re.fullmatch(r"\d{4}-01-01", value) and value.startswith(str(today.year)):
        # Some DOAJ records only expose a publication year.
        return True
    try:
        current = date.fromisoformat(value)
    except ValueError:
        return False
    return start <= current <= today


def _contains_term(text: str, term: str) -> bool:
    term = term.strip().casefold()
    if not term:
        return False
    if re.fullmatch(r"[a-z0-9]{2,8}", term):
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text.casefold()))
    return term in text.casefold()


def _translated_term(term: str) -> str:
    return TERM_ZH.get(term.casefold(), term)


def _display_terms(terms: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for term in terms:
        translated = _translated_term(term)
        key = translated.casefold()
        if translated and key not in seen:
            result.append(translated)
            seen.add(key)
    return result


def suggest_profile_keywords(
    papers: list[dict[str, Any]],
    inspirations: list[dict[str, Any]],
    readings: list[dict[str, Any]],
    journals: list[dict[str, Any]],
    existing: list[str] | None = None,
) -> list[str]:
    """Infer a compact English search profile from the user's local records."""
    parts: list[str] = []
    for paper in papers:
        parts.append(str(paper.get("title", "")))
        parts.extend(str(value) for value in paper.get("keywords", []) if str(value).strip())
        for journal in paper.get("journals", []):
            parts.append(str(journal.get("notes", "")))
    parts.extend(str(item.get("text", "")) for item in inspirations)
    for item in readings:
        parts.append(str(item.get("title", "")))
        parts.append(str(item.get("reason", "")))
    for journal in journals:
        parts.extend(str(value) for value in journal.get("fields", []) if str(value).strip())
        parts.append(str(journal.get("notes", "")))
    text = "\n".join(parts).casefold()
    ranked: list[tuple[int, str]] = []
    for canonical, aliases in AUTO_DIRECTION_TERMS.items():
        score = sum(text.count(alias.casefold()) for alias in aliases)
        if score:
            ranked.append((score, canonical))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    result: list[str] = []
    for term in existing or []:
        if str(term).strip() and str(term).casefold() not in {value.casefold() for value in result}:
            result.append(str(term).strip())
    for _score, term in ranked:
        if term.casefold() not in {value.casefold() for value in result}:
            result.append(term)
    return result[:10]


def _summary_cn(title: str, journal: str, matches: list[str], priority: str, level: str) -> str:
    topics = "、".join(_translated_term(term) for term in matches[:3]) or "你的研究方向"
    text = title.casefold()
    method = next((sentence for term, sentence in METHOD_TERMS.items() if _contains_term(text, term)), "聚焦相关研究问题")
    if priority == "必看":
        source = "来自你的必看期刊，"
    elif priority == "关注":
        source = "来自你的关注期刊，"
    elif journal:
        source = f"发表于 {journal}，"
    else:
        source = ""
    relevance = "严格相关" if level == "strict" else "探索相关"
    return f"{source}该研究聚焦{topics}，{method}；属于{relevance}，建议关注其方法与结论。"


def _fetch_works(params: dict[str, Any], path: str = "/works") -> list[dict[str, Any]]:
    payload = _request_json(path, params)
    message = payload.get("message", {}) if isinstance(payload, dict) else {}
    items = message.get("items", []) if isinstance(message, dict) else []
    return [item for item in items if isinstance(item, dict)]


def _journal_issn(name: str, source_cache: dict[str, str]) -> str:
    key = _normalized(name)
    if key in source_cache:
        return source_cache[key]
    payload = _request_json("/journals", {"query": name, "rows": 5})
    items = payload.get("message", {}).get("items", []) if isinstance(payload, dict) else []
    if not isinstance(items, list):
        return ""
    best_issn = ""
    best_score = -1
    for item in items:
        if not isinstance(item, dict):
            continue
        candidate = str(item.get("title", ""))
        candidate_key = _normalized(candidate)
        score = 0
        if candidate_key == key:
            score = 100
        elif key and (key in candidate_key or candidate_key in key):
            score = 60
        else:
            score = sum(1 for word in name.casefold().split() if word and word in candidate.casefold())
        issns = item.get("ISSN", [])
        if score > best_score and isinstance(issns, list) and issns:
            best_score = score
            best_issn = str(issns[0])
    if best_score >= 55 and best_issn:
        source_cache[key] = best_issn
        return best_issn
    return ""


# v11 source adapters below deliberately use the weighted scorer imported at
# module level.  Earlier pair-based front-end ranking implementations were
# removed so they cannot shadow the public refresh path.

def _record_to_item(record: dict[str, Any], profile: dict[str, Any], journals: list[dict[str, Any]]) -> dict[str, Any] | None:
    title = str(record.get("title", "")).strip()
    if not title:
        return None
    abstract = _strip_markup(record.get("abstract", ""))
    source_keywords = _plain_list(record.get("author_keywords"), 20)
    journal = str(record.get("journal", "")).strip()
    venue_key = journal.casefold()
    if any(blocked in venue_key for blocked in NON_JOURNAL_VENUES):
        return None
    journal_key = _normalized(journal)
    library_journal = next(
        (
            item
            for item in journals
            if isinstance(item, dict) and _normalized(str(item.get("name", ""))) == journal_key
        ),
        {},
    )
    candidate = {
        "title": title,
        "abstract": abstract,
        "journal": journal,
        "author_keywords": source_keywords,
    }
    match = evaluate_frontier_candidate(
        candidate,
        profile,
        library_journal,
        profile.get("journal_feedback", {}) if isinstance(profile.get("journal_feedback", {}), dict) else {},
    )
    if match is None:
        return None
    doi = str(record.get("doi", "")).strip().lower()
    doi = doi.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
    published_date = str(record.get("published_date", "")).strip()
    item_id = doi or _normalized(f"{title}|{journal}|{published_date[:4]}")
    if not item_id:
        return None
    source = str(record.get("source_name", "")).strip() or "公开数据库"
    topics = "、".join(match["matched_terms"][:3]) or "你的研究方向"
    method = next(
        (sentence for term, sentence in METHOD_TERMS.items() if _contains_term(title.casefold(), term)),
        "聚焦相关研究问题",
    )
    summary = f"聚焦{topics}，{method}。{match['recommendation_reason']}。"
    return {
        "id": item_id,
        "title": title,
        "journal": journal,
        "published_date": published_date,
        "url": str(record.get("url", "")).strip() or (f"https://doi.org/{doi}" if doi else ""),
        "doi": doi,
        "abstract": abstract,
        "authors": _plain_list(record.get("authors"), 4),
        "author_keywords": source_keywords,
        "keyword_source": str(record.get("keyword_source", "")).strip(),
        "publisher": str(record.get("publisher", library_journal.get("publisher", ""))).strip(),
        "match_mode": match["match_source"],
        "match_source": match["match_source"],
        "source_names": [source],
        "match_terms": match["matched_terms"],
        "matched_terms": match["matched_terms"],
        "matched_term_ids": match["matched_term_ids"],
        "priority": match["priority"],
        "score": int(match["score"]),
        "score_breakdown": match["score_breakdown"],
        "jcr_state": match["jcr_state"],
        "jcr_quartile": match["jcr_quartile"],
        "cas_upgrade": match.get("cas_upgrade", ""),
        "journal_metric_line": match.get("journal_metric_line", ""),
        "quality_gate_state": match.get("quality_gate_state", "eligible"),
        "quality_gate_reason": match.get("quality_gate_reason", "quality_unresolved"),
        "journal_quality_flag": match["journal_quality_flag"],
        "library_journal_id": str(library_journal.get("id", "")),
        "summary_cn": summary,
        "relevance_level": "weighted_total",
        "matched_axis_ids": [],
        "matched_axes": [],
        "recommendation_reason": match["recommendation_reason"],
        "feedback": "",
        "status": "new",
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "recommendation_date": "",
        "first_seen_date": "",
    }


def _crossref_record(work: dict[str, Any]) -> dict[str, Any]:
    titles = work.get("title", [])
    journals = work.get("container-title", [])
    return {
        "title": str(titles[0]).strip() if isinstance(titles, list) and titles else "",
        "journal": str(journals[0]).strip() if isinstance(journals, list) and journals else "",
        "published_date": _published_date(work),
        "url": str(work.get("URL", "")),
        "doi": str(work.get("DOI", "")),
        "abstract": _strip_markup(work.get("abstract", "")),
        "authors": _authors(work),
        # Crossref subjects are publisher classifications, not author words.
        "author_keywords": [],
        "keyword_source": "",
        "source_name": SOURCE_LABELS["crossref"],
    }


def _openalex_abstract(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    indexed: dict[int, str] = {}
    for word, positions in value.items():
        for position in positions if isinstance(positions, list) else []:
            if isinstance(position, int):
                indexed[position] = str(word)
    return " ".join(indexed[position] for position in sorted(indexed))


def _fetch_openalex(query: str, start: date, today: date, key: str) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "search": query,
        "filter": f"from_publication_date:{start.isoformat()},to_publication_date:{today.isoformat()}",
        "sort": "publication_date:desc",
        "per-page": 18,
    }
    if key:
        params["api_key"] = key
    payload = _request_external_json(f"{OPENALEX_API}/works", params)
    records: list[dict[str, Any]] = []
    for work in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
        if not isinstance(work, dict):
            continue
        location = work.get("primary_location", {})
        location = location if isinstance(location, dict) else {}
        venue = location.get("source", {})
        venue = venue if isinstance(venue, dict) else {}
        authors: list[str] = []
        for authorship in work.get("authorships", []) if isinstance(work.get("authorships"), list) else []:
            author = authorship.get("author", {}) if isinstance(authorship, dict) else {}
            name = str(author.get("display_name", "")).strip() if isinstance(author, dict) else ""
            if name:
                authors.append(name)
        records.append(
            {
                "title": str(work.get("display_name", "")),
                "journal": str(venue.get("display_name", "")),
                "published_date": _date_from_text(work.get("publication_date", "")),
                "url": str(location.get("landing_page_url", "")) or str(work.get("doi", "")),
                "doi": str(work.get("doi", "")),
                "abstract": _openalex_abstract(work.get("abstract_inverted_index")),
                "authors": authors,
                # OpenAlex terms are automated and cannot impersonate author keywords.
                "author_keywords": [],
                "keyword_source": "",
                "source_name": SOURCE_LABELS["openalex"],
            }
        )
    return records


def _fetch_doaj(query: str, start: date, today: date) -> list[dict[str, Any]]:
    payload = _request_external_json(
        f"{DOAJ_API}/search/articles/{quote(query)}", {"sort": "created_date:desc", "pageSize": 24}
    )
    records: list[dict[str, Any]] = []
    for raw in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
        bib = raw.get("bibjson", {}) if isinstance(raw, dict) else {}
        if not isinstance(bib, dict):
            continue
        published_date = _date_from_text(f"{bib.get('year', '')}-{bib.get('month', '')}")
        if not _is_in_range(published_date, start, today):
            continue
        journal = bib.get("journal", {})
        journal = journal if isinstance(journal, dict) else {}
        doi = ""
        for identifier in bib.get("identifier", []) if isinstance(bib.get("identifier"), list) else []:
            if isinstance(identifier, dict) and str(identifier.get("type", "")).casefold() == "doi":
                doi = str(identifier.get("id", ""))
                break
        url = ""
        for link in bib.get("link", []) if isinstance(bib.get("link"), list) else []:
            if isinstance(link, dict) and str(link.get("url", "")).strip():
                url = str(link["url"])
                break
        records.append(
            {
                "title": str(bib.get("title", "")),
                "journal": str(journal.get("title", "")),
                "published_date": published_date,
                "url": url,
                "doi": doi,
                "abstract": _strip_markup(bib.get("abstract", "")),
                "authors": [
                    str(author.get("name", "")).strip()
                    for author in bib.get("author", [])
                    if isinstance(author, dict) and str(author.get("name", "")).strip()
                ],
                "author_keywords": _plain_list(bib.get("keywords"), 20),
                "keyword_source": "DOAJ 论文关键词",
                "source_name": SOURCE_LABELS["doaj"],
            }
        )
    return records


def _fetch_semantic_scholar(query: str, start: date, today: date, key: str) -> list[dict[str, Any]]:
    headers = {"x-api-key": key} if key else {}
    payload = _request_external_json(
        f"{SEMANTIC_SCHOLAR_API}/paper/search",
        {
            "query": query,
            "limit": 18,
            "fields": "title,abstract,venue,publicationDate,externalIds,openAccessPdf,authors",
        },
        headers,
    )
    records: list[dict[str, Any]] = []
    for paper in payload.get("data", []) if isinstance(payload.get("data"), list) else []:
        if not isinstance(paper, dict):
            continue
        published_date = _date_from_text(paper.get("publicationDate", ""))
        if not _is_in_range(published_date, start, today):
            continue
        ids = paper.get("externalIds", {})
        ids = ids if isinstance(ids, dict) else {}
        open_access = paper.get("openAccessPdf", {})
        open_access = open_access if isinstance(open_access, dict) else {}
        records.append(
            {
                "title": str(paper.get("title", "")),
                "journal": str(paper.get("venue", "")),
                "published_date": published_date,
                "url": str(open_access.get("url", "")),
                "doi": str(ids.get("DOI", "")),
                "abstract": str(paper.get("abstract", "")),
                "authors": [
                    str(author.get("name", "")).strip()
                    for author in paper.get("authors", [])
                    if isinstance(author, dict) and str(author.get("name", "")).strip()
                ],
                "author_keywords": [],
                "keyword_source": "",
                "source_name": SOURCE_LABELS["semantic_scholar"],
            }
        )
    return records


def _fetch_arxiv(query: str, start: date, today: date) -> list[dict[str, Any]]:
    raw = _request_external_text(
        ARXIV_API,
        {
            "search_query": f'all:"{query}"',
            "start": 0,
            "max_results": 18,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        },
    )
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise FrontierNetworkError("arXiv 返回格式异常") from error
    atom = "{http://www.w3.org/2005/Atom}"
    records: list[dict[str, Any]] = []
    for entry in root.findall(f"{atom}entry"):
        published_date = _date_from_text(entry.findtext(f"{atom}published", ""))
        if not _is_in_range(published_date, start, today):
            continue
        records.append(
            {
                "title": _strip_markup(entry.findtext(f"{atom}title", "")),
                "journal": "arXiv 预印本",
                "published_date": published_date,
                "url": str(entry.findtext(f"{atom}id", "")),
                "doi": "",
                "abstract": _strip_markup(entry.findtext(f"{atom}summary", "")),
                "authors": [
                    _strip_markup(author.findtext(f"{atom}name", ""))
                    for author in entry.findall(f"{atom}author")
                    if _strip_markup(author.findtext(f"{atom}name", ""))
                ],
                "author_keywords": [],
                "keyword_source": "",
                "source_name": SOURCE_LABELS["arxiv"],
            }
        )
    return records


def _merge_candidates(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    sources = _plain_list([*existing.get("source_names", []), *incoming.get("source_names", [])], 6)
    incoming_better = incoming.get("match_mode") == "source_keywords" and existing.get("match_mode") != "source_keywords"
    if incoming.get("match_mode") == existing.get("match_mode"):
        incoming_better = int(incoming.get("score", 0)) > int(existing.get("score", 0))
    result = dict(incoming if incoming_better else existing)
    result["source_names"] = sources
    if not result.get("author_keywords"):
        result["author_keywords"] = _plain_list([*existing.get("author_keywords", []), *incoming.get("author_keywords", [])], 20)
    return result


def merge_frontier_refresh_item(incoming: dict[str, Any], previous: dict[str, Any] | None, today: str) -> dict[str, Any]:
    """Carry user decisions across a fresh metadata refresh without reusing old tiers.

    The incoming record already contains the v11 local base score.  Only the
    bounded feedback/AI adjustments and durable user actions are carried over,
    then the named score equation is recomputed from scratch.
    """
    result = dict(incoming if isinstance(incoming, dict) else {})
    previous = previous if isinstance(previous, dict) else {}
    if not previous:
        result["recommendation_date"] = str(result.get("recommendation_date", "")).strip() or today
        result["first_seen_date"] = str(result.get("first_seen_date", "")).strip() or result["recommendation_date"]
        return result
    if result.get("admission_version"):
        for key in ("status", "feedback", "feedback_events", "one_line_feedback", "library_journal_id",
                    "dismissed_from_status", "dismissed_from_feedback", "deprioritized_from_status",
                    "deprioritized_from_feedback", "first_seen_date", "recommendation_date"):
            if key in previous:
                result[key] = deepcopy(previous[key])
        return result
    for key in (
        "status",
        "feedback",
        "feedback_events",
        "one_line_feedback",
        "journal_quality_flag",
        "filtered_by_quality",
        "library_journal_id",
        "dismissed_from_status",
        "dismissed_from_feedback",
        "deprioritized_from_status",
        "deprioritized_from_feedback",
        "ai_summary_cn",
        "ai_reason_cn",
        "ai_model",
        "ai_updated_at",
    ):
        if key in previous:
            result[key] = previous[key]
    try:
        feedback = max(-30, min(30, int(previous.get("feedback_adjustment", 0) or 0)))
    except (TypeError, ValueError):
        feedback = 0
    try:
        ai = max(-15, min(15, int(previous.get("ai_adjustment", 0) or 0)))
    except (TypeError, ValueError):
        ai = 0
    breakdown = result.get("score_breakdown", {})
    breakdown = dict(breakdown) if isinstance(breakdown, dict) else {}
    breakdown["feedback"] = feedback
    breakdown["ai"] = ai
    for key in ("terms", "priority", "quality"):
        try:
            breakdown[key] = int(breakdown.get(key, 0) or 0)
        except (TypeError, ValueError):
            breakdown[key] = 0
    result["score_breakdown"] = breakdown
    result["feedback_adjustment"] = feedback
    result["ai_adjustment"] = ai
    result["score"] = sum(int(breakdown.get(key, 0) or 0) for key in ("terms", "priority", "quality", "feedback", "ai"))
    result["recommendation_date"] = str(previous.get("recommendation_date", "")).strip() or today
    result["first_seen_date"] = str(previous.get("first_seen_date", "")).strip() or result["recommendation_date"]
    return result


def frontier_profile_signature(profile: dict[str, Any], journals: list[dict[str, Any]]) -> str:
    """Return the v11 weighted-profile fingerprint used for cache invalidation."""
    return frontier_profile_signature_v11(profile, journals)


def frontier_cache_is_stale(data: dict[str, Any], journals: list[dict[str, Any]]) -> bool:
    if int(data.get("algorithm_version", 0) or 0) != FRONTIER_ALGORITHM_VERSION:
        return True
    return str(data.get("profile_signature", "")) != frontier_profile_signature(data.get("profile", {}), journals)


def select_daily_recommendations(items: list[dict[str, Any]], profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only first-seen recommendations, ordered by one v11 total score."""
    filtered = [
        item
        for item in items
        if isinstance(item, dict)
        and not any(blocked in str(item.get("journal", "")).casefold() for blocked in NON_JOURNAL_VENUES)
    ]
    effective_profile = dict(profile) if isinstance(profile, dict) else {}
    effective_profile["_easyscholar_configured"] = is_easyscholar_ready()
    return select_daily_recommendations_v11(filtered, effective_profile)


def update_daily_frontier(data: dict[str, Any], journals: list[dict[str, Any]]) -> dict[str, Any]:
    """Search the enabled public sources and merge a local, duplicate-free digest."""
    profile = data.get("profile", {})
    profile = dict(profile) if isinstance(profile, dict) else {}
    profile["_easyscholar_configured"] = is_easyscholar_ready()
    stored_profile = {key: value for key, value in profile.items() if key != "_easyscholar_configured"}
    today = date.today()
    today_key = today.isoformat()
    start = today - timedelta(days=max(int(profile.get("lookback_days", 7)) - 1, 0))
    sources = profile.get("sources", {})
    sources = sources if isinstance(sources, dict) else {}
    source_cache = dict(data.get("source_cache", {}))
    candidates: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    def configured(source_id: str) -> tuple[bool, str]:
        value = sources.get(source_id, {})
        value = value if isinstance(value, dict) else {}
        return bool(value.get("enabled", False)), str(value.get("api_key", "")).strip()

    def collect(records: list[dict[str, Any]]) -> None:
        for record in records:
            item = _record_to_item(record, profile, journals)
            if item is None:
                continue
            existing = candidates.get(item["id"])
            candidates[item["id"]] = _merge_candidates(existing, item) if existing else item

    fetchers = {
        "crossref": lambda term, key: [_crossref_record(work) for work in _fetch_works(
            {
                "filter": f"from-pub-date:{start.isoformat()},until-pub-date:{today.isoformat()},type:journal-article",
                "query": term,
                "sort": "published",
                "order": "desc",
                "rows": 18,
                "select": "DOI,title,container-title,published,published-online,published-print,issued,created,URL,abstract,author",
            }
        )],
        "openalex": lambda term, key: _fetch_openalex(term, start, today, key),
        "doaj": lambda term, key: _fetch_doaj(term, start, today),
        "semantic_scholar": lambda term, key: _fetch_semantic_scholar(term, start, today, key),
    }
    queries = _profile_query_terms(profile)
    enabled_crossref, _ = configured("crossref")
    parallel_jobs: list[tuple[str, Any]] = []
    crossref_jobs: list[tuple[str, Any]] = []
    for source_id, fetcher in fetchers.items():
        enabled, key = configured(source_id)
        if not enabled:
            continue
        for term in queries:
            job = (f"{SOURCE_LABELS[source_id]}“{term}”", lambda fetcher=fetcher, term=term, key=key: fetcher(term, key))
            (crossref_jobs if source_id == "crossref" else parallel_jobs).append(job)
    enabled_arxiv, _ = configured("arxiv")
    if enabled_arxiv and queries:
        parallel_jobs.append(("arXiv", lambda: _fetch_arxiv(queries[0], start, today)))

    # Subscription affects retrieval and ranking, never admission.
    if enabled_crossref:
        for journal in [entry for entry in journals if str(entry.get("frontier_priority", "不订阅")) != "不订阅"][:6]:
            name = str(journal.get("name", "")).strip()
            if not name:
                continue
            try:
                issn = str(journal.get("issn", "")).strip() or _journal_issn(name, source_cache)
                if issn:
                    common = {
                        "filter": f"from-pub-date:{start.isoformat()},until-pub-date:{today.isoformat()},type:journal-article",
                        "sort": "published",
                        "order": "desc",
                        "rows": 30,
                        "select": "DOI,title,container-title,published,published-online,published-print,issued,created,URL,abstract,author",
                    }
                    crossref_jobs.append(
                        (
                            f"期刊“{name}”",
                            lambda common=common, issn=issn: [
                                _crossref_record(work) for work in _fetch_works(common, f"/journals/{quote(issn)}/works")
                            ],
                        )
                    )
            except FrontierNetworkError as error:
                errors.append(f"期刊“{name}”：{error}")

    # The independent sources run in parallel. Crossref is deliberately
    # serialized afterwards to respect its polite public rate limit.
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(job): label for label, job in parallel_jobs}
        for future in as_completed(futures):
            label = futures[future]
            try:
                collect(future.result())
            except FrontierNetworkError as error:
                errors.append(f"{label}：{error}")
            except Exception as error:
                errors.append(f"{label}：{error}")
    for label, job in crossref_jobs:
        try:
            collect(job())
        except FrontierNetworkError as error:
            errors.append(f"{label}：{error}")
        except Exception as error:
            errors.append(f"{label}：{error}")
        time.sleep(0.15)

    old = {str(item.get("id", "")): item for item in data.get("items", []) if isinstance(item, dict)}
    merged: list[dict[str, Any]] = []
    new_count = 0
    for item in candidates.values():
        previous = old.get(item["id"])
        if previous is None:
            new_count += 1
        merged.append(merge_frontier_refresh_item(item, previous, today_key))
    candidate_ids = {item["id"] for item in merged}
    retained = [
        item
        for item in old.values()
        if item.get("id") not in candidate_ids and item.get("status") in {"saved", "liked", "dismissed", "read", "deprioritized"}
    ]
    all_items = deduplicate_frontier_items([*merged, *retained])
    all_items.sort(
        key=lambda item: (
            str(item.get("status", "")) == "dismissed",
            -int(item.get("score", 0) or 0),
            str(item.get("published_date", "")),
            str(item.get("id", "")),
        )
    )
    all_items = all_items[:500]
    top = select_daily_recommendations(all_items, profile)
    focus = "、".join(item.get("matched_terms", item.get("match_terms", [""]))[0] for item in top[:3] if item.get("matched_terms", item.get("match_terms", [])))
    keyword_matches = sum(1 for item in top if item.get("match_source", item.get("match_mode")) == "source_keywords")
    brief = f"今日新筛出 {len(top)} 篇论文"
    if keyword_matches:
        brief += f"；其中 {keyword_matches} 篇按来源关键词匹配"
    if focus:
        brief += f"；重点涉及 {focus}"
    brief += "。"
    return {
        "data": {
            **data,
            "profile": stored_profile,
            "items": all_items,
            "last_checked": today_key,
            "source_cache": source_cache,
            "algorithm_version": FRONTIER_ALGORITHM_VERSION,
            "profile_signature": frontier_profile_signature(profile, journals),
        },
        "new_count": new_count,
        "visible_count": len(top),
        "brief": brief,
        "errors": errors,
    }


def _profile_query_terms(profile: dict[str, Any]) -> list[str]:
    """Use v11 weighted profile terms for recall without weakening admission."""
    return profile_query_terms_v11(profile)


def _v12_doi(value: object) -> str:
    doi = str(value or "").strip().casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix) :]
            break
    return doi.strip()


def _v12_title_key(value: object) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", str(value or "").casefold(), flags=re.UNICODE).split())


def _v12_work_key(item: dict[str, Any]) -> str:
    doi = _v12_doi(item.get("doi"))
    if doi:
        return "doi:" + doi
    source = str(item.get("source", "")).strip().casefold()
    source_id = str(item.get("source_id", "")).strip().casefold()
    if source and source_id:
        return f"source:{source}:{source_id}"
    return "title:" + _v12_title_key(item.get("title", ""))


def dedupe_works(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge real-work evidence using DOI, source ID and title in that order."""
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for raw in items:
        if not isinstance(raw, dict) or not str(raw.get("title", "")).strip():
            continue
        item = dict(raw)
        item["doi"] = _v12_doi(item.get("doi"))
        key = _v12_work_key(item)
        if not key.removeprefix("title:"):
            continue
        if key not in merged:
            item["source_names"] = _plain_list(
                [*item.get("source_names", []), item.get("source", "")], 12
            )
            evidence = item.get("source_evidence", [])
            item["source_evidence"] = [dict(value) for value in evidence if isinstance(value, dict)]
            merged[key] = item
            order.append(key)
            continue
        current = merged[key]
        for field in (
            "journal",
            "abstract",
            "publisher",
            "url",
            "published_date",
            "issn",
            "authors",
        ):
            existing = current.get(field)
            incoming = item.get(field)
            if (not existing or (isinstance(existing, (list, str)) and len(existing) < len(incoming or []))) and incoming:
                current[field] = incoming
        current["source_names"] = _plain_list(
            [*current.get("source_names", []), *item.get("source_names", []), item.get("source", "")], 12
        )
        evidence = [
            *current.get("source_evidence", []),
            *item.get("source_evidence", []),
        ]
        distinct_evidence: list[dict[str, Any]] = []
        evidence_keys: set[str] = set()
        for value in evidence:
            if not isinstance(value, dict):
                continue
            evidence_key = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            if evidence_key not in evidence_keys:
                evidence_keys.add(evidence_key)
                distinct_evidence.append(dict(value))
        current["source_evidence"] = distinct_evidence
        if str(item.get("recommendation_kind", "")) == "core_keyword":
            current["recommendation_kind"] = "core_keyword"
    return [merged[key] for key in order]


def _fetch_v12_source(
    source: str,
    query: str,
    start: date,
    today: date,
    api_key: str,
) -> list[dict[str, Any]]:
    if source == "crossref":
        return [
            _crossref_record(work)
            for work in _fetch_works(
                {
                    "filter": f"from-pub-date:{start.isoformat()},until-pub-date:{today.isoformat()},type:journal-article",
                    "query": query,
                    "sort": "published",
                    "order": "desc",
                    "rows": 18,
                    "select": "DOI,title,container-title,published,published-online,published-print,issued,created,URL,abstract,author,ISSN,publisher",
                }
            )
        ]
    if source == "openalex":
        return _fetch_openalex(query, start, today, api_key)
    if source == "semantic_scholar":
        return _fetch_semantic_scholar(query, start, today, api_key)
    if source == "doaj":
        return _fetch_doaj(query, start, today)
    if source == "arxiv":
        return _fetch_arxiv(query, start, today)
    return []


def _normalise_v12_work(
    raw: dict[str, Any],
    *,
    source: str,
    query: str,
    recommendation_kind: str,
    fetched_at: str,
) -> dict[str, Any] | None:
    title = _strip_markup(raw.get("title", raw.get("display_name", "")))
    if not title:
        return None
    source_id = str(raw.get("source_id", raw.get("id", ""))).strip()
    issn_value = raw.get("issn", raw.get("ISSN", []))
    if isinstance(issn_value, str):
        issns = [issn_value.strip()] if issn_value.strip() else []
    elif isinstance(issn_value, list):
        issns = _plain_list(issn_value, 8)
    else:
        issns = []
    authors = raw.get("authors", [])
    authors = _plain_list(authors, 12) if isinstance(authors, list) else []
    doi = _v12_doi(raw.get("doi", raw.get("DOI", "")))
    source_label = SOURCE_LABELS.get(source, source)
    is_preprint = bool(raw.get("is_preprint")) or source == "arxiv" or "arxiv" in str(raw.get("journal", "")).casefold()
    evidence = {
        "source": source,
        "source_label": source_label,
        "source_id": source_id,
        "query": query,
        "fetched_at": fetched_at,
        "url": str(raw.get("url", raw.get("URL", ""))).strip(),
    }
    item_id = doi or source_id or hashlib.sha1(_v12_title_key(title).encode("utf-8")).hexdigest()[:24]
    text = f"{title} {_strip_markup(raw.get('abstract', ''))}".casefold()
    matched_terms = [query] if query.casefold() in text else []
    return {
        "id": item_id,
        "source": source,
        "source_id": source_id,
        "doi": doi,
        "title": title,
        "abstract": _strip_markup(raw.get("abstract", "")),
        "authors": authors,
        "author_keywords": _plain_list(raw.get("author_keywords", []), 20),
        "keyword_source": str(raw.get("keyword_source", "")).strip(),
        "published_date": str(raw.get("published_date", raw.get("publication_date", ""))).strip(),
        "journal": str(raw.get("journal", raw.get("venue", ""))).strip(),
        "issn": issns,
        "publisher": str(raw.get("publisher", "")).strip(),
        "url": str(raw.get("url", raw.get("URL", ""))).strip() or (f"https://doi.org/{doi}" if doi else ""),
        "is_preprint": is_preprint,
        "fetched_at": fetched_at,
        "source_names": [source],
        "source_evidence": [evidence],
        "discovery_lane": recommendation_kind,
        "recommendation_kind": "pending_review",
        "matched_terms": matched_terms,
        "recommendation_reason": "检索候选，尚未完成内容相关性审查。",
        "score": 0,
        "status": "new",
    }


def discover_frontier_candidates(
    profile_view: dict[str, Any],
    *,
    cache: EvidenceCache,
    progress: Any = None,
) -> list[dict[str, Any]]:
    """Discover normalized papers while isolating each public-source failure."""

    def emit(message: str, value: int | None = None) -> None:
        if progress is None:
            return
        try:
            progress(message, value)
        except TypeError:
            progress(message)

    today = date.today()
    try:
        lookback_days = max(1, min(90, int(profile_view.get("lookback_days", 7))))
    except (TypeError, ValueError):
        lookback_days = 7
    start = today - timedelta(days=lookback_days - 1)
    queries = [(q["query"], q["lane"]) for q in plan_frontier_queries(profile_view, today=today.isoformat())]
    if not queries:
        return []
    sources = profile_view.get("sources", {})
    sources = sources if isinstance(sources, dict) else {}
    if not sources:
        sources = DEFAULT_FRONTIER_SOURCES
    jobs = [
        (source, settings if isinstance(settings, dict) else {})
        for source, settings in sources.items()
        if source in SOURCE_LABELS and bool((settings if isinstance(settings, dict) else {}).get("enabled", False))
    ]
    fetched_at = datetime.now().isoformat(timespec="seconds")
    expires_at = (datetime.now() + timedelta(hours=24)).isoformat(timespec="seconds")
    all_rows: list[dict[str, Any]] = []
    total = max(1, len(jobs) * len(queries))
    completed = 0
    for source, settings in jobs:
        for query, recommendation_kind in queries:
            completed += 1
            label = SOURCE_LABELS.get(source, source)
            emit(f"正在从 {label} 查找“{query}”…", int(completed / total * 85))
            query_key = hashlib.sha256(
                f"v12|{query.casefold()}|{start.isoformat()}|{today.isoformat()}".encode("utf-8")
            ).hexdigest()[:32]
            cached = cache.get_source_response(source, query_key, now=fetched_at)
            if cached is not None:
                raw_rows = cached.get("results", [])
                raw_rows = raw_rows if isinstance(raw_rows, list) else []
            else:
                try:
                    raw_rows = _fetch_v12_source(
                        source,
                        query,
                        start,
                        today,
                        str(settings.get("api_key", "")).strip(),
                    )
                except Exception as error:
                    emit(f"{label} 获取失败，已继续其他来源：{error}", int(completed / total * 85))
                    continue
                cache.put_source_response(
                    source,
                    query_key,
                    {"results": raw_rows},
                    fetched_at,
                    expires_at,
                )
            for raw in raw_rows:
                if not isinstance(raw, dict):
                    continue
                work = _normalise_v12_work(
                    raw,
                    source=source,
                    query=query,
                    recommendation_kind=recommendation_kind,
                    fetched_at=fetched_at,
                )
                if work is not None:
                    all_rows.append(work)
                    cache.upsert_work(work)
    # Exclusions are contextual admission decisions, never substring deletion.
    result = dedupe_works(all_rows)
    emit(f"多源检索完成，共保留 {len(result)} 篇去重论文", 100)
    return result


def _quartile_number(value: object) -> str:
    match = re.search(r"(?:Q|JCR\s*)?([1-4])(?:区)?", str(value or "").upper())
    return match.group(1) if match else ""


def _quality_evidence(item: dict[str, Any], library: list[dict[str, Any]]) -> tuple[str, str, str]:
    status = str(item.get("jcr_status", item.get("quality_status", ""))).strip().casefold()
    quartile = _quartile_number(item.get("jcr_quartile", item.get("jcr", "")))
    if not quartile and isinstance(item.get("jcr"), dict):
        jcr = item["jcr"]
        status = status or str(jcr.get("status", "")).strip().casefold()
        metrics = jcr.get("metrics", []) if isinstance(jcr.get("metrics"), list) else []
        quartile = next((_quartile_number(metric.get("quartile")) for metric in metrics if isinstance(metric, dict) and _quartile_number(metric.get("quartile"))), "")
    if status == "verified" and quartile:
        return status, quartile, "item"
    journal_key = _normalized(str(item.get("journal", "")))
    local = next(
        (
            row
            for row in library
            if isinstance(row, dict) and _normalized(str(row.get("name", ""))) == journal_key
        ),
        {},
    )
    if local:
        jcr = local.get("jcr", {}) if isinstance(local.get("jcr"), dict) else {}
        local_status = str(jcr.get("status", local.get("jcr_status", ""))).strip().casefold()
        metrics = jcr.get("metrics", []) if isinstance(jcr.get("metrics"), list) else []
        local_quartile = next((_quartile_number(metric.get("quartile")) for metric in metrics if isinstance(metric, dict) and _quartile_number(metric.get("quartile"))), "")
        if local_status == "verified" and local_quartile:
            return local_status, local_quartile, "journal_library"
    return status or "unknown", quartile, ""


def partition_frontier_items(
    items: list[dict[str, Any]],
    profile: dict[str, Any],
    *,
    easyscholar_ready: bool,
) -> dict[str, list[dict[str, Any]]]:
    """Separate journal papers, preprints and withheld quality records."""
    streams: dict[str, list[dict[str, Any]]] = {"journal": [], "preprint": [], "pending_quality": []}
    library = profile.get("journal_library", []) if isinstance(profile, dict) else []
    library = library if isinstance(library, list) else []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        is_preprint = bool(item.get("is_preprint")) or str(item.get("source", "")).casefold() == "arxiv" or "arxiv" in str(item.get("journal", "")).casefold()
        if is_preprint:
            item["is_preprint"] = True
            item["quality_gate_state"] = "preprint"
            item["quality_gate_reason"] = "separate_preprint_stream"
            if item.get("admission_version"):
                item["admission_route"] = "preprint"
            streams["preprint"].append(item)
            continue
        if not easyscholar_ready:
            item["quality_gate_state"] = "eligible"
            item["quality_gate_reason"] = "division_check_not_configured"
            if item.get("admission_version"):
                item["admission_route"] = "journal"
            streams["journal"].append(item)
            continue
        status, quartile, source = _quality_evidence(item, library)
        item["jcr_status"] = status
        item["jcr_quartile"] = f"Q{quartile}" if quartile else ""
        item["quality_evidence_source"] = source
        if status == "verified" and quartile in {"1", "2"}:
            item["quality_gate_state"] = "eligible"
            item["quality_gate_reason"] = "verified_q1_q2"
            if item.get("admission_version"):
                item["admission_route"] = "journal"
            streams["journal"].append(item)
        else:
            item["quality_gate_state"] = "withheld"
            item["quality_gate_reason"] = "verified_q3_q4" if quartile in {"3", "4"} else "quality_pending"
            if item.get("admission_version"):
                item["admission_route"] = "excluded_quality" if status == "verified" and quartile in {"3", "4"} else "pending_quality"
            streams["pending_quality"].append(item)
    for values in streams.values():
        values.sort(
            key=lambda item: (
                int(item.get("score", 0) or 0),
                str(item.get("published_date", "")),
                str(item.get("id", "")),
            ),
            reverse=True,
        )
    return streams


def _enrich_frontier_quality_from_library(
    items: list[dict[str, Any]], journals: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    library = {
        _normalized(str(journal.get("name", ""))): journal
        for journal in journals
        if isinstance(journal, dict) and str(journal.get("name", "")).strip()
    }
    result: list[dict[str, Any]] = []
    for raw in items:
        item = dict(raw)
        journal = library.get(_normalized(str(item.get("journal", ""))))
        if journal is not None:
            snapshot = journal_quality_snapshot(journal)
            item.update(
                {
                    "jcr_status": snapshot.get("jcr_status", "pending"),
                    "jcr_state": snapshot.get("jcr_status", "pending"),
                    "jcr_quartile": snapshot.get("jcr_quartile", ""),
                    "cas_upgrade": snapshot.get("cas_upgrade", ""),
                    "cas_basic": snapshot.get("cas_basic", ""),
                    "journal_metric_line": snapshot.get("metric_line", ""),
                    "quality_checked_at": snapshot.get("checked_at", ""),
                    "quality_source": snapshot.get("source", ""),
                    "library_journal_id": str(journal.get("id", "")),
                }
            )
        item.setdefault(
            "score_breakdown",
            {"terms": int(item.get("score", 0) or 0), "priority": 0, "quality": 0, "feedback": 0, "ai": 0},
        )
        result.append(item)
    return result


def _enrich_frontier_quality_with_easyscholar(
    items: list[dict[str, Any]],
    journals: list[dict[str, Any]],
    *,
    progress: Any = None,
) -> list[dict[str, Any]]:
    """Fill missing frontier divisions directly from EasyScholar when configured."""
    result = _enrich_frontier_quality_from_library(items, journals)
    if not is_easyscholar_ready():
        return result

    from utils.easyscholar_service import (
        EasyScholarConfigurationError,
        EasyScholarRequestError,
        fetch_easyscholar_metrics,
        merge_easyscholar_patch,
    )

    unresolved: dict[str, list[dict[str, Any]]] = {}
    for item in result:
        journal_name = str(item.get("journal", "")).strip()
        is_preprint = bool(item.get("is_preprint")) or "arxiv" in journal_name.casefold()
        has_jcr = bool(str(item.get("jcr_quartile", "")).strip())
        has_cas = bool(str(item.get("cas_upgrade", "")).strip() or str(item.get("cas_basic", "")).strip())
        if journal_name and not is_preprint and not (has_jcr and has_cas):
            unresolved.setdefault(_normalized(journal_name), []).append(item)

    total = len(unresolved)
    for index, rows in enumerate(unresolved.values(), start=1):
        exemplar = rows[0]
        journal_name = str(exemplar.get("journal", "")).strip()
        if progress is not None:
            try:
                progress(f"EasyScholar 正在核对 {journal_name}（{index}/{total}）…", 88 + int(index * 8 / max(1, total)))
            except TypeError:
                progress(f"EasyScholar 正在核对 {journal_name}（{index}/{total}）…")
        journal = {
            "id": str(exemplar.get("library_journal_id", exemplar.get("id", ""))),
            "name": journal_name,
            "issn": exemplar.get("issn", exemplar.get("issns", [])),
            "publisher": str(exemplar.get("publisher", "")),
            "jcr": deepcopy(exemplar.get("jcr", {})) if isinstance(exemplar.get("jcr"), dict) else {},
            "easyscholar": deepcopy(exemplar.get("easyscholar", {})) if isinstance(exemplar.get("easyscholar"), dict) else {},
        }
        try:
            patch = fetch_easyscholar_metrics(journal)
            enriched = merge_easyscholar_patch(journal, patch, query_signature="frontier")
        except (EasyScholarConfigurationError, EasyScholarRequestError):
            continue
        snapshot = journal_quality_snapshot(enriched)
        for item in rows:
            item.update(
                {
                    "jcr": deepcopy(enriched.get("jcr", {})),
                    "easyscholar": deepcopy(enriched.get("easyscholar", {})),
                    "jcr_status": snapshot.get("jcr_status", "pending"),
                    "jcr_state": snapshot.get("jcr_status", "pending"),
                    "jcr_quartile": snapshot.get("jcr_quartile", ""),
                    "cas_upgrade": snapshot.get("cas_upgrade", ""),
                    "cas_basic": snapshot.get("cas_basic", ""),
                    "journal_metric_line": snapshot.get("metric_line", ""),
                    "quality_checked_at": snapshot.get("checked_at", ""),
                    "quality_source": snapshot.get("source", ""),
                }
            )
        if index < total:
            time.sleep(0.35)
    return result


def update_daily_frontier_v12(
    data: dict[str, Any],
    journals: list[dict[str, Any]],
    *,
    progress: Any = None,
) -> dict[str, Any]:
    """Run the v12 evidence-first refresh while preserving personal actions."""

    def emit(message: str, value: int | None = None) -> None:
        if progress is None:
            return
        try:
            progress(message, value)
        except TypeError:
            progress(message)

    original = data if isinstance(data, dict) else {}
    profile = dict(original.get("profile", {})) if isinstance(original.get("profile"), dict) else {}
    authored = original.get("authored_papers", profile.get("authored_papers", []))
    profile["authored_papers"] = [dict(item) for item in authored if isinstance(item, dict)] if isinstance(authored, list) else []
    profile_view = build_profile_view(profile, now=datetime.now())
    profile_view.update(
        {
            "sources": deepcopy(profile.get("sources", {})),
            "lookback_days": profile.get("lookback_days", 7),
            "excluded_terms": deepcopy(profile.get("excluded_entries", [])),
            "ai_search_terms": deepcopy(profile.get("ai_search_terms", [])),
        }
    )
    cache = EvidenceCache(RESEARCH_INTELLIGENCE_CACHE_FILE)
    cache.initialize()
    emit("共享证据缓存已就绪", 5)
    previous_items = {str(i["id"]): i for i in original.get("items", [])
                      if isinstance(i, dict) and str(i.get("id", "")).strip()}
    personal_states = {"saved", "liked", "dismissed", "read", "deprioritized"}
    candidates = discover_frontier_candidates(profile_view, cache=cache,
        progress=lambda message, value=0: emit(message, 5 + int((value or 0) * 0.3)))
    candidates = dedupe_works(candidates + [dict(i) for i in previous_items.values()
                                          if i.get("status") not in personal_states])
    candidates = [i for i in candidates if previous_items.get(str(i["id"]), {}).get("status") not in personal_states]
    candidates = review_frontier_content(profile, candidates, journals, cache=cache, easyscholar_ready=False,
        progress=lambda message, value=0: emit(message, 35 + int((value or 0) * 0.5)))
    for index, item in enumerate(candidates):
        prior = previous_items.get(str(item["id"]), {})
        if (item.get("content_decision") == "pending" and prior.get("content_decision") == "accept"
                and item.get("admission_issue") in {"reviewer_unavailable", "review_budget_pending", "missing_evaluation"}):
            candidates[index] = dict(prior, admission_stale=True)
    accepted = [i for i in candidates if i.get("content_decision") == "accept"]
    emit("正在核对内容合格论文的期刊分区…", 86)
    accepted = _enrich_frontier_quality_with_easyscholar(
        accepted,
        journals,
        progress=lambda message, value=0: emit(message, 86 + int((value or 0) * 0.12)),
    )
    accepted = apply_frontier_ranking(accepted, profile, journals)
    gate_profile = {"journal_library": journals}
    streams = partition_frontier_items(
        accepted,
        gate_profile,
        easyscholar_ready=is_easyscholar_ready(),
    )
    try:
        daily_limit = max(1, min(12, int(profile.get("daily_limit", 5))))
    except (TypeError, ValueError):
        daily_limit = 5
    journal_mix = select_daily_mix(streams["journal"], limit=daily_limit, minimum_core_ratio=0.5)
    preprint_mix = select_daily_mix(streams["preprint"], limit=daily_limit, minimum_core_ratio=0.5)
    selected_ids = {str(item.get("id", "")) for item in [*journal_mix, *preprint_mix]}
    today_key = date.today().isoformat()
    merged: list[dict[str, Any]] = []
    rejected_or_pending = [i for i in candidates if i.get("content_decision") != "accept"]
    for item in [*streams["journal"], *streams["preprint"], *streams["pending_quality"], *rejected_or_pending]:
        fresh = dict(item)
        if str(fresh.get("id", "")) in selected_ids:
            fresh["recommendation_date"] = today_key
        merged.append(
            merge_frontier_refresh_item(
                fresh,
                previous_items.get(str(fresh.get("id", ""))),
                today_key,
            )
        )
    current_ids = {str(item.get("id", "")) for item in merged}
    merged.extend(
        dict(item)
        for item_id, item in previous_items.items()
        if item_id not in current_ids and (str(item.get("status", "")) in personal_states or item.get("content_decision") == "accept")
    )
    # Source candidates are already deduplicated; never discard a personal
    # history entry just because another record has the same title.
    merged = list({str(i["id"]): i for i in merged}.values())
    personal = [i for i in merged if i.get("status") in personal_states]
    others = [i for i in merged if i.get("status") not in personal_states]
    merged = personal + others[:max(0, 500 - len(personal))]
    counts = {key: len(value) for key, value in streams.items()}
    visible_count = len(journal_mix) + len(preprint_mix)
    core_count = sum(
        1
        for item in [*journal_mix, *preprint_mix]
        if str(item.get("recommendation_kind", "")) == "core_keyword"
    )
    brief = f"今日整理 {visible_count} 篇推荐，其中 {core_count} 篇匹配核心关键词"
    if counts["pending_quality"]:
        brief += f"；另有 {counts['pending_quality']} 篇分区待核验"
    pending_count = sum(i.get("content_decision") == "pending" for i in candidates)
    rejected_count = sum(i.get("content_decision") == "reject" for i in candidates)
    if pending_count:
        brief += f"；{pending_count} 篇等待内容复核"
    brief += f"；已排除 {rejected_count} 篇不相关论文"
    brief += "。"
    emit("每日前沿刷新完成", 100)
    stored_profile = dict(profile)
    stored_profile.pop("authored_papers", None)
    return {
        "data": {
            **original,
            "profile": stored_profile,
            "items": merged,
            "last_checked": today_key,
            "algorithm_version": FRONTIER_ALGORITHM_VERSION,
            "profile_signature": frontier_profile_signature_v11(profile, journals),
        },
        "new_count": sum(1 for item in merged if str(item.get("id", "")) not in previous_items),
        "visible_count": visible_count,
        "brief": brief,
        "errors": [],
        "stream_counts": counts,
    }
