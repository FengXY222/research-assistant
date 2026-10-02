"""Isolated formal-data navigation and large-list regression without network requests."""
from __future__ import annotations
import ctypes
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / ".test-results" / "repair-20261002"
OUTPUT.mkdir(parents=True, exist_ok=True)


def memory_mb():
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    ctypes.windll.kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]
    ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return round(counters.WorkingSetSize / 1024 / 1024, 2)


def main():
    with tempfile.TemporaryDirectory(prefix="research-assistant-repair-") as temporary:
        data = Path(temporary) / "data"
        data.mkdir()
        formal = Path(os.environ["LOCALAPPDATA"]) / "科研助手" / "UserData"
        source = sqlite3.connect((formal / "research_assistant.sqlite").as_uri() + "?mode=ro", uri=True)
        destination = sqlite3.connect(data / "research_assistant.sqlite")
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()
        for name in ("papers.json", "achievements.json", "todo.json", "notes.json", "readings.json", "research_profile.json"):
            if (formal / name).is_file():
                shutil.copy2(formal / name, data / name)
        os.environ["RESEARCH_ASSISTANT_DATA_DIR"] = str(data)
        os.environ["RESEARCH_ASSISTANT_DISABLE_BACKGROUND"] = "1"
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtCore import QTimer, QCoreApplication, QEvent
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QWidget
        from ui.main_window import MainWindow
        from ui.achievements_page import AchievementsPage
        app = QApplication([])
        from ui.theme import ensure_application_font
        ensure_application_font(app)
        gaps, last_tick = [], [time.perf_counter()]
        heartbeat = QTimer()
        heartbeat.setInterval(16)
        def tick():
            now = time.perf_counter()
            gaps.append((now - last_tick[0]) * 1000)
            last_tick[0] = now
        heartbeat.timeout.connect(tick)
        heartbeat.start()
        with patch.object(MainWindow, "_apply_global_journal_import_shortcut"), patch.object(MainWindow, "_apply_global_visibility_shortcut"):
            window = MainWindow()
        window.settings["close_to_tray"] = False
        window.resize(850, 720)
        window.show()
        pages = ["papers", "achievements", "notes", "frontier", "journals", "special_issues", "tools", "todo"]
        first = []
        for key in pages:
            start = time.perf_counter()
            window.navigate(key)
            QTest.qWait(70)
            first.append({"page": key, "navigation_ms": round((time.perf_counter()-start)*1000 - 70, 2)})
        # Import threads can outlive a fixed initial delay. Warm all loaded pages
        # before measuring steady-state growth, so first-load work is not counted as a leak.
        deadline = time.perf_counter() + 15
        while time.perf_counter() < deadline:
            for key in pages:
                window.navigate(key)
                QTest.qWait(25)
            if all(key in window._loaded_pages for key in pages) and not window._page_loaders:
                if all(getattr(window._loaded_pages[key], "_loaded", True) for key in pages):
                    break
        QTest.qWait(100)
        if not all(key in window._loaded_pages for key in pages):
            raise RuntimeError("Navigation warmup did not finish")
        initial_memory = memory_mb()
        initial_widgets = len(window.findChildren(QWidget))
        warm_times = []
        cpu_started = time.process_time()
        wall_started = time.perf_counter()
        gaps.clear()
        last_tick[0] = time.perf_counter()
        for _ in range(20):
            for key in ("achievements", "frontier", "journals", "special_issues"):
                start = time.perf_counter()
                window.navigate(key)
                app.processEvents()
                warm_times.append((time.perf_counter()-start)*1000)
            QTest.qWait(20)
        cpu_seconds = time.process_time() - cpu_started
        wall_seconds = time.perf_counter() - wall_started
        result = {"formal_first_navigation": first, "switches": len(warm_times),
            "switch_cpu_seconds": round(cpu_seconds, 3), "switch_wall_seconds": round(wall_seconds, 3),
            "warm_switch_max_ms": round(max(warm_times), 2),
            "heartbeat_max_gap_ms": round(max(gaps, default=0), 2),
            "rss_before_mb": initial_memory, "rss_after_mb": memory_mb(),
            "widgets_before": initial_widgets, "widgets_after": len(window.findChildren(QWidget))}
        idle_cpu = time.process_time()
        QTest.qWait(500)
        result["idle_cpu_percent_one_core"] = round((time.process_time() - idle_cpu) / 0.5 * 100, 2)
        from utils.backup_io import _sha256_file
        start_memory = memory_mb()
        maximum_memory = [start_memory]
        done = threading.Event()
        def hash_backup():
            try:
                _sha256_file(data / "research_assistant.sqlite")
            finally:
                done.set()
        worker = threading.Thread(target=hash_backup)
        worker.start()
        while not done.is_set():
            maximum_memory[0] = max(maximum_memory[0], memory_mb())
            QTest.qWait(5)
        worker.join()
        result["backup_hash"] = {"file_mb": round((data / "research_assistant.sqlite").stat().st_size / 1024 / 1024, 2),
            "sampled_peak_rss_delta_mb": round(maximum_memory[0] - start_memory, 2)}
        for key in ("achievements", "frontier", "journals", "special_issues", "papers", "notes"):
            window.navigate(key)
            QTest.qWait(40)
            window.grab().save(str(OUTPUT / (key + ".png")))
        records = [{"id": str(i), "title": f"Synthetic achievement {i}", "category": "论文"} for i in range(1000)]
        with patch("ui.achievements_page.load_achievements", return_value=records):
            start = time.perf_counter()
            page = AchievementsPage()
            build_ms = (time.perf_counter()-start)*1000
        page.resize(650, 700)
        page.show()
        QTest.qWait(40)
        start = time.perf_counter()
        page._render()
        render_ms = (time.perf_counter()-start)*1000
        page.scroll.scrollToBottom()
        QTest.qWait(60)
        result["1000_achievements"] = {"construct_ms": round(build_ms, 2),
            "render_ms": round(render_ms, 2), "widgets": len(page.findChildren(QWidget)),
            "last_record_visible": 999 in page.scroll.cards}
        page.grab().save(str(OUTPUT / "achievements-1000.png"))
        page.close()
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        window._global_hotkey.close()
        window._visibility_hotkey.close()
        window.close()
        app.processEvents()
        (OUTPUT / "regression.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
