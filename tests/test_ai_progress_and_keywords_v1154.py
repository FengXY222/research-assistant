from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ui.ai_progress import AiProgressPanel
from ui.journal_selection_dialog import JournalSelectionDialog
from utils.ai_service import extract_research_keywords_with_ai


class AiProgressAndKeywordTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_progress_panel_is_hidden_until_started_and_reaches_completion(self) -> None:
        panel = AiProgressPanel()
        self.assertTrue(panel.isHidden())
        panel.begin("开始")
        self.assertTrue(panel.isVisible())
        self.assertEqual(panel.progress.value(), 0)
        panel.update("处理中", 61)
        self.assertEqual(panel.progress.value(), 61)
        panel.complete("完成")
        self.assertEqual(panel.progress.value(), 100)
        self.assertEqual(panel.status.text(), "完成")
        panel.deleteLater()

    def test_keyword_ai_filters_pdf_metadata_and_keeps_filename_out_of_prompt(self) -> None:
        captured: dict = {}
        progress: list[tuple[str, int | None]] = []

        def fake_chat(_config, _key, _system, payload, _limit):
            captured.update(payload)
            return {
                "keywords": [
                    "soil organic carbon",
                    "pdf",
                    "doi",
                    "Article https://example.org/a.pdf",
                    "remote sensing",
                    "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                ],
                "reason_cn": "来自研究对象和方法",
            }

        with patch("utils.ai_service._require_config", return_value=({"model": "test-model"}, "key")), patch(
            "utils.ai_service._chat_json", side_effect=fake_chat
        ):
            result = extract_research_keywords_with_ai(
                {
                    "source_type": "pdf",
                    "title": "",
                    "source_name": "my-paper-2026.pdf",
                    "summary": "Soil organic carbon mapping by remote sensing.",
                },
                lambda message, value=None: progress.append((message, value)),
            )

        self.assertEqual(result["keywords"], ["soil organic carbon", "remote sensing"])
        self.assertEqual(captured["paper_title"], "")
        self.assertEqual(captured["source_name_metadata_only"], "my-paper-2026.pdf")
        self.assertTrue(progress)
        self.assertEqual(progress[-1][1], 100)

    def test_journal_selection_click_surfaces_unconfigured_state_immediately(self) -> None:
        dialog = JournalSelectionDialog(
            [{"id": "p1", "title": "Paper", "summary": "Summary", "keywords": []}],
            [],
            {},
        )
        dialog.show()
        self.application.processEvents()
        with patch("ui.journal_selection_dialog.is_deepseek_ready", return_value=False):
            dialog._run_ai_recommendation()
        self.assertTrue(dialog.ai_progress.isVisible())
        self.assertIn("尚未配置", dialog.ai_status.text())
        self.assertEqual(dialog.ai_button.text(), "开始 AI 选刊")
        dialog.close()
