"""Paid Marketing Agents: Brand Spend Alert, the daily run over every account (no network of its own: the reads are passed in).

Every Google Ads account is read once (the day rows, the campaign list and, for an account with brand words saved, the search terms), analysed exactly as the page does it with what
the team saved, and turned into issues for the monitor engine (tracker/paid_monitor.py): one for each finding that is not just information, with a fingerprint that is the same
every day for the same problem in the same place. The engine says what is new, worse, still open or fixed and alerts; this file only says what an issue of this monitor is.
The day's figures of each account are kept too, for the history the page shows ("outside its band for 6 days in a row").
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import paid_brandspend as bs
from . import paid_brandspend_settings as st
from . import paid_gads_segments as gads
from . import paid_monitor as mon

AGENT = "brandspend"
LABEL = "Brand Spend Alert"
PAGE = "/p2/paid-marketing/brand-spend"
WORKERS = 4


def fingerprint(account_id, finding):
    """The same problem in the same place has the same fingerprint every day: the account, the kind and, where a kind has several, which one (the day of a spike, the campaign that is silent)."""
    return ":".join([account_id, finding["kind"]] + ([str(finding["key"])] if finding.get("key") else []))


def issues_of(account, analysed):
    """[issue] for one analysed account: every finding above information. `account` is {id, name, platform}."""
    out = []
    for f in analysed["findings"]:
        if f["level"] == "info":
            continue
        data = {"status": analysed["status"], "weekFrom": analysed["weekFrom"], "cost": analysed["brand"]["cost"], "usual": (analysed.get("expected") or {}).get("cost")}
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], f), account, f["kind"], f["level"], f["title"], f["detail"], f["nextStep"], data))
    return out


def analyse_account(member, today, *, search_fn, settings, mon_store, currency):
    """The analysed account the page would show for it, from a fresh read."""
    raw = bs.google_raw(dict(member, currency=currency), today, search_fn=search_fn)
    saved = st.account_of(settings, member["id"])
    leaks, warnings = ({}, [])
    if saved["brandTerms"]:
        leaks, warnings = bs.read_leaks([member], today, search_fn=search_fn)
    marks = {k: bool(v) for k, v in (settings.get("marks") or {}).items()}
    lim = settings.get("limit") or bs.m.DEFAULT_LIMIT
    rep = bs.assemble(raw, {"id": member["id"], "name": member["name"], "currency": currency}, today, lim, marks, settings=settings, leaks=leaks,
                      histories=bs.histories_for(mon_store, [member["id"]], today))
    return rep["accounts"][0], rep["warnings"] + warnings


def make_spec(*, today, search_fn=None, list_fn=None, settings=None, mon_store=None, workers=WORKERS):
    """The spec run_monitor wants. `settings` is what the team saved (paid_brandspend_settings.load) and `mon_store` the monitors' store the history is read from."""
    settings = settings or {}

    def collect(accounts, groups):
        listed = {a["id"]: a for a in (list_fn or gads.list_accounts)()}
        google = [a for a in accounts if a.get("platform", "google") == "google"]
        quota = {"hit": False}

        def one(a):
            if quota["hit"]:
                return a, None, "The Google Ads daily read budget was used up earlier in this run."
            try:
                got, warn = analyse_account(a, today, search_fn=search_fn, settings=settings, mon_store=mon_store, currency=(listed.get(a["id"]) or {}).get("currency") or "USD")
                return a, (got, warn), None
            except gads.GadsError as e:
                if getattr(e, "kind", "") == "quota":
                    quota["hit"] = True
                return a, None, str(e)[:240] or type(e).__name__
        issues, snaps, ok, unreadable = [], {}, set(), []
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for a, got, err in pool.map(one, google):
                if got is None:
                    unreadable.append({"accountId": a["id"], "name": a["name"], "platform": "google", "error": err})
                    continue
                analysed, _warn = got
                ok.add(a["id"])
                issues += issues_of({"id": a["id"], "name": a["name"], "platform": "google"}, analysed)
                snaps[a["id"]] = bs.snapshot_of(analysed)
        return {"issues": issues, "snaps": snaps, "okIds": ok, "unreadable": unreadable}
    return {"agent": AGENT, "label": LABEL, "collect": collect}
