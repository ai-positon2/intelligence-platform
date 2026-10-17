"""Paid Marketing Agents: Account Audit, the report.

Reads a Google Ads account, a Meta ad account or one of each, holds each against its checklist and returns the findings grouped by
section with a score for each section and for the account, and the checks to put right first. The reading is tracker/paid_audit_google_read.py,
tracker/paid_audit_pages.py (the landing pages) and tracker/paid_audit_meta_read.py; the checklists are tracker/paid_audit_google_checks.py
and tracker/paid_audit_meta_checks.py; how a finding and a score work is tracker/paid_audit_model.py.

The account is named the way Seasonality Analysis names it: a 10-digit Google Ads customer id, a Meta ad account id (act_...), or both
joined by "+". An MCC is not audited here (an audit is of one account). Reading is cached through the shared cache (tracker/paid_geo.py
_cached): one read per account per hour, Refresh limited per account, a spent quota served from the last saved read with its date. A part
that could not be read is a warning and its checks say "unknown": the audit never reports a pass or a failure for something it could not
look at. Read only: nothing is changed in either platform.
"""
from __future__ import annotations

import datetime as dt
import logging
import re

from . import paid_audit_google_checks as gchecks
from . import paid_audit_google_read as gread
from . import paid_audit_meta_checks as mchecks
from . import paid_audit_meta_read as mread
from . import paid_audit_model as model
from . import paid_audit_pages as pages
from . import paid_gads_segments as gads
from . import paid_segments as seg

BadRequest = seg.BadRequest

log = logging.getLogger(__name__)

ID_RE = re.compile(r"^\d{10}$")
COMBO_RE = re.compile(r"^(\d{10})\+(act_\d{4,20})$")
NOTES_GOOGLE = [
    "Google's API does not show account-level negative keywords or the Google Analytics 4 link. The audit says so where it matters and "
    "infers the GA4 link from the conversions imported from it.",
    "Landing pages are fetched from our servers. A site that refuses automated requests is reported as not verified, not as broken.",
]
NOTES_META = [
    "How Meta reports where events came from (the pixel or the Conversions API) is read as Meta labels it. If Meta uses a label the audit "
    "does not recognise as server-side, the Conversions API check says what it saw so a person can judge.",
]


def parse_account(raw):
    """{google: id or None, meta: act id or None} from "<google id>", "act_..." or "<google id>+<act_...>"."""
    raw = str(raw or "").strip()
    m = COMBO_RE.match(raw)
    if m:
        return {"google": m.group(1), "meta": m.group(2)}
    if ID_RE.match(raw):
        return {"google": raw, "meta": None}
    from . import paid_meta
    if paid_meta.is_meta(raw):
        return {"google": None, "meta": paid_meta.norm_id(raw)}
    raise BadRequest("account should be a 10-digit Google Ads customer id, a Meta ad account id (act_...) or both joined by +")


# ── Reading ─────────────────────────────────────────────────────────────────

def google_raw(account, *, search_fn=None, fetcher=None, now=None, check_pages=True):
    """The raw Google read of one account, with its landing pages checked: the saved shape. The account record failing is raised."""
    read = gread.read_account(account["id"], search_fn=search_fn)
    read["pages"] = None
    if check_pages and read["ads"] is not None and read["ads"]["urls"]["list"]:
        try:
            read["pages"] = pages.check(read["ads"]["urls"]["list"], fetcher=fetcher)
        except Exception:                           # a page check never costs the audit
            log.exception("account audit: the landing page check failed")
    elif check_pages and read["ads"] is not None:
        read["pages"] = {"checked": 0, "of": 0, "results": []}
    read["readAt"] = (now or dt.datetime.now(dt.timezone.utc)).isoformat()
    return read


def meta_raw(account, *, store, get=None, pages_fn=None, now=None):
    """The raw Meta read of one ad account."""
    from . import paid_meta
    get = get or (lambda path, params: paid_meta._get(path, params, store))
    pages_fn = pages_fn or (lambda path, params: paid_meta._pages(path, params, store))
    try:
        return mread.read_account(account["id"], get=get, pages=pages_fn, now=now)
    except paid_meta.AdsError as e:
        raise gads.translate(e) from e


def _cached(kind, key_id, build, *, force, store, now, day):
    from . import paid_geo
    return paid_geo._cached(kind, key_id, "audit", day, day, build, force=force, store=store, now=now)


# ── The report ──────────────────────────────────────────────────────────────

def assemble(google, meta, accounts, now=None):
    """The report from the raw reads (either may be None): findings by section, scores, the fix-first list and what could not be read."""
    findings = []
    notes, warnings, unread = [], [], []
    if google is not None:
        findings += gchecks.run(google)
        warnings += google["warnings"]
        notes += NOTES_GOOGLE
        unread += ["Google Ads: " + k for k, v in google["ok"].items() if not v]
    if meta is not None:
        findings += mchecks.run(meta)
        warnings += meta["warnings"]
        notes += NOTES_META
        unread += ["Meta: " + k for k, v in meta["ok"].items() if not v]
    summary = model.summarise(findings)
    reads = [r["readAt"] for r in (google, meta) if r is not None and r.get("readAt")]
    return {"accounts": accounts, "summary": summary, "sections": model.sections_of(findings), "fixFirst": model.fix_first(findings),
            "warnings": warnings, "unread": unread, "notes": notes, "readAt": min(reads) if reads else None,
            "generatedAt": (now or dt.datetime.now(dt.timezone.utc)).isoformat()}


def report_for(account, *, force=False, store=None, accounts_fn=None, search_fn=None, fetcher=None, meta_get=None, meta_pages=None,
               now=None, today=None, check_pages=True):
    """The audit of the account named by `account` (see parse_account). `force` (Refresh) skips the saved reads, at most once per
    paid_gads.REFRESH_COOLDOWN_S per account. Raises BadRequest for a bad or unknown account, paid_gads_segments.GadsError for a platform
    that would not answer."""
    from . import paid_meta, paid_store
    store = store or paid_store
    ids = parse_account(account)
    day = today or dt.date.today()
    clock = None
    accounts, google, meta, stale = [], None, None, []
    if ids["google"]:
        target = _google_account(ids["google"], accounts_fn)
        accounts.append({"platform": "google", "id": target["id"], "name": target["name"], "currency": target.get("currency") or ""})
        google, old = _cached("audit-google", target["id"], lambda: google_raw(target, search_fn=search_fn, fetcher=fetcher, now=now, check_pages=check_pages),
                              force=force, store=store, now=clock, day=day)
        if old:
            stale.append({"platform": "google", "savedAt": old})
    if ids["meta"]:
        target = _meta_account(ids["meta"], store)
        accounts.append({"platform": "meta", "id": target["id"], "name": target["name"], "currency": target.get("currency") or ""})
        meta, old = _cached("audit-meta", target["id"], lambda: meta_raw(target, store=store, get=meta_get, pages_fn=meta_pages, now=now),
                            force=force, store=store, now=clock, day=day)
        if old:
            stale.append({"platform": "meta", "savedAt": old})
    out = assemble(google, meta, accounts, now=now)
    if stale:
        out["stale"] = stale
    return out


def _google_account(cid, accounts_fn):
    return seg.resolve_target(cid, accounts_fn=accounts_fn, groups_fn=lambda: [])


def _meta_account(act, store):
    return seg._meta_target(act, store)
