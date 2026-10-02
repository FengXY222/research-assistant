"""Personal-state storage contracts for v12 special-issue recommendations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests import _data_root  # noqa: F401 - isolate persistence before imports
from utils import file_manager


def test_empty_store_has_a_stable_versioned_shape() -> None:
    from utils.special_issue_repository import normalize_special_issue_store

    assert normalize_special_issue_store(None) == {
        "version": 2,
        "revision": 0,
        "refresh_generation": 0,
        "id_aliases": {},
        "action_log": [],
        "notification_outbox": [],
        "source_checkpoints": {},
        "items": [],
        "last_checked_at": "",
        "last_refresh_status": "never",
        "notification_log": [],
        "reminder_log": [],
    }


def test_duplicate_personal_rows_merge_without_losing_links_or_history() -> None:
    from utils.special_issue_repository import normalize_special_issue_store

    store = normalize_special_issue_store(
        {
            "items": [
                {
                    "id": "si-1",
                    "title": "Special issue",
                    "linked_paper_ids": ["p1"],
                    "deadline_history": [{"deadline": "2026-12-01"}],
                },
                {
                    "id": "si-1",
                    "linked_paper_ids": ["p2", "p1"],
                    "created_task_ids": ["t1"],
                    "status": "saved",
                    "deadline_history": [{"deadline": "2027-01-01"}],
                },
            ]
        }
    )

    assert len(store["items"]) == 1
    item = store["items"][0]
    assert item["status"] == "saved"
    assert item["linked_paper_ids"] == ["p1", "p2"]
    assert item["created_task_ids"] == ["t1"]
    assert [row["deadline"] for row in item["deadline_history"]] == ["2026-12-01", "2027-01-01"]


def test_ignored_status_remains_explicit_during_duplicate_merge() -> None:
    from utils.special_issue_repository import normalize_special_issue_store

    store = normalize_special_issue_store(
        {"items": [{"id": "si-1", "status": "ignored"}, {"id": "si-1", "status": "saved"}]}
    )
    assert store["items"][0]["status"] == "ignored"


def test_save_load_roundtrip_and_data_root_switch(tmp_path: Path) -> None:
    from utils.special_issue_repository import load_special_issue_store, save_special_issue_store

    original = file_manager.DATA_DIR
    try:
        file_manager._set_data_dir(tmp_path)
        assert file_manager.SPECIAL_ISSUES_FILE == tmp_path / "special_issues.json"
        save_special_issue_store(
            {
                "items": [
                    {
                        "id": "si-1",
                        "title": "Topical collection",
                        "status": "saved",
                        "linked_paper_ids": ["p1"],
                    }
                ]
            }
        )

        payload = load_special_issue_store()
        assert payload["items"][0]["title"] == "Topical collection"
        assert (tmp_path / "research_assistant.sqlite").is_file()
        assert "research_assistant.sqlite" in file_manager.BACKUP_FILE_NAMES
        assert "research_intelligence.sqlite" not in file_manager.BACKUP_FILE_NAMES
    finally:
        file_manager._set_data_dir(original)


def test_failed_database_commit_preserves_original_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from utils import special_issue_repository as repository
    from utils.app_data_store import AppDataStore

    original_root = file_manager.DATA_DIR
    try:
        file_manager._set_data_dir(tmp_path)
        repository.save_special_issue_store({"items": [{"id": "si-old", "status": "saved"}]})
        original = repository.load_special_issue_store()

        monkeypatch.setattr(
            AppDataStore,
            "save_special_issues",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("blocked")),
        )
        with pytest.raises(repository.SpecialIssueRepositoryError):
            repository.save_special_issue_store({"items": [{"id": "si-new", "status": "saved"}]})

        assert repository.load_special_issue_store() == original
    finally:
        file_manager._set_data_dir(original_root)
