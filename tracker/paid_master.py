"""Paid Marketing Agents: the Master Agent (the Client Master Config), what the team saved once per client (no network).

The sheet (#37): "One source of truth per client (budgets, KPI targets, locations, phone numbers, brand terms, allowed domains, offers) that every agent reads from." Until now each agent kept
its own copy of what it needed (Brand Spend, KPI Monitoring, the Ad Copy Checker, Location Conflicts), each saying it was waiting for this. This is where a client is written down once, in the
monitors' store (tracker/paid_monitor_store.py, the value "settings:master"):

  client     a name and the accounts it owns (Google customer ids and Meta ad accounts; an account is in one client only)
  per account  budget {monthly, weeklyMin, weeklyMax}, targets {cpa, roas, cvr, ctr}, tolerance: what differs between a client's own accounts
  shared     phones, locations (where the client's patients or customers are), areas (regions people say), brandTerms (the words searches for the brand contain), writtenAs (how the name is
             written in copy), allowWords (words the dictionary does not know that are right), domains (the addresses its ads may send people to), offers (text and the day it ends), notes

How the agents read it: the master FILLS what an agent has not saved for itself, and what an agent saved for itself still wins (the team set it there on purpose); the page shows every place
the two disagree. The overlay_* functions return a copy of an agent's settings with the master's figures filled in; nothing is written to the agent's own saved settings.

Each change says who made it and when. The store is any object with state_get and state_set, so tests run on a dict.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timezone

from . import paid_brandspend_insight as ins
from . import paid_brandspend_settings as bs
from . import paid_copycheck_settings as cs
from . import paid_domains as dom
from . import paid_geoconf_settings as gs
from . import paid_kpi_settings as ks

KEY = "settings:master"
MAX_CLIENTS, MAX_ACCOUNTS, MAX_DOMAINS, MAX_OFFERS, MAX_AREAS = 300, 30, 100, 20, 50
MAX_NAME, MAX_NOTES, MAX_OFFER = 80, 500, 200
_ACCOUNT = re.compile(r"^(\d{10}|act_\d{4,20})$")

# which agent reads which field: the page says so, and the tests hold the overlays to it
USED_BY = {
    "budget.monthly": ["Brand Spend Alert"], "budget.weeklyMin": ["Brand Spend Alert"], "budget.weeklyMax": ["Brand Spend Alert"],
    "targets": ["KPI Monitoring"], "tolerance": ["KPI Monitoring"],
    "phones": ["Ad Copy Checker"], "locations": ["Ad Copy Checker"], "areas": ["Location Conflicts"], "brandTerms": ["Brand Spend Alert"],
    "writtenAs": ["Ad Copy Checker"], "allowWords": ["Ad Copy Checker"], "domains": ["Unknown Domains"], "offers": [], "notes": [],
}


class BadRequest(ValueError):
    pass


def empty():
    return {"clients": {}, "changes": []}


def load(store):
    s = store.state_get(KEY) or {}
    return {"clients": dict(s.get("clients") or {}), "changes": list(s.get("changes") or [])}


def _note(s, who, what, now):
    s["changes"] = ([{"at": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"), "by": who, "what": what}] + list(s.get("changes") or []))[:30]


# ── Cleaning what the page sent ─────────────────────────────────────────────

def account_id(v):
    v = str(v or "").strip()
    if v.lower().startswith("act_"):
        v = "act_" + re.sub(r"\D", "", v)
    elif re.fullmatch(r"[\d-]{10,12}", v):
        v = re.sub(r"\D", "", v)
    if not _ACCOUNT.match(v):
        raise BadRequest("%s is not a Google Ads customer id or a Meta ad account (act_...)." % v[:30])
    return v


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "client"


def _domains(v):
    items = [str(x).strip() for x in (v if isinstance(v, list) else re.split(r"[\s,;]+", str(v or ""))) if str(x).strip()]
    out = []
    for raw in items:
        d = dom.clean_domain(raw)
        if not d:
            raise BadRequest("%s is not a domain (like example.com)." % raw[:40])
        if d not in out:
            out.append(d)
    if len(out) > MAX_DOMAINS:
        raise BadRequest("At most %d domains per client." % MAX_DOMAINS)
    return out


def _offers(v):
    out = []
    for o in (v if isinstance(v, list) else []):
        o = o if isinstance(o, dict) else {"text": o}
        text = " ".join(str(o.get("text") or "").split())
        if not text:
            continue
        if len(text) < 3 or len(text) > MAX_OFFER:
            raise BadRequest("An offer must be 3 to %d characters." % MAX_OFFER)
        ends = str(o.get("ends") or "").strip()
        if ends:
            try:
                date.fromisoformat(ends)
            except ValueError:
                raise BadRequest("The end day of '%s' must be a date like 2026-12-31." % text[:40])
        out.append({"text": text, "ends": ends})
    if len(out) > MAX_OFFERS:
        raise BadRequest("At most %d offers per client." % MAX_OFFERS)
    return out


def _budget(v):
    v = v if isinstance(v, dict) else {}
    clean = bs.clean_account({"weeklyMin": v.get("weeklyMin"), "weeklyMax": v.get("weeklyMax"), "monthlyBudget": v.get("monthly")})
    return {"monthly": clean["monthlyBudget"], "weeklyMin": clean["weeklyMin"], "weeklyMax": clean["weeklyMax"]}


def _account_block(a):
    a = a if isinstance(a, dict) else {"id": a}
    targets = {m: v for m, v in ks._targets(a.get("targets")).items() if v}
    return account_id(a.get("id")), {"budget": {k: v for k, v in _budget(a.get("budget")).items() if v}, "targets": targets, "tolerance": ks._tolerance(a.get("tolerance"))}


def _shared(v):
    v = v if isinstance(v, dict) else {}
    brand = v.get("brandTerms")
    return {"phones": cs.clean_phones(v.get("phones")), "locations": cs.clean_places(v.get("locations")), "areas": gs._clean(v.get("areas"), "a place name", MAX_AREAS),
            "brandTerms": ins.terms_of(",".join(brand) if isinstance(brand, list) else brand), "writtenAs": cs.clean_brand(v.get("writtenAs")), "allowWords": cs.clean_words(v.get("allowWords")),
            "domains": _domains(v.get("domains")), "offers": _offers(v.get("offers")), "notes": str(v.get("notes") or "").strip()[:MAX_NOTES]}


def clean_client(data):
    """{name, accounts: {id: {budget, targets, tolerance}}, shared} from what the page sent, or BadRequest saying what is wrong (the cleaners borrowed from the agents raise their own
    BadRequest; it is said the same way, as this module's)."""
    try:
        return _clean_client(data)
    except (bs.BadRequest, cs.BadRequest, ks.BadRequest, gs.BadRequest) as e:
        raise BadRequest(str(e)) from e


def _clean_client(data):
    data = data if isinstance(data, dict) else {}
    name = " ".join(str(data.get("name") or "").split())
    if not name:
        raise BadRequest("Give the client a name.")
    if len(name) > MAX_NAME:
        raise BadRequest("The client's name is at most %d characters." % MAX_NAME)
    accounts = {}
    for a in data.get("accounts") or []:
        aid, block = _account_block(a)
        if aid in accounts:
            raise BadRequest("Account %s is listed twice." % aid)
        accounts[aid] = block
    if len(accounts) > MAX_ACCOUNTS:
        raise BadRequest("At most %d accounts per client." % MAX_ACCOUNTS)
    return {"name": name, "accounts": accounts, "shared": _shared(data.get("shared"))}


# ── Saving ──────────────────────────────────────────────────────────────────

def client_of(master, account):
    """The client that owns an account, as {id, name, accounts, shared}, or None."""
    for cid, c in (master.get("clients") or {}).items():
        if str(account) in (c.get("accounts") or {}):
            return dict(c, id=cid)
    return None


def save_client(store, client_id, data, who, now=None, names=None):
    """Create a client (client_id empty) or replace one. An account belongs to one client only. `names` is {account id: name} for the message that says which client owns an account."""
    clean = clean_client(data)
    s = load(store)
    cid = str(client_id or "").strip()
    if cid and cid not in s["clients"]:
        raise BadRequest("That client does not exist.")
    for other_id, other in s["clients"].items():
        if other_id != cid and other["name"].lower() == clean["name"].lower():
            raise BadRequest("A client called %s already exists." % other["name"])
        for aid in clean["accounts"]:
            if other_id != cid and aid in (other.get("accounts") or {}):
                raise BadRequest("%s is already in the client %s." % ((names or {}).get(aid) or aid, other["name"]))
    if not cid:
        if len(s["clients"]) >= MAX_CLIENTS:
            raise BadRequest("At most %d clients." % MAX_CLIENTS)
        base, n = "c-" + _slug(clean["name"]), 1
        cid = base
        while cid in s["clients"]:
            n += 1
            cid = "%s-%d" % (base, n)
    s["clients"][cid] = clean
    _note(s, who, "saved the client %s (%d accounts)" % (clean["name"], len(clean["accounts"])), now)
    store.state_set(KEY, s)
    return dict(clean, id=cid)


def delete_client(store, client_id, who, now=None):
    s = load(store)
    c = s["clients"].pop(str(client_id or ""), None)
    if c is None:
        raise BadRequest("That client does not exist.")
    _note(s, who, "removed the client %s" % c["name"], now)
    store.state_set(KEY, s)
    return {"id": client_id, "name": c["name"]}


def view(store):
    s = load(store)
    return {"clients": [dict(c, id=cid) for cid, c in sorted(s["clients"].items(), key=lambda kv: kv[1]["name"].lower())], "changes": s["changes"][:10],
            "limits": {"accounts": MAX_ACCOUNTS, "domains": MAX_DOMAINS, "offers": MAX_OFFERS}, "usedBy": USED_BY}


# ── What an account is held to ──────────────────────────────────────────────

def effective(master, account):
    """What the master says for one account: the client's shared lists with the account's own budget and targets, or None when the account is in no client."""
    c = client_of(master, account)
    if c is None:
        return None
    a = c["accounts"][str(account)]
    sh = c.get("shared") or {}
    return {"client": {"id": c["id"], "name": c["name"]}, "budget": dict(a.get("budget") or {}), "targets": dict(a.get("targets") or {}), "tolerance": a.get("tolerance"),
            "phones": list(sh.get("phones") or []), "locations": list(sh.get("locations") or []), "areas": list(sh.get("areas") or []), "brandTerms": list(sh.get("brandTerms") or []),
            "writtenAs": list(sh.get("writtenAs") or []), "allowWords": list(sh.get("allowWords") or []), "domains": list(sh.get("domains") or []),
            "offers": [dict(o) for o in sh.get("offers") or []], "notes": sh.get("notes") or ""}


def active_offers(offers, today):
    """The offers that have not ended by `today` (an offer with no end day never ends)."""
    return [o for o in offers or [] if not o.get("ends") or o["ends"] >= today.isoformat()]


def _accounts_copy(settings):
    out = dict(settings)
    out["accounts"] = {aid: dict(a) for aid, a in (settings.get("accounts") or {}).items()}
    return out


def overlay_brandspend(settings, master):
    """A copy of Brand Spend's settings with the master's budget and brand words where the account has none saved."""
    out = _accounts_copy(settings)
    for cid, c in (master.get("clients") or {}).items():
        for aid in c.get("accounts") or {}:
            e = effective(master, aid)
            if not aid.isdigit():
                continue
            have = out["accounts"].get(aid) or {}
            fill = {"weeklyMin": e["budget"].get("weeklyMin"), "weeklyMax": e["budget"].get("weeklyMax"), "monthlyBudget": e["budget"].get("monthly"), "brandTerms": e["brandTerms"]}
            merged = dict(have)
            for k, v in fill.items():
                if v and not have.get(k):
                    merged[k] = v
            if merged != have:
                out["accounts"][aid] = merged
    return out


def overlay_kpi(settings, master):
    """A copy of KPI Monitoring's settings with the master's targets and tolerance where the account has none saved (each figure on its own)."""
    out = _accounts_copy(settings)
    for cid, c in (master.get("clients") or {}).items():
        for aid in c.get("accounts") or {}:
            e = effective(master, aid)
            have = out["accounts"].get(aid) or {}
            targets = dict(have.get("targets") or {})
            for m, v in e["targets"].items():
                if v and not targets.get(m):
                    targets[m] = v
            merged = dict(have)
            if targets:
                merged["targets"] = targets
            if e["tolerance"] and not have.get("tolerance"):
                merged["tolerance"] = e["tolerance"]
            if merged != have:
                out["accounts"][aid] = merged
    return out


def overlay_copycheck(settings, master):
    """A copy of the Ad Copy Checker's settings with the master's phones, places, written name and allowed words where the account has none saved (each list on its own)."""
    out = _accounts_copy(settings)
    for cid, c in (master.get("clients") or {}).items():
        for aid in c.get("accounts") or {}:
            e = effective(master, aid)
            have = out["accounts"].get(aid) or {}
            merged = dict(have)
            for key, mine in (("phones", e["phones"]), ("places", e["locations"]), ("brand", e["writtenAs"]), ("allow", e["allowWords"])):
                if mine and not have.get(key):
                    merged[key] = list(mine)
            if merged != have:
                out["accounts"][aid] = merged
    return out


def overlay_geoconf(settings, master):
    """A copy of Location Conflicts' settings with the master's areas where the account has none saved."""
    out = _accounts_copy(settings)
    for cid, c in (master.get("clients") or {}).items():
        for aid in c.get("accounts") or {}:
            e = effective(master, aid)
            have = out["accounts"].get(aid) or {}
            if e["areas"] and not have.get("areas"):
                out["accounts"][aid] = dict(have, areas=list(e["areas"]))
    return out


def domain_entries(master):
    """[{domain, scope}]: the master's allowed domains as entries of the domain database, one per account of the client."""
    out = []
    for cid, c in (master.get("clients") or {}).items():
        for aid in c.get("accounts") or {}:
            for d in (c.get("shared") or {}).get("domains") or []:
                out.append({"domain": d, "scope": aid, "note": "From the Master Agent (%s)" % c["name"], "fromMaster": True})
    return out


# ── Where an agent's own saved value disagrees ──────────────────────────────

def _norm(v):
    return sorted(str(x).lower() for x in v) if isinstance(v, list) else v


def drift(master, *, brandspend=None, kpi=None, copycheck=None, geoconf=None):
    """[{account, client, agent, field, master, saved}]: an agent's own saved figure that is not the master's. The agent's wins, so this is what the team should settle."""
    out = []

    def add(aid, c, agent, field, m, saved):
        if m and saved and _norm(m) != _norm(saved):
            out.append({"account": aid, "client": c["name"], "agent": agent, "field": field, "master": m, "saved": saved})
    for cid, c in sorted((master.get("clients") or {}).items(), key=lambda kv: kv[1]["name"].lower()):
        c = dict(c, id=cid)
        for aid in sorted(c.get("accounts") or {}):
            e = effective(master, aid)
            b = bs.account_of(brandspend or {}, aid) if brandspend is not None else {}
            for f, mk in (("weeklyMin", "weeklyMin"), ("weeklyMax", "weeklyMax"), ("monthlyBudget", "monthly")):
                add(aid, c, "Brand Spend Alert", "budget." + mk, e["budget"].get(mk), b.get(f))
            add(aid, c, "Brand Spend Alert", "brandTerms", e["brandTerms"], b.get("brandTerms"))
            k = ks.account_of(kpi or {}, aid) if kpi is not None else {"targets": {}}
            for m in ks.METRICS:
                add(aid, c, "KPI Monitoring", "targets." + m, e["targets"].get(m), (k.get("targets") or {}).get(m))
            add(aid, c, "KPI Monitoring", "tolerance", e["tolerance"], k.get("tolerance"))
            cc = cs.account_of(copycheck or {}, aid) if copycheck is not None else {}
            for field, mk in (("phones", "phones"), ("places", "locations"), ("brand", "writtenAs"), ("allow", "allowWords")):
                add(aid, c, "Ad Copy Checker", mk, e[mk], cc.get(field))
            g = gs.account_of(geoconf or {}, aid) if geoconf is not None else {}
            add(aid, c, "Location Conflicts", "areas", e["areas"], g.get("areas"))
    return out
