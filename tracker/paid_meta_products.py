"""Paid Marketing Agents: Product Analysis for a Meta ad account.

Meta reports product-level numbers for catalog sales ads (Advantage+ catalog, dynamic product ads) through the
`product_id` breakdown of the insights API. This reads that breakdown and hands the page the same shape
tracker/paid_products.py gives it for a Google Shopping feed (by turning each Meta row into the Google row the parser
already reads), so the page needs no Meta-specific table.

What Meta does not give, and what that means on the page:

  * a product's brand and type (the product_id it sends is "<id>, <title>", which is split). Product Analysis for Both fills
    brand and type in from Google when the two feeds share ids (a Shopify feed usually does);
  * a product's sales: Meta credits purchases to individual products only in part (the on-site ones it can tie to a product;
    omni_purchase is 0 there), so a note says the product figures understate Meta's delivery. NOTE is sent with the data;
  * units sold, cost of goods and profit: sent as missing, so the page says so instead of showing zero;
  * stock status: not read (the page says the stock check is unavailable);
  * the whole-feed trend: it is the account's Sales campaigns by day (from the Performance Summarizer's stored days),
    because `product_id` with a daily split would be one row per product per day;
  * each top product's own days: not read (see `series` in fetch), so a product's drawer has no day-by-day chart for Meta;

An account that runs no catalog sales ads has no rows here, and the page says so.
"""
from __future__ import annotations

import json
import logging

from . import paid_gads as g
from . import paid_meta as meta
from . import paid_products as pp

log = logging.getLogger(__name__)

# Index of each number in a Meta row's page columns (see paid_meta.numbers) and where it goes in a product's NP columns.
MISSING = [8, 9, 10]            # units sold, cost of goods, gross profit: Meta has none


NOTE = ("Meta credits purchases to individual products only in part (it counts the on-site purchases it can tie to a product), so "
        "a product's sales, ROAS and \"spend, no sales\" here understate what Meta delivered. The account totals in the Performance "
        "Summarizer are complete.")


def split_id(raw):
    """(item id, title) from the product_id Meta sends, which is "<id>, <title>" (just the id when there is no title)."""
    raw = str(raw or "").strip()
    head, sep, tail = raw.partition(", ")
    return (head.strip(), tail.strip()) if sep and head.strip() else (raw, "")


def _purchase(row):
    return meta.purchases(row.get("actions"), row.get("action_values"))


def to_google_row(row, day=None):
    """A product insights row as the Google row paid_products reads."""
    n, v = _purchase(row)
    allc = n + sum(meta._first(row.get("actions"), t) for t in meta.ALL_EVENTS)
    allv = v
    pid, title = split_id(row.get("product_id"))
    s = {"productItemId": pid, "productTitle": title, "productBrand": "",
         "productTypeL1": "", "productTypeL2": "", "productTypeL3": ""}
    if day:
        s["date"] = day
    return {"segments": s, "metrics": {
        "impressions": str(int(meta._f(row.get("impressions")))), "clicks": str(int(meta._f(row.get("inline_link_clicks")))),
        "costMicros": str(int(round(meta._f(row.get("spend")) * 1e6))), "conversions": n, "conversionsValue": v,
        "allConversions": allc, "allConversionsValue": allv, "orders": n}}


def _query(start, end, extra=None):
    q = {"level": "campaign", "breakdowns": "product_id", "time_range": json.dumps({"since": start, "until": end}),
         "fields": "campaign_id,spend,impressions,inline_link_clicks,actions,action_values", "limit": meta.PAGE_LIMIT}
    q.update(extra or {})
    return q


def _daily_from_days(days):
    """The whole-feed trend: the account's Sales campaigns by day, in the product page's columns."""
    sales = {i for i, c in enumerate(days["campaigns"]) if c["type"] == "META_SALES"}
    out = [[0] * pp.NP for _ in days["days"]]
    for ci, di, *n in days["rows"]:
        if ci not in sales:
            continue
        row = out[di]
        impr, clk, cost, conv, val, allc, allv = n[0], n[1], n[2], n[3], n[4], n[5], n[6]
        for i, v in enumerate([impr, clk, cost, conv, val, allc, allv, conv]):
            row[i] += v
    return {"days": days["days"], "rows": [[round(x, 4) if isinstance(x, float) else x for x in r] for r in out]}


def fetch(store, act, start, end, *, currency="USD", with_series=True, notes=None, force=False, stamps=None, http=None):
    """One range of products for a Meta ad account, in paid_products.fetch's shape."""
    ttl = g._ttl(end)
    key = lambda k: meta.KEY + "%s:%s:%s:%s" % (k, act, start, end)

    def make_products():
        rows = meta._pages(act + "/insights", _query(start, end), store, http=http)
        out = pp.parse_products([to_google_row(r) for r in rows], start, end, [])
        out["missing"] = list(MISSING)
        out["note"] = NOTE
        return out

    def days():
        return meta.campaign_days(store, act, start, end, currency=currency, force=force, notes=notes, stamps=stamps, http=http)

    res = g._par([lambda: g._cached(store, key("prod"), ttl, make_products, notes, force=force, stamps=stamps), days])
    out = dict(res[0])
    out["daily"] = _daily_from_days(res[1])
    out["outOfStock"] = None
    out["series"] = None          # Meta cannot filter a product breakdown by id, and a daily split of every product is one row per product per day
    return out


def analysis(store, act, cur, cmp, *, currency="USD", force=False, stamps=None, http=None):
    """(current, compare, saved) like paid_products.analysis."""
    notes = []
    jobs = [lambda: fetch(store, act, cur[0], cur[1], currency=currency, with_series=True, notes=notes, force=force, stamps=stamps, http=http)]
    if cmp:
        jobs.append(lambda: fetch(store, act, cmp[0], cmp[1], currency=currency, with_series=False, notes=notes, force=force, stamps=stamps, http=http))
    res = g._par(jobs)
    return res[0], (res[1] if cmp else None), notes
