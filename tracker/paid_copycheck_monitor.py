"""Paid Marketing Agents: Ad Copy Checker, the weekly run over every account (no network of its own: the reads are passed in).

Every Google Ads and Meta ad account is read once and checked exactly as the page checks it. What the monitor engine (tracker/paid_monitor.py) is given are issues, and here an issue is
an ad a person can open and fix:

  * an ad with a high or medium finding: one issue per ad (a placeholder or a wrong phone number is high), so a second ad going wrong is news and a fixed ad is resolved;
  * the ads with only small things (a word in capitals, mixed styles, an unknown word the account uses often): one low issue per account that counts them, never alerted, so a Monday
    summary can say how many there are without a row for each;
  * at most MAX_ADS_PER_ACCOUNT ads per account become issues (worst first); the rest are counted in one issue that says so.

The fingerprint of an ad is the one the analysis gives (copycheck:<account>:<ad id>), so the page and the monitor agree on how long an ad has been wrong. An edited responsive ad is a
new ad in Google, so the old one resolves and the new one is checked afresh. The day's figures of each account are kept for the history.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import paid_copycheck as cc
from . import paid_copycheck_plan as plan
from . import paid_copycheck_rules as rules
from . import paid_gads_segments as gads
from . import paid_monitor as mon

AGENT = "copycheck"
LABEL = "Ad Copy Checker"
PAGE = "/p2/paid-marketing/ad-copy-checker"
WORKERS = 4
MAX_ADS_PER_ACCOUNT = 200
SHOWN_FINDINGS = 3


def _where(item):
    return "%s%s" % (item["campaign"] or "the account", (" / " + item["adGroup"]) if item["adGroup"] else "")


def _title(item):
    labels = []
    for f in item["findings"]:
        lab = rules.RULES[f["rule"]]["label"]
        if lab not in labels:
            labels.append(lab)
    return "%s in %s" % (", ".join(labels[:2]) + (" and more" if len(labels) > 2 else ""), _where(item))


def _detail(item):
    out = []
    for f in item["findings"][:SHOWN_FINDINGS]:
        out.append("%s \"%s\": %s" % (f["slot"], f["text"][:80], f["message"]))
    more = len(item["findings"]) - SHOWN_FINDINGS
    return " ".join(out) + (" And %d more." % more if more > 0 else "")


def issues_of(account, analysed):
    """[issue] for one analysed account (see the module's docstring for what an issue is). `account` is {id, name, platform}."""
    out, small = [], 0
    worth = [i for i in analysed["items"] if i["level"] in ("high", "medium")]
    for i in analysed["items"]:
        if i["level"] == "low":
            small += 1
    for i in worth[:MAX_ADS_PER_ACCOUNT]:
        first = next(iter(i["findings"]))
        step = plan.TITLES[first["rule"]] % "this ad"
        out.append(mon.make_issue(AGENT, i["fingerprint"], account, "ad", i["level"], _title(i), _detail(i), "Start with: %s." % step,
                                  {"ad": i["id"], "kind": i["kind"], "campaign": i["campaign"], "adGroup": i["adGroup"], "findings": len(i["findings"])}))
    if len(worth) > MAX_ADS_PER_ACCOUNT:
        n = len(worth) - MAX_ADS_PER_ACCOUNT
        out.append(mon.make_issue(AGENT, "copycheck:%s:more" % account["id"], account, "more", "medium", "%d more %s with something to fix" % (n, "ad" if n == 1 else "ads"),
                                  "Only the %d worst ads are followed one by one; the page lists every ad." % MAX_ADS_PER_ACCOUNT, "Start with: Work through the ads on the Ad Copy Checker page.", {"ads": n}))
    if small:
        out.append(mon.make_issue(AGENT, "copycheck:%s:small" % account["id"], account, "small", "low", "%s with small things to tidy" % mon.plural(small, "ad"),
                                  "Capitals, mixed styles or unknown words used often: none of it stops an ad.", "Open the Ad Copy Checker page and tidy them when there is time.", {"ads": small}))
    return out


def snapshot_of(a):
    c = a["counts"]
    return {"status": a["status"], "ads": a["ads"], "high": c["high"], "medium": c["medium"], "low": c["low"], "findings": a["findingCount"]}


def make_spec(*, today, search_fn=None, list_fn=None, mon_store=None, meta_pages_fn=None, meta_account_fn=None, now_fn=None, workers=WORKERS, speller=None):
    """The spec run_monitor wants. `mon_store` is the monitors' store what the team saved (phone numbers, places, brand spellings, allowed words) is read from."""
    from . import paid_brandspend_settings as bs
    from . import paid_copycheck_settings as st
    now_fn = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))
    speller = speller or rules.shared_speller()

    def collect(accounts, groups):
        listed = {a["id"]: a for a in (list_fn or gads.list_accounts)()}
        try:
            settings = st.load_for_read(mon_store) if mon_store is not None else st.empty()
            brand_terms = {aid: (a.get("brandTerms") or []) for aid, a in ((bs.load_for_read(mon_store).get("accounts") or {}) if mon_store is not None else {}).items()}
        except Exception:                                                            # noqa: BLE001 (no saved lists is not a reason to skip the run: only the checks that need them are skipped)
            settings, brand_terms = st.empty(), {}
        quota = {"hit": False}

        def one(a):
            meta = a.get("platform") == "meta"
            if quota["hit"] and not meta:
                return a, None, "The Google Ads daily read budget was used up earlier in this run."
            try:
                if meta:
                    raw = cc.meta_raw(a["id"], pages_fn=meta_pages_fn, account_fn=meta_account_fn or (lambda act: {"name": a["name"], "currency": "USD"}))
                else:
                    raw = cc.google_raw({"id": a["id"], "name": a["name"], "currency": (listed.get(a["id"]) or {}).get("currency") or "USD"}, search_fn=search_fn)
                res = cc.assemble(raw, {"id": a["id"], "name": a["name"]}, settings=settings, brand_terms=brand_terms, speller=speller, now=now_fn())["accounts"][0]
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
