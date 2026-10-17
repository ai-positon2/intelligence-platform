"""Paid Marketing Agents: Landing Page & Relevancy Check, the report.

Reads a Google Ads account's landing pages (tracker/paid_lp_read.py), fetches the busiest of them (tracker/paid_lp_page.py), holds each page
against the checklist (tracker/paid_lp_checks.py) and returns a score for every page and for the account, the checks to put right first, and, for
each page, the keywords, search terms and ad headlines with whether the page carries them.

The account is a 10-digit Google Ads customer id. An MCC is not checked (a check is of one account). What is read and fetched is saved in the shared
Postgres cache (tracker/paid_gads.py helpers) as one raw read per account per hour, so the report itself is arithmetic on a saved read and costs nothing
to make again: Refresh limited per account, a spent quota served from the last saved read of any age with its date. A read with a part missing (a
warning, or a page not read in time) is kept for only five minutes, so an account is not stuck on a failure for an hour after Google recovers.
A part that could not be read is a warning and its checks say "could not check": nothing is passed or failed on what could not be seen.
Read only: nothing is changed in Google Ads and the pages are only fetched.

Every figure in the report comes from Google Ads or the page. A finding names its numbers; nothing here is a model's opinion.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import threading

from . import paid_audit_pages as audit_pages
from . import paid_gads as base
from . import paid_gads_segments as gads
from . import paid_lp_checks as checks
from . import paid_lp_match as words
from . import paid_lp_model as model
from . import paid_lp_page as lp_page
from . import paid_lp_read as lp_read
from . import paid_segments as seg

BadRequest = seg.BadRequest

log = logging.getLogger(__name__)

ID_RE = re.compile(r"^\d{10}$")
MAX_PAGES = 40                                     # fetched and checked per account, the ones that spent most
MIN_JUDGED = 10                                    # checks a page needs judged (not "could not check") before it counts towards the account's score
CACHE_VERSION = "1"                                # bump when the shape of a saved read changes
TTL_S = 3600
PARTIAL_TTL_S = 300
SHOWN_GROUPS = 8                                   # ad groups shown per page
SHOWN_KEYWORDS = 12
SHOWN_TERMS = 8
SHOWN_HEADLINES = 6
FIX_ITEMS = 6                                      # affected things named under one page of the fix-first list
NOTES = [
    "Pages are fetched from our servers and only their HTML is read, as a browser first receives it. Anything a script adds after load is not seen, so a page "
    "built by scripts is reported as such, not failed.",
    "A site that refuses automated requests (HTTP 401, 403 or 429) is reported as not verified, not as broken. Google's crawler and real visitors are normally let in.",
    "Server time is measured from our servers and includes redirects. Google's own mobile speed score and the optional lab test are shown beside the "
    "measured figures and never replace them.",
    "A keyword is on the page when every one of its words is (plurals and common endings count). Search terms and keywords are weighted by what they spent.",
]
FINDING_KEYS = ("id", "result", "severity", "found", "items", "count", "caps", "detail")


def parse_account(raw):
    """The 10-digit Google Ads customer id in `raw`."""
    raw = str(raw or "").strip()
    if not ID_RE.match(raw):
        raise BadRequest("account should be a 10-digit Google Ads customer id")
    return raw


# ── Reading ─────────────────────────────────────────────────────────────────

def raw_read(account, *, search_fn=None, fetcher=None, now=None, max_pages=MAX_PAGES):
    """The raw read of one account: Google's side, and the busiest `max_pages` pages fetched. Reading the landing pages from Google failing is raised."""
    read = lp_read.read_account(account["id"], search_fn=search_fn)
    chosen = [p for p in read["pages"] if audit_pages.clean(p["url"])][:max_pages]
    records = lp_page.fetch_many([p["url"] for p in chosen], fetcher) if chosen else []
    fetches = {p["id"]: rec for p, rec in zip(chosen, records)}
    return {"read": read, "fetches": fetches, "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or ""},
            "readAt": (now or dt.datetime.now(dt.timezone.utc)).isoformat()}


_LOCKS, _LOCKS_GUARD = {}, threading.Lock()


def cache_key(account_id):
    return base.CACHE_V + "lp%s:%s" % (CACHE_VERSION, account_id)


def _partial(raw):
    """True when something in the read is missing: a Google part that failed or a page that was not read in time."""
    return bool(raw["read"].get("warnings")) or any(f.get("skipped") for f in raw["fetches"].values())


def _usable(hit):
    raw, saved = hit
    return not (_partial(raw) and (saved is None or base._age_s(saved) > PARTIAL_TTL_S))


def _cached(account_id, build, *, force, store):
    """(raw read, saved-at or None). Two people asking at once share one read; with `force` the saved read is skipped (at most once per
    paid_gads.REFRESH_COOLDOWN_S per account); when the platform's allowance is spent the last saved read of any age is served with its date."""
    key = cache_key(account_id)
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.Lock())
    if force:
        base.refresh_guard(store, "lp:%s" % account_id)
        base.forget(key)
    with lock:
        if not force:
            hit = base._entry(store, key, TTL_S)
            if hit is not None and _usable(hit):
                return hit[0], None
        try:
            raw = build()
        except gads.GadsError as err:
            old = base._saved_copy(store, key) if err.kind == "quota" else None
            if old is None:
                raise
            return old[0], old[1]
        saved = base._now_iso()
        try:
            store.put_gads_cache(key, raw)
        except Exception as err:                          # noqa: BLE001 (a failed save never costs the report)
            log.warning("landing page check: could not save the read (%s)", err)
        base._mem_put(key, raw, saved)
        return raw, None


# ── The report ──────────────────────────────────────────────────────────────

def _prune(finding):
    return {k: finding[k] for k in FINDING_KEYS if k in finding and finding[k] not in (None, "", [])}


def _seen(fetch):
    """What the page was seen to say and offer, small enough to send with every page."""
    if not fetch:
        return None
    f = fetch.get("facts") or {}
    base_ = {"status": fetch.get("status"), "error": fetch.get("error") or "", "blocked": bool(fetch.get("blocked")), "skipped": bool(fetch.get("skipped")),
             "finalUrl": fetch.get("finalUrl") or "", "redirected": bool(fetch.get("redirected")), "responseMs": fetch.get("responseMs"), "ms": fetch.get("ms"),
             "bytes": fetch.get("bytes"), "encoding": fetch.get("encoding") or "", "truncated": bool(fetch.get("truncated")), "adsbot": fetch.get("adsbot")}
    if not f:
        return base_
    forms = [x for x in f.get("forms") or [] if not x.get("search")]
    base_.update({
        "title": f.get("title"), "h1": f.get("h1"), "h1Count": f.get("h1Count"), "h2": (f.get("h2") or [])[:5], "description": f.get("metaDescription"),
        "words": f.get("wordCount"), "scriptBuilt": bool(f.get("scriptBuilt")), "viewport": f.get("viewport"),
        "forms": [{"fields": x["visible"], "required": x["required"], "phone": x.get("phone"), "email": x.get("email")} for x in forms[:3]],
        "embeds": f.get("embeds"), "booking": f.get("booking"), "commerce": f.get("commerce"),
        "ctas": [{"text": x["text"], "kind": x["kind"], "at": x["at"]} for x in (f.get("ctas") or [])[:8]], "tel": f.get("tel"), "phones": f.get("phones"),
        "address": f.get("address"), "emails": f.get("emails"), "links": f.get("links"), "images": f.get("images"),
        "scripts": (f.get("head") or {}).get("scripts"), "blockingScripts": (f.get("head") or {}).get("blockingScripts"),
        "thirdParty": len((f.get("head") or {}).get("thirdParty") or []), "proof": f.get("proof"), "privacyLink": f.get("privacyLink"), "consent": f.get("consent"),
        "tools": [t["name"] for t in (fetch.get("tech") or {}).get("tools", [])], "paramsSurvive": fetch.get("paramsSurvive")})
    return base_


def _group_rows(page, groups):
    """For each ad group that sends people to the page: its ads' headlines (and whether the page echoes the best), its keywords and search terms with
    whether the page carries each, and the promises in its ads with whether the page has them."""
    fetch = page.get("fetch") or {}
    facts = fetch.get("facts")
    readable = bool(facts) and not facts.get("scriptBuilt")
    prom, full = words.page_vocabs(facts) if readable else (set(), set())
    pid = page["id"]
    out = []
    for g in sorted(groups, key=lambda g: -g["cost"])[:SHOWN_GROUPS]:
        heads = [h for ad in g["ads"] for h in ad["headlines"]]
        share, best = words.best_echo(heads, prom) if readable else (None, "")
        offers = words.offers_in([t for ad in g["ads"] for t in ad["headlines"] + ad["descriptions"]]) if any(lp_read.page_id(u) == pid for ad in g["ads"] for u in ad["urls"]) else []
        out.append({
            "id": g["id"], "name": g["name"], "campaign": g["campaign"], "type": g["campaignType"], "cost": g["cost"], "clicks": g["clicks"], "conversions": g["conversions"],
            "headlines": heads[:SHOWN_HEADLINES], "bestHeadline": best, "echo": None if share is None else round(share, 2),
            "keywords": [{"text": k["text"], "match": k["match"], "cost": k["cost"], "clicks": k["clicks"], "qs": k["qs"], "landingPage": k["landingPage"],
                          "adRelevance": k["adRelevance"], "expectedCtr": k["expectedCtr"], "own": bool(k["url"]),
                          "on": words.hit(k["text"], prom, full) if readable else None} for k in g["keywords"][:SHOWN_KEYWORDS]],
            "terms": [{"text": t["text"], "clicks": t["clicks"], "cost": t["cost"], "conversions": t["conversions"], "on": words.hit(t["text"], prom, full) if readable else None}
                      for t in g["terms"][:SHOWN_TERMS]],
            "offers": [{"text": o["text"], "on": words.on_page(o, facts) if readable else None} for o in offers[:6]]})
    return out


def build_page(p, raw):
    """One page's report: its numbers, what was seen, the findings with their sections, the ad groups with what the page carries, the score and grade."""
    read = raw["read"]
    groups = [read["adGroups"][gid] for gid in p.get("adGroups", []) if gid in read["adGroups"]]
    fetch = raw["fetches"].get(p["id"])
    clicks = p["clicks"]
    rec = {"id": p["id"], "url": p["url"], "name": p["id"], "spend": p["cost"], "clicks": clicks, "conversions": p["conversions"], "impressions": p["impressions"],
           "devices": p["devices"], "speed": p["speed"], "fetch": fetch, "groups": groups, "lab": p.get("lab"), "ok": read["ok"]}
    findings = checks.run(rec)
    summary = model.page_summary(findings)
    summary["opens"] = not any(f["id"] == "reach_status" and f["result"] == "fail" for f in findings)
    if not summary["opens"]:                                  # nothing else about a page that does not open can be worth a score
        summary["score"], summary["grade"] = 0, "F"
    return {"id": p["id"], "url": p["url"], "name": rec["name"], "spend": p["cost"], "clicks": clicks, "conversions": p["conversions"], "impressions": p["impressions"],
            "value": p["value"], "convRate": round(100.0 * p["conversions"] / clicks, 2) if clicks else None,
            "cpa": round(p["cost"] / p["conversions"], 2) if p["conversions"] >= 1 else None, "devices": p["devices"], "speed": p["speed"], "lab": p.get("lab"),
            "checked": bool(fetch) and not fetch.get("skipped"), "seen": _seen(fetch), "summary": summary, "score": summary["score"], "grade": summary["grade"],
            "sections": [{"key": s["key"], "label": s["label"], "score": s["score"], "grade": s["grade"], "tally": s["tally"], "findings": [_prune(f) for f in s["findings"]]}
                         for s in model.sections_of(findings)],
            "adGroups": _group_rows(rec, groups), "groupCount": len(groups), "_findings": findings}


def _catalog():
    return {cid: {"section": e["section"], "title": e["title"], "severity": e["severity"], "why": e["why"], "fix": e["fix"]} for cid, e in model.CATALOG.items()}


def assemble(raw, now=None, labs=None):
    """The report from a raw read: every page's findings and score, the account's score, the fix-first list, what could not be read."""
    read = raw["read"]
    pages_out = []
    for p in read["pages"]:
        if labs and p["id"] in labs:
            p = dict(p, lab=labs[p["id"]])
        pages_out.append(build_page(p, raw))
    judged = [p for p in pages_out if p["checked"]]
    for p in pages_out:
        p["rated"] = p["checked"] and (p["summary"]["judged"] >= MIN_JUDGED or not p["summary"]["opens"])
    rated = [p for p in pages_out if p["rated"]]
    for_rank = [{"url": p["url"], "name": p["name"], "spend": p["spend"], "findings": p["_findings"]} for p in judged]
    fix = [dict(row, pages=[dict(x, items=x["items"][:FIX_ITEMS]) for x in row["pages"]]) for row in model.fix_first(for_rank)]
    for p in pages_out:
        p.pop("_findings")
    tally = {r: 0 for r in model.RESULTS}
    for p in judged:
        for r, n in p["summary"]["tally"].items():
            if r in tally:
                tally[r] += n
    score = model.weighted_score(rated)
    total_spend = read.get("pageCost") or sum(p["spend"] for p in pages_out)
    checked_spend = round(sum(p["spend"] for p in judged), 2)
    unverified = [p for p in pages_out if not p["rated"]]
    low = [p for p in rated if p["grade"] in ("D", "F") or p["summary"]["critical"]]
    sections = []
    for key, spec in sorted(model.SECTIONS.items(), key=lambda kv: kv[1]["order"]):
        rows = [{"score": next((s["score"] for s in p["sections"] if s["key"] == key), None), "spend": p["spend"]} for p in rated]
        s = model.weighted_score(rows)
        sections.append({"key": key, "label": spec["label"], "score": s, "grade": model.grade_of(s)})
    notes = list(NOTES)
    if read["pageCount"] > len(read["pages"]) or len(read["pages"]) > len(judged):
        notes.append("%d landing pages took money in the last 30 days. The %d that spent most were checked, %d%% of the spend." % (
            read["pageCount"], len(judged), round(100.0 * checked_spend / total_spend) if total_spend else 0))
    blocked = [p for p in pages_out if (p["seen"] or {}).get("blocked")]
    if blocked:
        notes.append("%d page%s refused our request, so %s checked only on what Google Ads says." % (len(blocked), "" if len(blocked) == 1 else "s", "it was" if len(blocked) == 1 else "they were"))
    caps = []
    if total_spend and model.share(sum(p["spend"] for p in unverified), total_spend) >= model.am.UNKNOWN_CAP:
        caps.append("pages that took %d%% of the spend could not be verified" % model.pct(sum(p["spend"] for p in unverified), total_spend))
    crit = sum(p["summary"]["critical"] for p in rated)
    if crit:
        caps.append("a critical check failed on %s" % model.plural(sum(1 for p in rated if p["summary"]["critical"]), "page"))
    return {
        "account": raw["account"], "window": "the last 30 days",
        "summary": {"score": score, "grade": model.grade_of(score, [{"result": "pass", "severity": "low", "caps": c} for c in caps]), "capReasons": caps, "pagesOfSpend": read["pageCount"], "checked": len(judged), "rated": len(rated), "spend": total_spend, "spendChecked": checked_spend,
                    "spendUnverified": round(sum(p["spend"] for p in unverified), 2), "spendAtRisk": round(sum(p["spend"] for p in low), 2), "pagesAtRisk": len(low), "tally": tally,
                    "critical": sum(p["summary"]["critical"] for p in judged), "high": sum(p["summary"]["high"] for p in judged),
                    "grades": {g: sum(1 for p in rated if p["grade"] == g) for g in ("A", "B", "C", "D", "F")}},
        "sections": sections, "pages": pages_out, "fixFirst": fix, "catalog": _catalog(), "warnings": read["warnings"], "notes": notes,
        "unread": ["Google Ads: " + k for k, v in read["ok"].items() if not v], "readAt": raw.get("readAt"),
        "generatedAt": (now or dt.datetime.now(dt.timezone.utc)).isoformat()}


def report_for(account, *, force=False, store=None, accounts_fn=None, search_fn=None, fetcher=None, now=None, max_pages=MAX_PAGES):
    """The report for the account `account` (see parse_account). `force` (Refresh) skips the saved read, at most once per paid_gads.REFRESH_COOLDOWN_S per
    account. Raises BadRequest for a bad or unknown account, paid_gads_segments.GadsError for a platform that would not answer."""
    from . import paid_store
    store = store or paid_store
    cid = parse_account(account)
    target = seg.resolve_target(cid, accounts_fn=accounts_fn, groups_fn=lambda: [])
    raw, old = _cached(target["id"], lambda: raw_read(target, search_fn=search_fn, fetcher=fetcher, now=now, max_pages=max_pages), force=force, store=store)
    out = assemble(raw, now=now, labs=lab_results(store, target["id"], raw))
    if old:
        out["stale"] = [{"platform": "google", "savedAt": old}]
    return out


def lab_results(store, account_id, raw):
    """The lab speed tests run for this account's pages: {page id: lab}. None are run yet, so this is empty until tracker/paid_lp_speed.py fills it."""
    return {}
