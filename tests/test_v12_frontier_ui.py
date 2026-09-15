"""Compact v12 Daily Frontier UI contracts."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("RESEARCH_ASSISTANT_REDUCE_MOTION", "1")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from tests import _data_root  # noqa: F401 - isolate persistence before UI imports
from ui.frontier_page import DailyFrontierPage, FrontierCard

_APPLICATION = QApplication.instance() or QApplication([])


def _app() -> QApplication:
    return _APPLICATION


def _show(widget: QWidget, width: int = 400, height: int = 480) -> QWidget:
    app = _app()
    host = QWidget()
    host.setFixedSize(width, height)
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(widget)
    host.show()
    app.processEvents()
    return host


def _fixture_items() -> list[dict]:
    rows = [
        {
            "id": "journal",
            "title": "A very long journal paper title about soil organic carbon mapping and remote sensing that must wrap safely",
            "journal": "CATENA",
            "published_date": "2026-08-30",
            "score": 91,
            "content_score": 94,
            "journal_score": 80,
            "status": "new",
            "jcr_status": "verified",
            "jcr_state": "verified",
            "jcr_quartile": "Q1",
            "cas_upgrade": "2区",
            "quality_gate_state": "eligible",
            "quality_gate_reason": "verified_q1_q2",
            "recommendation_kind": "core_keyword",
            "recommendation_reason": "与核心关键词 soil organic carbon 直接相关",
            "matched_terms": ["soil organic carbon"],
        },
        {
            "id": "pending",
            "title": "Unknown quality paper",
            "journal": "Unknown Journal",
            "score": 99,
            "status": "new",
            "quality_gate_state": "withheld",
            "quality_gate_reason": "quality_pending",
        },
        {
            "id": "preprint",
            "title": "A geospatial preprint",
            "journal": "arXiv",
            "score": 81,
            "status": "new",
            "is_preprint": True,
            "quality_gate_state": "preprint",
            "recommendation_kind": "profile_exploration",
            "recommendation_reason": "根据长期研究画像拓展发现",
        },
    ]
    for row in rows:
        row["content_decision"] = "accept"
        row["admission_version"] = "test"
        row["ai_score"] = row["score"]
    return rows


def test_page_has_journal_and_preprint_tabs_and_withholds_pending_quality() -> None:
    page = DailyFrontierPage()
    page.data = {
        "profile": {"daily_limit": 5},
        "items": _fixture_items(),
        "last_checked": "2026-08-31",
        "algorithm_version": 12,
    }
    page._render()
    host = _show(page)

    journal_tab = page.findChild(QPushButton, "frontierJournalTab")
    preprint_tab = page.findChild(QPushButton, "frontierPreprintTab")
    pending = page.findChild(QPushButton, "frontierPendingQualityButton")
    titles = [label.text() for label in page.content.findChildren(QLabel, "frontierTitle")]

    assert journal_tab is not None and journal_tab.isChecked()
    assert preprint_tab is not None
    assert pending is not None and "1" in pending.text()
    assert any("soil organic carbon" in title for title in titles)
    assert all("Unknown quality" not in title for title in titles)
    assert all("preprint" not in title.casefold() for title in titles)
    host.close()


def test_preprint_tab_switches_to_a_separate_stream() -> None:
    page = DailyFrontierPage()
    page.data = {"profile": {"daily_limit": 5}, "items": _fixture_items(), "algorithm_version": 12}
    page._render()
    host = _show(page)

    page.frontier_preprint_tab.click()
    _app().processEvents()
    titles = [label.text() for label in page.content.findChildren(QLabel, "frontierTitle")]

    assert titles == ["A geospatial preprint"]
    assert page.content.findChild(QLabel, "frontierPreprintBadge") is not None
    assert page.content.findChild(QLabel, "frontierJcrUnknown") is None
    assert page.content.findChild(QLabel, "frontierCasBadge") is None
    host.close()


def test_long_title_does_not_overlap_the_stable_action_strip_at_400_pixels() -> None:
    card = FrontierCard(_fixture_items()[0])
    host = _show(card, 380, 290)
    title = card.findChild(QLabel, "frontierTitle")
    actions = card.findChild(QWidget, "frontierActionStrip")

    assert title is not None and actions is not None
    assert title.geometry().bottom() < actions.geometry().top()
    assert actions.geometry().right() <= card.contentsRect().right()
    assert card.minimumSizeHint().width() <= 380
    host.close()


def test_widget_card_keeps_score_jcr_and_cas_readable_at_480_pixels() -> None:
    card = FrontierCard(_fixture_items()[0])
    host = _show(card, 480, 300)

    for object_name, expected in (
        ("frontierScore", "综合 91"),
        ("frontierContentScore", "内容 94"),
        ("frontierJournalScore", "期刊 80"),
        ("frontierJcrVerified", "Q1"),
        ("frontierCasBadge", "中科院 2区"),
    ):
        label = card.findChild(QLabel, object_name)
        assert label is not None and label.isVisible()
        assert label.text() == expected
        assert label.contentsRect().width() >= label.fontMetrics().horizontalAdvance(expected)
    host.close()


def test_comment_and_refresh_progress_surfaces_are_present() -> None:
    page = DailyFrontierPage()
    page.data = {"profile": {"daily_limit": 5}, "items": [_fixture_items()[0]], "algorithm_version": 12}
    page._render()
    host = _show(page)

    assert page.findChild(QWidget, "frontierRefreshProgress") is not None
    card = page.content.findChild(FrontierCard)
    assert card is not None
    comment = card.findChild(QPushButton, "frontierCommentSubmit")
    assert comment is not None
    assert card.findChild(QWidget, "frontierRecommendationRow") is not None
    assert card.findChild(QLabel, "frontierReason") is not None
    host.close()


def test_title_is_selectable_for_copying() -> None:
    card = FrontierCard(_fixture_items()[0])
    title = card.findChild(QLabel, "frontierTitle")

    assert title.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    card.close()


def test_completed_one_line_comment_updates_profile_and_keeps_the_page_open(monkeypatch) -> None:
    page = DailyFrontierPage()
    page.data = {
        "profile": {"terms": [{"canonical_en": "soil carbon", "locked": True}]},
        "items": [_fixture_items()[0]],
        "algorithm_version": 12,
    }
    monkeypatch.setattr(page, "_persist_data", lambda: None)
    host = _show(page)

    page._comment_ai_finished(
        "journal",
        "微生物不是我的研究方向",
        {
            "intent": "negative",
            "active_terms": [],
            "excluded_terms": [
                {"canonical_en": "soil microorganisms", "translation_zh": "土壤微生物"}
            ],
            "pending_terms": [],
            "reason": "明确排除",
            "confidence": "high",
            "item_id": "journal",
        },
    )

    assert page.data["profile"]["excluded_terms"] == ["soil microorganisms"]
    assert page.data["profile"]["terms"][0]["locked"] is True
    assert page.data["items"][0]["feedback_events"][-1]["action"] == "one_line_feedback"
    assert page.isVisible()
    host.close()
