"""Paid Marketing Agents: Ad Copy & Creative Generator, what the writing is held to.

The model writes the copy; this file decides what of it is kept. Nothing the model says is trusted for length or for rules, because the places the copy ends up (Google Ads,
Meta Ads Manager) refuse what does not fit:

  * a line over the format's limit is dropped, never cut short, and counted by reason;
  * a line a platform would refuse is dropped: Google Ads refuses emoji, an exclamation mark in a headline, a word in capitals, doubled punctuation, a phone number or a web
    address in the text; Meta refuses copy that tells the viewer something about their personal attributes ("Are you diabetic?");
  * a line that names a competitor or holds a word on the avoid list is dropped;
  * a line that repeats another (the same words, or the same words in another order) is dropped;
  * a claim in a line (a price, a percentage, a number of years or reviews, "free", a guarantee, a superlative, "same day", a health claim) that is not on the client's pages or in
    the facts the person gave is NOT dropped but marked "confirm": the exact claim is listed, and the file for Google Ads Editor leaves the line out until it is confirmed;
  * a format that ends with fewer lines than it needs says so (`shortfalls`), and the run asks the model once more for exactly what is missing.

Every function here is a plain function of its arguments. The model is not called and nothing is read from the network.
"""
from __future__ import annotations

import re

from . import paid_adcopy as pa
from . import paid_strategy_check as ck

READY, CONFIRM = "ready", "confirm"
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️]")
_PUNCT = "!?,;:*#$%&~^_=+|<>.-"                                   # Google Ads refuses these doubled
_PHONE = re.compile(r"(?<!\w)(?:\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}|\+\d[\d\s().-]{8,}\d)(?!\w)")        # a year range (2019-2024) or a price is not a phone number
_URL = re.compile(r"\bwww\.|https?://|\b[a-z0-9-]+\.(?:com|net|org|io|co|us|ca|uk|health|dental)\b", re.I)
_SECOND_PERSON = re.compile(r"\b(?:you|your|yours|you're|you've|you'll|yourself)\b|\bare you\b|\bdo you\b", re.I)
# Meta's personal attributes policy: the ad must not say or imply it knows something about the person looking at it. A stem is matched at the start of a word.
SENSITIVE = ("diabet", "depress", "anxi", "hiv", "cancer", "bankrupt", "in debt", "debt", "overweight", "obes", "pregnan", "disabilit", "arthrit", "adhd", "autis", "addict",
             "alcohol", "herpes", "std", "gay", "lgbt", "christian", "muslim", "jewish", "criminal record", "credit score", "bad credit", "poor credit", "unemploy", "bald", "acne",
             "hair loss", "erectile", "incontinen", "mental health", "eating disorder", "suicid", "sober", "recovery")
_SENSITIVE = re.compile(r"\b(?:%s)" % "|".join(re.escape(s) for s in SENSITIVE), re.I)

REASONS = {"empty": "was empty", "long": "was longer than the limit", "emoji": "held an emoji Google Ads refuses", "exclaim": "had an exclamation mark Google Ads refuses there",
           "caps": "was in capitals", "punct": "repeated a punctuation mark", "phone": "held a phone number or a web address", "avoid": "held a word on the avoid list",
           "rival": "named a competitor", "repeat": "said what another line already says", "attribute": "told the viewer something about themselves (Meta personal attributes policy)",
           "extra": "was more than the format holds", "cta": "named a button the platform does not have"}


# ── Words ───────────────────────────────────────────────────────────────────

def tokens(text):
    return re.findall(r"\w+", str(text or "").lower())


def same_words(a, b):
    """True when two lines say the same: the same words, in any order, or (for a line of four words or more) all but one of them with the length within one word."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return False
    sa, sb = set(ta), set(tb)
    if sa == sb:
        return True
    return len(sa) >= 4 and len(sa & sb) >= len(sa) - 1 and abs(len(sa) - len(sb)) <= 1


def has_phrase(text, phrase):
    p = " ".join(tokens(phrase))
    return bool(p) and (" " + p + " ") in (" " + " ".join(tokens(text)) + " ")


def context(inputs):
    """What every line is checked against, from the form: the words to avoid, the competitors' names, the angles asked for."""
    said = " ".join([inputs.get("name") or "", inputs.get("product") or "", inputs.get("facts") or ""])
    return {"avoid": pa.phrases(inputs.get("avoid")), "rivals": [c["name"] for c in inputs.get("competitors") or [] if c.get("name")],
            "angles": list(inputs.get("angles") or []), "platform": None,
            "caps_ok": {w for w in re.findall(r"\b[A-Z][A-Z0-9]{1,}\b", said)}}          # an acronym the client itself uses (HIPAA, LASIK) is not shouting


# ── One line ────────────────────────────────────────────────────────────────

def check_text(raw, field, platform, ctx):
    """(text, None) when the line is kept, (None, reason) when it is not. `field` is the format's field (its limit, its Start Case)."""
    t = ck.text(raw, 600)
    if not t:
        return None, "empty"
    if field["cased"]:
        t = ck.start_case(t)
    if len(t) > field["limit"]:
        return None, "long"
    letters = [c for c in t if c.isalpha()]
    if len(letters) > 4 and t == t.upper():
        return None, "caps"
    is_description = field["key"] in ("descriptions", "short_description")
    if platform == "google":
        if _EMOJI.search(t):
            return None, "emoji"
        if ("!" in t and not is_description) or t.count("!") > 1:
            return None, "exclaim"
        if any(a == b and a in _PUNCT for a, b in zip(t, t[1:])):
            return None, "punct"
        if any(len(w) >= 5 and w.isalpha() and w == w.upper() and w not in ctx.get("caps_ok", ()) for w in t.split()):
            return None, "caps"
        if _PHONE.search(t) or _URL.search(t):
            return None, "phone"
    if platform == "meta" and _SECOND_PERSON.search(t) and _SENSITIVE.search(t):
        return None, "attribute"
    if any(has_phrase(t, a) for a in ctx["avoid"]):
        return None, "avoid"
    if any(has_phrase(t, r) for r in ctx["rivals"]):
        return None, "rival"
    return t, None


def flags_of(text, platform):
    """Notes a person should read, for a line that is kept: the line may be fine, but it touches something the platform watches."""
    out = []
    if platform == "meta" and _SENSITIVE.search(text):
        out.append("It touches a sensitive subject. Read it against Meta's personal attributes policy before it runs.")
    return out


def entry(text, angle, claims, platform):
    return {"text": text, "angle": angle, "chars": len(text), "status": CONFIRM if claims else READY, "claims": claims, "flags": flags_of(text, platform)}


def angle_of(raw, ctx):
    a = ck.text(raw, 40).lower()
    return a if a in ctx["angles"] else "other"


def clean_field(items, field, platform, ctx, drops, claims_of):
    """The lines of one field that are kept: at most `field.max`, each a dict (see `entry`). `items` are what the model answered, [{text, angle}]; `claims_of(text)` gives the
    claims in a line the evidence does not hold. What is dropped is counted in `drops` ({reason: n})."""
    kept, texts = [], []
    for it in items if isinstance(items, list) else []:
        raw = it.get("text") if isinstance(it, dict) else it
        angle = angle_of(it.get("angle") if isinstance(it, dict) else "", ctx)
        t, why = check_text(raw, field, platform, ctx)
        if not why and any(same_words(t, o) for o in texts):
            why = "repeat"
        if not why and len(kept) >= field["max"]:
            why = "extra"
        if why:
            drops[why] = drops.get(why, 0) + 1
            continue
        texts.append(t)
        kept.append(entry(t, angle, claims_of(t), platform))
    return kept


# ── Claims ──────────────────────────────────────────────────────────────────

def evidence(research, inputs):
    """(corpus_norm, brief_norm, raw): what the client's pages say and what the person said about the client (the name, what to promote, the confirmed facts), for judging a
    claim. Keywords are not evidence (a keyword "free dental implants" does not make a free offer)."""
    from . import paid_strategy_research as rs
    pages = rs.corpus(research)
    said = " ".join([inputs.get("name") or "", inputs.get("product") or "", inputs.get("facts") or ""])
    return ck.norm(pages), ck.norm(said), " ".join((pages + " " + said).lower().split())


def claims_finder(corpus_norm, brief_norm, raw):
    """A function from a line to the claims in it that the evidence does not hold."""
    def find(text):
        got = ck.claims_to_confirm([("", text)], corpus_norm, brief_norm, raw)
        out = []
        for g in got:
            if g["claim"].lower() not in [x.lower() for x in out]:
                out.append(g["claim"])
        return out
    return find


# ── Paths and buttons ───────────────────────────────────────────────────────

def path_text(raw, limit):
    """A display path as Google Ads takes it: letters, digits and hyphens, at most `limit` characters; spaces become hyphens."""
    s = re.sub(r"[^A-Za-z0-9-]+", "-", ck.text(raw, 60)).strip("-")
    return s[:limit].strip("-")


def clean_paths(raw, limit):
    raw = raw if isinstance(raw, dict) else {}
    return {"path1": path_text(raw.get("path1"), limit), "path2": path_text(raw.get("path2"), limit)}


def clean_cta(raw, kind, drops):
    """(button, note): one of the platform's buttons, matched ignoring case; anything else becomes Learn more and is counted."""
    allowed = pa.CTAS.get(kind) or []
    want = ck.text(raw, 40).lower()
    for a in allowed:
        if a.lower() == want:
            return a, ""
    drops["cta"] = drops.get("cta", 0) + 1
    return "Learn more", "The model named a button the platform does not have, so this is the closest standard one."


# ── The brief ───────────────────────────────────────────────────────────────

SCRIPT_BEATS = 8


def clean_brief(raw, platform, ctx, claims_of, drops):
    """The creative brief, cleaned: text fields as plain text, lists as short lines, the words on screen and the spoken lines checked like ad copy (the avoid list, the competitors,
    the claims). Returns None when the model gave nothing."""
    raw = raw if isinstance(raw, dict) else {}

    def blocked(t):
        return any(has_phrase(t, a) for a in ctx["avoid"]) or any(has_phrase(t, r) for r in ctx["rivals"])

    def line(v, n):
        t = ck.text(v, n)
        if t and blocked(t):
            drops["brief"] = drops.get("brief", 0) + 1
            return ""
        return t

    script = []
    for r in raw.get("script") if isinstance(raw.get("script"), list) else []:
        if not isinstance(r, dict):
            continue
        row = {"beat": line(r.get("beat"), 200), "onScreen": line(r.get("onScreen"), 80), "voiceover": line(r.get("voiceover"), 200)}
        if row["beat"] or row["onScreen"] or row["voiceover"]:
            script.append(row)
        if len(script) >= SCRIPT_BEATS:
            break
    out = {"concept": line(raw.get("concept"), 400), "hook": line(raw.get("hook"), 240), "visual": line(raw.get("visual"), 600), "script": script,
           "assets": [t for t in (line(x, 160) for x in (raw.get("assets") if isinstance(raw.get("assets"), list) else [])[:10]) if t],
           "dos": [t for t in (line(x, 200) for x in (raw.get("dos") if isinstance(raw.get("dos"), list) else [])[:8]) if t],
           "donts": [t for t in (line(x, 200) for x in (raw.get("donts") if isinstance(raw.get("donts"), list) else [])[:8]) if t]}
    if not (out["concept"] or out["hook"] or out["visual"] or out["script"]):
        return None
    said = [("hook", out["hook"])] + [("on screen", r["onScreen"]) for r in script] + [("voiceover", r["voiceover"]) for r in script]
    out["claims"] = [{"where": w, "text": t, "claims": c} for w, t in said if t for c in [claims_of(t)] if c]
    return out


# ── A whole format ──────────────────────────────────────────────────────────

def assemble(fmt, raw, inputs, find_claims):
    """The format as it is kept: {key, label, platform, fields, paths, cta, ctaNote, brief, specs, dropped, shortfalls, state}. `raw` is the model's answer for it."""
    spec = pa.FORMATS[fmt]
    ctx = dict(context(inputs), platform=spec["platform"])
    raw = raw if isinstance(raw, dict) else {}
    drops = {}
    fields = []
    for f in spec["fields"]:
        items = clean_field(raw.get(f["key"]), f, spec["platform"], ctx, drops, find_claims)
        fields.append(dict(f, items=items))
    cta, cta_note = (clean_cta(raw.get("cta"), spec["cta"], drops) if spec["cta"] else ("", ""))
    out = {"key": fmt, "label": spec["label"], "short": spec["short"], "platform": spec["platform"], "fields": fields,
           "paths": clean_paths(raw.get("paths"), spec["paths"]) if spec["paths"] else None, "cta": cta, "ctaNote": cta_note,
           "brief": clean_brief(raw.get("brief"), spec["platform"], ctx, find_claims, drops) if pa.wants_brief(inputs, fmt) else None,
           "specs": pa.SPECS.get(fmt) if pa.wants_brief(inputs, fmt) else [], "dropped": drops}
    out["shortfalls"] = shortfalls(out, inputs)
    out["state"] = "short" if out["shortfalls"] else "ok"
    return out


def shortfalls(result, inputs=None):
    """What a format is short of, in words: a field that kept fewer lines than the format needs, a brief that is missing."""
    out = []
    for f in result["fields"]:
        have = len(f["items"])
        if have < f["min"]:
            out.append("%s: %d kept, at least %d needed" % (f["label"], have, f["min"]))
    if inputs is not None and pa.wants_brief(inputs, result["key"]) and not result.get("brief"):
        out.append("The creative brief was not written")
    return out


def problems_for(result, raw=None):
    """The sentence the repair call is given: which fields are short and why lines were lost."""
    parts = []
    for f in result["fields"]:
        have = len(f["items"])
        if have < f["min"]:
            parts.append("%s: only %d of the lines you wrote were usable and at least %d are needed (each must be at most %d characters)" % (f["label"], have, f["min"], f["limit"]))
    why = [REASONS.get(k, k) for k, n in sorted(result["dropped"].items(), key=lambda kv: -kv[1]) if k not in ("extra", "cta") and n]
    if why:
        parts.append("Lines were lost because they " + "; ".join(why[:4]))
    return parts


def merge(first, second, fmt, inputs, find_claims):
    """The answer to a repair added to the first answer: the first answer's kept lines stay, the second's new lines fill what is missing. Both are raw model answers; the
    result is assembled again so every rule is applied to the whole."""
    out = {}
    for f in pa.FORMATS[fmt]["fields"]:
        a, b = (first or {}).get(f["key"]), (second or {}).get(f["key"])
        out[f["key"]] = (a if isinstance(a, list) else []) + (b if isinstance(b, list) else [])
    for k in ("paths", "cta", "brief"):
        out[k] = (first or {}).get(k) if (first or {}).get(k) else (second or {}).get(k)
    return out


# ── Coverage and totals ─────────────────────────────────────────────────────

def keyword_coverage(keywords, results):
    """Which of the keywords given are carried by at least one Google headline (all the keyword's words, in any order): {covered: [...], missing: [...]}."""
    heads = []
    for r in results:
        if r["platform"] != "google":
            continue
        for f in r["fields"]:
            if f["key"] in ("headlines", "long_headlines"):
                heads += [set(tokens(i["text"])) for i in f["items"]]
    covered, missing = [], []
    for k in keywords:
        t = set(tokens(k))
        (covered if t and any(t <= h for h in heads) else missing).append(k)
    return {"covered": covered, "missing": missing, "checked": bool(heads)}


def totals(results):
    ready = confirm = 0
    for r in results:
        for f in r["fields"]:
            for i in f["items"]:
                if i["status"] == READY:
                    ready += 1
                else:
                    confirm += 1
    return {"ready": ready, "confirm": confirm}


def claims_list(results):
    """Every claim that needs the client's confirmation: [{format, field, text, claims}]."""
    out = []
    for r in results:
        for f in r["fields"]:
            out += [{"format": r["short"], "field": f["label"], "text": i["text"], "claims": i["claims"]} for i in f["items"] if i["claims"]]
        for c in (r.get("brief") or {}).get("claims") or []:
            out.append({"format": r["short"], "field": "Brief: " + c["where"], "text": c["text"], "claims": c["claims"]})
    return out
