"""Paid Marketing Agents: which bidding a campaign has decides which levers work (no network).

A hand-set bid adjustment (by day, hour, age, gender, place or device) changes the bid only on a campaign whose bids are set by hand or from clicks. Smart Bidding sets each
bid from how likely that search is to convert and mostly ignores adjustments set by hand; Performance Max and Demand Gen take none of these. Advice that says "raise the bid for
Tuesdays" is wrong on those campaigns, so the agents that give it read each campaign's bidding strategy and channel and say which kind the spend is on.

class_of(channel, strategy) is one of ADJUSTABLE, SMART, AUTO or OTHER; shares() turns spend by class into shares of the total; mode() names the table's kind of bidding.
"""
from __future__ import annotations

ADJUSTABLE, SMART, AUTO, OTHER = "adjustable", "smart", "auto", "other"
CLASSES = (ADJUSTABLE, SMART, AUTO, OTHER)

_HAND = {"MANUAL_CPC", "MANUAL_CPM", "MANUAL_CPV", "TARGET_SPEND", "PERCENT_CPC"}               # bids set by hand or from clicks: an adjustment works
_SMART = {"TARGET_CPA", "TARGET_ROAS", "MAXIMIZE_CONVERSIONS", "MAXIMIZE_CONVERSION_VALUE"}      # bids set from each search's chance to convert: adjustments are mostly ignored
_NO_ADJUSTMENT_CHANNELS = {"PERFORMANCE_MAX", "DEMAND_GEN", "DISCOVERY"}                          # these take none of the hand-set adjustments

MAJORITY = 0.6                  # this share of a table's spend makes it a table of that kind


def class_of(channel, strategy):
    if str(channel or "") in _NO_ADJUSTMENT_CHANNELS:
        return AUTO
    s = str(strategy or "")
    return ADJUSTABLE if s in _HAND else SMART if s in _SMART else OTHER


def shares(costs):
    """{class: share of the spend} from {class: spend}, or None when there is no spend or nothing is known (every class OTHER or missing)."""
    total = sum(max(0.0, v) for v in (costs or {}).values())
    if total <= 0:
        return None
    out = {c: round(max(0.0, (costs or {}).get(c, 0.0)) / total, 4) for c in CLASSES}
    return out if out[OTHER] < 1.0 - 1e-9 else None


def mode(bidding):
    """"adjustable", "smart", "auto" when that kind holds 60% or more of the spend; "mixed" when none does; None when the bidding is not known."""
    if not bidding:
        return None
    for c in (SMART, AUTO, ADJUSTABLE):
        if bidding.get(c, 0.0) >= MAJORITY - 1e-9:
            return c
    return "mixed"


def merge(costs_list):
    """Spend by class from several sources added up."""
    out = {}
    for costs in costs_list:
        for c, v in (costs or {}).items():
            out[c] = out.get(c, 0.0) + v
    return out
