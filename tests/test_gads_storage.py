"""Google Ads data kept in Postgres, so the agents stop asking Google for what they already have.

What is pinned here: campaign numbers are stored one day at a time and any range
is built from stored days, asking Google only for days never read or old enough
to have changed (one query per run of missing days); a day stored with no rows
is a quiet day, not a gap; Refresh asks again for the whole view, at most once
per cool-down per account, and the page is told when the numbers were read; a
saved copy stands in when Google cannot answer; and the SQL that does all this
works on a real Postgres (when DATABASE_URL points at one).
"""
import datetime as dt
import json
import os
import re
import sys
import time

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as appmod  # noqa: E402
from tests.gads_fakes import FakeStore, iso  # noqa: E402
from tracker import paid_gads as g  # noqa: E402
from tracker import paid_products as pp  # noqa: E402
from tracker import paid_segments as seg  # noqa: E402
from tracker import paid_store  # noqa: E402

CID = "1234567890"
TODAY = dt.date.today()


def d(offset):
    """The date `offset` days before today, as text."""
    return (TODAY - dt.timedelta(days=offset)).isoformat()


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body if body is not None else {}

    def json(self):
        return self._b


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k, v in (("GOOGLE_ADS_CLIENT_ID", "cid"), ("GOOGLE_ADS_CLIENT_SECRET", "sec"),
                 ("GOOGLE_ADS_DEVELOPER_TOKEN", "dev"), ("GOOGLE_ADS_REFRESH_TOKEN", "rt")):
        monkeypatch.setenv(k, v)
    g.reset_token_cache()
    g.reset_state()
    pp.reset_state()
    monkeypatch.setitem(g._TOKEN, "value", "tok")
    monkeypatch.setitem(g._TOKEN, "exp", 9e18)


def span(q):
    return re.search(r"BETWEEN '(\d{4}-\d\d-\d\d)' AND '(\d{4}-\d\d-\d\d)'", q).groups()


def days_between(a, b):
    x, y = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    return [(x + dt.timedelta(days=i)).isoformat() for i in range((y - x).days + 1)]


def google(log, quiet=(), campaigns=("1", "2"), fail=None):
    """A Google that returns one row per campaign per day for the asked span (none on `quiet` days)."""
    def http(url, headers=None, data=None, **k):
        q = json.loads(data)["query"]
        log.append(q)
        if fail:
            return Resp(fail[0], fail[1])
        a, b = span(q)
        rows = []
        for day in days_between(a, b):
            if day in quiet:
                continue
            for c in campaigns:
                rows.append({"campaign": {"id": c, "name": "Camp " + c, "status": "ENABLED", "advertisingChannelType": "SEARCH"},
                             "campaignBudget": {"amountMicros": "50000000"}, "segments": {"date": day},
                             "metrics": {"impressions": "100", "clicks": "10", "costMicros": "5000000", "conversions": 1.0,
                                         "conversionsValue": 50.0}})
        return Resp(200, {"results": rows})
    return http


QUOTA = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [{"errors": [{"message": "Too many requests.",
         "details": {"quotaErrorDetails": {"rateName": "Number of operations for explorer access", "retryDelay": "61458s"}}}]}]}}


# ── Days ───────────────────────────────────────────────────────────────────

def test_a_range_is_one_query_and_every_day_of_it_is_stored():
    s, log = FakeStore(), []
    out = g.campaign_days(s, CID, d(10), d(4), http=google(log))
    assert len(log) == 1 and span(log[0]) == (d(10), d(4))
    assert len(out["days"]) == 7 and len(out["rows"]) == 14 and [c["id"] for c in out["campaigns"]] == ["1", "2"]
    assert sorted(day for (_, day) in s.days) == days_between(d(10), d(4))


def test_a_second_range_costs_nothing_when_its_days_are_already_stored():
    s, log = FakeStore(), []
    g.campaign_days(s, CID, d(40), d(1), http=google(log))
    log.clear()
    for a, b in ((d(7), d(1)), (d(30), d(1)), (d(20), d(10))):
        out = g.campaign_days(s, CID, a, b, http=lambda *x, **k: pytest.fail("a stored range went to Google"))
        assert out["start"] == a and out["end"] == b and len(out["days"]) == (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days + 1
    assert log == []


def test_a_wider_range_asks_only_for_the_days_it_does_not_have():
    s, log = FakeStore(), []
    g.campaign_days(s, CID, d(20), d(10), http=google(log))
    log.clear()
    out = g.campaign_days(s, CID, d(30), d(5), http=google(log))
    assert [span(q) for q in log] == [(d(30), d(21)), (d(9), d(5))]         # one query for each side, none for the middle
    assert len(out["days"]) == 26 and len(out["rows"]) == 52


def test_a_quiet_day_is_stored_as_quiet_not_asked_for_again():
    s, log = FakeStore(), []
    out = g.campaign_days(s, CID, d(12), d(10), http=google(log, quiet={d(11)}))
    assert s.days[(CID, d(11))]["r"] == [] and len(out["rows"]) == 4        # three days, one quiet: 2 days x 2 campaigns
    log.clear()
    g.campaign_days(s, CID, d(12), d(10), http=google(log))
    assert log == []


def test_recent_days_go_stale_within_the_hour_and_old_days_do_not():
    s, log = FakeStore(), []
    g.campaign_days(s, CID, d(40), d(0), http=google(log))
    for key in list(s.dayat):
        s.dayat[key] -= 2 * 3600                                           # everything was read two hours ago
    log.clear()
    g.campaign_days(s, CID, d(40), d(0), http=google(log))
    asked = sorted(day for q in log for day in days_between(*span(q)))
    assert asked == [d(1), d(0)] or asked == [d(1), d(0)][::-1] or set(asked) == {d(1), d(0)}     # today and yesterday only
    for key in list(s.dayat):
        s.dayat[key] -= 2 * 86400                                          # now two days older: the last month is due, older days are not
    log.clear()
    g.campaign_days(s, CID, d(40), d(0), http=google(log))
    asked = {day for q in log for day in days_between(*span(q))}
    assert d(5) in asked and d(30) in asked and d(35) not in asked and d(40) not in asked


def test_days_with_a_stored_day_between_them_are_two_queries_not_one():
    s, log = FakeStore(), []
    g.campaign_days(s, CID, d(20), d(10), http=google(log))
    for gap in (d(15), d(13)):
        del s.days[(CID, gap)]                                               # the days in between stay stored
    log.clear()
    g.campaign_days(s, CID, d(20), d(10), http=google(log))
    assert [span(q) for q in log] == [(d(15), d(15)), (d(13), d(13))]       # one query for a day would pay for three


def test_day_lifetimes_follow_how_much_a_day_can_still_change():
    assert g._day_ttl(d(0)) == g._day_ttl(d(1)) == g.TTL_DAY_RECENT
    assert g._day_ttl(d(2)) == g._day_ttl(d(30)) == g.TTL_DAY_MONTH
    assert g._day_ttl(d(31)) == g._day_ttl(d(400)) == g.TTL_DAY_OLD


def test_refresh_asks_for_every_day_of_the_range_again():
    s, log = FakeStore(), []
    g.campaign_days(s, CID, d(20), d(10), http=google(log))
    log.clear()
    g.campaign_days(s, CID, d(20), d(10), force=True, http=google(log))
    assert [span(q) for q in log] == [(d(20), d(10))]


def test_a_stored_range_equals_the_range_read_whole():
    log = []
    rows = google(log)(None, data=json.dumps({"query": g.q_campaign_days(d(6), d(2))}))
    whole = g.parse_campaign_days(rows.json()["results"], d(6), d(2), [o[0] for o in g.OPTIONAL])
    s = FakeStore()
    out = g.campaign_days(s, CID, d(6), d(2), http=google([]))
    assert out == whole                                                     # splitting into days and joining them loses nothing


def test_when_google_cannot_answer_stored_days_of_any_age_stand_in_and_are_dated():
    s = FakeStore()
    g.campaign_days(s, CID, d(10), d(5), http=google([]))
    for key in list(s.dayat):
        s.dayat[key] -= 90 * 86400
    notes, stamps = [], []
    out = g.campaign_days(s, CID, d(10), d(5), notes=notes, stamps=stamps, http=google([], fail=(429, QUOTA)))
    assert len(out["days"]) == 6 and len(out["rows"]) == 12 and notes and stamps[0] <= iso(time.time() - 89 * 86400)
    with pytest.raises(g.OverQuota):                                       # a day never stored cannot be invented
        g.campaign_days(s, CID, d(20), d(5), http=google([], fail=(429, QUOTA)))


def test_a_range_saved_whole_before_days_were_stored_still_beats_showing_nothing():
    s = FakeStore()
    s.old[g.CACHE_V + "days:%s:%s:%s" % (CID, d(5), d(4))] = {"start": "legacy", "days": [], "rows": [], "campaigns": [], "missing": []}
    out = g.campaign_days(s, CID, d(5), d(4), http=google([], fail=(429, QUOTA)), notes=[])
    assert out["start"] == "legacy"


def test_the_oldest_read_is_found_across_different_utc_offsets():
    # 10:00 at +05:30 is 04:30 UTC: older than 06:00 UTC even though the text sorts after it
    assert g.oldest(["2026-10-14T06:00:00+00:00", "2026-10-14T10:00:00+05:30", None]) == "2026-10-14T10:00:00+05:30"
    assert g.oldest([None, ""]) is None and g.oldest([]) is None and g.oldest(None) is None


def test_a_column_any_stored_day_lacks_is_reported_missing():
    s = FakeStore()
    s.put_gads_days(CID, {d(3): {"m": [10], "r": [["1", "Camp", "ENABLED", "SEARCH", 5.0] + [0] * g.NB]},
                          d(2): {"m": [], "r": []}})
    out = g.campaign_days(s, CID, d(3), d(2), http=lambda *a, **k: pytest.fail("asked Google"))
    assert out["missing"] == [10]


# ── Whole results: memory, Postgres, refresh, cool-down ────────────────────

def test_a_hot_result_is_read_from_memory_not_postgres():
    s = FakeStore()
    calls = []
    make = lambda: calls.append(1) or {"v": 1}
    assert g._cached(s, "k1", 3600, make) == {"v": 1}
    reads = s.reads
    for _ in range(5):
        assert g._cached(s, "k1", 3600, make) == {"v": 1}
    assert len(calls) == 1 and s.reads == reads                            # five more reads, none reached the store


def test_refresh_skips_every_saved_copy_and_says_when_the_numbers_were_read():
    s, stamps = FakeStore(), []
    n = []
    make = lambda: n.append(1) or {"v": len(n)}
    g._cached(s, "k2", 3600, make)
    assert g._cached(s, "k2", 3600, make, stamps=stamps) == {"v": 1} and len(stamps) == 1
    stamps.clear()
    assert g._cached(s, "k2", 3600, make, force=True, stamps=stamps) == {"v": 2}
    assert g._cached(s, "k2", 3600, make) == {"v": 2}                      # and the new copy is the one kept
    assert stamps and abs(dt.datetime.fromisoformat(stamps[0]).timestamp() - time.time()) < 5


def test_a_saved_copy_past_its_lifetime_is_read_again():
    s = FakeStore()
    n = []
    make = lambda: n.append(1) or {"v": len(n)}
    g._cached(s, "k3", 3600, make)
    s.at["k3"] -= 7200
    g.forget()
    assert g._cached(s, "k3", 3600, make) == {"v": 2}


def test_a_refresh_that_google_refuses_keeps_the_saved_copy_on_screen():
    s = FakeStore()
    g._cached(s, "k4", 3600, lambda: {"v": "old"})
    notes = []

    def boom():
        raise g.OverQuota("spent")
    stamps = []
    assert g._cached(s, "k4", 3600, boom, notes, force=True, stamps=stamps) == {"v": "old"} and len(notes) == 1
    assert len(stamps) == 1 and stamps[0] == notes[0]                       # and the page is told how old that copy is


def test_refresh_can_only_be_pressed_once_per_cool_down_per_account():
    s = FakeStore()
    g.refresh_guard(s, "perf:111")
    with pytest.raises(g.RefreshTooSoon) as e:
        g.refresh_guard(s, "perf:111")
    assert 0 < e.value.retry_in <= g.REFRESH_COOLDOWN_S and "refreshed moments ago" in str(e.value)
    g.refresh_guard(s, "perf:222")                                         # another account is its own
    g.refresh_guard(s, "prod:111")                                         # and so is another agent
    s.at["rl:perf:111"] -= g.REFRESH_COOLDOWN_S + 5
    g.refresh_guard(s, "perf:111")                                         # after the cool-down it works again


def test_a_store_that_is_down_does_not_stop_a_refresh_or_a_read():
    class Down(FakeStore):
        def get_gads_entry(self, *a):
            raise RuntimeError("db down")

        def put_gads_cache(self, *a):
            raise RuntimeError("db down")
    s = Down()
    g.refresh_guard(s, "perf:1")
    assert g._cached(s, "k5", 3600, lambda: {"v": 1}) == {"v": 1}


# ── Products and segments ──────────────────────────────────────────────────

def test_the_top_products_get_their_days_and_an_unsafe_id_never_reaches_the_query():
    q = pp.q_product_days("2026-10-01", "2026-10-07", ["good-1", "bad'; DROP", "ok.2", ""])
    assert "'good-1', 'ok.2'" in q and "DROP" not in q and "segments.date" in q and "IN (" in q
    rows = [{"segments": {"productItemId": "GOOD-1", "date": "2026-10-02"}, "metrics": {"impressions": "10", "clicks": "2", "costMicros": "3000000"}},
            {"segments": {"productItemId": "other", "date": "2026-10-02"}, "metrics": {"clicks": "9"}},
            {"segments": {"productItemId": "good-1", "date": "2027-01-01"}, "metrics": {"clicks": "9"}}]
    out = pp.parse_series(rows, "2026-10-01", "2026-10-03", ["good-1", "quiet-2"])
    assert out["days"] == ["2026-10-01", "2026-10-02", "2026-10-03"] and out["ids"] == ["good-1", "quiet-2"]
    assert out["rows"]["good-1"][1][:3] == [10, 2, 3.0] and out["rows"]["good-1"][0] == [0] * pp.NP and out["rows"]["quiet-2"][1] == [0] * pp.NP


def seg_report(account, s, e, **k):
    return {"account": {"id": account["id"], "name": account["name"]}, "range": {"from": s.isoformat(), "to": e.isoformat()},
            "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "n": k.get("n", 1)}


@pytest.fixture
def seg_env(monkeypatch):
    seg.clear_cache()
    built = []

    def build(account, s, e, **k):
        built.append(1)
        return seg_report(account, s, e, n=len(built))
    monkeypatch.setattr(seg, "build_report", build)
    acct = {"id": CID, "name": "Acme", "currency": "USD"}
    return built, dict(accounts_fn=lambda: [acct], groups_fn=lambda: [])


def test_a_segment_report_is_saved_in_postgres_and_shared_across_workers(seg_env):
    built, kw = seg_env
    s = FakeStore()
    first = seg.report_for(CID, d(30), d(2), store=s, **kw)
    seg.clear_cache()                                                      # another worker, or a restart
    g.forget()
    second = seg.report_for(CID, d(30), d(2), store=s, **kw)
    assert len(built) == 1 and second == first and any(k.startswith(g.CACHE_V + "seg:" + CID) for k in s.cache)


def test_segment_refresh_builds_again_but_not_twice_in_a_row(seg_env):
    built, kw = seg_env
    s = FakeStore()
    seg.report_for(CID, d(30), d(2), store=s, **kw)
    assert seg.report_for(CID, d(30), d(2), store=s, force=True, **kw)["n"] == 2
    with pytest.raises(g.RefreshTooSoon):
        seg.report_for(CID, d(30), d(2), store=s, force=True, **kw)
    assert len(built) == 2


def test_a_spent_allowance_serves_the_last_saved_segment_report_and_dates_it(seg_env, monkeypatch):
    built, kw = seg_env
    s = FakeStore()
    seg.report_for(CID, d(30), d(2), store=s, **kw)
    s.at = {k: v - 5 * 86400 for k, v in s.at.items()}
    seg.clear_cache()
    g.forget()

    def boom(*a, **k):
        raise seg.gads.GadsError("quota", "spent")
    monkeypatch.setattr(seg, "build_report", boom)
    out = seg.report_for(CID, d(30), d(2), store=s, **kw)
    assert out["n"] == 1 and out["stale"]
    seg.clear_cache()
    with pytest.raises(seg.gads.GadsError):                                # a report never saved has nothing to fall back on
        seg.report_for(CID, d(90), d(60), store=s, **kw)


# ── Routes ─────────────────────────────────────────────────────────────────

STAFF = "someone@position2.com"
ACCTS = [{"id": CID, "name": "Acme", "currency": "USD", "tz": "", "level": 1}]


def client_as(email):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": email, "name": "T"}
    return c


@pytest.fixture
def routes(monkeypatch):
    fs = FakeStore()
    for name in ("get_gads_entry", "put_gads_cache", "get_gads_cache", "gads_calls", "get_gads_block"):
        monkeypatch.setattr(paid_store, name, getattr(fs, name))
    monkeypatch.setattr(g, "list_accounts", lambda st, **k: ACCTS)
    seen = []

    def pair(st, cid, cur, cmp, with_sis=True, with_shares=True, force=False, stamps=None, **k):
        seen.append(force)
        if stamps is not None:
            stamps.append("2026-10-14T08:00:00+00:00")
        return {"start": cur[0], "days": [], "campaigns": [], "rows": []}, None, []
    monkeypatch.setattr(g, "performance_pair", pair)
    def analysis(st, cid, cur, cmp, force=False, stamps=None, **k):
        seen.append(force)
        if stamps is not None:
            stamps.append("2026-10-14T07:00:00+00:00")
        return {"products": []}, None, []
    monkeypatch.setattr(pp, "analysis", analysis)
    return seen


def test_a_view_is_never_a_refresh_and_says_when_it_was_read(routes):
    c = client_as(STAFF)
    q = "?account=%s&start=2026-10-01&end=2026-10-07" % CID
    j = c.get("/p2/paid-marketing/api/gads/performance" + q).get_json()
    assert routes == [False] and j["fetchedAt"] == "2026-10-14T08:00:00+00:00"
    assert c.get("/p2/paid-marketing/api/gads/performance" + q).status_code == 200      # viewing twice never trips the cool-down
    assert routes == [False, False]


def test_refresh_is_passed_on_and_pressing_it_again_at_once_is_refused_with_the_wait(routes):
    c = client_as(STAFF)
    url = "/p2/paid-marketing/api/gads/performance?account=%s&start=2026-10-01&end=2026-10-07&refresh=1" % CID
    assert c.get(url).status_code == 200 and routes == [True]
    r = c.get(url)
    j = r.get_json()
    assert r.status_code == 429 and j["cooldown"] is True and 0 < j["retryIn"] <= g.REFRESH_COOLDOWN_S and routes == [True]
    purl = "/p2/paid-marketing/api/gads/products?account=%s&start=2026-10-01&end=2026-10-07&refresh=1" % CID
    p = c.get(purl)
    assert p.status_code == 200 and p.get_json()["fetchedAt"] == "2026-10-14T07:00:00+00:00"       # the products have their own cool-down
    again = c.get(purl)
    assert again.status_code == 429 and again.get_json()["cooldown"] is True and routes == [True, True]


def test_the_account_list_may_be_kept_by_the_browser_for_a_few_minutes(routes):
    r = client_as(STAFF).get("/p2/paid-marketing/api/gads/accounts")
    assert r.status_code == 200 and "max-age=300" in r.headers["Cache-Control"] and "private" in r.headers["Cache-Control"]
    r2 = client_as(STAFF).get("/p2/paid-marketing/api/gads/performance?account=%s&start=2026-10-01&end=2026-10-07" % CID)
    assert "max-age" not in (r2.headers.get("Cache-Control") or "")        # numbers are never kept by the browser


def test_segment_refresh_reaches_the_report_and_a_cool_down_is_a_429(monkeypatch):
    seen = []

    def report_for(account, a, b, force=False):
        seen.append(force)
        if len(seen) == 2:
            raise g.RefreshTooSoon("moments ago", retry_in=90)
        return {"account": {"name": "Acme"}, "range": {}, "segments": [], "total": {}, "types": [], "warnings": []}
    monkeypatch.setattr(seg, "report_for", report_for)
    monkeypatch.setattr(g, "config_problem", lambda: None)
    from tracker import paid_segment_insights
    monkeypatch.setattr(paid_segment_insights, "build", lambda report: [])
    c = client_as(STAFF)
    base = "/p2/paid-marketing/api/segments/report?account=%s&from=2026-10-01&to=2026-10-07" % CID
    assert c.get(base).status_code == 200 and seen == [False]
    r = c.get(base + "&refresh=1")
    assert r.status_code == 429 and r.get_json()["kind"] == "cooldown" and r.get_json()["retryIn"] == 90 and seen == [False, True]


# ── The real SQL ───────────────────────────────────────────────────────────

pg = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="no Postgres to test against")


@pg
def test_the_day_store_and_the_entries_work_on_a_real_postgres():
    cid = "999%07d" % (int(time.time()) % 10_000_000)
    paid_store.put_gads_days(cid, {"2026-01-01": {"m": [], "r": [["1", "A", "ENABLED", "SEARCH", 5.0, 1, 2]]}, "2026-01-02": {"m": [10], "r": []}})
    got = paid_store.get_gads_days(cid, "2026-01-01", "2026-01-31")
    assert sorted(got) == ["2026-01-01", "2026-01-02"] and got["2026-01-01"][0]["r"][0][1] == "A" and got["2026-01-02"][0]["m"] == [10]
    assert paid_store.get_gads_days(cid, "2026-02-01", "2026-02-28") == {} and paid_store.get_gads_days("0000000000", "2026-01-01", "2026-01-31") == {}
    paid_store.put_gads_days(cid, {"2026-01-01": {"m": [], "r": []}})
    assert paid_store.get_gads_days(cid, "2026-01-01", "2026-01-01")["2026-01-01"][0]["r"] == []        # replaced, not duplicated
    key = "test:%s" % cid
    paid_store.put_gads_cache(key, {"a": [1, 2]})
    value, saved = paid_store.get_gads_entry(key, 60)
    assert value == {"a": [1, 2]} and abs(dt.datetime.fromisoformat(saved).timestamp() - time.time()) < 30
    assert paid_store.get_gads_entry(key, -1) is None and paid_store.get_gads_entry("test:none", 60) is None
    end_to_end = FakeStore()                                               # and the whole path with the real store
    out = g.campaign_days(paid_store, cid, "2026-01-05", "2026-01-06", http=google([]))
    again = g.campaign_days(paid_store, cid, "2026-01-05", "2026-01-06", http=lambda *a, **k: pytest.fail("went to Google"))
    assert out == again and end_to_end is not None
