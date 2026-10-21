"""Ad Copy Checker: what the team saved, the phone numbers, places, brand spellings and allowed words (tracker/paid_copycheck_settings.py). No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import MemStore  # noqa: E402
from tracker import paid_copycheck_settings as st  # noqa: E402

NOW = dt.datetime(2026, 10, 22, 8, tzinfo=dt.timezone.utc)


def test_nothing_saved_is_every_list_empty_and_no_account_is_made_up():
    s = MemStore()
    assert st.load(s) == {"accounts": {}, "allow": [], "changes": []} and st.account_of(st.load(s), "1234567890") == {"phones": [], "places": [], "brand": [], "allow": [], "name": ""}
    assert st.view(s) == {"accounts": {}, "allow": [], "changes": []} and st.empty() == st.load(s)


def test_phone_numbers_are_kept_as_ten_digits_in_one_style_without_repeats():
    assert st.clean_phones("(617) 555-0100; 617.555.0100\n+1 617 555 0199, 1-617-555-0100") == ["617-555-0100", "617-555-0199"] and st.clean_phones("") == [] and st.clean_phones(["617 555 0100"]) == ["617-555-0100"]
    with pytest.raises(st.BadRequest, match="617-555 is not a ten digit US phone number"):
        st.clean_phones("617-555")
    with pytest.raises(st.BadRequest, match="At most 20"):
        st.clean_phones(["617-555-%04d" % i for i in range(21)])


def test_places_are_names_with_letters_and_no_repeats_ignoring_case():
    assert st.clean_places("Quincy, MA\nquincy, ma; Dover, NH") == ["Quincy, MA", "Dover, NH"] and st.clean_places([]) == []
    for bad in ("12345", "<script>", "Q"):
        with pytest.raises(st.BadRequest, match="is not a place name"):
            st.clean_places(bad)
    with pytest.raises(st.BadRequest, match="At most 50"):
        st.clean_places(["City " + chr(65 + i // 26) + chr(97 + i % 26) for i in range(51)])


def test_brand_spellings_keep_their_case_and_a_blank_or_symbol_only_one_is_refused():
    assert st.clean_brand("SmileCo\nSmile Co Dental; SmileCo") == ["SmileCo", "Smile Co Dental"]
    with pytest.raises(st.BadRequest, match="is not a brand name"):
        st.clean_brand("!!!")
    with pytest.raises(st.BadRequest, match="is not a brand name"):
        st.clean_brand("B" * 61)
    assert st.clean_brand("B" * 60) == ["B" * 60]
    with pytest.raises(st.BadRequest, match="At most 10"):
        st.clean_brand(["Brand%d" % i for i in range(11)])


def test_allowed_words_are_lower_case_letters_apostrophes_and_hyphens():
    assert st.clean_words("Invisalign, Zoom-whitening  O'Neil;invisalign") == ["invisalign", "zoom-whitening", "o'neil"] and st.clean_words(None) == []
    for bad in ("a", "word1", "two_words", "x" * 41):
        with pytest.raises(st.BadRequest, match="is not a word"):
            st.clean_words(bad)
    with pytest.raises(st.BadRequest, match="At most 500"):
        st.clean_words(["a" + chr(97 + (i // 26) % 26) + chr(97 + i % 26) for i in range(501)])


def test_saving_one_accounts_lists_keeps_them_for_the_team_and_says_who_and_when():
    s = MemStore()
    out = st.save_account(s, "1234567890", "Acme Dental", {"phones": "617-555-0100", "places": "Quincy, MA", "brand": "SmileCo", "allow": "invisalign"}, "ann@position2.com", now=NOW)
    assert out == {"phones": ["617-555-0100"], "places": ["Quincy, MA"], "brand": ["SmileCo"], "allow": ["invisalign"], "name": "Acme Dental"}
    assert st.load(s)["accounts"]["1234567890"] == out and st.load(s)["changes"] == [{"at": "2026-10-22T08:00:00+00:00", "by": "ann@position2.com", "what": "saved the lists of account 1234567890"}]
    st.save_account(s, "act_1234567", "Beta Meta", {"phones": "617-555-0199"}, "bo@position2.com", now=NOW)
    assert sorted(st.load(s)["accounts"]) == ["1234567890", "act_1234567"] and len(st.load(s)["changes"]) == 2


def test_saving_nothing_for_an_account_forgets_it_and_a_bad_list_saves_none_of_it():
    s = MemStore()
    st.save_account(s, "1234567890", "Acme", {"phones": "617-555-0100"}, "a@b.co", now=NOW)
    st.save_account(s, "1234567890", "Acme", {}, "a@b.co", now=NOW)
    assert st.load(s)["accounts"] == {}
    st.save_account(s, "1234567890", "Acme", {"phones": "617-555-0100"}, "a@b.co", now=NOW)
    with pytest.raises(st.BadRequest):
        st.save_account(s, "1234567890", "Acme", {"phones": "617-555-0100", "places": "12345"}, "a@b.co", now=NOW)
    assert st.account_of(st.load(s), "1234567890")["phones"] == ["617-555-0100"] and st.account_of(st.load(s), "1234567890")["places"] == []


def test_an_mcc_or_a_made_up_id_cannot_have_lists():
    s = MemStore()
    for bad in ("mcc-9923720096-USD", "", "abc", "act_12"):
        with pytest.raises(st.BadRequest, match="Pick one account"):
            st.save_account(s, bad, "x", {"phones": "617-555-0100"}, "a@b.co")
    assert st.load(s)["accounts"] == {}


def test_the_words_for_every_account_are_saved_and_the_change_log_keeps_thirty():
    s = MemStore()
    assert st.save_allow(s, "Invisalign, zoom", "a@b.co", now=NOW) == ["invisalign", "zoom"] and st.view(s)["allow"] == ["invisalign", "zoom"]
    for i in range(35):
        st.save_allow(s, "wa" + chr(97 + i % 26) + chr(97 + i // 26), "a@b.co", now=NOW)
    assert len(st.load(s)["changes"]) == 30 and len(st.view(s)["changes"]) == 10


def test_a_word_marked_right_on_a_finding_is_added_to_that_accounts_list_once():
    s = MemStore()
    st.save_account(s, "1234567890", "Acme", {"phones": "617-555-0100", "allow": "zoom"}, "a@b.co", now=NOW)
    out = st.add_allowed(s, "1234567890", "Invisalign zoom", "a@b.co", now=NOW)
    assert out["allow"] == ["zoom", "invisalign"] and out["phones"] == ["617-555-0100"] and out["name"] == "Acme"
    fresh = st.add_allowed(s, "2222222222", "Dentst", "a@b.co", now=NOW)
    assert fresh == {"phones": [], "places": [], "brand": [], "allow": ["dentst"], "name": ""}
    with pytest.raises(st.BadRequest, match="at least one word"):
        st.add_allowed(s, "1234567890", "", "a@b.co")
    with pytest.raises(st.BadRequest, match="is not a word"):
        st.add_allowed(s, "1234567890", "12", "a@b.co")
