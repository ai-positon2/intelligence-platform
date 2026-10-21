"""Brand Spend Alert: what the team saved (tracker/paid_brandspend_settings.py) and how the report uses it. No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import MemStore  # noqa: E402
from tests import test_paid_brandspend as T  # noqa: E402
from tracker import paid_brandspend as b  # noqa: E402
from tracker import paid_brandspend_settings as st  # noqa: E402

NOW = dt.datetime(2026, 10, 21, 9, 0, tzinfo=dt.timezone.utc)


def test_nothing_saved_is_every_default():
    s = MemStore()
    assert st.load(s) == {"limit": None, "marks": {}, "accounts": {}, "changes": []}
    assert st.account_of(st.load(s), "123") == {"weeklyMin": None, "weeklyMax": None, "monthlyBudget": None, "brandTerms": []}
    assert st.view(s) == {"limit": None, "defaultLimit": 0.3, "marks": {}, "accounts": {}, "changes": []}


def test_an_accounts_limits_and_brand_words_are_cleaned_saved_and_said_by_whom():
    s = MemStore()
    got = st.save_account(s, "1234567890", {"weeklyMin": "150", "weeklyMax": "$1,200.50", "monthlyBudget": 5000, "brandTerms": " Northwind, northwind;Acme Dental "}, "ana@position2.com", NOW)
    assert got == {"weeklyMin": 150.0, "weeklyMax": 1200.5, "monthlyBudget": 5000.0, "brandTerms": ["northwind", "acme dental"]}
    assert st.account_of(st.load(s), "1234567890") == got and st.load(s)["changes"][0] == {"at": "2026-10-21T09:00:00+00:00", "by": "ana@position2.com", "what": "saved the limits and brand words of account 1234567890"}
    assert st.account_of(st.load(s), "9999999999")["weeklyMax"] is None


def test_saving_nothing_forgets_the_account_and_brand_words_may_come_as_a_list():
    s = MemStore()
    st.save_account(s, "111", {"weeklyMax": 100, "brandTerms": ["Acme", "acme"]}, "a", NOW)
    assert st.account_of(st.load(s), "111")["brandTerms"] == ["acme"]
    st.save_account(s, "111", {}, "a", NOW)
    assert st.load(s)["accounts"] == {}


@pytest.mark.parametrize("data, why", [({"weeklyMin": "abc"}, "The weekly minimum must be a number."), ({"weeklyMax": -5}, "The weekly maximum must be between 0"), ({"monthlyBudget": "1e12"}, "The monthly budget must be between 0"),
                                        ({"weeklyMin": 500, "weeklyMax": 100}, "The weekly minimum cannot be above the weekly maximum."), ({"weeklyMax": "nan"}, "The weekly maximum must be between 0")])
def test_a_limit_that_is_not_a_sensible_number_is_refused_with_the_reason(data, why):
    with pytest.raises(st.BadRequest, match=why):
        st.save_account(MemStore(), "111", data, "a", NOW)


def test_limits_are_saved_for_one_account_never_for_an_mcc():
    for bad in ("mcc-9923720096-USD", "", None, "12 3"):
        with pytest.raises(st.BadRequest, match="Pick one account"):
            st.save_account(MemStore(), bad, {"weeklyMax": 1}, "a", NOW)


def test_a_campaign_is_marked_counted_or_not_counted_and_can_go_back_to_its_name():
    s = MemStore()
    assert st.set_mark(s, "24", True, "ana", NOW) == {"24": True} and st.set_mark(s, "22", False, "ana", NOW) == {"24": True, "22": False}
    assert st.set_mark(s, "24", None, "ana", NOW) == {"22": False}
    assert [c["what"] for c in st.load(s)["changes"]][:3] == ["went back to its name for campaign 24", "stopped counting campaign 22", "counted campaign 24"]
    for bad in ("x", "", None):
        with pytest.raises(st.BadRequest, match="not a campaign id"):
            st.set_mark(s, bad, True, "ana", NOW)


def test_there_is_a_limit_on_how_many_campaigns_can_be_marked(monkeypatch):
    monkeypatch.setattr(st, "MAX_MARKS", 2)
    s = MemStore()
    st.set_mark(s, "1", True, "a", NOW)
    st.set_mark(s, "2", True, "a", NOW)
    with pytest.raises(st.BadRequest, match="Too many"):
        st.set_mark(s, "3", True, "a", NOW)
    assert st.set_mark(s, "2", False, "a", NOW) == {"1": True, "2": False}               # changing one that is there is fine


def test_the_limit_is_saved_as_a_fraction_and_the_default_goes_back_to_none():
    s = MemStore()
    assert st.set_limit(s, "25", "ana", NOW) == 0.25 and st.load(s)["limit"] == 0.25
    assert st.set_limit(s, "", "ana", NOW) is None and st.load(s)["limit"] is None
    with pytest.raises(b.BadRequest):
        st.set_limit(s, "5", "ana", NOW)
    assert st.load(s)["changes"][0]["what"] == "set the limit to the default" and st.load(s)["changes"][1]["what"] == "set the limit to 25%"


def test_only_the_last_thirty_changes_are_kept():
    s = MemStore()
    for n in range(35):
        st.set_mark(s, str(n), True, "a", NOW)
    assert len(st.load(s)["changes"]) == 30 and st.load(s)["changes"][0]["what"] == "counted campaign 34" and len(st.view(s)["changes"]) == 10


# ── The report uses it ──────────────────────────────────────────────────────

@pytest.fixture
def cache(monkeypatch):
    from tracker import paid_geo, paid_store
    saved = {}
    monkeypatch.setattr(paid_store, "get_gads_cache", lambda key, ttl: saved.get(key))
    monkeypatch.setattr(paid_store, "put_gads_cache", lambda key, value: saved.update({key: value}))
    paid_geo.clear_cache()
    T.base.reset_state()
    T.base.forget("")
    yield saved
    paid_geo.clear_cache()
    T.base.forget("")


def report(search, mon=None, **kw):
    return b.report_for("1234567890", accounts_fn=lambda: [T.ACCT], groups_fn=lambda: [], search_fn=search, today=T.TODAY, mon=mon, **kw)


def test_the_teams_marks_and_limit_apply_and_a_request_can_still_say_otherwise(cache):
    rows = {T.ACCT["id"]: T.weeks_of(weekly=T.USUAL + [301])}
    mon = MemStore()
    st.set_mark(mon, "11", False, "ana", NOW)
    assert report(T.searcher(rows), mon)["accounts"][0]["status"] == "none"                          # the team does not count campaign 11
    assert report(T.searcher(rows), mon, overrides={"11": True})["accounts"][0]["status"] == "over"   # this request says it does
    st.set_mark(mon, "11", None, "ana", NOW)
    st.set_limit(mon, "60", "ana", NOW)
    rep = report(T.searcher(rows), mon)
    assert rep["limit"] == 0.6 and rep["accounts"][0]["status"] == "ok" and rep["saved"] == {"limit": 0.6, "marks": {}, "teamSettings": True}
    assert report(T.searcher(rows), mon, limit="20")["limit"] == 0.2                                  # a limit in the request wins
    assert report(T.searcher(rows))["limit"] == 0.3 and report(T.searcher(rows))["saved"]["teamSettings"] is False


def test_the_teams_agreed_limits_are_in_the_account_and_judged(cache):
    rows = {T.ACCT["id"]: T.weeks_of(weekly=T.USUAL + [301])}
    mon = MemStore()
    st.save_account(mon, T.ACCT["id"], {"weeklyMax": 250, "monthlyBudget": 800}, "ana", NOW)
    a = report(T.searcher(rows), mon)["accounts"][0]
    assert a["saved"]["weeklyMax"] == 250.0 and a["limits"]["status"] == "over" and a["month"]["budget"] == 800.0 and [f["kind"] for f in a["findings"]][:1] != []
    assert any(f["kind"] == "limit-over" for f in a["findings"])


def test_brand_words_make_the_search_terms_read_and_the_leak_found(cache):
    rows = {T.ACCT["id"]: T.weeks_of(weekly=T.USUAL + [203])}
    terms = {T.ACCT["id"]: [T.term_row("12", "NonBrand Implants", "acme dental near me", "300000000", "60", "2"), T.term_row("12", "NonBrand Implants", "dentist", "900000000", "100", "5")]}
    mon, log = MemStore(), []
    st.save_account(mon, T.ACCT["id"], {"brandTerms": "acme"}, "ana", NOW)
    rep = report(T.searcher(rows, terms=terms, log=log), mon)
    a = rep["accounts"][0]
    assert a["leak"]["cost"] == 300.0 and a["leak"]["clicks"] == 60 and a["leakRead"] == "read" and any(f["kind"] == "leak" for f in a["findings"]) and rep["warnings"] == []
    assert len([q for _c, q in log if "FROM search_term_view" in q]) == 1
    n = len(log)
    report(T.searcher(rows, terms=terms, log=log), mon)
    assert len(log) == n                                                                              # the terms are kept like the rest


def test_no_brand_words_means_the_search_terms_are_never_read(cache):
    log = []
    report(T.searcher(log=log), MemStore())
    assert not [q for _c, q in log if "search_term_view" in q]


def test_search_terms_that_cannot_be_read_are_said_and_the_rest_of_the_report_stands(cache):
    mon = MemStore()
    st.save_account(mon, T.ACCT["id"], {"brandTerms": "acme"}, "ana", NOW)
    rep = report(T.searcher(fail_terms=True), mon)
    assert rep["accounts"][0]["leak"] is None and rep["accounts"][0]["status"] == "ok" and any("search terms could not be read" in w for w in rep["warnings"])


def test_the_days_the_monitor_saved_become_the_history_in_the_report(cache):
    mon = MemStore()
    mon.snaps_put("brandspend", "2026-10-18", {"1234567890": {"status": "over", "cost": 301}})
    mon.snaps_put("brandspend", "2026-10-19", {"1234567890": {"status": "over", "cost": 301}})
    a = report(T.searcher({T.ACCT["id"]: T.weeks_of(weekly=T.USUAL + [301])}), mon)["accounts"][0]
    assert a["history"]["streak"] == 2 and "outside its band for 2 days in a row (since 2026-10-18)" in a["findings"][0]["detail"]
    assert report(T.searcher({T.ACCT["id"]: T.weeks_of(weekly=T.USUAL + [301])}))["accounts"][0]["history"] is None


def test_settings_that_cannot_be_read_are_said_and_the_defaults_apply(cache):
    class Broken(MemStore):
        def state_get(self, key, default=None):
            raise RuntimeError("store down")
    rep = report(T.searcher(), Broken())
    assert rep["warnings"][0] == "The team's saved settings could not be read (store down), so the defaults were used." and rep["saved"]["teamSettings"] is False and rep["limit"] == 0.3


def test_a_history_that_cannot_be_read_is_just_no_history(cache):
    class NoSnaps(MemStore):
        def snaps_get(self, *a, **k):
            raise RuntimeError("down")
    assert report(T.searcher(), NoSnaps())["accounts"][0]["history"] is None


def test_the_snapshot_the_monitor_keeps_is_small_and_plain(cache):
    a = report(T.searcher({T.ACCT["id"]: T.weeks_of(weekly=T.USUAL + [301])}))["accounts"][0]
    assert b.snapshot_of(a) == {"status": "over", "cost": 301.0, "ratio": 1.4828, "level": "medium", "share": 0.9, "usual": 203.0}
