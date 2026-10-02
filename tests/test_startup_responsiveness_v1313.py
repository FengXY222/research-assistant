"""Startup must paint before large local stores and optional pages are loaded."""

from __future__ import annotations

import ast
import time
from pathlib import Path

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QProgressBar

from tests import _data_root  # noqa: F401 - bind isolated persistence first
from main import StartupWindow


PROJECT = Path(__file__).resolve().parents[1]


def test_main_window_module_is_not_imported_at_entrypoint_import_time() -> None:
    tree = ast.parse((PROJECT / "main.py").read_text(encoding="utf-8"))
    top_level_imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert all(
        not (isinstance(node, ast.ImportFrom) and node.module == "ui.main_window")
        for node in top_level_imports
    )


def test_daily_backup_is_deferred_to_idle_maintenance() -> None:
    entrypoint = (PROJECT / "main.py").read_text(encoding="utf-8")
    main_window = (PROJECT / "ui" / "main_window.py").read_text(encoding="utf-8")
    constructor = main_window.split("class MainWindow", 1)[1].split("    def _build_ui", 1)[0]
    assert "maybe_create_daily_backup" not in entrypoint
    assert "class IdleBackupThread" in main_window
    assert "maybe_create_daily_backup(" in main_window
    assert "cancelled=self.isInterruptionRequested" in main_window
    assert "_maintenance_idle_timer" in constructor
    assert "maybe_create_daily_backup(self.settings)" not in constructor


def test_optional_pages_are_not_imported_with_the_main_window_shell() -> None:
    tree = ast.parse((PROJECT / "ui" / "main_window.py").read_text(encoding="utf-8"))
    imported_modules = {
        node.module
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and isinstance(node.module, str)
    }
    assert "ui.home_page" in imported_modules
    assert not imported_modules.intersection(
        {
            "ui.todo_page",
            "ui.paper_page",
            "ui.notes_page",
            "ui.journal_library_page",
            "ui.frontier_page",
            "ui.special_issue_page",
            "ui.achievements_page",
        }
    )


def test_packager_keeps_dynamically_imported_pages() -> None:
    build_script = (PROJECT / "build_windows.ps1").read_text(encoding="utf-8")
    for module_name, _class_name in (
        ("ui.todo_page", "TodoPage"),
        ("ui.paper_page", "PaperPage"),
        ("ui.notes_page", "NotesPage"),
        ("ui.journal_library_page", "JournalLibraryPage"),
        ("ui.frontier_page", "DailyFrontierPage"),
        ("ui.special_issue_page", "SpecialIssuePage"),
        ("ui.achievements_page", "AchievementsPage"),
    ):
        assert f'"--hidden-import", "{module_name}"' in build_script


def test_startup_window_exposes_indeterminate_progress_immediately() -> None:
    app = QApplication.instance() or QApplication([])
    window = StartupWindow()
    progress = window.findChild(QProgressBar)
    assert progress is not None
    assert progress.minimum() == 0
    assert progress.maximum() == 0
    assert window.status_label.text()
    window.close()
    app.processEvents()


def test_main_window_starts_with_home_only_and_replaces_lazy_page_in_place() -> None:
    from ui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    assert set(window._loaded_pages) == {"home"}
    window.navigate("journals")
    assert "journals" not in window._loaded_pages
    deadline = time.monotonic() + 5
    while "journals" not in window._loaded_pages and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert "journals" in window._loaded_pages
    assert window.workbench_shell.pages["journals"] is window._loaded_pages["journals"]
    window.close()
    app.processEvents()
