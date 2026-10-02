"""Bounded-memory backup primitives shared by application components."""
from pathlib import Path
from typing import Any
import hashlib
import sqlite3
import shutil


def _sqlite_online_backup(source: Path, destination: Path, cancelled: Any = None) -> None:
    """Copy one SQLite database without losing committed WAL content."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(source, timeout=30.0)
    destination_connection = sqlite3.connect(destination, timeout=30.0)
    try:
        source_connection.execute("PRAGMA busy_timeout=30000")
        destination_connection.execute("PRAGMA busy_timeout=30000")
        def progress(_status: int, _remaining: int, _total: int) -> None:
            if cancelled and cancelled():
                raise InterruptedError("备份已因用户恢复操作而暂停")

        source_connection.backup(destination_connection, pages=128, progress=progress)
    finally:
        destination_connection.close()
        source_connection.close()


def _sha256_file(
    path: Path,
    chunk_size: int = 1024 * 1024,
    cancelled: Any = None,
) -> str:
    """Hash a file with bounded memory, including multi-gigabyte databases."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(max(64 * 1024, int(chunk_size))):
            if cancelled and cancelled():
                raise InterruptedError("备份校验已因用户恢复操作而暂停")
            digest.update(chunk)
    return digest.hexdigest()


def _copy_file_streaming(source: Path, destination: Path, cancelled: Any = None) -> None:
    with source.open("rb") as reader, destination.open("wb") as writer:
        while chunk := reader.read(1024 * 1024):
            if cancelled and cancelled():
                raise InterruptedError("备份已因用户恢复操作而暂停")
            writer.write(chunk)
    shutil.copystat(source, destination)
