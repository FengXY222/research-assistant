"""Data protection regressions for the isolated Stage 1 implementation."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import pytest

from tests import _data_root  # noqa: F401
from utils import file_manager
from utils import special_issue_repository as repository


@pytest.fixture()
def isolated_root(tmp_path):
    original = file_manager.DATA_DIR
    file_manager._set_data_dir(tmp_path)
    file_manager.PAPERS_FILE.write_text(
        json.dumps([{"id": f"p{index}", "title": f"Paper {index}", "journals": []} for index in range(20)]),
        encoding="utf-8",
    )
    file_manager.JOURNALS_FILE.write_text("[]", encoding="utf-8")
    file_manager.TODO_FILE.write_text('{"version": 2, "tasks": []}', encoding="utf-8")
    repository.save_special_issue_store({"items": [{"id": "si-1", "title": "Soil carbon", "journal": "Geoderma", "deadline": "2027-06-30"}]})
    try:
        yield tmp_path
    finally:
        file_manager._set_data_dir(original)


def test_migration_keeps_original_status_and_independent_read_and_saved():
    original = {"version": 1, "future_field": {"keep": True}, "items": [
        {"id": "a", "status": "saved", "last_read_at": "2026-08-01T12:00:00"},
        {"id": "b", "status": "read"},
        {"id": "c", "status": "ignored"},
    ]}
    result = repository.normalize_special_issue_store(original)
    assert result["future_field"] == {"keep": True}
    assert result["items"][0]["saved"] is True
    assert result["items"][0]["read_at"] == "2026-08-01T12:00:00"
    assert result["items"][1]["is_read"] is True
    assert result["items"][1]["read_at"] == ""
    assert result["items"][2]["ignored"] is True
    assert result["items"][0]["legacy_status"] == "saved"
    assert repository.normalize_special_issue_store(result) == result
    assert original["version"] == 1


def test_independent_false_fields_win_over_legacy_projection():
    item = repository.normalize_special_issue_store({"items": [{
        "id": "a", "status": "saved", "saved": False, "ignored": False,
        "read_at": "2026-08-01T12:00:00",
    }]})["items"][0]
    assert item["status"] == "read"
    assert item["saved"] is False


def test_read_does_not_cancel_saved_and_restore_keeps_read(isolated_root):
    repository.set_special_issue_status("si-1", "saved")
    repository.set_special_issue_status("si-1", "read")
    item = repository.load_special_issue_store()["items"][0]
    assert item["saved"] is True and item["read_at"]
    repository.set_special_issue_status("si-1", "ignored")
    repository.set_special_issue_personal_state("si-1", ignored=False)
    item = repository.load_special_issue_store()["items"][0]
    assert item["status"] == "saved" and item["is_read"] is True
    repository.set_special_issue_status("si-1", "unsaved")
    item = repository.load_special_issue_store()["items"][0]
    assert item["saved"] is False and item["is_read"] is True
    repository.set_special_issue_status("si-1", "ignored")
    repository.set_special_issue_status("si-1", "restored")
    item = repository.load_special_issue_store()["items"][0]
    assert item["ignored"] is False and item["is_read"] is True


def test_refresh_preserves_last_user_decision_links_and_created_task(isolated_root):
    token = repository.begin_special_issue_refresh()
    repository.set_special_issue_status("si-1", "saved")
    repository.set_special_issue_personal_state("si-1", saved=False, ignored=True)
    repository.associate_special_issue("si-1", ["p1"])
    task_id = repository.create_special_issue_preparation_task("si-1", "p1")
    stale = token["store"]
    stale["items"][0]["deadline"] = "2027-09-30"
    result = repository.commit_special_issue_refresh(stale, token=token)
    item = result["items"][0]
    assert item["saved"] is False and item["ignored"] is True
    assert item["linked_paper_ids"] == ["p1"]
    assert item["created_task_ids"] == [task_id]
    assert item["deadline"] == "2027-09-30"
    assert repository.load_special_issue_store()["items"][0] == item


def test_personal_scope_note_survives_discovery_refresh(isolated_root):
    token = repository.begin_special_issue_refresh()
    repository.set_special_issue_scope_note("si-1", "我认为这个特刊更关注土壤碳制图方法。")
    incoming = token["store"]
    incoming["items"][0]["scope_text"] = "Updated official scope"

    result = repository.commit_special_issue_refresh(incoming, token=token)

    assert result["items"][0]["scope_text"] == "Updated official scope"
    assert result["items"][0]["personal_scope_note"] == "我认为这个特刊更关注土壤碳制图方法。"


def test_older_refresh_cannot_replace_newer_facts_or_checkpoint(isolated_root):
    old = repository.begin_special_issue_refresh()
    new = repository.begin_special_issue_refresh()
    repository.commit_special_issue_refresh({"items": [{"id": "si-1", "deadline": "2028-01-31"}], "source_checkpoints": {"a": {"status": "success"}}}, token=new)
    result = repository.commit_special_issue_refresh({"items": [{"id": "si-1", "deadline": "2027-01-31"}], "source_checkpoints": {"a": {"status": "failed"}}}, token=old)
    assert result["items"][0]["deadline"] == "2028-01-31"
    assert result["source_checkpoints"]["a"]["status"] == "success"


def test_stale_whole_store_save_is_rejected_instead_of_losing_user_changes(isolated_root):
    stale = repository.load_special_issue_store()
    repository.set_special_issue_status("si-1", "saved")
    with pytest.raises(repository.SpecialIssueRepositoryError):
        repository.save_special_issue_store(stale)
    assert repository.load_special_issue_store()["items"][0]["saved"] is True


def test_alias_survives_reload_and_all_actions_resolve_original_id(isolated_root):
    repository.associate_special_issue("si-1", ["p1"])
    original_task = repository.create_special_issue_preparation_task("si-1", "p1")
    repository.register_special_issue_alias("si-1", "publisher-123")
    store = repository.load_special_issue_store()
    assert store["id_aliases"] == {"si-1": "publisher-123"}
    assert store["items"][0]["id"] == "publisher-123"
    repository.set_special_issue_status("si-1", "saved")
    assert repository.create_special_issue_preparation_task("publisher-123", "p1") == original_task
    assert repository.resolve_special_issue_id("si-1") == "publisher-123"
    assert repository.load_special_issue_store()["items"][0]["saved"] is True


def test_alias_merge_retains_personal_refs_from_both_ids(isolated_root):
    store = repository.load_special_issue_store()
    store["items"].append({"id": "other", "created_task_ids": ["t2"], "status": "saved"})
    repository.save_special_issue_store(store)
    repository.associate_special_issue("si-1", ["p1"])
    repository.register_special_issue_alias("si-1", "other")
    store = repository.load_special_issue_store()
    assert len(store["items"]) == 1
    assert store["items"][0]["linked_paper_ids"] == ["p1"]
    assert store["items"][0]["created_task_ids"] == ["t2"]


def test_concurrent_associations_keep_every_change(isolated_root):
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: repository.associate_special_issue("si-1", [f"p{index}"]), range(20)))
    assert set(repository.load_special_issue_store()["items"][0]["linked_paper_ids"]) == {f"p{index}" for index in range(20)}


def test_concurrent_duplicate_actions_create_one_task_and_one_path(isolated_root):
    with ThreadPoolExecutor(max_workers=5) as pool:
        task_ids = list(pool.map(lambda _: repository.create_special_issue_preparation_task("si-1", "p1"), range(5)))
        paths = list(pool.map(lambda _: repository.add_special_issue_to_submission_path("si-1", "p1"), range(5)))
    assert len(set(task_ids)) == 1
    assert len({value["path_id"] for value in paths}) == 1
    assert len(file_manager._load_todo_tasks()) == 1
    assert len(next(p for p in file_manager.load_papers() if p["id"] == "p1")["journals"]) == 1
    assert repository.load_special_issue_store()["action_log"]


def test_refresh_keeps_history_outside_processing_budget(isolated_root):
    from utils.special_issue_service import refresh_special_issues
    from utils.evidence_cache import EvidenceCache

    store = repository.load_special_issue_store()
    store["items"] = [{"id": f"old-{i}", "title": f"Soil carbon {i}", "official_url": f"https://example.org/{i}", "deadline": "2027-06-30", "linked_paper_ids": ["p1"]} for i in range(8)]
    repository.save_special_issue_store(store)
    checked = []

    def verify(item, **_kwargs):
        checked.append(item["id"])
        return item

    result = refresh_special_issues(sources=[], now=datetime(2026, 9, 6), journal_library=[], research_profile={}, papers=[], cache=EvidenceCache(isolated_root / "cache.sqlite"), verifier=verify, enricher=lambda item, *_args, **_kwargs: item, easyscholar_ready=False, candidate_limit=2, translate_scopes=False)
    assert len(checked) == 2
    assert len(result["store"]["items"]) == 8
    assert len(repository.load_special_issue_store()["items"]) == 8


def test_notification_and_source_fields_survive_background_merge(isolated_root):
    token = repository.begin_special_issue_refresh()
    store = repository.load_special_issue_store()
    store["notification_outbox"] = [{"id": "n1", "state": "sent"}, {"id": "n2", "state": "pending"}]
    store["source_checkpoints"] = {"second": {"status": "success"}}
    repository.save_special_issue_store(store)
    result = repository.commit_special_issue_refresh({"items": [], "notification_outbox": [{"id": "n1", "state": "pending"}, {"id": "n3", "state": "pending"}], "source_checkpoints": {"first": {"status": "success"}}}, token=token)
    assert {event["id"]: event["state"] for event in result["notification_outbox"]} == {"n1": "sent", "n2": "pending", "n3": "pending"}
    assert set(result["source_checkpoints"]) == {"first", "second"}


def test_only_delivered_outbox_events_are_marked_sent(isolated_root):
    from utils.special_issue_repository import mark_special_issue_notifications_sent

    store = repository.load_special_issue_store()
    store["notification_outbox"] = [
        {"id": "n1", "kind": "new_high_match", "state": "pending"},
        {"id": "n2", "kind": "deadline", "state": "pending"},
    ]
    repository.save_special_issue_store(store)

    mark_special_issue_notifications_sent(["n1"])
    result = repository.load_special_issue_store()
    states = {value["id"]: value["state"] for value in result["notification_outbox"]}
    assert states == {"n1": "sent", "n2": "pending"}
    assert {value["id"] for value in result["notification_log"]} == {"n1"}
    assert result["reminder_log"] == []

    mark_special_issue_notifications_sent(["n2"])
    result = repository.load_special_issue_store()
    assert {value["id"] for value in result["reminder_log"]} == {"n2"}
