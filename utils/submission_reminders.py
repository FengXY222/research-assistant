"""Submission-age reminder rules for active journal records."""

from __future__ import annotations

from datetime import date
from typing import Any


REMINDER_INTERVAL_DAYS = 15
REMINDER_STATUSES = {"投稿中", "外审中", "修改中"}
SPECIAL_ISSUE_OFFSETS = (90, 60, 30, 14, 7, 3)


def special_issue_reminder_offsets() -> tuple[int, ...]:
    return SPECIAL_ISSUE_OFFSETS


def collect_special_issue_reminders(store: dict[str, Any], *, today: date) -> list[dict[str, Any]]:
    """Collect saved-call deadline and lifecycle events without mutating state."""
    payload = store if isinstance(store, dict) else {}
    logged = {
        str(value.get("id", "")).strip()
        for value in payload.get("reminder_log", [])
        if isinstance(value, dict) and str(value.get("id", "")).strip()
    }
    logged_deadline_offsets: dict[tuple[str, str], list[int]] = {}
    for value in payload.get("reminder_log", []):
        if not isinstance(value, dict) or str(value.get("kind", "")) != "deadline":
            continue
        key = (str(value.get("issue_id", "")).strip(), str(value.get("deadline", "")).strip()[:10])
        try:
            logged_deadline_offsets.setdefault(key, []).append(int(value.get("offset", -1)))
        except (TypeError, ValueError):
            continue
    reminders: list[dict[str, Any]] = []
    for item in payload.get("items", []) if isinstance(payload.get("items"), list) else []:
        if not isinstance(item, dict) or str(item.get("status", "")).casefold() != "saved":
            continue
        issue_id = str(item.get("id", "")).strip()
        title = str(item.get("title", "未命名特刊")).strip() or "未命名特刊"
        deadline = str(item.get("deadline", "")).strip()[:10]
        try:
            remaining = (date.fromisoformat(deadline) - today).days
        except ValueError:
            remaining = -1
        verification = str(item.get("verification_status", "")).casefold()
        call_status = str(item.get("call_status", "")).casefold()
        is_closed = verification in {"closed", "expired"} or call_status in {"closed", "expired"}
        eligible_offsets = [offset for offset in SPECIAL_ISSUE_OFFSETS if 0 <= remaining <= offset]
        if eligible_offsets and not is_closed:
            offset = min(eligible_offsets)
            reminder_id = f"special-issue|deadline|{issue_id}|{deadline}|{offset}"
            sent_later = any(
                sent_offset <= offset
                for sent_offset in logged_deadline_offsets.get((issue_id, deadline), [])
            )
            if reminder_id not in logged and not sent_later:
                reminders.append(
                    {
                        "id": reminder_id,
                        "kind": "deadline",
                        "issue_id": issue_id,
                        "title": title,
                        "deadline": deadline,
                        "days_remaining": remaining,
                        "offset": offset,
                    }
                )

        history = [value for value in item.get("deadline_history", []) if isinstance(value, dict)]
        previous = current = ""
        if history:
            latest = history[-1]
            previous = str(latest.get("previous", "")).strip()[:10]
            current = str(latest.get("current", "")).strip()[:10]
        if not previous or not current:
            history_deadlines = [
                str(value.get("deadline", "")).strip()[:10]
                for value in history
                if str(value.get("deadline", "")).strip()
            ]
            if len(history_deadlines) >= 2:
                previous, current = history_deadlines[-2], history_deadlines[-1]
        if previous and current and previous != current:
            reminder_id = f"special-issue|deadline-changed|{issue_id}|{previous}|{current}"
            if reminder_id not in logged:
                reminders.append(
                    {
                        "id": reminder_id,
                        "kind": "deadline_changed",
                        "issue_id": issue_id,
                        "title": title,
                        "previous_deadline": previous,
                        "deadline": current,
                    }
                )
        closed_state = verification if verification in {"closed", "expired"} else call_status
        if is_closed:
            reminder_id = f"special-issue|closed|{issue_id}|{deadline}|{closed_state}"
            if reminder_id not in logged:
                reminders.append(
                    {
                        "id": reminder_id,
                        "kind": "closed",
                        "issue_id": issue_id,
                        "title": title,
                        "deadline": deadline,
                        "verification_status": closed_state,
                    }
                )
    order = {"deadline_changed": 0, "closed": 1, "deadline": 2}
    return sorted(reminders, key=lambda value: (order.get(str(value.get("kind")), 9), str(value.get("id", ""))))


def _reminder_state(value: set[str] | dict[str, Any] | None) -> tuple[set[str], dict[str, str]]:
    """Accept old dismissed-ID sets as well as the newer state dictionary."""
    if isinstance(value, dict):
        dismissed = value.get("dismissed", set())
        snoozed = value.get("snoozed_until", {})
        return (
            {str(item) for item in dismissed if str(item)} if isinstance(dismissed, (set, list, tuple)) else set(),
            {str(key): str(day) for key, day in snoozed.items() if str(key) and str(day)} if isinstance(snoozed, dict) else {},
        )
    return ({str(item) for item in value if str(item)} if isinstance(value, (set, list, tuple)) else set(), {})


def due_submission_reminders(
    papers: list[dict],
    reminder_state: set[str] | dict[str, Any] | None = None,
    today: date | None = None,
) -> list[dict]:
    """Find active journals whose current status has gone 15 days without an update.

    A missed calendar day still yields one reminder for the current 15-day
    checkpoint (for example, day 16 produces the day-15 checkpoint), so a
    desktop app that was not opened on an exact day does not lose the prompt.
    """
    today = today or date.today()
    dismissed_ids, snoozed_until = _reminder_state(reminder_state)
    reminders: list[dict] = []

    for paper in papers:
        title = str(paper.get("title", "未命名论文"))
        for journal in paper.get("journals", []):
            status = str(journal.get("status", ""))
            status_updated_at = str(journal.get("status_updated_at", "")).strip() or str(journal.get("date", "")).strip()
            if status not in REMINDER_STATUSES or not status_updated_at:
                continue
            try:
                days = (today - date.fromisoformat(status_updated_at)).days
            except ValueError:
                continue
            if days < REMINDER_INTERVAL_DAYS:
                continue

            journal_name = str(journal.get("name", "未填写期刊"))
            checkpoint = days // REMINDER_INTERVAL_DAYS
            paper_id = str(paper.get("id", ""))
            journal_id = str(journal.get("id", ""))
            # Keep the action target stable from day 15 through day 29, while
            # a real status update creates a fresh reminder clock.
            reminder_id = f"status|{paper_id}|{journal_id}|{status_updated_at}|{checkpoint}"
            if reminder_id in dismissed_ids:
                continue
            snooze_date = str(snoozed_until.get(reminder_id, "")).strip()
            try:
                if snooze_date and date.fromisoformat(snooze_date) >= today:
                    continue
            except ValueError:
                pass
            reminders.append(
                {
                    "id": reminder_id,
                    "paper_id": paper_id,
                    "journal_id": journal_id,
                    "paper_title": title,
                    "journal_name": journal_name,
                    "status": status,
                    "status_updated_at": status_updated_at,
                    "checkpoint": checkpoint,
                    "days": days,
                }
            )

    return sorted(reminders, key=lambda item: item["days"], reverse=True)


def due_ready_submission_reminders(
    papers: list[dict],
    dismissed_ids: set[str] | dict[str, Any] | None = None,
    today: date | None = None,
) -> list[dict]:
    """Return reminders only for journals marked ready to submit today."""
    today = today or date.today()
    dismissed_ids, _snoozed = _reminder_state(dismissed_ids)
    reminders: list[dict] = []
    for paper in papers:
        title = str(paper.get("title", "未命名论文"))
        for journal in paper.get("journals", []):
            if str(journal.get("status", "")) != "准备投稿":
                continue
            if str(journal.get("date", "")) != today.isoformat():
                continue
            journal_name = str(journal.get("name", "未填写期刊"))
            reminder_id = f"ready|{title}|{journal_name}|{today.isoformat()}"
            if reminder_id in dismissed_ids:
                continue
            reminders.append(
                {
                    "id": reminder_id,
                    "paper_title": title,
                    "journal_name": journal_name,
                    "publisher": str(journal.get("publisher", "")),
                }
            )
    return reminders
