"""Paid Marketing Agents: Ad Copy Checker, what the team saved (no network).

The sheet asks for phone number and location checks "against the Client Master Config". That config is not built yet, so until it is this keeps the four things the checks need, for the
whole team, in the monitors' store (tracker/paid_monitor_store.py, the value "settings:copycheck"):

  per account   phones   the numbers the account's ads may carry (any ad with another number is a high finding)
                places   where the account's clients are ("Quincy, MA"): copy that names a place saved for another account is flagged
                brand    the way the client writes its name ("SmileCo"): copy that writes it another way is flagged
                allow    words the dictionary does not know that are right for this account (a product, a person)
  for all       allow    the same, for every account

Each change says who made it and when. The store is any object with state_get and state_set, so tests run on a dict.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from . import paid_copycheck_rules as rules

KEY = "settings:copycheck"
MAX_PHONES, MAX_PLACES, MAX_BRANDS, MAX_ALLOW = 20, 50, 10, 500
_PLACE = re.compile(r"^[A-Za-z][A-Za-z .,'&-]{1,58}$")
_WORD = re.compile(r"^[A-Za-z][A-Za-z'-]{1,39}$")


class BadRequest(ValueError):
    pass


def empty():
    return {"accounts": {}, "allow": [], "changes": []}


def load(store):
    s = store.state_get(KEY) or {}
    return {"accounts": dict(s.get("accounts") or {}), "allow": list(s.get("allow") or []), "changes": list(s.get("changes") or [])}


def load_for_read(store):
    """What the agent reads: its own saved settings with the Master Agent's figures filled in where it has none (tracker/paid_master.py). Never saved back; the settings pages and every
    save use load. If the master cannot be read the agent's own settings are used as they are."""
    s = load(store)
    try:
        from . import paid_master
        return paid_master.overlay_copycheck(s, paid_master.load(store))
    except Exception:                                                                # noqa: BLE001 (a master that cannot be read must not stop an agent)
        return s


def account_of(settings, account_id):
    """One account's saved lists, each empty when nothing is saved."""
    a = (settings.get("accounts") or {}).get(str(account_id)) or {}
    return {"phones": list(a.get("phones") or []), "places": list(a.get("places") or []), "brand": list(a.get("brand") or []), "allow": list(a.get("allow") or []), "name": a.get("name") or ""}


def _note(s, who, what, now):
    s["changes"] = ([{"at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "by": who, "what": what}] + list(s.get("changes") or []))[:30]


def _items(v, sep=r"[\n;]+"):
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in re.split(sep, str(v or "")) if x.strip()]


def clean_phones(v):
    out = []
    for raw in _items(v, r"[\n;,]+"):
        d = rules.phone_digits(raw)
        if not d:
            raise BadRequest("%s is not a ten digit US phone number." % raw[:30])
        shown = "%s-%s-%s" % (d[:3], d[3:6], d[6:])
        if shown not in out:
            out.append(shown)
    if len(out) > MAX_PHONES:
        raise BadRequest("At most %d phone numbers per account." % MAX_PHONES)
    return out


def clean_places(v):
    out = []
    for raw in _items(v):
        if not _PLACE.match(raw):
            raise BadRequest("%s is not a place name (use letters, like Quincy, MA)." % raw[:40])
        if raw.lower() not in [p.lower() for p in out]:
            out.append(raw)
    if len(out) > MAX_PLACES:
        raise BadRequest("At most %d places per account." % MAX_PLACES)
    return out


def clean_brand(v):
    out = []
    for raw in _items(v):
        if len(raw) > 60 or not re.search(r"[A-Za-z0-9]", raw):
            raise BadRequest("%s is not a brand name." % raw[:40])
        if raw not in out:
            out.append(raw)
    if len(out) > MAX_BRANDS:
        raise BadRequest("At most %d brand spellings per account." % MAX_BRANDS)
    return out


def clean_words(v):
    out = []
    for raw in _items(v, r"[\s,;]+"):
        w = raw.lower()
        if not _WORD.match(w):
            raise BadRequest("%s is not a word (letters, apostrophes and hyphens only)." % raw[:40])
        if w not in out:
            out.append(w)
    if len(out) > MAX_ALLOW:
        raise BadRequest("At most %d allowed words." % MAX_ALLOW)
    return out


def save_account(store, account_id, name, data, who, now=None):
    account_id = str(account_id or "").strip()
    if not (account_id.isdigit() or re.fullmatch(r"act_\d{4,20}", account_id)):
        raise BadRequest("Pick one account to save its lists: they are not saved for an MCC as a whole.")
    data = data if isinstance(data, dict) else {}
    clean = {"phones": clean_phones(data.get("phones")), "places": clean_places(data.get("places")), "brand": clean_brand(data.get("brand")), "allow": clean_words(data.get("allow")),
             "name": str(name or "")[:120]}
    s = load(store)
    if any(clean[k] for k in ("phones", "places", "brand", "allow")):
        s["accounts"][account_id] = clean
    else:
        s["accounts"].pop(account_id, None)
    _note(s, who, "saved the lists of account %s" % account_id, now)
    store.state_set(KEY, s)
    return account_of(s, account_id)


def save_allow(store, words, who, now=None):
    s = load(store)
    s["allow"] = clean_words(words)
    _note(s, who, "saved the allowed words for every account (%d)" % len(s["allow"]), now)
    store.state_set(KEY, s)
    return s["allow"]


def add_allowed(store, account_id, words, who, now=None):
    """Add words to one account's allowed words (the 'this is right' button on a finding)."""
    account_id = str(account_id or "").strip()
    new = clean_words(words)
    if not new:
        raise BadRequest("Give at least one word.")
    s = load(store)
    a = s["accounts"].get(account_id) or {"phones": [], "places": [], "brand": [], "allow": [], "name": ""}
    a["allow"] = (list(a.get("allow") or []) + [w for w in new if w not in (a.get("allow") or [])])[:MAX_ALLOW]
    s["accounts"][account_id] = a
    _note(s, who, "allowed %s for account %s" % (", ".join(new), account_id), now)
    store.state_set(KEY, s)
    return account_of(s, account_id)


def view(store):
    s = load(store)
    return {"accounts": s["accounts"], "allow": s["allow"], "changes": s["changes"][:10]}
