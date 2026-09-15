"""Evidence-bound independent matching, with all remote calls replaced offline."""
from copy import deepcopy
from datetime import datetime
from unittest.mock import patch

import pytest

from utils import special_issue_matching as matching


def profiles():
    return matching.build_special_issue_profiles({
        "terms": [{"canonical_en": "soil organic carbon", "weight": 95, "locked": True}],
        "excluded_entries": [{"canonical_en": "mineral"}, {"canonical_en": "soil health"}],
        "blocked_terms": [{"canonical_en": "metagenomics"}],
        "authored_papers": [{"id": "published", "title": "Soil fertility index"}],
        "research_signals": [{"id": "s1", "event_type": "favorite", "title": "Soil carbon", "occurred_at": "2026-09-06T00:00:00", "terms": ["soil carbon"]}],
    }, [
        {"id": "paper-a", "title": "SOC mapping", "keywords": ["soil organic carbon"], "summary": "Spatial prediction of soil carbon."},
        {"id": "paper-b", "title": "Landslide susceptibility", "keywords": ["DEM"], "summary": "Terrain methods for landslide susceptibility assessment."},
    ])


def issue(**overrides):
    return {"id": "si-1", "title": "Soil carbon and terrain methods", "publisher": "Elsevier",
        "scope_text": "We invite soil carbon mineral protection and carbon iron binding studies.\n\nDEM and hydrological methods explicitly serving landslide susceptibility are welcome.",
        "scope_is_complete": True, "scope_paragraphs": [
            {"id": "scope-1", "text": "We invite soil carbon mineral protection and carbon iron binding studies."},
            {"id": "scope-2", "text": "DEM and hydrological methods explicitly serving landslide susceptibility are welcome."},
        ], **overrides}


def assessment(score=90, *, branch="scope:soil_carbon_protection", refs=None, relation="core", **overrides):
    return {"score": score, "reason": "The cited call branch explicitly accepts this research question.",
        "risk": "Submission must address the stated research question.", "branch": branch, "relation": relation,
        "evidence_refs": refs or ["scope-1"], "exclusion_assessment": {
            "status": "incidental", "reason": "Mineral protection is the confirmed soil carbon branch.", "evidence_refs": refs or ["scope-1"]}, **overrides}


def response(score=90, **overrides):
    return {**assessment(score), "matched_terms": ["soil organic carbon"], "model": "fixture-model",
        "scope_coverage": ["scope-1", "scope-2"], "paper_matches": [
            {"paper_id": "paper-a", **assessment(5, branch="unrelated", relation="unrelated")},
            {"paper_id": "paper-b", **assessment(90, branch="scope:landslide_methods", relation="exploration", refs=["scope-2"])},
        ], **overrides}


def run_match(raw=None, **item_fields):
    return matching.match_special_issue(issue(**item_fields), profiles(), ai_matcher=lambda *_: response() if raw is None else raw)


def test_profiles_keep_shared_signals_without_blocked_term_content_exclusions():
    value = profiles()
    assert "metagenomics" not in value["global"]["excluded_terms"]
    assert "metagenomics" not in value["papers"]["paper-a"]["excluded_terms"]
    assert any(row["kind"] == "authored_paper" for row in value["global"]["long_term"])
    assert value["global"]["short_term"][0]["kind"] == "favorite"


def test_other_publisher_90_qualifies_with_ranking_45():
    value = run_match(publisher="MDPI")
    assert value["score"] == value["raw_score"] == 90
    assert value["rank_score"] == 45
    assert value["formal"] is value["content_qualified"] is True


def test_unknown_publisher_is_tail_metadata_not_content_rejection():
    value = run_match(publisher="未知")
    assert value["publisher_unknown"] is True
    assert value["formal"] is True


def test_paper_a_5_never_inherits_global_95_while_paper_b_keeps_90():
    value = run_match(response(95))
    assert [(row["paper_id"], row["score"]) for row in value["matched_papers"]] == [("paper-b", 90)]
    rows = {row["paper_id"]: row for row in value["paper_matches"]}
    assert rows["paper-a"]["score"] == 5 and rows["paper-a"]["formal"] is False
    assert rows["paper-b"]["relation"] == "exploration" and rows["paper-b"]["formal"] is True


def test_qualified_paper_survives_low_global_score():
    value = run_match(response(20))
    assert value["formal"] is False
    assert value["any_paper_qualified"] is True


def test_missing_paper_scores_remain_pending_without_inheritance():
    value = run_match(response(paper_matches=[]))
    assert value["formal"] is True and value["matched_papers"] == []
    assert {row["paper_id"] for row in value["paper_matches"]} == {"paper-a", "paper-b"}
    assert all(row["status"] == "pending" and row["score"] is None for row in value["paper_matches"])


@pytest.mark.parametrize("fields", [{"scope_text": ""}, {"scope_is_complete": False, "scope_status": "snippet"}])
def test_incomplete_scope_does_not_call_ai(fields):
    called = []
    value = matching.match_special_issue(issue(**fields), profiles(), ai_matcher=lambda *_: called.append(True))
    assert called == []
    assert value["status"] == "awaiting_scope" and value["formal"] is False


@pytest.mark.parametrize("change", [
    {"score": 101}, {"score": -1}, {"score": float("nan")}, {"score": True},
    {"reason": ""}, {"risk": None}, {"branch": "invented-direction"},
    {"evidence_refs": []}, {"evidence_refs": ["invented-paragraph"]},
    {"scope_coverage": ["scope-1"]}, {"relation": "invented"},
    {"exclusion_assessment": {"status": "none"}},
])
def test_invalid_contract_is_pending_without_heuristic_score(change):
    value = run_match(response(**change))
    assert value["formal"] is False and value["status"] == "invalid_response"
    assert value["score"] is None and value["validation_errors"]


@pytest.mark.parametrize("ids", [["invented"], ["paper-a", "paper-a"]])
def test_unknown_or_duplicate_paper_ids_invalidate_response(ids):
    value = run_match(response(paper_matches=[{"paper_id": identity, **assessment()} for identity in ids]))
    assert value["status"] == "invalid_response" and value["matched_papers"] == []


def test_primary_unrelated_scope_blocks_admission_without_repeated_score_penalties():
    value = run_match(response(90, relation="unrelated", branch="unrelated", exclusion_assessment={"status": "primary", "reason": "Only unrelated mineral synthesis is accepted.", "evidence_refs": ["scope-1"]}))
    assert value["score"] == 90 and value["formal"] is False and value["status"] == "out_of_scope"


@pytest.mark.parametrize("branch,relation", [("scope:soil_carbon_protection", "core"), ("scope:soil_quality", "core"), ("scope:landslide_methods", "exploration")])
def test_confirmed_branches_are_not_vetoed_by_legacy_words(branch, relation):
    value = run_match(response(60, branch=branch, relation=relation))
    assert value["formal"] is True and value["score"] == 60


def test_landslide_branch_is_always_exploration():
    assert run_match(response(branch="scope:landslide_methods", relation="core"))["relation"] == "exploration"


def test_ai_failure_keeps_pending_without_keyword_fallback():
    def unavailable(*_):
        raise TimeoutError("remote unavailable")
    value = matching.match_special_issue(issue(), profiles(), ai_matcher=unavailable)
    assert value["formal"] is False and value["score"] is None and value["status"] == "ai_unavailable"


def test_fingerprint_ignores_volatile_metadata_but_tracks_semantics_scope_and_model():
    profile = profiles()
    original = matching.match_input_fingerprint(issue(), profile, "model-a")
    changed = deepcopy(profile)
    changed["global"]["built_at"] = "2099-01-01"
    changed["global"]["active_terms"][0]["updated_at"] = "2099-01-01"
    assert matching.match_input_fingerprint(issue(), changed, "model-a") == original
    changed["papers"]["paper-a"]["abstract"] = "A new research purpose."
    assert matching.match_input_fingerprint(issue(), changed, "model-a") != original
    assert matching.match_input_fingerprint(issue(), profile, "model-b") != original
    assert matching.match_input_fingerprint(issue(scope_text="Revised call."), profile, "model-a") != original


def test_behavior_decay_is_stable_within_each_day_and_source_is_unchanged():
    source = {"terms": [{"canonical_en": "soil carbon", "locked": True}], "research_signals": [{"event_type": "favorite", "occurred_at": "2026-09-01T00:00:00", "title": "Soil carbon", "terms": ["soil carbon"]}]}
    first = matching.build_special_issue_profiles(source, [], now=datetime(2026, 9, 6, 1))
    second = matching.build_special_issue_profiles(source, [], now=datetime(2026, 9, 6, 23))
    assert matching.match_input_fingerprint(issue(), first, "m") == matching.match_input_fingerprint(issue(), second, "m")
    assert source["terms"][0] == {"canonical_en": "soil carbon", "locked": True}


def test_long_unbroken_scope_has_stable_paragraph_ids_and_full_coverage():
    item = issue(scope_text="soil carbon " * 3000, scope_paragraphs=[])
    rows = matching.scope_paragraphs(item)
    assert rows == matching.scope_paragraphs(item)
    assert "".join("".join(row["text"].split()) for row in rows) == "".join(item["scope_text"].split())
    assert len({row["id"] for row in rows}) == len(rows)


def test_ai_is_independent_of_disabled_profile_updates_and_covers_every_chunk():
    from utils import ai_service
    captured = []
    item = issue(scope_text="soil carbon " * 2000, scope_paragraphs=[])
    def fake_chat(_config, _key, system, payload, _max_tokens):
        captured.append((system, payload))
        ids = [row["id"] for row in payload["special_issue"]["scope_paragraphs"]]
        return response(scope_coverage=ids, evidence_refs=[ids[0]], exclusion_assessment={"status": "none", "reason": "No exclusion applies.", "evidence_refs": []}, paper_matches=[{"paper_id": paper_id, **assessment(refs=[ids[0]])} for paper_id in ("paper-a", "paper-b")])
    with (
        patch.object(ai_service, "get_ai_settings", return_value={"enabled": True, "research_profile_update": False, "model": "fixture", "api_key_secret": "encrypted"}),
        patch.object(ai_service, "load_app_settings", return_value={"ai": {}}),
        patch.object(ai_service, "reveal_secret", return_value="offline-secret"),
        patch.object(ai_service, "_chat_json", side_effect=fake_chat),
    ):
        value = matching.match_special_issue(item, profiles())
    assert value["formal"] is True and len(captured) > 1
    rows = [row for _system, payload in captured for row in payload["special_issue"]["scope_paragraphs"]]
    assert "".join("".join(row["text"].split()) for row in rows) == "".join(item["scope_text"].split())
    assert set(value["scope_coverage"]) == {row["id"] for row in rows}
    assert all("不执行" in system and "网页" in system for system, _payload in captured)


def test_explicit_special_issue_ai_optout_prevents_remote_request():
    from utils import ai_service
    with patch.object(ai_service, "load_app_settings", return_value={"ai": {"special_issue_matching": False}}):
        value = matching.match_special_issue(issue(), profiles())
    assert value["status"] == "ai_unavailable" and value["formal"] is False
