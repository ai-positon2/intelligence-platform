"""Competitor Analysis: what the team saved (tracker/paid_compete_settings.py). No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import MemStore  # noqa: E402
from tracker import paid_compete_settings as cs  # noqa: E402

NOW = dt.datetime(2026, 10, 27, 9, 0, tzinfo=dt.timezone.utc)
G, MA = "1234567890", "act_1234567"


@pytest.mark.parametrize("raw,want", [("", ""), (None, ""), ("TandemDiabetes", "https://www.facebook.com/TandemDiabetes"), ("184956728255249", "https://www.facebook.com/184956728255249"),
                                      ("https://www.facebook.com/TandemDiabetes/", "https://www.facebook.com/TandemDiabetes"), ("facebook.com/TandemDiabetes?ref=x", "https://www.facebook.com/TandemDiabetes"),
                                      ("https://m.facebook.com/Omnipod", "https://www.facebook.com/Omnipod"), ("https://www.facebook.com/profile.php?id=100012345678", "https://www.facebook.com/100012345678"),
                                      ("https://www.facebook.com/ads/library/?active_status=active&view_all_page_id=184956728255249&country=US", "https://www.facebook.com/184956728255249")])
def test_a_facebook_page_is_written_the_way_the_ad_library_is_asked_for_it(raw, want):
    assert cs.clean_facebook(raw) == want


@pytest.mark.parametrize("raw", ["https://example.com/page", "tandemdiabetes.com", "https://www.facebook.com/ads/library/", "https://www.facebook.com/login", "https://www.facebook.com/", "a b"])
def test_something_that_is_not_a_facebook_page_is_refused(raw):
    with pytest.raises(cs.BadRequest, match="Facebook page"):
        cs.clean_facebook(raw)


def test_a_competitor_is_cleaned_and_needs_somewhere_to_look():
    c = cs.clean_competitor({"name": "  Tandem   Diabetes ", "domain": "https://www.TandemDiabetes.com/pumps", "facebook": "TandemDiabetes", "googleAdvertiser": "ar17804925876349960193"})
    assert c == {"name": "Tandem Diabetes", "domain": "tandemdiabetes.com", "facebook": "https://www.facebook.com/TandemDiabetes", "googleAdvertiser": "AR17804925876349960193", "logo": "", "industry": "", "fbSource": "manual"}
    assert cs.clean_competitor({"name": "A", "domain": "a.com"}) == {"name": "A", "domain": "a.com", "facebook": "", "googleAdvertiser": "", "logo": "", "industry": "", "fbSource": ""}
    for bad, msg in (({"domain": "a.com"}, "Give each competitor a name"), ({"name": "A"}, "nowhere to look"), ({"name": "A", "domain": "not a domain"}, "not a website"), ({"name": "x" * 81, "domain": "a.com"}, "at most 80"),
                     ({"name": "A", "googleAdvertiser": "XX123"}, "not an advertiser id")):
        with pytest.raises(cs.BadRequest, match=msg):
            cs.clean_competitor(bad)


def test_competitors_are_written_one_per_line_and_read_back_the_same():
    text = "Tandem | tandemdiabetes.com | TandemDiabetes\nInsulet | omnipod.com | https://www.facebook.com/Omnipod | AR111111111111\n\nMedtronic | medtronicdiabetes.com"
    got = cs.clean_competitors(text)
    assert [c["name"] for c in got] == ["Tandem", "Insulet", "Medtronic"] and got[1]["googleAdvertiser"] == "AR111111111111" and got[2]["facebook"] == ""
    assert cs.lines_of(got) == "Tandem | tandemdiabetes.com | https://www.facebook.com/TandemDiabetes\nInsulet | omnipod.com | https://www.facebook.com/Omnipod | AR111111111111\nMedtronic | medtronicdiabetes.com"
    assert cs.clean_competitors(cs.lines_of(got)) == got and cs.clean_competitors(got) == got and cs.clean_competitors("") == [] and cs.clean_competitors(None) == []


def test_the_same_competitor_twice_and_too_many_are_refused():
    with pytest.raises(cs.BadRequest, match="Tandem again is listed twice"):
        cs.clean_competitors("Tandem | tandemdiabetes.com\nTandem again | www.tandemdiabetes.com")
    with pytest.raises(cs.BadRequest, match="At most 8"):
        cs.clean_competitors("\n".join("C%d | c%d.com" % (i, i) for i in range(9)))
    assert len(cs.clean_competitors("\n".join("C%d | c%d.com" % (i, i) for i in range(8)))) == 8


def test_the_country_is_two_letters_and_us_unless_set():
    assert cs.clean_region("de") == "DE" and cs.clean_region("") == "US" and cs.clean_region(None) == "US"
    with pytest.raises(cs.BadRequest, match="two letters"):
        cs.clean_region("USA")


def test_saving_keeps_the_list_with_who_and_removes_it_when_it_is_emptied():
    s = MemStore()
    a = cs.save_account(s, G, "Beta Bionics", {"competitors": "Tandem | tandemdiabetes.com | TandemDiabetes", "region": "us"}, "ana@position2.com", NOW)
    assert a["competitors"][0]["facebook"] == "https://www.facebook.com/TandemDiabetes" and a["region"] == "US" and a["name"] == "Beta Bionics"
    st = cs.load(s)
    assert st["changes"][0] == {"at": "2026-10-27T09:00:00+00:00", "by": "ana@position2.com", "what": "saved the competitors of account 1234567890 (1)"} and cs.view(s)["accounts"][G]["competitors"][0]["name"] == "Tandem"
    cs.save_account(s, MA, "Meta", {"competitors": "X | x.com"}, "ana", NOW)
    assert set(cs.load(s)["accounts"]) == {G, MA}
    assert cs.save_account(s, G, "", {"competitors": ""}, "ana", NOW)["competitors"] == [] and set(cs.load(s)["accounts"]) == {MA}


def test_an_mcc_or_a_bad_id_cannot_be_given_competitors_and_a_bad_list_saves_nothing():
    s = MemStore()
    for bad in ("", "abc", "act_12", "123"):
        with pytest.raises(cs.BadRequest, match="Pick one account"):
            cs.save_account(s, bad, "", {"competitors": "A | a.com"}, "ana", NOW)
    with pytest.raises(cs.BadRequest, match="nowhere to look"):
        cs.save_account(s, G, "", {"competitors": "A"}, "ana", NOW)
    assert s.state_get(cs.KEY) is None


def test_the_agent_reads_its_own_saved_list_and_a_master_that_cannot_be_read_changes_nothing(monkeypatch):
    s = MemStore()
    cs.save_account(s, G, "", {"competitors": "A | a.com"}, "ana", NOW)
    assert cs.account_of(cs.load_for_read(s), G)["competitors"][0]["name"] == "A"
    from tracker import paid_master

    def boom(store):
        raise RuntimeError("down")
    monkeypatch.setattr(paid_master, "load", boom)
    assert cs.account_of(cs.load_for_read(s), G)["competitors"][0]["name"] == "A"
    assert cs.account_of({}, "999") == {"competitors": [], "region": "US", "name": ""}


# ── the picker's changes: one competitor at a time, kept for each ad account ────────────────────────────

G1, G2, MA1 = "1234567890", "2345678901", "act_1234567"


def test_a_competitor_keeps_what_the_search_showed_and_where_its_facebook_page_came_from():
    c = cs.clean_competitor({"name": "Tandem", "domain": "tandemdiabetes.com", "facebook": "TandemDiabetes", "fbSource": "SEARCH", "logo": "https://cdn.example/t.png", "industry": "  medical   devices "})
    assert c["fbSource"] == "search" and c["logo"] == "https://cdn.example/t.png" and c["industry"] == "medical devices"
    assert cs.clean_competitor({"name": "A", "domain": "a.com", "fbSource": "search"})["fbSource"] == ""                    # no page, so no source
    assert cs.clean_competitor({"name": "A", "domain": "a.com", "facebook": "AFacebook", "fbSource": "made up"})["fbSource"] == "manual"
    assert cs.clean_competitor({"name": "A", "domain": "a.com", "logo": "http://insecure.example/x.png"})["logo"] == "" and cs.clean_competitor({"name": "A", "domain": "a.com", "logo": "javascript:alert(1)"})["logo"] == ""


def test_adding_competitors_one_at_a_time_builds_the_list_of_one_ad_account():
    s = MemStore()
    cs.change_account(s, G1, "Acme", "add", "ana", {"name": "Tandem", "domain": "tandemdiabetes.com"}, now=NOW)
    out = cs.change_account(s, G1, "Acme", "add", "ana", {"name": "Insulet", "domain": "omnipod.com"}, now=NOW)
    assert [c["name"] for c in out["competitors"]] == ["Tandem", "Insulet"] and out["region"] == "US"
    assert [c["name"] for c in cs.account_of(cs.load(s), G1)["competitors"]] == ["Tandem", "Insulet"]
    assert "saved the competitors of account %s (2)" % G1 in cs.load(s)["changes"][0]["what"] and cs.load(s)["changes"][0]["by"] == "ana"


def test_each_ad_account_keeps_its_own_competitors():
    s = MemStore()
    cs.change_account(s, G1, "", "add", "ana", {"name": "Tandem", "domain": "tandemdiabetes.com"}, now=NOW)
    cs.change_account(s, MA1, "", "add", "ana", {"name": "Medtronic", "domain": "medtronicdiabetes.com"}, now=NOW)
    cs.change_account(s, MA1, "", "add", "ana", {"name": "Tandem", "domain": "tandemdiabetes.com"}, now=NOW)
    assert [c["name"] for c in cs.account_of(cs.load(s), G1)["competitors"]] == ["Tandem"]
    assert [c["name"] for c in cs.account_of(cs.load(s), MA1)["competitors"]] == ["Medtronic", "Tandem"]
    assert cs.account_of(cs.load(s), G2)["competitors"] == []
    cs.change_account(s, MA1, "", "remove", "ana", {"name": "Tandem"}, now=NOW)
    assert [c["name"] for c in cs.account_of(cs.load(s), G1)["competitors"]] == ["Tandem"]                  # removing from one account leaves the other's list alone


def test_the_same_competitor_cannot_be_added_twice_and_there_is_a_limit():
    s = MemStore()
    cs.change_account(s, G1, "", "add", "ana", {"name": "Tandem", "domain": "tandemdiabetes.com"}, now=NOW)
    for dup in ({"name": "Tandem", "domain": "other.com"}, {"name": "Tandem Diabetes", "domain": "www.TandemDiabetes.com"}):
        with pytest.raises(cs.BadRequest, match="already in this account"):
            cs.change_account(s, G1, "", "add", "ana", dup, now=NOW)
    for i in range(7):
        cs.change_account(s, G1, "", "add", "ana", {"name": "C%d" % i, "domain": "c%d.com" % i}, now=NOW)
    with pytest.raises(cs.BadRequest, match="At most 8"):
        cs.change_account(s, G1, "", "add", "ana", {"name": "Nine", "domain": "nine.com"}, now=NOW)
    assert len(cs.account_of(cs.load(s), G1)["competitors"]) == 8


def test_removing_the_last_competitor_clears_the_account_and_an_unknown_one_is_refused():
    s = MemStore()
    cs.change_account(s, G1, "", "add", "ana", {"name": "Tandem", "domain": "tandemdiabetes.com"}, now=NOW)
    with pytest.raises(cs.BadRequest, match="not one of this account's competitors"):
        cs.change_account(s, G1, "", "remove", "ana", {"name": "Nobody"}, now=NOW)
    out = cs.change_account(s, G1, "", "remove", "ana", {"name": "tandem"}, now=NOW)
    assert out["competitors"] == [] and G1 not in cs.load(s)["accounts"]


def test_an_update_changes_one_competitors_facebook_page_and_nothing_else():
    s = MemStore()
    cs.change_account(s, G1, "", "add", "ana", {"name": "Tandem", "domain": "tandemdiabetes.com", "logo": "https://cdn.example/t.png"}, now=NOW)
    cs.change_account(s, G1, "", "add", "ana", {"name": "Insulet", "domain": "omnipod.com"}, now=NOW)
    out = cs.change_account(s, G1, "", "update", "ana", {"name": "Tandem", "domain": "tandemdiabetes.com", "facebook": "TandemDiabetes", "fbSource": "search"}, now=NOW)
    t, i = out["competitors"]
    assert t["facebook"] == "https://www.facebook.com/TandemDiabetes" and t["fbSource"] == "search" and t["logo"] == "https://cdn.example/t.png" and t["domain"] == "tandemdiabetes.com"
    assert i["facebook"] == "" and i["name"] == "Insulet"
    out = cs.change_account(s, G1, "", "update", "ana", {"name": "Tandem", "domain": "tandemdiabetes.com", "facebook": ""}, now=NOW)
    assert out["competitors"][0]["facebook"] == "" and out["competitors"][0]["fbSource"] == ""                 # clearing the page clears where it came from
    with pytest.raises(cs.BadRequest, match="not one of this account's competitors"):
        cs.change_account(s, G1, "", "update", "ana", {"name": "Ghost", "domain": "ghost.com"}, now=NOW)


def test_the_country_is_kept_with_the_account_and_a_bad_one_is_refused():
    s = MemStore()
    cs.change_account(s, G1, "", "add", "ana", {"name": "Tandem", "domain": "tandemdiabetes.com"}, now=NOW)
    out = cs.change_account(s, G1, "", "region", "ana", region="gb", now=NOW)
    assert out["region"] == "GB" and [c["name"] for c in out["competitors"]] == ["Tandem"]
    out = cs.change_account(s, G1, "", "add", "ana", {"name": "Insulet", "domain": "omnipod.com"}, now=NOW)
    assert out["region"] == "GB"
    with pytest.raises(cs.BadRequest, match="two letters"):
        cs.change_account(s, G1, "", "region", "ana", region="USA", now=NOW)


def test_the_first_change_starts_from_the_master_agents_list_so_nothing_is_lost():
    from tracker import paid_master
    s = MemStore()
    s.state_set(paid_master.KEY, {"clients": {"c1": {"name": "Acme", "accounts": [G1], "shared": {"competitors": [{"name": "Aspen", "domain": "aspendental.com"}]}}}})
    assert [c["name"] for c in cs.account_of(cs.load_for_read(s), G1)["competitors"]] == ["Aspen"]
    out = cs.change_account(s, G1, "", "add", "ana", {"name": "Tandem", "domain": "tandemdiabetes.com"}, now=NOW)
    assert [c["name"] for c in out["competitors"]] == ["Aspen", "Tandem"]


def test_an_unknown_change_and_a_group_of_accounts_are_refused():
    s = MemStore()
    with pytest.raises(cs.BadRequest, match="Unknown change"):
        cs.change_account(s, G1, "", "explode", "ana", {"name": "A", "domain": "a.com"}, now=NOW)
    with pytest.raises(cs.BadRequest, match="Pick one account"):
        cs.change_account(s, "all", "", "add", "ana", {"name": "A", "domain": "a.com"}, now=NOW)
