"""Offline-first, explainable journal-selection scoring for v11."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
import re
from uuid import uuid4
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from utils.evidence_cache import EvidenceCache
from utils.frontier_scoring import canonical_text, primary_jcr_quartile
from utils.frontier_service import dedupe_works
from utils.journal_quality import journal_quality_snapshot
from utils.publisher_utils import canonical_publisher


OPENALEX_API = "https://api.openalex.org"
SEMANTIC_SCHOLAR_API = "https://api.semanticscholar.org/graph/v1"
CROSSREF_API = "https://api.crossref.org"
SELECTION_USER_AGENT = "ResearchAssistant/12 (personal desktop research tool)"


def _public_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    query = urlencode({key: value for key, value in (params or {}).items() if value not in {None, ""}})
    address = f"{url}?{query}" if query else url
    request = Request(address, headers={"User-Agent": SELECTION_USER_AGENT, "Accept": "application/json"})
    try:
        with urlopen(request, timeout=16) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(str(error)) from error
    if not isinstance(payload, dict):
        raise RuntimeError("论文来源返回格式异常")
    return payload


def _doi(value: object) -> str:
    text = str(value or "").strip().casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text.strip()


def _issns(value: Any) -> list[str]:
    values = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip().upper()
        key = re.sub(r"[^0-9X]", "", text)
        if len(key) == 8 and key not in seen:
            seen.add(key)
            result.append(f"{key[:4]}-{key[4:]}")
    return result


def _openalex_abstract(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    positions: dict[int, str] = {}
    for word, indexes in value.items():
        for index in indexes if isinstance(indexes, list) else []:
            if isinstance(index, int):
                positions[index] = str(word)
    return " ".join(positions[index] for index in sorted(positions))


def _fetch_similar_source(source: str, manuscript: dict[str, Any], query: str) -> list[dict[str, Any]]:
    if source == "openalex":
        payload = _public_json(
            f"{OPENALEX_API}/works",
            {"search": query, "per-page": 35, "sort": "cited_by_count:desc"},
        )
        result: list[dict[str, Any]] = []
        for work in payload.get("results", []) if isinstance(payload.get("results"), list) else []:
            if not isinstance(work, dict):
                continue
            location = work.get("primary_location", {}) if isinstance(work.get("primary_location"), dict) else {}
            venue = location.get("source", {}) if isinstance(location.get("source"), dict) else {}
            result.append(
                {
                    "id": str(work.get("id", "")),
                    "title": str(work.get("display_name", "")),
                    "abstract": _openalex_abstract(work.get("abstract_inverted_index")),
                    "doi": str(work.get("doi", "")),
                    "journal": str(venue.get("display_name", "")),
                    "issn": _issns(venue.get("issn", [])),
                    "publisher": str(venue.get("host_organization_name", "")),
                    "url": str(location.get("landing_page_url", "")) or str(work.get("doi", "")),
                    "published_date": str(work.get("publication_date", "")),
                }
            )
        return result
    if source == "semantic_scholar":
        payload = _public_json(
            f"{SEMANTIC_SCHOLAR_API}/paper/search",
            {
                "query": query,
                "limit": 35,
                "fields": "title,abstract,venue,journal,publicationDate,externalIds,url,authors",
            },
        )
        result = []
        for paper in payload.get("data", []) if isinstance(payload.get("data"), list) else []:
            if not isinstance(paper, dict):
                continue
            ids = paper.get("externalIds", {}) if isinstance(paper.get("externalIds"), dict) else {}
            journal = paper.get("journal", {}) if isinstance(paper.get("journal"), dict) else {}
            result.append(
                {
                    "id": str(paper.get("paperId", "")),
                    "title": str(paper.get("title", "")),
                    "abstract": str(paper.get("abstract", "")),
                    "doi": str(ids.get("DOI", "")),
                    "journal": str(paper.get("venue", journal.get("name", ""))),
                    "issn": _issns(journal.get("issn", "")),
                    "publisher": "",
                    "url": str(paper.get("url", "")),
                    "published_date": str(paper.get("publicationDate", "")),
                }
            )
        return result
    if source == "crossref":
        payload = _public_json(
            f"{CROSSREF_API}/works",
            {
                "query.bibliographic": query,
                "filter": "type:journal-article",
                "rows": 35,
                "select": "DOI,title,container-title,ISSN,publisher,URL,abstract,published",
            },
        )
        message = payload.get("message", {}) if isinstance(payload.get("message"), dict) else {}
        result = []
        for work in message.get("items", []) if isinstance(message.get("items"), list) else []:
            if not isinstance(work, dict):
                continue
            titles = work.get("title", []) if isinstance(work.get("title"), list) else []
            venues = work.get("container-title", []) if isinstance(work.get("container-title"), list) else []
            result.append(
                {
                    "id": str(work.get("DOI", "")),
                    "title": str(titles[0]) if titles else "",
                    "abstract": re.sub(r"<[^>]+>", " ", str(work.get("abstract", ""))),
                    "doi": str(work.get("DOI", "")),
                    "journal": str(venues[0]) if venues else "",
                    "issn": _issns(work.get("ISSN", [])),
                    "publisher": str(work.get("publisher", "")),
                    "url": str(work.get("URL", "")),
                    "published_date": "",
                }
            )
        return result
    return []


def _similar_query(manuscript: dict[str, Any]) -> str:
    title = " ".join(str(manuscript.get("title", "")).split())
    keywords = _terms(manuscript.get("keywords", []))[:5]
    return " ".join([title, *keywords]).strip()[:700]


def find_similar_works(
    manuscript: dict[str, Any],
    *,
    cache: EvidenceCache,
    progress: Any = None,
) -> list[dict[str, Any]]:
    """Find real similar papers from OpenAlex, Semantic Scholar and Crossref."""

    def emit(message: str, value: int) -> None:
        if progress is None:
            return
        try:
            progress(message, value)
        except TypeError:
            progress(message)

    query = _similar_query(manuscript)
    if not query:
        return []
    labels = {"openalex": "OpenAlex", "semantic_scholar": "Semantic Scholar", "crossref": "Crossref"}
    fetched_at = datetime.now().isoformat(timespec="seconds")
    expires_at = (datetime.now() + timedelta(days=7)).isoformat(timespec="seconds")
    rows: list[dict[str, Any]] = []
    for index, source in enumerate(labels, start=1):
        emit(f"正在从 {labels[source]} 查找相似真实论文…", 8 + index * 20)
        cache_key = hashlib.sha256(f"similar-v12|{query.casefold()}".encode("utf-8")).hexdigest()[:32]
        cached = cache.get_source_response(source, cache_key, now=fetched_at)
        if cached is not None:
            records = cached.get("results", [])
            records = records if isinstance(records, list) else []
        else:
            try:
                records = _fetch_similar_source(source, manuscript, query)
            except Exception as error:
                emit(f"{labels[source]} 获取失败，已继续其他来源：{error}", 8 + index * 20)
                continue
            cache.put_source_response(source, cache_key, {"results": records}, fetched_at, expires_at)
        for raw in records:
            if not isinstance(raw, dict) or not str(raw.get("title", "")).strip():
                continue
            work = {
                "id": str(raw.get("id", raw.get("source_id", ""))).strip(),
                "source": source,
                "source_id": str(raw.get("id", raw.get("source_id", ""))).strip(),
                "title": str(raw.get("title", "")).strip(),
                "abstract": str(raw.get("abstract", "")).strip(),
                "doi": _doi(raw.get("doi", "")),
                "journal": str(raw.get("journal", raw.get("venue", ""))).strip(),
                "issn": _issns(raw.get("issn", raw.get("issns", []))),
                "publisher": str(raw.get("publisher", "")).strip(),
                "url": str(raw.get("url", "")).strip(),
                "published_date": str(raw.get("published_date", "")).strip(),
                "fetched_at": fetched_at,
                "source_names": [source],
                "source_evidence": [
                    {
                        "source": source,
                        "source_id": str(raw.get("id", raw.get("source_id", ""))).strip(),
                        "fetched_at": fetched_at,
                        "url": str(raw.get("url", "")).strip(),
                    }
                ],
            }
            rows.append(work)
            cache.upsert_work(work)
    own_doi = _doi(manuscript.get("doi", ""))
    own_title = canonical_text(manuscript.get("title", ""))
    result = [
        work
        for work in dedupe_works(rows)
        if (not own_doi or _doi(work.get("doi", "")) != own_doi)
        and (not own_title or canonical_text(work.get("title", "")) != own_title)
    ]
    emit(f"已汇总 {len(result)} 篇相似真实论文", 75)
    return result


def derive_journal_candidates(works: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate publication venues, preferring ISSN identity over title."""
    candidates: list[dict[str, Any]] = []
    issn_positions: dict[str, int] = {}
    name_positions: dict[str, int] = {}
    for work in works:
        if not isinstance(work, dict):
            continue
        name = str(work.get("journal", work.get("venue", ""))).strip()
        if not name:
            continue
        issns = _issns(work.get("issn", work.get("issns", [])))
        position = next((issn_positions[value] for value in issns if value in issn_positions), None)
        name_key = canonical_text(name)
        if position is None:
            position = name_positions.get(name_key)
        paper_evidence = {
            "id": str(work.get("id", work.get("source_id", ""))).strip(),
            "title": str(work.get("title", "")).strip(),
            "doi": _doi(work.get("doi", "")),
            "source": str(work.get("source", "")).strip(),
            "url": str(work.get("url", "")).strip(),
        }
        if position is None:
            position = len(candidates)
            candidates.append(
                {
                    "id": "issn:" + issns[0] if issns else "journal:" + hashlib.sha1(name_key.encode("utf-8")).hexdigest()[:20],
                    "name": name,
                    "issns": issns,
                    "publisher": str(work.get("publisher", "")).strip(),
                    "official_url": "",
                    "similar_papers": [paper_evidence],
                    "occurrence_count": 1,
                    "discovery_sources": [str(work.get("source", "")).strip()],
                }
            )
        else:
            candidate = candidates[position]
            candidate["issns"] = list(dict.fromkeys([*candidate.get("issns", []), *issns]))
            if not candidate.get("publisher") and str(work.get("publisher", "")).strip():
                candidate["publisher"] = str(work.get("publisher", "")).strip()
            if paper_evidence not in candidate["similar_papers"]:
                candidate["similar_papers"].append(paper_evidence)
            candidate["occurrence_count"] = len(candidate["similar_papers"])
            candidate["discovery_sources"] = list(
                dict.fromkeys([*candidate.get("discovery_sources", []), str(work.get("source", "")).strip()])
            )
        name_positions[name_key] = position
        for value in candidates[position].get("issns", []):
            issn_positions[value] = position
    candidates.sort(key=lambda item: (-int(item.get("occurrence_count", 0)), canonical_text(item.get("name", ""))))
    return candidates


def _identity_journal_key(candidate: dict[str, Any]) -> str:
    issns = _issns(candidate.get("issns", candidate.get("issn", [])))
    if issns:
        return "issn:" + issns[0]
    return "name:" + canonical_text(candidate.get("name", ""))


def _lookup_journal_identity_sources(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve authoritative identity fields without trusting AI-provided facts."""
    name = str(candidate.get("name", "")).strip()
    issns = _issns(candidate.get("issns", candidate.get("issn", [])))
    sources: list[dict[str, Any]] = []
    crossref_item: dict[str, Any] = {}
    try:
        if issns:
            payload = _public_json(f"{CROSSREF_API}/journals/{quote(issns[0])}")
            message = payload.get("message", {})
            crossref_item = message if isinstance(message, dict) else {}
        elif name:
            payload = _public_json(f"{CROSSREF_API}/journals", {"query": name, "rows": 20})
            message = payload.get("message", {}) if isinstance(payload.get("message"), dict) else {}
            items = message.get("items", []) if isinstance(message.get("items"), list) else []
            crossref_item = next(
                (item for item in items if isinstance(item, dict) and canonical_text(item.get("title", "")) == canonical_text(name)),
                {},
            )
    except Exception:
        crossref_item = {}
    if crossref_item:
        counts = crossref_item.get("counts", {}) if isinstance(crossref_item.get("counts"), dict) else {}
        current_dois = counts.get("current-dois")
        active = bool(current_dois) if isinstance(current_dois, (int, float)) else None
        sources.append(
            {
                "source": "crossref",
                "name": str(crossref_item.get("title", name)).strip(),
                "issns": _issns(crossref_item.get("ISSN", issns)),
                "publisher": str(crossref_item.get("publisher", "")).strip(),
                "official_url": str(crossref_item.get("URL", "")).strip(),
                "active": active,
                "checked_at": date.today().isoformat(),
            }
        )
    if issns:
        try:
            payload = _public_json(f"{OPENALEX_API}/sources/issn:{quote(issns[0])}")
        except Exception:
            payload = {}
        if payload:
            counts_by_year = payload.get("counts_by_year", []) if isinstance(payload.get("counts_by_year"), list) else []
            recent = [
                int(row.get("works_count", 0) or 0)
                for row in counts_by_year
                if isinstance(row, dict) and int(row.get("year", 0) or 0) >= date.today().year - 2
            ]
            sources.append(
                {
                    "source": "openalex",
                    "name": str(payload.get("display_name", name)).strip(),
                    "issns": _issns(payload.get("issn", [payload.get("issn_l", "")])),
                    "publisher": str(payload.get("host_organization_name", "")).strip(),
                    "official_url": str(payload.get("homepage_url", "")).strip(),
                    "active": any(recent) if recent else None,
                    "checked_at": date.today().isoformat(),
                }
            )
    return sources


def verify_journal_identity(
    candidate: dict[str, Any],
    *,
    cache: EvidenceCache,
) -> dict[str, Any]:
    """Admit only a real, active journal with authoritative identity fields."""
    candidate = candidate if isinstance(candidate, dict) else {}
    key = _identity_journal_key(candidate)
    cached = cache.get_journal_evidence(key)
    if isinstance(cached, dict) and cached.get("identity_contract") == 12:
        return cached
    sources = [dict(value) for value in _lookup_journal_identity_sources(candidate) if isinstance(value, dict)]
    name = next((str(source.get("name", "")).strip() for source in sources if str(source.get("name", "")).strip()), str(candidate.get("name", "")).strip())
    issns = _issns([value for source in sources for value in _issns(source.get("issns", []))])
    publisher_values = [
        canonical_publisher(str(source.get("publisher", "")).strip())
        for source in sources
        if str(source.get("publisher", "")).strip()
    ]
    publisher = publisher_values[0] if publisher_values else ""
    official_url = next(
        (str(source.get("official_url", "")).strip() for source in sources if str(source.get("official_url", "")).strip().startswith(("http://", "https://"))),
        "",
    )
    active_values = [source.get("active") for source in sources if isinstance(source.get("active"), bool)]
    active: bool | None = False if False in active_values else True if True in active_values else None
    conflicts: list[dict[str, Any]] = []
    unique_publishers = list(dict.fromkeys(value for value in publisher_values if value))
    if len(unique_publishers) > 1:
        conflicts.append({"field": "publisher", "values": unique_publishers})
    unique_names = list(dict.fromkeys(str(source.get("name", "")).strip() for source in sources if str(source.get("name", "")).strip()))
    if len({canonical_text(value) for value in unique_names}) > 1:
        conflicts.append({"field": "name", "values": unique_names})
    if len(set(active_values)) > 1:
        conflicts.append({"field": "active", "values": active_values})
    missing: list[str] = []
    if not issns:
        missing.append("ISSN")
    if not publisher:
        missing.append("出版社")
    if not official_url:
        missing.append("官方主页")
    if active is None:
        missing.append("出版状态")
    verified = bool(sources) and not missing and active is True
    if active is False:
        reason = "期刊已停止出版或近年无可确认的出版活动"
    elif missing:
        reason = "期刊身份信息未核验完整：" + "、".join(missing)
    elif not sources:
        reason = "未找到权威期刊身份记录"
    else:
        reason = "期刊身份、ISSN、出版社、官网和出版状态已核验"
    result = {
        "identity_contract": 12,
        "verified": verified,
        "name": name,
        "issns": issns,
        "publisher": publisher,
        "official_url": official_url,
        "active": active,
        "sources": sources,
        "verified_at": datetime.now().isoformat(timespec="seconds"),
        "conflicts": conflicts,
        "missing": missing,
        "reason": reason,
    }
    cache.put_journal_evidence(key, result)
    return result


def _terms(value: Any) -> list[str]:
    if isinstance(value, str):
        values = value.replace("，", ",").split(",")
    elif isinstance(value, list):
        values = value
    else:
        values = []
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        key = canonical_text(text)
        if text and key not in seen:
            seen.add(key)
            result.append(text)
    return result[:24]


def _matches(term: str, haystack: str) -> bool:
    key = canonical_text(term)
    if not key:
        return False
    if key in haystack:
        return True
    aliases = {
        "soil organic carbon": "soc",
        "mineral-associated organic carbon": "maoc",
        "particulate organic carbon": "poc",
    }
    return aliases.get(key, "") in haystack.split()


def _journal_haystack(journal: dict[str, Any]) -> str:
    fields = journal.get("fields", [])
    fields = fields if isinstance(fields, list) else []
    tags = journal.get("ai_tags", [])
    tags = tags if isinstance(tags, list) else []
    values = [
        *fields,
        *tags,
        journal.get("ai_scope_cn", ""),
        journal.get("ai_fit_cn", ""),
        journal.get("notes", ""),
    ]
    return canonical_text(" ".join(str(value) for value in values if str(value).strip()))


def _paper_terms(paper: dict[str, Any], profile: dict[str, Any]) -> list[str]:
    terms = _terms(paper.get("keywords", []))
    for item in profile.get("terms", []) if isinstance(profile.get("terms", []), list) else []:
        if isinstance(item, dict) and str(item.get("text", "")).strip():
            terms.append(str(item["text"]).strip())
    if not terms:
        terms = _terms([paper.get("title", ""), paper.get("summary", "")])
    return _terms(terms)


def _quality_component(journal: dict[str, Any]) -> tuple[int, str]:
    snapshot = journal_quality_snapshot(journal)
    status, quartile = snapshot["jcr_status"], snapshot["jcr_quartile"]
    if status in {"verified", "manual"}:
        return {"Q1": 25, "Q2": 19, "Q3": 8, "Q4": 4}.get(quartile, 8), quartile or "未知"
    if status == "ai_estimated":
        return 9, "AI 待核验"
    return 10, "未知"


def score_journal_candidate(
    paper: dict[str, Any],
    journal: dict[str, Any],
    profile: dict[str, Any],
    history: dict[str, Any],
    constraints: dict[str, Any],
) -> dict[str, Any]:
    """Score one candidate using four fixed, user-visible local components."""
    paper = paper if isinstance(paper, dict) else {}
    journal = journal if isinstance(journal, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    history = history if isinstance(history, dict) else {}
    constraints = constraints if isinstance(constraints, dict) else {}
    status, quartile = primary_jcr_quartile(journal)
    if bool(constraints.get("filter_known_q3_q4", False)) and status in {"verified", "manual"} and quartile in {"Q3", "Q4"}:
        return {
            "journal_id": str(journal.get("id", "")),
            "journal_name": str(journal.get("name", "")),
            "excluded": True,
            "exclusion_reason": f"已核验 {quartile}，当前设置过滤三四区期刊",
            "topic_fit": 0,
            "quality": 0,
            "personal_experience": 0,
            "constraints": 0,
            "local_score": 0,
            "ai_adjustment": 0,
            "total_score": 0,
            "matches": [],
            "reasons": [],
            "risks": [f"已核验 {quartile}"],
        }

    terms = _paper_terms(paper, profile)
    haystack = _journal_haystack(journal)
    matches = [term for term in terms if _matches(term, haystack)]
    topic_fit = min(40, len(matches) * 12 + (4 if len(matches) >= 2 else 0))
    quality, quality_label = _quality_component(journal)
    priority = str(journal.get("frontier_priority", "不订阅"))
    priority_score = {"必看": 8, "关注": 5, "扩展": 2}.get(priority, 0)
    try:
        submission_count = max(0, min(4, int(history.get("submission_count", 0))))
    except (TypeError, ValueError):
        submission_count = 0
    experience_score = min(20, priority_score + submission_count * 2 + (3 if str(journal.get("notes", "")).strip() else 0))
    remaining = 15
    risks: list[str] = []
    if not str(journal.get("issn", "")).strip():
        remaining -= 2
        risks.append("ISSN 未补全")
    if not str(journal.get("website", "")).strip():
        remaining -= 2
        risks.append("官网或投稿链接未补全")
    if bool(constraints.get("require_open_access", False)) and "open access" not in haystack and "oa" not in haystack:
        remaining -= 4
        risks.append("需核实开放获取要求")
    if bool(constraints.get("avoid_previously_rejected", False)) and bool(history.get("rejected", False)):
        remaining = 0
        risks.append("该论文曾被本刊拒稿")
    allowed_publishers = {canonical_text(value) for value in constraints.get("allowed_publishers", []) if str(value).strip()} if isinstance(constraints.get("allowed_publishers", []), list) else set()
    if allowed_publishers and canonical_text(journal.get("publisher", "")) not in allowed_publishers:
        remaining = 0
        risks.append("不在限定出版社范围")
    constraint_score = max(0, min(15, remaining))
    local = topic_fit + quality + experience_score + constraint_score
    reasons = [
        f"主题命中 {len(matches)} 项" if matches else "期刊方向资料较少",
        f"质量：{quality_label}",
    ]
    if priority_score:
        reasons.append(f"个人优先级：{priority}")
    return {
        "journal_id": str(journal.get("id", "")),
        "journal_name": str(journal.get("name", "")),
        "journal": deepcopy(journal),
        "excluded": False,
        "exclusion_reason": "",
        "topic_fit": topic_fit,
        "quality": quality,
        "personal_experience": experience_score,
        "constraints": constraint_score,
        "local_score": local,
        "ai_adjustment": 0,
        "total_score": local,
        "matches": matches,
        "reasons": reasons,
        "risks": risks,
    }


def _history_for_journal(journal: dict[str, Any], history: dict[str, Any]) -> dict[str, Any]:
    key = str(journal.get("id", ""))
    if isinstance(history.get(key), dict):
        return history[key]
    name = canonical_text(journal.get("name", ""))
    if isinstance(history.get(name), dict):
        return history[name]
    publisher = canonical_text(journal.get("publisher", ""))
    usage_key = f"{name}|{publisher}" if name else ""
    if usage_key and isinstance(history.get(usage_key), dict):
        return history[usage_key]
    return {}


def rank_journal_candidates(
    paper: dict[str, Any],
    journals: list[dict[str, Any]],
    profile: dict[str, Any],
    history: dict[str, Any],
    constraints: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates = [
        score_journal_candidate(paper, journal, profile, _history_for_journal(journal, history), constraints)
        for journal in journals
        if isinstance(journal, dict) and str(journal.get("id", "")).strip()
    ]
    visible = [candidate for candidate in candidates if not candidate["excluded"]]
    visible.sort(key=lambda row: (-int(row["total_score"]), canonical_text(row["journal_name"])))
    return visible


def apply_ai_selection_patch(candidates: list[dict[str, Any]], patch: dict[str, Any]) -> list[dict[str, Any]]:
    """Merge a validated AI patch while retaining the local order after failures."""
    adjustments = {
        str(item.get("id", "")): item
        for item in patch.get("ranked", []) if isinstance(patch, dict) and isinstance(item, dict) and str(item.get("id", ""))
    }
    result = [deepcopy(candidate) for candidate in candidates]
    for candidate in result:
        patch_item = adjustments.get(str(candidate.get("journal_id", "")))
        if patch_item:
            try:
                adjustment = max(-10, min(10, int(patch_item.get("adjustment", 0))))
            except (TypeError, ValueError):
                adjustment = 0
            candidate["ai_adjustment"] = adjustment
            candidate["total_score"] = int(candidate["local_score"]) + adjustment
            candidate["ai_total_score"] = candidate["total_score"]
            candidate["ai_reason_cn"] = str(patch_item.get("reason_cn", "")).strip()
            candidate["ai_risk_cn"] = str(patch_item.get("risk_cn", "")).strip()
        else:
            candidate["ai_adjustment"] = 0
            candidate["total_score"] = int(candidate["local_score"])
            candidate["ai_total_score"] = candidate["total_score"]
    if adjustments:
        result.sort(key=lambda row: (-int(row["total_score"]), canonical_text(row["journal_name"])))
    return result


def normalize_selection_requirements(raw: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize the small, explicit controls shown by the AI-first workbench."""
    raw = raw if isinstance(raw, dict) else {}
    oa_mode = str(raw.get("oa_mode", "")).strip().casefold()
    if oa_mode not in {"any", "prefer", "require"}:
        oa_mode = "require" if bool(raw.get("require_open_access", False)) else "any"
    quartile_target = str(raw.get("quartile_target", "")).strip().casefold()
    if quartile_target not in {"any", "q1", "q1_q2"}:
        quartile_target = "any"
    speed_priority = str(raw.get("speed_priority", "")).strip().casefold()
    if speed_priority not in {"standard", "priority", "urgent"}:
        speed_priority = "standard"
    publisher_value = raw.get("publishers", raw.get("publisher", []))
    if isinstance(publisher_value, str):
        publisher_value = [publisher_value]
    publishers = _terms(publisher_value)
    fee_modes: list[str] = []
    raw_fee_modes = raw.get("fee_modes", raw.get("fee_mode", []))
    if isinstance(raw_fee_modes, str):
        raw_fee_modes = [raw_fee_modes]
    for value in raw_fee_modes if isinstance(raw_fee_modes, list) else []:
        mode = str(value or "").strip().casefold()
        aliases = {"free": "no_fee", "unpaid": "no_fee", "any": "any", "不限": "any", "不付费": "no_fee", "付费": "paid"}
        mode = aliases.get(mode, mode)
        if mode in {"no_fee", "paid", "any"} and mode not in fee_modes:
            fee_modes.append(mode)
    jcr_quartiles: list[str] = []
    for value in raw.get("jcr_quartiles", []) if isinstance(raw.get("jcr_quartiles", []), list) else []:
        normalized = _normalize_quartile(value, prefix="Q")
        if normalized and normalized not in jcr_quartiles:
            jcr_quartiles.append(normalized)
    cas_quartiles: list[str] = []
    for value in raw.get("cas_quartiles", []) if isinstance(raw.get("cas_quartiles", []), list) else []:
        normalized = _normalize_quartile(value, prefix="")
        if normalized and normalized not in cas_quartiles:
            cas_quartiles.append(normalized)
    fit_strictness = str(raw.get("fit_strictness", "balanced")).strip().casefold()
    if fit_strictness not in {"lenient", "balanced", "strict"}:
        fit_strictness = "balanced"
    def names(key: str) -> list[str]:
        values = raw.get(key, [])
        values = values if isinstance(values, list) else []
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            normalized = canonical_text(text)
            if normalized and normalized not in seen:
                seen.add(normalized)
                result.append(text)
        return result[:120]
    return {
        "oa_mode": oa_mode,
        "quartile_target": quartile_target,
        "speed_priority": speed_priority,
        "fit_strictness": fit_strictness,
        "filter_known_q3_q4": bool(raw.get("filter_known_q3_q4", True)),
        "avoid_previously_rejected": bool(raw.get("avoid_previously_rejected", False)),
        "publishers": publishers,
        "fee_modes": fee_modes,
        "jcr_quartiles": jcr_quartiles,
        "cas_quartiles": cas_quartiles,
        # These fields are populated by the iterative AI workflow.  Keeping
        # them in the normalized contract makes every round deterministic and
        # prevents a rejected or already-searched title from reappearing.
        "rejected_journal_names": names("rejected_journal_names"),
        "searched_journal_names": names("searched_journal_names"),
    }


def topic_fit_threshold(strictness: str) -> int:
    return {"lenient": 55, "balanced": 65, "strict": 75}.get(str(strictness).strip().casefold(), 65)


def _selection_easyscholar_ready() -> bool:
    from utils.easyscholar_service import is_easyscholar_ready

    return is_easyscholar_ready()


def _local_journal_match(journal: dict[str, Any]) -> dict[str, Any]:
    try:
        from utils.file_manager import load_journal_library

        library = load_journal_library()
    except Exception:
        return {}
    target_issns = set(_issns(journal.get("issns", journal.get("issn", []))))
    target_name = canonical_text(journal.get("name", ""))
    for local in library:
        if not isinstance(local, dict):
            continue
        local_issns = set(_issns(local.get("issn", local.get("issns", []))))
        if target_issns and local_issns and target_issns.intersection(local_issns):
            return local
        if target_name and canonical_text(local.get("name", "")) == target_name:
            return local
    return {}


def _verify_selection_divisions(journal: dict[str, Any], ready: bool) -> dict[str, Any]:
    """Enrich division evidence; only this layer is allowed to call EasyScholar."""
    result = deepcopy(journal)
    local = _local_journal_match(result)
    if local:
        for field in ("jcr", "easyscholar", "fee_mode", "oa_status", "fields", "ai_scope_cn"):
            if field in local and (field not in result or not result.get(field)):
                result[field] = deepcopy(local[field])
        result["local_library_evidence"] = {
            "id": str(local.get("id", "")),
            "name": str(local.get("name", "")),
            "checked_at": str(local.get("metadata_updated_at", "")),
        }
    if not ready:
        return result
    snapshot = journal_quality_snapshot(result)
    if snapshot.get("jcr_status") in {"verified", "manual"} and snapshot.get("jcr_quartile") and _cas_quartile(result):
        return result
    try:
        from utils.easyscholar_service import (
            fetch_easyscholar_metrics,
            journal_easyscholar_signature,
            merge_easyscholar_patch,
        )

        patch = fetch_easyscholar_metrics(result)
        return merge_easyscholar_patch(
            result,
            patch,
            query_signature=journal_easyscholar_signature(result),
        )
    except Exception as error:  # noqa: BLE001 - failed verification becomes a hard unknown only when selected
        result["division_verification_error"] = str(error)
        return result


def _assess_verified_journals_with_ai(
    manuscript: dict[str, Any],
    journals: list[dict[str, Any]],
    requirements: dict[str, Any],
    progress: Any = None,
) -> dict[str, dict[str, Any]]:
    from utils.ai_service import assess_verified_journal_fit_with_ai

    return assess_verified_journal_fit_with_ai(manuscript, journals, requirements, progress=progress)


def _request_selection_supplements(
    manuscript: dict[str, Any],
    requirements: dict[str, Any],
    excluded_keys: list[str],
    round_index: int,
) -> list[dict[str, Any]]:
    from utils.ai_service import recommend_journals_with_ai

    response = recommend_journals_with_ai(
        manuscript,
        [],
        {},
        requirements,
        excluded_journal_names=excluded_keys,
        round_index=round_index,
        strict_discovery=True,
        verify_divisions=False,
    )
    values = response.get("external_candidates", []) if isinstance(response, dict) else []
    return [dict(value) for value in values if isinstance(value, dict)] if isinstance(values, list) else []


def _selection_candidate_key(candidate: dict[str, Any]) -> str:
    issns = _issns(candidate.get("issns", candidate.get("issn", [])))
    if issns:
        return "issn:" + issns[0]
    return "name:" + canonical_journal_name(candidate.get("name", candidate.get("journal_name", "")))


def _rejected_selection_keys(rejected: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    issns: set[str] = set()
    for item in rejected:
        if not isinstance(item, dict):
            continue
        for value in (
            item.get("journal_name", item.get("name", "")),
            *(
                item.get("aliases", [])
                if isinstance(item.get("aliases"), list)
                else []
            ),
        ):
            key = canonical_journal_name(value)
            if key:
                names.add(key)
        issns.update(_issns(item.get("issn", item.get("issns", []))))
    return names, issns


def _strict_division_match(journal: dict[str, Any], requirements: dict[str, Any], ready: bool) -> bool:
    if not ready:
        return True
    jcr_targets = set(requirements.get("jcr_quartiles", []))
    cas_targets = set(requirements.get("cas_quartiles", []))
    if jcr_targets:
        snapshot = journal_quality_snapshot(journal)
        if snapshot.get("jcr_status") not in {"verified", "manual"} or snapshot.get("jcr_quartile") not in jcr_targets:
            return False
    if cas_targets:
        cas = _cas_quartile(journal)
        if not cas or cas not in cas_targets:
            return False
    return True


def _fee_match_set(journal: dict[str, Any]) -> set[str]:
    _mode, supported = _fee_modes_for_journal(journal)
    return supported


def run_selection_rounds(
    manuscript: dict[str, Any],
    requirements: dict[str, Any],
    rejected: list[dict[str, Any]],
    *,
    cache: EvidenceCache,
    max_rounds: int = 8,
    no_growth_limit: int = 2,
    progress: Any = None,
) -> dict[str, Any]:
    """Search real venues first, then admit verified AI supplements over rounds."""

    def emit(message: str, value: int) -> None:
        if progress is None:
            return
        try:
            progress(message, value)
        except TypeError:
            progress(message)

    normalized = normalize_selection_requirements(requirements)
    rounds_limit = max(1, min(12, int(max_rounds or 8)))
    no_growth_target = max(1, min(5, int(no_growth_limit or 2)))
    rejected_names, rejected_issns = _rejected_selection_keys(rejected)
    emit("正在从相似真实论文生成候选期刊…", 4)
    similar_works = find_similar_works(manuscript, cache=cache, progress=progress)
    initial_candidates = derive_journal_candidates(similar_works)
    searched: set[str] = set()
    admitted: set[str] = set()
    results: list[dict[str, Any]] = []
    rejected_counts = {
        "previously_rejected": 0,
        "identity_or_inactive": 0,
        "duplicate": 0,
        "publisher": 0,
        "division": 0,
        "topic_fit": 0,
    }
    no_growth = 0
    rounds_completed = 0
    stop_reason = "max_rounds"
    ready = bool(_selection_easyscholar_ready())
    publisher_targets = {
        canonical_text(canonical_publisher(value))
        for value in normalized.get("publishers", [])
        if str(value).strip()
    }
    threshold = topic_fit_threshold(normalized.get("fit_strictness", "balanced"))

    for round_index in range(1, rounds_limit + 1):
        rounds_completed = round_index
        emit(f"第 {round_index}/{rounds_limit} 轮：补充并核验候选期刊…", min(8 + round_index * 9, 82))
        queue = [dict(value) for value in initial_candidates] if round_index == 1 else []
        try:
            supplements = _request_selection_supplements(
                manuscript,
                normalized,
                sorted(searched | rejected_names),
                round_index,
            )
        except Exception:
            supplements = []
        queue.extend(supplements)
        verified_batch: list[dict[str, Any]] = []
        for candidate in queue:
            name = str(candidate.get("name", candidate.get("journal_name", ""))).strip()
            candidate_issns = set(_issns(candidate.get("issns", candidate.get("issn", []))))
            name_key = canonical_journal_name(name)
            if name_key in rejected_names or candidate_issns.intersection(rejected_issns):
                rejected_counts["previously_rejected"] += 1
                continue
            key = _selection_candidate_key(candidate)
            if not key or key in searched or key in admitted:
                rejected_counts["duplicate"] += 1
                continue
            searched.add(key)
            emit(f"正在核验“{name[:42]}”的身份、ISSN、出版社和官网…", min(12 + round_index * 9, 84))
            identity = verify_journal_identity(candidate, cache=cache)
            if not identity.get("verified") or identity.get("active") is not True:
                rejected_counts["identity_or_inactive"] += 1
                continue
            journal = {
                **candidate,
                **identity,
                "id": _selection_candidate_key(identity),
                "name": str(identity.get("name", name)).strip(),
                "issn": identity.get("issns", []),
                "issns": identity.get("issns", []),
                "website": str(identity.get("official_url", "")),
                "identity_evidence": identity,
            }
            actual_publisher = canonical_text(canonical_publisher(str(journal.get("publisher", ""))))
            if publisher_targets and actual_publisher not in publisher_targets:
                rejected_counts["publisher"] += 1
                continue
            journal = _verify_selection_divisions(journal, ready)
            journal.setdefault("id", _selection_candidate_key(identity))
            journal.setdefault("name", str(identity.get("name", name)).strip())
            journal.setdefault("issns", identity.get("issns", []))
            journal.setdefault("issn", identity.get("issns", []))
            journal.setdefault("publisher", str(identity.get("publisher", "")))
            journal.setdefault("website", str(identity.get("official_url", "")))
            journal.setdefault("identity_evidence", identity)
            if not _strict_division_match(journal, normalized, ready):
                rejected_counts["division"] += 1
                continue
            verified_batch.append(journal)
        assessments = (
            _assess_verified_journals_with_ai(manuscript, verified_batch, normalized, progress=progress)
            if verified_batch
            else {}
        )
        growth = 0
        for journal in verified_batch:
            assessment = assessments.get(str(journal.get("id", "")), {})
            try:
                fit_score = max(0, min(100, int(round(float(assessment.get("fit_score", 0))))))
            except (TypeError, ValueError):
                fit_score = 0
            if fit_score < threshold:
                rejected_counts["topic_fit"] += 1
                continue
            key = _selection_candidate_key(journal)
            if key in admitted:
                rejected_counts["duplicate"] += 1
                continue
            admitted.add(key)
            fee_matches = _fee_match_set(journal)
            wanted_fees = set(normalized.get("fee_modes", [])) - {"any"}
            fee_bonus = 3 if wanted_fees and fee_matches.intersection(wanted_fees) else 0
            speed_score, speed_text = _time_score(assessment, normalized.get("speed_priority", "standard"))
            ranking_score = fit_score + fee_bonus + speed_score
            result_id = key or "journal:" + hashlib.sha1(str(journal.get("name", "")).encode("utf-8")).hexdigest()[:20]
            results.append(
                {
                    "result_id": result_id,
                    "journal_id": str(journal.get("id", result_id)),
                    "journal_name": str(journal.get("name", "")),
                    "journal": deepcopy(journal),
                    "ai_total_score": fit_score,
                    "total_score": fit_score,
                    "ranking_score": ranking_score,
                    "reason_cn": str(assessment.get("reason_cn", "")).strip()[:320] or "主题契合度达到所选最低线。",
                    "publisher": str(journal.get("publisher", "")),
                    "fee_mode": _fee_modes_for_journal(journal)[0],
                    "fee_matches": sorted(fee_matches),
                    "estimated_decision_days_min": int(assessment.get("estimated_decision_days_min", 0) or 0),
                    "estimated_decision_days_max": int(assessment.get("estimated_decision_days_max", 0) or 0),
                    "estimated_speed_text": speed_text,
                    "identity_evidence": deepcopy(journal.get("identity_evidence", {})),
                    "similar_papers": deepcopy(journal.get("similar_papers", [])),
                    "verified_at": str(journal.get("verified_at", "")),
                    "easyscholar_configured": ready,
                    "is_external": not bool(journal.get("local_library_evidence")),
                }
            )
            growth += 1
        if growth == 0:
            no_growth += 1
        else:
            no_growth = 0
        if no_growth >= no_growth_target:
            stop_reason = "no_growth_limit"
            break
    results.sort(
        key=lambda item: (
            -int(item.get("ranking_score", 0)),
            -int(item.get("ai_total_score", 0)),
            canonical_text(item.get("journal_name", "")),
        )
    )
    emit(f"选刊完成：共保留 {len(results)} 本满足全部硬条件的期刊。", 100)
    return {
        "results": results,
        "searched_keys": sorted(searched),
        "rejected_counts": rejected_counts,
        "rounds": rounds_completed,
        "stop_reason": stop_reason,
        "similar_work_count": len(similar_works),
        "verification_configured": ready,
    }


def _clamp_score(value: Any, maximum: int) -> int:
    try:
        return max(0, min(maximum, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def _oa_status(value: Any) -> str:
    status = str(value or "").strip().casefold()
    if status in {"yes", "open", "open_access", "oa", "开放", "开放获取"}:
        return "yes"
    if status in {"no", "closed", "subscription", "非开放", "订阅"}:
        return "no"
    return "unknown"


def _known_quartile(journal: dict[str, Any]) -> tuple[str, str]:
    snapshot = journal_quality_snapshot(journal)
    return str(snapshot["jcr_status"]), str(snapshot["jcr_quartile"])


def _normalize_quartile(value: Any, *, prefix: str) -> str:
    text = str(value or "").strip().upper().replace(" ", "")
    for number in range(1, 5):
        if text in {str(number), f"{number}区", f"Q{number}"}:
            return f"Q{number}" if prefix == "Q" else str(number)
    match = re.search(r"(?:Q)?([1-4])区", text)
    if match:
        return f"Q{match.group(1)}" if prefix == "Q" else match.group(1)
    return ""


def _normalize_fee_mode(value: Any) -> str:
    text = canonical_text(value)
    if text in {"hybrid", "mixed", "mixed oa", "hybrid oa", "混合", "混合 oa", "混合开放获取"}:
        return "hybrid"
    if text in {"apc", "full oa", "full open access", "gold oa", "开放获取", "开放"}:
        return "apc"
    if text in {"subscription", "closed", "closed access", "非开放", "订阅", "订阅制"}:
        return "subscription"
    return "unknown"


def _fee_modes_for_journal(journal: dict[str, Any]) -> tuple[str, set[str]]:
    raw = journal.get("fee_mode", journal.get("fee_type", journal.get("oa_fee_mode", "")))
    mode = _normalize_fee_mode(raw)
    supported = {"hybrid": {"no_fee", "paid"}, "apc": {"paid"}, "subscription": {"no_fee"}}.get(mode, set())
    return mode, supported


def _publisher_key(value: Any) -> str:
    return canonical_text(canonical_publisher(str(value or "")))


def _cas_quartile(journal: dict[str, Any]) -> str:
    snapshot = journal_quality_snapshot(journal)
    for value in (snapshot.get("cas_upgrade"), snapshot.get("cas_basic")):
        normalized = _normalize_quartile(value, prefix="")
        if normalized:
            return normalized
    return ""


def _normalized_name_set(values: Any) -> set[str]:
    raw = values if isinstance(values, list) else []
    return {canonical_journal_name(value) for value in raw if canonical_journal_name(value)}


def journal_meets_hard_requirements(
    journal: dict[str, Any],
    requirements: dict[str, Any] | None = None,
    *,
    rejected_names: list[str] | None = None,
    searched_names: list[str] | None = None,
    history: dict[str, Any] | None = None,
    verify_divisions: bool = True,
) -> tuple[bool, str]:
    """Apply the admission gate after optional EasyScholar verification.

    A selected publisher is a hard user preference: a known conflicting
    publisher is rejected, while an empty/unknown publisher is retained for
    manual verification.  JCR and CAS remain hard division gates when
    verification is available.  Fee mode and speed remain soft preferences.
    When ``verify_divisions`` is false (for example, EasyScholar is not
    configured), division facts are left pending instead of being treated as
    failures.
    """
    journal = journal if isinstance(journal, dict) else {}
    normalized = normalize_selection_requirements(requirements)
    name = canonical_journal_name(journal.get("name", ""))
    rejected = _normalized_name_set([*normalized.get("rejected_journal_names", []), *(rejected_names or [])])
    searched = _normalized_name_set([*normalized.get("searched_journal_names", []), *(searched_names or [])])
    if name and name in rejected:
        return False, "该论文曾被本刊拒稿"
    if name and name in searched:
        return False, "本轮已搜索过该期刊"
    publisher_reason = _publisher_requirement_reason(journal, normalized)
    if publisher_reason:
        return False, publisher_reason
    if not _passes_new_requirements(journal, normalized, verify_divisions=verify_divisions):
        return False, "未满足 JCR 或中科院分区硬条件"
    history = history if isinstance(history, dict) else {}
    exclusion = _is_excluded_by_requirements(journal, normalized, history, verify_divisions=verify_divisions)
    if exclusion:
        return False, exclusion
    return True, ""


def _publisher_requirement_reason(
    journal: dict[str, Any], requirements: dict[str, Any]
) -> str:
    """Return a rejection reason only for a known publisher mismatch.

    Missing publisher metadata is deliberately non-fatal.  This keeps AI
    external candidates visible for later Crossref/EasyScholar/manual review.
    """
    publishers = {_publisher_key(value) for value in requirements.get("publishers", []) if _publisher_key(value)}
    if not publishers:
        return ""
    actual = _publisher_key(journal.get("publisher", ""))
    if actual and actual not in publishers:
        return "未满足所选出版社硬偏好"
    return ""


def _passes_new_requirements(
    journal: dict[str, Any], requirements: dict[str, Any], *, verify_divisions: bool = True
) -> bool:
    """Return whether known division facts conflict with hard targets.

    Missing metadata is intentionally accepted.  A candidate is removed only
    when a verified/manual JCR or CAS value is known and contradicts the
    selected division.
    """
    if not verify_divisions:
        return True
    jcr_quartiles = set(requirements.get("jcr_quartiles", []))
    if jcr_quartiles:
        status, quartile = _known_quartile(journal)
        if status in {"verified", "manual"} and quartile and quartile not in jcr_quartiles:
            return False
    cas_quartiles = set(requirements.get("cas_quartiles", []))
    cas_quartile = _cas_quartile(journal)
    if cas_quartiles and cas_quartile and cas_quartile not in cas_quartiles:
        return False
    return True


def _is_excluded_by_requirements(
    journal: dict[str, Any],
    requirements: dict[str, Any],
    history: dict[str, Any],
    *,
    verify_divisions: bool = True,
) -> str:
    status, quartile = _known_quartile(journal)
    if verify_divisions and requirements["filter_known_q3_q4"] and status in {"verified", "manual"} and quartile in {"Q3", "Q4"}:
        return f"已同步/核验 {quartile}，当前设置过滤三四区"
    if requirements["avoid_previously_rejected"] and bool(history.get("rejected", False)):
        return "该论文曾被本刊拒稿"
    return ""


def _soft_requirement_notes(
    journal: dict[str, Any], requirements: dict[str, Any], *, is_external: bool = False
) -> tuple[list[str], list[str]]:
    """Describe fee and publisher verification state for the result row."""
    reasons: list[str] = []
    risks: list[str] = []
    publishers = {_publisher_key(value) for value in requirements.get("publishers", [])}
    if publishers:
        actual = _publisher_key(journal.get("publisher", ""))
        if is_external:
            risks.append("出版社由 AI 提供，投稿前需核对官网")
        elif not actual:
            risks.append("出版社信息待核验")
        elif actual in publishers:
            reasons.append("出版社符合偏好")
        else:
            # A known mismatch is removed by the admission gate.  This branch
            # is only reachable for a malformed/legacy row and stays visible.
            risks.append("出版社待核验")
    fee_modes = set(requirements.get("fee_modes", []))
    if fee_modes:
        _, supported = _fee_modes_for_journal(journal)
        if is_external:
            risks.append("费用模式由 AI 提供，投稿前需核对官网")
        elif not supported:
            risks.append("费用模式待核验")
        elif supported.intersection(fee_modes):
            reasons.append("费用模式符合偏好")
        else:
            risks.append("费用模式与当前偏好不一致")
    return reasons, risks


def _quality_and_preference_score(
    journal: dict[str, Any], requirements: dict[str, Any], oa_status: str, *, is_external: bool = False
) -> tuple[int, list[str], list[str]]:
    """Return the non-AI 20-point component and clear provenance-aware hints."""
    status, quartile = _known_quartile(journal)
    quality = {"Q1": 12, "Q2": 9, "Q3": 3, "Q4": 0}.get(quartile, 3) if status in {"verified", "manual"} else 2
    priority = {"必看": 4, "关注": 3, "扩展": 1}.get(str(journal.get("frontier_priority", "")), 0)
    target = requirements["quartile_target"]
    target_bonus = 0
    if target == "q1" and quartile == "Q1":
        target_bonus = 2
    elif target == "q1_q2" and quartile in {"Q1", "Q2"}:
        target_bonus = 2
    oa_score = 0
    risks: list[str] = []
    if requirements["oa_mode"] == "require":
        if oa_status == "yes":
            oa_score = 2
        elif oa_status == "no":
            risks.append("未满足“仅开放获取”要求")
        else:
            risks.append("开放获取状态未知，投稿前需确认")
    elif requirements["oa_mode"] == "prefer" and oa_status == "yes":
        oa_score = 2
    if target != "any" and quartile not in ({"Q1"} if target == "q1" else {"Q1", "Q2"}):
        risks.append("未满足偏好分区或分区尚未知")
    if requirements.get("jcr_quartiles") and status not in {"verified", "manual"}:
        risks.append("JCR 分区待核验")
    if requirements.get("cas_quartiles") and not _cas_quartile(journal):
        risks.append("中科院分区待核验")
    label = "分区未知"
    if quartile:
        label = ("EasyScholar 同步 " if "easyscholar" in str(journal.get("jcr", {}).get("source", "")).casefold() else "JCR ") + quartile
    reasons = [label]
    if priority:
        reasons.append(f"个人优先级：{str(journal.get('frontier_priority', ''))}")
    soft_reasons, soft_risks = _soft_requirement_notes(journal, requirements, is_external=is_external)
    reasons.extend(soft_reasons)
    risks.extend(soft_risks)
    return min(20, quality + priority + target_bonus + oa_score + len(soft_reasons)), reasons, risks


def _time_score(item: dict[str, Any], speed_priority: str) -> tuple[int, str]:
    if speed_priority == "standard":
        return 0, ""
    minimum = _clamp_score(item.get("estimated_decision_days_min", 0), 3650)
    maximum = _clamp_score(item.get("estimated_decision_days_max", 0), 3650)
    if not minimum and not maximum:
        return 0, "AI 未提供预计处理时间，不能计入时效附加分"
    if maximum and minimum and maximum < minimum:
        minimum, maximum = maximum, minimum
    days = maximum or minimum
    ceiling = 5 if speed_priority == "priority" else 10
    if days <= 45:
        return ceiling, f"预计首轮决定约 {minimum or days}–{maximum or days} 天"
    if days <= 90:
        return max(1, round(ceiling * 0.7)), f"预计首轮决定约 {minimum or days}–{maximum or days} 天"
    if days <= 150:
        return max(1, round(ceiling * 0.35)), f"预计首轮决定约 {minimum or days}–{maximum or days} 天"
    return 0, f"预计首轮决定约 {minimum or days}–{maximum or days} 天，时效优势较弱"


def _recommendation_by_id(recommendation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ranked = recommendation.get("ranked", []) if isinstance(recommendation, dict) else []
    return {
        str(item.get("id", "")): item
        for item in ranked
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    }


def _preliminary_fit(paper: dict[str, Any], journal: dict[str, Any], profile: dict[str, Any], history: dict[str, Any], requirements: dict[str, Any]) -> int:
    local = score_journal_candidate(paper, journal, profile, history, requirements)
    return _clamp_score(local.get("local_score", 0), 100)


def _candidate_from_ai_item(
    journal: dict[str, Any],
    ai_item: dict[str, Any] | None,
    paper: dict[str, Any],
    profile: dict[str, Any],
    history: dict[str, Any],
    requirements: dict[str, Any],
    *,
    is_external: bool = False,
) -> dict[str, Any]:
    ai_item = ai_item if isinstance(ai_item, dict) else {}
    is_ai_ranked = bool(ai_item)
    # AI-only candidates have no cached source record.  Keep their fit reason,
    # but do not read or promote AI hints about OA or review timing into facts.
    oa_status = (
        "unknown"
        if is_external
        else _oa_status(ai_item.get("oa_status", journal.get("oa_status", journal.get("open_access", ""))))
    )
    fit_score = _clamp_score(ai_item.get("fit_score", 0), 100) if is_ai_ranked else _preliminary_fit(
        paper, journal, profile, history, requirements
    )
    ai_score = _clamp_score(fit_score * 0.70, 70)
    verified_score, local_reasons, local_risks = _quality_and_preference_score(
        journal, requirements, oa_status, is_external=is_external
    )
    fee_mode, _ = _fee_modes_for_journal(journal)
    time_score, time_text = (0, "") if is_external else _time_score(ai_item, requirements["speed_priority"])
    ai_risk = str(ai_item.get("risk_cn", "")).strip()[:180]
    risks: list[str] = []
    for risk in [ai_risk, *local_risks]:
        if risk and risk not in risks:
            risks.append(risk)
    if time_text and time_score == 0:
        if time_text not in risks:
            risks.append(time_text)
    reason = str(ai_item.get("reason_cn", "")).strip()[:260]
    if not reason and not is_ai_ranked:
        reason = "尚未调用 AI；当前为本地预览排序，点击“AI 主推荐”可依据论文摘要重新排序。"
    if not str(paper.get("summary", "")).strip():
        empty_summary_risk = "论文摘要为空，AI 只能依据题目和关键词判断"
        if empty_summary_risk not in risks:
            risks.append(empty_summary_risk)
    return {
        "journal_id": str(journal.get("id", "")),
        "journal_name": str(journal.get("name", "")).strip() or "未命名期刊",
        "journal": deepcopy(journal),
        "source": "AI 扩展候选·待核验" if is_external else ("AI 主推荐" if is_ai_ranked else "本地预览"),
        "is_external": is_external,
        "ai_fit_score": fit_score,
        "ai_score": ai_score,
        "verified_score": verified_score,
        "time_score": time_score,
        "total_score": ai_score + verified_score + time_score,
        "ai_total_score": ai_score + verified_score + time_score,
        "oa_status": oa_status,
        "fee_mode": fee_mode,
        "fee_pending_verification": is_external or not bool(_fee_modes_for_journal(journal)[1]),
        "estimated_decision_days_min": 0 if is_external else _clamp_score(ai_item.get("estimated_decision_days_min", 0), 3650),
        "estimated_decision_days_max": 0 if is_external else _clamp_score(ai_item.get("estimated_decision_days_max", 0), 3650),
        "time_confidence": "" if is_external else str(ai_item.get("time_confidence", "")).strip().casefold(),
        "reason_cn": reason,
        "risk_cn": ai_risk or (risks[0] if risks else ""),
        "local_reasons": local_reasons,
        "risks": risks,
    }


def _external_journal_from_ai(item: dict[str, Any]) -> dict[str, Any] | None:
    name = str(item.get("name", "")).strip()
    if not name:
        return None
    publisher = str(item.get("publisher", "")).strip()
    stable_id = "external:" + canonical_text(name + "|" + publisher)[:160]
    fields = _terms(item.get("fields", item.get("keywords", [])))
    return {
        "id": stable_id,
        "name": name,
        "publisher": publisher,
        "issn": str(item.get("issn", "")).strip(),
        "website": str(item.get("website", item.get("url", ""))).strip(),
        "fields": fields,
        "ai_tags": _terms(item.get("keywords", [])),
        "frontier_priority": "不订阅",
        "fee_mode": str(item.get("fee_mode", item.get("fee_type", ""))).strip(),
        "oa_status": str(item.get("oa_status", "")).strip(),
        # AI-provided quartile hints intentionally never become a local JCR fact.
        "jcr": {"status": "pending", "source": "", "metrics": []},
        "metadata_dirty": True,
    }


def rank_ai_first_candidates(
    paper: dict[str, Any],
    journals: list[dict[str, Any]],
    profile: dict[str, Any] | None,
    history: dict[str, Any] | None,
    requirements: dict[str, Any] | None,
    *,
    recommendation: dict[str, Any] | None = None,
    verify_divisions: bool = True,
) -> list[dict[str, Any]]:
    """Rank AI recommendations on a transparent 70 / 20 / 10 score budget.

    Without an AI response, the same UI remains usable as a clearly labelled
    local preview.  Once a response arrives, AI fit is capped at 70 points;
    source-backed quality/preferences are at most 20 and optional speed at
    most 10.  AI-generated external journals are always marked unverified.
    """
    paper = paper if isinstance(paper, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    history = history if isinstance(history, dict) else {}
    requirements = normalize_selection_requirements(requirements)
    recommendation = recommendation if isinstance(recommendation, dict) else {}
    by_id = _recommendation_by_id(recommendation)
    result: list[dict[str, Any]] = []
    for journal in journals:
        if not isinstance(journal, dict) or not str(journal.get("id", "")).strip():
            continue
        if _publisher_requirement_reason(journal, requirements):
            continue
        if not _passes_new_requirements(journal, requirements, verify_divisions=verify_divisions):
            continue
        journal_history = _history_for_journal(journal, history)
        exclusion = _is_excluded_by_requirements(
            journal, requirements, journal_history, verify_divisions=verify_divisions
        )
        if not exclusion:
            name_key = canonical_journal_name(journal.get("name", ""))
            if name_key in _normalized_name_set(requirements.get("rejected_journal_names", [])):
                exclusion = "该论文曾被本刊拒稿"
            elif name_key in _normalized_name_set(requirements.get("searched_journal_names", [])):
                exclusion = "本轮已搜索过该期刊"
        if exclusion:
            continue
        item = by_id.get(str(journal.get("id", "")))
        oa_status = _oa_status(item.get("oa_status", "") if isinstance(item, dict) else journal.get("oa_status", ""))
        if requirements["oa_mode"] == "require" and oa_status == "no":
            continue
        result.append(_candidate_from_ai_item(journal, item, paper, profile, journal_history, requirements))

    external_items = recommendation.get("external_candidates", [])
    for raw in external_items if isinstance(external_items, list) else []:
        if not isinstance(raw, dict):
            continue
        journal = _external_journal_from_ai(raw)
        if journal is None:
            continue
        if _publisher_requirement_reason(journal, requirements):
            continue
        # A library-external candidate may still be shown.  If division
        # targets are active and verification is enabled, it remains pending
        # until EasyScholar supplies source-backed facts.
        if verify_divisions and any(requirements.get(key) for key in ("jcr_quartiles", "cas_quartiles")):
            continue
        result.append(_candidate_from_ai_item(journal, raw, paper, profile, {}, requirements, is_external=True))

    fee_order = {"hybrid": 0, "apc": 1, "subscription": 2, "unknown": 3}
    result.sort(
        key=lambda item: (
            -int(item["total_score"]),
            fee_order.get(str(item.get("fee_mode", "unknown")), 3),
            canonical_text(item["journal_name"]),
            bool(item["is_external"]),
        )
    )
    return result


def external_candidate_to_library_journal(candidate: dict[str, Any], *, today: str | None = None) -> dict[str, Any]:
    """Turn a chosen AI-only candidate into a normal local journal awaiting checks."""
    candidate = candidate if isinstance(candidate, dict) else {}
    raw = candidate.get("journal", {})
    raw = raw if isinstance(raw, dict) else {}
    name = str(raw.get("name", "")).strip()
    if not name:
        raise ValueError("期刊名称不能为空")
    note = str(candidate.get("reason_cn", "")).strip()
    today = str(today or date.today().isoformat())[:10]
    jcr = deepcopy(raw.get("jcr", {})) if isinstance(raw.get("jcr", {}), dict) else {"status": "pending", "source": "", "checked_at": "", "metrics": []}
    easyscholar = deepcopy(raw.get("easyscholar", {})) if isinstance(raw.get("easyscholar", {}), dict) else {}
    return {
        "id": uuid4().hex,
        "name": name,
        "publisher": str(raw.get("publisher", "")).strip(),
        "issn": str(raw.get("issn", "")).strip(),
        "website": str(raw.get("website", "")).strip(),
        "fields": _terms(raw.get("fields", [])),
        "ai_tags": _terms(raw.get("ai_tags", [])),
        "favorite": False,
        "frontier_priority": "不订阅",
        "jcr": jcr,
        "easyscholar": easyscholar,
        "fee_mode": str(raw.get("fee_mode", "")).strip(),
        "metadata_dirty": not bool(easyscholar),
        "ai_auto_pending": True,
        "notes": "来自 AI 选刊工作台，待核验。" + (f" 推荐理由：{note[:180]}" if note else ""),
        "provenance": {"kind": "ai_selection", "first_item_id": "", "source_url": "", "discovered_at": today},
        "created_at": today,
    }


def append_journal_to_submission_path(paper: dict[str, Any], journal: dict[str, Any], today: str | None = None) -> dict[str, Any]:
    """Append exactly one independent journal history entry to one paper."""
    result = deepcopy(paper if isinstance(paper, dict) else {})
    today = today or date.today().isoformat()
    journals = result.get("journals", [])
    journals = [deepcopy(item) for item in journals if isinstance(item, dict)] if isinstance(journals, list) else []
    name_key = canonical_text(journal.get("name", ""))
    publisher_key = canonical_text(journal.get("publisher", ""))
    if not name_key:
        raise ValueError("期刊名称不能为空")
    if any(canonical_text(item.get("name", "")) == name_key and canonical_text(item.get("publisher", "")) == publisher_key for item in journals):
        return result
    journal_id = uuid4().hex
    journals.append(
        {
            "id": journal_id,
            "name": str(journal.get("name", "")).strip(),
            "publisher": str(journal.get("publisher", "")).strip(),
            "date": today,
            "status_updated_at": today,
            "status": "准备投稿",
            "revision_due_date": "",
            "result": "",
            "notes": "从 AI 选刊工作台加入",
            "timeline": [{"id": uuid4().hex, "date": today, "status": "准备投稿", "note": "从选刊工作台加入"}],
        }
    )
    result["journals"] = journals
    return result


def special_issue_to_library_journal(issue: dict[str, Any], *, today: str | None = None) -> dict[str, Any]:
    """Create one conservative library record from a verified call."""
    issue = issue if isinstance(issue, dict) else {}
    name = str(issue.get("journal", "")).strip()
    if not name or name.casefold() == "unknown":
        raise ValueError("特刊缺少可核验的期刊名称")
    publisher = str(issue.get("publisher", "")).strip()
    if publisher.casefold() == "unknown":
        publisher = ""
    issns = issue.get("issns", []) if isinstance(issue.get("issns"), list) else []
    created = str(today or date.today().isoformat())[:10]
    return {
        "id": uuid4().hex,
        "name": name,
        "publisher": publisher,
        "issn": str(issns[0]).strip() if issns else str(issue.get("issn", "")).strip(),
        "website": str(issue.get("journal_homepage", issue.get("official_url", ""))).strip(),
        "fields": [],
        "ai_tags": [],
        "favorite": False,
        "frontier_priority": "不订阅",
        "jcr": deepcopy(issue.get("jcr", {})) if isinstance(issue.get("jcr"), dict) else {},
        "easyscholar": deepcopy(issue.get("easyscholar", {})) if isinstance(issue.get("easyscholar"), dict) else {},
        "fee_mode": str(issue.get("fee_mode", "unknown")).strip(),
        "metadata_dirty": True,
        "ai_auto_pending": False,
        "notes": f"从特刊征稿“{str(issue.get('title', '')).strip()}”快速入库。",
        "provenance": {
            "kind": "special_issue",
            "first_item_id": str(issue.get("id", "")).strip(),
            "source_url": str(issue.get("official_url", "")).strip(),
            "discovered_at": created,
        },
        "created_at": created,
    }


def append_special_issue_to_submission_path(
    paper: dict[str, Any],
    journal: dict[str, Any],
    issue: dict[str, Any],
    *,
    today: str | None = None,
) -> dict[str, Any]:
    """Append one issue-specific candidate while retaining its deadline link."""
    result = deepcopy(paper if isinstance(paper, dict) else {})
    journals = [deepcopy(value) for value in result.get("journals", []) if isinstance(value, dict)]
    issue_id = str(issue.get("id", "")).strip()
    if not issue_id:
        raise ValueError("特刊 ID 不能为空")
    if any(str(value.get("special_issue_id", "")).strip() == issue_id for value in journals):
        return result
    day = str(today or date.today().isoformat())[:10]
    path_id = uuid4().hex
    title = str(issue.get("title", "")).strip()
    deadline = str(issue.get("deadline", "")).strip()[:10]
    journals.append(
        {
            "id": path_id,
            "name": str(journal.get("name", "")).strip(),
            "publisher": str(journal.get("publisher", "")).strip(),
            "date": day,
            "status_updated_at": day,
            "status": "准备投稿",
            "revision_due_date": "",
            "result": "",
            "notes": f"特刊候选：{title}；截止日期：{deadline}",
            "special_issue_id": issue_id,
            "special_issue_title": title,
            "special_issue_deadline": deadline,
            "special_issue_url": str(issue.get("official_url", "")).strip(),
            "timeline": [{"id": uuid4().hex, "date": day, "status": "准备投稿", "note": f"加入特刊投稿路径：{title}"}],
        }
    )
    result["journals"] = journals
    return result
def canonical_journal_name(value: Any) -> str:
    """Normalize harmless journal-title variants used by archives and APIs."""
    text = canonical_text(value).replace("&", " and ")
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())

