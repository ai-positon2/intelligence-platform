"""Paid Marketing Agents: Unknown Domains Alert, reading the ad accounts.

Pinned here: a Google ad's final and mobile URLs, each once; tracking templates on the ad, ad group, campaign and account (only one that begins with
another host is a sighting, an ad group's once however many ads it has, the account's once); Performance Max asset groups; sitelinks and other assets
at campaign and account level; a failed read raised, not swallowed; and Meta creatives, including a smaller page when Meta refuses the big one.
Nothing here talks to Google or Meta.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_domains_read as rd  # noqa: E402

G = {"id": "1112223330", "name": "Acme Garden", "platform": "google", "group": "mcc-1"}
M = {"id": "act_77", "name": "Acme Garden", "platform": "meta", "group": ""}


def fake_search(rows):
    """A `search(customer_id, gaql)` that answers by the table the query reads."""
    def search(cid, gaql):
        for key in ("FROM ad_group_ad", "FROM asset_group", "FROM campaign_asset", "FROM customer_asset", "FROM campaign "):
            if key in gaql:
                return rows.get(key.split()[-1].strip(), [])
        raise AssertionError(gaql)
    return search


def test_google_ads_urls_and_templates():
    ad = lambda i, urls, mob=(), tpl=None, gtpl=None: {  # noqa: E731
        "campaign": {"name": "C"}, "adGroup": {"id": "7", "name": "G", "trackingUrlTemplate": gtpl},
        "adGroupAd": {"ad": {"id": i, "type": "RESPONSIVE_SEARCH_AD", "finalUrls": list(urls), "finalMobileUrls": list(mob), "trackingUrlTemplate": tpl}}}
    rows = {"ad_group_ad": [ad("1", ["https://acme-garden.com/a"], ["https://acme-garden.com/a", "https://m.acme-garden.com/"],
                               tpl="{lpurl}?gclid={gclid}", gtpl="https://go.tracker.example/r?u={lpurl}"),
                            ad("2", ["https://acme-garden.com/b"], gtpl="https://go.tracker.example/r?u={lpurl}")],
            "asset_group": [{"campaign": {"name": "PM"}, "assetGroup": {"id": "9", "name": "AG", "finalUrls": ["https://pm.example/"]}}],
            "campaign_asset": [{"campaign": {"id": "5", "name": "C"}, "campaignAsset": {"fieldType": "SITELINK"},
                                "asset": {"id": "11", "type": "SITELINK", "finalUrls": ["https://acme-garden.com/s"], "sitelinkAsset": {"linkText": "Services"}}}],
            "customer_asset": [{"customerAsset": {"fieldType": "SITELINK"}, "asset": {"id": "12", "type": "SITELINK", "finalUrls": ["https://other.example/"]}}],
            "campaign": [{"campaign": {"id": "5", "name": "C", "trackingUrlTemplate": "https://camp.tracker.example/x"}, "customer": {"trackingUrlTemplate": "https://acct.tracker.example/y"}},
                         {"campaign": {"id": "6", "name": "D"}, "customer": {"trackingUrlTemplate": "https://acct.tracker.example/y"}}]}
    s, n = rd.google_sightings(G, fake_search(rows))
    assert n == {"ads": 2, "assetGroups": 1, "assets": 2}
    urls = [(x["where"], x["host"]) for x in s]
    assert urls.count(("ad", "acme-garden.com")) == 2                           # ad 1's two final URLs are one address twice over: urls kept once each
    assert ("ad", "m.acme-garden.com") in urls and ("asset group", "pm.example") in urls
    assert [x["where"] for x in s if x["redirect"]].count("tracking template (ad group)") == 1       # once, not once per ad
    assert not [x for x in s if x["where"] == "tracking template (ad)"]          # "{lpurl}?gclid=" starts with the ad's own address
    assert ("tracking template (campaign)", "camp.tracker.example") in urls
    assert [x["where"] for x in s].count("tracking template (account)") == 1
    sitelink = next(x for x in s if x["objectName"] == "Services")
    assert sitelink["where"] == "sitelink (campaign level)" and sitelink["campaign"] == "C"
    assert any(x["where"] == "sitelink (account level)" and x["host"] == "other.example" for x in s)
    assert all(x["platform"] == "google" and x["accountId"] == G["id"] for x in s)


def test_a_failed_read_is_raised():
    def search(cid, gaql):
        if "FROM asset_group" in gaql:
            raise RuntimeError("quota")
        return []
    try:
        rd.google_sightings(G, search)
    except RuntimeError as e:
        assert "quota" in str(e)
    else:
        raise AssertionError("a half-read account must not look clean")


def test_meta_ads_creative_urls():
    rows = [{"id": "a1", "name": "Spring", "campaign": {"name": "C"}, "adset": {"name": "S"},
             "creative": {"object_story_spec": {"link_data": {"link": "https://acme-garden.com/spring"}}}},
            {"id": "a2", "name": "Form", "creative": {"object_story_spec": {"link_data": {"link": "https://fb.me/x"}}}}]
    calls = []

    def pages(path, params):
        calls.append((path, params["limit"]))
        return rows
    s, n = rd.meta_sightings(M, pages)
    assert n == {"ads": 2} and calls[0][0] == "act_77/ads"
    assert [(x["host"], x["campaign"], x["adGroup"]) for x in s][0] == ("acme-garden.com", "C", "S")
    assert all(x["platform"] == "meta" for x in s)


def test_meta_asks_again_with_a_smaller_page():
    calls = []

    def pages(path, params):
        calls.append(params["limit"])
        if len(calls) == 1:
            raise RuntimeError("Please reduce the amount of data")
        return []
    rd.meta_sightings(M, pages)
    assert len(calls) == 2 and calls[1] < calls[0]
