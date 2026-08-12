"""Apollo.io API client — data fetching only, no business logic."""

from __future__ import annotations

import csv
import json
import re
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


def _range(filters: dict, min_key: str, max_key: str) -> dict | None:
    """Apollo's {"min": x, "max": y} range object from a pair of flat filter keys.

    Returns None when neither bound is set, so callers can skip the parameter
    entirely rather than sending an empty object (which Apollo rejects on some
    range filters instead of treating as "unbounded").
    """
    lo, hi = filters.get(min_key), filters.get(max_key)
    if lo is None and hi is None:
        return None
    out = {}
    if lo is not None:
        out["min"] = lo
    if hi is not None:
        out["max"] = hi
    return out


# Org-level filters that mixed_people/api_search and mixed_companies/search
# accept under identical parameter names -- for people these constrain the
# person's *current employer*, for companies the company itself. Kept in one
# table so the two endpoints can't drift apart as filters are added.
_ORG_LIST_FILTERS = (
    ("industries",                 "q_organization_keyword_tags"),
    ("job_titles",                 "q_organization_job_titles"),
    ("job_locations",              "organization_job_locations"),
    ("market_segments",            "market_segments"),
    ("naics_codes",                "organization_naics_codes"),
    ("exclude_naics_codes",        "not_organization_naics_codes"),
    ("sic_codes",                  "organization_sic_codes"),
    ("exclude_sic_codes",          "not_organization_sic_codes"),
    ("technologies",               "currently_using_any_of_technology_uids"),
    ("technologies_all",           "currently_using_all_of_technology_uids"),
    ("exclude_technologies",       "currently_not_using_any_of_technology_uids"),
)

_ORG_RANGE_FILTERS = (
    ("revenue_min",       "revenue_max",       "revenue_range"),
    ("founded_min",       "founded_max",       "organization_founded_year_range"),
    ("num_jobs_min",      "num_jobs_max",      "organization_num_jobs_range"),
    ("job_posted_after",  "job_posted_before", "organization_job_posted_at_range"),
    ("headcount_growth_min", "headcount_growth_max", "organization_headcount_growth_range"),
)


def _clean_domain(d: str) -> str:
    """Lowercased domain with any protocol/www/trailing slash stripped, so
    "https://www.Acme.com/" and "acme.com" compare equal. Used to turn Apollo's
    q_organization_domains_list -- which is a fuzzy relevance input, not a
    strict filter -- into an actual strict match on the results (see callers)."""
    d = str(d or "").strip().lower()
    d = re.sub(r"^https?://", "", d).rstrip("/")
    return re.sub(r"^www\.", "", d)


def _apply_org_filters(payload: dict, filters: dict) -> None:
    """Fill in every org-level Apollo filter both search endpoints share."""
    for src, param in _ORG_LIST_FILTERS:
        if filters.get(src):
            payload[param] = list(filters[src])
    for min_key, max_key, param in _ORG_RANGE_FILTERS:
        rng = _range(filters, min_key, max_key)
        if rng is not None:
            payload[param] = rng
    if filters.get("headcount_growth_months") is not None:
        payload["organization_headcount_growth_past_n_months"] = filters["headcount_growth_months"]
    if filters.get("include_unknown_founded_year"):
        payload["organization_include_unknown_founded_year"] = True
    if filters.get("department_counts"):
        payload["organization_department_or_subdepartment_counts"] = dict(filters["department_counts"])
    emp_min, emp_max = filters.get("employee_min"), filters.get("employee_max")
    if emp_min is not None or emp_max is not None:
        # One-sided is a real request ("1000+ employees", "under 50"), and
        # requiring both bounds meant such a filter was dropped in silence, so the
        # search quietly answered a broader question than the one that was asked.
        # Apollo only takes discrete buckets, so an open end becomes the outermost
        # bucket rather than no filter at all.
        ranges = _employee_ranges_for(emp_min if emp_min is not None else 1,
                                      emp_max if emp_max is not None else 10 ** 9)
        if ranges:
            payload["organization_num_employees_ranges"] = ranges


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
    # Every attempt was rate limited (429 retries `continue` without recording an
    # exception). Returning {} here would be indistinguishable from "Apollo
    # answered and had nothing", which callers would go on to report to a user as
    # a definitive absence of data. Raise instead: not reaching Apollo and Apollo
    # confirming nothing exists must never share a code path.
    raise requests.HTTPError("Apollo did not answer %s after %d attempts per base "
                             "URL (rate limited)" % (endpoint, retries))


def search_companies(filters: dict, api_key: str, page: int = 1, per_page: int = 25,
                     strict: bool = False, meta: dict | None = None) -> list[dict]:
    """Search Apollo organizations via mixed_companies/search. Costs 1 Apollo
    credit per call that returns at least one result (0 if it returns none) --
    unlike search_people, this is NOT free.

    strict=True re-raises transport failures instead of returning []. Use it
    anywhere the empty result would be shown to a person as "no such company
    exists", because [] otherwise conflates that with "Apollo was unreachable".

    If `meta` is passed a dict, it is filled in with Apollo's own pagination
    totals ("total_entries"/"total_pages") so a caller can report an honest
    count instead of guessing one from len(returned rows), which undercounts
    the moment there is more than one page.

    Company-only filter keys (all optional): name (fuzzy match, e.g. for
    disambiguating a company by its display name), domains (list), locations
    (list, HQ location), exclude_locations (list, HQ locations to exclude),
    label_ids (list, Apollo list/label IDs -- ids not names),
    total_funding_min/max, latest_funding_min/max, funded_after/funded_before
    (ISO dates, most recent round), exclude_keywords (client-side post-filter --
    Apollo has no native text-exclusion param), organization_ids (list, Apollo
    org IDs -- the same namespace search_people takes, so a single call can
    describe every distinct employer on a page of people), max_companies (caps
    the returned list length).

    The remaining filter keys are shared with search_people and documented on
    _apply_org_filters / _ORG_LIST_FILTERS / _ORG_RANGE_FILTERS: industries
    (mapped to Apollo's keyword-tag search, since there is no separate industry
    filter), job_titles, job_locations, market_segments, naics_codes, sic_codes
    and their exclude_* forms, technologies / technologies_all /
    exclude_technologies, revenue_min/max, founded_min/max, num_jobs_min/max,
    job_posted_after/before, headcount_growth_min/max (+
    headcount_growth_months), include_unknown_founded_year, department_counts,
    employee_min/employee_max (mapped to Apollo's bucket ranges via
    _employee_ranges_for).
    """
    payload: dict = {
        "page": page,
        "per_page": min(per_page, 100),
    }
    if filters.get("name"):
        payload["q_organization_name"] = filters["name"]
    if filters.get("domains"):
        payload["q_organization_domains_list"] = list(filters["domains"])
    if filters.get("organization_ids"):
        # Same param and same id namespace search_people uses. Looking companies
        # up by id is what makes one paid call able to describe a whole page of
        # them at once: this endpoint charges per CALL, not per company, so N ids
        # in one request cost the same single credit as one id would.
        payload["organization_ids"] = list(filters["organization_ids"])
    if filters.get("locations"):
        payload["organization_locations"] = list(filters["locations"])
    if filters.get("exclude_locations"):
        payload["organization_not_locations"] = list(filters["exclude_locations"])
    if filters.get("label_ids"):
        payload["account_label_ids"] = list(filters["label_ids"])
    _apply_org_filters(payload, filters)
    # Funding filters exist only on the company endpoint, not on people search.
    for min_key, max_key, param in (
        ("total_funding_min",   "total_funding_max",   "total_funding_range"),
        ("latest_funding_min",  "latest_funding_max",  "latest_funding_amount_range"),
        ("funded_after",        "funded_before",       "latest_funding_date_range"),
    ):
        rng = _range(filters, min_key, max_key)
        if rng is not None:
            payload[param] = rng

    try:
        data = _post("mixed_companies/search", payload, api_key)
    except Exception:
        logger.error("Failed to fetch companies from Apollo.")
        if strict:
            raise
        return []

    if meta is not None:
        pagination = data.get("pagination") or {}
        meta["total_entries"] = pagination.get("total_entries")
        meta["total_pages"] = pagination.get("total_pages")

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
        org_id = acct.get("organization_id")
        if not org_id:
            # No organization id on this saved-account row. Falling back to its
            # account `id` would put an account-namespace value into org-level
            # filters (organization_ids, organizations/enrich), which matches
            # nothing and burns a credit while looking like "no data". Skipping
            # the row is the honest outcome.
            logger.warning("apollo accounts row without organization_id, skipping: %s",
                           str(acct.get("name") or "")[:60])
            continue
        merged = dict(acct)
        merged["id"] = org_id
        if not merged.get("primary_domain"):
            merged["primary_domain"] = acct.get("domain")
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

    # q_organization_domains_list does not actually restrict Apollo's results to
    # that domain -- a malformed or unindexed domain silently falls back to an
    # UNFILTERED search rather than erroring or matching nothing, which would
    # show an unrelated company as if it matched. This endpoint is also paid, so
    # a false match here both misinforms the caller AND spends a credit for it.
    # Enforce the filter for real, in code, against Apollo's own domain field.
    wanted_domains = {_clean_domain(d) for d in (filters.get("domains") or [])}
    wanted_domains.discard("")
    if wanted_domains:
        before = len(orgs)
        orgs = [o for o in orgs if _clean_domain(o.get("primary_domain") or o.get("domain") or "")
                in wanted_domains]
        logger.info("search_companies: domain filter kept %d/%d", len(orgs), before)
        if meta is not None:
            # Apollo's pagination totals describe the unfiltered call and would
            # wildly overstate how many companies actually match the domain now
            # that we enforce it ourselves -- an honest caller can't report a
            # total it doesn't know.
            meta["total_entries"] = None
            meta["total_pages"] = None

    max_companies = filters.get("max_companies")
    if max_companies is not None:
        orgs = orgs[:max_companies]

    if orgs:
        logger.debug("[DEBUG] First company: %s", json.dumps(orgs[0], default=str)[:200])

    logger.info("search_companies: received %d companies (after filtering)", len(orgs))
    return orgs


def search_people(filters: dict, api_key: str, page: int = 1, per_page: int = 25,
                  strict: bool = False, meta: dict | None = None) -> list[dict]:
    """Search Apollo people via mixed_people/api_search (free, no credits --
    this does NOT return verified emails/phones, only identity + role fields;
    use enrich_company/get_leadership, bulk_match_people, or the
    person-enrichment path for that). Apollo also masks/truncates some
    contacts' last names in THIS endpoint's results depending on plan type --
    that is expected, not a bug, and is resolved by enriching the id via
    bulk_match_people, not by anything this function can do differently.

    If `meta` is passed a dict, it is filled in with Apollo's own pagination
    totals ("total_entries"/"total_pages") so a caller can report an honest
    count instead of guessing one from len(returned rows), which undercounts
    the moment there is more than one page.

    Person-level filter keys (all optional): titles (list),
    include_similar_titles (bool, default True), seniorities (list, e.g.
    "c_suite"/"vp"/"director"/...), person_locations (list, where the PERSON
    lives), linkedin_urls (list of full profile URLs), keywords (str),
    email_status (list), days_in_title_min/days_in_title_max (tenure in the
    current role, in days), yoe_min/yoe_max (total career years),
    organization_ids (list, Apollo org IDs -- same namespace get_leadership
    uses), company_domains (list), company_locations (list, employer HQ --
    independent of and ANDed with person_locations), max_people (caps the
    returned list length, like get_leadership).

    Employer-level filter keys are shared with search_companies and documented
    on _apply_org_filters / _ORG_LIST_FILTERS / _ORG_RANGE_FILTERS: industries,
    job_titles, job_locations, market_segments, naics_codes, sic_codes and their
    exclude_* forms, technologies / technologies_all / exclude_technologies,
    revenue_min/max, founded_min/max, num_jobs_min/max, job_posted_after/before,
    headcount_growth_min/max (+ headcount_growth_months),
    include_unknown_founded_year, department_counts, employee_min/employee_max.
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
    if filters.get("linkedin_urls"):
        payload["person_linkedin_urls"] = list(filters["linkedin_urls"])
    if filters.get("keywords"):
        payload["q_keywords"] = filters["keywords"]
    if filters.get("email_status"):
        payload["contact_email_status"] = list(filters["email_status"])
    for min_key, max_key, param in (
        ("days_in_title_min", "days_in_title_max", "person_days_in_current_title_range"),
        ("yoe_min",           "yoe_max",           "person_total_yoe_range"),
    ):
        rng = _range(filters, min_key, max_key)
        if rng is not None:
            payload[param] = rng
    _apply_org_filters(payload, filters)

    try:
        data = _post("mixed_people/api_search", payload, api_key)
    except Exception:
        logger.error("Failed to search people on Apollo.")
        if strict:
            raise
        return []

    if meta is not None:
        pagination = data.get("pagination") or {}
        meta["total_entries"] = pagination.get("total_entries")
        meta["total_pages"] = pagination.get("total_pages")

    people = data.get("people", [])
    max_people = filters.get("max_people")
    if max_people is not None:
        people = people[:max_people]

    normalized = [_normalize_search_person(p) for p in people]

    # Same fuzzy-not-strict behavior as search_companies' domain filter above:
    # a domain Apollo does not treat as an exact match (including a malformed
    # one with no TLD) silently falls back to an unfiltered search rather than
    # matching nothing, which would show people from unrelated companies as if
    # they matched the requested employer. Enforce it for real here.
    wanted_domains = {_clean_domain(d) for d in (filters.get("company_domains") or [])}
    wanted_domains.discard("")
    if wanted_domains:
        before = len(normalized)
        normalized = [p for p in normalized
                     if _clean_domain(p.get("organization_domain") or "") in wanted_domains]
        logger.info("search_people: domain filter kept %d/%d", len(normalized), before)
        if meta is not None:
            # Apollo's pagination totals describe the unfiltered call and would
            # wildly overstate how many people actually match the domain now
            # that we enforce it ourselves -- an honest caller can't report a
            # total it doesn't know.
            meta["total_entries"] = None
            meta["total_pages"] = None

    logger.info("search_people: received %d people (%s)",
                len(normalized), _field_coverage(normalized))
    return normalized


# Fields worth reporting coverage on: everything the results grid can render.
# Which of these Apollo actually populates is plan-dependent and has changed
# over time (some tiers return only availability booleans plus an obfuscated
# last name), so the grid must treat every one as optional.
_COVERAGE_KEYS = ("last_name", "linkedin_url", "photo_url", "seniority", "city",
                  "country", "headline", "email_status", "departments",
                  "employment_history", "organization_domain",
                  "organization_industry", "organization_employees")


def _field_coverage(rows: list[dict]) -> str:
    """"last_name 25/25, photo_url 0/25, ..." for the log line.

    Counts only -- never the values themselves, which are personal data that
    must not land in application logs. This exists because which fields Apollo
    returns varies by plan and silently changes: without it, a grid that has
    gone sparse is indistinguishable from a rendering bug.
    """
    if not rows:
        return "no rows"
    total = len(rows)
    return ", ".join("%s %d/%d" % (k, sum(1 for r in rows if r.get(k)), total)
                     for k in _COVERAGE_KEYS)


def _normalize_search_person(p: dict) -> dict:
    """One mixed_people/api_search row -> the flat shape the CPI grid renders.

    Every field is optional by design. Apollo's free search tier returns a
    subset that depends on the plan -- verified live against this account it is
    id / first_name / last_name / title / linkedin_url / organization{id,name,
    domain} and nothing else -- while photo, location, seniority, department and
    employment history come back only from the paid enrichment endpoints. Rather
    than assume either shape, this reads all of them defensively so the grid
    shows whatever is genuinely present and hides the rest.
    """
    first = (p.get("first_name") or "").strip()
    last = (p.get("last_name") or "").strip()
    # Some plans withhold the real surname and return only a masked form. Show
    # it (it still disambiguates two same-first-name people in a list) but flag
    # it, so the UI can offer enrichment instead of implying it is the real name.
    masked = ""
    if not last:
        masked = (p.get("last_name_obfuscated") or "").strip()
    display_last = last or masked
    full_name = (f"{first} {display_last}".strip()
                 or (p.get("name") or "").strip() or None)

    org = p.get("organization") or {}
    history = [h for h in (p.get("employment_history") or []) if isinstance(h, dict)]
    current = next((h for h in history if h.get("current")), history[0] if history else {})
    past = [h for h in history if not h.get("current") and h.get("organization_name")]

    return {
        "id": p.get("id"),
        "full_name": full_name,
        "first_name": first or None,
        "last_name": last or None,
        "name_masked": bool(masked),
        "title": p.get("title"),
        "headline": p.get("headline"),
        "seniority": p.get("seniority"),
        "departments": [d for d in (p.get("departments") or []) if d],
        "subdepartments": [d for d in (p.get("subdepartments") or []) if d],
        "functions": [f for f in (p.get("functions") or []) if f],
        "email_status": p.get("email_status"),
        "photo_url": p.get("photo_url"),
        "linkedin_url": p.get("linkedin_url"),
        "twitter_url": p.get("twitter_url"),
        "github_url": p.get("github_url"),
        "city": p.get("city"),
        "state": p.get("state"),
        "country": p.get("country"),
        "title_start_date": current.get("start_date"),
        "past_companies": [h.get("organization_name") for h in past[:3]],
        "past_roles_count": len(past),
        "last_refreshed_at": p.get("last_refreshed_at"),
        "organization_id": org.get("id") or p.get("organization_id"),
        "organization_name": org.get("name"),
        "organization_domain": (org.get("primary_domain") or org.get("domain")
                                or org.get("website_url")),
        "organization_logo": org.get("logo_url"),
        "organization_industry": org.get("industry"),
        "organization_employees": org.get("estimated_num_employees"),
        "organization_founded": org.get("founded_year"),
        "organization_revenue": org.get("annual_revenue"),
        "organization_funding": org.get("total_funding"),
        "organization_linkedin": org.get("linkedin_url"),
        "organization_website": org.get("website_url"),
        "organization_city": org.get("city"),
        "organization_country": org.get("country"),
        "organization_technologies": [t for t in (org.get("technology_names") or []) if t][:12],
        "organization_keywords": [k for k in (org.get("keywords") or []) if k][:10],
    }


def bulk_match_people(ids: list, api_key: str) -> dict:
    """Apollo person id -> raw Apollo person record, via people/bulk_match (up to
    10 ids per call, issued in sequential chunks). Costs 1 Apollo credit per id
    that actually matches (0 for a miss).

    This is how search_people's masked/truncated last names get revealed: pass
    the `id` field straight from a search_people row here rather than
    re-searching by name, which could resolve to the wrong same-named person.
    Returns only ids that Apollo actually matched -- a missing id means either
    no match or a failed chunk, and callers should keep whatever they already
    had for that person rather than treating the omission as a fact.
    """
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return {}
    out: dict = {}
    for i in range(0, len(ids), 10):
        chunk = ids[i:i + 10]
        try:
            data = _post("people/bulk_match", {"details": [{"id": pid} for pid in chunk]}, api_key)
        except Exception:
            logger.error("bulk_match_people failed for a chunk of %d ids", len(chunk))
            continue
        matches = data.get("matches")
        if not isinstance(matches, list):
            logger.warning("bulk_match_people: unexpected response shape (keys=%s)",
                           sorted(list(data.keys()))[:8])
            continue
        for j, pid in enumerate(chunk):
            m = matches[j] if j < len(matches) else None
            if m:
                out[pid] = m
    return out


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
    """Return full Apollo organization profile for the given Apollo organization ID.

    NOTE: organizations/enrich is documented as a DOMAIN-keyed endpoint and does
    not officially accept an id, so this can legitimately come back empty even
    for a real organization. Callers must therefore treat an empty result as
    "try the domain instead", never as "Apollo has no such company" -- see
    _cpi_enrich_company, where taking this as final made every company profile
    question answer "no full profile" no matter which company was asked about.
    """
    try:
        data = _post("organizations/enrich", {"id": apollo_id}, api_key)
        org = data.get("organization", data)
        return org if isinstance(org, dict) and (org.get("id") or org.get("name")) else {}
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
        # Deliberately does NOT dump the raw record: it carries personal data
        # (name, email) that must not land in application logs. Count only.
        logger.info("get_leadership: %d people for org_id=%s", len(people), organization_id)
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
