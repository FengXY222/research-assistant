"""Rebuildable evidence cache shared by v12 research-intelligence features."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _normalise_doi(value: object) -> str:
    doi = str(value or "").strip().casefold()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if doi.startswith(prefix):
            doi = doi[len(prefix) :]
            break
    return doi.strip()


def _title_key(value: object) -> str:
    title = re.sub(r"[^\w]+", " ", str(value or "").casefold(), flags=re.UNICODE)
    return " ".join(title.split())


def work_key(work: dict[str, Any]) -> str:
    doi = _normalise_doi(work.get("doi"))
    if doi:
        return f"doi:{doi}"
    source = str(work.get("source", "")).strip().casefold()
    source_id = str(work.get("source_id", "")).strip().casefold()
    if source and source_id:
        return f"source:{source}:{source_id}"
    title = _title_key(work.get("title"))
    if title:
        return "title:" + hashlib.sha1(title.encode("utf-8")).hexdigest()
    return "work:" + hashlib.sha1(_json_dumps(work).encode("utf-8")).hexdigest()


class EvidenceCache:
    """A disposable SQLite cache; personal JSON is deliberately out of scope."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=12.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=12000")
        return connection

    @contextmanager
    def _session(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._migrate()
            if self.integrity_check() != "ok":
                raise sqlite3.DatabaseError("integrity check failed")
        except (sqlite3.DatabaseError, sqlite3.OperationalError):
            self._preserve_broken_database()
            self._migrate()

    def _preserve_broken_database(self) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        if self.path.exists():
            os.replace(self.path, self.path.with_name(f"{self.path.name}.broken-{stamp}"))
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(self.path) + suffix)
            if sidecar.exists():
                sidecar.unlink()

    def _migrate(self) -> None:
        statements = (
            "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)",
            """CREATE TABLE IF NOT EXISTS source_responses (
                source TEXT NOT NULL,
                query_key TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                PRIMARY KEY (source, query_key)
            )""",
            """CREATE TABLE IF NOT EXISTS works (
                work_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS journal_evidence (
                journal_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS ocr_pages (
                document_key TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (document_key, page_number)
            )""",
            """CREATE TABLE IF NOT EXISTS special_issue_discovery (
                issue_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS special_issue_verification (
                issue_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS dedupe_keys (
                namespace TEXT NOT NULL,
                item_key TEXT NOT NULL,
                canonical_key TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (namespace, item_key)
            )""",
            """CREATE TABLE IF NOT EXISTS job_checkpoints (
                job_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )""",
        )
        connection = sqlite3.connect(self.path, timeout=12.0)
        try:
            connection.execute("PRAGMA busy_timeout=12000")
            connection.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    connection.execute(statement)
                row = connection.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
                if row is None:
                    connection.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
                elif int(row[0]) < SCHEMA_VERSION:
                    connection.execute("UPDATE schema_version SET version=?", (SCHEMA_VERSION,))
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()
        finally:
            connection.close()

    def put_source_response(
        self,
        source: str,
        query_key: str,
        payload: dict[str, Any],
        fetched_at: str,
        expires_at: str,
    ) -> None:
        with self._session() as connection:
            connection.execute(
                """INSERT INTO source_responses(source, query_key, payload_json, fetched_at, expires_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(source, query_key) DO UPDATE SET
                     payload_json=excluded.payload_json,
                     fetched_at=excluded.fetched_at,
                     expires_at=excluded.expires_at""",
                (source.strip().casefold(), query_key, _json_dumps(payload), fetched_at, expires_at),
            )

    def get_source_response(self, source: str, query_key: str, *, now: str) -> dict[str, Any] | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT payload_json, expires_at FROM source_responses WHERE source=? AND query_key=?",
                (source.strip().casefold(), query_key),
            ).fetchone()
        if row is None or str(row[1]) <= str(now):
            return None
        payload = json.loads(row[0])
        return payload if isinstance(payload, dict) else None

    def upsert_work(self, work: dict[str, Any]) -> str:
        key = work_key(work)
        now = datetime.now().isoformat(timespec="seconds")
        with self._session() as connection:
            connection.execute(
                """INSERT INTO works(work_key, payload_json, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(work_key) DO UPDATE SET
                     payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                (key, _json_dumps(work), now),
            )
        return key

    def put_journal_evidence(self, journal_key: str, evidence: dict[str, Any]) -> None:
        with self._session() as connection:
            connection.execute(
                """INSERT INTO journal_evidence(journal_key, payload_json, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(journal_key) DO UPDATE SET
                     payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                (journal_key, _json_dumps(evidence), datetime.now().isoformat(timespec="seconds")),
            )

    def get_journal_evidence(self, journal_key: str) -> dict[str, Any] | None:
        with self._session() as connection:
            row = connection.execute(
                "SELECT payload_json FROM journal_evidence WHERE journal_key=?",
                (journal_key,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[0])
        return payload if isinstance(payload, dict) else None

    def _put_payload(self, table: str, key_column: str, key: str, payload: dict[str, Any]) -> None:
        with self._session() as connection:
            connection.execute(
                f"""INSERT INTO {table}({key_column}, payload_json, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT({key_column}) DO UPDATE SET
                      payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                (key, _json_dumps(payload), datetime.now().isoformat(timespec="seconds")),
            )

    def _put_payloads(
        self,
        table: str,
        key_column: str,
        values: list[tuple[str, dict[str, Any]]],
    ) -> None:
        """Upsert one logical batch in a single SQLite transaction."""

        if not values:
            return
        collapsed: dict[str, dict[str, Any]] = {}
        for key, payload in values:
            normalized_key = str(key)
            if normalized_key and isinstance(payload, dict):
                collapsed[normalized_key] = payload
        updated_at = datetime.now().isoformat(timespec="seconds")
        rows = [
            (str(key), _json_dumps(payload), updated_at)
            for key, payload in collapsed.items()
        ]
        if not rows:
            return
        with self._session() as connection:
            connection.executemany(
                f"""INSERT INTO {table}({key_column}, payload_json, updated_at) VALUES (?, ?, ?)
                    ON CONFLICT({key_column}) DO UPDATE SET
                      payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                rows,
            )

    def _get_payload(self, table: str, key_column: str, key: str) -> dict[str, Any] | None:
        with self._session() as connection:
            row = connection.execute(
                f"SELECT payload_json FROM {table} WHERE {key_column}=?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row[0])
        return payload if isinstance(payload, dict) else None

    def put_special_issue_discovery(self, issue_key: str, payload: dict[str, Any]) -> None:
        self._put_payload("special_issue_discovery", "issue_key", issue_key, payload)

    def put_special_issue_discoveries(self, values: list[tuple[str, dict[str, Any]]]) -> None:
        self._put_payloads("special_issue_discovery", "issue_key", values)

    def get_special_issue_discovery(self, issue_key: str) -> dict[str, Any] | None:
        return self._get_payload("special_issue_discovery", "issue_key", issue_key)

    def put_special_issue_verification(self, issue_key: str, payload: dict[str, Any]) -> None:
        self._put_payload("special_issue_verification", "issue_key", issue_key, payload)

    def get_special_issue_verification(self, issue_key: str) -> dict[str, Any] | None:
        return self._get_payload("special_issue_verification", "issue_key", issue_key)

    def put_job_checkpoint(self, job_key: str, payload: dict[str, Any]) -> None:
        self._put_payload("job_checkpoints", "job_key", job_key, payload)

    def get_job_checkpoint(self, job_key: str) -> dict[str, Any] | None:
        return self._get_payload("job_checkpoints", "job_key", job_key)

    def clear_rebuildable_data(self) -> None:
        tables = (
            "source_responses",
            "works",
            "journal_evidence",
            "ocr_pages",
            "special_issue_discovery",
            "special_issue_verification",
            "dedupe_keys",
            "job_checkpoints",
        )
        with self._session() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for table in tables:
                connection.execute(f"DELETE FROM {table}")

    def integrity_check(self) -> str:
        connection = sqlite3.connect(self.path, timeout=12.0)
        try:
            row = connection.execute("PRAGMA integrity_check").fetchone()
        finally:
            connection.close()
        return str(row[0]) if row else ""
