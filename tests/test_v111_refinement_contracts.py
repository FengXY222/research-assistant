"""Focused v11.1 regression contracts for the compact workbench refinements."""

from __future__ import annotations

from datetime import date
import os
from copy import deepcopy
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QFrame, QToolButton

from tests import _data_root  # noqa: F401 - isolate persistence before UI imports
from ui.home_page import HomePage, today_next_actions
from ui.main_window import MainWindow
from ui.paper_page import JournalHistoryRow, PaperPage
from ui.theme import THEME_REGISTRY, apply_application_theme
from ui.todo_page import TodoPage
from ui.workbench_shell import resolve_route
from utils.file_manager import save_papers, save_todos


class V111RefinementContracts(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_reapplying_the_same_theme_reports_a_noop_without_rebuilding_qss(self) -> None:
        apply_application_theme(self.application, "fog_teal", "comfortable")
        before = self.application.styleSheet()

        changed = apply_application_theme(self.application, "fog_teal", "comfortable")

        self.assertIs(changed, False)
        self.assertEqual(self.application.styleSheet(), before)

    def test_theme_picker_offers_contrasting_non_monochrome_palettes(self) -> None:
        self.assertTrue({"cinnabar_paper", "violet_grove", "night_coral"}.issubset(THEME_REGISTRY))

    def test_unchanged_settings_save_skips_expensive_window_reconfiguration(self) -> None:
        window = MainWindow()
        updated = deepcopy(window.settings)
        updated["auto_backup"] = False
        calls: list[str] = []
        window._apply_theme = lambda *_args: calls.append("theme")
        window._apply_application_mode = lambda *_args, **_kwargs: calls.append("mode")
        window._set_always_on_top = lambda *_args, **_kwargs: calls.append("top")
        window._set_click_through = lambda *_args, **_kwargs: calls.append("through")
        window._set_sidebar_position = lambda *_args, **_kwargs: calls.append("sidebar")
        window._set_sidebar_auto_hide = lambda *_args, **_kwargs: calls.append("autohide")
        window._apply_global_journal_import_shortcut = lambda *_args, **_kwargs: calls.append("journal-hotkey")
        window._apply_global_visibility_shortcut = lambda *_args, **_kwargs: calls.append("visibility-hotkey")
        window._start_frontier_checks = lambda: calls.append("frontier-checks")
        window._check_submission_reminders = lambda: calls.append("reminders")
        window._save_settings = lambda: calls.append("save")
        window.setWindowOpacity = lambda *_args: calls.append("opacity")

        window._apply_settings(updated)

        self.assertEqual(calls, ["save"])
        if window.tray_icon:
            window.tray_icon.hide()
        window._global_hotkey.close()
        window._visibility_hotkey.close()
        window.deleteLater()

    def test_library_route_opens_daily_frontier_by_default(self) -> None:
        self.assertEqual(resolve_route("library").anchor, "frontier")

    def test_paper_next_action_keeps_its_paper_and_journal_identity(self) -> None:
        actions = today_next_actions(
            date(2026, 8, 21),
            [],
            [
                {
                    "id": "paper-001",
                    "title": "SOC mapping",
                    "journals": [
                        {
                            "id": "journal-001",
                            "name": "CATENA",
                            "status": "修改中",
                            "revision_due_date": "2026-08-20",
                            "status_updated_at": "2026-08-01",
                        }
                    ],
                }
            ],
            reminder_state={},
        )

        action = next(item for item in actions if item.get("route") == "papers")
        self.assertIn("paper_id", action)
        self.assertIn("journal_id", action)
        self.assertEqual(action["paper_id"], "paper-001")
        self.assertEqual(action["journal_id"], "journal-001")

    def test_home_today_card_shows_specific_unfinished_tasks(self) -> None:
        today = date.today()
        save_todos(
            today,
            [
                {"id": "task-1", "title": "修改摘要", "done": False, "quadrant": "urgent_important"},
                {"id": "task-2", "title": "整理图件", "done": True},
            ],
        )
        page = HomePage()
        page.refresh()

        preview = page.findChild(QLabel, "todayTaskPreview")
        self.assertIsNotNone(preview)
        self.assertIn("修改摘要", preview.text())
        self.assertNotIn("整理图件", preview.text())
        page.close()
        save_todos(today, [])

    def test_notes_preview_footer_has_room_for_view_and_edit_text(self) -> None:
        page = HomePage()
        links = [button for button in page.findChildren(QPushButton) if button.text() == "查看与编辑"]

        self.assertEqual(len(links), 2)
        self.assertTrue(all(button.minimumHeight() >= 24 for button in links))
        page.close()

    def test_work_tasks_offer_a_direct_priority_control_before_dragging(self) -> None:
        page = TodoPage()
        page.items = [{"id": "task-1", "title": "修改摘要", "done": False, "schedule_mode": "none"}]
        page._render()

        priority_button = page.list_widget.findChild(QPushButton, "todoPriorityButton")
        self.assertIsNotNone(priority_button)
        self.assertIn("优先级", priority_button.toolTip())
        page.close()

    def test_rejection_archive_is_a_corner_control_not_a_full_width_panel(self) -> None:
        page = PaperPage()

        self.assertIsNotNone(page.findChild(QToolButton, "archiveCornerButton"))
        self.assertIsNone(page.findChild(QFrame, "rejectionArchiveFrame"))
        page.close()

    def test_home_paper_action_can_reveal_and_highlight_its_exact_journal(self) -> None:
        save_papers(
            [
                {
                    "id": "paper-focus",
                    "title": "SOC mapping",
                    "journals": [{"id": "journal-focus", "name": "CATENA", "status": "外审中", "date": "2026-08-01"}],
                }
            ]
        )
        page = PaperPage()

        self.assertTrue(callable(getattr(page, "reveal_journal", None)))
        page.reveal_journal("paper-focus", "journal-focus")
        self.application.processEvents()
        target = next(
            row for row in page.findChildren(JournalHistoryRow)
            if str(row.property("journal_id")) == "journal-focus"
        )
        self.assertTrue(bool(target.property("attention")))
        page.close()
