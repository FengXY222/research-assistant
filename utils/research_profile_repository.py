"""Atomic persistence and one-step daily organization for the v12 profile."""

from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any, Callable

from utils import file_manager
from utils.research_profile_service import (
    canonical_term_key,
    delete_excluded_term,
    normalize_research_profile_v12,
    reconcile_profile_proposal,
)


class ProfileRepositoryError(RuntimeError):
    """Raised when the formal profile cannot be read or saved atomically."""


def _profile_path() -> Path:
    return file_manager.RESEARCH_PROFILE_FILE


def _read_profile(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ProfileRepositoryError(f"无法读取研究画像: {error}") from error
    if not isinstance(payload, dict):
        raise ProfileRepositoryError("研究画像文件必须是 JSON 对象")
    return normalize_research_profile_v12(payload)


def load_research_profile() -> dict[str, Any]:
    """Load the independent v12 profile, migrating the frontier copy once."""
    path = _profile_path()
    if path.is_file():
        return _read_profile(path)
    previous = path.with_suffix(path.suffix + ".previous")
    if previous.is_file():
        return _read_profile(previous)
    try:
        legacy = file_manager.load_frontier_data().get("profile", {})
    except (OSError, ValueError):
        legacy = {}
    return normalize_research_profile_v12(legacy)


def save_research_profile(profile: dict[str, Any]) -> None:
    """Validate, fsync and atomically replace the profile JSON."""
    path = _profile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    previous = path.with_suffix(path.suffix + ".previous")
    normalized = normalize_research_profile_v12(profile)
    payload = json.dumps(normalized, ensure_ascii=False, indent=2) + "\n"
    had_original = path.is_file()
    try:
        if previous.exists():
            previous.unlink()
        if had_original:
            shutil.copy2(path, previous)
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        candidate = json.loads(temporary.read_text(encoding="utf-8"))
        if not isinstance(candidate, dict):
            raise ProfileRepositoryError("研究画像临时文件校验失败")
        os.replace(temporary, path)
        _read_profile(path)
    except Exception as error:
        if temporary.exists():
            temporary.unlink()
        if previous.is_file():
            os.replace(previous, path)
        if isinstance(error, ProfileRepositoryError):
            raise
        raise ProfileRepositoryError(f"保存研究画像失败: {error}") from error
    else:
        if previous.exists():
            previous.unlink()


def _validate_proposal(proposal: Any) -> dict[str, Any]:
    if not isinstance(proposal, dict):
        raise ValueError("AI 整理计划必须是 JSON 对象")
    for field in (
        "terms",
        "excluded_terms",
        "delete_term_ids",
        "delete_excluded_term_ids",
        "merge_terms",
    ):
        if field in proposal and not isinstance(proposal[field], list):
            raise ValueError(f"AI 整理计划的 {field} 必须是列表")
    return deepcopy(proposal)


def _profile_without_snapshot(profile: dict[str, Any]) -> dict[str, Any]:
    snapshot = deepcopy(profile)
    snapshot.pop("last_organization_snapshot", None)
    snapshot.pop("last_organization_changes", None)
    return snapshot


def _apply_merges(
    profile: dict[str, Any],
    merges: list[Any],
    *,
    today: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    result = deepcopy(profile)
    changes: list[dict[str, Any]] = []
    for raw in merges:
        if not isinstance(raw, dict):
            continue
        target_id = str(raw.get("target_id", "")).strip()
        source_ids = {
            str(value).strip()
            for value in raw.get("source_ids", [])
            if str(value).strip() and str(value).strip() != target_id
        }
        target = next((term for term in result["terms"] if str(term.get("id")) == target_id), None)
        if target is None:
            continue
        removable = [
            term
            for term in result["terms"]
            if str(term.get("id")) in source_ids and not term.get("locked")
        ]
        if not removable:
            continue
        aliases = [*target.get("aliases", [])]
        evidence = [*target.get("evidence", [])]
        sources = [*target.get("sources", [])]
        for term in removable:
            aliases.extend([term.get("canonical_en", ""), *term.get("aliases", [])])
            evidence.extend(term.get("evidence", []))
            sources.extend(term.get("sources", []))
        target["aliases"] = list(dict.fromkeys(value for value in aliases if str(value).strip()))[:24]
        target["evidence"] = list(dict.fromkeys(value for value in evidence if str(value).strip()))[:12]
        target["sources"] = list(dict.fromkeys(value for value in sources if str(value).strip()))[:12]
        if not target.get("locked"):
            name = str(raw.get("canonical_en", "")).strip()
            if name:
                old_name = target["canonical_en"]
                target["canonical_en"] = name[:180]
                target["text"] = target["canonical_en"]
                if canonical_term_key(old_name) != canonical_term_key(name):
                    target["aliases"] = list(dict.fromkeys([*target["aliases"], old_name]))[:24]
            translation = str(raw.get("translation_zh", "")).strip()
            if translation:
                target["translation_zh"] = translation[:180]
            if raw.get("weight") is not None:
                try:
                    target["weight"] = max(1, min(99, int(raw["weight"])))
                except (TypeError, ValueError):
                    pass
        result["terms"] = [term for term in result["terms"] if term not in removable]
        changes.append(
            {
                "kind": "terms_merged",
                "target_id": target_id,
                "source_ids": sorted(str(term.get("id")) for term in removable),
                "term": target["canonical_en"],
                "at": today,
            }
        )
    return normalize_research_profile_v12(result, today=today), changes


def apply_organization_transaction(
    current: dict[str, Any],
    proposal: dict[str, Any],
    *,
    today: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate and apply one plan while retaining exactly one undo snapshot."""
    plan = _validate_proposal(proposal)
    original = normalize_research_profile_v12(current, today=today)
    snapshot = _profile_without_snapshot(original)
    updated, changes = reconcile_profile_proposal(original, plan, today=today)
    updated, merge_changes = _apply_merges(updated, plan.get("merge_terms", []), today=today)
    changes.extend(merge_changes)

    for term_id in plan.get("delete_excluded_term_ids", []):
        term_id = str(term_id).strip()
        before = len(updated.get("excluded_entries", []))
        updated = delete_excluded_term(updated, term_id)
        if len(updated.get("excluded_entries", [])) < before:
            changes.append({"kind": "excluded_deleted", "term_id": term_id, "at": today})

    updated = normalize_research_profile_v12(updated, today=today)
    updated["last_organization_snapshot"] = {"date": today, "profile": snapshot}
    updated["last_organization_changes"] = deepcopy(changes)
    updated["last_organization_date"] = today
    updated["last_auto_organization_date"] = today
    updated.pop("auto_organization_suppressed_for_date", None)
    return updated, changes


def undo_last_organization(profile: dict[str, Any], *, today: str) -> dict[str, Any]:
    result = normalize_research_profile_v12(profile, today=today)
    snapshot = profile.get("last_organization_snapshot") if isinstance(profile, dict) else None
    if not isinstance(snapshot, dict) or str(snapshot.get("date", "")) != today:
        return result
    previous = snapshot.get("profile")
    if not isinstance(previous, dict):
        return result
    restored = normalize_research_profile_v12(previous, today=today)
    restored.pop("last_organization_snapshot", None)
    restored.pop("last_organization_changes", None)
    restored["last_auto_organization_date"] = today
    restored["last_organization_date"] = today
    restored["auto_organization_suppressed_for_date"] = today
    log = [dict(value) for value in restored.get("update_log", []) if isinstance(value, dict)]
    restored["update_log"] = [*log, {"kind": "organization_undone", "at": today}][-200:]
    return restored


def should_auto_organize(profile: dict[str, Any], *, today: str) -> bool:
    normalized = normalize_research_profile_v12(profile, today=today)
    return not (
        str(normalized.get("last_auto_organization_date", "")) == today
        or str(normalized.get("auto_organization_suppressed_for_date", "")) == today
    )


def apply_and_save_ai_organization(
    organizer: Callable[[], dict[str, Any]],
    *,
    today: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Call AI before touching disk, then atomically save a valid plan."""
    current = load_research_profile()
    proposal = organizer()
    effective_day = today or date.today().isoformat()
    updated, changes = apply_organization_transaction(current, proposal, today=effective_day)
    save_research_profile(updated)
    return updated, changes
