"""Paid Marketing Agents: QA Agent, the landing pages as a visitor sees them.

Pinned here: what is read from a page (title, headings, visible text without scripts,
forms, phones, privacy link, consent banner, tags, viewport, noindex, captcha); that a test
gclid and UTM are sent and that stripping them in a redirect is noticed; a junk parameter
that 404s a page is retried plainly; robots.txt for AdsBot; staging hosts; and that a URL
pointing inside our network is refused rather than fetched. No network is used: the
fetcher is a stand-in, except for the private-address cases, which fail before any connection.
"""
import os
import sys
from urllib.parse import urlsplit

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_qa_web as web  # noqa: E402

HTML = """<!doctype html><html><head><title> Example Co | Landscaping </title>
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="description" content="Design and build in Annapolis">
<script src="https://www.googletagmanager.com/gtag/js?id=AW-123456789"></script>
<script>var secret = "SCRIPT TEXT MUST NOT APPEAR"; dataLayer=[]; /* GTM-ABCD123 */</script>
<style>.x{color:red}</style></head>
<body><h1>Landscape design in Annapolis</h1><h2>Free estimates</h2>
<p>Call us on (410) 555-0100 for a free estimate. We use cookies to improve this site.</p>
<a href="tel:+14105550100">Call now</a><a href="/privacy-policy">Privacy Policy</a>
<form method="post" action="/contact"><input type="text" name="name"><input type="email" name="email"><input type="hidden" name="csrf">
<textarea name="message"></textarea><div class="g-recaptcha"></div><input type="submit" value="Send"></form>
<noscript>NOSCRIPT TEXT</noscript></body></html>"""


class Raw:
    def __init__(self, body):
        self.body = body

    def read(self, n=None, decode_content=True):
        return self.body[:n] if n else self.body


class Resp:
    def __init__(self, status, url, body, headers=None):
        self.status_code, self.url, self.raw = status, url, Raw(body.encode("utf-8"))
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self.encoding = "utf-8"
        self.closed = False

    def close(self):
        self.closed = True


class Site:
    """A fake fetcher: routes by path, can redirect (optionally stripping the query), 404 on junk parameters."""
    def __init__(self, routes=None, strip=False, junk404=False, robots=None):
        self.routes, self.strip, self.junk404, self.robots, self.seen = routes or {}, strip, junk404, robots, []

    def __call__(self, url, timeout=10, headers=None):
        self.seen.append(url)
        p = urlsplit(url)
        if p.path == "/robots.txt":
            return Resp(200, url, self.robots) if self.robots is not None else Resp(404, url, "not found")
        if self.junk404 and "gclid=" in p.query:
            return Resp(404, url, "<html>not found</html>")
        if p.path in self.routes and self.routes[p.path].startswith("->"):
            target = self.routes[p.path][2:]
            final = target if not self.strip else target.split("?")[0]
            if not self.strip and "?" not in final and p.query:
                final += "?" + p.query
            return Resp(200, final, HTML)
        return Resp(200, url, HTML)


# ── What is read from a page ────────────────────────────────────────────────

def test_a_page_is_boiled_down_to_facts_a_reviewer_can_quote():
    f = web.parse_page(HTML, "https://example.com/design/")
    assert f["title"] == "Example Co | Landscaping" and f["h1"] == ["Landscape design in Annapolis"] and f["h2"] == ["Free estimates"]
    assert f["viewport"].startswith("width=device-width") and f["metaDescription"].startswith("Design and build")
    assert "SCRIPT TEXT MUST NOT APPEAR" not in f["text"] and "NOSCRIPT TEXT" not in f["text"] and "color:red" not in f["text"]
    assert "Call us on (410) 555-0100" in f["text"]
    assert f["phonesInText"] == ["(410) 555-0100"] and f["telLinks"] == ["+14105550100"]
    assert f["privacyLink"] == {"href": "/privacy-policy", "text": "Privacy Policy"}
    assert f["consentBanner"] is True and f["hasCaptcha"] is True and f["noindex"] is False
    assert f["forms"] == [{"fields": ["name", "email", "message"], "method": "post"}]           # hidden and submit inputs are not fields
    assert "AW-123456789" in f["googleTags"] and f["googleTagManager"] == ["GTM-ABCD123"]
    assert f["pageHost"] == "example.com"


def test_a_noindex_page_and_a_page_with_nothing_on_it():
    assert web.parse_page('<meta name="robots" content="noindex, nofollow"><p>hi</p>')["noindex"] is True
    f = web.parse_page("<p>just text</p>")
    assert f["title"] == "" and f["forms"] == [] and f["privacyLink"] is None and f["consentBanner"] is False and f["googleTags"] == []


def test_broken_html_still_gives_what_it_can():
    f = web.parse_page("<html><title>Half a page<h1>Unclosed <b>heading<p>text &amp; more")
    assert isinstance(f, dict) and "text" in f


def test_the_text_excerpt_is_capped():
    assert len(web.parse_page("<p>" + "word " * 5000 + "</p>")["text"]) <= web.TEXT_CHARS


# ── Fetching one landing page ───────────────────────────────────────────────

def test_a_test_gclid_and_utm_are_sent_and_surviving_them_is_a_pass_for_the_check():
    site = Site()
    out = web.audit_page("https://example.com/design/", site)
    assert out["status"] == 200 and out["https"] is True and out["paramsSurvive"] is True and out["redirected"] is False
    assert "gclid=QAcheck123" in site.seen[0] and "utm_source=qa_check" in site.seen[0]
    assert out["title"] == "Example Co | Landscaping" and out["finalUrl"] == "https://example.com/design/" and "?" not in out["finalUrl"]
    assert out["robots"]["fetched"] and out["robots"]["adsbotAllowed"] is True


def test_a_redirect_that_strips_the_parameters_is_noticed():
    out = web.audit_page("https://example.com/old", Site({"/old": "->https://example.com/new"}, strip=True))
    assert out["paramsSurvive"] is False and out["redirected"] is True and out["finalUrl"] == "https://example.com/new"
    kept = web.audit_page("https://example.com/old", Site({"/old": "->https://example.com/new"}, strip=False))
    assert kept["paramsSurvive"] is True and kept["redirected"] is True


def test_an_existing_query_string_is_extended_not_replaced():
    site = Site()
    web.audit_page("https://example.com/p?a=1", site)
    assert site.seen[0].startswith("https://example.com/p?a=1&gclid=")


def test_a_page_that_404s_on_a_junk_parameter_is_asked_again_plainly():
    site = Site(junk404=True)
    out = web.audit_page("https://example.com/p", site)
    assert out["status"] == 200 and out["paramsSurvive"] is None                              # live, but the test could not be made
    assert any("gclid" not in u for u in site.seen[1:2])


def test_a_page_that_really_is_missing_says_404():
    class Gone(Site):
        def __call__(self, url, timeout=10, headers=None):
            return Resp(404, url, "<html>missing</html>") if urlsplit(url).path != "/robots.txt" else Resp(404, url, "")
    out = web.audit_page("https://example.com/gone", Gone())
    assert out["status"] == 404 and "title" not in out


def test_a_fetch_that_fails_is_reported_not_raised():
    def boom(url, timeout=10, headers=None):
        raise TimeoutError("timed out after 10s")
    out = web.audit_page("https://example.com/", boom)
    assert out["error"].startswith("TimeoutError") and "status" not in out and out["url"] == "https://example.com/"


def test_a_non_https_page_and_a_staging_address_are_marked():
    out = web.audit_page("http://staging.example.com/", Site())
    assert out["https"] is False and out["stagingHost"] is True
    for host, staging in (("www.example.com", False), ("dev.example.com", True), ("example-test.com", True), ("latest.example.com", False),
                          ("devon.example.com", False), ("uat-shop.example.com", True)):
        assert web.audit_page("https://%s/" % host, Site())["stagingHost"] is staging, host


# ── robots.txt ──────────────────────────────────────────────────────────────

def test_robots_can_block_adsbot_while_allowing_everyone_else():
    site = Site(robots="User-agent: AdsBot-Google\nDisallow: /\n\nUser-agent: *\nDisallow:\n")
    r = web.audit_page("https://example.com/", site)["robots"]
    assert r["adsbotAllowed"] is False and r["googlebotAllowed"] is True and r["wildcardAllowed"] is True


def test_no_robots_file_blocks_nothing_and_a_blocked_path_is_seen():
    assert web.audit_page("https://example.com/", Site(robots=None))["robots"]["adsbotAllowed"] is True
    site = Site(robots="User-agent: *\nDisallow: /private/\n")
    assert web.audit_page("https://example.com/private/x", site)["robots"]["wildcardAllowed"] is False


def test_an_html_page_served_as_robots_txt_is_not_read_as_rules():
    r = web.robots_rules("example.com", "https://x".split(":")[0], lambda *a, **k: Resp(200, "u", "<html><body>Welcome</body></html>"))
    assert r["adsbotAllowed"] is True and "no robots.txt" in r["note"]


# ── Several pages ───────────────────────────────────────────────────────────

def test_distinct_pages_only_a_few_and_only_http():
    urls = ["https://a.com/1", "https://a.com/1#top", "ftp://a.com/x", "javascript:alert(1)", "https://a.com/2", "https://a.com/3",
            "https://a.com/4", "https://a.com/5"]
    out = web.audit_pages(urls, Site())
    assert [p["url"] for p in out] == ["https://a.com/1", "https://a.com/2", "https://a.com/3", "https://a.com/4"]
    assert web.audit_pages([], Site()) == [] and web.audit_pages(["mailto:x@y.z"], Site()) == []


# ── Nothing inside our network is ever fetched ──────────────────────────────

@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://localhost/", "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/",
                                 "http://[::1]/", "https://192.168.1.1/", "http://127.0.0.1:8080/"])
def test_a_private_address_is_refused_before_any_connection(url):
    out = web.audit_page(url)                                   # the real fetcher: it refuses before it connects
    assert "error" in out and "status" not in out and out["error"].startswith("ValueError")


def test_credentials_in_a_url_are_refused():
    assert "error" in web.audit_page("https://user:pass@example.com/")
