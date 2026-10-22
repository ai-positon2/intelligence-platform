"""Paid Marketing Agents: Keyword Visibility Tracker, the weekly run over every Google Ads account (no network of its own: the reads are passed in).

Every Google Ads account is read once and checked exactly as the page checks it (Meta accounts have no keywords and are left alone). What the monitor engine (tracker/paid_monitor.py) is given are
issues, and here an issue is one thing a person can open and fix:

  * a high or medium finding: one issue each, fingerprint kvis:<account>:<the finding's key> (the rule and the keyword, or the rule and the competitor's domain), so a keyword that newly loses its
    place at the top of the page is news, one that stays low is alerted once (and again only if it gets worse) and one that recovers is resolved;
  * the low findings (a little under the lowest top-of-page share, a share lost to rank, a competitor overlapping more): one quiet issue per account that counts them, never alerted;
  * at most MAX_PER_ACCOUNT findings per account become issues (worst first); the rest are counted in one issue that says so.

An account whose read lost a part (the Quality Score history or the auction insights could not be read) is listed as unreadable with the reason, not checked on what was left: a finding that
needs the missing part would look fixed and be resolved by a read that could not see.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import paid_gads_segments as gads
from . import paid_kvis as kv
from . import paid_kvis_math as math
from . import paid_kvis_settings as st
from . import paid_monitor as mon

AGENT = kv.AGENT
LABEL = "Keyword Visibility Tracker"
PAGE = "/p2/paid-marketing/keyword-visibility"
WORKERS = 4
MAX_PER_ACCOUNT = 200


def fingerprint(account_id, key):
    return "kvis:%s:%s" % (account_id, key)


def issues_of(account, analysed):
    """[issue] for one analysed account (see the module's docstring for what an issue is). `account` is {id, name, platform}."""
    out, small = [], 0
    worth = []
    findings = [f for it in analysed["items"] for f in it["findings"]] + list(analysed.get("competitorFindings") or [])
    for f in findings:
        if f["level"] in ("high", "medium"):
            worth.append(f)
        else:
            small += 1
    worth.sort(key=lambda f: (-math.LEVEL_RANK[f["level"]], math.ORDER.index(f["rule"]), f["key"]))
    for f in worth[:MAX_PER_ACCOUNT]:
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], f["key"]), account, f["rule"], f["level"], f["title"], f["detail"], "Start with: %s" % f["fix"],
                                  {"keyword": f["keyword"], "campaign": f["campaign"], "campaigns": f["campaigns"], "evidence": f["evidence"]}))
    if len(worth) > MAX_PER_ACCOUNT:
        n = len(worth) - MAX_PER_ACCOUNT
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], "more"), account, "more", "medium", "%d more %s to look at" % (n, "thing" if n == 1 else "things"),
                                  "Only the %d worst are followed one by one; the page lists every one." % MAX_PER_ACCOUNT, "Start with: Work through the findings on the Keyword Visibility page.", {"findings": n}))
    if small:
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], "small"), account, "small", "low", "%s to check" % mon.plural(small, "small finding"),
                                  "Keywords a little under the lowest top-of-page share, searches lost to rank, or a competitor overlapping more: none of it stops a keyword.",
                                  "Open the Keyword Visibility page and check them when there is time.", {"findings": small}))
    return out


def snapshot_of(a):
    c = a["counts"]
    return {"status": a["status"], "keywords": a["keywords"], "judged": a["judged"], "high": c["high"], "medium": c["medium"], "low": c["low"], "findings": a["findingCount"],
            "competitors": len(a["competitorFindings"])}


def make_spec(*, today=None, search_fn=None, list_fn=None, mon_store=None, meta_pages_fn=None, meta_account_fn=None, now_fn=None, workers=WORKERS):
    """The spec run_monitor wants. `mon_store` is the monitors' store what the team saved (targets, ignored domains) is read from. The Meta arguments are accepted like the other monitors' and unused."""
    now_fn = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))

    def collect(accounts, groups):
        listed = {a["id"]: a for a in (list_fn or gads.list_accounts)()}
        try:
            settings = st.load(mon_store) if mon_store is not None else st.empty()
        except Exception:                                                            # noqa: BLE001 (no saved settings is not a reason to skip the run: the defaults apply)
            settings = st.empty()
        quota = {"hit": False}

        def one(a):
            if quota["hit"]:
                return a, None, "The Google Ads daily read budget was used up earlier in this run."
            at = now_fn()
            try:
                raw = kv.google_raw({"id": a["id"], "name": a["name"], "currency": (listed.get(a["id"]) or {}).get("currency") or "USD"}, search_fn=search_fn, today=at.date())
                if raw["warnings"]:
                    return a, None, "Part of the read failed, so the account was not checked: %s" % "; ".join(raw["warnings"])[:200]
                return a, kv.assemble(raw, {"id": a["id"], "name": a["name"]}, settings=settings, now=at)["accounts"][0], None
            except gads.GadsError as e:
                if getattr(e, "kind", "") == "quota":
                    quota["hit"] = True
                return a, None, str(e)[:240] or type(e).__name__
            except Exception as e:                                                   # noqa: BLE001 (one account failing never stops the rest)
                return a, None, ("%s: %s" % (type(e).__name__, e))[:240]
        google = [a for a in accounts if a.get("platform") != "meta"]
        issues, snaps, ok, unreadable = [], {}, set(), []
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for a, res, err in pool.map(one, google):
                if res is None:
                    unreadable.append({"accountId": a["id"], "name": a["name"], "platform": "google", "error": err})
                    continue
                ok.add(a["id"])
                issues += issues_of({"id": a["id"], "name": a["name"], "platform": "google"}, res)
                snaps[a["id"]] = snapshot_of(res)
        return {"issues": issues, "snaps": snaps, "okIds": ok, "unreadable": unreadable}
    return {"agent": AGENT, "label": LABEL, "collect": collect}
