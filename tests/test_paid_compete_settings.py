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
    assert c == {"name": "Tandem Diabetes", "domain": "tandemdiabetes.com", "facebook": "https://www.facebook.com/TandemDiabetes", "googleAdvertiser": "AR17804925876349960193"}
    assert cs.clean_competitor({"name": "A", "domain": "a.com"}) == {"name": "A", "domain": "a.com", "facebook": "", "googleAdvertiser": ""}
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
