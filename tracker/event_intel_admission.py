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
# The phrases an organizer uses to say the event itself is closed or gated.
# An audit on 2026-09-30 found nine everyday ones admitted clean with no
# reason at all ("Registration is now closed", "Registrations are closed",
# "SOLD-OUT", "Fully booked", "postponed", "Apply to attend", "Request an
# invitation", "At capacity"), so the wording allows an adverb, a hyphen
# and a plural where real pages use them. "Apply to sponsor" and "Apply to
# exhibit" (fintechweek.hk's header) are not in it: those gate a stand, not
# a seat.
_RESTRICTED = re.compile(r'\b(?:invite[- ]only|invitation[- ]only|members[- ]only|'
                         r'by invitation|application[- ]only|application required|subject to approval|'
                         r'sold[- ]?out|wait[- ]?list(?:ed)?|fully[- ]booked|at capacity|postponed|'
                         r'apply to attend|request (?:an? )?invit(?:e|ation)|'
                         r'registrations? (?:is |are |has |have )?(?:now |officially |already )?closed|'
                         r'cancelled|canceled)\b', re.I)


# Words that name a different gathering, or tie this one to another. Either,
# sitting between an event's name and a date, can hand it someone else's dates.
_EVENT_WORDS = {'summit', 'conference', 'forum', 'dinner', 'expo', 'festival',
                'meetup', 'workshop', 'awards', 'show', 'bootcamp', 'breakfast',
                'lunch', 'reception', 'roundtable', 'retreat', 'meetings',
                'congress', 'symposium', 'week', 'tour', 'day', 'days'}
_TIE_WORDS = {'at', 'during', 'with', 'alongside', 'within', 'part', 'powered',
              'presented', 'by', 'and', 'x', 'partnership', 'association'}


_BARE_PLACE = re.compile(r'([A-Z][a-z]+(?:[ -][A-Z][a-z]+){0,2}),[ \t]*'
                         r'(?:[A-Z]{2}|[A-Z][a-z]+(?:[ ][A-Z][a-z]+)?)\.?[ \t]*$')
_EDITION_WORDS = {'spring', 'summer', 'fall', 'autumn', 'winter', 'europe', 'asia',
                  'africa', 'america', 'americas', 'usa', 'emea', 'apac', 'latam',
                  'mena', 'middle', 'east', 'west', 'north', 'south', 'central',
                  'global', 'virtual', 'online', 'digital', 'live', 'world',
                  'international', 'regional'}


def _fold(text):
    # Accents are dropped: an organizer writes "Salon" in its nav and
    # "Salón" in its hero, and a model echoes either (audit, 2026-09-30).
    text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))
    text = unicodedata.normalize('NFKC',text).casefold().replace('&',' and ')
    return ' '.join(re.findall(r'\w+',text))


def _name_re(name):
    """A folded name as a pattern over folded text.

    Letters and digits are separate tokens and any spacing between tokens
    is allowed, so "Money 20/20" and "Money20/20" (both on money2020.com)
    are one name; nothing but whitespace may come between them."""
    tokens = re.findall(r'[^\W\d]+|\d+', name)
    return r'(?<!\w)' + r'\s*'.join(map(re.escape, tokens)) + r'(?!\w)'


def _names(event, year):
    name = str(event.get('name') or '').strip()
    if any(y != str(year) for y in re.findall(r'\b20\d{2}\b',name)):
        return []
    # Strip only a matching year and an explicit acronym, never regional names.
    name = re.sub(r'\s*\([A-Z0-9]{2,12}\)\s*$', '', name)
    name = re.sub(r'\s+'+str(year)+r'$', '', name)
    return [_fold(name)] if _fold(name) else []


# Cities that big brands use to NAME a regional spin-off ("Web Summit Rio",
# "Web Summit Vancouver", "MWC Shanghai", "Money20/20 Middle East" in
# Riyadh). An audit on 2026-09-30 found "Web Summit in Rio, ..." and a
# JSON-LD node "Web Summit | Vancouver" both admitting plain "Web Summit".
# A city a name does not carry, from this list, is another edition. It is
# a short list on purpose: a flagship's own city ("Web Summit, Lisbon",
# "Money20/20 Europe in Amsterdam", "Fintech Meetup, Las Vegas") must stay
# a place, and a city missing here falls back to the other checks.
_EDITION_CITIES = ('rio', 'rio de janeiro', 'vancouver', 'qatar', 'doha', 'tokyo',
                   'shanghai', 'riyadh', 'toronto', 'sao paulo', 'mexico city',
                   'abu dhabi', 'jakarta', 'bangalore', 'bengaluru', 'mumbai',
                   'cape town', 'nairobi', 'lagos', 'seoul', 'manila', 'bangkok',
                   'kuala lumpur')
# Words that make the thing after "to"/"in" a gathering, not a place:
# "returns to SaaStr Annual".
_EVENTISH = _EVENT_WORDS | {'annual', 'edition', 'series', 'track', 'stage',
                            'programme', 'program', 'session', 'sessions'}
_VENUE_NOUNS = {'center', 'centre', 'hall', 'halls', 'room', 'venue', 'hotel',
                'resort', 'campus', 'complex', 'park', 'arena', 'stadium',
                'pavilion', 'club', 'house', 'museum', 'theatre', 'theater',
                'palace', 'fairgrounds', 'casino', 'auditorium', 'plaza'}
_MONTH_NAMES = {m.casefold() for m in list(calendar.month_name) + list(calendar.month_abbr) if m} | {'sept'}
_WEEKDAYS = {d.casefold() for d in list(calendar.day_name) + list(calendar.day_abbr)}
# Real organizer copy between an event's name and its date ("TechConf 2026,
# taking place March 3-5", "will be held", "convenes", "returns to Las
# Vegas", "Join us"). Every word of that stretch must be one of these, or
# sit in a place phrase: an audit on 2026-09-30 found only the FIRST word
# was checked, and "CMO Summit is part of SaaStr Annual, September 9-11"
# and "CMO Summit from the SaaStr Annual team, ..." were admitted with
# SaaStr Annual's dates.
_CONNECT = {'on', 'runs', 'run', 'returns', 'return', 'returning', 'takes', 'taking',
            'take', 'place', 'starts', 'start', 'starting', 'begins', 'is', 'are',
            'will', 'be', 'being', 'held', 'convenes', 'convening', 'occurs',
            'happens', 'happening', 'scheduled', 'set', 'coming', 'comes', 'back',
            'again', 'this', 'year', 'dates', 'date', 'join', 'us', 'get', 'choose',
            'register', 'book', 'your', 'now', 'today', 'ticket', 'tickets', 'pass',
            'passes', 'officially'}
_PLACE_INTRO = {'in', 'to', 'at'}
# A call to action that may carry the previous sentence's name to a date:
# "CMO Summit 2027. Choose your pass to join us May 11-12, 2027." Nothing
# else may be in it: "Join us for the Women in SaaS Breakfast on ..." and
# "Register for SaaStr Europa, ..." name other events (audit, 2026-09-30).
_CTA = {'get', 'choose', 'join', 'register', 'book'}
_CTA_CLAUSE = _CTA | {'us', 'your', 'a', 'the', 'pass', 'passes', 'ticket', 'tickets',
                      'seat', 'spot', 'now', 'today', 'to', 'and', 'on'}
# One of the event's own nouns alone is the event naming itself ("MRC Vegas
# 2027 Conference"). Expo, forum and show are not: they are routinely a
# separately dated part of the event ("RSAC Expo" runs fewer days than
# RSAC), so "RSAC Expo September 9-11" does not date RSAC (audit, 2026-09-30).
_SELF_NOUNS = {'conference', 'summit', 'event', 'convention', 'congress',
               'festival', 'exhibition'}


def _edition_place(words, names):
    """The words name a regional edition this event's name does not carry."""
    own = set(' '.join(names).split())
    if set(words) & (_EDITION_WORDS - own):
        return True
    folded, owned = ' '.join(words), ' '.join(names)
    return any(re.search(r'(?<!\w)'+c+r'(?!\w)', folded) and not re.search(r'(?<!\w)'+c+r'(?!\w)', owned)
               for c in _EDITION_CITIES)


def _place_ok(intro, words, names):
    """A place after "in"/"to"/"at", and nothing that is another gathering."""
    if not words:
        return True
    if _edition_place(words, names):
        return False
    if intro in ('in', 'to'):
        # "NRF 2027: Retail's Big Show in New York City, January 10 - 12".
        return len(words) <= 5 and not set(words) & (_EVENTISH | _TIE_WORDS)
    # "at" introduces a venue ("at Moscone Center") or another event ("at
    # SaaStr Annual"). Only a venue noun makes it a venue; an event word is
    # allowed only as part of one ("Convention Center", "Expo Hall").
    if len(words) > 8 or not set(words) & _VENUE_NOUNS:
        return False
    return not any((w in _EVENTISH and not (i + 1 < len(words) and words[i + 1] in _VENUE_NOUNS))
                   or w in (_TIE_WORDS - {'and'}) for i, w in enumerate(words))


def _connecting(words, names, year):
    """Is this stretch between an event's name and a date only connecting
    copy and at most a place, so the date is the event's own?"""
    i = 0
    while i < len(words):
        w = words[i]
        if w in _PLACE_INTRO:
            j = i + 1
            while j < len(words) and words[j] not in _CONNECT and words[j] not in _PLACE_INTRO:
                j += 1
            if not _place_ok(w, words[i+1:j], names):
                return False
            i = j
            continue
        # "runs from September 9", never "from the SaaStr Annual team".
        if w == 'from' and i == len(words) - 1:
            i += 1
            continue
        if w in _CONNECT or w in _MONTH_NAMES or w in _WEEKDAYS or w == str(year):
            i += 1
            continue
        return False
    return True


def _bare_place(prefix, tail, names, year, titles):
    """ "fintech_devcon Boulder, CO August 2-4, 2027" (fintechdevcon.io,
    2026-09-30): a header's "City, ST" and nothing else. Never a season or
    region, which name an edition: "Shoptalk Fall Chicago, IL".

    That shape is also exactly how a regional edition is written: "MWC
    Shanghai, China June 1-5" and "SXSW London, UK June 1-5" admitted MWC and
    SXSW (audit, 2026-09-30), and nothing in the words tells "London, UK"
    after fintech_devcon from "London, UK" after SXSW. What does is whose
    page it is, so the place is accepted only when a page title names this
    event plainly (fintechdevcon.io's title is "fintech_devcon"; SXSW
    London's page is titled with its edition). The place is read from the
    text right after the name: searched from the left, "SaaStr Annual San
    Mateo, CA" was read as the place "Annual San Mateo" and held."""
    if not tail or not _title_identifies(titles, names, year):
        return False
    raw = prefix.split()
    for k in range(1, min(len(raw), 7) + 1):
        piece = ' '.join(raw[-k:])
        if _fold(piece) != tail:
            continue
        place = _BARE_PLACE.fullmatch(piece.strip())
        city = _fold(place.group(1)).split() if place else []
        return bool(place and not set(city) & (_EDITION_WORDS | _EVENTISH | _TIE_WORDS)
                    and not _edition_place(_fold(piece).split(), names))
    return False


def _owns_date(prefix, names, year, titles=None):
    last_clause = re.split(r'[.!?]\s+',prefix)[-1]
    clause = _fold(last_clause).split()
    if last_clause.strip() and not (clause and clause[0] in _CTA and set(clause) <= _CTA_CLAUSE):
        prefix = last_clause
    folded = _fold(prefix)
    for name in names:
        matches = list(re.finditer(_name_re(name),folded))
        if not matches:
            continue
        tail = folded[matches[-1].end():].strip()
        if any(y != str(year) for y in re.findall(r'\b20\d{2}\b',tail)):
            continue
        tail = re.sub(r'^'+str(year)+r'\b', '', tail).strip()
        # "MRC Vegas 2027 Conference 15 - 18 Mar, 2027" (merchantriskcouncil.org,
        # 2026-09-30): one of the event's own nouns, and nothing else, is the
        # event naming itself. Two words ("Payments Summit") is another event.
        if tail in _SELF_NOUNS:
            return True
        if _connecting(tail.split(), names, year):
            return True
        if _bare_place(prefix, tail, names, year, titles):
            return True
    return False


# Spaces only: the words a restriction modifies are on its own line, and the
# next line's "Check Out the Full Agenda" is not a programme name.
_MODIFIER = re.compile(r'(?:invite|invitation|members|application)[- ]only[ \t]+'
                       r'((?:[A-Z0-9][\w\u2019\'-]*[ \t]+){0,5}[A-Z0-9][\w\u2019\'-]*)')
_CLOCK = re.compile(r'\b\d{1,2}[:.]\d{2}\s*(?:[ap]\.?m\.?)?\s*(?:-|\u2013|\u2014|to)\s*\d{1,2}[:.]\d{2}|'
                    r'\b\d{1,2}(?::\d{2})?\s*[ap]\.?m\.?\s*(?:-|\u2013|\u2014|to)\s*\d{1,2}(?::\d{2})?\s*[ap]\.?m\b', re.I)


_MONTH = (r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?')
_DAY_RANGE = re.compile(r'\b(\d{1,2})(?:st|nd|rd|th)?\s*[-\u2010-\u2014\u2212]\s*\d{1,2}(?:st|nd|rd|th)?\s+'+_MONTH+
                        r'(?:,?\s+(20\d{2}))?|\b'+_MONTH+r'\s+(\d{1,2})(?:st|nd|rd|th)?\s*[-\u2010-\u2014\u2212]\s*'
                        r'\d{1,2}(?:st|nd|rd|th)?(?:,?\s+(20\d{2}))?', re.I)


def _another_edition(text, match, own, dates):
    """The restriction closes a sentence dating a DIFFERENT multi-day event.

    Merchant Risk Council's home page, 2026-09-30: "Connect with Europe's
    payments and fraud prevention leaders 2\u20134 November in Dublin.
    Members-Only." That is MRC Dublin; MRC Vegas is 15-18 March. Only a day
    range counts (a deadline is one day: "Early bird ends 27 January.
    Members only." stays unresolved), and only one that cannot overlap this
    event: a range with no year in one of this event's months could be it."""
    start, end = dates
    before = text[max(0, match.start()-240):match.start()]
    pieces = re.split(r'(?<=[.!?])\s+', before)
    current = pieces[-1]
    # The restriction is its own sentence, or shares one with the range.
    window = current if current.strip() else ' '.join(pieces[-2:])
    if _names_any(own, _fold(window)):
        return False
    months = {start.month, end.month}
    for m in _DAY_RANGE.finditer(window):
        month = m.group(2) or m.group(4)
        year = m.group(3) or m.group(6)
        number = [i for i, x in enumerate(calendar.month_abbr) if x and month[:3].lower() == x.lower()]
        if not number:
            continue
        if year:
            y = int(year)
            if not (date(y, number[0], 1) <= date(end.year, end.month, 1)
                    and date(y, number[0], 1) >= date(start.year, start.month, 1)):
                return True
        elif number[0] not in months:
            return True
    return False


def _own_names(event_name, year):
    """Every way the page may name this event: its folded names, plus the
    acronym _names strips. An audit on 2026-09-30 found "Singapore FinTech
    Festival (SFF)" kept its "(SFF)" here, so the page's "Singapore FinTech
    Festival is an invite-only Leadership Forum" was never recognised as
    the festival describing itself and was cleared as another programme."""
    own = list(_names({'name': event_name}, year))
    acronym = re.search(r'\(([A-Z0-9]{2,12})\)\s*$', str(event_name or ''))
    if acronym:
        own.append(_fold(acronym.group(1)))
    return own


def _names_any(own, folded):
    return any(re.search(_name_re(n), folded) for n in own)


# "Our flagship gathering is an invite-only Executive Summit" (audit,
# 2026-09-30): a subject and a copula in front of the modifier is the page
# describing a thing as invite-only, and on the event's own page that thing
# is the event. Only a modifier with no copula before it ("leads into the
# invitation-only Insights2040 Annual Meetings") names something else.
_COPULA = re.compile(r"\b(?:is|are|was|will be|remains|becomes)\s+(?:(?:an?|the|our|this|now|once again|again)\s+){0,2}\*?$", re.I)
_MONTH_WORD = (r'(' + '|'.join([m for m in calendar.month_name if m] + ['Sept'] +
                                [m for m in calendar.month_abbr if m]) + r')\b\.?')
_DAY_IN_ENTRY = re.compile(r'(?<![\d:.])\b(\d{1,2})(?:st|nd|rd|th)?\s+' + _MONTH_WORD + '|'
                           r'\b' + _MONTH_WORD + r'\s+(\d{1,2})(?:st|nd|rd|th)?\b', re.I)


def _entry_day(entry, year):
    m = None
    for m in _DAY_IN_ENTRY.finditer(entry):
        pass
    if not m:
        return None
    day, month = (m.group(1), m.group(2)) if m.group(1) else (m.group(4), m.group(3))
    number = [i for i, x in enumerate(calendar.month_abbr) if x and x.lower() == month[:3].lower()]
    try:
        return date(year, number[0], int(day))
    except (ValueError, IndexError):
        return None


def _clock_hours(clock):
    """Length in hours of a clock range, or None when it cannot be read."""
    times = re.findall(r'(\d{1,2})(?:[:.](\d{2}))?\s*([ap])?\.?m?\.?', clock, re.I)
    if len(times) < 2:
        return None
    def hours(h, m, ap):
        h = int(h) % 12 + (12 if (ap or '').lower() == 'p' else 0) if ap else int(h)
        return h + int(m or 0) / 60
    (h1, m1, a1), (h2, m2, a2) = times[0], times[-1]
    start, end = hours(h1, m1, a1 or a2), hours(h2, m2, a2)
    return (end - start) % 24


def _other_programme(text, match, clause, event_name, dates, page):
    """A restriction that plainly belongs to something other than the event.

    Singapore FinTech Festival's own overview page, 2026-09-29: "the
    invitation-only Insights2040 Annual Meetings" and a 19:00 - 22:00 evening
    session "*By invite-only", on a site selling general passes. Both were
    read as the whole festival being closed. Only these shapes are scoped,
    and anything else stays unresolved for a reviewer:

      * the restriction is a modifier and the named thing it modifies is not
        this event ("invitation-only Insights2040 Annual Meetings"), with no
        copula in front of it ("X is an invite-only Summit" is X itself);
      * the restriction sits in a calendar entry with a clock-time range
        that is plainly one session and not the event's own hours (see
        below);
      * the restriction closes a sentence dating another multi-day event
        (see _another_edition).

    Neither can clear "sold out", "cancelled", "registration closed" or a
    waitlist: those are about inventory the event itself runs out of."""
    word = match.group(0).lower()
    if not re.search(r'only|invitation', word):
        return None
    own = _own_names(event_name, dates[0].year)
    # A sentence that names this event is about this event: "CMO Summit is
    # an invitation-only Executive Gathering" is the summit being closed.
    if not own or _names_any(own, _fold(clause)):
        return None
    modified = _MODIFIER.match(text[match.start():match.start()+300])
    if modified and not _COPULA.search(clause):
        target = _fold(modified.group(1))
        if (not any(n in target or target in n for n in own)
                and set(target.split()) & _EVENT_WORDS):
            return 'named_other_programme'
    # An agenda entry spans lines (day, time, venue, then the note), so the
    # session window reads back across line breaks but never past a sentence.
    entry = re.split(r'[.;!?]', text[max(0, match.start()-120):match.start()])[-1]
    clocks = list(_CLOCK.finditer(entry))
    if clocks and not _names_any(own, _fold(entry)) and _one_session(entry, clocks[-1], dates, page):
        return 'timed_session'
    if _another_edition(text, match, own, dates):
        return 'other_dated_event'
    return None


def _one_session(entry, clock, dates, page):
    """Is this timed entry one session, not the event's own opening hours?

    An audit on 2026-09-30: a hero block "9 September 2026 / 09:00 - 17:30 /
    Moscone West / Invitation only" was cleared as a session, and that is
    the event's first day and its doors. SFF's real entry is "Tuesday 17th
    November / 19:00 - 22:00 / Jiak Kim House / *By invite-only", the night
    before an 18-20 November festival. So an entry is a session only when
    its day falls outside the event's own dates, or when the page runs an
    agenda (at least one other timed entry) and this one is short (under six
    hours) or is itself a named gathering ("Founders Dinner")."""
    start, end = dates
    day = _entry_day(entry, start.year)
    if day and not (start <= day <= end):
        return True
    if page.setdefault('clocks', None) is None:
        page['clocks'] = sum(1 for _ in zip(range(50), _CLOCK.finditer(page['text'])))
    if page['clocks'] < 2:
        return False
    hours = _clock_hours(clock.group(0))
    named = set(_fold(entry[clock.end():]).split()) & (_EVENT_WORDS - {'day', 'days', 'week'})
    return bool(named) or (hours is not None and hours < 6)


# Passes a festival hands out to a category of guest, not to the paying
# audience: "Government and Media Passes: complimentary and subject to
# approval" (fintechweek.hk FAQ, 2026-09-30) says nothing about buying a
# delegate pass.
_CONCESSION = {'government', 'media', 'press', 'journalist', 'student', 'academic',
               'startup', 'investor', 'volunteer', 'nonprofit', 'non', 'profit'}
_PASS_JOIN = {'and', 'or'}
# All that may stand between the concession pass and the restriction: the
# pass must be the subject the restriction is said of.
_PASS_VERB = {'are', 'is', 'will', 'be', 'remain', 'remains', 'complimentary',
              'free', 'and', 'also', 'still'}


def _concession_pass(clause, match):
    """Approval required for one guest category's pass, not the event's.

    The words just before "pass" must name concession categories only, and
    a phrase joining one to anything else is not one: "Delegate and media
    passes are subject to approval" covers the paying audience too. The
    pass must also be what the restriction is said OF: an audit on
    2026-09-30 found "Apart from media passes, attendance is by invitation
    only" and "Media passes sold separately, and all attendee registration
    is subject to approval" both cleared, and each closes the whole event."""
    if not re.search(r'approval|application|invit', match.group(0), re.I):
        return None
    for named in re.finditer(r'\bpass(?:es)?\b', clause, re.I):
        words = _fold(clause[:named.start()]).split()
        kinds = []
        while words and (words[-1] in _PASS_JOIN or words[-1] in _CONCESSION):
            kinds.append(words.pop())
        between = set(_fold(clause[named.end():]).split())
        if (kinds and kinds[-1] not in _PASS_JOIN and kinds[0] not in _PASS_JOIN
                and between <= _PASS_VERB):
            return 'concession_pass'
    return None


def _hypothetical(text, match):
    """A restriction asked about, not stated: "What happens if the Event is
    canceled or postponed?" is on every FAQ page. Only a question that is
    itself conditional counts; "Is the conference sold out?" is left for a
    reviewer, because the next line may answer yes.

    The look-ahead is bounded: unbounded, it rescanned the rest of the page
    for every restriction, and an audit on 2026-09-30 timed 180KB of
    punctuation-free "sold out" at 12 seconds. A question mark further than
    200 characters away does not end this sentence anyway."""
    after = re.match(r'[^.!?\n]*([.!?\n])', text[match.end():match.end()+200])
    if not after or after.group(1) != '?':
        return None
    question = re.split(r'[.!?\n]', text[max(0, match.start()-160):match.start()])[-1]
    if re.search(r'\b(?:if|in case|in the event|should|what happens)\b', question, re.I):
        return 'hypothetical_question'
    return None


# Enough to show a reviewer the pattern; the page is still read to its end
# for a blocking restriction.
MAX_OBSERVATIONS = 50


def _access(text, event_name, dates):
    observations, blocking = [], []
    page = {'text': text}
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
        scope = ('other_inventory' if scoped else _concession_pass(clause, match)
                 or _hypothetical(text, match) or _other_programme(
                     text, match, clause, event_name, dates, page))
        if len(observations) < MAX_OBSERVATIONS:
            observations.append({'text':excerpt,'scope':scope or 'unresolved_event_access'})
        if not scope:
            blocking.append(excerpt)
            if len(blocking) >= MAX_OBSERVATIONS:
                break
    return observations, blocking


# Range separators as organizers type them: hyphen, the Unicode hyphens,
# figure dash, en and em dash, minus sign (U+2012 and U+2212 both occur in
# CMS output), and words.
_RANGE_SEP = r'(?:[-\u2010-\u2014\u2212]|to|through|thru|until|till)'


def _month_rx(m):
    """A month as organizers write it: "September", "Sep", "Sep." and the
    most common US form, "Sept"/"Sept." (audit, 2026-09-30: "Sept 9-11,
    2026" was held for want of it)."""
    forms = [calendar.month_name[m], calendar.month_abbr[m] + r'\.?']
    if m == 9:
        forms.insert(1, r'Sept\.?')
    return '(?:' + '|'.join(forms) + ')'


def _weekday_rx(d):
    """An optional weekday before a date, and only the right one: a wrong
    weekday is another year's edition."""
    return r'(?:(?:' + calendar.day_name[d.weekday()] + '|' + calendar.day_abbr[d.weekday()] + r'\.?),?\s+)?'


def _day_rx(d):
    return str(d.day) + r'(?:st|nd|rd|th)?'


class _DatePattern:
    """A compiled date pattern that refuses two kinds of near miss.

    A one-day pattern refuses a match inside a range. Audit, 2026-09-30: a
    one-day candidate dated 11 September was admitted from "TechConf 9-11
    September 2026", because "11 September 2026" is in it. A date with a
    day or month and a range separator right before it, or a separator and
    a day or month right after it, is the end or start of a range.

    Any pattern refuses a match right after a weekday: the right weekday is
    part of the pattern and would have been matched with it, so "Tuesday,
    21 and Thursday, 22 April 2027" is another year's calendar."""
    _MONTHS = '|'.join(sorted(_MONTH_NAMES, key=len, reverse=True))
    _BEFORE = re.compile(r'(?:\d(?:st|nd|rd|th)?|\b(?:' + _MONTHS + r')\.?)\s*(?:' + _RANGE_SEP + r'|&|and)\s*$', re.I)
    _AFTER = re.compile(r'\s*(?:' + _RANGE_SEP + r'|&|and)\s*(?:\d|(?:' + _MONTHS + r')\b)', re.I)
    _WEEKDAY = re.compile(r'\b(?:' + '|'.join(sorted(_WEEKDAYS, key=len, reverse=True)) + r')\.?,?\s*$', re.I)

    def __init__(self, rx, single):
        self.rx, self.single = rx, single

    def finditer(self, text, pos=0):
        for m in self.rx.finditer(text, pos):
            before = text[max(0, m.start()-24):m.start()]
            if self._WEEKDAY.search(before):
                continue
            if self.single and (self._BEFORE.search(before) or self._AFTER.match(text, m.end())):
                continue
            yield m

    def search(self, text, pos=0):
        return next(self.finditer(text, pos), None)

    def sub(self, repl, text):
        out, last = [], 0
        for m in self.finditer(text):
            out += [text[last:m.start()], repl]
            last = m.end()
        return ''.join(out + [text[last:]])


def _date_patterns(start, end, text=""):
    """Explicit ISO or English dates/ranges; unsupported formats stay unknown."""
    def single(d):
        month, day, wd = _month_rx(d.month), _day_rx(d), _weekday_rx(d)
        out = [re.escape(d.isoformat()), wd + month + r'\s+' + day + r',?\s+' + str(d.year),
               wd + day + r'\s+(?:of\s+)?' + month + r',?\s+' + str(d.year)]
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
    wrap = lambda p: _DatePattern(re.compile(r'(?<!\w)' + p + r'(?!\w)', re.I), start == end)
    if start == end:
        return [wrap(p) for p in single(start)]
    patterns = []
    for a in single(start):
        for b in single(end):
            patterns.append(a + r'.{0,35}?' + b)
    sep = r'\s*' + _RANGE_SEP + r'\s*'
    ws, we = _weekday_rx(start), _weekday_rx(end)
    ms, me = _month_rx(start.month), _month_rx(end.month)
    ds, de = _day_rx(start), _day_rx(end)
    if start.year == end.year and start.month == end.month:
        # "21 & 22 April 2027", "Wednesday, 21 and Thursday, 22 April 2027":
        # a joining word covers the whole range only when the days touch.
        seps = [sep] + ([r'\s*(?:&|and)\s*'] if (end - start).days == 1 else [])
        for j in seps:
            patterns += [ws + ms + r'\s+' + ds + j + we + de + r',?\s+' + str(start.year),
                         ws + ds + j + we + de + r'\s+' + ms + r',?\s+' + str(start.year)]
    if start.year == end.year:
        # "November 2 - November 6, 2026", "30 October - 2 November 2026",
        # "Monday 2 November - Friday 6 November 2026".
        patterns += [ws + ms + r'\s+' + ds + sep + we + me + r'\s+' + de + r',?\s+' + str(end.year),
                     ws + ds + r'\s+' + ms + sep + we + de + r'\s+' + me + r',?\s+' + str(end.year)]
    elif end.year == start.year + 1 and end.month < start.month:
        # "December 30 - January 2, 2027": the year is written once, on the
        # end, and the start is the December before it.
        patterns += [ws + ms + r'\s+' + ds + sep + we + me + r'\s+' + de + r',?\s+' + str(end.year),
                     ws + ds + r'\s+' + ms + sep + we + de + r'\s+' + me + r',?\s+' + str(end.year)]
    return [wrap(p) for p in patterns]


def _structured_date(value):
    if not isinstance(value, str):
        raise ValueError("Missing structured date")
    if len(value) == 10:
        return date.fromisoformat(value)
    if len(value) > 10 and value[10] == 'T':
        return datetime.fromisoformat(value.replace('Z', '+00:00')).date()
    raise ValueError("Invalid structured date")


_STRUCTURED_SEGMENTS = re.compile(r'\s+[|\u2013\u2014\u00b7\u2022-]\s+|:\s+|,\s+')


def _key(name):
    """A folded name with its spacing removed: "Money 20/20" is "Money20/20"."""
    return re.sub(r'\s+', '', name)


def _structured_name_matches(row_name, names, year):
    """Is this schema.org Event node THIS event?

    Organizers routinely write the node's name the way they write a page
    title: "Fintech Meetup | Leading Fintech Event | Networking & Innovation",
    "Web Summit, Lisbon". The dates on that node are the organizer's own, so
    the name only has to lead it, and nothing after it may name another
    gathering ("SaaStr Annual | CMO Summit" is not SaaStr Annual's node),
    tie it to one, or name an edition the event's name does not carry:
    an audit on 2026-09-30 found "Web Summit | Vancouver", "Money20/20,
    Europe" and "SaaStr Annual: Virtual Edition" all admitting the plain
    name with that edition's dates."""
    keys = {_key(n) for n in names}
    if {_key(n) for n in _names({'name': row_name}, year)} & keys:
        return True
    segments = [x for x in _STRUCTURED_SEGMENTS.split(row_name) if x.strip()]
    if len(segments) < 2 or not {_key(n) for n in _names({'name': segments[0]}, year)} & keys:
        return False
    rest_words = _fold(' '.join(segments[1:])).split()
    rest = set(rest_words)
    # "and" alone joins words in a tagline ("Networking & Innovation"); it only
    # ties two gatherings together when one of them is named, which the event
    # words already catch.
    if rest & (_EVENT_WORDS | (_TIE_WORDS - {'and'}) | {'edition'}):
        return False
    if _edition_place(rest_words, names):
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
_TITLE_SEGMENTS = re.compile(r'\s+[|\u00b7\u2022]\s+|:\s+|\s+[-\u2010-\u2014\u2212]\s+')
_RANGE_LEFT = re.compile(r'(?:\b\d{1,2}(?:st|nd|rd|th)?|\b(?:' + '|'.join(sorted(_MONTH_NAMES, key=len, reverse=True)) + r')\.?)$', re.I)
_RANGE_RIGHT = re.compile(r'(?:\d|(?:' + '|'.join(sorted(_MONTH_NAMES | _WEEKDAYS, key=len, reverse=True)) + r')\b)', re.I)
_SEPARATOR_MARKS = ('|', ':', '\u00b7', '\u2022')


class _TitleSplit:
    # A spaced dash between two parts of a date is a range, not a
    # separator: "8 - 10 June 2027", and since the 2026-09-30 audit also
    # "30 October - 2 November 2026" and "December 30 - January 2, 2027",
    # which the old digit-only rule cut in two. A year before the dash is
    # not a day: "The Phocuswright Conference 2026 - November 17-19".
    @staticmethod
    def split(title):
        out, last = [], 0
        for m in _TITLE_SEGMENTS.finditer(title):
            if (m.group(0).strip() not in _SEPARATOR_MARKS
                    and _RANGE_LEFT.search(title[last:m.start()])
                    and _RANGE_RIGHT.match(title, m.end())):
                continue
            out.append(title[last:m.start()])
            last = m.end()
        return out + [title[last:]]
_TITLE_LEAD = {'attend', 'join', 'register', 'for', 'welcome', 'to', 'the',
               'official', 'home', 'us'}
_TITLE_NEXT = {'in', 'on', 'is', 'returns', 'from', 'will', 'takes', 'taking'}
_TITLE_FOREIGN = {'at', 'during', 'with', 'alongside', 'within', 'part',
                  'powered', 'presented', 'by', 'and', 'x'}


def _title_owns(segment, names):
    """The words after the name, if this segment is led by the name."""
    folded = _fold(segment)
    for name in names:
        m = re.search(_name_re(name), folded)
        if m and set(folded[:m.start()].split()) <= _TITLE_LEAD:
            return folded[m.end():].split()
    return None


# Phrases that tie a title to another event wherever they sit in it.
_TIE_PHRASE = re.compile(r'(?<!\w)(?:part of|powered by|presented by|hosted by|organi[sz]ed by|'
                         r'brought to you by|co located|colocated|in partnership|in association|'
                         r'alongside|during|within|returns to|comes to|coming to)(?!\w)')


def _foreign_segment(segment, names):
    """A title segment that ties the event to another one or names an
    edition. "CMO Summit | September 9-11, 2026 | at SaaStr Annual" (audit,
    2026-09-30) was admitted because only the first two segments were read."""
    words = _fold(segment).split()
    if not words:
        return False
    if words[0] == 'at' and _place_ok('at', words[1:], names):
        return False  # "at Moscone Center" is where, not whose.
    if words[0] in _TITLE_FOREIGN or _edition_place(words, names) or 'edition' in words:
        return True
    tie = _TIE_PHRASE.search(' '.join(words))
    if not tie:
        return False
    # "returns to Las Vegas" is a place; "returns to SaaStr Annual" is not.
    after = ' '.join(words)[tie.end():].split()
    if tie.group(0) in ('returns to', 'comes to', 'coming to'):
        return not _place_ok('to', after[:5], names) or len(after) > 5
    return True


def _title_support(titles, names, start, end):
    for title in titles or []:
        segments = [x for x in _TitleSplit.split(title) if x.strip()]
        if not segments:
            continue
        tail = _title_owns(segments[0], names)
        if tail is None:
            continue
        pinned = False
        if tail and re.fullmatch(r'20\d{2}', tail[0]):
            if tail[0] != str(start.year):
                continue  # "Money20/20 USA 2025" is another edition.
            tail = tail[1:]
            pinned = True
        months = _MONTH_NAMES
        if tail and not (tail[0] in _TITLE_NEXT or tail[0] in months or tail[0].isdigit()):
            continue
        # Between the name and the dates, nothing that introduces another
        # event: the same connecting-copy grammar as the body, so "CMO Summit
        # returns to SaaStr Annual | September 9-11, 2026" is refused.
        head = []
        for w in tail:
            if w in months or w.isdigit():
                break
            head.append(w)
        if (set(head) & _TITLE_FOREIGN or not _connecting(head, names, start.year)
                or any(y != str(start.year) for y in re.findall(r'\b20\d{2}\b', ' '.join(head)))):
            continue
        for pattern in _date_patterns(start, end, title):
            found = pattern.search(segments[0])
            if found:
                dated = 0
                # "CMO Summit, September 9-11, 2026 at SaaStr Annual".
                if _foreign_segment(segments[0][found.end():], names):
                    continue
            elif len(segments) > 1:
                second = segments[1]
                # "Hong Kong FinTech Week x StartmeupHK 2026 | Nov 2-6"
                # (fintechweek.hk, 2026-09-30): the name segment gives the
                # edition's year, the next one only the days. The year is
                # lent only to a segment that has none of its own.
                if pinned and not re.search(r'\b20\d{2}\b', second):
                    second = second + ' ' + str(start.year)
                rest = pattern.sub('', second)
                if not (pattern.search(second) and not re.search(r'\w', rest)):
                    continue
                dated = 1
            else:
                continue
            # Every other segment is read too.
            if any(_foreign_segment(x, names) for i, x in enumerate(segments) if i not in (0, dated)):
                continue
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
    r"(?P<noun>event|conference|summit|expo|show|forum|festival|convention|congress|exhibition)\s+"
    r"(?:takes\s+place|will\s+take\s+place|is\s+taking\s+place|is\s+held|will\s+be\s+held|"
    r"runs|returns|happens|comes\s+to|lands\s+in)\b", re.I)
# "by", "within" and "x" tie the event to another as surely as "during"
# does (audit, 2026-09-30). "at" is read separately: Nacha's own sentence
# is "takes place at the Gaylord National Harbor Resort & Convention
# Center", and "takes place at SaaStr Annual" is another event's dates.
_SELF_REF_FOREIGN = {'during', 'alongside', 'with', 'part', 'co', 'colocated',
                     'located', 'ahead', 'before', 'after', 'following', 'by',
                     'within', 'x'}
# A proper name ending in an event word: "the CMO Summit", "Women in SaaS
# Breakfast". A lone determiner is not a name: "The Summit takes place".
_NAMED_EVENT = re.compile(
    r"\b(?!(?:The|This|Our|Each|Every|An?|Its|Their)\b)[A-Z][\w'&/-]*"
    r"(?:\s+(?:[A-Z0-9][\w'&/-]*|of|in|for|and|&|on|the))*\s+"
    r"(?:" + '|'.join(w.capitalize() for w in sorted(_EVENT_WORDS - {'day', 'days'})) + r")\b")


def _title_identifies(titles, names, year):
    for title in titles or []:
        if any(y != str(year) for y in re.findall(r'\b20\d{2}\b', title)):
            continue
        segments = [x for x in _TitleSplit.split(title) if x.strip()]
        tail = _title_owns(segments[0], names) if segments else None
        if tail and tail[0] == str(year):
            tail = tail[1:]  # "MRC Vegas 2027" is the plainest title there is.
        if tail is None or not (not tail or (len(tail) == 1 and tail[0] in _SELF_NOUNS)):
            continue
        # "SaaStr Annual: Virtual Edition" names an edition, not the event.
        if any(_foreign_segment(x, names) for x in segments[1:]):
            continue
        return title
    return None


def _title_noun(title, names, year):
    """The generic noun the title gives the event, if any: "Conference" in
    "Smarter Faster Payments Conference", "summit" in "CMO Summit"."""
    tail = [w for w in (_title_owns(_TitleSplit.split(title)[0], names) or []) if w != str(year)]
    if tail and tail[0] in _OWN_NOUNS:
        return tail[0]
    last = names[0].split()[-1] if names and names[0].split() else ''
    return last if last in _OWN_NOUNS else None


def _names_other_event(text, names):
    for m in _NAMED_EVENT.finditer(text):
        if not _names_any(names, _fold(m.group(0))):
            return True
    return False


def _self_referenced_dates(text, start, end, names=(), noun=None):
    """The sentence that dates "the event" on a page whose title names it.

    Refused (audit, 2026-09-30) unless:
      * the sentence's noun agrees with the title's ("The conference takes
        place ..." on a page titled "CMO Summit" is the parent conference),
        or is plain "event";
      * neither this sentence nor the one before names another event
        ("Join us for the CMO Summit. The summit takes place ..." on
        "SaaStr Annual 2026");
      * nothing between the verb and the dates ties it to another event or
        names an edition ("takes place online" is the virtual edition)."""
    sentences = re.split(r'[.!?](?=\s+[A-Z]|\s*$)', text)
    for index, sentence in enumerate(sentences):
        ref = _SELF_REF.search(sentence)
        if not ref:
            continue
        said = ref.group('noun').casefold()
        if noun and said not in ('event', noun):
            continue
        # The previous sentence is read only near its end: on Nacha's page
        # it is a navigation run with no full stop, and its "15 Under 40
        # Awards" menu item 500 characters back is not what "our event"
        # refers to.
        before = (sentences[index - 1][-160:] if index else '') + ' ' + sentence[:ref.start()]
        if _names_other_event(before, names):
            continue
        for pattern in _date_patterns(start, end, sentence):
            m = pattern.search(sentence, ref.end())
            if not m:
                continue
            between = _fold(sentence[ref.end():m.start()]).split()
            if set(between) & _SELF_REF_FOREIGN or _edition_place(between, names):
                continue
            if 'at' in between and not _place_ok('at', between[between.index('at')+1:][:8], names):
                continue
            if _names_other_event(sentence[ref.end():m.start()], names):
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
        # Link destinations are provenance, not visible evidence of names,
        # dates or access: "Join [https://.../waitlist]" is a link, and an
        # audit on 2026-09-30 found it blocking a JSON-LD-admitted page
        # because the structured branch read the raw text.
        visible = re.sub(r'\[https?://[^\]\s]+\]', '', raw_text)
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
            _, blocked = _access(visible, str(event.get('name') or ''), (start, end))
            if blocked:
                restrictions.append('The organizer page describes restricted or unavailable access; verify this client’s access before recommending attendance.')
            # fetch_page returns a JavaScript-rendered page as status
            # 'blocked' with NO text at all, so its JSON-LD admitted it
            # clean: the access check above ran on nothing (audit,
            # 2026-09-30). Dates from metadata are fine; "nothing restricts
            # access" cannot be concluded from a page nobody read.
            if not visible.strip():
                restrictions.append('The organizer page text could not be read, so access could not be verified; confirm registration is open before recommending attendance.')
        # WordPress/React markers alone also occur on fully readable pages.
        if fetched.get('status') != 'ok' or fallback or fetched.get('truncated'):
            check['reason'] = 'The complete rendered page could not be read reliably.'
            continue
        text = re.sub(r'\s+', ' ', visible).strip()
        check['visible_text_snapshot'] = source_snapshot(final,text)
        observations, blocked = _access(visible,str(event.get('name') or ''),(start,end))
        check['access_observations'] = observations
        if blocked:
            restrictions.append('The organizer page describes restricted or unavailable access; verify this client’s access before recommending attendance.')
            check['access_excerpt'] = blocked[0]
        for pattern in _date_patterns(start, end, text):
            for match in pattern.finditer(text):
                context = text[max(0,match.start()-180):match.end()+180]
                prefix = text[max(0,match.start()-180):match.start()]
                if _owns_date(prefix,names,start.year,fetched.get('titles')):
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
            sentence = title and _self_referenced_dates(
                text, start, end, names, _title_noun(title, names, start.year))
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
