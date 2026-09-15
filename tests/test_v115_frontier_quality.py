from __future__ import annotations

from unittest import TestCase

from utils.frontier_scoring import evaluate_frontier_candidate, select_daily_recommendations_v11
from utils.journal_quality import compact_metric_line


class V115FrontierQualityTests(TestCase):
    def setUp(self) -> None:
        self.profile = {
            "terms": [
                {"id": "soc", "text": "soil organic carbon", "weight": 100, "locked": True},
                {"id": "mapping", "text": "digital soil mapping", "weight": 80, "locked": False},
            ],
            "filter_known_q3_q4": True,
            "require_verified_jcr_q1_q2": True,
        }
        self.item = {
            "id": "f1",
            "title": "SOC mapping with machine learning",
            "abstract": "soil organic carbon digital soil mapping",
            "status": "new",
        }

    def test_strict_frontier_gate_keeps_only_verified_q1_q2(self) -> None:
        q1 = {"name": "Q1 Journal", "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]}}
        pending = {"name": "Pending Journal", "jcr": {"status": "pending", "metrics": []}}
        q4 = {"name": "Q4 Journal", "jcr": {"status": "verified", "metrics": [{"quartile": "Q4"}]}}

        self.assertIsNotNone(evaluate_frontier_candidate(self.item, self.profile, q1, {}))
        self.assertIsNone(evaluate_frontier_candidate(self.item, self.profile, pending, {}))
        self.assertIsNone(evaluate_frontier_candidate(self.item, self.profile, q4, {}))

    def test_cached_strict_frontier_gate_excludes_unverified_rows(self) -> None:
        rows = [
            {"id": "q1", "status": "new", "quality_gate_state": "eligible", "quality_gate_reason": "verified_q1_q2", "score": 10},
            {"id": "pending", "status": "new", "quality_gate_state": "blocked", "quality_gate_reason": "quality_pending", "score": 99},
        ]

        result = select_daily_recommendations_v11(rows, {"daily_limit": 5, "require_verified_jcr_q1_q2": True})

        self.assertEqual([row["id"] for row in result], ["q1"])

    def test_compact_metric_line_uses_short_cas_label(self) -> None:
        line = compact_metric_line(
            {
                "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
                "easyscholar": {"cas_upgrade": "2区", "impact_factor": "5.6"},
            }
        )

        self.assertEqual(line, "JCR Q1 · 中科院 2区 · IF 5.6")
