from datetime import date, datetime, timedelta
from copy import deepcopy

from utils.special_issue_service import refresh_special_issues, build_special_issue_notifications, special_issue_refresh_due
from utils.submission_reminders import collect_special_issue_reminders
from utils.evidence_cache import EvidenceCache
from utils import file_manager, special_issue_repository

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


def test_partial_retry_calls_only_the_failed_source(tmp_path):
    failed = Source('a')
    healthy = Source('b')
    old = {
        'last_refresh_status': 'partial',
        'source_checkpoints': {
            'a': {'status': 'failed'},
            'b': {'status': 'success', 'last_success_at': '2026-09-06T11:00:00'},
        },
    }
    result = run(tmp_path, [failed, healthy], store=old, force=True)
    assert failed.calls == 1
    assert healthy.calls == 0
    assert result['store']['last_refresh_status'] == 'success'


def test_retired_failed_checkpoint_does_not_create_an_empty_retry(tmp_path):
    active = Source('active')
    old = {
        'last_refresh_status': 'partial',
        'source_checkpoints': {
            'retired_elsevier': {'status': 'failed'},
            'active': {'status': 'success', 'last_success_at': '2026-09-06T11:00:00'},
        },
    }
    result = run(tmp_path, [active], store=old, force=True)
    assert active.calls == 1
    assert result['store']['last_refresh_status'] == 'success'


def test_retired_elsevier_checkpoint_is_migrated_out_of_failed_state(tmp_path):
    active = Source('active')
    old = {
        'last_refresh_status': 'partial',
        'source_checkpoints': {'elsevier': {'status': 'failed', 'last_error': 'HTTP 404'}},
    }
    result = run(tmp_path, [active], store=old, force=True)
    checkpoint = result['store']['source_checkpoints']['elsevier']
    assert checkpoint['status'] == 'retired'
    assert checkpoint['consecutive_failures'] == 0
    assert 'last_error' not in checkpoint


def test_successful_failed_source_retry_does_not_reverify_healthy_history(tmp_path):
    failed = Source('a', rows=[{
        'id': 'from-a', 'title': 'Soil carbon call A', 'journal': 'Journal A',
        'publisher': 'Elsevier', 'deadline': '2027-06-30',
        'scope_text': 'Soil organic carbon mapping.', 'official_url': 'https://example.org/a',
    }])
    healthy = Source('b')
    old = {
        'items': [
            {
                'id': 'from-a', 'title': 'Soil carbon call A', 'journal': 'Journal A',
                'publisher': 'Elsevier', 'deadline': '2027-06-30',
                'scope_text': 'Soil organic carbon mapping.', 'official_url': 'https://example.org/a',
            },
            {
                'id': 'from-b', 'title': 'Healthy historical call B', 'journal': 'Journal B',
                'publisher': 'Wiley', 'deadline': '2027-07-30',
                'scope_text': 'Healthy source history.', 'official_url': 'https://example.org/b',
            },
        ],
        'last_refresh_status': 'partial',
        'source_checkpoints': {
            'a': {'status': 'failed'},
            'b': {'status': 'success', 'last_success_at': '2026-09-06T11:00:00'},
        },
    }
    verified = []

    def verifier(item, **_kwargs):
        verified.append(item['id'])
        return {**item, 'verification_status': 'conflict'}

    result = run(
        tmp_path, [failed, healthy], store=old, force=True,
        verifier=verifier, enricher=lambda item, *_args, **_kwargs: item,
    )
    assert failed.calls == 1 and healthy.calls == 0
    assert verified == ['from-a']
    assert {item['id'] for item in result['store']['items']} == {'from-a', 'from-b'}


def test_failed_retry_keeps_prior_results_partial_and_progress_never_moves_backward(tmp_path):
    class NoisyFailedSource(Source):
        def fetch(self, *, progress=None, **kwargs):
            self.calls += 1
            if progress:
                progress('direct', 70)
                progress('fallback failed', 0)
            raise OSError('offline')

    failed = NoisyFailedSource('a')
    healthy = Source('b')
    old = {
        'items': [
            {
                'id': 'kept', 'title': 'Kept call', 'journal': 'Journal A', 'publisher': 'Elsevier',
                'deadline': '2027-06-30', 'scope_text': 'soil carbon', 'official_url': 'https://example.org/call',
            }
        ],
        'last_refresh_status': 'partial',
        'source_checkpoints': {
            'a': {'status': 'failed'},
            'b': {'status': 'success', 'last_success_at': '2026-09-06T11:00:00'},
        },
    }
    progress_values = []
    result = run(
        tmp_path, [failed, healthy], store=old, force=True,
        progress=lambda _message, value=0: progress_values.append(int(value or 0)),
    )
    assert failed.calls == 1 and healthy.calls == 0
    assert result['store']['last_refresh_status'] == 'partial'
    assert [value['id'] for value in result['store']['items']] == ['kept']
    assert result['stats']['items'] == 1
    assert all(right >= left for left, right in zip(progress_values, progress_values[1:]))


def test_failed_source_retry_backfills_missing_v13_ai_axes_without_refetching_healthy_source(tmp_path):
    failed = Source('a', fail=True)
    healthy = Source('b')
    old = {
        'items': [
            {
                'id': 'legacy-v13',
                'title': 'Soil carbon mapping call',
                'journal': 'Journal A',
                'publisher': 'Elsevier',
                'deadline': '2027-06-30',
                'scope_text': 'Remote sensing and soil organic carbon mapping.',
                'scope_is_complete': True,
                'scope_status': 'full',
                'scope_paragraphs': [{'id': 'scope-1', 'text': 'Remote sensing and soil organic carbon mapping.'}],
                'verification_status': 'official_verified',
                'scoring_version': 'special-issue-dual-axis-13.0',
                'match': {'score': 84, 'formal': True, 'status': 'matched', 'reason': 'legacy scalar match'},
            }
        ],
        'last_refresh_status': 'partial',
        'source_checkpoints': {
            'a': {'status': 'failed'},
            'b': {'status': 'success', 'last_success_at': '2026-09-06T11:00:00'},
        },
    }

    def ai_matcher(*_args, **_kwargs):
        axis = {
            'adjustment': 26,
            'confidence': 'high',
            'reason': '输入证据支持双轴调整',
            'evidence_refs': ['title', 'scope_text'],
        }
        return {
            'score': 86,
            'reason': '主题和方法匹配',
            'risk': '需要核对最终投稿范围',
            'branch': 'scope:soil_carbon_protection',
            'relation': 'core',
            'evidence_refs': ['scope-1'],
            'exclusion_assessment': {'status': 'none', 'reason': '未触发排除方向', 'evidence_refs': []},
            'scope_coverage': ['scope-1'],
            'matched_terms': ['soil organic carbon'],
            'paper_matches': [],
            'model': 'fixture',
            'ai_axis_payload': {
                'provider': 'fixture',
                'model': 'fixture',
                'axes': {'relevance': axis, 'opportunity': axis},
            },
        }

    result = run(tmp_path, [failed, healthy], store=old, force=True, ai_matcher=ai_matcher)
    item = result['store']['items'][0]
    assert failed.calls == 1 and healthy.calls == 0
    assert result['store']['last_refresh_status'] == 'partial'
    assert item['relevance_axis']['ai_adjustment'] == 26
    assert item['opportunity_axis']['ai_adjustment'] == 26
    assert item['ai_review_status'] == 'success'


def test_each_completed_shard_is_committed_before_source_finishes(tmp_path, monkeypatch):
    class ShardedSource:
        source_id = 'journal_cfp_ddl'

        def fetch(self, *, on_shard, **_kwargs):
            one = {'id': 'one', 'title': 'Soil carbon one', 'deadline': '2027-06-30', 'scope_text': 'soil carbon', 'source': self.source_id}
            two = {'id': 'two', 'title': 'Soil carbon two', 'deadline': '2027-07-30', 'scope_text': 'soil carbon', 'source': self.source_id}
            on_shard(shard_id='2026-09', file_name='2026-09.json', rows=[one], status='success')
            on_shard(shard_id='2026-10', file_name='2026-10.json', rows=[two], status='success')
            return [one, two]

    monkeypatch.setattr(file_manager, 'SPECIAL_ISSUES_FILE', tmp_path / 'special_issues.json')
    commits = []
    real_commit = special_issue_repository.commit_special_issue_refresh

    def recording_commit(patch, *, token):
        if patch.get('last_refresh_status') == 'running':
            commits.append({
                'ids': [row['id'] for row in patch.get('items', [])],
                'shards': deepcopy(patch.get('source_checkpoints', {}).get('journal_cfp_ddl', {}).get('shards', {})),
            })
        return real_commit(patch, token=token)

    monkeypatch.setattr(special_issue_repository, 'commit_special_issue_refresh', recording_commit)
    refresh_special_issues(
        sources=[ShardedSource()], now=NOW, cache=EvidenceCache(tmp_path / 'cache.sqlite'),
        persist=True, store={}, journal_library=[],
        research_profile={'terms': [{'canonical_en': 'soil carbon'}]}, papers=[],
        easyscholar_ready=False, translate_scopes=False,
        verifier=lambda item, **_kwargs: item,
        enricher=lambda item, *_args, **_kwargs: item,
        ai_matcher=lambda *_args, **_kwargs: {'score': 80, 'formal': True, 'reason': 'match'},
    )
    assert [entry['ids'] for entry in commits[:2]] == [['one'], ['two']]
    assert commits[0]['shards']['2026-09']['status'] == 'success'
    assert commits[1]['shards']['2026-10']['status'] == 'success'


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
