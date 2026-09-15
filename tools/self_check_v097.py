"""Focused v0.9.7 regressions that never mutate the user's real data.

The script uses a temporary ``RESEARCH_ASSISTANT_DATA_DIR`` and a mocked
DeepSeek response.  It checks no-date tasks, AI-labelled JCR estimates and
the model-response parsing path without consuming API quota.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch


PROJECT = Path(__file__).resolve().parents[1]
WORK = Path(tempfile.mkdtemp(prefix="research-assistant-v097-"))
os.environ["RESEARCH_ASSISTANT_DATA_DIR"] = str(WORK / "data")
sys.path.insert(0, str(PROJECT))

from utils import ai_service  # noqa: E402
from utils.file_manager import load_todos, normalize_library_journal, save_todos  # noqa: E402
from utils.jcr_service import jcr_label, primary_jcr_quartile  # noqa: E402


today = date(2026, 8, 20)
tomorrow = today + timedelta(days=1)

# A task without a date window remains actionable until completion, but does
# not turn into a daily recurrence or a migration prompt.
unscheduled = {
    "id": "no-date-task",
    "title": "整理长期文献线索",
    "schedule_mode": "none",
    "created_for": today.isoformat(),
    "start_date": "",
    "end_date": "",
    "repeat_daily": False,
    "done": False,
    "done_dates": [],
}
save_todos(today, [unscheduled])
today_items = load_todos(today)
tomorrow_items = load_todos(tomorrow)
assert len(today_items) == 1 and today_items[0]["schedule_mode"] == "none"
assert len(tomorrow_items) == 1 and not tomorrow_items[0]["done"], "Unscheduled task disappeared before completion."

finished = dict(today_items[0])
finished["done"] = True
save_todos(today, [finished])
assert not load_todos(tomorrow), "Completed no-date task leaked into the following day."

# AI estimates are directly editable data, but must never masquerade as a
# verified Clarivate JCR result.
ai_jcr_journal = normalize_library_journal(
    {
        "id": "j-ai",
        "name": "Example Soil Journal",
        "jcr": {
            "status": "ai_estimated",
            "source": "DeepSeek AI 估计（待 Clarivate 核验）",
            "confidence": "中",
            "note": "模型依据刊名作出保守估计。",
            "metrics": [{"quartile": "Q1", "year": 0}],
        },
    }
)
assert primary_jcr_quartile(ai_jcr_journal) == "Q1"
assert jcr_label(ai_jcr_journal) == "AI 估计 Q1"
assert ai_service.journal_needs_ai_jcr_estimate(ai_jcr_journal) is False
assert ai_service.journal_needs_ai_jcr_estimate({"name": "Missing JCR", "jcr": {"status": "pending", "metrics": []}})

# Validate the exact structured-response path used by the live provider with a
# deterministic mock, including a JCR estimate and normal Chinese enrichment.
mock_response = {
    "journals": [
        {
            "id": "j-live",
            "tags": ["土壤碳", "数字土壤制图"],
            "scope_cn": "聚焦土壤碳过程与空间制图研究。",
            "fit_cn": "适合土壤有机碳与遥感建模方向。",
            "risks_cn": "需进一步核验栏目范围。",
            "jcr_estimate": {"quartile": "Q1", "confidence": "medium", "reason": "仅为模型估计，需官方核验。"},
        }
    ]
}
with patch.object(ai_service, "_require_config", return_value=({"model": "deepseek-test"}, "test-key")), patch.object(
    ai_service, "_chat_json", return_value=mock_response
):
    enriched = ai_service.enrich_journals_with_ai(
        [{"id": "j-live", "name": "Example Soil Journal", "publisher": "Example", "issn": "0000-0000"}]
    )
assert enriched["requested"] == 1 and len(enriched["updates"]) == 1
estimate = enriched["updates"][0].get("ai_jcr_estimate", {})
assert estimate.get("quartile") == "Q1" and estimate.get("confidence") == "medium"

print(
    json.dumps(
        {
            "result": "ok",
            "no_date_task": "persists_until_completed",
            "ai_jcr_label": jcr_label(ai_jcr_journal),
            "mocked_deepseek_updates": len(enriched["updates"]),
            "work": str(WORK),
        },
        ensure_ascii=False,
    )
)
