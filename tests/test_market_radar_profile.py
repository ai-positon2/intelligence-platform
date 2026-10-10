"""Market Radar Phase 1: reading a company's site (tracker/market_radar_site)
and turning it into a profile (tracker/market_radar_profile).

No network and no model: pages, robots.txt, sitemaps and the Claude client
are all fakes. Most cases here were found live on the 2026-10-09 test set of
12 real sites, and each test names the site that showed the trap.
"""

import json
import os
import sys
from types import SimpleNamespace

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import market_radar_profile as prof  # noqa: E402
from tracker import market_radar_site as site  # noqa: E402
from tests.test_market_radar_store import OWNER, pg, world  # noqa: E402,F401  (fixtures)


# == HTML, JSON-LD and vendor signals ============================================

PAGE = """<!doctype html><html lang="en-GB"><head><title> Acme Dental | Dentist in London </title>
<meta name="description" content="Family dentist in Kensington.">
<meta property="og:site_name" content="Acme Dental">
<link rel="alternate" hreflang="x-default" href="https://acme.example/">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"Dentist","name":"Acme Dental","telephone":"+44 20 7946 0000",
  "address":{"@type":"PostalAddress","streetAddress":"1 High St","addressLocality":"London",
             "postalCode":"W8 1AA","addressCountry":"United Kingdom"},
  "geo":{"latitude":"51.50","longitude":"-0.19"},"sameAs":["https://www.instagram.com/acmedental"]},
 {"@type":"WebSite","name":"Acme"}]}</script>
<script type="application/ld+json">{ not json </script>
<script src="https://cdn.shopify.com/s/files/theme.js"></script>
</head><body>
<a href="/about-us">About us</a> <a href="/impressum">Impressum</a> <a href="/unidades">Nossas unidades</a>
<a href="/cart">Cart</a> <a href="/brochure.pdf">Brochure</a> <a href="https://other.example/about">About them</a>
<a href="tel:+442079460000">Call</a> <a href="mailto:Hello@Acme.example?subject=hi">Email</a>
<a href="https://www.facebook.com/sharer/sharer.php?u=x">Share</a> <a href="https://www.facebook.com/acmedental">FB</a>
<a href="https://www.linkedin.com/company/acme-dental/">LinkedIn</a> <a href="https://twitter.com/intent/tweet">Tweet</a>
<a href="https://boards.greenhouse.io/acmedental">Jobs</a> <a href="https://acme.jobs.personio.de">Karriere</a>
</body></html>"""


def test_parse_html_collects_what_the_signals_need():
    d = site.parse_html(PAGE)
    assert d.lang == "en-GB" and d.title.strip().startswith("Acme Dental")
    assert d.meta["description"] == "Family dentist in Kensington."
    assert ("x-default", "https://acme.example/") in d.hreflang
    assert len(d.jsonld_raw) == 2 and ("/about-us", "About us") in d.links


def test_jsonld_graph_is_flattened_and_a_broken_block_is_skipped():
    nodes = site._jsonld_nodes(site.parse_html(PAGE).jsonld_raw)
    assert {t for n in nodes for t in site._types(n)} == {"Dentist", "WebSite"}
    [org] = site.organizations(nodes)
    assert org["address"]["country"] == "GB" and org["address"]["addressLocality"] == "London"
    assert org["geo"] == {"lat": 51.5, "lon": -0.19}
    assert org["same_as"] == ["https://www.instagram.com/acmedental"]


@pytest.mark.parametrize("value, code", [("US", "US"), ("uk", "GB"), ("United Kingdom", "GB"),
                                         ({"@type": "Country", "name": "DE"}, "DE"),
                                         ("Brasil", "BR"), ("Atlantis", None), (7, None)])
def test_country_codes_from_structured_addresses(value, code):
    assert site._country_code(value) == code


def test_social_profiles_skip_share_and_intent_links():
    hrefs = [h for h, _ in site.parse_html(PAGE).links]
    assert site.social_profiles(hrefs) == {"facebook": "acmedental", "linkedin": "acme-dental"}


def test_jobs_boards_are_found_with_their_board_names():
    hrefs = " ".join(h for h, _ in site.parse_html(PAGE).links)
    assert [(b["vendor"], b["board"]) for b in site.ats_boards(hrefs)] == [
        ("greenhouse", "acmedental"), ("personio", "acme")]


def test_a_workday_board_keeps_its_site_path():
    hay = "https://x.com/a https://acme.wd5.myworkdayjobs.com/en-US/Acme_Careers?q=1 https://y.com"
    (board,) = site.ats_boards(hay)
    assert board["vendor"] == "workday" and board["board"] == "acme"
    assert board["url"] == "acme.wd5.myworkdayjobs.com/en-US/Acme_Careers?q=1"


def test_feeds_and_review_scores_are_read_from_a_page():
    page = """<html><head>
      <link rel="alternate" type="application/rss+xml" title="News" href="/news/feed/">
      <link rel="alternate" hreflang="de" href="/de/">
      <script type="application/ld+json">{"@type":"Product","name":"Runner",
        "aggregateRating":{"@type":"AggregateRating","ratingValue":"4.6","reviewCount":"9,951"}}</script>
      <script type="application/ld+json">{"@type":"Product","name":"No count",
        "aggregateRating":{"ratingValue":"4"}}</script></head><body></body></html>"""
    assert site.parse_html(page).feeds == [("/news/feed/", "News")]
    assert site.ratings_from_html(page, "https://a.com/p") == [
        {"url": "https://a.com/p", "item": "Runner", "rating": 4.6, "count": 9951}]


@pytest.mark.parametrize("phone, code", [("+44 20 7946 0000", "GB"), ("0044 20 7946", "GB"),
                                         ("+1 (512) 555-0100", "US"), ("+91 80 4000 0000", "IN"),
                                         ("+351 21 000 0000", "PT"), ("020 7946 0000", None)])
def test_phone_prefixes(phone, code):
    assert site.phone_countries([phone]) == ([code] if code else [])


def test_currencies_prefer_structured_data_then_prices():
    markup = '{"priceCurrency":"BRL"} {"priceCurrency":"BRL"}'
    assert site.currencies(markup, "R$ 199,90 or US$ 40") [0] == "BRL"
    assert site.currencies("", "£25 £30 €10") == ["GBP", "EUR"]


# == country ==================================================================

def test_country_vote_names_its_evidence():
    v = site.country_vote("acme.co.uk", [{"name": "A", "address": {"country": "GB"}}],
                          ["+44 20 1"], ["GBP"], "en-GB", None)
    assert v["code"] == "GB" and v["confidence"] == 1.0
    assert [e["signal"] for e in v["evidence"]] == ["cctld", "jsonld_address", "phone", "currency",
                                                    "lang_region"]


def test_wordpress_default_en_us_barely_counts():
    # pembridgedental.co.uk and clovedental.in both declare en-US.
    v = site.country_vote("acme.in", [], ["+91 80 1"], [], "en-US", None)
    assert v["code"] == "IN" and v["confidence"] > 0.9


def test_language_is_ignored_after_a_location_redirect():
    # orangetheory.com sent a visitor in India to /en-in.
    v = site.country_vote("brand.com", [], [], ["USD"], "en-IN", None, geo_redirected=True)
    assert v["code"] == "US" and all(e["signal"] != "lang_region" for e in v["evidence"])


def test_a_dot_com_with_german_text_and_numbers_is_german():
    # snocks.com: no structured address, no phone links, EUR.
    v = site.country_vote("snocks.com", [], [], ["EUR"], "de", None,
                          text_phones=["+49 621 000000"], has_impressum=True)
    assert v["code"] == "DE"
    assert {e["signal"] for e in v["evidence"]} == {"phone_text", "lang_only", "impressum"}


def test_text_phones_count_only_when_there_are_no_phone_links():
    v = site.country_vote("brand.com", [], ["+1 512 555 0100"], [], None, None,
                          text_phones=["+44 20 7946 0000"])
    assert v["code"] == "US" and all(e["signal"] != "phone_text" for e in v["evidence"])


def test_no_signals_means_no_country():
    assert site.country_vote("brand.com", [], [], [], None, None)["code"] is None


def test_dot_co_is_not_colombia():
    assert site.country_vote("exceldent.co", [], [], [], None, None)["code"] is None


# == robots.txt ================================================================

ROBOTS = """User-agent: *
Disallow: /private
Disallow: /*?sort=
Allow: /private/press
Sitemap: https://acme.example/sitemap.xml

User-agent: Position2
Disallow: /secret$
"""


def test_robots_our_own_group_wins_over_star():
    rules, maps = site.parse_robots(ROBOTS)
    assert maps == ["https://acme.example/sitemap.xml"]
    assert rules == {"disallow": ["/secret$"], "allow": []}
    assert not site.allowed("/secret", rules) and site.allowed("/secret/x", rules)


def test_robots_star_rules_longest_match_and_wildcards():
    rules, _ = site.parse_robots(ROBOTS.split("User-agent: Position2")[0])
    assert not site.allowed("/private/x", rules)
    assert site.allowed("/private/press", rules)          # the longer Allow wins
    assert not site.allowed("/shop?sort=price", rules)    # wildcard
    assert site.allowed("/about", rules)


def test_no_robots_allows_everything():
    rules, maps = site.parse_robots(None)
    assert maps == [] and site.allowed("/anything", rules)


# == choosing pages ===============================================================

def test_pick_pages_by_kind_in_any_language_same_site_only():
    rules, _ = site.parse_robots("User-agent: *\nDisallow: /unidades")
    chosen = site.pick_pages(site.parse_html(PAGE).links, "https://acme.example/", rules)
    assert chosen == [("about", "https://acme.example/about-us"),
                      ("legal", "https://acme.example/impressum")]   # /unidades refused by robots


def test_pick_pages_skips_carts_files_and_other_sites():
    links = [("/cart", "Shop cart"), ("/menu.pdf", "Our services"), ("https://x.example/about", "About"),
             ("/services/", "Services"), ("/services", "Services again")]
    assert site.pick_pages(links, "https://acme.example/", {}) == [
        ("offerings", "https://acme.example/services/")]


@pytest.mark.parametrize("requested, final, expected", [
    ("https://orangetheory.com/", "https://www.orangetheory.com/en-in", "locale path /en-in"),
    ("https://gymshark.com/", "https://us.checkout.gymshark.com/", "host us.checkout.gymshark.com"),
    ("https://acme.com/", "https://www.acme.com/", None),
    ("https://acme.com/en-us/", "https://acme.com/en-us/", None),
])
def test_geo_redirect(requested, final, expected):
    assert site.geo_redirect(requested, final) == expected


# == fake network for whole-site reads =============================================

def page(url, html, status="ok", final=None, http=200):
    text = site.html_to_linked_text(html, final or url) if status == "ok" else ""
    return {"url": url, "final_url": final or url, "status": status, "http_status": http,
            "html": html if status == "ok" else "", "text": text, "note": "", "truncated": False}


def words(n, prefix="word"):
    return " ".join("%s%d" % (prefix, i) for i in range(n))


@pytest.fixture
def web(monkeypatch):
    """A fake web: {url: page dict} for fetch, {url: text} for fetch_text."""
    pages, texts, asked = {}, {}, []

    def fake_fetch(url):
        asked.append(url)
        return pages.get(url) or page(url, "", status="not_found", http=404)

    monkeypatch.setattr(site, "fetch", fake_fetch)
    monkeypatch.setattr(site, "fetch_text", lambda url, limit=0: texts.get(url))
    return SimpleNamespace(pages=pages, texts=texts, asked=asked)


def test_sitemap_index_is_followed_places_and_products_first(web):
    web.texts["https://a.example/sitemap.xml"] = (
        '<sitemapindex><sitemap><loc>https://a.example/blog-sitemap.xml</loc></sitemap>'
        '<sitemap><loc>https://a.example/locations-sitemap.xml</loc></sitemap></sitemapindex>')
    web.texts["https://a.example/locations-sitemap.xml"] = "<urlset>" + "".join(
        "<url><loc>https://a.example/locations/town-%d/</loc></url>" % i for i in range(30)) + "</urlset>"
    web.texts["https://a.example/blog-sitemap.xml"] = "<urlset>" + "".join(
        "<url><loc>https://a.example/products/p-%d</loc></url>" % i for i in range(25)) + "</urlset>"
    sm = site.read_sitemap("https://a.example/", [])
    assert sm["urls"] == 55 and sm["location_like"] == 30 and sm["product_like"] == 25
    assert sm["top_sections"][0] == ("/locations", 30)


BAD = "https://[wd_hustle id=1 type=popup]"


def test_an_address_python_cannot_take_apart_is_dropped_not_fatal(web):
    """toothaffair.com (2026-10-10): a plugin shortcode in square brackets in
    a link made urljoin raise and took the whole read down."""
    assert site.parseable("/about") and site.parseable("https://a.example/x[1]")
    assert not site.parseable(BAD) and not site.parseable("//[not an ip]/x")
    html = ('<html lang="en"><head><title>Tooth</title><link rel="canonical" href="%s">'
            '<link rel="alternate" hreflang="en" href="%s"><script src="%s"></script></head>'
            '<body><a href="%s">Offer</a><a href="/contact">Contact us</a> %s</body></html>'
            % (BAD, BAD, BAD, BAD, words(300)))
    web.pages["https://tooth.example/"] = page("https://tooth.example/", html)
    web.texts["https://tooth.example/sitemap.xml"] = (
        "<urlset><url><loc>%s</loc></url><url><loc>https://tooth.example/clinics/a/</loc></url></urlset>"
        % BAD.replace(" ", "%20"))
    rs = site.read_site("https://tooth.example/")
    assert rs["status"] == "ok" and rs["pages"][0]["status"] == "ok"
    assert "https://tooth.example/contact" in web.asked
    assert not any("wd_hustle" in u for u in web.asked)
    assert rs["signals"]["sitemap"]["urls"] == 1          # the bracketed entry skipped, not fatal


def test_the_page_reader_keeps_no_address_python_cannot_parse():
    c = site._Collector()
    c.feed('<link rel="canonical" href="%s"><link rel="alternate" hreflang="de" href="%s">'
           '<link rel="alternate" type="application/rss+xml" href="%s">'
           '<link rel="alternate" hreflang="en" href="/en/"><a href="%s">x</a><a href="/ok">ok</a>'
           % (BAD, BAD, BAD, BAD))
    assert c.canonical is None and c.feeds == [] and c.hreflang == [("en", "/en/")]
    assert c.links == [("/ok", "ok")]


def test_a_reader_that_breaks_still_returns_a_failed_read(monkeypatch):
    def boom(url):
        raise RuntimeError("parser exploded")
    monkeypatch.setattr(site, "choose_home", boom)
    rs = site.read_site("tooth.example")
    assert rs["status"] == "failed" and rs["home_url"] == "https://tooth.example"
    assert "the website reader broke on this site (RuntimeError: parser exploded)" in rs["pages"][0]["note"]
    assert rs["signals"] == {} and rs["domain"] == "tooth.example"


def test_no_sitemap_is_none_not_an_empty_one(web):
    assert site.read_sitemap("https://a.example/", []) is None


def test_bare_domain_that_lands_on_a_useless_host_is_read_as_www(web):
    # gymshark.com led to a 3-word checkout subdomain; www.gymshark.com is the store.
    web.pages["https://gym.example/"] = page("https://gym.example/", "<p>loading</p>",
                                             final="https://us.checkout.gym.example/")
    web.pages["https://www.gym.example/"] = page("https://www.gym.example/",
                                                 "<p>%s</p>" % words(400))
    home, notes = site.choose_home("https://gym.example/")
    assert home["final_url"] == "https://www.gym.example/" and home["geo_redirect"] is None
    assert notes and "www.gym.example" in notes[0]


def test_a_location_redirect_is_undone_with_the_sites_own_default(web):
    # orangetheory.com: India got /en-in; the page names /en-us as x-default.
    india = ('<html lang="en-IN"><head><link rel="alternate" hreflang="x-default" '
             'href="https://www.fit.example/en-us"></head><body>%s</body></html>' % words(300))
    web.pages["https://www.fit.example/"] = page("https://www.fit.example/", india,
                                                 final="https://www.fit.example/en-in")
    web.pages["https://www.fit.example/en-us"] = page("https://www.fit.example/en-us",
                                                      "<p>%s</p>" % words(300))
    home, notes = site.choose_home("https://www.fit.example/")
    assert home["final_url"] == "https://www.fit.example/en-us" and home["geo_redirect"] is None
    assert "en-in" in notes[0] and "default version" in notes[0]


def test_a_location_redirect_without_a_default_stays_flagged(web):
    web.pages["https://www.fit.example/"] = page("https://www.fit.example/", "<p>%s</p>" % words(300),
                                                 final="https://www.fit.example/en-in")
    home, _ = site.choose_home("https://www.fit.example/")
    assert home["geo_redirect"] == "locale path /en-in"


def test_read_site_end_to_end(web):
    web.pages["https://acme.example/"] = page("https://acme.example/", PAGE.replace(
        "</body>", "<p>%s</p></body>" % words(200)))
    web.pages["https://acme.example/about-us"] = page("https://acme.example/about-us",
                                                      "<p>%s</p>" % words(50, "about"))
    web.pages["https://acme.example/unidades"] = page("https://acme.example/unidades",
                                                      "<p>%s</p>" % words(20, "loc"))
    out = site.read_site("acme.example")
    assert out["status"] == "ok" and out["domain"] == "acme.example"
    kinds = [(p["kind"], p["status"]) for p in out["pages"]]
    assert kinds == [("home", "ok"), ("about", "ok"), ("legal", "not_found"), ("locations", "ok")]
    s = out["signals"]
    assert s["platforms"] == [{"name": "shopify", "kind": "commerce"}]
    assert s["phones"] == ["+442079460000", "+44 20 7946 0000"]
    assert s["emails"] == ["hello@acme.example"]
    assert out["country"]["code"] == "GB"
    assert "runs on a shop platform: shopify" in out["hints"]
    assert "structured data gives one business address" in out["hints"]
    assert set(out["texts"]) == {"https://acme.example/", "https://acme.example/about-us",
                                 "https://acme.example/unidades"}


def test_a_site_built_in_the_browser_is_flagged(web):
    web.pages["https://spa.example/"] = page("https://spa.example/",
                                             '<div id="__next"></div><p>Loading</p>')
    out = site.read_site("https://spa.example/")
    assert out["needs_browser"] and "built in the browser" in out["pages"][0]["note"]


def test_an_unreachable_site_says_so(web):
    out = site.read_site("https://down.example/")
    assert out["status"] == "not_found" and out["texts"] == {}


# == business-type hints ===============================================================

def signals(**over):
    base = {"platforms": [], "sitemap": None, "product_schema": False, "has_cart": False,
            "location_links": {"count": 0}, "locator_vendors": [], "organizations": []}
    base.update(over)
    return base


def test_a_woocommerce_plugin_without_products_is_not_a_shop():
    # austincitydental.com and pembridgedental.co.uk
    hints = site.archetype_hints(signals(platforms=[{"name": "woocommerce", "kind": "commerce"}]))
    assert hints == ["has a shop plugin installed (woocommerce) but no product pages were found"]


def test_woocommerce_with_products_is_a_shop_and_shopify_always_is():
    with_products = signals(platforms=[{"name": "woocommerce", "kind": "commerce"}],
                            sitemap={"product_like": 40, "location_like": 0})
    assert site.archetype_hints(with_products)[0] == "has a shop plugin with products: woocommerce"
    assert site.archetype_hints(signals(platforms=[{"name": "shopify", "kind": "commerce"}]))[0] == \
        "runs on a shop platform: shopify"


def test_one_place_written_two_ways_is_one_address():
    orgs = [{"address": {"addressLocality": "Austin", "postalCode": "78701", "streetAddress": "1 Main"}},
            {"address": {"addressLocality": "Austin ", "postalCode": "78701", "country": "US"}}]
    assert site.archetype_hints(signals(organizations=orgs)) == [
        "structured data gives one business address"]


def test_location_pages_named_near_me_count():
    # clovedental.in lists 688 pages under /dentist-near-me/
    assert site.LOCATION_PATH.search("/dentist-near-me/hsr-layout/")


# == the corpus =========================================================================

def test_corpus_keeps_the_homepage_whole_and_drops_shared_chrome():
    texts = {"https://a/": "Menu\nFooter\nHome intro",
             "https://a/about": "Menu\nFooter\nAbout text",
             "https://a/contact": "Menu\nFooter\nContact text"}
    corpus = prof.build_corpus({"texts": texts})
    assert "=== https://a/ ===\nMenu\nFooter\nHome intro" in corpus
    assert "=== https://a/about ===\nAbout text" in corpus


def test_corpus_is_capped():
    texts = {"https://a/%d" % i: "x" * 20_000 for i in range(10)}
    assert len(prof.build_corpus({"texts": texts})) <= prof.CORPUS_CHARS + 200


# == the model, faked =====================================================================

GOOD = {"name": "Acme Dental", "one_liner": "A family dental practice \u2014 in London.",
        "offerings": ["Check-ups", "Implants"], "customer_type": "B2C", "archetype": "local_single",
        "archetype_reason": "One practice.", "business_model": "Private dental fees",
        "sells_online": False, "location_count": 1, "location_count_basis": "One address",
        "hq": {"city": "London", "region": "", "country_code": "gb"}, "markets": ["gb", "GBR"],
        "service_area": "West London", "price_positioning": "premium",
        "industry": {"plain_label": "Dentist", "naics_code": "621210",
                     "naics_title": "Offices of Dentists", "keywords": ["dentist"]},
        "competitors_named": [], "languages": ["English"],
        "evidence": [{"field": "name", "quote": "Family dentist \u2014 Kensington",
                      "url": "https://acme.example/"}],
        "confidence": {"archetype": "high", "industry": "high", "hq": "high", "location_count": "high"},
        "unknowns": []}


def reply(body=GOOD, stop="end_turn", model="claude-sonnet-5-5",
          usage=None):
    usage = usage or {"input_tokens": 12_000, "output_tokens": 2_000}
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""),
                 SimpleNamespace(type="text", text=json.dumps(body) if not isinstance(body, str) else body)],
        stop_reason=stop, model=model, stop_details=None,
        usage=SimpleNamespace(model_dump=lambda: dict(usage)))


class FakeClient:
    def __init__(self, *replies, beta_error=None, plain_error=None):
        self.replies, self.calls = list(replies), []
        self.beta_error, self.plain_error = beta_error, plain_error
        outer = self

        class _Msgs:
            def __init__(self, beta):
                self.beta = beta

            def create(self, **kw):
                outer.calls.append(("beta" if self.beta else "plain", kw))
                err = outer.beta_error if self.beta else outer.plain_error
                if err:
                    raise err
                return outer.replies.pop(0)

        self.messages = _Msgs(False)
        self.beta = SimpleNamespace(messages=_Msgs(True))


def api_error(cls, status, message):
    import httpx2
    resp = httpx2.Response(status, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls(message, response=resp, body=None)


def test_the_request_asks_for_structured_output_with_fallbacks_and_no_tools():
    client = FakeClient(reply())
    parsed, meta = prof.ask_model({"website": "x"}, "TEXT", client=client)
    kind, kw = client.calls[0]
    assert kind == "beta" and kw["fallbacks"] == "default"
    assert kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"]["format"]["schema"] is prof.PROFILE_SCHEMA
    assert kw["output_config"]["effort"] == "low"
    assert "tools" not in kw and kw["model"] == prof.MODEL
    assert parsed["name"] == "Acme Dental"


def test_a_rejected_fallback_option_is_retried_without_it():
    import anthropic
    client = FakeClient(reply(), beta_error=api_error(anthropic.BadRequestError, 400,
                                                      "fallbacks: unsupported with output_config"))
    parsed, meta = prof.ask_model({}, "T", client=client)
    assert [k for k, _ in client.calls] == ["beta", "plain"]
    assert meta["fallbacks"].startswith("rejected")


@pytest.mark.parametrize("stop, kind", [("refusal", "refused"), ("max_tokens", "truncated")])
def test_refusals_and_truncation_are_errors_not_profiles(stop, kind):
    with pytest.raises(prof.ProfileError) as e:
        prof.ask_model({}, "T", client=FakeClient(reply(stop=stop)))
    assert e.value.kind == kind


def test_unparseable_text_is_an_error():
    with pytest.raises(prof.ProfileError) as e:
        prof.ask_model({}, "T", client=FakeClient(reply(body="{not json")))
    assert e.value.kind == "bad_json"


from tracker import market_radar_ledger as ledger  # noqa: E402


def test_a_measured_call_is_priced_at_the_model_that_served_it(world):
    # A fallback can serve the request on another model at that model's rates.
    client = FakeClient(reply(model="claude-opus-5-5", usage={"input_tokens": 10_000,
                                                             "output_tokens": 1_000}))
    prof.ask_model({}, "T", client=client, run_id=world["run"])
    s = ledger.summary(world["run"])
    assert s["total_usd"] == 0.06 and not s["partial"]       # 10k x $4 + 1k x $20


def test_a_refused_reply_is_still_billed(world):
    with pytest.raises(prof.ProfileError):
        prof.ask_model({}, "T", client=FakeClient(reply(stop="refusal")), run_id=world["run"])
    assert ledger.summary(world["run"])["calls_by_status"] == {"done": 1}


def test_a_request_the_api_rejects_costs_nothing(world):
    import anthropic
    err = api_error(anthropic.AuthenticationError, 401, "invalid x-api-key")
    with pytest.raises(prof.ProfileError) as e:
        prof.ask_model({}, "T", client=FakeClient(beta_error=err), run_id=world["run"])
    s = ledger.summary(world["run"])
    assert e.value.kind == "api_error" and s["total_usd"] == 0 and s["calls_by_status"] == {"failed": 1}


def test_a_dropped_connection_counts_at_its_booking(world):
    with pytest.raises(prof.ProfileError):
        prof.ask_model({}, "T", client=FakeClient(beta_error=ConnectionError("reset")),
                       run_id=world["run"])
    s = ledger.summary(world["run"])
    assert s["partial"] and s["calls_by_status"] == {"unmeasured": 1} and s["total_usd"] > 0


def test_no_call_is_made_when_the_budget_cannot_cover_it(world):
    ledger.reserve(world["run"], "x", "anthropic", model="claude-sonnet-5-5",
                   prompt_tokens=45_000, max_output_tokens=0)          # $0.09 of $0.10
    client = FakeClient(reply())
    with pytest.raises(prof.ProfileError) as e:
        prof.ask_model({}, "T", client=client, run_id=world["run"])
    assert e.value.kind == "budget" and client.calls == []


# == checking and assembling ==============================================================

def site_for(**over):
    base = {"status": "ok", "home_url": "https://acme.example/", "domain": "acme.example",
            "country": {"code": "GB", "confidence": 1.0, "evidence": []},
            "signals": signals(), "hints": [], "pages": [{"kind": "home", "url": "https://acme.example/",
                                                          "status": "ok", "note": ""}],
            "texts": {"https://acme.example/": "Family dentist \u2014 Kensington [https://acme.example/x] since 1990"},
            "home_notes": [], "needs_browser": False, "robots": None, "read_at": "2026-10-09T00:00:00+00:00"}
    base.update(over)
    return base


def test_assemble_cleans_prose_but_never_the_quotes():
    p = prof.assemble(site_for(), dict(GOOD), {"model_served": "claude-sonnet-5-5"})
    assert p["one_liner"] == "A family dental practice, in London."
    assert p["evidence"][0]["quote"] == "Family dentist \u2014 Kensington"   # verbatim from the page
    assert p["hq"]["country_code"] == "GB" and p["markets"] == ["GB"]
    assert p["checks"] == []          # the quote is found, ignoring dash and link markers


def test_checks_flag_a_shop_with_no_shop():
    p = dict(GOOD, archetype="ecommerce", sells_online=True)
    assert "no shop platform or product pages" in prof.checks(site_for(), p)[0]


def test_checks_flag_a_single_location_with_hundreds_of_location_pages():
    s = site_for(signals=signals(sitemap={"location_like": 500, "product_like": 0}))
    assert "500 location-like pages" in prof.checks(s, dict(GOOD))[0]


def test_checks_flag_a_headquarters_the_sites_own_signals_contradict():
    p = dict(GOOD, hq={"city": "Austin", "region": "TX", "country_code": "US"})
    assert "point to GB (100%)" in prof.checks(site_for(), p)[0]


def test_checks_flag_quotes_not_on_the_page_or_from_unread_pages():
    p = dict(GOOD, evidence=[{"field": "name", "quote": "Best dentist in Paris", "url": "https://acme.example/"},
                             {"field": "hq", "quote": "x", "url": "https://elsewhere.example/"}])
    out = prof.checks(site_for(), p)
    assert "1 evidence quote(s) cite a page that was not read." in out
    assert "1 evidence quote(s) do not appear on the page they cite." in out


def test_hq_uses_the_sites_own_coordinates_only_for_one_place(monkeypatch):
    monkeypatch.setattr(prof, "geocode", lambda q, conn=None: {"lat": 1.0, "lon": 2.0, "label": q})
    one = {"archetype": "local_single", "hq": {"city": "London"},
           "facts": {"structured_locations": [{"geo": {"lat": 51.5, "lon": -0.19}}]}}
    assert prof.locate_hq(one)["source"] == "structured data"
    chain = {"archetype": "multi_location", "hq": {"city": "Chicago", "country_code": "US"},
             "facts": {"structured_locations": [{"geo": {"lat": 1, "lon": 1}}, {"geo": {"lat": 2, "lon": 2}}]}}
    assert prof.locate_hq(chain) == {"lat": 1.0, "lon": 2.0, "label": "Chicago, US", "source": "geocoded",
                                     "precision": "city"}
    assert prof.locate_hq({"hq": {"city": ""}, "facts": {}}) is None


def test_build_profile_end_to_end_saves_the_company(world, monkeypatch):
    monkeypatch.setattr(prof.site_reader, "read_site", lambda url: site_for())
    monkeypatch.setattr(prof, "geocode", lambda q, conn=None: None)
    out = prof.build_profile("https://acme.example/", client=FakeClient(reply()), run_id=world["run"])
    assert out["status"] == "ok" and out["entity_id"]
    from tracker import market_radar_store as store
    row = store.get_entity(out["entity_id"])
    assert (row["domain"], row["name"], row["country"], row["archetype"]) == \
        ("acme.example", "Acme Dental", "GB", "local_single")
    assert row["profile"]["industry"]["naics_code"] == "621210"
    assert row["profile"]["model"]["cost_usd"] == 0.044      # 12k x $2 + 2k x $10


def test_build_profile_reports_an_unreadable_site_without_calling_the_model(monkeypatch):
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    monkeypatch.setattr(prof.site_reader, "read_site", lambda url: {"status": "blocked", "pages": [],
                                                                   "texts": {}})
    client = FakeClient()
    out = prof.build_profile("https://x.example/", client=client, save=False)
    assert out["status"] == "unreadable" and client.calls == []


BLOCKED = {"status": "blocked", "domain": "weg.net", "home_url": "https://www.weg.net/", "texts": {},
           "pages": [{"kind": "home", "url": "https://www.weg.net/", "status": "blocked", "note": "refused (HTTP 403)"}],
           "signals": {}, "home_notes": ["the site refused this server (HTTP 403)"]}
WEG = {"organization": {"name": "WEG", "short_description": "WEG makes electric motors, drives and transformers.",
                        "industry": "electrical/electronic manufacturing", "keywords": ["electric motors", "drives"],
                        "city": "Jaragua do Sul", "country": "Brazil", "estimated_num_employees": 40000,
                        "linkedin_url": "http://www.linkedin.com/company/weg"}}


def test_a_site_that_refuses_us_is_read_from_apollos_record_instead(world, monkeypatch):
    from tracker import market_radar_ledger as ledger
    monkeypatch.setattr(prof.site_reader, "read_site", lambda url: dict(BLOCKED))
    monkeypatch.setattr(prof, "geocode", lambda q, conn=None: None)
    asked = []
    client = FakeClient(reply())
    out = prof.build_profile("https://www.weg.net/", client=client, run_id=world["run"],
                             apollo_enrich=lambda d: asked.append(d) or WEG)
    assert out["status"] == "ok" and asked == ["weg.net"]
    user = client.calls[0][1]["messages"][0]["content"]
    assert "=== Apollo company record (the website refused our reader) ===" in user
    assert "What it does: WEG makes electric motors" in user and "Keywords: electric motors, drives" in user
    p = out["profile"]
    assert p["facts"]["read_from_apollo"] is True and p["facts"]["socials"]["linkedin"] == "weg"
    assert any("read from Apollo's company record" in n for n in p["coverage"]["home_notes"])
    calls = ledger.summary(world["run"])
    assert calls["by_provider"].get("apollo") == 0 and calls["calls_by_status"]["done"] == 2   # Apollo + model


@pytest.mark.parametrize("record", [{}, {"organization": {"name": "WEG"}}, None])
def test_an_apollo_record_with_nothing_to_read_leaves_the_site_unreadable(monkeypatch, record):
    monkeypatch.setattr(prof.site_reader, "read_site", lambda url: dict(BLOCKED))
    client = FakeClient()
    out = prof.build_profile("https://www.weg.net/", client=client, save=False,
                             apollo_enrich=lambda d: record)
    assert out["status"] == "unreadable" and client.calls == []

    def boom(d):
        raise ConnectionError("down")
    assert prof.build_profile("https://www.weg.net/", client=client, save=False,
                              apollo_enrich=boom)["status"] == "unreadable"


def test_geocode_is_cached_and_survives_a_failure(pg, monkeypatch):
    calls = []

    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"lat": "30.27", "lon": "-97.74", "display_name": "Austin, Texas"}]

    monkeypatch.setattr(prof.requests, "get", lambda *a, **k: calls.append(1) or R())
    monkeypatch.setattr(prof.time, "sleep", lambda s: None)
    assert prof.geocode("Austin, TX, US")["lat"] == 30.27
    assert prof.geocode("Austin,  TX, US")["lat"] == 30.27     # same query once spaces settle
    assert len(calls) == 1

    def boom(*a, **k):
        raise ConnectionError("down")
    monkeypatch.setattr(prof.requests, "get", boom)
    assert prof.geocode("Nowhere") is None


# == the admin route ======================================================================

ROUTE = "/p2/admin/external-usage/market-radar-profile-check"
ADMIN = "reporting@position2.com"


def _client(email):
    import app as appmod
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


@pytest.fixture
def no_profile(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("the route tried to spend money")
    monkeypatch.setattr(prof, "build_profile", refuse)


@pytest.mark.parametrize("body", [None, {"url": "acme.example"}, {"url": "acme.example", "confirm_spend": "yes"}])
def test_route_needs_an_explicit_confirmation(no_profile, body):
    c = _client(ADMIN)
    resp = c.post(ROUTE, json=body) if body is not None else c.post(ROUTE)
    assert resp.status_code == 400 and "confirm_spend" in resp.get_json()["error"]


def test_route_refuses_a_url_without_a_host(no_profile):
    resp = _client(ADMIN).post(ROUTE, json={"url": "localhost", "confirm_spend": True})
    assert resp.status_code == 400 and "usable company URL" in resp.get_json()["error"]


@pytest.mark.parametrize("email, headers, status", [
    (None, {}, 302), ("someone@position2.com", {}, 403),
    (ADMIN, {"Origin": "https://evil.example"}, 403)])
def test_route_access(no_profile, email, headers, status):
    resp = _client(email).post(ROUTE, json={"url": "acme.example", "confirm_spend": True}, headers=headers)
    assert resp.status_code == status


def test_route_profiles_books_and_closes_the_run(world, monkeypatch):
    monkeypatch.setattr(prof.site_reader, "read_site", lambda url: site_for())
    monkeypatch.setattr(prof, "geocode", lambda q, conn=None: None)
    monkeypatch.setattr(prof, "_client", lambda: FakeClient(reply()))
    resp = _client(ADMIN).post(ROUTE, json={"url": "https://acme.example/", "confirm_spend": True})
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["status"] == "ok" and body["profile"]["archetype"] == "local_single"
    assert body["ledger"]["total_usd"] == 0.044 and not body["ledger"]["partial"]
    from tracker import market_radar_store as store
    assert store.get_run(body["run_id"], ADMIN)["status"] == "complete"


def test_route_marks_the_run_failed_when_the_model_fails(world, monkeypatch):
    monkeypatch.setattr(prof.site_reader, "read_site", lambda url: site_for())
    monkeypatch.setattr(prof, "_client", lambda: FakeClient(reply(stop="refusal")))
    body = _client(ADMIN).post(ROUTE, json={"url": "acme.example", "confirm_spend": True}).get_json()
    assert body["status"] == "failed" and body["error"]["kind"] == "refused"
    from tracker import market_radar_store as store
    run = store.get_run(body["run_id"], ADMIN)
    assert run["status"] == "failed" and "refused" in run["error"]


def test_robots_and_sitemaps_are_requested_with_an_accept_header(monkeypatch):
    # Shopify answered 403 to robots.txt and sitemap.xml without one
    # (allbirds.com, snocks.com, 2026-10-09), which hid every shop's sitemap.
    seen = {}

    class Resp:
        status_code = 200

        def iter_content(self, n):
            yield b"User-agent: *\nDisallow:\n"

        def close(self):
            pass

    def fake_get(url, timeout, stream, headers, ssl_context=None):
        seen.update(headers)
        assert ssl_context is site.tls_context()
        return Resp() if headers.get("Accept") else SimpleNamespace(status_code=403, close=lambda: None)

    monkeypatch.setattr(site, "public_get", fake_get)
    assert site.fetch_text("https://shop.example/robots.txt").startswith("User-agent")
    assert seen["Accept"] == "*/*"


def test_a_blank_hq_country_takes_a_strong_site_signal_and_says_so():
    # gymshark.com never states a headquarters; its prices are in GBP.
    s = site_for(country={"code": "GB", "confidence": 0.8,
                          "evidence": [{"signal": "currency", "country": "GB", "detail": "GBP"}]})
    p = prof.assemble(s, dict(GOOD, hq={"city": "", "region": "", "country_code": ""}), {})
    assert p["hq"]["country_code"] == "GB"
    assert p["hq_country_source"] == "site signals (currency, 80%)"


def test_a_weak_site_signal_does_not_fill_a_blank_country():
    s = site_for(country={"code": "US", "confidence": 0.5, "evidence": []})
    p = prof.assemble(s, dict(GOOD, hq={"city": "", "region": "", "country_code": ""}), {})
    assert p["hq"]["country_code"] == "" and p["hq_country_source"] is None


def test_a_stated_country_is_never_replaced():
    p = prof.assemble(site_for(), dict(GOOD), {})
    assert p["hq"]["country_code"] == "GB" and p["hq_country_source"] == "pages"


def test_page_titles_head_the_text_so_quoted_titles_verify(web):
    web.pages["https://t.example/"] = page("https://t.example/",
        "<html><head><title>Gymshark Official Store - Gym Clothes</title></head><body><p>%s</p></body></html>" % words(200))
    out = site.read_site("https://t.example/")
    text = out["texts"]["https://t.example/"]
    assert text.startswith("TITLE: Gymshark Official Store - Gym Clothes\n")
    assert "DESCRIPTION" not in text      # no meta description on this page
    quote = {"field": "name", "quote": "Gymshark Official Store - Gym Clothes", "url": "https://t.example/"}
    p = dict(GOOD, evidence=[quote])
    assert prof.checks(dict(site_for(), texts=out["texts"], pages=out["pages"]), p) == []


# == sites that refuse this server ========================================================

def test_pages_are_requested_as_ourselves_with_generic_accept_headers(monkeypatch):
    # Three Shopify stores refused "Accept: text/html,..." from Railway and
    # accepted "*/*" with gzip, our own user agent unchanged (2026-10-09).
    seen = {}

    class Resp:
        status_code, url = 200, "https://shop.example/"
        headers = {"Content-Type": "text/html; charset=utf-8"}
        encoding = "utf-8"

        def iter_content(self, n):
            yield b"<html><body><p>hello</p></body></html>"

        def close(self):
            pass

    def fake_get(url, timeout, stream, headers, ssl_context=None):
        seen.update(headers)
        seen["tls"] = ssl_context
        return Resp()

    monkeypatch.setattr(site, "public_get", fake_get)
    assert site.fetch("https://shop.example/")["status"] == "ok"
    # requests' TLS settings: Shopify answered 429 to the stdlib default (Railway, 2026-10-09)
    assert seen.pop("tls") is site.tls_context()
    assert seen == {"User-Agent": site.UA, "Accept": "*/*", "Accept-Encoding": "gzip, deflate"}
    assert "Position2-MarketRadar" in seen["User-Agent"]          # never a disguised browser


def refused(url, code=403):
    return page(url, "", status="blocked", http=code)


def test_a_site_that_refuses_us_is_read_from_the_wayback_machine(web, monkeypatch):
    # clovedental.in: 403 to every request style from Railway.
    home = ('<html><head><title>Clinic Chain</title></head><body><p>%s</p>'
            '<a href="/about-us">About</a><a href="/locations">Our clinics</a>'
            '<a href="/contact">Contact</a><a href="/careers">Careers</a>'
            '<a href="/pricing">Prices</a><a href="/news">News</a></body></html>' % words(300))
    for path in ("/", "/about-us", "/locations", "/contact", "/careers", "/pricing", "/news"):
        web.pages["https://chain.example" + path] = refused("https://chain.example" + path)
        web.pages["https://web.archive.org/web/20260901000000id_/https://chain.example" + path] = page(
            "https://web.archive.org/web/20260901000000id_/https://chain.example" + path,
            home if path == "/" else "<p>%s</p>" % words(80, path.strip("/")))
    monkeypatch.setattr(site, "wayback_latest",
                        lambda url: ("20260901000000", url))
    out = site.read_site("https://chain.example/")
    assert out["status"] == "ok" and out["via_archive"]
    assert out["pages"][0]["via"] == "wayback:20260901000000"
    assert "Wayback Machine's copy of 2026-09-01" in out["pages"][0]["note"]
    assert "refused this server (HTTP 403)" in out["home_notes"][-1]
    assert len(out["pages"]) == 1 + site.MAX_ARCHIVE_PAGES       # a few archive pages only
    assert all(p["via"] for p in out["pages"])
    assert out["signals"]["sitemap"] is None                       # not fetched from a refusing site
    assert "https://chain.example/" in out["texts"]                # keyed by the real address


def test_a_refusing_site_with_no_archive_copy_stays_unread_but_keeps_its_shop_record(web, monkeypatch):
    web.pages["https://shop.example/"] = refused("https://shop.example/", 429)
    monkeypatch.setattr(site, "wayback_latest", lambda url: None)
    web.texts["https://shop.example/meta.json"] = json.dumps({
        "name": "Shop", "city": "Mannheim", "province": "", "country": "DE", "currency": "EUR",
        "myshopify_domain": "shop.myshopify.com", "ships_to_countries": ["DE", "AT", "CH"],
        "published_products_count": 485})
    out = site.read_site("https://shop.example/")
    assert out["status"] == "blocked"
    assert out["pages"][0]["note"].endswith("the Wayback Machine has no readable copy")
    assert out["shopify_store"]["country"] == "DE" and out["shopify_store"]["products"] == 485


def test_a_not_found_site_is_not_sent_to_the_archive(web, monkeypatch):
    monkeypatch.setattr(site, "wayback_latest", lambda url: pytest.fail("asked the archive"))
    assert site.read_site("https://gone.example/")["status"] == "not_found"


def test_shopify_record_is_read_for_shops_and_votes_for_a_country(web):
    web.pages["https://brand.example/"] = page("https://brand.example/",
        '<script src="https://cdn.shopify.com/x.js"></script><p>%s</p>' % words(300))
    web.texts["https://brand.example/meta.json"] = json.dumps({
        "name": "Brand", "city": "Extrema", "province": "Minas Gerais", "country": "BR",
        "currency": "BRL", "myshopify_domain": "brand.myshopify.com", "ships_to_countries": ["BR"]})
    out = site.read_site("https://brand.example/")
    assert out["shopify_store"]["city"] == "Extrema"
    assert out["country"]["code"] == "BR"
    assert {"signal": "shop_registration", "country": "BR",
            "detail": "Shopify store's registered country"} in out["country"]["evidence"]
    facts = prof.facts_for_model(out)
    assert facts["shopify_store_info"]["province"] == "Minas Gerais"
    assert facts["read_from_archive"] is False


def test_non_shopify_json_is_ignored():
    assert site.shopify_store({"name": "x"}) is None and site.shopify_store(None) is None


@pytest.fixture
def today(monkeypatch):
    from datetime import datetime, timezone
    monkeypatch.setattr(site, "_now", lambda: datetime(2026, 10, 9, tzinfo=timezone.utc))


def test_wayback_latest_reads_the_newest_capture(monkeypatch, today):
    monkeypatch.setattr(site, "fetch_text", lambda url, limit=0:
                        '[["timestamp","original"],["20260101000000","https://a.example/"],'
                        '["20260901000000","https://a.example/"]]' if "cdx" in url else None)
    assert site.wayback_latest("https://a.example/") == ("20260901000000", "https://a.example/")
    monkeypatch.setattr(site, "fetch_text", lambda url, limit=0: "[]")
    assert site.wayback_latest("https://a.example/") is None
    monkeypatch.setattr(site, "fetch_text", lambda url, limit=0: None)
    assert site.wayback_latest("https://a.example/") is None


def test_a_quoted_meta_description_or_link_address_verifies():
    # gymshark.com: its tagline is its meta description; its HQ evidence a link.
    d = site.parse_html('<html><head><title>T</title><meta name="description" content="Shop gym clothing for the gym, running &amp; everything in-between."></head><body>x</body></html>')
    text = site.page_head(d) + "Follow us LinkedIn [https://uk.linkedin.com/company/gymshark]"
    s = site_for(texts={"https://acme.example/": text})
    for quote in ("Shop gym clothing for the gym, running & everything in-between.",
                  "uk.linkedin.com/company/gymshark"):
        p = dict(GOOD, evidence=[{"field": "x", "quote": quote, "url": "https://acme.example/"}])
        assert prof.checks(s, p) == [], quote
    p = dict(GOOD, evidence=[{"field": "x", "quote": "Best gym in Leeds", "url": "https://acme.example/"}])
    assert prof.checks(s, p) == ["1 evidence quote(s) do not appear on the page they cite."]


def test_tls_context_verifies_certificates_and_is_built_once():
    import ssl
    ctx = site.tls_context()
    assert ctx is site.tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    assert ctx.cert_store_stats()["x509_ca"] > 50          # certifi's bundle is loaded


def test_a_quote_from_the_sites_structured_data_verifies():
    # drcjagadeesh.com: "688 9th A Main Road" in structured data, "687 ... Rd" in its text.
    orgs = [{"name": "Clinic", "address": {"streetAddress": "688 9th A Main Road, Near Chinmaya Mission Hospital, Indiranagar"}}]
    s = site_for(signals=signals(organizations=orgs),
                 texts={"https://acme.example/": "Visit us at 687 9th A Main Rd near Chinmaya Mission Hospital"})
    quote = {"field": "hq", "quote": "688 9th A Main Road, Near Chinmaya Mission Hospital, Indiranagar",
             "url": "https://acme.example/"}
    assert prof.checks(s, dict(GOOD, evidence=[quote])) == []
    made_up = dict(quote, quote="12 Baker Street, London")
    assert prof.checks(s, dict(GOOD, evidence=[made_up])) == [
        "1 evidence quote(s) do not appear on the page they cite."]


def test_a_local_business_is_located_by_its_street_not_its_city_centre(monkeypatch):
    # kottident.de is in Kreuzberg; "Berlin" geocoded 4 km away (2026-10-09).
    asked = []

    def geo(q, conn=None):
        asked.append(q)
        return None if q.startswith("Nowhere") else {"lat": 52.49, "lon": 13.42, "label": q}
    monkeypatch.setattr(prof, "geocode", geo)
    p = {"archetype": "local_single", "facts": {},
         "hq": {"street": "Kottbusser Damm 1", "postal_code": "10967", "city": "Berlin", "country_code": "DE"}}
    got = prof.locate_hq(p)
    assert got["precision"] == "street" and asked == ["Kottbusser Damm 1, 10967, Berlin, DE"]

    asked.clear()
    structured = {"archetype": "local_single", "hq": {"city": "Berlin", "country_code": "DE"},
                  "facts": {"structured_locations": [{"address": {"streetAddress": "Oranienstr. 2",
                                                                  "postalCode": "10997"}}]}}
    assert prof.locate_hq(structured)["source"] == "geocoded street address (structured data)"
    assert asked == ["Oranienstr. 2, 10997, Berlin, DE"]

    asked.clear()
    unmatched = {"archetype": "local_single", "facts": {},
                 "hq": {"street": "Nowhere 9", "postal_code": "10967", "city": "Berlin", "country_code": "DE"}}
    got = prof.locate_hq(unmatched)
    assert got["precision"] == "postcode" and asked[-1] == "10967, Berlin, DE"


def test_profile_asks_for_the_street_and_postcode():
    assert {"street", "postal_code"} <= set(prof.PROFILE_SCHEMA["properties"]["hq"]["required"])


def test_wayback_falls_back_to_the_availability_api_when_the_search_fails(monkeypatch, today):
    asked = []

    def fetch_json(url):
        asked.append(url)
        return {"archived_snapshots": {"closest": {
            "status": "200", "available": True, "timestamp": "20261004041021",
            "url": "http://web.archive.org/web/20261004041021/https://www.planetfitness.com/"}}}
    monkeypatch.setattr(site, "fetch_text", lambda url, limit=0: None)        # CDX answered 503
    monkeypatch.setattr(site, "fetch_json", fetch_json)
    assert site.wayback_latest("https://planetfitness.com/") == (
        "20261004041021", "https://www.planetfitness.com/")
    assert "url=planetfitness.com&timestamp=20261009" in asked[0]


@pytest.mark.parametrize("snap", [
    {"status": "200", "available": True, "timestamp": "20111018195440",
     "url": "http://web.archive.org/web/20111018195440/http://www.lululemon.com/"},
    {"status": "301", "available": True, "timestamp": "20261001000000",
     "url": "http://web.archive.org/web/20261001000000/http://x.example/"},
    {}])
def test_an_old_redirected_or_missing_capture_is_no_copy(monkeypatch, today, snap):
    monkeypatch.setattr(site, "fetch_text", lambda url, limit=0: None)
    monkeypatch.setattr(site, "fetch_json", lambda url: {"archived_snapshots": {"closest": snap} if snap else {}})
    assert site.wayback_latest("https://lululemon.com/") is None


def test_an_old_capture_from_the_search_is_no_copy_either(monkeypatch, today):
    monkeypatch.setattr(site, "fetch_text", lambda url, limit=0:
                        '[["timestamp","original"],["20111018195440","http://www.lululemon.com/"]]')
    assert site.wayback_latest("https://lululemon.com/") is None


def test_the_www_form_is_asked_when_the_bare_domain_has_only_a_redirect(monkeypatch, today):
    asked = []

    def fetch_json(url):
        asked.append(url)
        if "url=www.planetfitness.com" in url:
            return {"archived_snapshots": {"closest": {
                "status": "200", "available": True, "timestamp": "20261004041021",
                "url": "http://web.archive.org/web/20261004041021/https://www.planetfitness.com/"}}}
        return {"archived_snapshots": {"closest": {"status": "301", "available": True,
                                                   "timestamp": "20261008000000", "url": "x"}}}
    monkeypatch.setattr(site, "fetch_text", lambda url, limit=0: None)
    monkeypatch.setattr(site, "fetch_json", fetch_json)
    assert site.wayback_latest("https://planetfitness.com/")[0] == "20261004041021"
    assert len(asked) == 2


def test_careers_links_include_a_careers_host_of_the_company_and_nothing_foreign():
    links = [("/about", "About us"), ("/careers", "Careers"),
             ("https://careers.aspendental.com/us/en?utm=1", "Join our team"),
             ("https://jobs.otherfirm.com/", "Careers at Other"),
             ("https://www.aspendental.com/dentist/jobs-near-me", "x")]
    out = site.careers_links(links, "https://www.aspendental.com/")
    assert out[0] == "https://careers.aspendental.com/us/en?utm=1"
    assert "https://www.aspendental.com/careers" in out
    assert not any("otherfirm" in u for u in out)


def test_site_root_keeps_country_second_levels():
    assert site.site_root("www.mydentist.co.uk") == "mydentist.co.uk"
    assert site.site_root("careers.aspendental.com") == "aspendental.com"
    assert site.site_root("shop.brand.com.br") == "brand.com.br"
