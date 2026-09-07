"""Authenticated Flask requests through the real queue, pipeline and SQL store.

Sessions are test fixtures, not live Google sign-ins. Organizer/model responses
are synthetic; no provider is contacted or business-quality result inferred.
"""
import csv
import io
import os
import uuid

import pytest
import app as appmod
from tracker import event_intel_jobs as J, event_intel_store as S
from tracker import event_intel_harvest as H, event_intel_resolve as R

pytestmark = pytest.mark.skipif(not os.getenv('DATABASE_URL'), reason='requires disposable PostgreSQL')
BASE = '/p2/b2b-agents/event-conference-intelligence'
SOURCE = 'https://fixture-event.example/exhibitors'


def client(email=None):
    result = appmod.app.test_client()
    if email:
        with result.session_transaction() as session:
            session['google_user'] = {'email': email, 'name': 'Fixture reviewer'}
    return result


@pytest.fixture
def owner():
    email = 'http-' + uuid.uuid4().hex + '@position2.com'
    yield client(email), email
    with J.db() as conn, conn.cursor() as cur:
        cur.execute('SELECT run_id FROM evi_jobs WHERE email=%s', (email,))
        ids = [row[0] for row in cur.fetchall()]
    for run_id in ids:
        J.cancel(run_id, email)


def resolve_fixture(query, year_hint):
    return {'ok': True, 'event': {'name': 'Fixture Forum', 'website': 'https://fixture-event.example',
            'starts_on': '2027-04-01', 'ends_on': '2027-04-02', 'city': 'Boston', 'country': 'USA'},
            'pages': [{'url': SOURCE, 'kind': 'exhibitor_list'},
                      {'url': 'https://fixture-event.example/sponsors', 'kind': 'sponsor_list'}]}


def fetch_fixture(url):
    return {'status': S.SOURCE_OK, 'http_status': 200, 'note': '',
            'text': '2027 Exhibitors: Acme https://acme.example' if url == SOURCE else '2026 Sponsors: Old Company'}


def extract_fixture(*args):
    return {'rows': [{'org_name': 'Acme', 'org_domain': 'acme.example', 'role': S.ROLE_EXHIBITOR,
                      'source_url': SOURCE}], 'note': '', 'error': None}


def test_request_worker_report_and_csv_preserve_evidence_and_ownership(owner, monkeypatch):
    http, email = owner
    monkeypatch.setattr(R, 'resolve_event', resolve_fixture)
    monkeypatch.setattr(H, 'fetch_page', fetch_fixture)
    monkeypatch.setattr(H, '_extract_chunk', extract_fixture)
    payload = {'mode': 'lookup', 'query': 'Fixture Forum', 'request_key': 'same-request'}
    submitted = http.post(BASE+'/run', json=payload)
    assert submitted.status_code == 200
    run_id = submitted.get_json()['run_id']
    assert http.post(BASE+'/run', json=payload).get_json()['run_id'] == run_id
    assert http.post(BASE+'/run', json=dict(payload, query='Different')).status_code == 400
    path = BASE+'/runs/'+str(run_id)
    assert http.get(path+'/status').get_json()['stage'] == 'queued'
    assert J.run_once()
    response = http.get(path)
    assert response.status_code == 200
    run = response.get_json()
    assert run['status'] == 'complete'
    assert run['execution_ledger']['state'] == 'complete'
    assert len(run['execution_ledger']['stages']) == 3
    assert run['summary']['participants'] == len(run['participants']) == 1
    assert run['summary']['sources_unreadable'] == 1
    assert run['participants'][0]['evidence']['status'] == 'literal_support_only'
    assert run['evidence_ledger']
    result = http.get(path+'/export.csv')
    assert result.status_code == 200 and result.headers['Cache-Control'] == 'no-store'
    rows = list(csv.DictReader(io.StringIO(result.get_data(as_text=True))))
    assert len(rows) == 1
    row = rows[0]
    assert row['Organisation'] == run['participants'][0]['org_name'] == 'Acme'
    assert row['Source page'] == SOURCE
    assert row['Evidence status'] == 'literal_support_only'
    assert row['Requested edition'] == '2027'
    assert row['Observed roster editions'] == '2027'
    assert row['Unreadable sources'] == '1'
    assert row['Run status'] == run['status']
    assert 'not an attendee list' in row['Roster caveat']
    assert row['Coverage'] == 'Not independently verified'
    stranger = client('other-'+uuid.uuid4().hex+'@position2.com')
    for suffix in ('', '/status', '/export.csv'):
        assert stranger.get(path+suffix).status_code == 404
    assert stranger.post(path+'/cancel').status_code == 404
    assert http.get(BASE).status_code == 200


def test_owned_http_cancellation_prevents_worker_execution(owner, monkeypatch):
    http, email = owner
    run_id = http.post(BASE+'/run', json={'query': 'Cancel me', 'request_key': 'cancel'}).get_json()['run_id']
    path = BASE+'/runs/'+str(run_id)
    assert http.post(path+'/cancel').get_json()['cancelled'] is True
    def unexpected(*args):
        pytest.fail('Cancelled job reached research')
    monkeypatch.setattr(R, 'resolve_event', unexpected)
    assert J.run_once() is False
    run = http.get(path).get_json()
    assert run['stage'] == 'cancelled' and run['status'] == 'failed'
    assert run['execution_ledger']['state'] == 'cancelled'
    assert http.post(path+'/cancel').get_json()['cancelled'] is False


@pytest.mark.parametrize('email', [None, 'external@example.com'])
def test_cancellation_and_run_details_require_internal_authentication(email):
    http = client(email)
    assert http.post(BASE+'/run', json={'query': 'Forbidden'}).status_code in (302, 401, 403)
    assert http.post(BASE+'/runs/1/cancel').status_code in (302, 401, 403)
    assert http.get(BASE+'/runs/1').status_code in (302, 401, 403)
