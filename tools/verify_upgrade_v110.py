"""Stage and verify a data-safe 科研助手 v11 installer-upgrade fixture.

The real installation data is deliberately protected.  This helper produces a
hash/count snapshot and, only when an explicit empty destination is provided,
copies a disposable fixture for installer testing.  It never launches an
installer and it refuses the formal data directory unless the caller supplies
``--allow-formal``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.self_check_v110 import audit_data_root  # noqa: E402


FORMAL_DATA_ROOTS = (
    Path(r"C:\Users\fxy17\AppData\Local\Programs\科研助手\data").resolve(),
    (Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "科研助手" / "UserData").resolve(),
)


def _resolved(path_text: str) -> Path:
    return Path(path_text).expanduser().resolve()


def _is_formal_data_root(path: Path) -> bool:
    for formal_root in FORMAL_DATA_ROOTS:
        try:
            if path.samefile(formal_root):
                return True
        except OSError:
            if path == formal_root:
                return True
    return False


def snapshot_upgrade_fixture(data_root: Path) -> dict[str, Any]:
    report = audit_data_root(data_root)
    return {
        "data_root": str(data_root),
        "record_counts": report["record_counts"],
        "sha256": report["sha256"],
        "duplicate_ids": report["duplicate_ids"],
        "future_dates": report["future_dates"],
        "missing_pdf_paths": report["missing_pdf_paths"],
    }


def stage_copy(source: Path, destination: Path) -> dict[str, Any]:
    """Copy only into an explicit empty test root, then prove its snapshot matches."""
    if destination == source or source in destination.parents:
        raise ValueError("升级测试副本不能覆盖或嵌套在源数据目录中")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("升级测试目标目录必须不存在或为空，避免覆盖已有数据")
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True)
    before = snapshot_upgrade_fixture(source)
    after = snapshot_upgrade_fixture(destination)
    if before["sha256"] != after["sha256"] or before["record_counts"] != after["record_counts"]:
        raise RuntimeError("测试副本哈希或记录数不一致，已停止后续升级验证")
    return after


def main() -> int:
    parser = argparse.ArgumentParser(description="科研助手 v11 安装升级数据验证辅助工具")
    parser.add_argument("--data-root", required=True, help="要作为升级前基线检查的 data 文件夹")
    parser.add_argument("--copy-to", help="显式创建的空白临时目录；用于生成可安装测试副本")
    parser.add_argument("--snapshot-out", help="将升级前或副本快照写入指定 JSON 文件")
    parser.add_argument("--allow-formal", action="store_true", help="明确允许仅读取正式安装数据目录")
    arguments = parser.parse_args()

    source = _resolved(arguments.data_root)
    if _is_formal_data_root(source) and not arguments.allow_formal:
        parser.error("拒绝操作正式数据目录；如只读生成基线快照，请显式传入 --allow-formal")
    if not source.is_dir():
        parser.error(f"数据目录不存在：{source}")

    if arguments.copy_to:
        destination = _resolved(arguments.copy_to)
        snapshot = stage_copy(source, destination)
        snapshot["copied_to"] = str(destination)
    else:
        snapshot = snapshot_upgrade_fixture(source)

    if arguments.snapshot_out:
        output = _resolved(arguments.snapshot_out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        snapshot["snapshot_out"] = str(output)

    print(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
