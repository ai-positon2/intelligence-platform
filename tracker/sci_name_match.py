"""Does this account plausibly belong to this company?

Extracted from tracker/sci_youtube_client.py when Reddit became the second
platform to resolve a company against a vendor's own search index. Both
vendors have the same failure mode and it is the dangerous one: a search
endpoint returns a confident-looking top hit for almost any plausible query,
including queries with no real answer. Searching YouTube for "Harborview
Compliance Systems" returns "Outdoor Blinds and Awnings Australia" -- a
verified-live example, not a hypothetical. Attaching that account would make
the report silently describe a different company's content as this one's.

This lives in one module rather than being copied per platform because a
second copy is how the two quietly diverge: a fix made to whichever file the
next bug is reported against leaves the other one still wrong.
"""

from __future__ import annotations

import re

# Corporate boilerplate carries no identifying signal, so it must not be
# what makes a company name "match" an account title -- otherwise "Acme
# Systems" matches "Fairview Systems" on the strength of "systems" alone.
_CORP_NOISE = {
    "inc", "llc", "ltd", "limited", "corp", "corporation", "co", "company",
    "group", "holdings", "plc", "gmbh", "pvt", "private", "the", "and",
    "technologies", "technology", "solutions", "systems", "services",
    "partners", "labs", "global", "international",
}


def name_tokens(value: str) -> set[str]:
    """The significant lowercase word-tokens of a name. Falls back to the
    raw tokens when a name is nothing BUT boilerplate (e.g. "The Co"), since
    an empty set would otherwise match everything."""
    tokens = re.findall(r"[a-z0-9]+", (value or "").lower())
    significant = [t for t in tokens if t not in _CORP_NOISE]
    return set(significant or tokens)


def plausible_match(company_name: str, account_title: str) -> bool:
    """Deliberately conservative: a company whose account is branded under a
    genuinely different name is rejected and reported as not found, because
    a missing platform is recoverable and a wrong one silently poisons the
    whole report."""
    a, b = name_tokens(company_name), name_tokens(account_title)
    if not a or not b:
        return False
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if shorter <= longer:
        return True
    # Spacing differs between a company name and its account handle
    # ("Gentle Dental" vs "GentleDental"), so compare with separators
    # stripped as well.
    flat_a = re.sub(r"[^a-z0-9]", "", (company_name or "").lower())
    flat_b = re.sub(r"[^a-z0-9]", "", (account_title or "").lower())
    if flat_a and flat_b and (flat_a in flat_b or flat_b in flat_a):
        return True
    return len(a & b) / len(a | b) >= 0.5
