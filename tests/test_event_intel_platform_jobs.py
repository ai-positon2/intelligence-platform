"""Worker-queue behaviour across deploys, crashes and a down worker.

The PostgreSQL tests simulate two worker builds in one process by swapping
event_intel_jobs.code_version and WORKER_ID, and move time by rewriting the
timestamps the queue reads. Each test uses its own build string, so jobs
left queued by other test files in the shared database never match it.
"""
import os
import uuid

import pytest

from tracker import event_intel_jobs as J, event_intel_store as S

sql = pytest.mark.skipif(not os.getenv('DATABASE_URL'), reason='requires disposable PostgreSQL')


def _renew(stop):
    # In a copied context: _renew_loop sets CURRENT for its own thread, and
    # run on this thread directly it would leak a fake lease into every later
    # test's storage writes.
    import contextvars
    contextvars.copy_context().run(J._renew_loop, {'run_id': 1, 'token': 't'}, stop)


class _Stop:
    def __init__(self, beats):
        self.waits = []
        self.beats = beats

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return len(self.waits) > self.beats


# ── lease renewal ─────────────────────────────────────────────────────────

def test_one_heartbeat_error_does_not_end_renewal(monkeypatch):
    outcomes = iter([RuntimeError('db blip'), RuntimeError('db blip'), True, True])
    seen = []

    def beat(job):
        seen.append(1)
        value = next(outcomes)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(J, 'heartbeat', beat)
    stop = _Stop(beats=4)
    _renew(stop)
    assert len(seen) == 4, 'renewal stopped after a transient error'
    # First wait is the normal interval, then quick retries, then back to normal.
    assert stop.waits[:4] == [J.RENEW_SECONDS, J.RENEW_RETRY_SECONDS,
                              J.RENEW_RETRY_SECONDS * 2, J.RENEW_SECONDS]


def test_retries_back_off_but_stay_inside_the_lease(monkeypatch):
    monkeypatch.setattr(J, 'heartbeat', lambda job: (_ for _ in ()).throw(RuntimeError('down')))
    stop = _Stop(beats=8)
    _renew(stop)
    retries = stop.waits[1:]
    assert retries[:4] == [2, 4, 8, 16] and max(retries) == J.RENEW_RETRY_CAP_SECONDS < 90


def test_a_lost_lease_still_ends_renewal(monkeypatch):
    monkeypatch.setattr(J, 'heartbeat', lambda job: False)
    stop = _Stop(beats=10)
    _renew(stop)
    assert len(stop.waits) == 1


# ── one stored copy of each reply ────────────────────────────────────────

def test_an_unmarked_reply_is_stored_once_and_rebuilt_exactly():
    result = {'text': '{"a": 1}', 'raw': '{"a": 1}', 'usage': {}, 'error': None}
    stored = J._stored_response(result)
    assert 'raw' not in stored and stored['_raw_is_text']
    assert J._rehydrate(stored) == result


def test_a_reply_with_citation_markup_keeps_only_the_raw_copy():
    from tracker.claude_websearch import strip_citation_markup
    raw = 'They noted <cite index="1-2">attendees came from 47 states</cite>.'
    result = {'text': strip_citation_markup(raw), 'raw': raw, 'usage': {}}
    assert result['text'] != raw
    stored = J._stored_response(result)
    assert 'text' not in stored and stored['_text_from_raw']
    assert J._rehydrate(stored) == result


def test_an_error_reply_with_no_raw_is_stored_as_is():
    result = {'text': '', 'raw': '', 'error': {'kind': 'transport', 'detail': 'x'}}
    assert J._stored_response(result) == result


# ── PostgreSQL ────────────────────────────────────────────────────────────

@pytest.fixture
def builds(monkeypatch):
    """Two worker builds, A and B, unique to this test."""
    tag = uuid.uuid4().hex[:8]
    state = {'build': 'A'}
    monkeypatch.setattr(J, 'code_version', lambda: 'build-%s-%s' % (state['build'], tag))
    monkeypatch.setattr(J, 'WORKER_ID', 'worker-%s-A' % tag)

    def become(build):
        state['build'] = build
        J.WORKER_ID = 'worker-%s-%s' % (tag, build)
    with J.db():
        pass
    return become


def _email():
    return 'jobs-' + uuid.uuid4().hex + '@position2.com'


def _job_state(run_id):
    with J.db() as conn, conn.cursor() as cur:
        cur.execute('SELECT state FROM evi_jobs WHERE run_id=%s', (run_id,))
        return cur.fetchone()[0]


def _age(run_id, minutes):
    with J.db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE evi_jobs SET created_at=now()-%s*interval '1 minute' WHERE run_id=%s", (minutes, run_id))


def _worker_seen(minutes_ago, like):
    with J.db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE evi_workers SET seen_at=now()-%s*interval '1 minute' WHERE worker_id LIKE %s",
                    (minutes_ago, like))


@sql
def test_a_job_submitted_for_the_new_build_waits_for_the_new_worker(builds):
    email = _email()
    builds('B')                      # the web deployed first
    rid = J.start(email, 'lookup', 'Forum', {}, 'k')
    builds('A')                      # the old worker is still running
    assert J.claim() is None, 'an old-build worker claimed a new-build job'
    assert _job_state(rid) == 'queued'
    assert S.get_run(rid, email)['status'] == 'running'
    builds('B')                      # the worker deploy lands
    job = J.claim()
    assert job and job['run_id'] == rid
    J.cancel(rid, email)


@sql
def test_an_in_flight_job_is_reclaimed_only_by_its_own_build(builds):
    email = _email()
    rid = J.start(email, 'lookup', 'Forum', {}, 'k')
    job = J.claim()                  # build A starts it
    assert job['run_id'] == rid
    with J.db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE evi_jobs SET lease_until=now()-interval '1 second' WHERE run_id=%s", (rid,))
    builds('B')                      # A's process is replaced by a B worker
    assert J.claim() is None
    builds('A')                      # an A worker is still up somewhere
    again = J.claim()
    assert again and again['run_id'] == rid and again['token'] != job['token']
    J.cancel(rid, email)


@sql
def test_a_job_no_live_worker_can_run_fails_in_reader_english_after_the_limit(builds):
    email = _email()
    rid = J.start(email, 'lookup', 'Forum', {}, 'k')     # build A web
    builds('A')
    assert J.claim()['run_id'] == rid                   # build A worker starts it...
    with J.db() as conn, conn.cursor() as cur:          # ...then dies
        cur.execute("UPDATE evi_jobs SET lease_until=now()-interval '50 minutes' WHERE run_id=%s", (rid,))
    _worker_seen(50, J.WORKER_ID)                       # nobody has seen build A since
    builds('B')
    assert J.claim() is None                            # B is alive and polling
    assert J.expire_stale(run_id=rid) == [rid]
    run = S.get_run(rid, email)
    assert run['status'] == 'failed' and run['error'] == J.STRANDED_BY_UPDATE
    assert 'Start it again' in run['error'] and 'Worker code' not in run['error']
    assert _job_state(rid) == 'failed'


@sql
def test_a_stranded_job_is_left_alone_inside_the_limit(builds):
    email = _email()
    builds('B')
    rid = J.start(email, 'lookup', 'Forum', {}, 'k')
    builds('A')
    J.claim()                                           # only build A is alive
    _age(rid, 20)                                       # a 20-minute deploy gap
    assert J.expire_stale(run_id=rid) == []
    assert _job_state(rid) == 'queued'
    J.cancel(rid, email)


@sql
def test_a_queued_job_behind_a_live_matching_worker_never_expires(builds):
    email = _email()
    rid = J.start(email, 'lookup', 'Forum', {}, 'k')
    J.heartbeat({'run_id': -1, 'token': 'x'})           # build A worker is alive (busy)
    _age(rid, 600)
    assert J.expire_stale(run_id=rid) == []
    assert _job_state(rid) == 'queued'
    J.cancel(rid, email)


@sql
def test_with_the_worker_down_a_queued_run_stops_saying_queued(builds):
    email = _email()
    rid = J.start(email, 'lookup', 'Forum', {}, 'k')
    _age(rid, 50)
    with J.db() as conn, conn.cursor() as cur:          # no worker of any build is alive
        cur.execute("UPDATE evi_workers SET seen_at=now()-interval '3 hours'")
    assert J.expire_stale(run_id=rid) == [rid]
    assert S.get_run(rid, email)['error'] == J.STRANDED_NO_WORKER


@sql
def test_stranded_jobs_do_not_block_the_account_from_starting_another(builds, monkeypatch):
    monkeypatch.setenv('EVI_MAX_ACTIVE_PER_ACCOUNT', '2')
    email = _email()
    builds('B')
    stuck = [J.start(email, 'lookup', 'Forum %d' % i, {}, 'k%d' % i) for i in range(2)]
    with pytest.raises(ValueError, match='maximum number'):
        J.start(email, 'lookup', 'Third', {}, 'k-third')
    builds('A')
    J.claim()
    for rid in stuck:
        _age(rid, 50)
    third = J.start(email, 'lookup', 'Third', {}, 'k-third')
    assert third not in stuck
    assert [S.get_run(r, email)['status'] for r in stuck] == ['failed', 'failed']
    J.cancel(third, email)


@sql
def test_the_orphan_sweep_still_only_closes_job_less_runs(builds):
    email = _email()
    rid = J.start(email, 'lookup', 'Forum', {}, 'k')
    orphan = S.save_run(email, 'lookup', 'Legacy')
    with J.db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE evi_runs SET created_at=now()-interval '2 hours' WHERE id IN (%s,%s)", (rid, orphan))
    closed = J.close_orphaned_runs()
    assert orphan in closed and rid not in closed
    J.cancel(rid, email)


@sql
def test_a_reclaimed_job_reissues_its_dead_leases_call_and_prices_it(builds):
    email = _email()
    rid = J.start(email, 'lookup', 'Forum', {}, 'k')
    job = J.claim()
    marker = J.CURRENT.set(job)
    try:
        J.reserve_call('system', 'user', 'claude-sonnet-5', 100, 2)   # in flight when the worker dies
    finally:
        J.CURRENT.reset(marker)
    with J.db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE evi_jobs SET lease_until=now()-interval '1 second' WHERE run_id=%s", (rid,))
    job2 = J.claim()
    assert job2['run_id'] == rid
    marker = J.CURRENT.set(job2)
    try:
        retry = J.reserve_call('system', 'user', 'claude-sonnet-5', 100, 2)
        assert retry['cached'] is None, 'the retry was not allowed to issue the call'
        J.finish_call(retry['id'], {'text': 'ok', 'raw': 'ok', 'error': None, 'usage': {
            'input_tokens': 10, 'output_tokens': 10, 'cache_read_input_tokens': 0,
            'cache_creation_input_tokens': 0, 'web_search_requests': 1}}, 5)
        assert J.reserve_call('system', 'user', 'claude-sonnet-5', 100, 2)['cached']['text'] == 'ok'
    finally:
        J.CURRENT.reset(marker)
    ledger = J.ledger(rid, email)
    assert ledger['abandoned_calls'] == 1 and ledger['unknown_provider_outcomes'] == 0
    estimate = ledger['cost_estimate']
    assert estimate['estimated_usd'] is not None and estimate['partial']
    assert estimate['calls_priced'] == 1 and estimate['calls_estimated_from_reservation'] == 1
    J.cancel(rid, email)


@sql
def test_a_live_leases_own_in_flight_duplicate_is_still_refused(builds):
    email = _email()
    rid = J.start(email, 'lookup', 'Forum', {}, 'k')
    job = J.claim()
    marker = J.CURRENT.set(job)
    try:
        J.reserve_call('system', 'dup', 'claude-sonnet-5', 100, 0)
        with pytest.raises(RuntimeError, match='unknown outcome'):
            J.reserve_call('system', 'dup', 'claude-sonnet-5', 100, 0)
    finally:
        J.CURRENT.reset(marker)
    J.cancel(rid, email)


@sql
def test_a_retryable_failure_is_not_replayed_as_the_answer(builds):
    email = _email()
    rid = J.start(email, 'lookup', 'Forum', {}, 'k')
    job = J.claim()
    marker = J.CURRENT.set(job)
    try:
        first = J.reserve_call('system', 'user', 'claude-sonnet-5', 100, 0)
        J.finish_call(first['id'], {'text': '', 'raw': '', 'usage': {},
                                    'error': {'kind': 'transport', 'detail': '529'}}, 5)
        again = J.reserve_call('system', 'user', 'claude-sonnet-5', 100, 0)
        assert again['cached'] is None and again['id'] != first['id']
        J.finish_call(again['id'], {'text': 'answer', 'raw': 'answer', 'usage': {}, 'error': None}, 5)
        # A deterministic answer is still replayed, with no new row.
        third = J.reserve_call('system', 'user', 'claude-sonnet-5', 100, 0)
        assert third['id'] == again['id'] and third['cached']['text'] == 'answer'
        assert third['cached']['raw'] == 'answer'
    finally:
        J.CURRENT.reset(marker)
    J.cancel(rid, email)


@sql
def test_cancel_is_a_no_op_once_the_run_is_complete(builds):
    email = _email()
    rid = J.start(email, 'lookup', 'Forum', {}, 'k')
    job = J.claim()
    S.update_run(rid, status='complete', stage='done')  # pipeline finished; job row not yet updated
    assert _job_state(rid) == 'running'
    assert J.cancel(rid, email) is False
    run = S.get_run(rid, email)
    assert run['status'] == 'complete' and run['stage'] == 'done' and not run['error']
    assert _job_state(rid) == 'running'


@sql
def test_cancel_still_cancels_a_running_run(builds):
    email = _email()
    rid = J.start(email, 'lookup', 'Forum', {}, 'k')
    assert J.cancel(rid, email) is True
    assert S.get_run(rid, email)['stage'] == 'cancelled'


@sql
def test_old_replies_of_finished_jobs_are_pruned_and_live_ones_kept(builds):
    email = _email()
    done = J.start(email, 'lookup', 'Done', {}, 'k1')
    live = J.start(email, 'lookup', 'Live', {}, 'k2')
    ids = {}
    for rid in (done, live):
        with J.db() as conn, conn.cursor() as cur:
            cur.execute("""INSERT INTO evi_provider_calls(run_id,email,stage,model,prompt_hash,reserved_tokens,
                reserved_searches,result,response,created_at) VALUES (%s,%s,'s','m','h',1,0,'{}'::jsonb,
                '{"text":"REPLY","error":null}'::jsonb, now()-interval '40 days') RETURNING id""", (rid, email))
            ids[rid] = cur.fetchone()[0]
    with J.db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE evi_jobs SET state='complete' WHERE run_id=%s", (done,))
    assert J.prune_provider_replies() >= 1
    with J.db() as conn, conn.cursor() as cur:
        cur.execute('SELECT run_id,response FROM evi_provider_calls WHERE id IN %s', (tuple(ids.values()),))
        rows = dict(cur.fetchall())
    assert 'text' not in rows[done] and rows[done]['pruned_at']
    assert rows[live]['text'] == 'REPLY', 'a reply a live job may still replay was pruned'
    assert J.ledger(done, email)['unknown_provider_outcomes'] == 0
    J.cancel(live, email)
