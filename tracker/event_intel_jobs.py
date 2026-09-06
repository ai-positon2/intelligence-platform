"""PostgreSQL queue and resumable stages for the event agent.

A worker owns a renewable lease. Database triggers fence writes from expired
owners. Completed stage results survive retries; unknown provider outcomes
are never represented as zero-cost success.
"""
import contextvars
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
    payload = dict(request_payload, code_version=code_version(), runtime_versions=runtime_versions())
    with db() as conn, conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('evi-submit:' + email,))
        cur.execute('SELECT run_id,payload FROM evi_jobs WHERE email=%s AND request_key=%s', (email,request_key))
        existing = cur.fetchone()
        if existing:
            if {k:existing[1].get(k) for k in request_payload} != request_payload:
                raise ValueError('This request key already belongs to different inputs')
            return existing[0]
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


def claim():
    token = str(uuid.uuid4())
    with db() as conn, conn.cursor() as cur:
        cur.execute("""SELECT run_id,email,payload FROM evi_jobs WHERE NOT cancel_requested
            AND (state='queued' OR (state='running' AND lease_until < now())) AND attempts < 3
            ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1""")
        row = cur.fetchone()
        if not row:
            return None
        cur.execute("UPDATE evi_jobs SET state='running',token=%s,lease_until=now()+interval '90 seconds', attempts=attempts+1,updated_at=now() WHERE run_id=%s", (token,row[0]))
        return dict(run_id=row[0],email=row[1],payload=row[2],token=token)


def heartbeat(job):
    with db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE evi_jobs SET lease_until=now()+interval '90 seconds',updated_at=now() WHERE run_id=%s AND token=%s AND state='running' AND NOT cancel_requested RETURNING run_id", (job['run_id'],job['token']))
        return bool(cur.fetchone())


def cancel(run_id,email):
    with db() as conn, conn.cursor() as cur:
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
        cur.execute('SELECT id,response FROM evi_provider_calls WHERE run_id=%s AND stage=%s AND prompt_hash=%s', (job['run_id'],STAGE.get(),call_hash))
        previous = cur.fetchone()
        if previous:
            if previous[1] is None:
                raise RuntimeError('A previous provider call has an unknown outcome; manual reconciliation is required before retry')
            return {'id': previous[0], 'cached': previous[1]}
        cur.execute("SELECT count(*),COALESCE(sum(reserved_tokens),0) FROM evi_provider_calls WHERE email=%s AND created_at>=now()-interval '24 hours'", (job['email'],))
        calls,tokens = cur.fetchone()
        if int(calls) >= int(os.getenv('EVI_DAILY_CALL_LIMIT','100')) or int(tokens)+allowance > int(os.getenv('EVI_DAILY_TOKEN_ALLOWANCE','5000000')):
            raise RuntimeError('The account event research budget has been reached')
        cur.execute('INSERT INTO evi_provider_calls(run_id,email,stage,model,prompt_hash,reserved_tokens,reserved_searches) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                    (job['run_id'],job['email'],STAGE.get(),model,call_hash,allowance,max(0,max_uses)))
        return {'id': cur.fetchone()[0], 'cached': None}


def finish_call(call_id,result,elapsed_ms):
    if call_id is None:
        return
    # Usage may arrive after cancellation. Keep it for reconciliation.
    metadata = {k: result.get(k) for k in ('usage','tool_version','search_count','error','stop_reason')}
    with db() as conn, conn.cursor() as cur:
        cur.execute('UPDATE evi_provider_calls SET result=%s::jsonb,response=%s::jsonb,elapsed_ms=%s WHERE id=%s', (json.dumps(metadata),json.dumps(result),elapsed_ms,call_id))


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
        return False
    stop = threading.Event()
    def renew():
        while not stop.wait(20):
            try:
                if not heartbeat(job):
                    return
            except Exception:
                return  # The database fences later writes after lease expiry.
    thread = threading.Thread(target=renew, daemon=True)
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
        pipeline.run_job(job['run_id'],payload['mode'],payload['query'],**payload['kwargs'])
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


def ledger(run_id,email):
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT state,attempts,cancel_requested,created_at,updated_at,payload FROM evi_jobs WHERE run_id=%s AND email=%s", (run_id,email))
        row = cur.fetchone()
        if not row:
            return None
        job = dict(zip([c[0] for c in cur.description],row))
        cur.execute('SELECT stage,version,elapsed_ms,completed_at FROM evi_stages WHERE run_id=%s ORDER BY completed_at', (run_id,))
        job['stages'] = [dict(zip([c[0] for c in cur.description],r)) for r in cur.fetchall()]
        cur.execute('SELECT stage,model,prompt_hash,reserved_tokens,reserved_searches,result,elapsed_ms,created_at FROM evi_provider_calls WHERE run_id=%s ORDER BY id', (run_id,))
        job['calls'] = [dict(zip([c[0] for c in cur.description],r)) for r in cur.fetchall()]
        job['unknown_provider_outcomes'] = sum(c['result'] is None for c in job['calls'])
        job['calls_without_usage'] = sum(not (c['result'] or {}).get('usage') for c in job['calls'])
        job['runtime_versions'] = job['payload'].get('runtime_versions')
        job['code_version'] = job.pop('payload').get('code_version')
        job['billing_status'] = 'usage reported where available; not reconciled to provider invoice'
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
        return
    if args.once:
        run_once()
        return
    while True:
        try:
            if not run_once():
                time.sleep(2)
        except Exception:
            import logging
            logging.exception('Event worker iteration failed')
            time.sleep(5)


if __name__ == '__main__':
    main()
