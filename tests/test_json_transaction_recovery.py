"""Recoverable multi-file commit, including interrupted-process recovery."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from utils import action_transaction as transaction


def test_interrupted_commit_recovers_exact_original_bytes(tmp_path):
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    first.write_bytes(b'{ "old": 1 }\n')
    second.write_bytes(b'{"old":2}\n')
    originals = {p: p.read_bytes() for p in (first, second)}
    real = transaction.os.replace

    def interrupt(source, target):
        if Path(target) == second:
            raise KeyboardInterrupt("process interrupted")
        return real(source, target)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(transaction.os, "replace", interrupt)
        with pytest.raises(KeyboardInterrupt):
            transaction.apply_json_transaction({first: {"new": 1}, second: {"new": 2}})
    assert first.read_bytes() != originals[first]
    recovered = transaction.recover_json_transactions(tmp_path)
    assert recovered
    assert {p: p.read_bytes() for p in (first, second)} == originals
    assert transaction.recover_json_transactions(tmp_path) == []


def test_rollback_failure_preserves_backups_and_reports_recovery_required(tmp_path, monkeypatch):
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    first.write_text('{"old":1}', encoding="utf-8")
    second.write_text('{"old":2}', encoding="utf-8")
    real = transaction.os.replace
    touched = False

    def blocked(source, target):
        nonlocal touched
        if Path(target) == second or (Path(target) == first and touched):
            raise OSError("storage unavailable")
        result = real(source, target)
        if Path(target) == first:
            touched = True
        return result

    monkeypatch.setattr(transaction.os, "replace", blocked)
    with pytest.raises(transaction.JsonTransactionError) as caught:
        transaction.apply_json_transaction({first: {"new": 1}, second: {"new": 2}})
    assert caught.value.recovery_required is True
    assert list(tmp_path.rglob("*.bak"))
    monkeypatch.setattr(transaction.os, "replace", real)
    transaction.recover_json_transactions(tmp_path)
    assert json.loads(first.read_text()) == {"old": 1}


def test_recovery_refuses_to_overwrite_unrelated_later_edits(tmp_path, monkeypatch):
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    first.write_text('{"old":1}', encoding="utf-8")
    second.write_text('{"old":2}', encoding="utf-8")
    real = transaction.os.replace

    def interrupt(source, target):
        if Path(target) == second:
            raise KeyboardInterrupt()
        return real(source, target)

    monkeypatch.setattr(transaction.os, "replace", interrupt)
    with pytest.raises(KeyboardInterrupt):
        transaction.apply_json_transaction({first: {"new": 1}, second: {"new": 2}})
    monkeypatch.setattr(transaction.os, "replace", real)
    first.write_text('{"later_user_edit":true}', encoding="utf-8")
    with pytest.raises(transaction.JsonTransactionError) as caught:
        transaction.recover_json_transactions(tmp_path)
    assert caught.value.recovery_required is True
    assert json.loads(first.read_text()) == {"later_user_edit": True}
    assert list(tmp_path.rglob("*.bak"))


def test_separate_processes_serialize_the_entire_read_modify_write(tmp_path):
    target = tmp_path / "count.json"
    target.write_text("0", encoding="utf-8")
    script = '''
import json, sys, time
from pathlib import Path
from utils.action_transaction import json_write_lock, apply_json_transaction
path = Path(sys.argv[1])
for _ in range(8):
    with json_write_lock(path.parent):
        value = json.loads(path.read_text())
        time.sleep(0.01)
        apply_json_transaction({path: value + 1})
'''
    children = [subprocess.Popen([sys.executable, "-c", script, str(target)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ.copy()) for _ in range(3)]
    outputs = [child.communicate(timeout=60) for child in children]
    assert [child.returncode for child in children] == [0, 0, 0], outputs
    assert json.loads(target.read_text()) == 24
