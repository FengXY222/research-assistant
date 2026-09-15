"""Smoke tests for the v11 explainable journal-selection dialog."""

from __future__ import annotations

import os
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.journal_selection_dialog import JournalSelectionDialog


class JournalSelectionDialogTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_dialog_waits_for_ai_then_shows_ai_total_score_and_selects_a_candidate(self) -> None:
        dialog = JournalSelectionDialog(
            [{"id": "p1", "title": "SOC mapping", "keywords": ["soil organic carbon"], "journals": []}],
            [
                {
                    "id": "j1",
                    "name": "CATENA",
                    "fields": ["soil organic carbon"],
                    "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
                }
            ],
            {"terms": [{"text": "soil organic carbon", "weight": 100, "locked": True}]},
        )

        self.assertEqual(dialog.candidate_list.count(), 0)
        dialog.apply_ai_recommendation(
            {
                "ranked": [
                    {
                        "journal_id": "j1",
                        "journal_name": "CATENA",
                        "journal": {"id": "j1", "name": "CATENA"},
                        "is_external": False,
                        "ai_total_score": 92,
                        "reason_cn": "摘要与方法匹配",
                    }
                ],
                "external_candidates": [],
                "verified_count": 1,
            }
        )
        self.assertEqual(dialog.candidate_list.count(), 1)
        dialog.candidate_list.setCurrentRow(0)
        self.assertIn("AI 总分", dialog.score_equation.text())
        self.assertNotIn("本地总分", dialog.score_equation.text())
        self.assertEqual(dialog.selection(), ("p1", "j1"))
        dialog.close()
