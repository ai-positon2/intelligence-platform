"""Landing Page & Relevancy Check: the lab speed test (tracker/paid_lp_speed.py) and running it for a page of the report."""
import datetime as dt

import pytest
import requests

from tests.gads_fakes import FakeStore
from tests.test_paid_lp import ACCOUNT, ACCOUNT_ID, Fetcher, NOW, fresh_caches, run, search  # noqa: F401
from tracker import paid_lp as lp
from tracker import paid_lp_speed as sp


def psi(score=0.62, lcp=3100.4, tbt=410.0, cls=0.1234, fcp=1800, field=None, opportunities=None):
    audits = {"largest-contentful-paint": {"numericValue": lcp}, "total-blocking-time": {"numericValue": tbt}, "cumulative-layout-shift": {"numericValue": cls},
              "first-contentful-paint": {"numericValue": fcp}, "speed-index": {"numericValue": 3300}}
    for i, (title, ms) in enumerate(opportunities if opportunities is not None else [("Reduce unused JavaScript", 1200), ("Properly size images", 450), ("Tiny thing", 100)]):
        audits["opp%d" % i] = {"title": title, "details": {"type": "opportunity", "overallSavingsMs": ms}}
    audits["diag"] = {"title": "Not an opportunity", "details": {"type": "table", "overallSavingsMs": 9999}}
    body = {"lighthouseResult": {"categories": {"performance": {"score": score}}, "audits": audits}}
    if field is not None:
        body["loadingExperience"] = field
    return body


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def getter(resp, calls=None):
    def get(url, params=None, timeout=None):
        if calls is not None:
            calls.append((url, params, timeout))
        if isinstance(resp, Exception):
            raise resp
        return resp
    return get


# ── The key ─────────────────────────────────────────────────────────────────

def test_the_test_is_connected_only_when_a_key_is_set(monkeypatch):
    monkeypatch.delenv("PAGESPEED_API_KEY", raising=False)
    assert sp.configured() is False and "PAGESPEED_API_KEY" in sp.config_problem()
    monkeypatch.setenv("PAGESPEED_API_KEY", "  abc  ")
    assert sp.configured() is True and sp.api_key() == "abc" and sp.config_problem() is None
    monkeypatch.setenv("PAGESPEED_API_KEY", "   ")
    assert sp.configured() is False


def test_running_without_a_key_is_a_not_configured_error_and_nothing_is_sent(monkeypatch):
    monkeypatch.delenv("PAGESPEED_API_KEY", raising=False)
    calls = []
    with pytest.raises(sp.LabError) as e:
        sp.run("https://a.test/x", get=getter(Resp(200, psi()), calls))
    assert e.value.kind == "not_configured" and calls == []


# ── Reading the answer ──────────────────────────────────────────────────────

def test_a_result_has_the_score_the_times_and_the_biggest_savings():
    r = sp.parse(psi())
    assert (r["score"], r["lcpMs"], r["tbtMs"], r["cls"], r["fcpMs"], r["speedIndexMs"]) == (62, 3100, 410, 0.123, 1800, 3300)
    assert r["savings"] == [{"title": "Reduce unused JavaScript", "ms": 1200}, {"title": "Properly size images", "ms": 450}]          # a 100 ms saving and a table are left out
    assert r["field"] is None


def test_at_most_five_savings_the_biggest_first():
    opps = [("Saving %d" % i, 300 + i * 10) for i in range(9)]
    r = sp.parse(psi(opportunities=opps))
    assert len(r["savings"]) == sp.MAX_SAVINGS and r["savings"][0]["ms"] == 380 and [s["ms"] for s in r["savings"]] == sorted([s["ms"] for s in r["savings"]], reverse=True)


def test_a_page_lighthouse_could_not_score_has_no_score_but_still_a_result():
    assert sp.parse(psi(score=None))["score"] is None
    assert sp.parse(psi(score=1.0))["score"] == 100 and sp.parse(psi(score=0.0))["score"] == 0


def test_field_data_from_real_visitors_is_read_when_google_has_it():
    field = {"overall_category": "AVERAGE", "metrics": {"LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2800, "category": "AVERAGE"}, "CUMULATIVE_LAYOUT_SHIFT_SCORE": {"percentile": 12},
                                                         "INTERACTION_TO_NEXT_PAINT": {"percentile": 190}, "FIRST_CONTENTFUL_PAINT_MS": {"percentile": 1500}}}
    assert sp.parse(psi(field=field))["field"] == {"category": "average", "lcpMs": 2800, "inpMs": 190, "cls": 0.12, "fcpMs": 1500}
    assert sp.parse(psi(field={"overall_category": "FAST", "metrics": {}}))["field"] is None
    assert sp.parse(psi(field={"metrics": {"LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 900}}}))["field"] == {"category": None, "lcpMs": 900, "inpMs": None, "cls": None, "fcpMs": None}


def test_an_answer_that_is_not_a_test_result_is_an_error():
    for bad in (None, [], "x", {}, {"lighthouseResult": "nope"}):
        with pytest.raises(sp.LabError) as e:
            sp.parse(bad)
        assert e.value.kind == "api"


def test_missing_numbers_are_none_not_zero():
    r = sp.parse({"lighthouseResult": {"categories": {}, "audits": {}}})
    assert r["score"] is None and r["lcpMs"] is None and r["tbtMs"] is None and r["cls"] is None and r["savings"] == []


# ── Asking ──────────────────────────────────────────────────────────────────

def test_a_test_asks_for_a_mobile_performance_run_with_the_key_and_the_pages_own_address():
    calls = []
    r = sp.run("https://a.test/x?y=1", get=getter(Resp(200, psi()), calls), key="K")
    assert r["score"] == 62
    url, params, timeout = calls[0]
    assert url == sp.ENDPOINT and params == {"url": "https://a.test/x?y=1", "strategy": "mobile", "category": "performance", "key": "K"} and timeout == sp.TIMEOUT_S


@pytest.mark.parametrize("status, body, kind", [
    (429, {"error": {"message": "Quota exceeded"}}, "quota"),
    (400, {"error": {"message": "Lighthouse returned error: FAILED_DOCUMENT_REQUEST. Lighthouse was unable to reliably load the page"}}, "page"),
    (400, {"error": {"message": "API key not valid. Please pass a valid API key."}}, "not_configured"),
    (403, {"error": {"message": "PageSpeed Insights API has not been used in project 1 before or it is disabled."}}, "not_configured"),
    (500, {"error": {"message": "backend"}}, "api"), (502, None, "api")])
def test_what_goes_wrong_is_named(status, body, kind):
    with pytest.raises(sp.LabError) as e:
        sp.run("https://a.test/x", get=getter(Resp(status, body)), key="K")
    assert e.value.kind == kind and str(e.value)


def test_a_network_failure_is_an_api_error():
    with pytest.raises(sp.LabError) as e:
        sp.run("https://a.test/x", get=getter(requests.ConnectionError("down")), key="K")
    assert e.value.kind == "api" and "ConnectionError" in str(e.value)


def test_an_answer_that_is_not_json_is_an_error_not_a_crash():
    with pytest.raises(sp.LabError):
        sp.run("https://a.test/x", get=getter(Resp(200, None)), key="K")


# ── Kept results ────────────────────────────────────────────────────────────

def test_a_result_is_kept_with_when_it_ran_and_alongside_the_accounts_others():
    store = FakeStore()
    a = sp.remember(store, "111", "a.test/x", {"score": 70}, now=NOW)
    sp.remember(store, "111", "a.test/y", {"score": 50}, now=NOW)
    assert a == {"score": 70, "ranAt": NOW.isoformat()}
    got = sp.saved(store, "111", now=NOW)
    assert set(got) == {"a.test/x", "a.test/y"} and got["a.test/y"]["score"] == 50
    assert sp.saved(store, "222", now=NOW) == {}


def test_a_result_older_than_three_days_is_not_shown_and_a_new_one_drops_it():
    store = FakeStore()
    old = NOW - dt.timedelta(days=4)
    sp.remember(store, "111", "a.test/old", {"score": 10}, now=old)
    sp.remember(store, "111", "a.test/new", {"score": 90}, now=NOW)
    assert set(sp.saved(store, "111", now=NOW)) == {"a.test/new"}
    assert set(store.get_gads_cache_any(sp.cache_key("111"))[0]) == {"a.test/new"}
    assert set(sp.saved(store, "111", now=NOW + dt.timedelta(days=2))) == {"a.test/new"} and sp.saved(store, "111", now=NOW + dt.timedelta(days=4)) == {}


def test_a_store_that_fails_is_no_results_and_never_an_error():
    class Broken(FakeStore):
        def get_gads_cache_any(self, key):
            raise RuntimeError("db down")

        def put_gads_cache(self, key, value):
            raise RuntimeError("db down")
    assert sp.saved(Broken(), "111") == {}
    assert sp.remember(Broken(), "111", "a.test/x", {"score": 1})["score"] == 1


def test_a_malformed_saved_entry_is_ignored():
    store = FakeStore()
    store.put_gads_cache(sp.cache_key("111"), {"a.test/x": "nope", "a.test/y": {"score": 5, "ranAt": "garbage"}, "a.test/z": {"score": 6, "ranAt": NOW.isoformat()}})
    assert set(sp.saved(store, "111", now=NOW)) == {"a.test/z"}


# ── In the report ───────────────────────────────────────────────────────────

def test_a_page_with_a_kept_result_shows_it_and_the_speed_check_judges_it(monkeypatch):
    monkeypatch.setenv("PAGESPEED_API_KEY", "K")
    store = FakeStore()
    first = run(store=store)
    assert first["pages"][0]["lab"] is None and first["lab"] == {"configured": True}
    f = {x["id"]: x for s in first["pages"][0]["sections"] for x in s["findings"]}
    assert f["speed_lab"]["result"] == "na"
    sp.remember(store, ACCOUNT_ID, "acme-dental.com/lp", sp.parse(psi(score=0.35)), now=dt.datetime.now(dt.timezone.utc))
    out = run(store=store)
    page = out["pages"][0]
    assert page["lab"]["score"] == 35 and page["lab"]["savings"][0]["title"] == "Reduce unused JavaScript"
    f = {x["id"]: x for s in page["sections"] for x in s["findings"]}
    assert f["speed_lab"]["result"] == "fail" and "performance score 35 out of 100" in f["speed_lab"]["found"]


def test_the_report_says_whether_the_lab_test_is_connected(monkeypatch):
    monkeypatch.delenv("PAGESPEED_API_KEY", raising=False)
    assert run()["lab"] == {"configured": False}


def test_a_result_for_a_page_the_report_does_not_name_is_not_shown():
    store = FakeStore()
    sp.remember(store, ACCOUNT_ID, "other.test/page", {"score": 1}, now=dt.datetime.now(dt.timezone.utc))
    assert run(store=store)["pages"][0]["lab"] is None


# ── Running it for a page ───────────────────────────────────────────────────

def lab(page_id="acme-dental.com/lp", **kw):
    kw.setdefault("store", FakeStore())
    kw.setdefault("accounts_fn", lambda: [ACCOUNT])
    kw.setdefault("search_fn", search())
    kw.setdefault("fetcher", Fetcher())
    kw.setdefault("now", NOW)
    kw.setdefault("get", getter(Resp(200, psi())))
    return lp.run_lab(ACCOUNT_ID, page_id, **kw)


def test_the_test_is_run_for_a_page_of_the_report_with_its_own_address_and_the_result_is_kept(monkeypatch):
    monkeypatch.setenv("PAGESPEED_API_KEY", "K")
    store, calls = FakeStore(), []
    got = lab(store=store, get=getter(Resp(200, psi(score=0.8)), calls))
    assert got["score"] == 80 and got["ranAt"] == NOW.isoformat()
    assert calls[0][1]["url"] == "https://acme-dental.com/lp" and calls[0][1]["strategy"] == "mobile"
    assert sp.saved(store, ACCOUNT_ID, now=NOW)["acme-dental.com/lp"]["score"] == 80


def test_a_page_the_report_does_not_name_is_refused_and_nothing_is_sent(monkeypatch):
    monkeypatch.setenv("PAGESPEED_API_KEY", "K")
    calls = []
    for bad in ("evil.test/x", "", None, "https://acme-dental.com/lp"):
        with pytest.raises(lp.BadRequest):
            lab(page_id=bad, get=getter(Resp(200, psi()), calls))
    assert calls == []


def test_a_test_that_fails_keeps_nothing_and_raises(monkeypatch):
    monkeypatch.setenv("PAGESPEED_API_KEY", "K")
    store = FakeStore()
    with pytest.raises(sp.LabError):
        lab(store=store, get=getter(Resp(429, {"error": {"message": "x"}})))
    assert sp.saved(store, ACCOUNT_ID) == {}


def test_without_a_key_it_is_not_configured(monkeypatch):
    monkeypatch.delenv("PAGESPEED_API_KEY", raising=False)
    with pytest.raises(sp.LabError) as e:
        lab()
    assert e.value.kind == "not_configured"
