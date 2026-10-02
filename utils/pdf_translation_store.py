"""Small, independent durable queue; no credentials in job snapshots."""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3
import time
import uuid
from contextlib import contextmanager


class TranslationStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "jobs.sqlite"
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, created REAL NOT NULL, updated REAL NOT NULL,
                status TEXT NOT NULL, params TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0,
                result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '')""")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status,created)")
            db.execute("UPDATE jobs SET status='interrupted', updated=? WHERE status IN ('running','cancelling')", (time.time(),))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def settings(self):
        with self.connect() as db:
            row = db.execute("SELECT payload FROM settings WHERE id=1").fetchone()
            return json.loads(row[0]) if row else {}

    def save_settings(self, values):
        values = {key: value for key, value in values.items() if key != "api_key"}
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (1,?)", (json.dumps(values, ensure_ascii=False),))

    def enqueue(self, values):
        values = {key: value for key, value in values.items() if key not in ("api_key", "api_key_secret")}
        job_id = uuid.uuid4().hex
        values["output"] = str(self.root / "results" / job_id)
        now = time.time()
        with self.connect() as db:
            db.execute("INSERT INTO jobs(id,created,updated,status,params) VALUES (?,?,?,'queued',?)", (job_id, now, now, json.dumps(values, ensure_ascii=False)))
        return job_id

    def update(self, job_id, **fields):
        allowed = {"status", "progress", "result", "error"}
        if not fields or not set(fields).issubset(allowed):
            raise ValueError("Invalid queue update")
        if "result" in fields:
            fields["result"] = json.dumps(fields["result"], ensure_ascii=False)
        fields["updated"] = time.time()
        with self.connect() as db:
            db.execute("UPDATE jobs SET " + ",".join(key + "=?" for key in fields) + " WHERE id=?", (*fields.values(), job_id))

    def rows(self, limit=100):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT ?", (limit,))]

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def next(self):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            return dict(row) if row else None

    def rebase_paths(self, previous_root):
        previous = str(Path(previous_root).resolve())
        current = str(self.root.resolve())
        def rebase(value):
            if isinstance(value, str) and value.casefold().startswith(previous.casefold() + "\\"):
                return current + value[len(previous):]
            if isinstance(value, str) and value.casefold().startswith(previous.casefold() + "/"):
                return current + value[len(previous):]
            if isinstance(value, dict):
                return {key: rebase(child) for key, child in value.items()}
            if isinstance(value, list):
                return [rebase(child) for child in value]
            return value
        with self.connect() as db:
            for row in db.execute("SELECT id,params,result FROM jobs").fetchall():
                db.execute("UPDATE jobs SET params=?,result=? WHERE id=?", (
                    json.dumps(rebase(json.loads(row["params"])), ensure_ascii=False),
                    json.dumps(rebase(json.loads(row["result"])), ensure_ascii=False), row["id"],
                ))
