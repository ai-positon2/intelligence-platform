"""Test & Experiment Tracker: one read from the platforms read (tracker/paid_exp_merge.py)."""
import copy

from tracker import paid_exp_merge as mg


def exp(eid, start, name="E"):
    return {"id": eid, "resource": eid, "name": name, "start": start, "arms": [], "state": "running"}


def grp(gid, cost):
    return {"id": gid, "name": gid, "cost": cost, "ads": [], "adCount": 2}


def reads():
    g = {"experiments": [exp("111", "2026-09-01", "G1"), exp("222", "2026-10-05", "G2")], "adGroups": [grp("g1", 50.0), grp("g2", 10.0)], "adTests": 5,
         "ok": {"experiments": True, "ads": False}, "warnings": ["The ads could not be read (x)."], "today": "2026-10-18"}
    m = {"experiments": [exp("meta:9", "2026-10-01", "M1")], "adGroups": [grp("meta:s1", 30.0)], "adTests": 2, "ok": {"experiments": False, "ads": True},
         "warnings": ["The split tests could not be read (y)."], "today": "2026-10-18"}
    return copy.deepcopy(g), copy.deepcopy(m)


def test_one_platform_alone_is_its_own_read_with_the_platform_named():
    g, _m = reads()
    out = mg.merge(g, None)
    assert out["platforms"] == ["google"] and out["experiments"] == g["experiments"] and out["ok"] == g["ok"] and "platforms" not in g


def test_a_meta_read_alone_names_its_flags_and_warnings():
    _g, m = reads()
    out = mg.merge(None, m)
    assert out["platforms"] == ["meta"] and out["ok"] == {"meta:experiments": False, "meta:ads": True}
    assert out["warnings"] == ["Meta: The split tests could not be read (y)."] and out["adTests"] == 2 and out["today"] == "2026-10-18"
    assert [dict(e) for e in out["experiments"]] == [dict(e, platform="meta") for e in m["experiments"]] and out["adGroups"][0]["platform"] == "meta"


def test_two_reads_are_one_list_newest_first_with_each_experiment_marked_by_its_platform():
    g, m = reads()
    out = mg.merge(g, m)
    assert [(e["id"], e["platform"]) for e in out["experiments"]] == [("222", "google"), ("meta:9", "meta"), ("111", "google")]
    assert out["platforms"] == ["google", "meta"] and out["today"] == "2026-10-18"


def test_the_ad_tests_of_both_are_listed_by_what_they_spent_and_counted_together():
    g, m = reads()
    out = mg.merge(g, m)
    assert [(x["id"], x["platform"]) for x in out["adGroups"]] == [("g1", "google"), ("meta:s1", "meta"), ("g2", "google")] and out["adTests"] == 7


def test_only_the_busiest_ad_tests_are_kept_and_the_count_still_says_how_many_there_were():
    g, m = reads()
    out = mg.merge(g, m, keep=2)
    assert [x["id"] for x in out["adGroups"]] == ["g1", "meta:s1"] and out["adTests"] == 7


def test_the_flags_and_warnings_of_the_two_platforms_stay_apart():
    g, m = reads()
    out = mg.merge(g, m)
    assert out["ok"] == {"experiments": True, "ads": False, "meta:experiments": False, "meta:ads": True}
    assert out["warnings"] == ["The ads could not be read (x).", "Meta: The split tests could not be read (y)."]


def test_the_reads_given_are_not_changed():
    g, m = reads()
    g0, m0 = copy.deepcopy(g), copy.deepcopy(m)
    mg.merge(g, m)
    assert g == g0 and m == m0
