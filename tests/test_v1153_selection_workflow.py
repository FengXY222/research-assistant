"""Release contracts for the v11.5.3 verified journal-selection workflow."""

from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication, QPushButton

from ui.frontier_settings_dialog import FrontierSettingsDialog
from ui.journal_selection_dialog import JournalSelectionDialog
from ui.reorder import OrderDragHandle
from utils.ai_service import recommend_journals_iteratively_with_ai
from utils.journal_quality import compact_metric_line


class V1153SelectionWorkflowTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    @staticmethod
    def _paper() -> dict:
        return {
            "id": "paper-1",
            "title": "SOC mapping",
            "keywords": ["soil organic carbon", "remote sensing"],
            "summary": "A machine-learning workflow maps soil organic carbon from multi-source remote sensing data.",
            "journals": [{"id": "old", "name": "Rejected Journal", "status": "拒稿"}],
        }

    @staticmethod
    def _verified_patch(name: str) -> dict:
        quartile = "Q2" if name == "Wrong Partition" else "Q1"
        return {
            "jcr": {"status": "verified", "source": "EasyScholar Open API", "checked_at": "2026-08-25", "metrics": [{"quartile": quartile}]},
            "easyscholar": {"source": "EasyScholar Open API", "checked_at": "2026-08-25", "cas_upgrade": "农林科学1区"},
        }

    def test_iterative_ai_workflow_excludes_rejections_rechecks_partitions_and_refills_to_five(self) -> None:
        first_round = {
            "ranked": [],
            "external_candidates": [
                {"name": "Rejected Journal", "publisher": "Elsevier", "fee_mode": "hybrid", "fit_score": 99, "reason_cn": "不应保留"},
                {"name": "Wrong Partition", "publisher": "Elsevier", "fee_mode": "hybrid", "fit_score": 98, "reason_cn": "分区不应保留"},
                *[
                    {"name": f"Good Journal {index}", "publisher": "Elsevier", "fee_mode": "hybrid", "fit_score": 90 - index, "reason_cn": "硬条件与摘要均匹配"}
                    for index in range(1, 5)
                ],
            ],
        }
        second_round = {
            "ranked": [],
            "external_candidates": [{"name": "Good Journal 5", "publisher": "Elsevier", "fee_mode": "subscription", "fit_score": 84, "reason_cn": "补足第五本"}],
        }

        def discover(*_args, **kwargs):
            return first_round if kwargs["round_index"] == 1 else second_round

        result = None
        with patch("utils.ai_service.recommend_journals_with_ai", side_effect=discover) as recommender:
            result = recommend_journals_iteratively_with_ai(
                self._paper(),
                [],
                {},
                {"publishers": ["Elsevier"], "fee_modes": ["no_fee"], "jcr_quartiles": ["Q1"], "cas_quartiles": ["1"], "speed_priority": "urgent"},
                rejected_journal_names=["Rejected Journal"],
                max_rounds=3,
                easyscholar_ready=True,
                easyscholar_fetcher=lambda journal: self._verified_patch(str(journal.get("name", ""))),
            )

        assert result is not None
        names = [item["journal_name"] for item in result["external_candidates"]]
        self.assertEqual(len(names), 5)
        self.assertNotIn("Rejected Journal", names)
        self.assertNotIn("Wrong Partition", names)
        self.assertEqual(result["rounds"], 2)
        self.assertEqual(recommender.call_count, 2)
        self.assertTrue(all(item["journal"]["jcr"]["metrics"][0]["quartile"] == "Q1" for item in result["external_candidates"]))

    def test_dialog_opens_as_a_large_empty_workbench_and_keeps_the_actual_selected_result(self) -> None:
        paper = self._paper()
        dialog = JournalSelectionDialog([paper], [], {})
        self.assertEqual(dialog.size(), QSize(1024, 768))
        self.assertEqual(dialog.candidate_list.count(), 0)
        self.assertTrue(dialog.oa_mode.isHidden())
        self.assertTrue(dialog.quartile_target.isHidden())
        self.assertEqual(dialog._current_requirements()["rejected_journal_names"], ["Rejected Journal"])

        first = {"journal_id": "first", "journal_name": "First", "journal": {"id": "first", "name": "First"}, "is_external": False, "ai_total_score": 91, "reason_cn": "first"}
        second = {"journal_id": "second", "journal_name": "Second", "journal": {"id": "second", "name": "Second"}, "is_external": False, "ai_total_score": 83, "reason_cn": "second"}
        dialog.apply_ai_recommendation({"ranked": [first, second], "external_candidates": [], "verified_count": 2})
        dialog.candidate_list.setCurrentRow(1)
        self.application.processEvents()

        self.assertEqual(dialog.selected_candidate()["journal_id"], "second")
        self.assertEqual(dialog.selected_journal()["name"], "Second")
        dialog.close()

    def test_drag_handle_and_compact_cas_label_use_the_requested_symbols(self) -> None:
        self.assertEqual(OrderDragHandle("row", "scope").text(), "⥮")
        line = compact_metric_line(
            {
                "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
                "easyscholar": {"cas_upgrade": "农林科学1区", "impact_factor": "5.6"},
            }
        )
        self.assertEqual(line, "JCR Q1 · 中科院 1区 · IF 5.6")

    def test_frontier_settings_opens_large_and_pdf_results_enter_pending_review(self) -> None:
        dialog = FrontierSettingsDialog({"terms": []})
        self.assertEqual(dialog.size(), QSize(1024, 768))
        self.assertIsNotNone(dialog.findChild(QPushButton, "importPdfKeywordsButton"))
        dialog._pdf_keywords_imported({"name": "sample.pdf", "text": "soil organic carbon remote sensing machine learning"})
        pending = {str(item.get("text", "")) for item in dialog.profile()["pending_terms"]}
        self.assertIn("soil organic carbon", pending)
        dialog.close()
