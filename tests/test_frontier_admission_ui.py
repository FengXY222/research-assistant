"""Headless widget contracts; no computer-use or formal data access."""

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests import _data_root  # noqa: F401
from PySide6.QtWidgets import QApplication
from ui.frontier_page import DailyFrontierPage

APP = QApplication.instance() or QApplication([])


def row(key, **extra):
    return {"id": key, "title": key, "status": "new", "score": 80,
            "quality_gate_state": "eligible", "recommendation_kind": "core_keyword", **extra}


def test_main_feed_cannot_show_rejected_pending_or_unreviewed_legacy():
    page = DailyFrontierPage()
    page.data = {"profile": {"daily_limit": 10}, "items": [
        row("good", content_decision="accept", admission_version="test"),
        row("bad", content_decision="reject", admission_version="test"),
        row("pending", content_decision="pending", admission_version="test"), row("legacy"),
        row("old-read", status="read"),
    ]}
    assert [r["id"] for r in page._visible_items()] == ["good"]
    page.filter_combo.setCurrentText("已读")
    assert [r["id"] for r in page._visible_items()] == ["old-read"]
    page.filter_combo.setCurrentText("待内容复核")
    assert {r["id"] for r in page._visible_items()} == {"pending", "legacy"}
    page.close()


def test_background_result_cannot_overwrite_new_read_action_or_profile(monkeypatch):
    page = DailyFrontierPage()
    page.data = {"profile": {"daily_limit": 7}, "items": [row("p", status="read")]}
    monkeypatch.setattr(page, "_save", lambda *a: None)
    fresh = row("p", content_decision="accept", admission_version="test", score=93)
    page._refresh_finished({"data": {"profile": {"daily_limit": 5}, "items": [fresh]}, "visible_count": 0})
    assert page.data["items"][0]["status"] == "read"
    assert page.data["items"][0]["score"] == 93
    assert page.data["profile"]["daily_limit"] == 7
    page.close()


def test_background_result_retains_a_newly_saved_record_outside_returned_batch(monkeypatch):
    page = DailyFrontierPage()
    page.data = {"profile": {}, "items": [row("saved-during-refresh", status="saved")]}
    monkeypatch.setattr(page, "_save", lambda *a: None)
    page._refresh_finished({"data": {"profile": {}, "items": []}, "visible_count": 0})
    assert page.data["items"][0]["id"] == "saved-during-refresh"
    assert page.data["items"][0]["status"] == "saved"
    page.close()
