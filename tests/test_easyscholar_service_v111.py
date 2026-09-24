"""Contracts for the optional, cached EasyScholar journal-data adapter."""

from __future__ import annotations

from unittest import TestCase

from tests import _data_root  # noqa: F401 - isolate settings persistence before service imports
from utils.file_manager import load_app_settings, normalize_app_settings, normalize_library_journal, save_app_settings
from utils.jcr_service import jcr_label

try:
    from utils.easyscholar_service import (
        EasyScholarConfigurationError,
        EasyScholarRequestError,
        easyscholar_readiness,
        enrich_journals_with_easyscholar,
        fetch_easyscholar_metrics,
        journal_easyscholar_signature,
        journal_needs_easyscholar_update,
        merge_easyscholar_patch,
        parse_easyscholar_rank_payload,
    )
except ImportError:  # The first red run intentionally documents the missing adapter.
    EasyScholarConfigurationError = RuntimeError
    EasyScholarRequestError = RuntimeError
    easyscholar_readiness = None
    enrich_journals_with_easyscholar = None
    fetch_easyscholar_metrics = None
    journal_easyscholar_signature = None
    journal_needs_easyscholar_update = None
    merge_easyscholar_patch = None
    parse_easyscholar_rank_payload = None


class EasyScholarServiceV111Tests(TestCase):
    def setUp(self) -> None:
        self.payload = {
            "data": {
                "officialRank": {
                    "all": {
                        "sci": "Q2",
                        "sciUp": "2区",
                        "sciBase": "3区",
                        "sciif": "5.6",
                        "sciif5": "6.1",
                        "eii": "是",
                        "esci": "否",
                    }
                }
            }
        }
        self.journal = {"id": "soil", "name": "SOIL", "publisher": "Copernicus", "issn": "2199-398X"}

    def test_readiness_requires_the_saved_token_to_be_decryptable(self) -> None:
        readable = easyscholar_readiness(
            {"enabled": True, "secret_key_secret": "token"},
            revealer=lambda _token: "real-key",
        )

        def unreadable(_token: str) -> str:
            from utils.secure_store import SecretStoreError

            raise SecretStoreError("broken")

        broken = easyscholar_readiness(
            {"enabled": True, "secret_key_secret": "token"},
            revealer=unreadable,
        )
        self.assertTrue(readable["ready"])
        self.assertFalse(broken["ready"])
        self.assertEqual(broken["state"], "unreadable")
        self.assertTrue(broken["needs_reentry"])

    def test_parser_converts_public_rank_fields_into_source_labeled_metadata(self) -> None:
        self.assertTrue(callable(parse_easyscholar_rank_payload))

        patch = parse_easyscholar_rank_payload(self.payload, checked_at="2026-08-21")

        self.assertEqual(patch["jcr"]["status"], "verified")
        self.assertEqual(patch["jcr"]["source"], "EasyScholar Open API")
        self.assertEqual(patch["jcr"]["metrics"][0]["quartile"], "Q2")
        self.assertEqual(patch["easyscholar"]["cas_upgrade"], "2区")
        self.assertEqual(patch["easyscholar"]["cas_basic"], "3区")
        self.assertEqual(patch["easyscholar"]["impact_factor"], "5.6")
        self.assertEqual(patch["easyscholar"]["impact_factor_5y"], "6.1")
        self.assertTrue(patch["easyscholar"]["ei"])

    def test_parser_accepts_the_chinese_zone_format_returned_by_some_rank_records(self) -> None:
        payload = {"code": 200, "msg": "SUCCESS", "data": {"officialRank": {"all": {"sci": "2区"}}}}

        patch = parse_easyscholar_rank_payload(payload, checked_at="2026-08-21")

        self.assertEqual(patch["jcr"]["status"], "verified")
        self.assertEqual(patch["jcr"]["metrics"][0]["quartile"], "Q2")

    def test_non_success_api_payload_is_reported_as_a_safe_request_error(self) -> None:
        self.assertTrue(callable(fetch_easyscholar_metrics))

        with self.assertRaises(EasyScholarRequestError) as context:
            fetch_easyscholar_metrics(
                self.journal,
                secret_key="not-for-logs",
                request_json=lambda _journal, _key: {"code": 40002, "msg": "key invalid"},
                today="2026-08-21",
            )

        self.assertNotIn("not-for-logs", str(context.exception))

    def test_manual_or_clarivate_jcr_wins_while_easyscholar_secondary_fields_are_kept(self) -> None:
        self.assertTrue(callable(merge_easyscholar_patch))
        manual = {
            **self.journal,
            "jcr_locked": True,
            "jcr": {"status": "manual", "source": "手动", "metrics": [{"quartile": "Q1"}]},
        }
        patch = parse_easyscholar_rank_payload(self.payload, checked_at="2026-08-21")

        updated = merge_easyscholar_patch(manual, patch, query_signature="soil-signature")

        self.assertEqual(updated["jcr"]["source"], "手动")
        self.assertEqual(updated["jcr"]["metrics"][0]["quartile"], "Q1")
        self.assertEqual(updated["easyscholar"]["cas_upgrade"], "2区")
        self.assertEqual(updated["easyscholar"]["query_signature"], "soil-signature")

    def test_unchanged_recent_journal_is_skipped_without_a_request(self) -> None:
        self.assertTrue(callable(journal_easyscholar_signature))
        self.assertTrue(callable(journal_needs_easyscholar_update))
        signature = journal_easyscholar_signature(self.journal)
        fresh = {
            **self.journal,
            "easyscholar": {"checked_at": "2026-08-20", "query_signature": signature, "cas_upgrade": "2区"},
        }

        self.assertFalse(journal_needs_easyscholar_update(fresh, today="2026-08-21", cache_days=30))

    def test_batch_calls_only_stale_records_and_never_exposes_the_secret_in_errors(self) -> None:
        self.assertTrue(callable(enrich_journals_with_easyscholar))
        signature = journal_easyscholar_signature(self.journal)
        fresh = {
            **self.journal,
            "id": "fresh",
            "easyscholar": {"checked_at": "2026-08-21", "query_signature": signature},
        }
        stale = {**self.journal, "id": "stale", "name": "CATENA"}
        requested: list[str] = []

        def fake_request(journal: dict, _secret: str) -> dict:
            requested.append(str(journal["id"]))
            return self.payload

        result = enrich_journals_with_easyscholar(
            [fresh, stale],
            secret_key="not-for-logs",
            today="2026-08-21",
            request_json=fake_request,
        )

        self.assertEqual(requested, ["stale"])
        self.assertEqual(result["changed"], 1)
        self.assertEqual(result["journals"][0]["id"], "fresh")
        self.assertEqual(result["journals"][1]["jcr"]["status"], "verified")

        def failing_request(_journal: dict, _secret: str) -> dict:
            raise RuntimeError("not-for-logs")

        failed = enrich_journals_with_easyscholar(
            [stale],
            secret_key="not-for-logs",
            today="2026-08-21",
            request_json=failing_request,
        )
        self.assertEqual(len(failed["errors"]), 1)
        self.assertNotIn("not-for-logs", failed["errors"][0])

    def test_settings_keep_the_encrypted_key_and_cache_policy_without_dropping_them(self) -> None:
        original = load_app_settings()
        updated = dict(original)
        updated["easyscholar"] = {"enabled": True, "secret_key_secret": "dpapi-token", "cache_days": 45}
        try:
            save_app_settings(updated)
            saved = load_app_settings()
        finally:
            save_app_settings(original)

        self.assertEqual(
            saved["easyscholar"],
            {
                "enabled": True,
                "secret_key_secret": "dpapi-token",
                "cache_days": 45,
                "last_auto_checked": "",
            },
        )

    def test_settings_supply_a_complete_easyscholar_policy_for_new_installations(self) -> None:
        settings = normalize_app_settings({})

        self.assertEqual(
            settings["easyscholar"],
            {
                "enabled": False,
                "secret_key_secret": "",
                "cache_days": 30,
                "last_auto_checked": "",
            },
        )

    def test_journal_normalization_keeps_easyscholar_metrics_in_a_safe_shape(self) -> None:
        journal = normalize_library_journal(
            {
                "name": "SOIL",
                "easyscholar": {
                    "source": "EasyScholar Open API",
                    "checked_at": "2026-08-21",
                    "query_signature": "x" * 100,
                    "cas_upgrade": "2区",
                    "cas_upgrade_top": "Top期刊",
                    "cas_upgrade_small": "2区",
                    "cas_basic": "3区",
                    "impact_factor": "5.6",
                    "impact_factor_5y": "6.1",
                    "ei": "yes",
                    "esci": 0,
                },
            }
        )

        self.assertEqual(
            journal["easyscholar"],
            {
                "source": "EasyScholar Open API",
                "checked_at": "2026-08-21",
                "query_signature": "x" * 80,
                "cas_upgrade": "2区",
                "cas_upgrade_top": "Top期刊",
                "cas_upgrade_small": "2区",
                "cas_basic": "3区",
                "impact_factor": "5.6",
                "impact_factor_5y": "6.1",
                "ei": True,
                "esci": False,
                "jci": "",
            },
        )

    def test_library_label_distinguishes_easyscholar_sync_from_clarivate_jcr(self) -> None:
        label = jcr_label(
            {
                "jcr": {
                    "status": "verified",
                    "source": "EasyScholar Open API",
                    "metrics": [{"quartile": "Q2"}],
                }
            }
        )

        self.assertEqual(label, "EasyScholar Q2")
