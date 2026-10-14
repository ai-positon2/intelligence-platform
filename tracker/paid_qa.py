"""Paid Marketing Agents: QA Agent, the checklist and its verdict.

The PPC team's "Campaign Launch QC Checklist" (155 checks in 15 sections), run
on a live campaign: pick an account, pick a campaign, and the QC starts on the
shared checklist. Each check is right or wrong, with a comment beside it. For
the checks Google Ads can answer, tracker/paid_qa_auto.py reads the campaign and
prints what it found; this module is the rules, with no database in it
(tracker/paid_qa_store.py keeps what was saved):

  * which checks show for a campaign (its own type, and the client type if one
    is chosen: checks that exist only for Ecommerce or Lead Gen clients wait
    until that is said, rather than blocking a verdict they do not belong to);
  * what a person's answer may look like (wrong needs a reason);
  * how the agent's reading and a person's answer combine (a person wins);
  * the workbook's verdict, a rule over the Critical checks only:

        any Critical check wrong                 NOT READY: Critical check failed
        else any Critical check not yet done     INCOMPLETE: Critical checks pending
        else                                     READY TO LAUNCH

    A Critical check marked N/A counts as done. Major and Minor failures never
    block the verdict; they are counted and listed.

The checklist itself is tracker/qa_checklist.json, converted from the workbook.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from functools import lru_cache
from pathlib import Path

CHECKLIST_PATH = Path(__file__).resolve().parent / "qa_checklist.json"
STATUSES = ("pass", "fail", "na")
MAX_NAME = 120
MAX_NOTE = 2000
MIN_REASON = 3
STALE_REVIEW_S = 600
VERDICTS = {
    "not_ready": "NOT READY: Critical check failed",
    "incomplete": "INCOMPLETE: Critical checks pending",
    "ready": "READY TO LAUNCH",
}
CUSTOMER_ID_RE = re.compile(r"^\d{10}$")


class BadPayload(ValueError):
    """What is wrong with a request, in words the page can show."""


@lru_cache(maxsize=1)
def checklist():
    with open(CHECKLIST_PATH, encoding="utf-8") as f:
        return json.load(f)


def items():
    return checklist()["items"]


@lru_cache(maxsize=1)
def _by_id():
    return {i["id"]: i for i in items()}


def item(check_id):
    return _by_id().get(str(check_id))


# ── Which checks apply ──────────────────────────────────────────────────────

def applies(it, client_types, campaign_types):
    """A check shows when its Campaign Types say All or share a value with the
    campaign (an unknown campaign type narrows nothing), and its Client Types
    say All or share a value with the client types chosen. Until a client type
    is chosen, only the checks for every client show."""
    camp = "All" in it["campaignTypes"] or not campaign_types or bool(set(it["campaignTypes"]) & set(campaign_types))
    client = "All" in it["clientTypes"] or bool(set(it["clientTypes"]) & set(client_types or []))
    return camp and client


def applicable(client_types, campaign_types):
    return [i for i in items() if applies(i, client_types, campaign_types)]


def campaign_types_for(channel, dsa=False):
    """The checklist's campaign types for a Google Ads channel type."""
    label = {"SEARCH": "Search", "PERFORMANCE_MAX": "PMax", "SHOPPING": "Shopping", "DISPLAY": "Display",
             "DEMAND_GEN": "Demand Gen", "VIDEO": "Video", "MULTI_CHANNEL": "App"}.get(channel or "")
    if not label:
        return []
    return [label] + (["Search (DSA)"] if dsa and label == "Search" else [])


# ── Validation ──────────────────────────────────────────────────────────────

def _pick(values, allowed, what):
    if values is None:
        return []
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise BadPayload("%s should be a list" % what)
    bad = [v for v in values if v not in allowed]
    if bad:
        raise BadPayload("%s has a value this does not know: %s" % (what, ", ".join(bad[:3])))
    return [v for v in allowed if v in values]          # the checklist's own order, no duplicates


def _text(v, what, limit, required=False):
    if v is None:
        v = ""
    if not isinstance(v, str):
        raise BadPayload("%s should be text" % what)
    v = v.strip()
    if required and not v:
        raise BadPayload("%s is needed" % what)
    if len(v) > limit:
        raise BadPayload("%s is over %d characters" % (what, limit))
    return v


def customer_id(value):
    """A 10-digit Google Ads customer id from '123-456-7890' or '1234567890'."""
    digits = re.sub(r"\D", "", str(value or ""))
    if not CUSTOMER_ID_RE.match(digits):
        raise BadPayload("The Google Ads customer id should be 10 digits")
    return digits


CHANNELS = ("SEARCH", "PERFORMANCE_MAX", "SHOPPING", "DISPLAY", "DEMAND_GEN", "VIDEO", "MULTI_CHANNEL")
CAMPAIGN_ID_RE = re.compile(r"^\d{1,20}$")


def clean_start(body):
    """The account and campaign a QC is started on, checked."""
    if not isinstance(body, dict):
        raise BadPayload("the body should be a JSON object")
    campaign = re.sub(r"\D", "", str(body.get("campaignId") or ""))
    if not CAMPAIGN_ID_RE.match(campaign):
        raise BadPayload("Pick a campaign")
    channel = body.get("channel") or ""
    if channel and channel not in CHANNELS:
        channel = ""                                    # a type this list does not map: no narrowing
    return {"account_id": customer_id(body.get("accountId")),
            "account_name": _text(body.get("accountName"), "The account name", MAX_NAME, required=True),
            "campaign_id": campaign,
            "campaign_name": _text(body.get("campaignName"), "The campaign name", MAX_NAME, required=True),
            "channel": channel}


def clean_client_types(body):
    if not isinstance(body, dict):
        raise BadPayload("the body should be a JSON object")
    return _pick(body.get("clientTypes"), checklist()["clientTypes"], "Client types")


def clean_check(launch, check_id, body):
    """(status, note) to store for one check, or (None, "") to clear it.
    Raises BadPayload. A Fail needs a reason: the workbook asks for the exact
    campaign or ad group and the fix needed, and a bare cross helps nobody."""
    it = item(check_id)
    if not it:
        raise BadPayload("That is not a check on this checklist")
    if not applies(it, launch["client_types"], launch["campaign_types"]):
        raise BadPayload("That check does not apply to this launch")
    if not isinstance(body, dict):
        raise BadPayload("the body should be a JSON object")
    status = body.get("status")
    if status is None:
        return None, ""
    if status not in STATUSES:
        raise BadPayload("status should be pass, fail or na (or null to clear the check)")
    note = _text(body.get("note"), "The comment", MAX_NOTE)
    if status == "fail" and len(note) < MIN_REASON:
        raise BadPayload("A failed check needs a reason: say what is wrong and what needs fixing")
    return status, note


# ── Progress and the verdict ────────────────────────────────────────────────

def _blank():
    return {"total": 0, "pass": 0, "fail": 0, "na": 0, "pending": 0}


def summarize(launch, checks):
    """Counts, progress and the verdict for one launch. `checks` is
    {check_id: {"status": ...}}. Ticks on checks that no longer apply (the
    launch's details changed) are kept in storage but do not count."""
    cl = checklist()
    rows = applicable(launch["client_types"], launch["campaign_types"])
    by_sev = {s: _blank() for s in cl["severities"]}
    by_sec = {s: _blank() for s in cl["sections"]}
    failed, pending_critical, failed_critical, unverified = [], [], [], []
    for it in rows:
        st = (checks.get(it["id"]) or {}).get("status")
        key = st if st in STATUSES else "pending"
        for bucket in (by_sev[it["severity"]], by_sec[it["section"]]):
            bucket["total"] += 1
            bucket[key] += 1
        if key == "fail":
            failed.append(it["id"])
            if it["severity"] == "Critical":
                failed_critical.append(it["id"])
        elif key == "pending" and it["severity"] == "Critical":
            pending_critical.append(it["id"])
        if key == "pending":
            unverified.append(it["id"])
    total = len(rows)
    pending = sum(b["pending"] for b in by_sev.values())
    if failed_critical:
        code = "not_ready"
    elif pending_critical:
        code = "incomplete"
    else:
        code = "ready"
    return {
        "verdict": {"code": code, "text": VERDICTS[code]},
        "total": total, "checked": total - pending, "pending": pending,
        "percent": int(round(100.0 * (total - pending) / total)) if total else 0,
        "bySeverity": by_sev,
        "bySection": [dict(section=s, **by_sec[s]) for s in cl["sections"] if by_sec[s]["total"]],
        "failed": failed, "failedCritical": failed_critical, "pendingCritical": pending_critical,
        "needsHuman": unverified, "applicable": [i["id"] for i in rows],
    }


# ── A person's answer and the agent's reading, together ─────────────────────

def effective(launch, people, auto, auto_at=None):
    """What each check shows and counts as. `people` is {check_id: {status,
    note, by, at}} (what was clicked), `auto` is {check_id: {result, found}}
    (what the agent read). A person's answer always wins; clearing it returns
    the check to the agent's reading. The agent only ever supplies pass or fail,
    and only for the checks that apply."""
    out = {}
    for it in applicable(launch["client_types"], launch["campaign_types"]):
        cid = it["id"]
        mine, read = people.get(cid) or {}, (auto or {}).get(cid) or {}
        row = {"status": None, "note": "", "by": None, "at": None, "source": None, "found": read.get("found") or "",
               "fix": read.get("fix") or "", "evidence": read.get("evidence") or "", "facts": read.get("facts") or "",
               "by_what": read.get("source"), "unverified": bool(read.get("unverified"))}
        if mine.get("status") in STATUSES:
            row.update(status=mine["status"], note=mine.get("note") or "", by=mine.get("by"), at=mine.get("at"), source="person")
        elif read.get("result") in ("pass", "fail"):
            row.update(status=read["result"], by="QA Agent", at=auto_at, source=read.get("source") or "ai")
        if row["status"] or row["found"]:
            out[cid] = row
    return out


def hidden_by_client(launch):
    """The checks this campaign has that wait on a client type being chosen."""
    shown = {i["id"] for i in applicable(launch["client_types"], launch["campaign_types"])}
    rest = [i for i in applicable(checklist()["clientTypes"], launch["campaign_types"]) if i["id"] not in shown]
    return {"count": len(rest), "critical": sum(1 for i in rest if i["severity"] == "Critical"),
            "types": sorted({t for i in rest for t in i["clientTypes"]})}


# ── What the page is sent ───────────────────────────────────────────────────

def public_launch(l):
    """A stored launch with the page's field names."""
    return {"id": l["id"], "accountId": l["account_id"], "accountName": l["account_name"],
            "campaignId": l["campaign_id"], "campaignName": l["campaign_name"], "channel": l["channel"],
            "campaignTypes": l["campaign_types"], "clientTypes": l["client_types"],
            "readAt": l.get("auto_at"), "review": review_view(l), "createdBy": l["created_by"],
            "createdAt": l["created_at"], "updatedAt": l["updated_at"]}


def review_view(l, now=None):
    """Where the AI's review of this QC stands, for the page: status (running, done,
    error or empty), what it is doing, how far, and anything it could not do."""
    status = l.get("review_status") or ""
    prog = l.get("review_progress") or {}
    err = l.get("review_error") or ""
    if status == "running" and l.get("review_started"):
        started = dt.datetime.fromisoformat(l["review_started"])
        age = ((now or dt.datetime.now(dt.timezone.utc)) - started).total_seconds()
        if age > STALE_REVIEW_S:
            status, err = "error", "The review stopped before it finished (the server restarted). Run it again."
    return {"status": status, "phase": prog.get("phase") or "", "done": prog.get("done") or 0, "total": prog.get("total") or 0,
            "error": err, "note": l.get("review_note") or ""}


def brief(summary):
    """The part of a summary a list of QCs needs."""
    return {"verdict": summary["verdict"], "percent": summary["percent"], "checked": summary["checked"],
            "total": summary["total"], "failedCount": len(summary["failed"]),
            "failedCritical": len(summary["failedCritical"])}


def payload(launch, people):
    """Everything the page needs for one QC: the launch, every check's state,
    and the verdict."""
    rows = effective(launch, people, launch.get("auto") or {}, launch.get("auto_at"))
    return {"launch": public_launch(launch), "checks": rows, "summary": summarize(launch, rows),
            "needsClientType": hidden_by_client(launch)}
