"""Priority and density contracts for HOME's 今日下一步 card."""

from __future__ import annotations

from datetime import date
import os
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from tests import _data_root  # noqa: F401 - isolate persistence before UI imports
from ui.home_page import ElidedActionButton, HomePage, today_next_actions


class HomeNextActionsV11Tests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_next_actions_prioritise_overdue_revision_and_cap_the_list(self) -> None:
        actions = today_next_actions(
            date(2026, 8, 21),
            [
                {"id": "t1", "title": "修改图件", "done": False, "end_date": "2026-08-21", "quadrant": "urgent_important"},
                {"id": "t2", "title": "整理数据", "done": False, "end_date": "2026-08-23"},
            ],
            [
                {
                    "id": "p1",
                    "title": "SOC mapping",
                    "journals": [
                        {"id": "j1", "name": "CATENA", "status": "修改中", "revision_due_date": "2026-08-19", "status_updated_at": "2026-08-01"},
                        {"id": "j2", "name": "SOIL", "status": "外审中", "status_updated_at": "2026-06-01"},
                    ],
                }
            ],
            reminder_state={},
        )

        self.assertLessEqual(len(actions), 3)
        self.assertEqual(actions[0]["route"], "papers")
        self.assertIn("回复逾期", actions[0]["reason"])
        self.assertIn("修改图件", [action["title"] for action in actions])

    def test_next_actions_include_undated_urgent_important_task_without_inventing_a_deadline(self) -> None:
        actions = today_next_actions(
            date(2026, 8, 21),
            [{"id": "t1", "title": "确定实验方案", "done": False, "end_date": "", "quadrant": "urgent_important"}],
            [],
            reminder_state={},
        )

        self.assertEqual(actions[0]["route"], "todo")
        self.assertEqual(actions[0]["reason"], "紧急且重要，尚未安排日期")

    def test_next_action_card_hides_when_empty_and_only_renders_paper_actions(self) -> None:
        page = HomePage()

        page._render_next_actions([])
        self.assertTrue(page.next_card.isHidden())

        page._render_next_actions(
            [
                {"title": "任务 A", "reason": "今天截止", "route": "todo"},
                {"title": "任务 B", "reason": "回复剩余 2 天", "route": "papers"},
                {"title": "任务 C", "reason": "外审已持续 45 天", "route": "papers"},
                {"title": "任务 D", "reason": "不会显示", "route": "todo"},
            ]
        )

        self.assertFalse(page.next_card.isHidden())
        buttons = page.next_card.findChildren(QPushButton)
        self.assertEqual(len(buttons), 1)
        self.assertTrue(buttons[0].text().startswith("回复剩余 2 天"))
        self.assertEqual(buttons[0].objectName(), "nextActionButton")
        page.close()

    def test_next_action_card_leaves_stale_statuses_to_the_submission_observation_panel(self) -> None:
        page = HomePage()

        page._render_next_actions(
            [
                {"title": "CATENA", "reason": "外审中 已 37 天未更新", "route": "papers"},
                {"title": "Geoderma", "reason": "外审中 已持续 42 天", "route": "papers"},
                {"title": "SOIL", "reason": "回复剩余 2 天", "route": "papers"},
            ]
        )

        buttons = page.next_card.findChildren(QPushButton)
        self.assertEqual(len(buttons), 1)
        self.assertTrue(buttons[0].text().startswith("回复剩余 2 天"))
        page.close()

    def test_narrow_next_action_keeps_reason_and_elides_only_the_title(self) -> None:
        button = ElidedActionButton("回复逾期 4 天 · 这是一个非常长的论文标题，不能挤出卡片右侧")
        button.show()
        self.application.processEvents()
        button.resize(220, 28)
        self.application.processEvents()

        self.assertTrue(button.text().startswith("回复逾期 4 天"))
        self.assertTrue(button.text().endswith("…"))
        self.assertEqual(button.toolTip(), "回复逾期 4 天 · 这是一个非常长的论文标题，不能挤出卡片右侧")
        button.close()

    def test_submission_observation_omits_a_journal_already_shown_as_the_next_action(self) -> None:
        page = HomePage()
        page._render_recent_nodes(
            date(2026, 8, 21),
            [],
            [
                {
                    "id": "paper-1",
                    "journals": [
                        {
                            "id": "journal-1",
                            "name": "CATENA",
                            "status": "修改中",
                            "status_updated_at": "2026-08-17",
                            "revision_due_date": "2026-08-24",
                        }
                    ],
                }
            ],
            exclude_journal_ids={"journal-1"},
        )

        text = "\n".join(label.text() for label in page.recent_card.findChildren(QLabel) if label.text())
        self.assertNotIn("CATENA", text)
        self.assertTrue(page.recent_card.isHidden())
        page.close()
