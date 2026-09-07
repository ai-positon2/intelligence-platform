"""Conservative event identity. Matching suggestions is not database identity."""
import hashlib
import json
import re
import unicodedata
from datetime import date


def strict_name(value):
    value = unicodedata.normalize('NFKC', str(value or '')).casefold()
    return ' '.join(re.findall(r'\w+', value))


def event_key(event):
    """Keep geography and edition; undated events only have run-local memory.

    False negatives are preferable to applying another edition's decision.
    Canonical organizer aliases can be added later with verified mappings.
    """
    name = strict_name(event.get('name'))
    try:
        edition_date = date.fromisoformat(str(event.get('starts_on') or '')[:10]).isoformat()
    except ValueError:
        edition_date = None
    if not edition_date:
        scope = ['unverified', event.get('run_id'), event.get('id'), name,
                 strict_name(event.get('country')), strict_name(event.get('city') or event.get('location'))]
    else:
        scope = [re.sub(r'\b20\d{2}\b', '', name).strip(), edition_date,
                 strict_name(event.get('country')), strict_name(event.get('city') or event.get('location'))]
    return 'evi1:' + hashlib.sha256(json.dumps(scope).encode()).hexdigest()[:32]
