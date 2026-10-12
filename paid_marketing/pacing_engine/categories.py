"""Campaign -> category.

The category is the unit an account manager actually moves money between.
Nobody funds `P2_Heartland_MRI_ET_B`; they fund MRI. For OIA the shared budget
pools are literally named after these categories (MRI, CTScan, Ultrasound), so
the category is also the only level at which the budget CAN be changed.

Each client names campaigns differently, so each gets a rule. The fallback is
Google's own campaign type, which is always present.
"""
from __future__ import annotations

import re

# Canonical labels. Order matters: longest/most specific patterns first.
_CANON = [
    (r"pmax|performance[_\s-]?max", "PMax"),
    (r"demand[_\s-]?gen|demandgen", "Demand Gen"),
    (r"non[_\s-]?brand", "Non Brand"),
    (r"brand", "Brand"),
    (r"ct[_\s-]?scan", "CT Scan"),
    (r"mri", "MRI"),
    (r"ultra[_\s-]?sound", "Ultrasound"),
    (r"mammogram|mammo", "Mammogram"),
    (r"bone[_\s-]?density", "Bone Density"),
    (r"x[_\s-]?ray", "X-Ray"),
    (r"\bpet\b", "PET"),
    (r"generic", "Generic"),
    (r"display", "Display"),
    (r"shopping", "Shopping"),
    (r"video", "Video"),
    (r"broad", "Broad"),
    (r"phrase", "Phrase"),
    (r"exact", "Exact"),
    (r"search", "Search"),
]

# Match-type / variant suffixes that are NOT categories.
_NOISE = re.compile(
    r"(^|_)(e|p|b|et|et_b|smart|all|\d+|set[_-]?[a-z]|"
    r"created on .*|new|test\d*|copy)($|_)", re.I)


def canon(token: str) -> str | None:
    t = str(token or "").strip()
    if not t:
        return None
    for pat, label in _CANON:
        if re.search(pat, t, re.I):
            return label
    return None


def _from_name(campaign: str, drop: list[str] | None = None) -> str | None:
    """Walk the campaign name's tokens and return the first real category."""
    name = str(campaign)
    for d in (drop or []):
        name = re.sub(re.escape(d), " ", name, flags=re.I)
    # try the whole string first so multi-word categories survive tokenising
    for pat, label in _CANON:
        if re.search(pat, name, re.I):
            return label
    for tok in re.split(r"[_\-\s]+", name):
        if _NOISE.match(tok):
            continue
        c = canon(tok)
        if c:
            return c
    return None


# --------------------------------------------------------------------------- #
# Per-client rules. account = the Budget Tracker account name for the row.
# --------------------------------------------------------------------------- #
def _oia(campaign: str, account: str, gtype: str) -> str:
    # P2_<Account>_<Category>_<MatchType>   e.g. P2_Heartland_CTScan_ET_B
    return _from_name(campaign, drop=["P2", account, account.replace("-", " ")]) \
        or canon(gtype) or (gtype or "Other")


def _gd(campaign: str, account: str, gtype: str) -> str:
    # <Location>_<Type>  e.g. Arlington_Brand, Attleboro_Display_Custom_Intent
    parts = str(campaign).split("_", 1)
    tail = parts[1] if len(parts) > 1 else parts[0]
    return _from_name(tail) or canon(gtype) or (gtype or "Other")


def _riccobene(campaign: str, account: str, gtype: str) -> str:
    # P2_<Type>_<Location>   e.g. P2_Pmax_Emergency_Winston-Salem
    c = str(campaign)
    if re.search(r"emergency", c, re.I):
        return "PMax Emergency"
    return _from_name(c, drop=["P2", account]) or canon(gtype) or (gtype or "Other")


RULES = {
    "oia": _oia,
    "gd & affiliates": _gd,
    "riccobene": _riccobene,
}


def categorise(client: str, campaign: str, account: str, gtype: str) -> str:
    rule = RULES.get(str(client).strip().lower())
    if rule:
        try:
            return rule(campaign, account, gtype)
        except Exception:
            pass
    return _from_name(campaign, drop=[account]) or canon(gtype) or canon(gtype) or (gtype or "Other")
