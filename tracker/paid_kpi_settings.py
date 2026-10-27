"""Paid Marketing Agents: KPI Monitoring, what the team saved (no network).

The sheet asks for the KPIs to be held against "each client's targets". The Client Master Config that is meant to hold them is not built yet, so until it is the targets are kept, per account,
in the monitors' store (tracker/paid_monitor_store.py, the value "settings:kpi"):

  targets     the account's cpa (cost per conversion or lead, in the account's currency), roas (a multiple: 4 is 400 percent), cvr and ctr (the lowest wanted, as a percent)
  tolerance   how far past a target before it is a finding, in percent (20 when not saved)
  campaigns   a campaign's own targets, by campaign id (a brand campaign and a generic one rarely share one): the same four figures, at most 100 campaigns
  muted       campaigns to leave out, by campaign id, each with why (a test, a campaign being replaced): shown on the page and never alerted, at most 100

Each change says who made it and when. The store is any object with state_get and state_set, so tests run on a dict.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

KEY = "settings:kpi"
METRICS = ("cpa", "roas", "cvr", "ctr")
MAX_CAMPAIGNS = 100
MAX_NOTE = 200
LIMITS = {"cpa": (0.01, 1_000_000), "roas": (0.01, 1000), "cvr": (0.01, 100), "ctr": (0.01, 100)}
NAMES = {"cpa": "The cost per conversion", "roas": "The return on ad spend", "cvr": "The lowest conversion rate", "ctr": "The lowest click-through rate"}
TOLERANCE_LIMITS = (5, 100)


class BadRequest(ValueError):
    pass


def empty():
    return {"accounts": {}, "changes": []}


def load(store):
    s = store.state_get(KEY) or {}
    return {"accounts": dict(s.get("accounts") or {}), "changes": list(s.get("changes") or [])}


def load_for_read(store):
    """What the agent reads: its own saved settings with the Master Agent's figures filled in where it has none (tracker/paid_master.py). Never saved back; the settings pages and every
    save use load. If the master cannot be read the agent's own settings are used as they are."""
    s = load(store)
    try:
        from . import paid_master
        return paid_master.overlay_kpi(s, paid_master.load(store))
    except Exception:                                                                # noqa: BLE001 (a master that cannot be read must not stop an agent)
        return s


def account_of(settings, account_id):
    """One account's saved figures in the shape the rules read: {targets, tolerance, campaigns: {id: {cpa, roas, cvr, ctr}}, muted: {id: why}, names: {id: name}, name}."""
    a = (settings.get("accounts") or {}).get(str(account_id)) or {}
    return {"targets": {m: (a.get("targets") or {}).get(m) or None for m in METRICS}, "tolerance": a.get("tolerance") or None,
            "campaigns": {cid: {m: (c or {}).get(m) or None for m in METRICS} for cid, c in (a.get("campaigns") or {}).items()},
            "muted": {cid: (c or {}).get("why") or "" for cid, c in (a.get("muted") or {}).items()},
            "names": dict({cid: (c or {}).get("name") or "" for cid, c in (a.get("campaigns") or {}).items()}, **{cid: (c or {}).get("name") or "" for cid, c in (a.get("muted") or {}).items()}),
            "name": a.get("name") or ""}


def _note(s, who, what, now):
    s["changes"] = ([{"at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "by": who, "what": what}] + list(s.get("changes") or []))[:30]


def _figure(v, metric):
    if v in (None, ""):
        return None
    try:
        x = float(str(v).replace(",", "").replace("$", "").replace("%", "").replace("x", "").strip())
    except ValueError:
        raise BadRequest("%s must be a number." % NAMES[metric])
    lo, hi = LIMITS[metric]
    if x != x:
        raise BadRequest("%s must be a number." % NAMES[metric])
    if x == 0:
        return None
    if x < lo or x > hi:
        raise BadRequest("%s must be between %s and %s." % (NAMES[metric], format(lo, "g"), format(hi, ",g")))
    return round(x, 4)


def _targets(data):
    data = data if isinstance(data, dict) else {}
    return {m: _figure(data.get(m), m) for m in METRICS}


def _account_id(account_id):
    account_id = str(account_id or "").strip()
    if not (account_id.isdigit() or re.fullmatch(r"act_\d{4,20}", account_id)):
        raise BadRequest("Pick one account to save its targets: they are not saved for an MCC as a whole.")
    return account_id


def _campaign_id(cid):
    cid = str(cid or "").strip()
    if not cid.isdigit() and not re.fullmatch(r"\d{4,25}", cid):
        raise BadRequest("The campaign id must be a number.")
    return cid


def _tolerance(v):
    if v in (None, ""):
        return None
    try:
        x = float(str(v).replace("%", "").strip())
    except ValueError:
        raise BadRequest("The tolerance must be a number (a percent).")
    lo, hi = TOLERANCE_LIMITS
    if x != x:
        raise BadRequest("The tolerance must be a number (a percent).")
    if x < lo or x > hi:
        raise BadRequest("The tolerance must be between %d and %d percent." % (lo, hi))
    return round(x, 1)


def save_account(store, account_id, name, data, who, now=None):
    """Save one account's targets and tolerance (its campaigns' own targets and muted campaigns are kept as they are)."""
    account_id = _account_id(account_id)
    data = data if isinstance(data, dict) else {}
    s = load(store)
    a = dict(s["accounts"].get(account_id) or {})
    a["targets"] = {m: v for m, v in _targets(data.get("targets")).items() if v}
    a["tolerance"] = _tolerance(data.get("tolerance"))
    a["name"] = str(name or a.get("name") or "")[:120]
    _store(s, account_id, a)
    _note(s, who, "saved the targets of account %s" % account_id, now)
    store.state_set(KEY, s)
    return account_of(s, account_id)


def _store(s, account_id, a):
    keep = {k: v for k, v in a.items() if v not in (None, {}, "", [])}
    if keep.get("targets") or keep.get("tolerance") or keep.get("campaigns") or keep.get("muted"):
        s["accounts"][account_id] = keep
    else:
        s["accounts"].pop(account_id, None)


def save_campaign(store, account_id, campaign_id, name, data, who, now=None):
    """One campaign's own targets (all empty removes them)."""
    account_id, campaign_id = _account_id(account_id), _campaign_id(campaign_id)
    own = {m: v for m, v in _targets(data).items() if v}
    s = load(store)
    a = dict(s["accounts"].get(account_id) or {})
    camps = dict(a.get("campaigns") or {})
    if own:
        if campaign_id not in camps and len(camps) >= MAX_CAMPAIGNS:
            raise BadRequest("At most %d campaigns can have their own targets in one account." % MAX_CAMPAIGNS)
        camps[campaign_id] = dict(own, name=str(name or "")[:120])
    else:
        camps.pop(campaign_id, None)
    a["campaigns"] = camps
    _store(s, account_id, a)
    _note(s, who, "%s the targets of campaign %s in account %s" % ("saved" if own else "removed", campaign_id, account_id), now)
    store.state_set(KEY, s)
    return account_of(s, account_id)


def mute(store, account_id, campaign_id, name, why, who, now=None):
    """Leave one campaign out of the checks, with why."""
    account_id, campaign_id = _account_id(account_id), _campaign_id(campaign_id)
    why = str(why or "").strip()[:MAX_NOTE]
    if not why:
        raise BadRequest("Say why the campaign is left out, so the next person knows.")
    s = load(store)
    a = dict(s["accounts"].get(account_id) or {})
    muted = dict(a.get("muted") or {})
    if campaign_id not in muted and len(muted) >= MAX_CAMPAIGNS:
        raise BadRequest("At most %d campaigns can be left out in one account." % MAX_CAMPAIGNS)
    muted[campaign_id] = {"name": str(name or "")[:120], "why": why}
    a["muted"] = muted
    _store(s, account_id, a)
    _note(s, who, "left campaign %s out of the checks in account %s" % (campaign_id, account_id), now)
    store.state_set(KEY, s)
    return account_of(s, account_id)


def unmute(store, account_id, campaign_id, who, now=None):
    account_id, campaign_id = _account_id(account_id), _campaign_id(campaign_id)
    s = load(store)
    a = dict(s["accounts"].get(account_id) or {})
    muted = dict(a.get("muted") or {})
    muted.pop(campaign_id, None)
    a["muted"] = muted
    _store(s, account_id, a)
    _note(s, who, "put campaign %s back into the checks in account %s" % (campaign_id, account_id), now)
    store.state_set(KEY, s)
    return account_of(s, account_id)


def view(store):
    s = load(store)
    return {"accounts": {aid: account_of(s, aid) for aid in s["accounts"]}, "changes": s["changes"][:10], "limits": {m: list(LIMITS[m]) for m in METRICS}, "tolerance": list(TOLERANCE_LIMITS), "maxCampaigns": MAX_CAMPAIGNS}
