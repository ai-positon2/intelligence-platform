"""Bounded source checks before recommendation scoring.

Literal support is a necessary admission condition, not semantic verification.
Dynamic pages and restricted access require review rather than optimistic dates.
"""
import calendar
import json
import re
import unicodedata
from datetime import date, datetime
from urllib.parse import urlsplit, urldefrag

from .event_intel_access import organizer_url
from .event_intel_evidence import source_snapshot
from .event_intel_jobs import ContextExecutor

MAX_PAGES = 2
_RESTRICTED = re.compile(r'\b(?:invite[- ]only|invitation[- ]only|members[- ]only|'
                         r'by invitation|application[- ]only|application required|subject to approval|sold out|waitlist|'
                         r'registration (?:is )?closed|cancelled|canceled)\b', re.I)


# Words that name a different gathering, or tie this one to another. Either,
# sitting between an event's name and a date, can hand it someone else's dates.
_EVENT_WORDS = {'summit', 'conference', 'forum', 'dinner', 'expo', 'festival',
                'meetup', 'workshop', 'awards', 'show', 'bootcamp', 'breakfast',
                'lunch', 'reception', 'roundtable', 'retreat', 'meetings',
                'congress', 'symposium', 'week', 'tour', 'day', 'days'}
_TIE_WORDS = {'at', 'during', 'with', 'alongside', 'within', 'part', 'powered',
              'presented', 'by', 'and', 'x', 'partnership', 'association'}


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
        # Real organizer copy routinely uses one of these openers between an
        # event's name and its date ("TechConf 2026, taking place March 3-5",
        # "TechConf 2026 will be held March 3-5", "TechConf 2026 convenes
        # March 3-5"). Only the first word after the name is checked, so
        # "will"/"taking" alone is enough to cover the whole connecting
        # phrase that follows it. The list was originally narrow enough to
        # reject several of these on real, perfectly legitimate pages.
        allowed = {'on','runs','returns','takes','taking','starts','is','join','at','from',
                  'get','choose','register','book','will','convenes','occurs','happens'}
        allowed.update(m.casefold() for m in calendar.month_name if m)
        allowed.update(m.casefold() for m in calendar.month_abbr if m)
        if not first or first in allowed or first.isdigit():
            return True
        # "MRC Vegas 2027 Conference 15 - 18 Mar, 2027" (merchantriskcouncil.org,
        # 2026-09-30): one of the event's own nouns, and nothing else, is the
        # event naming itself. Two words ("Payments Summit") is another event.
        if tail in _OWN_NOUNS:
            return True
        # "NRF 2027: Retail's Big Show in New York City, January 10 - 12, 2027".
        # A short place is all that may stand between name and dates: nothing
        # naming another gathering, nothing tying this one to it.
        words = tail.split()
        if first == 'in' and len(words) <= 5 and not set(words) & (_EVENT_WORDS | _TIE_WORDS):
            return True
    return False


# Spaces only: the words a restriction modifies are on its own line, and the
# next line's "Check Out the Full Agenda" is not a programme name.
_MODIFIER = re.compile(r'(?:invite|invitation|members|application)[- ]only[ \t]+'
                       r'((?:[A-Z0-9][\w\u2019\'-]*[ \t]+){0,5}[A-Z0-9][\w\u2019\'-]*)')
_CLOCK = re.compile(r'\b\d{1,2}[:.]\d{2}\s*(?:[ap]\.?m\.?)?\s*(?:-|\u2013|\u2014|to)\s*\d{1,2}[:.]\d{2}|'
                    r'\b\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?\s*(?:-|\u2013|\u2014|to)\s*\d{1,2}(?::\d{2})?\s*[ap]\.?m\b', re.I)


def _other_programme(text, match, clause, event_name):
    """A restriction that plainly belongs to something other than the event.

    Singapore FinTech Festival's own overview page, 2026-09-29: "the
    invitation-only Insights2040 Annual Meetings" and a 19:00 - 22:00 evening
    session "*By invite-only", on a site selling general passes. Both were
    read as the whole festival being closed. Only two shapes are scoped, and
    anything else stays unresolved for a reviewer:

      * the restriction is a modifier and the named thing it modifies is not
        this event ("invitation-only Insights2040 Annual Meetings");
      * the restriction sits in a calendar entry with a clock-time range,
        which is a session on the agenda, not admission to the event.

    Neither can clear "sold out", "cancelled", "registration closed" or a
    waitlist: those are about inventory the event itself runs out of."""
    word = match.group(0).lower()
    if not re.search(r'only|invitation', word):
        return None
    own = _fold(re.sub(r'\s+20\d{2}$', '', event_name or ''))
    # A sentence that names this event is about this event: "CMO Summit is
    # an invitation-only Executive Gathering" is the summit being closed.
    if not own or re.search(r'(?<!\w)'+re.escape(own)+r'(?!\w)', _fold(clause)):
        return None
    modified = _MODIFIER.match(text, match.start())
    if modified:
        target = _fold(modified.group(1))
        if (own not in target and target not in own
                and set(target.split()) & _EVENT_WORDS):
            return 'named_other_programme'
    # An agenda entry spans lines (day, time, venue, then the note), so the
    # session window reads back across line breaks but never past a sentence.
    entry = re.split(r'[.;!?]', text[max(0, match.start()-120):match.start()])[-1]
    if _CLOCK.search(entry) and not re.search(
            r'(?<!\w)'+re.escape(own)+r'(?!\w)', _fold(entry)):
        return 'timed_session'
    return None


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
        # A real organizer page routinely phrases a sold-out SUB-inventory item
        # in ways this list did not originally cover ("VIP suite", "VIP box",
        # "exhibit booth", "sponsor table"), and each of those is exactly as
        # much "not the whole event" as the phrases already here.
        other=bool(re.search(r'\b(?:hotel room|room block|accommodation|'
                             r'VIP (?:pass|ticket|dinner|suite|box|table)|'
                             r'(?:exhibit|sponsor) (?:booth|table))',clause,re.I))
        named_inventory = bool(re.search(
            r'\b(?:VIP|dinner|hotel room|room block|suite|box|exhibit booth|sponsor table)\b',
            event_name,re.I))
        scoped = other and general_open and not named_inventory
        excerpt=text[max(0,match.start()-80):match.end()+120]
        scope = 'other_inventory' if scoped else _other_programme(
            text, match, clause, event_name)
        observations.append({'text':excerpt,'scope':scope or 'unresolved_event_access'})
        if not scope:
            blocking.append(excerpt)
    return observations, blocking


def _date_patterns(start, end, text=""):
    """Explicit ISO or English dates/ranges; unsupported formats stay unknown."""
    def single(d):
        month = '(?:' + calendar.month_name[d.month] + '|' + calendar.month_abbr[d.month] + r'\.?)'
        day = str(d.day) + r'(?:st|nd|rd|th)?'
        out = [re.escape(d.isoformat()), month + r'\s+' + day + r',?\s+' + str(d.year),
               day + r'\s+' + month + r',?\s+' + str(d.year)]
        # Numeric ordering must be explicit or unambiguous.
        formats = re.findall(r"\b(?:DD[/.-]MM|MM[/.-]DD)[/.-](?:YYYY|YY)\b", text, re.I)
        orders = {f[:2].upper() for f in formats}
        if len(orders) > 1:
            return out
        order = next(iter(orders), None)
        if order is None and d.day <= 12 and d.day != d.month:
            return out
        pairs = [(d.month, d.day)] if order == 'MM' else [(d.day, d.month)] if order == 'DD' else [(d.month, d.day), (d.day, d.month)]
        for first, second in pairs:
            for sep in ('/', '-'):
                for aa in dict.fromkeys((str(first), '%02d' % first)):
                    for bb in dict.fromkeys((str(second), '%02d' % second)):
                        for yy in (str(d.year), str(d.year)[2:]):
                            out.append(re.escape(aa + sep + bb + sep + yy))
        return out
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


def _structured_date(value):
    if not isinstance(value, str):
        raise ValueError("Missing structured date")
    if len(value) == 10:
        return date.fromisoformat(value)
    if len(value) > 10 and value[10] == 'T':
        return datetime.fromisoformat(value.replace('Z', '+00:00')).date()
    raise ValueError("Invalid structured date")


_STRUCTURED_SEGMENTS = re.compile(r'\s+[|\u2013\u2014\u00b7\u2022-]\s+|:\s+|,\s+')


def _structured_name_matches(row_name, names, year):
    """Is this schema.org Event node THIS event?

    Organizers routinely write the node's name the way they write a page
    title: "Fintech Meetup | Leading Fintech Event | Networking & Innovation",
    "Web Summit, Lisbon". The dates on that node are the organizer's own, so
    the name only has to lead it, and nothing after it may name another
    gathering ("SaaStr Annual | CMO Summit" is not SaaStr Annual's node) or
    tie it to one."""
    if set(_names({'name': row_name}, year)).intersection(names):
        return True
    segments = [x for x in _STRUCTURED_SEGMENTS.split(row_name) if x.strip()]
    if len(segments) < 2 or not set(_names({'name': segments[0]}, year)).intersection(names):
        return False
    rest = set(_fold(' '.join(segments[1:])).split())
    # "and" alone joins words in a tagline ("Networking & Innovation"); it only
    # ties two gatherings together when one of them is named, which the event
    # words already catch.
    if rest & (_EVENT_WORDS | (_TIE_WORDS - {'and'})):
        return False
    return not any(y != str(year) for y in re.findall(r'\b20\d{2}\b', ' '.join(segments[1:])))


def _structured_support(rows, names, start, end):
    matches, restrictions = [], []
    for row in rows or []:
        if not isinstance(row, dict) or not isinstance(row.get('name'), str):
            continue
        if not _structured_name_matches(row['name'], names, start.year):
            continue
        try:
            actual_start = _structured_date(row.get('startDate'))
            actual_end = _structured_date(row.get('endDate'))
        except ValueError:
            continue
        if (actual_start, actual_end) != (start, end):
            continue
        matches.append(row)
        status = str(row.get('eventStatus') or '').rsplit('/',1)[-1]
        if status in ('EventCancelled', 'EventPostponed', 'EventRescheduled'):
            restrictions.append('Organizer structured data reports a cancelled, postponed or rescheduled edition; verify current dates and access.')
        offers = row.get('offers') or []
        offers = offers if isinstance(offers, list) else [offers]
        if any(isinstance(o, dict) and str(o.get('availability') or '').rsplit('/',1)[-1] in
               ('SoldOut', 'Discontinued', 'OutOfStock') for o in offers):
            restrictions.append('Organizer structured ticket data reports unavailable inventory; verify access.')
    return matches, restrictions


# ── the page title as organizer copy ──────────────────────────────────────
#
# Real organizer pages often say their dates in a sentence that does not
# repeat the event's name: us.money2020.com reads "fintech's #1 event in Las
# Vegas October 18-21, 2026", with "Money20/20 USA" only in the heading. The
# body rule above correctly refuses that sentence, and Money20/20 USA, the
# most relevant event a 2026-09-29 Stripe run found, was withheld from
# scoring for it. The same page's <title> is "Money20/20 USA in Las Vegas |
# October 18-21, 2026": the organizer naming this page's event and its dates
# in one line it wrote for exactly that purpose.
#
# A title is trusted only in the shape that cannot carry somebody else's
# dates. The event's name must lead the FIRST segment (nothing before it but
# a call to action), and the dates must either follow it in that segment
# with nothing between that introduces another event, or BE the whole
# second segment. So "CMO Summit at SaaStr Annual | Sept 9-11" admits
# neither event, and "SaaStr Annual | CMO Summit | Sept 9-11" admits
# neither, because a title naming two events does not say whose dates
# those are. Headings are deliberately not used: a summit's own page headed
# with its name routinely carries only its parent conference's dates.

# A dash between two numbers is a date range ("8 - 10 June 2027"), not a separator.
_TITLE_SEGMENTS = re.compile(r'\s+[|\u00b7\u2022]\s+|:\s+|(?<!\d)\s+[\u2013\u2014-]\s+|\s+[\u2013\u2014-]\s+(?!\d)')
_TITLE_LEAD = {'attend', 'join', 'register', 'for', 'welcome', 'to', 'the',
               'official', 'home', 'us'}
_TITLE_NEXT = {'in', 'on', 'is', 'returns', 'from', 'will', 'takes', 'taking'}
_TITLE_FOREIGN = {'at', 'during', 'with', 'alongside', 'within', 'part',
                  'powered', 'presented', 'by', 'and', 'x'}


def _title_owns(segment, names):
    """The words after the name, if this segment is led by the name."""
    folded = _fold(segment)
    for name in names:
        m = re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)', folded)
        if m and set(folded[:m.start()].split()) <= _TITLE_LEAD:
            return folded[m.end():].split()
    return None


def _title_support(titles, names, start, end):
    for title in titles or []:
        segments = [x for x in _TITLE_SEGMENTS.split(title) if x.strip()]
        if not segments:
            continue
        tail = _title_owns(segments[0], names)
        if tail is None:
            continue
        if tail and re.fullmatch(r'20\d{2}', tail[0]):
            if tail[0] != str(start.year):
                continue  # "Money20/20 USA 2025" is another edition.
            tail = tail[1:]
        months = {m.casefold() for m in list(calendar.month_name) + list(calendar.month_abbr) if m}
        if tail and not (tail[0] in _TITLE_NEXT or tail[0] in months or tail[0].isdigit()):
            continue
        # Between the name and the dates, nothing that introduces another event.
        head = []
        for w in tail:
            if w in months or w.isdigit():
                break
            head.append(w)
        if set(head) & _TITLE_FOREIGN or any(y != str(start.year) for y in re.findall(r'\b20\d{2}\b', ' '.join(head))):
            continue
        for pattern in _date_patterns(start, end, title):
            if pattern.search(segments[0]):
                return title
            if len(segments) > 1:
                rest = pattern.sub('', segments[1])
                if pattern.search(segments[1]) and not re.search(r'\w', rest):
                    return title
    return None


# ── a page whose title names the event and whose body says "the event" ────
#
# payments.nacha.org, 2026-09-30: title "Smarter Faster Payments Conference |
# Payments 2027", body "The in-person event takes place at the Gaylord
# National Harbor Resort & Convention Center, minutes from Washington, D.C.,
# April 11-14, 2027." The organizer names its event in the title and refers
# to it as "the event" in the sentence that dates it. Both rules above refuse
# that, correctly on their own terms: the title has no full date and the body
# never repeats the name.
#
# Accepted only in this shape:
#   * the title's FIRST segment is led by the name, followed by nothing or
#     by one generic noun for the event itself ("Conference", "Summit"), and
#     the title carries no other year;
#   * one sentence refers to the event with "the/this/our", optionally
#     "in-person"/"annual"/"main"/"flagship", then a noun for the event and a
#     verb of taking place, and the candidate's full date range follows in
#     that sentence;
#   * nothing between that verb and the dates ties the event to another one.
# "The virtual event takes place June 7-9" is refused: a virtual edition is
# another event on the same page. So is "the event takes place during X".

_OWN_NOUNS = {'conference', 'summit', 'expo', 'forum', 'event', 'show',
              'convention', 'congress', 'festival', 'exhibition'}
_SELF_REF = re.compile(
    r"(?<![\w-])(?:the|this|our)\s+(?:(?:in[- ]person|annual|main|flagship|live)\s+){0,2}"
    r"(?:event|conference|summit|expo|show|forum|festival|convention|congress|exhibition)\s+"
    r"(?:takes\s+place|will\s+take\s+place|is\s+taking\s+place|is\s+held|will\s+be\s+held|"
    r"runs|returns|happens|comes\s+to|lands\s+in)\b", re.I)
_SELF_REF_FOREIGN = {'during', 'alongside', 'with', 'part', 'co', 'colocated',
                     'located', 'ahead', 'before', 'after', 'following'}
_VENUE_NOUNS = {'center', 'centre', 'hall', 'halls', 'room', 'venue', 'hotel',
                'resort', 'campus', 'complex', 'park'}


def _title_identifies(titles, names, year):
    for title in titles or []:
        if any(y != str(year) for y in re.findall(r'\b20\d{2}\b', title)):
            continue
        segments = [x for x in _TITLE_SEGMENTS.split(title) if x.strip()]
        tail = _title_owns(segments[0], names) if segments else None
        if tail and tail[0] == str(year):
            tail = tail[1:]  # "MRC Vegas 2027" is the plainest title there is.
        if tail is not None and (not tail or (len(tail) == 1 and tail[0] in _OWN_NOUNS)):
            return title
    return None


def _self_referenced_dates(text, start, end):
    for sentence in re.split(r'[.!?](?=\s+[A-Z]|\s*$)', text):
        ref = _SELF_REF.search(sentence)
        if not ref:
            continue
        for pattern in _date_patterns(start, end, sentence):
            m = pattern.search(sentence, ref.end())
            if not m:
                continue
            between = _fold(sentence[ref.end():m.start()]).split()
            if set(between) & _SELF_REF_FOREIGN:
                continue
            named = [w for i, w in enumerate(between) if w in _EVENT_WORDS | _OWN_NOUNS
                     and not (i + 1 < len(between) and between[i + 1] in _VENUE_NOUNS)]
            if named or any(y != str(start.year) for y in re.findall(r'\b20\d{2}\b', ' '.join(between))):
                continue
            return sentence.strip()
    return None


def inspect(event, fetcher=None):
    from .event_intel_harvest import fetch_page
    fetcher = fetcher or fetch_page
    result = {'name':event.get('name'), 'support':'unverified', 'checks':[], 'reasons':[]}
    try:
        start = date.fromisoformat(str(event.get('starts_on') or ''))
        end = date.fromisoformat(str(event.get('ends_on') or event.get('starts_on') or ''))
        host = urlsplit(event.get('website') or '').hostname
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
        # Public JSON-LD is organizer metadata, explicitly distinct from rendered text.
        # It must match this event and the complete date range; parent metadata cannot admit a subevent.
        structured = []
        if not fetched.get('truncated') and fetched.get('http_status') == 200:
            structured, structured_restrictions = _structured_support(
                fetched.get('structured_events'), names, start, end)
            restrictions.extend(structured_restrictions)
        if structured:
            check['structured_event_evidence'] = structured
            check['structured_snapshot'] = source_snapshot(final,json.dumps(structured,sort_keys=True))
            check['support'] = 'organizer_structured_name_and_dates'
            supported = True
            _, blocked = _access(raw_text, str(event.get('name') or ''))
            if blocked:
                restrictions.append('The organizer page describes restricted or unavailable access; verify this client’s access before recommending attendance.')
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
        for pattern in _date_patterns(start, end, text):
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
        if not check.get('date_excerpt') and not structured:
            title = _title_support(fetched.get('titles'), names, start, end)
            if title:
                supported = True
                check['title_excerpt'] = title
                check['title_snapshot'] = source_snapshot(final, title)
                check['support'] = 'organizer_title_name_and_dates'
        if not check.get('support'):
            title = _title_identifies(fetched.get('titles'), names, start.year)
            sentence = title and _self_referenced_dates(text, start, end)
            if sentence:
                supported = True
                check['title_excerpt'] = title
                check['title_snapshot'] = source_snapshot(final, title)
                check['date_excerpt'] = sentence[:500]
                check['support'] = 'organizer_title_name_and_self_referenced_dates'
    if not supported:
        result['reasons'].append('The named event and its date range could not be found together in readable organizer text; parent-event dates or model claims alone are insufficient.')
    result['reasons'].extend(sorted(set(restrictions)))
    if supported and not result['reasons']:
        # The strongest evidence any page gave, in this order.
        kinds = {c.get('support') for c in result['checks']}
        result['support'] = next(k for k in ('organizer_structured_name_and_dates',
                                             'literal_name_and_dates_only',
                                             'organizer_title_name_and_dates',
                                             'organizer_title_name_and_self_referenced_dates') if k in kinds)
    result['not_read'] = max(0,len(urls)-MAX_PAGES)
    return result


def inspect_all(events):
    with ContextExecutor(max_workers=4) as pool:
        return list(pool.map(inspect,events))
