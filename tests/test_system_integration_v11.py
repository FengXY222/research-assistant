"""Non-destructive v11 desktop-integration policy contracts."""

from __future__ import annotations

from unittest import TestCase

from PySide6.QtCore import Qt

from ui.main_window import _main_window_flags
from utils.global_hotkey import normalize_shortcut_config
from utils.window_mode import should_hide_to_tray


class SystemIntegrationV11Tests(TestCase):
    def test_shortcut_config_keeps_the_requested_double_tap_or_custom_sequence(self) -> None:
        self.assertEqual(
            normalize_shortcut_config(None, "double_space", "Ctrl+Alt+Space"),
            {"mode": "double_space", "sequence": "Ctrl+Alt+Space"},
        )
        self.assertEqual(
            normalize_shortcut_config({"mode": "sequence", "sequence": "Ctrl+Shift+K"}, "double_tab", "Ctrl+Alt+J"),
            {"mode": "sequence", "sequence": "Ctrl+Shift+K"},
        )
        self.assertEqual(
            normalize_shortcut_config({"mode": "unknown", "sequence": "ignored"}, "double_tab", "Ctrl+Alt+J"),
            {"mode": "double_tab", "sequence": "Ctrl+Alt+J"},
        )

    def test_legacy_software_mode_migrates_to_widget_window_policies(self) -> None:
        widget_flags = _main_window_flags("widget", always_on_top=True, click_through=True)
        software_flags = _main_window_flags("software", always_on_top=False, click_through=True)

        self.assertTrue(widget_flags & Qt.WindowType.Tool)
        self.assertTrue(widget_flags & Qt.WindowType.WindowTransparentForInput)
        self.assertTrue(software_flags & Qt.WindowType.Tool)
        self.assertTrue(software_flags & Qt.WindowType.WindowTransparentForInput)
        self.assertTrue(should_hide_to_tray("widget"))
        self.assertTrue(should_hide_to_tray("software"))
