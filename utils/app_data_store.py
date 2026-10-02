"""SQLite-backed durable application data.

Durable user state lives in ``research_assistant.sqlite``.  Older v13.1 builds
temporarily placed the ``app_*`` tables beside the rebuildable network cache in
``research_intelligence.sqlite``; the helpers below copy those tables into the
dedicated business database without parsing or rewriting their JSON payloads.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


APP_SCHEMA_VERSION = 2
_INITIALIZED: set[Path] = set()
_INITIALIZE_LOCK = threading.RLock()
_WRITE_LOCK = threading.RLock()


class StaleSnapshotError(RuntimeError):
    """A complete snapshot cannot replace a newer durable version."""

APP_TABLE_COPY_ORDER = (
    "app_schema_version",
    "app_datasets",
    "app_payloads",
    "app_frontier_articles",
    "app_frontier_candidates",
    "app_journals",
    "app_special_issues",
    "app_special_issue_sources",
    "app_special_issue_scopes",
    "app_special_issue_user_state",
    "app_special_deadline_candidates",
    "app_source_runs",
    "app_dedupe_fingerprints",
    "app_user_events",
    "app_task_runs",
)


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _json_load(value: str | bytes | None, default: Any) -> Any:
    if value in (None, ""):
        return deepcopy(default)
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return deepcopy(default)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def migrate_legacy_app_data(source: str | Path, destination: str | Path) -> dict[str, Any]:
    """Copy the legacy ``app_*`` tables into a fresh dedicated database.

    The destination schema is created first and the copy is one transaction.
    Identical retries are no-ops.  A newer legacy compatibility copy is allowed
    to replace the destination so edits made by the still-installed previous
    release are reconciled on the first start of the new code.
    """

    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    result: dict[str, Any] = {"migrated": False, "source": str(source_path), "destination": str(destination_path)}
    if source_path == destination_path or not source_path.is_file():
        result["reason"] = "source_missing_or_same"
        return result
    AppDataStore(destination_path)
    connection = sqlite3.connect(destination_path, timeout=30.0)
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("ATTACH DATABASE ? AS legacy", (str(source_path),))
        source_tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM legacy.sqlite_master WHERE type='table'")
        }
        if "app_datasets" not in source_tables:
            result["reason"] = "legacy_app_tables_missing"
            return result
        destination_rows = int(connection.execute("SELECT COUNT(*) FROM main.app_datasets").fetchone()[0])
        if destination_rows:
            source_signature = list(
                connection.execute(
                    "SELECT name,revision,item_count,updated_at FROM legacy.app_datasets ORDER BY name"
                )
            )
            destination_signature = list(
                connection.execute(
                    "SELECT name,revision,item_count,updated_at FROM main.app_datasets ORDER BY name"
                )
            )
            if source_signature == destination_signature:
                result["reason"] = "destination_current"
                return result
            destination_versions = {str(row[0]): row for row in destination_signature}
            selected = {
                str(row[0]) for row in source_signature
                if str(row[0]) not in destination_versions
                or (str(row[3]), int(row[1])) > (
                    str(destination_versions[str(row[0])][3]), int(destination_versions[str(row[0])][1])
                )
            }
            if not selected:
                result["reason"] = "destination_not_empty"
                return result
        else:
            selected = {str(row[0]) for row in connection.execute("SELECT name FROM legacy.app_datasets")}
        copied: dict[str, int] = {}
        connection.execute("BEGIN IMMEDIATE")
        try:
            for table in APP_TABLE_COPY_ORDER:
                if table not in source_tables:
                    continue
                owners = {
                    "app_frontier_articles": "frontier", "app_frontier_candidates": "frontier", "app_journals": "journals",
                    "app_special_issues": "special_issues", "app_special_issue_sources": "special_issues",
                    "app_special_issue_scopes": "special_issues", "app_special_issue_user_state": "special_issues",
                    "app_special_deadline_candidates": "special_issues", "app_task_runs": "runtime",
                }
                owner = owners.get(table)
                if owner is not None and owner not in selected:
                    continue
                if table == "app_schema_version" and destination_rows:
                    continue
                destination_columns = [
                    str(row[1]) for row in connection.execute(f"PRAGMA main.table_info({table})")
                ]
                source_columns = {
                    str(row[1]) for row in connection.execute(f"PRAGMA legacy.table_info({table})")
                }
                columns = [column for column in destination_columns if column in source_columns]
                if not columns:
                    continue
                quoted = ",".join(f'"{column}"' for column in columns)
                discriminator = "name" if table == "app_datasets" else "namespace" if "namespace" in columns else None
                condition = ""
                parameters: tuple[Any, ...] = ()
                if discriminator:
                    parameters = tuple(sorted(selected))
                    condition = f' WHERE "{discriminator}" IN ({",".join("?" for _ in parameters)})'
                connection.execute(f'DELETE FROM main."{table}"{condition}', parameters)
                connection.execute(
                    f'INSERT INTO main."{table}" ({quoted}) SELECT {quoted} FROM legacy."{table}"{condition}', parameters
                )
                copied[table] = int(connection.execute(f'SELECT COUNT(*) FROM main."{table}"{condition}', parameters).fetchone()[0])
                source_count = int(connection.execute(f'SELECT COUNT(*) FROM legacy."{table}"{condition}', parameters).fetchone()[0])
                if copied[table] != source_count:
                    raise sqlite3.DatabaseError(f"legacy copy count mismatch for {table}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        result.update(
            {
                "migrated": True,
                "tables": copied,
                "reason": "source_newer" if destination_rows else "copied",
            }
        )
        return result
    finally:
        try:
            connection.execute("DETACH DATABASE legacy")
        except sqlite3.Error:
            pass
        connection.close()


def drop_legacy_app_tables(cache_path: str | Path) -> int:
    """Remove inactive legacy business tables after the split was verified."""

    path = Path(cache_path).resolve()
    if not path.is_file():
        return 0
    connection = sqlite3.connect(path, timeout=30.0)
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        existing = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'app_%'"
            )
        }
        if not existing:
            return 0
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            dropped = 0
            for table in reversed(APP_TABLE_COPY_ORDER):
                if table in existing:
                    connection.execute(f'DROP TABLE IF EXISTS "{table}"')
                    dropped += 1
            for table in sorted(existing.difference(APP_TABLE_COPY_ORDER)):
                safe_table = table.replace('"', '""')
                connection.execute(f'DROP TABLE IF EXISTS "{safe_table}"')
                dropped += 1
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return dropped
    finally:
        connection.close()


def _candidate_source_key(candidate: dict[str, Any]) -> str:
    evidence = candidate.get("source_evidence", [])
    evidence = evidence if isinstance(evidence, list) else []
    latest = next((row for row in reversed(evidence) if isinstance(row, dict)), {})
    source = "|".join(
        str(candidate.get(key) or latest.get(key) or "").strip().casefold()
        for key in ("source", "source_id", "url", "source_url")
    ).strip("|")
    if source:
        return source[:900]
    stable = {
        str(key): value
        for key, value in latest.items()
        if str(key) not in {"fetched_at", "checked_at", "updated_at", "published_at"}
    }
    return "evidence:" + hashlib.sha1(_json_dump(stable).encode("utf-8")).hexdigest()


def compact_deadline_candidates(values: Any, *, limit: int = 10) -> list[dict[str, Any]]:
    """Collapse repeated deadline observations into a bounded audit history.

    A changed fetch timestamp is not a new candidate.  The newest complete
    evidence replaces older evidence for the same ``deadline + source`` pair,
    while first/last observation timestamps remain available for auditing.
    """

    rows = values if isinstance(values, list) else []
    compacted: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        deadline = str(raw.get("deadline", "")).strip()[:32]
        if not deadline:
            continue
        source_key = _candidate_source_key(raw)
        fetched = str(raw.get("last_seen_at") or raw.get("fetched_at") or "").strip()
        first = str(raw.get("first_seen_at") or raw.get("fetched_at") or "").strip()
        key = (deadline, source_key)
        previous = compacted.get(key)
        evidence = raw.get("source_evidence", [])
        evidence = [deepcopy(row) for row in evidence if isinstance(row, dict)] if isinstance(evidence, list) else []
        candidate = {
            "deadline": deadline,
            "source_key": source_key,
            "first_seen_at": first,
            "last_seen_at": fetched,
            "source_evidence": evidence,
        }
        if previous is None:
            compacted[key] = candidate
            continue
        first_values = [value for value in (previous.get("first_seen_at", ""), first) if value]
        last_values = [value for value in (previous.get("last_seen_at", ""), fetched) if value]
        previous["first_seen_at"] = min(first_values) if first_values else ""
        previous["last_seen_at"] = max(last_values) if last_values else ""
        if evidence and fetched >= str(previous.get("last_seen_at", "")):
            previous["source_evidence"] = evidence
    ordered = sorted(
        compacted.values(),
        key=lambda row: (str(row.get("last_seen_at", "")), str(row.get("deadline", "")), str(row.get("source_key", ""))),
    )
    return ordered[-max(1, int(limit)) :]


_SPECIAL_SCOPE_FIELDS = {
    "scope_text",
    "scope_text_zh",
    "scope_paragraphs",
    "scope_status",
    "scope_is_complete",
    "personal_scope_note",
}
_SPECIAL_USER_FIELDS = {
    "status",
    "legacy_status",
    "saved",
    "ignored",
    "is_read",
    "read_at",
    "last_read_at",
    "last_notified_at",
    "personal_revision",
    "linked_paper_ids",
    "created_task_ids",
    "submission_path_refs",
    "journal_library_id",
    "deadline_history",
    "verification_history",
}


class AppDataStore:
    """Transactional repository for durable application records."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self._transaction = threading.local()
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=12.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=12000")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    @contextmanager
    def _session(self, *, write: bool = False):
        existing = getattr(self._transaction, "connection", None)
        if existing is not None:
            yield existing
            return
        lock = _WRITE_LOCK if write else threading.RLock()
        with lock:
            connection = self._connect()
            try:
                if write:
                    connection.execute("BEGIN IMMEDIATE")
                yield connection
                if write:
                    connection.commit()
            except Exception:
                if write:
                    connection.rollback()
                raise
            finally:
                connection.close()

    def commit_frontier(self, payload: dict[str, Any], runtime: dict[str, Any]) -> None:
        """Commit the result, fingerprints and completion marker together."""
        with self._session(write=True) as connection:
            self._transaction.connection = connection
            try:
                row = connection.execute("SELECT revision FROM app_datasets WHERE name='frontier'").fetchone()
                payload["_storage_revision"] = int(row[0]) if row else 0
                self.save_frontier(payload)
                self.save_runtime(runtime)
            finally:
                self._transaction.connection = None

    def patch_frontier(self, before: dict[str, Any], after: dict[str, Any]) -> None:
        """Apply only edited fields to current records, never a stale full snapshot."""
        old = {str(item.get("id")): item for item in before.get("items", [])}
        new = {str(item.get("id")): item for item in after.get("items", [])}
        with self._session(write=True) as connection:
            for item_id, item in new.items():
                baseline = old.get(item_id, {})
                changes = {key: value for key, value in item.items() if value != baseline.get(key)}
                if not changes:
                    continue
                row = connection.execute("SELECT payload_json FROM app_frontier_articles WHERE article_id=?", (item_id,)).fetchone()
                if row is None:
                    # A refresh may have deliberately retired this record.
                    continue
                merged = _json_load(row[0], {})
                merged.update(changes)
                connection.execute(
                    """UPDATE app_frontier_articles SET status=?,display_bucket=?,relevance_score=?,
                       research_value_score=?,total_score=?,daily_rank=?,payload_json=?,updated_at=? WHERE article_id=?""",
                    (str(merged.get("status", "")), str(merged.get("display_bucket", "")),
                     _integer(merged.get("relevance_score")), _integer(merged.get("research_value_score")),
                     _integer(merged.get("score", merged.get("total_score", 0))), _integer(merged.get("daily_rank")),
                     _json_dump(merged), _now(), item_id),
                )
            for item_id in old.keys() - new.keys():
                connection.execute("DELETE FROM app_frontier_articles WHERE article_id=?", (item_id,))
            meta = self._get_payload(connection, "frontier", "meta", {})
            meta.update({key: value for key, value in after.items()
                         if key not in {"items", "fingerprints", "_storage_revision"} and value != before.get(key)})
            self._put_payload(connection, "frontier", "meta", meta)
            self._mark_dataset(connection, "frontier", int(connection.execute("SELECT COUNT(*) FROM app_frontier_articles").fetchone()[0]))

    def initialize(self) -> None:
        with _INITIALIZE_LOCK:
            if self.path in _INITIALIZED and self.path.exists():
                return
            statements = (
                "CREATE TABLE IF NOT EXISTS app_schema_version (version INTEGER NOT NULL)",
                """CREATE TABLE IF NOT EXISTS app_frontier_candidates (
                    article_id TEXT PRIMARY KEY, candidate_state TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL, updated_at TEXT NOT NULL)""",
                "CREATE INDEX IF NOT EXISTS app_frontier_candidates_state_idx ON app_frontier_candidates(candidate_state,updated_at)",
                """CREATE TABLE IF NOT EXISTS app_datasets (
                    name TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0,
                    item_count INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
                )""",
                """CREATE TABLE IF NOT EXISTS app_payloads (
                    namespace TEXT NOT NULL, key TEXT NOT NULL, payload_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL, PRIMARY KEY(namespace, key)
                )""",
                """CREATE TABLE IF NOT EXISTS app_frontier_articles (
                    article_id TEXT PRIMARY KEY, ordinal INTEGER NOT NULL, doi TEXT NOT NULL DEFAULT '',
                    fingerprint TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '',
                    display_bucket TEXT NOT NULL DEFAULT '', recommendation_date TEXT NOT NULL DEFAULT '',
                    relevance_score INTEGER NOT NULL DEFAULT 0, research_value_score INTEGER NOT NULL DEFAULT 0,
                    ai_score INTEGER NOT NULL DEFAULT 0, total_score INTEGER NOT NULL DEFAULT 0,
                    daily_rank INTEGER NOT NULL DEFAULT 0, payload_json TEXT NOT NULL, updated_at TEXT NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS app_frontier_display_idx ON app_frontier_articles(display_bucket, recommendation_date, daily_rank)",
                "CREATE INDEX IF NOT EXISTS app_frontier_status_idx ON app_frontier_articles(status)",
                "CREATE INDEX IF NOT EXISTS app_frontier_doi_idx ON app_frontier_articles(doi)",
                "CREATE INDEX IF NOT EXISTS app_frontier_fingerprint_idx ON app_frontier_articles(fingerprint)",
                """CREATE TABLE IF NOT EXISTS app_journals (
                    journal_id TEXT PRIMARY KEY, ordinal INTEGER NOT NULL, name TEXT NOT NULL,
                    publisher TEXT NOT NULL DEFAULT '', issn TEXT NOT NULL DEFAULT '',
                    jcr_quartile TEXT NOT NULL DEFAULT '', cas_quartile TEXT NOT NULL DEFAULT '',
                    favorite INTEGER NOT NULL DEFAULT 0, payload_json TEXT NOT NULL, updated_at TEXT NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS app_journal_name_idx ON app_journals(name)",
                "CREATE INDEX IF NOT EXISTS app_journal_publisher_idx ON app_journals(publisher)",
                "CREATE INDEX IF NOT EXISTS app_journal_issn_idx ON app_journals(issn)",
                """CREATE TABLE IF NOT EXISTS app_special_issues (
                    issue_id TEXT PRIMARY KEY, ordinal INTEGER NOT NULL, title TEXT NOT NULL DEFAULT '',
                    journal TEXT NOT NULL DEFAULT '', publisher TEXT NOT NULL DEFAULT '',
                    dedupe_key TEXT NOT NULL DEFAULT '',
                    deadline TEXT NOT NULL DEFAULT '', candidate_state TEXT NOT NULL DEFAULT '',
                    scoring_version TEXT NOT NULL DEFAULT '', relevance_score INTEGER NOT NULL DEFAULT 0,
                    opportunity_score INTEGER NOT NULL DEFAULT 0, match_score INTEGER NOT NULL DEFAULT 0,
                    content_qualified INTEGER NOT NULL DEFAULT 0, scope_complete INTEGER NOT NULL DEFAULT 0,
                    has_history INTEGER NOT NULL DEFAULT 0, payload_json TEXT NOT NULL, updated_at TEXT NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS app_special_visible_idx ON app_special_issues(candidate_state, deadline)",
                "CREATE INDEX IF NOT EXISTS app_special_journal_idx ON app_special_issues(journal)",
                "CREATE INDEX IF NOT EXISTS app_special_publisher_idx ON app_special_issues(publisher)",
                """CREATE TABLE IF NOT EXISTS app_special_issue_sources (
                    issue_id TEXT NOT NULL, ordinal INTEGER NOT NULL, source_key TEXT NOT NULL DEFAULT '',
                    verification_status TEXT NOT NULL DEFAULT '', evidence_json TEXT NOT NULL,
                    PRIMARY KEY(issue_id, ordinal),
                    FOREIGN KEY(issue_id) REFERENCES app_special_issues(issue_id) ON DELETE CASCADE
                )""",
                """CREATE TABLE IF NOT EXISTS app_special_issue_scopes (
                    issue_id TEXT PRIMARY KEY, scope_text TEXT NOT NULL DEFAULT '', scope_text_zh TEXT NOT NULL DEFAULT '',
                    scope_status TEXT NOT NULL DEFAULT '', scope_complete INTEGER NOT NULL DEFAULT 0,
                    paragraphs_json TEXT NOT NULL DEFAULT '[]', personal_scope_note TEXT NOT NULL DEFAULT '',
                    FOREIGN KEY(issue_id) REFERENCES app_special_issues(issue_id) ON DELETE CASCADE
                )""",
                """CREATE TABLE IF NOT EXISTS app_special_issue_user_state (
                    issue_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'unread', saved INTEGER NOT NULL DEFAULT 0,
                    ignored INTEGER NOT NULL DEFAULT 0, is_read INTEGER NOT NULL DEFAULT 0,
                    user_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL,
                    FOREIGN KEY(issue_id) REFERENCES app_special_issues(issue_id) ON DELETE CASCADE
                )""",
                "CREATE INDEX IF NOT EXISTS app_special_user_status_idx ON app_special_issue_user_state(status, saved, ignored)",
                """CREATE TABLE IF NOT EXISTS app_special_deadline_candidates (
                    issue_id TEXT NOT NULL, ordinal INTEGER NOT NULL, deadline TEXT NOT NULL,
                    source_key TEXT NOT NULL, first_seen_at TEXT NOT NULL DEFAULT '', last_seen_at TEXT NOT NULL DEFAULT '',
                    evidence_json TEXT NOT NULL DEFAULT '[]', PRIMARY KEY(issue_id, deadline, source_key),
                    FOREIGN KEY(issue_id) REFERENCES app_special_issues(issue_id) ON DELETE CASCADE
                )""",
                """CREATE TABLE IF NOT EXISTS app_source_runs (
                    namespace TEXT NOT NULL, source_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL, payload_json TEXT NOT NULL,
                    PRIMARY KEY(namespace, source_id)
                )""",
                """CREATE TABLE IF NOT EXISTS app_dedupe_fingerprints (
                    namespace TEXT NOT NULL, fingerprint TEXT NOT NULL, ordinal INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY(namespace, fingerprint)
                )""",
                """CREATE TABLE IF NOT EXISTS app_user_events (
                    namespace TEXT NOT NULL, event_id TEXT NOT NULL, ordinal INTEGER NOT NULL DEFAULT 0,
                    item_id TEXT NOT NULL DEFAULT '', event_type TEXT NOT NULL DEFAULT '', event_at TEXT NOT NULL DEFAULT '',
                    payload_json TEXT NOT NULL, PRIMARY KEY(namespace, event_id)
                )""",
                """CREATE TABLE IF NOT EXISTS app_task_runs (
                    batch_id TEXT PRIMARY KEY, task TEXT NOT NULL DEFAULT '', day TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL
                )""",
                "CREATE INDEX IF NOT EXISTS app_task_runs_lookup_idx ON app_task_runs(task, day, state, updated_at)",
                "CREATE INDEX IF NOT EXISTS app_journals_ordinal_idx ON app_journals(ordinal)",
            )
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                for statement in statements:
                    connection.execute(statement)
                special_columns = {
                    str(value[1])
                    for value in connection.execute("PRAGMA table_info(app_special_issues)")
                }
                if "dedupe_key" not in special_columns:
                    connection.execute(
                        "ALTER TABLE app_special_issues ADD COLUMN dedupe_key TEXT NOT NULL DEFAULT ''"
                    )
                    connection.execute(
                        """UPDATE app_special_issues
                           SET dedupe_key=COALESCE(NULLIF(json_extract(payload_json,'$.dedupe_key'),''),issue_id)"""
                    )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS app_special_dedupe_idx ON app_special_issues(dedupe_key)"
                )
                row = connection.execute("SELECT version FROM app_schema_version LIMIT 1").fetchone()
                if row is None:
                    connection.execute("INSERT INTO app_schema_version(version) VALUES (?)", (APP_SCHEMA_VERSION,))
                elif int(row[0]) < APP_SCHEMA_VERSION:
                    connection.execute("UPDATE app_schema_version SET version=?", (APP_SCHEMA_VERSION,))
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            _INITIALIZED.add(self.path)

    @staticmethod
    def _mark_dataset(connection: sqlite3.Connection, name: str, count: int) -> None:
        connection.execute(
            """INSERT INTO app_datasets(name, revision, item_count, updated_at) VALUES (?, 1, ?, ?)
               ON CONFLICT(name) DO UPDATE SET revision=app_datasets.revision+1,
                 item_count=excluded.item_count, updated_at=excluded.updated_at""",
            (name, int(count), _now()),
        )

    @staticmethod
    def _touch_dataset(connection: sqlite3.Connection, name: str) -> None:
        connection.execute(
            "UPDATE app_datasets SET revision=revision+1,updated_at=? WHERE name=?",
            (_now(), name),
        )

    @classmethod
    def _touch_special_dataset(cls, connection: sqlite3.Connection) -> None:
        cls._touch_dataset(connection, "special_issues")
        meta = cls._get_payload(connection, "special_issues", "meta", {})
        meta = meta if isinstance(meta, dict) else {}
        meta["revision"] = _integer(meta.get("revision")) + 1
        cls._put_payload(connection, "special_issues", "meta", meta)

    def has_dataset(self, name: str) -> bool:
        with self._session() as connection:
            return connection.execute("SELECT 1 FROM app_datasets WHERE name=?", (name,)).fetchone() is not None

    def save_named_payload(self, name: str, payload: Any, *, item_count: int = 1) -> None:
        namespace = str(name).strip()
        if not namespace:
            raise ValueError("payload namespace is required")
        with self._session(write=True) as connection:
            self._put_payload(connection, namespace, "store", payload)
            self._mark_dataset(connection, namespace, max(0, int(item_count)))

    def load_named_payload(self, name: str, default: Any = None) -> Any:
        namespace = str(name).strip()
        if not namespace or not self.has_dataset(namespace):
            return None
        with self._session() as connection:
            return self._get_payload(connection, namespace, "store", default)

    @staticmethod
    def _put_payload(connection: sqlite3.Connection, namespace: str, key: str, payload: Any) -> None:
        connection.execute(
            """INSERT INTO app_payloads(namespace,key,payload_json,updated_at) VALUES (?,?,?,?)
               ON CONFLICT(namespace,key) DO UPDATE SET payload_json=excluded.payload_json,
                 updated_at=excluded.updated_at""",
            (namespace, key, _json_dump(payload), _now()),
        )

    @staticmethod
    def _get_payload(connection: sqlite3.Connection, namespace: str, key: str, default: Any) -> Any:
        row = connection.execute(
            "SELECT payload_json FROM app_payloads WHERE namespace=? AND key=?", (namespace, key)
        ).fetchone()
        return _json_load(row[0], default) if row is not None else deepcopy(default)

    def save_frontier(self, payload: dict[str, Any]) -> None:
        value = deepcopy(payload) if isinstance(payload, dict) else {}
        expected_revision = value.pop("_storage_revision", None)
        items = [row for row in value.pop("items", []) if isinstance(row, dict)]
        fingerprints = [row for row in value.pop("fingerprints", []) if isinstance(row, dict)]
        now = _now()
        rows = []
        for ordinal, item in enumerate(items):
            item_id = str(item.get("id") or item.get("fingerprint") or f"frontier-{ordinal}").strip()
            score_breakdown = item.get("score_breakdown", {}) if isinstance(item.get("score_breakdown"), dict) else {}
            rows.append(
                (
                    item_id, ordinal, str(item.get("doi", "")).strip().casefold(), str(item.get("fingerprint", "")).strip(),
                    str(item.get("status", "")), str(item.get("display_bucket", "")), str(item.get("recommendation_date", "")),
                    _integer(item.get("relevance_score")), _integer(item.get("research_value_score")),
                    _integer(score_breakdown.get("ai")), _integer(item.get("score", item.get("total_score", 0))),
                    _integer(item.get("daily_rank", ordinal + 1)), _json_dump(item), now,
                )
            )
        with self._session(write=True) as connection:
            current = connection.execute("SELECT revision FROM app_datasets WHERE name='frontier'").fetchone()
            revision = int(current[0]) if current else 0
            if expected_revision is not None and int(expected_revision) != revision:
                raise StaleSnapshotError("每日前沿已更新，请重新加载后保存")
            self._sync_rows(connection, "app_frontier_articles",
                "article_id,ordinal,doi,fingerprint,status,display_bucket,recommendation_date,relevance_score,research_value_score,ai_score,total_score,daily_rank,payload_json,updated_at", rows)
            fingerprint_map: dict[str, tuple[str, str, int, str, str]] = {}
            for ordinal, entry in enumerate(fingerprints):
                key = str(entry.get("fingerprint") or entry.get("key") or "").strip()
                if key:
                    # Legacy stores may contain the same persistent fingerprint
                    # more than once.  Its newest record is authoritative.
                    fingerprint_map[key] = ("frontier", key, ordinal, _json_dump(entry), now)
            connection.executemany(
                """INSERT INTO app_dedupe_fingerprints(namespace,fingerprint,ordinal,payload_json,updated_at) VALUES (?,?,?,?,?)
                   ON CONFLICT(namespace,fingerprint) DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                list(fingerprint_map.values()),
            )
            connection.execute("DELETE FROM app_source_runs WHERE namespace='frontier'")
            health = value.get("source_health", {}) if isinstance(value.get("source_health"), dict) else {}
            watermarks = value.get("source_watermarks", {}) if isinstance(value.get("source_watermarks"), dict) else {}
            source_ids = sorted({str(key) for key in health} | {str(key) for key in watermarks})
            connection.executemany(
                "INSERT INTO app_source_runs(namespace,source_id,state,updated_at,payload_json) VALUES (?,?,?,?,?)",
                [
                    (
                        "frontier", source_id,
                        str(health.get(source_id, {}).get("status", "")) if isinstance(health.get(source_id), dict) else "",
                        str(health.get(source_id, {}).get("updated_at", now)) if isinstance(health.get(source_id), dict) else now,
                        _json_dump({"health": health.get(source_id), "watermark": watermarks.get(source_id)}),
                    )
                    for source_id in source_ids
                ],
            )
            self._put_payload(connection, "frontier", "meta", value)
            self._mark_dataset(connection, "frontier", len(rows))
        payload["_storage_revision"] = revision + 1

    def load_frontier(self) -> dict[str, Any] | None:
        if not self.has_dataset("frontier"):
            return None
        with self._session() as connection:
            result = self._get_payload(connection, "frontier", "meta", {})
            result["_storage_revision"] = int(connection.execute(
                "SELECT revision FROM app_datasets WHERE name='frontier'"
            ).fetchone()[0])
            result["items"] = [
                _json_load(row[0], {})
                for row in connection.execute("SELECT payload_json FROM app_frontier_articles ORDER BY ordinal")
            ]
            result["fingerprints"] = [
                _json_load(row[0], {})
                for row in connection.execute(
                    "SELECT payload_json FROM app_dedupe_fingerprints WHERE namespace='frontier' ORDER BY ordinal"
                )
            ]
            return result

    def query_frontier(self, *, offset: int = 0, limit: int = 30, display_bucket: str = "") -> list[dict[str, Any]]:
        sql = "SELECT payload_json FROM app_frontier_articles"
        params: list[Any] = []
        if display_bucket:
            sql += " WHERE display_bucket=?"
            params.append(display_bucket)
        sql += " ORDER BY recommendation_date DESC, daily_rank, ordinal LIMIT ? OFFSET ?"
        params.extend((max(1, int(limit)), max(0, int(offset))))
        with self._session() as connection:
            return [_json_load(row[0], {}) for row in connection.execute(sql, params)]

    def save_frontier_candidates(self, items) -> None:
        rows = []
        for item in items:
            key = str(item.get("id") or item.get("fingerprint") or "")
            if key:
                rows.append((key, str(item.get("candidate_state", "")), _json_dump(item), _now()))
        for offset in range(0, len(rows), 250):
            with self._session(write=True) as connection:
                connection.executemany(
                    """INSERT INTO app_frontier_candidates VALUES (?,?,?,?)
                       ON CONFLICT(article_id) DO UPDATE SET candidate_state=excluded.candidate_state,
                       payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                    rows[offset:offset + 250],
                )

    def load_frontier_preview(self, *, limit: int = 120) -> dict[str, Any] | None:
        """Load only preselected cards required by the home-page summary."""

        if not self.has_dataset("frontier"):
            return None
        with self._session() as connection:
            result = self._get_payload(connection, "frontier", "meta", {})
            result["items"] = [
                _json_load(row[0], {})
                for row in connection.execute(
                    """SELECT payload_json FROM app_frontier_articles
                       WHERE display_bucket IN ('today','previous_unread')
                       ORDER BY recommendation_date DESC,daily_rank,ordinal LIMIT ?""",
                    (max(1, int(limit)),),
                )
            ]
            result["fingerprints"] = []
            return result

    def save_journals(self, journals: Iterable[dict[str, Any]]) -> None:
        now = _now()
        rows = []
        for ordinal, journal in enumerate(row for row in journals if isinstance(row, dict)):
            journal_id = str(journal.get("id") or f"journal-{ordinal}").strip()
            jcr = journal.get("jcr", {}) if isinstance(journal.get("jcr"), dict) else {}
            easy = journal.get("easyscholar", {}) if isinstance(journal.get("easyscholar"), dict) else {}
            rows.append(
                (
                    journal_id, ordinal, str(journal.get("name", "")).strip(), str(journal.get("publisher", "")).strip(),
                    str(journal.get("issn", "")).strip(), str(jcr.get("quartile", jcr.get("jcr_quartile", ""))).strip(),
                    str(easy.get("cas_upgrade", easy.get("cas_basic", ""))).strip(), int(bool(journal.get("favorite"))),
                    _json_dump(journal), now,
                )
            )
        with self._session(write=True) as connection:
            self._sync_rows(connection, "app_journals",
                "journal_id,ordinal,name,publisher,issn,jcr_quartile,cas_quartile,favorite,payload_json,updated_at", rows)
            self._mark_dataset(connection, "journals", len(rows))

    def load_journals(self) -> list[dict[str, Any]] | None:
        if not self.has_dataset("journals"):
            return None
        with self._session() as connection:
            return [_json_load(row[0], {}) for row in connection.execute("SELECT payload_json FROM app_journals ORDER BY ordinal")]

    def journal_summaries(self) -> list[dict[str, Any]]:
        with self._session() as connection:
            return [{"id": row[0], "name": row[1], "publisher": row[2], "_summary": True}
                    for row in connection.execute("SELECT journal_id,name,publisher FROM app_journals ORDER BY ordinal")]

    def journal_shelf_page(self, *, offset=0, limit=40, search="", category="全部", group="按出版社", used_names=()):
        clauses, parameters = [], []
        if search.strip():
            clauses.append("instr(lower(payload_json),?)>0")
            parameters.append(search.strip().casefold())
        if category == "已收藏":
            clauses.append("favorite=1")
        elif category == "已有投稿记录":
            names = tuple(used_names)
            clauses.append("lower(name) IN (" + ",".join("?" for _ in names) + ")" if names else "0")
            parameters.extend(names)
        elif category == "待补资料":
            clauses.append("(issn='' OR COALESCE(json_extract(payload_json,'$.website'),'')='' OR COALESCE(json_array_length(payload_json,'$.fields'),0)=0)")
        elif category in {"JCR 已核验", "JCR 待核验"}:
            clauses.append("COALESCE(json_extract(payload_json,'$.jcr.status'),'')" + ("=" if category == "JCR 已核验" else "<>") + "'verified'")
        elif category.startswith("JCR Q"):
            clauses.append("jcr_quartile=?")
            parameters.append(category[-2:])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        grouping = "'全部期刊'" if group == "全部期刊" else "COALESCE(json_extract(payload_json,'$.fields[0]'),'未分类')" if group == "按标签" else "publisher"
        order = "ordinal" if group == "全部期刊" else f"{grouping} COLLATE NOCASE,ordinal"
        with self._session() as connection:
            count = int(connection.execute("SELECT COUNT(*) FROM app_journals" + where, parameters).fetchone()[0])
            groups = {str(row[0] or "未分类"): int(row[1]) for row in connection.execute(
                f"SELECT {grouping},COUNT(*) FROM app_journals{where} GROUP BY {grouping}", parameters)}
            rows = [_json_load(row[0], {}) for row in connection.execute(
                f"SELECT payload_json FROM app_journals{where} ORDER BY {order} LIMIT ? OFFSET ?",
                (*parameters, max(1, int(limit)), max(0, int(offset))))]
            return rows, count, groups

    def query_journals(self, *, offset: int = 0, limit: int = 40, search: str = "") -> list[dict[str, Any]]:
        sql = "SELECT payload_json FROM app_journals"
        params: list[Any] = []
        if search.strip():
            sql += " WHERE name LIKE ? OR publisher LIKE ? OR issn LIKE ?"
            needle = f"%{search.strip()}%"
            params.extend((needle, needle, needle))
        sql += " ORDER BY ordinal LIMIT ? OFFSET ?"
        params.extend((max(1, int(limit)), max(0, int(offset))))
        with self._session() as connection:
            return [_json_load(row[0], {}) for row in connection.execute(sql, params)]

    @staticmethod
    def _special_core(item: dict[str, Any]) -> dict[str, Any]:
        excluded = _SPECIAL_SCOPE_FIELDS | _SPECIAL_USER_FIELDS | {"source_evidence", "deadline_candidates"}
        return {str(key): deepcopy(value) for key, value in item.items() if str(key) not in excluded}

    def save_special_issues(self, payload: dict[str, Any]) -> None:
        value = deepcopy(payload) if isinstance(payload, dict) else {}
        items = [row for row in value.pop("items", []) if isinstance(row, dict) and str(row.get("id", "")).strip()]
        source_checkpoints = value.pop("source_checkpoints", {})
        logs = {
            key: value.pop(key, [])
            for key in ("action_log", "notification_log", "reminder_log", "notification_outbox")
        }
        now = _now()
        issue_rows = []
        source_rows = []
        scope_rows = []
        user_rows = []
        candidate_rows = []
        for ordinal, item in enumerate(items):
            issue_id = str(item.get("id", "")).strip()
            match = item.get("match", {}) if isinstance(item.get("match"), dict) else {}
            user_payload = {key: deepcopy(item.get(key)) for key in _SPECIAL_USER_FIELDS if key in item}
            sources = item.get("source_evidence", []) if isinstance(item.get("source_evidence"), list) else []
            scope_complete = int(bool(item.get("scope_is_complete") or item.get("scope_status") == "full"))
            has_history = int(bool(item.get("deadline_history") or item.get("verification_history")))
            issue_rows.append(
                (
                    issue_id, ordinal, str(item.get("title", "")), str(item.get("journal", "")),
                    str(item.get("publisher", "")), str(item.get("dedupe_key", issue_id)),
                    str(item.get("deadline", "")), str(item.get("candidate_state", "")),
                    str(item.get("scoring_version", "")), _integer(item.get("relevance_score")),
                    _integer(item.get("opportunity_score")), _integer(match.get("score")),
                    int(bool(match.get("content_qualified", match.get("formal", False)))), scope_complete, has_history,
                    _json_dump(self._special_core(item)), now,
                )
            )
            for source_ordinal, evidence in enumerate(row for row in sources if isinstance(row, dict)):
                source_key = "|".join(
                    str(evidence.get(field, "")).strip() for field in ("source", "url", "source_url")
                ).strip("|")
                source_rows.append(
                    (issue_id, source_ordinal, source_key, str(item.get("verification_status", "")), _json_dump(evidence))
                )
            scope_rows.append(
                (
                    issue_id, str(item.get("scope_text", "")), str(item.get("scope_text_zh", "")),
                    str(item.get("scope_status", "")), scope_complete,
                    _json_dump(item.get("scope_paragraphs", []) if isinstance(item.get("scope_paragraphs"), list) else []),
                    str(item.get("personal_scope_note", "")),
                )
            )
            status = str(item.get("status", "unread"))
            user_rows.append(
                (
                    issue_id, status, int(bool(item.get("saved"))), int(bool(item.get("ignored"))),
                    int(bool(item.get("is_read"))), _json_dump(user_payload), now,
                )
            )
            for candidate_ordinal, candidate in enumerate(compact_deadline_candidates(item.get("deadline_candidates", []), limit=10)):
                candidate_rows.append(
                    (
                        issue_id, candidate_ordinal, candidate["deadline"], candidate["source_key"],
                        candidate.get("first_seen_at", ""), candidate.get("last_seen_at", ""),
                        _json_dump(candidate.get("source_evidence", [])),
                    )
                )
        with self._session(write=True) as connection:
            connection.execute("DELETE FROM app_special_issues")
            connection.executemany(
                """INSERT INTO app_special_issues(
                    issue_id,ordinal,title,journal,publisher,dedupe_key,deadline,candidate_state,scoring_version,
                    relevance_score,opportunity_score,match_score,content_qualified,scope_complete,
                    has_history,payload_json,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                issue_rows,
            )
            connection.executemany(
                "INSERT INTO app_special_issue_sources(issue_id,ordinal,source_key,verification_status,evidence_json) VALUES (?,?,?,?,?)",
                source_rows,
            )
            connection.executemany(
                """INSERT INTO app_special_issue_scopes(
                    issue_id,scope_text,scope_text_zh,scope_status,scope_complete,paragraphs_json,personal_scope_note
                ) VALUES (?,?,?,?,?,?,?)""",
                scope_rows,
            )
            connection.executemany(
                """INSERT INTO app_special_issue_user_state(
                    issue_id,status,saved,ignored,is_read,user_json,updated_at
                ) VALUES (?,?,?,?,?,?,?)""",
                user_rows,
            )
            connection.executemany(
                """INSERT INTO app_special_deadline_candidates(
                    issue_id,ordinal,deadline,source_key,first_seen_at,last_seen_at,evidence_json
                ) VALUES (?,?,?,?,?,?,?)""",
                candidate_rows,
            )
            connection.execute("DELETE FROM app_source_runs WHERE namespace='special_issues'")
            if isinstance(source_checkpoints, dict):
                connection.executemany(
                    "INSERT INTO app_source_runs(namespace,source_id,state,updated_at,payload_json) VALUES (?,?,?,?,?)",
                    [
                        (
                            "special_issues", str(source_id), str(record.get("state", "")) if isinstance(record, dict) else "",
                            str(record.get("updated_at", now)) if isinstance(record, dict) else now, _json_dump(record),
                        )
                        for source_id, record in source_checkpoints.items()
                    ],
                )
            connection.execute("DELETE FROM app_user_events WHERE namespace LIKE 'special_issues:%'")
            for kind, entries in logs.items():
                prepared = []
                for log_ordinal, entry in enumerate(entries if isinstance(entries, list) else []):
                    if not isinstance(entry, dict):
                        continue
                    event_id = str(entry.get("id") or f"{kind}-{log_ordinal}")
                    prepared.append(
                        (
                            f"special_issues:{kind}", event_id, log_ordinal, str(entry.get("issue_id", "")),
                            str(entry.get("kind", entry.get("action", ""))),
                            str(entry.get("at", entry.get("notified_at", entry.get("sent_at", "")))), _json_dump(entry),
                        )
                    )
                connection.executemany(
                    """INSERT INTO app_user_events(
                        namespace,event_id,ordinal,item_id,event_type,event_at,payload_json
                    ) VALUES (?,?,?,?,?,?,?)""",
                    prepared,
                )
            self._put_payload(connection, "special_issues", "meta", value)
            self._mark_dataset(connection, "special_issues", len(issue_rows))

    @staticmethod
    def _reconstruct_special_rows(connection: sqlite3.Connection, issue_rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
        if not issue_rows:
            return []
        ids = [str(row["issue_id"]) for row in issue_rows]
        placeholders = ",".join("?" for _ in ids)
        sources: dict[str, list[dict[str, Any]]] = {}
        for row in connection.execute(
            f"SELECT issue_id,evidence_json FROM app_special_issue_sources WHERE issue_id IN ({placeholders}) ORDER BY issue_id,ordinal",
            ids,
        ):
            sources.setdefault(str(row["issue_id"]), []).append(_json_load(row["evidence_json"], {}))
        scopes = {
            str(row["issue_id"]): row
            for row in connection.execute(
                f"SELECT * FROM app_special_issue_scopes WHERE issue_id IN ({placeholders})", ids
            )
        }
        users = {
            str(row["issue_id"]): row
            for row in connection.execute(
                f"SELECT * FROM app_special_issue_user_state WHERE issue_id IN ({placeholders})", ids
            )
        }
        candidates: dict[str, list[dict[str, Any]]] = {}
        for row in connection.execute(
            f"""SELECT * FROM app_special_deadline_candidates WHERE issue_id IN ({placeholders})
                ORDER BY issue_id,ordinal""",
            ids,
        ):
            candidates.setdefault(str(row["issue_id"]), []).append(
                {
                    "deadline": str(row["deadline"]),
                    "source_key": str(row["source_key"]),
                    "first_seen_at": str(row["first_seen_at"]),
                    "last_seen_at": str(row["last_seen_at"]),
                    "fetched_at": str(row["last_seen_at"]),
                    "source_evidence": _json_load(row["evidence_json"], []),
                }
            )
        result = []
        for row in issue_rows:
            issue_id = str(row["issue_id"])
            item = _json_load(row["payload_json"], {})
            item["source_evidence"] = sources.get(issue_id, [])
            scope = scopes.get(issue_id)
            if scope is not None:
                item.update(
                    {
                        "scope_text": str(scope["scope_text"]),
                        "scope_text_zh": str(scope["scope_text_zh"]),
                        "scope_status": str(scope["scope_status"]),
                        "scope_is_complete": bool(scope["scope_complete"]),
                        "scope_paragraphs": _json_load(scope["paragraphs_json"], []),
                        "personal_scope_note": str(scope["personal_scope_note"]),
                    }
                )
            user = users.get(issue_id)
            if user is not None:
                item.update(_json_load(user["user_json"], {}))
                item.update(
                    {
                        "status": str(user["status"]), "saved": bool(user["saved"]),
                        "ignored": bool(user["ignored"]), "is_read": bool(user["is_read"]),
                    }
                )
            if candidates.get(issue_id):
                item["deadline_candidates"] = candidates[issue_id]
            result.append(item)
        return result

    @staticmethod
    def _upsert_special_rows(
        connection: sqlite3.Connection,
        items: Iterable[dict[str, Any]],
    ) -> int:
        """Write only changed special issues while preserving personal state."""

        next_ordinal = int(
            connection.execute(
                "SELECT COALESCE(MAX(ordinal),-1)+1 FROM app_special_issues"
            ).fetchone()[0]
        )
        written = 0
        now = _now()
        for item in (value for value in items if isinstance(value, dict)):
            issue_id = str(item.get("id", "")).strip()
            if not issue_id:
                continue
            existing = connection.execute(
                "SELECT ordinal FROM app_special_issues WHERE issue_id=?", (issue_id,)
            ).fetchone()
            ordinal = int(existing[0]) if existing is not None else next_ordinal
            if existing is None:
                next_ordinal += 1
            match = item.get("match", {}) if isinstance(item.get("match"), dict) else {}
            sources = item.get("source_evidence", []) if isinstance(item.get("source_evidence"), list) else []
            scope_complete = int(bool(item.get("scope_is_complete") or item.get("scope_status") == "full"))
            has_history = int(bool(item.get("deadline_history") or item.get("verification_history")))
            connection.execute(
                """INSERT INTO app_special_issues(
                    issue_id,ordinal,title,journal,publisher,dedupe_key,deadline,candidate_state,
                    scoring_version,relevance_score,opportunity_score,match_score,content_qualified,
                    scope_complete,has_history,payload_json,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(issue_id) DO UPDATE SET
                    title=excluded.title,journal=excluded.journal,publisher=excluded.publisher,
                    dedupe_key=excluded.dedupe_key,deadline=excluded.deadline,
                    candidate_state=excluded.candidate_state,scoring_version=excluded.scoring_version,
                    relevance_score=excluded.relevance_score,opportunity_score=excluded.opportunity_score,
                    match_score=excluded.match_score,content_qualified=excluded.content_qualified,
                    scope_complete=excluded.scope_complete,has_history=excluded.has_history,
                    payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                (
                    issue_id, ordinal, str(item.get("title", "")), str(item.get("journal", "")),
                    str(item.get("publisher", "")), str(item.get("dedupe_key", issue_id)),
                    str(item.get("deadline", "")), str(item.get("candidate_state", "")),
                    str(item.get("scoring_version", "")), _integer(item.get("relevance_score")),
                    _integer(item.get("opportunity_score")), _integer(match.get("score")),
                    int(bool(match.get("content_qualified", match.get("formal", False)))),
                    scope_complete, has_history, _json_dump(AppDataStore._special_core(item)), now,
                ),
            )
            connection.execute("DELETE FROM app_special_issue_sources WHERE issue_id=?", (issue_id,))
            connection.executemany(
                """INSERT INTO app_special_issue_sources(
                    issue_id,ordinal,source_key,verification_status,evidence_json
                ) VALUES (?,?,?,?,?)""",
                [
                    (
                        issue_id, source_ordinal,
                        "|".join(
                            str(evidence.get(field, "")).strip()
                            for field in ("source", "url", "source_url")
                        ).strip("|"),
                        str(item.get("verification_status", "")), _json_dump(evidence),
                    )
                    for source_ordinal, evidence in enumerate(sources)
                    if isinstance(evidence, dict)
                ],
            )
            connection.execute(
                """INSERT INTO app_special_issue_scopes(
                    issue_id,scope_text,scope_text_zh,scope_status,scope_complete,
                    paragraphs_json,personal_scope_note
                ) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(issue_id) DO UPDATE SET
                    scope_text=excluded.scope_text,scope_text_zh=excluded.scope_text_zh,
                    scope_status=excluded.scope_status,scope_complete=excluded.scope_complete,
                    paragraphs_json=excluded.paragraphs_json,
                    personal_scope_note=CASE
                        WHEN excluded.personal_scope_note<>'' THEN excluded.personal_scope_note
                        ELSE app_special_issue_scopes.personal_scope_note END""",
                (
                    issue_id, str(item.get("scope_text", "")), str(item.get("scope_text_zh", "")),
                    str(item.get("scope_status", "")), scope_complete,
                    _json_dump(item.get("scope_paragraphs", []) if isinstance(item.get("scope_paragraphs"), list) else []),
                    str(item.get("personal_scope_note", "")),
                ),
            )
            user_payload = {
                key: deepcopy(item.get(key))
                for key in _SPECIAL_USER_FIELDS
                if key in item
            }
            status = str(item.get("status", "unread"))
            connection.execute(
                """INSERT INTO app_special_issue_user_state(
                    issue_id,status,saved,ignored,is_read,user_json,updated_at
                ) VALUES (?,?,?,?,?,?,?) ON CONFLICT(issue_id) DO NOTHING""",
                (
                    issue_id, status, int(bool(item.get("saved"))), int(bool(item.get("ignored"))),
                    int(bool(item.get("is_read"))), _json_dump(user_payload), now,
                ),
            )
            connection.execute("DELETE FROM app_special_deadline_candidates WHERE issue_id=?", (issue_id,))
            connection.executemany(
                """INSERT INTO app_special_deadline_candidates(
                    issue_id,ordinal,deadline,source_key,first_seen_at,last_seen_at,evidence_json
                ) VALUES (?,?,?,?,?,?,?)""",
                [
                    (
                        issue_id, candidate_ordinal, candidate["deadline"], candidate["source_key"],
                        candidate.get("first_seen_at", ""), candidate.get("last_seen_at", ""),
                        _json_dump(candidate.get("source_evidence", [])),
                    )
                    for candidate_ordinal, candidate in enumerate(
                        compact_deadline_candidates(item.get("deadline_candidates", []), limit=10)
                    )
                ],
            )
            written += 1
        return written

    def special_issue_count(self) -> int:
        if not self.has_dataset("special_issues"):
            return 0
        with self._session() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM app_special_issues").fetchone()[0])

    def load_special_issues_for_refresh(self, keys: Iterable[str]) -> list[dict[str, Any]]:
        """Load only records that can collide with a newly discovered shard."""

        wanted = list(dict.fromkeys(str(value).strip() for value in keys if str(value).strip()))
        if not wanted or not self.has_dataset("special_issues"):
            return []
        result: list[dict[str, Any]] = []
        with self._session() as connection:
            for start in range(0, len(wanted), 400):
                batch = wanted[start : start + 400]
                placeholders = ",".join("?" for _ in batch)
                rows = list(
                    connection.execute(
                        f"""SELECT * FROM app_special_issues
                            WHERE issue_id IN ({placeholders}) OR dedupe_key IN ({placeholders})
                            ORDER BY ordinal""",
                        [*batch, *batch],
                    )
                )
                result.extend(self._reconstruct_special_rows(connection, rows))
        unique: dict[str, dict[str, Any]] = {}
        for item in result:
            unique[str(item.get("id", ""))] = item
        return list(unique.values())

    def begin_special_issue_refresh_incremental(self) -> dict[str, Any]:
        """Create a resumable generation without materializing the full library."""

        with self._session(write=True) as connection:
            meta = self._special_meta(connection)
            generation = _integer(meta.get("refresh_generation")) + 1
            at = _now()
            meta["refresh_generation"] = generation
            meta["active_refresh"] = {
                "generation": generation,
                "state": "running",
                "started_at": at,
                "updated_at": at,
            }
            self._put_payload(
                connection,
                "special_issues",
                "meta",
                {
                    key: value
                    for key, value in meta.items()
                    if key
                    not in {
                        "items",
                        "source_checkpoints",
                        "action_log",
                        "notification_log",
                        "reminder_log",
                        "notification_outbox",
                    }
                },
            )
            backfill_rows = list(
                connection.execute(
                    """SELECT * FROM app_special_issues
                       WHERE scoring_version LIKE 'special-issue-dual-axis-13%'
                         AND title<>''
                         AND COALESCE(json_extract(payload_json,'$.verification_status'),'')
                             NOT IN ('closed','expired','conflict')
                         AND (
                           json_extract(payload_json,'$.match.ai_axis_payload.axes.relevance.adjustment') IS NULL
                           OR json_extract(payload_json,'$.match.ai_axis_payload.axes.opportunity.adjustment') IS NULL
                         )
                       ORDER BY ordinal LIMIT 512"""
                )
            )
            meta["items"] = self._reconstruct_special_rows(connection, backfill_rows)
            meta["total_item_count"] = int(
                connection.execute("SELECT COUNT(*) FROM app_special_issues").fetchone()[0]
            )
            return meta

    def commit_special_issue_refresh_incremental(
        self,
        payload: dict[str, Any],
        *,
        generation: int,
        state: str,
    ) -> dict[str, Any]:
        """Upsert one refresh shard/checkpoint atomically and keep it resumable."""

        value = deepcopy(payload) if isinstance(payload, dict) else {}
        items = [item for item in value.pop("items", []) if isinstance(item, dict)]
        source_checkpoints = value.pop("source_checkpoints", {})
        logs = {
            key: value.pop(key, [])
            for key in ("action_log", "notification_log", "reminder_log", "notification_outbox")
        }
        with self._session(write=True) as connection:
            current = self._get_payload(connection, "special_issues", "meta", {})
            active = current.get("active_refresh", {}) if isinstance(current, dict) else {}
            if _integer(active.get("generation")) != int(generation):
                raise RuntimeError("特刊刷新检查点已被更新的任务替代")
            self._upsert_special_rows(connection, items)
            if isinstance(source_checkpoints, dict):
                connection.executemany(
                    """INSERT INTO app_source_runs(namespace,source_id,state,updated_at,payload_json)
                       VALUES (?,?,?,?,?) ON CONFLICT(namespace,source_id) DO UPDATE SET
                         state=excluded.state,updated_at=excluded.updated_at,payload_json=excluded.payload_json""",
                    [
                        (
                            "special_issues", str(source_id),
                            str(record.get("status", record.get("state", ""))) if isinstance(record, dict) else "",
                            str(record.get("updated_at", record.get("last_attempt_at", _now()))) if isinstance(record, dict) else _now(),
                            _json_dump(record),
                        )
                        for source_id, record in source_checkpoints.items()
                    ],
                )
            for kind, entries in logs.items():
                connection.execute(
                    "DELETE FROM app_user_events WHERE namespace=?",
                    (f"special_issues:{kind}",),
                )
                prepared = []
                for ordinal, entry in enumerate(entries if isinstance(entries, list) else []):
                    if not isinstance(entry, dict):
                        continue
                    prepared.append(
                        (
                            f"special_issues:{kind}", str(entry.get("id") or f"{kind}-{ordinal}"),
                            ordinal, str(entry.get("issue_id", "")),
                            str(entry.get("kind", entry.get("action", ""))),
                            str(entry.get("at", entry.get("notified_at", entry.get("sent_at", "")))),
                            _json_dump(entry),
                        )
                    )
                connection.executemany(
                    """INSERT INTO app_user_events(
                        namespace,event_id,ordinal,item_id,event_type,event_at,payload_json
                    ) VALUES (?,?,?,?,?,?,?)""",
                    prepared,
                )
            at = _now()
            value["refresh_generation"] = int(generation)
            value["active_refresh"] = {
                **(active if isinstance(active, dict) else {}),
                "generation": int(generation),
                "state": str(state),
                "updated_at": at,
            }
            self._put_payload(connection, "special_issues", "meta", value)
            count = int(connection.execute("SELECT COUNT(*) FROM app_special_issues").fetchone()[0])
            self._mark_dataset(connection, "special_issues", count)
            result = deepcopy(value)
            result["items"] = items
            result["source_checkpoints"] = deepcopy(source_checkpoints)
            result.update(logs)
            result["total_item_count"] = count
            return result

    def _special_meta(self, connection: sqlite3.Connection) -> dict[str, Any]:
        result = self._get_payload(connection, "special_issues", "meta", {})
        checkpoints = {}
        for row in connection.execute(
            "SELECT source_id,payload_json FROM app_source_runs WHERE namespace='special_issues'"
        ):
            checkpoints[str(row["source_id"])] = _json_load(row["payload_json"], {})
        result["source_checkpoints"] = checkpoints
        for kind in ("action_log", "notification_log", "reminder_log", "notification_outbox"):
            result[kind] = [
                _json_load(row[0], {})
                for row in connection.execute(
                    "SELECT payload_json FROM app_user_events WHERE namespace=? ORDER BY ordinal",
                    (f"special_issues:{kind}",),
                )
            ]
        return result

    def load_special_issues(self) -> dict[str, Any] | None:
        if not self.has_dataset("special_issues"):
            return None
        with self._session() as connection:
            result = self._special_meta(connection)
            rows = list(connection.execute("SELECT * FROM app_special_issues ORDER BY ordinal"))
            result["items"] = self._reconstruct_special_rows(connection, rows)
            return result

    def load_special_overview(self, *, legacy_threshold: int = 48) -> dict[str, Any] | None:
        if not self.has_dataset("special_issues"):
            return None
        with self._session() as connection:
            result = self._special_meta(connection)
            rows = list(
                connection.execute(
                    """SELECT si.* FROM app_special_issues si
                       JOIN app_special_issue_user_state us ON us.issue_id=si.issue_id
                       WHERE us.saved=1 OR us.ignored=1 OR si.has_history=1 OR si.candidate_state='visible'
                          OR (si.scoring_version NOT LIKE 'special-issue-dual-axis-13%'
                              AND si.scope_complete=1 AND si.content_qualified=1 AND si.match_score>=?)
                       ORDER BY si.ordinal""",
                    (int(legacy_threshold),),
                )
            )
            result["items"] = self._reconstruct_special_rows(connection, rows)
            total = connection.execute("SELECT COUNT(*) FROM app_special_issues").fetchone()[0]
            revision_row = connection.execute(
                "SELECT revision,updated_at FROM app_datasets WHERE name='special_issues'"
            ).fetchone()
            result["total_item_count"] = int(total)
            result["source_signature"] = {
                "revision": int(revision_row["revision"]) if revision_row else 0,
                "item_count": int(total),
            }
            result["summary_version"] = 2
            return result

    def get_special_issue(self, issue_id: str) -> dict[str, Any] | None:
        wanted = str(issue_id).strip()
        if not wanted or not self.has_dataset("special_issues"):
            return None
        with self._session() as connection:
            rows = list(connection.execute("SELECT * FROM app_special_issues WHERE issue_id=?", (wanted,)))
            values = self._reconstruct_special_rows(connection, rows)
            return values[0] if values else None

    def special_issue_aliases(self) -> dict[str, str]:
        if not self.has_dataset("special_issues"):
            return {}
        with self._session() as connection:
            meta = self._get_payload(connection, "special_issues", "meta", {})
            aliases = meta.get("id_aliases", {}) if isinstance(meta, dict) else {}
            return {str(key): str(value) for key, value in aliases.items()} if isinstance(aliases, dict) else {}

    def special_issue_cache_keys(self) -> set[str]:
        """Return identities that may still have a rebuildable discovery row."""

        if not self.has_dataset("special_issues"):
            return set()
        keys: set[str] = set()
        with self._session() as connection:
            for row in connection.execute("SELECT issue_id,payload_json FROM app_special_issues"):
                issue_id = str(row["issue_id"] or "").strip()
                if issue_id:
                    keys.add(issue_id)
                payload = _json_load(row["payload_json"], {})
                if not isinstance(payload, dict):
                    continue
                for field in ("id", "dedupe_key"):
                    value = str(payload.get(field, "")).strip()
                    if value:
                        keys.add(value)
        return keys

    def update_special_user_state(self, issue_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Update one user's decision without reading or rewriting discovery rows."""

        wanted = str(issue_id).strip()
        with self._session(write=True) as connection:
            row = connection.execute(
                "SELECT user_json FROM app_special_issue_user_state WHERE issue_id=?", (wanted,)
            ).fetchone()
            if row is None:
                raise KeyError(wanted)
            payload = _json_load(row["user_json"], {})
            payload.update(deepcopy(changes))
            saved = bool(payload.get("saved", False))
            ignored = bool(payload.get("ignored", False))
            is_read = bool(payload.get("is_read", False) or payload.get("read_at"))
            status = "ignored" if ignored else "saved" if saved else "read" if is_read else "unread"
            payload["saved"] = saved
            payload["ignored"] = ignored
            payload["is_read"] = is_read
            payload["status"] = status
            connection.execute(
                """UPDATE app_special_issue_user_state
                   SET status=?,saved=?,ignored=?,is_read=?,user_json=?,updated_at=? WHERE issue_id=?""",
                (status, int(saved), int(ignored), int(is_read), _json_dump(payload), _now(), wanted),
            )
            self._touch_special_dataset(connection)
        return payload

    def update_special_scope_note(self, issue_id: str, note: str) -> None:
        wanted = str(issue_id).strip()
        with self._session(write=True) as connection:
            cursor = connection.execute(
                "UPDATE app_special_issue_scopes SET personal_scope_note=? WHERE issue_id=?",
                (str(note or "").strip()[:12000], wanted),
            )
            if cursor.rowcount != 1:
                raise KeyError(wanted)
            self._touch_special_dataset(connection)

    def save_runtime(self, payload: dict[str, Any]) -> None:
        value = deepcopy(payload) if isinstance(payload, dict) else {}
        batches = value.pop("batches", {})
        batches = batches if isinstance(batches, dict) else {}
        rows = []
        for batch_id, batch in batches.items():
            if not isinstance(batch, dict):
                continue
            rows.append(
                (
                    str(batch_id), str(batch.get("task", "")), str(batch.get("day", "")),
                    str(batch.get("state", "")), str(batch.get("updated_at", "")), _json_dump(batch),
                )
            )
        with self._session(write=True) as connection:
            existing = {str(row["batch_id"]): _json_load(row["payload_json"], {}) for row in
                        connection.execute("SELECT batch_id,payload_json FROM app_task_runs")}
            def rank(batch):
                stages = batch.get("stages", {})
                return (str(batch.get("updated_at", "")),
                    sum(int(entry.get("attempts", 0) or 0) for entry in stages.values()),
                    sum(entry.get("state") == "success" for entry in stages.values()),
                    {"running": 0, "failed": 1, "paused": 2, "partial": 3, "success": 4}.get(batch.get("state"), 0))
            for batch_id, batch in batches.items():
                if batch_id not in existing or rank(batch) >= rank(existing[batch_id]):
                    existing[batch_id] = batch
            retained = sorted(existing.items(), key=lambda pair: rank(pair[1]), reverse=True)[:40]
            rows = [(str(batch_id), str(batch.get("task", "")), str(batch.get("day", "")),
                     str(batch.get("state", "")), str(batch.get("updated_at", "")), _json_dump(batch))
                    for batch_id, batch in retained]
            prior_meta = self._get_payload(connection, "runtime", "meta", {})
            task_state = prior_meta.get("task_state", {})
            for task, state in value.get("task_state", {}).items():
                previous = task_state.get(task, {})
                if (str(state.get("updated_at", "")), state.get("state") == "success") >= (
                    str(previous.get("updated_at", "")), previous.get("state") == "success"):
                    task_state[task] = state
            value["task_state"] = task_state
            self._sync_rows(connection, "app_task_runs", "batch_id,task,day,state,updated_at,payload_json", rows)
            self._put_payload(connection, "runtime", "meta", value)
            self._mark_dataset(connection, "runtime", len(rows))

    @staticmethod
    def _sync_rows(connection, table, columns, rows):
        """Update changed rows and delete explicit removals without rebuilding a table."""
        names = columns.split(",")
        key = names[0]
        assignments = ",".join(f"{name}=excluded.{name}" for name in names[1:])
        condition = f"excluded.payload_json<>{table}.payload_json"
        if "ordinal" in names:
            condition += f" OR excluded.ordinal<>{table}.ordinal"
        connection.executemany(
            f"INSERT INTO {table} ({columns}) VALUES ({','.join('?' for _ in names)}) "
            f"ON CONFLICT({key}) DO UPDATE SET {assignments} WHERE {condition}", rows)
        connection.execute("CREATE TEMP TABLE IF NOT EXISTS sync_record_ids (id TEXT PRIMARY KEY)")
        connection.execute("DELETE FROM sync_record_ids")
        connection.executemany("INSERT OR IGNORE INTO sync_record_ids VALUES (?)", [(row[0],) for row in rows])
        connection.execute(f"DELETE FROM {table} WHERE {key} NOT IN (SELECT id FROM sync_record_ids)")

    def load_runtime(self) -> dict[str, Any] | None:
        if not self.has_dataset("runtime"):
            return None
        with self._session() as connection:
            result = self._get_payload(connection, "runtime", "meta", {})
            result["batches"] = {
                str(row["batch_id"]): _json_load(row["payload_json"], {})
                for row in connection.execute("SELECT batch_id,payload_json FROM app_task_runs")
            }
            return result

    def dataset_counts(self) -> dict[str, int]:
        with self._session() as connection:
            return {
                str(row["name"]): int(row["item_count"])
                for row in connection.execute("SELECT name,item_count FROM app_datasets ORDER BY name")
            }
