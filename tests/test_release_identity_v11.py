"""Release identity and installer-preservation contracts for v12."""

from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path
from unittest import TestCase

from utils.app_info import APP_NAME, APP_VERSION, MIN_SUPPORTED_DATA_VERSION


PROJECT = Path(__file__).resolve().parents[1]
ISS = PROJECT / "科研助手.iss"
BUILD = PROJECT / "build_windows.ps1"
VERSION_INFO = PROJECT / "version_info.txt"
VERIFY = PROJECT / "tools" / "verify_upgrade_v110.py"
FORMAL_DATA = Path(r"C:\Users\fxy17\AppData\Local\Programs\科研助手\data")
STABLE_FORMAL_DATA = Path(os.environ["LOCALAPPDATA"]) / APP_NAME / "UserData"


class ReleaseIdentityV12Tests(TestCase):
    def test_every_release_identity_declares_v12(self) -> None:
        self.assertEqual(APP_VERSION, "12.2.1")
        self.assertEqual(MIN_SUPPORTED_DATA_VERSION, "10.0")
        self.assertIn(f'#define MyAppVersion "{APP_VERSION}"', ISS.read_text(encoding="utf-8"))
        self.assertIn(f'$appVersion = "{APP_VERSION}"', BUILD.read_text(encoding="utf-8"))
        version_info = VERSION_INFO.read_text(encoding="utf-8")
        self.assertIn("filevers=(12, 2, 1, 0)", version_info)
        self.assertIn(APP_NAME, version_info)

    def test_installer_keeps_existing_data_when_upgrading_in_place(self) -> None:
        installer = ISS.read_text(encoding="utf-8")

        self.assertIn("UsePreviousAppDir=yes", installer)
        self.assertIn('Name: "{app}\\data"; Flags: uninsneveruninstall', installer)
        self.assertIn(f"OutputBaseFilename={APP_NAME}-v{APP_VERSION}-安装包", installer)

    def test_installer_does_not_drop_nested_dependency_data_directories(self) -> None:
        installer = ISS.read_text(encoding="utf-8")
        build = BUILD.read_text(encoding="utf-8")

        self.assertNotIn('Excludes: "data\\*"', installer)
        self.assertIn("packagedDataPath", build)
        self.assertIn("Test-Path -LiteralPath $packagedDataPath", build)

    def test_installer_removes_legacy_poppler_icu_before_upgrade(self) -> None:
        installer = ISS.read_text(encoding="utf-8")

        self.assertIn("[InstallDelete]", installer)
        self.assertIn('Type: filesandordirs; Name: "{app}\\_internal"', installer)
        self.assertIn('Name: "{app}\\_internal\\icuuc.dll"', installer)
        self.assertIn('Name: "{app}\\_internal\\icudt*.dll"', installer)
        self.assertNotIn('Type: filesandordirs; Name: "{app}\\data"', installer)

    def test_build_collects_the_complete_local_ocr_runtime(self) -> None:
        build = BUILD.read_text(encoding="utf-8")
        requirements = (PROJECT / "requirements.txt").read_text(encoding="utf-8")

        self.assertIn('$buildEnvironment = Join-Path $PSScriptRoot ".build-venv"', build)
        self.assertIn("-m venv $buildEnvironment", build)
        self.assertIn('"--collect-data", "rapidocr"', build)
        for module in ("rapidocr.main", "onnxruntime", "pypdfium2"):
            self.assertIn(f'"--hidden-import", "{module}"', build)
        self.assertIn('"--collect-binaries", "ctranslate2"', build)
        self.assertIn('"--hidden-import", "sentencepiece"', build)
        self.assertGreaterEqual(build.count("$bundledTranslationData"), 2)
        self.assertIn("pywin32", requirements)
        unused_modules = (
            "torch",
            "tensorflow",
            "tensorrt",
            "openvino",
            "paddle",
            "PyQt5",
            "PyQt6",
            "PySide2",
            "matplotlib",
            "pandas",
            "scipy",
            "pytest",
            "numba",
            "IPython",
            "sphinx",
            "docutils",
            "nbformat",
            "zmq",
            "tkinter",
        )
        for unused_module in unused_modules:
            self.assertIn(f'"{unused_module}"', build)

    def test_build_rejects_codex_poppler_icu_that_breaks_qtcore(self) -> None:
        build = BUILD.read_text(encoding="utf-8")

        self.assertIn("dependencies[\\\\/]native[\\\\/]poppler", build)
        self.assertIn("$originalPath", build)
        self.assertIn("$env:PATH = $originalPath", build)
        self.assertIn("$conflictingIcuFiles", build)
        self.assertIn('"icuuc.dll"', build)
        self.assertIn('"icudt*.dll"', build)

    def test_upgrade_verifier_refuses_the_formal_data_directory_without_explicit_override(self) -> None:
        result = subprocess.run(
            [sys.executable, str(VERIFY), "--data-root", str(FORMAL_DATA)],
            cwd=PROJECT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("正式数据目录", result.stderr + result.stdout)

    def test_upgrade_verifier_also_protects_the_current_stable_user_data_directory(self) -> None:
        result = subprocess.run(
            [sys.executable, str(VERIFY), "--data-root", str(STABLE_FORMAL_DATA)],
            cwd=PROJECT,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("正式数据目录", result.stderr + result.stdout)

    def test_release_backup_script_uses_copy_and_sha256_verification(self) -> None:
        script = (PROJECT / "tools" / "backup_release_data.ps1").read_text(encoding="utf-8")
        self.assertIn("Copy-Item", script)
        self.assertIn("Get-FileHash", script)
        self.assertIn("hash_verified", script)
        self.assertNotIn("Remove-Item", script)

    def test_release_backup_script_checks_directory_topology_and_collisions(self) -> None:
        script = (PROJECT / "tools" / "backup_release_data.ps1").read_text(encoding="utf-8")
        self.assertIn("Get-ChildItem -LiteralPath $sourcePath -Directory -Recurse", script)
        self.assertIn("$sourceDirectoriesByRelativePath", script)
        self.assertIn("$backupDirectoriesByRelativePath", script)
        self.assertIn("while ($true)", script)
        self.assertIn("Test-Path -LiteralPath $destination -PathType Container", script)
