"""Idempotent, backup-first migration from the v12 JSON stores to v13."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Callable

from utils.action_transaction import apply_json_transaction
from utils.source_registry import migrate_legacy_frontier_secrets, normalize_source_settings
from utils.v13_pipeline import normalize_runtime_store
from utils.v13_policy import merge_work_families, work_fingerprint


SCHEMA_VERSION = 13


def _read(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return deepcopy(fallback)


def _confirmed_low_jcr(item: dict[str, Any]) -> bool:
    status = str(item.get("jcr_status", item.get("jcr_state", ""))).strip().casefold()
    quartile = str(item.get("jcr_quartile", "")).strip().upper()
    if status not in {"verified", "manual"}:
        return False
    return quartile in {"Q3", "Q4"}


def _migrate_frontier(raw: Any, profile: dict[str, Any]) -> tuple[dict[str, Any], dict[str, int]]:
    payload = deepcopy(raw if isinstance(raw, dict) else {})
    values = payload.get("items", []) if isinstance(payload.get("items"), list) else []
    migrated: list[dict[str, Any]] = []
    fingerprints = [dict(value) for value in payload.get("fingerprints", []) if isinstance(value, dict)]
    q34_removed = 0
    for value in values:
        if not isinstance(value, dict) or not str(value.get("title", "")).strip():
            continue
        item = deepcopy(value)
        fingerprint = work_fingerprint(item)
        legacy_score = {
            "score": item.get("score"),
            "score_breakdown": deepcopy(item.get("score_breakdown", {})),
            "ai_score": item.get("ai_score"),
            "algorithm_version": payload.get("algorithm_version"),
        }
        if any(value not in (None, "", {}, []) for value in legacy_score.values()):
            item.setdefault("legacy_score_snapshot", legacy_score)
        item.pop("relevance_axis", None)
        item.pop("value_axis", None)
        item.pop("relevance_score", None)
        item.pop("research_value_score", None)
        item["ai_adjustment"] = None
        item["candidate_state"] = "retained_unshown"
        item["fingerprint"] = fingerprint
        # Old automatic rejection was never a user permanent exclusion.
        if item.get("content_decision") == "reject" and str(item.get("status", "new")) == "new":
            item["legacy_content_decision"] = "reject"
            item["content_decision"] = "pending"
        if _confirmed_low_jcr(item) and not item.get("is_preprint"):
            fingerprints.append(
                {
                    "fingerprint": fingerprint,
                    "reason": "confirmed_jcr_q3_q4",
                    "jcr_quartile": str(item.get("jcr_quartile", "")),
                    "kept_at": datetime.now().isoformat(timespec="seconds"),
                    "source_evidence": deepcopy(item.get("source_evidence", [])),
                }
            )
            q34_removed += 1
            continue
        migrated.append(item)
    families = merge_work_families(migrated)
    fingerprint_map = {
        str(value.get("fingerprint", "")): value
        for value in fingerprints
        if str(value.get("fingerprint", ""))
    }
    payload.update(
        {
            "profile": profile,
            "items": families,
            "fingerprints": list(fingerprint_map.values()),
            "algorithm_version": 13,
            "schema_version": SCHEMA_VERSION,
            "migration": {
                "from": int(payload.get("schema_version", 12) or 12),
                "to": SCHEMA_VERSION,
                "completed_at": datetime.now().isoformat(timespec="seconds"),
            },
        }
    )
    return payload, {
        "input_items": len(values),
        "output_items": len(families),
        "q3_q4_content_removed": q34_removed,
        "fingerprints": len(fingerprint_map),
    }


def migrate_to_v13(
    *,
    data_dir: Path | None = None,
    protector: Callable[[str], str] | None = None,
    create_pre_migration_backup: bool = True,
) -> dict[str, Any]:
    """Migrate through an atomic multi-file replacement and remain rerunnable."""

    from utils import file_manager
    from utils.secure_store import protect_secret

    root = Path(data_dir) if data_dir is not None else file_manager.DATA_DIR
    settings_path = root / "settings.json"
    frontier_path = root / "frontier.json"
    profile_path = root / "research_profile.json"
    runtime_path = root / "runtime_v13.json"
    raw_settings = _read(settings_path, {})
    current_schema = int(raw_settings.get("schema_version", 0) or 0) if isinstance(raw_settings, dict) else 0
    if current_schema >= SCHEMA_VERSION:
        return {"migrated": False, "schema_version": current_schema, "reason": "already_current"}

    if create_pre_migration_backup:
        file_manager.create_backup(
            f"before-v13-migration-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}"
        )

    raw_frontier = _read(frontier_path, {})
    raw_profile = _read(profile_path, {})
    frontier_profile = raw_frontier.get("profile", {}) if isinstance(raw_frontier, dict) else {}
    combined_profile = {**(frontier_profile if isinstance(frontier_profile, dict) else {}), **(raw_profile if isinstance(raw_profile, dict) else {})}
    normalized_settings = file_manager.normalize_app_settings(raw_settings)
    normalized_sources = normalize_source_settings(normalized_settings.get("data_sources"))
    migrated_profile, migrated_sources, migrated_secret_ids = migrate_legacy_frontier_secrets(
        combined_profile,
        normalized_sources,
        protector=protector or protect_secret,
    )
    normalized_settings["data_sources"] = migrated_sources
    normalized_settings["schema_version"] = SCHEMA_VERSION
    normalized_settings["migration_v13_completed_at"] = datetime.now().isoformat(timespec="seconds")
    frontier, counts = _migrate_frontier(raw_frontier, migrated_profile)
    runtime = normalize_runtime_store(_read(runtime_path, {}))
    runtime["migration_report"] = {
        "schema_version": SCHEMA_VERSION,
        "completed_at": normalized_settings["migration_v13_completed_at"],
        "counts": counts,
        "encrypted_legacy_source_keys": migrated_secret_ids,
    }
    changes = {
        settings_path: normalized_settings,
        frontier_path: frontier,
        profile_path: migrated_profile,
        runtime_path: runtime,
    }
    apply_json_transaction(changes, action_key="schema-migration-v13")
    # Byte-level parsing after the commit gives startup a clear rollback
    # boundary; the action transaction has already preserved originals.
    for path in changes:
        loaded = _read(path, None)
        if loaded is None:
            raise RuntimeError(f"v13 迁移后校验失败：{path.name}")
    return {
        "migrated": True,
        "schema_version": SCHEMA_VERSION,
        "counts": counts,
        "encrypted_legacy_source_keys": migrated_secret_ids,
        "backup_created": bool(create_pre_migration_backup),
    }

