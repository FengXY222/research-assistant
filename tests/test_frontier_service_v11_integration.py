"""Regression test that public frontier retrieval uses v11 scoring, not pair tiers."""

from __future__ import annotations

from unittest import TestCase

from tests import _data_root  # noqa: F401 - set isolated persistence root
from utils.file_manager import save_frontier_data, load_frontier_data
from utils.frontier_service import _record_to_item, frontier_profile_signature, merge_frontier_refresh_item, select_daily_recommendations


class FrontierServiceV11IntegrationTests(TestCase):
    def test_record_conversion_uses_v11_total_score_and_not_legacy_pair_labels(self) -> None:
        item = _record_to_item(
            {
                "title": "Machine learning for soil organic carbon and MAOC mapping",
                "journal": "CATENA",
                "author_keywords": ["soil organic carbon", "MAOC"],
                "source_name": "OpenAlex",
                "published_date": "2026-08-21",
            },
            {
                "terms": [
                    {"id": "soc", "text": "soil organic carbon", "weight": 80},
                    {"id": "maoc", "text": "MAOC", "weight": 60},
                ],
                "filter_known_q3_q4": True,
            },
            [{"name": "CATENA", "frontier_priority": "必看", "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]}}],
        )

        self.assertEqual(item["score"], 240)
        self.assertNotIn("一级", item["recommendation_reason"])
        self.assertEqual(item["score_breakdown"]["priority"], 50)

    def test_public_daily_selection_orders_by_total_score_without_ai_score_override(self) -> None:
        profile = {"daily_limit": 2}
        selected = select_daily_recommendations(
            [
                {"id": "low", "status": "new", "recommendation_date": "", "score": 80, "ai_score": 99},
                {"id": "high", "status": "new", "recommendation_date": "", "score": 100, "ai_score": -1},
            ],
            profile,
        )

        self.assertEqual([item["id"] for item in selected], ["high", "low"])

    def test_cache_signature_changes_when_v11_term_weight_changes(self) -> None:
        profile = {"terms": [{"id": "soc", "text": "SOC", "weight": 50}], "filter_known_q3_q4": True}
        changed = {"terms": [{"id": "soc", "text": "SOC", "weight": 80}], "filter_known_q3_q4": True}

        self.assertNotEqual(frontier_profile_signature(profile, []), frontier_profile_signature(changed, []))

    def test_v11_score_explanation_round_trips_through_frontier_storage(self) -> None:
        save_frontier_data(
            {
                "profile": {"terms": []},
                "items": [
                    {
                        "id": "f1",
                        "title": "SOC MAOC",
                        "score": 140,
                        "score_breakdown": {"terms": 140, "priority": 0, "quality": 0, "feedback": 0, "ai": 0},
                        "matched_term_ids": ["soc", "maoc"],
                        "jcr_state": "分区未知",
                        "feedback_events": [{"action": "relevant"}],
                    }
                ],
            }
        )

        item = load_frontier_data()["items"][0]
        self.assertEqual(item["score_breakdown"]["terms"], 140)
        self.assertEqual(item["matched_term_ids"], ["soc", "maoc"])
        self.assertEqual(item["feedback_events"], [{"action": "relevant"}])

    def test_frontier_storage_bounds_ai_adjustment_to_v11_limit(self) -> None:
        save_frontier_data(
            {
                "profile": {"terms": []},
                "items": [
                    {
                        "id": "f-ai",
                        "title": "SOC MAOC",
                        "score_breakdown": {"terms": 140, "ai": -20},
                    }
                ],
            }
        )

        self.assertEqual(load_frontier_data()["items"][0]["score_breakdown"]["ai"], -15)

    def test_refresh_merge_preserves_auditable_feedback_and_recomputes_total(self) -> None:
        fresh = {
            "id": "f1",
            "score": 140,
            "score_breakdown": {"terms": 140, "priority": 0, "quality": 0, "feedback": 0, "ai": 0},
            "status": "new",
        }
        previous = {
            "id": "f1",
            "status": "saved",
            "feedback_adjustment": 15,
            "ai_adjustment": 5,
            "feedback_events": [{"id": "event-1"}],
            "one_line_feedback": "很有启发",
        }

        merged = merge_frontier_refresh_item(fresh, previous, today="2026-08-21")

        self.assertEqual(merged["status"], "saved")
        self.assertEqual(merged["score"], 160)
        self.assertEqual(merged["feedback_events"], [{"id": "event-1"}])
        self.assertEqual(merged["one_line_feedback"], "很有启发")
