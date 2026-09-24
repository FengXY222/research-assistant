"""Provider-independent contracts guarding v11 DeepSeek integration."""

from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch

# ``unittest discover -s tests`` imports this alphabetically-first module as a
# top-level module, bypassing ``tests.__init__``.  Bind the isolated data root
# before ai_service reaches file_manager transitively.
from tests import _data_root  # noqa: F401
from utils.ai_service import (
    assess_verified_journal_fit_with_ai,
    rank_journals_with_ai,
    recommend_journals_with_ai,
    rerank_frontier_with_ai,
    validate_ai_first_recommendation,
    validate_profile_proposal,
    validate_selection_ai_patch,
)


class AiContractsTests(TestCase):
    def test_profile_contract_rejects_weight_100_for_unlocked_ai_term(self) -> None:
        with self.assertRaises(ValueError):
            validate_profile_proposal(
                {"terms": [{"text": "SOC", "weight": 100, "locked": False, "evidence": ["成果 1"]}]}
            )

    def test_profile_contract_keeps_only_whitelisted_fields_and_evidence(self) -> None:
        result = validate_profile_proposal(
            {
                "terms": [{"text": "SOC", "weight": 88, "evidence": ["成果论文关键词"], "unexpected": "discard"}],
                "excluded_terms": ["marine sediment"],
                "search_terms": ["soil carbon mapping"],
                "unexpected": "discard",
            }
        )

        self.assertEqual(result["terms"][0]["weight"], 88)
        self.assertNotIn("unexpected", result)
        self.assertEqual(result["terms"][0]["evidence"], ["成果论文关键词"])

    def test_journal_selection_ai_adjustment_is_limited_to_minus_ten_to_plus_ten(self) -> None:
        result = validate_selection_ai_patch(
            {"ranked": [{"id": "catena", "adjustment": 80, "reason_cn": "主题适配", "risk_cn": "核验分区"}]},
            {"catena"},
        )

        self.assertEqual(result["ranked"][0]["adjustment"], 10)
        self.assertEqual(result["ranked"][0]["reason_cn"], "主题适配")

    def test_journal_selection_ai_drops_unknown_candidate_ids(self) -> None:
        result = validate_selection_ai_patch({"ranked": [{"id": "invented", "adjustment": 5}]}, {"catena"})

        self.assertEqual(result["ranked"], [])

    def test_rank_journals_returns_bounded_adjustment_instead_of_second_total_score(self) -> None:
        with patch("utils.ai_service._require_config", return_value=({"model": "deepseek-test"}, "key")), patch(
            "utils.ai_service._chat_json",
            return_value={"ranked": [{"id": "catena", "adjustment": 9, "reason_cn": "主题适合", "risk_cn": "核验 OA"}]},
        ):
            result = rank_journals_with_ai(
                {"title": "SOC", "keywords": ["SOC"]},
                [{"id": "catena", "name": "CATENA"}],
                {"terms": [{"text": "SOC", "weight": 100, "locked": True}]},
            )

        self.assertEqual(result["ranked"][0]["adjustment"], 9)
        self.assertNotIn("score", result["ranked"][0])

    def test_ai_first_selection_accepts_only_known_library_ids_and_explicit_external_candidates(self) -> None:
        result = validate_ai_first_recommendation(
            {
                "ranked": [
                    {"id": "catena", "fit_score": 180, "reason_cn": "摘要契合", "oa_status": "yes"},
                    {"id": "invented", "fit_score": 99},
                ],
                "external_candidates": [
                    {"name": "Land Journal", "publisher": "Press", "fit_score": 88, "reason_cn": "扩展候选"},
                    {"name": "", "fit_score": 99},
                ],
            },
            {"catena"},
        )

        self.assertEqual(result["ranked"], [{"id": "catena", "fit_score": 100, "reason_cn": "摘要契合", "risk_cn": "", "oa_status": "yes", "estimated_decision_days_min": 0, "estimated_decision_days_max": 0, "time_confidence": ""}])
        self.assertEqual(len(result["external_candidates"]), 1)
        self.assertEqual(result["external_candidates"][0]["name"], "Land Journal")
        self.assertNotIn("jcr", result["external_candidates"][0])

    def test_ai_first_selection_forwards_research_summary_and_requirements_to_the_model(self) -> None:
        captured: dict = {}

        def fake_chat(_config, _key, _system, payload, _limit):
            captured.update(payload)
            return {"ranked": [{"id": "catena", "fit_score": 90, "reason_cn": "摘要和方法一致"}], "external_candidates": []}

        with patch("utils.ai_service._require_config", return_value=({"model": "deepseek-test"}, "key")), patch(
            "utils.ai_service._chat_json", side_effect=fake_chat
        ):
            result = recommend_journals_with_ai(
                {"title": "SOC", "summary": "完整研究摘要", "keywords": ["SOC"]},
                [{"id": "catena", "name": "CATENA", "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]}}],
                {"terms": [{"text": "SOC", "weight": 100, "locked": True}]},
                {"oa_mode": "require", "quartile_target": "q1_q2", "speed_priority": "urgent"},
            )

        self.assertEqual(captured["paper"]["research_summary"], "完整研究摘要")
        self.assertEqual(captured["requirements"]["oa_mode"], "require")
        self.assertEqual(captured["requirements"]["speed_priority"], "urgent")
        self.assertEqual(result["ranked"][0]["fit_score"], 90)

    def test_ai_first_selection_has_no_count_quota_and_keeps_verified_local_candidates(self) -> None:
        captured: dict = {}

        def fake_chat(_config, _key, system, payload, _limit):
            captured["system"] = system
            captured.update(payload)
            return {"ranked": [{"id": "catena", "fit_score": 90, "reason_cn": "摘要方法一致"}], "external_candidates": []}

        journals = [
            {"id": "catena", "name": "CATENA", "publisher": "Elsevier", "fee_mode": "hybrid", "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]}},
            {"id": "soil", "name": "Soil Journal", "publisher": "Elsevier", "fee_mode": "hybrid", "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]}},
        ]
        with patch("utils.ai_service._require_config", return_value=({"model": "deepseek-test"}, "key")), patch(
            "utils.ai_service._chat_json", side_effect=fake_chat
        ):
            result = recommend_journals_with_ai(
                {"title": "SOC", "summary": "完整研究摘要", "keywords": ["SOC"]},
                journals,
                {},
                {"publishers": ["Elsevier"], "jcr_quartiles": ["Q1"], "fee_modes": ["no_fee"]},
            )

        self.assertIn("不设置数量目标", captured["task"])
        self.assertEqual(captured["requirements"]["publishers"], ["Elsevier"])
        self.assertEqual(captured["requirements"]["jcr_quartiles"], ["Q1"])
        self.assertEqual([item["id"] for item in result["ranked"]], ["catena", "soil"])
        self.assertFalse(result["fallback_reason_cn"])

    def test_ai_first_selection_drops_provider_rows_with_known_wrong_publisher(self) -> None:
        journals = [
            {"id": "elsevier", "name": "CATENA", "publisher": "Elsevier", "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]}},
            *[
                {"id": f"wiley-{index}", "name": f"Wiley {index}", "publisher": "Wiley", "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]}}
                for index in range(5)
            ],
        ]
        provider_result = {
            "ranked": [
                {"id": f"wiley-{index}", "fit_score": 90 - index, "reason_cn": "出版社不符合当前要求"}
                for index in range(5)
            ],
            "external_candidates": [],
        }

        with patch("utils.ai_service._require_config", return_value=({"model": "deepseek-test"}, "key")), patch(
            "utils.ai_service._chat_json", return_value=provider_result
        ):
            result = recommend_journals_with_ai(
                {"title": "SOC", "summary": "完整研究摘要", "keywords": ["SOC"]},
                journals,
                {},
                {"publishers": ["Elsevier"]},
            )

        self.assertEqual([item["id"] for item in result["ranked"]], ["elsevier"])
        self.assertFalse(result["fallback_reason_cn"])

    def test_frontier_ai_rerank_returns_two_bounded_axis_adjustments(self) -> None:
        with patch("utils.ai_service._require_config", return_value=({"model": "deepseek-test"}, "key")), patch(
            "utils.ai_service._chat_json",
            return_value={"ranked": [{"id": "frontier-1", "axes": {
                "relevance": {"adjustment": 30, "confidence": "high", "reason": "主题匹配", "evidence_refs": ["title"]},
                "value": {"adjustment": 22, "confidence": "medium", "reason": "期刊证据可用", "evidence_refs": ["journal"]},
            }, "summary_cn": "中文速览"}]},
        ):
            result = rerank_frontier_with_ai(
                {"terms": [{"text": "SOC", "weight": 100, "locked": True}]},
                [{"id": "frontier-1", "title": "SOC paper", "journal": "CATENA", "score": 120}],
            )

        self.assertEqual(result["ranked"][0]["ai_axis_payload"]["axes"]["relevance"]["adjustment"], 30)
        self.assertEqual(result["ranked"][0]["ai_axis_payload"]["axes"]["value"]["adjustment"], 22)
        self.assertNotIn("score", result["ranked"][0])

    def test_v12_fit_assessment_only_scores_verified_stable_ids(self) -> None:
        captured = {}
        progress = []

        def fake_chat(_config, _key, system, payload, _limit):
            captured.update({"system": system, "payload": payload})
            return {
                "assessments": [
                    {
                        "id": "issn:0341-8162",
                        "axes": {
                            "fit": {"adjustment": 30, "confidence": "high", "reason": "研究对象与空间制图方法契合", "evidence_refs": ["paper_abstract", "journal_scope"]},
                            "strategy": {"adjustment": 20, "confidence": "medium", "reason": "投稿目标匹配", "evidence_refs": ["journal_facts"]},
                        },
                    },
                    {"id": "invented", "fit_score": 99, "reason_cn": "不得进入"},
                ]
            }

        with patch("utils.ai_service._require_config", return_value=({"model": "deepseek-test"}, "key")), patch(
            "utils.ai_service._chat_json", side_effect=fake_chat
        ):
            result = assess_verified_journal_fit_with_ai(
                {"title": "SOC mapping", "summary": "完整摘要", "keywords": ["soil carbon"]},
                [
                    {
                        "id": "issn:0341-8162",
                        "name": "CATENA",
                        "publisher": "Elsevier",
                        "issns": ["0341-8162"],
                        "website": "https://example.org/catena",
                        "identity_evidence": {"verified": True},
                        "similar_papers": [{"title": "Related paper"}],
                    }
                ],
                {"fit_strictness": "balanced"},
                progress=lambda message, value=None: progress.append((message, value)),
            )

        self.assertEqual(set(result), {"issn:0341-8162"})
        self.assertIsNone(result["issn:0341-8162"]["fit_score"])
        self.assertEqual(result["issn:0341-8162"]["ai_axis_payload"]["axes"]["fit"]["adjustment"], 30)
        self.assertIn("不能新增", captured["system"])
        self.assertEqual(progress[0][1], 10)
        self.assertEqual(progress[-1][1], 100)
