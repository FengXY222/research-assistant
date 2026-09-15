"""AI admission adapter with per-paper evidence caching and no personal writes."""

from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json

from utils.frontier_admission import (
    POLICY_VERSION, REVIEW_SYSTEM, _validate, build_review_context, build_review_payload, review_candidates,
)


def review_frontier_content(profile, items, journals, *, progress=None, cache=None,
                            reviewer=None, easyscholar_ready=None):
    from utils.ai_service import _chat_json, _require_config
    from utils.easyscholar_service import is_easyscholar_ready
    from utils.research_signal_service import build_profile_view

    context = build_review_context(profile, profile.get("authored_papers", []))
    target_ids = {str(i.get("id", "")) for i in items}
    view = build_profile_view(profile, now=datetime.combine(date.today(), datetime.min.time()))
    context["behavior_evidence"] = [
        {k: s.get(k) for k in ("title", "terms", "strength", "event_type", "reason")}
        for s in view.get("short_term", [])
        if str(s.get("item_id", "")) not in target_ids
    ][:30]
    context["behavior_rule"] = "评价和收藏/忽略是辅助样本，不操作不算负样本；不得覆盖用户确认的scope_topics边界。"
    config, key, unavailable = {}, "", False
    if reviewer is None:
        try:
            config, key = _require_config("frontier_rerank")
        except Exception:
            unavailable = True
    now = datetime.now().isoformat(timespec="seconds")
    expires = (datetime.now() + timedelta(days=14)).isoformat(timespec="seconds")
    new_reviews = 0
    pending_budget = set()
    failed_ids = set()

    def cached_reviewer(payload):
        nonlocal new_reviews
        found, missing, signatures = [], [], {}
        for candidate in payload["candidates"]:
            value = {"model": config.get("model", "injected"), "system": REVIEW_SYSTEM,
                     "profile": context, "candidate": candidate}
            signature = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            signatures[candidate["id"]] = signature
            cached = cache.get_source_response("frontier_admission", signature, now=now) if cache else None
            if cached:
                found.append(cached["evaluation"])
            elif new_reviews < 80:
                new_reviews += 1
                missing.append(candidate)
            else:
                pending_budget.add(candidate["id"])
        if missing:
            request = {**payload, "candidates": missing}
            try:
                if unavailable:
                    raise RuntimeError("AI unavailable")
                response = reviewer(request) if reviewer else _chat_json(config, key, REVIEW_SYSTEM, request, 5000)
            except Exception:
                failed_ids.update(str(c["id"]) for c in missing)
                return {"evaluations": found}
            raw = response.get("evaluations", []) if isinstance(response, dict) else []
            raw = raw if isinstance(raw, list) else []
            ids = [str(v.get("id", "")) for v in raw if isinstance(v, dict)]
            originals = {str(i["id"]): i for i in items}
            requested_ids = {str(c["id"]) for c in missing}
            for value in raw:
                if not isinstance(value, dict) or value.get("id") not in requested_ids:
                    continue
                item_id = value["id"]
                checked = _validate(originals[item_id], value, context)
                if cache and ids.count(item_id) == 1 and checked["content_decision"] in {"accept", "reject"}:
                    cache.put_source_response("frontier_admission", signatures[item_id],
                                              {"evaluation": value}, now, expires)
            found.extend(raw)
        return {"evaluations": found}

    judgments = review_candidates(items, context, reviewer=cached_reviewer, journals=journals,
        easyscholar_ready=is_easyscholar_ready() if easyscholar_ready is None else easyscholar_ready,
        progress=progress)
    lookup = {v["id"]: v for v in judgments}
    labels = {t["id"]: t["text"] for t in context["terms"]}
    result = []
    for source in items:
        item = deepcopy(source)
        v = lookup.get(str(item["id"]))
        if v is None:
            continue
        decision, route = v["content_decision"], v["route"]
        if str(item["id"]) in failed_ids:
            v["validation_issue"] = "reviewer_unavailable"
            v["reason_cn"] = "AI 本批次未完成，暂存待复核；已通过的缓存结果保留。"
        item.update({
            "admission_version": POLICY_VERSION, "content_decision": decision,
            "admission_issue": "review_budget_pending" if item["id"] in pending_budget else v.get("validation_issue", ""),
            "admission_checked_at": now, "admission_route": route,
            "ai_score": v["score"] if v["score"] is not None else -1,
            "score": v.get("ranking_score", 0), "ranking_weight": v.get("ranking_weight", 1),
            "recommendation_reason": v["reason_cn"], "ai_reason_cn": v["reason_cn"],
            "ai_model": config.get("model", ""), "ai_updated_at": date.today().isoformat(),
            "recommendation_kind": "core_keyword" if decision == "accept" and v["relation"] == "core" else "profile_exploration",
            "relevance_level": "strict" if v["relation"] == "core" else "explore",
            "admission_evidence": v.get("evidence_quotes", []), "admission_profile_refs": v.get("profile_refs", []),
            "matched_terms": [labels[r] for r in v.get("profile_refs", []) if r in labels],
            "match_terms": [labels[r] for r in v.get("profile_refs", []) if r in labels],
            "score_breakdown": {"terms": v.get("ranking_score", 0), "priority": 0, "quality": 0, "feedback": 0, "ai": 0},
            "ai_adjustment": 0, "feedback_adjustment": 0,
            "quality_gate_state": {"journal": "eligible", "preprint": "preprint"}.get(route, "withheld"),
            "quality_gate_reason": route,
        })
        if v.get("jcr_quartile"):
            item["jcr_quartile"] = "Q" + str(v["jcr_quartile"])
            item["jcr_status"] = v.get("quality_status", "unknown")
        result.append(item)
    return result
