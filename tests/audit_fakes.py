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
