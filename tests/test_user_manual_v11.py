"""The generated user manual must describe the shipped v12 product."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest import TestCase

from docx import Document

from utils.app_info import APP_NAME, APP_VERSION


PROJECT = Path(__file__).resolve().parents[1]
BUILDER = PROJECT / "tools" / "build_user_manual.py"


class UserManualV12Tests(TestCase):
    def test_builder_creates_a_v12_manual_with_the_four_workbenches(self) -> None:
        with tempfile.TemporaryDirectory(prefix="research-assistant-v12-manual-") as temporary:
            output = Path(temporary) / f"{APP_NAME}-v{APP_VERSION}-使用手册.docx"
            result = subprocess.run(
                [sys.executable, str(BUILDER), str(output)],
                cwd=PROJECT,
                text=True,
                encoding="utf-8",
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output.is_file())
            text = "\n".join(paragraph.text for paragraph in Document(output).paragraphs)
            self.assertIn(f"科研助手 v{APP_VERSION}", text)
            self.assertIn("四个工作台", text)
            self.assertIn("今日下一步", text)
            self.assertIn("AI 内容相关性分数", text)
            self.assertIn("低权重探索", text)
            self.assertIn("只保留小组件模式", text)
            self.assertIn("特刊征稿", text)
            self.assertIn("扫描版 PDF", text)
            with zipfile.ZipFile(output) as archive:
                document_xml = archive.read("word/document.xml").decode("utf-8")
            self.assertIn("w:tblHeader", document_xml)
            self.assertIn("w:cantSplit", document_xml)
