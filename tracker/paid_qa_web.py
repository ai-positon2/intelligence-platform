"""Paid Marketing Agents: QA Agent, the landing pages as a visitor sees them.

The checklist's landing page section (13) and several ad checks (final URLs, message
match, offers, claims) can only be judged by reading the page the ad sends people to.
This fetches those pages, read only, and boils each down to facts the reviewer can
quote: status, redirects, whether gclid and UTM parameters survive them, the title,
headings and visible text, forms, phone numbers, the privacy link, a consent banner,
tags found, and what robots.txt says about AdsBot.

Fetching goes through tracker/event_intel_http.public_get, which only reaches public
addresses on ports 80 and 443 and re-checks every redirect, so a landing page URL that
points inside our network is refused. Pages are read for at most MAX_BYTES, a few at a
time, with short timeouts; nothing is sent but a GET with a test gclid and UTM.
"""
from __future__ import annotations

import logging
import re
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlsplit

log = logging.getLogger(__name__)

MAX_BYTES = 600_000
MAX_PAGES = 4
TIMEOUT_S = 10
TEXT_CHARS = 3000
UA = "Mozilla/5.0 (compatible; Position2-LaunchQA/1.0; +https://position2.com)"
TEST_PARAMS = "gclid=QAcheck123&utm_source=qa_check&utm_medium=qa"
STAGING = re.compile(r"(^|[.\-])(staging|stage|dev|uat|test|qa|preview|sandbox)([.\-]|$)", re.I)
PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}(?!\d)")
CONSENT_WORDS = re.compile(r"onetrust|cookiebot|cookie-?consent|cookielaw|osano|trustarc|iubenda|termly|cookie[- ]?banner|"
                           r"accept (all )?cookies|we use cookies", re.I)
CAPTCHA = re.compile(r"recaptcha|hcaptcha|turnstile|g-recaptcha|cf-turnstile", re.I)


class _Page(HTMLParser):
    """Collects what the checks need from one HTML page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.h1, self.h2 = [], []
        self.meta = {}
        self.robots = ""
        self.viewport = ""
        self.forms = []
        self.links = []
        self.scripts = []
        self.inline = []
        self.text = []
        self._skip = 0
        self._in = None
        self._buf = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag in ("script", "style", "noscript"):
            self._skip += 1
            if tag == "script" and a.get("src"):
                self.scripts.append(a["src"])
            self._in = tag
            self._buf = []
        elif tag == "title":
            self._in, self._buf = "title", []
        elif tag in ("h1", "h2"):
            self._in, self._buf = tag, []
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name == "viewport":
                self.viewport = a.get("content", "")
            elif name == "robots":
                self.robots = a.get("content", "").lower()
            elif name in ("description", "og:title", "og:description"):
                self.meta[name] = a.get("content", "")[:300]
        elif tag == "form":
            self.forms.append({"fields": [], "action": a.get("action", "")[:200], "method": (a.get("method") or "get").lower()})
        elif tag in ("input", "textarea", "select") and self.forms:
            kind = a.get("type", "text") if tag == "input" else tag
            if kind not in ("hidden", "submit", "button"):
                self.forms[-1]["fields"].append((a.get("name") or a.get("id") or kind)[:40])
        elif tag == "a" and a.get("href"):
            self.links.append((a["href"], ""))
            self._in, self._buf = "a", []

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = max(0, self._skip - 1)
            if tag == "script" and self._buf and len(self.inline) < 12:
                self.inline.append("".join(self._buf)[:4000])
            self._in = None
        elif tag == "title" and self._in == "title":
            self.title = " ".join("".join(self._buf).split())[:200]
            self._in = None
        elif tag in ("h1", "h2") and self._in == tag:
            t = " ".join("".join(self._buf).split())[:200]
            if t:
                (self.h1 if tag == "h1" else self.h2).append(t)
            self._in = None
        elif tag == "a" and self._in == "a":
            t = " ".join("".join(self._buf).split())[:80]
            if self.links:
                self.links[-1] = (self.links[-1][0], t)
            self._in = None

    def handle_data(self, data):
        if self._in in ("script",):
            self._buf.append(data)
            return
        if self._skip:
            return
        if self._in is not None:
            self._buf.append(data)
        if data.strip():
            self.text.append(data.strip())


def parse_page(html, final_url=""):
    """Facts from one page's HTML."""
    p = _Page()
    try:
        p.feed(html)
        p.close()
    except Exception as e:                                  # noqa: BLE001 (broken HTML still has facts in it)
        log.info("qa web: html parse stopped early: %s", e)
    text = " ".join(" ".join(p.text).split())
    low = html.lower()
    tel = sorted({h.split(":", 1)[1].split("?")[0].strip() for h, _ in p.links if h.lower().startswith("tel:")})
    phones = sorted(set(PHONE.findall(text)))[:6]
    privacy = next(((h, t) for h, t in p.links if "privacy" in (h + " " + t).lower()), None)
    gtm = sorted(set(re.findall(r"GTM-[A-Z0-9]{4,}", html)))[:4]
    gtag = sorted(set(re.findall(r"\b(AW-\d{6,}|G-[A-Z0-9]{6,}|UA-\d{4,}-\d+)\b", html)))[:6]
    other = [n for n, pat in (("Meta pixel", r"fbq\(|connect\.facebook\.net"), ("LinkedIn Insight", r"snap\.licdn\.com|_linkedin_partner_id"),
                              ("HubSpot", r"js\.hs-scripts\.com|hs-analytics"), ("Microsoft Ads UET", r"bat\.bing\.com"))
             if re.search(pat, html)]
    return {
        "title": p.title, "h1": p.h1[:3], "h2": p.h2[:6], "metaDescription": p.meta.get("description", ""),
        "viewport": p.viewport, "noindex": "noindex" in p.robots,
        "text": text[:TEXT_CHARS], "textChars": len(text),
        "forms": [{"fields": f["fields"][:10], "method": f["method"]} for f in p.forms[:4]],
        "hasCaptcha": bool(CAPTCHA.search(html)),
        "telLinks": tel[:4], "phonesInText": phones,
        "privacyLink": {"href": privacy[0][:200], "text": privacy[1]} if privacy else None,
        "consentBanner": bool(CONSENT_WORDS.search(html)) or bool(CONSENT_WORDS.search(" ".join(p.scripts))),
        "googleTagManager": gtm, "googleTags": gtag, "otherTags": other,
        "pageHost": urlsplit(final_url).netloc,
    }


def _get(fetcher, url, headers=None):
    resp = fetcher(url, timeout=TIMEOUT_S, headers=dict({"User-Agent": UA, "Accept": "text/html,*/*;q=0.8"}, **(headers or {})))
    try:
        raw = resp.raw.read(MAX_BYTES + 1, decode_content=True) if hasattr(resp.raw, "read") else resp.content
        status, final, hdrs = resp.status_code, resp.url, dict(resp.headers)
        enc = resp.encoding or "utf-8"
    finally:
        resp.close()
    html = raw[:MAX_BYTES].decode(enc, errors="replace")
    return status, final, hdrs, html, len(raw)


def _with_test_params(url):
    sep = "&" if urlsplit(url).query else "?"
    return url + sep + TEST_PARAMS


def _survived(final):
    keys = {k for k, _ in parse_qsl(urlsplit(final).query)}
    return "gclid" in keys and "utm_source" in keys


def robots_rules(host, scheme, fetcher, path="/"):
    """What robots.txt says about AdsBot and Googlebot for this path."""
    out = {"fetched": False, "adsbotAllowed": None, "googlebotAllowed": None, "wildcardAllowed": None}
    try:
        status, _final, _h, body, _n = _get(fetcher, "%s://%s/robots.txt" % (scheme, host))
    except Exception as e:                                  # noqa: BLE001
        out["error"] = str(e)[:120]
        return out
    if status != 200 or "<html" in body[:400].lower():
        out["note"] = "no robots.txt (status %s), so nothing is blocked" % status
        out.update(fetched=True, adsbotAllowed=True, googlebotAllowed=True, wildcardAllowed=True)
        return out
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(body.splitlines())
    url = "%s://%s%s" % (scheme, host, path)
    out.update(fetched=True, adsbotAllowed=rp.can_fetch("AdsBot-Google", url), googlebotAllowed=rp.can_fetch("Googlebot", url),
               wildcardAllowed=rp.can_fetch("*", url))
    return out


def audit_page(url, fetcher=None):
    """Everything known about one landing page, or {'url', 'error'} if it could not be read."""
    if fetcher is None:
        from .event_intel_http import public_get as fetcher
    out = {"url": url}
    parts = urlsplit(url)
    out["https"] = parts.scheme == "https"
    out["stagingHost"] = bool(STAGING.search(parts.hostname or ""))
    t0 = time.monotonic()
    try:
        status, final, hdrs, html, size = _get(fetcher, _with_test_params(url))
        if status >= 400:                                   # a junk parameter should not 404 a page; ask plainly to be sure
            status2, final2, hdrs2, html2, size2 = _get(fetcher, url)
            if status2 < 400:
                status, final, hdrs, html, size = status2, final2, hdrs2, html2, size2
                out["paramsSurvive"] = None
        else:
            out["paramsSurvive"] = _survived(final)
    except Exception as e:                                  # noqa: BLE001 (private address, DNS, timeout, TLS)
        out["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
        return out
    out.update(status=status, finalUrl=final.split("?")[0], redirected=final.split("?")[0].rstrip("/") != url.split("?")[0].rstrip("/"),
               loadMs=int((time.monotonic() - t0) * 1000), bytes=size,
               xRobots=(hdrs.get("X-Robots-Tag") or hdrs.get("x-robots-tag") or "")[:80])
    if status < 400 and "text/html" in (hdrs.get("Content-Type") or hdrs.get("content-type") or "text/html").lower():
        out.update(parse_page(html, final))
        if "noindex" in out["xRobots"].lower():
            out["noindex"] = True
    fparts = urlsplit(final)
    out["robots"] = robots_rules(fparts.netloc, fparts.scheme, fetcher, fparts.path or "/")
    return out


def audit_pages(urls, fetcher=None, max_pages=MAX_PAGES):
    """Audit up to `max_pages` distinct landing pages, a few at a time."""
    seen, picked = set(), []
    for u in urls:
        key = u.split("#")[0]
        if key not in seen and urlsplit(key).scheme in ("http", "https"):
            seen.add(key)
            picked.append(key)
        if len(picked) >= max_pages:
            break
    if not picked:
        return []
    with ThreadPoolExecutor(max_workers=min(4, len(picked))) as ex:
        return list(ex.map(lambda u: audit_page(u, fetcher), picked))
