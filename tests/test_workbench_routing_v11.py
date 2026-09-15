"""v11 primary-workbench routing contracts."""

from __future__ import annotations

from unittest import TestCase
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from ui.workbench_shell import WorkbenchShell, resolve_route
from ui.quick_capture_dialog import classify_quick_capture_locally


class WorkbenchRoutingTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
    def test_legacy_routes_resolve_to_one_of_four_workbenches(self) -> None:
        self.assertEqual(resolve_route("todo").workbench, "work")
        self.assertEqual(resolve_route("notes").workbench, "work")
        self.assertEqual(resolve_route("achievements").workbench, "papers")
        self.assertEqual(resolve_route("frontier").workbench, "library")
        self.assertEqual(resolve_route("special_issues").anchor, "special_issues")
        self.assertEqual(resolve_route("papers").anchor, "submissions")

    def test_primary_routes_remain_stable(self) -> None:
        self.assertEqual(resolve_route("home").workbench, "home")
        self.assertEqual(resolve_route("work").anchor, "tasks")
        self.assertEqual(resolve_route("library").anchor, "frontier")

    def test_one_page_set_stays_in_widget_mode_for_legacy_mode_requests(self) -> None:
        pages = {key: QWidget() for key in ("home", "todo", "notes", "papers", "achievements", "journals", "frontier", "special_issues")}
        shell = WorkbenchShell(pages)
        shell.set_mode("widget")
        self.assertEqual(shell.navigate("frontier").workbench, "library")
        shell.set_mode("software")
        self.assertEqual(shell.mode, "widget")
        self.assertEqual(shell.current_route.anchor, "frontier")
        self.assertIsNotNone(pages["frontier"].parentWidget())
        shell.close()

    def test_quick_capture_requires_confirmation_before_write(self) -> None:
        draft = classify_quick_capture_locally("查一下 2025 年遥感秸秆研究")

        self.assertEqual(draft["kind"], "inspiration")
        self.assertFalse(draft["confirmed"])
