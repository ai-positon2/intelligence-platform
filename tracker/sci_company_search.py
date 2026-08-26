"""Native (Apollo-backed) company search for Social Creative Intelligence's
disambiguation picker -- no dependency on the Arena vendor.

tracker/arena_client.py's own company search periodically fails because
Arena's WORKSPACE loses its connected LinkedIn account (a problem entirely
outside this codebase, see arena_client's module docs) -- so SCI's picker was
inheriting an outage it has no way to fix. This module answers the same
question a different way: tracker/apollo_client.py's mixed_companies/search,
which this platform already pays for and already uses (Contact Finder,
Person Enrichment) via the standing APOLLO_API_KEY, so disambiguating a
company name needs no new vendor account and no dependency on Arena's
workspace staying healthy.

Same typed-error contract as arena_client.search_companies_result --
{"companies": [...], "error": <error|None>, "elapsed_ms": int, "source": str}
-- and the same normalized company shape (id/name/logo/industry/location/
description/summary/followers_count/profile_url/website) arena_client's own
_to_company produces, so app.py's /search route and the frontend's rendering
needed no changes to have the vendor swapped out from under them.

Unlike Arena's search, this is NOT free: Apollo bills ~1 credit per call that
returns at least one result (see apollo_client.search_companies's own
docstring). Acceptable here since the platform already pays for Apollo
elsewhere and a typeahead is naturally rate-limited by debounce + a 2-char
minimum on the frontend, but worth knowing before raising that debounce.
"""

from __future__ import annotations

import logging
import os
import re
import time

import requests

logger = logging.getLogger(__name__)

ERR_NOT_CONFIGURED = "not_configured"
ERR_TIMEOUT = "timeout"
ERR_HTTP = "http_status"
ERR_NETWORK = "network"
ERR_UNPARSABLE = "unparsable"

# Mirrors arena_client._RETRY_STATUSES -- statuses worth a second attempt vs.
# ones (401/403/404/402) that fail identically every time. apollo_client's own
# _post already retries these internally before this module ever sees them;
# this set only decides whether the FRONTEND offers a Retry button.
_RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


def _api_key() -> str:
    return os.environ.get("APOLLO_API_KEY", "")


def _err(kind: str, detail: str = "", status: int | None = None) -> dict:
    """One failure, described. `detail` is for operators (logs, the admin
    self-test) and may quote Apollo's own words; it never carries the API
    key, which only ever lives in a request header."""
    return {"kind": kind, "status": status, "detail": (detail or "")[:500], "attempts": 1}


def describe_error(err: dict | None) -> str:
    """A failure kind rendered for whoever is looking at the screen."""
    if not isinstance(err, dict):
        return ""
    kind, status = err.get("kind"), err.get("status")
    if kind == ERR_NOT_CONFIGURED:
        return ("Company search is not configured on this deployment: "
                "APOLLO_API_KEY is missing.")
    if kind == ERR_HTTP and status in (401, 403):
        return ("Apollo rejected our API key (HTTP %s). The key needs to be "
                "renewed before company search will work." % status)
    if kind == ERR_HTTP and status == 429:
        return "Apollo is rate-limiting us. Try again in a minute."
    if kind == ERR_HTTP and status == 402:
        return ("Apollo refused the call for billing reasons (HTTP 402), "
                "so the account is likely out of credit.")
    if kind == ERR_HTTP:
        return ("Apollo returned an error (HTTP %s)." %
                (status if status is not None else "?"))
    if kind == ERR_TIMEOUT:
        return "Apollo did not respond in time. Try again in a moment."
    if kind == ERR_NETWORK:
        return "Apollo could not be reached. Try again in a moment."
    if kind == ERR_UNPARSABLE:
        return "Apollo's response could not be read."
    return "Company search is unavailable right now."


def is_retryable(err: dict | None) -> bool:
    """Whether trying the same call again could plausibly succeed. Drives
    whether the page offers a Retry button -- offering one for a revoked key
    would just teach people to click it."""
    if not isinstance(err, dict):
        return False
    if err.get("kind") in (ERR_TIMEOUT, ERR_NETWORK):
        return True
    return err.get("kind") == ERR_HTTP and err.get("status") in _RETRY_STATUSES


def _str(v):
    return v if isinstance(v, str) and v.strip() else None


def _to_company(org: dict) -> dict | None:
    """Normalize one Apollo organization row into the same shape
    arena_client._to_company produces, so a caller (or the frontend) cannot
    tell which vendor answered."""
    name = org.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    raw_id = org.get("id")
    company_id = raw_id if isinstance(raw_id, str) else (
        str(raw_id) if isinstance(raw_id, (int, float)) else "")
    domain = _str(org.get("primary_domain")) or _str(org.get("domain"))
    website = _str(org.get("website_url")) or (("https://" + domain) if domain else None)
    location = ", ".join(p for p in (
        _str(org.get("city")), _str(org.get("state")), _str(org.get("country"))
    ) if p) or None
    return {
        "id": company_id,
        "name": name.strip(),
        "logo": _str(org.get("logo_url")),
        "industry": _str(org.get("industry")),
        "location": location,
        "description": _str(org.get("short_description")),
        "summary": None,
        "followers_count": None,
        "profile_url": _str(org.get("linkedin_url")),
        "website": website,
    }


def _normalize_domain(url: str | None) -> str:
    d = (url or "").strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = re.sub(r"^www\.", "", d)
    return d.rstrip("/")


def _dedupe_companies(companies: list[dict]) -> list[dict]:
    """apollo_client.search_companies merges two buckets from a single
    Apollo response -- net-new "organizations" and this team's already-saved
    "accounts" -- and the SAME company can legitimately appear in both, or
    more than once within accounts if it was saved more than once. Apollo's
    own org id is not reliably consistent across the two buckets, but the
    website is, so dedupe on the normalized domain first and only fall back
    to id/name when a row has no domain at all. Keeps the first occurrence,
    which is the (fresher) "organizations" bucket's record when both exist,
    since apollo_client lists that bucket before accounts."""
    seen: set[str] = set()
    out: list[dict] = []
    for c in companies:
        key = _normalize_domain(c.get("website")) or (c.get("id") or "") \
            or (c.get("name") or "").strip().lower()
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(c)
    return out


def search_companies_result(company_name: str) -> dict:
    """Company search, with the reason attached when it fails. Same contract
    as arena_client.search_companies_result: `error` is None both on success
    and on a genuine zero-result search -- a caller tells those apart by
    whether `companies` is empty, and only claims "nothing matched" when
    error is None."""
    started = time.monotonic()
    key = _api_key()
    if not key:
        logger.info("sci_company_search: APOLLO_API_KEY not configured, skipping call")
        return {"companies": [], "error": _err(ERR_NOT_CONFIGURED,
                "APOLLO_API_KEY is not set on this deployment."),
                "elapsed_ms": 0, "source": ""}

    from tracker import apollo_client

    def _fail(err: dict) -> dict:
        elapsed = int((time.monotonic() - started) * 1000)
        logger.warning("sci_company_search: search failed for %r: %s (%s)",
                       company_name, err.get("kind"), err.get("detail"))
        return {"companies": [], "error": err, "elapsed_ms": elapsed, "source": ""}

    try:
        orgs = apollo_client.search_companies(
            {"name": company_name, "max_companies": 8}, key, per_page=8, strict=True)
    except requests.HTTPError as e:
        resp = getattr(e, "response", None)
        status = getattr(resp, "status_code", None)
        body = (getattr(resp, "text", "") or str(e)).strip()
        return _fail(_err(ERR_HTTP, "HTTP %s. Body: %s" %
                          (status if status is not None else "?", body[:300] or "(empty)"),
                          status=status))
    except requests.Timeout as e:
        return _fail(_err(ERR_TIMEOUT, "No response: %s" % e))
    except requests.RequestException as e:
        return _fail(_err(ERR_NETWORK, "%s: %s" % (type(e).__name__, e)))
    except Exception as e:
        return _fail(_err(ERR_UNPARSABLE, "%s: %s" % (type(e).__name__, e)))

    companies = _dedupe_companies([c for c in (_to_company(o) for o in orgs) if c is not None])
    elapsed = int((time.monotonic() - started) * 1000)
    return {"companies": companies, "error": None, "elapsed_ms": elapsed,
            "source": "mixed_companies/search"}


def search_companies(company_name: str) -> list[dict]:
    """Best-effort company search. [] if APOLLO_API_KEY isn't configured or
    the call fails -- never raises. Callers that need to explain an empty
    result should use search_companies_result instead."""
    return search_companies_result(company_name)["companies"]


def probe(company_name: str = "Microsoft") -> dict:
    """Prove this integration end to end and report exactly where it fails,
    in the same shape arena_client.probe / app.py's _apollo_selftest use.

    Probes with a company Apollo's own database is virtually certain to
    have, so a zero-result verdict is unambiguous: if this returns
    companies, the key and the response shape are both good, and an empty
    search on the page is a genuine absence of data rather than a broken
    integration. Costs 1 Apollo credit only if the probe itself returns a
    match (see apollo_client.search_companies)."""
    key = _api_key()
    out = {"configured": bool(key), "key_len": len(key), "probe": company_name,
           "elapsed_ms": 0, "http_status": None, "attempts": 1, "companies": 0,
           "sample": [], "source": "", "error_kind": "", "error": "", "detail": ""}
    if not key:
        out["error_kind"] = ERR_NOT_CONFIGURED
        out["error"] = describe_error(_err(ERR_NOT_CONFIGURED))
        return out
    try:
        result = search_companies_result(company_name)
    except Exception as e:  # defensive: probe must never 500 the admin page
        out["error_kind"] = "exception"
        out["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
        return out
    err = result.get("error")
    out["elapsed_ms"] = result.get("elapsed_ms", 0)
    out["source"] = result.get("source") or ""
    out["companies"] = len(result.get("companies") or [])
    out["sample"] = [c.get("name") for c in (result.get("companies") or [])[:5]]
    if err:
        out["http_status"] = err.get("status")
        out["error_kind"] = err.get("kind") or ""
        out["error"] = describe_error(err)
        out["detail"] = err.get("detail") or ""
    elif not out["companies"]:
        out["error"] = ("The call succeeded but even %s returned no companies, "
                        "which points at Apollo's own data or plan limits rather "
                        "than at this platform." % company_name)
    return out
