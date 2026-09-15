"""Deterministic v11 daily-frontier matching and total-score tests."""

from __future__ import annotations

from unittest import TestCase

from utils.frontier_scoring import (
    classify_feedback_locally,
    evaluate_frontier_candidate,
    select_daily_recommendations_v11,
)


class FrontierScoringTests(TestCase):
    def setUp(self) -> None:
        self.item = {
            "id": "work-1",
            "title": "Machine learning for soil organic carbon and MAOC mapping",
            "journal": "CATENA",
            "author_keywords": ["soil organic carbon", "MAOC"],
        }
        self.profile = {
            "terms": [
                {"id": "soc", "text": "soil organic carbon", "weight": 80},
                {"id": "maoc", "text": "MAOC", "weight": 60},
            ],
            "filter_known_q3_q4": True,
        }

    def test_total_score_uses_weighted_terms_priority_and_verified_quality(self) -> None:
        result = evaluate_frontier_candidate(
            self.item,
            self.profile,
            {"name": "CATENA", "frontier_priority": "必看", "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]}},
            {},
        )

        self.assertEqual(result["score"], 240)
        self.assertEqual(result["score_breakdown"], {"terms": 140, "priority": 50, "quality": 50, "feedback": 0, "ai": 0})
        self.assertEqual(result["matched_terms"], ["soil organic carbon", "MAOC"])
        self.assertEqual(result["jcr_state"], "已核验 Q1")

    def test_known_q3_is_hidden_only_when_filter_is_on(self) -> None:
        journal = {"name": "CATENA", "frontier_priority": "扩展", "jcr": {"status": "verified", "metrics": [{"quartile": "Q3"}]}}

        self.assertIsNone(evaluate_frontier_candidate(self.item, self.profile, journal, {}))
        visible_profile = {**self.profile, "filter_known_q3_q4": False}
        self.assertIsNotNone(evaluate_frontier_candidate(self.item, visible_profile, journal, {}))

    def test_easyscholar_partition_is_used_for_filtering_but_is_labeled_as_synced_not_official(self) -> None:
        journal = {
            "name": "CATENA",
            "frontier_priority": "必看",
            "jcr": {
                "status": "verified",
                "source": "EasyScholar Open API",
                "metrics": [{"quartile": "Q3"}],
            },
        }

        result = evaluate_frontier_candidate(self.item, {**self.profile, "filter_known_q3_q4": False}, journal, {})

        self.assertIsNotNone(result)
        self.assertEqual(result["jcr_state"], "EasyScholar 同步 Q3")

    def test_unknown_jcr_is_not_blocked_or_penalized(self) -> None:
        result = evaluate_frontier_candidate(
            self.item,
            self.profile,
            {"name": "CATENA", "frontier_priority": "扩展", "jcr": {"status": "pending", "metrics": []}},
            {},
        )

        self.assertEqual(result["score_breakdown"]["quality"], 0)
        self.assertEqual(result["jcr_state"], "分区未知")

    def test_quality_feedback_does_not_reduce_topic_weights(self) -> None:
        classified = classify_feedback_locally("这个期刊是三四区，质量不适合")

        self.assertEqual(classified["kind"], "journal_quality")
        self.assertEqual(classified["term_weight_delta"], 0)
        self.assertEqual(classified["quality_flag"], "low")

    def test_two_distinct_terms_are_required(self) -> None:
        item = {"title": "SOC mapping only", "journal": "CATENA", "author_keywords": ["SOC"]}

        self.assertIsNone(evaluate_frontier_candidate(item, self.profile, {"name": "CATENA"}, {}))

    def test_configured_easyscholar_hides_legacy_items_without_quality_metadata(self) -> None:
        legacy_item = {
            "id": "legacy-1",
            "title": "Legacy recommendation",
            "journal": "CATENA",
            "status": "new",
            "recommendation_date": "",
            "score": 999,
        }

        selected = select_daily_recommendations_v11(
            [legacy_item],
            {"_easyscholar_configured": True, "daily_limit": 5},
        )

        self.assertEqual(selected, [])
