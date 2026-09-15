"""Native offscreen UI release check for 科研助手 v11.5.3.

This does not invoke browser or computer-use tooling.  It renders the actual
Qt widgets offscreen, records their geometry, and saves PNGs for visual review.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from PySide6.QtWidgets import QApplication

from ui.frontier_settings_dialog import FrontierSettingsDialog
from ui.journal_library_page import JournalLibraryPage
from ui.journal_selection_dialog import JournalSelectionDialog
from ui.theme import apply_application_theme, ensure_application_font


OUTPUT = PROJECT_ROOT / ".test-results" / "v1153-ui"
os.environ.setdefault("RESEARCH_ASSISTANT_DATA_DIR", str(OUTPUT / "data"))


def _candidate(index: int, name: str, *, external: bool = False) -> dict:
    journal = {
        "id": f"candidate-{index}",
        "name": name,
        "publisher": "Elsevier",
        "fee_mode": "hybrid" if index % 2 else "subscription",
        "jcr": {
            "status": "verified",
            "source": "EasyScholar Open API",
            "checked_at": "2026-08-25",
            "metrics": [{"quartile": "Q1"}],
        },
        "easyscholar": {
            "source": "EasyScholar Open API",
            "checked_at": "2026-08-25",
            "cas_upgrade": "农林科学1区",
        },
    }
    return {
        "journal_id": journal["id"],
        "journal_name": name,
        "journal": journal,
        "is_external": external,
        "source": "AI 多轮核验",
        "ai_total_score": 96 - index * 3,
        "total_score": 96 - index * 3,
        "reason_cn": "研究对象、遥感数据与机器学习方法与论文摘要高度匹配，同时通过所选分区和投稿条件核验。",
        "risk_cn": "投稿前请根据期刊官网再次确认栏目范围和具体费用。",
    }


def _capture(app: QApplication, widget, name: str) -> dict:
    widget.show()
    app.processEvents()
    widget.repaint()
    app.processEvents()
    image = widget.grab().toImage()
    destination = OUTPUT / name
    image.save(str(destination))
    sample = []
    for x in range(0, image.width(), max(1, image.width() // 12)):
        for y in range(0, image.height(), max(1, image.height() // 12)):
            sample.append(image.pixelColor(x, y).rgba())
    return {
        "file": str(destination),
        "size": [image.width(), image.height()],
        "unique_sample_colors": len(set(sample)),
        "not_blank": len(set(sample)) > 1,
    }


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    ensure_application_font(app)
    apply_application_theme(app, "fog_teal", "comfortable")

    paper = {
        "id": "paper-1",
        "title": "多源遥感驱动的土壤有机碳精细制图",
        "keywords": ["soil organic carbon", "remote sensing", "machine learning"],
        "summary": "本研究整合多源遥感、地形和环境变量，构建机器学习框架进行区域尺度土壤有机碳精细制图，并评估模型不确定性与跨区域泛化能力。" * 12,
        "journals": [{"id": "old", "name": "已拒稿示例期刊", "status": "拒稿"}],
    }
    seed_journals = [_candidate(1, "CATENA")["journal"], _candidate(2, "Geoderma")["journal"]]

    selection = JournalSelectionDialog([paper], seed_journals, {})
    initial_checks = {
        "selection_is_1024x768": [selection.width(), selection.height()] == [1024, 768],
        "selection_initial_results_empty": selection.candidate_list.count() == 0,
        "selection_initial_oa_hidden": selection.oa_mode.isHidden(),
        "selection_initial_quartile_target_hidden": selection.quartile_target.isHidden(),
    }
    results = {"selection_before": _capture(app, selection, "selection-before.png")}
    selection.apply_ai_recommendation(
        {
            "ranked": [_candidate(1, "CATENA"), _candidate(2, "Geoderma"), _candidate(3, "Agricultural Systems")],
            "external_candidates": [_candidate(4, "Journal of Environmental Management", external=True), _candidate(5, "Science of the Total Environment", external=True)],
            "verified_count": 5,
        }
    )
    results["selection_results"] = _capture(app, selection, "selection-results.png")

    frontier = FrontierSettingsDialog(
        {
            "terms": [{"id": "soc", "text": "soil organic carbon", "weight": 100, "locked": True}],
            "pending_terms": [{"id": "rs", "text": "remote sensing", "weight": 65, "source": "manual"}],
            "require_verified_jcr_q1_q2": True,
        }
    )
    results["frontier_settings"] = _capture(app, frontier, "frontier-settings.png")

    library = JournalLibraryPage()
    library.resize(1024, 768)
    results["journal_library"] = _capture(app, library, "journal-library.png")

    results["checks"] = {
        **initial_checks,
        "frontier_settings_is_1024x768": [frontier.width(), frontier.height()] == [1024, 768],
        "selection_results_count": selection.candidate_list.count(),
    }
    (OUTPUT / "report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    for widget in (selection, frontier, library):
        widget.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
