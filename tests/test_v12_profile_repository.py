"""Atomic v12 research-profile persistence and daily organization tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import _data_root  # noqa: F401 - isolate persistence imports
from utils import file_manager
from utils.research_profile_repository import (
    apply_and_save_ai_organization,
    apply_organization_transaction,
    load_research_profile,
    save_research_profile,
    should_auto_organize,
    undo_last_organization,
)
from utils.research_profile_service import normalize_research_profile_v12


@pytest.fixture
def isolated_profile_root(tmp_path: Path):
    previous = file_manager.DATA_DIR
    file_manager._set_data_dir(tmp_path)
    try:
        yield tmp_path
    finally:
        file_manager._set_data_dir(previous)


def test_atomic_save_and_load_use_the_active_data_root(isolated_profile_root: Path) -> None:
    profile = normalize_research_profile_v12(
        {"terms": [{"canonical_en": "soil carbon", "translation_zh": "土壤碳", "weight": 88}]},
        today="2026-08-31",
    )

    save_research_profile(profile)
    loaded = load_research_profile()

    assert file_manager.RESEARCH_PROFILE_FILE.parent == isolated_profile_root
    assert loaded["terms"][0]["translation_zh"] == "土壤碳"
    assert not file_manager.RESEARCH_PROFILE_FILE.with_suffix(".json.previous").exists()
    assert not file_manager.RESEARCH_PROFILE_FILE.with_suffix(".json.tmp").exists()


def test_ai_failure_keeps_profile_bytes_unchanged(isolated_profile_root: Path) -> None:
    original = normalize_research_profile_v12(
        {"terms": [{"canonical_en": "soil carbon", "translation_zh": "土壤碳"}]},
        today="2026-08-31",
    )
    save_research_profile(original)
    before = file_manager.RESEARCH_PROFILE_FILE.read_bytes()

    with pytest.raises(RuntimeError, match="offline"):
        apply_and_save_ai_organization(
            lambda: (_ for _ in ()).throw(RuntimeError("offline")),
            today="2026-08-31",
        )

    assert file_manager.RESEARCH_PROFILE_FILE.read_bytes() == before


def test_only_latest_snapshot_can_be_undone_once() -> None:
    profile = normalize_research_profile_v12(
        {"terms": [{"canonical_en": "soil carbon", "translation_zh": "土壤碳", "weight": 70}]},
        today="2026-08-31",
    )
    proposal = {
        "terms": [
            {"canonical_en": "soil carbon", "translation_zh": "土壤碳", "weight": 85, "evidence": ["saved paper"]},
            {"canonical_en": "digital soil mapping", "translation_zh": "数字土壤制图", "weight": 76, "evidence": ["collection"]},
        ]
    }

    updated, changes = apply_organization_transaction(profile, proposal, today="2026-08-31")
    restored = undo_last_organization(updated, today="2026-08-31")
    second_undo = undo_last_organization(restored, today="2026-08-31")

    assert any(change["kind"] == "term_added" for change in changes)
    assert restored["terms"] == profile["terms"]
    assert "last_organization_snapshot" not in restored
    assert restored["auto_organization_suppressed_for_date"] == "2026-08-31"
    assert second_undo == restored
    assert should_auto_organize(restored, today="2026-08-31") is False
    assert should_auto_organize(restored, today="2026-09-01") is True


def test_new_organization_replaces_the_previous_snapshot() -> None:
    profile = normalize_research_profile_v12({}, today="2026-08-30")
    first, _ = apply_organization_transaction(
        profile,
        {"terms": [{"canonical_en": "soil carbon", "weight": 70, "evidence": ["paper"]}]},
        today="2026-08-30",
    )
    second, _ = apply_organization_transaction(
        first,
        {"terms": [{"canonical_en": "remote sensing", "weight": 60, "evidence": ["paper"]}]},
        today="2026-08-31",
    )
    restored = undo_last_organization(second, today="2026-08-31")

    assert [term["canonical_en"] for term in restored["terms"]] == ["soil carbon"]


def test_invalid_proposal_cannot_create_a_snapshot() -> None:
    profile = normalize_research_profile_v12({}, today="2026-08-31")

    with pytest.raises(ValueError, match="terms"):
        apply_organization_transaction(profile, {"terms": "not-a-list"}, today="2026-08-31")

    assert "last_organization_snapshot" not in profile


def test_locked_term_is_not_renamed_or_deleted_by_organization() -> None:
    profile = normalize_research_profile_v12(
        {"terms": [{"canonical_en": "soil carbon", "locked": True, "translation_zh": "土壤碳"}]},
        today="2026-08-31",
    )
    term_id = profile["terms"][0]["id"]

    updated, _ = apply_organization_transaction(
        profile,
        {
            "delete_term_ids": [term_id],
            "terms": [{"canonical_en": "soil carbon", "translation_zh": "更改", "weight": 20, "evidence": ["AI"]}],
        },
        today="2026-08-31",
    )

    assert updated["terms"][0]["canonical_en"] == "soil carbon"
    assert updated["terms"][0]["translation_zh"] == "土壤碳"
    assert updated["terms"][0]["weight"] == 100


def test_low_confidence_new_term_stays_pending_even_with_ai_evidence() -> None:
    current = normalize_research_profile_v12({"terms": [{"canonical_en": "soil carbon", "weight": 80}]})
    proposal = {
        "terms": [
            {
                "canonical_en": "microbial necromass",
                "translation_zh": "微生物残体",
                "weight": 64,
                "confidence": "low",
                "evidence": ["single weak signal"],
            }
        ]
    }

    updated, _changes = apply_organization_transaction(current, proposal, today="2026-08-31")

    assert [term["canonical_en"] for term in updated["terms"]] == ["soil carbon"]
    assert updated["pending_terms"][0]["canonical_en"] == "microbial necromass"
    assert updated["pending_terms"][0]["translation_zh"] == "微生物残体"
