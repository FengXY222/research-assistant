"""Regression guards for the v13.1 responsiveness and memory fixes."""

from __future__ import annotations

import os
import json
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtCore import QParallelAnimationGroup, QThread, Qt
from PySide6.QtWidgets import QApplication, QGraphicsEffect, QPushButton

from tests import _data_root  # noqa: F401 - isolate persistence before imports
from ui.journal_library_page import JournalLibraryPage
from ui.main_window import MainWindow
from utils import file_manager
from utils.special_issue_repository import load_special_issue_overview, save_special_issue_store
from utils.v13_pipeline import begin_batch, checkpoint, record_batch_sources, trim_batches


class _CountingJournalPage(JournalLibraryPage):
    def __init__(self) -> None:
        self.render_count = 0
        super().__init__()

    def _render(self) -> None:
        self.render_count += 1
        super()._render()


def test_journal_search_coalesces_rapid_keystrokes() -> None:
    app = QApplication.instance() or QApplication([])
    page = _CountingJournalPage()
    initial = page.render_count

    for text in ("s", "so", "soi", "soil"):
        page.search_edit.setText(text)

    assert page.render_count == initial
    assert page._search_render_timer.isActive()
    QTest.qWait(300)
    app.processEvents()
    assert page.render_count == initial + 1
    page.close()


def test_journal_save_does_not_reload_the_complete_library(monkeypatch) -> None:
    QApplication.instance() or QApplication([])
    page = JournalLibraryPage()
    monkeypatch.setattr("ui.journal_library_page.save_journal_library", lambda _rows: None)
    monkeypatch.setattr(page, "reload", lambda: (_ for _ in ()).throw(AssertionError("unexpected reload")))

    page._save()

    page.close()


def test_journal_changes_refresh_dependent_pages_only_when_opened() -> None:
    QApplication.instance() or QApplication([])
    window = MainWindow()
    calls: list[str] = []
    window.paper_page.reload = lambda: calls.append("papers")
    window.frontier_page.request_reload = lambda: calls.append("frontier")

    window._on_journal_data_changed()

    assert calls == []
    assert {"papers", "frontier"}.issubset(window._dirty_pages)
    window.navigate("frontier")
    assert calls == ["frontier"]
    assert "frontier" not in window._dirty_pages
    window.navigate("papers")
    QApplication.processEvents()
    assert calls == ["frontier", "papers"]
    if window.tray_icon:
        window.tray_icon.hide()
    window._global_hotkey.close()
    window._visibility_hotkey.close()
    window.close()


def test_special_issue_overview_keeps_visible_and_personal_rows_without_discovery_bulk() -> None:
    repeated_evidence = [
        {"source": "aggregator", "source_url": f"https://example.org/{index}", "updated_at": "2026-09-24"}
        for index in range(120)
    ]
    save_special_issue_store(
        {
            "items": [
                {
                    "id": "visible",
                    "title": "Soil carbon",
                    "scoring_version": "special-issue-dual-axis-13.0",
                    "candidate_state": "visible",
                    "source_evidence": repeated_evidence,
                    "deadline_candidates": [{"deadline": "2027-01-01", "raw": "x" * 1000}] * 100,
                },
                {"id": "saved", "title": "Saved call", "status": "saved"},
                {"id": "cold", "title": "Historical unverified record"},
            ]
        }
    )

    overview = load_special_issue_overview()

    assert overview["total_item_count"] == 3
    assert {item["id"] for item in overview["items"]} == {"visible", "saved"}
    visible = next(item for item in overview["items"] if item["id"] == "visible")
    assert "deadline_candidates" not in visible
    assert len(visible["source_evidence"]) == 80
    assert not file_manager.SPECIAL_ISSUES_SUMMARY_FILE.is_file()
    assert file_manager.BUSINESS_DATA_FILE.is_file()


def test_runtime_discards_old_payload_but_keeps_failed_source_metadata() -> None:
    first_day = datetime(2026, 9, 20, 8)
    store, first_id, _ = begin_batch({}, task="daily_frontier", inputs={"profile": "x"}, now=first_day)
    store = checkpoint(
        store,
        first_id,
        "recall",
        state="success",
        payload={"items": [{"abstract": "x" * 10000}]},
        now=first_day,
    )
    store = record_batch_sources(store, first_id, successful=["openalex"], failed=["crossref"])
    second_day = datetime(2026, 9, 21, 8)
    store, second_id, _ = begin_batch(store, task="daily_frontier", inputs={"profile": "x"}, now=second_day)

    compact = trim_batches(store)

    assert compact["batches"][first_id]["failed_sources"] == ["crossref"]
    assert "payload" not in compact["batches"][first_id]["stages"]["recall"]
    assert second_id in compact["batches"]


def test_oversized_runtime_is_compacted_on_first_load() -> None:
    first_day = datetime(2026, 9, 20, 8)
    store, first_id, _ = begin_batch({}, task="daily_frontier", inputs={"profile": "x"}, now=first_day)
    store = checkpoint(
        store,
        first_id,
        "recall",
        state="success",
        payload={"items": [{"abstract": "x" * (9 * 1024 * 1024)}]},
        now=first_day,
    )
    store, _second_id, _ = begin_batch(
        store,
        task="daily_frontier",
        inputs={"profile": "x"},
        now=datetime(2026, 9, 21, 8),
    )
    file_manager.V13_RUNTIME_FILE.write_text(json.dumps(store), encoding="utf-8")
    assert file_manager.V13_RUNTIME_FILE.stat().st_size > 8 * 1024 * 1024

    loaded = file_manager.load_v13_runtime()

    assert "payload" not in loaded["batches"][first_id]["stages"]["recall"]
    assert file_manager.V13_RUNTIME_FILE.stat().st_size < 1024 * 1024


def test_achievements_lazy_page_cannot_remain_on_preparing_placeholder(tmp_path, monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    runtime_log = tmp_path / "runtime.log"
    monkeypatch.setattr(file_manager, "RUNTIME_LOG_FILE", runtime_log)
    window = MainWindow()

    window.navigate("papers", "results")
    QTest.qWait(120)
    app.processEvents()

    assert "achievements" in window._loaded_pages
    assert "achievements" not in window._page_loaders
    events = [json.loads(line)["event"] for line in runtime_log.read_text(encoding="utf-8").splitlines()]
    assert "lazy_page_requested" in events
    assert "lazy_page_ready" in events
    if window.tray_icon:
        window.tray_icon.hide()
    window._global_hotkey.close()
    window._visibility_hotkey.close()
    window.close()


def test_child_result_chip_routes_through_main_window_lazy_loader() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    result_chip = next(
        button
        for button in window.workbench_shell.findChildren(QPushButton)
        if button.property("workbench_anchor") == "results"
    )

    QTest.mouseClick(result_chip, Qt.MouseButton.LeftButton)
    QTest.qWait(120)
    app.processEvents()

    assert window.workbench_shell.current_route.anchor == "results"
    assert "achievements" in window._loaded_pages
    if window.tray_icon:
        window.tray_icon.hide()
    window._global_hotkey.close()
    window._visibility_hotkey.close()
    window.close()


def test_complex_page_switches_do_not_accumulate_full_page_animations() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    for _index in range(30):
        window.navigate("frontier")
        window.navigate("journals")
        window.navigate("special_issues")
        window.navigate("papers")
    app.processEvents()

    assert window.workbench_shell.findChildren(QParallelAnimationGroup) == []
    assert window.workbench_shell.findChildren(QGraphicsEffect) == []
    if window.tray_icon:
        window.tray_icon.hide()
    window._global_hotkey.close()
    window._visibility_hotkey.close()
    window.close()


def test_user_activity_cancels_tracked_idle_worker_even_after_queue_is_empty() -> None:
    class CooperativeWorker(QThread):
        def run(self) -> None:
            while not self.isInterruptionRequested():
                self.msleep(5)

    QApplication.instance() or QApplication([])
    window = MainWindow()
    worker = CooperativeWorker(window)
    window._idle_background_workers.add(worker)
    window._idle_tasks_running = False
    worker.start()

    window._cancel_idle_background_work()

    assert worker.wait(1000)
    if window.tray_icon:
        window.tray_icon.hide()
    window._global_hotkey.close()
    window._visibility_hotkey.close()
    window.close()


def test_finished_lazy_worker_without_signal_recovers_on_gui_thread() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    silent_loader = object()
    window._page_loaders["achievements"] = silent_loader
    window._page_load_started["achievements"] = 0.0

    window._page_import_finished("achievements", silent_loader)
    QTest.qWait(80)
    app.processEvents()

    assert "achievements" in window._loaded_pages
    assert "achievements" not in window._page_loaders
    if window.tray_icon:
        window.tray_icon.hide()
    window._global_hotkey.close()
    window._visibility_hotkey.close()
    window.close()
