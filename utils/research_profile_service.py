"""Deterministic v12 research-profile migration and permission boundaries."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Any


PROFILE_VERSION = 12
ACTIVE_LIMIT = 96
PENDING_LIMIT = 48
EXCLUDED_LIMIT = 48
BLOCK_LIMIT = 256


def canonical_term_key(value: object) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _term_id(text: str) -> str:
    return "term-" + hashlib.sha1(canonical_term_key(text).encode("utf-8")).hexdigest()[:16]


def _integer(value: object, fallback: int, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = fallback
    return max(minimum, min(maximum, result))


def _string_list(value: Any, limit: int = 24, item_limit: int = 220) -> list[str]:
    if isinstance(value, str):
        values = value.replace("，", ",").replace("；", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = []
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()[:item_limit]
        key = canonical_term_key(text)
        if text and key and key not in seen:
            seen.add(key)
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _normalise_evidence(value: Any) -> list[str]:
    return _string_list(value, limit=12, item_limit=260)


def _normalise_sources(raw: dict[str, Any], fallback: str) -> list[str]:
    sources = _string_list(raw.get("sources"), limit=12, item_limit=80)
    source = str(raw.get("source", "")).strip()[:80]
    if source and canonical_term_key(source) not in {canonical_term_key(item) for item in sources}:
        sources.append(source)
    return sources or [fallback]


def _normalise_entry(
    raw: Any,
    *,
    status: str,
    fallback_weight: int,
    fallback_source: str,
    today: str,
) -> dict[str, Any] | None:
    raw = raw if isinstance(raw, dict) else {"canonical_en": raw}
    canonical_en = str(raw.get("canonical_en", raw.get("text", raw.get("term", "")))).strip()[:180]
    if not canonical_en:
        return None
    locked = bool(raw.get("locked", False))
    weight = 100 if locked else _integer(raw.get("weight"), fallback_weight, 1, 100)
    aliases = _string_list(raw.get("aliases"), limit=24, item_limit=180)
    alias_keys = {canonical_term_key(value) for value in aliases}
    legacy_text = str(raw.get("text", "")).strip()[:180]
    if legacy_text and canonical_term_key(legacy_text) != canonical_term_key(canonical_en) and canonical_term_key(legacy_text) not in alias_keys:
        aliases.append(legacy_text)
    confidence = str(raw.get("confidence", "low" if status == "pending" else "high")).strip().casefold()
    if confidence not in {"low", "medium", "high"}:
        confidence = "low" if status == "pending" else "high"
    created_at = str(raw.get("created_at", today)).strip()[:32] or today
    updated_at = str(raw.get("updated_at", created_at)).strip()[:32] or created_at
    entry = {
        "id": str(raw.get("id") or _term_id(canonical_en)).strip()[:80] or _term_id(canonical_en),
        "canonical_en": canonical_en,
        "translation_zh": str(raw.get("translation_zh", raw.get("translation", ""))).strip()[:180],
        "aliases": aliases,
        "status": status,
        "weight": weight,
        "locked": locked,
        "confidence": confidence,
        "sources": _normalise_sources(raw, fallback_source),
        "evidence": _normalise_evidence(raw.get("evidence")),
        "created_at": created_at,
        "updated_at": updated_at,
        "last_signal_at": str(raw.get("last_signal_at", updated_at)).strip()[:32] or updated_at,
        # Transitional aliases keep v11 pages and integrations readable while
        # every v12 consumer moves to the explicit bilingual fields.
        "text": canonical_en,
        "source": _normalise_sources(raw, fallback_source)[0],
    }
    if status == "pending":
        entry["locked"] = False
        entry["reason"] = str(raw.get("reason", "等待确认")).strip()[:260] or "等待确认"
    return entry


def _entry_keys(entry: dict[str, Any]) -> set[str]:
    values = [entry.get("canonical_en", entry.get("text", "")), *entry.get("aliases", [])]
    return {key for key in (canonical_term_key(value) for value in values) if key}


def _normalise_block(raw: Any, today: str) -> dict[str, Any] | None:
    raw = raw if isinstance(raw, dict) else {"display_text": raw}
    display_text = str(raw.get("display_text", raw.get("canonical_key", ""))).strip()[:180]
    canonical_key = canonical_term_key(raw.get("canonical_key", display_text))
    alias_keys = [canonical_term_key(value) for value in _string_list(raw.get("alias_keys"), limit=32, item_limit=180)]
    alias_keys = [value for value in alias_keys if value]
    if canonical_key and canonical_key not in alias_keys:
        alias_keys.insert(0, canonical_key)
    if not canonical_key and alias_keys:
        canonical_key = alias_keys[0]
    if not canonical_key:
        return None
    return {
        "canonical_key": canonical_key,
        "alias_keys": list(dict.fromkeys(alias_keys)),
        "display_text": display_text or canonical_key,
        "reason": str(raw.get("reason", "manual_delete")).strip()[:80] or "manual_delete",
        "deleted_at": str(raw.get("deleted_at", today)).strip()[:32] or today,
        "source": str(raw.get("source", "user")).strip()[:80] or "user",
    }


def _blocked_keys(blocks: list[dict[str, Any]]) -> set[str]:
    return {
        key
        for block in blocks
        for key in [canonical_term_key(block.get("canonical_key", "")), *block.get("alias_keys", [])]
        if key
    }


def _merge_entries(entries: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for entry in entries:
        keys = _entry_keys(entry)
        position = next((positions[key] for key in keys if key in positions), None)
        if position is None:
            position = len(merged)
            merged.append(dict(entry))
        else:
            current = merged[position]
            if entry.get("locked") and not current.get("locked"):
                primary, secondary = dict(entry), current
            else:
                primary, secondary = current, entry
            primary["locked"] = bool(primary.get("locked") or secondary.get("locked"))
            if primary["locked"]:
                primary["weight"] = 100
            else:
                primary["weight"] = max(int(primary.get("weight", 1)), int(secondary.get("weight", 1)))
            primary["aliases"] = _string_list(
                [*primary.get("aliases", []), secondary.get("canonical_en", ""), *secondary.get("aliases", [])],
                limit=24,
                item_limit=180,
            )
            primary["sources"] = _string_list([*primary.get("sources", []), *secondary.get("sources", [])], 12, 80)
            primary["source"] = primary["sources"][0]
            primary["evidence"] = _normalise_evidence([*primary.get("evidence", []), *secondary.get("evidence", [])])
            if not primary.get("translation_zh"):
                primary["translation_zh"] = str(secondary.get("translation_zh", ""))
            merged[position] = primary
        for key in _entry_keys(merged[position]):
            positions[key] = position
    return merged[:limit]


def normalize_research_profile_v12(raw: Any, *, today: str | None = None) -> dict[str, Any]:
    """Normalize v12 fields and migrate legacy keyword lists exactly once."""
    today = today or date.today().isoformat()
    raw = raw if isinstance(raw, dict) else {}
    result = dict(raw)

    blocks = [
        block
        for block in (_normalise_block(value, today) for value in (raw.get("blocked_terms") or []))
        if block is not None
    ]
    block_positions: dict[str, int] = {}
    deduped_blocks: list[dict[str, Any]] = []
    for block in blocks:
        key = block["canonical_key"]
        if key in block_positions:
            current = deduped_blocks[block_positions[key]]
            current["alias_keys"] = list(dict.fromkeys([*current["alias_keys"], *block["alias_keys"]]))
        else:
            block_positions[key] = len(deduped_blocks)
            deduped_blocks.append(block)
    blocks = deduped_blocks[-BLOCK_LIMIT:]
    blocked = _blocked_keys(blocks)

    legacy_migrated_at = str(raw.get("legacy_profile_migrated_at", "")).strip()[:32]
    migrate_legacy = not legacy_migrated_at
    legacy_feedback = raw.get("feedback", {}) if isinstance(raw.get("feedback"), dict) else {}
    legacy_weights = legacy_feedback.get("term_weights", {})
    legacy_weights = legacy_weights if isinstance(legacy_weights, dict) else {}

    active_candidates: list[dict[str, Any]] = []
    for value in raw.get("terms", []) if isinstance(raw.get("terms"), list) else []:
        entry = _normalise_entry(
            value,
            status="active",
            fallback_weight=50,
            fallback_source="user",
            today=today,
        )
        if entry is not None:
            active_candidates.append(entry)
    if migrate_legacy:
        for field, base_weight, source in (
            ("primary_keywords", 70, "legacy_primary"),
            ("secondary_keywords", 45, "legacy_secondary"),
        ):
            for text in _string_list(raw.get(field), limit=48, item_limit=180):
                delta = _integer(legacy_weights.get(canonical_term_key(text), 0), 0, -20, 20)
                entry = _normalise_entry(
                    {"canonical_en": text, "weight": base_weight + delta, "source": source, "evidence": ["从旧版关键词迁移"]},
                    status="active",
                    fallback_weight=base_weight,
                    fallback_source=source,
                    today=today,
                )
                if entry is not None:
                    active_candidates.append(entry)

    excluded_candidates: list[dict[str, Any]] = []
    raw_excluded_entries = raw.get("excluded_entries", [])
    if isinstance(raw_excluded_entries, list):
        for value in raw_excluded_entries:
            entry = _normalise_entry(
                value,
                status="excluded",
                fallback_weight=70,
                fallback_source="user",
                today=today,
            )
            if entry is not None:
                excluded_candidates.append(entry)
    for value in _string_list(raw.get("excluded_terms"), limit=EXCLUDED_LIMIT, item_limit=180):
        entry = _normalise_entry(
            {"canonical_en": value, "source": "legacy_excluded"},
            status="excluded",
            fallback_weight=70,
            fallback_source="legacy_excluded",
            today=today,
        )
        if entry is not None:
            excluded_candidates.append(entry)
    if migrate_legacy:
        for value in _string_list(raw.get("negative_keywords"), limit=EXCLUDED_LIMIT, item_limit=180):
            entry = _normalise_entry(
                {"canonical_en": value, "source": "legacy_negative", "evidence": ["从旧版排除词迁移"]},
                status="excluded",
                fallback_weight=70,
                fallback_source="legacy_negative",
                today=today,
            )
            if entry is not None:
                excluded_candidates.append(entry)
    excluded_entries = _merge_entries(excluded_candidates, limit=EXCLUDED_LIMIT)
    excluded_keys = {key for entry in excluded_entries for key in _entry_keys(entry)}

    terms = [
        entry
        for entry in _merge_entries(active_candidates, limit=ACTIVE_LIMIT)
        if not (_entry_keys(entry) & blocked) and not (_entry_keys(entry) & excluded_keys)
    ]
    terms.sort(key=lambda entry: (-int(entry.get("weight", 1)), canonical_term_key(entry.get("canonical_en", ""))))

    pending_candidates: list[dict[str, Any]] = []
    for value in raw.get("pending_terms", []) if isinstance(raw.get("pending_terms"), list) else []:
        entry = _normalise_entry(
            value,
            status="pending",
            fallback_weight=50,
            fallback_source="ai_pending",
            today=today,
        )
        if entry is not None:
            pending_candidates.append(entry)
    active_keys = {key for entry in terms for key in _entry_keys(entry)}
    pending_terms = [
        entry
        for entry in _merge_entries(pending_candidates, limit=PENDING_LIMIT)
        if not (_entry_keys(entry) & blocked)
        and not (_entry_keys(entry) & excluded_keys)
        and not (_entry_keys(entry) & active_keys)
    ]

    update_log = raw.get("update_log", []) if isinstance(raw.get("update_log"), list) else []
    result.update(
        {
            "version": PROFILE_VERSION,
            "legacy_profile_migrated_at": legacy_migrated_at or today,
            "terms": terms,
            "pending_terms": pending_terms,
            "excluded_entries": excluded_entries,
            "excluded_terms": [entry["canonical_en"] for entry in excluded_entries],
            "blocked_terms": blocks,
            "update_log": [dict(entry) for entry in update_log if isinstance(entry, dict)][-200:],
            "filter_known_q3_q4": bool(raw.get("filter_known_q3_q4", True)),
        }
    )
    return result


def normalize_research_profile_v11(raw: Any) -> dict[str, Any]:
    """Compatibility wrapper retained while v11 consumers move to v12."""
    return normalize_research_profile_v12(raw)


def _append_profile_log(profile: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    result = dict(profile)
    current = result.get("update_log", [])
    current = [dict(item) for item in current if isinstance(item, dict)] if isinstance(current, list) else []
    result["update_log"] = [*current, dict(entry)][-200:]
    return result


def _append_block(
    profile: dict[str, Any],
    entry: dict[str, Any],
    *,
    reason: str,
    today: str,
    source: str = "user",
) -> dict[str, Any]:
    result = dict(profile)
    key = canonical_term_key(entry.get("canonical_en", entry.get("text", "")))
    alias_keys = sorted(_entry_keys(entry))
    blocks = [dict(value) for value in result.get("blocked_terms", []) if isinstance(value, dict)]
    existing = next((value for value in blocks if canonical_term_key(value.get("canonical_key", "")) == key), None)
    if existing is None:
        blocks.append(
            {
                "canonical_key": key,
                "alias_keys": alias_keys,
                "display_text": str(entry.get("canonical_en", entry.get("text", ""))),
                "reason": reason,
                "deleted_at": today,
                "source": source,
            }
        )
    else:
        existing["alias_keys"] = sorted(set([*existing.get("alias_keys", []), *alias_keys]))
        existing.update({"reason": reason, "deleted_at": today, "source": source})
    result["blocked_terms"] = blocks[-BLOCK_LIMIT:]
    return result


def add_pending_term(
    profile: Any,
    text: str,
    *,
    translation_zh: str = "",
    weight: int = 50,
    evidence: Any = None,
    source: str = "manual",
    confidence: str = "low",
    reason: str = "等待用户确认",
    today: str | None = None,
) -> dict[str, Any]:
    today = today or date.today().isoformat()
    result = normalize_research_profile_v12(profile, today=today)
    candidate = _normalise_entry(
        {
            "canonical_en": text,
            "translation_zh": translation_zh,
            "weight": weight,
            "source": source,
            "confidence": confidence,
            "reason": reason,
            "evidence": evidence if evidence is not None else ([reason] if reason.strip() else []),
        },
        status="pending",
        fallback_weight=50,
        fallback_source=source,
        today=today,
    )
    if candidate is None:
        return result
    occupied = _blocked_keys(result["blocked_terms"])
    occupied |= {key for group in (result["terms"], result["pending_terms"], result["excluded_entries"]) for item in group for key in _entry_keys(item)}
    if _entry_keys(candidate) & occupied:
        return result
    result["pending_terms"] = [*result["pending_terms"], candidate][-PENDING_LIMIT:]
    return _append_profile_log(result, {"kind": "pending_added", "term": candidate["canonical_en"], "source": source, "at": today})


def confirm_pending_term(profile: Any, term_id: str, *, today: str | None = None) -> dict[str, Any]:
    today = today or date.today().isoformat()
    result = normalize_research_profile_v12(profile, today=today)
    pending = next((item for item in result["pending_terms"] if str(item.get("id")) == str(term_id)), None)
    if pending is None:
        return result
    result["pending_terms"] = [item for item in result["pending_terms"] if str(item.get("id")) != str(term_id)]
    active = {**pending, "status": "active", "source": "user_confirmed", "sources": [*pending.get("sources", []), "user_confirmed"], "updated_at": today}
    result["terms"] = [*result["terms"], active]
    result = normalize_research_profile_v12(result, today=today)
    return _append_profile_log(result, {"kind": "pending_confirmed", "term": pending["canonical_en"], "term_id": str(term_id), "at": today})


def reject_pending_term(profile: Any, term_id: str, *, today: str | None = None) -> dict[str, Any]:
    today = today or date.today().isoformat()
    result = normalize_research_profile_v12(profile, today=today)
    pending = next((item for item in result["pending_terms"] if str(item.get("id")) == str(term_id)), None)
    if pending is None:
        return result
    result["pending_terms"] = [item for item in result["pending_terms"] if str(item.get("id")) != str(term_id)]
    result = _append_block(result, pending, reason="pending_rejected", today=today)
    result = normalize_research_profile_v12(result, today=today)
    return _append_profile_log(result, {"kind": "pending_rejected", "term": pending["canonical_en"], "term_id": str(term_id), "at": today})


def lock_term(profile: Any, term_id: str, today: str | None = None) -> dict[str, Any]:
    today = today or date.today().isoformat()
    result = normalize_research_profile_v12(profile, today=today)
    for term in result["terms"]:
        if str(term.get("id")) == str(term_id):
            term.update({"locked": True, "weight": 100, "source": "user_locked", "sources": [*term.get("sources", []), "user_locked"], "updated_at": today})
            break
    return normalize_research_profile_v12(result, today=today)


def unlock_term(profile: Any, term_id: str, today: str | None = None) -> dict[str, Any]:
    today = today or date.today().isoformat()
    result = normalize_research_profile_v12(profile, today=today)
    for term in result["terms"]:
        if str(term.get("id")) == str(term_id):
            term.update({"locked": False, "weight": min(99, max(1, int(term.get("weight", 70)))), "source": "user_unlocked", "sources": [*term.get("sources", []), "user_unlocked"], "updated_at": today})
            break
    return normalize_research_profile_v12(result, today=today)


def remove_term(
    profile: Any,
    term_id: str,
    *,
    permanent: bool = True,
    today: str | None = None,
) -> dict[str, Any]:
    today = today or date.today().isoformat()
    result = normalize_research_profile_v12(profile, today=today)
    term = next((item for item in result["terms"] if str(item.get("id")) == str(term_id)), None)
    if term is None or term.get("locked"):
        return result
    result["terms"] = [item for item in result["terms"] if str(item.get("id")) != str(term_id)]
    if permanent:
        result = _append_block(result, term, reason="manual_delete", today=today)
    result = normalize_research_profile_v12(result, today=today)
    return _append_profile_log(result, {"kind": "term_removed", "term": term["canonical_en"], "permanent": permanent, "at": today})


def update_term_fields(
    profile: Any,
    term_id: str,
    *,
    canonical_en: str | None = None,
    translation_zh: str | None = None,
    weight: int | None = None,
) -> dict[str, Any]:
    result = normalize_research_profile_v12(profile)
    for term in result["terms"]:
        if str(term.get("id")) != str(term_id):
            continue
        if translation_zh is not None:
            term["translation_zh"] = str(translation_zh).strip()[:180]
        if not term.get("locked"):
            if canonical_en is not None and canonical_term_key(canonical_en):
                old_name = term["canonical_en"]
                term["canonical_en"] = str(canonical_en).strip()[:180]
                term["text"] = term["canonical_en"]
                if canonical_term_key(old_name) != canonical_term_key(term["canonical_en"]):
                    term["aliases"] = _string_list([*term.get("aliases", []), old_name], 24, 180)
            if weight is not None:
                term["weight"] = _integer(weight, int(term.get("weight", 50)), 1, 99)
        break
    return normalize_research_profile_v12(result)


def upsert_excluded_term(
    profile: Any,
    *,
    canonical_en: str,
    translation_zh: str,
    locked: bool = False,
    source: str = "user",
) -> dict[str, Any]:
    result = normalize_research_profile_v12(profile)
    key = canonical_term_key(canonical_en)
    if not key:
        return result
    active = next((item for item in result["terms"] if key in _entry_keys(item)), None)
    if active is not None and active.get("locked"):
        return result
    if active is not None:
        result["terms"] = [item for item in result["terms"] if item is not active]
    existing = next((item for item in result["excluded_entries"] if key in _entry_keys(item)), None)
    if existing is None:
        entry = _normalise_entry(
            {"canonical_en": canonical_en, "translation_zh": translation_zh, "locked": locked, "source": source},
            status="excluded",
            fallback_weight=70,
            fallback_source=source,
            today=date.today().isoformat(),
        )
        if entry is not None:
            result["excluded_entries"].append(entry)
    else:
        existing["translation_zh"] = str(translation_zh).strip()[:180]
        existing["sources"] = _string_list([*existing.get("sources", []), source], 12, 80)
        existing["source"] = existing["sources"][0]
        if locked:
            existing["locked"] = True
            existing["weight"] = 100
    result["excluded_terms"] = [item["canonical_en"] for item in result["excluded_entries"]]
    return normalize_research_profile_v12(result)


def delete_excluded_term(profile: Any, term_id: str) -> dict[str, Any]:
    result = normalize_research_profile_v12(profile)
    entry = next((item for item in result["excluded_entries"] if str(item.get("id")) == str(term_id)), None)
    if entry is None or entry.get("locked"):
        return result
    result["excluded_entries"] = [item for item in result["excluded_entries"] if str(item.get("id")) != str(term_id)]
    result["excluded_terms"] = [item["canonical_en"] for item in result["excluded_entries"]]
    return normalize_research_profile_v12(result)


def set_excluded_terms(profile: Any, terms: Any) -> dict[str, Any]:
    """Compatibility setter for v11 text boxes without bypassing locks."""
    result = normalize_research_profile_v12(profile)
    desired = _string_list(terms, limit=EXCLUDED_LIMIT, item_limit=180)
    desired_keys = {canonical_term_key(value) for value in desired}
    result["excluded_entries"] = [
        entry
        for entry in result["excluded_entries"]
        if entry.get("locked") or canonical_term_key(entry.get("canonical_en", "")) in desired_keys
    ]
    result["excluded_terms"] = [entry["canonical_en"] for entry in result["excluded_entries"]]
    for text in desired:
        result = upsert_excluded_term(result, canonical_en=text, translation_zh="", source="user")
    return normalize_research_profile_v12(result)


def reconcile_profile_proposal(
    profile: Any,
    proposal: Any,
    today: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply an AI proposal while local locks, blocks and exclusions win."""
    today = today or date.today().isoformat()
    result = normalize_research_profile_v12(profile, today=today)
    proposal = proposal if isinstance(proposal, dict) else {}
    log: list[dict[str, Any]] = []

    for raw in proposal.get("excluded_terms", []) if isinstance(proposal.get("excluded_terms"), list) else []:
        if isinstance(raw, dict):
            text = str(raw.get("canonical_en", raw.get("text", ""))).strip()
            translation = str(raw.get("translation_zh", "")).strip()
        else:
            text, translation = str(raw).strip(), ""
        key = canonical_term_key(text)
        active = next((item for item in result["terms"] if key in _entry_keys(item)), None)
        if active is not None and active.get("locked"):
            log.append({"kind": "conflict", "term": active["canonical_en"], "detail": "AI 建议排除已锁定研究词", "at": today})
            continue
        before = len(result["excluded_entries"])
        result = upsert_excluded_term(result, canonical_en=text, translation_zh=translation, source="ai")
        if len(result["excluded_entries"]) > before:
            log.append({"kind": "excluded", "term": text, "detail": "AI 建议排除", "at": today})

    for term_id in _string_list(proposal.get("delete_term_ids"), limit=ACTIVE_LIMIT, item_limit=80):
        before = len(result["terms"])
        result = remove_term(result, term_id, permanent=False, today=today)
        if len(result["terms"]) < before:
            log.append({"kind": "term_deleted", "term_id": term_id, "detail": "AI 整理删除普通词", "at": today})

    blocked = _blocked_keys(result["blocked_terms"])
    excluded_keys = {key for item in result["excluded_entries"] for key in _entry_keys(item)}
    for raw in proposal.get("terms", []) if isinstance(proposal.get("terms"), list) else []:
        candidate = _normalise_entry(raw, status="active", fallback_weight=50, fallback_source="ai", today=today)
        if candidate is None or (_entry_keys(candidate) & blocked) or (_entry_keys(candidate) & excluded_keys):
            continue
        current = next((item for item in result["terms"] if _entry_keys(item) & _entry_keys(candidate)), None)
        evidence = _normalise_evidence(raw.get("evidence", []) if isinstance(raw, dict) else [])
        if current is not None and current.get("locked"):
            current["aliases"] = _string_list([*current.get("aliases", []), *candidate.get("aliases", [])], 24, 180)
            current["evidence"] = _normalise_evidence([*current.get("evidence", []), *evidence])
            if int(candidate.get("weight", 100)) != 100:
                log.append({"kind": "locked_retained", "term": current["canonical_en"], "detail": "锁定词未被 AI 修改", "at": today})
            continue
        if current is not None:
            old_weight = int(current.get("weight", 50))
            current["weight"] = _integer(candidate.get("weight"), old_weight, 1, 99)
            current["evidence"] = evidence or current.get("evidence", [])
            current["sources"] = _string_list([*current.get("sources", []), "ai_reconciled"], 12, 80)
            current["source"] = current["sources"][0]
            current["updated_at"] = today
            if current["weight"] != old_weight:
                log.append({"kind": "weight_updated", "term": current["canonical_en"], "from": old_weight, "to": current["weight"], "at": today})
            continue
        if candidate.get("confidence") == "low":
            before = len(result["pending_terms"])
            result = add_pending_term(
                result,
                candidate["canonical_en"],
                translation_zh=str(candidate.get("translation_zh", "")),
                weight=int(candidate.get("weight", 50)),
                evidence=evidence,
                source="ai_pending",
                confidence="low",
                reason=str(raw.get("reason", "低置信度新词，等待确认")) if isinstance(raw, dict) else "低置信度新词，等待确认",
                today=today,
            )
            if len(result["pending_terms"]) > before:
                log.append({"kind": "pending", "term": candidate["canonical_en"], "detail": "低置信度 AI 建议", "at": today})
        elif evidence:
            candidate.update({"source": "ai_evidence", "sources": [*candidate.get("sources", []), "ai_evidence"], "evidence": evidence, "updated_at": today})
            result["terms"].append(candidate)
            log.append({"kind": "term_added", "term": candidate["canonical_en"], "detail": "有证据的 AI 建议", "at": today})
        else:
            before = len(result["pending_terms"])
            result = add_pending_term(
                result,
                candidate["canonical_en"],
                translation_zh=str(candidate.get("translation_zh", "")),
                weight=int(candidate.get("weight", 50)),
                evidence=evidence,
                source="ai_pending",
                confidence=candidate["confidence"],
                reason=str(raw.get("reason", "缺少可验证证据，等待确认")) if isinstance(raw, dict) else "等待确认",
                today=today,
            )
            if len(result["pending_terms"]) > before:
                log.append({"kind": "pending", "term": candidate["canonical_en"], "detail": "AI 建议缺少证据", "at": today})

    result = normalize_research_profile_v12(result, today=today)
    result["update_log"] = [*result["update_log"], *log][-200:]
    return result, log


def profile_source_signature(
    achievements: list[dict[str, Any]],
    feedback: list[dict[str, Any]],
    settings: dict[str, Any],
) -> str:
    """Hash source fingerprints and metadata, never raw PDF or note content."""
    achievement_rows = []
    for item in achievements:
        if not isinstance(item, dict):
            continue
        pdf_rows = []
        for pdf in item.get("pdf_files", []):
            if isinstance(pdf, dict):
                pdf_rows.append({key: str(pdf.get(key, "")) for key in ("id", "path", "sha256", "mtime", "size")})
        achievement_rows.append(
            {
                "id": str(item.get("id", "")),
                "updated_at": str(item.get("updated_at", item.get("date", ""))),
                "pdf": pdf_rows,
            }
        )
    feedback_rows = []
    for item in feedback:
        if not isinstance(item, dict):
            continue
        events = item.get("feedback_events", [])
        event_rows = [
            {key: str(event.get(key, "")) for key in ("id", "action", "at")}
            | {
                "kind": str(event.get("classification", {}).get("kind", ""))
                if isinstance(event.get("classification", {}), dict)
                else ""
            }
            for event in events
            if isinstance(event, dict)
        ]
        feedback_rows.append(
            {
                **{
                    key: str(item.get(key, ""))
                    for key in ("id", "item_id", "action", "updated_at", "created_at", "feedback")
                },
                "events": event_rows[-100:],
            }
        )
    enabled = {
        key: settings.get(key)
        for key in (
            "auto_profile_from_achievements",
            "auto_profile_from_frontier",
            "profile_read_achievement_pdfs",
        )
    }
    source = json.dumps(
        {"achievements": achievement_rows, "feedback": feedback_rows, "settings": enabled},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(source.encode("utf-8")).hexdigest()
