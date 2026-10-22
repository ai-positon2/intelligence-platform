"""Location Conflicts: the arithmetic and the rules (no network)."""
import pytest

from tracker import paid_geoconf_math as m


def camp(i, name, channel="SEARCH", positive="PRESENCE", negative="PRESENCE_OR_INTEREST"):
    return {"id": str(i), "name": name, "channel": channel, "positive": positive, "negative": negative}


def loc(cid, name, state="Massachusetts", target="City", negative=False, kind="location"):
    return {"campaignId": str(cid), "negative": negative, "kind": kind, "name": name, "canonical": "%s,%s,United States" % (name, state) if state else "%s,United States" % name,
            "targetType": target}


def ad(i, cid, group, texts, gid=None, campaign="x"):
    return {"id": str(i), "campaignId": str(cid), "campaign": campaign, "adGroupId": gid or ("g" + str(i)), "adGroup": group, "kind": "rsa", "texts": [{"slot": s, "text": t} for s, t in texts]}


def rules_of(res):
    return sorted(f["rule"] for it in res["items"] for f in it["findings"])


def findings(res, rule):
    return [f for it in res["items"] for f in it["findings"] if f["rule"] == rule]


# -- vocabulary and matching -------------------------------------------------

def test_norm_keeps_words_of_names_with_marks():
    assert m.norm("Dover_Emergency-Search's") == "dover emergency search s"
    assert m.norm(None) == ""


def test_find_places_longest_first_and_word_boundaries():
    vocab = {"york": "York", "new york": "New York", "dover": "Dover"}
    assert m.places_in("Dentist in New York", vocab) == ["new york"]
    assert m.places_in("Dover_Emergency", vocab) == ["dover"]
    assert m.places_in("Doverton dentist", vocab) == []
    assert m.places_in("York and New York", vocab) == ["new york", "york"]


def test_inside_follows_the_canonical_name():
    assert m.inside("Quincy,Massachusetts,United States", "Massachusetts,United States")
    assert m.inside("Quincy,Massachusetts,United States", "Quincy,Massachusetts,United States")
    assert not m.inside("Massachusetts,United States", "Quincy,Massachusetts,United States")
    assert not m.inside("Quincy,Massachusetts,United States", "Dover,New Hampshire,United States")
    assert not m.inside("", "Massachusetts")


def test_vocab_has_states_targets_saved_and_other_places_but_not_ignored_or_short():
    v = m.build_vocab([loc(1, "Quincy"), loc(1, "Rye", state="New York")], saved=["Greater Boston, MA"], other_places={"Dover, NH": "Other Co"}, ignore=["Orange"])
    assert "massachusetts" in v and "quincy" in v and "greater boston" in v and "dover" in v
    assert "rye" not in v                      # shorter than SHORT_PLACE
    assert "waco" in m.build_vocab([loc(1, "Waco", "Texas")])      # four letters is long enough
    assert "united states" not in v
    assert "ma" not in v
    v2 = m.build_vocab([loc(1, "Orange", state="California")], ignore=["Orange"])
    assert "orange" not in v2
    assert v["quincy"] == "Quincy" and v["massachusetts"] == "Massachusetts"


# -- the rules ---------------------------------------------------------------

def test_clean_account_has_no_findings():
    acc = {"campaigns": [camp(1, "Search_Quincy_Core")], "locations": [loc(1, "Quincy")],
           "groups": [{"id": "g1", "campaignId": "1", "name": "Quincy_Emergency"}],
           "ads": [ad(1, 1, "Quincy_Emergency", [("headline", "Emergency dentist in Quincy"), ("description", "Open late in Massachusetts")])],
           "keywords": [{"campaignId": "1", "adGroupId": "g1", "adGroup": "Quincy_Emergency", "text": "dentist quincy"}]}
    r = m.analyse(acc)
    assert r["status"] == "clean" and r["findingCount"] == 0 and rules_of(r) == []
    assert r["counts"] == {"high": 0, "medium": 0, "low": 0}
    assert r["items"][0]["targets"] == ["Quincy"]


def test_campaign_named_for_a_place_it_does_not_target_is_high_when_another_campaign_targets_it():
    acc = {"campaigns": [camp(1, "Search_Quincy_Core"), camp(2, "Search_Dover_Core")], "locations": [loc(1, "Dover", "New Hampshire"), loc(2, "Dover", "New Hampshire")]}
    # Quincy is not a place anyone targets or saved: not seen. Once another account's list holds it, it is.
    assert m.analyse(acc)["findingCount"] == 1                   # only the overlap
    r = m.analyse(acc, other_places={"Quincy, MA": "Other Co"})
    f = findings(r, "name_place")
    assert len(f) == 1 and f[0]["level"] == "medium" and f[0]["campaign"] == "Search_Quincy_Core"
    assert f[0]["places"] == ["Quincy"] and "targets Dover" in f[0]["detail"]
    acc2 = {"campaigns": [camp(1, "Search_Quincy_Core"), camp(2, "Search_Dover_Core")], "locations": [loc(1, "Dover", "New Hampshire"), loc(2, "Quincy")]}
    f2 = findings(m.analyse(acc2), "name_place")
    assert [(x["campaign"], x["level"], x["detail"]) for x in f2] == [
        ("Search_Dover_Core", "high", "The campaign's name says Dover, and it is targeted by Search_Quincy_Core."),
        ("Search_Quincy_Core", "high", "The campaign's name says Quincy, and it is targeted by Search_Dover_Core.")]


def test_name_place_keys_are_stable_and_distinct_per_place():
    acc = {"campaigns": [camp(1, "Quincy Dover")], "locations": [loc(1, "Quincy"), loc(9, "Dover", "New Hampshire")]}
    keys = sorted(f["key"] for f in findings(m.analyse(acc), "name_place"))
    assert keys == ["name_place:1:dover"]


def test_ad_group_name_place_not_targeted():
    acc = {"campaigns": [camp(1, "Search_Core"), camp(2, "Search_Two")], "locations": [loc(1, "Quincy"), loc(2, "Dover", "New Hampshire")],
           "groups": [{"id": "g1", "campaignId": "1", "name": "Dover_Emergency"}]}
    f = findings(m.analyse(acc), "name_place")
    assert len(f) == 1 and f[0]["scope"] == "adgroup" and f[0]["level"] == "high" and f[0]["adGroup"] == "Dover_Emergency" and f[0]["key"] == "name_place:g1:dover"


def test_group_copy_mismatch_is_high_and_replaces_the_copy_place_finding():
    acc = {"campaigns": [camp(1, "Search_Core"), camp(2, "Search_Two")], "locations": [loc(1, "Quincy"), loc(1, "Dover", "New Hampshire"), loc(2, "Quincy")],
           "ads": [ad(7, 1, "Dover_Emergency", [("headline", "Emergency dentist in Quincy"), ("description", "Call today")])]}
    r = m.analyse(acc)
    f = findings(r, "group_copy")
    assert len(f) == 1 and f[0]["level"] == "high" and f[0]["adId"] == "7" and f[0]["key"] == "group_copy:7"
    assert f[0]["evidence"] == [{"slot": "headline", "text": "Emergency dentist in Quincy"}]
    assert f[0]["places"] == ["Dover", "Quincy"]
    assert findings(r, "copy_place") == []


def test_group_and_copy_naming_a_shared_place_is_not_a_conflict():
    acc = {"campaigns": [camp(1, "Search_Core")], "locations": [loc(1, "Quincy"), loc(1, "Dover", "New Hampshire")],
           "ads": [ad(7, 1, "Dover_Emergency", [("headline", "Dentist in Dover or Quincy")])]}
    assert findings(m.analyse(acc), "group_copy") == []


def test_copy_place_not_targeted_levels():
    acc = {"campaigns": [camp(1, "Search_Core"), camp(2, "Search_Two")], "locations": [loc(1, "Quincy"), loc(2, "Dover", "New Hampshire"), loc(1, "Boston", negative=True)],
           "ads": [ad(5, 1, "General", [("headline", "Dentist in Dover")]), ad(6, 1, "General", [("headline", "Dentist in Boston")]), ad(8, 1, "General", [("headline", "Dentist in Texas")])]}
    f = {x["adId"]: x for x in findings(m.analyse(acc), "copy_place")}
    assert f["5"]["level"] == "medium" and "another campaign does" in f["5"]["detail"]
    assert f["6"]["level"] == "high" and "it is excluded" in f["6"]["detail"]
    assert f["8"]["level"] == "medium" and f["8"]["places"] == ["Texas"]


def test_copy_naming_the_state_or_country_of_a_target_is_fine():
    acc = {"campaigns": [camp(1, "Search_Core")], "locations": [loc(1, "Quincy")], "ads": [ad(5, 1, "General", [("headline", "Best dentist in Massachusetts")])]}
    assert findings(m.analyse(acc), "copy_place") == []


def test_saved_area_is_allowed_everywhere():
    acc = {"campaigns": [camp(1, "Search_Core")], "locations": [loc(1, "Quincy")], "ads": [ad(5, 1, "General", [("headline", "Serving Greater Boston")])]}
    assert [f["rule"] for f in m.analyse(acc)["items"][0]["findings"]] == []
    assert m.analyse(acc, config={"areas": ["Greater Boston"]})["findingCount"] == 0
    other = m.analyse(acc, other_places={"Greater Boston": "Another Co"})
    assert len(findings(other, "copy_place")) == 1


def test_ignore_list_silences_a_place_word():
    acc = {"campaigns": [camp(1, "Search_Core")], "locations": [loc(1, "Quincy"), loc(2, "Orange", "California")], "ads": [ad(5, 1, "General", [("headline", "Orange whitening")])]}
    assert len(findings(m.analyse(acc), "copy_place")) == 1
    assert findings(m.analyse(acc, config={"ignore": ["orange"]}), "copy_place") == []


def test_keywords_are_grouped_per_ad_group():
    acc = {"campaigns": [camp(1, "Search_Core")], "locations": [loc(1, "Quincy"), loc(2, "Dover", "New Hampshire")],
           "keywords": [{"campaignId": "1", "adGroupId": "g1", "adGroup": "A", "text": "dentist dover"}, {"campaignId": "1", "adGroupId": "g1", "adGroup": "A", "text": "dover dental"},
                        {"campaignId": "1", "adGroupId": "g1", "adGroup": "A", "text": "dentist quincy"}, {"campaignId": "1", "adGroupId": "g2", "adGroup": "B", "text": "texas dentist"}]}
    f = sorted(findings(m.analyse(acc), "kw_place"), key=lambda x: x["adGroupId"])
    assert [(x["adGroupId"], x["count"], x["places"]) for x in f] == [("g1", 2, ["Dover"]), ("g2", 1, ["Texas"])]
    assert [e["text"] for e in f[0]["evidence"]] == ["dentist dover", "dover dental"]
    assert f[0]["key"] == "kw_place:g1"


def test_targeted_and_excluded():
    acc = {"campaigns": [camp(1, "A"), camp(2, "B")],
           "locations": [loc(1, "Quincy"), loc(1, "Quincy", negative=True), loc(2, "Quincy"), loc(2, "Massachusetts", state=None, target="State", negative=True),
                         loc(2, "Quincy", negative=True, kind="proximity")]}
    f = {x["campaignId"]: x for x in findings(m.analyse(acc), "tgt_conflict")}
    assert set(f) == {"1", "2"} and f["1"]["level"] == "high"
    assert "the same place as" in f["1"]["detail"] and "inside Massachusetts" in f["2"]["detail"]


def test_excluded_inside_targeted_is_normal():
    acc = {"campaigns": [camp(1, "A")], "locations": [loc(1, "Massachusetts", state=None, target="State"), loc(1, "Quincy", negative=True)]}
    assert findings(m.analyse(acc), "tgt_conflict") == []


def test_no_location_set():
    acc = {"campaigns": [camp(1, "A"), camp(2, "B")], "locations": [loc(1, "Quincy")]}
    f = findings(m.analyse(acc), "no_target")
    assert [x["campaignId"] for x in f] == ["2"] and f[0]["level"] == "medium"
    assert findings(m.analyse(acc, platform="meta"), "no_target") == []


def test_presence_or_interest_levels_and_acceptance():
    acc = {"campaigns": [camp(1, "Local", positive="PRESENCE_OR_INTEREST"), camp(2, "National", positive="PRESENCE_OR_INTEREST"), camp(3, "Fine", positive="PRESENCE"), camp(4, "NoTgt", positive="PRESENCE_OR_INTEREST")],
           "locations": [loc(1, "Quincy"), loc(2, "United States", state=None, target="Country"), loc(3, "Quincy")]}
    f = {x["campaignId"]: x for x in findings(m.analyse(acc), "setting")}
    assert set(f) == {"1", "2"} and f["1"]["level"] == "medium" and f["2"]["level"] == "low"
    assert "people who only search about the places it targets" in f["1"]["detail"]
    acc_ok = m.analyse(acc, config={"acceptPresence": True})
    f2 = {x["campaignId"]: x for x in findings(acc_ok, "setting")}
    assert f2["1"]["level"] == "low" and "The team accepted this" in f2["1"]["detail"]


def test_proximity_counts_as_a_narrow_target():
    acc = {"campaigns": [camp(1, "Local", positive="PRESENCE_OR_INTEREST")], "locations": [{"campaignId": "1", "negative": False, "kind": "proximity", "name": "", "city": "Quincy", "canonical": "", "radiusText": "10 miles"}]}
    f = findings(m.analyse(acc), "setting")
    assert len(f) == 1 and f[0]["level"] == "medium"
    assert m.analyse(acc)["items"][0]["targets"] == ["Quincy (10 miles)"]


def test_negative_option_presence_with_exclusions_is_low():
    acc = {"campaigns": [camp(1, "A", negative="PRESENCE")], "locations": [loc(1, "Quincy"), loc(1, "Boston", negative=True)]}
    f = findings(m.analyse(acc), "setting")
    assert len(f) == 1 and f[0]["level"] == "low" and f[0]["key"] == "setting_excl:1"
    assert findings(m.analyse({"campaigns": [camp(1, "A", negative="PRESENCE")], "locations": [loc(1, "Quincy")]}), "setting") == []


def test_overlap_only_among_search_and_ignores_countries_and_exclusions():
    acc = {"campaigns": [camp(1, "A"), camp(2, "B"), camp(3, "C", channel="PERFORMANCE_MAX"), camp(4, "D"), camp(5, "E", channel="DISPLAY")],
           "locations": [loc(1, "Quincy"), loc(2, "Quincy"), loc(3, "Quincy"), loc(5, "Quincy"), loc(4, "Quincy", negative=True), loc(1, "United States", state=None, target="Country"), loc(2, "United States", state=None, target="Country")]}
    f = findings(m.analyse(acc), "overlap")
    assert len(f) == 1 and f[0]["campaigns"] == ["A", "B"] and f[0]["level"] == "low" and f[0]["key"] == "overlap:quincy"


def test_schedule_drift_needs_three_peers_and_a_majority():
    full = [{"campaignId": str(i), "day": d, "start": 8, "end": 18} for i in (1, 2, 3) for d in ("MONDAY", "TUESDAY")]
    odd = [{"campaignId": "4", "day": "MONDAY", "start": 8, "end": 9}]
    acc = {"campaigns": [camp(i, "C%d" % i) for i in (1, 2, 3, 4)], "locations": [loc(i, "Quincy") for i in (1, 2, 3, 4)], "schedules": full + odd}
    f = findings(m.analyse(acc), "schedule")
    assert [x["campaignId"] for x in f] == ["4"] and f[0]["level"] == "medium"
    assert "2 windows, 20 hours a week" in f[0]["detail"] and "1 window, 1 hour a week" in f[0]["detail"] and "3 of the other 3 Search" in f[0]["detail"]
    two = {"campaigns": [camp(1, "A"), camp(2, "B")], "locations": [], "schedules": [{"campaignId": "1", "day": "MONDAY", "start": 8, "end": 9}]}
    assert findings(m.analyse(two), "schedule") == []
    split = {"campaigns": [camp(i, "C%d" % i) for i in (1, 2, 3, 4)], "locations": [], "schedules": [{"campaignId": "1", "day": "MONDAY", "start": 8, "end": 9}, {"campaignId": "2", "day": "TUESDAY", "start": 8, "end": 9}]}
    assert findings(m.analyse(split), "schedule") == []


def test_schedule_all_day_against_limited_peers_is_low_for_a_small_gap():
    sched = [{"campaignId": str(i), "day": "MONDAY", "start": 8, "end": 18} for i in (1, 2, 3)]
    acc = {"campaigns": [camp(i, "C%d" % i) for i in (1, 2, 3, 4)], "locations": [], "schedules": sched}
    f = findings(m.analyse(acc), "schedule")
    assert [x["campaignId"] for x in f] == ["4"] and f[0]["level"] == "low" and "all day, every day" in f[0]["detail"]


def test_device_drift_excluded_device_is_medium():
    devs = [{"campaignId": "4", "device": "MOBILE", "modifier": 0.0}, {"campaignId": "3", "device": "TABLET", "modifier": 0.8}]
    acc = {"campaigns": [camp(i, "C%d" % i) for i in (1, 2, 3, 4, 5)], "locations": [], "devices": devs + [{"campaignId": "1", "device": "MOBILE", "modifier": 1.0}]}
    f = {x["campaignId"]: x for x in findings(m.analyse(acc), "device")}
    assert set(f) == {"3", "4"} and f["4"]["level"] == "medium" and f["3"]["level"] == "low"
    assert "mobile off" in f["4"]["detail"] and "tablet -20%" in f["3"]["detail"] and "3 of 5 Search campaigns have no device adjustments" in f["4"]["detail"]


def test_drift_is_per_channel():
    devs = [{"campaignId": "5", "device": "MOBILE", "modifier": 0.0}]
    acc = {"campaigns": [camp(i, "C%d" % i) for i in (1, 2, 3)] + [camp(5, "P", channel="PERFORMANCE_MAX")], "locations": [], "devices": devs}
    assert findings(m.analyse(acc), "device") == []


def test_every_finding_carries_the_fields_the_monitor_and_page_use():
    acc = {"campaigns": [camp(1, "Search_Core", positive="PRESENCE_OR_INTEREST")], "locations": [loc(1, "Quincy"), loc(2, "Dover", "New Hampshire")],
           "groups": [{"id": "g1", "campaignId": "1", "name": "Dover_Emergency"}], "ads": [ad(5, 1, "General", [("headline", "Dentist in Dover")])],
           "keywords": [{"campaignId": "1", "adGroupId": "g1", "adGroup": "A", "text": "dover dentist"}]}
    r = m.analyse(acc)
    for it in r["items"]:
        for f in it["findings"]:
            assert f["rule"] in m.RULES and f["level"] in ("high", "medium", "low") and f["key"] and f["title"] and f["detail"] and f["fix"]
            assert f["scope"] in ("campaign", "adgroup", "ad", "account") and f["campaignId"] == "1"
    keys = [f["key"] for it in r["items"] for f in it["findings"]]
    assert len(keys) == len(set(keys))


def test_status_counts_and_ranking():
    acc = {"campaigns": [camp(1, "A", positive="PRESENCE_OR_INTEREST"), camp(2, "B")], "locations": [loc(1, "Quincy"), loc(2, "Quincy"), loc(2, "Quincy", negative=True)]}
    r = m.analyse(acc)
    assert r["status"] == "fix" and r["counts"] == {"high": 1, "medium": 1, "low": 1}
    assert [i["name"] for i in r["items"]][0] == "B"
    assert [b["rule"] for b in r["byRule"]] == ["tgt_conflict", "setting", "overlap"]
    only_medium = m.analyse({"campaigns": [camp(1, "A")], "locations": []})
    assert only_medium["status"] == "fix" and only_medium["counts"] == {"high": 0, "medium": 1, "low": 0}
    low = m.analyse({"campaigns": [camp(1, "A"), camp(2, "B")], "locations": [loc(1, "Quincy"), loc(2, "Quincy")]})
    assert low["status"] == "check" and low["counts"] == {"high": 0, "medium": 0, "low": 1}


def test_campaigns_without_an_id_and_unknown_campaign_rows_are_ignored():
    acc = {"campaigns": [{"id": "", "name": "x"}, camp(1, "A")], "locations": [loc(1, "Quincy")], "ads": [ad(5, 99, "G", [("headline", "Dover")])], "keywords": [{"campaignId": "99", "adGroupId": "g", "text": "dover"}],
           "groups": [{"id": "g", "campaignId": "99", "name": "Dover"}]}
    assert m.analyse(acc)["findingCount"] == 0


def test_rules_table_is_complete():
    assert set(m.ORDER) == set(m.RULES)
    assert {r: v["level"] for r, v in m.RULES.items()} == {"tgt_conflict": "high", "name_place": "medium", "group_copy": "high", "copy_place": "medium", "kw_place": "medium", "setting": "medium",
                                                           "no_target": "medium", "overlap": "low", "schedule": "low", "device": "low"}
