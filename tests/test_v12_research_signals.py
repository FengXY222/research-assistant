"""Long/short research-signal learning contracts for v12."""

from __future__ import annotations

from datetime import datetime

import pytest

from utils.research_profile_service import normalize_research_profile_v12
from utils.research_signal_service import (
    apply_profile_comment,
    build_profile_view,
    record_signal,
)


def test_no_action_is_neutral() -> None:
    view = build_profile_view(normalize_research_profile_v12({}), now=datetime(2026, 8, 31, 12))

    assert view["short_term"] == []
    assert view["positive_seeds"] == []
    assert view["negative_seeds"] == []


def test_locked_terms_and_authored_papers_do_not_decay() -> None:
    profile = normalize_research_profile_v12(
        {
            "terms": [{"canonical_en": "soil carbon", "locked": True, "created_at": "2020-01-01"}],
            "authored_papers": [{"id": "paper-1", "title": "Mapping soil carbon", "at": "2020-01-01"}],
        },
        today="2026-08-31",
    )

    view = build_profile_view(profile, now=datetime(2036, 8, 31, 12))

    assert {item["kind"] for item in view["long_term"]} == {"locked_term", "authored_paper"}
    assert all(item["strength"] == 1.0 for item in view["long_term"])


@pytest.mark.parametrize(
    ("event_type", "expected_strength", "half_life"),
    [
        ("favorite", 0.5, 180),
        ("paper_association", 0.5, 180),
        ("detail_open", 0.175, 45),
        ("read", 0.175, 45),
        ("ignore", -0.5, 180),
        ("explicit_positive", 0.5, 365),
        ("explicit_negative", -0.5, 365),
    ],
)
def test_signal_strength_halves_at_the_contract_half_life(
    event_type: str, expected_strength: float, half_life: int
) -> None:
    profile = record_signal(
        {},
        {
            "event_type": event_type,
            "item_id": "work-1",
            "title": "Example paper",
            "terms": ["soil carbon"],
            "occurred_at": "2026-01-01T12:00:00",
        },
    )

    view = build_profile_view(profile, now=datetime(2026, 1, 1, 12).replace(day=1))
    at_half_life = build_profile_view(
        profile,
        now=datetime(2026, 1, 1, 12) + __import__("datetime").timedelta(days=half_life),
    )

    assert view["short_term"][0]["strength"] in {1.0, 0.35, -1.0}
    assert at_half_life["short_term"][0]["strength"] == pytest.approx(expected_strength)


def test_same_item_action_and_day_is_recorded_only_once() -> None:
    event = {
        "event_type": "read",
        "item_id": "work-1",
        "title": "Example",
        "occurred_at": "2026-08-31T08:00:00",
    }

    once = record_signal({}, event)
    twice = record_signal(once, {**event, "occurred_at": "2026-08-31T20:00:00"})

    assert len(twice["research_signals"]) == 1


def test_negative_comment_adds_exclusion_and_a_strong_negative_signal() -> None:
    profile, changes = apply_profile_comment(
        {},
        {
            "intent": "negative",
            "active_terms": [],
            "excluded_terms": [
                {"canonical_en": "soil microorganisms", "translation_zh": "土壤微生物"}
            ],
            "pending_terms": [],
            "reason": "用户明确说明不是研究方向",
            "confidence": "high",
            "item_id": "comment-1",
        },
        today="2026-08-31",
    )

    assert profile["excluded_terms"] == ["soil microorganisms"]
    assert profile["research_signals"][0]["event_type"] == "explicit_negative"
    assert any(change["kind"] == "excluded" for change in changes)


def test_positive_comment_adds_evidenced_term_immediately() -> None:
    profile, changes = apply_profile_comment(
        {},
        {
            "intent": "positive",
            "active_terms": [
                {
                    "canonical_en": "soil heavy metal mapping",
                    "translation_zh": "土壤重金属制图",
                    "weight": 88,
                }
            ],
            "excluded_terms": [],
            "pending_terms": [],
            "reason": "用户要求多推荐同类研究",
            "confidence": "high",
            "item_id": "comment-2",
        },
        today="2026-08-31",
    )

    assert profile["terms"][0]["canonical_en"] == "soil heavy metal mapping"
    assert profile["terms"][0]["translation_zh"] == "土壤重金属制图"
    assert profile["research_signals"][0]["event_type"] == "explicit_positive"
    assert any(change["kind"] == "term_added" for change in changes)


def test_ambiguous_comment_terms_wait_for_confirmation() -> None:
    profile, _changes = apply_profile_comment(
        {},
        {
            "intent": "ambiguous",
            "active_terms": [],
            "excluded_terms": [],
            "pending_terms": [
                {"canonical_en": "soil respiration", "translation_zh": "土壤呼吸", "weight": 55}
            ],
            "reason": "语义不明确",
            "confidence": "low",
        },
        today="2026-08-31",
    )

    assert profile["terms"] == []
    assert profile["pending_terms"][0]["canonical_en"] == "soil respiration"
    assert profile.get("research_signals", []) == []
