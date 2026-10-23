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
