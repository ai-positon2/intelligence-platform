"""Product Analysis: per-product Google Ads for the Paid Marketing Agents.

What is pinned here is what could silently go wrong with money numbers: a
product is one row however many rows Google sends for it, a trimmed feed keeps
every total exact, a quiet day still plots as zero, a metric Google refuses is
dropped instead of failing the page, a missing Merchant Center link only costs
the stock check, a repeat view costs no request, the spent daily allowance
serves the saved copy, and every route answers a bad account, range or missing
connection with a reason, never a crash.
"""
import json
import os
import re
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as appmod  # noqa: E402
from tracker import paid_gads as g  # noqa: E402
from tracker import paid_products as pp  # noqa: E402
from tracker import paid_store as store  # noqa: E402
from tests.gads_fakes import FakeStore  # noqa: E402

STAFF = "someone@position2.com"


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._b = status, body if body is not None else {}

    def json(self):
        return self._b


@pytest.fixture(autouse=True)
def ads_env(monkeypatch):
    for k, v in (("GOOGLE_ADS_CLIENT_ID", "cid"), ("GOOGLE_ADS_CLIENT_SECRET", "sec"),
                 ("GOOGLE_ADS_DEVELOPER_TOKEN", "dev"), ("GOOGLE_ADS_REFRESH_TOKEN", "rt")):
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", raising=False)
    g.reset_token_cache()
    g.reset_state()
    pp.reset_state()
    monkeypatch.setitem(g._TOKEN, "value", "tok")
    monkeypatch.setitem(g._TOKEN, "exp", 9e18)


def row(pid, title, cost, clicks=10, impr=100, conv=0, val=0, brand="", t1="", t2="", t3="", **extra):
    m = {"impressions": str(impr), "clicks": str(clicks), "costMicros": str(int(cost * 1_000_000)),
         "conversions": conv, "conversionsValue": val}
    m.update(extra)
    return {"segments": {"productItemId": pid, "productTitle": title, "productBrand": brand,
                         "productTypeL1": t1, "productTypeL2": t2, "productTypeL3": t3}, "metrics": m}


# ── Parsing what Google sends ──────────────────────────────────────────────

def test_a_product_is_one_row_however_many_rows_google_sends():
    rows = [row("SKU-1", "Blue Shoe", 10, 5, 100, 1, 50.0, brand="Acme", t1="Shoes", t2="Running"),
            row("sku-1", "Blue Shoe (old title)", 4, 2, 40, 0, 0),         # same item id, different case and title
            row("SKU-2", "Red Hat", 30, 9, 200, 3, 90.0)]
    out = pp.parse_products(rows, "2026-10-01", "2026-10-07")
    assert [p[0] for p in out["products"]] == ["sku-2", "sku-1"]           # biggest spend first, ids lowercased
    sku1 = out["products"][1]
    assert sku1[1] == "Blue Shoe" and sku1[6:11] == [140, 7, 14.0, 1.0, 50.0]      # the two rows added up, the bigger spender's title kept
    d = out["dims"]
    assert d[0] == "" and d[sku1[2]] == "Acme" and d[sku1[3]] == "Shoes" and d[sku1[4]] == "Running" and sku1[5] == 0
    assert out["total"][:5] == [340, 16, 44.0, 4.0, 140.0] and out["other"]["count"] == 0


def test_a_row_with_no_item_id_is_skipped_and_nothing_gives_an_empty_feed():
    out = pp.parse_products([row("", "No id", 5), row("  ", "Blank", 5)], "2026-10-01", "2026-10-07")
    assert out["products"] == [] and out["total"] == [0] * pp.NP
    assert pp.parse_products([], "2026-10-01", "2026-10-07")["products"] == []


def test_a_big_feed_is_trimmed_for_the_browser_but_every_total_stays_exact():
    rows = [row("p%d" % i, "Product %d" % i, 100 - i, 10, 100, 1, 10.0) for i in range(10)]
    full = pp.parse_products(rows, "2026-10-01", "2026-10-07", cap=100)
    out = pp.parse_products(rows, "2026-10-01", "2026-10-07", cap=3)
    assert [p[0] for p in out["products"]] == ["p0", "p1", "p2"]            # the biggest spenders stay one by one
    assert out["other"]["count"] == 7
    assert out["total"] == full["total"]
    kept = [sum(p[6 + i] for p in out["products"]) + out["other"]["n"][i] for i in range(pp.NP)]
    assert [round(x, 4) for x in kept] == [round(x, 4) for x in out["total"]]      # kept + folded is the whole feed


def test_which_optional_metrics_were_not_fetched_is_reported():
    out = pp.parse_products([row("a", "A", 1)], "2026-10-01", "2026-10-02", fields=["all_conversions", "orders"])
    assert out["missing"] == [pp.COLS.index(c) for c in ("allValue", "units", "cogs", "profit")]
    assert pp.parse_products([row("a", "A", 1)], "2026-10-01", "2026-10-02")["missing"] == []


def test_the_optional_numbers_are_read_and_scaled():
    r = row("a", "A", 2, 4, 100, 2, 80.0, allConversions=3.5, allConversionsValue=99.0, orders=2, unitsSold=5,
            costOfGoodsSoldMicros="30000000", grossProfitMicros="50000000")
    n = pp.parse_products([r], "2026-10-01", "2026-10-02")["products"][0][6:]
    assert n == [100, 4, 2.0, 2.0, 80.0, 3.5, 99.0, 2.0, 5.0, 30.0, 50.0]
    assert len(n) == pp.NP == len(pp.COLS)


def test_the_days_keep_a_quiet_day_and_ignore_a_stray_one():
    def d(day, cost, val):
        return {"segments": {"date": day}, "metrics": {"impressions": "10", "clicks": "1", "costMicros": str(cost * 1_000_000),
                                                      "conversions": 1, "conversionsValue": val}}
    out = pp.parse_daily([d("2026-10-01", 5, 20.0), d("2026-10-01", 3, 10.0), d("2026-10-03", 2, 0.0), d("2026-12-25", 9, 9.0)],
                         "2026-10-01", "2026-10-04")
    assert out["days"] == ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"]
    assert [r[2] for r in out["rows"]] == [8.0, 0, 2.0, 0] and out["rows"][0][4] == 30.0


def test_out_of_stock_ids_are_lowercased_and_unique():
    rows = [{"shoppingProduct": {"itemId": "SKU-9"}}, {"shoppingProduct": {"itemId": "sku-9"}},
            {"shoppingProduct": {"itemId": " "}}, {"shoppingProduct": {}}, {"shoppingProduct": {"itemId": "a-1"}}]
    assert pp.parse_out_of_stock(rows) == ["a-1", "sku-9"]


# ── The queries ────────────────────────────────────────────────────────────

def test_the_queries_ask_for_what_the_parsers_read():
    q = pp.q_products("2026-10-01", "2026-10-07", ["orders"])
    assert "FROM shopping_performance_view" in q and "BETWEEN '2026-10-01' AND '2026-10-07'" in q
    assert pp.DIMS == ["segments.product_item_id", "segments.product_title", "segments.product_brand", "segments.product_type_l1",
                       "segments.product_type_l2", "segments.product_type_l3"]           # what parse_products reads, spelled out
    for dim in pp.DIMS + ["metrics.impressions", "metrics.clicks", "metrics.cost_micros", "metrics.conversions",
                          "metrics.conversions_value", "metrics.orders"]:
        assert dim in q, dim
    assert "metrics.units_sold" not in q                                    # only the optional metrics asked for
    assert "segments.date" in pp.q_daily("2026-10-01", "2026-10-07") and "segments.product_item_id" not in pp.q_daily("a", "b")
    assert "OUT_OF_STOCK" in pp.q_out_of_stock()


# ── The calls ──────────────────────────────────────────────────────────────

def make_http(log, refuse=None, stock_error=None, quota=None):
    """A Google that answers each of the three reads, recording the queries."""
    def http(url, headers=None, data=None, **k):
        q = json.loads(data)["query"]
        log.append(q)
        if quota:
            return Resp(429, quota)
        if "shopping_product" in q:
            if stock_error:
                return Resp(stock_error, {"error": {"message": "no Merchant Center link"}})
            return Resp(200, {"results": [{"shoppingProduct": {"itemId": "SKU-2"}}]})
        if refuse and any(("metrics." + f) in q for f in refuse):
            return Resp(400, {"error": {"details": [{"errors": [{"message": "Unrecognized field %s" % refuse[0]}]}]}})
        if "product_item_id IN (" in q:                # one product's days: the top products, day by day
            return Resp(200, {"results": [{"segments": {"productItemId": "SKU-1", "date": "2026-10-02"},
                                           "metrics": {"impressions": "40", "clicks": "4", "costMicros": "5000000", "conversions": 1, "conversionsValue": 30.0}}]})
        if "segments.product_item_id" not in q:        # the day-by-day read (the products read also filters on dates)
            return Resp(200, {"results": [{"segments": {"date": "2026-10-01"}, "metrics": {"impressions": "5", "clicks": "1", "costMicros": "2000000"}}]})
        return Resp(200, {"results": [row("SKU-1", "One", 10, 5, 100, 1, 50.0), row("SKU-2", "Two", 3)]})
    return http


def test_a_range_reads_products_days_and_stock_and_a_repeat_view_costs_nothing():
    s, log = FakeStore(), []
    http = make_http(log)
    out = pp.fetch(s, "1234567890", "2026-10-01", "2026-10-07", http=http)
    assert len(log) == 4 and [p[0] for p in out["products"]] == ["sku-1", "sku-2"]
    assert out["daily"]["days"][0] == "2026-10-01" and out["outOfStock"] == ["sku-2"]
    assert out["series"]["ids"] == ["sku-1", "sku-2"] and out["series"]["rows"]["sku-1"][1][2] == 5.0
    pp.fetch(s, "1234567890", "2026-10-01", "2026-10-07", http=lambda *a, **k: pytest.fail("a repeat view went to Google"))
    assert len(log) == 4


def test_only_the_twenty_top_spenders_get_their_days():
    log = []

    def http(url, headers=None, data=None, **k):
        q = json.loads(data)["query"]
        log.append(q)
        if "product_item_id IN (" in q:
            return Resp(200, {"results": []})
        if "shopping_product" in q:
            return Resp(200, {"results": []})
        if "product_item_id" not in q:
            return Resp(200, {"results": []})
        return Resp(200, {"results": [row("SKU-%02d" % i, "P%d" % i, 100 - i) for i in range(25)]})
    out = pp.fetch(FakeStore(), "1234567890", "2026-10-01", "2026-10-07", http=http)
    ask = next(q for q in log if "IN (" in q)
    assert len(out["products"]) == 25 and len(out["series"]["ids"]) == pp.SERIES_TOP == 20 and ask.count("'sku-") == 20
    assert "'sku-19'" in ask and "'sku-20'" not in ask                      # the 21st spender is not asked about


def test_a_metric_google_refuses_is_dropped_remembered_and_never_fails_the_page():
    s, log = FakeStore(), []
    out = pp.fetch(s, "1234567890", "2026-10-01", "2026-10-07", with_stock=False, with_series=False, http=make_http(log, refuse=["orders"]))
    assert out["products"] and "orders" in pp._BAD and pp.COLS.index("orders") in out["missing"]
    assert "units" not in [pp.COLS[i] for i in out["missing"]]               # only the refused one is gone
    log.clear()
    pp.fetch(FakeStore(), "1234567890", "2026-10-08", "2026-10-09", with_stock=False, with_series=False, http=make_http(log, refuse=["orders"]))
    assert not any("metrics.orders" in q for q in log)                      # remembered: not asked for again


def test_without_a_merchant_center_link_the_products_still_load_and_stock_is_unknown():
    s, log = FakeStore(), []
    out = pp.fetch(s, "1234567890", "2026-10-01", "2026-10-07", http=make_http(log, stock_error=403))
    assert out["products"] and out["outOfStock"] is None


def test_the_comparison_needs_no_stock_check():
    s, log = FakeStore(), []
    cur, cmp, saved = pp.analysis(s, "1234567890", ("2026-10-01", "2026-10-07"), ("2026-09-24", "2026-09-30"), http=make_http(log))
    assert "outOfStock" in cur and "outOfStock" not in cmp and saved == []
    assert sum("shopping_product" in q for q in log) == 1 and sum("IN (" in q for q in log) == 1 and len(log) == 6      # the series and the stock are for the main range only
    assert pp.analysis(s, "1234567890", ("2026-10-01", "2026-10-07"), None, http=make_http([]))[1] is None


QUOTA = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "details": [{"errors": [{"message": "Too many requests.",
         "details": {"quotaErrorDetails": {"rateName": "Number of operations for explorer access", "retryDelay": "61458s"}}}]}]}}


def test_when_googles_allowance_is_spent_the_saved_copy_is_served_and_dated():
    s = FakeStore()
    pp.fetch(s, "1234567890", "2026-10-01", "2026-10-07", http=make_http([]))
    saved = dict(s.cache)
    s.cache.clear()
    g.forget()
    s.old.update(saved)                                                     # saved long ago: no longer fresh
    notes = []
    out = pp.fetch(s, "1234567890", "2026-10-01", "2026-10-07", notes=notes, http=make_http([], quota=QUOTA))
    assert [p[0] for p in out["products"]] == ["sku-1", "sku-2"] and notes and out["outOfStock"] == ["sku-2"]
    with pytest.raises(g.OverQuota):                                        # a range never seen has no copy to show
        pp.fetch(FakeStore(), "1234567890", "2026-01-01", "2026-01-02", http=make_http([], quota=QUOTA))


# ── Routes ─────────────────────────────────────────────────────────────────

def client_as(email):
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


ACCTS = [{"id": "1234567890", "name": "Acme", "currency": "USD", "tz": "", "level": 1}]
URL = "/p2/paid-marketing/api/gads/products"


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(g, "list_accounts", lambda st, **k: ACCTS)
    calls = []

    def analysis(st, cid, cur, cmp, **k):
        calls.append((cid, cur, cmp))
        return {"start": cur[0], "products": []}, ({"start": cmp[0]} if cmp else None), []
    monkeypatch.setattr(pp, "analysis", analysis)
    return calls


def test_the_products_route_is_for_signed_in_position2_staff_only(wired):
    assert client_as(None).get(URL).status_code == 302
    assert client_as("someone@gmail.com").get(URL).status_code == 302


def test_the_products_route_checks_the_account_and_both_ranges(wired):
    c = client_as(STAFF)
    q = "account=1234567890&start=2026-10-01&end=2026-10-07"
    assert c.get(URL + "?account=12&start=2026-10-01&end=2026-10-07").status_code == 400
    r = c.get(URL + "?account=9999999999&start=2026-10-01&end=2026-10-07")
    assert r.status_code == 400 and "not under Position2" in r.get_json()["error"]
    assert c.get(URL + "?account=1234567890&start=2026-10-09&end=2026-10-07").status_code == 400
    assert c.get(URL + "?" + q + "&compare_start=2026-09-24").status_code == 400
    assert not wired
    j = c.get(URL + "?" + q + "&compare_start=2026-09-24&compare_end=2026-09-30").get_json()
    assert j["account"]["name"] == "Acme" and j["compare"]["start"] == "2026-09-24" and j["stale"] is None
    assert wired == [("1234567890", ("2026-10-01", "2026-10-07"), ("2026-09-24", "2026-09-30"))]
    assert c.get(URL + "?" + q).get_json()["compare"] is None


def test_without_a_connection_the_route_says_what_is_missing(monkeypatch):
    monkeypatch.delenv("GOOGLE_ADS_REFRESH_TOKEN")
    r = client_as(STAFF).get(URL)
    assert r.status_code == 409 and "GOOGLE_ADS_REFRESH_TOKEN" in r.get_json()["error"]


def test_google_failures_become_answers_and_a_spent_allowance_says_when_it_returns(wired, monkeypatch):
    c = client_as(STAFF)
    url = URL + "?account=1234567890&start=2026-10-01&end=2026-10-07"
    assert c.get(url).status_code == 200
    for exc, code in ((g.OverQuota("budget used", until=1.8e9), 429), (g.AdsError("Google said no"), 502)):
        def boom(*a, **k):
            raise exc
        monkeypatch.setattr(pp, "analysis", boom)
        r = c.get(url)
        assert r.status_code == code and r.get_json()["error"] == str(exc)
        if code == 429:
            assert r.get_json()["blockedUntil"] == 1.8e9


def test_the_saved_copy_is_reported_as_stale_by_the_route(monkeypatch):
    monkeypatch.setattr(g, "list_accounts", lambda st, **k: ACCTS)
    monkeypatch.setattr(pp, "analysis", lambda *a, **k: ({"products": []}, None, ["2026-10-10T08:00:00+00:00", "2026-10-11T08:00:00+00:00"]))
    j = client_as(STAFF).get(URL + "?account=1234567890&start=2026-10-01&end=2026-10-07").get_json()
    assert j["stale"] == "2026-10-10T08:00:00+00:00"                          # the oldest copy that stood in


def test_the_page_renders_for_staff_and_not_for_anyone_else():
    path = "/p2/paid-marketing/product-analysis"
    assert client_as(STAFF).get(path).status_code == 200
    assert client_as(None).get(path).status_code == 302
    assert client_as("someone@gmail.com").get(path).status_code == 302


# ── The page script and its template ───────────────────────────────────────

def _read(*p):
    return open(os.path.join(ROOT, *p), encoding="utf-8").read()


def test_the_script_only_reaches_for_elements_the_template_has():
    js, html = _read("static", "js", "product_analysis.js"), _read("templates", "product_analysis.html")
    made_by_script = {"psToast", "pq", "moreRows", "bLog", "rbBy", "rbM", "rTarget", "rClicks", "rSpend", "rApply", "rReset", "drCopy", "cvPareto",
                      "cvGroups", "cvBubble", "cvRollup", "cvTrend", "cvPerf", "cvDr", "pmSeg", "pnSeg"}
    used = set(re.findall(r'\$\("#([A-Za-z]+)"\)', js))
    have = set(re.findall(r'id="([A-Za-z]+)"', html))
    assert not (used - have - made_by_script), sorted(used - have - made_by_script)
    for name in ("pm_picker.js", "ps_data.js", "pa_data.js", "product_analysis.js", "chart.umd.js", "product_analysis.css", "performance_summarizer.css"):
        assert name in html
        assert os.path.exists(os.path.join(ROOT, "static", "vendor" if name == "chart.umd.js" else
                                           "css" if name.endswith(".css") else "js", name)), name
    assert html.index("ps_data.js") < html.index("pa_data.js") < html.index("product_analysis.js")      # each needs the one before


def test_the_script_reads_only_routes_that_exist():
    js, app_src = _read("static", "js", "product_analysis.js"), _read("app.py")
    for route in set(re.findall(r'BASE\+"/([a-z]+)', js)):
        assert ('/p2/paid-marketing/api/gads/%s"' % route) in app_src, route


def test_the_agent_is_listed_wherever_the_others_are():
    section = _read("templates", "paid_marketing.html")
    assert 'class="dash-card active c-prod"' in section and "product-analysis" in section.split("var BASE=[", 1)[1]
    assert "product-analysis" in _read("templates", "_paid_kpal.html")
    assert ".c-prod{" in _read("static", "css", "paid_marketing.css")
    assert "Product Analysis" in _read("app.py")


def test_no_em_dash_in_what_this_agent_shows():
    for p in (("static", "js", "product_analysis.js"), ("static", "js", "pa_data.js"), ("templates", "product_analysis.html"),
              ("static", "css", "product_analysis.css"), ("tracker", "paid_products.py")):
        assert "—" not in _read(*p), p
