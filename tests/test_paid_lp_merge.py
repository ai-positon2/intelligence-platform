"""Landing Page & Relevancy Check: one read from the platforms read (tracker/paid_lp_merge.py)."""
import copy

from tracker import paid_lp_merge as mg


def gpage(pid, cost, groups=(), **over):
    p = {"id": pid, "name": "www." + pid, "templated": False, "url": "https://www." + pid, "impressions": 1000, "clicks": 100, "cost": cost, "conversions": 5.0, "value": 50.0,
         "speed": 8, "devices": {"mobile": {"clicks": 70, "cost": cost * 0.7, "conversions": 3.0}, "desktop": {"clicks": 30, "cost": cost * 0.3, "conversions": 2.0}}, "adGroups": list(groups)}
    p.update(over)
    return p


def mpage(pid, cost, groups=(), **over):
    p = gpage(pid, cost, groups, url="https://" + pid, name=pid, speed=None, platforms=["meta"], clicks=40, impressions=800, conversions=2.0, value=0.0,
              devices={"mobile": {"clicks": 30, "cost": cost * 0.8, "conversions": 1.0}, "tablet": {"clicks": 10, "cost": cost * 0.2, "conversions": 1.0}})
    p.update(over)
    return p


def group(gid, cost, pages, platform=None, **over):
    g = {"id": gid, "name": gid, "campaign": "c", "campaignType": "META_LEADS" if platform else "SEARCH", "cost": cost, "clicks": 1, "conversions": 0.0, "ads": [], "keywords": [], "terms": [],
         "pages": dict(pages)}
    if platform:
        g["platform"] = platform
    g.update(over)
    return g


def reads():
    g = {"pages": [gpage("a.test/x", 300.0, ["g1"]), gpage("a.test/y", 100.0, ["g2"])], "pageCount": 5, "pageCost": 700.0,
         "adGroups": {"g1": group("g1", 300.0, {"a.test/x": 300.0}), "g2": group("g2", 100.0, {"a.test/y": 100.0})}, "ok": {"pages": True, "ads": True, "keywords": False, "terms": True},
         "warnings": ["The keywords and their Quality Score could not be read (x)."]}
    m = {"pages": [mpage("a.test/x", 200.0, ["meta:s1"]), mpage("b.test/z", 150.0, ["meta:s2"])], "pageCount": 3, "pageCost": 400.0,
         "adGroups": {"meta:s1": group("meta:s1", 200.0, {"a.test/x": 200.0}, "meta"), "meta:s2": group("meta:s2", 150.0, {"b.test/z": 150.0}, "meta")},
         "ok": {"ads": True, "insights": True, "rankings": False}, "warnings": ["Meta's own ratings of the ads could not be read (y)."],
         "off": {"spend": 30.0, "ads": 2, "kinds": [{"kind": "an instant form", "spend": 30.0, "ads": 2}], "total": 430.0}}
    return copy.deepcopy(g), copy.deepcopy(m)


def test_one_platform_alone_is_its_own_read_with_the_platforms_named():
    g, _m = reads()
    out = mg.merge(g, None)
    assert out["platforms"] == ["google"] and out["pages"] == g["pages"] and out["ok"] == g["ok"] and out["warnings"] == g["warnings"]
    assert "platforms" not in g                                            # the Google read itself is not changed


def test_a_meta_read_alone_names_its_flags_and_warnings_so_they_cannot_be_taken_for_googles():
    _g, m = reads()
    out = mg.merge(None, m)
    assert out["platforms"] == ["meta"] and out["ok"] == {"meta:ads": True, "meta:insights": True, "meta:rankings": False}
    assert out["warnings"] == ["Meta: Meta's own ratings of the ads could not be read (y)."] and out["off"]["spend"] == 30.0
    assert [p["id"] for p in out["pages"]] == ["a.test/x", "b.test/z"] and out["adGroups"] == m["adGroups"]


def test_a_page_both_platforms_reach_is_one_page_with_the_spend_clicks_results_and_devices_added():
    g, m = reads()
    out = mg.merge(g, m)
    assert [p["id"] for p in out["pages"]] == ["a.test/x", "b.test/z", "a.test/y"]
    x = out["pages"][0]
    assert x["platforms"] == ["google", "meta"] and x["cost"] == 500.0 and x["clicks"] == 140 and x["impressions"] == 1800 and x["conversions"] == 7.0 and x["value"] == 50.0
    assert x["speed"] == 8                                                  # Google's own score stays Google's
    assert x["devices"]["mobile"] == {"clicks": 100, "cost": round(210.0 + 160.0, 2), "conversions": 4.0}
    assert x["devices"]["desktop"]["clicks"] == 30 and x["devices"]["tablet"] == {"clicks": 10, "cost": 40.0, "conversions": 1.0}
    assert x["adGroups"] == ["g1", "meta:s1"]                               # the bigger spender first
    assert out["pages"][1]["platforms"] == ["meta"] and out["pages"][2]["platforms"] == ["google"]


def test_the_address_shown_is_the_one_the_bigger_spender_sends_people_to():
    g, m = reads()
    assert mg.merge(g, m)["pages"][0]["url"] == "https://www.a.test/x"
    g, m = reads()
    m["pages"][0]["cost"] = 400.0
    assert mg.merge(g, m)["pages"][0]["url"] == "https://a.test/x"
    g, m = reads()
    m["pages"][0]["templated"] = True
    assert mg.merge(g, m)["pages"][0]["templated"] is True


def test_the_flags_and_warnings_of_the_two_platforms_stay_apart():
    g, m = reads()
    out = mg.merge(g, m)
    assert out["ok"] == {"pages": True, "ads": True, "keywords": False, "terms": True, "meta:ads": True, "meta:insights": True, "meta:rankings": False}
    assert out["warnings"] == ["The keywords and their Quality Score could not be read (x).", "Meta: Meta's own ratings of the ads could not be read (y)."]
    assert out["platforms"] == ["google", "meta"] and out["off"]["kinds"][0]["kind"] == "an instant form"


def test_the_totals_add_up_and_the_page_count_includes_pages_each_platform_read_and_did_not_keep():
    g, m = reads()
    out = mg.merge(g, m)
    assert out["pageCost"] == 1100.0
    assert out["pageCount"] == 3 + (5 - 2) + (3 - 2)                         # the three kept pages, plus those each platform counted but did not keep


def test_only_the_busiest_pages_of_the_two_together_are_kept_with_the_groups_that_reach_them():
    g, m = reads()
    out = mg.merge(g, m, keep=2)
    assert [p["id"] for p in out["pages"]] == ["a.test/x", "b.test/z"]
    assert set(out["adGroups"]) == {"g1", "meta:s1", "meta:s2"} and "g2" not in out["adGroups"]
    assert out["adGroups"]["g1"]["pages"] == {"a.test/x": 300.0}


def test_the_reads_given_are_not_changed_by_the_merge():
    g, m = reads()
    g0, m0 = copy.deepcopy(g["pages"]), copy.deepcopy(m["pages"])
    mg.merge(g, m)
    assert g["pages"] == g0 and m["pages"] == m0


def test_a_group_s_pages_are_cut_to_the_kept_pages():
    g, m = reads()
    g["adGroups"]["g1"]["pages"]["a.test/y"] = 1.0
    out = mg.merge(g, m, keep=1)
    assert out["adGroups"]["g1"]["pages"] == {"a.test/x": 300.0}


def test_a_group_s_pages_list_puts_the_bigger_spender_first_whichever_platform_it_is():
    g, m = reads()
    m["adGroups"]["meta:s1"]["pages"]["a.test/x"] = 500.0
    m["pages"][0]["adGroups"] = ["meta:s1"]
    assert mg.merge(g, m)["pages"][0]["adGroups"] == ["meta:s1", "g1"]


def test_the_value_of_both_platforms_is_added():
    g, m = reads()
    m["pages"][0]["value"] = 25.0
    assert mg.merge(g, m)["pages"][0]["value"] == 75.0


def test_a_page_only_meta_reaches_is_copied_so_the_merge_cannot_reorder_the_meta_read():
    g, m = reads()
    m["pages"][1]["adGroups"] = ["meta:s2", "meta:s3"]
    m["adGroups"]["meta:s3"] = group("meta:s3", 900.0, {"b.test/z": 900.0}, "meta")
    before = copy.deepcopy(m["pages"][1])
    out = mg.merge(g, m)
    assert out["pages"][1]["adGroups"] == ["meta:s3", "meta:s2"] and m["pages"][1] == before and out["pages"][1] is not m["pages"][1]
