"""Paid Marketing Agents: Tracking & Data Health, the reading and the report.

For a Google Ads account (or every account of an MCC) or a Meta ad account this reads what shows whether the account's measurement still works, and the rules (tracker/paid_track_math.py)
hold the last days against the 21 before them. Nothing is changed in an account.

Google: the account's cost and conversions by day, its conversion actions and what each recorded by day, its audience lists with their sizes, whether the customer data terms are accepted and the
offline conversion upload log. The days are the point (a failure there is raised); every other read is soft (a refusal leaves that check out and says so). The account's own time zone decides which
day is "today". Meta: the campaigns' insights by day (the platform's reader), the ad account's pixels (when each last fired and whether it is unavailable) and its custom audiences' sizes, soft.

The audience lists are held against the monitor's own earlier readings (a daily snapshot per account), so a list is judged from the second day the monitor has seen it. The read is cached like the
other agents (tracker/paid_geo.py _cached). The field names of the Google queries and the Meta ones are the platforms' documented ones; the ones new here are checked by the admin live query check
(tracker/paid_gaql_check.py), not by a test.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from . import paid_gads_segments as gads
from . import paid_segments as seg
from . import paid_track_math as math

BadRequest = seg.BadRequest
AGENT = "track"
READ_DAYS = 30                       # days read before today: the 28 the rules use and two spare for a time zone ahead of the server's

GAQL_DAYS = "SELECT customer.time_zone, segments.date, metrics.cost_micros, metrics.conversions FROM customer WHERE segments.date BETWEEN '%s' AND '%s'"
GAQL_ACTIONS = ("SELECT conversion_action.id, conversion_action.name, conversion_action.status, conversion_action.type, conversion_action.primary_for_goal FROM conversion_action "
                "WHERE conversion_action.status != REMOVED")
GAQL_ACTION_DAYS = "SELECT segments.conversion_action, segments.date, metrics.conversions FROM customer WHERE segments.date BETWEEN '%s' AND '%s'"
GAQL_LISTS = ("SELECT user_list.id, user_list.name, user_list.type, user_list.membership_status, user_list.size_for_search, user_list.size_for_display FROM user_list")
GAQL_TERMS = "SELECT customer.conversion_tracking_setting.accepted_customer_data_terms FROM customer"
GAQL_UPLOADS = ("SELECT offline_conversion_upload_client_summary.client, offline_conversion_upload_client_summary.status, offline_conversion_upload_client_summary.last_upload_date_time "
                "FROM offline_conversion_upload_client_summary")
UPLOAD_TYPES = {"UPLOAD_CLICKS", "UPLOAD_CALLS", "STORE_SALES_UPLOAD_FIRST_PARTY", "STORE_SALES_UPLOAD_THIRD_PARTY", "STORE_SALES_DIRECT_UPLOAD"}

META_PIXEL_FIELDS = "id,name,last_fired_time,is_unavailable"
META_AUDIENCE_FIELDS = "id,name,subtype,approximate_count_lower_bound,approximate_count_upper_bound"


def _get(row, *path, default=None):
    for p in path:
        if not isinstance(row, dict):
            return default
        row = row.get(p)
    return default if row is None else row


def _num(v, default=0.0):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if x == x else default


# ── Reading Google's rows ───────────────────────────────────────────────────

def read_days(rows):
    """([{date, cost, conv}], time zone name or ""): the account's totals by day."""
    out, tz = {}, ""
    for r in rows:
        day = str(_get(r, "segments", "date", default="") or "")
        tz = tz or str(_get(r, "customer", "timeZone", default="") or "")
        if not day:
            continue
        m = r.get("metrics") or {}
        d = out.setdefault(day, {"date": day, "cost": 0.0, "conv": 0.0})
        d["cost"] = round(d["cost"] + _num(m.get("costMicros")) / 1e6, 4)
        d["conv"] += _num(m.get("conversions"))
    return sorted(out.values(), key=lambda d: d["date"]), tz


def read_actions(rows, day_rows):
    """[{id, name, status, type, primary, days: [{date, conv}]}]: the conversion actions with what each recorded by day."""
    by = {}
    for r in day_rows:
        aid = str(_get(r, "segments", "conversionAction", default="") or "").rsplit("/", 1)[-1]
        day = str(_get(r, "segments", "date", default="") or "")
        if aid and day:
            by.setdefault(aid, {})[day] = by.get(aid, {}).get(day, 0.0) + _num(_get(r, "metrics", "conversions"))
    out = []
    for r in rows:
        c = r.get("conversionAction") or {}
        aid = str(c.get("id") or "")
        if not aid:
            continue
        out.append({"id": aid, "name": c.get("name") or aid, "status": c.get("status") or "", "type": c.get("type") or "", "primary": bool(c.get("primaryForGoal")),
                    "days": [{"date": d, "conv": v} for d, v in sorted(by.get(aid, {}).items())]})
    return out


def read_lists(rows):
    """[{id, name, size, open, type}]: Google's audience lists, the size being the larger of the search and display sizes."""
    out = []
    for r in rows:
        u = r.get("userList") or {}
        lid = str(u.get("id") or "")
        if not lid:
            continue
        out.append({"id": lid, "name": u.get("name") or lid, "type": u.get("type") or "", "open": u.get("membershipStatus") == "OPEN",
                    "size": int(max(_num(u.get("sizeForSearch")), _num(u.get("sizeForDisplay"))))})
    return out


def read_uploads(rows, actions):
    """{hasUploadActions, status, lastUpload}: the offline upload log's newest row, and whether an offline conversion action is enabled."""
    has = any(str(a.get("status") or "") == "ENABLED" and str(a.get("type") or "") in UPLOAD_TYPES for a in actions or [])
    best = None
    for r in rows:
        s = r.get("offlineConversionUploadClientSummary") or {}
        last = str(s.get("lastUploadDateTime") or "")
        if best is None or last > best[0]:
            best = (last, str(s.get("status") or ""))
    return {"hasUploadActions": has, "status": best[1] if best else "", "lastUpload": best[0] if best else ""}


def local_day(tz_name, now):
    """The account's date at `now` (a UTC time); an unknown or missing zone leaves the UTC date."""
    if tz_name:
        try:
            return now.astimezone(ZoneInfo(tz_name)).date()
        except Exception:                                                            # noqa: BLE001 (an unknown zone name must not stop the read)
            pass
    return now.date()


def _soft(warnings, what, fn, default=None):
    try:
        return fn()
    except gads.GadsError as e:
        warnings.append("%s could not be read (%s), so that check was skipped." % (what, e))
        return default


def read_account(account_id, search, now=None):
    """{days, actions, lists, terms, uploads, today (ISO date of the account), warnings}: one Google Ads account. The days are raised on failure; the rest is soft."""
    now = now or dt.datetime.now(dt.timezone.utc)
    utc_today = now.date()
    first, last = (utc_today - dt.timedelta(days=READ_DAYS)).isoformat(), utc_today.isoformat()
    days, tz = read_days(search(account_id, GAQL_DAYS % (first, last)))
    warnings = []
    action_rows = _soft(warnings, "The conversion actions", lambda: search(account_id, GAQL_ACTIONS))
    action_days = _soft(warnings, "What each conversion action recorded by day", lambda: search(account_id, GAQL_ACTION_DAYS % (first, last))) if action_rows is not None else None
    actions = read_actions(action_rows, action_days) if action_rows is not None and action_days is not None else None
    lists = _soft(warnings, "The audience lists", lambda: read_lists(search(account_id, GAQL_LISTS)))
    terms_rows = _soft(warnings, "Whether the customer data terms are accepted", lambda: search(account_id, GAQL_TERMS))
    terms = None if not terms_rows else _get(terms_rows[0], "customer", "conversionTrackingSetting", "acceptedCustomerDataTerms")
    upload_rows = _soft(warnings, "The offline conversion upload log", lambda: search(account_id, GAQL_UPLOADS)) if actions is not None else None
    return {"days": days, "actions": actions, "lists": lists, "terms": None if terms is None else bool(terms), "uploads": None if upload_rows is None else read_uploads(upload_rows, actions),
            "pixels": None, "today": local_day(tz, now).isoformat(), "warnings": warnings}


# ── Meta ────────────────────────────────────────────────────────────────────

def meta_pixels_request(act):
    return "/%s/adspixels" % act, {"fields": META_PIXEL_FIELDS, "limit": 100}


def meta_audiences_request(act):
    return "/%s/customaudiences" % act, {"fields": META_AUDIENCE_FIELDS, "limit": 200}


def parse_pixels(rows):
    return [{"id": str(p.get("id")), "name": p.get("name") or str(p.get("id")), "lastFired": str(p.get("last_fired_time") or ""), "unavailable": bool(p.get("is_unavailable"))} for p in rows if p.get("id")]


def parse_audiences(rows):
    """[{id, name, size, open}]: Meta's custom audiences; one whose size Meta does not give (a negative or missing bound) is left out, so it is never mistaken for an empty one."""
    out = []
    for a in rows:
        try:
            size = int(a.get("approximate_count_lower_bound"))
        except (TypeError, ValueError):
            continue
        if size < 0 or not a.get("id"):
            continue
        out.append({"id": str(a["id"]), "name": a.get("name") or str(a["id"]), "size": size, "open": True, "type": a.get("subtype") or ""})
    return out


def read_meta_account(act, pages, now=None):
    """The same shape as read_account, for a Meta ad account; the days come from the platform's insights reader."""
    from . import paid_meta
    utc_today = (now or dt.datetime.now(dt.timezone.utc)).date()
    first, last = (utc_today - dt.timedelta(days=READ_DAYS)).isoformat(), (utc_today - dt.timedelta(days=1)).isoformat()
    by = {}
    for r in pages("/%s/insights" % act, paid_meta.q_insights(first, last)):
        d = r.get("date_start")
        if not d:
            continue
        n = paid_meta.numbers(r)
        x = by.setdefault(d, {"date": d, "cost": 0.0, "conv": 0.0})
        x["cost"] = round(x["cost"] + n[2], 4)
        x["conv"] += n[3]
    warnings = []
    pixels = lists = None
    try:
        pixels = parse_pixels(pages(*meta_pixels_request(act)))
    except Exception as e:                                                           # noqa: BLE001 (Meta has its own errors; the pixels are soft)
        warnings.append("The pixels could not be read (%s), so that check was skipped." % str(e)[:160])
    try:
        lists = parse_audiences(pages(*meta_audiences_request(act)))
    except Exception as e:                                                           # noqa: BLE001
        warnings.append("The custom audiences could not be read (%s), so that check was skipped." % str(e)[:160])
    return {"days": sorted(by.values(), key=lambda d: d["date"]), "actions": None, "lists": lists, "terms": None, "uploads": None, "pixels": pixels, "today": utc_today.isoformat(), "warnings": warnings}


def google_raw(account, *, search_fn=None, workers=6, now=None):
    """{accounts: [{id, name, currency, platform, ...the read}], warnings, members}: one account or an MCC's accounts. An account whose days cannot be read is left out and said so; every one
    failing is raised."""
    search = search_fn or gads.search
    members = account.get("members") or [account]

    def run(member):
        try:
            return member, read_account(member["id"], search, now), None
        except gads.GadsError as e:
            return member, None, e
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(members)))) as pool:
        done = list(pool.map(run, members))
    ok = [(mem, r) for mem, r, e in done if r is not None]
    if not ok:
        raise next(e for _m, _r, e in done if e)
    many = bool(account.get("members"))
    return {"accounts": [dict(r, id=mem["id"], name=mem["name"], currency=mem.get("currency") or account.get("currency") or "USD", platform="google") for mem, r in ok],
            "warnings": ["%s could not be read: %s" % (mem["name"], e) for mem, r, e in done if e] + [("%s: %s" % (mem["name"], w)) if many else w for mem, r in ok for w in r["warnings"]],
            "members": [mem["name"] for mem in members] if many else []}


def meta_raw(act, *, pages_fn, account_fn, now=None):
    acct = account_fn(act) or {}
    got = read_meta_account(act, pages_fn, now)
    return {"accounts": [dict(got, id=act, name=acct.get("name") or act, currency=acct.get("currency") or "USD", platform="meta")], "warnings": list(got["warnings"]), "members": []}


# ── The report ──────────────────────────────────────────────────────────────

_RANK = {"fix": 0, "check": 1, "clean": 2}


def snapshot_of(a, res=None):
    """What the monitor saves of one account each day: the audience lists' sizes (what a later day is held against) and the outcome."""
    snap = {"lists": {x["id"]: {"name": x["name"], "size": x["size"]} for x in a.get("lists") or [] if x.get("size") is not None and x.get("open", True)}}
    if res is not None:
        snap.update(status=res["status"], high=res["counts"]["high"], medium=res["counts"]["medium"], low=res["counts"]["low"], findings=res["findingCount"])
    return snap


def history_for(mon, account_id, today):
    """The monitor's earlier readings of an account (oldest first), or [] when there are none or the store cannot be read."""
    if mon is None:
        return []
    try:
        return mon.snaps_get(AGENT, str(account_id), (today - dt.timedelta(days=math.HISTORY_DAYS + 1)).isoformat())
    except Exception:                                                                # noqa: BLE001 (without a history the lists are listed, not judged)
        return []


def assemble(raw, account, *, history_fn=None, now=None):
    """The report from the raw read. `history_fn(account id, today)` gives the monitor's earlier readings of the account."""
    now = now or dt.datetime.now(dt.timezone.utc)
    accounts, warnings = [], list(raw.get("warnings") or [])
    for a in raw["accounts"]:
        today = dt.date.fromisoformat(a.get("today") or now.date().isoformat())
        platform = a.get("platform") or "google"
        res = math.analyse(a, history=history_fn(a["id"], today) if history_fn else [], today=today, now=now, platform=platform)
        accounts.append(dict(res, id=a["id"], name=a["name"], currency=a.get("currency") or "USD", platform=platform, today=a.get("today") or "",
                             counts_read={"actions": None if a.get("actions") is None else len(a["actions"]), "lists": None if a.get("lists") is None else len(a["lists"]),
                                          "pixels": None if a.get("pixels") is None else len(a["pixels"])}))
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
        "thresholds": {"expected": math.MIN_EXPECTED, "shrinkPercent": int(math.SHRINK * 100), "listMin": math.LIST_MIN, "uploadDays": math.UPLOAD_STALE_DAYS, "pixelHours": math.PIXEL_MEDIUM_HOURS,
                       "baselineDays": math.BASE_LEN},
        "summary": {"counts": counts, "accounts": len(accounts), "high": sum(a["counts"]["high"] for a in accounts), "medium": sum(a["counts"]["medium"] for a in accounts),
                    "low": sum(a["counts"]["low"] for a in accounts), "findings": sum(a["findingCount"] for a in accounts),
                    "dark": sum(1 for a in accounts if any(f["rule"] == "no_conv" for f in a["findings"]))},
    }


def load_raw(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, at=None, force=False, store=None, today=None, meta_pages_fn=None, meta_account_fn=None):
    """(raw, stale, account): the saved read of an account, read from the platform when there is none (or when `force`). `now` is the cache's clock (seconds), `at` the time the read counts days from."""
    from . import paid_geo, paid_meta, paid_store
    store = store or paid_store
    day = today or dt.date.today()
    if paid_meta.is_meta(str(account_id or "")):
        act = paid_meta.norm_id(account_id)
        pages = meta_pages_fn or (lambda path, params: paid_meta._pages(path, params, store))
        acct_fn = meta_account_fn or (lambda a: paid_meta.account_by_id(store, a) or {})
        account = {"id": act, "name": (acct_fn(act) or {}).get("name") or act, "currency": (acct_fn(act) or {}).get("currency") or "USD", "platform": "meta"}
        raw, stale = paid_geo._cached("track", act, "days", day, day, lambda: meta_raw(act, pages_fn=pages, account_fn=acct_fn, now=at), force=force, store=store, now=now)
    else:
        account = seg.resolve_target(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn)
        raw, stale = paid_geo._cached("track", account["id"], "days", day, day, lambda: google_raw(account, search_fn=search_fn, now=at), force=force, store=store, now=now)
    return raw, stale, account


def report_for(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, at=None, force=False, store=None, today=None, mon=None, meta_pages_fn=None, meta_account_fn=None):
    """The Tracking & Data Health report for a Google Ads account or an MCC the root MCC holds, or for a Meta ad account (act_...). `force` (Refresh) skips the saved read, at most once per
    paid_gads.REFRESH_COOLDOWN_S for the account. `mon` is the monitors' store: with it the lists are held against the monitor's earlier daily readings."""
    raw, stale, account = load_raw(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn, search_fn=search_fn, now=now, at=at, force=force, store=store, today=today,
                                   meta_pages_fn=meta_pages_fn, meta_account_fn=meta_account_fn)
    report = assemble(raw, account, history_fn=lambda aid, day: history_for(mon, aid, day), now=at)
    if raw.get("readAt"):
        report["readAt"] = raw["readAt"]
    return dict(report, stale=stale) if stale else report
