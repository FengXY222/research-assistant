"""Real engine probe using existing protected app credentials, never prints credentials.

Run with the independent component Python (psutil/PyMuPDF live there).
"""
from __future__ import annotations
import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from utils.pdf_translation_process import ProcessTree


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    import psutil
    import pymupdf
    parser = argparse.ArgumentParser()
    parser.add_argument("input")
    parser.add_argument("--output", required=True)
    parser.add_argument("--pages", default="")
    parser.add_argument("--cancel-after", type=float, default=0)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--worker", default=str(ROOT / "components/pdf_translator/worker.py"))
    args = parser.parse_args()
    source = Path(args.input).resolve()
    before = sha256(source)
    credential_code = "import json; from utils.ai_service import get_ai_settings; from utils.secure_store import reveal_secret; c=get_ai_settings(); print(json.dumps({'base_url':c['base_url'],'model':c['model'],'api_key':reveal_secret(c['api_key_secret'])}))"
    config = json.loads(subprocess.check_output([str(ROOT / ".venv/Scripts/python.exe"), "-c", credential_code], cwd=ROOT, text=True, encoding="utf-8"))
    config.update(input=str(source), output=str(Path(args.output).resolve()), pages=args.pages, qps=2, workers=2)
    checkpoint = Path(args.output)
    checkpoint.mkdir(parents=True, exist_ok=True)
    (checkpoint / "task-parameters.json").write_text(json.dumps({key: value for key, value in config.items() if key != "api_key"}, ensure_ascii=False, indent=2), encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    # This machine's old local proxy is unavailable. Direct connection, process-local only.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env.pop(key, None)
    env["NO_PROXY"] = "*"
    for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        env[key] = "2"
    worker = subprocess.Popen([sys.executable, "-u", str(Path(args.worker).resolve())],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              encoding="utf-8", env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    tree = ProcessTree(worker.pid)
    worker.stdin.write(json.dumps(config, ensure_ascii=False) + "\n")
    worker.stdin.flush()
    logs = deque(maxlen=15)
    events = []
    start = time.monotonic()
    peak = 0
    observed = set()
    stop = threading.Event()

    def stderr():
        for line in worker.stderr:
            logs.append(line.replace(config["api_key"], "[redacted]").strip())

    def monitor():
        nonlocal peak
        sent = None
        last_status = 0
        while not stop.wait(0.5):
            try:
                processes = [psutil.Process(worker.pid)]
                processes += processes[0].children(recursive=True)
                observed.update((process.pid, process.create_time()) for process in processes)
                peak = max(peak, sum(process.memory_info().rss for process in processes if process.is_running()))
            except psutil.Error:
                pass
            elapsed = time.monotonic() - start
            if elapsed - last_status >= 30:
                last_status = elapsed
                print(json.dumps({"type": "probe_status", "elapsed_seconds": round(elapsed), "peak_memory_mb": round(peak / 1024**2, 1), "last_log": logs[-1] if logs else ""}, ensure_ascii=False), flush=True)
            if sent is None and ((args.cancel_after and elapsed >= args.cancel_after) or elapsed >= args.timeout or (checkpoint / "cancel.request").exists()):
                sent = time.monotonic()
                try:
                    worker.stdin.write('{"command":"cancel"}\n')
                    worker.stdin.flush()
                    print("cancel_requested", flush=True)
                except (OSError, ValueError):
                    pass
            if sent is not None and time.monotonic() - sent >= 6:
                tree.close()
                return

    threading.Thread(target=stderr, daemon=True).start()
    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    try:
        for line in worker.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            events.append(event)
            # Keep progress concise; complete event history stays in report.
            if event.get("type") in ("finish", "error", "cancelled", "progress_start", "progress_end"):
                print(json.dumps(event, ensure_ascii=False), flush=True)
        code = worker.wait(timeout=10)
    finally:
        stop.set()
        monitor_thread.join(timeout=2)
        tree.close()
    time.sleep(0.5)
    residual = []
    for pid, created in observed:
        try:
            process = psutil.Process(pid)
            if process.create_time() == created and process.is_running():
                residual.append(pid)
        except psutil.Error:
            pass
    outputs = []
    for event in events:
        if event.get("type") == "finish":
            for path in dict.fromkeys(event.get("paths", {}).values()):
                with pymupdf.open(path) as document:
                    text = "".join(page.get_text() for page in document)
                    outputs.append({"path": path, "pages": len(document), "cjk_characters": sum('\u4e00' <= char <= '\u9fff' for char in text), "sha256": sha256(path)})
    report = {"exit_code": code, "elapsed_seconds": round(time.monotonic() - start, 2), "peak_tree_memory_mb": round(peak / 1024**2, 1),
              "source_unchanged": before == sha256(source), "source_sha256": before,
              "residual_processes": residual, "outputs": outputs,
              "parameters": {key: value for key, value in config.items() if key != "api_key"}, "events": events,
              "last_logs": list(logs)}
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    # Probe report is a small, reproducible test artifact, not application business storage.
    (output / "probe-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key not in ("parameters", "events", "last_logs")}, ensure_ascii=False), flush=True)
    if code:
        print("\n".join(logs), flush=True)
    return 0 if code == 0 and outputs and not residual and report["source_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
