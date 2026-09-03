"""Google Sheets client — reads signal data from user-maintained sheets.

Each HIGH signal type has its own dedicated Google Sheet. This module:
  1. Reads all rows from each sheet once per run (fetch_all_signals).
  2. Filters rows down to a specific company (get_company_signals).

Expected column layouts per sheet
──────────────────────────────────
Funding:
    Company Name | Domain | Funding Type | Funding Amount (USD) | Raised At | Notes

C-Suite Changes:
    Company Name | Domain | Person Name | Title | Action | Start Date | LinkedIn URL | Notes
    (Action must be "Join" or "Exit")

M&A / Acquisition:
    Company Name | Domain | Description | Date | Source URL | Notes

IPO Signal:
    Company Name | Domain | Description | Date | Source URL | Notes

Subsidiary Change:
    Company Name | Domain | Old Parent | New Parent | Date | Notes

All date columns should be ISO format (YYYY-MM-DD) or YYYY-MM-DDTHH:MM:SS.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# ── Google Sheets API helpers ──────────────────────────────────────────────────

def _get_service(service_account_json: str):
    """Return an authenticated Google Sheets v4 service object."""
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError:
        raise ImportError(
            "Google API client libraries not installed.\n"
            "Run:  pip install google-auth google-api-python-client google-auth-httplib2"
        )

    creds = service_account.Credentials.from_service_account_file(
        service_account_json,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def read_sheet(
    sheet_id: str,
    service_account_json: str,
    tab_name: str = "Sheet1",
) -> list[dict]:
    """Read all data rows from a Google Sheet tab.

    Returns a list of row-dicts using the first row as column headers.
    Empty rows are skipped. Returns [] on any error.
    """
    if not sheet_id or not service_account_json:
        return []
    try:
        service = _get_service(service_account_json)
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=sheet_id, range=tab_name)
            .execute()
        )
        rows = result.get("values", [])
        if len(rows) < 2:
            logger.debug("Sheet %s / %s has no data rows.", sheet_id, tab_name)
            return []
        headers = [h.strip() for h in rows[0]]
        records = []
        for row in rows[1:]:
            if not any(cell.strip() for cell in row):
                continue  # skip blank rows
            record = {
                headers[i]: (row[i].strip() if i < len(row) else "")
                for i in range(len(headers))
            }
            records.append(record)
        logger.info("Read %d rows from sheet=%s tab=%s", len(records), sheet_id, tab_name)
        return records
    except Exception as exc:
        logger.error("Failed to read sheet=%s tab=%s: %s", sheet_id, tab_name, exc)
        return []


# ── Company matching ───────────────────────────────────────────────────────────

def _normalise_domain(domain: str) -> str:
    return (
        domain.lower()
        .replace("https://", "")
        .replace("http://", "")
        .rstrip("/")
        .strip()
    )


def _match_company(row: dict, company_name: str, company_domain: str) -> bool:
    """True if a sheet row belongs to this company.

    Domain match is preferred (exact, normalised). Falls back to
    case-insensitive company name match when domain is absent.
    """
    row_domain = _normalise_domain(
        row.get("Domain") or row.get("domain") or ""
    )
    row_name = (row.get("Company Name") or row.get("company_name") or "").strip().lower()

    if company_domain and row_domain:
        return _normalise_domain(company_domain) == row_domain
    if company_name and row_name:
        return company_name.strip().lower() == row_name
    return False


# ── Top-level: fetch all sheets once per run ──────────────────────────────────

def fetch_all_signals(config: dict) -> dict[str, list[dict]]:
    """Fetch every signal sheet once at the start of a run.

    Returns:
        {
            "funding":    [...],
            "csuite":     [...],
            "ma":         [...],
            "ipo":        [...],
            "subsidiary": [...],
        }

    Any sheet whose ID is missing / not configured returns an empty list
    so the rest of the run continues normally.
    """
    creds = config.get("credentials", {})
    sa_json = creds.get("google_service_account_json", "")
    sheets_cfg = config.get("google_sheets", {})

    # Resolve relative paths against the project root
    if sa_json and not Path(sa_json).is_absolute():
        sa_json = str(Path(__file__).parent.parent / sa_json)

    result: dict[str, list[dict]] = {
        "funding": [],
        "csuite": [],
        "ma": [],
        "ipo": [],
        "subsidiary": [],
    }

    if not sa_json or not Path(sa_json).exists():
        logger.warning(
            "Google service account JSON not found at '%s' — "
            "Sheets-based HIGH signals will be skipped this run.",
            sa_json,
        )
        return result

    _SIGNAL_KEYS = ("funding", "csuite", "ma", "ipo", "subsidiary")
    for key in _SIGNAL_KEYS:
        sheet_id = sheets_cfg.get(f"{key}_sheet_id", "").strip()
        if not sheet_id or sheet_id in ("", "YOUR_SHEET_ID"):
            logger.debug("No sheet ID configured for signal type '%s' — skipping.", key)
            continue
        tab = sheets_cfg.get(f"{key}_tab", "Sheet1")
        result[key] = read_sheet(sheet_id, sa_json, tab)

    return result


# ── Per-company filter ─────────────────────────────────────────────────────────

def get_company_signals(
    company_name: str,
    company_domain: str,
    all_sheet_data: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    """Return only the rows from all_sheet_data that match this company.

    Args:
        company_name:    The company's display name (from CSV).
        company_domain:  The company's primary domain (from CSV).
        all_sheet_data:  Output of fetch_all_signals().

    Returns:
        Same structure as all_sheet_data, but filtered to matching rows only.
    """
    return {
        signal_type: [
            row for row in rows
            if _match_company(row, company_name, company_domain)
        ]
        for signal_type, rows in all_sheet_data.items()
    }
