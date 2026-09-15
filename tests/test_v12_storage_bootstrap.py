"""v12 stable UserData selection and byte-preserving migration tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from utils.storage_bootstrap import (
    StorageBootstrapError,
    build_data_manifest,
    copy_data_store_verified,
    default_user_data_dir,
    prepare_v12_data_root,
)


def test_default_root_is_outside_programs(tmp_path: Path) -> None:
    assert default_user_data_dir(tmp_path) == tmp_path / "科研助手" / "UserData"


def test_verified_copy_preserves_hashes_and_leaves_source(tmp_path: Path) -> None:
    source = tmp_path / "Programs" / "科研助手" / "data"
    destination = tmp_path / "科研助手" / "UserData"
    source.mkdir(parents=True)
    (source / "papers.json").write_text('[{"id":"p1"}]', encoding="utf-8")
    (source / "attachments").mkdir()
    (source / "attachments" / "note.txt").write_text("keep", encoding="utf-8")

    result = copy_data_store_verified(source, destination)

    assert (source / "papers.json").read_bytes() == b'[{"id":"p1"}]'
    assert (destination / "attachments" / "note.txt").read_text(encoding="utf-8") == "keep"
    assert result["files"]["papers.json"]["source_sha256"] == result["files"]["papers.json"]["destination_sha256"]


def test_invalid_json_rolls_back_temporary_destination(tmp_path: Path) -> None:
    source = tmp_path / "legacy"
    destination = tmp_path / "科研助手" / "UserData"
    source.mkdir()
    (source / "papers.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(StorageBootstrapError, match="papers.json"):
        copy_data_store_verified(source, destination)

    assert not destination.exists()
    assert (source / "papers.json").read_text(encoding="utf-8") == "{broken"
    assert not list(destination.parent.glob("UserData.migrating-*"))


def test_prepare_frozen_root_copies_richest_legacy_store_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESEARCH_ASSISTANT_DATA_DIR", raising=False)
    local = tmp_path / "Local"
    app_root = local / "Programs" / "科研助手"
    bundle_data = app_root / "data"
    older_data = local / "科研助手" / "data"
    bundle_data.mkdir(parents=True)
    older_data.mkdir(parents=True)
    (bundle_data / "papers.json").write_text('[{"id":"smoke"}]', encoding="utf-8")
    (older_data / "papers.json").write_text('[{"id":"p1"},{"id":"p2"}]', encoding="utf-8")

    selected = prepare_v12_data_root(frozen=True, app_root=app_root, local_app_data=local)

    target = default_user_data_dir(local)
    assert selected == target
    assert json.loads((target / "papers.json").read_text(encoding="utf-8")) == [{"id": "p1"}, {"id": "p2"}]
    assert (older_data / "papers.json").exists()
    stored = json.loads((local / "科研助手" / "storage_location.json").read_text(encoding="utf-8"))
    assert Path(stored["data_directory"]) == target


def test_existing_target_is_never_overwritten_by_a_richer_legacy_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RESEARCH_ASSISTANT_DATA_DIR", raising=False)
    local = tmp_path / "Local"
    target = default_user_data_dir(local)
    legacy = local / "Programs" / "科研助手" / "data"
    target.mkdir(parents=True)
    legacy.mkdir(parents=True)
    (target / "papers.json").write_text('[{"id":"official"}]', encoding="utf-8")
    (legacy / "papers.json").write_text('[{"id":"a"},{"id":"b"},{"id":"c"}]', encoding="utf-8")

    selected = prepare_v12_data_root(frozen=True, app_root=legacy.parent, local_app_data=local)

    assert selected == target
    assert json.loads((target / "papers.json").read_text(encoding="utf-8")) == [{"id": "official"}]


def test_environment_override_has_highest_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    explicit = tmp_path / "explicit"
    monkeypatch.setenv("RESEARCH_ASSISTANT_DATA_DIR", str(explicit))

    assert prepare_v12_data_root(frozen=True, app_root=tmp_path / "app", local_app_data=tmp_path / "local") == explicit
    assert explicit.is_dir()


def test_manifest_is_deterministic_and_validates_json(tmp_path: Path) -> None:
    (tmp_path / "b.json").write_text('{"b":2}', encoding="utf-8")
    (tmp_path / "a.json").write_text('[1]', encoding="utf-8")

    first = build_data_manifest(tmp_path)
    second = build_data_manifest(tmp_path)

    assert first == second
    assert list(first["files"]) == ["a.json", "b.json"]
