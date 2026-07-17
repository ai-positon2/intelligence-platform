"""
Firmographic + person enrichment, reusing the platform's existing Apollo client.

Given a resolved company DOMAIN, we call Apollo to attach the full firmographic
profile (name, industry, size, revenue, HQ, LinkedIn, tech). Given a known/first-
party EMAIL, we resolve the individual (name, title, LinkedIn) via Apollo people
match. Both are cached and fail soft: no Apollo key => the resolver's raw output
is returned unchanged, so the surface never breaks.

We deliberately reuse tracker.apollo_client (same key, same retry/backoff, same
account) rather than adding a second Apollo integration.
"""

from __future__ import annotations

import time
import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

try:  # reuse the platform's Apollo client if importable
    from tracker.apollo_client import enrich_company as _apollo_enrich_company
    from tracker.apollo_client import get_leadership as _apollo_get_leadership
    from tracker.apollo_client import _post as _apollo_post
except Exception:  # pragma: no cover - keeps engine importable in isolation
    _apollo_enrich_company = None
    _apollo_get_leadership = None
    _apollo_post = None


# In-process caches (domain/email -> (expiry, data)). Firmographics move slowly,
# so a multi-day TTL turns N visits from one company into 1 Apollo call.
_CO_CACHE: Dict[str, tuple] = {}
_PPL_CACHE: Dict[str, tuple] = {}
_TTL = 7 * 86400


def _fresh(cache: Dict[str, tuple], key: str) -> Optional[Any]:
    hit = cache.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    return None


def enrich_company(domain: str, api_key: str) -> Dict[str, Any]:
    """Domain -> normalized firmographic dict. {} if unavailable."""
    if not domain:
        return {}
    key = domain.lower()
    cached = _fresh(_CO_CACHE, key)
    if cached is not None:
        return cached
    out: Dict[str, Any] = {}
    if _apollo_enrich_company and api_key:
        try:
            org = _apollo_enrich_company(domain, api_key) or {}
            out = _normalize_org(org)
        except Exception as e:  # pragma: no cover
            log.warning("apollo enrich_company failed domain=%s: %s", domain, e)
            out = {}
    _CO_CACHE[key] = (time.time() + _TTL, out)
    return out


def buying_committee(apollo_org_id: str, api_key: str,
                    max_people: int = 8) -> List[Dict[str, Any]]:
    """Pull key people at the identified company (org-level, not the specific
    visitor) so a rep has a ready buying committee. Uses Apollo people search."""
    if not (apollo_org_id and _apollo_get_leadership and api_key):
        return []
    key = "org:%s" % apollo_org_id
    cached = _fresh(_PPL_CACHE, key)
    if cached is not None:
        return cached
    try:
        people = _apollo_get_leadership(apollo_org_id, api_key, max_people=max_people) or []
    except Exception as e:  # pragma: no cover
        log.warning("apollo buying_committee failed org=%s: %s", apollo_org_id, e)
        people = []
    _PPL_CACHE[key] = (time.time() + _TTL, people)
    return people


def enrich_person(api_key: str, email: Optional[str] = None,
                hashed_email: Optional[str] = None, name: Optional[str] = None,
                domain: Optional[str] = None) -> Dict[str, Any]:
    """Resolve one person via Apollo people/match. Accepts a plain email, a
    hashed email (MD5/SHA-256, so plaintext never leaves your systems), or
    name+domain. Returns {full_name,title,email,linkedin_url,company} or {}.
    Costs 1 Apollo credit per successful match."""
    if not (api_key and _apollo_post):
        return {}
    if not (email or hashed_email or (name and domain)):
        return {}
    cache_key = "p:%s" % (email or hashed_email or ("%s@%s" % (name, domain)))
    cached = _fresh(_PPL_CACHE, cache_key)
    if cached is not None:
        return cached
    payload: Dict[str, Any] = {}
    if email:
        payload["email"] = email.strip().lower()
    if hashed_email:
        payload["hashed_email"] = hashed_email.strip().lower()
    if name:
        payload["name"] = name
    if domain:
        payload["domain"] = domain
    out: Dict[str, Any] = {}
    try:
        data = _apollo_post("people/match", payload, api_key) or {}
        p = data.get("person") or {}
        if p:
            first = (p.get("first_name") or "").strip()
            last = (p.get("last_name") or "").strip()
            org = p.get("organization") or {}
            out = {
                "full_name": ("%s %s" % (first, last)).strip() or p.get("name"),
                "title": p.get("title"),
                "email": p.get("email") or email,
                "linkedin_url": p.get("linkedin_url"),
                "company": org.get("name") if isinstance(org, dict) else None,
                "apollo_person_id": p.get("id"),
            }
    except Exception as e:  # pragma: no cover
        log.warning("apollo enrich_person failed: %s", e)
        out = {}
    _PPL_CACHE[cache_key] = (time.time() + _TTL, out)
    return out


def _normalize_org(org: Dict[str, Any]) -> Dict[str, Any]:
    if not org:
        return {}
    emp = org.get("estimated_num_employees")
    rev = org.get("annual_revenue_printed") or org.get("organization_revenue_printed")
    return {
        "apollo_org_id": org.get("id"),
        "name": org.get("name"),
        "domain": org.get("primary_domain") or org.get("domain") or org.get("website_url"),
        "industry": org.get("industry"),
        "employees": emp,
        "employee_range": _emp_range(emp),
        "revenue": rev,
        "hq_city": org.get("city"),
        "hq_state": org.get("state"),
        "hq_country": org.get("country"),
        "linkedin_url": org.get("linkedin_url"),
        "founded_year": org.get("founded_year"),
        "phone": org.get("phone") or (org.get("primary_phone") or {}).get("number"),
        "keywords": (org.get("keywords") or [])[:12],
        "technologies": [t.get("name") if isinstance(t, dict) else t
                        for t in (org.get("current_technologies") or [])][:15],
        "description": (org.get("short_description") or "")[:400],
    }


def _emp_range(n: Optional[Any]) -> Optional[str]:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    for lo, hi, label in [(0, 10, "1-10"), (11, 50, "11-50"), (51, 200, "51-200"),
                          (201, 500, "201-500"), (501, 1000, "501-1K"),
                          (1001, 5000, "1K-5K"), (5001, 10000, "5K-10K")]:
        if lo <= n <= hi:
            return label
    return "10K+"
