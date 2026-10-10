"""Market Radar, Phase 7: the client report.

    write_report(client_id, owner_email, run_id=run)

Three steps, each checkable on its own:

1. EVIDENCE PACK, built by code from what the collections stored: the
   client as read, its competitors, the moves the signal engine ranked for
   this client (every HIGH and MEDIUM, then the best LOW, up to MAX_MOVES),
   the radar's finds, the industry pulse's themes and US rules, each
   competitor's open roles, and the coverage lines. Every item gets a
   reference (M3, T2, N1...) and keeps its sources. No model sees anything
   that is not in the pack.

2. THE BRIEF. Sonnet writes the report's words from the pack: a summary,
   the top five with "so what" and a suggested action, a paragraph per
   competitor that did something, the local picture, the industry themes'
   implications, opportunities, threats and what to watch. Every statement
   cites pack references; one that cites nothing real is dropped in code.

3. THE CITATION CHECK. Haiku reads each statement next to the evidence it
   cites and says whether its facts (names, numbers, dates, places, what
   happened, and whether it has happened) are in that evidence. A statement
   that is not supported is removed, not softened, and the report says how
   many were and why. Advice and interpretation are allowed when they
   follow from cited facts; that is what the client pays for.

A report that could not be written is stored as "failed" with the reason,
so the page says so rather than showing an old one as new.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

WRITER_MODEL = os.environ.get("MR_REPORT_MODEL", "claude-sonnet-5-5")
CHECK_MODEL = os.environ.get("MR_REPORT_CHECK_MODEL", "claude-haiku-5-5")
MAX_MOVES = 50
MAX_THEMES = 8
MAX_SOURCES = 4
TOP_MAX = 5


def _clean(text, limit):
    text = re.sub(r"\s*[%s%s]\s*" % (chr(0x2014), chr(0x2013)), ", ",
                  " ".join(str(text or "").split()))
    return text[:limit]


def _iso(v):
    return v.isoformat()[:10] if hasattr(v, "isoformat") else (str(v)[:10] if v else None)


# == 1. the evidence pack ================================================================

def build_pack(client_id, owner_email, *, store, now, collection=None, run_id=None):
    """`collection` is the result of the collection this report is written
    at the end of (its run row has no summary yet); without it, the latest
    finished collection is read."""
    from . import market_radar_views as views
    from . import market_radar_pulse as mp
    c = store.get_client(client_id, owner_email)
    if not c:
        raise PermissionError("client %s is not yours or does not exist" % client_id)
    profile = views.effective_profile(c["profile"] or {}, c["settings"])
    hq = profile.get("hq") or {}
    pack = {"client": {
        "name": profile.get("name") or c.get("name") or c["domain"], "domain": c["domain"],
        "one_liner": profile.get("one_liner"), "archetype": profile.get("archetype"),
        "industry": (profile.get("industry") or {}).get("plain_label"),
        "city": hq.get("city"), "country": hq.get("country_code"),
        "locations": profile.get("location_count"), "offerings": (profile.get("offerings") or [])[:6]},
        "competitors": [], "moves": [], "nearby": [], "entrants": [], "themes": [], "rules": [],
        "hiring": [], "coverage": [], "built_at": now.isoformat(timespec="seconds")}

    comps = store.competitors(client_id, owner_email)
    cref = {}
    for i, r in enumerate(comps):
        ref = "C%d" % (i + 1)
        cref[r["entity_id"]] = ref
        pack["competitors"].append({
            "ref": ref, "entity_id": r["entity_id"], "name": r.get("name") or r["domain"],
            "domain": r["domain"], "kind": r["kind"], "status": r["status"],
            "sells": (r.get("details") or {}).get("sells")})
    names = {r["entity_id"]: r.get("name") or r["domain"] for r in comps}

    # Moves, as scored for this client.
    scores = store.client_scores(client_id)
    events = [e for e in store.recent_events(list(cref), days=120, limit=1000)
              if not e.get("hidden_reason") and e["id"] in scores]
    events.sort(key=lambda e: -(scores[e["id"]].get("score") or 0))
    strong = [e for e in events if scores[e["id"]].get("severity") in ("HIGH", "MEDIUM")]
    weak = [e for e in events if scores[e["id"]].get("severity") not in ("HIGH", "MEDIUM")]
    for i, e in enumerate((strong + weak)[:MAX_MOVES]):
        sc = scores[e["id"]]
        srcs = e.get("sources") or []
        pack["moves"].append({
            "ref": "M%d" % (i + 1), "event_id": e["id"], "entity_id": e["entity_id"],
            "company": names.get(e["entity_id"]), "company_ref": cref.get(e["entity_id"]),
            "type": e["type"], "label": views.EVENT_LABELS.get(e["type"], e["type"]),
            "status": e["status"], "date": _iso(e.get("event_date")) or _iso(e.get("first_seen_at")),
            "title": e["title"], "summary": e.get("summary"),
            "place": (e.get("location") or {}).get("label"),
            "severity": sc.get("severity"), "score": sc.get("score"),
            "distance_km": sc.get("distance_km"), "feedback": sc.get("feedback"),
            "articles": sum(1 for s in srcs if s.get("detector") == "news"),
            "evidence": e.get("evidence_count"), "first_seen": e.get("first_seen_at"),
            "sources": [{k: s.get(k) for k in ("url", "detector", "publisher", "headline", "date")}
                        for s in srcs[:MAX_SOURCES]]})
    left = len(events) - len(pack["moves"])
    if left > 0:
        pack["coverage"].append("%d lower-ranked moves were not given to the writer." % left)

    # The radar, from the latest collection.
    if collection is not None:
        run, summary = {"id": run_id, "finished_at": now}, collection
    else:
        run = store.latest_run(client_id, owner_email, collect=True)
        summary = (run or {}).get("summary") or {}
    radar = summary.get("radar") or {}
    for part, key, prefix in (("local", "nearby", "N"), ("entrants", "entrants", "B")):
        x = radar.get(part) or {}
        for i, f in enumerate((x.get("findings") or [])[:15]):
            pack[key].append({"ref": "%s%d" % (prefix, i + 1), **{k: f.get(k) for k in (
                "name", "category", "distance_km", "address", "website", "status", "certain",
                "evidence", "date", "lat", "lon", "location", "reason", "is_competitor")}})
        if x.get("note"):
            pack["coverage"].append(("Nearby: " if part == "local" else "New brands: ") +
                                    _clean(x["note"], 300))
    point = profile.get("hq_point") or {}
    if point.get("lat") is not None:
        pack["client"]["point"] = {"lat": point["lat"], "lon": point["lon"]}
        pack["client"]["radius_km"] = float(c.get("radius_km") or 0) or None

    # The industry pulse, per market.
    key = mp.industry_key(profile)
    n = 0
    for country in (mp.markets(profile) if key else []):
        row = store.latest_pulse(key, country)
        p = (row or {}).get("payload") or {}
        if p.get("read_at") and p["read_at"] > (pack.get("pulse_read_at") or ""):
            pack["pulse_read_at"] = p["read_at"]
        if not p:
            pack["coverage"].append("Industry news for %s has not been read yet." % country)
            continue
        for t in (p.get("themes") or [])[:MAX_THEMES]:
            n += 1
            pack["themes"].append({"ref": "T%d" % n, "country": country, "title": t["title"],
                                   "summary": t.get("summary"), "why": t.get("why_it_matters"),
                                   "kind": t.get("kind"),
                                   "articles": [{k: a.get(k) for k in ("title", "publisher", "date",
                                                                       "link")}
                                                for a in (t.get("articles") or [])[:MAX_SOURCES]]})
        for d in (p.get("regulation") or [])[:8]:
            pack["rules"].append({"ref": "R%d" % (len(pack["rules"]) + 1), **{
                k: d.get(k) for k in ("title", "type", "agency", "date", "link")}})
        if p.get("note"):
            pack["coverage"].append("Industry news (%s): %s" % (country, _clean(p["note"], 300)))

    # Hiring: each competitor's latest jobs read.
    for e in comps:
        snap = store.latest_snapshot(e["entity_id"], "jobs")
        jp = (snap or {}).get("payload") or {}
        if "open" not in jp:
            continue
        pack["hiring"].append({
            "ref": "H%d" % (len(pack["hiring"]) + 1), "company": names[e["entity_id"]],
            "company_ref": cref[e["entity_id"]], "open": jp.get("open"),
            "places": len(jp.get("places") or {}),
            "functions": list((jp.get("by_function") or {}).items())[:4],
            "senior": (jp.get("senior_open") or [])[:4],
            "read": _iso((snap or {}).get("last_seen_at"))})

    for line in summary.get("coverage") or []:
        pack["coverage"].append(line.get("text"))
    sig = summary.get("signals") or {}
    if sig.get("note"):
        pack["coverage"].append("Competitor news: " + _clean(sig["note"], 300))
    pack["collect_run_id"] = (run or {}).get("id")
    pack["collected_at"] = _iso((run or {}).get("finished_at") or (run or {}).get("created_at"))
    return pack


NO_CURRENCY_TYPES = {"product_launch", "product_removed", "sold_out"}


def evidence_text(item):
    """One pack item as the writer and the checker read it."""
    ref = item["ref"]
    if ref[0] == "M":
        # A shop's product prices come without a currency (Shopify's
        # products.json has none), and "a hoodie at 62.0" reached a report.
        summary = None if item.get("type") in NO_CURRENCY_TYPES else item.get("summary")
        src = "; ".join("%s%s: %s" % (s.get("publisher") or s.get("detector") or "",
                                      " (%s)" % s["date"] if s.get("date") else "",
                                      s.get("headline") or "") for s in item["sources"])
        return "%s | %s | %s | %s | %s | %s%s%s | sources: %s" % (
            ref, item["company"], item["label"], item["status"], item["date"] or "undated",
            item["title"], " | " + item["place"] if item.get("place") else "",
            " | " + summary if summary else "", src)
    if ref[0] == "N":
        return "%s | %s, %s, %s km away | %s | %s" % (
            ref, item["name"], (item.get("category") or "").replace("_", " "),
            item.get("distance_km"), "possibly new" if item.get("certain") is False else
            item.get("status"), "; ".join(item.get("evidence") or []))
    if ref[0] == "B":
        return "%s | new brand %s | %s | %s" % (ref, item["name"], item.get("reason") or "",
                                                 "; ".join(item.get("evidence") or []))
    if ref[0] == "T":
        return "%s | industry theme (%s): %s | %s | why: %s | articles: %s" % (
            ref, item["country"], item["title"], item.get("summary") or "", item.get("why") or "",
            "; ".join("%s (%s, %s)" % (a["title"], a.get("publisher"), a.get("date"))
                      for a in item["articles"]))
    if ref[0] == "R":
        return "%s | US Federal Register %s, %s, %s: %s" % (ref, item.get("type"), item.get("agency"),
                                                          item.get("date"), item["title"])
    if ref[0] == "H":
        return "%s | %s hiring: %s open roles%s in %s places; by function: %s; senior roles: %s" % (
            ref, item["company"], item["open"],
            " (was %s at the previous update)" % item["before"] if item.get("before") is not None
            else "", item["places"],
            ", ".join("%s %s" % tuple(kv) for kv in item["functions"]) or "n/a",
            "; ".join(item["senior"]) or "none")
    if ref[0] == "C":
        return "%s | competitor %s (%s), %s, %s%s" % (
            ref, item["name"], item["domain"], item["kind"], item["status"],
            ", sells " + item["sells"] if item.get("sells") else "")
    return ref


def items_by_ref(pack):
    out = {}
    for key in ("competitors", "moves", "nearby", "entrants", "themes", "rules", "hiring"):
        for it in pack[key]:
            out[it["ref"]] = it
    return out


def pack_text(pack):
    c = pack["client"]
    parts = ["CLIENT: %s (%s). %s Business type: %s. Industry: %s. Based in %s, %s.%s" % (
        c["name"], c["domain"], c.get("one_liner") or "", c.get("archetype"), c.get("industry"),
        c.get("city") or "?", c.get("country") or "?",
        " Sells: " + ", ".join(c["offerings"]) + "." if c.get("offerings") else "")]
    for title, key in (("COMPETITORS", "competitors"), ("COMPETITOR MOVES, highest ranked first "
                                                        "(severity is for this client)", "moves"),
                       ("NEW NEARBY", "nearby"), ("NEW BRANDS", "entrants"),
                       ("INDUSTRY THEMES", "themes"), ("US RULES", "rules"),
                       ("HIRING", "hiring")):
        items = pack[key]
        if not items:
            continue
        lines = []
        for it in items:
            line = evidence_text(it)
            if key == "moves":
                line = "%s [%s]" % (line, it.get("severity") or "LOW")
            lines.append(line)
        parts.append("%s:\n%s" % (title, "\n".join(lines)))
    if pack["coverage"]:
        parts.append("WHAT WAS AND WAS NOT CHECKED:\n" + "\n".join("- " + l for l in pack["coverage"]
                                                                  if l))
    return "\n\n".join(parts)


# == 2. the brief =======================================================================

CITED = {"type": "array", "items": {"type": "string"}}
BRIEF_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["summary", "top", "competitors", "local", "industry", "opportunities", "threats",
                 "watch"],
    "properties": {
        "summary": {"type": "object", "additionalProperties": False, "required": ["text", "cites"],
                    "properties": {"text": {"type": "string"}, "cites": CITED}},
        "top": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["title", "what_happened", "so_what", "action", "confidence", "cites"],
            "properties": {"title": {"type": "string"}, "what_happened": {"type": "string"},
                           "so_what": {"type": "string"}, "action": {"type": "string"},
                           "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                           "cites": CITED}}},
        "competitors": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["company_ref", "summary", "cites"],
            "properties": {"company_ref": {"type": "string"}, "summary": {"type": "string"},
                           "cites": CITED}}},
        "local": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "cites"],
            "properties": {"text": {"type": "string"}, "cites": CITED}}},
        "industry": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["title", "implication", "cites"],
            "properties": {"title": {"type": "string"}, "implication": {"type": "string"},
                           "cites": CITED}}},
        "opportunities": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "cites"],
            "properties": {"text": {"type": "string"}, "cites": CITED}}},
        "threats": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "cites"],
            "properties": {"text": {"type": "string"}, "cites": CITED}}},
        "watch": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["text", "cites"],
            "properties": {"text": {"type": "string"}, "cites": CITED}}}}}

WRITER_SYSTEM = """You write a market-intelligence brief for the business named as CLIENT, from an evidence pack: its competitors' recent moves, new businesses near it or new brands in its category, its industry's news themes and the hiring of its competitors. The reader runs the business and has five minutes.

Use only the evidence pack. Every statement cites the references it rests on (M3, T2, N1, H4, C2...). Never state a fact (a name, number, date, place, or what a company did) that is not in the evidence you cite. Do not invent figures, motives or outcomes. A move marked planned or announced has not happened yet: say "plans to", "will", "announced".

Write:
- summary: two or three sentences on what matters most this period for the client. It cites every reference whose facts it uses.
- top: the 1 to 5 developments that matter most to THIS client, most important first. In a quiet period list fewer: a website wording change, a routine product colour or a single small promotion is not a development unless it shows a new service, price, offer or market. Rank by consequence for the client, using severity as a guide, not by how often something was reported. Each has:
  - title: at most 10 words.
  - what_happened: one or two sentences of fact.
  - so_what: one or two sentences on what it means for the client specifically (its location, its offer, its customers).
  - action: one concrete thing the client could do in the next weeks. Practical, specific, modest. Not "monitor the situation".
  - confidence: high when several independent sources or the company itself confirm it and it has happened; medium for one solid source or a planned move; low for a rumour, a single weak source or an inference.
- competitors: for each competitor that did something worth knowing, two or three sentences on what it is doing, citing its moves. company_ref is its C reference. Skip a competitor whose only moves are routine: new colours or products in its shop, a page wording change, a small promotion.
- local: for a business that serves customers at a place, one or two statements on new businesses nearby. Empty for others or when there is nothing.
- industry: for each industry theme that matters to the client (at most 5), a title and one sentence on its implication for the client.
- opportunities, threats: two to four each, one sentence each, specific to the client.
- watch: two or three things to check in the coming weeks (a planned opening's date, a pending deal).

Rules for facts:
- Facts about the client itself (its location, offer, size) come only from the CLIENT line. Do not assume anything else about the client (ownership, payment model, history).
- Give a price only with the currency the evidence states; if it states none, leave the price out.
- Do not claim that something did not happen or was not found; say only what the evidence shows.

Plain English, short sentences, no jargon, no marketing tone. Never use em dashes or en dashes. If the evidence is thin, write less: an empty section is better than padding."""


def write_brief(pack, *, run_id=None, client=None, llm=None):
    if llm is None:
        from . import market_radar_llm as llm
    data, meta = llm.call_json(WRITER_SYSTEM, pack_text(pack), BRIEF_SCHEMA, model=WRITER_MODEL,
                               max_tokens=12000, run_id=run_id, stage="report_write",
                               client=client, effort="medium")
    return clean_brief(data, items_by_ref(pack)), meta


def _cites(raw, refs):
    out = []
    for r in raw or []:
        r = str(r).strip().upper()
        if r in refs and r not in out:
            out.append(r)
    return out


def clean_brief(data, refs):
    """Keep only statements that cite at least one real reference, cap the
    lists, and remove dashes. Returns the brief and what was dropped."""
    dropped = []

    def keep(item, fields, where):
        cites = _cites(item.get("cites"), refs)
        if not cites:
            dropped.append({"where": where, "text": _clean(item.get(fields[0]), 200),
                            "why": "cited no evidence from the pack"})
            return None
        out = {f: _clean(item.get(f), 700) for f in fields}
        out["cites"] = cites
        return out

    brief = {"summary": keep(data.get("summary") or {}, ["text"], "summary")}
    top = []
    for i, t in enumerate(data.get("top") or []):
        k = keep(t, ["title", "what_happened", "so_what", "action"], "top %d" % (i + 1))
        if k:
            k["confidence"] = t.get("confidence") if t.get("confidence") in ("high", "medium",
                                                                           "low") else "low"
            top.append(k)
    brief["top"] = top[:TOP_MAX]
    comps = []
    for i, c in enumerate(data.get("competitors") or []):
        ref = str(c.get("company_ref") or "").strip().upper()
        if ref not in refs or not ref.startswith("C"):
            dropped.append({"where": "competitor %d" % (i + 1), "text": _clean(c.get("summary"), 200),
                            "why": "names no competitor from the list"})
            continue
        k = keep(c, ["summary"], "competitor " + ref)
        if k:
            k["company_ref"] = ref
            comps.append(k)
    brief["competitors"] = comps
    for key, fields, cap in (("local", ["text"], 2), ("industry", ["title", "implication"], 5),
                             ("opportunities", ["text"], 4), ("threats", ["text"], 4),
                             ("watch", ["text"], 3)):
        brief[key] = [k for k in (keep(x, fields, "%s %d" % (key, i + 1))
                                  for i, x in enumerate(data.get(key) or [])) if k][:cap]
    return brief, dropped


# == 3. the citation check ==============================================================

CHECK_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["verdicts"],
    "properties": {"verdicts": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "reasoning", "unsupported_facts", "supported"],
        "properties": {"id": {"type": "string"}, "reasoning": {"type": "string"},
                       "unsupported_facts": {"type": "array", "items": {"type": "string"}},
                       "supported": {"type": "boolean"}}}}}}
CHECK_SYSTEM = """You check a market-intelligence brief against its evidence. For each numbered statement you get the statement and the evidence it cites.

A statement is SUPPORTED when every fact in it is in its cited evidence: company names, numbers, dates, places, what a company did, and whether it has happened (a move the evidence calls planned or announced must not be stated as done). Advice, interpretation and implications for the client are allowed when they follow from the cited facts; do not mark a statement unsupported for giving advice.

A statement is NOT SUPPORTED when it states a fact that is not in its cited evidence, contradicts it, or turns a plan into a done deal.

Facts about the client itself are checked against the CLIENT line given with each statement, and facts about what was searched or found against the WHAT WAS AND WAS NOT CHECKED lines.

Return one verdict per statement id, in this order: reasoning (one short sentence, checking the facts one by one), unsupported_facts (each fact that is not in the evidence, quoted briefly; empty when all are), then supported (false only when unsupported_facts is not empty)."""


def statements(brief):
    """[(id, text, cites)] for every statement in the brief."""
    out = []
    if brief.get("summary"):
        out.append(("summary", brief["summary"]["text"], brief["summary"]["cites"]))
    for i, t in enumerate(brief["top"]):
        out.append(("top.%d" % i, "%s. %s %s Suggested action: %s" % (
            t["title"], t["what_happened"], t["so_what"], t["action"]), t["cites"]))
    for i, c in enumerate(brief["competitors"]):
        out.append(("competitors.%d" % i, c["summary"], c["cites"]))
    for key in ("local", "opportunities", "threats", "watch"):
        for i, x in enumerate(brief[key]):
            out.append(("%s.%d" % (key, i), x["text"], x["cites"]))
    for i, x in enumerate(brief["industry"]):
        out.append(("industry.%d" % i, "%s: %s" % (x["title"], x["implication"]), x["cites"]))
    return out


def check_brief(brief, pack, *, run_id=None, client=None, llm=None):
    """(brief with unsupported statements removed, removed, note). A check
    that could not run keeps the brief and says it was not checked."""
    if llm is None:
        from . import market_radar_llm as llm
    refs = items_by_ref(pack)
    stmts = statements(brief)
    if not stmts:
        return brief, [], None
    # The client and what was checked: the writer may state both, so the
    # checker reads both (a true "18 other new map listings" was removed
    # for want of the coverage line, 2026-10-10).
    client_line = pack_text({"client": pack["client"], "competitors": [], "moves": [],
                             "nearby": [], "entrants": [], "themes": [], "rules": [], "hiring": [],
                             "coverage": pack.get("coverage") or []})
    user = "\n\n".join("STATEMENT %s: %s\nEVIDENCE:\n%s\n%s" % (
        sid, text, client_line, "\n".join(evidence_text(refs[r]) for r in cites))
        for sid, text, cites in stmts)
    try:
        data, _meta = llm.call_json(CHECK_SYSTEM, user, CHECK_SCHEMA, model=CHECK_MODEL,
                                    max_tokens=min(12000, 600 + 120 * len(stmts)),
                                    run_id=run_id, stage="report_check", client=client)
    except Exception as e:
        return brief, [], "The citation check could not run (%s), so the statements below " \
            "were not checked against their evidence." % getattr(e, "kind", type(e).__name__)
    verdicts = {str(v.get("id")): v for v in data.get("verdicts") or []}
    unchecked = [sid for sid, _t, _c in stmts if sid not in verdicts]
    # Removed only when the check names a fact it could not find: a "false"
    # with nothing named was the checker contradicting its own reasoning
    # (live, 2026-10-10: "supported on its face ... so supported", false).
    bad = {}
    for sid, _t, _c in stmts:
        v = verdicts.get(sid) or {}
        facts = [_clean(f, 120) for f in v.get("unsupported_facts") or [] if str(f).strip()]
        if v.get("supported") is False and facts:
            bad[sid] = "; ".join(facts)
    removed = [{"where": sid, "text": _clean(text, 300), "why": _clean(bad[sid], 200)}
               for sid, text, _c in stmts if sid in bad]
    out = dict(brief)
    if "summary" in bad:
        out["summary"] = None
    for key in ("top", "competitors", "local", "industry", "opportunities", "threats", "watch"):
        out[key] = [x for i, x in enumerate(brief[key]) if "%s.%d" % (key, i) not in bad]
    note = None
    if unchecked:
        note = "%d statements got no verdict from the check and are shown unchecked." % len(unchecked)
    return out, removed, note


# == one report =========================================================================

def empty_note(pack, collection):
    """Why there is nothing to write about, as it happened: never collected,
    or collected and nothing found, with what could and could not be read
    (a collection whose every read failed is not "nothing collected yet":
    found with the network switched off, 2026-10-10)."""
    if collection is None:
        return "Nothing has been collected yet for this client, so there is nothing to write about."
    lines = [_clean(l, 200) for l in pack.get("coverage") or [] if l and str(l).strip()]
    note = ("This collection found nothing to write about: no competitor moves, nearby openings, "
            "new brands or industry themes.")
    if lines:
        note += " What was and was not read: " + " ".join(
            l if l.endswith(".") else l + "." for l in lines[:6])
    return note


def write_report(client_id, owner_email, *, run_id=None, store=None, now=None, llm=None,
                 client=None, collection=None):
    """Build the pack, write and check the brief, store the report. Returns
    a short summary for the run."""
    if store is None:
        from . import market_radar_store as store
    now = now or datetime.now(timezone.utc)
    pack = build_pack(client_id, owner_email, store=store, now=now, collection=collection,
                      run_id=run_id)
    report = {"status": "ok", "pack": pack, "written_at": now.isoformat(timespec="seconds"),
              "models": {"writer": WRITER_MODEL, "check": CHECK_MODEL}}
    if not (pack["moves"] or pack["themes"] or pack["nearby"] or pack["entrants"]):
        report.update(status="empty", brief=None, note=empty_note(pack, collection))
    else:
        try:
            (brief, dropped), _meta = write_brief(pack, run_id=run_id, client=client, llm=llm)
        except Exception as e:
            report.update(status="failed", brief=None,
                          note="The report could not be written (%s: %s)." % (
                              getattr(e, "kind", type(e).__name__),
                              str(getattr(e, "detail", e))[:200]))
        else:
            checked, removed, check_note = check_brief(brief, pack, run_id=run_id, client=client,
                                                       llm=llm)
            report.update(brief=checked, dropped=dropped, removed=removed, check_note=check_note,
                          checked=check_note is None or "no verdict" in (check_note or ""))
    report_id = store.save_report(client_id, report, run_id=run_id)
    out = {"report_id": report_id, "status": report["status"], "note": report.get("note")}
    if report.get("brief"):
        b = report["brief"]
        out.update(top=len(b["top"]), removed=len(report.get("removed") or []),
                   dropped=len(report.get("dropped") or []), check_note=report.get("check_note"))
    return out
