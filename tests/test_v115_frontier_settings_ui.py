from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QLineEdit, QPushButton, QScrollArea

from ui.frontier_settings_dialog import FrontierSettingsDialog


class V115FrontierSettingsUiTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_large_frontier_settings_module_exposes_core_sections(self) -> None:
        dialog = FrontierSettingsDialog(
            {
                "terms": [{"id": "soc", "text": "soil organic carbon", "weight": 100, "locked": True}],
                "pending_terms": [{"id": "p1", "text": "carbon sequestration"}],
                "filter_known_q3_q4": True,
            }
        )

        self.assertIsNotNone(dialog.findChild(QCheckBox, "requireQ1Q2Check"))
        self.assertIsNotNone(dialog.findChild(QLineEdit, "manualKeywordEdit"))
        self.assertIsNotNone(dialog.findChild(QPushButton, "extractKeywordsButton"))
        self.assertIsNotNone(dialog.findChild(QComboBox, "frontierUpdateFrequency"))
        self.assertIsNotNone(dialog.findChild(QScrollArea))
        self.assertEqual(dialog.profile()["require_verified_jcr_q1_q2"], True)
        dialog.close()

    def test_settings_module_uses_the_dedicated_workbench_size(self) -> None:
        papers = [{"id": "p1", "title": "A very long paper title that must never force the research settings workbench beyond the small widget viewport", "summary": "摘要"}]
        with patch("ui.frontier_settings_dialog.load_papers", return_value=papers):
            dialog = FrontierSettingsDialog({"terms": [{"id": "soc", "text": "soil organic carbon", "weight": 100, "locked": True}]})
            dialog.show()
            self.application.processEvents()

            self.assertEqual((dialog.width(), dialog.height()), (1024, 768))
            dialog.close()

    def test_keyword_workbench_keeps_import_actions_in_the_profile_tab(self) -> None:
        dialog = FrontierSettingsDialog({"primary_keywords": ["soil organic carbon"]})
        dialog.show()
        self.application.processEvents()

        manual = dialog.findChild(QLineEdit, "manualKeywordEdit")
        extract = dialog.findChild(QPushButton, "extractKeywordsButton")
        self.assertIsNotNone(manual)
        self.assertIsNotNone(extract)
        self.assertIsNotNone(dialog.extract_status)
        self.assertTrue(manual.isVisible())
        self.assertTrue(extract.isVisible())
        self.assertTrue(dialog.extract_status.isVisible())
        dialog.close()

    def test_selected_paper_keywords_enter_the_pending_review_queue(self) -> None:
        papers = [
            {
                "id": "paper-1",
                "title": "遥感驱动的土壤有机碳制图",
                "summary": "利用多源遥感和机器学习开展区域土壤有机碳制图。",
                "keywords": ["soil organic carbon", "遥感", "机器学习"],
            }
        ]
        with patch("ui.frontier_settings_dialog.load_papers", return_value=papers):
            dialog = FrontierSettingsDialog({"terms": []})
            dialog.show()
            self.application.processEvents()
            dialog._extract_keywords()
            pending = {str(item.get("text", "")) for item in dialog.profile()["pending_terms"]}

        self.assertIn("soil organic carbon", pending)
        self.assertIn("遥感", pending)
        dialog.close()

    def test_ai_bilingual_terms_keep_translation_in_pending_queue(self) -> None:
        dialog = FrontierSettingsDialog({"terms": []})
        dialog.show()
        self.application.processEvents()
        dialog._keyword_ai_source_key = "ai_pdf"
        dialog._keyword_ai_reason = "来自扫描 PDF"
        dialog._keyword_ai_display_name = "scan.pdf"

        dialog._keyword_ai_finished(
            {
                "terms": [
                    {
                        "canonical_en": "digital soil mapping",
                        "translation_zh": "数字土壤制图",
                        "weight": 82,
                        "confidence": "high",
                        "evidence": ["OCR full text"],
                    }
                ],
                "model": "test",
            }
        )

        pending = dialog.profile()["pending_terms"][0]
        self.assertEqual(pending["translation_zh"], "数字土壤制图")
        self.assertEqual(pending["weight"], 82)
        dialog.close()

    def test_rejecting_pending_in_dialog_persists_a_block(self) -> None:
        dialog = FrontierSettingsDialog(
            {"pending_terms": [{"canonical_en": "soil microbes", "translation_zh": "土壤微生物"}]}
        )
        pending_id = dialog.profile()["pending_terms"][0]["id"]

        dialog._reject_pending(pending_id)
        profile = dialog.profile()

        self.assertEqual(profile["pending_terms"], [])
        self.assertEqual(profile["blocked_terms"][0]["canonical_key"], "soil microbes")
        dialog.close()
