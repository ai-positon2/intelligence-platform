"""Bounded source checks before recommendation scoring.

Literal support is a necessary admission condition, not semantic verification.
Dynamic pages and restricted access require review rather than optimistic dates.
"""
import calendar
import re
import unicodedata
from datetime import date
from urllib.parse import urlsplit, urldefrag

from .event_intel_access import organizer_url
from .event_intel_evidence import source_snapshot
from .event_intel_jobs import ContextExecutor

MAX_PAGES = 2
_RESTRICTED = re.compile(r'\b(?:invite[- ]only|invitation[- ]only|members[- ]only|'
                         r'by invitation|application[- ]only|application required|subject to approval|sold out|waitlist|'
                         r'registration (?:is )?closed|cancelled|canceled)\b', re.I)


def _fold(text):
    text = unicodedata.normalize('NFKC',text).casefold().replace('&',' and ')
    return ' '.join(re.findall(r'\w+',text))


def _names(event, year):
    name = str(event.get('name') or '').strip()
    if any(y != str(year) for y in re.findall(r'\b20\d{2}\b',name)):
        return []
    # Strip only a matching year and an explicit acronym, never regional names.
    name = re.sub(r'\s*\([A-Z0-9]{2,12}\)\s*$', '', name)
    name = re.sub(r'\s+'+str(year)+r'$', '', name)
    return [_fold(name)] if _fold(name) else []


def _owns_date(prefix, names, year):
    last_clause = re.split(r'[.!?]\s+',prefix)[-1]
    opening = _fold(last_clause).split()[:1]
    if last_clause.strip() and opening not in (['get'],['choose'],['join'],['register'],['book']):
        prefix = last_clause
    folded = _fold(prefix)
    for name in names:
        matches = list(re.finditer(r'(?<!\w)'+re.escape(name)+r'(?!\w)',folded))
        if not matches:
            continue
        tail = folded[matches[-1].end():].strip()
        if any(y != str(year) for y in re.findall(r'\b20\d{2}\b',tail)):
            continue
        tail = re.sub(r'^'+str(year)+r'\b', '', tail).strip()
        # A bare regional suffix or another event name is a different identity.
        first = tail.split()[0] if tail else ''
        allowed = {'on','runs','returns','takes','starts','is','join','at','from','get','choose','register','book'}
        allowed.update(m.casefold() for m in calendar.month_name if m)
        allowed.update(m.casefold() for m in calendar.month_abbr if m)
        if not first or first in allowed or first.isdigit():
            return True
    return False


def _access(text, event_name=''):
    observations, blocking = [], []
    general_open = bool(re.search(r'\b(?:general admission|event registration) (?:is |remains )?open\b',text,re.I))
    for match in _RESTRICTED.finditer(text):
        prefix=text[max(0,match.start()-80):match.start()]
        if re.search(r'\b(?:until|if|unless|not|never|no longer)\s*$',prefix,re.I):
            continue
        # Use the current clause so a different preceding inventory item does
        # not explain away an event-wide closure later in the same paragraph.
        clause=re.split(r'[.;!\n]',prefix)[-1]
        other=bool(re.search(r'\b(?:hotel room|room block|accommodation|VIP (?:pass|ticket|dinner))',clause,re.I))
        named_inventory = bool(re.search(r'\b(?:VIP|dinner|hotel room|room block)\b',event_name,re.I))
        scoped = other and general_open and not named_inventory
        excerpt=text[max(0,match.start()-80):match.end()+120]
        observations.append({'text':excerpt,'scope':'other_inventory' if scoped else 'unresolved_event_access'})
        if not scoped:
            blocking.append(excerpt)
    return observations, blocking


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
    if start.year == end.year and start.month != end.month:
        left = '(?:'+calendar.month_name[start.month]+'|'+calendar.month_abbr[start.month]+r'\.?)'
        right = '(?:'+calendar.month_name[end.month]+'|'+calendar.month_abbr[end.month]+r'\.?)'
        patterns.append(left+r'\s+'+str(start.day)+r'\s*(?:-|–|—|to)\s*'+right+r'\s+'+str(end.day)+r',?\s+'+str(end.year))
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
        if organizer_url(url,host):
            url = urldefrag(url)[0]
            if url not in urls:
                urls.append(url)
    # Keep the actual event name, not just a parent brand shared by summits.
    names = _names(event,start.year)
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
        check['read_mode'] = 'javascript_fallback' if fallback else 'extracted_html'
        if raw_text:
            check['snapshot'] = source_snapshot(final,raw_text)
        # WordPress/React markers alone also occur on fully readable pages.
        if fetched.get('status') != 'ok' or fallback or fetched.get('truncated'):
            check['reason'] = 'The complete rendered page could not be read reliably.'
            continue
        # Link destinations are provenance, not visible evidence of names/dates.
        visible = re.sub(r'\[https?://[^\]\s]+\]', '', raw_text)
        text = re.sub(r'\s+', ' ', visible).strip()
        check['visible_text_snapshot'] = source_snapshot(final,text)
        observations, blocked = _access(visible,str(event.get('name') or ''))
        check['access_observations'] = observations
        if blocked:
            restrictions.append('The organizer page describes restricted or unavailable access; verify this client’s access before recommending attendance.')
            check['access_excerpt'] = blocked[0]
        for pattern in patterns:
            for match in pattern.finditer(text):
                context = text[max(0,match.start()-180):match.end()+180]
                prefix = text[max(0,match.start()-180):match.start()]
                if _owns_date(prefix,names,start.year):
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
