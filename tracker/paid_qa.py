"""Paid Marketing Agents: QA Agent, the checklist and its verdict.

The PPC team's "Campaign Launch QC Checklist" (155 checks in 15 sections), run
inside the platform: pick an account, set what is launching, tick each check
Pass, Fail or N/A, and read off whether the launch can go. This module is the
rules, with no database in it (tracker/paid_qa_store.py keeps what was ticked):

  * which checks apply to a launch;
  * what a ticked check may look like (a Fail must say why);
  * the workbook's verdict, which is a rule over the Critical checks only:

        any Critical check Fail                  NOT READY: Critical check failed
        else any Critical check not yet ticked   INCOMPLETE: Critical checks pending
        else                                     READY TO LAUNCH

    A Critical check marked N/A counts as done. Major and Minor failures never
    block the verdict; they are counted and listed so they are fixed (Major
    before launch or within 24 hours with a note).

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
    """A check applies when its Campaign Types and its Client Types each either
    say All, or share a value with the launch. An empty choice (nothing picked
    yet) does not narrow that side, so the full list shows until you say what
    is launching."""
    def side(have, want):
        return "All" in have or not want or bool(set(have) & set(want))
    return side(it["campaignTypes"], campaign_types) and side(it["clientTypes"], client_types)


def applicable(client_types, campaign_types):
    return [i for i in items() if applies(i, client_types, campaign_types)]


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


def clean_details(body, *, creating):
    """The launch fields in a request, checked. On create the account and a
    launch name are required; on update only what is sent changes."""
    if not isinstance(body, dict):
        raise BadPayload("the body should be a JSON object")
    cl = checklist()
    out = {}
    if creating:
        out["account_id"] = customer_id(body.get("accountId"))
        out["account_name"] = _text(body.get("accountName"), "The account name", MAX_NAME, required=True)
    if creating or "name" in body:
        out["name"] = _text(body.get("name"), "The launch name", MAX_NAME, required=True)
    if "clientTypes" in body or creating:
        out["client_types"] = _pick(body.get("clientTypes"), cl["clientTypes"], "Client types")
    if "campaignTypes" in body or creating:
        out["campaign_types"] = _pick(body.get("campaignTypes"), cl["campaignTypes"], "Campaign types")
    if "plannedDate" in body or creating:
        d = _text(body.get("plannedDate"), "The planned launch date", 10)
        if d:
            try:
                dt.date.fromisoformat(d)
            except ValueError:
                raise BadPayload("The planned launch date should be YYYY-MM-DD")
        out["planned_date"] = d
    for key, field, label in (("qcBy", "qc_by", "QC done by"), ("approvedBy", "approved_by", "Reviewed / approved by")):
        if key in body or creating:
            out[field] = _text(body.get(key), label, MAX_NAME)
    return out


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
    failed, pending_critical, failed_critical = [], [], []
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
        "applicable": [i["id"] for i in rows],
    }


# ── What the page is sent ───────────────────────────────────────────────────

def public_launch(l):
    """A stored launch with the page's field names."""
    return {"id": l["id"], "accountId": l["account_id"], "accountName": l["account_name"], "name": l["name"],
            "clientTypes": l["client_types"], "campaignTypes": l["campaign_types"],
            "plannedDate": l["planned_date"], "qcBy": l["qc_by"], "approvedBy": l["approved_by"],
            "createdBy": l["created_by"], "createdAt": l["created_at"], "updatedAt": l["updated_at"]}


def brief(summary):
    """The part of a summary a list of launches needs."""
    return {"verdict": summary["verdict"], "percent": summary["percent"], "checked": summary["checked"],
            "total": summary["total"], "failedCount": len(summary["failed"]),
            "failedCritical": len(summary["failedCritical"])}


def preview(client_csv, campaign_csv):
    """How many checks a launch with these details would have, before it is
    created: the new-launch form shows it as you pick."""
    cl = checklist()
    split = lambda s: [v.strip() for v in (s or "").split(",") if v.strip()]
    rows = applicable(_pick(split(client_csv), cl["clientTypes"], "Client types"),
                      _pick(split(campaign_csv), cl["campaignTypes"], "Campaign types"))
    return {"count": len(rows),
            "bySeverity": {s: sum(1 for i in rows if i["severity"] == s) for s in cl["severities"]},
            "applicable": [i["id"] for i in rows]}
