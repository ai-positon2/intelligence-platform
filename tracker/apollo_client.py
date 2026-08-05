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

# Apollo serves the same API under two path prefixes: the currently documented
# "https://api.apollo.io/api/v1/..." and the older "https://api.apollo.io/v1/...".
# Which one answers has varied by endpoint and over time, and a wrong prefix
# fails as a 404 that looks exactly like "no data". So we try the documented one
# first, fall back to the legacy one on 404/405, and remember the winner for the
# rest of the process instead of paying that probe on every call.
_BASE_URLS = ("https://api.apollo.io/api/v1", "https://api.apollo.io/v1")
_BASE_URL = _BASE_URLS[1]      # kept for backwards compatibility / callers reading it
_BASE_OK: str | None = None    # the prefix proven to work in this process

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


def _bases_to_try() -> tuple:
    """Proven-good prefix first if we have one, else both in preference order."""
    if _BASE_OK:
        return (_BASE_OK,) + tuple(b for b in _BASE_URLS if b != _BASE_OK)
    return _BASE_URLS


def _post(endpoint: str, payload: dict, api_key: str, retries: int = 3) -> dict:
    global _BASE_OK
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": api_key,
    }
    delay = 1.0
    last_exc = None
    for base in _bases_to_try():
        url = f"{base}/{endpoint}"
        for attempt in range(retries):
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=30)
                if resp.status_code == 429:
                    wait = delay * (2 ** attempt)
                    logger.warning("Rate limited by Apollo. Waiting %.1fs before retry %d/%d", wait, attempt + 1, retries)
                    time.sleep(wait)
                    continue
                if resp.status_code in (404, 405):
                    # Wrong path prefix for this endpoint: try the other base
                    # rather than burning retries or reporting it as "no data".
                    logger.warning("Apollo %s on %s -- trying the other base URL", resp.status_code, url)
                    last_exc = requests.HTTPError("%s on %s" % (resp.status_code, url))
                    break
                if resp.status_code == 422 and attempt == 0:
                    logger.error("Apollo 422 on %s -- response body: %s", endpoint, resp.text[:500])
                resp.raise_for_status()
                _BASE_OK = base
                return resp.json()
            except requests.RequestException as exc:
                last_exc = exc
                if attempt == retries - 1:
                    logger.error("Apollo API error on %s after %d retries: %s", url, retries, exc)
                    break
                wait = delay * (2 ** attempt)
                logger.warning("Request error (%s). Retrying in %.1fs (%d/%d)...", exc, wait, attempt + 1, retries)
                time.sleep(wait)
    if last_exc:
        raise last_exc
    return {}


def search_companies(filters: dict, api_key: str, page: int = 1, per_page: int = 25) -> list[dict]:
    """Search Apollo organizations via mixed_companies/search. Costs 1 Apollo
    credit per call that returns at least one result (0 if it returns none) --
    unlike search_people, this is NOT free.

    filters keys (all optional): name (fuzzy match, e.g. for disambiguating a
    company by its display name), domains (list), employee_min/employee_max
    (mapped to Apollo's bucket ranges via _employee_ranges_for), locations
    (list, HQ location), industries (list, mapped to Apollo's keyword-tag
    search since there is no separate industry filter), exclude_keywords
    (client-side post-filter -- Apollo has no native text-exclusion param),
    max_companies (caps the returned list length).
    """
    payload: dict = {
        "page": page,
        "per_page": min(per_page, 100),
    }
    if filters.get("name"):
        payload["q_organization_name"] = filters["name"]
    if filters.get("domains"):
        payload["q_organization_domains_list"] = list(filters["domains"])
    if filters.get("locations"):
        payload["organization_locations"] = list(filters["locations"])
    if filters.get("industries"):
        payload["q_organization_keyword_tags"] = list(filters["industries"])
    emp_min, emp_max = filters.get("employee_min"), filters.get("employee_max")
    if emp_min is not None and emp_max is not None:
        ranges = _employee_ranges_for(emp_min, emp_max)
        if ranges:
            payload["organization_num_employees_ranges"] = ranges

    try:
        data = _post("mixed_companies/search", payload, api_key)
    except Exception:
        logger.error("Failed to fetch companies from Apollo.")
        return []

    # mixed_companies/search splits results into two buckets whose `id` fields are
    # NOT interchangeable: "organizations" (net-new companies) carries the real
    # Apollo organization ID in `id`; "accounts" (companies this Apollo team has
    # already saved) carries an ACCOUNT id in `id` -- the organization ID is a
    # separate `organization_id` field, and the domain lives in `domain` rather
    # than `primary_domain`. Feeding an account's raw `id` into an
    # organization_ids filter elsewhere (e.g. search_people) would silently
    # match nothing or the wrong org, so both buckets are normalized onto the
    # same shape here before any caller ever sees the difference.
    orgs = list(data.get("organizations") or [])
    for acct in (data.get("accounts") or []):
        merged = dict(acct)
        merged["id"] = acct.get("organization_id") or acct.get("id")
        merged.setdefault("primary_domain", acct.get("domain"))
        orgs.append(merged)

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

    max_companies = filters.get("max_companies")
    if max_companies is not None:
        orgs = orgs[:max_companies]

    if orgs:
        logger.debug("[DEBUG] First company: %s", json.dumps(orgs[0], default=str)[:200])

    logger.info("search_companies: received %d companies (after filtering)", len(orgs))
    return orgs


def search_people(filters: dict, api_key: str, page: int = 1, per_page: int = 25) -> list[dict]:
    """Search Apollo people via mixed_people/api_search (free, no credits --
    this does NOT return verified emails/phones, only identity + role fields;
    use enrich_company/get_leadership or the person-enrichment path for that).

    filters keys (all optional): titles (list), include_similar_titles (bool,
    default True), seniorities (list, e.g. "c_suite"/"vp"/"director"/...),
    person_locations (list), company_locations (list, employer HQ),
    company_domains (list), organization_ids (list, Apollo org IDs -- same
    namespace get_leadership uses), employee_min/employee_max (employer size,
    mapped via _employee_ranges_for), keywords (str), email_status (list),
    max_people (caps the returned list length, like get_leadership).
    """
    payload: dict = {
        "page": page,
        "per_page": min(per_page, 100),
    }
    if filters.get("titles"):
        payload["person_titles"] = list(filters["titles"])
        payload["include_similar_titles"] = bool(filters.get("include_similar_titles", True))
    if filters.get("seniorities"):
        payload["person_seniorities"] = list(filters["seniorities"])
    if filters.get("person_locations"):
        payload["person_locations"] = list(filters["person_locations"])
    if filters.get("company_locations"):
        payload["organization_locations"] = list(filters["company_locations"])
    if filters.get("company_domains"):
        payload["q_organization_domains_list"] = list(filters["company_domains"])
    if filters.get("organization_ids"):
        payload["organization_ids"] = list(filters["organization_ids"])
    if filters.get("keywords"):
        payload["q_keywords"] = filters["keywords"]
    if filters.get("email_status"):
        payload["contact_email_status"] = list(filters["email_status"])
    emp_min, emp_max = filters.get("employee_min"), filters.get("employee_max")
    if emp_min is not None and emp_max is not None:
        ranges = _employee_ranges_for(emp_min, emp_max)
        if ranges:
            payload["organization_num_employees_ranges"] = ranges

    try:
        data = _post("mixed_people/api_search", payload, api_key)
    except Exception:
        logger.error("Failed to search people on Apollo.")
        return []

    people = data.get("people", [])
    max_people = filters.get("max_people")
    if max_people is not None:
        people = people[:max_people]

    normalized = []
    for p in people:
        first = (p.get("first_name") or "").strip()
        last = (p.get("last_name") or "").strip()
        full_name = (f"{first} {last}".strip()) or (p.get("name") or "").strip() or None
        org = p.get("organization") or {}
        normalized.append({
            "id": p.get("id"),
            "full_name": full_name,
            "first_name": first or None,
            "last_name": last or None,
            "title": p.get("title"),
            "seniority": p.get("seniority"),
            "linkedin_url": p.get("linkedin_url"),
            "city": p.get("city"),
            "state": p.get("state"),
            "country": p.get("country"),
            "organization_id": org.get("id") or p.get("organization_id"),
            "organization_name": org.get("name"),
            "organization_domain": org.get("primary_domain") or org.get("website_url"),
        })

    logger.info("search_people: received %d people", len(normalized))
    return normalized


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
