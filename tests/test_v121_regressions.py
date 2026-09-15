"""Regression contracts for the v12.1 frontier, submission and call fixes."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from utils.evidence_cache import EvidenceCache


def test_frontier_discovery_accepts_positive_seed_without_active_terms(tmp_path: Path, monkeypatch) -> None:
    from utils import frontier_service

    monkeypatch.setattr(
        frontier_service,
        "_fetch_v12_source",
        lambda source, query, start, today, api_key: [
            {
                "id": "seed-result",
                "title": "A related paper",
                "abstract": "soil carbon mapping",
                "journal": "Geoderma",
                "published_date": today.isoformat(),
            }
        ],
    )
    cache = EvidenceCache(tmp_path / "frontier.sqlite")
    cache.initialize()

    result = frontier_service.discover_frontier_candidates(
        {
            "active_terms": [],
            "positive_seeds": [{"title": "Existing soil carbon paper"}],
            "sources": {"crossref": {"enabled": True}},
        },
        cache=cache,
    )

    assert [item["source_id"] for item in result] == ["seed-result"]


def test_frontier_reset_removes_only_the_requested_reading_states() -> None:
    from utils.frontier_service import reset_frontier_history

    store = {
        "items": [
            {"id": "keep-new", "status": "new"},
            {"id": "keep-liked", "status": "liked"},
            {"id": "drop-saved", "status": "saved"},
            {"id": "drop-read", "status": "read"},
            {"id": "drop-broad", "status": "deprioritized"},
            {"id": "drop-hidden", "status": "dismissed"},
        ],
        "feedback_events": [
            {"item_id": "drop-read", "action": "read"},
            {"item_id": "keep-liked", "action": "liked"},
        ],
    }

    cleaned = reset_frontier_history(store)

    assert [item["id"] for item in cleaned["items"]] == ["keep-new", "keep-liked"]
    assert cleaned["feedback_events"] == [{"item_id": "keep-liked", "action": "liked"}]


def test_journal_name_alias_normalization_blocks_ampersand_rejection() -> None:
    from utils.journal_selection_service import canonical_journal_name

    assert canonical_journal_name("Soil & Tillage Research") == canonical_journal_name(
        "Soil and Tillage Research"
    )
    assert canonical_journal_name("Journal of Soil Science") != canonical_journal_name("Soil Science")


def test_paper_specific_exclusion_is_persisted_with_the_journal_name(tmp_path: Path, monkeypatch) -> None:
    from utils import file_manager

    feedback_path = tmp_path / "selection_feedback.json"
    monkeypatch.setattr(file_manager, "SELECTION_FEEDBACK_FILE", feedback_path)

    file_manager.set_journal_selection_feedback(
        "paper-1",
        "journal-1",
        "不适合",
        ["soil carbon"],
        journal_name="Soil & Tillage Research",
    )

    assert file_manager.selection_excluded_journal_names("paper-1") == ["Soil & Tillage Research"]
    assert file_manager.selection_excluded_journal_names("paper-2") == []


def test_scope_cleaner_drops_script_and_style_but_keeps_visible_call_text() -> None:
    from utils.special_issue_service import clean_special_issue_scope

    html = """
    <html><head><style>.batch-articles a:hover { color: #000; }</style>
    <script>window.NREUM = {licenseKey: 'secret'};</script></head>
    <body><h1>Soil Carbon Special Issue</h1>
    <section>We welcome research on soil carbon mapping and remote sensing.</section></body></html>
    """

    cleaned = clean_special_issue_scope(html)

    assert "soil carbon mapping" in cleaned.casefold()
    assert "batch-articles" not in cleaned
    assert "NREUM" not in cleaned
    assert "licenseKey" not in cleaned


def test_verifier_never_replaces_clean_scope_with_full_page_code(tmp_path: Path, monkeypatch) -> None:
    from utils import special_issue_service as service

    item = service.normalize_special_issue(
        {
            "title": "Soil Carbon Special Issue",
            "journal": "Geoderma",
            "issn": "0016-7061",
            "publisher": "Elsevier",
            "scope": "Clean original scope about soil carbon mapping.",
            "deadline": "2027-06-30",
            "url": "https://example.org/call",
        },
        source="fixture",
        fetched_at="2026-09-01T10:00:00",
    )
    assert item is not None
    page = """
    <style>.bad { color: red; }</style><script>window.NREUM = {}</script>
    <h1>Soil Carbon Special Issue</h1><p>Geoderma ISSN 0016-7061</p>
    <p>Submission deadline: 30 June 2027</p>
    """
    monkeypatch.setattr(service, "_fetch_official_page", lambda _url: page)
    cache = EvidenceCache(tmp_path / "verify.sqlite")
    cache.initialize()

    verified = service.verify_special_issue(item, now=datetime(2026, 9, 1, 12), cache=cache)

    assert verified["scope_text"] == "Clean original scope about soil carbon mapping."
    assert "NREUM" not in verified["scope_text"]


def test_four_major_publishers_are_inferred_from_domains_and_imprints() -> None:
    from utils.special_issue_service import infer_special_issue_publisher

    assert infer_special_issue_publisher("", "https://www.sciencedirect.com/special-issue/x") == "Elsevier"
    assert infer_special_issue_publisher("BMC", "https://example.org/call") == "Springer Nature"
    assert infer_special_issue_publisher("Informa UK Limited", "https://example.org/call") == "Taylor & Francis"
    assert infer_special_issue_publisher("Wiley-VCH", "https://example.org/call") == "Wiley"


def test_non_priority_confirmed_publisher_score_is_halved_but_unknown_is_not() -> None:
    from utils.special_issue_matching import apply_publisher_priority

    assert apply_publisher_priority(90, "Elsevier") == (90, 1.0)
    assert apply_publisher_priority(90, "MDPI") == (45, 0.5)
    assert apply_publisher_priority(90, "unknown") == (90, 1.0)


def test_translation_helpers_fill_only_missing_chinese_values() -> None:
    from utils.local_translation import translate_missing_keywords, translate_special_issue_scope

    translator = lambda texts: [f"中:{text}" for text in texts]
    terms = [
        {"canonical_en": "soil carbon", "translation_zh": ""},
        {"canonical_en": "remote sensing", "translation_zh": "人工译文"},
    ]

    translated = translate_missing_keywords(terms, translator=translator)

    assert translated[0]["translation_zh"] == "中:soil carbon"
    assert translated[1]["translation_zh"] == "人工译文"
    assert translate_special_issue_scope("Full call scope", "", translator=translator) == "中:Full call scope"
    assert translate_special_issue_scope("Full call scope", "已有译文", translator=translator) == "已有译文"


def test_processing_budget_does_not_delete_old_special_issue_records(tmp_path: Path) -> None:
    from utils.special_issue_service import normalize_special_issue, refresh_special_issues

    now = datetime(2026, 9, 1, 12)
    old_items = []
    for index in range(36):
        item = normalize_special_issue(
            {
                "id": f"old-{index}",
                "title": f"Unrelated legacy topic {index}",
                "journal": f"Legacy Journal {index}",
                "publisher": "MDPI",
                "scope": "A legacy topic unrelated to the current research profile.",
                "deadline": "2027-12-31",
                "url": f"https://legacy.example/{index}",
            },
            source="fixture",
            fetched_at="2026-08-20T12:00:00",
            today=now.date(),
        )
        assert item is not None
        old_items.append(item)

    class NewSource:
        source_id = "taylor_francis"

        @staticmethod
        def fetch(**_kwargs):
            return [
                {
                    "title": "Digital Soil Mapping and Carbon Stocks",
                    "journal": "Soil Science and Plant Nutrition",
                    "publisher": "Taylor & Francis",
                    "scope_text": "Digital soil mapping and soil organic carbon monitoring.",
                    "deadline": "2027-06-30",
                    "official_url": "https://think.taylorandfrancis.com/special_issues/soil-carbon/",
                    "source": "taylor_francis",
                }
            ]

    result = refresh_special_issues(
        sources=[NewSource()],
        now=now,
        store={"items": old_items, "last_checked_at": "2026-08-31T12:00:00"},
        journal_library=[],
        research_profile={"terms": [{"canonical_en": "soil organic carbon", "weight": 100}]},
        papers=[],
        cache=EvidenceCache(tmp_path / "special-limit.sqlite"),
        verifier=lambda item, **_kwargs: item,
        enricher=lambda item, *_args, **_kwargs: item,
        ai_matcher=lambda _item, _profiles, _progress: {
            "score": 85,
            "reason": "与土壤有机碳制图画像高度匹配。",
            "matched_terms": ["soil organic carbon"],
        },
        persist=False,
        translate_scopes=False,
        candidate_limit=36,
    )

    titles = {item["title"] for item in result["store"]["items"]}
    assert "Digital Soil Mapping and Carbon Stocks" in titles
    assert len(result["store"]["items"]) == 37
