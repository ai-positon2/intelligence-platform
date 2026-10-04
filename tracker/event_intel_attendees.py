"""Attendees: the people who were at an event, or said they would be.

Events do not publish who bought a ticket, and nothing here pretends they
do. What can be shown is narrower and more useful: every person for whom
there is public proof of being there, each with that proof beside them.

    event    the event's own pages name them: its speakers, hosts and judges
    self     their own public post (LinkedIn or X) says they are going, are
             there, went, are speaking, or are on their company's stand
    others   someone else's public post names them as there ("great to meet
             Jane Doe at ...", "our CEO John Smith is speaking at ...")
    staff    they work at a company the event lists as exhibiting or
             sponsoring. Likely on site, never confirmed, and always shown
             apart from the other three

Where the proof comes from: the event roster this agent already read, a
LinkedIn post search through the workspace's connected LinkedIn account
(Unipile), a public web search, and Apollo's free people search at the
listed companies, and X's own search through the Apify tweet scraper the
Social Media agent already uses. A model reads each post and says who it shows at the
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
import threading
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

POSTS_PER_QUERY = 250
POSTS_PER_PAGE = 50
MAX_POSTS = 900
BATCH = 15
WORKERS = 8
POST_CHARS = 1400
QUOTE_CHARS = 240
STAFF_COMPANIES = 150
STAFF_PER_COMPANY = 3
STAFF_CHUNK = 10
# Who a company sends to its own stand, or who decides to: Apollo's own
# seniority names. Without a filter the free search returns whoever Apollo
# ranks first, which at a large exhibitor is an intern as often as not.
STAFF_SENIORITIES = ["owner", "founder", "c_suite", "partner", "vp", "head", "director"]
# Model calls in flight at once across every source of one search: LinkedIn
# posts, X posts and web pages are all read at the same time now.
_MODEL_SLOTS = threading.BoundedSemaphore(20)
# Whole-call limits, in seconds.
FIND_SECONDS = 240.0
READ_SECONDS = 180.0
RETRY_PAUSE = 2.0

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
    for i, p in enumerate(pieces):
        found = body.find(p, at)
        if found < 0:
            return False
        # What the ellipsis left out may not turn the sentence round: "I was
        # ... at Web Summit" cut from "I was not at Web Summit" says the
        # opposite of the post.
        if i and _NEGATION.search(body[at:found]):
            return False
        at = found + len(p)
    return True


_NEGATION = re.compile(r"\b(?:not|no|never|didn'?t|couldn'?t|can'?t|cannot|won'?t|wasn'?t|"
                       r"weren'?t|isn'?t|aren'?t|unable|missed|missing)\b")
# A wish, a regret or a "no" is nobody's proof of being there, whatever a
# model made of it ("I want to join Web Summit", live run 35).
_NOT_THERE = re.compile(
    r"\b(?:(?:could|can|did|will|would|was|were|is|are|wo|ca)n'?t|cannot|unable to|not able to|"
    r"won'?t be|not (?:going|attending|be|there|make)|missed|missing|skipp(?:ed|ing)|"
    r"(?:want|wanted|wanting|wish|wished|hope|hoping|hoped|would love|'d love|dream|dreaming|"
    r"plan(?:ning)? to apply|apply(?:ing)? to) (?:to )?(?:join|go|attend|be at|be there|"
    r"make it|speak|visit|get to)|wish i (?:was|were|could))\b")


# Negatives that mean yes: "can't wait to see you at Web Summit".
_EAGER = re.compile(
    r"\b(?:can'?t|cannot|couldn'?t) (?:wait|be (?:more )?(?:happier|prouder|excited|more))|"
    r"\b(?:won'?t|wouldn'?t|don'?t|never|can'?t|not) (?:want to )?miss\b|"
    r"\bnot to be missed\b|\bnot only\b|\bno better\b")


def _sentence_of(quote: str, text: str) -> list[tuple[str, str]]:
    """For each place the quote stands in the text: the words of its
    sentence before it, and after it. Both folded."""
    q = _fold(quote)
    # Folded line by line: a speaker card's lines are its sentences.
    body = "\n".join(_fold(line) for line in str(text or "").splitlines())
    pieces = [x.strip(" .,;:-") for x in re.split(r"\.\.\.|…", q) if x.strip(" .,;:-")]
    if not pieces:
        return []
    if body.find(pieces[0]) < 0:
        body = _fold(text)        # a quote that runs over a line break
    out = []
    at = body.find(pieces[0])
    while at >= 0:
        stop = at + len(q)
        window = body[max(0, at - 300):at]
        cut = max(window.rfind(c) for c in ".!?\n")
        before = window[cut + 1:] if cut >= 0 else window
        if cut >= 0 and window[cut] == "\n":
            # A speaker card prints the name on its own line above the
            # title: a short line just above belongs with this one.
            above = window[:cut].rsplit("\n", 1)[-1]
            if len(above.strip()) <= 60 and not re.search(r"[.!?]", above):
                before = above + " " + before
        after = body[stop:stop + 120]
        m = re.search(r"[.!?\n]", after)
        out.append((before, after[:m.start()] if m else after))
        at = body.find(pieces[0], at + 1)
    return out


def denies_presence(quote: str, text: str = "") -> bool:
    """Whether a quote says the person is not going, was not there, or only
    wishes they were, reading the words of its sentence just before it too:
    "make it to Web Summit 2026" cut from "I couldn't make it to Web Summit
    2026" is a no."""
    if _NOT_THERE.search(_EAGER.sub(" ", _fold(quote))):
        return True
    for before, _ in _sentence_of(quote, text):
        lead = before[-40:] + " " + _fold(quote)
        if _NOT_THERE.search(_EAGER.sub(" ", lead)):
            return True
    return False


def tied_to(name: str, quote: str, text: str) -> bool:
    """Whether a quote credited to `name` is about them: it names them, or
    its own sentence does. "Our CTO Tom Lee is on stage at Web Summit"
    is no proof that Satya Nadella, named one sentence earlier, is."""
    n, q = _fold(name), _fold(quote)
    if not n:
        return False
    last = _fold(name.split()[-1]) if name.split() else ""
    def names(span):
        return n in span or (len(last) > 2 and re.search(r"\b%s\b" % re.escape(last), span))
    if names(q):
        return True
    return any(names(before) or names(after) for before, after in _sentence_of(quote, text))


def clean_name(name: str) -> str:
    """A display name without the emoji and symbols people decorate it with
    ("Tyler Denk \U0001F41D", "Ana \u2728 Silva"): letters, marks, digits,
    spaces and the punctuation real names use."""
    out = []
    for c in unicodedata.normalize("NFC", str(name or "")):
        cat = unicodedata.category(c)
        if cat[0] in ("L", "M", "N") or c in " .-'\u2019":
            out.append(c)
        else:
            out.append(" ")
    return re.sub(r"\s+", " ", "".join(out)).strip(" .-")


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


# Places that series name their editions after. A post that puts the
# event's own name next to one of these, and never next to this edition's
# city, is about another edition: on the live Lisbon run "#WebSummit2026"
# posts from May (Vancouver) and June (Rio) were counted for November.
_SERIES_PLACES = (
    "lisbon", "lisboa", "vancouver", "rio", "rio de janeiro", "qatar", "doha", "tokyo",
    "shanghai", "riyadh", "toronto", "sao paulo", "mexico city", "abu dhabi", "dubai",
    "jakarta", "bangalore", "bengaluru", "mumbai", "delhi", "cape town", "nairobi", "lagos",
    "seoul", "manila", "bangkok", "kuala lumpur", "singapore", "hong kong", "sydney",
    "melbourne", "london", "paris", "berlin", "munich", "amsterdam", "barcelona", "madrid",
    "milan", "dublin", "stockholm", "copenhagen", "helsinki", "warsaw", "istanbul",
    "new york", "nyc", "las vegas", "vegas", "san francisco", "austin", "miami", "chicago",
    "boston", "denver", "atlanta", "dallas", "seattle", "orlando", "europe", "asia",
    "africa", "latam", "mena", "apac", "emea", "india", "usa", "middle east", "americas",
    "north america", "china", "japan", "korea", "brazil", "canada", "australia")
_PLACE_ALIASES = {"lisbon": ("lisboa",), "lisboa": ("lisbon",), "qatar": ("doha",),
                  "doha": ("qatar",), "rio de janeiro": ("rio",), "rio": ("rio de janeiro",),
                  "new york": ("nyc",), "las vegas": ("vegas",), "usa": ("us", "america")}


def _own_places(event: dict) -> set:
    own = set()
    for v in (event.get("city"), event.get("country")):
        for part in re.split(r"[,/]", str(v or "")):
            f = _fold(part).strip()
            if f:
                own.add(f)
    name = _fold(event.get("name"))
    own.update(p for p in _SERIES_PLACES if re.search(r"\b%s\b" % re.escape(p), name))
    for p in list(own):
        own.update(_PLACE_ALIASES.get(p, ()))
    return own


def _series_stem(event: dict, own: set) -> str:
    """The event's name with its year and its own place taken off: "Web
    Summit Qatar 2027" is the "web summit" series in Qatar."""
    stem = _YEAR.sub(" ", _fold(event.get("name")))
    for p in sorted(own, key=len, reverse=True):
        stem = re.sub(r"\b%s\b" % re.escape(p), " ", stem)
    return re.sub(r"\s+", " ", stem).strip(" -,:|")


def other_edition(text: str, event: dict) -> bool:
    """Whether the text places this event's series in another city or
    region and never in this edition's own."""
    own = _own_places(event)
    stem = _series_stem(event, own)
    if len(_letters(stem)) < 4:
        return False
    body = _fold(text)
    words = r"\s+".join(re.escape(w) for w in stem.split())
    tag = re.escape(_letters(stem))
    sep = r"(?:\s*(?:in|at|,|\||-|–|:)\s*|\s+)"

    def placed(place):
        p = r"\s+".join(re.escape(w) for w in place.split())
        return bool(re.search(r"\b%s(?:\s*(?:19|20)\d{2})?%s%s\b" % (words, sep, p), body)
                    or re.search(r"#%s(?:\d{2,4})?(?:%s)?%s\b" % (tag, sep, p), body)
                    or re.search(r"#%s(?:\d{2,4})?%s\b" % (tag, re.escape(_letters(place))), body))
    if any(placed(p) for p in own) or any(
            re.search(r"\b%s\b" % re.escape(p), body) for p in own if len(p) > 3):
        return False
    # A region is another edition only for a series that names editions by
    # region ("Money20/20 Europe" against "Money20/20 USA"); "Web Summit in
    # Europe" is just where Lisbon is.
    by_region = bool(own & _REGIONS)
    return any(placed(p) for p in _SERIES_PLACES
               if p not in own and (by_region or p not in _REGIONS))


_REGIONS = {"europe", "asia", "africa", "latam", "mena", "apac", "emea", "india", "usa",
            "middle east", "americas", "north america", "china", "japan", "korea", "brazil",
            "canada", "australia", "us", "america"}


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


TENSE_PAST = "past"
TENSE_NOW = "now"
TENSE_FUTURE = "future"
TENSES = (TENSE_PAST, TENSE_NOW, TENSE_FUTURE)


def settle_edition(model_edition: str, status: str, posted_at, event: dict,
                   today: date | None = None, tense: str | None = None) -> str:
    """Which edition a post is about, with the dates overruling the model
    where they can. A post written more than ten months before this edition
    starts is about an earlier one; "I was there" about an edition that has
    not happened yet is about an earlier one; and "I'll be there" written
    long after this edition ended is about a later one, which is no proof
    for this one either way.

    The tense decides what a date can prove. On the live Lisbon run (run 35,
    November 2026, read in October) half of the "this edition" rows were
    past tense written in the months before: a bio line "Spoken at Web
    Summit", "lessons from #WebSummit2026" after the May edition in
    Vancouver. Something that already happened, said before this edition
    began, is about an earlier one; and only "will be" or "am there" written
    in the run-up is taken as this one when the post names no year."""
    today = today or date.today()
    start = _as_date(event.get("starts_on"))
    end = _as_date(event.get("ends_on")) or start
    posted = _as_date(posted_at)
    edition = model_edition if model_edition in EDITIONS else EDITION_UNCLEAR
    tense = tense if tense in TENSES else (TENSE_PAST if status == ST_ATTENDED else None)
    if start and posted and posted < start - timedelta(days=300):
        return EDITION_EARLIER
    if start and start > today and (status == ST_ATTENDED or tense == TENSE_PAST):
        return EDITION_EARLIER
    if start and posted and tense == TENSE_PAST and posted < start:
        return EDITION_EARLIER
    if start and posted and tense == TENSE_NOW and posted < start - timedelta(days=2):
        return EDITION_EARLIER
    if end and posted and posted > end + timedelta(days=2) and tense in (TENSE_NOW, TENSE_FUTURE):
        return EDITION_UNCLEAR
    if end and posted and posted > end + timedelta(days=150) and tense != TENSE_PAST and status in (
            ST_ATTENDING, ST_SPEAKING, ST_EXHIBITING):
        return EDITION_UNCLEAR
    if (start and posted and edition == EDITION_UNCLEAR and tense in (TENSE_NOW, TENSE_FUTURE)
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
        role_label = ROLE_LABELS.get(role, role).lower()
        url = look.get("profile_url") or p.get("source_url")
        if ev.get("status") == "recovered_by_search" and ev.get("off_site"):
            # Found by search on someone else's site: that page names the
            # person, the event did not, and it need not be this edition's.
            out.append({
                "name": name[:200], "title": p.get("person_title") or None,
                "company": company, "company_domain": p.get("org_domain") or None,
                "linkedin": None, "basis": BASIS_OTHERS,
                # Never "Listed by the event": the event did not list them.
                "status": {"speaker": ST_SPEAKING, "exhibitor": ST_EXHIBITING,
                           "sponsor": ST_EXHIBITING, "partner": ST_EXHIBITING}.get(role, ST_ATTENDING),
                "edition": EDITION_EARLIER if past else EDITION_UNCLEAR,
                "proof": [{"kind": "web_page",
                           "label": "Named as %s on %s" % (role_label, _host_of(url) or "a public page"),
                           "url": url}],
            })
            continue
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
                "label": "Listed by the event as %s" % role_label,
                "url": url,
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
    name = clean_name(name)
    if not name:
        return None
    slug = str(author.get("public_identifier") or "").strip()
    url = (author.get("public_profile_url") or author.get("profile_url")
           or ("https://www.linkedin.com/in/%s" % slug if slug else None))
    return {
        "id": pid,
        "platform": "linkedin",
        "url": item.get("share_url") or item.get("post_url") or item.get("url"),
        "text": text,
        "posted_at": item.get("parsed_datetime") or item.get("posted_at"),
        "author": {"name": name[:200],
                   "headline": str(author.get("headline") or author.get("occupation")
                                   or "").strip()[:300] or None,
                   "url": url if linkedin_key(url or "") else None},
    }


# ── 2b. what people posted on X ──────────────────────────────────────────

X_POSTS = 1000


def _x_date(value):
    """X's own date string ("Wed Feb 04 10:12:00 +0000 2026") or ISO, as ISO."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value), "%a %b %d %H:%M:%S %z %Y").isoformat()
    except ValueError:
        return str(value)


def x_post(item: dict) -> dict | None:
    """One tweet from the Apify scraper in the same shape as linkedin_post.
    None for a retweet (it is someone else's words), for anything without
    text or an author, and for an organisation's account the scraper marks
    as one."""
    if not isinstance(item, dict):
        return None
    pid = str(item.get("id") or item.get("id_str") or item.get("tweetId") or "").strip()
    text = str(item.get("text") or item.get("fullText") or item.get("full_text") or "").strip()
    author = item.get("author") or {}
    if not pid or not text or not isinstance(author, dict):
        return None
    if item.get("isRetweet") or item.get("retweeted_status") or text.startswith("RT @"):
        return None
    if (str(author.get("type") or "").lower() in ("business", "organization", "company")
            or str(author.get("verifiedType") or "").lower() in ("business", "government")):
        return None
    handle = str(author.get("userName") or author.get("username")
                 or author.get("screen_name") or "").lstrip("@").strip()
    name = clean_name(author.get("name"))
    if not name or not handle:
        return None
    return {
        "id": "x:" + pid,
        "platform": "x",
        "url": item.get("url") or item.get("twitterUrl") or (
            "https://x.com/%s/status/%s" % (handle, pid)),
        "text": text,
        "posted_at": _x_date(item.get("createdAt") or item.get("created_at")),
        "author": {"name": name[:200],
                   "headline": str(author.get("description") or "").strip()[:300] or None,
                   "url": None, "x": "https://x.com/" + handle, "handle": handle},
    }


def x_terms(event: dict) -> list[str]:
    """The same two names the LinkedIn search uses, in X's own search
    syntax, with retweets left out at the source."""
    return ["%s -filter:retweets" % q for q in linkedin_queries(event)]


def search_x(event: dict, deadline: float | None = None, run=None) -> dict:
    """Tweets that name the event, from X's own search via the Apify tweet
    scraper. One actor run carries both search terms. `run` stands in for
    apify_transport.run_actor_and_wait in tests."""
    import os
    out = {"posts": [], "searched": 0, "returned": 0, "error": None}
    if run is None:
        from . import apify_transport, sci_source_x
        token = os.environ.get("APIFY_API_TOKEN", "")
        if not token:
            out["error"] = "not_configured"
            return out

        def run(terms):
            return apify_transport.run_actor_and_wait(
                sci_source_x.actor_id(),
                {"searchTerms": terms, "maxItems": X_POSTS, "sort": "Top"},
                token, timeout=240, strict=True)
    terms = x_terms(event)
    if not terms:
        return out
    if deadline and time.monotonic() > deadline:
        out["error"] = "out_of_time"
        return out
    out["searched"] = 1
    try:
        items = run(terms) or []
    except Exception as e:
        logger.warning("event_intel_attendees: X search failed: %s", e)
        out["error"] = "search_failed"
        return out
    out["returned"] = len(items)
    names = event_terms(event)
    seen = set()
    for item in items:
        post = x_post(item)
        if not post or post["id"] in seen or not mentions_event(post["text"], names):
            continue
        seen.add(post["id"])
        out["posts"].append(post)
    return out


def _edition_year(event: dict) -> int | None:
    m = _YEAR.search(str(event.get("name") or "")) or _YEAR.search(str(event.get("edition") or ""))
    if m:
        return int(m.group(0))
    start = _as_date(event.get("starts_on"))
    return start.year if start else None


def linkedin_queries(event: dict) -> list[str]:
    """The name without its year, its hashtag, and the hashtag with this
    edition's year and the year before: people tag a post with the edition
    they were at (#WebSummitQatar2026), and the bare hashtag misses them."""
    terms = event_terms(event)
    plain = [t for t in terms if not t.startswith("#")]
    tags = [t for t in terms if t.startswith("#")]
    out = []
    if plain:
        out.append('"%s"' % plain[-1])          # the name without the year
    if tags:
        bare = tags[-1]
        out.append(bare)
        year = _edition_year(event)
        if year:
            for y in (year, year - 1):
                if "%s%d" % (bare, y) not in out:
                    out.append("%s%d" % (bare, y))
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
                # Cut off, not finished: a run that stops here has not read
                # what the remaining pages hold, and must say so.
                out["error"] = out["error"] or "out_of_time"
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


_POST_SYSTEM = """You read public LinkedIn and X posts that mention one \
event, and say which people each post shows at that event, or going to it.

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
6. status, one of: speaking (they say they spoke, presented, pitched, \
moderated or were on a panel or stage there), exhibiting (on their \
company's stand or booth), attending (going, or there now), attended (was \
there, and says nothing about being on stage), organising (works for the \
organiser). Being in the audience for a talk is attending or attended, \
never speaking.
7. quote: the exact words from the post that show it, copied character for \
character, at most 200 characters. Never paraphrase. The quote must be about \
that person: their name, or the author's own "I"/"we".
11. tense, for each person: "past" (spoke, was there, met them there, \
"lessons from"), "now" (there today, live from the stand), or "future" \
(will speak, got a ticket, see you there). A bio line or a list of stages \
someone has spoken on is "past".
12. List nobody for a wish, a hope or a maybe ("I want to join", "hoping to \
make it", "would love to go"), or for anyone who is not going or did not \
go ("couldn't make it", "missed it this year").
8. title and company for a named person only when the post says them.
9. The organiser's own promotion, ticket offers, job ads and news about the \
event name nobody: return an empty people list.
10. author_is_person is false when the author is a company, brand, product, \
publication, community or event account rather than one human being. Then \
do not list the author; people the post names may still be listed.

Reply with JSON only:
{"posts": [{"id": "p1", "same_event": true, "author_is_person": true, \
"edition": "this", "people": \
[{"who": "author", "name": null, "status": "attending", "tense": "future", \
"title": null, "company": null, "quote": "..."}]}]}
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
        lines.append("--- %s (%s)\nauthor: %s%s\nposted: %s\n%s" % (
            ref, "X" if post.get("platform") == "x" else "LinkedIn", a["name"],
            (" (" + a["headline"] + ")") if a.get("headline") else "",
            post.get("posted_at") or "unknown", post["text"][:POST_CHARS]))
    user = ("Event: %s\nToday: %s\n\n%s" % (_event_brief(event), today.isoformat(),
                                             "\n\n".join(lines)))
    with _MODEL_SLOTS:
        res = claude_websearch.ask(_POST_SYSTEM, user, max_uses=0, max_tokens=10000,
                                   timeout=120.0, deadline=READ_SECONDS)
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
    def read(batch):
        """One batch, tried again once when it fails, and as two halves when
        the reply ran out of room: on the first live run 4 of 9 batches came
        back unread and their posts were never checked at all."""
        first = classify(batch, event, today)
        spent = [first.get("spend")]
        if first.get("posts") is not None:
            return first["posts"], spent, None, 0
        if first.get("error") == "max_tokens" and len(batch) > 1:
            parts = [batch[:len(batch) // 2], batch[len(batch) // 2:]]
        else:
            time.sleep(RETRY_PAUSE)
            parts = [batch]
        posts, kinds, unread = [], [], 0
        for part in parts:
            again = classify(part, event, today)
            spent.append(again.get("spend"))
            if again.get("posts") is None:
                kinds.append(again.get("error") or first.get("error") or "error")
                unread += len(part)
            else:
                posts.extend(again["posts"])
        if len(kinds) == len(parts):
            return None, spent, kinds[0], unread
        return posts, spent, (kinds[0] if kinds else None), unread

    results = []
    if batches:
        with ThreadPoolExecutor(max_workers=min(WORKERS, len(batches))) as pool:
            # Each batch runs in a copy of this context, so a call made inside
            # a worker job is still recorded on that job's ledger.
            futures = [pool.submit(contextvars.copy_context().run, read, b) for b in batches]
            results = [f.result() for f in futures]
    out = {"people": [], "batches": len(batches), "failed": 0, "spend": [],
           "rejected": 0, "failed_kinds": [], "unread_posts": 0, "other_edition": 0}
    for posts_read, spent, kind, unread in results:
        out["spend"].extend(spent)
        out["unread_posts"] += unread
        if kind:
            out["failed_kinds"].append(kind)
        if posts_read is None:
            out["failed"] += 1
            continue
        res = {"posts": posts_read}
        for row in res["posts"]:
            if not isinstance(row, dict):
                continue
            post = by_ref.get(str(row.get("id") or ""))
            if not post or row.get("same_event") is False:
                continue
            if other_edition(post["text"], event):
                # Checked here as well as asked of the model: a reply that
                # leaves same_event out is not a yes.
                out["other_edition"] += 1
                continue
            for person in row.get("people") or []:
                if (row.get("author_is_person") is False and isinstance(person, dict)
                        and str(person.get("who") or "").lower() == "author"):
                    continue
                got = _person_from_post(person, post, row.get("edition"), event, today)
                if got is None:
                    out["rejected"] += 1
                elif got:
                    out["people"].append(got)
    return out


def author_role(author: dict, on_x: bool = False) -> tuple[str | None, str | None]:
    """Title and employer from the author's own profile line. A LinkedIn
    headline is a job line; an X bio is free text ("Dad. Runner. Building
    @acme"), so from X only a plain "Title at Company" is read and anything
    else is left blank rather than shown as a job title."""
    title, company = split_headline(author.get("headline"))
    if on_x and not (company and title and len(title) <= 50 and len(company) <= 40
                     and not re.search(r"[.,;@/]", title + company)):
        return None, None
    return title, company


def _person_from_post(person, post, edition, event, today):
    """One person a model read off a post, checked against the post. None
    when the check fails (counted), {} when there is nothing to keep."""
    if not isinstance(person, dict):
        return None
    status = str(person.get("status") or "").strip().lower()
    quote = str(person.get("quote") or "").strip()[:QUOTE_CHARS]
    if status not in POST_STATUSES:
        return {}
    if not in_text(quote, post["text"]) or denies_presence(quote, post["text"]):
        return None
    tense = str(person.get("tense") or "").strip().lower()
    who = str(person.get("who") or "").strip().lower()
    author = post["author"]
    on_x = post.get("platform") == "x"
    if who == "author":
        title, company = author_role(author, on_x)
        name, linkedin, basis = author["name"], author.get("url"), BASIS_SELF
    else:
        name = clean_name(person.get("name"))
        # A full name, as written in the post: two words at least, and there.
        if len(name.split()) < 2 or _fold(name) not in _fold(post["text"]):
            return None
        if name_key(name) == name_key(author["name"]):
            title, company = author_role(author, on_x)
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
            if not tied_to(name, quote, post["text"]):
                return None
    where = "post on X" if on_x else "LinkedIn post"
    return {
        "name": name[:200], "title": title, "company": company, "company_domain": None,
        "linkedin": linkedin, "basis": basis, "status": status,
        "x": author.get("x") if basis == BASIS_SELF else None,
        "edition": settle_edition(str(edition or ""), status, post.get("posted_at"), event, today,
                                  tense),
        "proof": [{
            "kind": "x_post" if on_x else "linkedin_post",
            "label": ("Their own %s" % where if basis == BASIS_SELF
                      else "Named in a %s by %s" % (where, author["name"])),
            "url": post.get("url"), "quote": quote,
            "posted_at": str(post.get("posted_at") or "")[:10] or None,
        }],
    }


# ── 3. public pages found by web search ──────────────────────────────────
#
# Two steps, so nothing rests on a search snippet. Several searches, each
# from a different angle, collect the addresses of pages that may name people
# at the event. Then every page is opened here and read whole, and a person
# is kept only where that page's own text names them and says the quote.
# The first version asked one search for the people directly and kept 1
# person for Web Summit Qatar from 43 pages it never read.

WEB_ANGLES = (
    ("announcements", "speakers, panellists, judges and hosts announced in "
     "news, press releases, company blogs and university or government news: "
     "who is speaking, presenting, moderating or giving a keynote"),
    ("exhibitors", "companies and startups announcing they will exhibit, pitch "
     "or have a stand or booth, and the people from them who will be there; "
     "startup programme and showcase lists; delegation and pavilion lists"),
    ("recaps", "recaps, takeaways, highlights and diaries written by people who "
     "were there; interviews, podcasts and videos recorded at the event; photo "
     "galleries and award winners"),
)
WEB_SEARCHES_PER_ANGLE = 6
# Above anything one search can return (26 queries x 2 results pages x 10
# results), so every page that names the event is read: on the live runs a
# cap of 120 left 25 such pages unread at Lisbon. Kept as a number only so
# a runaway can never read without end.
MAX_WEB_PAGES = 600
PAGE_CHARS = 16000
PAGE_WORKERS = 16
# Read elsewhere (LinkedIn, X), unreadable as pages, or not pages at all.
_SKIP_HOSTS = ("linkedin.com", "x.com", "twitter.com", "facebook.com", "instagram.com",
               "tiktok.com", "youtube.com", "youtu.be", "google.com", "bing.com",
               "duckduckgo.com", "reddit.com")

_FIND_SYSTEM = """You find public web pages that name specific people at one \
event. You are not listing the people: you are finding the pages, which will \
be opened and read afterwards.

Search widely from the angle you are given. Vary the wording, use the year, \
the event's hashtag and the language of the host country as well as English, \
and include earlier editions of the same event (same series, same city). \
Leave out the event's own website, linkedin.com, x.com, twitter.com, \
facebook.com, instagram.com and youtube.com.

Reply with JSON only, every relevant page you found, at most 30:
{"pages": [{"url": "...", "why": "a few words"}]}"""

_PAGE_SYSTEM = """You read one public web page and list the people it shows at \
one event: speaking, exhibiting, attending, having attended, or organising.

Rules:
1. Only people named in full on this page. Never add anyone.
2. Only people the page shows at THIS event (the same series in the same \
city; any edition). Not people merely quoted, thanked or mentioned.
3. quote: the exact words on the page that show the person at the event, \
copied character for character, at most 200 characters.
4. status, one of: speaking (spoke, presented, pitched, moderated, judged, \
on a panel or stage), exhibiting (on a stand, booth or pavilion), attending, \
attended, organising.
5. edition: "this" for the edition given, "earlier" for an earlier year, \
"unclear" when the page does not say.
6. title and company only as the page states them for that person.
7. A page that is a list (speakers, startups, delegation) can name many \
people: list every one, up to 80.
8. tense: "past" when the page says it already happened (spoke, took part, \
was present), "future" when it is announced or to come, "now" when the page \
reports it live.
9. Nobody who only hopes, plans to apply, or did not go.

Reply with JSON only:
{"people": [{"name": "...", "title": null, "company": null, "status": \
"speaking", "tense": "future", "edition": "this", "quote": "..."}]}"""


def _host_of(url: str) -> str:
    m = re.match(r"https?://([^/:?#]+)", str(url or ""), re.I)
    return re.sub(r"^www\.", "", m.group(1).lower()) if m else ""


def _skipped_host(host: str, event_host: str = "") -> bool:
    if not host:
        return True
    if event_host and (host == event_host or host.endswith("." + event_host)):
        return True  # the event's own pages were read already, with their own proof
    return any(host == h or host.endswith("." + h) for h in _SKIP_HOSTS)


def find_pages(event: dict, angle: tuple, today: date, ask=None) -> dict:
    """One angle's searches. Pages the model points at come first; every
    other address the searches returned follows, because a page the model
    did not mention can still name people and is cheap to open."""
    from . import claude_websearch
    ask = ask or claude_websearch.ask
    user = ("Event: %s\nToday: %s\nAngle: %s"
            % (_event_brief(event), today.isoformat(), angle[1]))
    with _MODEL_SLOTS:
        # `deadline` is the whole call's limit; `timeout` is only one read's.
        # Without it a search could run for the helper's 40-minute default,
        # and on the live Qatar run the pages were never reached.
        res = ask(_FIND_SYSTEM, user, max_uses=WEB_SEARCHES_PER_ANGLE, max_tokens=4000,
                  timeout=120.0, deadline=FIND_SECONDS)
    out = {"urls": [], "named": 0, "spend": claude_websearch.spend_of(res), "error": None,
           "searches": int(res.get("search_count") or 0)}
    if res.get("error"):
        out["error"] = (res["error"] or {}).get("kind") or "error"
    returned = [str(u) for u in (res.get("result_urls") or []) if u]
    by_key = {u.rstrip("/"): u for u in returned}
    parsed = claude_websearch.extract_json(
        claude_websearch.remove_citation_tags(res.get("text") or ""), require="pages")
    named = []
    pages = parsed.get("pages") if isinstance(parsed, dict) else None
    for p in pages or []:
        u = str(p.get("url") or "").strip() if isinstance(p, dict) else ""
        # Only an address the searches really returned: a URL the model
        # wrote from memory is not a search result.
        if u and u.rstrip("/") in by_key:
            named.append(by_key[u.rstrip("/")])
    named = list(dict.fromkeys(named))
    out["urls"] = list(dict.fromkeys(named + returned))
    out["named"] = len(named)
    return out


def _near(fragment: str, name: str, text: str, span: int = 400) -> bool:
    """Whether `fragment` appears within `span` characters of `name` in the
    text: on a page that names fifty people, a title somewhere on it is not
    this person's title."""
    body, n, f = _fold(text), _fold(name), _fold(fragment)
    if not (n and f):
        return False
    at = body.find(n)
    while at >= 0:
        if f in body[max(0, at - span): at + len(n) + span]:
            return True
        at = body.find(n, at + 1)
    return False


def page_excerpt(text: str, terms: list[str], limit: int = PAGE_CHARS) -> str:
    """The part of a long page to read: all of it when it fits, otherwise
    the stretch from shortly before the first mention of the event."""
    if len(text) <= limit:
        return text
    body = _fold(text)
    hits = [i for i in (body.find(_fold(t)) for t in terms if not t.startswith("#")) if i >= 0]
    start = max(0, (min(hits) if hits else 0) - 1500)
    return text[start:start + limit]


def _read_page(url: str, text: str, event: dict, today: date) -> dict:
    from . import claude_websearch
    user = "Event: %s\nToday: %s\nPage: %s\n\n--- PAGE TEXT ---\n%s" % (
        _event_brief(event), today.isoformat(), url, text)
    with _MODEL_SLOTS:
        res = claude_websearch.ask(_PAGE_SYSTEM, user, max_uses=0, max_tokens=8000,
                                   timeout=120.0, deadline=READ_SECONDS)
    spend = claude_websearch.spend_of(res)
    if res.get("error"):
        return {"people": None, "error": (res["error"] or {}).get("kind") or "error",
                "spend": spend}
    parsed = claude_websearch.extract_json(res.get("text") or "", require="people")
    if not isinstance(parsed, dict):
        return {"people": None, "error": "unparsable", "spend": spend}
    return {"people": parsed.get("people") or [], "error": None, "spend": spend}


def page_edition(model_edition: str, excerpt: str, event: dict) -> str:
    """Which edition a web page is about, from the years the page itself
    prints. A page has no post date, and on the live Lisbon run the model
    called Wikipedia's article on the event and recaps of past editions
    "this edition". It is this edition only when the page prints this
    edition's year; earlier when it prints only earlier years; otherwise
    the page does not say."""
    year = _edition_year(event)
    if not year:
        return model_edition if model_edition in EDITIONS else EDITION_UNCLEAR
    years = {int(m.group(0)) for m in _YEAR.finditer(excerpt)}
    if year in years:
        return model_edition if model_edition in EDITIONS else EDITION_UNCLEAR
    if any(y < year for y in years):
        return EDITION_EARLIER
    return EDITION_UNCLEAR


def people_on_page(url: str, text: str, event: dict, today: date, read=None) -> dict:
    """Everyone one opened page shows at the event, each checked against the
    page's own text."""
    read = read or _read_page
    excerpt = page_excerpt(text, event_terms(event))
    res = read(url, excerpt, event, today)
    spent = [res.get("spend")]
    if res.get("people") is None:
        # Read again once, as a batch of posts is: with every page now read,
        # one bad minute would otherwise cost a page for good.
        time.sleep(RETRY_PAUSE)
        res = read(url, excerpt, event, today)
        spent.append(res.get("spend"))
    from . import claude_websearch
    out = {"people": [], "spend": claude_websearch.spend_sum(*[x for x in spent if x]),
           "error": res.get("error") if res.get("people") is None else None, "rejected": 0,
           # A 400-speaker page is longer than one reading: say so rather
           # than let the people past the cut look like nobody.
           "cut": len(text) > len(excerpt)}
    host = _host_of(url)
    for person in res.get("people") or []:
        if not isinstance(person, dict):
            continue
        name = clean_name(person.get("name"))
        quote = str(person.get("quote") or "").strip()[:QUOTE_CHARS]
        status = str(person.get("status") or "").strip().lower()
        if status not in POST_STATUSES:
            continue
        if (len(name.split()) < 2 or _fold(name) not in _fold(excerpt)
                or not in_text(quote, excerpt) or denies_presence(quote, excerpt)
                or not tied_to(name, quote, excerpt)):
            out["rejected"] += 1
            continue
        tense = str(person.get("tense") or "").strip().lower()
        title = str(person.get("title") or "").strip() or None
        company = str(person.get("company") or "").strip() or None
        out["people"].append({
            "name": name[:200],
            "title": title if title and _near(title, name, excerpt) else None,
            "company": company if company and _near(company, name, excerpt) else None,
            "company_domain": None, "linkedin": None,
            "basis": BASIS_OTHERS, "status": status,
            "edition": settle_edition(page_edition(str(person.get("edition") or ""),
                                                   excerpt, event), status, None, event, today,
                                      tense),
            "proof": [{"kind": "web_page", "label": "Named on %s" % (host or "a public page"),
                       "url": url, "quote": quote}],
        })
    return out


# Google, searched directly through Apify's Google Search scraper on the
# platform's own Apify account: about a minute for every query below, and a
# few cents. The model-driven search above is the fallback when it cannot
# run: on the live Lisbon run all three of its searches hit their 4-minute
# limit and found nothing.
GOOGLE_ACTOR = "apify/google-search-scraper"
GOOGLE_PAGES_PER_QUERY = 2
MAX_FETCH = 600

_QUERY_SHAPES = (
    '"{n}" speakers', '"{n} {y}" speaker', '"{n}" keynote', '"{n}" panel',
    '"{n}" "fireside chat"', '"speaking at {n}"', '"{n}" moderator',
    '"{n}" exhibiting', '"{n}" booth', '"{n}" stand', '"{n}" startup pitch',
    '"{n}" "startup showcase"', '"{n}" delegation', '"{n}" pavilion',
    '"{n}" recap', '"{n}" takeaways', '"{n} {p}" highlights', '"{n}" interview',
    '"{n}" podcast', '"{n}" "press release"', '"see you at {n}"', '"join us at {n}"',
    '"meet us at {n}"', '"{n} {y}"', '"{n} {p}"',
)


def google_queries(event: dict) -> list[str]:
    plain = [t for t in event_terms(event) if not t.startswith("#")]
    tags = [t for t in event_terms(event) if t.startswith("#")]
    if not plain:
        return []
    n = plain[-1]
    year = _edition_year(event) or date.today().year
    out = [q.format(n=n, y=year, p=year - 1) for q in _QUERY_SHAPES]
    if tags:
        out.append(tags[-1])
    return list(dict.fromkeys(out))


def google_results(event: dict, run=None) -> dict:
    """Every organic result for google_queries, as {"results": [{url, title,
    snippet, query}], "queries", "error"}. `run` stands in for the actor."""
    import os
    out = {"results": [], "queries": 0, "error": None}
    queries = google_queries(event)
    if not queries:
        return out
    if run is None:
        from . import apify_transport
        token = os.environ.get("APIFY_API_TOKEN", "")
        if not token:
            out["error"] = "not_configured"
            return out

        def run(qs):
            return apify_transport.run_actor_and_wait(
                os.environ.get("EVI_GOOGLE_ACTOR_ID", GOOGLE_ACTOR),
                {"queries": "\n".join(qs), "maxPagesPerQuery": GOOGLE_PAGES_PER_QUERY,
                 "resultsPerPage": 10, "mobileResults": False, "saveHtml": False,
                 "saveHtmlToKeyValueStore": False, "includeUnfilteredResults": False},
                token, timeout=240, strict=True)
    out["queries"] = len(queries)
    try:
        pages = run(queries) or []
    except Exception as e:
        logger.warning("event_intel_attendees: Google search failed: %s", e)
        out["error"] = "search_failed"
        return out
    for page in pages:
        if not isinstance(page, dict):
            continue
        q = ((page.get("searchQuery") or {}).get("term") if isinstance(
            page.get("searchQuery"), dict) else None) or ""
        for r in page.get("organicResults") or []:
            if isinstance(r, dict) and r.get("url"):
                out["results"].append({"url": str(r["url"]), "title": str(r.get("title") or ""),
                                       "snippet": str(r.get("description") or ""), "query": q})
    return out


def rank_results(results: list[dict], event: dict, event_host: str = "") -> list[str]:
    """Addresses worth opening, best first: a result whose own title or
    snippet names the event, then how many different searches returned it.
    The event's own site and the social sites are left out."""
    terms = event_terms(event)
    score: dict = {}
    first: dict = {}
    for i, r in enumerate(results):
        url = r["url"]
        if _skipped_host(_host_of(url), event_host):
            continue
        k = url.rstrip("/").lower()
        first.setdefault(k, (i, url))
        named = mentions_event(r.get("title", "") + " " + r.get("snippet", ""), terms)
        hits, was_named, queries = score.get(k, (0, False, set()))
        queries = queries | {r.get("query")}
        score[k] = (len(queries), was_named or named, queries)
    order = sorted(score, key=lambda k: (not score[k][1], -score[k][0], first[k][0]))
    return [first[k][1] for k in order]


# Pages that refuse a plain request (bot walls, scripts that build the page,
# timeouts) are opened again in a real browser through Apify's Website
# Content Crawler, all in one run. On the live Lisbon run 43 of 196 Google
# results would not open the plain way.
BROWSER_ACTOR = "apify/website-content-crawler"
# The live Lisbon run loaded 14 of 39 pages in 4 minutes at the crawler's
# default memory and ran out of time; memory is what lets it run more
# browsers at once.
BROWSER_SECONDS = 360
BROWSER_MEMORY_MB = 8192


def _url_key(u: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", str(u or "").strip().lower()).rstrip("/")


def browser_open(urls: list[str], run=None) -> dict:
    """Open `urls` in a browser. Returns {"pages": {requested url: {text,
    final}}, "error", "status", "detail"}. `run` stands in for the actor in
    tests and returns what apify_transport.run_actor_collect does.

    Whatever the crawler loaded is kept even when its run is cut off: on
    the first live run it was still going at its limit and every page it
    had already loaded was thrown away with it."""
    import os
    out = {"pages": {}, "error": None, "status": None, "detail": None}
    if not urls:
        return out
    if run is None:
        from . import apify_transport
        token = os.environ.get("APIFY_API_TOKEN", "")
        if not token:
            out["error"] = "not_configured"
            return out

        def run(us):
            return apify_transport.run_actor_collect(
                os.environ.get("EVI_BROWSER_ACTOR_ID", BROWSER_ACTOR),
                {"startUrls": [{"url": u} for u in us], "maxCrawlDepth": 0,
                 "maxCrawlPages": len(us), "crawlerType": "playwright:firefox",
                 "maxConcurrency": 20, "maxRequestRetries": 1,
                 "requestTimeoutSecs": 45, "removeCookieWarnings": True,
                 "saveMarkdown": False, "proxyConfiguration": {"useApifyProxy": True}},
                token, timeout=BROWSER_SECONDS, memory_mb=BROWSER_MEMORY_MB)
    try:
        got = run(list(urls)) or {}
    except Exception as e:
        logger.warning("event_intel_attendees: browser opening failed: %s", e)
        got = {"items": [], "error": str(e)[:200]}
    items = got.get("items") or []
    out["status"] = got.get("status")
    out["detail"] = got.get("error")
    if not items and (got.get("error") or got.get("status") not in (None, "SUCCEEDED")):
        out["error"] = "browser_failed"
    wanted = {_url_key(u): u for u in urls}
    for item in items:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("markdown") or "").strip()
        if len(text) < 200:
            continue  # a consent wall or an empty shell is not the page
        crawl = item.get("crawl") if isinstance(item.get("crawl"), dict) else {}
        loaded = crawl.get("loadedUrl") or item.get("loadedUrl") or item.get("url")
        # The crawler reports the address it was given and the one it ended
        # on after redirects; either ties the text to the request.
        for cand in (item.get("url"), crawl.get("requestUrl"), loaded):
            asked = wanted.get(_url_key(cand))
            if asked and asked not in out["pages"]:
                out["pages"][asked] = {"text": text, "final": loaded or asked}
                break
    return out


def search_web(event: dict, event_host: str = "", today: date | None = None,
               fetch=None, ask=None, read=None, deadline: float | None = None,
               google=None, browser=None) -> dict:
    """People named on public pages: Google searched directly from every
    angle (the model's own search when Google cannot be reached), then every
    page opened here and read whole."""
    from . import claude_websearch
    from .event_intel_harvest import fetch_page
    today = today or date.today()
    fetch = fetch or fetch_page
    out = {"people": [], "searches": 0, "pages": 0, "opened": 0, "on_event": 0,
           "read": 0, "unopened": 0, "rejected": 0, "unread": 0, "skipped": 0,
           "other_edition": 0, "cut": 0, "error": None, "spend": None, "via": "google",
           "results": 0}
    spend = []
    began = time.monotonic()
    g = (google or google_results)(event)
    keep = []
    if g.get("results"):
        out["searches"] = g.get("queries", 0)
        out["results"] = len(g["results"])
        keep = rank_results(g["results"], event, event_host)
    else:
        out["via"] = "model"
        out["google_error"] = g.get("error") or "no_results"
        with ThreadPoolExecutor(max_workers=len(WEB_ANGLES)) as pool:
            found = [f.result() for f in [
                pool.submit(contextvars.copy_context().run, find_pages, event, a, today, ask)
                for a in WEB_ANGLES]]
        errors = [f["error"] for f in found if f.get("error")]
        named, rest = [], []
        for f in found:
            spend.append(f.get("spend"))
            out["searches"] += f.get("searches", 0)
            urls = f.get("urls") or []
            named.extend(urls[:f.get("named", 0)])
            rest.extend(urls[f.get("named", 0):])
        if errors and len(errors) == len(found):
            out["error"] = errors[0]
        # The pages the searches pointed at first, then everything else
        # they returned, deduped, without the event's own site or the
        # social sites.
        seen = set()
        for u in named + rest:
            k = u.rstrip("/").lower()
            if k in seen or _skipped_host(_host_of(u), event_host):
                continue
            seen.add(k)
            keep.append(u)
    keep = keep[:MAX_FETCH]
    out["pages"] = len(keep)
    out["find_seconds"] = int(time.monotonic() - began)
    terms = event_terms(event)

    # Opening is free, so every candidate is opened; reading costs a model
    # call, so the first MAX_WEB_PAGES that name the event are read.
    def opened(url):
        if deadline and time.monotonic() > deadline:
            return url, {"skipped": True}
        got = fetch(url)
        if got.get("status") != "ok" or not got.get("text"):
            return url, {"unopened": True}
        return url, {"text": got["text"], "final": got.get("final_url") or url}
    fetched = []
    if keep:
        with ThreadPoolExecutor(max_workers=24) as pool:
            fetched = [f.result() for f in [
                pool.submit(contextvars.copy_context().run, opened, u) for u in keep]]
    # The pages that refused, once more in a real browser.
    refused = [url for url, got in fetched if got.get("unopened")]
    if refused and not (deadline and time.monotonic() > deadline):
        b = (browser or browser_open)(refused)
        out["browser_error"] = b.get("error")
        out["browser_status"] = b.get("status")
        out["browser_detail"] = b.get("detail")
        rescued = b.get("pages") or {}
        out["rescued"] = len(rescued)
        fetched = [(url, dict(rescued[url], browser=True) if url in rescued else got)
                   for url, got in fetched]
    to_read = []
    for url, got in fetched:
        if got.get("skipped"):
            out["skipped"] += 1
        elif got.get("unopened"):
            out["unopened"] += 1
        else:
            out["opened"] += 1
            if mentions_event(got["text"], terms):
                out["on_event"] += 1
                if other_edition(page_excerpt(got["text"], terms), event):
                    out["other_edition"] += 1
                    continue
                to_read.append((got["final"], got["text"]))
    out["not_read"] = max(0, len(to_read) - MAX_WEB_PAGES)
    to_read = to_read[:MAX_WEB_PAGES]

    def one(item):
        if deadline and time.monotonic() > deadline:
            return {"skipped": True}
        return people_on_page(item[0], item[1], event, today, read)
    results = []
    if to_read:
        with ThreadPoolExecutor(max_workers=PAGE_WORKERS) as pool:
            results = [f.result() for f in [
                pool.submit(contextvars.copy_context().run, one, it) for it in to_read]]
    for r in results:
        if r.get("skipped"):
            out["skipped"] += 1
            continue
        spend.append(r.get("spend"))
        out["unread" if r.get("error") else "read"] += 1
        out["rejected"] += r.get("rejected", 0)
        out["cut"] += 1 if r.get("cut") else 0
        out["people"].extend(r.get("people") or [])
    out["spend"] = claude_websearch.spend_sum(*[s for s in spend if s])
    out["seconds"] = int(time.monotonic() - began)
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
        for f in ("title", "company", "company_domain", "linkedin", "x"):
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
                     "name_masked": bool(r.get("name_masked")), "x": r.get("x")},
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

    # X, the web search and the staff lookup do not depend on anything else,
    # so they run while LinkedIn is searched and its posts are read. One
    # after another, a Lisbon search took 14 minutes.
    began = time.monotonic()

    def safely(fn, fallback, *args):
        t = time.monotonic()
        try:
            got = fn(*args)
            # How long each source took, so a slow search says where it went.
            return dict(got, seconds=int(time.monotonic() - t)) if isinstance(got, dict) else got
        except Exception as e:
            logger.exception("event_intel_attendees: a source failed")
            return dict(fallback, error=fallback.get("error") or str(e)[:200])

    classify = sources.get("classify")
    with ThreadPoolExecutor(max_workers=5) as pool:
        def later(fn, fallback, *args):
            return pool.submit(contextvars.copy_context().run, safely, fn, fallback, *args)
        x_job = later(sources.get("x") or search_x,
                      {"posts": [], "error": "search_failed"}, event, deadline)
        web_job = later(sources.get("web") or (
                            lambda e, h, t: search_web(e, h, t, deadline=deadline)),
                        {"people": [], "error": "error", "spend": None},
                        event, event_host, today)
        staff_job = later(sources.get("staff") or staff_at_companies,
                          {"people": [], "error": None}, participants, eid)

        li = safely(sources.get("linkedin") or search_linkedin,
                    {"posts": [], "error": "search_failed"}, event, deadline)
        # LinkedIn's posts are read the moment they are in, while X is still
        # being scraped; X's are read the moment they arrive.
        empty = {"people": [], "batches": 0, "failed": 0, "spend": [], "rejected": 0,
                 "failed_kinds": [], "unread_posts": 0, "other_edition": 0}
        li_read = (later(people_in_posts, empty, li["posts"], event, today, classify)
                   if li.get("posts") else None)
        xs = x_job.result()
        x_read = (people_in_posts(xs["posts"], event, today, classify=classify)
                  if xs.get("posts") else dict(empty))
        li_read = li_read.result() if li_read else dict(empty)
        web = web_job.result()
        staff = staff_job.result()

    report["linkedin"] = {"posts": len(li.get("posts") or []),
                          "searches": li.get("searched", 0),
                          "returned": li.get("returned", 0), "error": li.get("error"),
                          "seconds": li.get("seconds")}
    report["x"] = {"posts": len(xs.get("posts") or []), "returned": xs.get("returned", 0),
                   "error": xs.get("error"), "seconds": xs.get("seconds")}
    for key, read in (("linkedin", li_read), ("x", x_read)):
        spend.extend(read.get("spend") or [])
        people.extend(read.get("people") or [])
        report[key]["people"] = len(read.get("people") or [])
    report["linkedin"].update(
        batches=li_read["batches"] + x_read["batches"],
        failed_batches=li_read["failed"] + x_read["failed"],
        rejected=li_read["rejected"] + x_read["rejected"],
        failed_kinds=li_read["failed_kinds"] + x_read["failed_kinds"],
        unread_posts=li_read["unread_posts"] + x_read["unread_posts"],
        other_edition=li_read.get("other_edition", 0) + x_read.get("other_edition", 0))

    spend.append(web.get("spend"))
    people.extend(web.get("people") or [])
    report["web"] = {k: web.get(k) for k in ("via", "searches", "results", "pages", "opened",
                                              "rescued", "browser_error", "browser_status",
                                              "browser_detail",
                                              "on_event", "read", "not_read", "unread",
                                              "unopened", "rejected", "skipped", "error",
                                              "other_edition", "cut",
                                              "google_error", "find_seconds", "seconds")}
    report["web"]["people"] = len(web.get("people") or [])
    people.extend(staff.get("people") or [])
    report["staff"] = {"companies": staff.get("companies", 0), "skipped": staff.get("skipped", 0),
                       "people": len(staff.get("people") or []), "error": staff.get("error")}
    report["seconds"] = int(time.monotonic() - began)

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
    elif li.get("error") == "out_of_time":
        bits.append("The LinkedIn search ran out of time part-way, so posts after "
                    "that point were not read.")
    elif li.get("error"):
        bits.append("The LinkedIn search stopped part-way (%s), so posts after "
                    "that point were not read." % li["error"])
    x = report.get("x") or {}
    if x.get("error") == "not_configured":
        bits.append("X was not searched: the X scraper is not configured on this "
                    "deployment.")
    elif x.get("error"):
        bits.append("The X search did not finish, so posts on X were not read.")
    if li.get("unread_posts"):
        bits.append("%d of the %d posts found could not be read, so the people in "
                    "them were not checked." % (li["unread_posts"],
                                                li.get("posts", 0) + x.get("posts", 0)))
    web = report.get("web") or {}
    if web.get("error"):
        bits.append("The web search did not finish, so public pages beyond LinkedIn "
                    "and X were not checked.")
    if web.get("unopened"):
        bits.append("%d of the %d web pages found would not open, even in a browser%s, so "
                    "whether they name anyone is not known." % (
                        web["unopened"], web.get("pages", 0),
                        "" if not web.get("browser_error") else
                        " (the browser could not be used this time)"))
    if web.get("skipped"):
        bits.append("%d of the %d web pages found were not opened or read: the search "
                    "ran out of time first." % (web["skipped"], web.get("pages", 0)))
    if web.get("not_read"):
        bits.append("%d more web pages naming the event were found than are read in one "
                    "search; the best-matching %d were read." % (web["not_read"], MAX_WEB_PAGES))
    if web.get("unread"):
        bits.append("%d of the %d web pages naming the event could not be read." % (
            web["unread"], web.get("on_event", 0)))
    if web.get("cut"):
        bits.append("%d long web page%s read only in part, from where %s the "
                    "event; people named further down %s not checked." % (
                        web["cut"], " was" if web["cut"] == 1 else "s were",
                        "it first names" if web["cut"] == 1 else "each first names",
                        "it were" if web["cut"] == 1 else "them were"))
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


def find_for_run(run_id: int, email: str, deadline_seconds: float = 1500.0) -> dict:
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
