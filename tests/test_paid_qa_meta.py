"""QA Agent for a Meta campaign (tracker/paid_qa_meta.py): reading one campaign and the exact rules. Meta's answers are in the shapes the Graph API documents; a rule ticks or crosses a box only
when the answer is exact."""
import json

import pytest

from tracker import paid_qa_book, paid_qa_meta as qm

ACT = "act_1234567"
CAMP = {"id": "9000000001", "name": "P2_MetaLeads_Implants_20261102", "objective": "OUTCOME_LEADS", "status": "ACTIVE", "effective_status": "ACTIVE", "special_ad_categories": [], "buying_type": "AUCTION",
        "daily_budget": "10000", "bid_strategy": "LOWEST_COST_WITHOUT_CAP", "start_time": "2026-11-02T08:00:00+0000"}
TARGETING = {"geo_locations": {"cities": [{"key": "1", "name": "Austin", "radius": 25, "distance_unit": "mile"}], "location_types": ["home", "recent"]}, "age_min": 25, "age_max": 64,
             "flexible_spec": [{"interests": [{"id": "1", "name": "Dentistry"}]}], "excluded_custom_audiences": [{"id": "ca1", "name": "Existing patients"}], "targeting_automation": {"advantage_audience": 0}}


def adset(i="s1", **kw):
    base = {"id": i, "name": "Missing teeth " + i, "effective_status": "ACTIVE", "optimization_goal": "OFFSITE_CONVERSIONS", "destination_type": "WEBSITE", "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "promoted_object": {"pixel_id": "111", "custom_event_type": "LEAD"}, "attribution_spec": [{"event_type": "CLICK_THROUGH", "window_days": 7}, {"event_type": "VIEW_THROUGH", "window_days": 1}],
            "targeting": json.loads(json.dumps(TARGETING)), "daily_budget": "5000"}
    base.update(kw)
    return base


def ad(i, sid="s1", **kw):
    creative = {"id": "c" + i, "object_type": "SHARE", "image_hash": "h", "url_tags": "utm_source=meta&utm_medium=paid_social&utm_campaign=x", "instagram_actor_id": "77",
                "object_story_spec": {"page_id": "55", "link_data": {"message": "Missing a tooth? Book a visit today", "name": "Dental Implants Austin", "description": "Free consult",
                                                                    "link": "https://acme.com/implants", "call_to_action": {"type": "BOOK_TRAVEL"}}}}
    base = {"id": i, "name": "Ad " + i, "effective_status": "ACTIVE", "adset_id": sid, "creative": creative}
    for k, v in kw.items():
        if k == "link_data":
            creative["object_story_spec"]["link_data"].update(v)
        elif k == "creative":
            creative.update(v)
        else:
            base[k] = v
    return base


def world(camp=None, adsets=None, ads=None, fail=()):
    camp = dict(CAMP, **(camp or {}))
    adsets = [adset()] if adsets is None else adsets
    ads = [ad("1"), ad("2"), ad("3")] if ads is None else ads

    def get(path, params):
        assert path == "/" + CAMP["id"]
        return camp

    def pages(path, params):
        if path.endswith("/adsets"):
            if "adsets" in fail:
                raise RuntimeError("Meta answered 400")
            return adsets
        if path.endswith("/ads"):
            if "ads" in fail:
                raise RuntimeError("Meta answered 500")
            return ads
        if path.endswith("/campaigns"):
            return [{"id": "2", "name": "Beta", "objective": "OUTCOME_SALES"}, {"id": "1", "name": "alpha", "objective": "OUTCOME_ENGAGEMENT"}, {"name": "no id"}]
        raise AssertionError(path)
    return get, pages


def run(book=None, web=None, **kw):
    get, pages = world(**kw)
    return qm.run(ACT, CAMP["id"], get, pages, web_fn=web, book=book)


def book(**fields):
    rows = [{"field": k.replace("_", " "), "value": v, "other": ""} for k, v in fields.items()]
    return {"filename": "book.xlsx", "rows": rows, "text": ""}


def test_the_campaigns_are_listed_by_name_with_a_type_word_for_the_objectives_the_checklist_knows():
    got = qm.list_campaigns(ACT, world()[1])
    assert [(c["name"], c["channel"], c["type"]) for c in got] == [("alpha", "META_ENGAGEMENT", ""), ("Beta", "META_SALES", "Meta Sales")]


def test_one_campaign_is_read_with_its_ad_sets_and_its_ads_as_plain_facts():
    out = run()
    assert out["campaign"] == {"id": "9000000001", "name": CAMP["name"], "channel": "META_LEADS", "type": "Meta Leads", "dsa": False, "status": "ACTIVE"} and out["errors"] == {}
    a = out["data"]["ads"][0]
    assert a["primary"] == ["Missing a tooth? Book a visit today"] and a["headlines"] == ["Dental Implants Austin"] and a["descriptions"] == ["Free consult"] and a["urls"] == ["https://acme.com/implants"]
    assert a["ctas"] == ["BOOK_TRAVEL"] and a["page"] == "55" and a["instagram"] == "77" and a["format"] == "image" and a["status"] == "ACTIVE"
    with pytest.raises(ValueError):
        qm.gather(ACT, "abc", *world())
    empty = qm.run(ACT, CAMP["id"], lambda p, q: {}, world()[1])
    assert empty["campaign"]["id"] == "" and empty["findings"] == {}


def test_ad_text_is_read_from_every_place_meta_keeps_it():
    flex = {"id": "9", "name": "Flex", "effective_status": "ACTIVE", "adset_id": "s1", "creative": {"asset_feed_spec": {"bodies": [{"text": "A"}, {"text": "B"}], "titles": [{"text": "H1"}], "descriptions": [{"text": "D1"}],
            "link_urls": [{"website_url": "https://acme.com/a"}], "call_to_action_types": ["LEARN_MORE"]}, "object_story_spec": {"page_id": "5"}, "instagram_actor_id": "6", "video_id": "v"}}
    i = qm.ad_info(flex)
    assert i["primary"] == ["A", "B"] and i["headlines"] == ["H1"] and i["descriptions"] == ["D1"] and i["urls"] == ["https://acme.com/a"] and i["ctas"] == ["LEARN_MORE"] and i["format"] == "video" and i["instagram"] == "6"
    video = qm.ad_info({"id": "1", "creative": {"object_story_spec": {"page_id": "5", "instagram_actor_id": "8", "video_data": {"message": "M", "title": "T", "link_description": "L", "call_to_action": {"type": "SIGN_UP", "value": {"link": "https://x.com/"}}}}}})
    assert video["primary"] == ["M"] and video["headlines"] == ["T"] and video["descriptions"] == ["L"] and video["urls"] == ["https://x.com/"] and video["ctas"] == ["SIGN_UP"] and video["format"] == "video" and video["instagram"] == "8"
    carousel = qm.ad_info({"id": "2", "creative": {"object_story_spec": {"link_data": {"child_attachments": [{}, {}]}}}})
    assert carousel["format"] == "carousel" and qm.ad_info({})["format"] == "unknown"
    feats = qm.ad_info({"id": "3", "creative": {"degrees_of_freedom_spec": {"creative_features_spec": {"standard_enhancements": {"enroll_status": "OPT_IN"}, "text_optimizations": {"enroll_status": "OPT_OUT"}}}}})
    assert feats["enhancements"] == ["standard_enhancements"]


def f(out, cid):
    return out["findings"][cid]


def test_the_name_and_the_objective_are_checked_against_each_other_and_the_book():
    out = run()
    assert f(out, "1.1")["result"] == "pass" and "launch date 20261102" in f(out, "1.1")["found"]
    assert f(out, "1.2")["result"] is None and "OUTCOME_LEADS" in f(out, "1.2")["found"] and "The name says the same" in f(out, "1.2")["found"]
    bad = run(camp={"name": "Implants launch"})
    assert f(bad, "1.1")["result"] == "fail" and "does not follow P2_" in f(bad, "1.1")["found"]
    wrong = run(camp={"name": "P2_MetaSales_X_20261102"})
    assert f(wrong, "1.2")["result"] == "fail" and "The name says Sales" in f(wrong, "1.2")["found"]
    assert f(run(book=book(Campaign_Objective="Leads")), "1.2")["result"] == "pass"
    assert f(run(book=book(Campaign_Objective="Sales")), "1.2")["result"] == "fail"
    assert f(run(book=book(Campaign_Objective="depends on the campaign book")), "1.2")["result"] is None


def test_special_ad_categories_buying_type_and_dates():
    assert f(run(), "1.3")["result"] is None and "none" in f(run(), "1.3")["found"]
    assert f(run(camp={"special_ad_categories": ["CREDIT"]}), "1.3")["result"] is None
    assert f(run(camp={"special_ad_categories": ["CREDIT"]}, book=book(Special_Ad_Categories="None")), "1.3")["result"] == "fail"
    assert f(run(book=book(Special_Ad_Categories="None")), "1.3")["result"] == "pass"
    assert f(run(), "1.4")["result"] == "pass" and f(run(camp={"buying_type": "RESERVED"}), "1.4")["result"] == "fail"
    assert f(run(), "1.7")["result"] == "pass" and "No end date" in f(run(), "1.7")["found"]
    assert f(run(camp={"stop_time": "2026-12-01T00:00:00+0000"}), "1.7")["result"] == "fail"
    assert f(run(camp={"stop_time": "2026-12-01T00:00:00+0000", "lifetime_budget": "9000"}), "1.7")["result"] is None


def test_the_budget_and_the_bid_strategy_are_printed_never_ticked():
    out = run()
    assert f(out, "1.5")["result"] is None and "campaign budget: daily 100.00" in f(out, "1.5")["found"] and "Missing teeth s1: daily 50.00" in f(out, "1.5")["found"]
    assert f(out, "1.6")["result"] is None and "LOWEST_COST_WITHOUT_CAP" in f(out, "1.6")["found"]
    none = run(camp={"daily_budget": None, "bid_strategy": None}, adsets=[adset(daily_budget=None, bid_strategy=None)])
    assert f(none, "1.5")["found"].startswith("No budget") and f(none, "1.6")["found"].startswith("No bid strategy")


def test_the_number_of_ads_in_each_active_ad_set_must_be_between_three_and_ten():
    assert f(run(), "1.8")["result"] == "pass"
    out = run(ads=[ad("1"), ad("2")])
    assert f(out, "1.8")["result"] == "fail" and "Fewer than 3 ads in: Missing teeth s1 (2)" in f(out, "1.8")["found"]
    out = run(ads=[ad(str(i)) for i in range(11)])
    assert f(out, "1.8")["result"] == "fail" and "More than 10" in f(out, "1.8")["found"]
    assert f(run(adsets=[adset(effective_status="PAUSED")]), "1.8")["result"] == "fail"
    assert f(run(ads=[ad("1"), ad("2"), ad("3"), ad("4", effective_status="PAUSED")]), "1.8")["result"] == "pass"            # a paused ad is not counted
    assert f(run(fail=("ads",)), "1.8")["result"] is None and "could not be read from Meta" in f(run(fail=("ads",)), "1.8")["found"]


def test_every_active_ad_needs_a_page_and_an_instagram_account():
    assert f(run(), "1.9")["result"] == "pass"
    no_ig = run(ads=[ad("1"), ad("2", creative={"instagram_actor_id": ""}), ad("3")])
    assert f(no_ig, "1.9")["result"] == "fail" and "No Instagram account is set on: Ad 2" in f(no_ig, "1.9")["found"]
    no_page = run(ads=[ad("1", creative={"object_story_spec": {"link_data": {}}})])
    assert f(no_page, "1.9")["result"] == "fail" and "No Facebook Page" in f(no_page, "1.9")["found"]
    assert f(run(ads=[]), "1.9")["result"] is None


def test_the_ad_set_settings_are_printed_and_compared_with_the_book_only_when_it_is_unambiguous():
    out = run()
    assert f(out, "2.1")["result"] is None and "website" in f(out, "2.1")["found"]
    assert f(run(book=book(Conversion_Location="Website")), "2.1")["result"] == "pass" and f(run(book=book(Conversion_Location="Instant forms")), "2.1")["result"] == "fail"
    assert f(out, "2.2")["result"] is None and "conversions" in f(out, "2.2")["found"]
    assert f(run(book=book(**{"Performance_Goal_(optimisation)": "Maximize number of conversions (leads)"})), "2.2")["result"] == "pass"
    assert f(run(book=book(**{"Performance_Goal_(optimisation)": "Maximize reach"})), "2.2")["result"] == "fail"
    clicks = run(adsets=[adset(optimization_goal="LINK_CLICKS")])
    assert f(clicks, "2.2")["result"] == "fail" and "counts clicks, views or reach on a leads campaign" in f(clicks, "2.2")["found"]
    assert f(run(camp={"objective": "OUTCOME_TRAFFIC", "name": "P2_MetaTraffic_X_20261102"}, adsets=[adset(optimization_goal="LINK_CLICKS")]), "2.2")["result"] is None


def test_the_dataset_and_the_event_are_checked_and_the_attribution_window_is_read():
    assert f(run(), "2.3")["result"] is None and "LEAD" in f(run(), "2.3")["found"]
    assert f(run(book=book(**{"Dataset_(pixel)_and_Conversion_Event": "A dataset (pixel) is set, and the event is LEAD"})), "2.3")["result"] == "pass"
    assert f(run(book=book(**{"Dataset_(pixel)_and_Conversion_Event": "A dataset (pixel) is set, and the event is PURCHASE"})), "2.3")["result"] == "fail"
    no_pixel = run(adsets=[adset(promoted_object={"custom_event_type": "LEAD"})])
    assert f(no_pixel, "2.3")["result"] == "fail" and "No dataset (pixel) is set on: Missing teeth s1" in f(no_pixel, "2.3")["found"]
    assert "7-day click and 1-day view" in f(run(), "2.4")["found"] and f(run(), "2.4")["result"] is None
    assert f(run(book=book(Attribution_Setting="7-day click and 1-day view")), "2.4")["result"] == "pass"
    assert f(run(book=book(Attribution_Setting="1-day click")), "2.4")["result"] == "fail"
    assert f(run(adsets=[adset(attribution_spec=[])]), "2.4")["found"].startswith("Meta showed no attribution")


def test_places_location_type_age_gender_and_language():
    assert f(run(), "2.5")["result"] is None and "Austin (25 mile)" in f(run(), "2.5")["found"]
    assert f(run(), "2.6")["result"] == "pass"
    travel = adset(targeting=dict(TARGETING, geo_locations=dict(TARGETING["geo_locations"], location_types=["home"])))
    assert f(run(adsets=[travel]), "2.6")["result"] == "fail"
    assert f(run(), "2.7")["result"] is None and "25 to 64" in f(run(), "2.7")["found"]
    assert f(run(book=book(Age="25 to 64")), "2.7")["result"] == "pass" and f(run(book=book(Age="18 to 65")), "2.7")["result"] == "fail"
    assert f(run(book=book(Age="All ages Meta allows (18 and over)")), "2.7")["result"] == "fail"
    assert f(run(), "2.8")["result"] is None and "all genders" in f(run(), "2.8")["found"]
    assert f(run(book=book(Gender="All genders")), "2.8")["result"] == "pass"
    women = adset(targeting=dict(TARGETING, genders=[2]))
    assert f(run(adsets=[women], book=book(Gender="All genders")), "2.8")["result"] == "fail" and "women" in f(run(adsets=[women]), "2.8")["found"]
    assert f(run(), "2.9")["result"] == "pass"
    spanish = adset(targeting=dict(TARGETING, locales=[24]))
    assert f(run(adsets=[spanish]), "2.9")["result"] == "fail" and f(run(adsets=[spanish], book=book(Languages="Spanish")), "2.9")["result"] is None


def test_the_audience_the_exclusions_and_the_placements():
    out = run()
    assert f(out, "2.10")["result"] is None and "interests: Dentistry" in f(out, "2.10")["found"] and "Advantage+ audience on" not in f(out, "2.10")["found"]
    adv = adset(targeting=dict(TARGETING, targeting_automation={"advantage_audience": 1}))
    assert "Advantage+ audience on" in f(run(adsets=[adv]), "2.10")["found"]
    assert "Existing patients" in f(out, "2.11")["found"] and f(out, "2.11")["result"] is None
    assert f(out, "2.12")["result"] == "pass" and "Advantage+ placements" in f(out, "2.12")["found"]
    manual = adset(targeting=dict(TARGETING, publisher_platforms=["facebook"]))
    assert f(run(adsets=[manual]), "2.12")["result"] is None and "facebook" in f(run(adsets=[manual]), "2.12")["found"]
    assert f(run(adsets=[manual], book=book(Placements="Advantage+ placements")), "2.12")["result"] == "fail"


def test_formats_and_the_length_of_the_text_which_fails_only_when_it_is_exact():
    out = run()
    assert f(out, "3.1")["found"] == "Formats: 3 image." and f(out, "3.1")["result"] is None
    for cid in ("3.2", "3.3", "3.4"):
        assert f(out, cid)["result"] is None and "None is over" in f(out, cid)["found"]          # spelling and grammar need the reviewer, so a clean length is never a tick
    long = run(ads=[ad("1", link_data={"message": "x" * 126}), ad("2", link_data={"name": "h" * 41}), ad("3", link_data={"description": "d" * 31})])
    assert f(long, "3.2")["result"] == "fail" and "Ad 1 (126)" in f(long, "3.2")["found"] and f(long, "3.3")["result"] == "fail" and "Ad 2 (41)" in f(long, "3.3")["found"]
    assert f(long, "3.4")["result"] == "fail" and "Ad 3 (31)" in f(long, "3.4")["found"]
    exact = run(ads=[ad("1", link_data={"message": "x" * 125, "name": "h" * 40, "description": "d" * 30})])
    assert [f(exact, c)["result"] for c in ("3.2", "3.3", "3.4")] == [None, None, None]


def test_the_button_the_addresses_and_the_tracking_parameters():
    assert f(run(), "3.5")["result"] is None and "BOOK_TRAVEL" in f(run(), "3.5")["found"]
    no_button = run(ads=[ad("1", link_data={"call_to_action": {"type": "NO_BUTTON"}}), ad("2"), ad("3")])
    assert f(no_button, "3.5")["result"] == "fail" and "No button on: Ad 1" in f(no_button, "3.5")["found"]
    out = run(web=lambda urls: [{"url": u, "status": 200} for u in urls])
    assert f(out, "3.6")["result"] == "pass" and "All HTTPS and all load" in f(out, "3.6")["found"]
    assert f(run(web=lambda urls: [{"url": u, "status": 404} for u in urls]), "3.6")["result"] == "fail"
    refused = run(web=lambda urls: [{"url": u, "status": 403} for u in urls])
    assert f(refused, "3.6")["result"] is None and "refusing automated visits" in f(refused, "3.6")["found"]
    assert f(run(), "3.6")["result"] is None and "could not be opened to confirm" in f(run(), "3.6")["found"]
    assert f(run(ads=[ad("1", link_data={"link": "http://acme.com/x"})]), "3.6")["result"] == "fail"
    assert f(run(), "3.7")["result"] == "pass"
    assert f(run(ads=[ad("1", creative={"url_tags": "utm_source=meta"}), ad("2"), ad("3")]), "3.7")["result"] == "fail"
    assert f(run(ads=[ad("1", creative={"url_tags": ""}, link_data={"link": "https://acme.com/?utm_source=a&utm_medium=b&utm_campaign=c"})]), "3.7")["result"] == "pass"


def test_creative_enhancements_and_the_review_status():
    on = {"degrees_of_freedom_spec": {"creative_features_spec": {"standard_enhancements": {"enroll_status": "OPT_IN"}}}}
    assert f(run(), "3.8")["found"] == "Creative enhancements opted into: none." and f(run(), "3.8")["result"] is None
    assert f(run(book=book(**{"Advantage+_creative_enhancements": "Off"})), "3.8")["result"] == "pass"
    out = run(ads=[ad("1", creative=on), ad("2"), ad("3")], book=book(**{"Advantage+_creative_enhancements": "Off"}))
    assert f(out, "3.8")["result"] == "fail" and "standard_enhancements" in f(out, "3.8")["found"]
    assert f(run(), "3.9")["result"] == "pass"
    bad = run(ads=[ad("1", effective_status="DISAPPROVED"), ad("2"), ad("3", effective_status="WITH_ISSUES")])
    assert f(bad, "3.9")["result"] == "fail" and "Ad 1 (disapproved)" in f(bad, "3.9")["found"] and "Ad 3 (with issues)" in f(bad, "3.9")["found"]
    pending = run(ads=[ad("1", effective_status="PENDING_REVIEW"), ad("2"), ad("3")])
    assert f(pending, "3.9")["result"] is None and "Still in review: Ad 1" in f(pending, "3.9")["found"]
    assert f(run(ads=[ad("1", effective_status="PAUSED")]), "3.9")["result"] is None


def test_a_check_meta_cannot_answer_has_no_rule_and_a_read_that_failed_leaves_its_checks_unread_and_says_why():
    out = run()
    assert not {"4.1", "4.2", "4.3", "4.4", "5.1", "5.2", "5.3"} & set(out["findings"])
    broken = run(fail=("adsets",))
    assert broken["errors"] == {"adsets": "Meta answered 400"}
    for cid in ("1.8", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9", "2.10", "2.11", "2.12"):
        assert f(broken, cid)["result"] is None and "could not be read from Meta" in f(broken, cid)["found"], cid
    assert f(broken, "1.1")["result"] == "pass"


def test_a_rule_that_raises_is_said_and_never_sinks_the_rest(monkeypatch):
    monkeypatch.setitem(qm.RULES, "1.4", lambda c: 1 / 0)
    out = run()
    assert f(out, "1.4")["result"] is None and "could not be read automatically (ZeroDivisionError)" in f(out, "1.4")["found"] and f(out, "1.1")["result"] == "pass"


def test_a_long_finding_is_cut_and_the_pages_failing_to_open_is_an_error_not_a_crash():
    assert len(qm.Finding(None, "x " * 2000)["found"]) <= qm.MAX_FOUND
    out = run(web=lambda urls: (_ for _ in ()).throw(RuntimeError("blocked")))
    assert out["errors"] == {"pages": "blocked"} and f(out, "3.6")["result"] is None


def test_the_reviewer_is_shown_the_campaign_its_ad_sets_its_ads_and_the_book_and_told_what_the_api_cannot_show():
    out = run(book=book(Campaign_Objective="Leads"), web=lambda urls: [{"url": u, "status": 200, "text": "t" * 3000} for u in urls])
    ev = qm.evidence_bundle(out["data"])
    assert ev["campaign"]["objective"] == "OUTCOME_LEADS" and ev["adSets"][0]["places"] == ["Austin (25 mile)"] and ev["adSets"][0]["attribution"] == "7-day click and 1-day view"
    assert ev["adSets"][0]["audience"] == ["interests: Dentistry"] and ev["adSets"][0]["excluded"] == ["audience: Existing patients"] and ev["adSets"][0]["placements"] == "Advantage+ placements"
    assert ev["ads"][0]["primaryText"] == ["Missing a tooth? Book a visit today"] and ev["ads"][0]["facebookPage"] is True and ev["ads"][0]["address"] == ["https://acme.com/implants"]
    assert len(ev["landingPages"][0]["text"]) == 1800 and ev["campaignBook"]["fields"][0]["field"] == "Campaign Objective" and "Conversions API" in " ".join(ev["accountSettingsNotVisibleToTheApi"])
    assert qm.evidence_bundle(run()["data"])["campaignBook"] == "none uploaded"


def test_only_the_addresses_of_active_ads_are_opened():
    opened = []
    run(ads=[ad("1"), ad("2"), ad("3"), ad("4", effective_status="PAUSED", link_data={"link": "https://acme.com/paused"})], web=lambda urls: opened.extend(urls) or [])
    assert opened == ["https://acme.com/implants"]
