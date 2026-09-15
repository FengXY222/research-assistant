"""Rebuildable v12 evidence-cache contracts."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from utils.evidence_cache import EvidenceCache


def test_expired_source_response_is_not_returned(tmp_path: Path) -> None:
    cache = EvidenceCache(tmp_path / "research_intelligence.sqlite")
    cache.initialize()
    cache.put_source_response(
        "openalex",
        "soil",
        {"results": [1]},
        "2026-08-01T00:00:00",
        "2026-08-02T00:00:00",
    )

    assert cache.get_source_response("openalex", "soil", now="2026-08-03T00:00:00") is None


def test_source_response_round_trip_is_deterministic(tmp_path: Path) -> None:
    cache = EvidenceCache(tmp_path / "research_intelligence.sqlite")
    cache.initialize()
    payload = {"results": [{"title": "Soil carbon", "doi": "10.1/example"}]}
    cache.put_source_response(
        "crossref",
        "soil-carbon",
        payload,
        "2026-08-31T08:00:00",
        "2026-09-01T08:00:00",
    )

    assert cache.get_source_response(
        "crossref", "soil-carbon", now="2026-08-31T12:00:00"
    ) == payload


def test_work_upsert_uses_doi_before_source_id_or_title(tmp_path: Path) -> None:
    cache = EvidenceCache(tmp_path / "research_intelligence.sqlite")
    cache.initialize()

    first = cache.upsert_work(
        {
            "source": "openalex",
            "source_id": "W1",
            "doi": "https://doi.org/10.1000/ABC",
            "title": "Soil Carbon Mapping",
        }
    )
    second = cache.upsert_work(
        {
            "source": "crossref",
            "source_id": "CR-9",
            "doi": "10.1000/abc",
            "title": "A differently formatted title",
        }
    )

    assert first == second == "doi:10.1000/abc"


def test_cache_can_be_cleared_without_touching_personal_json(tmp_path: Path) -> None:
    personal = tmp_path / "research_profile.json"
    personal.write_text('{"terms":[{"canonical_en":"soil carbon"}]}', encoding="utf-8")
    before = personal.read_bytes()
    cache = EvidenceCache(tmp_path / "research_intelligence.sqlite")
    cache.initialize()
    cache.upsert_work({"source": "openalex", "source_id": "W1", "title": "Example"})

    cache.clear_rebuildable_data()

    assert cache.integrity_check() == "ok"
    assert personal.read_bytes() == before


def test_initialize_recovers_a_corrupt_database_and_keeps_the_broken_copy(tmp_path: Path) -> None:
    path = tmp_path / "research_intelligence.sqlite"
    path.write_bytes(b"not a sqlite database")
    cache = EvidenceCache(path)

    cache.initialize()

    assert cache.integrity_check() == "ok"
    assert list(tmp_path.glob("research_intelligence.sqlite.broken-*"))
    with sqlite3.connect(path) as connection:
        version = connection.execute("SELECT version FROM schema_version").fetchone()[0]
    assert version >= 1


def test_schema_contains_future_shared_core_tables(tmp_path: Path) -> None:
    cache = EvidenceCache(tmp_path / "research_intelligence.sqlite")
    cache.initialize()
    expected = {
        "source_responses",
        "works",
        "journal_evidence",
        "ocr_pages",
        "special_issue_discovery",
        "special_issue_verification",
        "dedupe_keys",
        "job_checkpoints",
    }

    with sqlite3.connect(cache.path) as connection:
        actual = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    assert expected <= actual


def test_special_issue_discovery_verification_and_checkpoint_round_trip(tmp_path: Path) -> None:
    cache = EvidenceCache(tmp_path / "research_intelligence.sqlite")
    cache.initialize()
    cache.put_special_issue_discovery("si-1", {"title": "Soil Carbon", "deadline": "2027-06-30"})
    cache.put_special_issue_verification("si-1", {"status": "official_verified", "page_hash": "abc"})
    cache.put_job_checkpoint("special_issue_refresh", {"last_success_at": "2026-08-31T08:00:00"})

    assert cache.get_special_issue_discovery("si-1")["deadline"] == "2027-06-30"
    assert cache.get_special_issue_verification("si-1")["status"] == "official_verified"
    assert cache.get_job_checkpoint("special_issue_refresh")["last_success_at"] == "2026-08-31T08:00:00"
