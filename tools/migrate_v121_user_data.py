"""One-time, auditable v12.1 repair for the formal local user store."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.frontier_service import reset_frontier_history
from utils.local_translation import translate_profile_keywords
from utils.local_translation_backend import translate_en_to_zh
from utils.special_issue_matching import apply_publisher_priority
from utils.special_issue_service import (
    _fetch_official_page,
    _official_scope_sections,
    clean_special_issue_scope,
    infer_special_issue_publisher,
    special_issue_scope_is_corrupted,
)


def _load(path: Path, default: Any) -> Any:
    if not path.is_file():
        return deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else ""


def _write_atomic(path: Path, value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    temporary = path.with_suffix(path.suffix + ".v121.tmp")
    temporary.write_text(payload, encoding="utf-8", newline="\n")
    json.loads(temporary.read_text(encoding="utf-8"))
    os.replace(temporary, path)


def _repair_scope(item: dict[str, Any]) -> tuple[str, str, str]:
    item_id = str(item.get("id", ""))
    url = str(item.get("official_url", "")).strip()
    if not url:
        return item_id, "", "缺少官网地址"
    try:
        scope = _official_scope_sections(_fetch_official_page(url))
    except Exception as error:  # noqa: BLE001 - retained for next daily retry
        return item_id, "", str(error)[:240]
    if not scope or special_issue_scope_is_corrupted(scope):
        return item_id, "", "官网未提取到可用语义正文"
    return item_id, clean_special_issue_scope(scope), ""


def _scope_needs_repair(item: dict[str, Any]) -> bool:
    raw = str(item.get("scope_text", ""))
    clean = clean_special_issue_scope(raw)
    title = clean_special_issue_scope(item.get("title", ""))
    sources = item.get("source_evidence", []) if isinstance(item.get("source_evidence"), list) else []
    source_names = {str(value.get("source", "")).casefold() for value in sources if isinstance(value, dict)}
    title_only = bool(title) and title.casefold() in clean.casefold() and len(clean) < max(180, len(title) + 80)
    frontiers_stub = "frontiers" in source_names and len(clean) < 260
    return special_issue_scope_is_corrupted(raw) or title_only or frontiers_stub


def migrate(data_root: Path, *, apply: bool) -> dict[str, Any]:
    frontier_path = data_root / "frontier.json"
    profile_path = data_root / "research_profile.json"
    special_path = data_root / "special_issues.json"
    before_hashes = {path.name: _hash(path) for path in (frontier_path, profile_path, special_path)}

    frontier = _load(frontier_path, {})
    before_frontier = [value for value in frontier.get("items", []) if isinstance(value, dict)]
    frontier = reset_frontier_history(frontier)
    after_frontier = [value for value in frontier.get("items", []) if isinstance(value, dict)]

    profile = _load(profile_path, {})
    profile = translate_profile_keywords(profile)

    special = _load(special_path, {"items": []})
    items = [deepcopy(value) for value in special.get("items", []) if isinstance(value, dict)]
    corrupted_ids: list[str] = []
    for item in items:
        sources = item.get("source_evidence", []) if isinstance(item.get("source_evidence"), list) else []
        source_names = [str(value.get("source", "")) for value in sources if isinstance(value, dict)]
        item["publisher"] = infer_special_issue_publisher(
            item.get("publisher", ""),
            item.get("official_url", ""),
            *(item.get("discovery_urls", []) if isinstance(item.get("discovery_urls"), list) else []),
            source=" ".join(source_names),
        )
        needs_repair = _scope_needs_repair(item)
        scope = clean_special_issue_scope(item.get("scope_text", ""))
        if needs_repair:
            scope = ""
            corrupted_ids.append(str(item.get("id", "")))
        item["scope_text"] = scope

    by_id = {str(value.get("id", "")): value for value in items}
    repair_errors: dict[str, str] = {}
    repair_jobs = [by_id[item_id] for item_id in corrupted_ids if item_id in by_id]
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_repair_scope, item): str(item.get("id", "")) for item in repair_jobs}
        for future in as_completed(futures):
            item_id, scope, error = future.result()
            item = by_id[item_id]
            if scope:
                item["scope_text"] = scope
                item.pop("scope_text_zh", None)
                item.pop("scope_translation_signature", None)
                item.pop("match_scope_signature", None)
                item.pop("scope_repair_error", None)
            else:
                item["scope_text"] = ""
                item.pop("scope_text_zh", None)
                item["scope_repair_error"] = error
                repair_errors[item_id] = error
                match = item.get("match", {}) if isinstance(item.get("match"), dict) else {}
                item["match"] = {
                    **match,
                    "score": 0,
                    "formal": False,
                    "status": "awaiting_scope",
                    "reason": "官网征稿范围待重新核验，已隐藏原有乱码。",
                }

    translatable = []
    for item in items:
        scope = str(item.get("scope_text", "")).strip()
        signature = hashlib.sha256(scope.encode("utf-8")).hexdigest() if scope else ""
        if scope and (
            not str(item.get("scope_text_zh", "")).strip()
            or str(item.get("scope_translation_signature", "")) != signature
        ):
            translatable.append((item, scope, signature))
    if translatable:
        translations = translate_en_to_zh([scope for _item, scope, _signature in translatable])
        for (item, _scope, signature), translated in zip(translatable, translations, strict=True):
            item["scope_text_zh"] = translated
            item["scope_translation_signature"] = signature
            item.pop("scope_translation_error", None)

    for item in items:
        match = item.get("match", {}) if isinstance(item.get("match"), dict) else {}
        if not match or not str(item.get("scope_text", "")).strip():
            continue
        try:
            base_score = int(match.get("raw_score", match.get("score", 0)) or 0)
        except (TypeError, ValueError):
            base_score = 0
        adjusted, multiplier = apply_publisher_priority(base_score, item.get("publisher", ""))
        match["raw_score"] = base_score
        match["score"] = adjusted
        match["publisher_multiplier"] = multiplier
        match["formal"] = bool(str(match.get("reason", "")).strip()) and adjusted >= 60
        item["match"] = match
    special["items"] = items
    special["v121_repaired_at"] = datetime.now().isoformat(timespec="seconds")

    if apply:
        _write_atomic(frontier_path, frontier)
        _write_atomic(profile_path, profile)
        _write_atomic(special_path, special)

    translated_keywords = sum(
        1
        for field in ("terms", "pending_terms", "excluded_entries")
        for value in (profile.get(field, []) if isinstance(profile.get(field), list) else [])
        if isinstance(value, dict) and str(value.get("translation_zh", "")).strip()
    )
    return {
        "applied": apply,
        "frontier_before": len(before_frontier),
        "frontier_after": len(after_frontier),
        "frontier_removed": len(before_frontier) - len(after_frontier),
        "special_items": len(items),
        "corrupted_scopes": len(corrupted_ids),
        "repaired_scopes": len(corrupted_ids) - len(repair_errors),
        "unrepaired_scopes": len(repair_errors),
        "translated_scopes": sum(bool(str(value.get("scope_text_zh", "")).strip()) for value in items),
        "translated_keywords": translated_keywords,
        "repair_errors": repair_errors,
        "before_hashes": before_hashes,
        "after_hashes": {path.name: _hash(path) for path in (frontier_path, profile_path, special_path)} if apply else {},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    result = migrate(args.data_root.resolve(), apply=bool(args.apply))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
