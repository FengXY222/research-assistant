"""Evidence-first journal discovery and identity verification contracts."""

from __future__ import annotations

from pathlib import Path

from tests import _data_root  # noqa: F401 - isolate persistence imports
from utils.evidence_cache import EvidenceCache
from utils.journal_selection_service import (
    derive_journal_candidates,
    find_similar_works,
    verify_journal_identity,
)


def test_same_issn_from_multiple_real_papers_becomes_one_candidate() -> None:
    works = [
        {
            "id": "w1",
            "title": "Paper one",
            "journal": "CATENA",
            "issn": ["0341-8162"],
            "publisher": "Elsevier",
            "source": "openalex",
        },
        {
            "id": "w2",
            "title": "Paper two",
            "journal": "Catena",
            "issn": ["0341-8162", "1872-6887"],
            "publisher": "Elsevier B.V.",
            "source": "crossref",
        },
    ]

    candidates = derive_journal_candidates(works)

    assert len(candidates) == 1
    assert candidates[0]["name"] == "CATENA"
    assert set(candidates[0]["issns"]) == {"0341-8162", "1872-6887"}
    assert [paper["title"] for paper in candidates[0]["similar_papers"]] == ["Paper one", "Paper two"]
    assert candidates[0]["occurrence_count"] == 2


def test_name_only_ai_suggestion_cannot_pass_identity_verification(tmp_path: Path, monkeypatch) -> None:
    from utils import journal_selection_service

    monkeypatch.setattr(journal_selection_service, "_lookup_journal_identity_sources", lambda candidate: [])
    cache = EvidenceCache(tmp_path / "evidence.sqlite")
    cache.initialize()

    result = verify_journal_identity({"name": "Invented Journal"}, cache=cache)

    assert result["verified"] is False
    assert "ISSN" in result["missing"]
    assert "出版社" in result["missing"]
    assert "官方主页" in result["missing"]


def test_identity_requires_issn_publisher_official_homepage_and_active_state(
    tmp_path: Path, monkeypatch
) -> None:
    from utils import journal_selection_service

    monkeypatch.setattr(
        journal_selection_service,
        "_lookup_journal_identity_sources",
        lambda candidate: [
            {
                "source": "crossref",
                "name": "CATENA",
                "issns": ["0341-8162"],
                "publisher": "Elsevier",
                "official_url": "https://www.sciencedirect.com/journal/catena",
                "active": True,
                "checked_at": "2026-08-31",
            }
        ],
    )
    cache = EvidenceCache(tmp_path / "evidence.sqlite")
    cache.initialize()

    result = verify_journal_identity({"name": "CATENA", "issns": ["0341-8162"]}, cache=cache)

    assert result["verified"] is True
    assert result["issns"] == ["0341-8162"]
    assert result["publisher"] == "Elsevier"
    assert result["active"] is True
    assert result["official_url"].startswith("https://")


def test_inactive_journal_is_rejected_even_when_identity_fields_exist(tmp_path: Path, monkeypatch) -> None:
    from utils import journal_selection_service

    monkeypatch.setattr(
        journal_selection_service,
        "_lookup_journal_identity_sources",
        lambda candidate: [
            {
                "source": "crossref",
                "name": "Former Journal",
                "issns": ["1111-2222"],
                "publisher": "Example Press",
                "official_url": "https://example.org/former",
                "active": False,
                "checked_at": "2026-08-31",
            }
        ],
    )
    cache = EvidenceCache(tmp_path / "evidence.sqlite")
    cache.initialize()

    result = verify_journal_identity({"name": "Former Journal", "issns": ["1111-2222"]}, cache=cache)

    assert result["verified"] is False
    assert result["active"] is False
    assert "停止出版" in result["reason"]


def test_identity_source_conflicts_remain_visible_in_evidence(tmp_path: Path, monkeypatch) -> None:
    from utils import journal_selection_service

    monkeypatch.setattr(
        journal_selection_service,
        "_lookup_journal_identity_sources",
        lambda candidate: [
            {
                "source": "crossref",
                "name": "Example Journal",
                "issns": ["1111-2222"],
                "publisher": "Elsevier",
                "official_url": "https://example.org/journal",
                "active": True,
            },
            {
                "source": "openalex",
                "name": "Example Journal",
                "issns": ["1111-2222"],
                "publisher": "Springer Nature",
                "official_url": "https://example.org/journal",
                "active": True,
            },
        ],
    )
    cache = EvidenceCache(tmp_path / "evidence.sqlite")
    cache.initialize()

    result = verify_journal_identity({"name": "Example Journal", "issns": ["1111-2222"]}, cache=cache)

    assert result["verified"] is True
    assert any(conflict["field"] == "publisher" for conflict in result["conflicts"])
    assert len(result["sources"]) == 2


def test_similar_work_search_normalizes_sources_and_continues_after_failure(
    tmp_path: Path, monkeypatch
) -> None:
    from utils import journal_selection_service

    def fake_fetch(source, manuscript, query):
        if source == "semantic_scholar":
            raise RuntimeError("rate limit")
        return [
            {
                "id": f"{source}-1",
                "title": "Similar soil carbon paper",
                "doi": "10.1000/shared",
                "journal": "CATENA",
                "issn": ["0341-8162"],
                "publisher": "Elsevier",
                "url": "https://doi.org/10.1000/shared",
            }
        ]

    monkeypatch.setattr(journal_selection_service, "_fetch_similar_source", fake_fetch)
    cache = EvidenceCache(tmp_path / "evidence.sqlite")
    cache.initialize()
    progress = []

    works = find_similar_works(
        {
            "id": "paper-1",
            "title": "Mapping soil organic carbon",
            "keywords": ["soil carbon", "digital soil mapping"],
            "summary": "A spatial prediction study.",
        },
        cache=cache,
        progress=lambda message, value=None: progress.append((message, value)),
    )

    assert len(works) == 1
    assert set(works[0]["source_names"]) == {"openalex", "crossref"}
    assert works[0]["issn"] == ["0341-8162"]
    assert any("Semantic Scholar" in message and "失败" in message for message, _value in progress)
