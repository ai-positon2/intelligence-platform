"""Paid Marketing Agents: Competitor Analysis, what kind of ads WE run (the reads are passed in, so all of it is tested without a network).

The words of our ads are the Ad Copy Checker's read (tracker/paid_copycheck.py). That read keeps text only, so the format, the button, the placements and the start day of our ads are read here, in
small reads of their own, and kept for the day like the other agents' reads (tracker/paid_geo.py _cached), so the report costs one read a day per account:

  * Google: every enabled ad's type (one row per ad), and the pictures and videos of each enabled Performance Max asset group (an asset group can show as text, image and video);
  * Meta: every active ad's creative (its format, its button), its ad set's placements and the day it was made.

What comes out is a list of "units" for tracker/paid_compete_mix.py: {kind, formats, ctaType, networks, automatic, created}.
"""
from __future__ import annotations

import json

from . import paid_compete_mix as mix

_ENABLED = "campaign.status = ENABLED AND ad_group.status = ENABLED AND ad_group_ad.status = ENABLED"
GAQL_AD_TYPES = "SELECT campaign.advertising_channel_type, ad_group_ad.ad.type FROM ad_group_ad WHERE %s" % _ENABLED
GAQL_PMAX_MEDIA = ("SELECT asset_group.id, asset_group_asset.field_type FROM asset_group_asset WHERE campaign.status = ENABLED AND asset_group.status = ENABLED "
                   "AND asset_group_asset.status = ENABLED AND asset_group_asset.field_type IN ('HEADLINE', 'MARKETING_IMAGE', 'SQUARE_MARKETING_IMAGE', 'PORTRAIT_MARKETING_IMAGE', 'YOUTUBE_VIDEO')")
META_FIELDS = "id,created_time,adset{id,targeting},creative{object_type,call_to_action_type,video_id,object_story_spec,asset_feed_spec}"

# Google's ad types as the Transparency Center's formats (text, image, video); a type not named here is left out of the mix
GOOGLE_FORMAT = {
    "RESPONSIVE_SEARCH_AD": "text", "EXPANDED_TEXT_AD": "text", "TEXT_AD": "text", "CALL_AD": "text", "EXPANDED_DYNAMIC_SEARCH_AD": "text", "DYNAMIC_SEARCH_AD": "text",
    "RESPONSIVE_DISPLAY_AD": "image", "LEGACY_RESPONSIVE_DISPLAY_AD": "image", "IMAGE_AD": "image", "DEMAND_GEN_MULTI_ASSET_AD": "image", "DEMAND_GEN_CAROUSEL_AD": "image",
    "DISCOVERY_MULTI_ASSET_AD": "image", "DISCOVERY_CAROUSEL_AD": "image", "SHOPPING_PRODUCT_AD": "image", "SHOPPING_SMART_AD": "image", "SHOPPING_COMPARISON_LISTING_AD": "image",
    "LOCAL_AD": "image", "SMART_CAMPAIGN_AD": "text", "APP_AD": "image", "APP_ENGAGEMENT_AD": "image",
    "VIDEO_AD": "video", "VIDEO_RESPONSIVE_AD": "video", "VIDEO_BUMPER_AD": "video", "VIDEO_NON_SKIPPABLE_IN_STREAM_AD": "video", "VIDEO_TRUEVIEW_IN_STREAM_AD": "video",
    "VIDEO_OUTSTREAM_AD": "video", "IN_FEED_VIDEO_AD": "video", "DEMAND_GEN_VIDEO_RESPONSIVE_AD": "video", "DISCOVERY_VIDEO_RESPONSIVE_AD": "video", "HTML5_UPLOAD_AD": "image",
}
_PMAX_FORMAT = {"HEADLINE": "text", "MARKETING_IMAGE": "image", "SQUARE_MARKETING_IMAGE": "image", "PORTRAIT_MARKETING_IMAGE": "image", "YOUTUBE_VIDEO": "video"}


def _get(row, *path):                                                            # the API's rows, keyed in camelCase
    for p in path:
        if not isinstance(row, dict):
            return None
        row = row.get(p)
    return row


def google_units(ad_rows, pmax_rows):
    """Our Google units: one per enabled ad whose type has a format, one per enabled Performance Max asset group with the formats it can show."""
    out = []
    for r in ad_rows or []:
        f = GOOGLE_FORMAT.get(str(_get(r, "adGroupAd", "ad", "type") or "").upper())
        if f:
            out.append({"kind": "ad", "formats": [f], "channel": str(_get(r, "campaign", "advertisingChannelType") or "")})
    groups = {}
    for r in pmax_rows or []:
        gid = str(_get(r, "assetGroup", "id") or "")
        f = _PMAX_FORMAT.get(str(_get(r, "assetGroupAsset", "fieldType") or "").upper())
        if gid and f:
            groups.setdefault(gid, set()).add(f)
    for gid in sorted(groups):
        out.append({"kind": "pmax", "formats": sorted(groups[gid] | {"text"}), "id": gid})
    return out


def meta_format(creative):
    """Our Meta ad's format, in the Ad Library's words: dynamic (several versions, a flexible ad), catalog, carousel, video or image."""
    cr = creative or {}
    story = cr.get("object_story_spec") or {}
    if cr.get("asset_feed_spec"):
        return "dynamic"
    if story.get("template_data"):
        return "catalog"
    link = story.get("link_data") or {}
    if len(link.get("child_attachments") or []) > 1:
        return "carousel"
    if story.get("video_data") or cr.get("video_id") or str(cr.get("object_type") or "").upper() == "VIDEO":
        return "video"
    return "image"


def meta_cta(creative):
    cr = creative or {}
    story = cr.get("object_story_spec") or {}
    for src in (story.get("link_data") or {}, story.get("video_data") or {}, story.get("template_data") or {}):
        t = (src.get("call_to_action") or {}).get("type")
        if t:
            return str(t).upper()
    types = (cr.get("asset_feed_spec") or {}).get("call_to_action_types") or []
    return str(cr.get("call_to_action_type") or (types[0] if types else "") or "").upper()


def meta_units(rows):
    """Our Meta units: one per active ad. An ad set with no placements chosen runs on automatic placements, which can show everywhere Meta places ads."""
    out = []
    for r in rows or []:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        cr = r.get("creative") or {}
        chosen = [str(p).lower() for p in ((r.get("adset") or {}).get("targeting") or {}).get("publisher_platforms") or []]
        out.append({"kind": "ad", "formats": [meta_format(cr)], "ctaType": meta_cta(cr), "networks": chosen or list(mix.META_NETWORKS), "automatic": not chosen,
                    "created": str(r.get("created_time") or "")[:10]})
    return out


def meta_request(act):
    return "/%s/ads" % act, {"fields": META_FIELDS, "effective_status": json.dumps(["ACTIVE"]), "limit": 100}


def read_google(account_id, search):
    """{units, error}: the ad types are the point (a failure says so); the Performance Max media are soft."""
    from . import paid_gads_segments as gads
    try:
        ads = search(account_id, GAQL_AD_TYPES)
    except gads.GadsError as e:
        return {"units": [], "error": str(e)[:200]}
    try:
        pmax = search(account_id, GAQL_PMAX_MEDIA)
    except gads.GadsError:
        pmax = []
    return {"units": google_units(ads, pmax), "error": ""}


def read_meta(act, pages):
    try:
        path, params = meta_request(act)
        return {"units": meta_units(pages(path, params)), "error": ""}
    except Exception as e:                                                           # noqa: BLE001 (a refused read is said, never read as "we run nothing")
        return {"units": [], "error": str(e)[:200]}


def load(platform, account_id, *, search_fn=None, meta_pages_fn=None, store=None, now=None, force=False, today=None):
    """{units, error} for one of our accounts, kept for the day. A read that failed carries a warning, so it is not kept and the next report reads again."""
    import datetime as dt
    from . import paid_geo, paid_store
    store = store or paid_store
    day = today or dt.date.today()
    if platform == "meta":
        from . import paid_meta
        pages = meta_pages_fn or (lambda path, params: paid_meta._pages(path, params, store))

        def build():
            got = read_meta(account_id, pages)
            return dict(got, warnings=[got["error"]] if got["error"] else [])
    else:
        from . import paid_gads_segments as gads

        def build():
            got = read_google(account_id, search_fn or gads.search)
            return dict(got, warnings=[got["error"]] if got["error"] else [])
    got, _stale = paid_geo._cached("compete-own", account_id, "kinds", day, day, build, force=force, store=store, now=now)
    return {"units": got.get("units") or [], "error": got.get("error") or ""}
