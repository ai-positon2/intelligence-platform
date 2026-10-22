"""Spend Anomaly Alert: what the team saved (tracker/paid_anomaly_settings.py). No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import MemStore  # noqa: E402
from tracker import paid_anomaly_settings as st  # noqa: E402

TODAY = dt.date(2026, 10, 22)
NOW = dt.datetime(2026, 10, 22, 8, 0, tzinfo=dt.timezone.utc)


def test_nothing_saved_gives_empty_figures():
    s = st.load(MemStore())
    assert s == {"sensitivity": None, "accounts": {}, "changes": []}
    assert st.account_of(s, "123") == {"usualDaily": None, "maxDaily": None, "minSwing": None, "mutedUntil": None, "note": None}


def test_an_accounts_figures_are_saved_cleaned_and_read_back():
    store = MemStore()
    got = st.save_account(store, "1234567890", {"usualDaily": "1,250.50", "maxDaily": "$3000", "minSwing": 40, "mutedUntil": "2026-11-30", "note": "  Black Friday push  "}, "me@x.co", today=TODAY, now=NOW)
    assert got == {"usualDaily": 1250.5, "maxDaily": 3000.0, "minSwing": 40.0, "mutedUntil": "2026-11-30", "note": "Black Friday push"}
    assert st.account_of(st.load(store), "1234567890") == got
    assert st.load(store)["changes"] == [{"at": "2026-10-22T08:00:00+00:00", "by": "me@x.co", "what": "saved the figures of account 1234567890"}]


def test_saving_nothing_removes_the_account():
    store = MemStore()
    st.save_account(store, "1234567890", {"maxDaily": 500}, "a@x.co", today=TODAY)
    st.save_account(store, "1234567890", {}, "b@x.co", today=TODAY)
    assert st.load(store)["accounts"] == {} and len(st.load(store)["changes"]) == 2


def test_a_meta_account_can_be_saved_too():
    store = MemStore()
    st.save_account(store, "act_5555", {"usualDaily": 80}, "me@x.co", today=TODAY)
    assert st.account_of(st.load(store), "act_5555")["usualDaily"] == 80.0


@pytest.mark.parametrize("data,message", [
    ({"usualDaily": "lots"}, "The usual daily spend must be a number."),
    ({"maxDaily": -1}, "The daily ceiling must be between 0 and 10,000,000."),
    ({"minSwing": 20_000_000}, "The smallest swing must be between 0 and 10,000,000."),
    ({"usualDaily": 500, "maxDaily": 400}, "The ceiling cannot be below the usual daily spend."),
    ({"mutedUntil": "next week"}, "The day to mute until must look like 2026-11-30."),
    ({"mutedUntil": "2026-12-07"}, "Alerts can be muted for at most 45 days ahead; mute again when it passes."),
    ({"maxDaily": "nan"}, "The daily ceiling must be between 0 and 10,000,000."),
])
def test_what_cannot_be_saved_says_why(data, message):
    with pytest.raises(st.BadRequest) as e:
        st.save_account(MemStore(), "1234567890", data, "me@x.co", today=TODAY)
    assert str(e.value) == message


def test_the_longest_mute_is_forty_five_days_and_a_past_day_is_allowed():
    store = MemStore()
    assert st.save_account(store, "1234567890", {"mutedUntil": "2026-12-06"}, "me@x.co", today=TODAY)["mutedUntil"] == "2026-12-06"
    assert st.save_account(store, "1234567890", {"mutedUntil": "2026-10-01"}, "me@x.co", today=TODAY)["mutedUntil"] == "2026-10-01"
    with pytest.raises(st.BadRequest):
        st.save_account(store, "1234567890", {"mutedUntil": "2026-12-07"}, "me@x.co", today=TODAY)


def test_an_equal_ceiling_and_usual_is_allowed():
    assert st.save_account(MemStore(), "1234567890", {"usualDaily": 500, "maxDaily": 500}, "me@x.co", today=TODAY)["maxDaily"] == 500.0


def test_the_note_is_cut_and_a_zero_is_nothing():
    store = MemStore()
    got = st.save_account(store, "1234567890", {"note": "x" * 500, "maxDaily": 0, "usualDaily": "0"}, "me@x.co", today=TODAY)
    assert got["note"] == "x" * 200 and got["maxDaily"] is None and got["usualDaily"] is None
    assert st.load(store)["accounts"]["1234567890"] == {"usualDaily": None, "maxDaily": None, "minSwing": None, "mutedUntil": None, "note": "x" * 200}


def test_the_largest_amount_is_ten_million():
    got = st.save_account(MemStore(), "1234567890", {"maxDaily": 10_000_000}, "me@x.co", today=TODAY)
    assert got["maxDaily"] == 10_000_000.0
    with pytest.raises(st.BadRequest):
        st.save_account(MemStore(), "1234567890", {"maxDaily": 10_000_000.01}, "me@x.co", today=TODAY)


@pytest.mark.parametrize("bad", ["", "  ", "12 34", "a;b", "../x"])
def test_an_account_must_be_named_one_at_a_time(bad):
    with pytest.raises(st.BadRequest, match="Pick one account"):
        st.save_account(MemStore(), bad, {"maxDaily": 5}, "me@x.co", today=TODAY)


def test_the_sensitivity_is_one_of_three_or_the_default():
    store = MemStore()
    assert st.set_sensitivity(store, "high", "me@x.co", now=NOW) == "high" and st.load(store)["sensitivity"] == "high"
    assert st.set_sensitivity(store, "", "me@x.co") is None and st.load(store)["sensitivity"] is None
    with pytest.raises(st.BadRequest, match="low, normal or high"):
        st.set_sensitivity(store, "wild", "me@x.co")
    store.state_set(st.KEY, {"sensitivity": "wild"})
    assert st.load(store)["sensitivity"] is None


def test_the_changes_keep_thirty_and_the_view_shows_ten_newest_first():
    store = MemStore()
    for i in range(35):
        st.set_sensitivity(store, "low" if i % 2 else "high", "u%d@x.co" % i)
    s = st.load(store)
    assert len(s["changes"]) == 30 and s["changes"][0]["by"] == "u34@x.co"
    v = st.view(store)
    assert len(v["changes"]) == 10 and v["changes"][0]["by"] == "u34@x.co"
    assert v["defaultSensitivity"] == "normal" and v["minSwing"] == 25.0 and v["maxMuteDays"] == 45 and v["ratios"]["normal"] == [1.75, 2.75]
