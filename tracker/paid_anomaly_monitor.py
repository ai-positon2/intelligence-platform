"""Paid Marketing Agents: Spend Anomaly Alert, the run over every account every few hours (no network of its own: the reads are passed in).

Every Google Ads and Meta ad account is read once (and further only when it looks wrong), judged exactly as the page judges it with what the team saved, and turned into issues for the
monitor engine (tracker/paid_monitor.py): one for each medium or high finding, with a fingerprint of the account, the kind and the day (so a spike that goes on all day is one issue that is
alerted once and again only if it gets worse). The engine says what is new, worse or fixed and sends the email and Slack alert; this file only says what an issue of this monitor is.
Low findings are on the page, never alerted. A planned push the team muted is information and makes no issue. The day's last figures of each account are kept for the history.

It runs on its own schedule (tracker/paid_monitors.py: "frequent"), not with the daily run, because a spike is worth an alert the hour it starts and not the next morning.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import paid_anomaly as an
from . import paid_anomaly_math as m
from . import paid_gads_segments as gads
from . import paid_monitor as mon

AGENT = "spend"
LABEL = "Spend Anomaly Alert"
PAGE = "/p2/paid-marketing/spend-anomaly"
WORKERS = 4
ALERTED = ("medium", "high")


def fingerprint(account_id, finding):
    """The same problem in the same place has the same fingerprint every run: the account, the kind and the day."""
    return ":".join([account_id, finding["kind"], finding["key"]])


def issues_of(account, analysed):
    """[issue] for one analysed account: every medium or high finding. `account` is {id, name, platform}."""
    out = []
    for f in analysed["findings"]:
        if f["level"] not in ALERTED:
            continue
        data = {"kind": f["kind"], "day": f["day"], "spent": f.get("spent"), "usual": f.get("usual"), "ratio": f.get("ratio"), "today": analysed["today"]["cost"], "usualDay": analysed["usualDay"]}
        if f.get("cause"):
            data["cause"] = f["cause"]["kind"]
        out.append(mon.make_issue(AGENT, fingerprint(account["id"], f), account, f["kind"], f["level"], f["title"], f["detail"], f["nextStep"], data))
    return out


def make_spec(*, today=None, search_fn=None, list_fn=None, mon_store=None, meta_pages_fn=None, meta_account_fn=None, now_fn=None, workers=WORKERS):
    """The spec run_monitor wants. `mon_store` is the monitors' store what the team saved (usual spend, ceilings, muted days, the sensitivity) is read from. The reads are fresh: a spike is
    about now, so the page's ten-minute copy is not used."""
    from . import paid_anomaly_settings as st
    from . import paid_meta, paid_store
    now_fn = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))

    def meta_account(act):
        if meta_account_fn is not None:
            return meta_account_fn(act) or {}
        try:
            return paid_meta.account_by_id(paid_store, act) or {}
        except Exception:                                                            # noqa: BLE001 (without its zone an account is read in UTC, and says so)
            return {}

    def collect(accounts, groups):
        listed = {a["id"]: a for a in (list_fn or gads.list_accounts)()}
        try:
            settings = st.load(mon_store) if mon_store is not None else st.empty()
        except Exception:                                                            # noqa: BLE001 (no saved figures is not a reason to skip the run)
            settings = st.empty()
        sens = settings.get("sensitivity")
        quota = {"hit": False}
        now_utc = now_fn()

        def one(a):
            meta = a.get("platform") == "meta"
            if quota["hit"] and not meta:
                return a, None, "The Google Ads daily read budget was used up earlier in this run."
            try:
                info = meta_account(a["id"]) if meta else {}
                member = {"id": a["id"], "name": a["name"], "platform": "meta" if meta else "google", "currency": (info.get("currency") if meta else (listed.get(a["id"]) or {}).get("currency")) or "USD"}
                saved = st.account_of(settings, a["id"])
                raw = an.read_account(member, search=search_fn, pages=meta_pages_fn or (lambda path, params: paid_meta._pages(path, params, paid_store)), meta_account=dict(info, id=a["id"]),
                                      now_utc=now_utc, saved=saved, sensitivity=sens)
                return a, an.analyse_raw(raw, saved=saved, sensitivity=sens), None
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
                snaps[a["id"]] = m.snapshot_of(res)
        return {"issues": issues, "snaps": snaps, "okIds": ok, "unreadable": unreadable}
    return {"agent": AGENT, "label": LABEL, "collect": collect}
