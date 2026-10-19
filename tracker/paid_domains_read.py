"""Paid Marketing Agents: Unknown Domains Alert, reading where the ads send people.

Read only. For one ad account it returns every web address an enabled ad points at, as sightings the logic in tracker/paid_domains.py judges:

  Google Ads   the final URLs and mobile final URLs of every enabled ad, the tracking templates of the ad, its ad group, the campaign and the account
               (a template that begins with the ad's own address, "{lpurl}?gclid=...", has nothing to judge; one that begins with another host sends
               people through it first, which is how a hijack hides), the final URLs of every Performance Max asset group, and the final URLs of the
               sitelinks and other assets attached to a campaign or the account. Five reads per account.
  Meta Ads     the web addresses of the creative of every active ad (a link ad, a video's button, a carousel's cards, a dynamic creative's websites),
               read the way tracker/paid_lp_meta_read.py reads them.

A sighting: {platform, accountId, accountName, where, objectId, objectName, campaign, adGroup, url, host, redirect}. `where` says what holds the address
(an ad, an asset group, a sitelink, a tracking template) and `redirect` is True for a template's host.
"""
from __future__ import annotations

import json

from . import paid_domains as dom
from . import paid_lp_meta_read as lpm

ADS_GAQL = (
    "SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, ad_group.tracking_url_template, ad_group_ad.ad.id, ad_group_ad.ad.type, "
    "ad_group_ad.ad.final_urls, ad_group_ad.ad.final_mobile_urls, ad_group_ad.ad.tracking_url_template FROM ad_group_ad "
    "WHERE ad_group_ad.status = 'ENABLED' AND ad_group.status = 'ENABLED' AND campaign.status = 'ENABLED'")
ASSET_GROUPS_GAQL = (
    "SELECT campaign.id, campaign.name, asset_group.id, asset_group.name, asset_group.final_urls, asset_group.final_mobile_urls FROM asset_group "
    "WHERE asset_group.status = 'ENABLED' AND campaign.status = 'ENABLED'")
_ASSET_TYPES = "('SITELINK', 'PROMOTION', 'MOBILE_APP', 'LEAD_FORM', 'PRICE')"
CAMPAIGN_ASSETS_GAQL = (
    "SELECT campaign.id, campaign.name, campaign.status, campaign_asset.field_type, campaign_asset.status, asset.id, asset.type, asset.final_urls, "
    "asset.final_mobile_urls, asset.sitelink_asset.link_text FROM campaign_asset WHERE campaign_asset.status = 'ENABLED' AND campaign.status = 'ENABLED' "
    "AND asset.type IN " + _ASSET_TYPES)
CUSTOMER_ASSETS_GAQL = (
    "SELECT customer_asset.field_type, customer_asset.status, asset.id, asset.type, asset.final_urls, asset.final_mobile_urls, asset.sitelink_asset.link_text "
    "FROM customer_asset WHERE customer_asset.status = 'ENABLED' AND asset.type IN " + _ASSET_TYPES)
TEMPLATES_GAQL = (
    "SELECT campaign.id, campaign.name, campaign.tracking_url_template, customer.tracking_url_template FROM campaign WHERE campaign.status = 'ENABLED'")

AD_TYPE_NAMES = {"RESPONSIVE_SEARCH_AD": "Responsive Search Ad", "EXPANDED_TEXT_AD": "Expanded Text Ad", "DEMAND_GEN_MULTI_ASSET_AD": "Demand Gen ad",
                 "DEMAND_GEN_VIDEO_RESPONSIVE_AD": "Demand Gen video ad", "DEMAND_GEN_CAROUSEL_AD": "Demand Gen carousel ad", "RESPONSIVE_DISPLAY_AD": "Responsive Display Ad",
                 "SHOPPING_PRODUCT_AD": "Shopping ad", "CALL_AD": "Call ad", "APP_AD": "App ad"}
META_ADS_FIELDS = lpm.ADS_FIELDS + ",campaign{name},adset{name}"


def _g(row, *path):
    for p in path:
        if not isinstance(row, dict):
            return None
        row = row.get(p)
    return row


def _sighting(account, where, object_id, object_name, url, campaign="", ad_group="", redirect=False, host=None):
    host = host if host is not None else dom.host_of(url)
    return {"platform": account["platform"], "accountId": account["id"], "accountName": account["name"], "where": where, "objectId": str(object_id),
            "objectName": object_name, "campaign": campaign, "adGroup": ad_group, "url": url, "host": host, "redirect": redirect}


def _urls(*lists):
    out = []
    for l in lists:
        for u in l or []:
            if u and u not in out:
                out.append(u)
    return out


def _template(account, where, object_id, name, template, campaign="", ad_group=""):
    """A sighting for a tracking template that begins with another host, else nothing."""
    host = dom.host_of_template(template)
    return [_sighting(account, "tracking template (%s)" % where, object_id, name, template, campaign, ad_group, True, host)] if host else []


def google_sightings(account, search):
    """Every address the enabled ads, asset groups, assets and templates of a Google Ads account point at, and how many ads were read:
    (sightings, {ads, assetGroups, assets}). The ads are the point, so a failure reading them is raised; the rest is raised too, because a part
    read half is not a clean bill: the scan names the account as unreadable instead."""
    cid = account["id"]
    out, seen = [], {}
    ads = search(cid, ADS_GAQL)
    for r in ads:
        ad = _g(r, "adGroupAd", "ad") or {}
        camp, group = _g(r, "campaign", "name") or "", _g(r, "adGroup", "name") or ""
        name = "%s %s" % (AD_TYPE_NAMES.get(ad.get("type"), "Ad"), ad.get("id") or "")
        for u in _urls(ad.get("finalUrls"), ad.get("finalMobileUrls")):
            out.append(_sighting(account, "ad", ad.get("id"), name, u, camp, group))
        out += _template(account, "ad", ad.get("id"), name, ad.get("trackingUrlTemplate"), camp, group)
        gt = _g(r, "adGroup", "trackingUrlTemplate")
        if gt and ("ag", _g(r, "adGroup", "id")) not in seen:
            seen[("ag", _g(r, "adGroup", "id"))] = 1
            out += _template(account, "ad group", _g(r, "adGroup", "id"), group, gt, camp, group)
    groups = search(cid, ASSET_GROUPS_GAQL)
    for r in groups:
        g = r.get("assetGroup") or {}
        for u in _urls(g.get("finalUrls"), g.get("finalMobileUrls")):
            out.append(_sighting(account, "asset group", g.get("id"), g.get("name") or "", u, _g(r, "campaign", "name") or ""))
    assets = 0
    for rows, level, key in ((search(cid, CAMPAIGN_ASSETS_GAQL), "campaign", "campaignAsset"), (search(cid, CUSTOMER_ASSETS_GAQL), "account", "customerAsset")):
        for r in rows:
            a = r.get("asset") or {}
            assets += 1
            kind = str((r.get(key) or {}).get("fieldType") or a.get("type") or "asset").replace("_", " ").lower()
            label = _g(a, "sitelinkAsset", "linkText") or ("%s %s" % (kind, a.get("id")))
            for u in _urls(a.get("finalUrls"), a.get("finalMobileUrls")):
                out.append(_sighting(account, "%s (%s level)" % (kind, level), "%s:%s:%s" % (level, a.get("id"), _g(r, "campaign", "id") or ""),
                                     label, u, _g(r, "campaign", "name") or ""))
    customer_done = False
    for r in search(cid, TEMPLATES_GAQL):
        out += _template(account, "campaign", _g(r, "campaign", "id"), _g(r, "campaign", "name") or "", _g(r, "campaign", "trackingUrlTemplate"), _g(r, "campaign", "name") or "")
        if not customer_done:
            customer_done = True
            out += _template(account, "account", "customer", account["name"], _g(r, "customer", "trackingUrlTemplate"))
    return out, {"ads": len(ads), "assetGroups": len(groups), "assets": assets}


def meta_sightings(account, pages):
    """Every address the active ads of a Meta ad account point at: (sightings, {ads}). `pages(path, params)` is the Graph call that gives every row of
    a list (paid_meta._pages bound to a store). An ad whose creative names no web address (an instant form, a message, a post) has nothing to judge."""
    act = account["id"]
    statuses = json.dumps(["ACTIVE"])
    try:
        rows = pages(act + "/ads", {"fields": META_ADS_FIELDS, "effective_status": statuses, "limit": lpm.ADS_PAGE})
    except Exception:                                      # noqa: BLE001 (a creative is a big record: Meta says so with an error, and a smaller page answers)
        rows = pages(act + "/ads", {"fields": META_ADS_FIELDS, "effective_status": statuses, "limit": lpm.ADS_PAGE_SMALL})
    out = []
    for r in rows:
        urls, _on_meta = lpm.creative_urls(r.get("creative") or {})
        name = r.get("name") or "Ad " + str(r.get("id") or "")
        for u in urls:
            out.append(_sighting(account, "ad", r.get("id"), name, u, _g(r, "campaign", "name") or "", _g(r, "adset", "name") or ""))
    return out, {"ads": len(rows)}
