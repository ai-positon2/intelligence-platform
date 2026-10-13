"""Paid Marketing Agents: the platform's read-only Google Ads connection.

Segment Analysis is the first agent that reads Google Ads itself rather than a
sheet or an upload, so the transport lives here, apart from any one agent.
Plain REST (no client library): an OAuth refresh-token exchange, then
`googleAds:search`. Everything is read-only; nothing here can change an account.

  GOOGLE_ADS_DEVELOPER_TOKEN       the Google Ads API developer token
  GOOGLE_ADS_CLIENT_ID / _SECRET   the OAuth client the refresh token belongs to
  GOOGLE_ADS_REFRESH_TOKEN         a refresh token for a user with access to the MCC
  GOOGLE_ADS_LOGIN_CUSTOMER_ID     the manager account, defaults to Position2's
                                   root MCC (also the account list's root)
  GOOGLE_ADS_API_VERSION           defaults to v24

The Cloud project is on Explorer access (2,880 operations a day), so callers
cache what they fetch (tracker/paid_segments.py does).
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time

import requests

log = logging.getLogger(__name__)

DEFAULT_LOGIN_CUSTOMER = "9923720096"
DEFAULT_API_VERSION = "v24"
HTTP_TIMEOUT = 60
ACCOUNTS_TTL_S = 10 * 60
CUSTOMER_ID_RE = re.compile(r"^\d{10}$")

_ENV = ("GOOGLE_ADS_DEVELOPER_TOKEN", "GOOGLE_ADS_CLIENT_ID", "GOOGLE_ADS_CLIENT_SECRET",
        "GOOGLE_ADS_REFRESH_TOKEN")


class GadsError(RuntimeError):
    """kind: not_configured, auth, permission, quota, bad_request, api."""

    def __init__(self, kind, detail):
        self.kind, self.detail = kind, detail
        super().__init__(detail)


def _env(name):
    return os.environ.get(name, "").strip()


def login_customer_id():
    return re.sub(r"\D", "", _env("GOOGLE_ADS_LOGIN_CUSTOMER_ID")) or DEFAULT_LOGIN_CUSTOMER


def api_version():
    return _env("GOOGLE_ADS_API_VERSION") or DEFAULT_API_VERSION


def config_problem():
    """None when Google Ads can be queried, else what is missing, in words."""
    missing = [n for n in _ENV if not _env(n)]
    if missing:
        return "Google Ads is not connected on this server (%s not set)." % ", ".join(missing)
    return None


# ── OAuth ───────────────────────────────────────────────────────────────────

_token = {"value": None, "exp": 0.0}
_token_lock = threading.Lock()


def access_token(post=requests.post):
    with _token_lock:
        if _token["value"] and _token["exp"] > time.time() + 60:
            return _token["value"]
        problem = config_problem()
        if problem:
            raise GadsError("not_configured", problem)
        r = post("https://oauth2.googleapis.com/token", data={
            "client_id": _env("GOOGLE_ADS_CLIENT_ID"), "client_secret": _env("GOOGLE_ADS_CLIENT_SECRET"),
            "refresh_token": _env("GOOGLE_ADS_REFRESH_TOKEN"), "grant_type": "refresh_token"},
            timeout=HTTP_TIMEOUT)
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code != 200 or not data.get("access_token"):
            raise GadsError("auth", "Google refused the Google Ads sign-in (%s). The refresh token may "
                                    "have been revoked." % (data.get("error_description") or data.get("error")
                                                            or r.status_code))
        _token.update(value=data["access_token"], exp=time.time() + int(data.get("expires_in") or 3600))
        return _token["value"]


def reset_token():
    with _token_lock:
        _token.update(value=None, exp=0.0)


# ── The query call ──────────────────────────────────────────────────────────

def _reason(status, body):
    """The API's own message, shortened, and the kind it implies."""
    err = (body or {}).get("error") or {}
    detail = ""
    for d in err.get("details") or []:
        for e in d.get("errors") or []:
            detail = e.get("message") or detail
            if detail:
                break
        if detail:
            break
    detail = (detail or err.get("message") or "HTTP %s" % status)[:300]
    if status == 429 or "RESOURCE_EXHAUSTED" in str(err.get("status")):
        return "quota", "Google Ads is rate limiting this server (%s). Try again in a few minutes." % detail
    if status in (401,):
        return "auth", "Google Ads rejected the sign-in: %s" % detail
    if status == 403:
        return "permission", "Google Ads denied access: %s" % detail
    if status == 400:
        return "bad_request", "Google Ads rejected the request: %s" % detail
    return "api", "Google Ads answered an error: %s" % detail


def search(customer_id, gaql, *, post=requests.post):
    """Every row of a GAQL query, following the page tokens. Rows are the
    API's own camelCase JSON."""
    customer_id = re.sub(r"\D", "", str(customer_id))
    if not CUSTOMER_ID_RE.match(customer_id):
        raise GadsError("bad_request", "That is not a Google Ads customer id.")
    url = "https://googleads.googleapis.com/%s/customers/%s/googleAds:search" % (api_version(), customer_id)
    rows, page_token = [], None
    for _page in range(200):
        body = {"query": gaql}
        if page_token:
            body["pageToken"] = page_token
        data = None
        for attempt in (0, 1):
            headers = {"Authorization": "Bearer " + access_token(post),
                       "developer-token": _env("GOOGLE_ADS_DEVELOPER_TOKEN"),
                       "login-customer-id": login_customer_id()}
            r = post(url, headers=headers, json=body, timeout=HTTP_TIMEOUT)
            try:
                data = r.json()
            except ValueError:
                data = {}
            if r.status_code == 401 and attempt == 0:
                reset_token()          # the cached token expired early: sign in once more
                continue
            if r.status_code >= 500 and attempt == 0:
                time.sleep(1.0)
                continue
            break
        if r.status_code != 200:
            kind, msg = _reason(r.status_code, data)
            log.warning("paid_gads: %s on customer %s: %s", r.status_code, customer_id, msg)
            raise GadsError(kind, msg)
        rows.extend(data.get("results") or [])
        page_token = data.get("nextPageToken")
        if not page_token:
            return rows
    raise GadsError("api", "That query returned more than 2,000,000 rows, which this will not read.")


# ── Accounts, and the MCCs that hold them ───────────────────────────────────
# Position2's accounts sit under sub-MCCs (Eventgroove, OIA, Tealium, Workato).
# A report can be run for one account or for every account of one MCC together,
# so the inventory is both: the accounts, and a group per MCC that holds at
# least two accounts in the same currency (spend in different currencies cannot
# be added, so an MCC with mixed currencies becomes one group per currency).

_inv = {"at": 0.0, "data": None}
_inv_lock = threading.Lock()

_ACCOUNTS_GAQL = (
    "SELECT customer_client.id, customer_client.descriptive_name, customer_client.currency_code, "
    "customer_client.time_zone FROM customer_client "
    "WHERE customer_client.manager = FALSE AND customer_client.status = 'ENABLED'")
_MANAGERS_GAQL = (
    "SELECT customer_client.id, customer_client.descriptive_name FROM customer_client "
    "WHERE customer_client.manager = TRUE AND customer_client.status = 'ENABLED' AND customer_client.level > 0")
GROUP_ID_RE = re.compile(r"^mcc-(\d{10})(?:-([A-Z]{3}))?$")


def _clients(rows):
    out = {}
    for r in rows:
        c = r.get("customerClient") or {}
        cid = re.sub(r"\D", "", str(c.get("id") or ""))
        if CUSTOMER_ID_RE.match(cid):
            out[cid] = {"id": cid, "name": (c.get("descriptiveName") or "").strip() or ("Account " + cid),
                        "currency": c.get("currencyCode") or "USD", "timeZone": c.get("timeZone") or ""}
    return out


def _mcc_name(name):
    return re.sub(r"\s*MCC\s*$", "", name or "", flags=re.I).strip() or name or "MCC"


def inventory(*, search_fn=None, now=None):
    """{accounts: [{id, name, currency, timeZone, group}], groups: [{id,
    managerId, name, currency, accountIds, timeZones}]}, cached for
    ACCOUNTS_TTL_S. An account's `group` is the id of the smallest group that
    holds it, or "". A manager this server cannot read is skipped, not fatal."""
    now = time.time() if now is None else now
    with _inv_lock:
        if _inv["data"] is not None and now - _inv["at"] < ACCOUNTS_TTL_S:
            return _inv["data"]
    run = search_fn or search
    everyone = _clients(run(login_customer_id(), _ACCOUNTS_GAQL))
    managers = _clients(run(login_customer_id(), _MANAGERS_GAQL))

    def members(mid):
        try:
            return mid, _clients(run(mid, _ACCOUNTS_GAQL))
        except GadsError as e:
            log.warning("paid_gads: could not read the accounts of manager %s: %s", mid, e)
            return mid, {}
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as ex:
        under = dict(ex.map(members, list(managers)))
    groups = []
    for mid, kids in under.items():
        kept = [a for a in kids.values() if a["id"] in everyone]
        by_cur = {}
        for a in kept:
            by_cur.setdefault(a["currency"], []).append(a)
        for cur, accts in by_cur.items():
            if len(accts) < 2:
                continue
            split = len(by_cur) > 1
            name = _mcc_name(managers[mid]["name"])
            groups.append({
                "id": "mcc-%s-%s" % (mid, cur) if split else "mcc-%s" % mid, "managerId": mid,
                "name": "%s (%s)" % (name, cur) if split else name, "currency": cur,
                "accountIds": sorted(a["id"] for a in accts),
                "timeZones": sorted({a["timeZone"] for a in accts if a["timeZone"]})})
    groups.sort(key=lambda g: (g["name"].lower(), g["id"]))
    accounts = []
    for a in sorted(everyone.values(), key=lambda a: (a["name"].lower(), a["id"])):
        holding = [g for g in groups if a["id"] in g["accountIds"]]
        home = min(holding, key=lambda g: len(g["accountIds"]), default=None)
        accounts.append(dict(a, group=home["id"] if home else ""))
    data = {"accounts": accounts, "groups": groups}
    with _inv_lock:
        _inv.update(at=now, data=data)
    return data


def list_accounts(**kw):
    """[{id, name, currency, timeZone, group}] for every enabled, non-manager
    account under the root MCC, by name."""
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
