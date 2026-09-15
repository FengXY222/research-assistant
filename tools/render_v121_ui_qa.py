"""Render the v12.1 workbenches offscreen without desktop automation."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("RESEARCH_ASSISTANT_DATA_DIR", str(Path(__file__).resolve().parents[1] / ".qa-user-data"))
os.environ.setdefault("RESEARCH_ASSISTANT_DISABLE_BACKGROUND", "1")

PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QPushButton

from ui.frontier_page import DailyFrontierPage
from ui.frontier_settings_dialog import FrontierSettingsDialog, JournalPreferenceScoreDialog
from ui.journal_selection_dialog import JournalSelectionDialog
from ui.special_issue_dialog import SpecialIssueDialog
from ui.special_issue_page import SpecialIssuePage
from ui.theme import apply_application_theme, ensure_application_font
from utils.file_manager import load_journal_library, load_papers
from utils.research_profile_repository import load_research_profile
from utils.special_issue_repository import load_special_issue_store, save_special_issue_store


def _selection_rows(journals: list[dict]) -> list[dict]:
    rows = []
    for index, journal in enumerate(journals[:4], start=1):
        name = str(journal.get("name", f"期刊 {index}"))
        rows.append(
            {
                "result_id": f"qa-{index}",
                "journal_id": str(journal.get("id", f"qa-journal-{index}")),
                "journal_name": name,
                "journal": journal,
                "ai_total_score": 96 - index * 3,
                "reason_cn": "相似论文的真实发表期刊与论文主题、方法及研究对象高度接近。",
                "publisher": str(journal.get("publisher", "出版社待核验")),
                "fee_mode": str(journal.get("fee_mode", "unknown")),
                "estimated_speed_text": "预计 30–90 天",
                "similar_papers": [{"title": "相似论文证据", "source_names": ["OpenAlex", "Crossref"]}],
                "identity_evidence": {"verified": True, "issns": [str(journal.get("issn", ""))], "source_names": ["Crossref"]},
            }
        )
    return rows


def _capture(app: QApplication, widget, output: Path, name: str) -> dict:
    widget.show()
    app.processEvents()
    loop = QEventLoop()
    QTimer.singleShot(260, loop.quit)
    loop.exec()
    app.processEvents()
    image = widget.grab().toImage()
    path = output / f"{name}.png"
    if not image.save(str(path)):
        raise RuntimeError(f"无法保存 {path}")
    colors = set()
    step_x = max(1, image.width() // 80)
    step_y = max(1, image.height() // 60)
    for y in range(0, image.height(), step_y):
        for x in range(0, image.width(), step_x):
            colors.add(image.pixelColor(x, y).rgba())
    return {
        "path": str(path),
        "width": image.width(),
        "height": image.height(),
        "sampled_colors": len(colors),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def main() -> int:
    output = PROJECT / "发布" / "v12.2-ui-qa"
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    ensure_application_font(app)
    app.setProperty("research_assistant_runtime_ready", False)
    profile = load_research_profile()
    papers = load_papers()
    journals = load_journal_library()
    special_store = load_special_issue_store()
    if special_store.get("items"):
        now_text = "2026-09-07T12:00:00"
        for index, item in enumerate(special_store["items"][:2]):
            item.update(
                scope_is_complete=True,
                scope_status="full",
                verification_status="official_verified",
                call_status="open",
                official_checked_at=now_text,
            )
            item["match"] = {
                **(item.get("match", {}) if isinstance(item.get("match"), dict) else {}),
                "score": 88 - index * 4,
                "rank_score": 88 - index * 4,
                "formal": True,
                "content_qualified": True,
                "relation": "core",
                "reason": "征稿范围与土壤碳、数字土壤制图及研究方法存在明确对应关系。",
            }
        special_store["items"][0].update(status="saved", saved=True, ignored=False)
        save_special_issue_store(special_store)
        special_store = load_special_issue_store()
    manifest: dict[str, object] = {"screenshots": {}, "checks": {}}

    apply_application_theme(app, "fog_teal", "comfortable")
    settings = FrontierSettingsDialog(profile)
    manifest["screenshots"]["research-settings-light"] = _capture(app, settings, output, "research-settings-light")
    manifest["checks"]["settings_tabs"] = [settings.settings_tabs.tabText(i) for i in range(settings.settings_tabs.count())]
    manifest["checks"]["settings_tab_height"] = settings.settings_tabs.tabBar().height()
    settings.close()

    journal_scores = JournalPreferenceScoreDialog(journals)
    manifest["screenshots"]["journal-score-workbench"] = _capture(app, journal_scores, output, "journal-score-workbench")
    manifest["checks"]["journal_score_rows"] = journal_scores.table.rowCount()
    journal_scores.close()

    frontier = DailyFrontierPage()
    frontier.resize(400, 480)
    manifest["screenshots"]["daily-frontier-widget-light"] = _capture(app, frontier, output, "daily-frontier-widget-light")
    manifest["checks"]["frontier_rendered_cards"] = max(0, frontier.rows.count() - 1)
    frontier.close()

    special_widget = SpecialIssuePage()
    special_widget.resize(400, 480)
    manifest["screenshots"]["special-issue-widget-light"] = _capture(app, special_widget, output, "special-issue-widget-light")
    manifest["checks"]["special_widget_rows"] = len(special_widget.preview_rows)
    special_widget.close()

    apply_application_theme(app, "night_coral", "comfortable")
    dark_settings = FrontierSettingsDialog(profile)
    manifest["screenshots"]["research-settings-dark"] = _capture(app, dark_settings, output, "research-settings-dark")
    dark_settings.close()

    dark_frontier = DailyFrontierPage()
    dark_frontier.resize(400, 480)
    manifest["screenshots"]["daily-frontier-widget-dark"] = _capture(app, dark_frontier, output, "daily-frontier-widget-dark")
    dark_frontier.close()

    apply_application_theme(app, "fog_teal", "comfortable")
    special_qa_store = json.loads(json.dumps(special_store, ensure_ascii=False))
    if special_qa_store.get("items"):
        special_qa_store["items"][0]["status"] = "saved"
    special = SpecialIssueDialog(special_qa_store, special_qa_store.get("items", []), papers)
    special.view_tabs.setCurrentIndex(1)
    manifest["screenshots"]["special-issue-workbench"] = _capture(app, special, output, "special-issue-workbench")
    manifest["checks"]["special_splitter"] = special.workbench_splitter.sizes()
    manifest["checks"]["special_scope_has_chinese"] = "中文翻译" in special.scope_view.toPlainText()
    manifest["checks"]["special_scope_has_code"] = any(
        marker in special.scope_view.toPlainText().casefold()
        for marker in ("window.nreum", ".batch_articles", "#main-content", ":hover")
    )
    special.close()

    selection = JournalSelectionDialog(papers, journals, profile)
    selection.apply_ai_recommendation({"results": _selection_rows(journals), "rounds": 2})
    manifest["screenshots"]["journal-selection-workbench"] = _capture(app, selection, output, "journal-selection-workbench")
    manifest["checks"]["selection_has_exclude"] = bool(selection.findChild(QPushButton, "selectionExcludeButton"))
    manifest["checks"]["selection_result_count"] = selection.candidate_list.count()
    selection.close()

    screenshots = manifest["screenshots"]
    manifest["checks"]["theme_images_differ"] = (
        screenshots["research-settings-light"]["sha256"] != screenshots["research-settings-dark"]["sha256"]
        and screenshots["daily-frontier-widget-light"]["sha256"] != screenshots["daily-frontier-widget-dark"]["sha256"]
    )
    manifest["checks"]["all_images_nonblank"] = all(value["sampled_colors"] >= 12 for value in screenshots.values())
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(manifest_path), **manifest["checks"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
