"""Strategy & Campaign Book: what is read before anything is written (tracker/paid_strategy_research.py), all on fakes."""
import datetime as dt
import json

import pytest

EM = chr(0x2014)

from tests import strategy_fakes as F
from tracker import paid_strategy as ps, paid_strategy_research as rs


def inputs(**kw):
    return ps.clean_inputs(F.inputs(**kw), today=dt.date(2026, 10, 16))


# ── one page ────────────────────────────────────────────────────────────────

def test_read_page_boils_a_page_down_to_what_a_strategist_needs():
    pg = rs.read_page(F.HOME, "https://www.acmedental.com/", 2600)
    assert pg["title"].startswith("Acme Dental") and pg["h1"] == ["Your Austin Family Dentist"] and "Implants, Invisalign and everyday care" in pg["h2"]
    assert pg["rating"] == {"value": 4.9, "count": 312}
    assert "Book an Appointment" in pg["ctas"] and any("Free consultation" in o for o in pg["offers"])
    assert any("555-0100" in p for p in pg["phones"]) and pg["forms"] == [{"fields": ["name", "phone", "email"]}]
    assert "Free consultation for dental implants" in pg["text"] and pg["chars"] > 100
    assert ("/services/dental-implants", "Dental Implants") in pg["links"]


def test_the_text_of_a_page_is_cut_to_the_limit_and_broken_html_is_survived():
    pg = rs.read_page("<html><body><p>" + "word " * 2000 + "</p><div><span>", "https://a.com/", 500)
    assert len(pg["text"]) == 500 and pg["chars"] > 5000


def test_links_are_ranked_by_what_a_page_is_worth_and_same_site_only():
    links = [("/blog/post", "Read"), ("/services/implants", "Implants"), ("/pricing", "Pricing"), ("https://other.com/services", "Other"), ("/login", "Log in"),
             ("/a.pdf", "PDF"), ("mailto:x@y.com", "Mail"), ("#top", "Top"), ("/about", "About"), ("/services/implants", "dup")]
    ranked = rs.rank_links(links, "https://www.acme.com/", "acme.com")
    urls = [u for _s, u in ranked]
    assert urls[:3] == ["https://www.acme.com/services/implants", "https://www.acme.com/pricing", "https://www.acme.com/about"] and urls[-1].endswith("/blog/post")
    assert not any(x in " ".join(urls) for x in ("other.com", "login", ".pdf", "mailto", "#top")) and len(urls) == len(set(urls))


def test_a_deeper_page_ranks_below_a_shallower_one_of_the_same_kind():
    ranked = rs.rank_links([("/services/a/b/c/d/e", "Deep"), ("/services/a", "Shallow")], "https://acme.com/", "acme.com")
    assert [u for _s, u in ranked] == ["https://acme.com/services/a", "https://acme.com/services/a/b/c/d/e"]


def test_one_folder_cannot_take_every_place():
    ranked = [(5, "https://a.com/blog/%d" % i) for i in range(10)] + [(4, "https://a.com/about")]
    picked = rs.pick_pages(ranked, 6)
    assert len([u for u in picked if "/blog/" in u]) == 3 and "https://a.com/about" in picked


# ── the crawl ───────────────────────────────────────────────────────────────

def test_a_site_is_read_from_its_home_page_outwards():
    c = rs.crawl("https://acmedental.com/", fetcher=F.fetcher())
    assert c["ok"] and c["pages"][0]["url"].rstrip("/").endswith("acmedental.com")
    got = {p["url"].split("acmedental.com")[1] for p in c["pages"]}
    assert {"/", "/services/dental-implants", "/services/invisalign", "/financing", "/about", "/contact"} <= got
    assert "/privacy" not in got and "/blog/flossing-tips" in got
    assert c["pages"][-1]["url"].endswith("/blog/flossing-tips") and c["pages"][0]["url"].rstrip("/").endswith("acmedental.com")      # the blog is read, but last
    assert all("links" not in p for p in c["pages"])
    facts = rs.site_facts(c)
    assert facts["pagesRead"] >= 6 and facts["rating"] == {"value": 4.9, "count": 312} and any("512" in p for p in facts["phones"]) and facts["thin"] is False


def test_the_page_count_is_the_total_including_the_home_page_and_the_home_page_is_read_once():
    calls = []
    home = F.HOME.replace("<h1>", '<a href="/">Home</a><a href="https://www.acmedental.com">Home again</a><h1>', 1)
    c = rs.crawl("https://acmedental.com/", fetcher=F.fetcher({"acmedental.com": dict(F.PAGES, **{"/": home})}, calls), max_pages=3)
    assert len(c["pages"]) == 3
    assert len([u for u in calls if u.rstrip("/") in ("https://acmedental.com", "https://www.acmedental.com")]) == 1


def test_robots_txt_is_honoured():
    site = dict(F.PAGES, **{"/robots.txt": "User-agent: *\nDisallow: /services/\n"})
    c = rs.crawl("https://acmedental.com/", fetcher=F.fetcher({"acmedental.com": site}))
    urls = [p["url"] for p in c["pages"]]
    assert c["ok"] and not any("/services/" in u for u in urls) and c["site"]["robots"] is True
    assert any("/services/" in u for u in c["site"]["blocked"])


def test_a_site_that_blocks_everything_says_so():
    site = {"/robots.txt": "User-agent: *\nDisallow: /\n", "/": F.HOME}
    c = rs.crawl("https://acmedental.com/", fetcher=F.fetcher({"acmedental.com": site}))
    assert not c["ok"] and "robots.txt" in c["reason"]


def test_a_sitemap_adds_pages_the_menu_does_not_link():
    site = dict(F.PAGES, **{"/sitemap.xml": "<urlset><url><loc>https://www.acmedental.com/services/whitening</loc></url></urlset>",
                            "/services/whitening": "<html><head><title>Whitening</title></head><body><h1>Whitening</h1></body></html>"})
    c = rs.crawl("https://acmedental.com/", fetcher=F.fetcher({"acmedental.com": site}))
    assert any(p["url"].endswith("/services/whitening") for p in c["pages"]) and c["site"]["sitemapUrls"] == 1


def test_unreadable_sites_are_named_not_raised():
    c = rs.crawl("https://nowhere.example/", fetcher=F.fetcher())
    assert not c["ok"] and "could not be read" in c["reason"] and "nowhere.example" in c["reason"]
    pc = rs.read_website(inputs(website="https://nowhere.example/"), fetcher=F.fetcher())
    assert pc["state"] == "failed" and "could not be read" in pc["note"]
    assert rs.read_website(inputs(website="", account="1234567890"))["state"] == "skipped"


def test_a_page_that_errors_is_left_out_and_counted():
    site = dict(F.PAGES)
    site.pop("/about")
    c = rs.crawl("https://acmedental.com/", fetcher=F.fetcher({"acmedental.com": site}))
    assert c["ok"] and any("answered 404" in e for e in c["site"]["errors"]) and not any(p["url"].endswith("/about") for p in c["pages"])
    assert any(p["url"].endswith("/financing") for p in c["pages"])           # the others were still read


def test_a_site_where_only_the_home_page_could_be_read_is_partial():
    home = '<html><head><title>Acme</title></head><body><h1>Acme</h1><a href="/services/a">A</a><a href="/services/b">B</a><a href="/about">About</a></body></html>'
    pc = rs.read_website(inputs(), fetcher=F.fetcher({"acmedental.com": {"/": home}}))
    assert pc["state"] == "partial" and pc["data"]["facts"]["pagesRead"] == 1 and "answered 404" in " ".join(pc["data"]["site"]["errors"])
    assert rs.read_website(inputs(), fetcher=F.fetcher())["state"] == "ok"


def test_a_site_drawn_by_scripts_is_called_thin():
    html = "<html><head><title>App</title>" + "<script src='/a.js'></script>" * 8 + "</head><body><div id=root></div></body></html>"
    c = rs.crawl("https://acmedental.com/", fetcher=F.fetcher({"acmedental.com": {"/": html}}))
    assert c["site"]["thin"] is True
    assert "scripts" in rs.read_website(inputs(), fetcher=F.fetcher({"acmedental.com": {"/": html}}))["note"]


# ── competitors ─────────────────────────────────────────────────────────────

def test_a_competitor_that_is_a_directory_or_the_client_is_not_a_competitor():
    assert rs.usable_competitor("https://smileaustin.com/", "acmedental.com")
    for bad in ("https://yelp.com/biz/x", "https://www.facebook.com/x", "https://www.acmedental.com/other", "https://m.yelp.com/x", "https://acmedental.com/"):
        assert not rs.usable_competitor(bad, "acmedental.com"), bad
    assert rs.registrable("shop.example.co.uk") == "example.co.uk" and rs.registrable("www.a.com") == "a.com"


def test_competitors_are_found_by_a_web_search_when_none_are_named():
    items, spend, note = rs.find_competitors(inputs(), ask=F.ask_search())
    assert [i["name"] for i in items] == ["Smile Austin"] and note == "" and spend["searches"] == 2
    items, _s, note = rs.find_competitors(inputs(), ask=lambda *a, **k: {"text": "", "error": {"kind": "transport"}})
    assert items == [] and "did not complete" in note
    items, _s, note = rs.find_competitors(inputs(), ask=lambda *a, **k: {"text": "sorry, no"})
    assert items == [] and "could not be read" in note


def test_named_competitors_are_read_without_searching():
    asked = []
    pc = rs.read_competitors(inputs(competitors="smileaustin.com"), fetcher=F.fetcher(), ask=lambda *a, **k: asked.append(1))
    assert not asked and pc["state"] == "ok" and pc["data"]["competitors"][0]["name"] == "smileaustin.com"
    assert len(pc["data"]["competitors"][0]["pages"]) == 3 and pc["data"]["competitors"][0]["via"] == "named"


def test_every_named_competitor_is_read_however_many_are_named_and_the_wrong_kinds_are_not():
    hosts = ["c%d.com" % i for i in range(7)]
    sites = {h: {"/": "<html><head><title>%s</title></head><body><h1>%s</h1></body></html>" % (h, h)} for h in hosts}
    sites.update({"acmedental.com": F.PAGES, "yelp.com": {"/": "<html><title>Yelp</title></html>"}})
    calls = []
    pc = rs.read_competitors(inputs(competitors=hosts), fetcher=F.fetcher(sites, calls), ask=lambda *a, **k: None)
    assert len(pc["data"]["competitors"]) == 7 and all(r["ok"] for r in pc["data"]["competitors"])
    calls.clear()
    pc = rs.read_competitors(inputs(competitors=["yelp.com", "www.acmedental.com", "c1.com"]), fetcher=F.fetcher(sites, calls), ask=lambda *a, **k: None)
    assert [r["name"] for r in pc["data"]["competitors"]] == ["c1.com"] and not any("yelp.com" in u or "acmedental.com" in u for u in calls)


def test_none_named_means_the_agent_finds_them():
    pc = rs.read_competitors(inputs(), fetcher=F.fetcher(), ask=F.ask_search())
    assert pc["state"] == "ok" and pc["data"]["found"] is True and [r["name"] for r in pc["data"]["competitors"]] == ["Smile Austin"] and pc["usd"] > 0


def test_a_name_without_an_address_is_looked_up():
    seen = []

    def ask(system, user, **kw):
        seen.append(system)
        return F.ask_search()(system, user, **kw)
    pc = rs.read_competitors(inputs(competitors="Smile Austin"), fetcher=F.fetcher(), ask=ask)
    assert len(seen) == 1 and "official website" in seen[0] and pc["data"]["competitors"][0]["url"] == "https://smileaustin.com/"


def test_competitors_that_cannot_be_read_are_reported_and_partial_is_partial():
    pc = rs.read_competitors(inputs(competitors="smileaustin.com, gone.example"), fetcher=F.fetcher())
    assert pc["state"] == "partial" and "gone.example" in pc["note"] and any(not r["ok"] for r in pc["data"]["competitors"])
    pc = rs.read_competitors(inputs(competitors="gone.example"), fetcher=F.fetcher())
    assert pc["state"] == "failed"
    pc = rs.read_competitors(inputs(), fetcher=F.fetcher(), ask=F.ask_search(competitors=[]))
    assert pc["state"] == "failed" and "did not find" in pc["note"]


# ── the account ─────────────────────────────────────────────────────────────

def history(**kw):
    return rs.read_history({"id": "1234567890", "name": "Acme", "currency": "USD"}, search=F.fake_search, geo_names=F.fake_geo_names, sqr_read=F.fake_sqr_read,
                           today=dt.date(2026, 10, 16), **kw)


def test_history_reads_what_the_account_spent_and_what_converted():
    pc = history()
    d = pc["data"]
    assert pc["state"] == "ok" and d["window"] == {"start": "2026-07-18", "end": "2026-10-15"}
    assert d["totals"]["cost"] == 1300.0 and d["totals"]["conv"] == 12.0 and d["totals"]["cpa"] == 108.33 and d["totals"]["cvr"] == 4.1 and d["totals"]["cpc"] == 4.48
    assert "roas" not in d["totals"]
    c = d["campaigns"][0]
    assert c["name"] == "P2_Search_Old_20240101" and c["type"] == "Search" and c["bid"] == "Maximize Conversions" and c["daily"] == 50.0
    assert d["keywordsBest"][0]["text"] == "dental implants austin" and d["keywordsWasted"][0]["text"] == "dentist"
    assert d["terms"]["converting"][0]["term"] == "dental implants austin tx" and d["terms"]["wasteful"][0]["term"] == "dentist jobs austin"
    assert d["negatives"] == {"campaign": 3, "lists": 1} and d["keywordCount"] == 2
    assert [x["device"] for x in d["devices"]] == ["Mobile", "Desktop"] and d["regions"][0]["name"] == "Texas,United States"
    assert d["actions"][0]["primary"] is True and d["ads"][0]["headlines"] == ["Austin Dentist", "Book Today"]


def test_a_query_that_fails_is_named_and_the_rest_is_kept():
    def search(cid, gaql):
        if "FROM keyword_view" in gaql:
            raise RuntimeError("quota")
        return F.fake_search(cid, gaql)
    pc = rs.read_history({"id": "1", "currency": "USD"}, search=search, sqr_read=F.fake_sqr_read, today=dt.date(2026, 10, 16))
    assert pc["state"] == "partial" and "Keywords could not be read" in pc["note"] and pc["data"]["campaigns"] and pc["data"]["keywordsBest"] == []


def test_an_account_whose_campaigns_cannot_be_read_fails():
    def search(cid, gaql):
        raise RuntimeError("down")
    pc = rs.read_history({"id": "1", "currency": "USD"}, search=search, today=dt.date(2026, 10, 16))
    assert pc["state"] == "failed" and "campaigns of this account could not be read" in pc["note"]


def test_history_is_cached_by_the_caller():
    seen = []

    def cached(key, ttl, make):
        seen.append(key)
        return make()
    history(cached=cached)
    assert len(seen) == 6 and all(k.startswith("strategy:") and k.endswith(":2026-10-15") for k in seen)


def kw_row(text, conv, cost, clicks=10, match="EXACT", camp="C"):
    return {"campaign": {"name": camp}, "adGroup": {"name": "G"}, "metrics": {"clicks": str(clicks), "conversions": conv, "costMicros": str(int(cost * 1e6)), "impressions": "100"},
            "adGroupCriterion": {"keyword": {"matchType": match, "text": text}}}


def region_row(res, clicks, cost, conv):
    return {"segments": {"geoTargetRegion": res}, "metrics": {"clicks": str(clicks), "costMicros": str(int(cost * 1e6)), "conversions": conv}}


def custom_search(**rows):
    def search(cid, gaql):
        for key, marker in (("keywords", "FROM keyword_view"), ("regions", "user_location_view"), ("campaigns", "FROM campaign WHERE campaign.status")):
            if marker in gaql and key in rows:
                return rows[key]
        return F.fake_search(cid, gaql)
    return search


def test_the_best_keywords_come_first_the_wasted_ones_are_the_costliest_and_a_free_one_is_not_wasted():
    rows = [kw_row("small win", 2, 50), kw_row("big win", 9, 400), kw_row("cheap win", 2, 20), kw_row("zero cost", 0, 0), kw_row("small waste", 0, 30), kw_row("big waste", 0, 300)]
    d = rs.read_history({"id": "1", "currency": "USD"}, search=custom_search(keywords=rows), today=dt.date(2026, 10, 16))["data"]
    assert [k["text"] for k in d["keywordsBest"]] == ["big win", "cheap win", "small win"]            # most conversions first, the cheaper first on a tie
    assert [k["text"] for k in d["keywordsWasted"]] == ["big waste", "small waste"]


def sqr_with(terms):
    return lambda store, account, start, end: {"terms": terms, "keywords": [], "negatives": [], "lists": [], "warnings": []}


def test_search_terms_that_converted_are_the_ones_with_conversions_and_the_wasted_ones_cost_the_most():
    terms = [{"t": "no conv", "c": "C", "k": 5, "x": 10.0, "v": 0.0}, {"t": "one conv", "c": "C", "k": 5, "x": 20.0, "v": 1.0}, {"t": "three conv", "c": "C", "k": 5, "x": 20.0, "v": 3.0},
             {"t": "costly waste", "c": "C", "k": 5, "x": 90.0, "v": 0.0}, {"t": "free", "c": "C", "k": 5, "x": 0.0, "v": 0.0}]
    d = rs.read_history({"id": "1", "currency": "USD"}, search=F.fake_search, sqr_read=sqr_with(terms), today=dt.date(2026, 10, 16))["data"]["terms"]
    assert [t["term"] for t in d["converting"]] == ["three conv", "one conv"] and [t["term"] for t in d["wasteful"]] == ["costly waste", "no conv"] and d["total"] == 5


def test_places_are_added_up_and_sorted_by_what_they_cost():
    rows = [region_row("geoTargetConstants/1", 10, 100, 1), region_row("geoTargetConstants/2", 10, 900, 0), region_row("geoTargetConstants/1", 5, 250, 2), region_row("geoTargetConstants/3", 1, 500, 1)]
    names = lambda res: {r: {"name": "Place " + r.split("/")[1], "canonical": ""} for r in res}
    d = rs.read_history({"id": "1", "currency": "USD"}, search=custom_search(regions=rows), geo_names=names, today=dt.date(2026, 10, 16))["data"]
    assert [(r["name"], r["clicks"], r["cost"], r["conv"], r["cpa"]) for r in d["regions"]] == [("Place 2", 10, 900.0, 0.0, None), ("Place 3", 1, 500.0, 1.0, 500.0), ("Place 1", 15, 350.0, 3.0, 116.67)]


def test_a_campaign_with_no_conversions_has_no_cost_per_conversion_not_an_error():
    camp = [{"campaign": {"id": "9", "name": "Dud", "status": "ENABLED", "advertisingChannelType": "SEARCH", "biddingStrategyType": "MANUAL_CPC"}, "campaignBudget": {"amountMicros": "10000000"},
             "metrics": {"impressions": "0", "clicks": "0", "costMicros": "0", "conversions": 0, "conversionsValue": 0}}]
    pc = rs.read_history({"id": "1", "currency": "USD"}, search=custom_search(campaigns=camp), today=dt.date(2026, 10, 16))
    d = pc["data"]
    assert d["campaigns"][0]["cpa"] is None and d["campaigns"][0]["cvr"] is None and d["totals"]["cpa"] is None and d["totals"]["cpc"] is None and "No spend" in pc["note"]


# ── Keyword Planner ─────────────────────────────────────────────────────────

def test_places_are_split_on_semicolons_only():
    assert rs.split_places("Austin, TX; Round Rock, TX\nCedar Park") == ["Austin, TX", "Round Rock, TX", "Cedar Park"] and rs.split_places("") == []


def test_places_are_resolved_and_an_unknown_one_is_left_out():
    def post(path, body):
        if body["locationNames"]["names"] == ["Nowhere"]:
            raise RuntimeError("no")
        return F.fake_post(path, body)
    got = rs.resolve_places(["Austin, TX", "Nowhere"], post=post)
    assert got == [{"name": "Austin, TX", "id": "1026201", "canonical": "Austin,Texas,United States", "type": "City"}]


def test_keyword_ideas_come_with_volume_competition_and_bids():
    site = rs.read_website(inputs(), fetcher=F.fetcher())
    pc = rs.read_keywords(inputs(), post=F.fake_post, planner_account="1234567890", website_piece=site, history_piece=history(), currency="USD")
    d = pc["data"]
    assert pc["state"] == "ok" and d["geo"] == ["geoTargetConstants/1026201"] and d["seeds"][0] == "dental implants"
    top = d["ideas"][0]
    assert top["text"] == "cost of dental implants" and top["avg"] == 8100 and top["competition"] == "Medium" and top["lowBid"] == 3.0 and top["highBid"] == 9.0 and top["peak"] == "January"
    assert all(i["from"] in ("your terms", "site", "competitor") for i in d["ideas"]) and d["total"] == 3


def test_planner_asks_with_the_seeds_the_site_and_each_competitor_and_never_with_adult_words():
    sent = []

    def post(path, body):
        sent.append((path, body))
        return F.fake_post(path, body)
    site = rs.read_website(inputs(), fetcher=F.fetcher())
    comp = rs.read_competitors(inputs(competitors="smileaustin.com"), fetcher=F.fetcher())
    rs.read_keywords(inputs(), post=post, planner_account="1234567890", website_piece=site, competitor_piece=comp, currency="USD")
    kinds = [list(b)[-1] if "keywordSeed" not in b and "urlSeed" not in b else ("keywordSeed" if "keywordSeed" in b else "urlSeed") for p, b in sent if "generateKeywordIdeas" in p]
    assert kinds.count("keywordSeed") == 1 and kinds.count("urlSeed") == 2
    assert all(b["includeAdultKeywords"] is False and b["keywordPlanNetwork"] == "GOOGLE_SEARCH" for p, b in sent if "generateKeywordIdeas" in p)
    assert all("/customers/1234567890:generateKeywordIdeas" == p for p, b in sent if "generateKeywordIdeas" in p)


def test_with_no_place_the_country_comes_from_the_currency_and_with_no_seed_there_is_nothing_to_ask():
    sent = []
    pc = rs.read_keywords(inputs(locations="", services="dental implants"), post=lambda p, b: sent.append(b) or F.fake_post(p, b), planner_account="1", currency="CAD")
    assert sent[0]["geoTargetConstants"] == ["geoTargetConstants/2124"] and pc["state"] == "ok"
    none = rs.read_keywords(inputs(services="", industry="", website="", account="", brief="b" * 60), post=F.fake_post, planner_account="1")
    assert none["state"] == "skipped" and "nothing to start" in none["note"]
    assert rs.read_keywords(inputs(), post=F.fake_post, planner_account=None)["state"] == "skipped"


def test_planner_that_fails_says_so_and_a_partial_answer_is_partial():
    def boom(path, body):
        raise RuntimeError("quota")
    pc = rs.read_keywords(inputs(locations=""), post=boom, planner_account="1")
    assert pc["state"] == "failed" and "quota" in pc["note"]
    n = []

    def half(path, body):
        n.append(1)
        if len(n) == 2:
            raise RuntimeError("limit")
        return F.fake_post(path, body)
    site = rs.read_website(inputs(), fetcher=F.fetcher())
    pc = rs.read_keywords(inputs(locations=""), post=half, planner_account="1", website_piece=site, currency="USD")
    assert pc["state"] == "partial" and "limit" in pc["note"] and pc["data"]["ideas"]


# ── seasonality ─────────────────────────────────────────────────────────────

def test_seasonality_is_the_accounts_own_months():
    pc = rs.read_seasonality({"id": "1", "currency": "USD"}, report=F.fake_season)
    assert pc["state"] == "ok" and len(pc["data"]["months"]) == 12 and pc["data"]["months"][0] == {"month": "January", "conv": 100, "cost": 100}
    assert rs.read_seasonality({"id": "1"}, report=lambda a, m: {"empty": True})["state"] == "partial"
    short = rs.read_seasonality({"id": "1"}, report=lambda a, m: {"empty": False, "coverage": {"yearly": False, "monthsInARow": 5, "note": "Needs 13 months."}})
    assert short["state"] == "partial" and short["data"]["months"] == [] and "13 months" in short["note"]

    def boom(a, m):
        raise RuntimeError("no data")
    assert rs.read_seasonality({"id": "1"}, report=boom)["state"] == "failed"


# ── social ──────────────────────────────────────────────────────────────────

def done_run(**kw):
    return dict({"email": "ana@position2.com", "company_name": "Acme Dental", "company_url": "https://www.acmedental.com/", "status": "done", "updated_at": "2026-10-10T09:00:00+00:00",
                 "created_at": "2026-10-10T09:00:00+00:00", "synthesis": F.SYNTHESIS}, **kw)


NOW = dt.datetime(2026, 10, 16, tzinfo=dt.timezone.utc)


def test_a_recent_finished_run_for_the_company_is_reused():
    sci = F.FakeSci(runs={5: done_run(), 6: done_run(company_name="Other", company_url="https://other.com/", updated_at="2026-10-15T00:00:00+00:00"),
                          7: done_run(status="running", updated_at="2026-10-15T00:00:00+00:00"), 8: done_run(updated_at="2026-07-01T00:00:00+00:00"),
                          9: done_run(email="bob@position2.com", updated_at="2026-10-15T00:00:00+00:00")})
    assert rs.find_social_run("ana@position2.com", inputs(), store=sci, now=NOW)["id"] == 5
    assert rs.find_social_run("ana@position2.com", inputs(name="Different", website="https://zzz.com/"), store=sci, now=NOW) is None
    assert rs.find_social_run("ana@position2.com", inputs(name="Different", website="acmedental.com"), store=sci, now=NOW)["id"] == 5      # same website
    assert rs.find_social_run("cat@position2.com", inputs(), store=sci, now=NOW) is None


def test_a_run_older_than_forty_five_days_is_not_reused():
    sci = F.FakeSci(runs={8: done_run(updated_at="2026-08-30T00:00:00+00:00")})
    assert rs.find_social_run("ana@position2.com", inputs(), store=sci, now=NOW) is None
    assert rs.find_social_run("ana@position2.com", inputs(), store=F.FakeSci(runs={8: done_run(updated_at="2026-09-02T00:00:00+00:00")}), now=NOW)["id"] == 8


def test_the_newest_of_several_runs_wins():
    sci = F.FakeSci(runs={5: done_run(updated_at="2026-10-01T00:00:00+00:00"), 6: done_run(updated_at="2026-10-12T00:00:00+00:00")})
    assert rs.find_social_run("ana@position2.com", inputs(), store=sci, now=NOW)["id"] == 6


def test_a_new_run_is_started_for_the_user_when_there_is_none():
    sci = F.FakeSci(runs={}, finish=False)
    spawned = []
    run_id = rs.start_social("ana@position2.com", inputs(), store=sci, pipeline=sci, spawn=lambda fn: spawned.append(fn))
    assert run_id == 101 and sci.runs[101]["email"] == "ana@position2.com" and sci.runs[101]["company_name"] == "Acme Dental" and len(spawned) == 1
    spawned[0]()
    assert sci.started == [101]


def test_the_social_findings_are_read_from_a_finished_run():
    sci = F.FakeSci(runs={5: done_run()})
    pc = rs.read_social("ana@position2.com", 5, store=sci, reused=True)
    d = pc["data"]
    assert pc["state"] == "ok" and d["reused"] and "Used the finished" in pc["note"]
    assert d["platforms"][0]["platform"] == "instagram" and d["platforms"][0]["summary"] == ["Short reels of the team", "Before and after smiles"]
    assert d["crossPlatform"]["messaging"] == ["Lead with the dentist, not the offer"]
    assert d["topPosts"][0]["engagement"] == 129 and d["topPosts"][0]["caption"].startswith("Meet Dr. Lopez")


def test_a_run_that_is_still_going_is_waited_for_and_then_given_up_on():
    sci = F.FakeSci(runs={5: done_run(status="running")})
    t = [0]
    pc = rs.read_social("ana@position2.com", 5, store=sci, reused=False, wait_s=60, poll_s=20, sleep=lambda s: t.__setitem__(0, t[0] + s), clock=lambda: t[0])
    assert pc["state"] == "partial" and pc["data"]["pending"] is True and "still working" in pc["note"] and t[0] >= 60
    # it finishes while we wait
    sci2 = F.FakeSci(runs={5: done_run(status="running")})

    def finish(s):
        sci2.runs[5]["status"] = "done"
    pc = rs.read_social("ana@position2.com", 5, store=sci2, reused=False, wait_s=600, poll_s=1, sleep=finish, clock=lambda: 0)
    assert pc["state"] == "ok" and "Ran and used" in pc["note"]


def test_failed_missing_and_empty_runs_are_said_in_words():
    sci = F.FakeSci(runs={5: done_run(status="error", error="Analysis could not be completed.")})
    assert rs.read_social("ana@position2.com", 5, store=sci, reused=False)["state"] == "failed"
    assert rs.read_social("ana@position2.com", 99, store=sci, reused=False)["state"] == "failed"
    assert rs.read_social("bob@position2.com", 5, store=sci, reused=False)["state"] == "failed"          # someone else's run
    empty = F.FakeSci(runs={5: done_run(synthesis={})})
    empty.get_platform_runs = lambda run_id: [{"platform": "tiktok", "status": "no_presence", "post_count": 0}]
    empty.get_posts = lambda run_id: []
    assert rs.read_social("ana@position2.com", 5, store=empty, reused=True)["state"] == "partial"


# ── the market ──────────────────────────────────────────────────────────────

def test_the_market_search_is_kept_to_what_it_found_and_its_sources():
    pc = rs.read_market(inputs(), ask=F.ask_search())
    d = pc["data"]
    assert pc["state"] == "ok" and d["demand"] == ["Implant searches peak in January"] and d["adPolicy"] == [{"rule": "Healthcare ads must not promise results", "appliesTo": "All dental ads"}]
    assert d["sources"] == ["https://example.com/report", "https://example.com/more"] and pc["usd"] > 0


def test_a_market_search_that_fails_or_finds_nothing_is_failed():
    assert rs.read_market(inputs(), ask=lambda *a, **k: {"text": "", "error": {"kind": "max_uses_exceeded"}})["state"] == "failed"
    assert rs.read_market(inputs(), ask=lambda *a, **k: {"text": "prose only"})["state"] == "failed"
    empty = json.dumps({"demand": [], "decision_factors": [], "trends": [], "ad_policy": [], "cost_signals": [], "sources": []})
    assert rs.read_market(inputs(), ask=lambda *a, **k: {"text": empty})["state"] == "failed"


def test_em_dashes_in_what_a_search_wrote_are_removed():
    text = json.dumps({"demand": ["Peaks in winter " + EM + " January most"], "decision_factors": [], "trends": [], "ad_policy": [], "cost_signals": [], "sources": []})
    assert rs.read_market(inputs(), ask=lambda *a, **k: {"text": text})["data"]["demand"] == ["Peaks in winter, January most"]


# ── for the page and the model ──────────────────────────────────────────────

def test_public_drops_the_words_on_the_pages_and_nothing_else():
    research = {"website": rs.read_website(inputs(), fetcher=F.fetcher()), "competitors": rs.read_competitors(inputs(competitors="smileaustin.com"), fetcher=F.fetcher()),
                "keywords": rs.read_keywords(inputs(locations=""), post=F.fake_post, planner_account="1", currency="USD"),
                "history": history(), "market": rs.read_market(inputs(), ask=F.ask_search())}
    assert "text" in research["website"]["data"]["pages"][0] and "text" in research["competitors"]["data"]["competitors"][0]["pages"][0]
    pub = rs.public(research)
    assert "text" not in pub["website"]["data"]["pages"][0] and "text" not in pub["competitors"]["data"]["competitors"][0]["pages"][0] and "spend" not in pub["market"]
    assert pub["website"]["data"]["pages"][0]["title"].startswith("Acme Dental") and pub["competitors"]["data"]["competitors"][0]["pages"][0]["url"]
    assert pub["keywords"]["data"]["ideas"][0]["text"] == "cost of dental implants"                              # a keyword's own text is not page text
    assert pub["history"]["data"]["keywordsBest"][0]["text"] == "dental implants austin" and pub["history"]["data"]["terms"]["converting"][0]["term"]
    assert research["website"]["data"]["pages"][0]["text"] and rs.public({"x": 5}) == {}                            # the research itself is untouched


def test_the_corpus_is_what_a_quote_is_checked_against():
    research = {"website": rs.read_website(inputs(), fetcher=F.fetcher())}
    corpus = rs.corpus(research)
    assert "Free consultation for dental implants" in corpus and "rated 4.9 stars from 312 reviews" in corpus and "+15125550100" in corpus and rs.corpus({}) == ""


def test_a_word_is_matched_whole_not_inside_another_word():
    ranked = rs.rank_links([("/corporate-governance", "Governance"), ("/rates", "Rates"), ("/accelerate", "Go faster"), ("/the-planning-guide", "Guide")], "https://acme.com/", "acme.com")
    scores = {u.rsplit("/", 1)[1]: s for s, u in ranked}
    assert scores["rates"] == 9 and scores["the-planning-guide"] == 9 and scores["corporate-governance"] == 2 and scores["accelerate"] == 2


# ── what the review of the first version found ──────────────────────────────

def test_a_page_with_no_charset_is_read_as_utf_8_not_as_latin_1():
    page = "<html><head><title>Café Dentaire à Montréal</title></head><body><h1>Soins détaillés</h1></body></html>"
    site = {"/": F.Resp("https://acmedental.com/", page, ctype="text/html", raw=page.encode("utf-8"))}

    def fetch(url, **kw):
        return F.fetcher({"acmedental.com": {}})(url, **kw) if "robots" in url or "sitemap" in url else site["/"]
    pc = rs.read_website(inputs(), fetcher=fetch)
    assert pc["data"]["pages"][0]["title"] == "Café Dentaire à Montréal" and pc["data"]["pages"][0]["h1"] == ["Soins détaillés"]


def test_decoding_uses_the_named_charset_then_the_page_then_utf_8_and_survives_a_charset_nobody_knows():
    latin = "Café".encode("latin-1")
    assert rs.decode(latin, "text/html; charset=ISO-8859-1") == "Café" and rs.decode("Café".encode("utf-8"), "text/html") == "Café"
    assert rs.decode(b'<meta charset="windows-1252"><p>caf\xe9</p>', "text/html") == '<meta charset="windows-1252"><p>café</p>'
    assert rs.decode("Café".encode("utf-8"), "text/html; charset=nonsense-9") == "Café" and rs.decode(b"\xff\xfe broken", "") .endswith(" broken")


def test_a_site_that_drips_out_its_answer_is_given_up_on(monkeypatch):
    import time as _t
    monkeypatch.setattr(rs, "PAGE_DEADLINE_S", 0.2)

    def slow(url, **kw):
        _t.sleep(1.5)
        return F.fetcher()(url, **kw)
    t0 = _t.monotonic()
    pc = rs.read_website(inputs(), fetcher=slow)
    assert pc["state"] == "failed" and "took longer than" in pc["note"] and _t.monotonic() - t0 < 1.4


def test_a_site_is_not_crawled_for_ever(monkeypatch):
    monkeypatch.setattr(rs, "CRAWL_DEADLINE_S", -1)
    c = rs.crawl("https://acmedental.com/", fetcher=F.fetcher())
    assert c["ok"] and len(c["pages"]) == 1 and any("took too long" in e for e in c["site"]["errors"])


def test_a_robots_rule_written_for_us_wins_over_the_one_for_everybody():
    shut = dict(F.PAGES, **{"/robots.txt": "User-agent: Position2-Strategy\nDisallow: /\n\nUser-agent: *\nAllow: /\n"})
    assert not rs.crawl("https://acmedental.com/", fetcher=F.fetcher({"acmedental.com": shut}))["ok"]
    open_ = dict(F.PAGES, **{"/robots.txt": "User-agent: Position2-Strategy\nAllow: /\n\nUser-agent: *\nDisallow: /\n"})
    assert rs.crawl("https://acmedental.com/", fetcher=F.fetcher({"acmedental.com": open_}))["ok"]
    star = dict(F.PAGES, **{"/robots.txt": "User-agent: *\nDisallow: /services/\n"})
    assert not any("/services/" in p["url"] for p in rs.crawl("https://acmedental.com/", fetcher=F.fetcher({"acmedental.com": star}))["pages"])


def test_two_firms_with_one_name_are_told_apart_by_their_websites():
    other = done_run(company_url="https://acmedental.co.uk/")
    sci = F.FakeSci(runs={5: other})
    assert rs.find_social_run("ana@position2.com", inputs(), store=sci, now=NOW) is None                  # same name, a different website: not this client
    nameless = F.FakeSci(runs={5: done_run(company_url=None)})
    assert rs.find_social_run("ana@position2.com", inputs(), store=nameless, now=NOW)["id"] == 5           # no website on the run: the name is all there is
    assert rs.find_social_run("ana@position2.com", inputs(website=""), store=F.FakeSci(runs={5: other}), now=NOW)["id"] == 5     # no website given: the name is all there is


def test_every_web_search_is_given_a_deadline():
    seen = []

    def spy(system, user, **kw):
        seen.append(kw.get("deadline"))
        return F.ask_search()(system, user, **kw)
    rs.find_competitors(inputs(), ask=spy)
    rs.find_named(inputs(), ["Smile Austin"], ask=spy)
    rs.read_market(inputs(), ask=spy)
    assert seen == [rs.SEARCH_DEADLINE_S] * 3 and rs.SEARCH_DEADLINE_S == 420.0


def test_only_short_link_texts_that_ask_for_the_sale_count_as_calls_to_action():
    html = ('<html><body><a href="/a">Book Now</a><a href="/b">' + "Get " + "started with our comprehensive range of services today and tomorrow" + '</a><a href="/c">x</a><a href="/d">About us</a></body></html>')
    assert rs.read_page(html, "https://a.com/", 500)["ctas"] == ["Book Now"]


def test_a_page_the_home_page_links_back_to_is_not_read_twice():
    home = '<html><head><title>Acme</title></head><body><h1>Acme</h1><a href="/">Home</a><a href="https://acmedental.com/">Home again</a><a href="/about">About</a></body></html>'
    calls = []
    c = rs.crawl("https://acmedental.com/", fetcher=F.fetcher({"acmedental.com": {"/": home, "/about": F.PAGES["/about"]}}, calls))
    assert [p["url"] for p in c["pages"]] == ["https://acmedental.com/", "https://acmedental.com/about"]
    assert len([u for u in calls if u.rstrip("/") == "https://acmedental.com"]) == 1


def test_a_device_seen_in_several_campaigns_is_added_up():
    rows = [{"segments": {"device": "MOBILE"}, "metrics": {"clicks": "10", "costMicros": "100000000", "conversions": 1.0}, "campaign": {}},
            {"segments": {"device": "MOBILE"}, "metrics": {"clicks": "20", "costMicros": "300000000", "conversions": 3.0}, "campaign": {}},
            {"segments": {"device": "DESKTOP"}, "metrics": {"clicks": "5", "costMicros": "50000000", "conversions": 0}, "campaign": {}}]

    def search(cid, gaql):
        return rows if "segments.device" in gaql else F.fake_search(cid, gaql)
    d = rs.read_history({"id": "1", "currency": "USD"}, search=search, today=dt.date(2026, 10, 16))["data"]
    assert [(x["device"], x["clicks"], x["cost"], x["conv"], x["cpa"]) for x in d["devices"]] == [("Mobile", 30, 400.0, 4.0, 100.0), ("Desktop", 5, 50.0, 0.0, None)]


def test_a_source_that_is_not_an_address_is_left_out_of_the_market_findings():
    text = json.dumps({"demand": ["Peaks in January"], "decision_factors": [], "trends": [], "ad_policy": [], "cost_signals": [], "sources": ["not an address", "https://ok.example/a", "https://ok.example/a", "javascript:x"]})
    pc = rs.read_market(inputs(), ask=lambda *a, **k: {"text": text, "result_urls": ["ftp://bad.example/x", "https://more.example/y"]})
    assert pc["data"]["sources"] == ["https://ok.example/a", "https://more.example/y"]


def test_a_market_reply_with_a_string_where_a_list_belongs_is_one_item_not_a_failed_card():
    import json
    body = {"demand": "Implant searches peak in January", "decision_factors": ["Price"], "trends": None, "ad_policy": {"rule": "x"}, "cost_signals": [], "sources": "https://example.com/report"}
    pc = rs.read_market(F.clean(), ask=lambda system, user, **kw: {"text": json.dumps(body), "usage": {}, "search_count": 1})
    assert pc["state"] == "ok" and pc["data"]["demand"] == ["Implant searches peak in January"] and pc["data"]["sources"] == ["https://example.com/report"] and pc["data"]["adPolicy"] == []


def test_places_are_looked_up_worldwide_not_only_in_the_united_states():
    """A Canadian or British client's "Toronto; Leeds" was sent with countryCode US, so the market, volumes and bids were the wrong country's."""
    sent = []

    def post(path, body):
        sent.append((path, body))
        return {"geoTargetConstantSuggestions": [{"geoTargetConstant": {"id": "1", "canonicalName": "X", "targetType": "City"}}]}
    rs.resolve_places(["Toronto", "Leeds"], post=post)
    assert [b for _p, b in sent] == [{"locale": "en", "locationNames": {"names": ["Toronto"]}}, {"locale": "en", "locationNames": {"names": ["Leeds"]}}]
    sent.clear()
    rs.resolve_places(["Austin"], post=post, country_code="US")
    assert sent[0][1]["countryCode"] == "US"
