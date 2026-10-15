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
from tests.gads_fakes import FakeStore  # noqa: E402

STAFF = "someone@position2.com"
ADMIN = "reporting@position2.com"


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
    g.reset_state()
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
    pad = [0.0] * (g.NB - 5)                                           # the optional metrics Google did not send
    assert out["rows"][0] == [0, 0, 100, 10, 12.34, 2.5, 300.0] + pad       # micros become currency units
    assert out["rows"][2] == [1, 2, 70, 7, 1.5, 1.0, 80.5] + pad and len(out["rows"]) == 3
    assert out["missing"] == []                                        # none were dropped


def test_search_impression_share_is_kept_only_where_google_gave_one():
    camp = [{"campaign": {"id": "1"}, "metrics": {"searchImpressionShare": 0.43211}},
            {"campaign": {"id": "2"}, "metrics": {}}]
    assert g.parse_sis(camp) == {"1": 0.4321} and g.parse_sis([]) == {}
    acct = [{"metrics": {"searchImpressionShare": 0.3, "searchBudgetLostImpressionShare": "0.0999"}}]
    got = g.parse_shares(acct, ["search_impression_share", "search_budget_lost_impression_share", "content_impression_share"])
    assert got == {"search_impression_share": 0.3, "search_budget_lost_impression_share": 0.0999, "content_impression_share": None}


def test_device_network_and_hour_rows_are_summed_by_segment():
    m = lambda i, c, mi, cv, v: {"impressions": i, "clicks": c, "costMicros": mi, "conversions": cv, "conversionsValue": v}
    dev = [{"segments": {"device": "MOBILE"}, "metrics": m(10, 2, 1_000_000, 1, 5)},
           {"segments": {"device": "MOBILE"}, "metrics": m(5, 1, 500_000, 0, 0)},
           {"segments": {"device": "DESKTOP"}, "metrics": m(1, 1, 100_000, 0, 0)}]
    heat = [{"segments": {"dayOfWeek": "MONDAY", "hour": 9}, "metrics": m(10, 2, 2_000_000, 1, 5)},
            {"segments": {"dayOfWeek": "SUNDAY", "hour": 0}, "metrics": m(1, 0, 0, 0, 0)},
            {"segments": {"dayOfWeek": "NOTADAY", "hour": 3}, "metrics": m(9, 9, 9, 9, 9)}]       # unknown weekday: dropped
    out = g.parse_extras(dev, [], heat)
    pad = [0.0] * (g.NB - 5)
    assert out["device"] == [["DESKTOP", 1, 1, 0.1, 0.0, 0.0] + pad, ["MOBILE", 15, 3, 1.5, 1.0, 5.0] + pad]
    assert out["network"] == []
    assert out["dowHour"] == [[0, 9, 10, 2, 2.0, 1.0, 5.0] + pad, [6, 0, 1, 0, 0.0, 0.0, 0.0] + pad]       # Monday is 0, Sunday 6


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
    assert out == {"device": [], "network": [], "dowHour": [], "missing": []}


def test_the_main_range_and_its_comparison_are_fetched_together():
    s = FakeStore()
    http = lambda url, headers=None, data=None, **k: Resp(200, {"results": []})
    cur, cmp, saved = g.performance_pair(s, "1234567890", ("2026-10-01", "2026-10-07"), ("2026-09-24", "2026-09-30"), http=http)
    assert cur["start"] == "2026-10-01" and "sis" in cur and "shares" in cur and cmp["start"] == "2026-09-24"
    assert "sis" not in cmp and "shares" in cmp and saved == []                 # the comparison needs the shares, not the campaign ones
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
        return {"start": cur[0], "end": cur[1], "days": [], "campaigns": [], "rows": []}, ({"start": cmp[0]} if cmp else None), []
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
    assert j["stale"] is None
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
    assert "Performance Summarizer" in _read("app.py")


def test_no_em_dash_in_what_this_agent_shows():
    for p in (("static", "js", "performance_summarizer.js"), ("static", "js", "ps_data.js"),
              ("templates", "performance_summarizer.html"), ("static", "css", "performance_summarizer.css")):
        assert "—" not in _read(*p), p


def test_every_chart_kind_has_a_picture_and_the_studio_is_on_the_page():
    js = _read("static", "js", "performance_summarizer.js")
    kinds = re.findall(r'\{k:"([a-z]+)",l:"[^"]+",hint:', _read("static", "js", "ps_data.js"))
    assert len(kinds) == 11 and {"line", "area", "bar", "hbar", "combo", "stacked", "stackedarea", "pie", "donut", "radar", "scatter"} == set(kinds)
    icons = js.split("var TYPE_ICON={", 1)[1].split("};", 1)[0]
    assert all(re.search(r"\b%s:'<" % k, icons) for k in kinds), [k for k in kinds if not re.search(r"\b%s:'<" % k, icons)]
    html = _read("templates", "performance_summarizer.html")
    assert 'id="studio"' in html and 'id="studioSec"' in html and 'id="studioBtn"' in html
    assert html.index('id="splits"') < html.index('id="camps"') < html.index('id="studio"') < html.index('id="board"')   # the studio and what it keeps are last


def test_a_saved_or_linked_chart_can_only_use_kinds_and_groupings_that_exist():
    js = _read("static", "js", "performance_summarizer.js")
    assert "P.TYPE[s.type]" in js and "P.DIM[s.dim]" in js and 'P.TYPE[h.t]' in js and 'P.DIM[h.g]' in js


# ── Quota, saved copies and metrics Google refuses ─────────────────────────

def quota_body(delay):
    return {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [{"errors": [{
        "message": "Too many requests. Retry in %s seconds." % delay,
        "details": {"quotaErrorDetails": {"rateName": "Number of operations for explorer access", "retryDelay": "%ss" % delay}}}]}]}}


def test_googles_daily_allowance_is_remembered_and_no_request_is_sent_until_it_returns(monkeypatch):
    s, sent = FakeStore(), []

    def http(*a, **k):
        sent.append(1)
        return Resp(429, quota_body(61458))
    with pytest.raises(g.OverQuota, match="about 17 hours") as e:
        g._post("/x", {}, s, http=http)
    assert len(sent) == 1 and e.value.until and s.block == e.value.until        # remembered in memory and in the database
    with pytest.raises(g.OverQuota, match="about 17 hours"):
        g._post("/x", {}, s, http=http)
    assert len(sent) == 1                                                       # nothing went to Google the second time
    g.reset_state()                                                             # another worker that never saw the 429 ...
    s.block = e.value.until
    with pytest.raises(g.OverQuota):
        g._post("/x", {}, s, http=http)
    assert len(sent) == 1                                                       # ... learns it from the database
    s.block = 0.0
    g.reset_state()
    seq = iter([Resp(200, {"ok": 1})])
    assert g._post("/x", {}, s, http=lambda *a, **k: next(seq)) == {"ok": 1}     # and asks again once it has passed


def test_a_short_retry_delay_is_waited_out_not_treated_as_the_daily_allowance(monkeypatch):
    slept = []
    monkeypatch.setattr(g.time, "sleep", lambda x: slept.append(x))
    seq = iter([Resp(429, quota_body(5)), Resp(200, {"ok": 1})])
    s = FakeStore()
    assert g._post("/x", {}, s, http=lambda *a, **k: next(seq)) == {"ok": 1}
    assert slept == [5.0] and not s.block


def ok_rows(q):
    return Resp(200, {"results": [day_row("1", "Brand", "2026-10-01", 5, 1, 1_000_000, 0, 0)]})


def test_a_metric_google_refuses_is_dropped_and_remembered_and_the_page_is_told():
    queries = []

    def http(url, headers=None, data=None, **k):
        q = json.loads(data)["query"]
        queries.append(q)
        if "metrics.video_trueview_views" in q:
            return Resp(400, {"error": {"details": [{"errors": [{"message": "Unrecognized field in the query: 'metrics.video_trueview_views'."}]}]}})
        return ok_rows(q)
    out = g.performance(FakeStore(), "1234567890", "2026-10-01", "2026-10-02", with_sis=False, with_shares=False, http=http)
    assert len(out["rows"]) == 1 and out["missing"] == [g.NB - 12 + [o[0] for o in g.OPTIONAL].index("video_trueview_views")]
    assert "video_trueview_views" in g._BAD and "metrics.all_conversions," in queries[-1] and "video_trueview_views" not in queries[-1]
    n = len(queries)
    g.performance(FakeStore(), "1234567890", "2026-10-03", "2026-10-04", with_sis=False, with_shares=False, http=http)
    assert len(queries) == n + 1 and "video_trueview_views" not in queries[-1]          # the next range does not ask for it again


def test_a_refusal_naming_one_metric_does_not_take_out_its_shorter_cousin():
    def http(url, headers=None, data=None, **k):
        q = json.loads(data)["query"]
        if re.search(r"metrics\.all_conversions_value(?![a-z_])", q):
            return Resp(400, {"error": {"details": [{"errors": [{"message": "metrics.all_conversions_value cannot be selected with segments.date"}]}]}})
        return ok_rows(q)
    out = g.performance(FakeStore(), "1234567890", "2026-10-01", "2026-10-02", with_sis=False, with_shares=False, http=http)
    assert g._BAD == {"all_conversions_value"} and len(out["rows"]) == 1             # all_conversions is a different metric and stays


def test_a_400_that_names_no_metric_falls_back_to_the_core_five_and_blames_nothing():
    seen = []

    def http(url, headers=None, data=None, **k):
        q = json.loads(data)["query"]
        seen.append(q)
        if "metrics.all_conversions" in q:
            return Resp(400, {"error": {"message": "something else is wrong with this request"}})
        return ok_rows(q)
    out = g.performance(FakeStore(), "1234567890", "2026-10-01", "2026-10-02", with_sis=False, with_shares=False, http=http)
    assert len(out["rows"]) == 1 and not g._BAD                                 # the cause might be the range, so nothing is remembered
    assert len(out["missing"]) == g.NB - 5                                       # but this answer says every optional metric is missing


def test_a_400_on_the_core_query_is_still_an_error():
    with pytest.raises(g.AdsError, match="400"):
        g.performance(FakeStore(), "1234567890", "2026-10-01", "2026-10-02", with_sis=False, with_shares=False,
                      http=lambda *a, **k: Resp(400, {"error": {"message": "bad"}}))


def test_when_google_cannot_answer_the_last_saved_copy_stands_in_and_says_when_it_was_saved():
    s = FakeStore()
    key = g.CACHE_V + "days:1234567890:2026-10-01:2026-10-02"
    s.old[key] = {"start": "2026-10-01", "days": ["2026-10-01"], "rows": [], "campaigns": [], "missing": []}
    notes = []
    out = g._cached(s, key, 60, lambda: (_ for _ in ()).throw(g.OverQuota("spent")), notes)
    assert out["start"] == "2026-10-01" and notes == ["2026-10-10T08:00:00+00:00"]
    with pytest.raises(g.OverQuota):
        g._cached(s, g.CACHE_V + "days:other", 60, lambda: (_ for _ in ()).throw(g.OverQuota("spent")), [])     # nothing saved: say so
    with pytest.raises(g.AdsError, match="bad"):
        g._cached(s, key, 60, lambda: (_ for _ in ()).throw(g.AdsError("bad", status=400)), [])                 # a refusal of the request is not hidden


def test_ranges_saved_before_the_shape_changed_still_beat_showing_nothing():
    s = FakeStore()
    s.old["days:1234567890:2026-10-01:2026-10-02"] = {"start": "legacy", "rows": [[0, 0, 5, 1, 2.0, 0, 0]]}
    s.old["sis:1234567890:2026-10-01:2026-10-02"] = {"account": 0.3, "campaigns": {}}
    boom = lambda: (_ for _ in ()).throw(g.OverQuota("spent"))
    assert g._cached(s, g.CACHE_V + "days:1234567890:2026-10-01:2026-10-02", 60, boom, [])["start"] == "legacy"
    with pytest.raises(g.OverQuota):                      # the old share shape is not reused: it would be read as the wrong thing
        g._cached(s, g.CACHE_V + "sis:1234567890:2026-10-01:2026-10-02", 60, boom, [])


def test_the_shares_failing_never_loses_the_range_even_with_no_saved_copy():
    def http(url, headers=None, data=None, **k):
        q = json.loads(data)["query"]
        if "FROM campaign WHERE" in q and "segments.date," in q.split("FROM")[0]:
            return ok_rows(q)
        return Resp(429, quota_body(5))              # shares and impression share: rate limited, twice
    import tracker.paid_gads as mod
    orig = mod.time.sleep
    mod.time.sleep = lambda x: None
    try:
        out = g.performance(FakeStore(), "1234567890", "2026-10-01", "2026-10-02", http=http)
    finally:
        mod.time.sleep = orig
    assert len(out["rows"]) == 1 and out["sis"] == {"account": None, "campaigns": {}}
    assert set(out["shares"]) == set(g.SHARES) and all(v is None for v in out["shares"].values())


def test_a_share_google_refuses_is_dropped_the_rest_still_arrive():
    def http(url, headers=None, data=None, **k):
        q = json.loads(data)["query"]
        if "metrics.top_impression_percentage" in q:
            return Resp(400, {"error": {"details": [{"errors": [{"message": "Cannot select top_impression_percentage here"}]}]}})
        return Resp(200, {"results": [{"metrics": {"searchImpressionShare": 0.4, "contentImpressionShare": 0.0999}}]})
    out = g.performance(FakeStore(), "1234567890", "2026-10-01", "2026-10-02", with_sis=False, http=http, notes=[])
    assert out["shares"]["search_impression_share"] == 0.4 and out["shares"]["content_impression_share"] == 0.0999
    assert out["shares"]["top_impression_percentage"] is None and g._BAD_SHARES == {"top_impression_percentage"}


def test_the_routes_say_how_old_a_saved_copy_is_and_when_google_returns(monkeypatch):
    monkeypatch.setattr(g, "list_accounts", lambda st, **k: ACCTS)
    monkeypatch.setattr(store, "gads_calls", lambda day: 0)
    until = 1_900_000_000.0
    monkeypatch.setattr(g, "blocked_until", lambda st=None: until)
    monkeypatch.setattr(g, "performance_pair", lambda *a, **k: ({"days": [], "campaigns": [], "rows": []}, None, ["2026-10-09T10:00:00+00:00", "2026-10-10T10:00:00+00:00"]))
    c = client_as(STAFF)
    j = c.get(BASE + "performance?account=1234567890&start=2026-10-01&end=2026-10-07").get_json()
    assert j["stale"] == "2026-10-09T10:00:00+00:00" and j["blockedUntil"] == until          # the oldest copy is the one to admit to
    assert c.get(BASE + "accounts").get_json()["blockedUntil"] == until
    monkeypatch.setattr(g, "performance_pair", lambda *a, **k: (_ for _ in ()).throw(g.OverQuota("spent", until=until)))
    r = c.get(BASE + "performance?account=1234567890&start=2026-10-01&end=2026-10-07")
    assert r.status_code == 429 and r.get_json()["blockedUntil"] == until


def test_the_server_and_the_page_agree_on_the_columns_and_the_shares():
    import subprocess
    js = os.path.join(ROOT, "static", "js", "ps_data.js")
    names = {"IMPR": "impressions", "CLK": "clicks", "COST": "cost_micros", "CONV": "conversions", "VAL": "conversions_value",
             "ALLC": "all_conversions", "ALLV": "all_conversions_value", "VTC": "view_through_conversions", "INT": "interactions",
             "ENG": "engagements", "VID": "video_trueview_views", "XDV": "cross_device_conversions", "INV": "invalid_clicks",
             "PCALL": "phone_calls", "PIMPR": "phone_impressions", "AVV": "active_view_impressions",
             "AVM": "active_view_measurable_impressions"}
    code = "const P=require(%s);console.log(JSON.stringify({nb:P.NB,idx:%s,shares:P.METRIC_ALL.filter(k=>P.METRICS[k].period).map(k=>P.METRICS[k].share)}))" % (
        json.dumps(js), "{" + ",".join("%s:P.%s" % (k, k) for k in names) + "}")
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    if r.returncode:
        pytest.skip("node is not installed")
    got = json.loads(r.stdout)
    assert got["nb"] == g.NB
    assert {g.BASE[i][0]: k for k, i in got["idx"].items()} == {v: k for k, v in names.items()}
    from tracker import paid_meta as _mt
    assert sorted(got["shares"]) == sorted(g.SHARES + _mt.SHARE_KEYS)           # every share the page can show is one a server reads (Google's, and Meta's reach and frequency), and back
