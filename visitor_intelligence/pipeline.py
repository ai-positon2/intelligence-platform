"""
The single entry point the platform calls.

    from visitor_intelligence import resolve_visitor

    rec = resolve_visitor(ip="199.47.216.10",
                        pages=["/pricing", "/demo"],
                        apollo_key=APOLLO_KEY, ipinfo_token=IPINFO_TOKEN)

`rec` is a flat dict ready to drop into the Anonymous Visitors surface:
company + domain + confidence + connection_type + firmographics + intent +
optional buying committee. Everything degrades gracefully: no keys => you still
get the (gated) IP resolution, just without firmographics.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .resolver import resolve_ip, Resolution
from .intent import score_intent
from . import enrich as _enrich


def resolve_visitor(ip: str,
                    pages: Optional[List[str]] = None,
                    sessions: int = 1,
                    engaged_seconds: int = 0,
                    apollo_key: Optional[str] = None,
                    ipinfo_token: Optional[str] = None,
                    with_committee: bool = False,
                    online: bool = True) -> Dict[str, Any]:
    apollo_key = apollo_key if apollo_key is not None else os.environ.get("APOLLO_API_KEY", "")
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
        # firmographics (filled below if identifiable + apollo key)
        "industry": None, "employees": None, "employee_range": None,
        "revenue": None, "hq_country": res.country, "linkedin_url": None,
        "technologies": [], "buying_committee": [],
        # intent
        "intent_score": 0.0, "intent_stage": "awareness", "intent_reasons": [],
    }

    # Firmographic enrichment (only for identifiable business/edu/gov visitors)
    if res.identifiable and res.domain and apollo_key:
        firmo = _enrich.enrich_company(res.domain, apollo_key)
        if firmo:
            # Apollo's canonical name/domain overrides our guess.
            rec["company"] = firmo.get("name") or rec["company"]
            rec["domain"] = firmo.get("domain") or rec["domain"]
            rec["industry"] = firmo.get("industry")
            rec["employees"] = firmo.get("employees")
            rec["employee_range"] = firmo.get("employee_range")
            rec["revenue"] = firmo.get("revenue")
            rec["hq_country"] = firmo.get("hq_country") or rec["hq_country"]
            rec["linkedin_url"] = firmo.get("linkedin_url")
            rec["technologies"] = firmo.get("technologies") or []
            rec["apollo_org_id"] = firmo.get("apollo_org_id")
            rec["description"] = firmo.get("description")
            # Getting a canonical Apollo record corroborates the match.
            rec["confidence"] = min(1.0, round(rec["confidence"] + 0.1, 3))
            if with_committee and firmo.get("apollo_org_id"):
                rec["buying_committee"] = _enrich.buying_committee(
                    firmo["apollo_org_id"], apollo_key)

    # Intent
    score, stage, ireasons = score_intent(
        pages or [], pageviews=len(pages or []), sessions=sessions,
        engaged_seconds=engaged_seconds)
    rec["intent_score"] = score
    rec["intent_stage"] = stage
    rec["intent_reasons"] = ireasons
    return rec
