"""Responsive empty-state layout contracts for the WORK task pane."""

from __future__ import annotations

import os
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from tests import _data_root  # noqa: F401 - isolate persistence before UI imports
from ui.todo_page import TodoPage


class TodoLayoutV11Tests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_empty_task_day_does_not_leave_the_hidden_list_as_an_expanding_gap(self) -> None:
        page = TodoPage()
        page.items = []
        page._render()

        self.assertTrue(page.list_widget.isHidden())
        self.assertEqual(page.layout().stretch(2), 0)
        page.close()

    def test_active_task_list_remains_the_expanding_part_of_the_work_pane(self) -> None:
        page = TodoPage()
        page.items = [{"id": "task-1", "title": "修改摘要", "done": False, "schedule_mode": "none"}]
        page._render()

        self.assertFalse(page.list_widget.isHidden())
        self.assertEqual(page.layout().stretch(2), 1)
        page.close()

    def test_empty_task_day_keeps_header_and_empty_copy_compact_at_widget_width(self) -> None:
        page = TodoPage()
        page.resize(348, 602)
        page.items = []
        page._render()
        page.show()
        self.application.processEvents()

        self.assertLess(page.input.y(), 100)
        self.assertLessEqual(page.empty_label.height(), 72)
        page.close()

    def test_task_row_keeps_title_readable_and_actions_compact_in_a_narrow_workbench(self) -> None:
        """Task actions must never consume the title column on a small widget."""
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

        viewport = page.list_widget.viewport()
        title = page.list_widget.findChild(QLabel, "todoText")
        priority = page.list_widget.findChild(QPushButton, "todoPriorityButton")
        actions = [
            button
            for button in page.list_widget.findChildren(QPushButton)
            if button.objectName() in {"rowButton", "dangerButton", "dragHandle"}
        ]

        self.assertIsNotNone(title)
        self.assertIsNotNone(priority)
        self.assertGreaterEqual(title.width(), int(viewport.width() * 0.55))
        self.assertLessEqual(priority.width(), 104)
        self.assertTrue(all(button.width() <= 32 for button in actions))
        self.assertTrue(
            all(
                button.mapTo(viewport, button.rect().topRight()).x() < viewport.width()
                for button in [priority, *actions]
            )
        )
        page.close()
