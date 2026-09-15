"""Deterministic regression checks for v0.9.7 data safety and API savings.

Run with a source ``papers.json`` path.  The source is only read; every write
goes into a temporary data directory created by this script.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
SOURCE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else PROJECT / "data" / "papers.json"
WORK = Path(tempfile.mkdtemp(prefix="research-assistant-v094-"))
DATA = WORK / "data"
DATA.mkdir(parents=True, exist_ok=True)
shutil.copy2(SOURCE, DATA / "papers.json")
os.environ["RESEARCH_ASSISTANT_DATA_DIR"] = str(DATA)
sys.path.insert(0, str(PROJECT))

from utils.ai_service import (  # noqa: E402
    journal_ai_source_signature,
    journal_needs_ai_enrichment,
    research_profile_source_signature,
)
from utils.file_manager import (  # noqa: E402
    PAPERS_FILE,
    find_future_paper_date_issues,
    load_papers,
    repair_future_paper_dates,
    save_papers,
)
from utils.journal_service import journal_needs_metadata_enrichment  # noqa: E402
from utils.submission_reminders import due_submission_reminders  # noqa: E402


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity_snapshot(papers: list[dict]) -> list[tuple]:
    return [
        (
            str(paper.get("id", "")),
            str(paper.get("title", "")),
            tuple(str(item.get("path", "")) for item in paper.get("files", []) if isinstance(item, dict)),
            tuple(str(item.get("text", "")) for item in paper.get("ideas", []) if isinstance(item, dict)),
            tuple(
                (
                    str(journal.get("id", "")),
                    str(journal.get("name", "")),
                    str(journal.get("publisher", "")),
                    str(journal.get("status", "")),
                    str(journal.get("result", "")),
                    str(journal.get("notes", "")),
                    str(journal.get("revision_due_date", "")),
                    tuple(
                        (str(event.get("id", "")), str(event.get("status", "")), str(event.get("note", "")))
                        for event in journal.get("timeline", [])
                        if isinstance(event, dict)
                    ),
                )
                for journal in paper.get("journals", [])
            ),
        )
        for paper in papers
    ]


source_digest = digest(SOURCE)
temp_digest_before = digest(PAPERS_FILE)
papers = load_papers()
before_snapshot = identity_snapshot(papers)
issues = find_future_paper_date_issues(papers)
assert digest(SOURCE) == source_digest, "Read-only load modified the real source data."

if issues:
    blocked = save_papers(papers)
    assert not blocked["saved"] and blocked["future_date_issue_count"] == len(issues)
    assert digest(PAPERS_FILE) == temp_digest_before, "Blocked save changed the isolated legacy file."
    assert digest(SOURCE) == source_digest, "Blocked save changed the real source data."

    repair = repair_future_paper_dates(papers)
    assert repair["total"] == len(issues)
else:
    repair = {"submission_dates": 0, "status_updated_dates": 0, "timeline_dates": 0, "total": 0}

saved = save_papers(papers)
assert saved["saved"], saved
reloaded = load_papers()
assert identity_snapshot(reloaded) == before_snapshot, "Repair lost a paper, journal, ID or timeline row."
assert not find_future_paper_date_issues(reloaded), "Future dates remain after explicit repair."
assert digest(SOURCE) == source_digest, "Repair touched the real source data."

future_attempt = copy.deepcopy(reloaded)
first_journal = next(
    (
        journal
        for paper in future_attempt
        for journal in paper.get("journals", [])
        if isinstance(journal, dict)
    ),
    None,
)
if first_journal is not None:
    first_journal["status_updated_at"] = (date.today() + timedelta(days=1)).isoformat()
    persisted_digest = digest(PAPERS_FILE)
    assert not save_papers(future_attempt)["saved"], "A new future status date was saved."
    assert digest(PAPERS_FILE) == persisted_digest, "A blocked future date changed persisted data."

today = date(2026, 8, 11)
reminder_papers = [
    {
        "id": "paper-a",
        "title": "Reminder target",
        "journals": [
            {
                "id": "journal-a",
                "name": "CATENA",
                "status": "外审中",
                "date": "2020-01-01",
                "status_updated_at": "2026-07-27",
            }
        ],
    }
]
due = due_submission_reminders(reminder_papers, today=today)
assert len(due) == 1 and due[0]["days"] == 15
next_day = due_submission_reminders(reminder_papers, today=today + timedelta(days=1))
assert len(next_day) == 1 and next_day[0]["id"] == due[0]["id"], "Missed day should retain the same checkpoint."
snoozed = due_submission_reminders(
    reminder_papers,
    {"snoozed_until": {due[0]["id"]: "2026-08-14"}},
    today=today,
)
assert not snoozed, "Snoozed reminder was shown too early."
reminder_papers[0]["journals"][0]["status_updated_at"] = today.isoformat()
assert not due_submission_reminders(reminder_papers, today=today), "Handled reminder did not reset its clock."

ai_config = {"model": "deepseek-test", "profile_read_achievement_pdfs": False}
achievement = {"id": "outcome-1", "title": "SOC mapping", "category": "论文", "paper": {"keywords": ["SOC"], "summary": "mapping"}}
feedback = [{"id": "frontier-1", "title": "Soil carbon", "feedback": "relevant", "match_terms": ["SOC"]}]
signature = research_profile_source_signature([achievement], feedback, ai_config)
assert signature == research_profile_source_signature([achievement], feedback, ai_config)
changed_achievement = copy.deepcopy(achievement)
changed_achievement["paper"]["keywords"].append("MAOC")
assert signature != research_profile_source_signature([changed_achievement], feedback, ai_config)

journal = {"id": "j-1", "name": "CATENA", "publisher": "Elsevier", "fields": ["soil"], "notes": ""}
journal["ai_source_signature"] = journal_ai_source_signature(journal, "deepseek-test")
assert not journal_needs_ai_enrichment(journal, "deepseek-test")
journal["notes"] = "faster review"
assert journal_needs_ai_enrichment(journal, "deepseek-test")
assert journal_needs_metadata_enrichment({"name": "CATENA", "publisher": "Elsevier", "issn": "0341-8162"})

print(json.dumps({
    "result": "ok",
    "source": str(SOURCE),
    "papers": len(reloaded),
    "future_issues_repaired": repair,
    "initial_future_issue_count": len(issues),
    "source_unchanged": digest(SOURCE) == source_digest,
    "work": str(WORK),
}, ensure_ascii=False))
