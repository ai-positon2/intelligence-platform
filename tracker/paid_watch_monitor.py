"""Paid Marketing Agents: Account Watch, the daily run over every account (no network of its own: the reads are passed in).

Every Google Ads and Meta ad account is read once and checked exactly as the page checks it. What the monitor engine (tracker/paid_monitor.py) is given are issues, and here an issue is one thing
a person can open and fix:

  * a high or medium finding: one issue each, fingerprint watch:<account>:<the finding's key> (the status, a budget change, who now has access...), so a suspension or a change is news once and a
    thing that is put right is resolved;
  * the low findings: one quiet issue per account, never alerted;
  * the manager account's own list is read once: an account that is suspended, canceled or closed is an issue even though the platform's account list leaves it out, and an account that was in the
    list at an earlier reading and is not any more is an issue until it is listed again.

A part of the read that failed for an account (its budget orders, change history, conversion actions, users, or on Meta its status, Pages and activity log) is handed to the engine as a hold: the
issues of the rules that part feeds stay open instead of being resolved by a read that could not see them, and are not alerted as new the next day.

Each run saves, per account, who can open it, its conversion actions and its Pages (what a later day is held against), and the manager account's list under the key "mcc". An account that cannot be
read is listed as unreadable so nothing open is resolved by a read that could not see.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import paid_gads_segments as gads
from . import paid_monitor as mon
from . import paid_watch as w
from . import paid_watch_math as math

AGENT = w.AGENT
LABEL = "Account Watch"
PAGE = "/p2/paid-marketing/account-watch"
WORKERS = 4
MAX_PER_ACCOUNT = 100


def fingerprint(account_id, key):
    return "watch:%s:%s" % (account_id, key)


# What each soft part of the read feeds: the rules (issue kinds) that cannot be judged while it is unread. A part is unread when it is None (for Google's status too: the manager account's list
# could not be read); a Meta status without a code is unread as well. The platform that never reads a part does not hold it.
PART_RULES = {
    "google": {"status": ("account_status",), "orders": ("spend_limit",), "changes": ("budget_change", "bid_change", "outside_editor"), "actions": ("goal_change",), "users": ("access_change",)},
    "meta": {"status": ("account_status", "spend_limit"), "pages": ("page_lost",), "activity": ("budget_change", "bid_change", "activity", "outside_editor")},
}


def unread_rules(raw_account, platform):
    """The issue kinds that cannot be judged because their part of the read failed for this account (a set; empty when everything was read). The quiet `small` issue and the counted `more` issue
    may hold findings of any rule, so they are held with any of them."""
    parts = PART_RULES["meta" if platform == "meta" else "google"]
    held = set()
    for part, rules in parts.items():
        v = raw_account.get(part)
        if v is None or (part == "status" and platform == "meta" and v.get("code") is None):
            held |= set(rules)
    return held | {"small", "more"} if held else set()


def issues_of(account, analysed):
    """[issue] for one analysed account (see the module's docstring for what an issue is). `account` is {id, name, platform}."""
    out, small, worth = [], [], []
    for f in analysed["findings"]:
        (worth if f["level"] in ("high", "medium") else small).append(f)
    worth.sort(key=lambda f: (-math.LEVEL_RANK[f["level"]], math.ORDER.index(f["rule"]), f["key"]))
    for f in worth[:MAX_PER_ACCOUNT]:
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], f["key"]), account, f["rule"], f["level"], f["title"], f["detail"], "Start with: %s" % f["fix"], {"evidence": f["evidence"], "area": f["area"]}))
    if len(worth) > MAX_PER_ACCOUNT:
        n = len(worth) - MAX_PER_ACCOUNT
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], "more"), account, "more", "medium", "%d more changes to check" % n, "Only the %d worst are followed one by one; the page lists every one." % MAX_PER_ACCOUNT,
                                  "Start with: Work through the findings on the Account Watch page.", {"findings": n}))
    if small:
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], "small"), account, "small", "low", "%s to check" % mon.plural(len(small), "small finding"), "; ".join(f["title"] for f in small)[:300] + ".",
                                  "Open the Account Watch page and check it when there is time.", {"findings": len(small)}))
    return out


def make_spec(*, today=None, search_fn=None, list_fn=None, mon_store=None, meta_pages_fn=None, meta_account_fn=None, now_fn=None, workers=WORKERS, meta_get_fn=None):
    """The spec run_monitor wants. `mon_store` is the monitors' store the earlier readings are read from. `today` is accepted like the other monitors' (the days are counted from the clock)."""
    now_fn = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))

    def collect(accounts, groups):
        listed = {a["id"]: a for a in (list_fn or gads.list_accounts)()}
        search = search_fn or gads.search
        at0 = now_fn()
        clients, problem = None, None
        if any(a.get("platform") != "meta" for a in accounts):
            try:
                clients = w.read_clients(search(gads.login_customer_id(), w.GAQL_CLIENTS))
            except gads.GadsError as e:
                problem = str(e)[:240]
        quota = {"hit": False}

        def one(a):
            meta = a.get("platform") == "meta"
            if quota["hit"] and not meta:
                return a, None, None, "The Google Ads daily read budget was used up earlier in this run."
            at = now_fn()
            try:
                if meta:
                    raw = w.meta_raw(a["id"], pages_fn=meta_pages_fn, account_fn=meta_account_fn or (lambda act: {"name": a["name"], "currency": "USD"}), get_fn=meta_get_fn, now=at)
                else:
                    raw = w.google_raw({"id": a["id"], "name": a["name"], "currency": (listed.get(a["id"]) or {}).get("currency") or "USD"}, search_fn=search_fn, clients=clients if clients is not None else {}, now=at)
                    if clients is None:
                        raw["accounts"][0]["status"] = None
                res = w.assemble(raw, {"id": a["id"], "name": a["name"]}, history_fn=lambda aid, day: w.history_for(mon_store, aid, day), now=at)["accounts"][0]
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
                snaps[a["id"]] = w.snapshot_of(raw_acct, res)
        if clients is not None:
            read_ids = {a["id"] for a in accounts}
            for c in clients.values():
                if c["id"] in read_ids:
                    continue
                ok.add(c["id"])                                                      # listed by the manager account, so an issue about it can be resolved (an account enabled again before the platform's own list shows it)
                if c["status"] != "ENABLED":
                    issues_out += issues_of({"id": c["id"], "name": c["name"], "platform": "google"}, w._inactive_result(c, at0))
            snap = w.mcc_snapshot(clients, w.history_for(mon_store, "mcc", at0.date()), at0.date())
            snaps["mcc"] = snap
            for cid, name in snap["gone"].items():
                issues_out += issues_of({"id": cid, "name": name, "platform": "google"}, w._gone_result(cid, name, at0))
        elif problem and any(a.get("platform") != "meta" for a in accounts):
            unreadable.append({"accountId": "mcc", "name": "The manager account's list", "platform": "google", "error": problem})
        return {"issues": issues_out, "snaps": snaps, "okIds": ok, "unreadable": unreadable, "hold": hold}
    return {"agent": AGENT, "label": LABEL, "collect": collect}
