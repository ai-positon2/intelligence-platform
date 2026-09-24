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
import unicodedata

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


# NFKD decomposes a letter into "base + combining mark" only where such a
# decomposition exists. These Latin letters have none -- the stroke or the
# ligature IS the letter -- so a Scandinavian, Polish, German or Icelandic
# name typed on an ASCII keyboard (Soren for Søren, Lech Walesa for Wałęsa)
# would still never match its real spelling without this map.
_LATIN_FOLD = str.maketrans({
    "ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE",
    "ß": "ss", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ð": "d", "Ð": "D",
    "þ": "th", "Þ": "TH", "ı": "i", "ŋ": "n", "Ŋ": "N",
})


def person_name_tokens(value: str) -> set[str]:
    """name_tokens for a PERSON's name, which is not always written in
    ASCII the way a B2B company's registered name effectively always is.

    name_tokens' own pattern is [a-z0-9]+, so it splits an accented name
    mid-word ("Jose Angel" and "Jose Angel" with its real accents produce
    different tokens and never match each other) and returns the EMPTY SET
    for a name written entirely in a non-Latin script. An empty set is the
    dangerous case: every caller that asks "is this the same person" reads
    it as "no", so a Cyrillic/CJK/Arabic/Devanagari name never matches even
    a character-for-character identical one -- Thought Leader Intelligence
    was rejecting the person's OWN LinkedIn profile as "not this person" on
    exactly that basis, and its LinkedIn pulse was failing open and
    counting the subject's own posts as other people's reaction to them.

    Accents are folded away rather than kept so a name typed without them
    still matches the same person written with them. Kept additive: nothing
    that already calls name_tokens/plausible_match changes behavior, since
    for a name that is already plain ASCII this returns exactly what
    name_tokens does."""
    folded = "".join(ch for ch in unicodedata.normalize("NFKD", (value or "").translate(_LATIN_FOLD))
                     if not unicodedata.combining(ch))
    tokens = name_tokens(folded)
    if tokens:
        return tokens
    return set(re.findall(r"[^\W_]+", (value or "").lower()))


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
