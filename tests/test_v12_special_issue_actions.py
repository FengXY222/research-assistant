"""Transactional special-issue action boundaries for v12."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

import pytest

from tests import _data_root  # noqa: F401 - isolate persistence before imports
from utils import file_manager


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _seed(root: Path) -> None:
    file_manager._set_data_dir(root)
    _write(
        file_manager.SPECIAL_ISSUES_FILE,
        {
            "version": 1,
            "items": [
                {
                    "id": "si-1",
                    "title": "Digital soil carbon mapping",
                    "journal": "Geoderma",
                    "publisher": "Elsevier",
                    "issns": ["0016-7061"],
                    "official_url": "https://example.org/call",
                    "deadline": "2027-06-30",
                    "status": "unread",
                    "linked_paper_ids": [],
                }
            ],
        },
    )
    _write(
        file_manager.PAPERS_FILE,
        [{"id": "p1", "title": "Mapping soil organic carbon", "keywords": [], "journals": []}],
    )
    _write(file_manager.JOURNALS_FILE, [])
    _write(file_manager.TODO_FILE, {"version": 2, "tasks": []})


@pytest.fixture()
def action_root(tmp_path: Path):
    original = file_manager.DATA_DIR
    _seed(tmp_path)
    try:
        yield tmp_path
    finally:
        file_manager._set_data_dir(original)


def test_association_changes_only_special_issue_store(action_root: Path) -> None:
    from utils.special_issue_repository import associate_special_issue, load_special_issue_store

    before = {
        path: _sha(path)
        for path in (file_manager.PAPERS_FILE, file_manager.JOURNALS_FILE, file_manager.TODO_FILE)
    }
    associate_special_issue("si-1", ["p1"])

    assert load_special_issue_store()["items"][0]["linked_paper_ids"] == ["p1"]
    assert {path: _sha(path) for path in before} == before


def test_path_action_imports_missing_journal_and_keeps_issue_metadata(action_root: Path) -> None:
    from utils.special_issue_repository import add_special_issue_to_submission_path, load_special_issue_store

    result = add_special_issue_to_submission_path("si-1", "p1", today=date(2026, 8, 31))

    journals = file_manager.load_journal_library()
    papers = file_manager.load_papers()
    candidate = papers[0]["journals"][0]
    issue = load_special_issue_store()["items"][0]
    imported = next(value for value in journals if value["name"] == "Geoderma")
    assert result["journal_id"] == imported["id"]
    assert candidate["special_issue_id"] == "si-1"
    assert candidate["special_issue_title"] == "Digital soil carbon mapping"
    assert candidate["special_issue_deadline"] == "2027-06-30"
    assert candidate["status"] == "准备投稿"
    assert issue["linked_paper_ids"] == ["p1"]
    assert issue["submission_path_refs"] == [f"p1:{candidate['id']}"]
    assert json.loads(file_manager.TODO_FILE.read_text(encoding="utf-8"))["tasks"] == []


def test_task_action_links_issue_and_paper_without_touching_path_or_library(action_root: Path) -> None:
    from utils.special_issue_repository import create_special_issue_preparation_task, load_special_issue_store

    before_papers = _sha(file_manager.PAPERS_FILE)
    before_journals = _sha(file_manager.JOURNALS_FILE)
    task_id = create_special_issue_preparation_task("si-1", "p1", today=date(2026, 8, 31))

    task = file_manager._load_todo_tasks()[0]
    issue = load_special_issue_store()["items"][0]
    assert task["id"] == task_id
    assert task["special_issue_id"] == "si-1"
    assert task["paper_id"] == "p1"
    assert task["deadline"] == "2027-06-30"
    assert task["deadline_mode"] == "follow_issue"
    assert task["source"] == {"kind": "special_issue", "id": "si-1", "label": "Digital soil carbon mapping"}
    assert issue["linked_paper_ids"] == ["p1"]
    assert issue["created_task_ids"] == [task_id]
    assert _sha(file_manager.PAPERS_FILE) == before_papers
    assert _sha(file_manager.JOURNALS_FILE) == before_journals


@pytest.mark.parametrize("state", ["closed", "expired", "conflict"])
def test_closed_expired_or_conflicted_issue_cannot_create_submission_path(action_root: Path, state: str) -> None:
    from utils.special_issue_repository import add_special_issue_to_submission_path, load_special_issue_store, save_special_issue_store

    store = load_special_issue_store()
    store["items"][0]["verification_status"] = state
    store["items"][0]["call_status"] = "closed" if state != "conflict" else "unknown"
    save_special_issue_store(store)

    with pytest.raises(ValueError, match="不能加入投稿路径"):
        add_special_issue_to_submission_path("si-1", "p1", today=date(2026, 8, 31))
    assert file_manager.load_papers()[0]["journals"] == []


def test_rejected_or_manually_excluded_journal_cannot_return_to_same_paper_path(action_root: Path) -> None:
    from utils.special_issue_repository import add_special_issue_to_submission_path

    _write(
        file_manager.REJECTION_ARCHIVE_FILE,
        [{"paper_id": "p1", "paper_title": "Mapping soil organic carbon", "journal_name": "Geoderma"}],
    )
    with pytest.raises(ValueError, match="已拒稿或已排除"):
        add_special_issue_to_submission_path("si-1", "p1", today=date(2026, 8, 31))

    _write(file_manager.REJECTION_ARCHIVE_FILE, [])
    file_manager.set_journal_selection_feedback("p1", "geoderma", "不适合", journal_name="Geoderma")
    with pytest.raises(ValueError, match="已拒稿或已排除"):
        add_special_issue_to_submission_path("si-1", "p1", today=date(2026, 8, 31))


@pytest.mark.parametrize("state", ["closed", "expired"])
def test_closed_or_expired_issue_cannot_create_new_preparation_task(action_root: Path, state: str) -> None:
    from utils.special_issue_repository import create_special_issue_preparation_task, load_special_issue_store, save_special_issue_store

    store = load_special_issue_store()
    store["items"][0]["verification_status"] = state
    store["items"][0]["call_status"] = "closed"
    save_special_issue_store(store)

    with pytest.raises(ValueError, match="不能创建准备任务"):
        create_special_issue_preparation_task("si-1", "p1", today=date(2026, 8, 31))
    assert file_manager._load_todo_tasks() == []


def test_second_replace_failure_restores_every_original_byte(action_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from utils import action_transaction

    paths = (file_manager.PAPERS_FILE, file_manager.JOURNALS_FILE)
    originals = {path: path.read_bytes() for path in paths}
    real_replace = action_transaction.os.replace
    calls = 0

    def fail_second(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second-file failure")
        return real_replace(source, target)

    monkeypatch.setattr(action_transaction.os, "replace", fail_second)
    with pytest.raises(action_transaction.JsonTransactionError):
        action_transaction.apply_json_transaction(
            {
                paths[0]: [{"id": "changed"}],
                paths[1]: [{"id": "changed-journal", "name": "Changed"}],
            }
        )

    assert {path: path.read_bytes() for path in paths} == originals
    assert not list(action_root.glob("*.v12txn-*"))


def test_profile_learning_failure_does_not_invalidate_completed_action(
    action_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ui.main_window import MainWindow
    from utils import research_profile_repository

    def fail_to_save_profile(_profile: dict) -> None:
        raise OSError("profile unavailable")

    monkeypatch.setattr(research_profile_repository, "save_research_profile", fail_to_save_profile)

    MainWindow._record_special_issue_signal("si-1", "favorite")
