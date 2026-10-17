"""Rows shaped like Google Ads' real answers (taken from live reads of a Position2 account), for the Account Audit tests."""


def customer(**kw):
    c = {"resourceName": "customers/1112223334", "callReportingSetting": {"callReportingEnabled": True, "callConversionReportingEnabled": True},
         "conversionTrackingSetting": {"acceptedCustomerDataTerms": True, "conversionTrackingStatus": "CONVERSION_TRACKING_MANAGED_BY_SELF",
                                       "enhancedConversionsForLeadsEnabled": True},
         "remarketingSetting": {"googleGlobalSiteTag": "<script async src=\"https://www.googletagmanager.com/gtag/js?id=AW-16599110526\"></script>\n"
                                                       "gtag('config', 'AW-16599110526');"},
         "id": "1112223334", "descriptiveName": "Acme Dental", "currencyCode": "USD", "timeZone": "America/New_York",
         "trackingUrlTemplate": "{lpurl}?gclid={gclid}&utm_source=google", "autoTaggingEnabled": True, "manager": False, "testAccount": False,
         "optimizationScore": 0.4328472, "status": "ENABLED"}
    c.update(kw)
    return [{"customer": c}]


def action(aid="1", name="Submit lead form", status="ENABLED", type="WEBPAGE", category="SUBMIT_LEAD_FORM", primary=True, counting="ONE_PER_CLICK",
           attribution="GOOGLE_SEARCH_ATTRIBUTION_DATA_DRIVEN", default=1.0, always=False, click="90", view="1", origin="WEBSITE", included=True,
           owner="customers/1112223334"):
    return {"conversionAction": {"resourceName": "customers/1112223334/conversionActions/" + aid, "status": status, "type": type, "category": category,
                                 "valueSettings": {"defaultValue": default, "alwaysUseDefaultValue": always}, "countingType": counting,
                                 "attributionModelSettings": {"attributionModel": attribution}, "id": aid, "name": name, "ownerCustomer": owner,
                                 "includeInConversionsMetric": included, "clickThroughLookbackWindowDays": click, "viewThroughLookbackWindowDays": view,
                                 "origin": origin, "primaryForGoal": primary}}


def volume(aid="1", conversions=10.0, all_conversions=None, value=0.0):
    return {"customer": {"resourceName": "customers/1112223334"},
            "metrics": {"conversionsValue": value, "conversions": conversions, "allConversions": conversions if all_conversions is None else all_conversions,
                        "allConversionsValue": value},
            "segments": {"conversionAction": "customers/1112223334/conversionActions/" + aid}}


def link(kind="MERCHANT_CENTER", rid="1"):
    return {"productLink": {"resourceName": "customers/1112223334/productLinks/" + rid, "type": kind}}


def merchant(mid="9282495"):
    return {"productLink": {"resourceName": "customers/1112223334/productLinks/9", "type": "MERCHANT_CENTER", "merchantCenter": {"merchantCenterId": mid}}}


def user_list(name="All Converters", kind="RULE_BASED", status="OPEN", search=0, display=0, lid="1"):
    return {"userList": {"resourceName": "customers/1112223334/userLists/" + lid, "membershipStatus": status, "type": kind, "id": lid, "readOnly": True,
                         "name": name, "sizeForDisplay": str(display), "sizeForSearch": str(search), "eligibleForSearch": True, "eligibleForDisplay": True}}


def campaign(cid="10", name="Search | Implants", status="ENABLED", ctype="SEARCH", bidding="MAXIMIZE_CONVERSIONS", primary="ELIGIBLE", reasons=(),
             google=True, partners=False, display=False, geo="PRESENCE_OR_INTEREST", budget="50000000", shared=False, merchant="", target_cpa=None,
             target_roas=None, portfolio="", sub=""):
    c = {"resourceName": "customers/1112223334/campaigns/" + cid, "status": status, "advertisingChannelType": ctype, "primaryStatus": primary,
         "primaryStatusReasons": list(reasons), "servingStatus": "SERVING", "biddingStrategyType": bidding, "biddingStrategySystemStatus": "ENABLED",
         "networkSettings": {"targetGoogleSearch": google, "targetSearchNetwork": partners, "targetContentNetwork": display},
         "geoTargetTypeSetting": {"positiveGeoTargetType": geo, "negativeGeoTargetType": "PRESENCE"}, "name": name, "id": cid,
         "startDateTime": "2024-08-12 17:30:40"}
    if sub:
        c["advertisingChannelSubType"] = sub
    if portfolio:
        c["biddingStrategy"] = portfolio
    if merchant:
        c["shoppingSetting"] = {"merchantId": merchant}
    if target_cpa:
        c["targetCpa"] = {"targetCpaMicros": target_cpa}
    if target_roas:
        c["targetRoas"] = {"targetRoas": target_roas}
    return {"campaign": c, "campaignBudget": {"resourceName": "customers/1112223334/campaignBudgets/1", "amountMicros": budget, "explicitlyShared": shared}}


def campaign_metrics(cid="10", impressions=1000, clicks=100, cost=500.0, conversions=10.0, all_conversions=None, budget_lost=None, rank_lost=None, share=None):
    m = {"impressions": str(impressions), "clicks": str(clicks), "costMicros": str(int(cost * 1e6)), "conversions": conversions,
         "allConversions": conversions if all_conversions is None else all_conversions}
    if budget_lost is not None:
        m["searchBudgetLostImpressionShare"] = budget_lost
    if rank_lost is not None:
        m["searchRankLostImpressionShare"] = rank_lost
    if share is not None:
        m["searchImpressionShare"] = share
    return {"campaign": {"resourceName": "customers/1112223334/campaigns/" + cid, "id": cid}, "metrics": m}


def criterion(cid="10", kind="LOCATION", negative=False):
    return {"campaign": {"resourceName": "customers/1112223334/campaigns/" + cid, "id": cid},
            "campaignCriterion": {"resourceName": "customers/1112223334/campaignCriteria/%s~1" % cid, "type": kind, "negative": negative}}


def negative(cid="10", match="BROAD"):
    return {"campaign": {"resourceName": "customers/1112223334/campaigns/" + cid, "id": cid},
            "campaignCriterion": {"resourceName": "customers/1112223334/campaignCriteria/%s~2" % cid, "keyword": {"matchType": match, "text": "free"}, "negative": True}}


def attached_list(cid="10", sid="900", name="Master negatives", members="4000"):
    return {"campaign": {"resourceName": "customers/1112223334/campaigns/" + cid, "id": cid},
            "sharedSet": {"resourceName": "customers/1112223334/sharedSets/" + sid, "id": sid, "name": name, "memberCount": members},
            "campaignSharedSet": {"resourceName": "customers/1112223334/campaignSharedSets/%s~%s" % (cid, sid)}}


def network_row(cid="10", network="SEARCH", cost=100.0, conversions=5.0):
    return {"campaign": {"resourceName": "customers/1112223334/campaigns/" + cid, "id": cid},
            "metrics": {"impressions": "100", "clicks": "10", "costMicros": str(int(cost * 1e6)), "conversions": conversions}, "segments": {"adNetworkType": network}}
