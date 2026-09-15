"""Deadline and lifecycle reminder rules for special-issue calls."""

from __future__ import annotations

from datetime import date

from tests import _data_root  # noqa: F401


def _store(*, status: str = "saved", deadline: str = "2026-11-29", logs=None, verification: str = "official_verified"):
    return {
        "items": [
            {
                "id": "si-1",
                "title": "Soil carbon collection",
                "status": status,
                "deadline": deadline,
                "verification_status": verification,
                "deadline_history": [],
            }
        ],
        "reminder_log": logs or [],
    }


def test_offsets_are_exactly_the_confirmed_schedule() -> None:
    from utils.submission_reminders import special_issue_reminder_offsets

    assert special_issue_reminder_offsets() == (90, 60, 30, 14, 7, 3)


def test_saved_call_emits_one_exact_offset_and_log_deduplicates_it() -> None:
    from utils.submission_reminders import collect_special_issue_reminders

    today = date(2026, 8, 31)
    result = collect_special_issue_reminders(_store(), today=today)
    assert [(row["kind"], row["days_remaining"], row["offset"]) for row in result] == [("deadline", 90, 90)]
    reminder_id = result[0]["id"]
    assert collect_special_issue_reminders(_store(logs=[{"id": reminder_id}]), today=today) == []


def test_unread_or_ignored_calls_do_not_emit_deadline_reminders() -> None:
    from utils.submission_reminders import collect_special_issue_reminders

    assert collect_special_issue_reminders(_store(status="unread"), today=date(2026, 8, 31)) == []
    assert collect_special_issue_reminders(_store(status="ignored"), today=date(2026, 8, 31)) == []


def test_deadline_change_and_closed_events_have_separate_stable_ids() -> None:
    from utils.submission_reminders import collect_special_issue_reminders

    store = _store(deadline="2027-01-31", verification="closed")
    store["items"][0]["deadline_history"] = [
        {"deadline": "2026-12-31", "recorded_at": "2026-08-20T10:00:00"},
        {"deadline": "2027-01-31", "recorded_at": "2026-08-30T10:00:00"},
    ]
    result = collect_special_issue_reminders(store, today=date(2026, 8, 31))
    assert {row["kind"] for row in result} == {"deadline_changed", "closed"}
    assert len({row["id"] for row in result}) == 2

