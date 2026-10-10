"""Market Radar, Phase 11: a real browser for sites that refuse this server."""
import os
import sys

import pytest
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracker import market_radar_browser as B  # noqa: E402
from tracker import market_radar_site as site  # noqa: E402

from test_market_radar_profile import page, web, words  # noqa: E402,F401
from test_market_radar_store import OWNER, pg, world  # noqa: E402,F401


def html(title, body, links=()):
    a = "".join('<a href="%s">%s</a>' % (u, t) for u, t in links)
    return ('<html lang="en"><head><title>%s</title><script type="application/ld+json">'
            '{"@type":"Organization","name":"Acme"}</script></head><body><nav>%s</nav><p>%s</p>'
            '<footer><a href="https://www.linkedin.com/company/acme">LinkedIn</a></footer>'
            '</body></html>' % (title, a, body))


def item(url, title="Acme | Welding", body=None, code=200, loaded=None, links=()):
    return {"url": url, "html": html(title, body if body is not None else words(200), links),
            "metadata": {"title": title},
            "crawl": {"httpStatusCode": code, "loadedUrl": loaded or url}}


class FakeApify:
    def __init__(self, items=(), status="SUCCEEDED", charge=0.031, start_error=None):
        self.items_, self.status, self.charge, self.start_error = list(items), status, charge, start_error
        self.started_with, self.aborted = None, False

    def start(self, actor, run_input, params):
        if self.start_error:
            raise self.start_error
        self.started_with = (actor, run_input, params)
        return {"id": "run1"}

    def run(self, run_id):
        d = {"id": run_id, "status": self.status, "defaultDatasetId": "ds1"}
        if self.charge is not None:
            d["usageTotalUsd"] = self.charge
        return d

    def items(self, dataset_id):
        return self.items_

    def abort(self, run_id):
        self.aborted = True


def go(api, urls, **kw):
    return B.open_pages(urls, token="t", api=api, sleep=lambda s: None, **kw)


# -- the actor's input ---------------------------------------------------------

def test_the_run_keeps_the_pages_own_markup_and_is_bounded():
    api = FakeApify()
    go(api, ["https://a.example/", "https://a.example/", "https://b.example/"])
    actor, inp, params = api.started_with
    assert actor == B.ACTOR
    assert [u["url"] for u in inp["startUrls"]] == ["https://a.example/", "https://b.example/"]
    # the defaults strip scripts (JSON-LD), nav and footer (social links)
    assert inp["htmlTransformer"] == "none" and inp["saveHtml"] is True
    assert inp["removeElementsCssSelector"] == "dummy_keep_everything"
    assert inp["clickElementsCssSelector"] == ""
    assert inp["maxCrawlDepth"] == 0 and inp["maxCrawlPages"] == 2 and inp["maxRequestRetries"] == 0
    assert inp["requestTimeoutSecs"] == B.PAGE_TIMEOUT_S
    assert params == {"timeout": B.RUN_TIMEOUT_S, "memory": B.MEMORY_MB}


def test_at_most_max_pages_are_opened():
    api = FakeApify()
    go(api, ["https://a.example/%d" % i for i in range(9)])
    assert len(api.started_with[1]["startUrls"]) == B.MAX_PAGES


def test_the_booking_is_the_runs_memory_for_its_whole_limit():
    # 4 GB for 150 s at $0.40 a GB-hour
    assert float(B.booking_usd()) == pytest.approx(4 * 150 / 3600 * 0.40, abs=1e-4)


# -- what counts as the page ---------------------------------------------------

def test_a_block_page_served_with_200_is_not_the_page():
    """weg.net answered the browser "Access Denied" with HTTP 200."""
    got, why = B.page_from_item(item("https://www.weg.net/", title="Access Denied",
                                     body="You don't have permission to access this server. " + words(60)))
    assert got is None and "block page" in why and "Access Denied" in why
    got, why = B.page_from_item(item("https://x.example/", title="Just a moment...", body=words(80)))
    assert got is None and "block page" in why


def test_a_refusal_and_an_empty_shell_are_not_the_page():
    got, why = B.page_from_item(item("https://x.example/", code=403))
    assert got is None and "HTTP 403" in why
    got, why = B.page_from_item(item("https://x.example/", body=words(10)))
    assert got is None and "only" in why


def test_a_real_page_is_shaped_like_a_fetch_with_its_links():
    got, why = B.page_from_item(item("https://new.abb.com/", loaded="https://www.abb.com/global/en",
                                     links=[("/about", "About us")]))
    assert why is None and got["status"] == "ok" and got["via"] == "browser"
    assert got["final_url"] == "https://www.abb.com/global/en"
    assert "application/ld+json" in got["html"] and "linkedin.com/company/acme" in got["html"]
    assert "https://www.abb.com/about" in got["text"]          # links resolve on the final address


# -- one run -------------------------------------------------------------------

def test_a_run_cut_off_at_its_limit_keeps_what_it_loaded():
    """Clove Dental hung until the live run's time ran out; the four pages
    it had already loaded were good."""
    urls = ["https://new.abb.com/", "https://www.weg.net/", "https://clovedental.in/"]
    api = FakeApify(status="TIMED-OUT", items=[
        item("https://new.abb.com/", loaded="https://www.abb.com/global/en"),
        item("https://www.weg.net/", title="Access Denied", body=words(60))])
    out = go(api, urls)
    assert set(out["pages"]) == {"https://new.abb.com/"}
    assert out["pages"]["https://new.abb.com/"]["url"] == "https://new.abb.com/"
    assert "block page" in out["missed"]["https://www.weg.net/"]
    assert out["missed"]["https://clovedental.in/"] == "the browser could not load it in time"
    assert out["charged_usd"] == 0.031


def test_a_page_is_matched_by_the_address_it_ended_on():
    out = go(FakeApify(items=[dict(item("https://x.example/", loaded="https://www.x.example/"),
                                   url=None)]), ["https://x.example/"])
    assert list(out["pages"]) == ["https://x.example/"]


def test_no_token_starts_nothing(monkeypatch):
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    out = B.open_pages(["https://a.example/"])
    assert out["error"] == "APIFY_API_TOKEN is not set" and out["pages"] == {}
    assert B.for_run(1) is None


def test_the_charge_apify_reports_replaces_the_booking(world):
    from tracker import market_radar_ledger as ledger
    go(FakeApify(items=[item("https://a.example/")], charge=0.0123), ["https://a.example/"],
       run_id=world["run"])
    s = ledger.summary(world["run"])
    assert s["by_provider"] == {"apify": 0.0123} and not s["partial"]


def test_a_refused_start_costs_nothing_and_an_unread_charge_counts_at_its_booking(pg):
    from tracker import market_radar_ledger as ledger
    me = pg.upsert_entity("acme.example")
    client = pg.upsert_client(OWNER, me)
    resp = requests.Response(); resp.status_code = 402
    run = pg.create_run(client, OWNER, "collect")
    out = go(FakeApify(start_error=requests.HTTPError("402", response=resp)), ["https://a.example/"],
             run_id=run)
    assert out["start"] == "refused" and ledger.summary(run)["total_usd"] == 0
    run = pg.create_run(client, OWNER, "collect")
    go(FakeApify(charge=None), ["https://a.example/"], run_id=run)
    s = ledger.summary(run)
    assert s["partial"] and s["total_usd"] == pytest.approx(float(B.booking_usd()))


def test_a_browser_run_that_could_pass_the_cap_never_starts(pg):
    from tracker import market_radar_ledger as ledger
    me = pg.upsert_entity("acme.example")
    client = pg.upsert_client(OWNER, me)
    run = pg.create_run(client, OWNER, "collect", cost_cap_usd="0.05")
    api = FakeApify()
    with pytest.raises(ledger.BudgetExceeded):
        go(api, ["https://a.example/"], run_id=run)
    assert api.started_with is None


# -- the allowance ---------------------------------------------------------------

def test_the_allowance_counts_companies_not_runs():
    calls = []
    allowance = B.Allowance(2)

    def opener(urls):
        calls.append(urls)
        return {"pages": {}, "missed": {}}

    a, b, c = (B.for_run(1, allowance, opener=opener) for _ in range(3))
    assert a(["https://a/"]) is not None and a(["https://a/x"], True) is not None
    assert a(["https://a/"]) is not None          # the same company again is not a new one
    assert b(["https://b/"]) is not None
    assert c(["https://c/"]) is None              # the third company: the allowance is spent
    assert len(calls) == 4


# -- the site reader ---------------------------------------------------------------

REFUSED = dict(status="blocked", http=403)


def browser_with(pages, missed=None, log=None):
    def browser(urls, more=False):
        if log is not None:
            log.append((list(urls), more))
        return {"pages": {u: pages[u] for u in urls if u in pages},
                "missed": {u: (missed or {}).get(u, "not loaded") for u in urls if u not in pages}}
    return browser


def browsed(url, body=None, links=()):
    p, _ = B.page_from_item(item(url, body=body, links=links))
    return p


def test_a_refusing_site_is_read_in_a_browser_before_the_archive(web, monkeypatch):
    web.pages["https://acme.example/"] = page("https://acme.example/", "", **REFUSED)
    monkeypatch.setattr(site, "fetch_archived", lambda url: pytest.fail("the archive was read"))
    log = []
    home = browsed("https://acme.example/", links=[("/about", "About us"), ("/pricing", "Pricing")])
    rs = site.read_site("https://acme.example/", browser=browser_with({"https://acme.example/": home},
                                                                       log=log))
    assert rs["status"] == "ok" and rs["via_browser"] and not rs["via_archive"]
    assert rs["pages"][0]["via"] == "browser" and not rs["needs_browser"]
    assert rs["signals"]["socials"].get("linkedin") == "acme"
    # a collection pays for the homepage only: no other page is opened
    assert log == [(["https://acme.example/"], False)] and len(rs["pages"]) == 1


def test_a_profile_opens_its_other_pages_in_the_same_browser(web, monkeypatch):
    web.pages["https://acme.example/"] = page("https://acme.example/", "", **REFUSED)
    monkeypatch.setattr(site, "fetch_archived", lambda url: pytest.fail("the archive was read"))
    log = []
    home = browsed("https://acme.example/", links=[("/about", "About us"), ("/pricing", "Pricing"),
                                                  ("/contact", "Contact")])
    pages = {"https://acme.example/": home,
             "https://acme.example/about": browsed("https://acme.example/about")}
    rs = site.read_site("https://acme.example/", browser=browser_with(pages, log=log),
                        browser_pages=2)
    assert len(log) == 2 and log[1][1] is True and len(log[1][0]) == 2
    kinds = {p["url"]: p for p in rs["pages"][1:]}
    assert kinds["https://acme.example/about"]["status"] == "ok"
    assert [p for p in rs["pages"][1:] if p["status"] != "ok"][0]["note"] == "not loaded"
    assert len(rs["texts"]) == 2


def test_when_the_browser_is_refused_too_the_archive_still_answers(web, monkeypatch):
    web.pages["https://acme.example/"] = page("https://acme.example/", "", **REFUSED)
    archived = dict(page("https://acme.example/", html("Acme", words(300))),
                    via="wayback:20260901000000", note="read from the Wayback Machine")
    monkeypatch.setattr(site, "fetch_archived", lambda url: dict(archived, final_url=url))
    rs = site.read_site("https://acme.example/", browser=browser_with(
        {}, missed={"https://acme.example/": "the site showed the browser a block page (Access Denied)"}))
    assert rs["status"] == "ok" and rs["via_archive"] and not rs["via_browser"]
    assert any("a real browser could not read it either" in n and "Access Denied" in n
               for n in rs["home_notes"])


def test_when_nothing_reads_it_the_reasons_say_every_attempt(web, monkeypatch):
    web.pages["https://acme.example/"] = page("https://acme.example/", "", **REFUSED)
    monkeypatch.setattr(site, "fetch_archived", lambda url: dict(
        site.fetch_failed(url), note="the Wayback Machine has no readable copy"))
    rs = site.read_site("https://acme.example/", browser=browser_with(
        {}, missed={"https://acme.example/": "the browser could not load it in time"}))
    assert rs["status"] == "blocked"
    note = rs["pages"][0]["note"]
    assert "could not load it in time" in note and "no readable copy" in note


def test_a_spent_allowance_falls_back_to_the_archive(web, monkeypatch):
    web.pages["https://acme.example/"] = page("https://acme.example/", "", **REFUSED)
    archived = dict(page("https://acme.example/", html("Acme", words(300))), via="wayback:1")
    monkeypatch.setattr(site, "fetch_archived", lambda url: dict(archived, final_url=url))
    rs = site.read_site("https://acme.example/", browser=lambda urls, more=False: None)
    assert rs["status"] == "ok" and rs["via_archive"] and not rs["via_browser"]


def test_a_page_built_in_the_browser_is_read_there(web):
    web.pages["https://acme.example/"] = page("https://acme.example/", html("Acme", words(20)))
    full = browsed("https://acme.example/", body=words(400))
    rs = site.read_site("https://acme.example/", browser=browser_with({"https://acme.example/": full}))
    assert rs["via_browser"] and not rs["needs_browser"]
    assert rs["pages"][0]["words"] > 400
    assert "built there" in rs["pages"][0]["note"]


def test_a_full_page_never_opens_the_browser(web):
    web.pages["https://acme.example/"] = page("https://acme.example/", html("Acme", words(400)))
    rs = site.read_site("https://acme.example/", browser=lambda *a: pytest.fail("browser opened"))
    assert rs["status"] == "ok" and not rs["via_browser"]


# -- wiring --------------------------------------------------------------------

def test_a_collection_reads_sites_with_a_browser_and_one_allowance(monkeypatch):
    from tracker import market_radar_collect as mc
    seen = []
    monkeypatch.setattr(site, "read_site", lambda url, browser=None, browser_pages=0:
                        seen.append((url, browser, browser_pages)) or {"status": "blocked",
                                                                       "pages": []})
    allowance = B.Allowance(1)

    class Store:
        def latest_snapshot(self, *a):
            return None

    io = {"browser_allowance": allowance, "get": None, "get_json": None, "fetch": None}
    monkeypatch.setenv("APIFY_API_TOKEN", "t")
    mc.collect_entity({"id": 1, "domain": "acme.example"}, io=dict(io), store=Store(),
                      only={"pages"})
    (url, browser, n), = seen
    assert url == "https://acme.example" and browser is not None and n == 0


def test_a_profile_pays_for_its_other_pages(monkeypatch):
    from tracker import market_radar_profile as prof
    seen = {}
    monkeypatch.setattr(prof.site_reader, "read_site", lambda url, browser=None, browser_pages=0:
                        seen.update(browser=browser, n=browser_pages) or {"status": "blocked",
                                                                          "pages": []})
    out = prof.build_profile("https://x.example/", save=False, browser=lambda urls, more=False: None,
                             apollo_enrich=lambda *a, **k: None)
    assert out["status"] == "unreadable" and seen["n"] == prof.BROWSER_PAGES


def test_a_browser_read_thinner_than_the_plain_one_is_not_used(web):
    web.pages["https://acme.example/"] = page("https://acme.example/", html("Acme", words(100)))
    thin = browsed("https://acme.example/", body=words(50))
    rs = site.read_site("https://acme.example/", browser=browser_with({"https://acme.example/": thin}))
    assert not rs["via_browser"] and rs["needs_browser"] and rs["pages"][0]["via"] is None


def test_a_short_page_read_in_a_browser_does_not_ask_for_a_browser(web, monkeypatch):
    web.pages["https://acme.example/"] = page("https://acme.example/", "", **REFUSED)
    monkeypatch.setattr(site, "fetch_archived", lambda url: pytest.fail("the archive was read"))
    short = browsed("https://acme.example/", body=words(90))
    rs = site.read_site("https://acme.example/", browser=browser_with({"https://acme.example/": short}))
    assert rs["via_browser"] and not rs["needs_browser"]


def test_a_browser_run_that_never_started_says_why(web, monkeypatch):
    """A refused start returns no per-page reasons at all."""
    web.pages["https://acme.example/"] = page("https://acme.example/", "", **REFUSED)
    monkeypatch.setattr(site, "fetch_archived", lambda url: dict(
        site.fetch_failed(url), note="the Wayback Machine has no readable copy"))
    rs = site.read_site("https://acme.example/", browser=lambda urls, more=False: {
        "pages": {}, "missed": {}, "error": "Apify refused the run: HTTP 402"})
    assert "the browser failed (Apify refused the run: HTTP 402)" in rs["pages"][0]["note"]


# -- block pages anywhere -----------------------------------------------------------

AKAMAI = ('<html><head><title>Access Denied</title></head><body><h1>Access Denied</h1>'
          "You don't have permission to access \"http://www.weg.net/\" on this server.<p>"
          'Reference #18.6f2d3417.1760099087.1a2b3c</p></body></html>')


def fake_public_get(markup, code=200):
    class Resp:
        status_code = code
        headers = {"Content-Type": "text/html; charset=utf-8"}
        url = "https://www.weg.net/"
        encoding = "utf-8"

        def iter_content(self, n):
            yield markup.encode()

        def close(self):
            pass

    return lambda url, **kw: Resp()


def test_a_block_page_served_with_200_is_a_refusal(monkeypatch):
    monkeypatch.setattr(site, "public_get", fake_public_get(AKAMAI))
    got = site.fetch("https://www.weg.net/")
    assert got["status"] == "blocked" and got["wall"] and got["text"] == ""
    assert "block page (Access Denied)" in got["note"]
    assert site.refuses_us(got)


def test_an_archived_block_page_is_not_the_site(monkeypatch):
    """The Wayback Machine's latest weg.net is Akamai's notice; the profile
    was read from it and came out empty."""
    monkeypatch.setattr(site, "wayback_latest", lambda url: ("20260901000000", url))
    monkeypatch.setattr(site, "public_get", fake_public_get(AKAMAI))
    got = site.fetch_archived("https://www.weg.net/")
    assert got["status"] != "ok" and not got.get("wall")
    assert "latest copy is the site's block page" in got["note"]


def test_a_real_page_that_mentions_a_captcha_is_still_the_page(monkeypatch):
    body = "<title>Acme | Bot protection</title><p>Our captcha stops bots. %s</p>" % words(400)
    monkeypatch.setattr(site, "public_get", fake_public_get(body))
    assert site.fetch("https://acme.example/")["status"] == "ok"
    short = "<title>Acme</title><p>Our captcha stops bots. %s</p>" % words(120)
    monkeypatch.setattr(site, "public_get", fake_public_get(short))
    assert site.fetch("https://acme.example/")["status"] == "blocked"


def test_a_site_that_serves_a_block_page_goes_to_the_browser(web, monkeypatch):
    web.pages["https://acme.example/"] = dict(page("https://acme.example/", "", status="blocked",
                                                   http=200), wall=True)
    monkeypatch.setattr(site, "fetch_archived", lambda url: pytest.fail("the archive was read"))
    home = browsed("https://acme.example/")
    rs = site.read_site("https://acme.example/", browser=browser_with({"https://acme.example/": home}))
    assert rs["status"] == "ok" and rs["via_browser"]
    assert "the site refused this server (it served a block page)" in rs["home_notes"]
