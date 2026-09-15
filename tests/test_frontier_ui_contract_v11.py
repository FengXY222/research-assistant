"""Non-network UI contracts for the v11 frontier experience."""

from __future__ import annotations

import os
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from ui.frontier_page import DailyFrontierPage, FrontierCard
from ui.research_profile_dialog import ResearchProfileDialog


class FrontierUiContractTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_profile_dialog_renders_legacy_and_weighted_profiles(self) -> None:
        for profile in (
            {"primary_keywords": ["SOC"], "secondary_keywords": ["MAOC"]},
            {"terms": [{"text": "soil organic carbon", "weight": 100, "locked": True}]},
        ):
            dialog = ResearchProfileDialog(profile)
            self.assertIsInstance(dialog.profile(), dict)
            dialog.close()

    def test_unknown_jcr_card_keeps_a_visible_non_blocking_badge(self) -> None:
        card = FrontierCard(
            {
                "id": "f1",
                "title": "SOC and MAOC dynamics",
                "journal": "Example Journal",
                "score": 135,
                "jcr_state": "未知",
                "match_terms": ["SOC", "MAOC"],
                "score_breakdown": {"terms": 120, "priority": 15, "quality": 0, "feedback": 0, "ai": 0},
            }
        )
        text = "\n".join(label.text() for label in card.findChildren(QLabel))

        self.assertIn("分区未知", text)
        card.close()

    def test_frontier_card_names_the_cas_division_explicitly(self) -> None:
        card = FrontierCard(
            {
                "id": "f-cas",
                "title": "SOC mapping",
                "journal": "Example Journal",
                "score": 135,
                "jcr_state": "verified",
                "jcr_quartile": "Q1",
                "cas_upgrade": "2区",
            }
        )

        badge = card.findChild(QLabel, "frontierCasBadge")

        self.assertIsNotNone(badge)
        self.assertEqual(badge.text(), "中科院 2区")
        card.close()

    def test_quality_feedback_records_an_event_without_reweighting_profile_terms(self) -> None:
        page = DailyFrontierPage()
        page.data = {
            "profile": {"terms": [{"text": "SOC", "weight": 100, "locked": True}], "filter_known_q3_q4": True},
            "items": [{"id": "f1", "title": "SOC", "journal": "Example", "match_terms": ["SOC"], "score": 100, "status": "new"}],
        }

        page._record_feedback("f1", "这个期刊是三四区，不适合投")

        self.assertEqual(page.data["profile"]["terms"][0]["weight"], 100)
        self.assertEqual(page.data["items"][0]["feedback_events"][0]["classification"]["kind"], "journal_quality")
        page.close()

    def test_compact_header_keeps_title_and_core_actions_visible(self) -> None:
        page = DailyFrontierPage()
        host = QWidget()
        host.setFixedSize(348, 600)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(page)
        host.show()
        self.application.processEvents()

        self.assertEqual(page.width(), 348)
        title_width = QFontMetrics(page.title_label.font()).horizontalAdvance("每日前沿")
        self.assertGreaterEqual(page.title_label.contentsRect().width(), title_width)
        self.assertTrue(page.settings_button.isVisible())
        self.assertTrue(page.refresh_button.isVisible())
        self.assertTrue(page.more_actions_button.isVisible())
        self.assertTrue(page.profile_update_button.isHidden())
        self.assertTrue(page.ai_button.isHidden())
        host.close()

    def test_standalone_widget_can_enter_compact_mode_before_first_show(self) -> None:
        page = DailyFrontierPage()
        page.resize(400, 480)
        page.show()
        self.application.processEvents()

        self.assertEqual(page.width(), 400)
        self.assertTrue(page.more_actions_button.isVisible())
        self.assertTrue(page.profile_update_button.isHidden())
        self.assertEqual(page.refresh_button.text(), "更新")
        self.assertTrue(page.frontier_subtitle.isHidden())
        filter_right = page.filter_combo.mapTo(page, page.filter_combo.rect().topRight()).x()
        self.assertLessEqual(filter_right, page.rect().right())
        page.close()
