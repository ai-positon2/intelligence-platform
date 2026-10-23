"""What a Meta ad account has done in the last 90 days, for the agents that start from a client (tracker/paid_strategy_research_meta.py). Meta's answers are in the shapes the Graph API documents."""
import datetime as dt
import json

import pytest

from tests import business_fakes as B
from tracker import paid_strategy_research_meta as m

TODAY = dt.date(2026, 10, 21)
ACCT = {"id": "act_1234567", "name": "Acme Meta", "currency": "USD"}


def test_the_window_is_the_90_days_before_today():
    assert m.window(TODAY) == ("2026-07-23", "2026-10-20")


def test_the_four_reads_ask_for_campaigns_by_objective_placements_devices_and_the_live_ads():
    r = m.requests_for("act_9", "2026-07-23", "2026-10-20")
    assert set(r) == {"campaigns", "placements", "devices", "ads"}
    path, p = r["campaigns"]
    assert path == "/act_9/insights" and p["level"] == "campaign" and json.loads(p["time_range"]) == {"since": "2026-07-23", "until": "2026-10-20"} and "objective" in p["fields"] and "action_values" in p["fields"]
    assert r["placements"][1]["breakdowns"] == "publisher_platform" and r["devices"][1]["breakdowns"] == "device_platform"
    path, p = r["ads"]
    assert path == "/act_9/ads" and json.loads(p["effective_status"]) == ["ACTIVE"] and "creative{title,body,call_to_action_type,object_type}" in p["fields"]


def test_a_campaign_is_read_with_its_objective_its_result_and_its_cost_and_one_with_no_delivery_is_left_out():
    rows = B.meta_pages()("/act_1/insights", {"level": "campaign"})
    got = m.parse_campaigns(rows)
    assert len(got) == 1 and got[0]["name"] == "Leads <b>Spring</b>" and got[0]["objective"] == "LEADS" and got[0]["cost"] == 900.5 and got[0]["conv"] == 30 and got[0]["cpa"] == 30.02 and got[0]["cvr"] == 5.0


def test_placements_and_devices_are_labelled_added_by_label_and_the_biggest_spend_comes_first():
    pages = B.meta_pages()
    pl = m.parse_split(pages("/act_1/insights", {"breakdowns": "publisher_platform"}), "publisher_platform", m.LABELS)
    assert [p["name"] for p in pl] == ["Facebook", "Instagram"] and pl[0]["share"] == 77.7 and pl[1]["cpa"] == 20.05
    dev = m.parse_split(pages("/act_1/insights", {"breakdowns": "device_platform"}), "device_platform", m.DEVICES)
    assert [d["name"] for d in dev] == ["Mobile", "Desktop"] and dev[0]["cost"] == 800 and dev[0]["conv"] == 25                  # mobile app and mobile web are one device
    assert m.parse_split([{"publisher_platform": "newplace", "spend": "5", "impressions": "10"}], "publisher_platform", m.LABELS)[0]["name"] == "Newplace"


def test_the_live_ads_keep_their_own_words_and_an_ad_with_none_is_left_out():
    ads = m.parse_ads(B.meta_pages()("/act_1/ads", {}))
    assert ads == [{"name": "Spring offer", "campaign": "Leads", "title": "Free consult", "body": "Book a free consultation this week.", "cta": "Book Travel", "format": "Share"}]
    many = [{"name": str(i), "creative": {"title": "t%d" % i}} for i in range(40)]
    assert len(m.parse_ads(many)) == m.MAX_ADS


def test_the_read_is_a_piece_with_totals_and_says_what_it_could_not_read():
    got = m.read_meta_history(ACCT, pages_fn=B.meta_pages(), today=TODAY)
    assert got["state"] == "ok" and got["data"]["totals"]["cost"] == 900.5 and got["data"]["account"]["platform"] == "meta" and got["data"]["window"] == {"start": "2026-07-23", "end": "2026-10-20"}
    assert "1 campaigns, 900 spent and 30 results" in got["note"]
    part = m.read_meta_history(ACCT, pages_fn=B.meta_pages(fail=("ads",)), today=TODAY)
    assert part["state"] == "partial" and part["data"]["ads"] == [] and "Ads could not be read" in part["note"]
    dead = m.read_meta_history(ACCT, pages_fn=B.meta_pages(fail=("campaigns",)), today=TODAY)
    assert dead["state"] == "failed" and "campaigns of this Meta ad account could not be read" in dead["note"]
    empty = m.read_meta_history(ACCT, pages_fn=lambda p, q: [], today=TODAY)
    assert empty["state"] == "ok" and empty["note"].startswith("No spend in the last 90 days")


def test_the_writing_is_shown_nothing_for_an_unread_account_and_the_account_when_it_was_read():
    got = m.read_meta_history(ACCT, pages_fn=B.meta_pages(), today=TODAY)
    ev = m.evidence({"metahistory": got})
    assert set(ev) == {"account", "window", "totals", "campaigns", "placements", "devices", "ads"} and ev["totals"]["cost"] == 900.5
    assert m.evidence({}) is None and m.evidence({"metahistory": {"state": "failed", "data": None}}) is None
    assert m.evidence({"metahistory": m.read_meta_history(ACCT, pages_fn=lambda p, q: [], today=TODAY)}) is None


# ── Interests and audiences ─────────────────────────────────────────────────

from tests.test_paid_keywords_meta import ACCT as KM_ACCT, pages as km_pages          # noqa: E402
from tests.test_paid_meta_interests import getter                                       # noqa: E402


def test_the_phrases_to_look_up_come_from_what_it_sells_the_page_headings_the_rivals_and_the_industry_and_are_short_and_not_repeated():
    inp = {"services": "dental implants, Invisalign; emergency dentist care for a whole family at night time", "competitors": [{"name": "Smile Austin", "url": ""}, {"name": "implants Dental", "url": ""}],
           "industry": "Dental"}
    research = {"website": {"data": {"pages": [{"h1": ["Family Dentist"]}, {"h1": ["Dental Implants"]}, {"h1": []}]}}}
    got = m.audience_phrases(inp, research)
    assert got == ["dental implants", "Invisalign", "Family Dentist", "Smile Austin", "implants Dental", "Dental"]            # the long service is left out, a repeat of "dental implants" is too
    assert m.audience_phrases({}, {}) == [] and len(m.audience_phrases({"services": ",".join("s%d" % i for i in range(30))}, {})) == 12


def test_the_interests_are_meta_s_own_and_those_the_ad_sets_already_aim_at_are_marked():
    pc = m.read_meta_audience({"services": "insulin pump, diabetes"}, {}, get_fn=getter(), account=KM_ACCT, pages_fn=km_pages(), today=TODAY)
    d = pc["data"]
    assert pc["state"] == "ok" and d["phrases"] == ["insulin pump", "diabetes"] and d["adsetCount"] == 3 and d["totals"]["cost"] == 1100.0
    by = {c["id"]: c for c in d["candidates"]}
    assert sorted(by) == ["1", "2", "3", "4", "5"] and by["1"]["inUse"]["adsets"] == 1 and by["3"]["inUse"]["adsets"] == 1 and by["2"]["inUse"] is None
    assert [c["id"] for c in d["candidates"]][:2] == ["1", "3"] and "5 interests from 2 phrases, and 3 interests in use by the ad sets." in pc["note"]
    assert {u["id"] for u in d["inUse"]} == {"1", "3", "90"}                                    # the one no search found is in the list of what is in use, not in the interests to choose from


def test_without_a_meta_account_the_interests_are_still_looked_up_and_nothing_is_said_to_be_in_use():
    pc = m.read_meta_audience({"services": "insulin pump"}, {}, get_fn=getter())
    assert pc["state"] == "ok" and pc["data"]["inUse"] == [] and pc["data"]["adsetCount"] == 0 and all(c["inUse"] is None for c in pc["data"]["candidates"])


def test_an_ad_account_that_cannot_be_read_is_said_and_the_lookup_goes_on():
    pc = m.read_meta_audience({"services": "insulin pump"}, {}, get_fn=getter(), account=KM_ACCT, pages_fn=km_pages(fail=("adsets",)), today=TODAY)
    assert pc["state"] == "partial" and "ad sets of this Meta ad account could not be read" in pc["note"] and pc["data"]["candidates"] and pc["data"]["inUse"] == []


def test_no_phrase_and_no_interest_in_use_fails_the_piece_and_so_does_a_list_with_no_interest_at_all():
    assert m.read_meta_audience({}, {}, get_fn=getter())["state"] == "failed"
    pc = m.read_meta_audience({"services": "type 1"}, {}, get_fn=getter())
    assert pc["state"] == "failed" and '"type 1"' in pc["note"] and "health conditions" in pc["note"]


def test_the_words_to_avoid_leave_an_interest_out_and_more_than_the_limit_are_cut_with_those_in_use_first():
    pc = m.read_meta_audience({"services": "diabetes", "avoid": "diet; jobs"}, {}, get_fn=getter())
    assert "4" not in {c["id"] for c in pc["data"]["candidates"]}
    big = [opt_("%d" % (100 + i), "Extra %d" % i) for i in range(80)] + [opt_("1", "Insulin pump")]
    pc = m.read_meta_audience({"services": "insulin pump"}, {}, get_fn=lambda path, params: big if params.get("type") == "adinterest" else [], account=KM_ACCT, pages_fn=km_pages(), today=TODAY)
    assert len(pc["data"]["candidates"]) == 60 and pc["data"]["found"] == 81 and pc["data"]["candidates"][0]["id"] == "1" and pc["data"]["candidates"][0]["inUse"]


def opt_(oid, name):
    return {"id": oid, "name": name, "audience_size_lower_bound": 1000000, "audience_size_upper_bound": 1200000, "path": ["Interests"], "topic": "x"}


def test_the_evidence_shows_each_interest_with_its_size_and_path_and_how_it_did_where_it_is_in_use():
    pc = m.read_meta_audience({"services": "insulin pump"}, {}, get_fn=getter(), account=KM_ACCT, pages_fn=km_pages(), today=TODAY)
    ev = m.audience_evidence({"metaaudience": pc})
    assert any(x.startswith("Insulin pump | 1.0M to 1.2M | Interests > Additional interests | in use: 1 ad sets, 600.0 spent, 20.0 results") for x in ev["interestsMetaHas"])
    assert any(x.startswith("Insulin | 5.0M to 6.0M | Interests > Additional interests") and "in use" not in x for x in ev["interestsMetaHas"])
    assert ev["adSets"] == 3 and {u["name"] for u in ev["interestsInUse"]} == {"Insulin pump", "Diabetes", "Frequent travellers"}
    assert m.audience_evidence({}) is None and m.audience_evidence({"metaaudience": {"data": {}}}) is None


def test_nothing_to_look_up_is_said_in_those_words_and_the_evidence_of_an_empty_piece_is_none():
    assert "nothing to look up" in m.read_meta_audience({}, {}, get_fn=getter())["note"]
    assert m.audience_evidence({"metaaudience": {"data": {"phrases": ["x"], "candidates": [], "inUse": []}}}) is None


def test_meta_is_asked_for_suggestions_from_the_interests_in_use_and_the_top_result_of_each_of_the_first_four_phrases():
    calls = []
    m.read_meta_audience({"services": "insulin pump, diabetes, a, b, c, d"}, {}, get_fn=getter(calls), account=KM_ACCT, pages_fn=km_pages(), today=TODAY)
    sugg = [p for _path, p in calls if p.get("type") == "adinterestsuggestion"]
    asked = json.loads(sugg[0]["interest_list"])
    assert len(sugg) == 1 and asked[:2] == ["Insulin pump", "Diabetes"] and len(asked) <= 8                # the interests in use, biggest spend first, then the top result of phrases
    calls.clear()
    m.read_meta_audience({"services": "insulin pump, diabetes, a, b, c, d"}, {}, get_fn=getter(calls))
    asked = json.loads([p for _path, p in calls if p.get("type") == "adinterestsuggestion"][0]["interest_list"])
    assert asked == ["Insulin pump", "Diabetes"]                                                          # a, b, c and d found nothing; only the first four phrases are used


def test_an_interest_in_use_is_a_seed_for_suggestions_even_when_no_phrase_found_it():
    calls = []
    m.read_meta_audience({"services": "insulin pump"}, {}, get_fn=getter(calls), account=KM_ACCT, pages_fn=km_pages(), today=TODAY)
    asked = json.loads([p for _path, p in calls if p.get("type") == "adinterestsuggestion"][0]["interest_list"])
    assert asked == ["Insulin pump", "Diabetes", "Frequent travellers"]                                    # the interests in use by ad sets, though no phrase found Diabetes or the travellers
