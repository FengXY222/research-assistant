"""Migrate active large JSON stores into the durable ``app_*`` SQLite schema."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


LEGACY_FILES = (
    "frontier.json",
    "journals.json",
    "special_issues.json",
    "special_issues_summary.json",
    "runtime_v13.json",
    "selection_feedback.json",
)


def _json_count(path: Path, key: str = "items") -> int:
    if not path.is_file():
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return 0
    if isinstance(payload, list):
        return len(payload)
    values = payload.get(key, []) if isinstance(payload, dict) else []
    return len(values) if isinstance(values, (list, dict)) else 0


def migrate(data_root: Path, *, archive_legacy: bool = True) -> dict[str, object]:
    root = data_root.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["RESEARCH_ASSISTANT_DATA_DIR"] = str(root)

    from utils import file_manager
    from utils.special_issue_repository import load_special_issue_overview, load_special_issue_store

    file_manager._set_data_dir(root)
    source_counts = {
        "frontier": _json_count(root / "frontier.json"),
        "journals": _json_count(root / "journals.json", key="items"),
        "special_issues": _json_count(root / "special_issues.json"),
        "runtime": _json_count(root / "runtime_v13.json", key="batches"),
        "selection_feedback": _json_count(root / "selection_feedback.json", key="entries"),
    }
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    archive = root / "legacy-json-archive" / stamp
    runtime_source = root / "runtime_v13.json"
    if archive_legacy and runtime_source.is_file():
        archive.mkdir(parents=True, exist_ok=False)
        shutil.copy2(runtime_source, archive / runtime_source.name)

    frontier = file_manager.load_frontier_data()
    journals = file_manager.load_journal_library()
    special = load_special_issue_store()
    runtime = file_manager.load_v13_runtime()
    selection_feedback = file_manager.load_journal_selection_feedback()
    overview = load_special_issue_overview()
    store = file_manager.business_data_store()
    database_counts = store.dataset_counts()

    expected = {
        "frontier": len(frontier.get("items", [])),
        "journals": len(journals),
        "special_issues": len(special.get("items", [])),
        "runtime": len(runtime.get("batches", {})),
        "selection_feedback": len(selection_feedback.get("entries", {})),
    }
    mismatches = {
        key: {"expected": value, "database": database_counts.get(key, -1)}
        for key, value in expected.items()
        if database_counts.get(key) != value
    }
    connection = sqlite3.connect(file_manager.BUSINESS_DATA_FILE, timeout=30.0)
    try:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        max_candidates = int(
            connection.execute(
                """SELECT COALESCE(MAX(candidate_count), 0) FROM (
                       SELECT COUNT(*) AS candidate_count
                       FROM app_special_deadline_candidates GROUP BY issue_id
                   )"""
            ).fetchone()[0]
        )
    finally:
        connection.close()
    if integrity != "ok" or mismatches or max_candidates > 10:
        raise RuntimeError(
            f"迁移校验失败: integrity={integrity}, mismatches={mismatches}, max_candidates={max_candidates}"
        )

    if archive_legacy:
        archive.mkdir(parents=True, exist_ok=True)
        for name in LEGACY_FILES:
            source = root / name
            destination = archive / name
            if not source.is_file():
                continue
            if name == "runtime_v13.json" and destination.exists():
                source.unlink()
            else:
                os.replace(source, destination)
            source.write_text(
                json.dumps(
                    {
                        "migrated_to": file_manager.BUSINESS_DATA_FILE.name,
                        "migrated_at": datetime.now().isoformat(timespec="seconds"),
                        "legacy_archive": str(destination.relative_to(root)),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
        (archive / "migration_report.json").write_text(
            json.dumps(
                {
                    "source_counts": source_counts,
                    "database_counts": database_counts,
                    "overview_count": len(overview.get("items", [])),
                    "integrity": integrity,
                    "max_deadline_candidates_per_issue": max_candidates,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return {
        "data_root": str(root),
        "database": str(file_manager.BUSINESS_DATA_FILE),
        "source_counts": source_counts,
        "database_counts": database_counts,
        "overview_count": len(overview.get("items", [])),
        "integrity": integrity,
        "max_deadline_candidates_per_issue": max_candidates,
        "archive": str(archive) if archive_legacy else "",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--keep-json-active", action="store_true")
    arguments = parser.parse_args()
    result = migrate(arguments.data_root, archive_legacy=not arguments.keep_json_active)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
