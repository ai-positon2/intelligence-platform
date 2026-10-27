"""Paid Marketing Agents: Location Conflicts, the daily run over every account (no network of its own: the reads are passed in).

Every Google Ads and Meta ad account is read once and checked exactly as the page checks it. What the monitor engine (tracker/paid_monitor.py) is given are issues, and here an issue is
one thing a person can open and fix:

  * a high or medium finding: one issue each, fingerprint geoconf:<account>:<the finding's key> (campaign and place, or the ad, or the ad group), so a second conflict is news, an unchanged
    one is alerted once and a fixed one is resolved;
  * the low findings (two campaigns on one place, a schedule that differs): one quiet issue per account that counts them, never alerted, so a Monday summary can say how many there are;
  * at most MAX_PER_ACCOUNT findings per account become issues (worst first); the rest are counted in one issue that says so.

An account whose place names could not be read yields no place findings and is not called clean: it is listed as unreadable so nothing open is resolved by a read that could not see.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import paid_copycheck_settings as copy_st
from . import paid_geoconf as gc
from . import paid_geoconf_math as math
from . import paid_geoconf_settings as st
from . import paid_gads_segments as gads
from . import paid_monitor as mon

AGENT = "geoconf"
LABEL = "Location Conflicts"
PAGE = "/p2/paid-marketing/location-conflicts"
WORKERS = 4
MAX_PER_ACCOUNT = 200


def fingerprint(account_id, key):
    return "geoconf:%s:%s" % (account_id, key)


def issues_of(account, analysed):
    """[issue] for one analysed account (see the module's docstring for what an issue is). `account` is {id, name, platform}."""
    out, small = [], 0
    worth = []
    for it in analysed["items"]:
        for f in it["findings"]:
            if f["level"] in ("high", "medium"):
                worth.append(f)
            else:
                small += 1
    worth.sort(key=lambda f: (-math.LEVEL_RANK[f["level"]], math.ORDER.index(f["rule"]), f["key"]))
    for f in worth[:MAX_PER_ACCOUNT]:
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], f["key"]), account, f["rule"], f["level"], f["title"], f["detail"], "Start with: %s" % f["fix"],
                                  {"campaign": f["campaign"], "adGroup": f["adGroup"], "ad": f["adId"], "places": f["places"]}))
    if len(worth) > MAX_PER_ACCOUNT:
        n = len(worth) - MAX_PER_ACCOUNT
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], "more"), account, "more", "medium", "%d more %s with a location to fix" % (n, "thing" if n == 1 else "things"),
                                  "Only the %d worst are followed one by one; the page lists every one." % MAX_PER_ACCOUNT, "Start with: Work through the findings on the Location Conflicts page.", {"findings": n}))
    if small:
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], "small"), account, "small", "low", "%s to check" % mon.plural(small, "small setting"),
                                  "Campaigns on the same place, or a schedule or device setting that differs from the others: none of it stops a campaign.",
                                  "Open the Location Conflicts page and check them when there is time.", {"findings": small}))
    return out


def snapshot_of(a):
    c = a["counts"]
    return {"status": a["status"], "campaigns": a["campaigns"], "high": c["high"], "medium": c["medium"], "low": c["low"], "findings": a["findingCount"]}


def make_spec(*, today, search_fn=None, list_fn=None, mon_store=None, meta_pages_fn=None, meta_account_fn=None, now_fn=None, workers=WORKERS):
    """The spec run_monitor wants. `mon_store` is the monitors' store what the team saved (areas, words to ignore, the presence option) is read from."""
    now_fn = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))

    def collect(accounts, groups):
        listed = {a["id"]: a for a in (list_fn or gads.list_accounts)()}
        try:
            settings = st.load_for_read(mon_store) if mon_store is not None else st.empty()
            copy_settings = copy_st.load_for_read(mon_store) if mon_store is not None else copy_st.empty()
        except Exception:                                                            # noqa: BLE001 (no saved lists is not a reason to skip the run: only the checks that need them are skipped)
            settings, copy_settings = st.empty(), copy_st.empty()
        quota = {"hit": False}

        def one(a):
            meta = a.get("platform") == "meta"
            if quota["hit"] and not meta:
                return a, None, "The Google Ads daily read budget was used up earlier in this run."
            try:
                if meta:
                    raw = gc.meta_raw(a["id"], pages_fn=meta_pages_fn, account_fn=meta_account_fn or (lambda act: {"name": a["name"], "currency": "USD"}))
                else:
                    raw = gc.google_raw({"id": a["id"], "name": a["name"], "currency": (listed.get(a["id"]) or {}).get("currency") or "USD"}, search_fn=search_fn)
                res = gc.assemble(raw, {"id": a["id"], "name": a["name"]}, settings=settings, copy_settings=copy_settings, now=now_fn())["accounts"][0]
                if not res["namesRead"]:
                    return a, None, "The names of the places could not be read, so the account was not checked."
                return a, res, None
            except gads.GadsError as e:
                if getattr(e, "kind", "") == "quota":
                    quota["hit"] = True
                return a, None, str(e)[:240] or type(e).__name__
            except Exception as e:                                                   # noqa: BLE001 (Meta has its own errors; one account failing never stops the rest)
                return a, None, ("%s: %s" % (type(e).__name__, e))[:240]
        issues, snaps, ok, unreadable = [], {}, set(), []
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for a, res, err in pool.map(one, accounts):
                if res is None:
                    unreadable.append({"accountId": a["id"], "name": a["name"], "platform": a.get("platform") or "google", "error": err})
                    continue
                ok.add(a["id"])
                issues += issues_of({"id": a["id"], "name": a["name"], "platform": a.get("platform") or "google"}, res)
                snaps[a["id"]] = snapshot_of(res)
        return {"issues": issues, "snaps": snaps, "okIds": ok, "unreadable": unreadable}
    return {"agent": AGENT, "label": LABEL, "collect": collect}
