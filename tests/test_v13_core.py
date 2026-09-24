"""Release contracts for the 13.0 discovery, scoring and reliability model."""

from __future__ import annotations

import json
from datetime import date, datetime
from types import SimpleNamespace

from tests import _data_root  # noqa: F401
from utils import autostart, file_manager
from utils.ai_material_context import build_ai_material_context, load_ai_material_manifests
from utils.app_info import APP_VERSION
from utils.frontier_discovery_v13 import discover_frontier_candidates_v13, normalize_issns, plan_recall
from utils.evidence_cache import EvidenceCache
from utils.frontier_service import (
    _aggregate_frontier_errors,
    _frontier_ai_review_candidate,
    _merge_retry_recall_outcomes,
    merge_frontier_refresh_item,
    update_daily_frontier_v13,
)
from utils.source_registry import (
    RECALL_STRATEGIES,
    SOURCE_REGISTRY,
    recall_outcome,
    record_source_outcome,
    normalize_source_settings,
)
from utils.v13_pipeline import (
    batch_should_run,
    begin_batch,
    checkpoint,
    compact_runtime_store,
    record_batch_sources,
    resume_stage,
    retry_sources,
    stage_payload,
)
from utils.v13_policy import (
    apply_unread_policy,
    compose_axis,
    dynamic_pyramid,
    frontier_display_decision,
    score_frontier_item,
    score_journal_selection,
    score_special_issue,
    special_issue_display_decision,
)


def _profile() -> dict:
    return {
        "terms": [
            {"canonical_en": "soil organic carbon", "text": "soil organic carbon", "weight": 100, "status": "active", "locked": True},
            {"canonical_en": "remote sensing", "text": "remote sensing", "weight": 85, "status": "active"},
            {"canonical_en": "machine learning", "text": "machine learning", "weight": 80, "status": "active"},
        ],
        "positive_seeds": [{"doi": "10.1000/seed", "title": "Seed", "confirmed": True}],
        "confirmed_authors": [{"name": "Researcher", "orcid": "0000-0001-0002-0003", "confirmed": True}],
        "confirmed_topics": [{"name": "soil carbon", "openalex_topic_id": "T1"}],
    }


def test_release_and_six_source_six_lane_contract() -> None:
    assert APP_VERSION == "13.1.2"
    core = {key for key, value in SOURCE_REGISTRY.items() if value.get("group") == "frontier_core"}
    assert core == {"openalex", "crossref", "semantic_scholar", "europe_pmc", "doaj", "arxiv"}
    assert set(RECALL_STRATEGIES) == {
        "keyword", "semantic_related", "citation_network", "confirmed_author_team", "watched_journal", "topic_expansion"
    }
    plan = plan_recall(
        _profile(),
        [{"id": "j1", "name": "Geoderma", "issn": ["0016-7061"], "frontier_priority": "必看"}],
    )
    assert set(plan) == set(RECALL_STRATEGIES)
    assert all(plan[name] for name in RECALL_STRATEGIES)


def test_seeded_recall_requires_explicit_positive_evidence() -> None:
    read_only = {
        "terms": [{"canonical_en": "soil carbon"}],
        "positive_seeds": [{"doi": "10.1000/read", "kind": "read"}],
    }
    plan = plan_recall(read_only, [])
    assert plan["semantic_related"] == []
    assert plan["citation_network"] == []
    assert plan["confirmed_author_team"] == []

    confirmed = {
        **read_only,
        "positive_seeds": [{"doi": "10.1000/favorite", "kind": "favorite"}],
        "confirmed_authors": [{"name": "Researcher", "orcid": "0000-0001-0002-0003", "confirmed": True}],
    }
    confirmed_plan = plan_recall(confirmed, [])
    assert confirmed_plan["semantic_related"][0]["query"] == "10.1000/favorite"
    assert confirmed_plan["citation_network"][0]["query"] == "10.1000/favorite"
    assert confirmed_plan["confirmed_author_team"][0]["query"] == "0000-0001-0002-0003"


def test_no_seed_status_is_explained_as_actionable_not_as_failure() -> None:
    from ui.frontier_page import recall_seed_guidance

    guidance = recall_seed_guidance(
        {
            "semantic_related": {"status": "NO_SEED"},
            "citation_network": {"status": "NO_SEED"},
            "confirmed_author_team": {"status": "NO_SEED"},
        }
    )
    assert "尚无已确认种子" in guidance
    assert "相关" in guidance
    assert "确认作者" in guidance
    assert recall_seed_guidance({"semantic_related": {"status": "SUCCESS"}}) == ""


def test_frontier_ai_gate_and_error_aggregation_keep_batches_small_and_status_concise() -> None:
    boundary = {
        "candidate_state": "visible",
        "relevance_axis": {"base_score": 36},
        "value_axis": {"base_score": 34},
    }
    distant = {
        "candidate_state": "visible",
        "relevance_axis": {"base_score": 3},
        "value_axis": {"base_score": 2},
    }
    assert _frontier_ai_review_candidate(boundary)
    assert not _frontier_ai_review_candidate(distant)

    errors = _aggregate_frontier_errors(
        [
            {"source": "semantic_scholar", "type": "rate_limit", "error": "HTTP 429", "query": "a"},
            {"source": "semantic_scholar", "type": "rate_limit", "error": "HTTP 429", "query": "b"},
        ]
    )
    assert len(errors) == 1
    assert errors[0]["occurrences"] == 2
    assert errors[0]["queries"] == ["a", "b"]

    merged = _merge_retry_recall_outcomes(
        {
            "keyword": {"status": "SUCCESS", "count": 12, "error": ""},
            "confirmed_author_team": {"status": "NO_SEED", "count": 0, "error": ""},
        },
        {
            "keyword": {"status": "FAILED", "count": 0, "error": "offline"},
            "confirmed_author_team": {"status": "FAILED", "count": 0, "error": ""},
        },
        [{"recall_strategies": ["keyword"]}, {"recall_strategy": "keyword"}],
        ["keyword"],
    )
    assert merged["keyword"]["status"] == "SUCCESS"
    assert merged["keyword"]["count"] == 2
    assert merged["keyword"]["retry_status"] == "FAILED"
    assert merged["confirmed_author_team"]["status"] == "NO_SEED"


def test_ai_absence_is_null_and_rules_are_normalized() -> None:
    axis = compose_axis({"a": 33, "b": 0}, {"a": 33, "b": 33}, ai_adjustment=None)
    assert axis["ai_adjustment"] is None
    assert axis["mode"] == "rules_normalized"
    assert axis["total"] == 50
    assert axis["valid"]

    partial = compose_axis({"a": 24, "b": None}, {"a": 33, "b": 33}, ai_adjustment=None)
    assert partial["raw_base_score"] == 24
    assert partial["known_base_max"] == 33
    assert partial["base_score"] == 48
    assert partial["total"] == 73
    assert partial["coverage"] == 0.5


def test_selection_forms_a_rule_only_pyramid_from_similar_papers_and_known_quality() -> None:
    paper = {
        "id": "rice-soc",
        "title": "Net effects of rice cultivation on soil organic carbon using causal forests",
        "summary": "We estimate heterogeneous soil carbon effects across Chinese cropland.",
        "keywords": ["soil organic carbon; rice cultivation; causal forest"],
    }
    journal = {
        "id": "still",
        "name": "Soil and Tillage Research",
        "publisher": "Elsevier",
        "jcr_status": "verified",
        "jcr_quartile": "Q1",
        "similar_papers": [
            {"title": "Rice cultivation and soil organic carbon sequestration"},
            {"title": "Cropping systems alter organic carbon in agricultural soils"},
        ],
    }
    profile = {
        "terms": [
            {"canonical_en": "soil organic carbon", "translation_zh": "土壤有机碳", "status": "active"},
            {"canonical_en": "causal forest", "translation_zh": "因果森林", "status": "active"},
        ]
    }
    result = score_journal_selection(paper, journal, profile, {})
    assert result["fit_axis"]["ai_adjustment"] is None
    assert result["strategy_axis"]["ai_adjustment"] is None
    assert result["fit_score"] >= 55
    assert result["strategy_score"] >= 70
    assert result["tier"] in {"主投", "稳妥", "探索"}


def test_selection_ai_batches_open_circuit_after_repeated_transport_failure(monkeypatch) -> None:
    from utils import journal_selection_service as selection_service
    from utils.ai_service import DeepSeekRequestError

    calls = []

    def offline(*_args, **_kwargs):
        calls.append(1)
        raise DeepSeekRequestError("无法连接 DeepSeek，请检查网络、API 地址或代理设置。")

    monkeypatch.setattr(selection_service, "_assess_verified_journals_with_ai", offline)
    journals = [
        {"id": f"j-{index}", "identity_evidence": {"verified": True}}
        for index in range(20)
    ]
    _assessments, errors = selection_service._assess_verified_journals_in_batches(
        {"title": "Paper", "summary": "Summary"}, journals, {}, emit=lambda *_args: None
    )
    assert len(calls) == 2
    assert len(errors) == 2
    assert errors[-1]["circuit_open"] is True


def test_daily_hard_gate_and_dynamic_pyramid_have_no_quota() -> None:
    raw = {
        "id": "paper-1",
        "title": "Remote sensing and machine learning mapping of soil organic carbon",
        "abstract": "A global high resolution soil organic carbon mapping framework with remote sensing, machine learning and uncertainty. " * 3,
        "author_keywords": ["soil organic carbon", "remote sensing", "machine learning"],
        "authors": ["A", "B"],
        "journal": "Geoderma",
        "publisher": "Elsevier",
        "published_date": "2026-09-10",
        "doi": "10.1000/frontier",
        "source_names": ["openalex", "crossref"],
        "recall_strategies": ["keyword", "semantic_related"],
        "jcr_quartile": "Q1",
        "jcr_status": "verified",
    }
    scored = score_frontier_item(raw, _profile(), today=date(2026, 9, 17))
    assert scored["relevance_axis"]["ai_adjustment"] is None
    assert scored["value_axis"]["ai_adjustment"] is None
    assert frontier_display_decision(scored, jcr_service_available=True)["visible"]
    items = []
    for index in range(25):
        item = dict(scored, id=f"p-{index}", relevance_score=82, research_value_score=78, pyramid_level="A")
        items.append(item)
    assert len(dynamic_pyramid(items)) == 25

    incomplete = dict(scored, authors=[])
    decision = frontier_display_decision(incomplete, jcr_service_available=True)
    assert not decision["visible"] and "authors" in decision["missing_fields"]
    low_jcr = dict(scored, jcr_quartile="Q3", jcr_status="verified")
    assert frontier_display_decision(low_jcr, jcr_service_available=True)["state"] == "content_deleted_fingerprint_kept"


def test_frontier_backfill_uses_only_same_day_relative_rank() -> None:
    rows = [
        {"id": "old", "relevance_score": 69, "research_value_score": 69, "first_seen_date": "2026-09-16"},
        {"id": "today-1", "relevance_score": 64, "research_value_score": 58, "first_seen_date": "2026-09-17"},
        {"id": "today-2", "relevance_score": 62, "research_value_score": 57, "first_seen_date": "2026-09-17"},
        {"id": "today-3", "relevance_score": 60, "research_value_score": 55, "first_seen_date": "2026-09-17"},
        {"id": "today-4", "relevance_score": 58, "research_value_score": 50, "first_seen_date": "2026-09-17"},
    ]
    selected = dynamic_pyramid(rows, today=date(2026, 9, 17))
    backfilled = [value for value in selected if value.get("backfill_rule")]
    assert [value["id"] for value in backfilled] == ["today-1"]
    assert backfilled[0]["backfill_rule"] == "same_day_relative_rank"
    assert backfilled[0]["daily_relative_rank"] == 1


def test_issn_normalization_handles_library_and_api_shapes() -> None:
    assert normalize_issns(
        {
            "issn": "ISSN 00167061; 1872-9831",
            "issn_l": "0016-7061",
            "electronic_issn": {"value": "2049 3630"},
        }
    ) == ["0016-7061", "1872-9831", "2049-3630"]


def test_frontier_auto_ai_finishes_before_dynamic_pyramid(monkeypatch, tmp_path) -> None:
    from utils import ai_service, frontier_discovery_v13, frontier_service, v13_policy

    now = datetime(2026, 9, 17, 8)
    raw = {
        "id": "paper-ai",
        "title": "Remote sensing and machine learning mapping of soil organic carbon",
        "abstract": "A global high resolution soil organic carbon mapping framework with remote sensing, machine learning and uncertainty. " * 3,
        "author_keywords": ["soil organic carbon", "remote sensing", "machine learning"],
        "authors": ["A", "B"],
        "journal": "Geoderma",
        "publisher": "Elsevier",
        "published_date": "2026-09-16",
        "doi": "10.1000/frontier-ai",
        "source_names": ["openalex"],
        "recall_strategies": ["keyword"],
        "jcr_quartile": "Q1",
        "jcr_status": "verified",
    }
    discovered = {
        "items": [raw], "successful_sources": ["openalex"], "failed_sources": [],
        "errors": [], "source_health": {}, "recall_outcomes": {},
    }
    monkeypatch.setattr(frontier_discovery_v13, "discover_frontier_candidates_v13", lambda *_args, **_kwargs: discovered)
    monkeypatch.setattr(frontier_service, "_enrich_frontier_quality_with_easyscholar", lambda items, *_args, **_kwargs: items)
    monkeypatch.setattr(frontier_service, "is_easyscholar_ready", lambda: False)
    monkeypatch.setattr(file_manager, "load_v13_runtime", lambda: {})
    monkeypatch.setattr(file_manager, "save_v13_runtime", lambda _value: None)
    settings = file_manager.load_app_settings()
    settings["ai"] = {**settings.get("ai", {}), "auto_frontier_rerank": True}
    monkeypatch.setattr(file_manager, "load_app_settings", lambda: settings)
    monkeypatch.setattr(ai_service, "is_deepseek_ready", lambda _feature="": True)
    order = []

    def rerank(_profile, items, progress=None):
        order.append("ai")
        assert items and isinstance(items[0].get("relevance_axis"), dict)
        axis = {"adjustment": 34, "confidence": "high", "reason": "输入证据充分", "evidence_refs": ["title", "abstract"]}
        return {"ranked": [{"id": "paper-ai", "ai_axis_payload": {"provider": "test", "model": "test", "axes": {"relevance": axis, "value": axis}}, "summary_cn": "测试"}]}

    monkeypatch.setattr(ai_service, "rerank_frontier_with_ai", rerank)
    original_pyramid = v13_policy.dynamic_pyramid

    def pyramid(items, **kwargs):
        order.append("pyramid")
        values = list(items)
        assert values[0]["ai_review_status"] == "success"
        assert values[0]["ai_axis_payload"]["axes"]["relevance"]["adjustment"] == 34
        return original_pyramid(values, **kwargs)

    monkeypatch.setattr(v13_policy, "dynamic_pyramid", pyramid)
    result = update_daily_frontier_v13(
        {"profile": _profile(), "items": []},
        [{"id": "j1", "name": "Geoderma", "frontier_priority": "必看"}],
        now=now,
    )
    assert order == ["ai", "pyramid"]
    assert result["data"]["items"][0]["ai_review_status"] == "success"


def test_unread_policy_deletes_content_but_keeps_fingerprint_when_disabled() -> None:
    previous = {"id": "old", "title": "Old", "doi": "10.1000/old", "status": "new", "first_seen_date": "2026-09-16"}
    current = {"id": "today", "title": "Today", "doi": "10.1000/today", "status": "new", "first_seen_date": "2026-09-17"}
    visible, fingerprints = apply_unread_policy([previous, current], today=date(2026, 9, 17), retain_unread=False)
    assert [value["id"] for value in visible] == ["today"]
    assert fingerprints[0]["fingerprint"] == "doi:10.1000/old"


def test_special_issue_can_pass_hard_gate_without_ai() -> None:
    issue = score_special_issue(
        {
            "title": "Remote sensing and machine learning for soil organic carbon",
            "journal": "Geoderma",
            "publisher": "Elsevier",
            "deadline": "2027-06-30",
            "official_url": "https://example.org/call",
            "scope_text": "Remote sensing, machine learning and soil organic carbon mapping at regional and global scales.",
            "publisher_identity_verified": True,
            "jcr_quartile": "Q1",
            "jcr_status": "verified",
            "call_status": "open",
        },
        _profile(),
        today=date(2026, 9, 17),
    )
    assert issue["relevance_axis"]["ai_adjustment"] is None
    assert issue["relevance_score"] >= 60
    assert special_issue_display_decision(issue)["visible"]


def test_title_only_special_issue_can_be_scored_without_full_scope() -> None:
    issue = score_special_issue(
        {
            "title": "Remote sensing and machine learning for soil organic carbon",
            "journal": "Geoderma",
            "publisher": "Elsevier",
            "deadline": "2027-06-30",
            "official_url": "https://aggregator.example/call",
            "source_evidence": [{"source": "research_collection_radar", "is_aggregator": True}],
            "jcr_quartile": "Q1",
            "jcr_status": "verified",
            "call_status": "open",
        },
        _profile(),
        today=date(2026, 9, 17),
    )
    decision = special_issue_display_decision(issue)
    assert issue["relevance_score"] >= 60
    assert "scope_or_keywords" not in decision["missing_fields"]
    assert decision["visible"]


def test_third_party_elsevier_gets_distinct_publisher_priority() -> None:
    common = {
        "title": "Soil organic carbon special issue",
        "journal": "Journal",
        "deadline": "2027-06-30",
        "official_url": "https://aggregator.example/call",
        "source_evidence": [{"source": "third_party", "is_aggregator": True}],
        "call_status": "open",
    }
    elsevier = score_special_issue({**common, "publisher": "Elsevier"}, _profile(), today=date(2026, 9, 17))
    wiley = score_special_issue({**common, "publisher": "Wiley"}, _profile(), today=date(2026, 9, 17))
    assert elsevier["opportunity_axis"]["components"]["journal_publisher_quality"] == 10
    assert wiley["opportunity_axis"]["components"]["journal_publisher_quality"] == 8


def test_third_party_discovery_urls_satisfy_source_link_hard_gate() -> None:
    issue = score_special_issue(
        {
            "title": "Remote sensing and machine learning for soil organic carbon",
            "journal": "Geoderma",
            "publisher": "Elsevier",
            "deadline": "2027-06-30",
            "discovery_urls": ["https://aggregator.example/call"],
            "source_evidence": [
                {"source": "third_party", "is_aggregator": True, "url": "https://aggregator.example/call"}
            ],
            "jcr_quartile": "Q1",
            "jcr_status": "verified",
            "call_status": "open",
        },
        _profile(),
        today=date(2026, 9, 17),
    )
    decision = special_issue_display_decision(issue)
    assert "source_url" not in decision["missing_fields"]
    assert decision["visible"]


def test_retired_elsevier_official_source_cannot_be_reenabled() -> None:
    settings = normalize_source_settings({"elsevier": {"enabled": True}})
    assert SOURCE_REGISTRY["elsevier"]["key_mode"] == "none"
    assert settings["elsevier"]["enabled"] is False


def test_selection_unknowns_are_excluded_only_when_user_sets_a_strict_division() -> None:
    result = score_journal_selection(
        {"id": "p1", "title": "Soil carbon mapping", "summary": "Remote sensing mapping of soil carbon."},
        {"id": "j1", "name": "Journal", "scope": "Soil carbon and remote sensing", "active": None},
        _profile(),
        {"jcr_quartiles": ["Q1"]},
    )
    assert result["objective_excluded"]
    assert result["constraint_state"] == "hard_excluded"
    unrestricted = score_journal_selection(
        {"id": "p1", "title": "Soil carbon mapping", "summary": "Remote sensing mapping of soil carbon."},
        {"id": "j1", "name": "Journal", "scope": "Soil carbon and remote sensing", "active": None},
        _profile(),
        {},
    )
    assert not unrestricted["objective_excluded"]
    assert unrestricted["constraint_state"] == "eligible"
    assert result["strategy_axis"]["components"]["quality_target"] is None
    assert result["fit_axis"]["ai_adjustment"] is None


def test_source_health_and_retry_checkpoints_preserve_partial_progress() -> None:
    now = datetime(2026, 9, 17, 8)
    healthy = record_source_outcome({}, at=now, success=True, result_count=3, successful_watermark="2026-09-17")
    failed = record_source_outcome(healthy, at=now, success=False, error_type="network", error="offline")
    assert failed["successful_watermark"] == "2026-09-17"
    assert failed["state"] == "DEGRADED"
    assert recall_outcome(has_seed=False, succeeded=True)["status"] == "NO_SEED"
    assert recall_outcome(has_seed=True, succeeded=False)["status"] == "FAILED"
    assert recall_outcome(has_seed=True, succeeded=True, count=0)["status"] == "SUCCESS_EMPTY"

    store, batch_id, resumed = begin_batch({}, task="daily_frontier", inputs={"profile": "x"}, now=now)
    assert not resumed and resume_stage(store, batch_id) == "recall"
    store = checkpoint(store, batch_id, "recall", state="success", payload={"items": []}, now=now)
    assert resume_stage(store, batch_id) == "enrich"
    store = record_batch_sources(store, batch_id, successful=["openalex"], failed=["crossref"])
    store = checkpoint(store, batch_id, "commit", state="partial", now=now)
    assert batch_should_run(store, task="daily_frontier", day=now.date())
    assert retry_sources(store, task="daily_frontier", day=now.date()) == ["crossref"]

    other, other_batch, _ = begin_batch(store, task="special_issue", inputs={"profile": "x"}, now=now)
    other = record_batch_sources(other, other_batch, successful=[], failed=["wiley"])
    other = checkpoint(other, other_batch, "commit", state="partial", now=now)
    assert retry_sources(other, task="daily_frontier", day=now.date()) == ["crossref"]
    assert retry_sources(other, task="special_issue", day=now.date()) == ["wiley"]
    assert retry_sources(other, task="daily_frontier", day=date(2026, 9, 18)) is None


def test_discovery_stops_repeating_queries_after_same_source_network_failure(tmp_path) -> None:
    cache = EvidenceCache(tmp_path / "circuit.sqlite")
    cache.initialize()
    settings = normalize_source_settings({})
    calls = []

    def offline(strategy, source, seed, query, start, today, config):
        calls.append((strategy, source, query))
        raise RuntimeError("network: offline")

    result = discover_frontier_candidates_v13(
        {
            "terms": [
                {"canonical_en": "soil organic carbon"},
                {"canonical_en": "remote sensing"},
                {"canonical_en": "causal forest"},
                {"canonical_en": "cropping system"},
            ]
        },
        [],
        cache=cache,
        source_settings=settings,
        source_health={},
        now=datetime(2026, 9, 17, 8),
        source_filter=["openalex"],
        fetcher=offline,
    )
    assert len(calls) == 2
    assert result["failed_sources"] == ["openalex"]
    assert result["errors"][0]["query"] == "soil organic carbon"


def test_runtime_journal_keeps_only_the_payload_needed_for_resume() -> None:
    now = datetime(2026, 9, 17, 8)
    store, batch_id, _resumed = begin_batch({}, task="daily_frontier", inputs={"profile": "x"}, now=now)
    recall_payload = {"items": [{"id": "paper-1", "abstract": "x" * 2000}]}
    enrich_payload = {"items": [{"id": "paper-1", "abstract": "y" * 2000}]}

    store = checkpoint(store, batch_id, "recall", state="success", payload=recall_payload, now=now)
    assert stage_payload(store, batch_id, "recall") == recall_payload
    store = checkpoint(store, batch_id, "enrich", state="success", payload=enrich_payload, now=now)
    assert stage_payload(store, batch_id, "recall") is None
    assert stage_payload(store, batch_id, "enrich") == enrich_payload

    store = checkpoint(store, batch_id, "commit", state="partial", now=now)
    assert all(
        "payload" not in entry
        for entry in compact_runtime_store(store)["batches"][batch_id]["stages"].values()
    )


def test_runtime_journal_is_not_copied_into_portable_backups() -> None:
    assert file_manager.V13_RUNTIME_FILE.name not in file_manager.BACKUP_FILE_NAMES


def test_v13_refresh_merge_never_rewrites_dual_score_to_zero() -> None:
    incoming = {
        "id": "x", "scoring_version": "frontier-dual-axis-13.0", "score": 76,
        "relevance_score": 80, "research_value_score": 72, "ai_adjustment": {"relevance": 20, "value": 18},
    }
    merged = merge_frontier_refresh_item(incoming, {"id": "x", "status": "saved", "score": 0}, "2026-09-17")
    assert merged["score"] == 76
    assert merged["ai_adjustment"] == {"relevance": 20, "value": 18}
    assert merged["status"] == "saved"


def test_local_material_context_is_minimised_sanitised_and_audited(monkeypatch) -> None:
    settings = file_manager.load_app_settings()
    settings["ai"].update(
        {"local_material_access": True, "local_material_project_isolation": True, "local_material_use_manifest": True}
    )
    file_manager.save_app_settings(settings)
    monkeypatch.setattr(file_manager, "load_inspirations", lambda: [{"id": "n1", "text": "Unrelated note", "project_id": "other"}])
    monkeypatch.setattr(file_manager, "load_rejection_archive", lambda: [])
    package = build_ai_material_context(
        "journal_selection",
        "soil carbon",
        paper={
            "id": "paper-1", "project_id": "project-a", "title": "Soil carbon",
            "summary": "Contact me at secret@example.com or 13812345678. Soil carbon mapping.",
            "keywords": ["soil carbon"], "journals": [], "files": [],
        },
        include_pdf=False,
    )
    encoded = json.dumps(package["materials"], ensure_ascii=False)
    assert "secret@example.com" not in encoded
    assert "13812345678" not in encoded
    assert "Unrelated note" not in encoded
    manifests = load_ai_material_manifests()
    assert manifests and manifests[-1]["task"] == "journal_selection"
    assert "content" not in json.dumps(manifests[-1], ensure_ascii=False)


def test_autostart_creation_includes_delay_and_requires_real_probe(monkeypatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(autostart.sys, "platform", "win32")
    monkeypatch.setattr(autostart, "_run_schtasks", lambda *args: calls.append(args) or SimpleNamespace(returncode=0, stderr="", stdout=""))
    monkeypatch.setattr(autostart, "_scheduled_task_exists", lambda: True)
    monkeypatch.setattr(autostart, "_remove_registry_value", lambda: None)
    monkeypatch.setattr(autostart, "probe_startup", lambda: {"healthy": True, "exit_code": 0})
    status = autostart.set_autostart(True)
    create = next(value for value in calls if "/Create" in value)
    assert create[create.index("/DELAY") + 1] == "0000:30"
    assert status["mode"] == "task_scheduler"
    assert status["probe"]["exit_code"] == 0
