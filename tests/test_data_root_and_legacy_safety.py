"""Tests for the v11 persistence safety boundary."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from tests import _data_root  # noqa: F401 - must run before file_manager import
from utils import file_manager
from utils.storage_bootstrap import default_user_data_dir


class DataRootTests(TestCase):
    def test_explicit_test_root_never_equals_formal_install_data(self) -> None:
        active = file_manager.data_location().resolve()
        formal = Path(r"C:\Users\fxy17\AppData\Local\Programs\科研助手\data").resolve()

        self.assertEqual(active, _data_root.DATA_ROOT.resolve())
        self.assertNotEqual(active, formal)

    def test_frozen_runtime_prefers_existing_richer_data_over_smoke_copy(self) -> None:
        """A test/install bundle must not make a populated personal store disappear."""
        with TemporaryDirectory() as temp:
            root = Path(temp)
            bundle = root / "smoke-install"
            canonical = root / "local" / "Programs" / "科研助手" / "data"
            (bundle / "data").mkdir(parents=True)
            canonical.mkdir(parents=True)
            (bundle / "data" / "papers.json").write_text(
                '[{"id":"smoke-fixture"}]', encoding="utf-8"
            )
            (canonical / "papers.json").write_text(
                '[{"id":"paper-1"},{"id":"paper-2"}]', encoding="utf-8"
            )

            selected = file_manager._choose_frozen_data_dir(bundle, root / "local")

            self.assertEqual(selected, canonical)

    def test_v12_default_frozen_root_is_stable_user_data(self) -> None:
        local = Path(r"C:\Users\test\AppData\Local")

        self.assertEqual(default_user_data_dir(local), local / "科研助手" / "UserData")

    def test_research_profile_path_follows_the_active_data_root(self) -> None:
        self.assertEqual(file_manager.RESEARCH_PROFILE_FILE.parent, file_manager.DATA_DIR)
        self.assertEqual(file_manager.RESEARCH_PROFILE_FILE.name, "research_profile.json")

    def test_normalizing_paper_preserves_unknown_legacy_fields(self) -> None:
        paper = file_manager.normalize_paper(
            {
                "id": "paper-legacy",
                "title": "旧论文",
                "legacy_marker": {"keep": True},
                "journals": [],
            }
        )

        self.assertEqual(paper["legacy_marker"], {"keep": True})

    def test_normalizing_todo_returns_a_task_record(self) -> None:
        todo = file_manager._normalize_todo({"id": "todo-1", "title": "整理文献", "date": "2026-08-21"})

        self.assertEqual(todo["id"], "todo-1")
        self.assertEqual(todo["title"], "整理文献")
