"""Confirmed research boundaries and live frontier admission integration."""

from copy import deepcopy
from datetime import datetime

import pytest

from utils import frontier_admission as admission


def item(key="p", **extra):
    return {"id": key, "title": f"Soil health and fertility assessment {key}",
            "abstract": "Soil health assessment uses soil fertility and spatial mapping.",
            "journal": "CATENA", "jcr_status": "verified", "jcr_quartile": "Q1",
            "status": "new", **extra}


def response(key="p", **extra):
    return {"id": key, "decision": "accept", "relation": "core", "score": 90,
            "confidence": "high", "profile_refs": ["scope:soil_quality"],
            "evidence_refs": ["title", "abstract"], "reason_cn": "属于用户确认接收的土壤质量评价。",
            "exclusion": "none", "exclusion_refs": [], "exclusion_reason_cn": "", **extra}


def test_soil_quality_is_a_confirmed_direction_even_with_old_exclusion():
    profile = {"terms": [], "excluded_entries": [{"id": "health", "text": "soil health"}]}
    before = deepcopy(profile)
    context = admission.build_review_context(profile, [])
    rows = admission.review_candidates([item()], context, reviewer=lambda _: {"evaluations": [response()]},
                                      journals=[], easyscholar_ready=False)
    assert rows[0]["content_decision"] == "accept"
    assert profile == before


def test_landslide_method_is_exploration_with_reduced_rank_not_relaxed_admission():
    context = admission.build_review_context({}, [])
    v = response(profile_refs=["scope:landslide_methods"], relation="transferable")
    result = admission.review_candidates([item(title="DEM resolution and flood susceptibility",
        abstract="DEM resolution changes slope estimates and susceptibility mapping.")], context,
        reviewer=lambda _: {"evaluations": [v]}, journals=[], easyscholar_ready=False)[0]
    assert result["content_decision"] == "accept"
    assert result["ranking_weight"] == 0.65
    assert result["ranking_score"] < result["score"] == 90
    assert result["relation"] == "transferable"
    v["score"] = 60
    assert admission.review_candidates([item()], context, reviewer=lambda _: {"evaluations": [v]},
        journals=[], easyscholar_ready=False)[0]["content_decision"] == "reject"


def test_confirmed_carbon_protection_overrides_generic_mineral_conflict():
    context = admission.build_review_context({"terms": [{"id": "maoc", "text": "mineral-associated organic carbon"}],
        "excluded_entries": [{"id": "mineral", "text": "mineral"}]}, [])
    v = response(profile_refs=["scope:soil_carbon_protection"], exclusion="primary",
                 exclusion_refs=["exclude:mineral"], exclusion_reason_cn="涉及矿物结合")
    rows = admission.review_candidates([item(title="Mineral protection of soil organic carbon",
        abstract="Soil organic carbon binding to iron controls carbon stabilization.")], context,
        reviewer=lambda _: {"evaluations": [v]}, journals=[], easyscholar_ready=False)
    assert rows[0]["content_decision"] == "accept"


def test_new_content_score_and_reason_survive_refresh_merge():
    from utils.frontier_service import merge_frontier_refresh_item
    incoming = item(content_decision="accept", admission_version="test", score=88,
                    recommendation_reason="new evidence", ai_reason_cn="new evidence")
    previous = item(status="read", score=200, ai_adjustment=-15, ai_reason_cn="old reason")
    merged = merge_frontier_refresh_item(incoming, previous, "2026-09-05")
    assert merged["status"] == "read"
    assert merged["score"] == 88 and merged["ai_reason_cn"] == "new evidence"


def test_strict_daily_mix_does_not_fill_missing_core_quota_with_exploration():
    from utils.frontier_scoring import select_daily_mix
    rows = [item(str(i), recommendation_kind="profile_exploration", score=90-i,
                 content_decision="accept", admission_version="test") for i in range(12)]
    rows[0]["recommendation_kind"] = "core_keyword"
    selected = select_daily_mix(rows, limit=10)
    assert len(selected) == 2


def test_query_planner_reserves_authored_and_exploration_lanes():
    assert hasattr(admission, "plan_frontier_queries")
    view = {"active_terms": [{"text": f"soil direction {i}", "weight": 80} for i in range(30)],
            "positive_seeds": [{"title": "Existing soil carbon paper", "kind": "authored_paper"}]}
    queries = admission.plan_frontier_queries(view, today="2026-09-05")
    assert {q["lane"] for q in queries} >= {"core", "similar", "exploration"}
    assert len(queries) <= 8


def test_refresh_calls_admission_and_does_not_push_rejected_candidates(tmp_path, monkeypatch):
    from utils import frontier_service as service
    monkeypatch.setattr(service.file_manager, "RESEARCH_INTELLIGENCE_CACHE_FILE", tmp_path / "cache.sqlite")
    monkeypatch.setattr(service, "discover_frontier_candidates", lambda *a, **k: [item("good"), item("bad")])
    monkeypatch.setattr(service, "_enrich_frontier_quality_with_easyscholar", lambda items, *a, **k: items)
    assert hasattr(service, "review_frontier_content")
    def review(profile, items, journals, **kwargs):
        return [dict(i, content_decision="accept" if i["id"]=="good" else "reject",
            admission_version="test", quality_gate_state="eligible", score=80,
            recommendation_kind="core_keyword") for i in items]
    monkeypatch.setattr(service, "review_frontier_content", review)
    original = {"profile": {}, "items": [item("history", status="read")]}
    result = service.update_daily_frontier_v12(original, [])
    assert result["visible_count"] == 1
    assert next(i for i in result["data"]["items"] if i["id"] == "history")["status"] == "read"


def test_ai_failure_preserves_previously_approved_items(tmp_path, monkeypatch):
    from utils import frontier_service as service
    monkeypatch.setattr(service.file_manager, "RESEARCH_INTELLIGENCE_CACHE_FILE", tmp_path / "cache.sqlite")
    monkeypatch.setattr(service, "discover_frontier_candidates", lambda *a, **k: [item("new")])
    monkeypatch.setattr(service, "_enrich_frontier_quality_with_easyscholar", lambda items, *a, **k: items)
    assert hasattr(service, "review_frontier_content")
    monkeypatch.setattr(service, "review_frontier_content", lambda profile, items, journals, **kwargs:
        [dict(i, content_decision="pending", admission_version="test", admission_issue="reviewer_unavailable") for i in items])
    old = item("old", content_decision="accept", admission_version="test", quality_gate_state="eligible",
               recommendation_kind="core_keyword", score=90)
    result = service.update_daily_frontier_v12({"profile": {}, "items": [old]}, [])
    assert any(i["id"] == "old" and i["content_decision"] == "accept" for i in result["data"]["items"])


def test_cached_accept_survives_failure_for_a_different_paper(tmp_path):
    from utils.frontier_review_service import review_frontier_content
    from utils.evidence_cache import EvidenceCache
    cache = EvidenceCache(tmp_path / "reviews.sqlite")
    cache.initialize()
    def good(payload):
        return {"evaluations": [response(c["id"]) for c in payload["candidates"]]}
    first = review_frontier_content({}, [item("cached")], [], reviewer=good, cache=cache, easyscholar_ready=False)
    assert first[0]["content_decision"] == "accept"
    def failed(payload):
        raise RuntimeError("provider offline")
    rows = review_frontier_content({}, [item("cached"), item("new")], [], reviewer=failed,
                                   cache=cache, easyscholar_ready=False)
    assert rows[0]["content_decision"] == "accept"
    assert rows[1]["admission_issue"] == "reviewer_unavailable"


def test_review_budget_queues_unreviewed_candidates_without_pushing_them():
    from utils.frontier_review_service import review_frontier_content
    rows = review_frontier_content({}, [item(str(n)) for n in range(81)], [],
        reviewer=lambda p: {"evaluations": [response(c["id"]) for c in p["candidates"]]},
        easyscholar_ready=False)
    assert sum(i["content_decision"] == "accept" for i in rows) == 80
    assert rows[-1]["content_decision"] == "pending"
    assert rows[-1]["admission_issue"] == "review_budget_pending"


def test_quality_routing_updates_the_admission_audit_route():
    from utils.frontier_service import partition_frontier_items
    streams = partition_frontier_items([
        item("unknown", content_decision="accept", admission_version="test",
             admission_route="journal", jcr_status="unknown", jcr_quartile=""),
        item("q4", content_decision="accept", admission_version="test",
             admission_route="journal", jcr_quartile="Q4"),
    ], {}, easyscholar_ready=True)
    routes = {i["id"]: i["admission_route"] for i in streams["pending_quality"]}
    assert routes == {"unknown": "pending_quality", "q4": "excluded_quality"}
