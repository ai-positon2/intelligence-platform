"""Paid Marketing Agents: KPI Monitoring, the reading and the report.

For a Google Ads account (or every account of an MCC) or a Meta ad account this reads each live campaign's last two weeks (impressions, clicks, cost, conversions and their value by day) and its
delivery status, and the rules (tracker/paid_kpi_math.py) hold the last 7 whole days against the campaign's target and against the 7 days before. Nothing is changed in an account.

Google: the enabled campaigns with their primary status and reasons, the bid strategy's system status and its own target; the days; and, softly, the share of impressions the budget lost over the
week. The campaigns and their days are the point (a failure there is raised); the budget read is soft (a refusal leaves that check out and says so). The account's own time zone (Google gives it
with the days) decides which day is "today", so a week is never a partial one. Meta: the campaigns' insights by day (the platform's reader), the active campaigns and, softly, their ad sets'
`learning_stage_info`, mapped onto Google's words (learning, limited by data) so one set of rules reads both; Meta has no budget-limited read. Meta days are the UTC date's.

The read is cached like the other agents (tracker/paid_geo.py _cached); what the team saved (targets, tolerance, a campaign's own targets, muted campaigns) is applied to the saved read each time,
so a change on the page shows at once without another read. The field names of the Google queries are the API's and the Meta ones are Meta's documented ones; none of the new ones has been run
against the live service from here.
"""
from __future__ import annotations

import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

from . import paid_gads_segments as gads
from . import paid_kpi_math as math
from . import paid_kpi_plan as plan
from . import paid_segments as seg

BadRequest = seg.BadRequest
READ_DAYS = 16                      # days read before today: the 14 the rules use and two spare for a time zone ahead of the server's
AGENT = "kpi"

GAQL_CAMPAIGNS = ("SELECT campaign.id, campaign.name, campaign.advertising_channel_type, campaign.primary_status, campaign.primary_status_reasons, campaign.bidding_strategy_system_status, "
                  "campaign.bidding_strategy_type, campaign.target_cpa.target_cpa_micros, campaign.maximize_conversions.target_cpa_micros, campaign.target_roas.target_roas, "
                  "campaign.maximize_conversion_value.target_roas, campaign.start_date_time FROM campaign WHERE campaign.status = ENABLED")
GAQL_DAYS = ("SELECT customer.time_zone, campaign.id, segments.date, metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.conversions_value FROM campaign "
             "WHERE campaign.status = ENABLED AND segments.date BETWEEN '%s' AND '%s'")
GAQL_LOST = ("SELECT campaign.id, metrics.search_budget_lost_impression_share FROM campaign WHERE campaign.status = ENABLED AND segments.date BETWEEN '%s' AND '%s'")

META_CAMPAIGNS_FIELDS = "id,name,effective_status,objective,start_time"
META_ADSETS_FIELDS = "id,campaign_id,effective_status,learning_stage_info"
META_LIVE = ["ACTIVE", "WITH_ISSUES"]


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

def read_campaigns(rows):
    """[{id, name, channel, primaryStatus, reasons, systemStatus, strategy, targetCpa, targetRoas, startDate}]: the enabled campaigns."""
    out = []
    for r in rows:
        c = r.get("campaign") or {}
        cid = str(c.get("id") or "")
        if not cid:
            continue
        micros = _num(_get(c, "targetCpa", "targetCpaMicros")) or _num(_get(c, "maximizeConversions", "targetCpaMicros"))
        roas = _num(_get(c, "targetRoas", "targetRoas")) or _num(_get(c, "maximizeConversionValue", "targetRoas"))
        out.append({"id": cid, "name": c.get("name") or cid, "channel": c.get("advertisingChannelType") or "", "primaryStatus": c.get("primaryStatus") or "",
                    "reasons": [str(x) for x in c.get("primaryStatusReasons") or []], "systemStatus": c.get("biddingStrategySystemStatus") or "", "strategy": c.get("biddingStrategyType") or "",
                    "targetCpa": round(micros / 1e6, 4) if micros else None, "targetRoas": round(roas, 4) if roas else None, "startDate": str(c.get("startDateTime") or "")[:10]})
    return out


def read_days(rows):
    """({campaign id: [{date, impr, clicks, cost, conv, value}]}, time zone name or "")."""
    out, tz = {}, ""
    for r in rows:
        cid = str(_get(r, "campaign", "id", default="") or "")
        day = str(_get(r, "segments", "date", default="") or "")
        tz = tz or str(_get(r, "customer", "timeZone", default="") or "")
        if not cid or not day:
            continue
        m = r.get("metrics") or {}
        out.setdefault(cid, []).append({"date": day, "impr": _num(m.get("impressions")), "clicks": _num(m.get("clicks")), "cost": round(_num(m.get("costMicros")) / 1e6, 4),
                                        "conv": _num(m.get("conversions")), "value": _num(m.get("conversionsValue"))})
    return out, tz


def read_lost(rows):
    """{campaign id: share of impressions lost to the budget (0 to 1)}."""
    out = {}
    for r in rows:
        cid = str(_get(r, "campaign", "id", default="") or "")
        v = _get(r, "metrics", "searchBudgetLostImpressionShare")
        if cid and v is not None:
            out[cid] = max(0.0, min(1.0, _num(v)))
    return out


def local_day(tz_name, now):
    """The account's date at `now` (a UTC time): a zone name Google gave decides whether the account is a day behind or ahead; an unknown or missing zone leaves the UTC date."""
    if tz_name:
        try:
            return now.astimezone(ZoneInfo(tz_name)).date()
        except Exception:                                                            # noqa: BLE001 (an unknown zone name must not stop the read)
            pass
    return now.date()


def _soft(warnings, what, fn, default):
    try:
        return fn()
    except gads.GadsError as e:
        warnings.append("%s could not be read (%s), so that check was skipped." % (what, e))
        return default


def read_account(account_id, search, now=None):
    """{campaigns: [...with days and lostBudget], today (ISO date of the account), warnings}: one Google Ads account. The campaigns and their days are raised on failure; the rest is soft."""
    now = now or dt.datetime.now(dt.timezone.utc)
    utc_today = now.date()
    campaigns = read_campaigns(search(account_id, GAQL_CAMPAIGNS))
    days, tz = read_days(search(account_id, GAQL_DAYS % ((utc_today - dt.timedelta(days=READ_DAYS)).isoformat(), utc_today.isoformat())))
    day = local_day(tz, now)
    warnings = []
    lost = _soft(warnings, "The share of impressions lost to the budget", lambda: read_lost(search(account_id, GAQL_LOST % ((day - dt.timedelta(days=math.WINDOW)).isoformat(), (day - dt.timedelta(days=1)).isoformat()))), {})
    for c in campaigns:
        c["days"] = days.get(c["id"], [])
        c["lostBudget"] = lost.get(c["id"])
    return {"campaigns": campaigns, "today": day.isoformat(), "warnings": warnings}


# ── Meta ────────────────────────────────────────────────────────────────────

def meta_campaigns_request(act):
    return "/%s/campaigns" % act, {"fields": META_CAMPAIGNS_FIELDS, "effective_status": json.dumps(META_LIVE), "limit": 200}


def meta_adsets_request(act):
    return "/%s/adsets" % act, {"fields": META_ADSETS_FIELDS, "effective_status": json.dumps(META_LIVE), "limit": 200}


def meta_learning(adsets):
    """{campaign id: "LIMITED_BY_DATA" | "LEARNING_NEW"}: Meta's learning_stage_info.status FAIL (learning limited) or LEARNING of an ad set, worst first, in Google's words."""
    out = {}
    for a in adsets:
        cid = str(a.get("campaign_id") or "")
        st = str((a.get("learning_stage_info") or {}).get("status") or "").upper()
        if not cid:
            continue
        if st == "FAIL":
            out[cid] = "LIMITED_BY_DATA"
        elif st == "LEARNING" and out.get(cid) != "LIMITED_BY_DATA":
            out[cid] = "LEARNING_NEW"
    return out


def read_meta_account(act, pages, now=None):
    """The same shape as read_account, for a Meta ad account: a Meta campaign is the unit; the days come from the platform's insights reader."""
    from . import paid_meta
    utc_today = (now or dt.datetime.now(dt.timezone.utc)).date()
    first, last = (utc_today - dt.timedelta(days=READ_DAYS)).isoformat(), (utc_today - dt.timedelta(days=1)).isoformat()
    live = {str(c.get("id")): c for c in pages(*meta_campaigns_request(act)) if c.get("id")}
    rows = pages("/%s/insights" % act, paid_meta.q_insights(first, last))
    days, kinds = {}, {}
    for r in rows:
        cid, d = str(r.get("campaign_id") or ""), r.get("date_start")
        if not cid or not d:
            continue
        n = paid_meta.numbers(r)
        days.setdefault(cid, []).append({"date": d, "impr": n[0], "clicks": n[1], "cost": n[2], "conv": n[3], "value": n[4]})
        kinds.setdefault(cid, paid_meta.campaign_type(r.get("objective")))
    warnings = []
    learning = {}
    try:
        learning = meta_learning(pages(*meta_adsets_request(act)))
    except Exception as e:                                                           # noqa: BLE001 (Meta has its own errors; the learning state is soft)
        warnings.append("The ad sets' learning state could not be read (%s), so that check was skipped." % str(e)[:160])
    campaigns = []
    for cid, c in live.items():
        issue = str(c.get("effective_status") or "") == "WITH_ISSUES"
        campaigns.append({"id": cid, "name": c.get("name") or cid, "channel": kinds.get(cid) or paid_meta.campaign_type(c.get("objective")), "primaryStatus": "LIMITED" if issue else "ELIGIBLE",
                          "reasons": ["META_WITH_ISSUES"] if issue else [], "systemStatus": learning.get(cid, ""), "strategy": "", "targetCpa": None, "targetRoas": None,
                          "startDate": str(c.get("start_time") or "")[:10], "lostBudget": None, "days": days.get(cid, [])})
    return {"campaigns": campaigns, "today": utc_today.isoformat(), "warnings": warnings}


def google_raw(account, *, search_fn=None, workers=6, now=None):
    """{accounts: [{id, name, currency, platform, ...the read}], warnings, members}: one account or an MCC's accounts. An account whose campaigns cannot be read is left out and said so; every one
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
            "members": [mem["name"] for mem in members] if many else []}                  # every warning is up here, so a read that lost a part is not kept for hours (tracker/paid_geo.py _cached)


def meta_raw(act, *, pages_fn, account_fn, now=None):
    acct = account_fn(act) or {}
    got = read_meta_account(act, pages_fn, now)
    return {"accounts": [dict(got, id=act, name=acct.get("name") or act, currency=acct.get("currency") or "USD", platform="meta")], "warnings": list(got["warnings"]), "members": []}


# ── The report ──────────────────────────────────────────────────────────────

_RANK = {"fix": 0, "check": 1, "clean": 2}


def learning_days(issues, account_id, now):
    """{campaign id: days the monitor has had that campaign's learning issue open}, from the monitor's saved issues."""
    from . import paid_monitor as mon
    out = {}
    for i in issues or []:
        if i.get("kind") != "learning" or i.get("status") != "open" or str(i.get("accountId")) != str(account_id):
            continue
        cid = str((i.get("data") or {}).get("campaignId") or "")
        age = mon.age_days(i.get("firstSeen"), now)
        if cid and age is not None:
            out[cid] = age
    return out


def assemble(raw, account, *, settings=None, issues=None, now=None):
    """The report from the raw read. `settings` is what the team saved (tracker/paid_kpi_settings.py load), `issues` the monitor's saved issues (for how long a campaign has been learning)."""
    from . import paid_kpi_settings as st
    settings = settings or st.empty()
    now = now or dt.datetime.now(dt.timezone.utc)
    accounts, warnings = [], list(raw.get("warnings") or [])
    for a in raw["accounts"]:
        cfg = st.account_of(settings, a["id"])
        res = math.analyse(a, config=cfg, today=dt.date.fromisoformat(a.get("today") or now.date().isoformat()), learning_days=learning_days(issues, a["id"], now), platform=a.get("platform") or "google")
        accounts.append(dict(res, id=a["id"], name=a["name"], currency=a.get("currency") or "USD", platform=a.get("platform") or "google", today=a.get("today") or "",
                             saved={"targets": sum(1 for v in cfg["targets"].values() if v), "campaigns": len(cfg["campaigns"]), "muted": len(cfg["muted"]), "tolerance": cfg["tolerance"]},
                             plan=plan.build(res)))
    accounts.sort(key=lambda a: (_RANK[a["status"]], -a["counts"]["high"], -a["counts"]["medium"], -a["counts"]["low"], a["name"].lower()))
    counts = {k: 0 for k in _RANK}
    for a in accounts:
        counts[a["status"]] += 1
    return {
        "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or "USD", "platform": account.get("platform") or "google",
                    "isGroup": bool(account.get("members")), "accountCount": len(account.get("members") or [])},
        "generatedAt": now.isoformat(timespec="seconds"), "warnings": warnings, "accounts": accounts,
        "rules": [{"rule": k, "label": math.RULES[k]["label"], "level": math.RULES[k]["level"], "fix": math.RULES[k]["fix"]} for k in math.ORDER],
        "minimums": {"conversions": math.MIN_CONV, "clicks": math.MIN_CLICKS, "impressions": math.MIN_IMPR, "tolerance": int(math.DEFAULT_TOLERANCE * 100), "stuckDays": math.STUCK_DAYS},
        "summary": {"counts": counts, "accounts": len(accounts), "campaigns": sum(a["campaigns"] for a in accounts), "offTarget": sum(a["offTarget"] for a in accounts),
                    "onTarget": sum(a["onTarget"] for a in accounts), "notJudged": sum(a["notJudged"] for a in accounts), "withoutTarget": sum(a["withoutTarget"] for a in accounts),
                    "high": sum(a["counts"]["high"] for a in accounts), "medium": sum(a["counts"]["medium"] for a in accounts), "low": sum(a["counts"]["low"] for a in accounts),
                    "findings": sum(a["findingCount"] for a in accounts)},
    }


def load_raw(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, at=None, force=False, store=None, today=None, meta_pages_fn=None, meta_account_fn=None):
    """(raw, stale, account): the saved read of an account's campaigns, read from the platform when there is none (or when `force`). `now` is the cache's clock (seconds), `at` the time the read counts days from."""
    from . import paid_geo, paid_meta, paid_store
    store = store or paid_store
    day = today or dt.date.today()
    if paid_meta.is_meta(str(account_id or "")):
        act = paid_meta.norm_id(account_id)
        pages = meta_pages_fn or (lambda path, params: paid_meta._pages(path, params, store))
        acct_fn = meta_account_fn or (lambda a: paid_meta.account_by_id(store, a) or {})
        account = {"id": act, "name": (acct_fn(act) or {}).get("name") or act, "currency": (acct_fn(act) or {}).get("currency") or "USD", "platform": "meta"}
        raw, stale = paid_geo._cached("kpi", act, "days", day, day, lambda: meta_raw(act, pages_fn=pages, account_fn=acct_fn, now=at), force=force, store=store, now=now)
    else:
        account = seg.resolve_target(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn)
        raw, stale = paid_geo._cached("kpi", account["id"], "days", day, day, lambda: google_raw(account, search_fn=search_fn, now=at), force=force, store=store, now=now)
    return raw, stale, account


def report_for(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, at=None, force=False, store=None, today=None, mon=None, meta_pages_fn=None, meta_account_fn=None):
    """The KPI Monitoring report for a Google Ads account or an MCC the root MCC holds, or for a Meta ad account (act_...). `force` (Refresh) skips the saved read, at most once per
    paid_gads.REFRESH_COOLDOWN_S for the account. `mon` is the monitors' store: with it what the team saved here and how long campaigns have been learning are applied. `now` is the cache's
    clock (seconds, for tests), `at` the time (UTC) the days are counted from."""
    from . import paid_kpi_settings as st
    warnings, settings, issues = [], None, None
    if mon is not None:
        try:
            settings = st.load(mon)
        except Exception as e:                                                       # noqa: BLE001
            warnings.append("What the team saved (targets, muted campaigns) could not be read (%s), so it was not applied." % e)
        try:
            issues = mon.issues_all(AGENT)
        except Exception:                                                            # noqa: BLE001 (without the history a learning campaign is only "still learning")
            issues = None
    raw, stale, account = load_raw(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn, search_fn=search_fn, now=now, at=at, force=force, store=store, today=today,
                                   meta_pages_fn=meta_pages_fn, meta_account_fn=meta_account_fn)
    report = assemble(raw, account, settings=settings, issues=issues, now=at)
    report["warnings"] = warnings + report["warnings"]
    if raw.get("readAt"):
        report["readAt"] = raw["readAt"]
    return dict(report, stale=stale) if stale else report
