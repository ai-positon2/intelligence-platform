"""A fake Google News for article-link decoding tests.

It replaces `requests.request`, not anything in news_client, so the code
under test runs its own request building and its own read loop. It serves
what the live endpoints served on 2026-10-08:

  * the article page: 597,296 bytes with `data-n-a-sg` starting at byte
    595,767, the shape that made a 512 KB read cap find no signature;
  * batchexecute: the publisher URL, JSON-escaped the way Google escapes it
    (`=` as \\u003d, `&` as \\u0026), but only when the posted f.req carries
    the id, timestamp and signature that page handed out, and only for the
    request shape that was verified live (POST, form content type).
    Anything else gets Google's error row.

Bodies are yielded in whatever chunk size the reader asks for, and `served`
counts only what was actually pulled, so a read cap in the code under test
is honoured exactly as the network would honour it.
"""

import base64
import hashlib
import json
import urllib.parse

ARTICLE_PAGE = "https://news.google.com/rss/articles/"
BATCHEXECUTE = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
PAGE_SIZE = 597_296
SIG_AT = 595_767
ERROR_REPLY = ')]}\'\n\n[["er",null,null,null,null,400,null,null,null,3],["di",10],["af.httprm",10,"-1",31]]'


def current_id(seed):
    """A current-format article id: CBMi + base64 of an opaque AU_yqL token,
    which holds no URL for an offline read to find."""
    token = b"AU_yqL" + base64.urlsafe_b64encode(
        hashlib.sha256(seed.encode()).digest() * 2).rstrip(b"=")
    raw = b"\x08\x13\x22" + bytes([len(token)]) + token
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def old_format_link(publisher_url):
    """An old-format article link: the publisher URL sits inside the id."""
    raw = b"\x08\x13\x22" + bytes([len(publisher_url)]) + publisher_url.encode() + b"\xd2\x01\x00"
    return ARTICLE_PAGE + base64.urlsafe_b64encode(raw).decode().rstrip("=") + "?oc=5"


def link(aid, query="?oc=5"):
    return ARTICLE_PAGE + aid + query


def signature(aid):
    return "AZ5r-" + aid[-12:], 1728400000 + len(aid)


def article_page(aid, size=PAGE_SIZE, sig_at=SIG_AT):
    sig, ts = signature(aid)
    marker = 'data-n-a-sg="%s" data-n-a-ts="%d"' % (sig, ts)
    head = "<!doctype html><html><body><c-wiz "
    filler = "x" * (sig_at - len(head))
    tail = "></c-wiz></body></html>"
    body = head + filler + marker + tail
    return body + "y" * max(0, size - len(body))


def batchexecute_reply(url):
    inner = json.dumps(["garturlres", url, 1], separators=(",", ":"))
    inner = inner.replace("=", "\\u003d").replace("&", "\\u0026")
    outer = json.dumps([["wrb.fr", "Fbv4je", inner, None, None, None, "generic"],
                        ["di", 13], ["af.httprm", 13, "-1360796389938591349", 50]],
                       separators=(",", ":"))
    return ")]}'\n\n" + outer


class FakeResponse:
    def __init__(self, body, status, url, on_chunk=None):
        self.status_code = status
        self.url = url
        self.encoding = "utf-8"
        self.served = 0
        self._body = body.encode("utf-8")
        self._on_chunk = on_chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), chunk_size):
            chunk = self._body[i:i + chunk_size]
            self.served += len(chunk)
            if self._on_chunk:
                self._on_chunk()
            yield chunk


class FakeGoogle:
    """Callable in place of `requests.request`. `urls` maps article id to the
    publisher URL Google would answer with."""

    def __init__(self, urls=None):
        self.urls = dict(urls or {})
        self.calls = []
        self.responses = []
        self.page = None      # optional aid -> (status, body, final_url)
        self.raises = None    # exception raised for every request
        self.on_chunk = None  # called each time a chunk is read

    def __call__(self, method, url, headers=None, data=None, timeout=None, **kw):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}),
                           "data": data, "timeout": timeout})
        if self.raises:
            raise self.raises
        if url.startswith(ARTICLE_PAGE):
            aid = url[len(ARTICLE_PAGE):]
            status, body, final = (self.page(aid) if self.page
                                   else (200, article_page(aid), url))
        elif url == BATCHEXECUTE:
            status, body, final = 200, self._reply(method, headers or {}, data), url
        else:
            raise AssertionError("unexpected request to %s" % url)
        resp = FakeResponse(body, status, final, self.on_chunk)
        self.responses.append(resp)
        return resp

    def _reply(self, method, headers, data):
        if method != "POST" or not (headers.get("Content-Type") or "").startswith(
                "application/x-www-form-urlencoded"):
            return ERROR_REPLY
        try:
            freq = json.loads(urllib.parse.parse_qs(data)["f.req"][0])
            rpc, payload = freq[0][0][0], json.loads(freq[0][0][1])
            aid, ts, sig = payload[2], payload[3], payload[4]
        except Exception:
            return ERROR_REPLY
        if rpc != "Fbv4je" or payload[0] != "garturlreq" or aid not in self.urls \
                or (sig, ts) != signature(aid):
            return ERROR_REPLY
        return batchexecute_reply(self.urls[aid])

    def requests_to(self, prefix):
        return [c for c in self.calls if c["url"].startswith(prefix)]


def rss(items):
    """A Google News RSS feed. items: dicts with title, link, published and
    optionally source / source_url / summary."""
    out = []
    for it in items:
        out.append(
            "<item><title>%s</title><link>%s</link><pubDate>%s</pubDate>%s%s</item>" % (
                it["title"], it["link"], it["published"],
                "<description>%s</description>" % it["summary"] if it.get("summary") else "",
                '<source url="%s">%s</source>' % (it.get("source_url", "https://pub.example"),
                                                  it.get("source", "Pub Example"))))
    return ('<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
            "<title>q - Google News</title>%s</channel></rss>" % "".join(out))


class FakeFeedResponse:
    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._data


def fake_urlopen(xml, seen=None):
    """In place of urllib.request.urlopen, which _fetch_feed uses."""
    def urlopen(req, timeout=None):
        if seen is not None:
            seen.append(req.full_url)
        return FakeFeedResponse(xml.encode("utf-8"))
    return urlopen
