"""Apollo.io API client — data fetching only, no business logic."""

from __future__ import annotations

import csv
import json
import time
import logging
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.apollo.io/v1"

# Apollo employee range buckets that map to integer bounds
_EMPLOYEE_RANGES = [
    ("1,10", 1, 10),
    ("11,20", 11, 20),
    ("21,50", 21, 50),
    ("51,100", 51, 100),
    ("101,200", 101, 200),
    ("201,500", 201, 500),
    ("501,1000", 501, 1000),
    ("1001,2000", 1001, 2000),
    ("2001,5000", 2001, 5000),
    ("5001,10000", 5001, 10000),
    ("10001,", 10001, None),
]


def _employee_ranges_for(min_emp: int, max_emp: int) -> list[str]:
    result = []
    for label, low, high in _EMPLOYEE_RANGES:
        if high is None:
            if low <= max_emp:
                result.append(label)
        elif high >= min_emp and low <= max_emp:
            result.append(label)
    return result


def _post(endpoint: str, payload: dict, api_key: str, retries: int = 3) -> dict:
    url = f"{_BASE_URL}/{endpoint}"
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": api_key,
    }
    delay = 1.0
    for attempt in range(retries):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            if resp.status_code == 429:
                wait = delay * (2 ** attempt)
                logger.warning("Rate limited by Apollo. Waiting %.1fs before retry %d/%d", wait, attempt + 1, retries)
                time.sleep(wait)
                continue
            if resp.status_code == 422 and attempt == 0:
                logger.error("Apollo 422 on %s — response body: %s", endpoint, resp.text[:500])
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            if attempt == retries - 1:
                logger.error("Apollo API error on %s after %d retries: %s", endpoint, retries, exc)
                raise
            wait = delay * (2 ** attempt)
            logger.warning("Request error (%s). Retrying in %.1fs (%d/%d)…", exc, wait, attempt + 1, retries)
            time.sleep(wait)
    return {}


def search_companies(filters: dict, api_key: str) -> list[dict]:
    """Fetch 10 companies from Apollo and print the first result's raw JSON."""
    payload = {
        "page": 1,
        "per_page": 10,
        "organization_num_employees_ranges": ["100,2000"],
        "organization_locations": ["United States"],
    }

    try:
        data = _post("mixed_companies/search", payload, api_key)
    except Exception:
        logger.error("Failed to fetch companies from Apollo.")
        return []

    orgs = data.get("organizations", []) or data.get("accounts", [])

    # Client-side keyword exclusion (Apollo doesn't natively filter by text keywords)
    exclude_kws = [kw.lower() for kw in (filters.get("exclude_keywords") or [])]
    if exclude_kws:
        def _excluded(org: dict) -> bool:
            text = " ".join([
                (org.get("name") or ""),
                (org.get("short_description") or ""),
                (org.get("industry") or ""),
            ]).lower()
            return any(kw in text for kw in exclude_kws)
        orgs = [o for o in orgs if not _excluded(o)]

    if orgs:
        logger.debug("[DEBUG] First company: %s", json.dumps(orgs[0], default=str)[:200])

    logger.info("search_companies: received %d companies (after filtering)", len(orgs))
    return orgs


def enrich_company(domain: str, api_key: str) -> dict:
    """Return full Apollo organization profile for the given domain."""
    clean = domain.replace("https://", "").replace("http://", "").rstrip("/")
    try:
        data = _post("organizations/enrich", {"domain": clean}, api_key)
        return data.get("organization", data)
    except Exception:
        logger.error("Failed to enrich company domain=%s", domain)
        return {}


def enrich_company_by_id(apollo_id: str, api_key: str) -> dict:
    """Return full Apollo organization profile for the given Apollo account ID."""
    try:
        data = _post("organizations/enrich", {"id": apollo_id}, api_key)
        return data.get("organization", data)
    except Exception:
        logger.error("Failed to enrich company apollo_id=%s", apollo_id)
        return {}


def enrich_from_csv(csv_path: str | Path, api_key: str) -> list[dict]:
    """Read my-companies.csv (columns: Company Name, Domain, Location, Employee Count),
    enrich each row via Apollo, and return a list of org dicts."""
    path = Path(csv_path)
    if not path.exists():
        logger.error("CSV not found: %s", path)
        return []

    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    logger.info("enrich_from_csv: %d rows from %s", len(rows), path)
    results: list[dict] = []

    for i, row in enumerate(rows, 1):
        name     = (row.get("Company Name") or "").strip()
        domain   = (row.get("Domain") or "").strip()
        location = (row.get("Location") or "").strip()
        emp      = (row.get("Employee Count") or "").strip()

        if not name and not domain:
            continue

        if domain:
            logger.info("  [%d/%d] Enriching %s (%s)…", i, len(rows), name, domain)
            enriched = enrich_company(domain, api_key)
            if enriched.get("id"):
                if not enriched.get("name"):
                    enriched["name"] = name
                results.append(enriched)
                time.sleep(0.3)
                continue
            logger.warning("  No Apollo data for %s (%s) — using CSV fallback", name, domain)

        # Fallback: build a minimal org dict from CSV columns
        results.append({
            "name": name,
            "primary_domain": domain,
            "domain": domain,
            "city": location,
            "estimated_num_employees": emp,
        })
        time.sleep(0.3)

    logger.info("enrich_from_csv: returning %d companies", len(results))
    return results


def get_leadership(organization_id: str, api_key: str, max_people: int = 20) -> list[dict]:
    """Return people for the organization via mixed_people/api_search.

    organization_id must be the Apollo-internal org ID returned by organizations/enrich,
    NOT the Apollo Account ID from a CSV export — those are different namespaces.
    """
    payload = {
        "organization_ids": [organization_id],
        "page": 1,
        "per_page": min(max_people, 25),
    }
    try:
        data = _post("mixed_people/api_search", payload, api_key)
        people = data.get("people", [])
        if people:
            logger.info("[DEBUG] First person raw fields: %s", json.dumps(people[0], default=str)[:500])
        result = []
        for p in people[:max_people]:
            first = (p.get("first_name") or "").strip()
            last = (p.get("last_name") or "").strip()
            full_name = (f"{first} {last}".strip()) or (p.get("name") or "").strip() or None
            result.append({
                "id": p.get("id"),
                "full_name": full_name,
                "first_name": first or None,
                "last_name": last or None,
                "title": p.get("title"),
                "linkedin_url": p.get("linkedin_url"),
                "email": p.get("email"),
                "start_date": (p.get("employment_history") or [{}])[0].get("start_date"),
            })
        return result
    except Exception:
        logger.error("Failed to get leadership for org_id=%s", organization_id)
        return []
