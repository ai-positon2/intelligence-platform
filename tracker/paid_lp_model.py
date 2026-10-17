"""Paid Marketing Agents: Landing Page & Relevancy Check, what a check is and how a page is scored.

The same idea and the same arithmetic as the Account Audit (tracker/paid_audit_model.py, whose scoring this uses): every check has an id and
a catalog entry (its section, title, severity, why it matters and what to do), a check function only decides a result and says what it
found, and `make` joins the two into a finding. A result is pass, warn, fail, na (does not apply to this page) or unknown (the page or Google
would not say, so nothing is guessed). Not applicable and unknown are left out of the score and counted.

What differs is the catalog (its own, so the audit's checks and these never mix) and that the checks are run once per landing page, so a page has a
score and a grade, and the account's score is the pages' scores weighted by what each page spent.
"""
from __future__ import annotations

from . import paid_audit_model as am

SEVERITIES = am.SEVERITIES
RESULTS = am.RESULTS
CATALOG: dict = {}                                # check id -> {section, title, severity, why, fix}
SECTIONS: dict = {}                               # section key -> {label, order}
MAX_ITEMS = am.MAX_ITEMS


class CatalogError(ValueError):
    pass


def section(key, label, order):
    """Declares a section. Declaring it again with the same values is allowed (modules are imported more than once in tests)."""
    spec = {"label": label, "order": order}
    if key in SECTIONS and SECTIONS[key] != spec:
        raise CatalogError("section %s declared twice with different values" % key)
    SECTIONS[key] = spec


def register(entries):
    """Adds catalog entries: a list of dicts with id, section, title, severity, why, fix. A broken entry is a programming error."""
    for e in entries:
        missing = [k for k in ("id", "section", "title", "severity", "why", "fix") if not str(e.get(k) or "").strip()]
        if missing:
            raise CatalogError("check %s: missing %s" % (e.get("id"), ", ".join(missing)))
        if e["severity"] not in SEVERITIES:
            raise CatalogError("check %s: unknown severity %r" % (e["id"], e["severity"]))
        if e["section"] not in SECTIONS:
            raise CatalogError("check %s: unknown section %r" % (e["id"], e["section"]))
        if e["id"] in CATALOG and CATALOG[e["id"]] != e:
            raise CatalogError("check %s declared twice with different values" % e["id"])
        CATALOG[e["id"]] = dict(e)


def make(check_id, result, found, *, items=None, count=None, severity=None, detail=None, caps=None):
    """One finding. `found` is the sentence that says what was seen, with its numbers. `items` names the affected things (keywords, links),
    capped; `count` is how many there really are when that is more than the items shown. `severity` overrides the catalog's for this finding.
    `caps` is a short reason that holds the page's grade at C or below for as long as the finding stands."""
    spec = CATALOG.get(check_id)
    if spec is None:
        raise CatalogError("unknown check %r" % check_id)
    if result not in RESULTS:
        raise CatalogError("check %s: unknown result %r" % (check_id, result))
    sev = severity or spec["severity"]
    if sev not in SEVERITIES:
        raise CatalogError("check %s: unknown severity %r" % (check_id, sev))
    items = list(items or [])
    shown = items[:MAX_ITEMS]
    total = max(len(items), int(count)) if count is not None else len(items)
    out = {"id": check_id, "section": spec["section"], "title": spec["title"], "severity": sev, "result": result,
           "found": str(found or "").strip(), "why": spec["why"], "fix": spec["fix"], "items": shown, "count": total}
    if detail:
        out["detail"] = detail
    if caps:
        out["caps"] = str(caps)
    return out


def unknown(check_id, reason):
    """A check whose input could not be read. Says so; never passes and never fails."""
    return make(check_id, "unknown", "Could not be checked: %s" % str(reason).strip().rstrip("."))


def not_applicable(check_id, reason):
    return make(check_id, "na", str(reason).strip())


# ── A page's results ────────────────────────────────────────────────────────

def sections_of(findings):
    """The findings grouped by section, in section order: [{key, label, score, grade, tally, findings}]. A section no check ran in is left out."""
    by = {}
    for f in findings:
        by.setdefault(f["section"], []).append(f)
    out = []
    for key, spec in sorted(SECTIONS.items(), key=lambda kv: kv[1]["order"]):
        fs = by.get(key)
        if not fs:
            continue
        fs = sorted(fs, key=am._order)
        score = am._score(fs)
        out.append({"key": key, "label": spec["label"], "score": score, "grade": am.grade_of(score, fs), "tally": am.tally(fs), "findings": fs})
    return out


def page_summary(findings):
    """A page's score and grade, what held the grade down, and how many checks could not be read."""
    score = am._score(findings)
    judged = sum(1 for f in findings if f["result"] in am.CREDIT)
    unread = sum(1 for f in findings if f["result"] == "unknown")
    return {"score": score, "grade": am.grade_of(score, findings), "capReasons": am.cap_reasons(findings), "tally": am.tally(findings),
            "judged": judged, "unknown": unread,
            "critical": sum(1 for f in findings if f["severity"] == "critical" and f["result"] == "fail"),
            "high": sum(1 for f in findings if f["severity"] == "high" and f["result"] == "fail")}


# ── The account's ───────────────────────────────────────────────────────────

def weighted_score(pages):
    """The account's score: the pages' scores weighted by what each page spent (a page with no spend counts once, so an account of
    unspent pages still has a score). Pages with no score (nothing could be judged) are left out. None when no page has one."""
    scored = [(p["score"], max(float(p.get("spend") or 0.0), 0.0)) for p in pages if p.get("score") is not None]
    if not scored:
        return None
    total = sum(w for _s, w in scored)
    if total <= 0:
        return round(sum(s for s, _w in scored) / len(scored))
    return round(sum(s * w for s, w in scored) / total)


def fix_first(pages, limit=12):
    """The checks to put right first across the pages: failed and warned ones, ordered by severity, then failure before warning, then by what
    the page spent. Each row is the finding with the page it is on (`page`: url) and what that page spent (`spend`)."""
    rows = []
    for p in pages:
        for f in p.get("findings") or []:
            if f["result"] in ("fail", "warn"):
                rows.append(dict(f, page=p["url"], pageName=p.get("name") or p["url"], spend=p.get("spend") or 0.0))
    rows.sort(key=lambda f: (SEVERITIES.index(f["severity"]), ("fail", "warn").index(f["result"]), -f["spend"], f["id"], f["page"]))
    return rows[:limit]


# ── Wording and arithmetic the checklist uses (the audit's) ─────────────────

share = am.share
pct = am.pct
plural = am.plural
verb = am.verb
