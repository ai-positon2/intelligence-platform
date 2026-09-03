"""Parses Apollo's "Job_change_alert_apollo_database" Slack notification into a
job-change-event dict. Pure function, no I/O -- the message text is Apollo's own
fixed template, e.g.:

    We found *someone* who recently changed their job: *<url|Job_change_alert_apollo_database>*

    >*Name:* <url|First> <url|Last>
    >*Title:* Head of Payer Partnerships
    >*Company:* <url|Spring Care Inc>
    >*Company industry:* mental health care
    >*Company description: *A paragraph, sometimes spanning several lines...
    >*City:* New York
    >*# Employees:* 2600
    >*Revenue:* $281.3M
    >*LinkedIn*: <http://www.linkedin.com/in/dmullaney>
    >*Current job start date:* Jul 01, 2026

Two quirks this has to handle: the bold/colon placement is inconsistent
("*Name:*" vs "*LinkedIn*:" vs "*Company description: *" with a trailing space
before the closing star), and "Company description" is the only field that can
wrap across multiple lines. Any field Apollo couldn't resolve comes through as
the literal text "[Unavailable]" (sometimes inside an empty link, "<|[Unavailable]>"
for Company) -- normalized to None here so the UI never has to special-case it.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

_HEADER_RE = re.compile(r"^We found \*someone\* who recently changed their job", re.I)
_FIELD_LINE_RE = re.compile(r"^>\s*\*([^*]+?)\*:?\s*(.*)$")
_LINK_RE = re.compile(r"<([^|>]*)(?:\|([^>]*))?>")
_EMOJI_SHORTCODE_RE = re.compile(r":[a-z0-9_+\-]+:")
_CONTACT_ID_RE = re.compile(r"/contacts/([a-f0-9]+)", re.I)
_ACCOUNT_ID_RE = re.compile(r"/accounts/([a-f0-9]+)", re.I)
_TRAILING_NOISE = {"see contact button"}

_MULTILINE_FIELDS = {"company description"}


def _links(value: str) -> list[tuple[str, str | None]]:
    return [(url.strip(), (label.strip() if label is not None else None))
            for url, label in _LINK_RE.findall(value)]


def _clean_slack_text(value: str) -> str:
    """Strips Slack mrkdwn from free-text fields (only "company description"
    today): "<url|label>" -> "label" (or the bare url if there's no label),
    ":emoji_shortcode:" -> removed, then whitespace is collapsed. Apollo's
    own company-description text sometimes embeds these verbatim -- e.g.
    ":globe_with_meridians: <http://x.com|x.com>" -- and without this the
    raw mrkdwn leaked straight into the UI."""
    value = _LINK_RE.sub(lambda m: m.group(2) if m.group(2) else m.group(1), value)
    value = _EMOJI_SHORTCODE_RE.sub("", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _clean_plain(value: str) -> str | None:
    value = value.strip()
    if not value or value == "[Unavailable]":
        return None
    return value


def parse_job_change_message(text: str, message_ts: str, permalink: str = "") -> dict | None:
    """Returns a flat job-change-event dict, or None if `text` isn't one of
    Apollo's job-change notifications (e.g. a human reply or a join message)."""
    if not text or not _HEADER_RE.search(text.strip()):
        return None

    fields: dict[str, str] = {}
    current_label = None
    for line in text.split("\n"):
        m = _FIELD_LINE_RE.match(line)
        if m:
            current_label = m.group(1).strip().rstrip(":").strip().lower()
            fields[current_label] = m.group(2)
        elif current_label is not None and current_label in _MULTILINE_FIELDS:
            stripped = line.strip()
            if stripped and stripped.lower() not in _TRAILING_NOISE:
                fields[current_label] += "\n" + line
        # Non-field lines outside a multiline field (e.g. the trailing blank
        # line + "See contact button" footer) are simply dropped.

    name_links = _links(fields.get("name", ""))
    contact_id = None
    person_name = None
    if name_links:
        contact_id_match = _CONTACT_ID_RE.search(name_links[0][0])
        contact_id = contact_id_match.group(1) if contact_id_match else None
        person_name = " ".join(label for _, label in name_links if label) or None

    company_links = _links(fields.get("company", ""))
    account_id = None
    company_name = None
    if company_links:
        account_id_match = _ACCOUNT_ID_RE.search(company_links[0][0])
        account_id = account_id_match.group(1) if account_id_match else None
        company_name = _clean_plain(company_links[0][1] or "")
    else:
        company_name = _clean_plain(fields.get("company", ""))

    linkedin_links = _links(fields.get("linkedin", ""))
    linkedin_url = linkedin_links[0][0] if linkedin_links and linkedin_links[0][0] else None

    try:
        detected_at = datetime.fromtimestamp(float(message_ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        detected_at = None

    return {
        "apollo_contact_id": contact_id,
        "person_name": person_name,
        "linkedin_url": linkedin_url,
        "new_title": _clean_plain(fields.get("title", "")),
        "new_company_name": company_name,
        "apollo_account_id": account_id,
        "company_industry": _clean_plain(fields.get("company industry", "")),
        "company_description": _clean_plain(_clean_slack_text(fields.get("company description", ""))),
        "city": _clean_plain(fields.get("city", "")),
        "employees": _clean_plain(fields.get("# employees", "")),
        "revenue": _clean_plain(fields.get("revenue", "")),
        "job_start_date": _clean_plain(fields.get("current job start date", "")),
        "detected_at": detected_at,
        "slack_message_ts": message_ts,
        "slack_permalink": permalink or None,
    }
