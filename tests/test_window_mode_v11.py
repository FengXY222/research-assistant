"""Pure v11 window-mode policy tests."""

from __future__ import annotations

from unittest import TestCase

from utils.window_mode import mode_minimum_size, mode_window_key, normalize_application_mode, should_hide_to_tray


class WindowModeTests(TestCase):
    def test_every_legacy_mode_uses_the_widget_policy(self) -> None:
        self.assertEqual(normalize_application_mode("software"), "widget")
        self.assertEqual(normalize_application_mode("anything"), "widget")
        self.assertEqual(mode_window_key("widget"), "widget_window")
        self.assertEqual(mode_window_key("software"), "widget_window")
        self.assertEqual(mode_minimum_size("widget"), (400, 480))
        self.assertEqual(mode_minimum_size("software"), (400, 480))
        self.assertTrue(should_hide_to_tray("widget"))
        self.assertTrue(should_hide_to_tray("software"))
