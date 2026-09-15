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


@pytest.mark.parametrize('text', [
    # These connector phrases and this date format were previously rejected
    # on an otherwise perfectly readable, correctly-dated page: _owns_date's
    # allowed-opener list didn't cover them, and _date_patterns had no
    # numeric branch at all.
    'CMO Summit, taking place May 11-12, 2027 in Austin.',
    'CMO Summit will be held May 11-12, 2027 in Austin.',
    'CMO Summit convenes May 11-12, 2027 in Austin.',
    'CMO Summit takes place 5/11/2027 - 5/12/2027 in Austin. Format MM/DD/YYYY.',
    'CMO Summit takes place 05/11/2027 - 05/12/2027 in Austin. Format MM/DD/YYYY.',
])
def test_realistic_connector_phrasing_and_numeric_dates_are_recognised(text):
    result = A.inspect(EVENT, lambda url: page(text))
    assert result['reasons'] == []
    assert result['support'] == 'literal_name_and_dates_only'


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


@pytest.mark.parametrize('name,text', [
    ('CMO Summit 2027','CMO Summit: May 11–12, 2027'),
    ('CMO Summit (CMOS)','CMO Summit: May 11–12, 2027'),
    ('Growth & Marketing Summit','Growth and Marketing Summit: May 11–12, 2027'),
    ('CMO Summit','CMO-Summit: May 11–12, 2027'),
])
def test_safe_name_variations_keep_literal_identity(name,text):
    assert A.inspect(dict(EVENT,name=name),lambda url: page(text))['reasons'] == []


@pytest.mark.parametrize('text', [
    'Parent Conference May 11–12, 2027. CMO Summit dates to be announced.',
    'CMO Summit Europe May 11–12, 2027',
    'Old CMO Summit [https://event.example/CMO-Summit-May-11-12-2027]',
    'CMO Summit 2026. Parent Conference May 11–12, 2027.',
])
def test_neighbor_or_different_edition_cannot_supply_dates(text):
    assert A.inspect(EVENT,lambda url: page(text))['reasons']


def test_shared_year_cross_month_date_range():
    event = dict(EVENT,starts_on='2027-05-31',ends_on='2027-06-02')
    assert A.inspect(event,lambda url: page('CMO Summit May 31–June 2, 2027'))['reasons'] == []


@pytest.mark.parametrize('copy', [
    'Hotel room block is sold out. Event registration is open.',
    'VIP passes are sold out. General admission is open.',
    'VIP dinner is invite-only. General admission is open.',
    # Real organizer copy phrases the same "not the whole event" restriction
    # in ways the original list didn't cover.
    'The VIP suite is sold out. General admission is open.',
    'The VIP box is sold out. General admission is open.',
    'The exhibit booth is sold out. General admission is open.',
    'The sponsor table is sold out. General admission is open.',
])
def test_explicit_other_inventory_does_not_close_general_event(copy):
    r=A.inspect(EVENT,lambda url: page('CMO Summit May 11–12, 2027. '+copy))
    assert r['reasons'] == []
    assert r['checks'][0]['access_observations']


def test_unknown_tier_access_remains_unresolved():
    assert A.inspect(EVENT,lambda url: page('CMO Summit May 11–12, 2027. VIP passes sold out.'))['reasons']


def test_duplicate_fragment_does_not_use_second_source_slot():
    event=dict(EVENT,sources=[EVENT['website']+'#agenda','https://event.example/details'])
    fetch=Mock(side_effect=lambda url: page('CMO Summit May 11–12, 2027') if url.endswith('details') else page('No dated announcement'))
    assert A.inspect(event,fetch)['reasons'] == []
    assert fetch.call_args_list[-1].args[0] == 'https://event.example/details'


def test_dynamic_fallback_records_evidence_without_admitting_it():
    r=A.inspect(EVENT,lambda url: page('CMO Summit May 11–12, 2027. Please enable JavaScript.',spa='root'))
    assert r['reasons']
    assert r['checks'][0]['read_mode'] == 'javascript_fallback'
    assert r['checks'][0]['snapshot']['text_sha256']


@pytest.mark.parametrize('copy',['Application required','Subject to approval'])
def test_application_condition_is_not_confirmed_client_access(copy):
    assert A.inspect(EVENT,lambda url: page('CMO Summit May 11–12, 2027. '+copy))['reasons']


def test_other_inventory_exception_cannot_hide_event_closure():
    text='CMO Summit May 11–12, 2027. Hotel room block sold out. Event registration is open. CMO Summit is cancelled.'
    assert A.inspect(EVENT,lambda url: page(text))['reasons']


def test_intervening_sentence_cannot_transfer_parent_dates():
    text='CMO Summit is planned. Parent Conference May 11–12, 2027.'
    assert A.inspect(EVENT,lambda url: page(text))['reasons']


def test_conflicting_year_in_candidate_name_is_not_normalized_away():
    event=dict(EVENT,name='CMO Summit 2026')
    assert A.inspect(event,lambda url: page('CMO Summit 2026 May 11–12, 2027'))['reasons']


def test_registration_call_to_action_keeps_nearby_named_event():
    text='CMO Summit 2027. Choose your pass to join us May 11–12, 2027.'
    assert A.inspect(EVENT,lambda url: page(text))['reasons'] == []


def test_parent_general_admission_does_not_unlock_named_vip_dinner():
    event=dict(EVENT,name='VIP Dinner')
    text='VIP Dinner May 11–12, 2027. VIP dinner is invite-only. General admission is open.'
    assert A.inspect(event,lambda url: page(text))['reasons']


def test_parent_general_admission_does_not_unlock_named_vip_suite():
    event=dict(EVENT,name='VIP Suite Experience')
    text='VIP Suite Experience May 11–12, 2027. VIP suite is invite-only. General admission is open.'
    assert A.inspect(event,lambda url: page(text))['reasons']
@pytest.mark.parametrize('row,accepted', [
    ({'name':'CMO Summit','startDate':'2027-05-11T09:00:00-07:00','endDate':'2027-05-12T17:00:00-07:00'},True),
    ({'name':'Parent Conference','startDate':'2027-05-11','endDate':'2027-05-12'},False),
    ({'name':'CMO Summit','startDate':'2026-05-11','endDate':'2026-05-12'},False),
    ({'name':'CMO Summit','startDate':'2027-05-11'},False),
    ({'name':'CMO Summit Europe','startDate':'2027-05-11','endDate':'2027-05-12'},False),
    ({'name':'CMO Summit','startDate':'2027-05-11','endDate':'2027-05-12','eventStatus':'https://schema.org/EventCancelled'},False),
    ({'name':'CMO Summit','startDate':'2027-05-11','endDate':'2027-05-12','offers':{'availability':'https://schema.org/SoldOut'}},False),
])
def test_structured_organizer_edition_does_not_borrow_parent_or_old_dates(row, accepted):
    fetched = dict(status='blocked',http_status=200,text='Please enable JavaScript',spa='react',structured_events=[row])
    result = A.inspect(EVENT, lambda url: fetched)
    assert (not result['reasons']) == accepted
    if accepted:
        assert result['support'] == 'organizer_structured_name_and_dates'
        assert result['checks'][0]['structured_snapshot']['text_sha256']
        assert result['checks'][0]['read_mode'] == 'javascript_fallback'


@pytest.mark.parametrize('override', [{'truncated':True},{'http_status':403},{'final_url':'https://foreign.example/'}])
def test_structured_evidence_does_not_override_fetch_boundaries(override):
    fetched = dict(status='blocked',http_status=200,text='',structured_events=[
        {'name':'CMO Summit','startDate':'2027-05-11','endDate':'2027-05-12'}])
    fetched.update(override)
    assert A.inspect(EVENT,lambda url:fetched)['reasons']


def test_extract_only_typed_event_jsonld_and_ignore_broken_scripts():
    from tracker.event_intel_structured import events
    markup = '<script>window.event={"name":"Invented"}</script>'
    markup += '<script type="application/ld+json">not json</script>'
    markup += '<script type="application/ld+json">{"@graph":[{"@type":"WebPage","name":"Parent"},{"@type":"Event","name":"CMO Summit","startDate":"2027-05-11"}]}</script>'
    assert events(markup) == [{'name':'CMO Summit','startDate':'2027-05-11'}]


@pytest.mark.parametrize("start,end", [
    ("2027-05-11junk", "2027-05-12"),
    ("2027-05-11T99:00:00", "2027-05-12"),
    ("2027-05-11", None),
])
def test_structured_dates_require_complete_valid_values(start, end):
    fetched = dict(status='blocked', http_status=200, text='', structured_events=[
        {'name':'CMO Summit','startDate':start,'endDate':end}])
    assert A.inspect(EVENT, lambda _: fetched)['reasons']


def test_structured_metadata_cannot_clear_visible_access_restriction():
    fetched = dict(status='blocked', http_status=200,
        text='CMO Summit is invite-only.',
        structured_events=[{'name':'CMO Summit','startDate':'2027-05-11','endDate':'2027-05-12'}])
    assert A.inspect(EVENT, lambda _: fetched)['reasons']
