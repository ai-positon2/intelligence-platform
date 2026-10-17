"""Paid Marketing Agents: Landing Page & Relevancy Check, do the words match.

Whether a keyword, an ad and a landing page say the same thing is judged here with plain word matching, not a model, so the same
page and the same ad always give the same answer and a finding can show the exact words that were and were not found.

A phrase is cut into its content words: lower case, accents and apostrophes dropped, "&" read as "and", filler words and words that describe how
someone searches rather than what they want ("near me", "best", "cheap") left out, and each word reduced to a simple stem so "dentists" matches "dentist"
and "pricing" matches "cost". A keyword is then on the page when every one of its content words is; it is in the page's headline when they are all in the
title or its headings; it is partly there when at least half of them are. Single-word keywords are all or nothing.

Nothing here reads the network or a database: `page` is the dict tracker/paid_lp_page.py makes from a fetched page.
"""
from __future__ import annotations

import re
import unicodedata

MIN_WORDS = 120                                   # visible words below which the page's text is too thin to judge a match on
HEADLINE_ECHO = 0.6                               # share of a headline's words the page's headings must carry for the headline to be echoed
PARTIAL = 0.5                                     # share of a keyword's words that makes it partly on the page
MAX_PHRASE_WORDS = 12                             # a "keyword" longer than this is a pasted sentence, not a keyword: it is judged on its first words

STOP = frozenset((
    "a an and or the of in on at to for from by with without as is are was be it its this that these those i me my we our us you your "
    "do does how what when where who why which can will would should near nearby"
).split())
MODIFIERS = frozenset(("best", "top", "cheap", "cheapest", "affordable", "good", "great", "find", "get", "buy", "local", "me", "now", "today", "quote", "quotes"))
# Words that mean the same thing on a page and in a search, folded to one so "pricing" on the page meets "cost" in the keyword.
CANON = {"price": "price", "prices": "price", "pricing": "price", "cost": "price", "costs": "price", "fee": "price", "fees": "price", "rate": "price", "rates": "price",
         "phone": "phone", "telephone": "phone", "dr": "doctor", "physician": "doctor", "attorney": "lawyer", "lawyers": "lawyer", "attorneys": "lawyer"}

_SPLIT = re.compile(r"[^a-z0-9]+")


def _fold(text):
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()
    return t.replace("&", " and ").replace("'", "").replace("’", "")


def stem(word):
    """A word cut back to a stem that plural, -ing and -ed forms share. Short words are left alone."""
    if word in CANON:
        return CANON[word]
    w = word
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith("sses"):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        w = w[:-1]
    if len(w) > 5 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 4 and w.endswith("ed"):
        w = w[:-2]
    if len(w) > 4 and w.endswith("e"):                  # license, licensed and licensing meet at "licens"
        w = w[:-1]
    return CANON.get(w, w)


def words(text):
    """Every word of `text`, folded and stemmed, in order (filler words kept)."""
    return [stem(t) for t in _SPLIT.split(_fold(text)) if t]


def content(text, limit=MAX_PHRASE_WORDS):
    """The words of a keyword, headline or search term that say what is wanted: filler and search-style words removed, stemmed, in order, no repeats."""
    seen, out = set(), []
    for t in _SPLIT.split(_fold(text)):
        if not t or t in STOP or t in MODIFIERS:
            continue
        s = stem(t)
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out[:limit]


def vocab(*texts):
    """The set of stems in the given texts."""
    out = set()
    for t in texts:
        out.update(words(t))
    return out


def prominent_text(page):
    """What a visitor reads first: the title and the headings."""
    return " ".join([str(page.get("title") or "")] + [str(h) for h in page.get("h1") or []] + [str(h) for h in page.get("h2") or []] + [str(h) for h in page.get("h3") or []])


def page_vocabs(page):
    """(prominent stems, every stem on the page)."""
    prom = vocab(prominent_text(page))
    return prom, prom | vocab(page.get("text"))


def word_count(page):
    return int(page.get("wordCount") or 0)


def too_thin(page):
    """True when the page's HTML carries too few words to judge a match on (a page built by scripts, a gate, a redirect shell)."""
    return word_count(page) < MIN_WORDS


def hit(phrase, prom, full):
    """How `phrase` sits on the page: 'headline' (all its content words are in the title or headings), 'page' (all are on the page), 'partial' (at
    least half), 'none', or None when it has no content words to look for."""
    ws = content(phrase)
    if not ws:
        return None
    if all(w in prom for w in ws):
        return "headline"
    if all(w in full for w in ws):
        return "page"
    if len(ws) > 1 and sum(1 for w in ws if w in full) >= PARTIAL * len(ws):
        return "partial"
    return "none"


def _weight(row):
    """What a keyword or search term counts for: what it spent (a tiny amount when it spent nothing, so a list that spent nothing is weighed evenly)."""
    return max(float(row.get("cost") or 0.0), 0.01)


def phrase_stats(rows, page, key="text"):
    """How a list of keywords or search terms ([{text, cost, clicks}]) sits on the page, weighted by what each spent:
    {judged, onPage (share on the page at all, headline included), inHeadline (share in the title or headings), missing (rows not on the page,
    biggest first), partial}. Rows with no content words are not judged. judged is 0 when there is nothing to judge."""
    prom, full = page_vocabs(page)
    total = on_page = in_head = 0.0
    judged = 0
    missing, partial = [], []
    for r in sorted(rows, key=_weight, reverse=True):
        h = hit(r.get(key), prom, full)
        if h is None:
            continue
        judged += 1
        w = _weight(r)
        total += w
        if h in ("headline", "page"):
            on_page += w
        if h == "headline":
            in_head += w
        if h == "none":
            missing.append(r[key])
        elif h == "partial":
            partial.append(r[key])
    return {"judged": judged, "onPage": (on_page / total) if total else None, "inHeadline": (in_head / total) if total else None,
            "missing": missing, "partial": partial}


# ── Headlines ───────────────────────────────────────────────────────────────

def best_echo(headlines, prom):
    """The best share of any one headline's words found in the page's title and headings, and the headline it was. Headlines of one word are used
    only when there is none longer (a one-word headline matches almost anything). (0.0, '') for no usable headline."""
    usable = [(h, content(h)) for h in headlines]
    usable = [(h, ws) for h, ws in usable if ws]
    longer = [(h, ws) for h, ws in usable if len(ws) > 1]
    best, best_h = -1.0, ""
    for h, ws in (longer or usable):
        share = sum(1 for w in ws if w in prom) / len(ws)
        if share > best:
            best, best_h = share, h
    return max(best, 0.0), best_h


# ── What the ad promises ────────────────────────────────────────────────────

_MONEY = re.compile(r"\$\s?(\d[\d,]*(?:\.\d{1,2})?)")
_PERCENT = re.compile(r"(?<![\d.])(\d{1,3})(?:\.\d+)?\s?(?:%|percent\b)")
_FREE = re.compile(r"(?<![-\w])free\s+([a-z][a-z-]+(?:\s+[a-z][a-z-]+){0,2})", re.I)
_ROUND_THE_CLOCK = re.compile(r"\b24\s?/\s?7\b|\b24[\s-]?hours?\b", re.I)
_SAME_DAY = re.compile(r"\bsame[\s-]day\b", re.I)
_NO_COST = re.compile(r"\bno[\s-](?:cost|obligation|fees?|commitment)\b", re.I)
FREE_FILLER = frozenset(("for", "and", "to", "with", "the", "when", "on", "in", "at", "of", "or", "if", "your"))


def _digits(s):
    return str(s).replace(",", "").rstrip("0").rstrip(".") if "." in str(s) else str(s).replace(",", "")


def offers_in(texts):
    """The promises in ad copy, each once: [{'kind', 'text', 'key'}]. Prices ($99), discounts (20%), free things (free consultation), round-the-clock
    and same-day service, no-cost and no-obligation. These are the things a visitor expects to see again on the page."""
    out, seen = [], set()

    def add(kind, text, key):
        if (kind, key) not in seen:
            seen.add((kind, key))
            out.append({"kind": kind, "text": text, "key": key})

    for raw in texts:
        t = str(raw or "")
        for m in _MONEY.finditer(t):
            add("price", "$" + m.group(1), _digits(m.group(1)))
        for m in _PERCENT.finditer(t):
            add("percent", "%s%%" % m.group(1), m.group(1))
        for m in _FREE.finditer(t):
            thing = []
            for w in m.group(1).lower().split():            # the words of what is free, up to the first filler word ("free consultation for new patients")
                if w in FREE_FILLER:
                    break
                thing.append(w)
            if thing:
                add("free", "free " + " ".join(thing), "|".join(stem(w) for w in thing))
        if _ROUND_THE_CLOCK.search(t):
            add("hours", "24/7", "24")
        if _SAME_DAY.search(t):
            add("sameday", "same day", "sameday")
        m = _NO_COST.search(t)
        if m:
            add("nocost", m.group(0).lower(), m.group(0).lower().replace("-", " "))
    return out


def _page_flat(page):
    """The page's text as one lower-case string with the spacing after a currency sign closed, for finding a number in."""
    t = _fold(str(page.get("text") or "") + " " + prominent_text(page))
    return re.sub(r"\$\s+", "$", t)


def on_page(offer, page):
    """True when the page carries the promise `offer` (from offers_in)."""
    flat = _page_flat(page)
    kind, key = offer["kind"], offer["key"]
    if kind == "price":
        flat = flat.replace(",", "")
        tail = re.escape(key) + ("0?" if "." in key else r"(?:\.00?)?")
        return re.search(r"\$" + tail + r"(?!\d)", flat) is not None or re.search(r"(?<![\d.$])" + tail + r"\s?(?:dollars|usd)\b", flat) is not None
    if kind == "percent":
        return re.search(r"(?<![\d.])%s(?:\.\d+)?\s?(?:%%|percent\b)" % re.escape(key), flat) is not None
    if kind == "free":
        wanted = set(key.split("|"))
        for m in re.finditer(r"(?<![-\w])free\b((?:\s+[a-z0-9-]+){1,4})", flat):         # the next words of the same sentence; any one of the promised words will do
            if wanted & set(words(m.group(1))):
                return True
        return False
    if kind == "hours":
        return re.search(r"\b24\s?/\s?7\b|\b24[\s-]?hours?\b|\baround the clock\b|\ball day\b", flat) is not None
    if kind == "sameday":
        return re.search(r"\bsame[\s-]day\b", flat) is not None
    if kind == "nocost":
        return re.search(r"\bno[\s-](?:cost|obligation|fees?|commitment)\b|\bfree\b", flat) is not None
    return False


def offer_status(offers, page):
    """[(offer, on the page?)] for every promise."""
    return [(o, on_page(o, page)) for o in offers]
