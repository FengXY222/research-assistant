"""Task-minimised local material retrieval with auditable usage manifests.

The user may allow AI features to use private research material, but permission
is not blanket upload authority.  This module selects only records connected to
the current paper/project, removes obvious contact/account identifiers, sends
small relevant excerpts, and records hashes instead of copied source text.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from utils import file_manager


MANIFEST_NAME = "ai_material_usage.json"
_SENSITIVE_PATTERNS = (
    re.compile(r"\b[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx]\b"),
    re.compile(r"\b1[3-9]\d{9}\b"),
    re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{11,30}\b", re.I),
    re.compile(r"\b(?:account|bank|银行卡|账号|身份证|电话|手机)\s*[:：]\s*\S+", re.I),
    re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"),
)


def _clean(value: Any, limit: int = 2400) -> str:
    text = " ".join(str(value or "").split())
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub("[已省略非必要个人信息]", text)
    return text[:limit]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def _terms(value: Any) -> set[str]:
    text = " ".join(str(value or "").casefold().split())
    return {token for token in re.findall(r"[a-z0-9][a-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text) if len(token) >= 3}


def _rank_excerpt(text: str, query: str, *, limit: int) -> str:
    clean = _clean(text, max(limit * 8, limit))
    if len(clean) <= limit:
        return clean
    wanted = _terms(query)
    blocks = [value.strip() for value in re.split(r"(?<=[。！？.!?])\s+|\n{2,}", clean) if value.strip()]
    ranked = sorted(
        enumerate(blocks),
        key=lambda pair: (-len(_terms(pair[1]) & wanted), pair[0]),
    )
    chosen: list[tuple[int, str]] = []
    used = 0
    for index, block in ranked:
        if used >= limit:
            break
        excerpt = block[: max(0, limit - used)]
        if excerpt:
            chosen.append((index, excerpt))
            used += len(excerpt) + 1
    return "\n".join(value for _, value in sorted(chosen))[:limit]


def _manifest_path() -> Path:
    return file_manager.DATA_DIR / MANIFEST_NAME


def load_ai_material_manifests(limit: int = 20) -> list[dict[str, Any]]:
    path = _manifest_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    values = payload.get("entries", []) if isinstance(payload, dict) else []
    return [dict(value) for value in values[-max(1, limit):] if isinstance(value, dict)]


def _save_manifest(entry: dict[str, Any]) -> None:
    path = _manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    values = load_ai_material_manifests(199)
    values.append(entry)
    temporary = path.with_suffix(path.suffix + ".tmp")
    encoded = json.dumps({"version": 1, "entries": values[-200:]}, ensure_ascii=False, indent=2) + "\n"
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _same_scope(record: dict[str, Any], *, paper_id: str, project_id: str, isolated: bool) -> bool:
    if not isolated:
        return True
    record_paper = str(record.get("paper_id", record.get("source_paper_id", record.get("id", "")))).strip()
    record_project = str(record.get("project_id", "")).strip()
    if paper_id:
        return record_paper == paper_id or str(record.get("id", "")).strip() == paper_id
    if project_id:
        return record_project == project_id
    # With no explicit project/paper, global profile records may be used, but
    # records assigned to another project stay isolated.
    return not record_project


def _paper_material(paper: dict[str, Any], query: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    context: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    paper_id = str(paper.get("id", "")).strip()
    body = {
        "title": _clean(paper.get("title"), 360),
        "keywords": [_clean(value, 80) for value in paper.get("keywords", []) if _clean(value, 80)][:20]
        if isinstance(paper.get("keywords"), list) else [],
        "summary": _rank_excerpt(str(paper.get("summary", paper.get("abstract", ""))), query, limit=2800),
        "ideas": [_clean(value.get("text"), 500) for value in paper.get("ideas", []) if isinstance(value, dict) and _clean(value.get("text"), 500)][:8],
        "submission_history": [],
    }
    for journal in paper.get("journals", []) if isinstance(paper.get("journals"), list) else []:
        if not isinstance(journal, dict):
            continue
        body["submission_history"].append(
            {
                "journal": _clean(journal.get("name"), 160),
                "status": _clean(journal.get("status"), 40),
                "result": _clean(journal.get("result"), 500),
                "notes_or_review": _rank_excerpt(str(journal.get("notes", "")), query, limit=900),
                "timeline": [
                    {"date": _clean(event.get("date"), 20), "status": _clean(event.get("status"), 50), "note": _clean(event.get("note"), 500)}
                    for event in journal.get("timeline", [])
                    if isinstance(event, dict)
                ][-10:],
            }
        )
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True)
    if any(value for key, value in body.items() if key != "submission_history") or body["submission_history"]:
        context.append({"category": "paper_record_and_submission_history", "record_id": paper_id, "content": body})
        manifest.append({"category": "paper_record_and_submission_history", "record_id": paper_id, "sha256": _hash(encoded), "characters_sent": len(encoded)})
    return context, manifest


def _pdf_material(paper: dict[str, Any], query: str, remaining: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if remaining < 400:
        return [], []
    try:
        from utils.pdf_text_service import PdfTextExtractionError, extract_pdf_full_text, split_pdf_text_for_ai
    except ImportError:
        return [], []
    context: list[dict[str, Any]] = []
    manifest: list[dict[str, Any]] = []
    files = paper.get("files", []) if isinstance(paper.get("files"), list) else []
    for value in files:
        if not isinstance(value, dict):
            continue
        if value.get("ai_access") is False:
            continue
        path = Path(str(value.get("path", ""))).expanduser()
        if path.suffix.casefold() != ".pdf" or not path.is_file():
            continue
        try:
            extracted = extract_pdf_full_text(path)
        except (OSError, PdfTextExtractionError):
            continue
        chunks = split_pdf_text_for_ai(str(extracted.get("text", "")), chunk_size=5000)
        ranked = sorted(chunks, key=lambda text: len(_terms(text) & _terms(query)), reverse=True)
        excerpt = "\n\n".join(ranked[:2])[:remaining]
        if not excerpt:
            continue
        context.append({"category": "current_paper_pdf_excerpt", "record_id": str(paper.get("id", "")), "file_name": path.name, "content": _clean(excerpt, remaining)})
        manifest.append(
            {
                "category": "current_paper_pdf",
                "record_id": str(paper.get("id", "")),
                "file_name": path.name,
                "sha256": str(extracted.get("fingerprint", "")) or _hash(str(extracted.get("text", ""))),
                "pages_read_locally": int(extracted.get("page_count", 0) or 0),
                "characters_sent": min(len(excerpt), remaining),
            }
        )
        break
    return context, manifest


def build_ai_material_context(
    task: str,
    query: str,
    *,
    paper: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
    include_pdf: bool = False,
    max_characters: int = 6000,
) -> dict[str, Any]:
    """Return a small outbound context and persist its non-content manifest."""

    settings = file_manager.load_app_settings()
    ai = settings.get("ai", {}) if isinstance(settings.get("ai"), dict) else {}
    if not bool(ai.get("local_material_access", True)):
        return {"enabled": False, "materials": [], "manifest_id": ""}
    isolated = bool(ai.get("local_material_project_isolation", True))
    paper = dict(paper) if isinstance(paper, dict) else {}
    profile = dict(profile) if isinstance(profile, dict) else {}
    if paper.get("ai_material_access") is False:
        return {"enabled": False, "materials": [], "manifest_id": "", "reason": "paper_disabled"}
    paper_id = str(paper.get("id", "")).strip()
    project_id = str(paper.get("project_id", profile.get("active_project_id", ""))).strip()
    if project_id:
        for project in profile.get("projects", []) if isinstance(profile.get("projects"), list) else []:
            if isinstance(project, dict) and str(project.get("id", "")).strip() == project_id and project.get("ai_material_access") is False:
                return {"enabled": False, "materials": [], "manifest_id": "", "reason": "project_disabled"}
    context, manifest = _paper_material(paper, query) if paper else ([], [])

    used = len(json.dumps(context, ensure_ascii=False))
    if include_pdf and paper and used < max_characters:
        pdf_context, pdf_manifest = _pdf_material(paper, query, max_characters - used)
        context.extend(pdf_context)
        manifest.extend(pdf_manifest)

    if used < max_characters:
        notes = []
        for record in file_manager.load_inspirations():
            if not _same_scope(record, paper_id=paper_id, project_id=project_id, isolated=isolated):
                continue
            text = _clean(record.get("text"), 700)
            if text and (_terms(text) & _terms(query) or not _terms(query)):
                notes.append({"id": str(record.get("id", "")), "text": text})
        if notes:
            serialized = json.dumps(notes[:8], ensure_ascii=False)
            room = max(0, max_characters - len(json.dumps(context, ensure_ascii=False)))
            clipped = _clean(serialized, room)
            if clipped:
                context.append({"category": "research_notes", "content": clipped})
                manifest.append({"category": "research_notes", "record_count": len(notes[:8]), "sha256": _hash(serialized), "characters_sent": len(clipped)})

    if paper_id:
        archive = [
            value for value in file_manager.load_rejection_archive()
            if isinstance(value, dict) and str(value.get("paper_id", "")).strip() == paper_id
        ]
        if archive:
            compact = [
                {
                    "journal": _clean(value.get("journal_name"), 160),
                    "result": _clean(value.get("result"), 500),
                    "review_or_notes": _clean(value.get("notes"), 900),
                }
                for value in archive[:8]
            ]
            serialized = json.dumps(compact, ensure_ascii=False)
            room = max(0, max_characters - len(json.dumps(context, ensure_ascii=False)))
            clipped = _clean(serialized, room)
            if clipped:
                context.append({"category": "rejection_and_review_history", "content": clipped})
                manifest.append({"category": "rejection_and_review_history", "record_count": len(compact), "sha256": _hash(serialized), "characters_sent": len(clipped)})

    manifest_id = _hash(f"{task}|{datetime.now().isoformat()}|{json.dumps(manifest, sort_keys=True)}")[:20]
    if manifest and bool(ai.get("local_material_use_manifest", True)):
        _save_manifest(
            {
                "id": manifest_id,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "task": str(task),
                "paper_id": paper_id,
                "project_id": project_id,
                "project_isolation": isolated,
                "materials": manifest,
            }
        )
    return {"enabled": True, "materials": context, "manifest_id": manifest_id if manifest else ""}
