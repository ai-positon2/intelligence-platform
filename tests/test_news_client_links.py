"""Google News article links -> publisher URLs (tracker/news_client.py).

Google's RSS links stopped carrying the publisher URL (0 of 52 decoded from
a Mac, 0 of 50 from Railway, 2026-10-08), so news_client now asks Google:
the article page for a signature, then batchexecute for the URL. These tests
run that code against tests/fake_google_news.py, which stands in for
`requests.request` and serves the live page shape (signature ~596 KB into a
~597 KB page), so the real read loop and the real request bodies are what
is being tested. Feeds come through a fake urlopen, so the three callers
that store these links are tested end to end.
"""

import base64
import email.utils
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
import requests

from tests import fake_google_news as fg
from tracker import jobs_client
from tracker import news_client as nc

PUB = "https://www.dentistrytoday.com/aspen-dental-launches-new-patient-apps/"


@pytest.fixture(autouse=True)
def clean_state(monkeypatch):
    """Fresh cache and breakers for every test, restored afterwards."""
    monkeypatch.setattr(nc, "_decode_cache", {})
    monkeypatch.setattr(nc, "_decode_fails", 0)
    monkeypatch.setattr(nc, "_decode_open_until", 0.0)
    monkeypatch.setattr(nc, "_circuit_open", False)
    monkeypatch.setattr(nc, "_circuit_fails", 0)


@pytest.fixture
def google(monkeypatch):
    g = fg.FakeGoogle()
    monkeypatch.setattr(requests, "request", g)
    return g


class Clock:
    """Stands in for news_client's `time` module only."""

    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(nc, "time", c)
    return c


CONSENT = "<html>Before you continue to Google</html>"


def failing_page(aid):
    return 200, CONSENT, fg.ARTICLE_PAGE + aid


# -- the decode itself -----------------------------------------------------------

def test_an_old_format_link_is_read_offline_without_any_request(google):
    # Old ids carried a 0xD2 0x01 field marker after the URL ("0gEA" in
    # base64); a UTF-8 read turned it into a trailing U+FFFD on the URL.
    link = fg.old_format_link(PUB)
    aid = nc._gnews_article_id(link)
    assert b"\xd2\x01" in base64.urlsafe_b64decode(aid + "=" * (-len(aid) % 4))
    assert nc._decode_google_news_url(link) == PUB
    assert google.calls == []


def test_a_current_link_is_decoded_through_google(google):
    aid = fg.current_id("a")
    google.urls[aid] = PUB
    assert nc._decode_offline(fg.link(aid)) is None   # what broke on 2026-10-08
    assert nc._decode_google_news_url(fg.link(aid)) == PUB

    page, post = google.calls
    assert (page["method"], page["url"]) == ("GET", fg.ARTICLE_PAGE + aid)
    assert "Chrome/" in page["headers"]["User-Agent"]   # the request shape verified live
    assert (post["method"], post["url"]) == ("POST", fg.BATCHEXECUTE)
    assert post["headers"]["Content-Type"].startswith("application/x-www-form-urlencoded")
    assert page["timeout"] == post["timeout"] == nc._DECODE_TIMEOUT
    # the page was read to its end, where the signature is
    assert google.responses[0].served == fg.PAGE_SIZE


def test_a_512_kb_read_cap_misses_the_signature_and_says_why(google, monkeypatch):
    """The trap the probe hit first: the signature is ~596 KB in. This shows
    the fake honours the cap, so the test above fails under any cap below
    the page size."""
    aid = fg.current_id("cap")
    google.urls[aid] = PUB
    monkeypatch.setattr(nc, "_DECODE_PAGE_MAX_READ", 512 * 1024)
    out = nc._resolve_article_url(fg.link(aid))
    assert out["url"] is None
    assert out["reason"] == ("article page gave no signature "
                             "(200, 524288 bytes, cut short at the 512 KB read cap)")
    assert google.responses[0].served == 512 * 1024
    assert len(google.calls) == 1


def test_a_runaway_page_stops_at_the_read_cap(google):
    aid = fg.current_id("big")
    google.page = lambda a: (200, "z" * (6 * 1024 * 1024), fg.ARTICLE_PAGE + a)
    out = nc._resolve_article_url(fg.link(aid))
    assert out["reason"] == ("article page gave no signature "
                             "(200, 4194304 bytes, cut short at the 4096 KB read cap)")
    assert google.responses[0].served == nc._DECODE_PAGE_MAX_READ


def test_an_escaped_url_comes_back_unescaped(google):
    # Live: a regex over the raw reply gave "watch?v\\u003dnLiSWJDhAmQ".
    aid = fg.current_id("yt")
    google.urls[aid] = "https://www.youtube.com/watch?v=nLiSWJDhAmQ&t=5"
    assert nc._decode_google_news_url(fg.link(aid)) == \
        "https://www.youtube.com/watch?v=nLiSWJDhAmQ&t=5"


def test_a_decoded_link_is_cached_by_article_id(google):
    aid = fg.current_id("cache")
    google.urls[aid] = PUB
    assert nc._decode_google_news_url(fg.link(aid)) == PUB
    assert nc._decode_google_news_url(fg.link(aid, query="")) == PUB   # same id, other query
    assert len(google.calls) == 2   # one page + one batchexecute, not four


def test_the_cache_drops_its_oldest_entry_at_the_cap(google, monkeypatch):
    monkeypatch.setattr(nc, "_DECODE_CACHE_MAX", 2)
    ids = [fg.current_id("e%d" % i) for i in range(3)]
    for i, aid in enumerate(ids):
        google.urls[aid] = "https://pub.example/%d" % i
        nc._decode_google_news_url(fg.link(aid))
    assert nc._decode_cache == {ids[1]: "https://pub.example/1", ids[2]: "https://pub.example/2"}
    nc._decode_google_news_url(fg.link(ids[2]))   # still cached: no request
    assert len(google.calls) == 6


def test_a_failed_decode_keeps_the_google_link_and_is_retried_next_time(google):
    aid = fg.current_id("fail")
    google.page = failing_page
    assert nc._decode_google_news_url(fg.link(aid)) == fg.link(aid)
    assert nc._decode_google_news_url(fg.link(aid)) == fg.link(aid)
    assert len(google.requests_to(fg.ARTICLE_PAGE)) == 2   # a failure is not cached


def test_an_unknown_article_gets_googles_error_row_and_no_url(google):
    aid = fg.current_id("unknown")   # not in google.urls
    out = nc._resolve_article_url(fg.link(aid))
    assert out["url"] is None
    assert out["reason"].startswith("batchexecute gave no URL (200, ")


def test_the_reason_names_googles_block_page(google):
    body = "<html>Our systems have detected unusual traffic</html>"
    google.page = lambda a: (200, body, "https://www.google.com/sorry/index?continue=x")
    out = nc._resolve_article_url(fg.link(fg.current_id("sorry")))
    assert out["reason"] == ("article page gave no signature "
                             "(200, %d bytes, redirected to www.google.com/sorry)" % len(body))


@pytest.mark.parametrize("exc, reason", [
    (requests.ConnectionError("refused"), "article page gave no signature (ConnectionError: refused)"),
    (requests.Timeout("slow"), "article page gave no signature (timeout)"),
    (ValueError("odd"), "article page gave no signature (ValueError: odd)"),
])
def test_a_transport_failure_never_raises(google, exc, reason):
    google.raises = exc
    link = fg.link(fg.current_id("x"))
    assert nc._resolve_article_url(link)["reason"] == reason
    assert nc._decode_google_news_url(link) == link


def test_a_slow_trickle_is_cut_at_the_total_timeout(google, clock):
    """requests' timeout is per socket read; a page trickling in under it
    would otherwise hold a worker for as long as Google cares to drip."""
    aid = fg.current_id("slow")
    google.urls[aid] = PUB

    def tick():
        clock.t += 1.0   # one second per 64 KB chunk: ~10 s for the page
    google.on_chunk = tick
    out = nc._resolve_article_url(fg.link(aid))
    assert out["reason"] == "article page gave no signature (timeout)"
    assert google.responses[0].served < fg.PAGE_SIZE
    assert out["ms"] == int((nc._DECODE_TIMEOUT + 1) * 1000)
    assert len(google.calls) == 1


def test_a_signature_that_arrived_before_the_timeout_is_still_used(google, clock):
    aid = fg.current_id("early-sig")
    google.urls[aid] = PUB
    google.page = lambda a: (200, fg.article_page(a, sig_at=1_000), fg.ARTICLE_PAGE + a)

    def tick():
        if len(google.calls) == 1:   # only the page trickles
            clock.t += 1.0
    google.on_chunk = tick
    assert nc._decode_google_news_url(fg.link(aid)) == PUB
    assert google.responses[0].served < fg.PAGE_SIZE   # the page was cut at the deadline
    assert len(google.calls) == 2


def test_the_read_cap_is_exact_even_off_a_chunk_boundary(google, monkeypatch):
    monkeypatch.setattr(nc, "_DECODE_PAGE_MAX_READ", 100_000)
    out = nc._resolve_article_url(fg.link(fg.current_id("odd-cap")))
    assert out["reason"] == ("article page gave no signature "
                             "(200, 100000 bytes, cut short at the 97 KB read cap)")


@pytest.mark.parametrize("link", [
    PUB,
    "",
    "https://news.google.com/rss/search?q=aspen",
    "https://evil.example/?next=news.google.com/rss/articles/CBMiABC",
])
def test_anything_but_a_google_article_link_passes_through_untouched(google, link):
    assert nc._decode_google_news_url(link) == link
    assert google.calls == []


# -- breakers --------------------------------------------------------------------

def test_no_request_while_the_feed_breaker_is_open(google, monkeypatch):
    aid = fg.current_id("blocked")
    google.urls[aid] = PUB
    monkeypatch.setattr(nc, "_circuit_open", True)
    assert nc._decode_google_news_url(fg.link(aid)) == fg.link(aid)
    assert google.calls == []


def test_the_decode_breaker_opens_and_names_the_last_failure(google, clock, caplog):
    google.page = failing_page
    links = [fg.link(fg.current_id("b%d" % i)) for i in range(nc._DECODE_CIRCUIT_THRESHOLD + 3)]
    with caplog.at_level(logging.ERROR, logger=nc.logger.name):
        out = [nc._decode_google_news_url(l) for l in links]
    assert out == links
    assert len(google.calls) == nc._DECODE_CIRCUIT_THRESHOLD   # then it stops asking
    opened = [r.getMessage() for r in caplog.records if "circuit OPEN" in r.getMessage()]
    assert len(opened) == 1
    assert "after %d consecutive failures" % nc._DECODE_CIRCUIT_THRESHOLD in opened[0]
    assert "(last: article page gave no signature (200, %d bytes))" % len(CONSENT) in opened[0]


def test_a_success_resets_the_failure_count(google, clock):
    good = fg.current_id("good")
    google.urls[good] = PUB
    google.page = lambda a: (200, fg.article_page(a), fg.ARTICLE_PAGE + a) if a == good \
        else failing_page(a)
    n = nc._DECODE_CIRCUIT_THRESHOLD - 1
    for i in range(n):
        nc._decode_google_news_url(fg.link(fg.current_id("f%d" % i)))
    assert nc._decode_google_news_url(fg.link(good)) == PUB
    for i in range(n):
        nc._decode_google_news_url(fg.link(fg.current_id("g%d" % i)))
    before = len(google.calls)
    nc._decode_google_news_url(fg.link(fg.current_id("still-asking")))
    assert len(google.calls) == before + 1   # 4 + 1 + 4 failures never reached 5 in a row


def test_a_decode_in_flight_when_the_breaker_opened_closes_it_on_success(google, clock):
    google.page = failing_page
    for i in range(nc._DECODE_CIRCUIT_THRESHOLD):
        nc._decode_google_news_url(fg.link(fg.current_id("o%d" % i)))
    opened = len(google.calls)
    # another worker's request, sent before the breaker opened, comes back good
    nc._decode_record("in-flight", {"url": PUB, "reason": None, "ms": 5})
    nc._decode_google_news_url(fg.link(fg.current_id("after")))
    assert len(google.calls) == opened + 1


def test_after_the_cooldown_one_trial_decides(google, clock):
    google.page = failing_page
    for i in range(nc._DECODE_CIRCUIT_THRESHOLD):
        nc._decode_google_news_url(fg.link(fg.current_id("c%d" % i)))
    opened = len(google.calls)

    clock.t += nc._DECODE_COOLDOWN - 1
    nc._decode_google_news_url(fg.link(fg.current_id("early")))
    assert len(google.calls) == opened   # still open

    clock.t += 2
    nc._decode_google_news_url(fg.link(fg.current_id("trial-1")))
    assert len(google.calls) == opened + 1   # one trial...
    nc._decode_google_news_url(fg.link(fg.current_id("trial-2")))
    assert len(google.calls) == opened + 1   # ...which failed, so open again at once

    clock.t += nc._DECODE_COOLDOWN + 1
    good = fg.current_id("recovered")
    google.urls[good] = PUB
    google.page = None
    assert nc._decode_google_news_url(fg.link(good)) == PUB
    after = len(google.calls)
    google.page = failing_page
    nc._decode_google_news_url(fg.link(fg.current_id("next")))
    assert len(google.calls) == after + 1   # closed: the count started over


def test_concurrent_decodes_all_land(google):
    ids = [fg.current_id("t%d" % i) for i in range(12)]
    for i, aid in enumerate(ids):
        google.urls[aid] = "https://pub.example/%d" % i
    barrier = threading.Barrier(6, timeout=10)

    def one(i):
        if i < 6:
            barrier.wait()   # the first six start together
        return nc._decode_google_news_url(fg.link(ids[i]))

    with ThreadPoolExecutor(max_workers=6) as pool:
        out = list(pool.map(one, range(12)))
    assert out == ["https://pub.example/%d" % i for i in range(12)]
    assert len(nc._decode_cache) == 12


# -- the helpers -------------------------------------------------------------------

def test_decode_params_reads_signature_and_timestamp():
    html = '<div jscontroller="x" data-n-a-sg="AZ5r3eT" data-n-a-ts="1728400000"></div>'
    assert nc.decode_params(html) == ("AZ5r3eT", 1728400000)


@pytest.mark.parametrize("html", [
    "<html>consent page</html>",
    '<div data-n-a-sg="AZ5r3eT"></div>',
    '<div data-n-a-sg="AZ5r3eT" data-n-a-ts="soon"></div>',
    "",
])
def test_decode_params_is_none_without_both(html):
    assert nc.decode_params(html) is None


def test_decoded_url_reads_a_live_reply():
    reply = (')]}\'\n\n[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://cw33.com/news/a/\\",1,'
             '\\"https://cw33.com/news/a/amp/\\"]",null,null,null,"generic"],["di",15],'
             '["af.httprm",15,"-5320025654765032916",53]]')
    assert nc.decoded_url(reply) == "https://cw33.com/news/a/"


def test_decoded_url_unescapes_a_live_youtube_reply():
    reply = (')]}\'\n\n[["wrb.fr","Fbv4je","[\\"garturlres\\",\\"https://www.youtube.com/watch?v'
             '\\\\u003dnLiSWJDhAmQ\\",1]",null,null,null,"generic"],["di",10],'
             '["af.httprm",10,"-4424043557222765121",63]]')
    assert nc.decoded_url(reply) == "https://www.youtube.com/watch?v=nLiSWJDhAmQ"


@pytest.mark.parametrize("reply", [
    fg.ERROR_REPLY,
    "",
    "<html>502</html>",
    fg.batchexecute_reply("https://news.google.com/rss/articles/CBMiABC"),
    fg.batchexecute_reply("javascript:alert(1)"),
    fg.batchexecute_reply("/relative/path"),
    ')]}\'\n\n[["wrb.fr","Fbv4je","not json"]]',
])
def test_decoded_url_is_none_for_anything_but_a_publisher_url(reply):
    assert nc.decoded_url(reply) is None


def test_decoded_url_reads_a_reply_without_the_guard_prefix():
    assert nc.decoded_url(fg.batchexecute_reply(PUB)[len(")]}'\n\n"):]) == PUB


def test_batchexecute_body_round_trips_id_timestamp_and_signature():
    g = fg.FakeGoogle({"CBMiXYZ": PUB})
    sig, ts = fg.signature("CBMiXYZ")
    body = nc.batchexecute_body("CBMiXYZ", ts, sig)
    assert body.startswith("f.req=")
    assert nc.decoded_url(g._reply("POST", {"Content-Type": "application/x-www-form-urlencoded"},
                                   body)) == PUB
    assert nc.decoded_url(g._reply("POST", {"Content-Type": "application/x-www-form-urlencoded"},
                                   nc.batchexecute_body("CBMiXYZ", ts, "WRONG"))) is None


# -- the callers that store these links ----------------------------------------------

def _recent(days=1):
    return email.utils.format_datetime(datetime.now(timezone.utc) - timedelta(days=days))


@pytest.fixture
def feed(monkeypatch):
    """Serve one RSS feed to _fetch_feed, with its jitter sleep at zero."""
    seen = []

    def serve(items):
        monkeypatch.setattr(nc.urllib.request, "urlopen", fg.fake_urlopen(fg.rss(items), seen))
        return seen
    monkeypatch.setattr(nc.random, "uniform", lambda a, b: 0)
    return serve


def test_rss_articles_store_the_publisher_url(google, feed):
    aid = fg.current_id("rss")
    google.urls[aid] = PUB
    feed([{"title": "Aspen Dental launches apps", "link": fg.link(aid), "published": _recent(),
           "source": "Dentistry Today", "source_url": "https://www.dentistrytoday.com",
           "summary": "New patient apps."}])
    [art] = nc._rss_articles("Aspen Dental")
    assert art["url"] == PUB
    # everything else is what it was before the decode changed
    assert art["title"] == "Aspen Dental launches apps"
    assert art["source"] == "Dentistry Today"
    assert art["summary"] == "New patient apps."


def test_leadership_news_stores_the_publisher_url(google, feed):
    aid = fg.current_id("lead")
    google.urls[aid] = "https://www.prnewswire.com/news/acme-cmo.html"
    feed([{"title": "Acme names Jane Smith as Chief Marketing Officer", "link": fg.link(aid),
           "published": _recent()}])
    [hit] = nc.get_leadership_from_news("Acme")
    assert hit["source_url"] == "https://www.prnewswire.com/news/acme-cmo.html"
    assert (hit["name"], hit["title"]) == ("Jane Smith", "Chief Marketing Officer")


def test_job_postings_store_the_publisher_url(google, feed):
    aid = fg.current_id("job")
    google.urls[aid] = "https://boards.example/acme/3d-artist"
    seen = feed([{"title": "Acme is hiring a 3D Artist", "link": fg.link(aid),
                  "published": _recent(), "source": "Job Board"}])
    [post] = jobs_client.get_job_postings("Acme")
    assert post["url"] == "https://boards.example/acme/3d-artist"
    assert (post["title"], post["source"], post["role"]) == \
        ("Acme is hiring a 3D Artist", "Job Board", "3d artist")
    assert len(seen) == 1


def test_a_stale_article_costs_no_decode(google, feed):
    aid = fg.current_id("old")
    google.urls[aid] = PUB
    feed([{"title": "Aspen Dental news", "link": fg.link(aid), "published": _recent(days=400)}])
    assert nc._rss_articles("Aspen Dental") == []
    assert google.calls == []
