"""One admission and view policy for calls, the widget, workbench and reminders."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from utils.publisher_utils import canonical_publisher

CONTENT_THRESHOLD = 60
NOTIFY_THRESHOLD = 80
DISCOVERY_BUDGET = 36
AGGREGATOR_FRESH_DAYS = 7
METADATA_TTL_DAYS = 30
SOURCE_RETRY_MINUTES = (15, 60, 360)
REMINDER_DAYS = (90, 60, 30, 14, 7, 3)


def local_datetime(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or '').strip().replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed


def known(value: Any) -> bool:
    return str(value or '').strip().casefold() not in {'', 'unknown', '未知', '待确认', '待核验', 'n/a', 'none'}


def is_saved(item: dict[str, Any]) -> bool:
    return bool(item['saved']) if 'saved' in item else item.get('status') == 'saved'


def is_ignored(item: dict[str, Any]) -> bool:
    return bool(item['ignored']) if 'ignored' in item else item.get('status') == 'ignored'


def is_read(item: dict[str, Any]) -> bool:
    return bool(item.get('is_read') or item.get('read_at') or item.get('status') == 'read')


def quartile(item: dict[str, Any], kind: str) -> str:
    raw = item.get(kind) if isinstance(item.get(kind), dict) else {}
    values = [item.get(f'{kind}_quartile', '')]
    if kind == 'cas':
        values += [raw.get(key, item.get(key, '')) for key in ('cas_upgrade', 'cas_basic', 'cas_upgrade_small')]
    values += [raw.get(key, '') for key in ('quartile', 'partition', 'zone', 'value')]
    for metric in raw.get('metrics', []) if isinstance(raw.get('metrics'), list) else []:
        if isinstance(metric, dict):
            values += [metric.get('quartile', metric.get('partition', ''))]
    for value in values:
        text = str(value or '').strip().upper()
        match = re.search(r'(?:Q\s*([1-4])\b|\b([1-4])\s*区|^([1-4])$)', text)
        if match:
            return next(group for group in match.groups() if group)
    return ''


def days_remaining(item: dict[str, Any], now: datetime) -> int | None:
    try:
        return (date.fromisoformat(str(item.get('deadline', ''))[:10]) - now.date()).days
    except ValueError:
        return None


def aggregator_is_fresh(item: dict[str, Any], now: datetime) -> bool:
    for evidence in item.get('source_evidence', []):
        if not isinstance(evidence, dict) or not evidence.get('is_aggregator'):
            continue
        # Fetching an old announcement again does not make its content new.
        updated = local_datetime(evidence.get('updated_at') or evidence.get('published_at'))
        if updated is not None and timedelta(0) <= now - updated <= timedelta(days=AGGREGATOR_FRESH_DAYS):
            return True
    return False


def open_eligibility(item: dict[str, Any], now: datetime | None = None) -> tuple[bool, str]:
    now = now or datetime.now()
    now = now.astimezone().replace(tzinfo=None) if now.tzinfo else now
    verification = str(item.get('verification_status', '')).casefold()
    if item.get('identity_status') == 'conflict' or verification == 'conflict' or item.get('fact_conflicts'):
        return False, 'identity_or_fact_conflict'
    if verification in {'closed', 'expired'} or item.get('call_status') in {'closed', 'expired'}:
        return False, 'closed'
    remaining = days_remaining(item, now)
    if remaining is None:
        return False, 'deadline_unknown'
    if remaining < 0:
        return False, 'expired'
    if verification == 'official_verified':
        checked = local_datetime(item.get('official_verified_at') or item.get('official_checked_at'))
        ttl = timedelta(hours=6 if remaining <= 14 else 24)
        if checked is not None and timedelta(0) <= now - checked <= ttl:
            return True, 'official_verified'
        if aggregator_is_fresh(item, now):
            return True, 'aggregator_unverified'
        return False, 'verification_stale'
    if verification in {'aggregator_unverified', 'temporarily_unavailable', 'pending_official'}:
        if aggregator_is_fresh(item, now):
            return True, 'aggregator_unverified'
    return False, 'awaiting_verification'


def match_for_view(item: dict[str, Any], paper_id: str = '') -> dict[str, Any]:
    match = item.get('match') if isinstance(item.get('match'), dict) else {}
    if paper_id in {'', 'global'}:
        return dict(match)
    rows = match.get('paper_matches', match.get('matched_papers', []))
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and str(row.get('paper_id', '')) == str(paper_id):
            return {**row, 'stale': bool(match.get('stale') or row.get('stale'))}
    return {'score': 0, 'formal': False, 'status': 'pending', 'reason': '该论文尚未完成匹配。'}


def evaluate_special_issue(
    item: dict[str, Any], *, now: datetime | None = None, paper_id: str = '',
    view: str = 'recommended', filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now()
    match = match_for_view(item, paper_id)
    try:
        score = max(0, min(100, int(match.get('score', 0) or 0)))
        rank_score = float(match.get('rank_score', score))
    except (TypeError, ValueError):
        score, rank_score = 0, 0.0
    has_scope = bool(item.get('scope_is_complete') or item.get('scope_status') == 'full')
    qualified = bool(match.get('content_qualified', match.get('formal', False)))
    content_ok = bool(has_scope and qualified and score >= CONTENT_THRESHOLD and match.get('reason')
                      and not match.get('stale') and match.get('relation') not in {'unrelated', 'uncertain'})
    open_ok, reason_code = open_eligibility(item, now)
    recommended = content_ok and open_ok and not is_ignored(item)
    if not content_ok and open_ok:
        reason_code = 'awaiting_scope' if not has_scope else 'awaiting_match' if match.get('stale') else 'content_not_qualified'
    if view == 'saved':
        visible = is_saved(item) and not is_ignored(item)
    elif view == 'ignored':
        visible = is_ignored(item)
    elif view == 'changed':
        visible = not is_ignored(item) and bool(item.get('deadline_history') or item.get('verification_history'))
    elif view == 'unverified':
        visible = not is_ignored(item) and (not open_ok or reason_code == 'aggregator_unverified' or not has_scope or match.get('stale', False)
                                          or match.get('status') in {'pending', 'ai_unavailable', 'invalid_response', 'awaiting_scope'})
    elif view == 'all':
        visible = not is_ignored(item)
    else:
        visible = recommended and not is_saved(item)
    filters = filters or {}
    publisher = canonical_publisher(item.get('publisher', ''))
    fee = str(item.get('fee_mode', 'unknown')).casefold()
    actual = {'publisher': publisher, 'fee': fee, 'jcr': quartile(item, 'jcr'), 'cas': quartile(item, 'cas')}
    unknowns = {name for name, value in actual.items() if not known(value)}
    mismatch = False
    for name, wanted in filters.items():
        if wanted in (None, '', 'any'):
            continue
        if name in actual:
            if name in unknowns:
                continue
            if name == 'fee':
                options = {'no_fee': {'subscription', 'hybrid', 'no_fee'}, 'paid': {'apc', 'hybrid', 'paid', 'gold', 'open_access'}}
                mismatch |= actual[name] not in options.get(str(wanted), {str(wanted)})
            elif name == 'publisher':
                mismatch |= str(actual[name]).casefold() != canonical_publisher(wanted).casefold()
            else:
                wanted_values = wanted if isinstance(wanted, (list, tuple, set)) else [wanted]
                mismatch |= actual[name] not in {str(v).upper().removeprefix('Q').removesuffix('区') for v in wanted_values}
        elif name == 'search':
            haystack = ' '.join(str(item.get(key, '')) for key in ('title', 'journal', 'publisher', 'scope_text')).casefold()
            mismatch |= ' '.join(str(wanted).casefold().split()) not in haystack
    remaining = days_remaining(item, now)
    wanted_days = filters.get('deadline')
    if wanted_days not in (None, '', 'any'):
        if remaining is None:
            unknowns.add('deadline')
        else:
            mismatch |= remaining < 0 or remaining > int(wanted_days)
    is_exploration = match.get('relation') == 'exploration' or match.get('branch') == 'landslide_methods'
    return {
        'visible': bool(visible and not mismatch), 'recommended': bool(recommended),
        'content_qualified': content_ok, 'open': open_ok, 'reason_code': reason_code,
        'score': score, 'rank_score': rank_score, 'reason': str(match.get('reason', '')),
        'match': match, 'unknown_count': len(unknowns), 'unknown_fields': sorted(unknowns),
        'days_remaining': remaining,
        'sort_key': (1 if unknowns else 0, 1 if is_exploration else 0, -rank_score,
                     remaining if remaining is not None else 999999, str(item.get('title', '')).casefold()),
    }
