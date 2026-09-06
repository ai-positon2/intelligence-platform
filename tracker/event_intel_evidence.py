"""Run-scoped observations; public catalog identity is never client scoring."""
import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .event_intel_identity import event_key, strict_name


def text_hash(text):
    return hashlib.sha256(str(text).encode()).hexdigest()


def source_snapshot(url, text, **metadata):
    return dict(metadata, url=url, retrieved_at=datetime.now(timezone.utc).isoformat(),
                text_sha256=text_hash(text), characters=len(text))


def chunks(text, size=12000, overlap=600):
    """Bound input without dropping a tail or splitting every directory row."""
    if size <= overlap or overlap < 0:
        raise ValueError('Invalid chunk overlap')
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind('\n', start + size // 2, end)
            if boundary > start:
                end = boundary
        yield text[start:end]
        if end == len(text):
            break
        start = end - overlap


def supported_rows(rows, text, page_kind):
    """Require literal local support; never infer personal attendance.

    Literal occurrence is a necessary gate, not semantic proof. Store that
    distinction rather than calling the result independently verified.
    """
    folded = strict_name(text)
    role_words = {'exhibitor': ('exhibitor', 'exhibitors'),
                  'sponsor': ('sponsor', 'sponsors'),
                  'speaker': ('speaker', 'speakers'),
                  'partner': ('partner', 'partners'),
                  'media': ('media',)}
    kept, rejected = [], []
    for source_row in rows:
        row = dict(source_row)
        name = strict_name(row.get('org_name'))
        match = re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', folded) if name else None
        pos = match.start() if match else -1
        role = row.get('role')
        # The URL classifier is not evidence of what the page says.
        tokens = role_words.get(role, ())
        if pos < 0 or not any(re.search(r'\b' + word + r'\b', folded) for word in tokens):
            rejected.append({'org_name': row.get('org_name'), 'reason': 'Organization or published role lacks literal page support.'})
            continue
        evidence = {'org_name': name, 'role': role, 'status': 'literal_support_only'}
        for field in ('person_name', 'person_title', 'tier', 'booth'):
            value = strict_name(row.get(field))
            if value and not re.search(r'(?<!\w)' + re.escape(value) + r'(?!\w)', folded):
                row[field] = None
            elif value:
                evidence[field] = value
        domain = row.get('org_domain')
        if domain and not re.search(r'(?<![\w.-])' + re.escape(domain) + r'(?![\w.-])', text, re.I):
            row['org_domain'] = None
        elif domain:
            evidence['org_domain'] = domain
        row['evidence'] = evidence
        row['source_text_sha256'] = text_hash(text)
        kept.append(row)
    return kept, rejected


def catalog_series_identity(event):
    """Only source-reviewed, host-restricted aliases can join series names."""
    from pathlib import Path
    name = re.sub(r'\b20\d{2}\b', '', strict_name(event.get('name'))).strip()
    host = (urlsplit(event.get('website') or '').hostname or '').removeprefix('www.')
    aliases = json.loads(Path(__file__).with_name('event_intel_aliases.json').read_text())
    for reviewed in aliases:
        if name in {strict_name(n) for n in reviewed['aliases']} and host in reviewed['allowed_hosts']:
            return strict_name(reviewed['canonical_name']), reviewed['canonical_host'], reviewed
    return name, host, None


def schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS evi_catalog_series (
        series_key TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT,
        aliases JSONB NOT NULL DEFAULT '[]', created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
    cur.execute('''CREATE TABLE IF NOT EXISTS evi_catalog_editions (
        edition_key TEXT PRIMARY KEY, series_key TEXT NOT NULL REFERENCES evi_catalog_series(series_key),
        identity JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now())''')
    cur.execute('''CREATE TABLE IF NOT EXISTS evi_observations (
        id BIGSERIAL PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES evi_runs(id),
        edition_key TEXT REFERENCES evi_catalog_editions(edition_key),
        observation_key TEXT NOT NULL, field TEXT NOT NULL, value JSONB,
        source_url TEXT, text_sha256 TEXT, support TEXT NOT NULL,
        metadata JSONB NOT NULL DEFAULT '{}', observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(run_id, observation_key))''')
    cur.execute("ALTER TABLE evi_sources ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'")
    cur.execute("ALTER TABLE evi_participants ADD COLUMN IF NOT EXISTS evidence JSONB NOT NULL DEFAULT '{}'")
    for field in ('availability', 'availability_source'):
        cur.execute('ALTER TABLE evi_candidates ADD COLUMN IF NOT EXISTS ' + field + ' TEXT')
    for field in ('what_they_sell', 'selected_product', 'firmographics'):
        cur.execute('ALTER TABLE evi_profiles ADD COLUMN IF NOT EXISTS ' + field + ' TEXT')


def record_event(run_id, event):
    """Record model-reported facts without promoting citations to proof."""
    from . import event_intel_store as store
    conn = store._pg_conn()
    if conn is None:
        raise RuntimeError('Evidence storage unavailable')
    try:
        store._ensure_tables(conn)
        name, host, alias = catalog_series_identity(event)
        series = text_hash(json.dumps([name, host, strict_name(event.get('country'))]))
        edition = event_key(dict(event, run_id=run_id))
        identity = {k: event.get(k) for k in ('name','website','starts_on','ends_on','country','city','location','edition','availability','availability_source')}
        with conn.cursor() as cur:
            cur.execute('INSERT INTO evi_catalog_series(series_key,name,host,aliases) VALUES (%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING',
                        (series, name, host, json.dumps(alias['aliases'] if alias else [event.get('name')])))
            cur.execute('INSERT INTO evi_catalog_editions(edition_key,series_key,identity) VALUES (%s,%s,%s::jsonb) ON CONFLICT DO NOTHING',
                        (edition, series, json.dumps(identity, default=str)))
            for field, value in identity.items():
                if value is not None:
                    key = text_hash(json.dumps([edition, field, value], default=str))
                    cur.execute('''INSERT INTO evi_observations(run_id,edition_key,observation_key,field,value,source_url,support,metadata)
                        VALUES (%s,%s,%s,%s,%s::jsonb,%s,'model_reported',%s::jsonb) ON CONFLICT DO NOTHING''',
                        (run_id, edition, key, field, json.dumps(value, default=str), event.get('website'),
                         json.dumps({'cited_urls': event.get('sources') or [], 'requires_verification': True, 'series_alias_review': alias})))
        conn.commit()
        return edition
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_observations(run_id, email):
    from . import event_intel_store as store
    conn = store._pg_conn()
    if conn is None:
        return []
    try:
        store._ensure_tables(conn)
        with conn.cursor() as cur:
            cur.execute('''SELECT o.edition_key,o.field,o.value,o.source_url,o.text_sha256,o.support,o.metadata,o.observed_at
                FROM evi_observations o JOIN evi_runs r ON r.id=o.run_id
                WHERE o.run_id=%s AND r.email=%s ORDER BY o.id''', (run_id,email))
            names = [c[0] for c in cur.description]
            return [dict(zip(names,row)) for row in cur.fetchall()]
    finally:
        conn.close()


def roster_years(text):
    """Years explicitly attached to roster headings, not footer copyright."""
    roles = r'(?:exhibitors?|sponsors?|speakers?|partners?)'
    years = set()
    for line in text.splitlines():
        if 'copyright' in line.lower() or '©' in line:
            continue
        for pattern in (r'\b(20\d{2})\b.{0,60}\b'+roles+r'\b',
                        r'\b'+roles+r'\b.{0,30}\b(20\d{2})\b'):
            years.update(re.findall(pattern,line,re.I))
    return sorted(years)
