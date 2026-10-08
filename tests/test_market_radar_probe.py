"""Market Radar Phase 0 source check (tracker/market_radar_probe.py) and its
admin route. No network: `_fetch` is replaced with canned responses, because
the point of these tests is the reading of a response (blocked vs rate
limited vs empty vs ok), the burst and time-budget bookkeeping, and who may
run the check. The live answer is what the deployed route is for.
"""

import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
from tracker import market_radar_probe as mrp  # noqa: E402

ROUTE = "/p2/admin/external-usage/market-radar-sources-check"
ADMIN = "reporting@position2.com"


def res(status=200, text="", final_url="https://example.com/", error=None):
    return {"status": status, "bytes": len(text), "truncated": False, "ms": 5,
            "final_url": final_url, "text": text, "error": error}


def feed(n):
    items = "".join(
        "<item><title>T%d</title><link>https://news.google.com/rss/articles/CBMiA%d?oc=5</link></item>"
        % (i, i) for i in range(n))
    return '<?xml version="1.0"?><rss version="2.0"><channel>%s</channel></rss>' % items


# -- verdict -----------------------------------------------------------------

@pytest.mark.parametrize("r, items, expected", [
    (res(200, feed(3)), 3, "ok"),
    (res(200, feed(0)), 0, "empty"),
    (res(503, "<html>Service Unavailable</html>"), 0, "blocked"),
    (res(403, "Forbidden"), None, "blocked"),
    (res(200, "<html>please verify</html>",
         final_url="https://www.google.com/sorry/index?continue=x"), 0, "blocked"),
    (res(200, "<html>Our systems have detected unusual traffic</html>"), 0, "blocked"),
    (res(429, "Please limit requests to one every 5 seconds"), None, "rate_limited"),
    (res(None, error="timeout"), None, "timeout"),
    (res(None, error="connection: refused"), None, "error"),
    (res(500, "oops"), None, "error"),
    (res(200, "{}"), None, "ok"),
])
def test_verdict(r, items, expected):
    assert mrp.verdict(r, items) == expected


def test_a_headline_mentioning_unusual_traffic_is_not_a_block():
    text = feed(1).replace("<title>T0</title>", "<title>Unusual traffic on I-5 today</title>")
    assert mrp.verdict(res(200, text), 1) == "ok"


def test_a_200_with_no_items_is_never_reported_as_ok():
    row = mrp._row("Google News US", "x", res(200, feed(0)), 0)
    assert row["verdict"] == "empty"


def test_a_failure_row_carries_the_start_of_the_body_as_its_detail():
    row = mrp._row("GDELT", "x", res(429, "Please   limit requests\n to one"), None)
    assert row["verdict"] == "rate_limited"
    assert row["detail"] == "Please limit requests to one"


# -- link decoding helpers -----------------------------------------------------

def test_decode_params_reads_signature_and_timestamp():
    html = '<div jscontroller="x" data-n-a-sg="AZ5r3eT" data-n-a-ts="1728400000"></div>'
    assert mrp.decode_params(html) == ("AZ5r3eT", 1728400000)


def test_decode_params_is_none_without_them():
    assert mrp.decode_params("<html>consent page</html>") is None


def test_decoded_url_reads_the_batchexecute_reply():
    reply = (')]}\'\n\n[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://www.prnewswire.com/a.html\\",1]",'
             'null,null,null,"generic"]]')
    assert mrp.decoded_url(reply) == "https://www.prnewswire.com/a.html"


def test_decoded_url_is_none_on_an_error_reply():
    assert mrp.decoded_url(')]}\'\n\n[["er",null,null,null,null,400]]') is None


def test_batchexecute_body_carries_id_timestamp_and_signature():
    body = mrp.batchexecute_body("CBMiXYZ", 1728400000, "AZ5r3eT")
    assert body.startswith("f.req=")
    for part in ("CBMiXYZ", "1728400000", "AZ5r3eT", "garturlreq", "Fbv4je"):
        assert part in body


def _page_fetch(page, reply):
    """A fake network that, like the real one, returns at most `max_read`
    bytes of each body."""
    def fake(url, max_read=mrp.MAX_READ, **kw):
        body = page if "/articles/" in url else reply
        r = res(200, body[:max_read])
        r["bytes"], r["truncated"] = len(r["text"]), len(body) > max_read
        return r
    return fake


def test_current_decode_reads_a_signature_near_the_end_of_a_large_page(monkeypatch):
    # Measured live: the signature sits ~596 KB into a ~597 KB page, past the
    # default read cap. A capped read reported "no signature" for every link.
    page = "x" * 595_000 + '<div data-n-a-sg="SIG" data-n-a-ts="1728400000"></div>' + "y" * 1_500
    reply = '[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://pub.example/a\\",1]"]]'
    monkeypatch.setattr(mrp, "_fetch", _page_fetch(page, reply))
    out = mrp._decode_current("https://news.google.com/rss/articles/CBMiABC?oc=5")
    assert out["ok"] is True and out["url"] == "https://pub.example/a"


def test_a_page_without_a_signature_says_how_much_was_read(monkeypatch):
    monkeypatch.setattr(mrp, "_fetch", _page_fetch("<html>consent</html>", ""))
    out = mrp._decode_current("https://news.google.com/rss/articles/CBMiABC")
    assert out["ok"] is False
    assert out["reason"] == "article page gave no signature (ok, 200, 20 bytes)"


# -- the Google section with a fake network ------------------------------------

@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(mrp, "BURST_GAP", 0)
    monkeypatch.setattr(mrp, "_decode_current",
                        lambda link: {"ok": True, "url": "https://pub.example/a", "ms": 3})


def test_burst_reports_where_the_first_failure_came(monkeypatch, fast):
    calls = {"n": 0}
    burst_start = 2 + len(mrp.EDITIONS)   # two user-agent checks, then editions

    def fake_fetch(url, **kw):
        calls["n"] += 1
        if calls["n"] > burst_start + 5:    # burst requests 6 onwards are refused
            return res(503, "<html>sorry</html>")
        return res(200, feed(4))

    monkeypatch.setattr(mrp, "_fetch", fake_fetch)
    out = mrp.check_google_news()
    burst = out["burst"]
    assert burst["requests"] == len(mrp.BURST_QUERIES)
    assert burst["ok"] == 5
    assert burst["first_failure_at"] == 6
    assert burst["verdicts"] == {"ok": 5, "blocked": len(mrp.BURST_QUERIES) - 5}
    assert all(r["verdict"] == "ok" for r in out["editions"])


def test_a_clean_burst_has_no_first_failure(monkeypatch, fast):
    monkeypatch.setattr(mrp, "_fetch", lambda url, **kw: res(200, feed(5)))
    out = mrp.check_google_news()
    assert out["burst"]["first_failure_at"] is None
    assert out["burst"]["ok"] == len(mrp.BURST_QUERIES)
    # only the first DECODE_SAMPLE of the 5 links go through the costly method
    assert out["links"]["current_method"]["tried"] == mrp.DECODE_SAMPLE == 3
    assert out["links"]["current_method"]["decoded"] == mrp.DECODE_SAMPLE
    # the old offline decoder finds nothing in these opaque links
    assert out["links"]["old_offline_decoder"] == {"links": 5, "decoded": 0}


def test_when_every_feed_is_blocked_link_decoding_is_not_checked(monkeypatch, fast):
    monkeypatch.setattr(mrp, "_fetch", lambda url, **kw: res(503, "<html>no</html>"))
    out = mrp.check_google_news()
    assert [r["verdict"] for r in out["user_agent"]] == ["blocked", "blocked"]
    assert "not_checked" in out["links"]
    assert out["burst"]["ok"] == 0 and out["burst"]["first_failure_at"] == 1


def test_a_spent_time_budget_is_reported_as_skipped_not_passed(monkeypatch, fast):
    monkeypatch.setattr(mrp, "_fetch", lambda url, **kw: res(200, feed(2)))
    out = mrp.check_google_news(budget=-1)   # already out of time after the UA checks
    assert {r["verdict"] for r in out["editions"]} == {"skipped"}
    assert out["burst"]["requests"] == 0
    assert out["burst"]["planned"] == len(mrp.BURST_QUERIES)
    assert out["links"] == {"not_checked": "time budget spent before link decoding"}


def test_probe_summary_counts_every_row_that_is_not_ok(monkeypatch, fast):
    monkeypatch.setattr(mrp, "egress", lambda: {"ip": "1.2.3.4", "org": "AS1 Test"})
    monkeypatch.setattr(mrp, "check_google_news", lambda: {
        "user_agent": [{"name": "a", "verdict": "ok", "status": 200}],
        "editions": [{"name": "b", "verdict": "blocked", "status": 503}],
        "burst": {}, "links": {}})
    monkeypatch.setattr(mrp, "other_sources", lambda: [
        {"name": "c", "verdict": "rate_limited", "status": 429},
        {"name": "d", "verdict": "ok", "status": 200}])
    out = mrp.probe()
    assert out["summary"]["checks"] == 4
    assert out["summary"]["ok"] == 2
    assert [r["name"] for r in out["summary"]["not_ok"]] == ["b", "c"]
    assert out["not_checked"]   # what this check cannot see is always listed


# -- the route ----------------------------------------------------------------

def _client(email):
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


@pytest.fixture
def stub_probe(monkeypatch):
    monkeypatch.setattr(mrp, "probe", lambda: {"summary": {"ok": 1}})


def test_logged_out_visitor_is_sent_to_sign_in(stub_probe):
    resp = _client(None).post(ROUTE)
    assert resp.status_code in (301, 302)


def test_position2_non_admin_is_forbidden(stub_probe):
    resp = _client("someone@position2.com").post(ROUTE)
    assert resp.status_code == 403


def test_admin_gets_the_probe_result(stub_probe):
    resp = _client(ADMIN).post(ROUTE)
    assert resp.status_code == 200
    assert resp.get_json() == {"summary": {"ok": 1}}


def test_get_is_not_allowed(stub_probe):
    assert _client(ADMIN).get(ROUTE).status_code == 405


def test_cross_site_post_is_refused(stub_probe):
    resp = _client(ADMIN).post(ROUTE, headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_same_site_post_is_allowed(stub_probe):
    resp = _client(ADMIN).post(ROUTE, headers={"Origin": "http://localhost"})
    assert resp.status_code == 200


def test_a_crashing_probe_returns_its_error_not_a_500_page(monkeypatch):
    def boom():
        raise RuntimeError("network stack missing")
    monkeypatch.setattr(mrp, "probe", boom)
    resp = _client(ADMIN).post(ROUTE)
    assert resp.status_code == 500
    assert resp.get_json() == {"error": "RuntimeError: network stack missing"}
