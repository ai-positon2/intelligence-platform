"""Paid Marketing Agents: Landing Page & Relevancy Check, a landing page as a visitor's browser first receives it.

One plain GET per page (two more only when the address redirects, to see whether the click id survives, and one robots.txt per site), made through
tracker/event_intel_http.public_get, which only reaches public addresses on ports 80 and 443 and checks every redirect again, so an address that
points inside our network is refused. The page's HTML is read (never run): what it says, how it is built, how long the server took, and what it
asks a visitor to do. Nothing a script adds after load is seen, and every finding built on this says so: a page whose words are put there by
scripts has few words here and is reported as such, not failed.

Some sites refuse automated requests with 401, 403 or 429 while serving real visitors and Google's crawler normally. Those pages are `blocked`, not broken.

`parse(html)` takes text and returns facts, `fetch(url)` returns the whole record for one address, `fetch_many(urls)` does a list of them a few at a time
inside a time budget. No database.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import urllib.robotparser
from concurrent.futures import ThreadPoolExecutor, wait
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlsplit

from . import paid_audit_pages as pages
from . import paid_site_tech as site_tech
from .paid_qa_web import CONSENT_WORDS, PHONE
from .paid_strategy_research import registrable

log = logging.getLogger(__name__)

MAX_BYTES = 800_000                               # of one page's HTML that is read; more is cut and the record says so
TEXT_CHARS = 12_000                               # of the page's visible text that is kept
TIMEOUT_S = 8                                     # per request
READ_BUDGET_S = 12                                # to read one body, however slowly it arrives
BUDGET_S = 70                                     # for the whole list of pages (the rest are reported as not read in time)
WORKERS = 6
ATF_CHARS = 1500                                  # visible characters from the top that stand in for "above the fold"
UA = "Mozilla/5.0 (compatible; Position2LandingPageCheck/1.0; +https://intelligence.position2.com)"
TEST_PARAMS = "gclid=LPcheck123&utm_source=lp_check"
BLOCKED = pages.BLOCKED
RETRY_PAUSE_S = 0.5                                 # wait before a page that timed out or answered with a server error is asked again
SKIP_BLOCK = frozenset(("script", "style", "noscript", "template", "svg", "select"))      # whose text is never part of what a visitor reads
CAP_TEXT = 400                                    # characters kept of the text of a link, button or heading

STRONG_CTA = re.compile(r"^(?:book|schedule|call|get|request|contact|buy|shop|order|add to (?:cart|bag|basket)|sign up|subscribe|download|start|try|register|apply|reserve|"
                        r"claim|enroll|donate|see pricing|view pricing|talk to|speak (?:to|with)|text us|find (?:a|your)|check (?:availability|price|rates)|"
                        r"schedule|make an appointment|free (?:quote|estimate|consultation))\b", re.I)
WEAK_CTA = re.compile(r"^(?:learn more|read more|find out|see more|view (?:all|more)|discover|explore)\b", re.I)
SOCIAL = ("facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com", "youtube.com", "youtu.be", "tiktok.com", "pinterest.com", "yelp.com",
          "google.com", "goo.gl", "maps.apple.com", "g.page", "apple.com", "wa.me", "t.me")
FORM_EMBEDS = ("hsforms", "hubspot", "typeform", "jotform", "calendly", "acuityscheduling", "formstack", "wufoo", "cognitoforms", "docs.google.com/forms",
               "forms.gle", "tally.so", "paperform", "zohopublic", "zohoforms", "123formbuilder", "pipedrive", "leadconnector", "gohighlevel", "msgsndr", "nexhealth",
               "zocdoc", "setmore", "squareup", "simplybook", "calendar.app.google", "marketo", "pardot", "formassembly", "wpforms", "gravityforms", "podium", "birdeye")
PROOF = [
    ("reviews", r"\breviews?\b|\btestimonials?\b|\bwhat (?:our )?(?:clients|customers|patients) say\b"),
    ("star rating", r"[\u2605\u2b50]|\b\d(?:\.\d)?\s?(?:/\s?5|out of 5|stars?)\b|\b5[- ]star\b"),
    ("awards", r"\baward(?:s|-winning)?\b|\bbest of\b|\btop rated\b|\bwinner\b"),
    ("credentials", r"\baccredited\b|\bcertified\b|\blicensed\b|\binsured\b|\bbbb\b|\bboard[- ]certified\b"),
    ("guarantee", r"\bguarantee[ds]?\b|\bmoney[- ]back\b"),
    ("experience", r"\b\d{1,3}\+?\s+years\b|\bsince (?:19|20)\d\d\b|\bestablished in\b"),
    ("clients", r"\btrusted by\b|\bas seen (?:on|in)\b|\bcase stud(?:y|ies)\b|\bour clients\b"),
]
_PROOF = [(n, re.compile(p, re.I)) for n, p in PROOF]
ADDRESS = re.compile(r"\b\d{1,5}\s+(?:[A-Z0-9][A-Za-z0-9.'-]*\s+){1,4}(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Court|Ct|Place|Pl|Highway|Hwy|"
                     r"Parkway|Pkwy|Square|Sq|Suite|Ste)\b\.?")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
APP_ROOTS = frozenset(("root", "__next", "app", "__nuxt", "___gatsby"))
SCRIPT_BUILT_WORDS = 80                            # a page with fewer visible words than this and an app shell or many scripts is built by scripts
SCRIPT_BUILT_SCRIPTS = 6
SEARCH_NAMES = frozenset(("q", "s", "search", "query", "keyword", "keywords", "term", "k"))


def _clip(text, n):
    return " ".join(str(text or "").split())[:n]


class _Reader(HTMLParser):
    """Collects what the checks need from one page. Never raises on odd markup: the caller catches whatever HTMLParser itself throws."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.lang, self.viewport, self.robots, self.description = "", "", "", "", ""
        self.h1_count = 0
        self.h1, self.h2, self.h3 = [], [], []
        self.forms, self.loose = [], []
        self.embeds = []
        self.ctas, self.tel, self.mailto = [], [], []
        self.links = {"total": 0, "nav": 0, "outbound": 0}
        self.privacy = False
        self.images = {"count": 0, "noSize": 0, "lazy": 0, "noAlt": 0}
        self.head = {"blockingScripts": 0, "stylesheets": 0}
        self.scripts, self.inline = [], []
        self.text = []
        self.chars = 0
        self._skip = 0
        self._in_head = False
        self._ctx = {"nav": 0, "header": 0, "footer": 0, "aside": 0}
        self._caps = []                           # the open elements whose text is being collected: [tag, pieces, attrs, offset, characters kept, limit]
        self._form = None
        self._radio = set()
        self.host = ""
        self.app_root = False

    # -- elements ------------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "head":
            self._in_head = True
        elif tag == "body":
            self._in_head = False
        elif tag == "html":
            self.lang = a.get("lang", "")[:12]
        if tag in self._ctx:
            self._ctx[tag] += 1
        if tag in SKIP_BLOCK:
            self._skip += 1
        if a.get("id") in APP_ROOTS or "ng-version" in a or "data-reactroot" in a:
            self.app_root = True
        if tag == "script":
            self._script(a)
            self._caps.append([tag, [], a, 0, 0, 4000])
        elif tag == "title":
            self._caps.append([tag, [], a, 0, 0, CAP_TEXT])
        elif tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            if name == "viewport":
                self.viewport = a.get("content", "")
            elif name == "robots":
                self.robots = a.get("content", "").lower()
            elif name == "description":
                self.description = _clip(a.get("content"), 300)
        elif tag == "link":
            if "stylesheet" in a.get("rel", "").lower() and self._in_head:
                self.head["stylesheets"] += 1
        elif tag in ("h1", "h2", "h3"):
            if tag == "h1":
                self.h1_count += 1
            self._caps.append([tag, [], a, self.chars, 0, CAP_TEXT])
        elif tag == "a":
            self._anchor(a)
            self._caps.append([tag, [], a, self.chars, 0, CAP_TEXT])
        elif tag == "button":
            self._caps.append([tag, [], a, self.chars, 0, CAP_TEXT])
        elif tag == "form":
            self._form = {"fields": [], "action": a.get("action", "")[:200], "method": (a.get("method") or "get").lower(), "search": a.get("role", "").lower() == "search"
                          or "search" in a.get("action", "").lower() or "search" in a.get("class", "").lower() or "search" in a.get("id", "").lower()}
            self.forms.append(self._form)
            self._radio = set()
        elif tag in ("input", "textarea"):
            self._field(tag, a)
        elif tag == "iframe":
            src = a.get("src") or a.get("data-src") or ""
            if any(k in src.lower() for k in FORM_EMBEDS):
                host = urlsplit(src if "//" in src else "//" + src).netloc
                if host and host not in self.embeds:
                    self.embeds.append(host)
        elif tag == "img":
            self._image(a)

    def handle_endtag(self, tag):
        if tag in self._ctx and self._ctx[tag] > 0:
            self._ctx[tag] -= 1
        if tag in SKIP_BLOCK:
            self._skip = max(0, self._skip - 1)
        if tag == "head":
            self._in_head = False
        if tag == "form":
            self._form = None
        for i in range(len(self._caps) - 1, -1, -1):
            if self._caps[i][0] == tag:
                cap = self._caps[i]
                del self._caps[i:]                     # whatever was opened inside it and never closed ends with it
                self._finish(tag, " ".join("".join(cap[1]).split()), cap)
                break

    def _finish(self, tag, text, cap):
        if tag == "title":
            self.title = text[:200]
        elif tag == "script":
            if not cap[2].get("src") and "".join(cap[1]).strip() and len(self.inline) < 12:
                self.inline.append("".join(cap[1])[:4000])
        elif tag in ("h1", "h2", "h3"):
            if text:
                bucket = {"h1": self.h1, "h2": self.h2, "h3": self.h3}[tag]
                if len(bucket) < 8:
                    bucket.append(text[:200])
        elif tag in ("a", "button"):
            self._cta(tag, text, cap[2], cap[3])

    def handle_startendtag(self, tag, attrs):
        if tag not in ("script", "title", "a", "button", "h1", "h2", "h3", "form", "select", "svg"):     # a self-closed void tag; an odd self-closed block opens nothing
            self.handle_starttag(tag, attrs)

    def handle_data(self, data):
        for cap in self._caps:
            if cap[4] < cap[5]:
                cap[1].append(data)
                cap[4] += len(data)
        if self._skip or self._in_head or (self._caps and self._caps[-1][0] in ("title", "script")):
            return
        t = data.strip()
        if t:
            self.text.append(t)
            self.chars += len(t) + 1

    # -- parts ---------------------------------------------------------------
    def _script(self, a):
        src = a.get("src", "")
        if not src:
            return
        self.scripts.append(src)
        if self._in_head and not ("async" in a or "defer" in a or a.get("type", "").lower() == "module" or "nomodule" in a):
            self.head["blockingScripts"] += 1

    def _anchor(self, a):
        href = a.get("href", "").strip()
        if not href or href.startswith("#") or href.lower().startswith("javascript:"):
            return
        low = href.lower()
        if low.startswith("tel:"):
            n = href.split(":", 1)[1].split("?")[0].strip()
            if n and n not in self.tel:
                self.tel.append(n)
            return
        if low.startswith("mailto:"):
            n = href.split(":", 1)[1].split("?")[0].strip()
            if n and n not in self.mailto:
                self.mailto.append(n)
            return
        if low.startswith(("sms:", "whatsapp:")):
            return
        self.links["total"] += 1
        if self._ctx["nav"] or self._ctx["header"]:
            self.links["nav"] += 1
        host = urlsplit(href).netloc.lower() if "//" in href else ""
        if host and self.host and registrable(host) != registrable(self.host) and not any(host == s or host.endswith("." + s) for s in SOCIAL):
            self.links["outbound"] += 1
        if "privacy" in low:
            self.privacy = True

    def _cta(self, tag, text, a, at):
        if tag == "a" and "privacy" in text.lower():
            self.privacy = True
        label = text[:60]
        if not label or len(label) > 45 or label.lower().startswith("skip"):
            return
        kind = "strong" if STRONG_CTA.match(label) or (tag == "a" and a.get("href", "").lower().startswith("tel:")) else "weak" if WEAK_CTA.match(label) else None
        cls = (a.get("class", "") + " " + a.get("role", "")).lower()
        if kind is None and tag == "a" and any(k in cls for k in ("btn", "button", "cta")) and len(label) >= 3:
            kind = "weak" if not STRONG_CTA.match(label) else "strong"
        if kind is None and tag == "button" and (a.get("type", "").lower() == "submit" or (not a.get("type") and self._form is not None)):
            kind = "strong"                                 # the button that sends a form; a menu or a slider button outside one is not a call to action
        if kind and len(self.ctas) < 40:
            self.ctas.append({"text": label, "kind": kind, "at": int(at), "tag": tag})

    def _field(self, tag, a):
        kind = a.get("type", "text").lower() if tag == "input" else "textarea"
        if kind in ("submit", "button", "image", "reset"):
            if kind == "submit" and a.get("value"):
                self._cta("button", a["value"], dict(a, type="submit"), self.chars)
            return
        if kind == "hidden" or "hidden" in a or HIDDEN_STYLE.search(a.get("style", "")) or a.get("aria-hidden") == "true" or (a.get("tabindex") == "-1" and kind != "checkbox"):
            return
        name = (a.get("name") or a.get("id") or "")[:40]
        if kind == "radio":
            if name in self._radio:
                return
            self._radio.add(name)
        field = {"kind": kind, "name": name, "required": "required" in a or a.get("aria-required") == "true",
                 "label": _clip(a.get("placeholder") or a.get("aria-label"), 40)}
        (self._form["fields"] if self._form is not None else self.loose).append(field)

    def _image(self, a):
        self.images["count"] += 1
        sized = (a.get("width") and a.get("height")) or "aspect-ratio" in a.get("style", "").lower() or (
            "width" in a.get("style", "").lower() and "height" in a.get("style", "").lower())
        if not sized:
            self.images["noSize"] += 1
        if a.get("loading", "").lower() == "lazy" or any(k.startswith("data-") and ("lazy" in k or k in ("data-src", "data-srcset")) for k in a):
            self.images["lazy"] += 1
        if "alt" not in a:
            self.images["noAlt"] += 1


def _script_host(src):
    """The site a script is loaded from, '' for one on the page's own site (a relative address)."""
    return urlsplit(src).netloc.lower() if src.startswith(("http://", "https://", "//")) else ""


def _form_facts(forms, loose):
    out = []
    for f in forms:
        fields = f["fields"]
        boxes = [x for x in fields if x["kind"] == "checkbox"]
        visible = [x for x in fields if x["kind"] != "checkbox"]
        kinds = {x["kind"] for x in visible}
        names = " ".join(x["name"].lower() for x in visible)
        search = f["search"] or (len(visible) == 1 and (visible[0]["name"].lower() in SEARCH_NAMES or visible[0]["kind"] == "search"))
        out.append({"fields": [dict(x) for x in fields[:30]], "visible": len(visible), "required": sum(1 for x in visible if x["required"]), "checks": len(boxes),
                    "search": bool(search), "action": f["action"], "method": f["method"], "phone": "tel" in kinds or "phone" in names, "email": "email" in kinds or "mail" in names})
    if sum(1 for x in loose if x["kind"] != "checkbox") >= 2:
        visible = [x for x in loose if x["kind"] != "checkbox"]
        out.append({"fields": [dict(x) for x in loose[:30]], "visible": len(visible), "required": sum(1 for x in visible if x["required"]),
                    "checks": len(loose) - len(visible), "search": False, "action": "", "method": "", "phone": any(x["kind"] == "tel" for x in visible),
                    "email": any(x["kind"] == "email" for x in visible), "loose": True})
    return out[:8]


def parse(html, final_url=""):
    """{'facts': what the page says and offers, 'tech': tags, mobile viewport and schema} from one page's HTML. Broken HTML still has facts in it."""
    r = _Reader()
    r.host = urlsplit(final_url).netloc
    try:
        r.feed(html)
        r.close()
    except Exception as e:                                  # noqa: BLE001
        log.info("landing page check: html parse stopped early: %s", e)
    text = " ".join(" ".join(r.text).split())
    low = text.lower()
    phones = sorted(set(PHONE.findall(text)))[:6]
    tech = site_tech.detect(r, final_url)
    blob = " ".join(r.inline)
    proof = [n for n, rx in _PROOF if rx.search(low)]
    if "AggregateRating" in tech["schema"] or "Review" in tech["schema"]:
        proof.append("rating markup")
    forms = _form_facts(r.forms, r.loose)
    booking = any(t["name"] == "Online booking" for t in tech["tools"])
    commerce = any(re.match(r"add to (?:cart|bag|basket)|buy now|checkout", c["text"], re.I) for c in r.ctas)
    facts = {
        "title": r.title, "metaDescription": r.description, "viewport": r.viewport, "noindex": "noindex" in r.robots, "lang": r.lang,
        "h1": r.h1[:3], "h1Count": r.h1_count, "h2": r.h2[:8], "h3": r.h3[:8],
        "text": text[:TEXT_CHARS], "wordCount": len(text.split()), "forms": forms, "embeds": r.embeds[:6], "booking": booking, "commerce": commerce,
        "ctas": r.ctas[:20], "tel": r.tel[:4], "mailto": r.mailto[:3], "phones": phones, "address": bool(ADDRESS.search(text)) or "PostalAddress" in tech["schema"] or '"streetAddress"' in blob,
        "emails": sorted(set(EMAIL.findall(text)))[:3], "links": dict(r.links), "images": dict(r.images),
        "head": {"blockingScripts": r.head["blockingScripts"], "stylesheets": r.head["stylesheets"], "scripts": len(r.scripts),
                 "thirdParty": sorted({h for h in map(_script_host, r.scripts) if h and registrable(h) != registrable(r.host)})[:40]},
        "appRoot": r.app_root, "proof": proof[:8], "privacyLink": r.privacy, "consent": bool(CONSENT_WORDS.search(html[:200000])) or bool(CONSENT_WORDS.search(" ".join(r.scripts))),
    }
    facts["scriptBuilt"] = facts["wordCount"] < SCRIPT_BUILT_WORDS and (r.app_root or facts["head"]["scripts"] >= SCRIPT_BUILT_SCRIPTS)
    return {"facts": facts, "tech": tech}


# ── Fetching ────────────────────────────────────────────────────────────────

def _read(resp, budget_s=READ_BUDGET_S, limit=MAX_BYTES):
    """(bytes read, truncated) from a response, in pieces, within a time budget."""
    raw = getattr(resp, "raw", None)
    if raw is None or not hasattr(raw, "read"):
        body = getattr(resp, "content", b"") or b""
        return body[:limit], len(body) > limit
    out, size, t0 = [], 0, time.monotonic()
    while size <= limit:
        chunk = raw.read(65536, decode_content=True)
        if not chunk:
            break
        out.append(chunk)
        size += len(chunk)
        if time.monotonic() - t0 > budget_s:
            break
    data = b"".join(out)
    return data[:limit], len(data) > limit


def _headers(resp):
    try:
        return {str(k).lower(): str(v) for k, v in dict(resp.headers).items()}
    except Exception:                                       # noqa: BLE001
        return {}


def _decode(raw, hdrs):
    ct = hdrs.get("content-type", "")
    m = re.search(r"charset=([\w-]+)", ct, re.I)
    for enc in ([m.group(1)] if m else []) + ["utf-8"]:
        try:
            return raw.decode(enc, errors="replace")
        except LookupError:
            continue
    return raw.decode("utf-8", errors="replace")


def _once(url, fetcher, html_only=True):
    """One GET: (status, final url, headers, html or None, bytes, truncated, ms to the first answer, ms in all, error). Never raises."""
    t0 = time.monotonic()
    try:
        resp = fetcher(url, timeout=TIMEOUT_S, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8", "Accept-Encoding": "gzip, deflate"})
    except Exception as e:                                  # noqa: BLE001 (a private address, DNS, a timeout, TLS)
        return 0, "", {}, None, 0, False, 0, int((time.monotonic() - t0) * 1000), pages._say(e)
    first = int((time.monotonic() - t0) * 1000)
    try:
        status, final, hdrs = int(resp.status_code), str(getattr(resp, "url", "") or url), _headers(resp)
        html, size, cut, err = None, 0, False, ""
        if status < 400 and (not html_only or "html" in (hdrs.get("content-type") or "text/html").lower()):
            try:
                raw, cut = _read(resp)
                size, html = len(raw), _decode(raw, hdrs)
            except Exception as e:                          # noqa: BLE001 (the connection dropped while the body arrived)
                err = pages._say(e)
        return status, final, hdrs, html, size, cut, first, int((time.monotonic() - t0) * 1000), err
    finally:
        resp.close()


def _challenged(hdrs):
    """True when a response is a bot check ("just a moment") rather than the site's own answer. Cloudflare marks one with cf-mitigated: challenge, whatever the status."""
    return str((hdrs or {}).get("cf-mitigated", "")).lower() == "challenge"


def _where(url):
    """(scheme, host, path) of an address, for telling whether a request ended somewhere else: http against https, the apex against www and a changed path all count, a trailing slash, a default port, a fragment and the query do not."""
    p = urlsplit(str(url or ""))
    scheme, host = p.scheme.lower(), p.netloc.lower().rsplit("@", 1)[-1]
    if host.endswith(":80" if scheme == "http" else ":443"):
        host = host.rsplit(":", 1)[0]
    return scheme, host, p.path.rstrip("/") or "/"


def _survives(final):
    keys = {k for k, _ in parse_qsl(urlsplit(final).query)}
    return "gclid" in keys and "utm_source" in keys


def fetch(url, fetcher=None, robots=None):
    """The record for one landing page address. `robots` is a function (url) -> True/False/None for whether AdsBot may fetch it (None: not known)."""
    if fetcher is None:
        from .event_intel_http import public_get as fetcher
    out = {"url": url, "status": 0, "error": "", "blocked": False, "ms": 0, "responseMs": 0, "bytes": 0, "truncated": False, "encoding": "", "contentType": "",
           "finalUrl": "", "redirected": False, "collapsed": False, "retried": False, "isHtml": False, "facts": None, "tech": None, "paramsSurvive": None, "adsbot": None}
    for attempt in (0, 1):
        status, final, hdrs, html, size, cut, first, total, err = _once(url, fetcher)
        out.update(status=status, finalUrl=final, error=err, ms=total, responseMs=first, bytes=size, truncated=cut,
                   encoding=hdrs.get("content-encoding", "")[:20], contentType=hdrs.get("content-type", "")[:60])
        if attempt == 0 and (err == pages.TIMED_OUT or (500 <= status < 600 and not _challenged(hdrs))):      # one slow or failed answer is asked again before it is held against the page
            out["retried"] = True
            time.sleep(RETRY_PAUSE_S)
            continue
        break
    out["blocked"] = out["status"] in BLOCKED or _challenged(hdrs)
    out["redirected"] = bool(out["finalUrl"]) and _where(out["finalUrl"]) != _where(url)
    if out["redirected"]:
        a, b = urlsplit(url), urlsplit(out["finalUrl"])
        out["collapsed"] = (b.path or "/") == "/" and (a.path or "/") != "/"
    if html is not None and out["status"] < 400:
        out["isHtml"] = True
        parsed = parse(html, out["finalUrl"] or url)
        out["facts"], out["tech"] = parsed["facts"], parsed["tech"]
        out["tech"]["https"] = (out["finalUrl"] or url).lower().startswith("https://")
        if "noindex" in hdrs.get("x-robots-tag", "").lower():
            out["facts"]["noindex"] = True
    if out["redirected"] and out["status"] and out["status"] < 400:
        sep = "&" if urlsplit(url).query else "?"
        s2, f2, _h, _html, _n, _c, _a, _t, e2 = _once(url + sep + TEST_PARAMS, fetcher)
        out["paramsSurvive"] = _survives(f2) if (s2 and s2 < 400 and not e2) else None
    if robots is not None:
        try:
            out["adsbot"] = robots(out["finalUrl"] or url)
        except Exception:                                   # noqa: BLE001 (robots.txt never costs the page)
            out["adsbot"] = None
    return out


def make_robots(fetcher=None):
    """A function (url) -> whether AdsBot-Google may fetch it, True, False or None (could not tell). robots.txt is read once per site. AdsBot ignores
    the rules written for every crawler ('*') and obeys only the ones that name it, so a site with no AdsBot rule is open to it."""
    if fetcher is None:
        from .event_intel_http import public_get as fetcher
    cache, lock = {}, threading.Lock()

    def rules(parts):
        key = (parts.scheme, parts.netloc)
        with lock:                                           # one reader per site, the others wait for its answer
            if key not in cache:
                body = None
                for _try in range(2):                        # a request that fails is made once more: one bad moment must not leave a whole site unchecked
                    status, _f, _h, text, _n, _c, _a, _t, err = _once("%s://%s/robots.txt" % key, fetcher, html_only=False)
                    if err == "" and status == 200 and text is not None and "<html" not in text[:400].lower():
                        body = text
                        break
                    if err == "" and 400 <= status < 500:
                        body = ""                            # no robots.txt: nothing is blocked
                        break
                cache[key] = body
            return cache[key]

    def allowed(url):
        parts = urlsplit(url)
        body = rules(parts)
        if body is None:
            return None
        if not re.search(r"(?im)^\s*user-agent\s*:\s*adsbot-google\b", body):
            return True
        rp = urllib.robotparser.RobotFileParser()
        rp.parse(body.splitlines())
        return bool(rp.can_fetch("AdsBot-Google", (parts.path or "/") + ("?" + parts.query if parts.query else "")))
    return allowed


def fetch_many(urls, fetcher=None, *, workers=WORKERS, budget_s=BUDGET_S):
    """[record] for each address, in order, a few at a time. An address not read inside the time budget is {'url', 'error': 'not read in time', 'skipped': True}."""
    urls = list(urls)
    if not urls:
        return []
    robots = make_robots(fetcher)
    ex = ThreadPoolExecutor(max_workers=max(1, min(workers, len(urls))))
    futures = [ex.submit(fetch, u, fetcher, robots) for u in urls]
    done, _pending = wait(futures, timeout=budget_s)
    ex.shutdown(wait=False, cancel_futures=True)
    out = []
    for u, f in zip(urls, futures):
        if f in done and not f.cancelled() and f.exception() is None:
            out.append(f.result())
        else:
            out.append({"url": u, "status": 0, "error": "not read in time" if f not in done else "could not be read", "skipped": f not in done, "blocked": False,
                        "isHtml": False, "facts": None, "tech": None})
    return out
