"""Paid Marketing Agents: Spend Anomaly Alert, the reading and the report.

For a Google Ads account, every account of an MCC, or a Meta ad account this reads the account's spend by day and by hour (one read per account: the customer's cost by date and hour for the
last 15 days and today; Meta's insights by day, and by hour when Meta will give it) and holds it against the account's own usual (tracker/paid_anomaly_math.py). Only an account that looks
wrong is read further: its campaigns by day (what spent the excess), its budgets (Google) and the changes made in it lately (Google), so a healthy account costs one read.

The time is the account's own: Google and Meta both report days and hours in the account's time zone, so "today" and "this hour" are worked out there. Google reports the latest hours late;
the math leaves them out where a missing hour would look like a drop. Nothing is changed in any account.

The read is kept in memory for ten minutes (a spike is about now, so the hour-long copy the other agents keep would be too old), Refresh reads again at most every two minutes per account,
and what the team saved (usual spend, a ceiling, a muted push, the sensitivity) is applied to the saved read on every request, so changing it never reads Google again.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import paid_anomaly_math as m
from . import paid_anomaly_plan as plan
from . import paid_gads_segments as gads
from . import paid_segments as seg

BadRequest = seg.BadRequest
MAX_MEMBERS = seg.MAX_MEMBERS
CACHE_TTL_S = 600
CACHE_MAX = 400
WORKERS = 6
CHANGES_LIMIT = 1000
CHANGE_DAYS = 14
RECENT_HOURS = 48                  # changes this recent are the ones named beside a spike
SHOWN_CHANGES = 8
EXTRA_LEVELS = ("low", "medium", "high")

# ── The Google queries ──────────────────────────────────────────────────────

GAQL_HOURLY = "SELECT customer.time_zone, segments.date, segments.hour, metrics.cost_micros FROM customer WHERE segments.date BETWEEN '%s' AND '%s'"
GAQL_DAILY = "SELECT customer.time_zone, segments.date, metrics.cost_micros FROM customer WHERE segments.date BETWEEN '%s' AND '%s'"
GAQL_CAMPAIGN_DAYS = ("SELECT campaign.id, campaign.name, campaign.start_date_time, segments.date, metrics.cost_micros FROM campaign "
                      "WHERE segments.date BETWEEN '%s' AND '%s' AND metrics.cost_micros > 0")
GAQL_BUDGETS = ("SELECT campaign.id, campaign_budget.id, campaign_budget.amount_micros, campaign_budget.explicitly_shared FROM campaign "
                "WHERE campaign.status = 'ENABLED'")
GAQL_CHANGES = ("SELECT change_event.change_date_time, change_event.user_email, change_event.client_type, change_event.change_resource_type, change_event.resource_change_operation, "
                "change_event.changed_fields, change_event.old_resource, change_event.new_resource FROM change_event "
                "WHERE change_event.change_date_time >= '%s' AND change_event.change_date_time <= '%s' ORDER BY change_event.change_date_time DESC LIMIT " + str(CHANGES_LIMIT))


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def parse_hourly(api_rows):
    """(time zone name or "", [{date, hour, cost}]) from the customer read; `hour` is None for the daily form."""
    tz, out = "", []
    for r in api_rows:
        seg_ = r.get("segments") or {}
        tz = tz or str((r.get("customer") or {}).get("timeZone") or "")
        day = str(seg_.get("date") or "")
        micros = _num((r.get("metrics") or {}).get("costMicros"))
        if not day or micros is None:
            continue
        hour = seg_.get("hour")
        out.append({"date": day, "hour": None if hour is None else int(hour), "cost": round(micros / 1e6, 4)})
    return tz, out


def parse_campaign_days(api_rows):
    out = []
    for r in api_rows:
        c, mt = r.get("campaign") or {}, r.get("metrics") or {}
        day, micros = str((r.get("segments") or {}).get("date") or ""), _num(mt.get("costMicros"))
        if c.get("id") and day and micros:
            out.append({"id": str(c["id"]), "name": c.get("name") or str(c["id"]), "date": day, "cost": round(micros / 1e6, 4), "startDate": str(c.get("startDateTime") or "")[:10]})
    return out


def parse_budgets(api_rows):
    """The daily budgets of the enabled campaigns added up, a shared budget once; None when the read has none."""
    seen, total = set(), 0.0
    for r in api_rows:
        b = r.get("campaignBudget") or {}
        micros = _num(b.get("amountMicros"))
        if micros is None:
            continue
        if b.get("explicitlyShared"):
            if b.get("id") in seen:
                continue
            seen.add(b.get("id"))
        total += micros / 1e6
    return round(total, 2) or None


_CHANGE_WORDS = {"CAMPAIGN_BUDGET": "a budget", "CAMPAIGN": "a campaign", "BIDDING_STRATEGY": "a bidding strategy", "AD_GROUP": "an ad group"}
_CLIENTS = {"GOOGLE_ADS_WEB_CLIENT": "the Google Ads website", "GOOGLE_ADS_AUTOMATED_RULE": "an automated rule", "GOOGLE_ADS_SCRIPTS": "a Google Ads script", "GOOGLE_ADS_BULK_UPLOAD": "a bulk upload",
            "GOOGLE_ADS_EDITOR": "Google Ads Editor", "GOOGLE_ADS_MOBILE_APP": "the Google Ads app", "GOOGLE_ADS_RECOMMENDATIONS": "an applied recommendation", "GOOGLE_ADS_API": "the Google Ads API",
            "GOOGLE_ADS_RECOMMENDATIONS_SUBSCRIPTION": "an automatically applied recommendation", "SEARCH_ADS_360_SYNC": "Search Ads 360", "INTERNAL_TOOL": "a Google tool"}


def _amount(resource, *path):
    for p in path:
        resource = (resource or {}).get(p) if isinstance(resource, dict) else None
    return _num(resource)


def _label(kind, op, fields, old, new, currency):
    from .paid_brandspend_math import money
    word = _CHANGE_WORDS.get(kind)
    if word is None:
        return None
    op = str(op or "")
    if op == "CREATE":
        return "%s created" % word
    if op == "REMOVE":
        return "%s removed" % word
    names = {f.strip() for f in str(fields or "").replace(",", " ").split() if f.strip()}
    if kind == "CAMPAIGN_BUDGET" and ({"amountMicros", "amount_micros"} & names):
        was, now = _amount(old, "campaignBudget", "amountMicros"), _amount(new, "campaignBudget", "amountMicros")
        if was is not None and now is not None and now != was:
            return "a budget %s from %s to %s" % ("raised" if now > was else "lowered", money(was / 1e6, currency), money(now / 1e6, currency))
        return "a budget amount changed"
    if kind == "CAMPAIGN" and "status" in names:
        return "a campaign switched on or off"
    if kind == "CAMPAIGN" and ({"biddingStrategyType", "bidding_strategy_type", "targetCpa", "targetRoas", "maximizeConversions", "maximizeConversionValue", "manualCpc"} & names):
        return "a campaign's bidding changed"
    return None


def parse_changes(api_rows, now, currency="USD"):
    """(changes, truncated): [{at, user, label, newUser}] of the budgets, campaigns, bidding strategies and ad groups changed in the last RECENT_HOURS, newest first, from the change
    history; `newUser` says the person made no change in the days before (only claimed when the read was not cut short). `now` is the account's local time."""
    cut = now - dt.timedelta(hours=RECENT_HOURS)
    parsed = []
    for r in api_rows:
        e = r.get("changeEvent") or {}
        try:
            at = dt.datetime.strptime(str(e.get("changeDateTime") or "")[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        user = str(e.get("userEmail") or "").strip().lower()
        parsed.append((at, user, str(e.get("clientType") or ""), e))
    truncated = len(api_rows) >= CHANGES_LIMIT
    before = {u for at, u, _c, _e in parsed if at < cut and u}
    out = []
    for at, user, client, e in sorted(parsed, key=lambda p: p[0], reverse=True):
        if at < cut:
            continue
        label = _label(str(e.get("changeResourceType") or ""), e.get("resourceChangeOperation"), e.get("changedFields"), e.get("oldResource"), e.get("newResource"), currency)
        if label is None:
            continue
        who = user or _CLIENTS.get(client) or "an unknown source"
        out.append({"at": at.strftime("%Y-%m-%d %H:%M:%S"), "user": who, "label": label, "newUser": bool(user) and user not in before and not truncated})
    return out[:SHOWN_CHANGES], truncated


# ── The Meta queries ────────────────────────────────────────────────────────

META_HOUR_KEY = "hourly_stats_aggregated_by_advertiser_time_zone"


def meta_daily_request(act, first, last, hourly=False, level="account"):
    fields = "spend,date_start" if level == "account" else "campaign_id,campaign_name,spend,date_start"
    params = {"level": level, "time_increment": 1, "time_range": json.dumps({"since": first, "until": last}), "fields": fields, "limit": 500}
    if hourly:
        params["breakdowns"] = META_HOUR_KEY
    return "/%s/insights" % act, params


def parse_meta_rows(rows):
    """[{date, hour, cost}] from account insights rows, by day or by hour."""
    out = []
    for r in rows:
        day, cost = str(r.get("date_start") or ""), _num(r.get("spend"))
        if not day or cost is None:
            continue
        h = str(r.get(META_HOUR_KEY) or "")[:2]
        out.append({"date": day, "hour": int(h) if h.isdigit() else None, "cost": round(cost, 4)})
    return out


def parse_meta_campaigns(rows):
    out = []
    for r in rows:
        cost = _num(r.get("spend"))
        if r.get("campaign_id") and r.get("date_start") and cost:
            out.append({"id": str(r["campaign_id"]), "name": r.get("campaign_name") or str(r["campaign_id"]), "date": str(r["date_start"]), "cost": round(cost, 4), "startDate": ""})
    return out


# ── Time ────────────────────────────────────────────────────────────────────

def local_now(tz, now_utc):
    """(naive local datetime, warning or "") in an account's time zone; UTC with a warning when the zone is not known."""
    from zoneinfo import ZoneInfo
    try:
        if tz:
            return now_utc.astimezone(ZoneInfo(tz)).replace(tzinfo=None), ""
    except Exception:                                                                    # noqa: BLE001 (an unknown zone name is a warning, not a failure)
        pass
    return now_utc.astimezone(dt.timezone.utc).replace(tzinfo=None), ("The account's time zone is %s, so UTC was used for its day and hour." % ("not known" if not tz else "not recognised (%s)" % tz))


def _span(local):
    """(first, last) dates of a read that ends today in the account's own days."""
    return (local.date() - dt.timedelta(days=m.WINDOW_DAYS + 1)).isoformat(), (local.date() + dt.timedelta(days=1)).isoformat()


# ── One account ─────────────────────────────────────────────────────────────

def _soft(warnings, what, fn, default=None):
    try:
        return fn()
    except gads.GadsError as e:
        warnings.append("%s could not be read (%s)." % (what, e))
        return default
    except Exception as e:                                                               # noqa: BLE001 (Meta has its own errors; the extras never cost the account)
        warnings.append("%s could not be read (%s: %s)." % (what, type(e).__name__, e))
        return default


def read_google(member, search, now_utc):
    """The raw read of one Google Ads account: {rows, hourly, tz, today, hour, warnings}. The hourly read is raised on failure unless the cause is the field itself, when the daily form is used."""
    warnings = []
    guess, _w = local_now("", now_utc)
    first, last = _span(guess)
    try:
        tz, rows = parse_hourly(search(member["id"], GAQL_HOURLY % (first, last)))
    except gads.GadsError as e:
        if e.kind in ("quota", "auth", "not_configured", "permission"):
            raise
        tz, rows = parse_hourly(search(member["id"], GAQL_DAILY % (first, last)))
        warnings.append("Google would not give the spend by hour (%s), so only whole days are judged." % e)
    local, warn = local_now(tz, now_utc)
    if warn:
        warnings.append(warn)
    return {"rows": rows, "hourly": any(r["hour"] is not None for r in rows), "tz": tz, "today": local.date().isoformat(), "hour": local.hour, "warnings": warnings, "local": local.strftime("%Y-%m-%d %H:%M")}


def read_google_extra(member, search, raw, now_utc, currency):
    """What explains a spike: {campaigns, budget, changes, warnings} read for an account that looks wrong. Each part is soft."""
    local = dt.datetime.strptime(raw["local"], "%Y-%m-%d %H:%M")
    first = (local.date() - dt.timedelta(days=m.HISTORY_DAYS + 1)).isoformat()
    warnings = []
    campaigns = _soft(warnings, "The campaigns' spend by day", lambda: parse_campaign_days(search(member["id"], GAQL_CAMPAIGN_DAYS % (first, (local.date() + dt.timedelta(days=1)).isoformat()))))
    budget = _soft(warnings, "The campaigns' budgets", lambda: parse_budgets(search(member["id"], GAQL_BUDGETS)))
    since = (local - dt.timedelta(days=CHANGE_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    until = (local + dt.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    changes = _soft(warnings, "The change history", lambda: parse_changes(search(member["id"], GAQL_CHANGES % (since, until)), local, currency))
    if changes is not None:
        changes, truncated = changes
        if truncated:
            warnings.append("The change history is long, so who had made no change before could not be told.")
    return {"campaigns": campaigns, "budget": budget, "changes": changes, "warnings": warnings}


def read_meta(act, pages, account, now_utc):
    """The raw read of one Meta ad account (the rows are required; the hours are tried and dropped when Meta refuses them)."""
    warnings = []
    tz = (account or {}).get("tz") or ""
    local, warn = local_now(tz, now_utc)
    if warn:
        warnings.append(warn)
    first, last = _span(local)
    rows = parse_meta_rows(pages(*meta_daily_request(act, first, last)))
    hours = _soft(warnings, "The spend by hour", lambda: parse_meta_rows(pages(*meta_daily_request(act, first, last, hourly=True))))
    hours = [r for r in (hours or []) if r["hour"] is not None]
    if hours:
        rows = hours
    elif hours is not None and not warnings:
        warnings.append("Meta gave no spend by hour, so only whole days are judged.")
    return {"rows": rows, "hourly": bool(hours), "tz": tz, "today": local.date().isoformat(), "hour": local.hour, "warnings": warnings, "local": local.strftime("%Y-%m-%d %H:%M")}


def read_meta_extra(act, pages, raw):
    local = dt.datetime.strptime(raw["local"], "%Y-%m-%d %H:%M")
    first = (local.date() - dt.timedelta(days=m.HISTORY_DAYS + 1)).isoformat()
    warnings = []
    campaigns = _soft(warnings, "The campaigns' spend by day", lambda: parse_meta_campaigns(pages(*meta_daily_request(act, first, local.date().isoformat(), level="campaign"))))
    return {"campaigns": campaigns, "budget": None, "changes": None, "warnings": warnings}


def analyse_raw(raw, *, saved=None, sensitivity=None):
    """The verdict on one raw read with what the team saved applied."""
    extra = raw.get("extra") or {}
    return m.analyse(raw["rows"], raw["today"], raw["hour"], currency=raw.get("currency") or "USD", sensitivity=sensitivity, saved=saved, campaigns=extra.get("campaigns"),
                     budget=extra.get("budget"), changes=extra.get("changes"))


def needs_extra(analysed):
    return any(f["kind"] in ("spike", "surge") and f["level"] in ("medium", "high") for f in analysed["findings"])


def read_account(member, *, search=None, pages=None, meta_account=None, now_utc=None, saved=None, sensitivity=None):
    """The raw read of one account with its extras when it looks wrong: {id, name, currency, platform, rows, hourly, tz, today, hour, local, extra, warnings, readAt}.
    `member` is {id, name, currency, platform}."""
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    meta = member.get("platform") == "meta"
    raw = read_meta(member["id"], pages, meta_account, now_utc) if meta else read_google(member, search or gads.search, now_utc)
    raw = dict(raw, id=member["id"], name=member["name"], currency=member.get("currency") or "USD", platform="meta" if meta else "google", extra=None)
    if needs_extra(analyse_raw(raw, saved=saved, sensitivity=sensitivity)):
        raw["extra"] = read_meta_extra(member["id"], pages, raw) if meta else read_google_extra(member, search or gads.search, raw, now_utc, raw["currency"])
        raw["warnings"] = raw["warnings"] + raw["extra"]["warnings"]
    return raw


# ── The report ──────────────────────────────────────────────────────────────

_cache = {}
_lock = threading.Lock()


def clear_cache():
    with _lock:
        _cache.clear()


def cached_read(member, *, force=False, store=None, clock=None, **kw):
    """One read per account per CACHE_TTL_S however many ask; Refresh (`force`) reads again, at most once per paid_gads.REFRESH_COOLDOWN_S for the account."""
    from . import paid_gads as base, paid_store
    store = store or paid_store
    key = (member.get("platform") or "google", member["id"])
    now = time.time() if clock is None else clock
    if force:
        base.refresh_guard(store, "anomaly:%s" % member["id"])
    else:
        with _lock:
            hit = _cache.get(key)
            if hit and now - hit[0] < CACHE_TTL_S:
                return hit[1]
    raw = dict(read_account(member, **kw), readAt=dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"))
    with _lock:
        _cache[key] = (now, raw)
        while len(_cache) > CACHE_MAX:
            _cache.pop(min(_cache, key=lambda k: _cache[k][0]))
    return raw


def snapshot_of(a):
    return m.snapshot_of(a)


def overall(accounts):
    """What every account of one currency has spent so far today against what is usual by now: {cost, usual, accounts, currency}; None when there are several currencies or no usual."""
    judged = [a for a in accounts if a["today"]["usualByNow"] is not None]
    cur = {a["currency"] for a in judged}
    if len(cur) != 1:
        return None
    return {"cost": round(sum(a["today"]["cost"] for a in judged), 2), "usual": round(sum(a["today"]["usualByNow"] for a in judged), 2), "accounts": len(judged), "currency": cur.pop()}


def assemble(raws, account, *, settings=None, now_utc=None):
    """The report from the raw reads, with what the team saved applied. `raws` [{raw read or {id, name, error}}]."""
    from . import paid_anomaly_settings as st
    settings = settings or st.empty()
    sens = settings.get("sensitivity")
    accounts, warnings = [], []
    for raw in raws:
        if raw.get("error"):
            warnings.append("%s could not be read: %s" % (raw["name"], raw["error"]))
            continue
        saved = st.account_of(settings, raw["id"])
        a = analyse_raw(raw, saved=saved, sensitivity=sens)
        accounts.append(dict(a, plan=plan.build(a), id=raw["id"], name=raw["name"], currency=raw["currency"], platform=raw["platform"], tz=raw["tz"], local=raw["local"], hourly=raw["hourly"], warnings=raw["warnings"],
                             readAt=raw.get("readAt")))
        warnings += ["%s: %s" % (raw["name"], w) if account.get("members") or len(raws) > 1 else w for w in raw["warnings"]]
    accounts.sort(key=m.rank_key)
    counts = {k: 0 for k in ("over", "under", "ok", "new", "none")}
    for a in accounts:
        counts[a["status"]] += 1
    return {"account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or "USD", "platform": account.get("platform") or "google", "isGroup": bool(account.get("members")),
                        "accountCount": len(account.get("members") or [])},
            "generatedAt": (now_utc or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds"), "sensitivity": m.sensitivity_of(sens), "warnings": warnings, "accounts": accounts,
            "summary": {"counts": counts, "accounts": len(accounts), "needsLook": counts["over"] + counts["under"],
                        "high": sum(1 for a in accounts if a["level"] == "high"), "overall": overall(accounts)}}


def report_for(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, store=None, mon=None, force=False, now_utc=None, meta_pages_fn=None, meta_account_fn=None, clock=None,
               workers=WORKERS):
    """The Spend Anomaly report for a Google Ads account, an MCC the root MCC holds, or a Meta ad account (act_...)."""
    from . import paid_anomaly_settings as st
    from . import paid_meta, paid_store
    store = store or paid_store
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    warnings, settings = [], None
    if mon is not None:
        try:
            settings = st.load(mon)
        except Exception as e:                                                       # noqa: BLE001
            warnings.append("What the team saved (usual spend, ceilings, muted days) could not be read (%s), so the defaults were used." % e)
    sens = (settings or {}).get("sensitivity")
    if paid_meta.is_meta(str(account_id or "")):
        act = paid_meta.norm_id(account_id)
        acct_fn = meta_account_fn or (lambda a: paid_meta.account_by_id(store, a) or {})
        info = acct_fn(act) or {}
        account = {"id": act, "name": info.get("name") or act, "currency": info.get("currency") or "USD", "platform": "meta"}
        members = [dict(account, tz=info.get("tz") or "")]
        pages = meta_pages_fn or (lambda path, params: paid_meta._pages(path, params, store))
        kw = {"pages": pages, "meta_account": members[0]}
    else:
        account = seg.resolve_target(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn)
        members = [dict(mem, currency=mem.get("currency") or account.get("currency") or "USD", platform="google") for mem in (account.get("members") or [account])]
        kw = {"search": search_fn}

    def one(mem):
        saved = st.account_of(settings or st.empty(), mem["id"])
        try:
            return cached_read(mem, force=force, store=store, clock=clock, now_utc=now_utc, saved=saved, sensitivity=sens, **kw)
        except gads.GadsError as e:
            if e.kind == "quota":
                return {"id": mem["id"], "name": mem["name"], "error": str(e), "quota": True}
            return {"id": mem["id"], "name": mem["name"], "error": str(e)}
        except Exception as e:                                                       # noqa: BLE001 (one Meta account failing never stops the rest)
            if len(members) == 1:
                raise
            return {"id": mem["id"], "name": mem["name"], "error": "%s: %s" % (type(e).__name__, e)}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(members)))) as pool:
        raws = list(pool.map(one, members))
    if all(r.get("error") for r in raws):
        first = raws[0]
        raise gads.GadsError("quota" if first.get("quota") else "api", first["error"])
    report = assemble(raws, account, settings=settings, now_utc=now_utc)
    report["warnings"] = warnings + report["warnings"]
    report["saved"] = {"teamSettings": mon is not None and not warnings}
    return report
