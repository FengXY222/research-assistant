"""v12 Daily Frontier mix and quality-gate contracts."""

from __future__ import annotations

from utils.frontier_scoring import select_daily_mix
from utils.frontier_service import partition_frontier_items


def _item(item_id: str, score: int, kind: str = "core_keyword", **extra):
    return {
        "id": item_id,
        "title": item_id,
        "score": score,
        "recommendation_kind": kind,
        **extra,
    }


def test_daily_mix_keeps_at_least_half_core_matches_when_available() -> None:
    items = [
        *[_item(f"core-{index}", 60 - index) for index in range(5)],
        *[_item(f"explore-{index}", 100 - index, "profile_exploration") for index in range(8)],
    ]

    selected = select_daily_mix(items, limit=6, minimum_core_ratio=0.5)

    assert len(selected) == 6
    assert sum(item["recommendation_kind"] == "core_keyword" for item in selected) >= 3


def test_daily_mix_uses_profile_exploration_when_core_has_no_results() -> None:
    selected = select_daily_mix(
        [_item("explore-1", 90, "profile_exploration"), _item("explore-2", 80, "profile_exploration")],
        limit=5,
    )

    assert [item["id"] for item in selected] == ["explore-1", "explore-2"]
    assert all(item["recommendation_kind"] == "profile_exploration" for item in selected)


def test_configured_easyscholar_only_admits_verified_q1_q2_to_journal_stream() -> None:
    items = [
        _item("q1", 90, jcr_status="verified", jcr_quartile="Q1"),
        _item("q2", 80, jcr_status="verified", jcr_quartile="2区"),
        _item("q3", 70, jcr_status="verified", jcr_quartile="Q3"),
        _item("unknown", 60, jcr_status="pending", jcr_quartile=""),
        _item("preprint", 50, is_preprint=True, journal="arXiv"),
    ]

    streams = partition_frontier_items(items, {}, easyscholar_ready=True)

    assert [item["id"] for item in streams["journal"]] == ["q1", "q2"]
    assert [item["id"] for item in streams["preprint"]] == ["preprint"]
    assert {item["id"] for item in streams["pending_quality"]} == {"q3", "unknown"}


def test_unconfigured_easyscholar_does_not_filter_journal_quartiles() -> None:
    items = [
        _item("q4", 70, jcr_status="verified", jcr_quartile="Q4"),
        _item("unknown", 60, jcr_status="unknown", jcr_quartile=""),
    ]

    streams = partition_frontier_items(items, {}, easyscholar_ready=False)

    assert [item["id"] for item in streams["journal"]] == ["q4", "unknown"]
    assert streams["pending_quality"] == []


def test_local_verified_library_evidence_can_admit_a_q2_journal() -> None:
    item = _item("local", 70, journal="CATENA")
    profile = {
        "journal_library": [
            {
                "name": "CATENA",
                "jcr": {"status": "verified", "metrics": [{"quartile": "Q2"}]},
                "jcr_verified_at": "2026-08-20",
            }
        ]
    }

    streams = partition_frontier_items([item], profile, easyscholar_ready=True)

    assert [row["id"] for row in streams["journal"]] == ["local"]
    assert streams["journal"][0]["quality_gate_reason"] == "verified_q1_q2"
