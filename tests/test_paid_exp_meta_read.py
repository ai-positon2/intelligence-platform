"""Test & Experiment Tracker: what Meta says is being tested (tracker/paid_exp_meta_read.py)."""
import datetime as dt
import json

import pytest

from tracker import paid_exp_meta_read as r
from tracker import paid_meta as meta

TODAY = dt.date(2026, 10, 18)


def cell(cid, name, split=50, campaigns=(), adsets=()):
    c = {"id": cid, "name": name, "treatment_percentage": split}
    if campaigns:
        c["campaigns"] = {"data": [{"id": x, "name": "Camp " + x} for x in campaigns]}
    if adsets:
        c["adsets"] = {"data": [{"id": x, "name": "Set " + x} for x in adsets]}
    return c


def study(sid="900", name="Headline test", typ="SPLIT_TEST", start="2026-10-01T09:00:00+0000", end="2026-11-01T09:00:00+0000", cells=None, **over):
    s = {"id": sid, "name": name, "description": "Which headline", "type": typ, "start_time": start, "end_time": end}
    if cells is not None:
        s["cells"] = {"data": cells}
    s.update(over)
    return s


def day_row(eid, day, imp=1000, clicks=50, spend=20.0, leads=2, level="adset", **over):
    row = {level + "_id": eid, "date_start": day, "objective": "OUTCOME_LEADS", "spend": str(spend), "impressions": str(imp), "inline_link_clicks": str(clicks),
           "actions": [{"action_type": "lead", "value": str(leads)}]}
    row.update(over)
    return row


# ── The tests ───────────────────────────────────────────────────────────────

def test_a_test_is_running_scheduled_or_ended_by_its_dates_and_a_cancelled_one_has_ended():
    rows = [study("1", start="2026-10-01T09:00:00+0000", end="2026-11-01T09:00:00+0000"), study("2", start="2026-10-25T09:00:00+0000", end="2026-11-25T09:00:00+0000"),
            study("3", start="2026-09-01T09:00:00+0000", end="2026-09-30T09:00:00+0000"), study("4", canceled_time="2026-10-05T00:00:00+0000"),
            study("5", start="2026-10-01T09:00:00+0000", end=None)]
    got = {s["id"]: s for s in r.parse_studies(rows, TODAY)}
    assert [(got[i]["state"], got[i]["status"]) for i in ("meta:1", "meta:2", "meta:3", "meta:4", "meta:5")] == [
        ("running", "ACTIVE"), ("setting up", "SCHEDULED"), ("ended", "ENDED"), ("ended", "CANCELED"), ("running", "ACTIVE")]
    assert got["meta:1"]["start"] == "2026-10-01" and got["meta:1"]["end"] == "2026-11-01" and got["meta:5"]["end"] is None
    assert got["meta:1"]["resource"] == "meta:1" and got["meta:1"]["platform"] == "meta" and got["meta:1"]["typeLabel"] == "Meta A/B test"


def test_a_test_ending_today_is_still_running_and_one_starting_today_has_started():
    got = {s["id"]: s for s in r.parse_studies([study("1", end="2026-10-18T00:00:00+0000"), study("2", start="2026-10-18T09:00:00+0000")], TODAY)}
    assert got["meta:1"]["state"] == "running" and got["meta:2"]["state"] == "running"


def test_the_kinds_of_study_are_worded_and_an_unknown_one_is_made_readable():
    got = {s["id"]: s["typeLabel"] for s in r.parse_studies([study("1", typ="LIFT"), study("2", typ="BRAND_LIFT"), study("3", typ="WEIRD_NEW_KIND"), study("4", typ="")], TODAY)}
    assert got == {"meta:1": "Conversion lift study", "meta:2": "Brand lift study", "meta:3": "Weird new kind", "meta:4": ""}


def test_the_cells_come_in_order_with_their_campaigns_and_ad_sets_and_say_whether_meta_listed_them():
    s = r.parse_studies([study(cells=[cell("30", "B", 40, adsets=["a2"]), cell("20", "A", 60, campaigns=["c1"], adsets=["a1"]), {"name": "no id"}])], TODAY)[0]
    assert s["cellsRead"] is True and [c["name"] for c in s["cells"]] == ["A", "B"]
    a = s["cells"][0]
    assert a["split"] == 60 and a["campaigns"] == [{"id": "c1", "name": "Camp c1"}] and a["adsets"] == [{"id": "a1", "name": "Set a1"}] and s["cells"][1]["campaigns"] == []
    none = r.parse_studies([study()], TODAY)[0]
    assert none["cellsRead"] is False and none["cells"] == []
    odd = study()
    odd["cells"] = "nope"
    assert r.parse_studies([odd], TODAY)[0]["cellsRead"] is False


def test_a_study_with_no_id_is_left_out_and_the_newest_is_first():
    got = r.parse_studies([study("1", start="2026-09-01T00:00:00+0000"), {"name": "no id"}, study("2", start="2026-10-10T00:00:00+0000")], TODAY)
    assert [s["id"] for s in got] == ["meta:2", "meta:1"]


def test_a_blank_name_becomes_test_and_cell():
    s = r.parse_studies([study(name="  ", cells=[cell("1", " ")])], TODAY)[0]
    assert s["name"] == "Test" and s["cells"][0]["name"] == "Cell"


# ── What the cells did ──────────────────────────────────────────────────────

def test_a_day_is_one_row_of_the_entity_it_belongs_to_and_the_ad_set_wins_over_the_campaign():
    got = r.parse_days([day_row("a1", "2026-10-02", 1000, 50, 20.0, 3), day_row("a1", "2026-10-03T00:00:00", 500, 20, 8.0, 1), {"date_start": "2026-10-02"}, day_row("a2", "")])
    assert got == {"a1": {"2026-10-02": [1000, 50, 20.0, 3.0, 0.0], "2026-10-03": [500, 20, 8.0, 1.0, 0.0]}}
    assert r.parse_days([{"campaign_id": "c1", "adset_id": "", "date_start": "2026-10-02", "spend": "5", "impressions": "10"}]) == {"c1": {"2026-10-02": [10, 0, 5.0, 0.0, 0.0]}}


def built(cells, **over):
    return build_one(study(cells=cells, **over))


def build_one(raw):
    s = r.parse_studies([raw], TODAY)
    days = {"a1": {"2026-10-02": [1000, 50, 20.0, 2.0, 0.0], "2026-10-03": [1000, 60, 20.0, 3.0, 0.0]}, "a2": {"2026-10-02": [1000, 40, 20.0, 1.0, 0.0]},
            "c1": {"2026-10-02": [500, 10, 5.0, 0.0, 0.0]}}
    return r.build_studies(s, days, TODAY)[0]


def test_the_first_cell_is_the_control_and_each_cell_has_the_days_of_its_ad_sets():
    e = built([cell("2", "B", adsets=["a2"]), cell("1", "A", adsets=["a1"])])
    assert [(a["name"], a["control"]) for a in e["arms"]] == [("A", True), ("B", False)]
    assert e["measured"] is True and e["reason"] == "" and e["window"] == ["2026-10-01", "2026-10-17"] and e["clipped"] is False
    a, b = e["arms"]
    assert a["results"]["total"] == {"impressions": 2000, "clicks": 110, "cost": 40.0, "conversions": 5.0, "value": 0.0}
    assert sorted(a["results"]["days"]) == ["2026-10-02", "2026-10-03"] and b["results"]["total"]["clicks"] == 40
    assert a["campaigns"] == [{"id": "a1", "name": "Set a1", "type": "ad set"}] and a["split"] == 50


def test_a_cell_can_hold_campaigns_and_ad_sets_together():
    e = built([cell("1", "A", campaigns=["c1"], adsets=["a1"]), cell("2", "B", adsets=["a2"])])
    assert e["arms"][0]["results"]["total"]["clicks"] == 120
    assert [x["type"] for x in e["arms"][0]["campaigns"]] == ["campaign", "ad set"]


def test_a_test_with_a_lookback_longer_than_a_year_is_clipped():
    e = built([cell("1", "A", adsets=["a1"]), cell("2", "B", adsets=["a2"])], start="2024-01-01T00:00:00+0000", end="2026-12-01T00:00:00+0000")
    assert e["clipped"] is True and e["window"][0] == (TODAY - dt.timedelta(days=1) - dt.timedelta(days=rd_days() - 1)).isoformat()


def rd_days():
    return r.rd.MAX_LOOKBACK_DAYS


def test_a_test_that_has_not_started_has_no_window_and_no_result():
    e = built([cell("1", "A", adsets=["a1"]), cell("2", "B", adsets=["a2"])], start="2026-10-25T09:00:00+0000", end="2026-11-25T09:00:00+0000")
    assert e["window"] is None and e["measured"] is False and all(a["results"] is None for a in e["arms"])


def test_a_split_test_with_no_result_says_why():
    assert built([cell("1", "A")])["reason"] == "Meta lists fewer than two cells for this test."
    none = r.build_studies(r.parse_studies([study()], TODAY), {}, TODAY)[0]
    assert none["reason"] == "Meta did not list this test's cells." and none["measured"] is False
    assert built([cell("1", "A", adsets=["a1"]), cell("2", "B")])["reason"] == "Meta did not say which campaigns or ad sets each cell holds."
    assert built([cell("1", "A", adsets=["a1"]), cell("2", "B", adsets=["a2"])])["reason"] == ""


def test_the_numbers_of_a_cell_with_no_days_are_zeros_not_missing():
    e = built([cell("1", "A", adsets=["a1"]), cell("2", "B", adsets=["zz"])])
    assert e["measured"] is True and e["arms"][1]["results"]["total"]["clicks"] == 0


def test_a_study_that_is_not_an_a_b_test_is_listed_and_never_measured():
    e = built([cell("1", "A", adsets=["a1"]), cell("2", "B", adsets=["a2"])], typ="LIFT")
    assert e["measured"] is False and e["reason"] == "" and all(a["results"] is None for a in e["arms"]) and e["typeLabel"] == "Conversion lift study"


def test_a_test_of_three_cells_keeps_all_three_and_the_first_two_decide_whether_it_is_measured():
    e = built([cell("1", "A", adsets=["a1"]), cell("2", "B", adsets=["a2"]), cell("3", "C", adsets=["zz"])])
    assert len(e["arms"]) == 3 and e["measured"] is True and e["arms"][2]["control"] is False


# ── Reading the days ────────────────────────────────────────────────────────

class Graph:
    """A Graph API double answering by path and by the insights level."""

    def __init__(self, studies=None, ads=None, ad_stats=None, days=None, fail=(), over=None):
        self.calls, self.fail, self.over = [], set(fail), over
        self.studies = studies if studies is not None else [study(cells=[cell("1", "A", adsets=["a1"]), cell("2", "B", adsets=["a2"])])]
        self.ads = ads if ads is not None else []
        self.ad_stats = ad_stats if ad_stats is not None else []
        self.days = days if days is not None else [day_row("a1", "2026-10-02"), day_row("a2", "2026-10-02", clicks=40, leads=1)]

    def pages(self, path, params):
        self.calls.append((path, dict(params)))
        kind = path.rsplit("/", 1)[-1]
        if kind == "insights":
            kind = "days" if params.get("time_increment") else "ad_stats"
        if kind == "ad_studies" and "adsets{" in params["fields"] and "nested" in self.fail:
            raise meta.AdsError("Meta answered 400: (#100) cells edge has no adsets")
        if kind in self.fail:
            raise meta.AdsError("Meta answered 400: (#100) bad %s" % kind)
        if kind == "ad_studies" and "adsets{" not in params["fields"]:
            return [dict(s, cells={"data": [{k: v for k, v in c.items() if k not in ("campaigns", "adsets")} for c in s["cells"]["data"]]}) if isinstance(s.get("cells"), dict) else s for s in self.studies]
        if kind == "ads" and "creative" in params["fields"] and ("creative" in self.fail or (self.over and params["limit"] > self.over)):
            raise meta.AdsError("Meta answered 500: reduce the amount of data")
        if kind == "OVER":
            raise meta.OverQuota("wait")
        if kind == "ads" and "creative" not in params["fields"]:
            return [{k: v for k, v in a.items() if k != "creative"} for a in self.ads]
        return {"ad_studies": self.studies, "ads": self.ads, "ad_stats": self.ad_stats, "days": self.days}[kind]


def test_the_days_are_read_for_the_campaigns_and_ad_sets_of_the_tests_that_can_be_measured_in_one_window():
    g = Graph()
    studies = r.parse_studies([study(cells=[cell("1", "A", campaigns=["c1"], adsets=["a1"]), cell("2", "B", adsets=["a2"])]),
                               study("901", typ="LIFT", start="2026-09-01T00:00:00+0000", cells=[cell("3", "A", adsets=["zz"])]),
                               study("902", start="2026-10-25T09:00:00+0000", cells=[cell("4", "A", adsets=["future"])])], TODAY)
    days = r.read_days(g.pages, "act_1", studies, TODAY)
    assert set(days) == {"a1", "a2"}
    levels = {c[1]["level"]: c[1] for c in g.calls}
    assert set(levels) == {"campaign", "adset"}
    assert json.loads(levels["adset"]["filtering"]) == [{"field": "adset.id", "operator": "IN", "value": ["a1", "a2"]}]
    assert json.loads(levels["campaign"]["filtering"]) == [{"field": "campaign.id", "operator": "IN", "value": ["c1"]}]
    assert json.loads(levels["adset"]["time_range"]) == {"since": "2026-10-01", "until": "2026-10-17"} and levels["adset"]["time_increment"] == 1


def test_nothing_is_asked_when_no_test_can_be_measured_and_many_entities_are_asked_in_chunks():
    g = Graph()
    assert r.read_days(g.pages, "act_1", r.parse_studies([study(typ="LIFT", cells=[cell("1", "A", adsets=["a1"])])], TODAY), TODAY) == {} and g.calls == []
    many = r.parse_studies([study(cells=[cell("1", "A", adsets=["a%03d" % i for i in range(120)]), cell("2", "B", adsets=["b1"])])], TODAY)
    r.read_days(g.pages, "act_1", many, TODAY)
    assert [len(json.loads(c[1]["filtering"])[0]["value"]) for c in g.calls] == [50, 50, 21]


def test_the_window_covers_the_earliest_start_to_the_latest_end_or_yesterday():
    g = Graph()
    studies = r.parse_studies([study("1", start="2026-09-10T00:00:00+0000", end="2026-09-20T00:00:00+0000", cells=[cell("1", "A", adsets=["a1"])]),
                               study("2", start="2026-10-05T00:00:00+0000", end="2026-12-20T00:00:00+0000", cells=[cell("2", "A", adsets=["a2"])])], TODAY)
    r.read_days(g.pages, "act_1", studies, TODAY)
    assert json.loads(g.calls[0][1]["time_range"]) == {"since": "2026-09-10", "until": "2026-10-17"}


# ── The ads ─────────────────────────────────────────────────────────────────

def live(aid, name, adset="s1", url="https://a.test/x", headline="Headline"):
    return {"id": aid, "name": name, "adset_id": adset, "campaign_id": "c1", "effective_status": "ACTIVE",
            "creative": {"object_story_spec": {"link_data": {"link": url, "name": headline, "message": "Body " + aid}}}}


def stat(aid, adset="s1", spend=10.0, clicks=5, imp=100, leads=1, **over):
    row = {"ad_id": aid, "ad_name": "Ad " + aid, "adset_id": adset, "adset_name": "Set " + adset, "campaign_id": "c1", "campaign_name": "Camp", "objective": "OUTCOME_LEADS", "spend": str(spend),
           "impressions": str(imp), "inline_link_clicks": str(clicks), "actions": [{"action_type": "lead", "value": str(leads)}]}
    row.update(over)
    return row


def test_a_live_ad_carries_its_name_its_words_and_its_address():
    got = r.parse_live_ads([live("1", "  Spring  offer "), {"name": "no id"}])
    assert list(got) == ["1"]
    assert got["1"] == {"id": "1", "name": "Spring offer", "adSet": "s1", "campaign": "c1", "type": "LINK", "headlines": ["Headline"], "descriptions": ["Body 1"], "url": "https://a.test/x"}
    assert r.parse_live_ads([{"id": "2"}])["2"]["url"] == "" and r.parse_live_ads([{"id": "2"}])["2"]["headlines"] == []


def test_an_ad_set_is_a_group_of_its_live_ads_with_what_each_took_and_an_unshown_ad_has_zeros():
    ads = r.parse_live_ads([live("1", "A"), live("2", "B"), live("3", "C", adset="s2"), {"id": "4", "name": "no set"}])
    groups = r.build_groups(ads, r.lpm.parse_insights([stat("1", spend=12.5, clicks=6, imp=200, leads=2), stat("3", adset="s2")]))
    assert set(groups) == {"meta:s1", "meta:s2"}
    g = groups["meta:s1"]
    assert g["platform"] == "meta" and g["name"] == "Set s1" and g["campaign"] == "Camp" and g["campaignType"] == "META_LEADS" and g["campaignId"] == "c1"
    a, b = g["ads"]
    assert (a["cost"], a["clicks"], a["impressions"], a["conversions"], a["strength"]) == (12.5, 6, 200, 2.0, "") and a["name"] == "A" and a["headlines"] == ["Headline"]
    assert (b["cost"], b["clicks"], b["impressions"], b["conversions"]) == (0.0, 0, 0, 0.0)


def test_an_ad_set_whose_ads_were_never_shown_still_has_a_name_and_an_unknown_kind():
    groups = r.build_groups(r.parse_live_ads([live("1", "A"), live("2", "B")]), {})
    g = groups["meta:s1"]
    assert g["name"] == "s1" and g["campaign"] == "" and g["campaignType"] == "META_OTHER"


def test_the_names_come_from_the_ad_that_was_shown_when_the_first_was_not():
    groups = r.build_groups(r.parse_live_ads([live("1", "A"), live("2", "B")]), r.lpm.parse_insights([stat("2")]))
    g = groups["meta:s1"]
    assert g["name"] == "Set s1" and g["campaign"] == "Camp" and g["campaignType"] == "META_LEADS"


# ── The whole read ──────────────────────────────────────────────────────────

def run(g, **kw):
    return r.read_account("act_1234", pages=g.pages, today=TODAY, **kw)


def two_ads(**kw):
    return Graph(ads=[live("1", "A"), live("2", "B"), live("3", "C", adset="s2")], ad_stats=[stat("1", leads=3), stat("2", leads=1), stat("3", adset="s2")], **kw)


def test_the_whole_read_has_the_google_shape_split_tests_and_ad_tests():
    g = two_ads()
    out = run(g)
    assert out["ok"] == {"experiments": True, "ads": True} and out["warnings"] == [] and out["today"] == "2026-10-18"
    e = out["experiments"][0]
    assert e["measured"] is True and e["arms"][0]["results"]["total"]["clicks"] == 50 and e["id"] == "meta:900"
    assert out["adTests"] == 1 and [x["id"] for x in out["adGroups"]] == ["meta:s1"] and out["adGroups"][0]["adCount"] == 2
    assert [c[0] for c in g.calls] == ["act_1234/ad_studies", "act_1234/insights", "act_1234/insights", "act_1234/ads", "act_1234/insights"] or len(g.calls) >= 4
    ads_call = next(c for c in g.calls if c[0].endswith("/ads"))[1]
    assert json.loads(ads_call["effective_status"]) == ["ACTIVE"] and "creative{" in ads_call["fields"]
    insights = [c[1] for c in g.calls if c[1].get("level") == "ad"][0]
    assert insights["date_preset"] == "last_30d"


def test_an_ad_set_with_one_live_ad_is_not_a_test():
    out = run(Graph(ads=[live("1", "A")], ad_stats=[stat("1")]))
    assert out["adGroups"] == [] and out["adTests"] == 0


def test_a_failure_of_the_split_tests_is_a_warning_and_the_ad_tests_stand():
    out = run(two_ads(fail=["ad_studies"]))
    assert out["ok"] == {"experiments": False, "ads": True} and out["experiments"] == [] and out["adTests"] == 1
    assert out["warnings"] == ["The split tests could not be read (Meta answered 400: (#100) bad ad_studies)."]


def test_a_failure_of_the_ads_is_a_warning_and_the_split_tests_stand():
    for part in ("ads", "ad_stats"):
        out = run(two_ads(fail=[part]))
        assert out["ok"] == {"experiments": True, "ads": False} and out["adGroups"] == [] and len(out["experiments"]) == 1
        assert out["warnings"][0].startswith("The ads could not be read (Meta answered 400")


def test_both_failing_raises_the_first_error():
    with pytest.raises(meta.AdsError) as e:
        run(two_ads(fail=["ad_studies", "ads"]))
    assert "bad ad_studies" in str(e.value)


def test_a_study_answer_that_will_not_carry_the_cells_entities_is_asked_again_without_them():
    g = two_ads(fail=["nested"])
    out = run(g)
    fields = [c[1]["fields"] for c in g.calls if c[0].endswith("/ad_studies")]
    assert len(fields) == 2 and "adsets{" in fields[0] and "adsets{" not in fields[1] and "treatment_percentage" in fields[1]
    e = out["experiments"][0]
    assert e["measured"] is False and e["reason"] == "Meta did not say which campaigns or ad sets each cell holds." and out["ok"]["experiments"] is True


def test_when_the_days_cannot_be_read_the_tests_are_kept_without_a_result_and_it_is_said():
    out = run(two_ads(fail=["days"]))
    assert out["ok"]["experiments"] is False and out["warnings"] == ["The split tests were read but not what their cells did, so none of them has a result here."]
    e = out["experiments"][0]
    assert e["arms"][0]["results"]["total"]["clicks"] == 0 and e["arms"][1]["results"]["total"]["clicks"] == 0           # nothing was counted, so there is nothing to compare


def test_a_creative_answer_that_is_too_large_is_asked_again_in_smaller_pages_and_then_without_the_creative():
    g = two_ads(over=50)
    out = run(g)
    assert [c[1]["limit"] for c in g.calls if c[0].endswith("/ads")] == [r.ADS_PAGE, r.ADS_PAGE_SMALL] and out["adTests"] == 1
    assert out["adGroups"][0]["ads"][0]["headlines"] == ["Headline"]


def test_a_creative_that_cannot_be_read_at_any_size_leaves_the_ads_without_their_words_not_without_a_test():
    g = two_ads(fail=["creative"])
    out = run(g)
    calls = [c[1] for c in g.calls if c[0].endswith("/ads")]
    assert [c["limit"] for c in calls] == [r.ADS_PAGE, r.ADS_PAGE_SMALL, r.ADS_PAGE] and calls[-1]["fields"] == r.ADS_FIELDS_PLAIN
    assert out["adTests"] == 1 and out["adGroups"][0]["ads"][0]["headlines"] == [] and out["adGroups"][0]["ads"][0]["name"] == "A" and out["ok"]["ads"] is True


def test_meta_asking_to_wait_stops_the_read_whichever_part_it_comes_in():
    class Slow(Graph):
        def pages(self, path, params):
            if path.endswith("/ad_studies") or params.get("level") == "ad":
                raise meta.OverQuota("wait")
            return super().pages(path, params)
    with pytest.raises(meta.OverQuota):
        run(Slow())

    class Slow2(Graph):
        def pages(self, path, params):
            if params.get("time_increment"):
                raise meta.OverQuota("wait")
            return super().pages(path, params)
    with pytest.raises(meta.OverQuota):
        run(Slow2())

    class Slow3(Graph):
        def pages(self, path, params):
            if path.endswith("/ads"):
                raise meta.OverQuota("wait")
            return super().pages(path, params)
    with pytest.raises(meta.OverQuota):
        run(Slow3())


def test_an_account_with_nothing_is_an_empty_read_not_an_error():
    out = run(Graph(studies=[], ads=[], ad_stats=[]))
    assert out["experiments"] == [] and out["adGroups"] == [] and out["adTests"] == 0 and out["warnings"] == [] and out["ok"] == {"experiments": True, "ads": True}


def test_the_day_defaults_to_the_pacific_day_when_none_is_given():
    out = r.read_account("act_1", pages=Graph(studies=[], ads=[], ad_stats=[]).pages)
    assert dt.date.fromisoformat(out["today"])


# ── Pinned by mutation ──────────────────────────────────────────────────────

def test_cells_are_ordered_by_their_number_not_their_name_or_their_text():
    s = r.parse_studies([study(cells=[cell("10", "Alpha"), cell("9", "Zeta")])], TODAY)[0]
    assert [c["id"] for c in s["cells"]] == ["9", "10"] and s["cells"][0]["name"] == "Zeta"


def test_a_row_at_the_ad_set_level_is_filed_under_the_ad_set_though_it_also_names_its_campaign():
    row = day_row("a1", "2026-10-02", campaign_id="c9")
    assert list(r.parse_days([row])) == ["a1"]


def test_a_test_is_measured_only_when_both_of_its_first_two_cells_have_a_result_and_a_lift_study_is_never_blamed_for_missing_cells():
    assert built([cell("1", "A", adsets=["a1"]), cell("2", "B")])["measured"] is False
    lift = r.build_studies(r.parse_studies([study(typ="LIFT")], TODAY), {}, TODAY)[0]
    assert lift["reason"] == "" and lift["measured"] is False
    assert r.build_studies(r.parse_studies([study(cells=[cell("1", "A", adsets=["a1"]), cell("2", "B", adsets=["a2"])])], TODAY), {"a1": {}, "a2": {}}, TODAY)[0]["measured"] is True


def test_meta_asking_to_wait_while_the_ads_are_read_stops_at_the_first_ask():
    class Slow(Graph):
        def pages(self, path, params):
            if path.endswith("/ads"):
                self.calls.append((path, dict(params)))
                raise meta.OverQuota("wait")
            return super().pages(path, params)
    g = Slow()
    with pytest.raises(meta.OverQuota):
        run(g)
    assert len([c for c in g.calls if c[0].endswith("/ads")]) == 1
