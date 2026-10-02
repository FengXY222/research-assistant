"""Idle-only maintenance for durable data and rebuildable SQLite caches."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from utils.app_data_store import AppDataStore, drop_legacy_app_tables, migrate_legacy_app_data
from utils.evidence_cache import EvidenceCache


_VACUUM_FREE_BYTES = 32 * 1024 * 1024
_VACUUM_FREE_RATIO = 0.15
_VACUUM_MIN_INTERVAL = timedelta(days=7)


def _quick_check(path: Path) -> str:
    connection = sqlite3.connect(path, timeout=30.0)
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        row = connection.execute("PRAGMA quick_check").fetchone()
        return str(row[0]) if row else ""
    finally:
        connection.close()


def _app_dataset_counts(path: Path) -> dict[str, int] | None:
    if not path.is_file():
        return None
    connection = sqlite3.connect(path, timeout=30.0)
    try:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_datasets'"
        ).fetchone()
        if not exists:
            return None
        return {
            str(name): int(count)
            for name, count in connection.execute(
                "SELECT name,item_count FROM app_datasets ORDER BY name"
            )
        }
    finally:
        connection.close()


def _storage_stats(path: Path) -> dict[str, int | float]:
    connection = sqlite3.connect(path, timeout=30.0)
    try:
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        free_pages = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
    finally:
        connection.close()
    total_bytes = page_count * page_size
    free_bytes = free_pages * page_size
    return {
        "page_count": page_count,
        "free_pages": free_pages,
        "page_size": page_size,
        "total_bytes": total_bytes,
        "free_bytes": free_bytes,
        "free_ratio": (free_bytes / total_bytes) if total_bytes else 0.0,
    }


def _parse_timestamp(value: object) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _checkpoint_and_compact(
    path: Path,
    *,
    force: bool,
    last_vacuum_at: object = "",
    now: datetime,
    cancelled: Any = None,
) -> dict[str, Any]:
    before = _storage_stats(path)
    connection = sqlite3.connect(path, timeout=120.0)
    try:
        connection.execute("PRAGMA busy_timeout=120000")
        connection.set_progress_handler(
            lambda: 1 if cancelled and cancelled() else 0,
            1000,
        )
        connection.execute("PRAGMA optimize")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()

    last_vacuum = _parse_timestamp(last_vacuum_at)
    interval_elapsed = last_vacuum is None or now - last_vacuum >= _VACUUM_MIN_INTERVAL
    worth_compacting = (
        int(before["free_bytes"]) >= _VACUUM_FREE_BYTES
        and float(before["free_ratio"]) >= _VACUUM_FREE_RATIO
    )
    vacuumed = bool(force or (interval_elapsed and worth_compacting))
    if vacuumed:
        connection = sqlite3.connect(path, timeout=300.0)
        try:
            connection.execute("PRAGMA busy_timeout=300000")
            connection.set_progress_handler(
                lambda: 1 if cancelled and cancelled() else 0,
                1000,
            )
            connection.execute("VACUUM")
        finally:
            connection.close()
    return {"before": before, "after": _storage_stats(path), "vacuumed": vacuumed}


def run_database_maintenance(
    cache_path: str | Path,
    business_path: str | Path,
    *,
    force_compact: bool = False,
    now: datetime | None = None,
    cancelled: Any = None,
) -> dict[str, Any]:
    """Prune disposable rows and reclaim space while the application is idle.

    Legacy ``app_*`` tables are removed from the cache only after their dataset
    counts match the dedicated durable database and both files pass a quick
    integrity check.
    """

    current = now or datetime.now()
    if cancelled and cancelled():
        raise InterruptedError("数据库维护已因用户恢复操作而暂停")
    cache_file = Path(cache_path).resolve()
    business_file = Path(business_path).resolve()
    reconciliation = migrate_legacy_app_data(cache_file, business_file)
    store = AppDataStore(business_file)
    cache = EvidenceCache(cache_file, cancelled=cancelled)
    cache.initialize()

    previous = store.load_named_payload("database_maintenance", {})
    previous = previous if isinstance(previous, dict) else {}
    result: dict[str, Any] = {
        "started_at": current.isoformat(timespec="seconds"),
        "expired_source_responses": cache.prune_expired_source_responses(
            now=current.isoformat(timespec="seconds")
        ),
        "stale_special_discoveries": 0,
        "legacy_app_tables_dropped": 0,
        "legacy_reconciliation": reconciliation.get("reason", ""),
        "expired_rebuildable_records": cache.prune_rebuildable_records(now=current),
    }

    if cancelled and cancelled():
        raise InterruptedError("数据库维护已因用户恢复操作而暂停")

    if store.has_dataset("special_issues"):
        result["stale_special_discoveries"] = cache.prune_special_issue_discoveries(
            store.special_issue_cache_keys()
        )

    business_check = _quick_check(business_file)
    cache_check = cache.quick_check()
    result["business_quick_check"] = business_check
    result["cache_quick_check"] = cache_check
    if business_check != "ok" or cache_check != "ok":
        raise sqlite3.DatabaseError(
            f"database quick check failed: business={business_check}, cache={cache_check}"
        )

    legacy_counts = _app_dataset_counts(cache_file)
    business_counts = store.dataset_counts()
    if legacy_counts is not None:
        if legacy_counts == business_counts:
            result["legacy_app_tables_dropped"] = drop_legacy_app_tables(cache_file)
        else:
            result["legacy_app_tables_retained"] = True

    result["cache_storage"] = _checkpoint_and_compact(
        cache_file,
        force=force_compact,
        last_vacuum_at=previous.get("cache_vacuum_at", ""),
        now=current,
        cancelled=cancelled,
    )
    result["business_storage"] = _checkpoint_and_compact(
        business_file,
        force=False,
        last_vacuum_at=previous.get("business_vacuum_at", ""),
        now=current,
        cancelled=cancelled,
    )
    result["completed_at"] = datetime.now().isoformat(timespec="seconds")
    store.save_named_payload(
        "database_maintenance",
        {
            "last_completed_at": result["completed_at"],
            "cache_vacuum_at": result["completed_at"]
            if result["cache_storage"]["vacuumed"]
            else previous.get("cache_vacuum_at", ""),
            "business_vacuum_at": result["completed_at"]
            if result["business_storage"]["vacuumed"]
            else previous.get("business_vacuum_at", ""),
            "last_result": {
                "expired_source_responses": result["expired_source_responses"],
                "stale_special_discoveries": result["stale_special_discoveries"],
                "legacy_app_tables_dropped": result["legacy_app_tables_dropped"],
            },
        },
        item_count=1,
    )
    return result
