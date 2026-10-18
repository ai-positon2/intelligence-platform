"""Paid Marketing Agents: Landing Page & Relevancy Check, the report.

Reads a Google Ads account's landing pages (tracker/paid_lp_read.py), a Meta ad account's (tracker/paid_lp_meta_read.py) or one of each, fetches the busiest of
them (tracker/paid_lp_page.py), holds each page against the checklist (tracker/paid_lp_checks.py) and returns a score for every page and for the account, the
checks to put right first, and, for each page, the keywords, search terms and ad headlines with whether the page carries them. A page both platforms send people
to is one page: it is fetched once, its numbers are added and it is held to every platform's checks.

The account is named the way Account Audit names it: a 10-digit Google Ads customer id, a Meta ad account id (act_...) or both joined by "+". An MCC is not checked (a check is of one account). What is read and fetched is saved in the shared
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
import threading

from . import paid_gads as base
from . import paid_gads_segments as gads
from . import paid_lp_ai as lp_ai
from . import paid_lp_checks as checks
from . import paid_lp_match as words
from . import paid_lp_merge as lp_merge
from . import paid_lp_meta_read as lp_meta
from . import paid_lp_model as model
from . import paid_lp_page as lp_page
from . import paid_lp_read as lp_read
from . import paid_lp_speed as lp_speed
from . import paid_segments as seg
from .paid_accounts import NAMES, targets as _targets_of

BadRequest = seg.BadRequest

log = logging.getLogger(__name__)

MAX_PAGES = 40                                     # fetched and checked per account, the ones that spent most
MIN_JUDGED = 10                                    # checks a page needs judged (not "could not check") before it counts towards the account's score
CACHE_VERSION = "3"                                # bump when the shape of a saved read changes
TTL_S = 3600
PARTIAL_TTL_S = 300
SHOWN_GROUPS = 8                                   # ad groups shown per page
SHOWN_KEYWORDS = 12
SHOWN_TERMS = 8
SHOWN_HEADLINES = 6
FIX_ITEMS = 6                                      # affected things named under one page of the fix-first list
NOTES_META = [
    "A Meta ad set stands where a Google ad group stands. Meta has no keywords and no search terms, so a page Meta's ads send people to is held to what the ads say (the headline, "
    "the primary text and the description), to Meta's own ratings of the ads, and to what Meta's pixel needs to count the visit.",
    "Meta gives its quality and conversion rate ratings only to ads with enough impressions, and what Meta counts as a result depends on the campaign's objective (a lead, a purchase or an "
    "install), so a page that only traffic or awareness campaigns reach shows no conversions.",
    "The Meta pixel is looked for in the page's HTML as a browser first receives it. A pixel added later by a script or a tag manager, and the Conversions API, which runs on a server, cannot be seen from a page.",
]
NOTES = [
    "Pages are fetched from our servers and only their HTML is read, as a browser first receives it. Anything a script adds after load is not seen, so a page "
    "built by scripts is reported as such, not failed.",
    "A site that refuses automated requests (HTTP 401, 403 or 429) is reported as not verified, not as broken. Google's crawler and real visitors are normally let in.",
    "Server time is measured from our servers and includes redirects. Google's own mobile speed score and the optional lab test are shown beside the "
    "measured figures and never replace them.",
    "A page is its address without the tracking tags: the same host and path is one page, and so is the same page with or without www or https. A parameter that chooses "
    "which page a site shows (a location, a product, a post) makes another page.",
    "A keyword is on the page when every one of its words is (plurals and common endings count). Search terms and keywords are weighted by what they spent.",
]
FINDING_KEYS = ("id", "result", "severity", "found", "items", "count", "caps", "detail")


# ── Reading ─────────────────────────────────────────────────────────────────

def _read_google(target, *, search_fn, keep):
    return lp_read.read_account(target["id"], search_fn=search_fn, keep=keep)


def _read_meta(target, *, store, pages_fn, now, keep):
    from . import paid_meta
    pages_fn = pages_fn or (lambda path, params: paid_meta._pages(path, params, store))
    try:
        return lp_meta.read_account(target["id"], pages=pages_fn, now=now, keep=keep)
    except paid_meta.AdsError as e:
        raise gads.translate(e) from e


def raw_read(targets, *, search_fn=None, fetcher=None, now=None, max_pages=MAX_PAGES, store=None, meta_pages=None):
    """The raw read of one account: each platform's side, put together (tracker/paid_lp_merge.py), and the busiest `max_pages` pages fetched. `targets` is [{platform, id, name,
    currency}], one per platform. Reading a platform's landing pages failing is raised when it is the only platform; when there are two, the one that could not be read is a
    warning ("failed" in the read says which and why) and the report is the other's alone. Both failing raises the first error."""
    both = len(targets) > 1
    keep = lp_read.KEEP_PAGES * 2 if both else lp_read.KEEP_PAGES              # a page's other platform may be what puts it among the busiest
    reads, failed = {}, []
    for t in targets:
        try:
            if t["platform"] == "meta":
                reads["meta"] = _read_meta(t, store=store, pages_fn=meta_pages, now=now, keep=keep)
            else:
                reads["google"] = _read_google(t, search_fn=search_fn, keep=keep)
        except gads.GadsError as e:
            failed.append((t, e))
    if not reads:
        raise failed[0][1]
    read = lp_merge.merge(reads.get("google"), reads.get("meta"))
    read["failed"] = [{"platform": t["platform"], "kind": e.kind, "error": str(e)} for t, e in failed]
    for t, e in failed:
        read["warnings"] = list(read["warnings"]) + ["%s could not be read (%s). This report is %s alone." % (NAMES[t["platform"]], e, NAMES[next(iter(reads))])]
    chosen = read["pages"][:max_pages]
    reachable = [p for p in chosen if not p.get("templated")]
    records = lp_page.fetch_many([p["url"] for p in reachable], fetcher) if reachable else []
    fetches = {p["id"]: rec for p, rec in zip(reachable, records)}
    for p in chosen:
        if p.get("templated"):                                # the platform fills the host or path in at click time: the page people reach is not known, so none is fetched
            fetches[p["id"]] = {"url": p["url"], "status": 0, "error": "", "templated": True, "skipped": False, "blocked": False, "isHtml": False, "facts": None, "tech": None}
    accounts = [{"platform": t["platform"], "id": t["id"], "name": t["name"], "currency": t.get("currency") or ""} for t in targets]
    return {"read": read, "fetches": fetches, "accounts": accounts,
            "account": {"id": "+".join(a["id"] for a in accounts), "name": " + ".join(dict.fromkeys(a["name"] for a in accounts)), "currency": accounts[0]["currency"]},
            "readAt": (now or dt.datetime.now(dt.timezone.utc)).isoformat()}


_LOCKS, _LOCKS_GUARD = {}, threading.Lock()


def cache_key(account_id):
    return base.CACHE_V + "lp%s:%s" % (CACHE_VERSION, account_id)


def _partial(raw):
    """True when something in the read is missing: a part that failed, a platform that could not be read or a page that was not read in time."""
    return bool(raw["read"].get("warnings")) or any(f.get("skipped") for f in raw["fetches"].values())


def _quota_failed(raw):
    """True when a platform of the read was not read because its allowance is spent."""
    return any(f.get("kind") == "quota" for f in raw["read"].get("failed") or [])


def _usable(hit):
    raw, saved = hit
    return not (_partial(raw) and (saved is None or base._age_s(saved) > PARTIAL_TTL_S))


def _cached(key_id, build, *, force, store):
    """(raw read, saved-at or None). Two people asking at once share one read; with `force` the saved read is skipped (at most once per
    paid_gads.REFRESH_COOLDOWN_S per account); when a platform's allowance is spent the last saved read of any age is served with its date, and one that was whole is
    preferred to a fresh read that has lost a platform to the allowance."""
    key = cache_key(key_id)
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.Lock())
    if force:
        base.refresh_guard(store, "lp:%s" % key_id)
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
        if _quota_failed(raw):
            old = base._saved_copy(store, key)
            if old is not None and not _quota_failed(old[0]):
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
             "templated": bool(fetch.get("templated")), "finalUrl": fetch.get("finalUrl") or "", "redirected": bool(fetch.get("redirected")), "responseMs": fetch.get("responseMs"), "ms": fetch.get("ms"),
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
            "id": g["id"], "name": g["name"], "campaign": g["campaign"], "type": g["campaignType"], "platform": g.get("platform", "google"), "cost": g["cost"], "clicks": g["clicks"], "conversions": g["conversions"],
            "headlines": heads[:SHOWN_HEADLINES], "bestHeadline": best, "echo": None if share is None else round(share, 2),
            "keywords": [{"text": k["text"], "match": k["match"], "cost": k["cost"], "clicks": k["clicks"], "qs": k["qs"], "landingPage": k["landingPage"],
                          "adRelevance": k["adRelevance"], "expectedCtr": k["expectedCtr"], "own": bool(k["url"]),
                          "on": words.hit(k["text"], prom, full) if readable else None} for k in g["keywords"][:SHOWN_KEYWORDS]],
            "terms": [{"text": t["text"], "clicks": t["clicks"], "cost": t["cost"], "conversions": t["conversions"], "on": words.hit(t["text"], prom, full) if readable else None}
                      for t in g["terms"][:SHOWN_TERMS]],
            "offers": [{"text": o["text"], "on": words.on_page(o, facts) if readable else None} for o in offers[:6]]})
        if g.get("platform") == "meta":                                   # a Meta ad is what the ratings are about, so the ads are listed
            out[-1]["ads"] = [{"name": ad.get("name") or "", "cost": ad["cost"], "headlines": ad["headlines"][:2], "ranks": ad.get("ranks") or {}} for ad in g["ads"]]
    return out


def build_page(p, raw):
    """One page's report: its numbers, what was seen, the findings with their sections, the ad groups with what the page carries, the score and grade."""
    read = raw["read"]
    groups = [read["adGroups"][gid] for gid in p.get("adGroups", []) if gid in read["adGroups"]]
    fetch = raw["fetches"].get(p["id"])
    clicks = p["clicks"]
    rec = {"id": p["id"], "url": p["url"], "name": p.get("name") or p["id"], "spend": p["cost"], "clicks": clicks, "conversions": p["conversions"], "impressions": p["impressions"],
           "devices": p["devices"], "speed": p["speed"], "fetch": fetch, "groups": groups, "lab": p.get("lab"), "ok": read["ok"], "platforms": p.get("platforms") or ["google"]}
    findings = checks.run(rec)
    summary = model.page_summary(findings)
    summary["opens"] = not any(f["id"] == "reach_status" and f["result"] == "fail" for f in findings)
    if not summary["opens"]:                                  # nothing else about a page that does not open can be worth a score
        summary["score"], summary["grade"] = 0, "F"
    return {"id": p["id"], "url": p["url"], "name": rec["name"], "platforms": rec["platforms"], "spend": p["cost"], "clicks": clicks, "conversions": p["conversions"], "impressions": p["impressions"],
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
    platforms = read.get("platforms") or ["google"]
    notes = list(NOTES) if "google" in platforms else NOTES[:-1]                 # the last note is about keywords, which only Google has
    if "meta" in platforms:
        notes += NOTES_META
    if read["pageCount"] > len(read["pages"]) or len(read["pages"]) > len(judged):
        notes.append("%d landing pages took money in the last 30 days. The %d that spent most were checked, %d%% of the spend." % (
            read["pageCount"], len(judged), round(100.0 * checked_spend / total_spend) if total_spend else 0))
    unknown_address = [p for p in pages_out if (p["seen"] or {}).get("templated")]
    if unknown_address:
        who = "Google" if platforms == ["google"] else "the ad platform"
        notes.append("%d page%s send%s people to an address %s fills in when someone clicks (a {placeholder} in the host or path), so the page they reach is not known here and %s not fetched. "
                     "Their spend is counted as not verified." % (len(unknown_address), "" if len(unknown_address) == 1 else "s", "s" if len(unknown_address) == 1 else "", who, "it was" if len(unknown_address) == 1 else "they were"))
    blocked = [p for p in pages_out if (p["seen"] or {}).get("blocked")]
    if blocked:
        says = " and ".join(NAMES[x] for x in platforms) + (" says" if len(platforms) == 1 else " say")
        notes.append("%d page%s refused our request, so %s checked only on what %s." % (len(blocked), "" if len(blocked) == 1 else "s", "it was" if len(blocked) == 1 else "they were", says))
    off = read.get("off") or {}
    if off.get("spend"):
        kinds = ", ".join(k["kind"] for k in off["kinds"])
        notes.append("Meta spent %s of its %s on ads that send people to no web page of the client's (%s), so there is no page to check for them." % (_money(off["spend"]), _money(off["total"]), kinds))
    caps = []
    if total_spend and model.share(sum(p["spend"] for p in unverified), total_spend) >= model.am.UNKNOWN_CAP:
        caps.append("pages that took %d%% of the spend could not be verified" % model.pct(sum(p["spend"] for p in unverified), total_spend))
    crit = sum(p["summary"]["critical"] for p in rated)
    if crit:
        caps.append("a critical check failed on %s" % model.plural(sum(1 for p in rated if p["summary"]["critical"]), "page"))
    return {
        "account": raw["account"], "accounts": raw.get("accounts") or [], "window": "the last 30 days",
        "summary": {"platforms": platforms, "score": score, "grade": model.grade_of(score, [{"result": "pass", "severity": "low", "caps": c} for c in caps]), "capReasons": caps, "pagesOfSpend": read["pageCount"], "checked": len(judged), "rated": len(rated), "spend": total_spend, "spendChecked": checked_spend,
                    "spendUnverified": round(sum(p["spend"] for p in unverified), 2), "spendAtRisk": round(sum(p["spend"] for p in low), 2), "pagesAtRisk": len(low), "tally": tally,
                    "critical": sum(p["summary"]["critical"] for p in judged), "high": sum(p["summary"]["high"] for p in judged),
                    "grades": {g: sum(1 for p in rated if p["grade"] == g) for g in ("A", "B", "C", "D", "F")}},
        "sections": sections, "pages": pages_out, "fixFirst": fix, "catalog": _catalog(), "warnings": read["warnings"], "notes": notes,
        "unread": _unread(read), "readAt": raw.get("readAt"),
        "generatedAt": (now or dt.datetime.now(dt.timezone.utc)).isoformat()}


def _money(x):
    return "${:,.0f}".format(x)


def _unread(read):
    """The parts of the read that could not be read, named by platform."""
    out = [(NAMES["meta"] + ": " + k[5:]) if k.startswith("meta:") else (NAMES["google"] + ": " + k) for k, v in read["ok"].items() if not v]
    return out + ["%s: the whole account" % NAMES[f["platform"]] for f in read.get("failed") or []]


def _raw_for(account, *, force, store, accounts_fn, search_fn, fetcher, now, max_pages, meta_pages):
    """(account key, raw read, saved-at of an old copy or None)."""
    key, targets = _targets_of(account, store=store, accounts_fn=accounts_fn)
    raw, old = _cached(key, lambda: raw_read(targets, search_fn=search_fn, fetcher=fetcher, now=now, max_pages=max_pages, store=store, meta_pages=meta_pages),
                       force=force, store=store)
    return key, raw, old


def report_for(account, *, force=False, store=None, accounts_fn=None, search_fn=None, fetcher=None, now=None, max_pages=MAX_PAGES, meta_pages=None):
    """The report for the account `account` (see parse_account). `force` (Refresh) skips the saved read, at most once per paid_gads.REFRESH_COOLDOWN_S per
    account. Raises BadRequest for a bad or unknown account, paid_gads_segments.GadsError for a platform that would not answer (with two platforms, one that would not
    is a warning and the report is the other's)."""
    from . import paid_store
    store = store or paid_store
    key, raw, old = _raw_for(account, force=force, store=store, accounts_fn=accounts_fn, search_fn=search_fn, fetcher=fetcher, now=now, max_pages=max_pages, meta_pages=meta_pages)
    out = assemble(raw, now=now, labs=lab_results(store, key, raw))
    out["lab"] = {"configured": lp_speed.configured()}
    if old:
        out["stale"] = [{"platform": a["platform"], "savedAt": old} for a in raw["accounts"]]
    return out


def lab_results(store, account_id, raw):
    """The lab speed tests run for this account's pages in the last three days: {page id: result}."""
    ids = {p["id"] for p in raw["read"]["pages"]}
    return {pid: r for pid, r in lp_speed.saved(store, account_id).items() if pid in ids}


def run_lab(account, page_id, *, store=None, accounts_fn=None, search_fn=None, fetcher=None, get=None, now=None, meta_pages=None):
    """Runs the lab speed test (tracker/paid_lp_speed.py) for one page of the account's report and keeps the result. The page must be one the report
    names: the test is never run for an address the caller supplies. Raises BadRequest for an account or page that is not one, LabError for a test that could not run."""
    from . import paid_store
    store = store or paid_store
    key, raw, _old = _raw_for(account, force=False, store=store, accounts_fn=accounts_fn, search_fn=search_fn, fetcher=fetcher, now=now, max_pages=MAX_PAGES, meta_pages=meta_pages)
    page = next((p for p in raw["read"]["pages"] if p["id"] == str(page_id or "")), None)
    if page is None:
        raise BadRequest("That page is not one of this account's landing pages.")
    result = lp_speed.run(page["url"], get=get)
    return lp_speed.remember(store, key, page["id"], result, now=now)


def explain(account, page_id, *, store=None, accounts_fn=None, search_fn=None, fetcher=None, call=None, now=None, meta_pages=None):
    """The model's read of one page of the report (tracker/paid_lp_ai.py): did the page keep the ads' promise, what is missing, what to change. {..., source: "model" or
    "rules"}. Raises BadRequest for an account or page that is not one, or a page there is nothing to hold against (it was not fetched, is built by scripts, or no Search ad
    group or Meta ad set with words sends people to it)."""
    from . import paid_store
    store = store or paid_store
    key, raw, _old = _raw_for(account, force=False, store=store, accounts_fn=accounts_fn, search_fn=search_fn, fetcher=fetcher, now=now, max_pages=MAX_PAGES, meta_pages=meta_pages)
    p = next((x for x in raw["read"]["pages"] if x["id"] == str(page_id or "")), None)
    if p is None:
        raise BadRequest("That page is not one of this account's landing pages.")
    built = build_page(p, raw)
    findings = [dict(f, fix=model.CATALOG[f["id"]]["fix"]) for sec in built["sections"] for f in sec["findings"]]
    findings.sort(key=lambda f: (("fail", "warn").index(f["result"]) if f["result"] in ("fail", "warn") else 2, model.SEVERITIES.index(f.get("severity") or model.CATALOG[f["id"]]["severity"])))
    out = lp_ai.cached_read((key, p["id"]), raw.get("readAt"), raw, p["id"], findings, now=None, call=call)
    if out is None:
        raise BadRequest("This page cannot be read like a person yet: it was not fetched, it is built by scripts, or no Search ad group or Meta ad set with words sends people to it.")
    return out
