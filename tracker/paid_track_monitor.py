"""Paid Marketing Agents: Tracking & Data Health, the daily run over every account (no network of its own: the reads are passed in).

Every Google Ads and Meta ad account is read once and checked exactly as the page checks it. What the monitor engine (tracker/paid_monitor.py) is given are issues, and here an issue is one thing
a person can open and fix:

  * a high or medium finding: one issue each, fingerprint track:<account>:<the finding's key> (conversions, a conversion action, a list, the pixel), so a tag that newly broke is news, one that
    stays broken is alerted once (and again only if it gets worse) and one that recovers is resolved;
  * the low findings (customer data terms not accepted): one quiet issue per account, never alerted;
  * at most MAX_PER_ACCOUNT findings per account become issues (worst first); the rest are counted in one issue that says so.

Each run also saves what the audience lists held that day (tracker/paid_track.py snapshot_of), which is what a later day's lists are held against: a list is judged from the second day.
An account that cannot be read is listed as unreadable so nothing open is resolved by a read that could not see. A part of the read that failed for an account (its conversion actions, its audience
lists, the customer data terms, the offline upload log, the pixels) is handed to the engine as a hold: the issues of the rules that part feeds stay open instead of being resolved by a read that
could not see them, and are not alerted as new the next day.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import paid_gads_segments as gads
from . import paid_monitor as mon
from . import paid_track as t
from . import paid_track_math as math

AGENT = t.AGENT
LABEL = "Tracking & Data Health"
PAGE = "/p2/paid-marketing/tracking-health"
WORKERS = 4
MAX_PER_ACCOUNT = 100


def fingerprint(account_id, key):
    return "track:%s:%s" % (account_id, key)


# What each soft part of the read feeds: the rules (issue kinds) that cannot be judged while it is unread. A part is unread when it is None; the platform that never reads a part (Meta has no
# conversion actions, terms or uploads; Google has no pixels) does not hold it.
PART_RULES = {"actions": ("action_stopped",), "lists": ("list_lost", "list_shrunk"), "terms": ("terms",), "uploads": ("upload_stale",), "pixels": ("pixel_stale", "pixel_unavailable")}
PLATFORM_PARTS = {"google": ("actions", "lists", "terms", "uploads"), "meta": ("lists", "pixels")}


def unread_rules(raw_account, platform):
    """The issue kinds that cannot be judged because their part of the read failed for this account (a set; empty when everything was read). The quiet `small` issue and the counted `more` issue
    may hold findings of any rule, so they are held with any of them."""
    held = set()
    for part in PLATFORM_PARTS.get(platform, PLATFORM_PARTS["google"]):
        if raw_account.get(part) is None:
            held |= set(PART_RULES[part])
    return held | {"small", "more"} if held else set()


def issues_of(account, analysed):
    """[issue] for one analysed account (see the module's docstring for what an issue is). `account` is {id, name, platform}."""
    out, small = [], []
    worth = []
    for f in analysed["findings"]:
        (worth if f["level"] in ("high", "medium") else small).append(f)
    worth.sort(key=lambda f: (-math.LEVEL_RANK[f["level"]], math.ORDER.index(f["rule"]), f["key"]))
    for f in worth[:MAX_PER_ACCOUNT]:
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], f["key"]), account, f["rule"], f["level"], f["title"], f["detail"], "Start with: %s" % f["fix"], {"evidence": f["evidence"], "area": f["area"]}))
    if len(worth) > MAX_PER_ACCOUNT:
        n = len(worth) - MAX_PER_ACCOUNT
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], "more"), account, "more", "medium", "%d more tracking %s" % (n, "problem" if n == 1 else "problems"),
                                  "Only the %d worst are followed one by one; the page lists every one." % MAX_PER_ACCOUNT, "Start with: Work through the findings on the Tracking & Data Health page.", {"findings": n}))
    if small:
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], "small"), account, "small", "low", "%s to check" % mon.plural(len(small), "small finding"),
                                  "; ".join(f["title"] for f in small)[:300] + ". None of it stops tracking.", "Open the Tracking & Data Health page and check it when there is time.", {"findings": len(small)}))
    return out


def make_spec(*, today=None, search_fn=None, list_fn=None, mon_store=None, meta_pages_fn=None, meta_account_fn=None, now_fn=None, workers=WORKERS):
    """The spec run_monitor wants. `mon_store` is the monitors' store the lists' earlier readings are read from. `today` is accepted like the other monitors' (the days are counted from the clock,
    in each account's own time zone)."""
    now_fn = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))

    def collect(accounts, groups):
        listed = {a["id"]: a for a in (list_fn or gads.list_accounts)()}
        quota = {"hit": False}

        def one(a):
            meta = a.get("platform") == "meta"
            if quota["hit"] and not meta:
                return a, None, None, "The Google Ads daily read budget was used up earlier in this run."
            at = now_fn()
            try:
                if meta:
                    raw = t.meta_raw(a["id"], pages_fn=meta_pages_fn, account_fn=meta_account_fn or (lambda act: {"name": a["name"], "currency": "USD"}), now=at)
                else:
                    raw = t.google_raw({"id": a["id"], "name": a["name"], "currency": (listed.get(a["id"]) or {}).get("currency") or "USD"}, search_fn=search_fn, now=at)
                res = t.assemble(raw, {"id": a["id"], "name": a["name"]}, history_fn=lambda aid, day: t.history_for(mon_store, aid, day), now=at)["accounts"][0]
                return a, res, raw["accounts"][0], None
            except gads.GadsError as e:
                if getattr(e, "kind", "") == "quota":
                    quota["hit"] = True
                return a, None, None, str(e)[:240] or type(e).__name__
            except Exception as e:                                                   # noqa: BLE001 (Meta has its own errors; one account failing never stops the rest)
                return a, None, None, ("%s: %s" % (type(e).__name__, e))[:240]
        issues_out, snaps, ok, unreadable, hold = [], {}, set(), [], {}
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for a, res, raw_acct, err in pool.map(one, accounts):
                if res is None:
                    unreadable.append({"accountId": a["id"], "name": a["name"], "platform": a.get("platform") or "google", "error": err})
                    continue
                ok.add(a["id"])
                held = unread_rules(raw_acct, a.get("platform") or "google")
                if held:
                    hold[a["id"]] = held
                issues_out += issues_of({"id": a["id"], "name": a["name"], "platform": a.get("platform") or "google"}, res)
                snaps[a["id"]] = t.snapshot_of(raw_acct, res)
        return {"issues": issues_out, "snaps": snaps, "okIds": ok, "unreadable": unreadable, "hold": hold}
    return {"agent": AGENT, "label": LABEL, "collect": collect}
