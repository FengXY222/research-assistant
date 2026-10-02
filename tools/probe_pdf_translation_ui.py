"""Isolated real Qt integration probe: navigation, cancel, cache retry, GUI timing."""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def memory_mb():
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [(key, ctypes.c_size_t) for key in
            ("peak", "working", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile")]
    kernel = ctypes.WinDLL("kernel32")
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD)
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return counters.working / 1024**2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--output", required=True)
    parser.add_argument("--visual-only", action="store_true")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads(subprocess.check_output([sys.executable, "-c", "import json; from utils.ai_service import get_ai_settings; print(json.dumps(get_ai_settings()))"], cwd=ROOT, text=True, encoding="utf-8"))
    os.environ["RESEARCH_ASSISTANT_DATA_DIR"] = str(output / "data")
    os.environ["RESEARCH_ASSISTANT_DISABLE_BACKGROUND"] = "1"
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from ui.main_window import MainWindow
    from ui.theme import apply_application_theme, ensure_application_font
    app = QApplication([])
    app.setStyle("Fusion")
    ensure_application_font(app)
    apply_application_theme(app, "fog_teal", "comfortable")
    window = MainWindow()
    window.resize(900, 660)
    window.show()
    last_tick = time.monotonic()
    max_gap = 0.0
    peak = memory_mb()
    ticks = 0

    def tick():
        nonlocal last_tick, max_gap, peak, ticks
        now = time.monotonic()
        max_gap = max(max_gap, now - last_tick)
        last_tick = now
        peak = max(peak, memory_mb())
        ticks += 1

    timer = QTimer()
    timer.setInterval(100)
    timer.timeout.connect(tick)
    timer.start()

    def wait(condition, timeout=180):
        deadline = time.monotonic() + timeout
        while not condition() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        if not condition():
            raise RuntimeError("Qt translation integration probe timed out")

    page = None
    try:
        window.navigate("tools")
        wait(lambda: "tools" in window._loaded_pages, 15)
        page = window._loaded_pages["tools"]
        if args.visual_only:
            for _ in range(50):
                app.processEvents()
                time.sleep(0.01)
            window.grab().save(str(output / "tools-page.png"))
            print(json.dumps({"page": "work/tools", "screenshot": str(output / "tools-page.png")}, ensure_ascii=False))
            return 0
        controller = page.translation
        controller.store.save_settings({key: config[key] for key in ("base_url", "model", "api_key_secret")}
                                       | {"qps": 2, "workers": 2, "lang_in": "en", "lang_out": "zh-CN"})
        job = controller.enqueue(args.input, pages="1")
        wait(lambda: controller.tree is not None, 10)
        process = controller.process
        snapshot = controller.store.get(job)["params"]
        for _ in range(10):
            window.navigate("home")
            window._cancel_idle_background_work()
            window.navigate("tools")
            app.processEvents()
        survived_navigation = controller.process is process and controller.store.get(job)["status"] == "running"
        if not survived_navigation:
            raise RuntimeError("Manual translation was cancelled by navigation")
        controller.cancel(job)
        wait(lambda: controller.process is None, 15)
        cancelled = controller.store.get(job)["status"] == "cancelled"
        controller.retry(job)
        wait(lambda: controller.process is None, 180)
        row = controller.store.get(job)
        if row["status"] != "completed":
            raise RuntimeError(row["error"])
        for _ in range(10):
            app.processEvents()
            time.sleep(0.02)
        window.grab().save(str(output / "tools-page.png"))
        report = {"manual_task_survived_20_navigations": survived_navigation, "cancelled": cancelled,
                  "retry_completed": row["status"] == "completed", "parameters_preserved": snapshot == row["params"],
                  "main_process_engine_imported": any(key in sys.modules for key in ("pdf2zh_next", "babeldoc", "onnxruntime")),
                  "main_process_peak_mb": round(peak, 1), "max_gui_timer_gap_ms": round(max_gap * 1000, 1),
                  "gui_timer_ticks": ticks, "result": json.loads(row["result"]), "process_released": controller.process is None and controller.tree is None}
        (output / "ui-probe-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 0
    finally:
        timer.stop()
        if page:
            page.translation.shutdown()
        if window.tray_icon:
            window.tray_icon.hide()
        window._global_hotkey.close()
        window._visibility_hotkey.close()
        window.hide()
        app.quit()


if __name__ == "__main__":
    raise SystemExit(main())
