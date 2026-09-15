"""Serialized JSON writes with a durable rollback journal.

Individual replacements are atomic; a group becomes consistent by recovery.
Every participating reader/writer must hold the same data-root lock.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4


class JsonTransactionError(RuntimeError):
    """A write failed; recovery_required identifies retained recovery artifacts."""

    def __init__(self, message: str, *, recovery_required: bool = False):
        super().__init__(message)
        self.recovery_required = recovery_required


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_LOCAL = threading.local()


@contextmanager
def json_write_lock(directory: Path, *, timeout: float = 30.0):
    """Lock the whole read-modify-write operation, reentrantly in one thread."""
    root = Path(directory).resolve()
    key = str(root).casefold()
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.RLock())
    with lock:
        depths = getattr(_LOCAL, "depths", None)
        if depths is None:
            depths = _LOCAL.depths = {}
        if depths.get(key, 0):
            depths[key] += 1
            try:
                yield
            finally:
                depths[key] -= 1
            return
        root.mkdir(parents=True, exist_ok=True)
        handle = (root / ".json-write.lock").open("a+b")
        acquired = False
        try:
            handle.seek(0, os.SEEK_END)
            if not handle.tell():
                handle.write(b"\0")
                handle.flush()
            deadline = time.monotonic() + timeout
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as error:
                    if time.monotonic() >= deadline:
                        raise JsonTransactionError("数据写入锁等待超时") from error
                    time.sleep(0.025)
            depths[key] = 1
            yield
        finally:
            depths.pop(key, None)
            if acquired:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()


def _hash(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _durable_write(path: Path, payload: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _write_manifest(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    _durable_write(temporary, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    os.replace(temporary, path)


def _entries(manifest: Path, payload: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    if payload.get("version") != 1 or payload.get("id") != manifest.parent.name:
        raise ValueError("无效的事务清单")
    rows = payload.get("entries")
    if not isinstance(rows, list) or not rows:
        raise ValueError("事务清单没有目标")
    seen: set[str] = set()
    for index, row in enumerate(rows):
        target = Path(row["target"]).resolve()
        if target.parent != root or str(target).casefold() in seen:
            raise ValueError("事务目标超出数据目录或重复")
        if row.get("backup") != f"{index}.bak" or row.get("temporary") != f"{index}.tmp":
            raise ValueError("无效的事务备份路径")
        seen.add(str(target).casefold())
    return rows


def _clean(manifest: Path, rows: list[dict[str, Any]]) -> None:
    # The manifest is deleted last so cleanup itself can be retried.
    for row in rows:
        for name in (row["backup"], row["temporary"], row["backup"] + ".restore"):
            (manifest.parent / name).unlink(missing_ok=True)
    manifest.with_suffix(".tmp").unlink(missing_ok=True)
    manifest.unlink(missing_ok=True)
    manifest.parent.rmdir()


def _recover_one(manifest: Path, root: Path) -> str:
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        rows = _entries(manifest, payload, root)
        state = payload.get("state")
        if state not in {"prepared", "committed", "rolled_back"}:
            raise ValueError("未知的事务阶段")
        if state == "prepared":
            # Validate every target before undoing any, preserving later edits.
            for row in rows:
                current = _hash(Path(row["target"]))
                if current not in {row["before_hash"], row["after_hash"]}:
                    raise ValueError(f"目标已被其他操作修改: {row['target']}")
                if current != row["before_hash"] and row["existed"]:
                    if _hash(manifest.parent / row["backup"]) != row["before_hash"]:
                        raise ValueError(f"备份缺失或校验失败: {row['target']}")
            for row in reversed(rows):
                target = Path(row["target"])
                if _hash(target) == row["before_hash"]:
                    continue
                if row["existed"]:
                    backup = manifest.parent / row["backup"]
                    restore = backup.with_name(backup.name + ".restore")
                    _durable_write(restore, backup.read_bytes())
                    os.replace(restore, target)
                else:
                    target.unlink(missing_ok=True)
                if _hash(target) != row["before_hash"]:
                    raise ValueError(f"恢复后校验失败: {target}")
            payload["state"] = "rolled_back"
            _write_manifest(manifest, payload)
        _clean(manifest, rows)
        return str(payload["id"])
    except Exception as error:
        raise JsonTransactionError(
            f"事务恢复未完成，已保留清单与备份: {manifest}: {error}", recovery_required=True
        ) from error


def recover_json_transactions(directory: Path) -> list[str]:
    """Recover incomplete groups before participating readers open data files."""
    root = Path(directory).resolve()
    with json_write_lock(root):
        journal = root / ".json-transactions"
        if not journal.is_dir():
            return []
        return [_recover_one(path, root) for path in sorted(journal.glob("*/manifest.json"))]


def apply_json_transaction(changes: dict[Path, Any], *, action_key: str | None = None) -> None:
    """Prepare durable originals, journal the group, then replace targets.

    Callers enforce idempotency under json_write_lock and store their action
    key/results in the same changes. The journal keeps the key for diagnostics.
    """
    if not isinstance(changes, dict) or not changes:
        raise ValueError("JSON 事务至少需要一个目标文件")
    paths = [Path(path).resolve() for path in changes]
    root = paths[0].parent
    if any(path.parent != root for path in paths):
        raise ValueError("JSON 事务目标必须位于同一数据目录")
    if len({str(path).casefold() for path in paths}) != len(paths):
        raise ValueError("JSON 事务包含重复目标")
    with json_write_lock(root):
        recover_json_transactions(root)
        transaction_id = uuid4().hex
        directory = root / ".json-transactions" / transaction_id
        directory.mkdir(parents=True)
        manifest = directory / "manifest.json"
        rows: list[dict[str, Any]] = []
        payload: dict[str, Any] = {"version": 1, "id": transaction_id, "state": "prepared", "action_key": action_key, "entries": rows}
        try:
            for index, (path, value) in enumerate(zip(paths, changes.values())):
                content = (json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n").encode("utf-8")
                json.loads(content)
                temporary, backup = directory / f"{index}.tmp", directory / f"{index}.bak"
                _durable_write(temporary, content)
                existed = path.is_file()
                before = path.read_bytes() if existed else None
                if before is not None:
                    _durable_write(backup, before)
                rows.append({"target": str(path), "temporary": temporary.name, "backup": backup.name, "existed": existed, "before_hash": hashlib.sha256(before).hexdigest() if before is not None else None, "after_hash": hashlib.sha256(content).hexdigest()})
            _write_manifest(manifest, payload)
            for row in rows:
                target = Path(row["target"])
                os.replace(directory / row["temporary"], target)
                if _hash(target) != row["after_hash"]:
                    raise ValueError(f"事务写入校验失败: {target}")
            payload["state"] = "committed"
            _write_manifest(manifest, payload)
        except Exception as error:
            if manifest.exists():
                try:
                    _recover_one(manifest, root)
                except JsonTransactionError as recovery_error:
                    raise JsonTransactionError(
                        f"跨文件操作失败且恢复未完成，保留备份: {error}; {recovery_error}", recovery_required=True
                    ) from error
            else:
                # No target replacements can occur before the durable manifest.
                for artifact in directory.iterdir():
                    artifact.unlink()
                directory.rmdir()
            raise JsonTransactionError(f"跨文件操作失败，原数据已恢复: {error}") from error
        try:
            _clean(manifest, rows)
        except OSError:
            pass  # Startup can finish cleanup from the committed manifest.
