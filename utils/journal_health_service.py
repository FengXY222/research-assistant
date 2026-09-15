"""Pure v11 journal-library health and frontier import helpers."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from uuid import uuid4
from typing import Any

from utils.publisher_utils import canonical_publisher


def _journal_key(name: object, publisher: object) -> tuple[str, str]:
    compact_name = " ".join(str(name or "").casefold().split())
    compact_publisher = " ".join(canonical_publisher(str(publisher or "")).casefold().split())
    return compact_name, compact_publisher


def publisher_group_key(journal: dict[str, Any]) -> str:
    """Return the compact publisher family label used by the library shelf."""
    journal = journal if isinstance(journal, dict) else {}
    return canonical_publisher(str(journal.get("publisher", "")).strip()) or "未填写出版社"


def journal_row_model(journal: dict[str, Any], tag_limit: int = 3) -> dict[str, Any]:
    """Derive the few fields a compact journal row may display persistently."""
    journal = journal if isinstance(journal, dict) else {}
    jcr = journal.get("jcr", {})
    jcr = jcr if isinstance(jcr, dict) else {}
    metrics = jcr.get("metrics", [])
    metrics = metrics if isinstance(metrics, list) else []
    quartile = ""
    for metric in metrics:
        if isinstance(metric, dict) and str(metric.get("quartile", "")).upper() in {"Q1", "Q2", "Q3", "Q4"}:
            quartile = str(metric.get("quartile", "")).upper()
            break
    health = journal_health_status(journal)
    state = {
        "verified": f"已核验 {quartile}" if quartile else "已核验",
        "manual": f"手动 {quartile}" if quartile else "手动",
        "ai_pending_verification": f"AI 估计 {quartile}·待核验" if quartile else "AI 估计·待核验",
        # The compact row reserves its last slot for JCR provenance.  Calling
        # every pending record "资料不全" hid the actionable fact users need:
        # JCR information still needs to be supplied or verified.
        "incomplete": "JCR 待补",
        "needs_metadata_update": "待更新",
        "user_low_quality": "用户标记低分区",
        "unknown": "未知",
    }.get(health, "未知")
    raw_tags = journal.get("fields", []) or journal.get("ai_tags", [])
    raw_tags = raw_tags if isinstance(raw_tags, list) else []
    tags = [str(value).strip() for value in raw_tags if str(value).strip()]
    return {
        "id": str(journal.get("id", "")),
        "name": str(journal.get("name", "未命名期刊")).strip() or "未命名期刊",
        "publisher": publisher_group_key(journal),
        "tags": " · ".join(tags[:max(1, tag_limit)]) or "未分类",
        "jcr_state": state,
        "more_actions": ["收藏", "更新资料", "删除"],
    }


def import_frontier_journal(
    library: list[dict[str, Any]], frontier_item: dict[str, Any], now: str | None = None
) -> tuple[list[dict[str, Any]], bool]:
    """Create a minimal, idempotent journal record from a frontier article."""
    now = now or date.today().isoformat()
    result = [deepcopy(item) for item in library if isinstance(item, dict)]
    name = str(frontier_item.get("journal", frontier_item.get("name", ""))).strip()
    publisher = canonical_publisher(str(frontier_item.get("publisher", "")).strip())
    if not name:
        return result, False
    key = _journal_key(name, publisher)
    for item in result:
        existing_key = _journal_key(item.get("name", ""), item.get("publisher", ""))
        # A source article often has no publisher. In that case, a matching
        # known journal is still the same library item rather than a duplicate.
        if existing_key == key or (existing_key[0] == key[0] and (not key[1] or not existing_key[1])):
            return result, False
    result.append(
        {
            "id": uuid4().hex,
            "name": name,
            "publisher": publisher,
            "issn": "",
            "fields": [],
            "ai_tags": [],
            "website": "",
            "notes": "",
            "frontier_priority": "扩展",
            "favorite": False,
            "jcr": {"status": "pending", "source": "", "checked_at": "", "metrics": [], "confidence": "", "note": ""},
            "provenance": {
                "kind": "frontier",
                "first_item_id": str(frontier_item.get("id", "")).strip(),
                "source_url": str(frontier_item.get("url", "")).strip(),
                "discovered_at": now,
            },
            "metadata_dirty": True,
            "ai_auto_pending": True,
            "created_at": now,
        }
    )
    return result, True


def journal_health_status(journal: dict[str, Any], today: str | None = None) -> str:
    """Return the one compact state that should appear in the library row."""
    journal = journal if isinstance(journal, dict) else {}
    if str(journal.get("user_quality_flag", "")).strip().casefold() in {"low", "q3_q4", "三四区", "低质量"}:
        return "user_low_quality"
    jcr = journal.get("jcr", {})
    jcr = jcr if isinstance(jcr, dict) else {}
    status = str(jcr.get("status", "pending")).strip()
    if status == "verified":
        return "verified"
    if status == "manual":
        return "manual"
    if status == "ai_estimated":
        return "ai_pending_verification"
    if not str(journal.get("issn", "")).strip() or not str(journal.get("website", "")).strip() or not journal.get("fields"):
        return "incomplete"
    if bool(journal.get("metadata_dirty", False)) or bool(journal.get("ai_auto_pending", False)):
        return "needs_metadata_update"
    return "unknown"


def journal_health_targets(library: list[dict[str, Any]], model: str, today: str | None = None) -> list[dict[str, Any]]:
    """Select only incomplete/changed records so a full update does not waste API quota."""
    targets: list[dict[str, Any]] = []
    for journal in library:
        if not isinstance(journal, dict) or not str(journal.get("id", "")).strip():
            continue
        reason = journal_health_status(journal, today)
        if reason not in {"incomplete", "needs_metadata_update"}:
            continue
        target = deepcopy(journal)
        target["health_reason"] = reason
        target["health_model"] = str(model or "").strip()
        targets.append(target)
    return targets


def merge_journal_health_patch(journal: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Merge enrichment safely; verified/manual/locked JCR facts always win."""
    journal = journal if isinstance(journal, dict) else {}
    patch = patch if isinstance(patch, dict) else {}
    result = deepcopy(journal)
    existing_jcr = result.get("jcr", {})
    existing_jcr = existing_jcr if isinstance(existing_jcr, dict) else {}
    existing_state = str(existing_jcr.get("status", "")).strip()
    for key, value in patch.items():
        if key == "jcr":
            continue
        result[key] = deepcopy(value)
    incoming_jcr = patch.get("jcr")
    if isinstance(incoming_jcr, dict) and not bool(result.get("jcr_locked", False)) and existing_state not in {"verified", "manual"}:
        result["jcr"] = deepcopy(incoming_jcr)
    else:
        result["jcr"] = deepcopy(existing_jcr)
    return result
