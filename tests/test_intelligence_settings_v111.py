"""UI contracts for the optional EasyScholar configuration card."""

from __future__ import annotations

import os
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from tests import _data_root  # noqa: F401 - isolate settings before UI imports
from ui.intelligence_dialog import IntelligenceSettingsDialog


class IntelligenceSettingsV111Tests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_easyscholar_card_round_trips_policy_without_revealing_saved_secret(self) -> None:
        dialog = IntelligenceSettingsDialog(
            {},
            {},
            {
                "enabled": True,
                "secret_key_secret": "dpapi-token",
                "cache_days": 45,
                "last_auto_checked": "2026-08-21",
            },
        )

        self.assertTrue(dialog.easyscholar_enabled.isChecked())
        self.assertEqual(dialog.easyscholar_key.placeholderText(), "已保存（留空则保留）")
        self.assertEqual(dialog.easyscholar_cache_days.value(), 45)
        _, _, easyscholar = dialog.values()

        self.assertEqual(easyscholar["secret_key_secret"], "dpapi-token")
        self.assertEqual(easyscholar["cache_days"], 45)
        self.assertEqual(easyscholar["last_auto_checked"], "2026-08-21")
        dialog.close()
