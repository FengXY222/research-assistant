"""Black-box tests for the read-only v11 data audit command."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import TestCase


PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "tools" / "self_check_v110.py"


class SelfCheckV110Tests(TestCase):
    def test_manual_windows_smoke_checklist_is_available_without_opening_data(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--manual-smoke-checklist"],
            cwd=PROJECT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("双击空格", result.stdout)
        self.assertIn("中文输入法", result.stdout)
        self.assertIn("系统托盘", result.stdout)

    def test_read_only_audit_reports_counts_without_rewriting_fixture(self) -> None:
        with tempfile.TemporaryDirectory(prefix="research-assistant-audit-") as temporary:
            data = Path(temporary) / "data"
            data.mkdir()
            papers = [{"id": "paper-1", "title": "SOC", "journals": [{"id": "journal-1", "name": "CATENA", "date": "2026-08-20"}]}]
            source = data / "papers.json"
            source.write_text(json.dumps(papers, ensure_ascii=False), encoding="utf-8")
            before = source.read_bytes()

            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--data-root", str(data), "--read-only"],
                cwd=PROJECT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["record_counts"]["papers"], 1)
            self.assertEqual(report["record_counts"]["journal_entries"], 1)
            self.assertEqual(source.read_bytes(), before)

    def test_future_revision_due_date_is_not_reported_as_invalid_submission_date(self) -> None:
        with tempfile.TemporaryDirectory(prefix="research-assistant-audit-") as temporary:
            data = Path(temporary) / "data"
            data.mkdir()
            (data / "papers.json").write_text(
                json.dumps(
                    [
                        {
                            "id": "paper-1",
                            "title": "SOC",
                            "journals": [
                                {
                                    "id": "journal-1",
                                    "name": "CATENA",
                                    "date": "2026-08-20",
                                    "status_updated_at": "2026-08-20",
                                    "revision_due_date": "2026-10-18",
                                }
                            ],
                        }
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--data-root", str(data), "--read-only"],
                cwd=PROJECT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["future_dates"], [])
