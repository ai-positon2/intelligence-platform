"""Test & Experiment Tracker: reading the experiments and the ads from Google Ads."""
import datetime as dt

import pytest

from tracker import paid_exp_read as rd
from tracker import paid_gads_segments as gads

TODAY = dt.date(2026, 10, 18)
CUST = "customers/1234567890"


def exp_row(i=1, name="Headline test", status="ENABLED", typ="SEARCH_CUSTOM", start="2026-09-20", end="2026-10-25", desc=""):
    return {"experiment": {"resourceName": "%s/experiments/%d" % (CUST, i), "experimentId": str(i), "name": name, "description": desc, "status": status, "type": typ,
                           "startDate": start, "endDate": end}}


def arm_row(i, name, control, split, *campaigns):
    return {"experimentArm": {"resourceName": "%s/experimentArms/%d~%s" % (CUST, i, name), "experiment": "%s/experiments/%d" % (CUST, i), "name": name, "control": control,
                              "trafficSplit": split, "campaigns": ["%s/campaigns/%s" % (CUST, c) for c in campaigns]}}


def camp_row(cid, name, typ="SEARCH", exp="BASE", base=""):
    return {"campaign": {"resourceName": "%s/campaigns/%s" % (CUST, cid), "id": str(cid), "name": name, "status": "ENABLED", "advertisingChannelType": typ, "experimentType": exp, "baseCampaign": base}}


def day_row(cid, day, impr=1000, clicks=50, micros=100_000_000, conv=5, value=0):
    return {"campaign": {"id": str(cid)}, "segments": {"date": day}, "metrics": {"impressions": str(impr), "clicks": str(clicks), "costMicros": str(micros), "conversions": conv,
                                                                                "conversionsValue": value}}


def ad_row(gid, aid, impr=1000, clicks=50, micros=50_000_000, conv=5, cid="11", exp="BASE", heads=("Fix your teeth", "Book today"), strength="GOOD", gname="Implants", cname="Search Implants"):
    return {"campaign": {"id": cid, "name": cname, "advertisingChannelType": "SEARCH", "experimentType": exp}, "adGroup": {"id": str(gid), "name": gname},
            "adGroupAd": {"ad": {"id": str(aid), "type": "RESPONSIVE_SEARCH_AD", "finalUrls": ["https://x.com/a"], "responsiveSearchAd": {
                "headlines": [{"text": h} for h in heads], "descriptions": [{"text": "Call us"}]}}, "adStrength": strength},
            "metrics": {"impressions": str(impr), "clicks": str(clicks), "costMicros": str(micros), "conversions": conv, "conversionsValue": 0}}


# ── parsing ─────────────────────────────────────────────────────────────────

def test_experiments_are_parsed_and_newest_first():
    out = rd.parse_experiments([exp_row(1, "Old", start="2026-01-05"), exp_row(2, "New", start="2026-09-20", status="INITIATED"), {"experiment": {"name": "no resource"}}, {}])
    assert [e["name"] for e in out] == ["New", "Old"]
    new = out[0]
    assert new["id"] == "2" and new["state"] == "running" and new["typeLabel"] == "Search custom" and new["start"] == "2026-09-20" and new["end"] == "2026-10-25"


def test_statuses_map_to_plain_words_and_unknown_ones_pass_through():
    s = {x["status"]: x["state"] for x in rd.parse_experiments([exp_row(1, status="HALTED"), exp_row(2, status="GRADUATED"), exp_row(3, status="SETUP"), exp_row(4, status="NEW_THING")])}
    assert s == {"HALTED": "ended", "GRADUATED": "applied", "SETUP": "setting up", "NEW_THING": "new_thing"}


def test_an_unknown_type_is_made_readable_and_a_missing_one_is_empty():
    out = {e["id"]: e["typeLabel"] for e in rd.parse_experiments([exp_row(1, typ="SOME_NEW_TYPE"), exp_row(2, typ="")])}
    assert out == {"1": "Some new type", "2": ""}


def test_an_experiment_with_no_end_date_has_none():
    assert rd.parse_experiments([exp_row(1, end="2037-12-30")])[0]["end"] is None
    assert rd.parse_experiments([exp_row(1, end="")])[0]["end"] is None
    assert rd.parse_experiments([exp_row(1, start="not a date")])[0]["start"] is None


def test_arms_come_back_control_first_with_their_campaigns():
    arms = rd.parse_arms([arm_row(1, "Trial", False, 50, 22), arm_row(1, "Base", True, 50, 11), {"experimentArm": {"name": "orphan"}}])
    got = arms["%s/experiments/1" % CUST]
    assert list(arms) == ["%s/experiments/1" % CUST]
    assert [a["name"] for a in got] == ["Base", "Trial"] and got[0]["control"] and got[1]["split"] == 50
    assert got[1]["campaigns"] == ["%s/campaigns/22" % CUST]


def test_campaigns_are_kept_by_resource_name():
    c = rd.parse_campaigns([camp_row(22, "Trial", exp="EXPERIMENT", base="%s/campaigns/11" % CUST), {"campaign": {"id": "5"}}])
    assert list(c) == ["%s/campaigns/22" % CUST] and c["%s/campaigns/22" % CUST]["experimentType"] == "EXPERIMENT"


def test_days_are_kept_per_campaign_per_date_and_junk_rows_are_ignored():
    d = rd.parse_days([day_row(11, "2026-10-01", 1000, 50, 12_340_000, 2.5, 99.5), day_row(11, "2026-10-02"), {"campaign": {"id": "11"}}, {"segments": {"date": "2026-10-01"}}])
    assert d["11"]["2026-10-01"] == [1000, 50, 12.34, 2.5, 99.5] and len(d["11"]) == 2


# ── windows and sums ────────────────────────────────────────────────────────

def e(start, end=None):
    return {"start": start, "end": end}


def test_the_window_runs_from_the_start_to_the_earlier_of_the_end_and_yesterday():
    assert rd._window(e("2026-10-01", "2026-10-10"), TODAY) == (dt.date(2026, 10, 1), dt.date(2026, 10, 10))
    assert rd._window(e("2026-10-01", "2026-10-30"), TODAY) == (dt.date(2026, 10, 1), dt.date(2026, 10, 17))
    assert rd._window(e("2026-10-01"), TODAY) == (dt.date(2026, 10, 1), dt.date(2026, 10, 17))


def test_an_experiment_that_has_not_started_has_no_window():
    assert rd._window(e("2026-10-18"), TODAY) is None          # starts today: no full day yet
    assert rd._window(e("2026-11-01"), TODAY) is None
    assert rd._window(e(None), TODAY) is None
    assert rd._window(e("2026-10-17"), TODAY) == (dt.date(2026, 10, 17), dt.date(2026, 10, 17))


def test_an_experiment_that_ended_before_it_started_has_no_window():
    assert rd._window(e("2026-10-10", "2026-10-05"), TODAY) is None


def test_a_very_old_start_is_clipped_to_the_lookback():
    first, last = rd._window(e("2020-01-01"), TODAY)
    assert last == dt.date(2026, 10, 17) and (last - first).days == rd.MAX_LOOKBACK_DAYS - 1


def test_sum_arm_adds_campaigns_by_date_and_keeps_only_the_window_inclusive():
    days = {"1": {"2026-10-01": [100, 10, 5.0, 1.0, 0.0], "2026-10-02": [200, 20, 6.0, 2.0, 10.0], "2026-10-03": [300, 30, 7.0, 3.0, 0.0]},
            "2": {"2026-10-02": [50, 5, 1.5, 0.5, 5.0]}}
    got = rd.sum_arm(days, ["1", "2"], dt.date(2026, 10, 2), dt.date(2026, 10, 3))
    assert got["days"] == {"2026-10-02": [250, 25, 7.5, 2.5, 15.0], "2026-10-03": [300, 30, 7.0, 3.0, 0.0]}
    assert got["total"] == {"impressions": 550, "clicks": 55, "cost": 14.5, "conversions": 5.5, "value": 15.0}
    empty = rd.sum_arm(days, ["9"], dt.date(2026, 10, 1), dt.date(2026, 10, 3))
    assert empty["total"]["impressions"] == 0 and empty["days"] == {}


def test_sum_arm_skips_a_bad_date():
    assert rd.sum_arm({"1": {"junk": [1, 1, 1.0, 1.0, 1.0]}}, ["1"], dt.date(2026, 1, 1), dt.date(2026, 12, 31))["total"]["clicks"] == 0


# ── putting an experiment together ──────────────────────────────────────────

def built(*exps, arms=None, camps=None, days=None):
    return rd.build_experiments(rd.parse_experiments(list(exps)), rd.parse_arms(arms or []), rd.parse_campaigns(camps or []), days or {}, TODAY)


def test_a_running_experiment_is_measured_over_its_days():
    days = rd.parse_days([day_row(11, "2026-10-01", 1000, 50), day_row(22, "2026-10-01", 900, 60), day_row(11, "2026-09-01", 5000, 500)])
    out = built(exp_row(1, start="2026-10-01", end="2026-10-30"), arms=[arm_row(1, "Base", True, 50, 11), arm_row(1, "Trial", False, 50, 22)],
                camps=[camp_row(11, "Search"), camp_row(22, "Search [trial]", exp="EXPERIMENT")], days=days)[0]
    assert out["measured"] and out["window"] == ["2026-10-01", "2026-10-17"] and not out["clipped"]
    control, trial = out["arms"]
    assert control["control"] and control["results"]["total"]["impressions"] == 1000        # the 5,000 on 1 September is before the start
    assert trial["results"]["total"]["clicks"] == 60 and trial["campaigns"] == [{"id": "22", "name": "Search [trial]", "type": "SEARCH"}]


def test_an_old_experiment_is_marked_clipped():
    out = built(exp_row(1, start="2020-01-01", end=""), arms=[arm_row(1, "Base", True, 50, 11), arm_row(1, "Trial", False, 50, 22)], camps=[camp_row(11, "A"), camp_row(22, "B")])[0]
    assert out["clipped"] and out["window"][0] != "2020-01-01"


def test_an_experiment_with_no_arm_campaigns_is_listed_but_not_measured():
    out = built(exp_row(1, typ="AD_VARIATION"))[0]
    assert out["arms"] == [] and out["measured"] is False and out["typeLabel"] == "Ad variations"


@pytest.mark.parametrize("typ,label", [("OPTIMIZE_ASSETS", "Optimize assets"), ("ADOPT_AI_MAX", "Adopt AI Max"), ("ADOPT_BROAD_MATCH_KEYWORDS", "Adopt broad match keywords"),
                                       ("PMAX_REPLACEMENT_SHOPPING", "Performance Max replacing Shopping")])
def test_the_experiment_types_added_in_v24_have_a_plain_label(typ, label):
    assert built(exp_row(1, typ=typ))[0]["typeLabel"] == label


def test_an_experiment_that_has_not_started_is_not_measured():
    out = built(exp_row(1, start="2026-11-01", end="2026-11-30"), arms=[arm_row(1, "Base", True, 50, 11), arm_row(1, "Trial", False, 50, 22)], camps=[camp_row(11, "A"), camp_row(22, "B")])[0]
    assert out["window"] is None and out["measured"] is False


def test_an_experiment_needs_both_a_control_and_a_trial_to_be_measured():
    out = built(exp_row(1), arms=[arm_row(1, "Base", True, 50, 11)], camps=[camp_row(11, "A")])[0]
    assert out["measured"] is False
    only_trial = built(exp_row(1), arms=[arm_row(1, "Trial", False, 50, 22)], camps=[camp_row(22, "A")])[0]
    assert only_trial["measured"] is False


def test_an_arm_whose_campaign_is_gone_has_no_results():
    out = built(exp_row(1), arms=[arm_row(1, "Base", True, 50, 11), arm_row(1, "Trial", False, 50, 22)], camps=[camp_row(11, "A")])[0]
    assert out["arms"][1]["results"] is None and out["measured"] is False


def test_which_campaigns_to_read_and_for_which_dates():
    exps = rd.parse_experiments([exp_row(1, start="2026-10-01", end="2026-10-10"), exp_row(2, start="2026-09-15", end="2026-11-30"), exp_row(3, start="2026-12-01")])
    arms = rd.parse_arms([arm_row(1, "B", True, 50, 11), arm_row(1, "T", False, 50, 22), arm_row(2, "B", True, 50, 33), arm_row(2, "T", False, 50, 44), arm_row(3, "B", True, 50, 55)])
    camps = rd.parse_campaigns([camp_row(c, str(c)) for c in (11, 22, 33, 44, 55)])
    ids, first, last = rd.experiment_campaign_ids(exps, arms, camps, TODAY)
    assert ids == {"11", "22", "33", "44"} and first == dt.date(2026, 9, 15) and last == dt.date(2026, 10, 17)
    assert rd.experiment_campaign_ids(exps[:0], arms, camps, TODAY) is None


def test_the_days_query_names_the_campaigns_and_the_dates():
    q = rd.days_gaql({"22", "11"}, dt.date(2026, 10, 1), dt.date(2026, 10, 17))
    assert "campaign.id IN (11, 22)" in q and "BETWEEN '2026-10-01' AND '2026-10-17'" in q and "FROM campaign" in q


# ── ads ─────────────────────────────────────────────────────────────────────

def test_ads_are_grouped_by_ad_group_with_their_text_and_numbers():
    g = rd.parse_ads([ad_row(5, 1, heads=("A", "A", "B")), ad_row(5, 2, heads=("C",))])
    assert list(g) == ["5"] and len(g["5"]["ads"]) == 2
    first = g["5"]["ads"][0]
    assert first["headlines"] == ["A", "B"] and first["descriptions"] == ["Call us"] and first["cost"] == 50.0 and first["url"] == "https://x.com/a" and first["strength"] == "GOOD"
    assert g["5"]["campaign"] == "Search Implants" and g["5"]["campaignType"] == "SEARCH"


def test_ads_in_a_trial_campaign_are_not_ad_tests_of_their_own():
    rows = [ad_row(5, 1), ad_row(5, 2), ad_row(6, 3, cid="22", exp="EXPERIMENT"), ad_row(7, 4, cid="33")]
    assert set(rd.parse_ads(rows)) == {"5", "7"}
    assert set(rd.parse_ads(rows, {"33"})) == {"5"}


def test_a_row_without_an_ad_group_or_an_ad_is_skipped():
    no_group, no_ad = ad_row(1, 1), ad_row(1, 1)
    no_group["adGroup"] = {}
    no_ad["adGroupAd"]["ad"].pop("id")
    assert rd.parse_ads([{"campaign": {"id": "1"}}, no_group, no_ad]) == {}


def test_only_ad_groups_with_two_ads_are_tests_biggest_spenders_first():
    groups = rd.parse_ads([ad_row(5, 1, micros=10_000_000), ad_row(5, 2, micros=20_000_000), ad_row(6, 3), ad_row(7, 4, micros=90_000_000), ad_row(7, 5, micros=90_000_000)])
    kept, total = rd.compact_groups(groups)
    assert total == 2 and [g["id"] for g in kept] == ["7", "5"]
    assert kept[1]["cost"] == 30.0 and kept[1]["adCount"] == 2 and [a["id"] for a in kept[1]["ads"]] == ["2", "1"]


def test_compacting_caps_the_groups_and_the_ads():
    rows = [ad_row(g, g * 100 + a, micros=(g + 1) * 1_000_000) for g in range(1, 6) for a in range(1, 5)]
    kept, total = rd.compact_groups(rd.parse_ads(rows), keep=3, per_group=2)
    assert total == 5 and len(kept) == 3 and [g["id"] for g in kept] == ["5", "4", "3"] and all(len(g["ads"]) == 2 and g["adCount"] == 4 for g in kept)


# ── the whole read ──────────────────────────────────────────────────────────

class Answers:
    def __init__(self, **by):
        self.by, self.queries = by, []

    def __call__(self, account, gaql):
        self.queries.append(gaql)
        for key, val in self.by.items():
            if gaql.startswith(rd_prefix[key]):
                if isinstance(val, Exception):
                    raise val
                return val
        raise AssertionError("unexpected query: " + gaql[:80])


rd_prefix = {"experiments": "SELECT experiment.resource_name", "arms": "SELECT experiment_arm", "campaigns": "SELECT campaign.resource_name", "days": "SELECT campaign.id, segments.date",
             "ads": "SELECT campaign.id, campaign.name"}


def full():
    return dict(experiments=[exp_row(1, start="2026-10-01", end="2026-10-30")], arms=[arm_row(1, "Base", True, 50, 11), arm_row(1, "Trial", False, 50, 22)],
                campaigns=[camp_row(11, "Base"), camp_row(22, "Trial", exp="EXPERIMENT")], days=[day_row(11, "2026-10-01"), day_row(22, "2026-10-01", 900)],
                ads=[ad_row(5, 1), ad_row(5, 2), ad_row(9, 3, cid="22", exp="EXPERIMENT"), ad_row(9, 4, cid="22", exp="EXPERIMENT")])


def test_a_full_read_returns_experiments_and_ad_tests():
    ans = Answers(**full())
    out = rd.read_account("1234567890", search_fn=ans, today=TODAY)
    assert out["ok"] == {"experiments": True, "ads": True} and out["warnings"] == [] and out["today"] == "2026-10-18"
    assert len(out["experiments"]) == 1 and out["experiments"][0]["measured"]
    assert out["adTests"] == 1 and [g["id"] for g in out["adGroups"]] == ["5"]


def test_the_account_with_no_experiments_reads_only_the_ads():
    ans = Answers(**dict(full(), experiments=[]))
    out = rd.read_account("1234567890", search_fn=ans, today=TODAY)
    assert out["experiments"] == [] and out["adTests"] == 1
    assert not any(q.startswith("SELECT experiment_arm") for q in ans.queries)


def test_trial_campaigns_are_left_out_of_the_ads_even_when_their_rows_say_nothing():
    plain = dict(full(), ads=[ad_row(9, 3, cid="22"), ad_row(9, 4, cid="22"), ad_row(5, 1), ad_row(5, 2)])
    out = rd.read_account("1234567890", search_fn=Answers(**plain), today=TODAY)
    assert [g["id"] for g in out["adGroups"]] == ["5"]


def test_experiments_failing_is_a_warning_and_the_ads_are_still_read():
    err = gads.GadsError("upstream", "boom")
    out = rd.read_account("1234567890", search_fn=Answers(**dict(full(), experiments=err)), today=TODAY)
    assert out["ok"] == {"experiments": False, "ads": True} and "experiments could not be read" in out["warnings"][0] and out["experiments"] == []
    assert out["adTests"] == 1


def test_ads_failing_is_a_warning_and_the_experiments_are_still_read():
    err = gads.GadsError("upstream", "boom")
    out = rd.read_account("1234567890", search_fn=Answers(**dict(full(), ads=err)), today=TODAY)
    assert out["ok"] == {"experiments": True, "ads": False} and "ads could not be read" in out["warnings"][0] and len(out["experiments"]) == 1


def test_both_failing_is_raised():
    err = gads.GadsError("upstream", "boom")
    with pytest.raises(gads.GadsError):
        rd.read_account("1234567890", search_fn=Answers(**dict(full(), experiments=err, ads=err)), today=TODAY)


def test_days_failing_keeps_the_experiments_but_gives_them_no_results():
    err = gads.GadsError("upstream", "boom")
    out = rd.read_account("1234567890", search_fn=Answers(**dict(full(), days=err)), today=TODAY)
    assert out["ok"]["experiments"] is False and "not what their arms did" in out["warnings"][0]
    assert len(out["experiments"]) == 1 and out["experiments"][0]["arms"][0]["results"]["total"]["impressions"] == 0


def test_many_campaigns_are_read_in_chunks():
    n = rd.CAMPAIGN_IDS_PER_QUERY + 5
    exps, arms, camps = [], [], []
    for i in range(1, n + 1):
        exps.append(exp_row(i, start="2026-10-01"))
        arms += [arm_row(i, "Base", True, 50, 1000 + i), arm_row(i, "Trial", False, 50, 5000 + i)]
        camps += [camp_row(1000 + i, "b%d" % i), camp_row(5000 + i, "t%d" % i, exp="EXPERIMENT")]
    ans = Answers(experiments=exps, arms=arms, campaigns=camps, days=[], ads=[])
    rd.read_account("1234567890", search_fn=ans, today=TODAY)
    day_queries = [q for q in ans.queries if q.startswith("SELECT campaign.id, segments.date")]
    assert len(day_queries) == 3 and all(len(q.split("IN (")[1].split(")")[0].split(",")) <= rd.CAMPAIGN_IDS_PER_QUERY for q in day_queries)


def test_a_started_experiment_with_no_campaigns_asks_for_no_days():
    ans = Answers(experiments=[exp_row(1, typ="AD_VARIATION", start="2026-10-01")], arms=[], campaigns=[], ads=[])
    out = rd.read_account("1234567890", search_fn=ans, today=TODAY)
    assert not any(q.startswith("SELECT campaign.id, segments.date") for q in ans.queries) and out["experiments"][0]["measured"] is False


def test_the_queries_only_read():
    for q in (rd.EXPERIMENTS_GAQL, rd.ARMS_GAQL, rd.CAMPAIGNS_GAQL, rd.ADS_GAQL):
        assert q.startswith("SELECT ") and " FROM " in q
        assert not any(w in q.upper() for w in ("MUTATE", "UPDATE ", "INSERT", "REMOVE "))


def test_an_arm_with_no_split_has_none_and_empty_campaign_names_are_dropped():
    row = arm_row(1, "Base", True, 50, 11)
    row["experimentArm"].pop("trafficSplit")
    row["experimentArm"]["campaigns"].append("")
    got = rd.parse_arms([row])["%s/experiments/1" % CUST][0]
    assert got["split"] is None and got["campaigns"] == ["%s/campaigns/11" % CUST]


def test_an_experiment_with_no_campaigns_does_not_widen_the_dates_to_read():
    exps = rd.parse_experiments([exp_row(1, start="2026-10-01"), exp_row(2, typ="AD_VARIATION", start="2026-03-01"), exp_row(3, typ="AD_VARIATION", start="2026-10-17")])
    arms = rd.parse_arms([arm_row(1, "B", True, 50, 11), arm_row(1, "T", False, 50, 22)])
    camps = rd.parse_campaigns([camp_row(11, "a"), camp_row(22, "b")])
    ids, first, last = rd.experiment_campaign_ids(exps, arms, camps, TODAY)
    assert first == dt.date(2026, 10, 1) and last == dt.date(2026, 10, 17)


def test_the_days_query_ends_at_the_last_day():
    q = rd.days_gaql({"1"}, dt.date(2026, 10, 1), dt.date(2026, 10, 17))
    assert q.endswith("BETWEEN '2026-10-01' AND '2026-10-17'")


def test_every_campaign_is_read_exactly_once_and_every_chunk_is_kept():
    n = rd.CAMPAIGN_IDS_PER_QUERY * 2 + 5
    exps, arms, camps, rows = [], [], [], []
    for i in range(1, n + 1):
        exps.append(exp_row(i, start="2026-10-01"))
        arms += [arm_row(i, "Base", True, 50, 1000 + i), arm_row(i, "Trial", False, 50, 5000 + i)]
        camps += [camp_row(1000 + i, "b%d" % i), camp_row(5000 + i, "t%d" % i, exp="EXPERIMENT")]
        rows += [day_row(1000 + i, "2026-10-01", 100 + i), day_row(5000 + i, "2026-10-01", 200 + i)]
    asked = []

    def search(account, gaql):
        if gaql.startswith("SELECT campaign.id, segments.date"):
            ids = {x.strip() for x in gaql.split("IN (")[1].split(")")[0].split(",")}
            asked.extend(ids)
            return [r for r in rows if r["campaign"]["id"] in ids]
        return {"SELECT experiment.resource_name": exps, "SELECT experiment_arm": arms, "SELECT campaign.resource_name": camps}.get(next((k for k in (
            "SELECT experiment.resource_name", "SELECT experiment_arm", "SELECT campaign.resource_name") if gaql.startswith(k)), ""), [])
    out = rd.read_account("1234567890", search_fn=search, today=TODAY)
    want = {str(1000 + i) for i in range(1, n + 1)} | {str(5000 + i) for i in range(1, n + 1)}
    assert sorted(asked) == sorted(want)
    for e in out["experiments"]:
        assert all(a["results"]["total"]["impressions"] > 0 for a in e["arms"])
