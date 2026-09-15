"""Select and migrate the v12 personal-data root before persistence imports."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from utils.app_info import APP_NAME


class StorageBootstrapError(RuntimeError):
    """Raised when a data root cannot be validated or copied safely."""


def default_user_data_dir(local_app_data: Path) -> Path:
    return Path(local_app_data).expanduser() / APP_NAME / "UserData"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _record_count(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("tasks", "items", "records", "data", "terms"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
        return int(bool(payload))
    return 0


def build_data_manifest(directory: Path) -> dict[str, Any]:
    """Hash every regular file and validate every JSON document."""
    root = Path(directory).expanduser()
    files: dict[str, dict[str, Any]] = {}
    total_records = 0
    total_bytes = 0
    if not root.is_dir():
        return {"root": str(root), "files": files, "record_count": 0, "byte_count": 0}

    paths = sorted(
        (path for path in root.rglob("*") if path.is_file() and not path.is_symlink()),
        key=lambda path: path.relative_to(root).as_posix().casefold(),
    )
    for path in paths:
        relative = path.relative_to(root).as_posix()
        record_count = 0
        if path.suffix.casefold() == ".json":
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise StorageBootstrapError(f"无法验证 {relative}: {error}") from error
            record_count = _record_count(payload)
        size = path.stat().st_size
        files[relative] = {
            "size": size,
            "sha256": _file_sha256(path),
            "record_count": record_count,
        }
        total_records += record_count
        total_bytes += size
    return {
        "root": str(root),
        "files": files,
        "record_count": total_records,
        "byte_count": total_bytes,
    }


def _matching_file_manifests(source: dict[str, Any], destination: dict[str, Any]) -> bool:
    source_files = source.get("files", {})
    destination_files = destination.get("files", {})
    if source_files.keys() != destination_files.keys():
        return False
    return all(
        source_files[name].get("size") == destination_files[name].get("size")
        and source_files[name].get("sha256") == destination_files[name].get("sha256")
        for name in source_files
    )


def copy_data_store_verified(source: Path, destination: Path) -> dict[str, Any]:
    """Copy a complete store through a sibling directory and verify each byte."""
    source = Path(source).expanduser()
    destination = Path(destination).expanduser()
    if not source.is_dir():
        raise StorageBootstrapError(f"源数据目录不存在: {source}")
    if source.resolve() == destination.resolve():
        manifest = build_data_manifest(source)
        return {
            "source": str(source),
            "destination": str(destination),
            "files": {
                name: {
                    "source_sha256": info["sha256"],
                    "destination_sha256": info["sha256"],
                    "size": info["size"],
                }
                for name, info in manifest["files"].items()
            },
        }

    source_manifest = build_data_manifest(source)
    if destination.exists() and any(destination.iterdir()):
        raise StorageBootstrapError(f"目标数据目录已有内容，未覆盖: {destination}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f"{destination.name}.migrating-{uuid.uuid4().hex}")
    try:
        shutil.copytree(source, temporary, copy_function=shutil.copy2)
        temporary_manifest = build_data_manifest(temporary)
        if not _matching_file_manifests(source_manifest, temporary_manifest):
            raise StorageBootstrapError("迁移副本哈希与源数据不一致")
        if destination.exists():
            destination.rmdir()
        temporary.replace(destination)
    except Exception as error:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
        if isinstance(error, StorageBootstrapError):
            raise
        raise StorageBootstrapError(f"复制个人数据失败: {error}") from error

    return {
        "source": str(source),
        "destination": str(destination),
        "files": {
            name: {
                "source_sha256": info["sha256"],
                "destination_sha256": temporary_manifest["files"][name]["sha256"],
                "size": info["size"],
            }
            for name, info in source_manifest["files"].items()
        },
    }


def _read_storage_location(paths: list[Path]) -> Path | None:
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
            value = str(payload.get("data_directory", "")).strip() if isinstance(payload, dict) else ""
            if value:
                return Path(value).expanduser()
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
    return None


def _write_storage_location(local_app_data: Path, directory: Path) -> None:
    path = Path(local_app_data) / APP_NAME / "storage_location.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    payload = {
        "data_directory": str(directory),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "schema": 2,
    }
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _score_candidate(directory: Path) -> tuple[int, int]:
    try:
        manifest = build_data_manifest(directory)
    except StorageBootstrapError:
        return (-1, -1)
    return int(manifest["record_count"]), int(manifest["byte_count"])


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))


def _is_legacy_managed_root(path: Path, *, app_root: Path, local_app_data: Path, target: Path) -> bool:
    known = (
        app_root / "data",
        local_app_data / "Programs" / APP_NAME / "data",
        local_app_data / APP_NAME / "data",
        target,
    )
    return any(_same_path(path, candidate) for candidate in known)


def prepare_v12_data_root(
    *,
    frozen: bool | None = None,
    app_root: Path | None = None,
    local_app_data: Path | None = None,
) -> Path:
    """Resolve the active store and migrate a frozen v11 store exactly once."""
    explicit = str(os.environ.get("RESEARCH_ASSISTANT_DATA_DIR", "")).strip()
    if explicit:
        selected = Path(explicit).expanduser()
        selected.mkdir(parents=True, exist_ok=True)
        return selected

    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else bool(frozen)
    root = Path(app_root) if app_root is not None else (
        Path(sys.executable).resolve().parent if is_frozen else Path(__file__).resolve().parents[1]
    )
    if not is_frozen:
        selected = root / "data"
        selected.mkdir(parents=True, exist_ok=True)
        os.environ["RESEARCH_ASSISTANT_DATA_DIR"] = str(selected)
        return selected

    local = Path(local_app_data) if local_app_data is not None else Path(
        os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    )
    target = default_user_data_dir(local)
    config_paths = [local / APP_NAME / "storage_location.json", root / "storage_location.json"]
    configured = _read_storage_location(config_paths)

    if configured is not None and not _is_legacy_managed_root(
        configured, app_root=root, local_app_data=local, target=target
    ):
        configured.mkdir(parents=True, exist_ok=True)
        os.environ["RESEARCH_ASSISTANT_DATA_DIR"] = str(configured)
        return configured

    if target.is_dir() and any(target.iterdir()):
        selected = target
    else:
        candidates = [
            candidate
            for candidate in (
                configured,
                root / "data",
                local / "Programs" / APP_NAME / "data",
                local / APP_NAME / "data",
            )
            if candidate is not None and candidate.is_dir() and not _same_path(candidate, target)
        ]
        source = max(candidates, key=_score_candidate) if candidates else None
        if source is not None and _score_candidate(source) > (0, 0):
            copy_data_store_verified(source, target)
        else:
            target.mkdir(parents=True, exist_ok=True)
        selected = target

    _write_storage_location(local, selected)
    os.environ["RESEARCH_ASSISTANT_DATA_DIR"] = str(selected)
    return selected
