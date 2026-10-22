"""Paid Marketing Agents: Location Conflicts, what the team saved (no network).

The sheet asks for location checks "against the Client Master Config". That config is not built yet, so until it is this keeps what the checks need, per account, in the monitors' store
(tracker/paid_monitor_store.py, the value "settings:geoconf"):

  areas           places the account's ads and campaigns may name though no campaign targets them ("Greater Boston": a region people say, not a place Google targets)
  ignore          words that look like a place and are not (Orange, Reading, Jackson): they are never taken for a place in this account
  acceptPresence  the team chose to show to people interested in the places (the Presence or interest option), so that setting is not a finding

Each change says who made it and when. The store is any object with state_get and state_set, so tests run on a dict.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

KEY = "settings:geoconf"
MAX_AREAS, MAX_IGNORE = 50, 100
_PLACE = re.compile(r"^[A-Za-z][A-Za-z .,'&-]{1,58}$")


class BadRequest(ValueError):
    pass


def empty():
    return {"accounts": {}, "changes": []}


def load(store):
    s = store.state_get(KEY) or {}
    return {"accounts": dict(s.get("accounts") or {}), "changes": list(s.get("changes") or [])}


def account_of(settings, account_id):
    a = (settings.get("accounts") or {}).get(str(account_id)) or {}
    return {"areas": list(a.get("areas") or []), "ignore": list(a.get("ignore") or []), "acceptPresence": bool(a.get("acceptPresence")), "name": a.get("name") or ""}


def _note(s, who, what, now):
    s["changes"] = ([{"at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "by": who, "what": what}] + list(s.get("changes") or []))[:30]


def _items(v):
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in re.split(r"[\n;]+", str(v or "")) if x.strip()]


def _clean(v, what, limit):
    out = []
    for raw in _items(v):
        if not _PLACE.match(raw):
            raise BadRequest("%s is not %s (use letters, like Quincy, MA)." % (raw[:40], what))
        if raw.lower() not in [p.lower() for p in out]:
            out.append(raw)
    if len(out) > limit:
        raise BadRequest("At most %d per account." % limit)
    return out


def save_account(store, account_id, name, data, who, now=None):
    account_id = str(account_id or "").strip()
    if not (account_id.isdigit() or re.fullmatch(r"act_\d{4,20}", account_id)):
        raise BadRequest("Pick one account to save its places: they are not saved for an MCC as a whole.")
    data = data if isinstance(data, dict) else {}
    clean = {"areas": _clean(data.get("areas"), "a place name", MAX_AREAS), "ignore": _clean(data.get("ignore"), "a word", MAX_IGNORE), "acceptPresence": bool(data.get("acceptPresence")),
             "name": str(name or "")[:120]}
    s = load(store)
    if clean["areas"] or clean["ignore"] or clean["acceptPresence"]:
        s["accounts"][account_id] = clean
    else:
        s["accounts"].pop(account_id, None)
    _note(s, who, "saved the places of account %s" % account_id, now)
    store.state_set(KEY, s)
    return account_of(s, account_id)


def add_ignore(store, account_id, words, who, now=None):
    """Add words to one account's ignored words (the 'this is not a place' button on a finding)."""
    account_id = str(account_id or "").strip()
    new = _clean(words, "a word", MAX_IGNORE)
    if not new:
        raise BadRequest("Give at least one word.")
    s = load(store)
    a = s["accounts"].get(account_id) or {"areas": [], "ignore": [], "acceptPresence": False, "name": ""}
    have = [w.lower() for w in a.get("ignore") or []]
    a["ignore"] = (list(a.get("ignore") or []) + [w for w in new if w.lower() not in have])[:MAX_IGNORE]
    s["accounts"][account_id] = a
    _note(s, who, "ignored %s for account %s" % (", ".join(new), account_id), now)
    store.state_set(KEY, s)
    return account_of(s, account_id)


def view(store):
    s = load(store)
    return {"accounts": s["accounts"], "changes": s["changes"][:10]}
