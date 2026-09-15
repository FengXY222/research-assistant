from copy import deepcopy
from datetime import datetime, timedelta
import importlib
import importlib.util

NOW = datetime(2026, 9, 6, 12)


def policy():
    assert importlib.util.find_spec('utils.special_issue_policy'), 'Shared admission policy is missing'
    return importlib.import_module('utils.special_issue_policy')


def issue(**updates):
    row = {
        'id': 'call-a', 'title': 'Soil carbon protection', 'journal': 'Journal A',
        'publisher': 'Elsevier', 'fee_mode': 'hybrid', 'jcr_quartile': 'Q1',
        'cas_upgrade': 'Environmental science 1区', 'deadline': '2026-12-31',
        'status': 'unread', 'verification_status': 'official_verified',
        'official_checked_at': NOW.isoformat(), 'call_status': 'open',
        'scope_is_complete': True,
        'match': {'score': 90, 'rank_score': 90, 'formal': True, 'reason': 'Relevant scope',
                  'status': 'matched', 'relation': 'core'},
    }
    row.update(updates)
    return row


def test_closed_conflicted_and_expired_are_never_open_despite_old_score():
    for state in ('closed', 'conflict', 'expired'):
        result = policy().evaluate_special_issue(issue(verification_status=state), now=NOW)
        assert not result['recommended'], state


def test_old_official_evidence_is_pending_not_open_forever():
    row = issue(official_checked_at=(NOW - timedelta(days=2)).isoformat())
    assert not policy().evaluate_special_issue(row, now=NOW)['recommended']


def test_fresh_aggregator_can_be_recommended_but_fetch_time_does_not_prove_freshness():
    row = issue(verification_status='aggregator_unverified', source_evidence=[
        {'is_aggregator': True, 'updated_at': '2026-09-04', 'fetched_at': NOW.isoformat()}])
    assert policy().evaluate_special_issue(row, now=NOW)['recommended']
    row['source_evidence'][0]['updated_at'] = '2020-09-04'
    assert not policy().evaluate_special_issue(row, now=NOW)['recommended']
    del row['source_evidence'][0]['updated_at']
    assert not policy().evaluate_special_issue(row, now=NOW)['recommended']


def test_saved_expired_remains_visible_in_saved_but_not_recommended():
    row = issue(saved=True, status='saved', deadline='2026-09-01')
    assert policy().evaluate_special_issue(row, now=NOW, view='saved')['visible']
    assert not policy().evaluate_special_issue(row, now=NOW)['recommended']


def test_independent_read_saved_and_ignored_states():
    row = issue(saved=True, read_at=NOW.isoformat(), status='read')
    assert policy().evaluate_special_issue(row, now=NOW, view='saved')['visible']
    row['ignored'] = True
    assert not policy().evaluate_special_issue(row, now=NOW)['visible']
    assert policy().evaluate_special_issue(row, now=NOW, view='ignored')['visible']


def test_paper_view_does_not_borrow_global_score_or_reason():
    row = issue()
    row['match']['paper_matches'] = [
        {'paper_id': 'a', 'score': 5, 'formal': False, 'reason': 'Not A'},
        {'paper_id': 'b', 'score': 85, 'rank_score': 85, 'formal': True, 'reason': 'Fits B'},
    ]
    assert not policy().evaluate_special_issue(row, now=NOW, paper_id='a')['recommended']
    b = policy().evaluate_special_issue(row, now=NOW, paper_id='b')
    assert b['recommended'] and b['score'] == 85 and b['reason'] == 'Fits B'
    assert not policy().evaluate_special_issue(row, now=NOW, paper_id='missing')['recommended']


def test_unknown_metadata_kept_at_tail_even_with_filters():
    row = issue(publisher='unknown', fee_mode='unknown', jcr_quartile='', cas_upgrade='')
    result = policy().evaluate_special_issue(row, now=NOW, filters={
        'publisher': 'Elsevier', 'fee': 'no_fee', 'jcr': '1', 'cas': '1'})
    assert result['visible'] and result['unknown_count'] == 4
    assert result['sort_key'][0] == 1


def test_known_mismatch_filters_out_hybrid_matches_both_fee_modes():
    for fee in ('paid', 'no_fee'):
        assert policy().evaluate_special_issue(issue(), now=NOW, filters={'fee': fee})['visible']
    assert not policy().evaluate_special_issue(issue(), now=NOW, filters={'publisher': 'Wiley'})['visible']


def test_other_publishers_pass_content_gate_despite_rank_penalty():
    row = issue(publisher='MDPI')
    row['match']['rank_score'] = 45
    result = policy().evaluate_special_issue(row, now=NOW)
    assert result['recommended'] and result['score'] == 90 and result['rank_score'] == 45


def test_identity_conflict_and_incomplete_scope_stay_pending():
    assert not policy().evaluate_special_issue(issue(identity_status='conflict'), now=NOW)['recommended']
    assert not policy().evaluate_special_issue(issue(scope_is_complete=False), now=NOW)['recommended']
    assert policy().evaluate_special_issue(issue(identity_status='conflict'), now=NOW, view='unverified')['visible']


def test_explicit_score_stale_does_not_qualify_until_reassessed():
    row = issue()
    row['match']['stale'] = True
    assert not policy().evaluate_special_issue(row, now=NOW)['recommended']


def test_deadline_unknown_can_be_inspected_with_filter_not_fabricated():
    row = issue(deadline='')
    result = policy().evaluate_special_issue(row, now=NOW, view='unverified', filters={'deadline': 30})
    assert result['visible'] and result['days_remaining'] is None


def test_unknown_unrelated_does_not_become_recommendation():
    row = issue(publisher='unknown')
    row['match'].update(score=5, formal=False)
    result = policy().evaluate_special_issue(row, now=NOW, filters={'publisher': 'Elsevier'})
    assert not result['visible']


def test_policy_does_not_mutate_durable_item():
    row = issue()
    before = deepcopy(row)
    policy().evaluate_special_issue(row, now=NOW)
    assert row == before
