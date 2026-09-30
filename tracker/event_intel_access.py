"""Observed organizer links and explicit action constraints, not access promises."""
from datetime import date
from decimal import Decimal
import re
from urllib.parse import urlsplit

LINK_KINDS = {
    'registration': r'\b(register|registration|buy tickets?|book tickets?)\b',
    'exhibit': r'\b(become an exhibitor|exhibit with us|book a booth|exhibitor enquiry)\b',
    'sponsor': r'\b(become a sponsor|sponsorship opportunities|sponsor us|sponsorship enquiry)\b',
    'meetings': r'\b(book a meeting|schedule a meeting|matchmaking|meeting programme|meeting program)\b',
    'agenda': r'\b(agenda|conference programme|conference program|session schedule)\b',
}


# Hosts where many organizers each have a page. Being on the same host
# proves nothing there: an audit on 2026-09-30 found that an event whose
# website is an Eventbrite or Luma listing counted ANY other organizer's
# listing on that platform as its own organizer page. Only a shared
# platform's generic subdomains are shared; "acme.bizzabo.com" is Acme's.
_SHARED_LABELS = {'www', 'web', 'app', 'events', 'event', 'portal', 'next', 'my',
                  'go', 'm', 'register', 'tickets'}
# One platform, two hosts: lu.ma now redirects to luma.com.
_HOST_ALIASES = {'lu.ma': 'luma.com'}


def _platform_hosts():
    from .event_intel_harvest import _NON_COMPANY_HOSTS
    return set(_NON_COMPANY_HOSTS) | {'luma.com'}


def _shared_host(host):
    platforms = _platform_hosts()
    if host in platforms:
        return True
    label, _, rest = host.partition('.')
    return label in _SHARED_LABELS and rest in platforms


def organizer_url(url, host, website=None):
    """Is `url` on the organizer's own site?

    `host` is the website's hostname; a full website URL is also accepted
    in its place. On a shared platform host the URL must also sit at or
    under the website's own listing path ("/e/cmo-summit-123",
    "/o/acme-456"), and without that path nothing there is the organizer's."""
    try:
        if host and '://' in str(host):
            website, host = host, urlsplit(host).hostname
        parsed = urlsplit(url)
        target = (parsed.hostname or '').lower().removeprefix('www.')
        host = (host or '').lower().removeprefix('www.')
        target, host = _HOST_ALIASES.get(target, target), _HOST_ALIASES.get(host, host)
        if not (host and parsed.scheme in ('http', 'https') and not parsed.username
                and not parsed.password and parsed.port in (None, 80, 443)):
            return False
        if _shared_host(host):
            listing = urlsplit(website or '').path.rstrip('/') if website else ''
            path = parsed.path.rstrip('/')
            return bool(target == host and listing
                        and (path == listing or path.startswith(listing + '/')))
        return target == host or target.endswith('.'+host)
    except (ValueError, TypeError):
        return False


def discover(text, source_url, host):
    """Use existing linked page text; never invent URLs or fetch destinations."""
    if not organizer_url(source_url, host):
        return []
    found, seen = [], set()
    for match in re.finditer(r'([^\n\[\]]{1,120})\s*\[(https?://[^\]\s]+)\]', text):
        label, url = match.group(1).strip(), match.group(2)
        if not organizer_url(url, host):
            continue
        for kind, pattern in LINK_KINDS.items():
            if re.search(pattern, label, re.I) and (kind, url) not in seen:
                seen.add((kind, url))
                found.append(dict(kind=kind, label=label, url=url, source_url=source_url,
                                  support='observed_link_only'))
    return found[:30]


def assess(event, plan, matches, links):
    """Explain gaps for the chosen action. Never rank actions as proven ROI."""
    action = (plan or {}).get('action')
    if not action:
        return {'status':'choose_action', 'checks':['Choose and save an action to assess its evidence and constraints.']}
    if action == 'monitor':
        return {'status':'manual_follow_up', 'checks':['Considering a future edition does not schedule monitoring.']}
    blockers, gaps = [], []
    try:
        starts = date.fromisoformat(str(event.get('starts_on') or ''))
        if starts < date.today():
            blockers.append('This edition has already started; confirm whether any relevant access remains.')
    except ValueError:
        gaps.append('The edition date is not established.')
    if event.get('availability') in ('cancelled', 'sold_out'):
        blockers.append('The report marks this edition '+event['availability'].replace('_',' ')+'. Recheck with the organizer.')
    access = plan.get('access_status', 'unknown')
    if access == 'unavailable':
        blockers.append('You reported that access is unavailable.')
    elif access != 'confirmed':
        gaps.append('Confirm access for the chosen action with the organizer; a published link is not confirmation.')
    else:
        gaps.append('Access is user-reported; reconfirm terms and availability before committing.')
    budget, estimate = plan.get('planned_budget'), plan.get('estimated_total_cost')
    if budget is None or estimate is None:
        gaps.append('Enter both a budget and estimated total cost in the selected currency to compare affordability.')
    elif Decimal(estimate) > Decimal(budget):
        blockers.append('The estimated total cost exceeds the planned budget.')
    route = {'attend':'registration','exhibit':'exhibit','sponsor':'sponsor','meetings':'meetings'}.get(action)
    if route and not any(link['kind'] == route for link in links):
        gaps.append('No matching organizer access link was observed in the pages read; this does not prove none exists.')
    if action == 'meetings' and not any(m['timing'] == 'announced' for m in matches):
        gaps.append('No target account has current-edition roster support in this report.')
    if action == 'side_event':
        gaps.append('Verify venue, permissions, invitee interest and total delivery cost for the side event.')
    gaps.append('Review the written constraints and client fit; these are not automatically verified.')
    return {'status':'blocked_for_review' if blockers else 'needs_review', 'checks':blockers+gaps}
