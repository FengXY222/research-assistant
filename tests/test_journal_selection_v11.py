"""Explainable, offline-first v11 journal-selection tests."""

from __future__ import annotations

from unittest import TestCase

from utils.journal_selection_service import (
    append_journal_to_submission_path,
    apply_ai_selection_patch,
    rank_journal_candidates,
    score_journal_candidate,
)


class JournalSelectionTests(TestCase):
    def setUp(self) -> None:
        self.paper = {
            "id": "paper-1",
            "title": "SOC mapping with machine learning",
            "keywords": ["soil organic carbon", "machine learning", "digital soil mapping"],
            "summary": "研究土壤有机碳空间制图。",
            "journals": [],
        }
        self.profile = {"terms": [{"text": "soil organic carbon", "weight": 100, "locked": True}]}
        self.journal = {
            "id": "catena",
            "name": "CATENA",
            "publisher": "Elsevier",
            "fields": ["soil organic carbon", "digital soil mapping"],
            "frontier_priority": "必看",
            "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
        }

    def test_local_components_are_bounded_and_total_is_explainable(self) -> None:
        score = score_journal_candidate(self.paper, self.journal, self.profile, {"submission_count": 2}, {})

        self.assertLessEqual(score["topic_fit"], 40)
        self.assertLessEqual(score["quality"], 25)
        self.assertLessEqual(score["personal_experience"], 20)
        self.assertLessEqual(score["constraints"], 15)
        self.assertEqual(
            score["local_score"],
            sum(score[key] for key in ("topic_fit", "quality", "personal_experience", "constraints")),
        )
        self.assertEqual(score["local_score"], score["total_score"])

    def test_ai_failure_patch_keeps_local_order(self) -> None:
        library = [self.journal, {**self.journal, "id": "second", "name": "Second Journal", "frontier_priority": "扩展"}]
        candidates = rank_journal_candidates(self.paper, library, self.profile, history={}, constraints={})
        revised = apply_ai_selection_patch(candidates, {"ranked": []})

        self.assertEqual([row["journal_id"] for row in revised], [row["journal_id"] for row in candidates])
        self.assertTrue(all(row["ai_adjustment"] == 0 for row in revised))

    def test_usage_index_style_history_is_recognized_by_name_and_publisher(self) -> None:
        history = {"catena|elsevier": {"submission_count": 3}}

        ranked = rank_journal_candidates(self.paper, [self.journal], self.profile, history=history, constraints={})

        self.assertEqual(ranked[0]["personal_experience"], 14)

    def test_add_to_submission_path_appends_one_journal_without_touching_history(self) -> None:
        paper = {"id": "p1", "title": "SOC", "journals": [{"id": "old", "name": "Old Journal", "status": "拒稿"}]}

        updated = append_journal_to_submission_path(paper, self.journal, today="2026-08-21")

        self.assertEqual(len(updated["journals"]), 2)
        self.assertEqual(updated["journals"][0]["id"], "old")
        self.assertEqual(updated["journals"][1]["status"], "准备投稿")
        self.assertEqual(updated["journals"][1]["date"], "2026-08-21")

    def test_known_q3_q4_is_a_hard_exclusion_when_filter_is_enabled(self) -> None:
        q4 = {**self.journal, "jcr": {"status": "verified", "metrics": [{"quartile": "Q4"}]}}

        score = score_journal_candidate(self.paper, q4, self.profile, {}, {"filter_known_q3_q4": True})

        self.assertTrue(score["excluded"])
        self.assertIn("Q4", score["exclusion_reason"])
