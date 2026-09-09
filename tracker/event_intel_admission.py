"""Bounded source checks before recommendation scoring.

Literal support is a necessary admission condition, not semantic verification.
Dynamic pages and restricted access require review rather than optimistic dates.
"""
import calendar
import re
from datetime import date
from urllib.parse import urlsplit

from .event_intel_access import organizer_url
from .event_intel_evidence import source_snapshot
from .event_intel_jobs import ContextExecutor

MAX_PAGES = 2
_RESTRICTED = re.compile(r'\b(?:invite[- ]only|invitation[- ]only|members[- ]only|'
                         r'by invitation|application[- ]only|sold out|waitlist|'
                         r'registration (?:is )?closed|cancelled|canceled)\b', re.I)


def _restriction(text):
    for match in _RESTRICTED.finditer(text):
        prefix = text[max(0,match.start()-45):match.start()]
        # Conditional sales copy is not an announcement of closure.
        if re.search(r'\b(?:until|if|unless|not|never|no longer)\s*$',prefix,re.I):
            continue
        return match
    return None


def _date_patterns(start, end):
    """Explicit ISO or English dates/ranges; unsupported formats stay unknown."""
    def single(d):
        month = '(?:' + calendar.month_name[d.month] + '|' + calendar.month_abbr[d.month] + r'\.?)'
        day = str(d.day) + r'(?:st|nd|rd|th)?'
        return [re.escape(d.isoformat()), month + r'\s+' + day + r',?\s+' + str(d.year),
                day + r'\s+' + month + r',?\s+' + str(d.year)]
    patterns = []
    for a in single(start):
        for b in single(end):
            patterns.append(a if start == end else a + r'.{0,35}?' + b)
    if start.year == end.year and start.month == end.month and start != end:
        month = '(?:' + calendar.month_name[start.month] + '|' + calendar.month_abbr[start.month] + r'\.?)'
        days = str(start.day) + r'(?:st|nd|rd|th)?\s*(?:-|–|—|to|through)\s*' + str(end.day) + r'(?:st|nd|rd|th)?'
        patterns += [month + r'\s+' + days + r',?\s+' + str(start.year),
                     days + r'\s+' + month + r',?\s+' + str(start.year)]
    return [re.compile(r'(?<!\w)' + p + r'(?!\w)', re.I) for p in patterns]


def inspect(event, fetcher=None):
    from .event_intel_harvest import fetch_page
    fetcher = fetcher or fetch_page
    result = {'name':event.get('name'), 'support':'unverified', 'checks':[], 'reasons':[]}
    try:
        start = date.fromisoformat(str(event.get('starts_on') or ''))
        end = date.fromisoformat(str(event.get('ends_on') or event.get('starts_on') or ''))
        host = urlsplit(event.get('website') or '').hostname
        patterns = _date_patterns(start,end)
    except (ValueError, TypeError):
        result['reasons'] = ['The named edition has no usable date range.']
        return result
    urls = []
    for url in [event.get('website')] + list(event.get('sources') or []):
        if organizer_url(url,host) and url not in urls:
            urls.append(url)
    # Keep the actual event name, not just a parent brand shared by summits.
    name = re.sub(r'\s+', ' ', str(event.get('name') or '')).strip().casefold()
    supported = False
    restrictions = []
    for url in urls[:MAX_PAGES]:
        try:
            fetched = fetcher(url)
        except Exception:
            fetched = {'status':'error'}
        final = fetched.get('final_url') or url
        check = {'url':url, 'final_url':final, 'status':fetched.get('status','error')}
        result['checks'].append(check)
        if not organizer_url(final,host):
            check['reason'] = 'The page redirected outside the event host.'
            continue
        raw_text = fetched.get('text') or ''
        fallback = fetched.get('spa') and re.search(
            r'javascript is disabled|please enable javascript|enable javascript to', raw_text, re.I)
        # WordPress/React markers alone also occur on fully readable pages.
        if fetched.get('status') != 'ok' or fallback or fetched.get('truncated'):
            check['reason'] = 'The complete rendered page could not be read reliably.'
            continue
        text = re.sub(r'\s+', ' ', raw_text).strip()
        check['snapshot'] = source_snapshot(final,text)
        restricted = _restriction(text)
        if restricted:
            restrictions.append('The organizer page describes restricted or unavailable access; verify this client’s access before recommending attendance.')
            check['access_excerpt'] = text[max(0,restricted.start()-80):restricted.end()+120]
        for pattern in patterns:
            for match in pattern.finditer(text):
                context = text[max(0,match.start()-180):match.end()+180]
                if name and name in context.casefold():
                    supported = True
                    check['date_excerpt'] = context
                    check['support'] = 'literal_name_and_dates_only'
                    break
            if check.get('date_excerpt'):
                break
    if not supported:
        result['reasons'].append('The named event and its date range could not be found together in readable organizer text; parent-event dates or model claims alone are insufficient.')
    result['reasons'].extend(sorted(set(restrictions)))
    if supported and not result['reasons']:
        result['support'] = 'literal_name_and_dates_only'
    result['not_read'] = max(0,len(urls)-MAX_PAGES)
    return result


def inspect_all(events):
    with ContextExecutor(max_workers=4) as pool:
        return list(pool.map(inspect,events))
