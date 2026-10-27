"""Paid Marketing Agents: the Meta side of Ad Strength Analysis and Asset Coverage Audit, the reading.

Google rates its own ads (ad strength) and counts the assets of a campaign against Google's rules. Meta has neither. What Meta gives for the same questions ("are the ads any
good?", "does every ad set have enough to work with?") is, per ad:

    the ad            its status, ad set, campaign, and the creative: video, carousel or image, whether it is a flexible (dynamic) creative and how many texts it carries
    its delivery      the last 30 days: spend, impressions, reach, frequency, link clicks and the results the campaign's objective counts
    Meta's rankings   quality, engagement rate and conversion rate ranking, each against the ads competing for the same audience: above average, average, or below average
                      (the bottom 35, 20 or 10 percent). Meta only ranks an ad after 500 impressions, and says UNKNOWN before.

This module only reads and shapes. What the numbers mean for the work is tracker/paid_meta_creative_analyse.py (the quality view for Ad Strength, the coverage view for Asset
Coverage); both views are made from the one read here and the read is kept with the shared cache (tracker/paid_geo.py _cached), so opening the second page costs no Meta read.
Read only: nothing is changed in an account.
"""
from __future__ import annotations

import datetime as dt
import json

from . import paid_geo as geo
from . import paid_meta as meta

WINDOW_DAYS = 30
MAX_ADS = 5000
PAGE = 100

# The ads that are live, and the ones Meta stopped or limited (those are the ones with something to fix).
LIVE_STATUSES = ["ACTIVE", "DISAPPROVED", "WITH_ISSUES"]
PROBLEM_STATUSES = {"DISAPPROVED": "Disapproved", "WITH_ISSUES": "Has issues"}

ADS_FIELDS = ("id,name,effective_status,adset_id,campaign_id,adset{id,name},campaign{id,name,objective},"
              "creative{object_type,video_id,image_hash,call_to_action_type,asset_feed_spec{titles,bodies,images,videos},"
              "object_story_spec{link_data{child_attachments}}}")
INSIGHT_FIELDS = ("ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,objective,spend,impressions,reach,frequency,inline_link_clicks,"
                  "quality_ranking,engagement_rate_ranking,conversion_rate_ranking,actions,action_values")

# Meta's ranking words -> (rank, short label). The higher the rank, the worse.
RANKINGS = {"ABOVE_AVERAGE": (0, "Above average"), "AVERAGE": (1, "Average"), "BELOW_AVERAGE_35": (2, "Below average (bottom 35%)"),
            "BELOW_AVERAGE_20": (3, "Below average (bottom 20%)"), "BELOW_AVERAGE_10": (4, "Below average (bottom 10%)")}


def window(today=None):
    end = (today or dt.date.today()) - dt.timedelta(days=1)
    return end - dt.timedelta(days=WINDOW_DAYS - 1), end


def ads_request(act):
    """(path, params) of the read of the account's live ads and the ones Meta stopped or limited."""
    return "/%s/ads" % act, {"fields": ADS_FIELDS, "effective_status": json.dumps(LIVE_STATUSES), "limit": PAGE}


def insights_request(act, start, end):
    """(path, params) of the last days' numbers and rankings, one row per ad."""
    return "/%s/insights" % act, {"level": "ad", "time_range": json.dumps({"since": start, "until": end}), "fields": INSIGHT_FIELDS, "limit": 500}


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _n(v):
    return len(v) if isinstance(v, list) else 0


def creative_format(c):
    """(format, dynamic, texts): "video", "carousel" or "image" (anything else is "other"), whether it is a flexible creative that mixes assets and texts, and how many
    different headlines and primary texts it carries."""
    c = c if isinstance(c, dict) else {}
    spec = c.get("asset_feed_spec") if isinstance(c.get("asset_feed_spec"), dict) else {}
    link = ((c.get("object_story_spec") or {}).get("link_data")) or {}
    kind = str(c.get("object_type") or "").upper()
    texts = _n(spec.get("titles")) + _n(spec.get("bodies"))
    if c.get("video_id") or kind == "VIDEO" or (_n(spec.get("videos")) and not _n(spec.get("images"))):
        fmt = "video"
    elif _n(link.get("child_attachments")) >= 2:
        fmt = "carousel"
    elif c.get("image_hash") or kind in ("PHOTO", "SHARE", "STATUS", "IMAGE") or _n(spec.get("images")):
        fmt = "image"
    else:
        fmt = "other"
    dynamic = bool(spec) and (_n(spec.get("images")) + _n(spec.get("videos")) > 1 or texts > 2)
    return fmt, dynamic, texts


def read_ads(rows):
    """The ads as plain items. A row with no id is dropped."""
    out = []
    for r in rows:
        aid = str(r.get("id") or "")
        if not aid:
            continue
        ad, camp = r.get("adset") or {}, r.get("campaign") or {}
        fmt, dynamic, texts = creative_format(r.get("creative"))
        out.append({"id": aid, "name": str(r.get("name") or aid), "status": str(r.get("effective_status") or ""),
                    "adsetId": str(r.get("adset_id") or ad.get("id") or ""), "adset": str(ad.get("name") or ""),
                    "campaignId": str(r.get("campaign_id") or camp.get("id") or ""), "campaign": str(camp.get("name") or ""),
                    "objective": str(camp.get("objective") or ""), "format": fmt, "dynamic": dynamic, "texts": texts})
    return out


def read_insights(rows):
    """{ad id: its numbers and rankings}. An unknown ranking word is kept as None (the page says it was not ranked), never made up."""
    out = {}
    for r in rows:
        aid = str(r.get("ad_id") or "")
        if not aid:
            continue
        kind = meta.objective_kind(r.get("objective"))
        conv, value = meta.result_of(kind, r.get("actions"), r.get("action_values"))
        rank = {}
        for key, field in (("quality", "quality_ranking"), ("engagement", "engagement_rate_ranking"), ("conversion", "conversion_rate_ranking")):
            word = str(r.get(field) or "").upper()
            rank[key] = word if word in RANKINGS else None
        out[aid] = {"adsetId": str(r.get("adset_id") or ""), "adset": str(r.get("adset_name") or ""), "campaignId": str(r.get("campaign_id") or ""),
                    "campaign": str(r.get("campaign_name") or ""), "name": str(r.get("ad_name") or ""), "objective": kind, "spend": round(_f(r.get("spend")), 2),
                    "impressions": int(_f(r.get("impressions"))), "reach": int(_f(r.get("reach"))), "frequency": round(_f(r.get("frequency")), 2),
                    "clicks": int(_f(r.get("inline_link_clicks"))), "results": round(conv, 2), "value": round(value, 2), "rank": rank}
    return out


def raw_read(act, *, pages_fn, today=None):
    """The raw read of one Meta ad account. The ads are the read (a failure there is the error); the numbers are a soft part: without them the coverage view is
    complete and the quality view says it could not rank anything."""
    start, end = window(today)
    warnings = []
    path, params = ads_request(act)
    ads = read_ads(pages_fn(path, params))
    if len(ads) > MAX_ADS:
        warnings.append("The account has more than %d live ads, so the first %d are read." % (MAX_ADS, MAX_ADS))
        ads = ads[:MAX_ADS]
    insights = {}
    try:
        path, params = insights_request(act, start.isoformat(), end.isoformat())
        insights = read_insights(pages_fn(path, params))
    except meta.AdsError as e:
        warnings.append("Meta's numbers and quality rankings for the last %d days could not be read (%s), so only what the ads are made of is shown." % (WINDOW_DAYS, e))
    return {"ads": ads, "insights": insights, "warnings": warnings, "window": {"from": start.isoformat(), "to": end.isoformat(), "days": WINDOW_DAYS},
            "insightsRead": not any("could not be read" in w for w in warnings)}


def raw_for(account_id, *, store=None, force=False, now=None, today=None, pages_fn=None, account_fn=None):
    """(raw, stale, account): the saved read of one Meta ad account (act_...), read from Meta when there is none or when `force`. Ad Strength, Asset Coverage and Creative & Asset Health
    share it, so opening a second page costs no Meta read."""
    from . import paid_store
    store = store or paid_store
    if not meta.is_meta(str(account_id or "")):
        raise ValueError("That is not a Meta ad account.")
    act = meta.norm_id(account_id)
    day = today or dt.date.today()
    pages = pages_fn or (lambda path, params: meta._pages(path, params, store))
    acct = (account_fn or (lambda a: meta.account_by_id(store, a) or {}))(act) or {}
    account = {"id": act, "name": acct.get("name") or act, "currency": acct.get("currency") or "USD", "platform": "meta", "isGroup": False, "accountCount": 1}
    raw, stale = geo._cached("metacreative", act, "ads", day, day, lambda: raw_read(act, pages_fn=pages, today=day), force=force, store=store, now=now)
    return raw, stale, account


def report_for(account_id, *, store=None, force=False, now=None, today=None, pages_fn=None, account_fn=None, view="quality"):
    """The Meta view ("quality" for Ad Strength, "coverage" for Asset Coverage) of one Meta ad account (act_...). `force` (Refresh) skips the saved read, at most once per
    paid_gads.REFRESH_COOLDOWN_S for the account."""
    from . import paid_meta_creative_analyse as an
    day = today or dt.date.today()
    raw, stale, account = raw_for(account_id, store=store, force=force, now=now, today=day, pages_fn=pages_fn, account_fn=account_fn)
    report = (an.coverage if view == "coverage" else an.quality)(raw, account, day)
    if raw.get("readAt"):
        report["readAt"] = raw["readAt"]
    return dict(report, stale=stale) if stale else report
