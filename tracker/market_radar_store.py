"""Market Radar: the Postgres tables and the reads and writes on them.

Two kinds of data live here, and the split is deliberate:

* PUBLIC facts about companies (mr_entities, mr_snapshots, mr_events,
  mr_industry_sources) are stored once per company and shared by every
  client that tracks it. A competitor's sitemap or a news story is the same
  fact whoever asked, so the tenth client tracking Aspen Dental costs less
  than the first.
* CLIENT data (mr_clients, mr_competitors, mr_runs, mr_client_events) is
  owned by the person who set the client up and is always read with their
  email in the WHERE clause.

Companies are keyed by their web host (see normalize_domain), never by name:
two firms can share a name, and the website is the only reliable tell.

Every function takes an optional `conn`. Given one, it works inside the
caller's transaction and does not commit, which is how the admin schema
check exercises the real tables and then rolls everything back. Without
one, it opens a connection, commits and closes it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from contextlib import contextmanager
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

_TABLES_READY = False

TABLES = ("mr_entities", "mr_clients", "mr_competitors", "mr_runs", "mr_provider_calls",
          "mr_snapshots", "mr_events", "mr_client_events", "mr_industry_sources", "mr_geocodes")

COMPETITOR_KINDS = ("direct", "indirect", "local", "aspirational")
COMPETITOR_STATUSES = ("proposed", "confirmed", "removed")
RUN_MODES = ("baseline", "refresh")
RUN_STATUSES = ("queued", "running", "complete", "failed", "cancelled")
EVENT_STATUSES = ("rumored", "announced", "planned", "opened", "completed", "closed", "unknown")


class StoreUnavailable(RuntimeError):
    """DATABASE_URL is unset or Postgres refused the connection."""


def _connect():
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise StoreUnavailable("DATABASE_URL is not set")
    import psycopg2
    try:
        return psycopg2.connect(url, connect_timeout=8)
    except Exception as e:
        raise StoreUnavailable("Postgres connection failed: %s" % e) from e


@contextmanager
def _tx(conn=None):
    """Yield a cursor. With a caller's connection: no commit, no close. Without:
    a fresh connection, committed on success, rolled back on error."""
    ensure_tables()
    if conn is not None:
        with conn.cursor() as cur:
            yield cur
        return
    own = _connect()
    try:
        with own.cursor() as cur:
            yield cur
        own.commit()
    except Exception:
        own.rollback()
        raise
    finally:
        own.close()


def ensure_tables():
    """Create every mr_ table if missing, on a connection of its own that is
    committed straight away. Never inside a caller's transaction: if that
    caller rolled back, the tables would vanish while this module went on
    believing they existed. Idempotent; an advisory lock stops two workers
    starting together from racing."""
    global _TABLES_READY
    if _TABLES_READY:
        return
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('mr-schema-v1'))")
            for statement in SCHEMA:
                cur.execute(statement)
        conn.commit()
    finally:
        conn.close()
    _TABLES_READY = True


SCHEMA = [
    # One row per company, shared by every client. `domain` is the web host
    # without "www." (normalize_domain).
    """CREATE TABLE IF NOT EXISTS mr_entities (
        id BIGSERIAL PRIMARY KEY,
        domain TEXT NOT NULL UNIQUE,
        name TEXT,
        country TEXT,
        archetype TEXT,
        profile JSONB NOT NULL DEFAULT '{}'::jsonb,
        profile_updated_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    # A company someone is getting reports for, owned by that person.
    """CREATE TABLE IF NOT EXISTS mr_clients (
        id BIGSERIAL PRIMARY KEY,
        owner_email TEXT NOT NULL,
        entity_id BIGINT NOT NULL REFERENCES mr_entities(id),
        radius_km NUMERIC(7,2),
        settings JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (owner_email, entity_id))""",
    # Who competes with a client, how, how sure we are, where we found them,
    # and whether the user confirmed or removed them (edits survive reruns).
    """CREATE TABLE IF NOT EXISTS mr_competitors (
        client_id BIGINT NOT NULL REFERENCES mr_clients(id) ON DELETE CASCADE,
        entity_id BIGINT NOT NULL REFERENCES mr_entities(id),
        kind TEXT NOT NULL CHECK (kind IN ('direct','indirect','local','aspirational')),
        confidence NUMERIC(4,3),
        found_via JSONB NOT NULL DEFAULT '[]'::jsonb,
        status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','confirmed','removed')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (client_id, entity_id))""",
    """CREATE TABLE IF NOT EXISTS mr_runs (
        id BIGSERIAL PRIMARY KEY,
        client_id BIGINT NOT NULL REFERENCES mr_clients(id) ON DELETE CASCADE,
        owner_email TEXT NOT NULL,
        mode TEXT NOT NULL CHECK (mode IN ('baseline','refresh')),
        status TEXT NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued','running','complete','failed','cancelled')),
        stage TEXT,
        cost_cap_usd NUMERIC(10,4) NOT NULL,
        error TEXT,
        summary JSONB,
        coverage JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at TIMESTAMPTZ)""",
    "CREATE INDEX IF NOT EXISTS idx_mr_runs_owner ON mr_runs (owner_email, created_at DESC)",
    # The cost ledger: one row per paid call (see tracker/market_radar_ledger).
    """CREATE TABLE IF NOT EXISTS mr_provider_calls (
        id BIGSERIAL PRIMARY KEY,
        run_id BIGINT NOT NULL REFERENCES mr_runs(id) ON DELETE CASCADE,
        stage TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT,
        batch BOOLEAN NOT NULL DEFAULT FALSE,
        units INTEGER NOT NULL DEFAULT 0,
        reserved_usd NUMERIC(12,6),
        actual_usd NUMERIC(12,6),
        usage JSONB,
        status TEXT NOT NULL DEFAULT 'reserved'
            CHECK (status IN ('reserved','done','failed','unmeasured','abandoned')),
        error TEXT,
        notes JSONB NOT NULL DEFAULT '[]'::jsonb,
        elapsed_ms INTEGER,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        finished_at TIMESTAMPTZ)""",
    "CREATE INDEX IF NOT EXISTS idx_mr_calls_run ON mr_provider_calls (run_id)",
    # What a detector saw on a company, stored once per distinct content.
    # Seeing the same content again only moves last_seen_at, so a weekly
    # check of an unchanged sitemap adds no row.
    """CREATE TABLE IF NOT EXISTS mr_snapshots (
        id BIGSERIAL PRIMARY KEY,
        entity_id BIGINT NOT NULL REFERENCES mr_entities(id),
        detector TEXT NOT NULL,
        content_hash TEXT NOT NULL,
        payload JSONB NOT NULL,
        item_count INTEGER,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        last_seen_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        seen_count INTEGER NOT NULL DEFAULT 1,
        first_run_id BIGINT REFERENCES mr_runs(id) ON DELETE SET NULL,
        UNIQUE (entity_id, detector, content_hash))""",
    "CREATE INDEX IF NOT EXISTS idx_mr_snapshots_latest ON mr_snapshots (entity_id, detector, last_seen_at DESC)",
    # Something that happened to a company (a branch opened, a price rose),
    # merged across every source that reported it.
    """CREATE TABLE IF NOT EXISTS mr_events (
        id BIGSERIAL PRIMARY KEY,
        entity_id BIGINT NOT NULL REFERENCES mr_entities(id),
        dedupe_key TEXT NOT NULL,
        type TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'unknown'
            CHECK (status IN ('rumored','announced','planned','opened','completed','closed','unknown')),
        event_date DATE,
        title TEXT NOT NULL,
        summary TEXT,
        location JSONB,
        sources JSONB NOT NULL DEFAULT '[]'::jsonb,
        evidence_count INTEGER NOT NULL DEFAULT 0,
        first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (entity_id, dedupe_key))""",
    "CREATE INDEX IF NOT EXISTS idx_mr_events_entity ON mr_events (entity_id, first_seen_at DESC)",
    # How an event matters to one client, and what that client said about it.
    """CREATE TABLE IF NOT EXISTS mr_client_events (
        client_id BIGINT NOT NULL REFERENCES mr_clients(id) ON DELETE CASCADE,
        event_id BIGINT NOT NULL REFERENCES mr_events(id) ON DELETE CASCADE,
        score NUMERIC(8,3),
        severity TEXT CHECK (severity IN ('HIGH','MEDIUM','LOW')),
        distance_km NUMERIC(9,2),
        feedback TEXT CHECK (feedback IN ('up','down')),
        feedback_reason TEXT,
        feedback_at TIMESTAMPTZ,
        first_run_id BIGINT REFERENCES mr_runs(id) ON DELETE SET NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (client_id, event_id))""",
    # Feeds that cover an industry in a country, learned once and shared.
    """CREATE TABLE IF NOT EXISTS mr_industry_sources (
        id BIGSERIAL PRIMARY KEY,
        industry_key TEXT NOT NULL,
        country TEXT NOT NULL DEFAULT '',
        feed_url TEXT NOT NULL,
        site_domain TEXT,
        kind TEXT NOT NULL DEFAULT 'rss',
        discovered_from TEXT,
        last_ok_at TIMESTAMPTZ,
        last_error TEXT,
        item_count INTEGER,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (industry_key, country, feed_url))""",
    # Place name to coordinates, cached forever: OpenStreetMap's geocoder
    # asks callers to cache and to stay under one request a second.
    """CREATE TABLE IF NOT EXISTS mr_geocodes (
        query TEXT PRIMARY KEY,
        result JSONB NOT NULL,
        fetched_at TIMESTAMPTZ NOT NULL DEFAULT now())""",
    # Added with the edit page (2026-10-09): why a competitor was chosen and
    # how it was checked, as shown to the user. Empty for older rows.
    "ALTER TABLE mr_competitors ADD COLUMN IF NOT EXISTS details JSONB NOT NULL DEFAULT '{}'::jsonb",
    "CREATE INDEX IF NOT EXISTS idx_mr_clients_owner ON mr_clients (owner_email, updated_at DESC)",
]


# -- companies ---------------------------------------------------------------

def normalize_domain(url_or_host):
    """The key a company is stored under: its lowercase host without "www.",
    from a full URL or a bare host. Raises ValueError when there is no host.

    This is the host, not the registrable domain: "shop.brand.com" and
    "brand.com" stay distinct, because guessing where a domain's public
    suffix ends (brand.co.uk vs co.uk) without the public-suffix list is how
    two unrelated companies end up merged."""
    text = (url_or_host or "").strip()
    if not text:
        raise ValueError("no URL or host given")
    if "://" not in text:
        text = "http://" + text
    host = (urlsplit(text).hostname or "").strip(".").lower()
    if not host or "." not in host:
        raise ValueError("no usable host in %r" % url_or_host)
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError("host %r is not a valid domain name" % host)
    if host.startswith("www."):
        host = host[4:]
    return host


def upsert_entity(domain, *, name=None, country=None, archetype=None, conn=None):
    """Return the id for this company, creating it if new. Non-empty fields
    given here overwrite stored ones; omitted fields are left alone."""
    domain = normalize_domain(domain)
    with _tx(conn) as cur:
        cur.execute("""
            INSERT INTO mr_entities (domain, name, country, archetype) VALUES (%s,%s,%s,%s)
            ON CONFLICT (domain) DO UPDATE SET
                name = COALESCE(EXCLUDED.name, mr_entities.name),
                country = COALESCE(EXCLUDED.country, mr_entities.country),
                archetype = COALESCE(EXCLUDED.archetype, mr_entities.archetype),
                updated_at = now()
            RETURNING id""", (domain, name or None, country or None, archetype or None))
        return cur.fetchone()[0]


def set_profile(entity_id, profile, *, conn=None):
    with _tx(conn) as cur:
        cur.execute("""UPDATE mr_entities SET profile=%s::jsonb, profile_updated_at=now(),
                       updated_at=now() WHERE id=%s""", (json.dumps(profile), entity_id))
        if cur.rowcount != 1:
            raise KeyError("no company with id %s" % entity_id)


def get_entity(entity_id, *, conn=None):
    with _tx(conn) as cur:
        cur.execute("""SELECT id, domain, name, country, archetype, profile, profile_updated_at
                       FROM mr_entities WHERE id=%s""", (entity_id,))
        row = cur.fetchone()
    if not row:
        return None
    keys = ("id", "domain", "name", "country", "archetype", "profile", "profile_updated_at")
    return dict(zip(keys, row))


# -- clients and competitors ---------------------------------------------------

def upsert_client(owner_email, entity_id, *, radius_km=None, conn=None):
    owner_email = (owner_email or "").strip().lower()
    if not owner_email:
        raise ValueError("a client needs an owner email")
    with _tx(conn) as cur:
        cur.execute("""
            INSERT INTO mr_clients (owner_email, entity_id, radius_km) VALUES (%s,%s,%s)
            ON CONFLICT (owner_email, entity_id) DO UPDATE SET
                radius_km = COALESCE(EXCLUDED.radius_km, mr_clients.radius_km), updated_at = now()
            RETURNING id""", (owner_email, entity_id, radius_km))
        return cur.fetchone()[0]


def _owned_client(cur, client_id, owner_email):
    cur.execute("SELECT 1 FROM mr_clients WHERE id=%s AND owner_email=%s",
                (client_id, (owner_email or "").strip().lower()))
    if not cur.fetchone():
        raise PermissionError("client %s is not yours or does not exist" % client_id)


def propose_competitor(client_id, owner_email, entity_id, kind, *, confidence=None,
                       found_via=None, details=None, conn=None):
    """Add or refresh an automatically found competitor. A competitor the
    user already confirmed or removed keeps that decision: a rerun must not
    resurrect a company someone deleted, or demote one they confirmed."""
    if kind not in COMPETITOR_KINDS:
        raise ValueError("unknown competitor kind %r" % kind)
    with _tx(conn) as cur:
        _owned_client(cur, client_id, owner_email)
        cur.execute("""
            INSERT INTO mr_competitors (client_id, entity_id, kind, confidence, found_via, details)
            VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb)
            ON CONFLICT (client_id, entity_id) DO UPDATE SET
                kind = CASE WHEN mr_competitors.status='proposed' THEN EXCLUDED.kind
                            ELSE mr_competitors.kind END,
                confidence = EXCLUDED.confidence,
                found_via = EXCLUDED.found_via,
                details = mr_competitors.details || EXCLUDED.details,
                updated_at = now()""",
                    (client_id, entity_id, kind, confidence, json.dumps(found_via or []),
                     json.dumps(details or {})))


def set_competitor_status(client_id, owner_email, entity_id, status, *, kind=None, conn=None):
    """The user's edit: confirm, remove, or relabel a competitor."""
    if status not in COMPETITOR_STATUSES:
        raise ValueError("unknown competitor status %r" % status)
    if kind is not None and kind not in COMPETITOR_KINDS:
        raise ValueError("unknown competitor kind %r" % kind)
    with _tx(conn) as cur:
        _owned_client(cur, client_id, owner_email)
        cur.execute("""UPDATE mr_competitors SET status=%s, kind=COALESCE(%s, kind), updated_at=now()
                       WHERE client_id=%s AND entity_id=%s""", (status, kind, client_id, entity_id))
        if cur.rowcount != 1:
            raise KeyError("company %s is not on client %s's competitor list" % (entity_id, client_id))


def competitors(client_id, owner_email, *, include_removed=False, conn=None):
    with _tx(conn) as cur:
        _owned_client(cur, client_id, owner_email)
        cur.execute("""
            SELECT c.entity_id, e.domain, e.name, c.kind, c.confidence, c.status, c.found_via,
                   c.details, c.updated_at
            FROM mr_competitors c JOIN mr_entities e ON e.id = c.entity_id
            WHERE c.client_id=%s AND (%s OR c.status <> 'removed')
            ORDER BY c.status='confirmed' DESC, c.confidence DESC NULLS LAST, e.domain""",
                    (client_id, include_removed))
        keys = ("entity_id", "domain", "name", "kind", "confidence", "status", "found_via",
                "details", "updated_at")
        return [dict(zip(keys, r)) for r in cur.fetchall()]


def add_competitor(client_id, owner_email, entity_id, kind, *, conn=None):
    """A competitor the user added by hand: confirmed from the start. If the
    company was already on the list (even removed), it is confirmed and
    relabelled rather than duplicated."""
    if kind not in COMPETITOR_KINDS:
        raise ValueError("unknown competitor kind %r" % kind)
    with _tx(conn) as cur:
        _owned_client(cur, client_id, owner_email)
        cur.execute("""
            INSERT INTO mr_competitors (client_id, entity_id, kind, status, found_via, details)
            VALUES (%s,%s,%s,'confirmed','["added by you"]'::jsonb,'{"added_by_user": true}'::jsonb)
            ON CONFLICT (client_id, entity_id) DO UPDATE SET
                kind = EXCLUDED.kind, status = 'confirmed', updated_at = now()""",
                    (client_id, entity_id, kind))


def retire_suggestions(client_id, owner_email, keep_entity_ids, *, conn=None):
    """Drop the agent's earlier suggestions that its latest full search no
    longer makes. Only rows still 'proposed' go: anything the user confirmed,
    removed or added stays. Returns how many were dropped."""
    with _tx(conn) as cur:
        _owned_client(cur, client_id, owner_email)
        cur.execute("""DELETE FROM mr_competitors
                       WHERE client_id=%s AND status='proposed' AND NOT (entity_id = ANY(%s))""",
                    (client_id, list(keep_entity_ids)))
        return cur.rowcount


def list_clients(owner_email, *, conn=None):
    """The companies this person tracks, newest first, with competitor counts
    and their latest run. Test records (the reserved .example domain, such
    as the Phase 0 Apify check's) are not listed."""
    with _tx(conn) as cur:
        cur.execute("""
            SELECT cl.id, cl.entity_id, e.domain, e.name, e.archetype, e.country, cl.updated_at,
                   COUNT(c.entity_id) FILTER (WHERE c.status='confirmed'),
                   COUNT(c.entity_id) FILTER (WHERE c.status='proposed'),
                   COUNT(c.entity_id) FILTER (WHERE c.status='removed'),
                   (SELECT json_build_object('id', r.id, 'status', r.status, 'stage', r.stage,
                                             'created_at', r.created_at, 'finished_at', r.finished_at)
                      FROM mr_runs r WHERE r.client_id = cl.id ORDER BY r.id DESC LIMIT 1)
            FROM mr_clients cl JOIN mr_entities e ON e.id = cl.entity_id
            LEFT JOIN mr_competitors c ON c.client_id = cl.id
            WHERE cl.owner_email=%s AND e.domain NOT LIKE '%%.example'
            GROUP BY cl.id, e.id
            ORDER BY cl.updated_at DESC""", ((owner_email or "").strip().lower(),))
        keys = ("client_id", "entity_id", "domain", "name", "archetype", "country", "updated_at",
                "confirmed", "proposed", "removed", "last_run")
        return [dict(zip(keys, r)) for r in cur.fetchall()]


def get_client(client_id, owner_email, *, conn=None):
    """One client of this person's, with its company record and settings;
    None when it is not theirs or does not exist."""
    with _tx(conn) as cur:
        cur.execute("""
            SELECT cl.id, cl.entity_id, cl.radius_km, cl.settings, cl.updated_at,
                   e.domain, e.name, e.archetype, e.country, e.profile, e.profile_updated_at
            FROM mr_clients cl JOIN mr_entities e ON e.id = cl.entity_id
            WHERE cl.id=%s AND cl.owner_email=%s""",
                    (client_id, (owner_email or "").strip().lower()))
        row = cur.fetchone()
    if not row:
        return None
    keys = ("client_id", "entity_id", "radius_km", "settings", "updated_at", "domain", "name",
            "archetype", "country", "profile", "profile_updated_at")
    return dict(zip(keys, row))


def update_client_settings(client_id, owner_email, *, settings=None, radius_km=False, conn=None):
    """Replace the client's settings (the caller merges) and/or set its radius
    (None clears it; the default False leaves it alone)."""
    with _tx(conn) as cur:
        _owned_client(cur, client_id, owner_email)
        if settings is not None:
            cur.execute("UPDATE mr_clients SET settings=%s::jsonb, updated_at=now() WHERE id=%s",
                        (json.dumps(settings), client_id))
        if radius_km is not False:
            cur.execute("UPDATE mr_clients SET radius_km=%s, updated_at=now() WHERE id=%s",
                        (radius_km, client_id))


def found_in_runs(client_id, owner_email, *, limit=10, conn=None):
    """Each competitor as the most recent finished run described it, by
    domain: for rows saved before mr_competitors.details existed."""
    with _tx(conn) as cur:
        cur.execute("""SELECT summary FROM mr_runs WHERE client_id=%s AND owner_email=%s
                       AND status='complete' AND summary IS NOT NULL ORDER BY id DESC LIMIT %s""",
                    (client_id, (owner_email or "").strip().lower(), limit))
        rows = cur.fetchall()
    out = {}
    for (summary,) in rows:
        for c in ((summary or {}).get("result") or {}).get("competitors") or []:
            if c.get("domain") and c["domain"] not in out:
                out[c["domain"]] = c
    return out


def latest_run(client_id, owner_email, *, conn=None):
    with _tx(conn) as cur:
        cur.execute("SELECT id FROM mr_runs WHERE client_id=%s AND owner_email=%s "
                    "ORDER BY id DESC LIMIT 1", (client_id, (owner_email or "").strip().lower()))
        row = cur.fetchone()
    return get_run(row[0], owner_email, conn=conn) if row else None


# -- runs ---------------------------------------------------------------------

def create_run(client_id, owner_email, mode, *, cost_cap_usd=None, conn=None):
    from .market_radar_costs import DEFAULT_RUN_CAP_USD
    if mode not in RUN_MODES:
        raise ValueError("unknown run mode %r" % mode)
    cap = cost_cap_usd if cost_cap_usd is not None else os.environ.get(
        "MR_RUN_COST_CAP_USD") or DEFAULT_RUN_CAP_USD
    with _tx(conn) as cur:
        _owned_client(cur, client_id, owner_email)
        cur.execute("""INSERT INTO mr_runs (client_id, owner_email, mode, cost_cap_usd)
                       VALUES (%s,%s,%s,%s) RETURNING id""",
                    (client_id, owner_email.strip().lower(), mode, str(cap)))
        return cur.fetchone()[0]


def update_run(run_id, *, status=None, stage=None, error=None, summary=None, coverage=None,
               conn=None):
    if status is not None and status not in RUN_STATUSES:
        raise ValueError("unknown run status %r" % status)
    finished = status in ("complete", "failed", "cancelled")
    with _tx(conn) as cur:
        cur.execute("""
            UPDATE mr_runs SET
                status = COALESCE(%s, status), stage = COALESCE(%s, stage),
                error = COALESCE(%s, error),
                summary = COALESCE(%s::jsonb, summary), coverage = COALESCE(%s::jsonb, coverage),
                finished_at = CASE WHEN %s THEN now() ELSE finished_at END,
                updated_at = now()
            WHERE id=%s""", (status, stage, error,
                             None if summary is None else json.dumps(summary),
                             None if coverage is None else json.dumps(coverage),
                             finished, run_id))
        if cur.rowcount != 1:
            raise KeyError("no run with id %s" % run_id)


def get_run(run_id, owner_email, *, conn=None):
    with _tx(conn) as cur:
        cur.execute("""SELECT id, client_id, mode, status, stage, cost_cap_usd, error, summary,
                              coverage, created_at, finished_at
                       FROM mr_runs WHERE id=%s AND owner_email=%s""",
                    (run_id, (owner_email or "").strip().lower()))
        row = cur.fetchone()
    if not row:
        return None
    keys = ("id", "client_id", "mode", "status", "stage", "cost_cap_usd", "error", "summary",
            "coverage", "created_at", "finished_at")
    return dict(zip(keys, row))


# -- snapshots -----------------------------------------------------------------

def content_hash(payload):
    """Same content, same hash, whatever the key order."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def save_snapshot(entity_id, detector, payload, *, item_count=None, run_id=None, conn=None):
    """Record what `detector` saw on this company now, and say whether it
    differs from the last thing it saw.

    Returns {"snapshot_id", "first": bool, "changed": bool, "previous": payload or None}.
    `first` is True when this detector has never seen the company before:
    there is nothing to compare, which is not the same as "no change"."""
    digest = content_hash(payload)
    with _tx(conn) as cur:
        # Serialise per company and detector, so two runs saving at once
        # cannot both read the same "latest" and both miss the change.
        cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                    ("mr-snap:%s:%s" % (entity_id, detector),))
        cur.execute("""SELECT content_hash, payload FROM mr_snapshots
                       WHERE entity_id=%s AND detector=%s
                       ORDER BY last_seen_at DESC, id DESC LIMIT 1""", (entity_id, detector))
        latest = cur.fetchone()
        cur.execute("""
            INSERT INTO mr_snapshots (entity_id, detector, content_hash, payload, item_count, first_run_id)
            VALUES (%s,%s,%s,%s::jsonb,%s,%s)
            ON CONFLICT (entity_id, detector, content_hash) DO UPDATE SET
                last_seen_at = clock_timestamp(), seen_count = mr_snapshots.seen_count + 1
            RETURNING id""", (entity_id, detector, digest, json.dumps(payload), item_count, run_id))
        snapshot_id = cur.fetchone()[0]
    if latest is None:
        return {"snapshot_id": snapshot_id, "first": True, "changed": False, "previous": None}
    changed = latest[0] != digest
    return {"snapshot_id": snapshot_id, "first": False, "changed": changed,
            "previous": latest[1] if changed else None}


def latest_snapshot(entity_id, detector, *, conn=None):
    with _tx(conn) as cur:
        cur.execute("""SELECT payload, first_seen_at, last_seen_at, seen_count FROM mr_snapshots
                       WHERE entity_id=%s AND detector=%s
                       ORDER BY last_seen_at DESC, id DESC LIMIT 1""", (entity_id, detector))
        row = cur.fetchone()
    if not row:
        return None
    return dict(zip(("payload", "first_seen_at", "last_seen_at", "seen_count"), row))


# -- events --------------------------------------------------------------------

def record_event(entity_id, dedupe_key, *, type, title, source, status="unknown",
                 event_date=None, summary=None, location=None, conn=None):
    """Add an event, or merge a new source into the event already stored
    under (company, dedupe_key). `source` is a dict with at least "url" and
    "detector". Returns (event_id, created).

    evidence_count is the number of DIFFERENT detectors that reported the
    event: forty sites reposting one press release are one source of
    evidence, while news plus a sitemap change plus a job post are three."""
    if status not in EVENT_STATUSES:
        raise ValueError("unknown event status %r" % status)
    if not isinstance(source, dict) or not source.get("url") or not source.get("detector"):
        raise ValueError("an event source needs a url and a detector")
    with _tx(conn) as cur:
        cur.execute("""
            INSERT INTO mr_events (entity_id, dedupe_key, type, status, event_date, title, summary,
                                   location, sources, evidence_count)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,1)
            ON CONFLICT (entity_id, dedupe_key) DO NOTHING
            RETURNING id""", (entity_id, dedupe_key, type, status, event_date, title, summary,
                              None if location is None else json.dumps(location),
                              json.dumps([source])))
        row = cur.fetchone()
        if row:
            return row[0], True
        cur.execute("""SELECT id, sources, status, event_date FROM mr_events
                       WHERE entity_id=%s AND dedupe_key=%s FOR UPDATE""", (entity_id, dedupe_key))
        event_id, sources, stored_status, stored_date = cur.fetchone()
        if not any(s.get("url") == source["url"] for s in sources):
            sources.append(source)
        detectors = {s.get("detector") for s in sources}
        # A later, more definite status wins over "unknown"; a known status is
        # never overwritten by "unknown".
        new_status = stored_status if status == "unknown" else status
        cur.execute("""UPDATE mr_events SET sources=%s::jsonb, evidence_count=%s, status=%s,
                           event_date=COALESCE(event_date, %s),
                           summary=COALESCE(summary, %s), updated_at=now()
                       WHERE id=%s""", (json.dumps(sources), len(detectors), new_status,
                                        event_date, summary, event_id))
        return event_id, False


def link_client_event(client_id, event_id, *, score=None, severity=None, distance_km=None,
                      run_id=None, conn=None):
    """How an event matters to one client. Re-scoring updates the score but
    never touches the client's own feedback."""
    with _tx(conn) as cur:
        cur.execute("""
            INSERT INTO mr_client_events (client_id, event_id, score, severity, distance_km, first_run_id)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (client_id, event_id) DO UPDATE SET
                score = EXCLUDED.score, severity = EXCLUDED.severity,
                distance_km = EXCLUDED.distance_km, updated_at = now()""",
                    (client_id, event_id, score, severity, distance_km, run_id))


def set_feedback(client_id, owner_email, event_id, feedback, reason=None, *, conn=None):
    if feedback not in ("up", "down", None):
        raise ValueError("feedback must be 'up', 'down' or None")
    with _tx(conn) as cur:
        _owned_client(cur, client_id, owner_email)
        cur.execute("""UPDATE mr_client_events SET feedback=%s, feedback_reason=%s,
                           feedback_at=CASE WHEN %s IS NULL THEN NULL ELSE now() END, updated_at=now()
                       WHERE client_id=%s AND event_id=%s""",
                    (feedback, reason, feedback, client_id, event_id))
        if cur.rowcount != 1:
            raise KeyError("event %s is not on client %s's report" % (event_id, client_id))


# -- industry sources ----------------------------------------------------------

def save_industry_source(industry_key, feed_url, *, country="", site_domain=None, kind="rss",
                         discovered_from=None, ok=None, error=None, item_count=None, conn=None):
    """Remember a feed for an industry, and the outcome of its latest read
    (ok=True / ok=False with error / ok=None when not read yet)."""
    with _tx(conn) as cur:
        cur.execute("""
            INSERT INTO mr_industry_sources (industry_key, country, feed_url, site_domain, kind,
                                             discovered_from, last_ok_at, last_error, item_count)
            VALUES (%s,%s,%s,%s,%s,%s, CASE WHEN %s THEN now() END, %s, %s)
            ON CONFLICT (industry_key, country, feed_url) DO UPDATE SET
                last_ok_at = CASE WHEN %s THEN now() ELSE mr_industry_sources.last_ok_at END,
                last_error = CASE WHEN %s IS NULL THEN mr_industry_sources.last_error ELSE %s END,
                item_count = COALESCE(EXCLUDED.item_count, mr_industry_sources.item_count)
            RETURNING id""",
                    (industry_key, country or "", feed_url, site_domain, kind, discovered_from,
                     ok is True, None if ok else error, item_count,
                     ok is True, ok, None if ok else error))
        return cur.fetchone()[0]


def cached_geocode(query, *, conn=None):
    with _tx(conn) as cur:
        cur.execute("SELECT result FROM mr_geocodes WHERE query=%s", (query,))
        row = cur.fetchone()
    return row[0] if row else None


def save_geocode(query, result, *, conn=None):
    with _tx(conn) as cur:
        cur.execute("""INSERT INTO mr_geocodes (query, result) VALUES (%s,%s::jsonb)
                       ON CONFLICT (query) DO UPDATE SET result=EXCLUDED.result, fetched_at=now()""",
                    (query, json.dumps(result)))


def table_counts(*, conn=None):
    """Row count per mr_ table, for the admin check."""
    out = {}
    with _tx(conn) as cur:
        for table in TABLES:
            cur.execute("SELECT count(*) FROM " + table)   # names are constants above
            out[table] = cur.fetchone()[0]
    return out
