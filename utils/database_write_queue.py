"""Single-writer queue for non-blocking GUI persistence.

Tasks sharing a key are coalesced: while the writer is busy, only the newest
snapshot is retained.  Service workers may still call repositories
synchronously because they already run outside the GUI thread.
"""

from __future__ import annotations

import atexit
import queue
import threading
from collections.abc import Callable
from typing import Any


class DatabaseWriteQueue:
    def __init__(self) -> None:
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._pending: dict[str, tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]] = {}
        self._scheduled: set[str] = set()
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._errors: list[str] = []
        self._failed: dict[str, tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]] = {}

    def submit(self, key: str, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        normalized = str(key).strip() or "default"
        with self._lock:
            self._pending[normalized] = (callback, args, kwargs)
            if normalized not in self._scheduled:
                self._scheduled.add(normalized)
                self._queue.put(normalized)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="research-assistant-db-writer", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while True:
            key = self._queue.get()
            try:
                if key is None:
                    return
                with self._lock:
                    task = self._pending.pop(key, None)
                    self._scheduled.discard(key)
                if task is None:
                    continue
                callback, args, kwargs = task
                try:
                    callback(*args, **kwargs)
                except Exception as error:  # noqa: BLE001 - retain diagnostics without killing the writer
                    with self._lock:
                        self._errors.append(f"{key}: {error}")
                        self._errors = self._errors[-20:]
                        self._failed[key] = task
                else:
                    with self._lock:
                        self._failed.pop(key, None)
            finally:
                self._queue.task_done()

    def flush(self, timeout: float | None = None) -> bool:
        """Wait for queued commits; a timeout keeps application shutdown bounded."""

        done = threading.Event()

        def waiter() -> None:
            self._queue.join()
            done.set()

        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()
        if not done.wait(timeout):
            return False
        with self._lock:
            return not self._failed

    def retry_failed(self) -> None:
        with self._lock:
            tasks = list(self._failed.items())
        for key, (callback, args, kwargs) in tasks:
            self.submit(key, callback, *args, **kwargs)

    def pending_count(self) -> int:
        return self._queue.unfinished_tasks

    def failures(self) -> list[str]:
        with self._lock:
            return list(self._failed)

    def errors(self) -> list[str]:
        with self._lock:
            return list(self._errors)


WRITE_QUEUE = DatabaseWriteQueue()
atexit.register(lambda: WRITE_QUEUE.flush(5.0))


def submit_database_write(key: str, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    WRITE_QUEUE.submit(key, callback, *args, **kwargs)


def flush_database_writes(timeout: float | None = None) -> bool:
    return WRITE_QUEUE.flush(timeout)
