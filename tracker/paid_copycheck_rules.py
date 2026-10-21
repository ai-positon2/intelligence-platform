"""Paid Marketing Agents: Ad Copy Checker, what is wrong with a piece of ad text (no network, no clock).

Every rule here is a plain function of the text and what the team told us, so a finding can always be traced to the exact characters it points at. Nothing is guessed by a model:

  placeholder      {Brand}, [City], <name>, lorem ipsum, TBD: a template nobody filled in (a real ad customizer or keyword insertion is recognised and left alone)
  garbled          characters that were damaged on the way in (â€™ for an apostrophe, a replacement character)
  spelling         a word the dictionary does not know that is one or two letters from one it does; the account's own words, the brand, the team's allowed words and
                   words that appear in many of the account's ads spelt the same way are not doubted (the last ones are only listed, quietly)
  repeat           the same word twice in a row ("the the")
  gap              no space after a comma, colon or full stop, or a space before one
  punct            doubled punctuation (!!, ??, ,,)
  caps_line        a whole headline or description in capitals
  caps_word        one word in capitals that is not an acronym or a brand
  lower_start      a line that starts with a small letter (unless it is a brand written that way)
  odd_caps         a capital inside a word (DenTist) that is not a brand
  ending           descriptions of one ad that do not agree on whether they end with a full stop, and a headline that ends in one
  style            headlines of one ad that mix Title Case and sentence case
  phone            a phone number that is not on the account's list (when the team saved one), or an account whose ads carry several numbers (when it did not)
  place            a place the team saved for another account
  brand            the brand written two ways, or not the way the team saved it

The dictionary is the pyspellchecker package when it is installed (a soft dependency): without it the spelling check says it could not run, and the rest still does.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

try:                                                                                # a soft dependency: without it spelling is reported as not checked
    from spellchecker import SpellChecker
except Exception:                                                                   # noqa: BLE001
    SpellChecker = None

RULES = {
    "placeholder": {"level": "high", "label": "Unfilled placeholder", "fix": "Replace the placeholder with the real text, or remove the line."},
    "phone": {"level": "high", "label": "Phone number", "fix": "Use a number from the account's saved list, or add this one to the list if it is right."},
    "garbled": {"level": "medium", "label": "Garbled characters", "fix": "Retype the character (an apostrophe, a quote or an accent) in Google Ads or Ads Manager."},
    "spelling": {"level": "medium", "label": "Possible misspelling", "fix": "Correct the spelling, or add the word to the allowed words if it is right."},
    "repeat": {"level": "medium", "label": "Repeated word", "fix": "Delete the second copy of the word."},
    "gap": {"level": "medium", "label": "Spacing around punctuation", "fix": "Put one space after a comma, colon or full stop and none before it."},
    "punct": {"level": "medium", "label": "Doubled punctuation", "fix": "Keep one mark. Google Ads refuses repeated punctuation in some places."},
    "caps_line": {"level": "medium", "label": "Whole line in capitals", "fix": "Write it in sentence or title case. Google Ads limits excessive capitals."},
    "lower_start": {"level": "medium", "label": "Starts with a small letter", "fix": "Start the line with a capital, unless the brand is written that way."},
    "place": {"level": "medium", "label": "Place of another account", "fix": "Check the line belongs to this account, or add the place to this account's list."},
    "brand": {"level": "medium", "label": "Brand spelling", "fix": "Write the brand the same way everywhere, the way the client writes it."},
    "caps_word": {"level": "low", "label": "Word in capitals", "fix": "Write it in normal case unless it is an acronym or the brand."},
    "odd_caps": {"level": "low", "label": "Capital inside a word", "fix": "Check the word. Brands written that way go in the allowed words."},
    "ending": {"level": "low", "label": "Inconsistent ending", "fix": "End all descriptions of an ad the same way (all with a full stop, or none)."},
    "style": {"level": "low", "label": "Mixed capitalisation", "fix": "Use Title Case for every headline of the ad or sentence case for every one."},
    "vocab": {"level": "low", "label": "Unknown word used often", "fix": "If it is right (a product or a name), add it to the allowed words so it is not doubted again."},
}
ORDER = ["placeholder", "phone", "garbled", "spelling", "repeat", "gap", "punct", "caps_line", "lower_start", "place", "brand", "caps_word", "odd_caps", "ending", "style", "vocab"]
LEVEL_RANK = {"low": 1, "medium": 2, "high": 3}

LINE_SLOTS = {"headline", "description", "primary", "title", "link", "sitelink", "sitelinkdesc", "callout", "asset", "longheadline"}   # read as a sentence or a title
HEADLINE_SLOTS = {"headline", "title", "longheadline"}
DESCRIPTION_SLOTS = {"description", "primary", "link", "sitelinkdesc"}
VOCAB_ADS = 3                    # an unknown word in this many ads, spelt the same way, is the account's own word
MIN_WORD = 4                     # shorter words are not looked up
MAX_UNKNOWN = 400                # distinct unknown words looked up for suggestions in one account

_MASK_VALID = re.compile(r"\{(?:KeyWord|LOCATION\([^)]*\)|=[^}]*|COUNTDOWN\([^)]*\)|CUSTOMIZER\.[^}]*|[A-Za-z_]+\.[A-Za-z_]+)(?::([^}]*))?\}", re.I)
_PLACEHOLDER = re.compile(r"\{\{[^}]*\}\}|\{[^{}]{1,40}\}|\[[A-Za-z][A-Za-z _/-]{0,38}\]|<[A-Za-z][A-Za-z _/-]{0,38}>|\blorem ipsum\b|\b(?:TBD|TODO|XXX+|PLACEHOLDER)\b|\bINSERT (?:HERE|TEXT|CITY|NAME)\b|\bYOUR (?:CITY|NAME|BRAND|TEXT) HERE\b", re.I)
_GARBLED = re.compile(r"â€\S?|Ã[\u0080-\u00ff]|Â[\u00a0-\u00bf]|ï»¿|\ufffd")
_URL = re.compile(r"(?:https?://|www\.)\S+|\b[\w.-]+@[\w-]+\.[\w.]+|\b[a-z0-9-]+\.(?:com|net|org|io|co|us|ca|uk|health|dental|care|clinic|app|ai)\b(?:/\S*)?", re.I)
_WORD = re.compile(r"[A-Za-z][A-Za-z']*[A-Za-z]|[A-Za-z]")
_REPEAT = re.compile(r"\b([A-Za-z]{2,})(\s+)(\1)\b", re.I)
_GAP_AFTER = re.compile(r"[A-Za-z]{2,}[,;:](?=[A-Za-z])|[a-z]{2,}[.!?](?=[A-Z][a-z])")
_GAP_BEFORE = re.compile(r"(?<=[A-Za-z0-9])[ \t]+[,;:!?](?=\s|$)|(?<=[A-Za-z0-9])[ \t]+\.(?=\s|$)")
_DOUBLE = re.compile(r"[!?,;:]{2,}|\.,|,\.|(?<!\.)\.\.(?!\.)|\.{4,}")
_PHONE = re.compile(r"(?<![\d$.])(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?![\d%])")
_CAMEL_OK = re.compile(r"^(?:Mc|Mac|De|La|Le|Di|Van|Von|O')[A-Z]")
_SMALL = {"a", "an", "and", "as", "at", "by", "for", "in", "of", "on", "or", "the", "to", "with", "from", "vs", "via", "per", "your", "our", "is", "are", "be"}
ACRONYMS = {"USA", "HVAC", "HIPAA", "LLC", "INC", "CBD", "LASIK", "TMJ", "ADA", "FDA", "OSHA", "NASA", "NCAA", "USPS", "PPO", "HMO", "CPAP", "ACLS", "DUI", "EPA", "HVAC", "LGBTQ", "NEW",
            "FREE", "SALE", "OFF", "VIP", "DIY", "FAQ", "USDA", "LED", "UPS", "NFL", "NBA", "MLB", "FSA", "HSA", "IRS", "SBA", "RV", "EV", "RN", "MD", "DDS", "DMD", "CEO", "CPA", "API",
            "SEO", "PPC", "SaaS", "SMS", "GPS", "ATM", "DNA", "MRI", "IVF", "ADHD", "COVID", "STEM", "ESL", "GED", "SAT", "ACT", "MBA", "PhD"}
_DEFAULT_ALLOWED = {"google", "facebook", "instagram", "youtube", "whatsapp", "iphone", "ipad", "ebay", "linkedin", "tiktok", "wordpress", "shopify", "paypal", "zoom", "airbnb", "ios"}


class Speller:
    """What the spelling check needs from a dictionary: unknown(words), candidates(word), correction(word). Tests pass a stand-in with the same three."""

    def __init__(self, inner=None):
        self.inner = inner or (SpellChecker() if SpellChecker else None)
        self._cache = {}

    @property
    def ready(self):
        return self.inner is not None

    def unknown(self, words):
        return set(self.inner.unknown(words)) if self.inner else set()

    def candidates(self, word):
        if word not in self._cache:
            try:
                self._cache[word] = sorted(self.inner.candidates(word) or []) if self.inner else []
            except Exception:                                                       # noqa: BLE001
                self._cache[word] = []
        return self._cache[word]

    def correction(self, word):
        try:
            return self.inner.correction(word) if self.inner else None
        except Exception:                                                           # noqa: BLE001
            return None


# ── Text helpers ────────────────────────────────────────────────────────────

def norm_key(s):
    """A word or name with case, spaces and punctuation taken out: how two spellings of one brand are recognised as the same."""
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def phone_digits(s):
    """The ten digits of a US phone number (a leading 1 dropped), or '' when the text is not one."""
    d = re.sub(r"\D", "", str(s or ""))
    if len(d) == 11 and d.startswith("1"):
        d = d[1:]
    return d if len(d) == 10 else ""


def mask(text):
    """The text with real ad customizers and keyword insertions blanked out, but their default text kept (the same length, so every position still points into the original)."""
    def blank(m):
        whole = m.group(0)
        default = m.group(1)
        if default is None:
            return " " * len(whole)
        head = whole[:len(whole) - len(default) - 1]
        return " " * len(head) + default + " "
    return _MASK_VALID.sub(blank, text)


def _spans_of_urls(text):
    return [(m.start(), m.end()) for m in _URL.finditer(text)]


def _inside(pos, spans):
    return any(a <= pos < b for a, b in spans)


def words_of(text, skip_urls=True):
    """[(word, start, end)] of the words of a text, without the 's of a possessive and without anything inside a web address or an email."""
    spans = _spans_of_urls(text) if skip_urls else []
    out = []
    for m in _WORD.finditer(text):
        if _inside(m.start(), spans):
            continue
        w, a, b = m.group(0), m.start(), m.end()
        if w.lower().endswith("'s"):
            w, b = w[:-2], b - 2
        if not w or (a > 0 and text[a - 1].isdigit()) or (b < len(text) and text[b:b + 1].isdigit()) or (a > 1 and text[a - 1] == "-" and text[a - 2].isdigit()):
            continue                                                                # next to a digit it is a code or a vanity number, not a word
        out.append((w, a, b))
    return out


def context(*, allowed=(), brands=(), phones=(), places=(), other_places=None, speller=None, vocab=None, own=None, account_words=()):
    """What the rules need that is not in the text: the words never to doubt, the brand spellings the team saved, the account's phone numbers and places, the places saved for other
    accounts ({place: account name}), the dictionary, and how many ads each unknown word was found in ({word: ads})."""
    ok = {str(w).lower() for w in allowed} | _DEFAULT_ALLOWED | {w.lower() for w in account_words}
    for b in brands:
        ok |= {t.lower() for t in re.findall(r"[A-Za-z']+", str(b))} | {norm_key(b)}
    return {"allowed": ok, "brands": [str(b) for b in brands if str(b).strip()], "phones": {phone_digits(p) for p in phones if phone_digits(p)}, "places": [str(p) for p in places if str(p).strip()],
            "otherPlaces": dict(other_places or {}), "speller": speller, "vocab": vocab or {}, "own": own or {}}


def _f(rule, text, a, b, message, suggestion="", level=None):
    return {"rule": rule, "level": level or RULES[rule]["level"], "start": a, "end": b, "excerpt": text[a:b], "message": message, "suggestion": suggestion}


# ── The rules, one text at a time ───────────────────────────────────────────

def _placeholder(text):
    raw = mask(text)
    return [_f("placeholder", text, m.start(), m.end(), "%s looks like a template that was never filled in." % repr(m.group(0))[1:-1]) for m in _PLACEHOLDER.finditer(raw)
            if not _MASK_VALID.fullmatch(m.group(0))]


def _garbled(text):
    return [_f("garbled", text, m.start(), m.end(), "%s is a damaged character (an apostrophe or an accent that was saved wrongly)." % m.group(0)) for m in _GARBLED.finditer(text)]


def _repeat(text, ctx):
    out = []
    for m in _REPEAT.finditer(text):
        w = m.group(1).lower()
        if w in ctx["allowed"] or w in ("had", "that", "bye", "no", "so", "ha", "very"):
            continue
        if norm_key(m.group(0)) in ctx["allowed"]:
            continue
        out.append(_f("repeat", text, m.start(), m.end(), "\"%s\" is written twice in a row." % m.group(1), m.group(1)))
    return out


def _gaps(text):
    out = []
    spans = _spans_of_urls(text)
    for m in _GAP_AFTER.finditer(text):
        if _inside(m.start(), spans) or _inside(m.end(), spans):
            continue
        out.append(_f("gap", text, m.start(), m.end() + 1, "There is no space after the mark in \"%s\"." % text[m.start():m.end() + 1]))
    for m in _GAP_BEFORE.finditer(text):
        out.append(_f("gap", text, m.start(), m.end(), "There is a space before the mark in \"%s\"." % text[max(0, m.start() - 6):m.end()].strip()))
    return out


def _punct(text):
    return [_f("punct", text, m.start(), m.end(), "\"%s\" repeats punctuation." % m.group(0)) for m in _DOUBLE.finditer(text)]


def _caps(text, slot, ctx):
    out, ws = [], words_of(text)
    real = [w for w, _a, _b in ws if len(w) >= 2 and w.upper() not in ACRONYMS and w.lower() not in ctx["allowed"] and norm_key(w) not in ctx["allowed"]]
    shouting = [(w, a, b) for w, a, b in ws if len(w) >= 4 and w.isupper() and w not in ACRONYMS and w.lower() not in ctx["allowed"] and norm_key(w) not in ctx["allowed"]]
    if slot in LINE_SLOTS and len(real) >= 2 and shouting and all(w.isupper() for w in real):
        return [_f("caps_line", text, 0, len(text), "The whole line is in capitals.")]
    for w, a, b in shouting:
        out.append(_f("caps_word", text, a, b, "\"%s\" is in capitals." % w, w.capitalize()))
    for w, a, b in ws:
        if len(w) >= 4 and re.search(r"[a-z][A-Z]", w) and not _CAMEL_OK.match(w) and w.lower() not in ctx["allowed"] and norm_key(w) not in ctx["allowed"] and not w.isupper():
            out.append(_f("odd_caps", text, a, b, "\"%s\" has a capital inside the word." % w, w[0] + w[1:].lower()))
    return out


def _lower_start(text, slot, ctx, raw):
    if slot not in LINE_SLOTS or not text.strip() or raw.lstrip().startswith("{"):                  # a line that starts with a customizer starts with whatever it inserts
        return []
    s = text.lstrip()
    off = len(text) - len(s)
    if not s[:1].isalpha() or not s[:1].islower():
        return []
    first = _WORD.match(s)
    w = first.group(0) if first else ""
    if w.lower() in ctx["allowed"] or norm_key(w) in ctx["allowed"] or re.match(r"^[a-z]+[A-Z]", s):
        return []
    return [_f("lower_start", text, off, off + len(w), "The line starts with a small letter (\"%s\")." % w, w[:1].upper() + w[1:])]


def _spelling(text, ctx):
    """[(finding, word)] for the words the dictionary does not know and has a near word for. Whether it is told or only listed is decided with the account's other ads (see analyse)."""
    sp = ctx["speller"]
    if not sp or not sp.ready:
        return []
    cands = [(w, a, b) for w, a, b in words_of(text) if len(w) >= MIN_WORD and not w.isupper() and w.lower() not in ctx["allowed"] and norm_key(w) not in ctx["allowed"]]
    unknown = sp.unknown([w.lower() for w, _a, _b in cands])
    out = []
    for w, a, b in cands:
        lw = w.lower()
        if lw not in unknown:
            continue
        near = sp.candidates(lw)
        own = ctx["own"]
        pick = next((c for c in near if c != lw and own.get(c, 0) >= 2), None) or (sp.correction(lw) if sp.correction(lw) not in (None, lw) else None) or (near[0] if near else None)
        if not pick or pick == lw:
            continue
        out.append((_f("spelling", text, a, b, "\"%s\" is not a word I know. Did you mean \"%s\"?" % (w, pick), pick), lw))
    return out


def _phones(text, ctx):
    out = []
    for m in _PHONE.finditer(text):
        d = phone_digits(m.group(0))
        if not d:
            continue
        if ctx["phones"] and d not in ctx["phones"]:
            out.append(_f("phone", text, m.start(), m.end(), "%s is not one of the numbers saved for this account." % m.group(0).strip()))
    return out


def _places(text, ctx):
    out = []
    mine = {p.lower() for p in ctx["places"]}
    low = text.lower()
    for place, owner in ctx["otherPlaces"].items():
        pl = place.lower()
        if pl in mine or any(pl in x or x in pl for x in mine):
            continue
        city = pl.split(",")[0].strip()
        if len(city) < 3:
            continue
        for m in re.finditer(r"\b%s\b" % re.escape(city), low):
            out.append(_f("place", text, m.start(), m.end(), "\"%s\" is a place saved for %s, not for this account." % (text[m.start():m.end()], owner)))
            break
    return out


def check_text(text, slot, ctx):
    """Every finding in one text, left to right. `slot` is what the text is (headline, description, primary, ...); the rules that read a text as a sentence skip a path or a snippet."""
    text = str(text or "")
    if not text.strip():
        return []
    out = _placeholder(text) + _garbled(text)
    masked = mask(text)
    out += _repeat(masked, ctx) + _gaps(masked) + _punct(masked) + _caps(masked, slot, ctx) + _lower_start(masked, slot, ctx, text)
    out += [f for f, _w in _spelling(masked, ctx)] + _phones(masked, ctx) + _places(masked, ctx)
    out.sort(key=lambda f: (f["start"], ORDER.index(f["rule"])))
    return out


# ── Rules over the texts of one ad ──────────────────────────────────────────

def _case_style(text, ctx):
    """'title', 'sentence' or '' for a headline of three words or more, looking only at the words long enough to tell."""
    ws = [w for w, _a, _b in words_of(text) if len(w) >= 4 and w.lower() not in _SMALL and w.lower() not in ctx["allowed"]]
    if len(ws) < 2:
        return ""
    up = [w[0].isupper() for w in ws]
    if all(up):
        return "title"
    if up[0] and sum(up[1:]) <= 1:                                                  # one later capital is allowed: it is most likely a name (Boston)
        return "sentence"
    return ""


def check_ad(ad, ctx):
    """[{slot, text, ...finding}] for one ad: its texts one by one, then what only shows across them (endings, case style)."""
    out, texts = [], ad.get("texts") or []
    for t in texts:
        for f in check_text(t["text"], t["slot"], ctx):
            out.append(dict(f, slot=t["slot"], text=t["text"]))
    heads = [t for t in texts if t["slot"] in HEADLINE_SLOTS and t["text"].strip()]
    for t in heads:
        if t["text"].rstrip().endswith((".", ",")) and not t["text"].rstrip().endswith(("..", "etc.", "Inc.", "Ltd.", "Dr.")):
            end = len(t["text"].rstrip())
            out.append(dict(_f("ending", t["text"], end - 1, end, "The headline ends with \"%s\"." % t["text"].rstrip()[-1]), slot=t["slot"], text=t["text"]))
    descs = [t for t in texts if t["slot"] in DESCRIPTION_SLOTS and t["slot"] != "primary" and t["text"].strip()]
    if len(descs) >= 2:
        ends = [t["text"].rstrip().endswith(".") for t in descs]
        if any(ends) and not all(ends):
            majority = ends.count(True) > ends.count(False)
            for t, e in zip(descs, ends):
                if e != majority:
                    end = len(t["text"].rstrip())
                    msg = ("The other descriptions of this ad end with a full stop; this one does not." if majority else "The other descriptions of this ad do not end with a full stop; this one does.")
                    out.append(dict(_f("ending", t["text"], max(0, end - 1), end, msg), slot=t["slot"], text=t["text"]))
    styles = [(t, _case_style(t["text"], ctx)) for t in heads if len(words_of(t["text"])) >= 3]
    kinds = Counter(s for _t, s in styles if s)
    if len(kinds) == 2 and min(kinds.values()) >= 1 and sum(kinds.values()) >= 3:
        minority = min(kinds, key=lambda k: kinds[k])
        for t, s in styles:
            if s == minority:
                out.append(dict(_f("style", t["text"], 0, len(t["text"]), "This headline is in %s case; the other headlines of the ad are in %s case." % (minority, "title" if minority == "sentence" else "sentence")),
                                slot=t["slot"], text=t["text"]))
    return out


def level_of(findings):
    """The highest level among findings, or ''."""
    best = max((LEVEL_RANK[f["level"]] for f in findings), default=0)
    return {3: "high", 2: "medium", 1: "low"}.get(best, "")
