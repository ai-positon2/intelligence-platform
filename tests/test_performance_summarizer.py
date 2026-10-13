"""Performance Summarizer: live Google Ads for the Paid Marketing Agents.

What is pinned here is what could silently go wrong with money numbers: the
account list keeps only real, enabled, non-manager client accounts; a range
is parsed into the compact rows the page charts without losing a quiet day;
the request budget refuses calls past the day's limit instead of letting
Google start rejecting the shared developer token; a result is cached so a
repeat view costs nothing; a bad account, range or missing connection is
answered with a reason, never a crash; and the page script only reaches for
elements the template actually has.
"""
import json
import os
import re
import sys
from datetime import date, timedelta

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as appmod  # noqa: E402
from tracker import paid_gads as g  # noqa: E402
from tracker import paid_store as store  # noqa: E402

STAFF = "someone@position2.com"
ADMIN = "reporting@position2.com"


class FakeStore:
    """The three store calls paid_gads makes, in memory."""
    def __init__(self, calls=0):
        self.calls, self.cache = calls, {}

    def bump_gads_calls(self, day):
        self.calls += 1
        return self.calls

    def get_gads_cache(self, key, ttl):
        return self.cache.get(key)

    def put_gads_cache(self, key, value):
        self.cache[key] = value

    def gads_calls(self, day):
        return self.calls


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body if body is not None else {}

    def json(self):
        return self._b


@pytest.fixture(autouse=True)
def ads_env(monkeypatch):
    for k, v in (("GOOGLE_ADS_CLIENT_ID", "cid"), ("GOOGLE_ADS_CLIENT_SECRET", "sec"),
                 ("GOOGLE_ADS_DEVELOPER_TOKEN", "dev"), ("GOOGLE_ADS_REFRESH_TOKEN", "rt")):
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", raising=False)
    g.reset_token_cache()
    monkeypatch.setitem(g._TOKEN, "value", "tok")
    monkeypatch.setitem(g._TOKEN, "exp", 9e18)


# ── Configuration ──────────────────────────────────────────────────────────

def test_a_missing_credential_is_named(monkeypatch):
    assert g.config_problem() is None
    monkeypatch.delenv("GOOGLE_ADS_REFRESH_TOKEN")
    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "  ")
    p = g.config_problem()
    assert "GOOGLE_ADS_REFRESH_TOKEN" in p and "GOOGLE_ADS_DEVELOPER_TOKEN" in p and "GOOGLE_ADS_CLIENT_ID" not in p


def test_the_manager_account_defaults_and_is_cleaned(monkeypatch):
    assert g.login_customer() == "9923720096"
    monkeypatch.setenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "123-456-7890")
    assert g.login_customer() == "1234567890"
    monkeypatch.setenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "abc")
    assert g.login_customer() == "9923720096"


# ── Parsing what Google sends ──────────────────────────────────────────────

def acct(id_, name, manager=False, status="ENABLED", test=False, level=1, cur="USD"):
    return {"customerClient": {"id": id_, "descriptiveName": name, "manager": manager, "status": status,
                               "testAccount": test, "level": level, "currencyCode": cur, "timeZone": "America/New_York"}}


def test_the_account_list_keeps_only_real_client_accounts():
    rows = [acct("9923720096", "Position2-Main", manager=True, level=0), acct("1111111111", "Sub manager", manager=True),
            acct("2222222222", "Zeta Co"), acct("3333333333", "alpha co", level=2, cur="CAD"),
            acct("4444444444", "Cancelled", status="CANCELED"), acct("5555555555", "A test", test=True),
            acct("2222222222", "Zeta Co duplicate"), acct("bad", "No id")]
    out = g.parse_accounts(rows)
    assert [a["name"] for a in out] == ["alpha co", "Zeta Co"]        # name order, case-blind
    assert out[0]["currency"] == "CAD" and out[0]["level"] == 2 and out[1]["tz"] == "America/New_York"


def day_row(cid, name, d, impr, clk, micros, conv, val, budget=50_000_000, status="ENABLED", typ="SEARCH"):
    return {"campaign": {"id": cid, "name": name, "status": status, "advertisingChannelType": typ},
            "campaignBudget": {"amountMicros": str(budget)}, "segments": {"date": d},
            "metrics": {"impressions": str(impr), "clicks": str(clk), "costMicros": str(micros),
                        "conversions": conv, "conversionsValue": val}}


def test_a_range_becomes_compact_rows_and_keeps_quiet_days():
    rows = [day_row("1", "Brand", "2026-10-01", 100, 10, 12_340_000, 2.5, 300.0),
            day_row("1", "Brand", "2026-10-03", 50, 5, 6_000_000, 0, 0),
            day_row("2", "PMax", "2026-10-03", 70, 7, 1_500_000, 1.0, 80.5, typ="PERFORMANCE_MAX", status="PAUSED"),
            day_row("3", "Stray", "2026-11-09", 1, 1, 1, 0, 0)]          # outside the range: dropped
    out = g.parse_campaign_days(rows, "2026-10-01", "2026-10-04")
    assert out["days"] == ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]    # 10-02 and 10-04 had no rows
    assert [c["name"] for c in out["campaigns"]] == ["Brand", "PMax"]
    assert out["campaigns"][0]["budget"] == 50.0 and out["campaigns"][1]["type"] == "PERFORMANCE_MAX"
    assert out["rows"][0] == [0, 0, 100, 10, 12.34, 2.5, 300.0]        # micros become currency units
    assert out["rows"][2] == [1, 2, 70, 7, 1.5, 1.0, 80.5] and len(out["rows"]) == 3


def test_search_impression_share_is_kept_only_where_google_gave_one():
    camp = [{"campaign": {"id": "1"}, "metrics": {"searchImpressionShare": 0.43211}},
            {"campaign": {"id": "2"}, "metrics": {}}]
    assert g.parse_sis(camp, [{"metrics": {"searchImpressionShare": 0.3}}]) == {"account": 0.3, "campaigns": {"1": 0.4321}}
    assert g.parse_sis([], []) == {"account": None, "campaigns": {}}


def test_device_network_and_hour_rows_are_summed_by_segment():
    m = lambda i, c, mi, cv, v: {"impressions": i, "clicks": c, "costMicros": mi, "conversions": cv, "conversionsValue": v}
    dev = [{"segments": {"device": "MOBILE"}, "metrics": m(10, 2, 1_000_000, 1, 5)},
           {"segments": {"device": "MOBILE"}, "metrics": m(5, 1, 500_000, 0, 0)},
           {"segments": {"device": "DESKTOP"}, "metrics": m(1, 1, 100_000, 0, 0)}]
    heat = [{"segments": {"dayOfWeek": "MONDAY", "hour": 9}, "metrics": m(10, 2, 2_000_000, 1, 5)},
            {"segments": {"dayOfWeek": "SUNDAY", "hour": 0}, "metrics": m(1, 0, 0, 0, 0)},
            {"segments": {"dayOfWeek": "NOTADAY", "hour": 3}, "metrics": m(9, 9, 9, 9, 9)}]       # unknown weekday: dropped
    out = g.parse_extras(dev, [], heat)
    assert out["device"] == [["DESKTOP", 1, 1, 0.1, 0.0, 0.0], ["MOBILE", 15, 3, 1.5, 1.0, 5.0]]
    assert out["network"] == []
    assert out["dowHour"] == [[0, 9, 10, 2, 2.0, 1.0, 5.0], [6, 0, 1, 0, 0.0, 0.0, 0.0]]       # Monday is 0, Sunday 6


# ── Ranges ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("a,b,says", [
    ("2026-10-01", "2026-09-01", "starts after"), ("2026-02-30", "2026-03-01", "does not exist"),
    ("10/01/2026", "2026-10-02", "YYYY-MM-DD"), ("2024-01-01", "2026-10-02", "longer than"),
    ("2026-10-01", (date.today() + timedelta(days=5)).isoformat(), "future"), (None, "2026-10-02", "YYYY-MM-DD")])
def test_a_bad_range_is_refused_with_the_reason(a, b, says):
    with pytest.raises(ValueError, match=says):
        g.check_range(a, b, "the range")


def test_a_good_range_passes_and_is_cached_longer_once_it_has_ended():
    y = (date.today() - timedelta(days=1)).isoformat()
    assert g.check_range("2026-10-01", y) == ("2026-10-01", y)
    assert g._ttl(y) == g.TTL_OPEN and g._ttl(date.today().isoformat()) == g.TTL_OPEN
    assert g._ttl((date.today() - timedelta(days=9)).isoformat()) == g.TTL_CLOSED


# ── Calls: the request budget, retries, errors, pages ──────────────────────

def test_requests_stop_at_the_days_budget_and_say_when_it_resets():
    s = FakeStore(calls=g.DAILY_LIMIT)
    with pytest.raises(g.OverQuota, match="resets at midnight Pacific"):
        g._post("/x", {}, s, http=lambda *a, **k: pytest.fail("a request went out over the budget"))
    s2 = FakeStore(calls=g.DAILY_LIMIT - 1)
    assert g._post("/x", {}, s2, http=lambda *a, **k: Resp(200, {"ok": 1})) == {"ok": 1} and s2.calls == g.DAILY_LIMIT


def test_a_broken_counter_does_not_stop_reads():
    class Down(FakeStore):
        def bump_gads_calls(self, day):
            raise RuntimeError("db down")
    assert g._post("/x", {}, Down(), http=lambda *a, **k: Resp(200, {"ok": 1})) == {"ok": 1}


def test_a_rate_limit_is_retried_once_then_reported(monkeypatch):
    monkeypatch.setattr(g.time, "sleep", lambda s: None)
    seq = iter([Resp(429), Resp(200, {"ok": 1})])
    s = FakeStore()
    assert g._post("/x", {}, s, http=lambda *a, **k: next(seq)) == {"ok": 1} and s.calls == 2     # both tries are counted
    with pytest.raises(g.OverQuota, match="rate limiting"):
        g._post("/x", {}, FakeStore(), http=lambda *a, **k: Resp(429))


def test_google_s_own_words_reach_the_page():
    body = {"error": {"message": "generic", "details": [{"errors": [{"message": "The caller does not have permission"}]}]}}
    with pytest.raises(g.AdsError, match="403: The caller does not have permission"):
        g._post("/x", {}, FakeStore(), http=lambda *a, **k: Resp(403, body))


def test_an_expired_token_is_refreshed_and_the_call_retried():
    seen = []

    def http(url, headers=None, **k):
        seen.append(headers["Authorization"])
        return Resp(401) if len(seen) == 1 else Resp(200, {"ok": 1})
    posted = []

    def token_post(url, data=None, timeout=None):
        posted.append(data["grant_type"])
        return Resp(200, {"access_token": "fresh", "expires_in": 3600})
    real = g._access_token
    g._access_token = lambda post=None: real(token_post)
    try:
        assert g._post("/x", {}, FakeStore(), http=http) == {"ok": 1}
    finally:
        g._access_token = real
    assert seen == ["Bearer tok", "Bearer fresh"] and posted == ["refresh_token"]


def test_a_rejected_sign_in_says_what_to_do():
    g.reset_token_cache()
    with pytest.raises(g.AdsError, match="GOOGLE_ADS_REFRESH_TOKEN"):
        g._access_token(lambda *a, **k: Resp(400, {"error": "invalid_grant"}))


def test_every_page_of_a_long_result_is_read_and_no_page_size_is_sent():
    pages = iter([{"results": [{"a": 1}], "nextPageToken": "p2"}, {"results": [{"a": 2}]}])
    bodies = []

    def http(url, headers=None, data=None, **k):
        bodies.append(json.loads(data))
        return Resp(200, next(pages))
    assert g.search("1234567890", "SELECT x", FakeStore(), http=http) == [{"a": 1}, {"a": 2}]
    assert bodies == [{"query": "SELECT x"}, {"query": "SELECT x", "pageToken": "p2"}]      # v24 refuses pageSize


def test_only_read_endpoints_are_ever_called():
    src = open(os.path.join(ROOT, "tracker", "paid_gads.py")).read()
    paths = set(re.findall(r'_post\(\s*"([^"]+)"|_post\(\s*\n?\s*"([^"]+)"', src))
    assert re.findall(r'"/customers/%s/googleAds:search"', src) and "mutate" not in src.lower().replace("immutable", "")
    assert not re.search(r"googleAds:(mutate|upload)|:mutate", src)
    del paths


# ── Cache ──────────────────────────────────────────────────────────────────

def test_a_repeat_view_costs_no_request():
    s, calls = FakeStore(), []

    def http(url, headers=None, data=None, **k):
        calls.append(json.loads(data)["query"][:30])
        q = json.loads(data)["query"]
        if "FROM customer WHERE" in q and "search_impression" in q:
            return Resp(200, {"results": []})
        return Resp(200, {"results": [day_row("1", "Brand", "2026-10-01", 5, 1, 1_000_000, 0, 0)]})
    y = (date.today() - timedelta(days=1)).isoformat()
    first = g.performance(s, "1234567890", "2026-10-01", y, http=http)
    n = len(calls)
    again = g.performance(s, "1234567890", "2026-10-01", y, http=http)
    assert n == 3 and len(calls) == 3 and again == first          # days + two share queries, then nothing
    assert first["sis"] == {"account": None, "campaigns": {}} and first["rows"][0][4] == 1.0


def test_impression_share_failing_does_not_lose_the_range():
    def http(url, headers=None, data=None, **k):
        q = json.loads(data)["query"]
        if "search_impression_share" in q:
            return Resp(400, {"error": {"message": "nope"}})
        return Resp(200, {"results": [day_row("1", "Brand", "2026-10-01", 5, 1, 1_000_000, 0, 0)]})
    out = g.performance(FakeStore(), "1234567890", "2026-10-01", "2026-10-02", http=http)
    assert out["sis"] == {"account": None, "campaigns": {}} and len(out["rows"]) == 1


def test_a_cache_outage_falls_back_to_asking_google():
    class NoCache(FakeStore):
        def get_gads_cache(self, key, ttl):
            raise RuntimeError("db down")

        def put_gads_cache(self, key, value):
            raise RuntimeError("db down")
    out = g.extras(NoCache(), "1234567890", "2026-10-01", "2026-10-02", http=lambda *a, **k: Resp(200, {"results": []}))
    assert out == {"device": [], "network": [], "dowHour": []}


def test_the_main_range_and_its_comparison_are_fetched_together():
    s = FakeStore()
    http = lambda url, headers=None, data=None, **k: Resp(200, {"results": []})
    cur, cmp = g.performance_pair(s, "1234567890", ("2026-10-01", "2026-10-07"), ("2026-09-24", "2026-09-30"), http=http)
    assert cur["start"] == "2026-10-01" and "sis" in cur and cmp["start"] == "2026-09-24" and "sis" not in cmp
    assert g.performance_pair(s, "1234567890", ("2026-10-01", "2026-10-07"), None, http=http)[1] is None


# ── Routes ─────────────────────────────────────────────────────────────────

def client_as(email):
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


ACCTS = [{"id": "1234567890", "name": "Acme", "currency": "USD", "tz": "", "level": 1}]
BASE = "/p2/paid-marketing/api/gads/"


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(g, "list_accounts", lambda st, **k: ACCTS)
    monkeypatch.setattr(store, "gads_calls", lambda day: 41)
    calls = []

    def pair(st, cid, cur, cmp, with_sis=True, **k):
        calls.append((cid, cur, cmp, with_sis))
        return {"start": cur[0], "end": cur[1], "days": [], "campaigns": [], "rows": []}, ({"start": cmp[0]} if cmp else None)
    monkeypatch.setattr(g, "performance_pair", pair)
    monkeypatch.setattr(g, "extras", lambda st, cid, a, b, **k: {"device": [], "network": [], "dowHour": []})
    return calls


@pytest.mark.parametrize("path", ["accounts", "performance", "extras"])
def test_the_data_routes_are_for_signed_in_position2_staff_only(wired, path):
    assert client_as(None).get(BASE + path).status_code == 302
    assert client_as("someone@gmail.com").get(BASE + path).status_code == 302


def test_without_a_connection_the_routes_say_what_is_missing(monkeypatch):
    monkeypatch.delenv("GOOGLE_ADS_REFRESH_TOKEN")
    r = client_as(STAFF).get(BASE + "accounts")
    assert r.status_code == 409 and "GOOGLE_ADS_REFRESH_TOKEN" in r.get_json()["error"]


def test_only_admins_see_the_request_tally(wired):
    assert client_as(STAFF).get(BASE + "accounts").get_json()["usage"] is None
    assert client_as(ADMIN).get(BASE + "accounts").get_json()["usage"] == {"calls": 41, "limit": g.DAILY_LIMIT}


def test_performance_checks_the_account_and_both_ranges(wired):
    c = client_as(STAFF)
    q = "account=1234567890&start=2026-10-01&end=2026-10-07"
    assert c.get(BASE + "performance?account=12&start=2026-10-01&end=2026-10-07").status_code == 400
    r = c.get(BASE + "performance?account=9999999999&start=2026-10-01&end=2026-10-07")
    assert r.status_code == 400 and "not under Position2" in r.get_json()["error"]          # not a guessable id
    assert c.get(BASE + "performance?account=1234567890&start=2026-10-09&end=2026-10-07").status_code == 400
    assert c.get(BASE + "performance?" + q + "&compare_start=2026-09-24").status_code == 400
    assert not wired
    r = c.get(BASE + "performance?" + q + "&compare_start=2026-09-24&compare_end=2026-09-30&sis=0")
    j = r.get_json()
    assert r.status_code == 200 and j["account"]["name"] == "Acme" and j["compare"]["start"] == "2026-09-24"
    assert wired == [("1234567890", ("2026-10-01", "2026-10-07"), ("2026-09-24", "2026-09-30"), False)]
    assert c.get(BASE + "performance?" + q).get_json()["compare"] is None


def test_google_failures_become_answers(wired, monkeypatch):
    c = client_as(STAFF)
    url = BASE + "extras?account=1234567890&start=2026-10-01&end=2026-10-07"
    assert c.get(url).status_code == 200
    for exc, code in ((g.OverQuota("budget used"), 429), (g.AdsError("Google said no"), 502)):
        def boom(*a, **k):
            raise exc
        monkeypatch.setattr(g, "extras", boom)
        r = c.get(url)
        assert r.status_code == code and r.get_json()["error"] == str(exc)


def test_the_page_renders_for_staff_and_not_for_anyone_else():
    path = "/p2/paid-marketing/performance-summarizer"
    assert client_as(STAFF).get(path).status_code == 200
    assert client_as(None).get(path).status_code == 302
    assert client_as("someone@gmail.com").get(path).status_code == 302


# ── The page script and its template ───────────────────────────────────────

def _read(*p):
    return open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_the_script_only_reaches_for_elements_the_template_has():
    js, html = _read("static", "js", "performance_summarizer.js"), _read("templates", "performance_summarizer.html")
    made_by_script = {"spDevice", "spNetwork", "spType", "cq", "moreRows", "clearFilter", "psToast", "mf"}
    used = set(re.findall(r'\$\("#([A-Za-z]+)"\)', js))
    have = set(re.findall(r'id="([A-Za-z]+)"', html))
    assert not (used - have - made_by_script), sorted(used - have - made_by_script)
    for name in ("pm_picker.js", "ps_data.js", "performance_summarizer.js", "chart.umd.js", "performance_summarizer.css"):
        assert name in html
        assert os.path.exists(os.path.join(ROOT, "static", "vendor" if name == "chart.umd.js" else
                                           "css" if name.endswith(".css") else "js", name)), name


def test_the_script_reads_only_the_routes_that_exist():
    js = _read("static", "js", "performance_summarizer.js")
    for route in set(re.findall(r'BASE\+"/([a-z]+)', js)):
        assert BASE + route in _read("app.py").replace('"/p2/paid-marketing/api/gads/', '"' + BASE) or \
            ('/p2/paid-marketing/api/gads/%s"' % route) in _read("app.py"), route


def test_the_agent_is_listed_wherever_the_others_are():
    section = _read("templates", "paid_marketing.html")
    assert "performance-summarizer" in section and 'class="dash-card active c-perf"' in section
    assert "performance-summarizer" in _read("templates", "_paid_kpal.html")
    assert "performance-summarizer" in section.split("var BASE=[", 1)[1]          # the section page carries its own copy of the palette
    assert "performance-summarizer" in _read("static", "js", "paid_marketing.js")
    assert "Performance Summarizer" in _read("app.py")


def test_no_em_dash_in_what_this_agent_shows():
    for p in (("static", "js", "performance_summarizer.js"), ("static", "js", "ps_data.js"),
              ("templates", "performance_summarizer.html"), ("static", "css", "performance_summarizer.css")):
        assert "—" not in _read(*p), p
