"""Paid Marketing Agents: Strategy & Campaign Book, what the writing is held to.

The model writes the strategy and the campaigns; this file decides what of it is kept. Nothing the model says is trusted for shape or for
limits, because the places it ends up (Google Ads, a client's inbox) are strict:

  * every field has the type the page expects, text is cleaned (no em dashes, no markup, no runaway length);
  * an ad headline over 30 characters, a description over 90, a sitelink over 25 never reaches the book (it is dropped and counted);
  * a fact the writing relies on must be quoted from a page the research read, or it is marked "needs the client to confirm";
  * a claim in an ad (a price, a percentage, "free", a guarantee, a superlative) that is not on the client's pages is listed for the
    client to confirm before launch;
  * a negative keyword that would block one of the campaign's own keywords is removed;
  * budgets add up to the monthly budget, locations and final URLs are the client's own.

Everything here is a plain function of its arguments; the model is not called and nothing is read from the network.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

from . import paid_strategy as ps

# ── Google Ads limits ───────────────────────────────────────────────────────

LIMITS = {"headline": 30, "description": 90, "path": 15, "long_headline": 90, "sitelink": 25, "sitelink_desc": 35, "callout": 25, "snippet_value": 25,
          "business_name": 25, "dg_headline": 40, "pmax_short_desc": 60}
RSA = {"headlines": (3, 15), "descriptions": (2, 4)}
PMAX = {"headlines": (3, 15), "long_headlines": (1, 5), "descriptions": (2, 5)}
MATCH = {"exact": "Exact", "phrase": "Phrase", "broad": "Broad"}
SNIPPET_HEADERS = ["Amenities", "Brands", "Courses", "Degree programs", "Destinations", "Featured hotels", "Insurance coverage", "Models", "Neighborhoods",
                   "Service catalog", "Services", "Shows", "Styles", "Types"]
SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "nor", "of", "on", "or", "the", "to", "vs", "via", "with"}
_EM = re.compile(r"\s*[\u2013\u2014]\s*")
_TAGS = re.compile(r"</?[A-Za-z][^<>]*>")


def text(v, n=400):
    """Plain one-line text: no markup, no em or en dashes (a comma instead), whitespace collapsed, cut to `n` at a word."""
    s = _TAGS.sub(" ", ps.scrub(str(v if v is not None else "")))
    s = _EM.sub(", ", s)
    s = " ".join(s.split())
    if len(s) > n:
        s = s[:n].rsplit(" ", 1)[0].rstrip(",;:") if " " in s[:n] else s[:n]
    return s


def para(v, n=1500):
    """Free text that may keep its line breaks."""
    out = [_EM.sub(", ", _TAGS.sub(" ", line)) for line in ps.scrub(str(v if v is not None else "")).replace("\r", "").split("\n")]
    s = "\n".join(" ".join(line.split()) for line in out)
    return re.sub(r"\n{3,}", "\n\n", s).strip()[:n]


def lines(v, n=8, each=240):
    """A list of short strings: anything else is dropped, duplicates (ignoring case) are dropped, at most `n`."""
    out, seen = [], set()
    for x in v if isinstance(v, list) else ([v] if isinstance(v, str) else []):
        t = text(x, each) if isinstance(x, (str, int, float)) else ""
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
        if len(out) >= n:
            break
    return out


def rows(v, keys, n=8, each=300):
    """A list of objects with exactly `keys` as text; a row with none of its first key is dropped."""
    out = []
    for r in v if isinstance(v, list) else []:
        if not isinstance(r, dict):
            continue
        row = {k: text(r.get(k), each) for k in keys}
        if row[keys[0]]:
            out.append(row)
        if len(out) >= n:
            break
    return out


def number(v, lo=None, hi=None, default=None):
    try:
        x = float(str(v).replace(",", "").replace("%", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return default
    if lo is not None and x < lo:
        return default
    if hi is not None and x > hi:
        return default
    return x


def norm(s):
    """For comparing a quote with a page: lower case, letters and digits only."""
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def in_corpus(quote, corpus_norm):
    """True when the quote is on the pages (whole words, in order). A quote shorter than 12 characters proves nothing and is refused."""
    q = norm(quote)
    return len(q) >= 12 and (" " + q + " ") in (" " + corpus_norm + " ")


def start_case(s):
    """Start Case for a headline, the way the team writes ads: lower-case words are capitalised (small joining words are not, unless first);
    anything already carrying a capital (a brand like iLet, an acronym like T1D) is left exactly as written."""
    words = str(s or "").split(" ")
    out = []
    for i, w in enumerate(words):
        if not w or any(ch.isupper() for ch in w) or not w[0].isalpha():
            out.append(w)
        elif w.lower() in SMALL_WORDS and i > 0:
            out.append(w.lower())
        else:
            out.append(w[0].upper() + w[1:])
    return " ".join(out)


def dedupe_ci(items):
    seen, out = set(), []
    for x in items:
        if x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out


def fit(items, limit, upper, *, cased=False, drops=None, what="line"):
    """Ad text that fits `limit` characters, deduplicated, at most `upper`. What does not fit is counted in `drops` (a dict), never cut short."""
    out = []
    for raw in items if isinstance(items, list) else []:
        t = text(raw, 400)
        if cased:
            t = start_case(t)
        if not t:
            continue
        if len(t) > limit:
            if drops is not None:
                drops[what] = drops.get(what, 0) + 1
            continue
        out.append(t)
    return dedupe_ci(out)[:upper]


# ── Claims ──────────────────────────────────────────────────────────────────

CLAIM = re.compile(r"(\$\s?\d[\d,.]*|(?<![\d.,])\d[\d,]*(?:\.\d+)?\s?%|(?<![\d.])\d[\d,]*(?:\.\d+)?\+?\s?(?:years?|yrs?|patients?|customers?|clients?|reviews?|locations?|offices?|stars?)\b|"
                   r"\brated\s+\d+(?:\.\d+)?|(?<![\d.])\d(?:\.\d)?[- ]?(?:star|stars|star rating)\b|\bfree(?:\s+[a-z]+)?|\bno cost\b|\bcomplimentary\b|\bguarantee[ds]?\b|\bbest\b|\b#\s?1\b|\bnumber one\b|\btop[- ]rated\b|\bleading\b|\bfastest\b|"
                   r"\bcheapest\b|\blowest\b|\bsame[- ]day\b|\b24/7\b|\bcure[sd]?\b|\bpainless\b|\bfda[- ]?(?:approved|cleared)\b|\baward[- ]winning\b|\bcertified\b|"
                   r"\bnearest\b|\bopen now\b)", re.I)
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def claim_tokens(s):
    return [m.group(0).strip() for m in CLAIM.finditer(str(s or ""))]


def claim_found(token, have, raw=""):
    """Is this claim on the client's pages? A number must be on a page as the same number (4.9, 312), whatever noun follows it; a price or a percentage
    must be there with its own sign or word ($1,900, 50% or 50 percent), so "save 50%" is not proved by "50 implants"; "free" needs the word free and
    the thing offered; any other claim needs its own words. `raw` is the pages' text as written (lower case, one line), for the signs."""
    padded = " " + have + " "
    nums = NUMBER.findall(token)
    if nums and raw and "$" in token:
        return all(re.search(r"\$\s?%s(?!\d)" % re.escape(n), raw) for n in nums)
    if nums and raw and "%" in token:
        return all(re.search(r"(?<![\d.,])%s\s?(?:%%|percent\b|per cent\b)" % re.escape(n), raw) for n in nums)
    if nums:
        return all((" " + norm(n) + " ") in padded for n in nums)
    key = norm(token)
    if key.startswith("free "):
        thing = key.split(" ", 1)[1][:5]
        return " free " in padded and bool(thing) and (" " + thing) in padded
    return (" " + key + " ") in padded


def claims_to_confirm(items, corpus_norm, brief_norm="", raw=""):
    """Claims in the ad text that are not on the client's pages or in the brief. `items` are (where, text). Returns
    [{'where', 'text', 'claim'}], one per claim per place, for the client to confirm before launch. `raw` is the pages and the brief as written."""
    have = corpus_norm + " " + brief_norm
    out, seen = [], set()
    for where, t in items:
        for tok in claim_tokens(t):
            if not norm(tok) or claim_found(tok, have, raw):
                continue
            ident = (where, norm(tok), norm(t))
            if ident in seen:
                continue
            seen.add(ident)
            out.append({"where": where, "text": t, "claim": tok})
    return out


# ── Keywords and negatives ──────────────────────────────────────────────────

def kw_text(s):
    """A keyword as Google Ads takes it: lower case, letters, digits, spaces and a few joiners, at most 10 words and 80 characters."""
    t = re.sub(r"[^\w&'+ ]|_", " ", text(s, 200).lower())
    t = " ".join(t.split())
    return t if 1 <= len(t) <= 80 and len(t.split()) <= 10 and re.search(r"\w", t) else ""


def match_of(v, default="phrase"):
    v = str(v or "").strip().lower().strip("[]\"")
    return v if v in MATCH else default


def blocks(neg, kw):
    """Would the negative keyword block the keyword? `neg` and `kw` are {'text', 'match'}."""
    n, k = neg["text"].split(), kw["text"].split()
    if not n or not k:
        return False
    m = neg["match"]
    if m == "exact":
        return n == k
    if m == "phrase":
        return any(k[i:i + len(n)] == n for i in range(len(k) - len(n) + 1))
    return all(w in k for w in n)


def negative_conflicts(keywords, negatives):
    """(negatives that block none of the keywords, [(negative, keyword blocked)]). A broad negative blocks any keyword holding all its words."""
    kept, removed = [], []
    for n in negatives:
        hit = next((k for k in keywords if blocks(n, k)), None)
        if hit:
            removed.append((n, hit))
        else:
            kept.append(n)
    return kept, removed


def keyword_rows(raw, *, limit=40, drops=None):
    """Keywords the model proposed, cleaned: match type known, no duplicates of the same text and match, at most `limit`."""
    out, seen = [], set()
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        t, m = kw_text(r.get("text")), match_of(r.get("match"))
        if not t or (t, m) in seen:
            continue
        seen.add((t, m))
        out.append({"text": t, "match": m, "intent": text(r.get("intent"), 40).lower(), "note": text(r.get("note"), 160)})
        if len(out) >= limit:
            if drops is not None:
                drops["keywords"] = drops.get("keywords", 0) + 1
            break
    return out


def negative_rows(raw, *, limit=60):
    out, seen = [], set()
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        t = kw_text(r.get("text"))
        m = match_of(r.get("match"))
        if not t or (t, m) in seen:
            continue
        seen.add((t, m))
        out.append({"text": t, "match": m, "reason": text(r.get("reason"), 200)})
        if len(out) >= limit:
            break
    return out


# ── Addresses ───────────────────────────────────────────────────────────────

def own_url(raw, site_url, known):
    """(url, verified). An address on the client's own site, made absolute against its home page; verified when the research read that page.
    An address on another site is refused (''), because an ad sends people to the client."""
    s = str(raw or "").strip()
    if not s or not site_url:
        return "", False
    try:
        absolute = urljoin(site_url, s)
        parts = urlsplit(absolute)
        port = parts.port
    except ValueError:
        return "", False
    if parts.username or parts.password or port not in (None, 80, 443):
        return "", False
    host = (parts.hostname or "").lower().removeprefix("www.")
    site_host = ps.host_of(site_url)
    if parts.scheme not in ("http", "https") or not (host == site_host or host.endswith("." + site_host)):
        return "", False
    clean = "https://%s%s" % ((parts.hostname or "").lower(), re.sub(r"/{2,}", "/", parts.path or "/"))
    return clean, _page_key(clean) in known


def _page_key(url):
    """An address as it is compared: the host without www, the path without a trailing slash, no query or fragment, lower case."""
    parts = urlsplit(str(url or ""))
    return ("%s%s" % ((parts.hostname or "").removeprefix("www."), re.sub(r"/{2,}", "/", parts.path or "/"))).rstrip("/").lower()


def known_urls(research):
    """The addresses of the pages the research read on the client's own site, normalised for comparing (www or not, slash or not)."""
    out = set()
    for pg in (((research or {}).get("website") or {}).get("data") or {}).get("pages") or []:
        if pg.get("url"):
            out.add(_page_key(pg["url"]))
    return out


# ── Stage 1: the business ───────────────────────────────────────────────────

def profile(raw, corpus_norm):
    raw = raw if isinstance(raw, dict) else {}
    pos = raw.get("positioning") if isinstance(raw.get("positioning"), dict) else {}
    facts = []
    for r in rows(raw.get("facts"), ["fact", "quote", "source"], n=30, each=400):
        r["verified"] = in_corpus(r["quote"], corpus_norm)
        facts.append(r)
    offerings = rows(raw.get("offerings"), ["name", "description", "priority"], n=14)
    for o in offerings:
        o["priority"] = o["priority"].lower() if o["priority"].lower() in ("high", "medium", "low") else "medium"
    return {"summary": para(raw.get("summary"), 1600), "offerings": offerings,
            "audiences": rows(raw.get("audiences"), ["name", "who", "need", "intent"], n=8),
            "positioning": {"promise": text(pos.get("promise"), 300), "differentiators": lines(pos.get("differentiators"), 8), "tone": text(pos.get("tone"), 240)},
            "proof": rows(raw.get("proof"), ["point", "source"], n=10), "conversionPaths": rows(raw.get("conversionPaths"), ["action", "where", "note"], n=8),
            "compliance": rows(raw.get("compliance"), ["topic", "rule", "impact"], n=8), "siteGaps": rows(raw.get("siteGaps"), ["issue", "why", "fix"], n=10),
            "facts": facts, "assumptions": lines(raw.get("assumptions"), 10), "questions": lines(raw.get("questions"), 10)}


# ── Stage 2: the competitors ────────────────────────────────────────────────

def competitive(raw, research):
    """The competitor read, kept to competitors the research actually read (matched on web address or name)."""
    raw = raw if isinstance(raw, dict) else {}
    read = ((research or {}).get("competitors") or {}).get("data", {}).get("competitors") or []
    ok = [r for r in read if r.get("ok")]
    by_host = {ps.host_of(r["url"]): r for r in ok}
    by_name = {norm(r["name"]): r for r in ok}
    out = []
    for r in raw.get("competitors") if isinstance(raw.get("competitors"), list) else []:
        if not isinstance(r, dict):
            continue
        src = by_host.get(ps.host_of(r.get("url"))) or by_name.get(norm(r.get("name")))
        if not src:
            continue
        out.append({"name": text(r.get("name"), 80) or src["name"], "url": src["url"], "positioning": text(r.get("positioning"), 400),
                    "offers": lines(r.get("offers"), 6), "messages": lines(r.get("messages"), 6), "ctas": lines(r.get("ctas"), 6),
                    "strengths": lines(r.get("strengths"), 5), "weaknesses": lines(r.get("weaknesses"), 5), "themes": lines(r.get("themes"), 8, 80)})
    return {"landscape": para(raw.get("landscape"), 1400), "competitors": out, "whiteSpace": rows(raw.get("whiteSpace"), ["opportunity", "why"], n=8),
            "differentiation": lines(raw.get("differentiation"), 8), "watchOuts": lines(raw.get("watchOuts"), 8)}


# ── Stage 3: the strategy ───────────────────────────────────────────────────

def strategy(raw, inputs):
    raw = raw if isinstance(raw, dict) else {}
    tgt = raw.get("targeting") if isinstance(raw.get("targeting"), dict) else {}
    msg = raw.get("messaging") if isinstance(raw.get("messaging"), dict) else {}
    meas = raw.get("measurement") if isinstance(raw.get("measurement"), dict) else {}
    chans = rows(raw.get("channels"), ["channel", "why", "share", "priority", "phase"], n=8)
    for c in chans:
        c["share"] = number(c["share"], 0, 100, None)
    over = sum(c["share"] or 0 for c in chans)
    if over > 100.5:                                    # shares of 60, 50 and 30 are not a split of one budget: scale them to 100 (the prompt says they add to 100)
        for c in chans:
            if c["share"] is not None:
                c["share"] = round(c["share"] * 100.0 / over, 1)
    conv = []
    for r in rows(meas.get("conversions"), ["name", "type", "primary", "how"], n=10):
        r["primary"] = r["primary"].lower() in ("yes", "true", "primary", "1")
        conv.append(r)
    road = []
    for r in raw.get("roadmap") if isinstance(raw.get("roadmap"), list) else []:
        if isinstance(r, dict) and text(r.get("phase"), 60):
            road.append({"phase": text(r["phase"], 60), "actions": lines(r.get("actions"), 8)})
    return {"summary": lines(raw.get("summary"), 8, 420), "objectives": rows(raw.get("objectives"), ["objective", "kpi", "target", "basis"], n=8),
            "funnel": rows(raw.get("funnel"), ["stage", "role", "message", "channels"], n=6), "channels": chans,
            "budget": {"monthly": inputs.get("budget"), "currency": inputs.get("currency") or "USD", "daily": ps.daily_from_monthly(inputs.get("budget")),
                       "allocation": [], "pacing": text((raw.get("budget") or {}).get("pacing") if isinstance(raw.get("budget"), dict) else "", 500),
                       "scaleRule": text((raw.get("budget") or {}).get("scaleRule") if isinstance(raw.get("budget"), dict) else "", 500)},
            "bidding": rows(raw.get("bidding"), ["phase", "strategy", "trigger", "note"], n=6),
            "targeting": {"locations": rows(tgt.get("locations"), ["name", "why"], n=12), "devices": text(tgt.get("devices"), 300), "schedule": text(tgt.get("schedule"), 300),
                          "demographics": text(tgt.get("demographics"), 300), "audiences": lines(tgt.get("audiences"), 10)},
            "messaging": {"pillars": rows(msg.get("pillars"), ["pillar", "proof", "example"], n=6), "tone": text(msg.get("tone"), 300),
                          "offers": lines(msg.get("offers"), 8), "avoid": lines(msg.get("avoid"), 8)},
            "social": rows(raw.get("social"), ["insight", "use"], n=8), "seasonality": rows(raw.get("seasonality"), ["window", "action"], n=8),
            "measurement": {"conversions": conv, "tracking": rows(meas.get("tracking"), ["item", "detail"], n=10), "reporting": text(meas.get("reporting"), 500)},
            "tests": rows(raw.get("tests"), ["test", "hypothesis", "metric", "when"], n=8), "roadmap": road[:6],
            "risks": rows(raw.get("risks"), ["risk", "mitigation"], n=8), "assumptions": lines(raw.get("assumptions"), 10), "clientNeeds": lines(raw.get("clientNeeds"), 12)}


# ── Stage 4: the campaigns ──────────────────────────────────────────────────

SETTING_KEYS = ["bidStrategy", "devices", "genders", "income", "ages", "schedule", "locations", "language"]
SETTING_DEFAULTS = {
    "devices": ("All devices, no bid adjustments", "Nothing in the plan says to favour or switch off a device."),
    "genders": ("All genders (none excluded)", "The offer is not gender specific."),
    "income": ("All household incomes (none excluded)", "The offer is not income specific."),
    "ages": ("All ages (none excluded)", "The offer is not age specific."),
    "schedule": ("All day, every day", "No opening hours or lead handling limit the plan."),
    "language": ("English", "The site and the ads are in English."),
}


def _setting(raw, key, fallback_value="", fallback_why=""):
    r = raw.get(key) if isinstance(raw.get(key), dict) else {}
    value, why = text(r.get("value"), 400), text(r.get("why"), 400)
    if not value and key in SETTING_DEFAULTS:
        value, why = SETTING_DEFAULTS[key]
    return {"value": value or fallback_value, "why": why or fallback_why}


def architecture(raw, inputs):
    """The campaign skeletons. At most 8, only the kinds asked for (any kind when none were), each named the team's way, budgets split so
    they add up to the monthly budget."""
    raw = raw if isinstance(raw, dict) else {}
    allowed = inputs.get("campaignTypes") or ps.KIND_KEYS
    campaigns, keys, names = [], set(), set()
    for r in raw.get("campaigns") if isinstance(raw.get("campaigns"), list) else []:
        if not isinstance(r, dict):
            continue
        kind = str(r.get("kind") or "").strip().lower().replace(" ", "").replace("_", "").replace("-", "")
        kind = {"performancemax": "pmax", "demandgen": "demandgen", "youtube": "video"}.get(kind, kind)
        if kind not in ps.KINDS or kind not in allowed:
            continue
        theme = text(r.get("theme"), 60)
        key = ps.slug("%s %s" % (kind, theme), 40).lower() or kind
        base, n = key, 2
        while key in keys:
            key, n = "%s%d" % (base, n), n + 1
        keys.add(key)
        name = base_name = ps.campaign_name(kind, theme, inputs.get("launch"))
        while name in names:                            # two campaigns on the same theme get the same name, and Google Ads refuses a repeated campaign name
            name = "%s_%d" % (base_name, len(names) + 1)
        names.add(name)
        sets = r.get("settings") if isinstance(r.get("settings"), dict) else {}
        settings = {k: _setting(sets, k) for k in SETTING_KEYS}
        if not settings["bidStrategy"]["value"]:
            settings["bidStrategy"] = {"value": "Maximize conversions", "why": "The standard start while a campaign has no conversion history."}
        if not settings["locations"]["value"]:
            settings["locations"] = {"value": inputs.get("locations") or "To be confirmed with the client", "why": "Taken from the client's service area."}
        campaigns.append({"key": key, "kind": kind, "kindLabel": ps.KINDS[kind]["label"], "theme": theme or ps.KINDS[kind]["label"],
                          "name": name, "purpose": text(r.get("purpose"), 500),
                          "share": number(r.get("share"), 0, 1000, 0) or 0, "landingPage": text(r.get("landingPage"), 300),
                          "keywordFocus": lines(r.get("keywordFocus"), 10, 80), "audience": text(r.get("audience"), 400), "notes": text(r.get("notes"), 500),
                          "settings": settings})
        if len(campaigns) >= 8:
            break
    if not campaigns:
        return {"campaigns": []}
    shares = [c["share"] or 1 for c in campaigns]
    days = ps.split_budget(inputs.get("budget"), shares, floor=1)
    total = sum(shares)
    for c, d, s in zip(campaigns, days, shares):
        c["share"] = round(100.0 * s / total, 1)
        c["daily"] = d
        c["monthly"] = round(d * 30.4, 0) if d else None
    return {"campaigns": campaigns}


# ── Stage 5: one campaign ───────────────────────────────────────────────────

def sitelinks(raw, site_url, known, drops=None, limit=8):
    out = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        url, verified = own_url(r.get("url"), site_url, known)
        t = text(r.get("text"), 80)
        d1, d2 = text(r.get("desc1"), 80), text(r.get("desc2"), 80)
        if not t or not url:
            if drops is not None:
                drops["sitelinks"] = drops.get("sitelinks", 0) + 1
            continue
        if len(t) > LIMITS["sitelink"] or len(d1) > LIMITS["sitelink_desc"] or len(d2) > LIMITS["sitelink_desc"]:
            if drops is not None:
                drops["sitelinks"] = drops.get("sitelinks", 0) + 1
            continue
        if not (d1 and d2):
            d1 = d2 = ""                                                # Google shows description lines in pairs: one alone is not used
        out.append({"text": start_case(t), "desc1": d1, "desc2": d2, "url": url, "verified": verified})
        if len(out) >= limit:
            break
    return out


def snippets(raw, drops=None):
    out = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        header = text(r.get("header"), 40)
        header = next((h for h in SNIPPET_HEADERS if h.lower() == header.lower()), "")
        values = fit(r.get("values"), LIMITS["snippet_value"], 10, drops=drops, what="snippet values")
        if header and len(values) >= 3:
            out.append({"header": header, "values": values})
        elif drops is not None:
            drops["snippets"] = drops.get("snippets", 0) + 1
    return out[:3]


def assets(raw, site_url, known, drops=None):
    raw = raw if isinstance(raw, dict) else {}
    call = raw.get("call") if isinstance(raw.get("call"), dict) else {}
    return {"sitelinks": sitelinks(raw.get("sitelinks"), site_url, known, drops), "callouts": fit(raw.get("callouts"), LIMITS["callout"], 10, cased=True, drops=drops, what="callouts"),
            "snippets": snippets(raw.get("snippets"), drops), "call": {"use": bool(call.get("use")), "note": text(call.get("note"), 200)},
            "promotions": rows(raw.get("promotions"), ["text", "detail"], n=3, each=160)}


def ads_search(raw, drops=None, limit=2):
    out = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        heads = fit(r.get("headlines"), LIMITS["headline"], RSA["headlines"][1], cased=True, drops=drops, what="headlines")
        descs = fit(r.get("descriptions"), LIMITS["description"], RSA["descriptions"][1], drops=drops, what="descriptions")
        p1, p2 = text(r.get("path1"), 30), text(r.get("path2"), 30)
        p1 = p1 if len(p1) <= LIMITS["path"] else ""
        p2 = p2 if p1 and len(p2) <= LIMITS["path"] else ""
        pins = []
        for pn in r.get("pins") if isinstance(r.get("pins"), list) else []:
            cased = start_case(text(pn.get("text"), 40)) if isinstance(pn, dict) else ""
            if cased in heads and str(pn.get("position")) in ("1", "2", "3"):
                pins.append({"text": cased, "position": int(pn["position"])})
        out.append({"headlines": heads, "descriptions": descs, "path1": p1, "path2": p2, "pins": pins[:3],
                    "short": len(heads) < RSA["headlines"][0] or len(descs) < RSA["descriptions"][0]})
        if len(out) >= limit:
            break
    return out


def build_search(raw, ctx, drops):
    """One Search campaign: ad groups with keywords and ads, negatives, assets. `ctx` has site, known."""
    raw = raw if isinstance(raw, dict) else {}
    groups, all_kw = [], []
    for g in raw.get("adGroups") if isinstance(raw.get("adGroups"), list) else []:
        if not isinstance(g, dict) or not text(g.get("name"), 80):
            continue
        kws = keyword_rows(g.get("keywords"), limit=30, drops=drops)
        ads = ads_search(g.get("ads"), drops)
        url, verified = own_url(g.get("landingPage"), ctx["site"], ctx["known"])
        if g.get("landingPage") and not url:
            drops["landing pages"] = drops.get("landing pages", 0) + 1
        groups.append({"name": text(g["name"], 80), "theme": text(g.get("theme"), 200), "intent": text(g.get("intent"), 60).lower(), "landingPage": url,
                       "landingVerified": verified, "keywords": kws, "ads": ads})
        all_kw.extend(kws)
        if len(groups) >= 12:
            break
    neg = raw.get("negatives") if isinstance(raw.get("negatives"), dict) else {}
    negatives, removed = negative_conflicts(all_kw, negative_rows(neg.get("campaign")))
    lists, removed_from_lists = [], []
    for r in neg.get("lists") if isinstance(neg.get("lists"), list) else []:
        if isinstance(r, dict) and text(r.get("name"), 80):
            wanted = [k for k in (kw_text(x) for x in (r.get("keywords") if isinstance(r.get("keywords"), list) else [])) if k][:60]
            kept, blocked = negative_conflicts(all_kw, [{"text": k, "match": "phrase"} for k in wanted])
            removed_from_lists += [{"negative": n["text"], "match": "phrase (list %s)" % text(r["name"], 40), "blocked": k["text"]} for n, k in blocked]
            lists.append({"name": text(r["name"], 80), "keywords": [n["text"] for n in kept], "reason": text(r.get("reason"), 200)})
    return {"adGroups": groups, "negatives": {"campaign": negatives, "lists": lists[:6], "removed": [{"negative": n["text"], "match": n["match"], "blocked": k["text"]} for n, k in removed] + removed_from_lists},
            "assets": assets(raw.get("assets"), ctx["site"], ctx["known"], drops)}


def build_pmax(raw, ctx, drops):
    raw = raw if isinstance(raw, dict) else {}
    groups = []
    for g in raw.get("assetGroups") if isinstance(raw.get("assetGroups"), list) else []:
        if not isinstance(g, dict) or not text(g.get("name"), 80):
            continue
        url, verified = own_url(g.get("finalUrl"), ctx["site"], ctx["known"])
        sig = g.get("signals") if isinstance(g.get("signals"), dict) else {}
        longs = fit(g.get("longHeadlines"), LIMITS["long_headline"], PMAX["long_headlines"][1], cased=True, drops=drops, what="long headlines")
        descs = fit(g.get("descriptions"), LIMITS["description"], PMAX["descriptions"][1], drops=drops, what="descriptions")
        if descs and not any(len(d) <= LIMITS["pmax_short_desc"] for d in descs):
            drops["descriptions without a short one"] = drops.get("descriptions without a short one", 0) + 1
        groups.append({"name": text(g["name"], 80), "theme": text(g.get("theme"), 200), "finalUrl": url, "landingVerified": verified,
                       "headlines": fit(g.get("headlines"), LIMITS["headline"], PMAX["headlines"][1], cased=True, drops=drops, what="headlines"),
                       "longHeadlines": longs, "descriptions": descs,
                       "businessName": text(g.get("businessName"), 40) if len(text(g.get("businessName"), 40)) <= LIMITS["business_name"] else "",
                       "cta": text(g.get("cta"), 40), "images": rows(g.get("images"), ["ratio", "concept"], n=8, each=240),
                       "videos": rows(g.get("videos"), ["concept"], n=3, each=300),
                       "signals": {"customSegments": lines(sig.get("customSegments"), 8, 100), "interests": lines(sig.get("interests"), 8, 100),
                                   "yourData": lines(sig.get("yourData"), 6, 100), "demographics": text(sig.get("demographics"), 200)},
                       "searchThemes": lines(g.get("searchThemes"), 25, 80)})
        if len(groups) >= 6:
            break
    return {"assetGroups": groups, "assets": assets(raw.get("assets"), ctx["site"], ctx["known"], drops)}


def build_creative(raw, ctx, drops):
    """Demand Gen, Display and Video: ad groups with audiences and ads whose limits differ by format."""
    raw = raw if isinstance(raw, dict) else {}
    kind = ctx.get("kind")
    head_limit = LIMITS["dg_headline"] if kind == "demandgen" else LIMITS["headline"]
    groups = []
    for g in raw.get("adGroups") if isinstance(raw.get("adGroups"), list) else []:
        if not isinstance(g, dict) or not text(g.get("name"), 80):
            continue
        ads = []
        for a in g.get("ads") if isinstance(g.get("ads"), list) else []:
            if not isinstance(a, dict):
                continue
            url, verified = own_url(a.get("finalUrl"), ctx["site"], ctx["known"])
            long_h = fit([a.get("longHeadline")], LIMITS["long_headline"], 1, cased=True, drops=drops, what="long headlines")
            ads.append({"format": text(a.get("format"), 60), "headlines": fit(a.get("headlines"), head_limit, 5, cased=True, drops=drops, what="headlines"),
                        "longHeadline": long_h[0] if long_h else "", "descriptions": fit(a.get("descriptions"), LIMITS["description"], 5, drops=drops, what="descriptions"),
                        "cta": text(a.get("cta"), 40), "concept": text(a.get("concept"), 500), "script": para(a.get("script"), 900), "finalUrl": url,
                        "landingVerified": verified})
            if len(ads) >= 3:
                break
        groups.append({"name": text(g["name"], 80), "audiences": lines(g.get("audiences"), 10, 120), "placements": lines(g.get("placements"), 8, 120), "ads": ads})
        if len(groups) >= 6:
            break
    return {"adGroups": groups, "assets": assets(raw.get("assets"), ctx["site"], ctx["known"], drops)}


def build(kind, raw, ctx):
    """(build, drops): the cleaned build for one campaign and what was dropped (limits, other sites) so the page can say so."""
    drops = {}
    fn = {"search": build_search, "pmax": build_pmax}.get(kind, build_creative)
    out = fn(raw, dict(ctx, kind=kind), drops)
    out["drops"] = drops
    return out, drops


def ad_texts(kind, built):
    """Every piece of ad text in a build as (where, text), for the claim scan."""
    out = []
    if kind == "search":
        for g in built.get("adGroups") or []:
            for i, ad in enumerate(g.get("ads") or [], 1):
                out += [("%s, ad %d headline" % (g["name"], i), h) for h in ad["headlines"]] + [("%s, ad %d description" % (g["name"], i), d) for d in ad["descriptions"]]
    elif kind == "pmax":
        for g in built.get("assetGroups") or []:
            out += [("%s headline" % g["name"], h) for h in g["headlines"]] + [("%s long headline" % g["name"], h) for h in g["longHeadlines"]]
            out += [("%s description" % g["name"], d) for d in g["descriptions"]]
    else:
        for g in built.get("adGroups") or []:
            for ad in g.get("ads") or []:
                out += [("%s headline" % g["name"], h) for h in ad["headlines"]] + [("%s description" % g["name"], d) for d in ad["descriptions"]]
                if ad.get("longHeadline"):
                    out.append(("%s long headline" % g["name"], ad["longHeadline"]))
    a = built.get("assets") or {}
    out += [("sitelink", s["text"]) for s in a.get("sitelinks") or []] + [("sitelink description", d) for s in a.get("sitelinks") or [] for d in (s["desc1"], s["desc2"]) if d]
    out += [("callout", c) for c in a.get("callouts") or []]
    out += [("promotion", "%s %s" % (p["text"], p["detail"])) for p in a.get("promotions") or []]
    return out


def shortfalls(kind, built):
    """What a build is short of against Google's minimums, in words (an empty list when it has what it needs)."""
    out = []
    if kind == "search":
        if not built.get("adGroups"):
            out.append("No ad groups were written.")
        for g in built.get("adGroups") or []:
            if not g["keywords"]:
                out.append("Ad group %s has no keywords." % g["name"])
            if not g["ads"]:
                out.append("Ad group %s has no ad." % g["name"])
            for i, ad in enumerate(g["ads"], 1):
                if ad["short"]:
                    out.append("Ad %d of %s has %d headlines and %d descriptions after the limits were applied (needs at least 3 and 2)." % (
                        i, g["name"], len(ad["headlines"]), len(ad["descriptions"])))
    elif kind == "pmax":
        if not built.get("assetGroups"):
            out.append("No asset groups were written.")
        for g in built.get("assetGroups") or []:
            if len(g["headlines"]) < PMAX["headlines"][0] or not g["longHeadlines"] or len(g["descriptions"]) < PMAX["descriptions"][0]:
                out.append("Asset group %s is short of text after the limits were applied (needs 3 headlines, 1 long headline, 2 descriptions)." % g["name"])
            elif not any(len(d) <= LIMITS["pmax_short_desc"] for d in g["descriptions"]):
                out.append("Asset group %s has no description of %d characters or fewer, which Performance Max needs." % (g["name"], LIMITS["pmax_short_desc"]))
    else:
        if not built.get("adGroups"):
            out.append("No ad groups were written.")
        for g in built.get("adGroups") or []:
            if not [a for a in g.get("ads") or [] if a["headlines"] and a["descriptions"]]:
                out.append("Ad group %s has no ad with a headline and a description." % g["name"])
    return out
