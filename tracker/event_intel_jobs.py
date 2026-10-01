"""PostgreSQL queue and resumable stages for the event agent.

A worker owns a renewable lease. Database triggers fence writes from expired
owners. Completed stage results survive retries; unknown provider outcomes
are never represented as zero-cost success.
"""
import contextvars
import datetime
import hashlib
import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor as BaseExecutor
from contextlib import contextmanager

CURRENT = contextvars.ContextVar('event_job', default=None)
STAGE = contextvars.ContextVar('event_stage', default='pipeline')


class ContextExecutor(BaseExecutor):
    def submit(self, fn, /, *args, **kwargs):
        context = contextvars.copy_context()
        return super().submit(context.run, fn, *args, **kwargs)


@contextmanager
def db():
    from . import event_intel_store as store
    conn = store._pg_conn()
    if conn is None:
        raise RuntimeError('Event job storage is unavailable')
    try:
        store._ensure_tables(conn)
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS evi_jobs (
        run_id INTEGER PRIMARY KEY REFERENCES evi_runs(id), email TEXT NOT NULL,
        request_key TEXT NOT NULL, payload JSONB NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
        token TEXT, lease_until TIMESTAMPTZ, attempts INTEGER NOT NULL DEFAULT 0,
        cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(email,request_key))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS evi_stages (
        run_id INTEGER NOT NULL REFERENCES evi_runs(id), stage TEXT NOT NULL,
        version TEXT NOT NULL, result JSONB NOT NULL, elapsed_ms INTEGER NOT NULL,
        completed_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(run_id,stage))''')
    cur.execute('''CREATE TABLE IF NOT EXISTS evi_provider_calls (
        id BIGSERIAL PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES evi_runs(id), email TEXT NOT NULL,
        stage TEXT NOT NULL, model TEXT NOT NULL, prompt_hash TEXT NOT NULL,
        reserved_tokens BIGINT NOT NULL, reserved_searches INTEGER NOT NULL,
        result JSONB, response JSONB, elapsed_ms INTEGER, created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
    # Which worker builds are alive. claim() only takes a job whose payload
    # names this worker's own code and runtime, so a job waiting on a build
    # that no longer runs anywhere has to be recognised as stranded rather
    # than left queued forever; this table is how (see expire_stale).
    cur.execute('''CREATE TABLE IF NOT EXISTS evi_workers (
        worker_id TEXT PRIMARY KEY, code_version TEXT NOT NULL,
        runtime_versions JSONB NOT NULL, seen_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_evi_jobs_queue ON evi_jobs(state,lease_until,created_at)')
    cur.execute('CREATE INDEX IF NOT EXISTS idx_evi_calls_account ON evi_provider_calls(email,created_at)')
    cur.execute('''CREATE OR REPLACE FUNCTION evi_fence_worker() RETURNS trigger AS $$
        DECLARE worker_token TEXT; target_run INTEGER;
        BEGIN
            worker_token := current_setting('evi.worker_token', true);
            IF worker_token IS NULL OR worker_token = '' THEN
                IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
            END IF;
            IF TG_OP='DELETE' THEN target_run := OLD.run_id;
            ELSIF TG_TABLE_NAME = 'evi_runs' THEN target_run := NEW.id; ELSE target_run := NEW.run_id; END IF;
            PERFORM 1 FROM evi_jobs WHERE run_id=target_run AND token=worker_token
                AND state='running' AND NOT cancel_requested AND lease_until > now() FOR SHARE;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Event worker lease expired or cancelled';
            END IF;
            IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
        END; $$ LANGUAGE plpgsql''')
    for table in ('evi_runs','evi_events','evi_candidates','evi_participants','evi_sources','evi_outreach','evi_observations','evi_stages'):
        cur.execute('DROP TRIGGER IF EXISTS evi_worker_guard ON ' + table)
        cur.execute('CREATE TRIGGER evi_worker_guard BEFORE INSERT OR UPDATE OR DELETE ON ' + table + ' FOR EACH ROW EXECUTE FUNCTION evi_fence_worker()')


def start(email, mode, query, kwargs, request_key):
    """Atomic run+job creation. One client-generated key identifies a retry."""
    request_key = str(request_key or '')
    if not request_key or len(request_key) > 128:
        raise ValueError('A request key of 1–128 characters is required')
    request_payload = dict(mode=mode, query=query, kwargs=kwargs)
    # The run's own date, fixed at submission. Every prompt carries "today",
    # so a run resumed after midnight built new prompts, missed every stored
    # reply and paid for the whole run again. Kept out of request_payload:
    # the same request retried tomorrow is still the same request.
    payload = dict(request_payload, code_version=code_version(), runtime_versions=runtime_versions(),
                   as_of=datetime.date.today().isoformat())
    with db() as conn, conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('evi-submit:' + email,))
        cur.execute('SELECT run_id,payload FROM evi_jobs WHERE email=%s AND request_key=%s', (email,request_key))
        existing = cur.fetchone()
        if existing:
            if {k:existing[1].get(k) for k in request_payload} != request_payload:
                raise ValueError('This request key already belongs to different inputs')
            return existing[0]
        # A job nobody can ever run (its worker build is gone, or no worker
        # is up at all) must not hold one of this account's active slots.
        _expire_stale(cur, email=email)
        cur.execute("SELECT count(*) FROM evi_jobs WHERE email=%s AND state IN ('queued','running')", (email,))
        if cur.fetchone()[0] >= int(os.getenv('EVI_MAX_ACTIVE_PER_ACCOUNT','2')):
            raise ValueError('This account already has the maximum number of active event runs')
        cur.execute('''INSERT INTO evi_runs(email,mode,query,profile_id,source_run_id,icp_note,status,stage)
            VALUES (%s,%s,%s,%s,%s,%s,'running','queued') RETURNING id''',
            (email,mode,query,(kwargs.get('profile') or {}).get('id'),kwargs.get('source_run_id'),kwargs.get('icp_note')))
        run_id = cur.fetchone()[0]
        cur.execute('INSERT INTO evi_jobs(run_id,email,request_key,payload) VALUES (%s,%s,%s,%s::jsonb)',
                    (run_id,email,request_key,json.dumps(payload)))
        return run_id


WORKER_ID = '%s-%d-%s' % (os.uname().nodename if hasattr(os, 'uname') else 'host', os.getpid(), uuid.uuid4().hex[:8])
# A worker counts as alive for this long after it last claimed, polled or
# renewed a lease. The idle loop polls every 2 seconds and a busy worker's
# renewal thread every 20, so two minutes is several missed beats.
WORKER_ALIVE_SECONDS = 120

ABANDONED_REASON = ('The worker running this call stopped before its outcome was recorded. '
                    'The call may still have been billed; it is counted at its reservation.')


def _touch_worker(cur):
    cur.execute("""INSERT INTO evi_workers(worker_id,code_version,runtime_versions,seen_at)
        VALUES (%s,%s,%s::jsonb,now()) ON CONFLICT(worker_id) DO UPDATE
        SET code_version=EXCLUDED.code_version,runtime_versions=EXCLUDED.runtime_versions,seen_at=now()""",
                (WORKER_ID, code_version(), json.dumps(runtime_versions())))


def claim():
    """Take the oldest runnable job built for THIS worker's code and runtime.

    A job whose payload names another build is left exactly where it is.
    During a deploy the web and the worker change about twenty minutes
    apart, and failing every run submitted, queued or in flight in that
    window was the old behaviour. Now a new-build job waits for the new
    worker, an old-build job is finished by an old worker if one is still
    up, and expire_stale() fails whatever no live worker can ever run once it
    has waited QUEUE_WAIT_LIMIT_MINUTES.

    Re-claiming a job whose previous lease expired also reconciles that
    lease's provider calls: a call with no recorded outcome can only belong
    to the dead lease, so it is marked abandoned (priced at its reservation
    by event_intel_costs) and reserve_call() lets the retry issue it again.
    """
    token = str(uuid.uuid4())
    with db() as conn, conn.cursor() as cur:
        _touch_worker(cur)
        cur.execute("""SELECT run_id,email,payload,state FROM evi_jobs WHERE NOT cancel_requested
            AND (state='queued' OR (state='running' AND lease_until < now())) AND attempts < 3
            AND payload->>'code_version' = %s AND payload->'runtime_versions' = %s::jsonb
            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""",
                    (code_version(), json.dumps(runtime_versions())))
        row = cur.fetchone()
        if not row:
            return None
        if row[3] == 'running':
            cur.execute("""UPDATE evi_provider_calls SET result=jsonb_build_object(
                    'abandoned',true,'reason',%s::text,'abandoned_at',now()::text,'usage',NULL)
                WHERE run_id=%s AND result IS NULL""", (ABANDONED_REASON, row[0]))
        cur.execute("UPDATE evi_jobs SET state='running',token=%s,lease_until=now()+interval '90 seconds', attempts=attempts+1,updated_at=now() WHERE run_id=%s", (token,row[0]))
        return dict(run_id=row[0],email=row[1],payload=row[2],token=token)


def heartbeat(job):
    with db() as conn, conn.cursor() as cur:
        _touch_worker(cur)
        cur.execute("UPDATE evi_jobs SET lease_until=now()+interval '90 seconds',updated_at=now() WHERE run_id=%s AND token=%s AND state='running' AND NOT cancel_requested RETURNING run_id", (job['run_id'],job['token']))
        return bool(cur.fetchone())


def _queue_wait_limit_minutes():
    try:
        value = int(os.getenv('EVI_QUEUE_WAIT_LIMIT_MINUTES') or 45)
    except ValueError:
        return 45
    return value if value > 0 else 45


STRANDED_BY_UPDATE = ('The research service was updated while this run was waiting, so it could '
                      'not be finished. Nothing more will be spent on it. Start it again.')
STRANDED_NO_WORKER = ('The research service did not pick this run up in time, so it was stopped '
                      'before anything was spent on it. Start it again, and tell an admin if this '
                      'keeps happening.')


def _expire_stale(cur, email=None, run_id=None):
    """Fail jobs no live worker can run once they have waited long enough.

    A job is stranded when it has waited (queued since submission, or
    orphaned since its lease ran out) longer than the limit, counted from
    whichever is later: when it started waiting, or when a worker built from
    its payload's code and runtime was last seen. So a queued job with a
    matching worker alive is never expired here, however long it has waited:
    it is behind other work, not stranded. Runs from the web (start, status) as well as the
    worker, because a worker that is down cannot sweep its own queue."""
    limit = _queue_wait_limit_minutes()
    where, args = '', [limit]
    if email is not None:
        where += ' AND j.email=%s'
        args.append(email)
    if run_id is not None:
        where += ' AND j.run_id=%s'
        args.append(run_id)
    cur.execute("""WITH stranded AS (
            SELECT j.run_id FROM evi_jobs j
            WHERE NOT j.cancel_requested
            AND (j.state='queued' OR (j.state='running' AND j.lease_until < now()))
            AND GREATEST(CASE WHEN j.state='queued' THEN j.created_at ELSE j.lease_until END,
                (SELECT max(w.seen_at) FROM evi_workers w WHERE w.code_version=j.payload->>'code_version'
                    AND w.runtime_versions=j.payload->'runtime_versions'))
                < now() - %s * interval '1 minute'""" + where + """
            FOR UPDATE OF j SKIP LOCKED)
        UPDATE evi_jobs SET state='failed',token=NULL,lease_until=NULL,updated_at=now()
        WHERE run_id IN (SELECT run_id FROM stranded) RETURNING run_id""", args)
    expired = [r[0] for r in cur.fetchall()]
    if expired:
        cur.execute("SELECT EXISTS (SELECT 1 FROM evi_workers WHERE seen_at > now() - %s * interval '1 second')",
                    (WORKER_ALIVE_SECONDS,))
        message = STRANDED_BY_UPDATE if cur.fetchone()[0] else STRANDED_NO_WORKER
        cur.execute("""UPDATE evi_runs SET status='failed',stage='interrupted',error=%s,updated_at=now()
            WHERE id = ANY(%s) AND status='running'""", (message, expired))
    return expired


def expire_stale(email=None, run_id=None):
    with db() as conn, conn.cursor() as cur:
        return _expire_stale(cur, email=email, run_id=run_id)


def cancel(run_id,email):
    """Cancel a queued or running job. A no-op on a run that already ended.

    The pipeline marks a run complete a moment before run_once() marks its
    job complete. A cancel landing in that gap used to overwrite a finished,
    paid-for run with "Cancelled by the user". The job row is locked first,
    so a pipeline write holding it (the fence trigger takes it FOR SHARE)
    commits before the run's status is read in a fresh statement."""
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT state FROM evi_jobs WHERE run_id=%s AND email=%s AND state IN ('queued','running') FOR UPDATE", (run_id,email))
        if not cur.fetchone():
            return False
        cur.execute("SELECT status FROM evi_runs WHERE id=%s", (run_id,))
        status = (cur.fetchone() or [None])[0]
        if status != 'running':
            return False
        cur.execute("UPDATE evi_jobs SET cancel_requested=TRUE,state='cancelled',token=NULL,updated_at=now() WHERE run_id=%s AND email=%s AND state IN ('queued','running') RETURNING run_id", (run_id,email))
        changed = bool(cur.fetchone())
        if changed:
            cur.execute("UPDATE evi_runs SET status='failed',stage='cancelled',error='Cancelled by the user. Calls already reserved or in flight may still be billed.' WHERE id=%s", (run_id,))
        return changed


def stage(name, fn, *args, **kwargs):
    job = CURRENT.get()
    if not job:
        return fn(*args, **kwargs)
    import inspect
    version = hashlib.sha256((code_version() + inspect.getsource(fn) + json.dumps([args,kwargs],sort_keys=True,default=str)).encode()).hexdigest()
    with db() as conn, conn.cursor() as cur:
        cur.execute('SELECT version,result FROM evi_stages WHERE run_id=%s AND stage=%s', (job['run_id'],name))
        previous = cur.fetchone()
    if previous:
        if previous[0] != version:
            raise RuntimeError('A completed stage has different code or inputs; start a new run')
        return previous[1]
    marker = STAGE.set(name)
    began = time.monotonic()
    try:
        result = fn(*args, **kwargs)
        with db() as conn, conn.cursor() as cur:
            cur.execute('INSERT INTO evi_stages(run_id,stage,version,result,elapsed_ms) VALUES (%s,%s,%s,%s::jsonb,%s)',
                        (job['run_id'],name,version,json.dumps(result,default=str),int((time.monotonic()-began)*1000)))
        return result
    finally:
        STAGE.reset(marker)


def _daily_limit(name):
    """A rolling-24-hour cap, only when an operator has set one.

    There is no default. The built-in 100 calls / 5M tokens used to apply
    whenever the variable was absent, which on Railway was always, so two
    full recommend runs (2026-09-29: 30 + 38 calls, 4.8M reserved) stopped
    an account's third run after three calls. Unset, empty, zero or
    unreadable all mean no cap."""
    try:
        value = int(os.getenv(name) or 0)
    except ValueError:
        return 0
    return value if value > 0 else 0


def reserve_call(system,user,model,max_tokens,max_uses):
    job = CURRENT.get()
    if not job:
        return None
    # Byte count is a conservative input-token allowance. Include generous
    # tool-result space; this is an operational token budget, not a dollar quote.
    call_hash = hashlib.sha256(json.dumps([system,user,model,max_tokens,max_uses]).encode()).hexdigest()
    allowance = len((system+user).encode()) + max_tokens + max(0,max_uses)*10000
    with db() as conn, conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('evi-budget:' + job['email'],))
        cur.execute("SELECT 1 FROM evi_jobs WHERE run_id=%s AND token=%s AND state='running' AND NOT cancel_requested AND lease_until>now()", (job['run_id'],job['token']))
        if not cur.fetchone():
            raise RuntimeError('Event run cancelled or lease lost')
        # Abandoned rows (a dead lease's in-flight calls, see claim()) are
        # history, not answers. A recorded reply wins over a newer row still
        # in flight, and a reply that failed for a reason a retry can fix is
        # re-issued rather than replayed: replaying a cached transport error
        # to every later attempt made that failure permanent.
        cur.execute('''SELECT id,response FROM evi_provider_calls WHERE run_id=%s AND stage=%s AND prompt_hash=%s
            AND NOT COALESCE((result->>'abandoned')::boolean, false)
            ORDER BY (response IS NOT NULL) DESC, id DESC''', (job['run_id'],STAGE.get(),call_hash))
        for previous_id, previous in cur.fetchall():
            if previous is None:
                raise RuntimeError('A previous provider call has an unknown outcome; manual reconciliation is required before retry')
            if ((previous.get('error') or {}).get('kind') in RETRYABLE_ERROR_KINDS
                    or previous.get('pruned_at')):
                break
            return {'id': previous_id, 'cached': _rehydrate(previous)}
        call_limit, token_limit = _daily_limit('EVI_DAILY_CALL_LIMIT'), _daily_limit('EVI_DAILY_TOKEN_ALLOWANCE')
        if call_limit or token_limit:
            cur.execute("SELECT count(*),COALESCE(sum(reserved_tokens),0) FROM evi_provider_calls WHERE email=%s AND created_at>=now()-interval '24 hours'", (job['email'],))
            calls,tokens = cur.fetchone()
            if (call_limit and int(calls) >= call_limit) or (token_limit and int(tokens)+allowance > token_limit):
                raise RuntimeError('The account event research budget has been reached')
        cur.execute('INSERT INTO evi_provider_calls(run_id,email,stage,model,prompt_hash,reserved_tokens,reserved_searches) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                    (job['run_id'],job['email'],STAGE.get(),model,call_hash,allowance,max(0,max_uses)))
        return {'id': cur.fetchone()[0], 'cached': None}


# Error kinds from claude_websearch that say nothing about the question and
# everything about the moment it was asked. Their stored reply is kept for the
# ledger but never replayed as the answer to a retry.
RETRYABLE_ERROR_KINDS = ('transport', 'empty_response', 'search_limit',
                         'not_configured', 'no_tool_version')


def _stored_response(result):
    """The reply as stored: one copy of its text, not two.

    claude_websearch returns `text` (citation markup rewritten) and `raw`
    (exactly as sent). For most replies they are identical, and otherwise
    `text` is a pure function of `raw`, so every reply used to be stored
    twice. Only the copy that cannot be rebuilt is kept, with a flag saying
    how to rebuild the other; _rehydrate() reverses it."""
    stored = dict(result)
    text, raw = stored.get('text'), stored.get('raw')
    if not isinstance(text, str) or not isinstance(raw, str) or not raw:
        return stored
    if raw == text:
        stored.pop('raw')
        stored['_raw_is_text'] = True
    else:
        from .claude_websearch import strip_citation_markup
        if strip_citation_markup(raw) == text:
            stored.pop('text')
            stored['_text_from_raw'] = True
    return stored


def _rehydrate(stored):
    if not isinstance(stored, dict):
        return stored
    out = dict(stored)
    if out.pop('_raw_is_text', False):
        out['raw'] = out.get('text')
    if out.pop('_text_from_raw', False):
        from .claude_websearch import strip_citation_markup
        out['text'] = strip_citation_markup(out.get('raw') or '')
    return out


def finish_call(call_id,result,elapsed_ms):
    if call_id is None:
        return
    # Usage may arrive after cancellation, or after the lease was lost and
    # claim() marked the call abandoned. Either way it overwrites the
    # placeholder: a measured outcome beats a reservation estimate.
    metadata = {k: result.get(k) for k in ('usage','tool_version','search_count','error','stop_reason')}
    with db() as conn, conn.cursor() as cur:
        cur.execute('UPDATE evi_provider_calls SET result=%s::jsonb,response=%s::jsonb,elapsed_ms=%s WHERE id=%s',
                    (json.dumps(metadata),json.dumps(_stored_response(result)),elapsed_ms,call_id))


REPLY_RETENTION_DAYS = 30


def prune_provider_replies(days=None):
    """Drop stored reply text older than the retention window.

    Only for jobs that have ended (complete, failed, cancelled): those are
    never claimed again, so nothing can ask to replay the reply. The row, its
    reservation and its usage stay; `response` becomes a small non-NULL
    marker, because NULL means "outcome unknown" to reserve_call()."""
    days = int(days or os.getenv('EVI_REPLY_RETENTION_DAYS') or REPLY_RETENTION_DAYS)
    with db() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE evi_provider_calls c SET response=jsonb_build_object('pruned_at',now()::text,
                'error',c.response->'error','stop_reason',c.response->'stop_reason')
            FROM evi_jobs j WHERE j.run_id=c.run_id AND j.state IN ('complete','failed','cancelled')
            AND c.response IS NOT NULL AND c.response->>'pruned_at' IS NULL
            AND c.created_at < now() - %s * interval '1 day'""", (days,))
        return cur.rowcount


def _renew_loop(job, stop):
    """The renewal thread's body. A module-level function (not a closure
    inside run_once()) so a test can drive it directly, through a real
    thread boundary, without waiting through 20 real seconds of `stop.wait`.

    A new thread starts with its own fresh top-level contextvars Context, not
    a copy of the caller's -- CURRENT.set(job) elsewhere on the main thread
    has no effect here regardless of ordering. heartbeat() itself doesn't
    need CURRENT (it targets evi_jobs, an unguarded table, by the real
    run_id/token bound as query parameters), but store._pg_conn() reads
    CURRENT to set this connection's session-level evi.worker_token, the same
    as every other thread in this codebase that touches storage goes through
    ContextExecutor for. Setting it directly, once, is the equivalent for a
    single dedicated thread: without it, a future guarded-table write added
    here would be silently fenced by the trigger despite a valid lease.
    """
    CURRENT.set(job)
    delay, failures = RENEW_SECONDS, 0
    while not stop.wait(delay):
        try:
            renewed = heartbeat(job)
        except Exception:
            # One database blip used to end renewal for good, so the lease
            # ran out 90 seconds later under a healthy worker and the run was
            # re-claimed mid-flight. Retry sooner, backing off (2, 4, 8, 16,
            # 16... seconds, all well inside the 90-second lease), and let
            # only a definite "this lease is no longer yours" end the loop.
            import logging
            logging.warning('Event lease renewal for run %s failed; retrying', job.get('run_id'), exc_info=True)
            delay = min(RENEW_RETRY_SECONDS * 2 ** failures, RENEW_RETRY_CAP_SECONDS)
            failures += 1
            continue
        if not renewed:
            return
        delay, failures = RENEW_SECONDS, 0


RENEW_SECONDS = 20
RENEW_RETRY_SECONDS = 2
RENEW_RETRY_CAP_SECONDS = 16


ORPHAN_ERROR = ('Interrupted. This run started before runs were queued, and the '
                'process running it stopped before it finished. Start a new run.')


def close_orphaned_runs():
    """Fail runs left 'running' with no queue row, so none spins forever.

    Before the queue, a run lived only in the web process's thread; a deploy
    or restart killed it mid-flight and nothing ever marked it finished. Every
    run is now created together with its evi_jobs row in one transaction
    (start()), so a 'running' run without one can only be one of those. The
    hour's margin is not needed for that argument; it is there so a future
    job-less writer cannot have its fresh run closed from under it."""
    with db() as conn, conn.cursor() as cur:
        cur.execute("""UPDATE evi_runs r SET status='failed',stage='interrupted',error=%s
            WHERE r.status='running' AND r.created_at < now() - interval '1 hour'
            AND NOT EXISTS (SELECT 1 FROM evi_jobs j WHERE j.run_id=r.id) RETURNING r.id""",
                    (ORPHAN_ERROR,))
        return [row[0] for row in cur.fetchall()]


def run_once():
    """Claim one job; daemon heartbeat never performs the research itself."""
    import threading
    from . import event_intel_pipeline as pipeline, event_intel_store as store
    job = claim()
    if not job:
        # Exhausted leases must not remain 'running' forever.
        with db() as conn, conn.cursor() as cur:
            cur.execute("UPDATE evi_jobs SET state='failed',token=NULL WHERE state='running' AND lease_until<now() AND attempts>=3 RETURNING run_id")
            for (run_id,) in cur.fetchall():
                cur.execute("UPDATE evi_runs SET status='failed',stage='interrupted',error='Worker recovery attempts exhausted.' WHERE id=%s", (run_id,))
            # Nor may a job no live worker can run stay queued forever.
            _expire_stale(cur)
        return False
    stop = threading.Event()
    thread = threading.Thread(target=_renew_loop, args=(job, stop), daemon=True)
    thread.start()
    marker = CURRENT.set(job)
    try:
        payload = job['payload']
        if payload.get('code_version') != code_version() or payload.get('runtime_versions') != runtime_versions():
            raise RuntimeError('Worker code or runtime changed after submission. Start a new run rather than mixing research versions.')
        # Replay deterministic writes from saved stage results. Source runs
        # cannot be selected by workroom until they finish successfully.
        with db() as conn, conn.cursor() as cur:
            for table in ('evi_outreach','evi_participants','evi_sources','evi_candidates','evi_observations','evi_events'):
                cur.execute('DELETE FROM ' + table + ' WHERE run_id=%s', (job['run_id'],))
        store.update_run(job['run_id'], status='running', summary={}, error=None)
        pipeline.run_job(job['run_id'],payload['mode'],payload['query'],
                         **dict(payload['kwargs'], as_of=payload.get('as_of')))
    except Exception as exc:
        store.update_run(job['run_id'],status='failed',error=str(exc)[:300])
    finally:
        CURRENT.reset(marker)
        stop.set()
        thread.join(timeout=2)
    with db() as conn, conn.cursor() as cur:
        cur.execute('''UPDATE evi_jobs SET state=CASE WHEN r.status='complete' THEN 'complete' ELSE 'failed' END,
            lease_until=NULL,updated_at=now() FROM evi_runs r
            WHERE evi_jobs.run_id=r.id AND evi_jobs.run_id=%s AND evi_jobs.token=%s AND evi_jobs.state='running' AND evi_jobs.lease_until>now()
            AND r.status IN ('complete','failed') ''',
            (job['run_id'],job['token']))
    return True


def runtime_versions():
    from importlib.metadata import version, PackageNotFoundError
    result = {}
    for name in ('anthropic','requests','urllib3','psycopg2-binary','flask','gunicorn'):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = 'unavailable'
    return result


def code_version():
    from pathlib import Path
    directory = Path(__file__).parent
    paths = sorted(directory.glob('event_intel_*.py')) + [directory/'claude_websearch.py',directory/'event_intel_aliases.json']
    return hashlib.sha256(b''.join(p.read_bytes() for p in paths)).hexdigest()


REPLY_EXCERPT_CHARS = 1500


def ledger(run_id,email,replies=False):
    """The run's execution record. `replies` adds a head and tail excerpt of
    each stored model reply, for an ADMIN diagnosing a stage from what the
    model actually wrote; the route decides who may ask. Without it, reply
    text never leaves the database, as before."""
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT state,attempts,cancel_requested,created_at,updated_at,payload FROM evi_jobs WHERE run_id=%s AND email=%s", (run_id,email))
        row = cur.fetchone()
        if not row:
            return None
        job = dict(zip([c[0] for c in cur.description],row))
        cur.execute('SELECT stage,version,elapsed_ms,completed_at FROM evi_stages WHERE run_id=%s ORDER BY completed_at', (run_id,))
        job['stages'] = [dict(zip([c[0] for c in cur.description],r)) for r in cur.fetchall()]
        cur.execute('SELECT stage,model,prompt_hash,reserved_tokens,reserved_searches,result,elapsed_ms,created_at'
                    + (',response' if replies else '') + ' FROM evi_provider_calls WHERE run_id=%s ORDER BY id', (run_id,))
        job['calls'] = [dict(zip([c[0] for c in cur.description],r)) for r in cur.fetchall()]
        if replies:
            n = REPLY_EXCERPT_CHARS
            for call in job['calls']:
                text = str((_rehydrate(call.pop('response')) or {}).get('text') or '')
                call['reply'] = text if len(text) <= 2 * n else text[:n] + ' [...] ' + text[-n:]
        job['unknown_provider_outcomes'] = sum(c['result'] is None for c in job['calls'])
        job['calls_without_usage'] = sum(not (c['result'] or {}).get('usage') for c in job['calls'])
        # Calls a dead lease left in flight (see claim()). Their outcome is
        # still unknown to us; they are counted at their reservation.
        job['abandoned_calls'] = sum(bool((c['result'] or {}).get('abandoned')) for c in job['calls'])
        job['runtime_versions'] = job['payload'].get('runtime_versions')
        job['code_version'] = job.pop('payload').get('code_version')
        job['billing_status'] = 'usage reported where available; not reconciled to provider invoice'
        from .event_intel_costs import estimate
        job['cost_estimate'] = estimate(job['calls'])
        return job


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--migrate', action='store_true', help='Initialize the schema and exit')
    parser.add_argument('--once', action='store_true', help='Process at most one job and exit')
    args = parser.parse_args()
    if args.migrate:
        with db():
            pass
        close_orphaned_runs()
        prune_provider_replies()
        return
    try:
        close_orphaned_runs()
    except Exception:
        import logging
        logging.exception('Closing orphaned event runs failed')
    if args.once:
        run_once()
        return
    last_prune = 0.0
    while True:
        try:
            if time.monotonic() - last_prune > 3600:
                last_prune = time.monotonic()
                prune_provider_replies()
            if not run_once():
                time.sleep(2)
        except Exception:
            import logging
            logging.exception('Event worker iteration failed')
            time.sleep(5)


if __name__ == '__main__':
    # -m executes this file as __main__. Research and storage import its
    # canonical name, so dispatch there to share CURRENT/STAGE ContextVars.
    from tracker.event_intel_jobs import main as worker_main
    worker_main()
