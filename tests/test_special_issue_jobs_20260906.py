from datetime import date, datetime, timedelta
from copy import deepcopy

from utils.special_issue_service import refresh_special_issues, build_special_issue_notifications, special_issue_refresh_due
from utils.submission_reminders import collect_special_issue_reminders
from utils.evidence_cache import EvidenceCache

NOW = datetime(2026, 9, 6, 12)


class Source:
    def __init__(self, name, fail=False, rows=None):
        self.source_id, self.fail, self.rows = name, fail, rows or []
        self.calls = 0

    def fetch(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise OSError('offline')
        return self.rows


def run(tmp_path, sources, **kwargs):
    return refresh_special_issues(sources=sources, now=NOW, cache=EvidenceCache(tmp_path / 'cache.sqlite'),
        persist=False, store=kwargs.pop('store', {}), journal_library=[], research_profile={}, papers=[],
        easyscholar_ready=False, translate_scopes=False, **kwargs)


def test_all_sources_failed_preserves_success_checkpoint_and_schedules_retry(tmp_path):
    old = {'items': [], 'last_checked_at': '2026-09-01T12:00:00'}
    result = run(tmp_path, [Source('a', fail=True)], store=old)
    assert result['store']['last_refresh_status'] == 'failed'
    assert result['store']['last_checked_at'] == old['last_checked_at']
    checkpoint = result['store']['source_checkpoints']['a']
    assert checkpoint['status'] == 'failed' and checkpoint['next_retry_at'] == '2026-09-06T12:15:00'
    assert not special_issue_refresh_due(result['store'], now=NOW)
    assert special_issue_refresh_due(result['store'], now=NOW + timedelta(minutes=15))


def test_partial_success_is_not_all_success_and_retains_per_source_state(tmp_path):
    result = run(tmp_path, [Source('a', fail=True), Source('b')])
    assert result['store']['last_refresh_status'] == 'partial'
    assert result['store']['source_checkpoints']['b']['last_success_at'] == NOW.isoformat()
    assert not result['store']['source_checkpoints']['a'].get('last_success_at')


def test_backoff_skips_only_failed_source_unless_manually_forced(tmp_path):
    source = Source('a', fail=True)
    old = {'source_checkpoints': {'a': {'status': 'failed', 'next_retry_at': '2026-09-06T12:15:00'}}}
    run(tmp_path, [source], store=old)
    assert source.calls == 0
    run(tmp_path, [source], store=old, force=True)
    assert source.calls == 1


def test_budget_rotates_sources_and_does_not_erase_records(tmp_path):
    rows = [{'id': f'{publisher}-{i}', 'title': f'Soil carbon {publisher} {i}', 'publisher': publisher,
             'official_url': f'https://{publisher}.example/{i}', 'deadline': '2027-06-30'}
            for publisher in ('Elsevier', 'Wiley', 'MDPI') for i in range(3)]
    checked = []
    def verify(item, **kwargs):
        checked.append(item['publisher'])
        return item
    result = run(tmp_path, [], store={'items': rows}, candidate_limit=3, verifier=verify,
                 enricher=lambda item, *_args, **_kwargs: item)
    assert set(checked) == {'Elsevier', 'Wiley', 'MDPI'}
    assert len(result['store']['items']) == 9


def open_call(**updates):
    row = {'id': 'a', 'title': 'Soil carbon', 'publisher': 'Elsevier', 'deadline': '2026-10-01',
           'scope_is_complete': True, 'verification_status': 'official_verified', 'call_status': 'open',
           'official_checked_at': NOW.isoformat(), 'status': 'saved', 'saved': True,
           'match': {'score': 90, 'formal': True, 'reason': 'Relevant'}}
    row.update(updates)
    return row


def test_closed_calls_do_not_generate_new_high_or_deadline_notifications():
    row = open_call(verification_status='closed', call_status='closed', deadline='2026-09-09')
    notifications = build_special_issue_notifications({}, {'items': [row]}, today=NOW.date())
    assert all(value['kind'] not in {'new_high_match', 'deadline'} for value in notifications)


def test_missed_reminders_only_catch_up_nearest_valid_checkpoint():
    store = {'items': [open_call()]}
    reminders = [row for row in collect_special_issue_reminders(store, today=NOW.date()) if row['kind'] == 'deadline']
    assert len(reminders) == 1 and reminders[0]['offset'] == 30 and reminders[0]['days_remaining'] == 25
    store['reminder_log'] = reminders
    assert not [row for row in collect_special_issue_reminders(store, today=NOW.date()) if row['kind'] == 'deadline']


def test_sent_later_checkpoint_does_not_trigger_earlier_missed_reminder():
    store = {'items': [open_call()], 'reminder_log': [{'id': 'special-issue|deadline|a|2026-10-01|14',
        'kind': 'deadline', 'issue_id': 'a', 'deadline': '2026-10-01', 'offset': 14}]}
    assert not [row for row in collect_special_issue_reminders(store, today=NOW.date()) if row['kind'] == 'deadline']


def test_deadline_history_previous_current_contract_is_used():
    row = open_call(deadline_history=[{'previous': '2026-09-10', 'current': '2026-10-01', 'checked_at': NOW.isoformat()}])
    reminders = collect_special_issue_reminders({'items': [row]}, today=NOW.date())
    changed = [item for item in reminders if item['kind'] == 'deadline_changed']
    assert len(changed) == 1 and changed[0]['previous_deadline'] == '2026-09-10'


def test_cancelled_refresh_keeps_data_and_does_not_claim_success(tmp_path):
    old = {'items': [open_call()]}
    result = run(tmp_path, [Source('a')], store=old, cancelled=lambda: True)
    assert result['store']['last_refresh_status'] == 'cancelled'
    assert result['store']['items'][0]['id'] == 'a'


def test_refresh_queues_notifications_without_premarking_delivery(tmp_path):
    row = open_call(id="queued")
    result = run(
        tmp_path,
        [],
        store={"items": [row]},
        verifier=lambda item, **_kwargs: item,
        enricher=lambda item, *_args, **_kwargs: item,
    )
    pending = result["store"]["notification_outbox"]
    assert pending and pending[0]["state"] == "pending"
    assert result["store"]["notification_log"] == []
