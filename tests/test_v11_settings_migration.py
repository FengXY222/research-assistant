"""Tests for v11 settings migration and dual window profiles."""

from __future__ import annotations

from unittest import TestCase

from tests import _data_root  # noqa: F401 - set data root before persistence import
from utils.file_manager import normalize_app_settings


class V11SettingsTests(TestCase):
    def test_old_settings_gain_safe_v11_defaults(self) -> None:
        normalized = normalize_app_settings({"opacity": 96, "nav_order": ["home", "todo"]})

        self.assertEqual(normalized["appearance"]["theme_id"], "fog_teal")
        self.assertEqual(normalized["application_mode"], "widget")
        self.assertEqual(normalized["workbench_order"], ["home", "work", "papers", "library"])
        self.assertGreaterEqual(normalized["widget_window"]["width"], 400)
        self.assertEqual(normalized["nav_order"], ["home", "todo", "papers", "notes", "journals", "frontier", "achievements"])

    def test_widget_geometry_survives_and_software_mode_is_ignored(self) -> None:
        normalized = normalize_app_settings({"window": {"width": 520, "height": 680, "x": 4, "y": 5}})

        self.assertEqual(normalized["widget_window"]["width"], 520)
        self.assertEqual(normalized["widget_window"]["height"], 680)
        self.assertEqual(normalized["widget_window"]["x"], 4)
        self.assertEqual(normalized["widget_window"]["y"], 5)
        self.assertEqual(normalized["application_mode"], "widget")

    def test_legacy_software_mode_migrates_to_widget(self) -> None:
        normalized = normalize_app_settings({"application_mode": "software"})

        self.assertEqual(normalized["application_mode"], "widget")

    def test_invalid_new_settings_fall_back_without_losing_legacy_values(self) -> None:
        normalized = normalize_app_settings(
            {
                "application_mode": "unsupported",
                "appearance": {"theme_id": "not-a-theme", "density": "wrong"},
                "window": {"locked": True, "width": 550, "height": 700},
                "journal_import_shortcut": {"mode": "sequence", "sequence": "Ctrl+Alt+J"},
            }
        )

        self.assertEqual(normalized["application_mode"], "widget")
        self.assertEqual(normalized["appearance"], {"theme_id": "fog_teal", "density": "comfortable"})
        self.assertTrue(normalized["widget_window"]["locked"])
        self.assertEqual(normalized["journal_import_shortcut"]["sequence"], "Ctrl+Alt+J")

    def test_new_theme_id_survives_settings_normalization(self) -> None:
        """A selectable non-monochrome theme must persist across restart."""
        normalized = normalize_app_settings(
            {"appearance": {"theme_id": "night_coral", "density": "comfortable"}}
        )

        self.assertEqual(normalized["appearance"]["theme_id"], "night_coral")
