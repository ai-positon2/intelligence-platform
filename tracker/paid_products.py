"""Paid Marketing Agents: Product Analysis (Ecommerce).

Per-product Shopping and Performance Max numbers for one Google Ads account,
read through the same read-only sign-in, request tally, saved copies and
quota handling as the Performance Summarizer (tracker/paid_gads.py). Nothing
here can change an ad account: only `googleAds:search` is called.

What is read for a range (each one request, plus more pages if the account has
more than 10,000 products):

  * every product with impressions, grouped by item id, with brand and the
    first three levels of the product type the merchant set, and the five core
    numbers (impressions, clicks, cost, conversions, conversion value);
  * the same range one day at a time for the whole product feed, so the page
    can draw a trend;
  * the same range one day at a time for each of the SERIES_TOP products that
    spent the most, so the page can show how each one performed;
  * the product ids Merchant Center says are out of stock (soft: a missing
    Merchant Center link just means that section says so).

Orders, units sold, cost of goods and gross profit ride along when the account
reports them. Like the Performance Summarizer's optional metrics, one that
Google refuses for any reason is dropped and remembered, never an error.

A big feed is trimmed for the browser: the top MAX_PRODUCTS by spend are sent
one by one and everything else is folded into one "all other products" row, so
every total stays exact.
"""
from __future__ import annotations

import datetime as dt
import logging
import re

from tracker import paid_gads as g

log = logging.getLogger(__name__)

MAX_PRODUCTS = 3000
SERIES_TOP = 20                       # products whose day-by-day numbers are sent
ID_SAFE = re.compile(r"^[a-z0-9][a-z0-9._~+:/-]{0,126}$")

CORE = [("impressions", 1), ("clicks", 1), ("cost_micros", 1e-6), ("conversions", 1), ("conversions_value", 1)]
OPTIONAL = [("all_conversions", 1), ("all_conversions_value", 1), ("orders", 1), ("units_sold", 1),
            ("cost_of_goods_sold_micros", 1e-6), ("gross_profit_micros", 1e-6)]
BASE = CORE + OPTIONAL
NP = len(BASE)                       # numbers per product: impressions, clicks, cost, conv, value, allConv, allValue, orders, units, cogs, profit
COLS = ["impressions", "clicks", "cost", "conversions", "value", "allConv", "allValue", "orders", "units", "cogs", "profit"]
assert NP == len(COLS)
_KEYS = [(g._camel(f), k) for f, k in BASE]
_BAD = set()                         # optional metrics Google named in a refusal (per worker, until restart)

DIMS = ["segments.product_item_id", "segments.product_title", "segments.product_brand",
        "segments.product_type_l1", "segments.product_type_l2", "segments.product_type_l3"]


def reset_state():
    """For tests: forget what was learned about Google's refusals."""
    _BAD.clear()


# ── Queries ─────────────────────────────────────────────────────────────────

def _metrics(fields):
    return ", ".join("metrics." + f for f in [c[0] for c in CORE] + list(fields))


def q_products(start, end, fields=()):
    return ("SELECT %s, %s FROM shopping_performance_view WHERE segments.date BETWEEN '%s' AND '%s'"
            % (", ".join(DIMS), _metrics(fields), start, end))


def q_daily(start, end, fields=()):
    return ("SELECT segments.date, %s FROM shopping_performance_view WHERE segments.date BETWEEN '%s' AND '%s'"
            % (_metrics(fields), start, end))


def q_product_days(start, end, ids, fields=()):
    """One row per product per day for the products in `ids` (item ids made only of safe characters)."""
    ids = [i for i in ids if ID_SAFE.match(i)]
    return ("SELECT segments.product_item_id, segments.date, %s FROM shopping_performance_view "
            "WHERE segments.date BETWEEN '%s' AND '%s' AND segments.product_item_id IN (%s)"
            % (_metrics(fields), start, end, ", ".join("'%s'" % i for i in ids)))


def q_out_of_stock():
    return ("SELECT shopping_product.item_id, shopping_product.availability FROM shopping_product "
            "WHERE shopping_product.availability = 'OUT_OF_STOCK'")


# ── Parsing ─────────────────────────────────────────────────────────────────

def _nums(m):
    """NP numbers from a metrics block (0 where Google sent nothing): counts as whole numbers, the rest to 4 places."""
    m = m or {}
    out = []
    for i, (key, scale) in enumerate(_KEYS):
        v = g._f(m.get(key)) * scale
        out.append(int(v) if i < 2 else round(v, 4))
    return out


def _add(a, b):
    for i in range(NP):
        a[i] += b[i]


def missing_columns(fields):
    """Indexes (into COLS) of the optional metrics that were NOT fetched, so the page says so instead of showing 0."""
    return [len(CORE) + i for i, (f, _) in enumerate(OPTIONAL) if f not in fields]


def _clean(s):
    return " ".join(str(s or "").split())


def parse_products(rows, start, end, fields=None, cap=MAX_PRODUCTS):
    """The compact shape the page works from.

    dims: strings brand and type levels point into (dims[0] is ""), products: [[item id, title, brand, type 1,
    type 2, type 3, <NP numbers>], ...] biggest spend first, other: how many products were folded into one row and
    their numbers (the page shows them as "All other products"), total: NP numbers for the whole feed, missing:
    the columns Google would not give."""
    prods = {}
    for r in rows:
        s = r.get("segments") or {}
        pid = _clean(s.get("productItemId")).lower()
        if not pid:
            continue
        nums = _nums(r.get("metrics"))
        p = prods.get(pid)
        if p is None:
            prods[pid] = {"id": pid, "title": _clean(s.get("productTitle")) or pid, "brand": _clean(s.get("productBrand")),
                          "t": [_clean(s.get("productTypeL%d" % i)) for i in (1, 2, 3)], "n": nums, "top": nums[2]}
        else:
            _add(p["n"], nums)
            if nums[2] > p["top"]:        # the same item under two titles: keep the one that spent more
                p["title"], p["top"] = _clean(s.get("productTitle")) or p["title"], nums[2]
    ordered = sorted(prods.values(), key=lambda p: (-p["n"][2], -p["n"][4], -p["n"][1], p["id"]))
    total = [0] * NP
    for p in ordered:
        _add(total, p["n"])
    keep, rest = ordered[:cap], ordered[cap:]
    dims, at = [""], {"": 0}

    def di(s):
        if s not in at:
            at[s] = len(dims)
            dims.append(s)
        return at[s]
    out = [[p["id"], p["title"], di(p["brand"]), di(p["t"][0]), di(p["t"][1]), di(p["t"][2])]
           + [round(x, 4) if isinstance(x, float) else x for x in p["n"]] for p in keep]
    other = [0] * NP
    for p in rest:
        _add(other, p["n"])
    return {"start": start, "end": end, "dims": dims, "products": out,
            "other": {"count": len(rest), "n": [round(x, 4) if isinstance(x, float) else x for x in other]},
            "total": [round(x, 4) if isinstance(x, float) else x for x in total],
            "missing": missing_columns([f for f, _ in OPTIONAL] if fields is None else fields)}


def parse_daily(rows, start, end):
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    days = [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    at = {d: i for i, d in enumerate(days)}
    out = [[0] * NP for _ in days]
    for r in rows:
        d = (r.get("segments") or {}).get("date")
        if d in at:
            _add(out[at[d]], _nums(r.get("metrics")))
    return {"days": days, "rows": [[round(x, 4) if isinstance(x, float) else x for x in n] for n in out]}


def parse_series(rows, start, end, ids):
    """{"days": [...], "ids": [...], "rows": {item id: [NP numbers for each day]}} for the products in `ids`, a quiet day
    as zeros. Ids are matched in lower case."""
    d0, d1 = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    days = [(d0 + dt.timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]
    at = {d: i for i, d in enumerate(days)}
    want = [i.lower() for i in ids]
    out = {i: [[0] * NP for _ in days] for i in want}
    for r in rows:
        s = r.get("segments") or {}
        pid, d = _clean(s.get("productItemId")).lower(), s.get("date")
        if pid in out and d in at:
            _add(out[pid][at[d]], _nums(r.get("metrics")))
    return {"days": days, "ids": want, "rows": {i: [[round(x, 4) if isinstance(x, float) else x for x in n] for n in v] for i, v in out.items()}}


def parse_out_of_stock(rows):
    return sorted({_clean((r.get("shoppingProduct") or {}).get("itemId")).lower() for r in rows} - {""})


# ── Cached fetches ──────────────────────────────────────────────────────────

def _read(store, customer_id, build, http):
    return g.search_pruned(customer_id, build, [o[0] for o in OPTIONAL], _BAD, store, http=http)


def fetch(store, customer_id, start, end, *, with_stock=True, with_series=True, notes=None, force=False, stamps=None, http=None):
    """One range: the products, the days, (for the main range) the out-of-stock ids and each top product's days."""
    def make_products():
        rows, used = _read(store, customer_id, lambda f: q_products(start, end, f), http)
        return parse_products(rows, start, end, used)

    def make_daily():
        rows, _ = _read(store, customer_id, lambda f: q_daily(start, end, f), http)
        return parse_daily(rows, start, end)

    def make_stock():
        return parse_out_of_stock(g.search(customer_id, q_out_of_stock(), store, http=http))

    ttl = g._ttl(end)
    key = lambda k: g.CACHE_V + "%s:%s:%s:%s" % (k, customer_id, start, end)

    def soft(fn, empty):
        """Out-of-stock ids are an extra: without a Merchant Center link (or when Google cannot give them and no
        saved copy exists) the range still draws, and the page says the stock check is unavailable."""
        def run():
            try:
                return fn()
            except g.AdsError as e:
                log.warning("products: leaving out an optional read for %s (%s)", customer_id, e)
                return empty
        return run

    jobs = [lambda: g._cached(store, key("prod"), ttl, make_products, notes, force=force, stamps=stamps),
            lambda: g._cached(store, key("pday"), ttl, make_daily, notes, force=force, stamps=stamps)]
    if with_stock:
        jobs.append(soft(lambda: g._cached(store, key("oos"), ttl, make_stock, notes, force=force, stamps=stamps), None))
    res = g._par(jobs)
    out = dict(res[0])
    out["daily"] = res[1]
    if with_stock:
        out["outOfStock"] = res[2]          # None: could not be read
    if with_series:                         # needs the products first: it asks for the ones that spent the most
        top = [p[0] for p in out["products"][:SERIES_TOP] if ID_SAFE.match(p[0])]

        def make_series():
            rows, _ = _read(store, customer_id, lambda f: q_product_days(start, end, top, f), http)
            return parse_series(rows, start, end, top)
        out["series"] = soft(lambda: g._cached(store, key("pser"), ttl, make_series, notes, force=force, stamps=stamps), None)() if top else None
    return out


def analysis(store, customer_id, cur, cmp, *, force=False, stamps=None, http=None):
    """The main range and (if given) its comparison, fetched together. Returns (current, compare, saved) where
    `saved` lists the times of any saved copies served because Google could not answer; `stamps`, if given, is filled
    with the times the numbers were read from Google (see paid_gads.oldest)."""
    notes = []
    jobs = [lambda: fetch(store, customer_id, cur[0], cur[1], with_stock=True, with_series=True, notes=notes, force=force, stamps=stamps, http=http)]
    if cmp:
        jobs.append(lambda: fetch(store, customer_id, cmp[0], cmp[1], with_stock=False, with_series=False, notes=notes, force=force, stamps=stamps, http=http))
    res = g._par(jobs)
    return res[0], (res[1] if cmp else None), notes
