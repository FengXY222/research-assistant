"""UI contracts for the v12 evidence-led journal-selection workbench."""

from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication, QComboBox, QListWidget, QPushButton, QWidget

from tests import _data_root  # noqa: F401 - isolate persistence before UI imports
from ui.journal_selection_dialog import JournalSelectionAiThread, JournalSelectionDialog


class V12JournalSelectionUiTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.dialog = JournalSelectionDialog(
            [
                {
                    "id": "paper-1",
                    "title": "Soil organic carbon mapping",
                    "summary": "Remote sensing and machine learning for soil organic carbon mapping.",
                    "keywords": ["soil organic carbon", "remote sensing"],
                    "journals": [],
                }
            ],
            [],
            {},
        )
        self.dialog.show()
        self.application.processEvents()

    def tearDown(self) -> None:
        self.dialog.close()

    @staticmethod
    def _result(index: int, *, external: bool = False) -> dict:
        name = f"Verified Journal {index}"
        return {
            "result_id": f"result-{index}",
            "journal_id": f"journal-{index}",
            "journal_name": name,
            "journal": {
                "id": f"journal-{index}",
                "name": name,
                "publisher": "Elsevier",
                "issns": [f"1234-56{index:02d}"],
                "website": f"https://example.org/journal/{index}",
                "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
                "easyscholar": {"cas_upgrade": "1区", "checked_at": "2026-08-31"},
            },
            "ai_total_score": 90 - index,
            "reason_cn": "相似论文与稿件主题、方法和研究对象均高度接近。",
            "publisher": "Elsevier",
            "fee_mode": "hybrid",
            "estimated_speed_text": "预计较快",
            "is_external": external,
            "similar_papers": [
                {
                    "title": f"Similar paper {index}",
                    "doi": f"10.1000/example.{index}",
                    "source_names": ["OpenAlex", "Crossref"],
                }
            ],
            "identity_evidence": {
                "verified": True,
                "issns": [f"1234-56{index:02d}"],
                "official_url": f"https://example.org/journal/{index}",
                "source_names": ["Crossref", "OpenAlex"],
                "verified_at": "2026-08-31T12:00:00",
            },
        }

    def test_workbench_exposes_v12_controls_and_balanced_result_space(self) -> None:
        self.assertEqual(self.dialog.size(), QSize(1024, 768))
        for object_name, widget_type in (
            ("selectionPublisher", QComboBox),
            ("selectionFeeMode", QComboBox),
            ("selectionJcrMulti", QPushButton),
            ("selectionCasMulti", QPushButton),
            ("selectionSpeedPriority", QComboBox),
            ("selectionFitStrictness", QComboBox),
            ("selectionStartButton", QPushButton),
            ("selectionResults", QListWidget),
        ):
            self.assertIsNotNone(self.dialog.findChild(widget_type, object_name), object_name)
        self.assertIsNotNone(self.dialog.findChild(QWidget, "selectionProgress"))

        top, bottom = self.dialog.selection_splitter.sizes()
        total = top + bottom
        self.assertGreater(total, 0)
        self.assertGreaterEqual(bottom, int(total * 0.48))
        self.assertLessEqual(bottom, int(total * 0.62))

    def test_multi_select_summary_and_fit_strictness_are_serialized(self) -> None:
        self.dialog.jcr_quartile_combo.set_checked_values(["Q1", "Q2"])
        self.dialog.cas_quartile_combo.set_checked_values(["1", "2"])
        strictness = self.dialog.findChild(QComboBox, "selectionFitStrictness")
        assert strictness is not None
        strictness.setCurrentIndex(strictness.findData("strict"))

        self.assertEqual(self.dialog.jcr_quartile_combo.text(), "Q1、Q2")
        self.assertEqual(self.dialog.cas_quartile_combo.text(), "1区、2区")
        requirements = self.dialog._current_requirements()
        self.assertEqual(requirements["jcr_quartiles"], ["Q1", "Q2"])
        self.assertEqual(requirements["cas_quartiles"], ["1", "2"])
        self.assertEqual(requirements["fit_strictness"], "strict")

    def test_v12_results_expand_verification_evidence_without_closing(self) -> None:
        self.dialog.apply_ai_recommendation({"results": [self._result(1)], "rounds": 2})
        self.application.processEvents()

        self.assertEqual(self.dialog.candidate_list.count(), 1)
        row = self.dialog.candidate_list.itemWidget(self.dialog.candidate_list.item(0))
        summary = row.summary_fact_label.text()
        self.assertIn("Elsevier", summary)
        self.assertIn("JCR Q1", summary)
        self.assertIn("中科院 1区", summary)
        self.assertIn("可付费/不付费", summary)
        self.assertIn("预计较快", summary)
        toggle = row.findChild(QPushButton, "selectionEvidenceToggle")
        self.assertIsNotNone(toggle)
        before = row.preferred_height(self.dialog.candidate_list.viewport().width())
        assert toggle is not None
        toggle.click()
        self.application.processEvents()
        after = row.preferred_height(self.dialog.candidate_list.viewport().width())

        self.assertGreater(after, before)
        self.assertFalse(row.fact_label.isHidden())
        self.assertIn("Similar paper 1", row.evidence_label.text())
        self.assertTrue(self.dialog.isVisible())

    def test_row_actions_use_stable_result_id_instead_of_visual_index(self) -> None:
        results = [self._result(index, external=index == 3) for index in range(1, 4)]
        self.dialog.apply_ai_recommendation({"results": results, "rounds": 3})
        self.application.processEvents()
        actions: list[str] = []
        self.dialog.action_requested.connect(actions.append)

        third_row = self.dialog.candidate_list.itemWidget(self.dialog.candidate_list.item(2))
        third_row.action_button.click()
        self.application.processEvents()

        self.assertEqual(actions, ["path"])
        self.assertEqual(self.dialog.selected_candidate()["result_id"], "result-3")
        self.assertEqual(self.dialog.selected_journal()["name"], "Verified Journal 3")
        self.assertTrue(self.dialog.isVisible())

    def test_exclude_action_uses_selected_result_and_keeps_workbench_open(self) -> None:
        self.dialog.apply_ai_recommendation({"results": [self._result(1), self._result(2)], "rounds": 2})
        self.application.processEvents()
        actions: list[str] = []
        self.dialog.action_requested.connect(actions.append)

        second_row = self.dialog.candidate_list.itemWidget(self.dialog.candidate_list.item(1))
        exclude = second_row.findChild(QPushButton, "selectionExcludeButton")
        self.assertIsNotNone(exclude)
        assert exclude is not None
        exclude.click()
        self.application.processEvents()

        self.assertEqual(actions, ["exclude"])
        self.assertEqual(self.dialog.selected_candidate()["result_id"], "result-2")
        self.assertTrue(self.dialog.isVisible())

    def test_ai_worker_runs_the_v12_evidence_engine_and_adapts_its_results(self) -> None:
        completed: list[dict] = []
        worker = JournalSelectionAiThread(
            self.dialog._selected_paper(),
            [],
            {},
            {"rejected_journal_names": ["Rejected Journal"]},
        )
        worker.completed.connect(completed.append)
        result = {
            "results": [self._result(1)],
            "rounds": 2,
            "verification_configured": True,
        }

        with (
            patch("ui.journal_selection_dialog.EvidenceCache") as cache_type,
            patch("ui.journal_selection_dialog.run_selection_rounds", return_value=result) as runner,
        ):
            worker.run()

        cache_type.return_value.initialize.assert_called_once_with()
        self.assertEqual(runner.call_args.args[2], [{"name": "Rejected Journal", "journal_name": "Rejected Journal"}])
        self.assertEqual(completed[0]["ranked"][0]["result_id"], "result-1")
        self.assertEqual(completed[0]["verification_mode"], "configured")
