"""Paid Marketing Agents: Test & Experiment Tracker, what Google Ads says about the tests in an account.

Read only, in two parts that are read on their own (a part that could not be read is a warning and what depends on it is empty):

  experiments   every Google Ads experiment that is not removed (experiment), its arms (experiment_arm: the control campaign and the trial campaign and the
                traffic split), the campaigns those arms run (campaign), and what each arm's campaigns did on every day from the experiment's start to yesterday
                (campaign by day, summed per arm)
  ads           every live ad of a live ad group over the last 30 days, with its text and the numbers it earned (ad_group_ad), grouped by ad group

Ads that live in an experiment's trial campaign are not ad copy tests of their own: they are part of the experiment, so they are left out of the ads part.
An experiment whose arms Google does not report as campaigns (an ad variations experiment) is listed with its dates and the report says its results are in the
Google Ads screen only.
"""
from __future__ import annotations

import datetime as dt

from . import paid_gads as base
from . import paid_gads_segments as gads

ADS_WINDOW = "LAST_30_DAYS"
ADS_DAYS = 30
MAX_LOOKBACK_DAYS = 365                          # the longest stretch of an experiment's days that is read
MAX_ADS_PER_GROUP = 8
MAX_GROUPS = 150                                 # ad groups kept, the ones that spent most
HEADLINES = 3
CAMPAIGN_IDS_PER_QUERY = 200

EXPERIMENTS_GAQL = (
    "SELECT experiment.resource_name, experiment.experiment_id, experiment.name, experiment.description, experiment.status, experiment.type, "
    "experiment.start_date, experiment.end_date FROM experiment WHERE experiment.status != REMOVED")
ARMS_GAQL = (
    "SELECT experiment_arm.resource_name, experiment_arm.experiment, experiment_arm.name, experiment_arm.control, experiment_arm.traffic_split, "
    "experiment_arm.campaigns FROM experiment_arm")
CAMPAIGNS_GAQL = (
    "SELECT campaign.resource_name, campaign.id, campaign.name, campaign.status, campaign.advertising_channel_type, campaign.experiment_type, campaign.base_campaign "
    "FROM campaign WHERE campaign.status != REMOVED")
ADS_GAQL = (
    "SELECT campaign.id, campaign.name, campaign.advertising_channel_type, campaign.experiment_type, ad_group.id, ad_group.name, ad_group_ad.ad.id, "
    "ad_group_ad.ad.type, ad_group_ad.ad.final_urls, ad_group_ad.ad.responsive_search_ad.headlines, ad_group_ad.ad.responsive_search_ad.descriptions, "
    "ad_group_ad.ad_strength, metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.conversions_value "
    "FROM ad_group_ad WHERE ad_group_ad.status = ENABLED AND campaign.status = ENABLED AND ad_group.status = ENABLED AND segments.date DURING " + ADS_WINDOW +
    " ORDER BY metrics.cost_micros DESC LIMIT 5000")

STATUSES = {"ENABLED": "running", "INITIATED": "running", "SETUP": "setting up", "HALTED": "ended", "GRADUATED": "applied", "PROMOTED": "applied", "REMOVED": "removed"}
TYPES = {"SEARCH_CUSTOM": "Search custom", "DISPLAY_CUSTOM": "Display custom", "YOUTUBE_CUSTOM": "YouTube custom", "HOTEL_CUSTOM": "Hotel custom",
         "SEARCH_AUTOMATED_BIDDING_STRATEGY": "Search bidding strategy", "DISPLAY_AUTOMATED_BIDDING_STRATEGY": "Display bidding strategy",
         "SHOPPING_AUTOMATED_BIDDING_STRATEGY": "Shopping bidding strategy", "DISPLAY_AND_VIDEO_360": "Display and Video 360", "AD_VARIATION": "Ad variations",
         "SMART_MATCHING": "Smart matching", "OPTIMIZE_ASSETS": "Optimize assets", "ADOPT_AI_MAX": "Adopt AI Max", "ADOPT_BROAD_MATCH_KEYWORDS": "Adopt broad match keywords",
         "PMAX_REPLACEMENT_SHOPPING": "Performance Max replacing Shopping"}


def _get(row, *path, default=None):
    for p in path:
        if not isinstance(row, dict):
            return default
        row = row.get(p)
    return default if row is None else row


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


def _date(v):
    """A date from Google's "YYYY-MM-DD", or None (Google writes 2037-12-30 for an experiment with no end)."""
    try:
        d = dt.date.fromisoformat(str(v or "")[:10])
    except ValueError:
        return None
    return None if d.year >= 2037 else d


def _day(v):
    return v.isoformat() if v else None


def _norm(text):
    return " ".join(str(text or "").split())


# ── Experiments ─────────────────────────────────────────────────────────────

def parse_experiments(rows):
    """[{resource, id, name, description, status, state, type, typeLabel, start, end}], newest start first. `state` is running, setting up, ended, applied or the raw status."""
    out = []
    for r in rows:
        e = r.get("experiment") or {}
        res = e.get("resourceName")
        if not res:
            continue
        status = str(e.get("status") or "")
        typ = str(e.get("type") or "")
        out.append({"resource": res, "id": str(e.get("experimentId") or res.rsplit("/", 1)[-1]), "name": _norm(e.get("name")) or "Experiment", "description": _norm(e.get("description")),
                    "status": status, "state": STATUSES.get(status, status.lower() or "unknown"), "type": typ, "typeLabel": TYPES.get(typ, typ.replace("_", " ").capitalize() if typ else ""),
                    "start": _day(_date(e.get("startDate"))), "end": _day(_date(e.get("endDate")))})
    out.sort(key=lambda x: (x["start"] or "", x["name"]), reverse=True)
    return out


def parse_arms(rows):
    """{experiment resource: [{name, control, split, campaigns: [resource names]}]}, the control arm first."""
    out = {}
    for r in rows:
        a = r.get("experimentArm") or {}
        exp = a.get("experiment")
        if not exp:
            continue
        out.setdefault(exp, []).append({"name": _norm(a.get("name")) or "Arm", "control": bool(a.get("control")), "split": _i(a.get("trafficSplit"), None),
                                        "campaigns": [c for c in (a.get("campaigns") or []) if c]})
    for arms in out.values():
        arms.sort(key=lambda x: (not x["control"], x["name"]))
    return out


def parse_campaigns(rows):
    """{resource name: {id, name, type, experimentType, base}}."""
    out = {}
    for r in rows:
        c = r.get("campaign") or {}
        res = c.get("resourceName")
        if res:
            out[res] = {"id": str(c.get("id") or res.rsplit("/", 1)[-1]), "name": _norm(c.get("name")), "type": str(c.get("advertisingChannelType") or ""),
                        "experimentType": str(c.get("experimentType") or ""), "base": c.get("baseCampaign") or ""}
    return out


def parse_days(rows):
    """{campaign id: {date: [impressions, clicks, cost, conversions, value]}}."""
    out = {}
    for r in rows:
        cid = str(_get(r, "campaign", "id", default=""))
        day = str(_get(r, "segments", "date", default=""))[:10]
        if not cid or not day:
            continue
        m = r.get("metrics") or {}
        out.setdefault(cid, {})[day] = [_i(m.get("impressions")), _i(m.get("clicks")), round(_f(m.get("costMicros")) / 1e6, 2), round(_f(m.get("conversions")), 4),
                                        round(_f(m.get("conversionsValue")), 2)]
    return out


def _window(exp, today):
    """(first day, last day) of an experiment's results: its start to the earlier of its end and yesterday, never more than MAX_LOOKBACK_DAYS back. None when it has not started."""
    start, end = _date(exp["start"]), _date(exp["end"])
    yesterday = today - dt.timedelta(days=1)
    if start is None:
        return None
    last = min(end, yesterday) if end else yesterday
    first = max(start, yesterday - dt.timedelta(days=MAX_LOOKBACK_DAYS - 1))
    return (first, last) if first <= last else None


def sum_arm(days_by_campaign, campaign_ids, first, last):
    """{days: {date: [..]}, total: {impressions, clicks, cost, conversions, value}} for the campaigns of one arm between two dates."""
    series, tot = {}, [0, 0, 0.0, 0.0, 0.0]
    for cid in campaign_ids:
        for day, v in (days_by_campaign.get(cid) or {}).items():
            d = _date(day)
            if d is None or d < first or d > last:
                continue
            acc = series.setdefault(day, [0, 0, 0.0, 0.0, 0.0])
            for i in range(5):
                acc[i] += v[i]
                tot[i] += v[i]
    series = {k: [v[0], v[1], round(v[2], 2), round(v[3], 4), round(v[4], 2)] for k, v in sorted(series.items())}
    return {"days": series, "total": {"impressions": tot[0], "clicks": tot[1], "cost": round(tot[2], 2), "conversions": round(tot[3], 4), "value": round(tot[4], 2)}}


def build_experiments(experiments, arms, campaigns, days, today):
    """Each experiment with its arms: {..., window: [first, last] or None, clipped, arms: [{name, control, split, campaigns: [{id, name}], results: {days, total} or None}], measured}.
    `measured` is True only for an experiment that has started and whose control arm and trial arm both run a campaign (an ad variations experiment has none)."""
    out = []
    for e in experiments:
        win = _window(e, today)
        rows = []
        for a in arms.get(e["resource"], []):
            camps = [dict(campaigns[c], resource=c) for c in a["campaigns"] if c in campaigns]
            ids = [c["id"] for c in camps]
            rows.append({"name": a["name"], "control": a["control"], "split": a["split"], "campaigns": [{"id": c["id"], "name": c["name"], "type": c["type"]} for c in camps],
                         "results": sum_arm(days, ids, win[0], win[1]) if win and ids else None})
        start = _date(e["start"])
        clipped = bool(win and start and win[0] > start)
        has = lambda control: any(a["control"] is control and a["results"] is not None for a in rows)   # noqa: E731
        out.append(dict(e, window=[_day(win[0]), _day(win[1])] if win else None, clipped=clipped, arms=rows, measured=bool(win) and has(True) and has(False)))
    return out


def experiment_campaign_ids(experiments, arms, campaigns, today):
    """The campaign ids whose days must be read, and the date range to read them for: ({ids}, first, last) or None when there is nothing to read."""
    ids, first, last = set(), None, None
    for e in experiments:
        win = _window(e, today)
        if win is None:
            continue
        got = {campaigns[c]["id"] for a in arms.get(e["resource"], []) for c in a["campaigns"] if c in campaigns}
        if not got:
            continue
        ids |= got
        first = win[0] if first is None else min(first, win[0])
        last = win[1] if last is None else max(last, win[1])
    return (ids, first, last) if ids else None


def days_gaql(ids, first, last):
    return ("SELECT campaign.id, segments.date, metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.conversions_value FROM campaign "
            "WHERE campaign.id IN (%s) AND segments.date BETWEEN '%s' AND '%s'" % (", ".join(sorted(ids)), first.isoformat(), last.isoformat()))


# ── Ads ─────────────────────────────────────────────────────────────────────

def _texts(assets, limit):
    out = []
    for a in assets or []:
        t = _norm((a or {}).get("text"))
        if t and t not in out:
            out.append(t)
    return out[:limit]


def parse_ads(rows, trial_campaigns=frozenset()):
    """{ad group id: {id, name, campaign, campaignId, campaignType, ads: [{id, type, headlines, descriptions, url, strength, impressions, clicks, cost, conversions, value}]}}.
    A campaign that is an experiment's trial (by its experiment type or by `trial_campaigns`, a set of campaign ids) is left out."""
    groups = {}
    for r in rows:
        gid = str(_get(r, "adGroup", "id", default=""))
        aid = str(_get(r, "adGroupAd", "ad", "id", default=""))
        cid = str(_get(r, "campaign", "id", default=""))
        if not gid or not aid:
            continue
        if str(_get(r, "campaign", "experimentType", default="")) == "EXPERIMENT" or cid in trial_campaigns:
            continue
        ad = _get(r, "adGroupAd", "ad", default={}) or {}
        rsa = ad.get("responsiveSearchAd") or {}
        m = r.get("metrics") or {}
        g = groups.setdefault(gid, {"id": gid, "name": _norm(_get(r, "adGroup", "name", default="")), "campaign": _norm(_get(r, "campaign", "name", default="")), "campaignId": cid,
                                    "campaignType": str(_get(r, "campaign", "advertisingChannelType", default="")), "ads": []})
        urls = [u for u in (ad.get("finalUrls") or []) if u]
        g["ads"].append({"id": aid, "type": str(ad.get("type") or ""), "headlines": _texts(rsa.get("headlines"), 15), "descriptions": _texts(rsa.get("descriptions"), 4),
                         "url": urls[0] if urls else "", "strength": str(_get(r, "adGroupAd", "adStrength", default="")),
                         "impressions": _i(m.get("impressions")), "clicks": _i(m.get("clicks")), "cost": round(_f(m.get("costMicros")) / 1e6, 2),
                         "conversions": round(_f(m.get("conversions")), 4), "value": round(_f(m.get("conversionsValue")), 2)})
    return groups


def compact_groups(groups, keep=MAX_GROUPS, per_group=MAX_ADS_PER_GROUP):
    """The ad groups that have two or more live ads (a test), the `keep` that spent most, each with its `per_group` ads that spent most. Returns (groups, how many tests there were)."""
    tests = [g for g in groups.values() if len(g["ads"]) >= 2]
    for g in tests:
        g["adCount"] = len(g["ads"])
        g["cost"] = round(sum(a["cost"] for a in g["ads"]), 2)
        g["ads"] = sorted(g["ads"], key=lambda a: (-a["cost"], -a["clicks"], a["id"]))[:per_group]
    tests.sort(key=lambda g: (-g["cost"], g["id"]))
    return tests[:keep], len(tests)


# ── The whole read ──────────────────────────────────────────────────────────

def _today(today):
    if today is not None:
        return today
    return dt.date.fromisoformat(base._pacific_day())


def read_experiments(account_id, search, today):
    """{experiments, trialCampaigns}. Raises GadsError when the experiments themselves cannot be read; a failure reading their days is the caller's to report (see read_account)."""
    exps = parse_experiments(search(account_id, EXPERIMENTS_GAQL))
    if not exps:
        return {"experiments": [], "trialCampaigns": [], "daysOk": True}
    arms = parse_arms(search(account_id, ARMS_GAQL))
    campaigns = parse_campaigns(search(account_id, CAMPAIGNS_GAQL))
    trial = sorted({campaigns[c]["id"] for a in arms.values() for x in a if not x["control"] for c in x["campaigns"] if c in campaigns})
    need = experiment_campaign_ids(exps, arms, campaigns, today)
    days, days_ok = {}, True
    if need:
        ids, first, last = need
        ordered = sorted(ids)
        try:
            for i in range(0, len(ordered), CAMPAIGN_IDS_PER_QUERY):
                got = parse_days(search(account_id, days_gaql(ordered[i:i + CAMPAIGN_IDS_PER_QUERY], first, last)))
                days.update(got)
        except gads.GadsError:
            days, days_ok = {}, False
    return {"experiments": build_experiments(exps, arms, campaigns, days, today), "trialCampaigns": trial, "daysOk": days_ok}


def read_account(account_id, *, search_fn=None, today=None):
    """{experiments, adGroups, adTests, ok, warnings}. The two parts are read on their own: one that fails is a warning, and both failing is raised. Read only."""
    search = search_fn or gads.search
    today = _today(today)
    ok, warnings, errors = {"experiments": True, "ads": True}, [], {}
    exp = {"experiments": [], "trialCampaigns": [], "daysOk": True}
    try:
        exp = read_experiments(account_id, search, today)
    except gads.GadsError as e:
        errors["experiments"] = e
        ok["experiments"] = False
        warnings.append("The experiments could not be read (%s)." % e)
    if not exp["daysOk"]:
        ok["experiments"] = False
        warnings.append("The experiments were read but not what their arms did, so none of them has a result here.")
    groups, tests = [], 0
    try:
        found = parse_ads(search(account_id, ADS_GAQL), set(exp["trialCampaigns"]))
        groups, tests = compact_groups(found)
    except gads.GadsError as e:
        errors["ads"] = e
        ok["ads"] = False
        warnings.append("The ads could not be read (%s)." % e)
    if len(errors) == 2:
        raise errors["experiments"]
    return {"experiments": exp["experiments"], "adGroups": groups, "adTests": tests, "ok": ok, "warnings": warnings, "today": today.isoformat()}
