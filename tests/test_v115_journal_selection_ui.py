"""Focused UI contracts for the v11.5 AI journal-selection workbench."""

from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QCheckBox, QScrollArea, QWidget

from ui.journal_selection_dialog import JournalSelectionDialog


class JournalSelectionWorkbenchUiTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.paper = {
            "id": "paper-1",
            "title": "SOC mapping",
            "summary": "土壤有机碳遥感制图与机器学习方法。" * 120,
            "keywords": ["SOC", "遥感"],
        }
        self.journals = [
            {
                "id": "catena",
                "name": "CATENA",
                "publisher": "Elsevier",
                "fee_mode": "hybrid",
                "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
                "easyscholar": {"cas_upgrade": "1区"},
            },
            {
                "id": "land",
                "name": "Land Degradation & Development",
                "publisher": "Wiley",
                "fee_mode": "subscription",
                "jcr": {"status": "verified", "metrics": [{"quartile": "Q2"}]},
                "easyscholar": {"cas_upgrade": "2区"},
            },
        ]
        self.dialog = JournalSelectionDialog([self.paper], self.journals, {})
        self.dialog.show()
        self.application.processEvents()

    def tearDown(self) -> None:
        self.dialog.close()

    def test_long_abstract_uses_bounded_scroll_preview_and_keeps_ai_action_reachable(self) -> None:
        parent = QWidget()
        parent.resize(520, 640)
        dialog = JournalSelectionDialog([self.paper], self.journals, {}, parent)
        dialog.show()
        self.application.processEvents()

        preview = dialog.findChild(QScrollArea, "selectionSummaryScroll")
        self.assertIsNotNone(preview)
        self.assertLessEqual(preview.maximumHeight(), 150)
        self.assertTrue(preview.verticalScrollBar().maximum() > 0)
        self.assertFalse(dialog.ai_button.geometry().isEmpty())
        dialog.close()
        parent.close()

    def test_extended_filter_controls_serialize_into_selection_requirements(self) -> None:
        self.dialog.publisher_combo.setCurrentIndex(self.dialog.publisher_combo.findData("Elsevier"))
        self.dialog.fee_combo.setCurrentIndex(self.dialog.fee_combo.findData("no_fee"))
        self.dialog.jcr_quartile_combo.set_checked_values(["Q1", "Q2"])
        self.dialog.cas_quartile_combo.set_checked_values(["1", "2"])

        requirements = self.dialog._current_requirements()

        self.assertEqual(requirements["publishers"], ["Elsevier"])
        self.assertEqual(requirements["fee_modes"], ["no_fee"])
        self.assertEqual(requirements["jcr_quartiles"], ["Q1", "Q2"])
        self.assertEqual(requirements["cas_quartiles"], ["1", "2"])

    def test_waiting_and_unconfigured_ai_states_keep_results_empty_until_a_real_ai_run(self) -> None:
        self.assertTrue(self.dialog.ai_status.text().strip())
        self.assertFalse(self.dialog._candidates)
        self.assertEqual(self.dialog.candidate_list.count(), 0)

        with patch("ui.journal_selection_dialog.is_deepseek_ready", return_value=False):
            self.dialog._run_ai_recommendation()

        self.assertIn("尚未配置", self.dialog.ai_status.text())
        self.assertFalse(self.dialog._candidates)
        self.assertFalse(self.dialog.add_button.isEnabled())

    def test_result_rows_show_a_compact_ai_total_and_reason_without_legacy_detail_blocks(self) -> None:
        self.dialog.apply_ai_recommendation(
            {
                "ranked": [
                    {
                        "id": "catena",
                        "fit_score": 92,
                        "reason_cn": "摘要的遥感制图方法与期刊范围相符。",
                        "risk_cn": "投稿前需核对版面费与栏目范围。",
                    }
                ]
            }
        )

        row = self.dialog.candidate_list.itemWidget(self.dialog.candidate_list.item(0))
        self.assertIn("AI 总分", row.score_label.text())
        self.assertIn("推荐理由", row.reason_label.text())
        self.assertTrue(row.risk_label.isHidden())
        self.assertTrue(row.fact_label.isHidden())
        self.assertNotIn("×70%", self.dialog.score_equation.text())

    def test_recommendation_pane_keeps_about_half_the_workbench_height(self) -> None:
        self.dialog.resize(520, 720)
        self.dialog.show()
        self.application.processEvents()

        self.assertEqual(self.dialog.selection_splitter.count(), 2)
        top, bottom = self.dialog.selection_splitter.sizes()
        self.assertGreaterEqual(bottom, int((top + bottom) * 0.45))
        self.assertGreaterEqual(self.dialog.candidate_list.height(), int((top + bottom) * 0.40))

    def test_jcr_multi_select_allows_two_choices_without_closing_the_popup(self) -> None:
        multi = self.dialog.jcr_quartile_combo
        multi.open_popup()
        self.application.processEvents()
        options = multi.option_checkboxes()

        self.assertEqual(len(options), 4)
        options[0].click()
        self.application.processEvents()
        self.assertTrue(multi.popup_menu().isVisible())
        options[1].click()
        self.application.processEvents()

        self.assertEqual(multi.checked_values(), ["Q1", "Q2"])

    def test_narrow_result_rows_expand_for_wrapped_title_reason_and_risk(self) -> None:
        self.dialog.resize(380, 680)
        self.dialog._journals[0]["name"] = "A Journal with an exceptionally long title that must wrap cleanly in the narrow recommendation workbench"
        self.dialog.apply_ai_recommendation(
            {
                "ranked": [
                    {
                        "id": "catena",
                        "fit_score": 96,
                        "reason_cn": "这是一段足够长的推荐理由，用来验证小组件宽度下不会把摘要匹配、方法特点与投稿建议截断。" * 3,
                        "risk_cn": "这是一段足够长的风险提示，用来验证风险信息也会完整换行显示，而不是被固定行高裁掉。" * 2,
                    }
                ]
            }
        )
        self.application.processEvents()

        row = self.dialog.candidate_list.itemWidget(self.dialog.candidate_list.item(0))
        title = row.findChild(QWidget, "selectionRowTitle")

        self.assertIsNotNone(title)
        self.assertGreaterEqual(row.height(), row.sizeHint().height())
        self.assertGreaterEqual(row.title_label.height(), row.title_label.heightForWidth(row.title_label.width()))
        self.assertGreaterEqual(row.reason_label.height(), row.reason_label.heightForWidth(row.reason_label.width()))
        self.assertGreaterEqual(row.risk_label.height(), row.risk_label.heightForWidth(row.risk_label.width()))

    def test_external_rows_keep_unverified_label_and_quick_import(self) -> None:
        self.dialog.apply_ai_recommendation(
            {
                "ranked": [],
                "external_candidates": [{"name": "External Journal", "fit_score": 90, "reason_cn": "研究对象匹配。"}],
            }
        )
        external_row = next(index for index, item in enumerate(self.dialog._candidates) if item.get("is_external"))
        self.dialog.candidate_list.setCurrentRow(external_row)
        row = self.dialog.candidate_list.itemWidget(self.dialog.candidate_list.item(external_row))

        self.assertIn("待核验", row.fact_label.text())
        self.assertFalse(self.dialog.quick_import_button.isHidden())
        self.assertEqual(row.action_button.text(), "加入投稿路径")

    def test_ai_completion_does_not_claim_success_when_no_ai_row_survives_filters(self) -> None:
        self.dialog._ai_recommendation_finished(
            {
                "ranked": [],
                "external_candidates": [],
                "ai_qualified_count": 0,
                "fallback_reason_cn": "当前筛选条件下仅有 0 个合格候选，已保留全部可用推荐。",
            }
        )

        self.assertNotIn("已更新", self.dialog.ai_status.text())
        self.assertIn("没有期刊通过", self.dialog.ai_status.text())

    def test_narrow_workbench_has_no_horizontal_scroll_and_visible_actions(self) -> None:
        for width in (380, 460, 540):
            dialog = JournalSelectionDialog([self.paper], self.journals, {})
            dialog.resize(width, 680)
            dialog.show()
            self.application.processEvents()

            self.assertEqual(dialog.candidate_list.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.assertFalse(dialog.candidate_list.horizontalScrollBar().isVisible())
            self.assertEqual(dialog.candidate_list.horizontalScrollBar().maximum(), 0)
            self.assertFalse(dialog.ai_button.geometry().isEmpty())
            self.assertFalse(dialog.add_button.geometry().isEmpty())
            self.assertEqual(dialog.candidate_list.count(), 0)
            dialog.close()
