"""Market Radar, Phase 5: the industry pulse, no network.

Google News, the trade feeds, the Federal Register and the models are fakes
shaped like the live answers measured on 2026-10-10 (US and UK dental, US
footwear and German dental searches; DrBicuspid, Dentistry Today and
dentistry.co.uk feeds; the Federal Register's documents API).
"""
import email.utils
import os
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracker import market_radar_news as news  # noqa: E402
from tracker import market_radar_pulse as P  # noqa: E402
from test_market_radar_detectors import ok, miss  # noqa: E402

NOW = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)
PLAN = {"queries": ["NHS dentistry", "dental contract"],
        "match_terms": ["dental", "dentist", "dentistry"], "regulator_terms": ["dental"]}


def headline(title, pub="BBC", site="https://www.bbc.co.uk", day="2026-10-08", link=None):
    return (title, pub, site, day, link or "https://news.google.com/rss/articles/%d" % abs(hash(title)))


def gnews(*items):
    out = []
    for title, pub, site, day, link in items:
        stamp = email.utils.format_datetime(datetime.fromisoformat(day + "T08:00:00+00:00"))
        out.append("<item><title>%s - %s</title><link>%s</link><pubDate>%s</pubDate>"
                   '<source url="%s">%s</source></item>' % (title, pub, link, stamp, site, pub))
    return ok("<rss><channel>%s</channel></rss>" % "".join(out))


def feed(*titles, day="2026-10-08"):
    stamp = email.utils.format_datetime(datetime.fromisoformat(day + "T08:00:00+00:00"))
    return ok("<rss><channel>%s</channel></rss>" % "".join(
        "<item><title>%s</title><link>https://pub.example/%d</link><pubDate>%s</pubDate></item>"
        % (t, i, stamp) for i, t in enumerate(titles)))


def query_of(url):
    q = parse_qs(urlsplit(url).query).get("q", [""])[0]
    return q.rsplit(" when:", 1)[0]


class Net:
    """url -> answer. Google News answers are keyed by the query text."""

    def __init__(self, google=None, pages=None, urls=None):
        self.google, self.pages, self.urls, self.asked = google or {}, pages or {}, urls or {}, []

    def get(self, url, **kw):
        self.asked.append(url)
        if "news.google.com" in url:
            return self.google.get(query_of(url), miss())
        return self.urls.get(url, miss())

    def get_json(self, url, **kw):
        self.asked.append(url)
        v = self.urls.get(url.split("?")[0])
        if isinstance(v, dict) and "status" in v:
            return None, v
        if v is None:
            return None, miss()
        return v, ok("")

    def fetch(self, url):
        self.asked.append(url)
        html = self.pages.get(url)
        if html is None:
            return {"status": "blocked", "note": "server refused (HTTP 403)", "html": "",
                    "final_url": url}
        return {"status": "ok", "html": html, "final_url": url, "note": ""}


def breaker():
    return news.Breaker(gap=0, sleep=lambda s: None)


class Store:
    def __init__(self):
        self.sources, self.pulses, self.clients = [], [], {}

    def industry_sources(self, key, country=""):
        return [dict(r) for r in self.sources if r["industry_key"] == key and r["country"] == country]

    def save_industry_source(self, key, feed_url, *, country="", site_domain=None, kind="rss",
                             discovered_from=None, ok=None, error=None, item_count=None):
        for r in self.sources:
            if (r["industry_key"], r["country"], r["feed_url"]) == (key, country, feed_url):
                if ok:
                    r["last_ok_at"], r["last_error"] = NOW, None
                elif ok is False:
                    r["last_error"] = error
                r["item_count"] = item_count if item_count is not None else r["item_count"]
                return
        self.sources.append({"industry_key": key, "country": country, "feed_url": feed_url,
                             "site_domain": site_domain, "kind": kind,
                             "discovered_from": discovered_from, "created_at": NOW,
                             "last_ok_at": NOW if ok else None,
                             "last_error": None if ok else error, "item_count": item_count})

    def save_pulse(self, key, country, payload, *, run_id=None):
        self.pulses.append({"id": len(self.pulses) + 1, "industry_key": key, "country": country,
                            "payload": payload, "created_at": NOW, "run_id": run_id})
        return len(self.pulses)

    def latest_pulse(self, key, country):
        rows = [p for p in self.pulses if (p["industry_key"], p["country"]) == (key, country)]
        return rows[-1] if rows else None

    def get_client(self, client_id, owner_email):
        return self.clients.get(client_id)


class LLM:
    """Answers by ledger stage; an Exception instance is raised."""

    def __init__(self, **answers):
        self.answers, self.calls = answers, []

    def call_json(self, system, user, schema, *, model, max_tokens, run_id=None, stage, client=None):
        self.calls.append({"stage": stage, "user": user, "model": model})
        a = self.answers[stage]
        if isinstance(a, Exception):
            raise a
        return (a(user) if callable(a) else a), {}


class ModelError(Exception):
    def __init__(self, kind):
        self.kind, self.detail = kind, "detail"
        super().__init__(kind)


# == what counts as industry news ====================================================

@pytest.mark.parametrize("title,site,noise", [
    ("Dental Implants Market Size, Share | Growth Forecast [2034]", "https://news.example", True),
    ("Cosmetic Dentistry Market Companies, Size & Trends 2026-2034", "https://news.example", True),
    ("5 Medical Dental Supply Stocks to Watch as Industry Prospects Improve", "https://x.example", True),
    ("5 Best Dental Insurance Plans of October 2026", "https://www.forbes.com", True),
    ("Dental chains expand in Texas", "https://www.indexbox.io", True),
    ("Dental chains expand in Texas", "https://uk.openpr.com", True),
    ("META_TITLE_SECTORS", "https://wwd.com", True),
    ("Hoka gains market share from Nike in running shoes", "https://www.reuters.com", False),
    ("BDA rejects incremental NHS dental contract reform", "https://www.bda.org", False),
])
def test_market_research_adverts_and_listicles_are_noise_but_real_news_is_not(title, site, noise):
    assert P.is_noise({"title": title, "publisher_site": site}) is noise


def test_a_headline_names_the_industry_by_the_start_of_a_word_in_any_accent():
    pat = P.term_pattern(["dental", "zahnärzt", "zahnarzt", "x"])
    assert P.about_industry("Neue Zahnarztpraxis in Staßfurt", pat)
    assert P.about_industry("Zahnärzte-Versorgungswerk verzockt 1,3 Milliarden Euro", pat)
    assert P.about_industry("Dentalcare chain opens", pat) is True
    assert not P.about_industry("An accidental fire closed the mall", pat)
    assert P.term_pattern(["ab", ""]) is None and not P.about_industry("dental", None)


def test_the_industry_key_is_the_naics_code_or_the_label():
    assert P.industry_key({"industry": {"naics_code": "621210", "plain_label": "Dentist"}}) == \
        "naics:621210"
    assert P.industry_key({"industry": {"naics_code": "6212", "plain_label": "Zahnarzt Praxis"}}) \
        == "label:zahnarzt-praxis"
    assert P.industry_key({"industry": {}}) is None and P.industry_key({}) is None


@pytest.mark.parametrize("profile,expected", [
    ({"hq": {"country_code": "gb"}, "markets": ["GB", "IE"]}, ["GB", "IE"]),
    ({"hq": {"country_code": "US"}, "markets": ["AE", "AU", "CA", "DE", "GB", "US"]}, ["US"]),
    ({"hq": {"country_code": "DE"}, "markets": []}, ["DE"]),
    ({"hq": {}, "markets": ["GB"]}, ["GB"]),
    ({}, ["US"]),
])
def test_markets_are_home_first_and_a_global_shipper_is_read_at_home(profile, expected):
    assert P.markets(profile) == expected


# == the query plan ====================================================================

PROFILE = {"name": "Pembridge Dental", "archetype": "local_single",
           "industry": {"plain_label": "Dentist", "naics_code": "621210", "naics_title": "Offices of Dentists",
                        "keywords": ["dentist Notting Hill"]},
           "hq": {"country_code": "GB", "city": "London"}, "markets": ["GB"]}


def test_the_plan_is_cleaned_and_capped():
    llm = LLM(pulse_plan={"queries": ["NHS dentistry", "nhs  dentistry", "", "dental contract"] +
                          ["q%d" % i for i in range(9)],
                          "match_terms": ["Dental", "dent", "den", "dentistry", "dental"],
                          "regulator_terms": ["dental", "x" * 40]})
    plan, note = P.plan_queries(PROFILE, "GB", llm=llm)
    assert note is None and plan["queries"][:2] == ["NHS dentistry", "dental contract"]
    assert len(plan["queries"]) == P.MAX_QUERIES
    assert plan["match_terms"] == ["dental", "dent", "dentistry"] and plan["regulator_terms"] == ["dental"]
    assert "EDITION LANGUAGE: en" in llm.calls[0]["user"] and llm.calls[0]["model"] == P.PLAN_MODEL


def test_a_plan_the_model_could_not_write_falls_back_to_the_industry_name_and_says_so():
    plan, note = P.plan_queries(PROFILE, "GB", llm=LLM(pulse_plan=ModelError("truncated")))
    assert plan["queries"] == ["Dentist"] and plan["match_terms"] == ["dentist"]
    assert "truncated" in note and "industry's name only" in note
    plan, note = P.plan_queries(PROFILE, "DE", llm=LLM(pulse_plan={
        "queries": ["Zahnärzte"], "match_terms": [], "regulator_terms": []}))
    assert plan["queries"] == ["Zahnärzte"] and plan["match_terms"] == ["dentist"] and "incomplete" in note


# == Google News =======================================================================

def test_google_news_keeps_industry_headlines_once_and_counts_what_it_dropped():
    net = Net(google={
        "NHS dentistry": gnews(headline("Scramble for new NHS dentist as 14,000 join queue"),
                               headline("Scramble for new NHS dentist as 14,000 join queue", pub="ITV",
                                        site="https://www.itv.com"),
                               headline("Dental Implants Market Size, Share [2034]"),
                               headline("Town hall votes on parking charges")),
        "dental contract": gnews(headline("BDA rejects incremental NHS dental contract reform",
                                          pub="BDA", site="https://www.bda.org", day="2026-10-09"))})
    items, per_query, failures, dropped = P.read_google(PLAN, "GB", get=net.get, breaker=breaker())
    assert [i["title"] for i in items] == ["BDA rejects incremental NHS dental contract reform",
                                           "Scramble for new NHS dentist as 14,000 join queue"]
    assert items[1]["copies"] == 2 and dropped == {"noise": 1, "off_topic": 1} and failures == []
    assert [(q["items"], q["kept"]) for q in per_query] == [(4, 1), (1, 1)]
    assert all("when:30d" in unquote(u) and "gl=GB" in u for u in net.asked)


def test_a_query_google_refused_is_a_failure_and_an_open_breaker_reads_nothing_more():
    net = Net(google={"NHS dentistry": miss("blocked", "refused (HTTP 429)")})
    b = breaker()
    items, per_query, failures, _ = P.read_google(PLAN, "GB", get=net.get, breaker=b)
    assert items == [] and per_query[0]["status"] == "failed" and "429" in failures[0]
    b.open = True
    _, per_query, failures, _ = P.read_google(PLAN, "GB", get=net.get, breaker=b)
    assert [q["status"] for q in per_query] == ["not_read", "not_read"] and len(failures) == 2


# == trade publications =================================================================

# The comments feed is announced first, as WordPress themes often do.
TRADE_HOME = '<html><head><link rel="alternate" type="application/rss+xml" href="/comments/feed/">' \
             '<link rel="alternate" type="application/rss+xml" href="/feed/"></head></html>'


def trade_world():
    google = [headline("NHS dental contract talks", pub="Dentistry.co.uk", site="https://dentistry.co.uk"),
              headline("Dentists struck off", pub="Dentistry.co.uk", site="https://dentistry.co.uk"),
              headline("Dental queue grows", pub="The Mirror", site="https://www.mirror.co.uk"),
              headline("Dental hygienists quit", pub="The Mirror", site="https://www.mirror.co.uk"),
              headline("Dentist award", pub="Teesside Live", site="https://www.gazettelive.co.uk"),
              headline("Dental report", pub="IndexBox", site="https://www.indexbox.io"),
              headline("Dental report 2", pub="IndexBox", site="https://www.indexbox.io")]
    items = [dict(zip(("title", "publisher", "publisher_site", "date", "link"), h),
                  id=news._story_key(h[0])) for h in google]
    net = Net(pages={"https://dentistry.co.uk/": TRADE_HOME, "https://www.mirror.co.uk/": "<html></html>"},
              urls={"https://dentistry.co.uk/feed/": feed("Amalgam waste after 2034", "Private dentistry",
                                                          "Dentists and AI", "Practice sale"),
                    "https://www.mirror.co.uk/rss.xml": feed("Football", "Royals", "Dentist queue", "TV")})
    return items, net


def test_a_publisher_that_keeps_covering_the_industry_is_looked_up_once_and_remembered():
    items, net = trade_world()
    store = Store()
    got, report = P.read_trade("naics:621210", "GB", items, PLAN, store=store, fetch=net.fetch,
                               get=net.get, now=NOW)
    kinds = {r["site_domain"]: r["kind"] for r in store.sources}
    # One mention is not enough, and a market-research site is never looked up.
    assert kinds == {"dentistry.co.uk": "trade", "mirror.co.uk": "general"}
    assert not any("indexbox" in u or "gazettelive" in u for u in net.asked)
    assert not any("comments" in u for u in net.asked)
    (d,) = [x for x in report["discovered"] if x["trade"]]
    assert d["share"] == 0.5 and d["feed"] == "https://dentistry.co.uk/feed/"
    # A trade feed's items all count; a general feed is remembered but not read.
    assert len(got) == 4 and {g["source"] for g in got} == {"feed"}
    assert [r["publisher"] for r in report["read"]] == ["Dentistry.co.uk"]

    # The next pulse reads the trade feed straight away and looks up no one.
    net.asked.clear()
    got, report = P.read_trade("naics:621210", "GB", items, PLAN, store=store, fetch=net.fetch,
                               get=net.get, now=NOW + timedelta(days=1))
    assert net.asked == ["https://dentistry.co.uk/feed/"] and len(got) == 4
    assert report["read"][0]["new"] is False and report["discovered"] == []


def test_a_publisher_with_no_feed_is_remembered_and_asked_again_only_after_a_month():
    items, net = trade_world()
    del net.urls["https://dentistry.co.uk/feed/"]
    store = Store()
    _, report = P.read_trade("naics:621210", "GB", items, PLAN, store=store, fetch=net.fetch,
                             get=net.get, now=NOW)
    row = next(r for r in store.sources if r["site_domain"] == "dentistry.co.uk")
    assert row["kind"] == "none" and row["last_ok_at"] is None and report["failed"][0]["note"]
    net.asked.clear()
    P.read_trade("naics:621210", "GB", items, PLAN, store=store, fetch=net.fetch, get=net.get,
                 now=NOW + timedelta(days=5))
    assert not any("dentistry.co.uk" in u for u in net.asked)
    P.read_trade("naics:621210", "GB", items, PLAN, store=store, fetch=net.fetch, get=net.get,
                 now=NOW + timedelta(days=P.FEED_RETRY_DAYS + 1))
    assert any("dentistry.co.uk" in u for u in net.asked)


def test_a_known_trade_feed_that_fails_is_reported_and_old_items_are_left_out():
    store = Store()
    store.save_industry_source("naics:621210", "https://a.example/feed/", country="GB",
                               site_domain="a.example", kind="trade", discovered_from="A", ok=True)
    store.save_industry_source("naics:621210", "https://b.example/feed/", country="GB",
                               site_domain="b.example", kind="trade", discovered_from="B", ok=True)
    net = Net(urls={"https://a.example/feed/": miss("blocked", "refused (HTTP 403)"),
                    "https://b.example/feed/": ok(
                        "<rss><channel><item><title>Fresh</title><link>https://b.example/f</link>"
                        "<pubDate>Thu, 08 Oct 2026 08:00:00 +0000</pubDate></item>"
                        "<item><title>Old</title><link>https://b.example/o</link>"
                        "<pubDate>Mon, 01 Jun 2026 08:00:00 +0000</pubDate></item></channel></rss>")})
    got, report = P.read_trade("naics:621210", "GB", [], PLAN, store=store, fetch=net.fetch,
                               get=net.get, now=NOW)
    assert [g["title"] for g in got] == ["Fresh"]
    assert report["failed"] == [{"publisher": "A", "note": "refused (HTTP 403)"}]
    assert next(r for r in store.sources if r["site_domain"] == "a.example")["last_error"] == \
        "refused (HTTP 403)"


# == the Federal Register ===============================================================

def fr_doc(title, abstract="", kind="Rule", day="2026-09-23"):
    return {"title": title, "abstract": abstract, "type": kind, "publication_date": day,
            "agencies": [{"name": "Health and Human Services Department"}],
            "html_url": "https://www.federalregister.gov/d/%d" % abs(hash(title))}


def test_federal_rules_count_only_when_their_title_or_summary_names_the_industry():
    net = Net(urls={P.FEDREG_URL: {"results": [
        fr_doc("Patient Protection and Affordable Care Act; agent and broker moratoria",
               "pauses registration of agents and brokers"),
        fr_doc("Dental hygienist supervision in federal facilities", kind="Proposed Rule", day="2026-09-30"),
        fr_doc("Medicaid program", "coverage of dental services for adults")]}})
    docs, note = P.federal_register(["dental"], ["dentist"], get_json=net.get_json, now=NOW)
    assert [d["type"] for d in docs] == ["Proposed rule", "Final rule"]
    assert docs[0]["title"].startswith("Dental hygienist") and "2 rules" in note
    q = parse_qs(urlsplit(net.asked[0]).query)
    assert q["conditions[type][]"] == ["RULE", "PRORULE"] and q["conditions[publication_date][gte]"] == \
        ["2026-07-12"]


def test_a_federal_register_that_does_not_answer_is_not_an_empty_one():
    net = Net(urls={P.FEDREG_URL: miss("error", "timed out after 20s")})
    docs, note = P.federal_register(["dental"], [], get_json=net.get_json, now=NOW)
    assert docs is None and "did not answer" in note and "timed out" in note


# == themes ============================================================================

ARTS = [{"title": "Headline %d about dental care" % i, "publisher": "Pub %d" % (i % 3),
         "date": "2026-10-0%d" % (1 + i % 9), "link": "https://x.example/%d" % i, "source": "google_news",
         "copies": 1} for i in range(6)]


def test_themes_rest_on_at_least_two_given_headlines_each_used_once():
    llm = LLM(pulse_themes={"themes": [
        {"title": "NHS contract reform " + chr(0x2014) + " stalls", "summary": "s", "why_it_matters": "w",
         "kind": "regulation", "items": [0, 1, 1, 99]},
        {"title": "Weak", "summary": "s", "why_it_matters": "w", "kind": "demand", "items": [1, 2]},
        {"title": "Staffing", "summary": "s", "why_it_matters": "w", "kind": "workforce", "items": [3, 4, 6]},
        {"title": "Odd kind", "summary": "s", "why_it_matters": "w", "kind": "nonsense", "items": [5, 2]}]})
    reg = [{"title": "Dental rule", "type": "Final rule", "agency": "HHS", "date": "2026-09-01",
            "link": "https://fr.example/1"}]
    out, note = P.themes(ARTS, reg, label="Dentist", country="GB", llm=llm)
    assert [t["title"] for t in out] == ["NHS contract reform, stalls", "Staffing", "Odd kind"]
    assert [len(t["articles"]) for t in out] == [2, 3, 2] and out[2]["kind"] == "other"
    assert out[1]["articles"][-1]["title"].startswith("Final rule: Dental rule")
    assert "1 theme cited fewer than two" in note
    assert "6. 2026-09-01 | US Federal Register (HHS) | Final rule: Dental rule" in llm.calls[0]["user"]


def test_themes_that_could_not_be_written_are_none_not_empty():
    out, note = P.themes(ARTS, None, label="Dentist", country="GB", llm=LLM(pulse_themes=ModelError("refused")))
    assert out is None and "refused" in note
    assert P.themes(ARTS[:1], None, label="Dentist", country="GB", llm=LLM()) == \
        ([], "too few headlines to find themes")


# == one pulse, one client ================================================================

THEMES = {"themes": [{"title": "Access to NHS dentistry", "summary": "s", "why_it_matters": "w",
                      "kind": "demand", "items": [0, 1]}]}


def pulse_world():
    net = Net(google={"NHS dentistry": gnews(headline("Scramble for NHS dentist"),
                                             headline("More NHS dental care", day="2026-10-07")),
                      "dental contract": gnews(headline("BDA rejects dental contract reform"))},
              urls={P.FEDREG_URL: {"results": [fr_doc("Dental rule")]}})
    llm = LLM(pulse_plan=PLAN, pulse_themes=THEMES)
    return net, llm


def test_a_pulse_reads_news_and_groups_it_and_only_the_us_reads_federal_rules():
    net, llm = pulse_world()
    p = P.build_pulse(PROFILE, "naics:621210", "GB", store=Store(), get=net.get, get_json=net.get_json,
                      fetch=net.fetch, breaker=breaker(), now=NOW, llm=llm)
    assert p["status"] == "ok" and p["themes"][0]["title"] == "Access to NHS dentistry"
    assert p["regulation"] is None and not any("federalregister" in u for u in net.asked)
    assert p["note"].startswith("3 headlines about Dentist in the GB:en edition from 2 of 2 searches")
    net, llm = pulse_world()
    p = P.build_pulse(PROFILE, "naics:621210", "US", store=Store(), get=net.get, get_json=net.get_json,
                      fetch=net.fetch, breaker=breaker(), now=NOW, llm=llm)
    assert [d["title"] for d in p["regulation"]] == ["Dental rule"]


def test_a_pulse_whose_news_could_not_be_read_fails_and_is_not_an_empty_industry():
    net, llm = pulse_world()
    net.google = {}
    p = P.build_pulse(PROFILE, "naics:621210", "GB", store=Store(), get=net.get, get_json=net.get_json,
                      fetch=net.fetch, breaker=breaker(), now=NOW, llm=llm)
    assert p["status"] == "failed" and p["themes"] == [] and "no news could be read" in p["note"]
    assert [c["stage"] for c in llm.calls] == ["pulse_plan"]


def test_a_pulse_whose_themes_failed_is_partial_and_keeps_its_headlines():
    net, _ = pulse_world()
    llm = LLM(pulse_plan=PLAN, pulse_themes=ModelError("truncated"))
    p = P.build_pulse(PROFILE, "naics:621210", "GB", store=Store(), get=net.get, get_json=net.get_json,
                      fetch=net.fetch, breaker=breaker(), now=NOW, llm=llm)
    assert p["status"] == "partial" and len(p["articles"]) == 3 and "truncated" in p["note"]


def test_a_client_reuses_a_fresh_pulse_of_its_industry_and_country(monkeypatch):
    from tracker import market_radar_views as views
    monkeypatch.setattr(views, "effective_profile", lambda prof, settings: prof)
    store = Store()
    store.clients[1] = {"profile": PROFILE, "settings": {}}
    store.clients[2] = {"profile": dict(PROFILE, name="Another practice"), "settings": {}}
    net, llm = pulse_world()
    io = {"get": net.get, "get_json": net.get_json, "fetch": net.fetch}
    out = P.run_for_client(1, "o", store=store, io=io, now=NOW, llm=llm, breaker=breaker(), run_id=7)
    assert out["pulses"][0]["reused"] is False and store.pulses[0]["run_id"] == 7
    out = P.run_for_client(2, "o", store=store, io=io, now=NOW + timedelta(hours=3), llm=llm,
                           breaker=breaker())
    assert out["pulses"][0]["reused"] is True and len(store.pulses) == 1
    # A day later, or after a failed read, it is read again.
    P.run_for_client(2, "o", store=store, io=io, now=NOW + timedelta(hours=P.REUSE_HOURS + 1), llm=llm,
                     breaker=breaker())
    assert len(store.pulses) == 2
    # A failed read an hour ago is not reused.
    store.pulses[-1]["payload"]["status"] = "failed"
    store.pulses[-1]["created_at"] = NOW + timedelta(hours=P.REUSE_HOURS + 1)
    P.run_for_client(2, "o", store=store, io=io, now=NOW + timedelta(hours=P.REUSE_HOURS + 2),
                     llm=llm, breaker=breaker())
    assert len(store.pulses) == 3
    # A pulse read before the way pulses are read changed is read again.
    store.pulses[-1]["payload"]["version"] = P.PULSE_VERSION - 1
    P.run_for_client(2, "o", store=store, io=io, now=NOW + timedelta(hours=P.REUSE_HOURS + 3),
                     llm=llm, breaker=breaker())
    assert len(store.pulses) == 4 and store.pulses[-1]["payload"]["version"] == P.PULSE_VERSION
    store.clients[3] = {"profile": dict(PROFILE, industry={}), "settings": {}}
    assert "industry is not known" in P.run_for_client(3, "o", store=store, io=io, now=NOW)["skipped"]
    with pytest.raises(PermissionError):
        P.run_for_client(9, "o", store=store, io=io, now=NOW)


def test_a_pulse_that_breaks_does_not_lose_the_collection():
    from tracker import market_radar_run as mrun
    import tracker.market_radar_store as real
    saved = {}
    orig = real.update_run
    real.update_run = lambda run_id, **kw: saved.update(kw)
    try:
        def boom(*a, **k):
            raise RuntimeError("google down")
        mrun.collect_job(1, 2, "o", collect=lambda *a, **k: {"companies": []}, report=lambda *a, **k: {},
                         radar=lambda *a, **k: {"local": None}, pulse=boom)
    finally:
        real.update_run = orig
    assert saved["status"] == "complete" and "google down" in saved["summary"]["pulse"]["error"]
    assert saved["summary"]["radar"] == {"local": None}


def test_client_facing_pulse_text_has_no_em_dashes():
    import inspect
    for name in ("THEMES_SYSTEM", "PLAN_SYSTEM"):
        assert chr(0x2014) not in getattr(P, name) and chr(0x2013) not in getattr(P, name)
    assert chr(0x2014) not in inspect.getsource(P)
    assert P._clean("a %s b %s c" % (chr(0x2014), chr(0x2013)), 50) == "a, b, c"


def test_a_one_word_headline_is_news_but_googles_placeholder_is_not():
    assert not P.is_noise({"title": "Fresh", "publisher_site": "https://a.example"})
    assert not P.is_noise({"title": "NHS", "publisher_site": "https://a.example"})
    assert P.is_noise({"title": "META_TITLE_SECTORS", "publisher_site": "https://a.example"})


def test_the_prompts_ask_for_short_searches_and_english_themes():
    assert "1 to 3 words" in P.PLAN_SYSTEM and "product brand" in P.PLAN_SYSTEM
    assert "plain English even when the headlines are in another language" in P.THEMES_SYSTEM
    assert "shopping guides" in P.THEMES_SYSTEM and "share-price moves" in P.THEMES_SYSTEM
