"""AI-first selection contracts: abstract, requirements and external journals."""

from __future__ import annotations

from unittest import TestCase

try:
    from utils.journal_selection_service import (
        external_candidate_to_library_journal,
        normalize_selection_requirements,
        rank_ai_first_candidates,
    )
except ImportError:  # First red run documents the v11.1 interface.
    external_candidate_to_library_journal = None
    normalize_selection_requirements = None
    rank_ai_first_candidates = None


class AiFirstSelectionV111Tests(TestCase):
    def setUp(self) -> None:
        self.paper = {
            "id": "paper-1",
            "title": "SOC mapping under land-use change",
            "summary": "This study maps soil organic carbon using remote sensing and machine learning.",
            "keywords": ["SOC", "remote sensing"],
        }
        self.profile = {"terms": [{"text": "soil organic carbon", "weight": 100, "locked": True}]}
        self.library = [
            {
                "id": "q1-low-ai",
                "name": "Q1 Journal",
                "publisher": "Publisher A",
                "frontier_priority": "必看",
                "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
            },
            {
                "id": "q2-high-ai",
                "name": "Q2 Journal",
                "publisher": "Publisher B",
                "frontier_priority": "不订阅",
                "jcr": {"status": "verified", "metrics": [{"quartile": "Q2"}]},
            },
            {
                "id": "known-q3",
                "name": "Known Q3 Journal",
                "jcr": {"status": "verified", "metrics": [{"quartile": "Q3"}]},
            },
        ]

    def test_requirements_expose_open_access_quartile_and_speed_controls(self) -> None:
        self.assertTrue(callable(normalize_selection_requirements))

        requirements = normalize_selection_requirements(
            {"oa_mode": "require", "quartile_target": "q1_q2", "speed_priority": "urgent"}
        )

        self.assertEqual(requirements["oa_mode"], "require")
        self.assertEqual(requirements["quartile_target"], "q1_q2")
        self.assertEqual(requirements["speed_priority"], "urgent")
        self.assertTrue(requirements["filter_known_q3_q4"])

    def test_ai_fit_is_the_main_score_and_external_candidates_remain_explicitly_unverified(self) -> None:
        self.assertTrue(callable(rank_ai_first_candidates))
        recommendation = {
            "ranked": [
                {"id": "q1-low-ai", "fit_score": 50, "reason_cn": "范围相符", "oa_status": "unknown"},
                {
                    "id": "q2-high-ai",
                    "fit_score": 90,
                    "reason_cn": "摘要中的 SOC 遥感建模与范围高度一致",
                    "oa_status": "yes",
                    "estimated_decision_days_min": 30,
                    "estimated_decision_days_max": 50,
                    "time_confidence": "medium",
                },
            ],
            "external_candidates": [
                {
                    "name": "External Soil Journal",
                    "publisher": "External Press",
                    "fit_score": 86,
                    "reason_cn": "研究主题与方法匹配",
                    "quartile_hint": "Q3",
                    "oa_status": "unknown",
                }
            ],
        }

        candidates = rank_ai_first_candidates(
            self.paper,
            self.library,
            self.profile,
            {},
            {"filter_known_q3_q4": True, "speed_priority": "urgent"},
            recommendation=recommendation,
        )

        ids = [row["journal_id"] for row in candidates]
        self.assertNotIn("known-q3", ids)
        self.assertEqual(ids[0], "q2-high-ai")
        self.assertEqual(candidates[0]["ai_score"], 63)
        self.assertLessEqual(candidates[0]["verified_score"], 20)
        self.assertLessEqual(candidates[0]["time_score"], 10)
        external = next(row for row in candidates if row["is_external"])
        self.assertEqual(external["source"], "AI 扩展候选·待核验")
        self.assertEqual(external["journal"]["jcr"]["status"], "pending")
        self.assertEqual(external["total_score"], external["ai_score"] + external["verified_score"] + external["time_score"])

    def test_external_candidate_can_be_quickly_imported_without_turning_ai_hints_into_verified_facts(self) -> None:
        self.assertTrue(callable(external_candidate_to_library_journal))
        candidate = {
            "journal": {
                "name": "External Soil Journal",
                "publisher": "External Press",
                "issn": "1234-5678",
                "fields": ["soil organic carbon"],
                "jcr": {"status": "pending", "metrics": []},
            },
            "reason_cn": "研究主题与方法匹配",
            "is_external": True,
        }

        journal = external_candidate_to_library_journal(candidate, today="2026-08-21")

        self.assertEqual(journal["name"], "External Soil Journal")
        self.assertEqual(journal["jcr"]["status"], "pending")
        self.assertTrue(journal["metadata_dirty"])
        self.assertEqual(journal["provenance"]["kind"], "ai_selection")
        self.assertIn("待核验", journal["notes"])
