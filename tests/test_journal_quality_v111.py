"""Pure journal quality snapshot and eligibility contracts."""

from __future__ import annotations

from unittest import TestCase

from utils.journal_quality import is_default_frontier_eligible, journal_quality_snapshot


class JournalQualityV111Tests(TestCase):
    def test_snapshot_combines_source_labelled_jcr_cas_and_if_metrics(self) -> None:
        snapshot = journal_quality_snapshot(
            {
                "jcr": {
                    "status": "verified",
                    "source": "Clarivate JCR",
                    "checked_at": "2026-08-21",
                    "metrics": [{"quartile": "Q1"}],
                },
                "easyscholar": {
                    "source": "EasyScholar Open API",
                    "checked_at": "2026-08-20",
                    "cas_upgrade": "2区",
                    "cas_basic": "3区",
                    "impact_factor": "5.6",
                },
            }
        )
        self.assertEqual(snapshot["jcr_status"], "verified")
        self.assertEqual(snapshot["jcr_quartile"], "Q1")
        self.assertEqual(snapshot["metric_line"], "JCR Q1 · 中科院升级版 2区 · 中科院基础版 3区 · IF 5.6")
        self.assertEqual(snapshot["source"], "Clarivate JCR")
        self.assertEqual(snapshot["checked_at"], "2026-08-21")
        self.assertTrue(snapshot["is_verified"])

    def test_quality_gate_accepts_q1_q2_rejects_q3_q4_and_requires_provider_when_configured(self) -> None:
        q2 = journal_quality_snapshot({"jcr": {"status": "verified", "metrics": [{"quartile": "Q2"}]}})
        q4 = journal_quality_snapshot({"jcr": {"status": "verified", "metrics": [{"quartile": "Q4"}]}})
        pending = journal_quality_snapshot({"jcr": {"status": "pending", "metrics": []}})
        self.assertEqual(is_default_frontier_eligible(q2, easyscholar_configured=True), (True, "verified_q1_q2"))
        self.assertEqual(is_default_frontier_eligible(q4, easyscholar_configured=False), (False, "known_q3_q4"))
        self.assertEqual(is_default_frontier_eligible(pending, easyscholar_configured=False), (True, "quality_unresolved"))
        self.assertEqual(is_default_frontier_eligible(pending, easyscholar_configured=True), (False, "quality_pending"))

    def test_ai_only_or_missing_metrics_never_become_verified(self) -> None:
        snapshot = journal_quality_snapshot(
            {
                "jcr": {"status": "ai_estimated", "metrics": [{"quartile": "Q1"}]},
                "easyscholar": {"cas_upgrade": "1区", "impact_factor": "9.9"},
            }
        )
        self.assertFalse(snapshot["is_verified"])
