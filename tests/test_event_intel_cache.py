"""Fresh-read extraction reuse never promotes model claims to verified facts."""
import os
import uuid
from contextlib import contextmanager
import pytest
from tracker import event_intel_cache as C, event_intel_jobs as J, event_intel_store as S

pytestmark = pytest.mark.skipif(not os.getenv('DATABASE_URL'), reason='requires disposable PostgreSQL')


@contextmanager
def worker(email):
    rid = J.start(email, 'lookup', 'Fixture', {}, uuid.uuid4().hex)
    job = J.claim()
    assert job['run_id'] == rid
    marker = J.CURRENT.set(job)
    try:
        yield job
    finally:
        J.CURRENT.reset(marker)
        J.cancel(rid, email)


def complete(job):
    S.update_run(job['run_id'], status='complete')
    # Tests explicitly simulate successful worker completion, with no provider.
    with J.db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE evi_jobs SET state='complete' WHERE run_id=%s", (job['run_id'],))


def result():
    return {'rows': [{'org_name': 'Acme', 'role': 'exhibitor', 'evidence': {'status':'literal_support_only'}}],
            'note': '', 'error': None, 'spend': {'calls': 1},
            'coverage': {'chunks_total': 1, 'chunks_read': 1, 'errors': [], 'complete': False}}


def read(extractor, **changes):
    args = dict(text='2027 Exhibitors Acme', url='https://fixture.example/roster', kind='exhibitor_list',
                name='Forum', host='fixture.example', identity='dated-edition', prompt='rules', extractor=extractor)
    args.update(changes)
    return C.extract(**args)


def test_reuse_requires_completed_origin_and_does_not_copy_spend():
    email = uuid.uuid4().hex+'@position2.com'
    calls = []
    def extract(*args):
        calls.append(args)
        return result()
    with worker(email) as first:
        read(extract)
        read(extract)  # Incomplete origin is not a cross-run cache hit.
        assert len(calls) == 2
        complete(first)
    with worker(email):
        hit = read(extract)
        assert len(calls) == 2
        assert hit['spend'] is None
        assert hit['coverage']['reuse']['origin_run_id'] == first['run_id']
        assert hit['rows'][0]['evidence']['status'] == 'literal_support_only'
        assert hit['snapshot']['text_sha256'] == C.text_hash('2027 Exhibitors Acme')
        assert hit['coverage']['complete'] is False
    with worker('other-'+email):
        read(extract)
        assert len(calls) == 3


@pytest.mark.parametrize('change', [
    {'text':'2027 Exhibitors Different'}, {'url':'https://other.example/roster'},
    {'identity':'next-edition'}, {'prompt':'new-rules'}, {'kind':'speaker_list'},
    {'host':'other.example'}, {'name':'Different Forum'},
])
def test_changed_inputs_require_extraction(change):
    email = uuid.uuid4().hex+'@position2.com'
    with worker(email) as first:
        read(lambda *a: result())
        complete(first)
    calls = []
    with worker(email):
        read(lambda *a: calls.append(a) or result(), **change)
    assert len(calls) == 1


@pytest.mark.parametrize('field,value', [('rows', []), ('error', {'detail':'failed'}),
    ('coverage', {'chunks_total':2,'chunks_read':1,'errors':[{}]})])
def test_incomplete_results_are_not_cached(field, value):
    email = uuid.uuid4().hex+'@position2.com'
    with worker(email) as first:
        read(lambda *a: dict(result(), **{field:value}))
        complete(first)
    calls = []
    with worker(email):
        read(lambda *a: calls.append(a) or result())
    assert calls


def test_expiry_and_version_invalidate_cache(monkeypatch):
    email = uuid.uuid4().hex+'@position2.com'
    with worker(email) as first:
        read(lambda *a: result())
        complete(first)
    with J.db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE evi_extraction_cache SET created_at=now()-interval '8 days' WHERE email=%s", (email,))
    calls = []
    with worker(email) as second:
        read(lambda *a: calls.append(a) or result())
        complete(second)
    assert len(calls) == 1
    monkeypatch.setattr(C, 'VERSION', C.VERSION+1)
    with worker(email):
        read(lambda *a: calls.append(a) or result())
    assert len(calls) == 2


def test_repeated_pipeline_fetches_sources_but_reuses_extraction(monkeypatch):
    from tracker import event_intel_harvest as H, event_intel_resolve as R
    email = uuid.uuid4().hex+'@position2.com'
    fetches, calls = [], []
    url = 'https://fixture.example/exhibitors'
    monkeypatch.setattr(R, 'resolve_event', lambda *a: {'ok': True,
        'event': {'name':'Forum', 'starts_on':'2027-04-01', 'website':'https://fixture.example'},
        'pages':[{'url':url, 'kind':'exhibitor_list'}]})
    def fetch(url):
        fetches.append(url)
        return {'status':'ok', 'http_status':200, 'note':'', 'text':'2027 Exhibitors Acme https://acme.example'}
    monkeypatch.setattr(H, 'fetch_page', fetch)
    def extract(*args):
        calls.append(args)
        return {'rows':[{'org_name':'Acme','org_domain':'acme.example','role':'exhibitor','source_url':url}], 'error':None,'note':''}
    monkeypatch.setattr(H, '_extract_chunk', extract)
    runs = []
    for _ in range(2):
        rid = J.start(email, 'lookup', 'Forum', {}, uuid.uuid4().hex)
        runs.append(rid)
        assert J.run_once()
        assert S.get_run(rid, email)['status'] == 'complete'
    assert len(fetches) == 2 and len(calls) == 1
    source = S.get_sources(runs[1])[0]
    reuse = source['metadata']['extraction'][0]['reuse']
    assert reuse['origin_run_id'] == runs[0]
    assert S.get_participants(runs[1])[0]['evidence']['observed_roster_years'] == ['2027']


def test_paginated_rows_keep_their_own_year_evidence(monkeypatch):
    from tracker import event_intel_harvest as H
    def fetch(url):
        return {'status':'ok', 'http_status':200, 'note':'', 'text':
            '2027 Exhibitors Acme' if url.endswith('/1') else 'Exhibitors Other'}
    monkeypatch.setattr(H, 'fetch_page', fetch)
    monkeypatch.setattr(H, 'next_page_links', lambda text, url, **k: ['https://fixture.example/2'] if url.endswith('/1') else [])
    monkeypatch.setattr(H, 'extract_participants', lambda text, *a: {'rows':[
        {'org_name':'Acme' if 'Acme' in text else 'Other','role':'exhibitor'}], 'error':None})
    got = H.harvest_page({'url':'https://fixture.example/1','edition':'2027'}, 'Forum')
    assert got['rows'][0]['evidence']['observed_roster_years'] == ['2027']
    assert got['rows'][1]['evidence']['observed_roster_years'] == []
