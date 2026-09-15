"""Tests for v11 frontier-to-library and journal-health operations."""

from __future__ import annotations

from unittest import TestCase

from utils.file_manager import normalize_library_journal, record_frontier_feedback_event
from utils.journal_health_service import import_frontier_journal, journal_health_targets, merge_journal_health_patch


class FrontierLibraryTests(TestCase):
    def test_frontier_journal_import_is_name_publisher_deduplicated(self) -> None:
        imported, created = import_frontier_journal(
            [],
            {"journal": "CATENA", "publisher": "Elsevier", "id": "f1", "url": "https://example.test"},
            now="2026-08-21",
        )
        again, created_again = import_frontier_journal(
            imported,
            {"journal": "catena", "publisher": "ELSEVIER", "id": "f2"},
            now="2026-08-21",
        )

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(len(again), 1)
        self.assertEqual(again[0]["frontier_priority"], "扩展")
        self.assertEqual(again[0]["provenance"]["kind"], "frontier")

    def test_verified_jcr_is_never_overwritten_by_ai_health_patch(self) -> None:
        journal = {"name": "CATENA", "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]}}

        updated = merge_journal_health_patch(
            journal,
            {"jcr": {"status": "ai_estimated", "metrics": [{"quartile": "Q4"}]}, "ai_scope_cn": "模型说明"},
        )

        self.assertEqual(updated["jcr"]["status"], "verified")
        self.assertEqual(updated["jcr"]["metrics"][0]["quartile"], "Q1")
        self.assertEqual(updated["ai_scope_cn"], "模型说明")

    def test_health_check_targets_only_incomplete_or_changed_records(self) -> None:
        journals = [
            {
                "id": "complete",
                "name": "A",
                "issn": "0000-0000",
                "website": "https://a.test",
                "fields": ["soil"],
                "metadata_dirty": False,
            },
            {"id": "incomplete", "name": "B", "issn": "", "website": "", "fields": []},
            {"id": "changed", "name": "C", "issn": "0000-0001", "website": "https://c.test", "fields": ["soil"], "metadata_dirty": True},
        ]

        targets = journal_health_targets(journals, model="deepseek-chat", today="2026-08-21")

        self.assertEqual([target["id"] for target in targets], ["incomplete", "changed"])
        self.assertEqual(targets[0]["health_reason"], "incomplete")
        self.assertEqual(targets[1]["health_reason"], "needs_metadata_update")

    def test_library_normalizer_preserves_v11_health_and_provenance_fields(self) -> None:
        journal = normalize_library_journal(
            {
                "id": "frontier-catena",
                "name": "CATENA",
                "provenance": {"kind": "frontier", "first_item_id": "f1"},
                "metadata_dirty": True,
                "user_quality_flag": "low",
                "jcr_locked": True,
            }
        )

        self.assertEqual(journal["provenance"]["kind"], "frontier")
        self.assertTrue(journal["metadata_dirty"])
        self.assertEqual(journal["user_quality_flag"], "low")
        self.assertTrue(journal["jcr_locked"])

    def test_frontier_feedback_event_keeps_score_snapshot_and_comment(self) -> None:
        data = {
            "items": [
                {
                    "id": "f1",
                    "title": "SOC MAOC",
                    "journal": "CATENA",
                    "match_terms": ["SOC", "MAOC"],
                    "score": 180,
                }
            ]
        }

        changed = record_frontier_feedback_event(
            data,
            "f1",
            "one_line_feedback",
            "这个期刊三四区，质量不适合",
            {"kind": "journal_quality", "term_weight_delta": 0, "quality_flag": "low"},
            now="2026-08-21T10:00:00",
        )

        event = changed["items"][0]["feedback_events"][0]
        self.assertEqual(event["action"], "one_line_feedback")
        self.assertEqual(event["score_snapshot"], 180)
        self.assertEqual(event["classification"]["kind"], "journal_quality")
        self.assertEqual(changed["items"][0]["one_line_feedback"], "这个期刊三四区，质量不适合")
