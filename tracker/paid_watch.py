"""Paid Marketing Agents: Account Watch, the reading and the report.

For a Google Ads account (or every account of an MCC) or a Meta ad account this reads what shows that something happened to the account that nobody may have meant, and the rules
(tracker/paid_watch_math.py) judge it. Nothing is changed in an account.

Google: the manager account's list of every client with its status (the platform's own account list only holds enabled ones, so a suspended or canceled account would otherwise just vanish), and
for each account the account budget orders, the change history of the last days (budgets, bid strategies, and who made each change), the conversion actions and who can open the account. Meta:
the ad account's status and spending limit, the Pages it can promote and its activity log of the last days (who did what). Who can open an account, the conversion goals and the Pages are held
against the monitor's own earlier daily readings (a snapshot per account), so they are judged from the second day; an account that was in the manager account and is not any more is found the same way.

Every read but the account list is soft (a refusal leaves that check out and says so); an account all of whose reads were refused is raised. The read is cached like the other agents
(tracker/paid_geo.py _cached). The field names of the Google queries and the Meta ones are the platforms' documented ones; the ones new here are checked by the admin live query check
(tracker/paid_gaql_check.py), not by a test.
"""
from __future__ import annotations

import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor

from . import paid_gads_segments as gads
from . import paid_segments as seg
from . import paid_watch_math as math

BadRequest = seg.BadRequest
AGENT = "watch"
CHANGES_LIMIT = 1000
READ_DAYS = math.CHANGE_DAYS + 1

GAQL_CLIENTS = ("SELECT customer_client.id, customer_client.descriptive_name, customer_client.status, customer_client.currency_code, customer_client.level FROM customer_client "
                "WHERE customer_client.manager = FALSE")
GAQL_ORDERS = ("SELECT account_budget.status, account_budget.approved_spending_limit_micros, account_budget.adjusted_spending_limit_micros, account_budget.amount_served_micros, "
               "account_budget.approved_end_date_time FROM account_budget")
GAQL_CHANGES = ("SELECT change_event.change_date_time, change_event.user_email, change_event.client_type, change_event.change_resource_type, change_event.resource_change_operation, "
                "change_event.changed_fields, change_event.old_resource, change_event.new_resource FROM change_event "
                "WHERE change_event.change_date_time >= '%s' AND change_event.change_date_time <= '%s' ORDER BY change_event.change_date_time DESC LIMIT " + str(CHANGES_LIMIT))
GAQL_ACTIONS = ("SELECT conversion_action.id, conversion_action.name, conversion_action.status, conversion_action.primary_for_goal, conversion_action.include_in_conversions_metric "
                "FROM conversion_action WHERE conversion_action.status != REMOVED")
GAQL_USERS = "SELECT customer_user_access.user_id, customer_user_access.email_address, customer_user_access.access_role FROM customer_user_access"

META_STATUS_FIELDS = "account_status,disable_reason,spend_cap,amount_spent,currency"
META_PAGE_FIELDS = "id,name,is_published"
META_PAGE_FIELDS_PLAIN = "id,name"
META_ACTIVITY_FIELDS = "event_type,event_time,actor_name,object_name,object_type,extra_data"
ZERO_DECIMAL = {"JPY", "KRW", "VND", "CLP", "ISK", "UGX", "PYG", "XAF", "XOF", "XPF", "RWF", "KMF", "GNF", "DJF", "BIF", "VUV"}

_BUDGET_FIELDS = {"amountMicros", "amount_micros"}
_BID_FIELDS = {"biddingStrategyType", "bidding_strategy_type", "targetCpa", "targetRoas", "maximizeConversions", "maximizeConversionValue", "manualCpc", "target_cpa", "target_roas",
               "maximize_conversions", "maximize_conversion_value", "manual_cpc"}
_CLIENTS = {"GOOGLE_ADS_WEB_CLIENT": "the Google Ads website", "GOOGLE_ADS_AUTOMATED_RULE": "an automated rule", "GOOGLE_ADS_SCRIPTS": "a Google Ads script", "GOOGLE_ADS_BULK_UPLOAD": "a bulk upload",
            "GOOGLE_ADS_EDITOR": "Google Ads Editor", "GOOGLE_ADS_MOBILE_APP": "the Google Ads app", "GOOGLE_ADS_RECOMMENDATIONS": "an applied recommendation", "GOOGLE_ADS_API": "the Google Ads API",
            "GOOGLE_ADS_RECOMMENDATIONS_SUBSCRIPTION": "an automatically applied recommendation", "SEARCH_ADS_360_SYNC": "Search Ads 360", "INTERNAL_TOOL": "a Google tool"}


def _get(row, *path, default=None):
    for p in path:
        if not isinstance(row, dict):
            return default
        row = row.get(p)
    return default if row is None else row


def _num(v, default=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if x == x else default


def _money(c, currency):
    from .paid_brandspend_math import money
    return money(c, currency)


# ── Reading Google's rows ───────────────────────────────────────────────────

def read_clients(rows):
    """{id: {id, name, status, currency}}: every client of the manager account, whatever its status."""
    out = {}
    for r in rows:
        c = r.get("customerClient") or {}
        cid = str(c.get("id") or "")
        if cid:
            out[cid] = {"id": cid, "name": c.get("descriptiveName") or "Account " + cid, "status": str(c.get("status") or "UNKNOWN"), "currency": c.get("currencyCode") or "USD"}
    return out


def read_orders(rows):
    """[{status, limit, served, endsAt}] in the account's currency: the approved limit as adjusted, else as approved; none when the order has no limit."""
    out = []
    for r in rows:
        b = r.get("accountBudget") or {}
        limit = _num(b.get("adjustedSpendingLimitMicros")) or _num(b.get("approvedSpendingLimitMicros"))
        out.append({"status": str(b.get("status") or ""), "limit": round(limit / 1e6, 2) if limit else None, "served": round((_num(b.get("amountServedMicros")) or 0.0) / 1e6, 2),
                    "endsAt": str(b.get("approvedEndDateTime") or "")})
    return out


def read_changes(rows):
    """[{at, user, client, kind, op, label, was, now}]: every change event with a time, newest first. `kind` is budget (a budget amount moved), bidding (a campaign's bid strategy or target) or other."""
    out = []
    for r in rows:
        e = r.get("changeEvent") or {}
        at = str(e.get("changeDateTime") or "")[:19]
        if not at:
            continue
        kind_t, op = str(e.get("changeResourceType") or ""), str(e.get("resourceChangeOperation") or "")
        names = {f.strip() for f in str(e.get("changedFields") or "").replace(",", " ").split() if f.strip()}
        kind, was, now, label = "other", None, None, "a %s change" % kind_t.lower().replace("_", " ") if kind_t else "a change"
        if kind_t == "CAMPAIGN_BUDGET":
            kind = "budget"
            if op == "CREATE":
                label = "a budget created"
            elif op == "REMOVE":
                label = "a budget removed"
            elif names & _BUDGET_FIELDS:
                was, now = _num(_get(e, "oldResource", "campaignBudget", "amountMicros")), _num(_get(e, "newResource", "campaignBudget", "amountMicros"))
                was, now = (was / 1e6 if was is not None else None), (now / 1e6 if now is not None else None)
                label = "a budget amount changed"
            else:
                kind = "other"
                label = "a budget setting changed"
        elif kind_t == "CAMPAIGN" and names & _BID_FIELDS:
            kind, label = "bidding", "a campaign's bidding changed"
        elif kind_t == "CAMPAIGN" and "status" in names:
            label = "a campaign switched on or off"
        user = str(e.get("userEmail") or "").strip().lower()
        client = str(e.get("clientType") or "")
        out.append({"at": at, "user": user or _CLIENTS.get(client) or "an unknown source", "client": client, "kind": kind, "op": op, "label": label, "was": was, "now": now})
    return out


def with_amounts(changes, currency):
    """The labels of the budget changes with their amounts, now that the currency is known."""
    out = []
    for c in changes:
        c = dict(c)
        if c["kind"] == "budget" and c["was"] is not None and c["now"] is not None and c["now"] != c["was"]:
            c["label"] = "a budget %s from %s to %s" % ("raised" if c["now"] > c["was"] else "lowered", _money(c["was"], currency), _money(c["now"], currency))
        out.append(c)
    return out


def read_actions(rows):
    out = []
    for r in rows:
        c = r.get("conversionAction") or {}
        aid = str(c.get("id") or "")
        if aid:
            out.append({"id": aid, "name": c.get("name") or aid, "status": str(c.get("status") or ""), "primary": bool(c.get("primaryForGoal")), "include": bool(c.get("includeInConversionsMetric"))})
    return out


def read_users(rows):
    out = []
    for r in rows:
        u = r.get("customerUserAccess") or {}
        email = str(u.get("emailAddress") or "").strip().lower()
        if email:
            out.append({"email": email, "role": str(u.get("accessRole") or "")})
    return out


def _soft(warnings, what, fn, errors):
    try:
        return fn()
    except gads.GadsError as e:
        errors.append(e)
        warnings.append("%s could not be read (%s), so that check was skipped." % (what, e))
        return None


def read_account(account_id, search, now=None, currency="USD"):
    """{orders, changes, actions, users, warnings}: one Google Ads account. Every read is soft; when every one is refused the first error is raised."""
    now = now or dt.datetime.now(dt.timezone.utc)
    since = (now - dt.timedelta(days=READ_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    until = (now + dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    warnings, errors = [], []
    orders = _soft(warnings, "The account budget orders", lambda: read_orders(search(account_id, GAQL_ORDERS)), errors)
    changes = _soft(warnings, "The change history", lambda: with_amounts(read_changes(search(account_id, GAQL_CHANGES % (since, until))), currency), errors)
    actions = _soft(warnings, "The conversion actions", lambda: read_actions(search(account_id, GAQL_ACTIONS)), errors)
    users = _soft(warnings, "Who can open the account", lambda: read_users(search(account_id, GAQL_USERS)), errors)
    if len(errors) == 4:
        raise errors[0]
    return {"orders": orders, "changes": changes, "actions": actions, "users": users, "warnings": warnings}


def google_raw(account, *, search_fn=None, clients=None, workers=6, now=None):
    """{accounts: [{id, name, currency, platform, status, ...the read}], warnings, members, clients}: one account or an MCC's accounts. An account all of whose reads were refused is left out and said
    so; every one failing is raised. `clients` is the manager account's list (read here when not given): it gives each account's status and the accounts that are not enabled."""
    search = search_fn or gads.search
    warnings = []
    if clients is None:
        try:
            clients = read_clients(search(gads.login_customer_id(), GAQL_CLIENTS))
        except gads.GadsError as e:
            clients = None
            warnings.append("The manager account's list of accounts could not be read (%s), so account status was skipped." % e)
    members = account.get("members") or [account]

    def run(member):
        try:
            return member, read_account(member["id"], search, now, member.get("currency") or account.get("currency") or "USD"), None
        except gads.GadsError as e:
            return member, None, e
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(members)))) as pool:
        done = list(pool.map(run, members))
    ok = [(mem, r) for mem, r, e in done if r is not None]
    if not ok:
        raise next(e for _m, _r, e in done if e)
    many = bool(account.get("members"))
    accounts = [dict(r, id=mem["id"], name=mem["name"], currency=mem.get("currency") or account.get("currency") or "USD", platform="google",
                     status=(clients or {}).get(mem["id"], {}).get("status") if clients is not None else None, today=(now or dt.datetime.now(dt.timezone.utc)).date().isoformat()) for mem, r in ok]
    inactive = []
    if clients is not None and many:
        cur = account.get("currency")
        listed = {m["id"] for m in members}
        inactive = [c for c in clients.values() if c["status"] != "ENABLED" and c["id"] not in listed and (not cur or c["currency"] == cur)]
    return {"accounts": accounts, "inactive": inactive, "clients": clients,
            "warnings": warnings + ["%s could not be read: %s" % (mem["name"], e) for mem, r, e in done if e] + [("%s: %s" % (mem["name"], w)) if many else w for mem, r in ok for w in r["warnings"]],
            "members": [mem["name"] for mem in members] if many else []}


# ── Meta ────────────────────────────────────────────────────────────────────

def meta_status_request(act):
    return "/%s" % act, {"fields": META_STATUS_FIELDS}


def meta_pages_request(act, plain=False):
    return "/%s/promote_pages" % act, {"fields": META_PAGE_FIELDS_PLAIN if plain else META_PAGE_FIELDS, "limit": 100}


def meta_activity_request(act, since):
    return "/%s/activities" % act, {"fields": META_ACTIVITY_FIELDS, "since": since, "limit": 200}


def _minor(v, currency):
    x = _num(v)
    if x is None:
        return None
    return x if currency in ZERO_DECIMAL else x / 100.0


def parse_status(node):
    """{code, disableReason, spendCap, spent, currency}: the ad account node, the amounts in the currency's main unit (Meta gives them in the smallest unit)."""
    cur = node.get("currency") or "USD"
    code = _num(node.get("account_status"))
    return {"code": None if code is None else int(code), "disableReason": int(_num(node.get("disable_reason"), 0) or 0), "spendCap": _minor(node.get("spend_cap"), cur) or 0.0,
            "spent": _minor(node.get("amount_spent"), cur) or 0.0, "currency": cur}


def parse_pages(rows):
    return [{"id": str(p.get("id")), "name": p.get("name") or str(p.get("id")), "published": (None if "is_published" not in p else bool(p.get("is_published")))} for p in rows if p.get("id")]


def _budget_values(extra, currency):
    """(was, now) of a budget event from Meta's extra_data (a JSON text with old_value and new_value), or (None, None) when it is not in a shape this knows."""
    try:
        data = json.loads(extra) if isinstance(extra, str) else (extra or {})
    except ValueError:
        return None, None
    old, new = data.get("old_value"), data.get("new_value")

    def pick(v):
        if isinstance(v, dict):
            for k, x in v.items():
                if "budget" in str(k).lower() and _num(x) is not None:
                    return _minor(x, currency)
            return None
        return _minor(v, currency) if _num(v) is not None else None
    return pick(old), pick(new)


def parse_activities(rows, currency="USD"):
    """[{at, actor, type, what, object, was, now}]: Meta's activity log, newest first; `what` is Meta's own wording of the type, never a guess at the meaning."""
    out = []
    for r in rows:
        t = str(r.get("event_type") or "")
        at = str(r.get("event_time") or "")
        if not t or not at:
            continue
        was = now = None
        if "budget" in t.lower():
            was, now = _budget_values(r.get("extra_data"), currency)
        out.append({"at": at, "actor": str(r.get("actor_name") or "").strip(), "type": t, "what": t.replace("_", " "), "object": str(r.get("object_name") or ""), "was": was, "now": now})
    return sorted(out, key=lambda c: c["at"], reverse=True)


def read_meta_account(act, pages, get, now=None, currency="USD"):
    """The same shape as read_account, for a Meta ad account. Every read is soft; when every one is refused the first error is raised."""
    now = now or dt.datetime.now(dt.timezone.utc)
    warnings, errors = [], []

    def soft(what, fn):
        try:
            return fn()
        except Exception as e:                                                       # noqa: BLE001 (Meta has its own errors; each read is soft)
            errors.append(e)
            warnings.append("%s could not be read (%s), so that check was skipped." % (what, str(e)[:160]))
            return None
    status = soft("The ad account's status", lambda: parse_status(get(*meta_status_request(act))))
    cur = (status or {}).get("currency") or currency

    def read_pages():
        try:
            return parse_pages(pages(*meta_pages_request(act)))
        except Exception:                                                            # noqa: BLE001 (is_published may be refused; the Pages themselves are what matters)
            return parse_pages(pages(*meta_pages_request(act, plain=True)))
    pg = soft("The Pages", read_pages)
    act_rows = soft("The activity history", lambda: parse_activities(pages(*meta_activity_request(act, (now - dt.timedelta(days=READ_DAYS)).strftime("%Y-%m-%d"))), cur))
    if len(errors) == 3:
        raise errors[0]
    return {"status": status, "pages": pg, "activity": act_rows, "currency": cur, "orders": None, "changes": None, "actions": None, "users": None, "warnings": warnings}


def meta_raw(act, *, pages_fn, account_fn, get_fn=None, now=None):
    from . import paid_meta, paid_store
    get = get_fn or (lambda path, params: paid_meta._get(path, params, paid_store))
    acct = account_fn(act) or {}
    got = read_meta_account(act, pages_fn, get, now, acct.get("currency") or "USD")
    return {"accounts": [dict(got, id=act, name=acct.get("name") or act, currency=got.get("currency") or acct.get("currency") or "USD", platform="meta",
                              today=(now or dt.datetime.now(dt.timezone.utc)).date().isoformat())], "inactive": [], "clients": None, "warnings": list(got["warnings"]), "members": []}


# ── The report ──────────────────────────────────────────────────────────────

_RANK = {"fix": 0, "check": 1, "clean": 2}


def snapshot_of(a, res=None):
    """What the monitor saves of one account each day: who has access, the conversion actions, the Pages (what a later day is held against) and the outcome."""
    snap = {}
    if a.get("users") is not None:
        snap["users"] = {u["email"]: u.get("role") or "" for u in a["users"]}
    if a.get("actions") is not None:
        snap["actions"] = {x["id"]: {"name": x["name"], "status": x["status"], "primary": x["primary"], "include": x["include"]} for x in a["actions"]}
    if a.get("pages") is not None:
        snap["pages"] = {p["id"]: p["name"] for p in a["pages"]}
    if res is not None:
        snap.update(status=res["status"], high=res["counts"]["high"], medium=res["counts"]["medium"], low=res["counts"]["low"], findings=res["findingCount"])
    return snap


def history_for(mon, account_id, today):
    """The monitor's earlier readings of an account (oldest first), or [] when there are none or the store cannot be read."""
    if mon is None:
        return []
    try:
        return mon.snaps_get(AGENT, str(account_id), (today - dt.timedelta(days=math.HISTORY_DAYS + 1)).isoformat())
    except Exception:                                                                # noqa: BLE001 (without a history the comparisons are listed, not judged)
        return []


def mcc_snapshot(clients, history, today):
    """The saved reading of the manager account's whole list: {ids: {id: name}, gone: {id: name}}. An account in the earlier readings and not in the list now is gone, and stays gone until it is listed."""
    ids = {cid: c["name"] for cid, c in (clients or {}).items()}
    prev = next((h for h in reversed(history or []) if str(h.get("day") or "") < today.isoformat()), None)
    gone = {}
    if prev is not None and clients is not None:
        for cid, name in {**(prev.get("gone") or {}), **(prev.get("ids") or {})}.items():
            if cid not in ids:
                gone[cid] = name
    return {"ids": ids, "gone": gone}


def _inactive_result(c, now):
    res = math.analyse({"currency": c.get("currency") or "USD", "status": c["status"], "orders": None, "changes": None, "actions": None, "users": None}, history=[], today=now.date(), now=now)
    res["checks"] = res["checks"][:1]
    return dict(res, id=c["id"], name=c["name"], currency=c.get("currency") or "USD", platform="google", today=now.date().isoformat(), inactive=True)


def _gone_result(cid, name, now):
    f = math._finding("account_gone", "gone", "The account is no longer in the manager account", "It was listed at an earlier reading and is not now: the link was removed, or the account was closed.", evidence=["Was listed as \"%s\"" % name])
    return {"status": "fix", "counts": {"high": 1, "medium": 0, "low": 0}, "findingCount": 1, "findings": [f], "checks": [math._check("status", "The account is in the manager account", "problem", "No longer listed.")],
            "byRule": [{"rule": "account_gone", "label": math.RULES["account_gone"]["label"], "level": "high", "findings": 1}], "id": cid, "name": name, "currency": "USD", "platform": "google",
            "today": now.date().isoformat(), "inactive": True}


def assemble(raw, account, *, history_fn=None, now=None):
    """The report from the raw read. `history_fn(account id, today)` gives the monitor's earlier readings of the account (and, for "mcc", of the manager account's list)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    accounts, warnings = [], list(raw.get("warnings") or [])
    for a in raw["accounts"]:
        today = dt.date.fromisoformat(a.get("today") or now.date().isoformat())
        platform = a.get("platform") or "google"
        res = math.analyse(a, history=history_fn(a["id"], today) if history_fn else [], today=today, now=now, platform=platform)
        accounts.append(dict(res, id=a["id"], name=a["name"], currency=a.get("currency") or "USD", platform=platform, today=a.get("today") or "", inactive=False))
    for c in raw.get("inactive") or []:
        accounts.append(_inactive_result(c, now))
    if raw.get("clients") is not None and history_fn is not None and account.get("members"):
        snap = mcc_snapshot(raw["clients"], history_fn("mcc", now.date()), now.date())
        cur = account.get("currency")
        for cid, name in snap["gone"].items():
            accounts.append(_gone_result(cid, name, now))
    accounts.sort(key=lambda a: (_RANK[a["status"]], -a["counts"]["high"], -a["counts"]["medium"], -a["counts"]["low"], a["name"].lower()))
    counts = {k: 0 for k in _RANK}
    for a in accounts:
        counts[a["status"]] += 1
    return {
        "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or "USD", "platform": account.get("platform") or "google",
                    "isGroup": bool(account.get("members")), "accountCount": len(account.get("members") or [])},
        "generatedAt": now.isoformat(timespec="seconds"), "warnings": warnings, "accounts": accounts,
        "rules": [{"rule": k, "label": math.RULES[k]["label"], "level": math.RULES[k]["level"], "area": math.RULES[k]["area"], "fix": math.RULES[k]["fix"]} for k in math.ORDER],
        "notMeasured": math.NOT_MEASURED,
        "thresholds": {"changeDays": math.CHANGE_DAYS, "budgetMovePercent": int(math.BUDGET_MOVE * 100), "orderPercent": int(math.ORDER_MEDIUM * 100), "capPercent": int(math.CAP_MEDIUM * 100),
                       "teamDomains": list(math.TEAM_DOMAINS)},
        "summary": {"counts": counts, "accounts": len(accounts), "high": sum(a["counts"]["high"] for a in accounts), "medium": sum(a["counts"]["medium"] for a in accounts),
                    "low": sum(a["counts"]["low"] for a in accounts), "findings": sum(a["findingCount"] for a in accounts),
                    "notActive": sum(1 for a in accounts if any(f["rule"] in ("account_status", "account_gone") for f in a["findings"]))},
    }


def load_raw(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, at=None, force=False, store=None, today=None, meta_pages_fn=None, meta_account_fn=None, meta_get_fn=None):
    """(raw, stale, account): the saved read of an account, read from the platform when there is none (or when `force`). `now` is the cache's clock (seconds), `at` the time the read counts days from."""
    from . import paid_geo, paid_meta, paid_store
    store = store or paid_store
    day = today or dt.date.today()
    if paid_meta.is_meta(str(account_id or "")):
        act = paid_meta.norm_id(account_id)
        pages = meta_pages_fn or (lambda path, params: paid_meta._pages(path, params, store))
        acct_fn = meta_account_fn or (lambda a: paid_meta.account_by_id(store, a) or {})
        account = {"id": act, "name": (acct_fn(act) or {}).get("name") or act, "currency": (acct_fn(act) or {}).get("currency") or "USD", "platform": "meta"}
        raw, stale = paid_geo._cached("watch", act, "days", day, day, lambda: meta_raw(act, pages_fn=pages, account_fn=acct_fn, get_fn=meta_get_fn or (lambda p, q: paid_meta._get(p, q, store)), now=at),
                                      force=force, store=store, now=now)
    else:
        account = seg.resolve_target(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn)
        raw, stale = paid_geo._cached("watch", account["id"], "days", day, day, lambda: google_raw(account, search_fn=search_fn, now=at), force=force, store=store, now=now)
    return raw, stale, account


def report_for(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, at=None, force=False, store=None, today=None, mon=None, meta_pages_fn=None, meta_account_fn=None,
               meta_get_fn=None):
    """The Account Watch report for a Google Ads account or an MCC the root MCC holds, or for a Meta ad account (act_...). `force` (Refresh) skips the saved read, at most once per
    paid_gads.REFRESH_COOLDOWN_S for the account. `mon` is the monitors' store: with it who has access, the goals and the Pages are held against the monitor's earlier daily readings."""
    raw, stale, account = load_raw(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn, search_fn=search_fn, now=now, at=at, force=force, store=store, today=today,
                                   meta_pages_fn=meta_pages_fn, meta_account_fn=meta_account_fn, meta_get_fn=meta_get_fn)
    report = assemble(raw, account, history_fn=lambda aid, day: history_for(mon, aid, day), now=at)
    if raw.get("readAt"):
        report["readAt"] = raw["readAt"]
    return dict(report, stale=stale) if stale else report
