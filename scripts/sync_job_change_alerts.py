#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Syncs Job Change Alert events from Slack's #job_change_alert_apollo channel
into data/job_change_alerts.db (+ the data/job_change_alerts_manual.json ledger,
this feature's source of truth -- same role as data/northstar_signals_manual.json).

Two ways to feed it messages:
  --from-file PATH   Offline backfill: reads a pre-fetched channel dump, either the
                      slack_read_channel tool's {"messages": "<transcript>"} shape,
                      or a plain JSON list of {"ts": ..., "text": ...} (Slack's own
                      conversations.history shape).
  (default)          Calls Slack's conversations.history API live via SLACK_BOT_TOKEN,
                      picking up from the newest event already stored.

Never raises: a missing/under-scoped SLACK_BOT_TOKEN, the bot not being a member of
the channel, or any network error is logged clearly and the script exits 0 having
changed nothing -- the page this feeds must keep serving the last-known-good data
regardless of whether the live sync can run.
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

import requests

from tracker.job_change_parser import parse_job_change_message
from tracker.job_change_store import JobChangeStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sync_job_change_alerts")

CHANNEL_ID = "C0AUUH7BNUA"  # #job_change_alert_apollo
DB_PATH = ROOT / "data" / "job_change_alerts.db"
LEDGER_PATH = ROOT / "data" / "job_change_alerts_manual.json"

_MSG_HEADER_RE = re.compile(r"^=== Message from (.+?) \((\S+)\) at (.+?) IST ===\s*\n", re.M)


def _permalink(ts: str) -> str:
    return f"https://positionsquared.slack.com/archives/{CHANNEL_ID}/p{ts.replace('.', '')}"


def _iso_to_slack_ts(iso_str: str) -> str | None:
    try:
        return str(datetime.fromisoformat(iso_str).timestamp())
    except (TypeError, ValueError):
        return None


def _messages_from_dump_file(path: Path) -> list[dict]:
    raw = json.loads(path.read_text())
    if isinstance(raw, list):
        return raw
    text = raw.get("messages", "") if isinstance(raw, dict) else ""
    parts = _MSG_HEADER_RE.split(text)
    out = []
    for i in range(1, len(parts), 4):
        uid, body = parts[i + 1], parts[i + 3]
        body = html.unescape(body)
        m = re.match(r"^Message TS: (\S+)\n(.*)$", body, re.S)
        if not m:
            continue
        out.append({"ts": m.group(1), "text": m.group(2), "user": uid})
    return out


def _messages_from_slack_api(oldest: str | None) -> list[dict]:
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        log.info("SLACK_BOT_TOKEN not set -- skipping live sync (backfilled data still serves fine).")
        return []
    messages, cursor = [], None
    try:
        while True:
            params = {"channel": CHANNEL_ID, "limit": 200}
            if oldest:
                params["oldest"] = oldest
            if cursor:
                params["cursor"] = cursor
            r = requests.get(
                "https://slack.com/api/conversations.history",
                headers={"Authorization": f"Bearer {token}"},
                params=params, timeout=15,
            )
            data = r.json()
            if not data.get("ok"):
                log.warning(
                    "Slack conversations.history failed: %s -- likely missing "
                    "channels:history scope or the bot isn't a member of "
                    "#job_change_alert_apollo. Backfilled data still serves fine.",
                    data.get("error"))
                return []
            messages.extend(data.get("messages", []))
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
    except Exception as e:
        log.warning("Slack conversations.history error: %s -- skipping live sync.", e)
        return []
    return messages


def _load_ledger() -> dict:
    if LEDGER_PATH.exists():
        return json.loads(LEDGER_PATH.read_text())
    return {
        "_readme": (
            "Source-of-truth ledger for the Job Change Alert page "
            "(/p2/b2b-agents/job-change-alert). scripts/sync_job_change_alerts.py "
            "appends new events here on every sync and keeps data/job_change_alerts.db "
            "in lockstep -- this file is the one to hand-edit/audit, the .db is derived."
        ),
        "events": [],
    }


def _save_ledger(ledger: dict) -> None:
    LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_PATH.write_text(json.dumps(ledger, indent=2, ensure_ascii=False) + "\n")


def sync(from_file: str | None = None) -> dict:
    store = JobChangeStore(DB_PATH)
    ledger = _load_ledger()
    known_ids = {e.get("apollo_contact_id") for e in ledger["events"] if e.get("apollo_contact_id")}

    if from_file:
        raw_messages = _messages_from_dump_file(Path(from_file))
    else:
        latest = store.get_latest_detected_at()
        raw_messages = _messages_from_slack_api(_iso_to_slack_ts(latest) if latest else None)

    added = 0
    for m in raw_messages:
        ts = m.get("ts") or m.get("message_ts") or ""
        event = parse_job_change_message(m.get("text", ""), ts, _permalink(ts) if ts else "")
        if not event:
            continue
        if store.upsert_event(event):
            added += 1
            if not event.get("apollo_contact_id") or event["apollo_contact_id"] not in known_ids:
                ledger["events"].append(event)
                if event.get("apollo_contact_id"):
                    known_ids.add(event["apollo_contact_id"])

    if added:
        ledger["events"].sort(key=lambda e: e.get("detected_at") or "")
        _save_ledger(ledger)

    result = {"checked": len(raw_messages), "added": added, "total": store.count()}
    log.info("Job Change Alert sync: %s", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-file", help="Offline backfill from a saved channel dump instead of live Slack")
    args = parser.parse_args()
    _result = sync(from_file=args.from_file)
    # The Flask route that triggers this as a subprocess parses the last stdout
    # line as JSON to report the outcome back to the caller.
    print(json.dumps(_result))
