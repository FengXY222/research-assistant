"""v12 exposes one compact widget shell and no application-mode choice."""

from __future__ import annotations

import os
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QWidget

from ui.settings_dialog import SettingsDialog
from ui.workbench_shell import WorkbenchShell
from utils.file_manager import normalize_app_settings
from utils.window_mode import mode_minimum_size, mode_window_key, normalize_application_mode


class WidgetOnlyV12Tests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_legacy_software_values_always_migrate_to_widget(self) -> None:
        self.assertEqual(normalize_application_mode("software"), "widget")
        self.assertEqual(mode_window_key("software"), "widget_window")
        self.assertEqual(mode_minimum_size("software"), (400, 480))
        self.assertEqual(normalize_app_settings({"application_mode": "software"})["application_mode"], "widget")

    def test_settings_has_no_mode_selector_or_software_copy(self) -> None:
        dialog = SettingsDialog({"application_mode": "software", "opacity": 86})
        self.addCleanup(dialog.close)

        self.assertFalse(hasattr(dialog, "application_mode_selector"))
        visible_copy = "\n".join(label.text() for label in dialog.findChildren(QLabel))
        self.assertNotIn("软件模式", visible_copy)
        self.assertNotIn("打开方式", visible_copy)
        self.assertTrue(dialog.opacity_slider.isEnabled())
        self.assertEqual(dialog.opacity_value.text(), "86%")
        self.assertEqual(dialog.values()["application_mode"], "widget")

    def test_legacy_mode_request_builds_only_the_widget_stack(self) -> None:
        pages = {key: QWidget() for key in ("home", "todo", "notes", "papers", "achievements", "journals", "frontier")}
        shell = WorkbenchShell(pages)
        self.addCleanup(shell.close)

        shell.set_mode("software")
        target = shell.navigate("frontier")

        self.assertEqual(shell.mode, "widget")
        self.assertEqual(target.workbench, "library")
        self.assertIsNotNone(shell._primary_stack)
        self.assertIsNone(shell._software_scroll)
        self.assertFalse(hasattr(shell, "_build_software_shell"))
        self.assertTrue(all(page.parentWidget() is not None for page in pages.values()))
