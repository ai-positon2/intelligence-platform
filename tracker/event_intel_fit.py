"""Literal topic and person evidence; no inferred attendance or session roles."""
import re
from datetime import date
from .event_intel_access import organizer_url
from .event_intel_identity import strict_name


def topics(value):
    if not isinstance(value, str):
        raise ValueError('Enter topic phrases, one per line.')
    result = []
    for line in value.splitlines():
        line = line.strip()
        if not line:
            continue
        if not 3 <= len(line) <= 100 or not strict_name(line):
            raise ValueError('Each topic must contain 3 to 100 characters.')
        if strict_name(line) not in {strict_name(item) for item in result}:
            result.append(line)
    if len(result) > 20:
        raise ValueError('Use at most 20 topic phrases.')
    return result


def agenda_evidence(text, url, host, kind):
    # A page classifier is insufficient proof of a session, so retain excerpts,
    # not fabricated session objects, speaker assignments or start times.
    if kind != 'agenda' or not organizer_url(url, host):
        return []
    years = set()
    for line in text.splitlines():
        if 'copyright' in line.lower() or '©' in line:
            continue
        for pattern in (r'\b(20\d{2})\b.{0,40}\b(?:agenda|programme|program)\b',
                        r'\b(?:agenda|programme|program)\b.{0,40}\b(20\d{2})\b'):
            years.update(re.findall(pattern, line, re.I))
    excerpts, seen = [], set()
    for line in text.splitlines():
        # Preserve a bounded line verbatim; do not join nearby names/topics.
        line = line.strip()
        if 12 <= len(line) <= 500 and line not in seen:
            seen.add(line)
            excerpts.append({'text':line, 'source_url':url, 'observed_years':sorted(years),
                             'support':'agenda_page_excerpt'})
        if len(excerpts) == 120:
            break
    return excerpts


def timing(starts_on, years):
    try:
        starts = date.fromisoformat(str(starts_on or '')[:10])
    except ValueError:
        return 'edition_not_established'
    if [str(year) for year in years] != [str(starts.year)]:
        return 'edition_not_established'
    return 'historical' if starts < date.today() else 'announced'


def compare(event, plan, participants, excerpts):
    wanted = plan.get('topic_interests', [])
    matches, seen = [], set()
    for excerpt in excerpts:
        normalized = strict_name(excerpt['text'])
        matched = [topic for topic in wanted if re.search(r'(?<!\w)'+re.escape(strict_name(topic))+r'(?!\w)', normalized)]
        key = (excerpt['source_url'], excerpt['text'])
        if matched and key not in seen:
            seen.add(key)
            matches.append(dict(excerpt, topics=matched,
                                timing=timing(event.get('starts_on'),excerpt.get('observed_years', []))))
    people, seen_people = [], set()
    from .event_intel_planning import domains
    for row in participants:
        evidence = row.get('evidence') or {}
        try:
            company = domains(row.get('org_domain'))
        except ValueError:
            continue
        name = row.get('person_name')
        if (not company or company[0] not in plan.get('target_domains', []) or not name
                or row.get('role') not in ('speaker', 'exhibitor', 'sponsor', 'partner', 'media')
                or evidence.get('status') != 'literal_support_only'
                or evidence.get('person_name') != strict_name(name)
                or strict_name(evidence.get('org_domain')) != strict_name(row.get('org_domain'))
                or not row.get('source_url')):
            continue
        key = (company[0], strict_name(name), row['source_url'])
        if key in seen_people:
            continue
        seen_people.add(key)
        title = row.get('person_title') if evidence.get('person_title') == strict_name(row.get('person_title')) else None
        people.append(dict(name=name,title=title,company=row.get('org_name'),domain=company[0],
                           source_url=row['source_url'],support='literal_support_only',
                           timing=timing(event.get('starts_on'),evidence.get('observed_roster_years') or [])))
    return {'topic_matches':matches[:100], 'people':people[:100],
            'unmatched_topics':[topic for topic in wanted if not any(topic in match['topics'] for match in matches)],
            'limitation':'Topic phrases match source text only, not verified sessions or buyer intent. Published names do not establish personal attendance, a session assignment, contact permission or meeting access.'}
