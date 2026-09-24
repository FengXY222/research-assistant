"""Render v12 UI evidence and audit geometry without desktop automation."""

from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("RESEARCH_ASSISTANT_REDUCE_MOTION", "1")
os.environ.setdefault("RESEARCH_ASSISTANT_DISABLE_BACKGROUND", "1")
_TEMP_DATA = tempfile.TemporaryDirectory(prefix="research-assistant-v12-render-")
os.environ["RESEARCH_ASSISTANT_DATA_DIR"] = _TEMP_DATA.name

from PySide6.QtCore import QRect
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QComboBox,
    QLabel,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from ui.frontier_settings_dialog import FrontierSettingsDialog
from ui.frontier_page import DailyFrontierPage
from ui.journal_selection_dialog import JournalSelectionDialog
from ui.main_window import MainWindow
from ui.special_issue_dialog import SpecialIssueDialog
from ui.special_issue_page import SpecialIssuePage
from ui.theme import THEME_REGISTRY, apply_application_theme, ensure_application_font
from utils.special_issue_repository import save_special_issue_store


PROFILE_FIXTURE = {
    "terms": [
        {
            "canonical_en": "soil organic carbon mapping with multisource remote sensing",
            "translation_zh": "多源遥感土壤有机碳制图",
            "weight": 94,
            "locked": True,
            "source": "paper",
            "evidence": ["已有论文与手动锁定"],
        },
        {
            "canonical_en": "digital soil mapping",
            "translation_zh": "数字土壤制图",
            "weight": 82,
            "source": "user_confirmed",
        },
        {
            "canonical_en": "mineral-associated organic carbon",
            "translation_zh": "矿物结合态有机碳",
            "weight": 71,
            "source": "ai",
        },
    ],
    "pending_terms": [
        {
            "canonical_en": "spectral transfer learning",
            "translation_zh": "光谱迁移学习",
            "weight": 62,
            "source": "ai_pdf",
        }
    ],
    "excluded_entries": [
        {
            "canonical_en": "soil microbial community",
            "translation_zh": "土壤微生物群落",
            "source": "user",
        }
    ],
}

FRONTIER_FIXTURE = {
    "profile": {**PROFILE_FIXTURE, "daily_limit": 5},
    "last_checked": "2026-08-31",
    "items": [
        {
            "id": "frontier-journal-1",
            "title": "Mapping mineral-associated organic carbon using multisource remote sensing and machine learning",
            "journal": "Geoderma",
            "published_date": "2026-08-30",
            "score": 94,
            "jcr_status": "verified",
            "jcr_quartile": "Q1",
            "cas_upgrade": "1区",
            "matched_terms": ["soil organic carbon", "digital soil mapping", "remote sensing"],
            "recommendation_kind": "core_keyword",
            "recommendation_reason": "同时命中长期画像中的土壤有机碳与数字土壤制图，并与近期遥感方法兴趣相符。",
            "quality_gate_state": "journal",
            "source_names": ["OpenAlex", "Semantic Scholar"],
            "url": "https://example.org/paper/1",
        },
        {
            "id": "frontier-journal-2",
            "title": "Transfer learning improves spatial prediction of soil carbon fractions across regions",
            "journal": "Science of the Total Environment",
            "published_date": "2026-08-29",
            "score": 86,
            "jcr_status": "verified",
            "jcr_quartile": "Q1",
            "cas_upgrade": "2区",
            "matched_terms": ["soil organic carbon", "spectral transfer learning"],
            "recommendation_kind": "profile_exploration",
            "recommendation_reason": "核心关键词结果不足时，由综合研究画像补足的相邻方向。",
            "quality_gate_state": "journal",
            "source_names": ["Crossref"],
            "url": "https://example.org/paper/2",
        },
        {
            "id": "frontier-preprint-1",
            "title": "Foundation models for global digital soil mapping",
            "journal": "EarthArXiv",
            "published_date": "2026-08-31",
            "score": 91,
            "matched_terms": ["digital soil mapping", "remote sensing"],
            "recommendation_kind": "core_keyword",
            "recommendation_reason": "预印本单独展示，不冒充经过分区核验的期刊论文。",
            "quality_gate_state": "preprint",
            "is_preprint": True,
            "source_names": ["OpenAlex"],
            "url": "https://example.org/preprint/1",
        },
        {
            "id": "frontier-pending-1",
            "title": "Unverified journal record held outside the main feed",
            "journal": "Unknown Journal",
            "score": 79,
            "quality_gate_state": "withheld",
        },
    ],
}

SELECTION_PAPER = {
    "id": "selection-paper",
    "title": "Multisource remote sensing for soil organic carbon mapping",
    "summary": "This study combines satellite imagery, terrain attributes and machine learning to map soil organic carbon at regional scale. " * 8,
    "keywords": ["soil organic carbon", "remote sensing", "digital soil mapping"],
    "journals": [],
}

SPECIAL_ISSUE_ITEMS = [
    {
        "id": "special-saved",
        "title": "Multisource soil carbon mapping and monitoring",
        "type": "special_issue",
        "journal": "Geoderma",
        "publisher": "Elsevier",
        "issns": ["0016-7061"],
        "official_url": "https://example.org/special/saved",
        "discovery_urls": ["https://example.org/discovery/saved"],
        "source_evidence": [{"source": "Elsevier", "fetched_at": "2026-08-31T10:00:00"}],
        "scope_text": "This special issue welcomes full research articles on multisource remote sensing, digital soil mapping and soil organic carbon monitoring across spatial scales.",
        "deadline": "2027-01-15",
        "fee_mode": "hybrid",
        "jcr_quartile": "Q1",
        "cas_quartile": "1",
        "status": "saved",
        "verification_status": "official_verified",
        "official_checked_at": "2026-08-31T12:00:00",
        "linked_paper_ids": ["selection-paper"],
        "match": {"score": 92, "formal": True, "reason": "与现有土壤有机碳制图论文及锁定关键词高度一致。", "matched_terms": ["soil organic carbon", "remote sensing"], "matched_papers": [{"paper_id": "selection-paper", "score": 94, "reason": "研究对象与制图方法直接匹配"}]},
    },
    {
        "id": "special-recommended",
        "title": "AI and Earth observation for sustainable soil management",
        "type": "research_topic",
        "journal": "Remote Sensing of Environment",
        "publisher": "Elsevier",
        "issns": ["0034-4257"],
        "official_url": "https://example.org/special/recommended",
        "discovery_urls": ["https://example.org/discovery/recommended"],
        "source_evidence": [{"source": "Crossref", "fetched_at": "2026-08-31T11:00:00"}],
        "scope_text": "The collection seeks advances in artificial intelligence, Earth observation and spatial prediction for sustainable soil management, including uncertainty-aware digital mapping.",
        "deadline": "2027-03-31",
        "fee_mode": "subscription",
        "jcr_quartile": "Q1",
        "cas_quartile": "1",
        "status": "unread",
        "verification_status": "official_verified",
        "official_checked_at": "2026-08-31T12:00:00",
        "match": {"score": 88, "formal": True, "reason": "遥感、机器学习和数字土壤制图均命中长期画像。", "matched_terms": ["digital soil mapping"], "matched_papers": [{"paper_id": "selection-paper", "score": 87, "reason": "方法路线相近"}]},
    },
    {
        "id": "special-unverified",
        "title": "Spatial modelling of soil carbon fractions",
        "type": "article_collection",
        "journal": "Unknown Journal",
        "publisher": "unknown",
        "issns": [],
        "official_url": "https://example.org/special/unverified",
        "discovery_urls": ["https://example.org/aggregator/unverified"],
        "source_evidence": [{"source": "Aggregator", "fetched_at": "2026-08-31T11:30:00"}],
        "scope_text": "Full call scope covering spatial modelling and soil carbon fractions.",
        "deadline": "2027-05-20",
        "fee_mode": "unknown",
        "status": "unread",
        "verification_status": "aggregator_unverified",
        "official_checked_at": "2026-08-31T12:00:00",
        "match": {"score": 80, "formal": True, "reason": "研究对象匹配，但期刊身份与分区仍待补。", "matched_terms": ["soil carbon fractions"], "matched_papers": []},
    },
    {
        "id": "special-changed",
        "title": "Deadline-updated topical collection",
        "type": "topical_collection",
        "journal": "CATENA",
        "publisher": "Elsevier",
        "issns": ["0341-8162"],
        "official_url": "https://example.org/special/changed",
        "scope_text": "Erosion, landscape processes and spatial prediction in soil systems.",
        "deadline": "2027-06-30",
        "fee_mode": "hybrid",
        "jcr_quartile": "Q1",
        "cas_quartile": "1",
        "status": "read",
        "verification_status": "official_verified",
        "deadline_history": [{"deadline": "2027-05-31"}, {"deadline": "2027-06-30"}],
        "match": {"score": 74, "formal": True, "reason": "空间预测方法相关。", "matched_terms": ["spatial prediction"], "matched_papers": []},
    },
]

SPECIAL_ISSUE_STORE = {
    "version": 1,
    "items": SPECIAL_ISSUE_ITEMS,
    "last_checked_at": "2026-08-31T12:00:00",
    "last_refresh_status": "success",
    "notification_log": [],
    "reminder_log": [],
}


def _selection_result(index: int) -> dict[str, Any]:
    name = ["Geoderma", "CATENA", "Remote Sensing of Environment"][index - 1]
    return {
        "result_id": f"selection-result-{index}",
        "journal_id": f"selection-journal-{index}",
        "journal_name": name,
        "journal": {
            "id": f"selection-journal-{index}",
            "name": name,
            "publisher": "Elsevier",
            "issns": [f"0016-70{index}0"],
            "website": f"https://example.org/journal/{index}",
            "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
            "easyscholar": {"cas_upgrade": "1区", "checked_at": "2026-08-31"},
            "aims_scope": "Publishes rigorous research on soil processes, spatial modelling and environmental remote sensing.",
        },
        "ai_total_score": 96 - index * 4,
        "reason_cn": "相似真实论文集中发表于该刊，稿件的研究对象、空间制图方法和数据来源均达到主题最低线。",
        "publisher": "Elsevier",
        "fee_mode": "hybrid",
        "estimated_speed_text": "预计较快",
        "is_external": index == 3,
        "similar_papers": [
            {"title": f"Verified similar paper {index}", "doi": f"10.1000/selection.{index}"}
        ],
        "identity_evidence": {
            "verified": True,
            "issns": [f"0016-70{index}0"],
            "official_url": f"https://example.org/journal/{index}",
            "source_names": ["Crossref", "OpenAlex"],
            "verified_at": "2026-08-31T12:00:00",
        },
    }


def _leaf_controls(root: QWidget) -> list[QWidget]:
    kinds = (QAbstractButton, QComboBox, QLineEdit, QSpinBox, QLabel)
    return [
        child
        for child in root.findChildren(QWidget)
        if isinstance(child, kinds) and child.isVisible() and not child.visibleRegion().isEmpty()
    ]


def _mapped_rect(widget: QWidget, root: QWidget) -> QRect:
    top_left = widget.mapTo(root, widget.rect().topLeft())
    return QRect(top_left, widget.size())


def _text_is_clipped(widget: QWidget) -> bool:
    if isinstance(widget, QLabel):
        text = widget.text().strip()
        if not text or widget.wordWrap():
            return False
        required = max(QFontMetrics(widget.font()).horizontalAdvance(line) for line in text.splitlines())
        return required > widget.contentsRect().width() + 2
    if isinstance(widget, QAbstractButton):
        text = widget.text().strip()
        if not text:
            return False
        return widget.sizeHint().width() > widget.width() + 2
    return False


def audit_widget(root: QWidget) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    controls = _leaf_controls(root)
    root_rect = root.rect().adjusted(-1, -1, 1, 1)
    for control in controls:
        mapped = _mapped_rect(control, root)
        if not root_rect.intersects(mapped):
            issues.append({"kind": "outside-root", "object": control.objectName(), "class": type(control).__name__})
        if _text_is_clipped(control):
            issues.append(
                {
                    "kind": "text-clipped",
                    "object": control.objectName(),
                    "class": type(control).__name__,
                    "text": control.text()[:100],
                }
            )

    by_parent: dict[int, list[QWidget]] = {}
    for control in controls:
        by_parent.setdefault(id(control.parentWidget()), []).append(control)
    for siblings in by_parent.values():
        for index, left in enumerate(siblings):
            left_rect = left.geometry()
            for right in siblings[index + 1 :]:
                intersection = left_rect.intersected(right.geometry())
                if intersection.width() > 3 and intersection.height() > 3:
                    issues.append(
                        {
                            "kind": "overlap",
                            "object": left.objectName() or type(left).__name__,
                            "other": right.objectName() or type(right).__name__,
                        }
                    )
    return {"control_count": len(controls), "issues": issues}


def _image_color_count(widget: QWidget, path: Path) -> int:
    pixmap = widget.grab()
    if pixmap.isNull() or not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"无法保存界面截图: {path}")
    image = pixmap.toImage()
    colors: set[int] = set()
    step_x = max(1, image.width() // 80)
    step_y = max(1, image.height() // 60)
    for y in range(0, image.height(), step_y):
        for x in range(0, image.width(), step_x):
            colors.add(int(image.pixel(x, y)))
    return len(colors)


def _capture(
    app: QApplication,
    widget: QWidget,
    path: Path,
    *,
    hide_after: bool = True,
) -> dict[str, Any]:
    widget.show()
    app.processEvents()
    widget.update()
    app.processEvents()
    report = audit_widget(widget)
    report["path"] = str(path)
    report["size"] = [widget.width(), widget.height()]
    report["sampled_color_count"] = _image_color_count(widget, path)
    if report["sampled_color_count"] < 8:
        report["issues"].append({"kind": "near-blank-image", "object": widget.objectName()})
    if hide_after:
        widget.hide()
    return report


def render(output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    for stale in output.glob("*.png"):
        stale.unlink()
    app = QApplication.instance() or QApplication([])
    ensure_application_font(app)
    entries: list[dict[str, Any]] = []
    save_special_issue_store(SPECIAL_ISSUE_STORE)
    widget = MainWindow()
    widget.settings["frontier_background_refresh"] = False
    workbench = FrontierSettingsDialog(PROFILE_FIXTURE)
    frontier = DailyFrontierPage()
    frontier_fixture = json.loads(json.dumps(FRONTIER_FIXTURE, ensure_ascii=False))
    frontier_fixture["algorithm_version"] = 13
    for index, item in enumerate(frontier_fixture["items"]):
        if item.get("quality_gate_state") == "withheld":
            continue
        relevance = max(62, int(item.get("score", 80) or 80) - 4)
        value = max(60, int(item.get("score", 80) or 80) - 7)
        item.update(
            content_decision="accept",
            admission_version="frontier-v13",
            candidate_state="visible",
            relevance_score=relevance,
            research_value_score=value,
            relevance_axis={"base_score": min(66, relevance), "ai_adjustment": max(0, relevance - 66)},
            value_axis={"base_score": min(66, value), "ai_adjustment": max(0, value - 66)},
            pyramid_level="A" if index == 0 else "B",
            display_bucket="today",
        )
    frontier.data = frontier_fixture
    frontier.resize(480, 720)
    frontier._render()
    selection = JournalSelectionDialog([SELECTION_PAPER], [], PROFILE_FIXTURE)
    selection.apply_ai_recommendation(
        {"results": [_selection_result(index) for index in range(1, 4)], "rounds": 3}
    )
    special_page = SpecialIssuePage()
    special_items = json.loads(json.dumps(SPECIAL_ISSUE_ITEMS, ensure_ascii=False))
    for item in special_items:
        item.update(scope_is_complete=True, call_status="open", official_checked_at=datetime.now().isoformat(timespec="seconds"))
        item["match"] = {
            **item.get("match", {}),
            "rank_score": item.get("match", {}).get("score", 80),
            "content_qualified": True,
            "relation": "core",
        }
    special_store = {**SPECIAL_ISSUE_STORE, "items": special_items}
    special_dialog = SpecialIssueDialog(
        special_store,
        special_items,
        [SELECTION_PAPER],
        selected_issue_id="special-recommended",
    )
    for theme_id in THEME_REGISTRY:
        apply_application_theme(app, theme_id, "comfortable")
        special_dialog.progress.hide()
        special_dialog.action_status.clear()
        special_dialog.refresh_button.setEnabled(True)
        workbench.settings_tabs.setCurrentIndex(0)
        entries.append(
            _capture(
                app,
                workbench,
                output / f"{theme_id}-research-keywords-1024x768.png",
                hide_after=False,
            )
        )
        workbench.settings_tabs.setCurrentIndex(1)
        entries.append(_capture(app, workbench, output / f"{theme_id}-research-strategy-1024x768.png"))

        selection_row = selection.candidate_list.itemWidget(selection.candidate_list.item(0))
        selection_row.evidence_button.setChecked(False)
        entries.append(
            _capture(app, selection, output / f"{theme_id}-journal-selection-1024x768.png", hide_after=False)
        )
        selection_row.evidence_button.setChecked(True)
        entries.append(_capture(app, selection, output / f"{theme_id}-journal-evidence-1024x768.png"))

        special_dialog.search_input.clear()
        special_dialog.view_tabs.setCurrentIndex(0)
        special_dialog.select_issue("special-recommended")
        special_dialog.evidence_toggle.setChecked(False)
        entries.append(_capture(app, special_dialog, output / f"{theme_id}-special-recommended-1024x768.png", hide_after=False))
        special_dialog.view_tabs.setCurrentIndex(1)
        entries.append(_capture(app, special_dialog, output / f"{theme_id}-special-saved-1024x768.png", hide_after=False))
        special_dialog.view_tabs.setCurrentIndex(2)
        entries.append(_capture(app, special_dialog, output / f"{theme_id}-special-unverified-1024x768.png", hide_after=False))
        special_dialog.view_tabs.setCurrentIndex(3)
        entries.append(_capture(app, special_dialog, output / f"{theme_id}-special-status-change-1024x768.png", hide_after=False))
        special_dialog.view_tabs.setCurrentIndex(0)
        special_dialog.select_issue("special-recommended")
        special_dialog.evidence_toggle.setChecked(True)
        entries.append(_capture(app, special_dialog, output / f"{theme_id}-special-evidence-1024x768.png", hide_after=False))
        special_dialog.set_progress(46, "正在核验官网与分区…")
        entries.append(_capture(app, special_dialog, output / f"{theme_id}-special-loading-1024x768.png", hide_after=False))
        special_dialog.evidence_toggle.setChecked(False)
        special_dialog.search_input.setText("no-result-fixture")
        entries.append(_capture(app, special_dialog, output / f"{theme_id}-special-empty-1024x768.png", hide_after=False))
        special_dialog.search_input.clear()
        special_dialog.finish_progress("一个来源暂不可用，旧结果已完整保留")
        entries.append(_capture(app, special_dialog, output / f"{theme_id}-special-error-1024x768.png"))

        apply_application_theme(app, theme_id, "compact")
        frontier._select_view("今日推荐")
        entries.append(_capture(app, frontier, output / f"{theme_id}-frontier-today-480x720.png", hide_after=False))
        frontier._select_view("全部记录")
        entries.append(_capture(app, frontier, output / f"{theme_id}-frontier-all-480x720.png"))
        for width, height in ((400, 480), (480, 720)):
            special_page.resize(width, height)
            entries.append(_capture(app, special_page, output / f"{theme_id}-special-widget-{width}x{height}.png"))
        for width, height in ((400, 480), (480, 720)):
            widget.resize(width, height)
            entries.append(_capture(app, widget, output / f"{theme_id}-widget-{width}x{height}.png"))
    workbench.deleteLater()
    selection.deleteLater()
    frontier.deleteLater()
    special_page.deleteLater()
    special_dialog.deleteLater()
    widget.deleteLater()
    app.processEvents()

    issue_count = sum(len(entry["issues"]) for entry in entries)
    report = {
        "version": 1,
        "renderer": "Qt QWidget.grab (offscreen)",
        "theme_count": len(THEME_REGISTRY),
        "screenshot_count": len(entries),
        "issue_count": issue_count,
        "entries": entries,
    }
    (output.parent / "geometry-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs" / "qa" / "v12-phase1" / "screenshots",
    )
    args = parser.parse_args()
    report = render(args.output.resolve())
    print(json.dumps({key: report[key] for key in ("theme_count", "screenshot_count", "issue_count")}, ensure_ascii=False))
    return 0 if report["issue_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
