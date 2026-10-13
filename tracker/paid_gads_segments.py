"""Paid Marketing Agents: Segment Analysis's view of Google Ads.

The transport is tracker/paid_gads.py (the Performance Summarizer's): one OAuth
sign-in, retries, paging, the Postgres cache and, above all, the daily request
budget that both agents share, because the Explorer allowance (about 2,880
calls a day) is one pool. Nothing here talks to Google any other way, so a
Segment Analysis view is counted against the same budget and refused with the
same message when it is used up.

What this adds on top:

  * the same account list, plus the MCCs that hold them (Eventgroove, OIA,
    Tealium, Workato), so a report can be run for every account of one MCC
    together. An MCC whose accounts use several currencies is split into one
    group per currency, because spend in different currencies cannot be added;
  * place names for city rows;
  * `GadsError`, the error the Segment Analysis routes and page already speak:
    a kind (not_configured, auth, permission, quota, bad_request, api) and a
    sentence that is safe to show staff.
"""
from __future__ import annotations

import logging
import re
import threading
import time

from . import paid_gads as base
from . import paid_store

log = logging.getLogger(__name__)

ACCOUNTS_TTL_S = 10 * 60          # the combined inventory, in memory
MANAGERS_TTL_S = 60 * 60          # which accounts sit under which MCC, in Postgres
CUSTOMER_ID_RE = base.ID_RE
GROUP_ID_RE = re.compile(r"^mcc-(\d{10})(?:-([A-Z]{3}))?$")


class GadsError(RuntimeError):
    """kind: not_configured, auth, permission, quota, bad_request, api."""

    def __init__(self, kind, detail):
        self.kind, self.detail = kind, detail
        super().__init__(detail)


def translate(e):
    """paid_gads' errors as GadsError, keeping its message."""
    msg = str(e)
    if isinstance(e, base.NotConfigured):
        return GadsError("not_configured", msg)
    if isinstance(e, base.OverQuota):
        return GadsError("quota", msg)
    if "sign-in was rejected" in msg or "would not sign in" in msg:
        return GadsError("auth", msg)
    status = re.search(r"answered (\d{3})", msg)
    status = int(status.group(1)) if status else 0
    if status == 401:
        return GadsError("auth", msg)
    if status == 403:
        return GadsError("permission", msg)
    if status == 400:
        return GadsError("bad_request", msg)
    return GadsError("api", msg)


def config_problem():
    return base.config_problem()


def login_customer_id():
    return base.login_customer()


def search(customer_id, gaql, *, store=None, http=None):
    """Every row of one GAQL query, counted against the shared daily budget."""
    cid = re.sub(r"\D", "", str(customer_id))
    if not CUSTOMER_ID_RE.match(cid):
        raise GadsError("bad_request", "That is not a Google Ads customer id.")
    try:
        return base.search(cid, gaql, store or paid_store, http=http)
    except base.AdsError as e:
        raise translate(e) from e


# ── Accounts, and the MCCs that hold them ───────────────────────────────────

_inv = {"at": 0.0, "data": None}
_inv_lock = threading.Lock()

_ACCOUNTS_GAQL = (
    "SELECT customer_client.id, customer_client.descriptive_name, customer_client.currency_code, "
    "customer_client.time_zone FROM customer_client "
    "WHERE customer_client.manager = FALSE AND customer_client.status = 'ENABLED'")
_MANAGERS_GAQL = (
    "SELECT customer_client.id, customer_client.descriptive_name FROM customer_client "
    "WHERE customer_client.manager = TRUE AND customer_client.status = 'ENABLED' AND customer_client.level > 0")


def _ids(rows):
    """{id: name} from customer_client rows."""
    out = {}
    for r in rows:
        c = r.get("customerClient") or {}
        cid = re.sub(r"\D", "", str(c.get("id") or ""))
        if CUSTOMER_ID_RE.match(cid):
            out[cid] = (c.get("descriptiveName") or "").strip() or ("Account " + cid)
    return out


def _mcc_name(name):
    return re.sub(r"\s*MCC\s*$", "", name or "", flags=re.I).strip() or name or "MCC"


def _shared_accounts():
    """The Performance Summarizer's account list (cached in Postgres for an
    hour), in this module's shape."""
    try:
        listed = base.list_accounts(paid_store)
    except base.AdsError as e:
        raise translate(e) from e
    return [{"id": a["id"], "name": a["name"], "currency": a["currency"], "timeZone": a.get("tz") or ""}
            for a in listed]


def _managers(run):
    """{manager id: {name, ids: [account ids under it]}}. One call for the
    managers and one per manager; kept in Postgres for MANAGERS_TTL_S because
    it changes only when an account is moved. A manager this server cannot
    read is skipped, not fatal."""
    key = "mcc-members"
    try:
        hit = paid_store.get_gads_cache(key, MANAGERS_TTL_S)
    except Exception as e:                                   # a store outage only costs the calls
        log.warning("paid_gads_segments: cache read failed (%s)", e)
        hit = None
    if hit is not None:
        return hit
    managers = _ids(run(login_customer_id(), _MANAGERS_GAQL))

    def members(mid):
        try:
            return mid, sorted(_ids(run(mid, _ACCOUNTS_GAQL)))
        except GadsError as e:
            if e.kind in ("quota", "not_configured", "auth"):
                raise                                          # nothing else will work either
            log.warning("paid_gads_segments: could not read the accounts of manager %s: %s", mid, e)
            return mid, []
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        under = dict(ex.map(members, list(managers)))
    value = {mid: {"name": managers[mid], "ids": ids} for mid, ids in under.items()}
    try:
        paid_store.put_gads_cache(key, value)
    except Exception as e:
        log.warning("paid_gads_segments: cache write failed (%s)", e)
    return value


def build_inventory(accounts, managers):
    """(accounts with their `group`, groups) from the account list and the
    managers' member ids. A group is an MCC's accounts in one currency, two or
    more of them. An account's `group` is the smallest group holding it."""
    everyone = {a["id"]: a for a in accounts}
    groups = []
    for mid, info in managers.items():
        by_cur = {}
        for aid in info["ids"]:
            if aid in everyone:
                by_cur.setdefault(everyone[aid]["currency"], []).append(everyone[aid])
        for cur, accts in by_cur.items():
            if len(accts) < 2:
                continue
            split = len(by_cur) > 1
            name = _mcc_name(info["name"])
            groups.append({
                "id": "mcc-%s-%s" % (mid, cur) if split else "mcc-%s" % mid, "managerId": mid,
                "name": "%s (%s)" % (name, cur) if split else name, "currency": cur,
                "accountIds": sorted(a["id"] for a in accts),
                "timeZones": sorted({a["timeZone"] for a in accts if a["timeZone"]})})
    groups.sort(key=lambda g: (g["name"].lower(), g["id"]))
    out = []
    for a in sorted(everyone.values(), key=lambda a: (a["name"].lower(), a["id"])):
        holding = [g for g in groups if a["id"] in g["accountIds"]]
        home = min(holding, key=lambda g: len(g["accountIds"]), default=None)
        out.append(dict(a, group=home["id"] if home else ""))
    return out, groups


def inventory(*, search_fn=None, accounts_fn=None, now=None):
    """{accounts: [{id, name, currency, timeZone, group}], groups: [{id,
    managerId, name, currency, accountIds, timeZones}]}, kept in memory for
    ACCOUNTS_TTL_S."""
    now = time.time() if now is None else now
    with _inv_lock:
        if _inv["data"] is not None and now - _inv["at"] < ACCOUNTS_TTL_S:
            return _inv["data"]
    accounts = (accounts_fn or _shared_accounts)()
    accounts_out, groups = build_inventory(accounts, _managers(search_fn or search))
    data = {"accounts": accounts_out, "groups": groups}
    with _inv_lock:
        _inv.update(at=now, data=data)
    return data


def list_accounts(**kw):
    return list(inventory(**kw)["accounts"])


def list_groups(**kw):
    return list(inventory(**kw)["groups"])


def reset_accounts():
    with _inv_lock:
        _inv.update(at=0.0, data=None)


# ── Place names (a city is only an id in a report row) ──────────────────────

_geo_names = {}
_geo_lock = threading.Lock()
_GEO_CHUNK = 120


def geo_names(resource_names, *, search_fn=None):
    """{'geoTargetConstants/1014897': {'name': 'Annapolis', 'canonical': ...}}
    for the ones that resolve. Place names never change, so they are kept for
    the life of the process."""
    wanted = [r for r in dict.fromkeys(resource_names) if re.fullmatch(r"geoTargetConstants/\d+", r or "")]
    with _geo_lock:
        missing = [r for r in wanted if r not in _geo_names]
    for i in range(0, len(missing), _GEO_CHUNK):
        chunk = missing[i:i + _GEO_CHUNK]
        gaql = ("SELECT geo_target_constant.resource_name, geo_target_constant.name, "
                "geo_target_constant.canonical_name FROM geo_target_constant "
                "WHERE geo_target_constant.resource_name IN (%s)" % ", ".join("'%s'" % r for r in chunk))
        rows = (search_fn or search)(login_customer_id(), gaql)
        with _geo_lock:
            for row in rows:
                g = row.get("geoTargetConstant") or {}
                if g.get("resourceName"):
                    _geo_names[g["resourceName"]] = {"name": g.get("name") or "",
                                                     "canonical": g.get("canonicalName") or ""}
    with _geo_lock:
        return {r: _geo_names[r] for r in wanted if r in _geo_names}
