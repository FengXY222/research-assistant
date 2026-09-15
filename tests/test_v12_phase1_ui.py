"""Offscreen geometry contracts for the v12 research-profile workbench."""

from __future__ import annotations

import os
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QFrame, QLineEdit, QSplitter, QTabBar, QTabWidget

from ui.frontier_settings_dialog import FrontierSettingsDialog


class PhaseOneUiTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def _dialog(self) -> FrontierSettingsDialog:
        dialog = FrontierSettingsDialog(
            {
                "terms": [
                    {
                        "canonical_en": "soil organic carbon mapping with multisource remote sensing",
                        "translation_zh": "多源遥感土壤有机碳制图",
                        "weight": 92,
                        "locked": True,
                        "source": "paper",
                    },
                    {"canonical_en": "digital soil mapping", "translation_zh": "数字土壤制图", "weight": 76},
                ],
                "pending_terms": [
                    {"canonical_en": "mineral-associated organic carbon", "translation_zh": "矿物结合态有机碳", "weight": 64}
                ],
                "excluded_entries": [
                    {"canonical_en": "soil microbes", "translation_zh": "土壤微生物", "status": "excluded"}
                ],
            }
        )
        dialog.show()
        self.application.processEvents()
        self.addCleanup(dialog.close)
        return dialog

    def test_workbench_is_1024_by_768_with_profile_strategy_and_journal_tabs(self) -> None:
        dialog = self._dialog()
        tabs = dialog.findChild(QTabWidget, "researchSettingsTabs")

        self.assertEqual(dialog.size().width(), 1024)
        self.assertEqual(dialog.size().height(), 768)
        self.assertIsNotNone(tabs)
        assert tabs is not None
        self.assertEqual(
            [tabs.tabText(index) for index in range(tabs.count())],
            ["关键词工作台", "发现策略", "期刊排序"],
        )
        self.assertEqual(tabs.currentIndex(), 0)
        tab_bar = dialog.findChild(QTabBar, "researchSettingsTabBar")
        self.assertIsNotNone(tab_bar)
        assert tab_bar is not None
        self.assertGreaterEqual(tab_bar.minimumHeight(), 40)

    def test_keyword_workbench_has_three_stable_nonoverlapping_regions(self) -> None:
        dialog = self._dialog()
        splitter = dialog.findChild(QSplitter, "profileRegionSplitter")
        regions = [dialog.findChild(QFrame, name) for name in ("activeTermsRegion", "pendingTermsRegion", "excludedTermsRegion")]

        self.assertIsNotNone(splitter)
        self.assertTrue(all(region is not None for region in regions))
        assert splitter is not None
        self.assertEqual(splitter.orientation(), Qt.Orientation.Horizontal)
        geometries = [region.geometry() for region in regions if region is not None]
        self.assertTrue(all(rect.width() >= 210 for rect in geometries))
        self.assertLess(geometries[0].right(), geometries[1].left())
        self.assertLess(geometries[1].right(), geometries[2].left())

    def test_every_visible_term_row_has_an_editable_chinese_translation(self) -> None:
        dialog = self._dialog()
        translations = dialog.findChildren(QLineEdit, "profileTranslationEdit")

        self.assertGreaterEqual(len(translations), 4)
        self.assertIn("多源遥感土壤有机碳制图", [editor.text() for editor in translations])
        self.assertIn("土壤微生物", [editor.text() for editor in translations])

    def test_long_active_term_stays_inside_its_region(self) -> None:
        dialog = self._dialog()
        row = dialog.findChild(QFrame, "profileActiveRow")
        region = dialog.findChild(QFrame, "activeTermsRegion")

        self.assertIsNotNone(row)
        self.assertIsNotNone(region)
        assert row is not None and region is not None
        self.assertLessEqual(row.width(), region.width())
        self.assertGreaterEqual(row.height(), 92)
