"""Pure, explainable v11 daily-frontier scoring.

Network retrieval lives in ``frontier_service``.  This module contains the
single source of truth for admission, quality filtering, scoring and ordering.
"""

from __future__ import annotations

import hashlib
import json
import re
from math import ceil
from datetime import date
from typing import Any

from utils.journal_quality import is_default_frontier_eligible, journal_quality_snapshot


PRIORITY_BONUS = {"必看": 50, "关注": 35, "扩展": 15, "不订阅": 0}
QUALITY_BONUS = {"Q1": 50, "Q2": 20, "Q3": 0, "Q4": 0}
LOW_QUALITY_FLAGS = {"low", "q3_q4", "三四区", "低质量"}
LEGACY_JOURNAL_SCORES = {"必看": 100, "关注": 80, "扩展": 60, "不订阅": 50}


def canonical_text(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def frontier_ranking_settings(profile: dict[str, Any] | None) -> dict[str, int]:
    """Return the two compact ranking controls with bounded defaults."""
    profile = profile if isinstance(profile, dict) else {}
    raw = profile.get("frontier_ranking", {})
    raw = raw if isinstance(raw, dict) else {}
    try:
        journal_weight = int(raw.get("journal_weight", 25))
    except (TypeError, ValueError):
        journal_weight = 25
    try:
        default_score = int(raw.get("default_journal_score", 50))
    except (TypeError, ValueError):
        default_score = 50
    return {
        "journal_weight": max(0, min(50, journal_weight)),
        "default_journal_score": max(0, min(100, default_score)),
    }


def journal_preference_score(journal: dict[str, Any] | None, *, default_score: int = 50) -> int:
    """Read an explicit score or migrate the former four-level priority in memory."""
    journal = journal if isinstance(journal, dict) else {}
    raw = journal.get("frontier_score")
    if raw is not None and raw != "":
        try:
            return max(0, min(100, int(raw)))
        except (TypeError, ValueError):
            pass
    priority = str(journal.get("frontier_priority", "")).strip()
    return LEGACY_JOURNAL_SCORES.get(priority, max(0, min(100, int(default_score))))


def _issn_keys(value: object) -> set[str]:
    values = value if isinstance(value, list) else [value]
    return {
        re.sub(r"[^0-9X]", "", str(entry).upper())
        for entry in values
        if len(re.sub(r"[^0-9X]", "", str(entry).upper())) == 8
    }


def _journal_for_frontier_item(item: dict[str, Any], journals: list[dict[str, Any]]) -> dict[str, Any] | None:
    library_id = str(item.get("library_journal_id", "")).strip()
    if library_id:
        found = next((row for row in journals if str(row.get("id", "")).strip() == library_id), None)
        if found is not None:
            return found
    item_issns = _issn_keys(item.get("issn", item.get("issns", [])))
    if item_issns:
        found = next((row for row in journals if item_issns & _issn_keys(row.get("issn", row.get("issns", [])))), None)
        if found is not None:
            return found
    name = canonical_text(item.get("journal", ""))
    return next((row for row in journals if name and canonical_text(row.get("name", "")) == name), None)


def apply_frontier_ranking(
    items: list[dict[str, Any]],
    profile: dict[str, Any] | None,
    journals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compose content and personal journal scores without weakening admission gates."""
    settings = frontier_ranking_settings(profile)
    journal_weight = settings["journal_weight"]
    content_weight = 100 - journal_weight
    result: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        try:
            ai_score = int(item.get("ai_score", -1))
        except (TypeError, ValueError):
            ai_score = -1
        try:
            stored_score = int(item.get("score", 0))
        except (TypeError, ValueError):
            stored_score = 0
        content_score = max(0, min(100, ai_score if ai_score >= 0 else stored_score))
        try:
            relation_weight = max(0.0, min(1.0, float(item.get("ranking_weight", 1) or 1)))
        except (TypeError, ValueError):
            relation_weight = 1.0
        journal_name = canonical_text(item.get("journal", ""))
        is_preprint = bool(item.get("is_preprint")) or str(item.get("quality_gate_state", "")) == "preprint" or "arxiv" in journal_name
        journal = None if is_preprint else _journal_for_frontier_item(item, journals)
        if is_preprint:
            score = int(round(content_score * relation_weight))
            journal_score: int | None = None
            journal_source = "not_applicable"
        else:
            journal_score = journal_preference_score(
                journal,
                default_score=settings["default_journal_score"],
            )
            if journal and journal.get("frontier_score") is not None and journal.get("frontier_score") != "":
                journal_source = "explicit"
            elif journal:
                journal_source = "legacy_priority"
            else:
                journal_source = "profile_default"
            composite = (content_score * content_weight + journal_score * journal_weight) / 100
            score = int(round(composite * relation_weight))
        item.update(
            {
                "content_score": content_score,
                "journal_score": journal_score,
                "journal_score_source": journal_source,
                "score": score,
                "ranking_breakdown": {
                    "content_score": content_score,
                    "content_weight": content_weight,
                    "journal_score": journal_score,
                    "journal_weight": 0 if is_preprint else journal_weight,
                    "relation_weight": relation_weight,
                    "total": score,
                },
            }
        )
        result.append(item)
    return result


def _term_aliases(term: str) -> set[str]:
    value = canonical_text(term)
    aliases = {value}
    acronym = "".join(part[0] for part in re.findall(r"[a-z]+", value) if part)
    if len(acronym) >= 2:
        aliases.add(acronym)
    known = {
        "soil organic carbon": "soc",
        "mineral-associated organic carbon": "maoc",
        "particulate organic carbon": "poc",
    }
    if value in known:
        aliases.add(known[value])
    return {alias for alias in aliases if alias}


def _contains_term(text: str, term: str) -> bool:
    source = canonical_text(text)
    for alias in _term_aliases(term):
        if len(alias) <= 4 and alias.isascii() and alias.isalpha():
            if re.search(rf"(?<![a-z]){re.escape(alias)}(?![a-z])", source):
                return True
        elif alias in source:
            return True
    return False


def _profile_terms(profile: dict[str, Any]) -> list[dict[str, Any]]:
    terms = profile.get("terms", []) if isinstance(profile.get("terms", []), list) else []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in terms:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text", "")).strip()
        key = canonical_text(text)
        if not text or not key or key in seen:
            continue
        seen.add(key)
        try:
            weight = int(raw.get("weight", 1))
        except (TypeError, ValueError):
            weight = 1
        result.append({"id": str(raw.get("id", "")), "text": text, "weight": max(1, min(100, weight)), "locked": bool(raw.get("locked", False))})
    if result:
        return result
    # Read-only fallback while old frontier.json records are being migrated.
    for field, weight in (("primary_keywords", 70), ("secondary_keywords", 45)):
        values = profile.get(field, [])
        values = values if isinstance(values, list) else []
        for raw in values:
            text = str(raw).strip()
            key = canonical_text(text)
            if text and key not in seen:
                seen.add(key)
                result.append({"id": "legacy-" + key, "text": text, "weight": weight, "locked": False})
    return result


def primary_jcr_quartile(journal: dict[str, Any] | None) -> tuple[str, str]:
    snapshot = journal_quality_snapshot(journal)
    return str(snapshot["jcr_status"]), str(snapshot["jcr_quartile"])


def jcr_state_label(journal: dict[str, Any] | None) -> str:
    status, quartile = primary_jcr_quartile(journal)
    journal = journal if isinstance(journal, dict) else {}
    jcr = journal.get("jcr", {})
    jcr = jcr if isinstance(jcr, dict) else {}
    source = str(jcr.get("source", "")).casefold()
    if status == "verified" and quartile and "easyscholar" in source:
        return f"EasyScholar 同步 {quartile}"
    if status in {"verified", "manual"} and quartile:
        return ("已核验 " if status == "verified" else "手动 ") + quartile
    if status == "ai_estimated" and quartile:
        return f"AI 估计 {quartile}·待核验"
    return "分区未知"


def classify_feedback_locally(text: object) -> dict[str, Any]:
    value = str(text or "").strip()
    folded = canonical_text(value)
    if any(token in folded for token in ("三四区", "3区", "4区", "q3", "q4", "低质量", "分区低")):
        return {"kind": "journal_quality", "term_weight_delta": 0, "quality_flag": "low", "feedback_adjustment": -30}
    if any(token in folded for token in ("不相关", "偏题", "无关", "不适合")):
        return {"kind": "topic_negative", "term_weight_delta": -1, "quality_flag": "", "feedback_adjustment": -15}
    if any(token in folded for token in ("很相关", "有用", "值得", "启发", "喜欢")):
        return {"kind": "topic_positive", "term_weight_delta": 1, "quality_flag": "", "feedback_adjustment": 15}
    if any(token in folded for token in ("方法", "遥感", "机器学习", "模型", "尺度")):
        return {"kind": "method_or_object_preference", "term_weight_delta": 0, "quality_flag": "", "feedback_adjustment": 5}
    return {"kind": "reading_value", "term_weight_delta": 0, "quality_flag": "", "feedback_adjustment": 0}


def _feedback_adjustment(item: dict[str, Any], journal: dict[str, Any], feedback_index: dict[str, Any]) -> int:
    candidate = item.get("feedback_adjustment", 0)
    if isinstance(feedback_index, dict):
        journal_key = canonical_text(journal.get("name", item.get("journal", "")))
        entry = feedback_index.get(journal_key, feedback_index.get(str(item.get("id", "")), {}))
        if isinstance(entry, dict):
            candidate = entry.get("feedback_adjustment", entry.get("score", candidate))
        elif isinstance(entry, (int, float)):
            candidate = entry
    try:
        return max(-30, min(30, int(candidate)))
    except (TypeError, ValueError):
        return 0


def _ai_adjustment(item: dict[str, Any]) -> int:
    candidate = item.get("ai_adjustment", item.get("ai_score_adjustment", 0))
    try:
        return max(-15, min(15, int(candidate)))
    except (TypeError, ValueError):
        return 0


def evaluate_frontier_candidate(
    item: dict[str, Any],
    profile: dict[str, Any],
    journal: dict[str, Any] | None,
    feedback_index: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Admit a candidate only with two distinct profile terms and score it."""
    item = item if isinstance(item, dict) else {}
    profile = profile if isinstance(profile, dict) else {}
    journal = journal if isinstance(journal, dict) else {}
    feedback_index = feedback_index if isinstance(feedback_index, dict) else {}
    author_keywords = item.get("author_keywords", [])
    author_keywords = author_keywords if isinstance(author_keywords, list) else []
    keyword_text = "\n".join(str(value).strip() for value in author_keywords if str(value).strip())
    fallback_text = "\n".join((str(item.get("title", "")), str(item.get("abstract", ""))))
    match_text = keyword_text or fallback_text
    context_text = "\n".join((fallback_text, keyword_text))

    excluded = {canonical_text(value) for value in profile.get("excluded_terms", []) if str(value).strip()} if isinstance(profile.get("excluded_terms", []), list) else set()
    matched: list[dict[str, Any]] = []
    for term in _profile_terms(profile):
        if canonical_text(term["text"]) in excluded:
            continue
        if _contains_term(match_text, term["text"]):
            matched.append(term)
    if len(matched) < 2:
        return None

    jcr_status, quartile = primary_jcr_quartile(journal)
    quality_snapshot = journal_quality_snapshot(journal)
    easyscholar_configured = bool(profile.get("_easyscholar_configured", False))
    quality_eligible, quality_reason = is_default_frontier_eligible(
        quality_snapshot,
        easyscholar_configured=easyscholar_configured,
    )
    if bool(profile.get("require_verified_jcr_q1_q2", False)) and quality_reason != "verified_q1_q2":
        return None
    known_low_quartile = jcr_status in {"verified", "manual"} and quartile in {"Q3", "Q4"}
    journal_quality_flag = canonical_text(journal.get("user_quality_flag", ""))
    if (easyscholar_configured and not quality_eligible) or (bool(profile.get("filter_known_q3_q4", True)) and (known_low_quartile or journal_quality_flag in LOW_QUALITY_FLAGS)):
        return None

    priority = str(journal.get("frontier_priority", item.get("priority", "不订阅"))).strip()
    if priority not in PRIORITY_BONUS:
        priority = "不订阅"
    term_score = sum(int(term["weight"]) for term in matched)
    priority_score = PRIORITY_BONUS[priority]
    quality_score = QUALITY_BONUS.get(quartile, 0) if jcr_status in {"verified", "manual"} else 0
    feedback_score = _feedback_adjustment(item, journal, feedback_index)
    ai_score = _ai_adjustment(item)
    breakdown = {"terms": term_score, "priority": priority_score, "quality": quality_score, "feedback": feedback_score, "ai": ai_score}
    total = sum(breakdown.values())
    names = [str(term["text"]) for term in matched]
    reasons = ["命中 " + "、".join(names[:3])]
    if priority_score:
        reasons.append(priority)
    if quality_score:
        reasons.append(jcr_state_label(journal))
    elif jcr_state_label(journal) != "分区未知":
        reasons.append(jcr_state_label(journal))
    return {
        "score": total,
        "score_breakdown": breakdown,
        "matched_term_ids": [str(term["id"]) for term in matched],
        "matched_terms": names,
        "match_source": "source_keywords" if keyword_text else "title_abstract",
        "priority": priority,
        "jcr_quartile": quartile,
        "jcr_state": jcr_state_label(journal),
        "jcr_status": jcr_status,
        "cas_upgrade": quality_snapshot["cas_upgrade"],
        "journal_metric_line": quality_snapshot["metric_line"],
        "quality_gate_state": "eligible" if quality_eligible else "blocked",
        "quality_gate_reason": quality_reason,
        "journal_quality_flag": str(journal.get("user_quality_flag", "")).strip(),
        "recommendation_reason": "；".join(reasons),
    }


def select_daily_recommendations_v11(items: list[dict[str, Any]], profile: dict[str, Any], today: date | None = None) -> list[dict[str, Any]]:
    today_key = (today or date.today()).isoformat()
    easyscholar_configured = bool(profile.get("_easyscholar_configured", False))
    strict_q1_q2 = bool(profile.get("require_verified_jcr_q1_q2", False))
    visible = [
        item
        for item in items
        if isinstance(item, dict)
        and str(item.get("status", "new")) == "new"
        and str(item.get("recommendation_date", "")) in {"", today_key}
        and not bool(item.get("filtered_by_quality", False))
        and not (
            strict_q1_q2
            and (
                str(item.get("quality_gate_state", "")).strip() != "eligible"
                or str(item.get("quality_gate_reason", "")).strip() != "verified_q1_q2"
            )
        )
        and not (
            easyscholar_configured
            and (
                str(item.get("quality_gate_state", "")).strip() != "eligible"
                or str(item.get("quality_gate_reason", "")).strip() != "verified_q1_q2"
            )
        )
    ]
    visible.sort(key=lambda item: (int(item.get("score", 0) or 0), str(item.get("published_date", "")), str(item.get("id", ""))), reverse=True)
    try:
        limit = max(1, min(12, int(profile.get("daily_limit", 5))))
    except (TypeError, ValueError):
        limit = 5
    return visible[:limit]


def frontier_item_key(item: dict[str, Any]) -> str:
    doi = str(item.get("doi", "")).strip().casefold()
    if doi:
        return "doi:" + doi.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
    packed = "|".join((canonical_text(item.get("title", "")), canonical_text(item.get("journal", "")), str(item.get("published_date", ""))[:4]))
    return "title:" + hashlib.sha1(packed.encode("utf-8")).hexdigest()[:20]


def deduplicate_frontier_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = frontier_item_key(item)
        previous = result.get(key)
        if previous is None or int(item.get("score", 0) or 0) > int(previous.get("score", 0) or 0):
            result[key] = item
    return list(result.values())


def profile_query_terms_v11(profile: dict[str, Any], limit: int = 6) -> list[str]:
    profile = profile if isinstance(profile, dict) else {}
    terms = sorted(_profile_terms(profile), key=lambda item: (-int(item["weight"]), canonical_text(item["text"])))
    values = [str(item["text"]) for item in terms]
    values.extend(str(item).strip() for item in profile.get("ai_search_terms", []) if str(item).strip())
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = canonical_text(value)
        if key and key not in seen:
            seen.add(key)
            result.append(value)
        if len(result) >= limit:
            break
    return result


def frontier_profile_signature_v11(profile: dict[str, Any], journals: list[dict[str, Any]]) -> str:
    profile = profile if isinstance(profile, dict) else {}
    payload = {
        "terms": [{"text": canonical_text(item["text"]), "weight": int(item["weight"]), "locked": bool(item["locked"])} for item in _profile_terms(profile)],
        "excluded": sorted(canonical_text(value) for value in profile.get("excluded_terms", []) if str(value).strip()),
        "filter_known_q3_q4": bool(profile.get("filter_known_q3_q4", True)),
        "frontier_ranking": frontier_ranking_settings(profile),
        "sources": profile.get("sources", {}),
        "journals": [
            {
                "name": canonical_text(item.get("name", "")),
                "priority": str(item.get("frontier_priority", "不订阅")),
                "jcr": item.get("jcr", {}),
                "quality": str(item.get("user_quality_flag", "")),
                "frontier_score": item.get("frontier_score"),
            }
            for item in journals
            if isinstance(item, dict)
        ],
        "algorithm": 11,
    }
    packed = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()[:16]


def select_daily_mix(
    items: list[dict[str, Any]],
    *,
    limit: int,
    minimum_core_ratio: float = 0.5,
) -> list[dict[str, Any]]:
    """Select a scored feed while reserving at least half for core matches."""
    try:
        bounded_limit = max(1, min(50, int(limit)))
    except (TypeError, ValueError):
        bounded_limit = 5
    try:
        ratio = max(0.0, min(1.0, float(minimum_core_ratio)))
    except (TypeError, ValueError):
        ratio = 0.5
    valid = [dict(item) for item in items if isinstance(item, dict) and str(item.get("title", "")).strip()]
    if any(item.get("admission_version") for item in valid):
        valid = [item for item in valid if item.get("content_decision") == "accept"]

    def order(item: dict[str, Any]) -> tuple[int, str, str]:
        return (
            int(item.get("score", item.get("ai_score", 0)) or 0),
            str(item.get("published_date", "")),
            str(item.get("id", item.get("doi", ""))),
        )

    valid.sort(key=order, reverse=True)
    core = [item for item in valid if str(item.get("recommendation_kind", "core_keyword")) == "core_keyword"]
    if core and ratio > 0 and any(item.get("admission_version") for item in valid):
        bounded_limit = min(bounded_limit, int(len(core) / ratio))
    target = min(len(core), ceil(bounded_limit * ratio))
    selected = core[:target]
    selected_ids = {id(item) for item in selected}
    remaining = [item for item in valid if id(item) not in selected_ids]
    selected.extend(remaining[: max(0, bounded_limit - len(selected))])
    selected.sort(key=order, reverse=True)
    return selected[:bounded_limit]
