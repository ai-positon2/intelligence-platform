"""Tracking Data Health: reading Google and Meta, the report and its cache, and what the team saved (tracker/paid_tracking.py, paid_tracking_settings.py). No network; every account is made up."""
import datetime as dt

import pytest

from tests.monitor_fakes import MemStore
from tests.tracking_fakes import Google, Meta
from tracker import paid_gads as base
from tracker import paid_gads_segments as gads
from tracker import paid_store
from tracker import paid_tracking as tr
from tracker import paid_tracking_settings as st

TODAY = dt.date(2026, 10, 26)
AT = dt.datetime(2026, 10, 26, 9, 0, tzinfo=dt.timezone.utc)
ACCT = {"id": "1234567890", "name": "Acme Dental", "currency": "USD"}
ACCT2 = {"id": "2222222222", "name": "Beta Clinic", "currency": "USD"}
WHO = "ana@position2.com"


@pytest.fixture
def cache(monkeypatch):
    saved = {}
    monkeypatch.setattr(paid_store, "get_gads_cache", lambda key, ttl: saved.get(key))
    monkeypatch.setattr(paid_store, "put_gads_cache", lambda key, value: saved.update({key: value}))
    from tracker import paid_geo
    paid_geo.clear_cache()
    base.reset_state()
    base.forget("")
    yield saved
    paid_geo.clear_cache()
    base.forget("")


def google(**accounts):
    return Google(accounts or {ACCT["id"]: {}}, lambda: AT)


# ── Reading Google's rows (the shapes were taken from a live read on 2026-10-26) ──

def test_the_days_parse_clicks_as_text_and_conversions_as_numbers_and_give_the_time_zone():
    rows = [{"customer": {"resourceName": "customers/1", "timeZone": "America/New_York"}, "metrics": {"clicks": "136", "conversions": 0, "costMicros": "213534437"}, "segments": {"date": "2026-10-18"}}]
    days, tz = tr.read_days(rows)
    assert days == [{"date": "2026-10-18", "clicks": 136.0, "cost": 213.5344, "conv": 0.0}] and tz == "America/New_York"


def test_actions_carry_what_each_recorded_by_day_and_whether_it_is_primary():
    acts = [{"conversionAction": {"resourceName": "customers/1/conversionActions/75", "status": "ENABLED", "type": "UPLOAD_CLICKS", "category": "CONTACT", "id": "75", "name": "Form", "primaryForGoal": True}},
            {"conversionAction": {"resourceName": "customers/1/conversionActions/76", "status": "ENABLED", "type": "WEBPAGE", "category": "PAGE_VIEW", "id": "76", "name": "Visit", "primaryForGoal": False}}]
    days = [{"metrics": {"conversions": 1}, "segments": {"date": "2026-10-24", "conversionAction": "customers/1/conversionActions/75"}},
            {"metrics": {"conversions": 2}, "segments": {"date": "2026-10-24", "conversionAction": "customers/1/conversionActions/75"}}]
    out = tr.read_actions(acts, days)
    assert out[0]["primary"] and out[0]["days"] == {"2026-10-24": 3.0} and out[1]["days"] == {} and not out[1]["primary"]


def test_lists_are_sized_for_search_and_tagging_is_read_as_is():
    rows = [{"userList": {"resourceName": "customers/1/userLists/9", "membershipStatus": "OPEN", "type": "LOGICAL", "id": "9", "name": "All visitors", "sizeForDisplay": "1000", "sizeForSearch": "1900"}}]
    assert tr.read_lists(rows) == [{"id": "9", "name": "All visitors", "kind": "LOGICAL", "size": 1900.0, "platform": "google"}]
    assert tr.read_tagging([{"customer": {"autoTaggingEnabled": False}}]) is False and tr.read_tagging([]) is None


def test_one_account_is_five_reads_and_a_refused_part_is_named_not_raised():
    g = google(**{ACCT["id"]: {"fail": {"lists"}}})
    r = tr.read_account(ACCT["id"], g, AT)
    assert len(g.calls) == 5 and r["lists"] is None and r["actions"] == [] and r["autoTagging"] is True and r["today"] == "2026-10-26"
    assert r["parts"] == {"lists": "The remarketing lists could not be read (not allowed), so that check was not run."}


def test_the_days_failing_fails_the_account():
    with pytest.raises(gads.GadsError):
        tr.read_account(ACCT["id"], google(**{ACCT["id"]: {"down": "permission"}}), AT)


# ── Meta ────────────────────────────────────────────────────────────────────

def test_meta_adds_the_campaigns_up_by_day_and_reads_pixels_and_audiences():
    meta = Meta({"conv": 3, "pixels": [{"id": "88", "name": "Main", "hoursAgo": 5}], "audiences": [{"id": "5", "name": "Visitors 30d", "size": 4200}]}, lambda: AT)
    r = tr.read_meta_account("act_5550001", meta, AT)
    assert len(r["days"]) == 37 and r["days"][-1]["conv"] == 3 and r["days"][-1]["clicks"] == 100
    assert r["pixels"][0]["id"] == "88" and r["lists"] == [{"id": "5", "name": "Visitors 30d", "kind": "WEBSITE", "size": 4200.0, "platform": "meta"}] and r["parts"] == {}


def test_a_meta_part_refused_is_soft():
    meta = Meta({"fail": {"adspixels"}}, lambda: AT)
    r = tr.read_meta_account("act_5550001", meta, AT)
    assert r["pixels"] is None and "The pixels could not be read" in r["parts"]["pixels"]


# ── The report ──────────────────────────────────────────────────────────────

def report(search, **kw):
    return tr.report_for(ACCT["id"], accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=search, today=TODAY, at=AT, **kw)


def test_the_report_judges_the_account_and_says_what_was_not_run(cache):
    rep = report(google(**{ACCT["id"]: {"over": {0: (100, 0)}, "fail": {"tagging"}}}))
    a = rep["accounts"][0]
    assert a["status"] == "broken" and a["score"] == 60 and a["findings"][0]["kind"] == "conv_stopped" and rep["summary"]["critical"] == 1
    assert a["notRun"] == ["The auto-tagging setting could not be read (not allowed), so that check was not run."] and rep["warnings"] == a["notRun"]
    assert len(a["days"]) == 35 and rep["minimums"]["listMin"] == 1000


def test_what_the_team_saved_and_the_kept_list_sizes_are_applied(cache):
    mon = MemStore()
    st.save_owner(mon, ACCT["id"], ACCT["name"], "Ana", WHO, now=AT)
    mon.snaps_put("tracking", "2026-10-10", {ACCT["id"]: {"lists": {"9": 2000}}})
    rep = report(google(**{ACCT["id"]: {"lists": [{"id": "9", "name": "Visitors", "size": 600}]}}), mon=mon)
    a = rep["accounts"][0]
    assert a["owner"] == "Ana" and [f["kind"] for f in a["findings"]] == ["list_below"]


def test_an_mcc_reads_each_account_and_names_one_it_could_not_read(cache):
    group = {"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "accountIds": [ACCT["id"], ACCT2["id"]]}
    g = google(**{ACCT["id"]: {"over": {0: (100, 0)}}, ACCT2["id"]: {"down": "permission"}})
    rep = tr.report_for(group["id"], accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [group], search_fn=g, today=TODAY, at=AT)
    assert rep["account"]["isGroup"] and [a["name"] for a in rep["accounts"]] == ["Acme Dental"] and rep["warnings"] == ["Beta Clinic could not be read: refused"]


def test_the_accounts_are_listed_worst_first(cache):
    group = {"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "accountIds": [ACCT["id"], ACCT2["id"]]}
    g = google(**{ACCT["id"]: {}, ACCT2["id"]: {"tagging": False}})
    rep = tr.report_for(group["id"], accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [group], search_fn=g, today=TODAY, at=AT)
    assert [a["status"] for a in rep["accounts"]] == ["fix", "healthy"] and rep["summary"]["counts"] == {"broken": 0, "fix": 1, "check": 0, "healthy": 1}


def test_a_meta_account_reports_on_its_own_reads(cache):
    meta = Meta({"over": {0: (100, 0)}}, lambda: AT)
    rep = tr.report_for("act_5550001", meta_pages_fn=meta, meta_account_fn=lambda act: {"name": "Acme Meta", "currency": "EUR"}, today=TODAY, at=AT)
    a = rep["accounts"][0]
    assert rep["account"]["platform"] == "meta" and a["currency"] == "EUR" and a["findings"][0]["kind"] == "conv_stopped" and a["actions"] is None


# ── What the team saved ─────────────────────────────────────────────────────

def test_settings_save_an_owner_windows_muted_items_and_acknowledgements_and_say_who():
    s = MemStore()
    st.save_owner(s, ACCT["id"], ACCT["name"], "Ana", WHO, now=AT)
    st.add_window(s, ACCT["id"], None, "2026-11-01", "2026-11-03", "site migration", WHO, now=AT)
    st.mute(s, ACCT["id"], "action:75", "Old form", "replaced", WHO, now=AT)
    a = st.acknowledge(s, ACCT["id"], "conv_stopped", "fix going out", WHO, now=AT)
    assert a["owner"] == "Ana" and a["windows"][0]["why"] == "site migration" and a["muted"] == {"action:75": "replaced"} and a["acks"]["conv_stopped"]["by"] == WHO
    v = st.view(s)
    assert v["changes"][0] == {"at": "2026-10-26T09:00:00+00:00", "by": WHO, "what": "acknowledged conv_stopped in account 1234567890"}
    st.unacknowledge(s, ACCT["id"], "conv_stopped", WHO)
    st.unmute(s, ACCT["id"], "action:75", WHO)
    st.remove_window(s, ACCT["id"], "2026-11-01", "2026-11-03", WHO)
    a = st.save_owner(s, ACCT["id"], None, "", WHO)
    assert a == {"owner": "", "windows": [], "muted": {}, "names": {}, "acks": {}, "name": ""} and ACCT["id"] not in st.load(s)["accounts"]


@pytest.mark.parametrize("call,msg", [
    (lambda s: st.save_owner(s, "mcc-1-USD", None, "Ana", WHO), "Pick one account"),
    (lambda s: st.add_window(s, ACCT["id"], None, "2026-11-03", "2026-11-01", "x", WHO), "before the first"),
    (lambda s: st.add_window(s, ACCT["id"], None, "2026-01-01", "2026-06-01", "x", WHO), "at most 92 days"),
    (lambda s: st.add_window(s, ACCT["id"], None, "2026-11-01", "2026-11-02", "", WHO), "Say why"),
    (lambda s: st.add_window(s, ACCT["id"], None, "soon", "2026-11-02", "x", WHO), "must be a date"),
    (lambda s: st.mute(s, ACCT["id"], "campaign:1", "", "x", WHO), "Only a conversion action"),
    (lambda s: st.acknowledge(s, ACCT["id"], "DROP TABLE", "x", WHO), "not a finding"),
])
def test_settings_refuse_what_they_cannot_keep(call, msg):
    with pytest.raises(st.BadRequest) as e:
        call(MemStore())
    assert msg in str(e.value)
