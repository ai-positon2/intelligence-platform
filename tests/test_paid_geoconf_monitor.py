"""Location Conflicts: the daily run over every account (tracker/paid_geoconf_monitor.py) through the monitor engine. No network."""
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import Deliver, MemStore  # noqa: E402
from tests import test_paid_geoconf as T  # noqa: E402
from tracker import paid_geoconf as gc  # noqa: E402
from tracker import paid_geoconf_monitor as gm  # noqa: E402
from tracker import paid_geoconf_settings as st  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_monitor as mon  # noqa: E402

LINK = "https://p.example/p2/paid-marketing/location-conflicts"
A = {"id": "1234567890", "name": "Acme Dental", "platform": "google", "group": "g"}
B = {"id": "2222222222", "name": "Beta Clinic", "platform": "google", "group": "g"}
M = {"id": "act_1234567", "name": "Meta Co", "platform": "meta", "group": ""}
LISTED = [dict(A, currency="USD"), dict(B, currency="EUR")]
DAY = dt.date(2026, 10, 26)


def good():
    return {"camps": {A["id"]: [T.campaign()], B["id"]: [T.campaign()]}, "locs": {A["id"]: [T.loc_row("5", T.QUINCY)], B["id"]: [T.loc_row("5", T.QUINCY)]}, "extra": {}}


class World:
    def __init__(self):
        self.d = good()
        self.fail, self.quota, self.meta_adsets, self.meta_ads = set(), False, [], []

    def search(self, cid, q):
        if self.quota:
            raise gads.GadsError("quota", "The daily limit is used up.")
        return T.searcher(camps=self.d["camps"], locs=self.d["locs"], extra=self.d["extra"], fail=self.fail)(cid, q)

    def pages(self, path, params):
        return self.meta_adsets if path.endswith("/adsets") else self.meta_ads


def run(store, world, deliver, day=DAY, accounts=(A, B), hour=7, workers=4):
    now = lambda: dt.datetime.combine(day, dt.time(hour), tzinfo=dt.timezone.utc)                    # noqa: E731
    spec = gm.make_spec(today=day, search_fn=world.search, list_fn=lambda: LISTED, mon_store=store, meta_pages_fn=world.pages, meta_account_fn=lambda act: {"name": "Meta Co", "currency": "GBP"},
                        now_fn=now, workers=workers)
    return mon.run_monitor(store, spec, trigger="test", link=LINK, deliver=deliver, accounts=list(accounts), groups=[], now_fn=now)


def configured():
    s = MemStore()
    s.state_set("alerts", {"emails": ["ppc@example.com"], "slackChannel": "#alerts"})
    return s


def d(n):
    return DAY + dt.timedelta(days=n)


def analysed(raw_account, account=A):
    raw = {"accounts": [dict(raw_account, id=account["id"], name=account["name"], currency="USD", platform="google")], "warnings": [], "members": []}
    return gc.assemble(raw, account)["accounts"][0]


def raw_of(world, cid=None):
    return gc.read_account(cid or A["id"], lambda c, q: world.search(c, q))


def test_a_conflict_is_one_issue_with_its_own_fingerprint_level_and_words():
    w = World()
    w.d["locs"][A["id"]] += [T.loc_row("5", T.QUINCY, negative=True)]
    w.d["camps"][A["id"]] = [T.campaign(pos="PRESENCE_OR_INTEREST")]
    issues = gm.issues_of({"id": A["id"], "name": A["name"], "platform": "google"}, analysed(raw_of(w)))
    assert [(i["fingerprint"], i["level"], i["kind"]) for i in issues] == [("geoconf:1234567890:tgt_conflict:5", "high", "tgt_conflict"), ("geoconf:1234567890:setting:5", "medium", "setting")]
    high = issues[0]
    assert high["title"] == "Search_Quincy targets a place it also excludes" and high["agent"] == "geoconf" and high["accountName"] == "Acme Dental"
    assert high["detail"] == "Quincy is the same place as Quincy, which this campaign excludes." and high["nextStep"].startswith("Start with: Remove the place from the excluded locations")
    assert high["data"] == {"campaign": "Search_Quincy", "adGroup": "", "ad": "", "places": ["Quincy", "Quincy"]}


def test_low_findings_are_counted_in_one_quiet_issue_and_a_clean_account_has_none():
    w = World()
    w.d["camps"][A["id"]] = [T.campaign("5", "A"), T.campaign("6", "B")]
    w.d["locs"][A["id"]] = [T.loc_row("5", T.QUINCY), T.loc_row("6", T.QUINCY)]
    issues = gm.issues_of({"id": A["id"], "name": A["name"], "platform": "google"}, analysed(raw_of(w)))
    assert [(i["fingerprint"], i["level"], i["kind"], i["title"]) for i in issues] == [("geoconf:1234567890:small", "low", "small", "1 small setting to check")]
    assert gm.issues_of({"id": A["id"], "name": A["name"], "platform": "google"}, analysed(raw_of(World()))) == []


def test_only_the_worst_findings_are_followed_one_by_one(monkeypatch):
    monkeypatch.setattr(gm, "MAX_PER_ACCOUNT", 2)
    w = World()
    w.d["camps"][A["id"]] = [T.campaign(str(i), "C%d" % i, pos="PRESENCE_OR_INTEREST") for i in range(1, 5)] + [T.campaign("9", "X")]
    w.d["locs"][A["id"]] = [T.loc_row(str(i), T.QUINCY) for i in range(1, 5)] + [T.loc_row("9", T.QUINCY), T.loc_row("9", T.QUINCY, negative=True)]
    issues = gm.issues_of({"id": A["id"], "name": A["name"], "platform": "google"}, analysed(raw_of(w)))
    assert [i["fingerprint"] for i in issues][:2] == ["geoconf:1234567890:tgt_conflict:9", "geoconf:1234567890:setting:1"] and [i["fingerprint"] for i in issues][2:] == ["geoconf:1234567890:more", "geoconf:1234567890:small"]
    assert issues[2]["title"] == "3 more things with a location to fix" and issues[2]["level"] == "medium" and issues[2]["data"] == {"findings": 3} and len(issues) == 4


def test_the_first_run_sets_the_baseline_sends_nothing_and_keeps_the_days_figures():
    s, dv, w = configured(), Deliver(), World()
    w.d["camps"][A["id"]] = [T.campaign(pos="PRESENCE_OR_INTEREST")]
    r = run(s, w, dv)
    assert r["status"] == "done" and r["baseline"] is True and r["alerted"] == 0 and not dv.emails and r["read"] == 2
    assert s.snaps_get("geoconf", A["id"], "2026-10-01") == [{"status": "fix", "campaigns": 1, "high": 0, "medium": 1, "low": 0, "findings": 1, "day": "2026-10-26"}]
    assert s.snaps_get("geoconf", B["id"], "2026-10-01")[0]["status"] == "clean"
    assert [i["kind"] for i in s.issues_all("geoconf")] == ["setting"]


def test_a_conflict_after_the_baseline_is_alerted_once_and_a_fixed_one_resolves():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv)
    w.d["extra"] = {"ads": [T.rsa(ad="1", gname="Dover_Emergency")], "groups": [{"campaign": {"id": "5"}, "adGroup": {"id": "10", "name": "Dover_Emergency"}}]}
    w.d["locs"][A["id"]] += [T.loc_row("6", T.DOVER)]
    from tracker import paid_geo
    paid_geo.clear_cache()
    r = run(s, w, dv, d(1))
    assert r["new"] == 2 and r["alerted"] == 2 and dv.emails[0]["subject"] == "HIGH: Location Conflicts: 2 new (1 account)" and "Dover_Emergency says Dover but its ad says Quincy" in dv.emails[0]["text"]
    assert dv.emails[0]["text"].endswith("Open the page: " + LINK)
    n = len(dv.emails)
    run(s, w, dv, d(1))
    assert len(dv.emails) == n
    w.d["extra"] = {}
    r = run(s, w, dv, d(2))
    assert r["resolved"] == 2 and sorted(i["status"] for i in s.issues_all("geoconf")) == ["resolved", "resolved"]


def test_what_the_team_saved_is_used_so_an_accepted_setting_is_not_an_alert():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv)
    w.d["camps"][A["id"]] = [T.campaign(pos="PRESENCE_OR_INTEREST")]
    from tracker import paid_geo
    paid_geo.clear_cache()
    st.save_account(s, A["id"], A["name"], {"acceptPresence": True}, "a@b.co")
    r = run(s, w, dv, d(1))
    assert r["new"] == 0 and [(i["kind"], i["level"]) for i in s.issues_all("geoconf")] == [("small", "low")]


def test_an_account_that_cannot_be_read_is_named_and_never_resolves_what_was_open():
    s, dv, w = configured(), Deliver(), World()
    w.d["camps"][A["id"]] = [T.campaign(pos="PRESENCE_OR_INTEREST")]
    run(s, w, dv)
    w.fail = {A["id"]}
    from tracker import paid_geo
    paid_geo.clear_cache()
    r = run(s, w, dv, d(7))
    assert r["unreadable"] == [{"accountId": A["id"], "name": "Acme Dental", "platform": "google", "error": "Account 1234567890 cannot be read."}] and r["resolved"] == 0
    assert [i["status"] for i in s.issues_all("geoconf")] == ["open"]


def test_an_account_whose_place_names_could_not_be_read_is_unreadable_not_clean():
    s, dv, w = configured(), Deliver(), World()

    def search(cid, q):
        if "FROM geo_target_constant" in q:
            raise gads.GadsError("api", "no names")
        return World().search(cid, q)
    w.search = search
    r = run(s, w, dv, accounts=(A,))
    assert r["unreadable"] == [{"accountId": A["id"], "name": "Acme Dental", "platform": "google", "error": "The names of the places could not be read, so the account was not checked."}] and r["read"] == 0


def test_a_spent_quota_stops_the_google_accounts_after_the_first_and_says_so_but_meta_is_still_read():
    s, dv, w = configured(), Deliver(), World()
    w.quota = True
    w.meta_adsets = [{"id": "77", "name": "Quincy adset", "campaign": {"id": "9", "name": "Leads"}, "targeting": {"geo_locations": {"cities": [{"name": "Quincy", "region": "Massachusetts", "country": "US"}],
                                                                                                                                  "location_types": ["home", "recent", "travel_in"]}}}]
    r = run(s, w, dv, accounts=(A, B, M), workers=1)
    assert [u["accountId"] for u in r["unreadable"]] == [A["id"], B["id"]] and r["unreadable"][1]["error"].startswith("The Google Ads daily read budget was used up") and r["read"] == 1
    assert [(i["accountId"], i["kind"]) for i in s.issues_all("geoconf")] == [("act_1234567", "setting")] and s.issues_all("geoconf")[0]["platform"] == "meta"


def test_a_team_list_that_cannot_be_read_does_not_stop_the_run():
    class Broken(MemStore):
        def state_get(self, key, default=None):
            if key.startswith("settings:"):
                raise RuntimeError("db down")
            return super().state_get(key, default)
    s, dv, w = Broken(), Deliver(), World()
    w.d["camps"][A["id"]] = [T.campaign(pos="PRESENCE_OR_INTEREST")]
    r = run(s, w, dv)
    assert r["status"] == "done" and [i["kind"] for i in s.issues_all("geoconf")] == ["setting"]


def test_the_worst_kind_of_conflict_comes_first_whatever_the_campaign_names():
    w = World()
    w.d["camps"][A["id"]] = [T.campaign("1", "Dover_A"), T.campaign("2", "Z"), T.campaign("3", "Dover_C")]
    w.d["locs"][A["id"]] = [T.loc_row("1", T.QUINCY), T.loc_row("2", T.QUINCY), T.loc_row("2", T.QUINCY, negative=True), T.loc_row("3", T.DOVER)]
    issues = gm.issues_of({"id": A["id"], "name": A["name"], "platform": "google"}, analysed(raw_of(w)))
    assert [(i["kind"], i["data"]["campaign"]) for i in issues if i["level"] == "high"] == [("tgt_conflict", "Z"), ("name_place", "Dover_A")]


def test_exactly_the_limit_of_findings_makes_no_more_issue(monkeypatch):
    monkeypatch.setattr(gm, "MAX_PER_ACCOUNT", 2)
    w = World()
    w.d["camps"][A["id"]] = [T.campaign(str(i), "C%d" % i, pos="PRESENCE_OR_INTEREST") for i in (1, 2)]
    w.d["locs"][A["id"]] = [T.loc_row("1", T.QUINCY), T.loc_row("2", T.DOVER)]
    issues = gm.issues_of({"id": A["id"], "name": A["name"], "platform": "google"}, analysed(raw_of(w)))
    assert [i["kind"] for i in issues] == ["setting", "setting"]


def test_one_account_failing_with_an_ordinary_error_does_not_stop_the_next():
    s, dv, w = configured(), Deliver(), World()
    w.fail = {A["id"]}
    r = run(s, w, dv, workers=1)
    assert [u["accountId"] for u in r["unreadable"]] == [A["id"]] and r["read"] == 1 and r["unreadable"][0]["error"] == "Account 1234567890 cannot be read."
