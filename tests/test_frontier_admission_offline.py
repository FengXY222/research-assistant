"""Admission contracts for the offline-only replacement; no app data access."""

from copy import deepcopy
import importlib

import pytest


def test_admission_module_exists():
    assert importlib.util.find_spec("utils.frontier_admission") is not None


@pytest.fixture
def admission():
    spec = importlib.util.find_spec("utils.frontier_admission")
    assert spec is not None, "The independent frontier admission module is not implemented"
    return importlib.import_module("utils.frontier_admission")


@pytest.fixture
def context(admission):
    return admission.build_review_context({"terms": [
        {"id": "soc", "text": "SOC", "locked": True},
        {"id": "carbon", "text": "soil organic carbon", "locked": True},
        {"id": "maoc", "text": "mineral-associated organic carbon"},
        {"id": "ml", "text": "machine learning"},
    ], "excluded_entries": [{"id": "mineral", "text": "mineral"},
                             {"id": "medical", "text": "medical papers"}]}, [])


def paper(**extra):
    return {"id": "p1", "title": "Mapping soil organic carbon with spatial uncertainty",
            "abstract": "We map soil organic carbon stocks using spatial cross-validation.",
            "journal": "A journal", "jcr_status": "verified", "jcr_quartile": "Q1",
            "score": 80, "recommendation_reason": "old reason", **extra}


def verdict(**extra):
    return {"id": "p1", "decision": "accept", "relation": "core", "score": 95,
            "confidence": "high", "profile_refs": ["term:carbon"],
            "evidence_refs": ["title", "abstract"],
            "reason_cn": "研究土壤有机碳空间预测，与已有方向直接相关。",
            "exclusion": "none", "exclusion_refs": [], "exclusion_reason_cn": "", **extra}


def run(admission, context, response, items=None, **extra):
    return admission.review_candidates(items or [paper()], context,
        reviewer=lambda payload: response, journals=[], easyscholar_ready=True, **extra)


def test_search_metadata_and_reference_labels_never_reach_ai(admission, context):
    item = paper(expected="reject", matched_terms=["SOC"], ai_reason_cn="label leak")
    payload = admission.build_review_payload(context, [item])
    assert set(payload["candidates"][0]) == {"id", "title", "abstract", "author_keywords", "content_evidence"}
    assert "old reason" not in str(payload)
    assert "label leak" not in str(payload)


def test_acronym_is_merged_and_does_not_match_social(admission, context):
    carbon = next(t for t in context["terms"] if t["id"] == "term:carbon")
    assert "SOC" in carbon["aliases"]
    assert len(context["terms"]) == 3
    ev = admission.content_evidence(paper(title="Social anxiety and social media", abstract=""), context)
    assert not ev["matched_profile_refs"]


def test_unexpanded_soc_is_ambiguous_not_an_automatic_core_match(admission, context):
    ev = admission.content_evidence(paper(title="Survival after standard of care (SOC)", abstract=""), context)
    assert ev["ambiguous_acronyms"] == ["SOC"]
    assert not ev["matched_profile_refs"]


def test_profile_conflict_is_visible_without_mutation(admission, context):
    assert any(c["excluded_ref"] == "exclude:mineral" for c in context["conflicts"])
    assert next(t for t in context["terms"] if t["id"] == "term:ml")["role"] == "method"


def test_valid_accept_replaces_old_score_and_reason(admission, context):
    item = paper(); before = deepcopy(item)
    result = run(admission, context, {"evaluations": [verdict()]}, [item])[0]
    assert result["route"] == "journal"
    assert result["content_decision"] == "accept"
    assert result["score"] == 95 and result["reason_cn"] == verdict()["reason_cn"]
    assert item == before


def test_ai_can_reject_even_q1_and_high_old_score(admission, context):
    result = run(admission, context, {"evaluations": [verdict(decision="reject", relation="unrelated", score=0)]})[0]
    assert result["route"] == "rejected"


@pytest.mark.parametrize("changes", [
    {"evidence_refs": ["invented_source"]},
    {"evidence_refs": []}, {"profile_refs": ["term:invented"]},
    {"profile_refs": ["term:ml"]}, {"confidence": "low"},
    {"score": 101}, {"score": True}, {"score": "not a score"},
    {"reason_cn": ""}, {"decision": "unknown"},
    {"exclusion": "conflict", "exclusion_refs": ["exclude:mineral"]},
])
def test_invalid_or_uncertain_accept_is_not_pushed(admission, context, changes):
    result = run(admission, context, {"evaluations": [verdict(**changes)]})[0]
    assert result["route"] == "pending_content"


def test_below_threshold_never_receives_a_quality_bonus(admission, context):
    result = run(admission, context, {"evaluations": [verdict(score=60)]})[0]
    assert result["route"] == "rejected"


def test_primary_exclusion_overrules_accept_score(admission, context):
    v = verdict(exclusion="primary", exclusion_refs=["exclude:medical"], exclusion_reason_cn="主题命中排除方向")
    assert run(admission, context, {"evaluations": [v]})[0]["route"] == "rejected"


@pytest.mark.parametrize("response", [{}, {"evaluations": []}, {"evaluations": [verdict(id="other")]},
                                       {"evaluations": [verdict(), verdict()]}])
def test_missing_duplicate_or_unknown_ai_ids_fail_closed(admission, context, response):
    assert run(admission, context, response)[0]["route"] == "pending_content"


def test_failure_isolated_by_batch_and_no_twelve_item_truncation(admission, context):
    calls = []
    def reviewer(payload):
        calls.append(payload)
        if len(calls) == 2:
            raise RuntimeError("private failure text")
        return {"evaluations": [verdict(id=i["id"]) for i in payload["candidates"]]}
    items = [paper(id=str(i)) for i in range(17)]
    rows = admission.review_candidates(items, context, reviewer=reviewer, batch_size=4,
                                      journals=[], easyscholar_ready=True)
    assert len(rows) == 17 and len(calls) == 5
    assert sum(r["route"] == "pending_content" for r in rows) == 4
    assert rows[-1]["route"] == "journal"
    assert "private failure text" not in str(rows)


@pytest.mark.parametrize("quartile,status,ready,route", [
    ("Q2", "verified", True, "journal"), ("Q3", "verified", True, "excluded_quality"),
    ("", "unknown", True, "pending_quality"), ("Q1", "unknown", True, "pending_quality"),
    ("Q4", "verified", False, "journal"), ("", "unknown", False, "journal"),
])
def test_quality_independent_of_relevance(admission, context, quartile, status, ready, route):
    result = admission.review_candidates([paper(jcr_quartile=quartile, jcr_status=status)], context,
        reviewer=lambda _: {"evaluations": [verdict()]}, journals=[], easyscholar_ready=ready)[0]
    assert result["content_decision"] == "accept" and result["route"] == route


def test_library_evidence_reused_without_network(admission, context):
    rows = admission.review_candidates([paper(jcr_status="unknown", jcr_quartile="")], context,
        reviewer=lambda _: {"evaluations": [verdict()]}, easyscholar_ready=True,
        journals=[{"name": "A journal", "jcr": {"status": "verified", "metrics": [{"quartile": "Q2"}]}}])
    assert rows[0]["route"] == "journal"


def test_preprint_is_separate_but_still_needs_content_admission(admission, context):
    assert run(admission, context, {"evaluations": [verdict()]}, [paper(is_preprint=True)])[0]["route"] == "preprint"
    assert run(admission, context, {"evaluations": [verdict(decision="reject", relation="unrelated")]},
               [paper(is_preprint=True)])[0]["route"] == "rejected"


def test_duplicate_source_records_reviewed_once_with_richer_abstract(admission, context):
    seen = []
    def reviewer(payload):
        seen.extend(payload["candidates"])
        return {"evaluations": [verdict()]}
    rows = admission.review_candidates([paper(abstract=""), paper()], context, reviewer=reviewer,
                                      journals=[], easyscholar_ready=True)
    assert len(seen) == len(rows) == 1
    assert seen[0]["abstract"]


def test_conflicting_generic_exclusion_cannot_silently_delete_specific_direction(admission, context):
    v = verdict(exclusion="primary", exclusion_refs=["exclude:mineral"],
                exclusion_reason_cn="矿物相关", profile_refs=["term:maoc"])
    result = run(admission, context, {"evaluations": [v]})[0]
    assert result["route"] == "pending_content"
    assert result["validation_issue"] == "profile_conflict"


def test_ai_uses_source_references_and_quotes_are_resolved_locally(admission, context):
    v = verdict(evidence_refs=["title"], evidence_quotes=["hallucinated quotation"])
    result = run(admission, context, {"evaluations": [v]})[0]
    assert result["route"] == "journal"
    assert result["evidence_quotes"] == [paper()["title"]]


@pytest.mark.parametrize("changes", [{"decision": []}, {"relation": {}}, {"confidence": []}, {"exclusion": []}])
def test_bad_schema_does_not_crash_review(admission, context, changes):
    assert run(admission, context, {"evaluations": [verdict(**changes)]})[0]["route"] == "pending_content"


def test_title_only_reason_does_not_repeat_ai_invented_methods(admission, context):
    v = verdict(evidence_refs=["title"], reason_cn="研究使用了未提供的实验方法")
    result = run(admission, context, {"evaluations": [v]}, [paper(abstract="")])[0]
    assert result["content_decision"] == "accept"
    assert result["evidence_level"] == "title_only"
    assert "未提供的实验方法" not in result["reason_cn"]
    assert "摘要缺失" in result["reason_cn"]


def test_transfer_claim_without_abstract_is_pending(admission, context):
    v = verdict(relation="transferable", evidence_refs=["title"])
    result = run(admission, context, {"evaluations": [v]}, [paper(abstract="")])[0]
    assert result["route"] == "pending_content"
    assert result["validation_issue"] == "missing_transfer_evidence"
