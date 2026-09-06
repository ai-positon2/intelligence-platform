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


def organizer_url(url, host):
    try:
        parsed = urlsplit(url)
        target = (parsed.hostname or '').lower().removeprefix('www.')
        host = (host or '').lower().removeprefix('www.')
        return bool(host and parsed.scheme in ('http', 'https') and not parsed.username
                    and not parsed.password and parsed.port in (None, 80, 443)
                    and (target == host or target.endswith('.'+host)))
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
