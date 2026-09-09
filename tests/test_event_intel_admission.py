from unittest.mock import Mock

import pytest

from tracker import event_intel_admission as A

EVENT = dict(name='CMO Summit', website='https://event.example/cmo',
             sources=['https://event.example/cmo'], starts_on='2027-05-11', ends_on='2027-05-12')


def page(text, **over):
    return dict(status='ok', text=text, **over)


@pytest.mark.parametrize('dates', ['May 11–12, 2027', '11-12 May 2027',
                                 '2027-05-11 to 2027-05-12',
                                 'May 11, 2027 through May 12, 2027'])
def test_named_edition_has_literal_support(dates):
    result = A.inspect(EVENT, lambda url: page('CMO Summit: '+dates))
    assert result['reasons'] == []
    assert result['support'] == 'literal_name_and_dates_only'
    assert result['checks'][0]['snapshot']['text_sha256']


@pytest.mark.parametrize('fetched', [
    page('Parent Conference May 11–12, 2027'),
    page('CMO Summit May 14, 2026'),
    page('CMO Summit 2027. Date to be announced.'),
    page('CMO Summit May 11–12, 2027. JavaScript is disabled.',spa='react'),
    page('CMO Summit May 11–12, 2027',truncated=True),
    page('CMO Summit May 11–12, 2027',final_url='https://foreign.example/cmo'),
    {'status':'blocked'},
])
def test_unknown_or_unreadable_editions_are_held_for_review(fetched):
    assert A.inspect(EVENT, lambda url: fetched)['reasons']


@pytest.mark.parametrize('term', ['Invite-only', 'By invitation', 'Members only',
                                 'Application-only', 'Sold out', 'Waitlist'])
def test_date_support_does_not_override_restricted_access(term):
    result = A.inspect(EVENT, lambda url: page('CMO Summit May 11–12, 2027. '+term))
    assert 'access' in result['reasons'][0]
    assert result['support'] == 'unverified'


def test_bound_and_no_third_party_fetch():
    event = dict(EVENT, sources=['https://foreign.example/cmo'] +
                 ['https://event.example/'+str(i) for i in range(5)])
    fetch = Mock(return_value=page('No dated event announcement.'))
    result = A.inspect(event,fetch)
    assert fetch.call_count == A.MAX_PAGES
    assert all('foreign.example' not in call.args[0] for call in fetch.call_args_list)
    assert result['not_read'] == 4


def test_fetch_failure_is_reported_not_raised():
    assert A.inspect(EVENT,Mock(side_effect=RuntimeError('unavailable')))['reasons']


def test_framework_marker_alone_does_not_reject_readable_page():
    result = A.inspect(EVENT,lambda url: page('CMO Summit May 11–12, 2027',spa='wp-json/wp/v2'))
    assert result['reasons'] == []


@pytest.mark.parametrize('copy', ['Available until sold out', 'If sold out, join our list',
                                  'Not invite-only', 'No longer sold out'])
def test_conditional_or_negated_restriction_is_not_a_closure(copy):
    assert A.inspect(EVENT,lambda url: page('CMO Summit May 11–12, 2027. '+copy))['reasons'] == []


def stub_readable_sources(monkeypatch):
    """Synthetic organizer pages for unrelated pipeline/store wiring tests."""
    def inspect_all(events):
        return [A.inspect(e, lambda url, e=e: page(
            e['name']+' '+e['starts_on']+' to '+e['ends_on'])) for e in events]
    monkeypatch.setattr(A,'inspect_all',inspect_all)


def test_pipeline_keeps_unsupported_event_out_of_scoring_and_reports_why(monkeypatch):
    from tests.test_event_intel_recommend import _FakeStore, _wire, _cand, PROFILE
    from tracker import event_intel_pipeline as P, event_intel_harvest as H
    fake = _FakeStore()
    original = A.inspect_all
    _wire(monkeypatch,fake)
    monkeypatch.setattr(A,'inspect_all',original)
    monkeypatch.setattr(H,'fetch_page',lambda url: page('CMO Summit: 2026 agenda coming soon.'))
    monkeypatch.setattr(P.event_intel_discover,'discover',lambda p: {
        'candidates':[_cand('CMO Summit')], 'by_category':{}, 'statuses':{},
        'shortfall':[], 'categories_searched':6,'categories_failed':0,'found':1})
    monkeypatch.setattr(P.event_intel_audit,'audit_famous',lambda c,p: {
        'checked':0,'error':None,'cut':[],'kept':[],'verdicts':{}})
    monkeypatch.setattr(P.event_intel_audit,'promote_alternatives',lambda *a,**k: {
        'promoted':[], 'unconfirmed':[], 'not_attempted':[]})
    score = Mock(return_value={'scored':[],'unscored':[],'errors':[],'batches':0})
    monkeypatch.setattr(P.event_intel_scorer,'score_all',score)
    P._run_recommend(1,'me@p2.example',PROFILE)
    assert score.call_args.args[0] == []
    assert fake.rows == []
    summary = fake.runs[1]['summary']
    assert summary['source_admission'][0]['reasons']
    assert 'parent-event dates' in str(summary)
    assert summary['completion_state'] != 'complete'
