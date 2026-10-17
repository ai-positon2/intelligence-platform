"""Paid Marketing Agents: Account Audit, the report.

Reads a Google Ads account, a Meta ad account or one of each, holds each against its checklist and returns the findings grouped by
section with a score for each section and for the account, and the checks to put right first. The reading is tracker/paid_audit_google_read.py,
tracker/paid_audit_pages.py (the landing pages) and tracker/paid_audit_meta_read.py; the checklists are tracker/paid_audit_google_checks.py
and tracker/paid_audit_meta_checks.py; how a finding and a score work is tracker/paid_audit_model.py.

The account is named the way Seasonality Analysis names it: a 10-digit Google Ads customer id, a Meta ad account id (act_...), or both
joined by "+". An MCC is not audited here (an audit is of one account). A read is saved in the shared Postgres cache (tracker/paid_gads.py
helpers): one read per account per hour, Refresh limited per account, a spent quota served from the last saved read of any age with its date.
A read with a part missing (a warning) is kept for only five minutes, so an account is not stuck on a failure for an hour after the platform
recovers. A part that could not be read is a warning and its checks say "unknown": the audit never reports a pass or a failure for something
it could not look at. Read only: nothing is changed in either platform.
"""
from __future__ import annotations

import datetime as dt
import logging
import re
import threading

from . import paid_audit_google_checks as gchecks
from . import paid_audit_google_read as gread
from . import paid_audit_meta_checks as mchecks
from . import paid_audit_meta_read as mread
from . import paid_audit_model as model
from . import paid_audit_pages as pages
from . import paid_gads as base
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


def account_key(raw):
    """The one string a saved audit is filed under for this account: "<google id>", "act_..." or "<google id>+<act_...>"."""
    ids = parse_account(raw)
    return "+".join(x for x in (ids["google"], ids["meta"]) if x)


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


CACHE_VERSION = "1"                 # bump when the shape of a saved read changes, so an older copy is never read as a newer one
TTL_S = 3600
PARTIAL_TTL_S = 300                 # a read with a part missing is worth keeping for minutes, not an hour
_LOCKS, _LOCKS_GUARD = {}, threading.Lock()


def cache_key(kind, key_id):
    return base.CACHE_V + "audit%s:%s:%s" % (CACHE_VERSION, kind, key_id)


def _usable(hit):
    """A saved read is used while it is fresh; one with a part missing only for PARTIAL_TTL_S."""
    raw, saved = hit
    return not (raw.get("warnings") and (saved is None or base._age_s(saved) > PARTIAL_TTL_S))


def _cached(kind, key_id, build, *, force, store):
    """(raw read, saved-at or None). Two people asking at once share one read; with `force` the saved read is skipped (at most once per
    paid_gads.REFRESH_COOLDOWN_S per account); when the platform's allowance is spent the last saved read of any age is served with its date."""
    key = cache_key(kind, key_id)
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.Lock())
    if force:
        base.refresh_guard(store, "audit:%s:%s" % (kind, key_id))
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
        except Exception as err:                          # a failed save never costs the report
            log.warning("account audit: could not save the read (%s)", err)
        base._mem_put(key, raw, saved)
        return raw, None


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
               now=None, check_pages=True):
    """The audit of the account named by `account` (see parse_account). `force` (Refresh) skips the saved reads, at most once per
    paid_gads.REFRESH_COOLDOWN_S per account. Raises BadRequest for a bad or unknown account, paid_gads_segments.GadsError for a platform
    that would not answer."""
    from . import paid_meta, paid_store
    store = store or paid_store
    ids = parse_account(account)
    accounts, google, meta, stale = [], None, None, []
    if ids["google"]:
        target = _google_account(ids["google"], accounts_fn)
        accounts.append({"platform": "google", "id": target["id"], "name": target["name"], "currency": target.get("currency") or ""})
        google, old = _cached("audit-google", target["id"], lambda: google_raw(target, search_fn=search_fn, fetcher=fetcher, now=now, check_pages=check_pages),
                              force=force, store=store)
        if old:
            stale.append({"platform": "google", "savedAt": old})
    if ids["meta"]:
        target = _meta_account(ids["meta"], store)
        accounts.append({"platform": "meta", "id": target["id"], "name": target["name"], "currency": target.get("currency") or ""})
        meta, old = _cached("audit-meta", target["id"], lambda: meta_raw(target, store=store, get=meta_get, pages_fn=meta_pages, now=now),
                            force=force, store=store)
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
