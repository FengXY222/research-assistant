"""Small durable funnel summaries; never retain raw source responses in logs."""
from collections import Counter


def run_summary(*, batch_id, at, recalled, filtered, candidates, visible, ai, pool, errors, levels):
    reasons = Counter(str(item.get("display_reason") or item.get("quality_gate_reason") or "unspecified")[:120]
                      for item in candidates if item.get("candidate_state") != "visible")
    return {
        "batch_id": batch_id, "at": at, "recalled": recalled, "after_initial_filter": filtered,
        "displayed": len(visible),
        "today": sum(item.get("display_bucket") == "today" for item in visible),
        "previous_unread": sum(item.get("display_bucket") == "previous_unread" for item in visible),
        "levels": levels, "exclusion_reasons": dict(reasons.most_common(30)),
        "ai": ai, "pool": pool, "source_errors": len(errors),
        "empty_reason": "没有候选通过当前筛选与保留规则" if not visible else "",
    }
