"""Compact-library v11 data and layout contracts."""

from __future__ import annotations

from unittest import TestCase

from utils.journal_health_service import journal_row_model, publisher_group_key


class JournalLibraryV11Tests(TestCase):
    def test_publisher_grouping_uses_canonical_parent_family_case_insensitively(self) -> None:
        first = publisher_group_key({"publisher": "Elsevier B.V."})
        second = publisher_group_key({"publisher": "elsevier"})

        self.assertEqual(first, "Elsevier")
        self.assertEqual(first, second)

    def test_compact_row_model_keeps_name_and_exposes_one_jcr_state(self) -> None:
        model = journal_row_model(
            {
                "id": "j1",
                "name": "Journal of Very Long Environmental Research Name",
                "publisher": "Elsevier",
                "fields": ["土壤碳", "土地利用变化", "遥感"],
                "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
            }
        )

        self.assertEqual(model["name"], "Journal of Very Long Environmental Research Name")
        self.assertEqual(model["jcr_state"], "已核验 Q1")
        self.assertIn("土壤碳", model["tags"])
        self.assertEqual(model["more_actions"], ["收藏", "更新资料", "删除"])

    def test_unverified_jcr_is_labelled_as_pending_jcr_not_generic_incomplete(self) -> None:
        model = journal_row_model(
            {
                "id": "j2",
                "name": "SOIL",
                "publisher": "Copernicus Publications",
                "ai_tags": ["土壤学"],
                "jcr": {"status": "pending", "metrics": []},
            }
        )

        self.assertEqual(model["jcr_state"], "JCR 待补")
