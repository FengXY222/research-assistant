"""Large special-issue workbench layout, filtering and action contracts."""

from __future__ import annotations

import os
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QSize
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QListWidget, QPlainTextEdit, QPushButton, QWidget

from tests import _data_root  # noqa: F401
from ui.special_issue_dialog import SpecialIssueDialog


def _issue(
    issue_id: str,
    title: str,
    *,
    publisher: str = "Elsevier",
    fee: str = "hybrid",
    jcr: str = "Q1",
    cas: str = "1",
    status: str = "unread",
    verified: str = "official_verified",
    score: int = 85,
) -> dict:
    return {
        "id": issue_id,
        "title": title,
        "type": "special_issue",
        "journal": f"Journal {title}",
        "publisher": publisher,
        "issns": ["1234-5678"],
        "official_url": f"https://example.org/{issue_id}",
        "discovery_urls": [f"https://aggregator.example/{issue_id}"],
        "source_evidence": [{"source": "official", "url": f"https://example.org/{issue_id}", "fetched_at": "2026-08-31"}],
        "scope_text": f"Complete aims and scope for {title}: soil organic carbon mapping and remote sensing.",
        "scope_text_zh": f"{title} 的完整征稿范围：土壤有机碳制图与遥感。",
        "deadline": "2027-06-30",
        "publisher_value": publisher,
        "fee_mode": fee,
        "jcr_quartile": jcr,
        "cas_quartile": cas,
        "jcr": {"quartile": jcr} if jcr != "unknown" else {},
        "cas": {"quartile": cas} if cas != "unknown" else {},
        "status": status,
        "verification_status": verified,
        "official_checked_at": "2026-08-31T12:00:00",
        "call_status": "open",
        "scope_is_complete": True,
        "match": {
            "score": score,
            "formal": score >= 60,
            "rank_score": score,
            "relation": "core",
            "reason": "研究对象、遥感方法和土壤碳制图目标高度一致。",
            "paper_matches": [
                {"paper_id": "p1", "score": 91, "rank_score": 91, "formal": True, "reason": "论文主题直接匹配", "relation": "core"},
                {"paper_id": "p2", "score": 15, "rank_score": 15, "formal": False, "reason": "与该论文不匹配", "relation": "unrelated"},
            ],
            "matched_papers": [{"paper_id": "p1", "score": 91, "rank_score": 91, "formal": True, "reason": "论文主题直接匹配", "relation": "core"}],
        },
        "linked_paper_ids": ["p1"] if status == "saved" else [],
    }


class SpecialIssueDialogUiTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.items = [
            _issue("known", "Known match", score=92),
            _issue("unknown", "Unknown metadata", publisher="unknown", fee="unknown", jcr="unknown", cas="unknown", score=88),
            _issue("mismatch", "Publisher mismatch", publisher="Springer Nature", score=90),
            _issue("saved", "Saved call", status="saved", score=80),
            _issue("unverified", "Aggregator call", verified="aggregator_unverified", score=76),
        ]
        self.papers = [
            {"id": "p1", "title": "Soil carbon mapping", "summary": "", "keywords": [], "journals": []},
            {"id": "p2", "title": "Heavy metal mapping", "summary": "", "keywords": [], "journals": []},
        ]
        self.store = {"items": self.items, "reminder_log": [], "notification_log": []}
        from datetime import datetime

        self.dialog = SpecialIssueDialog(
            self.store,
            self.items,
            self.papers,
            selected_issue_id="known",
            now_provider=lambda: datetime(2026, 8, 31, 12),
        )
        self.dialog.show()
        self.application.processEvents()

    def tearDown(self) -> None:
        self.dialog.close()

    def test_fixed_workbench_and_profile_scope_fit(self) -> None:
        self.assertEqual(self.dialog.size(), QSize(1024, 768))
        scope = self.dialog.findChild(QComboBox, "specialProfileScope")
        self.assertEqual([scope.itemData(index) for index in range(scope.count())], ["global", "p1", "p2"])
        result_list = self.dialog.findChild(QListWidget, "specialResultList")
        self.assertGreater(result_list.width(), 300)
        self.assertGreater(self.dialog.findChild(QWidget, "specialDetailPane").width(), 450)

    def test_all_result_tabs_remain_visible_without_scroll_arrows(self) -> None:
        tabs = self.dialog.view_tabs
        self.assertFalse(tabs.usesScrollButtons())
        self.assertEqual(tabs.count(), 5)
        for index in range(tabs.count()):
            rect = tabs.tabRect(index)
            self.assertGreaterEqual(rect.width(), 48)
            self.assertLessEqual(rect.right(), tabs.width())

    def test_known_filter_match_stays_first_and_unknown_is_retained_last(self) -> None:
        publisher = self.dialog.findChild(QComboBox, "specialPublisherFilter")
        index = publisher.findData("Elsevier")
        self.assertGreaterEqual(index, 0)
        publisher.setCurrentIndex(index)
        self.application.processEvents()

        self.assertIn("known", self.dialog.visible_issue_ids)
        self.assertIn("unknown", self.dialog.visible_issue_ids)
        self.assertNotIn("mismatch", self.dialog.visible_issue_ids)
        self.assertLess(self.dialog.visible_issue_ids.index("known"), self.dialog.visible_issue_ids.index("unknown"))
        unknown_row = self.dialog.row_for_issue("unknown")
        self.assertIn("信息待补", unknown_row.summary_text())

    def test_selection_drives_detail_and_expanded_evidence(self) -> None:
        self.dialog.select_issue("known")
        self.assertEqual(self.dialog.selected_issue_id(), "known")
        self.assertIn("Known match", self.dialog.detail_title.text())
        self.assertIn("研究对象", self.dialog.detail_reason.text())
        self.dialog.evidence_toggle.setChecked(True)
        self.application.processEvents()
        evidence = self.dialog.findChild(QWidget, "specialVerificationEvidence")
        self.assertTrue(evidence.isVisible())
        self.assertIn("1234-5678", self.dialog.evidence_browser.toPlainText())
        self.assertIn("2026-08-31", self.dialog.evidence_browser.toPlainText())

    def test_paper_view_uses_that_papers_independent_score(self) -> None:
        self.dialog.profile_scope.setCurrentIndex(self.dialog.profile_scope.findData("p2"))
        self.application.processEvents()
        self.assertNotIn("known", self.dialog.visible_issue_ids)

    def test_left_rows_show_status_and_journal_and_detail_is_bilingual(self) -> None:
        known = self.dialog.row_for_issue("known")
        self.assertIn("推荐", known.summary_text())
        self.assertIn("Journal Known match", known.summary_text())
        self.dialog.select_issue("known")
        scope = self.dialog.scope_view.toPlainText()
        self.assertTrue(scope.startswith("中文翻译\n"))
        self.assertIn("土壤有机碳", scope)
        self.assertIn("英文原文", scope)
        self.assertIn("Complete aims", scope)

        self.dialog.view_tabs.setCurrentIndex(1)
        self.application.processEvents()
        saved = self.dialog.row_for_issue("saved")
        self.assertIn("已收藏", saved.summary_text())

        self.dialog.view_tabs.setCurrentIndex(2)
        self.application.processEvents()
        unverified = self.dialog.row_for_issue("unverified")
        self.assertIn("待核验", unverified.summary_text())

    def test_long_rows_fit_left_viewport_and_keep_state_and_journal_visible(self) -> None:
        long_issue = _issue(
            "long-row",
            "Advancements in Geospatial Techniques for Land Change Analysis and Environmental Monitoring Across Complex Landscapes",
            verified="aggregator_unverified",
            score=91,
        )
        long_issue["journal"] = "International Journal of Applied Earth Observation and Geoinformation"
        self.dialog.reload_data({"items": [long_issue]}, [long_issue], self.papers)
        self.dialog.view_tabs.setCurrentIndex(2)
        self.application.processEvents()

        row = self.dialog.row_for_issue("long-row")
        self.assertLessEqual(row.width(), self.dialog.result_list.viewport().width() + 1)
        item_rect = self.dialog.result_list.visualItemRect(self.dialog.result_list.currentItem())
        self.assertGreaterEqual(item_rect.height(), row.minimumHeight())
        state = row.findChild(QLabel, "specialIssueResultState")
        journal = row.findChild(QLabel, "specialIssueResultJournal")
        self.assertIsNotNone(state)
        self.assertIsNotNone(journal)
        self.assertEqual(state.text(), "待核验")
        self.assertEqual(journal.toolTip(), long_issue["journal"])
        self.assertTrue(journal.text().endswith("…"))
        for child in row.findChildren(QLabel):
            self.assertLessEqual(child.geometry().right(), row.rect().right(), child.objectName())

    def test_action_signals_keep_dialog_open_and_selection_stable(self) -> None:
        calls: list[tuple] = []
        self.dialog.associate_requested.connect(lambda issue_id, paper_ids: calls.append(("associate", issue_id, paper_ids)))
        self.dialog.path_requested.connect(lambda issue_id, paper_id: calls.append(("path", issue_id, paper_id)))
        self.dialog.task_requested.connect(lambda issue_id, paper_id: calls.append(("task", issue_id, paper_id)))
        self.dialog.status_requested.connect(lambda issue_id, status: calls.append(("status", issue_id, status)))
        self.dialog.select_issue("known")
        self.dialog.paper_selector.setCurrentIndex(self.dialog.paper_selector.findData("p1"))
        self.dialog.findChild(QPushButton, "specialAssociateButton").click()
        self.dialog.findChild(QPushButton, "specialPathButton").click()
        self.dialog.findChild(QPushButton, "specialTaskButton").click()
        self.dialog.findChild(QPushButton, "specialReadButton").click()
        self.assertEqual(calls, [("associate", "known", ["p1"]), ("path", "known", "p1"), ("task", "known", "p1"), ("status", "known", "read")])
        self.assertTrue(self.dialog.isVisible())
        self.dialog.reload_data(self.store, self.items, self.papers)
        self.assertEqual(self.dialog.selected_issue_id(), "known")
        self.assertTrue(self.dialog.isVisible())

    def test_personal_scope_note_and_issue_actions_are_separate_from_paper_actions(self) -> None:
        self.dialog.select_issue("known")
        note = self.dialog.findChild(QPlainTextEdit, "specialPersonalScopeNote")
        note.setPlainText("我认为该征稿更适合土壤碳制图方法论文。")
        calls: list[tuple[str, str]] = []
        self.dialog.scope_note_requested.connect(lambda issue_id, text: calls.append((issue_id, text)))
        self.dialog.findChild(QPushButton, "specialSaveScopeNoteButton").click()

        self.assertEqual(calls, [("known", "我认为该征稿更适合土壤碳制图方法论文。")])
        issue_actions = self.dialog.findChild(QWidget, "specialIssueActions")
        paper_actions = self.dialog.findChild(QWidget, "specialPaperActions")
        self.assertIs(self.dialog.findChild(QPushButton, "specialSaveButton").parentWidget(), issue_actions)
        self.assertIs(self.dialog.findChild(QPushButton, "specialIgnoreButton").parentWidget(), issue_actions)
        self.assertIs(self.dialog.findChild(QComboBox, "specialPaperSelector").parentWidget(), paper_actions)

    def test_saved_and_ignored_states_have_explicit_reverse_actions(self) -> None:
        calls: list[tuple[str, str]] = []
        self.dialog.status_requested.connect(lambda issue_id, status: calls.append((issue_id, status)))
        self.dialog.view_tabs.setCurrentIndex(1)
        self.dialog.select_issue("saved")
        self.dialog.findChild(QPushButton, "specialSaveButton").click()
        self.assertEqual(calls[-1], ("saved", "unsaved"))

        ignored = _issue("ignored", "Ignored call", status="ignored")
        ignored["ignored"] = True
        self.dialog.reload_data({"items": [*self.items, ignored]}, [*self.items, ignored], self.papers)
        ignored_index = next(index for index in range(self.dialog.view_tabs.count()) if self.dialog.view_tabs.tabData(index) == "ignored")
        self.dialog.view_tabs.setCurrentIndex(ignored_index)
        self.dialog.select_issue("ignored")
        self.dialog.findChild(QPushButton, "specialIgnoreButton").click()
        self.assertEqual(calls[-1], ("ignored", "restored"))

    def test_closed_issue_keeps_library_and_association_but_disables_new_deadline_actions(self) -> None:
        closed = _issue("closed", "Closed call", verified="closed")
        closed["call_status"] = "closed"
        self.dialog.reload_data({"items": [closed]}, [closed], self.papers)
        changed_index = next(index for index in range(self.dialog.view_tabs.count()) if self.dialog.view_tabs.tabData(index) == "unverified")
        self.dialog.view_tabs.setCurrentIndex(changed_index)
        self.dialog.select_issue("closed")
        self.assertTrue(self.dialog.findChild(QPushButton, "specialLibraryButton").isEnabled())
        self.assertTrue(self.dialog.findChild(QPushButton, "specialAssociateButton").isEnabled())
        self.assertFalse(self.dialog.findChild(QPushButton, "specialPathButton").isEnabled())
        self.assertFalse(self.dialog.findChild(QPushButton, "specialTaskButton").isEnabled())

    def test_visible_controls_do_not_leave_1024x768_window(self) -> None:
        bounds = self.dialog.rect()
        for widget in self.dialog.findChildren(QWidget):
            if not widget.isVisible() or widget.window() is not self.dialog:
                continue
            top_left = widget.mapTo(self.dialog, QPoint(0, 0))
            bottom_right = widget.mapTo(self.dialog, QPoint(widget.width() - 1, widget.height() - 1))
            self.assertGreaterEqual(top_left.x(), bounds.left() - 1, widget.objectName())
            self.assertGreaterEqual(top_left.y(), bounds.top() - 1, widget.objectName())
            self.assertLessEqual(bottom_right.x(), bounds.right() + 1, widget.objectName())
            self.assertLessEqual(bottom_right.y(), bounds.bottom() + 1, widget.objectName())
        for object_name in ("specialAssociateButton", "specialPathButton", "specialTaskButton"):
            self.assertTrue(self.dialog.findChild(QPushButton, object_name).isVisible())

    def test_refresh_progress_can_be_cancelled_without_closing_workbench(self) -> None:
        calls: list[str] = []
        self.dialog.refresh_requested.connect(lambda: calls.append("refresh"))
        self.dialog.cancel_refresh_requested.connect(lambda: calls.append("cancel"))
        cancel = self.dialog.findChild(QPushButton, "specialCancelRefreshButton")
        self.assertFalse(cancel.isVisible())
        self.dialog.findChild(QPushButton, "specialRefreshButton").click()
        self.application.processEvents()
        self.assertTrue(cancel.isVisible())
        cancel.click()
        self.dialog.finish_progress("已取消")
        self.assertEqual(calls, ["refresh", "cancel"])
        self.assertFalse(cancel.isVisible())
        self.assertTrue(self.dialog.isVisible())
