"""Step 0.5: read a company's own site and propose the intake it implies.

The recommendation play opens on thirteen fields. Only two of them are
actually required, and nothing on the form says so, but the deeper problem is
who is filling it in: an agency planner answering questions about someone
else's business. "Deal size" and "sales cycle" are not facts they carry
around, so the form stalls on things that are not theirs to know.

Most of it is on the client's own website. This module goes and reads it, and
hands back a filled draft the planner corrects instead of authors.

WE FETCH THE PAGES. THE MODEL DOES NOT GO LOOKING.
--------------------------------------------------

The first version of this handed the job to Claude with the web_search tool
and let it find its own way around the site. It worked, and it was unusable:
measured drafts took 29, 167 and 450 seconds, one exceeded ten minutes, and
one came back starved because the search tool refused every call after the
first batch. That is not a wait you can put behind a button on a form.

So this fetches the homepage with `event_intel_harvest.fetch_page`, follows a
handful of the site's own links, and hands the text to a model that is given
NO search tool at all. `claude_websearch.ask` treats `max_uses=0` as a real
mode for exactly this: a model extracting from pages already in hand must not
be able to search, or it completes a partial picture from elsewhere and the
answer stops being a reading of what those pages said.

Three things get better at once. It is fast, because a handful of concurrent
HTTP fetches plus one tool-free call is seconds rather than minutes. It is
cheaper, because nothing is billed as a search and the input is a bounded
corpus rather than a growing conversation. And it is more honest, because
`sources` becomes the list of pages WE retrieved and can report the HTTP
status of, rather than a list of URLs the model asserts it opened.

WHEN THE SITE ITSELF WON'T COOPERATE
-------------------------------------

A live site can refuse a plain fetch for reasons that have nothing to do with
whether the company is real or findable: Akamai or Cloudflare bot mitigation
silently stalling a non-browser request (myntra.com, timing out rather than
answering), an overloaded origin turning away automated traffic with a 529, a
page that only renders in a browser. None of that means there is nothing to
say about the company, and reporting a hard failure in exactly those cases is
the least useful moment to do it: a large, real, well-covered company is
usually the one whose own site fights back the hardest.

So a site that could not be read directly gets one more try, THROUGH web
search, before this module gives up: `_search_draft` asks a model, with the
real web_search tool on, to find public information from other sources
(their LinkedIn or Crunchbase page, press coverage, a partner naming them)
instead of their own pages. It keeps every rule below intact -- a field still
needs a citable source or it stays null, `wrong_company` is still checked,
and a reply that ran no real search (`search_count == 0`, the same guard
`claude_websearch` documents for exactly this) is discarded rather than
accepted as an answer drawn from the model's own memory. The result says so:
`via` is `"search"` rather than `"site"`, and `note` states plainly that the
company's own site could not be read and this came from public search
instead, so the person reviewing it weighs it accordingly. Only when neither
path finds anything does this module report the honest failure the rest of
this docstring describes.

WHAT THIS MODULE IS NOT ALLOWED TO DO
-------------------------------------

1. **It proposes. It never decides.** Everything here is a draft that a person
   reviews before anything is saved, and nothing it returns is written to a
   profile by this module. The same rule Contact Finder's "Fill filters"
   established: it fills the controls, the user sees every value it set, and
   it stays an accelerator rather than a black box.

2. **The classification is a proposal that must be confirmed by a human, and
   the caller has to enforce that.** `event_intel_rubric.orientation_for`
   raises rather than defaulting, because that one answer decides which side
   of the trade-show floor every later score measures, and a wrong one
   produces a confident, fully-reasoned report about the wrong crowd. That
   rule is about the SYSTEM never picking silently. A model that argues for an
   answer a person then clicks does not break it; a draft that arrives
   pre-accepted does. So the draft carries a reason and a confidence, and the
   page keeps its run button dark until someone chooses.

3. **A field it could not find stays empty and says so.** Pricing and sales
   cycle are usually not published, and a plausible guess at a deal size is
   indistinguishable from a read one once it is sitting in a form field. Every
   field it could not answer is named in `unknown`, which is what lets the
   page show "we could not find this" instead of a blank that looks like an
   oversight. A field that arrives with no supporting evidence is emptied,
   because with no search tool the only thing left for it to have come from is
   the model's own recall.

4. **A site we could not read gets one honest fallback, not a shrug and not a
   fabrication.** Plenty of sites render their text in the browser, block
   automated readers outright, or are simply down when this runs, and
   `fetch_page` says so plainly when it sees one. Rather than stopping there,
   this module tries a bounded web search for the same information from
   OTHER sources -- but that search still owes every field a real, checkable
   source. "We could not find this anywhere, fill it in yourself" remains a
   better answer than a profile assembled from what a model happens to
   remember about a company with this name, and is still what this module
   says when the search comes back with nothing either.
"""

from __future__ import annotations

import collections
import concurrent.futures
import logging
import re
from urllib.parse import urlparse

from . import claude_websearch
from . import event_intel_harvest as harvest
from . import event_intel_rubric as rubric

logger = logging.getLogger(__name__)

# No search tool. See the module docstring: this is the whole design.
MAX_USES = 0

# The reply carries five fields plus a sentence of evidence for each, a
# company sentence and the classification argument. A live draft of one
# company spent 11,180 output tokens when it was also narrating a search;
# without one it is far smaller, but the ceiling costs nothing until it is
# needed and running out of room produces an empty form.
MAX_TOKENS = 12000

# Generous for a call that runs no searches, and far short of the ten minutes
# the search version could take. `claude_websearch.ask` defaults to 280.
TIMEOUT = 180.0

# The web-search fallback, used only when the site itself could not be read.
# Sized like event_intel_resolve's single-entity lookup (max_uses=10),
# slightly lower because a company overview needs fewer searches than
# resolving one event's identity and dates. Timeout matches
# claude_websearch.ask's own default for a call that actually searches.
SEARCH_MAX_USES = 8
SEARCH_TIMEOUT = 280.0

# The homepage plus this many of its own links.
MAX_PAGES = 6

# Per page and in total. Applied AFTER the shared navigation is stripped, so
# what fits is the page's own body rather than its header.
PAGE_CHARS = 14000
CORPUS_CHARS = 70000

# A line appearing on at least this share of the pages is chrome, not content.
# Six pages of a marketing site share a header, a mega-menu and a footer, and
# the first live draft of a real site filled ONE of five fields and said why:
# "the fetched pages are mostly repeated global navigation rather than full
# page bodies". Every page was spending its character budget on the same menu.
BOILERPLATE_SHARE = 0.6

# What to follow off the homepage, best first. Matched against the link's own
# text and its path, so a site that calls it "Who we serve" is found by the
# words as well as by the URL.
PAGE_WANTS = (
    ("pricing", ("pricing", "plans", "packages", "how much")),
    ("customers", ("customer", "case stud", "case-stud", "success stor",
                   "success-stor", "clients", "testimonial")),
    ("what they sell", ("product", "platform", "solution", "services",
                        "what we do", "what-we-do")),
    ("who they sell to", ("industr", "vertical", "who we serve", "who-we-serve",
                          "sectors", "use case", "use-case")),
    ("about", ("about", "company", "who we are", "who-we-are")),
)

# Paths that never say who a company sells to, checked before the hints
# above rather than after. A live draft spent one of its five page slots on
# /terms-of-services, which matched the "services" hint and is a page about
# arbitration venues. Blogs and news are excluded for a different reason: they
# are usually the largest thing on a marketing site and the least
# representative of what it sells today.
SKIP_PATHS = ("/terms", "/privacy", "/policy", "/cookie", "/legal", "/gdpr",
              "/dpa", "/sitemap", "/login", "/signin", "/sign-in", "/register",
              "/blog", "/news", "/press", "/events", "/webinar", "/careers",
              "/jobs", "/support", "/help", "/docs", "/status", "/security",
              "/accessibility", "/trust")


def _skippable(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") or path.startswith(p + "-")
               for p in SKIP_PATHS)


# The fields this module may propose. `client_name` and `website` are what the
# user typed and are never re-drafted: correcting the name someone just typed
# is the one thing they did not ask for.
DRAFT_FIELDS = ("buyer_roles", "verticals", "acv_band", "sales_cycle",
                "geo_scope")

# Caps mirror event_intel_store._PROFILE_TEXT_FIELDS, so a draft cannot arrive
# in a shape the save path would silently truncate differently.
_CAP = 400

_LINK = re.compile(r"\[(https?://[^\]\s]+)\]")


def _classification_menu() -> str:
    return "\n".join(
        "  %s = %s. Their buyers stand: %s"
        % (k, rubric.CLASSIFICATION_LABELS[k],
           rubric.CLASSIFICATION_BUYER_PLACE[k])
        for k in rubric.CLASSIFICATIONS)


_SYSTEM = """You read one company's own web pages and describe who it sells \
to, so that a person can check your reading and correct it. You are filling in \
a form on their behalf. You are not making a decision.

THE COMPANY: {client_name}
THEIR SITE: {website}

THE PAGES BELOW ARE EVERYTHING YOU HAVE. They were fetched for you. You have \
no search tool, so there is nowhere else to look, and anything you write that \
is not in them came out of your own memory of a company with this name. That \
is the one thing you must not do: two businesses sharing a name is common, and \
a recalled profile is indistinguishable from a read one once it is sitting in \
a form field.

FIRST, MAKE SURE IT IS THE RIGHT COMPANY. If the name you were given and these \
pages plainly describe different companies, say so in `wrong_company` and stop \
rather than describing whichever one the pages are about.

RULES.
1. ANY FIELD THESE PAGES DO NOT ANSWER MUST BE null, and its name must appear \
in `unknown`. This is the most important rule here. The person checking your \
work cannot tell which fields you actually confirmed unless you say. Leaving a \
field blank is a good answer.
2. `acv_band` and `sales_cycle` are usually NOT published. Fill them only from \
something concrete in the pages: a price, a published plan, a case study \
naming a contract value or an evaluation length. "Enterprise software so \
probably six figures" is a guess. Leave it null and put it in `unknown`.
3. `buyer_roles` are the job titles that sign or champion the purchase, as the \
pages describe them, comma separated. `verticals` are the industries they sell \
INTO, comma separated, not the industry they are in.
4. `geo_scope` is where they actually sell, as evidenced by offices, case \
studies, currencies or stated coverage. It is not where they might like to \
sell.
5. Every field you fill needs a matching entry in `evidence` saying, in one \
short sentence, what you read that told you, close enough to the page that \
somebody could go and find it. A field with no evidence is a guess wearing a \
fact's clothes, and it will be thrown away.

THE CLASSIFICATION. A person will confirm this before anything runs, so give \
them your best reading and your real reasoning, not a hedge. It decides which \
side of a trade-show floor gets measured, and the four options are:

{classification_menu}

The distinction people get wrong: a B2B company selling to marketing, growth \
or sales teams finds its buyers WORKING the exhibitor booths, because at most \
B2B events every booth is staffed by exactly those people. A B2B company \
selling to any other function finds its buyers in the audience and the session \
tracks instead. Say which of those two the company is, and why, in \
`classification_why`.

Respond with ONLY a JSON object, no prose before or after:
{{"wrong_company": str|null, "what_they_sell": str, \
"classification": str|null, "classification_why": str, \
"classification_confidence": "high"|"medium"|"low", \
"buyer_roles": str|null, "verticals": str|null, "acv_band": str|null, \
"sales_cycle": str|null, "geo_scope": str|null, \
"evidence": {{"field name": "what you read"}}, "unknown": [str], \
"note": str}}

`what_they_sell` is one plain sentence a reader can check at a glance. It is \
the fastest way for them to notice the pages are about the wrong company, so \
write it for that purpose rather than as a summary.

`note` is anything the person filling this form should know that does not fit \
a field: something ambiguous, two plausible readings, a business that has \
clearly changed shape recently, or a page you expected and did not get."""


# Used only when the company's own site could not be fetched directly. Same
# shape and the same discipline as `_SYSTEM` -- a field with no source stays
# null -- except the source is now whatever the search tool actually turned
# up rather than a fixed set of pages this module fetched itself, so this
# variant asks for `sources` in the reply (the direct-read path already owns
# that list, since it is the pages it fetched) and asks each evidence
# sentence to name where it came from.
_SYSTEM_SEARCH = """You are filling in a form on {client_name}'s behalf, the \
same as always, but their own website at {website} could not be read \
directly just now -- it may block automated readers, or it may simply be \
unreachable right now. You have a web search tool instead. Use it to find \
public information about this company from OTHER sources: their LinkedIn or \
Crunchbase page, press coverage, review sites, industry directories, a \
partner or customer naming them. Do not answer from memory alone: every field \
you fill must be something you found through the search tool just now and can \
point to, not something you already knew about a company with this name \
before you searched.

FIRST, MAKE SURE IT IS THE RIGHT COMPANY -- BUT THE WEBSITE YOU WERE GIVEN, \
NOT THE TYPED NAME, IS THE GROUND TRUTH FOR WHICH ONE. A name can arrive \
misspelled, abbreviated, or slightly off from the company's real trading \
name, and that alone is not a mismatch: you have no page from that domain in \
hand to catch a typo the way a direct read would, so start by searching for \
the domain itself (who owns {website}, what they are called) rather than \
searching on the typed name alone and treating an imperfect match as a \
different company. Only say so in `wrong_company`, and stop, when what you \
find about that DOMAIN plainly describes a different, unrelated business \
than a person would reasonably expect from the name they typed -- not \
because search results for the name turned up other, unrelated companies \
that merely share a similar name.

RULES.
1. ANY FIELD YOU CANNOT SUPPORT WITH SOMETHING YOU FOUND MUST be null, and its \
name must appear in `unknown`. The person checking your work cannot tell which \
fields you actually confirmed unless you say. Leaving a field blank is a good \
answer.
2. `acv_band` and `sales_cycle` are usually not published anywhere. Fill them \
only from something concrete: a price, a published plan, a case study naming a \
contract value or an evaluation length. "Enterprise software so probably six \
figures" is a guess. Leave it null and put it in `unknown`.
3. `buyer_roles` are the job titles that sign or champion the purchase, as \
your sources describe them, comma separated. `verticals` are the industries \
they sell INTO, comma separated, not the industry they are in.
4. `geo_scope` is where they actually sell, as evidenced by offices, case \
studies, currencies or stated coverage. It is not where they might like to \
sell.
5. Every field you fill needs a matching entry in `evidence` naming WHERE you \
found it (the source, by name or URL) and what it said, in one short sentence. \
A field with no named source is a guess wearing a fact's clothes, and it will \
be thrown away.
6. `sources` is every URL you actually consulted through the search tool, in \
the order you used them. Only URLs you genuinely visited, never one you are \
inferring exists because it would be the obvious place to look.

THE CLASSIFICATION. A person will confirm this before anything runs, so give \
them your best reading and your real reasoning, not a hedge. It decides which \
side of a trade-show floor gets measured, and the four options are:

{classification_menu}

The distinction people get wrong: a B2B company selling to marketing, growth \
or sales teams finds its buyers WORKING the exhibitor booths, because at most \
B2B events every booth is staffed by exactly those people. A B2B company \
selling to any other function finds its buyers in the audience and the \
session tracks instead. Say which of those two the company is, and why, in \
`classification_why`.

Respond with ONLY a JSON object, no prose before or after:
{{"wrong_company": str|null, "what_they_sell": str, \
"classification": str|null, "classification_why": str, \
"classification_confidence": "high"|"medium"|"low", \
"buyer_roles": str|null, "verticals": str|null, "acv_band": str|null, \
"sales_cycle": str|null, "geo_scope": str|null, \
"evidence": {{"field name": "where you found it and what it said"}}, \
"unknown": [str], "sources": [str], "note": str}}

`what_they_sell` is one plain sentence a reader can check at a glance. It is \
the fastest way for them to notice you found the wrong company, so write it \
for that purpose rather than as a summary.

`note` is anything the person filling this form should know that does not fit \
a field: something ambiguous, two plausible readings, a business that has \
clearly changed shape recently, or that public information about them turned \
out to be limited."""


def _err(kind: str, detail: str, pages=None) -> dict:
    return {"draft": {}, "evidence": {}, "unknown": [], "sources": [],
            "pages": pages or [], "note": "", "what_they_sell": "",
            "classification": None, "classification_why": "",
            "classification_confidence": None,
            "error": {"kind": kind, "detail": detail[:500]}}


# A draft field is not free prose. Every one of these values is stored on the
# profile, printed in the client's report, AND pasted verbatim into the find
# and confirm prompts by `event_intel_discover.profile_brief`. So a sentence
# about what the intake could not find on a webpage becomes a sentence six
# concurrent event searches are told is the client's geography.
#
# Measured on a live run for Position2, this is what `geo_scope` came back as:
#
#   "Global - client logos and case studies reference international/global
#    brands (e.g., 'International Footwear Brand', 'Global Automotive
#    Manufacturer') though no specific office locations are listed on these
#    pages."
#
# The answer is the first word. The rest is the model narrating its own
# reading, and it went into the prompt and the report. `event_intel_discover`
# already refuses exactly this for a category note (see `_reader_note` and
# `_NOTE_PLUMBING` there); the intake had no equivalent for its own fields.
#
# The em dash in that value is the other half: the house style has no em
# dashes, and this one was headed for a client report. Was this module's own
# private regex; now shared as claude_websearch.strip_em_dash, because the
# same defect turned up in five more fields across two other modules once
# anyone went looking for it. Same substitution, so no behaviour changes here.

# Split on sentence ends AND on the concessive joins a model reaches for when
# it starts hedging about the page it just read.
_FIELD_CLAUSE = re.compile(r"(?<=[.;])\s+|\s+(?=though\b|although\b|however\b)",
                           re.I)

# A clause carrying any of these is talking about the READING rather than
# about the client. Dropping one too many is the safe direction: the field
# falls back to blank, and blank is already handled as an honest unknown.
_FIELD_NARRATION = (
    "these pages", "this page", "the pages", "the site does",
    "the website does", "not listed", "not stated", "not specified",
    "not mentioned", "no specific", "does not say", "do not say",
    "does not state", "could not find", "cannot find", "no mention",
    "is unclear", "unclear from", "not disclosed", "no information",
    "implied by", "inferred from", "appears to",
)


def _field(raw, cap: int = _CAP) -> str | None:
    """A draft field value, fit for a prompt and a client's report.

    Em dashes become commas and any clause narrating what was or was not on
    the pages is dropped. Returns None when nothing survives, which the
    caller already treats as an honest unknown.
    """
    text = claude_websearch.strip_em_dash(" ".join(str(raw or "").split()))
    if not text:
        return None
    kept = []
    for piece in _FIELD_CLAUSE.split(text):
        p = piece.strip().strip(",")
        if not p:
            continue
        low = p.lower()
        if any(bit in low for bit in _FIELD_NARRATION):
            continue
        kept.append(p)
    if not kept:
        return None
    out = " ".join(kept).strip().rstrip(",;:")
    # A value that was nothing but a parenthetical aside is not an answer.
    return _text(out, cap)


def _text(raw, cap: int = _CAP) -> str | None:
    """Trim to `cap`, on a word boundary, and mark the cut.

    A hard slice produced "...no sales-cycle length was published on t" on
    screen, which reads as a sentence that trailed off rather than as text we
    trimmed. The reader cannot tell a model that stopped mid-thought from a
    cap it ran into, and one of those is a reason to go and look.
    """
    v = str(raw or "").strip()
    if len(v) <= cap:
        return v or None
    cut = v[:cap]
    at = cut.rfind(" ")
    # Only honour the boundary if it is near the end. A single very long token
    # would otherwise throw most of the text away to avoid splitting it.
    if at > cap * 0.7:
        cut = cut[:at]
    return cut.rstrip(" ,;:.\u2014-") + "\u2026"


def _host(url: str) -> str:
    """Host, ignoring a leading www.

    Links are followed only within the SAME host, deliberately not the same
    registrable domain. A link to another subdomain is usually a different
    property, a docs site or a status page or a separate product, and
    following one is how a profile ends up describing something the company
    merely also owns.
    """
    try:
        h = (urlparse(url).netloc or "").lower()
    except Exception:
        return ""
    return h[4:] if h.startswith("www.") else h


def pick_links(home_text: str, home_url: str, limit: int = MAX_PAGES - 1) -> list:
    """The site's own links worth following, best first.

    `fetch_page` returns text in which every anchor reads `label [url]`, so
    the label and the path are both available and both are used: a site that
    calls its industries page "Who we serve" is found by the words, and one
    that gives it an opaque label is found by the URL.
    """
    host = _host(home_url)
    seen, scored = set(), []
    for m in _LINK.finditer(home_text or ""):
        url = m.group(1)
        if _host(url) != host:
            continue
        try:
            path = (urlparse(url).path or "/").lower().rstrip("/") or "/"
        except Exception:
            continue
        if path == "/" or path in seen or _skippable(path):
            continue
        # The anchor's OWN label, which runs from the end of the previous
        # anchor (or the previous line break) up to this URL. Taking a fixed
        # window backwards instead swept in the neighbouring menu items, and a
        # link scored on its neighbour's words is how a real run followed
        # /support/ while looking for the page that says who they sell to.
        head = home_text[:m.start()]
        cut = max(head.rfind("\n"), head.rfind("]"))
        label = head[cut + 1:].lower() if cut >= 0 else head.lower()
        hay = label + " " + path.replace("-", " ").replace("/", " ")
        for rank, (_, words) in enumerate(PAGE_WANTS):
            if any(w in hay for w in words):
                seen.add(path)
                scored.append((rank, len(path), url))
                break
    scored.sort()
    return [u for _, _, u in scored[:limit]]


def read_site(website: str) -> list:
    """The homepage plus a few of its own pages, fetched. Never raises.

    Returns `fetch_page` results in the order they were read, homepage first.
    A page that could not be read is KEPT in the list with its status and
    note, because "we tried the pricing page and it refused us" is the
    difference between a blank deal size and an unexplained one.
    """
    home = harvest.fetch_page(website)
    if home.get("status") != harvest.SOURCE_OK:
        return [home]

    links = pick_links(home.get("text") or "", website)
    if not links:
        return [home]

    pages = [home]
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(4, len(links))) as pool:
        futures = [pool.submit(harvest.fetch_page, u) for u in links]
        for u, fut in zip(links, futures):
            try:
                pages.append(fut.result())
            except Exception as e:
                # fetch_page does not raise, but a pool can still hand back a
                # failure and one dead link must not cost the whole draft.
                logger.info("event_intel_intake: %s failed: %s", u, e)
                pages.append({"url": u, "status": harvest.SOURCE_ERROR,
                              "http_status": None, "text": "",
                              "note": "Request failed: %s" % str(e)[:200]})
    return pages


def strip_shared_lines(pages: list) -> dict:
    """Per URL, the page's text with the site's shared chrome removed.

    The homepage is kept WHOLE and deliberately. Its navigation is the single
    most informative thing on the site: the menu is where a company lists the
    industries it sells into and the products it sells, and the first live
    draft got its only filled field out of exactly that. So the nav is
    present once, from the page where it is content, and every other page
    contributes only what is unique to it.

    Needs at least three pages to tell chrome from content. Below that a line
    shared by two pages is as likely to be the thing they have in common
    because it matters.
    """
    ok = [p for p in pages if p.get("status") == harvest.SOURCE_OK
          and (p.get("text") or "").strip()]
    out = {p.get("url"): (p.get("text") or "") for p in ok}
    if len(ok) < 3:
        return out

    counts = collections.Counter()
    for p in ok:
        counts.update({ln.strip() for ln in (p.get("text") or "").splitlines()
                       if ln.strip()})
    cut = max(2, int(round(len(ok) * BOILERPLATE_SHARE)))
    shared = {ln for ln, n in counts.items() if n >= cut}
    if not shared:
        return out

    for p in ok[1:]:
        kept = [ln for ln in (p.get("text") or "").splitlines()
                if ln.strip() and ln.strip() not in shared]
        # A page that is nothing BUT chrome keeps its original text rather
        # than becoming empty. Empty would drop it from the corpus silently,
        # and "we read this and it said nothing new" is worth the few hundred
        # characters it costs to show.
        out[p.get("url")] = "\n".join(kept) if kept else (p.get("text") or "")
    return out


def build_corpus(pages: list) -> str:
    """The readable pages, labelled with the URL each came from.

    The URL heading is not decoration. The model is asked for evidence a
    person can go and find, and "their pricing page says" is only checkable if
    it knew which page it was reading.
    """
    bodies = strip_shared_lines(pages)
    out, total = [], 0
    for p in pages:
        if p.get("status") != harvest.SOURCE_OK:
            continue
        body = (bodies.get(p.get("url")) or "")[:PAGE_CHARS]
        if not body.strip():
            continue
        block = "=== %s ===\n%s" % (p.get("url"), body)
        if total + len(block) > CORPUS_CHARS:
            block = block[:max(0, CORPUS_CHARS - total)]
        out.append(block)
        total += len(block)
        if total >= CORPUS_CHARS:
            break
    return "\n\n".join(out)


def _unreadable_detail(pages: list) -> str:
    bits = []
    for p in pages[:MAX_PAGES]:
        note = (p.get("note") or "").strip() or ("HTTP %s" % p.get("http_status"))
        bits.append("%s: %s" % (p.get("url"), note))
    return ("Nothing on that site could be read, so there is nothing to fill "
            "the form in from. " + " ".join(bits))


# A handful of plausible URLs, in the order the model listed them. Capped and
# de-duplicated for the same reason `_text` caps everything else here: this
# came from a reply, not from something this module fetched itself, so it
# gets the same "trust but bound it" treatment as every other model-supplied
# field rather than being passed straight to the page.
_MAX_SEARCH_SOURCES = 12


def _search_sources(raw) -> list:
    out = []
    for u in (raw or []):
        u = str(u or "").strip()
        if u.lower().startswith(("http://", "https://")) and u not in out:
            out.append(u)
        if len(out) >= _MAX_SEARCH_SOURCES:
            break
    return out


def _shape_draft(parsed: dict, sources: list, pages: list, via: str,
                 forced_note: str = "") -> dict:
    """Turn one model reply into the dict `draft_profile` returns.

    Shared by the direct site-read path and the search fallback below: both
    hand this the same JSON shape, and both owe the reader the same
    discipline -- a field with no evidence is thrown away rather than kept as
    a guess, whichever path found it. `via` ("site" or "search") says which
    one actually produced this draft; `forced_note`, when given, is prepended
    to whatever the model itself wrote in `note` rather than replacing it, so
    the fallback's disclosure survives even if the model's own note is empty.
    """
    wrong = _text(parsed.get("wrong_company"), 600)
    if wrong:
        out = _err("wrong_company", wrong, pages)
        out["sources"] = sources
        return out

    draft = {f: _field(parsed.get(f)) for f in DRAFT_FIELDS}

    # Whatever the model listed, plus anything it left blank without saying so.
    # Both halves matter: the list is what the page shows as "we could not find
    # this", and a field that is empty and unlisted would otherwise read as an
    # oversight by the person filling the form rather than as a real absence.
    unknown = {str(u).strip() for u in (parsed.get("unknown") or [])
               if str(u).strip() in DRAFT_FIELDS}
    unknown |= {f for f in DRAFT_FIELDS if draft[f] is None}

    # Evidence is per field and is only kept for fields that were actually
    # filled. Evidence attached to a blank field is a sentence about something
    # that is not on the form, and the page would have nowhere honest to put it.
    ev_raw = parsed.get("evidence")
    evidence = {}
    if isinstance(ev_raw, dict):
        for f in DRAFT_FIELDS:
            if draft[f] is not None:
                t = _text(ev_raw.get(f), 300)
                if t:
                    evidence[f] = t

    # A filled field with no evidence is a guess wearing a fact's clothes, and
    # neither path here has a search tool free to go looking beyond what it
    # was given or what it just found. The prompt asks for evidence; asking is
    # not the same as getting.
    for f in DRAFT_FIELDS:
        if draft[f] is not None and f not in evidence:
            draft[f] = None
            unknown.add(f)

    classification = str(parsed.get("classification") or "").strip()
    if classification not in rubric.CLASSIFICATIONS:
        classification = None

    note = _text(parsed.get("note"), 600) or ""
    if forced_note:
        note = (forced_note + " " + note).strip() if note else forced_note

    return {
        "draft": draft,
        "evidence": evidence,
        "unknown": sorted(unknown),
        "what_they_sell": _text(parsed.get("what_they_sell"), 400) or "",
        "classification": classification,
        "classification_why": _text(parsed.get("classification_why"), 600) or "",
        "classification_confidence": (
            str(parsed.get("classification_confidence") or "").strip().lower()[:10]
            or None),
        "sources": sources,
        "pages": [{"url": p.get("url"), "status": p.get("status"),
                   "note": p.get("note") or ""} for p in pages],
        "note": note,
        "via": via,
        "error": None,
    }


def _search_draft(name: str, site: str) -> dict | None:
    """The web-search fallback. Returns a parsed reply dict, or None on any
    failure -- the caller reports that exactly as it would have reported the
    direct read failing, since from the reader's side both mean the same
    thing: nothing usable was found.

    `search_count == 0` is treated as a failure, not a thin success. A reply
    that ran no real search answered from the model's own recall regardless
    of what it claims in `evidence`, which is the one thing this whole module
    exists to refuse. See claude_websearch's own note on why `search_count`,
    not the presence of an answer, is the only honest signal of whether a
    search actually happened.
    """
    system = _SYSTEM_SEARCH.format(client_name=name[:200], website=site[:400],
                                   classification_menu=_classification_menu())
    user = ("%s's own site could not be read directly. Search for public "
            "information about them from other sources and fill in the form, "
            "leaving blank anything you cannot find and point to." % name[:200])
    res = claude_websearch.ask(system, user, max_uses=SEARCH_MAX_USES,
                               max_tokens=MAX_TOKENS, timeout=SEARCH_TIMEOUT)
    if res.get("error") or not res.get("search_count"):
        if res.get("error"):
            logger.info("event_intel_intake: search fallback failed for %r: %s",
                        name[:80], res["error"])
        else:
            logger.info("event_intel_intake: search fallback for %r ran no "
                       "real search, discarding", name[:80])
        return None
    parsed = claude_websearch.extract_json(res.get("text") or "",
                                           require="what_they_sell")
    if not isinstance(parsed, dict):
        logger.warning("event_intel_intake: unparsable search fallback for %r "
                       "(blocks=%s, stop=%s)", name[:80],
                       res.get("text_block_count"), res.get("stop_reason"))
        return None
    if not _search_sources(parsed.get("sources")):
        return None
    return parsed


def draft_profile(client_name: str, website: str) -> dict:
    """Propose an intake for one company. Never raises.

    Returns a dict with `draft` (the fields to put in the form), `evidence`
    (per field, what was read), `unknown` (fields deliberately left empty),
    `classification` plus its reasoning, `sources` (the pages actually read,
    or the URLs a search fallback actually visited), `pages` (every page this
    module itself tried, with its status), `via` ("site" or "search") and
    `error`.

    `classification` is a PROPOSAL. Nothing in this module or its route saves
    it. See the module docstring for why that distinction is the whole point.
    """
    name = str(client_name or "").strip()
    site = str(website or "").strip()
    if not name:
        return _err("bad_request", "A company name is required.")
    if not site.lower().startswith(("http://", "https://")):
        # Not a nicety. The site is the only thing that separates two firms
        # with one name, so a draft without one is guessing which company it
        # is before it starts guessing anything else.
        return _err("bad_request",
                    "A website starting with http:// or https:// is required. "
                    "It is the only thing that tells two companies with the "
                    "same name apart.")

    pages = read_site(site)
    read = [p for p in pages if p.get("status") == harvest.SOURCE_OK]
    corpus = build_corpus(pages)

    if not corpus:
        # The direct read failed. See the module docstring's "WHEN THE SITE
        # ITSELF WON'T COOPERATE" section: this is exactly the case a bot
        # block or an overloaded origin produces, and it says nothing about
        # whether the company is real or findable. Try search before giving
        # up, and if THAT also finds nothing, report the original, honest
        # failure rather than the fallback's.
        parsed = _search_draft(name, site)
        if parsed is not None:
            return _shape_draft(
                parsed, _search_sources(parsed.get("sources")), pages, "search",
                forced_note=("%s's own site could not be read directly, so "
                            "this draft comes from public search results "
                            "instead. Check it a little more carefully."
                            % name[:200]))
        return _err("unreadable", _unreadable_detail(pages), pages)

    system = _SYSTEM.format(client_name=name[:200], website=site[:400],
                            classification_menu=_classification_menu())
    user = ("Fill in the form for %s from these pages, and leave blank "
            "anything they do not tell you.\n\n%s" % (name[:200], corpus))

    res = claude_websearch.ask(system, user, max_uses=MAX_USES,
                               max_tokens=MAX_TOKENS, timeout=TIMEOUT)
    if res.get("error"):
        e = res["error"]
        return _err(e["kind"], e["detail"], pages)

    parsed = claude_websearch.extract_json(res.get("text") or "",
                                           require="what_they_sell")
    if not isinstance(parsed, dict):
        logger.warning("event_intel_intake: unparsable draft for %r "
                       "(blocks=%s, stop=%s)", name[:80],
                       res.get("text_block_count"), res.get("stop_reason"))
        return _err("unparsable", "The pages were read but the answer could "
                                  "not be understood, so nothing was filled "
                                  "in.", pages)

    # Ours, not the model's. It was handed a fixed set of pages and had no way
    # to open another, so what was read is a fact this module owns rather than
    # a claim it has to take on trust.
    sources = [p.get("url") for p in read if p.get("url")]

    return _shape_draft(parsed, sources, pages, "site")
