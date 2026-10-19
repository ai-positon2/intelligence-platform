"""Paid Marketing Agents: Unknown Domains Alert, the scan and what people do with its findings.

A scan reads every ad account (tracker/paid_domains_read.py), holds each address against the domain database (tracker/paid_domains.py), keeps the
findings, and alerts by email and Slack about the ones it has not alerted before. The rules it follows:

  * an account that cannot be read is named on the scan; it is never counted as clean, and a finding in it is not called gone;
  * a finding is new the first time it is seen, and again when it comes back (it was gone, or its approval was taken away); a dismissed one stays quiet;
  * nothing is alerted until a baseline is set, because before the database has anything in it every address is "unknown"; setting it marks what is
    open as already seen;
  * a finding is marked alerted only when a channel took it, so a digest that went nowhere (no recipient set, Slack refused) is tried again next scan;
  * two scans never run at once (a lock in the store that expires, so a scan that died does not block the next).

Everything outside the platform (the ad accounts, the senders, the store) is passed in, so the whole of it is tested without a network.
"""
from __future__ import annotations

import csv
import io
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import paid_domains as dom
from . import paid_domains_read as rd

LOCK = "scan-lock"
LOCK_TTL_S = 30 * 60
WORKERS = 4
RUNS_KEPT = 20
MAX_EMAILS = 10
_EMAIL = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
_CHANNEL = re.compile(r"^[#@]?[A-Za-z0-9._-]{1,80}$")


class BadRequest(ValueError):
    pass


def _iso(now=None):
    return (now or datetime.now(timezone.utc)).isoformat()


# ── The ad accounts ─────────────────────────────────────────────────────────

def inventory(store):
    """(accounts, groups, problems): every ad account the platform's logins see, as {id, name, platform, group}, the MCC groups ({id, name, accountIds}) and,
    for a platform that could not be listed, a sentence saying so (the scan goes on with the other)."""
    from . import paid_gads_segments as g
    from . import paid_meta
    accounts, groups, problems = [], [], []
    if g.config_problem():
        problems.append("Google Ads is not connected: %s" % g.config_problem())
    else:
        try:
            inv = g.inventory()
            accounts += [{"id": a["id"], "name": a["name"], "platform": "google", "group": a.get("group") or ""} for a in inv["accounts"]]
            groups = [{"id": x["id"], "name": x["name"], "accountIds": x.get("accountIds") or []} for x in inv["groups"]]
        except Exception as e:                                                      # noqa: BLE001
            problems.append("Google Ads accounts could not be listed: %s" % e)
    if paid_meta.config_problem():
        problems.append("Meta Ads is not connected: %s" % paid_meta.config_problem())
    else:
        try:
            accounts += [{"id": a["id"], "name": a["name"], "platform": "meta", "group": ""} for a in paid_meta.list_accounts(store)
                         if a.get("status") in ("ACTIVE", "UNKNOWN", "")]
        except Exception as e:                                                      # noqa: BLE001
            problems.append("Meta Ads accounts could not be listed: %s" % e)
    return accounts, groups, problems


def read_account(account, store):
    """(sightings, counts) of one account, read through the platform's own readers."""
    if account["platform"] == "meta":
        from . import paid_meta
        return rd.meta_sightings(account, lambda path, params: paid_meta._pages(path, params, store))
    from . import paid_gads_segments as g
    return rd.google_sightings(account, g.search)


# ── Settings ────────────────────────────────────────────────────────────────

def clean_settings(data):
    """{emails, slackChannel} from what the page sent, or BadRequest saying what is wrong."""
    raw = data.get("emails") if isinstance(data, dict) else None
    if isinstance(raw, str):
        raw = re.split(r"[,;\s]+", raw)
    emails = []
    for e in raw or []:
        e = str(e).strip().lower()
        if not e:
            continue
        if not _EMAIL.match(e):
            raise BadRequest("%s is not an email address." % e)
        if e not in emails:
            emails.append(e)
    if len(emails) > MAX_EMAILS:
        raise BadRequest("At most %d email addresses." % MAX_EMAILS)
    channel = str((data or {}).get("slackChannel") or "").strip()
    if channel and not _CHANNEL.match(channel):
        raise BadRequest("%s is not a Slack channel name or id." % channel)
    return {"emails": emails, "slackChannel": channel}


def settings(store):
    s = store.state_get("settings") or {}
    return {"emails": s.get("emails") or [], "slackChannel": s.get("slackChannel") or ""}


# ── Sending ─────────────────────────────────────────────────────────────────

def send(store, message, deliver):
    """Send an alert message ({subject, text, html, slack_text, slack_blocks}) to the saved recipients and channel: {email: {status, error?}, slack: {...}},
    and `sent` True when at least one channel took it."""
    s = settings(store)
    email = (deliver.attempt(lambda: deliver.send_email(message["subject"], message["text"], message["html"], s["emails"]))
             if s["emails"] else {"status": "not_configured", "error": "No email address is set."})
    slack = (deliver.attempt(lambda: deliver.send_slack(s["slackChannel"], message["slack_text"], message["slack_blocks"]))
             if s["slackChannel"] else {"status": "not_configured", "error": "No Slack channel is set."})
    return {"email": email, "slack": slack, "sent": "sent" in (email["status"], slack["status"])}


def test_alert(store, link, deliver):
    """A made-up finding through the real senders, to prove the wiring."""
    f = {"kind": "unknown", "severity": "high", "platform": "google", "accountId": "0", "accountName": "Test account (not real)", "where": "ad",
         "objectId": "0", "objectName": "Test ad", "campaign": "Test campaign", "adGroup": "", "url": "https://example.invalid/test", "host": "example.invalid",
         "domain": "example.invalid", "redirect": False, "lookalike": None, "owners": [], "why": "This is a test alert. Nothing was found."}
    m = dom.digest([f], link)
    m["subject"] = "TEST: " + m["subject"]
    m["slack_text"] = "TEST\n" + m["slack_text"]
    return send(store, m, deliver)


# ── The baseline ────────────────────────────────────────────────────────────

def baseline(store):
    return store.state_get("baseline")


def in_use(store):
    """The domains each account points at as of the last scan: [{accountId, name, platform, domains: [{domain, ads, known}]}], for the baseline view."""
    snap = (store.state_get("inuse") or {}).get("accounts") or {}
    entries = store.known_list()
    out = []
    for aid, a in snap.items():
        acct = {"id": aid, "platform": a["platform"], "group": a.get("group") or ""}
        out.append({"accountId": aid, "name": a["name"], "platform": a["platform"], "domains": sorted(
            ({"domain": d, "ads": n, "known": dom.judge(d, acct, entries)[0] == "ok"} for d, n in a["domains"].items()), key=lambda x: (x["known"], x["domain"]))})
    return sorted(out, key=lambda a: a["name"].lower())


def set_baseline(store, by, accounts, trust=(), *, now=None):
    """Mark the baseline set. `trust` is a list of account ids (or "all") whose domains in use at the last scan are added to the database, scoped to the
    account that uses them. Everything open is marked as already seen, so the first alert is about what turns up after this. Returns {trusted, added}."""
    snap = (store.state_get("inuse") or {}).get("accounts") or {}
    ids = list(snap) if trust == "all" else [t for t in trust if t in snap]
    rows = [{"domain": d, "scope": aid, "note": "In use when the baseline was set"} for aid in ids for d in snap[aid]["domains"]]
    result = store.known_bulk(rows, by) if rows else {"added": 0, "existing": 0}
    stamp = _iso(now)
    if not baseline(store):
        store.state_set("baseline", {"at": stamp, "by": by})
    _open_covered(store, store.known_list(), accounts, by, stamp)
    seen = []
    for f in store.findings_all():
        if f["status"] == "open" and not f.get("alertedAt"):
            seen.append(dict(f, alertedAt=stamp, alert={"baseline": True}))
    store.findings_put(seen)
    return {"trusted": len(ids), "added": result["added"], "existing": result["existing"]}


# ── What people do with a finding ───────────────────────────────────────────

def _open_covered(store, entries, accounts, by, stamp):
    """Mark approved every open finding the entries now cover."""
    by_id = {a["id"]: a for a in accounts}
    out = []
    for f in store.findings_all():
        a = by_id.get(f["accountId"])
        if f["status"] == "open" and a and dom.judge(f["host"], a, entries)[0] == "ok":
            out.append(dict(f, status="approved", decidedBy=by, decidedAt=stamp))
    store.findings_put(out)
    return len(out)


def approve(store, fingerprint, who, by, accounts, groups, *, now=None):
    """Add the finding's domain to the database for `who` ("account", "group" or "everyone") and approve every open finding it now covers: the number."""
    f = store.finding_get(fingerprint)
    if not f:
        raise BadRequest("That finding is not there.")
    account = next((a for a in accounts if a["id"] == f["accountId"]), None)
    if who == "everyone":
        scope = dom.EVERYONE
    elif who == "group":
        if not account or not account.get("group"):
            raise BadRequest("This account is not under a client (manager account), so it has no client to approve the domain for.")
        scope = account["group"]
    elif who == "account":
        scope = f["accountId"]
    else:
        raise BadRequest("Choose who the domain is for: this account, its client or everyone.")
    store.known_add(f["domain"], scope, "Approved from a finding", by)
    return _open_covered(store, store.known_list(), accounts, by, _iso(now)) or 0


def decide(store, fingerprint, status, by, *, now=None):
    """Dismiss a finding, or open it again."""
    if status not in ("dismissed", "open"):
        raise BadRequest("Unknown action.")
    f = store.finding_get(fingerprint)
    if not f:
        raise BadRequest("That finding is not there.")
    store.findings_put([dict(f, status=status, decidedBy=by, decidedAt=_iso(now))])


def add_known(store, text, scope, note, by, accounts, groups):
    d = dom.clean_domain(text)
    if not d:
        raise BadRequest("%s is not a domain." % (str(text)[:80] or "That"))
    sc = dom.clean_scope(scope, accounts, groups)
    if sc is None:
        raise BadRequest("That account or client is not one the platform knows.")
    row, created = store.known_add(d, sc, str(note or "")[:200], by)
    _open_covered(store, store.known_list(), accounts, by, _iso())          # what this domain now covers is no longer a finding
    return row, created


def import_known(store, rows, by, accounts, groups):
    """Add many entries ([{domain, scope, note}], scope as typed: blank means everyone). Rows that are not a domain or name an unknown account are
    listed with their line number, and the rest are added."""
    names = {}
    for x in list(accounts) + list(groups):
        names.setdefault(x["name"].strip().lower(), set()).add(x["id"])
    good, bad = [], []
    for i, r in enumerate(rows, 1):
        d = dom.clean_domain(r.get("domain"))
        typed = str(r.get("scope") or "").strip()
        named = names.get(typed.lower())
        sc = next(iter(named)) if named and len(named) == 1 else dom.clean_scope(typed, accounts, groups)
        if not d:
            bad.append({"row": i, "text": str(r.get("domain") or "")[:80], "problem": "not a domain"})
        elif sc is None:
            bad.append({"row": i, "text": str(r.get("scope") or "")[:80], "problem": "not an account or client the platform knows"})
        else:
            good.append({"domain": d, "scope": sc, "note": str(r.get("note") or "")[:200]})
    res = store.known_bulk(good, by) if good else {"added": 0, "existing": 0}
    if good:
        _open_covered(store, store.known_list(), accounts, by, _iso())
    return dict(res, skipped=bad[:50], skippedCount=len(bad))


# ── Importing a list ────────────────────────────────────────────────────────

MAX_ROWS = 5000
_DOMAIN_HEADS = ("domain", "domains", "website", "url", "site", "host")
_SCOPE_HEADS = ("scope", "account", "client", "owner", "for")
_NOTE_HEADS = ("note", "notes", "comment", "comments")


def parse_csv(text):
    """[{domain, scope, note}] from CSV text. With a header row (a column called domain, website, url...; optionally account or client, and note) the
    columns are found by name, without one they are domain, scope, note in that order. A client or account may be given by its name or its id."""
    rows = list(csv.reader(io.StringIO(str(text or "").lstrip(chr(0xFEFF)))))
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise BadRequest("There is nothing in that file.")
    head = [c.strip().lower() for c in rows[0]]
    if any(h in _DOMAIN_HEADS for h in head):
        def col(names):
            return next((i for i, h in enumerate(head) if h in names), None)
        di, si, ni = col(_DOMAIN_HEADS), col(_SCOPE_HEADS), col(_NOTE_HEADS)
        rows = rows[1:]
    else:
        di, si, ni = 0, 1, 2
    if len(rows) > MAX_ROWS:
        raise BadRequest("At most %d rows at a time." % MAX_ROWS)

    def cell(r, i):
        return r[i].strip() if i is not None and i < len(r) else ""
    return [{"domain": cell(r, di), "scope": cell(r, si), "note": cell(r, ni)} for r in rows]


def sheet_csv_url(link):
    """The CSV export address of a Google Sheet link (shared as anyone with the link can view)."""
    m = re.match(r"^https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{20,})", str(link or "").strip())
    if not m:
        raise BadRequest("That is not a Google Sheets link (https://docs.google.com/spreadsheets/d/...).")
    gid = re.search(r"[#&?]gid=(\d+)", str(link))
    return "https://docs.google.com/spreadsheets/d/%s/export?format=csv%s" % (m.group(1), ("&gid=" + gid.group(1)) if gid else "")


def fetch_sheet(link, get=None):
    """The CSV text of a shared Google Sheet."""
    import requests
    url = sheet_csv_url(link)
    try:
        r = (get or requests.get)(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
    except Exception as e:                                                          # noqa: BLE001
        raise BadRequest("The sheet could not be reached: %s" % type(e).__name__)
    if getattr(r, "status_code", 200) != 200 or "text/csv" not in (r.headers.get("Content-Type") or ""):
        raise BadRequest("That sheet could not be read. Share it as \"anyone with the link can view\" and try again.")
    if len(r.content) > 2 * 1024 * 1024:
        raise BadRequest("That sheet is over 2 MB.")
    return r.content.decode("utf-8", "replace")


def export_csv(entries, accounts, groups):
    """The database as CSV text: domain, scope (a name), scope id, note, added by."""
    names = {x["id"]: x["name"] for x in list(accounts) + list(groups)}
    out = io.StringIO()
    w = csv.writer(out, lineterminator=chr(10))

    def safe(v):                                  # a spreadsheet would run a cell that starts with = + - @ as a formula
        v = str(v or "")
        return "'" + v if v[:1] in ("=", "+", "-", "@") else v
    w.writerow(["domain", "for", "for_id", "note", "added_by"])
    for e in entries:
        w.writerow([e["domain"], "Everyone" if e["scope"] == dom.EVERYONE else names.get(e["scope"], e["scope"]), "" if e["scope"] == dom.EVERYONE else e["scope"],
                    safe(e.get("note")), safe(e.get("added_by"))])
    return out.getvalue()


# ── The scan ────────────────────────────────────────────────────────────────

def _progress(store, **kw):
    store.state_set("progress", kw)


def _keep_run(store, run):
    runs = [run] + (store.state_get("runs") or [])
    store.state_set("runs", runs[:RUNS_KEPT])


def run_scan(store, *, trigger, link, deliver, inventory_fn=None, read_fn=None, now_fn=None, workers=WORKERS):
    """One scan. Returns the run record ({status: "busy"} when another is going)."""
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    if not store.claim(LOCK, LOCK_TTL_S):
        return {"status": "busy"}
    started = now_fn()
    try:
        accounts, groups, problems = (inventory_fn or inventory)(store)
        read = read_fn or read_account
        _progress(store, running=True, startedAt=_iso(started), done=0, total=len(accounts))
        sightings, unreadable, counts, done = [], [], {"ads": 0, "assetGroups": 0, "assets": 0}, [0]
        ok_ids = set()
        stop = {"quota": False}

        def one(a):
            if stop["quota"] and a["platform"] == "google":
                return a, None, "The Google Ads daily read budget was used up earlier in this scan."
            try:
                s, c = read(a, store)
                return a, (s, c), None
            except Exception as e:                                                  # noqa: BLE001
                if getattr(e, "kind", "") == "quota":
                    stop["quota"] = True
                return a, None, str(e)[:240] or type(e).__name__

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for a, ok, err in pool.map(one, accounts):
                done[0] += 1
                if ok:
                    ok_ids.add(a["id"])
                    sightings += ok[0]
                    for k, v in ok[1].items():
                        counts[k] = counts.get(k, 0) + v
                else:
                    unreadable.append({"accountId": a["id"], "name": a["name"], "platform": a["platform"], "error": err})
                if done[0] % 5 == 0 or done[0] == len(accounts):
                    _progress(store, running=True, startedAt=_iso(started), done=done[0], total=len(accounts))

        entries = store.known_list()
        by_id = {a["id"]: a for a in accounts}
        found = dom.assess(sightings, entries, [a for a in accounts if a["id"] in ok_ids], groups)
        stamp = _iso(now_fn())
        existing = {f["fingerprint"]: f for f in store.findings_all()}
        seen, writes, new, reopened, gone = set(), [], [], 0, 0
        for f in found:
            seen.add(f["fingerprint"])
            old = existing.get(f["fingerprint"])
            if old is None:
                row = dict(f, status="open", firstSeen=stamp, lastSeen=stamp, alertedAt=None, alert={}, decidedBy=None, decidedAt=None)
                new.append(row)
            elif old["status"] == "dismissed":
                row = dict(old, lastSeen=stamp, url=f["url"], why=f["why"], owners=f["owners"], severity=f["severity"], kind=f["kind"])
            elif old["status"] in ("gone", "approved"):
                row = dict(f, status="open", firstSeen=old["firstSeen"], lastSeen=stamp, alertedAt=None, alert={}, decidedBy=None, decidedAt=None)
                reopened += 1
            else:
                row = dict(f, status="open", firstSeen=old["firstSeen"], lastSeen=stamp, alertedAt=old.get("alertedAt"), alert=old.get("alert") or {},
                           decidedBy=old.get("decidedBy"), decidedAt=old.get("decidedAt"))
            writes.append(row)
        for fp, old in existing.items():
            if fp not in seen and old["status"] == "open" and old["accountId"] in ok_ids:
                if dom.judge(old["host"], by_id[old["accountId"]], entries)[0] == "ok":          # approved by a domain added since, not fixed
                    writes.append(dict(old, status="approved", decidedBy=old.get("decidedBy") or "the domain database", decidedAt=stamp))
                else:
                    writes.append(dict(old, status="gone"))
                    gone += 1
        store.findings_put(writes)

        snap = (store.state_get("inuse") or {}).get("accounts") or {}
        use = dom.in_use(sightings)
        for aid in ok_ids:
            snap[aid] = {"name": by_id[aid]["name"], "platform": by_id[aid]["platform"], "group": by_id[aid].get("group") or "", "domains": use.get(aid, {})}
        store.state_set("inuse", {"at": stamp, "accounts": {k: v for k, v in snap.items() if k in by_id}})

        run = {"status": "done", "trigger": trigger, "startedAt": _iso(started), "finishedAt": stamp, "accounts": len(accounts), "read": len(ok_ids),
               "unreadable": unreadable[:30], "unreadableCount": len(unreadable), "problems": problems, "counts": counts, "sightings": len(sightings),
               "findings": len(found), "new": len(new), "reopened": reopened, "gone": gone, "baseline": bool(baseline(store)), "alerted": 0,
               "email": None, "slack": None, "takeover": []}
        if baseline(store):
            pending = [w for w in writes if w["status"] == "open" and not w.get("alertedAt")]
            if pending:
                pending.sort(key=lambda f: (dom.SEVERITY_ORDER[f["severity"]], f["accountName"].lower(), f["domain"]))
                take = dom.takeovers(pending)
                res = send(store, dom.digest(pending, link, take), deliver)
                run.update(email=res["email"], slack=res["slack"], takeover=take)
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

def snapshot(store, accounts, groups, problems, deliver):
    """Everything the page needs in one answer."""
    findings = store.findings_all()
    order = {"open": 0, "approved": 1, "dismissed": 2, "gone": 3}
    findings.sort(key=lambda f: (order.get(f["status"], 9), dom.SEVERITY_ORDER.get(f["severity"], 9), f["accountName"].lower(), f["domain"]))
    counts = {s: sum(1 for f in findings if f["status"] == s) for s in order}
    runs = store.state_get("runs") or []
    return {"accounts": accounts, "groups": groups, "problems": problems, "known": store.known_list(), "findings": findings, "counts": counts,
            "settings": settings(store), "senders": deliver.ready(), "baseline": baseline(store), "inUse": in_use(store), "runs": runs[:10],
            "progress": store.state_get("progress") or {}}
