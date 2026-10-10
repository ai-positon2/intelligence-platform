"""Market Radar, Phase 4: the local radar and new entrants, no network.

The map, the network, the model and the search are all fakes shaped like
the live answers measured on 2026-10-09 (Overture releases around Austin
and Cedar Park, RDAP records, Google News RSS, the whoisds zip).
"""
import io
import json
import os
import sys
import zipfile
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracker import market_radar_radar as R  # noqa: E402
from tracker import market_radar_news as news  # noqa: E402
from test_market_radar_detectors import ok, miss  # noqa: E402

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def place(name, lat=30.50, lon=-97.82, *, pid=None, site=None, cat="restaurant", status=None,
          source="meta", dist=1.0):
    return {"id": pid or name, "name": name, "lat": lat, "lon": lon, "websites": [site] if site else [],
            "status": status, "category": cat, "taxonomy": cat, "distance_km": dist, "street": "1 Main",
            "city": "Cedar Park", "source": source, "source_updated": "2026-09-14T00:00:00Z"}


# == the map, month on month =========================================================

def test_new_listings_ignore_the_same_place_under_a_new_id_name_or_page():
    prev = [place("Old Grill", pid="a", site="https://oldgrill.com/"),
            place("Taco Hut", pid="b", lat=30.5000),
            place("Chipotle Mexican Grill", pid="c", site="https://locations.chipotle.com/tx/austin/1-a")]
    cur = [place("Old Grill", pid="a2", site="https://www.oldgrill.com"),        # same page, new id
           place("Taco Hut", pid="b2", lat=30.5010),                              # same name, 110 m
           place("Taco Hut", pid="b3", lat=30.5300),                              # same name, 3 km
           place("Coming Soon - Chipotle Mexican Grill", pid="d",
                 site="https://locations.chipotle.com/tx/cedar-park/1521-juliette-way"),
           place("Closed Diner", pid="e", status="permanently_closed"),
           place("Me", pid="f", site="https://client.com/"),
           place("Still Here", pid="a")]
    names = [p["name"] for p in R.new_listings(cur, prev, own_domain="client.com")]
    assert names == ["Taco Hut", "Coming Soon - Chipotle Mexican Grill"]


class Places:
    def __init__(self, cur, prev, releases=("2026-08-19.0", "2026-09-23.0", "2026-09-23.1")):
        self.cur, self.prev, self.rel = cur, prev, list(releases)

    def latest_release(self):
        return self.rel[-1]

    def releases(self):
        return self.rel

    def previous_release(self, current, all_r):
        from tracker.market_radar_places import previous_release
        return previous_release(current, all_r)

    def query(self, lat, lon, r, *, categories=(), terms=(), release=None):
        return list(self.cur if release == self.rel[-1] else self.prev)


def test_the_previous_release_is_from_an_earlier_month():
    from tracker.market_radar_places import previous_release
    assert previous_release("2026-09-23.1", ["2026-08-19.0", "2026-09-23.0", "2026-09-23.1"]) == "2026-08-19.0"
    assert previous_release("2026-09-23.1", ["2026-09-23.0", "2026-09-23.1"]) is None


class Cache(dict):
    def get(self, k):
        return dict.get(self, k)

    def put(self, k, v):
        self[k] = v


def rdap(day):
    return ok(json.dumps({"events": [{"eventAction": "registration", "eventDate": day + "T00:00:00Z"},
                                     {"eventAction": "last changed", "eventDate": "2026-09-30T00:00:00Z"}]}))


def gnews(*titles):
    return ok("<rss><channel>" + "".join(
        "<item><title>%s - Paper</title><link>https://news.google.com/rss/articles/%d</link>"
        "<pubDate>Thu, 25 Sep 2026 07:00:00 GMT</pubDate><source url=\"https://paper.com\">Paper</source>"
        "</item>" % (t, i) for i, t in enumerate(titles)) + "</channel></rss>")


def net(pages):
    asked = []

    def get(url, **kw):
        asked.append(url)
        for k, v in pages.items():
            if url.startswith(k):
                return v
        return miss()
    get.asked = asked
    return get


def scan(cur, prev, pages, *, fetch=None, **kw):
    args = dict(point={"lat": 30.5, "lon": -97.82}, categories=["restaurant"], terms=["restaurant"],
                radius_km=5, city="Cedar Park", country="US", lang="en", own_domain="client.com",
                places=Places(cur, prev), get=net(pages), cache=Cache(),
                fetch=fetch or (lambda u: {"status": "error"}),
                breaker=news.Breaker(gap=0, sleep=lambda s: None), now=NOW)
    args.update(kw)
    return R.scan_local(**args)


def test_only_candidates_with_a_sign_of_being_new_are_reported():
    cur = [place("Coming Soon - Chipotle Mexican Grill", source="AllThePlaces", dist=2.2,
                 site="https://locations.chipotle.com/tx/cedar-park/1521"),
           place("Tutta Bella", site="https://tuttabellacedarparktx.com/", dist=2.4),
           place("Smokey Tacos", site="https://smokeytacos.com/", dist=3.4),
           place("Old Steakhouse", site="https://oldsteak.com/", dist=1.0),
           place("Luigi's Pizza", dist=4.0), place("Cedar Park", dist=4.5)]
    pages = {"https://rdap.org/domain/tuttabellacedarparktx.com": rdap("2026-09-03"),
             "https://rdap.org/domain/smokeytacos.com": rdap("2026-08-03"),
             "https://rdap.org/domain/oldsteak.com": rdap("2007-03-05"),
             "https://rdap.org/domain/locations.chipotle.com": miss(),
             "https://news.google.com/": gnews("Luigi's Pizza opens in Cedar Park",
                                              "Highway opening in Cedar Park")}
    # (a place named just "Cedar Park" must not make the highway headline count)
    sites = {"https://tuttabellacedarparktx.com/": "Tutta Bella NOW OPEN Grand Opening Announcement",
             "https://locations.chipotle.com/tx/cedar-park/1521": "Chipotle - Coming Soon 1521 Juliette Way"}
    out = scan(cur, [], pages, fetch=lambda u: {"status": "ok", "text": sites.get(u, "Welcome")})
    got = {f["name"]: f for f in out["findings"]}
    assert set(got) == {"Coming Soon - Chipotle Mexican Grill", "Tutta Bella", "Smokey Tacos",
                        "Luigi's Pizza"}
    chip = got["Coming Soon - Chipotle Mexican Grill"]
    assert chip["status"] == "planned" and chip["certain"]
    assert "listed on the chain's own store locator" in chip["evidence"]
    assert got["Tutta Bella"]["status"] == "opened" and got["Tutta Bella"]["date"] == "2026-09-03"
    assert any("NOW OPEN" in e for e in got["Tutta Bella"]["evidence"])
    # A young domain alone says "maybe".
    assert got["Smokey Tacos"]["status"] == "unknown" and not got["Smokey Tacos"]["certain"]
    assert got["Luigi's Pizza"]["certain"] and "local news" in got["Luigi's Pizza"]["evidence"][0]
    assert [h["title"] for h in out["news"]] == ["Luigi's Pizza opens in Cedar Park"]
    assert out["coverage"]["new_listings"] == 6 and out["coverage"]["confirmed_new"] == 4
    assert "2 are most likely existing businesses" in out["note"]


def test_a_headline_must_name_the_kind_of_business():
    pages = {"https://news.google.com/": gnews("Ideal Dental opens new clinic in Austin",
                                               "Silicon Labs opens new R&D center in Austin",
                                               "Austin dentist wins award")}
    items, _ = R.local_news("US", "Austin", ["dentist"], "en", get=net(pages),
                            breaker=news.Breaker(gap=0, sleep=lambda s: None),
                            stems=R.category_stems(["dental_clinic"], ["dent"], ["dentist"]))
    assert [i["title"] for i in items] == ["Ideal Dental opens new clinic in Austin"]


def test_a_map_that_did_not_answer_or_has_no_earlier_month_fails_plainly():
    class Broken(Places):
        def query(self, *a, **k):
            raise RuntimeError("s3")
    out = scan([], [], {}, places=Broken([], []))
    assert out["status"] == "failed" and "could not be read" in out["note"]
    out = scan([], [], {}, places=Places([], [], releases=("2026-09-23.0", "2026-09-23.1")))
    assert out["status"] == "failed" and "no earlier" in out["note"]


def test_a_short_earlier_release_is_said():
    cur = [place("P%d" % i, pid=str(i)) for i in range(30)]
    out = scan(cur, cur[:10], {})
    assert out["coverage"]["earlier_release_short"] and "far fewer places" in out["note"]


def test_rdap_dates_are_read_and_cached():
    cache, get = Cache(), net({"https://rdap.org/domain/x.com": rdap("2026-02-20")})
    assert R.registered_on("x.com", get=get, cache=cache) == "2026-02-20"
    assert R.registered_on("x.com", get=get, cache=cache) == "2026-02-20"
    assert len(get.asked) == 1
    assert R.registered_on("y.com", get=get, cache=cache) is None and "rdap:y.com" not in cache


# == new entrants =====================================================================

def test_product_nouns_and_domain_stems():
    words = ["sustainable sneakers", "wool shoes", "running shoes", "skincare"]
    assert R.head_nouns(words) == ["sneaker", "shoe", "skincare"]
    assert R.domain_stems(words) == ["sneaker", "skincare", "sustainablesneaker", "woolshoe",
                                     "runningshoe"]


def test_new_domains_come_from_yesterdays_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("domain-names.txt", "a.com\nNewSneakerCo.com\n")
    seen = []

    def get(url, **kw):
        seen.append(url)
        return {"status": "ok", "raw": buf.getvalue(), "note": ""}
    got, notes = R.new_domains(get, NOW)
    assert got == ["a.com", "newsneakerco.com"] and notes == []
    import base64
    assert base64.b64encode(b"2026-10-08.zip").decode() in seen[0]
    got, notes = R.new_domains(lambda u, **k: miss("blocked", "refused (HTTP 403)"), NOW)
    assert got == [] and "refused" in notes[0]


def test_launch_headlines_that_call_the_brand_new_come_first():
    pages = {"https://news.google.com/": gnews("Nike launches Caitlin 1 sneakers",
                                               "New running shoe brand January launches",
                                               "Weather report")}
    items, _ = R.launch_news(["running shoes"], ["US"], get=net(pages),
                             breaker=news.Breaker(gap=0, sleep=lambda s: None))
    assert [i["title"] for i in items] == ["New running shoe brand January launches",
                                           "Nike launches Caitlin 1 sneakers"]


class Llm:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def call_json(self, system, user, schema, **kw):
        self.calls.append(kw)
        return self.reply, {}


def test_brands_are_taken_from_headlines_without_the_client_or_bad_indexes():
    heads = [{"title": "New brand January launches"}, {"title": "Allbirds opens store"}]
    llm = Llm({"brands": [{"name": "January", "headline": 0, "is_new_brand": True},
                          {"name": "january", "headline": 0, "is_new_brand": True},
                          {"name": "Allbirds", "headline": 1, "is_new_brand": False},
                          {"name": "Ghost", "headline": 9, "is_new_brand": True}]})
    got = R.brands_from_headlines(heads, {"name": "Allbirds"}, llm=llm)
    assert [(b["name"], b["is_new_brand"]) for b in got] == [("January", True)]
    assert llm.calls[0]["stage"] == "radar_brands"


def test_a_brand_site_must_carry_the_brand_name_and_not_be_a_marketplace():
    def search(queries, **kw):
        return {"results": [{"query": queries[0], "position": 1, "domain": "amazon.com"},
                            {"query": queries[0], "position": 2, "domain": "runjanuary.com"},
                            {"query": queries[1], "position": 1, "domain": "news.example"}]}
    got, err = R.resolve_brands([{"name": "January"}, {"name": "Zaydn"}], country="US",
                                token="t", search=search)
    assert got == {"January": "runjanuary.com"} and err is None
    assert R.resolve_brands([{"name": "X"}], country="US", token="", search=search) == ({}, "no search token")


def test_entrants_are_new_brands_that_really_compete():
    profile = {"name": "Allbirds", "industry": {"keywords": ["wool shoes"]}, "markets": ["US"]}
    pages = {"https://news.google.com/": gnews("New shoe brand January launches",
                                               "Old Shoe Co launches a boot"),
             "https://rdap.org/domain/runjanuary.com": rdap("2026-03-01"),
             "https://rdap.org/domain/oldshoe.com": rdap("1999-01-01"),
             "https://www.whoisds.com": miss("blocked", "refused (HTTP 403)")}
    llm = Llm({"brands": [{"name": "January", "headline": 0, "is_new_brand": True},
                          {"name": "Old Shoe Co", "headline": 1, "is_new_brand": False},
                          {"name": "Rival", "headline": 1, "is_new_brand": False}]})

    def search(queries, **kw):
        assert queries == ['"January" official site']      # only the new brand is looked up
        return {"results": [{"query": queries[0], "position": 1, "domain": "runjanuary.com"}]}

    def verify(items, briefs, profile, **kw):
        return {i["domain"]: {"site_type": "business", "same_offering": "yes", "same_customers": "yes",
                              "name": i["name"], "sells": "running shoes", "reason": "same",
                              "location": "US", "where": "same_country", "locations": "online_only"}
                for i in items}, []
    out = R.scan_entrants(profile, own_domain="allbirds.com", competitors=["rival.com"],
                          get=net(pages), fetch_home=lambda i: {"status": "ok"}, now=NOW, token="t",
                          breaker=news.Breaker(gap=0, sleep=lambda s: None), llm=llm,
                          search=search, verify=verify)
    assert [f["domain"] for f in out["findings"]] == ["runjanuary.com"]
    # "Old Shoe Co" was called new by nobody; an old domain called new by a
    # headline is not an entrant either (below).
    f = out["findings"][0]
    assert any("registered on 2026-03-01" in e for e in f["evidence"])
    assert out["coverage"]["candidates"] == 1
    assert {l["name"]: l["why"] for l in out["coverage"]["left_out"]} == {
        "Old Shoe Co": "an established brand launching a product, not a new brand",
        "Rival": "an established brand launching a product, not a new brand"}
    assert "refused" in out["note"]                     # the domain list failure is said


def test_a_profile_without_products_skips_the_entrant_scan():
    out = R.scan_entrants({"name": "X"}, own_domain="x.com", competitors=[], get=net({}),
                          fetch_home=None, breaker=news.Breaker())
    assert out["status"] == "skipped"


# == one client, on Postgres ===========================================================

from test_market_radar_store import pg, OWNER  # noqa: E402,F401

LOCAL_PROFILE = {"name": "Acme Dental", "archetype": "local_single", "hq_point": {"lat": 30.5, "lon": -97.82},
                 "hq": {"city": "Cedar Park", "country_code": "US"},
                 "industry": {"keywords": ["dentist Brushy Creek"], "plain_label": "Dentist"}}


def _client(pg, profile, domain="acme-dental.com"):
    e = pg.upsert_entity(domain, name=profile["name"])
    pg.set_profile(e, profile)
    c = pg.upsert_client(OWNER, e)
    return e, c


def test_the_local_radar_needs_a_competitor_search_first(pg):
    _e, c = _client(pg, LOCAL_PROFILE)
    out = R.run_for_client(c, OWNER, store=pg, places=Places([], []), io={
        "get": net({}), "fetch": lambda u: {}, "fetch_home": lambda i: {}})
    assert out["local"]["status"] == "skipped" and "competitor search first" in out["local"]["note"]


def test_the_local_radar_stores_findings_once_and_reuses_a_fresh_scan(pg):
    e, c = _client(pg, LOCAL_PROFILE)
    run = pg.create_run(c, OWNER, "baseline")
    pg.update_run(run, status="complete", summary={"result": {"coverage": {"places": {
        "categories": ["restaurant"], "terms": ["restaurant"], "radius_km": 5}}}})
    cur = [place("Coming Soon - Taco Palace", pid="t1", dist=1.2)]
    io_ = {"get": net({}), "fetch": lambda u: {"status": "error"}, "fetch_home": lambda i: {}}
    out = R.run_for_client(c, OWNER, store=pg, places=Places(cur, []), io=io_,
                           now=datetime.now(timezone.utc))
    assert out["local"]["new_events"] == 1
    (ev,) = pg.recent_events([e])
    assert ev["type"] == "nearby_opening" and ev["status"] == "planned"
    assert ev["title"] == "Coming soon nearby: Coming Soon - Taco Palace (restaurant, 1.2 km away)"
    assert ev["location"]["distance_km"] == 1.2

    class Fail(Places):
        def query(self, *a, **k):
            raise AssertionError("the map was read again")
    again = R.run_for_client(c, OWNER, store=pg, places=Fail([], []), io=io_,
                             now=datetime.now(timezone.utc))
    assert again["local"]["reused"] and len(pg.recent_events([e])) == 1


def test_a_shop_gets_the_entrant_scan_and_other_kinds_get_neither(pg):
    shop = dict(LOCAL_PROFILE, name="Shoe Co", archetype="ecommerce", industry={"keywords": []})
    _e, c = _client(pg, shop, "shoeco.com")
    out = R.run_for_client(c, OWNER, store=pg, places=Places([], []), io={
        "get": net({}), "fetch": lambda u: {}, "fetch_home": lambda i: {}})
    assert "entrants" in out and "local" not in out and out["entrants"]["status"] == "skipped"
    b2b = dict(LOCAL_PROFILE, name="Soft Co", archetype="b2b_product")
    _e, c = _client(pg, b2b, "softco.com")
    out = R.run_for_client(c, OWNER, store=pg, places=Places([], []), io={
        "get": net({}), "fetch": lambda u: {}, "fetch_home": lambda i: {}})
    assert "b2b_product" in out["skipped"]


def test_a_radar_that_breaks_does_not_lose_the_collection():
    from tracker import market_radar_run as mrun
    saved = {}

    class S:
        def update_run(self, run_id, **kw):
            saved.update(kw)

    import tracker.market_radar_store as real
    orig = real.update_run
    real.update_run = S().update_run
    try:
        def boom(*a, **k):
            raise RuntimeError("overture down")
        mrun.collect_job(1, 2, OWNER, collect=lambda *a, **k: {"companies": []}, radar=boom,
                         pulse=lambda *a, **k: {"pulses": []})
    finally:
        real.update_run = orig
    assert saved["status"] == "complete" and "overture down" in saved["summary"]["radar"]["error"]


def test_a_renamed_place_on_the_same_page_is_not_new_but_another_branch_page_is():
    prev = [place("Bob's Grill", pid="a", site="https://chain.com/loc/austin")]
    cur = [place("Bob's Grill & Bar", pid="a2", site="https://chain.com/loc/austin/"),
           place("Bob's Grill", pid="b", lat=30.6, site="https://chain.com/loc/cedar-park")]
    assert [p["site"] if "site" in p else p["websites"][0] for p in R.new_listings(cur, prev)] == [
        "https://chain.com/loc/cedar-park"]


def test_a_chain_listing_alone_is_not_evidence():
    cur = [place("Burger Chain", source="AllThePlaces", site="https://burgerchain.com/loc/1")]
    out = scan(cur, [], {"https://rdap.org/domain/burgerchain.com": rdap("1990-01-01")},
               fetch=lambda u: {"status": "ok", "text": "Welcome"})
    assert out["findings"] == []


def test_generic_words_are_not_products():
    assert R.head_nouns(["online store", "premium goods", "candles"]) == ["candle"]


def test_a_brand_the_check_says_does_not_compete_is_not_an_entrant():
    profile = {"name": "Allbirds", "industry": {"keywords": ["wool shoes"]}, "markets": ["US"]}
    pages = {"https://news.google.com/": gnews("New shoe brand January launches"),
             "https://rdap.org/domain/runjanuary.com": rdap("2026-03-01")}
    llm = Llm({"brands": [{"name": "January", "headline": 0, "is_new_brand": True}]})
    out = R.scan_entrants(
        profile, own_domain="allbirds.com", competitors=[], get=net(pages),
        fetch_home=lambda i: {"status": "ok"}, now=NOW, token="t",
        breaker=news.Breaker(gap=0, sleep=lambda s: None), llm=llm,
        search=lambda q, **k: {"results": [{"query": q[0], "position": 1, "domain": "runjanuary.com"}]},
        verify=lambda items, b, p, **k: ({i["domain"]: {"site_type": "business", "same_offering": "no",
                                                         "same_customers": "yes"} for i in items}, []))
    assert out["findings"] == [] and out["coverage"]["readable"] == 1
    assert out["coverage"]["left_out"] == [{"name": "January", "domain": "runjanuary.com",
                                            "why": "sells something else: unknown"}]


def test_a_finding_with_only_a_young_domain_is_titled_possibly_new():
    class S:
        def __init__(self):
            self.ev = []

        def record_event(self, entity_id, key, **kw):
            self.ev.append(kw)
            return 1, True
    s = S()
    R._record(s, 1, "radar_local", {"findings": [
        {"key": "x", "name": "Smokey Tacos", "category": "restaurant", "distance_km": 3.45,
         "status": "unknown", "certain": False, "evidence": ["its website x was registered"],
         "website": "https://x.com/"}]}, NOW)
    assert s.ev[0]["title"] == "Possibly new nearby: Smokey Tacos (restaurant, 3.5 km away)"
    assert s.ev[0]["status"] == "unknown"


def test_a_neighbourhood_named_practice_does_not_claim_neighbourhood_headlines():
    assert not R._mentions("A Burger Restaurant Is Opening In Notting Hill", "Notting Hill Dental", "London")
    assert R._mentions("Luigi's Pizza opens in Cedar Park", "Luigi's Pizza", "Cedar Park")
    assert not R._mentions("New shop opens in Cedar Park", "Cedar Park", "Cedar Park")
    assert not R._mentions("Pizzeria opens", "Pizza", "")


def test_a_brand_named_in_launch_news_may_overlap_but_a_bare_domain_may_not():
    v = {"site_type": "business", "same_offering": "partly", "same_customers": "yes",
         "where": "same_country", "locations": "online_only"}
    assert R.entrant(v, from_news=True) and not R.entrant(v)


@pytest.mark.parametrize("change, ok_", [({}, True), ({"same_offering": "partly"}, False),
                                         ({"where": "other_country"}, False), ({"locations": "one"}, False),
                                         ({"same_customers": "no"}, False), ({"site_type": "other"}, False)])
def test_an_entrant_sells_the_same_thing_to_the_same_customers_in_the_same_markets(change, ok_):
    v = dict({"site_type": "business", "same_offering": "yes", "same_customers": "partly",
              "where": "worldwide", "locations": "online_only"}, **change)
    assert R.entrant(v) is ok_


def test_a_failed_headline_read_says_why():
    class Bad:
        def call_json(self, *a, **k):
            from tracker.market_radar_llm import ModelError
            raise ModelError("truncated", "the reply hit max_tokens")
    out = R.scan_entrants({"name": "A", "industry": {"keywords": ["wool shoes"]}}, own_domain="a.com",
                          competitors=[], get=net({"https://news.google.com/": gnews("New shoe brand X launches")}),
                          fetch_home=lambda i: {}, now=NOW, token="", llm=Bad(),
                          breaker=news.Breaker(gap=0, sleep=lambda s: None))
    assert "truncated: the reply hit max_tokens" in out["note"]


def test_the_one_off_radar_tidy_forgets_scans_and_entrant_events_only(pg):
    e = pg.upsert_entity("shop.example")
    pg.record_event(e, "radar_entrants:x", type="new_entrant", title="New brand", source={"url": "u", "detector": "radar_entrants"})
    pg.record_event(e, "radar_local:y", type="nearby_opening", title="New nearby", source={"url": "u", "detector": "radar_local"})
    pg.save_snapshot(e, "radar_local", {"scan": {}})
    pg.save_snapshot(e, "catalog", {"products": {}})
    assert pg.drop_radar_scans() == (1, 1)
    assert [x["type"] for x in pg.recent_events([e])] == ["nearby_opening"]
    assert pg.latest_snapshot(e, "catalog") and pg.latest_snapshot(e, "radar_local") is None


def test_local_news_uses_the_industry_label_not_keywords_that_name_a_neighbourhood(pg):
    e, c = _client(pg, LOCAL_PROFILE)
    run = pg.create_run(c, OWNER, "baseline")
    pg.update_run(run, status="complete", summary={"result": {"coverage": {"places": {
        "categories": ["dental_clinic"], "terms": ["dent"], "radius_km": 5}}}})
    pages = {"https://news.google.com/": gnews("Smile Dental opens in Cedar Park",
                                               "Brushy Creek trail opens in Cedar Park")}
    out = R.run_for_client(c, OWNER, store=pg, places=Places([], []), io={
        "get": net(pages), "fetch": lambda u: {}, "fetch_home": lambda i: {}},
        now=datetime.now(timezone.utc))
    assert [h["title"] for h in out["local"]["news"]] == ["Smile Dental opens in Cedar Park"]


def test_local_news_asks_with_the_map_category_unquoted_and_the_label():
    asked = []

    def get(url, **kw):
        asked.append(url)
        return gnews()
    R.scan_local(point={"lat": 30.5, "lon": -97.82}, categories=["dental_clinic", "dentist"], terms=["dent"],
                 radius_km=5, city="Austin", country="US", lang="en", own_domain="a.com",
                 places=Places([], []), get=get, cache=Cache(), fetch=lambda u: {},
                 breaker=news.Breaker(gap=0, sleep=lambda s: None), now=NOW,
                 industry_words=["General dentistry"])
    from urllib.parse import unquote_plus
    qs = [unquote_plus(u.split("q=")[1].split("&")[0]) for u in asked if "news.google" in u]
    assert qs[0].startswith('"Austin" dental clinic (') and qs[1].startswith('"Austin" General dentistry (')


def test_an_old_domain_outweighs_a_headline_that_calls_the_brand_new():
    profile = {"name": "Allbirds", "industry": {"keywords": ["wool shoes"]}, "markets": ["US"]}
    pages = {"https://news.google.com/": gnews("First-Ever Shaq shoe collection debuts"),
             "https://rdap.org/domain/shaq.com": rdap("1996-01-01")}
    llm = Llm({"brands": [{"name": "Shaq", "headline": 0, "is_new_brand": True}]})
    out = R.scan_entrants(
        profile, own_domain="allbirds.com", competitors=[], get=net(pages),
        fetch_home=lambda i: {"status": "ok"}, now=NOW, token="t",
        breaker=news.Breaker(gap=0, sleep=lambda s: None), llm=llm,
        search=lambda q, **k: {"results": [{"query": q[0], "position": 1, "domain": "shaq.com"}]},
        verify=lambda items, b, p, **k: ({i["domain"]: {"site_type": "business", "same_offering": "yes",
                                                         "same_customers": "yes", "where": "same_country",
                                                         "locations": "online_only"} for i in items}, []))
    assert out["findings"] == []


@pytest.mark.parametrize("v, why", [
    (None, "the check gave no verdict"),
    ({"site_type": "directory_or_marketplace"}, "not a brand's own shop (directory or marketplace)"),
    ({"site_type": "business", "same_offering": "no", "sells": "hoodies"}, "sells something else: hoodies"),
    ({"site_type": "business", "same_offering": "partly", "sells": "dresses"}, "only partly the same products: dresses"),
    ({"site_type": "business", "same_offering": "yes", "same_customers": "yes", "where": "other_country",
      "location": "India"}, "sells in another country (India)"),
    ({"site_type": "business", "same_offering": "yes", "same_customers": "yes", "locations": "one"},
     "a single local store")])
def test_each_left_out_candidate_says_why(v, why):
    assert R._why_not(v) == why


def test_generic_business_words_are_not_the_kind_of_business():
    assert R.category_stems(["dental_clinic"], ["dent"], ["Dental practice"]) == ["dent", "dental"]


def test_a_non_english_market_says_launch_news_is_read_in_english():
    out = R.scan_entrants({"name": "S", "industry": {"keywords": ["socks"]}, "markets": ["DE"]},
                          own_domain="s.de", competitors=[], get=net({}), fetch_home=lambda i: {},
                          now=NOW, token="", llm=Llm({"brands": []}),
                          breaker=news.Breaker(gap=0, sleep=lambda s: None))
    assert "asked in English" in out["note"]


def test_brands_past_the_search_limit_say_they_were_not_looked_up(monkeypatch):
    monkeypatch.setattr(R, "MAX_BRAND_SEARCHES", 1)
    llm = Llm({"brands": [{"name": "A1", "headline": 0, "is_new_brand": True},
                          {"name": "B2", "headline": 0, "is_new_brand": True}]})
    out = R.scan_entrants({"name": "S", "industry": {"keywords": ["shoes"]}}, own_domain="s.com",
                          competitors=[], get=net({"https://news.google.com/": gnews("New shoe brand A1 launches")}),
                          fetch_home=lambda i: {}, now=NOW, token="t", llm=llm,
                          breaker=news.Breaker(gap=0, sleep=lambda s: None),
                          search=lambda q, **k: {"results": []})
    assert {l["name"]: l["why"] for l in out["coverage"]["left_out"]} == {
        "A1": "its website was not found",
        "B2": "not looked up: at most 1 brands are searched per scan"}
