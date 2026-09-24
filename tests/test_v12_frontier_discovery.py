"""v12 multi-source paper normalization and deduplication contracts."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from utils.evidence_cache import EvidenceCache
from utils.frontier_service import dedupe_works, discover_frontier_candidates, update_daily_frontier_v12


def test_dedupe_uses_doi_then_source_id_then_title_and_merges_sources() -> None:
    rows = [
        {
            "source": "openalex",
            "source_id": "W1",
            "doi": "https://doi.org/10.1000/ABC",
            "title": "Soil carbon mapping",
            "abstract": "long abstract",
            "source_evidence": [{"source": "openalex"}],
        },
        {
            "source": "crossref",
            "source_id": "CR9",
            "doi": "10.1000/abc",
            "title": "SOIL CARBON MAPPING",
            "journal": "CATENA",
            "source_evidence": [{"source": "crossref"}],
        },
        {"source": "semantic_scholar", "source_id": "S2", "title": "A unique title"},
        {"source": "semantic_scholar", "source_id": "S2", "title": "A changed title"},
        {"source": "doaj", "source_id": "", "title": "Fallback   title!"},
        {"source": "crossref", "source_id": "", "title": "fallback title"},
    ]

    result = dedupe_works(rows)

    assert len(result) == 3
    merged = next(item for item in result if item.get("doi"))
    assert set(merged["source_names"]) == {"openalex", "crossref"}
    assert merged["journal"] == "CATENA"
    assert len(merged["source_evidence"]) == 2


def test_discovery_normalizes_adapter_records_and_survives_one_source_failure(
    tmp_path: Path, monkeypatch
) -> None:
    from utils import frontier_service

    def fake_adapter(source, query, start, today, api_key):
        if source == "openalex":
            raise frontier_service.FrontierNetworkError("timeout")
        return [
            {
                "id": f"{source}-1",
                "title": f"{query} paper",
                "abstract": "soil carbon mapping",
                "journal": "CATENA",
                "doi": "10.1000/shared",
                "authors": ["A. Author"],
                "published_date": "2026-08-30",
                "url": "https://doi.org/10.1000/shared",
            }
        ]

    monkeypatch.setattr(frontier_service, "_fetch_v12_source", fake_adapter)
    cache = EvidenceCache(tmp_path / "evidence.sqlite")
    cache.initialize()
    progress = []
    profile_view = {
        "active_terms": [{"canonical_en": "soil carbon", "weight": 90}],
        "sources": {
            "crossref": {"enabled": True},
            "openalex": {"enabled": True},
            "semantic_scholar": {"enabled": False},
            "doaj": {"enabled": False},
            "arxiv": {"enabled": False},
        },
        "lookback_days": 7,
    }

    result = discover_frontier_candidates(
        profile_view,
        cache=cache,
        progress=lambda message, value=None: progress.append((message, value)),
    )

    assert len(result) == 1
    assert result[0]["source"] == "crossref"
    assert result[0]["source_id"] == "crossref-1"
    assert result[0]["issn"] == []
    assert result[0]["is_preprint"] is False
    assert result[0]["fetched_at"]
    assert result[0]["source_evidence"][0]["source"] == "crossref"
    assert any("OpenAlex" in message and "失败" in message for message, _value in progress)


def test_discovery_marks_arxiv_as_preprint(tmp_path: Path, monkeypatch) -> None:
    from utils import frontier_service

    monkeypatch.setattr(
        frontier_service,
        "_fetch_v12_source",
        lambda source, query, start, today, api_key: [
            {"id": "arxiv:1234", "title": "Preprint", "journal": "arXiv", "url": "https://arxiv.org/abs/1234"}
        ],
    )
    cache = EvidenceCache(tmp_path / "evidence.sqlite")
    cache.initialize()

    result = discover_frontier_candidates(
        {
            "active_terms": [{"canonical_en": "soil carbon", "weight": 90}],
            "sources": {"arxiv": {"enabled": True}},
        },
        cache=cache,
    )

    assert result[0]["is_preprint"] is True
    assert result[0]["source"] == "arxiv"


def test_arxiv_406_falls_back_to_public_html_search(monkeypatch) -> None:
    from utils import frontier_service

    html = b"""
    <ol><li class="arxiv-result">
      <p class="list-title is-inline-block"><a href="https://arxiv.org/abs/2609.01234">arXiv:2609.01234</a></p>
      <p class="title is-5 mathjax"> Soil organic carbon mapping with remote sensing </p>
      <p class="authors"><span>Authors:</span><a>A. Researcher</a><a>B. Scientist</a></p>
      <p class="is-size-7"><span>Submitted</span> 17 September, 2026;</p>
      <p class="abstract mathjax"><span class="abstract-full has-text-grey-dark mathjax">Abstract: A reproducible mapping study.</span></p>
    </li></ol>
    """
    calls = []

    def request(url, params, accept="application/atom+xml"):
        calls.append((url, params, accept))
        if url == frontier_service.ARXIV_API:
            raise frontier_service.FrontierNetworkError("HTTP Error 406: Not Acceptable")
        return html

    monkeypatch.setattr(frontier_service, "_request_external_text", request)
    rows = frontier_service._fetch_arxiv(
        "soil organic carbon", date(2026, 9, 1), date(2026, 9, 18)
    )

    assert [call[0] for call in calls] == [frontier_service.ARXIV_API, frontier_service.ARXIV_SEARCH_URL]
    assert calls[1][1]["query"] == "soil organic carbon"
    assert "text/html" in calls[1][2]
    assert rows[0]["title"] == "Soil organic carbon mapping with remote sensing"
    assert rows[0]["published_date"] == "2026-09-17"
    assert rows[0]["authors"] == ["A. Researcher", "B. Scientist"]
    assert rows[0]["abstract"] == "A reproducible mapping study."


def test_v12_refresh_persists_stream_states_and_core_mix(tmp_path: Path, monkeypatch) -> None:
    from utils import frontier_service

    candidates = [
        {
            "id": "q1",
            "title": "Core Q1",
            "journal": "Verified Journal",
            "score": 80,
            "recommendation_kind": "core_keyword",
            "status": "new",
        },
        {
            "id": "unknown",
            "title": "Unknown quality",
            "journal": "Unknown Journal",
            "score": 90,
            "recommendation_kind": "profile_exploration",
            "status": "new",
        },
        {
            "id": "preprint",
            "title": "Preprint",
            "journal": "arXiv",
            "is_preprint": True,
            "score": 70,
            "recommendation_kind": "core_keyword",
            "status": "new",
        },
    ]
    monkeypatch.setattr(frontier_service, "discover_frontier_candidates", lambda *args, **kwargs: candidates)
    monkeypatch.setattr(frontier_service, "review_frontier_content", lambda profile, items, journals, **kwargs:
        [dict(item, content_decision="accept", admission_version="test") for item in items])
    monkeypatch.setattr(frontier_service, "is_easyscholar_ready", lambda: True)
    monkeypatch.setattr(frontier_service, "RESEARCH_INTELLIGENCE_CACHE_FILE", tmp_path / "cache.sqlite", raising=False)
    journals = [
        {
            "name": "Verified Journal",
            "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
            "easyscholar": {"cas_upgrade": "农林科学1区"},
        }
    ]

    result = update_daily_frontier_v12(
        {"profile": {"terms": [{"canonical_en": "soil carbon", "weight": 90}], "daily_limit": 5}, "items": []},
        journals,
    )

    by_id = {item["id"]: item for item in result["data"]["items"]}
    assert by_id["q1"]["quality_gate_state"] == "eligible"
    assert by_id["unknown"]["quality_gate_state"] == "withheld"
    assert by_id["preprint"]["quality_gate_state"] == "preprint"
    assert result["stream_counts"] == {"journal": 1, "preprint": 1, "pending_quality": 1}
    assert result["visible_count"] == 2
