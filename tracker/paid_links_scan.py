"""Paid Marketing Agents: Unknown Domains Alert, the URL Checker's part of a scan.

It runs inside the scan (tracker/paid_domains_scan.py), on the final URLs the scan has just read, so it costs no extra Google Ads or Meta reads:

  1. work out which addresses are due (never checked, broken, or last checked a day ago) and check them, a few at a time, inside a time budget,
  2. ask again for every one that came back broken, once the pass is over, and keep it broken only if it fails both times,
  3. save the latest answer for each address, and turn the answers into findings (one per address in one place: new, back, fixed, dismissed),
  4. email one digest with a sheet of every broken link, and a note to Slack, about the findings it has not alerted before.

A finding is marked alerted only when a channel took the digest, so a digest that went nowhere is tried again at the next scan. Everything outside the
platform (the store, the fetch, the senders, the clock) is passed in, so this is tested without a network.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from . import paid_domains as dom
from . import paid_links as pl

CONFIRM_BUDGET_S = 3 * 60
PRUNE_AFTER_S = 30 * 24 * 3600
SHOWN_ROWS = 1000
ORDER = {"open": 0, "dismissed": 1, "fixed": 2}


def run_check(store, sightings, ok_ids, *, link, send, deliver, fetch, sleep=time.sleep, now_fn=None, on_progress=None, budget_s=pl.BUDGET_S):
    """Check the addresses in `sightings` (those of accounts that were read: `ok_ids`), keep the findings and alert about the new ones. Returns the
    summary kept with the scan: {urls, due, checked, skipped, blocked, unchecked, broken, redirects, new, reopened, fixed, alerted, email, slack}."""
    now = (now_fn or (lambda: datetime.now(timezone.utc)))()
    stamp, now_s = now.isoformat(), now.timestamp()
    occs = pl.occurrences(sightings)
    urls = sorted({o["url"] for o in occs})
    cache = store.urls_all()
    todo = pl.due(urls, cache, now_s)
    todo.sort(key=lambda u: 0 if (cache.get(u) or {}).get("verdict") == "broken" else 1)        # what is known broken is looked at first
    skipped = max(0, len(todo) - pl.MAX_URLS)
    todo = todo[:pl.MAX_URLS]
    first = pl.check_many(todo, fetch, sleep=sleep, budget_s=budget_s, on_done=(lambda n: on_progress(n, len(todo))) if on_progress else None)
    results = pl.confirm(first, fetch, sleep=sleep, budget_s=CONFIRM_BUDGET_S)
    rows = {u: dict(r, checkedAt=now_s) for u, r in results.items()}
    store.urls_put(rows)
    cache.update(rows)
    store.urls_prune(now_s - PRUNE_AFTER_S)

    existing = {f["fingerprint"]: f for f in store.links_all()}
    writes, counts = pl.apply(occs, cache, existing, ok_ids, stamp)
    store.links_put(writes)
    merged = dict(existing)
    merged.update({w["fingerprint"]: w for w in writes})
    open_rows = [f for f in merged.values() if f["status"] == "open"]
    pending = [f for f in open_rows if not f.get("alertedAt")]
    out = {"urls": len(urls), "due": len(todo) + skipped, "checked": len(results), "skipped": skipped + (len(todo) - len(first)),
           "blocked": sum(1 for u in urls if (cache.get(u) or {}).get("verdict") == "blocked"),
           "unchecked": sum(1 for u in urls if (cache.get(u) or {}).get("verdict") == "unchecked"),
           "broken": sum(1 for f in open_rows if f["kind"] == "broken"), "redirects": sum(1 for f in open_rows if f["kind"] != "broken"),
           "alerted": 0, "email": None, "slack": None, **counts}
    if pending:
        res = send(store, pl.digest(open_rows, pending, link), deliver)
        out.update(email=res["email"], slack=res["slack"])
        if res["sent"]:
            when = datetime.now(timezone.utc).isoformat() if now_fn is None else now_fn().isoformat()
            store.links_put([dict(f, alertedAt=when, alert={"email": res["email"]["status"], "slack": res["slack"]["status"]}) for f in pending])
            out["alerted"] = len(pending)
    return out


def decide(store, fingerprint, status, by, *, now=None):
    """Dismiss a broken-link or redirect finding, or open it again."""
    from .paid_domains_scan import BadRequest
    if status not in ("dismissed", "open"):
        raise BadRequest("Unknown action.")
    f = store.link_get(fingerprint)
    if not f:
        raise BadRequest("That link is not there.")
    store.links_put([dict(f, status=status, decidedBy=by, decidedAt=(now or datetime.now(timezone.utc)).isoformat())])


def snapshot(store):
    """{links, linkCounts} for the page: open ones first, then dismissed and fixed, the most recent first within each."""
    rows = store.links_all()
    counts = {s: sum(1 for f in rows if f["status"] == s) for s in ORDER}
    rows.sort(key=lambda f: (ORDER.get(f["status"], 9), f["accountName"].lower(), f["campaign"].lower(), f["url"]))
    return {"links": rows[:SHOWN_ROWS], "linkCounts": counts}


def open_sheet(store):
    """The Excel sheet of every open broken link, as bytes."""
    return pl.sheet([f for f in store.links_all() if f["status"] == "open"])
