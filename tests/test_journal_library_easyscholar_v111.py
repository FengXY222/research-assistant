"""Library-level contracts for optional EasyScholar updates."""

from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QDialogButtonBox, QScrollArea, QLabel

from tests import _data_root  # noqa: F401 - isolate data before UI imports
from ui.journal_library_page import CompactJournalRow, JournalLibraryDialog, JournalLibraryPage, JournalLibraryRow
from ui.main_window import MainWindow


class JournalLibraryEasyScholarV111Tests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.page = JournalLibraryPage()

    def tearDown(self) -> None:
        self.page.deleteLater()

    def test_manual_update_explains_that_the_optional_key_is_not_configured(self) -> None:
        messages: list[str] = []
        self.page._show_notice = messages.append  # type: ignore[method-assign]

        with patch("ui.journal_library_page.is_easyscholar_ready", return_value=False):
            self.page._update_easyscholar()

        self.assertEqual(len(messages), 1)
        self.assertIn("设置", messages[0])
        self.assertIn("EasyScholar", messages[0])

    def test_automatic_update_stays_quiet_when_the_service_is_not_configured(self) -> None:
        with patch("ui.journal_library_page.is_easyscholar_ready", return_value=False):
            self.assertFalse(self.page.auto_update_easyscholar_if_due())

    def test_main_window_runs_indicator_check_before_ai_enrichment(self) -> None:
        window = MainWindow()
        calls: list[str] = []
        window.journal_page.auto_update_easyscholar_if_due = lambda: calls.append("easyscholar") or False
        window.journal_page.auto_enrich_new_if_due = lambda: calls.append("deepseek") or False

        window._auto_enrich_new_journals()

        self.assertEqual(calls, ["easyscholar", "deepseek"])
        if window.tray_icon:
            window.tray_icon.hide()
        window._global_hotkey.close()
        window._visibility_hotkey.close()
        window.deleteLater()

    def test_editor_keeps_easyscholar_metrics_and_names_their_actual_source(self) -> None:
        dialog = JournalLibraryDialog(
            journal={
                "id": "soil",
                "name": "SOIL",
                "publisher": "Copernicus Publications",
                "jcr": {
                    "status": "verified",
                    "source": "EasyScholar Open API",
                    "metrics": [{"quartile": "Q2", "year": 2025}],
                },
                "easyscholar": {
                    "source": "EasyScholar Open API",
                    "checked_at": "2026-08-21",
                    "cas_upgrade": "2区",
                    "cas_basic": "3区",
                    "impact_factor": "5.6",
                    "impact_factor_5y": "6.1",
                    "jci": "0.88",
                },
            }
        )

        self.assertIn("EasyScholar", dialog.jcr_source_hint.text())
        self.assertNotIn("Clarivate", dialog.jcr_source_hint.text())
        self.assertIn("中科院升级版 2区", dialog.easyscholar_hint.text())
        self.assertIn("影响因子 5.6", dialog.easyscholar_hint.text())
        self.assertEqual(dialog.journal()["easyscholar"]["cas_upgrade"], "2区")
        dialog.close()

    def test_editor_keeps_save_actions_visible_when_metrics_make_the_form_tall(self) -> None:
        dialog = JournalLibraryDialog(
            journal={
                "name": "SOIL",
                "jcr": {"status": "verified", "source": "EasyScholar Open API", "metrics": [{"quartile": "Q2"}]},
                "easyscholar": {
                    "source": "EasyScholar Open API",
                    "cas_upgrade": "2区",
                    "cas_upgrade_top": "Top期刊",
                    "cas_upgrade_small": "2区",
                    "cas_basic": "3区",
                    "impact_factor": "5.6",
                    "impact_factor_5y": "6.1",
                    "jci": "0.88",
                },
            }
        )
        dialog.resize(380, 480)
        dialog.show()
        self.application.processEvents()

        self.assertIsNotNone(dialog.findChild(QScrollArea))
        actions = dialog.findChild(QDialogButtonBox)
        self.assertIsNotNone(actions)
        self.assertLessEqual(actions.geometry().bottom(), dialog.contentsRect().bottom())
        dialog.close()

    def test_journal_library_row_displays_cached_jcr_and_easyscholar_cas_metrics(self) -> None:
        row = JournalLibraryRow(
            {
                "id": "soil",
                "name": "SOIL",
                "publisher": "Copernicus",
                "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
                "easyscholar": {
                    "source": "EasyScholar Open API",
                    "checked_at": "2026-08-21",
                    "cas_upgrade": "2区",
                    "impact_factor": "5.6",
                },
            },
            {},
        )
        metric = row.findChild(QLabel, "journalMetricLine")
        self.assertIsNotNone(metric)
        self.assertIn("JCR Q1", metric.text())
        self.assertIn("中科院 2区", metric.text())
        self.assertIn("IF 5.6", metric.text())
        self.assertIn("2026-08-21", metric.toolTip())
        row.close()

    def test_compact_journal_library_row_names_the_cas_division(self) -> None:
        row = CompactJournalRow(
            {
                "id": "soil",
                "name": "SOIL",
                "publisher": "Copernicus",
                "jcr": {"status": "verified", "metrics": [{"quartile": "Q1"}]},
                "easyscholar": {"cas_upgrade": "2区"},
            }
        )

        badge = row.findChild(QLabel, "journalCasBadge")

        self.assertIsNotNone(badge)
        self.assertEqual(badge.text(), "中科院 2区")
        row.close()
