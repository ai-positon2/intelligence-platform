"""Send a weekly signal summary to Slack via an Incoming Webhook."""

from __future__ import annotations

import json
import logging
import urllib.request
import urllib.error
from collections import Counter

logger = logging.getLogger(__name__)


def send_weekly_summary(
    changes: list,          # list[ChangeEvent]
    company_count: int,
    webhook_url: str,
    dashboard_url: str = "",
    dry_run: bool = False,
) -> None:
    """Post a concise weekly summary to Slack.

    Format:
        5 new Funding signals, 3 new IPO signals, 7 C-suite changes
        and 13 new signals tracked this week.

        🔗 Dashboard: <url>
    """
    if not changes:
        logger.info("No signals this run — skipping Slack notification")
        return

    if not webhook_url or webhook_url == "YOUR_SLACK_WEBHOOK_URL":
        logger.warning("Slack webhook URL not configured — skipping notification")
        return

    # Count by signal type
    type_counts: Counter = Counter(e.signal_type for e in changes)
    total = len(changes)

    # Build human-readable signal breakdown
    # Map signal types to friendly labels
    LABELS = {
        "Funding Round":    "Funding signal",
        "IPO Signal":       "IPO signal",
        "C-Suite Join":     "C-suite join",
        "C-Suite Exit":     "C-suite exit",
        "Acquisition / M&A":"M&A signal",
        "Subsidiary Change":"Subsidiary change",
        "News Mention":     "News mention",
    }

    parts = []
    for sig_type, label in LABELS.items():
        count = type_counts.get(sig_type, 0)
        if count:
            plural = label + ("s" if count != 1 else "")
            parts.append(f"*{count}* new {plural}")

    # Any unlabelled signal types
    for sig_type, count in type_counts.items():
        if sig_type not in LABELS and count:
            parts.append(f"*{count}* {sig_type}")

    # Join with commas, last item with "and"
    if len(parts) > 1:
        summary_line = ", ".join(parts[:-1]) + f" and {parts[-1]}"
    elif parts:
        summary_line = parts[0]
    else:
        summary_line = f"*{total}* new signals"

    summary_line += f" — *{total} signals* tracked this week across *{company_count:,}* companies."

    # Build Slack Block Kit message
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":bar_chart: *Weekly Signal Tracker Update*\n\n{summary_line}",
            },
        },
    ]

    if dashboard_url and dashboard_url != "YOUR_DASHBOARD_URL":
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":link: *Dashboard:* {dashboard_url}",
            },
        })

    blocks.append({"type": "divider"})

    payload = {"blocks": blocks}

    if dry_run:
        logger.info("[DRY RUN] Slack message:\n%s", json.dumps(payload, indent=2))
        return

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
        if status == 200:
            logger.info("Slack weekly summary sent (%d signals)", total)
        else:
            logger.warning("Slack webhook returned status %d", status)
    except urllib.error.URLError as exc:
        logger.error("Failed to send Slack notification: %s", exc)
