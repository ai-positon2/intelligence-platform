"""Company websites read off an event's own profile pages.

The roster's company data hangs on one field: a company's own website, the
only thing Apollo is asked by, because a name match attaches someone else's
firmographics (see event_intel_enrich). Many directories never print that
link on the list itself. Web Summit's featured startups, Money20/20's sponsor
list and every directory shaped like them link each company to a PROFILE on
the event's own site, and the website is printed there instead. Three of the
first four live rosters (Web Summit, Money20/20, The AI Conference: 439 rows)
came back with no website on a single row, so "Match companies in Apollo"
never appeared and every row said "not looked up" with no way to change it.

This follows those profile links and reads the website the event published
for the company. It never derives one: a profile that shows no website, or
shows more than one outside link it cannot tell apart, leaves the row as it
was. Free (plain page reads, no model call, no Apollo credit), bounded, and it
never raises into the harvest that calls it.

What counts as "the website" on a profile page, in order:
  1. A link the page itself labels as the website ("website", "visit site",
     a link whose label or aria-label says so), when exactly one domain is
     labelled that way.
  2. Otherwise exactly one outside domain left once the event's own hosts,
     social networks and event platforms (clean_domain's exclusions), the
     listing page's own outside links (its header and footer), and any domain
     that appears on more than one company's profile (a sponsor banner, a
     sister event) are set aside.
"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

logger = logging.getLogger(__name__)

# Per listing. Web Summit's featured startups are 100 and Money20/20's
# sponsors 245; past this the note says how many were left unread.
MAX_PROFILES = 300
WORKERS = 6
_TIMEOUT = 12
_MAX_BYTES = 1_500_000
_UA = ("Mozilla/5.0 (compatible; Position2-Intelligence/1.0; "
       "+https://intelligence.position2.com)")

_LINK_IN_TEXT = re.compile(r"([^\[\]\n]{1,300}?) \[(https?://[^\]\s]+)\]")
# The WHOLE label, not a word in it: a footer's "Website Terms of Use" is a
# link that says "website" and is the event's own legal page.
_WEBSITE_LABEL = re.compile(r"^\s*(visit\s+)?(our\s+|the\s+|company\s+|official\s+)?"
                            r"(web\s?site|homepage|home\s+page|site)(\s+link)?\s*$", re.I)

# What a profile lookup recorded on a row, so a backfill never asks twice.
FOUND, NO_WEBSITE, UNREADABLE = "found", "no_website", "unreadable"
# The row's own listing was read and links it to no profile page, so there is
# nowhere to read a website from. Settled, like FOUND and NO_WEBSITE: without
# it the offer to look came back after every press for a partner logo wall.
NO_PROFILE = "no_profile"
SETTLED = (FOUND, NO_WEBSITE, NO_PROFILE)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _words(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split())


def _host(url: str) -> str:
    try:
        h = (urlparse(url).hostname or "").lower()
    except Exception:
        return ""
    return h[4:] if h.startswith("www.") else h


_SUFFIX_LABELS = {"co", "com", "org", "net", "ac", "gov", "edu", "or", "ne", "go"}


def _with_parent(host: str) -> set:
    """A host and the site it sits under: us.money2020.com is money2020.com's,
    so a link back to money2020.com is the event's own. Never a bare public
    suffix (the parent of x.co.uk is not co.uk)."""
    out = {host} if host else set()
    parts = (host or "").split(".")
    if len(parts) > 2:
        parent = ".".join(parts[1:])
        if len(parent.split(".")) >= 2 and parts[1] not in _SUFFIX_LABELS:
            out.add(parent)
    return out


def _slug(url: str) -> str:
    try:
        parts = [p for p in (urlparse(url).path or "").split("/") if p]
    except Exception:
        return ""
    return _norm(parts[-1]) if parts else ""


def _names_label(label: str, name: str) -> bool:
    """The anchor's text IS this company's name, or opens or closes with it.
    A card often adds the category beside the name ("Checkout.com Payments
    & Payments Technology Checkout.com")."""
    lw, nw = _words(label), _words(name)
    if not nw:
        return False
    return lw == nw or lw.startswith(nw + " ") or lw.endswith(" " + nw)


def profile_links(rows: list[dict], listing_texts: list[tuple[str, str]]) -> dict[int, str]:
    """Row index -> the profile page the listing links that company to.

    `listing_texts` is (url, linked text) for every page of the listing, the
    text as html_to_linked_text writes it (`label [href]`). Only links on the
    listing's own host count. A company linked to two different profiles, or
    a profile claimed by two companies, is left out: neither can be told
    apart from the page alone.
    """
    listing_urls = {u.split("#")[0].rstrip("/") for u, _ in listing_texts}
    hosts = {_host(u) for u, _ in listing_texts if _host(u)}
    anchors = []
    for _, text in listing_texts:
        for label, href in _LINK_IN_TEXT.findall(text or ""):
            href = href.split("#")[0]
            if _host(href) in hosts and href.rstrip("/") not in listing_urls:
                anchors.append((label.strip(), href))

    by_row: dict[int, set] = {}
    for i, r in enumerate(rows):
        if r.get("org_domain"):
            continue
        name = r.get("org_name") or ""
        n = _norm(name)
        if len(n) < 2:
            continue
        for label, href in anchors:
            slug = _slug(href)
            # A label that opens or closes with the name only counts when
            # the profile's own address is a shortening of the name too:
            # "ABC Technologies" at /abc-technologies is not "ABC".
            if (slug == n or _words(label) == _words(name)
                    or (_names_label(label, name) and len(slug) >= 3 and slug in n)):
                by_row.setdefault(i, set()).add(href.rstrip("/"))

    claimed: dict[str, set] = {}
    for i, hrefs in by_row.items():
        for h in hrefs:
            claimed.setdefault(h, set()).add(_norm(rows[i].get("org_name") or ""))
    out = {}
    for i, hrefs in by_row.items():
        if len(hrefs) == 1:
            h = next(iter(hrefs))
            if len(claimed[h]) == 1:
                out[i] = h
    return out


class _Anchors(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.links, self._open = base, [], None

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        a = dict(attrs)
        href = (a.get("href") or "").strip()
        if not href or href.lower().startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
            self._open = None
            return
        labels = " ".join(str(a.get(k) or "") for k in ("label", "aria-label", "title", "name"))
        self._open = {"href": urljoin(self.base, href), "label": labels, "text": []}

    def handle_data(self, data):
        if self._open is not None:
            self._open["text"].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._open is not None:
            o = self._open
            o["text"] = " ".join("".join(o["text"]).split())
            self.links.append(o)
            self._open = None


def outside_links(markup: str, base_url: str) -> list[dict]:
    """Every outside link on a profile page with whatever names it: its text
    and its label, aria-label, title and name attributes."""
    p = _Anchors(base_url)
    try:
        p.feed(markup or "")
        p.close()
    except Exception:
        logger.debug("event_intel_profiles: markup did not parse cleanly on %s", base_url)
    return [l for l in p.links if l["href"].startswith(("http://", "https://"))]


def _fetch(url: str) -> str | None:
    from .event_intel_http import public_get
    try:
        r = public_get(url, timeout=_TIMEOUT, stream=True, headers={
            "User-Agent": _UA, "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9"})
    except Exception as e:
        logger.info("event_intel_profiles: could not fetch %s: %s", url, e)
        return None
    try:
        if r.status_code != 200:
            logger.info("event_intel_profiles: HTTP %s on %s", r.status_code, url)
            return None
        ctype = (r.headers.get("Content-Type") or "").lower()
        if ctype and "html" not in ctype:
            return None
        body, size = [], 0
        for chunk in r.iter_content(65536):
            body.append(chunk)
            size += len(chunk)
            if size > _MAX_BYTES:
                break
        return b"".join(body).decode(r.encoding or "utf-8", errors="replace")
    except Exception as e:
        logger.info("event_intel_profiles: could not read %s: %s", url, e)
        return None
    finally:
        r.close()


def choose_website(links: list[dict], skip: set, event_host: str = "") -> str | None:
    """The one domain this profile publishes as the company's own, or None."""
    from .event_intel_harvest import clean_domain
    labelled, plain = set(), set()
    for l in links:
        d = clean_domain(l["href"], event_host)
        if not d or d in skip or any(d == s or d.endswith("." + s) for s in skip):
            continue
        plain.add(d)
        if _WEBSITE_LABEL.search(l.get("label") or "") or _WEBSITE_LABEL.search(l.get("text") or ""):
            labelled.add(d)
    if len(labelled) == 1:
        return next(iter(labelled))
    if not labelled and len(plain) == 1:
        return next(iter(plain))
    return None


def fill_websites(rows: list[dict], listing_texts: list[tuple[str, str]],
                  event_host: str = "", deadline: float | None = None,
                  limit: int = MAX_PROFILES, fetch=None) -> dict:
    """Give rows with no website the one their profile page publishes.

    Mutates `rows` in place: `org_domain` is set only when found, and each row
    whose profile was opened records the outcome in evidence.profile_lookup,
    so a later pass skips it. Returns counts for the source note. Never
    raises. `deadline` (a time.monotonic() value) stops starting new fetches,
    which is what lets a web request backfill inside its own timeout.
    """
    stats = {"profiles": 0, "read": 0, "websites": 0, "unreadable": 0, "left": 0,
             "no_profile": 0}
    try:
        links = profile_links(rows, listing_texts)
    except Exception:
        logger.exception("event_intel_profiles: matching profile links failed")
        return stats

    def status(i):
        return ((rows[i].get("evidence") or {}).get("profile_lookup") or {}).get("status")

    read_here = {u.split("#")[0].rstrip("/") for u, _ in listing_texts}
    for i, r in enumerate(rows):
        src = (r.get("source_url") or "").split("#")[0].rstrip("/")
        if (not r.get("org_domain") and i not in links and status(i) not in SETTLED
                and (not src or src in read_here)):
            ev = r.get("evidence")
            if not isinstance(ev, dict):
                ev = r["evidence"] = {}
            ev["profile_lookup"] = {"status": NO_PROFILE}
            stats["no_profile"] += 1
    # Only a settled answer is final. A page that could not be read is asked
    # again on the next pass rather than written off.
    todo = [(i, u) for i, u in links.items() if status(i) not in SETTLED]
    stats["profiles"] = len(todo)
    if not todo:
        return stats
    fetch = fetch or _fetch
    chrome = set()
    for h in {_host(u) for u, _ in listing_texts} | ({event_host} if event_host else set()):
        chrome.update(_with_parent(h))
    from .event_intel_harvest import clean_domain
    for _, text in listing_texts:
        for _, href in _LINK_IN_TEXT.findall(text or ""):
            d = clean_domain(href)
            if d:
                chrome.add(d)

    batch = todo[:limit]
    stats["left"] = len(todo) - len(batch)
    pages: dict[int, list | None] = {}

    def one(item):
        i, url = item
        if deadline is not None and time.monotonic() > deadline:
            return i, url, "skipped"
        markup = fetch(url)
        if markup is None and (deadline is None or time.monotonic() + 2 < deadline):
            # Directories throttle a burst of profile reads (Money20/20 refused
            # 16 of 78 on a second pass a minute after the first). Once more,
            # after a pause, before calling the page unreadable.
            time.sleep(1.5)
            markup = fetch(url)
        return i, url, (outside_links(markup, url) if markup else None)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i, url, got in pool.map(one, batch):
            if got == "skipped":
                stats["left"] += 1
                continue
            pages[i] = got

    # A domain on two different companies' profiles is the page's own
    # furniture (a sponsor banner, a sister event), not either company's site.
    seen_on: dict[str, set] = {}
    for i, got in pages.items():
        for l in got or []:
            d = clean_domain(l["href"], event_host)
            if d:
                seen_on.setdefault(d, set()).add(_norm(rows[i].get("org_name") or ""))
    shared = {d for d, owners in seen_on.items() if len(owners) > 1}

    for i, got in pages.items():
        row = rows[i]
        ev = row.get("evidence")
        if not isinstance(ev, dict):
            ev = row["evidence"] = {}
        if got is None:
            stats["unreadable"] += 1
            ev["profile_lookup"] = {"status": UNREADABLE, "profile_url": links[i]}
            continue
        stats["read"] += 1
        site = choose_website(got, chrome | shared, event_host)
        if site:
            row["org_domain"] = site
            stats["websites"] += 1
            ev["profile_lookup"] = {"status": FOUND, "profile_url": links[i]}
        else:
            ev["profile_lookup"] = {"status": NO_WEBSITE, "profile_url": links[i]}
    return stats


def note(stats: dict) -> str:
    """One plain sentence for the source ledger, or "" when nothing ran."""
    if not stats.get("profiles"):
        return ""
    read, found = stats.get("read", 0), stats.get("websites", 0)
    s = ("Opened %d company profile page%s on the event's site and found %d "
         "website%s published there."
         % (read, "" if read == 1 else "s", found, "" if found == 1 else "s"))
    if stats.get("unreadable"):
        s += " %d profile page%s could not be read." % (
            stats["unreadable"], "" if stats["unreadable"] == 1 else "s")
    if stats.get("left"):
        s += " %d more %s not opened yet." % (
            stats["left"], "was" if stats["left"] == 1 else "were")
    return s
