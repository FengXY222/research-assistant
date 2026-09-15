"""Small-widget regression contracts for v11.5 cross-page refinements."""

from __future__ import annotations

import os
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from tests import _data_root  # noqa: F401 - isolate persistence before UI imports
from ui.home_page import HomePage
from ui.journal_library_page import CompactJournalRow, JournalLibraryPage
from ui.todo_page import QuadrantDropZone, TodoPage
from ui.research_profile_dialog import ResearchProfileDialog


class V115CrossPageUiTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_home_stacks_task_and_paper_status_cards_in_a_narrow_widget(self) -> None:
        page = HomePage()
        page.resize(520, 780)
        page._render_today_tasks(
            [
                {
                    "id": f"task-{index}",
                    "title": f"较长的今日任务 {index}，用于验证任务增多时论文状态不会被挤成窄条",
                    "done": False,
                    "quadrant": "urgent_important",
                }
                for index in range(8)
            ]
        )
        page.show()
        self.application.processEvents()

        todo_card = page.todo_card[0]
        paper_card = page.paper_card[0]

        self.assertGreaterEqual(paper_card.width(), 360)
        self.assertGreaterEqual(paper_card.y(), todo_card.geometry().bottom() + 1)
        self.assertLessEqual(todo_card.width(), page.contentsRect().width())
        page.close()

    def test_long_press_priority_surface_supports_direct_quadrant_choice_in_a_narrow_widget(self) -> None:
        page = TodoPage()
        page.resize(380, 620)
        page.items = [{"id": "task-1", "title": "修改论文摘要", "done": False, "schedule_mode": "none"}]
        page._render()
        page.show()
        self.application.processEvents()

        from PySide6.QtWidgets import QPushButton

        priority = page.list_widget.findChild(QPushButton, "todoPriorityButton")
        self.assertIsNotNone(priority)
        assert priority is not None
        page._show_quadrant_overlay(priority)
        self.application.processEvents()

        overlay = page.quadrant_overlay
        self.assertTrue(overlay.isVisible())
        self.assertLessEqual(overlay.width(), page.width() - 16)
        urgent = next(zone for zone in overlay.findChildren(QuadrantDropZone) if zone.key == "urgent_important")
        urgent.choose()
        self.application.processEvents()

        self.assertEqual(page.items[0]["quadrant"], "urgent_important")
        self.assertTrue(overlay.isHidden())
        page.close()

    def test_compact_journal_row_allows_name_selection_and_uses_status_colour_roles(self) -> None:
        verified = CompactJournalRow(
            {
                "id": "verified",
                "name": "Journal of Long Environmental Research",
                "publisher": "Elsevier",
                "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
                "easyscholar": {"cas_upgrade": "2区"},
            }
        )
        pending = CompactJournalRow(
            {"id": "pending", "name": "Pending Journal", "publisher": "Wiley", "jcr": {"status": "pending", "metrics": []}}
        )
        for row in (verified, pending):
            row.resize(460, 56)
            row.show()
        self.application.processEvents()

        name = verified.findChild(QLabel, "journalCopyName")
        verified_badge = verified.findChild(QLabel, "journalHealthChip")
        pending_badge = pending.findChild(QLabel, "journalHealthChip")
        cas = verified.findChild(QLabel, "journalCasBadge")

        self.assertIsNotNone(name)
        self.assertTrue(name.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse)
        self.assertEqual(verified_badge.text(), "Q1")
        self.assertEqual(verified_badge.property("verification"), "verified")
        self.assertEqual(pending_badge.text(), "待核验")
        self.assertEqual(pending_badge.property("verification"), "pending")
        self.assertEqual(cas.text(), "中科院 2区")
        self.assertNotIn("…", cas.text())
        verified.close()
        pending.close()

    def test_journal_library_toolbar_and_rows_fit_a_380_pixel_widget(self) -> None:
        page = JournalLibraryPage()
        page.resize(380, 640)
        page.journals = [
            {
                "id": "journal-1",
                "name": "Journal of Very Long Environmental Research Name",
                "publisher": "Elsevier",
                "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
                "easyscholar": {"cas_upgrade": "2区"},
            }
        ]
        page.usage = {}
        page._render()
        page.show()
        self.application.processEvents()

        row = page.scroll.widget().findChild(CompactJournalRow)
        self.assertLessEqual(page.minimumSizeHint().width(), 380)
        self.assertLessEqual(page.scroll.widget().width(), page.scroll.viewport().width() + 1)
        self.assertLessEqual(row.width(), page.scroll.viewport().width() + 1)
        self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
        page.close()

    def test_research_profile_does_not_render_an_empty_evidence_placeholder(self) -> None:
        dialog = ResearchProfileDialog({"terms": [{"id": "soc", "text": "soil organic carbon", "weight": 100, "locked": True, "evidence": []}]})
        dialog.show()
        self.application.processEvents()

        evidence = dialog.findChildren(QLabel, "profileEvidence")
        self.assertFalse(evidence)
        dialog.close()
