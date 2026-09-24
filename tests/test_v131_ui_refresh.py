"""v13.1 shared UI and settings-center acceptance contracts."""

from __future__ import annotations

import os
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QPushButton

from tests import _data_root  # noqa: F401 - isolate persistence before UI imports
from ui.main_window import MainWindow
from ui.page_kit import EllipsisMenu, FilterBar, PageHeader, StatusBadge
from ui.settings_center import SECTION_IDS, SettingsCenterDialog
from ui.settings_dialog import SettingsDialog
from ui.intelligence_dialog import IntelligenceSettingsDialog
from ui.theme import SECTION_ACCENTS, THEME_REGISTRY, get_theme


class V131UiRefreshTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_retired_theme_ids_resolve_to_the_single_theme(self) -> None:
        self.assertEqual(set(THEME_REGISTRY), {"fog_teal"})
        self.assertEqual(get_theme("night_coral"), get_theme("fog_teal"))
        self.assertEqual(len(set(SECTION_ACCENTS.values())), 8)

    def test_page_kit_exposes_header_filter_badge_and_overflow(self) -> None:
        header = PageHeader("每日前沿", accent="frontier")
        primary = header.add_primary_action("检查更新", lambda: None)
        overflow = header.add_overflow_menu()
        overflow.add_action("研究设置", lambda: None)
        filters = FilterBar("搜索")
        combo = filters.add_filter(["全部", "已读"])
        badge = StatusBadge("Q1", "success")

        self.assertEqual(header.property("accent"), "frontier")
        self.assertEqual(primary.property("accent"), "frontier")
        self.assertIsInstance(overflow, EllipsisMenu)
        self.assertIsInstance(combo, QComboBox)
        self.assertEqual(badge.property("tone"), "success")

    def test_settings_center_has_two_direct_first_level_sections(self) -> None:
        dialog = SettingsCenterDialog({"appearance": {"theme_id": "warm_sand", "density": "comfortable"}})

        self.assertEqual(SECTION_IDS, ("general", "enhancement"))
        self.assertEqual(dialog.stack.count(), 2)
        self.assertEqual(dialog.navigation.count(), len(SECTION_IDS))
        self.assertFalse(hasattr(dialog, "theme_selector"))
        self.assertIsInstance(dialog.stack.widget(0), SettingsDialog)
        self.assertIsInstance(dialog.stack.widget(1), IntelligenceSettingsDialog)
        labels = [dialog.navigation.item(index).text() for index in range(dialog.navigation.count())]
        self.assertEqual(labels, ["通用设置", "增强服务"])
        button_texts = [button.text() for button in dialog.findChildren(QPushButton)]
        self.assertNotIn("编辑通用与数据", button_texts)
        self.assertNotIn("配置 AI 与接口", button_texts)
        dialog.open_section("ai")
        self.assertEqual(dialog.navigation.currentRow(), 1)
        dialog.close()

    def test_main_surface_uses_horizontal_chinese_nav_and_shared_page_headers(self) -> None:
        window = MainWindow()
        self.addCleanup(lambda: window.tray_icon.hide() if window.tray_icon else None)
        self.addCleanup(window._global_hotkey.close)
        self.addCleanup(window._visibility_hotkey.close)
        self.addCleanup(window.deleteLater)

        self.assertEqual([button.text() for button in window._nav_buttons], ["概览", "工作", "论文", "文献"])
        self.assertTrue(all(not button.icon().isNull() for button in window._nav_buttons))
        self.assertGreaterEqual(len(window.findChildren(PageHeader)), 8)
        frontier_actions = [action.text() for action in window.frontier_page.more_actions_button.menu().actions()]
        self.assertNotIn("切换列表", frontier_actions)
        journal_actions = [action.text() for action in window.journal_page.tools_button.menu().actions()]
        for retired in (
            "数据工具…",
            "补充土地科学期刊",
            "全部更新缺少/变化期刊",
            "联网补全期刊信息",
            "补全资料不完整的期刊",
            "模糊校验出版社（Crossref）",
            "新增期刊",
        ):
            self.assertNotIn(retired, journal_actions)
        self.assertFalse(window.journal_page.add_button.isHidden())
