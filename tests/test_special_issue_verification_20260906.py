from datetime import datetime, timedelta
from copy import deepcopy

from utils import special_issue_service as service
from utils.evidence_cache import EvidenceCache

NOW = datetime(2026, 9, 6, 12)


def make_cache(path):
    cache = EvidenceCache(path)
    cache.initialize()
    return cache


def raw(**changes):
    item = {'id': 'call', 'title': 'Soil carbon protection', 'journal': 'Journal A',
            'publisher': 'Elsevier', 'issns': ['1234-5678'], 'deadline': '2026-12-31',
            'official_url': 'https://www.sciencedirect.com/special-issue/42/soil',
            'scope_text': 'Old summary', 'verification_status': 'pending_official'}
    item.update(changes)
    return item


def page(deadline='30 June 2027', closed=False):
    return f'''<title>Soil carbon protection | Journal A</title><main><h1>Soil carbon protection</h1>
    <div>Journal A, ISSN 1234-5678</div><section id="scope"><h2>Aims and Scope</h2>
    <p>We welcome research on soil carbon mineral protection using field observations and experiments.</p>
    <p>Topics include iron-associated carbon retention and digital soil mapping of carbon fractions.</p></section>
    <p>Submission deadline: {deadline}</p>{'<p>Submissions closed</p>' if closed else ''}</main>'''


def test_disjoint_issns_never_borrow_near_name_metadata():
    item = raw(journal='Soil Science', issns=['1111-1111'])
    library = [{'id': 'wrong', 'name': 'Soil Science Society of America Journal', 'issn': '2222-2222', 'jcr': {'quartile': 'Q1'}}]
    result = service.enrich_special_issue_journal(item, library, easyscholar_ready=False)
    assert not result.get('jcr_quartile')
    assert result['issns'] == ['1111-1111']
    assert not result.get('journal_library_id')


def test_matching_identity_alias_is_allowed_without_fuzzy_fact_transfer():
    library = [{'id': 'right', 'name': 'Journal A', 'issn': ['1234-5678', '2049-3630'], 'jcr': {'quartile': 'Q1'}}]
    result = service.enrich_special_issue_journal(raw(issns=['2049-3630']), library, easyscholar_ready=False)
    assert result['journal_library_id'] == 'right'


def test_aggregator_cannot_be_verified_as_official(tmp_path, monkeypatch):
    requested = []
    monkeypatch.setattr(service, '_fetch_official_page', lambda url: requested.append(url) or page())
    item = raw(official_url='https://aggregate.example/call/42', source_evidence=[
        {'is_aggregator': True, 'url': 'https://aggregate.example/call/42', 'updated_at': '2026-09-05'}])
    result = service.verify_special_issue(item, now=NOW, cache=make_cache(tmp_path / 'c.sqlite'))
    assert result['verification_status'] == 'aggregator_unverified'
    assert not requested


def test_expired_old_deadline_does_not_skip_official_extension_check(tmp_path, monkeypatch):
    requested = []
    monkeypatch.setattr(service, '_fetch_official_page', lambda url: requested.append(url) or page())
    result = service.verify_special_issue(raw(deadline='2026-09-01', verification_status='expired', call_status='closed'), now=NOW, cache=make_cache(tmp_path / 'c.sqlite'))
    assert len(requested) == 1
    assert result['deadline'] == '2027-06-30'
    assert result['verification_status'] == 'official_verified'
    assert result['call_status'] == 'open'
    assert result['deadline_history'][0]['previous'] == '2026-09-01'


def test_network_failure_never_reopens_confirmed_closed_call(tmp_path, monkeypatch):
    def fail(url):
        raise OSError('timeout')
    monkeypatch.setattr(service, '_fetch_official_page', fail)
    result = service.verify_special_issue(raw(verification_status='closed', call_status='closed'), now=NOW, cache=make_cache(tmp_path / 'c.sqlite'))
    assert result['verification_status'] == 'closed'
    assert result['call_status'] == 'closed'


def test_deadline_not_adopted_from_unrelated_page(tmp_path, monkeypatch):
    monkeypatch.setattr(service, '_fetch_official_page', lambda url: '<main>Other title | Other Journal. Submission deadline: 1 January 2030</main>')
    result = service.verify_special_issue(raw(), now=NOW, cache=make_cache(tmp_path / 'c.sqlite'))
    assert result['verification_status'] == 'conflict'
    assert result['deadline'] == '2026-12-31'


def test_full_scope_replaces_snippet_instead_of_repeated_concatenation(tmp_path, monkeypatch):
    monkeypatch.setattr(service, '_fetch_official_page', lambda url: page())
    cache = make_cache(tmp_path / 'c.sqlite')
    first = service.verify_special_issue(raw(), now=NOW, cache=cache)
    second = service.verify_special_issue(first, now=NOW + timedelta(days=1), cache=cache)
    assert first['scope_is_complete'] and len(first['scope_paragraphs']) >= 2
    assert 'Old summary' not in first['scope_text']
    assert first['scope_text'] == second['scope_text']


def test_identity_query_parameters_and_deadline_independent_dedupe():
    first = raw(official_url='https://www.sciencedirect.com/special-issue?id=1&utm_source=x')
    extended = raw(deadline='2027-06-30', official_url='https://www.sciencedirect.com/special-issue?id=1')
    other = raw(id='other', official_url='https://www.sciencedirect.com/special-issue?id=2')
    assert service.special_issue_dedupe_key(first) == service.special_issue_dedupe_key(extended)
    assert service.special_issue_dedupe_key(first) != service.special_issue_dedupe_key(other)
    merged = service.merge_special_issue_records([first, extended, other])
    assert len(merged) == 2
    assert merged[0].get('deadline_candidates')


def test_embedded_publisher_domain_is_not_publisher_evidence():
    assert service.infer_special_issue_publisher('', 'https://evil.example/?site=nature.com') == 'unknown'
    assert service.infer_special_issue_publisher('', 'https://nature.com.evil.example/call') == 'unknown'


def test_unknown_deadline_is_retained_pending_and_source_times_preserved():
    item = service.normalize_special_issue(raw(deadline='', updated_at='2026-09-03', source_record_id='42'), source='elsevier', fetched_at=NOW.isoformat(), today=NOW.date())
    assert item is not None and item['deadline'] == ''
    assert item['source_evidence'][0]['updated_at'] == '2026-09-03'


def test_verifier_does_not_mutate_its_input(tmp_path, monkeypatch):
    monkeypatch.setattr(service, '_fetch_official_page', lambda url: page())
    item = raw()
    before = deepcopy(item)
    service.verify_special_issue(item, now=NOW, cache=make_cache(tmp_path / 'c.sqlite'))
    assert item == before
