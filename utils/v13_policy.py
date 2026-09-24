"""Version 13 policy primitives shared by discovery, scoring and the UI.

This module deliberately contains no network or Qt code.  It is the single
place where the v13 rules that must not silently diverge are enforced:

* facts, preferences and unknown values are different states;
* every recommendation uses two 66-point local axes plus an optional
  34-point AI contribution;
* AI failure is ``None`` rather than a zero score;
* display admission and candidate retention are separate decisions;
* pyramid groups are dynamic and never imply a quota.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import hashlib
import re
import unicodedata
from typing import Any, Iterable
from urllib.parse import urlsplit


POLICY_VERSION = "13.0"
FRONTIER_SCORE_VERSION = "frontier-dual-axis-13.0"
SPECIAL_ISSUE_SCORE_VERSION = "special-issue-dual-axis-13.0"
JOURNAL_SELECTION_SCORE_VERSION = "journal-selection-dual-axis-13.0"

FRONTIER_RELEVANCE_WEIGHTS = {
    "topic_object": 22,
    "project_question": 16,
    "method_data_scale": 12,
    "knowledge_similarity": 10,
    "personal_signal": 6,
}
FRONTIER_VALUE_WEIGHTS = {
    "journal_priority_quality": 15,
    "scientific_importance": 14,
    "innovation": 14,
    "evidence_completeness": 8,
    "freshness": 8,
    "multi_channel_signal": 7,
}
SPECIAL_RELEVANCE_WEIGHTS = {
    "topic": 24,
    "project_paper": 18,
    "method_data_scale": 10,
    "journal_field": 8,
    "personal_signal": 6,
}
SPECIAL_OPPORTUNITY_WEIGHTS = {
    "journal_publisher_quality": 15,
    "frontier_value": 15,
    "scope_article_fit": 12,
    "call_clarity": 8,
    "deadline": 7,
    "editors": 5,
    "actionability": 4,
}
SELECTION_FIT_WEIGHTS = {
    "topic_scope": 22,
    "object_field": 10,
    "method": 10,
    "data_scale": 8,
    "article_type": 8,
    "similar_papers": 8,
}
SELECTION_STRATEGY_WEIGHTS = {
    "contribution_threshold": 18,
    "quality_target": 15,
    "submission_goal": 10,
    "review_cycle": 8,
    "fee_oa": 7,
    "activity_continuity": 5,
    "requirements_feasibility": 3,
}

AI_MAX = 34
BASE_MAX = 66

PREPRINT_HOSTS = {
    "arxiv.org": "arXiv",
    "biorxiv.org": "bioRxiv",
    "medrxiv.org": "medRxiv",
    "researchsquare.com": "Research Square",
    "egusphere.copernicus.org": "EGUsphere",
}
FACTUAL_EXCLUSION_FLAGS = {
    "retracted",
    "confirmed_retracted",
    "junk",
    "confirmed_junk",
    "permanently_excluded",
    "duplicate",
}


def canonical_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).split())


def _clamp(value: Any, minimum: float, maximum: float, fallback: float = 0.0) -> float:
    try:
        return max(minimum, min(maximum, float(value)))
    except (TypeError, ValueError):
        return fallback


def _unique_strings(values: Any, limit: int = 100) -> list[str]:
    source = values if isinstance(values, (list, tuple, set)) else [values]
    result: list[str] = []
    seen: set[str] = set()
    for raw in source:
        value = str(raw or "").strip()
        key = canonical_text(value)
        if value and key and key not in seen:
            seen.add(key)
            result.append(value)
        if len(result) >= limit:
            break
    return result


def compose_axis(
    components: dict[str, Any],
    weights: dict[str, int],
    *,
    ai_adjustment: Any = None,
    reasons: Iterable[str] = (),
    unknown_fields: Iterable[str] = (),
) -> dict[str, Any]:
    """Compose one v13 axis while preserving unknowns and AI failure.

    Component values are expressed on the component's own maximum.  ``None``
    means unknown and is excluded from the evidence coverage denominator; a
    real numeric zero stays zero.  The public base score always remains out of
    66 so explanations are stable across modules.
    """

    clean: dict[str, int | None] = {}
    known_weight = 0
    base = 0
    for name, maximum in weights.items():
        raw = components.get(name)
        if raw is None:
            clean[name] = None
            continue
        value = int(round(_clamp(raw, 0, maximum)))
        clean[name] = value
        base += value
        known_weight += maximum
    raw_base = max(0, min(BASE_MAX, base))
    coverage = round(known_weight / BASE_MAX, 4)
    # Unknown evidence is not a factual zero.  First project the score from
    # the dimensions that are actually known back onto the 66-point rule
    # scale, while coverage separately tells the UI how provisional it is.
    base = int(round(raw_base / known_weight * BASE_MAX)) if known_weight else 0
    base = max(0, min(BASE_MAX, base))
    ai: int | None
    if ai_adjustment is None:
        ai = None
        total = int(round(base / BASE_MAX * 100))
        mode = "rules_normalized"
    else:
        try:
            ai = int(round(float(ai_adjustment)))
        except (TypeError, ValueError):
            ai = None
        if ai is None or not 0 <= ai <= AI_MAX:
            ai = None
            total = int(round(base / BASE_MAX * 100))
            mode = "rules_normalized"
        else:
            total = base + ai
            mode = "rules_plus_ai"
    return {
        "base_score": base,
        "raw_base_score": raw_base,
        "known_base_max": known_weight,
        "base_max": BASE_MAX,
        "ai_adjustment": ai,
        "ai_max": AI_MAX,
        "total": max(0, min(100, total)),
        "coverage": coverage,
        "components": clean,
        "mode": mode,
        "valid": bool(known_weight and raw_base > 0),
        "reasons": _unique_strings(list(reasons), 12),
        "unknown_fields": _unique_strings(list(unknown_fields), 24),
    }


def validate_ai_axes(payload: Any, *, evidence_fields: Iterable[str]) -> dict[str, Any]:
    """Validate a structured two-axis AI response without accepting facts.

    Low-confidence, malformed or evidence-free axes are discarded separately.
    The caller can therefore fall back to its 66-point rule score per axis.
    """

    raw = payload if isinstance(payload, dict) else {}
    allowed = {str(value).strip() for value in evidence_fields if str(value).strip()}
    result: dict[str, Any] = {"provider": str(raw.get("provider", "")).strip(), "model": str(raw.get("model", "")).strip()}
    axes = raw.get("axes", raw)
    axes = axes if isinstance(axes, dict) else {}
    for name in ("relevance", "value", "opportunity", "fit", "strategy"):
        value = axes.get(name)
        if not isinstance(value, dict):
            continue
        confidence = str(value.get("confidence", "")).strip().casefold()
        refs = _unique_strings(value.get("evidence_refs", []), 12)
        try:
            adjustment = int(round(float(value.get("adjustment"))))
        except (TypeError, ValueError):
            adjustment = -1
        valid_refs = bool(refs) and all(ref in allowed for ref in refs)
        valid = confidence in {"medium", "high"} and 0 <= adjustment <= AI_MAX and valid_refs
        result[name] = {
            "adjustment": adjustment if valid else None,
            "confidence": confidence if confidence in {"low", "medium", "high"} else "invalid",
            "reason": str(value.get("reason", value.get("reason_cn", ""))).strip()[:500],
            "evidence_refs": refs if valid_refs else [],
            "valid": valid,
        }
    return result


def normalized_doi(value: Any) -> str:
    text = str(value or "").strip().casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text.strip().rstrip(".,;)")


def _preprint_id(item: dict[str, Any]) -> str:
    explicit = str(item.get("preprint_id", "")).strip().casefold()
    if explicit:
        return explicit.removeprefix("arxiv:")
    for value in (item.get("source_id"), item.get("url"), item.get("doi")):
        text = str(value or "").strip()
        match = re.search(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:)(\d{4}\.\d{4,5})(?:v\d+)?", text, re.I)
        if match:
            return "arxiv:" + match.group(1).casefold()
        match = re.search(r"10\.1101/(\d{4}\.\d{2}\.\d{2}\.\d+)", text, re.I)
        if match:
            return "biorxiv:" + match.group(1).casefold()
    return ""


def work_fingerprint(item: dict[str, Any]) -> str:
    """Return the strongest stable fingerprint available for a work."""

    doi = normalized_doi(item.get("doi"))
    if doi:
        return "doi:" + doi
    preprint = _preprint_id(item)
    if preprint:
        return "preprint:" + preprint
    source = canonical_text(item.get("source"))
    source_id = canonical_text(item.get("source_id"))
    if source and source_id:
        return f"source:{source}:{source_id}"
    title = canonical_text(item.get("title"))
    authors = _unique_strings(item.get("authors", []), 3)
    first_author = canonical_text(authors[0]) if authors else ""
    year_match = re.search(r"(?:19|20)\d{2}", str(item.get("published_date", item.get("year", ""))))
    year = year_match.group(0) if year_match else ""
    payload = "|".join((title, first_author, year))
    return "work:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32] if title else ""


def _work_family_hint(item: dict[str, Any]) -> str:
    title = canonical_text(item.get("title"))
    authors = _unique_strings(item.get("authors", []), 2)
    payload = "|".join((title, canonical_text(authors[0]) if authors else ""))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24] if title else ""


def merge_work_families(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge exact identities and group preprint/formal versions defensively."""

    merged: dict[str, dict[str, Any]] = {}
    family_positions: dict[str, str] = {}
    order: list[str] = []
    for raw in items:
        if not isinstance(raw, dict) or not str(raw.get("title", "")).strip():
            continue
        item = deepcopy(raw)
        fingerprint = work_fingerprint(item)
        if not fingerprint:
            continue
        family_hint = _work_family_hint(item)
        key = fingerprint
        # A title+author family can join a preprint and a version of record,
        # but never two unrelated journal records with conflicting DOIs.
        prior_key = family_positions.get(family_hint)
        if prior_key and prior_key in merged:
            prior = merged[prior_key]
            prior_doi, item_doi = normalized_doi(prior.get("doi")), normalized_doi(item.get("doi"))
            if bool(prior.get("is_preprint")) != bool(item.get("is_preprint")) or not (prior_doi and item_doi):
                key = prior_key
        if key not in merged:
            item["fingerprint"] = fingerprint
            item["work_family_id"] = "family:" + (family_hint or fingerprint.split(":", 1)[-1])
            item["source_names"] = _unique_strings([*item.get("source_names", []), item.get("source", "")], 20)
            item["recall_strategies"] = _unique_strings(
                [*item.get("recall_strategies", []), item.get("recall_strategy", item.get("discovery_lane", ""))], 12
            )
            item["source_evidence"] = [deepcopy(value) for value in item.get("source_evidence", []) if isinstance(value, dict)]
            merged[key] = item
            order.append(key)
            if family_hint:
                family_positions[family_hint] = key
            continue
        current = merged[key]
        current_preprint = bool(current.get("is_preprint"))
        incoming_preprint = bool(item.get("is_preprint"))
        prefer_incoming = current_preprint and not incoming_preprint
        preferred, other = (item, current) if prefer_incoming else (current, item)
        combined = deepcopy(preferred)
        for field in ("abstract", "authors", "journal", "publisher", "url", "published_date", "doi", "issn", "author_keywords"):
            preferred_value = combined.get(field)
            other_value = other.get(field)
            if not preferred_value or len(str(preferred_value)) < len(str(other_value or "")):
                if other_value:
                    combined[field] = deepcopy(other_value)
        combined["fingerprint"] = work_fingerprint(combined) or current.get("fingerprint", fingerprint)
        combined["work_family_id"] = current.get("work_family_id", "family:" + family_hint)
        combined["source_names"] = _unique_strings(
            [*current.get("source_names", []), *item.get("source_names", []), current.get("source", ""), item.get("source", "")], 20
        )
        combined["recall_strategies"] = _unique_strings(
            [*current.get("recall_strategies", []), *item.get("recall_strategies", []), item.get("recall_strategy", item.get("discovery_lane", ""))], 12
        )
        evidence: list[dict[str, Any]] = []
        seen: set[str] = set()
        for value in [*current.get("source_evidence", []), *item.get("source_evidence", [])]:
            if not isinstance(value, dict):
                continue
            identity = repr(sorted(value.items()))
            if identity not in seen:
                seen.add(identity)
                evidence.append(deepcopy(value))
        combined["source_evidence"] = evidence
        if current_preprint != incoming_preprint:
            combined["version_update"] = "formal_publication" if not bool(combined.get("is_preprint")) else "preprint"
            combined["version_update_pending_once"] = True
        merged[key] = combined
    return [merged[key] for key in order]


def _profile_terms(profile: dict[str, Any]) -> tuple[list[str], list[str], list[str]]:
    topic: list[str] = []
    methods: list[str] = []
    locked: list[str] = []
    method_markers = {
        "machine learning", "deep learning", "random forest", "xgboost", "remote sensing",
        "model", "mapping", "spatial", "dataset", "data", "scale", "shap", "regression",
    }
    for raw in profile.get("terms", []) if isinstance(profile.get("terms"), list) else []:
        if not isinstance(raw, dict) or str(raw.get("status", "active")) != "active":
            continue
        value = str(raw.get("canonical_en", raw.get("text", ""))).strip()
        if not value:
            continue
        target = methods if any(marker in canonical_text(value) for marker in method_markers) else topic
        target.append(value)
        if raw.get("locked"):
            locked.append(value)
    if not topic and not methods:
        topic = _unique_strings(profile.get("primary_keywords", []), 30)
        methods = _unique_strings(profile.get("secondary_keywords", []), 30)
    return topic, methods, locked


def _matched_terms(text: str, terms: Iterable[str]) -> list[str]:
    haystack = " " + canonical_text(text) + " "
    found: list[str] = []
    for term in terms:
        needle = canonical_text(term)
        if not needle:
            continue
        if len(needle) <= 5 and needle.isascii():
            matched = bool(re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack))
        else:
            matched = needle in haystack
        if matched:
            found.append(str(term))
    return _unique_strings(found, 40)


def _active_projects(profile: dict[str, Any]) -> list[dict[str, Any]]:
    projects = profile.get("projects", []) if isinstance(profile.get("projects"), list) else []
    result = []
    for value in projects:
        if not isinstance(value, dict):
            continue
        status = str(value.get("status", "active")).strip().casefold()
        if status not in {"primary", "active", "in_progress", "进行中", "主要"}:
            continue
        result.append(value)
    return result


def _project_matches(text: str, profile: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for project in _active_projects(profile):
        material = " ".join(
            str(project.get(field, ""))
            for field in ("name", "title", "scientific_question", "summary", "keywords", "methods", "objects")
        )
        tokens = [token for token in canonical_text(material).split() if len(token) >= 4]
        hits = sum(token in canonical_text(text) for token in set(tokens))
        if hits:
            result.append({"id": str(project.get("id", "")), "name": str(project.get("name", project.get("title", ""))), "hits": hits, "primary": str(project.get("status", "")).casefold() in {"primary", "主要"}})
    return result


def _quartile(item: dict[str, Any], journal: dict[str, Any] | None = None) -> tuple[str, str]:
    sources = [item, journal or {}]
    for source in sources:
        jcr = source.get("jcr", {}) if isinstance(source.get("jcr"), dict) else {}
        status = str(
            source.get("jcr_status", source.get("jcr_state", ""))
            or jcr.get("status", "")
        ).strip().casefold()
        values = [source.get("jcr_quartile", "")]
        values.extend(jcr.get(key, "") for key in ("quartile", "partition", "value"))
        for metric in jcr.get("metrics", []) if isinstance(jcr.get("metrics"), list) else []:
            if isinstance(metric, dict):
                values.extend(metric.get(key, "") for key in ("quartile", "partition", "value"))
        for value in values:
            match = re.search(r"(?:Q\s*)?([1-4])(?:\s*区)?", str(value or "").upper())
            if match:
                known = status if status in {"verified", "manual"} else "verified" if "核验" in status else status
                return known or "verified", "Q" + match.group(1)
    return "unknown", ""


def _is_preprint(item: dict[str, Any]) -> bool:
    if bool(item.get("is_preprint")):
        return True
    venue = canonical_text(item.get("journal", item.get("venue", "")))
    if any(name in venue for name in ("arxiv", "biorxiv", "medrxiv", "research square", "egusphere")):
        return True
    try:
        host = (urlsplit(str(item.get("url", ""))).hostname or "").casefold()
    except ValueError:
        host = ""
    return any(host == domain or host.endswith("." + domain) for domain in PREPRINT_HOSTS)


def score_frontier_item(
    item: dict[str, Any],
    profile: dict[str, Any],
    journal: dict[str, Any] | None = None,
    *,
    today: date | None = None,
    ai_payload: Any = None,
) -> dict[str, Any]:
    """Calculate the v13 Daily Frontier dual-axis score."""

    today = today or date.today()
    result = deepcopy(item)
    text = " ".join(
        str(item.get(field, ""))
        for field in ("title", "abstract", "structured_description", "author_keywords", "keywords")
    )
    topics, methods, locked = _profile_terms(profile)
    topic_hits = _matched_terms(text, topics)
    method_hits = _matched_terms(text, methods)
    project_hits = _project_matches(text, profile)
    relation = str(item.get("relation", item.get("content_relation", ""))).casefold()
    reviewer_score = _clamp(item.get("content_score", item.get("ai_score", 0)), 0, 100)
    topic_component = min(22, len(topic_hits) * 7 + (8 if relation == "core" else 4 if relation == "transferable" else 0))
    if reviewer_score >= 85:
        topic_component = max(topic_component, round(22 * reviewer_score / 100))
    project_component = min(16, sum(8 if value.get("primary") else 5 for value in project_hits))
    method_component = min(12, len(method_hits) * 4 + min(4, len(_matched_terms(text, ("dataset", "scale", "resolution", "spatial", "temporal"))) * 2))
    similarity = item.get("semantic_similarity")
    if similarity is None:
        semantic_lanes = {canonical_text(value) for value in item.get("recall_strategies", [])}
        similarity_component = 8 if "semantic related" in semantic_lanes or "semantic" in semantic_lanes else 5 if item.get("seed_id") else 0
    else:
        similarity_component = round(10 * _clamp(similarity, 0, 1))
    manual = min(4, (2 if topic_hits else 0) + (2 if _matched_terms(text, locked) else 0))
    behavior = min(2, max(0, int(round(_clamp(item.get("behavior_signal", 0), -2, 2)))))

    status, quartile = _quartile(item, journal)
    priority = str((journal or {}).get("frontier_priority", item.get("priority", ""))).strip()
    journal_component = 0
    if not _is_preprint(item):
        journal_component += {"必看": 7, "关注": 5, "扩展": 2}.get(priority, 0)
        if status in {"verified", "manual"}:
            journal_component += {"Q1": 8, "Q2": 5, "Q3": 0, "Q4": 0}.get(quartile, 0)
    journal_component = min(15, journal_component)
    importance_hits = _matched_terms(text, ("global", "mechanism", "climate change", "food security", "carbon cycle", "biodiversity", "sustainability", "risk", "uncertainty"))
    importance = min(14, 4 + len(importance_hits) * 3) if topic_hits or project_hits else min(14, len(importance_hits) * 3)
    innovation_hits = _matched_terms(text, ("novel", "new framework", "first", "innovative", "causal", "benchmark", "high resolution", "multi source", "foundation model", "explainable"))
    innovation = min(14, len(innovation_hits) * 3 + (3 if len(method_hits) >= 2 else 0))
    completeness = 0
    completeness += 2 if len(str(item.get("abstract", "")).strip()) >= 180 else 1 if str(item.get("abstract", "")).strip() else 0
    completeness += 2 if _unique_strings(item.get("authors", []), 2) else 0
    completeness += 1 if normalized_doi(item.get("doi")) else 0
    completeness += 1 if _unique_strings(item.get("author_keywords", item.get("keywords", [])), 2) else 0
    completeness += 1 if str(item.get("journal", item.get("venue", ""))).strip() else 0
    completeness += 1 if str(item.get("published_date", "")).strip() else 0
    published = None
    try:
        published = date.fromisoformat(str(item.get("published_date", ""))[:10])
    except ValueError:
        pass
    if published is None:
        freshness = 0
    else:
        age = max(0, (today - published).days)
        freshness = 8 if age <= 14 else 7 if age <= 30 else 5 if age <= 90 else 3 if age <= 365 else 1
    sources = _unique_strings([*item.get("source_names", []), item.get("source", "")], 20)
    strategies = _unique_strings([*item.get("recall_strategies", []), item.get("recall_strategy", item.get("discovery_lane", ""))], 12)
    multi = min(7, max(0, len(sources) - 1) * 2 + max(0, len(strategies) - 1) * 2 + (1 if item.get("citation_relation") else 0))

    ai = validate_ai_axes(ai_payload, evidence_fields=("title", "abstract", "author_keywords", "journal", "source_evidence", "task_relevant_local_materials"))
    relevance_ai = ai.get("relevance", {}).get("adjustment") if isinstance(ai.get("relevance"), dict) else None
    value_ai = ai.get("value", {}).get("adjustment") if isinstance(ai.get("value"), dict) else None
    relevance = compose_axis(
        {
            "topic_object": topic_component,
            "project_question": project_component,
            "method_data_scale": method_component,
            "knowledge_similarity": similarity_component,
            "personal_signal": manual + behavior,
        },
        FRONTIER_RELEVANCE_WEIGHTS,
        ai_adjustment=relevance_ai,
        reasons=[
            *(f"命中研究主题：{'、'.join(topic_hits[:3])}" for _ in [0] if topic_hits),
            *(f"匹配项目：{'、'.join(value['name'] for value in project_hits[:2] if value['name'])}" for _ in [0] if project_hits),
            *(f"可迁移方法：{'、'.join(method_hits[:3])}" for _ in [0] if method_hits),
        ],
    )
    value = compose_axis(
        {
            "journal_priority_quality": journal_component,
            "scientific_importance": importance,
            "innovation": innovation,
            "evidence_completeness": completeness,
            "freshness": freshness,
            "multi_channel_signal": multi,
        },
        FRONTIER_VALUE_WEIGHTS,
        ai_adjustment=value_ai,
        reasons=[
            *(f"期刊质量：{quartile}" for _ in [0] if quartile),
            *("多来源或多策略发现" for _ in [0] if multi >= 3),
            *("包含明确创新信号" for _ in [0] if innovation >= 6),
        ],
    )
    result.update(
        {
            "relevance_axis": relevance,
            "value_axis": value,
            "relevance_score": relevance["total"],
            "research_value_score": value["total"],
            "score": int(round((relevance["total"] + value["total"]) / 2)),
            "ai_axis_review": ai,
            "ai_adjustment": None if relevance_ai is None and value_ai is None else {"relevance": relevance_ai, "value": value_ai},
            "matched_terms": _unique_strings([*topic_hits, *method_hits], 12),
            "matched_projects": project_hits,
            "scoring_version": FRONTIER_SCORE_VERSION,
            "policy_version": POLICY_VERSION,
            "jcr_status": status,
            "jcr_quartile": quartile,
            "is_preprint": _is_preprint(item),
        }
    )
    result["pyramid_level"] = pyramid_level(relevance["total"], value["total"], previous=str(item.get("pyramid_level", "")))
    return result


def pyramid_level(relevance: Any, value: Any, *, previous: str = "", relaxed: bool = False) -> str:
    """Classify one pair of scores without applying a count quota."""

    r = int(round(_clamp(relevance, 0, 100)))
    v = int(round(_clamp(value, 0, 100)))
    prior = str(previous).strip().upper()
    # Five-point hysteresis keeps a previously published card from bouncing
    # when a source adds a small amount of metadata.
    if prior == "A" and r >= 70 and v >= 70:
        return "A"
    if prior == "B" and ((r >= 65 and v >= 55) or (v >= 65 and r >= 55)):
        return "B"
    if prior == "C" and max(r, v) >= 70 and min(r, v) >= 40:
        return "C"
    if r >= 75 and v >= 75:
        return "A"
    if (r >= 75 and v >= 60) or (v >= 75 and r >= 60) or (r >= 68 and v >= 68):
        return "B"
    weak_floor = 40 if relaxed else 45
    strong_floor = 70 if relaxed else 75
    if max(r, v) >= strong_floor and min(r, v) >= weak_floor:
        return "C"
    return ""


def dynamic_pyramid(
    items: Iterable[dict[str, Any]],
    *,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Return every qualified item and use same-day relative rank for backfill.

    Normal A/B/C admission is still score based and has no count quota.  Only
    when the current day produced no normal item do we open the backfill lane.
    That lane is ranked against *today's* candidates, never against retained
    unread cards from an earlier day.  A small safety floor prevents a uniformly
    irrelevant day from turning its least-bad record into a recommendation.
    """

    values: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        item = deepcopy(raw)
        level = pyramid_level(item.get("relevance_score"), item.get("research_value_score"), previous=str(item.get("pyramid_level", "")))
        if level:
            item["pyramid_level"] = level
            values.append(item)
        else:
            deferred.append(item)
    day_key = (today or date.today()).isoformat()

    def belongs_to_today(item: dict[str, Any]) -> bool:
        if today is None:
            return True
        observed = str(
            item.get("recommendation_date", item.get("first_seen_date", item.get("first_seen_at", "")))
        )[:10]
        return observed == day_key

    day_values = [item for item in values if belongs_to_today(item)]
    day_deferred = [item for item in deferred if belongs_to_today(item)]
    ranked_day = sorted(
        day_deferred,
        key=lambda item: (
            -min(int(item.get("relevance_score", 0) or 0), int(item.get("research_value_score", 0) or 0)),
            -((int(item.get("relevance_score", 0) or 0) + int(item.get("research_value_score", 0) or 0)) / 2),
            -max(int(item.get("relevance_score", 0) or 0), int(item.get("research_value_score", 0) or 0)),
            str(item.get("id", "")),
        ),
    )
    cohort_size = len(ranked_day)
    for index, item in enumerate(ranked_day, start=1):
        item["daily_relative_rank"] = index
        item["daily_relative_cohort"] = cohort_size
        item["daily_relative_percentile"] = 100 if cohort_size == 1 else round(
            100 * (cohort_size - index) / (cohort_size - 1)
        )

    # No fixed number is requested.  The leading band follows the day's score
    # distribution: the top quartile is eligible, and ties at its boundary are
    # kept.  The safety floor is intentionally lower than the normal C gate and
    # only blocks both-low / nearly unrelated material.
    if not day_values and ranked_day:
        balanced_scores = sorted(
            (
                min(int(item.get("relevance_score", 0) or 0), int(item.get("research_value_score", 0) or 0))
                for item in ranked_day
            ),
            reverse=True,
        )
        leading_count = max(1, (len(balanced_scores) + 3) // 4)
        relative_boundary = balanced_scores[leading_count - 1]
        for item in ranked_day:
            relevance = int(item.get("relevance_score", 0) or 0)
            value = int(item.get("research_value_score", 0) or 0)
            balanced = min(relevance, value)
            average = (relevance + value) / 2
            if balanced < relative_boundary or balanced < 35 or max(relevance, value) < 55 or average < 50:
                continue
            item["pyramid_level"] = "C"
            item["backfill_rule"] = "same_day_relative_rank"
            item["daily_relative_score"] = balanced
            values.append(item)
    order = {"A": 0, "B": 1, "C": 2}
    values.sort(
        key=lambda item: (
            order.get(str(item.get("pyramid_level", "")), 9),
            -min(int(item.get("relevance_score", 0)), int(item.get("research_value_score", 0))),
            -max(int(item.get("relevance_score", 0)), int(item.get("research_value_score", 0))),
            str(item.get("first_seen_at", item.get("first_seen_date", ""))),
        )
    )
    return values


def _source_recognized(item: dict[str, Any]) -> bool:
    venue = str(item.get("journal", item.get("venue", item.get("platform", "")))).strip()
    sources = _unique_strings([*item.get("source_names", []), item.get("source", "")], 10)
    return bool(venue or (_is_preprint(item) and sources))


def _has_stable_link(item: dict[str, Any]) -> bool:
    if normalized_doi(item.get("doi")) or _preprint_id(item) or str(item.get("source_id", "")).strip():
        return True
    try:
        parsed = urlsplit(str(item.get("url", "")).strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def frontier_display_decision(
    item: dict[str, Any],
    *,
    jcr_service_available: bool,
    show_preprints: bool = True,
) -> dict[str, Any]:
    """Return a storage/display state without conflating absence with zero."""

    fingerprint = work_fingerprint(item)
    title = str(item.get("title", "")).strip()
    flags = {canonical_text(value).replace(" ", "_") for value in item.get("exclusion_flags", [])}
    flags.add(canonical_text(item.get("factual_status", "")).replace(" ", "_"))
    if not title:
        return {"state": "factually_excluded", "reason": "missing_title", "visible": False, "fingerprint": fingerprint}
    if flags & FACTUAL_EXCLUSION_FLAGS:
        return {"state": "factually_excluded", "reason": sorted(flags & FACTUAL_EXCLUSION_FLAGS)[0], "visible": False, "fingerprint": fingerprint}
    if str(item.get("status", "")).casefold() in {"dismissed", "permanently_excluded"}:
        return {"state": "content_deleted_fingerprint_kept", "reason": "user_excluded", "visible": False, "fingerprint": fingerprint}
    preprint = _is_preprint(item)
    if preprint and not show_preprints:
        return {"state": "retained_unshown", "reason": "preprints_hidden", "visible": False, "fingerprint": fingerprint}
    status, quartile = _quartile(item)
    if not preprint and status in {"verified", "manual"} and quartile in {"Q3", "Q4"}:
        return {"state": "content_deleted_fingerprint_kept", "reason": "confirmed_jcr_q3_q4", "visible": False, "fingerprint": fingerprint}
    missing: list[str] = []
    if not _unique_strings(item.get("authors", []), 1):
        missing.append("authors")
    if not _source_recognized(item):
        missing.append("academic_source")
    if not str(item.get("published_date", "")).strip():
        missing.append("published_date")
    if not _has_stable_link(item):
        missing.append("stable_identifier")
    if not (
        str(item.get("abstract", item.get("structured_description", ""))).strip()
        or _unique_strings(item.get("author_keywords", item.get("keywords", [])), 1)
    ):
        missing.append("abstract_or_keywords")
    axes = (item.get("relevance_axis"), item.get("value_axis"))
    if any(not isinstance(axis, dict) or not axis.get("valid") or int(axis.get("total", 0) or 0) <= 0 for axis in axes):
        missing.append("valid_dual_axis_score")
    if missing:
        return {"state": "retained_unshown", "reason": "incomplete", "missing_fields": missing, "visible": False, "fingerprint": fingerprint}
    if not preprint and not quartile:
        trusted = bool(item.get("watched_journal") or item.get("trusted_journal") or item.get("official_toc"))
        high = int(item.get("relevance_score", 0)) >= 75 and int(item.get("research_value_score", 0)) >= 70
        identified = bool(str(item.get("journal", "")).strip() and str(item.get("publisher", "")).strip())
        if not (not jcr_service_available and trusted and high and identified):
            return {"state": "retained_unshown", "reason": "jcr_unknown", "visible": False, "fingerprint": fingerprint}
        return {"state": "visible", "reason": "jcr_service_unavailable_fallback", "visible": True, "fingerprint": fingerprint, "label": "JCR暂未核实"}
    return {
        "state": "visible",
        "reason": "eligible_preprint" if preprint else "eligible_jcr_q1_q2",
        "visible": True,
        "fingerprint": fingerprint,
        "label": "尚未同行评审" if preprint else quartile,
    }


def apply_unread_policy(
    items: Iterable[dict[str, Any]],
    *,
    today: date,
    retain_unread: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split display records and minimal fingerprints after the daily rollover."""

    visible: list[dict[str, Any]] = []
    fingerprints: list[dict[str, Any]] = []
    today_key = today.isoformat()
    for raw in items:
        if not isinstance(raw, dict):
            continue
        item = deepcopy(raw)
        status = str(item.get("status", "new")).casefold()
        protected = status in {"saved", "liked", "locked"} or bool(item.get("locked") or item.get("favorite"))
        first_seen = str(item.get("first_seen_at", item.get("first_seen_date", "")))[:10]
        if status in {"read", "dismissed", "permanently_excluded"}:
            fingerprints.append({"fingerprint": work_fingerprint(item), "reason": status, "kept_at": today_key})
            continue
        if first_seen == today_key:
            item["display_bucket"] = "today"
            visible.append(item)
            continue
        if retain_unread or protected:
            item["display_bucket"] = "previous_unread"
            visible.append(item)
        else:
            fingerprints.append({"fingerprint": work_fingerprint(item), "reason": "unread_retention_disabled", "kept_at": today_key})
    return visible, [value for value in fingerprints if value.get("fingerprint")]


def _publisher_quality(item: dict[str, Any], journal: dict[str, Any] | None = None) -> int:
    publisher = canonical_text((journal or {}).get("publisher", item.get("publisher", "")))
    source_evidence = item.get("source_evidence", []) if isinstance(item.get("source_evidence"), list) else []
    third_party = bool(item.get("is_aggregator")) or any(
        bool(value.get("is_aggregator")) for value in source_evidence if isinstance(value, dict)
    )
    known = bool(
        item.get("publisher_identity_verified")
        or (journal or {}).get("id")
        or item.get("journal_library_id")
        or (third_party and publisher)
    )
    if not known:
        return 0
    # The user deliberately moved Elsevier discovery away from ScienceDirect.
    # A third-party Elsevier identification therefore receives a small recall
    # advantage while remaining visibly unverified and below the 15-point cap.
    if third_party and "elsevier" in publisher:
        return 10
    if any(value in publisher for value in ("elsevier", "springer", "wiley", "taylor francis")):
        return 8
    if any(value in publisher for value in ("frontiers", "mdpi")):
        return 5
    return 3


def score_special_issue(
    item: dict[str, Any],
    profile: dict[str, Any],
    journal: dict[str, Any] | None = None,
    *,
    today: date | None = None,
    ai_payload: Any = None,
) -> dict[str, Any]:
    """Calculate v13 special-issue relevance and opportunity axes."""

    today = today or date.today()
    result = deepcopy(item)
    text = " ".join(str(item.get(field, "")) for field in ("title", "scope_text", "keywords", "journal"))
    topics, methods, locked = _profile_terms(profile)
    topic_hits = _matched_terms(text, topics)
    method_hits = _matched_terms(text, methods)
    project_hits = _project_matches(text, profile)
    # An exact hit on a user-maintained research direction is the strongest
    # deterministic signal.  Additional matching directions still matter, but
    # the first core hit must not be diluted to 8/24 merely because AI is off.
    topic = min(24, 16 + len(topic_hits) * 8) if topic_hits else 0
    project = min(18, sum(9 if value.get("primary") else 6 for value in project_hits))
    method = min(10, len(method_hits) * 4)
    journal_fields = _unique_strings((journal or {}).get("fields", []), 20)
    field_hits = _matched_terms(text, journal_fields)
    field = min(8, len(field_hits) * 4 + (2 if str(item.get("journal", "")).strip() else 0))
    personal = min(6, (6 if _matched_terms(text, locked) else 4 if topic_hits else 0) + min(2, int(_clamp(item.get("behavior_signal", 0), 0, 2))))

    status, quartile = _quartile(item, journal)
    quality = min(15, _publisher_quality(item, journal) + ({"Q1": 7, "Q2": 5, "Q3": 2, "Q4": 0}.get(quartile, 0) if status in {"verified", "manual"} else 0))
    frontier_hits = _matched_terms(text, ("emerging", "frontier", "novel", "climate", "global", "mechanism", "sustainable", "digital", "artificial intelligence"))
    frontier = min(15, len(frontier_hits) * 3 + (5 if topic >= 16 else 0))
    scope_fit = min(12, (6 if len(str(item.get("scope_text", ""))) >= 240 else 3 if item.get("scope_text") else 0) + len(method_hits) * 2)
    clarity = 0
    clarity += 3 if str(item.get("scope_text", "")).strip() else 0
    clarity += 2 if str(item.get("submission_url", item.get("official_url", ""))).strip() else 0
    clarity += 2 if str(item.get("article_types", "")).strip() else 0
    clarity += 1 if str(item.get("call_status", "")).strip() else 0
    deadline_text = str(item.get("deadline", "")).strip().casefold()
    rolling = bool(item.get("rolling")) or deadline_text == "rolling"
    deadline_score = 5 if rolling else 0
    if not rolling:
        try:
            remaining = (date.fromisoformat(deadline_text[:10]) - today).days
            deadline_score = 7 if 30 <= remaining <= 180 else 5 if remaining > 0 else 0
        except ValueError:
            deadline_score = 0
    editors = min(5, len(_unique_strings(item.get("guest_editors", item.get("editors", [])), 5)))
    actionability = min(4, sum(bool(str(item.get(field, "")).strip()) for field in ("submission_url", "author_guidelines_url", "contact")))

    ai = validate_ai_axes(
        ai_payload,
        evidence_fields=(
            "title", "scope_text", "keywords", "journal", "publisher", "deadline",
            "verification_status", "source_evidence", "metadata_inference",
        ),
    )
    rel_ai = ai.get("relevance", {}).get("adjustment") if isinstance(ai.get("relevance"), dict) else None
    opp_ai = ai.get("opportunity", {}).get("adjustment") if isinstance(ai.get("opportunity"), dict) else None
    relevance = compose_axis(
        {"topic": topic, "project_paper": project, "method_data_scale": method, "journal_field": field, "personal_signal": personal},
        SPECIAL_RELEVANCE_WEIGHTS,
        ai_adjustment=rel_ai,
        reasons=[*(f"匹配方向：{'、'.join(topic_hits[:3])}" for _ in [0] if topic_hits), *(f"匹配项目：{'、'.join(v['name'] for v in project_hits[:2])}" for _ in [0] if project_hits)],
    )
    opportunity = compose_axis(
        {"journal_publisher_quality": quality, "frontier_value": frontier, "scope_article_fit": scope_fit, "call_clarity": clarity, "deadline": deadline_score, "editors": editors, "actionability": actionability},
        SPECIAL_OPPORTUNITY_WEIGHTS,
        ai_adjustment=opp_ai,
        reasons=[*(f"出版社与期刊身份已核对（{quartile or '分区未知'}）" for _ in [0] if quality), *("征稿范围与操作入口较完整" for _ in [0] if clarity >= 6)],
    )
    result.update(
        {
            "relevance_axis": relevance,
            "opportunity_axis": opportunity,
            "relevance_score": relevance["total"],
            "opportunity_score": opportunity["total"],
            "score": int(round((relevance["total"] + opportunity["total"]) / 2)),
            "ai_axis_review": ai,
            "scoring_version": SPECIAL_ISSUE_SCORE_VERSION,
            "policy_version": POLICY_VERSION,
            "pyramid_level": pyramid_level(relevance["total"], opportunity["total"], previous=str(item.get("pyramid_level", ""))),
        }
    )
    return result


def special_issue_display_decision(item: dict[str, Any], *, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    missing: list[str] = []
    if not str(item.get("title", "")).strip():
        missing.append("title")
    participants = item.get("participating_journals", []) if isinstance(item.get("participating_journals"), list) else []
    has_journal = bool(str(item.get("journal", "")).strip() or _unique_strings(participants, 1))
    has_publisher = bool(str(item.get("publisher", "")).strip())
    if not (has_journal and has_publisher):
        missing.append("journal_and_publisher")
    # The call title is accepted as thematic evidence when a third-party
    # listing has not captured the complete scope.  Confidence remains visible
    # in the axis details; missing factual fields are still hard-gated below.
    discovery_urls = item.get("discovery_urls", []) if isinstance(item.get("discovery_urls"), list) else []
    source_evidence = item.get("source_evidence", []) if isinstance(item.get("source_evidence"), list) else []
    source_url = str(item.get("official_url", item.get("source_url", item.get("discovery_url", "")))).strip()
    if not source_url:
        source_url = next((str(value).strip() for value in discovery_urls if str(value).strip()), "")
    if not source_url:
        source_url = next(
            (
                str(value.get("url", "")).strip()
                for value in source_evidence
                if isinstance(value, dict) and str(value.get("url", "")).strip()
            ),
            "",
        )
    if not source_url:
        missing.append("source_url")
    deadline = str(item.get("deadline", "")).strip().casefold()
    if not deadline and not item.get("rolling"):
        missing.append("deadline_or_rolling")
    if str(item.get("identity_status", "")).casefold() == "conflict" or item.get("fact_conflicts"):
        missing.append("identity_or_fact_conflict")
    call_state = str(item.get("call_status", item.get("verification_status", ""))).casefold()
    if call_state in {"closed", "expired"}:
        missing.append("closed_or_expired")
    if deadline and deadline != "rolling":
        try:
            if date.fromisoformat(deadline[:10]) < today:
                missing.append("closed_or_expired")
        except ValueError:
            missing.append("deadline_or_rolling")
    axes = (item.get("relevance_axis"), item.get("opportunity_axis"))
    if any(not isinstance(axis, dict) or not axis.get("valid") or int(axis.get("total", 0) or 0) <= 0 for axis in axes):
        missing.append("valid_dual_axis_score")
    if int(item.get("relevance_score", 0) or 0) < 60:
        missing.append("relevance_below_60")
    return {
        "visible": not missing,
        "state": "visible" if not missing else "retained_unshown",
        "reason": "eligible" if not missing else "incomplete_or_low_relevance",
        "missing_fields": missing,
    }


def manuscript_sufficiency(paper: dict[str, Any]) -> dict[str, Any]:
    title = str(paper.get("title", "")).strip()
    abstract = str(paper.get("summary", paper.get("abstract", ""))).strip()
    keywords = _unique_strings(paper.get("keywords", []), 30)
    details = [
        str(paper.get(field, "")).strip()
        for field in ("research_object", "method", "data", "scale", "innovation", "article_type", "submission_goal")
    ]
    filled = sum(bool(value) for value in details)
    if not title:
        return {"state": "insufficient", "coverage": 0.0, "reason": "请先填写论文题目。"}
    if not abstract and not keywords:
        return {"state": "insufficient", "coverage": 0.15, "reason": "仅有题目，不能生成伪精确选刊名单。"}
    coverage = min(1.0, 0.35 + (0.25 if abstract else 0) + (0.1 if keywords else 0) + filled * 0.05)
    return {"state": "full" if coverage >= 0.7 else "preliminary", "coverage": round(coverage, 4), "reason": "信息足以完整分层。" if coverage >= 0.7 else "当前只生成初步候选；补充对象、方法、数据或投稿目标可提高可信度。"}


def journal_objective_exclusion(journal: dict[str, Any], *, paper_id: str = "") -> tuple[bool, str]:
    status = canonical_text(journal.get("status", journal.get("publication_status", "")))
    if status in {"ceased", "discontinued", "inactive", "stopped", "停刊"}:
        return True, "confirmed_inactive"
    if journal.get("accepts_article_type") is False or journal.get("article_type_impossible") is True:
        return True, "article_type_not_accepted"
    excluded_for = journal.get("permanently_excluded_for", [])
    if paper_id and paper_id in (excluded_for if isinstance(excluded_for, list) else []):
        return True, "user_permanent_exclusion"
    if bool(journal.get("confirmed_junk")):
        return True, "confirmed_junk"
    return False, ""


def selection_hard_gate(
    journal: dict[str, Any],
    requirements: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Single division gate for legacy and v13 journal-selection paths."""

    journal = journal if isinstance(journal, dict) else {}
    requirements = requirements if isinstance(requirements, dict) else {}
    status, quartile = _quartile(journal)
    confirmed = status in {"verified", "manual"}
    if confirmed and quartile in {"Q3", "Q4"}:
        return False, f"已核验 JCR {quartile}，按选刊统一硬门槛排除"

    def normalized_quartile(value: Any, *, jcr: bool) -> str:
        text = str(value or "").strip().upper().replace(" ", "")
        match = re.search(r"(?:Q)?([1-4])(?:区)?", text)
        if not match:
            return ""
        return f"Q{match.group(1)}" if jcr else match.group(1)

    jcr_targets = {
        normalized_quartile(value, jcr=True)
        for value in requirements.get("jcr_quartiles", [])
        if normalized_quartile(value, jcr=True)
    }
    target = str(requirements.get("quartile_target", "any")).strip().casefold()
    if target == "q1":
        jcr_targets.add("Q1")
    elif target == "q1_q2" or bool(requirements.get("strict_q1_q2", False)):
        jcr_targets.update(("Q1", "Q2"))
    if jcr_targets:
        if not confirmed or not quartile:
            return False, "当前无法核验 JCR 分区，不满足用户选择的严格分区条件"
        if quartile not in jcr_targets:
            return False, f"已核验 JCR {quartile}，不在用户限定分区内"

    cas_targets = {
        normalized_quartile(value, jcr=False)
        for value in requirements.get("cas_quartiles", [])
        if normalized_quartile(value, jcr=False)
    }
    if cas_targets:
        easyscholar = journal.get("easyscholar", {})
        easyscholar = easyscholar if isinstance(easyscholar, dict) else {}
        cas = next(
            (
                value
                for raw in (
                    journal.get("cas_quartile", ""),
                    easyscholar.get("cas_upgrade", ""),
                    easyscholar.get("cas_basic", ""),
                    easyscholar.get("cas_upgrade_small", ""),
                )
                if (value := normalized_quartile(raw, jcr=False))
            ),
            "",
        )
        if not cas:
            return False, "当前无法核验中科院分区，不满足用户选择的严格分区条件"
        if cas not in cas_targets:
            return False, f"已核验中科院 {cas} 区，不在用户限定分区内"
    return True, ""


def score_journal_selection(
    paper: dict[str, Any],
    journal: dict[str, Any],
    profile: dict[str, Any],
    requirements: dict[str, Any] | None = None,
    *,
    ai_payload: Any = None,
) -> dict[str, Any]:
    """Score a paper/journal relationship with explicit evidence coverage."""

    requirements = requirements if isinstance(requirements, dict) else {}
    result = {"journal": deepcopy(journal)}
    def selection_phrases(value: Any) -> list[str]:
        raw_values = value if isinstance(value, list) else [value]
        phrases: list[str] = []
        for raw in raw_values:
            for part in re.split(r"[;；\n,，]+", str(raw or "")):
                text = " ".join(part.split()).strip()
                if len(text) >= 3 and canonical_text(text) not in {canonical_text(item) for item in phrases}:
                    phrases.append(text)
        return phrases

    def lexical_tokens(value: Any) -> set[str]:
        return {
            token
            for token in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)?|[\u4e00-\u9fff]{2,}", canonical_text(value))
            if len(token) >= 3
        }

    paper_text = " ".join(str(paper.get(field, "")) for field in ("title", "summary", "abstract", "keywords", "research_object", "method", "data", "scale", "article_type", "innovation"))
    similar = journal.get("similar_papers", []) if isinstance(journal.get("similar_papers"), list) else []
    similar_text = " ".join(
        str(value.get("title", ""))
        for value in similar
        if isinstance(value, dict)
    )
    scope_text = " ".join(
        str(journal.get(field, ""))
        for field in (
            "name", "scope", "aims_scope", "official_scope", "ai_scope_cn",
            "fields", "topics", "ai_tags", "accepted_article_types", "notes",
        )
    )
    scope_text = " ".join((scope_text, similar_text))
    paper_folded = canonical_text(paper_text)
    scope_folded = canonical_text(scope_text)
    relevant_profile_terms: list[str] = []
    for value in profile.get("terms", []) if isinstance(profile.get("terms"), list) else []:
        if not isinstance(value, dict) or str(value.get("status", "active")).casefold() not in {"active", "confirmed", "locked"}:
            continue
        canonical = str(value.get("canonical_en", value.get("text", ""))).strip()
        aliases = selection_phrases(value.get("aliases", []))
        paper_variants = [canonical, *aliases]
        if not canonical or not any(canonical_text(term) in paper_folded for term in paper_variants if term):
            continue
        relevant_profile_terms.append(canonical)
        translation = str(value.get("translation_zh", "")).strip()
        if translation and canonical_text(translation) in scope_folded:
            scope_text += " " + canonical
    paper_tokens = lexical_tokens(paper_text)
    scope_tokens = lexical_tokens(scope_text)
    overlap = len(paper_tokens & scope_tokens)
    keyword_phrases = selection_phrases(paper.get("keywords", []))
    phrase_hits = _matched_terms(scope_text, [*keyword_phrases, *relevant_profile_terms])
    topic_scope = min(22, max(overlap * 3, len(phrase_hits) * 7 + overlap * 2)) if scope_text.strip() else None
    object_terms = _unique_strings(paper.get("research_object", paper.get("objects", [])), 12)
    if not object_terms:
        object_terms = _unique_strings([*keyword_phrases, *relevant_profile_terms], 12)
    object_field = min(10, len(_matched_terms(scope_text, object_terms)) * 5) if object_terms and scope_text.strip() else None
    method_terms = _unique_strings(paper.get("methods", paper.get("method", [])), 12)
    if not method_terms:
        method_markers = ("method", "model", "forest", "learning", "regression", "shap", "trajectory", "mapping", "remote sensing")
        method_terms = _unique_strings(
            [
                value
                for value in [*keyword_phrases, *relevant_profile_terms]
                if any(marker in canonical_text(value) for marker in method_markers)
            ],
            12,
        )
    method = min(10, len(_matched_terms(scope_text + " " + str(journal.get("recent_methods", "")), method_terms)) * 4) if method_terms else None
    data_terms = _unique_strings([paper.get("data", ""), paper.get("scale", "")], 12)
    data_scale = min(8, len(_matched_terms(scope_text + " " + str(journal.get("recent_papers", "")), data_terms)) * 4) if data_terms else None
    article_type = str(paper.get("article_type", "")).strip()
    accepted = canonical_text(journal.get("accepted_article_types", journal.get("article_types", "")))
    article_score = (8 if canonical_text(article_type) in accepted else 3) if article_type and accepted else None
    similar_score = min(8, len(similar) * 3 + (2 if similar else 0)) if "similar_papers" in journal else None

    contribution = str(paper.get("innovation", paper.get("contribution", ""))).strip()
    threshold = str(journal.get("contribution_threshold", journal.get("positioning", ""))).strip()
    contribution_score = min(18, 8 + overlap) if contribution and threshold else None
    status, quartile = _quartile(journal)
    quality_score = None
    if status in {"verified", "manual"} and quartile:
        quality_score = {"Q1": 15, "Q2": 12, "Q3": 7, "Q4": 3}[quartile]
        priority = str(journal.get("frontier_priority", ""))
        quality_score = min(15, quality_score + {"必看": 3, "关注": 2, "扩展": 1}.get(priority, 0))
    goal = str(requirements.get("strategy", paper.get("submission_goal", "balanced"))).casefold()
    goal_score = 7 if goal in {"balanced", "main", "主投"} else 6 if goal in {"reach", "冲刺"} else 8 if goal in {"safe", "稳妥", "fast", "快速"} else 5
    cycle_days = journal.get("decision_days", journal.get("estimated_decision_days"))
    cycle_score = None if cycle_days in {None, ""} else 8 if _clamp(cycle_days, 0, 5000) <= 60 else 5 if _clamp(cycle_days, 0, 5000) <= 120 else 2
    fee_known = journal.get("fee_mode") not in {None, "", "unknown"} or journal.get("apc") not in {None, ""}
    fee_score = None if not fee_known else 7 if str(requirements.get("fee_mode", "any")) in {"", "any"} else 7 if str(requirements.get("fee_mode")) in canonical_text(journal.get("fee_mode", "")) else 3
    recent = journal.get("recent_papers", journal.get("similar_papers"))
    activity = None if recent is None else min(5, len(recent) if isinstance(recent, list) else int(bool(recent)) * 3)
    requirements_known = any(
        journal.get(field) not in (None, "", [])
        for field in ("data_policy", "code_policy", "format_requirements")
    )
    feasibility = 3 if requirements_known else None

    ai = validate_ai_axes(ai_payload, evidence_fields=("paper_title", "paper_abstract", "journal_scope", "similar_papers", "journal_facts", "task_relevant_local_materials"))
    fit_ai = ai.get("fit", {}).get("adjustment") if isinstance(ai.get("fit"), dict) else None
    strategy_ai = ai.get("strategy", {}).get("adjustment") if isinstance(ai.get("strategy"), dict) else None
    fit = compose_axis(
        {"topic_scope": topic_scope, "object_field": object_field, "method": method, "data_scale": data_scale, "article_type": article_score, "similar_papers": similar_score},
        SELECTION_FIT_WEIGHTS,
        ai_adjustment=fit_ai,
        unknown_fields=[name for name, value in {"object_field": object_field, "method": method, "data_scale": data_scale, "article_type": article_score, "similar_papers": similar_score}.items() if value is None],
    )
    strategy = compose_axis(
        {"contribution_threshold": contribution_score, "quality_target": quality_score, "submission_goal": goal_score, "review_cycle": cycle_score, "fee_oa": fee_score, "activity_continuity": activity, "requirements_feasibility": feasibility},
        SELECTION_STRATEGY_WEIGHTS,
        ai_adjustment=strategy_ai,
        unknown_fields=[name for name, value in {"contribution_threshold": contribution_score, "quality_target": quality_score, "review_cycle": cycle_score, "fee_oa": fee_score, "activity_continuity": activity, "requirements_feasibility": feasibility}.items() if value is None],
    )
    known_weight = sum(weight for name, weight in SELECTION_FIT_WEIGHTS.items() if fit["components"].get(name) is not None)
    known_weight += sum(weight for name, weight in SELECTION_STRATEGY_WEIGHTS.items() if strategy["components"].get(name) is not None)
    coverage = round(known_weight / (BASE_MAX * 2), 4)
    excluded, exclusion_reason = journal_objective_exclusion(journal, paper_id=str(paper.get("id", "")))
    division_allowed, division_reason = selection_hard_gate(journal, requirements)
    if not division_allowed:
        excluded = True
        exclusion_reason = division_reason
    constraint_state = "hard_excluded" if not division_allowed else "eligible"
    tier = selection_tier(fit["total"], strategy["total"], goal=goal)
    sufficiency = manuscript_sufficiency(paper)
    recommendation_state = "full" if coverage >= 0.7 and sufficiency["state"] == "full" else "preliminary"
    result.update(
        {
            "journal_id": str(journal.get("id", "")),
            "journal_name": str(journal.get("name", "")),
            "fit_axis": fit,
            "strategy_axis": strategy,
            "fit_score": fit["total"],
            "strategy_score": strategy["total"],
            "total_score": int(round((fit["total"] + strategy["total"]) / 2)),
            "evidence_coverage": coverage,
            "recommendation_state": recommendation_state,
            "tier": tier if not excluded else "",
            "objective_excluded": excluded,
            "exclusion_reason": exclusion_reason,
            "constraint_state": constraint_state,
            "jcr_status": status,
            "jcr_quartile": quartile,
            "ai_axis_review": ai,
            "scoring_version": JOURNAL_SELECTION_SCORE_VERSION,
            "policy_version": POLICY_VERSION,
        }
    )
    return result


def selection_tier(fit: Any, strategy: Any, *, goal: str = "balanced") -> str:
    f = int(round(_clamp(fit, 0, 100)))
    s = int(round(_clamp(strategy, 0, 100)))
    goal = canonical_text(goal)
    if goal in {"reach", "冲刺"} and f >= 72 and s >= 48:
        return "冲刺"
    if f >= 72 and s >= 65:
        return "主投"
    if s >= 72 and f >= 55:
        return "稳妥"
    if max(f, s) >= 68 and min(f, s) >= 42:
        return "探索"
    return ""
