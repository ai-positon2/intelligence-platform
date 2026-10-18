"""Paid Marketing Agents: Keyword Research, the arithmetic.

Everything a person reads as a number or a status on the Keyword Research page is worked out here, in code, from what Keyword Planner and the account said. The
model is never asked for a number and cannot change one: it judges fit, intent and themes (tracker/paid_keywords_ai.py) and what it says is held to what is here.

  parse_idea           a Keyword Planner result as one row: searches, competition, bids and the twelve months
  pattern              how a keyword moves through the year: peak month, how far above average, rising or falling
  status_of            is the keyword idea already in the account ("bidding"), already matched by a phrase or broad keyword ("covered"), or "new"
  negated_by           which negative keywords (in a campaign or a shared list) already block it, and in how many campaigns
  candidates           the keyword ideas worth judging: past the floor, not avoided, not blocked everywhere, merged with their close variants, the most relevant first
  assemble             the judged candidates as rows, the themes with their totals, the coverage, and the Google Ads Editor rows

No network and no database.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from . import paid_strategy_check as ck

MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]
MONTH_NAMES = [m.title() for m in MONTHS]
CANDIDATES = 480                 # what is judged: enough to cover a market, few enough to judge in four calls
NEW_SLOTS = 380                  # of those, ideas the account does not have (the rest are ideas it already has, so the coverage can be told)
CHUNK = 120                      # how many the model judges in one answer
TERM_SLOTS = 60                  # converting search terms that are not keywords: as many as the account read gives (60) are kept
SEASONAL_SWING = 1.5             # the peak month this many times the average makes a keyword seasonal
TREND_CHANGE = 0.25              # the last three months this far above the first three make it rising; the same ratio the other way (a fifth below) makes it falling
FITS = ("yes", "maybe", "no")
INTENTS = ("transactional", "commercial", "local", "informational", "brand", "competitor", "support", "jobs", "other")
MATCHES = ("exact", "phrase", "broad")
PRIORITIES = ("high", "medium", "low")

# Words that say nothing about what a person is looking for, so they do not make a keyword relevant to the client.
GENERIC = {"near", "me", "best", "cost", "costs", "price", "prices", "cheap", "affordable", "top", "in", "for", "the", "of", "and", "a", "an", "to", "how", "much", "what", "is",
           "do", "does", "vs", "with", "my", "your", "on", "at", "by", "from", "or", "are", "can", "services", "service", "company", "companies", "local", "reviews", "review"}


# ── words ───────────────────────────────────────────────────────────────────

def words(s):
    """The lower case words of a keyword, in any language. An apostrophe is dropped (women's is womens), anything else that is not a letter or a digit separates words."""
    return re.findall(r"[^\W_]+", str(s or "").lower().replace("'", "").replace("\u2019", ""))


def stem(w):
    """A word with its plural taken off, so dentist and dentists, or study and studies, compare equal. Only used to compare, never shown."""
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def stems(s):
    return tuple(stem(w) for w in words(s))


def skey(s):
    """The same for keywords that differ only in plurals or word order (dental implant cost, cost of dental implants is not one: it has a word more)."""
    return " ".join(sorted(stems(s)))


# ── Keyword Planner results ─────────────────────────────────────────────────

def _micros(v):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return round(n / 1e6, 2) if n > 0 else None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def monthly(metrics):
    """{'2025-10': searches, ...} from Planner's monthlySearchVolumes (each has a month name, a year and a count)."""
    out = {}
    for mm in (metrics or {}).get("monthlySearchVolumes") or []:
        try:
            i = MONTHS.index(str(mm["month"]).upper()) + 1
            out["%04d-%02d" % (int(mm["year"]), i)] = out.get("%04d-%02d" % (int(mm["year"]), i), 0) + _int(mm.get("monthlySearches"))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def parse_idea(r):
    """One Planner result (an idea, or a historical metric: Google names the block differently) as a row. None when it has no text."""
    r = r if isinstance(r, dict) else {}
    m = r.get("keywordIdeaMetrics") or r.get("keywordMetrics") or {}
    text = " ".join(str(r.get("text") or "").lower().split())
    if not text:
        return None
    return {"kw": text, "avg": _int(m.get("avgMonthlySearches")), "comp": str(m.get("competition") or "").title() if m.get("competition") not in (None, "UNSPECIFIED", "UNKNOWN") else "",
            "idx": _int(m["competitionIndex"]) if m.get("competitionIndex") not in (None, "") else None,
            "lo": _micros(m.get("lowTopOfPageBidMicros")), "hi": _micros(m.get("highTopOfPageBidMicros")), "m": monthly(m)}


def _ym_name(ym):
    return MONTH_NAMES[int(ym[5:7]) - 1]


def pattern(m):
    """How a keyword's twelve months move: {'peak', 'low', 'swing' (peak over average), 'trend' (last three months against the first three), 'label'}.
    Under six months of history there is no pattern to describe."""
    items = sorted((m or {}).items())
    vals = [v for _, v in items]
    if len(vals) < 6 or not sum(vals):
        return {"peak": None, "low": None, "swing": None, "trend": None, "label": ""}
    avg = sum(vals) / len(vals)
    peak, low = max(items, key=lambda kv: kv[1]), min(items, key=lambda kv: kv[1])
    first, last = sum(vals[:3]) / 3, sum(vals[-3:]) / 3
    trend = round((last - first) / first, 2) if first > 0 else None
    swing = round(peak[1] / avg, 2) if avg else None
    at = [k for k, _ in items].index(peak[0])
    half = len(vals) // 2
    m1, m2 = sorted(vals[:half])[half // 2], sorted(vals[-half:])[half // 2]
    ratio = 1 + TREND_CHANGE
    steady_move = m1 > 0 and (m2 >= m1 * ratio or m2 <= m1 / ratio)  # the middle of the later months against the middle of the earlier ones: one spike does not move it
    run_up = steady_move and (at >= len(vals) - 3 or at <= 2)       # the peak at one end of a steady climb or fall is a trend, not a season
    if swing is not None and swing >= SEASONAL_SWING and not run_up:
        label = "Peaks in %s, %.1f times the monthly average" % (_ym_name(peak[0]), swing)
    elif trend is not None and trend >= TREND_CHANGE:
        label = "Rising: the last three months are %d%% above the first three" % round(trend * 100)
    elif trend is not None and trend <= 1 / ratio - 1:
        label = "Falling: the last three months are %d%% below the first three" % round(-trend * 100)
    else:
        label = "Steady through the year"
    return {"peak": _ym_name(peak[0]), "low": _ym_name(low[0]), "swing": swing, "trend": trend, "label": label}


# ── What the account already has ────────────────────────────────────────────

def _contains(seq, sub):
    n = len(sub)
    return n > 0 and any(seq[i:i + n] == sub for i in range(len(seq) - n + 1))


def _rarest_index(entries):
    """{word: [entry, ...]} with each entry filed under its rarest word. An idea can only match an entry whose every word it holds, so it holds the entry's rarest word too:
    looking up the idea's words finds every entry that can match, and a word that begins half the account's keywords (dental) does not make every idea scan them all.
    `entries` are (words, entry); ties for the rarest word go to the longer word, then the alphabet, so the same account is always indexed the same way."""
    df = {}
    for ws, _e in entries:
        for w in set(ws):
            df[w] = df.get(w, 0) + 1
    idx = {}
    for ws, e in entries:
        idx.setdefault(min(set(ws), key=lambda w: (df[w], -len(w), w)), []).append(e)
    return idx


def index_keywords(keywords):
    """The account's keywords for comparing: [{'t' text, 'm' match, 'c' campaign, 'g' ad group}] filed by their rarest word."""
    entries = []
    for k in keywords or []:
        st = stems(k.get("t"))
        if st:
            entries.append((st, (st, tuple(sorted(st)), str(k.get("m") or "").upper(), k.get("t") or "", k.get("c") or "", k.get("g") or "")))
    return _rarest_index(entries)


def status_of(idea, index):
    """('bidding', by) when the account has this keyword (in any match type), ('covered', by) when a phrase or broad keyword it has would already match it,
    else ('new', None). `by` is the keyword that decides it: {'kw', 'match', 'campaign'}. The longest covering keyword is named (the first in alphabetical order of two the same length)."""
    st = stems(idea)
    if not st:
        return "new", None
    sset, ordered = set(st), tuple(sorted(st))
    covering = None
    for w in sorted(sset):
        for kst, korder, match, text, camp, _grp in index.get(w, ()):
            by = {"kw": text, "match": match.title(), "campaign": camp}
            if korder == ordered:
                return "bidding", by
            if match == "EXACT":
                continue
            hit = _contains(st, kst) if match == "PHRASE" else set(kst) <= sset
            if hit and (covering is None or (len(kst), text) > (covering[0], covering[2])):
                covering = (len(kst), by, text)
    return ("covered", covering[1]) if covering else ("new", None)


def index_negatives(negatives, lists):
    """The account's negative keywords by their first word: [{'t','m','c': [campaigns]}] and the shared lists [{'n','c','k': [[text, match]]}]. A negative is
    compared as written (a negative does not match close variants)."""
    entries = []
    for n in negatives or []:
        w = words(n.get("t"))
        if w:
            entries.append((w, (tuple(w), str(n.get("m") or "").upper(), n.get("t") or "", set(n.get("c") or []))))
    for lst in lists or []:
        for text, match in lst.get("k") or []:
            w = words(text)
            if w:
                entries.append((w, (tuple(w), str(match or "").upper(), text, set(lst.get("c") or []))))
    return _rarest_index(entries)


def negated_by(idea, neg_index):
    """[{'kw', 'match', 'campaigns': [...]}] the negatives that block this idea: exact is the same words, phrase is these words together in order, broad is all of them."""
    iw = words(idea)
    out = []
    for w in set(iw):
        for nw, match, text, camps in neg_index.get(w, ()):
            if match == "EXACT":
                hit = list(nw) == iw
            elif match == "PHRASE":
                hit = _contains(tuple(iw), nw)
            else:
                hit = set(nw) <= set(iw)
            if hit:
                out.append({"kw": text, "match": match.title(), "campaigns": sorted(camps)})
    return out


def neg_covered_by(text, match, neg_index):
    """[{'kw', 'match', 'campaigns'}] the account's negatives that already block everything a negative to be added (`text` in `match` type: exact, phrase or broad)
    would block. Exact blocks one search; phrase blocks a search holding its words together; broad one holding all its words: so an existing exact [jobs] covers
    nothing a new phrase "jobs" would, an existing phrase "jobs" covers a new exact [dental jobs], and a broad one covers a phrase or a broad one with more words."""
    nw = words(text)
    nset = set(nw)
    out = []
    for w in nset:
        for ew, ematch, etext, camps in neg_index.get(w, ()):
            eset = set(ew)
            if match == "exact":
                hit = list(ew) == nw if ematch == "EXACT" else (_contains(tuple(nw), ew) if ematch == "PHRASE" else eset <= nset)
            elif match == "phrase":
                hit = _contains(tuple(nw), ew) if ematch == "PHRASE" else (eset <= nset if ematch == "BROAD" else False)
            else:
                hit = eset <= nset if ematch == "BROAD" else (ematch == "PHRASE" and len(ew) == 1)
            if hit:
                out.append({"kw": etext, "match": ematch.title(), "campaigns": sorted(camps)})
    return out


def blocked_scope(hits, keyword_campaigns):
    """'all' when the negatives that block an idea cover every campaign the account bids in, 'some' when they cover only part of them, '' when none block it."""
    if not hits:
        return ""
    blocked = set().union(*[set(h["campaigns"]) for h in hits])
    return "all" if keyword_campaigns and set(keyword_campaigns) <= blocked else "some"


# ── Who is the client, who is a competitor ──────────────────────────────────

COMMON_NAME_WORDS = {"dental", "dentist", "dentists", "clinic", "clinics", "care", "health", "healthcare", "medical", "center", "centre", "group", "law", "legal", "lawyers", "attorneys",
                     "plumbing", "roofing", "hvac", "auto", "home", "family", "services", "service", "company", "solutions", "studio", "the", "and", "of", "inc", "llc", "ltd", "co"}
SECOND_LEVEL = {"co", "com", "org", "net", "gov", "edu", "ac"}


def _stem_of_host(url):
    host = (urlsplit(str(url or "")).hostname or "").lower().removeprefix("www.")
    parts = host.split(".")
    if len(parts) >= 3 and parts[-2] in SECOND_LEVEL:
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else ""


COMMON_STEMS = {stem(w) for w in COMMON_NAME_WORDS}


def _name_words(s):
    """The words of a name, plurals taken off and 'and' left out (Smith & Jones, Smith and Jones)."""
    return tuple(stem(w) for w in words(s) if w != "and")


def name_marks(name, url=""):
    """What identifies a business in a keyword: its whole name as words (unless every word of it is a common word of its trade, like Dental Care, which every
    search for the trade holds; or the 'name' is only a web address), and the first part of its web address (acmedental for acmedental.com), unless that is a
    common word of its trade too (dentist.com)."""
    w = _name_words(name)
    if re.fullmatch(r"\S+\.[a-z]{2,}", str(name or "").strip().lower()):
        w = ()
    generic = bool(w) and all(x in COMMON_STEMS for x in w)
    host = re.sub(r"[^\w]", "", _stem_of_host(url))
    if host in GENERIC or host in COMMON_NAME_WORDS or stem(host) in COMMON_STEMS:
        host = ""
    return {"words": () if generic else w, "stem": host if len(host) >= 4 else "", "generic": generic}


def _joined(kws, target):
    """True when some run of the keyword's words, written together, is the target (smile austin for smileaustin: people type a name with spaces)."""
    for i in range(len(kws)):
        acc = ""
        for w in kws[i:]:
            acc += w
            if acc == target:
                return True
    return False


def names_it(kw, marks):
    """True when the keyword holds the business's whole name (in order), or the stem of its web address: as a word of its own, as a run of words written together,
    or inside a longer word (for a stem of six letters or more)."""
    kws = tuple(words(kw))
    if marks["words"] and _contains(_name_words(kw), marks["words"]):
        return True
    stem_ = marks["stem"]
    if not stem_:
        return False
    if marks.get("generic"):                                          # a trade's common words written together (dental care) are in every search for the trade
        return stem_ in kws
    return any(w == stem_ or (len(stem_) >= 6 and stem_ in w) for w in kws) or _joined(kws, stem_)


def brand_marks(inputs):
    return name_marks(inputs.get("name"), inputs.get("website"))


def competitor_marks(inputs, research):
    marks, seen = [], set()
    read = (((research or {}).get("competitors") or {}).get("data") or {}).get("competitors") or []
    for c in list(inputs.get("competitors") or []) + [c for c in read if isinstance(c, dict)]:
        m = name_marks(c.get("name"), c.get("url"))
        key = (m["words"], m["stem"])
        if (m["words"] or m["stem"]) and key not in seen:
            seen.add(key)
            marks.append(dict(m, name=c.get("name") or ""))
    return marks


def avoided(kw, avoid_phrases):
    """True when the keyword holds every word of a phrase the client wants to stay away from."""
    kws = set(stems(kw))
    for p in avoid_phrases or []:
        ps_ = {w for w in stems(p)}
        if ps_ and ps_ <= kws:
            return True
    return False


# ── The candidates ──────────────────────────────────────────────────────────

def _seed_stems(inputs, research, avoid_phrases):
    from . import paid_keywords as pk
    texts = list(pk.phrases(inputs.get("services")))
    biz = (((research or {}).get("business") or {}).get("data") or {})
    texts += [o.get("name") for o in biz.get("offerings") or []]
    site = (((research or {}).get("website") or {}).get("data") or {})
    for pg in (site.get("pages") or [])[1:8]:
        texts += list(pg.get("h1") or []) + [re.split(r"[|:]", pg.get("title") or "")[0]]
    out = set()
    for t in texts:
        out |= {s for s in stems(t) if s not in GENERIC and len(s) > 2}
    for p in avoid_phrases or []:
        out -= set(stems(p))
    return out


def candidates(inputs, research):
    """(rows, stats): the keyword ideas worth judging.

    Planner's ideas and the converting search terms that are not yet keywords, with close variants merged (the biggest is kept and says how many it stands for),
    the ones below the client's floor, on the avoid list or blocked in every campaign by a negative the account has left out, and the rest ranked: the converting
    terms first, then the ones that share a word with what the client sells, by searches. Of those kept, NEW_SLOTS are ideas the account does not have and the
    rest ideas it does, so the coverage can be told."""
    from . import paid_keywords as pk
    inputs = inputs or {}
    r = research or {}
    planner = ((r.get("planner") or {}).get("data")) or {}
    account = ((r.get("account") or {}).get("data")) or {}
    floor = int(inputs.get("minSearches") or pk.DEFAULT_MIN)
    avoid_phrases = pk.phrases(inputs.get("avoid"))
    kidx = index_keywords(account.get("keywords"))
    nidx = index_negatives(account.get("negatives"), account.get("lists"))
    kcamps = sorted({k.get("c") for k in account.get("keywords") or [] if k.get("c")})
    seeds = _seed_stems(inputs, r, avoid_phrases)
    brand, rivals = brand_marks(inputs), competitor_marks(inputs, r)
    stats = {"ideas": 0, "belowFloor": 0, "avoided": 0, "blockedEverywhere": 0, "merged": 0, "terms": 0}

    pool = {}

    def add(row, src):
        k = skey(row["kw"])
        have = pool.get(k)
        if have is None:
            pool[k] = dict(row, **{"from": list(row.get("from") or []), "vars": 1, "src": src})
            return
        stats["merged"] += 1
        have["vars"] += 1
        for label in row.get("from") or []:
            if label not in have["from"] and len(have["from"]) < 4:
                have["from"].append(label)
        if (row.get("avg") or 0, len(row["kw"])) > (have.get("avg") or 0, len(have["kw"])):
            keep = have["from"], have["vars"], have.get("conv"), have["src"]
            have.update({x: row[x] for x in ("kw", "avg", "comp", "idx", "lo", "hi", "m") if x in row})
            have["from"], have["vars"], have["conv"], have["src"] = keep

    for idea in planner.get("ideas") or []:
        stats["ideas"] += 1
        add(idea, "idea")
    metrics = planner.get("termMetrics") or {}
    for t in ((account.get("terms") or {}).get("converting")) or []:
        term = " ".join(str(t.get("term") or "").lower().split())
        if not term or status_of(term, kidx)[0] == "bidding":
            continue
        stats["terms"] += 1
        base = dict(metrics.get(term) or {"kw": term, "avg": None, "comp": "", "idx": None, "lo": None, "hi": None, "m": {}}, kw=term)
        base["conv"] = {"clicks": t.get("clicks"), "cost": t.get("cost"), "conv": t.get("conv"), "campaign": t.get("campaign")}
        base["from"] = ["a converting search term"]
        k = skey(term)
        if k in pool:
            pool[k]["conv"] = base["conv"]
            if "a converting search term" not in pool[k]["from"]:
                pool[k]["from"].append("a converting search term")
        else:
            pool[k] = dict(base, vars=1, src="term")

    kept = []
    for row in pool.values():
        conv = bool(row.get("conv"))
        if not conv and (row.get("avg") or 0) < floor:
            stats["belowFloor"] += 1
            continue
        if avoided(row["kw"], avoid_phrases):
            stats["avoided"] += 1
            continue
        hits = negated_by(row["kw"], nidx)
        scope = blocked_scope(hits, kcamps)
        if scope == "all":
            stats["blockedEverywhere"] += 1
            continue
        status, by = status_of(row["kw"], kidx)
        row = dict(row, status=status, by=by, neg=({"scope": scope, "by": hits[:3], "campaigns": sorted(set().union(*[set(h["campaigns"]) for h in hits]))} if hits else None),
                   overlap=len({s for s in stems(row["kw"]) if s in seeds}),
                   brand=names_it(row["kw"], brand), rival=next((m["name"] or "a competitor" for m in rivals if names_it(row["kw"], m)), ""))
        kept.append(row)

    def rank(x):
        return (0 if x.get("conv") else 1, 0 if x["overlap"] else 1, -(x.get("avg") or 0), x["kw"])

    market = [x for x in kept if x["overlap"] and x.get("avg")]                         # the market as far as the client's own words reach, before any slot is given
    stats.update(poolIdeas=len(market), poolSearches=sum(x["avg"] for x in market), poolCovered=sum(x["avg"] for x in market if x["status"] != "new"))
    new = sorted([x for x in kept if x["status"] == "new"], key=rank)
    have = sorted([x for x in kept if x["status"] != "new"], key=rank)
    terms = [x for x in new if x.get("conv")][:TERM_SLOTS]
    rest = [x for x in new if not x.get("conv")]
    new_chosen = terms + rest[:max(0, NEW_SLOTS - len(terms))]
    chosen = new_chosen + have[:max(0, CANDIDATES - len(new_chosen))]
    chosen.sort(key=lambda x: (-(x.get("avg") or 0), x["kw"]))
    stats.update(considered=len(pool), kept=len(kept), judged=len(chosen), newKept=len(new), haveKept=len(have))
    return chosen, stats


def pool_coverage(stats):
    """The share of the searches the account already covers, over every idea that was kept and shares a word with what the client sells (not only the ideas that were
    judged: those are the biggest new ones and a few the account has, so their split says nothing about the market): {'poolPct', 'poolSearches', 'poolCovered', 'poolIdeas'}."""
    s, c = (stats or {}).get("poolSearches") or 0, (stats or {}).get("poolCovered") or 0
    return {"poolPct": round(c / s * 100) if s else None, "poolSearches": s, "poolCovered": c, "poolIdeas": (stats or {}).get("poolIdeas") or 0}


def chunks(rows, size=None):
    size = size or CHUNK
    return [rows[i:i + size] for i in range(0, len(rows), size)]


def signature(rows):
    """What identifies a set of candidates, so judged chunks kept from an earlier run are only reused for the same candidates."""
    import hashlib
    return hashlib.sha1("\n".join(x["kw"] for x in rows).encode("utf-8")).hexdigest()[:16]


# ── The judged candidates as rows, themes and coverage ──────────────────────

def slug(text, n=40):
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")[:n] or "other"


def _sum_months(rows):
    tot = {}
    for r in rows:
        for ym, v in (r.get("m") or {}).items():
            tot[ym] = tot.get(ym, 0) + v
    return tot


def _wavg(rows, key):
    pairs = [(r[key], r.get("avg") or 0) for r in rows if r.get(key) is not None]
    w = sum(p[1] for p in pairs)
    return round(sum(v * x for v, x in pairs) / w, 2) if w else (round(sum(p[0] for p in pairs) / len(pairs), 2) if pairs else None)


def campaign_for(theme):
    """The campaign a theme's keywords would go in: the account's own campaign when the theme says one fits, else a new one named the way the QA Agent expects."""
    return theme.get("campaign") or "P2_Search_%s" % "".join(w.capitalize() for w in re.findall(r"[A-Za-z0-9]+", theme.get("name") or "Other"))[:40]


def ad_group_for(theme, intent):
    return "%s | %s" % ((theme.get("name") or "Other")[:40], (intent or "other").capitalize())


def editor_ok(kw):
    """True when Google Ads takes the keyword as it is: letters, digits, & ' + and spaces, a hyphen inside a word, at most 10 words and 80 characters."""
    return bool(re.fullmatch(r"[\w&'+]+(?:[ \-][\w&'+]+)*", kw)) and len(kw) <= 80 and len(kw.split()) <= 10


def _row(c, j, theme_keys):
    fit = j.get("fit") if j.get("fit") in FITS else "unjudged"
    intent = j.get("intent") if j.get("intent") in INTENTS else "other"
    if c.get("brand"):
        intent = "brand"
    elif c.get("rival"):
        intent = "competitor"
    theme = j.get("theme") if j.get("theme") in theme_keys else "other"
    pat = pattern(c.get("m"))
    row = {"kw": c["kw"], "avg": c.get("avg"), "comp": c.get("comp") or "", "idx": c.get("idx"), "lo": c.get("lo"), "hi": c.get("hi"), "m": c.get("m") or {},
           "peak": pat["peak"], "low": pat["low"], "swing": pat["swing"], "trend": pat["trend"], "pattern": pat["label"], "from": c.get("from") or [], "vars": c.get("vars", 1),
           "src": c.get("src") or "idea", "status": c.get("status") or "new", "by": c.get("by"), "neg": c.get("neg"), "conv": c.get("conv"), "rival": c.get("rival") or "",
           "fit": fit, "intent": intent, "theme": theme, "match": j.get("match") if j.get("match") in MATCHES else "phrase", "priority": j.get("priority") if j.get("priority") in PRIORITIES else "medium",
           "note": j.get("note") or "", "blockedInTarget": False}
    if row["conv"]:
        row["match"] = "exact"                          # a search term that converted is added as exact whatever the model said: a broader keyword already covers it, and exact is what pins it
    row["editorOk"] = editor_ok(c["kw"])
    row["include"] = bool(fit == "yes" and (row["status"] == "new" or row["conv"]) and intent != "competitor" and row["editorOk"])        # a converting search term is worth adding as exact even where a broader keyword already matches it
    return row


def assemble(cands, judged, themes):
    """{'keywords': rows, 'themes': themes with their totals, 'coverage': {...}} from the candidates, the model's judgement of each (a dict by keyword) and the checked themes.
    Every number here is the candidates' own, summed. The coverage here is over the keywords judged a fit (`pct`); the share over the whole market the client's words reach is
    `pool_coverage` of the candidates' stats."""
    keys = [t["key"] for t in themes]
    rows = [_row(c, (judged or {}).get(c["kw"]) or {}, keys) for c in cands]
    by_theme = {}
    for r in rows:
        by_theme.setdefault(r["theme"], []).append(r)
    all_themes = list(themes)
    if by_theme.get("other"):
        all_themes.append({"key": "other", "name": "Other", "intent": "other", "priority": "low", "why": "Keywords that fit none of the themes.", "campaign": "", "existing": False})
    target = {t["key"]: campaign_for(t) for t in all_themes}
    for r in rows:                                                       # a keyword the campaign it would go in already blocks with a negative keyword would never serve there
        if r["include"] and target[r["theme"]] in ((r["neg"] or {}).get("campaigns") or []):
            r["include"], r["blockedInTarget"] = False, True
    out = []
    for t in all_themes:
        mine = by_theme.get(t["key"], [])
        if not mine:
            continue
        yes = [r for r in mine if r["fit"] == "yes"]
        new = [r for r in yes if r["status"] == "new"]
        covered = sum(r["avg"] or 0 for r in yes if r["status"] != "new")
        total = sum(r["avg"] or 0 for r in yes)
        months = _sum_months(yes)
        pat = pattern(months)
        out.append(dict(t, campaign=campaign_for(t), keywords=len(mine), yes=len(yes), maybe=sum(1 for r in mine if r["fit"] == "maybe"), no=sum(1 for r in mine if r["fit"] == "no"),
                        new=len(new), searches=total, newSearches=sum(r["avg"] or 0 for r in new), coveredSearches=covered,
                        coverage=round(covered / total * 100) if total else None, avgLow=_wavg(yes, "lo"), avgHigh=_wavg(yes, "hi"),
                        months=months, peak=pat["peak"], swing=pat["swing"], trend=pat["trend"], pattern=pat["label"],
                        top=[{"kw": r["kw"], "avg": r["avg"]} for r in sorted(new, key=lambda r: -(r["avg"] or 0))[:6]]))
    out.sort(key=lambda t: (-t["newSearches"], t["name"]))
    yes_all = [r for r in rows if r["fit"] == "yes"]
    total = sum(r["avg"] or 0 for r in yes_all)
    covered = sum(r["avg"] or 0 for r in yes_all if r["status"] != "new")
    coverage = {"judged": len(rows), "yes": len(yes_all), "maybe": sum(1 for r in rows if r["fit"] == "maybe"), "no": sum(1 for r in rows if r["fit"] == "no"),
                "unjudged": sum(1 for r in rows if r["fit"] == "unjudged"), "searches": total, "coveredSearches": covered, "pct": round(covered / total * 100) if total else None,
                "new": sum(1 for r in yes_all if r["status"] == "new"), "newSearches": total - covered, "bidding": sum(1 for r in yes_all if r["status"] == "bidding"),
                "matched": sum(1 for r in yes_all if r["status"] == "covered"), "include": sum(1 for r in rows if r["include"]), "blockedInTarget": sum(1 for r in rows if r.get("blockedInTarget"))}
    return {"keywords": rows, "themes": out, "coverage": coverage}


def editor_rows(keywords, themes):
    """The rows for a Google Ads Editor import: [Campaign, Ad group, Keyword, Criterion type, Status]. Only keywords marked `include`: judged a fit, new to the account,
    not a competitor's name. Status is Paused, so nothing serves until a person has looked at it."""
    by_key = {t["key"]: t for t in themes}
    out = []
    for r in sorted((r for r in keywords if r.get("include")), key=lambda r: (by_key.get(r["theme"], {}).get("name", ""), r["intent"], -(r.get("avg") or 0), r["kw"])):
        t = by_key.get(r["theme"]) or {"name": "Other", "campaign": ""}
        out.append([campaign_for(t), ad_group_for(t, r["intent"]), r["kw"], r["match"].capitalize(), "Paused"])
    return out


def seasonal_table(themes):
    """[theme name, searches in each of the twelve months, in the order Planner gave them] for the themes with a year to show: the month names and one row of figures each."""
    months = sorted({ym for t in themes for ym in (t.get("months") or {})})
    head = [_ym_name(ym)[:3] + " " + ym[:4] for ym in months]
    rows = [[t["name"]] + [t["months"].get(ym, 0) for ym in months] + [t.get("pattern") or ""] for t in themes if len(t.get("months") or {}) >= 6 and t.get("searches")]
    return head, rows
