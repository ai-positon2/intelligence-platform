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
from .event_intel_http import public_get

from . import claude_websearch
from .event_intel_store import (SOURCE_BLOCKED, SOURCE_ERROR, SOURCE_NOT_FOUND,
                                SOURCE_OK, ROLES, VIA_PAGE)

logger = logging.getLogger(__name__)

_UA = ("Mozilla/5.0 (compatible; Position2-Intelligence/1.0; "
       "+https://intelligence.position2.com)")
_TIMEOUT = 20
_MAX_BYTES = 3_000_000
# What we hand the model. Past this, the page is truncated and the source row
# says so, because a silently clipped exhibitor list undercounts an event.
_MAX_CHARS = 240_000
# Below this much readable text, the page almost certainly rendered its list
# client-side. Reported as blocked, never as "this event has no exhibitors".
_MIN_USEFUL_CHARS = 400

# How many pages of one paginated directory to follow. An exhibitor directory
# is very often "Page 1 of 14", and reading only page one produced the single
# largest undercount in the first version of this agent: a 40-row roster that
# looked complete because nothing said otherwise. Bounded rather than
# unbounded, and the ledger records where it stopped, so an incomplete read is
# still a stated one.
MAX_PAGES = 12

# Mount points that mean the list is assembled in the browser. Checked against
# the RAW markup, since the text flattener drops script tags by design.
_SPA_MARKERS = (
    '__next_data__', 'id="root"', "id='root'", 'id="__next"', "id='__next'",
    'ng-app', 'ng-version', 'data-reactroot', 'id="app"', "id='app'",
    'window.__nuxt__', 'v-cloak', 'data-svelte', 'wp-json/wp/v2',
)

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
                # the company name lives only in the alt text. When the logo is
                # inside its own link, which is how every sponsor tier is built,
                # that alt text IS the anchor's label: writing it to self.parts
                # instead left the anchor empty, and an anchor with no label had
                # its href thrown away a few lines below. That single misplaced
                # append cost the domain of every logo-linked sponsor.
                self._append(alt + " ")
        elif tag in _BLOCK_TAGS:
            # A separator inside an anchor belongs to the label, not to the
            # page. Written to self.parts it landed outside the text being
            # collected, and a linked card came back as "Acme IncBooth 402".
            self._append("\n" if self._href is None else " ")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "a":
            label = " ".join("".join(self._a_text).split()).strip()
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
            self._append("\n" if self._href is None else " ")

    def _append(self, text: str) -> None:
        """Write to whichever buffer is currently open.

        Everything between <a> and </a> belongs to that anchor's label. Every
        writer in this class goes through here so a new one cannot reintroduce
        the bug where content was written past an open anchor.
        """
        if self._href is not None:
            self._a_text.append(text)
        else:
            self.parts.append(text)

    def handle_data(self, data):
        if self._skip:
            return
        self._append(data)

    def text(self) -> str:
        # NOT unescaped again here. HTMLParser(convert_charrefs=True) has
        # already decoded character data, and it always decodes attribute
        # values, so a second pass only reaches the URLs this class just
        # inlined. html.unescape resolves the legacy entity names without a
        # trailing semicolon, which rewrote "?type=exh&reg=EU&sect=3" into
        # "?type=exh(R)=EU(S)=3" and put a dead link in the client's report.
        raw = "".join(self.parts)
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
    out = {"url": url, "final_url": url, "status": SOURCE_ERROR,
           "http_status": None, "text": "", "note": "", "truncated": False,
           "spa": None, "redirected": False}
    try:
        r = public_get(url, timeout=_TIMEOUT, stream=True, headers={
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
    # Where we actually ended up. `requests` follows redirects by default, so
    # until this was read every relative link on a redirected page resolved
    # against the URL we asked for rather than the one we got, and every
    # next-page link failed the same-path test below, which stopped the walk
    # at page one and said nothing. Event sites redirect constantly:
    # /exhibitors -> /2026/exhibitors/ is the normal shape of a site that has
    # run for more than one year.
    final = str(getattr(r, "url", "") or url)
    out["final_url"] = final
    out["redirected"] = _same_page(final, url) is False
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

    # Relative links resolve against where the document actually came from.
    text = html_to_linked_text(markup, out["final_url"])
    out["spa"] = client_render_marker(markup)
    if len(text) < _MIN_USEFUL_CHARS:
        out["status"] = SOURCE_BLOCKED
        out["note"] = ("Returned only %d characters of readable text%s, so its "
                       "list is rendered by JavaScript after load and a plain "
                       "fetch cannot see it."
                       % (len(text),
                          " and carries a %s mount point" % out["spa"]
                          if out["spa"] else ""))
        return out

    if len(text) > _MAX_CHARS:
        text = text[:_MAX_CHARS]
        out["truncated"] = True
        out["note"] = ("Page was longer than the %d-character extraction limit and "
                       "was truncated, so this list may be incomplete." % _MAX_CHARS)
    if out["redirected"]:
        note = "Redirected to %s." % out["final_url"][:200]
        out["note"] = (out["note"] + " " + note).strip() if out["note"] else note
    out["status"] = SOURCE_OK
    out["text"] = text
    return out


def _same_page(a: str, b: str) -> bool:
    """Same host and same path, ignoring the fragment and the trailing slash.

    A redirect that only adds a slash or swaps http for https has not moved
    the page, and reporting those as redirects would put a note on most of
    the web.
    """
    try:
        ua, ub = urlparse(a or ""), urlparse(b or "")
    except Exception:
        return a == b
    return (ua.netloc.lower() == ub.netloc.lower()
            and (ua.path or "/").rstrip("/") == (ub.path or "/").rstrip("/")
            and ua.query == ub.query)


def client_render_marker(markup: str) -> str | None:
    """The framework mount point this markup carries, if any.

    Used for one purpose only: telling "this event lists no exhibitors" apart
    from "this event's exhibitor list is built in the browser and we read the
    empty shell around it". Those produce identical output today, and the
    first is a fact about the event while the second is a hole in the read.

    Deliberately NOT used on its own to reject a page. Plenty of
    server-rendered sites mount a React widget somewhere; a marker only counts
    against a page that also yielded nothing.
    """
    low = (markup or "").lower()
    for marker in _SPA_MARKERS:
        if marker in low:
            return marker.strip('"\'').replace("id=", "").replace("_", "")
    return None


_PAGE_LINK = re.compile(r"\[(https?://[^\]\s]+)\]")
_PAGE_PARAM = re.compile(
    r"(?:^|[?&])(page|pg|p|paged|offset|start|from)=(\d+)\b", re.I)

# Pagination carried in the PATH rather than the query string. WordPress is
# the reason this exists: it serves page two of any archive at
# `/exhibitors/page/2/`, and a query-string-only reader stops at page one on
# every WordPress event site, which is a large share of them.
#
# Every pattern here carries the literal word `page` or `pg`. That label IS
# the safety check, and a bare trailing number is deliberately NOT accepted:
# on `/speakers/`, the link `/speakers/42/` is speaker forty-two, not page
# forty-two, and following it would pull a profile page into the roster and
# count it as a directory page we had read.
_PAGE_PATH = re.compile(r"/(?:page|pg)[/_-]?(\d+)/?$", re.I)


def _page_of_path(path: str):
    """(stem, page number) for a path that paginates in its own segments.

    The stem is the path with the pagination part removed, which is what makes
    `/exhibitors/` and `/exhibitors/page/2/` recognisable as one listing.
    Returns None when the path carries no page marker.
    """
    m = _PAGE_PATH.search(path or "")
    if not m:
        return None
    try:
        n = int(m.group(1))
    except ValueError:
        return None
    stem = (path[:m.start()] or "/").rstrip("/") + "/"
    return stem, n


def _listing_key(u) -> tuple:
    """What has to match for two URLs to be pages of the SAME listing.

    Host, the path with any pagination stripped, and every query parameter
    except the pagination one. A link that also changes `?category=` is a
    filtered view of the directory rather than the next page of it.
    """
    path = u.path or "/"
    got = _page_of_path(path)
    stem = got[0] if got else (path.rstrip("/") + "/" if path != "/" else "/")
    return (u.netloc.lower(), stem,
            _PAGE_PARAM.sub("", u.query or ""))


def _page_number(u) -> int:
    """Which page of its listing this URL is. 1 when it does not say."""
    m = _PAGE_PARAM.search(u.query or "")
    if m:
        try:
            return int(m.group(2))
        except ValueError:
            return 1
    got = _page_of_path(u.path or "")
    return got[1] if got else 1


# "Page 1 of 14" and its common phrasings. Read for one purpose: an event that
# TELLS us how many pages its directory has, and from which we read fewer, is
# an undercount we can name instead of one the reader has to guess at.
_DECLARED_PAGES = re.compile(
    r"\bpages?\s*(?:\d+\s*)?(?:of|/)\s*(\d{1,4})\b"
    r"|\b\d+\s+of\s+(\d{1,4})\s+pages\b", re.I)


def declared_page_count(text: str) -> int | None:
    """How many pages the listing says it has, if it says.

    The largest declared number wins: a directory footer often carries both
    "Page 1 of 14" and a per-section counter, and the roster is short by the
    bigger of the two.
    """
    best = 0
    for m in _DECLARED_PAGES.finditer(text or ""):
        for g in m.groups():
            if not g:
                continue
            try:
                n = int(g)
            except ValueError:
                continue
            # A four-digit count is a year or a row total, not a page count.
            if 1 < n <= 500:
                best = max(best, n)
    return best or None


def next_page_links(text: str, current_url: str, limit: int = MAX_PAGES) -> list[str]:
    """Later pages of the SAME paginated listing, in page order.

    An exhibitor directory that says "1 2 3 ... 14" is the normal case, and
    following it is the difference between a 40-row roster and a 300-row one.

    Three restrictions, each of which exists because dropping it produced a
    wrong roster in testing:

      * Same host and same listing. A `?page=2` on a different path is a
        different listing, and merging it silently mixes two rosters. The
        path's own pagination is stripped before that comparison, so
        `/exhibitors/` and `/exhibitors/page/2/` are recognised as one
        listing rather than two.
      * The pagination parameter must be the only thing that differs. A link
        that also changes `?category=` is a filtered view, not the next page.
      * Strictly greater page numbers only, so "Previous" and "1" do not send
        the harvester round in a circle re-reading what it already has.
    """
    try:
        cur = urlparse(current_url)
    except Exception:
        return []
    cur_key = _listing_key(cur)
    cur_page = _page_number(cur)

    found: dict[int, str] = {}
    for href in _PAGE_LINK.findall(text or ""):
        try:
            u = urlparse(href)
        except Exception:
            continue
        # Same listing: same host, same path once pagination is stripped, and
        # the same query apart from the pagination parameter.
        if _listing_key(u) != cur_key:
            continue
        n = _page_number(u)
        # Strictly greater, so "Previous" and "1" do not send the harvester
        # round in a circle re-reading what it already has. This also rejects
        # a same-listing link that carries no page marker at all, which is the
        # listing's own front page.
        if n <= cur_page:
            continue
        found.setdefault(n, href)
    return [found[n] for n in sorted(found)[:max(0, limit)]]


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
    # Social and link shorteners.
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "fb.me",
    "instagram.com", "youtube.com", "youtu.be", "tiktok.com", "xing.com",
    "crunchbase.com", "wikipedia.org", "goo.gl", "bit.ly", "lnkd.in",
    "t.co", "hubs.ly", "ow.ly", "buff.ly", "medium.com", "github.com",
    "google.com", "maps.google.com",
    # Event platforms. A directory hosted on one of these links each exhibitor
    # to its in-platform profile, so without this every row on the floor
    # resolves to the platform and the client is shown a roster where all 200
    # exhibitors are Whova Inc. These six are the ones this product meets most
    # often and every one of them was missing.
    "cvent.com", "bizzabo.com", "whova.com", "brella.io", "grip.events",
    "sched.com", "swapcard.com", "hopin.com", "splashthat.com", "lu.ma",
    "eventbrite.com", "eventbrite.co.uk", "eventbrite.ca", "eventbrite.com.au",
    "eventbrite.de", "eventbrite.fr", "eventbrite.ie", "eventbrite.nl",
    "eventbrite.es", "eventbrite.it", "eventbrite.sg", "eventbrite.hk",
    "accelevents.com", "pheedloop.com", "attendify.com", "expofp.com",
    "map-dynamics.com", "a2zinc.net", "mapyourshow.com", "swoogo.com",
    "regfox.com", "ticketmaster.com", "meetup.com",
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


def _extract_chunk(page_text: str, page_url: str, page_kind: str,
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
        return {"rows": [], "note": "", "error": res["error"], "spend": claude_websearch.spend_of(res)}

    parsed = claude_websearch.extract_json(res.get("text") or "", require="rows")
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
    # The model's own written note; the roster row fields above it are facts
    # copied off the page and are left exactly as published.
    note = claude_websearch.strip_em_dash(str(parsed.get("note") or ""))[:400]
    return {"rows": rows, "note": note, "error": None, "spend": claude_websearch.spend_of(res)}


def extract_participants(page_text, page_url, page_kind, event_name, event_host=""):
    from .event_intel_evidence import chunks, supported_rows, source_snapshot, text_hash
    pieces = list(chunks(page_text))
    rows, rejected, errors, spend, notes = [], [], [], [], []
    for index, piece in enumerate(pieces):
        result = _extract_chunk(piece, page_url, page_kind, event_name, event_host)
        spend.append(result.get('spend'))
        if result.get('error'):
            errors.append(dict(result['error'], chunk=index))
            continue
        accepted, refused = supported_rows(result['rows'], piece, page_kind)
        rows.extend(accepted)
        rejected.extend(refused)
        if result.get('note'):
            notes.append(result['note'])
    if rejected:
        notes.append('%d proposed rows lacked literal organization/role support and were withheld.' % len(rejected))
    if errors:
        notes.append('%d of %d extraction chunks failed; the roster is incomplete.' % (len(errors), len(pieces)))
    return {'rows': rows, 'note': ' '.join(notes),
            'error': errors[0] if pieces and len(errors) == len(pieces) else None,
            'spend': claude_websearch.spend_sum(*spend),
            'coverage': {'chunks_total': len(pieces), 'chunks_read': len(pieces)-len(errors),
                         'chunker_version': 1,
                         'chunks': [{'index':i,'text_sha256':text_hash(piece),'characters':len(piece)} for i,piece in enumerate(pieces)],
                         'rejected_rows': len(rejected), 'errors': errors,
                         'complete': False, 'scope': 'readable text only; public directory completeness unverified'},
            'snapshot': source_snapshot(page_url, page_text)}


def harvest_page(page: dict, event_name: str, event_host: str = "",
                 max_pages: int = MAX_PAGES) -> dict:
    """Fetch one listing and extract it, following its pagination.

    Returns the source ledger entry and the rows together, so a caller writes
    one and saves the other without needing to know how either failed.

    Two behaviours here that the first version of this agent got wrong:

    Pagination is followed. Reading page one of a fourteen-page exhibitor
    directory and reporting the result as the roster is an undercount with
    nothing on screen to reveal it, which is the exact defect the source
    ledger exists to prevent and which page-one-only reintroduced.

    A page that yields nothing AND carries a browser-side mount point is
    reported as unreadable, not as an event with no exhibitors. Those two
    render identically and mean opposite things.
    """
    url, kind = page["url"], page.get("kind") or "unknown"
    fetched = fetch_page(url)
    # The ledger keeps the URL the event PUBLISHED, because that is the one a
    # reader can check against the event's own site. Everything that reads the
    # document works from where the document actually came from.
    here = fetched.get("final_url") or url
    source = {"url": url, "kind": kind, "status": fetched["status"],
              "http_status": fetched["http_status"], "rows_found": 0,
              "note": fetched["note"], "pages_read": 0, "pages_seen": 1,
              "spa": fetched.get("spa"),
              "final_url": here if here != url else None}
    if fetched["status"] != SOURCE_OK:
        return {"source": source, "rows": []}

    from .event_intel_access import discover
    source["access_links"] = discover(fetched["text"], here, event_host)
    source["snapshots"] = []
    source["extraction"] = []
    from .event_intel_evidence import roster_years, source_snapshot
    expected_year = str(page.get('edition') or '')[:4]
    observed_years = roster_years(fetched['text'])
    source['expected_edition'] = expected_year or None
    source['observed_roster_years'] = observed_years
    if expected_year.isdigit() and observed_years and observed_years != [expected_year]:
        source['status'] = SOURCE_BLOCKED
        source['note'] = 'The roster names edition(s) %s; the requested edition is %s. Rows were withheld until edition ownership is verified.' % (', '.join(observed_years),expected_year)
        source['snapshots'] = [source_snapshot(here,fetched['text'])]
        source['coverage'] = {'complete': False, 'edition_mismatch': True}
        return {'source':source,'rows':[]}
    from .event_intel_cache import extract as cached_extract
    def read(fetched_page, page_url):
        # Truncated pages never populate or consume the reusable extraction.
        identity = page.get('cache_identity') if not fetched_page.get('truncated') else None
        return cached_extract(fetched_page['text'], page_url, kind, event_name,
                              event_host, identity, _SYSTEM, extract_participants)
    ext = read(fetched, here)
    source["snapshots"].append(ext.get("snapshot", {}))
    source["extraction"].append(ext.get("coverage", {}))
    source["truncated"] = bool(fetched.get("truncated"))
    if ext.get("error"):
        source["status"] = SOURCE_ERROR
        source["note"] = ("The page was fetched but could not be read: %s"
                          % ext["error"]["detail"])[:500]
        return {"source": source, "rows": []}

    rows = list(ext["rows"])
    for row in rows:
        row.setdefault('evidence', {}).update(observed_roster_years=observed_years)
    notes = [n for n in (fetched["note"], ext.get("note")) if n]
    source["pages_read"] = 1

    # Follow the rest of the listing. A page that fails mid-run stops the
    # walk and says where it stopped, rather than silently returning what it
    # happened to reach.
    seen_urls = {url, here}
    queue = next_page_links(fetched["text"], here, limit=max_pages - 1)
    source["pages_seen"] = 1 + len(queue)
    # What the listing SAYS it has, which is not always what it links to.
    declared = declared_page_count(fetched["text"])
    stopped = None
    while queue and source["pages_read"] < max_pages:
        nxt = queue.pop(0)
        if nxt in seen_urls:
            continue
        seen_urls.add(nxt)
        got = fetch_page(nxt)
        if got["status"] != SOURCE_OK:
            stopped = ("Stopped following this listing at page %d of %d: %s"
                       % (source["pages_read"] + 1, source["pages_seen"],
                          got["note"] or got["status"]))
            break
        nxt = got.get("final_url") or nxt
        seen_urls.add(nxt)
        page_years = roster_years(got['text'])
        if expected_year.isdigit() and page_years and page_years != [expected_year]:
            stopped = 'A later roster page names a different edition; its rows were withheld.'
            source['snapshots'].append(source_snapshot(nxt,got['text'],observed_roster_years=page_years))
            break
        source["access_links"].extend(discover(got["text"], nxt, event_host))
        sub = read(got, nxt)
        source["snapshots"].append(sub.get("snapshot", {}))
        source["extraction"].append(sub.get("coverage", {}))
        source["truncated"] = source["truncated"] or bool(got.get("truncated"))
        if sub.get("note"):
            notes.append(sub["note"])
        if sub.get("error"):
            stopped = ("Stopped following this listing at page %d of %d: the "
                       "page was fetched but could not be read."
                       % (source["pages_read"] + 1, source["pages_seen"]))
            break
        for row in sub['rows']:
            row.setdefault('evidence', {}).update(observed_roster_years=page_years)
        rows.extend(sub["rows"])
        source["pages_read"] += 1
        for extra in next_page_links(got["text"], nxt, limit=max_pages):
            if extra not in seen_urls and extra not in queue:
                queue.append(extra)
                source["pages_seen"] += 1

    if source["pages_read"] > 1:
        notes.append("Followed %d of %d pages of this listing."
                     % (source["pages_read"], source["pages_seen"]))
    if stopped:
        notes.append(stopped)
    elif queue and source["pages_read"] >= max_pages:
        notes.append("This listing has more pages than the %d-page limit, so "
                     "it is incomplete." % max_pages)

    # A directory that prints "Page 1 of 14" has told us its own size. If we
    # read fewer than that, the roster is short by a knowable amount, and
    # saying so is the difference between an undercount and a stated one.
    # Checked LAST and independently of the queue, because the case this
    # exists for is precisely the one where no next-page link was followable:
    # a cursor, a button that posts, a page number rendered in the browser.
    if declared and source["pages_read"] < declared:
        source["pages_declared"] = declared
        notes.append("This listing says it has %d pages and %d %s read, so it "
                     "is incomplete%s."
                     % (declared, source["pages_read"],
                        "was" if source["pages_read"] == 1 else "were",
                        "" if (queue or stopped)
                        else " and the remaining pages could not be followed "
                             "from links on the page"))

    # De-duplicate across pages. A directory that repeats a headline sponsor
    # on every page would otherwise inflate the roster by the page count.
    deduped, seen_rows = [], set()
    for r in rows:
        key = ((r.get("org_domain") or r.get("org_name") or "").lower(),
               (r.get("person_name") or "").lower(), r.get("role"))
        if key in seen_rows:
            continue
        seen_rows.add(key)
        r.setdefault("provenance", VIA_PAGE)
        r.setdefault("evidence", {}).update(expected_edition=expected_year or None)
        deduped.append(r)
    if len(deduped) < len(rows):
        notes.append("%d duplicate rows across pages were merged."
                     % (len(rows) - len(deduped)))

    source["rows_found"] = len(deduped)
    if not deduped and source["spa"]:
        # Read fine, listed nobody, and the markup says the list is built in
        # the browser. That is a hole in the read, not a fact about the event.
        source["status"] = SOURCE_BLOCKED
        notes.append("This page listed nobody and carries a %s mount point, so "
                     "its list is built in the browser and a plain fetch cannot "
                     "see it. This is not evidence the event has no %s."
                     % (source["spa"], kind if kind != "unknown" else "participants"))
    source["coverage"] = {"complete": False, "scope": "published pages only",
        "pages_read": source["pages_read"], "pages_seen": source["pages_seen"],
        "pages_declared": declared, "truncated": source["truncated"],
        "pagination_incomplete": bool(stopped or queue or (declared and source["pages_read"] < declared))}
    source["note"] = " ".join(notes)[:800]
    return {"source": source, "rows": deduped}
