"""Daily Frontier must use the independent v12 research-profile store."""

from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.frontier_page import DailyFrontierPage, FrontierCommentAiThread, FrontierProfileAiThread


class V12ProfilePageIntegrationTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    @staticmethod
    def _frontier_payload() -> dict:
        return {
            "profile": {"terms": [{"canonical_en": "stale frontier copy"}]},
            "items": [],
            "last_checked": "",
        }

    def test_page_prefers_the_independent_profile_over_the_frontier_copy(self) -> None:
        independent = {
            "terms": [
                {
                    "canonical_en": "soil organic carbon",
                    "translation_zh": "土壤有机碳",
                    "weight": 90,
                }
            ]
        }
        with (
            patch("ui.frontier_page.load_frontier_data", return_value=self._frontier_payload()),
            patch("ui.frontier_page.load_research_profile", return_value=independent),
        ):
            page = DailyFrontierPage()

        self.assertEqual(page.data["profile"]["terms"][0]["canonical_en"], "soil organic carbon")
        page.close()

    def test_page_persists_frontier_state_and_independent_profile_together(self) -> None:
        profile = {"terms": [{"canonical_en": "digital soil mapping", "translation_zh": "数字土壤制图"}]}
        with (
            patch("ui.frontier_page.load_frontier_data", return_value=self._frontier_payload()),
            patch("ui.frontier_page.load_research_profile", return_value=profile),
        ):
            page = DailyFrontierPage()
        with (
            patch("ui.frontier_page.save_frontier_data") as save_frontier,
            patch("ui.frontier_page.save_research_profile") as save_profile,
        ):
            page._persist_data()

        save_frontier.assert_called_once_with(page.data)
        save_profile.assert_called_once_with(page.data["profile"])
        page.close()

    def test_daily_worker_uses_the_v12_change_plan_contract(self) -> None:
        evidence = {
            "papers": [{"id": "p1", "title": "SOC mapping"}],
            "signals": [{"kind": "explicit_feedback", "text": "多推土壤重金属制图", "weight": 100}],
        }
        result: list[dict] = []
        worker = FrontierProfileAiThread({"terms": []}, evidence)
        worker.completed.connect(result.append)

        with patch(
            "ui.frontier_page.organize_research_profile_with_ai",
            return_value={"terms": [], "excluded_terms": [], "summary": "无需修改"},
        ) as organizer:
            worker.run()

        organizer.assert_called_once()
        self.assertEqual(organizer.call_args.args[1], evidence)
        self.assertEqual(result[0]["summary"], "无需修改")

    def test_today_organization_can_be_undone_from_the_widget_menu(self) -> None:
        profile = {
            "terms": [{"canonical_en": "new term"}],
            "last_organization_snapshot": {
                "date": "2026-08-31",
                "profile": {"terms": [{"canonical_en": "original term"}]},
            },
        }
        with (
            patch("ui.frontier_page.load_frontier_data", return_value=self._frontier_payload()),
            patch("ui.frontier_page.load_research_profile", return_value=profile),
        ):
            page = DailyFrontierPage()
        restored = {"terms": [{"canonical_en": "original term"}], "auto_organization_suppressed_for_date": "2026-08-31"}

        with (
            patch("ui.frontier_page.date") as mocked_date,
            patch("ui.frontier_page.undo_last_organization", return_value=restored) as undo,
            patch.object(page, "_save") as save,
        ):
            mocked_date.today.return_value.isoformat.return_value = "2026-08-31"
            self.assertTrue(page.undo_profile_organization())

        undo.assert_called_once()
        self.assertEqual(page.data["profile"], restored)
        save.assert_called_once()
        page.close()

    def test_one_line_direction_feedback_triggers_immediate_ai_profile_update(self) -> None:
        with (
            patch("ui.frontier_page.load_frontier_data", return_value=self._frontier_payload()),
            patch("ui.frontier_page.load_research_profile", return_value={"terms": []}),
        ):
            page = DailyFrontierPage()
        page.data["items"] = [
            {
                "id": "frontier-1",
                "title": "Soil heavy metal mapping",
                "journal": "Example Journal",
                "status": "new",
                "match_terms": ["soil heavy metals"],
            }
        ]

        with (
            patch("ui.frontier_page.is_deepseek_ready", return_value=True),
            patch.object(FrontierCommentAiThread, "start", lambda worker: worker.run()),
            patch(
                "ui.frontier_page.classify_profile_comment_with_ai",
                return_value={
                    "intent": "positive",
                    "active_terms": [
                        {
                            "canonical_en": "soil heavy metal mapping",
                            "translation_zh": "土壤重金属制图",
                            "weight": 88,
                        }
                    ],
                    "excluded_terms": [],
                    "pending_terms": [],
                    "reason": "用户要求多推荐同类研究",
                    "confidence": "high",
                    "item_id": "frontier-1",
                },
            ) as classifier,
            patch.object(page, "_persist_data"),
        ):
            page._record_feedback("frontier-1", "可以多推些类似土壤重金属制图的")

        classifier.assert_called_once()
        self.assertEqual(page.data["profile"]["terms"][0]["canonical_en"], "soil heavy metal mapping")
        self.assertEqual(page.data["profile"]["terms"][0]["translation_zh"], "土壤重金属制图")
        page.close()
