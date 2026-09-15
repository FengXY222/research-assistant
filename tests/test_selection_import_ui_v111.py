"""Regression coverage for bringing an AI-only journal into normal local data."""

from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialog

from tests import _data_root  # noqa: F401 - isolate page persistence before imports
from ui.paper_page import PaperPage


class _ExternalSelectionDialog:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def exec(self):
        return QDialog.DialogCode.Accepted

    @staticmethod
    def selection() -> tuple[str, str]:
        return "paper-1", "external:journal"

    @staticmethod
    def selected_journal() -> dict:
        return {"id": "external:journal", "name": "External Journal", "publisher": "Press", "jcr": {"status": "pending", "metrics": []}}

    @staticmethod
    def selected_candidate() -> dict:
        return {"is_external": True, "reason_cn": "摘要高度匹配", "journal": _ExternalSelectionDialog.selected_journal()}

    @staticmethod
    def is_external_selection() -> bool:
        return True

    @staticmethod
    def selection_action() -> str:
        return "path"


class SelectionImportUiV111Tests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_paper_page_imports_external_candidate_before_adding_its_submission_path(self) -> None:
        page = PaperPage()
        page.papers = [{"id": "paper-1", "title": "SOC paper", "journals": []}]
        saved_library: list[list[dict]] = []
        committed: list[bool] = []
        page._commit_papers = lambda: committed.append(True)  # type: ignore[method-assign]
        with patch("ui.paper_page.load_journal_library", return_value=[]), patch(
            "ui.paper_page.save_journal_library", side_effect=lambda rows: saved_library.append(rows)
        ), patch("ui.paper_page.JournalSelectionDialog", _ExternalSelectionDialog):
            page._choose_journal()

        self.assertEqual(len(saved_library), 1)
        self.assertEqual(saved_library[0][0]["name"], "External Journal")
        self.assertEqual(saved_library[0][0]["jcr"]["status"], "pending")
        self.assertEqual(page.papers[0]["journals"][0]["name"], "External Journal")
        self.assertEqual(committed, [True])
        page.close()
