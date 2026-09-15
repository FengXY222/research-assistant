from __future__ import annotations

import os
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QScrollArea

from tests import _data_root  # noqa: F401 - isolate persistence before UI imports
from ui.journal_library_page import CompactJournalRow
from ui.notes_page import InspirationRow, NotesPage, ReadingRow
from ui.settings_dialog import SettingsDialog
from ui.theme import apply_application_theme
from ui.todo_page import TodoPage


class V112LayoutTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        apply_application_theme(cls.application, "fog_teal", "comfortable")

    def test_todo_text_and_meta_have_explicit_readable_alignment_and_scale(self) -> None:
        page = TodoPage()
        page.resize(480, 600)
        page.items = [
            {
                "id": "task-1",
                "title": "给论文摘要做一版更详细的研究设计，并与合作者确认",
                "done": False,
                "schedule_mode": "none",
            }
        ]
        page._render()
        page.show()
        self.application.processEvents()

        title = page.list_widget.findChild(QLabel, "todoText")
        meta = page.list_widget.findChild(QLabel, "todoMeta")
        self.assertIsNotNone(title)
        self.assertIsNotNone(meta)
        assert title is not None and meta is not None
        self.assertTrue(title.alignment() & Qt.AlignmentFlag.AlignTop)
        self.assertTrue(meta.alignment() & Qt.AlignmentFlag.AlignVCenter)
        self.assertEqual(title.font().pixelSize(), 12)
        self.assertEqual(meta.font().pixelSize(), 10)
        self.assertLessEqual(title.height(), 40)
        page.close()

    def test_notes_rows_keep_titles_and_action_rows_compact_and_aligned(self) -> None:
        inspiration = InspirationRow({"id": "idea-1", "text": "比较多个环境变量对 SOC 制图精度的贡献。"})
        reading = ReadingRow(
            {
                "id": "reading-1",
                "title": "A paper with a deliberately long title for the reading inbox",
                "status": "未阅读",
                "reason": "方法参考",
            }
        )

        for row in (inspiration, reading):
            row.setFixedWidth(420)
            row.show()
            self.application.processEvents()
            title = row.findChild(QLabel, "noteItemTitle")
            self.assertIsNotNone(title)
            assert title is not None
            self.assertTrue(title.alignment() & Qt.AlignmentFlag.AlignTop)
            self.assertEqual(title.font().pixelSize(), 12)
            action_buttons = [
                button
                for button in row.findChildren(QPushButton)
                if button.objectName() in {"rowButton", "dangerButton"}
            ]
            self.assertTrue(action_buttons)
            self.assertTrue(all(button.minimumHeight() >= 22 for button in action_buttons))
            self.assertTrue(all(button.y() >= title.y() + title.height() - 2 for button in action_buttons))
            row.close()

    def test_notes_page_renders_without_horizontal_overflow_in_a_narrow_window(self) -> None:
        page = NotesPage()
        page.resize(360, 620)
        page.inspiration.items = [{"id": "idea-1", "text": "一条较长的科研灵感内容，用来验证换行和操作行。"}]
        page.inspiration._render()
        page.readings = [
            {
                "id": "reading-1",
                "title": "一篇较长的待读论文标题，用来验证窄窗口布局。",
                "status": "未阅读",
                "reason": "方法参考",
            }
        ]
        page._render_readings()
        page.show()
        self.application.processEvents()

        content = page.scroll.widget()
        self.assertIsNotNone(content)
        assert content is not None
        viewport_width = page.scroll.viewport().width()
        for child in content.findChildren(QLabel) + content.findChildren(QPushButton):
            if not child.isVisible():
                continue
            right = child.mapTo(content, child.rect().bottomRight()).x()
            self.assertLessEqual(right, content.width() + 1, child.objectName() or child.text())
            self.assertLessEqual(child.width(), max(1, viewport_width))
        page.close()

    def test_cas_badge_reserves_width_for_the_complete_division_label(self) -> None:
        row = CompactJournalRow(
            {
                "id": "soil",
                "name": "SOIL",
                "publisher": "Copernicus",
                "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
                "easyscholar": {"cas_upgrade": "2区"},
            }
        )
        row.resize(360, 52)
        row.show()
        self.application.processEvents()

        badge = row.findChild(QLabel, "journalCasBadge")
        self.assertIsNotNone(badge)
        assert badge is not None
        required = QFontMetrics(badge.font()).horizontalAdvance(badge.text()) + 10
        self.assertGreaterEqual(badge.width(), required)
        self.assertNotIn("…", badge.text())
        row.close()

    def test_settings_dialog_stays_inside_the_narrow_scroll_view(self) -> None:
        dialog = SettingsDialog({"appearance": {"theme_id": "fog_teal", "density": "comfortable"}})
        dialog.resize(350, 500)
        dialog.show()
        self.application.processEvents()

        scroll = dialog.findChild(QScrollArea)
        self.assertIsNotNone(scroll)
        assert scroll is not None
        content = scroll.widget()
        self.assertIsNotNone(content)
        assert content is not None
        self.assertGreater(content.width(), 0)
        self.assertLessEqual(content.width(), scroll.viewport().width() + 1)
        for child in content.findChildren(QLabel) + content.findChildren(QPushButton):
            if not child.isVisible():
                continue
            right = child.mapTo(content, child.rect().bottomRight()).x()
            self.assertLessEqual(right, content.width() + 1, child.objectName() or child.text())
        self.assertGreaterEqual(dialog.theme_selector.width(), 120)
        self.assertGreaterEqual(dialog.density_selector.width(), 120)
        dialog.close()
