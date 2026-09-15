"""Regression contracts for v11.5.7 journal actions and rejection gates."""

from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel

from tests import _data_root  # noqa: F401 - isolate persistence before UI imports
from ui.journal_library_page import CompactJournalRow
from ui.journal_selection_dialog import JournalSelectionDialog, RecommendationRow
from utils.journal_service import JournalLookupError, enrich_journal_library
from utils import publisher_utils


class V1156JournalActionTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_publisher_check_distinguishes_match_probable_unknown_and_mismatch(self) -> None:
        self.assertEqual(publisher_utils.fuzzy_publisher_check("Elsevier B.V.", "Elsevier")["status"], "match")
        self.assertEqual(publisher_utils.fuzzy_publisher_check("Springer", "Springer Nature")["status"], "match")
        self.assertEqual(publisher_utils.fuzzy_publisher_check("Nature Portfolio", "Nature Publishing Group")["status"], "probable")
        self.assertEqual(publisher_utils.fuzzy_publisher_check("", "Elsevier")["status"], "unknown")
        self.assertEqual(publisher_utils.fuzzy_publisher_check("Elsevier", "Wiley")["status"], "mismatch")

    def test_metadata_enrichment_keeps_a_nonfatal_publisher_validation_record(self) -> None:
        journal = {"id": "j1", "name": "CATENA", "publisher": "Elsevier", "issn": ""}
        with patch(
            "utils.journal_service.lookup_journal",
            return_value={
                "name": "CATENA",
                "publisher": "Elsevier B.V.",
                "issn": "0341-8162",
                "metadata_updated_at": "2026-08-25",
            },
        ):
            result = enrich_journal_library([journal])
        updated = result["journals"][0]
        self.assertEqual(updated["publisher_validation"]["status"], "match")
        self.assertEqual(updated["publisher_validation"]["source"], "Crossref")

    def test_missing_publisher_is_kept_without_being_filtered(self) -> None:
        journal = {"id": "j2", "name": "Unresolved Journal", "publisher": "", "issn": ""}
        with patch("utils.journal_service.lookup_journal", side_effect=JournalLookupError("network unavailable")):
            result = enrich_journal_library([journal])
        self.assertEqual(len(result["journals"]), 1)
        self.assertEqual(result["journals"][0]["name"], "Unresolved Journal")

    def test_publisher_mismatch_preserves_the_local_value_for_review(self) -> None:
        journal = {"id": "j3", "name": "CATENA", "publisher": "Wiley", "issn": ""}
        with patch(
            "utils.journal_service.lookup_journal",
            return_value={
                "name": "CATENA",
                "publisher": "Elsevier B.V.",
                "issn": "0341-8162",
                "metadata_updated_at": "2026-08-25",
            },
        ):
            result = enrich_journal_library([journal])
        updated = result["journals"][0]
        self.assertEqual(updated["publisher"], "Wiley")
        self.assertEqual(updated["publisher_validation"]["status"], "mismatch")
        self.assertEqual(updated["publisher_validation"]["observed"], "Elsevier B.V.")

    def test_journal_names_are_selectable_and_external_rows_offer_direct_import(self) -> None:
        row = CompactJournalRow(
            {"id": "j1", "name": "CATENA", "publisher": "Elsevier", "jcr": {"status": "pending", "metrics": []}}
        )
        name = row.findChild(QLabel, "journalCopyName")
        self.assertIsNotNone(name)
        assert name is not None
        self.assertTrue(name.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse)

        recommendation = RecommendationRow({"journal_name": "External Journal", "is_external": True}, lambda: None)
        title = recommendation.findChild(QLabel, "selectionRowTitle")
        self.assertIsNotNone(title)
        assert title is not None
        self.assertTrue(title.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse)
        self.assertIsNotNone(recommendation.findChild(QLabel, "selectionRowTitle"))
        library_button = recommendation.findChild(type(recommendation.action_button), "selectionLibraryButton")
        self.assertIsNotNone(library_button)
        row.close()
        recommendation.close()

    def test_quick_import_emits_action_without_closing_selection_workbench(self) -> None:
        dialog = JournalSelectionDialog(
            [{"id": "p1", "title": "Paper", "summary": "摘要", "journals": []}],
            [],
            {},
        )
        dialog.apply_ai_recommendation(
            {"ranked": [], "external_candidates": [{"name": "External Journal", "publisher": "Press"}]}
        )
        dialog.show()
        self.application.processEvents()
        actions: list[str] = []
        dialog.action_requested.connect(actions.append)
        dialog._quick_import()
        self.application.processEvents()
        self.assertEqual(actions, ["import"])
        self.assertEqual(dialog.result(), 0)
        self.assertTrue(dialog.isVisible())
        dialog.close()

    def test_rejection_archive_is_included_when_building_current_paper_exclusions(self) -> None:
        with patch(
            "ui.journal_selection_dialog.load_rejection_archive",
            return_value=[
                {"paper_id": "p1", "paper_title": "Paper", "journal_name": "CATENA", "publisher": "Elsevier"}
            ],
        ):
            dialog = JournalSelectionDialog(
                [{"id": "p1", "title": "Paper", "summary": "摘要", "journals": []}],
                [],
                {},
            )
            self.assertEqual(dialog._current_requirements()["rejected_journal_names"], ["CATENA"])
            dialog.close()

    def test_ai_result_is_defensively_filtered_against_archived_rejection(self) -> None:
        with patch(
            "ui.journal_selection_dialog.load_rejection_archive",
            return_value=[
                {"paper_id": "p1", "paper_title": "Paper", "journal_name": "CATENA", "publisher": "Elsevier"}
            ],
        ):
            dialog = JournalSelectionDialog(
                [{"id": "p1", "title": "Paper", "summary": "摘要", "journals": []}],
                [{"id": "catena", "name": "CATENA", "publisher": "Elsevier"}],
                {},
            )
            dialog.apply_ai_recommendation(
                {"ranked": [{"id": "catena", "fit_score": 99, "reason_cn": "不应再次推荐"}], "external_candidates": []}
            )
            self.assertEqual(dialog._candidates, [])
            dialog.close()
