"""Deterministic admission rules shared by discovery and replacement paths."""
import calendar
import re
from datetime import date
from urllib.parse import urlparse

_REGIONS = {
    'north america': {'usa', 'canada', 'mexico'},
    'europe': {'uk', 'germany', 'france', 'netherlands', 'spain', 'italy', 'sweden',
               'denmark', 'finland', 'norway', 'switzerland', 'austria', 'ireland',
               'belgium', 'poland', 'portugal', 'czechia', 'greece'},
    'apac': {'india', 'singapore', 'australia', 'japan', 'china', 'south korea',
             'indonesia', 'malaysia', 'thailand', 'vietnam', 'new zealand'},
}


def _geo(text):
    text = str(text or '').lower()
    for pattern, value in [(r'\bunited states(?: of america)?\b|\bu\.?s\.?a?\.?\b', 'usa'),
                           (r'\bunited kingdom\b|\bu\.?k\.?\b|\bbritain\b', 'uk')]:
        text = re.sub(pattern, value, text)
    return text


def eligibility(event, profile, today=None):
    """Return reasons requiring verification; never silently admit unknowns."""
    from .event_intel_discover import _excluded
    today = today or date.today()
    reasons = []
    availability = event.get('availability')
    if availability == 'cancelled':
        reasons.append('The organizer reports this edition is cancelled.')
    if availability == 'sold_out':
        from .event_intel_discover import committed_keys, is_committed
        if not is_committed(event.get('name') or '', committed_keys(profile.get('force_include'))):
            reasons.append('This edition is sold out; access must be resolved before recommending attendance.')
    if _excluded(event.get('name') or '', profile.get('force_exclude')):
        reasons.append('This event is on the client exclusion list.')
    try:
        start = date.fromisoformat(str(event.get('starts_on') or '')[:10])
        end = date.fromisoformat(str(event.get('ends_on') or event.get('starts_on') or '')[:10])
        months = int(profile.get('window_months') or 12)
        month = today.month - 1 + months
        year, month = today.year + month // 12, month % 12 + 1
        last = date(year, month, min(today.day, calendar.monthrange(year, month)[1]))
        if end < start or end < today or start > last:
            reasons.append('The edition is outside the requested date window or has invalid dates.')
    except (ValueError, TypeError):
        reasons.append('The edition dates need confirmation.')
    scope = _geo(profile.get('geo_scope'))
    location = _geo(' '.join(str(event.get(k) or '') for k in ('country','city','location')))
    if not any(x in scope for x in ('global', 'worldwide', 'anywhere', 'international')):
        allowed = set()
        for region, countries in _REGIONS.items():
            if region in scope:
                allowed.update(countries)
        words = re.split(r'[,;/]|\band\b|\bor\b', scope)
        for w in words:
            w = re.sub(r'\b(only|the|based|in|events|conferences)\b', '', w).strip()
            if w:
                allowed.add(w)
        if not location or not any(re.search(r'(?<!\w)' + re.escape(w) + r'(?!\w)', location) for w in allowed):
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
