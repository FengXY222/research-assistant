"""Compact LIBRARY special-issue widget contracts."""

from __future__ import annotations

import os
from datetime import date
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

from tests import _data_root  # noqa: F401
from ui.special_issue_page import SpecialIssuePage
from ui.workbench_shell import WorkbenchShell, resolve_route
from utils.special_issue_repository import save_special_issue_store


class SpecialIssueWidgetUiTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        save_special_issue_store(
            {
                "items": [
                    {
                        "id": "saved-1",
                        "title": "Saved soil carbon collection",
                        "journal": "Geoderma",
                        "publisher": "Elsevier",
                        "deadline": "2027-01-15",
                        "status": "saved",
                        "match": {"score": 82, "formal": True, "reason": "与土壤有机碳制图高度匹配"},
                    },
                    {
                        "id": "recommended-1",
                        "title": "High match remote sensing topic",
                        "journal": "Remote Sensing of Environment",
                        "publisher": "Elsevier",
                        "deadline": "2027-02-20",
                        "status": "unread",
                        "verification_status": "official_verified",
                        "official_checked_at": "2026-08-31T12:00:00",
                        "call_status": "open",
                        "scope_is_complete": True,
                        "match": {"score": 91, "rank_score": 91, "formal": True, "reason": "遥感与数字土壤制图匹配", "relation": "core"},
                    },
                    {
                        "id": "ignored-1",
                        "title": "Ignored call",
                        "deadline": "2027-03-01",
                        "status": "ignored",
                        "match": {"score": 99, "formal": True, "reason": "不应预览"},
                    },
                    {
                        "id": "closed-1",
                        "title": "Closed high score call",
                        "journal": "Journal Closed",
                        "deadline": "2027-03-01",
                        "status": "unread",
                        "verification_status": "closed",
                        "call_status": "closed",
                        "scope_is_complete": True,
                        "match": {"score": 99, "rank_score": 99, "formal": True, "reason": "旧高分", "relation": "core"},
                    },
                ]
            }
        )
        self.page = SpecialIssuePage(today_provider=lambda: date(2026, 8, 31))
        self.page.resize(400, 480)
        self.page.show()
        for _attempt in range(100):
            self.application.processEvents()
            if self.page.preview_rows:
                break
            QTest.qWait(10)

    def tearDown(self) -> None:
        self.page.close()

    def test_summary_and_saved_first_preview_are_data_driven(self) -> None:
        self.assertEqual(self.page.preview_issue_ids, ["saved-1", "recommended-1"])
        self.assertEqual(self.page.findChild(QLabel, "specialIssueUnreadCount").text(), "1")
        self.assertEqual(self.page.findChild(QLabel, "specialIssueSavedCount").text(), "1")
        self.assertNotIn("closed-1", self.page.preview_issue_ids)
        nearest = self.page.findChild(QLabel, "specialIssueNearestDeadline").text()
        self.assertIn("137", nearest)

    def test_explicit_workbench_action_and_row_emit_without_auto_open_on_navigation(self) -> None:
        emitted: list[str] = []
        self.page.open_workbench.connect(emitted.append)
        pages = {key: QWidget() for key in ("home", "todo", "notes", "papers", "achievements", "journals", "frontier")}
        pages["special_issues"] = self.page
        shell = WorkbenchShell(pages)
        shell.resize(400, 480)
        shell.set_mode("widget")
        shell.navigate("special_issues")
        self.application.processEvents()
        self.assertEqual(emitted, [])
        self.page.findChild(QPushButton, "specialIssueOpenWorkbench").click()
        self.assertEqual(emitted, [""])
        self.page.preview_rows[0].clicked.emit("saved-1")
        self.assertEqual(emitted[-1], "saved-1")
        shell.close()

    def test_library_has_three_ordered_chips_and_frontier_remains_default(self) -> None:
        self.assertEqual(resolve_route("library").anchor, "frontier")
        self.assertEqual(resolve_route("special_issues").anchor, "special_issues")
        pages = {key: QWidget() for key in ("home", "todo", "notes", "papers", "achievements", "journals", "frontier", "special_issues")}
        shell = WorkbenchShell(pages)
        shell.set_mode("widget")
        shell.navigate("library")
        panel = shell._primary_stack.widget(3)
        chips = [button.text() for button in panel.findChildren(QPushButton) if button.property("workbench_anchor")]
        self.assertEqual(chips, ["每日前沿", "期刊库", "特刊征稿"])
        self.assertEqual(shell.current_route.anchor, "frontier")
        shell.close()

    def test_400x480_surface_keeps_visible_controls_inside_bounds(self) -> None:
        self.assertEqual(self.page.size(), QSize(400, 480))
        page_rect = self.page.rect()
        for widget in self.page.findChildren(QWidget):
            if not widget.isVisible() or widget.window() is not self.page.window():
                continue
            top_left = widget.mapTo(self.page, QPoint(0, 0))
            bottom_right = widget.mapTo(self.page, QPoint(widget.width() - 1, widget.height() - 1))
            self.assertGreaterEqual(top_left.x(), page_rect.left() - 1, widget.objectName())
            self.assertGreaterEqual(top_left.y(), page_rect.top() - 1, widget.objectName())
            self.assertLessEqual(bottom_right.x(), page_rect.right() + 1, widget.objectName())
            self.assertLessEqual(bottom_right.y(), page_rect.bottom() + 1, widget.objectName())
        open_button = self.page.findChild(QPushButton, "specialIssueOpenWorkbench")
        self.assertTrue(open_button.isVisible())
        self.assertGreaterEqual(open_button.height(), 28)
