"""Paid Marketing Agents: Ad Copy & Creative Generator, what is read about the client.

Two cards, both optional:

  website   the client's pages (tracker/paid_strategy_research.read_website): what it sells, the words it uses, the offers and proof it states. A claim in the copy is
            allowed only if it is on these pages or in the facts the person gave.
  account   a Google Ads account: the ads that already earn clicks (so new copy does not repeat them and keeps the words that work), the keywords the account bids on and
            the search terms that converted.

A card that cannot be read says so and the build goes on without it: the model is told what was not available and writes without it. No card writes anything anywhere.
"""
from __future__ import annotations

import time

from . import paid_strategy_research as rs

MAX_ADS = 6
MAX_KEYWORDS = 40
MAX_TERMS = 25


def _text_of(x):
    return x.get("text") if isinstance(x, dict) else x


def read_account(account, *, ads_report, sqr_read, store=None, today=None):
    """The account card. `ads_report(account_id)` is the Ad Strength report (tracker/paid_adstrength.report_for), `sqr_read(store, account, start, end)` is paid_sqr_data.read.
    Each is read on its own: one failing leaves the other, and the card says which is missing."""
    started = time.monotonic()
    cid = account["id"]
    ads, keywords, converting, warnings = [], [], [], []
    try:
        report = ads_report(cid)
        rows = sorted(((report.get("search") or {}).get("items") or []), key=lambda a: (-(a.get("clicks") or 0), -(a.get("impressions") or 0)))
        for a in rows[:MAX_ADS]:
            ads.append({"campaign": a.get("campaign") or "", "adGroup": a.get("adGroup") or "", "clicks": a.get("clicks") or 0, "strength": a.get("strengthLabel") or a.get("strength") or "",
                        "headlines": [t for t in (_text_of(h) for h in a.get("headlines") or []) if t], "descriptions": [t for t in (_text_of(d) for d in a.get("descriptions") or []) if t]})
        warnings += list(report.get("warnings") or [])
    except Exception as e:                                  # noqa: BLE001 (the other half of the card is still worth having)
        warnings.append("The account's ads could not be read (%s)." % rs._clip(e, 140))
    ads_failed = bool(warnings) and not ads and any(w.startswith("The account's ads") for w in warnings)
    keywords_failed = False
    try:
        start, end = rs.window(today)
        sq = sqr_read(store, {"id": cid, "name": account.get("name")}, start, end)
        seen = set()
        for k in sq.get("keywords") or []:
            t = (k.get("t") or "").strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                keywords.append(t)
        keywords = keywords[:MAX_KEYWORDS]
        terms = sq.get("terms") or []
        converting = [t["t"] for t in sorted((t for t in terms if t.get("v", 0) > 0), key=lambda t: (-t["v"], t.get("x", 0)))[:MAX_TERMS]]
    except Exception as e:                                  # noqa: BLE001
        keywords_failed = True
        warnings.append("The account's keywords and search terms could not be read (%s)." % rs._clip(e, 140))
    data = {"account": {"id": cid, "name": account.get("name") or ""}, "ads": ads, "keywords": keywords, "convertingTerms": converting, "warnings": warnings,
            "adsFailed": ads_failed, "keywordsFailed": keywords_failed}
    took = time.monotonic() - started
    if ads_failed and keywords_failed:
        return rs.failed("account", " ".join(warnings), took)
    note = "%d ad%s, %d keywords and %d converting search terms read." % (len(ads), "" if len(ads) == 1 else "s", len(keywords), len(converting))
    return rs.piece("account", "partial" if warnings else "ok", " ".join([note] + warnings), data, took)


def account_evidence(research):
    """The account card as the model is shown it, or None."""
    d = (((research or {}).get("account") or {}).get("data")) or {}
    if not d:
        return None
    return {"bestAds": [{"headlines": a["headlines"][:15], "descriptions": a["descriptions"][:4]} for a in d.get("ads") or []],
            "keywordsTheAccountBidsOn": d.get("keywords") or [], "searchTermsThatConverted": d.get("convertingTerms") or []}


def account_keywords(research):
    """The keywords the account bids on, for the keyword coverage check."""
    return list((((research or {}).get("account") or {}).get("data") or {}).get("keywords") or [])
