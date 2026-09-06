"""Native-only transaction checks. Use a disposable, otherwise idle database.

The explicit opt-in prevents the single-session PGlite adapter from being
reported as evidence for PostgreSQL concurrency or rollback guarantees.
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from tracker import event_intel_jobs as J

pytestmark = pytest.mark.skipif(
    not os.getenv('DATABASE_URL') or os.getenv('EVI_TEST_NATIVE_POSTGRES') != '1',
    reason='requires disposable native PostgreSQL and EVI_TEST_NATIVE_POSTGRES=1')


@pytest.fixture
def account():
    # Complete schema initialization before opening simultaneous transactions.
    with J.db():
        pass
    email = 'native-' + uuid.uuid4().hex + '@example.test'
    yield email
    with J.db() as conn, conn.cursor() as cur:
        cur.execute('SELECT run_id FROM evi_jobs WHERE email=%s', (email,))
        ids = [row[0] for row in cur.fetchall()]
    for run_id in ids:
        J.cancel(run_id, email)


def parallel_pair(fn):
    ready = Barrier(2, timeout=10)
    def invoke(index):
        ready.wait()
        return fn(index)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(invoke, index) for index in range(2)]
        return [future.result(timeout=20) for future in futures]


def test_simultaneous_submission_creates_only_one_run(account):
    ids = parallel_pair(lambda _: J.start(account, 'lookup', 'Forum', {}, 'same-key'))
    assert ids[0] == ids[1]
    with J.db() as conn, conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM evi_runs WHERE email=%s', (account,))
        assert cur.fetchone()[0] == 1


def test_two_workers_claim_distinct_jobs(account):
    expected = {J.start(account, 'lookup', 'Forum', {}, str(i)) for i in range(2)}
    claims = parallel_pair(lambda _: J.claim())
    assert all(claims)
    assert {job['run_id'] for job in claims} == expected
    assert len({job['token'] for job in claims}) == 2
    assert all(job['email'] == account for job in claims)


def test_simultaneous_reservations_cannot_exceed_account_limit(account, monkeypatch):
    run_id = J.start(account, 'lookup', 'Forum', {}, 'budget')
    job = J.claim()
    assert job['run_id'] == run_id
    monkeypatch.setenv('EVI_DAILY_CALL_LIMIT', '1')
    def reserve(index):
        marker = J.CURRENT.set(job)
        try:
            try:
                J.reserve_call('system', str(index), 'offline-test', 10, 0)
                return 'reserved'
            except RuntimeError as error:
                assert 'budget' in str(error)
                return 'limited'
        finally:
            J.CURRENT.reset(marker)
    assert sorted(parallel_pair(reserve)) == ['limited', 'reserved']
    with J.db() as conn, conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM evi_provider_calls WHERE run_id=%s', (run_id,))
        assert cur.fetchone()[0] == 1


def test_failed_transaction_rolls_back_run_insertion(account):
    with pytest.raises(RuntimeError, match='injected failure'):
        with J.db() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO evi_runs(email,mode,query) VALUES (%s,'lookup','rollback')", (account,))
            raise RuntimeError('injected failure')
    with J.db() as conn, conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM evi_runs WHERE email=%s', (account,))
        assert cur.fetchone()[0] == 0


@pytest.mark.parametrize('boundary', ['before_response', 'after_response', 'after_stage'])
def test_process_kill_preserves_paid_call_outcome_boundaries(account, boundary):
    import select
    import subprocess
    import sys
    from pathlib import Path
    from tracker import event_intel_store as S
    run_id = J.start(account, 'lookup', 'Probe', {}, 'kill-probe')
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=str(root), EVI_PROBE_BOUNDARY=boundary)
    command = [sys.executable, '-u', str(root/'tests/event_intel_worker_probe.py')]
    child = subprocess.Popen(command, cwd=root, env=env, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([child.stdout], [], [], 20)[0], 'Worker did not reach probe boundary'
        assert child.stdout.readline().strip() == 'PROBE_READY'
        child.kill()
        child.wait(timeout=10)
        assert child.returncode != 0
        # Advance lease eligibility deterministically after a real process kill.
        with J.db() as conn, conn.cursor() as cur:
            cur.execute("UPDATE evi_jobs SET lease_until=now()-interval '1 second' WHERE run_id=%s", (run_id,))
        replacement_env = dict(env, EVI_PROBE_BOUNDARY='')
        subprocess.run(command, cwd=root, env=replacement_env, check=True,
                       capture_output=True, text=True, timeout=30)
        ledger = J.ledger(run_id, account)
        assert ledger['attempts'] == 2
        assert len(ledger['calls']) == 1
        if boundary == 'before_response':
            assert ledger['state'] == 'failed'
            assert ledger['unknown_provider_outcomes'] == 1
            assert S.get_events(run_id) == []
            assert 'unknown outcome' in S.get_run(run_id, account)['error']
        else:
            assert ledger['state'] == 'complete'
            assert ledger['unknown_provider_outcomes'] == 0
            assert len(ledger['stages']) == 1
            assert [event['name'] for event in S.get_events(run_id)] == ['Recovered probe event']
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


def test_cancellation_waits_for_valid_write_then_fences_old_worker(account):
    import time
    from tracker import event_intel_store as S
    run_id = J.start(account, 'lookup', 'Cancellation', {}, 'cancel-write')
    job = J.claim()
    assert job['run_id'] == run_id
    with ThreadPoolExecutor(max_workers=1) as executor:
        marker = J.CURRENT.set(job)
        try:
            with J.db() as conn, conn.cursor() as cur:
                cur.execute("INSERT INTO evi_events(run_id,name) VALUES (%s,'Write before cancellation')", (run_id,))
                cancelled = executor.submit(J.cancel, run_id, account)
                # Observe a real PostgreSQL lock wait, not a timing-only guess.
                deadline = time.monotonic() + 5
                waiting = False
                while time.monotonic() < deadline:
                    with J.db() as observer, observer.cursor() as watch:
                        watch.execute("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type='Lock' AND query LIKE %s", ('%'+account+'%',))
                        waiting = watch.fetchone()[0] > 0
                    if waiting:
                        break
                    time.sleep(0.02)
                assert waiting, 'Cancellation did not wait for the valid worker transaction'
            assert cancelled.result(timeout=10)
            assert S.save_event(run_id, {'name': 'Stale write after cancellation'}) is None
        finally:
            J.CURRENT.reset(marker)
    assert S.get_run(run_id, account)['stage'] == 'cancelled'
    assert [event['name'] for event in S.get_events(run_id)] == ['Write before cancellation']


def test_simultaneous_plan_edits_preserve_one_winner(account):
    from tracker import event_intel_planning as P, event_intel_store as S
    profile = S.save_profile(account, {'client_name': 'Fixture', 'classification': 'b2b_to_marketing'})
    rid = S.save_run(account, 'lookup', 'Forum', profile_id=profile)
    S.save_event(rid, {'name': 'Forum', 'starts_on': '2027-04-01'})
    S.update_run(rid, status='complete')
    identity = P.context(rid, account)['event']['event_identity']
    def edit(index):
        try:
            P.save(rid, account, {'profile_id': profile, 'event_identity': identity,
                   'version': 0, 'action': 'attend', 'currency': 'USD', 'notes': str(index)})
            return 'saved'
        except P.Conflict:
            return 'conflict'
    assert sorted(parallel_pair(edit)) == ['conflict', 'saved']
    assert P.context(rid, account)['plan']['version'] == 1


def test_cancelled_worker_cannot_publish_reusable_extraction(account):
    from tracker import event_intel_cache as C
    rid = J.start(account, 'lookup', 'Forum', {}, 'cache-cancel')
    job = J.claim()
    assert job['run_id'] == rid
    assert J.cancel(rid, account)
    marker = J.CURRENT.set(job)
    try:
        with pytest.raises(Exception, match='lease expired or cancelled'):
            C.extract('text', 'https://fixture.example', 'exhibitor_list', 'Forum',
                      'fixture.example', 'edition', 'rules', lambda *a: {
                          'rows':[{'org_name':'Acme'}], 'coverage':{'chunks_total':1,'chunks_read':1}})
    finally:
        J.CURRENT.reset(marker)
    with J.db() as conn, conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM evi_extraction_cache WHERE email=%s', (account,))
        assert cur.fetchone()[0] == 0
