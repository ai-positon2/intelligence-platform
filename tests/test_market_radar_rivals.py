"""Market Radar Phase 2: competitor discovery (tracker/market_radar_rivals),
Overture places (tracker/market_radar_places), the shared model call
(tracker/market_radar_llm) and the background run (tracker/market_radar_run).

No network and no model: search, map data, homepages and the Claude client
are fakes. The traps named here were seen on real sites on 2026-10-09.
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

from tracker import market_radar_llm as llm  # noqa: E402
from tracker import market_radar_places as places  # noqa: E402
from tracker import market_radar_rivals as rv  # noqa: E402
from tests.test_market_radar_store import OWNER, pg, world  # noqa: E402,F401  (fixtures)


# == a scripted Claude client ==============================================================

def reply(body, stop="end_turn", model="claude-sonnet-5-5", usage=None):
    usage = usage or {"input_tokens": 1000, "output_tokens": 200}
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=json.dumps(body) if not isinstance(body, str) else body)],
        stop_reason=stop, model=model, stop_details=None,
        usage=SimpleNamespace(model_dump=lambda: dict(usage)))


class Scripted:
    """Answers each call by which step's system prompt it carries."""

    def __init__(self, plan=None, verdicts=None, ranked=None, fail=()):
        self.plan, self.verdicts, self.ranked, self.fail = plan, verdicts or {}, ranked, set(fail)
        self.calls = []
        outer = self

        class _Msgs:
            def create(self, **kw):
                outer.calls.append(kw)
                return outer.answer(kw)

        self.messages = _Msgs()
        self.beta = SimpleNamespace(messages=_Msgs())

    def step(self, kw):
        s = kw["system"]
        return "plan" if s is rv.PLAN_SYSTEM else "verify" if s is rv.VERIFY_SYSTEM else "rank"

    def answer(self, kw):
        step = self.step(kw)
        if step in self.fail:
            return reply("", stop="refusal")
        if step == "plan":
            return reply(self.plan or plan_body())
        if step == "verify":
            asked = json.loads(kw["messages"][0]["content"].split("CANDIDATES:\n", 1)[1])
            return reply({"verdicts": [self.verdicts.get(c["domain"], verdict(c["domain"]))
                                       for c in asked]}, model="claude-haiku-5-5")
        body = self.ranked
        if body is None:
            asked = json.loads(kw["messages"][0]["content"].split("CANDIDATES (checked):\n", 1)[1])
            body = {"competitors": [{"domain": c["domain"], "kind": "direct", "score": 70,
                                     "reason": "Sells the same — nearby."} for c in asked],
                    "left_out": [], "gaps": []}
        return reply(body)

    def steps(self):
        return [self.step(kw) for kw in self.calls]


def plan_body(**over):
    body = {"queries": [{"q": "dentist austin tx", "purpose": "local"},
                        {"q": "dentist austin tx", "purpose": "dupe"},
                        {"q": "best dentists austin", "purpose": "lists"}],
            "search_country": "us", "search_language": "en",
            "known": [{"name": "Rival", "domain": "rival.example", "relation": "direct",
                       "why": "Same – market."}],
            "place_categories": ["dentist"], "place_terms": ["dent"], "radius_km": 4, "notes": ""}
    body.update(over)
    return body


def verdict(domain, **over):
    v = {"domain": domain, "site_type": "business", "name": domain.split(".")[0].title(),
         "sells": "dental care", "same_offering": "yes", "same_customers": "yes",
         "where": "same_city", "location": "Austin, US", "scale": "similar", "locations": "one",
         "reason": "A dental practice — in Austin."}
    v.update(over)
    return v


PROFILE = {
    "name": "Acme Dental", "one_liner": "A family dental practice in Austin.",
    "offerings": ["checkups", "implants"], "customer_type": "B2C", "archetype": "local_single",
    "business_model": "fee for service", "location_count": 1,
    "hq": {"city": "Austin", "region": "TX", "country_code": "US"}, "markets": ["US"],
    "service_area": "Central Austin", "price_positioning": "mid",
    "industry": {"plain_label": "Dental practice", "keywords": ["dentist"]},
    "competitors_named": [{"name": "Named Co", "url": "https://named.example/vs"}],
    "languages": ["en"], "facts": {"website": "https://www.acme.example/", "domain": "acme.example"},
    "hq_point": {"lat": 30.28, "lon": -97.73},
}


# == the shared model call =================================================================

def test_call_json_asks_for_the_schema_with_fallbacks_and_no_tools():
    c = Scripted()
    parsed, meta = llm.call_json(rv.PLAN_SYSTEM, "x", rv.PLAN_SCHEMA, model="claude-sonnet-5-5",
                                 max_tokens=100, stage="t", client=c)
    kw = c.calls[0]
    assert kw["output_config"]["format"]["schema"] is rv.PLAN_SCHEMA and "tools" not in kw
    assert kw["fallbacks"] == "default" and kw["max_tokens"] == 100
    assert parsed["search_country"] == "us"


@pytest.mark.parametrize("stop, kind", [("refusal", "refused"), ("max_tokens", "truncated")])
def test_call_json_never_returns_a_refusal_or_a_cut_reply(stop, kind):
    class C(Scripted):
        def answer(self, kw):
            return reply({"a": 1}, stop=stop)
    with pytest.raises(llm.ModelError) as e:
        llm.call_json("s", "u", {}, model="claude-haiku-5-5", max_tokens=10, stage="t", client=C())
    assert e.value.kind == kind


def test_profile_errors_are_the_shared_model_errors():
    from tracker import market_radar_profile as prof
    assert prof.ProfileError is llm.ModelError


# == the plan ==============================================================================

def test_plan_is_cleaned_before_anything_is_spent_on_it():
    plan, _ = rv.plan_search(PROFILE, client=Scripted(plan=plan_body(
        search_country="UK", search_language="hi", radius_km=400,
        queries=[{"q": "  a   b ", "purpose": "p"}, {"q": "A B", "purpose": "dupe"},
                 {"q": " ".join(["w"] * 33), "purpose": "too long"}]
        + [{"q": "q%d" % i, "purpose": "p"} for i in range(20)])))
    assert plan["search_country"] == "gb"           # Google's code for the UK
    assert plan["search_language"] is None          # not a language the search accepts
    assert plan["radius_km"] == 25
    assert plan["queries"][0]["q"] == "a b" and len(plan["queries"]) == rv.MAX_QUERIES
    assert all(len(q["q"].split()) <= 32 for q in plan["queries"])


def test_a_plan_without_a_usable_country_falls_back_to_the_headquarters():
    plan, _ = rv.plan_search(dict(PROFILE, hq={"country_code": "DE"}),
                             client=Scripted(plan=plan_body(search_country="")))
    assert plan["search_country"] == "de"


# == the candidate pool ====================================================================

def test_pool_keys_by_domain_and_never_holds_the_client_or_a_directory():
    pool = rv.Pool("acme.example")
    assert pool.add("https://www.acme.example/about", "search") is None
    assert pool.add("https://shop.acme.example/", "search") is None
    assert pool.add("https://www.yelp.com/biz/x", "search") is None
    assert pool.add("https://m.facebook.com/x", "places") is None
    assert "yelp.com" in pool.skipped
    a = pool.add("https://www.Rival.example/x", "search", position=3, query="q")
    b = pool.add("rival.example", "model", name="Rival")
    assert a is b and len(a["via"]) == 2 and a["names"] == ["Rival"]


def test_pool_puts_the_best_evidenced_candidates_first():
    pool = rv.Pool("acme.example")
    pool.add("search-only.example", "search", position=1, query="q")
    pool.add("far.example", "places", distance_km=4.0)
    pool.add("near.example", "places", distance_km=0.5)
    pool.add("named.example", "site")
    pool.add("twice.example", "search", position=5, query="q")
    pool.add("twice.example", "model")
    order = [i["domain"] for i in pool.ordered()]
    assert order[0] == "named.example"
    assert order.index("near.example") < order.index("far.example")
    assert order.index("twice.example") < order.index("search-only.example")


# == search ================================================================================

def test_search_results_become_candidates_with_their_query_and_position():
    pool = rv.Pool("acme.example")
    seen = {}

    def fake_search(queries, **kw):
        seen.update(kw, queries=queries)
        return {"results": [{"query": "dentist austin tx", "position": 2, "title": "Rival Dental",
                             "url": "https://rival.example/"},
                            {"query": "dentist austin tx", "position": 1, "title": "Top 10",
                             "url": "https://www.yelp.com/search"}],
                "error": None, "charged_usd": 0.005, "elapsed_ms": 1}
    plan, _ = rv.plan_search(PROFILE, client=Scripted())
    meta, results = rv.from_search(pool, plan, token="t", search=fake_search)
    assert seen["country"] == "us" and seen["stage"] == "rivals_search"
    assert seen["queries"] == ["dentist austin tx", "best dentists austin"]
    assert meta["status"] == "ok" and len(results) == 2
    assert pool.items["rival.example"]["via"][0]["position"] == 2


def test_a_failed_search_is_reported_as_failed_not_as_no_results():
    def broken(queries, **kw):
        return {"results": [], "error": "Apify run ended FAILED", "charged_usd": 0.0}
    meta, results = rv.from_search(rv.Pool("a.example"), {"queries": [{"q": "x"}],
                                                          "search_country": "us"},
                                   token="t", search=broken)
    assert meta["status"] == "failed" and meta["error"] and results == []


def test_a_search_that_raises_is_reported_too():
    def boom(queries, **kw):
        raise RuntimeError("APIFY_API_TOKEN is not set")
    meta, _ = rv.from_search(rv.Pool("a.example"), {"queries": [{"q": "x"}],
                                                    "search_country": "us"}, token="", search=boom)
    assert meta["status"] == "failed" and "APIFY" in meta["error"]


# == map data ==============================================================================

def place(name, web, km, cat="dentist", status="open"):
    return {"name": name, "websites": [web] if web else [], "distance_km": km, "category": cat,
            "taxonomy": cat, "hierarchy": [], "status": status, "street": "1 Main St",
            "city": "Austin", "lat": 30.0, "lon": -97.0}


class FakePlaces:
    SELF_RADIUS_KM = 0.4

    def __init__(self, near_self, found, wider=None):
        self.near_self, self.found, self.wider, self.calls = near_self, found, wider, []

    def query(self, lat, lon, radius_km, categories=(), terms=()):
        self.calls.append({"radius": radius_km, "categories": sorted(categories), "terms": terms})
        if radius_km == self.SELF_RADIUS_KM:
            return self.near_self
        if len(self.calls) > 2 and self.wider is not None:
            return self.wider
        return self.found


def test_places_use_the_clients_own_listing_category_and_keep_the_nearest_companies():
    found = ([place("Acme", "https://acme.example", 0.0),
              place("Closed", "https://closed.example", 0.1, status="permanently_closed"),
              place("No site", None, 0.2),
              place("FB only", "https://facebook.com/x", 0.3),
              place("Chain A", "https://chain.example/a", 0.4),
              place("Chain B", "https://chain.example/b", 0.9)]
             + [place("P%d" % i, "https://p%d.example" % i, 1 + i / 10) for i in range(30)])
    fp = FakePlaces([place("Acme", "https://www.acme.example/", 0.01, cat="general_dentistry")],
                    found)
    pool = rv.Pool("acme.example")
    meta = rv.from_places(pool, PROFILE, {"place_categories": ["dentist"], "place_terms": ["dent"],
                                          "radius_km": 4}, places=fp)
    assert "general_dentistry" in fp.calls[1]["categories"]      # learnt from its own listing
    assert meta["own_listing"]["category"] == "general_dentistry"
    assert (meta["closed"], meta["without_website"], meta["website_is_social"]) == (1, 1, 1)
    assert meta["added"] == rv.MAX_PLACES
    assert "closed.example" not in pool.items and "acme.example" not in pool.items
    chain = pool.items["chain.example"]["via"][0]
    assert chain["distance_km"] == 0.4 and chain["branches_nearby"] == 2


def test_places_widen_the_radius_once_when_the_area_is_thin():
    fp = FakePlaces([], [place("One", "https://one.example", 2)],
                    wider=[place("P%d" % i, "https://p%d.example" % i, 5) for i in range(9)])
    meta = rv.from_places(rv.Pool("acme.example"), PROFILE,
                          {"place_categories": ["dentist"], "place_terms": [], "radius_km": 3},
                          places=fp)
    assert meta["radius_km"] == 9 and meta["widened"] and meta["added"] == 9


def test_places_report_why_they_did_not_run():
    no_point = rv.from_places(rv.Pool("a.example"), dict(PROFILE, hq_point=None), {}, places=FakePlaces([], []))
    assert no_point["status"] == "skipped" and "location" in no_point["note"]

    class Down(FakePlaces):
        def query(self, *a, **k):
            raise places.PlacesUnavailable("duckdb is not installed")
    down = rv.from_places(rv.Pool("a.example"), PROFILE, {"place_terms": ["dent"]}, places=Down([], []))
    assert down["status"] == "failed" and "duckdb" in down["error"]


def test_overture_sql_filters_by_box_and_category_and_trims_the_corners():
    seen = {}

    def runner(sql, params):
        seen.update(sql=sql, params=params)
        cols = (None, None, None, None, None, None, None)
        near = ("A", "dentist", "dentist", ["health", "dentist"], ["https://a.example"], "open", 0.9,
                -97.7431, 30.2672, "1 St", "Austin", "US", None)
        corner = ("B", "dentist", "dentist", [], None, "open", 0.9,
                  -97.7431 + 0.0310, 30.2672 + 0.0269, None, None, None, None)
        return [corner, near, cols + (None,) * 6]
    rows = places.query(30.2672, -97.7431, 3, categories=["Dentist"], terms=["dent"],
                        release="2026-09-23.1", runner=runner)
    assert "release/2026-09-23.1/theme=places" in seen["sql"]
    assert "basic_category IN (?)" in seen["sql"] and "dentist" in seen["params"]
    assert "%dent%" in seen["params"]
    assert [r["name"] for r in rows] == ["A"] and rows[0]["distance_km"] == 0.0
    assert rows[0]["websites"] == ["https://a.example"]


def test_latest_release_reads_the_bucket_listing(monkeypatch):
    monkeypatch.setitem(places._state, "release", None)
    xml = ("<ListBucketResult><CommonPrefixes><Prefix>release/2026-08-20.0/</Prefix></CommonPrefixes>"
           "<CommonPrefixes><Prefix>release/2026-09-23.1/</Prefix></CommonPrefixes></ListBucketResult>")
    assert places.latest_release(fetch_text=lambda url, limit=0: xml) == "2026-09-23.1"
    monkeypatch.setitem(places._state, "release", None)
    assert places.latest_release(fetch_text=lambda url, limit=0: None) == places.FALLBACK_RELEASE


def test_distance_is_great_circle():
    assert round(places.distance_km(51.5074, -0.1278, 48.8566, 2.3522)) == 344    # London-Paris


# == reading candidates, merging redirects =================================================

def test_two_candidates_landing_on_one_site_are_one_company():
    # centralaustindental.com redirects to koladentistry.com (2026-10-09).
    a = {"domain": "koladentistry.com", "names": ["KoLa"], "via": [{"kind": "places", "distance_km": 0.75}]}
    b = {"domain": "centralaustindental.com", "names": ["Central"], "via": [{"kind": "places", "distance_km": 0.76}]}
    briefs = {"koladentistry.com": {"status": "ok", "final_domain": "koladentistry.com"},
              "centralaustindental.com": {"status": "ok", "final_domain": "koladentistry.com"}}
    rv.merge_redirects([a, b], briefs, {})
    assert briefs["centralaustindental.com"]["status"] == "merged"
    assert len(a["via"]) == 2 and a["names"] == ["KoLa", "Central"]


def test_a_map_listed_place_whose_site_refuses_us_is_kept_as_map_only():
    item = {"domain": "drloya.example", "names": ["Dr Loya"],
            "via": [{"kind": "places", "distance_km": 1.16, "category": "general_dentistry",
                     "address": "1 Main St, Austin"}]}
    v = rv.map_only_verdict(item, {})
    assert v["map_only"] and rv.passes(v) and "1.2 km" in v["reason"] and "general dentistry" in v["reason"]
    assert rv.map_only_verdict({"domain": "x.example", "names": [], "via": [{"kind": "search"}]}, {}) is None


# == the check =============================================================================

def test_verify_batches_and_ignores_domains_it_was_not_asked_about():
    items = [{"domain": "c%d.example" % i, "names": [], "via": [{"kind": "search", "query": "q"}]}
             for i in range(10)]
    briefs = {i["domain"]: {"status": "ok", "head": "TITLE: x", "text": "t"} for i in items}
    briefs["c9.example"] = {"status": "blocked"}

    class Extra(Scripted):
        def answer(self, kw):
            r = super().answer(kw)
            body = json.loads(r.content[0].text)
            body["verdicts"].append(verdict("invented.example"))
            r.content[0].text = json.dumps(body)
            return r
    c = Extra()
    verdicts, errors = rv.verify(items, briefs, PROFILE, client=c)
    assert c.steps() == ["verify", "verify"]                 # 9 readable, batches of 8
    assert set(verdicts) == {"c%d.example" % i for i in range(9)} and errors == []
    assert "—" not in verdicts["c0.example"]["reason"]


def test_a_failed_check_batch_is_reported_with_its_candidates():
    items = [{"domain": "a.example", "names": [], "via": []}]
    verdicts, errors = rv.verify(items, {"a.example": {"status": "ok"}}, PROFILE,
                                 client=Scripted(fail={"verify"}))
    assert verdicts == {} and errors[0]["kind"] == "refused" and errors[0]["domains"] == ["a.example"]


@pytest.mark.parametrize("over, ok", [
    ({}, True), ({"same_offering": "partly"}, True), ({"site_type": "article_or_publisher"}, False),
    ({"site_type": "directory_or_marketplace"}, False), ({"same_offering": "no"}, False),
    ({"same_offering": "unclear"}, False), ({"same_customers": "no"}, False)])
def test_what_passes_the_check(over, ok):
    assert rv.passes(verdict("a.example", **over)) is ok


# == "top 10" articles =====================================================================

ARTICLE = """<html><body><h1>Best dentists in Austin</h1>
<a href="/about">About us</a> <a href="https://www.facebook.com/mag">FB</a>
<a href="https://www.koladentistry.com/">KoLa Dentistry</a>
<a href="https://aqua.example/">Aqua Dental</a> <a href="https://aqua.example/blog/2024/x">post</a>
<a href="https://deep.example/products/teeth/whitening">deep link</a>
<a href="https://news.mag.example/other">sister site</a></body></html>"""


def test_article_links_are_outside_company_sites_only():
    fetch = lambda url: {"status": "ok", "html": ARTICLE, "final_url": "https://mag.example/best"}
    links, err = rv.links_from_article("https://mag.example/best", fetch=fetch)
    assert err is None
    assert [d for d, _, _ in links] == ["koladentistry.com", "aqua.example", "deep.example"]
    pool = rv.Pool("acme.example")
    meta = rv.from_articles(pool, [{"url": "https://mag.example/best"}], fetch=fetch)
    assert set(pool.items) == {"koladentistry.com", "aqua.example"}   # the deep link is not a company
    assert pool.items["aqua.example"]["via"][0]["article"] == "mag.example"
    assert meta["added"] == 2


def test_only_list_like_results_on_articles_or_unjudged_sites_are_read():
    results = [{"url": "https://mag.example/best", "title": "10 best dentists in Austin", "position": 3},
               {"url": "https://rival.example/", "title": "Rival Dental", "position": 1},
               {"url": "https://shop.example/top", "title": "Top picks", "position": 2},
               {"url": "https://unjudged.example/x", "title": "Alternatives to Acme", "position": 4},
               {"url": "https://www.yelp.com/top", "title": "Top 10 Dentists", "position": 5}]
    verdicts = {"mag.example": verdict("mag.example", site_type="article_or_publisher"),
                "rival.example": verdict("rival.example"), "shop.example": verdict("shop.example")}
    picked = [r["url"] for r in rv.articles_to_read(results, verdicts, {})]
    assert picked == ["https://mag.example/best", "https://unjudged.example/x"]


# == the whole step ========================================================================

def home(**over):
    def read(item):
        b = {"status": "ok", "final_domain": item["domain"], "head": "TITLE: %s" % item["domain"],
             "text": "Dentist in Austin", "address_countries": ["US"]}
        b.update(over.get(item["domain"], {}))
        return b
    return read


def run_discover(client, *, search_results=(), found=(), reader=None, profile=PROFILE, **kw):
    def fake_search(queries, **k):
        return {"results": list(search_results), "error": None, "charged_usd": 0.005}
    fp = FakePlaces([], list(found))
    kw.setdefault("archive_reader", lambda item: {"status": "error", "note": "no archived copy"})
    return rv.discover(profile, client=client, token="t", search=fake_search, places=fp,
                       reader=reader or home(), fetch=lambda url: {"status": "error"}, **kw)


def test_discover_end_to_end_ranks_only_checked_candidates():
    ranked = {"competitors": [
        {"domain": "rival.example", "kind": "direct", "score": 90, "reason": "Same care — same city."},
        {"domain": "invented.example", "kind": "direct", "score": 99, "reason": "x"},
        {"domain": "near.example", "kind": "local", "score": 80, "reason": "Next door."},
        {"domain": "web.example", "kind": "local", "score": 60, "reason": "Says local."},
        {"domain": "rival.example", "kind": "direct", "score": 10, "reason": "duplicate"}],
        "left_out": [{"domain": "mag.example", "why": "an article"}], "gaps": ["Some — gaps"]}
    c = Scripted(ranked=ranked, verdicts={"web.example": verdict("web.example", where="same_country"),
                                          "mag.example": verdict("mag.example", site_type="article_or_publisher")})
    stages = []
    out = run_discover(c, progress=stages.append,
                       search_results=[{"query": "q", "position": 1, "url": "https://web.example/", "title": "Web"},
                                       {"query": "q", "position": 2, "url": "https://mag.example/a", "title": "Mag"}],
                       found=[place("Near", "https://near.example", 0.5)])
    assert c.steps()[0] == "plan" and c.steps()[-1] == "rank"
    assert stages == ["rivals_plan", "rivals_search", "rivals_verify", "rivals_rank"]
    got = {x["domain"]: x for x in out["competitors"]}
    assert list(got) == ["rival.example", "near.example", "web.example"]     # by score
    assert out["coverage"]["rank_dropped"]["domains"] == ["invented.example"]
    assert got["near.example"]["kind"] == "local" and got["near.example"]["distance_km"] == 0.5
    assert got["web.example"]["kind"] == "direct"     # "local" needs the map or a same-city address
    assert "—" not in got["rival.example"]["reason"] and "—" not in out["gaps"][0]
    assert got["rival.example"]["checked_on"] == "its own homepage"
    assert any(r["domain"] == "mag.example" for r in out["rejected"])
    assert {"site", "model", "search", "places"} <= set(out["coverage"])


def test_discover_keeps_unreadable_sites_visible_and_drops_self_redirects():
    reader = home(**{"rival.example": {"status": "blocked", "note": "server refused (HTTP 403)"},
                     "near.example": {"status": "blocked", "note": "server refused (HTTP 403)"},
                     "named.example": {"final_domain": "acme.example"}})
    out = run_discover(Scripted(), reader=reader, found=[place("Near", "https://near.example", 0.5)])
    assert [u["domain"] for u in out["unread"]] == ["rival.example"]
    assert out["unread"][0]["why"] == "server refused (HTTP 403); archive: no archived copy"
    assert [c["domain"] for c in out["competitors"]] == ["near.example"]
    assert out["competitors"][0]["checked_on"] == "map listing only"
    assert {"domain": "named.example", "why": "redirects to the client's own site"} in out["rejected"]


def test_discover_respects_the_limit_for_the_business_type():
    found = [place("P%d" % i, "https://p%d.example" % i, i / 10) for i in range(25)]
    out = run_discover(Scripted(), found=found)
    assert len(out["competitors"]) == rv.LIMITS["local_single"]


def test_online_brands_do_not_get_a_map_search():
    out = run_discover(Scripted(), profile=dict(PROFILE, archetype="ecommerce"),
                       found=[place("Near", "https://near.example", 0.5)])
    assert out["coverage"]["places"]["status"] == "not_run"
    assert "near.example" not in [c["domain"] for c in out["competitors"]]


def test_a_failed_plan_or_ranking_fails_the_step_with_its_reason():
    out = run_discover(Scripted(fail={"plan"}))
    assert out["status"] == "failed" and out["error"]["stage"] == "plan"
    out = run_discover(Scripted(fail={"rank"}))
    assert out["status"] == "failed" and out["error"]["stage"] == "rank" and out["survivors"]


def test_nothing_found_is_none_found_with_its_coverage():
    reader = home(**{d: {"status": "blocked"} for d in ("rival.example", "named.example")})
    out = run_discover(Scripted(), reader=reader)
    assert out["status"] == "none_found" and len(out["unread"]) == 2 and out["coverage"]["search"]


# == storing and the background run ========================================================

def test_save_proposes_competitors_and_keeps_a_users_removal(world):
    from tracker import market_radar_store as store
    result = {"competitors": [{"domain": "rival.example", "name": "Rival", "kind": "direct",
                               "score": 83, "found": ["Google #1"]}]}
    [eid] = rv.save(result, client_id=world["client"], owner_email=OWNER)
    store.set_competitor_status(world["client"], OWNER, eid, "removed")
    rv.save(result, client_id=world["client"], owner_email=OWNER)
    assert store.competitors(world["client"], OWNER) == []
    [row] = store.competitors(world["client"], OWNER, include_removed=True)
    assert float(row["confidence"]) == pytest.approx(0.83) and row["found_via"] == ["Google #1"]


def test_the_background_run_profiles_discovers_saves_and_closes(world):
    from tracker import market_radar_run as mrun
    from tracker import market_radar_store as store
    spawned = []
    run_id = mrun.start("https://www.acme-dental.com/", OWNER, spawn=lambda f, a: spawned.append((f, a)))
    assert store.get_run(run_id, OWNER)["status"] == "running"
    f, args = spawned[0]

    def built(url, run_id=None, client=None):
        return {"status": "ok", "profile": dict(PROFILE)}

    def found(profile, run_id=None, client=None, progress=None):
        progress("rivals_plan")
        return {"status": "ok", "competitors": [{"domain": "rival.example", "name": "Rival",
                                                 "kind": "direct", "score": 70, "found": []}],
                "coverage": {"search": {"status": "ok"}}}
    f(*args, discover=found, build_profile=built)
    s = mrun.status(run_id, OWNER)
    assert s["status"] == "complete" and s["stage"] == "done"
    assert s["summary"]["result"]["competitors"][0]["domain"] == "rival.example"
    assert [c["domain"] for c in store.competitors(args[3], OWNER)] == ["rival.example"]
    assert mrun.status(run_id, "someone@else.example") is None


def test_a_background_run_whose_profile_fails_says_so(world):
    from tracker import market_radar_run as mrun
    spawned = []
    run_id = mrun.start("acme-dental.com", OWNER, spawn=lambda f, a: spawned.append((f, a)))
    f, args = spawned[0]
    f(*args, build_profile=lambda url, run_id=None, client=None: {"status": "unreadable", "error": "403"},
      discover=lambda *a, **k: pytest.fail("discovery ran without a profile"))
    s = mrun.status(run_id, OWNER)
    assert s["status"] == "failed" and "unreadable" in s["error"]


def test_a_crashing_run_is_marked_failed_not_left_running(world):
    from tracker import market_radar_run as mrun
    spawned = []
    run_id = mrun.start("acme-dental.com", OWNER, spawn=lambda f, a: spawned.append((f, a)))
    f, args = spawned[0]

    def boom(*a, **k):
        raise RuntimeError("kaboom")
    f(*args, build_profile=lambda url, run_id=None, client=None: {"status": "ok", "profile": PROFILE},
      discover=boom)
    s = mrun.status(run_id, OWNER)
    assert s["status"] == "failed" and "kaboom" in s["error"]


# == the admin routes ======================================================================

ADMIN = "reporting@position2.com"
START = "/p2/admin/external-usage/market-radar-competitors-check"
STATUS = "/p2/admin/external-usage/market-radar-run-status"


def _client(email):
    import app as appmod
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


@pytest.fixture
def no_spend(monkeypatch):
    from tracker import market_radar_run as mrun

    def refuse(*a, **k):
        raise AssertionError("the route tried to start a paid run")
    monkeypatch.setattr(mrun, "start", refuse)


@pytest.mark.parametrize("body", [None, {"url": "acme.example"}, {"url": "acme.example", "confirm_spend": 1}])
def test_start_route_needs_an_explicit_confirmation(no_spend, body):
    c = _client(ADMIN)
    resp = c.post(START, json=body) if body is not None else c.post(START)
    assert resp.status_code == 400 and "confirm_spend" in resp.get_json()["error"]


@pytest.mark.parametrize("route", [START, STATUS])
@pytest.mark.parametrize("email, headers, status", [
    (None, {}, 302), ("someone@position2.com", {}, 403),
    (ADMIN, {"Origin": "https://evil.example"}, 403)])
def test_routes_are_admin_only_and_same_origin(no_spend, route, email, headers, status):
    resp = _client(email).post(route, json={"url": "acme.example", "confirm_spend": True, "run_id": 1},
                               headers=headers)
    assert resp.status_code == status


def test_start_route_starts_in_the_background_and_status_reports(world, monkeypatch):
    from tracker import market_radar_run as mrun
    started = []
    real = mrun.start
    monkeypatch.setattr(mrun, "start", lambda url, email, reuse_profile=False: real(
        url, email, reuse_profile=reuse_profile, spawn=lambda f, a: started.append(a)))
    resp = _client(ADMIN).post(START, json={"url": "https://acme.example/", "confirm_spend": True,
                                            "reuse_profile": True})
    assert resp.status_code == 202 and started and started[0][5] is True
    run_id = resp.get_json()["run_id"]
    s = _client(ADMIN).post(STATUS, json={"run_id": run_id}).get_json()
    assert s["status"] == "running" and s["stage"] == "queued" and s["ledger"]["total_usd"] == 0
    assert _client(ADMIN).post(STATUS, json={"run_id": "x"}).status_code == 400
    assert _client(ADMIN).post(STATUS, json={"run_id": run_id + 999}).status_code == 404


def test_start_route_refuses_a_url_without_a_host(world):
    resp = _client(ADMIN).post(START, json={"url": "localhost", "confirm_spend": True})
    assert resp.status_code == 400 and "usable company URL" in resp.get_json()["error"]


def test_client_facing_text_has_no_em_dashes_in_prompts():
    for text in (rv.PLAN_SYSTEM, rv.VERIFY_SYSTEM, rv.RANK_SYSTEM):
        assert "—" not in text and "–" not in text


def test_searches_go_in_parallel_groups_and_one_failed_group_makes_it_partial():
    calls = []

    def fake(queries, **kw):
        calls.append(list(queries))
        if "q4" in queries:
            return {"results": [], "error": "Apify run ended FAILED", "charged_usd": 0.0001,
                    "elapsed_ms": 5}
        return {"results": [{"query": queries[0], "position": 1, "url": "https://%s.example/" % queries[0]}],
                "error": None, "charged_usd": 0.01, "elapsed_ms": 9}
    plan = {"queries": [{"q": "q%d" % i} for i in range(10)], "search_country": "us"}
    meta, results = rv.from_search(rv.Pool("a.example"), plan, token="t", search=fake)
    assert sorted(map(tuple, calls)) == [("q0", "q1", "q2", "q3"), ("q4", "q5", "q6", "q7"), ("q8", "q9")]
    assert meta["status"] == "partial" and meta["runs"] == 3 and len(results) == 2
    assert meta["charged_usd"] == pytest.approx(0.0201) and meta["elapsed_ms"] == 9



def test_a_chain_does_not_get_single_offices_as_rivals():
    c = Scripted(verdicts={"rival.example": verdict("rival.example", locations="many")})
    out = run_discover(c, profile=dict(PROFILE, archetype="multi_location"))
    assert [x["domain"] for x in out["competitors"]] == ["rival.example"]
    assert {"domain": "named.example", "site_type": "business",
            "why": "a single site, not a rival to a chain at its level"} in out["rejected"]


def test_weak_matches_are_left_out_with_their_score():
    ranked = {"competitors": [{"domain": "rival.example", "kind": "direct", "score": 49, "reason": "far"},
                              {"domain": "named.example", "kind": "direct", "score": 50, "reason": "ok"}],
              "left_out": [], "gaps": []}
    out = run_discover(Scripted(ranked=ranked))
    assert [x["domain"] for x in out["competitors"]] == ["named.example"]
    assert out["left_out"][0]["domain"] == "rival.example" and "score 49" in out["left_out"][0]["why"]


def test_a_city_level_location_is_flagged_on_the_map_search():
    fp = FakePlaces([], [place("P", "https://p.example", 1)] * 9)
    meta = rv.from_places(rv.Pool("acme.example"),
                          dict(PROFILE, hq_point={"lat": 1, "lon": 2, "precision": "city", "source": "geocoded"}),
                          {"place_terms": ["dent"], "radius_km": 4}, places=fp)
    assert meta["location_precision"] == "city" and "city centre" in meta["warning"]
    meta = rv.from_places(rv.Pool("acme.example"), dict(PROFILE, hq_point={"lat": 1, "lon": 2, "precision": "street"}),
                          {"place_terms": ["dent"], "radius_km": 4}, places=fp)
    assert "warning" not in meta


def test_the_check_is_told_to_judge_the_business_behind_a_group_site():
    assert "business behind the site" in rv.VERIFY_SYSTEM and "Heartland" in rv.VERIFY_SYSTEM
    assert "locations" in rv.VERIFY_SCHEMA["properties"]["verdicts"]["items"]["required"]


def test_a_domain_pointing_at_a_reserved_address_says_so(monkeypatch):
    from tracker import market_radar_site as site

    def refuse(*a, **k):
        raise ValueError("Private or reserved page destinations are not allowed.")
    monkeypatch.setattr(site, "public_get", refuse)
    out = site.fetch("https://pacificdentalservices.com/")
    assert out["status"] == "error" and "address we do not fetch" in out["note"]



def test_a_refused_homepage_is_read_from_the_archive_and_labelled():
    # planetfitness.com answered 403 from Railway (2026-10-09).
    reader = home(**{"rival.example": {"status": "blocked", "note": "server refused (HTTP 403)"},
                     "near.example": {"status": "blocked", "note": "server refused (HTTP 403)"}})
    tried = []

    def archive(item):
        tried.append(item["domain"])
        return {"status": "ok", "final_domain": item["domain"], "head": "TITLE: Rival",
                "text": "Dentist", "archived": "2026-09-30"}
    c = Scripted()
    out = run_discover(c, reader=reader, archive_reader=archive,
                       found=[place("Near", "https://near.example", 0.5)])
    assert tried == ["rival.example"]           # the map already vouches for near.example
    got = {x["domain"]: x for x in out["competitors"]}
    assert got["rival.example"]["checked_on"] == "an archived copy of its homepage (2026-09-30)"
    assert out["coverage"]["checked"]["read_from_archive"] == 1
    asked = [kw for kw in c.calls if kw["system"] is rv.VERIFY_SYSTEM]
    assert "Wayback Machine's copy of 2026-09-30" in asked[0]["messages"][0]["content"]


def test_read_archived_labels_the_capture_date(monkeypatch):
    from tracker import market_radar_site as site
    monkeypatch.setattr(site, "fetch_archived", lambda url: {
        "status": "ok", "html": "<title>PF</title>", "text": "Gyms", "final_url": url,
        "via": "wayback:20260930120000", "note": "read from the Wayback Machine"})
    b = rv.read_archived({"domain": "planetfitness.com"})
    assert b["status"] == "ok" and b["archived"] == "2026-09-30" and b["final_domain"] == "planetfitness.com"
    monkeypatch.setattr(site, "fetch_archived", lambda url: {"status": "error", "note": "no copy"})
    assert rv.read_archived({"domain": "x.example"}) == {"status": "error", "note": "no copy"}



@pytest.mark.parametrize("brief, retry", [
    ({"status": "blocked", "note": "server refused (HTTP 403)"}, True),
    ({"status": "error", "note": "could not be reached (ReadTimeoutError)"}, True),
    ({"status": "error", "note": "unexpected HTTP 418"}, True),
    ({"status": "error", "note": "could not be reached (gaierror)"}, False),
    ({"status": "not_found", "note": "page not found (404)"}, False),
    ({"status": "ok"}, False)])
def test_which_unread_sites_are_retried_from_the_archive(brief, retry):
    assert rv.worth_archive(brief) is retry


def test_competitors_come_out_in_score_order():
    ranked = {"competitors": [{"domain": "named.example", "kind": "direct", "score": 70, "reason": "a"},
                              {"domain": "rival.example", "kind": "direct", "score": 90, "reason": "b"}],
              "left_out": [], "gaps": []}
    out = run_discover(Scripted(ranked=ranked))
    assert [c["domain"] for c in out["competitors"]] == ["rival.example", "named.example"]
