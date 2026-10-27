"""Creative & Asset Health: the daily run over every account (tracker/paid_creative_monitor.py) through the monitor engine. No network."""
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import Deliver, MemStore  # noqa: E402
from tests import test_paid_creative as T  # noqa: E402
from tracker import paid_creative_math as math  # noqa: E402
from tracker import paid_creative_monitor as cm  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_monitor as mon  # noqa: E402

LINK = "https://p.example/p2/paid-marketing/creative-health"
A = {"id": "1234567890", "name": "Acme Dental", "platform": "google", "group": "g"}
B = {"id": "2222222222", "name": "Beta Clinic", "platform": "google", "group": "g"}
M = {"id": "act_1234567", "name": "Meta Co", "platform": "meta", "group": ""}
LISTED = [dict(A, currency="USD"), dict(B, currency="EUR")]
DAY = dt.date(2026, 10, 22)


class World:
    """What Google and Meta answer: per account, how the ad's click-through rate moved (`recent_ctr`), and what is missing or unreadable."""

    def __init__(self):
        self.recent = {A["id"]: 0.05, B["id"]: 0.05}
        self.gaps = {A["id"]: [], B["id"]: []}
        self.fail_kinds, self.fail_accounts, self.quota = (), (), False
        self.weak = {A["id"]: [], B["id"]: []}

    def search(self, cid, q):
        if self.quota:
            raise gads.GadsError("quota", "The daily limit is used up.")
        return T.searcher(ads=T.ad_weeks(cid[:2], 0.05, self.recent[cid]), heads=[T.head_row(cid[:2])], weak_ads=self.weak[cid], fail=self.fail_kinds, fail_accounts=self.fail_accounts)(cid, q)

    def report(self, member):
        if "gaps" in self.fail_kinds:
            raise gads.GadsError("api", "no coverage")
        return {"campaigns": [{"id": "9", "name": "Search Crowns", "gaps": self.gaps[member["id"]]}]}


def run(store, world, deliver, accounts=(A, B), workers=4, meta_raw_fn=None, meta_pages_fn=None):
    now = lambda: dt.datetime.combine(DAY, dt.time(7), tzinfo=dt.timezone.utc)                       # noqa: E731
    spec = cm.make_spec(today=DAY, search_fn=world.search, list_fn=lambda: LISTED, mon_store=store, report_fn=world.report, meta_raw_fn=meta_raw_fn, meta_pages_fn=meta_pages_fn,
                        meta_account_fn=lambda act: {"name": "Meta Co", "currency": "GBP"}, now_fn=now, workers=workers)
    return mon.run_monitor(store, spec, trigger="test", link=LINK, deliver=deliver, accounts=list(accounts), groups=[], now_fn=now)


def configured():
    s = MemStore()
    s.state_set("alerts", {"emails": ["ppc@example.com"], "slackChannel": "#alerts"})
    return s


def fnd(rule, key, level, title="t"):
    return {"rule": rule, "key": key, "level": level, "area": "x", "title": title, "detail": "d", "fix": "Do the thing.", "evidence": ["e"], "where": {"campaign": "C"}}


def analysed(*findings):
    return {"findings": list(findings)}


def gap(sev="high"):
    return {"key": "SITELINK", "scope": "Campaign", "campaign": "Search Crowns", "campaignId": "9", "label": "Sitelinks", "severity": sev, "have": 0, "target": 4, "add": 4, "message": "No sitelinks."}


# ── What becomes an issue ───────────────────────────────────────────────────

def test_a_high_or_medium_finding_is_one_issue_each_with_the_fix_as_the_next_step_and_a_low_one_is_quiet():
    out = cm.issues_of(A, analysed(fnd("missing_asset", "gap:9:Campaign:SITELINK", "low", "Small gap"), fnd("low_rating", "low:11", "medium"), fnd("tired", "tired:11", "high")))
    assert [(i["fingerprint"], i["level"], i["kind"]) for i in out] == [("creative:1234567890:tired:11", "high", "tired"), ("creative:1234567890:low:11", "medium", "low_rating"),
                                                                       ("creative:1234567890:small", "low", "small")]
    assert out[0]["nextStep"] == "Start with: Do the thing." and out[0]["data"] == {"evidence": ["e"], "area": "x", "where": {"campaign": "C"}} and out[0]["agent"] == "creative"
    assert out[2]["title"] == "1 small finding to check" and "Small gap" in out[2]["detail"] and out[2]["detail"].endswith("None of it stops the ads.")


def test_only_the_worst_hundred_findings_of_an_account_follow_one_by_one_and_the_rest_is_counted():
    many = [fnd("missing_asset", "gap:%d" % i, "medium") for i in range(104)] + [fnd("tired", "tired:1", "high")]
    out = cm.issues_of(A, analysed(*many))
    assert len(out) == 101 and out[0]["kind"] == "tired" and out[-1]["kind"] == "more" and out[-1]["title"] == "5 more creative problems" and out[-1]["data"] == {"findings": 5}
    assert cm.issues_of(A, analysed(*[fnd("missing_asset", "gap:%d" % i, "medium") for i in range(101)]))[-1]["title"] == "1 more creative problem"
    assert cm.issues_of(A, analysed()) == []


# ── The daily run ───────────────────────────────────────────────────────────

def test_a_creative_that_wears_out_after_the_baseline_is_alerted_once_and_resolved_when_it_recovers():
    s, dv, w = configured(), Deliver(), World()
    first = run(s, w, dv)
    assert first["alerted"] == 0 and first["baseline"] is True and first["read"] == 2
    w.recent[A["id"]] = 0.02
    second = run(s, w, dv)
    assert second["new"] == 1 and second["alerted"] == 1 and "click-through rate down" in dv.emails[-1]["text"] and "Acme Dental" in dv.emails[-1]["text"]
    assert run(s, w, dv)["alerted"] == 0                                                   # open and already alerted
    w.recent[A["id"]] = 0.05
    r = run(s, w, dv)
    assert r["resolved"] == 1 and [x["status"] for x in s.issues_all("creative")] == ["resolved"]


def test_missing_assets_alert_by_their_own_severity_and_an_account_read_for_the_first_time_stays_quiet():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv, accounts=(A,))
    w.gaps[A["id"]] = [gap("high")]
    w.gaps[B["id"]] = [gap("high")]
    r = run(s, w, dv, accounts=(A, B))
    assert r["new"] == 1 and r["firstRead"] == 1 and r["alerted"] == 1                      # B is read for the first time: what is wrong there already is its baseline
    by = {x["accountId"]: x for x in s.issues_all("creative")}
    assert by[A["id"]]["alert"] != {"baseline": True} and by[B["id"]]["alert"] == {"baseline": True}


def test_a_part_of_the_read_that_failed_keeps_its_issues_open_and_never_resolves_them():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv)
    w.gaps[A["id"]] = [gap("high")]
    run(s, w, dv)
    assert [x["status"] for x in s.issues_all("creative")] == ["open"]
    w.gaps[A["id"]], w.fail_kinds = [], ("gaps",)
    r = run(s, w, dv)
    assert r["resolved"] == 0 and r["held"] >= 1 and [x["status"] for x in s.issues_all("creative")] == ["open"]
    w.fail_kinds = ()
    assert run(s, w, dv)["resolved"] == 1                                                  # read properly and gone


def test_a_failed_part_still_lets_the_other_findings_of_the_account_be_judged():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv)
    w.recent[A["id"]] = 0.02
    w.fail_kinds = ("weak_ads",)
    r = run(s, w, dv)
    assert r["new"] == 1 and r["read"] == 2


def test_an_account_whose_ads_cannot_be_read_is_listed_and_never_resolves_anything():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv)
    w.recent[A["id"]] = 0.02
    run(s, w, dv)
    w.recent[A["id"]], w.fail_accounts = 0.05, (A["id"],)
    r = run(s, w, dv)
    assert r["unreadableCount"] == 1 and r["unreadable"][0]["accountId"] == A["id"] and r["resolved"] == 0 and r["read"] == 1


def test_a_spent_google_quota_names_the_accounts_it_stopped_and_leaves_meta_alone():
    s, dv, w = configured(), Deliver(), World()
    w.quota = True
    r = run(s, w, dv, accounts=(A, B), workers=1)
    assert r["read"] == 0 and r["unreadableCount"] == 2 and "daily limit" in r["unreadable"][0]["error"] and "budget was used up" in r["unreadable"][1]["error"]


def test_a_meta_account_is_read_through_the_shared_creative_read():
    s, dv, w = configured(), Deliver(), World()
    rows = [T.meta_row("a1", st, impr=5000, clicks=250 if i < 3 else 60) for i, st in enumerate(("2026-09-17", "2026-09-24", "2026-10-01", "2026-10-08", "2026-10-15"))]
    pages = T.meta_api(rows)
    raw = T.meta_raw_read()
    run(s, w, dv, accounts=(M,), meta_raw_fn=lambda act: raw, meta_pages_fn=pages)
    r = run(s, w, dv, accounts=(M,), meta_raw_fn=lambda act: raw, meta_pages_fn=pages)
    assert r["read"] == 1
    assert [x["kind"] for x in s.issues_all("creative")] and all(x["platform"] == "meta" and x["fingerprint"].startswith("creative:act_1234567:") for x in s.issues_all("creative"))


def test_an_account_with_nothing_wrong_is_read_and_quiet():
    s, dv, w = configured(), Deliver(), World()
    r = run(s, w, dv)
    assert r["issues"] == 0 and r["read"] == 2 and r["unreadableCount"] == 0


def test_the_quiet_issue_of_small_findings_is_held_too_when_a_part_failed():
    s, dv, w = configured(), Deliver(), World()
    run(s, w, dv)
    w.gaps[A["id"]] = [gap("low")]
    run(s, w, dv)
    assert [(x["kind"], x["status"]) for x in s.issues_all("creative")] == [("small", "open")]
    w.gaps[A["id"]], w.fail_kinds = [], ("gaps",)
    assert run(s, w, dv)["resolved"] == 0 and [x["status"] for x in s.issues_all("creative")] == ["open"]
