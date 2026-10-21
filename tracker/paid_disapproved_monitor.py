"""Paid Marketing Agents: Disapproved Ads Tracker, the daily run over every account (no network of its own: the reads are passed in).

Every Google Ads and Meta ad account is read once and analysed exactly as the page does it. What the monitor engine (tracker/paid_monitor.py) is given are issues, and here an issue is
a problem a person can fix, not an ad:

  * a stopped ad group (or Performance Max field, or Meta ad set): high, one issue each, so a second one stopping is news;
  * a policy flagging disapproved ads that another ad covers: medium, one issue for the policy in the account, however many ads it flags;
  * a policy limiting ads: low, on the page and in the weekly digest, never alerted;
  * disapproved keywords: medium, one issue for the account.

The fingerprints are the ones the analysis uses to find the age of what is open (tracker/paid_disapproved_math.py group_fp and topic_fp), so a stopped ad group knows how many days it has been
stopped. The day's figures of each account are kept for the history.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import paid_disapproved as dis
from . import paid_disapproved_math as dm
from . import paid_gads_segments as gads
from . import paid_monitor as mon

AGENT = "disapproved"
LABEL = "Disapproved Ads Tracker"
PAGE = "/p2/paid-marketing/disapproved-ads"
WORKERS = 4


def _topic_issue_text(label, items, topic):
    names = sorted({i["campaign"] for i in items})
    return "%d flagged for %s in %s" % (len(items), label.lower(), ", ".join(names[:3]) + (" and %d more" % (len(names) - 3) if len(names) > 3 else ""))


def issues_of(account, analysed):
    """[issue] for one analysed account (see the module's docstring for what an issue is). `account` is {id, name, platform}."""
    out = []
    aid = account["id"]
    for g in analysed["deadGroups"]:
        topics = [t["label"] for t in analysed["topics"] if t["topic"] in g["topics"]]
        w = g["weight"]
        detail = "Every enabled item in %s (%s) is disapproved, so nothing in it can show%s." % (g["name"], g["campaign"], (": " + ", ".join(topics[:3])) if topics else "")
        if w and w["usualClicks"]:
            detail += " It made about %s clicks a week before; last week %s." % (format(w["usualClicks"], ","), format(w["lastClicks"], ","))
        if g.get("ageDays"):
            detail += " Stopped for %s." % dm.plural(g["ageDays"], "day")
        out.append(mon.make_issue(AGENT, g["fp"], account, "stopped", "high", "%s cannot show any ad" % g["name"], detail, "Start with: Get the ad groups that cannot show an ad running again.",
                                  {"group": g["id"], "campaign": g["campaign"], "ads": g["ads"]}))
    by = {}
    for i in analysed["items"]:
        if i["state"] in ("covered", "unchecked"):
            key, level = "topic", "medium"
        elif i["state"] == "limited":
            key, level = "limited", "low"
        else:
            continue
        for t in [x["t"] for x in i["topics"]] or [dm.NO_TOPIC]:
            by.setdefault((key, t), {"level": level, "items": []})["items"].append(i)
    labels = {t["topic"]: t["label"] for t in analysed["topics"]}
    for (key, t), g in sorted(by.items()):
        label = labels.get(t, t)
        fp = dm.topic_fp(aid, t, key == "topic")
        out.append(mon.make_issue(AGENT, fp, account, "topic" if key == "topic" else "limited", g["level"],
                                  "%s %s" % (dm.plural(len(g["items"]), "item is" if any(x["kind"] == "asset" for x in g["items"]) else "ad is", "items are" if any(x["kind"] == "asset" for x in g["items"]) else "ads are"),
                                             ("disapproved for " if key == "topic" else "limited for ") + label.lower()),
                                  _topic_issue_text(label, g["items"], t) + (". Each has another approved one beside it, so its group still shows." if key == "topic" and analysed["coverKnown"] else "."),
                                  "Start with: Fix the ads flagged for %s." % label if key == "topic" else "Check the reason in Policy manager.", {"topic": t, "ads": len(g["items"])}))
    if analysed["counts"]["keywords"]:
        n = analysed["counts"]["keywords"]
        out.append(mon.make_issue(AGENT, "keywords:%s" % aid, account, "keywords", "medium", "%s disapproved" % dm.plural(n, "keyword is", "keywords are"),
                                  "A disapproved keyword cannot trigger any ad.", "Start with: Fix the disapproved keywords.", {"keywords": n}))
    return out


def snapshot_of(a):
    c = a["counts"]
    return {"status": a["status"], "stoppedGroups": c["stoppedGroups"], "disapproved": c["disapproved"], "keywords": c["keywords"], "limited": c["limited"]}


def make_spec(*, today, search_fn=None, list_fn=None, mon_store=None, meta_pages_fn=None, meta_account_fn=None, now_fn=None, workers=WORKERS):
    """The spec run_monitor wants. `mon_store` is the monitors' store the ages of what is open are read from."""
    now_fn = now_fn or (lambda: dt.datetime.now(dt.timezone.utc))

    def collect(accounts, groups):
        listed = {a["id"]: a for a in (list_fn or gads.list_accounts)()}
        try:
            ages = dis.ages_of(mon_store.issues_all(AGENT)) if mon_store is not None else None
        except Exception:                                                            # noqa: BLE001 (no history is not a reason to skip the run)
            ages = None
        quota = {"hit": False}

        def one(a):
            meta = a.get("platform") == "meta"
            if quota["hit"] and not meta:
                return a, None, "The Google Ads daily read budget was used up earlier in this run."
            try:
                if meta:
                    raw = dis.meta_raw(a["id"], pages_fn=meta_pages_fn, account_fn=meta_account_fn or (lambda act: {"name": a["name"], "currency": "USD"}))
                else:
                    raw = dis.google_raw({"id": a["id"], "name": a["name"], "currency": (listed.get(a["id"]) or {}).get("currency") or "USD"}, today, search_fn=search_fn)
                an = m_analyse(raw["accounts"][0], ages, now_fn())
                return a, an, None
            except gads.GadsError as e:
                if getattr(e, "kind", "") == "quota":
                    quota["hit"] = True
                return a, None, str(e)[:240] or type(e).__name__
            except Exception as e:                                                   # noqa: BLE001 (Meta has its own errors; one account failing never stops the rest)
                return a, None, ("%s: %s" % (type(e).__name__, e))[:240]
        issues, snaps, ok, unreadable = [], {}, set(), []
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for a, an, err in pool.map(one, accounts):
                if an is None:
                    unreadable.append({"accountId": a["id"], "name": a["name"], "platform": a.get("platform") or "google", "error": err})
                    continue
                ok.add(a["id"])
                issues += issues_of({"id": a["id"], "name": a["name"], "platform": a.get("platform") or "google"}, an)
                snaps[a["id"]] = snapshot_of(an)
        return {"issues": issues, "snaps": snaps, "okIds": ok, "unreadable": unreadable}
    return {"agent": AGENT, "label": LABEL, "collect": collect}


def m_analyse(raw_account, ages, now):
    return dm.analyse(raw_account["ads"], raw_account["cover"], raw_account["weekly"], currency=raw_account["currency"], account_id=raw_account["id"], ages=ages, now=now)
