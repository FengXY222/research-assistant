"""Regression contracts for the Daily Frontier stream switch and journal score."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QSpinBox

from ui.frontier_page import DailyFrontierPage, FrontierCard
from ui import frontier_settings_dialog
from ui.frontier_settings_dialog import FrontierSettingsDialog, JournalPreferenceScoreDialog
from ui.theme import build_application_stylesheet
from utils.frontier_scoring import apply_frontier_ranking, journal_preference_score


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_explicit_journal_score_contributes_to_composite_ranking() -> None:
    ranked = apply_frontier_ranking(
        [{"id": "p1", "journal": "CATENA", "ai_score": 92, "ranking_weight": 1.0}],
        {"frontier_ranking": {"journal_weight": 25, "default_journal_score": 50}},
        [{"id": "j1", "name": "CATENA", "frontier_score": 80}],
    )[0]

    assert ranked["content_score"] == 92
    assert ranked["journal_score"] == 80
    assert ranked["score"] == 89
    assert ranked["ranking_breakdown"]["journal_weight"] == 25


def test_exploration_weight_is_applied_after_composite_score() -> None:
    ranked = apply_frontier_ranking(
        [{"id": "p1", "journal": "CATENA", "ai_score": 100, "ranking_weight": 0.65}],
        {"frontier_ranking": {"journal_weight": 25, "default_journal_score": 50}},
        [{"id": "j1", "name": "CATENA", "frontier_score": 80}],
    )[0]

    assert ranked["score"] == 62


def test_preprints_do_not_receive_a_synthetic_journal_score() -> None:
    ranked = apply_frontier_ranking(
        [{"id": "p1", "journal": "arXiv", "is_preprint": True, "ai_score": 88, "ranking_weight": 1.0}],
        {"frontier_ranking": {"journal_weight": 50, "default_journal_score": 20}},
        [],
    )[0]

    assert ranked["journal_score"] is None
    assert ranked["score"] == 88


def test_legacy_priority_maps_to_a_score_without_rewriting_the_record() -> None:
    journal = {"name": "Legacy Journal", "frontier_priority": "关注"}

    assert journal_preference_score(journal, default_score=50) == 80
    assert "frontier_score" not in journal


def test_unknown_journal_uses_the_profile_default_score() -> None:
    ranked = apply_frontier_ranking(
        [{"id": "p1", "journal": "Outside Library", "ai_score": 80}],
        {"frontier_ranking": {"journal_weight": 25, "default_journal_score": 40}},
        [],
    )[0]

    assert ranked["journal_score"] == 40
    assert ranked["score"] == 70


def test_pending_stream_is_batched_and_main_stream_switch_restores_regular_filter() -> None:
    _app()
    page = DailyFrontierPage()
    pending = [
        {
            "id": f"pending-{index}",
            "title": f"Pending {index}",
            "content_decision": "pending",
            "status": "new",
            "score": index,
        }
        for index in range(80)
    ]
    accepted = {
        "id": "accepted",
        "title": "Accepted",
        "content_decision": "accept",
        "quality_gate_state": "eligible",
        "status": "new",
        "score": 90,
    }
    page.data = {"profile": {"daily_limit": 5}, "items": [accepted, *pending]}

    page.filter_combo.setCurrentText("待内容复核")
    assert len(page.findChildren(FrontierCard)) == 24

    page._switch_stream("journal")
    assert page.filter_combo.currentText() == "今日推荐"
    assert [item["id"] for item in page._visible_items()] == ["accepted"]
    assert len(page.findChildren(FrontierCard)) == 1
    page.close()


def test_settings_exposes_one_weight_control_and_persists_values() -> None:
    _app()
    dialog = FrontierSettingsDialog(
        {
            "terms": [],
            "frontier_ranking": {"journal_weight": 35, "default_journal_score": 45},
        }
    )

    assert dialog.journal_weight_spin.value() == 35
    assert dialog.default_journal_score_spin.value() == 45
    assert [dialog.settings_tabs.tabText(index) for index in range(dialog.settings_tabs.count())] == [
        "关键词工作台",
        "发现策略",
        "期刊排序",
    ]
    result = dialog.profile()["frontier_ranking"]
    assert result == {"journal_weight": 35, "default_journal_score": 45}
    dialog.close()


def test_journal_priority_editor_applies_one_category_to_all_visible_selected_rows() -> None:
    _app()
    assert hasattr(frontier_settings_dialog, "JournalPriorityBatchEditor")
    editor = frontier_settings_dialog.JournalPriorityBatchEditor(
        [
            {"id": "catena", "name": "CATENA", "publisher": "Elsevier", "frontier_priority": "必看", "frontier_score": 95},
            {"id": "geoderma", "name": "Geoderma", "publisher": "Elsevier", "frontier_priority": "不订阅"},
            {"id": "soil-biology", "name": "Soil Biology", "publisher": "Springer Nature", "frontier_priority": "扩展"},
        ]
    )
    editor.search_edit.setText("Elsevier")
    editor.select_visible_button.click()
    editor.subscribe_button.click()

    updated = {journal["id"]: journal for journal in editor.journals_with_priorities()}
    assert updated["catena"]["frontier_priority"] == "关注"
    assert updated["catena"]["frontier_score"] == 80
    assert updated["geoderma"]["frontier_priority"] == "关注"
    assert updated["geoderma"]["frontier_score"] == 80
    assert updated["soil-biology"]["frontier_priority"] == "扩展"
    editor.close()


def test_journal_score_table_supports_search_and_editing() -> None:
    _app()
    dialog = JournalPreferenceScoreDialog(
        [
            {"id": "catena", "name": "CATENA", "frontier_score": 90, "jcr_quartile": "Q1"},
            {"id": "geoderma", "name": "Geoderma", "frontier_priority": "关注", "jcr_quartile": "Q2"},
        ]
    )

    assert dialog.table.rowCount() == 2
    dialog.search_edit.setText("catena")
    assert dialog.table.isRowHidden(0) is False
    assert dialog.table.isRowHidden(1) is True
    score = dialog.table.cellWidget(0, 3)
    assert isinstance(score, QSpinBox)
    score.setValue(95)
    updated = dialog.journals_with_scores()
    assert next(item for item in updated if item["id"] == "catena")["frontier_score"] == 95
    dialog.close()


def test_journal_score_table_header_uses_theme_contrast_colors() -> None:
    stylesheet = build_application_stylesheet("fog_teal", "comfortable")
    rule = stylesheet.split("#journalScoreTable QHeaderView::section", 1)[1].split("}", 1)[0]
    assert "background: palette(alternate-base)" in rule
    assert "color: palette(text)" in rule


def test_batch_priority_table_uses_the_same_readable_theme_rules() -> None:
    stylesheet = build_application_stylesheet("fog_teal", "comfortable")
    assert "#journalPriorityTable" in stylesheet
    assert "#journalPriorityTable QHeaderView::section" in stylesheet
