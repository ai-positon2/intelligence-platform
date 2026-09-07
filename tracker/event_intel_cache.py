"""Owner-scoped extraction reuse after a fresh, identical source read.

This is not a verified event catalog. Model-reported dates and client scoring
are never cached here. Only completed originating runs can supply a hit.
"""
import copy
import json
import os
from . import event_intel_jobs as J
from .event_intel_evidence import text_hash, source_snapshot

VERSION = 1
TTL_DAYS = 7


def schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS evi_extraction_cache (
        email TEXT NOT NULL, cache_key TEXT NOT NULL,
        run_id INTEGER NOT NULL REFERENCES evi_runs(id),
        result JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(email,cache_key))''')
    cur.execute('DROP TRIGGER IF EXISTS evi_worker_guard ON evi_extraction_cache')
    cur.execute('''CREATE TRIGGER evi_worker_guard BEFORE INSERT OR UPDATE OR DELETE
        ON evi_extraction_cache FOR EACH ROW EXECUTE FUNCTION evi_fence_worker()''')


def extract(text, url, kind, name, host, identity, prompt, extractor):
    job = J.CURRENT.get()
    if not job or not identity:
        return extractor(text, url, kind, name, host)
    key = text_hash(json.dumps([VERSION, identity, url, kind, name, host,
        text_hash(text), text_hash(prompt), os.getenv('ANTHROPIC_MODEL', 'claude-sonnet-5')]))
    with J.db() as conn, conn.cursor() as cur:
        cur.execute('''SELECT c.result,c.run_id,c.created_at FROM evi_extraction_cache c
            JOIN evi_runs r ON r.id=c.run_id JOIN evi_jobs j ON j.run_id=c.run_id
            WHERE c.email=%s AND c.cache_key=%s AND r.email=c.email
            AND r.status='complete' AND j.state='complete' AND NOT j.cancel_requested
            AND c.created_at >= now() - (%s * interval '1 day')''',
            (job['email'], key, TTL_DAYS))
        row = cur.fetchone()
    if row:
        result = copy.deepcopy(row[0])
        result['snapshot'] = source_snapshot(url, text)
        result['spend'] = None  # No new provider call; never copy original spend.
        result['coverage']['reuse'] = {'status': 'identical_source_reused',
            'origin_run_id': row[1], 'extracted_at': str(row[2]), 'version': VERSION,
            'freshness': 'source fetched again; text hash unchanged'}
        result['note'] = (result.get('note', '') + ' Reused extraction after fetching identical source text; literal support is not independent verification.').strip()
        return result
    result = extractor(text, url, kind, name, host)
    coverage = result.get('coverage') or {}
    # Empty, partial, failed, or legacy extraction must get another attempt.
    if (result.get('rows') and not result.get('error') and coverage.get('chunks_total', 0) > 0
            and coverage.get('chunks_read') == coverage.get('chunks_total')
            and not coverage.get('errors')):
        stored = {k: result.get(k) for k in ('rows', 'note', 'error', 'coverage')}
        with J.db() as conn, conn.cursor() as cur:
            cur.execute('''INSERT INTO evi_extraction_cache(email,cache_key,run_id,result)
                VALUES (%s,%s,%s,%s::jsonb) ON CONFLICT(email,cache_key) DO UPDATE
                SET run_id=EXCLUDED.run_id,result=EXCLUDED.result,created_at=now()''',
                (job['email'], key, job['run_id'], json.dumps(stored)))
    return result
