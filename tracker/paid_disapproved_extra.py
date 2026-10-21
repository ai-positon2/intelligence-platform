"""Paid Marketing Agents: Disapproved Ads Tracker, the three reads beyond the ads of Search, Display and the like (no network of its own: the reads are passed in).

  Performance Max   the assets of an asset group that Google has disapproved or limited (headlines, descriptions, images...), with the policy topics. An asset group is stopped when a
                    field has fewer approved assets than Google needs to serve it (three headlines, two descriptions, a landscape and a square image, a long headline, a business name),
                    so the cover of a field is the number of approved assets in it and its `need` the minimum.
  keywords          the disapproved keywords of enabled ad groups. A disapproved keyword cannot trigger an ad.
  Meta              the ads Meta has rejected (DISAPPROVED) or flagged (WITH_ISSUES) and the policies it names; an ad set is stopped when it has no active ad left.

Each becomes the same item the ads are (tracker/paid_disapproved_math.py): {id, campaignId, campaign, adGroupId, adGroup, type, approval, review, topics, headlines, url, kind, platform, need},
so the analysis, the plan and the page treat them alike. The field names of the Google queries are the API's; the Meta field names are Meta's documented ones, and none of the three has been
run against the live service from here, which is why each read is soft where it is used (tracker/paid_disapproved.py).
"""
from __future__ import annotations

import json

PMAX = "pmax"
APPROVAL = "'DISAPPROVED', 'APPROVED_LIMITED', 'AREA_OF_INTEREST_ONLY'"
GOOD = "'APPROVED', 'APPROVED_LIMITED', 'AREA_OF_INTEREST_ONLY'"

# What Google needs approved in a field before the asset group can serve, and how to say the field.
FIELD_NEED = {"HEADLINE": 3, "LONG_HEADLINE": 1, "DESCRIPTION": 2, "MARKETING_IMAGE": 1, "SQUARE_MARKETING_IMAGE": 1, "BUSINESS_NAME": 1}
FIELD_LABEL = {"HEADLINE": "headlines", "LONG_HEADLINE": "long headlines", "DESCRIPTION": "descriptions", "MARKETING_IMAGE": "landscape images", "SQUARE_MARKETING_IMAGE": "square images",
               "PORTRAIT_MARKETING_IMAGE": "portrait images", "LOGO": "logos", "LANDSCAPE_LOGO": "landscape logos", "BUSINESS_NAME": "business name", "YOUTUBE_VIDEO": "videos",
               "CALL_TO_ACTION_SELECTION": "calls to action", "MARKETING_IMAGE_LEGACY": "images"}

GAQL_ASSETS = ("SELECT campaign.id, campaign.name, asset_group.id, asset_group.name, asset_group_asset.field_type, asset_group_asset.policy_summary.approval_status, "
               "asset_group_asset.policy_summary.review_status, asset_group_asset.policy_summary.policy_topic_entries, asset.id, asset.type, asset.name, asset.text_asset.text "
               "FROM asset_group_asset WHERE campaign.status = ENABLED AND asset_group.status = ENABLED AND asset_group_asset.status = ENABLED "
               "AND asset_group_asset.policy_summary.approval_status IN (%s)" % APPROVAL)
GAQL_KEYWORDS = ("SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, ad_group_criterion.criterion_id, ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, "
                 "ad_group_criterion.approval_status, ad_group_criterion.policy_summary.policy_topic_entries FROM ad_group_criterion "
                 "WHERE ad_group_criterion.type = KEYWORD AND ad_group_criterion.negative = FALSE AND ad_group_criterion.status = ENABLED AND ad_group.status = ENABLED "
                 "AND campaign.status = ENABLED AND ad_group_criterion.approval_status = DISAPPROVED")
GAQL_KEYWORDS_PLAIN = GAQL_KEYWORDS.replace(", ad_group_criterion.policy_summary.policy_topic_entries", "")


def _get(row, *path, default=None):
    for p in path:
        if not isinstance(row, dict):
            return default
        row = row.get(p)
    return default if row is None else row


def _topics(entries):
    out = []
    for e in entries or []:
        t = str(e.get("topic") or "")
        if not t or any(x["t"] == t for x in out):
            continue
        ev = []
        for x in e.get("evidences") or []:
            for text in _get(x, "textList", "texts", default=[]) or []:
                if text and text not in ev and len(ev) < 3:
                    ev.append(str(text))
        out.append({"t": t, "ty": str(e.get("type") or "").upper(), "ev": ev})
    return out


def _ids(ids):
    return ", ".join(str(i) for i in ids)


# ── Performance Max assets ──────────────────────────────────────────────────

def pmax_key(group_id, field):
    return "%s:%s:%s" % (PMAX, group_id, field)


def read_assets(api_rows):
    """The flagged assets of Performance Max asset groups as items. The 'ad group' of an asset is its asset group and field together, so a field with too few approved assets is a stopped unit."""
    out = []
    for r in api_rows:
        gid, aid = str(_get(r, "assetGroup", "id", default="") or ""), str(_get(r, "asset", "id", default="") or "")
        field = str(_get(r, "assetGroupAsset", "fieldType", default="") or "")
        if not gid or not aid or not field:
            continue
        pol = _get(r, "assetGroupAsset", "policySummary", default={}) or {}
        text = _get(r, "asset", "textAsset", "text", default="") or _get(r, "asset", "name", default="") or ("%s asset %s" % (str(_get(r, "asset", "type", default="") or "").lower().replace("_", " "), aid))
        out.append({"id": "asset:%s:%s:%s" % (gid, aid, field), "campaignId": str(_get(r, "campaign", "id", default="") or ""), "campaign": _get(r, "campaign", "name", default="") or "",
                    "adGroupId": pmax_key(gid, field), "adGroup": "%s (%s)" % (_get(r, "assetGroup", "name", default="") or "Asset group %s" % gid, FIELD_LABEL.get(field, field.lower().replace("_", " "))),
                    "type": "ASSET_" + field, "approval": str(pol.get("approvalStatus") or ""), "review": str(pol.get("reviewStatus") or ""), "topics": _topics(pol.get("policyTopicEntries")),
                    "headlines": [str(text)], "url": "", "kind": "asset", "platform": "google", "need": FIELD_NEED.get(field, 0)})
    return out


def gaql_asset_cover(group_ids):
    return ("SELECT asset_group.id, asset_group_asset.field_type, asset.id FROM asset_group_asset WHERE campaign.status = ENABLED AND asset_group.status = ENABLED "
            "AND asset_group_asset.status = ENABLED AND asset_group_asset.policy_summary.approval_status IN (%s) AND asset_group.id IN (%s)" % (GOOD, _ids(group_ids)))


def read_asset_cover(api_rows):
    """{"pmax:<group>:<FIELD>": approved assets} for the fields that have any."""
    out = {}
    for r in api_rows:
        gid, field = str(_get(r, "assetGroup", "id", default="") or ""), str(_get(r, "assetGroupAsset", "fieldType", default="") or "")
        if gid and field:
            out[pmax_key(gid, field)] = out.get(pmax_key(gid, field), 0) + 1
    return out


def gaql_asset_weekly(group_ids, first, last):
    return "SELECT asset_group.id, segments.week, metrics.clicks, metrics.cost_micros FROM asset_group WHERE segments.date BETWEEN '%s' AND '%s' AND asset_group.id IN (%s)" % (first, last, _ids(group_ids))


def read_asset_weekly(api_rows, weeks, keys_by_group):
    """{key: [{clicks, cost}] one per week} for every field key of the asset groups read (an asset group's clicks are those of all its fields)."""
    from . import paid_disapproved as d
    by_group = d.read_weekly([{"adGroup": r.get("assetGroup") or {}, "segments": r.get("segments") or {}, "metrics": r.get("metrics") or {}} for r in api_rows], weeks)
    out = {}
    for gid, series in by_group.items():
        for key in keys_by_group.get(gid, []):
            out[key] = series
    return out


# ── Keywords ────────────────────────────────────────────────────────────────

def read_keywords(api_rows):
    """The disapproved keywords as items: the keyword and its match type is the text, there is no ad and no final URL."""
    out = []
    for r in api_rows:
        cid, gid = str(_get(r, "adGroupCriterion", "criterionId", default="") or ""), str(_get(r, "adGroup", "id", default="") or "")
        text = _get(r, "adGroupCriterion", "keyword", "text", default="")
        if not cid or not gid or not text:
            continue
        mt = str(_get(r, "adGroupCriterion", "keyword", "matchType", default="") or "")
        shown = "[%s]" % text if mt == "EXACT" else '"%s"' % text if mt == "PHRASE" else str(text)
        out.append({"id": "kw:%s:%s" % (gid, cid), "campaignId": str(_get(r, "campaign", "id", default="") or ""), "campaign": _get(r, "campaign", "name", default="") or "",
                    "adGroupId": gid, "adGroup": _get(r, "adGroup", "name", default="") or "", "type": "KEYWORD", "approval": "DISAPPROVED", "review": "",
                    "topics": _topics(_get(r, "adGroupCriterion", "policySummary", "policyTopicEntries", default=[])), "headlines": [shown], "url": "", "kind": "keyword", "platform": "google", "need": 0})
    return out


# ── Meta ────────────────────────────────────────────────────────────────────

META_ADS_FIELDS = "id,name,effective_status,ad_review_feedback,issues_info,adset{id,name},campaign{id,name}"
META_STATUS = {"DISAPPROVED": "DISAPPROVED", "WITH_ISSUES": "APPROVED_LIMITED"}


def meta_ads_request(act):
    """(path, params) of the read of the ads Meta rejected or flagged."""
    return "/%s/ads" % act, {"fields": META_ADS_FIELDS, "effective_status": json.dumps(sorted(META_STATUS)), "limit": 200}


def meta_active_request(act):
    return "/%s/ads" % act, {"fields": "id,adset_id", "effective_status": json.dumps(["ACTIVE"]), "limit": 500}


def read_meta_ads(rows):
    """The rejected and flagged ads as items. The policies Meta names (the keys of ad_review_feedback.global, with its explanation as the text it points at) and the issues it lists."""
    out = []
    for r in rows:
        status = META_STATUS.get(str(r.get("effective_status") or ""))
        adset = r.get("adset") or {}
        if not status or not r.get("id") or not adset.get("id"):
            continue
        topics = []
        for key, why in sorted(((_get(r, "ad_review_feedback", "global", default={}) or {}).items())):
            topics.append({"t": str(key), "ty": "", "ev": [str(why)] if isinstance(why, str) and why else []})
        for issue in r.get("issues_info") or []:
            code = issue.get("error_code")
            t = ("ISSUE_%s" % code) if code else str(issue.get("error_summary") or "")
            if t and not any(x["t"] == t for x in topics):
                msg = str(issue.get("error_message") or issue.get("error_summary") or "")
                topics.append({"t": t, "ty": "", "ev": [msg] if msg else []})
        out.append({"id": "meta:%s" % r["id"], "campaignId": str((r.get("campaign") or {}).get("id") or ""), "campaign": (r.get("campaign") or {}).get("name") or "",
                    "adGroupId": "meta:%s" % adset["id"], "adGroup": adset.get("name") or "Ad set %s" % adset["id"], "type": "META_AD", "approval": status, "review": "",
                    "topics": topics, "headlines": [str(r.get("name") or "")] if r.get("name") else [], "url": "", "kind": "meta", "platform": "meta", "need": 1})
    return out


def read_meta_cover(rows):
    """{"meta:<ad set id>": active ads} from the read of active ads."""
    out = {}
    for r in rows:
        if r.get("adset_id"):
            k = "meta:%s" % r["adset_id"]
            out[k] = out.get(k, 0) + 1
    return out
