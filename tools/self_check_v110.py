"""Read-only integrity audit for 科研助手 v11 local data.

The command deliberately uses the JSON files directly instead of importing the
application persistence layer.  That keeps ``--read-only`` genuinely
non-mutating, even when a future normalizer gains an automatic migration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any


DATA_FILES = {
    "todos": "todo.json",
    "todo_meta": "todo_meta.json",
    "papers": "papers.json",
    "achievements": "achievements.json",
    "rejection_archive": "rejection_archive.json",
    "selection_feedback": "selection_feedback.json",
    "inspirations": "inspiration.json",
    "reminders": "reminders.json",
    "settings": "settings.json",
    "readings": "readings.json",
    "journals": "journals.json",
    "frontier": "frontier.json",
    "achievement_pdf_cache": "achievement_pdf_cache.json",
}
JCR_STATUSES = {"pending", "verified", "not_found", "manual", "ai_estimated"}
MANUAL_WINDOWS_SMOKE_CHECKLIST = (
    "在另一款前台软件中双击空格：科研助手应显示/隐藏；中文输入法开启时不得触发。",
    "分别测试双击 Tab 和自定义组合键：应从任意前台软件唤起选刊，失效时设置页应给出明确状态。",
    "小组件模式最小化后应收起到系统托盘；双击托盘图标应恢复窗口；关闭 × 应退出进程。",
    "软件模式最小化后应保留常规任务栏入口；启动后应直接显示主窗口，不应自动隐藏。",
    "启用鼠标穿透后，双击“科研助手”标题区域应解除穿透；固定后重启应恢复固定位置与尺寸。",
    "开机自启动开关应只写入当前用户 Run 项，关闭后应移除对应项。",
)


def _read_json(path: Path, errors: list[str]) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"{path.name}: 无法读取 JSON（{error}）")
        return None


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_records(payload: Any) -> list[dict[str, Any]]:
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


def _is_future(value: Any, today: date) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        return date.fromisoformat(text[:10]) > today
    except ValueError:
        return False


def _record_duplicate(ids: list[str], label: str, duplicates: list[str]) -> None:
    seen: set[str] = set()
    for value in ids:
        if not value:
            continue
        if value in seen:
            duplicates.append(f"{label}:{value}")
        seen.add(value)


def audit_data_root(root: Path, today: date | None = None) -> dict[str, Any]:
    """Return an inspection report without opening any target file for write."""
    root = root.expanduser().resolve()
    today = today or date.today()
    errors: list[str] = []
    warnings: list[str] = []
    hashes: dict[str, str] = {}
    payloads: dict[str, Any] = {}
    for key, filename in DATA_FILES.items():
        path = root / filename
        if path.is_file():
            hashes[filename] = _hash_file(path)
        payloads[key] = _read_json(path, errors)

    papers = _as_records(payloads["papers"])
    achievements = _as_records(payloads["achievements"])
    journals = _as_records(payloads["journals"])
    readings = _as_records(payloads["readings"])
    inspirations = _as_records(payloads["inspirations"])
    rejection_archive = _as_records(payloads["rejection_archive"])
    frontier = payloads["frontier"] if isinstance(payloads["frontier"], dict) else {}
    frontier_items = _as_records(frontier.get("items", []))
    todo_payload = payloads["todos"]
    if isinstance(todo_payload, dict):
        todo_count = sum(len(_as_records(items)) for items in todo_payload.values())
    else:
        todo_count = len(_as_records(todo_payload))

    duplicates: list[str] = []
    _record_duplicate([str(item.get("id", "")) for item in papers], "papers", duplicates)
    _record_duplicate([str(item.get("id", "")) for item in achievements], "achievements", duplicates)
    _record_duplicate([str(item.get("id", "")) for item in journals], "journals", duplicates)
    _record_duplicate([str(item.get("id", "")) for item in frontier_items], "frontier", duplicates)
    journal_entries = [
        entry
        for paper in papers
        for entry in paper.get("journals", [])
        if isinstance(entry, dict)
    ]
    _record_duplicate([str(item.get("id", "")) for item in journal_entries], "paper_journals", duplicates)

    future_dates: list[str] = []
    for paper in papers:
        paper_id = str(paper.get("id", "")) or str(paper.get("title", "未命名论文"))
        for journal in paper.get("journals", []):
            if not isinstance(journal, dict):
                continue
            journal_id = str(journal.get("id", "")) or str(journal.get("name", "未命名期刊"))
            # A revision deadline is intentionally allowed to be in the
            # future.  Only submission, status-update and timeline dates are
            # invalid when they exceed today.
            for field in ("date", "status_updated_at"):
                if _is_future(journal.get(field), today):
                    future_dates.append(f"{paper_id}/{journal_id}:{field}={journal.get(field)}")
            for node in journal.get("timeline", []):
                if isinstance(node, dict) and _is_future(node.get("date"), today):
                    future_dates.append(f"{paper_id}/{journal_id}:timeline={node.get('date')}")

    malformed_jcr: list[str] = []
    for journal in journals:
        jcr = journal.get("jcr", {})
        if not isinstance(jcr, dict):
            malformed_jcr.append(f"{journal.get('name', '未命名期刊')}: jcr 不是对象")
            continue
        state = str(jcr.get("status", "pending")).strip() or "pending"
        if state not in JCR_STATUSES:
            malformed_jcr.append(f"{journal.get('name', '未命名期刊')}: 未知 JCR 状态 {state}")

    pdf_paths = [
        str(file.get("path", "")).strip()
        for achievement in achievements
        for field in ("pdf_files", "files")
        for file in achievement.get(field, [])
        if isinstance(file, dict) and str(file.get("path", "")).strip().casefold().endswith(".pdf")
    ]
    missing_pdfs = [path for path in pdf_paths if not Path(path).is_file()]
    if duplicates:
        warnings.append(f"发现 {len(duplicates)} 个重复 ID")
    if future_dates:
        warnings.append(f"发现 {len(future_dates)} 个晚于 {today.isoformat()} 的投稿相关日期")
    if malformed_jcr:
        warnings.append(f"发现 {len(malformed_jcr)} 个 JCR 状态问题")
    if missing_pdfs:
        warnings.append(f"发现 {len(missing_pdfs)} 个失效的成果 PDF 链接")

    return {
        "result": "ok" if not errors else "errors",
        "read_only": True,
        "data_root": str(root),
        "today": today.isoformat(),
        "record_counts": {
            "todos": todo_count,
            "papers": len(papers),
            "journal_entries": len(journal_entries),
            "achievements": len(achievements),
            "journals": len(journals),
            "readings": len(readings),
            "inspirations": len(inspirations),
            "rejection_archive": len(rejection_archive),
            "frontier_items": len(frontier_items),
            "linked_pdfs": len(pdf_paths),
            "existing_pdfs": len(pdf_paths) - len(missing_pdfs),
        },
        "sha256": hashes,
        "duplicate_ids": duplicates,
        "future_dates": future_dates,
        "malformed_jcr": malformed_jcr,
        "missing_pdf_paths": missing_pdfs,
        "warnings": warnings,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="科研助手 v11 本地数据只读自检")
    parser.add_argument("--data-root", help="要检查的 data 文件夹")
    parser.add_argument("--read-only", action="store_true", help="显式确认只读检查")
    parser.add_argument("--manual-smoke-checklist", action="store_true", help="输出 Windows 手工集成检查清单")
    arguments = parser.parse_args()
    if arguments.manual_smoke_checklist:
        print("科研助手 v11 Windows 手工集成检查清单：")
        for index, item in enumerate(MANUAL_WINDOWS_SMOKE_CHECKLIST, start=1):
            print(f"{index}. {item}")
        return 0
    if not arguments.data_root:
        parser.error("必须传入 --data-root，或使用 --manual-smoke-checklist")
    if not arguments.read_only:
        parser.error("为防止误操作，必须显式传入 --read-only")
    report = audit_data_root(Path(arguments.data_root))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["result"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
