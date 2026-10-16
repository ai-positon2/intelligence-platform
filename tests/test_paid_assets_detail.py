"""Paid Marketing Agents: Asset Coverage Audit, the detail view: what each asset actually is.

Pinned here: what an asset's content is for every kind (a picture only from Google's own hosts, a sitelink's lines and dates, a
snippet's values, a promotion's terms, a price list, a video id); that reading an account into a Collector keeps each asset once and
each link with its state, why, dates, source and 30-day numbers; that the saved detail comes back from memory, from the store, or from
a fresh read of just that account, and from an old saved copy when Google's allowance is spent; that one campaign's sections follow
the audit's own kinds (own assets, else the account's; a group's logo and name from the campaign; Display and Demand Gen ad assets by
id), and that the library of one kind (or every problem) lists each asset once with who uses it. Nothing here talks to Google.
"""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests.test_paid_assets import ACCT, STAFF, TODAY, API, camp_row, client_as, link_row, many, metric_row, searcher  # noqa: E402
from tracker import paid_assets as a  # noqa: E402
from tracker import paid_assets_detail as D  # noqa: E402
from tracker import paid_assets_rules as rules  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_store  # noqa: E402

MEMBER = {"id": "1234567890", "name": "Acme Dental"}
GOOGLE_IMG = "https://tpc.googlesyndication.com/simgad/2833249052621513497"


@pytest.fixture(autouse=True)
def fresh_memory():
    D.mem_clear()
    yield
    D.mem_clear()


def image(aid, name="Logo.png", url=GOOGLE_IMG, w=896, h=896, size=24273):
    return {"type": "IMAGE", "id": aid, "name": name, "imageAsset": {"mimeType": "IMAGE_PNG", "fullSize": {"heightPixels": str(h), "widthPixels": str(w), "url": url}, "fileSize": str(size)}}


def text(aid, words):
    return {"type": "TEXT", "id": aid, "textAsset": {"text": words}}


def sink(member=MEMBER):
    return D.Collector(member, TODAY)


def collect(**kw):
    s = sink()
    read = a.read_account(MEMBER, search_fn=searcher(**kw), today=TODAY, sink=s)
    return s, read


def detail_of(**kw):
    return collect(**kw)[0].to_dict()


def sections(det, cid="5"):
    return {x["key"]: x for x in D.campaign_detail(det, cid)["sections"]}


# ── What an asset is ────────────────────────────────────────────────────────

def test_an_image_keeps_its_name_size_and_picture_only_from_googles_own_hosts():
    c = D.content(image("9", "Logo.png", GOOGLE_IMG, 800, 896, 24273))
    assert c == {"t": "IMAGE", "n": "Logo.png", "img": GOOGLE_IMG, "w": 800, "h": 896, "b": 24273}
    for url in ("http://tpc.googlesyndication.com/simgad/1", "https://evil.example.com/x.png", "https://googlesyndication.com.evil.com/x", "https://evilgooglesyndication.com/x",
                "javascript:alert(1)", "//tpc.googlesyndication.com/x", ""):
        assert "img" not in D.content(image("9", url=url)), url
    for url in ("https://lh3.googleusercontent.com/abc", "https://tpc.googlesyndication.com/simgad/1", "https://googlesyndication.com/x", "https://x.ggpht.com/y"):
        assert D.content(image("9", url=url))["img"] == url, url


def test_a_sitelink_keeps_its_lines_link_and_dates_and_never_a_script_link():
    s = {"type": "SITELINK", "finalUrls": ["https://x.com/a?b=1"], "sitelinkAsset": {"linkText": "Book", "description1": "Same day", "description2": "", "startDate": "2026-01-01", "endDate": "2026-12-31"}}
    assert D.content(s) == {"t": "SITELINK", "x": "Book", "d": ["Same day"], "u": "https://x.com/a?b=1", "s": "2026-01-01", "e": "2026-12-31"}
    for url in ("javascript:alert(1)", "data:text/html,hi", "ftp://x.com", "https://"):
        assert "u" not in D.content(dict(s, finalUrls=[url])), url
    assert "u" not in D.content({"type": "SITELINK", "sitelinkAsset": {"linkText": "x"}})


def test_callouts_snippets_calls_text_videos_and_calls_to_action_keep_what_they_say():
    assert D.content({"type": "CALLOUT", "calloutAsset": {"calloutText": "Open late"}}) == {"t": "CALLOUT", "x": "Open late"}
    assert D.content({"type": "STRUCTURED_SNIPPET", "structuredSnippetAsset": {"header": "Types", "values": ["Exam", "Cleaning"]}}) == {"t": "STRUCTURED_SNIPPET", "x": "Types", "v": ["Exam", "Cleaning"]}
    assert D.content({"type": "CALL", "callAsset": {"phoneNumber": "(781) 318-8040", "countryCode": "US"}}) == {"t": "CALL", "x": "(781) 318-8040", "cc": "US"}
    assert D.content(text("1", "Gentle Dental")) == {"t": "TEXT", "x": "Gentle Dental"}
    assert D.content({"type": "YOUTUBE_VIDEO", "youtubeVideoAsset": {"youtubeVideoId": "DceKeCNqo-g", "youtubeVideoTitle": "New Patient Deal"}}) == {"t": "YOUTUBE_VIDEO", "x": "New Patient Deal", "vid": "DceKeCNqo-g"}
    assert D.content({"type": "CALL_TO_ACTION", "callToActionAsset": {"callToAction": "LEARN_MORE"}}) == {"t": "CALL_TO_ACTION", "cta": "Learn more"}
    assert D.content({"type": "MEDIA_BUNDLE", "name": "bundle.zip"}) == {"t": "MEDIA_BUNDLE", "n": "bundle.zip"}
    assert D.content(None) == {"t": ""} and D.content({"type": "TEXT", "textAsset": {"text": "   "}}) == {"t": "TEXT"}


def test_a_promotion_says_its_terms_in_words():
    p = {"type": "PROMOTION", "finalUrls": ["https://x.com/offer"], "promotionAsset": {"promotionTarget": "Whitening", "percentOff": "10000000", "occasion": "BLACK_FRIDAY",
                                                                                        "promotionCode": "SAVE10", "startDate": "2026-11-01", "endDate": "2026-11-30"}}
    assert D.content(p) == {"t": "PROMOTION", "x": "Whitening", "off": "10% off", "code": "SAVE10", "occ": "Black friday", "s": "2026-11-01", "e": "2026-11-30", "u": "https://x.com/offer"}

    def off(**terms):
        return D.content({"type": "PROMOTION", "promotionAsset": terms}).get("off")
    assert off(moneyAmountOff={"amountMicros": "20000000", "currencyCode": "USD"}) == "USD 20 off"
    assert off(percentOff="5500000", ordersOverAmount={"amountMicros": "50000000", "currencyCode": "USD"}) == "5.5% off on orders over USD 50"
    assert off(percentOff="10000000", discountModifier="UP_TO") == "Up to 10% off"
    assert off(discountModifier="UP_TO") is None and off() is None


def test_a_price_asset_lists_its_offers_and_a_lead_form_its_questions_and_button():
    price = {"type": "PRICE", "priceAsset": {"type": "PRODUCT_CATEGORIES", "priceOfferings": [
        {"header": "HR", "description": "Free Trial", "price": {"currencyCode": "USD", "amountMicros": "0"}, "finalUrl": "https://w.com/hr"},
        {"header": "Sales", "price": {"currencyCode": "USD", "amountMicros": "1250000"}, "finalUrl": "javascript:x"}]}}
    assert D.content(price) == {"t": "PRICE", "x": "Product categories", "o": [{"h": "HR", "d": "Free Trial", "p": "USD 0", "u": "https://w.com/hr"}, {"h": "Sales", "d": "", "p": "USD 1.25", "u": ""}]}
    many_offers = {"type": "PRICE", "priceAsset": {"type": "SERVICES", "priceOfferings": [{"header": "H%d" % i} for i in range(20)]}}
    assert len(D.content(many_offers)["o"]) == D.MAX_OFFERS
    form = {"type": "LEAD_FORM", "leadFormAsset": {"businessName": "Workato", "headline": "Integration in 3 steps", "description": "Fill the form.", "callToActionType": "REQUEST_DEMO"}}
    assert D.content(form) == {"t": "LEAD_FORM", "x": "Integration in 3 steps", "d": ["Fill the form."], "bn": "Workato", "cta": "Request demo"}


def test_long_text_is_cut_and_money_has_no_stray_zeros():
    assert len(D.content(text("1", "x" * 5000))["x"]) == D.MAX_TEXT
    assert D.content({"sitelinkAsset": {"linkText": "No type given"}}) == {"t": "SITELINK", "x": "No type given"}                     # the kind is found from what is filled in
    assert D.money({"currencyCode": "USD", "amountMicros": "100000000"}) == "USD 100"
    assert D.money({"currencyCode": "USD", "amountMicros": "12500000"}) == "USD 12.5"
    assert D.money({}) == "" and D.money(None) == "" and D.money({"amountMicros": ""}) == ""


# ── Reading an account into a Collector ─────────────────────────────────────

def test_a_link_keeps_its_state_why_dates_source_and_30_day_numbers_and_each_asset_is_stored_once():
    s = sink()
    shown = {("5", "a1"): {"i": 120, "k": 9}}
    rows = [link_row("5", "SITELINK", "a1", asset={"sitelinkAsset": {"linkText": "One"}}),
            link_row("5", "SITELINK", "a2", primary="NOT_ELIGIBLE", reasons=["ASSET_DISAPPROVED"], asset={"sitelinkAsset": {"linkText": "Two"}}),
            link_row("5", "SITELINK", "a3", status="PAUSED", primary="PAUSED", reasons=["ASSET_APPROVED_LABELED", "ASSET_LINK_PAUSED"], asset={"sitelinkAsset": {"linkText": "Three"}}),
            link_row("5", "SITELINK", "a4", asset={"sitelinkAsset": {"linkText": "Four", "endDate": "2026-05-31"}}),
            link_row("5", "SITELINK", "a5", primary="PENDING", reasons=["ASSET_UNDER_REVIEW"], asset={"sitelinkAsset": {"linkText": "Five"}}),
            link_row("6", "SITELINK", "a1", asset={"sitelinkAsset": {"linkText": "One"}})]
    rows[0]["campaignAsset"]["source"] = "AUTOMATICALLY_CREATED"
    a.read_campaign_assets(rows, shown, TODAY, s)
    by = {l["a"]: l for l in s.c["5"]}
    assert by["a1"] == {"a": "a1", "f": "SITELINK", "s": "live", "auto": 1, "i": 120, "k": 9}
    assert by["a2"] == {"a": "a2", "f": "SITELINK", "s": "disapproved", "r": ["ASSET_DISAPPROVED"]}
    assert by["a3"] == {"a": "a3", "f": "SITELINK", "s": "paused", "r": ["ASSET_LINK_PAUSED"]}            # the quiet reason says nothing
    assert by["a4"] == {"a": "a4", "f": "SITELINK", "s": "expired", "e": "2026-05-31"}
    assert by["a5"] == {"a": "a5", "f": "SITELINK", "s": "pending", "r": ["ASSET_UNDER_REVIEW"]}
    assert s.c["6"][0] == {"a": "a1", "f": "SITELINK", "s": "live"}                                       # a different campaign: its own numbers (none)
    assert sorted(s.assets) == ["a1", "a2", "a3", "a4", "a5"] and s.assets["a1"] == {"t": "SITELINK", "x": "One"}


def test_the_same_link_read_twice_is_one_tile():
    s = sink()
    row = link_row("5", "CALLOUT", "c1", asset={"calloutAsset": {"calloutText": "Open late"}})
    a.read_campaign_assets([row, row], None, TODAY, s)
    s.campaigns["5"] = {"name": "Brand", "type": "SEARCH"}
    assert len(s.c["5"]) == 2 and len(sections(s.to_dict())["CALLOUT"]["tiles"]) == 1 and sections(s.to_dict())["CALLOUT"]["live"] == 2


def test_a_failed_shown_read_still_keeps_the_links_without_numbers():
    s = sink()
    a.read_campaign_assets([link_row("5", "CALLOUT", "c1", asset={"calloutAsset": {"calloutText": "Open late"}})], None, TODAY, s)
    assert s.c["5"] == [{"a": "c1", "f": "CALLOUT", "s": "live"}]


def test_shown_numbers_are_read_with_clicks_and_summed_for_the_same_link():
    got = a.read_shown([{"campaign": {"id": "5"}, "asset": {"id": "a1"}, "metrics": {"impressions": "100", "clicks": "7"}},
                        {"campaign": {"id": "5"}, "asset": {"id": "a1"}, "metrics": {"impressions": "50", "clicks": "1"}},
                        {"campaign": {"id": "5"}, "asset": {"id": "a2"}, "metrics": {"impressions": "0", "clicks": "0"}}])
    assert got == {("5", "a1"): {"i": 150, "k": 8}} and ("5", "a1") in got and ("5", "a2") not in got


def test_every_part_of_an_account_lands_in_the_collector_and_only_when_asked_for():
    camps = [camp_row("5", "Brand"), camp_row("6", "PMax", "PERFORMANCE_MAX")]
    kw = dict(campaigns=camps,
              campaign_assets=[link_row("5", "CALLOUT", "c1", asset={"calloutAsset": {"calloutText": "Open late"}})],
              customer_assets=[link_row(None, "SITELINK", "u1", level="customerAsset", asset={"sitelinkAsset": {"linkText": "Home"}, "finalUrls": ["https://x.com"]})],
              groups=[{"campaign": {"id": "6"}, "assetGroup": {"id": "70", "name": "Crowns"}}],
              group_assets=[{"campaign": {"id": "6"}, "assetGroup": {"id": "70"}, "assetGroupAsset": {"fieldType": "HEADLINE", "status": "ENABLED", "primaryStatus": "ELIGIBLE"}, "asset": dict(text("h1", "Crowns today"))}],
              campaign_sets=[{"campaign": {"id": "5"}, "assetSet": {"type": "LOCATION_SYNC", "name": "Main Street"}}],
              customer_sets=[{"assetSet": {"type": "BUSINESS_PROFILE_DYNAMIC_LOCATION_GROUP", "name": "All offices"}}, {"assetSet": {"type": "MERCHANT_CENTER_FEED", "name": "Feed"}}])
    s, read = collect(**kw)
    assert s.campaigns == {"5": {"name": "Brand", "type": "SEARCH"}, "6": {"name": "PMax", "type": "PERFORMANCE_MAX"}}
    assert s.c["5"][0]["a"] == "c1" and s.u == [{"a": "u1", "f": "SITELINK", "s": "live"}] and s.assets["u1"]["u"] == "https://x.com"
    assert s.g == {"70": {"n": "Crowns", "c": "6", "l": [{"a": "h1", "f": "HEADLINE", "s": "live"}]}}
    assert s.sets == [{"n": "Main Street", "t": "LOCATION_SYNC", "c": "5"}, {"n": "All offices", "t": "BUSINESS_PROFILE_DYNAMIC_LOCATION_GROUP", "c": ""}]
    d = s.to_dict()
    assert d["v"] == D.DETAIL_V and d["id"] == "1234567890" and d["name"] == "Acme Dental" and d["day"] == "2026-10-16" and d["assets"] == s.assets
    plain = a.read_account(MEMBER, search_fn=searcher(**kw), today=TODAY)                          # no sink: judged exactly the same
    assert plain["campaignAssets"] == read["campaignAssets"] and plain["groups"] == read["groups"] and plain["sets"] == read["sets"]


def display_ad(ad_id="900", cid="7"):
    return {"campaign": {"id": cid}, "adGroup": {"id": "70", "name": "Level 1"}, "adGroupAd": {"ad": {"id": ad_id, "type": "RESPONSIVE_DISPLAY_AD", "responsiveDisplayAd": {
        "marketingImages": [{"asset": "customers/1234567890/assets/555"}], "squareMarketingImages": [{"asset": "customers/1234567890/assets/555"}],
        "headlines": [{"text": "Hello"}, {"text": "World"}], "longHeadline": {"text": "A long one"}, "descriptions": [{"text": " "}],
        "businessName": "Acme", "youtubeVideos": [{"asset": "customers/1234567890/assets/556"}]}}}}


def test_the_assets_inside_a_display_ad_are_read_by_id_in_one_batch():
    log = []
    by_id = [{"asset": image("555", "Hero.jpg")}, {"asset": {"type": "YOUTUBE_VIDEO", "id": "556", "youtubeVideoAsset": {"youtubeVideoId": "abcdefghijk", "youtubeVideoTitle": "Tour"}}}]
    s, read = collect(campaigns=[camp_row("7", "Disp", "DISPLAY")], ads={"RESPONSIVE_DISPLAY_AD": [display_ad()]}, asset_by_id=by_id, log=log)
    f = s.ads[0]["f"]
    assert f["MARKETING_IMAGE"] == [{"a": "555"}] and f["SQUARE_MARKETING_IMAGE"] == [{"a": "555"}] and f["YOUTUBE_VIDEO"] == [{"a": "556"}]
    assert f["HEADLINE"] == [{"x": "Hello"}, {"x": "World"}] and f["LONG_HEADLINE"] == [{"x": "A long one"}] and f["BUSINESS_NAME"] == [{"x": "Acme"}] and f["DESCRIPTION"] == []
    assert s.ads[0]["c"] == "7" and s.ads[0]["g"] == "Level 1" and s.ads[0]["t"] == "RESPONSIVE_DISPLAY_AD" and s.ads[0]["id"] == "900"
    assert s.assets["555"]["t"] == "IMAGE" and s.assets["556"]["vid"] == "abcdefghijk"
    asked = [q for _c, q in log if "FROM asset WHERE" in q]
    assert len(asked) == 1 and "asset.id IN (555, 556)" in asked[0]                                    # each picture once, in one question
    assert read["ads"][0]["counts"]["HEADLINE"] == 2                                                    # the judging counts are what they were


def test_only_a_real_asset_name_is_taken_as_an_id():
    assert a._asset_id("customers/1234567890/assets/555") == "555" and a._asset_id("customers/1/assets/abc") == "" and a._asset_id("") == "" and a._asset_id(None) == ""
    assert a._ad_items([{"asset": "customers/1/assets/abc"}, {"asset": "customers/1/assets/7"}, {"text": " "}, {"text": "Hi"}, "Plain", "  ", {}]) == [{"a": "7"}, {"x": "Hi"}, {"x": "Plain"}]
    assert a._ad_items(None) == [] and a._ad_items({"text": "One"}) == [{"x": "One"}]


def test_a_big_display_account_asks_for_its_pictures_in_batches():
    ids = list(range(1, 400))
    log = []
    ad = display_ad()
    ad["adGroupAd"]["ad"]["responsiveDisplayAd"]["marketingImages"] = [{"asset": "customers/1/assets/%d" % i} for i in ids]
    collect(campaigns=[camp_row("7", "Disp", "DISPLAY")], ads={"RESPONSIVE_DISPLAY_AD": [ad]}, asset_by_id=[], log=log)
    asked = [q for _c, q in log if "FROM asset WHERE" in q]
    sizes = [len(q.split("IN (")[1].rstrip(")").split(", ")) for q in asked]
    assert sizes == [a.ASSET_BATCH, a.ASSET_BATCH, len(ids) + 2 - 2 * a.ASSET_BATCH]                      # the 399 pictures and the ad's own 555 and 556


def test_pictures_that_cannot_be_read_are_a_warning_and_the_audit_is_untouched():
    s, read = collect(campaigns=[camp_row("7", "Disp", "DISPLAY")], ads={"RESPONSIVE_DISPLAY_AD": [display_ad()]}, fail=("asset_by_id",))
    assert any("pictures and videos" in w for w in read["warnings"]) and read["ok"]["adAssets"] is False
    assert read["ads"][0]["counts"]["MARKETING_IMAGE"] == 1 and s.ads[0]["f"]["MARKETING_IMAGE"] == [{"a": "555"}] and "555" not in s.assets


def test_nothing_extra_is_asked_without_a_collector():
    log = []
    a.read_account(MEMBER, search_fn=searcher(campaigns=[camp_row("7", "Disp", "DISPLAY")], ads={"RESPONSIVE_DISPLAY_AD": [display_ad()]}, log=log), today=TODAY)
    assert not [q for _c, q in log if "FROM asset WHERE" in q]


def test_the_queries_ask_for_what_the_detail_shows():
    for q in (a.CAMPAIGN_ASSETS_GAQL, a.CUSTOMER_ASSETS_GAQL, a.GROUP_ASSETS_GAQL):
        for field in ("asset.image_asset.full_size.url", "asset.sitelink_asset.description1", "asset.structured_snippet_asset.values", "asset.price_asset.price_offerings",
                      "asset.youtube_video_asset.youtube_video_id", "asset.promotion_asset.percent_off", "asset.final_urls"):
            assert field in q, (field, q[:60])
    assert "campaign_asset.source" in a.CAMPAIGN_ASSETS_GAQL and "customer_asset.source" in a.CUSTOMER_ASSETS_GAQL and "metrics.clicks" in a.CAMPAIGN_ASSETS_SHOWN_GAQL
    assert "asset_set.name" in a.CAMPAIGN_SETS_GAQL and "asset_set.name" in a.CUSTOMER_SETS_GAQL
    assert "asset.id IN (%s)" in a.ASSET_BY_ID_GAQL and "asset.image_asset.full_size.url" in a.ASSET_BY_ID_GAQL


# ── The summary no longer mixes expired with disapproved ────────────────────

def test_disapproved_and_past_their_end_date_are_counted_as_separate_assets():
    camps = [camp_row("1"), camp_row("2")]
    rows = ([link_row(c, "SITELINK", "bad", primary="NOT_ELIGIBLE", asset={"sitelinkAsset": {"linkText": "Bad"}}) for c in ("1", "2")] +
            [link_row(c, "SITELINK", "old", asset={"sitelinkAsset": {"linkText": "Old", "endDate": "2026-05-01"}}) for c in ("1", "2")] +
            [link_row("1", "SITELINK", "old2", asset={"sitelinkAsset": {"linkText": "Old 2", "endDate": "2026-05-01"}})])
    rep = a.assemble({"accounts": [a.read_account(MEMBER, search_fn=searcher(campaigns=camps, campaign_assets=rows), today=TODAY)], "warnings": []}, ACCT, TODAY)
    s = rep["summary"]
    assert s["disapprovedAssets"] == 1 and s["expiredAssets"] == 2 and s["expired"] == 3        # 2 assets, linked 3 times


# ── The saved detail ────────────────────────────────────────────────────────

class Store:
    def __init__(self):
        self.saved, self.any, self.fail_get, self.fail_put = {}, {}, False, False

    def get_gads_cache(self, key, ttl):
        if self.fail_get:
            raise RuntimeError("down")
        return self.saved.get(key)

    def get_gads_cache_any(self, key):
        return (self.any[key], "2026-10-10T00:00:00+00:00") if key in self.any else None

    def put_gads_cache(self, key, value):
        if self.fail_put:
            raise RuntimeError("down")
        self.saved[key] = value


def reader(log=None, **kw):
    search = searcher(log=log, **kw)
    return lambda m, sink_: a.read_account(m, search_fn=search, today=TODAY, sink=sink_)


def test_a_detail_is_read_once_saved_and_then_served_from_memory():
    store, log = Store(), []
    rd = reader(log, campaigns=[camp_row("5")], campaign_assets=many("5", "SITELINK", 2))
    d = D.load_detail(MEMBER, read=rd, store=store, today=TODAY)
    n = len(log)
    assert n and d["c"]["5"][0]["f"] == "SITELINK" and D.store_key("1234567890") in store.saved
    assert D.load_detail(MEMBER, read=rd, store=store, today=TODAY) is d and len(log) == n
    D.mem_clear()
    again = D.load_detail(MEMBER, read=rd, store=store, today=TODAY)                      # memory gone: the store answers, Google is not asked
    assert again == d and len(log) == n


def test_two_people_opening_the_same_account_at_once_share_one_read():
    import threading
    store, started, go, reads = Store(), threading.Event(), threading.Event(), []

    def slow(m, sink_):
        reads.append(1)
        started.set()
        go.wait(5)
        sink_.campaigns["5"] = {"name": "Brand", "type": "SEARCH"}
    got = []
    first = threading.Thread(target=lambda: got.append(D.load_detail(MEMBER, read=slow, store=store, today=TODAY)))
    first.start()
    started.wait(5)
    second = threading.Thread(target=lambda: got.append(D.load_detail(MEMBER, read=slow, store=store, today=TODAY)))
    second.start()
    go.set()
    first.join(5)
    second.join(5)
    assert len(reads) == 1 and len(got) == 2 and got[0] is got[1]


def test_a_detail_saved_in_another_shape_or_not_at_all_is_read_again():
    store, log = Store(), []
    store.saved[D.store_key("1234567890")] = {"v": D.DETAIL_V + 1, "id": "1234567890", "c": {"5": ["old"]}}
    rd = reader(log, campaigns=[camp_row("5")], campaign_assets=many("5", "SITELINK", 2))
    d = D.load_detail(MEMBER, read=rd, store=store, today=TODAY)
    assert log and d["v"] == D.DETAIL_V and len(d["c"]["5"]) == 2 and store.saved[D.store_key("1234567890")]["v"] == D.DETAIL_V


def test_a_store_that_is_down_costs_nothing_but_the_save():
    store, log = Store(), []
    store.fail_get = store.fail_put = True
    d = D.load_detail(MEMBER, read=reader(log, campaigns=[camp_row("5")], campaign_assets=many("5", "SITELINK", 1)), store=store, today=TODAY)
    assert d["c"]["5"][0]["f"] == "SITELINK" and store.saved == {}
    n = len(log)
    assert D.load_detail(MEMBER, read=reader(log), store=store, today=TODAY) is d and len(log) == n      # memory still serves it
    D.save_detail(store, d)                                                                               # and a failed save is swallowed


def test_memory_holds_a_few_accounts_for_a_few_minutes():
    for i in range(D.MEM_MAX + 2):
        D.mem_put("acct%d" % i, {"id": "acct%d" % i}, now=1000 + i)
    assert D.mem_get("acct0", now=1010) is None and D.mem_get("acct%d" % (D.MEM_MAX + 1), now=1010) == {"id": "acct%d" % (D.MEM_MAX + 1)}
    assert D.mem_get("acct%d" % (D.MEM_MAX + 1), now=1000 + D.MEM_MAX + 1 + D.MEM_TTL_S + 1) is None


def test_a_spent_allowance_serves_the_last_saved_detail_and_without_one_raises():
    store = Store()

    def spent(m, sink_):
        raise gads.GadsError("quota", "The daily limit is used up.")
    with pytest.raises(gads.GadsError, match="daily limit"):
        D.load_detail(MEMBER, read=spent, store=store, today=TODAY)
    store.any[D.store_key("1234567890")] = {"v": D.DETAIL_V, "id": "1234567890", "assets": {}, "campaigns": {}, "c": {}, "u": [], "g": {}, "ads": [], "sets": []}
    assert D.load_detail(MEMBER, read=spent, store=store, today=TODAY)["id"] == "1234567890"
    store.any[D.store_key("1234567890")]["v"] = 0
    with pytest.raises(gads.GadsError):
        D.load_detail(MEMBER, read=spent, store=store, today=TODAY)

    def broken(m, sink_):
        raise gads.GadsError("api", "google broke")
    store.any[D.store_key("1234567890")]["v"] = D.DETAIL_V
    with pytest.raises(gads.GadsError, match="google broke"):                                       # only a spent allowance falls back to an old copy
        D.load_detail(MEMBER, read=broken, store=store, today=TODAY)


# ── One campaign ────────────────────────────────────────────────────────────

def callout(cid, aid, words, **kw):
    return link_row(cid, "CALLOUT", aid, asset={"calloutAsset": {"calloutText": words}}, **kw)


def test_a_search_campaigns_sections_follow_the_audits_kinds_and_open_onto_the_assets():
    det = detail_of(campaigns=[camp_row("5")], metrics=[metric_row("5")],
                    campaign_assets=[link_row("5", "SITELINK", "s%d" % i, asset={"sitelinkAsset": {"linkText": "Link %d" % i}}) for i in range(3)] +
                                    [link_row("5", "AD_IMAGE", "i1", asset=image("i1", "Hero.jpg")), link_row("5", "BUSINESS_NAME", "b1", asset=text("b1", "Acme"))])
    sec = sections(det)
    assert list(sec) == ["SITELINK", "CALLOUT", "STRUCTURED_SNIPPET", "CALL", "AD_IMAGE", "BUSINESS_NAME", "LOGO", "PROMOTION", "PRICE", "LEAD_FORM"]
    assert sec["SITELINK"]["live"] == 3 and sorted(t["x"] for t in sec["SITELINK"]["tiles"]) == ["Link 0", "Link 1", "Link 2"] and sec["SITELINK"]["level"] == "campaign"
    assert all(t["lv"] == "campaign" and t["st"] == "live" for t in sec["SITELINK"]["tiles"])
    assert sec["AD_IMAGE"]["tiles"][0]["img"] == GOOGLE_IMG and sec["AD_IMAGE"]["tiles"][0]["n"] == "Hero.jpg"
    assert sec["BUSINESS_NAME"]["tiles"][0]["x"] == "Acme" and sec["CALL"]["tiles"] == [] and sec["CALL"]["live"] == 0
    out = D.campaign_detail(det, "5")
    assert out["campaign"] == "Search_Brand" and out["type"] == "SEARCH" and out["groups"] == [] and out["ads"] == []


def test_a_kind_with_none_of_its_own_shows_the_accounts_and_still_shows_the_dead_ones_of_its_own():
    det = detail_of(campaigns=[camp_row("5")],
                    campaign_assets=[callout("5", "own1", "Gone", primary="NOT_ELIGIBLE", reasons=["ASSET_DISAPPROVED"])],
                    customer_assets=[link_row(None, "CALLOUT", "u%d" % i, level="customerAsset", asset={"calloutAsset": {"calloutText": "Acct %d" % i}}) for i in range(2)])
    c = sections(det)["CALLOUT"]
    assert c["level"] == "account" and c["live"] == 2
    assert [(t["x"], t["lv"], t["st"]) for t in c["tiles"]] == [("Gone", "campaign", "disapproved"), ("Acct 0", "account", "live"), ("Acct 1", "account", "live")]
    gone = next(t for t in c["tiles"] if t["x"] == "Gone")
    assert gone["why"] == ["Disapproved by Google"]


def test_an_accounts_dead_assets_are_not_shown_under_a_campaign_that_has_none_of_its_own():
    det = detail_of(campaigns=[camp_row("5")], customer_assets=[link_row(None, "CALLOUT", "u1", level="customerAsset", primary="NOT_ELIGIBLE", asset={"calloutAsset": {"calloutText": "Dead"}})])
    c = sections(det)["CALLOUT"]
    assert c["level"] == "campaign" and c["live"] == 0 and c["tiles"] == []                 # the account's problems are told once, under the account's assets


def test_a_kind_with_a_live_asset_of_its_own_does_not_show_the_accounts():
    det = detail_of(campaigns=[camp_row("5")], campaign_assets=[callout("5", "own1", "Mine")],
                    customer_assets=[link_row(None, "CALLOUT", "u1", level="customerAsset", asset={"calloutAsset": {"calloutText": "Account"}})])
    c = sections(det)["CALLOUT"]
    assert c["level"] == "campaign" and [t["x"] for t in c["tiles"]] == ["Mine"]


def test_expired_and_paused_assets_say_why_and_come_after_the_live_ones():
    det = detail_of(campaigns=[camp_row("5")], campaign_assets=[
        link_row("5", "SITELINK", "old", asset={"sitelinkAsset": {"linkText": "Old offer", "endDate": "2026-05-31"}}),
        link_row("5", "SITELINK", "off", status="PAUSED", primary="PAUSED", reasons=["ASSET_LINK_PAUSED"], asset={"sitelinkAsset": {"linkText": "Off"}}),
        link_row("5", "SITELINK", "rev", primary="PENDING", reasons=["ASSET_UNDER_REVIEW"], asset={"sitelinkAsset": {"linkText": "In review"}}),
        link_row("5", "SITELINK", "ok", asset={"sitelinkAsset": {"linkText": "Fine"}})])
    s = sections(det)["SITELINK"]
    assert [(t["x"], t["st"]) for t in s["tiles"]] == [("In review", "pending"), ("Fine", "live"), ("Old offer", "expired"), ("Off", "paused")]
    assert s["live"] == 2                                                                              # under review counts, as the audit counts it
    by = {t["x"]: t for t in s["tiles"]}
    assert by["Old offer"]["why"] == ["Ended 2026-05-31 but still switched on"] and by["Old offer"]["st"] == "expired"
    assert by["Off"]["why"] == ["Paused"] and by["In review"]["why"] == ["Under review"]


def test_the_thirty_day_numbers_and_who_added_it_ride_on_the_tile():
    row = link_row("5", "SITELINK", "s1", asset={"sitelinkAsset": {"linkText": "One"}})
    row["campaignAsset"]["source"] = "AUTOMATICALLY_CREATED"
    det = detail_of(campaigns=[camp_row("5")], campaign_assets=[row], shown=[{"campaign": {"id": "5"}, "asset": {"id": "s1"}, "metrics": {"impressions": "300", "clicks": "12"}}])
    t = sections(det)["SITELINK"]["tiles"][0]
    assert (t["i"], t["k"], t["auto"]) == (300, 12, 1) and t["id"] == "s1"


def test_assets_the_audit_does_not_judge_are_still_listed_for_the_campaign():
    det = detail_of(campaigns=[camp_row("6", "PMax", "PERFORMANCE_MAX")], campaign_assets=[
        link_row("6", "BUSINESS_NAME", "bn", asset=text("bn", "Acme")), link_row("6", "LANDSCAPE_LOGO", "ll", asset=image("ll", "Wide logo.png"))])
    sec = sections(det, "6")
    assert "BUSINESS_NAME" in sec and sec["BUSINESS_NAME"]["label"] == "Business name" and sec["BUSINESS_NAME"]["tiles"][0]["x"] == "Acme"
    assert sec["LANDSCAPE_LOGO"]["label"] == "Landscape logo" and sec["LANDSCAPE_LOGO"]["tiles"][0]["n"] == "Wide logo.png"
    assert "AD_IMAGE" not in sec and "SITELINK" in sec


def test_a_campaigns_location_sets_are_named_and_the_accounts_are_used_when_it_has_none():
    mine = detail_of(campaigns=[camp_row("5")], campaign_sets=[{"campaign": {"id": "5"}, "assetSet": {"type": "LOCATION_SYNC", "name": "Main Street"}}],
                     customer_sets=[{"assetSet": {"type": "STATIC_LOCATION_GROUP", "name": "All offices"}}])
    loc = sections(mine)["LOCATION"]
    assert loc["level"] == "campaign" and loc["tiles"] == [{"t": "SET", "x": "Main Street", "n": "Location sync", "st": "live", "lv": "campaign"}]
    theirs = detail_of(campaigns=[camp_row("5")], customer_sets=[{"assetSet": {"type": "STATIC_LOCATION_GROUP", "name": "All offices"}}])
    loc = sections(theirs)["LOCATION"]
    assert loc["level"] == "account" and loc["tiles"][0]["x"] == "All offices" and loc["tiles"][0]["lv"] == "account"
    assert "LOCATION" not in sections(detail_of(campaigns=[camp_row("5")]))


def pmax_group(gid, name, cid="6"):
    return {"campaign": {"id": cid}, "assetGroup": {"id": gid, "name": name}}


def group_link(gid, field, aid, asset, cid="6", **kw):
    node = {"fieldType": field, "status": kw.get("status", "ENABLED"), "primaryStatus": kw.get("primary", "ELIGIBLE")}
    return {"campaign": {"id": cid}, "assetGroup": {"id": gid}, "assetGroupAsset": node, "asset": dict({"id": aid}, **asset)}


def test_a_performance_max_group_shows_its_assets_and_the_campaigns_logo_when_it_has_none_of_its_own():
    det = detail_of(
        campaigns=[camp_row("6", "PMax", "PERFORMANCE_MAX")], groups=[pmax_group("70", "Crowns"), pmax_group("71", "Implants")],
        campaign_assets=[link_row("6", "BUSINESS_LOGO", "logo1", asset=image("logo1", "Logo.png"))],
        group_assets=[group_link("70", "HEADLINE", "h1", text("h1", "Crowns today")), group_link("70", "HEADLINE", "h2", text("h2", "Gentle crowns")),
                      group_link("70", "SQUARE_MARKETING_IMAGE", "sq", image("sq", "Square.jpg")), group_link("70", "CALL_TO_ACTION_SELECTION", "cta", {"type": "CALL_TO_ACTION"}),
                      group_link("71", "LOGO", "own", image("own", "Group logo.png"))])
    groups = {g["id"]: {x["key"]: x for x in g["sections"]} for g in D.campaign_detail(det, "6")["groups"]}
    assert set(groups) == {"70", "71"}
    crowns, implants = groups["70"], groups["71"]
    assert [t["x"] for t in crowns["HEADLINE"]["tiles"]] == ["Crowns today", "Gentle crowns"]
    assert crowns["HEADLINE"]["level"] == "asset group" and crowns["SQUARE_MARKETING_IMAGE"]["tiles"][0]["img"] == GOOGLE_IMG
    assert crowns["LOGO"]["level"] == "campaign" and crowns["LOGO"]["tiles"][0]["n"] == "Logo.png" and crowns["LOGO"]["tiles"][0]["lv"] == "campaign"
    assert implants["LOGO"]["tiles"][0]["n"] == "Group logo.png" and implants["LOGO"]["tiles"][0]["lv"] == "asset group"
    assert crowns["CALL_TO_ACTION_SELECTION"]["label"] == "Call to action" and crowns["CALL_TO_ACTION_SELECTION"]["tiles"][0]["t"] == "CALL_TO_ACTION"
    names = {g["id"]: g["name"] for g in D.campaign_detail(det, "6")["groups"]}
    assert names == {"70": "Crowns", "71": "Implants"}


def test_a_groups_logo_falls_back_to_the_accounts_when_neither_group_nor_campaign_has_one():
    det = detail_of(campaigns=[camp_row("6", "PMax", "PERFORMANCE_MAX")], groups=[pmax_group("70", "Crowns")],
                    customer_assets=[link_row(None, "BUSINESS_LOGO", "al", level="customerAsset", asset=image("al", "Account logo.png"))])
    logo = {x["key"]: x for x in D.campaign_detail(det, "6")["groups"][0]["sections"]}["LOGO"]
    assert logo["level"] == "account" and logo["tiles"][0]["n"] == "Account logo.png"


def test_display_and_demand_gen_ads_show_their_pictures_by_id_and_their_text():
    by_id = [{"asset": image("555", "Hero.jpg")}, {"asset": {"type": "YOUTUBE_VIDEO", "id": "556", "youtubeVideoAsset": {"youtubeVideoId": "abcdefghijk", "youtubeVideoTitle": "Tour"}}}]
    det = detail_of(campaigns=[camp_row("7", "Disp", "DISPLAY")], ads={"RESPONSIVE_DISPLAY_AD": [display_ad()]}, asset_by_id=by_id)
    out = D.campaign_detail(det, "7")
    ad = out["ads"][0]
    assert ad["id"] == "900" and ad["adGroup"] == "Level 1" and ad["typeLabel"] == "Responsive display ad" and out["sections"] == [] and out["groups"] == []
    sec = {x["key"]: x for x in ad["sections"]}
    assert [t["x"] for t in sec["HEADLINE"]["tiles"]] == ["Hello", "World"] and sec["HEADLINE"]["tiles"][0]["t"] == "TEXT" and sec["HEADLINE"]["level"] == "ad"
    assert sec["MARKETING_IMAGE"]["tiles"][0]["img"] == GOOGLE_IMG and sec["SQUARE_MARKETING_IMAGE"]["tiles"][0]["n"] == "Hero.jpg"
    assert sec["YOUTUBE_VIDEO"]["tiles"][0]["vid"] == "abcdefghijk" and sec["BUSINESS_NAME"]["tiles"][0]["x"] == "Acme" and sec["LONG_HEADLINE"]["tiles"][0]["x"] == "A long one"
    assert sec["DESCRIPTION"]["tiles"] == [] and sec["LOGO"]["live"] == 0
    assert [s["key"] for s in ad["sections"]] == list(rules.DISPLAY_AD_ORDER)


def test_a_picture_already_known_from_a_link_is_not_asked_for_again():
    log = []
    ad = display_ad()
    ad["adGroupAd"]["ad"]["responsiveDisplayAd"]["youtubeVideos"] = []
    collect(campaigns=[camp_row("7", "Disp", "DISPLAY")], campaign_assets=[link_row("7", "AD_IMAGE", "555", asset=image("555", "Known.jpg"))], ads={"RESPONSIVE_DISPLAY_AD": [ad]}, log=log)
    assert not [q for _c, q in log if "FROM asset WHERE" in q]                                  # 555 came with the campaign's own link


def test_a_picture_that_could_not_be_read_is_still_a_tile_without_a_picture():
    det = detail_of(campaigns=[camp_row("7", "Disp", "DISPLAY")], ads={"RESPONSIVE_DISPLAY_AD": [display_ad()]}, fail=("asset_by_id",))
    sec = {x["key"]: x for x in D.campaign_detail(det, "7")["ads"][0]["sections"]}
    assert sec["MARKETING_IMAGE"]["tiles"][0]["id"] == "555" and "img" not in sec["MARKETING_IMAGE"]["tiles"][0]


def test_a_campaign_the_detail_does_not_know_has_no_sections():
    out = D.campaign_detail(detail_of(campaigns=[camp_row("5")]), "99")
    assert out == {"campaign": "", "type": "", "sections": [], "groups": [], "ads": []}


# ── Every asset of one kind ─────────────────────────────────────────────────

def shared_detail():
    """Three campaigns: one sitelink copied into two of them, a dead one, an account callout, a group's image."""
    return detail_of(
        campaigns=[camp_row("1", "Alpha"), camp_row("2", "Beta"), camp_row("3", "Gamma", "PERFORMANCE_MAX")],
        shown=[{"campaign": {"id": "1"}, "asset": {"id": "s1"}, "metrics": {"impressions": "100", "clicks": "5"}}, {"campaign": {"id": "2"}, "asset": {"id": "s2"}, "metrics": {"impressions": "40", "clicks": "2"}}],
        campaign_assets=[link_row("1", "SITELINK", "s1", asset={"sitelinkAsset": {"linkText": "Book now"}, "finalUrls": ["https://x.com/a"]}),
                         link_row("2", "SITELINK", "s2", asset={"sitelinkAsset": {"linkText": "Book now"}, "finalUrls": ["https://x.com/a"]}),
                         link_row("2", "SITELINK", "s3", primary="NOT_ELIGIBLE", reasons=["ASSET_DISAPPROVED"], asset={"sitelinkAsset": {"linkText": "Gone"}}),
                         link_row("1", "SITELINK", "s4", asset={"sitelinkAsset": {"linkText": "Old", "endDate": "2026-01-01"}}),
                         link_row("1", "SITELINK", "s5", status="PAUSED", primary="PAUSED", asset={"sitelinkAsset": {"linkText": "Off"}}),
                         link_row("1", "AD_IMAGE", "i1", asset=image("i1", "Hero.jpg"))],
        customer_assets=[link_row(None, "CALLOUT", "u1", level="customerAsset", asset={"calloutAsset": {"calloutText": "Open late"}}),
                         link_row(None, "SITELINK", "u2", level="customerAsset", asset={"sitelinkAsset": {"linkText": "Home"}})],
        groups=[pmax_group("70", "Crowns", "3")],
        group_assets=[group_link("70", "SQUARE_MARKETING_IMAGE", "i1", image("i1", "Hero.jpg"), cid="3")])


def test_a_kind_lists_each_asset_once_with_who_uses_it_and_how_it_did():
    lib = D.library([shared_detail()], "SITELINK", "all")
    by = {x["x"]: x for x in lib["assets"]}
    assert set(by) == {"Book now", "Gone", "Old", "Off", "Home"}
    book = by["Book now"]
    assert book["copies"] == 2 and book["nc"] == 2 and (book["i"], book["k"]) == (140, 7) and book["st"] == "live"      # two identical copies, one card
    assert sorted(c["name"] for c in book["camps"]) == ["Alpha", "Beta"] and {c["key"] for c in book["camps"]} == {"1234567890:1", "1234567890:2"}
    assert by["Home"]["acct"] == 1 and by["Home"]["nc"] == 0 and "camps" in by["Home"]
    assert by["Gone"]["st"] == "disapproved" and by["Gone"]["why"] == ["Disapproved by Google"] and by["Old"]["st"] == "expired" and by["Off"]["st"] == "paused"
    assert lib["counts"] == {"all": 5, "live": 2, "problem": 3, "disapproved": 1, "expired": 1, "paused": 1, "pending": 0} and lib["total"] == 5 and lib["assetCount"] == 6
    assert lib["kind"] == "SITELINK" and lib["state"] == "all"


@pytest.mark.parametrize("state,want", [
    ("live", {"Book now", "Home"}), ("problem", {"Gone", "Old", "Off"}), ("disapproved", {"Gone"}), ("expired", {"Old"}), ("paused", {"Off"}), ("pending", set()),
    ("all", {"Book now", "Gone", "Old", "Off", "Home"})])
def test_a_library_can_be_narrowed_to_live_or_to_each_kind_of_problem(state, want):
    lib = D.library([shared_detail()], "SITELINK", state)
    assert {x["x"] for x in lib["assets"]} == want and lib["total"] == len(want) and lib["counts"]["all"] == 5      # the counts are always the whole kind


def test_with_no_kind_every_kind_is_listed_and_a_picture_used_in_a_campaign_and_a_group_is_one_asset():
    lib = D.library([shared_detail()], None, "all")
    imgs = [x for x in lib["assets"] if x["t"] == "IMAGE"]
    assert len(imgs) == 1 and imgs[0]["nc"] == 2 and imgs[0]["ng"] == 1 and sorted(imgs[0]["fl"]) == ["Images", "Square images"]
    assert {x["t"] for x in lib["assets"]} == {"SITELINK", "CALLOUT", "IMAGE"}


def test_the_accounts_own_links_can_be_asked_for_alone():
    lib = D.library([shared_detail()], "SITELINK", "all", level="account")
    assert [x["x"] for x in lib["assets"]] == ["Home"] and D.library([shared_detail()], None, "all", level="account")["total"] == 2


def test_problems_come_first_when_asked_for_and_the_busiest_first_otherwise():
    live = D.library([shared_detail()], "SITELINK", "live")["assets"]
    assert [x["x"] for x in live] == ["Book now", "Home"]                                              # the shown one, then the unshown
    prob = D.library([shared_detail()], "SITELINK", "problem")["assets"]
    assert [x["st"] for x in prob] == ["disapproved", "expired", "paused"]


def test_one_asset_linked_in_two_places_is_as_bad_as_its_worst_link_and_adds_up_its_numbers():
    shared = dict(asset={"sitelinkAsset": {"linkText": "Shared"}})
    det = detail_of(campaigns=[camp_row("1"), camp_row("2")], campaign_assets=[
        link_row("1", "SITELINK", "same", **shared), link_row("2", "SITELINK", "same", primary="NOT_ELIGIBLE", reasons=["ASSET_DISAPPROVED"], **shared)],
        shown=[{"campaign": {"id": "1"}, "asset": {"id": "same"}, "metrics": {"impressions": "100", "clicks": "5"}},
               {"campaign": {"id": "2"}, "asset": {"id": "same"}, "metrics": {"impressions": "40", "clicks": "2"}}])
    lib = D.library([det], "SITELINK", "all")
    x = lib["assets"][0]
    assert lib["total"] == 1 and "copies" not in x and x["st"] == "disapproved" and x["why"] == ["Disapproved by Google"] and x["nc"] == 2 and (x["i"], x["k"]) == (140, 7)


def test_identical_assets_in_a_different_state_are_not_folded_together():
    det = detail_of(campaigns=[camp_row("1"), camp_row("2")], campaign_assets=[
        link_row("1", "CALLOUT", "c1", asset={"calloutAsset": {"calloutText": "Same"}}),
        link_row("2", "CALLOUT", "c2", primary="NOT_ELIGIBLE", reasons=["ASSET_DISAPPROVED"], asset={"calloutAsset": {"calloutText": "Same"}})])
    lib = D.library([det], "CALLOUT", "all")
    assert sorted(x["st"] for x in lib["assets"]) == ["disapproved", "live"] and all("copies" not in x for x in lib["assets"])


def test_under_review_counts_as_live_in_a_library_as_it_does_in_the_audit():
    det = detail_of(campaigns=[camp_row("1")], campaign_assets=[link_row("1", "CALLOUT", "c1", asset={"calloutAsset": {"calloutText": "Fine"}}),
                                                                 link_row("1", "CALLOUT", "c2", primary="PENDING", reasons=["ASSET_UNDER_REVIEW"], asset={"calloutAsset": {"calloutText": "Waiting"}})])
    lib = D.library([det], "CALLOUT", "live")
    assert sorted(x["x"] for x in lib["assets"]) == ["Fine", "Waiting"] and lib["counts"]["live"] == 2 and lib["counts"]["pending"] == 1 and lib["counts"]["problem"] == 0
    assert [x["x"] for x in D.library([det], "CALLOUT", "pending")["assets"]] == ["Waiting"]


def test_a_library_is_capped_and_says_how_many_there_are():
    det = detail_of(campaigns=[camp_row("1")], campaign_assets=[link_row("1", "CALLOUT", "c%d" % i, asset={"calloutAsset": {"calloutText": "Callout %03d" % i}}) for i in range(30)])
    lib = D.library([det], "CALLOUT", "all", limit=10)
    assert lib["total"] == 30 and len(lib["assets"]) == 10 and lib["assets"][0]["x"] == "Callout 000"


def test_campaigns_named_under_an_asset_are_capped_but_counted():
    camps = [camp_row(str(i), "Camp %02d" % i) for i in range(1, 21)]
    det = detail_of(campaigns=camps, campaign_assets=[link_row(str(i), "CALLOUT", "same%d" % i, asset={"calloutAsset": {"calloutText": "Everywhere"}}) for i in range(1, 21)])
    x = D.library([det], "CALLOUT", "all")["assets"][0]
    assert x["copies"] == 20 and x["nc"] == 20 and len(x["camps"]) == D.CAMPAIGNS_NAMED


def test_an_mccs_library_names_each_assets_account_and_keeps_accounts_apart():
    one = detail_of(campaigns=[camp_row("1")], campaign_assets=[callout("1", "c1", "Open late")])
    two_s = sink({"id": "2222222222", "name": "Other Practice"})
    a.read_account({"id": "2222222222", "name": "Other Practice"}, search_fn=searcher(campaigns=[camp_row("1", "Theirs")], campaign_assets=[callout("1", "c1", "Open late")]), today=TODAY, sink=two_s)
    lib = D.library([one, two_s.to_dict()], "CALLOUT", "all")
    assert sorted(x["account"] for x in lib["assets"]) == ["Acme Dental", "Other Practice"] and lib["total"] == 2           # the same id in two accounts is two assets
    assert "account" not in D.library([one], "CALLOUT", "all")["assets"][0]
    assert {c["key"] for x in lib["assets"] for c in x["camps"]} == {"1234567890:1", "2222222222:1"}


def test_the_location_sets_are_a_kind_too():
    det = detail_of(campaigns=[camp_row("1")], campaign_sets=[{"campaign": {"id": "1"}, "assetSet": {"type": "LOCATION_SYNC", "name": "Main Street"}}],
                    customer_sets=[{"assetSet": {"type": "STATIC_LOCATION_GROUP", "name": "All offices"}}])
    lib = D.library([det], "LOCATION", "all")
    assert sorted(x["x"] for x in lib["assets"]) == ["All offices", "Main Street"] and lib["counts"]["all"] == 2
    assert [x["x"] for x in D.library([det], "LOCATION", "all", level="account")["assets"]] == ["All offices"]


def test_the_kinds_a_library_knows_are_the_audits_kinds():
    assert D.kind_fields(None) is None and D.kind_fields("") is None
    assert D.kind_fields("LOGO") == ("LOGO", "BUSINESS_LOGO") and D.kind_fields("SITELINK") == ("SITELINK",) and D.kind_fields("LOCATION") == ()
    with pytest.raises(KeyError):
        D.kind_fields("NOT_A_KIND")


# ── Asking for it ───────────────────────────────────────────────────────────

GROUP_ID = "mcc-9923720096"


def read_and_ask(monkeypatch, search, **kw):
    store = Store()
    monkeypatch.setattr(paid_store, "get_gads_cache", store.get_gads_cache)
    monkeypatch.setattr(paid_store, "put_gads_cache", store.put_gads_cache)
    return store


def ask_campaign(search, key="1234567890:5", store=None, account="1234567890", **kw):
    return a.campaign_detail_for(account, key, accounts_fn=lambda: [ACCT, {"id": "2222222222", "name": "Other Practice", "currency": "USD"}], groups_fn=lambda: [
        {"id": GROUP_ID, "name": "Group", "currency": "USD", "accountIds": ["1234567890", "2222222222"]}], search_fn=search, store=store or Store(), today=TODAY, **kw)


def test_a_campaigns_assets_come_from_the_read_the_report_saved_without_asking_google_again(monkeypatch):
    store = read_and_ask(monkeypatch, None)
    log = []
    search = searcher(campaigns=[camp_row("5", "Brand")], campaign_assets=[callout("5", "c1", "Open late")], log=log)
    from tracker import paid_geo
    paid_geo.clear_cache()
    base.reset_state()
    base.forget("")
    a.report_for("1234567890", accounts_fn=lambda: [dict(ACCT, timeZone="America/New_York")], groups_fn=lambda: [], search_fn=search, today=TODAY, store=paid_store)
    n = len(log)
    assert n and D.store_key("1234567890") in store.saved
    D.mem_clear()
    out = ask_campaign(search, store=paid_store)
    assert len(log) == n and out["campaign"] == "Brand" and out["type"] == "SEARCH"
    assert {x["key"]: x for x in out["sections"]}["CALLOUT"]["tiles"][0]["x"] == "Open late"


def test_a_campaign_is_found_inside_an_mcc_by_its_accounts_id():
    search = searcher(campaigns=[camp_row("5", "Brand")], campaign_assets=[callout("5", "c1", "Open late")])
    out = ask_campaign(search, key="2222222222:5", account=GROUP_ID)
    assert out["campaign"] == "Brand"


@pytest.mark.parametrize("key", ["", "5", "1234567890:", "1234567890:abc", "9999999999:5", "1234567890:99", None, "1234567890:5:6"])
def test_a_campaign_that_is_not_there_is_a_plain_error(key):
    with pytest.raises(a.BadRequest):
        ask_campaign(searcher(campaigns=[camp_row("5")]), key=key)


def test_an_account_that_is_not_position2s_cannot_have_its_assets_opened():
    with pytest.raises(a.BadRequest):
        ask_campaign(searcher(campaigns=[camp_row("5")]), account="9999999999")
    with pytest.raises(a.BadRequest):
        ask_campaign(searcher(campaigns=[camp_row("5")]), account=GROUP_ID, key="3333333333:5")                   # an account outside the MCC


def ask_library(search, kind=None, state="all", level=None, account="1234567890", store=None):
    return a.library_for(account, kind, state, level, accounts_fn=lambda: [ACCT, {"id": "2222222222", "name": "Other Practice", "currency": "USD"}], groups_fn=lambda: [
        {"id": GROUP_ID, "name": "Group", "currency": "USD", "accountIds": ["1234567890", "2222222222"]}], search_fn=search, store=store or Store(), today=TODAY)


def test_a_library_is_read_for_an_account_and_for_an_mcc_together():
    search = searcher(campaigns=[camp_row("5", "Brand")], campaign_assets=[callout("5", "c1", "Open late")])
    one = ask_library(search, "CALLOUT")
    assert one["label"] == "Callouts" and one["total"] == 1 and "account" not in one["assets"][0]
    both = ask_library(search, "CALLOUT", account=GROUP_ID)
    assert both["total"] == 2 and sorted(x["account"] for x in both["assets"]) == ["Acme Dental", "Other Practice"]
    assert ask_library(search, None)["label"] == "All assets" and ask_library(search, "LOCATION")["label"] == "Location"


@pytest.mark.parametrize("kind,state,level", [("NOPE", "all", None), ("SITELINK", "bogus", None), ("SITELINK", "all", "campaign"), ("PMAX_HEADLINE", "all", None)])
def test_a_library_asked_for_nonsense_is_refused(kind, state, level):
    with pytest.raises(a.BadRequest):
        ask_library(searcher(campaigns=[camp_row("5")]), kind, state, level)


# ── The routes ──────────────────────────────────────────────────────────────

def test_the_detail_routes_pass_what_was_asked_and_are_staff_only(monkeypatch):
    seen = []
    monkeypatch.setattr(a, "campaign_detail_for", lambda *x, **k: seen.append(("campaign", x, k)) or {"campaign": "Brand", "sections": []})
    monkeypatch.setattr(a, "library_for", lambda *x, **k: seen.append(("library", x, k)) or {"assets": [], "total": 0})
    c = client_as(STAFF)
    assert c.get(API + "/campaign?account=1234567890&campaign=1234567890:5").get_json() == {"campaign": "Brand", "sections": []}
    assert seen[0] == ("campaign", ("1234567890", "1234567890:5"), {})
    assert c.get(API + "/library?account=1234567890&kind=SITELINK&state=problem&level=account").status_code == 200
    assert seen[1] == ("library", ("1234567890", "SITELINK", "problem", "account"), {})
    c.get(API + "/library?account=1234567890")
    assert seen[2] == ("library", ("1234567890", None, "all", None), {})
    for path in ("/campaign?account=1&campaign=1:2", "/library?account=1"):
        assert client_as(None).get(API + path).status_code == 302 and client_as("someone@gmail.com").get(API + path).status_code == 302


@pytest.mark.parametrize("route,fn", [("/campaign?account=1234567890&campaign=1234567890:5", "campaign_detail_for"), ("/library?account=1234567890", "library_for")])
@pytest.mark.parametrize("err, status", [
    (gads.GadsError("not_configured", "not set"), 503), (gads.GadsError("quota", "slow down"), 429), (gads.GadsError("auth", "revoked"), 502),
    (gads.GadsError("api", "google broke"), 502), (a.BadRequest("That campaign is not in this account."), 400)])
def test_detail_failures_reach_the_page_with_a_status_and_a_reason(monkeypatch, route, fn, err, status):
    def boom(*x, **k):
        raise err
    monkeypatch.setattr(a, fn, boom)
    r = client_as(STAFF).get(API + route)
    assert r.status_code == status and str(err) in r.get_json()["error"]


def test_the_page_script_and_styles_are_in_the_page():
    page = client_as(STAFF).get("/p2/paid-marketing/asset-coverage").data.decode()
    assert page.index("asset_tiles.js") < page.index("asset_coverage.js") and 'id="acDrawer"' in page and 'id="acZoom"' in page


def test_there_is_no_em_dash_in_the_detail():
    for p in ("tracker/paid_assets_detail.py", "static/js/asset_tiles.js"):
        assert "—" not in open(os.path.join(ROOT, p), encoding="utf-8").read(), p
