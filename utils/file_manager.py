"""Small JSON-only persistence layer.

Development runs keep data in the project's data/ directory. Packaged v12
builds bind personal data to a stable LocalAppData UserData directory before
this module is imported, so application upgrades cannot replace user records.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from utils.journal_catalog import default_land_science_catalog
from utils.publisher_utils import canonical_publisher
from utils.app_info import APP_NAME
from utils.research_profile_service import normalize_research_profile_v11

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# These are the portable records that distinguish a real personal store from
# the empty ``data`` folder bundled by a smoke-test or a fresh installer.
_DATA_CANDIDATE_FILES = (
    "todo.json",
    "papers.json",
    "journals.json",
    "achievements.json",
    "frontier.json",
    "special_issues.json",
    "readings.json",
    "inspiration.json",
    "rejection_archive.json",
)


def _app_root() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else PROJECT_ROOT


APP_ROOT = _app_root()


def _storage_config_paths() -> list[Path]:
    paths: list[Path] = []
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        paths.append(Path(local_app_data) / APP_NAME / "storage_location.json")
    legacy = APP_ROOT / "storage_location.json"
    if legacy not in paths:
        paths.append(legacy)
    return paths


def _stored_data_dir() -> Path | None:
    for path in _storage_config_paths():
        try:
            with path.open("r", encoding="utf-8") as handle:
                value = json.load(handle)
            configured = str(value.get("data_directory", "")).strip() if isinstance(value, dict) else ""
            if configured:
                return Path(configured).expanduser()
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _data_directory_score(directory: Path) -> tuple[int, int]:
    """Return a small, read-only richness score for a candidate data root."""
    record_count = 0
    byte_count = 0
    if not directory.is_dir():
        return (0, 0)
    for filename in _DATA_CANDIDATE_FILES:
        path = directory / filename
        try:
            if not path.is_file():
                continue
            byte_count += path.stat().st_size
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                record_count += len(payload)
            elif isinstance(payload, dict):
                for key in ("tasks", "items", "records", "data"):
                    value = payload.get(key)
                    if isinstance(value, list):
                        record_count += len(value)
                        break
                else:
                    # A non-empty object still represents preferences or a
                    # legacy record set, but it should rank below real lists.
                    record_count += int(bool(payload))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
    return (record_count, byte_count)


def _choose_frozen_data_dir(app_root: Path, local_app_data: Path | None = None) -> Path:
    """Choose the personal store when a frozen bundle is launched elsewhere.

    Inno Setup deliberately excludes ``data`` from upgrades.  A stale desktop
    shortcut or a test-installed copy can therefore launch beside a fresh or
    lightly populated folder while the previous formal installation still
    contains the user's records.  Prefer the richest known local store unless
    the user explicitly selected a location through the normal setting.
    """
    bundle_data = Path(app_root).resolve() / "data"
    local_root = Path(local_app_data).expanduser().resolve() if local_app_data else None
    candidates = [bundle_data]
    if local_root is not None:
        candidates.extend(
            [
                local_root / "Programs" / APP_NAME / "data",
                local_root / APP_NAME / "data",
            ]
        )
    existing = [candidate for candidate in candidates if candidate.is_dir()]
    if not existing:
        return bundle_data
    return max(existing, key=_data_directory_score)


def _data_dir() -> Path:
    configured = os.environ.get("RESEARCH_ASSISTANT_DATA_DIR")
    if configured:
        return Path(configured).expanduser()

    stored = _stored_data_dir()
    if stored is not None:
        return stored

    if getattr(sys, "frozen", False):
        from utils.storage_bootstrap import prepare_v12_data_root

        return prepare_v12_data_root(
            frozen=True,
            app_root=APP_ROOT,
            local_app_data=Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else None,
        )
    return PROJECT_ROOT / "data"


DATA_DIR = _data_dir()
TODO_FILE = DATA_DIR / "todo.json"
TODO_META_FILE = DATA_DIR / "todo_meta.json"
PAPERS_FILE = DATA_DIR / "papers.json"
ACHIEVEMENTS_FILE = DATA_DIR / "achievements.json"
REJECTION_ARCHIVE_FILE = DATA_DIR / "rejection_archive.json"
SELECTION_FEEDBACK_FILE = DATA_DIR / "selection_feedback.json"
INSPIRATION_FILE = DATA_DIR / "inspiration.json"
REMINDER_FILE = DATA_DIR / "reminders.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
READINGS_FILE = DATA_DIR / "readings.json"
JOURNALS_FILE = DATA_DIR / "journals.json"
FRONTIER_FILE = DATA_DIR / "frontier.json"
SPECIAL_ISSUES_FILE = DATA_DIR / "special_issues.json"
PDF_RESEARCH_CACHE_FILE = DATA_DIR / "achievement_pdf_cache.json"
RESEARCH_PROFILE_FILE = DATA_DIR / "research_profile.json"
RESEARCH_INTELLIGENCE_CACHE_FILE = DATA_DIR / "research_intelligence.sqlite"
BACKUP_DIR = DATA_DIR / "backups"
BACKUP_FILE_NAMES = (
    TODO_FILE.name,
    TODO_META_FILE.name,
    PAPERS_FILE.name,
    ACHIEVEMENTS_FILE.name,
    REJECTION_ARCHIVE_FILE.name,
    SELECTION_FEEDBACK_FILE.name,
    INSPIRATION_FILE.name,
    REMINDER_FILE.name,
    READINGS_FILE.name,
    JOURNALS_FILE.name,
    FRONTIER_FILE.name,
    SPECIAL_ISSUES_FILE.name,
    PDF_RESEARCH_CACHE_FILE.name,
    RESEARCH_PROFILE_FILE.name,
)


def _set_data_dir(directory: Path) -> None:
    """Update all active JSON paths after a successful local storage switch."""
    global DATA_DIR, TODO_FILE, TODO_META_FILE, PAPERS_FILE, ACHIEVEMENTS_FILE, REJECTION_ARCHIVE_FILE
    global SELECTION_FEEDBACK_FILE, INSPIRATION_FILE
    global REMINDER_FILE, SETTINGS_FILE, READINGS_FILE, JOURNALS_FILE, FRONTIER_FILE, SPECIAL_ISSUES_FILE, PDF_RESEARCH_CACHE_FILE
    global RESEARCH_PROFILE_FILE, RESEARCH_INTELLIGENCE_CACHE_FILE, BACKUP_DIR
    DATA_DIR = directory
    TODO_FILE = DATA_DIR / "todo.json"
    TODO_META_FILE = DATA_DIR / "todo_meta.json"
    PAPERS_FILE = DATA_DIR / "papers.json"
    ACHIEVEMENTS_FILE = DATA_DIR / "achievements.json"
    REJECTION_ARCHIVE_FILE = DATA_DIR / "rejection_archive.json"
    SELECTION_FEEDBACK_FILE = DATA_DIR / "selection_feedback.json"
    INSPIRATION_FILE = DATA_DIR / "inspiration.json"
    REMINDER_FILE = DATA_DIR / "reminders.json"
    SETTINGS_FILE = DATA_DIR / "settings.json"
    READINGS_FILE = DATA_DIR / "readings.json"
    JOURNALS_FILE = DATA_DIR / "journals.json"
    FRONTIER_FILE = DATA_DIR / "frontier.json"
    SPECIAL_ISSUES_FILE = DATA_DIR / "special_issues.json"
    PDF_RESEARCH_CACHE_FILE = DATA_DIR / "achievement_pdf_cache.json"
    RESEARCH_PROFILE_FILE = DATA_DIR / "research_profile.json"
    RESEARCH_INTELLIGENCE_CACHE_FILE = DATA_DIR / "research_intelligence.sqlite"
    BACKUP_DIR = DATA_DIR / "backups"


def _save_storage_location(directory: Path) -> None:
    payload = {"data_directory": str(directory), "updated_at": datetime.now().isoformat(timespec="seconds")}
    errors: list[OSError] = []
    for path in _storage_config_paths():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + ".tmp")
            with temporary.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            temporary.replace(path)
            return
        except OSError as error:
            errors.append(error)
    raise errors[-1] if errors else OSError("无法保存数据位置设置")

DEFAULT_APP_SETTINGS = {
    "opacity": 96,
    "always_on_top": True,
    "autostart": False,
    "click_through": False,
    "ready_submission_reminder": True,
    "sidebar_auto_hide": True,
    "sidebar_pinned": False,
    "sidebar_position": "left",
    "sidebar_collapse_mode": "labels",
    # Both the low-friction Double-Tab default and a normal key sequence
    # selected in settings work globally on Windows while the app is running.
    "journal_import_shortcut": {"mode": "double_tab", "sequence": "Ctrl+Alt+J"},
    # A separate global visibility toggle. It deliberately ignores Double-
    # Space while an IME is open, so Chinese input remains uninterrupted.
    "window_visibility_shortcut": {"mode": "double_space", "sequence": "Ctrl+Alt+Space"},
    "return_home_when_inactive": True,
    "frontier_background_refresh": True,
    "ai": {
        "enabled": False,
        "base_url": "https://api.deepseek.com",
        "model": "deepseek-v4-flash",
        "api_key_secret": "",
        "journal_enrichment": True,
        "journal_recommendation": True,
        "frontier_rerank": True,
        "auto_frontier_rerank": False,
        "research_profile_update": True,
        "auto_profile_from_achievements": True,
        "auto_profile_from_frontier": True,
        "profile_read_achievement_pdfs": True,
        "journal_auto_enrichment": True,
        "journal_auto_last_checked": "",
        "paper_record_fill": True,
        "quick_capture": True,
    },
    "jcr": {
        "enabled": False,
        "endpoint_template": "",
        "api_key_secret": "",
    },
    "easyscholar": {
        "enabled": False,
        "secret_key_secret": "",
        "cache_days": 30,
        "last_auto_checked": "",
    },
    "auto_backup": False,
    "nav_order": ["home", "todo", "papers", "notes", "journals", "frontier", "achievements"],
    "appearance": {"theme_id": "fog_teal", "density": "comfortable"},
    "application_mode": "widget",
    "workbench_order": ["home", "work", "papers", "library"],
    "research": {"daily_profile_update": True, "filter_known_q3_q4": True},
    "widget_window": {
        "locked": False,
        "x": None,
        "y": None,
        "width": 520,
        "height": 680,
    },
    "software_window": {
        "x": None,
        "y": None,
        "width": 1180,
        "height": 820,
        "maximized": False,
    },
    # Kept on disk while v11 progressively migrates existing callers to the
    # explicit widget/software profiles.
    "window": {
        "locked": False,
        "x": None,
        "y": None,
        "width": 520,
        "height": 680,
    },
}

THEME_IDS = (
    "fog_teal",
    "ink_white",
    "moss_paper",
    "warm_sand",
    "graphite_mist",
    "night_sea",
    "cinnabar_paper",
    "violet_grove",
    "night_coral",
    "sunrise_cloud",
    "aurora_night",
    "iris_sun",
)
WORKBENCH_IDS = ("home", "work", "papers", "library")

DEFAULT_RESEARCH_AXES = (
    {
        "id": "soc_mapping",
        "name": "SOC 遥感制图",
        "core_terms": ["soil organic carbon", "SOC", "digital soil mapping"],
        "support_terms": ["remote sensing", "machine learning", "deep learning", "spatial prediction", "geospatial"],
        "strict_terms": ["digital soil mapping", "soil organic carbon mapping"],
    },
    {
        "id": "carbon_fractions",
        "name": "土壤碳组分与过程",
        "core_terms": ["MAOC", "POC", "mineral-associated organic carbon", "particulate organic carbon"],
        "support_terms": ["soil organic carbon", "soil carbon", "soil aggregate", "carbon stabilization"],
        "strict_terms": ["MAOC", "POC", "mineral-associated organic carbon", "particulate organic carbon"],
    },
    {
        "id": "land_soc",
        "name": "土地利用变化与 SOC",
        "core_terms": ["land use change", "land-use change", "cropland conversion", "land transfer"],
        "support_terms": ["soil organic carbon", "soil carbon", "SOC", "carbon sequestration"],
        "strict_terms": [],
    },
)

DEFAULT_FRONTIER_SOURCES = {
    # Crossref, OpenAlex and DOAJ work without a personal account. Semantic
    # Scholar is opt-in because its public anonymous quota is intentionally
    # small; a personal key can be supplied in the research settings.
    "crossref": {"enabled": True, "api_key": ""},
    "openalex": {"enabled": True, "api_key": ""},
    "doaj": {"enabled": True, "api_key": ""},
    "semantic_scholar": {"enabled": False, "api_key": ""},
    "arxiv": {"enabled": False, "api_key": ""},
}

DEFAULT_FRONTIER_PROFILE = {
    "version": 5,
    "primary_keywords": [],
    "secondary_keywords": [],
    "negative_keywords": [],
    "daily_limit": 5,
    "lookback_days": 7,
    "notify": True,
    "sources": DEFAULT_FRONTIER_SOURCES,
    "feedback": {"axis_weights": {}},
}


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        # Keep the user's original file for recovery and start with a clean
        # in-memory value rather than making the whole app fail to open.
        backup = path.with_suffix(path.suffix + ".broken")
        try:
            shutil.copy2(path, backup)
        except OSError:
            pass
        return fallback


def _write_json(path: Path, value: Any) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    temporary.replace(path)


def _normalize_todo(item: dict[str, Any], default_day: str | None = None) -> dict[str, Any]:
    """Normalise both scheduled and no-date tasks without losing old records.

    ``schedule_mode == 'none'`` means a task deliberately has no calendar
    window.  It remains visible in Today until it is completed, while legacy
    records continue to use their original start/end date range unchanged.
    """
    schedule_mode = str(item.get("schedule_mode", "range")).strip().casefold()
    if schedule_mode not in {"range", "none"}:
        schedule_mode = "range"

    created_for = str(
        item.get("created_for")
        or item.get("start_date")
        or item.get("date")
        or default_day
        or date.today().isoformat()
    )
    if schedule_mode == "none":
        start = ""
        end = ""
    else:
        start = str(item.get("start_date") or item.get("date") or default_day or date.today().isoformat())
        end = str(item.get("end_date") or start)
        if end < start:
            end = start
    done_dates = item.get("done_dates", [])
    if not isinstance(done_dates, list):
        done_dates = []
    completion_anchor = start or created_for
    if item.get("done") and completion_anchor not in done_dates:
        done_dates = [*done_dates, completion_anchor]
    # An open-ended task cannot sensibly be "daily recurring": it is one
    # continuous item until completion rather than a fresh daily check-off.
    repeat_daily = bool(item.get("repeat_daily", False)) and schedule_mode == "range"
    completed_at = str(item.get("completed_at", ""))
    if not completed_at and done_dates and not repeat_daily:
        completed_at = max(str(value) for value in done_dates)
    sort_order = _integer(item.get("sort_order"), 10_000, 0)
    raw_source = item.get("source", {})
    raw_source = raw_source if isinstance(raw_source, dict) else {}
    source_kind = str(raw_source.get("kind", "")).strip()
    source_id = str(raw_source.get("id", "")).strip()
    source_label = str(raw_source.get("label", "")).strip()
    normalized = {
        "id": str(item.get("id") or uuid.uuid4().hex),
        "title": str(item.get("title", "")),
        "schedule_mode": schedule_mode,
        "created_for": created_for,
        "start_date": start,
        "end_date": end,
        "repeat_daily": repeat_daily,
        "done_dates": [str(value) for value in done_dates],
        "quadrant": str(item.get("quadrant", "")),
        "completed_at": completed_at,
        "sort_order": sort_order,
        "source": {
            "kind": source_kind,
            "id": source_id,
            "label": source_label,
        }
        if source_kind or source_id or source_label
        else {},
    }
    for field in ("paper_id", "special_issue_id", "deadline", "deadline_mode"):
        value = str(item.get(field, "")).strip()
        if value:
            normalized[field] = value
    return normalized


def _load_todo_tasks() -> list[dict[str, Any]]:
    payload = _read_json(TODO_FILE, {})
    if not isinstance(payload, dict):
        return []
    current_tasks = payload.get("tasks")
    if isinstance(current_tasks, list):
        return [_normalize_todo(item) for item in current_tasks if isinstance(item, dict)]

    # Migrate the original {"yyyy-mm-dd": [items]} format to task records.
    tasks: list[dict[str, Any]] = []
    for day_key, items in payload.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            tasks.append(_normalize_todo(item, str(day_key)))
    return tasks


def load_todos(day: date) -> list[dict[str, Any]]:
    day_key = day.isoformat()
    result = []
    for task in _load_todo_tasks():
        schedule_mode = str(task.get("schedule_mode", "range"))
        if schedule_mode == "none":
            created_for = str(task.get("created_for", day_key))
            completed_at = str(task.get("completed_at", ""))
            visible = created_for <= day_key and (not completed_at or day_key <= completed_at)
        else:
            visible = str(task.get("start_date", day_key)) <= day_key <= str(task.get("end_date", day_key))
        if visible:
            item = dict(task)
            item["done"] = day_key in task.get("done_dates", [])
            result.append(item)
    return sorted(result, key=lambda item: int(item.get("sort_order", 10_000)))


def save_todos(day: date, items: list[dict[str, Any]]) -> None:
    day_key = day.isoformat()
    tasks = _load_todo_tasks()
    indexes = {str(task.get("id")): index for index, task in enumerate(tasks)}
    for order, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        item = dict(item)
        item["sort_order"] = order
        normalized = _normalize_todo(item, day_key)
        task_id = normalized["id"]
        if task_id in indexes:
            current = tasks[indexes[task_id]]
            normalized["done_dates"] = list(current.get("done_dates", []))
            normalized["completed_at"] = str(item.get("completed_at", current.get("completed_at", "")))
            if item.get("done"):
                if day_key not in normalized["done_dates"]:
                    normalized["done_dates"].append(day_key)
                if not normalized["repeat_daily"]:
                    normalized["completed_at"] = day_key
            else:
                normalized["done_dates"] = [value for value in normalized["done_dates"] if value != day_key]
                if normalized["completed_at"] == day_key:
                    normalized["completed_at"] = ""
            tasks[indexes[task_id]] = normalized
        else:
            if item.get("done") and day_key not in normalized["done_dates"]:
                normalized["done_dates"].append(day_key)
                if not normalized["repeat_daily"]:
                    normalized["completed_at"] = day_key
            tasks.append(normalized)
            indexes[task_id] = len(tasks) - 1
    _write_json(TODO_FILE, {"version": 2, "tasks": tasks})


def add_todo(
    title: str,
    day: date | None = None,
    *,
    quadrant: str = "",
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create one compact task without requiring a page-level editor.

    Inbox and action-center flows use this helper so they remain safe even
    when the Today page is not currently loaded.
    """
    text = str(title).strip()
    if not text:
        raise ValueError("任务内容不能为空")
    target_day = day or date.today()
    key = target_day.isoformat()
    tasks = _load_todo_tasks()
    current_orders = [int(task.get("sort_order", 0)) for task in tasks if str(task.get("start_date", "")) == key]
    task = _normalize_todo(
        {
            "id": uuid.uuid4().hex,
            "title": text,
            "start_date": key,
            "end_date": key,
            "repeat_daily": False,
            "done_dates": [],
            "quadrant": quadrant,
            "sort_order": (max(current_orders) + 1) if current_orders else 0,
            "source": source or {},
        },
        key,
    )
    tasks.append(task)
    _write_json(TODO_FILE, {"version": 2, "tasks": tasks})
    result = dict(task)
    result["done"] = False
    return result


def delete_todo(todo_id: str) -> None:
    tasks = [task for task in _load_todo_tasks() if str(task.get("id")) != str(todo_id)]
    _write_json(TODO_FILE, {"version": 2, "tasks": tasks})


def cleanup_old_completed_tasks(today: date) -> None:
    """Keep a rolling 30-day completion history without touching unfinished work."""
    cutoff = (today - timedelta(days=30)).isoformat()
    cleaned: list[dict[str, Any]] = []
    for task in _load_todo_tasks():
        task["done_dates"] = [value for value in task.get("done_dates", []) if value >= cutoff]
        completed_at = str(task.get("completed_at", ""))
        if not task.get("repeat_daily") and completed_at and completed_at < cutoff:
            continue
        cleaned.append(task)
    _write_json(TODO_FILE, {"version": 2, "tasks": cleaned})


def load_task_history(today: date, days: int = 30) -> list[dict[str, str]]:
    """Return individual completion events for the compact Todo history section."""
    cutoff = (today - timedelta(days=max(days - 1, 0))).isoformat()
    history: list[dict[str, str]] = []
    for task in _load_todo_tasks():
        for completed_on in task.get("done_dates", []):
            completed_on = str(completed_on)
            if cutoff <= completed_on <= today.isoformat():
                history.append(
                    {
                        "id": f"{task.get('id', '')}:{completed_on}",
                        "title": str(task.get("title", "")),
                        "completed_on": completed_on,
                    }
                )
    return sorted(history, key=lambda item: item["completed_on"], reverse=True)


def pending_todo_migrations(today: date) -> list[dict[str, Any]]:
    """Find non-recurring tasks left unfinished when yesterday ended."""
    yesterday = (today - timedelta(days=1)).isoformat()
    pending: list[dict[str, Any]] = []
    for task in _load_todo_tasks():
        if (
            str(task.get("schedule_mode", "range")) == "none"
            or task.get("repeat_daily")
            or str(task.get("end_date", "")) != yesterday
        ):
            continue
        if str(task.get("completed_at", "")) or yesterday in task.get("done_dates", []):
            continue
        pending.append(dict(task))
    return pending


def migrate_todos_to_today(todo_ids: list[str], today: date) -> None:
    task_ids = {str(todo_id) for todo_id in todo_ids}
    day_key = today.isoformat()
    tasks = _load_todo_tasks()
    for task in tasks:
        if str(task.get("id")) not in task_ids:
            continue
        task["start_date"] = day_key
        task["end_date"] = day_key
        task["completed_at"] = ""
    _write_json(TODO_FILE, {"version": 2, "tasks": tasks})


def todo_migration_checked(today: date) -> bool:
    payload = _read_json(TODO_META_FILE, {})
    days = payload.get("migration_checked_days", []) if isinstance(payload, dict) else []
    return today.isoformat() in {str(value) for value in days} if isinstance(days, list) else False


def mark_todo_migration_checked(today: date) -> None:
    payload = _read_json(TODO_META_FILE, {})
    days = payload.get("migration_checked_days", []) if isinstance(payload, dict) else []
    days = [str(value) for value in days] if isinstance(days, list) else []
    day_key = today.isoformat()
    if day_key not in days:
        days.append(day_key)
    _write_json(TODO_META_FILE, {"migration_checked_days": sorted(days)[-90:]})


def load_inspirations() -> list[dict[str, Any]]:
    payload = _read_json(INSPIRATION_FILE, [])
    if not isinstance(payload, list):
        return []
    result = []
    for item in payload:
        if isinstance(item, str) and item.strip():
            result.append({"id": uuid.uuid4().hex, "text": item.strip(), "created_at": ""})
        elif isinstance(item, dict) and str(item.get("text", "")).strip():
            result.append(
                {
                    "id": str(item.get("id") or uuid.uuid4().hex),
                    "text": str(item["text"]).strip(),
                    "created_at": str(item.get("created_at", "")).strip(),
                }
            )
    return result


def save_inspirations(items: list[dict[str, Any]]) -> None:
    normalized = [
        {
            "id": str(item.get("id") or uuid.uuid4().hex),
            "text": str(item.get("text", "")).strip(),
            "created_at": str(item.get("created_at", "")).strip(),
        }
        for item in items
        if isinstance(item, dict) and str(item.get("text", "")).strip()
    ]
    _write_json(INSPIRATION_FILE, normalized)


def add_inspiration(text: str) -> dict[str, Any]:
    value = str(text).strip()
    if not value:
        raise ValueError("灵感内容不能为空")
    item = {"id": uuid.uuid4().hex, "text": value, "created_at": date.today().isoformat()}
    inspirations = load_inspirations()
    inspirations.insert(0, item)
    save_inspirations(inspirations)
    return item


def _integer(value: Any, fallback: int, minimum: int | None = None, maximum: int | None = None) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def _normalize_ai_settings(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "base_url": str(raw.get("base_url", "https://api.deepseek.com")).strip() or "https://api.deepseek.com",
        "model": str(raw.get("model", "deepseek-v4-flash")).strip() or "deepseek-v4-flash",
        # This is a Windows-DPAPI encrypted token, never the original API key.
        "api_key_secret": str(raw.get("api_key_secret", "")).strip(),
        "journal_enrichment": bool(raw.get("journal_enrichment", True)),
        "journal_recommendation": bool(raw.get("journal_recommendation", True)),
        "frontier_rerank": bool(raw.get("frontier_rerank", True)),
        "auto_frontier_rerank": bool(raw.get("auto_frontier_rerank", False)),
        "research_profile_update": bool(raw.get("research_profile_update", True)),
        "auto_profile_from_achievements": bool(raw.get("auto_profile_from_achievements", True)),
        "auto_profile_from_frontier": bool(raw.get("auto_profile_from_frontier", True)),
        "profile_read_achievement_pdfs": bool(raw.get("profile_read_achievement_pdfs", True)),
        "journal_auto_enrichment": bool(raw.get("journal_auto_enrichment", True)),
        "journal_auto_last_checked": str(raw.get("journal_auto_last_checked", "")).strip(),
        "paper_record_fill": bool(raw.get("paper_record_fill", True)),
        "quick_capture": bool(raw.get("quick_capture", True)),
    }


def _normalize_jcr_settings(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(raw.get("enabled", False)),
        "endpoint_template": str(raw.get("endpoint_template", "")).strip(),
        # This is a Windows-DPAPI encrypted token, never the original API key.
        "api_key_secret": str(raw.get("api_key_secret", "")).strip(),
    }


def _normalize_easyscholar_settings(raw: Any) -> dict[str, Any]:
    """Keep the optional EasyScholar token and local refresh policy explicit."""
    raw = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(raw.get("enabled", False)),
        # This is a Windows-DPAPI encrypted token, never the original API key.
        "secret_key_secret": str(raw.get("secret_key_secret", "")).strip(),
        "cache_days": _integer(raw.get("cache_days"), 30, 1, 365),
        "last_auto_checked": str(raw.get("last_auto_checked", "")).strip()[:32],
    }


def _normalize_journal_import_shortcut(raw: Any, legacy_enabled: Any = True) -> dict[str, str]:
    """Keep shortcut preferences readable and compatible with old Double-Tab builds."""
    raw = raw if isinstance(raw, dict) else {}
    mode = str(raw.get("mode", "")).strip().casefold()
    if mode not in {"double_tab", "sequence", "off"}:
        mode = "double_tab" if bool(legacy_enabled) else "off"
    sequence = " ".join(str(raw.get("sequence", "Ctrl+Alt+J")).split())[:80]
    if not sequence:
        sequence = "Ctrl+Alt+J"
    return {"mode": mode, "sequence": sequence}


def _normalize_window_visibility_shortcut(raw: Any) -> dict[str, str]:
    """Normalize the global show/hide shortcut without inheriting Tab settings."""
    raw = raw if isinstance(raw, dict) else {}
    mode = str(raw.get("mode", "double_space")).strip().casefold()
    if mode not in {"double_space", "sequence", "off"}:
        mode = "double_space"
    sequence = " ".join(str(raw.get("sequence", "Ctrl+Alt+Space")).split())[:80]
    if not sequence:
        sequence = "Ctrl+Alt+Space"
    return {"mode": mode, "sequence": sequence}


def _coordinate(raw: dict[str, Any], fallback: dict[str, Any], key: str) -> int | None:
    value = raw[key] if key in raw else fallback.get(key)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _normalize_window_profile(
    raw: Any,
    fallback: Any,
    *,
    default_width: int,
    default_height: int,
    minimum_width: int,
    minimum_height: int,
    include_locked: bool = False,
    include_maximized: bool = False,
) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    fallback = fallback if isinstance(fallback, dict) else {}
    result: dict[str, Any] = {
        "x": _coordinate(raw, fallback, "x"),
        "y": _coordinate(raw, fallback, "y"),
        "width": _integer(raw.get("width", fallback.get("width")), default_width, minimum_width),
        "height": _integer(raw.get("height", fallback.get("height")), default_height, minimum_height),
    }
    if include_locked:
        result["locked"] = bool(raw.get("locked", fallback.get("locked", False)))
    if include_maximized:
        result["maximized"] = bool(raw.get("maximized", fallback.get("maximized", False)))
    return result


def normalize_app_settings(payload: Any) -> dict[str, Any]:
    """Return a lossless settings map with the v11 application defaults."""
    payload = payload if isinstance(payload, dict) else {}
    normalized = dict(payload)
    raw_window = payload.get("window", {}) if isinstance(payload.get("window"), dict) else {}
    raw_widget_window = payload.get("widget_window", raw_window)
    raw_software_window = payload.get("software_window", {})
    raw_appearance = payload.get("appearance", {}) if isinstance(payload.get("appearance"), dict) else {}
    raw_research = payload.get("research", {}) if isinstance(payload.get("research"), dict) else {}

    default_nav_order = list(DEFAULT_APP_SETTINGS["nav_order"])
    raw_nav_order = payload.get("nav_order", default_nav_order)
    raw_nav_order = raw_nav_order if isinstance(raw_nav_order, list) else default_nav_order
    nav_order = [str(value) for value in raw_nav_order if str(value) in default_nav_order]
    nav_order.extend(value for value in default_nav_order if value not in nav_order)

    raw_workbench_order = payload.get("workbench_order", list(WORKBENCH_IDS))
    raw_workbench_order = raw_workbench_order if isinstance(raw_workbench_order, list) else list(WORKBENCH_IDS)
    workbench_order = [str(value) for value in raw_workbench_order if str(value) in WORKBENCH_IDS]
    workbench_order.extend(value for value in WORKBENCH_IDS if value not in workbench_order)

    theme_id = str(raw_appearance.get("theme_id", "fog_teal")).strip()
    density = str(raw_appearance.get("density", "comfortable")).strip()
    widget_window = _normalize_window_profile(
        raw_widget_window,
        raw_window,
        default_width=520,
        default_height=680,
        minimum_width=400,
        minimum_height=480,
        include_locked=True,
    )
    software_window = _normalize_window_profile(
        raw_software_window,
        {},
        default_width=1180,
        default_height=820,
        minimum_width=920,
        minimum_height=680,
        include_maximized=True,
    )
    normalized.update(
        {
            "opacity": _integer(payload.get("opacity"), 96, 20, 100),
            "always_on_top": bool(payload.get("always_on_top", True)),
            "autostart": bool(payload.get("autostart", False)),
            "click_through": bool(payload.get("click_through", False)),
            "ready_submission_reminder": bool(payload.get("ready_submission_reminder", True)),
            "sidebar_auto_hide": bool(payload.get("sidebar_auto_hide", True)),
            "sidebar_pinned": bool(payload.get("sidebar_pinned", False)),
            "sidebar_position": "right" if str(payload.get("sidebar_position", "left")).casefold() == "right" else "left",
            "sidebar_collapse_mode": "content" if str(payload.get("sidebar_collapse_mode", "labels")).casefold() == "content" else "labels",
            "journal_import_shortcut": _normalize_journal_import_shortcut(
                payload.get("journal_import_shortcut"),
                payload.get("journal_import_double_tab", payload.get("research_inbox_double_tab", True)),
            ),
            "window_visibility_shortcut": _normalize_window_visibility_shortcut(payload.get("window_visibility_shortcut")),
            "return_home_when_inactive": bool(payload.get("return_home_when_inactive", True)),
            "frontier_background_refresh": bool(payload.get("frontier_background_refresh", True)),
            "ai": _normalize_ai_settings(payload.get("ai")),
            "jcr": _normalize_jcr_settings(payload.get("jcr")),
            "easyscholar": _normalize_easyscholar_settings(payload.get("easyscholar")),
            "auto_backup": bool(payload.get("auto_backup", False)),
            "nav_order": nav_order,
            "appearance": {"theme_id": theme_id if theme_id in THEME_IDS else "fog_teal", "density": density if density in {"compact", "comfortable"} else "comfortable"},
            # v12 has one compact shell. Retain legacy geometry elsewhere for
            # rollback, but never expose or reactivate the removed mode.
            "application_mode": "widget",
            "workbench_order": workbench_order,
            "research": {
                "daily_profile_update": bool(raw_research.get("daily_profile_update", True)),
                "filter_known_q3_q4": bool(raw_research.get("filter_known_q3_q4", True)),
            },
            "widget_window": widget_window,
            "software_window": software_window,
            # Legacy callers still read and update this field. Keep it an
            # exact alias of the active widget geometry during migration.
            "window": dict(widget_window),
        }
    )
    return normalized


def load_app_settings() -> dict[str, Any]:
    """Load lightweight, local UI preferences with safe defaults."""
    return normalize_app_settings(_read_json(SETTINGS_FILE, {}))


def save_app_settings(settings: dict[str, Any]) -> None:
    """Persist losslessly normalized UI preferences next to the local JSON files."""
    payload = dict(settings) if isinstance(settings, dict) else {}
    # Existing pages still update ``window``.  Treat it as authoritative until
    # all callers have moved to ``widget_window`` so resizing cannot regress.
    legacy_window = payload.get("window")
    if isinstance(legacy_window, dict):
        payload["widget_window"] = dict(legacy_window)
    _write_json(SETTINGS_FILE, normalize_app_settings(payload))


def _normalize_reading(item: dict[str, Any]) -> dict[str, str] | None:
    title = str(item.get("title", "")).strip()
    if not title:
        return None
    status = str(item.get("status", "未阅读"))
    return {
        "id": str(item.get("id") or uuid.uuid4().hex),
        "title": title,
        "status": status if status in {"未阅读", "已阅读"} else "未阅读",
        "reason": str(item.get("reason", "")).strip(),
        "doi": str(item.get("doi", "")).strip(),
        "url": str(item.get("url", "")).strip(),
        "created_at": str(item.get("created_at", "")).strip(),
        "updated_at": str(item.get("updated_at", "")).strip(),
    }


def load_readings() -> list[dict[str, str]]:
    payload = _read_json(READINGS_FILE, [])
    if not isinstance(payload, list):
        return []
    readings: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        normalized = _normalize_reading(item)
        if normalized is not None:
            readings.append(normalized)
    return readings


def save_readings(items: list[dict[str, Any]]) -> None:
    normalized = [_normalize_reading(item) for item in items if isinstance(item, dict)]
    _write_json(READINGS_FILE, [item for item in normalized if item is not None])


def _normalize_frontier_profile(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}

    def normalize_terms(value: Any, fallback: list[str]) -> list[str]:
        if isinstance(value, str):
            values = value.replace("，", ",").replace("；", ",").split(",")
        elif isinstance(value, list):
            values = value
        else:
            values = fallback
        result: list[str] = []
        seen: set[str] = set()
        for item in values:
            term = str(item).strip()
            key = term.casefold()
            if term and key not in seen:
                result.append(term)
                seen.add(key)
        return result[:12]

    # v5 keeps one user-owned primary-keyword box and one secondary-keyword
    # global primary-keyword box and one global secondary-keyword box. Old
    # directional data is flattened once so prior custom work is not lost.
    primary_keywords = normalize_terms(raw.get("primary_keywords"), [])
    secondary_keywords = normalize_terms(raw.get("secondary_keywords"), [])
    raw_axes = raw.get("research_axes")
    raw_axes = raw_axes if isinstance(raw_axes, list) else []
    if not primary_keywords and raw_axes:
        primary_keywords = normalize_terms(
            [term for axis in raw_axes if isinstance(axis, dict) for term in axis.get("core_terms", [])],
            [],
        )
    if not secondary_keywords and raw_axes:
        secondary_keywords = normalize_terms(
            [term for axis in raw_axes if isinstance(axis, dict) for term in axis.get("support_terms", [])],
            [],
        )
    legacy_terms = normalize_terms(raw.get("keywords"), [])
    known = {term.casefold() for term in [*primary_keywords, *secondary_keywords]}
    for term in legacy_terms:
        if term.casefold() not in known:
            secondary_keywords.append(term)
            known.add(term.casefold())
    primary_keywords = primary_keywords[:24]
    secondary_keywords = secondary_keywords[:24]

    raw_feedback = raw.get("feedback", {})
    raw_weights = raw_feedback.get("axis_weights", {}) if isinstance(raw_feedback, dict) else {}
    raw_weights = raw_weights if isinstance(raw_weights, dict) else {}
    weights = {
        axis_id: _integer(value, 0, -6, 6)
        for axis_id, value in raw_weights.items()
        if isinstance(value, int)
    }
    raw_term_weights = raw_feedback.get("term_weights", {}) if isinstance(raw_feedback, dict) else {}
    raw_term_weights = raw_term_weights if isinstance(raw_term_weights, dict) else {}
    term_weights = {
        str(term).strip().casefold(): _integer(value, 0, -6, 6)
        for term, value in raw_term_weights.items()
        if str(term).strip() and isinstance(value, int)
    }
    # v2 used separate "strict" and "explore" quotas. v3 has three explicit
    # keyword-pair tiers, so keep the old total as a sensible one-time default.
    legacy_total = _integer(raw.get("strict_limit"), 3, 1, 8) + _integer(raw.get("explore_limit"), 2, 0, 5)
    daily_limit = _integer(raw.get("daily_limit"), legacy_total, 1, 12)

    raw_sources = raw.get("sources", {})
    raw_sources = raw_sources if isinstance(raw_sources, dict) else {}
    sources: dict[str, dict[str, Any]] = {}
    for source_id, defaults in DEFAULT_FRONTIER_SOURCES.items():
        current = raw_sources.get(source_id, {})
        current = current if isinstance(current, dict) else {}
        key = str(current.get("api_key", defaults["api_key"])).strip()
        sources[source_id] = {
            "enabled": bool(current.get("enabled", defaults["enabled"])),
            # Keys live only in the user's local frontier.json. Keep a
            # conservative bound so a pasted response cannot bloat backups.
            "api_key": key[:512],
        }
    legacy_result = {
        "version": 6,
        "primary_keywords": primary_keywords,
        "secondary_keywords": secondary_keywords,
        "negative_keywords": normalize_terms(raw.get("negative_keywords"), []),
        # AI augmentation never replaces the local deterministic admission
        # gate.  These terms only improve upstream recall and explain the
        # search focus selected from outcomes and explicit feedback.
        "ai_search_terms": normalize_terms(raw.get("ai_search_terms"), []),
        "ai_search_logic": str(raw.get("ai_search_logic", "")).strip()[:420],
        "ai_profile_updated_at": str(raw.get("ai_profile_updated_at", "")).strip(),
        "ai_profile_attempted_at": str(raw.get("ai_profile_attempted_at", "")).strip(),
        "ai_profile_model": str(raw.get("ai_profile_model", "")).strip()[:80],
        "ai_profile_source_signature": str(raw.get("ai_profile_source_signature", "")).strip()[:80],
        "ai_profile_last_checked_at": str(raw.get("ai_profile_last_checked_at", "")).strip(),
        "daily_limit": daily_limit,
        "lookback_days": _integer(raw.get("lookback_days"), 7, 1, 30),
        "notify": bool(raw.get("notify", True)),
        "sources": sources,
        "feedback": {"axis_weights": weights, "term_weights": term_weights},
        "require_verified_jcr_q1_q2": bool(raw.get("require_verified_jcr_q1_q2", True)),
        "frontier_update_frequency": str(raw.get("frontier_update_frequency", "daily"))
        if str(raw.get("frontier_update_frequency", "daily")) in {"daily", "weekly", "manual"}
        else "daily",
        "frontier_auto_update": bool(raw.get("frontier_auto_update", True)),
    }
    # Normalize the new terms/pending queue after the legacy profile fields
    # have been flattened.  The previous release returned the legacy mapping
    # above and left this normalization unreachable, which silently discarded
    # locked terms every time frontier.json was reopened.
    profile_keys = (
        "terms",
        "pending_terms",
        "excluded_terms",
        "excluded_entries",
        "blocked_terms",
        "update_log",
        "filter_known_q3_q4",
        "legacy_profile_migrated_at",
        "last_organization_snapshot",
        "last_auto_organization_date",
        "auto_organization_suppressed_for_date",
    )
    return normalize_research_profile_v11(
        {**legacy_result, **{key: raw[key] for key in profile_keys if key in raw}}
    )


def _normalize_frontier_item(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    item_id = str(raw.get("id", "")).strip()
    title = str(raw.get("title", "")).strip()
    if not item_id or not title:
        return None
    status = str(raw.get("status", "new"))
    if status not in {"new", "saved", "liked", "dismissed", "read", "deprioritized"}:
        status = "new"
    terms = raw.get("match_terms", [])
    terms = terms if isinstance(terms, list) else []
    score_breakdown = raw.get("score_breakdown", {})
    score_breakdown = score_breakdown if isinstance(score_breakdown, dict) else {}
    normalized = dict(raw)
    normalized.update(
        {
        "id": item_id,
        "title": title,
        "journal": str(raw.get("journal", "")).strip(),
        "published_date": str(raw.get("published_date", "")).strip(),
        "url": str(raw.get("url", "")).strip(),
        "doi": str(raw.get("doi", "")).strip(),
        "abstract": str(raw.get("abstract", "")).strip()[:8000],
        "author_keywords": [str(value).strip() for value in raw.get("author_keywords", []) if str(value).strip()][:20]
        if isinstance(raw.get("author_keywords"), list)
        else [],
        "keyword_source": str(raw.get("keyword_source", "")).strip(),
        "match_mode": str(raw.get("match_mode", "title_abstract")).strip()
        if str(raw.get("match_mode", "title_abstract")).strip() in {"source_keywords", "title_abstract"}
        else "title_abstract",
        "source_names": [str(value).strip() for value in raw.get("source_names", []) if str(value).strip()][:6]
        if isinstance(raw.get("source_names"), list)
        else [],
        "authors": [str(value).strip() for value in raw.get("authors", []) if str(value).strip()]
        if isinstance(raw.get("authors"), list)
        else [],
        "match_terms": [str(value).strip() for value in terms if str(value).strip()][:12],
        "priority": str(raw.get("priority", "扩展")),
        "score": _integer(raw.get("score"), 0),
        "score_breakdown": {
            key: _integer(
                score_breakdown.get(key),
                0,
                -15 if key == "ai" else -30 if key == "feedback" else 0,
                500,
            )
            for key in ("terms", "priority", "quality", "feedback", "ai")
        },
        "matched_term_ids": [str(value).strip() for value in raw.get("matched_term_ids", []) if str(value).strip()][:24]
        if isinstance(raw.get("matched_term_ids"), list)
        else [],
        "jcr_state": str(raw.get("jcr_state", "")).strip()[:80],
        "jcr_quartile": str(raw.get("jcr_quartile", "")).strip().upper()[:2],
        "journal_quality_flag": str(raw.get("journal_quality_flag", "")).strip()[:40],
        "filtered_by_quality": bool(raw.get("filtered_by_quality", False)),
        "feedback_adjustment": _integer(raw.get("feedback_adjustment"), 0, -30, 30),
        "ai_adjustment": _integer(raw.get("ai_adjustment"), 0, -15, 15),
        "feedback_events": [dict(event) for event in raw.get("feedback_events", []) if isinstance(event, dict)][-100:]
        if isinstance(raw.get("feedback_events"), list)
        else [],
        "one_line_feedback": str(raw.get("one_line_feedback", "")).strip()[:800],
        "library_journal_id": str(raw.get("library_journal_id", "")).strip()[:80],
        "profile_algorithm_version": _integer(raw.get("profile_algorithm_version"), 0, 0, 99),
        "summary_cn": str(raw.get("summary_cn", "")).strip(),
        "relevance_level": str(raw.get("relevance_level", "legacy"))
        if str(raw.get("relevance_level", "legacy")) in {"primary_pair", "mixed_pair", "secondary_pair", "strict", "explore", "legacy"}
        else "legacy",
        "matched_axis_ids": [str(value).strip() for value in raw.get("matched_axis_ids", []) if str(value).strip()][:3]
        if isinstance(raw.get("matched_axis_ids"), list)
        else [],
        "matched_axes": [str(value).strip() for value in raw.get("matched_axes", []) if str(value).strip()][:3]
        if isinstance(raw.get("matched_axes"), list)
        else [],
        "recommendation_reason": str(raw.get("recommendation_reason", "")).strip(),
        "ai_score": _integer(raw.get("ai_score"), -1, -1, 100),
        "ai_summary_cn": str(raw.get("ai_summary_cn", "")).strip()[:220],
        "ai_reason_cn": str(raw.get("ai_reason_cn", "")).strip()[:240],
        "ai_model": str(raw.get("ai_model", "")).strip()[:80],
        "ai_updated_at": str(raw.get("ai_updated_at", "")).strip(),
        "feedback": str(raw.get("feedback", ""))
        if str(raw.get("feedback", "")) in {"relevant", "irrelevant", "too_broad", "read"}
        else "",
        "status": status,
        # Preserve enough context to let a user recover a previously hidden
        # recommendation without corrupting learned preference weights.
        "dismissed_from_status": str(raw.get("dismissed_from_status", ""))
        if str(raw.get("dismissed_from_status", "")) in {"new", "saved", "liked", "read", "deprioritized"}
        else "",
        "dismissed_from_feedback": str(raw.get("dismissed_from_feedback", ""))
        if str(raw.get("dismissed_from_feedback", "")) in {"", "relevant", "irrelevant", "too_broad", "read"}
        else "",
        "dismissed_feedback_delta": _integer(raw.get("dismissed_feedback_delta"), 0, -2, 0),
        "deprioritized_from_status": str(raw.get("deprioritized_from_status", ""))
        if str(raw.get("deprioritized_from_status", "")) in {"new", "saved", "liked", "read"}
        else "",
        "deprioritized_from_feedback": str(raw.get("deprioritized_from_feedback", ""))
        if str(raw.get("deprioritized_from_feedback", "")) in {"", "relevant", "irrelevant", "read"}
        else "",
        "deprioritized_feedback_delta": _integer(raw.get("deprioritized_feedback_delta"), 0, -2, 0),
        "fetched_at": str(raw.get("fetched_at", "")).strip(),
        "recommendation_date": str(raw.get("recommendation_date", "")).strip(),
        "first_seen_date": str(raw.get("first_seen_date", "")).strip(),
        }
    )
    return normalized


def load_frontier_data() -> dict[str, Any]:
    payload = _read_json(FRONTIER_FILE, {})
    payload = payload if isinstance(payload, dict) else {}
    raw_items = payload.get("items", [])
    items = [_normalize_frontier_item(item) for item in raw_items] if isinstance(raw_items, list) else []
    source_cache = payload.get("source_cache", {})
    source_cache = source_cache if isinstance(source_cache, dict) else {}
    return {
        "profile": _normalize_frontier_profile(payload.get("profile")),
        "items": [item for item in items if item is not None],
        "last_checked": str(payload.get("last_checked", "")).strip(),
        "last_notified": str(payload.get("last_notified", "")).strip(),
        "source_cache": {str(key): str(value) for key, value in source_cache.items() if str(key) and str(value)},
        "algorithm_version": _integer(payload.get("algorithm_version"), 0, 0),
        "profile_signature": str(payload.get("profile_signature", "")).strip(),
    }


def save_frontier_data(data: dict[str, Any]) -> None:
    data = data if isinstance(data, dict) else {}
    items = data.get("items", [])
    normalized_items = [_normalize_frontier_item(item) for item in items] if isinstance(items, list) else []
    source_cache = data.get("source_cache", {})
    source_cache = source_cache if isinstance(source_cache, dict) else {}
    _write_json(
        FRONTIER_FILE,
        {
            "profile": _normalize_frontier_profile(data.get("profile")),
            "items": [item for item in normalized_items if item is not None][:500],
            "last_checked": str(data.get("last_checked", "")).strip(),
            "last_notified": str(data.get("last_notified", "")).strip(),
            "source_cache": {str(key): str(value) for key, value in source_cache.items() if str(key) and str(value)},
            "algorithm_version": _integer(data.get("algorithm_version"), 0, 0),
            "profile_signature": str(data.get("profile_signature", "")).strip(),
        },
    )


def record_frontier_feedback_event(
    data: dict[str, Any],
    item_id: str,
    action: str,
    text: str,
    classification: dict[str, Any],
    now: str | None = None,
) -> dict[str, Any]:
    """Return a serializable copy with one auditable frontier feedback event."""
    result = deepcopy(data if isinstance(data, dict) else {})
    items = result.get("items", [])
    items = items if isinstance(items, list) else []
    result["items"] = items
    clean_item_id = str(item_id).strip()
    clean_text = str(text).strip()[:800]
    event_at = str(now or datetime.now().isoformat(timespec="seconds")).strip()
    for item in items:
        if not isinstance(item, dict) or str(item.get("id", "")).strip() != clean_item_id:
            continue
        raw_events = item.get("feedback_events", [])
        events = [dict(event) for event in raw_events if isinstance(event, dict)] if isinstance(raw_events, list) else []
        event = {
            "id": uuid.uuid4().hex,
            "action": str(action).strip()[:60],
            "text": clean_text,
            "at": event_at,
            "article_id": clean_item_id,
            "journal": str(item.get("journal", "")).strip()[:180],
            "matched_terms": [str(value).strip() for value in item.get("match_terms", []) if str(value).strip()][:12]
            if isinstance(item.get("match_terms"), list)
            else [],
            "score_snapshot": _integer(item.get("score"), 0),
            "classification": dict(classification) if isinstance(classification, dict) else {},
        }
        events.append(event)
        item["feedback_events"] = events[-100:]
        if clean_text:
            item["one_line_feedback"] = clean_text
        if str(event["classification"].get("quality_flag", "")).strip().casefold() in {"low", "q3_q4"}:
            item["journal_quality_flag"] = "low"
        break
    return result


def load_reminder_state() -> dict[str, Any]:
    """Load reminder decisions without invalidating records from older builds.

    ``dismissed`` is retained for the once-only "准备投稿" prompts.  Status
    reminders are deliberately *not* permanently dismissed: users can snooze a
    checkpoint or mark it handled, which resets the status-update clock.
    """
    payload = _read_json(REMINDER_FILE, {})
    payload = payload if isinstance(payload, dict) else {}
    dismissed = payload.get("dismissed", [])
    dismissed = dismissed if isinstance(dismissed, list) else []
    raw_snoozed = payload.get("snoozed_until", {})
    raw_snoozed = raw_snoozed if isinstance(raw_snoozed, dict) else {}
    return {
        "dismissed": {str(value) for value in dismissed if str(value)},
        "snoozed_until": {
            str(key): str(value).strip()
            for key, value in raw_snoozed.items()
            if str(key) and str(value).strip()
        },
    }


def save_reminder_state(state: dict[str, Any]) -> None:
    state = state if isinstance(state, dict) else {}
    dismissed = state.get("dismissed", set())
    if not isinstance(dismissed, (set, list, tuple)):
        dismissed = set()
    snoozed = state.get("snoozed_until", {})
    snoozed = snoozed if isinstance(snoozed, dict) else {}
    _write_json(
        REMINDER_FILE,
        {
            "dismissed": sorted({str(value) for value in dismissed if str(value)}),
            "snoozed_until": {
                str(key): str(value).strip()
                for key, value in snoozed.items()
                if str(key) and str(value).strip()
            },
        },
    )


def load_dismissed_reminders() -> set[str]:
    """Compatibility wrapper for the legacy ready-to-submit prompt."""
    return set(load_reminder_state()["dismissed"])


def save_dismissed_reminders(reminder_ids: set[str]) -> None:
    state = load_reminder_state()
    state["dismissed"] = set(reminder_ids)
    save_reminder_state(state)


def load_papers() -> list[dict[str, Any]]:
    payload = _read_json(PAPERS_FILE, [])
    if not isinstance(payload, list):
        return []
    return [normalize_paper(item) for item in payload if isinstance(item, dict)]


def _normalize_timeline(raw: Any, status: str, fallback_date: str) -> list[dict[str, str]]:
    timeline: list[dict[str, str]] = []
    if isinstance(raw, list):
        for event in raw:
            if not isinstance(event, dict):
                continue
            event_date = str(event.get("date", "")).strip()
            event_status = str(event.get("status", status)).strip()
            if not event_date or not event_status:
                continue
            timeline.append(
                {
                    "id": str(event.get("id") or uuid.uuid4().hex),
                    "date": event_date,
                    "status": event_status,
                    "note": str(event.get("note", "")).strip(),
                }
            )
    if not timeline and fallback_date:
        timeline.append({"id": uuid.uuid4().hex, "date": fallback_date, "status": status, "note": ""})
    return sorted(timeline, key=lambda event: event["date"])


def _future_date(value: Any, today: date) -> str | None:
    """Return an ISO date only when it is a real date later than ``today``."""
    value = str(value or "").strip()
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return value if parsed > today else None


def find_future_paper_date_issues(
    papers: list[dict[str, Any]],
    today: date | None = None,
) -> list[dict[str, Any]]:
    """List legacy/new submission dates that are later than today.

    The function is intentionally read-only.  It lets the UI make historical
    data visible before the user chooses the one-click correction, instead of
    silently changing a past record during application startup.
    """
    today = today or date.today()
    issues: list[dict[str, Any]] = []
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        paper_id = str(paper.get("id", ""))
        paper_title = str(paper.get("title", "未命名论文"))
        journals = paper.get("journals", [])
        for journal in journals if isinstance(journals, list) else []:
            if not isinstance(journal, dict):
                continue
            journal_id = str(journal.get("id", ""))
            journal_name = str(journal.get("name", "未填写期刊"))
            for field, label in (("date", "投稿日期"), ("status_updated_at", "状态更新时间")):
                value = _future_date(journal.get(field), today)
                if value:
                    issues.append(
                        {
                            "paper_id": paper_id,
                            "paper_title": paper_title,
                            "journal_id": journal_id,
                            "journal_name": journal_name,
                            "field": field,
                            "label": label,
                            "date": value,
                        }
                    )
            timeline = journal.get("timeline", [])
            for event in timeline if isinstance(timeline, list) else []:
                if not isinstance(event, dict):
                    continue
                value = _future_date(event.get("date"), today)
                if value:
                    issues.append(
                        {
                            "paper_id": paper_id,
                            "paper_title": paper_title,
                            "journal_id": journal_id,
                            "journal_name": journal_name,
                            "event_id": str(event.get("id", "")),
                            "field": "timeline.date",
                            "label": "时间线日期",
                            "date": value,
                        }
                    )
    return issues


def repair_future_paper_dates(
    papers: list[dict[str, Any]],
    today: date | None = None,
) -> dict[str, int]:
    """Replace only invalid future submission/status/timeline dates with today.

    Future revision deadlines are intentional and must never be touched.  The
    caller owns the supplied list; all paper IDs, journal IDs, notes, files and
    other history remain exactly as they were.
    """
    today = today or date.today()
    today_key = today.isoformat()
    counts = {"submission_dates": 0, "status_updated_dates": 0, "timeline_dates": 0}
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        journals = paper.get("journals", [])
        for journal in journals if isinstance(journals, list) else []:
            if not isinstance(journal, dict):
                continue
            if _future_date(journal.get("date"), today):
                journal["date"] = today_key
                counts["submission_dates"] += 1
            if _future_date(journal.get("status_updated_at"), today):
                journal["status_updated_at"] = today_key
                counts["status_updated_dates"] += 1
            timeline = journal.get("timeline", [])
            for event in timeline if isinstance(timeline, list) else []:
                if isinstance(event, dict) and _future_date(event.get("date"), today):
                    event["date"] = today_key
                    counts["timeline_dates"] += 1
    counts["total"] = sum(counts.values())
    return counts


def _normalize_paper_keywords(raw: Any) -> list[str]:
    if isinstance(raw, str):
        values = raw.replace("，", ",").split(",")
    elif isinstance(raw, list):
        values = raw
    else:
        values = []
    return [str(value).strip() for value in values if str(value).strip()]


def _normalize_paper_files(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    files: list[dict[str, str]] = []
    for item in raw:
        if isinstance(item, str):
            path = item.strip()
            item = {"path": path}
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip()
        if not path:
            continue
        kind = str(item.get("kind", "file"))
        files.append(
            {
                "id": str(item.get("id") or uuid.uuid4().hex),
                "name": str(item.get("name", "")).strip() or Path(path).name or path,
                "path": path,
                "kind": "folder" if kind == "folder" else "file",
            }
        )
    return files


def _normalize_pdf_files(raw: Any) -> list[dict[str, str]]:
    """Keep explicit local PDF links only; files are never copied into data."""
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in _normalize_paper_files(raw):
        path = str(item.get("path", "")).strip()
        if str(item.get("kind", "file")) != "file" or Path(path).suffix.casefold() != ".pdf":
            continue
        key = path.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append({**item, "kind": "file"})
    return result


def _normalize_paper_ideas(raw: Any) -> list[dict[str, str]]:
    """Keep ideas attached to a paper without turning them into a database."""
    entries = raw if isinstance(raw, list) else []
    result: list[dict[str, str]] = []
    for entry in entries:
        if isinstance(entry, str):
            entry = {"text": entry}
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("text", "")).strip()
        if not text:
            continue
        result.append(
            {
                "id": str(entry.get("id") or uuid.uuid4().hex),
                "text": text,
                "created_at": str(entry.get("created_at", "")).strip(),
                "source_inspiration_id": str(entry.get("source_inspiration_id", "")).strip(),
            }
        )
    return result[-50:]


PAPER_JOURNAL_PUBLISHER_ORDER = ("elsevier", "springer", "taylor", "wiley")


def _paper_journal_sort_key(journal: dict[str, Any]) -> tuple[int, str, str]:
    """Keep each paper's journal history in a stable, familiar publisher order."""
    publisher = str(journal.get("publisher", "")).strip().casefold()
    if "elsevier" in publisher or (publisher.startswith("els") and "vier" in publisher):
        rank = 0
    elif "springer" in publisher:
        rank = 1
    elif "taylor" in publisher:
        rank = 2
    elif "wiley" in publisher:
        rank = 3
    else:
        rank = len(PAPER_JOURNAL_PUBLISHER_ORDER)
    return rank, publisher, str(journal.get("name", "")).strip().casefold()


def _sort_paper_journals(journals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(journals, key=_paper_journal_sort_key)


def normalize_paper(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize old one-journal records into the current nested format."""
    raw_journals = item.get("journals")
    journals: list[dict[str, Any]] = []
    if isinstance(raw_journals, list):
        for raw in raw_journals:
            if not isinstance(raw, dict):
                continue
            status = str(raw.get("status", "准备投稿"))
            submitted_date = str(raw.get("date", ""))
            # Older releases had only ``date`` (or overwrote it after every
            # status change).  Preserve that date as both values on migration;
            # new records keep the original submission date and a separate
            # status-update clock.
            status_updated_at = str(raw.get("status_updated_at", "")).strip() or submitted_date
            journals.append(
                {
                    "id": str(raw.get("id") or uuid.uuid4().hex),
                    "name": str(raw.get("name", raw.get("journal", ""))),
                    "publisher": str(raw.get("publisher", "")),
                    "date": submitted_date,
                    "status_updated_at": status_updated_at,
                    "status": status,
                    "revision_due_date": str(raw.get("revision_due_date", "")).strip(),
                    "timeline": _normalize_timeline(raw.get("timeline"), status, submitted_date or status_updated_at),
                    "result": str(raw.get("result", "")),
                    "notes": str(raw.get("notes", "")),
                    "special_issue_id": str(raw.get("special_issue_id", "")).strip(),
                    "special_issue_title": str(raw.get("special_issue_title", "")).strip(),
                    "special_issue_deadline": str(raw.get("special_issue_deadline", "")).strip(),
                    "special_issue_url": str(raw.get("special_issue_url", "")).strip(),
                }
            )
    elif item.get("journal") or item.get("publisher"):
        status = str(item.get("status", "准备投稿"))
        submitted_date = str(item.get("date", ""))
        status_updated_at = str(item.get("status_updated_at", "")).strip() or submitted_date
        journals.append(
            {
                "id": uuid.uuid4().hex,
                "name": str(item.get("journal", "")),
                "publisher": str(item.get("publisher", "")),
                "date": submitted_date,
                "status_updated_at": status_updated_at,
                "status": status,
                "revision_due_date": str(item.get("revision_due_date", "")).strip(),
                "timeline": _normalize_timeline(item.get("timeline"), status, submitted_date or status_updated_at),
                "result": str(item.get("result", "")),
                "notes": str(item.get("notes", "")),
            }
        )
    # Retain fields introduced by prior releases or user-side extensions.  A
    # normalizer owns only the canonical paper fields below; dropping unknown
    # values during a future upgrade would silently destroy local history.
    normalized = dict(item)
    normalized.update(
        {
        "id": str(item.get("id") or uuid.uuid4().hex),
        "title": str(item.get("title", "")),
        "keywords": _normalize_paper_keywords(item.get("keywords", [])),
        "summary": str(item.get("summary", item.get("abstract", ""))).strip()[:3000],
        "files": _normalize_paper_files(item.get("files", [])),
        "ideas": _normalize_paper_ideas(item.get("ideas", [])),
        "created_at": str(item.get("created_at", "")).strip(),
        "journals": _sort_paper_journals(journals),
        }
    )
    return normalized


ACHIEVEMENT_CATEGORIES = ("论文", "专利", "奖项", "其他")
TERMINAL_PAPER_STATUSES = {"已接收", "已发表"}


def _normalize_achievement(item: dict[str, Any]) -> dict[str, Any] | None:
    title = str(item.get("title", "")).strip()
    if not title:
        return None
    category = str(item.get("category", "论文")).strip()
    if category not in ACHIEVEMENT_CATEGORIES:
        category = "其他"
    paper = item.get("paper")
    return {
        "id": str(item.get("id") or uuid.uuid4().hex),
        "category": category,
        "title": title,
        "date": str(item.get("date", "")).strip(),
        "venue": str(item.get("venue", "")).strip(),
        "status": str(item.get("status", "")).strip(),
        "identifier": str(item.get("identifier", "")).strip(),
        "notes": str(item.get("notes", "")).strip(),
        "files": _normalize_paper_files(item.get("files", [])),
        "pdf_files": _normalize_pdf_files(item.get("pdf_files", [])),
        "source": str(item.get("source", "手动添加")).strip() or "手动添加",
        "source_paper_id": str(item.get("source_paper_id", "")).strip(),
        "created_at": str(item.get("created_at", "")).strip() or date.today().isoformat(),
        "paper": normalize_paper(paper) if isinstance(paper, dict) else {},
    }


def load_achievements() -> list[dict[str, Any]]:
    payload = _read_json(ACHIEVEMENTS_FILE, [])
    if not isinstance(payload, list):
        return []
    return [entry for entry in (_normalize_achievement(item) for item in payload if isinstance(item, dict)) if entry]


def save_achievements(items: list[dict[str, Any]]) -> None:
    normalized = [entry for entry in (_normalize_achievement(item) for item in items if isinstance(item, dict)) if entry]
    _write_json(ACHIEVEMENTS_FILE, normalized)


def load_achievement_pdf_cache() -> dict[str, Any]:
    """Load derived PDF reading notes.  Missing/corrupt cache never affects outcomes."""
    payload = _read_json(PDF_RESEARCH_CACHE_FILE, {})
    if not isinstance(payload, dict):
        return {"version": 1, "entries": {}}
    entries = payload.get("entries", {})
    entries = entries if isinstance(entries, dict) else {}
    return {"version": 1, "entries": entries}


def save_achievement_pdf_cache(payload: dict[str, Any]) -> None:
    entries = payload.get("entries", {}) if isinstance(payload, dict) else {}
    entries = entries if isinstance(entries, dict) else {}
    _write_json(PDF_RESEARCH_CACHE_FILE, {"version": 1, "entries": entries})


def _normalize_rejection_archive(item: dict[str, Any]) -> dict[str, Any] | None:
    title = str(item.get("paper_title", item.get("title", ""))).strip()
    journal_name = str(item.get("journal_name", item.get("journal", ""))).strip()
    if not title or not journal_name:
        return None
    journal = item.get("journal_data") if isinstance(item.get("journal_data"), dict) else {}
    return {
        "id": str(item.get("id") or uuid.uuid4().hex),
        "paper_id": str(item.get("paper_id", "")).strip(),
        "paper_title": title,
        "journal_id": str(item.get("journal_id", journal.get("id", ""))).strip(),
        "journal_name": journal_name,
        "publisher": str(item.get("publisher", journal.get("publisher", ""))).strip(),
        "rejected_date": str(
            item.get("rejected_date", journal.get("status_updated_at", journal.get("date", "")))
        ).strip(),
        "archived_at": str(item.get("archived_at", "")).strip() or date.today().isoformat(),
        "result": str(item.get("result", journal.get("result", ""))).strip(),
        "notes": str(item.get("notes", journal.get("notes", ""))).strip(),
        "journal_data": dict(journal),
    }


def load_rejection_archive() -> list[dict[str, Any]]:
    payload = _read_json(REJECTION_ARCHIVE_FILE, [])
    if not isinstance(payload, list):
        return []
    result = [entry for entry in (_normalize_rejection_archive(item) for item in payload if isinstance(item, dict)) if entry]
    return sorted(result, key=lambda item: (str(item.get("archived_at", "")), str(item.get("rejected_date", ""))), reverse=True)


def save_rejection_archive(items: list[dict[str, Any]]) -> None:
    normalized = [entry for entry in (_normalize_rejection_archive(item) for item in items if isinstance(item, dict)) if entry]
    _write_json(REJECTION_ARCHIVE_FILE, normalized)


def _normalize_selection_feedback(raw: Any) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    raw_entries = raw.get("entries", {})
    entries: dict[str, dict[str, str]] = {}
    if isinstance(raw_entries, dict):
        for key, value in raw_entries.items():
            value = value if isinstance(value, dict) else {}
            label = str(value.get("label", "")).strip()
            if label not in {"适合", "不适合", "暂不考虑"}:
                continue
            entries[str(key)] = {
                "label": label,
                "updated_at": str(value.get("updated_at", "")).strip(),
                "journal_name": str(value.get("journal_name", "")).strip(),
            }
    raw_terms = raw.get("term_weights", {})
    term_weights: dict[str, int] = {}
    if isinstance(raw_terms, dict):
        for term, value in raw_terms.items():
            normalized_term = str(term).strip().casefold()
            if normalized_term:
                term_weights[normalized_term] = _integer(value, 0, -12, 12)
    return {"entries": entries, "term_weights": term_weights}


def load_journal_selection_feedback() -> dict[str, Any]:
    return _normalize_selection_feedback(_read_json(SELECTION_FEEDBACK_FILE, {}))


def save_journal_selection_feedback(value: dict[str, Any]) -> None:
    _write_json(SELECTION_FEEDBACK_FILE, _normalize_selection_feedback(value))


def selection_feedback_for(paper_id: str, journal_id: str) -> str:
    entry = load_journal_selection_feedback().get("entries", {}).get(f"{paper_id}:{journal_id}", {})
    return str(entry.get("label", "")) if isinstance(entry, dict) else ""


def set_journal_selection_feedback(
    paper_id: str,
    journal_id: str,
    label: str,
    terms: list[str] | None = None,
    *,
    journal_name: str = "",
) -> None:
    """Persist a paper-specific judgement and gently learn from its matched terms."""
    label = str(label).strip()
    if label not in {"适合", "不适合", "暂不考虑"}:
        raise ValueError("不支持的选刊反馈")
    data = load_journal_selection_feedback()
    entries = data["entries"]
    key = f"{paper_id}:{journal_id}"
    previous = str(entries.get(key, {}).get("label", ""))
    entries[key] = {
        "label": label,
        "updated_at": date.today().isoformat(),
        "journal_name": str(journal_name or entries.get(key, {}).get("journal_name", "")).strip(),
    }
    effect = {"适合": 2, "暂不考虑": -1, "不适合": -4}
    delta = effect[label] - effect.get(previous, 0)
    if delta:
        weights = data["term_weights"]
        for term in terms or []:
            normalized_term = str(term).strip().casefold()
            if normalized_term:
                weights[normalized_term] = max(-12, min(12, _integer(weights.get(normalized_term), 0) + delta))
    save_journal_selection_feedback(data)


def selection_excluded_journal_names(paper_id: str) -> list[str]:
    """Return the named journals explicitly excluded for one manuscript."""
    prefix = f"{str(paper_id).strip()}:"
    result: list[str] = []
    seen: set[str] = set()
    for key, value in load_journal_selection_feedback().get("entries", {}).items():
        if not str(key).startswith(prefix) or not isinstance(value, dict) or value.get("label") != "不适合":
            continue
        name = str(value.get("journal_name", "")).strip()
        folded = name.casefold()
        if name and folded not in seen:
            seen.add(folded)
            result.append(name)
    return result


def _new_terminal_journal(previous: dict[str, Any] | None, paper: dict[str, Any]) -> dict[str, Any] | None:
    prior = {
        str(journal.get("id", "")): str(journal.get("status", ""))
        for journal in (previous or {}).get("journals", [])
        if isinstance(journal, dict)
    }
    candidates = [
        journal
        for journal in paper.get("journals", [])
        if str(journal.get("status", "")) in TERMINAL_PAPER_STATUSES
        and prior.get(str(journal.get("id", "")), "") not in TERMINAL_PAPER_STATUSES
    ]
    if not candidates:
        return None
    return next((item for item in candidates if str(item.get("status", "")) == "已发表"), candidates[0])


def _newly_rejected(previous: dict[str, Any] | None, journal: dict[str, Any]) -> bool:
    if str(journal.get("status", "")) != "拒稿":
        return False
    prior = {
        str(item.get("id", "")): str(item.get("status", ""))
        for item in (previous or {}).get("journals", [])
        if isinstance(item, dict)
    }
    return prior.get(str(journal.get("id", "")), "") != "拒稿"


def _achievement_from_paper(paper: dict[str, Any], terminal_journal: dict[str, Any]) -> dict[str, Any]:
    status = str(terminal_journal.get("status", "已接收"))
    return {
        "id": uuid.uuid4().hex,
        "category": "论文",
        "title": str(paper.get("title", "未命名论文")),
        "date": str(terminal_journal.get("status_updated_at", "")).strip() or str(terminal_journal.get("date", "")),
        "venue": str(terminal_journal.get("name", "")),
        "status": status,
        "identifier": "",
        "notes": str(terminal_journal.get("notes", "")),
        "files": list(paper.get("files", [])),
        "pdf_files": _normalize_pdf_files(paper.get("files", [])),
        "source": "投稿记录自动归档",
        "source_paper_id": str(paper.get("id", "")),
        "created_at": date.today().isoformat(),
        "paper": dict(paper),
    }


def _rejection_archive_from_journal(paper: dict[str, Any], journal: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": uuid.uuid4().hex,
        "paper_id": str(paper.get("id", "")),
        "paper_title": str(paper.get("title", "未命名论文")),
        "journal_id": str(journal.get("id", "")),
        "journal_name": str(journal.get("name", "未填写期刊")),
        "publisher": str(journal.get("publisher", "")),
        "rejected_date": str(journal.get("status_updated_at", "")).strip() or str(journal.get("date", "")),
        "archived_at": date.today().isoformat(),
        "result": str(journal.get("result", "")),
        "notes": str(journal.get("notes", "")),
        "journal_data": dict(journal),
    }


def save_papers(items: list[dict[str, Any]]) -> dict[str, int | bool]:
    """Save submissions and apply their two explicit lifecycle rules.

    A newly accepted/published journal moves the whole manuscript to results.
    A newly rejected journal alone moves to the rejection archive.  Centralising
    this here keeps full editing, the action centre and quick capture in sync.
    """
    normalized = [normalize_paper(item) for item in items if isinstance(item, dict)]
    future_issues = find_future_paper_date_issues(normalized)
    if future_issues:
        # Never turn a legacy anomaly into an invisible mutation.  The Paper
        # page provides a one-click, user-confirmed repair path.
        return {
            "saved": False,
            "future_date_issue_count": len(future_issues),
            "achievement_count": 0,
            "rejection_archive_count": 0,
        }

    before_by_id = {str(paper.get("id", "")): paper for paper in load_papers()}
    achievements = load_achievements()
    rejection_archive = load_rejection_archive()
    existing_achievement_ids = {str(item.get("source_paper_id", "")) for item in achievements if str(item.get("source_paper_id", ""))}
    existing_rejections = {
        (str(item.get("paper_id", "")), str(item.get("journal_id", "")))
        for item in rejection_archive
    }
    retained: list[dict[str, Any]] = []
    achievement_count = 0
    rejected_count = 0
    for paper in normalized:
        previous = before_by_id.get(str(paper.get("id", "")))
        terminal_journal = _new_terminal_journal(previous, paper)
        if terminal_journal is not None:
            paper_id = str(paper.get("id", ""))
            if paper_id not in existing_achievement_ids:
                achievements.insert(0, _achievement_from_paper(paper, terminal_journal))
                existing_achievement_ids.add(paper_id)
                achievement_count += 1
            continue
        journals: list[dict[str, Any]] = []
        for journal in paper.get("journals", []):
            if _newly_rejected(previous, journal):
                key = (str(paper.get("id", "")), str(journal.get("id", "")))
                if key not in existing_rejections:
                    rejection_archive.insert(0, _rejection_archive_from_journal(paper, journal))
                    existing_rejections.add(key)
                    rejected_count += 1
                continue
            journals.append(journal)
        paper["journals"] = _sort_paper_journals(journals)
        retained.append(paper)

    # Keep the in-memory list in step with the persisted list, so every page
    # immediately drops a manuscript that just became an achievement.
    items[:] = retained
    _write_json(PAPERS_FILE, retained)
    if achievement_count:
        save_achievements(achievements)
    if rejected_count:
        save_rejection_archive(rejection_archive)
    sync_journal_library_from_papers(retained)
    return {
        "saved": True,
        "future_date_issue_count": 0,
        "achievement_count": achievement_count,
        "rejection_archive_count": rejected_count,
    }


def _journal_key(name: str, publisher: str = "") -> str:
    return f"{name.strip().casefold()}|{publisher.strip().casefold()}"


def _normalize_fields(value: Any) -> list[str]:
    if isinstance(value, str):
        values = value.replace("，", ",").split(",")
    elif isinstance(value, list):
        values = value
    else:
        values = []
    return [str(field).strip() for field in values if str(field).strip()]


def _normalize_jcr_metric(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    quartile = str(raw.get("quartile", "")).strip().upper()
    if quartile not in {"Q1", "Q2", "Q3", "Q4"}:
        return None
    return {
        "quartile": quartile,
        "year": _integer(raw.get("year"), 0, 0, 2100),
        "category": str(raw.get("category", "")).strip()[:180],
        "jif": str(raw.get("jif", "")).strip()[:30],
        "rank": str(raw.get("rank", "")).strip()[:24],
        "total": str(raw.get("total", "")).strip()[:24],
    }


def _normalize_jcr_info(raw: Any, item: dict[str, Any]) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    metrics = raw.get("metrics", [])
    metrics = metrics if isinstance(metrics, list) else []
    normalized_metrics = [_normalize_jcr_metric(metric) for metric in metrics]
    normalized_metrics = [metric for metric in normalized_metrics if metric is not None]
    if not normalized_metrics:
        # Accept compact manual fields from an early user record without
        # presenting it as an API-verified JCR result.
        legacy = _normalize_jcr_metric(
            {
                "quartile": item.get("jcr_quartile", ""),
                "year": item.get("jcr_year", 0),
                "category": item.get("jcr_category", ""),
            }
        )
        if legacy is not None:
            normalized_metrics = [legacy]
    status = str(raw.get("status", "")).strip()
    if status not in {"pending", "verified", "not_found", "manual", "ai_estimated"}:
        status = "manual" if normalized_metrics else "pending"
    if status == "verified" and not normalized_metrics:
        status = "not_found"
    return {
        "status": status,
        "source": str(raw.get("source", "")).strip()[:80],
        "checked_at": str(raw.get("checked_at", "")).strip(),
        "metrics": normalized_metrics[:12],
        "confidence": str(raw.get("confidence", "")).strip()[:24],
        "note": str(raw.get("note", "")).strip()[:220],
    }


def _normalize_easyscholar_info(raw: Any) -> dict[str, Any]:
    """Normalize optional third-party metrics without treating them as user data."""
    raw = raw if isinstance(raw, dict) else {}

    def as_bool(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().casefold() in {"1", "true", "yes", "y", "是"}
        return bool(value)

    return {
        "source": str(raw.get("source", "")).strip()[:80],
        "checked_at": str(raw.get("checked_at", "")).strip()[:32],
        "query_signature": str(raw.get("query_signature", "")).strip()[:80],
        "cas_upgrade": str(raw.get("cas_upgrade", "")).strip()[:32],
        "cas_upgrade_top": str(raw.get("cas_upgrade_top", "")).strip()[:32],
        "cas_upgrade_small": str(raw.get("cas_upgrade_small", "")).strip()[:32],
        "cas_basic": str(raw.get("cas_basic", "")).strip()[:32],
        "impact_factor": str(raw.get("impact_factor", "")).strip()[:32],
        "impact_factor_5y": str(raw.get("impact_factor_5y", "")).strip()[:32],
        "ei": as_bool(raw.get("ei", False)),
        "esci": as_bool(raw.get("esci", False)),
        "jci": str(raw.get("jci", "")).strip()[:32],
    }


def normalize_library_journal(item: dict[str, Any]) -> dict[str, Any]:
    frontier_priority = str(item.get("frontier_priority", ""))
    if frontier_priority not in {"不订阅", "必看", "关注", "扩展"}:
        frontier_priority = "必看" if item.get("favorite", False) else "不订阅"
    journal_id = str(item.get("id") or uuid.uuid4().hex)
    name = str(item.get("name", "")).strip()
    publisher = canonical_publisher(str(item.get("publisher", "")))
    issn = str(item.get("issn", "")).strip()
    # Correct a one-time ambiguous Crossref match from older releases.
    if journal_id == "catalog-land" and name.casefold() == "landing":
        name, publisher, issn = "Land", "MDPI", "2073-445X"
    raw_provenance = item.get("provenance", {})
    raw_provenance = raw_provenance if isinstance(raw_provenance, dict) else {}
    raw_frontier_score = item.get("frontier_score")
    try:
        frontier_score = max(0, min(100, int(raw_frontier_score))) if raw_frontier_score is not None and raw_frontier_score != "" else None
    except (TypeError, ValueError):
        frontier_score = None
    normalized = dict(item)
    normalized.update(
        {
        "id": journal_id,
        "name": name,
        "publisher": publisher,
        "issn": issn,
        "fields": _normalize_fields(item.get("fields", [])),
        "ai_tags": _normalize_fields(item.get("ai_tags", [])),
        "website": str(item.get("website", "")).strip(),
        "notes": str(item.get("notes", "")).strip(),
        "ai_scope_cn": str(item.get("ai_scope_cn", "")).strip()[:180],
        "ai_fit_cn": str(item.get("ai_fit_cn", "")).strip()[:220],
        "ai_risks_cn": str(item.get("ai_risks_cn", "")).strip()[:180],
        "ai_model": str(item.get("ai_model", "")).strip()[:80],
        "ai_updated_at": str(item.get("ai_updated_at", "")).strip(),
        "ai_source_signature": str(item.get("ai_source_signature", "")).strip()[:80],
        "ai_auto_pending": bool(item.get("ai_auto_pending", False)),
        "jcr": _normalize_jcr_info(item.get("jcr"), item),
        "easyscholar": _normalize_easyscholar_info(item.get("easyscholar")),
        "favorite": bool(item.get("favorite", False)),
        "frontier_priority": frontier_priority,
        "metadata_updated_at": str(item.get("metadata_updated_at", "")).strip(),
        "metadata_source_signature": str(item.get("metadata_source_signature", "")).strip()[:80],
        "metadata_dirty": bool(item.get("metadata_dirty", False)),
        "user_quality_flag": str(item.get("user_quality_flag", "")).strip()[:40],
        "jcr_locked": bool(item.get("jcr_locked", False)),
        "provenance": {
            "kind": str(raw_provenance.get("kind", "")).strip()[:40],
            "first_item_id": str(raw_provenance.get("first_item_id", "")).strip()[:80],
            "source_url": str(raw_provenance.get("source_url", "")).strip()[:600],
            "discovered_at": str(raw_provenance.get("discovered_at", "")).strip()[:32],
        },
        "created_at": str(item.get("created_at", "")).strip() or date.today().isoformat(),
        "last_used_at": str(item.get("last_used_at", "")).strip(),
        }
    )
    if frontier_score is None:
        normalized.pop("frontier_score", None)
    else:
        normalized["frontier_score"] = frontier_score
    return normalized


def load_journal_library() -> list[dict[str, Any]]:
    payload = _read_json(JOURNALS_FILE, [])
    if not isinstance(payload, list) or not payload:
        # A new installation starts with a compact, useful land-science library.
        return [normalize_library_journal(item) for item in default_land_science_catalog()]
    normalized = [normalize_library_journal(item) for item in payload if isinstance(item, dict) and str(item.get("name", "")).strip()]
    # One-time, lossless local migration for publisher-family grouping and old
    # metadata corrections. Personal tags, notes and submission history remain intact.
    if normalized != payload:
        _write_json(JOURNALS_FILE, normalized)
    return normalized


def save_journal_library(items: list[dict[str, Any]]) -> None:
    normalized = [normalize_library_journal(item) for item in items if isinstance(item, dict) and str(item.get("name", "")).strip()]
    _write_json(JOURNALS_FILE, normalized)


def journal_usage_index(papers: list[dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
    """Summarize how often each journal occurs in the paper history."""
    index: dict[str, dict[str, Any]] = {}
    for paper in papers if papers is not None else load_papers():
        title = str(paper.get("title", "")).strip()
        for journal in paper.get("journals", []):
            name = str(journal.get("name", "")).strip()
            publisher = str(journal.get("publisher", "")).strip()
            if not name:
                continue
            key = _journal_key(name, publisher)
            entry = index.setdefault(
                key,
                {"submission_count": 0, "paper_titles": set(), "last_used_at": ""},
            )
            entry["submission_count"] += 1
            if title:
                entry["paper_titles"].add(title)
            submitted = str(journal.get("date", ""))
            if submitted > entry["last_used_at"]:
                entry["last_used_at"] = submitted
    for entry in index.values():
        entry["paper_titles"] = sorted(entry["paper_titles"])
    return index


def sync_journal_library_from_papers(papers: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Ensure every submitted journal also exists in the personal journal library."""
    library = load_journal_library()
    changed = False
    for paper in papers if papers is not None else load_papers():
        for journal in paper.get("journals", []):
            name = str(journal.get("name", "")).strip()
            publisher = str(journal.get("publisher", "")).strip()
            if not name:
                continue
            exact_key = _journal_key(name, publisher)
            name_key = name.casefold()
            index = next(
                (
                    position
                    for position, item in enumerate(library)
                    if _journal_key(item.get("name", ""), item.get("publisher", "")) == exact_key
                    or str(item.get("name", "")).strip().casefold() == name_key
                ),
                -1,
            )
            submitted = str(journal.get("date", "")).strip()
            if index < 0:
                library.append(
                    {
                        "id": uuid.uuid4().hex,
                        "name": name,
                        "publisher": publisher,
                        "fields": [],
                        "website": "",
                        "notes": "",
                        "favorite": False,
                        "ai_auto_pending": True,
                        "created_at": date.today().isoformat(),
                        "last_used_at": submitted,
                    }
                )
                changed = True
                continue
            item = library[index]
            if not item.get("publisher") and publisher:
                item["publisher"] = publisher
                changed = True
            if submitted > str(item.get("last_used_at", "")):
                item["last_used_at"] = submitted
                changed = True
    if changed:
        save_journal_library(library)
    return library


def create_backup(name: str | None = None, automatic: bool = False) -> Path:
    """Copy the JSON data files into a dated, portable snapshot directory."""
    if name is None:
        name = f"{'auto' if automatic else 'manual'}-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}"
    safe_name = Path(name).name
    if safe_name != name:
        raise ValueError("Invalid backup name")
    target = BACKUP_DIR / safe_name
    if target.exists():
        return target
    target.mkdir(parents=True, exist_ok=False)
    files: list[str] = []
    for filename in BACKUP_FILE_NAMES:
        source = DATA_DIR / filename
        if source.exists():
            shutil.copy2(source, target / filename)
            files.append(filename)
    _write_json(
        target / "manifest.json",
        {"created_at": datetime.now().isoformat(timespec="seconds"), "automatic": automatic, "files": files},
    )
    _trim_auto_backups()
    return target


def _trim_auto_backups(limit: int = 30) -> None:
    if not BACKUP_DIR.exists():
        return
    automatic = sorted((path for path in BACKUP_DIR.iterdir() if path.is_dir() and path.name.startswith("auto-")), reverse=True)
    for path in automatic[limit:]:
        shutil.rmtree(path, ignore_errors=True)


def maybe_create_daily_backup(settings: dict[str, Any]) -> Path | None:
    if not bool(settings.get("auto_backup", False)):
        return None
    return create_backup(f"auto-{date.today().isoformat()}", automatic=True)


def list_backups() -> list[dict[str, Any]]:
    if not BACKUP_DIR.exists():
        return []
    backups: list[dict[str, Any]] = []
    for path in BACKUP_DIR.iterdir():
        if not path.is_dir():
            continue
        manifest = _read_json(path / "manifest.json", {})
        if not isinstance(manifest, dict):
            continue
        files = manifest.get("files", [])
        backups.append(
            {
                "name": path.name,
                "created_at": str(manifest.get("created_at", path.name)),
                "automatic": bool(manifest.get("automatic", False)),
                "file_count": len(files) if isinstance(files, list) else 0,
            }
        )
    return sorted(backups, key=lambda item: item["name"], reverse=True)


def restore_backup(name: str) -> None:
    safe_name = Path(name).name
    if safe_name != name:
        raise ValueError("Invalid backup name")
    source_dir = BACKUP_DIR / safe_name
    manifest = _read_json(source_dir / "manifest.json", {})
    if not source_dir.is_dir() or not isinstance(manifest, dict):
        raise FileNotFoundError("Backup not found")
    files = manifest.get("files", [])
    if not isinstance(files, list):
        raise ValueError("Invalid backup manifest")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    backed_up_files = {str(filename) for filename in files if str(filename) in BACKUP_FILE_NAMES}
    for filename in BACKUP_FILE_NAMES:
        target = DATA_DIR / filename
        if filename not in backed_up_files:
            if target.exists():
                target.unlink()
            continue
        source = source_dir / filename
        if source.is_file():
            shutil.copy2(source, target)


def data_location() -> Path:
    """Return the active data directory for display in the About dialog."""
    return DATA_DIR


def change_data_location(destination: str | Path) -> Path:
    """Copy active local data to a new folder, then make it the persistent store.

    The previous folder is intentionally retained as a recovery copy. A target
    containing files with the same names is rejected so this never overwrites
    an unrelated or existing research-assistant data set.
    """
    target = Path(destination).expanduser().resolve()
    if target.exists() and not target.is_dir():
        raise ValueError("保存位置必须是文件夹")
    source = DATA_DIR.resolve()
    if target == source:
        return source
    if source in target.parents or target in source.parents:
        raise ValueError("请选择当前数据目录之外的独立文件夹")
    target.mkdir(parents=True, exist_ok=True)
    source_items = list(source.iterdir()) if source.is_dir() else []
    conflicts = [item.name for item in source_items if (target / item.name).exists()]
    if conflicts:
        raise FileExistsError(f"目标位置已有同名数据：{', '.join(conflicts[:3])}")
    try:
        for item in source_items:
            destination_item = target / item.name
            if item.is_dir():
                shutil.copytree(item, destination_item)
            else:
                shutil.copy2(item, destination_item)
        _save_storage_location(target)
    except Exception:
        # The old location remains active unless every copy and the pointer
        # update succeeds, so an interrupted migration cannot lose data.
        raise
    _set_data_dir(target)
    return target


def import_legacy_data_directory(source_directory: str | Path) -> int:
    """Import a legacy ``data`` folder into the active store safely.

    A pre-import snapshot is always created first.  Only research records are
    copied; window preferences stay local to the new installation so a legacy
    fixed position or click-through state cannot make the new widget vanish.
    """
    source = Path(source_directory).expanduser().resolve()
    if not source.is_dir():
        raise ValueError("请选择旧版的 data 文件夹")
    if source == DATA_DIR.resolve():
        raise ValueError("这已经是当前数据文件夹")
    files = [name for name in BACKUP_FILE_NAMES if (source / name).is_file()]
    if not files:
        raise ValueError("所选文件夹中没有可导入的科研助手数据")
    try:
        create_backup(f"before-import-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}")
    except OSError:
        # The import is still safe because every individual copy below uses a
        # temporary target; report an actual copy failure rather than refusing
        # a perfectly usable legacy directory because its first backup failed.
        pass
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name in files:
        target = DATA_DIR / name
        temporary = target.with_suffix(target.suffix + ".importing")
        shutil.copy2(source / name, temporary)
        temporary.replace(target)
    return len(files)
