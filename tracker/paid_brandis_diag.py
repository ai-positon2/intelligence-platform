"""Paid Marketing Agents: Brand IS Monitor, "check the campaign settings" (read only).

When a brand share is low the plan says what usually causes it. This reads what the brand campaigns of one account actually have, so the plan can say what is
wrong with *these* campaigns: what Google says about their serving status, the bidding strategy with its target and bid limit, the budget and whether it is shared,
the Quality Score of the brand keywords and the state of their ads. Four small reads for the brand campaigns only (the field names are the Account Audit's,
already checked live; the bid settings are a separate read so that a refused field costs only that part).
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from . import paid_gads_segments as gads

KEYWORDS_SHOWN = 6
WEAK_QS = 6                      # a Quality Score at or under this is weak
BELOW = "BELOW_AVERAGE"
NO_REASON = ("UNKNOWN", "UNSPECIFIED")          # what Google sends when it has a reason this API version cannot name: said as no reason, not as "unknown"


def _ids(campaign_ids):
    out = [str(int(i)) for i in campaign_ids if str(i).strip().isdigit()]
    if not out:
        raise ValueError("No campaign to read.")
    return ", ".join(sorted(set(out), key=int))


def gaql_campaigns(ids):
    return ("SELECT campaign.id, campaign.name, campaign.primary_status, campaign.primary_status_reasons, campaign.serving_status, campaign.bidding_strategy_type, "
            "campaign.bidding_strategy_system_status, campaign.bidding_strategy, campaign.target_cpa.target_cpa_micros, campaign.maximize_conversions.target_cpa_micros, "
            "campaign.target_roas.target_roas, campaign.maximize_conversion_value.target_roas, campaign_budget.amount_micros, campaign_budget.explicitly_shared "
            "FROM campaign WHERE campaign.id IN (%s)" % _ids(ids))


def gaql_settings(ids):
    return ("SELECT campaign.id, campaign.target_impression_share.location, campaign.target_impression_share.location_fraction_micros, "
            "campaign.target_impression_share.cpc_bid_ceiling_micros, campaign.target_spend.cpc_bid_ceiling_micros, campaign.manual_cpc.enhanced_cpc_enabled "
            "FROM campaign WHERE campaign.id IN (%s)" % _ids(ids))


def gaql_keywords(ids):
    return ("SELECT campaign.id, ad_group.id, ad_group_criterion.criterion_id, ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, "
            "ad_group_criterion.status, ad_group_criterion.approval_status, ad_group_criterion.system_serving_status, "
            "ad_group_criterion.quality_info.quality_score, ad_group_criterion.quality_info.creative_quality_score, "
            "ad_group_criterion.quality_info.post_click_quality_score, ad_group_criterion.quality_info.search_predicted_ctr FROM ad_group_criterion "
            "WHERE ad_group_criterion.type = KEYWORD AND ad_group_criterion.negative = FALSE AND ad_group_criterion.status = ENABLED "
            "AND campaign.id IN (%s) AND ad_group.status = ENABLED" % _ids(ids))


def gaql_keyword_numbers(ids):
    return ("SELECT campaign.id, ad_group.id, ad_group_criterion.criterion_id, metrics.impressions FROM keyword_view "
            "WHERE segments.date DURING LAST_30_DAYS AND campaign.id IN (%s) AND ad_group_criterion.status = ENABLED AND metrics.impressions > 0" % _ids(ids))


def gaql_ads(ids):
    return ("SELECT campaign.id, ad_group.id, ad_group_ad.ad.id, ad_group_ad.ad.type, ad_group_ad.status, ad_group_ad.ad_strength, "
            "ad_group_ad.policy_summary.approval_status, ad_group_ad.policy_summary.policy_topic_entries FROM ad_group_ad "
            "WHERE campaign.id IN (%s) AND ad_group.status = ENABLED AND ad_group_ad.status = ENABLED" % _ids(ids))


# ── Reading the answers ─────────────────────────────────────────────────────

def _get(row, *path, default=None):
    for p in path:
        if not isinstance(row, dict):
            return default
        row = row.get(p)
    return default if row is None else row


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _units(micros):
    v = _num(micros)
    return None if v is None else round(v / 1e6, 2)


def parse_campaigns(rows):
    """{campaign id: {name, primaryStatus, reasons, serving, bidding, biddingStatus, portfolio, targetKind, target, budget, budgetShared}}."""
    out = {}
    for r in rows:
        c, b = r.get("campaign") or {}, r.get("campaignBudget") or {}
        cid = str(c.get("id") or "")
        if not cid:
            continue
        cpa = _get(c, "targetCpa", "targetCpaMicros", default=_get(c, "maximizeConversions", "targetCpaMicros"))
        roas = _get(c, "targetRoas", "targetRoas", default=_get(c, "maximizeConversionValue", "targetRoas"))
        kind, target = ("cpa", _units(cpa)) if _num(cpa) else ("roas", round(_num(roas), 2)) if _num(roas) else ("", None)
        out[cid] = {"name": c.get("name") or "", "primaryStatus": c.get("primaryStatus") or "", "reasons": [r for r in (c.get("primaryStatusReasons") or []) if r not in NO_REASON],
                    "serving": c.get("servingStatus") or "", "bidding": c.get("biddingStrategyType") or "", "biddingStatus": c.get("biddingStrategySystemStatus") or "",
                    "portfolio": bool(c.get("biddingStrategy")), "targetKind": kind, "target": target,
                    "budget": _units(b.get("amountMicros")), "budgetShared": bool(b.get("explicitlyShared"))}
    return out


def parse_settings(rows):
    """{campaign id: {tisLocation, tisTarget (a share 0 to 1), tisCeiling, spendCeiling, enhancedCpc}}, each None where Google gave nothing."""
    out = {}
    for r in rows:
        c = r.get("campaign") or {}
        cid = str(c.get("id") or "")
        if not cid:
            continue
        tis = c.get("targetImpressionShare") or {}
        frac = _num(tis.get("locationFractionMicros"))
        out[cid] = {"tisLocation": tis.get("location") or None, "tisTarget": None if frac is None else round(frac / 1e6, 4),
                    "tisCeiling": _units(tis.get("cpcBidCeilingMicros")), "spendCeiling": _units(_get(c, "targetSpend", "cpcBidCeilingMicros")),
                    "enhancedCpc": _get(c, "manualCpc", "enhancedCpcEnabled")}
    return out


def parse_keywords(rows, number_rows):
    """{campaign id: {enabled, rarelyServed: [text], disapproved: [text], scored, weak: [{text, match, qs, expectedCtr, adRelevance, landingPage, impressions}]}}.
    A keyword is weak when its Quality Score is 6 or under, or one of its three parts is Below average; the busiest come first."""
    impressions = {}
    for r in number_rows or []:
        k = (str(_get(r, "adGroup", "id", default="")), str(_get(r, "adGroupCriterion", "criterionId", default="")))
        impressions[k] = impressions.get(k, 0) + int(_num(_get(r, "metrics", "impressions")) or 0)
    out = {}
    for r in rows:
        c, g, k = r.get("campaign") or {}, r.get("adGroup") or {}, r.get("adGroupCriterion") or {}
        cid = str(c.get("id") or "")
        if not cid:
            continue
        slot = out.setdefault(cid, {"enabled": 0, "scored": 0, "rarelyServed": [], "disapproved": [], "weak": []})
        kw = k.get("keyword") or {}
        text = kw.get("text") or ""
        slot["enabled"] += 1
        if k.get("systemServingStatus") == "RARELY_SERVED":
            slot["rarelyServed"].append(text)
        if k.get("approvalStatus") == "DISAPPROVED":
            slot["disapproved"].append(text)
        qi = k.get("qualityInfo") or {}
        score = int(_num(qi.get("qualityScore")) or 0)
        parts = {"expectedCtr": qi.get("searchPredictedCtr"), "adRelevance": qi.get("creativeQualityScore"), "landingPage": qi.get("postClickQualityScore")}
        if score:
            slot["scored"] += 1
        if (score and score <= WEAK_QS) or BELOW in parts.values():
            slot["weak"].append(dict(text=text, match=kw.get("matchType") or "", qs=score or None, **parts,
                                     impressions=impressions.get((str(g.get("id") or ""), str(k.get("criterionId") or "")), 0)))
    for slot in out.values():
        slot["weak"].sort(key=lambda w: (-w["impressions"], w["text"]))
        slot["weak"] = slot["weak"][:KEYWORDS_SHOWN]
        slot["rarelyServed"] = slot["rarelyServed"][:KEYWORDS_SHOWN]
        slot["disapproved"] = slot["disapproved"][:KEYWORDS_SHOWN]
    return out


def parse_ads(rows):
    """{campaign id: {ads, rsa, strength: {rating: n}, disapproved, limited, topics}}."""
    out = {}
    for r in rows:
        a, c = r.get("adGroupAd") or {}, r.get("campaign") or {}
        cid = str(c.get("id") or "")
        if not cid:
            continue
        slot = out.setdefault(cid, {"ads": 0, "rsa": 0, "strength": {}, "disapproved": 0, "limited": 0, "topics": []})
        slot["ads"] += 1
        if _get(a, "ad", "type") == "RESPONSIVE_SEARCH_AD":
            slot["rsa"] += 1
            s = a.get("adStrength") or "UNSPECIFIED"
            slot["strength"][s] = slot["strength"].get(s, 0) + 1
        approval = _get(a, "policySummary", "approvalStatus", default="")
        if approval == "DISAPPROVED":
            slot["disapproved"] += 1
            for e in _get(a, "policySummary", "policyTopicEntries", default=[]) or []:
                t = str(e.get("topic") or "")
                if t and t not in slot["topics"] and len(slot["topics"]) < 3:
                    slot["topics"].append(t)
        elif approval in ("APPROVED_LIMITED", "AREA_OF_INTEREST_ONLY"):
            slot["limited"] += 1
    return out


# ── The read ────────────────────────────────────────────────────────────────

def read(account_id, campaign_ids, *, search_fn=None):
    """{campaigns: {id: facts}, warnings}: what the brand campaigns of one account have. The campaign settings are the point (a failure there is raised); the bid
    settings, the keywords and the ads are each read on their own, and a failure is a warning that leaves that part out."""
    search = search_fn or gads.search
    reads = [("campaigns", gaql_campaigns(campaign_ids)), ("settings", gaql_settings(campaign_ids)), ("keywords", gaql_keywords(campaign_ids)),
             ("numbers", gaql_keyword_numbers(campaign_ids)), ("ads", gaql_ads(campaign_ids))]

    def run(item):
        key, q = item
        try:
            return key, search(account_id, q), None
        except gads.GadsError as e:
            return key, None, e
    with ThreadPoolExecutor(max_workers=len(reads)) as ex:
        done = list(ex.map(run, reads))
    rows = {k: r for k, r, e in done if e is None}
    errors = {k: e for k, r, e in done if e is not None}
    if "campaigns" in errors:
        raise errors["campaigns"]
    parts = {"settings": "the bid settings", "keywords": "the brand keywords", "ads": "the brand ads", "numbers": "the keyword impressions"}
    warnings = ["%s could not be read (%s)." % (parts[k][0].upper() + parts[k][1:], e) for k, e in errors.items()]
    camps = parse_campaigns(rows["campaigns"])
    settings = parse_settings(rows["settings"]) if "settings" in rows else {}
    kws = parse_keywords(rows["keywords"], rows.get("numbers")) if "keywords" in rows else {}
    ads = parse_ads(rows["ads"]) if "ads" in rows else {}
    out = {}
    for cid, c in camps.items():
        out[cid] = dict(c, settings=settings.get(cid) if "settings" in rows else None,
                        keywords=kws.get(cid, {"enabled": 0, "scored": 0, "rarelyServed": [], "disapproved": [], "weak": []}) if "keywords" in rows else None,
                        adFacts=ads.get(cid, {"ads": 0, "rsa": 0, "strength": {}, "disapproved": 0, "limited": 0, "topics": []}) if "ads" in rows else None)
    return {"campaigns": out, "warnings": warnings}
