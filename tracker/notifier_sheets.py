"""Google Sheets notifier — appends ChangeEvents and refreshes company roster."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from tracker.change_detector import ChangeEvent

logger = logging.getLogger(__name__)

_CHANGE_LOG_TAB = "Change Log"
_COMPANY_LIST_TAB = "Company List"

# Printed once per run so the terminal isn't spammed per-company.
_skip_message_printed = False


def _is_configured(sheet_id: str, creds_path: str) -> bool:
    """Return True only if both the sheet ID and credentials file look valid."""
    global _skip_message_printed
    if not sheet_id or sheet_id == "YOUR_GOOGLE_SHEET_ID" or not Path(creds_path).exists():
        if not _skip_message_printed:
            print("[Sheets] Skipped — credentials not configured")
            _skip_message_printed = True
        return False
    return True

_CHANGE_LOG_HEADERS = [
    "Date", "Company", "Domain", "Signal Type", "Severity",
    "Headline", "Detail", "Previous Value", "New Value",
    "Apollo ID", "Detected At",
]

_COMPANY_LIST_HEADERS = [
    "Apollo ID", "Company Name", "Domain", "Industry", "HQ Location",
    "Employees", "Revenue Band", "Funding Stage", "First Seen", "Last Seen", "Active",
]


def _get_sheet(sheet_id: str, creds_path: str):
    """Return an authenticated gspread Spreadsheet object."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials  # type: ignore
    except ImportError as exc:
        raise ImportError("gspread and google-auth required: pip install gspread google-auth") from exc

    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id)


def _ensure_tab(spreadsheet, tab_name: str, headers: list[str]):
    """Create tab with headers if it doesn't exist."""
    try:
        ws = spreadsheet.worksheet(tab_name)
    except Exception:
        ws = spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=len(headers))
        ws.append_row(headers, value_input_option="RAW")
    return ws


def append_change_event(
    event: ChangeEvent,
    sheet_id: str,
    creds_path: str,
    dry_run: bool = False,
) -> None:
    """Append one row to the Change Log tab."""
    today = datetime.now(timezone.utc).date().isoformat()
    row = [
        today,
        event.company_name,
        event.company_domain,
        event.signal_type,
        event.severity,
        event.headline,
        event.detail,
        event.previous_value,
        event.new_value,
        event.apollo_id,
        event.detected_at,
    ]

    if dry_run:
        logger.info("[DRY RUN] Sheets append: %s", row)
        return

    if not _is_configured(sheet_id, creds_path):
        return

    try:
        spreadsheet = _get_sheet(sheet_id, creds_path)
        ws = _ensure_tab(spreadsheet, _CHANGE_LOG_TAB, _CHANGE_LOG_HEADERS)
        ws.append_row(row, value_input_option="USER_ENTERED")
        logger.info("Appended change event to Sheets: %s / %s", event.company_name, event.signal_type)
    except Exception as exc:
        logger.error("Failed to append to Google Sheet: %s", exc)


def refresh_company_list(
    companies: list[dict],
    sheet_id: str,
    creds_path: str,
    dry_run: bool = False,
) -> None:
    """Overwrite the Company List tab with the current company roster."""
    rows = [_COMPANY_LIST_HEADERS]
    for c in companies:
        rows.append([
            c.get("apollo_id", ""),
            c.get("name", ""),
            c.get("domain", ""),
            c.get("industry", ""),
            c.get("hq_location", ""),
            c.get("num_employees", ""),
            c.get("revenue_band", ""),
            c.get("funding_stage", ""),
            c.get("first_seen_at", "")[:10] if c.get("first_seen_at") else "",
            c.get("last_enriched_at", "")[:10] if c.get("last_enriched_at") else "",
            "Y" if c.get("is_active", 1) else "N",
        ])

    if dry_run:
        logger.info("[DRY RUN] Sheets company list refresh: %d companies", len(companies))
        return

    if not _is_configured(sheet_id, creds_path):
        return

    try:
        spreadsheet = _get_sheet(sheet_id, creds_path)
        try:
            ws = spreadsheet.worksheet(_COMPANY_LIST_TAB)
            ws.clear()
        except Exception:
            ws = spreadsheet.add_worksheet(title=_COMPANY_LIST_TAB, rows=max(1000, len(rows) + 10), cols=len(_COMPANY_LIST_HEADERS))
        ws.update("A1", rows, value_input_option="USER_ENTERED")
        logger.info("Company list tab refreshed (%d rows).", len(rows) - 1)
    except Exception as exc:
        logger.error("Failed to refresh company list in Google Sheet: %s", exc)


def get_existing_company_ids(sheet_id: str, creds_path: str) -> list[str]:
    """Read Apollo IDs from the Company List tab for dedup."""
    if not _is_configured(sheet_id, creds_path):
        return []
    try:
        spreadsheet = _get_sheet(sheet_id, creds_path)
        ws = spreadsheet.worksheet(_COMPANY_LIST_TAB)
        col_values = ws.col_values(1)  # Column A = Apollo ID
        return [v for v in col_values[1:] if v]  # skip header
    except Exception as exc:
        logger.warning("Could not read existing company IDs from Sheets: %s", exc)
        return []
