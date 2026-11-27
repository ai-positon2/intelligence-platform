"""Paid Marketing Agents: Creative Analysis, the Google Ads reading.

The same shape tracker/paid_creative_roas.py reads from Meta, so the page judges Google creatives with the same rules. Built first in Soteri Skin's stand-alone dashboard
(ai-positon2/soteri-creative-analysis, gads.py); here it runs on the platform's own Google Ads connection (tracker/paid_gads.py: the shared sign-in, the daily allowance and
the saved reads).

What counts as one creative:
  * every ad (search, shopping, YouTube, Demand Gen, display) that cost money in the range, by day: cost, impressions, clicks, conversions, conversion value and video views;
  * every Performance Max asset group (Performance Max has no ads of its own), by day, with its headlines, descriptions and a picture.
"Purchases" are Google's Conversions column (the account's primary conversion actions) and their value its Conv. value, so the ROAS matches the Google Ads interface. For an
account with no conversion value the same conversions are the "leads" (cost per conversion).

Read only: googleAds:search and nothing else.
"""
from __future__ import annotations

from . import paid_gads as gads

FORMATS = {
    "RESPONSIVE_SEARCH_AD": "Search (responsive)", "EXPANDED_TEXT_AD": "Search (text)", "TEXT_AD": "Search (text)",
    "CALL_AD": "Call", "SHOPPING_PRODUCT_AD": "Shopping", "SHOPPING_SMART_AD": "Shopping",
    "VIDEO_AD": "YouTube video", "VIDEO_RESPONSIVE_AD": "YouTube video", "IN_FEED_VIDEO_AD": "YouTube video",
    "VIDEO_BUMPER_AD": "YouTube video", "VIDEO_NON_SKIPPABLE_IN_STREAM_AD": "YouTube video", "VIDEO_TRUEVIEW_IN_STREAM_AD": "YouTube video",
    "DEMAND_GEN_VIDEO_RESPONSIVE_AD": "Demand Gen video", "DEMAND_GEN_MULTI_ASSET_AD": "Demand Gen image",
    "DEMAND_GEN_CAROUSEL_AD": "Demand Gen carousel", "DEMAND_GEN_PRODUCT_AD": "Demand Gen product",
    "DISCOVERY_MULTI_ASSET_AD": "Demand Gen image", "DISCOVERY_CAROUSEL_AD": "Demand Gen carousel",
    "DISCOVERY_VIDEO_RESPONSIVE_AD": "Demand Gen video",
    "RESPONSIVE_DISPLAY_AD": "Display (responsive)", "IMAGE_AD": "Display image", "APP_AD": "App",
}

# What an ad shows. A field Google refuses (renamed in a newer API version) is dropped and the read goes on (paid_gads.search_pruned).
AD_FIELDS = [
    "ad_group_ad.ad.final_urls",
    "ad_group_ad.ad.responsive_search_ad.headlines", "ad_group_ad.ad.responsive_search_ad.descriptions",
    "ad_group_ad.ad.video_ad.video.asset",
    "ad_group_ad.ad.video_responsive_ad.videos", "ad_group_ad.ad.video_responsive_ad.headlines",
    "ad_group_ad.ad.video_responsive_ad.long_headlines", "ad_group_ad.ad.video_responsive_ad.descriptions",
    "ad_group_ad.ad.demand_gen_multi_asset_ad.marketing_images", "ad_group_ad.ad.demand_gen_multi_asset_ad.square_marketing_images",
    "ad_group_ad.ad.demand_gen_multi_asset_ad.portrait_marketing_images",
    "ad_group_ad.ad.demand_gen_multi_asset_ad.headlines", "ad_group_ad.ad.demand_gen_multi_asset_ad.descriptions",
    "ad_group_ad.ad.demand_gen_video_responsive_ad.videos", "ad_group_ad.ad.demand_gen_video_responsive_ad.headlines",
    "ad_group_ad.ad.demand_gen_video_responsive_ad.long_headlines", "ad_group_ad.ad.demand_gen_video_responsive_ad.descriptions",
    "ad_group_ad.ad.demand_gen_carousel_ad.headline", "ad_group_ad.ad.demand_gen_carousel_ad.description",
    "ad_group_ad.ad.responsive_display_ad.marketing_images", "ad_group_ad.ad.responsive_display_ad.square_marketing_images",
    "ad_group_ad.ad.responsive_display_ad.headlines", "ad_group_ad.ad.responsive_display_ad.long_headline",
    "ad_group_ad.ad.responsive_display_ad.descriptions",
    "ad_group_ad.ad.image_ad.image_url",
]
VIDEO_METRICS = ["metrics.video_trueview_views", "metrics.video_quartile_p100_rate"]
_BAD = set()                       # fields Google refused (per worker, until restart)


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return x if x == x else 0.0


def _row(r, ad_id, name, adset, campaign):
    m = r.get("metrics") or {}
    impr = int(_f(m.get("impressions")))
    views = _f(m.get("videoTrueviewViews"))
    conv = _f(m.get("conversions"))
    return {"date": str((r.get("segments") or {}).get("date") or "")[:10], "ad_id": ad_id, "ad_name": name, "adset": adset, "campaign": campaign,
            "spend": _f(m.get("costMicros")) / 1e6, "impr": impr, "clicks": int(_f(m.get("clicks"))), "purch": conv, "rev": _f(m.get("conversionsValue")),
            "atc": 0.0, "v3": views, "thru": float(round(_f(m.get("videoQuartileP100Rate")) * impr)) if views else 0.0, "leads": conv}


def status_of(*parts):
    """ACTIVE when the ad, its ad group and its campaign are all enabled (what the page calls running)."""
    if any(p == "REMOVED" for p in parts):
        return "REMOVED"
    return "ACTIVE" if parts and all(p == "ENABLED" for p in parts) else "PAUSED"


def _camp_status(camp):
    s = camp.get("status") or ""
    return "ACTIVE" if s == "ENABLED" else s


def _texts(lst):
    return [" ".join(str((x or {}).get("text") or "").split()) for x in (lst or []) if (x or {}).get("text")]


def first_headline(lst):
    """The headline that shows first: one pinned to position 1, else the first written."""
    lst = lst or []
    pinned = [x for x in lst if (x or {}).get("pinnedField") == "HEADLINE_1" and x.get("text")]
    t = _texts(pinned or lst)
    return t[0] if t else ""


def _assets(lst):
    return [(x or {}).get("asset") for x in (lst or []) if (x or {}).get("asset")]


def describe_ad(ad, ad_type):
    """Format, opening headline, copy and the assets to look up for a picture, from one ad_group_ad.ad."""
    ad = ad or {}
    fmt = FORMATS.get(ad_type, (ad_type or "Other").replace("_", " ").capitalize())
    rsa, vra = ad.get("responsiveSearchAd") or {}, ad.get("videoResponsiveAd") or {}
    dgm, dgv = ad.get("demandGenMultiAssetAd") or {}, ad.get("demandGenVideoResponsiveAd") or {}
    dgc, rda = ad.get("demandGenCarouselAd") or {}, ad.get("responsiveDisplayAd") or {}
    heads = rsa.get("headlines") or vra.get("headlines") or dgm.get("headlines") or dgv.get("headlines") or rda.get("headlines") or []
    longs = _texts(vra.get("longHeadlines") or dgv.get("longHeadlines"))
    if not heads and dgc.get("headline"):
        heads = [{"text": dgc["headline"]}]
    if not heads and (rda.get("longHeadline") or {}).get("text"):
        heads = [rda["longHeadline"]]
    descs = _texts(rsa.get("descriptions") or vra.get("descriptions") or dgm.get("descriptions") or dgv.get("descriptions") or rda.get("descriptions"))
    if not descs and dgc.get("description"):
        descs = [dgc["description"]]
    videos = _assets(vra.get("videos") or dgv.get("videos")) + ([ad["videoAd"]["video"]["asset"]] if ((ad.get("videoAd") or {}).get("video") or {}).get("asset") else [])
    images = _assets(dgm.get("marketingImages") or dgm.get("squareMarketingImages") or dgm.get("portraitMarketingImages")
                     or rda.get("marketingImages") or rda.get("squareMarketingImages"))
    return {"format": fmt, "hook": first_headline(heads) or (longs[0] if longs else ""), "headline": " | ".join(_texts(heads)[:15]), "body": "\n".join(descs),
            "cta": "", "partnership": None, "partnership_ids": "", "creative_id": "", "landing": (ad.get("finalUrls") or [""])[0],
            "_video": videos[0] if videos else "", "_image": images[0] if images else "", "thumb": (ad.get("imageAd") or {}).get("imageUrl") or "",
            "video_id": "", "post_url": ""}


def read_ads(customer, since, until, *, search, search_pruned):
    """(rows, ads): every ad that cost money in the range, by day, and what each shows."""
    def build(fields):
        return ("SELECT segments.date, campaign.id, campaign.name, campaign.status, ad_group.id, ad_group.name, ad_group.status, "
                "ad_group_ad.status, ad_group_ad.ad.id, ad_group_ad.ad.name, ad_group_ad.ad.type, "
                "metrics.cost_micros, metrics.impressions, metrics.clicks, metrics.conversions, metrics.conversions_value%s "
                "FROM ad_group_ad WHERE segments.date BETWEEN '%s' AND '%s' AND metrics.cost_micros > 0"
                % ("".join(", " + f for f in fields), since, until))
    got, _used = search_pruned(customer, build, VIDEO_METRICS + AD_FIELDS, _BAD)
    rows, ads = [], {}
    for r in got:
        aga = r.get("adGroupAd") or {}
        ad = aga.get("ad") or {}
        ad_id = str(ad.get("id") or "")
        if not ad_id:
            continue
        camp, grp = r.get("campaign") or {}, r.get("adGroup") or {}
        if ad_id not in ads:
            info = describe_ad(ad, ad.get("type"))
            name = (ad.get("name") or "").strip() or (grp.get("name") or "Ad group")
            info.update({"id": ad_id, "name": name, "status": status_of(aga.get("status"), grp.get("status"), camp.get("status")),
                         "campaign_status": _camp_status(camp), "adset_status": grp.get("status") or "",
                         "campaign": camp.get("name") or "", "adset": grp.get("name") or "", "created": ""})
            ads[ad_id] = info
        a = ads[ad_id]
        rows.append(_row(r, ad_id, a["name"], a["adset"], a["campaign"]))
    return rows, ads


def read_asset_groups(customer, since, until, *, search):
    """(rows, ads): Performance Max, one creative per asset group, by day, with its headlines, descriptions and the assets to picture it by."""
    got = search(customer, "SELECT segments.date, campaign.id, campaign.name, campaign.status, asset_group.id, asset_group.name, "
                           "asset_group.status, metrics.cost_micros, metrics.impressions, metrics.clicks, metrics.conversions, "
                           "metrics.conversions_value FROM asset_group WHERE segments.date BETWEEN '%s' AND '%s' "
                           "AND metrics.cost_micros > 0" % (since, until))
    rows, ads = [], {}
    for r in got:
        g, camp = r.get("assetGroup") or {}, r.get("campaign") or {}
        key = "pmax-%s" % g.get("id")
        if key not in ads:
            ads[key] = {"id": key, "name": g.get("name") or "Asset group", "format": "Performance Max", "status": status_of(g.get("status"), camp.get("status")),
                        "campaign_status": _camp_status(camp), "adset_status": g.get("status") or "", "campaign": camp.get("name") or "", "adset": "Asset group",
                        "created": "", "hook": "", "headline": "", "body": "", "cta": "", "partnership": None, "partnership_ids": "", "creative_id": "",
                        "landing": "", "thumb": "", "video_id": "", "post_url": "", "_video": "", "_image": ""}
        rows.append(_row(r, key, ads[key]["name"], "Asset group", ads[key]["campaign"]))
    if not ads:
        return rows, ads
    got = search(customer, "SELECT asset_group.id, asset_group_asset.field_type, asset.text_asset.text, asset.resource_name "
                           "FROM asset_group_asset WHERE asset_group_asset.status != 'REMOVED' "
                           "AND asset_group_asset.field_type IN ('HEADLINE', 'DESCRIPTION', 'MARKETING_IMAGE', 'SQUARE_MARKETING_IMAGE', 'YOUTUBE_VIDEO')")
    heads, descs = {}, {}
    for r in got:
        key = "pmax-%s" % (r.get("assetGroup") or {}).get("id")
        a = ads.get(key)
        if not a:
            continue
        ft = (r.get("assetGroupAsset") or {}).get("fieldType")
        asset = r.get("asset") or {}
        text = " ".join(str((asset.get("textAsset") or {}).get("text") or "").split())
        if ft == "HEADLINE" and text:
            heads.setdefault(key, []).append(text)
        elif ft == "DESCRIPTION" and text:
            descs.setdefault(key, []).append(text)
        elif ft == "YOUTUBE_VIDEO" and not a["_video"]:
            a["_video"] = asset.get("resourceName") or ""
        elif ft in ("MARKETING_IMAGE", "SQUARE_MARKETING_IMAGE") and not a["_image"]:
            a["_image"] = asset.get("resourceName") or ""
    for key, a in ads.items():
        a["hook"] = (heads.get(key) or [""])[0]
        a["headline"] = " | ".join((heads.get(key) or [])[:15])
        a["body"] = "\n".join(descs.get(key) or [])
    return rows, ads


def fill_pictures(customer, ads, *, search):
    """Image URLs and YouTube ids of the assets the creatives point at, three hundred per request."""
    names = sorted({a[k] for a in ads.values() for k in ("_video", "_image") if a.get(k)})
    found = {}
    for i in range(0, len(names), 300):
        ids = ", ".join(n.rsplit("/", 1)[-1] for n in names[i:i + 300])
        for r in search(customer, "SELECT asset.resource_name, asset.type, asset.image_asset.full_size.url, asset.youtube_video_asset.youtube_video_id "
                                  "FROM asset WHERE asset.id IN (%s)" % ids):
            a = r.get("asset") or {}
            found[a.get("resourceName")] = a
    for a in ads.values():
        v = found.get(a.get("_video") or "", {})
        yt = (v.get("youtubeVideoAsset") or {}).get("youtubeVideoId")
        if yt:
            a["video_id"] = yt
            a["post_url"] = "https://www.youtube.com/watch?v=" + yt
            a["thumb"] = a["thumb"] or "https://i.ytimg.com/vi/%s/mqdefault.jpg" % yt
        img = found.get(a.get("_image") or "", {})
        a["thumb"] = a["thumb"] or ((img.get("imageAsset") or {}).get("fullSize") or {}).get("url") or ""


def _clean(ads):
    for a in ads.values():
        a.pop("_video", None)
        a.pop("_image", None)
    return ads


def read_account(customer, since, until, *, store=None, search_fn=None, search_pruned_fn=None):
    """{rows, ads, since, until, warnings}: the raw read of one Google Ads account. The ads' daily rows are the point (a failure there is raised); Performance Max and the
    pictures are soft: a part that could not be read is said in `warnings`."""
    search = search_fn or (lambda c, q: gads.search(c, q, store))
    pruned = search_pruned_fn or (lambda c, build, optional, bad: gads.search_pruned(c, build, optional, bad, store))
    s, e = since.isoformat(), until.isoformat()
    warnings = []
    rows, ads = read_ads(customer, s, e, search=search, search_pruned=pruned)
    try:
        r2, a2 = read_asset_groups(customer, s, e, search=search)
        rows += r2
        ads.update(a2)
    except gads.OverQuota:
        raise
    except gads.AdsError as err:
        warnings.append("Performance Max could not be read (%s), so its asset groups are left out." % str(err)[:160])
    try:
        fill_pictures(customer, ads, search=search)
    except gads.OverQuota:
        raise
    except gads.AdsError as err:
        warnings.append("The pictures could not be read (%s)." % str(err)[:160])
    return {"rows": [r for r in rows if r["date"]], "ads": _clean(ads), "since": s, "until": e, "warnings": warnings, "extrasRead": 0, "extrasCapped": False}
