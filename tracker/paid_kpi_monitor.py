"""Paid Marketing Agents: KPI Monitoring, the daily run over every account (no network of its own: the reads are passed in).

Every Google Ads and Meta ad account is read once and checked exactly as the page checks it. What the monitor engine (tracker/paid_monitor.py) is given are issues, and here an issue is one
thing a person can open and fix:

  * a high or medium finding: one issue each, fingerprint kpi:<account>:<the finding's key> (the rule and the campaign), so a campaign newly off its target is news, one that stays off is alerted
    once (and again only if it gets worse, which the engine sees as a higher level on the same fingerprint) and one back on target is resolved;
  * a learning finding at any level: its own issue too, because how long it has been open is how long the campaign has been learning (the page and the next run read that age from here, which is
    how a campaign that learns for 14 days becomes "stuck" without the monitor keeping a second record);
  * the other low findings (a budget limit on a campaign off target, a conversion rate a little low): one quiet issue per account that counts them, never alerted;
  * at most MAX_PER_ACCOUNT findings per account become issues (worst first); the rest are counted in one issue that says so.

An account that cannot be read is listed as unreadable so nothing open is resolved by a read that could not see.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import paid_gads_segments as gads
from . import paid_kpi as k
from . import paid_kpi_math as math
from . import paid_kpi_settings as st
from . import paid_monitor as mon

AGENT = k.AGENT
LABEL = "KPI Monitoring"
PAGE = "/p2/paid-marketing/kpi-monitoring"
WORKERS = 4
MAX_PER_ACCOUNT = 200


def fingerprint(account_id, key):
    return "kpi:%s:%s" % (account_id, key)


def issues_of(account, analysed):
    """[issue] for one analysed account (see the module's docstring for what an issue is). `account` is {id, name, platform}."""
    out, small = [], 0
    worth = []
    for it in analysed["items"]:
        for f in it["findings"]:
            if f["level"] in ("high", "medium") or f["rule"] == "learning":
                worth.append(f)
            else:
                small += 1
    worth.sort(key=lambda f: (-math.LEVEL_RANK[f["level"]], math.ORDER.index(f["rule"]), f["key"]))
    for f in worth[:MAX_PER_ACCOUNT]:
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], f["key"]), account, f["rule"], f["level"], f["title"], f["detail"], "Start with: %s" % f["fix"],
                                  {"campaign": f["campaign"], "campaignId": f["campaignId"], "evidence": f["evidence"]}))
    if len(worth) > MAX_PER_ACCOUNT:
        n = len(worth) - MAX_PER_ACCOUNT
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], "more"), account, "more", "medium", "%d more %s off target or held back" % (n, "campaign" if n == 1 else "campaigns"),
                                  "Only the %d worst are followed one by one; the page lists every one." % MAX_PER_ACCOUNT, "Start with: Work through the findings on the KPI Monitoring page.", {"findings": n}))
    if small:
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], "small"), account, "small", "low", "%s to check" % mon.plural(small, "small finding"),
                                  "A campaign held back by a budget it does not use well, or a conversion or click-through rate a little under what is wanted: none of it stops a campaign.",
                                  "Open the KPI Monitoring page and check them when there is time.", {"findings": small}))
    return out


def snapshot_of(a):
    c = a["counts"]
    return {"status": a["status"], "campaigns": a["campaigns"], "high": c["high"], "medium": c["medium"], "low": c["low"], "findings": a["findingCount"], "offTarget": a["offTarget"], "onTarget": a["onTarget"]}


def make_spec(*, today=None, search_fn=None, list_fn=None, mon_store=None, meta_pages_fn=None, meta_account_fn=None, now_fn=None, workers=WORKERS):
    """The spec run_monitor wants. `mon_store` is the monitors' store what the team saved (targets, muted campaigns) and the open learning issues are read from. `today` is accepted like the
    other monitors' (the days are counted from the clock, in each account's own time zone)."""
    now_fn = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))

    def collect(accounts, groups):
        listed = {a["id"]: a for a in (list_fn or gads.list_accounts)()}
        try:
            settings = st.load(mon_store) if mon_store is not None else st.empty()
        except Exception:                                                            # noqa: BLE001 (no saved targets is not a reason to skip the run: only the checks that need them are skipped)
            settings = st.empty()
        try:
            issues = mon_store.issues_all(AGENT) if mon_store is not None else []
        except Exception:                                                            # noqa: BLE001 (without the history a learning campaign is only "still learning")
            issues = []
        quota = {"hit": False}

        def one(a):
            meta = a.get("platform") == "meta"
            if quota["hit"] and not meta:
                return a, None, "The Google Ads daily read budget was used up earlier in this run."
            at = now_fn()
            try:
                if meta:
                    raw = k.meta_raw(a["id"], pages_fn=meta_pages_fn, account_fn=meta_account_fn or (lambda act: {"name": a["name"], "currency": "USD"}), now=at)
                else:
                    raw = k.google_raw({"id": a["id"], "name": a["name"], "currency": (listed.get(a["id"]) or {}).get("currency") or "USD"}, search_fn=search_fn, now=at)
                res = k.assemble(raw, {"id": a["id"], "name": a["name"]}, settings=settings, issues=issues, now=at)["accounts"][0]
                return a, res, None
            except gads.GadsError as e:
                if getattr(e, "kind", "") == "quota":
                    quota["hit"] = True
                return a, None, str(e)[:240] or type(e).__name__
            except Exception as e:                                                   # noqa: BLE001 (Meta has its own errors; one account failing never stops the rest)
                return a, None, ("%s: %s" % (type(e).__name__, e))[:240]
        issues_out, snaps, ok, unreadable = [], {}, set(), []
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for a, res, err in pool.map(one, accounts):
                if res is None:
                    unreadable.append({"accountId": a["id"], "name": a["name"], "platform": a.get("platform") or "google", "error": err})
                    continue
                ok.add(a["id"])
                issues_out += issues_of({"id": a["id"], "name": a["name"], "platform": a.get("platform") or "google"}, res)
                snaps[a["id"]] = snapshot_of(res)
        return {"issues": issues_out, "snaps": snaps, "okIds": ok, "unreadable": unreadable}
    return {"agent": AGENT, "label": LABEL, "collect": collect}
