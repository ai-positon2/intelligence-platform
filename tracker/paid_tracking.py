"""Paid Marketing Agents: Tracking Data Health, the reading and the report.

For a Google Ads account (or every account of an MCC) or a Meta ad account this reads the last five weeks of clicks, cost and conversions by day, and what the conversions depend on; the rules
(tracker/paid_tracking_math.py) say whether the numbers can still be trusted. Nothing is changed in an account.

Google: the account's days (customer.time_zone, segments.date, clicks, cost, conversions FROM customer) are the point, and a failure there is raised. Read softly, each a refusal of which
becomes a "not run" line instead of a failure: the conversions of each conversion action by day with the enabled actions (status, category, whether primary for its goal), whether
auto-tagging is on, and the open user lists' sizes for Search. Five reads an account, all run live against a real account on 2026-10-26.
Meta: the campaigns' insights by day (the platform's reader and its idea of a conversion, the objective's result), and softly the ad account's pixels (last_fired_time) and its custom
audiences (approximate_count_lower_bound). The Meta field names are Meta's documented ones; none has been run against a live Meta account from here.

The read is cached like the other agents (tracker/paid_geo.py _cached); what the team saved and the list sizes the daily check kept are applied each time, so a change on the page shows at once.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from . import paid_gads_segments as gads
from . import paid_segments as seg
from . import paid_tracking_math as math

BadRequest = seg.BadRequest
AGENT = "tracking"
READ_DAYS = math.READ_DAYS + 2          # two spare for a time zone ahead of the server's

GAQL_DAYS = ("SELECT customer.time_zone, segments.date, metrics.clicks, metrics.cost_micros, metrics.conversions FROM customer "
             "WHERE segments.date BETWEEN '%s' AND '%s'")
GAQL_ACTION_DAYS = ("SELECT segments.conversion_action, segments.conversion_action_name, segments.date, metrics.conversions FROM customer "
                    "WHERE segments.date BETWEEN '%s' AND '%s'")
GAQL_ACTIONS = ("SELECT conversion_action.id, conversion_action.name, conversion_action.status, conversion_action.category, conversion_action.primary_for_goal, conversion_action.type "
                "FROM conversion_action WHERE conversion_action.status = 'ENABLED'")
GAQL_TAGGING = "SELECT customer.auto_tagging_enabled FROM customer"
GAQL_LISTS = ("SELECT user_list.id, user_list.name, user_list.type, user_list.size_for_search, user_list.membership_status FROM user_list "
              "WHERE user_list.membership_status = 'OPEN'")

META_PIXEL_FIELDS = "id,name,last_fired_time,is_unavailable"
META_AUDIENCE_FIELDS = "id,name,subtype,approximate_count_lower_bound"
PARTS = {"actions": "The conversion actions", "tagging": "The auto-tagging setting", "lists": "The remarketing lists", "pixels": "The pixels", "audiences": "The custom audiences"}


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


def _tail(resource):
    return str(resource or "").rsplit("/", 1)[-1]


# ── Reading Google's rows ───────────────────────────────────────────────────

def read_days(rows):
    """([{date, clicks, cost, conv}], time zone name or "")."""
    out, tz = [], ""
    for r in rows:
        tz = tz or str(_get(r, "customer", "timeZone", default="") or "")
        day = str(_get(r, "segments", "date", default="") or "")
        if not day:
            continue
        m = r.get("metrics") or {}
        out.append({"date": day, "clicks": _num(m.get("clicks")), "cost": round(_num(m.get("costMicros")) / 1e6, 4), "conv": _num(m.get("conversions"))})
    return out, tz


def read_actions(action_rows, day_rows):
    """[{id, name, status, category, type, primary, days: {date: conversions}}]: the enabled actions, with what each recorded by day (an action that recorded nothing has no days)."""
    days = {}
    for r in day_rows:
        aid = _tail(_get(r, "segments", "conversionAction"))
        d = str(_get(r, "segments", "date", default="") or "")
        if aid and d:
            days.setdefault(aid, {})
            days[aid][d] = days[aid].get(d, 0.0) + _num(_get(r, "metrics", "conversions"))
    out = []
    for r in action_rows:
        a = r.get("conversionAction") or {}
        aid = str(a.get("id") or _tail(a.get("resourceName")))
        if not aid:
            continue
        out.append({"id": aid, "name": a.get("name") or aid, "status": a.get("status") or "", "category": a.get("category") or "", "type": a.get("type") or "",
                    "primary": bool(a.get("primaryForGoal")), "days": days.get(aid, {})})
    return out


def read_tagging(rows):
    for r in rows:
        v = _get(r, "customer", "autoTaggingEnabled")
        if v is not None:
            return bool(v)
    return None


def read_lists(rows):
    """[{id, name, kind, size, platform}]: the open user lists, sized for Search."""
    out = []
    for r in rows:
        u = r.get("userList") or {}
        lid = str(u.get("id") or _tail(u.get("resourceName")))
        if lid:
            out.append({"id": lid, "name": u.get("name") or lid, "kind": u.get("type") or "", "size": _num(u.get("sizeForSearch"), None), "platform": "google"})
    return out


def local_day(tz_name, now):
    """The account's date at `now` (UTC); an unknown or missing zone leaves the UTC date."""
    if tz_name:
        try:
            return now.astimezone(ZoneInfo(tz_name)).date()
        except Exception:                                                            # noqa: BLE001 (an unknown zone name must not stop the read)
            pass
    return now.date()


def _soft(parts, name, fn):
    """fn() or None, with why in parts[name] when it was refused (Google's errors and Meta's alike: a part is never worth the whole read)."""
    try:
        return fn()
    except Exception as e:                                                           # noqa: BLE001
        parts[name] = "%s could not be read (%s), so that check was not run." % (PARTS[name], str(e)[:160] or type(e).__name__)
        return None


def read_account(account_id, search, now=None):
    """One Google Ads account: {today, days, actions, autoTagging, lists, pixels: None, parts}. The days are raised on failure; the rest is soft."""
    now = now or dt.datetime.now(dt.timezone.utc)
    first, last = (now.date() - dt.timedelta(days=READ_DAYS)).isoformat(), now.date().isoformat()
    days, tz = read_days(search(account_id, GAQL_DAYS % (first, last)))
    parts = {}
    actions = _soft(parts, "actions", lambda: read_actions(search(account_id, GAQL_ACTIONS), search(account_id, GAQL_ACTION_DAYS % (first, last))))
    tagging = _soft(parts, "tagging", lambda: read_tagging(search(account_id, GAQL_TAGGING)))
    lists = _soft(parts, "lists", lambda: read_lists(search(account_id, GAQL_LISTS)))
    return {"today": local_day(tz, now).isoformat(), "days": days, "actions": actions, "autoTagging": tagging, "lists": lists, "pixels": None, "parts": parts}


# ── Meta ────────────────────────────────────────────────────────────────────

def meta_days(rows):
    """[{date, clicks, cost, conv}] from campaign insights by day: the campaigns added up, a conversion being each objective's result (the platform's own rule)."""
    from . import paid_meta
    by = {}
    for r in rows:
        d = r.get("date_start")
        if not d:
            continue
        n = paid_meta.numbers(r)
        cur = by.setdefault(d, {"date": d, "clicks": 0.0, "cost": 0.0, "conv": 0.0})
        cur["clicks"] += n[1]
        cur["cost"] += n[2]
        cur["conv"] += n[3]
    return [by[d] for d in sorted(by)]


def meta_pixels(rows):
    return [{"id": str(p.get("id")), "name": p.get("name") or str(p.get("id")), "lastFired": p.get("last_fired_time") or None} for p in rows if p.get("id") and not p.get("is_unavailable")]


def meta_audiences(rows):
    out = []
    for a in rows:
        if not a.get("id"):
            continue
        size = a.get("approximate_count_lower_bound")
        out.append({"id": str(a["id"]), "name": a.get("name") or str(a["id"]), "kind": str(a.get("subtype") or "").upper(), "size": _num(size, None) if size is not None and _num(size, -1) >= 0 else None,
                    "platform": "meta"})
    return out


def read_meta_account(act, pages, now=None):
    from . import paid_meta
    now = now or dt.datetime.now(dt.timezone.utc)
    first, last = (now.date() - dt.timedelta(days=READ_DAYS)).isoformat(), (now.date() - dt.timedelta(days=1)).isoformat()
    days = meta_days(pages("/%s/insights" % act, paid_meta.q_insights(first, last)))
    parts = {}
    pixels = _soft(parts, "pixels", lambda: meta_pixels(pages("/%s/adspixels" % act, {"fields": META_PIXEL_FIELDS, "limit": 50})))
    lists = _soft(parts, "audiences", lambda: meta_audiences(pages("/%s/customaudiences" % act, {"fields": META_AUDIENCE_FIELDS, "limit": 200})))
    return {"today": now.date().isoformat(), "days": days, "actions": None, "autoTagging": None, "lists": lists, "pixels": pixels, "parts": parts}


def google_raw(account, *, search_fn=None, workers=6, now=None):
    """{accounts: [{id, name, currency, platform, ...the read}], warnings, members}: one account or an MCC's accounts. An account whose days cannot be read is left out and said so; every
    one failing is raised."""
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
            "warnings": ["%s could not be read: %s" % (mem["name"], e) for mem, r, e in done if e]
                        + [("%s: %s" % (mem["name"], w)) if many else w for mem, r in ok for w in r["parts"].values()],
            "members": [mem["name"] for mem in members] if many else []}


def meta_raw(act, *, pages_fn, account_fn, now=None):
    acct = account_fn(act) or {}
    got = read_meta_account(act, pages_fn, now)
    return {"accounts": [dict(got, id=act, name=acct.get("name") or act, currency=acct.get("currency") or "USD", platform="meta")], "warnings": list(got["parts"].values()), "members": []}


# ── The report ──────────────────────────────────────────────────────────────

_RANK = {"broken": 0, "fix": 1, "check": 2, "healthy": 3}


def history_of(mon, account_id, today):
    """The list sizes the daily check kept for an account, oldest first ([] without the store)."""
    if mon is None:
        return []
    try:
        return mon.snaps_get(AGENT, account_id, (today - dt.timedelta(days=math.LIST_DAYS + 7)).isoformat())
    except Exception:                                                                # noqa: BLE001 (without the history the list trends wait, nothing else does)
        return []


def judge(a, *, settings=None, mon=None, now=None):
    """One read account analysed with what the team saved and the history: the analysis plus who it is and what was not run."""
    from . import paid_tracking_settings as st
    settings = settings or st.empty()
    now = now or dt.datetime.now(dt.timezone.utc)
    cfg = st.account_of(settings, a["id"])
    today = dt.date.fromisoformat(a.get("today") or now.date().isoformat())
    res = math.analyse(a, config=cfg, history=history_of(mon, a["id"], today), now=now)
    return dict(res, id=a["id"], name=a["name"], currency=a.get("currency") or "USD", platform=a.get("platform") or "google", today=a.get("today") or "", owner=cfg["owner"],
                windows=cfg["windows"], notRun=list((a.get("parts") or {}).values()))


def assemble(raw, account, *, settings=None, mon=None, now=None):
    """The report from the raw read."""
    now = now or dt.datetime.now(dt.timezone.utc)
    accounts = [judge(a, settings=settings, mon=mon, now=now) for a in raw["accounts"]]
    accounts.sort(key=lambda a: (_RANK[a["status"]], a["score"], a["name"].lower()))
    counts = {k: sum(1 for a in accounts if a["status"] == k) for k in _RANK}
    return {
        "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or "USD", "platform": account.get("platform") or "google",
                    "isGroup": bool(account.get("members")), "accountCount": len(account.get("members") or [])},
        "generatedAt": now.isoformat(timespec="seconds"), "warnings": list(raw.get("warnings") or []), "accounts": accounts,
        "rules": [{"rule": k, "label": math.RULES[k]["label"], "fix": math.RULES[k]["fix"]} for k in math.ORDER],
        "minimums": {"stopDaily": math.STOP_MIN_DAILY, "drop": int(math.DROP * 100), "cvrDrop": int(math.CVR_DROP * 100), "baseDays": math.BASE_DAYS, "minBaseDays": math.MIN_BASE_DAYS,
                     "listMin": math.LIST_MIN, "listDrop": int(math.LIST_DROP * 100), "listDays": math.LIST_DAYS, "pixelHours": math.PIXEL_HOURS, "ackDays": math.ACK_DAYS},
        "summary": {"counts": counts, "accounts": len(accounts), "critical": sum(a["counts"]["critical"] for a in accounts), "high": sum(a["counts"]["high"] for a in accounts),
                    "medium": sum(a["counts"]["medium"] for a in accounts), "low": sum(a["counts"]["low"] for a in accounts),
                    "scoreTotal": sum(a["score"] for a in accounts)},
    }


def load_raw(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, at=None, force=False, store=None, today=None, meta_pages_fn=None, meta_account_fn=None):
    """(raw, stale, account): the saved read, read from the platform when there is none (or when `force`)."""
    from . import paid_geo, paid_meta, paid_store
    store = store or paid_store
    day = today or dt.date.today()
    if paid_meta.is_meta(str(account_id or "")):
        act = paid_meta.norm_id(account_id)
        pages = meta_pages_fn or (lambda path, params: paid_meta._pages(path, params, store))
        acct_fn = meta_account_fn or (lambda a: paid_meta.account_by_id(store, a) or {})
        info = acct_fn(act) or {}
        account = {"id": act, "name": info.get("name") or act, "currency": info.get("currency") or "USD", "platform": "meta"}
        raw, stale = paid_geo._cached("tracking", act, "days", day, day, lambda: meta_raw(act, pages_fn=pages, account_fn=acct_fn, now=at), force=force, store=store, now=now)
    else:
        account = seg.resolve_target(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn)
        raw, stale = paid_geo._cached("tracking", account["id"], "days", day, day, lambda: google_raw(account, search_fn=search_fn, now=at), force=force, store=store, now=now)
    return raw, stale, account


def report_for(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, at=None, force=False, store=None, today=None, mon=None, meta_pages_fn=None, meta_account_fn=None):
    """The Tracking Data Health report for a Google Ads account, an MCC, or a Meta ad account (act_...). `force` (Refresh) skips the saved read, at most once per
    paid_gads.REFRESH_COOLDOWN_S. `mon` is the monitors' store: what the team saved and the list sizes the daily check kept come from it."""
    from . import paid_tracking_settings as st
    warnings, settings = [], None
    if mon is not None:
        try:
            settings = st.load(mon)
        except Exception as e:                                                       # noqa: BLE001
            warnings.append("What the team saved (owners, expected drops, muted items) could not be read (%s), so it was not applied." % e)
    raw, stale, account = load_raw(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn, search_fn=search_fn, now=now, at=at, force=force, store=store, today=today,
                                   meta_pages_fn=meta_pages_fn, meta_account_fn=meta_account_fn)
    report = assemble(raw, account, settings=settings, mon=mon, now=at)
    report["warnings"] = warnings + report["warnings"]
    if raw.get("readAt"):
        report["readAt"] = raw["readAt"]
    return dict(report, stale=stale) if stale else report
