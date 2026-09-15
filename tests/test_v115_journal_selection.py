from __future__ import annotations

from unittest import TestCase

from utils.journal_selection_service import apply_ai_selection_patch, normalize_selection_requirements, rank_ai_first_candidates


class V115JournalSelectionTests(TestCase):
    def test_ai_patch_keeps_display_total_score_in_sync(self) -> None:
        candidates = [{"journal_id": "j1", "journal_name": "Journal 1", "local_score": 60, "total_score": 60, "ai_total_score": 60}]

        result = apply_ai_selection_patch(candidates, {"ranked": [{"id": "j1", "adjustment": 7}]})

        self.assertEqual(result[0]["total_score"], 67)
        self.assertEqual(result[0]["ai_total_score"], result[0]["total_score"])

    def test_selection_requirements_support_publisher_fee_and_multi_quartile_filters(self) -> None:
        requirements = normalize_selection_requirements(
            {
                "publishers": ["Elsevier", "Springer Nature"],
                "fee_modes": ["no_fee", "paid"],
                "jcr_quartiles": ["Q1", "Q2"],
                "cas_quartiles": ["1", "2"],
                "speed_priority": "urgent",
            }
        )

        self.assertEqual(requirements["publishers"], ["Elsevier", "Springer Nature"])
        self.assertEqual(requirements["fee_modes"], ["no_fee", "paid"])
        self.assertEqual(requirements["jcr_quartiles"], ["Q1", "Q2"])
        self.assertEqual(requirements["cas_quartiles"], ["1", "2"])
        self.assertEqual(requirements["speed_priority"], "urgent")

    def test_fee_preference_is_soft_and_mixed_oa_still_scores_as_preferred(self) -> None:
        paper = {"title": "SOC mapping", "summary": "soil organic carbon mapping", "keywords": ["SOC"]}
        profile = {"terms": [{"text": "soil organic carbon", "weight": 100, "locked": True}]}
        journals = [
            {"id": "hybrid", "name": "Hybrid", "publisher": "Elsevier", "fee_mode": "hybrid", "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]}},
            {"id": "apc", "name": "APC", "publisher": "Elsevier", "fee_mode": "apc", "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]}},
        ]

        no_fee = rank_ai_first_candidates(paper, journals, profile, {}, {"fee_modes": ["no_fee"]})
        paid = rank_ai_first_candidates(paper, journals, profile, {}, {"fee_modes": ["paid"]})

        self.assertEqual([item["journal_id"] for item in no_fee], ["hybrid", "apc"])
        self.assertEqual([item["journal_id"] for item in paid], ["hybrid", "apc"])
        apc_row = next(item for item in no_fee if item["journal_id"] == "apc")
        self.assertIn("费用模式与当前偏好不一致", apc_row["risks"])

    def test_known_external_publisher_mismatch_is_not_admitted(self) -> None:
        paper = {"title": "SOC mapping", "summary": "soil organic carbon mapping", "keywords": ["SOC"]}
        result = rank_ai_first_candidates(
            paper,
            [],
            {},
            {},
            {"publishers": ["Elsevier"]},
            recommendation={
                "ranked": [],
                "external_candidates": [
                    {"name": "Wiley Journal", "publisher": "Wiley", "fit_score": 95, "reason_cn": "不应保留"},
                    {"name": "Unknown Journal", "publisher": "", "fit_score": 80, "reason_cn": "待核验"},
                ],
            },
            verify_divisions=False,
        )

        self.assertEqual([item["journal_name"] for item in result], ["Unknown Journal"])
        self.assertTrue(result[0]["is_external"])

    def test_multi_select_filters_use_local_publisher_jcr_and_cas_facts(self) -> None:
        paper = {"title": "SOC mapping", "summary": "soil organic carbon mapping", "keywords": ["SOC"]}
        profile = {"terms": [{"text": "soil organic carbon", "weight": 100, "locked": True}]}
        journals = [
            {
                "id": "match",
                "name": "Match",
                "publisher": "Elsevier",
                "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
                "easyscholar": {"cas_upgrade": "1区"},
            },
            {
                "id": "wrong-jcr",
                "name": "Wrong JCR",
                "publisher": "Elsevier",
                "jcr": {"status": "verified", "metrics": [{"quartile": "Q2"}]},
                "easyscholar": {"cas_upgrade": "1区"},
            },
            {
                "id": "unverified",
                "name": "Unverified",
                "publisher": "Elsevier",
                "jcr": {"status": "pending", "metrics": [{"quartile": "Q1"}]},
                "easyscholar": {"cas_upgrade": "1区"},
            },
            {
                "id": "wrong-cas",
                "name": "Wrong CAS",
                "publisher": "Springer Nature",
                "jcr": {"status": "manual", "metrics": [{"quartile": "Q1"}]},
                "easyscholar": {"cas_upgrade": "3区"},
            },
        ]

        result = rank_ai_first_candidates(
            paper,
            journals,
            profile,
            {},
            {"publishers": ["Elsevier", "Springer Nature"], "jcr_quartiles": ["Q1"], "cas_quartiles": ["1"]},
        )

        self.assertEqual([item["journal_id"] for item in result], ["match", "unverified"])
        self.assertIn("JCR 分区待核验", result[1]["risks"])

    def test_candidate_result_exposes_one_ai_total_score_and_reason(self) -> None:
        paper = {"title": "SOC mapping", "summary": "soil organic carbon mapping", "keywords": ["SOC"]}
        profile = {"terms": [{"text": "soil organic carbon", "weight": 100, "locked": True}]}
        journal = {"id": "j1", "name": "Journal 1", "publisher": "Elsevier", "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]}}

        result = rank_ai_first_candidates(
            paper,
            [journal],
            profile,
            {},
            {},
            recommendation={"ranked": [{"id": "j1", "fit_score": 92, "reason_cn": "摘要主题高度匹配"}]},
        )[0]

        self.assertEqual(result["ai_total_score"], result["total_score"])
        self.assertIn("摘要主题高度匹配", result["reason_cn"])

    def test_unknown_fee_records_remain_visible_but_are_marked_for_verification(self) -> None:
        """Migrated v11.2 journals have no fee field yet and must not blank the workbench."""
        paper = {"title": "SOC mapping", "summary": "soil organic carbon mapping", "keywords": ["SOC"]}
        journal = {
            "id": "legacy-fee",
            "name": "Legacy Journal",
            "publisher": "Elsevier",
            "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
        }

        result = rank_ai_first_candidates(paper, [journal], {}, {}, {"fee_modes": ["no_fee"]})

        self.assertEqual([item["journal_id"] for item in result], ["legacy-fee"])
        self.assertEqual(result[0]["fee_mode"], "unknown")
        self.assertTrue(result[0]["fee_pending_verification"])

    def test_candidate_keeps_the_ai_risk_for_the_result_row(self) -> None:
        paper = {"title": "SOC mapping", "summary": "soil organic carbon mapping", "keywords": ["SOC"]}
        journal = {"id": "j1", "name": "Journal 1", "publisher": "Elsevier", "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]}}

        result = rank_ai_first_candidates(
            paper,
            [journal],
            {},
            {},
            {},
            recommendation={"ranked": [{"id": "j1", "fit_score": 92, "reason_cn": "摘要主题高度匹配", "risk_cn": "投稿前需核对版面费与栏目范围"}]},
        )[0]

        self.assertEqual(result["risk_cn"], "投稿前需核对版面费与栏目范围")
