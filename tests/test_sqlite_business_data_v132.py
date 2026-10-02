from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from ui.journal_library_page import JournalItemDelegate, JournalListModel
from ui.theme import build_application_palette
from utils.app_data_store import (
    AppDataStore,
    compact_deadline_candidates,
    migrate_legacy_app_data,
)
from utils.database_maintenance import run_database_maintenance
from utils.evidence_cache import EvidenceCache
from utils import file_manager


def test_deadline_candidates_ignore_fetch_time_and_keep_latest_evidence() -> None:
    rows = [
        {
            "deadline": "2027-01-01",
            "source": "publisher",
            "fetched_at": f"2026-09-{day:02d}",
            "source_evidence": [{"source": "publisher", "url": "https://example.org/cfp", "version": day}],
        }
        for day in range(1, 21)
    ]

    compacted = compact_deadline_candidates(rows)

    assert len(compacted) == 1
    assert compacted[0]["first_seen_at"] == "2026-09-01"
    assert compacted[0]["last_seen_at"] == "2026-09-20"
    assert compacted[0]["source_evidence"][0]["version"] == 20


def test_sqlite_business_store_splits_special_issue_data_and_queries_overview(tmp_path) -> None:
    store = AppDataStore(tmp_path / "research_assistant.sqlite")
    candidates = [
        {
            "deadline": f"2027-{month:02d}-01",
            "source": "publisher",
            "fetched_at": f"2026-09-{month:02d}",
            "source_evidence": [{"source": "publisher", "url": f"https://example.org/{month}"}],
        }
        for month in range(1, 13)
    ]
    store.save_special_issues(
        {
            "source_checkpoints": {"publisher": {"state": "success", "updated_at": "2026-09-25"}},
            "items": [
                {
                    "id": "visible",
                    "title": "Visible issue",
                    "candidate_state": "visible",
                    "scoring_version": "special-issue-dual-axis-13.0",
                    "scope_text": "soil carbon and land use",
                    "scope_status": "full",
                    "scope_is_complete": True,
                    "source_evidence": [{"source": "publisher", "url": "https://example.org/visible"}],
                    "deadline_candidates": candidates,
                },
                {"id": "saved", "title": "Saved issue", "status": "saved", "saved": True},
                {"id": "cold", "title": "Cold history"},
            ],
        }
    )

    overview = store.load_special_overview()
    complete = store.load_special_issues()

    assert overview is not None
    assert overview["total_item_count"] == 3
    assert {item["id"] for item in overview["items"]} == {"visible", "saved"}
    assert complete is not None and len(complete["items"]) == 3
    visible = next(item for item in complete["items"] if item["id"] == "visible")
    assert visible["scope_text"] == "soil carbon and land use"
    assert len(visible["deadline_candidates"]) == 10

    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM app_special_issue_scopes").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM app_special_issue_sources").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM app_special_deadline_candidates").fetchone()[0] == 10
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_incremental_special_refresh_loads_only_missing_ai_and_preserves_user_state(tmp_path) -> None:
    store = AppDataStore(tmp_path / "research_assistant.sqlite")
    complete_axes = {
        "axes": {
            "relevance": {"adjustment": 10, "confidence": "high", "evidence_refs": ["title"]},
            "opportunity": {"adjustment": 8, "confidence": "medium", "evidence_refs": ["deadline"]},
        }
    }
    store.save_special_issues(
        {
            "last_refresh_status": "partial",
            "source_checkpoints": {"failed-source": {"status": "failed"}},
            "items": [
                {
                    "id": "needs-ai",
                    "dedupe_key": "call:needs-ai",
                    "title": "Soil carbon",
                    "scoring_version": "special-issue-dual-axis-13.0",
                    "status": "saved",
                    "saved": True,
                    "match": {},
                },
                {
                    "id": "complete",
                    "dedupe_key": "call:complete",
                    "title": "Land systems",
                    "scoring_version": "special-issue-dual-axis-13.0",
                    "match": {"ai_axis_payload": complete_axes},
                },
                *[
                    {"id": f"cold-{index}", "title": f"Cold {index}"}
                    for index in range(600)
                ],
            ],
        }
    )

    refresh = store.begin_special_issue_refresh_incremental()

    assert refresh["total_item_count"] == 602
    assert [item["id"] for item in refresh["items"]] == ["needs-ai"]
    refreshed = dict(refresh["items"][0])
    refreshed["title"] = "Updated soil carbon"
    committed = store.commit_special_issue_refresh_incremental(
        {
            **refresh,
            "items": [refreshed],
            "last_refresh_status": "cancelled",
        },
        generation=refresh["refresh_generation"],
        state="cancelled",
    )

    assert committed["total_item_count"] == 602
    assert store.get_special_issue("needs-ai")["saved"] is True
    assert store.get_special_issue("needs-ai")["title"] == "Updated soil carbon"
    assert store.get_special_issue("cold-599")["title"] == "Cold 599"


def test_backup_hashing_never_uses_read_bytes(tmp_path, monkeypatch) -> None:
    original = file_manager.DATA_DIR
    try:
        file_manager._set_data_dir(tmp_path)
        file_manager.business_data_store().save_journals(
            [{"id": "j-1", "name": "Soil Research"}]
        )
        monkeypatch.setattr(
            Path,
            "read_bytes",
            lambda _path: (_ for _ in ()).throw(AssertionError("whole-file read")),
        )

        backup = file_manager.create_backup("streaming-hash-test")

        assert (backup / "manifest.json").is_file()
    finally:
        file_manager._set_data_dir(original)


def test_journal_model_fetches_in_batches_without_row_widgets() -> None:
    QApplication.instance() or QApplication([])
    model = JournalListModel(batch_size=40)
    model.set_entries(
        [{"kind": "journal", "journal": {"id": f"j-{index}", "name": f"Journal {index}"}} for index in range(112)]
    )

    assert model.rowCount() == 40
    assert model.canFetchMore()
    model.fetchMore()
    assert model.rowCount() == 80
    model.fetchMore()
    assert model.rowCount() == 112
    assert not model.canFetchMore()


def test_virtual_journal_model_keeps_manual_ordering() -> None:
    QApplication.instance() or QApplication([])
    model = JournalListModel(batch_size=40)
    model.set_entries(
        [
            {"kind": "group", "name": "全部期刊", "count": 3},
            *[
                {"kind": "journal", "journal": {"id": journal_id, "name": journal_id}}
                for journal_id in ("a", "b", "c")
            ],
        ],
        reorder_enabled=True,
    )
    orders: list[list[str]] = []
    model.order_changed.connect(orders.append)
    mime = model.mimeData([model.index(1, 0)])

    assert model.dropMimeData(mime, model.supportedDropActions(), 4, 0, model.index(-1, -1))
    assert orders[-1] == ["b", "c", "a"]


def test_virtual_journal_delegate_uses_application_surface_not_transparent_view_base() -> None:
    app = QApplication.instance() or QApplication([])
    app.setPalette(build_application_palette("fog_teal"))
    model = JournalListModel(batch_size=40)
    model.set_entries(
        [{"kind": "journal", "journal": {"id": "journal-1", "name": "Soil Research"}}]
    )
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 420, 68)
    option.font = app.font()
    option.state = QStyle.StateFlag.State_Enabled
    option.palette = QPalette(app.palette())
    # Reproduce the transparent-scroll-area palette that caused black cards.
    option.palette.setColor(QPalette.ColorRole.Base, QColor("#000000"))
    image = QImage(420, 68, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#EAF2F2"))
    painter = QPainter(image)
    JournalItemDelegate().paint(painter, option, model.index(0, 0))
    painter.end()

    card_colour = image.pixelColor(390, 50)
    assert card_colour.lightness() > 220


def test_legacy_business_tables_are_copied_before_cache_cleanup(tmp_path) -> None:
    cache_path = tmp_path / "research_intelligence.sqlite"
    business_path = tmp_path / "research_assistant.sqlite"
    legacy = AppDataStore(cache_path)
    legacy.save_journals([{"id": "j-1", "name": "Soil Research"}])
    legacy.save_special_issues(
        {"items": [{"id": "si-1", "dedupe_key": "call:soil", "title": "Soil carbon"}]}
    )

    migration = migrate_legacy_app_data(cache_path, business_path)
    repeated = migrate_legacy_app_data(cache_path, business_path)
    current = AppDataStore(business_path)

    assert migration["migrated"] is True
    assert repeated["migrated"] is False
    assert repeated["reason"] == "destination_current"
    assert current.dataset_counts()["journals"] == 1
    assert current.dataset_counts()["special_issues"] == 1
    assert current.special_issue_cache_keys() == {"si-1", "call:soil"}


def test_newer_legacy_compatibility_copy_is_reconciled_on_next_start(tmp_path) -> None:
    cache_path = tmp_path / "research_intelligence.sqlite"
    business_path = tmp_path / "research_assistant.sqlite"
    legacy = AppDataStore(cache_path)
    legacy.save_journals([{"id": "j-1", "name": "Original"}])
    assert migrate_legacy_app_data(cache_path, business_path)["migrated"] is True
    legacy.save_journals([{"id": "j-1", "name": "Changed by installed release"}])
    with sqlite3.connect(cache_path) as connection:
        connection.execute(
            "UPDATE app_datasets SET updated_at='2099-01-01T00:00:00' WHERE name='journals'"
        )

    result = migrate_legacy_app_data(cache_path, business_path)

    assert result["migrated"] is True
    assert result["reason"] == "source_newer"
    assert AppDataStore(business_path).load_journals()[0]["name"] == "Changed by installed release"


def test_idle_maintenance_prunes_expired_and_orphaned_cache_rows(tmp_path) -> None:
    cache_path = tmp_path / "research_intelligence.sqlite"
    business_path = tmp_path / "research_assistant.sqlite"
    store = AppDataStore(business_path)
    store.save_special_issues(
        {"items": [{"id": "si-current", "dedupe_key": "call:current", "title": "Current"}]}
    )
    cache = EvidenceCache(cache_path)
    cache.initialize()
    now = datetime(2026, 9, 30, 12, 0, 0)
    cache.put_source_response(
        "openalex",
        "expired",
        {"items": [1]},
        (now - timedelta(days=2)).isoformat(),
        (now - timedelta(days=1)).isoformat(),
    )
    cache.put_source_response(
        "openalex",
        "active",
        {"items": [2]},
        now.isoformat(),
        (now + timedelta(days=1)).isoformat(),
    )
    cache.put_special_issue_discovery("call:current", {"title": "Current"})
    cache.put_special_issue_discovery("call:orphan", {"title": "Orphan"})

    result = run_database_maintenance(cache_path, business_path, now=now)

    assert result["expired_source_responses"] == 1
    assert result["stale_special_discoveries"] == 1
    assert cache.get_source_response("openalex", "expired", now=now.isoformat()) is None
    assert cache.get_source_response("openalex", "active", now=now.isoformat()) == {"items": [2]}
    assert cache.get_special_issue_discovery("call:current") == {"title": "Current"}
    assert cache.get_special_issue_discovery("call:orphan") is None
    assert result["business_quick_check"] == "ok"
    assert result["cache_quick_check"] == "ok"


def test_idle_maintenance_drops_legacy_app_tables_only_after_matching_copy(tmp_path) -> None:
    cache_path = tmp_path / "research_intelligence.sqlite"
    business_path = tmp_path / "research_assistant.sqlite"
    AppDataStore(cache_path).save_journals([{"id": "j-1", "name": "Soil Research"}])

    result = run_database_maintenance(cache_path, business_path)

    assert result["legacy_app_tables_dropped"] > 0
    assert AppDataStore(business_path).dataset_counts()["journals"] == 1
    with sqlite3.connect(cache_path) as connection:
        remaining = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name LIKE 'app_%'"
        ).fetchone()[0]
    assert remaining == 0


def test_portable_backup_keeps_business_database_but_excludes_rebuildable_cache(tmp_path) -> None:
    original = file_manager.DATA_DIR
    try:
        file_manager._set_data_dir(tmp_path)
        file_manager.business_data_store().save_journals(
            [{"id": "j-1", "name": "Soil Research"}]
        )
        cache = EvidenceCache(file_manager.RESEARCH_INTELLIGENCE_CACHE_FILE)
        cache.initialize()
        cache.put_source_response(
            "openalex", "query", {"items": [1]}, "2026-09-30", "2026-10-01"
        )

        backup = file_manager.create_backup("split-database-test")
        manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))

        assert file_manager.BUSINESS_DATA_FILE.name in manifest["files"]
        assert file_manager.RESEARCH_INTELLIGENCE_CACHE_FILE.name not in manifest["files"]
        restored_copy = AppDataStore(backup / file_manager.BUSINESS_DATA_FILE.name)
        assert restored_copy.dataset_counts()["journals"] == 1
    finally:
        file_manager._set_data_dir(original)
