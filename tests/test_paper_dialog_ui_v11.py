"""Usability contracts for the paper editor dialog."""

from __future__ import annotations

import os
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialogButtonBox

from tests import _data_root  # noqa: F401 - isolate persistence before UI imports
from ui.paper_page import PaperDialog


class PaperDialogUiV11Tests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_paper_editor_uses_chinese_save_and_cancel_labels(self) -> None:
        dialog = PaperDialog()
        buttons = dialog.findChild(QDialogButtonBox)

        self.assertIsNotNone(buttons)
        self.assertEqual(buttons.button(QDialogButtonBox.StandardButton.Save).text(), "保存")
        self.assertEqual(buttons.button(QDialogButtonBox.StandardButton.Cancel).text(), "取消")
        dialog.close()

    def test_existing_long_title_is_presented_from_its_beginning(self) -> None:
        title = "A very long research paper title that should not open scrolled to its trailing words"
        dialog = PaperDialog(paper={"title": title, "journals": []})

        self.assertEqual(dialog.title_edit.cursorPosition(), 0)
        dialog.close()
