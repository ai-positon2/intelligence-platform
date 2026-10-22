"""Paid Marketing Agents: Keyword Visibility Tracker, what the team saved (no network).

The sheet gives no limits, so the defaults are the page's (top-of-page share 50 percent, Quality Score 5, the top 25 keywords). What a team knows that the numbers cannot is kept for everyone in
the monitors' store (tracker/paid_monitor_store.py, the value "settings:kvis"), per account:

  topIs   the lowest wanted top-of-page share, in percent (a brand keyword wants 90, a generic one may want 30)
  qsMin   the lowest wanted Quality Score (1 to 10)
  topN    how many of the highest-spend keywords are tracked (5 to 100)
  ignore  competitor domains to leave out of the auction findings (a sister site, a partner, the client's other brand)

Each change says who made it and when. The store is any object with state_get and state_set, so tests run on a dict.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

KEY = "settings:kvis"
MAX_IGNORE = 100
TOP_IS_LIMITS, QS_LIMITS, TOP_N_LIMITS = (1, 100), (2, 10), (5, 100)
_DOMAIN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")


class BadRequest(ValueError):
    pass


def empty():
    return {"accounts": {}, "changes": []}


def load(store):
    s = store.state_get(KEY) or {}
    return {"accounts": dict(s.get("accounts") or {}), "changes": list(s.get("changes") or [])}


def account_of(settings, account_id):
    a = (settings.get("accounts") or {}).get(str(account_id)) or {}
    return {"topIs": a.get("topIs") or None, "qsMin": a.get("qsMin") or None, "topN": a.get("topN") or None, "ignore": list(a.get("ignore") or []), "name": a.get("name") or ""}


def _note(s, who, what, now):
    s["changes"] = ([{"at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "by": who, "what": what}] + list(s.get("changes") or []))[:30]


def _number(v, name, limits, whole=False):
    if v in (None, ""):
        return None
    try:
        x = float(str(v).replace("%", "").strip())
    except ValueError:
        raise BadRequest("%s must be a number." % name)
    lo, hi = limits
    if x != x:
        raise BadRequest("%s must be a number." % name)
    if x < lo or x > hi:
        raise BadRequest("%s must be between %d and %d." % (name, lo, hi))
    return int(round(x)) if whole else round(x, 1)


def _domains(v):
    items = v if isinstance(v, list) else re.split(r"[\s,;]+", str(v or ""))
    out = []
    for raw in items:
        d = str(raw).strip().lower()
        d = re.sub(r"^https?://", "", d).split("/")[0]
        d = d[4:] if d.startswith("www.") else d
        if not d:
            continue
        if not _DOMAIN.match(d):
            raise BadRequest("%s is not a domain (like example.com)." % str(raw)[:40])
        if d not in out:
            out.append(d)
    if len(out) > MAX_IGNORE:
        raise BadRequest("At most %d domains per account." % MAX_IGNORE)
    return out


def save_account(store, account_id, name, data, who, now=None):
    account_id = str(account_id or "").strip()
    if not account_id.isdigit():
        raise BadRequest("Pick one Google Ads account to save its settings: they are not saved for an MCC as a whole.")
    data = data if isinstance(data, dict) else {}
    clean = {"topIs": _number(data.get("topIs"), "The lowest top-of-page share", TOP_IS_LIMITS), "qsMin": _number(data.get("qsMin"), "The lowest Quality Score", QS_LIMITS, True),
             "topN": _number(data.get("topN"), "The number of keywords", TOP_N_LIMITS, True), "ignore": _domains(data.get("ignore")), "name": str(name or "")[:120]}
    s = load(store)
    if clean["topIs"] or clean["qsMin"] or clean["topN"] or clean["ignore"]:
        s["accounts"][account_id] = {k: v for k, v in clean.items() if v}
    else:
        s["accounts"].pop(account_id, None)
    _note(s, who, "saved the settings of account %s" % account_id, now)
    store.state_set(KEY, s)
    return account_of(s, account_id)


def add_ignore(store, account_id, domains, who, now=None):
    """Add domains to one account's ignored competitors (the 'ignore this domain' button on a finding)."""
    account_id = str(account_id or "").strip()
    if not account_id.isdigit():
        raise BadRequest("Pick one Google Ads account to save its settings.")
    new = _domains(domains)
    if not new:
        raise BadRequest("Give at least one domain.")
    s = load(store)
    a = dict(s["accounts"].get(account_id) or {})
    a["ignore"] = (list(a.get("ignore") or []) + [d for d in new if d not in (a.get("ignore") or [])])[:MAX_IGNORE]
    s["accounts"][account_id] = a
    _note(s, who, "ignored %s for account %s" % (", ".join(new), account_id), now)
    store.state_set(KEY, s)
    return account_of(s, account_id)


def view(store):
    s = load(store)
    return {"accounts": {aid: account_of(s, aid) for aid in s["accounts"]}, "changes": s["changes"][:10], "limits": {"topIs": list(TOP_IS_LIMITS), "qsMin": list(QS_LIMITS), "topN": list(TOP_N_LIMITS)}}
