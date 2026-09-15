"""Deterministic long/short research signals for v12 recommendations."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import date, datetime
from typing import Any

from utils.research_profile_service import (
    add_pending_term,
    canonical_term_key,
    normalize_research_profile_v12,
    reconcile_profile_proposal,
)


SIGNAL_RULES: dict[str, tuple[float, int]] = {
    "favorite": (1.0, 180),
    "paper_association": (1.0, 180),
    "detail_open": (0.35, 45),
    "read": (0.35, 45),
    "ignore": (-1.0, 180),
    "explicit_positive": (1.0, 365),
    "explicit_negative": (-1.0, 365),
}
SIGNAL_LIMIT = 1200


def _timestamp(value: object) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        parsed = datetime.now()
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed


def _string_list(value: Any, limit: int = 24) -> list[str]:
    values = value if isinstance(value, (list, tuple, set)) else []
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()[:180]
        key = canonical_term_key(text)
        if text and key and key not in seen:
            seen.add(key)
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _event_day(value: object) -> str:
    return _timestamp(value).date().isoformat()


def record_signal(profile: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    """Append one valid event, deduplicated by item/action/day."""
    result = normalize_research_profile_v12(profile)
    event_type = str(event.get("event_type", event.get("kind", ""))).strip().casefold()
    if event_type not in SIGNAL_RULES:
        return result
    occurred_at = str(event.get("occurred_at", event.get("at", ""))).strip()
    if not occurred_at:
        occurred_at = datetime.now().isoformat(timespec="seconds")
    item_id = str(event.get("item_id", event.get("article_id", ""))).strip()[:180]
    title = str(event.get("title", "")).strip()[:360]
    if not item_id:
        item_id = hashlib.sha1((title or occurred_at).encode("utf-8")).hexdigest()[:20]
    dedupe_key = f"{event_type}:{canonical_term_key(item_id)}:{_event_day(occurred_at)}"
    existing = [
        dict(value)
        for value in result.get("research_signals", [])
        if isinstance(value, dict)
    ]
    if any(str(value.get("dedupe_key", "")) == dedupe_key for value in existing):
        return result
    base_strength, half_life_days = SIGNAL_RULES[event_type]
    clean = {
        "id": "signal-" + hashlib.sha1(dedupe_key.encode("utf-8")).hexdigest()[:20],
        "dedupe_key": dedupe_key,
        "event_type": event_type,
        "item_id": item_id,
        "title": title,
        "doi": str(event.get("doi", "")).strip().casefold()[:180],
        "terms": _string_list(event.get("terms", event.get("matched_terms", []))),
        "occurred_at": occurred_at,
        "base_strength": base_strength,
        "half_life_days": half_life_days,
        "source": str(event.get("source", "frontier")).strip()[:80] or "frontier",
        "reason": str(event.get("reason", "")).strip()[:360],
    }
    result["research_signals"] = [*existing, clean][-SIGNAL_LIMIT:]
    return result


def _decayed_event(event: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    event_type = str(event.get("event_type", "")).strip().casefold()
    rule = SIGNAL_RULES.get(event_type)
    if rule is None:
        return None
    default_strength, default_half_life = rule
    try:
        base = float(event.get("base_strength", default_strength))
        half_life = max(1, int(event.get("half_life_days", default_half_life)))
    except (TypeError, ValueError):
        base, half_life = default_strength, default_half_life
    age_days = max(0.0, (now - _timestamp(event.get("occurred_at"))).total_seconds() / 86400.0)
    strength = base * (0.5 ** (age_days / half_life))
    result = deepcopy(event)
    result.update(
        {
            "kind": event_type,
            "strength": strength,
            "age_days": age_days,
            "half_life_days": half_life,
        }
    )
    return result


def build_profile_view(profile: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    """Return stable long-term directions and time-decayed behavior seeds."""
    normalized = normalize_research_profile_v12(profile, today=now.date().isoformat())
    long_term: list[dict[str, Any]] = []
    for term in normalized.get("terms", []):
        if not isinstance(term, dict):
            continue
        locked = bool(term.get("locked"))
        long_term.append(
            {
                "id": str(term.get("id", "")),
                "kind": "locked_term" if locked else "active_term",
                "title": str(term.get("canonical_en", "")),
                "translation_zh": str(term.get("translation_zh", "")),
                "terms": [str(term.get("canonical_en", ""))],
                "strength": 1.0 if locked else max(0.01, min(0.99, int(term.get("weight", 50)) / 100.0)),
                "non_decaying": locked,
            }
        )
    for paper in normalized.get("authored_papers", []):
        if not isinstance(paper, dict):
            continue
        title = str(paper.get("title", "")).strip()
        if not title:
            continue
        long_term.append(
            {
                "id": str(paper.get("id", paper.get("doi", title))).strip(),
                "kind": "authored_paper",
                "title": title,
                "doi": str(paper.get("doi", "")).strip(),
                "terms": _string_list(paper.get("keywords", paper.get("terms", []))),
                "strength": 1.0,
                "non_decaying": True,
            }
        )
    short_term = [
        value
        for value in (
            _decayed_event(event, now)
            for event in normalized.get("research_signals", [])
            if isinstance(event, dict)
        )
        if value is not None
    ]
    short_term.sort(
        key=lambda item: (
            -abs(float(item.get("strength", 0.0))),
            str(item.get("occurred_at", "")),
            str(item.get("id", "")),
        )
    )
    long_term.sort(key=lambda item: (-float(item["strength"]), item["kind"], canonical_term_key(item["title"])))
    positive_seeds = [item for item in long_term if item["kind"] == "authored_paper"]
    positive_seeds.extend(item for item in short_term if float(item.get("strength", 0.0)) > 0)
    negative_seeds = [item for item in short_term if float(item.get("strength", 0.0)) < 0]
    return {
        "long_term": long_term,
        "short_term": short_term,
        "positive_seeds": positive_seeds,
        "negative_seeds": negative_seeds,
        "active_terms": deepcopy(normalized.get("terms", [])),
        "excluded_terms": deepcopy(normalized.get("excluded_entries", [])),
        "built_at": now.isoformat(timespec="seconds"),
    }


def apply_profile_comment(
    profile: dict[str, Any],
    classification: dict[str, Any],
    *,
    today: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply an explicit comment through the existing lock/block boundaries."""
    current = normalize_research_profile_v12(profile, today=today)
    classification = classification if isinstance(classification, dict) else {}
    intent = str(classification.get("intent", "ambiguous")).strip().casefold()
    if intent not in {"positive", "negative", "ambiguous"}:
        intent = "ambiguous"
    reason = str(classification.get("reason", "一句话评价")).strip()[:360] or "一句话评价"
    proposal: dict[str, Any] = {"terms": [], "excluded_terms": []}
    if intent == "positive":
        for raw in classification.get("active_terms", []):
            if not isinstance(raw, dict):
                continue
            proposal["terms"].append(
                {
                    **raw,
                    "confidence": "high",
                    "evidence": [*(_string_list(raw.get("evidence"))), reason],
                    "source": "explicit_feedback",
                }
            )
    if intent == "negative":
        proposal["excluded_terms"] = [
            raw for raw in classification.get("excluded_terms", []) if isinstance(raw, (dict, str))
        ]
    updated, changes = reconcile_profile_proposal(current, proposal, today=today)
    for raw in classification.get("pending_terms", []):
        if not isinstance(raw, dict):
            continue
        before = len(updated.get("pending_terms", []))
        updated = add_pending_term(
            updated,
            str(raw.get("canonical_en", raw.get("text", ""))).strip(),
            translation_zh=str(raw.get("translation_zh", "")).strip(),
            weight=int(raw.get("weight", 50) or 50),
            evidence=[reason],
            source="explicit_feedback_pending",
            confidence="low",
            reason=reason,
            today=today,
        )
        if len(updated.get("pending_terms", [])) > before:
            changes.append({"kind": "pending", "term": raw.get("canonical_en", ""), "at": today})
    if intent in {"positive", "negative"}:
        term_values = classification.get("active_terms" if intent == "positive" else "excluded_terms", [])
        terms = [
            str(value.get("canonical_en", value.get("text", ""))).strip()
            for value in term_values
            if isinstance(value, dict)
        ]
        identity_payload = json.dumps(classification, ensure_ascii=False, sort_keys=True, default=str)
        updated = record_signal(
            updated,
            {
                "event_type": "explicit_positive" if intent == "positive" else "explicit_negative",
                "item_id": str(classification.get("item_id", "")).strip()
                or "comment-" + hashlib.sha1(identity_payload.encode("utf-8")).hexdigest()[:16],
                "title": str(classification.get("title", "")).strip(),
                "terms": terms,
                "occurred_at": f"{today}T12:00:00",
                "source": "one_line_feedback",
                "reason": reason,
            },
        )
    return normalize_research_profile_v12(updated, today=today), changes
