from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest import TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from tests import _data_root  # noqa: F401
from PySide6.QtWidgets import QApplication
from utils.pdf_translation_store import TranslationStore
from utils.pdf_translation_controller import TranslationController
from utils.secure_store import protect_secret
from ui.workbench_shell import resolve_route


class TranslationComponentTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.input = self.root / "source.pdf"
        self.input.write_bytes(b"%PDF-1.4\n")

    def wait(self, condition, timeout=15):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.assertTrue(condition(), "Qt process queue did not reach expected state")

    def controller(self, mode="complete", timeout=200):
        controller = TranslationController(self.root / "queue", command=[sys.executable, "-u", str(Path(__file__).parent / "fixtures/pdf_translation_worker.py"), mode], cancel_timeout_ms=timeout)
        controller.store.save_settings({"base_url": "https://example.invalid", "model": "test", "api_key_secret": protect_secret("test-key"), "qps": 2, "workers": 2})
        self.addCleanup(controller.shutdown)
        return controller

    def test_queue_snapshots_never_include_credentials_and_recover(self):
        store = TranslationStore(self.root / "queue")
        job = store.enqueue({"input": str(self.input), "api_key": "private", "api_key_secret": "private-encrypted", "model": "test"})
        params = json.loads(store.get(job)["params"])
        self.assertNotIn("api_key", params)
        self.assertNotIn("api_key_secret", params)
        store.update(job, status="running")
        recovered = TranslationStore(self.root / "queue")
        self.assertEqual(recovered.get(job)["status"], "interrupted")
        self.assertEqual(recovered.get(job)["params"], store.get(job)["params"])

    def test_single_process_fifo_and_completed_outputs(self):
        controller = self.controller()
        first = controller.enqueue(self.input)
        process = controller.process
        second = controller.enqueue(self.input)
        self.assertIs(controller.process, process)
        self.assertEqual(controller.store.get(second)["status"], "queued")
        self.wait(lambda: all(controller.store.get(job)["status"] == "completed" for job in (first, second)))
        self.assertIsNone(controller.process)
        self.assertIsNone(controller.tree)
        self.assertEqual(self.input.read_bytes(), b"%PDF-1.4\n")

    def test_cooperative_cancel_and_retry_preserves_parameters(self):
        controller = self.controller("cancel", timeout=3000)
        job = controller.enqueue(self.input)
        self.wait(lambda: controller.tree is not None)
        original = controller.store.get(job)["params"]
        controller.cancel()
        self.wait(lambda: controller.process is None)
        self.assertEqual(controller.store.get(job)["status"], "cancelled")
        self.assertFalse(controller.running_queue)
        controller.command[-1] = "complete"
        controller.retry(job)
        self.wait(lambda: controller.store.get(job)["status"] == "completed")
        self.assertEqual(controller.store.get(job)["params"], original)

    def test_timeout_cleans_entire_owned_tree(self):
        controller = self.controller("hang", timeout=300)
        events = []
        controller.progress.connect(events.append)
        job = controller.enqueue(self.input)
        self.wait(lambda: any(event.get("type") == "child" for event in events))
        child = next(event["pid"] for event in events if event.get("type") == "child")
        controller.cancel()
        self.wait(lambda: controller.process is None)
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, child)
        if handle:
            exit_code = ctypes.c_ulong()
            kernel.GetExitCodeProcess.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong))
            kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
            kernel.CloseHandle(handle)
            self.assertNotEqual(exit_code.value, 259)
        self.assertEqual(controller.store.get(job)["status"], "cancelled")

    def test_tools_route_is_in_workbench_and_does_not_import_engine(self):
        self.assertEqual((resolve_route("tools").workbench, resolve_route("tools").anchor), ("work", "tools"))
        from ui.tools_page import ToolsPage
        page = ToolsPage()
        self.addCleanup(page.deleteLater)
        self.assertNotIn("pdf2zh_next", sys.modules)
        self.assertNotIn("babeldoc", sys.modules)
        self.assertFalse(hasattr(page, "_worker"))
        self.assertFalse(hasattr(page, "_ai_worker"))
