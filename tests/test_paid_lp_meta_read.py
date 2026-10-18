"""Landing Page & Relevancy Check: what Meta says about each landing page (tracker/paid_lp_meta_read.py)."""
import json

import pytest

from tracker import paid_meta as meta
from tracker import paid_lp_meta_read as r


def link_ad(aid, url, name="Free quote", message="Save 20% this week", **creative):
    c = {"object_story_spec": {"page_id": "1", "link_data": {"link": url, "name": name, "message": message, "description": "Call today"}}}
    c.update(creative)
    return {"id": aid, "name": "Ad " + aid, "adset_id": "s1", "campaign_id": "c1", "effective_status": "ACTIVE", "creative": c}


def ins(aid, spend=10.0, clicks=5, impressions=100, adset="s1", objective="OUTCOME_LEADS", leads=1, **over):
    row = {"ad_id": aid, "ad_name": "Ad " + aid, "adset_id": adset, "adset_name": "Set " + adset, "campaign_id": "c1", "campaign_name": "Camp", "objective": objective,
           "spend": str(spend), "impressions": str(impressions), "inline_link_clicks": str(clicks), "actions": [{"action_type": "lead", "value": str(leads)}]}
    row.update(over)
    return row


# ── The creative ────────────────────────────────────────────────────────────

def test_the_address_is_found_wherever_meta_keeps_it():
    assert r.creative_urls(link_ad("1", "https://a.test/x")["creative"]) == (["https://a.test/x"], False)
    video = {"object_story_spec": {"video_data": {"call_to_action": {"value": {"link": "https://a.test/v"}}}}}
    assert r.creative_urls(video)[0] == ["https://a.test/v"]
    cta = {"object_story_spec": {"link_data": {"link": "https://a.test/a", "call_to_action": {"value": {"link": "https://a.test/b"}}}}}
    assert r.creative_urls(cta)[0] == ["https://a.test/a", "https://a.test/b"]
    carousel = {"object_story_spec": {"link_data": {"link": "https://a.test/a", "child_attachments": [{"link": "https://a.test/c1"}, {"link": "https://a.test/c2", "call_to_action": {"value": {"link": "https://a.test/c3"}}}]}}}
    assert r.creative_urls(carousel)[0] == ["https://a.test/a", "https://a.test/c1", "https://a.test/c2", "https://a.test/c3"]
    dynamic = {"asset_feed_spec": {"link_urls": [{"website_url": "https://a.test/d1"}, {"website_url": "https://a.test/d2"}]}}
    assert r.creative_urls(dynamic)[0] == ["https://a.test/d1", "https://a.test/d2"]
    assert r.creative_urls({"object_url": "https://a.test/o", "template_url": "https://a.test/t"})[0] == ["https://a.test/o", "https://a.test/t"]
    assert r.creative_urls({"object_story_spec": {"template_data": {"link": "https://a.test/tpl", "child_attachments": [{"link": "https://a.test/tc"}]}}})[0] == ["https://a.test/tpl", "https://a.test/tc"]


def test_an_address_on_facebook_or_instagram_is_not_a_landing_page_but_is_remembered_as_where_the_ad_goes():
    c = {"object_story_spec": {"link_data": {"link": "https://www.facebook.com/acme/posts/1"}}}
    assert r.creative_urls(c) == ([], True)
    mixed = {"object_story_spec": {"link_data": {"link": "https://a.test/x", "call_to_action": {"value": {"link": "https://m.me/acme"}}}}}
    assert r.creative_urls(mixed) == (["https://a.test/x"], True)
    for host in ("https://l.instagram.com/x", "https://wa.me/1555", "https://fb.me/x", "https://messenger.com/t/1"):
        assert r.creative_urls({"object_url": host}) == ([], True)
    assert r.creative_urls({"object_url": "https://notfacebook.com/x"}) == (["https://notfacebook.com/x"], False)       # a lookalike name is a real site


def test_an_address_that_is_not_a_web_address_or_is_repeated_or_too_many_is_dropped():
    c = {"object_story_spec": {"link_data": {"link": "ftp://a.test/x", "call_to_action": {"value": {"link": "https://a.test/x"}}}}, "object_url": "https://a.test/x"}
    assert r.creative_urls(c) == (["https://a.test/x"], False)
    assert r.creative_urls({"object_url": 5, "template_url": None}) == ([], False)
    many = {"asset_feed_spec": {"link_urls": [{"website_url": "https://a.test/%d" % i} for i in range(9)]}}
    assert len(r.creative_urls(many)[0]) == r.MAX_URLS_PER_AD
    assert r.creative_urls(None) == ([], False) and r.creative_urls("x") == ([], False)


def test_the_headline_and_the_words_around_it_are_read_from_every_kind_of_creative():
    heads, descs = r.creative_text(link_ad("1", "https://a.test/x", name="Free quote", message="Save 20% this week")["creative"])
    assert heads == ["Free quote"] and descs == ["Save 20% this week", "Call today"]
    video = {"object_story_spec": {"video_data": {"title": "Watch", "message": "Primary", "link_description": "More"}}}
    assert r.creative_text(video) == (["Watch"], ["Primary", "More"])
    dyn = {"asset_feed_spec": {"titles": [{"text": "T1"}, {"text": "T2"}], "bodies": [{"text": "B1"}], "descriptions": [{"text": "D1"}]}, "title": "Own", "body": "Body"}
    heads, descs = r.creative_text(dyn)
    assert heads == ["Own", "T1", "T2"] and descs == ["Body", "B1", "D1"]
    card = {"object_story_spec": {"link_data": {"child_attachments": [{"name": "Card one", "description": "Card text"}]}}}
    assert r.creative_text(card) == (["Card one"], ["Card text"])


def test_the_words_are_tidied_and_limited():
    c = {"object_story_spec": {"link_data": {"name": "  A   b\n c ", "message": "x"}}, "asset_feed_spec": {"titles": [{"text": "A b c"}] + [{"text": "H%d" % i} for i in range(30)],
                                                                                                    "bodies": [{"text": "D%d" % i} for i in range(9)]}}
    heads, descs = r.creative_text(c)
    assert heads[0] == "A b c" and heads.count("A b c") == 1 and len(heads) == r.lp.MAX_HEADLINES
    assert len(descs) == r.lp.MAX_DESCRIPTIONS
    assert r.creative_text(None) == ([], []) and r.creative_text({"title": 7, "asset_feed_spec": {"titles": [None, {"text": None}]}}) == ([], [])


def test_the_kind_of_creative():
    assert r.creative_kind({"asset_feed_spec": {"titles": []}, "object_story_spec": {"link_data": {}}}) == "DYNAMIC"
    assert r.creative_kind({"object_story_spec": {"link_data": {"child_attachments": [{}]}}}) == "CAROUSEL"
    assert r.creative_kind({"object_story_spec": {"video_data": {}}}) == "VIDEO"
    assert r.creative_kind({"object_story_spec": {"link_data": {}}}) == "LINK"
    assert r.creative_kind({"effective_object_story_id": "1_2"}) == "POST"
    assert r.creative_kind({}) == "" and r.creative_kind(None) == ""


def test_an_ad_that_only_shows_an_existing_post_is_marked_as_one():
    got = r.parse_ads([{"id": "1", "creative": {"effective_object_story_id": "1_2"}}, link_ad("2", "https://a.test/x"),
                       {"id": "3", "creative": {"effective_object_story_id": "1_3", "object_story_spec": {"link_data": {"link": "https://a.test/y"}}}}, {"name": "no id"}])
    assert got["1"]["post"] is True and got["1"]["urls"] == []
    assert got["2"]["post"] is False and got["2"]["urls"] == ["https://a.test/x"]
    assert got["3"]["post"] is False                                    # it has a link of its own
    assert set(got) == {"1", "2", "3"}


# ── The numbers ─────────────────────────────────────────────────────────────

def test_an_ad_that_spent_nothing_is_left_out_and_results_follow_the_objective():
    got = r.parse_insights([ins("1", 12.5, 6, 200, leads=2), ins("2", 0, 0, 0), ins("3", 9, 3, 90, objective="OUTCOME_TRAFFIC"), {"spend": "5"}])
    assert set(got) == {"1", "3"}
    assert got["1"]["cost"] == 12.5 and got["1"]["clicks"] == 6 and got["1"]["impressions"] == 200 and got["1"]["conversions"] == 2.0 and got["1"]["kind"] == "LEADS"
    assert got["1"]["adSet"] == "s1" and got["1"]["adSetName"] == "Set s1" and got["1"]["campaignName"] == "Camp"
    assert got["3"]["conversions"] == 0.0 and got["3"]["kind"] == "TRAFFIC"                        # a traffic campaign has no result to count


def test_a_sale_is_the_largest_of_the_names_meta_gives_a_purchase():
    row = ins("1", 50, objective="OUTCOME_SALES", actions=[{"action_type": "omni_purchase", "value": "0"}, {"action_type": "onsite_web_purchase", "value": "4"}],
              action_values=[{"action_type": "onsite_web_purchase", "value": "400"}])
    got = r.parse_insights([row])["1"]
    assert got["conversions"] == 4.0 and got["value"] == 400.0


def test_the_device_split_groups_meta_s_devices_into_phone_desktop_and_tablet():
    rows = [ins("1", 5, 3, impression_device="iphone"), ins("1", 4, 2, impression_device="android_smartphone"), ins("1", 3, 1, impression_device="desktop"),
            ins("1", 2, 1, impression_device="ipad"), ins("1", 1, 1, impression_device="something new"), ins("1", 1, 1), {"impression_device": "desktop"}]
    got = r.parse_devices(rows)["1"]
    assert got["mobile"] == {"clicks": 5, "cost": 9.0, "conversions": 2.0}
    assert got["desktop"]["clicks"] == 1 and got["tablet"]["clicks"] == 1 and got["other"]["clicks"] == 2


def test_the_rankings_are_above_average_or_below_and_none_when_meta_gave_none():
    got = r.parse_rankings([{"ad_id": "1", "quality_ranking": "ABOVE_AVERAGE", "engagement_rate_ranking": "BELOW_AVERAGE_35", "conversion_rate_ranking": "AVERAGE"},
                            {"ad_id": "2", "quality_ranking": "UNKNOWN", "conversion_rate_ranking": "BELOW_AVERAGE_10"}, {"quality_ranking": "AVERAGE"}])
    assert got["1"] == {"quality": "above", "engagement": "below", "conversion": "average"}
    assert got["2"] == {"quality": None, "engagement": None, "conversion": "below"} and len(got) == 2


def test_where_an_ad_set_sends_people_when_it_has_no_web_address():
    got = r.parse_adset_kinds([{"id": "1", "destination_type": "ON_AD"}, {"id": "2", "destination_type": "WHATSAPP"}, {"id": "3", "destination_type": "WEBSITE"},
                               {"id": "4", "destination_type": "PHONE_CALL"}, {"destination_type": "ON_AD"}])
    assert got == {"1": "an instant form", "2": "a message thread", "3": "", "4": "a phone call"}


# ── Joining ─────────────────────────────────────────────────────────────────

def built(ads, stats, **kw):
    return r.build(r.parse_insights(stats), r.parse_ads(ads), **kw)


def test_pages_are_one_row_per_page_with_the_ads_that_reach_them():
    ads = [link_ad("1", "https://a.test/x?utm=1"), link_ad("2", "https://www.a.test/x/"), link_ad("3", "https://a.test/y")]
    pages, groups, count, cost, off = built(ads, [ins("1", 30, 10, 300, leads=3), ins("2", 10, 4, 120, leads=1), ins("3", 5, 2, 50, leads=0)])
    assert [p["id"] for p in pages] == ["a.test/x", "a.test/y"] and count == 2 and cost == 45.0
    x = pages[0]
    assert x["cost"] == 40.0 and x["clicks"] == 14 and x["impressions"] == 420 and x["conversions"] == 4.0 and x["platforms"] == ["meta"] and x["speed"] is None
    assert x["url"] == "https://a.test/x?utm=1" and x["adGroups"] == ["meta:s1"]                  # the address shown is the variant that spent most
    g = groups["meta:s1"]
    assert g["platform"] == "meta" and g["campaignType"] == "META_LEADS" and g["cost"] == 45.0 and g["keywords"] == [] and g["terms"] == []
    assert [a["name"] for a in g["ads"]] == ["Ad 1", "Ad 2", "Ad 3"] and g["ads"][0]["headlines"] == ["Free quote"]
    assert off == {"spend": 0.0, "ads": 0, "kinds": []}


def test_an_ad_with_two_pages_splits_its_spend_between_them():
    ads = [link_ad("1", "https://a.test/x", asset_feed_spec={"link_urls": [{"website_url": "https://a.test/x"}, {"website_url": "https://a.test/y"}]})]
    pages, groups, *_ = built(ads, [ins("1", 20, 10, 200, leads=2)])
    assert {p["id"]: (p["cost"], p["clicks"], p["conversions"]) for p in pages} == {"a.test/x": (10.0, 5, 1.0), "a.test/y": (10.0, 5, 1.0)}
    assert groups["meta:s1"]["cost"] == 20.0                                    # the ad set's own spend is not split


def test_the_device_split_follows_the_ad_s_pages():
    pages, *_ = built([link_ad("1", "https://a.test/x")], [ins("1", 20, 10, 200)], devices={"1": {"mobile": {"clicks": 8, "cost": 15.0, "conversions": 1.0}, "desktop": {"clicks": 2, "cost": 5.0, "conversions": 0.0}}})
    assert pages[0]["devices"] == {"mobile": {"clicks": 8, "cost": 15.0, "conversions": 1.0}, "desktop": {"clicks": 2, "cost": 5.0, "conversions": 0.0}}


def test_spend_that_reaches_no_web_page_is_counted_and_named_not_guessed():
    ads = [link_ad("1", "https://a.test/x"), {"id": "2", "adset_id": "s2", "creative": {"object_story_spec": {"link_data": {"link": "https://facebook.com/acme"}}}},
           {"id": "3", "adset_id": "s3", "creative": {"effective_object_story_id": "9_9"}}, {"id": "4", "adset_id": "s4", "creative": {"object_story_spec": {"link_data": {"message": "hi"}}}},
           {"id": "5", "adset_id": "s5", "creative": {"object_story_spec": {"link_data": {"message": "hi"}}}}]
    stats = [ins("1", 50), ins("2", 7, adset="s2"), ins("3", 6, adset="s3"), ins("4", 5, adset="s4"), ins("5", 3, adset="s5"), ins("6", 2, adset="s6")]
    pages, groups, count, cost, off = built(ads, stats, kinds={"s4": "an instant form"})
    assert [p["id"] for p in pages] == ["a.test/x"] and cost == 50.0
    kinds = {k["kind"]: (k["spend"], k["ads"]) for k in off["kinds"]}
    assert kinds == {r.KIND_META: (7.0, 1), r.KIND_POST: (6.0, 1), "an instant form": (5.0, 1), r.KIND_UNKNOWN: (3.0, 1), r.KIND_GONE: (2.0, 1)}
    assert off["spend"] == 23.0 and off["ads"] == 5
    assert set(groups) == {"meta:s1"}                                    # an ad set that reaches no page is not a page's ad set


def test_ad_sets_are_named_apart_from_google_ad_groups_and_keep_the_ads_that_spent_most():
    ads = [link_ad(str(i), "https://a.test/x") for i in range(1, 9)]
    stats = [ins(str(i), 100 - i) for i in range(1, 9)]
    _pages, groups, *_ = built(ads, stats)
    g = groups["meta:s1"]
    assert len(g["ads"]) == r.lp.MAX_ADS and [a["name"] for a in g["ads"]][:2] == ["Ad 1", "Ad 2"] and g["cost"] == round(sum(100 - i for i in range(1, 9)), 2)


def test_each_ad_carries_metas_ratings_so_a_page_can_be_judged_on_them():
    pages, groups, *_ = built([link_ad("1", "https://a.test/x")], [ins("1")], ranks={"1": {"quality": "below", "engagement": None, "conversion": "above"}})
    assert groups["meta:s1"]["ads"][0]["ranks"] == {"quality": "below", "engagement": None, "conversion": "above"}
    _p, groups2, *_ = built([link_ad("1", "https://a.test/x")], [ins("1")])
    assert groups2["meta:s1"]["ads"][0]["ranks"] == {}


def test_only_the_busiest_pages_are_kept_and_the_count_says_how_many_there_were():
    ads = [link_ad(str(i), "https://a.test/p%d" % i) for i in range(1, 6)]
    pages, groups, count, cost, _off = built(ads, [ins(str(i), 10 * i, adset="s%d" % i) for i in range(1, 6)], keep=2)
    assert [p["id"] for p in pages] == ["a.test/p5", "a.test/p4"] and count == 5 and cost == 150.0
    assert set(groups) == {"meta:s5", "meta:s4"}


def test_a_templated_address_is_marked_so_it_is_not_fetched():
    pages, *_ = built([link_ad("1", "https://{{site}}.a.test/x")], [ins("1")])
    assert pages[0]["templated"] is True


# ── The whole read ──────────────────────────────────────────────────────────

class Graph:
    """A Graph API double: answers by the path's last part, records the calls, and fails the parts it is told to."""

    def __init__(self, fail=(), ads=None, stats=None, over=None):
        self.calls, self.fail, self.over = [], set(fail), over
        self.ads = ads if ads is not None else [link_ad("1", "https://a.test/x")]
        self.stats = stats if stats is not None else [ins("1", 20, 10, 200, leads=2)]

    def pages(self, path, params):
        self.calls.append((path, dict(params)))
        kind = path.rsplit("/", 1)[-1]
        if kind == "insights":
            kind = "devices" if params.get("breakdowns") else "rankings" if "quality_ranking" in params["fields"] else "insights"
        if kind in self.fail:
            raise meta.AdsError("Meta answered 400: (#100) bad field")
        if kind == "ads" and self.over and params["limit"] > self.over:
            raise meta.AdsError("Meta answered 500: reduce the amount of data")
        if kind == "OVER":
            raise meta.OverQuota("wait")
        return {"ads": self.ads, "insights": self.stats, "devices": [dict(s, impression_device="iphone") for s in self.stats],
                "rankings": [{"ad_id": "1", "quality_ranking": "AVERAGE", "engagement_rate_ranking": "AVERAGE", "conversion_rate_ranking": "BELOW_AVERAGE_20"}],
                "adsets": [{"id": "s1", "destination_type": "WEBSITE"}]}[kind]


def run(g, **kw):
    return r.read_account("act_1234", pages=g.pages, **kw)


def test_the_whole_read_gives_the_google_shape_and_names_what_it_asked_for():
    g = Graph()
    out = run(g)
    assert [p["id"] for p in out["pages"]] == ["a.test/x"] and out["pageCount"] == 1 and out["pageCost"] == 20.0 and out["warnings"] == []
    assert out["ok"] == {"ads": True, "insights": True, "devices": True, "rankings": True, "adSets": True}
    assert out["pages"][0]["devices"]["mobile"]["clicks"] == 10
    assert out["adGroups"]["meta:s1"]["ads"][0]["ranks"]["conversion"] == "below"
    assert out["off"] == {"spend": 0.0, "ads": 0, "kinds": [], "total": 20.0}
    paths = [c[0] for c in g.calls]
    assert paths == ["act_1234/ads", "act_1234/insights", "act_1234/insights", "act_1234/insights", "act_1234/adsets"]
    ads = g.calls[0][1]
    assert json.loads(ads["effective_status"]) == r.AD_STATUSES and "creative{" in ads["fields"]
    assert all(c[1].get("date_preset") == "last_30d" and c[1].get("level") == "ad" for c in g.calls[1:4])
    assert g.calls[2][1]["breakdowns"] == "impression_device"


def test_a_failure_of_the_ads_or_the_insights_is_raised_because_there_is_no_page_without_them():
    for part in ("ads", "insights"):
        with pytest.raises(meta.AdsError):
            run(Graph(fail=[part]))


def test_a_failure_of_any_other_part_is_a_warning_and_the_rest_is_kept():
    out = run(Graph(fail=["devices", "rankings", "adsets"]))
    assert out["ok"] == {"ads": True, "insights": True, "devices": False, "rankings": False, "adSets": False}
    assert len(out["warnings"]) == 3 and out["warnings"][0].startswith("The split by device could not be read (Meta answered 400")
    assert out["pages"][0]["devices"] == {} and out["adGroups"]["meta:s1"]["ads"][0]["ranks"] == {}


def test_without_the_ad_set_read_an_ad_with_no_address_is_somewhere_that_could_not_be_read():
    ads = [{"id": "1", "adset_id": "s1", "creative": {"object_story_spec": {"link_data": {"message": "hi"}}}}]
    out = run(Graph(ads=ads, fail=["adsets"]))
    assert out["pages"] == [] and out["off"]["kinds"][0]["kind"] == r.KIND_UNKNOWN and out["off"]["spend"] == 20.0 and out["off"]["total"] == 20.0


def test_meta_asking_to_wait_stops_the_read_and_is_raised_even_from_an_optional_part():
    class Slow(Graph):
        def pages(self, path, params):
            if params.get("breakdowns"):
                raise meta.OverQuota("wait")
            return super().pages(path, params)
    with pytest.raises(meta.OverQuota):
        run(Slow())


def test_a_creative_answer_that_is_too_large_is_asked_again_in_smaller_pages():
    g = Graph(over=50)
    out = run(g)
    assert out["pages"] and [c[1]["limit"] for c in g.calls if c[0].endswith("/ads")] == [r.ADS_PAGE, r.ADS_PAGE_SMALL]


def test_an_account_with_no_spend_has_no_pages_and_no_error():
    out = run(Graph(stats=[], ads=[]))
    assert out["pages"] == [] and out["pageCount"] == 0 and out["off"]["total"] == 0.0 and out["warnings"] == []


def test_the_read_says_when_it_was_made():
    import datetime as dt
    now = dt.datetime(2026, 10, 18, 9, 0, tzinfo=dt.timezone.utc)
    assert run(Graph(), now=now)["readAt"] == now.isoformat()


# ── Pinned by mutation: the details a first test left loose ─────────────────

def test_a_carousel_made_from_a_template_is_a_carousel_and_a_story_with_only_a_page_is_not_a_post_kind():
    assert r.creative_kind({"object_story_spec": {"template_data": {"child_attachments": [{}]}}}) == "CAROUSEL"
    assert r.creative_kind({"effective_object_story_id": "1_2", "object_story_spec": {"page_id": "1"}}) == ""


def test_a_creative_with_a_dynamic_feed_or_a_spec_is_not_just_a_shown_post():
    assert r.parse_ads([{"id": "1", "creative": {"effective_object_story_id": "1_2", "asset_feed_spec": {"link_urls": []}}}])["1"]["post"] is False
    assert r.parse_ads([{"id": "1", "creative": {"effective_object_story_id": "1_2", "object_story_spec": {"page_id": "1"}}}])["1"]["post"] is False
    assert r.parse_ads([{"id": "1", "creative": {"effective_object_story_id": "1_2"}}])["1"]["post"] is True


def test_the_kind_of_destination_is_read_in_any_case():
    assert r.parse_adset_kinds([{"id": "1", "destination_type": "on_ad"}]) == {"1": "an instant form"}


def test_an_ad_with_an_address_on_facebook_and_a_shown_post_is_counted_as_facebook_itself():
    ads = {"id": "1", "adset_id": "s1", "creative": {"effective_object_story_id": "9_9", "object_url": "https://facebook.com/acme/posts/1"}}
    _p, _g, _c, _cost, off = built([ads], [ins("1", 5)])
    assert [k["kind"] for k in off["kinds"]] == [r.KIND_META]


def test_two_ads_that_go_the_same_place_are_two_ads_and_the_kinds_are_listed_biggest_spend_first():
    ads = [{"id": str(i), "adset_id": "s%d" % i, "creative": {"effective_object_story_id": "9_%d" % i}} for i in range(1, 5)]
    ads += [{"id": "5", "adset_id": "s5", "creative": {"object_story_spec": {"link_data": {"message": "hi"}}}}]
    _p, _g, _c, _cost, off = built(ads, [ins("1", 5, adset="s1"), ins("2", 6, adset="s2"), ins("3", 7, adset="s3"), ins("5", 40, adset="s5")], kinds={"s5": "a phone call"})
    assert [(k["kind"], k["spend"], k["ads"]) for k in off["kinds"]] == [("a phone call", 40.0, 1), (r.KIND_POST, 18.0, 3)]
    assert off["ads"] == 4 and off["spend"] == 58.0


def test_the_address_shown_is_the_one_that_took_the_biggest_share_even_when_a_smaller_ad_comes_later():
    many = link_ad("1", "https://a.test/x?utm=a", asset_feed_spec={"link_urls": [{"website_url": "https://a.test/x?utm=a"}, {"website_url": "https://a.test/y"}, {"website_url": "https://a.test/z"}]})
    small = link_ad("2", "https://a.test/x?utm=b")
    pages, *_ = built([many, small], [ins("1", 30), ins("2", 12)])
    x = next(p for p in pages if p["id"] == "a.test/x")
    assert x["url"] == "https://a.test/x?utm=b" and x["cost"] == 22.0


def test_everything_that_belongs_to_a_split_ad_is_split_and_nothing_private_is_left_on_a_page():
    ad = link_ad("1", "https://a.test/x", asset_feed_spec={"link_urls": [{"website_url": "https://a.test/x"}, {"website_url": "https://a.test/y"}]})
    row = ins("1", 20, 10, 200, objective="OUTCOME_SALES", actions=[{"action_type": "omni_purchase", "value": "4"}], action_values=[{"action_type": "omni_purchase", "value": "400"}])
    dev = {"1": {"mobile": {"clicks": 8, "cost": 15.0, "conversions": 2.0}}}
    pages, *_ = built([ad], [row], devices=dev)
    for p in pages:
        assert p["impressions"] == 100 and p["clicks"] == 5 and p["cost"] == 10.0 and p["conversions"] == 2.0 and p["value"] == 200.0
        assert p["devices"] == {"mobile": {"clicks": 4, "cost": 7.5, "conversions": 1.0}}
        assert not any(k.startswith("_") for k in p)


def test_a_group_counts_every_click_and_result_of_its_ads_and_an_ad_lists_one_address_per_page():
    ad1 = link_ad("1", "https://a.test/x?utm=1", asset_feed_spec={"link_urls": [{"website_url": "https://a.test/x?utm=1"}, {"website_url": "https://www.a.test/x"}]})
    _p, groups, *_ = built([ad1, link_ad("2", "https://a.test/x")], [ins("1", 20, 10, 200, leads=3), ins("2", 5, 4, 50, leads=1)])
    g = groups["meta:s1"]
    assert g["clicks"] == 14 and g["conversions"] == 4.0
    assert g["ads"][0]["urls"] == ["https://a.test/x?utm=1"]                                          # the same page twice is one address, the first


def test_the_device_names_are_read_in_any_case():
    got = r.parse_devices([ins("1", 5, 3, impression_device="IPhone")])
    assert list(got["1"]) == ["mobile"]
