"""Parse apollo-accounts-export.csv and map columns to internal field names."""

from __future__ import annotations

import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

COLUMN_MAP: dict[str, str] = {
    "Company Name": "name",
    "# Employees": "employees",
    "Industry": "industry",
    "Website": "domain",
    "Company Linkedin Url": "linkedin_url",
    "Company City": "city",
    "Company State": "state",
    "Company Country": "country",
    "Keywords": "keywords",
    "Technologies": "tech_stack",
    "Total Funding": "total_funding",
    "Latest Funding": "latest_funding_type",
    "Latest Funding Amount": "latest_funding_amount",
    "Last Raised At": "last_raised_at",
    "Annual Revenue": "annual_revenue",
    "Apollo Account Id": "apollo_id",
    "Short Description": "description",
    "Founded Year": "founded_year",
    "Primary Intent Topic": "intent_topic_1",
    "Primary Intent Score": "intent_score_1",
    "Secondary Intent Topic": "intent_topic_2",
    "Secondary Intent Score": "intent_score_2",
    "Account Stage": "crm_stage",
    "SIC Codes": "sic_codes",
    "NAICS Codes": "naics_codes",
    "Logo Url": "logo_url",
    "Twitter Url": "twitter_url",
    "Facebook Url": "facebook_url",
    "Number of Retail Locations": "retail_locations",
    "Subsidiary of": "subsidiary_of",
}

KEYWORDS_MAX_CHARS = 500


def _safe_print(text: str) -> None:
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", errors="replace").decode("ascii"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_int(value: str) -> int | None:
    if not value or not value.strip():
        return None
    try:
        return int(float(value.replace(",", "").strip()))
    except (ValueError, TypeError):
        return None


def _parse_float(value: str) -> float | None:
    if not value or not value.strip():
        return None
    try:
        return float(value.replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_tech_stack(value: str) -> list[str]:
    if not value or not value.strip():
        return []
    return [t.strip() for t in value.split(",") if t.strip()]


def _normalize_domain(domain: str) -> str:
    """Lowercase, strip protocol and trailing slash for consistent matching."""
    d = domain.lower().strip()
    for prefix in ("https://www.", "http://www.", "https://", "http://"):
        if d.startswith(prefix):
            d = d[len(prefix):]
            break
    return d.rstrip("/")


# ── Tier classification ───────────────────────────────────────────────────────



# ── Public loader ─────────────────────────────────────────────────────────────

def load_companies(csv_path: str | Path) -> list[dict]:
    """Read apollo-accounts-export.csv and return normalised company dicts.

    All companies are treated equally — no tier system.
    """
    path = Path(csv_path)
    if not path.exists():
        logger.error("CSV not found: %s", path)
        return []

    companies: list[dict] = []
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            company: dict = {}
            for csv_col, internal_field in COLUMN_MAP.items():
                company[internal_field] = (row.get(csv_col) or "").strip()

            # Type coercions
            company["employees"]             = _parse_int(company["employees"])
            company["total_funding"]         = _parse_float(company["total_funding"])
            company["latest_funding_amount"] = _parse_float(company["latest_funding_amount"])
            company["annual_revenue"]        = _parse_float(company["annual_revenue"])
            company["intent_score_1"]        = _parse_float(company["intent_score_1"])
            company["intent_score_2"]        = _parse_float(company["intent_score_2"])
            company["retail_locations"]      = _parse_int(company["retail_locations"])
            company["founded_year"]          = _parse_int(company["founded_year"])

            # Tech stack: CSV string → list (stored as JSON later)
            company["tech_stack"] = _parse_tech_stack(company["tech_stack"])

            # Keywords: full copy for signal detection, truncated for DB storage
            company["keywords_full"] = company["keywords"]
            if len(company["keywords"]) > KEYWORDS_MAX_CHARS:
                company["keywords"] = company["keywords"][:KEYWORDS_MAX_CHARS]

            # Synthesize a stable ID for companies missing apollo_id
            if not company["apollo_id"]:
                domain = company.get("domain", "")
                if domain:
                    company["apollo_id"] = f"domain:{domain.replace('https://', '').replace('http://', '').rstrip('/')}"
                else:
                    company["apollo_id"] = f"name:{company['name'].lower().replace(' ', '_')}"

            if company["name"]:
                companies.append(company)

    logger.info("csv_loader: loaded %d companies", len(companies))
    _safe_print(f"\n  Loaded {len(companies):,} companies from CSV\n")
    return companies
