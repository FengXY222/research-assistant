"""Manual translation queue, deliberately outside global API/idle-task managers."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys
import time
import weakref

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal
from PySide6.QtWidgets import QApplication

from utils.pdf_translation_process import ProcessTree
from utils.pdf_translation_store import TranslationStore
from utils.secure_store import reveal_secret

CONTROLLERS = weakref.WeakSet()


def ensure_translation_idle() -> None:
    if any(controller.active_id for controller in CONTROLLERS):
        raise ValueError("请先停止正在翻译的论文，再迁移或恢复数据目录。")


def component_paths():
    root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    component = root / "components" / "pdf_translator"
    worker = component / "worker.py"
    if getattr(sys, "frozen", False):
        worker = Path(sys._MEIPASS) / "components" / "pdf_translator" / "worker.py"
    candidates = [component / "runtime" / "python.exe", component / "runtime" / "Scripts" / "python.exe"]
    local_data = os.environ.get("LOCALAPPDATA")
    if local_data:
        candidates.append(Path(local_data) / "科研助手" / "Components" / "pdf_translator" / "runtime" / "Scripts" / "python.exe")
    python = next((path for path in candidates if path.is_file()), candidates[0])
    return python, worker


class TranslationController(QObject):
    changed = Signal()
    progress = Signal(dict)
    notice = Signal(str)

    def __init__(self, root, parent=None, *, command=None, cancel_timeout_ms=6000):
        super().__init__(parent)
        self.store = TranslationStore(root)
        CONTROLLERS.add(self)
        self.command = command
        self.cancel_timeout_ms = cancel_timeout_ms
        self.process = None
        self.tree = None
        self.active_id = None
        self.running_queue = False
        self.closing = False
        self._buffer = b""
        self._result = {}
        self._error = ""
        self._secret = ""
        self._cancelled = False
        self._last_progress = 0.0
        app = QApplication.instance()
        if app:
            app.aboutToQuit.connect(self.shutdown)

    def relocate(self, root) -> None:
        if self.active_id:
            raise ValueError("翻译运行期间不能切换数据目录")
        previous = self.store.root
        self.store = TranslationStore(root)
        self.store.rebase_paths(previous)
        self.changed.emit()

    def enqueue(self, path, *, pages=""):
        path = Path(path).resolve()
        if not path.is_file() or path.suffix.lower() != ".pdf":
            raise ValueError("请选择存在的 PDF 文件。")
        values = self.store.settings()
        if not values.get("base_url") or not values.get("model") or not values.get("api_key_secret"):
            raise ValueError("请先填写翻译组件自己的 API 地址、模型和密钥。")
        reveal_secret(values["api_key_secret"])
        job_id = self.store.enqueue({**values, "input": str(path), "pages": pages})
        self.changed.emit()
        self.start()
        return job_id

    def retry(self, job_id):
        job = self.store.get(job_id)
        if job and job["status"] in ("cancelled", "failed", "interrupted"):
            # Retain original input, language, model and output folder; engine cache is global.
            self.store.update(job_id, status="queued", progress=0, error="", result={})
            self.changed.emit()
            self.start()

    def start(self):
        if self.closing:
            return
        self.running_queue = True
        if self.process is None:
            self._next()

    def _next(self):
        if self.closing or not self.running_queue or self.process is not None:
            return
        job = self.store.next()
        if not job:
            self.running_queue = False
            return
        params = json.loads(job["params"])
        self.active_id = job["id"]
        try:
            self._secret = reveal_secret(self.store.settings().get("api_key_secret", ""))
            if not self._secret:
                raise ValueError("翻译密钥为空，请重新填写。")
            if self.command is None:
                python, worker = component_paths()
                if not python.is_file() or not worker.is_file():
                    raise ValueError("独立翻译组件尚未安装，请安装组件运行环境。")
                command = [str(python), "-u", str(worker)]
            else:
                command = self.command
        except Exception as error:
            self.store.update(self.active_id, status="failed", error=str(error))
            self.active_id = None
            self.running_queue = False
            self.changed.emit()
            self.notice.emit(str(error))
            return
        self._buffer, self._result, self._error = b"", {}, ""
        self._cancelled = False
        self._last_progress = 0
        process = QProcess(self)
        self.process = process
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        env.insert("PYTHONUNBUFFERED", "1")
        if not params.get("use_system_proxy", False):
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
                env.remove(name)
            env.insert("NO_PROXY", "*")
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            env.insert(name, "2")
        # BabelDOC maintains its own user-local cache, retained across worker exits.
        process.setProcessEnvironment(env)
        process.readyReadStandardOutput.connect(self._read)
        process.readyReadStandardError.connect(self._drain_errors)
        process.started.connect(lambda: self._started(params))
        process.finished.connect(self._finished)
        process.errorOccurred.connect(self._process_error)
        self.store.update(self.active_id, status="running")
        self.changed.emit()
        self.progress.emit({"type": "progress_start", "stage": "正在启动独立翻译进程", "overall_progress": 0})
        process.start(command[0], command[1:])

    def _started(self, params):
        try:
            # Worker blocks on its first input line; assign ownership before it can spawn.
            self.tree = ProcessTree(self.process.processId())
            payload = {**params, "api_key": self._secret}
            self.process.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            if self._cancelled:
                self._send_cancel()
        except Exception as error:
            self._error = "无法隔离翻译进程：" + str(error)
            self.process.kill()

    def _read(self):
        if self.process is None:
            return
        self._buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            kind = event.get("type")
            if kind == "finish":
                self._result = event.get("paths", {})
            elif kind == "error":
                self._error = str(event.get("error", "翻译失败")).replace(self._secret, "[redacted]")[:2000]
            elif time.monotonic() - self._last_progress >= 0.5:
                self._last_progress = time.monotonic()
                value = min(100.0, max(0.0, float(event.get("overall_progress", 0) or 0)))
                self.store.update(self.active_id, progress=value)
                self.progress.emit(event)
                self.changed.emit()

    def _drain_errors(self):
        # Drain, don't retain unbounded logs or API/provider debug output in app memory.
        if self.process is not None:
            self.process.readAllStandardError()

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._error = "独立翻译进程启动失败。"
            self._finished(1, QProcess.ExitStatus.CrashExit)

    def _finished(self, code, exit_status):
        if self.process is None:
            return
        self._read()
        self._drain_errors()
        process, self.process = self.process, None
        if self.tree:
            self.tree.close()
            self.tree = None
        paths = {key: value for key, value in self._result.items() if Path(value).is_file() and Path(value).suffix.lower() == ".pdf"}
        status = "interrupted" if self.closing else ("cancelled" if self._cancelled else ("completed" if code == 0 and paths else "failed"))
        self.store.update(self.active_id, status=status, result=paths, progress=100 if status == "completed" else 0,
                          error="" if status != "failed" else self._error or f"翻译进程退出（{code}），未生成可用 PDF。")
        self.active_id, self._secret = None, ""
        process.deleteLater()
        self.changed.emit()
        if status == "completed":
            self.progress.emit({"type": "finish", "stage": "翻译完成", "overall_progress": 100})
        elif status in ("cancelled", "interrupted"):
            self.progress.emit({"type": status, "stage": "任务已停止，参数和缓存已保留", "overall_progress": 0})
        else:
            self.progress.emit({"type": "error", "stage": self._error or "翻译失败，请选择任务查看原因", "overall_progress": 0})
        QTimer.singleShot(0, self._next)

    def cancel(self, job_id=None):
        if job_id and job_id != self.active_id:
            job = self.store.get(job_id)
            if job and job["status"] == "queued":
                self.store.update(job_id, status="cancelled")
                self.changed.emit()
            return
        if self.process is None:
            return
        self._cancelled = True
        self.running_queue = False  # Explicit resume avoids surprise API charges after cancel.
        self.store.update(self.active_id, status="cancelling")
        self._send_cancel()
        process = self.process
        QTimer.singleShot(self.cancel_timeout_ms, lambda: self._force_stop(process))
        self.changed.emit()

    def _send_cancel(self):
        if self.process and self.process.state() == QProcess.ProcessState.Running:
            self.process.write(b'{"command":"cancel"}\n')

    def _force_stop(self, process):
        if self.process is process:
            if self.tree:
                self.tree.close()
                self.tree = None
            process.kill()

    def shutdown(self):
        self.closing = True
        self.running_queue = False
        if self.process:
            self._send_cancel()
            self.store.update(self.active_id, status="interrupted")
            self._force_stop(self.process)
