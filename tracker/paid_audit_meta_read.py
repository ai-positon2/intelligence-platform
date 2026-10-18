"""Paid Marketing Agents: Account Audit, reading one Meta ad account.

The same shape as the Google read (tracker/paid_audit_google_read.py): one request per part of the account, each compacted to what the
checks need, each read on its own so a part Meta will not answer becomes a warning and an `ok` flag of False instead of a guess. The
requests are Graph API GETs through tracker/paid_meta.py (the platform's own rate limit handling, token and version); nothing here
changes the ad account.

Fields that may not exist for every account or token (advanced matching, first-party cookie status, the business, the spend terms) are
asked in a second request after the core fields, so a refusal of an optional field never costs the core read.
"""
from __future__ import annotations

import datetime as dt
import json

from . import paid_meta as meta

EXAMPLES = 8
LEARNING_LIMITED = "FAIL"                      # Meta's learning_stage_info.status for "Learning limited"
LIVE = ["ACTIVE", "WITH_ISSUES"]                # campaigns and ad sets that are on; Meta flags some of them WITH_ISSUES and they still deliver
AD_STATES = ["ACTIVE", "DISAPPROVED", "WITH_ISSUES", "PENDING_REVIEW"]
CAN_RUN = ("ACTIVE", "WITH_ISSUES", "PENDING_REVIEW")      # an ad in one of these states is delivering or about to; a disapproved one is not
WINDOW = "last_30d"
STATS_DAYS = 7
MAX_PIXELS_READ = 3

ACCOUNT_CORE = "name,account_status,disable_reason,spend_cap,amount_spent,currency,timezone_name"
ACCOUNT_EXTRA = "is_prepay_account,offsite_pixels_tos_accepted"
ACCOUNT_BUSINESS = "business{id,name}"          # asked alone: Meta refuses this field to a token without business_management, and it must not take the terms above down with it
PIXEL_CORE = "id,name,last_fired_time,is_unavailable,creation_time"
PIXEL_EXTRA = "id,enable_automatic_matching,first_party_cookie_status"
CAMPAIGN_FIELDS = "id,name,objective,effective_status,buying_type,special_ad_categories,bid_strategy,issues_info"
ADSET_FIELDS = ("id,name,campaign_id,effective_status,optimization_goal,billing_event,learning_stage_info,issues_info,destination_type,"
                "daily_budget,lifetime_budget")
AD_FIELDS = "id,name,adset_id,campaign_id,effective_status,issues_info,ad_review_feedback"
INSIGHT_FIELDS = "campaign_id,campaign_name,objective,spend,impressions,inline_link_clicks,reach,frequency,actions,action_values"


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def parse_time(text):
    """Meta's "2026-10-16T12:30:00+0000" (or with a colon in the offset) as an aware UTC datetime, or None."""
    t = str(text or "").strip()
    if not t:
        return None
    try:
        if len(t) > 5 and t[-5] in "+-" and t[-3] != ":":
            t = t[:-2] + ":" + t[-2:]
        d = dt.datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def _major(value, currency):
    """Meta gives money in the currency's smallest unit as a string."""
    v = _f(value)
    return round(v if currency in meta.ZERO_DECIMAL else v / 100.0, 2)


def _issues(items):
    return [str(i.get("error_summary") or i.get("error_message") or "Meta reports an issue") for i in (items or []) if isinstance(i, dict)][:3]


# ── The account ─────────────────────────────────────────────────────────────

def parse_account(core, extra=None):
    """{name, status, statusCode, disableReason, currency, timeZone, spendCap, amountSpent, prepaid, pixelTerms, business}. The extra
    fields are None when they could not be read."""
    core, extra = core or {}, extra or {}
    code = core.get("account_status")
    cur = core.get("currency") or ""
    cap = _major(core.get("spend_cap"), cur)                       # "0" and a missing cap both mean no cap
    return {"name": core.get("name") or "", "statusCode": code, "status": meta.ACCOUNT_STATUS.get(code, "UNKNOWN"),
            "disableReason": core.get("disable_reason"), "currency": cur, "timeZone": core.get("timezone_name") or "",
            "spendCap": cap, "amountSpent": _major(core.get("amount_spent"), cur),
            "prepaid": None if "is_prepay_account" not in extra else bool(extra.get("is_prepay_account")),
            "pixelTerms": None if "offsite_pixels_tos_accepted" not in extra else bool(extra.get("offsite_pixels_tos_accepted")),
            "business": (extra.get("business") or {}).get("name") or ""}


# ── Pixels and what they received ───────────────────────────────────────────

def parse_pixels(core_rows, extra_rows=None):
    """[{id, name, lastFired, unavailable, autoMatching, firstPartyCookie}]. `autoMatching` is None when the extra read failed."""
    extra = {str(r.get("id")): r for r in (extra_rows or [])}
    out = []
    for r in core_rows:
        pid = str(r.get("id") or "")
        e = extra.get(pid)
        out.append({"id": pid, "name": r.get("name") or pid, "lastFired": r.get("last_fired_time") or "", "unavailable": bool(r.get("is_unavailable")),
                    "autoMatching": None if extra_rows is None or e is None else bool(e.get("enable_automatic_matching")),
                    "firstPartyCookie": None if extra_rows is None or e is None else (e.get("first_party_cookie_status") or "")})
    out.sort(key=lambda p: (parse_time(p["lastFired"]) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)), reverse=True)
    return out


def parse_stats(rows):
    """Pixel stats (one row per hour, each with data: [{value, count}]) added up: {value: count}."""
    out = {}
    for r in rows:
        for d in r.get("data") or []:
            v = str(d.get("value") or "")
            if v:
                out[v] = out.get(v, 0) + _i(d.get("count"))
    return out


# ── Campaigns, ad sets, ads ─────────────────────────────────────────────────

def parse_campaigns(rows, insight_rows=None):
    """Every live campaign, with its last 30 days when the insights were read: [{id, name, objective, kind, issues, special, bidStrategy,
    spend, results, impressions, clicks, frequency, reach}]; spend and the others are None when the insights were not read."""
    stats = None
    if insight_rows is not None:
        stats = {}
        for r in insight_rows:
            n = meta.numbers(r)                        # impressions, link clicks, spend, results ...
            stats[str(r.get("campaign_id") or "")] = {"impressions": n[0], "clicks": n[1], "spend": n[2], "results": n[3], "anyResults": n[5],
                                                       "frequency": round(_f(r.get("frequency")), 2), "reach": _i(r.get("reach"))}
    out = []
    for c in rows:
        cid = str(c.get("id") or "")
        s = (stats or {}).get(cid, {"impressions": 0, "clicks": 0, "spend": 0.0, "results": 0.0, "anyResults": 0.0, "frequency": 0.0, "reach": 0}) if stats is not None else None
        out.append({"id": cid, "name": c.get("name") or cid, "objective": c.get("objective") or "", "kind": meta.objective_kind(c.get("objective")),
                    "issues": _issues(c.get("issues_info")), "special": list(c.get("special_ad_categories") or []), "bidStrategy": c.get("bid_strategy") or "",
                    "spend": None if s is None else s["spend"], "results": None if s is None else s["results"], "anyResults": None if s is None else s["anyResults"],
                    "impressions": None if s is None else s["impressions"], "clicks": None if s is None else s["clicks"],
                    "frequency": None if s is None else s["frequency"], "reach": None if s is None else s["reach"]})
    out.sort(key=lambda c: (-(c["spend"] or 0), c["name"].lower()))
    return out


def parse_adsets(rows):
    """Every live ad set: [{id, name, campaignId, optimization, billing, learning, learningConversions, issues, destination}]."""
    out = []
    for a in rows:
        learn = a.get("learning_stage_info") or {}
        out.append({"id": str(a.get("id") or ""), "name": a.get("name") or "", "campaignId": str(a.get("campaign_id") or ""),
                    "optimization": a.get("optimization_goal") or "", "billing": a.get("billing_event") or "",
                    "learning": learn.get("status") or "", "learningConversions": _i(learn.get("conversions")),
                    "issues": _issues(a.get("issues_info")), "destination": a.get("destination_type") or ""})
    return out


def parse_ads(rows, adsets=None, campaigns=None):
    """Counts of ads by delivery state, the disapproved and flagged ones with Meta's reason, and how many ads each ad set holds that are
    delivering or about to (active, flagged with issues, in review; not disapproved)."""
    set_names = {s["id"]: s["name"] for s in adsets or []}
    camp_names = {c["id"]: c["name"] for c in campaigns or []}
    by_status, per_set, bad, flagged = {}, {}, [], []
    for a in rows:
        st = a.get("effective_status") or "UNKNOWN"
        by_status[st] = by_status.get(st, 0) + 1
        sid = str(a.get("adset_id") or "")
        if st in CAN_RUN:
            per_set[sid] = per_set.get(sid, 0) + 1
        where = {"ad": a.get("name") or str(a.get("id") or ""), "adSet": set_names.get(sid, ""), "campaign": camp_names.get(str(a.get("campaign_id") or ""), "")}
        if st == "DISAPPROVED":
            why = sorted((((a.get("ad_review_feedback") or {}).get("global")) or {}).keys())[:3] or _issues(a.get("issues_info"))
            bad.append(dict(where, why=why))
        elif st == "WITH_ISSUES":
            flagged.append(dict(where, why=_issues(a.get("issues_info"))))
    return {"total": len(rows), "byStatus": by_status, "disapproved": {"count": len(bad), "examples": bad[:EXAMPLES]},
            "withIssues": {"count": len(flagged), "examples": flagged[:EXAMPLES]}, "_perAdSet": per_set}


def compact(ads):
    return {k: v for k, v in (ads or {}).items() if not k.startswith("_")} if ads is not None else None


# ── Reading the whole account ───────────────────────────────────────────────

def _q(**kw):
    return kw


def read_account(act, *, get, pages, now=None):
    """The compact read of one ad account: {account, pixels, stats, campaigns, adSets, ads, ok, warnings, readAt}. `get(path, params)` and
    `pages(path, params)` are the Graph API calls (paid_meta._get and _pages bound to a store). The account record is the point, so a
    failure there is raised; every other part is a warning with its ok flag False and what depends on it None. Meta asking to wait
    (paid_meta.OverQuota) stops the read and is raised: the rest would only fail the same way. An odd answer that cannot be parsed is
    a warning too."""
    now = now or dt.datetime.now(dt.timezone.utc)
    ok, warnings = {}, []

    def part(key, what, fn, required=False):
        try:
            out = fn()
            ok[key] = True
            return out
        except meta.OverQuota:
            raise
        except Exception as e:                  # Meta refusing a field is an AdsError; anything odd in its answer is a warning too
            if required:
                raise
            ok[key] = False
            warnings.append("%s could not be read (%s)." % (what[0].upper() + what[1:], e))
            return None
    core = part("account", "the account", lambda: get(act, _q(fields=ACCOUNT_CORE)), required=True)
    extra = part("accountExtra", "the account's payment and pixel terms", lambda: get(act, _q(fields=ACCOUNT_EXTRA)))
    try:
        biz = get(act, _q(fields=ACCOUNT_BUSINESS))
    except meta.OverQuota:
        raise
    except Exception:                           # only the business's name hangs on it, so a refusal is not worth a warning on every audit
        biz = None
    if extra is not None and isinstance(biz, dict) and biz.get("business"):
        extra = dict(extra, business=biz["business"])
    account = parse_account(core, extra)
    pixel_rows = part("pixels", "the pixels", lambda: pages(act + "/adspixels", _q(fields=PIXEL_CORE, limit=100)))
    pixel_extra = part("pixelExtra", "the pixels' matching settings", lambda: pages(act + "/adspixels", _q(fields=PIXEL_EXTRA, limit=100))) if pixel_rows else None
    pixels = None if pixel_rows is None else part("pixelList", "the pixels", lambda: parse_pixels(pixel_rows, pixel_extra))
    stats = None
    if pixels:
        since = int((now - dt.timedelta(days=STATS_DAYS)).timestamp())
        events = methods = None
        for px in pixels[:MAX_PIXELS_READ]:
            got = part("stats:" + px["id"], "the events received by pixel %s" % px["name"],
                       lambda px=px: parse_stats(pages(px["id"] + "/stats", _q(aggregation="event", start_time=since, limit=500))))
            if got is not None:
                events = dict(events or {})
                for k, v in got.items():
                    events[k] = events.get(k, 0) + v
            via = part("sources:" + px["id"], "how pixel %s received its events" % px["name"],
                       lambda px=px: parse_stats(pages(px["id"] + "/stats", _q(aggregation="event_detection_method", start_time=since, limit=500))))
            if via is not None:
                methods = dict(methods or {})
                for k, v in via.items():
                    methods[k] = methods.get(k, 0) + v
        if events is not None or methods is not None:
            stats = {"days": STATS_DAYS, "events": events, "methods": methods}
    live = json.dumps(LIVE)
    insights = part("insights", "the campaigns' numbers for the last 30 days", lambda: pages(act + "/insights", _q(level="campaign", date_preset=WINDOW, fields=INSIGHT_FIELDS, limit=500)))
    campaigns = part("campaigns", "the campaigns", lambda: parse_campaigns(pages(act + "/campaigns", _q(fields=CAMPAIGN_FIELDS, effective_status=live, limit=500)), insights))
    adsets = part("adSets", "the ad sets", lambda: parse_adsets(pages(act + "/adsets", _q(fields=ADSET_FIELDS, effective_status=live, limit=500))))
    ads = part("ads", "the ads", lambda: parse_ads(pages(act + "/ads", _q(fields=AD_FIELDS, effective_status=json.dumps(AD_STATES), limit=500)), adsets, campaigns))
    return {"account": account, "pixels": pixels, "stats": stats, "campaigns": campaigns, "adSets": adsets, "ads": compact(ads),
            "adsPerSet": None if ads is None else ads["_perAdSet"], "ok": ok, "warnings": warnings, "readAt": now.isoformat()}
