"""Crash-resumable six-stage job journal used by v13 discovery tasks."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from utils.action_transaction import apply_json_transaction


STAGES = ("recall", "enrich", "deduplicate", "filter", "score", "commit")
TERMINAL_STATES = {"success", "partial", "aborted"}


def _signature(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_runtime_store(raw: Any) -> dict[str, Any]:
    value = raw if isinstance(raw, dict) else {}
    batches = value.get("batches", {}) if isinstance(value.get("batches"), dict) else {}
    normalized: dict[str, dict[str, Any]] = {}
    for batch_id, batch in batches.items():
        if not isinstance(batch, dict):
            continue
        stages = batch.get("stages", {}) if isinstance(batch.get("stages"), dict) else {}
        # Copy stage payloads exactly once.  The previous ``deepcopy(batch)``
        # also copied ``stages`` before replacing it below, briefly doubling
        # the memory required to open a large recovery journal.
        batch_fields = {
            str(key): deepcopy(child)
            for key, child in batch.items()
            if str(key) != "stages"
        }
        normalized_stages: dict[str, dict[str, Any]] = {}
        for stage in STAGES:
            source_entry = stages.get(stage, {"state": "pending", "attempts": 0})
            source_entry = source_entry if isinstance(source_entry, dict) else {"state": "pending", "attempts": 0}
            # Stage payloads can contain thousands of complete papers.  The
            # journal never mutates an existing payload in place, so sharing
            # that value while copying the small checkpoint envelope avoids a
            # full extra in-memory copy on every status query.
            normalized_stages[stage] = {
                str(key): (child if str(key) == "payload" else deepcopy(child))
                for key, child in source_entry.items()
            }
        normalized[str(batch_id)] = {
            **batch_fields,
            "id": str(batch.get("id", batch_id)),
            "task": str(batch.get("task", "")),
            "day": str(batch.get("day", "")),
            "input_signature": str(batch.get("input_signature", "")),
            "state": str(batch.get("state", "running")),
            "stages": normalized_stages,
        }
    return {
        "version": 1,
        "batches": normalized,
        "task_state": deepcopy(value.get("task_state", {})) if isinstance(value.get("task_state"), dict) else {},
    }


def _prune_batch_payloads(batch: dict[str, Any]) -> None:
    """Keep only the one payload required to resume the next stage.

    Completed batches need no stage payload: their durable result already
    lives in the owning data file.  An unfinished batch only needs the output
    of the stage immediately before the first incomplete stage.  Keeping all
    earlier copies made a 47 MB frontier file grow into a 350 MB journal.
    """

    stages = batch.get("stages", {}) if isinstance(batch.get("stages"), dict) else {}
    keep_stage = ""
    if str(batch.get("state", "running")) not in TERMINAL_STATES:
        first_incomplete = next(
            (
                index
                for index, stage in enumerate(STAGES)
                if str(stages.get(stage, {}).get("state", "pending")) != "success"
            ),
            len(STAGES),
        )
        if first_incomplete > 0:
            keep_stage = STAGES[min(first_incomplete - 1, len(STAGES) - 2)]
    for stage in STAGES:
        entry = stages.get(stage)
        if not isinstance(entry, dict) or stage == keep_stage:
            continue
        entry.pop("payload", None)
        entry.pop("payload_signature", None)


def _drop_all_payloads(batch: dict[str, Any]) -> None:
    stages = batch.get("stages", {}) if isinstance(batch.get("stages"), dict) else {}
    for entry in stages.values():
        if isinstance(entry, dict):
            entry.pop("payload", None)
            entry.pop("payload_signature", None)


def _prune_superseded_payloads(batches: dict[str, dict[str, Any]]) -> None:
    """Retain resumable data only for the newest relevant attempt.

    A past-day batch cannot be selected by ``begin_batch`` for today's run.
    For the newest day, only the latest unfinished batch with the same input
    signature can be resumed.  Source failure metadata remains in every batch
    and in ``task_state``; only bulky intermediate candidate snapshots go.
    """

    newest_day: dict[str, str] = {}
    for batch in batches.values():
        task = str(batch.get("task", ""))
        day = str(batch.get("day", ""))
        if day > newest_day.get(task, ""):
            newest_day[task] = day
    resumable: dict[tuple[str, str, str], dict[str, Any]] = {}
    for batch in batches.values():
        task = str(batch.get("task", ""))
        day = str(batch.get("day", ""))
        if day != newest_day.get(task, "") or str(batch.get("state", "running")) in TERMINAL_STATES:
            continue
        key = (task, day, str(batch.get("input_signature", "")))
        previous = resumable.get(key)
        if previous is None or (
            str(batch.get("updated_at", "")), str(batch.get("created_at", "")), str(batch.get("id", ""))
        ) > (
            str(previous.get("updated_at", "")), str(previous.get("created_at", "")), str(previous.get("id", ""))
        ):
            resumable[key] = batch
    keep_ids = {str(batch.get("id", "")) for batch in resumable.values()}
    for batch in batches.values():
        if str(batch.get("id", "")) not in keep_ids:
            _drop_all_payloads(batch)


def compact_runtime_store(raw: Any) -> dict[str, Any]:
    """Return a crash-resumable journal without redundant stage snapshots."""

    result = normalize_runtime_store(raw)
    for batch in result["batches"].values():
        _prune_batch_payloads(batch)
    _prune_superseded_payloads(result["batches"])
    return result


def begin_batch(
    store: Any,
    *,
    task: str,
    inputs: Any,
    now: datetime | None = None,
    manual: bool = False,
) -> tuple[dict[str, Any], str, bool]:
    """Create or resume an idempotent batch for the current effective day."""

    now = now or datetime.now()
    result = compact_runtime_store(store)
    day = now.date().isoformat()
    signature = _signature(inputs)
    candidates = [
        batch
        for batch in result["batches"].values()
        if batch.get("task") == task
        and batch.get("day") == day
        and batch.get("input_signature") == signature
        and batch.get("state") not in TERMINAL_STATES
    ]
    if candidates:
        candidates.sort(key=lambda value: str(value.get("created_at", "")), reverse=True)
        return result, str(candidates[0]["id"]), True
    batch_id = f"{task}-{day}-{uuid4().hex[:12]}"
    result["batches"][batch_id] = {
        "id": batch_id,
        "task": task,
        "day": day,
        "input_signature": signature,
        "manual": bool(manual),
        "state": "running",
        "created_at": now.isoformat(timespec="seconds"),
        "updated_at": now.isoformat(timespec="seconds"),
        "stages": {stage: {"state": "pending", "attempts": 0} for stage in STAGES},
        "failed_sources": [],
        "successful_sources": [],
    }
    return result, batch_id, False


def batch_should_run(store: Any, *, task: str, day: date, manual: bool = False) -> bool:
    if manual:
        return True
    value = normalize_runtime_store(store)
    task_state = value["task_state"].get(task, {})
    return not (
        isinstance(task_state, dict)
        and str(task_state.get("last_success_day", "")) == day.isoformat()
        and str(task_state.get("state", "")) == "success"
    )


def checkpoint(
    store: Any,
    batch_id: str,
    stage: str,
    *,
    payload: Any = None,
    state: str = "success",
    error: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    if stage not in STAGES:
        raise ValueError(f"未知检查点: {stage}")
    if state not in {"running", "success", "partial", "failed", "cancelled"}:
        raise ValueError(f"未知检查点状态: {state}")
    result = normalize_runtime_store(store)
    if batch_id not in result["batches"]:
        raise KeyError(batch_id)
    now = now or datetime.now()
    batch = result["batches"][batch_id]
    entry = batch["stages"][stage]
    entry["attempts"] = int(entry.get("attempts", 0) or 0) + (1 if state == "running" else 0)
    entry["state"] = state
    entry["updated_at"] = now.isoformat(timespec="seconds")
    entry["error"] = str(error)[:500] if state == "failed" else ""
    if payload is not None:
        entry["payload"] = deepcopy(payload)
        entry["payload_signature"] = _signature(payload)
    batch["updated_at"] = now.isoformat(timespec="seconds")
    if state == "failed":
        batch["state"] = "failed"
        batch["last_error_stage"] = stage
    elif state == "cancelled":
        batch["state"] = "paused"
    elif stage == "commit" and state in {"success", "partial"}:
        batch["state"] = state
        task = str(batch.get("task", ""))
        pending_sources = [] if state == "success" else sorted(
            {
                str(value)
                for value in batch.get("failed_sources", [])
                if str(value)
            }
        )
        result["task_state"][task] = {
            "state": state,
            "last_success_day": str(batch.get("day", "")) if state == "success" else "",
            "last_attempt_day": str(batch.get("day", "")),
            "last_batch_id": batch_id,
            "failed_sources": pending_sources,
            "updated_at": now.isoformat(timespec="seconds"),
        }
    else:
        batch["state"] = "running"
    _prune_batch_payloads(batch)
    return result


def resume_stage(store: Any, batch_id: str) -> str:
    value = normalize_runtime_store(store)
    batch = value["batches"].get(batch_id)
    if not isinstance(batch, dict):
        raise KeyError(batch_id)
    for stage in STAGES:
        if str(batch["stages"][stage].get("state", "pending")) != "success":
            return stage
    return "commit"


def stage_payload(store: Any, batch_id: str, stage: str) -> Any:
    value = normalize_runtime_store(store)
    return deepcopy(value["batches"][batch_id]["stages"][stage].get("payload"))


def record_batch_sources(
    store: Any,
    batch_id: str,
    *,
    successful: list[str],
    failed: list[str],
) -> dict[str, Any]:
    result = normalize_runtime_store(store)
    batch = result["batches"][batch_id]
    batch["successful_sources"] = sorted(set(str(value) for value in successful if str(value)))
    batch["failed_sources"] = sorted(set(str(value) for value in failed if str(value)))
    task = str(batch.get("task", ""))
    task_state = deepcopy(result["task_state"].get(task, {})) if task else {}
    pending = {
        str(value)
        for value in task_state.get("failed_sources", [])
        if str(value)
    }
    pending.difference_update(batch["successful_sources"])
    pending.update(batch["failed_sources"])
    if task:
        task_state["failed_sources"] = sorted(pending)
        task_state["last_batch_id"] = batch_id
        task_state["last_attempt_day"] = str(batch.get("day", ""))
        result["task_state"][task] = task_state
    return result


def retry_sources(store: Any, *, task: str, day: date) -> list[str] | None:
    """Return only failed sources, or ``None`` when a full run is needed."""

    value = normalize_runtime_store(store)
    task_state = value["task_state"].get(task, {})
    if isinstance(task_state, dict):
        failed = task_state.get("failed_sources", [])
        if (
            str(task_state.get("last_attempt_day", "")) == day.isoformat()
            and isinstance(failed, list)
            and failed
        ):
            return sorted({str(source) for source in failed if str(source)})
    batches = [
        batch for batch in value["batches"].values()
        if batch.get("task") == task and batch.get("day") == day.isoformat()
    ]
    if not batches:
        return None
    batches.sort(key=lambda batch: str(batch.get("updated_at", "")), reverse=True)
    latest = batches[0]
    if latest.get("state") == "success":
        return []
    failed = latest.get("failed_sources", [])
    return [str(value) for value in failed] if isinstance(failed, list) and failed else None


def commit_transaction(
    *,
    result_path: Path,
    result_payload: Any,
    runtime_path: Path,
    runtime_store: Any,
    batch_id: str,
    fingerprint_path: Path | None = None,
    fingerprint_payload: Any = None,
) -> dict[str, Any]:
    """Commit visible results, fingerprints and success state atomically."""

    finalized = checkpoint(runtime_store, batch_id, "commit", state="success")
    changes: dict[Path, Any] = {
        Path(result_path): result_payload,
        Path(runtime_path): finalized,
    }
    if fingerprint_path is not None:
        changes[Path(fingerprint_path)] = fingerprint_payload if fingerprint_payload is not None else []
    apply_json_transaction(changes, action_key=batch_id)
    return finalized


def trim_batches(store: Any, *, keep: int = 40) -> dict[str, Any]:
    result = compact_runtime_store(store)
    ordered = sorted(result["batches"].values(), key=lambda value: str(value.get("updated_at", "")), reverse=True)
    result["batches"] = {str(value["id"]): value for value in ordered[: max(5, int(keep))]}
    return result
