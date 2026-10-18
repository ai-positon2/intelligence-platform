"""Paid Marketing Agents: Landing Page & Relevancy Check, what Meta says about each landing page.

The same job as tracker/paid_lp_read.py for a Meta ad account, in the same shape, so the same checks and the same page can hold a Meta page
against its ads. Read only, over the last 30 days:

  ads        each ad's creative: the address it sends people to and the words it uses (headline, primary text, description)
  insights   each ad's spend, impressions, link clicks and results (the campaign objective decides what a result is, see tracker/paid_meta.py)
  devices    the same by device (desktop, phone, tablet)
  rankings   Meta's own three ratings of each ad (quality, engagement rate, conversion rate), which Meta gives only to ads with enough impressions
  ad sets    where an ad set sends people when its ads carry no web address (an instant form, a message, a call, an app)

A Meta ad set stands where a Google ad group stands: it holds the ads, and the ads' words are what the page is held against. Meta has no keywords
and no search terms, so those are empty. A page is its host and path (tracker/paid_lp_read.py page_id), so a page that Google and Meta both send
people to is one page.

Not every ad sends people to a web page. An ad for an instant form, a message, a call, an app, or one that shows a post whose link the ad record
does not carry has no landing page to check. Their spend is counted and said, never guessed at. A part that could not be read is a warning and its
`ok` flag is False; the ads and the insights are the point, so their failure is raised.
"""
from __future__ import annotations

import datetime as dt
import json
from urllib.parse import urlsplit

from . import paid_lp_read as lp
from . import paid_meta as meta

WINDOW = "last_30d"
AD_STATUSES = ["ACTIVE", "PAUSED", "CAMPAIGN_PAUSED", "ADSET_PAUSED", "WITH_ISSUES", "PENDING_REVIEW"]
ADS_FIELDS = ("id,name,adset_id,campaign_id,effective_status,"
              "creative{object_story_spec,asset_feed_spec,title,body,object_url,template_url,effective_object_story_id}")
INSIGHT_FIELDS = "ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,objective,spend,impressions,inline_link_clicks,actions,action_values"
DEVICE_FIELDS = "ad_id,objective,spend,impressions,inline_link_clicks,actions,action_values"
RANK_FIELDS = "ad_id,quality_ranking,engagement_rate_ranking,conversion_rate_ranking"
ADSET_FIELDS = "id,destination_type"
ADS_PAGE = 100                                    # ads per request; a creative is a big record, so a smaller page is tried when Meta says the answer is too large
ADS_PAGE_SMALL = 25
MAX_URLS_PER_AD = 5
MAX_OFF_KINDS = 6

# Where people land that is not a page of the client's own site.
META_HOSTS = ("facebook.com", "fb.com", "fb.me", "fb.watch", "m.me", "messenger.com", "instagram.com", "wa.me", "whatsapp.com")
DEVICES = {"desktop": "desktop", "iphone": "mobile", "ipod": "mobile", "android_smartphone": "mobile", "ipad": "tablet", "android_tablet": "tablet"}
RANKS = {"ABOVE_AVERAGE": "above", "AVERAGE": "average", "BELOW_AVERAGE_10": "below", "BELOW_AVERAGE_20": "below", "BELOW_AVERAGE_35": "below"}
OFF_PAGE = {"ON_AD": "an instant form", "MESSENGER": "a message thread", "WHATSAPP": "a message thread", "INSTAGRAM_DIRECT": "a message thread",
            "MESSAGING_MESSENGER_WHATSAPP": "a message thread", "MESSAGING_INSTAGRAM_DIRECT_MESSENGER": "a message thread",
            "MESSAGING_INSTAGRAM_DIRECT_MESSENGER_WHATSAPP": "a message thread", "PHONE_CALL": "a phone call", "APP": "an app store page",
            "ON_POST": "Facebook or Instagram itself", "ON_PAGE": "Facebook or Instagram itself", "ON_VIDEO": "Facebook or Instagram itself",
            "ON_EVENT": "Facebook or Instagram itself", "SHOP_AUTOMATIC": "Facebook or Instagram itself", "INSTAGRAM_PROFILE": "Facebook or Instagram itself",
            "FACEBOOK": "Facebook or Instagram itself", "INSTAGRAM_PROFILE_AND_FACEBOOK_PAGE": "Facebook or Instagram itself"}
KIND_POST = "an existing post whose link the ad record does not carry"
KIND_META = "Facebook or Instagram itself"
KIND_GONE = "ads no longer in the account"
KIND_UNKNOWN = "somewhere that could not be read"


def _is_meta_host(url):
    host = (urlsplit(str(url or "")).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in META_HOSTS)


def _dig(obj, *path):
    for p in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(p)
    return obj


def _list(obj, *path):
    v = _dig(obj, *path)
    return v if isinstance(v, list) else []


# ── The creative ────────────────────────────────────────────────────────────

def creative_urls(c):
    """The distinct web addresses an ad's creative sends people to, as (urls, on_meta): the addresses on the client's side, and whether the creative also names an address on Facebook or
    Instagram themselves (which is not a landing page). The addresses are read from every place Meta keeps one: a link ad, a video ad's button, a carousel's cards, a
    dynamic creative's list of websites, a template and the creative's own address."""
    c = c if isinstance(c, dict) else {}
    spec = c.get("object_story_spec") or {}
    found = [_dig(spec, "link_data", "link"), _dig(spec, "link_data", "call_to_action", "value", "link"), _dig(spec, "video_data", "call_to_action", "value", "link"),
             _dig(spec, "template_data", "link"), c.get("object_url"), c.get("template_url")]
    for card in _list(spec, "link_data", "child_attachments") + _list(spec, "template_data", "child_attachments"):
        found += [(card or {}).get("link"), _dig(card, "call_to_action", "value", "link")]
    for u in _list(c, "asset_feed_spec", "link_urls"):
        found.append((u or {}).get("website_url"))
    urls, on_meta = [], False
    for raw in found:
        u = lp._valid(raw)
        if not u:
            continue
        if _is_meta_host(u):
            on_meta = True
        elif u not in urls and len(urls) < MAX_URLS_PER_AD:
            urls.append(u)
    return urls, on_meta


def creative_text(c):
    """(headlines, descriptions) of a creative. Meta's headline is the short bold line under the picture; the primary text, the description and the link description are what the ad says
    above and below it, so they are the ad's descriptions (that is where an offer is usually made)."""
    c = c if isinstance(c, dict) else {}
    spec = c.get("object_story_spec") or {}
    heads = [_dig(spec, "link_data", "name"), _dig(spec, "video_data", "title"), _dig(spec, "template_data", "name"), c.get("title")]
    heads += [(t or {}).get("text") for t in _list(c, "asset_feed_spec", "titles")]
    descs = [_dig(spec, "link_data", "message"), _dig(spec, "video_data", "message"), _dig(spec, "template_data", "message"), c.get("body"),
             _dig(spec, "link_data", "description"), _dig(spec, "video_data", "link_description"), _dig(spec, "template_data", "description")]
    descs += [(t or {}).get("text") for t in _list(c, "asset_feed_spec", "bodies")] + [(t or {}).get("text") for t in _list(c, "asset_feed_spec", "descriptions")]
    for card in _list(spec, "link_data", "child_attachments") + _list(spec, "template_data", "child_attachments"):
        heads.append((card or {}).get("name"))
        descs.append((card or {}).get("description"))
    return (lp._texts([{"text": h} for h in heads if isinstance(h, str)], lp.MAX_HEADLINES),
            lp._texts([{"text": d} for d in descs if isinstance(d, str)], lp.MAX_DESCRIPTIONS))


def creative_kind(c):
    c = c if isinstance(c, dict) else {}
    spec = c.get("object_story_spec") or {}
    if "asset_feed_spec" in c:
        return "DYNAMIC"
    if _list(spec, "link_data", "child_attachments") or _list(spec, "template_data", "child_attachments"):
        return "CAROUSEL"
    if "video_data" in spec:
        return "VIDEO"
    if "link_data" in spec:
        return "LINK"
    return "POST" if c.get("effective_object_story_id") and not spec else ""


def parse_ads(rows):
    """{ad id: {urls, onMeta, headlines, descriptions, type, post}} from the ads list. `post` is True for an ad that only shows an existing post (no creative spec, so no link to read)."""
    out = {}
    for r in rows:
        aid = str(r.get("id") or "")
        if not aid:
            continue
        c = r.get("creative") or {}
        urls, on_meta = creative_urls(c)
        heads, descs = creative_text(c)
        out[aid] = {"urls": urls, "onMeta": on_meta, "headlines": heads, "descriptions": descs, "type": creative_kind(c),
                    "post": bool(c.get("effective_object_story_id")) and not (c.get("object_story_spec") or c.get("asset_feed_spec"))}
    return out


# ── The numbers ─────────────────────────────────────────────────────────────

def parse_insights(rows):
    """{ad id: {name, adSet, adSetName, campaign, campaignName, kind, impressions, clicks, cost, conversions, value}} for the ads that spent."""
    out = {}
    for r in rows:
        aid = str(r.get("ad_id") or "")
        n = meta.numbers(r)
        if not aid or n[2] <= 0:
            continue
        out[aid] = {"name": r.get("ad_name") or aid, "adSet": str(r.get("adset_id") or ""), "adSetName": r.get("adset_name") or "", "campaign": str(r.get("campaign_id") or ""),
                    "campaignName": r.get("campaign_name") or "", "kind": meta.objective_kind(r.get("objective")), "impressions": n[0], "clicks": n[1],
                    "cost": round(n[2], 2), "conversions": round(n[3], 4), "value": round(n[4], 2)}
    return out


def parse_devices(rows):
    """{ad id: {mobile|desktop|tablet|other: {clicks, cost, conversions}}}."""
    out = {}
    for r in rows:
        aid = str(r.get("ad_id") or "")
        if not aid:
            continue
        n = meta.numbers(r)
        d = out.setdefault(aid, {}).setdefault(DEVICES.get(str(r.get("impression_device") or "").lower(), "other"), {"clicks": 0, "cost": 0.0, "conversions": 0.0})
        d["clicks"] += n[1]
        d["cost"] = round(d["cost"] + n[2], 2)
        d["conversions"] = round(d["conversions"] + n[3], 4)
    return out


def parse_rankings(rows):
    """{ad id: {quality, engagement, conversion}}, each above, average, below or None (Meta gives no ranking to an ad with too few impressions)."""
    out = {}
    for r in rows:
        aid = str(r.get("ad_id") or "")
        if aid:
            out[aid] = {"quality": RANKS.get(str(r.get("quality_ranking") or "").upper()), "engagement": RANKS.get(str(r.get("engagement_rate_ranking") or "").upper()),
                        "conversion": RANKS.get(str(r.get("conversion_rate_ranking") or "").upper())}
    return out


def parse_adset_kinds(rows):
    """{ad set id: where its ads send people when they carry no web address}."""
    return {str(r.get("id")): OFF_PAGE.get(str(r.get("destination_type") or "").upper(), "") for r in rows if r.get("id")}


# ── Joining ─────────────────────────────────────────────────────────────────

def build(insights, ads, devices=None, ranks=None, kinds=None, keep=lp.KEEP_PAGES):
    """(pages, ad sets, pageCount, pageCost, off) from the parts. Each ad's spend is split evenly between the distinct pages its creative sends people to. `off` is what the ads that
    reach no web page of the client's took: {spend, ads, kinds: [{kind, spend, ads}]}."""
    devices, ranks, kinds = devices or {}, ranks or {}, kinds or {}
    pages, groups, off = {}, {}, {}
    for aid, i in sorted(insights.items(), key=lambda kv: (-kv[1]["cost"], kv[0])):
        ad = ads.get(aid)
        if ad is None:
            where = KIND_GONE
        elif ad["urls"]:
            where = ""
        elif ad["onMeta"]:
            where = KIND_META
        elif ad["post"]:
            where = KIND_POST
        else:
            where = kinds.get(i["adSet"]) or KIND_UNKNOWN
        if where:
            row = off.setdefault(where, {"kind": where, "spend": 0.0, "ads": 0})
            row["spend"] = round(row["spend"] + i["cost"], 2)
            row["ads"] += 1
            continue
        by_id = {}
        for u in ad["urls"]:
            by_id.setdefault(lp.page_id(u), u)                  # the same page by two addresses is one page
        share = len(by_id)
        for pid, u in by_id.items():
            p = pages.setdefault(pid, {"id": pid, "name": lp.label(u), "templated": lp.templated(u), "url": u, "impressions": 0, "clicks": 0, "cost": 0.0, "conversions": 0.0,
                                       "value": 0.0, "speed": None, "devices": {}, "platforms": ["meta"], "_top": -1.0})
            p["impressions"] += int(i["impressions"] / share)
            p["clicks"] += int(i["clicks"] / share)
            p["cost"] = round(p["cost"] + i["cost"] / share, 2)
            p["conversions"] = round(p["conversions"] + i["conversions"] / share, 4)
            p["value"] = round(p["value"] + i["value"] / share, 2)
            for dev, d in (devices.get(aid) or {}).items():
                t = p["devices"].setdefault(dev, {"clicks": 0, "cost": 0.0, "conversions": 0.0})
                t["clicks"] += int(d["clicks"] / share)
                t["cost"] = round(t["cost"] + d["cost"] / share, 2)
                t["conversions"] = round(t["conversions"] + d["conversions"] / share, 4)
            if i["cost"] / share > p["_top"]:                  # the address shown is the variant that spent most
                p["_top"], p["url"], p["name"] = i["cost"] / share, u, lp.label(u)
        gid = "meta:" + i["adSet"]
        g = groups.setdefault(gid, {"id": gid, "name": i["adSetName"] or i["adSet"], "campaign": i["campaignName"], "campaignType": "META_" + i["kind"], "cost": 0.0, "clicks": 0,
                                    "conversions": 0.0, "ads": [], "keywords": [], "terms": [], "platform": "meta"})
        g["cost"] = round(g["cost"] + i["cost"], 2)
        g["clicks"] += i["clicks"]
        g["conversions"] = round(g["conversions"] + i["conversions"], 4)
        if len(g["ads"]) < lp.MAX_ADS:                          # the ads that spent most (the loop is in that order)
            g["ads"].append({"name": i["name"], "headlines": ad["headlines"], "descriptions": ad["descriptions"], "urls": list(by_id.values()), "cost": i["cost"],
                             "type": ad["type"], "ranks": ranks.get(aid) or {}})
    page_rows = sorted(pages.values(), key=lambda p: (-p["cost"], p["id"]))
    for p in page_rows:
        p.pop("_top", None)
    spend_off = round(sum(r["spend"] for r in off.values()), 2)
    off_rows = sorted(off.values(), key=lambda r: (-r["spend"], r["kind"]))[:MAX_OFF_KINDS]
    kept, used = lp.join(page_rows, groups, {}, {}, keep=keep)
    return kept, used, len(page_rows), round(sum(p["cost"] for p in page_rows), 2), {"spend": spend_off, "ads": sum(r["ads"] for r in off.values()), "kinds": off_rows}


# ── The whole read ──────────────────────────────────────────────────────────

def _q(**kw):
    return kw


def read_account(act, *, pages, now=None, keep=lp.KEEP_PAGES):
    """{pages, pageCount, pageCost, adGroups, off, ok, warnings, readAt}: the Meta side of the landing page read. `pages(path, params)` is the Graph API call that gives every
    row of a list (paid_meta._pages bound to a store). The ads and the insights are the point, so a failure there is raised; every other part is a warning with its `ok`
    flag False. Meta asking to wait (paid_meta.OverQuota) stops the read and is raised: the rest would only fail the same way."""
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

    def ads_rows():
        statuses = json.dumps(AD_STATUSES)
        try:
            return pages(act + "/ads", _q(fields=ADS_FIELDS, effective_status=statuses, limit=ADS_PAGE))
        except meta.AdsError:
            return pages(act + "/ads", _q(fields=ADS_FIELDS, effective_status=statuses, limit=ADS_PAGE_SMALL))

    def insight(fields, **extra):
        return pages(act + "/insights", _q(level="ad", date_preset=WINDOW, fields=fields, limit=500, **extra))
    ad_rows = part("ads", "the ads and where they send people", ads_rows, required=True)
    stats = part("insights", "what the ads spent and took", lambda: parse_insights(insight(INSIGHT_FIELDS)), required=True)
    devices = part("devices", "the split by device", lambda: parse_devices(insight(DEVICE_FIELDS, breakdowns="impression_device")))
    ranks = part("rankings", "Meta's own ratings of the ads", lambda: parse_rankings(insight(RANK_FIELDS)))
    kinds = part("adSets", "where the ad sets send people", lambda: parse_adset_kinds(pages(act + "/adsets", _q(fields=ADSET_FIELDS, limit=500))))
    kept, used, count, cost, off = build(stats, parse_ads(ad_rows), devices, ranks, kinds, keep=keep)
    return {"pages": kept, "pageCount": count, "pageCost": cost, "adGroups": used, "off": dict(off, total=round(sum(i["cost"] for i in stats.values()), 2)),
            "ok": ok, "warnings": warnings, "readAt": now.isoformat()}
