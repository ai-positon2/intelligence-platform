"""Paid Marketing Agents: Keyword Research, what of the model's answers is kept.

The model's answers (tracker/paid_keywords_ai.py) are text it chose. This file decides what is kept, with the same rules as the other agents' checks
(tracker/paid_strategy_check.py): every field has its type and no markup, no em or en dashes, a word from a fixed list is one of that list or its default, and
what refers to something outside the answer (a keyword, a campaign, a theme) is only kept if that thing is really there. A keyword the model judged that was not
one of the candidates is dropped; a negative keyword that would block a keyword the account bids on or the research recommends is dropped, and says so.
"""
from __future__ import annotations

import re

from . import paid_keywords_data as kd, paid_strategy_check as ck
from .paid_business_check import _pick, _urgency

MAX_THEMES = 10
MAX_NEGATIVES = 40
_NUMBER = re.compile(r"(\d[\d,]*(?:\.\d+)?)(\s?%)?")


def _norm_kw(s):
    return " ".join(str(s or "").lower().split())


def themes(raw, campaigns):
    """The themes the market was grouped into: [{'key', 'name', 'intent', 'priority', 'why', 'campaign' (an existing campaign of the account or ''), 'existing'}].
    `campaigns` are the names the account has. A theme with no name is dropped, a duplicate key is dropped, and an existing campaign that is not one of the account's is blanked."""
    raw = raw if isinstance(raw, dict) else {}
    have = {c.lower(): c for c in campaigns or []}
    out, seen = [], {"other"}
    for r in ck.rows(raw.get("themes"), ["name", "key", "intent", "why", "existingCampaign", "priority"], n=MAX_THEMES + 5, each=240):      # the name first: a row without its first field is dropped
        name = ck.text(r["name"], 60)
        key = kd.slug(r["key"] or name, 30)
        if not name or key in seen:
            continue
        seen.add(key)
        camp = have.get(r["existingCampaign"].strip().lower(), "")
        out.append({"key": key, "name": name, "intent": _pick(r["intent"], kd.INTENTS, "commercial"), "priority": _pick(r["priority"], kd.PRIORITIES, "medium"),
                    "why": r["why"], "campaign": camp, "existing": bool(camp)})
        if len(out) >= MAX_THEMES:
            break
    return {"themes": out, "assumptions": ck.lines(raw.get("assumptions"), 6)}


def theme_key(value, theme_list):
    """The key of the theme a judged keyword names: its key, its name or its slug; 'other' for anything else."""
    v = _norm_kw(value)
    for t in theme_list:
        if v in (t["key"], t["name"].lower(), kd.slug(t["name"], 30)) or kd.slug(v, 30) == t["key"]:
            return t["key"]
    return "other"


def classify(raw, wanted, theme_list):
    """(judged, dropped): {keyword: {'fit', 'intent', 'theme', 'match', 'priority', 'note'}} for the candidates the model answered about, and how many answers were
    dropped (not a candidate, or a second answer about the same keyword). `wanted` is the set of candidate keywords the chunk asked about."""
    raw = raw if isinstance(raw, dict) else {}
    judged, dropped = {}, 0
    for r in ck.rows(raw.get("rows"), ["kw", "fit", "intent", "theme", "match", "priority", "note"], n=400, each=240):
        kw = _norm_kw(str(r["kw"]).split(" | ")[0])                      # a model that echoes a whole line it was given is still understood
        if kw not in wanted or kw in judged:
            dropped += 1
            continue
        judged[kw] = {"fit": _pick(r["fit"], kd.FITS, "maybe"), "intent": _pick(r["intent"], kd.INTENTS, "other"), "theme": theme_key(r["theme"], theme_list),
                      "match": _pick(r["match"], kd.MATCHES, "phrase"), "priority": _pick(r["priority"], kd.PRIORITIES, "medium"), "note": ck.text(r["note"], 160)}
    return judged, dropped


def negatives(raw, keywords, account):
    """(kept, removed): the negative keywords worth adding, and the ones dropped with the reason. A negative is dropped when it would block a keyword the account already
    bids on or one the research recommends (judged a fit), when the account already blocks it in every campaign, and when it repeats another. `keywords` are the
    assembled rows; `account` is the account piece's data."""
    keep_words = [{"text": k["kw"], "match": "exact"} for k in keywords if k.get("fit") == "yes"] + [{"text": " ".join(kd.words(k.get("t"))), "match": "exact"} for k in account.get("keywords") or []]
    nidx = kd.index_negatives(account.get("negatives"), account.get("lists"))
    kcamps = sorted({k.get("c") for k in account.get("keywords") or [] if k.get("c")})
    kept, removed, seen = [], [], set()
    for r in ck.rows(raw.get("negatives") if isinstance(raw, dict) else [], ["kw", "match", "why"], n=MAX_NEGATIVES * 2, each=240):
        text = ck.kw_text(r["kw"])
        match = ck.match_of(r["match"])
        if not text or (text, match) in seen:
            continue
        seen.add((text, match))
        neg = {"text": text, "match": match}
        blocked = next((k for k in keep_words if ck.blocks(neg, k)), None)
        if blocked:
            removed.append({"kw": text, "match": match, "reason": "It would block the keyword \"%s\", which the account bids on or this research recommends." % blocked["text"]})
            continue
        hits = kd.neg_covered_by(text, match, nidx)                    # an existing negative covers it only if it blocks everything it would
        scope = kd.blocked_scope(hits, kcamps)
        if scope == "all":
            removed.append({"kw": text, "match": match, "reason": "The account already blocks it in every campaign it bids in (with \"%s\")." % hits[0]["kw"]})
            continue
        kept.append({"kw": text, "match": match, "why": r["why"], "note": "Some campaigns already block it; add it where it is missing." if scope == "some" else ""})
        if len(kept) >= MAX_NEGATIVES:
            break
    return kept, removed


def _norm_number(tok):
    tok = tok.replace(",", "").rstrip(".")
    return (tok.lstrip("0") or "0") if tok else ""


def numbers_in(text):
    """The numbers a piece of text holds, written plainly (263,610 is 263610): the numbers the model was given."""
    return {_norm_number(m.group(1)) for m in _NUMBER.finditer(str(text or ""))}


def unbacked_numbers(items, allowed):
    """[{'where', 'text', 'number'}] one for each of the (where, text) items that holds a number the model was not given. Small whole numbers (up to twelve, as in
    three tests) and years are words, not figures; a percentage is always a figure."""
    out = []
    for where, text in items:
        for m in _NUMBER.finditer(text or ""):
            n = _norm_number(m.group(1))
            whole = n.isdigit()
            if n in allowed or (whole and not m.group(2) and (int(n) <= 12 or 2000 <= int(n) <= 2100)):
                continue
            out.append({"where": where, "text": text, "number": m.group(0).strip()})
            break
    return out


def plan(raw, keywords, account, allowed=None):
    """The plan part of the answer. The model is not asked for a number: any it writes that it was not given is listed (`numbers`) for a person to check before the
    text is shared. `allowed` is the set of numbers it was given (numbers_in of the evidence); None skips the check."""
    raw = raw if isinstance(raw, dict) else {}
    kept, removed = negatives(raw, keywords, account or {})
    questions = ck.rows(raw.get("questions"), ["question", "area", "why", "urgency"], n=20)
    for q in questions:
        q["area"] = q["area"] or "General"
        q["urgency"] = _urgency(q["urgency"])
    out = {"summary": ck.lines(raw.get("summary"), 7, 320), "quickWins": ck.rows(raw.get("quickWins"), ["what", "why"], n=8), "negatives": kept, "removedNegatives": removed,
           "tests": ck.rows(raw.get("tests"), ["hypothesis", "how", "metric"], n=8), "questions": questions, "risks": ck.rows(raw.get("risks"), ["risk", "mitigation"], n=8),
           "seasonal": ck.rows(raw.get("seasonal"), ["window", "action"], n=8)}
    items = [("In short", t) for t in out["summary"]] + [("First move", "%s. %s" % (q["what"], q["why"])) for q in out["quickWins"]]
    items += [("Test", "%s. %s. %s" % (t["hypothesis"], t["how"], t["metric"])) for t in out["tests"]] + [("Risk", "%s. %s" % (r["risk"], r["mitigation"])) for r in out["risks"]]
    items += [("Season", "%s. %s" % (s["window"], s["action"])) for s in out["seasonal"]]
    out["numbers"] = unbacked_numbers(items, allowed) if allowed is not None else []
    return out
