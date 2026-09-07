"""Edition/owner isolation and honest evidence/results; synthetic, offline data."""
from datetime import date, timedelta
import os
import uuid

import pytest
from tracker import event_intel_planning as P, event_intel_store as S

SQL = pytest.mark.skipif(not os.getenv('DATABASE_URL'), reason='requires disposable PostgreSQL')


def payload(**changes):
    return dict(action='meetings', currency='USD', **changes)


def test_unknown_results_are_distinct_from_zero():
    assert P.validate(payload())['meetings'] is None
    result = P.validate(payload(meetings=0, actual_spend='0', results_as_of=str(date.today())))
    assert result['meetings'] == 0 and result['actual_spend'] == '0.00'
    assert result['support'] == 'user_reported'


@pytest.mark.parametrize('changes', [
    {'meetings': -1}, {'meetings': True}, {'meetings': '1.5'},
    {'actual_spend': 'NaN'}, {'actual_spend': 'Infinity'}, {'actual_spend': '.001'},
    {'reported_pipeline': '1000000000001'}, {'meetings': 0},
    {'meetings': 1, 'results_as_of': str(date.today()+timedelta(days=1))},
    {'target_domains': 'person@example.com'}, {'target_domains': 'example.com/path'},
    {'notes': 'x'*4001},
])
def test_invalid_results_rejected(changes):
    with pytest.raises(ValueError):
        P.validate(payload(**changes))


def test_domains_normalize_without_guessing_company_names():
    assert P.domains('https://www.Example.com/; example.com,other.org') == ['example.com', 'other.org']
    with pytest.raises(ValueError):
        P.domains('Example Company')


@pytest.mark.parametrize('past,role,status,years,expected', [
    (False, 'exhibitor', 'literal_support_only', 'current', 'announced'),
    (True, 'sponsor', 'literal_support_only', 'current', 'historical'),
    (False, 'attendee_declared', 'literal_support_only', 'current', 'edition_not_established'),
    (False, 'exhibitor', 'unverified', 'current', 'edition_not_established'),
    (False, 'exhibitor', 'literal_support_only', None, 'edition_not_established'),
    (False, 'exhibitor', 'literal_support_only', [2001], 'edition_not_established'),
])
def test_overlay_does_not_turn_historical_or_unverified_rosters_into_future_attendance(past, role, status, years, expected):
    day = date.today()+timedelta(days=-400 if past else 400)
    event = {'starts_on': str(day)}
    row = {'org_domain': 'acme.example', 'org_name': 'Acme', 'role': role,
           'source_url': 'https://event.example/roster',
           'evidence': {'status': status, 'observed_roster_years': [day.year] if years == 'current' else years}}
    result = P.overlay(event, [row], ['acme.example', 'other.example'])
    assert result['matches'][0]['timing'] == expected
    assert result['suggested_action'] == ('monitor' if past else 'meetings' if expected == 'announced' else None)
    assert result['not_observed'] == ['other.example']
    assert P.overlay(event, [dict(row, source_url='')], ['acme.example'])['matches'] == []


@pytest.fixture
def fixture():
    email = 'plan-'+uuid.uuid4().hex+'@position2.com'
    profile = S.save_profile(email, {'client_name': 'Position2', 'classification': 'b2b_to_marketing'})
    other = S.save_profile(email, {'client_name': 'Other', 'classification': 'b2b_to_marketing'})
    assert profile and other
    def run(day='2027-04-01'):
        rid = S.save_run(email, 'lookup', 'Forum')
        S.save_event(rid, {'name': 'Forum', 'starts_on': day, 'city': 'Boston', 'country': 'USA'})
        S.update_run(rid, status='complete')
        return rid
    rid = run()
    identity = P.context(rid, email, profile)['event']['event_identity']
    return email, profile, other, rid, identity, run


@SQL
def test_plan_persistence_is_client_and_edition_specific(fixture):
    email, profile, other, rid, identity, run = fixture
    data = payload(profile_id=profile, event_identity=identity, version=0, meetings=0,
                   results_as_of=str(date.today()), target_domains='acme.example')
    saved = P.save(rid, email, data)
    assert saved['plan']['version'] == 1 and saved['plan']['meetings'] == 0
    assert P.context(run(), email, profile)['plan']['version'] == 1
    assert P.context(rid, email, other)['plan'] is None
    assert P.context(run('2028-04-01'), email, profile)['plan'] is None
    with pytest.raises(P.Conflict):
        P.save(rid, email, data)
    assert P.save(rid, email, dict(data, version=1))['plan']['version'] == 2
    assert S.get_outcomes(email, profile) == {}
    with pytest.raises(LookupError):
        P.context(rid, 'stranger@position2.com', profile)
    with pytest.raises(ValueError):
        P.save(rid, email, dict(data, event_identity='invented'))


@SQL
def test_http_auth_validation_conflict_and_escaping(fixture):
    import app as appmod
    email, profile, other, rid, identity, run = fixture
    http = appmod.app.test_client()
    path = '/p2/b2b-agents/event-conference-intelligence/runs/'+str(rid)+'/plan'
    assert http.get(path).status_code == 302
    with http.session_transaction() as session:
        session['google_user'] = {'email': email}
    data = payload(profile_id=profile, event_identity=identity, notes='<script>alert(1)</script>')
    assert http.post(path, data=data).status_code == 415
    assert http.post(path, json=[]).status_code == 400
    assert http.post(path, json=data).status_code == 200
    assert http.post(path, json=data).status_code == 409
    page = http.get(path, query_string={'profile_id': profile}).get_data(as_text=True)
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in page
    assert '<script>alert(1)</script>' not in page
    with http.session_transaction() as session:
        session['google_user'] = {'email': 'stranger@position2.com'}
    assert http.get(path).status_code == 404
    assert http.post(path, json=data).status_code == 404


@SQL
def test_fixed_profile_and_incomplete_reports(fixture):
    email, profile, other, rid, identity, run = fixture
    fixed = S.save_run(email, 'lookup', 'Forum', profile_id=profile)
    S.save_event(fixed, {'name': 'Forum', 'starts_on': '2027-04-01'})
    with pytest.raises(ValueError, match='Complete'):
        P.context(fixed, email)
    S.update_run(fixed, status='complete')
    assert [p['id'] for p in P.context(fixed, email)['profiles']] == [profile]
    with pytest.raises(ValueError, match='different client'):
        P.context(fixed, email, other)


@pytest.mark.parametrize('key', ['action', 'currency'])
def test_invalid_choice_shapes_are_validation_errors(key):
    for value in ([], {}, None):
        data = payload()
        data[key] = value
        with pytest.raises(ValueError):
            P.validate(data)


@pytest.mark.parametrize('value', [True, 1.5, '1.5', [], None])
def test_invalid_plan_identifiers_rejected_before_access(value):
    with pytest.raises(ValueError):
        P.save(1, 'fixture@position2.com', {'profile_id': value, 'version': 0})


@SQL
def test_foreign_profile_cannot_receive_a_plan(fixture):
    email, profile, other, rid, identity, run = fixture
    foreign = S.save_profile('foreign-'+uuid.uuid4().hex+'@position2.com',
                             {'client_name': 'Foreign', 'classification': 'b2b_to_marketing'})
    with pytest.raises(LookupError):
        P.save(rid, email, payload(profile_id=foreign, event_identity=identity))


@SQL
def test_changing_action_resets_reported_access(fixture):
    email, profile, other, rid, identity, run = fixture
    data = payload(profile_id=profile,event_identity=identity,access_status='confirmed',access_checked_on=str(date.today()))
    saved = P.save(rid,email,data)
    assert saved['plan']['access_status'] == 'confirmed'
    changed = P.save(rid,email,dict(data,action='sponsor',version=1))
    assert changed['plan']['access_status'] == 'unknown'
    assert changed['plan']['access_checked_on'] is None


@SQL
def test_plan_shows_only_selected_event_organizer_links(fixture):
    email, profile, other, rid, identity, run = fixture
    target = S.save_event(rid, {'name':'Other Forum','starts_on':'2027-06-01','website':'https://forum.example'})
    row = next(e for e in S.get_events(rid) if e['id'] == target)
    from tracker.event_intel_identity import event_key
    links = [{'kind':'registration','label':'Register','url':'https://forum.example/register',
              'source_url':'https://forum.example/exhibitors','support':'observed_link_only'},
             {'kind':'registration','label':'Bad','url':'javascript:alert(1)','source_url':'https://forum.example'}]
    S.save_source(rid,target,'https://forum.example/exhibitors','exhibitors','ok',metadata={'access_links':links})
    selected = P.context(rid,email,profile,event_key(row))
    assert len(selected['access_links']) == 1
    assert P.context(rid,email,profile,identity)['access_links'] == []


@SQL
def test_topics_persist_and_render_only_selected_event_evidence(fixture):
    email, profile, other, rid, identity, run = fixture
    target = S.save_event(rid, {'name':'Agenda Forum','starts_on':'2027-06-01','website':'https://forum.example'})
    row = next(e for e in S.get_events(rid) if e['id'] == target)
    from tracker.event_intel_identity import event_key
    from tracker.event_intel_fit import agenda_evidence
    excerpt = agenda_evidence('2027 Agenda\nDemand generation <script>alert(1)</script>',
                              'https://forum.example/agenda','forum.example','agenda')
    S.save_source(rid,target,'https://forum.example/agenda','agenda','ok',metadata={'agenda_excerpts':excerpt})
    data = payload(profile_id=profile,event_identity=event_key(row),topic_interests='demand generation')
    saved = P.save(rid,email,data)
    assert saved['plan']['topic_interests'] == ['demand generation']
    assert saved['fit']['topic_matches'][0]['timing'] == 'announced'
    assert P.context(rid,email,profile,identity)['fit']['topic_matches'] == []
    import app as appmod
    http = appmod.app.test_client()
    with http.session_transaction() as session:
        session['google_user'] = {'email':email}
    page = http.get('/p2/b2b-agents/event-conference-intelligence/runs/'+str(rid)+'/plan',
                    query_string={'profile_id':profile,'event_identity':event_key(row)})
    assert page.status_code == 200
    assert '&lt;script&gt;' in page.get_data(as_text=True)
    assert '<script>alert(1)</script>' not in page.get_data(as_text=True)


@SQL
def test_access_checks_show_failures_and_escape_quoted_terms(fixture):
    email, profile, other, rid, identity, run = fixture
    target=S.save_event(rid,{'name':'Access Forum','starts_on':'2027-06-01','website':'https://forum.example'})
    from tracker.event_intel_identity import event_key
    row=next(e for e in S.get_events(rid) if e['id']==target)
    for status in ('ok','blocked'):
        check={'url':'https://forum.example/register','kind':'registration','status':status,
               'note':'Fixture read','claims':[{'field':'price','text':'USD 99 <script>alert(1)</script>'}] if status=='ok' else []}
        S.save_source(rid,target,check['url'],'access_review',status,metadata={'access_review':check})
    saved=P.save(rid,email,payload(profile_id=profile,event_identity=event_key(row)))
    assert len(saved['access_checks'])==2
    assert P.context(rid,email,profile,identity)['access_checks']==[]
    import app as appmod
    http=appmod.app.test_client()
    with http.session_transaction() as session:
        session['google_user']={'email':email}
    html=http.get('/p2/b2b-agents/event-conference-intelligence/runs/'+str(rid)+'/plan',
                  query_string={'profile_id':profile,'event_identity':event_key(row)}).get_data(as_text=True)
    assert '&lt;script&gt;' in html and 'Registration · blocked' in html
    assert '<script>alert(1)</script>' not in html
