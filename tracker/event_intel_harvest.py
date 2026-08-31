"""Step 2 (HARVEST) for Event & Conference Intelligence.

Fetches the participant pages resolve_event() found and turns each into rows,
recording the outcome of EVERY page it tried, including the ones it could not
read.

Why the source ledger is not optional: a roster built from four of an event's
seven published pages looks exactly like a complete roster, only shorter.
Nothing in the output distinguishes "this event has 40 exhibitors" from "we
read 40 of 300 because three pages were JavaScript-rendered". Recording each
attempt with its status is what makes the difference visible.

Link preservation is the other load-bearing choice. Exhibitor directories
almost always link each exhibitor to its own website, and that link is the
ONLY trustworthy way to get a company's domain. The alternative -- deriving a
domain from a company name -- is exactly the defect already logged against
Contact Finder's `_cpi_probe_company_free`, which guesses `.com` and is
wrong for every company that is not a .com. So the HTML is flattened to text
with anchors kept as `label [href]`, and a participant with no published link
gets `org_domain: None` rather than an invention.

No new dependency: stdlib html.parser, since adding bs4/lxml would change the
Railway build for one extractor.
"""

from __future__ import annotations

import html as _html
import logging
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

from . import claude_websearch
from .event_intel_store import (SOURCE_BLOCKED, SOURCE_ERROR, SOURCE_NOT_FOUND,
                                SOURCE_OK, ROLES)

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (compatible; Position2-Intelligence/1.0; "
       "+https://intelligence.position2.com)")
_TIMEOUT = 20
_MAX_BYTES = 3_000_000
# What we hand the model. Past this, the page is truncated and the source row
# says so, because a silently clipped exhibitor list undercounts an event.
_MAX_CHARS = 55_000
# Below this much readable text, the page almost certainly rendered its list
# client-side. Reported as blocked, never as "this event has no exhibitors".
_MIN_USEFUL_CHARS = 400

_SKIP_TAGS = {"script", "style", "noscript", "svg", "head", "template", "iframe"}
_BLOCK_TAGS = {"p", "div", "li", "tr", "br", "h1", "h2", "h3", "h4", "h5", "h6",
               "section", "article", "td", "th", "ul", "ol", "table", "header",
               "footer", "nav", "dt", "dd"}


class _LinkedText(HTMLParser):
    """Flatten HTML to text, keeping anchor targets inline as `label [href]`."""

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.parts: list[str] = []
        self._skip = 0
        self._href: str | None = None
        self._a_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "a":
            href = dict(attrs).get("href") or ""
            self._href = href.strip() or None
            self._a_text = []
        elif tag == "img" and not self._skip:
            alt = (dict(attrs).get("alt") or "").strip()
            if alt:
                # Exhibitor grids are frequently nothing but logo images, and
                # the company name lives only in the alt text.
                self.parts.append(alt + " ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "a":
            label = "".join(self._a_text).strip()
            href = self._href
            self._href, self._a_text = None, []
            if label:
                if href and not href.lower().startswith(("javascript:", "data:", "#", "mailto:")):
                    try:
                        absolute = urljoin(self.base_url, href)
                    except Exception:
                        absolute = href
                    self.parts.append("%s [%s]" % (label, absolute))
                else:
                    self.parts.append(label)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._skip:
            return
        if self._href is not None:
            self._a_text.append(data)
        else:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = _html.unescape(raw)
        raw = re.sub(r"[ \t\r\f\v]+", " ", raw)
        raw = re.sub(r"\n\s*\n\s*\n+", "\n\n", raw)
        lines = [ln.strip() for ln in raw.split("\n")]
        return "\n".join(ln for ln in lines if ln)


def html_to_linked_text(markup: str, base_url: str = "") -> str:
    """Public for tests: HTML in, `label [href]` text out."""
    p = _LinkedText(base_url)
    try:
        p.feed(markup)
        p.close()
    except Exception as e:
        logger.info("event_intel_harvest: HTML parse degraded for %s: %s", base_url, e)
    return p.text()


def fetch_page(url: str) -> dict:
    """One page fetch, classified. Never raises.

    status is one of ok / blocked / not_found / error, and `blocked` covers
    both an explicit refusal (401/403/429) and a page that returned almost no
    readable text, which in practice means its list is client-rendered. Both
    are 'we could not read this', which is what the report needs to say.
    """
    out = {"url": url, "status": SOURCE_ERROR, "http_status": None,
           "text": "", "note": "", "truncated": False}
    try:
        r = requests.get(url, timeout=_TIMEOUT, stream=True, headers={
            "User-Agent": _UA,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        })
    except requests.Timeout:
        out["note"] = "Timed out after %ss." % _TIMEOUT
        return out
    except Exception as e:
        out["note"] = "Request failed: %s" % str(e)[:200]
        return out

    out["http_status"] = r.status_code
    try:
        if r.status_code == 404:
            out["status"] = SOURCE_NOT_FOUND
            out["note"] = "Page not found (404)."
            return out
        if r.status_code in (401, 403, 429) or r.status_code >= 500:
            out["status"] = SOURCE_BLOCKED
            out["note"] = "Server refused the request (HTTP %s)." % r.status_code
            return out
        if r.status_code != 200:
            out["note"] = "Unexpected HTTP %s." % r.status_code
            return out

        ctype = (r.headers.get("Content-Type") or "").lower()
        if ctype and "html" not in ctype and "xml" not in ctype:
            out["status"] = SOURCE_BLOCKED
            out["note"] = "Not an HTML page (Content-Type: %s)." % ctype[:80]
            return out

        chunks, total = [], 0
        for chunk in r.iter_content(65536):
            chunks.append(chunk)
            total += len(chunk)
            if total >= _MAX_BYTES:
                break
        markup = b"".join(chunks).decode(r.encoding or "utf-8", errors="replace")
    finally:
        r.close()

    text = html_to_linked_text(markup, url)
    if len(text) < _MIN_USEFUL_CHARS:
        out["status"] = SOURCE_BLOCKED
        out["note"] = ("Returned only %d characters of readable text, so its list "
                       "is almost certainly rendered by JavaScript after load."
                       % len(text))
        return out

    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS]
        out["truncated"] = True
        out["note"] = ("Page was longer than the %d-character extraction limit and "
                       "was truncated, so this list may be incomplete." % _MAX_CHARS)
    out["status"] = SOURCE_OK
    out["text"] = text
    return out


_SYSTEM = (
    "You read one page from a business event's website and extract the "
    "organisations and people it lists.\n\n"
    "The page text has been flattened from HTML. Links are preserved inline "
    "as `label [https://url]`. That URL is the only reliable source of an "
    "organisation's own domain.\n\n"
    "RULES.\n"
    "1. Extract only what the page actually lists. Never add an organisation "
    "you know attends this event but that this page does not name. Never "
    "complete a partial list from memory.\n"
    "2. `role` must describe how THIS PAGE presents them:\n"
    "   exhibitor - listed in an exhibitor or booth directory\n"
    "   sponsor - listed as a sponsor, at any tier\n"
    "   speaker - a person speaking, presenting or on a panel\n"
    "   partner - listed as a partner, supporter or association\n"
    "   media - listed as media or press partner\n"
    "   attendee_declared - the page explicitly says this specific person or "
    "company is attending, and they are not any of the above\n"
    "   Use attendee_declared ONLY for an explicit statement of attendance. "
    "An exhibitor is not an attendee. A sponsor is not an attendee. If you "
    "are unsure which role applies, omit the row entirely.\n"
    "3. `org_domain` is the registrable domain from the organisation's OWN "
    "linked website (example.com from https://www.example.com/about). Set it "
    "to null unless the page actually links to their site. NEVER derive a "
    "domain from a company name. A link to the event's own site, a social "
    "network, or a directory profile is NOT the organisation's domain.\n"
    "4. For a speaker, `person_name` is the individual and `org_name` is the "
    "employer the page gives them. If a speaker's employer is not shown, use "
    "the person's name as org_name and set person_name too.\n"
    "5. `tier` is a sponsorship or exhibitor tier exactly as printed "
    "(\"Platinum\", \"Diamond\"), else null. `booth` is a stand or booth "
    "number exactly as printed, else null.\n"
    "6. Ignore the site's own navigation, footer, cookie notice, and the "
    "event organiser itself.\n\n"
    "Respond with ONLY a JSON object:\n"
    '{"rows": [{"org_name": str, "org_domain": str|null, "role": str, '
    '"person_name": str|null, "person_title": str|null, "tier": str|null, '
    '"booth": str|null}], "note": str}\n\n'
    "`note` is one short sentence on what the page was and anything that "
    "limits the extraction, for example that it is page 1 of several. If the "
    "page lists nobody, return an empty rows array and say so in note."
)

_DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")
# Never accepted as an organisation's "own" domain: these are where a
# directory links when it does not have the company's site.
_NON_COMPANY_HOSTS = {
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com",
    "youtube.com", "tiktok.com", "crunchbase.com", "wikipedia.org", "goo.gl",
    "bit.ly", "lnkd.in", "eventbrite.com", "hopin.com", "swapcard.com",
    "medium.com", "github.com", "google.com", "maps.google.com",
}


def clean_domain(value: str | None, event_host: str = "") -> str | None:
    """Registrable-ish host from a URL or bare domain, or None.

    Rejects the event's own host and the social/directory hosts a listing
    falls back to, because either one silently turns a whole roster into rows
    that all point at the same company.
    """
    if not value:
        return None
    v = str(value).strip().lower()
    if not v:
        return None
    if "://" in v:
        try:
            v = urlparse(v).netloc
        except Exception:
            return None
    v = v.split("/")[0].split("?")[0].split("#")[0]
    if "@" in v:
        return None
    if v.startswith("www."):
        v = v[4:]
    v = v.strip(".")
    if not v or not _DOMAIN_RE.match(v):
        return None
    if event_host and (v == event_host or v.endswith("." + event_host)):
        return None
    for bad in _NON_COMPANY_HOSTS:
        if v == bad or v.endswith("." + bad):
            return None
    return v


def extract_participants(page_text: str, page_url: str, page_kind: str,
                         event_name: str, event_host: str = "") -> dict:
    """One Claude call turning a fetched page into participant rows.

    Returns {"rows": [...], "note": str, "error": {kind,detail}|None}. No web
    search here: the page text is already in hand, and letting the model
    search would let it complete the list from elsewhere, which rule 1 exists
    to forbid.
    """
    if not (page_text or "").strip():
        return {"rows": [], "note": "The page had no readable text.", "error": None}

    user = ("Event: %s\nPage kind, as classified by the previous step: %s\n"
            "Page URL: %s\n\n--- PAGE TEXT ---\n%s"
            % (event_name, page_kind, page_url, page_text))

    res = claude_websearch.ask(_SYSTEM, user, max_uses=0, max_tokens=8000, timeout=180.0)
    if res.get("error"):
        return {"rows": [], "note": "", "error": res["error"]}

    parsed = claude_websearch.extract_json(res.get("text") or "")
    if not isinstance(parsed, dict):
        return {"rows": [], "note": "",
                "error": {"kind": claude_websearch.ERR_UNPARSABLE,
                          "detail": (res.get("text") or "")[:400]}}

    rows = []
    for r in (parsed.get("rows") or []):
        if not isinstance(r, dict):
            continue
        name = str(r.get("org_name") or "").strip()
        role = str(r.get("role") or "").strip().lower()
        if not name or role not in ROLES:
            continue
        rows.append({
            "org_name": name[:200],
            "org_domain": clean_domain(r.get("org_domain"), event_host),
            "role": role,
            "person_name": (str(r.get("person_name") or "").strip() or None),
            "person_title": (str(r.get("person_title") or "").strip() or None),
            "tier": (str(r.get("tier") or "").strip() or None),
            "booth": (str(r.get("booth") or "").strip() or None),
            "source_url": page_url,
        })
    return {"rows": rows, "note": str(parsed.get("note") or "")[:400], "error": None}


def harvest_page(page: dict, event_name: str, event_host: str = "") -> dict:
    """Fetch one page and extract it. Returns a dict carrying both the source
    ledger entry and the rows, so a caller writes one and saves the other
    without needing to know how either failed."""
    url, kind = page["url"], page.get("kind") or "unknown"
    fetched = fetch_page(url)
    source = {"url": url, "kind": kind, "status": fetched["status"],
              "http_status": fetched["http_status"], "rows_found": 0,
              "note": fetched["note"]}
    if fetched["status"] != SOURCE_OK:
        return {"source": source, "rows": []}

    ext = extract_participants(fetched["text"], url, kind, event_name, event_host)
    if ext.get("error"):
        source["status"] = SOURCE_ERROR
        source["note"] = ("The page was fetched but could not be read: %s"
                          % ext["error"]["detail"])[:500]
        return {"source": source, "rows": []}

    rows = ext["rows"]
    source["rows_found"] = len(rows)
    notes = [n for n in (fetched["note"], ext.get("note")) if n]
    source["note"] = " ".join(notes)[:500]
    if not rows:
        # Fetched fine, read fine, listed nobody. Distinct from blocked, and
        # the distinction is the whole point of this ledger.
        source["status"] = SOURCE_OK
    return {"source": source, "rows": rows}
