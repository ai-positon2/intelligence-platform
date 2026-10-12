"""Paid Marketing Agents: Pacing Desk snapshots.

The pacing engine (paid_marketing/pacing_engine/, from Agent Holmes) runs on
the PPC team's machine against that day's Google Ads export and the Budget
Tracker, writes out/dashboard.json, and pushes it here
(paid_marketing/pacing_engine/push.py, or Holmes' own dashboard/seed_live.js
pointed at this URL). This module turns that push into a stored snapshot.

Two body shapes are accepted, because both senders exist:

  * the raw dashboard payload, {asOf, month, ..., clients: [{name, accounts}]}
  * seed_live.js's {meta, clients: [{id, data}], latest: {date}}

Either way the portfolio rollup is recomputed here from the client data
rather than trusted from the sender, with the same rules as the page and
seed_live.js: an account whose budget figure is ambiguous counts towards
conversions and moves, never towards money.
"""
from __future__ import annotations

import re

MAX_CLIENTS = 200
MAX_ACCOUNTS = 2000
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
LANDINGS = ("WILL OVERSPEND", "WILL UNDERSPEND", "ON TARGET", "BUDGET UNCLEAR")
META_KEYS = ("asOf", "dataThrough", "month", "elapsed", "daysInMonth", "idealPacing", "tolerance")

# A move's id is built by the page (moveDocId): the day, two short slugs and
# a hash of (location, from, to). Anything else is refused.
MOVE_ID_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})__[a-z0-9-]{0,24}__[a-z0-9-]{0,24}__[0-9a-f]{1,8}$")


class BadPayload(ValueError):
    pass


def slug(s):
    """seed_live.js's slug(), so a client keeps one id whichever sender
    pushed it."""
    s = str(s).lower().replace("&", "and")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:120]
    return s or "x"


def _num(v, field):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise BadPayload("%s should be a number, got %r" % (field, v))
    return float(v)


def _client_list(body):
    """The clients, as plain dicts, from either body shape."""
    clients = body.get("clients")
    if not isinstance(clients, list) or not clients:
        raise BadPayload("no clients in the payload")
    if len(clients) > MAX_CLIENTS:
        raise BadPayload("%d clients is more than the %d this accepts" % (len(clients), MAX_CLIENTS))
    out = []
    for c in clients:
        if isinstance(c, dict) and isinstance(c.get("data"), dict) and "accounts" not in c:
            c = c["data"]                      # seed_live.js shape: {id, data}
        if not isinstance(c, dict) or not isinstance(c.get("name"), str) or not c["name"].strip():
            raise BadPayload("every client needs a name")
        if not isinstance(c.get("accounts"), list):
            raise BadPayload("client %r has no accounts list" % c.get("name"))
        out.append(c)
    return out


def _check_account(client, a):
    where = "%s / %s" % (client, a.get("name") if isinstance(a, dict) else a)
    if not isinstance(a, dict) or not isinstance(a.get("name"), str):
        raise BadPayload("an account in %s has no name" % client)
    if a.get("landing") not in LANDINGS:
        raise BadPayload("%s: landing %r is not one of %s" % (where, a.get("landing"), ", ".join(LANDINGS)))
    for f in ("allocated", "spend", "projected", "conv"):
        _num(a.get(f), "%s: %s" % (where, f))
    for f in ("moves", "campaigns"):
        if not isinstance(a.get(f), list):
            raise BadPayload("%s: %s should be a list" % (where, f))


def rollup(clients):
    """The portfolio figures the page's top strip and trend chart read, from
    the client data alone (seed_live.js / the page's computeDerived)."""
    A = [a for c in clients for a in c["accounts"]]
    solid = [a for a in A if not a.get("ambiguous")]
    capped = [a for a in A if a.get("capped")]
    return {
        "clientCount": len(clients), "accountCount": len(A),
        "tAll": sum(a["allocated"] for a in solid),
        "tSp": sum(a["spend"] for a in solid),
        "tPr": sum(a["projected"] for a in solid),
        "tCv": sum(a["conv"] for a in A),
        "tMv": sum(len(a["moves"]) for a in A),
        "atRisk": sum(a["allocated"] - a["projected"] for a in capped),
        "cappedCount": len(capped),
        "ambiguousCount": sum(1 for a in A if a.get("ambiguous")),
        "overCount": sum(1 for a in A if a["landing"] == "WILL OVERSPEND"),
        "underCount": sum(1 for a in A if a["landing"] == "WILL UNDERSPEND"),
        "okCount": sum(1 for a in A if a["landing"] == "ON TARGET"),
    }


_EM_DASH = re.compile(r"\s*\u2014\s*")


def _no_em_dash(v):
    """The engine writes its advice with em dashes; the platform's copy never
    uses them. Every string in the snapshot is rewritten on the way in."""
    if isinstance(v, str):
        return _EM_DASH.sub(", ", v)
    if isinstance(v, list):
        return [_no_em_dash(x) for x in v]
    if isinstance(v, dict):
        return {k: _no_em_dash(x) for k, x in v.items()}
    return v


def build_seed(body):
    """(meta, [(slug, client)], latest_date) from a pushed body. Raises
    BadPayload saying what is wrong."""
    if not isinstance(body, dict):
        raise BadPayload("the body should be a JSON object")
    body = _no_em_dash(body)
    head = body.get("meta") if isinstance(body.get("meta"), dict) else body
    clients = _client_list(body)
    n_accounts = sum(len(c["accounts"]) for c in clients)
    if n_accounts > MAX_ACCOUNTS:
        raise BadPayload("%d accounts is more than the %d this accepts" % (n_accounts, MAX_ACCOUNTS))
    for c in clients:
        for a in c["accounts"]:
            _check_account(c["name"], a)

    as_of = head.get("asOf")
    if not isinstance(as_of, str) or not DATE_RE.match(as_of):
        raise BadPayload("asOf should be a YYYY-MM-DD date, got %r" % (as_of,))
    meta = {k: head.get(k) for k in META_KEYS}
    for k in ("elapsed", "daysInMonth", "idealPacing", "tolerance"):
        _num(meta[k], k)
    meta["portfolioWarnings"] = [str(w) for w in (head.get("portfolioWarnings") or [])][:200]
    meta["otherChannels"] = [o for o in (head.get("otherChannels") or []) if isinstance(o, dict)][:200]
    meta.update(rollup(clients))

    seen, rows = set(), []
    for c in clients:
        s = slug(c["name"])
        if s in seen:
            raise BadPayload("two clients share the id %r (%s)" % (s, c["name"]))
        seen.add(s)
        rows.append((s, c))

    latest = body.get("latest") if isinstance(body.get("latest"), dict) else {}
    latest_date = latest.get("date") or as_of
    if not isinstance(latest_date, str) or not DATE_RE.match(latest_date):
        raise BadPayload("latest.date should be a YYYY-MM-DD date")
    return meta, rows, latest_date


def check_move(move_id, body):
    """The fields to store for one ticked/unticked move. Raises BadPayload."""
    m = MOVE_ID_RE.match(move_id or "")
    if not m:
        raise BadPayload("not a move id")
    if not isinstance(body, dict):
        raise BadPayload("the body should be a JSON object")
    if body.get("date") != m.group(1):
        raise BadPayload("the move's date does not match its id")
    if not isinstance(body.get("applied"), bool):
        raise BadPayload("applied should be true or false")
    fields = {}
    for k in ("loc", "from", "to"):
        v = body.get(k)
        if not isinstance(v, str) or not v.strip() or len(v) > 500:
            raise BadPayload("%s should be a campaign or location name" % k)
        fields[k] = v
    amt = body.get("amt")
    if isinstance(amt, bool) or not isinstance(amt, (int, float)):
        raise BadPayload("amt should be a number")
    return {"date": m.group(1), "loc": fields["loc"], "from_campaign": fields["from"],
            "to_campaign": fields["to"], "amt": float(amt), "applied": body["applied"]}
