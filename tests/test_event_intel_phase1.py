"""User-visible regression cases from the September end-to-end audit."""
from datetime import date, timedelta
from unittest.mock import Mock

import pytest

from tracker import event_intel_audit as A, event_intel_report as REP
from tracker import event_intel_rubric as R, event_intel_workroom as W
from tracker import event_intel_http as HTTP, event_intel_pipeline as P
from tracker import event_intel_store as S
from tracker.event_intel_identity import event_key
from tracker.event_intel_policy import eligibility
from tests.test_event_intel_charts import _render, page_script


PROFILE = dict(client_name='Audit Client', classification='b2b_to_marketing',
               geo_scope='USA only', window_months=12, max_events=3)


def event(name, **over):
    row = dict(name=name, starts_on=(date.today()+timedelta(days=30)).isoformat(),
        ends_on=(date.today()+timedelta(days=32)).isoformat(), country='USA',
        city='Boston', confidence='high', website='https://event.example',
        sources=['https://event.example/agenda'], category='vertical_summit',
        format='in_person', total=80, tier='P1', relevance=32)
    row.update(over)
    return row


def test_snapshot_page_and_export_membership_survives_profile_edits(page_script):
    rows = [event('Past winner',total=100,starts_on='2020-01-01',ends_on='2020-01-02'),
            event('Future correct')]
    ranked = R.rank(rows)
    run = dict(id=88,mode='recommend',status='complete',summary={
        'selection':REP.selection_snapshot(ranked, PROFILE)},events=[],participants=[],sources=[])
    shown = REP.present_run(run,dict(PROFILE,max_events=1),rows,{})
    body = _render(page_script,shown)
    assert 'data-event="Future correct"' in body
    assert 'data-event="Past winner"' not in body
    assert shown['summary']['top_five'][0]['name']=='Future correct'
    assert shown['profile']['max_events']==3


def test_saved_adjustment_order_is_the_page_order(page_script):
    rows=[event('Raw leader',total=81),event('Learned leader',total=80,category='side_event')]
    ranked=R.rank(rows)
    ranked['kept']=REP.apply_outcome_pattern(ranked['kept'],{'by_category':{
        'vertical_summit':dict(decisions=3,skipped=3,went_or_going=0)}})
    run=dict(id=99,mode='recommend',status='complete',summary={
        'selection':REP.selection_snapshot(ranked,PROFILE)},events=[],participants=[],sources=[])
    shown=REP.present_run(run,PROFILE,rows,{})
    body=_render(page_script,shown)
    assert body.index('data-event="Learned leader"') < body.index('data-event="Raw leader"')


def test_every_second_tier_candidate_has_a_disposition():
    rows=[event(str(i),total=60,tier='P3',relevance=25) for i in range(8)]
    ranked=R.rank(rows,cap=3)
    assert len(ranked['worth_a_look'])==3 and len(ranked['over_cap'])==5
    assert sum(len(ranked[b]) for b in ('kept','worth_a_look','excluded','over_cap','finished'))==8


def test_legacy_cross_client_claims_are_removed_everywhere():
    row=event('Forum',cross_client_note='4 other clients',cross_client_count=4)
    run=dict(summary=dict(top_five=[row],selection=REP.selection_snapshot(R.rank([row]),PROFILE),
        generic={'comparisons':[{'client_name':'Confidential other client'}]},
        notes=[{'head':'Cross-client check: this list looks generic','detail':'Private comparison'}]))
    shown=REP.present_run(run,PROFILE,[row],{})
    assert '4 other clients' not in str(shown)
    assert 'cross_client_count' not in str(shown)
    assert 'Confidential other client' not in str(shown)
    assert 'Private comparison' not in str(shown)
    assert shown['summary']['generic']['measured'] is False


def test_current_decisions_overlay_the_saved_snapshot():
    row=event('Forum')
    run=dict(summary={'selection':REP.selection_snapshot(R.rank([row]),PROFILE)})
    shown=REP.present_run(run,PROFILE,[row],{event_key(row):dict(decision='going',note='Booked')})
    assert shown['summary']['outcomes']['by_identity'][event_key(row)]['decision']=='going'
    assert shown['candidates'][0]['prior_note']=='Booked'


def test_regions_years_and_unknown_editions_are_distinct():
    usa=event('Money20/20 USA 2026',starts_on='2026-10-18')
    eu=event('Money20/20 Europe 2027',starts_on='2027-06-08')
    assert event_key(usa)!=event_key(eu)
    assert event_key(event('Forum',starts_on=None,run_id=1))!=event_key(event('Forum',starts_on=None,run_id=2))


def test_regional_audit_verdicts_cannot_overwrite(monkeypatch):
    rows=[event('Money20/20 USA',famous=True),event('Money20/20 Europe',famous=True)]
    def check(c,system):
        return dict(rec=dict(name=c['name'],verdict='kept' if 'USA' in c['name'] else 'cut',
                 alternative='Local Forum',why='Separate event evidence'),spend={},error=None)
    monkeypatch.setattr(A,'_audit_one',check)
    audit=A.audit_famous(rows,PROFILE)
    assert len(audit['verdicts'])==2
    assert [c['name'] for c in A.apply_audit(rows,audit)]==['Money20/20 USA']


@pytest.mark.parametrize('over',[
    dict(starts_on='2060-01-01',ends_on='2060-01-02'),dict(country='Australia',city='Sydney'),
    dict(starts_on=None,ends_on=None),dict(confidence='low'),dict(sources=['https://unrelated.example'])])
def test_out_of_scope_or_unverified_events_are_not_admitted(over):
    assert eligibility(event('Forum',**over),PROFILE)


def test_valid_event_and_explicit_exclusion():
    assert eligibility(event('Forum'),PROFILE)==[]
    assert eligibility(event('Forum'),dict(PROFILE,force_exclude='Forum'))


@pytest.mark.parametrize('opener,note',[
    ('It was a pleasure discussing your expansion plans with you at FutureExpo.',''),
    ('Thanks for attending FutureExpo, Alex.',''),
    ('As promised, here is the proposal.','Did not speak with anyone from Acme.')])
def test_no_model_interaction_claim_is_approved(opener,note):
    row=dict(org_name='Acme',person_name='Alex',role='exhibitor',opener=opener,
             angle=opener,fit_note=opener)
    out=W.enforce([row],event_class='exhibited',notes={W.org_key('Acme'):note} if note else {},event_name='FutureExpo')['rows'][0]
    assert out['draft_status']!='ok'
    assert all(opener not in out[k] for k in ('opener','angle','fit_note'))


@pytest.mark.parametrize('address',['127.0.0.1','10.0.0.1','169.254.169.254','::1','fc00::1'])
def test_private_destinations_block_before_http(monkeypatch,address):
    monkeypatch.setattr(HTTP.socket,'getaddrinfo',lambda *a,**k:[(None,None,None,None,(address,80))])
    transport=Mock()
    monkeypatch.setattr(HTTP.urllib3,'HTTPConnectionPool',transport)
    with pytest.raises(ValueError): HTTP.public_get('http://event.example')
    transport.assert_not_called()


def test_redirect_to_private_destination_is_revalidated(monkeypatch):
    monkeypatch.setattr(HTTP.socket,'getaddrinfo',lambda host,*a,**k:[(None,None,None,None,('8.8.8.8' if host=='event.example' else '127.0.0.1',80))])
    pool=Mock();pool.urlopen.return_value.status=302
    pool.urlopen.return_value.headers={'Location':'http://internal.example/private'}
    constructor=Mock(return_value=pool)
    monkeypatch.setattr(HTTP.urllib3,'HTTPConnectionPool',constructor)
    with pytest.raises(ValueError): HTTP.public_get('http://event.example')
    assert constructor.call_count==1
    assert constructor.call_args.args[0]=='8.8.8.8'


def test_failed_discovery_keeps_spend_and_failed_status(monkeypatch):
    updates=[]
    monkeypatch.setattr(P.store,'update_run',lambda rid,**fields:updates.append(fields))
    monkeypatch.setattr(P.event_intel_discover,'discover',lambda p:dict(candidates=[],shortfall=[],statuses={},categories_failed=6,spend={'calls':6,'input_tokens':10000}))
    P._run_recommend(1,'audit@position2.com',PROFILE)
    assert updates[-1]['status']=='failed'
    assert updates[-1]['summary']['spend']['calls']==6


def test_same_series_can_have_two_editions_in_one_year():
    from tracker.event_intel_discover import merge
    first = event('Regional Forum', starts_on='2026-10-01')
    second = event('Regional Forum', starts_on='2026-12-01')
    assert event_key(first) != event_key(second)
    assert len(merge({'vertical_summit': [first, second, dict(first)]})) == 2


@pytest.mark.parametrize('saved,readback', [(0, []), (1, []), (1, [event('Wrong event')])])
def test_incomplete_or_wrong_persistence_cannot_finish_successfully(monkeypatch, saved, readback):
    updates = []
    row = event('Verified Forum')
    monkeypatch.setattr(P.store, 'update_run', lambda rid, **fields: updates.append(fields))
    monkeypatch.setattr(P.event_intel_discover, 'discover', lambda p: dict(
        candidates=[row], spend={'calls': 2}, categories_failed=0))
    monkeypatch.setattr(P.event_intel_audit, 'audit_famous', lambda *a: dict(spend={'calls': 1}))
    monkeypatch.setattr(P.event_intel_audit, 'apply_audit', lambda *a: [row])
    monkeypatch.setattr(P.event_intel_audit, 'promote_alternatives', lambda *a, **k: dict(promoted=[], spend={}))
    monkeypatch.setattr(P.event_intel_scorer, 'score_all', lambda *a: dict(scored=[row], unscored=[], spend={'calls': 1}))
    monkeypatch.setattr(P.store, 'save_candidates', lambda *a: saved)
    monkeypatch.setattr(P.store, 'get_candidates', lambda *a: readback)
    P._run_recommend(1, 'audit@position2.com', PROFILE)
    assert updates[-1]['status'] == 'failed'
    assert updates[-1]['summary']['spend']['calls'] == 4
    assert updates[-1]['summary']['expected_saved'] == 1
    assert [u['summary']['spend']['calls'] for u in updates
            if u.get('summary', {}).get('completion_state') == 'running'] == [2, 3, 3, 4]


def test_failed_save_rows_are_visible_as_incomplete_not_recommendations():
    row = event('Only partial row')
    shown = REP.present_run(dict(status='failed', error='Save failed', summary={}), PROFILE, [row], {})
    assert shown['summary']['selection']['kept'] == []
    assert shown['summary']['selection']['incomplete'][0]['name'] == row['name']
    assert shown['summary']['top_five'] == []
