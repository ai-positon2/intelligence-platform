"""Market Radar: what a provider call costs, as pure arithmetic.

No database and no network here, so every number can be tested exactly.
tracker/market_radar_ledger.py stores what this module computes.

Two figures exist for every paid call:

* a RESERVATION, booked before the call is sent: the most it can cost
  (every input token at the full rate, the whole output budget, every
  search). The run's hard cap is checked against it, so a call that could
  push the run past the cap is refused before any money is spent;
* the MEASURED cost, computed from the usage the provider reports back.

A call whose usage never comes back keeps counting at its reservation, never
at zero, and the run's total is then marked partial. That is the lesson of
Event Intelligence's ledger (tracker/event_intel_costs.py), kept here.

Model rates are the Claude API list prices, checked against the source below.
Discounts, tax and negotiated rates are not modelled.
"""
from __future__ import annotations

import math
from decimal import Decimal

PRICING_SOURCE = "https://platform.claude.com/docs/en/about-claude/pricing"
PRICING_CHECKED = "2026-10-08"

M = Decimal(1_000_000)

# USD per million tokens: (input, 5-minute cache write, 1-hour cache write,
# cache read, output). Batch API halves all of them.
MODEL_RATES = {
    "claude-opus-5-5": (Decimal("4"), Decimal("5"), Decimal("8"), Decimal("0.20"), Decimal("20")),
    "claude-sonnet-5-5": (Decimal("2"), Decimal("2.50"), Decimal("4"), Decimal("0.10"), Decimal("10")),
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.125"), Decimal("0.20"), Decimal("0.01"), Decimal("0.50")),
}
# Haiku 5.5 alone is priced by prompt length: a prompt over 100,000 tokens
# pays these rates for the whole request.
HAIKU_LONG_PROMPT_TOKENS = 100_000
HAIKU_LONG_RATES = (Decimal("0.50"), Decimal("0.625"), Decimal("1"), Decimal("0.05"), Decimal("2.50"))

BATCH_MULTIPLIER = Decimal("0.5")
USD_PER_WEB_SEARCH = Decimal("0.01")   # Claude's web_search tool: $10 per 1,000

# Non-model providers, priced per unit. A provider that reports its own
# charge (Apify returns each run's cost) is recorded with that figure
# instead. Apollo is billed in credits, not dollars, so it has no USD rate
# and is totalled separately.
PROVIDER_RATES = {
    "google_places_pro": {
        "usd_per_unit": Decimal("0.032"), "unit": "request",
        "source": "https://developers.google.com/maps/billing-and-pricing/pricing",
        "checked": "2026-10-08",
        "note": "List price; the first 5,000 Pro requests each month are free and are not netted here.",
    },
}
CREDIT_PROVIDERS = {"apollo"}

DEFAULT_RUN_CAP_USD = Decimal("1.00")


class UnknownPrice(ValueError):
    """A model or provider this module has no rate for. Refusing is better
    than guessing: an unpriced call would make every total quietly wrong."""


def _model_rates(model):
    for key, rates in MODEL_RATES.items():
        if model == key or model.startswith(key + "-"):
            return key, rates
    raise UnknownPrice("No price is configured for model %r" % model)


def estimate_tokens(text):
    """A token allowance for text not yet sent. Bytes / 3, rounded up: newer
    Claude tokenizers average a little over 3 characters per token in
    English, and a CJK character is 3 bytes and about one token, so this
    rarely undercounts. Only reservations use it; costs use measured usage."""
    return math.ceil(len((text or "").encode("utf-8")) / 3)


def worst_case_model_usd(model, prompt_tokens, max_output_tokens, searches=0, batch=False):
    """The reservation for one model call: all input at the full (uncached)
    rate, the whole output budget, every allowed search."""
    if prompt_tokens < 0 or max_output_tokens < 0 or searches < 0:
        raise ValueError("token and search counts cannot be negative")
    key, rates = _model_rates(model)
    if key == "claude-haiku-5-5" and prompt_tokens > HAIKU_LONG_PROMPT_TOKENS:
        rates = HAIKU_LONG_RATES
    inp, _, _, _, out = rates
    mult = BATCH_MULTIPLIER if batch else Decimal(1)
    tokens = (Decimal(prompt_tokens) * inp + Decimal(max_output_tokens) * out) * mult / M
    return tokens + Decimal(searches) * USD_PER_WEB_SEARCH


def _int(usage, key):
    value = usage.get(key, 0)
    if value is None:
        return 0
    if type(value) is not int or value < 0:
        raise ValueError("usage.%s is not a nonnegative integer: %r" % (key, value))
    return value


def model_cost(model, usage, batch=False):
    """Measured cost of one model call from the `usage` block the API
    returned (as a dict). Returns (usd, notes); notes says when a figure had
    to be assumed. Raises UnknownPrice or ValueError rather than guess."""
    key, rates = _model_rates(model)
    if not isinstance(usage, dict):
        raise ValueError("usage is missing")
    inp_n = _int(usage, "input_tokens")
    out_n = _int(usage, "output_tokens")
    read_n = _int(usage, "cache_read_input_tokens")
    write_n = _int(usage, "cache_creation_input_tokens")
    tool_use = usage.get("server_tool_use") or {}
    searches = _int(tool_use, "web_search_requests") if isinstance(tool_use, dict) else 0

    notes = []
    prompt_tokens = inp_n + read_n + write_n
    if key == "claude-haiku-5-5" and prompt_tokens > HAIKU_LONG_PROMPT_TOKENS:
        rates = HAIKU_LONG_RATES
        notes.append("Haiku long-prompt rates (prompt over 100,000 tokens)")
    inp, w5, w1h, read, out = rates

    # Cache writes cost 1.25x (5 minutes) or 2x (1 hour) the input rate. The
    # API splits them in usage.cache_creation; without that split the 1-hour
    # rate is used, so the figure can be high but never low.
    split = usage.get("cache_creation")
    if write_n and isinstance(split, dict):
        w5_n = _int(split, "ephemeral_5m_input_tokens")
        w1h_n = _int(split, "ephemeral_1h_input_tokens")
        if w5_n + w1h_n != write_n:
            raise ValueError("cache_creation split does not add up to cache_creation_input_tokens")
    else:
        w5_n, w1h_n = 0, write_n
        if write_n:
            notes.append("cache-write duration not reported; priced at the 1-hour rate")

    mult = BATCH_MULTIPLIER if batch else Decimal(1)
    tokens = (Decimal(inp_n) * inp + Decimal(w5_n) * w5 + Decimal(w1h_n) * w1h
              + Decimal(read_n) * read + Decimal(out_n) * out) * mult / M
    if batch:
        notes.append("Batch API rates")
    return tokens + Decimal(searches) * USD_PER_WEB_SEARCH, notes


def provider_cost(provider, units):
    """Cost of `units` of a non-model provider (searches, place lookups).
    None for credit-billed providers, which have no dollar figure."""
    if units < 0:
        raise ValueError("units cannot be negative")
    if provider in CREDIT_PROVIDERS:
        return None
    rate = PROVIDER_RATES.get(provider)
    if rate is None:
        raise UnknownPrice("No price is configured for provider %r" % provider)
    return Decimal(units) * rate["usd_per_unit"]


def usd(value):
    """A Decimal rounded for display and JSON: 6 places, as a float."""
    return None if value is None else float(Decimal(value).quantize(Decimal("0.000001")))
