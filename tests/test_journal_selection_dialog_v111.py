"""Widget contracts for the compact AI-first journal-selection workbench."""

from __future__ import annotations

import os
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget, QLabel

from ui.journal_selection_dialog import JournalSelectionDialog


class JournalSelectionDialogV111Tests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.dialog = JournalSelectionDialog(
            [
                {
                    "id": "paper-1",
                    "title": "SOC mapping",
                    "summary": "研究摘要：SOC 遥感制图与机器学习方法。",
                    "keywords": ["SOC"],
                }
            ],
            [
                {
                    "id": "catena",
                    "name": "CATENA",
                    "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
                    "easyscholar": {"source": "EasyScholar Open API", "checked_at": "2026-08-21", "cas_upgrade": "2区"},
                }
            ],
            {},
        )

    def tearDown(self) -> None:
        self.dialog.close()

    def test_controls_keep_legacy_state_but_show_only_the_new_hard_condition_fields(self) -> None:
        self.assertIn("研究摘要", self.dialog.summary_preview.text())
        self.assertEqual(self.dialog.oa_mode.currentData(), "any")
        self.assertEqual(self.dialog.quartile_target.currentData(), "any")
        self.assertEqual(self.dialog.speed_priority.currentData(), "standard")
        self.assertTrue(self.dialog.oa_mode.isHidden())
        self.assertTrue(self.dialog.quartile_target.isHidden())
        self.assertEqual(self.dialog.ai_button.text(), "开始 AI 选刊")
        self.assertEqual(self.dialog.candidate_list.count(), 0)

    def test_external_ai_candidate_exposes_a_fast_import_action(self) -> None:
        self.dialog.apply_ai_recommendation(
            {
                "ranked": [{"id": "catena", "fit_score": 80, "reason_cn": "摘要匹配"}],
                "external_candidates": [
                    {"name": "External Journal", "publisher": "Press", "fit_score": 90, "reason_cn": "外部期刊候选"}
                ],
            }
        )
        external_row = next(
            index
            for index, candidate in enumerate(self.dialog._candidates)
            if bool(candidate.get("is_external", False))
        )
        self.dialog.candidate_list.setCurrentRow(external_row)

        self.assertTrue(self.dialog.is_external_selection())
        self.assertFalse(self.dialog.quick_import_button.isHidden())
        self.assertEqual(self.dialog.quick_import_button.text(), "快速入库")

    def test_selected_candidate_keeps_the_ai_reason_for_safe_library_import(self) -> None:
        self.dialog.apply_ai_recommendation(
            {
                "ranked": [],
                "external_candidates": [{"name": "External Journal", "fit_score": 90, "reason_cn": "外部期刊候选"}],
            }
        )
        external_row = next(index for index, item in enumerate(self.dialog._candidates) if item.get("is_external"))
        self.dialog.candidate_list.setCurrentRow(external_row)

        candidate = self.dialog.selected_candidate()

        self.assertTrue(candidate["is_external"])
        self.assertEqual(candidate["reason_cn"], "外部期刊候选")

    def test_dialog_opens_as_the_fixed_full_workbench_from_a_narrow_widget_parent(self) -> None:
        parent = QWidget()
        parent.resize(520, 680)
        parent.show()
        dialog = JournalSelectionDialog(
            self.dialog._papers,
            self.dialog._journals,
            {},
            parent,
        )
        dialog.show()
        self.application.processEvents()

        self.assertEqual((dialog.width(), dialog.height()), (1024, 768))
        self.assertEqual((dialog.minimumWidth(), dialog.minimumHeight()), (1024, 768))
        self.assertEqual(dialog.selection_splitter.orientation(), Qt.Orientation.Vertical)
        dialog.close()
        parent.close()

    def test_selector_candidate_displays_cached_jcr_and_cas_without_network_access(self) -> None:
        self.dialog.apply_ai_recommendation(
            {
                "ranked": [{"id": "catena", "fit_score": 90, "reason_cn": "摘要匹配"}],
                "external_candidates": [],
            }
        )
        metric = self.dialog.findChild(QLabel, "selectionMetricLine")
        self.assertIsNotNone(metric)
        self.assertIn("JCR Q1", metric.text())
        self.assertIn("中科院升级版 2区", metric.text())

    def test_external_selector_candidate_shows_metrics_pending_and_keeps_fast_import(self) -> None:
        self.dialog.apply_ai_recommendation(
            {
                "ranked": [],
                "external_candidates": [
                    {
                        "name": "External Journal",
                        "publisher": "Press",
                        "fit_score": 90,
                        "reason_cn": "外部期刊候选",
                        "quartile_hint": "Q1",
                        "impact_factor": "99",
                    }
                ],
            }
        )
        external_row = next(index for index, item in enumerate(self.dialog._candidates) if item.get("is_external"))
        self.dialog.candidate_list.setCurrentRow(external_row)
        metric = self.dialog.findChild(QLabel, "selectionMetricLine")
        self.assertIsNotNone(metric)
        self.assertEqual(metric.text(), "指标待核验")
        self.assertFalse(self.dialog.quick_import_button.isHidden())

    def test_external_ai_candidate_does_not_present_oa_or_review_hints_as_verified_facts(self) -> None:
        self.dialog.apply_ai_recommendation(
            {
                "ranked": [],
                "external_candidates": [
                    {
                        "name": "External Journal",
                        "publisher": "Press",
                        "fit_score": 90,
                        "reason_cn": "外部期刊候选",
                        "oa_status": "yes",
                        "estimated_decision_days_min": 12,
                        "estimated_decision_days_max": 18,
                        "time_confidence": "high",
                    }
                ],
            }
        )
        external_row = next(index for index, item in enumerate(self.dialog._candidates) if item.get("is_external"))
        self.dialog.candidate_list.setCurrentRow(external_row)

        candidate = self.dialog.selected_candidate()

        self.assertEqual(candidate["oa_status"], "unknown")
        self.assertEqual(candidate["estimated_decision_days_min"], 0)
        self.assertEqual(candidate["estimated_decision_days_max"], 0)
        self.assertEqual(candidate["time_confidence"], "")
        self.assertEqual(self.dialog.selection_metric_line.text(), "指标待核验")
        self.assertIn("JCR、OA 与处理周期均待核验", self.dialog.detail_body.text())
        self.assertNotIn("预计首轮决定", self.dialog.detail_body.text())

    def test_external_candidate_is_not_filtered_by_an_unverified_ai_oa_hint(self) -> None:
        self.dialog.oa_mode.setCurrentIndex(self.dialog.oa_mode.findData("require"))
        self.dialog.apply_ai_recommendation(
            {
                "ranked": [],
                "external_candidates": [
                    {
                        "name": "External Journal",
                        "publisher": "Press",
                        "fit_score": 90,
                        "reason_cn": "外部期刊候选",
                        "oa_status": "no",
                    }
                ],
            }
        )

        external = next(item for item in self.dialog._candidates if item.get("is_external"))

        self.assertEqual(external["oa_status"], "unknown")
