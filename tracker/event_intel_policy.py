"""Deterministic admission rules shared by discovery and replacement paths."""
import calendar
import re
from datetime import date
from urllib.parse import urlparse

# Countries by the names people and event pages give them. Every value is one
# canonical country; a region below is a set of these.
_COUNTRY_ALIASES = {
    'usa': ('united states of america', 'united states', 'usa', 'u.s.a.', 'u.s.a', 'u.s.', 'us', 'america'),
    'canada': ('canada',), 'mexico': ('mexico', 'méxico'),
    'uk': ('united kingdom', 'great britain', 'britain', 'england', 'scotland', 'wales',
           'northern ireland', 'u.k.', 'uk'),
    'ireland': ('ireland', 'republic of ireland'),
    'germany': ('germany', 'deutschland'), 'france': ('france',), 'netherlands': ('netherlands', 'holland', 'the netherlands'),
    'belgium': ('belgium',), 'luxembourg': ('luxembourg',), 'spain': ('spain', 'españa'),
    'portugal': ('portugal',), 'italy': ('italy', 'italia'), 'switzerland': ('switzerland',),
    'austria': ('austria', 'österreich'), 'sweden': ('sweden',), 'denmark': ('denmark',),
    'norway': ('norway',), 'finland': ('finland',), 'iceland': ('iceland',),
    'poland': ('poland',), 'czechia': ('czechia', 'czech republic'), 'slovakia': ('slovakia',),
    'hungary': ('hungary',), 'romania': ('romania',), 'bulgaria': ('bulgaria',),
    'greece': ('greece',), 'croatia': ('croatia',), 'slovenia': ('slovenia',), 'serbia': ('serbia',),
    'estonia': ('estonia',), 'latvia': ('latvia',), 'lithuania': ('lithuania',),
    'malta': ('malta',), 'cyprus': ('cyprus',), 'monaco': ('monaco',), 'ukraine': ('ukraine',),
    'turkey': ('turkey', 'türkiye', 'turkiye'),
    'uae': ('united arab emirates', 'uae', 'u.a.e.', 'emirates'),
    'saudi arabia': ('saudi arabia', 'ksa', 'saudi'), 'qatar': ('qatar',), 'bahrain': ('bahrain',),
    'kuwait': ('kuwait',), 'oman': ('oman',), 'israel': ('israel',), 'jordan': ('jordan',),
    'egypt': ('egypt',), 'lebanon': ('lebanon',), 'morocco': ('morocco',),
    'south africa': ('south africa',), 'nigeria': ('nigeria',), 'kenya': ('kenya',),
    'ghana': ('ghana',), 'rwanda': ('rwanda',), 'ethiopia': ('ethiopia',), 'tunisia': ('tunisia',),
    'india': ('india',), 'pakistan': ('pakistan',), 'bangladesh': ('bangladesh',), 'sri lanka': ('sri lanka',),
    'singapore': ('singapore',), 'malaysia': ('malaysia',), 'indonesia': ('indonesia',),
    'thailand': ('thailand',), 'vietnam': ('vietnam', 'viet nam'), 'philippines': ('philippines',),
    'japan': ('japan',), 'china': ('china', "people's republic of china", 'prc'),
    'hong kong': ('hong kong', 'hong kong sar'), 'taiwan': ('taiwan',), 'macau': ('macau', 'macao'),
    'south korea': ('south korea', 'korea', 'republic of korea'),
    'australia': ('australia',), 'new zealand': ('new zealand',),
    'brazil': ('brazil', 'brasil'), 'argentina': ('argentina',), 'chile': ('chile',),
    'colombia': ('colombia',), 'peru': ('peru',), 'uruguay': ('uruguay',),
    'costa rica': ('costa rica',), 'panama': ('panama',), 'puerto rico': ('puerto rico',),
}
_EU = {'germany', 'france', 'netherlands', 'belgium', 'luxembourg', 'spain', 'portugal', 'italy',
       'austria', 'sweden', 'denmark', 'finland', 'poland', 'czechia', 'slovakia', 'hungary',
       'romania', 'bulgaria', 'greece', 'croatia', 'slovenia', 'estonia', 'latvia', 'lithuania',
       'malta', 'cyprus', 'ireland'}
_EUROPE = _EU | {'uk', 'switzerland', 'norway', 'iceland', 'serbia', 'monaco', 'ukraine', 'turkey'}
_MIDDLE_EAST = {'uae', 'saudi arabia', 'qatar', 'bahrain', 'kuwait', 'oman', 'israel', 'jordan',
                'egypt', 'lebanon', 'turkey'}
_AFRICA = {'south africa', 'nigeria', 'kenya', 'ghana', 'rwanda', 'ethiopia', 'egypt', 'morocco', 'tunisia'}
_APAC = {'india', 'pakistan', 'bangladesh', 'sri lanka', 'singapore', 'malaysia', 'indonesia',
         'thailand', 'vietnam', 'philippines', 'japan', 'china', 'hong kong', 'taiwan', 'macau',
         'south korea', 'australia', 'new zealand'}
_LATAM = {'mexico', 'brazil', 'argentina', 'chile', 'colombia', 'peru', 'uruguay', 'costa rica',
          'panama', 'puerto rico'}
# Longest first when read, so "north america" is taken before "america".
_REGIONS = {
    'north america': {'usa', 'canada', 'mexico'}, 'americas': {'usa', 'canada'} | _LATAM,
    'latin america': _LATAM, 'latam': _LATAM, 'south america': _LATAM - {'mexico', 'costa rica', 'panama', 'puerto rico'},
    'europe': _EUROPE, 'european union': _EU, 'eu': _EU, 'western europe': _EUROPE,
    'dach': {'germany', 'austria', 'switzerland'}, 'benelux': {'belgium', 'netherlands', 'luxembourg'},
    'nordics': {'sweden', 'denmark', 'norway', 'finland', 'iceland'},
    'nordic': {'sweden', 'denmark', 'norway', 'finland', 'iceland'},
    'scandinavia': {'sweden', 'denmark', 'norway'},
    'uk&i': {'uk', 'ireland'}, 'uki': {'uk', 'ireland'}, 'uk and ireland': {'uk', 'ireland'},
    'middle east': _MIDDLE_EAST, 'mena': _MIDDLE_EAST | {'morocco', 'tunisia'},
    'gcc': {'uae', 'saudi arabia', 'qatar', 'bahrain', 'kuwait', 'oman'}, 'gulf': {'uae', 'saudi arabia', 'qatar', 'bahrain', 'kuwait', 'oman'},
    'africa': _AFRICA, 'emea': _EUROPE | _MIDDLE_EAST | _AFRICA,
    'apac': _APAC, 'asia-pacific': _APAC, 'asia pacific': _APAC, 'asia': _APAC - {'australia', 'new zealand'},
    'southeast asia': {'singapore', 'malaysia', 'indonesia', 'thailand', 'vietnam', 'philippines'},
    'sea': {'singapore', 'malaysia', 'indonesia', 'thailand', 'vietnam', 'philippines'},
    'anz': {'australia', 'new zealand'}, 'oceania': {'australia', 'new zealand'},
    'greater china': {'china', 'hong kong', 'taiwan', 'macau'},
}
# Where events are held, for an event that gives a city and no country.
_CITIES = {
    'usa': ('las vegas', 'new york', 'nyc', 'san francisco', 'san diego', 'los angeles', 'chicago',
            'boston', 'austin', 'dallas', 'houston', 'atlanta', 'orlando', 'miami', 'miami beach',
            'denver', 'seattle', 'washington', 'national harbor', 'nashville', 'new orleans',
            'philadelphia', 'phoenix', 'scottsdale', 'anaheim', 'san jose', 'san mateo', 'santa clara',
            'palo alto', 'salt lake city', 'minneapolis', 'detroit', 'indianapolis', 'baltimore',
            'charlotte', 'kansas city', 'st. louis', 'portland', 'honolulu', 'pittsburgh', 'tampa'),
    'canada': ('toronto', 'vancouver', 'montreal', 'montréal', 'calgary', 'ottawa'),
    'mexico': ('mexico city', 'cancun', 'cancún', 'monterrey', 'guadalajara'),
    'uk': ('london', 'manchester', 'birmingham', 'edinburgh', 'glasgow', 'liverpool', 'leeds', 'bristol'),
    'ireland': ('dublin',), 'germany': ('berlin', 'munich', 'münchen', 'frankfurt', 'hamburg', 'cologne',
                                        'köln', 'düsseldorf', 'dusseldorf', 'stuttgart', 'hanover', 'hannover',
                                        'nuremberg', 'nürnberg', 'leipzig', 'essen'),
    'france': ('paris', 'cannes', 'nice', 'lyon', 'marseille', 'monaco'),
    'netherlands': ('amsterdam', 'rotterdam', 'utrecht', 'the hague'), 'belgium': ('brussels', 'antwerp'),
    'spain': ('barcelona', 'madrid', 'valencia', 'malaga', 'málaga', 'seville', 'bilbao'),
    'portugal': ('lisbon', 'lisboa', 'porto'), 'italy': ('milan', 'milano', 'rome', 'roma', 'turin', 'bologna', 'florence'),
    'switzerland': ('zurich', 'zürich', 'geneva', 'basel', 'davos', 'lausanne'), 'austria': ('vienna', 'wien', 'salzburg'),
    'sweden': ('stockholm', 'gothenburg', 'malmö', 'malmo'), 'denmark': ('copenhagen',), 'norway': ('oslo',),
    'finland': ('helsinki',), 'poland': ('warsaw', 'krakow', 'kraków'), 'czechia': ('prague',),
    'hungary': ('budapest',), 'greece': ('athens',), 'estonia': ('tallinn',), 'lithuania': ('vilnius',),
    'latvia': ('riga',), 'romania': ('bucharest',), 'croatia': ('zagreb',), 'turkey': ('istanbul', 'ankara'),
    'uae': ('dubai', 'abu dhabi', 'sharjah'), 'saudi arabia': ('riyadh', 'jeddah'), 'qatar': ('doha',),
    'bahrain': ('manama',), 'israel': ('tel aviv', 'jerusalem'), 'egypt': ('cairo',),
    'south africa': ('cape town', 'johannesburg', 'durban'), 'kenya': ('nairobi',), 'nigeria': ('lagos',),
    'morocco': ('marrakech', 'casablanca'), 'rwanda': ('kigali',),
    'india': ('mumbai', 'bangalore', 'bengaluru', 'new delhi', 'delhi', 'hyderabad', 'chennai', 'pune', 'gurgaon', 'gurugram', 'noida'),
    'singapore': ('singapore',), 'malaysia': ('kuala lumpur',), 'indonesia': ('jakarta', 'bali'),
    'thailand': ('bangkok',), 'vietnam': ('ho chi minh city', 'hanoi'), 'philippines': ('manila',),
    'japan': ('tokyo', 'osaka', 'yokohama', 'kyoto'), 'china': ('shanghai', 'beijing', 'shenzhen', 'guangzhou'),
    'hong kong': ('hong kong',), 'taiwan': ('taipei',), 'south korea': ('seoul', 'busan'),
    'australia': ('sydney', 'melbourne', 'brisbane', 'perth', 'adelaide', 'gold coast'),
    'new zealand': ('auckland', 'wellington'), 'brazil': ('são paulo', 'sao paulo', 'rio de janeiro', 'rio'),
    'argentina': ('buenos aires',), 'chile': ('santiago',), 'colombia': ('bogotá', 'bogota', 'medellín', 'medellin'),
    'peru': ('lima',),
}
_US_STATES = {
    'al', 'ak', 'az', 'ar', 'ca', 'co', 'ct', 'de', 'fl', 'ga', 'hi', 'id', 'il', 'in', 'ia', 'ks', 'ky',
    'la', 'me', 'md', 'ma', 'mi', 'mn', 'ms', 'mo', 'mt', 'ne', 'nv', 'nh', 'nj', 'nm', 'ny', 'nc', 'nd',
    'oh', 'ok', 'or', 'pa', 'ri', 'sc', 'sd', 'tn', 'tx', 'ut', 'vt', 'va', 'wa', 'wv', 'wi', 'wy', 'dc',
    'alabama', 'alaska', 'arizona', 'arkansas', 'california', 'colorado', 'connecticut', 'delaware',
    'florida', 'georgia', 'hawaii', 'idaho', 'illinois', 'indiana', 'iowa', 'kansas', 'kentucky',
    'louisiana', 'maine', 'maryland', 'massachusetts', 'michigan', 'minnesota', 'mississippi',
    'missouri', 'montana', 'nebraska', 'nevada', 'new hampshire', 'new jersey', 'new mexico',
    'north carolina', 'north dakota', 'ohio', 'oklahoma', 'oregon', 'pennsylvania', 'rhode island',
    'south carolina', 'south dakota', 'tennessee', 'texas', 'utah', 'vermont', 'virginia',
    'west virginia', 'wisconsin', 'wyoming'}
# Words in a scope that say how much, not where.
_SCOPE_NOISE = re.compile(
    r'\b(?:mainly|mostly|primarily|predominantly|largely|chiefly|focus(?:ed)?|on|only|the|based|'
    r'in|events?|conferences?|region|regions|market|markets|and|or|plus|with|some|also|'
    r'including|incl|e\.g|across|wide|first|then)\b')
_GLOBAL_SCOPE = ('global', 'worldwide', 'anywhere', 'international', 'any region', 'all regions')


def _has(phrase, text):
    return re.search(r'(?<![\w.])' + re.escape(phrase) + r'(?![\w])', text) is not None


def scope_countries(scope):
    """The countries a client's geographic scope covers, or None for global.

    Read for what it says, not matched as literal words: "Mainly US",
    "Primarily the United States", "USA & Canada", "DACH", "GCC" and "EMEA"
    each rejected every real event before (Recommend audit, 2026-10-04)."""
    text = ' ' + re.sub(r'\s+', ' ', str(scope or '').lower().replace('&', ' and ')) + ' '
    if any(_has(g, text) for g in _GLOBAL_SCOPE):
        return None
    out = set()
    for region in sorted(_REGIONS, key=len, reverse=True):
        if _has(region, text):
            out |= _REGIONS[region]
            text = re.sub(r'(?<![\w.])' + re.escape(region) + r'(?![\w])', ' ', text)
    for country, names in _COUNTRY_ALIASES.items():
        if any(_has(n, text) for n in names):
            out.add(country)
    if any(_has(s, text) for s in _US_STATES if len(s) > 2):
        out.add('usa')
    return out


def event_country(event):
    """The country an event is held in, from its country, else its city or
    location line; None when nothing names one."""
    for key in ('country', 'city', 'location'):
        text = ' ' + str(event.get(key) or '').lower() + ' '
        if not text.strip():
            continue
        for country, names in _COUNTRY_ALIASES.items():
            # "US" or "America" inside a longer place name is not the country.
            if any(_has(n, text) for n in names if n not in ('us', 'america') or key == 'country'):
                return country
        for country, cities in _CITIES.items():
            if any(_has(c, text) for c in cities):
                return country
        m = re.search(r',\s*([a-z]{2}|[a-z][a-z ]+?)\s*(?:\d{5})?\s*$', text.strip())
        if m and m.group(1) in _US_STATES:
            return 'usa'
    return None


def eligibility(event, profile, today=None):
    """Return reasons requiring verification; never silently admit unknowns.

    One side effect, on purpose: an edition that already ended, and is
    otherwise valid, is marked `event["finished"] = True` and returns no
    date reason (see below). Genuinely out-of-window future dates and
    impossible ranges are still refused.
    """
    from .event_intel_discover import _excluded
    today = today or date.today()
    reasons = []
    availability = event.get('availability')
    if availability == 'cancelled':
        reasons.append('The organizer reports this edition is cancelled.')
    if availability == 'sold_out':
        from .event_intel_discover import committed_keys, is_committed_same_edition
        # is_committed_same_edition, not is_committed: a client's own real
        # commitment must agree on region before it waives this specific
        # sold-out warning, or "MarTech Summit" (no region) would clear a
        # sold-out "MarTech Summit Europe" they never actually committed to.
        if not is_committed_same_edition(event.get('name') or '', committed_keys(profile.get('force_include'))):
            reasons.append('This edition is sold out; access must be resolved before recommending attendance.')
    if _excluded(event.get('name') or '', profile.get('force_exclude'), event):
        reasons.append('This event is on the client exclusion list.')
    try:
        start = date.fromisoformat(str(event.get('starts_on') or '')[:10])
        end = date.fromisoformat(str(event.get('ends_on') or event.get('starts_on') or '')[:10])
        months = int(profile.get('window_months') or 12)
        month = today.month - 1 + months
        year, month = today.year + month // 12, month % 12 + 1
        last = date(year, month, min(today.day, calendar.monthrange(year, month)[1]))
        if end < start or start > last:
            reasons.append('The edition is outside the requested date window or has invalid dates.')
        elif end < today:
            # Over, and otherwise valid: flagged and let through rather than
            # refused. Refusing it here meant rank() never saw it, so the
            # report's "Already over" section could never fill, and a real
            # edition that had just happened was shown under "Not scored" as
            # "outside the requested date window", which reads as a gap in
            # the search. rubric.has_finished reads the same dates and puts it
            # in `finished`; the flag says so to anything before ranking.
            event['finished'] = True
    except (ValueError, TypeError):
        reasons.append('The edition dates need confirmation.')
    allowed = scope_countries(profile.get('geo_scope'))
    if allowed is not None:
        country = event_country(event)
        scope_text = str(profile.get('geo_scope') or '').lower()
        # A place the scope names directly ("Las Vegas only") still counts.
        named = any(_has(str(event.get(k) or '').lower().strip(), ' %s ' % scope_text)
                    for k in ('city',) if str(event.get(k) or '').strip())
        if not allowed:
            # A scope none of the tables above knows ("Bay Area"): its own
            # words, matched against where the event is, as before.
            words = [w.strip() for w in re.split(r'[,;/]', _SCOPE_NOISE.sub(' ', scope_text))]
            location = ' %s ' % ' '.join(str(event.get(k) or '') for k in
                                         ('country', 'city', 'location')).lower()
            named = named or any(w and _has(w, location) for w in words)
        if not named and (not country or country not in allowed):
            reasons.append('The location could not be verified against the client geography.')
    website = urlparse(str(event.get('website') or ''))
    host = (website.hostname or '').lower().removeprefix('www.')
    source_hosts = [(urlparse(str(s)).hostname or '').lower().removeprefix('www.')
                    for s in event.get('sources') or [] if isinstance(s, str)]
    if website.scheme not in ('http','https') or not host or not any(
            s == host or s.endswith('.' + host) for s in source_hosts):
        reasons.append('An event-site source is required to verify this edition.')
    if event.get('confidence') not in ('high','medium'):
        reasons.append('The event identity has insufficient confidence.')
    return reasons


def intake_errors(profile):
    missing = [label for key, label in [('buyer_roles','buyer roles'),('verticals','target verticals'),
                                    ('geo_scope','geographic scope'),('website','client website')]
            if not str(profile.get(key) or '').strip()]
    if not str(profile.get('selected_product') or profile.get('what_they_sell') or '').strip():
        missing.append('product or service to promote')
    return missing
