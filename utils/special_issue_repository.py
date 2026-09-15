"""Atomic personal-state storage for special-issue recommendations."""

from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from datetime import date, datetime
from functools import wraps
from pathlib import Path
from typing import Any
from uuid import uuid4

from utils import file_manager
from utils.action_transaction import apply_json_transaction, json_write_lock, recover_json_transactions


class SpecialIssueRepositoryError(RuntimeError):
    """Raised when the formal special-issue store cannot be saved safely."""


_STATUS_PRIORITY = {"unread": 0, "read": 1, "saved": 2, "ignored": 3}
_LIST_FIELDS = ("linked_paper_ids", "created_task_ids", "submission_path_refs")
_HISTORY_FIELDS = ("deadline_history", "verification_history")
_PERSONAL_FIELDS = {
    *_LIST_FIELDS,
    "saved",
    "ignored",
    "is_read",
    "read_at",
    "last_read_at",
    "status",
    "legacy_status",
    "personal_revision",
    "journal_library_id",
    "personal_scope_note",
}


def _serialized(function):
    @wraps(function)
    def call(*args, **kwargs):
        with json_write_lock(_store_path().parent):
            recover_json_transactions(_store_path().parent)
            return function(*args, **kwargs)
    return call


def _unique_strings(values: Any) -> list[str]:
    values = values if isinstance(values, list) else []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _unique_dicts(values: Any, *, limit: int = 120) -> list[dict[str, Any]]:
    values = values if isinstance(values, list) else []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        row = deepcopy(value)
        key = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result[-limit:]


def _normalize_item(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    item_id = str(raw.get("id", "")).strip()
    if not item_id:
        return None
    result = deepcopy(raw)
    result["id"] = item_id
    status = str(raw.get("status", "unread")).strip().casefold()
    result.setdefault("legacy_status", status if status in _STATUS_PRIORITY else "unread")
    result["saved"] = bool(raw.get("saved", status == "saved"))
    result["ignored"] = bool(raw.get("ignored", status == "ignored"))
    result["read_at"] = str(raw.get("read_at", raw.get("last_read_at", "")) or "")
    result["is_read"] = bool(raw.get("is_read", status == "read") or result["read_at"])
    result["status"] = "ignored" if result["ignored"] else "saved" if result["saved"] else "read" if result["is_read"] else "unread"
    result["personal_scope_note"] = str(raw.get("personal_scope_note", "") or "").strip()[:12000]
    for field in _LIST_FIELDS:
        result[field] = _unique_strings(raw.get(field, []))
    for field in _HISTORY_FIELDS:
        result[field] = _unique_dicts(raw.get(field, []))
    for field in ("first_seen_at", "last_seen_at", "last_read_at", "last_notified_at"):
        result[field] = str(raw.get(field, "")).strip()
    return result


def _merge_items(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(left)
    for key, value in right.items():
        if key not in {*_LIST_FIELDS, *_HISTORY_FIELDS, "status", "first_seen_at", "last_seen_at"}:
            if value not in (None, "", [], {}):
                result[key] = deepcopy(value)
    for field in _LIST_FIELDS:
        result[field] = _unique_strings([*left.get(field, []), *right.get(field, [])])
    for field in _HISTORY_FIELDS:
        result[field] = _unique_dicts([*left.get(field, []), *right.get(field, [])])
    left_status = str(left.get("status", "unread"))
    right_status = str(right.get("status", "unread"))
    result["status"] = max((left_status, right_status), key=lambda value: _STATUS_PRIORITY.get(value, 0))
    for field in ("saved", "ignored", "is_read"):
        result[field] = bool(left.get(field) or right.get(field))
    result["read_at"] = max(str(left.get("read_at", "")), str(right.get("read_at", "")))
    first_values = [str(value).strip() for value in (left.get("first_seen_at"), right.get("first_seen_at")) if str(value or "").strip()]
    last_values = [str(value).strip() for value in (left.get("last_seen_at"), right.get("last_seen_at")) if str(value or "").strip()]
    result["first_seen_at"] = min(first_values) if first_values else ""
    result["last_seen_at"] = max(last_values) if last_values else ""
    return _normalize_item(result)


def normalize_special_issue_store(raw: Any) -> dict[str, Any]:
    payload = raw if isinstance(raw, dict) else {}
    items: list[dict[str, Any]] = []
    positions: dict[str, int] = {}
    for value in payload.get("items", []) if isinstance(payload.get("items"), list) else []:
        item = _normalize_item(value)
        if item is None:
            continue
        position = positions.get(item["id"])
        if position is None:
            positions[item["id"]] = len(items)
            items.append(item)
        else:
            items[position] = _merge_items(items[position], item)
    refresh_status = str(payload.get("last_refresh_status", "never")).strip() or "never"
    return {
        **deepcopy(payload),
        "version": 2,
        "revision": int(payload.get("revision", 0) or 0),
        "refresh_generation": int(payload.get("refresh_generation", 0) or 0),
        "id_aliases": deepcopy(payload.get("id_aliases", {})),
        "action_log": deepcopy(payload.get("action_log", [])),
        "notification_outbox": deepcopy(payload.get("notification_outbox", [])),
        "source_checkpoints": deepcopy(payload.get("source_checkpoints", {})),
        "items": items,
        "last_checked_at": str(payload.get("last_checked_at", "")).strip(),
        "last_refresh_status": refresh_status,
        "notification_log": _unique_dicts(payload.get("notification_log", []), limit=500),
        "reminder_log": _unique_dicts(payload.get("reminder_log", []), limit=500),
    }


def _store_path() -> Path:
    return file_manager.SPECIAL_ISSUES_FILE


@_serialized
def load_special_issue_store() -> dict[str, Any]:
    path = _store_path()
    if not path.is_file():
        return normalize_special_issue_store({})
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SpecialIssueRepositoryError(f"无法读取特刊数据: {error}") from error
    if not isinstance(payload, dict):
        raise SpecialIssueRepositoryError("特刊数据文件必须是 JSON 对象")
    return normalize_special_issue_store(payload)


def _prepare_store(store: dict[str, Any]) -> dict[str, Any]:
    current = load_special_issue_store()
    if "revision" in store and int(store["revision"]) != current["revision"]:
        raise SpecialIssueRepositoryError("特刊数据已更新，请重新载入后操作")
    result = normalize_special_issue_store(store)
    result["revision"] = current["revision"] + 1
    return result


@_serialized
def save_special_issue_store(store: dict[str, Any]) -> None:
    try:
        apply_json_transaction({_store_path(): _prepare_store(store)})
    except Exception as error:
        raise SpecialIssueRepositoryError(f"保存特刊数据失败: {error}") from error


def resolve_special_issue_id(issue_id: str, store: dict[str, Any] | None = None) -> str:
    aliases = (store if store is not None else load_special_issue_store()).get("id_aliases", {})
    wanted, seen = str(issue_id), set()
    while wanted in aliases:
        if wanted in seen:
            raise SpecialIssueRepositoryError("特刊身份别名存在循环")
        seen.add(wanted)
        wanted = str(aliases[wanted])
    return wanted


@_serialized
def begin_special_issue_refresh() -> dict[str, Any]:
    store = load_special_issue_store()
    store["refresh_generation"] += 1
    save_special_issue_store(store)
    return {"generation": store["refresh_generation"], "store": load_special_issue_store()}


def _merge_events(left, right):
    rows = {str(row.get("id")): deepcopy(row) for row in left if isinstance(row, dict)}
    for row in right:
        if not isinstance(row, dict):
            continue
        old = rows.get(str(row.get("id")), {})
        rows[str(row.get("id"))] = {**row, **old} if old.get("state") == "sent" else {**old, **row}
    return list(rows.values())


@_serialized
def commit_special_issue_refresh(patch: dict[str, Any], *, token: dict[str, Any]) -> dict[str, Any]:
    latest = load_special_issue_store()
    if int(token.get("generation", -1)) != latest["refresh_generation"]:
        return latest
    rows = {row["id"]: deepcopy(row) for row in latest["items"]}
    for incoming in patch.get("items", []):
        item_id = resolve_special_issue_id(str(incoming.get("id", "")), latest)
        if not item_id:
            continue
        old = rows.get(item_id)
        facts = {key: deepcopy(value) for key, value in incoming.items() if key not in _PERSONAL_FIELDS}
        rows[item_id] = _normalize_item({**(old or {}), **facts, "id": item_id})
    for key, value in patch.items():
        if key not in {"items", "revision", "refresh_generation", "id_aliases", "action_log", "notification_outbox", "source_checkpoints", "notification_log", "reminder_log"}:
            latest[key] = deepcopy(value)
    for field in ("notification_outbox", "notification_log", "reminder_log"):
        latest[field] = _merge_events(latest.get(field, []), patch.get(field, []))
    latest["source_checkpoints"] = {**latest.get("source_checkpoints", {}), **patch.get("source_checkpoints", {})}
    latest["items"] = list(rows.values())
    save_special_issue_store(latest)
    return load_special_issue_store()


@_serialized
def register_special_issue_alias(old_id: str, canonical_id: str) -> None:
    store = load_special_issue_store()
    old = resolve_special_issue_id(old_id, store)
    target = resolve_special_issue_id(canonical_id, store)
    if old == target:
        return
    old_item = _require_issue(store, old)
    target_item = next((row for row in store["items"] if row["id"] == target), None)
    merged = _merge_items(old_item, target_item) if target_item else deepcopy(old_item)
    merged["id"] = target
    store["items"] = [row for row in store["items"] if row["id"] not in {old, target}] + [merged]
    store["id_aliases"][old] = target
    papers = file_manager.load_papers()
    for paper in papers:
        for candidate in paper.get("journals", []):
            if candidate.get("special_issue_id") == old:
                candidate["special_issue_id"] = target
    tasks = file_manager._load_todo_tasks()
    for task in tasks:
        if task.get("special_issue_id") == old:
            task["special_issue_id"] = target
            if isinstance(task.get("source"), dict):
                task["source"]["id"] = target
    apply_json_transaction({_store_path(): _prepare_store(store), file_manager.PAPERS_FILE: papers,
                            file_manager.TODO_FILE: {"version": 2, "tasks": tasks}})


def _require_issue(store: dict[str, Any], issue_id: str) -> dict[str, Any]:
    wanted = resolve_special_issue_id(str(issue_id).strip(), store)
    issue = next((value for value in store.get("items", []) if str(value.get("id", "")).strip() == wanted), None)
    if issue is None:
        raise ValueError("找不到所选特刊")
    return issue


def _require_paper(papers: list[dict[str, Any]], paper_id: str) -> tuple[int, dict[str, Any]]:
    wanted = str(paper_id).strip()
    for index, paper in enumerate(papers):
        if str(paper.get("id", "")).strip() == wanted:
            return index, paper
    raise ValueError("找不到所选论文")


def _append_unique(values: Any, value: str) -> list[str]:
    return _unique_strings([*(values if isinstance(values, list) else []), value])


@_serialized
def associate_special_issue(issue_id: str, paper_ids: list[str]) -> None:
    """Link existing papers to one issue without touching any other file."""
    store = load_special_issue_store()
    issue = _require_issue(store, issue_id)
    known_ids = {str(value.get("id", "")).strip() for value in file_manager.load_papers()}
    requested = _unique_strings(paper_ids)
    unknown = [value for value in requested if value not in known_ids]
    if unknown:
        raise ValueError("关联论文不存在: " + "、".join(unknown))
    issue["linked_paper_ids"] = _unique_strings([*issue.get("linked_paper_ids", []), *requested])
    save_special_issue_store(store)


@_serialized
def set_special_issue_status(issue_id: str, status: str) -> None:
    """Persist one explicit triage decision without rewriting discovery data."""
    normalized_status = str(status).strip().casefold()
    if normalized_status not in {*_STATUS_PRIORITY, "unsaved", "restored"}:
        raise ValueError("不支持的特刊状态")
    changes = (
        {"saved": True}
        if normalized_status == "saved"
        else {"saved": False}
        if normalized_status == "unsaved"
        else {"ignored": True}
        if normalized_status == "ignored"
        else {"ignored": False}
        if normalized_status == "restored"
        else {"is_read": True, "read_at": datetime.now().isoformat(timespec="seconds")}
        if normalized_status == "read"
        else {"saved": False, "ignored": False}
    )
    set_special_issue_personal_state(issue_id, **changes)


@_serialized
def set_special_issue_scope_note(issue_id: str, note: str) -> None:
    store = load_special_issue_store()
    issue = _require_issue(store, issue_id)
    issue["personal_scope_note"] = str(note or "").strip()[:12000]
    issue["personal_revision"] = int(issue.get("personal_revision", 0)) + 1
    save_special_issue_store(store)


@_serialized
def set_special_issue_personal_state(issue_id: str, **changes: Any) -> None:
    if set(changes) - {"saved", "ignored", "is_read", "read_at"}:
        raise ValueError("不支持的个人状态字段")
    store = load_special_issue_store()
    issue = _require_issue(store, issue_id)
    issue.update(changes)
    issue["personal_revision"] = int(issue.get("personal_revision", 0)) + 1
    save_special_issue_store(store)


@_serialized
def mark_special_issue_notifications_sent(notification_ids: list[str]) -> None:
    """Acknowledge only notification events actually handed to the desktop tray."""
    wanted = set(_unique_strings(notification_ids))
    if not wanted:
        return
    store = load_special_issue_store()
    sent_at = datetime.now().isoformat(timespec="seconds")
    notification_log = [dict(value) for value in store.get("notification_log", []) if isinstance(value, dict)]
    reminder_log = [dict(value) for value in store.get("reminder_log", []) if isinstance(value, dict)]
    logged_ids = {str(value.get("id", "")) for value in notification_log}
    reminder_ids = {str(value.get("id", "")) for value in reminder_log}
    for event in store.get("notification_outbox", []):
        if not isinstance(event, dict) or str(event.get("id", "")) not in wanted:
            continue
        event["state"] = "sent"
        event["sent_at"] = sent_at
        event_id = str(event.get("id", ""))
        record = {**event, "notified_at": sent_at}
        if event_id not in logged_ids:
            notification_log.append(record)
            logged_ids.add(event_id)
        if event.get("kind") == "deadline" and event_id not in reminder_ids:
            reminder_log.append(record)
            reminder_ids.add(event_id)
    store["notification_log"] = notification_log[-500:]
    store["reminder_log"] = reminder_log[-500:]
    save_special_issue_store(store)


def _log_action(store: dict[str, Any], key: str, result: Any) -> None:
    store["action_log"] = [row for row in store.get("action_log", []) if row.get("id") != key]
    store["action_log"].append({"id": key, "result": result, "at": datetime.now().isoformat(timespec="seconds")})


def _matching_library_journal(issue: dict[str, Any], journals: list[dict[str, Any]]) -> dict[str, Any] | None:
    from utils.journal_selection_service import canonical_text

    wanted_issns = {
        canonical_text(value)
        for value in (issue.get("issns", []) if isinstance(issue.get("issns"), list) else [])
        if canonical_text(value)
    }
    wanted_name = canonical_text(issue.get("journal", ""))
    for journal in journals:
        if wanted_issns and canonical_text(journal.get("issn", "")) in wanted_issns:
            return journal
    for journal in journals:
        if wanted_name and canonical_text(journal.get("name", "")) == wanted_name:
            return journal
    return None


def _require_issue_actionable(issue: dict[str, Any], *, today: date, action: str, allow_conflict: bool = False) -> None:
    verification = str(issue.get("verification_status", "")).strip().casefold()
    call_status = str(issue.get("call_status", "")).strip().casefold()
    deadline = str(issue.get("deadline", "")).strip()[:10]
    if verification in {"closed", "expired"} or call_status in {"closed", "expired"}:
        raise ValueError(f"该特刊已关闭或过期，不能{action}")
    if verification == "conflict" and not allow_conflict:
        raise ValueError(f"该特刊身份存在冲突，不能{action}")
    try:
        if deadline and date.fromisoformat(deadline) < today:
            raise ValueError(f"该特刊已过截止日期，不能{action}")
    except ValueError as error:
        if str(error).startswith("该特刊"):
            raise


def _paper_rejects_issue_journal(issue: dict[str, Any], paper_id: str) -> bool:
    from utils.journal_selection_service import canonical_journal_name

    wanted = canonical_journal_name(issue.get("journal", ""))
    if not wanted:
        return False
    blocked = {
        canonical_journal_name(value)
        for value in file_manager.selection_excluded_journal_names(paper_id)
        if canonical_journal_name(value)
    }
    for entry in file_manager.load_rejection_archive():
        if str(entry.get("paper_id", "")).strip() != str(paper_id).strip():
            continue
        name = canonical_journal_name(entry.get("journal_name", ""))
        if name:
            blocked.add(name)
    return wanted in blocked


@_serialized
def add_special_issue_journal_to_library(
    issue_id: str,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Import a special issue's journal without creating a submission path."""
    from utils.action_transaction import apply_json_transaction
    from utils.journal_selection_service import special_issue_to_library_journal

    day = today or date.today()
    store = load_special_issue_store()
    issue = _require_issue(store, issue_id)
    journals = file_manager.load_journal_library()
    journal = _matching_library_journal(issue, journals)
    created = journal is None
    if journal is None:
        journal = file_manager.normalize_library_journal(
            special_issue_to_library_journal(issue, today=day.isoformat())
        )
        journals.append(journal)
    issue["journal_library_id"] = str(journal.get("id", ""))
    _log_action(store, f"library:{issue['id']}", issue["journal_library_id"])
    apply_json_transaction(
        {
            file_manager.SPECIAL_ISSUES_FILE: _prepare_store(store),
            file_manager.JOURNALS_FILE: [file_manager.normalize_library_journal(value) for value in journals],
        }
    )
    return {"journal_id": str(journal.get("id", "")), "created": created}


@_serialized
def add_special_issue_to_submission_path(
    issue_id: str,
    paper_id: str,
    *,
    today: date | None = None,
) -> dict[str, str]:
    """Quick-import the journal and create one issue-specific paper candidate."""
    from utils.action_transaction import apply_json_transaction
    from utils.journal_selection_service import append_special_issue_to_submission_path, special_issue_to_library_journal

    day = today or date.today()
    store = load_special_issue_store()
    issue = _require_issue(store, issue_id)
    issue_id = issue["id"]
    _require_issue_actionable(issue, today=day, action="加入投稿路径")
    papers = file_manager.load_papers()
    paper_index, paper = _require_paper(papers, paper_id)
    if _paper_rejects_issue_journal(issue, paper_id):
        raise ValueError("该论文已拒稿或已排除此期刊，不能自动加入投稿路径")
    journals = file_manager.load_journal_library()
    journal = _matching_library_journal(issue, journals)
    if journal is None:
        journal = file_manager.normalize_library_journal(special_issue_to_library_journal(issue, today=day.isoformat()))
        journals.append(journal)
    updated_paper = append_special_issue_to_submission_path(paper, journal, issue, today=day.isoformat())
    papers[paper_index] = file_manager.normalize_paper(updated_paper)
    candidate = next(
        value
        for value in papers[paper_index].get("journals", [])
        if str(value.get("special_issue_id", "")).strip() == str(issue_id).strip()
    )
    if file_manager.find_future_paper_date_issues(papers, today=day):
        raise ValueError("投稿路径包含未来的状态日期，未写入数据")
    reference = f"{paper_id}:{candidate['id']}"
    issue["linked_paper_ids"] = _append_unique(issue.get("linked_paper_ids", []), str(paper_id))
    issue["submission_path_refs"] = _append_unique(issue.get("submission_path_refs", []), reference)
    issue["journal_library_id"] = str(journal.get("id", ""))
    _log_action(store, f"path:{issue_id}:{paper_id}", candidate["id"])
    normalized_store = _prepare_store(store)
    normalized_journals = [file_manager.normalize_library_journal(value) for value in journals]
    apply_json_transaction(
        {
            file_manager.SPECIAL_ISSUES_FILE: normalized_store,
            file_manager.PAPERS_FILE: papers,
            file_manager.JOURNALS_FILE: normalized_journals,
        }
    )
    return {"journal_id": str(journal.get("id", "")), "path_id": str(candidate.get("id", ""))}


@_serialized
def create_special_issue_preparation_task(
    issue_id: str,
    paper_id: str,
    *,
    today: date | None = None,
) -> str:
    """Create a standalone due-date task and link it to the issue and paper."""
    from utils.action_transaction import apply_json_transaction

    day = today or date.today()
    store = load_special_issue_store()
    issue = _require_issue(store, issue_id)
    issue_id = issue["id"]
    _require_issue_actionable(issue, today=day, action="创建准备任务", allow_conflict=True)
    papers = file_manager.load_papers()
    _paper_index, paper = _require_paper(papers, paper_id)
    tasks = file_manager._load_todo_tasks()
    for task in tasks:
        if str(task.get("special_issue_id", "")) == str(issue_id) and str(task.get("paper_id", "")) == str(paper_id):
            existing_id = str(task.get("id", ""))
            issue["linked_paper_ids"] = _append_unique(issue.get("linked_paper_ids", []), str(paper_id))
            issue["created_task_ids"] = _append_unique(issue.get("created_task_ids", []), existing_id)
            save_special_issue_store(store)
            return existing_id
    deadline = str(issue.get("deadline", "")).strip()[:10]
    try:
        deadline_day = date.fromisoformat(deadline)
    except ValueError as error:
        raise ValueError("特刊截止日期无效，不能创建准备任务") from error
    if deadline_day < day:
        raise ValueError("特刊已过截止日期，不能创建准备任务")
    task_id = uuid4().hex
    title = str(issue.get("title", "未命名特刊")).strip() or "未命名特刊"
    task = file_manager._normalize_todo(
        {
            "id": task_id,
            "title": f"准备特刊投稿：{title}（{str(paper.get('title', '未命名论文')).strip()}）",
            "start_date": day.isoformat(),
            "end_date": deadline,
            "repeat_daily": False,
            "done_dates": [],
            "quadrant": "important",
            "sort_order": len(tasks),
            "paper_id": str(paper_id),
            "special_issue_id": str(issue_id),
            "deadline": deadline,
            "source": {"kind": "special_issue", "id": str(issue_id), "label": title},
        },
        day.isoformat(),
    )
    tasks.append(task)
    task["deadline_mode"] = "follow_issue"
    _log_action(store, f"task:{issue_id}:{paper_id}", task_id)
    issue["linked_paper_ids"] = _append_unique(issue.get("linked_paper_ids", []), str(paper_id))
    issue["created_task_ids"] = _append_unique(issue.get("created_task_ids", []), task_id)
    apply_json_transaction(
        {
            file_manager.SPECIAL_ISSUES_FILE: _prepare_store(store),
            file_manager.TODO_FILE: {"version": 2, "tasks": tasks},
        }
    )
    return task_id
