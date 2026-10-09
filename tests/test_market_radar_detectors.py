"""Market Radar, Phase 3 detectors: reading and comparing, no network.

Every read takes its network through ctx ("get", "get_json", "fetch"), so
these tests serve each detector the shapes the live sources answer with
(Shopify products.json, WooCommerce's store API, sitemaps, RSS, Atom, the
job-board APIs, Google News RSS) and check both halves: what is read, and
what a week-to-week difference becomes.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import market_radar_detectors as det  # noqa: E402
from tracker import market_radar_jobs as jobs  # noqa: E402
from tracker import market_radar_news as news  # noqa: E402

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def ok(body, **kw):
    return dict({"status": "ok", "http": 200, "body": body, "note": "", "final_url": "",
                 "truncated": False}, **kw)


def miss(status="not_found", note="not found (HTTP 404)"):
    return {"status": status, "http": 404, "body": "", "note": note, "final_url": "",
            "truncated": False}


class Net:
    """A fake network: url -> answer (a dict for get, a JSON-able for get_json).
    Records every URL asked."""

    def __init__(self, pages=None, posts=None):
        self.pages = pages or {}
        self.posts = posts or {}
        self.asked = []

    def get(self, url, **kw):
        self.asked.append(url)
        if kw.get("post") is not None:
            ans = self.posts.get((url, kw["post"].get("offset")))
            return ok(json.dumps(ans)) if ans is not None else miss()
        ans = self.pages.get(url)
        if ans is None:
            return miss()
        return ans if isinstance(ans, dict) and "status" in ans else ok(
            ans if isinstance(ans, str) else json.dumps(ans))

    def get_json(self, url, **kw):
        read = self.get(url, **kw)
        if read["status"] != "ok":
            return None, read
        try:
            return json.loads(read["body"]), read
        except ValueError:
            return None, dict(read, status="error", note="the answer was not JSON")


def site(**over):
    s = {"home_url": "https://acme.com/", "status": "ok", "pages": [], "texts": {},
         "robots": {"sitemaps": ["https://acme.com/sitemap.xml"]}, "needs_browser": False,
         "signals": {"platforms": [], "organizations": [], "location_links": {"sample": []},
                     "locator_vendors": [], "sitemap": {}, "feeds": [], "ratings": [],
                     "ats": []}}
    sig = over.pop("signals", {})
    s.update(over)
    s["signals"].update(sig)
    return s


def ctx(net, rs=None, *, prev=None, results=None, fetch=None, entity=None):
    return {"entity": entity or {"id": 1, "domain": "acme.com", "name": "Acme", "country": "US"},
            "site": rs or site(), "get": net.get, "get_json": net.get_json,
            "fetch": fetch or (lambda u: {"status": "error", "html": "", "final_url": u,
                                          "note": "x"}),
            "now": NOW, "prev": prev or {}, "results": results or {},
            "news_breaker": news.Breaker(gap=0, sleep=lambda s: None)}


def urlset(*locs):
    return "<urlset>" + "".join("<url><loc>%s</loc><lastmod>2026-09-01</lastmod></url>" % l
                                for l in locs) + "</urlset>"


# == sitemaps and locations ========================================================

def test_a_sitemap_index_follows_the_location_children_first_and_only_them():
    net = Net({"https://acme.com/sitemap.xml":
               "<sitemapindex><sitemap><loc>https://acme.com/sm-blog.xml</loc></sitemap>"
               "<sitemap><loc>https://acme.com/sm-locations.xml</loc></sitemap></sitemapindex>",
               "https://acme.com/sm-locations.xml": urlset("https://acme.com/locations/austin/",
                                                           "https://acme.com/about/")})
    got = det.read_sitemaps(net.get, ["https://acme.com/sitemap.xml"],
                            prefer=det.LOCATION_SITEMAP,
                            keep=lambda p: bool(det.site.LOCATION_PATH.search(p)))
    assert got["entries"] == {"https://acme.com/locations/austin/": "2026-09-01"}
    assert got["complete"] and "https://acme.com/sm-blog.xml" not in net.asked


def test_a_sitemap_cut_short_is_not_complete():
    net = Net({"https://acme.com/sitemap.xml": urlset(*["https://acme.com/locations/%d" % i
                                                        for i in range(5)])})
    got = det.read_sitemaps(net.get, ["https://acme.com/sitemap.xml"], prefer=det.LOCATION_SITEMAP,
                            keep=lambda p: True, max_urls=3)
    assert len(got["entries"]) == 3 and not got["complete"]
    got = det.read_sitemaps(Net().get, ["https://acme.com/sitemap.xml"],
                            prefer=det.LOCATION_SITEMAP, keep=lambda p: True)
    assert not got["complete"] and got["failed"]


def test_a_refused_file_that_holds_no_locations_does_not_make_the_read_partial():
    # aspendental.com: its appointment index has no location-like child, so
    # all its children are read, and one of them refuses us.
    net = Net({"https://acme.com/locations/index.xml":
               "<sitemapindex><sitemap><loc>https://acme.com/locations/sm.xml</loc></sitemap>"
               "</sitemapindex>",
               "https://acme.com/locations/sm.xml": urlset("https://acme.com/locations/a"),
               "https://acme.com/booking/index.xml":
               "<sitemapindex><sitemap><loc>https://acme.com/booking/sm.xml</loc></sitemap>"
               "</sitemapindex>",
               "https://acme.com/booking/sm.xml": miss("blocked", "refused (HTTP 403)")})
    tops = ["https://acme.com/locations/index.xml", "https://acme.com/booking/index.xml"]
    got = det.read_sitemaps(net.get, tops, prefer=re.compile("locations"), keep=lambda p: True)
    assert got["complete"] and list(got["entries"]) == ["https://acme.com/locations/a"]
    # The same refusal on a file that could hold locations makes it partial.
    got = det.read_sitemaps(net.get, tops, prefer=re.compile("locations|booking"),
                            keep=lambda p: True)
    assert not got["complete"]
    # And so does one the site names itself in robots.txt.
    net.pages["https://acme.com/booking/index.xml"] = miss("blocked", "refused (HTTP 403)")
    got = det.read_sitemaps(net.get, tops, prefer=re.compile("locations"), keep=lambda p: True)
    assert not got["complete"]


def test_an_index_whose_only_readable_children_are_unrelated_is_partial():
    net = Net({"https://acme.com/sitemap.xml":
               "<sitemapindex><sitemap><loc>https://acme.com/sm-1.xml</loc></sitemap>"
               "<sitemap><loc>https://acme.com/sm-2.xml</loc></sitemap></sitemapindex>",
               "https://acme.com/sm-1.xml": miss("blocked", "refused (HTTP 403)"),
               "https://acme.com/sm-2.xml": urlset("https://acme.com/about")})
    got = det.read_sitemaps(net.get, ["https://acme.com/sitemap.xml"], prefer=det.LOCATION_SITEMAP,
                            keep=lambda p: True)
    assert not got["complete"]          # sm-1 may have held the locations


def test_locations_skip_product_and_blog_sitemaps():
    net = Net({"https://acme.com/sitemap.xml":
               "<sitemapindex><sitemap><loc>https://acme.com/sitemap_products_1.xml</loc></sitemap>"
               "<sitemap><loc>https://acme.com/sitemap_pages_1.xml</loc></sitemap></sitemapindex>",
               "https://acme.com/sitemap_pages_1.xml": urlset("https://acme.com/stores/london")})
    r = det.read_locations(ctx(net, site()))
    assert r["payload"]["places"] == ["acme.com/stores/london"]
    assert "https://acme.com/sitemap_products_1.xml" not in net.asked


def test_a_list_that_shrank_a_lot_is_one_warning_not_dozens_of_closures():
    prev = {"places": ["a.com/l/%d" % i for i in range(50)], "complete": True}
    cur = {"places": ["a.com/l/%d" % i for i in range(30)], "complete": True}
    (ev,) = det.compare_locations(prev, cur, NOW)
    assert ev["type"] == "location_list_shrank" and ev["title"].startswith("20 location pages")


def test_hub_pages_are_not_places_and_places_get_readable_labels():
    keys = ["acme.com/dentist/ca", "acme.com/dentist/ca/merced", "acme.com/dentist/ca/fresno",
            "acme.com/locations/london-soho"]
    assert det.leaves(keys) == ["acme.com/dentist/ca/fresno", "acme.com/dentist/ca/merced",
                                "acme.com/locations/london-soho"]
    assert det.place_label("acme.com/dentist/ca/merced") == "Merced, CA"
    assert det.place_label("acme.com/locations/london-soho") == "London Soho"


def test_tabs_repeated_under_every_office_fold_into_the_office():
    offices = ["a.com/dentist/tx/austin/1-main-st", "a.com/dentist/tx/dallas/9-oak-ave",
               "a.com/dentist/ca/merced/5-elm-rd", "a.com/dentist/ca/fresno/2-pine-ln"]
    keys = ["a.com/dentist/tx", "a.com/dentist/ca"] + offices + [
        o + "/" + tab for o in offices for tab in ("dentures", "implants")]
    assert det.leaves(keys) == sorted(offices)


def test_a_city_name_shared_by_a_few_states_is_still_a_place():
    keys = ["a.com/l/%s/springfield" % st for st in ("il", "mo", "ma")] + \
        ["a.com/l/%s/%s-town%d" % (st, st, i) for st in ("il", "mo", "ma", "tx", "ca", "ny", "fl",
                                                        "oh", "pa", "ga", "nc") for i in range(3)]
    assert len(det.leaves(keys)) == 3 + 33


def test_locations_read_the_main_and_the_locator_host_sitemaps():
    rs = site(signals={"location_links": {"sample": ["https://locations.acme.com/tx/austin"]},
                       "locator_vendors": ["yext"]})
    net = Net({"https://acme.com/sitemap.xml": urlset("https://acme.com/locations/dallas"),
               "https://locations.acme.com/robots.txt": "Sitemap: https://locations.acme.com/s.xml",
               "https://locations.acme.com/s.xml": urlset("https://locations.acme.com/locations/tx/austin")})
    r = det.read_locations(ctx(net, rs))
    assert r["status"] == "ok" and r["complete"]
    assert r["payload"]["places"] == ["acme.com/locations/dallas",
                                      "locations.acme.com/locations/tx/austin"]
    assert "yext" in r["note"]


def test_no_sitemap_answering_is_a_failure_not_an_absence():
    net = Net({"https://acme.com/sitemap.xml": miss("error", "timed out after 20s")})
    r = det.read_locations(ctx(net, site()))
    assert r["status"] == "failed" and r["payload"] is None
    net = Net({"https://acme.com/sitemap.xml": urlset("https://acme.com/about")})
    assert det.read_locations(ctx(net, site()))["status"] == "none"


def test_new_and_removed_location_pages_become_events():
    prev = {"places": ["a.com/l/austin", "a.com/l/dallas"], "addresses": [], "complete": True}
    cur = {"places": ["a.com/l/austin", "a.com/l/merced"], "addresses": [], "complete": True}
    evs = det.compare_locations(prev, cur, NOW)
    assert [(e["type"], e["title"]) for e in evs] == [
        ("new_location", "New location page: Merced"),
        ("closed_location", "Location page removed: Dallas")]


def test_removals_from_a_partial_read_are_not_closures():
    prev = {"places": ["a.com/l/austin", "a.com/l/dallas"], "addresses": [], "complete": True}
    cur = {"places": ["a.com/l/austin"], "addresses": [], "complete": False}
    assert det.compare_locations(prev, cur, NOW) == []


def test_a_renamed_url_scheme_is_one_restructure_not_mass_closures():
    prev = {"places": ["a.com/l/%d" % i for i in range(10)], "complete": True}
    cur = {"places": ["a.com/locations/%d" % i for i in range(10)], "complete": True}
    (ev,) = det.compare_locations(prev, cur, NOW)
    assert ev["type"] == "site_restructured"


def test_a_big_opening_wave_is_capped_with_a_count():
    prev = {"places": ["a.com/l/x"], "complete": True}
    cur = {"places": ["a.com/l/x"] + ["a.com/l/n%d" % i for i in range(40)], "complete": True}
    evs = det.compare_locations(prev, cur, NOW)
    assert len(evs) == det.MAX_EVENTS_PER_KIND + 1
    assert evs[-1]["title"] == "15 more new location pages"


def test_a_new_structured_address_is_an_opening():
    prev = {"places": [], "addresses": ["1 Main St, Austin"], "complete": True}
    cur = {"places": [], "addresses": ["1 Main St, Austin", "9 Oak Ave, Dallas"], "complete": True}
    (ev,) = det.compare_locations(prev, cur, NOW)
    assert ev["type"] == "new_location" and ev["location"] == {"address": "9 Oak Ave, Dallas"}


# == catalog =======================================================================

def shop_product(pid, title, price, *, compare=None, available=True, created="2026-01-01"):
    return {"id": pid, "title": title, "handle": "h%d" % pid, "created_at": created + "T00:00:00Z",
            "product_type": "Shoes",
            "variants": [{"price": str(price), "compare_at_price": compare and str(compare),
                          "available": available}]}


def shopify_site():
    return site(signals={"platforms": [{"name": "shopify", "kind": "commerce"}]})


def test_shopify_catalog_pages_until_a_short_page(monkeypatch):
    monkeypatch.setattr(det, "SHOPIFY_PAGE", 2)
    net = Net({"https://acme.com/products.json?limit=2&page=1":
               {"products": [shop_product(1, "A", 10), shop_product(2, "B", 20, compare=25)]},
               "https://acme.com/products.json?limit=2&page=2": {"products": [shop_product(3, "C", 5)]}})
    r = det.read_catalog(ctx(net, shopify_site()))
    assert r["status"] == "ok" and r["complete"] and r["items"] == 3
    assert r["payload"]["products"]["2"] == ["B", "/products/h2", 20.0, 20.0, 25.0, True, 1,
                                             "2026-01-01", "Shoes", None]
    assert "prices 5.0 to 20.0, 1 on sale" in r["note"]


def test_a_catalog_cut_at_the_page_limit_is_incomplete(monkeypatch):
    monkeypatch.setattr(det, "SHOPIFY_PAGE", 1)
    monkeypatch.setattr(det, "SHOPIFY_MAX_PAGES", 2)
    net = Net({"https://acme.com/products.json?limit=1&page=1": {"products": [shop_product(1, "A", 1)]},
               "https://acme.com/products.json?limit=1&page=2": {"products": [shop_product(2, "B", 1)]}})
    r = det.read_catalog(ctx(net, shopify_site()))
    assert not r["complete"] and "removals are not reported" in r["note"]


def test_woocommerce_prices_are_read_in_minor_units():
    rs = site(signals={"platforms": [{"name": "woocommerce", "kind": "commerce"}]})
    net = Net({"https://acme.com/wp-json/wc/store/v1/products?per_page=100&page=1": [
        {"id": 7, "name": "Mug &#038; Lid", "permalink": "https://acme.com/p/mug", "is_in_stock": True,
         "prices": {"price": "1299", "regular_price": "1599", "currency_minor_unit": 2}}]})
    r = det.read_catalog(ctx(net, rs))
    assert r["payload"]["products"]["7"][:6] == ["Mug & Lid", "/p/mug", 12.99, 12.99, 15.99, True]


def test_no_shop_is_none_and_a_dead_feed_is_failed():
    assert det.read_catalog(ctx(Net(), site()))["status"] == "none"
    r = det.read_catalog(ctx(Net(), shopify_site()))
    assert r["status"] == "failed" and r["payload"] is None


def test_product_sitemap_is_the_fallback_without_prices():
    rs = site(signals={"sitemap": {"product_like": 30}})
    net = Net({"https://acme.com/sitemap.xml": urlset("https://acme.com/products/a",
                                                      "https://acme.com/about")})
    r = det.read_catalog(ctx(net, rs))
    assert r["payload"]["source"] == "sitemap" and list(r["payload"]["products"]) == ["acme.com/products/a"]


def test_first_read_reports_only_products_the_shop_dates_recent_grouped_by_family():
    payload = {"products": {
        "1": ["Tree Runner - Navy", "/products/a", 98.0, 98.0, None, True, 1, "2026-09-25", ""],
        "2": ["Tree Runner - Pink", "/products/b", 98.0, 110.0, None, True, 1, "2026-09-26", ""],
        "3": ["Old Shoe", "/products/c", 50.0, 50.0, None, True, 1, "2024-01-01", ""]}}
    (ev,) = det.baseline_catalog(payload, NOW)
    assert ev["title"] == "New product: Tree Runner (2 versions)"
    assert ev["summary"] == "Priced 98.0 to 110.0" and ev["date"] == "2026-09-25"


def test_a_launch_is_dated_by_publishing_and_an_old_product_put_back_is_not_one():
    payload = {"products": {
        "1": ["Flip Flop - Pink", "/p/a", 50.0, 50.0, None, True, 1, "2026-03-01", "", "2026-09-25"],
        "2": ["Classic - Navy", "/p/b", 90.0, 90.0, None, True, 1, "2022-01-01", "", "2026-10-01"]}}
    (ev,) = det.baseline_catalog(payload, NOW)
    assert ev["title"] == "New product: Flip Flop" and ev["date"] == "2026-09-25"


def row(title, price, *, compare=None, available=True):
    return [title, "/p/" + title, price, price, compare, available, 1, None, ""]


def test_catalog_changes_become_typed_events():
    prev = {"source": "shopify", "complete": True, "products": {
        "1": row("A", 100.0), "2": row("B", 50.0), "3": row("C", 20.0), "9": row("Gone", 1.0)}}
    cur = {"source": "shopify", "complete": True, "products": {
        "1": row("A", 110.0), "2": row("B", 40.0, compare=50.0), "3": row("C", 20.0, available=False),
        "4": row("New", 5.0)}}
    types = sorted(e["type"] for e in det.compare_catalog(prev, cur, NOW))
    assert types == ["price_cut", "price_increase", "product_launch", "product_removed",
                     "sale_started", "sold_out"]
    up = next(e for e in det.compare_catalog(prev, cur, NOW) if e["type"] == "price_increase")
    assert up["title"] == "A: price up from 100.0 to 110.0 (+10.0%)"


def test_removed_products_need_two_complete_reads():
    prev = {"source": "shopify", "complete": True, "products": {"1": row("A", 1.0)}}
    cur = {"source": "shopify", "complete": False, "products": {}}
    assert det.compare_catalog(prev, cur, NOW) == []


def test_a_shop_wide_price_rise_is_named_once():
    prev = {"source": "shopify", "complete": True,
            "products": {str(i): row("P%d" % i, 100.0) for i in range(20)}}
    cur = {"source": "shopify", "complete": True,
           "products": {str(i): row("P%d" % i, 105.0) for i in range(20)}}
    evs = det.compare_catalog(prev, cur, NOW)
    assert evs[0]["title"] == "Shop-wide price increases: 20 of 20 products"
    assert len(evs) == 1 + 5


def test_catalogs_read_two_different_ways_are_not_compared():
    prev = {"source": "sitemap", "complete": True, "products": {"x": row("A", None)}}
    cur = {"source": "shopify", "complete": True, "products": {"1": row("B", 1.0)}}
    assert det.compare_catalog(prev, cur, NOW) == []


# == promotions and pages ==========================================================

@pytest.mark.parametrize("line", ["Get 20% off everything", "Free shipping on orders over $50",
                                  "Frete grátis para todo o Brasil", "Versandkostenfrei ab 50 €",
                                  "Use code SAVE10 at checkout", "Black Friday starts now"])
def test_offer_lines_are_recognised_in_several_languages(line):
    assert det.PROMO.search(line)


def test_ordinary_lines_are_not_offers():
    for line in ("Our story", "Find a clinic near you", "Shop men's shoes"):
        assert not det.PROMO.search(line)


def test_promotions_come_from_the_homepage_without_link_addresses():
    rs = site(pages=[{"kind": "home", "status": "ok", "url": "https://acme.com/"}],
              texts={"https://acme.com/": "Welcome\n20% off sitewide [https://acme.com/sale]\nAbout us"})
    r = det.read_promotions(ctx(Net(), rs))
    assert r["payload"]["lines"] == ["20% off sitewide"]


def test_new_and_ended_offers():
    evs = det.compare_promotions({"lines": ["10% off"]}, {"lines": ["Free shipping over $50"]}, NOW)
    assert [e["type"] for e in evs] == ["promotion", "promotion_ended"]


def test_a_one_line_page_change_is_not_news_but_a_rewrite_is():
    base = ["line %d" % i for i in range(20)]
    prev = {"pages": {"home": {"url": "u", "hash": "a", "lines": base}}}
    small = {"pages": {"home": {"url": "u", "hash": "b", "lines": base[:-1] + ["2026-10-09"]}}}
    assert det.compare_pages(prev, small, NOW) == []
    big = {"pages": {"home": {"url": "u", "hash": "c",
                              "lines": base[:15] + ["New plans from $9", "Now in Texas", "x", "y"]}}}
    (ev,) = det.compare_pages(prev, big, NOW)
    assert ev["type"] == "page_changed" and "Now in Texas" in ev["summary"]


def test_wayback_history_counts_versions_and_says_when_it_could_not_ask():
    rows = [["timestamp", "digest"], ["20260801", "a"], ["20260901", "b"]]
    net = Net({})
    net.get = lambda url, **kw: ok(json.dumps(rows))
    assert det.page_history(net.get, "https://acme.com/pricing", NOW) == 2
    assert det.page_history(Net().get, "https://acme.com/pricing", NOW) is None


# == reviews =======================================================================

def test_reviews_track_the_oldest_products_and_keep_tracking_them():
    catalog = {"payload": {"source": "shopify", "products": {
        "1": ["Old", "/products/old", 1, 1, None, True, 1, "2020-01-01", ""],
        "2": ["New", "/products/new", 1, 1, None, True, 1, "2026-09-01", ""]}}}
    pages = {"https://acme.com/products/old":
             '<script type="application/ld+json">{"@type":"Product","name":"Old",'
             '"aggregateRating":{"ratingValue":"4.5","reviewCount":"120"}}</script>'}

    def fetch(u):
        return {"status": "ok", "html": pages[u], "final_url": u} if u in pages else \
            {"status": "error", "html": "", "final_url": u, "note": "x"}
    r = det.read_reviews(ctx(Net(), site(), results={"catalog": catalog}, fetch=fetch))
    assert r["payload"]["tracked"][0] == "https://acme.com/products/old"
    assert r["payload"]["scores"] == {"https://acme.com/products/old": ["Old", 4.5, 120]}
    assert not r["complete"]                # the second tracked page failed


def test_review_growth_needs_a_real_rise():
    prev = {"scores": {"u": ["A", 4.5, 1000]}}
    assert det.compare_reviews(prev, {"scores": {"u": ["A", 4.5, 1010]}}, NOW) == []
    (ev,) = det.compare_reviews(prev, {"scores": {"u": ["A", 4.5, 1100]}}, NOW)
    assert ev["title"] == "100 new reviews on 1 tracked pages (+10.0%)"


def test_no_review_count_anywhere_is_none():
    assert det.read_reviews(ctx(Net(), site()))["status"] == "none"


# == newsroom ======================================================================

RSS = """<rss><channel><item><title><![CDATA[We open in Austin &amp; Dallas]]></title>
<link>https://acme.com/news/austin</link><pubDate>Thu, 01 Oct 2026 07:00:00 GMT</pubDate></item>
<item><title>Old post</title><link>https://acme.com/news/old</link>
<pubDate>Mon, 01 Jan 2024 07:00:00 GMT</pubDate></item></channel></rss>"""
ATOM = """<feed><entry><title>Launch</title><link href="https://acme.com/blog/launch"/>
<updated>2026-09-30T10:00:00Z</updated></entry></feed>"""


def test_rss_and_atom_feeds_are_parsed():
    assert det.parse_feed(RSS)[0] == {"title": "We open in Austin & Dallas",
                                      "url": "https://acme.com/news/austin", "date": "2026-10-01"}
    assert det.parse_feed(ATOM) == [{"title": "Launch", "url": "https://acme.com/blog/launch",
                                     "date": "2026-09-30"}]


def test_newsroom_reads_the_announced_feed_and_dates_only_recent_posts_on_a_first_read():
    rs = site(signals={"feeds": ["https://acme.com/comments/feed/", "https://acme.com/news/feed/"]})
    net = Net({"https://acme.com/news/feed/": RSS})
    r = det.read_newsroom(ctx(net, rs))
    assert r["payload"]["source"] == "https://acme.com/news/feed/"
    (ev,) = det.baseline_newsroom(r["payload"], NOW)
    assert ev["title"] == "We open in Austin & Dallas" and ev["type"] == "announcement"


def test_wordpress_without_an_announced_feed_tries_slash_feed():
    rs = site(signals={"platforms": [{"name": "wordpress", "kind": "cms"}]})
    net = Net({"https://acme.com/feed/": RSS})
    assert det.read_newsroom(ctx(net, rs))["items"] == 2


def test_a_newsletter_page_is_not_a_news_page_and_comment_feeds_are_not_news():
    rs = site(pages=[{"kind": "press", "status": "ok",
                      "url": "https://acme.com/pages/sign-up-to-our-newsletter"}],
              signals={"feeds": ["https://acme.com/comments/feed/"]})
    assert det.read_newsroom(ctx(Net(), rs))["status"] == "none"


def test_a_press_page_without_a_feed_gives_its_article_links():
    rs = site(pages=[{"kind": "press", "status": "ok", "url": "https://acme.com/press"}])
    html = ('<a href="/press/2026/acme-opens-fifty-new-clinics">Acme opens fifty new clinics in Texas</a>'
            '<a href="/contact">Contact us about anything you like at all</a>'
            '<a href="https://other.com/press/x">Somebody else writes about something</a>')
    r = det.read_newsroom(ctx(Net(), rs, fetch=lambda u: {"status": "ok", "html": html,
                                                          "final_url": u}))
    assert [i["url"] for i in r["payload"]["items"]] == [
        "https://acme.com/press/2026/acme-opens-fifty-new-clinics"]


def test_new_posts_are_events_but_a_new_source_back_catalogue_is_not():
    prev = {"source": "f1", "kind": "feed", "items": [{"id": "a", "title": "A", "url": "u"}]}
    cur = {"source": "f1", "kind": "feed", "items": [{"id": "b", "title": "B", "url": "v"},
                                                     {"id": "a", "title": "A", "url": "u"}]}
    assert [e["title"] for e in det.compare_newsroom(prev, cur, NOW)] == ["B"]
    moved = {"source": "f2", "kind": "feed",
             "items": [{"id": str(i), "title": "T", "url": "u%d" % i} for i in range(9)]}
    assert det.compare_newsroom(prev, moved, NOW) == []


# == jobs ==========================================================================

@pytest.mark.parametrize("title, fn", [("Senior Software Engineer", "engineering"),
                                       ("Dental Hygienist", "clinical"),
                                       ("Store Manager - Austin", "store_ops"),
                                       ("Chief Marketing Officer", "leadership"),
                                       ("Account Executive, Mid-Market", "sales"),
                                       ("Warehouse Associate", "operations"),
                                       ("Barista", "store_ops")])
def test_titles_are_sorted_into_functions(title, fn):
    assert jobs.function_of(title) == fn


def test_workday_relative_dates():
    assert jobs.workday_posted("Posted Today", NOW) == "2026-10-09"
    assert jobs.workday_posted("Posted 5 Days Ago", NOW) == "2026-10-04"
    assert jobs.workday_posted("Posted 30+ Days Ago", NOW) is None


def gh_site(**sig):
    return site(signals=dict({"ats": [{"vendor": "greenhouse", "board": "acme", "url": ""}]}, **sig))


def test_greenhouse_jobs_are_counted_by_function_and_place():
    net = Net({"https://boards-api.greenhouse.io/v1/boards/acme/jobs": {"jobs": [
        {"id": 1, "title": "Store Manager", "location": {"name": "Austin, TX"},
         "first_published": "2026-10-01T00:00:00Z"},
        {"id": 2, "title": "Head of Growth", "location": {"name": "Remote"},
         "updated_at": "2026-01-01T00:00:00Z"}]}})
    r = jobs.read_jobs(ctx(net, gh_site()))
    p = r["payload"]
    assert p["open"] == 2 and p["by_function"] == {"store_ops": 1, "marketing": 1}
    assert p["senior_open"] == ["Head of Growth"] and "1 posted in the last 30 days" in r["note"]


def test_every_reader_parses_its_board(monkeypatch):
    net = Net({
        "https://api.lever.co/v0/postings/acme?mode=json": [
            {"id": "l1", "text": "Nurse", "categories": {"location": "Leeds"}, "createdAt": 1790000000000}],
        "https://api.ashbyhq.com/posting-api/job-board/acme": {"jobs": [
            {"id": "a1", "title": "PM", "location": "NYC", "publishedAt": "2026-09-01"},
            {"id": "a2", "title": "Hidden", "isListed": False}]},
        "https://api.smartrecruiters.com/v1/companies/acme/postings?limit=100&offset=0": {
            "totalFound": 1, "content": [{"id": "s1", "name": "Cashier",
                                          "location": {"city": "Lyon", "country": "fr"}}]},
        "https://apply.workable.com/api/v1/widget/accounts/acme": {"jobs": [
            {"shortcode": "W1", "title": "Chef", "city": "Rome", "country": "Italy"}]},
        "https://acme.recruitee.com/api/offers/": {"offers": [{"id": 5, "title": "Dev",
                                                               "location": "Berlin"}]},
        "https://acme.breezy.hr/json": [{"id": "b", "name": "Coach", "location": {"name": "Leeds"}}],
        "https://acme.bamboohr.com/careers/list": {"result": [
            {"id": 3, "jobOpeningName": "Driver", "location": {"city": "Ogden", "state": "UT"}}]},
        "https://acme.jobs.personio.de/xml": "<workzag-jobs><position><id>9</id><name>Koch</name>"
                                             "<office>Köln</office></position></workzag-jobs>"})
    for vendor, n in (("lever", 1), ("ashby", 1), ("smartrecruiters", 1), ("workable", 1),
                      ("recruitee", 1), ("breezy", 1), ("bamboohr", 1)):
        got = jobs.READERS[vendor](net.get_json, "acme", NOW)
        assert got[0] is not None and len(got[0]) == n, vendor
    found, total, complete, _ = jobs._personio(net.get, "acme", NOW)
    assert found == {"9": ["Koch", "Köln", "", None]} and complete


def test_workday_pages_through_its_post_listing():
    api = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Careers/jobs"
    page0 = {"total": 21, "jobPostings": [{"title": "T%d" % i, "locationsText": "Austin",
                                           "postedOn": "Posted Today", "bulletFields": ["R%d" % i]}
                                          for i in range(20)]}
    page1 = {"jobPostings": [{"title": "Last", "locationsText": "Dallas", "postedOn": "Posted 2 Days Ago",
                              "bulletFields": ["R20"]}]}
    net = Net(posts={(api, 0): page0, (api, 20): page1})
    found, total, complete, _ = jobs._workday(net.get, "acme.wd5.myworkdayjobs.com/en-US/Careers", NOW)
    assert (len(found), total, complete) == (21, 21, True)
    assert found["R20"] == ["Last", "Dallas", "", "2026-10-07"]


def test_a_board_without_a_public_listing_is_named_not_counted_as_no_jobs():
    rs = site(signals={"ats": [{"vendor": "icims", "board": "acme", "url": ""}]})
    r = jobs.read_jobs(ctx(Net(), rs))
    assert r["status"] == "failed" and "icims board found but it has no public listing" in r["note"]


def test_greenhouse_is_guessed_only_when_the_board_carries_the_company_name():
    net = Net({"https://boards-api.greenhouse.io/v1/boards/acmeco": {"name": "Acme Co"},
               "https://boards-api.greenhouse.io/v1/boards/acmeco/jobs": {"jobs": []}})
    e = {"id": 1, "domain": "acmeco.com", "name": "Acme Co"}
    r = jobs.read_jobs(ctx(net, site(), entity=e))
    assert r["status"] == "empty" and "guessed board" in r["note"]
    net = Net({"https://boards-api.greenhouse.io/v1/boards/acmeco": {"name": "Acme Cola Bottling"}})
    assert jobs.read_jobs(ctx(net, site(), entity=e))["status"] == "none"


def test_hiring_changes_become_events():
    prev = {"open": 10, "complete": True, "by_function": {"store_ops": 8}, "places_complete": True,
            "places": {"Austin, TX": 1}, "jobs": {"g:1": ["Barista", "Austin, TX", "", None]}}
    cur = {"open": 20, "complete": True, "by_function": {"store_ops": 16, "leadership": 1},
           "places_complete": True, "places": {"Austin, TX": 1, "Merced, CA": 2, "Remote": 1},
           "jobs": {"g:1": ["Barista", "Austin, TX", "", None],
                    "g:2": ["Store Manager", "Merced, CA", "", "2026-10-05"],
                    "g:3": ["Barista", "Merced, CA", "", None],
                    "g:4": ["VP Retail", "Remote", "", None]}}
    evs = jobs.compare_jobs(prev, cur, NOW)
    types = [e["type"] for e in evs]
    assert types == ["hiring_surge", "new_job_location", "senior_hire_search"]
    assert evs[1]["title"] == "Hiring in a new place: Merced, CA (2 roles)"
    assert "store ops +8" in evs[0]["summary"]


PHENOM_PAGE = ('<html><script>phApp.ddo = {"eagerLoadRefineSearch":{"status":200,"hits":1,'
               '"totalHits":1772,"data":{"jobs":[{"title":"Dentist","jobSeqNo":"X1",'
               '"cityState":"Olathe, Kansas","category":"Dentists",'
               '"postedDate":"2026-10-01T00:00:00.000+0000"}],"aggregations":['
               '{"field":"city","value":{"Olathe":3,"Merced":2}},'
               '{"field":"state","value":{"Kansas":3,"California":2}},'
               '{"field":"category","value":{"Dentists":5}}]}}};</script>'
               '<link href="https://cdn.phenompeople.com/x.css"></html>')


def test_a_phenom_careers_site_is_found_through_the_careers_link_and_counted_by_place():
    rs = site(signals={"careers_links": ["https://careers.acme.com/us/en"]})
    net = Net({"https://careers.acme.com/us/en/search-results": ok(PHENOM_PAGE)})

    def fetch(u):
        return {"status": "ok", "html": PHENOM_PAGE, "final_url": u}
    r = jobs.read_jobs(ctx(net, rs, fetch=fetch))
    p = r["payload"]
    assert p["open"] == 1772 and p["places"] == {"Merced": 2, "Olathe": 3}
    assert p["regions"] == {"California": 2, "Kansas": 3} and p["places_complete"]
    assert not p["complete"] and "counted by place but not read one by one" in r["note"]


def test_a_careers_page_that_embeds_a_known_board_uses_that_board():
    rs = site(signals={"careers_links": ["https://acme.com/careers"]})
    html = '<a href="https://jobs.lever.co/acme">Open roles</a>'
    net = Net({"https://api.lever.co/v0/postings/acme?mode=json": []})
    r = jobs.read_jobs(ctx(net, rs, fetch=lambda u: {"status": "ok", "html": html, "final_url": u}))
    assert r["status"] == "empty" and r["payload"]["boards"][0]["vendor"] == "lever"


def test_a_new_region_and_city_from_place_counts_even_without_the_role_list():
    prev = {"open": 100, "complete": False, "places_complete": True,
            "places": {"Olathe": 3}, "regions": {"Kansas": 3}}
    cur = {"open": 102, "complete": False, "places_complete": True,
           "places": {"Olathe": 3, "Boise": 2}, "regions": {"Kansas": 3, "Idaho": 2}}
    assert [e["title"] for e in jobs.compare_jobs(prev, cur, NOW)] == [
        "Hiring in a new region: Idaho (2 roles)", "Hiring in a new place: Boise (2 roles)"]


def test_places_from_a_partly_read_board_are_not_new_places():
    prev = {"open": 10, "complete": False, "places_complete": False, "places": {"Austin": 3}}
    cur = {"open": 11, "complete": True, "places_complete": True, "places": {"Austin": 3, "Boise": 1}}
    assert jobs.compare_jobs(prev, cur, NOW) == []


def test_a_board_read_in_part_marks_its_places_incomplete():
    api = "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/Careers/jobs"
    page0 = {"total": 50, "jobPostings": [{"title": "T", "locationsText": "Austin",
                                           "postedOn": "Posted Today", "bulletFields": ["R1"]}]}
    net = Net(posts={(api, 0): page0})
    rs = site(signals={"ats": [{"vendor": "workday", "board": "acme",
                                "url": "acme.wd5.myworkdayjobs.com/Careers"}]})
    r = jobs.read_jobs(ctx(net, rs))
    assert r["payload"]["places_complete"] is False


def test_role_level_changes_need_two_complete_lists():
    prev = {"open": 10, "complete": False, "jobs": {}}
    cur = {"open": 11, "complete": True, "jobs": {"x": ["VP Sales", "Paris", "", None]}}
    assert jobs.compare_jobs(prev, cur, NOW) == []


# == news ==========================================================================

def gnews(*items):
    return "<rss><channel>" + "".join(
        "<item><title>%s - %s</title><link>https://news.google.com/rss/articles/%d</link>"
        "<pubDate>%s</pubDate><source url=\"https://%s.com\">%s</source></item>"
        % (t, p, i, d, p.lower().replace(" ", ""), p) for i, (t, p, d) in enumerate(items)) + \
        "</channel></rss>"


def test_feed_items_lose_the_publisher_suffix():
    (it,) = news.parse_items(gnews(("Acme opens in Austin", "Daily News",
                                    "Thu, 01 Oct 2026 07:00:00 GMT")))
    assert it["title"] == "Acme opens in Austin" and it["publisher"] == "Daily News"
    assert it["publisher_site"] == "https://dailynews.com" and it["date"] == "2026-10-01"


def test_a_headline_must_name_the_company():
    assert news.names_company("Aspen Dental opens in Merced", "Aspen Dental", "aspendental.com")
    assert not news.names_company("Aspen skiing season opens", "Aspen Dental", "aspendental.com")
    assert news.names_company("{my}dentist closes 3 practices", "mydentist", "mydentist.co.uk")
    assert not news.names_company("Acmeco shares fall", "Acme", "acme.com")


def test_legal_suffixes_come_off_the_name():
    assert news.clean_name("Aspen Dental Management, Inc.", "aspendental.com") == "Aspen Dental Management"
    assert news.clean_name("", "gymshark.com") == "gymshark"


def test_news_reads_the_edition_of_the_company_country_and_folds_copies():
    feed = gnews(("Acme opens new store", "A", "Thu, 01 Oct 2026 07:00:00 GMT"),
                 ("Acme opens new store", "B", "Thu, 01 Oct 2026 08:00:00 GMT"),
                 ("Weather today", "C", "Thu, 01 Oct 2026 08:00:00 GMT"))
    asked = []

    def get(url, **kw):
        asked.append(url)
        return ok(feed)
    c = ctx(Net(), entity={"id": 1, "domain": "acme.de", "name": "Acme GmbH", "country": "DE"})
    c["get"] = get
    r = news.read_news(c)
    assert "ceid=DE%3Ade" in asked[0]
    assert r["items"] == 1 and r["payload"]["items"][0]["copies"] == 2


def test_a_full_english_feed_asks_again_and_a_full_other_language_one_says_it_was_cut():
    full = gnews(*[("Acme story %d" % i, "P", "Thu, 01 Oct 2026 07:00:00 GMT") for i in range(95)])
    for country, calls, complete in (("US", 2, True), ("DE", 1, False)):
        asked = []
        c = ctx(Net(), entity={"id": 1, "domain": "acme.com", "name": "Acme", "country": country})
        c["get"] = lambda url, **kw: (asked.append(url), ok(full))[1]
        r = news.read_news(c)
        assert len(asked) == calls and r["complete"] is complete


def test_news_that_did_not_answer_is_failed_and_the_breaker_stops_asking():
    b = news.Breaker(after=2, gap=0, sleep=lambda s: None)
    c = ctx(Net())
    c["news_breaker"] = b
    assert news.read_news(c)["status"] == "failed"
    news.read_news(c)
    assert b.open
    r = news.read_news(c)
    assert r["status"] == "failed" and "stopped answering" in r["note"]


def test_a_company_without_a_country_reads_the_client_country_edition():
    asked = []
    c = ctx(Net(), entity={"id": 1, "domain": "acme.in", "name": "Acme", "country": None})
    c["client_country"] = "IN"
    c["get"] = lambda url, **kw: (asked.append(url), ok(gnews()))[1]
    assert news.read_news(c)["status"] == "empty" and "ceid=IN%3Aen" in asked[0]


# == false failures found in the first live collections (2026-10-09) ==================

def test_a_woocommerce_plugin_without_products_is_no_shop():
    rs = site(signals={"platforms": [{"name": "woocommerce", "kind": "commerce"}]})
    r = det.read_catalog(ctx(Net(), rs))
    assert r["status"] == "none"
    # ... but a refused store API on a site with product pages is a failure.
    rs = site(signals={"platforms": [{"name": "woocommerce", "kind": "commerce"}],
                       "product_schema": True})
    assert det.read_catalog(ctx(Net(), rs))["status"] == "failed"


def test_a_sitemap_that_does_not_exist_is_none_but_one_that_refuses_is_failed():
    r = det.read_locations(ctx(Net(), site()))
    assert r["status"] == "none" and r["note"].startswith("no sitemap")
    net = Net({"https://acme.com/sitemap.xml": miss("blocked", "refused (HTTP 403)")})
    assert det.read_locations(ctx(net, site()))["status"] == "failed"


def test_a_feed_with_no_posts_is_empty_and_a_feed_that_is_a_page_is_none():
    rs = site(signals={"platforms": [{"name": "wordpress", "kind": "cms"}]})
    net = Net({"https://acme.com/feed/": "<rss><channel><title>x</title></channel></rss>"})
    assert det.read_newsroom(ctx(net, rs))["status"] == "empty"
    net = Net({"https://acme.com/feed/": "<html><body>Home</body></html>"})
    assert det.read_newsroom(ctx(net, rs))["status"] == "none"
    net = Net({"https://acme.com/feed/": miss("blocked", "refused (HTTP 403)")})
    assert det.read_newsroom(ctx(net, rs))["status"] == "failed"


def test_colourways_fold_even_without_a_space_before_the_dash():
    assert det._family("Women's Alta High Top- Mid Grey") == "Women's Alta High Top"
    assert det._family("3-Pack Crew Socks") == "3-Pack Crew Socks"


def test_the_summary_card_of_a_big_launch_wave_is_dated():
    payload = {"products": {str(i): ["P%d" % i, "/p/%d" % i, 1.0, 1.0, None, True, 1, "2026-09-01", "",
                                     "2026-09-01"] for i in range(30)}}
    evs = det.baseline_catalog(payload, NOW)
    assert evs[-1]["title"] == "5 more new products" and evs[-1]["date"] == "2026-10-09"


def test_a_shopify_product_feed_is_not_a_newsroom():
    rs = site(signals={"feeds": ["https://acme.com/collections/all.atom"]})
    net = Net({"https://acme.com/collections/all.atom": ATOM})
    assert det.read_newsroom(ctx(net, rs))["status"] == "none"
    products = ATOM.replace("/blog/launch", "/products/runner")
    rs = site(signals={"feeds": ["https://acme.com/feed.atom"]})
    net = Net({"https://acme.com/feed.atom": products})
    assert det.read_newsroom(ctx(net, rs))["status"] == "none"
