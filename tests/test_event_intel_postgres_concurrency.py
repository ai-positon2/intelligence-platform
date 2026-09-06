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
