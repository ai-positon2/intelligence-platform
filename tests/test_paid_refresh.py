"""Pacing Desk: building a run from Google Ads and the saved budgets (tracker/paid_refresh.py), with the real engine in the loop."""
import datetime as dt
from types import SimpleNamespace

import pytest

pytest.importorskip("pandas")

from tracker import paid_gads_segments as gads            # noqa: E402
from tracker import paid_pacing as pp                      # noqa: E402
from tracker import paid_pacing_budgets as budgets         # noqa: E402
from tracker import paid_refresh as rf                     # noqa: E402

NOW = dt.datetime(2026, 10, 12, 15, tzinfo=dt.timezone.utc)      # the 12th: the run is for the 12th, data through the 11th


def utc(*a):
    return dt.datetime(*a, tzinfo=dt.timezone.utc)


# ── A pretend Google Ads: three accounts, two budgeted ──────────────────────

ACCOUNTS = [{"id": "1111111111", "name": "Acme Dental", "currency": "USD", "timeZone": "America/New_York"},
            {"id": "2222222222", "name": "Beta Imaging", "currency": "USD", "timeZone": "America/New_York"},
            {"id": "3333333333", "name": "Stray Co", "currency": "USD", "timeZone": "America/New_York"},
            {"id": "4444444444", "name": "Delta Co", "currency": "USD", "timeZone": "America/New_York"}]

CAMPAIGNS = {   # account id -> [(campaign id, name, type, daily budget $, daily spend $, conversions a day)]
    "1111111111": [("11", "P2_Brand", "SEARCH", 60, 35, 1.0), ("12", "P2_Generic", "SEARCH", 80, 78, 2.0), ("13", "P2_PMax", "PERFORMANCE_MAX", 50, 20, 0.5)],
    "2222222222": [("21", "P2_Brand", "SEARCH", 40, 38, 3.0), ("22", "P2_Generic", "SEARCH", 40, 10, 0.2)],
    "3333333333": [("31", "P2_Brand", "SEARCH", 20, 20, 1.0)],
    "4444444444": [("41", "P2_Brand", "SEARCH", 10, 10, 1.0)],
}


def day_rows(cid, last=11):
    out = []
    for camp, name, kind, budget, spend, conv in CAMPAIGNS[cid]:
        for d in range(1, last + 1):
            out.append({"campaign": {"id": camp, "name": name, "status": "ENABLED", "advertisingChannelType": kind, "biddingStrategyType": "MAXIMIZE_CONVERSIONS"},
                        "campaignBudget": {"id": "b" + camp, "name": name, "amountMicros": str(budget * 1_000_000), "explicitlyShared": False},
                        "segments": {"date": "2026-10-%02d" % d},
                        "metrics": {"impressions": "400", "clicks": "40", "costMicros": str(int(spend * 1e6)), "conversions": conv,
                                    "conversionsValue": conv * 100, "searchImpressionShare": 0.5}})
    return out


def enabled_rows(cid):
    return [{"campaign": {"id": c, "name": n, "status": "ENABLED", "advertisingChannelType": k},
             "campaignBudget": {"name": n, "amountMicros": str(b * 1_000_000), "explicitlyShared": False}} for c, n, k, b, _s, _c in CAMPAIGNS[cid]]


class Ads:
    def __init__(self, fail=None):
        self.fail, self.calls = fail or {}, []

    def __call__(self, cid, gaql):
        self.calls.append(cid)
        if cid in self.fail:
            raise gads.GadsError(*self.fail[cid])
        return day_rows(cid) if "segments.date" in gaql else enabled_rows(cid)


class Budgets:
    def __init__(self, **by_month):
        self.by = by_month

    def list_month(self, m):
        return [dict(r) for r in self.by.get(m, [])]

    def months(self):
        return sorted(m for m, rows in self.by.items() if rows)


def book(*rows):
    return [budgets.check_row({"client": c, "account": a, "allocated": n}) for c, a, n in rows]


class RunStore:
    """The slice of tracker.paid_store a refresh touches."""

    def __init__(self, latest=None, state=None, read_age_s=None):
        self.saved, self.state, self.latest, self.cache = [], state or {}, latest, {}
        if read_age_s is not None:
            self.cache[rf.READ_KEY] = ({"asOf": "2026-10-12", "accounts": 4, "rows": [], "unread": [], "notes": [], "readAt": (NOW - dt.timedelta(seconds=read_age_s)).isoformat()},
                                       NOW - dt.timedelta(seconds=read_age_s))

    def put_gads_cache(self, key, value):
        self.cache[key] = (value, rf._now())

    def get_gads_entry(self, key, ttl):
        hit = self.cache.get(key)
        if hit is None or (rf._now() - hit[1]).total_seconds() > ttl:
            return None
        return hit[0], hit[1].isoformat()

    def gads_saved_at(self, key):
        return self.cache[key][1].isoformat() if key in self.cache else None

    def claim_refresh(self, by, stale_s):
        if self.state.get("state") == "running":
            return False
        self.state = {"state": "running", "by": by, "startedAt": dt.datetime.now(dt.timezone.utc).isoformat()}
        return True

    def update_refresh(self, **f):
        self.state.update(f)

    def get_refresh(self):
        return dict(self.state)

    def save_snapshot(self, *a):
        self.saved.append(a)

    def latest_date(self):
        return self.latest[0] if self.latest else None

    def get_snapshot(self, date):
        return {"clients": self.latest[1]} if self.latest else None


@pytest.fixture
def clock(monkeypatch):
    """The refresh's own clock stopped at NOW (8am Pacific on the 12th), so ages in these tests are exact."""
    monkeypatch.setattr(rf, "_now", lambda: NOW)


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(rf, "config_problem", lambda: None)


def run(store=None, bstore=None, ads=None, accounts=ACCOUNTS, steps=None, reuse=False, now=NOW):
    store = store or RunStore()
    res = rf.refresh(store, pp, (steps.append if steps is not None else (lambda s: None)),
                     budget_store=bstore or Budgets(**{"2026-10": book(("Acme", "Acme Dental", 3000), ("Beta", "Beta Imaging", 1500))}),
                     accounts_fn=lambda: accounts, search_fn=ads or Ads(), now=now, reuse=reuse)
    return store, res


# ── The whole run ───────────────────────────────────────────────────────────

def test_a_run_is_built_from_google_ads_and_the_saved_budgets(configured):
    steps = []
    store, res = run(steps=steps)
    assert (res["date"], res["dataThrough"], res["clients"], res["accounts"]) == ("2026-10-12", "2026-10-11", 2, 2)
    assert [s.split(" ")[0] for s in steps] == ["Listing", "Reading", "Running", "Publishing"]
    meta, clients, latest = store.saved[0]
    assert latest == "2026-10-12" and sorted(s for s, _c in clients) == ["acme", "beta"]
    acme = next(c for s, c in clients if s == "acme")["accounts"][0]
    assert acme["name"] == "Acme Dental" and acme["allocated"] == 3000 and round(acme["spend"]) == 11 * (35 + 78 + 20)
    # 11 days of $133 a day against a $3000 month: the engine's own arithmetic, not ours
    assert acme["landing"] in pp.LANDINGS and len(acme["campaigns"]) == 3
    assert {c["budget"] for c in acme["campaigns"]} == {60.0, 80.0, 50.0}          # the REAL daily budgets, straight from Google Ads
    assert all(c["conv"] > 0 for c in acme["campaigns"])
    assert meta["source"]["kind"] == "googleAds" and meta["source"]["accounts"] == 4 and meta["source"]["budgets"] == {"kind": "saved", "month": "2026-10"}


def test_conversion_value_reaches_the_engine(configured):
    store, _ = run()
    acme = next(c for s, c in store.saved[0][1] if s == "acme")["accounts"][0]
    assert acme["convValue"] > 0


def test_an_account_with_spend_and_no_budget_row_is_named_not_lost(configured):
    store, res = run()
    warnings = store.saved[0][0]["portfolioWarnings"]
    assert any("Stray Co" in w and "no Google budget row" in w for w in warnings)
    assert not any("Allocations" in w and "Budget Tracker" in w for w in warnings)


def test_an_account_google_would_not_return_is_named_and_left_out(configured):
    store, res = run(ads=Ads(fail={"3333333333": ("permission", "User does not have access")}))
    assert res["unread"] == 1 and any("Stray Co" in n and "User does not have access" in n for n in res["notes"])
    assert any("Stray Co" in n and "Its pacing is missing" in n for n in store.saved[0][0]["source"]["notes"])
    assert not any("would not return" in w for w in store.saved[0][0]["portfolioWarnings"])        # budget warnings stay budget warnings


def test_too_many_unreadable_accounts_publish_nothing(configured):
    store = RunStore()
    with pytest.raises(rf.RefreshError, match="2 of 4 accounts"):
        run(store=store, ads=Ads(fail={"1111111111": ("permission", "no"), "2222222222": ("permission", "no")}))
    assert store.saved == []


def test_a_spent_allowance_is_reported_and_nothing_is_published(configured):
    store = RunStore()
    with pytest.raises(rf.RefreshError, match="would not answer: used up"):
        run(store=store, ads=Ads(fail={a["id"]: ("quota", "used up") for a in ACCOUNTS}))
    assert store.saved == []


def test_no_accounts_and_no_rows_are_reported_in_words(configured):
    with pytest.raises(rf.RefreshError, match="no enabled client accounts"):
        run(accounts=[])
    empty = lambda cid, gaql: []
    with pytest.raises(rf.RefreshError, match="No campaign rows came back"):
        run(ads=empty)


def test_without_any_allocations_the_run_says_where_to_add_them(configured):
    store = RunStore()
    with pytest.raises(rf.RefreshError, match="no October 2026 allocations"):
        run(store=store, bstore=Budgets())
    assert store.saved == []


def test_last_months_allocations_are_carried_and_the_run_says_so(configured):
    store, res = run(bstore=Budgets(**{"2026-09": book(("Acme", "Acme Dental", 3000), ("Beta", "Beta Imaging", 1500))}))
    assert res["budgets"] == "carried" and any("carried over from September 2026" in n for n in res["notes"])
    assert store.saved[0][0]["source"]["budgets"] == {"kind": "carried", "from": "2026-09", "month": "2026-10"}


def test_the_first_months_allocations_come_from_the_last_stored_run(configured):
    last = ("2026-09-30", [{"name": "Acme", "accounts": [{"name": "Acme Dental", "allocated": 3000}]}])
    store, res = run(store=RunStore(latest=last), bstore=Budgets())
    assert res["budgets"] == "seeded" and any("taken from the run of 2026-09-30" in n for n in res["notes"])


def test_a_missing_google_ads_setup_stops_the_run_before_anything_is_read(monkeypatch):
    monkeypatch.setattr(rf, "config_problem", lambda: "GOOGLE_ADS_DEVELOPER_TOKEN is not set.")
    ads = Ads()
    with pytest.raises(rf.RefreshError, match="GOOGLE_ADS_DEVELOPER_TOKEN"):
        run(ads=ads)
    assert ads.calls == []


def test_an_engine_that_stops_is_reported_and_flags_are_not(monkeypatch):
    seen = []

    def fake_run(script, args, cwd, channel="Google"):
        seen.append(script)
        if script == "validate.py":
            return 1, "[FAIL] x: 1 issue(s)\n   ✗ OIA/Wolf River/MRI: below the $1/day policy floor\n"
        import json
        import os
        os.makedirs(os.path.join(cwd, "out"), exist_ok=True)
        with open(os.path.join(cwd, "out", "dashboard.json"), "w") as f:
            json.dump({"asOf": "2026-10-12"}, f)
        return 0, "ok"
    monkeypatch.setattr(rf, "_run", fake_run)
    body, issues = rf.run_engine([["Day"]], [["Client"]], dt.date(2026, 10, 12))
    assert body == {"asOf": "2026-10-12"} and issues == ["OIA/Wolf River/MRI: below the $1/day policy floor"] and seen == ["validate.py", "export_dashboard.py"]
    monkeypatch.setattr(rf, "_run", lambda s, a, c, ch="Google": (1, "Traceback...\nKeyError: 'Account'"))
    with pytest.raises(rf.RefreshError, match="KeyError"):
        rf.run_engine([["Day"]], [["Client"]], dt.date(2026, 10, 12))


# ── Starting a run ──────────────────────────────────────────────────────────

class Inline:
    """threading.Thread that runs its target at once."""

    def __init__(self, target, **kw):
        self.target = target

    def start(self):
        self.target()


@pytest.fixture
def inline(monkeypatch):
    monkeypatch.setattr(rf.threading, "Thread", Inline)


def test_a_started_run_that_fails_keeps_the_old_run_and_says_why(monkeypatch, inline):
    s = RunStore()
    monkeypatch.setattr(rf, "refresh", lambda *a, **k: (_ for _ in ()).throw(rf.RefreshError("Google Ads would not answer: used up")))
    rf.start(s, pp, "ana@position2.com")
    assert s.state["state"] == "failed" and s.state["error"] == "Google Ads would not answer: used up" and s.saved == []
    monkeypatch.setattr(rf, "refresh", lambda *a, **k: 1 / 0)
    s.state = {}
    rf.start(s, pp, "ana@position2.com")
    assert s.state["error"] == "The refresh failed unexpectedly (ZeroDivisionError)."


def test_a_started_run_that_works_records_its_result(monkeypatch, inline):
    s = RunStore()
    monkeypatch.setattr(rf, "refresh", lambda *a, **k: {"date": "2026-10-12", "clients": 2})
    rf.start(s, pp, "ana@position2.com")
    assert s.state["state"] == "done" and s.state["result"]["date"] == "2026-10-12" and s.state["finishedAt"] and s.state["error"] is None


def test_two_runs_at_once_are_refused(monkeypatch):
    monkeypatch.setattr(rf.threading, "Thread", lambda target, **k: SimpleNamespace(start=lambda: None))      # the worker never finishes
    s = RunStore()
    rf.start(s, pp, "ana@position2.com")
    with pytest.raises(rf.RefreshBusy):
        rf.start(s, pp, "ben@position2.com")


def test_google_ads_is_not_read_again_right_after_a_read_and_the_wait_is_said(monkeypatch, inline, clock):
    s = RunStore(read_age_s=180)
    with pytest.raises(rf.RefreshTooSoon, match="Google Ads was just read. Try again in 7 minutes") as e:
        rf.start(s, pp, "ana@position2.com")
    assert 415 <= e.value.retry_in <= 420 and s.state == {}
    s2 = RunStore(read_age_s=11 * 60)
    monkeypatch.setattr(rf, "refresh", lambda *a, **k: {"date": "d", "clients": 1})
    rf.start(s2, pp, "ana@position2.com")                       # past the cooldown
    assert s2.state["state"] == "done"


def test_a_failed_run_can_be_retried_after_a_minute(monkeypatch, inline, clock):
    now = NOW
    s = RunStore(state={"state": "failed", "finishedAt": (now - dt.timedelta(seconds=20)).isoformat()})
    with pytest.raises(rf.RefreshTooSoon, match="Try again in 4[01] seconds|Try again in 39 seconds"):
        rf.start(s, pp, "ana@position2.com")
    s.state["finishedAt"] = (now - dt.timedelta(seconds=70)).isoformat()
    monkeypatch.setattr(rf, "refresh", lambda *a, **k: {"date": "d", "clients": 1})
    rf.start(s, pp, "ana@position2.com")


def test_changed_allocations_run_again_on_the_saved_read_with_no_wait(monkeypatch, inline, clock):
    """The read was made 3 minutes ago, so a refresh is held back, but a recompute (an allocation changed) is not and does not ask Google."""
    seen = []
    monkeypatch.setattr(rf, "refresh", lambda *a, **k: seen.append(k.get("reuse")) or {"date": "d", "clients": 1})
    s = RunStore(read_age_s=180)
    rf.start(s, pp, "ana@position2.com", recompute=True)
    assert seen == [True] and s.state["state"] == "done"


def test_a_recompute_without_a_usable_read_is_a_normal_refresh_with_its_wait(monkeypatch, inline, clock):
    monkeypatch.setattr(rf, "refresh", lambda *a, **k: {"date": "d", "clients": 1})
    with pytest.raises(rf.RefreshTooSoon):                      # a read from yesterday is not usable, but one 3 minutes old still counts for the wait
        s = RunStore(read_age_s=180)
        s.cache[rf.READ_KEY][0]["readAt"] = (NOW - dt.timedelta(days=1)).isoformat()
        rf.start(s, pp, "ana@position2.com", recompute=True)
    seen = []
    monkeypatch.setattr(rf, "refresh", lambda *a, **k: seen.append(k.get("reuse")) or {"date": "d", "clients": 1})
    rf.start(RunStore(), pp, "ana@position2.com", recompute=True)          # nothing saved at all: reads Google (reuse is False)
    assert seen == [False]


def test_a_run_started_normally_never_reuses_a_read(monkeypatch, inline, clock):
    seen = []
    monkeypatch.setattr(rf, "refresh", lambda *a, **k: seen.append(k.get("reuse")) or {"date": "d", "clients": 1})
    rf.start(RunStore(), pp, "ana@position2.com")
    assert seen == [False]


def test_the_page_only_starts_a_run_when_one_is_due(monkeypatch, inline):
    now = dt.datetime.now(dt.timezone.utc)
    s = RunStore()
    with pytest.raises(rf.RefreshTooSoon, match="already built today"):
        rf.start(s, pp, "page", auto=True, last_built=now.isoformat())
    monkeypatch.setattr(rf, "refresh", lambda *a, **k: {"date": "d", "clients": 1})
    rf.start(s, pp, "page", auto=True, last_built=(now - dt.timedelta(days=2)).isoformat())
    assert s.state["state"] == "done"


# ── When a run is due, and the cooldown ─────────────────────────────────────

def test_the_cooldown():
    read = utc(2026, 10, 12, 12).isoformat()
    assert rf.cooldown_left({}, read, utc(2026, 10, 12, 12, 4)) == 360
    assert rf.cooldown_left({}, read, utc(2026, 10, 12, 12, 10)) == 0 and rf.cooldown_left({}, read, utc(2026, 10, 12, 13)) == 0
    failed = {"state": "failed", "finishedAt": utc(2026, 10, 12, 12).isoformat()}
    assert rf.cooldown_left(failed, None, utc(2026, 10, 12, 12, 0, 20)) == 40 and rf.cooldown_left(failed, None, utc(2026, 10, 12, 12, 2)) == 0
    assert rf.cooldown_left(failed, read, utc(2026, 10, 12, 12, 0, 20)) == 580                  # the longer of the two waits
    done = {"state": "done", "finishedAt": utc(2026, 10, 12, 12).isoformat()}
    assert rf.cooldown_left(done, None, utc(2026, 10, 12, 12, 1)) == 0                           # a good run alone never holds anyone back
    for st in ({}, None, {"state": "running"}, {"state": "done"}, {"state": "failed", "finishedAt": "garbage"}):
        assert rf.cooldown_left(st, "garbage") == 0 and rf.cooldown_left(st) == 0


def test_a_run_is_due_when_none_was_built_since_the_pacific_day_began():
    now = utc(2026, 10, 12, 20)                                         # 1pm Pacific on the 12th
    assert rf.is_due({}, None, now) is True                             # nothing built yet
    assert rf.is_due({}, utc(2026, 10, 12, 15).isoformat(), now) is False               # built at 8am Pacific today
    assert rf.is_due({}, utc(2026, 10, 12, 5).isoformat(), now) is True                 # built at 10pm Pacific yesterday
    assert rf.is_due({}, utc(2026, 10, 11, 20).isoformat(), now) is True
    assert rf.is_due({}, "garbage", now) is True
    assert rf.is_due({}, dt.datetime(2026, 10, 12, 15).isoformat(), now) is False       # no zone on the stored time: read as UTC


def test_a_run_is_not_due_while_one_runs_or_just_ran_even_if_it_failed():
    now = utc(2026, 10, 12, 20)
    old = utc(2026, 10, 10).isoformat()
    assert rf.is_due({"state": "running"}, old, now) is False
    assert rf.is_due({"state": "failed", "finishedAt": utc(2026, 10, 12, 19, 45).isoformat()}, old, now) is False
    assert rf.is_due({"state": "failed", "finishedAt": utc(2026, 10, 12, 19, 0).isoformat()}, old, now) is True


def test_status_says_whether_it_can_run_may_run_now_and_is_due(monkeypatch):
    monkeypatch.setattr(rf, "config_problem", lambda: None)
    st = rf.status(RunStore(), last_built=None)
    assert st["configProblem"] is None and st["cooldownLeft"] == 0 and st["due"] is True
    monkeypatch.setattr(rf, "config_problem", lambda: "GOOGLE_ADS_CLIENT_ID is not set.")
    st = rf.status(RunStore(), last_built=None)
    assert st["configProblem"] and st["due"] is False                                 # nothing to start without Google Ads


def test_a_running_refresh_its_worker_lost_reads_as_failed():
    now = dt.datetime.now(dt.timezone.utc)
    s = RunStore(state={"state": "running", "startedAt": (now - dt.timedelta(minutes=11)).isoformat()})
    assert rf.status(s)["state"] == "failed"
    s.state["startedAt"] = now.isoformat()
    assert rf.status(s)["state"] == "running"


def test_pacific_day_is_the_day_in_los_angeles():
    assert rf.pacific_day(utc(2026, 10, 12, 5)) == dt.date(2026, 10, 11) and rf.pacific_day(utc(2026, 10, 12, 8)) == dt.date(2026, 10, 12)


# ── What a stored run says about where it came from ─────────────────────────

def test_a_runs_source_is_kept_cleaned_and_capped():
    from tests.test_paid_marketing import dashboard
    src = {"kind": "googleAds", "accounts": 4, "unread": 1, "readAt": "2026-10-12T15:00:00+00:00", "budgets": {"kind": "saved", "month": "2026-10"},
           "notes": ["a" * 400] + ["n%d" % i for i in range(100)], "secret": "dropped"}
    meta, _, _ = pp.build_seed(dashboard(source=src))
    got = meta["source"]
    assert got["kind"] == "googleAds" and got["accounts"] == 4 and got["budgets"] == {"kind": "saved", "month": "2026-10"} and "secret" not in got
    assert len(got["notes"]) == pp.MAX_SOURCE_NOTES and len(got["notes"][0]) == 300


def test_a_pushed_run_has_no_source_and_a_source_without_notes_gets_an_empty_list():
    from tests.test_paid_marketing import dashboard
    assert pp.build_seed(dashboard())[0]["source"] is None
    assert pp.build_seed(dashboard(source={"kind": "googleAds"}))[0]["source"]["notes"] == []
    assert pp.build_seed(dashboard(source={"kind": "googleAds", "notes": "text"}))[0]["source"]["notes"] == []
    assert pp.build_seed(dashboard(source="googleAds"))[0]["source"] is None


# ── Running again on the saved read ─────────────────────────────────────────

def allocation_of(store, client="acme"):
    meta, clients, _ = store.saved[-1]
    return next(c for s, c in clients if s == client)["accounts"][0]["allocated"]


def test_every_read_of_google_is_saved_for_a_rerun(configured):
    store, res = run()
    read, _ = store.cache[rf.READ_KEY]
    assert read["asOf"] == "2026-10-12" and read["accounts"] == 4 and len(read["rows"]) > 0 and read["readAt"] == NOW.isoformat() and res["reusedRead"] is False


def test_a_changed_allocation_is_run_on_the_saved_read_without_asking_google_again(configured):
    ads = Ads()
    store, _ = run(ads=ads)
    asked = len(ads.calls)
    changed = Budgets(**{"2026-10": book(("Acme", "Acme Dental", 3500), ("Beta", "Beta Imaging", 1500))})
    steps = []
    store, res = run(store=store, bstore=changed, ads=ads, reuse=True, steps=steps, now=NOW + dt.timedelta(minutes=5))
    assert len(ads.calls) == asked and res["reusedRead"] is True and allocation_of(store) == 3500
    assert any(s.startswith("Using the Google Ads read of 15:00") for s in steps) and not any(s.startswith("Reading") for s in steps)
    assert store.saved[-1][0]["source"]["readAt"] == NOW.isoformat()                 # the run still says when Google was actually read


def test_a_read_from_an_earlier_pacific_day_is_not_run_again(configured):
    ads = Ads()
    store, _ = run(ads=ads)
    asked = len(ads.calls)
    run(store=store, ads=ads, reuse=True, now=NOW + dt.timedelta(hours=17))           # 1am Pacific on the 13th
    assert len(ads.calls) > asked


def test_a_read_that_cannot_be_trusted_is_read_again_not_guessed_at(configured):
    ads = Ads()
    for bad in ({"asOf": "2026-10-12"}, {"readAt": "garbage"}, {"readAt": None}):
        store = RunStore()
        store.cache[rf.READ_KEY] = (bad, NOW)
        before = len(ads.calls)
        run(store=store, ads=ads, reuse=True)
        assert len(ads.calls) > before, bad


def test_a_store_that_cannot_find_the_read_means_reading_google(configured):
    class Broken(RunStore):
        def get_gads_entry(self, key, ttl):
            raise RuntimeError("down")
    ads = Ads()
    run(store=Broken(), ads=ads, reuse=True)
    assert len(ads.calls) > 0


def test_a_read_that_cannot_be_saved_does_not_stop_the_run(configured):
    class NoCache(RunStore):
        def put_gads_cache(self, key, value):
            raise RuntimeError("down")
    store, res = run(store=NoCache())
    assert res["clients"] == 2 and store.saved
