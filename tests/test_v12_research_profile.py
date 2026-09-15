"""v12 research-profile migration, permissions and permanent-block tests."""

from __future__ import annotations

from tests import _data_root  # noqa: F401 - isolate file_manager imports
from utils.file_manager import _normalize_frontier_profile
from utils.research_profile_service import (
    add_pending_term,
    delete_excluded_term,
    normalize_research_profile_v12,
    reject_pending_term,
    remove_term,
    update_term_fields,
    upsert_excluded_term,
)


def test_removed_legacy_term_never_resurrects_after_twenty_normalizations() -> None:
    profile = normalize_research_profile_v12({"primary_keywords": ["soil carbon"]}, today="2026-08-31")
    term_id = profile["terms"][0]["id"]

    profile = remove_term(profile, term_id, today="2026-08-31")
    for _ in range(20):
        profile = normalize_research_profile_v12(profile, today="2026-08-31")

    assert profile["terms"] == []
    assert profile["legacy_profile_migrated_at"] == "2026-08-31"
    assert profile["primary_keywords"] == ["soil carbon"]
    assert profile["blocked_terms"][0]["canonical_key"] == "soil carbon"


def test_removed_alias_is_blocked_from_pending_and_active_reintroduction() -> None:
    profile = normalize_research_profile_v12(
        {"terms": [{"canonical_en": "soil organic carbon", "aliases": ["SOC"], "weight": 80}]},
        today="2026-08-31",
    )
    profile = remove_term(profile, profile["terms"][0]["id"], today="2026-08-31")

    profile = add_pending_term(profile, "SOC", source="ai", today="2026-08-31")
    normalized = normalize_research_profile_v12(
        {**profile, "terms": [{"canonical_en": "SOC", "weight": 90}]},
        today="2026-08-31",
    )

    assert normalized["terms"] == []
    assert normalized["pending_terms"] == []
    assert set(normalized["blocked_terms"][0]["alias_keys"]) >= {"soc", "soil organic carbon"}


def test_term_shape_translation_and_weight_sorting_are_stable() -> None:
    profile = normalize_research_profile_v12(
        {
            "terms": [
                {"text": "remote sensing", "translation_zh": "遥感", "weight": 40, "source": "paper"},
                {"canonical_en": "soil carbon", "translation_zh": "土壤碳", "weight": 90, "sources": ["manual"]},
            ]
        },
        today="2026-08-31",
    )

    assert [term["canonical_en"] for term in profile["terms"]] == ["soil carbon", "remote sensing"]
    assert profile["terms"][0]["translation_zh"] == "土壤碳"
    assert profile["terms"][0]["status"] == "active"
    assert profile["terms"][0]["text"] == "soil carbon"
    assert profile["terms"][1]["sources"] == ["paper"]


def test_locked_term_rejects_name_weight_and_deletion_but_translation_is_user_editable() -> None:
    profile = normalize_research_profile_v12(
        {"terms": [{"canonical_en": "soil carbon", "translation_zh": "土壤碳", "locked": True}]},
        today="2026-08-31",
    )
    term_id = profile["terms"][0]["id"]

    updated = update_term_fields(
        profile,
        term_id,
        canonical_en="changed",
        translation_zh="土壤有机碳",
        weight=20,
    )
    removed = remove_term(updated, term_id, today="2026-08-31")

    assert removed["terms"][0]["canonical_en"] == "soil carbon"
    assert removed["terms"][0]["translation_zh"] == "土壤有机碳"
    assert removed["terms"][0]["weight"] == 100
    assert removed["blocked_terms"] == []


def test_rejecting_pending_term_creates_a_permanent_block() -> None:
    profile = normalize_research_profile_v12({}, today="2026-08-31")
    profile = add_pending_term(profile, "microbial ecology", source="ai_pdf", today="2026-08-31")
    pending_id = profile["pending_terms"][0]["id"]

    rejected = reject_pending_term(profile, pending_id, today="2026-08-31")
    retried = add_pending_term(rejected, "microbial ecology", source="ai_daily", today="2026-09-01")

    assert retried["pending_terms"] == []
    assert retried["blocked_terms"][0]["reason"] == "pending_rejected"


def test_pending_term_keeps_translation_weight_and_evidence() -> None:
    profile = add_pending_term(
        {},
        "digital soil mapping",
        translation_zh="数字土壤制图",
        weight=78,
        evidence=["OCR full text"],
        source="ai_pdf",
        confidence="high",
        today="2026-08-31",
    )

    assert profile["pending_terms"][0]["translation_zh"] == "数字土壤制图"
    assert profile["pending_terms"][0]["weight"] == 78
    assert profile["pending_terms"][0]["evidence"] == ["OCR full text"]


def test_excluded_terms_are_translated_editable_and_lock_protected() -> None:
    profile = normalize_research_profile_v12({}, today="2026-08-31")
    profile = upsert_excluded_term(
        profile,
        canonical_en="soil microbes",
        translation_zh="土壤微生物",
        locked=True,
        source="user_feedback",
    )
    excluded = profile["excluded_entries"][0]

    still_present = delete_excluded_term(profile, excluded["id"])
    unlocked_payload = dict(still_present)
    unlocked_payload["excluded_entries"] = [{**excluded, "locked": False, "translation_zh": "微生物方向"}]
    deleted = delete_excluded_term(normalize_research_profile_v12(unlocked_payload), excluded["id"])

    assert still_present["excluded_terms"] == ["soil microbes"]
    assert still_present["excluded_entries"][0]["translation_zh"] == "土壤微生物"
    assert deleted["excluded_entries"] == []
    assert deleted["excluded_terms"] == []


def test_frontier_profile_round_trip_preserves_blocks_and_migration_marker() -> None:
    raw = normalize_research_profile_v12({"primary_keywords": ["soil carbon"]}, today="2026-08-31")
    raw = remove_term(raw, raw["terms"][0]["id"], today="2026-08-31")

    reloaded = _normalize_frontier_profile(raw)

    assert reloaded["terms"] == []
    assert reloaded["legacy_profile_migrated_at"] == "2026-08-31"
    assert reloaded["blocked_terms"][0]["canonical_key"] == "soil carbon"
