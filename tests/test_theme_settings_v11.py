"""v11 semantic theme and settings contracts."""

from __future__ import annotations

import os
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QLabel, QPushButton, QVBoxLayout
from PySide6.QtGui import QFontMetricsF, QPalette

from tests import _data_root  # noqa: F401 - isolate persistence before UI imports
from ui.main_window import MainWindow
from ui.settings_dialog import SettingsDialog
from ui.theme import THEME_REGISTRY, apply_application_theme, build_application_stylesheet, ensure_application_font, get_theme
from utils.file_manager import THEME_IDS


class ThemeSettingsV11Tests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_fog_teal_is_the_default_semantic_light_theme(self) -> None:
        theme = get_theme("fog_teal")

        self.assertEqual(theme["window"], "#EAF2F2")
        self.assertEqual(theme["surface"], "#F8FBFA")
        self.assertEqual(theme["text"], "#173337")
        self.assertEqual(theme["accent"], "#246F79")
        self.assertEqual(theme["success"], "#3B7560")
        self.assertEqual(theme["warning"], "#A86D31")
        self.assertEqual(theme["danger"], "#AA514D")

    def test_single_theme_builds_readable_control_and_popup_rules(self) -> None:
        self.assertEqual(set(THEME_REGISTRY), {"fog_teal"})
        self.assertEqual(set(THEME_IDS), set(THEME_REGISTRY))
        for theme_id in THEME_REGISTRY:
            stylesheet = build_application_stylesheet(theme_id, "comfortable")
            self.assertIn("QComboBox QAbstractItemView", stylesheet)
            self.assertIn("QComboBox::down-arrow", stylesheet)
            self.assertIn("QMenu", stylesheet)
            self.assertIn("#windowRoot", stylesheet)
            self.assertIn("#nextActionButton", stylesheet)
            self.assertIn("text-align: left", stylesheet)
            self.assertIn("#unlockOverlay", stylesheet)
            self.assertIn("#unlockBrand", stylesheet)

    def test_app_theme_changes_are_centralized_and_density_is_supported(self) -> None:
        apply_application_theme(self.application, "fog_teal", "compact")

        self.assertEqual(self.application.property("research_assistant_theme_id"), "fog_teal")
        self.assertEqual(self.application.property("research_assistant_density"), "compact")
        self.assertIn("font-size: 11px", self.application.styleSheet())

    def test_theme_uses_a_cjk_capable_font_for_visible_chinese_text(self) -> None:
        """A fresh Windows profile must still render the app's Chinese UI text."""
        self.assertEqual(ensure_application_font(self.application), "Noto Sans CJK SC")
        apply_application_theme(self.application, "fog_teal", "comfortable")
        dialog = QDialog()
        layout = QVBoxLayout(dialog)
        label = QLabel("科研助手")
        layout.addWidget(label)
        dialog.show()
        self.application.processEvents()
        self.addCleanup(dialog.close)

        metrics = QFontMetricsF(label.font())

        self.assertTrue(metrics.inFontUcs4(ord("科")))

    def test_legacy_dialog_local_palette_is_replaced_when_the_dialog_is_shown(self) -> None:
        apply_application_theme(self.application, "fog_teal", "comfortable")
        dialog = QDialog()
        dialog.setStyleSheet("QDialog { background: #101a36; color: #f4f6ff; }")

        dialog.show()
        self.application.processEvents()

        self.assertNotIn("#101a36", dialog.styleSheet())
        dialog.close()

    def test_legacy_style_clear_is_deferred_until_after_the_show_filter(self) -> None:
        """Legacy local sheets must not be rewritten inside the Show stack."""
        apply_application_theme(self.application, "fog_teal", "comfortable")
        theme_filter = getattr(self.application, "_research_assistant_theme_filter")

        class ReentrantDialog(QDialog):
            def __init__(self) -> None:
                super().__init__()
                self.clear_calls = 0
                self.reentry_attempted = False

            def setStyleSheet(self, stylesheet: str) -> None:  # noqa: N802 - Qt API spelling
                if stylesheet == "":
                    self.clear_calls += 1
                    if not self.reentry_attempted:
                        self.reentry_attempted = True
                        theme_filter.eventFilter(self, QEvent(QEvent.Type.Show))
                super().setStyleSheet(stylesheet)

        dialog = ReentrantDialog()
        dialog.setStyleSheet("QDialog { background: #101a36; }")

        theme_filter.eventFilter(dialog, QEvent(QEvent.Type.Show))

        self.assertEqual(dialog.clear_calls, 0)
        QTest.qWait(20)
        self.assertEqual(dialog.clear_calls, 1)
        dialog.close()

    def test_legacy_theme_request_maps_to_the_single_palette(self) -> None:
        apply_application_theme(self.application, "fog_teal", "comfortable")
        dialog = QDialog()
        dialog.show()
        self.application.processEvents()
        before = dialog.palette().color(QPalette.ColorRole.Window).name()

        apply_application_theme(self.application, "warm_sand", "comfortable")
        self.application.processEvents()
        after = dialog.palette().color(QPalette.ColorRole.Window).name()

        self.assertEqual(before, after)
        self.assertEqual(self.application.property("research_assistant_theme_id"), "fog_teal")
        dialog.close()

    def test_existing_palette_driven_cards_ignore_retired_theme_ids(self) -> None:
        apply_application_theme(self.application, "fog_teal", "comfortable")
        dialog = QDialog()
        layout = QVBoxLayout(dialog)
        card = QFrame()
        card.setObjectName("frontierCard")
        card.setFixedSize(220, 120)
        layout.addWidget(card)
        dialog.resize(260, 160)
        dialog.show()
        self.application.processEvents()
        QTest.qWait(20)
        self.addCleanup(dialog.close)
        self.addCleanup(lambda: apply_application_theme(self.application, "fog_teal", "comfortable"))

        point = card.mapTo(dialog, card.rect().center())
        before = dialog.grab().toImage().pixelColor(point).name()

        apply_application_theme(self.application, "night_coral", "comfortable")
        self.application.processEvents()
        QTest.qWait(20)
        after = dialog.grab().toImage().pixelColor(point).name()

        self.assertEqual(before, after)

    def test_row_actions_keep_the_standard_text_size(self) -> None:
        """Frontier, papers and profile actions share rowButton styling."""
        apply_application_theme(self.application, "fog_teal", "comfortable")
        dialog = QDialog()
        layout = QVBoxLayout(dialog)
        button = QPushButton("期刊")
        button.setObjectName("rowButton")
        layout.addWidget(button)
        dialog.show()
        self.application.processEvents()
        QTest.qWait(20)
        self.addCleanup(dialog.close)

        self.assertEqual(button.font().pixelSize(), 12)

    def test_settings_exposes_appearance_without_a_mode_selector(self) -> None:
        dialog = SettingsDialog(
            {
                "appearance": {"theme_id": "fog_teal", "density": "comfortable"},
                "application_mode": "widget",
                "opacity": 96,
                "journal_import_shortcut": {"mode": "double_tab", "sequence": "Ctrl+Alt+J"},
                "window_visibility_shortcut": {"mode": "double_space", "sequence": "Ctrl+Alt+Space"},
            }
        )

        self.assertFalse(hasattr(dialog, "theme_selector"))
        self.assertEqual(dialog.density_selector.currentData(), "comfortable")
        self.assertFalse(hasattr(dialog, "application_mode_selector"))
        payload = dialog.values()
        self.assertEqual(payload["appearance"], {"theme_id": "fog_teal", "density": "comfortable"})
        self.assertEqual(payload["application_mode"], "widget")
        dialog.close()

    def test_legacy_software_setting_still_uses_widget_opacity(self) -> None:
        dialog = SettingsDialog(
            {
                "application_mode": "software",
                "opacity": 86,
            }
        )

        self.assertTrue(dialog.opacity_slider.isEnabled())
        self.assertEqual(dialog.opacity_value.text(), "86%")
        dialog.close()

    def test_settings_preserves_easyscholar_policy_for_the_configuration_dialog(self) -> None:
        dialog = SettingsDialog(
            {
                "easyscholar": {
                    "enabled": True,
                    "secret_key_secret": "dpapi-token",
                    "cache_days": 45,
                    "last_auto_checked": "2026-08-21",
                },
            }
        )

        self.assertEqual(
            dialog.values()["easyscholar"],
            {
                "enabled": True,
                "secret_key_secret": "dpapi-token",
                "cache_days": 45,
                "last_auto_checked": "2026-08-21",
            },
        )
        dialog.close()

    def test_main_window_applies_saved_theme_and_migrates_legacy_mode(self) -> None:
        window = MainWindow()
        updated = dict(window.settings)
        updated["appearance"] = {"theme_id": "warm_sand", "density": "compact"}
        updated["application_mode"] = "software"

        window._apply_settings(updated)

        self.assertEqual(self.application.property("research_assistant_theme_id"), "fog_teal")
        self.assertEqual(self.application.property("research_assistant_density"), "compact")
        self.assertEqual(window.application_mode, "widget")
        if window.tray_icon:
            window.tray_icon.hide()
        window._global_hotkey.close()
        window._visibility_hotkey.close()
        window.deleteLater()

    def test_deferred_main_window_disposal_leaves_event_processing_safe(self) -> None:
        """A queued HOME callback must not touch a deleted window tree."""
        window = MainWindow()
        window._global_hotkey.close()
        window._visibility_hotkey.close()
        window.deleteLater()

        self.application.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.application.processEvents()

        dialog = QDialog()
        dialog.show()
        QTest.qWait(20)
        dialog.close()

    def test_legacy_software_request_keeps_the_widget_surface(self) -> None:
        """A removed mode value cannot switch away from the compact widget."""
        window = MainWindow()
        self.addCleanup(lambda: window.tray_icon.hide() if window.tray_icon else None)
        self.addCleanup(window._global_hotkey.close)
        self.addCleanup(window._visibility_hotkey.close)
        self.addCleanup(window.deleteLater)

        window._apply_application_mode("software", persist=False)

        self.assertEqual(window.application_mode, "widget")
        self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertAlmostEqual(window.windowOpacity(), window.settings["opacity"] / 100, delta=1 / 255)

    def test_main_window_applies_easyscholar_configuration_without_resetting_its_cache_marker(self) -> None:
        window = MainWindow()
        updated = dict(window.settings)
        updated["easyscholar"] = {
            "enabled": True,
            "secret_key_secret": "dpapi-token",
            "cache_days": 45,
            "last_auto_checked": "2026-08-21",
        }

        window._apply_settings(updated)

        self.assertEqual(window.settings["easyscholar"], updated["easyscholar"])
        if window.tray_icon:
            window.tray_icon.hide()
        window._global_hotkey.close()
        window._visibility_hotkey.close()
        window.deleteLater()

    def test_main_window_skips_the_retired_local_navy_stylesheet_at_startup(self) -> None:
        class ThemeProbe:
            settings = {"appearance": {"theme_id": "fog_teal", "density": "comfortable"}}

            def __init__(self) -> None:
                self.local_styles: list[str] = []
                self.applied: list[tuple[str, str]] = []

            def setStyleSheet(self, stylesheet: str) -> None:  # noqa: N802 - Qt API spelling
                self.local_styles.append(stylesheet)

            def _apply_theme(self, theme_id: str, density: str) -> None:
                self.applied.append((theme_id, density))

        probe = ThemeProbe()
        MainWindow._apply_styles(probe)

        self.assertEqual(probe.local_styles, [""])
        self.assertEqual(probe.applied, [("fog_teal", "comfortable")])
