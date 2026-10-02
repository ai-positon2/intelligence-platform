"""Attendees: the people who were at an event, or said they would be.

Events do not publish who bought a ticket, and nothing here pretends they
do. What can be shown is narrower and more useful: every person for whom
there is public proof of being there, each with that proof beside them.

    event    the event's own pages name them: its speakers, hosts and judges
    self     their own public post says they are going, are there, went, are
             speaking, or are on their company's stand
    others   someone else's public post names them as there ("great to meet
             Jane Doe at ...", "our CEO John Smith is speaking at ...")
    staff    they work at a company the event lists as exhibiting or
             sponsoring. Likely on site, never confirmed, and always shown
             apart from the other three

Where the proof comes from: the event roster this agent already read, a
LinkedIn post search through the workspace's connected LinkedIn account
(Unipile), a public web search, and Apollo's free people search at the
listed companies. A model reads each post and says who it shows at the
event; nothing it says is kept unless the words it quotes are in the post
and the person it names is in the post too. The model never supplies a
name, a title or a profile link that the post did not.

Each source fails on its own. A LinkedIn account that is not connected, a
search that errors or an Apollo key that is missing is recorded as that,
and the rest of the list is still built.
"""

from __future__ import annotations

import contextvars
import logging
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

BASIS_EVENT = "event"
BASIS_SELF = "self"
BASIS_OTHERS = "others"
BASIS_STAFF = "staff"
BASIS_ORDER = (BASIS_EVENT, BASIS_SELF, BASIS_OTHERS, BASIS_STAFF)
CONFIRMED = (BASIS_EVENT, BASIS_SELF, BASIS_OTHERS)

ST_SPEAKING = "speaking"
ST_EXHIBITING = "exhibiting"
ST_ATTENDING = "attending"
ST_ATTENDED = "attended"
ST_ORGANISING = "organising"
ST_LISTED = "listed"
ST_STAFF = "staff"
# Strongest first: a person who spoke also attended, and is shown as a speaker.
STATUS_ORDER = (ST_SPEAKING, ST_ORGANISING, ST_EXHIBITING, ST_ATTENDING,
                ST_ATTENDED, ST_LISTED, ST_STAFF)
POST_STATUSES = (ST_SPEAKING, ST_EXHIBITING, ST_ATTENDING, ST_ATTENDED, ST_ORGANISING)

EDITION_THIS = "this"
EDITION_EARLIER = "earlier"
EDITION_UNCLEAR = "unclear"
EDITIONS = (EDITION_THIS, EDITION_EARLIER, EDITION_UNCLEAR)

POSTS_PER_QUERY = 150
POSTS_PER_PAGE = 50
MAX_POSTS = 300
BATCH = 20
WORKERS = 4
POST_CHARS = 1400
QUOTE_CHARS = 240
STAFF_COMPANIES = 60
STAFF_PER_COMPANY = 3
STAFF_CHUNK = 10
# Who a company sends to its own stand, or who decides to: Apollo's own
# seniority names. Without a filter the free search returns whoever Apollo
# ranks first, which at a large exhibitor is an intern as often as not.
STAFF_SENIORITIES = ["owner", "founder", "c_suite", "partner", "vp", "head", "director"]
WEB_SEARCHES = 6

STAFF_ROLES = ("exhibitor", "sponsor", "partner")

# The words the page and the CSV use. "Attendee" is never claimed for a
# staff row: working at an exhibitor is not proof of being on its stand.
BASIS_LABELS = {
    BASIS_EVENT: "Named by the event",
    BASIS_SELF: "Said so publicly",
    BASIS_OTHERS: "Named by someone who was there",
    BASIS_STAFF: "Works at an exhibitor or sponsor (not confirmed)",
}
STATUS_LABELS = {
    ST_SPEAKING: "Speaking", ST_EXHIBITING: "On the stand", ST_ATTENDING: "Going",
    ST_ATTENDED: "Went", ST_ORGANISING: "Organising", ST_LISTED: "Listed by the event",
    ST_STAFF: "Not confirmed",
}
EDITION_LABELS = {EDITION_THIS: "This edition", EDITION_EARLIER: "An earlier edition",
                  EDITION_UNCLEAR: "Edition not stated"}


# ── text helpers ──────────────────────────────────────────────────────────

def _fold(text: str) -> str:
    """Lowercase, accents removed, whitespace collapsed. "José  Álvarez"
    and "jose alvarez" compare equal; nothing else is changed."""
    t = unicodedata.normalize("NFKD", str(text or ""))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t).strip().lower()


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", _fold(text))


def in_text(fragment: str, text: str) -> bool:
    """Whether `fragment` appears in `text`, ignoring case, accents,
    whitespace and an ellipsis the model put where it shortened a quote.
    Every piece must be there in order; a quote stitched from words that
    are each somewhere in the post is not a quote."""
    frag, body = _fold(fragment), _fold(text)
    if not frag or not body:
        return False
    pieces = [p.strip(" .,;:-") for p in re.split(r"\.\.\.|…", frag)]
    pieces = [p for p in pieces if p]
    if not pieces or sum(len(p) for p in pieces) < 12:
        return False
    at = 0
    for p in pieces:
        found = body.find(p, at)
        if found < 0:
            return False
        at = found + len(p)
    return True


def name_key(name: str) -> str:
    return _letters(name)


def linkedin_key(url: str) -> str:
    """The /in/<slug> of a LinkedIn profile URL, or "" for anything else."""
    m = re.search(r"linkedin\.com/in/([^/?#]+)", str(url or ""), re.I)
    return m.group(1).lower().rstrip("/") if m else ""


def split_headline(headline: str) -> tuple[str | None, str | None]:
    """A LinkedIn headline's job title and employer, when it states them in
    the usual "Title at Company" or "Title @ Company" form. Anything else is
    left as the title alone: "Helping founders scale | Speaker" names no
    employer, and inventing one from it would be a guess."""
    h = str(headline or "").strip()
    if not h:
        return None, None
    first = re.split(r"\s[|•·]\s|\s\|\s?|\|", h)[0].strip()
    m = re.match(r"^(.{2,120}?)\s+(?:at|@)\s+(.{2,80})$", first, re.I)
    if m:
        return m.group(1).strip(" ,-"), m.group(2).strip(" ,-")
    return first[:160] or None, None


# ── what the event is called ──────────────────────────────────────────────

_YEAR = re.compile(r"\b(19|20)\d{2}\b")


def event_terms(event: dict) -> list[str]:
    """The names a post would use for this event: its name, its name without
    the year, and the hashtag form of each ("#WebSummitQatar")."""
    name = re.sub(r"\s+", " ", str(event.get("name") or "")).strip()
    if not name:
        return []
    bare = re.sub(r"\s+", " ", _YEAR.sub("", name)).strip(" -,:")
    out = []
    for n in (name, bare):
        if n and n not in out:
            out.append(n)
    for n in list(out):
        tag = "#" + re.sub(r"[^A-Za-z0-9]+", "", n)
        if len(tag) > 4 and tag not in out:
            out.append(tag)
    return out


def mentions_event(text: str, terms: list[str]) -> bool:
    """Whether a post names the event at all. The LinkedIn search matches
    loosely (a post about "the summit" and "the web" comes back for
    "Web Summit"), and a post that never names the event is not proof of
    anything about it."""
    body = _fold(text)
    flat = _letters(text)
    for t in terms:
        if t.startswith("#"):
            if len(t) > 5 and t[1:].lower() in flat:
                return True
        elif _fold(t) in body:
            return True
    return False


def _as_date(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")[:25]).date()
    except ValueError:
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def settle_edition(model_edition: str, status: str, posted_at, event: dict,
                   today: date | None = None) -> str:
    """Which edition a post is about, with the dates overruling the model
    where they can. A post written more than ten months before this edition
    starts is about an earlier one; "I was there" about an edition that has
    not happened yet is about an earlier one; and "I'll be there" written
    long after this edition ended is about a later one, which is no proof
    for this one either way."""
    today = today or date.today()
    start = _as_date(event.get("starts_on"))
    end = _as_date(event.get("ends_on")) or start
    posted = _as_date(posted_at)
    edition = model_edition if model_edition in EDITIONS else EDITION_UNCLEAR
    if start and posted and posted < start - timedelta(days=300):
        return EDITION_EARLIER
    if start and start > today and status == ST_ATTENDED:
        return EDITION_EARLIER
    if end and posted and posted > end + timedelta(days=150) and status in (
            ST_ATTENDING, ST_SPEAKING, ST_EXHIBITING):
        return EDITION_UNCLEAR
    if (start and posted and edition == EDITION_UNCLEAR
            and start - timedelta(days=200) <= posted <= (end or start) + timedelta(days=45)):
        # Written in the run-up to this edition or during it, and naming it.
        return EDITION_THIS
    return edition


# ── 1. the people the event itself names ─────────────────────────────────

_PERSON_STATUS = {"speaker": ST_SPEAKING, "attendee_declared": ST_ATTENDING}


def named_by_event(participants: list[dict], event_id=None) -> list[dict]:
    """Every roster row that names a person, as an attendee with the event's
    own page as the proof. A company row with a contact name on it counts:
    the event printed that person against that company."""
    from .event_intel_store import ROLE_LABELS
    out = []
    for p in participants:
        if event_id is not None and p.get("event_id") != event_id:
            continue
        name = str(p.get("person_name") or "").strip()
        if not name or len(name_key(name)) < 3:
            continue
        ev = p.get("evidence") or {}
        detail = ev.get("profile_detail") or {}
        look = ev.get("profile_lookup") or {}
        org = str(p.get("org_name") or "").strip()
        company = org if org and name_key(org) != name_key(name) else None
        tags = [str(t) for t in (detail.get("tags") or [])]
        past = any(re.match(r"^past\b", t, re.I) for t in tags)
        role = p.get("role") or ""
        out.append({
            "name": name[:200],
            "title": p.get("person_title") or None,
            "company": company,
            "company_domain": p.get("org_domain") or None,
            # A LinkedIn link off the event's profile page belongs to the
            # person only when the row is the person's own profile; on a
            # company row it is the company's page.
            "linkedin": (detail.get("linkedin") if not company and
                         linkedin_key(detail.get("linkedin")) else None),
            "basis": BASIS_EVENT,
            "status": _PERSON_STATUS.get(role, ST_LISTED),
            "edition": EDITION_EARLIER if past else EDITION_THIS,
            "proof": [{
                "kind": "event_page",
                "label": "Listed by the event as %s" % ROLE_LABELS.get(role, role).lower(),
                "url": look.get("profile_url") or p.get("source_url"),
            }],
        })
    return out


# ── 2. what people posted on LinkedIn ────────────────────────────────────

def _items_from(payload) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "elements", "results", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _cursor_of(payload):
    if isinstance(payload, dict):
        c = payload.get("cursor")
        if isinstance(c, str) and c:
            return c
        paging = payload.get("paging")
        if isinstance(paging, dict) and isinstance(paging.get("cursor"), str):
            return paging["cursor"] or None
    return None


def linkedin_post(item: dict) -> dict | None:
    """One LinkedIn search result as {id, url, text, posted_at, author}.
    None for anything without text or an author, and for a post by a company
    page: a company announcing its stand is not a person going."""
    if not isinstance(item, dict):
        return None
    pid = str(item.get("social_id") or item.get("id") or item.get("urn") or "").strip()
    text = str(item.get("text") or item.get("commentary") or "").strip()
    author = item.get("author") or {}
    if not pid or not text or not isinstance(author, dict):
        return None
    if author.get("is_company") or str(author.get("type") or "").lower() == "company":
        return None
    name = str(author.get("name") or "").strip()
    if not name:
        first, last = author.get("first_name"), author.get("last_name")
        name = " ".join(x for x in (first, last) if x).strip()
    if not name:
        return None
    slug = str(author.get("public_identifier") or "").strip()
    url = (author.get("public_profile_url") or author.get("profile_url")
           or ("https://www.linkedin.com/in/%s" % slug if slug else None))
    return {
        "id": pid,
        "url": item.get("share_url") or item.get("post_url") or item.get("url"),
        "text": text,
        "posted_at": item.get("parsed_datetime") or item.get("posted_at"),
        "author": {"name": name[:200],
                   "headline": str(author.get("headline") or author.get("occupation")
                                   or "").strip()[:300] or None,
                   "url": url if linkedin_key(url or "") else None},
    }


def linkedin_queries(event: dict) -> list[str]:
    terms = event_terms(event)
    plain = [t for t in terms if not t.startswith("#")]
    tags = [t for t in terms if t.startswith("#")]
    out = []
    if plain:
        out.append('"%s"' % plain[-1])          # the name without the year
    if tags:
        out.append(tags[-1])
    return out


def search_linkedin(event: dict, deadline: float | None = None,
                    search=None, account_id: str | None = None) -> dict:
    """Posts that name the event, from LinkedIn's own post search.

    Returns {"posts": [...], "searched": n, "error": str|None}. `search`
    stands in for unipile_client.search_posts in tests."""
    out = {"posts": [], "searched": 0, "returned": 0, "error": None}
    if search is None:
        from . import unipile_client, unipile_transport
        try:
            account_id = account_id or unipile_transport.account_for_platform("linkedin")
        except Exception as e:  # the vendor being down is a finding, not a crash
            logger.warning("event_intel_attendees: LinkedIn account lookup failed: %s", e)
            account_id = None
        if not account_id:
            out["error"] = "no_account"
            return out
        search = unipile_client.search_posts
        describe = unipile_client.describe_error
    else:
        def describe(err):
            return str(err)
    terms = event_terms(event)
    seen = set()
    for q in linkedin_queries(event):
        cursor, got = None, 0
        while got < POSTS_PER_QUERY and len(out["posts"]) < MAX_POSTS:
            if deadline and time.monotonic() > deadline:
                return out
            try:
                data, err = search(account_id, keywords=q, cursor=cursor, limit=POSTS_PER_PAGE)
            except Exception as e:
                logger.warning("event_intel_attendees: LinkedIn search %r failed: %s", q, e)
                data, err = None, str(e)
            out["searched"] += 1
            if err is not None:
                out["error"] = out["error"] or (describe(err) or "search_failed")
                break
            items = _items_from(data)
            out["returned"] += len(items)
            if not items:
                break
            got += len(items)
            for item in items:
                post = linkedin_post(item)
                if not post or post["id"] in seen:
                    continue
                if not mentions_event(post["text"], terms):
                    continue
                seen.add(post["id"])
                out["posts"].append(post)
            cursor = _cursor_of(data)
            if not cursor:
                break
    return out


_POST_SYSTEM = """You read public LinkedIn posts that mention one event, and \
say which people each post shows at that event, or going to it.

Rules:
1. Use only the words of the post. Never add a person, title or company the \
post does not state.
2. The AUTHOR: list them when they say in their own words that they are \
going, are there, were there, are speaking, are hosting, or are on their \
company's stand at THIS event. "We're exhibiting" from a person counts as \
exhibiting. Liking, sharing a link to, or advertising the event does not.
3. OTHER PEOPLE: list a person the post names in full ("Jane Doe") and says \
was, is or will be at the event: met there, spoke there, joined the author \
there, is on the stand. Not people thanked or tagged without saying they \
were there.
4. same_event is false when the post is about a different event or a \
different edition city of the same series (for example Web Summit Lisbon \
when the event is Web Summit Qatar). Then list nobody.
5. edition: "this" for the edition given below, "earlier" for an earlier \
year, "unclear" when the post does not say.
6. status, one of: speaking, exhibiting, attending (going or there now), \
attended (was there), organising (works for the organiser).
7. quote: the exact words from the post that show it, copied character for \
character, at most 200 characters. Never paraphrase.
8. title and company for a named person only when the post says them.
9. The organiser's own promotion, ticket offers, job ads and news about the \
event name nobody: return an empty people list.

Reply with JSON only:
{"posts": [{"id": "p1", "same_event": true, "edition": "this", "people": \
[{"who": "author", "name": null, "status": "attending", "title": null, \
"company": null, "quote": "..."}]}]}
For the author, leave name null: it is taken from the post itself."""


def _event_brief(event: dict) -> str:
    bits = [str(event.get("name") or "")]
    when = " to ".join(str(x) for x in (event.get("starts_on"), event.get("ends_on")) if x)
    if when:
        bits.append("dates: " + when)
    where = ", ".join(str(x) for x in (event.get("city"), event.get("country")) if x)
    if where:
        bits.append("place: " + where)
    return "; ".join(b for b in bits if b)


def _classify_batch(batch: list[tuple[str, dict]], event: dict, today: date) -> dict:
    from . import claude_websearch
    lines = []
    for ref, post in batch:
        a = post["author"]
        lines.append("--- %s\nauthor: %s%s\nposted: %s\n%s" % (
            ref, a["name"], (" (" + a["headline"] + ")") if a.get("headline") else "",
            post.get("posted_at") or "unknown", post["text"][:POST_CHARS]))
    user = ("Event: %s\nToday: %s\n\n%s" % (_event_brief(event), today.isoformat(),
                                             "\n\n".join(lines)))
    res = claude_websearch.ask(_POST_SYSTEM, user, max_uses=0, max_tokens=6000, timeout=150.0)
    spend = claude_websearch.spend_of(res)
    if res.get("error"):
        return {"posts": None, "error": (res["error"] or {}).get("kind") or "error", "spend": spend}
    parsed = claude_websearch.extract_json(res.get("text") or "", require="posts")
    if not isinstance(parsed, dict):
        logger.warning("event_intel_attendees: unparsable post reading: %r",
                       (res.get("text") or "")[:300])
        return {"posts": None, "error": "unparsable", "spend": spend}
    return {"posts": parsed.get("posts") or [], "error": None, "spend": spend}


def people_in_posts(posts: list[dict], event: dict, today: date | None = None,
                    classify=None) -> dict:
    """Ask a model who each post shows at the event, and keep only what the
    post itself supports. Returns {"people": [...], "batches": n,
    "failed": n, "spend": [...]}."""
    today = today or date.today()
    classify = classify or _classify_batch
    refs = [("p%d" % (i + 1), p) for i, p in enumerate(posts)]
    by_ref = dict(refs)
    batches = [refs[i:i + BATCH] for i in range(0, len(refs), BATCH)]
    results = []
    if batches:
        with ThreadPoolExecutor(max_workers=min(WORKERS, len(batches))) as pool:
            # Each batch runs in a copy of this context, so a call made inside
            # a worker job is still recorded on that job's ledger.
            futures = [pool.submit(contextvars.copy_context().run, classify, b, event, today)
                       for b in batches]
            results = [f.result() for f in futures]
    out = {"people": [], "batches": len(batches), "failed": 0, "spend": [],
           "rejected": 0}
    for res in results:
        out["spend"].append(res.get("spend"))
        if res.get("posts") is None:
            out["failed"] += 1
            continue
        for row in res["posts"]:
            if not isinstance(row, dict):
                continue
            post = by_ref.get(str(row.get("id") or ""))
            if not post or row.get("same_event") is False:
                continue
            for person in row.get("people") or []:
                got = _person_from_post(person, post, row.get("edition"), event, today)
                if got is None:
                    out["rejected"] += 1
                elif got:
                    out["people"].append(got)
    return out


def _person_from_post(person, post, edition, event, today):
    """One person a model read off a post, checked against the post. None
    when the check fails (counted), {} when there is nothing to keep."""
    if not isinstance(person, dict):
        return None
    status = str(person.get("status") or "").strip().lower()
    quote = str(person.get("quote") or "").strip()[:QUOTE_CHARS]
    if status not in POST_STATUSES:
        return {}
    if not in_text(quote, post["text"]):
        return None
    who = str(person.get("who") or "").strip().lower()
    author = post["author"]
    if who == "author":
        title, company = split_headline(author.get("headline"))
        name, linkedin, basis = author["name"], author.get("url"), BASIS_SELF
    else:
        name = re.sub(r"\s+", " ", str(person.get("name") or "")).strip()
        # A full name, as written in the post: two words at least, and there.
        if len(name.split()) < 2 or _fold(name) not in _fold(post["text"]):
            return None
        if name_key(name) == name_key(author["name"]):
            title, company = split_headline(author.get("headline"))
            name, linkedin, basis = author["name"], author.get("url"), BASIS_SELF
        else:
            title = str(person.get("title") or "").strip() or None
            company = str(person.get("company") or "").strip() or None
            # Kept only when the post says them; a title the model inferred
            # from nothing is dropped rather than shown as the post's word.
            if title and _fold(title) not in _fold(post["text"]):
                title = None
            if company and _fold(company) not in _fold(post["text"]):
                company = None
            linkedin, basis = None, BASIS_OTHERS
    return {
        "name": name[:200], "title": title, "company": company, "company_domain": None,
        "linkedin": linkedin, "basis": basis, "status": status,
        "edition": settle_edition(str(edition or ""), status, post.get("posted_at"), event, today),
        "proof": [{
            "kind": "linkedin_post",
            "label": ("Their own LinkedIn post" if basis == BASIS_SELF
                      else "Named in a LinkedIn post by %s" % author["name"]),
            "url": post.get("url"), "quote": quote,
            "posted_at": str(post.get("posted_at") or "")[:10] or None,
        }],
    }


# ── 3. public pages found by web search ──────────────────────────────────

_WEB_SYSTEM = """You search the public web for named people who were at one \
event, or say they will be: speakers announced in company news, people who \
wrote that they attended, exhibitors' staff announced for the stand, \
published interviews recorded there. Search for posts, articles, press \
releases and blogs, not the event's own website.

Rules:
1. Only people named in full on a page you found in these searches.
2. quote: the exact words on that page that show the person at the event, \
copied character for character, at most 200 characters.
3. url: the page the quote is on, exactly as the search returned it.
4. status, one of: speaking, exhibiting, attending, attended, organising.
5. edition: "this" for the edition given, "earlier" for an earlier year, \
"unclear" when the page does not say.
6. title and company only when that page states them.
7. Return at most 40 people. Return an empty list rather than a guess.

Reply with JSON only:
{"people": [{"name": "...", "title": null, "company": null, "status": \
"speaking", "edition": "this", "url": "...", "quote": "..."}]}"""


def search_web(event: dict, event_host: str = "", today: date | None = None,
               fetch=None, ask=None) -> dict:
    """People named on public pages, each re-checked by opening the page.

    A quote the page itself does not contain is dropped, so is a page the
    searches never returned. Pages that cannot be opened (LinkedIn's own
    post pages mostly refuse) are dropped too: a quote that cannot be
    checked is not kept as one. Returns {"people", "found", "checked",
    "error", "spend"}."""
    from . import claude_websearch
    from .event_intel_harvest import fetch_page
    today = today or date.today()
    fetch = fetch or fetch_page
    ask = ask or claude_websearch.ask
    user = "Event: %s\nToday: %s" % (_event_brief(event), today.isoformat())
    res = ask(_WEB_SYSTEM, user, max_uses=WEB_SEARCHES, max_tokens=6000, timeout=240.0)
    out = {"people": [], "found": 0, "checked": 0, "unopened": 0, "error": None,
           "spend": claude_websearch.spend_of(res)}
    if res.get("error"):
        out["error"] = (res["error"] or {}).get("kind") or "error"
        return out
    parsed = claude_websearch.extract_json(
        claude_websearch.remove_citation_tags(res.get("text") or ""), require="people")
    if not isinstance(parsed, dict):
        out["error"] = "unparsable"
        return out
    returned = {str(u).rstrip("/") for u in (res.get("result_urls") or [])}
    pages: dict = {}
    for person in (parsed.get("people") or [])[:40]:
        if not isinstance(person, dict):
            continue
        out["found"] += 1
        url = str(person.get("url") or "").strip()
        name = re.sub(r"\s+", " ", str(person.get("name") or "")).strip()
        quote = str(person.get("quote") or "").strip()[:QUOTE_CHARS]
        status = str(person.get("status") or "").strip().lower()
        if (not url or url.rstrip("/") not in returned or len(name.split()) < 2
                or status not in POST_STATUSES):
            continue
        host = re.sub(r"^www\.", "", (re.match(r"https?://([^/]+)", url) or [None, ""])[1].lower())
        if event_host and (host == event_host or host.endswith("." + event_host)):
            continue  # the event's own pages were read already, with their own proof
        if url not in pages:
            got = fetch(url)
            pages[url] = got.get("text") if got.get("status") == "ok" else None
        text = pages[url]
        if not text:
            out["unopened"] += 1
            continue
        if not (in_text(quote, text) and _fold(name) in _fold(text)):
            continue
        out["checked"] += 1
        title = str(person.get("title") or "").strip() or None
        company = str(person.get("company") or "").strip() or None
        out["people"].append({
            "name": name[:200],
            "title": title if title and _fold(title) in _fold(text) else None,
            "company": company if company and _fold(company) in _fold(text) else None,
            "company_domain": None, "linkedin": None,
            "basis": BASIS_OTHERS, "status": status,
            "edition": settle_edition(str(person.get("edition") or ""), status, None, event, today),
            "proof": [{"kind": "web_page", "label": "Named on %s" % (host or "a public page"),
                       "url": url, "quote": quote}],
        })
    return out


# ── 4. people at the exhibiting and sponsoring companies ─────────────────

def staff_at_companies(participants: list[dict], event_id=None, find=None,
                       limit: int = STAFF_COMPANIES) -> dict:
    """Senior people at the companies the event lists as exhibiting,
    sponsoring or partnering, from Apollo's free people search. Who staffs
    a stand is never published, so none of these is confirmed attending,
    and every row says so."""
    from . import event_intel_enrich
    find = find or event_intel_enrich.find_people
    order = {"sponsor": 0, "exhibitor": 1, "partner": 2}
    companies: dict = {}
    for p in sorted(participants, key=lambda p: order.get(p.get("role"), 9)):
        if event_id is not None and p.get("event_id") != event_id:
            continue
        d = p.get("org_domain")
        if d and p.get("role") in STAFF_ROLES and d not in companies:
            companies[d] = p
    domains = list(companies)[:limit]
    out = {"people": [], "companies": len(domains), "skipped": max(0, len(companies) - limit),
           "error": None}
    from .event_intel_store import ROLE_LABELS
    for i in range(0, len(domains), STAFF_CHUNK):
        chunk = domains[i:i + STAFF_CHUNK]
        got = find(chunk, per_company=STAFF_PER_COMPANY, seniorities=STAFF_SENIORITIES)
        if got.get("error"):
            out["error"] = got["error"]
            if "APOLLO_API_KEY" in str(got["error"]):
                break
        for d, people in (got.get("by_domain") or {}).items():
            row = companies.get(d) or {}
            for person in people:
                name = str(person.get("name") or "").strip()
                if not name:
                    continue
                out["people"].append({
                    "name": name[:200], "title": person.get("title") or None,
                    "company": row.get("org_name") or d, "company_domain": d,
                    "linkedin": person.get("linkedin") or None,
                    "basis": BASIS_STAFF, "status": ST_STAFF, "edition": EDITION_UNCLEAR,
                    "name_masked": bool(person.get("name_masked")),
                    "proof": [{"kind": "apollo",
                               "label": "Works at %s, listed by the event as %s"
                                        % (row.get("org_name") or d,
                                           ROLE_LABELS.get(row.get("role"), "").lower()),
                               "url": row.get("source_url")}],
                })
    return out


# ── putting it together ──────────────────────────────────────────────────

def _key(person: dict) -> str:
    li = linkedin_key(person.get("linkedin"))
    if li:
        return "li:" + li
    return "n:" + name_key(person.get("name")) + "|" + name_key(person.get("company") or "")


def merge(people: list[dict]) -> list[dict]:
    """One row per person. The same speaker found on the event's page and in
    their own post is one attendee with two proofs, not two attendees.

    Matched on LinkedIn profile first, then on name and company; a row with
    no company joins a same-named row that has one when that name is the
    only one of its kind."""
    rows: dict = {}
    alias: dict = {}
    for p in people:
        k = _key(p)
        k = alias.get(k, k)
        if k not in rows:
            # No row under this key yet: join the one row with the same name
            # whose company and LinkedIn profile do not contradict this one.
            # A staff row needs the same company stated on both sides: "John
            # Smith" named in a post is not, on his name alone, the John Smith
            # Apollo has at an exhibitor, and joining them would hand one
            # person the other's title and profile.
            nk, li = name_key(p.get("name")), linkedin_key(p.get("linkedin"))

            def fits(r):
                if name_key(r["name"]) != nk:
                    return False
                if li and linkedin_key(r.get("linkedin")) and linkedin_key(r.get("linkedin")) != li:
                    return False
                a, b = name_key(p.get("company") or ""), name_key(r.get("company") or "")
                if BASIS_STAFF in ([p["basis"]] + r["bases"]) and not (
                        li and linkedin_key(r.get("linkedin")) == li):
                    return bool(a) and a == b
                return not a or not b or a == b
            matches = [rk for rk, r in rows.items() if fits(r)]
            if len(matches) > 1 and p.get("company"):
                # Several could be them: the one at the same company is.
                matches = [rk for rk in matches if name_key(rows[rk].get("company") or "")
                           == name_key(p["company"])]
            if len(matches) == 1:
                alias[k] = matches[0]
                k = matches[0]
        r = rows.get(k)
        if r is None:
            rows[k] = dict(p, proof=list(p.get("proof") or []), bases=[p["basis"]])
            continue
        if p["basis"] not in r["bases"]:
            r["bases"].append(p["basis"])
        for f in ("title", "company", "company_domain", "linkedin"):
            if not r.get(f) and p.get(f):
                r[f] = p[f]
        r["proof"].extend(x for x in (p.get("proof") or []) if x not in r["proof"])
        if STATUS_ORDER.index(p["status"]) < STATUS_ORDER.index(r["status"]):
            r["status"] = p["status"]
        if EDITION_THIS in (p.get("edition"), r.get("edition")):
            r["edition"] = EDITION_THIS
        elif r.get("edition") == EDITION_UNCLEAR and p.get("edition") == EDITION_EARLIER:
            r["edition"] = EDITION_EARLIER
    out = []
    for r in rows.values():
        r["bases"].sort(key=BASIS_ORDER.index)
        r["basis"] = r["bases"][0]
        r["proof"] = r["proof"][:6]
        out.append(r)
    out.sort(key=lambda r: (BASIS_ORDER.index(r["basis"]),
                            {EDITION_THIS: 0, EDITION_UNCLEAR: 1, EDITION_EARLIER: 2}.get(
                                r.get("edition"), 1),
                            STATUS_ORDER.index(r["status"]), _fold(r["name"])))
    return out


def for_storage(rows: list[dict]) -> list[dict]:
    return [{
        "name": r["name"], "title": r.get("title"), "company": r.get("company"),
        "company_domain": r.get("company_domain"), "linkedin": r.get("linkedin"),
        "basis": r["basis"], "status": r.get("status"), "edition": r.get("edition"),
        "evidence": {"bases": r.get("bases") or [r["basis"]], "proof": r.get("proof") or [],
                     "name_masked": bool(r.get("name_masked"))},
    } for r in rows]


def gather(event: dict, participants: list[dict], event_host: str = "",
           deadline: float | None = None, today: date | None = None,
           sources=None) -> dict:
    """Every attendee this agent can show for one event, and how each source
    went. `sources` overrides the four collectors in tests:
    {"linkedin": fn(event, deadline), "classify": fn(batch, event, today),
     "web": fn(event, host, today), "staff": fn(participants, event_id)}."""
    from . import claude_websearch
    sources = sources or {}
    today = today or date.today()
    eid = event.get("id")
    people = named_by_event(participants, eid)
    report = {"event": len(people)}
    spend = []

    li = (sources.get("linkedin") or search_linkedin)(event, deadline)
    report["linkedin"] = {"posts": len(li.get("posts") or []), "searches": li.get("searched", 0),
                          "returned": li.get("returned", 0), "error": li.get("error")}
    if li.get("posts"):
        read = people_in_posts(li["posts"], event, today, classify=sources.get("classify"))
        spend.extend(read["spend"])
        people.extend(read["people"])
        report["linkedin"].update(people=len(read["people"]), batches=read["batches"],
                                  failed_batches=read["failed"], rejected=read["rejected"])

    try:
        web = (sources.get("web") or search_web)(event, event_host, today)
    except Exception as e:
        logger.exception("event_intel_attendees: web search failed")
        web = {"people": [], "error": "error", "spend": None}
    spend.append(web.get("spend"))
    people.extend(web.get("people") or [])
    report["web"] = {k: web.get(k) for k in ("found", "checked", "unopened", "error")}
    report["web"]["people"] = len(web.get("people") or [])

    try:
        staff = (sources.get("staff") or staff_at_companies)(participants, eid)
    except Exception as e:
        logger.exception("event_intel_attendees: staff lookup failed")
        staff = {"people": [], "error": str(e)[:200]}
    people.extend(staff.get("people") or [])
    report["staff"] = {"companies": staff.get("companies", 0), "skipped": staff.get("skipped", 0),
                       "people": len(staff.get("people") or []), "error": staff.get("error")}

    rows = merge(people)
    total = claude_websearch.spend_sum(*[s for s in spend if s])
    report["usd"] = round(claude_websearch.spend_usd(total), 4)
    report["counts"] = counts(rows)
    return {"rows": for_storage(rows), "report": report, "spend": total}


def counts(rows: list[dict]) -> dict:
    """How many of each kind, for the headline. A row counts once, under its
    strongest proof."""
    c = {b: 0 for b in BASIS_ORDER}
    this = earlier = 0
    for r in rows:
        c[r["basis"]] = c.get(r["basis"], 0) + 1
        if r["basis"] in CONFIRMED:
            if r.get("edition") == EDITION_THIS:
                this += 1
            elif r.get("edition") == EDITION_EARLIER:
                earlier += 1
    c["confirmed"] = sum(c[b] for b in CONFIRMED)
    c["this_edition"] = this
    c["earlier_edition"] = earlier
    return c


def note(report: dict) -> str:
    """What the search found and what it could not do, in one paragraph."""
    c = report.get("counts") or {}
    bits = []
    li = report.get("linkedin") or {}
    if li.get("error") == "no_account":
        bits.append("LinkedIn was not searched: no LinkedIn account is connected "
                    "to this workspace.")
    elif li.get("error"):
        bits.append("The LinkedIn search stopped part-way (%s), so posts after "
                    "that point were not read." % li["error"])
    if li.get("failed_batches"):
        bits.append("%d of %d groups of LinkedIn posts could not be read, so some "
                    "posts were not checked." % (li["failed_batches"], li.get("batches", 0)))
    web = report.get("web") or {}
    if web.get("error"):
        bits.append("The web search did not finish, so public pages beyond LinkedIn "
                    "were not checked.")
    st = report.get("staff") or {}
    if st.get("error") and "APOLLO_API_KEY" in str(st["error"]):
        bits.append("People at the exhibiting companies were not looked up: company "
                    "data is not switched on for this workspace.")
    elif st.get("error"):
        bits.append("The lookup of people at the exhibiting companies stopped part-way.")
    if st.get("skipped"):
        bits.append("People were looked up at the first %d listed companies; %d more "
                    "were not reached." % (st.get("companies", 0), st["skipped"]))
    if not c.get("confirmed"):
        bits.insert(0, "No one was found with public proof of being at this event.")
    return " ".join(bits)


def find_for_run(run_id: int, email: str, deadline_seconds: float = 600.0) -> dict:
    """Search every event in a finished run and store what was found.
    Never raises; the result is what the scan record keeps."""
    from . import event_intel_store as store
    from .event_intel_pipeline import _host, attendee_inputs
    deadline = time.monotonic() + deadline_seconds
    run = store.get_run(run_id, email)
    if not run:
        return {"error": "not_found"}
    participants = store.get_participants(run_id)
    result = {"events": [], "usd": 0.0}
    for ev in store.get_events(run_id):
        try:
            event, rows = attendee_inputs(ev, participants)
            got = gather(event, rows, _host(ev.get("website")), deadline=deadline)
        except Exception:
            logger.exception("event_intel_attendees: search failed for run %s", run_id)
            result["events"].append({"event_id": ev.get("id"), "error": "failed"})
            continue
        store.save_attendees(run_id, ev.get("id"), got["rows"])
        rep = dict(got["report"], event_id=ev.get("id"))
        rep["note"] = note(rep)
        result["events"].append(rep)
        result["usd"] += rep.get("usd") or 0
        sp = got.get("spend") or {}
        if sp.get("calls"):
            store.record_account_usage(email, "attendees", calls=sp.get("calls", 0),
                                       usd=rep.get("usd") or 0)
    result["usd"] = round(result["usd"], 4)
    return result
