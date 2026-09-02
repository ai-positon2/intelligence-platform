"""Step 3 (RESOLVE COMPANIES + FIND PEOPLE) for Event & Conference Intelligence.

Apollo's two search endpoints bill very differently, and this module's whole
shape follows from that (see tracker/apollo_client.py's own docstrings, which
are the authority):

  mixed_companies/search  ~1 credit PER CALL that returns at least one row.
  mixed_people/api_search  free.

Per call, not per record. So resolving 200 exhibitors one name at a time is
200 credits, while resolving them in batches of 25 by domain is 8. That is
the entire reason harvesting works so hard to keep each exhibitor's published
link: a batch lookup keyed on domains is roughly 25x cheaper than the obvious
name-at-a-time loop, and it is also more accurate, because a domain is exact
where a fuzzy name match is not.

Two rules carried over from Contact Finder's thirteen audit rounds:

  * Only explicit user action spends credits. The harvest stage is free; this
    stage runs when someone asks for it, and estimate_cost() lets the UI say
    what it will cost BEFORE it is spent.
  * A company with no published link is reported unresolved, never guessed.
    Deriving `acme.com` from "Acme" is the defect already logged against
    `_cpi_probe_company_free`, and at roster scale it would attach real
    firmographics belonging to someone else to a meaningful share of rows.

Finding PEOPLE at the resolved companies is free, so it always runs when
company resolution has produced domains. That is the step that turns a list
of exhibiting companies into named humans to talk to.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Apollo caps a domain list per request well above this, but 25 keeps one
# failed batch cheap to retry and keeps a partial result useful.
DOMAIN_BATCH = 25
# Free endpoint, so this is a latency budget rather than a cost one.
PEOPLE_PAGE = 25


def api_key() -> str:
    return os.environ.get("APOLLO_API_KEY", "")


def estimate_cost(domains: list[str]) -> dict:
    """What resolving these domains will cost, before anything is spent.

    Apollo bills a company search per CALL, and only when it returns at least
    one row, so this is a ceiling rather than a prediction: a batch matching
    nothing costs nothing. Stated as a ceiling because a user deciding
    whether to spend needs the worst case, not the average.
    """
    n = len(set(d for d in domains if d))
    batches = (n + DOMAIN_BATCH - 1) // DOMAIN_BATCH if n else 0
    return {"domains": n, "batches": batches, "max_credits": batches,
            "note": ("Up to %d Apollo credit%s: company search bills once per "
                     "request, and %d domain%s fit in %d request%s of %d. A "
                     "request that matches nothing is not billed. Finding "
                     "people at the matched companies afterwards is free."
                     % (batches, "" if batches == 1 else "s", n,
                        "" if n == 1 else "s", batches,
                        "" if batches == 1 else "s", DOMAIN_BATCH))}


def _index_by_domain(companies: list[dict]) -> dict[str, dict]:
    """Apollo rows keyed by every domain field they carry.

    A company row can come back with its primary domain, its website URL and
    a list of secondary domains that do not all agree, and the roster asked
    by exactly one of them. Indexing every variant is what stops a matched
    company being reported unresolved because the directory linked to the
    marketing domain and Apollo answered with the corporate one.
    """
    from .event_intel_harvest import clean_domain
    out: dict[str, dict] = {}
    for c in companies or []:
        candidates = [c.get("primary_domain"), c.get("domain"), c.get("website_url"),
                      c.get("website")]
        for extra in (c.get("domains") or []):
            candidates.append(extra)
        for cand in candidates:
            d = clean_domain(cand)
            if d and d not in out:
                out[d] = c
    return out


def _slim(c: dict) -> dict:
    """Only the firmographics the report renders, so a JSONB column does not
    accumulate a whole Apollo payload per participant row."""
    return {
        "apollo_id": c.get("id"),
        "name": c.get("name"),
        "domain": c.get("primary_domain") or c.get("domain"),
        "website": c.get("website_url") or c.get("website"),
        "industry": c.get("industry"),
        "employees": c.get("estimated_num_employees") or c.get("employee_count"),
        "revenue": c.get("annual_revenue_printed") or c.get("annual_revenue"),
        "location": (c.get("location") or ", ".join(
            [x for x in (c.get("city"), c.get("state"), c.get("country")) if x]) or None),
        "founded_year": c.get("founded_year"),
        "logo": c.get("logo_url"),
        "linkedin": c.get("linkedin_url"),
    }


def resolve_companies(domains: list[str], key: str | None = None,
                      strict: bool = True) -> dict:
    """Batch-resolve domains to Apollo companies.

    Returns {"by_domain": {...}, "credits": int, "unmatched": [...],
             "error": str|None}. `credits` counts only batches that actually
    returned rows, matching how Apollo bills, so a run's recorded spend is
    the real one rather than a worst case.

    strict=True asks apollo_client to re-raise transport failures instead of
    returning [], because an empty result would otherwise be indistinguishable
    from "none of these companies exist" and get written into the report as a
    fact about the world. That exact conflation is audit round 9.
    """
    from . import apollo_client
    key = key or api_key()
    uniq = sorted({d for d in domains if d})
    result = {"by_domain": {}, "credits": 0, "unmatched": [],
              "unattempted": [], "error": None}
    if not uniq:
        return result
    if not key:
        # Nothing was looked up, so nothing is unmatched. Reporting these as
        # unmatched would say Apollo has no record of companies Apollo was
        # never asked about.
        result["error"] = "APOLLO_API_KEY is not configured on this deployment."
        result["unattempted"] = uniq
        return result

    matched: dict[str, dict] = {}
    attempted: list[str] = []
    for i in range(0, len(uniq), DOMAIN_BATCH):
        batch = uniq[i:i + DOMAIN_BATCH]
        try:
            rows = apollo_client.search_companies(
                {"domains": batch, "max_companies": len(batch)}, key,
                per_page=min(len(batch), 100), strict=strict)
        except Exception as e:
            # Partial results are kept. Half a resolved roster plus an honest
            # error beats discarding work already paid for.
            result["error"] = "Apollo company lookup failed: %s" % str(e)[:200]
            logger.warning("event_intel_enrich: company batch %d failed: %s", i, e)
            break
        # Counted only once the call has returned. A batch that raised was not
        # an answer about the companies in it, and every batch after it never
        # ran at all: on a 200-domain roster failing at batch 3, that is 125
        # companies. Reporting those as unmatched hands the caller the exact
        # sentence this module exists to refuse, "we looked and Apollo has no
        # record", about companies nobody looked up.
        attempted.extend(batch)
        if rows:
            result["credits"] += 1
        for domain, row in _index_by_domain(rows).items():
            matched.setdefault(domain, row)

    result["by_domain"] = {d: _slim(c) for d, c in matched.items() if d in set(uniq)}
    done = set(attempted)
    result["unmatched"] = [d for d in attempted if d not in result["by_domain"]]
    result["unattempted"] = [d for d in uniq if d not in done]
    return result


def find_people(domains: list[str], titles: list[str] | None = None,
                key: str | None = None, per_company: int = 5) -> dict:
    """Free people lookup at resolved companies.

    mixed_people/api_search costs nothing, so this runs for every resolved
    domain without asking. It returns names, titles and LinkedIn URLs; it does
    NOT reveal email or phone, which is what actually bills. Revealing is left
    to Contact Finder, where the credit accounting and history already exist.
    """
    from . import apollo_client
    key = key or api_key()
    uniq = sorted({d for d in domains if d})
    out: dict = {"by_domain": {}, "error": None, "total": 0}
    if not uniq:
        return out
    if not key:
        out["error"] = "APOLLO_API_KEY is not configured on this deployment."
        return out

    filters: dict = {"company_domains": uniq}
    if titles:
        # "titles" is the PERSON-title key, which apollo_client maps to
        # person_titles. "job_titles" is an employer-level key mapping to
        # q_organization_job_titles, which means "companies with open job
        # postings for these titles": a different question with a plausible
        # name. Asking it returned arbitrary employees, at only the subset of
        # exhibitors currently hiring a VP Marketing, and zero people at every
        # exhibitor that was not. The title box did the opposite of its label,
        # silently, on the one step that bills.
        filters["titles"] = list(titles)

    people: list[dict] = []
    try:
        # Paged rather than one big request: Apollo caps per_page, and a
        # roster of 40 companies at 5 people each needs several pages.
        wanted = min(len(uniq) * per_company, 200)
        page = 1
        while len(people) < wanted and page <= 8:
            rows = apollo_client.search_people(filters, key, page=page,
                                               per_page=PEOPLE_PAGE, strict=True)
            if not rows:
                break
            people.extend(rows)
            if len(rows) < PEOPLE_PAGE:
                break
            page += 1
    except Exception as e:
        out["error"] = "Apollo people lookup failed: %s" % str(e)[:200]
        logger.warning("event_intel_enrich: people lookup failed: %s", e)

    from .event_intel_harvest import clean_domain
    wanted_domains = set(uniq)
    for p in people:
        d = clean_domain(p.get("company_domain") or p.get("organization_domain")
                         or p.get("company_website") or "")
        # Apollo's q_organization_domains_list is a relevance hint, not a
        # hard filter (apollo_client says so in its own comments), so rows
        # for companies nobody asked about do come back. Dropping them here
        # is what keeps a person from being shown under the wrong exhibitor.
        if not d or d not in wanted_domains:
            continue
        bucket = out["by_domain"].setdefault(d, [])
        if len(bucket) >= per_company:
            continue
        bucket.append({
            "name": p.get("name") or " ".join(
                [x for x in (p.get("first_name"), p.get("last_name")) if x]) or None,
            "title": p.get("title"),
            "seniority": p.get("seniority"),
            "linkedin": p.get("linkedin_url"),
            "location": p.get("location") or p.get("city"),
        })
    out["total"] = sum(len(v) for v in out["by_domain"].values())
    return out
