"""The Meta side of Ad Strength and Asset Coverage (tracker/paid_meta_creative.py, paid_meta_creative_analyse.py). No network: Meta's answers are written out in the shapes the
Graph API documents (nested ad set / campaign / creative on an ad, ranking words on an insights row), and the first live read is checked against the real API by the admin query check."""
import datetime as dt
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gads_fakes import FakeStore  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_geo  # noqa: E402
from tracker import paid_meta_creative as c  # noqa: E402
from tracker import paid_meta_creative_analyse as an  # noqa: E402

TODAY = dt.date(2026, 10, 21)
ACCT = {"id": "act_1", "name": "Acme", "currency": "USD", "platform": "meta"}


def ad(i="1", status="ACTIVE", adset="11", campaign="21", creative=None, objective="OUTCOME_LEADS", name=None):
    return {"id": i, "name": name or "Ad " + i, "effective_status": status, "adset_id": adset, "campaign_id": campaign, "adset": {"id": adset, "name": "Set " + adset},
            "campaign": {"id": campaign, "name": "Camp " + campaign, "objective": objective}, "creative": creative if creative is not None else {"object_type": "SHARE", "image_hash": "h"}}


def ins(i="1", spend="100", imp="2000", freq="1.5", q=None, e=None, v=None, leads=None, objective="OUTCOME_LEADS", adset="11"):
    row = {"ad_id": i, "ad_name": "Ad " + i, "adset_id": adset, "adset_name": "Set " + adset, "campaign_id": "21", "campaign_name": "Camp 21", "objective": objective, "spend": spend,
           "impressions": imp, "reach": "900", "frequency": freq, "inline_link_clicks": "30"}
    for k, val in (("quality_ranking", q), ("engagement_rate_ranking", e), ("conversion_rate_ranking", v)):
        if val:
            row[k] = val
    if leads is not None:
        row["actions"] = [{"action_type": "lead", "value": str(leads)}]
    return row


def raw(ads, rows, insights_read=True, warnings=None):
    return {"ads": c.read_ads(ads), "insights": c.read_insights(rows), "warnings": warnings or [], "window": {"from": "2026-09-21", "to": "2026-10-20", "days": 30}, "insightsRead": insights_read}


# ── Reading ──────────────────────────────────────────────────────────────────

def test_a_creative_is_a_video_a_carousel_or_an_image_and_a_flexible_one_is_marked():
    f = c.creative_format
    assert f({"video_id": "9"}) == ("video", False, 0) and f({"object_type": "VIDEO"})[0] == "video"
    assert f({"object_story_spec": {"link_data": {"child_attachments": [{}, {}]}}})[0] == "carousel"
    assert f({"object_story_spec": {"link_data": {"child_attachments": [{}]}}, "image_hash": "h"})[0] == "image"        # one card is not a carousel
    assert f({"image_hash": "h"})[0] == "image" and f({"object_type": "SHARE"})[0] == "image" and f({})[0] == "other" and f(None)[0] == "other"
    fmt, dynamic, texts = f({"asset_feed_spec": {"titles": [{}, {}], "bodies": [{}, {}, {}], "images": [{}, {}]}})
    assert (fmt, dynamic, texts) == ("image", True, 5)
    assert f({"asset_feed_spec": {"videos": [{}], "titles": [{}]}})[0] == "video" and f({"asset_feed_spec": {"images": [{}], "titles": [{}]}})[1] is False


def test_an_ad_row_becomes_an_item_with_its_ad_set_campaign_and_objective_and_a_row_with_no_id_is_dropped():
    out = c.read_ads([ad("1", creative={"video_id": "v"}), {"name": "no id"}])
    assert len(out) == 1 and out[0]["adset"] == "Set 11" and out[0]["campaign"] == "Camp 21" and out[0]["objective"] == "OUTCOME_LEADS" and out[0]["format"] == "video"


def test_an_insights_row_keeps_the_numbers_the_rankings_and_the_result_of_the_objective():
    r = c.read_insights([ins("1", spend="123.456", imp="2500", freq="2.349", q="BELOW_AVERAGE_20", e="AVERAGE", v="UNKNOWN", leads=5), {"ad_name": "no id"}])
    assert list(r) == ["1"]
    one = r["1"]
    assert one["spend"] == 123.46 and one["impressions"] == 2500 and one["frequency"] == 2.35 and one["results"] == 5 and one["objective"] == "LEADS"
    assert one["rank"] == {"quality": "BELOW_AVERAGE_20", "engagement": "AVERAGE", "conversion": None}                       # UNKNOWN is not a ranking


def test_a_word_meta_never_sent_is_not_turned_into_a_ranking():
    assert c.read_insights([ins("1", q="SPECTACULAR")])["1"]["rank"]["quality"] is None


def test_the_requests_ask_for_live_and_stopped_ads_and_one_row_per_ad():
    path, p = c.ads_request("act_9")
    assert path == "/act_9/ads" and json.loads(p["effective_status"]) == ["ACTIVE", "DISAPPROVED", "WITH_ISSUES"] and "asset_feed_spec" in p["fields"]
    path, p = c.insights_request("act_9", "2026-09-21", "2026-10-20")
    assert path == "/act_9/insights" and p["level"] == "ad" and json.loads(p["time_range"]) == {"since": "2026-09-21", "until": "2026-10-20"}
    assert all(k in p["fields"] for k in ("quality_ranking", "engagement_rate_ranking", "conversion_rate_ranking", "frequency", "actions"))


def test_the_numbers_failing_does_not_fail_the_read_it_is_said():
    def pages(path, params):
        if path.endswith("/insights"):
            raise c.meta.AdsError("Meta answered 400: bad field")
        return [ad("1")]
    r = c.raw_read("act_1", pages_fn=pages, today=TODAY)
    assert len(r["ads"]) == 1 and r["insights"] == {} and r["insightsRead"] is False and "could not be read (Meta answered 400: bad field)" in r["warnings"][0]


def test_the_ads_failing_is_the_error_not_an_empty_report():
    def pages(path, params):
        raise c.meta.AdsError("Meta rejected the access token")
    with pytest.raises(c.meta.AdsError):
        c.raw_read("act_1", pages_fn=pages, today=TODAY)


def test_the_window_is_the_30_days_before_today():
    assert c.window(TODAY) == (dt.date(2026, 9, 21), dt.date(2026, 10, 20))


# ── Quality ──────────────────────────────────────────────────────────────────

def test_an_ad_below_average_is_flagged_by_how_far_below_and_the_dearest_comes_first():
    r = raw([ad("1"), ad("2"), ad("3"), ad("4")], [ins("1", spend="500", q="BELOW_AVERAGE_10"), ins("2", spend="900", e="BELOW_AVERAGE_35"), ins("3", spend="50", q="ABOVE_AVERAGE", e="AVERAGE"),
                                                 ins("4", spend="300", v="BELOW_AVERAGE_20")])
    q = an.quality(r, ACCT, TODAY)
    s = q["summary"]
    assert s["ads"] == 4 and s["ranked"] == 4 and s["belowAverage"] == 3 and s["spendBelow"] == 1700 and s["shareBelow"] == round(1700 / 1750, 4)
    assert [i["id"] for i in q["fixFirst"]] == ["1", "4", "2"]                                  # high, then medium, then low
    assert [i["severity"] for i in q["fixFirst"]] == ["high", "medium", "low"]
    assert q["fixFirst"][0]["flags"][0]["code"] == "rank_quality" and "bottom 10%" in q["fixFirst"][0]["flags"][0]["text"]
    assert "landing page" in q["fixFirst"][1]["next"] and "hook" in q["fixFirst"][2]["next"] and "image or video" in q["fixFirst"][0]["next"]
    assert q["headline"] == "3 of 4 ranked ads are below average on at least one of Meta's rankings, and they took 97% of the spend."


def test_an_ad_under_500_impressions_is_not_ranked_and_never_called_below_average():
    q = an.quality(raw([ad("1")], [ins("1", imp="300", q="BELOW_AVERAGE_10")]), ACCT, TODAY)
    assert q["summary"]["ranked"] == 0 and q["summary"]["notRanked"] == 1 and q["summary"]["belowAverage"] == 0
    assert q["headline"] == "None of the 1 delivering ads has had the 500 impressions Meta needs to rank it yet."


def test_a_worn_out_ad_is_flagged_by_its_frequency_and_a_small_one_is_not():
    q = an.quality(raw([ad("1"), ad("2"), ad("3")], [ins("1", freq="6.4"), ins("2", freq="4.2"), ins("3", freq="9", imp="400")]), ACCT, TODAY)
    by = {i["id"]: i for i in q["items"]}
    assert by["1"]["flags"][0]["code"] == "fatigue" and by["1"]["severity"] == "high" and by["2"]["severity"] == "medium" and by["3"]["flags"] == []
    assert "6.4 times in 30 days" in by["1"]["flags"][0]["text"] and q["summary"]["fatigued"] == 2


def test_spend_with_no_result_is_flagged_only_when_the_account_has_results_and_the_objective_has_one():
    rows = [ins("1", spend="200", leads=0), ins("2", spend="200", leads=4), ins("3", spend="200", objective="OUTCOME_TRAFFIC", leads=0), ins("4", spend="20", leads=0)]
    q = an.quality(raw([ad("1"), ad("2"), ad("3", objective="OUTCOME_TRAFFIC"), ad("4")], rows), ACCT, TODAY)
    by = {i["id"]: i for i in q["items"]}
    assert [f["code"] for f in by["1"]["flags"]] == ["no_results"] and by["1"]["severity"] == "high" and "spent 200 in 30 days" in by["1"]["flags"][0]["text"]
    assert by["2"]["flags"] == [] and by["3"]["flags"] == [] and by["4"]["flags"] == []              # a result, a traffic objective, a small spend
    q = an.quality(raw([ad("1")], [ins("1", spend="200", leads=0)]), ACCT, TODAY)
    assert q["items"][0]["flags"] == []                                                                  # nothing in the account has a result: the pixel is the finding, not this ad


def test_only_live_ads_are_judged_and_an_ad_with_no_delivery_is_idle_not_flagged():
    q = an.quality(raw([ad("1"), ad("2", status="DISAPPROVED"), ad("3")], [ins("1", q="AVERAGE"), ins("2", q="BELOW_AVERAGE_10")]), ACCT, TODAY)
    assert q["summary"]["ads"] == 2 and q["summary"]["delivered"] == 1 and q["summary"]["idle"] == 1 and q["summary"]["belowAverage"] == 0


def test_without_the_numbers_the_view_says_so_and_does_not_invent_a_ranking():
    q = an.quality(raw([ad("1")], [], insights_read=False, warnings=["x"]), ACCT, TODAY)
    assert q["headline"] == "Meta's rankings could not be read this time, so the ads are listed without them." and q["fixFirst"] == [] and q["warnings"] == ["x"]


def test_an_account_with_no_live_ad_says_so():
    assert an.quality(raw([], []), ACCT, TODAY)["headline"] == "No ad in this account is live."


# ── Coverage ─────────────────────────────────────────────────────────────────

def test_an_ad_set_with_one_ad_is_flagged_and_the_bigger_its_spend_the_higher():
    ads = [ad("1", adset="11"), ad("2", adset="12"), ad("3", adset="13"), ad("4", adset="13"), ad("5", adset="13")]
    rows = [ins("1", spend="800", adset="11"), ins("2", spend="30", adset="12"), ins("3", spend="100", adset="13"), ins("4", spend="35", adset="13"), ins("5", spend="35", adset="13")]
    cov = an.coverage(raw(ads, rows), ACCT, TODAY)
    by = {r["id"]: r for r in cov["adsets"]}
    assert by["11"]["flags"][0]["code"] == "single_ad" and by["11"]["severity"] == "high" and by["12"]["severity"] == "medium"
    assert [f["code"] for f in by["13"]["flags"]] == ["one_format"] and by["13"]["ads"] == 3
    assert cov["summary"]["adsets"] == 3 and cov["summary"]["singleAd"] == 2 and cov["summary"]["enough"] == 1


def test_two_ads_is_below_target_and_eleven_is_too_many_and_a_mix_of_formats_is_not_flagged():
    two = an.coverage(raw([ad("1"), ad("2")], []), ACCT, TODAY)["adsets"][0]
    assert [f["code"] for f in two["flags"]] == ["few_ads"] and "Our target is 3" in two["flags"][0]["text"]
    many = an.coverage(raw([ad(str(i), creative={"video_id": "v"} if i % 2 else {"image_hash": "h"}) for i in range(11)], []), ACCT, TODAY)["adsets"][0]
    assert [f["code"] for f in many["flags"]] == ["many_ads"] and many["formatList"] == ["image", "video"]
    ok = an.coverage(raw([ad("1"), ad("2", creative={"video_id": "v"}), ad("3")], []), ACCT, TODAY)["adsets"][0]
    assert ok["flags"] == [] and ok["severity"] == ""


def test_a_campaign_with_no_video_is_noted_and_a_stopped_ad_is_a_next_step_with_its_reason_pointer():
    ads = [ad("1"), ad("2", creative={"video_id": "v"}, campaign="22", adset="12"), ad("3", status="DISAPPROVED"), ad("4", status="WITH_ISSUES")]
    cov = an.coverage(raw(ads, []), ACCT, TODAY)
    by = {k["id"]: k for k in cov["campaigns"]}
    assert by["21"]["flags"][0]["code"] == "no_video" and by["22"]["flags"] == []
    assert [s["label"] for s in cov["stopped"]] == ["Disapproved", "Has issues"] and cov["stopped"][0]["severity"] == "high"
    assert cov["summary"]["stopped"] == 2 and cov["nextSteps"][0]["severity"] == "high" and "Disapproved by Meta" in cov["nextSteps"][0]["text"]
    assert cov["summary"]["ads"] == 2                                                                # a stopped ad is not a live one


def test_the_headline_counts_ad_sets_with_enough_ads_and_names_the_single_ad_and_stopped_ones():
    ads = [ad("1", adset="11"), ad("2", adset="12"), ad("3", adset="12"), ad("4", adset="12"), ad("5", status="DISAPPROVED")]
    assert an.coverage(raw(ads, []), ACCT, TODAY)["headline"] == "1 of 2 ad sets have 3 or more live ads, 1 run on a single ad, 1 ads are stopped or limited by Meta."
    assert an.coverage(raw([], []), ACCT, TODAY)["headline"] == "No ad in this account is live."


# ── The report: one read for both views, cached ─────────────────────────────

def test_one_read_feeds_both_views_and_the_second_costs_no_meta_call():
    store = FakeStore()
    paid_geo.clear_cache()
    base.reset_state()
    calls = []

    def pages(path, params):
        calls.append(path)
        return [ad("1"), ad("2"), ad("3")] if path.endswith("/ads") else [ins("1", q="BELOW_AVERAGE_10"), ins("2"), ins("3")]
    acct = lambda a: {"name": "Acme", "currency": "EUR"}                                                # noqa: E731
    q = c.report_for("act_1234567", store=store, pages_fn=pages, account_fn=acct, today=TODAY, view="quality")
    n = len(calls)
    v = c.report_for("act_1234567", store=store, pages_fn=pages, account_fn=acct, today=TODAY, view="coverage")
    assert n == 2 and len(calls) == 2 and q["view"] == "quality" and v["view"] == "coverage"
    assert q["account"] == {"id": "act_1234567", "name": "Acme", "currency": "EUR", "platform": "meta", "isGroup": False, "accountCount": 1} and q["readAt"]
    assert q["summary"]["belowAverage"] == 1 and v["summary"]["ads"] == 3
    paid_geo.clear_cache()


def test_a_google_account_is_refused_here():
    with pytest.raises(ValueError):
        c.report_for("1234567890", store=FakeStore(), pages_fn=lambda p, q: [], today=TODAY)


def test_the_spend_share_that_makes_a_single_ad_urgent_is_15_percent_and_3_percent_is_medium():
    def level(spend_one, spend_rest):
        ads = [ad("1", adset="11"), ad("2", adset="12"), ad("3", adset="12"), ad("4", adset="12")]
        rows = [ins("1", spend=str(spend_one), adset="11"), ins("2", spend=str(spend_rest / 3), adset="12"), ins("3", spend=str(spend_rest / 3), adset="12"), ins("4", spend=str(spend_rest / 3), adset="12")]
        return {r["id"]: r["severity"] for r in an.coverage(raw(ads, rows), ACCT, TODAY)["adsets"]}["11"]
    assert level(150, 850) == "high" and level(149, 851) == "medium" and level(30, 970) == "medium" and level(29, 971) == "low"


# ── The routes: a Meta account goes to Meta's view, a Google one is untouched, Meta's errors are a clear 502 ──

def _routes():
    from tests.test_paid_marketing import STAFF, client_as
    return client_as(STAFF)


def test_the_ad_strength_route_sends_a_meta_account_to_the_quality_view_and_a_google_one_to_the_old_report(monkeypatch):
    from tracker import paid_adstrength
    seen = []
    monkeypatch.setattr(c, "report_for", lambda acct, **kw: seen.append(("meta", acct, kw)) or {"platform": "meta", "view": kw["view"]})
    monkeypatch.setattr(paid_adstrength, "report_for", lambda acct, **kw: seen.append(("google", acct, kw)) or {"platform": "google"})
    cl = _routes()
    assert cl.get("/p2/paid-marketing/api/adstrength/report?account=act_1234567").get_json() == {"platform": "meta", "view": "quality"}
    assert cl.get("/p2/paid-marketing/api/adstrength/report?account=1234567890").get_json() == {"platform": "google"}
    assert cl.get("/p2/paid-marketing/api/adstrength/report?account=act_1234567&refresh=1").status_code == 200
    assert seen[0][:2] == ("meta", "act_1234567") and seen[1][:2] == ("google", "1234567890") and seen[2][2] == {"view": "quality", "force": True}


def test_the_asset_coverage_route_sends_a_meta_account_to_the_coverage_view(monkeypatch):
    from tracker import paid_assets
    monkeypatch.setattr(c, "report_for", lambda acct, **kw: {"platform": "meta", "view": kw["view"]})
    monkeypatch.setattr(paid_assets, "report_for", lambda acct, **kw: {"platform": "google"})
    cl = _routes()
    assert cl.get("/p2/paid-marketing/api/assets/report?account=act_1234567").get_json() == {"platform": "meta", "view": "coverage"}
    assert cl.get("/p2/paid-marketing/api/assets/report?account=1234567890").get_json() == {"platform": "google"}


@pytest.mark.parametrize("path", ["/p2/paid-marketing/api/adstrength/report?account=act_1234567", "/p2/paid-marketing/api/assets/report?account=act_1234567"])
def test_a_meta_error_is_a_502_with_meta_named_and_never_a_500(monkeypatch, path):
    def boom(acct, **kw):
        raise c.meta.AdsError("Meta is rate limiting this app right now. Try again in a minute.")
    monkeypatch.setattr(c, "report_for", boom)
    r = _routes().get(path)
    assert r.status_code == 502 and r.get_json()["kind"] == "meta" and "rate limiting" in r.get_json()["error"]
