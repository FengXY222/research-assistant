from __future__ import annotations

from unittest import TestCase

from ui.workbench_shell import resolve_route
from utils import file_manager
from utils.research_profile_service import (
    add_pending_term,
    confirm_pending_term,
    normalize_research_profile_v11,
    reject_pending_term,
)


class V115DataContractTests(TestCase):
    def test_legacy_keywords_migrate_without_losing_old_fields(self) -> None:
        profile = normalize_research_profile_v11(
            {
                "primary_keywords": ["soil organic carbon"],
                "secondary_keywords": ["digital soil mapping"],
                "legacy_marker": {"keep": True},
            }
        )

        self.assertEqual(profile["legacy_marker"], {"keep": True})
        self.assertEqual([term["text"] for term in profile["terms"]], ["soil organic carbon", "digital soil mapping"])
        self.assertTrue(all(term["locked"] is False for term in profile["terms"]))

    def test_pending_term_requires_confirmation_before_entering_active_terms(self) -> None:
        profile = add_pending_term({}, "carbon sequestration", source="manual", confidence="high")

        self.assertEqual(profile["terms"], [])
        self.assertEqual(profile["pending_terms"][0]["text"], "carbon sequestration")
        self.assertEqual(profile["pending_terms"][0]["source"], "manual")

        confirmed = confirm_pending_term(profile, profile["pending_terms"][0]["id"], today="2026-08-24")

        self.assertEqual([term["text"] for term in confirmed["terms"]], ["carbon sequestration"])
        self.assertEqual(confirmed["terms"][0]["updated_at"], "2026-08-24")
        self.assertEqual(confirmed["pending_terms"], [])

    def test_rejecting_pending_term_keeps_a_reversible_log(self) -> None:
        profile = add_pending_term({}, "marine sediment", source="ai", confidence="low")
        rejected = reject_pending_term(profile, profile["pending_terms"][0]["id"], today="2026-08-24")

        self.assertEqual(rejected["terms"], [])
        self.assertEqual(rejected["pending_terms"], [])
        self.assertEqual(rejected["update_log"][-1]["kind"], "pending_rejected")
        self.assertEqual(rejected["update_log"][-1]["term"], "marine sediment")

    def test_library_route_defaults_to_daily_frontier(self) -> None:
        self.assertEqual(resolve_route("library").anchor, "frontier")

    def test_frontier_profile_loader_preserves_new_terms_and_pending_terms(self) -> None:
        profile = file_manager._normalize_frontier_profile(
            {
                "terms": [
                    {"id": "soc", "text": "soil organic carbon", "weight": 100, "locked": True},
                ],
                "pending_terms": [{"id": "pending-1", "text": "carbon sequestration"}],
                "filter_known_q3_q4": True,
            }
        )

        self.assertEqual(profile["terms"][0]["text"], "soil organic carbon")
        self.assertTrue(profile["terms"][0]["locked"])
        self.assertEqual(profile["pending_terms"][0]["text"], "carbon sequestration")
