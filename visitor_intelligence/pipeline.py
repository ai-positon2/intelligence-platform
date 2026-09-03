"""
The single entry point the platform calls.

    from visitor_intelligence import resolve_visitor

    rec = resolve_visitor(ip="199.47.216.10", pages=["/pricing", "/demo"])

`rec` is a flat dict ready to drop into the Anonymous Visitors surface:
company + domain + confidence + connection_type + firmographics + intent.
Everything degrades gracefully: no keys, no network => you still get the
(gated) IP resolution.

Two-tier enrichment, by cost:

  1. FREE, always on: enrich_company_free() -- the company's own published
     schema.org/OpenGraph data, self-built tech-stack fingerprinting, and (for
     the minority that are US public filers) SEC EDGAR. Runs on every
     identified visitor, no credits spent, no API key needed.

  2. PAID, explicit opt-in only: deepen_with_apollo() -- a SEPARATE function,
     never called automatically by resolve_visitor(). Call it when a human
     has decided a specific lead is worth spending an Apollo credit on (e.g. a
     rep clicks "Enrich further" on one account), not on every page view.
     Adds what free sources genuinely cannot get for a private company:
     precise revenue/employee counts and a real buying committee.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .resolver import resolve_ip, Resolution
from .intent import score_intent
from . import enrich as _enrich
from . import free_enrich as _free


def resolve_visitor(ip: str,
                    pages: Optional[List[str]] = None,
                    sessions: int = 1,
                    engaged_seconds: int = 0,
                    ipinfo_token: Optional[str] = None,
                    check_sec: bool = False,
                    online: bool = True) -> Dict[str, Any]:
    """Free-tier resolution + enrichment. No Apollo call is ever made here --
    that is a deliberate, separate opt-in step (see deepen_with_apollo below),
    so this function can run on every visitor without spending anything."""
    res: Resolution = resolve_ip(ip, ipinfo_token=ipinfo_token, online=online)

    rec: Dict[str, Any] = {
        "ip": ip,
        "identifiable": res.identifiable,
        "connection_type": res.connection_type,
        "confidence": res.confidence,
        "company": res.company,
        "domain": res.domain,
        "asn": res.asn,
        "asn_org": res.asn_org,
        "country": res.country,
        "city": res.city,
        "method": res.method,
        "methods": res.methods,
        "is_vpn": res.is_vpn,
        "is_proxy": res.is_proxy,
        "is_hosting": res.is_hosting,
        "reasons": res.reasons,
        # firmographics -- filled below from free sources; deepen_with_apollo
        # can fill in what free sources can't (revenue, precise headcount).
        "industry": None, "employees": None, "employee_range": None,
        "revenue": None, "hq_country": res.country, "hq_city": None,
        "linkedin_url": None, "technologies": [], "description": None,
        "social_links": [], "buying_committee": [], "enrichment_source": None,
        # intent
        "intent_score": 0.0, "intent_stage": "awareness", "intent_reasons": [],
    }

    if res.identifiable and res.domain:
        free = _free.enrich_company_free(res.domain, check_sec=check_sec)
        if free:
            rec["company"] = free.get("name") or rec["company"]
            rec["description"] = free.get("description")
            rec["hq_city"] = free.get("hq_city")
            rec["hq_country"] = free.get("hq_country") or rec["hq_country"]
            rec["linkedin_url"] = free.get("linkedin_url")
            rec["social_links"] = free.get("social_links") or []
            rec["technologies"] = free.get("technologies") or []
            rec["founded_year"] = free.get("founded_year")
            rec["logo_url"] = free.get("logo_url")
            if free.get("sec_public_filer"):
                rec["industry"] = free.get("sec_industry")
                rec["sec_cik"] = free.get("sec_cik")
            rec["enrichment_source"] = "free"
            # A confirmed real-world company page corroborates the IP match.
            rec["confidence"] = min(1.0, round(rec["confidence"] + 0.05, 3))

    # Intent
    score, stage, ireasons = score_intent(
        pages or [], pageviews=len(pages or []), sessions=sessions,
        engaged_seconds=engaged_seconds)
    rec["intent_score"] = score
    rec["intent_stage"] = stage
    rec["intent_reasons"] = ireasons
    return rec


def deepen_with_apollo(rec: Dict[str, Any], apollo_key: Optional[str] = None,
                    with_committee: bool = False) -> Dict[str, Any]:
    """Explicit, human-triggered enrichment of an ALREADY-resolved record.
    Call this only when someone has decided this specific lead is worth
    spending Apollo credits on (e.g. a rep clicking "Enrich further"), never
    automatically per visitor/page-view. Mutates and returns `rec`; a no-op if
    there's no domain or no key."""
    apollo_key = apollo_key if apollo_key is not None else os.environ.get("APOLLO_API_KEY", "")
    domain = rec.get("domain")
    if not (domain and apollo_key):
        return rec
    firmo = _enrich.enrich_company(domain, apollo_key)
    if firmo:
        rec["company"] = firmo.get("name") or rec["company"]
        rec["domain"] = firmo.get("domain") or rec["domain"]
        rec["industry"] = firmo.get("industry") or rec.get("industry")
        rec["employees"] = firmo.get("employees")
        rec["employee_range"] = firmo.get("employee_range")
        rec["revenue"] = firmo.get("revenue")
        rec["hq_country"] = firmo.get("hq_country") or rec.get("hq_country")
        rec["linkedin_url"] = firmo.get("linkedin_url") or rec.get("linkedin_url")
        rec["technologies"] = firmo.get("technologies") or rec.get("technologies") or []
        rec["apollo_org_id"] = firmo.get("apollo_org_id")
        rec["description"] = firmo.get("description") or rec.get("description")
        rec["enrichment_source"] = "apollo"
        rec["confidence"] = min(1.0, round(rec.get("confidence", 0.0) + 0.1, 3))
        if with_committee and firmo.get("apollo_org_id"):
            rec["buying_committee"] = _enrich.buying_committee(
                firmo["apollo_org_id"], apollo_key)
    return rec
