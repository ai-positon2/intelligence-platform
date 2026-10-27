"""Paid Marketing Agents: Creative & Asset Health, the daily run over every account (no network of its own: the reads are passed in).

Every Google Ads and Meta ad account is read once and checked exactly as the page checks it. What the monitor engine (tracker/paid_monitor.py) is given are issues, and here an issue is one thing
a person can open and fix:

  * a high or medium finding: one issue each, fingerprint creative:<account>:<the finding's key> (an ad, an asset group, a campaign's missing sitelinks), so a creative that newly wears out
    is news, one that stays tired is alerted once (and again only if it gets worse) and one that recovers is resolved;
  * the low findings (a Low asset on an ad nobody sees, a small gap): one quiet issue per account, never alerted;
  * at most MAX_PER_ACCOUNT findings per account become issues (worst first); the rest are counted in one issue that says so.

A part of the read that failed for an account (Google's ratings, the asset coverage, Meta's weeks) is handed to the engine as a hold: the issues of that kind stay open instead of being resolved by
a read that could not see them. An account whose ads cannot be read at all is listed as unreadable.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import paid_creative as c
from . import paid_creative_math as math
from . import paid_gads_segments as gads
from . import paid_monitor as mon

AGENT = c.AGENT
LABEL = "Creative & Asset Health"
PAGE = "/p2/paid-marketing/creative-health"
WORKERS = 4
MAX_PER_ACCOUNT = 100


def fingerprint(account_id, key):
    return "creative:%s:%s" % (account_id, key)


def issues_of(account, analysed):
    """[issue] for one analysed account (see the module's docstring for what an issue is). `account` is {id, name, platform}."""
    out, small, worth = [], [], []
    for f in analysed["findings"]:
        (worth if f["level"] in ("high", "medium") else small).append(f)
    worth.sort(key=lambda f: (-math.LEVEL_RANK[f["level"]], math.ORDER.index(f["rule"]), f["key"]))
    for f in worth[:MAX_PER_ACCOUNT]:
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], f["key"]), account, f["rule"], f["level"], f["title"], f["detail"], "Start with: %s" % f["fix"],
                                  {"evidence": f["evidence"], "area": f["area"], "where": f.get("where") or {}}))
    if len(worth) > MAX_PER_ACCOUNT:
        n = len(worth) - MAX_PER_ACCOUNT
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], "more"), account, "more", "medium", "%d more creative %s" % (n, "problem" if n == 1 else "problems"),
                                  "Only the %d worst are followed one by one; the page lists every one." % MAX_PER_ACCOUNT, "Start with: Work through the findings on the Creative & Asset Health page.",
                                  {"findings": n}))
    if small:
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], "small"), account, "small", "low", "%s to check" % mon.plural(len(small), "small finding"),
                                  "; ".join(f["title"] for f in small)[:300] + ". None of it stops the ads.", "Open the Creative & Asset Health page and check it when there is time.",
                                  {"findings": len(small)}))
    return out


def make_spec(*, today=None, search_fn=None, list_fn=None, mon_store=None, meta_pages_fn=None, meta_account_fn=None, report_fn=None, meta_raw_fn=None, now_fn=None, workers=WORKERS):
    """The spec run_monitor wants. `report_fn(member)` is Asset Coverage's report for a Google account and `meta_raw_fn(act)` the shared Meta creative read (both default to the real ones). `today`
    is the day the weeks are counted from."""
    now_fn = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))

    def collect(accounts, groups):
        listed = {a["id"]: a for a in (list_fn or gads.list_accounts)()}
        quota = {"hit": False}
        day = today or now_fn().date()

        def one(a):
            meta = a.get("platform") == "meta"
            if quota["hit"] and not meta:
                return a, None, "The Google Ads daily read budget was used up earlier in this run."
            try:
                if meta:
                    raw = c.meta_raw(a["id"], pages_fn=meta_pages_fn or (lambda p, q: []), account_fn=meta_account_fn or (lambda act: {"name": a["name"], "currency": "USD"}), today=day,
                                     raw_fn=(lambda: meta_raw_fn(a["id"])) if meta_raw_fn else None)
                else:
                    member = {"id": a["id"], "name": a["name"], "currency": (listed.get(a["id"]) or {}).get("currency") or "USD"}
                    raw = c.google_raw(member, search_fn=search_fn, report_fn=report_fn, today=day)
                acct = raw["accounts"][0]
                return a, dict(math.analyse(acct, platform=acct.get("platform") or "google"), unread=acct.get("unread") or []), None
            except gads.GadsError as e:
                if getattr(e, "kind", "") == "quota":
                    quota["hit"] = True
                return a, None, str(e)[:240] or type(e).__name__
            except Exception as e:                                                   # noqa: BLE001 (Meta has its own errors; one account failing never stops the rest)
                return a, None, ("%s: %s" % (type(e).__name__, e))[:240]
        issues_out, ok, unreadable, hold = [], set(), [], {}
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for a, res, err in pool.map(one, accounts):
                if res is None:
                    unreadable.append({"accountId": a["id"], "name": a["name"], "platform": a.get("platform") or "google", "error": err})
                    continue
                ok.add(a["id"])
                if res["unread"]:
                    hold[a["id"]] = set(res["unread"]) | {"small", "more"}           # the quiet and the counted issues may hold findings of the part that failed
                issues_out += issues_of({"id": a["id"], "name": a["name"], "platform": a.get("platform") or "google"}, res)
        return {"issues": issues_out, "snaps": {}, "okIds": ok, "unreadable": unreadable, "hold": hold}
    return {"agent": AGENT, "label": LABEL, "collect": collect}
