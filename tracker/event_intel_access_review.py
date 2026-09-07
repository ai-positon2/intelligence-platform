"""Bounded organizer destination reads with quoted evidence, never inferred terms."""
import re
from .event_intel_access import organizer_url
from .event_intel_evidence import source_snapshot

MAX_DESTINATIONS = 4
FIELDS = {
    'price': r'(?:[$€£₹]\s*\d|\b(?:USD|EUR|GBP|INR|price|pricing|fee|fees)\b)',
    'deadline': r'\b(?:deadline|closes? on|ends? on|register by|early bird)\b',
    'eligibility': r'\b(?:eligible|eligibility|members only|invite.only|invitation.only|must be|qualification)\b',
    'availability': r'\b(?:sold out|registration (?:is )?(?:open|closed)|waitlist|cancelled|canceled|applications (?:open|closed))\b',
}


def inspect(links, host, edition, fetcher=None):
    from .event_intel_harvest import fetch_page
    fetcher = fetcher or fetch_page
    selected, seen = [], set()
    for link in links:
        url = link.get('url')
        if (link.get('kind') not in ('registration','exhibit','sponsor','meetings')
                or not organizer_url(url,host) or not organizer_url(link.get('source_url'),host)):
            continue
        if url not in seen:
            selected.append(link)
            seen.add(url)
    checks = []
    for link in selected[:MAX_DESTINATIONS]:
        url = link['url']
        try:
            fetched = fetcher(url)
        except Exception:
            fetched = {'status':'error','note':'Destination fetch failed.'}
        final = fetched.get('final_url') or url
        check = dict(url=url, kind=link['kind'], discovered_on=link['source_url'],
                     final_url=final, status=fetched.get('status','error'),
                     note=fetched.get('note') or '', edition=edition,
                     support='unverified', claims=[], truncated=bool(fetched.get('truncated')))
        if not organizer_url(final,host):
            check.update(status='blocked',note='Destination redirected outside the organizer host; its content was not used.')
        elif check['status'] == 'ok':
            text = fetched.get('text') or ''
            check['snapshot'] = source_snapshot(final,text)
            check['support'] = 'literal_destination_text'
            # Page years are not proof of edition ownership; expose all visible
            # non-copyright years and leave ownership unresolved for review.
            years = set()
            counts = {field:0 for field in FIELDS}
            for line in text.splitlines():
                line = line.strip()
                if 'copyright' in line.lower() or '©' in line:
                    continue
                years.update(re.findall(r'\b20\d{2}\b',line))
                if not 5 <= len(line) <= 500:
                    continue
                for field, pattern in FIELDS.items():
                    if counts[field] < 5 and re.search(pattern,line,re.I):
                        check['claims'].append({'field':field,'text':line,'source_url':final,
                                                'support':'literal_destination_text'})
                        counts[field] += 1
            check['observed_years'] = sorted(years)
            check['edition_status'] = 'requires_review'
            check['note'] = (check['note']+' Destination text was read; applicability to this edition, ticket tier and client remains unverified.').strip()
        checks.append(check)
    return {'checks':checks,'candidates':len(selected),'attempted':len(checks),
            'not_checked':max(0,len(selected)-len(checks)), 'limit':MAX_DESTINATIONS,
            'complete':False}
