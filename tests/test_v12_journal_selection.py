"""v12 multi-round journal-selection hard-gate contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import _data_root  # noqa: F401
from utils.evidence_cache import EvidenceCache
from utils.journal_selection_service import (
    normalize_selection_requirements,
    run_selection_rounds,
    topic_fit_threshold,
)


def test_requirement_normalization_and_topic_thresholds() -> None:
    normalized = normalize_selection_requirements(
        {
            "publisher": "Elsevier",
            "fee_mode": "no_fee",
            "jcr_quartiles": [1, "Q2"],
            "cas_quartiles": ["1区", 2],
            "speed_priority": "urgent",
            "fit_strictness": "strict",
        }
    )

    assert normalized["publishers"] == ["Elsevier"]
    assert normalized["fee_modes"] == ["no_fee"]
    assert normalized["jcr_quartiles"] == ["Q1", "Q2"]
    assert normalized["cas_quartiles"] == ["1", "2"]
    assert topic_fit_threshold("lenient") == 55
    assert topic_fit_threshold("balanced") == 65
    assert topic_fit_threshold("strict") == 75


def _identity(name: str, issn: str, publisher: str = "Elsevier", active: bool = True) -> dict:
    return {
        "verified": active,
        "name": name,
        "issns": [issn],
        "publisher": publisher,
        "official_url": f"https://example.org/{issn}",
        "active": active,
        "sources": [{"source": "crossref"}],
        "verified_at": "2026-08-31T12:00:00",
        "conflicts": [],
        "missing": [],
        "reason": "ok" if active else "停止出版",
        "scope": "Soil carbon mapping, soil observations, regional modelling and environmental prediction.",
        "accepted_article_types": ["research article"],
        "positioning": "Publishes original soil carbon mapping methods.",
        "jcr": {"status": "verified", "metrics": [{"quartile": "Q2"}]},
        "decision_days": 90,
        "fee_mode": "hybrid",
        "recent_papers": [{"title": "Regional soil carbon mapping"}],
        "data_policy": "Data availability statement required",
    }


def _run_fixture(
    tmp_path: Path,
    monkeypatch,
    *,
    identities: dict[str, dict],
    requirements: dict | None = None,
    rejected: list[dict] | None = None,
    assessments: dict[str, dict] | None = None,
    easyscholar_ready: bool = False,
    division_patches: dict[str, dict] | None = None,
    supplements: list[list[dict]] | None = None,
):
    from utils import journal_selection_service as service

    works = [
        {
            "id": f"work-{index}",
            "title": f"Similar paper {index}",
            "journal": identity["name"],
            "issn": identity["issns"],
            "publisher": identity.get("publisher", ""),
            "source": "openalex",
        }
        for index, identity in enumerate(identities.values())
    ]
    monkeypatch.setattr(service, "find_similar_works", lambda *args, **kwargs: works)
    # This unit suite isolates similar-paper discovery.  v13 separately tests
    # the full local journal-library recall lane.
    monkeypatch.setattr(service, "_journal_library_recall", lambda: [])

    def verify(candidate, *, cache):
        return identities[str(candidate.get("name", ""))]

    monkeypatch.setattr(service, "verify_journal_identity", verify)
    monkeypatch.setattr(service, "_selection_easyscholar_ready", lambda: easyscholar_ready)
    monkeypatch.setattr(
        service,
        "_verify_selection_divisions",
        lambda journal, ready: (division_patches or {}).get(journal["name"], journal),
    )
    monkeypatch.setattr(
        service,
        "_assess_verified_journals_with_ai",
        lambda manuscript, journals, requirements, progress=None: {
            journal["id"]: (assessments or {}).get(
                journal["name"],
                {"fit_score": 85, "reason_cn": "主题契合", "estimated_decision_days_max": 90},
            )
            for journal in journals
        },
    )
    rounds = iter(supplements or [[], []])
    monkeypatch.setattr(
        service,
        "_request_selection_supplements",
        lambda *args, **kwargs: next(rounds, []),
    )
    cache = EvidenceCache(tmp_path / "evidence.sqlite")
    cache.initialize()
    return run_selection_rounds(
        {
            "id": "paper-1",
            "title": "Soil carbon mapping",
            "keywords": ["soil carbon"],
            "summary": "Regional soil carbon mapping from soil observations.",
            "research_object": ["soil carbon", "soil"],
            "methods": ["mapping"],
            "data": "soil observations",
            "scale": "regional",
            "article_type": "research article",
            "innovation": "new mapping method",
        },
        requirements or {},
        rejected or [],
        cache=cache,
        max_rounds=5,
        no_growth_limit=2,
    )


def test_rejected_name_and_issn_aliases_are_hard_eliminations(tmp_path: Path, monkeypatch) -> None:
    identities = {
        "CATENA": _identity("CATENA", "0341-8162"),
        "Geoderma": _identity("Geoderma", "0016-7061"),
    }

    result = _run_fixture(
        tmp_path,
        monkeypatch,
        identities=identities,
        rejected=[{"journal_name": "Catena", "issn": "03418162"}, {"journal_name": "Old Geoderma Alias", "aliases": ["Geoderma"]}],
    )

    assert result["results"] == []
    assert result["rejected_counts"]["previously_rejected"] == 2


def test_inactive_is_excluded_but_unverified_identity_is_retained_pending(tmp_path: Path, monkeypatch) -> None:
    inactive = _identity("Former Journal", "1111-2222", active=False)
    unknown = _identity("Unknown Journal", "3333-4444")
    unknown.update({"verified": False, "missing": ["官方主页"], "official_url": ""})

    result = _run_fixture(
        tmp_path,
        monkeypatch,
        identities={"Former Journal": inactive, "Unknown Journal": unknown},
    )

    assert [item["journal_name"] for item in result["results"]] == ["Unknown Journal"]
    assert result["results"][0]["journal"]["identity_status"] == "pending_verification"
    assert result["rejected_counts"]["identity_or_inactive"] == 1


def test_selected_publisher_is_a_hard_gate(tmp_path: Path, monkeypatch) -> None:
    result = _run_fixture(
        tmp_path,
        monkeypatch,
        identities={
            "Elsevier Journal": _identity("Elsevier Journal", "1111-2222", "Elsevier"),
            "Wiley Journal": _identity("Wiley Journal", "3333-4444", "Wiley"),
        },
        requirements={"publisher": "Elsevier"},
    )

    assert [item["journal_name"] for item in result["results"]] == ["Elsevier Journal"]
    assert result["rejected_counts"]["publisher"] == 1


def test_configured_divisions_are_a_strict_hard_gate_including_unknowns(tmp_path: Path, monkeypatch) -> None:
    patches = {
        "Q1 Journal": {
            **_identity("Q1 Journal", "1111-2222"),
            "name": "Q1 Journal",
            "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
            "easyscholar": {"cas_upgrade": "1区"},
        },
        "Q2 Journal": {
            **_identity("Q2 Journal", "3333-4444"),
            "name": "Q2 Journal",
            "jcr": {"status": "verified", "metrics": [{"quartile": "Q2"}]},
            "easyscholar": {"cas_upgrade": "2区"},
        },
        "Unknown Journal": {**_identity("Unknown Journal", "5555-6666"), "name": "Unknown Journal"},
    }

    result = _run_fixture(
        tmp_path,
        monkeypatch,
        identities={name: _identity(name, patch["issns"][0]) for name, patch in patches.items()},
        requirements={"jcr_quartiles": ["Q1"], "cas_quartiles": ["1区"]},
        easyscholar_ready=True,
        division_patches=patches,
    )

    assert [item["journal_name"] for item in result["results"]] == ["Q1 Journal"]
    assert result["rejected_counts"]["division"] == 2


def test_explicit_division_constraint_does_not_silently_pass_when_verifier_is_unconfigured(tmp_path: Path, monkeypatch) -> None:
    result = _run_fixture(
        tmp_path,
        monkeypatch,
        identities={"Unknown Journal": _identity("Unknown Journal", "1111-2222")},
        requirements={"jcr_quartiles": ["Q1"], "cas_quartiles": ["1区"]},
        easyscholar_ready=False,
    )

    assert result["results"] == []
    assert result["rejected_counts"]["division"] == 1


def test_confirmed_jcr_q3_q4_are_excluded_without_a_legacy_toggle(tmp_path: Path, monkeypatch) -> None:
    patches = {
        "Q2 Journal": {
            **_identity("Q2 Journal", "1111-2222"),
            "name": "Q2 Journal",
            "jcr": {"status": "verified", "metrics": [{"quartile": "Q2"}]},
        },
        "Q3 Journal": {
            **_identity("Q3 Journal", "3333-4444"),
            "name": "Q3 Journal",
            "jcr": {"status": "verified", "metrics": [{"quartile": "Q3"}]},
        },
    }
    result = _run_fixture(
        tmp_path,
        monkeypatch,
        identities={name: _identity(name, patch["issns"][0]) for name, patch in patches.items()},
        division_patches=patches,
    )

    assert [item["journal_name"] for item in result["results"]] == ["Q2 Journal"]
    assert result["rejected_counts"]["division"] == 1


def test_fee_and_speed_rank_but_never_eliminate_and_hybrid_matches_both(
    tmp_path: Path, monkeypatch
) -> None:
    identities = {
        "Hybrid Journal": {**_identity("Hybrid Journal", "1111-2222"), "fee_mode": "hybrid"},
        "Slow APC Journal": {**_identity("Slow APC Journal", "3333-4444"), "fee_mode": "apc"},
    }
    assessments = {
        "Hybrid Journal": {"fit_score": 80, "reason_cn": "契合", "estimated_decision_days_max": 35},
        "Slow APC Journal": {"fit_score": 80, "reason_cn": "契合", "estimated_decision_days_max": 220},
    }

    result = _run_fixture(
        tmp_path,
        monkeypatch,
        identities=identities,
        requirements={"fee_mode": "no_fee", "speed_priority": "urgent"},
        assessments=assessments,
    )

    assert len(result["results"]) == 2
    assert result["results"][0]["journal_name"] == "Hybrid Journal"
    assert set(result["results"][0]["fee_matches"]) == {"no_fee", "paid"}


def test_topic_threshold_no_longer_hard_deletes_a_candidate(tmp_path: Path, monkeypatch) -> None:
    weak = _identity("Weak Fit", "1111-2222")
    weak.update(
        {
            "scope": "Clinical medicine and hospital interventions.",
            "accepted_article_types": [],
            "positioning": "",
            "jcr": {},
            "decision_days": "",
            "fee_mode": "unknown",
            "recent_papers": [],
            "data_policy": "",
        }
    )
    result = _run_fixture(
        tmp_path,
        monkeypatch,
        identities={"Weak Fit": weak},
        requirements={"fit_strictness": "balanced"},
        assessments={"Weak Fit": {"fit_score": 64, "reason_cn": "略相关"}},
    )

    assert result["results"] == []
    assert [item["journal_name"] for item in result["retained_candidates"]] == ["Weak Fit"]
    assert result["retained_candidates"][0]["candidate_state"] == "retained_unshown"
    assert result["retained_candidates"][0]["display_reason"] == "below_dynamic_pyramid"


def test_ai_assessment_is_chunked_and_one_failed_batch_does_not_cancel_the_rest(monkeypatch) -> None:
    from utils import journal_selection_service as service

    journals = [
        {
            "id": f"journal-{index}",
            "name": f"Journal {index}",
            "identity_evidence": {"verified": True},
        }
        for index in range(12)
    ]
    calls: list[list[str]] = []

    def assess(_manuscript, batch, _requirements, progress=None):
        del progress
        ids = [value["id"] for value in batch]
        calls.append(ids)
        if len(calls) == 2:
            raise RuntimeError("output truncated")
        return {value: {"reason_cn": "ok"} for value in ids}

    monkeypatch.setattr(service, "_assess_verified_journals_with_ai", assess)
    assessments, errors = service._assess_verified_journals_in_batches(
        {"title": "Paper"},
        journals,
        {},
        emit=lambda *_args: None,
    )

    assert [len(value) for value in calls] == [5, 5, 2]
    assert len(assessments) == 7
    assert errors == [
        {"start": 6, "end": 10, "error_type": "RuntimeError", "error": "output truncated"}
    ]


def test_two_consecutive_no_growth_rounds_stop_the_search(tmp_path: Path, monkeypatch) -> None:
    result = _run_fixture(tmp_path, monkeypatch, identities={}, supplements=[[], [], [{"name": "Too Late"}]])

    assert result["rounds"] == 2
    assert result["stop_reason"] == "no_growth_limit"
