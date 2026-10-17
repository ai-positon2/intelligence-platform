"""Paid Marketing Agents: Account Audit, what a check is and how the results are scored.

Every check the audit runs has an id and a catalog entry (its section, title, severity, why it matters and what to do). A check
function only decides a result and says what it found; `make` joins the two into a finding. Keeping the wording in one catalog means
the same check always reads the same, and a test can hold every id a check function produces against the catalog.

Results:
    pass      the account does what it should
    warn      partly there, or there is a risk worth a look (half marks)
    fail      the account does not do what it should
    na        the check does not apply to this account (it has no shopping campaigns, so no Merchant Center link is needed)
    unknown   the platform would not say, so the audit does not guess (never counted as a pass or as a failure)

Scoring is plain arithmetic: a pass earns the check's weight, a warn half of it, a fail nothing; na and unknown are left out of the
denominator and counted so the report can say how much of the account it could not look at. A failed critical check keeps the grade
from rising above a C however much else is right, because one broken tracking or targeting setting can make every other number
untrustworthy.
"""
from __future__ import annotations

SEVERITIES = ("critical", "high", "medium", "low")
RESULTS = ("pass", "warn", "fail", "na", "unknown")
WEIGHT = {"critical": 8, "high": 4, "medium": 2, "low": 1}
CREDIT = {"pass": 1.0, "warn": 0.5, "fail": 0.0}
MAX_ITEMS = 12                                    # affected things named in one finding
GRADES = ((90, "A"), (75, "B"), (60, "C"), (40, "D"), (0, "F"))
CAP_GRADE = "C"                                   # the best grade an account with a failed critical check can have
PLATFORMS = ("google", "meta")

CATALOG: dict = {}                                # check id -> {section, platform, title, severity, why, fix}
SECTIONS: dict = {}                               # section key -> {label, platform, order}


class CatalogError(ValueError):
    pass


def section(key, label, platform, order):
    """Declares a section. Re-declaring the same section with the same values is allowed (modules are imported more than once in tests)."""
    if platform not in PLATFORMS:
        raise CatalogError("section %s: unknown platform %r" % (key, platform))
    spec = {"label": label, "platform": platform, "order": order}
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


def make(check_id, result, found, *, items=None, count=None, severity=None, detail=None):
    """One finding. `found` is the sentence that says what was seen, with its numbers. `items` names the affected things (campaigns,
    ad groups, keywords), capped; `count` is how many there really are when that is more than the items shown. `severity` overrides the
    catalog's for this finding (a failure that is costing a lot is more severe than the same failure on a quiet account)."""
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
    out = {"id": check_id, "section": spec["section"], "platform": SECTIONS[spec["section"]]["platform"], "title": spec["title"],
           "severity": sev, "result": result, "found": str(found or "").strip(), "why": spec["why"], "fix": spec["fix"],
           "items": shown, "count": total}
    if detail:
        out["detail"] = detail
    return out


def unknown(check_id, reason):
    """A check whose input could not be read. Says so; never passes and never fails."""
    return make(check_id, "unknown", "Could not be checked: %s" % str(reason).strip().rstrip("."))


def not_applicable(check_id, reason):
    return make(check_id, "na", str(reason).strip())


# ── Scoring ─────────────────────────────────────────────────────────────────

def _score(findings):
    earned = possible = 0
    for f in findings:
        credit = CREDIT.get(f["result"])
        if credit is None:
            continue
        w = WEIGHT[f["severity"]]
        possible += w
        earned += w * credit
    return None if not possible else round(100.0 * earned / possible)


def grade_of(score, findings=()):
    """A letter for a score, held to C when any finding is a failed critical check. None when nothing could be judged."""
    if score is None:
        return None
    letter = next(g for floor, g in GRADES if score >= floor)
    if letter in ("A", "B") and any(f["severity"] == "critical" and f["result"] == "fail" for f in findings):
        return CAP_GRADE
    return letter


def tally(findings):
    t = {r: 0 for r in RESULTS}
    for f in findings:
        t[f["result"]] += 1
    t["total"] = len(findings)
    return t


def sections_of(findings):
    """The findings grouped by section, in section order: [{key, label, platform, score, grade, tally, findings}]. A section no check
    ran in is left out."""
    by = {}
    for f in findings:
        by.setdefault(f["section"], []).append(f)
    out = []
    for key, spec in sorted(SECTIONS.items(), key=lambda kv: (kv[1]["platform"] != "google", kv[1]["order"])):
        fs = by.get(key)
        if not fs:
            continue
        fs = sorted(fs, key=_order)
        score = _score(fs)
        out.append({"key": key, "label": spec["label"], "platform": spec["platform"], "score": score, "grade": grade_of(score, fs),
                    "tally": tally(fs), "findings": fs})
    return out


def _order(f):
    """Failed before warned before the rest, then the most severe first, then the one that touches most things."""
    return (("fail", "warn", "unknown", "pass", "na").index(f["result"]), SEVERITIES.index(f["severity"]), -f.get("count", 0), f["id"])


def fix_first(findings, limit=10):
    """The checks to put right first: failed and warned ones, most severe first, failures before warnings of the same severity."""
    bad = [f for f in findings if f["result"] in ("fail", "warn")]
    bad.sort(key=lambda f: (SEVERITIES.index(f["severity"]), ("fail", "warn").index(f["result"]), -f.get("count", 0), f["id"]))
    return bad[:limit]


def summarise(findings):
    """The numbers at the top of the report: overall score and grade, the tally, how much could not be checked, and per platform."""
    score = _score(findings)
    judged = sum(1 for f in findings if f["result"] in CREDIT)
    unread = sum(1 for f in findings if f["result"] == "unknown")
    out = {"score": score, "grade": grade_of(score, findings), "tally": tally(findings), "judged": judged, "unknown": unread,
           "unknownShare": round(100.0 * unread / (judged + unread)) if (judged + unread) else 0,
           "critical": sum(1 for f in findings if f["severity"] == "critical" and f["result"] == "fail"),
           "high": sum(1 for f in findings if f["severity"] == "high" and f["result"] == "fail")}
    for p in PLATFORMS:
        fs = [f for f in findings if f["platform"] == p]
        if fs:
            s = _score(fs)
            out.setdefault("platforms", {})[p] = {"score": s, "grade": grade_of(s, fs), "tally": tally(fs)}
    return out
