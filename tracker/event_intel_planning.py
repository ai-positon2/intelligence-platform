"""Client/edition planning and self-reported results, separate from preferences.

No CRM write, outreach delivery, monitoring or model call occurs here. Exact
domain matches are roster evidence, never evidence of a person's attendance.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import re

from . import event_intel_store as S
from .event_intel_identity import event_key
from .event_intel_jobs import db

ACTIONS = {
    'attend': 'Attend', 'exhibit': 'Exhibit', 'sponsor': 'Sponsor',
    'side_event': 'Host a side event', 'meetings': 'Request meetings',
    'monitor': 'Consider a future edition',
}
COUNTS = ('conversations', 'meetings', 'qualified_opportunities')
AMOUNTS = ('planned_budget', 'estimated_total_cost', 'actual_spend', 'effort_hours', 'reported_pipeline')
CURRENCIES = ('USD', 'INR', 'EUR', 'GBP', 'CAD', 'AUD')


class Conflict(ValueError):
    pass


def schema(cur):
    cur.execute('''CREATE TABLE IF NOT EXISTS evi_execution_plans (
        profile_id INTEGER NOT NULL REFERENCES evi_profiles(id),
        event_identity TEXT NOT NULL, email TEXT NOT NULL,
        run_id INTEGER NOT NULL REFERENCES evi_runs(id),
        body JSONB NOT NULL, version INTEGER NOT NULL DEFAULT 1,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY(profile_id,event_identity))''')


def domains(value):
    result = []
    for item in re.split(r'[\s,;]+', str(value or '').strip()):
        if not item:
            continue
        item = item.lower().removeprefix('https://').removeprefix('http://').rstrip('/')
        item = item.removeprefix('www.')
        if len(item) > 253 or not re.fullmatch(r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', item):
            raise ValueError('Enter company domains only, such as example.com; omit paths and email addresses.')
        if item not in result:
            result.append(item)
    if len(result) > 200:
        raise ValueError('Use at most 200 target domains per event plan.')
    return result


def validate(payload):
    if not isinstance(payload.get('action'), str) or payload['action'] not in ACTIONS:
        raise ValueError('Choose an event action.')
    if not isinstance(payload.get('currency'), str) or payload['currency'] not in CURRENCIES:
        raise ValueError('Choose a supported currency.')
    body = {key: payload[key] for key in ('action', 'currency')}
    for key in ('constraints', 'notes'):
        value = str(payload.get(key) or '').strip()
        if len(value) > 4000:
            raise ValueError(key+' must be at most 4,000 characters.')
        body[key] = value
    body['target_domains'] = domains(payload.get('target_domains'))
    for key in COUNTS + AMOUNTS:
        value = payload.get(key)
        if value is None or value == '':
            body[key] = None
            continue
        try:
            if isinstance(value, bool):
                raise InvalidOperation
            number = Decimal(str(value))
            if not number.is_finite() or number < 0 or number > 10**12:
                raise InvalidOperation
            if key in COUNTS and number != number.to_integral_value():
                raise InvalidOperation
            if key in AMOUNTS and number != number.quantize(Decimal('0.01')):
                raise InvalidOperation
            body[key] = int(number) if key in COUNTS else str(number.quantize(Decimal('0.01')))
        except (InvalidOperation, ValueError):
            raise ValueError(key+' must be a nonnegative '+('whole number.' if key in COUNTS else 'number with at most two decimal places.'))
    has_results = any(body[k] is not None for k in COUNTS + ('actual_spend', 'effort_hours', 'reported_pipeline'))
    body['results_as_of'] = None
    if has_results:
        try:
            observed = date.fromisoformat(str(payload.get('results_as_of') or ''))
            if observed > date.today():
                raise ValueError
            body['results_as_of'] = observed.isoformat()
        except ValueError:
            raise ValueError('Results need an as-of date no later than today.')
    body['access_status'] = payload.get('access_status') or 'unknown'
    if body['access_status'] not in ('unknown', 'confirmed', 'unavailable'):
        raise ValueError('Choose a valid access status.')
    body['access_checked_on'] = None
    if body['access_status'] != 'unknown':
        try:
            checked = date.fromisoformat(str(payload.get('access_checked_on') or ''))
            if checked > date.today():
                raise ValueError
            body['access_checked_on'] = checked.isoformat()
        except ValueError:
            raise ValueError('Reported access needs a check date no later than today.')
    body['support'] = 'user_reported'
    return body


def context(run_id, email, profile_id=None, identity=None):
    run = S.get_run(run_id, email)
    if not run:
        raise LookupError('Run not found.')
    if run.get('mode') not in ('lookup', 'recommend'):
        raise ValueError('Plans require an event research or recommendation report.')
    if run.get('status') != 'complete':
        raise ValueError('Complete the research run before creating a plan.')
    profiles = S.list_profiles(email)
    fixed_profile = run.get('profile_id')
    if fixed_profile and profile_id and fixed_profile != profile_id:
        raise ValueError('This run belongs to a different client profile.')
    profile_id = fixed_profile or profile_id
    profile = S.get_profile(profile_id, email) if profile_id else None
    if profile_id and not profile:
        raise LookupError('Client profile not found.')
    if fixed_profile:
        profiles = [profile]
    events = S.get_candidates(run_id) if run.get('mode') == 'recommend' else S.get_events(run_id)
    events = [dict(event, event_identity=event_key(event)) for event in events]
    selected = next((event for event in events if event['event_identity'] == identity), None) if identity else (events[0] if events else None)
    if not selected:
        raise ValueError('Choose an event from this report.')
    plan = None
    if profile:
        with db() as conn, conn.cursor() as cur:
            cur.execute('SELECT body,version,updated_at FROM evi_execution_plans WHERE profile_id=%s AND event_identity=%s AND email=%s',
                        (profile_id, selected['event_identity'], email))
            row = cur.fetchone()
            if row:
                plan = dict(row[0], version=row[1], updated_at=str(row[2]))
    from .event_intel_access import assess, organizer_url
    from urllib.parse import urlsplit
    participants = [row for row in S.get_participants(run_id) if row.get('event_id') == selected.get('id')] if run.get('mode') == 'lookup' else []
    try:
        host = urlsplit(selected.get('website') or '').hostname or ''
    except ValueError:
        host = ''
    links, seen = [], set()
    for source in S.get_sources(run_id) if run.get('mode') == 'lookup' else []:
        if source.get('event_id') != selected['id'] or source.get('status') != 'ok':
            continue
        for link in (source.get('metadata') or {}).get('access_links', []):
            key = (link.get('kind'), link.get('url'))
            if key not in seen and organizer_url(link.get('url'), host) and organizer_url(link.get('source_url'), host):
                seen.add(key)
                links.append(dict(link, observed_at=source.get('fetched_at')))
    comparison = overlay(selected, participants, (plan or {}).get('target_domains', []))
    return dict(run_id=run_id, profile=profile, profiles=profiles, events=events,
                event=selected, plan=plan, participants=participants,
                overlay=comparison, access_links=links, assessment=assess(selected, plan or {}, comparison['matches'], links))


def overlay(event, participants, targets):
    matches = []
    try:
        starts = date.fromisoformat(str(event.get('starts_on') or ''))
    except ValueError:
        starts = None
    for row in participants:
        try:
            org_domain = domains(row.get('org_domain'))
        except ValueError:
            continue
        if not org_domain or org_domain[0] not in targets or not row.get('source_url'):
            continue
        evidence = row.get('evidence') or {}
        years = [str(year) for year in (evidence.get('observed_roster_years') or [])]
        same_edition = bool(row.get('role') in ('exhibitor', 'sponsor', 'speaker', 'partner', 'media') and starts and years == [str(starts.year)] and evidence.get('status') == 'literal_support_only')
        timing = ('historical' if starts < date.today() else 'announced') if same_edition else 'edition_not_established'
        matches.append(dict(company=row.get('org_name'), domain=org_domain[0], role=row.get('role'),
                            source_url=row['source_url'], timing=timing, support=evidence.get('status') or 'unverified'))
    unavailable = event.get('availability') in ('sold_out', 'cancelled') or not starts or starts < date.today()
    suggestion = 'monitor' if unavailable else ('meetings' if any(m['timing'] == 'announced' for m in matches) else None)
    return dict(matches=matches, suggested_action=suggestion,
                matched_domains=sorted({m['domain'] for m in matches}),
                not_observed=sorted(set(targets)-{m['domain'] for m in matches}),
                limitation='A roster match is not proof of personal attendance or meeting access. Missing matches do not prove absence. Historical or undated evidence cannot establish future participation.',
                next_check='Verify organizer access, contact details and budget before committing.' if suggestion == 'meetings' else 'Confirm the edition, availability and access options before choosing an action.')


def save(run_id, email, payload):
    try:
        for key in ('profile_id', 'version'):
            value = payload.get(key, 0)
            if isinstance(value, bool) or not re.fullmatch(r'[0-9]+', str(value)):
                raise ValueError
        profile_id = int(payload.get('profile_id') or 0)
        expected = int(payload.get('version', 0))
    except (ValueError, TypeError):
        raise ValueError('A valid client profile and plan version are required.')
    if not profile_id or expected < 0 or not isinstance(payload.get('event_identity'), str) or not payload['event_identity']:
        raise ValueError('Choose a client and event from this report.')
    view = context(run_id, email, profile_id, payload['event_identity'])
    body = validate(payload)
    if view.get('plan') and view['plan'].get('action') != body['action']:
        body['access_status'] = 'unknown'
        body['access_checked_on'] = None
    identity = view['event']['event_identity']
    with db() as conn, conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', ('evi-plan:'+str(profile_id)+':'+identity,))
        cur.execute('SELECT version FROM evi_execution_plans WHERE profile_id=%s AND event_identity=%s', (profile_id, identity))
        row = cur.fetchone()
        if (row[0] if row else 0) != expected:
            raise Conflict('This plan changed in another tab. Reload it before saving.')
        cur.execute('''INSERT INTO evi_execution_plans(profile_id,event_identity,email,run_id,body,version)
            VALUES (%s,%s,%s,%s,%s::jsonb,1) ON CONFLICT(profile_id,event_identity)
            DO UPDATE SET body=EXCLUDED.body,run_id=EXCLUDED.run_id,version=evi_execution_plans.version+1,updated_at=now()''',
            (profile_id, identity, email, run_id, json.dumps(body)))
    return context(run_id, email, profile_id, identity)
