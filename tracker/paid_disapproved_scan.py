"""Paid Marketing Agents: Disapproved Ads Agent, the scan.

A scan reads every active ad account for what is disapproved among the live ads (tracker/paid_disapproved.py), keeps the findings, and alerts by email and Slack
about the ones it has not alerted before. The rules it follows:

  * only live ads count (enabled campaign, enabled ad group, enabled ad; active on Meta): the reading asks for nothing else;
  * an account that cannot be read is named on the scan, never counted clean, and its findings are not called fixed;
  * a finding is new the first time it is seen and again when it comes back after being fixed; a dismissed one stays quiet;
  * a finding is marked alerted only when a channel took the digest, so one that went nowhere (no recipient, Slack refused) is tried again at the next scan;
  * two scans never run at once (a lock in the store that expires, so a scan that died does not block the next).

Everything outside the platform (the ad accounts, the senders, the store) is passed in, so the whole of it is tested without a network.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import paid_disapproved as da
from . import paid_domains as dom
from . import paid_domains_scan as sc

LOCK = "da-scan-lock"
LOCK_TTL_S = 30 * 60
WORKERS = 4
RUNS_KEPT = 20
BadRequest = sc.BadRequest
ORDER = {"open": 0, "dismissed": 1, "fixed": 2}


def _iso(now=None):
    return (now or datetime.now(timezone.utc)).isoformat()


def read_account(account, store):
    """(items, counts) of one account, read through the platform's own readers."""
    if account["platform"] == "meta":
        from . import paid_meta, paid_store
        return da.meta_items(account, lambda path, params: paid_meta._pages(path, params, paid_store))
    from . import paid_gads_segments as g
    return da.google_items(account, g.search)


# ── Settings and sending ────────────────────────────────────────────────────

def settings(store):
    """{emails, slackChannel, own}: this agent's recipients and Slack channel; while it has none, the ones saved for the Unknown Domains Alert (own is False)."""
    mine = store.state_get("da_settings") or {}
    if mine.get("emails") or mine.get("slackChannel"):
        return {"emails": mine.get("emails") or [], "slackChannel": mine.get("slackChannel") or "", "own": True}
    theirs = store.state_get("settings") or {}
    return {"emails": theirs.get("emails") or [], "slackChannel": theirs.get("slackChannel") or "", "own": False}


def save_settings(store, data):
    saved = sc.clean_settings(data)
    store.state_set("da_settings", saved)
    return saved


def send(store, message, deliver):
    """Send an alert message to the recipients and channel: {email: {status, error?}, slack: {...}, sent}, `sent` True when at least one channel took it."""
    s = settings(store)
    extra = {"attachments": message["attachments"]} if message.get("attachments") else {}
    email = (deliver.attempt(lambda: deliver.send_email(message["subject"], message["text"], message["html"], s["emails"], **extra))
             if s["emails"] else {"status": "not_configured", "error": "No email address is set."})
    slack = (deliver.attempt(lambda: deliver.send_slack(s["slackChannel"], message["slack_text"], message["slack_blocks"]))
             if s["slackChannel"] else {"status": "not_configured", "error": "No Slack channel is set."})
    return {"email": email, "slack": slack, "sent": "sent" in (email["status"], slack["status"])}


def test_alert(store, link, deliver):
    """A made-up finding through the real senders, to prove the wiring."""
    f = {"fingerprint": "0" * 24, "platform": "google", "accountId": "0", "accountName": "Test account (not real)", "where": "ad", "objectId": "0", "objectName": "Test ad",
         "adType": "Responsive Search Ad", "campaign": "Test campaign", "adGroup": "Test ad group", "url": "", "severity": "high", "topics": [], "firstSeen": _iso(),
         "reason": "This is a test alert. Nothing was disapproved.", "status": "open"}
    m = da.digest([f], [f], link)
    m["subject"] = "TEST: " + m["subject"]
    m["slack_text"] = "TEST\n" + m["slack_text"]
    return send(store, m, deliver)


# ── What people do with a finding ───────────────────────────────────────────

def decide(store, fingerprint, status, by, *, now=None):
    """Dismiss a finding, or open it again."""
    if status not in ("dismissed", "open"):
        raise BadRequest("Unknown action.")
    f = store.finding_get(fingerprint)
    if not f:
        raise BadRequest("That ad is not there.")
    store.findings_put([dict(f, status=status, decidedBy=by, decidedAt=_iso(now))])


def open_sheet(store):
    """The Excel sheet of every open disapproval, as bytes."""
    return da.sheet([f for f in store.findings_all() if f["status"] == "open"])


# ── The scan ────────────────────────────────────────────────────────────────

def _progress(store, **kw):
    store.state_set("da_progress", kw)


def _keep_run(store, run):
    store.state_set("da_runs", ([run] + (store.state_get("da_runs") or []))[:RUNS_KEPT])


def run_scan(store, *, trigger, link, deliver, inventory_fn=None, read_fn=None, now_fn=None, workers=WORKERS):
    """One scan. Returns the run record ({status: "busy"} when another is going)."""
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    if not store.claim(LOCK, LOCK_TTL_S):
        return {"status": "busy"}
    started = now_fn()
    try:
        accounts, _groups, problems = (inventory_fn or sc.inventory)(store)
        read = read_fn or read_account
        _progress(store, running=True, startedAt=_iso(started), done=0, total=len(accounts))
        items, unreadable, counts, done, ok_ids = [], [], {"ads": 0, "assetGroups": 0, "assets": 0}, [0], set()
        stop = {"quota": False}

        def one(a):
            if stop["quota"] and a["platform"] == "google":
                return a, None, "The Google Ads daily read budget was used up earlier in this scan."
            try:
                return a, read(a, store), None
            except Exception as e:                                                  # noqa: BLE001
                if getattr(e, "kind", "") == "quota":
                    stop["quota"] = True
                return a, None, str(e)[:240] or type(e).__name__

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for a, ok, err in pool.map(one, accounts):
                done[0] += 1
                if ok:
                    ok_ids.add(a["id"])
                    items += ok[0]
                    for k, v in ok[1].items():
                        counts[k] = counts.get(k, 0) + v
                else:
                    unreadable.append({"accountId": a["id"], "name": a["name"], "platform": a["platform"], "error": err})
                if done[0] % 5 == 0 or done[0] == len(accounts):
                    _progress(store, running=True, startedAt=_iso(started), done=done[0], total=len(accounts))

        stamp = _iso(now_fn())
        existing = {f["fingerprint"]: f for f in store.findings_all()}
        writes, c = da.apply(items, existing, ok_ids, stamp)
        store.findings_put(writes)
        merged = dict(existing)
        merged.update({w["fingerprint"]: w for w in writes})
        open_rows = [f for f in merged.values() if f["status"] == "open"]
        pending = [f for f in open_rows if not f.get("alertedAt")]
        run = {"status": "done", "trigger": trigger, "startedAt": _iso(started), "finishedAt": stamp, "accounts": len(accounts), "read": len(ok_ids),
               "unreadable": unreadable[:30], "unreadableCount": len(unreadable), "problems": problems, "counts": counts, "disapproved": len(open_rows),
               "new": c["new"], "reopened": c["reopened"], "fixed": c["fixed"], "alerted": 0, "email": None, "slack": None}
        if pending:
            res = send(store, da.digest(open_rows, pending, link), deliver)
            run.update(email=res["email"], slack=res["slack"])
            if res["sent"]:
                when = _iso(now_fn())
                store.findings_put([dict(f, alertedAt=when, alert={"email": res["email"]["status"], "slack": res["slack"]["status"]}) for f in pending])
                run["alerted"] = len(pending)
        _keep_run(store, run)
        return run
    except Exception as e:                                                          # noqa: BLE001
        run = {"status": "failed", "trigger": trigger, "startedAt": _iso(started), "finishedAt": _iso(now_fn()), "error": ("%s: %s" % (type(e).__name__, e))[:300]}
        _keep_run(store, run)
        return run
    finally:
        try:
            _progress(store, running=False, startedAt=_iso(started))
        finally:
            store.release(LOCK)


# ── What the page shows ─────────────────────────────────────────────────────

def snapshot(store, deliver):
    """Everything the page needs in one answer."""
    rows = store.findings_all()
    counts = {s: sum(1 for f in rows if f["status"] == s) for s in ORDER}
    rows.sort(key=lambda f: (ORDER.get(f["status"], 9), da.SEVERITY_ORDER.get(f["severity"], 9), f["accountName"].lower(), f["campaign"].lower(), f["objectName"]))
    return {"findings": rows[:1000], "counts": counts, "settings": settings(store), "senders": deliver.ready(), "runs": (store.state_get("da_runs") or [])[:10],
            "progress": store.state_get("da_progress") or {}, "platforms": dom.PLATFORM}
