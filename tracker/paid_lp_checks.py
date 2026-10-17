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


def run(page):
    """The findings for one landing page record (see paid_lp_checks_page.Ctx for what a record carries), in section order."""
    ctx = page_checks.Ctx(page)
    out = []
    for cid, fn in CHECKS:
        try:
            out.append(fn(ctx))
        except Exception:                                   # noqa: BLE001 (one broken check must not take the page's report down)
            log.exception("landing page check %s failed on %s", cid, page.get("url"))
            out.append(model.unknown(cid, "the check could not run"))
    order = {key: spec["order"] for key, spec in model.SECTIONS.items()}
    out.sort(key=lambda f: order[f["section"]])             # stable: a section's checks stay in the order they are written
    return out
