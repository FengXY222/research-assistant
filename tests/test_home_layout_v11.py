"""Responsive homepage density contracts for the v11 widget."""

from __future__ import annotations

import os
from datetime import date
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from tests import _data_root  # noqa: F401 - isolate persistence before UI imports
from ui.home_page import HomePage


class HomeLayoutV11Tests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_recent_node_card_contracts_when_there_are_no_alerts(self) -> None:
        page = HomePage()

        page._render_recent_nodes(date.today(), [], [])

        self.assertLessEqual(page.recent_scroll.maximumHeight(), 56)
        page.close()

    def test_recent_node_card_compacts_when_only_one_alert_needs_attention(self) -> None:
        page = HomePage()
        papers = [
            {
                "title": "SOC mapping",
                "journals": [{"name": "CATENA", "status": "外审中", "status_updated_at": "2026-06-01"}],
            }
        ]

        page._render_recent_nodes(date(2026, 8, 21), [], papers)

        self.assertGreaterEqual(page.recent_scroll.maximumHeight(), 40)
        self.assertLessEqual(page.recent_scroll.maximumHeight(), 72)
        page.close()

    def test_widget_home_uses_a_scrollable_work_canvas_instead_of_crushing_cards(self) -> None:
        page = HomePage()
        page.resize(511, 686)
        page._render_today_tasks(
            [
                {
                    "id": f"task-{index}",
                    "title": f"需要在今天推进的较长科研任务 {index}，用于验证任务较多时首页仍保持清楚的层级",
                    "done": False,
                    "quadrant": "urgent_important",
                }
                for index in range(8)
            ]
        )
        page.show()
        self.application.processEvents()

        self.assertTrue(page.dashboard_scroll.widgetResizable())
        self.assertGreater(page.dashboard_scroll.verticalScrollBar().maximum(), 0)
        self.assertGreaterEqual(page.todo_card[0].height(), 140)
        self.assertGreaterEqual(page.paper_card[0].height(), 92)
        self.assertTrue(page._notes_stacked)
        self.assertGreaterEqual(page.reading_preview.y(), page.inspiration_preview.geometry().bottom() + 1)
        page.close()

    def test_submission_observation_card_is_a_styled_surface(self) -> None:
        page = HomePage()

        self.assertTrue(page.recent_card.testAttribute(Qt.WidgetAttribute.WA_StyledBackground))
        page.close()

    def test_wide_home_gives_submission_observation_the_full_row_when_no_action_exists(self) -> None:
        page = HomePage()
        page.resize(1120, 760)
        page._render_next_actions([])
        page._render_recent_nodes(
            date(2026, 8, 25),
            [],
            [{"title": "SOC mapping", "journals": [{"id": "journal-1", "name": "CATENA", "status": "外审中", "status_updated_at": "2026-07-01"}]}],
        )
        page.show()
        self.application.processEvents()

        self.assertTrue(page.next_card.isHidden())
        self.assertGreaterEqual(page.recent_card.width(), page.submission_container.width() - 2)
        self.assertEqual(page.recent_card.x(), 0)
        page.close()

    def test_wide_home_uses_a_focus_workbench_not_two_equal_metric_cards(self) -> None:
        """A busy day keeps concrete tasks dominant without flattening paper status."""
        page = HomePage()
        page.resize(1120, 760)
        page._render_today_tasks(
            [
                {
                    "id": f"task-{index}",
                    "title": f"推进今天最关键的研究任务 {index}",
                    "done": False,
                    "quadrant": "urgent_important",
                }
                for index in range(6)
            ]
        )
        page._render_next_actions(
            [
                {
                    "title": "CATENA · 多源遥感土壤碳制图",
                    "reason": "今天可投稿",
                    "route": "papers",
                    "paper_id": "paper-1",
                    "journal_id": "journal-1",
                }
            ]
        )
        page._render_recent_nodes(
            date(2026, 8, 25),
            [],
            [
                {
                    "title": "SOC mapping",
                    "journals": [
                        {
                            "id": "journal-2",
                            "name": "Geoderma",
                            "status": "外审中",
                            "status_updated_at": "2026-07-01",
                        }
                    ],
                }
            ],
        )
        page.show()
        self.application.processEvents()

        todo_card = page.todo_card[0]
        paper_card = page.paper_card[0]

        self.assertEqual(todo_card.property("homeRole"), "focus")
        self.assertEqual(paper_card.property("homeRole"), "paper-rail")
        self.assertGreater(todo_card.width(), int(paper_card.width() * 1.45))
        self.assertEqual(todo_card.y(), paper_card.y())
        self.assertGreaterEqual(page.next_card.width(), page.submission_container.width() - 2)
        self.assertGreaterEqual(page.recent_card.y(), page.next_card.geometry().bottom() + 1)
        page.close()
