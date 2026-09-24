"""UI contracts for the optional EasyScholar configuration card."""

from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from tests import _data_root  # noqa: F401 - isolate settings before UI imports
from ui.intelligence_dialog import IntelligenceSettingsDialog


class IntelligenceSettingsV111Tests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_easyscholar_card_round_trips_policy_without_revealing_saved_secret(self) -> None:
        with patch(
            "ui.intelligence_dialog.easyscholar_readiness",
            return_value={"ready": True, "needs_reentry": False, "state": "ready", "message": "可读取"},
        ):
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

    def test_unreadable_easyscholar_key_is_shown_as_needing_reentry(self) -> None:
        with patch(
            "ui.intelligence_dialog.easyscholar_readiness",
            return_value={
                "ready": False,
                "needs_reentry": True,
                "state": "unreadable",
                "message": "当前保存的 EasyScholar 密钥无法解密，请重新填写。",
            },
        ):
            dialog = IntelligenceSettingsDialog(
                {}, {}, {"enabled": True, "secret_key_secret": "broken-token"}
            )
        self.assertIn("无法读取", dialog.easyscholar_key.placeholderText())
        self.assertTrue(any("无法解密" in label.text() for label in dialog.findChildren(QLabel)))
        dialog.close()
