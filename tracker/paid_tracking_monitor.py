"""Paid Marketing Agents: Tracking Data Health, the daily run over every account (no network of its own: the reads are passed in).

Every Google Ads and Meta ad account is read once and judged exactly as the page judges it (tracker/paid_tracking.py, paid_tracking_math.py). What the monitor engine
(tracker/paid_monitor.py) is given are issues, one per finding, with the fingerprint tracking:<account>:<the finding's key> (the rule, and the action, list or pixel), so conversions that stay
stopped are one issue alerted once, a stop that turns into a drop is the same account's other rule, and tracking that comes back resolves it. Critical and high findings are alerted at once and
reminded weekly while open; medium ones are alerted once; low ones (a shrinking list, an expected drop, a finding the team acknowledged) stay on the page and in the weekly summary.

The day's figures and every list's size are kept (pm_mon_snaps): the list rules read those sizes on later days. An account that cannot be read is listed as unreadable, so nothing open in it is
resolved by a read that could not see.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import paid_gads_segments as gads
from . import paid_monitor as mon
from . import paid_tracking as tr
from . import paid_tracking_math as math
from . import paid_tracking_settings as st

AGENT = tr.AGENT
LABEL = "Tracking Data Health"
PAGE = "/p2/paid-marketing/tracking-health"
WORKERS = 4


def fingerprint(account_id, key):
    return "tracking:%s:%s" % (account_id, key)


def issues_of(account, analysed):
    """[issue] for one analysed account. `account` is {id, name, platform}."""
    out = []
    owner = analysed.get("owner") or ""
    for f in analysed["findings"]:
        if f["level"] == "info":
            continue
        detail = f["detail"]
        if f.get("expected"):
            detail += " The team expected a drop on these days (%s), so it is not alerted." % f["expected"]
        if f.get("acked"):
            detail += " Acknowledged by %s: %s" % (f["acked"].get("by") or "the team", f["acked"].get("note") or "")
        if f["evidence"]:
            detail += " " + " ".join(f["evidence"])
        nxt = "Start with: %s" % f["fix"] + ((" Owner: %s." % owner) if owner else "")
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], f["key"]), account, f["kind"], f["level"], f["title"], detail, nxt,
                                  {"key": f["key"], "baseLevel": f["baseLevel"], "expected": f.get("expected") or "", "acked": bool(f.get("acked")), "score": analysed["score"]}))
    return out


def make_spec(*, today=None, search_fn=None, list_fn=None, mon_store=None, meta_pages_fn=None, meta_account_fn=None, now_fn=None, workers=WORKERS):
    """The spec run_monitor wants. `mon_store` is the monitors' store what the team saved and the kept list sizes are read from. `today` is accepted like the other monitors' (the days are
    counted from the clock, in each account's own time zone)."""
    now_fn = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))

    def collect(accounts, groups):
        listed = {a["id"]: a for a in (list_fn or gads.list_accounts)()}
        try:
            settings = st.load(mon_store) if mon_store is not None else st.empty()
        except Exception:                                                            # noqa: BLE001 (nothing saved is not a reason to skip the run)
            settings = st.empty()
        quota = {"hit": False}

        def one(a):
            meta = a.get("platform") == "meta"
            if quota["hit"] and not meta:
                return a, None, None, "The Google Ads daily read budget was used up earlier in this run."
            at = now_fn()
            try:
                if meta:
                    raw = tr.meta_raw(a["id"], pages_fn=meta_pages_fn, account_fn=meta_account_fn or (lambda act: {"name": a["name"], "currency": "USD"}), now=at)
                else:
                    raw = tr.google_raw({"id": a["id"], "name": a["name"], "currency": (listed.get(a["id"]) or {}).get("currency") or "USD"}, search_fn=search_fn, now=at)
                acc = raw["accounts"][0]
                return a, tr.judge(acc, settings=settings, mon=mon_store, now=at), acc, None
            except gads.GadsError as e:
                if getattr(e, "kind", "") == "quota":
                    quota["hit"] = True
                return a, None, None, str(e)[:240] or type(e).__name__
            except Exception as e:                                                   # noqa: BLE001 (Meta has its own errors; one account failing never stops the rest)
                return a, None, None, ("%s: %s" % (type(e).__name__, e))[:240]
        issues, snaps, ok, unreadable = [], {}, set(), []
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for a, res, acc, err in pool.map(one, accounts):
                if res is None:
                    unreadable.append({"accountId": a["id"], "name": a["name"], "platform": a.get("platform") or "google", "error": err})
                    continue
                ok.add(a["id"])
                issues += issues_of({"id": a["id"], "name": a["name"], "platform": a.get("platform") or "google"}, res)
                snaps[a["id"]] = math.snapshot_of(res, acc)
        return {"issues": issues, "snaps": snaps, "okIds": ok, "unreadable": unreadable}
    return {"agent": AGENT, "label": LABEL, "collect": collect}
