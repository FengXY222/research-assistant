"""Evidence-bound content admission and independent paper matching for calls."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any, Callable

FORMAL_MINIMUM_SCORE = 60
HIGH_NOTIFICATION_SCORE = 80
POLICY_VERSION = "special-issue-matching-2"
SCOPE_CHUNK_CHARACTERS = 12000
_PRIORITY_PUBLISHERS = {"Elsevier", "Springer Nature", "Taylor & Francis", "Wiley"}
_UNKNOWN_PUBLISHERS = {"", "unknown", "未知", "待确认", "n/a", "none", "null"}
_VOLATILE_FIELDS = {"built_at", "created_at", "updated_at", "evaluated_at", "last_seen_at", "profile_fingerprint", "cache_fingerprint"}


def _canonical(value: Any) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", str(value or "").casefold()).split())


def _entry_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("canonical_en", value.get("text", value.get("term", "")))).strip()
    return str(value or "").strip()


def _unique_terms(values: list[Any]) -> list[str]:
    found: dict[str, str] = {}
    for value in values:
        text = _entry_text(value)
        if _canonical(text):
            found.setdefault(_canonical(text), text)
    return list(found.values())


def publisher_is_unknown(publisher: Any) -> bool:
    from utils.publisher_utils import canonical_publisher
    return canonical_publisher(str(publisher or "").strip()).casefold() in _UNKNOWN_PUBLISHERS


def apply_publisher_priority(score: int, publisher: Any) -> tuple[int, float]:
    """Return a ranking score only; callers must never use it for admission."""
    from utils.publisher_utils import canonical_publisher
    normalized = canonical_publisher(str(publisher or "").strip())
    multiplier = 1.0 if publisher_is_unknown(publisher) or normalized in _PRIORITY_PUBLISHERS else 0.5
    return max(0, min(100, int(round(int(score) * multiplier)))), multiplier


def confirmed_scope_topics() -> list[dict[str, Any]]:
    """Reuse confirmed research boundaries, without importing frontier gates."""
    from utils.frontier_admission import SCOPE_TOPICS
    return deepcopy(SCOPE_TOPICS)


def _semantic(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _semantic(item) for key, item in value.items() if key not in _VOLATILE_FIELDS}
    if isinstance(value, (list, tuple)):
        return sorted((_semantic(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False, default=str))
    return value


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def build_special_issue_profiles(
    research_profile: dict[str, Any],
    papers: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build read-only semantic profiles from shared long/short-term signals."""
    from utils.research_signal_service import build_profile_view

    source = deepcopy(research_profile) if isinstance(research_profile, dict) else {}
    # Behavior is consolidated daily; sub-second decay must not invalidate caches.
    signal_day = (now or datetime.now()).replace(hour=0, minute=0, second=0, microsecond=0)
    view = build_profile_view(source, now=signal_day)
    active = [deepcopy(row) for row in view.get("active_terms", []) if isinstance(row, dict) and _entry_text(row)]
    active.sort(key=lambda row: (-int(row.get("weight", 0) or 0), _canonical(_entry_text(row))))
    exclusions = _unique_terms([
        *(source.get("excluded_entries", []) if isinstance(source.get("excluded_entries"), list) else []),
        *(source.get("excluded_terms", []) if isinstance(source.get("excluded_terms"), list) else []),
    ])
    locked = [deepcopy(row) for row in active if row.get("locked")]
    paper_profiles: dict[str, dict[str, Any]] = {}
    for paper in papers:
        if not isinstance(paper, dict):
            continue
        paper_id = str(paper.get("id", "")).strip()
        if not paper_id:
            continue
        paper_profiles[paper_id] = {
            "id": paper_id, "title": str(paper.get("title", "")).strip(),
            "keywords": _unique_terms(paper.get("keywords", []) if isinstance(paper.get("keywords"), list) else []),
            "abstract": str(paper.get("summary") or paper.get("abstract") or "").strip(),
            "locked_terms": deepcopy(locked), "excluded_terms": list(exclusions),
        }
    global_profile = {
        **view, "active_terms": active, "locked_terms": locked, "excluded_terms": exclusions,
        "papers": [{key: deepcopy(row[key]) for key in ("id", "title", "keywords", "abstract")} for row in paper_profiles.values()],
        "scope_topics": confirmed_scope_topics(),
    }
    result = {"global": global_profile, "papers": paper_profiles, "policy_version": POLICY_VERSION}
    result["profile_fingerprint"] = _fingerprint(_semantic(result))
    return result


def _scope_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def scope_paragraphs(item: dict[str, Any] | str) -> list[dict[str, str]]:
    """Preserve source paragraph IDs when they cover the scope, else derive IDs."""
    if isinstance(item, str):
        item = {"scope_text": item}
    scope = str(item.get("scope_text", "")).strip()
    if not scope:
        return []
    supplied = item.get("scope_paragraphs", [])
    rows = []
    for row in supplied if isinstance(supplied, list) else []:
        if isinstance(row, dict):
            rows.append({"id": str(row.get("id") or row.get("paragraph_id") or "").strip(), "text": _scope_text(row.get("text"))})
    full = "".join(scope.split())
    explicit_valid = (
        bool(rows) and all(row["id"] and row["text"] for row in rows)
        and len({row["id"] for row in rows}) == len(rows)
        and "".join("".join(row["text"].split()) for row in rows) == full
    )
    if not explicit_valid:
        rows = [{"id": f"scope-{index:04d}", "text": _scope_text(text)}
                for index, text in enumerate(re.split(r"\n\s*\n|\r?\n", scope), 1) if _scope_text(text)]
    result = []
    for row in rows:
        text = row["text"]
        if len(text) <= 4000:
            result.append(row)
            continue
        parts = [text[offset:offset + 4000] for offset in range(0, len(text), 4000)]
        result.extend({"id": f"{row['id']}.part-{index:04d}", "text": part} for index, part in enumerate(parts, 1))
    return result


def has_complete_scope(item: dict[str, Any]) -> bool:
    if not str(item.get("scope_text", "")).strip():
        return False
    status = str(item.get("scope_status", "")).casefold()
    if item.get("scope_is_complete") is False or status in {"snippet", "missing", "partial"}:
        return False
    if item.get("scope_is_complete") is True or status == "full":
        return True
    supplied = item.get("scope_paragraphs")
    if not isinstance(supplied, list) or not supplied:
        return False
    return (
        all(isinstance(row, dict) and (row.get("id") or row.get("paragraph_id")) and row.get("text") for row in supplied)
        and "".join("".join(str(row["text"]).split()) for row in supplied) == "".join(str(item["scope_text"]).split())
    )


def split_scope_chunks(item: dict[str, Any]) -> list[list[dict[str, str]]]:
    chunks: list[list[dict[str, str]]] = []
    current: list[dict[str, str]] = []
    count = 0
    for row in scope_paragraphs(item):
        if current and count + len(row["text"]) > SCOPE_CHUNK_CHARACTERS:
            chunks.append(current)
            current, count = [], 0
        current.append(row)
        count += len(row["text"])
    if current:
        chunks.append(current)
    return chunks


def match_input_fingerprint(item: dict[str, Any], profiles: dict[str, Any], model: str = "") -> str:
    """Cache only semantic inputs, complete scope evidence, model and policy."""
    return _fingerprint({
        "policy_version": POLICY_VERSION, "scope_topics": confirmed_scope_topics(), "model": str(model),
        "profile": _semantic(profiles), "title": _scope_text(item.get("title")),
        "type": str(item.get("type", "")), "scope": scope_paragraphs(item),
        "scope_complete": has_complete_scope(item),
    })


def allowed_branches(profiles: dict[str, Any]) -> list[dict[str, str]]:
    values = [{"id": row["id"], "text": row["text"]} for row in confirmed_scope_topics()]
    global_profile = profiles.get("global", {})
    for row in global_profile.get("active_terms", []) if isinstance(global_profile, dict) else []:
        text = _entry_text(row)
        if text:
            values.append({"id": "term:" + _fingerprint(_canonical(text))[:16], "text": text})
    papers = profiles.get("papers", {})
    for paper_id, paper in papers.items() if isinstance(papers, dict) else []:
        values.append({"id": "paper:" + str(paper_id), "text": str(paper.get("title", ""))})
    values.extend([{"id": "unrelated", "text": "No substantive research connection"}, {"id": "uncertain", "text": "Insufficient contextual evidence"}])
    return values


def _evidence_refs(value: Any, known: set[str], *, required: bool = True) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(ref, str) or ref not in known for ref in value):
        raise ValueError("Invalid paragraph evidence references")
    if required and not value:
        raise ValueError("Missing paragraph evidence")
    return list(dict.fromkeys(value))


def _assessment(raw: Any, known_refs: set[str], branches: set[str], threshold: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("Assessment must be an object")
    number = raw.get("score")
    if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number) or not 0 <= number <= 100:
        raise ValueError("Score must be a finite number from 0 to 100")
    for field in ("reason", "risk", "branch", "relation"):
        if not isinstance(raw.get(field), str) or not raw[field].strip():
            raise ValueError("Missing assessment " + field)
    branch, relation = raw["branch"].strip(), raw["relation"].strip()
    if branch not in branches or relation not in {"core", "exploration", "unrelated", "uncertain"}:
        raise ValueError("Unknown research branch or relation")
    evidence = _evidence_refs(raw.get("evidence_refs"), known_refs)
    exclusion = raw.get("exclusion_assessment")
    if not isinstance(exclusion, dict) or exclusion.get("status") not in {"none", "incidental", "separate_branch", "primary", "uncertain"}:
        raise ValueError("Missing contextual exclusion assessment")
    if not isinstance(exclusion.get("reason"), str) or not exclusion["reason"].strip():
        raise ValueError("Missing contextual exclusion reason")
    exclusion = {
        "status": exclusion["status"], "reason": exclusion["reason"].strip()[:500],
        "evidence_refs": _evidence_refs(exclusion.get("evidence_refs"), known_refs, required=exclusion["status"] != "none"),
    }
    if branch == "scope:landslide_methods" and relation in {"core", "exploration"}:
        relation = "exploration"
    score = int(round(number))
    in_scope = relation in {"core", "exploration"} and branch not in {"unrelated", "uncertain"}
    context_valid = exclusion["status"] not in {"primary", "uncertain"}
    qualified = score >= threshold and in_scope and context_valid
    status = "matched" if qualified else "below_threshold"
    if not in_scope or exclusion["status"] == "primary":
        status = "out_of_scope"
    if relation == "uncertain" or exclusion["status"] == "uncertain":
        status = "pending"
    return {
        "score": score, "raw_score": score, "reason": raw["reason"].strip()[:1000],
        "risk": raw["risk"].strip()[:1000], "branch": branch, "relation": relation,
        "evidence_refs": evidence, "exclusion_assessment": exclusion,
        "formal": qualified, "content_qualified": qualified, "status": status,
    }


def _pending_paper(paper_id: str, reason: str = "该论文尚未完成独立评估。") -> dict[str, Any]:
    return {
        "paper_id": paper_id, "score": None, "raw_score": None, "rank_score": None,
        "reason": reason, "risk": "", "branch": "uncertain", "relation": "uncertain",
        "evidence_refs": [], "formal": False, "content_qualified": False, "status": "pending",
    }


def validate_ai_match_result(
    raw: Any, profiles: dict[str, Any], paragraphs: list[dict[str, str]], *,
    minimum_score: int = FORMAL_MINIMUM_SCORE,
) -> dict[str, Any]:
    """Validate one complete response; missing paper judgments stay independent."""
    if not isinstance(raw, dict):
        raise ValueError("AI response must be an object")
    refs = {row["id"] for row in paragraphs}
    coverage = _evidence_refs(raw.get("scope_coverage"), refs)
    if set(coverage) != refs:
        raise ValueError("AI did not cover every supplied scope paragraph")
    branches = {row["id"] for row in allowed_branches(profiles)}
    result = _assessment(raw, refs, branches, minimum_score)
    known_papers = profiles.get("papers", {})
    known_papers = known_papers if isinstance(known_papers, dict) else {}
    values = raw.get("paper_matches", raw.get("matched_papers"))
    if not isinstance(values, list):
        raise ValueError("Missing independent paper assessments")
    paper_results = {}
    errors = []
    for row in values:
        if not isinstance(row, dict):
            raise ValueError("Paper assessment must be an object")
        paper_id = str(row.get("paper_id", "")).strip()
        if paper_id not in known_papers or paper_id in paper_results:
            raise ValueError("Unknown or duplicate paper ID")
        try:
            paper_results[paper_id] = {"paper_id": paper_id, **_assessment(row, refs, branches, minimum_score)}
        except ValueError as error:
            errors.append(f"{paper_id}: {error}")
            paper_results[paper_id] = _pending_paper(paper_id, "该论文评分证据无效，等待重新评估。")
    for paper_id in known_papers:
        if paper_id not in paper_results:
            errors.append(f"{paper_id}: missing assessment")
            paper_results[paper_id] = _pending_paper(paper_id)
    result.update({
        "paper_matches": [paper_results[key] for key in sorted(paper_results)],
        "scope_coverage": coverage, "matched_terms": _unique_terms(raw.get("matched_terms", []) if isinstance(raw.get("matched_terms"), list) else []),
        "validation_errors": errors,
    })
    return result


def merge_chunk_assessments(results: list[dict[str, Any]]) -> dict[str, Any]:
    """A call can contain independent submission branches; retain the best fit."""
    if not results:
        raise ValueError("No completed scope assessment")
    def best(rows: list[dict[str, Any]]) -> dict[str, Any]:
        selected = max(rows, key=lambda row: (bool(row.get("formal")), row.get("status") not in {"out_of_scope", "pending"}, row.get("score") or 0))
        return deepcopy(selected)

    combined = best(results)
    combined["scope_coverage"] = list(dict.fromkeys(ref for row in results for ref in row["scope_coverage"]))
    combined["matched_terms"] = _unique_terms([term for row in results for term in row["matched_terms"]])
    combined["validation_errors"] = [error for row in results for error in row.get("validation_errors", [])]
    combined["chunk_assessments"] = deepcopy(results)
    papers = {}
    for row in results:
        for paper in row["paper_matches"]:
            papers.setdefault(paper["paper_id"], []).append(paper)
    combined["paper_matches"] = [
        _pending_paper(paper_id) if len(rows) != len(results) or any(row["score"] is None for row in rows)
        else best(rows) for paper_id, rows in sorted(papers.items())
    ]
    return combined


def match_special_issue(
    item: dict[str, Any], profiles: dict[str, Any], *,
    ai_matcher: Callable[..., dict[str, Any]] | None = None,
    progress: Any = None, minimum_score: int = FORMAL_MINIMUM_SCORE,
) -> dict[str, Any]:
    """Validate AI evidence, then qualify content independently of publisher rank."""
    threshold = max(FORMAL_MINIMUM_SCORE, min(100, int(minimum_score)))
    paragraphs = scope_paragraphs(item)
    model = ""
    known_papers = profiles.get("papers", {})
    known_papers = known_papers if isinstance(known_papers, dict) else {}
    base = {
        "issue_id": str(item.get("id", "")), "score": None, "raw_score": None, "rank_score": None,
        "reason": "", "risk": "", "branch": "uncertain", "relation": "uncertain",
        "matched_terms": [], "matched_papers": [], "paper_matches": [_pending_paper(key) for key in sorted(known_papers)],
        "formal": False, "content_qualified": False, "any_paper_qualified": False,
        "profile_scope": "global_and_papers" if known_papers else "global",
        "model": model, "evaluated_at": datetime.now().isoformat(timespec="seconds"),
        "status": "pending", "excluded_conflicts": [], "evidence_refs": [], "scope_coverage": [],
        "scope_paragraphs": paragraphs, "policy_version": POLICY_VERSION,
        "profile_fingerprint": _fingerprint(_semantic(profiles)), "scope_fingerprint": _fingerprint(paragraphs),
        "cache_fingerprint": match_input_fingerprint(item, profiles, model),
        "publisher_unknown": publisher_is_unknown(item.get("publisher")), "publisher_multiplier": apply_publisher_priority(0, item.get("publisher"))[1],
        "validation_errors": [],
    }
    if not has_complete_scope(item):
        return {**base, "status": "awaiting_scope", "reason": "尚未取得完整征稿范围，暂不进行匹配评分。"}
    if ai_matcher is None:
        from utils.ai_service import score_special_issue_with_ai
        ai_matcher = score_special_issue_with_ai
    try:
        raw = ai_matcher({**deepcopy(item), "scope_paragraphs": deepcopy(paragraphs)}, deepcopy(profiles), progress)
    except Exception as error:
        return {**base, "status": "ai_unavailable", "reason": "AI 匹配暂不可用，等待重新评估。", "error_type": type(error).__name__}
    try:
        validated = validate_ai_match_result(raw, profiles, paragraphs, minimum_score=threshold)
    except ValueError as error:
        return {**base, "status": "invalid_response", "reason": "AI 匹配结果缺少有效证据，等待重新评估。", "validation_errors": [str(error)]}
    model = str(raw.get("model", "")).strip()
    result = {**base, **validated, "model": model, "cache_fingerprint": match_input_fingerprint(item, profiles, model)}
    result["rank_score"], result["publisher_multiplier"] = apply_publisher_priority(result["score"], item.get("publisher"))
    for paper in result["paper_matches"]:
        paper["rank_score"] = None if paper["score"] is None else apply_publisher_priority(paper["score"], item.get("publisher"))[0]
    result["matched_papers"] = sorted([deepcopy(row) for row in result["paper_matches"] if row["formal"]], key=lambda row: (-row["score"], row["paper_id"]))
    result["any_paper_qualified"] = bool(result["matched_papers"])
    result["chunk_assessments"] = deepcopy(raw.get("chunk_assessments", []))
    return result
