from __future__ import annotations

from unittest import TestCase
from unittest.mock import Mock, patch

from utils.ai_service import recommend_journals_iteratively_with_ai
from utils.journal_selection_service import journal_meets_hard_requirements


class V1155SelectionGateTests(TestCase):
    @staticmethod
    def _journal(*, name: str = "Candidate", publisher: str = "Wiley", fee_mode: str = "apc", quartile: str = "Q1", cas: str = "1") -> dict:
        return {
            "id": name.casefold().replace(" ", "-"),
            "name": name,
            "publisher": publisher,
            "fee_mode": fee_mode,
            "jcr": {"status": "verified", "metrics": [{"quartile": quartile}]},
            "easyscholar": {"cas_upgrade": f"{cas}区"},
        }

    def test_publisher_is_a_hard_preference_while_fee_and_speed_remain_soft(self) -> None:
        ok, reason = journal_meets_hard_requirements(
            self._journal(),
            {
                "publishers": ["Elsevier"],
                "fee_modes": ["no_fee"],
                "speed_priority": "urgent",
                "jcr_quartiles": ["Q1"],
                "cas_quartiles": ["1"],
            },
            verify_divisions=True,
        )

        self.assertFalse(ok)
        self.assertEqual(reason, "未满足所选出版社硬偏好")

    def test_unknown_publisher_is_retained_for_manual_verification(self) -> None:
        candidate = self._journal(publisher="")
        ok, reason = journal_meets_hard_requirements(
            candidate,
            {"publishers": ["Elsevier"], "jcr_quartiles": ["Q1"], "cas_quartiles": ["1"]},
            verify_divisions=True,
        )

        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_jcr_and_cas_are_the_only_hard_conditions_when_easyscholar_is_ready(self) -> None:
        wrong = self._journal(publisher="Elsevier", quartile="Q2", cas="2")
        ok, reason = journal_meets_hard_requirements(
            wrong,
            {"publishers": ["Elsevier"], "fee_modes": ["no_fee"], "jcr_quartiles": ["Q1"], "cas_quartiles": ["1"]},
            verify_divisions=True,
        )

        self.assertFalse(ok)
        self.assertEqual(reason, "未满足 JCR 或中科院分区硬条件")

    def test_without_easyscholar_no_metadata_is_verified_or_hard_filtered(self) -> None:
        candidate = {"name": "Unknown Journal", "publisher": "Wiley", "fee_mode": "apc", "jcr": {"status": "pending", "metrics": []}}
        fetcher = Mock(side_effect=AssertionError("EasyScholar must not be called when disabled"))

        with patch(
            "utils.ai_service.recommend_journals_with_ai",
            return_value={
                "ranked": [],
                "external_candidates": [
                    {"name": "Unknown Journal", "publisher": "", "fee_mode": "apc", "fit_score": 88, "reason_cn": "摘要匹配"}
                ],
            },
        ):
            result = recommend_journals_iteratively_with_ai(
                {"title": "Paper", "summary": "Summary", "keywords": ["soil"]},
                [],
                {},
                {"publishers": ["Elsevier"], "fee_modes": ["no_fee"], "jcr_quartiles": ["Q1"], "cas_quartiles": ["1"]},
                easyscholar_ready=False,
                easyscholar_fetcher=fetcher,
                max_rounds=1,
            )

        self.assertEqual(result["verified_count"], 1)
        self.assertEqual(result["verification_mode"], "skipped")
        self.assertEqual(result["external_candidates"][0]["journal_name"], "Unknown Journal")
        fetcher.assert_not_called()
