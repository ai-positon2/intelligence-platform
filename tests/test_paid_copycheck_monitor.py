"""Ad Copy Checker: the weekly run over every account (tracker/paid_copycheck_monitor.py) through the monitor engine. No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import Deliver, MemStore  # noqa: E402
from tests import test_paid_copycheck as T  # noqa: E402
from tracker import paid_copycheck_monitor as cm  # noqa: E402
from tracker import paid_copycheck_settings as st  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_monitor as mon  # noqa: E402

LINK = "https://p.example/p2/paid-marketing/ad-copy-checker"
A = {"id": "1234567890", "name": "Acme Dental", "platform": "google", "group": "g"}
B = {"id": "2222222222", "name": "Beta Clinic", "platform": "google", "group": "g"}
M = {"id": "act_1234567", "name": "Meta Co", "platform": "meta", "group": ""}
LISTED = [dict(A, currency="USD"), dict(B, currency="EUR")]
DAY = dt.date(2026, 10, 26)

GOOD = T.rsa(ad="1")
MEDIUM = T.rsa(ad="2", heads=("Affordble Dentst", "Quality Dental Care"), descs=("We offer implants.",))
HIGH = T.rsa(ad="3", heads=("{Brand} Implants", "Quality Dental Care"), descs=("We offer implants.",))
LOW = T.rsa(ad="4", heads=("Quality Dental Care", "Call Now For FREEBIES today"), descs=("We offer implants.",))


class World:
    def __init__(self, a=None, b=None):
        self.search_rows = {A["id"]: list(a if a is not None else [GOOD]), B["id"]: list(b if b is not None else [GOOD])}
        self.fail, self.quota, self.meta = set(), False, []

    def search(self, cid, q):
        if self.quota:
            raise gads.GadsError("quota", "The daily limit is used up.")
        return T.searcher(self.search_rows, fail=self.fail)(cid, q)

    def pages(self, path, params):
        return self.meta


def run(store, world, deliver, day=DAY, accounts=(A, B), hour=7, workers=4):
    now = lambda: dt.datetime.combine(day, dt.time(hour), tzinfo=dt.timezone.utc)                    # noqa: E731
    spec = cm.make_spec(today=day, search_fn=world.search, list_fn=lambda: LISTED, mon_store=store, meta_pages_fn=world.pages, meta_account_fn=lambda act: {"name": "Meta Co", "currency": "GBP"},
                        now_fn=now, workers=workers, speller=T.speller())
    return mon.run_monitor(store, spec, trigger="test", link=LINK, deliver=deliver, accounts=list(accounts), groups=[], now_fn=now)


def configured():
    s = MemStore()
    s.state_set("alerts", {"emails": ["ppc@example.com"], "slackChannel": "#alerts"})
    return s


def d(n):
    return DAY + dt.timedelta(days=n)


def analysed(*ads, account=A):
    raw = {"accounts": [dict(id=account["id"], name=account["name"], currency="USD", platform="google", ads=list(ads), warnings=[])], "warnings": [], "members": []}
    from tracker import paid_copycheck as cc
    return cc.assemble(raw, account, speller=T.speller())["accounts"][0]


def test_an_ad_with_something_to_fix_is_one_issue_with_its_own_fingerprint_level_and_words():
    a = analysed(*T.cc.read_search_ads([GOOD, MEDIUM, HIGH]))
    issues = cm.issues_of({"id": A["id"], "name": A["name"], "platform": "google"}, a)
    assert [(i["fingerprint"], i["level"], i["kind"]) for i in issues] == [("copycheck:1234567890:3", "high", "ad"), ("copycheck:1234567890:2", "medium", "ad")]
    high, med = issues
    assert high["title"] == "Unfilled placeholder in Core Search / Group 10" and high["agent"] == "copycheck" and high["accountName"] == "Acme Dental"
    assert high["detail"] == "headline \"{Brand} Implants\": {Brand} looks like a template that was never filled in." and high["nextStep"] == "Start with: Fill in or remove the placeholders in this ad."
    assert med["title"] == "Possible misspelling in Core Search / Group 10" and med["detail"].startswith("headline \"Affordble Dentst\": \"Affordble\" is not a word I know. Did you mean \"affordable\"?")
    assert med["data"] == {"ad": "2", "kind": "rsa", "campaign": "Core Search", "adGroup": "Group 10", "findings": 2}


def test_ads_with_only_small_things_are_counted_in_one_quiet_issue_and_a_clean_ad_has_none():
    a = analysed(*T.cc.read_search_ads([GOOD, LOW, T.rsa(ad="5", heads=("Quality Dental Care", "Call Now For FREEBIES today"), descs=("We offer implants.",))]))
    issues = cm.issues_of({"id": A["id"], "name": A["name"], "platform": "google"}, a)
    assert [(i["fingerprint"], i["level"], i["kind"], i["title"]) for i in issues] == [("copycheck:1234567890:small", "low", "small", "2 ads with small things to tidy")]
    assert cm.issues_of({"id": A["id"], "name": A["name"], "platform": "google"}, analysed(*T.cc.read_search_ads([GOOD]))) == []


def test_only_the_worst_ads_are_followed_one_by_one_and_the_rest_are_counted(monkeypatch):
    monkeypatch.setattr(cm, "MAX_ADS_PER_ACCOUNT", 2)
    a = analysed(*T.cc.read_search_ads([T.rsa(ad=str(10 + n), heads=(typo + " near you", "Quality Dental Care"), descs=("We offer implants.",)) for n, typo in enumerate(("Affordble", "Dentst", "Qualty", "Clincs", "Tooht"))] + [HIGH]))
    issues = cm.issues_of({"id": A["id"], "name": A["name"], "platform": "google"}, a)
    assert [i["fingerprint"] for i in issues][:2] == ["copycheck:1234567890:3", "copycheck:1234567890:10"] and issues[-1]["fingerprint"] == "copycheck:1234567890:more"
    assert issues[-1]["title"] == "4 more ads with something to fix" and issues[-1]["level"] == "medium" and issues[-1]["data"] == {"ads": 4} and len(issues) == 3


def test_a_title_names_two_things_and_says_there_is_more_when_there_is():
    ad = T.rsa(ad="9", heads=("dentst near you!!", "Quality Dental Care"), descs=("We offer implants.",))
    a = analysed(*T.cc.read_search_ads([ad]))
    i = cm.issues_of({"id": A["id"], "name": A["name"], "platform": "google"}, a)[0]
    assert i["title"] == "Possible misspelling, Doubled punctuation and more in Core Search / Group 10" and i["detail"].endswith("And 1 more.") is False and i["detail"].count("headline") == 3


def test_the_first_run_sets_the_baseline_sends_nothing_and_keeps_the_days_figures():
    s, dv, w = configured(), Deliver(), World(a=[GOOD, MEDIUM, HIGH, LOW])
    r = run(s, w, dv)
    assert r["status"] == "done" and r["baseline"] is True and r["alerted"] == 0 and not dv.emails and r["read"] == 2
    assert s.snaps_get("copycheck", A["id"], "2026-10-01") == [{"status": "fix", "ads": 4, "high": 1, "medium": 1, "low": 1, "findings": 4, "day": "2026-10-26"}]
    assert s.snaps_get("copycheck", B["id"], "2026-10-01")[0]["status"] == "clean"
    assert sorted(i["kind"] for i in s.issues_all("copycheck")) == ["ad", "ad", "small"]


def test_an_ad_that_goes_wrong_after_the_baseline_is_alerted_once_and_a_placeholder_is_high():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv)
    w.search_rows[A["id"]] = [GOOD, MEDIUM]
    r = run(s, w, dv, d(7))
    assert r["new"] == 1 and r["alerted"] == 1 and dv.emails[0]["subject"] == "Ad Copy Checker: 1 new (1 account)" and "Possible misspelling in Core Search / Group 10" in dv.emails[0]["text"]
    assert dv.emails[0]["text"].endswith("Open the page: " + LINK)
    w.search_rows[A["id"]] = [GOOD, MEDIUM, HIGH]
    r = run(s, w, dv, d(14))
    assert r["new"] == 1 and dv.emails[-1]["subject"] == "HIGH: Ad Copy Checker: 1 new (1 account)" and len(dv.emails) == 2


def test_a_fixed_ad_is_resolved_and_a_small_thing_is_never_alerted():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv)
    w.search_rows[A["id"]] = [GOOD, MEDIUM, LOW]
    run(s, w, dv, d(7))
    n = len(dv.emails)
    assert n == 1 and "small things" not in dv.emails[0]["text"]
    w.search_rows[A["id"]] = [GOOD, LOW]
    r = run(s, w, dv, d(14))
    assert r["resolved"] == 1 and [i["status"] for i in s.issues_all("copycheck") if i["fingerprint"].endswith(":2")] == ["resolved"] and len(dv.emails) == n


def test_what_the_team_saved_is_used_so_a_number_that_is_not_on_the_list_is_a_high_alert():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv)
    st.save_account(s, A["id"], A["name"], {"phones": "617-555-0100"}, "a@b.co")
    w.search_rows[A["id"]] = [GOOD, T.rsa(ad="6", heads=("Quality Dental Care", "Book Today"), descs=("Call 617-555-0199 today.",))]
    r = run(s, w, dv, d(7))
    assert r["new"] == 1 and dv.emails[0]["subject"].startswith("HIGH: Ad Copy Checker") and "617-555-0199 is not one of the numbers saved" in dv.emails[0]["text"]


def test_an_account_that_cannot_be_read_is_named_and_never_resolves_what_was_open():
    s, dv, w = configured(), Deliver(), World(a=[MEDIUM])
    run(s, w, dv)
    w.fail = {A["id"]}
    r = run(s, w, dv, d(7))
    assert r["unreadable"] == [{"accountId": A["id"], "name": "Acme Dental", "platform": "google", "error": "Account 1234567890 cannot be read."}] and r["resolved"] == 0
    assert [i["status"] for i in s.issues_all("copycheck")] == ["open"]


def test_a_spent_quota_stops_the_google_accounts_after_the_first_and_says_so_but_meta_is_still_read():
    s, dv, w = configured(), Deliver(), World()
    w.quota = True
    w.meta = [T.meta_ad("1", body="Affordble care")]
    r = run(s, w, dv, accounts=(A, B, M), workers=1)
    assert [u["accountId"] for u in r["unreadable"]] == [A["id"], B["id"]] and r["unreadable"][1]["error"].startswith("The Google Ads daily read budget was used up") and r["read"] == 1
    assert [i["accountId"] for i in s.issues_all("copycheck")] == ["act_1234567"] and s.issues_all("copycheck")[0]["platform"] == "meta"


def test_a_meta_ad_is_checked_with_the_same_rules_and_names_its_ad_set():
    s, dv, w = configured(), Deliver(), World()
    w.meta = [T.meta_ad("1", body="Affordble care now!!", title="Dentst")]
    run(s, w, dv, accounts=(M,))
    i = s.issues_all("copycheck")[0]
    assert i["fingerprint"] == "copycheck:act_1234567:meta:1" and i["title"] == "Possible misspelling, Doubled punctuation in Camp / Set" and i["level"] == "medium"


def test_a_team_list_that_cannot_be_read_does_not_stop_the_run():
    class Broken(MemStore):
        def state_get(self, key, default=None):
            if key.startswith("settings:"):
                raise RuntimeError("db down")
            return super().state_get(key, default)
    s, dv, w = Broken(), Deliver(), World(a=[MEDIUM])
    r = run(s, w, dv)
    assert r["status"] == "done" and [i["kind"] for i in s.issues_all("copycheck")] == ["ad"]


def test_the_brand_words_the_brand_spend_alert_saved_are_read_as_the_accounts_brand():
    from tracker import paid_brandspend_settings as bs
    s, dv = configured(), Deliver()
    bs.save_account(s, A["id"], {"brandTerms": ["smileco"]}, "a@b.co")
    ads = [T.rsa(ad="7", heads=("SmileCo Dental", "Visit SmileCo"), descs=("SmileCo cares.",)), T.rsa(ad="8", heads=("Smile Co Dental", "Quality Dental Care"), descs=("We care.",))]
    run(s, World(a=ads), dv, accounts=(A,))
    snap = s.snaps_get("copycheck", A["id"], "2026-10-01")[0]
    assert snap["low"] == 1 and snap["findings"] == 1 and [i["kind"] for i in s.issues_all("copycheck")] == ["small"]            # one place writes the brand the other way; without the brand known every SmileCo would be an odd capital


def test_phone_numbers_in_the_master_agent_are_used_by_the_checker():
    from tracker import paid_master
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv)
    paid_master.save_client(s, "", {"name": "Acme", "accounts": [{"id": A["id"]}], "shared": {"phones": "617-555-0100"}}, "a@b.co")
    w.search_rows[A["id"]] = [GOOD, T.rsa(ad="6", heads=("Quality Dental Care", "Book Today"), descs=("Call 617-555-0199 today.",))]
    r = run(s, w, dv, d(7))
    assert r["new"] == 1 and dv.emails[0]["subject"].startswith("HIGH: Ad Copy Checker") and "617-555-0199 is not one of the numbers saved" in dv.emails[0]["text"]
