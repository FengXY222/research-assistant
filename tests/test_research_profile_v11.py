"""Unit tests for the lossless weighted v11 research profile."""

from __future__ import annotations

from unittest import TestCase

from utils.research_profile_service import normalize_research_profile_v11, profile_source_signature, reconcile_profile_proposal


class ResearchProfileV11Tests(TestCase):
    def test_legacy_terms_are_migrated_without_deleting_legacy_fields(self) -> None:
        profile = normalize_research_profile_v11(
            {
                "primary_keywords": ["SOC"],
                "secondary_keywords": ["MAOC"],
                "feedback": {"term_weights": {"soc": 2}},
            }
        )

        weights = {term["text"].casefold(): term["weight"] for term in profile["terms"]}
        self.assertEqual(weights["soc"], 72)
        self.assertEqual(weights["maoc"], 45)
        self.assertEqual(profile["primary_keywords"], ["SOC"])
        self.assertEqual(profile["secondary_keywords"], ["MAOC"])
        self.assertTrue(profile["filter_known_q3_q4"])

    def test_locked_term_wins_over_ai_proposal_and_exclusion_conflict_is_logged(self) -> None:
        profile = normalize_research_profile_v11(
            {"terms": [{"text": "SOC", "weight": 100, "locked": True, "evidence": ["manual_lock"]}]}
        )

        revised, log = reconcile_profile_proposal(
            profile,
            {
                "terms": [
                    {"text": "SOC", "weight": 20, "evidence": ["model proposal"]},
                    {"text": "marine sediment", "weight": 90, "evidence": ["paper evidence"]},
                ],
                "excluded_terms": ["SOC", "marine sediment"],
            },
            today="2026-08-21",
        )

        active = {term["text"].casefold(): term for term in revised["terms"]}
        self.assertEqual(active["soc"]["weight"], 100)
        self.assertTrue(active["soc"]["locked"])
        self.assertNotIn("marine sediment", active)
        self.assertIn("marine sediment", [term.casefold() for term in revised["excluded_terms"]])
        self.assertTrue(any(entry["kind"] == "conflict" and entry["term"] == "SOC" for entry in log))

    def test_evidence_free_new_ai_term_stays_pending(self) -> None:
        profile = normalize_research_profile_v11({"terms": [{"text": "SOC", "weight": 80}]})

        revised, _log = reconcile_profile_proposal(
            profile,
            {"terms": [{"text": "black carbon", "weight": 75, "evidence": []}]},
            today="2026-08-21",
        )

        self.assertNotIn("black carbon", [term["text"].casefold() for term in revised["terms"]])
        self.assertEqual(revised["pending_terms"][0]["text"], "black carbon")

    def test_profile_exclusions_do_not_coexist_with_active_terms(self) -> None:
        profile = normalize_research_profile_v11(
            {
                "terms": [{"text": "marine sediment", "weight": 80}],
                "excluded_terms": ["marine sediment"],
            }
        )

        self.assertEqual(profile["terms"], [])
        self.assertEqual(profile["excluded_terms"], ["marine sediment"])

    def test_source_signature_changes_for_a_new_frontier_feedback_event(self) -> None:
        settings = {"auto_profile_from_achievements": True, "auto_profile_from_frontier": True}
        first = profile_source_signature([], [{"id": "f1", "feedback_events": []}], settings)
        second = profile_source_signature(
            [],
            [{"id": "f1", "feedback_events": [{"id": "event-1", "at": "2026-08-21T10:00:00", "action": "one_line_feedback"}]}],
            settings,
        )

        self.assertNotEqual(first, second)
