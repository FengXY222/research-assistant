"""Render v11.1's key pages with isolated data and verify release UI invariants."""

from __future__ import annotations

import argparse
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run native Qt visual QA for Research Assistant v11.1")
    parser.add_argument("--output-root", required=True, help="Empty QA evidence folder; formal data is never read.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    qa_root = Path(args.output_root).resolve()
    qa_root.mkdir(parents=True, exist_ok=True)
    data_root = qa_root / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    os.environ["RESEARCH_ASSISTANT_DATA_DIR"] = str(data_root)

    from PySide6.QtGui import QImage
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QToolButton

    from ui.frontier_page import DailyFrontierPage
    from ui.home_page import HomePage
    from ui.journal_library_page import JournalLibraryPage
    from ui.paper_page import PaperPage
    from ui.research_profile_dialog import ResearchProfileDialog
    from ui.settings_dialog import SettingsDialog
    from ui.theme import apply_application_theme, ensure_application_font
    from ui.todo_page import TodoPage
    from utils.file_manager import (
        save_frontier_data,
        save_inspirations,
        save_journal_library,
        save_papers,
        save_readings,
        save_rejection_archive,
        save_todos,
    )

    application = QApplication.instance() or QApplication([])
    ensure_application_font(application)
    today = date.today().isoformat()
    manifest: dict[str, object] = {"screenshots": {}, "checks": {}}

    journals = [
        {
            "id": "journal-q1",
            "name": "CATENA",
            "publisher": "Elsevier",
            "fields": ["土壤科学", "遥感"],
            "favorite": True,
            "frontier_priority": "必看",
            "jcr": {"status": "verified", "source": "EasyScholar Open API", "metrics": [{"quartile": "Q1", "year": 2025}]},
            "easyscholar": {"source": "EasyScholar Open API", "checked_at": today, "cas_upgrade": "2区", "cas_basic": "2区", "impact_factor": "6.1"},
        },
        {
            "id": "journal-q2",
            "name": "Geoderma",
            "publisher": "Elsevier",
            "fields": ["数字土壤制图"],
            "frontier_priority": "关注",
            "jcr": {"status": "verified", "source": "EasyScholar Open API", "metrics": [{"quartile": "Q2", "year": 2025}]},
            "easyscholar": {"source": "EasyScholar Open API", "checked_at": today, "cas_upgrade": "2区", "impact_factor": "7.2"},
        },
    ]
    paper_title = "多源遥感驱动的土壤有机碳制图"
    papers = [
        {
            "id": "paper-ui",
            "title": paper_title,
            "summary": "以多源遥感、环境协变量和机器学习模型开展区域土壤有机碳制图。",
            "keywords": ["SOC", "遥感", "数字土壤制图"],
            "journals": [
                {
                    "id": "journal-history-q1",
                    "name": "CATENA",
                    "publisher": "Elsevier",
                    "status": "外审中",
                    "date": "2026-08-01",
                    "status_updated_at": "2026-08-18",
                    "jcr": {"status": "verified", "metrics": [{"quartile": "Q1", "year": 2025}]},
                    "easyscholar": {"cas_upgrade": "2区", "impact_factor": "6.1"},
                }
            ],
        }
    ]
    save_todos(
        date.today(),
        [
            {"id": "task-urgent", "title": "修改论文摘要并确认投稿策略", "done": False, "quadrant": "urgent_important", "schedule_mode": "none", "end_date": today},
            {"id": "task-normal", "title": "整理图件注释", "done": False, "quadrant": "not_urgent_important", "schedule_mode": "none"},
        ],
    )
    save_papers(papers)
    save_journal_library(journals)
    save_inspirations([{"id": "idea-ui", "text": "比较多源环境变量对 SOC 制图精度的贡献。"}])
    save_readings([{"id": "reading-ui", "title": "Soil carbon mapping with remote sensing", "status": "未阅读", "reason": "方法参考"}])
    save_rejection_archive(
        [{"id": "archive-ui", "paper_id": "paper-ui", "paper_title": paper_title, "journal_name": "Archive Example", "reason": "范围不匹配", "archived_at": today}]
    )
    save_frontier_data({"profile": {"terms": [{"text": "soil organic carbon", "weight": 100, "locked": True}]}, "items": []})

    def settle() -> None:
        application.processEvents()
        QTest.qWait(80)
        application.processEvents()

    def image_samples(image: QImage) -> list[str]:
        points = [(8, 8), (min(80, image.width() - 1), min(80, image.height() - 1)), (image.width() // 2, image.height() // 2)]
        return [image.pixelColor(x, y).name() for x, y in points]

    def screenshot(widget, name: str, width: int = 1120, height: int = 760) -> QImage:
        widget.resize(width, height)
        widget.show()
        settle()
        image = widget.grab().toImage()
        if image.isNull() or image.width() < 200 or image.height() < 200:
            raise AssertionError(f"{name}: empty native render")
        path = qa_root / f"{name}.png"
        if not image.save(str(path), "PNG"):
            raise AssertionError(f"{name}: failed to save image")
        manifest["screenshots"][name] = {
            "path": str(path),
            "sha256": sha256(path.read_bytes()).hexdigest(),
            "size": [image.width(), image.height()],
            "samples": image_samples(image),
        }
        return image

    def close(widget) -> None:
        widget.close()
        widget.deleteLater()
        settle()

    apply_application_theme(application, "fog_teal", "comfortable")
    settings_dialog = SettingsDialog({"appearance": {"theme_id": "fog_teal", "density": "comfortable"}, "application_mode": "software"})
    settings_dialog.theme_previewed.connect(lambda theme_id, density: apply_application_theme(application, theme_id, density))
    settings_dialog.theme_preview_reverted.connect(lambda theme_id, density: apply_application_theme(application, theme_id, density))
    fog_image = screenshot(settings_dialog, "settings-fog-teal", 680, 860)
    fog_samples = image_samples(fog_image)
    night_index = settings_dialog.theme_selector.findData("night_coral")
    if night_index < 0:
        raise AssertionError("night_coral theme is unavailable")
    settings_dialog.theme_selector.setCurrentIndex(night_index)
    settle()
    night_image = screenshot(settings_dialog, "settings-night-coral", 680, 860)
    if fog_samples == image_samples(night_image):
        raise AssertionError("theme preview did not change native rendered pixels")
    if settings_dialog.values()["appearance"]["theme_id"] != "night_coral":
        raise AssertionError("theme selector did not update its saved value")
    settings_dialog.reject()
    settle()
    if application.property("research_assistant_theme_id") != "fog_teal":
        raise AssertionError("theme preview revert did not restore fog_teal")
    manifest["checks"]["theme_preview_changed_native_pixels"] = True
    manifest["checks"]["theme_preview_reverted"] = True
    close(settings_dialog)

    apply_application_theme(application, "fog_teal", "comfortable")
    home = HomePage()
    screenshot(home, "home-fog-teal")
    today_preview = home.findChild(QLabel, "todayTaskPreview")
    if today_preview is None or "修改论文摘要" not in today_preview.text():
        raise AssertionError("HOME did not render concrete unfinished tasks")
    home_links = [button for button in home.findChildren(QPushButton) if button.text() == "查看与编辑"]
    if len(home_links) < 2 or not all(button.minimumHeight() >= 24 for button in home_links):
        raise AssertionError("HOME edit actions are not stable")
    manifest["checks"]["home_task_preview_and_edit_actions"] = True
    close(home)

    todo = TodoPage()
    screenshot(todo, "work-priority")
    priority = todo.list_widget.findChild(QPushButton, "todoPriorityButton")
    todo_text = todo.list_widget.findChild(QLabel, "todoText")
    if priority is None or todo_text is None or priority.width() > 104 or todo_text.width() < int(todo.list_widget.viewport().width() * 0.45):
        raise AssertionError("WORK priority layout is not readable")
    manifest["checks"]["work_priority_layout"] = True
    close(todo)

    paper_page = PaperPage()
    screenshot(paper_page, "papers-records")
    archive = paper_page.findChild(QToolButton, "archiveCornerButton")
    if archive is None or "拒稿归档 1" not in archive.text():
        raise AssertionError("PAPERS archive corner control is missing")
    row_action_sizes = {button.font().pixelSize() for button in paper_page.findChildren(QPushButton) if button.objectName() == "rowButton"}
    if row_action_sizes and row_action_sizes != {12}:
        raise AssertionError(f"PAPERS row action font sizes unexpected: {row_action_sizes}")
    manifest["checks"]["papers_archive_and_typography"] = True
    close(paper_page)

    frontier = DailyFrontierPage()
    frontier.data = {
        "profile": {"daily_limit": 10, "_easyscholar_configured": True, "terms": [{"text": "SOC", "weight": 100, "locked": True}]},
        "last_checked": today,
        "algorithm_version": 11,
        "items": [
            {"id": "frontier-q1", "title": "Q1 soil carbon mapping", "journal": "CATENA", "published_date": today, "recommendation_date": today, "status": "new", "score": 260, "jcr_quartile": "Q1", "jcr_state": "verified", "quality_gate_state": "eligible", "quality_gate_reason": "verified_q1_q2", "cas_upgrade": "2区", "journal_metric_line": "JCR Q1 · 中科院升级版 2区 · IF 6.1", "match_terms": ["SOC"], "summary_cn": "Q1 期刊的土壤碳制图研究。"},
            {"id": "frontier-q2", "title": "Q2 digital soil mapping", "journal": "Geoderma", "published_date": today, "recommendation_date": today, "status": "new", "score": 240, "jcr_quartile": "Q2", "jcr_state": "verified", "quality_gate_state": "eligible", "quality_gate_reason": "verified_q1_q2", "cas_upgrade": "2区", "journal_metric_line": "JCR Q2 · 中科院升级版 2区 · IF 7.2", "match_terms": ["SOC"], "summary_cn": "Q2 期刊的数字土壤制图研究。"},
            {"id": "frontier-q3", "title": "Q3 must be filtered", "journal": "Low Quality Journal", "published_date": today, "recommendation_date": today, "status": "new", "score": 300, "jcr_quartile": "Q3", "jcr_state": "verified", "quality_gate_state": "blocked", "quality_gate_reason": "known_q3_q4", "match_terms": ["SOC"]},
            {"id": "frontier-unknown", "title": "Unknown must be filtered", "journal": "Unknown Journal", "published_date": today, "recommendation_date": today, "status": "new", "score": 290, "jcr_quartile": "", "jcr_state": "pending", "quality_gate_state": "blocked", "quality_gate_reason": "quality_pending", "match_terms": ["SOC"]},
        ],
    }
    with patch("utils.frontier_service.is_easyscholar_ready", return_value=True):
        frontier._render()
        screenshot(frontier, "daily-frontier-q1-q2")
        frontier_text = "\n".join(label.text() for label in frontier.findChildren(QLabel))
    if "Q3 must be filtered" in frontier_text or "Unknown must be filtered" in frontier_text:
        raise AssertionError("Daily Frontier exposed a Q3 or unresolved record under configured quality gating")
    if "中科院 2区" not in frontier_text:
        raise AssertionError("Daily Frontier did not display CAS division")
    manifest["checks"]["daily_frontier_q1_q2_and_cas"] = True
    close(frontier)

    library = JournalLibraryPage()
    screenshot(library, "journal-library-metrics")
    library_health = "\n".join(label.text() for label in library.findChildren(QLabel, "journalHealthChip"))
    library_cas = "\n".join(label.text() for label in library.findChildren(QLabel, "journalCasBadge"))
    if "Q1" not in library_health or "中科院 2区" not in library_cas:
        raise AssertionError("Journal Library did not display cached JCR/CAS metrics")
    manifest["checks"]["journal_library_jcr_cas"] = True
    close(library)

    profile = ResearchProfileDialog({"terms": [{"text": "soil organic carbon", "weight": 100, "locked": True}, {"text": "遥感", "weight": 78, "locked": False}], "sources": {"crossref": {"enabled": True}, "openalex": {"enabled": True}}})
    screenshot(profile, "research-profile", 700, 820)
    profile_actions = [button.font().pixelSize() for button in profile.findChildren(QPushButton) if button.objectName() == "rowButton"]
    if profile_actions and any(size != 12 for size in profile_actions):
        raise AssertionError(f"Research Profile row actions have unexpected sizes: {profile_actions}")
    manifest["checks"]["research_profile_typography"] = True
    close(profile)

    manifest["data_root"] = str(data_root)
    manifest_path = qa_root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"qa_root": str(qa_root), "manifest": str(manifest_path), "checks": manifest["checks"], "screenshots": list(manifest["screenshots"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
