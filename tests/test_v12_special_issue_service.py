"""Admission, normalization and deduplication contracts for journal calls."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from utils.evidence_cache import EvidenceCache

from utils.special_issue_service import (
    merge_special_issue_records,
    normalize_special_issue,
    special_issue_dedupe_key,
    enrich_special_issue_journal,
    build_special_issue_notifications,
    special_issue_refresh_due,
    refresh_special_issues,
    verify_special_issue,
)


def _normalize(**overrides):
    raw = {
        "title": "Digital Soil Mapping and Carbon Monitoring",
        "type": "Special Issue",
        "journal": "Geoderma",
        "issn": "0016-7061",
        "publisher": "Elsevier",
        "scope": "Remote sensing, spatial modelling and soil carbon assessment.",
        "deadline": "2027-06-30",
        "url": "https://example.org/calls/soil-carbon",
        **overrides,
    }
    return normalize_special_issue(raw, source="fixture", fetched_at="2026-08-31T10:00:00", today=date(2026, 8, 31))


def test_supported_collection_types_normalize_to_four_explicit_kinds() -> None:
    expected = {
        "Special Issue": "special_issue",
        "Topical Collection": "topical_collection",
        "Research Topic": "research_topic",
        "Article Collection": "article_collection",
    }
    for raw_type, normalized_type in expected.items():
        item = _normalize(type=raw_type)
        assert item is not None and item["type"] == normalized_type
        assert item["deadline"] == "2027-06-30"
        assert item["issns"] == ["0016-7061"]


def test_unsupported_calls_are_rejected_but_expired_calls_remain_auditable() -> None:
    rejected = (
        {"title": "International Conference Special Session"},
        {"title": "Workshop on Soil Carbon"},
        {"title": "Call for book chapters"},
        {"scope": "This is a rolling call with no fixed closing date", "deadline": ""},
        {"scope": "Manuscripts are accepted by invitation only"},
    )
    assert all(_normalize(**raw) is None for raw in rejected)
    assert _normalize(deadline="2026-08-01") is not None


def test_unknown_metadata_is_retained_instead_of_invented() -> None:
    item = _normalize(publisher="", issn="", fee_mode="", jcr=None, cas=None)
    assert item is not None
    assert item["publisher"] == "unknown"
    assert item["fee_mode"] == "unknown"
    assert item["jcr"] == {}
    assert item["cas"] == {}


def test_same_official_url_merges_and_retains_every_source_evidence() -> None:
    first = _normalize(source_name="official")
    second = normalize_special_issue(
        {
            "title": "Digital soil mapping & carbon monitoring",
            "type": "special_issue",
            "journal": "Geoderma",
            "deadline": "30 June 2027",
            "official_url": "https://example.org/calls/soil-carbon?utm_source=feed",
            "scope_text": "A longer full scope about remote sensing, machine learning and soil carbon assessment.",
        },
        source="aggregator",
        fetched_at="2026-08-31T11:00:00",
        today=date(2026, 8, 31),
    )
    assert first is not None and second is not None

    merged = merge_special_issue_records([first, second])
    assert len(merged) == 1
    assert len(merged[0]["source_evidence"]) == 2
    assert "longer full scope" in merged[0]["scope_text"]


def test_same_issn_title_and_deadline_merge_when_urls_differ() -> None:
    first = _normalize(url="https://publisher.example/one")
    second = _normalize(url="https://aggregator.example/two")
    assert first is not None and second is not None
    assert special_issue_dedupe_key(first) != special_issue_dedupe_key(second)
    assert len(merge_special_issue_records([first, second])) == 1


def test_official_page_must_match_title_journal_issn_and_future_deadline(tmp_path, monkeypatch) -> None:
    from utils import special_issue_service as service

    item = _normalize()
    assert item is not None
    page = """
    <html><body><h1>Digital Soil Mapping and Carbon Monitoring</h1>
    <p>A special issue of Geoderma (ISSN 0016-7061).</p>
    <p>Submission deadline: 30 June 2027</p>
    <section>Remote sensing, spatial modelling, machine learning and soil carbon assessment.</section>
    </body></html>
    """
    monkeypatch.setattr(service, "_fetch_official_page", lambda _url: page)
    cache = EvidenceCache(tmp_path / "cache.sqlite")
    cache.initialize()

    verified = verify_special_issue(item, now=datetime(2026, 8, 31, 12), cache=cache)

    assert verified["verification_status"] == "official_verified"
    assert verified["official_checked_at"] == "2026-08-31T12:00:00"
    assert verified["official_page_hash"]
    assert "machine learning" in verified["scope_text"]
    assert cache.get_special_issue_verification(item["id"])["status"] == "official_verified"


def test_fresh_aggregator_record_survives_official_failure_as_unverified(tmp_path, monkeypatch) -> None:
    from utils import special_issue_service as service

    item = _normalize(is_aggregator=True, published_at="2026-08-30T10:00:00")
    assert item is not None
    monkeypatch.setattr(service, "_fetch_official_page", lambda _url: (_ for _ in ()).throw(OSError("offline")))
    cache = EvidenceCache(tmp_path / "cache.sqlite")
    cache.initialize()

    result = verify_special_issue(item, now=datetime(2026, 8, 31, 12), cache=cache)

    assert result["verification_status"] == "aggregator_unverified"
    assert result["status"] != "closed"
    assert result["official_retry_after"]


def test_official_closed_and_changed_deadline_states_are_auditable(tmp_path, monkeypatch) -> None:
    from utils import special_issue_service as service

    item = _normalize(status="saved")
    assert item is not None
    cache = EvidenceCache(tmp_path / "cache.sqlite")
    cache.initialize()
    monkeypatch.setattr(
        service,
        "_fetch_official_page",
        lambda _url: "Digital Soil Mapping and Carbon Monitoring | Geoderma | ISSN 0016-7061 | Submission deadline: 31 August 2027 | Submission closed",
    )

    result = verify_special_issue(item, now=datetime(2026, 8, 31, 12), cache=cache)

    assert result["verification_status"] == "closed"
    assert result["status"] == "saved"
    assert result["call_status"] == "closed"
    assert result["deadline"] == "2027-08-31"
    assert result["deadline_history"][-1]["previous"] == "2027-06-30"
    assert result["deadline_history"][-1]["current"] == "2027-08-31"


def test_enrichment_prefers_issn_library_evidence_and_preserves_unknowns() -> None:
    item = _normalize(publisher="", fee_mode="", jcr=None, cas=None)
    assert item is not None
    enriched = enrich_special_issue_journal(
        item,
        [
            {
                "id": "j1",
                "name": "Geoderma",
                "issn": "0016-7061",
                "publisher": "Elsevier",
                "fee_mode": "hybrid",
                "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
                "easyscholar": {"cas_upgrade": "1区", "checked_at": "2026-08-30"},
            }
        ],
        easyscholar_ready=False,
    )

    assert enriched["publisher"] == "Elsevier"
    assert enriched["fee_mode"] == "hybrid"
    assert enriched["jcr"]["metrics"][0]["quartile"] == "Q1"
    assert enriched["cas"]["cas_upgrade"] == "1区"
    assert enriched["journal_evidence"]["source"] == "journal_library"


def test_refresh_due_boundary_is_exactly_24_hours() -> None:
    now = datetime(2026, 8, 31, 12, 0, 0)
    assert not special_issue_refresh_due((now - timedelta(hours=23, minutes=59)).isoformat(), now=now)
    assert special_issue_refresh_due((now - timedelta(hours=24)).isoformat(), now=now)
    assert special_issue_refresh_due("", now=now)
    assert special_issue_refresh_due("not-a-date", now=now)


def test_new_high_match_notification_starts_at_80_and_is_deduplicated() -> None:
    before = {"items": [], "notification_log": []}
    after = {
        "items": [
            {"id": "low", "title": "Score 79", "status": "unread", "deadline": "2027-06-30", "match": {"score": 79, "formal": True}},
            {"id": "high", "title": "Score 80", "status": "unread", "deadline": "2027-06-30", "match": {"score": 80, "formal": True}},
        ],
        "notification_log": [],
    }
    result = build_special_issue_notifications(before, after, today=date(2026, 8, 31))
    assert [(row["kind"], row["issue_id"]) for row in result] == [("new_high_match", "high")]
    after["notification_log"] = [{"id": result[0]["id"]}]
    assert build_special_issue_notifications(before, after, today=date(2026, 8, 31)) == []


def test_deadline_and_closed_changes_notify_regardless_of_score() -> None:
    before = {
        "items": [
            {"id": "si-1", "title": "Changed", "deadline": "2027-05-01", "verification_status": "official_verified", "match": {"score": 20}},
            {"id": "si-2", "title": "Closed", "deadline": "2027-06-01", "verification_status": "official_verified", "match": {"score": 10}},
        ],
        "notification_log": [],
    }
    after = {
        "items": [
            {"id": "si-1", "title": "Changed", "deadline": "2027-05-20", "verification_status": "official_verified", "match": {"score": 20}},
            {"id": "si-2", "title": "Closed", "deadline": "2027-06-01", "verification_status": "closed", "match": {"score": 10}},
        ],
        "notification_log": [],
    }
    assert {row["kind"] for row in build_special_issue_notifications(before, after, today=date(2026, 8, 31))} == {"deadline_changed", "closed"}


def test_refresh_pipeline_discovers_verifies_enriches_and_matches_without_persisting(tmp_path) -> None:
    class Source:
        source_id = "fixture"

        def fetch(self, *, since, progress=None):
            assert since <= datetime(2026, 8, 31, 12)
            if progress:
                progress("fixture", 100)
            return [
                {
                    "title": "Soil carbon mapping collection",
                    "type": "Special Issue",
                    "journal": "Geoderma",
                    "publisher": "Elsevier",
                    "deadline": "2027-06-30",
                    "official_url": "https://example.org/call",
                    "scope_text": "Remote sensing and soil organic carbon mapping.",
                    "scope_is_complete": True,
                    "scope_status": "full",
                    "scope_paragraphs": [
                        {"id": "scope-1", "text": "Remote sensing and soil organic carbon mapping."}
                    ],
                }
            ]

    def verify(item, *, now, cache):
        return {**item, "verification_status": "official_verified", "official_checked_at": now.isoformat()}

    def enrich(item, _library, *, easyscholar_ready):
        assert not easyscholar_ready
        return {**item, "jcr_quartile": "Q1", "cas_quartile": "1"}

    def match(_item, _profiles, _progress):
        return {
            "score": 86,
            "reason": "主题和方法匹配",
            "risk": "需要维持土壤碳主线。",
            "branch": "scope:soil_carbon_protection",
            "relation": "core",
            "evidence_refs": ["scope-1"],
            "exclusion_assessment": {
                "status": "none",
                "reason": "未触发排除方向。",
                "evidence_refs": [],
            },
            "scope_coverage": ["scope-1"],
            "matched_terms": ["soil organic carbon"],
            "paper_matches": [],
            "model": "fixture",
            "ai_axis_payload": {
                "provider": "fixture",
                "model": "fixture",
                "axes": {
                    "relevance": {
                        "adjustment": 28,
                        "confidence": "high",
                        "reason": "方向与征稿范围高度匹配",
                        "evidence_refs": ["title", "scope_text"],
                    },
                    "opportunity": {
                        "adjustment": 24,
                        "confidence": "medium",
                        "reason": "征稿信息完整且可执行",
                        "evidence_refs": ["journal", "source_evidence"],
                    },
                },
            },
        }

    cache = EvidenceCache(tmp_path / "pipeline.sqlite")
    result = refresh_special_issues(
        sources=[Source()],
        now=datetime(2026, 8, 31, 12),
        store={"items": [], "last_checked_at": ""},
        journal_library=[],
        research_profile={"terms": [{"canonical_en": "soil organic carbon", "weight": 90}]},
        papers=[],
        cache=cache,
        ai_matcher=match,
        verifier=verify,
        enricher=enrich,
        easyscholar_ready=False,
        persist=False,
    )

    from utils.special_issue_policy import evaluate_special_issue

    assert result["stats"]["raw"] == 1
    assert result["stats"]["discovered"] == 1
    assert result["stats"]["items"] == 1
    expected_visible = int(
        evaluate_special_issue(
            result["store"]["items"][0], now=datetime(2026, 8, 31, 12), view="recommended"
        )["visible"]
    )
    assert result["stats"]["eligible"] == expected_visible
    assert result["store"]["items"][0]["match"]["score"] == 86
    assert result["store"]["items"][0]["match"]["formal"]
    # v13 notifications require the visible dual-axis hard gate; a legacy
    # scalar AI score alone is no longer enough to notify.
    assert result["notifications"] == []

    def must_not_run(*_args):
        raise AssertionError("unchanged scope/profile should reuse the previous AI score")

    second = refresh_special_issues(
        sources=[Source()],
        now=datetime(2026, 9, 1, 12),
        store=result["store"],
        journal_library=[],
        research_profile={"terms": [{"canonical_en": "soil organic carbon", "weight": 90}]},
        papers=[],
        cache=cache,
        ai_matcher=must_not_run,
        verifier=verify,
        enricher=enrich,
        easyscholar_ready=False,
        persist=False,
    )
    assert second["store"]["items"][0]["match"]["score"] == 86
    assert "match_error" not in second["store"]["items"][0]
