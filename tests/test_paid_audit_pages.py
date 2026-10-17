"""Account Audit: landing pages answer (tracker/paid_audit_pages.py)."""
import pytest

from tracker import paid_audit_pages as p


class Resp:
    def __init__(self, status=200, url="", closed=None):
        self.status_code, self.url, self._closed = status, url, closed

    def close(self):
        if self._closed is not None:
            self._closed.append(self.url)


def fetcher(answers, closed=None, calls=None):
    """answers: url -> status, or an exception to raise, or (status, final url)."""
    def get(url, **kw):
        if calls is not None:
            calls.append((url, kw))
        a = answers.get(url, 200)
        if isinstance(a, Exception):
            raise a
        if isinstance(a, tuple):
            return Resp(a[0], a[1], closed)
        return Resp(a, url, closed)
    return get


def test_a_url_is_cleaned_the_way_a_visitor_reaches_it():
    assert p.clean("https://a.test/x?utm_term={keyword}&c={_campaign}#top") == "https://a.test/x?utm_term=x&c=x"
    assert p.clean("  https://a.test/y  ") == "https://a.test/y" and p.clean(None) == ""


def test_a_page_is_its_host_and_path_not_its_query():
    assert p.page_key("https://A.test/x/?a=1") == p.page_key("https://a.test/x?b=2") == ("a.test", "/x")
    assert p.page_key("https://a.test") == p.page_key("https://a.test/") == ("a.test", "/")
    assert p.page_key("https://a.test/x") != p.page_key("https://a.test/y") and p.page_key("https://a.test/x") != p.page_key("https://b.test/x")


def test_one_url_per_page_busiest_first_http_only_and_capped():
    rows = [{"url": "https://a.test/x?a=1", "ads": 9}, {"url": "https://a.test/x?a=2", "ads": 5}, {"url": "ftp://a.test/f", "ads": 4}, {"url": "javascript:alert(1)", "ads": 3},
            {"url": "https://a.test/y", "ads": 2}, {"url": "https:///nohost", "ads": 1}]
    urls, distinct = p.pick(rows)
    assert urls == ["https://a.test/x?a=1", "https://a.test/y"] and distinct == 2
    many = [{"url": "https://a.test/p%d" % i, "ads": 1} for i in range(40)]
    urls, distinct = p.pick(many)
    assert len(urls) == p.MAX_CHECK == 25 and distinct == 40
    assert p.pick(many, limit=3)[0] == ["https://a.test/p0", "https://a.test/p1", "https://a.test/p2"]
    assert p.pick([]) == ([], 0)


def test_a_page_that_answers_is_recorded_with_status_and_time_and_the_response_is_closed():
    closed, calls = [], []
    out = p.check_one("https://a.test/x", fetcher({"https://a.test/x": 200}, closed, calls))
    assert out["status"] == 200 and out["error"] == "" and out["blocked"] is False and out["redirected"] is False and out["finalUrl"] == "https://a.test/x"
    assert isinstance(out["ms"], int) and out["ms"] >= 0
    assert closed == ["https://a.test/x"]
    url, kw = calls[0]
    assert kw["timeout"] == p.TIMEOUT_S and "Position2AccountAudit" in kw["headers"]["User-Agent"]


def test_a_page_that_moved_says_where():
    out = p.check_one("https://a.test/old", fetcher({"https://a.test/old": (200, "https://a.test/new")}))
    assert out["redirected"] is True and out["finalUrl"] == "https://a.test/new"
    same = p.check_one("https://a.test/x?a=1", fetcher({"https://a.test/x?a=1": (200, "https://a.test/x/?a=1&b=2")}))
    assert same["redirected"] is False                                       # a query or a trailing slash is not a move


def test_error_statuses_are_kept_and_automated_refusals_are_blocked_not_broken():
    assert p.check_one("https://a.test/x", fetcher({"https://a.test/x": 404}))["status"] == 404
    for code in (401, 403, 429):
        assert p.check_one("https://a.test/x", fetcher({"https://a.test/x": code}))["blocked"] is True
    for code in (200, 404, 500, 503):
        assert p.check_one("https://a.test/x", fetcher({"https://a.test/x": code}))["blocked"] is False


def test_a_fetch_that_raises_is_an_error_not_a_crash():
    cases = [(ValueError("Private or reserved page destinations are not allowed."), "refused (Private or reserved page destinations are not allowed.)"),
             (TimeoutError("timed out"), "timed out"), (OSError("Name or service not known"), "the address does not resolve"),
             (RuntimeError("boom"), "RuntimeError")]
    for exc, want in cases:
        out = p.check_one("https://a.test/x", fetcher({"https://a.test/x": exc}))
        assert out["error"] == want and out["status"] == 0 and out["blocked"] is False and out["redirected"] is False, want


def test_check_returns_the_pages_checked_out_of_all_the_distinct_ones():
    rows = [{"url": "https://a.test/p%d" % i, "ads": 100 - i} for i in range(30)]
    out = p.check(rows, fetcher({"https://a.test/p3": 500}))
    assert out["checked"] == 25 and out["of"] == 30 and len(out["results"]) == 25
    assert [r["url"] for r in out["results"]][:3] == ["https://a.test/p0", "https://a.test/p1", "https://a.test/p2"]       # busiest first, in order
    assert [r["status"] for r in out["results"] if r["status"] != 200] == [500]
    assert p.check([], fetcher({})) == {"checked": 0, "of": 0, "results": []}
    one = p.check(rows[:1], fetcher({}), workers=0)
    assert one["checked"] == 1
