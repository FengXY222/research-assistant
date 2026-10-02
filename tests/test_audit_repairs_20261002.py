"""Regression tests for concurrency and failure scenarios missed by the audit suite."""
from contextlib import closing
from datetime import datetime
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from utils.app_data_store import AppDataStore, StaleSnapshotError, migrate_legacy_app_data
from utils.database_write_queue import DatabaseWriteQueue
from utils.evidence_cache import EvidenceCache
from utils.v13_pipeline import begin_batch, checkpoint, stage_payload


def test_delayed_snapshot_cannot_overwrite_new_results(tmp_path):
    store = AppDataStore(tmp_path / "business.sqlite")
    store.save_frontier({"items": [{"id": "old"}]})
    stale = store.load_frontier()
    store.save_frontier({"items": [{"id": "new"}]})
    with pytest.raises(StaleSnapshotError):
        store.save_frontier(stale)
    assert store.load_frontier()["items"][0]["id"] == "new"


def test_user_patch_keeps_new_scores_and_new_records(tmp_path):
    store = AppDataStore(tmp_path / "business.sqlite")
    before = {"items": [{"id": "a", "status": "new", "score": 50}]}
    store.save_frontier({"items": [{"id": "a", "status": "new", "score": 90}, {"id": "b"}]})
    after = {"items": [{"id": "a", "status": "read", "score": 50}]}
    store.patch_frontier(before, after)
    rows = {item["id"]: item for item in store.load_frontier()["items"]}
    assert rows["a"]["status"] == "read"
    assert rows["a"]["score"] == 90
    assert "b" in rows


def test_result_and_runtime_roll_back_together(tmp_path):
    store = AppDataStore(tmp_path / "business.sqlite")
    store.save_frontier({"items": [{"id": "old"}]})
    with patch.object(store, "save_runtime", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            store.commit_frontier({"items": [{"id": "new"}]}, {})
    assert store.load_frontier()["items"][0]["id"] == "old"


def test_failed_queue_flush_is_false_and_retry_can_recover():
    queue = DatabaseWriteQueue()
    failing = [True]
    writes = []
    def write():
        if failing[0]:
            raise OSError("disk full")
        writes.append("saved")
    queue.submit("edit", write)
    assert queue.flush(3) is False
    failing[0] = False
    queue.retry_failed()
    assert queue.flush(3) is True
    assert writes == ["saved"]


def test_locked_database_is_not_corruption(tmp_path):
    cache = EvidenceCache(tmp_path / "cache.sqlite")
    with patch.object(cache, "_migrate", side_effect=sqlite3.OperationalError("database is locked")), patch.object(cache, "_preserve_broken_database") as recovery:
        with pytest.raises(sqlite3.OperationalError):
            cache.initialize()
        recovery.assert_not_called()


def test_newer_runtime_does_not_replace_newer_journals(tmp_path):
    source = AppDataStore(tmp_path / "source.sqlite")
    target = AppDataStore(tmp_path / "target.sqlite")
    source.save_journals([{"id": "old"}])
    target.save_journals([{"id": "new"}])
    source.save_runtime({"batches": {}})
    with closing(sqlite3.connect(source.path)) as connection, connection:
        connection.execute("UPDATE app_datasets SET updated_at='2020-01-01'")
        connection.execute("UPDATE app_datasets SET updated_at='2030-01-01' WHERE name='runtime'")
    with closing(sqlite3.connect(target.path)) as connection, connection:
        connection.execute("UPDATE app_datasets SET updated_at='2026-01-01'")
    migrate_legacy_app_data(source.path, target.path)
    assert target.load_journals()[0]["id"] == "new"


def test_pause_keeps_checkpoint_and_manual_run_resumes():
    now = datetime(2026, 10, 2, 12)
    runtime, batch, _ = begin_batch({}, task="frontier", inputs={}, now=now)
    runtime = checkpoint(runtime, batch, "recall", payload={"items": ["a"]}, now=now)
    runtime = checkpoint(runtime, batch, "enrich", state="cancelled", now=now)
    runtime, resumed_batch, resumed = begin_batch(runtime, task="frontier", inputs={}, now=now, manual=True)
    assert resumed and resumed_batch == batch
    assert stage_payload(runtime, batch, "recall") == {"items": ["a"]}


def test_dictionary_hit_does_not_load_model():
    from utils import local_translation_backend as backend
    with patch.object(backend, "_runtime", side_effect=AssertionError("model loaded")):
        assert backend.translate_en_to_zh(["soil organic carbon", ""]) == ["土壤有机碳", ""]


def test_failed_backup_retry_is_complete_and_translation_secret_excluded(tmp_path, monkeypatch):
    from utils import file_manager as fm
    from utils.pdf_translation_store import TranslationStore
    data = tmp_path / "data"
    data.mkdir()
    (data / "todo.json").write_text("[]", encoding="utf-8")
    component = TranslationStore(data / "pdf-translator")
    component.save_settings({"api_key_secret": "secret-marker-must-not-be-in-backup", "model": "model"})
    monkeypatch.setattr(fm, "DATA_DIR", data)
    monkeypatch.setattr(fm, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(fm, "BACKUP_FILE_NAMES", ("todo.json",))
    monkeypatch.setattr(fm, "business_data_store", lambda: None)
    monkeypatch.setattr(fm, "load_app_settings", lambda: {})
    with patch.object(fm, "_copy_file_streaming", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            fm.create_backup("retry")
    backup = fm.create_backup("retry")
    assert (backup / "manifest.json").is_file()
    restored = TranslationStore(backup / "pdf-translator")
    assert restored.settings()["api_key_secret"] == ""
    assert b"secret-marker-must-not-be-in-backup" not in restored.path.read_bytes()
    result_dir = data / "pdf-translator" / "results" / "job"
    result_dir.mkdir(parents=True)
    (result_dir / "translation.pdf").write_bytes(b"%PDF-test")
    component.save_settings({"backup_outputs": True})
    with_outputs = fm.create_backup("with-outputs")
    assert (with_outputs / "pdf-translator" / "results" / "job" / "translation.pdf").read_bytes() == b"%PDF-test"


def test_user_activity_leaves_manual_worker_running():
    from ui.main_window import MainWindow
    class Worker:
        interrupted = False
        def isRunning(self): return True
        def requestInterruption(self): self.interrupted = True
    idle, manual = Worker(), Worker()
    owner = SimpleNamespace(_idle_tasks_running=False, _idle_task_queue=[], _backup_thread=idle,
        _storage_maintenance_thread=None, _idle_background_workers=set(),
        _loaded_pages={"frontier": SimpleNamespace(_worker=manual)})
    with patch("ui.main_window.append_runtime_log"):
        MainWindow._cancel_idle_background_work(owner)
    assert idle.interrupted
    assert not manual.interrupted


def test_thousand_achievements_keep_bounded_widgets_and_last_row_accessible():
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtTest import QTest
    from ui.achievements_page import AchievementsPage
    app = QApplication.instance() or QApplication([])
    records = [{"id": str(i), "title": f"Record {i}", "category": "论文"} for i in range(1000)]
    with patch("ui.achievements_page.load_achievements", return_value=records):
        page = AchievementsPage()
    page.resize(600, 700)
    page.show()
    QTest.qWait(60)
    assert len(page.findChildren(QWidget)) < 200
    page.scroll.scrollToBottom()
    QTest.qWait(60)
    assert 999 in page.scroll.cards
    assert len(page.findChildren(QWidget)) < 200
    page.close()
    page.deleteLater()


def test_translation_relocation_rewrites_internal_output_paths(tmp_path):
    from utils.pdf_translation_store import TranslationStore
    import json
    old = tmp_path / "old"
    new = tmp_path / "new"
    store = TranslationStore(new)
    job_id = store.enqueue({"input": str(tmp_path / "paper.pdf")})
    with store.connect() as connection:
        connection.execute("UPDATE jobs SET params=? WHERE id=?", (json.dumps({"input": str(tmp_path / "paper.pdf"), "output": str(old / "results" / job_id)}), job_id))
    store.rebase_paths(old)
    values = json.loads(store.get(job_id)["params"])
    assert values["output"] == str(new / "results" / job_id)
    assert values["input"] == str(tmp_path / "paper.pdf")


@pytest.mark.parametrize("kind", ["papers", "inspirations", "readings"])
def test_thousand_other_cards_keep_widgets_bounded(kind):
    from PySide6.QtWidgets import QApplication, QWidget
    from PySide6.QtTest import QTest
    app = QApplication.instance() or QApplication([])
    records = [{"id": str(i), "title": f"Paper {i}", "text": f"Idea {i}",
                "journals": [], "status": "未阅读"} for i in range(1000)]
    if kind == "papers":
        from ui.paper_page import PaperPage
        with patch("ui.paper_page.load_papers", return_value=records), patch("ui.paper_page.load_rejection_archive", return_value=[]):
            page = PaperPage()
        view = page.scroll
    elif kind == "inspirations":
        from ui.notes_page import InspirationPanel
        with patch("ui.notes_page.load_inspirations", return_value=records):
            page = InspirationPanel()
        view = page.list_box
    else:
        from ui.notes_page import NotesPage
        with patch("ui.notes_page.load_readings", return_value=records), patch("ui.notes_page.load_inspirations", return_value=[]):
            page = NotesPage()
        view = page.reading_box
    page.resize(600, 700)
    page.show()
    QTest.qWait(80)
    assert len(page.findChildren(QWidget)) < 250
    view.scrollToBottom()
    QTest.qWait(80)
    assert 999 in view.cards
    assert len(page.findChildren(QWidget)) < 250
    page.close()
    page.deleteLater()


def test_journal_shelf_fetches_sql_pages_and_preserves_filters(tmp_path):
    from PySide6.QtWidgets import QApplication
    from ui.journal_library_page import JournalLibraryPage
    app = QApplication.instance() or QApplication([])
    store = AppDataStore(tmp_path / "business.sqlite")
    store.save_journals([{"id": str(i), "name": f"Journal {i}", "publisher": "Publisher",
        "favorite": i % 2 == 0, "jcr": {"quartile": "Q1", "status": "verified"}} for i in range(500)])
    with patch("ui.journal_library_page.business_data_store", return_value=store):
        page = JournalLibraryPage()
        page._apply_loaded_library(store.journal_summaries(), [])
        assert page.journal_model._provider_offset == 40
        assert page.journal_model._provider_total == 500
        assert all(row.get("_summary") for row in page.journals)
        page.journal_model.fetchMore()
        assert page.journal_model._provider_offset == 80
        page.filter_combo.setCurrentText("已收藏")
        assert page.journal_model._provider_total == 250
        page.close()
        page.deleteLater()


def test_custom_ai_endpoint_omits_provider_specific_fields():
    import json
    from utils import ai_service
    sent = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'{"choices":[{"message":{"content":"{}"}}]}'
    def open_request(request, **kwargs):
        sent.append(json.loads(request.data))
        return Response()
    with patch.object(ai_service, "urlopen", side_effect=open_request):
        ai_service._chat_json({"base_url": "https://custom.example/v1", "model": "model"}, "fake-key", "system", {}, 100)
    assert "thinking" not in sent[0]
    assert "response_format" not in sent[0]


def test_concurrent_runtime_snapshots_keep_independent_batches(tmp_path):
    store = AppDataStore(tmp_path / "business.sqlite")
    store.save_runtime({"batches": {"a": {"id": "a", "state": "running", "stages": {}}}})
    store.save_runtime({"batches": {"b": {"id": "b", "state": "success", "stages": {}}}})
    assert set(store.load_runtime()["batches"]) == {"a", "b"}


def test_same_clock_stale_runtime_does_not_downgrade_success(tmp_path):
    store = AppDataStore(tmp_path / "business.sqlite")
    now = datetime(2026, 10, 2, 12)
    runtime, batch, _ = begin_batch({}, task="frontier", inputs={}, now=now)
    store.save_runtime(runtime)
    finalized = checkpoint(runtime, batch, "commit", state="success", now=now)
    store.save_runtime(finalized)
    store.save_runtime(runtime)
    assert store.load_runtime()["batches"][batch]["state"] == "success"


def test_cache_expiry_does_not_remove_durable_candidates(tmp_path):
    store = AppDataStore(tmp_path / "business.sqlite")
    cache = EvidenceCache(tmp_path / "cache.sqlite")
    cache.initialize()
    item = {"id": "candidate", "title": "SOC mapping", "candidate_state": "retained_unshown"}
    store.save_frontier_candidates([item])
    cache.upsert_works([item])
    with cache._session() as connection:
        connection.execute("UPDATE works SET updated_at='2020-01-01'")
    removed = cache.prune_rebuildable_records(now=datetime(2026, 10, 2))
    assert removed["works"] == 1
    with store._session() as connection:
        assert connection.execute("SELECT COUNT(*) FROM app_frontier_candidates").fetchone()[0] == 1
