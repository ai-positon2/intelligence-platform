"""Paid Marketing Agents: Call Transcript Analyzer, the CallRail reads and the routes.

What is pinned here: the key goes only in an Authorization header (never a URL, a log or a response); every request is a GET; a
refused key, a missing permission, a rate limit and a hiccup each say what to do; the app stops at 90% of CallRail's hourly or
daily allowance; the overview asks for the right groupings and filters, picks the chosen source's own figures and is read from
the saved copy the second time; the sample is spread across the range, exact on source, and keeps no caller name, number or call
text; and the routes (the page, the lists, both views, their errors and the Refresh cooldown).
"""
import datetime as dt
import json
import os
import sys
import time

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as appmod  # noqa: E402
from tests.gads_fakes import FakeStore  # noqa: E402
from tracker import paid_calls as C  # noqa: E402
from tracker import paid_gads as g  # noqa: E402
from tracker import paid_store  # noqa: E402

KEY = "ctrk_SECRETSECRETSECRET0123456789"
ACC = "ACC" + "a" * 32
ACC2 = "ACC" + "b" * 32
COM = "COM" + "c" * 32
COM2 = "COM" + "d" * 32
TODAY = dt.date.today()
START, END = (TODAY - dt.timedelta(days=30)).isoformat(), (TODAY - dt.timedelta(days=1)).isoformat()


class Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code, self._b, self.headers = status, body if body is not None else {}, headers or {}

    def json(self):
        if isinstance(self._b, Exception):
            raise self._b
        return self._b


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("CALLRAIL_API_KEY", KEY)
    g.forget()
    C._RL.update(hourly=None, daily=None, hourlyAllowed=1000, dailyAllowed=10000, at=0.0)


def row(key, calls=100, missed=5, leads=10, **k):
    return dict({"key": key, "total_calls": calls, "answered_calls": calls - missed, "missed_calls": missed, "abandoned_calls": 1,
                 "first_time_callers": calls // 3, "average_duration": 120.0, "leads": leads}, **k)


def total(calls, missed, leads):
    return {"total_calls": calls, "answered_calls": calls - missed, "missed_calls": missed, "abandoned_calls": 2, "first_time_callers": 30,
            "average_duration": 100.0, "leads": leads}


class CallRail:
    """A CallRail that answers every GET and remembers it."""

    def __init__(self, summary=None, calls=None, accounts=None, companies=None, fail=None):
        self.log, self.summary, self.calls, self.fail = [], summary or {}, calls or {}, fail
        self.accounts = accounts if accounts is not None else [{"id": ACC, "name": "42 North Dental", "hipaa_account": True, "inbound_recording_enabled": False},
                                                              {"id": ACC2, "name": "OIA", "hipaa_account": True}, {"id": "junk", "name": "bad id"}]
        self.companies = companies if companies is not None else [{"id": COM, "name": "Great Hill Braintree", "status": "active"},
                                                                  {"id": COM2, "name": "Old Office", "status": "disabled"}, {"id": "nope", "name": "x"}]

    def __call__(self, url, headers=None, params=None, timeout=None, **k):
        self.log.append((url, dict(headers or {}), dict(params or {})))
        if self.fail:
            return self.fail
        h = {"x-rate-limit-hourly-used": "12", "x-rate-limit-daily-used": "345", "x-rate-limit-hourly-allowed": "1000", "x-rate-limit-daily-allowed": "10000"}
        path = url.split("/v3/", 1)[1]
        if path == "a.json":
            return Resp(200, {"accounts": self.accounts}, h)
        if path.endswith("/companies.json"):
            return Resp(200, {"companies": self.companies, "total_pages": 1}, h)
        if path.endswith("/calls/summary.json"):
            by = (params or {}).get("group_by")
            if params.get("start_date") != START and by == "source":                      # the previous period
                return Resp(200, {"grouped_results": [row("Google Ads", 80, 8, 6), row("Directories", 200, 10, 12)], "total_results": total(280, 18, 18)}, h)
            rows = self.summary.get(by) or []
            return Resp(200, {"grouped_results": rows, "total_results": self.summary.get("total") or total(sum(r["total_calls"] for r in rows), 0, 0)}, h)
        if path.endswith("/calls.json"):
            page = (params or {}).get("page", 1)
            pages = self.calls.get("pages", 1)
            return Resp(200, {"page": page, "total_pages": pages, "total_records": self.calls.get("records", 0),
                              "calls": self.calls.get("by_page", {}).get(page, self.calls.get("calls", []))}, h)
        return Resp(404, {"error": "not found"}, h)

    def paths(self):
        return [u.split("/v3/", 1)[1] for u, _h, _p in self.log]


SUMMARY = {
    "source": [row("Google Ads", 100, 5, 20), row("Directories", 300, 30, 15), row("", 4, 0, 0), row("Google Ads Maps", 50, 2, 9)],
    "company": [row("Great Hill Braintree", 60, 3, 8), row("Old Office", 40, 2, 4)],
    "campaign": [row("performance-max-braintree", 30), row("17603057689", 20), row("", 9), row("brand", 12)],
    "keywords": [row("dentist near me", 15), row("", 70), row("[gentle dental]", 9)],
    "total": total(454, 37, 44),
}


def raw_call(i, source="Google Ads", transcript="Agent: Hello. Caller: I am a new patient, can I book?", summary="A new patient called to book.", **k):
    c = {"id": "CAL%032d" % i, "start_time": "2026-10-1%dT09:00:00.000-04:00" % (i % 9), "company_name": "Great Hill Braintree", "source": source, "campaign": "brand",
         "duration": 100, "answered": True, "call_summary": summary, "transcription": transcript, "tags": [{"id": 1, "name": "Booked Appointment"}],
         "sentiment": "Neutral", "lead_score": "72", "timeline_url": "https://app.callrail.com/analytics/a/1/calls/%d" % i,
         "customer_name": "Heather Wright", "customer_phone_number": "+18573109334", "customer_city": "Boston"}
    c.update(k)
    return c


# ── One request ────────────────────────────────────────────────────────────

def test_the_key_goes_in_the_authorization_header_and_nowhere_else():
    cr = CallRail()
    C._get("a.json", {"page": 1, "company_id": "", "search": None}, http=cr)
    url, headers, params = cr.log[0]
    assert headers == {"Authorization": 'Token token="%s"' % KEY}
    assert KEY not in url and KEY not in json.dumps(params)
    assert params == {"page": 1}                                    # empty and None parameters are not sent


def test_nothing_is_asked_without_a_key():
    os.environ.pop("CALLRAIL_API_KEY")
    assert "CALLRAIL_API_KEY" in C.config_problem()
    with pytest.raises(g.NotConfigured):
        C._get("a.json", http=CallRail())
    assert C.key() == ""


@pytest.mark.parametrize("status,exc,text", [
    (401, g.AdsError, "rejected the API key"), (403, g.AdsError, "cannot read this account"), (429, g.OverQuota, "rate limiting"),
    (404, g.AdsError, "answered 404")])
def test_each_way_a_request_fails_says_what_to_do(status, exc, text):
    with pytest.raises(exc) as e:
        C._get("a.json", http=CallRail(fail=Resp(status, {"error": "no"})))
    assert text in str(e.value) and KEY not in str(e.value)


def test_a_hiccup_is_retried_once_and_a_second_one_is_an_error(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    answers = [Resp(503, {}), Resp(200, {"ok": 1})]
    assert C._get("a.json", http=lambda *a, **k: answers.pop(0)) == {"ok": 1}
    two = [Resp(503, {}), Resp(502, {})]
    with pytest.raises(g.AdsError):
        C._get("a.json", http=lambda *a, **k: two.pop(0))


def test_a_network_failure_never_shows_the_key():
    def boom(*a, **k):
        raise ConnectionError("could not connect with " + KEY)
    with pytest.raises(g.AdsError) as e:
        C._get("a.json", http=boom)
    assert KEY not in str(e.value) and "ConnectionError" in str(e.value)


def test_the_rate_limit_headers_are_read_and_the_app_stops_at_ninety_percent():
    cr = CallRail()
    C._get("a.json", http=cr)
    assert C.limits()["hourly"] == 12 and C.limits()["daily"] == 345 and C.limits()["hourlyAllowed"] == 1000
    C._RL.update(hourly=900, at=time.time())
    with pytest.raises(g.OverQuota) as e:
        C._get("a.json", http=cr)
    assert "1000 requests an hour" in str(e.value) and len(cr.log) == 1                     # not asked
    C._RL.update(hourly=10, daily=9000, at=time.time())
    with pytest.raises(g.OverQuota) as e:
        C._get("a.json", http=cr)
    assert "a day" in str(e.value)
    C._RL.update(hourly=950, daily=10, at=time.time() - 7200)                              # a reading from two hours ago is not trusted
    C._get("a.json", http=cr)


def test_only_get_requests_are_ever_made():
    seen = []
    C._get("a.json", http=lambda url, **k: seen.append(k) or Resp(200, {}))
    import inspect
    src = inspect.getsource(C)
    assert "requests.post" not in src and "requests.put" not in src and "requests.delete" not in src and "requests.get" in src


# ── Accounts, locations, ranges ────────────────────────────────────────────

def test_accounts_are_read_validated_sorted_and_saved():
    st, cr = FakeStore(), CallRail()
    accts = C.list_accounts(st, http=cr)
    assert [a["name"] for a in accts] == ["42 North Dental", "OIA"] and accts[0]["hipaa"] is True            # "junk" is not an account id
    n = len(cr.log)
    C.list_accounts(st, http=cr)
    assert len(cr.log) == n


def test_locations_are_read_with_the_active_ones_first():
    cos = C.list_companies(FakeStore(), ACC, http=CallRail())
    assert [c["name"] for c in cos] == ["Great Hill Braintree", "Old Office"] and cos[0]["active"] and not cos[1]["active"]


def test_an_account_the_key_cannot_read_is_refused():
    st, cr = FakeStore(), CallRail()
    assert C.account_of(st, ACC, http=cr)["name"] == "42 North Dental"
    for bad in ("", "abc", "ACC" + "z" * 32, "ACC" + "e" * 32):
        with pytest.raises(ValueError):
            C.account_of(st, bad, http=cr)


def test_the_range_is_checked():
    assert C.check_range(START, END) == (START, END)
    for a, b in [("x", END), (END, START), ("2026-02-30", END), (START, (TODAY + dt.timedelta(days=2)).isoformat()),
                 ((TODAY - dt.timedelta(days=100)).isoformat(), END), (None, None)]:
        with pytest.raises(ValueError):
            C.check_range(a, b)
    assert C.check_range((TODAY - dt.timedelta(days=91)).isoformat(), TODAY.isoformat())[0]


def test_a_location_must_belong_to_the_account():
    cos = C.parse_companies([{"id": COM, "name": "A", "status": "active"}])
    assert C.check_company(cos, "") == "" and C.check_company(cos, COM) == COM
    for bad in (COM2, "COM" + "z" * 32, "x"):
        with pytest.raises(ValueError):
            C.check_company(cos, bad)


def test_the_previous_range_is_the_same_length_ending_the_day_before():
    assert C.previous_range("2026-10-08", "2026-10-14") == ("2026-10-01", "2026-10-07")
    assert C.previous_range("2026-10-01", "2026-10-01") == ("2026-09-30", "2026-09-30")


def test_a_paid_source_is_one_whose_name_says_so():
    for s in ("Google Ads", "Google Ads Maps", "Bing Ads", "Yelp Ads", "PPC-Yelp", "Paid Social", "google cpc"):
        assert C.is_paid(s), s
    for s in ("Directories", "Google Organic", "Direct", "Yelp", "Google My Business", "Bing Organic", "Unknown", "", None, "Leads"):
        assert not C.is_paid(s), s


# ── The overview ───────────────────────────────────────────────────────────

def test_the_overview_asks_for_every_grouping_and_reads_the_totals():
    st, cr = FakeStore(), CallRail(summary=SUMMARY)
    o = C.overview(st, ACC, START, END, http=cr)
    groups = sorted(p.get("group_by") for u, _h, p in cr.log if "summary" in u)
    assert groups == ["campaign", "company", "keywords", "source", "source"]                 # source twice: this range and the one before
    assert o["totals"]["calls"] == 454 and o["totals"]["missed"] == 37 and o["totals"]["missRate"] == round(37 / 454, 4)
    assert o["previous"]["calls"] == 280 and o["previousRange"] == list(C.previous_range(START, END))
    assert [s["key"] for s in o["sources"]] == ["Directories", "Google Ads", "Google Ads Maps", "Unknown"]
    assert {s["key"]: s["paid"] for s in o["sources"]} == {"Directories": False, "Google Ads": True, "Google Ads Maps": True, "Unknown": False}
    assert [l["key"] for l in o["locations"]] == ["Great Hill Braintree", "Old Office"]
    assert [c["key"] for c in o["campaigns"]] == ["performance-max-braintree", "brand"]       # a campaign that is a phone number, and a blank one, are dropped
    assert [k["key"] for k in o["keywords"]] == ["dentist near me", "[gentle dental]"]
    assert all(p.get("fields") == C.SUMMARY_FIELDS for u, _h, p in cr.log if "summary" in u)


def test_a_location_filter_is_sent_and_the_location_table_is_not_asked_for():
    cr = CallRail(summary=SUMMARY)
    o = C.overview(FakeStore(), ACC, START, END, company_id=COM, http=cr)
    sums = [p for u, _h, p in cr.log if "summary" in u]
    assert all(p.get("company_id") == COM for p in sums) and "company" not in {p["group_by"] for p in sums}
    assert "locations" not in o


def test_a_chosen_source_gives_its_own_key_numbers_and_searches_the_other_tables_for_it():
    cr = CallRail(summary=SUMMARY)
    o = C.overview(FakeStore(), ACC, START, END, source="Google Ads", http=cr)
    assert o["totals"]["calls"] == 100 and o["totals"]["leads"] == 20 and o["previous"]["calls"] == 80
    by = {(p["group_by"], p.get("search")) for u, _h, p in cr.log if "summary" in u and p["start_date"] == START}
    assert by == {("source", None), ("campaign", "Google Ads"), ("keywords", "Google Ads"), ("company", "Google Ads")}
    assert len(o["sources"]) == 4                                                           # the source table still shows every source
    none = C.overview(FakeStore(), ACC, START, END, source="Yelp", http=CallRail(summary=SUMMARY))
    assert none["totals"]["calls"] == 0 and none["totals"]["missRate"] is None and none["totals"]["leadRate"] is None


def test_the_overview_is_saved_and_refresh_asks_again():
    st, cr = FakeStore(), CallRail(summary=SUMMARY)
    C.overview(st, ACC, START, END, http=cr)
    n = len(cr.log)
    C.overview(st, ACC, START, END, http=cr)
    assert len(cr.log) == n
    C.overview(st, ACC, START, END, http=cr, force=True)
    assert len(cr.log) == 2 * n


def test_a_saved_copy_stands_in_when_callrail_cannot_answer_and_says_when_it_was_saved():
    st, cr = FakeStore(), CallRail(summary=SUMMARY)
    C.overview(st, ACC, START, END, http=cr)
    for k in list(st.at):
        st.at[k] -= 99999
    g.forget()
    notes = []
    o = C.overview(st, ACC, START, END, http=CallRail(fail=Resp(503, {})), notes=notes)
    assert o["totals"]["calls"] == 454 and len(notes) == 1


def test_rates_are_none_not_zero_when_there_were_no_calls():
    m = C._rates(C._metrics({}))
    assert m["calls"] == 0 and m["missRate"] is None and m["leadRate"] is None
    assert C._metrics({"total_calls": "x", "leads": None})["calls"] == 0


# ── The analysis ───────────────────────────────────────────────────────────

def test_the_sample_pages_are_spread_from_the_first_to_the_last():
    assert C.sample_pages(1) == [1] and C.sample_pages(4) == [1, 2, 3, 4] and C.sample_pages(0) == [1]
    p = C.sample_pages(455)
    assert len(p) == C.SAMPLE_PAGES and p[0] == 1 and p[-1] == 455 and p == sorted(set(p))
    assert all(1 <= x <= 455 for x in C.sample_pages(7))


def test_a_call_keeps_no_caller_name_number_or_city():
    c = C.parse_call(raw_call(1))
    blob = json.dumps(c)
    assert "Heather" not in blob and "8573109334" not in blob and "Boston" not in blob
    assert set(c) == {"id", "start", "company", "source", "campaign", "duration", "answered", "summary", "transcript", "tags", "sentiment", "score", "url"}
    assert c["tags"] == ["Booked Appointment"] and c["start"] == "2026-10-11T09:00:00"


def test_only_a_link_to_callrail_itself_is_kept():
    assert C._link("https://app.callrail.com/a/1") and C._link("https://callrail.com/x")
    for bad in ("http://app.callrail.com/a", "https://evil.example/app.callrail.com/", "https://callrail.com.evil.example/x", "javascript:alert(1)", "", None):
        assert C._link(bad) == "", bad


def test_the_analysis_samples_the_pages_filters_on_source_and_returns_no_call_text():
    pages = {i: [raw_call(i * 10 + j, source="Google Ads" if j % 2 == 0 else "Directories") for j in range(4)] for i in C.sample_pages(100)}
    cr = CallRail(calls={"pages": 100, "records": 9999, "by_page": pages})
    st = FakeStore()
    out = C.analysis(st, ACC, START, END, source="Google Ads", http=cr)
    calls = [(p["page"], p) for u, _h, p in cr.log if u.endswith("/calls.json")]
    assert sorted(p for p, _ in calls)[0] == 1 and len({p for p, _ in calls}) == C.SAMPLE_PAGES
    assert all(q["search"] == "Google Ads" and q["min_duration"] == C.MIN_SAMPLE_SECONDS and q["per_page"] == 100 for _p, q in calls)
    assert all("customer_name" not in q["fields"] and "customer_phone_number" not in q["fields"] for _p, q in calls)
    assert out["sampled"] == 12 and out["read"] == 12 and out["matching"] == 9999 and out["pages"] == C.SAMPLE_PAGES      # 24 calls read, 12 of them Google Ads
    assert out["minSeconds"] == C.MIN_SAMPLE_SECONDS
    assert {s["source"] for s in out["bySource"]} == {"Google Ads"}                  # CallRail's text search is not exact, this is
    blob = json.dumps(out) + json.dumps(st.cache)
    for leak in ("A new patient called to book", "Heather", "8573109334", "I am a new patient", "Hello"):
        assert leak not in blob, leak


def test_the_analysis_without_a_source_keeps_every_source_and_sends_no_search():
    pages = {1: [raw_call(1, source="Directories"), raw_call(2, source="Google Ads")]}
    cr = CallRail(calls={"pages": 1, "records": 2, "by_page": pages})
    out = C.analysis(FakeStore(), ACC, START, END, http=cr)
    assert "search" not in cr.log[0][2] and out["sampled"] == 2 and len(cr.log) == 1
    assert len(out["bySource"]) == 2


def test_the_analysis_filters_by_location_and_is_saved():
    cr = CallRail(calls={"pages": 1, "records": 1, "by_page": {1: [raw_call(1)]}})
    st = FakeStore()
    C.analysis(st, ACC, START, END, company_id=COM, http=cr)
    assert cr.log[0][2]["company_id"] == COM
    n = len(cr.log)
    C.analysis(st, ACC, START, END, company_id=COM, http=cr)
    assert len(cr.log) == n


def test_a_range_with_no_transcripts_is_an_empty_analysis_not_an_error():
    cr = CallRail(calls={"pages": 1, "records": 3, "by_page": {1: [raw_call(i, summary="", transcript="") for i in range(3)]}})
    out = C.analysis(FakeStore(), ACC, START, END, http=cr)
    assert out["sampled"] == 3 and out["read"] == 0 and out["withTranscript"] == 0 and out["bookRate"] is None


# ── The routes ─────────────────────────────────────────────────────────────

STAFF = "someone@position2.com"


def client_as(email):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": email, "name": "T"}
    return c


@pytest.fixture
def routes(monkeypatch):
    fs = FakeStore()
    for name in ("get_gads_entry", "put_gads_cache", "get_gads_cache", "get_gads_block", "gads_calls"):
        monkeypatch.setattr(paid_store, name, getattr(fs, name))
    cr = CallRail(summary=SUMMARY, calls={"pages": 1, "records": 2, "by_page": {1: [raw_call(1), raw_call(2)]}})
    import requests
    monkeypatch.setattr(requests, "get", cr)
    return cr


def url(kind, extra=""):
    return "/p2/paid-marketing/api/calls/%s?account=%s&start=%s&end=%s%s" % (kind, ACC, START, END, extra)


def test_the_page_opens_for_staff_and_not_for_anyone_else():
    path = "/p2/paid-marketing/call-transcript-analyzer"
    page = client_as(STAFF).get(path)
    assert page.status_code == 200 and b"cr_data.js" in page.data and b"call_analyzer.js" in page.data and b"HIPAA" in page.data
    assert appmod.app.test_client().get(path).status_code in (302, 401, 403)
    assert client_as("someone@gmail.com").get(path).status_code in (302, 401, 403)


def test_the_lists_and_both_views_answer(routes):
    c = client_as(STAFF)
    j = c.get("/p2/paid-marketing/api/calls/accounts").get_json()
    assert [a["name"] for a in j["accounts"]] == ["42 North Dental", "OIA"]
    j = c.get("/p2/paid-marketing/api/calls/locations?account=" + ACC).get_json()
    assert j["account"]["name"] == "42 North Dental" and [l["name"] for l in j["locations"]][0] == "Great Hill Braintree"
    ov = c.get(url("overview")).get_json()
    assert ov["overview"]["totals"]["calls"] == 454 and ov["account"]["id"] == ACC and ov["stale"] is None and ov["fetchedAt"]
    an = c.get(url("analysis", "&company=" + COM + "&source=Google%20Ads")).get_json()
    assert an["analysis"]["read"] == 2 and an["analysis"]["company"] == COM and an["analysis"]["source"] == "Google Ads"


def test_the_key_is_in_no_response(routes):
    c = client_as(STAFF)
    for u in ("/p2/paid-marketing/api/calls/accounts", url("overview"), url("analysis"), "/p2/paid-marketing/api/calls/locations?account=" + ACC):
        assert KEY not in c.get(u).get_data(as_text=True)
    assert KEY not in client_as(STAFF).get("/p2/paid-marketing/call-transcript-analyzer").get_data(as_text=True)


def test_every_request_to_callrail_was_a_get(routes):
    client_as(STAFF).get(url("overview"))
    client_as(STAFF).get(url("analysis"))
    assert routes.log and all(h["Authorization"] == 'Token token="%s"' % KEY for _u, h, _p in routes.log)


def test_bad_arguments_are_a_400_and_never_reach_callrail_beyond_the_lists(routes):
    c = client_as(STAFF)
    base = "/p2/paid-marketing/api/calls/overview?"
    for q in ("account=abc&start=%s&end=%s" % (START, END), "account=%s&start=x&end=%s" % (ACC, END), "account=%s&start=%s&end=%s&company=%s" % (ACC, START, END, "COM" + "e" * 32),
              "account=%s&start=%s&end=%s&source=%s" % (ACC, START, END, "a<script>"), "account=%s&start=%s&end=%s&source=%s" % (ACC, START, END, "x" * 80),
              "account=%s&start=%s&end=%s" % (ACC2.replace("b", "e"), START, END)):
        assert c.get(base + q).status_code == 400, q
    assert not any("summary" in u or u.endswith("/calls.json") for u, _h, _p in routes.log)


def test_an_unconnected_key_is_a_409_with_what_to_set(routes, monkeypatch):
    monkeypatch.delenv("CALLRAIL_API_KEY")
    r = client_as(STAFF).get(url("overview"))
    assert r.status_code == 409 and "CALLRAIL_API_KEY" in r.get_json()["configProblem"]


def test_a_refused_key_a_rate_limit_and_a_hiccup_are_401ish_429_and_502(routes, monkeypatch):
    import requests
    c = client_as(STAFF)
    monkeypatch.setattr(requests, "get", CallRail(fail=Resp(429, {})))
    r = c.get("/p2/paid-marketing/api/calls/accounts")
    assert r.status_code == 429 and r.get_json()["rateLimited"] is True
    monkeypatch.setattr(requests, "get", CallRail(fail=Resp(401, {"error": "bad"})))
    g.forget()
    paid_store_fs = FakeStore()
    for name in ("get_gads_entry", "put_gads_cache", "get_gads_cache", "get_gads_block"):
        monkeypatch.setattr(paid_store, name, getattr(paid_store_fs, name))
    r = c.get("/p2/paid-marketing/api/calls/accounts")
    assert r.status_code == 502 and "rejected the API key" in r.get_json()["error"]


def test_refresh_asks_again_and_pressing_it_twice_is_refused_with_the_wait(routes):
    c = client_as(STAFF)
    c.get(url("overview"))
    n = len(routes.log)
    r = c.get(url("overview", "&refresh=1"))
    assert r.status_code == 200 and len(routes.log) > n
    again = c.get(url("overview", "&refresh=1"))
    assert again.status_code == 429 and again.get_json()["cooldown"] is True and again.get_json()["retryIn"] > 0
    other = c.get(url("analysis", "&refresh=1"))                       # a different view has its own cool-down
    assert other.status_code == 200


def test_the_rate_limit_numbers_are_shown_to_admins_only(routes):
    j = client_as(STAFF).get("/p2/paid-marketing/api/calls/accounts").get_json()
    assert j["limits"] is None
    admin = client_as(sorted(appmod.ADMIN_EMAILS)[0]).get("/p2/paid-marketing/api/calls/accounts").get_json()
    assert admin["limits"] and admin["limits"]["hourlyAllowed"] == 1000


def test_a_call_keeps_its_tags_whether_callrail_sends_them_as_names_or_as_objects():
    assert C.parse_call({"id": "1", "tags": ["Booked Appointment", {"id": 2, "name": "Lead"}, "", {"id": 3}]})["tags"] == ["Booked Appointment", "Lead"]
