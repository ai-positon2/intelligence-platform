"""Paid Marketing Agents: Landing Page & Relevancy Check, every check on one page.

The checks about the page itself are tracker/paid_lp_checks_page.py, the ones that hold the page against the ads are
tracker/paid_lp_checks_match.py. `run(page)` runs all of them and returns the findings (see tracker/paid_lp_model.py for what a finding is).
A check that raises is a bug, but it never costs the report: that one check says it could not run and the rest go on.
"""
from __future__ import annotations

import logging

from . import paid_lp_checks_match as match_checks
from . import paid_lp_checks_page as page_checks
from . import paid_lp_model as model

log = logging.getLogger(__name__)

CHECKS = page_checks.CHECKS + match_checks.CHECKS

# A check that holds a page against what only one platform knows runs only for a page that platform's ads send people to. Every other check runs for every page.
GOOGLE_ONLY = frozenset(("reach_params", "reach_adsbot", "speed_google", "trust_tracking", "match_keywords_page", "match_keywords_headline", "match_terms", "match_lp_experience",
                         "match_ad_relevance"))
META_ONLY = frozenset(("reach_params_meta", "trust_meta_pixel", "match_meta_conversion", "match_meta_quality"))


def applies(check_id, platforms):
    """True when the check runs for a page that these platforms' ads send people to."""
    if check_id in GOOGLE_ONLY:
        return "google" in platforms
    if check_id in META_ONLY:
        return "meta" in platforms
    return True


def run(page):
    """The findings for one landing page record (see paid_lp_checks_page.Ctx for what a record carries), in section order. A page reached by Meta ads alone is not held against
    Google's keywords, Quality Score or tag, and a page reached by Google ads alone is not held against Meta's pixel or ratings."""
    ctx = page_checks.Ctx(page)
    out = []
    for cid, fn in CHECKS:
        if not applies(cid, ctx.platforms):
            continue
        try:
            out.append(fn(ctx))
        except Exception:                                   # noqa: BLE001 (one broken check must not take the page's report down)
            log.exception("landing page check %s failed on %s", cid, page.get("url"))
            out.append(model.unknown(cid, "the check could not run"))
    order = {key: spec["order"] for key, spec in model.SECTIONS.items()}
    out.sort(key=lambda f: order[f["section"]])             # stable: a section's checks stay in the order they are written
    return out
