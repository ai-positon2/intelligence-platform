"""Landing Page & Relevancy Check: how an address becomes a page, and what is done with an address that cannot be fetched.

Found by checking the agent against real accounts: Gentle Dental's ads send people to /ols?location_title=quincy and /ols?location_title=natick, which are
different pages that used to be counted as one, so the report checked one town's page and spoke for all of them."""
import pytest

from tests.lp_fakes import page
from tests.test_paid_lp import Fetcher, GOOD_HTML, Resp, fresh, run, search
from tests.test_paid_lp_read import ad_row, kw_row, page_row
from tracker import paid_lp_checks as checks
from tracker import paid_lp_page as pg
from tracker import paid_lp_read as r
from tracker.paid_lp_checks_page import is_test_site


@pytest.fixture(autouse=True)
def fresh_caches():
    fresh()
    yield
    fresh()


# ── What makes a page one page ──────────────────────────────────────────────


def test_pages_that_a_site_tells_apart_by_a_parameter_are_different_pages():
    a = r.page_id("https://www.gentledental.com/ols?location_title=south-nashua&state=nh&utm_source=GoogleAds&utm_term={Keyword}")
    b = r.page_id("https://www.gentledental.com/ols?location_title=somerville&state=ma&utm_source=GoogleAds&utm_term={Keyword}")
    assert a != b
    assert a == "gentledental.com/ols?location_title=south-nashua&state=nh"


def test_the_same_page_with_other_tracking_is_one_page():
    base = "https://a.test/x?loc=quincy"
    assert r.page_id(base) == r.page_id(base + "&utm_source=g&utm_campaign=Brand&gclid=abc&utm_term={keyword}&fbclid=1&msclkid=2&_gl=3")
    assert r.page_id("https://a.test/x?utm_source=1") == r.page_id("https://a.test/x") == "a.test/x"


def test_a_parameter_that_only_varies_one_page_does_not_make_a_second_page():
    for q in ("variant=1234", "currency=USD", "country=US", "sort=price", "page=2", "q=turbo", "search=turbo", "constraint=ford", "lang=en", "color=red"):
        assert r.page_id("https://a.test/products/turbo?" + q) == "a.test/products/turbo", q


def test_the_value_of_a_page_choosing_parameter_is_not_case_sensitive_and_its_name_too():
    assert r.page_id("https://a.test/x?Loc=Quincy") == r.page_id("https://a.test/x?loc=quincy")


def test_several_page_choosing_parameters_are_sorted():
    assert r.page_id("https://a.test/ols?state=ma&location_title=quincy") == r.page_id("https://a.test/ols?location_title=quincy&state=ma")


def test_an_empty_or_unfilled_page_choosing_parameter_is_not_part_of_the_page():
    assert r.page_id("https://a.test/x?loc=") == "a.test/x"
    assert r.page_id("https://a.test/x?loc={_city}") == "a.test/x"
    assert r.page_id("https://a.test/x?loc=%7B_city%7D") == "a.test/x"
    assert r.page_id("https://a.test/x?loc=%20") == "a.test/x" and r.page_id("https://a.test/x?loc=+&state=ma") == "a.test/x?state=ma"


def test_www_the_scheme_a_default_port_a_slash_and_a_fragment_do_not_make_another_page():
    ids = {r.page_id(u) for u in ("https://www.a.test/x", "http://a.test/x", "https://a.test:443/x", "http://a.test:80/x/", "https://A.TEST/x#top", "https://user@a.test/x")}
    assert ids == {"a.test/x"}


def test_a_port_that_is_not_the_default_is_a_different_site():
    assert r.page_id("https://a.test:8443/x") == "a.test:8443/x"
    assert r.page_id("http://a.test:443/x") == "a.test:443/x"           # 443 is only the default for https


def test_percent_encoding_does_not_make_another_page():
    assert r.page_id("https://a.test/caf%C3%A9") == r.page_id("https://a.test/café")


def test_a_path_keeps_its_case_because_a_server_may_tell_pages_apart_by_it():
    assert r.page_id("https://a.test/Page") != r.page_id("https://a.test/page")


def test_the_name_shown_is_the_one_of_the_address_that_spent_most():
    got = r.parse_pages([page_row("https://a.test/x", 5.0), page_row("https://www.a.test/x", 50.0), page_row("http://a.test/x?utm=1", 7.0)])[0]
    assert got["name"] == "www.a.test/x" and got["url"] == "https://www.a.test/x"


def test_a_page_is_named_the_way_the_ad_writes_it():
    assert r.label("https://WWW.a.test/ols/?location_title=Quincy&utm_source=g") == "www.a.test/ols?location_title=quincy"
    assert r.label("https://a.test") == "a.test/"


# ── Cleaning an address ─────────────────────────────────────────────────────

def test_a_placeholder_in_the_query_is_left_out_not_replaced_by_an_invented_value():
    assert r.clean("https://a.test/x?utm_term={Keyword}&utm_content={creative}&gclid={gclid}&city=quincy#t") == "https://a.test/x?city=quincy"
    assert r.clean("https://a.test/x?a=1&b={_b}&c=3") == "https://a.test/x?a=1&c=3"
    assert r.clean("https://a.test/x?a=%7B_b%7D") == "https://a.test/x"
    assert r.clean("https://a.test/x?q={a}b") == "https://a.test/x"


def test_cleaning_leaves_a_plain_address_as_it_is():
    assert r.clean("  https://a.test/x?a=1&b=two%20words ") == "https://a.test/x?a=1&b=two%20words"
    assert r.clean("") == "" and r.clean(None) == ""


def test_a_placeholder_in_the_host_or_path_is_kept_and_marks_the_address():
    assert r.templated("https://{_domain}/offer") and r.templated("https://a.test/{_page}") and r.templated("https://a.test/lp/{_slug}/x")
    assert r.clean("https://{_domain}/offer?x={a}") == "https://{_domain}/offer"
    assert not r.templated("https://a.test/x?q={keyword}") and not r.templated("https://a.test/x") and not r.templated("")


def test_two_different_templated_addresses_are_two_pages():
    assert r.page_id("https://{_a}/offer") != r.page_id("https://{_b}/offer")
    assert r.page_id("https://a.test/{_page}") == "a.test/{_page}"


def test_an_address_that_is_only_a_placeholder_is_not_a_page():
    assert r._valid("{lpurl}?x=1") == "" and r._valid("a.test/x") == "" and r._valid("//a.test/x") == "" and r._valid("ftp://a.test/x") == ""


def test_parse_pages_names_each_page_and_marks_a_templated_one():
    rows = [page_row("https://www.a.test/ols?location_title=quincy&utm_term={k}", 30), page_row("https://a.test/ols?location_title=natick", 20), page_row("https://a.test/{_p}", 10)]
    got = {p["id"]: p for p in r.parse_pages(rows)}
    assert set(got) == {"a.test/ols?location_title=quincy", "a.test/ols?location_title=natick", "a.test/{_p}"}
    assert got["a.test/ols?location_title=quincy"]["name"] == "www.a.test/ols?location_title=quincy"
    assert got["a.test/ols?location_title=quincy"]["url"] == "https://www.a.test/ols?location_title=quincy"
    assert got["a.test/{_p}"]["templated"] and not got["a.test/ols?location_title=natick"]["templated"]


def test_a_keyword_with_its_own_address_reaches_that_page_not_its_siblings():
    pages = r.parse_pages([page_row("https://a.test/ols?location_title=quincy", 30), page_row("https://a.test/ols?location_title=natick", 20)])
    groups = r.parse_ads([ad_row("g1", "Dentist", "Search", "https://a.test/ols?location_title=quincy", 30)])
    kws = r.parse_keywords([kw_row("g1", "dentist natick", 5, urls=["https://a.test/ols?location_title=natick"])])
    kept, used = r.join(pages, groups, kws, {})
    by = {p["id"]: p["adGroups"] for p in kept}
    assert by["a.test/ols?location_title=quincy"] == ["g1"] and by["a.test/ols?location_title=natick"] == ["g1"]
    assert set(used["g1"]["pages"]) == {"a.test/ols?location_title=quincy", "a.test/ols?location_title=natick"}


# ── In the report ───────────────────────────────────────────────────────────

def _report(pages, ads, fetcher=None):
    fetcher = fetcher or Fetcher()
    return run(search_fn=search(pages=pages, ads=ads, keywords=[]), fetcher=fetcher), fetcher


def test_each_town_page_is_fetched_with_its_own_address_and_reported_on_its_own():
    pages = [page_row("https://acme-dental.com/ols?location_title=quincy&utm_term={Keyword}", 600.0, clicks=40), page_row("https://acme-dental.com/ols?location_title=natick", 300.0, clicks=20)]
    ads = [ad_row("g1", "Quincy", "Search", "https://acme-dental.com/ols?location_title=quincy&utm_term={Keyword}", 600.0), ad_row("g2", "Natick", "Search", "https://acme-dental.com/ols?location_title=natick", 300.0)]
    rep, fetcher = _report(pages, ads)
    assert [p["name"] for p in rep["pages"]] == ["acme-dental.com/ols?location_title=quincy", "acme-dental.com/ols?location_title=natick"]
    assert rep["summary"]["pagesOfSpend"] == 2 and rep["summary"]["checked"] == 2
    got = sorted(u for u in fetcher.calls if "/ols" in u and "gclid" not in u)
    assert got == ["https://acme-dental.com/ols?location_title=natick", "https://acme-dental.com/ols?location_title=quincy"]
    assert not any("{" in u or "%7B" in u for u in fetcher.calls)


def test_an_address_with_a_placeholder_in_its_path_is_not_fetched_and_is_not_called_broken():
    pages = [page_row("https://acme-dental.com/{_landing}", 700.0, clicks=40), page_row("https://acme-dental.com/lp", 300.0, clicks=20)]
    ads = [ad_row("g1", "A", "Search", "https://acme-dental.com/{_landing}", 700.0)]
    rep, fetcher = _report(pages, ads)
    bad = next(p for p in rep["pages"] if p["id"] == "acme-dental.com/{_landing}")
    assert not any("_landing" in u or "{" in u for u in fetcher.calls)
    status = next(f for s in bad["sections"] for f in s["findings"] if f["id"] == "reach_status")
    assert status["result"] == "unknown" and "placeholder" in status["found"] and "Google fills" in status["found"]
    assert not bad["rated"] and bad["grade"] != "F" and bad["score"] != 0
    assert rep["summary"]["pagesAtRisk"] == 0 and not rep["summary"]["critical"]
    assert rep["summary"]["spendUnverified"] >= 700.0


def test_an_address_with_a_placeholder_for_the_whole_host_is_not_called_unresolvable():
    rep, fetcher = _report([page_row("https://{_domain}/offer", 700.0, clicks=40)], [ad_row("g1", "A", "Search", "https://{_domain}/offer", 700.0)])
    p = rep["pages"][0]
    assert fetcher.calls == [] and p["checked"] and not p["rated"] and rep["summary"]["pagesAtRisk"] == 0
    assert all(f["result"] in ("unknown", "na") for s in p["sections"] for f in s["findings"] if f["id"] != "reach_staging")


def test_a_placeholder_in_the_query_does_not_change_the_page_that_is_fetched():
    rep, fetcher = _report([page_row("https://acme-dental.com/lp?city={_city}&utm_term={Keyword}", 700.0, clicks=40)],
                           [ad_row("g1", "A", "Search", "https://acme-dental.com/lp?city={_city}&utm_term={Keyword}", 700.0)])
    assert "https://acme-dental.com/lp" in fetcher.calls
    assert rep["pages"][0]["grade"] != "F"


def test_a_page_reached_with_and_without_www_is_one_page_and_is_fetched_once():
    pages = [page_row("https://www.acme-dental.com/lp", 500.0, clicks=30), page_row("https://acme-dental.com/lp", 300.0, clicks=20)]
    rep, fetcher = _report(pages, [ad_row("g1", "A", "Search", "https://acme-dental.com/lp", 800.0)])
    assert len(rep["pages"]) == 1 and rep["pages"][0]["name"] == "www.acme-dental.com/lp" and rep["pages"][0]["spend"] == 800.0
    assert len([u for u in fetcher.calls if u.endswith("/lp")]) == 1


def test_the_report_says_how_a_page_is_told_from_another_and_names_the_addresses_it_could_not_fetch():
    pages = [page_row("https://acme-dental.com/{_landing}", 700.0, clicks=40), page_row("https://acme-dental.com/lp", 300.0, clicks=20)]
    rep, _f = _report(pages, [ad_row("g1", "A", "Search", "https://acme-dental.com/{_landing}", 700.0)])
    assert any("A page is its address without the tracking tags" in n for n in rep["notes"])
    note = next(n for n in rep["notes"] if "fills in when someone clicks" in n)
    assert note.startswith("1 page sends people to an address Google fills in") and "it was not fetched" in note and "not verified" in note
    assert next(p for p in rep["pages"] if p["id"] == "acme-dental.com/{_landing}")["seen"]["templated"] is True
    fresh()
    rep, _f = _report([page_row("https://acme-dental.com/lp", 300.0)], [ad_row("g1", "A", "Search", "https://acme-dental.com/lp", 300.0)])
    assert not any("fills in when someone clicks" in n for n in rep["notes"])
    fresh()
    two = [page_row("https://{_a}/x", 5.0), page_row("https://acme-dental.com/{_b}", 4.0)]
    rep, _f = _report(two, [])
    assert next(n for n in rep["notes"] if "fills in when someone clicks" in n).startswith("2 pages send people") and "they were not fetched" in next(n for n in rep["notes"] if "fills in" in n)


# ── Where a request ends up ─────────────────────────────────────────────────

class Moves:
    """A fetcher that answers every address with the page at `to`, as a redirect would."""

    def __init__(self, to):
        self.to, self.calls = to, []

    def __call__(self, url, **kw):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return Resp(404, url, b"", {"Content-Type": "text/plain"})
        return Resp(200, self.to + ("?" + url.split("?", 1)[1] if "?" in url else ""), GOOD_HTML)


def test_http_to_https_is_a_redirect_and_the_ad_is_asked_to_use_https():
    rec = pg.fetch("http://acme-dental.com/lp", Moves("https://acme-dental.com/lp"))
    assert rec["redirected"] is True and rec["paramsSurvive"] is True
    f = page(fetch_over={"url": "http://acme-dental.com/lp", "redirected": True, "finalUrl": "https://acme-dental.com/lp"}, tech_over={"https": True})
    out = {x["id"]: x for x in checks.run(f)}
    assert out["reach_https"]["result"] == "warn" and "uses plain http" in out["reach_https"]["found"]
    assert out["reach_redirect"]["result"] == "warn"


def test_an_https_ad_address_on_an_https_page_passes_https():
    f = page(fetch_over={"url": "https://acme-dental.com/lp", "finalUrl": "https://acme-dental.com/lp"}, tech_over={"https": True})
    assert {x["id"]: x for x in checks.run(f)}["reach_https"]["result"] == "pass"


def test_the_apex_to_www_move_is_a_redirect():
    assert pg.fetch("https://acme-dental.com/lp", Moves("https://www.acme-dental.com/lp"))["redirected"] is True


def test_a_trailing_slash_a_default_port_and_the_query_are_not_a_redirect():
    for to in ("https://acme-dental.com/lp/", "https://acme-dental.com:443/lp"):
        assert pg.fetch("https://acme-dental.com/lp?a=1", Moves(to))["redirected"] is False, to
    assert pg.fetch("http://acme-dental.com:80/lp", Moves("http://acme-dental.com/lp"))["redirected"] is False


def test_a_move_to_the_home_page_is_still_collapsed():
    rec = pg.fetch("https://acme-dental.com/lp", Moves("https://acme-dental.com/"))
    assert rec["redirected"] and rec["collapsed"]


# ── A test site ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("host", ["dev.acme.com", "staging.acme.com", "acme-staging.com", "www.dev.acme.com", "test2.acme.com", "uat.acme.co.uk", "staging2.acme.com", "acme-staging2.com", "dev-staging.acme.com", "acme.staging.host.io", "qa.acme.com",
                                  "preview.acme.com", "sandbox.acme.com", "acme-uat.com", "stage.acme.com", "localhost", "192.168.1.5", "x.local", "shop.acme.test"])
def test_a_test_or_staging_address_is_one(host):
    assert is_test_site(host)


@pytest.mark.parametrize("host", ["www.acme.com", "test-prep.com", "www.qa-solutions.com", "dev-tools.io", "preview-homes.com", "stagecoachdental.com", "www.stage.com", "acme.com",
                                  "testing.acme.com", "contest.acme.com", "latest.acme.com", "us.bddiesel.com", "promo.dieselperformance.com", "www.acme.co.uk", "", None])
def test_a_business_with_one_of_those_words_in_its_name_is_not_one(host):
    assert not is_test_site(host)


def test_the_staging_check_reads_the_ad_address_and_where_it_ended_up():
    f = page(fetch_over={"url": "https://www.test-prep.com/lp", "finalUrl": "https://www.test-prep.com/lp"}, url="https://www.test-prep.com/lp")
    assert {x["id"]: x for x in checks.run(f)}["reach_staging"]["result"] == "pass"
    f = page(fetch_over={"finalUrl": "https://staging.acme-dental.com/lp"})
    assert {x["id"]: x for x in checks.run(f)}["reach_staging"]["result"] == "fail"


# ── Does the page really open ───────────────────────────────────────────────

@pytest.fixture
def no_pause(monkeypatch):
    monkeypatch.setattr(pg, "RETRY_PAUSE_S", 0)


class Answers:
    """A fetcher that gives the page's answers in turn (the last one is repeated); robots.txt is not there."""

    def __init__(self, *answers):
        self.answers, self.pages = list(answers), 0

    def __call__(self, url, **kw):
        if url.endswith("/robots.txt"):
            return Resp(404, url, b"", {"Content-Type": "text/plain"})
        self.pages += 1
        status, body, headers = (self.answers.pop(0) if len(self.answers) > 1 else self.answers[0])
        return Resp(status, url, body, headers or {"Content-Type": "text/html"})


def _status(rec, **over):
    f = page(fetch_over=dict(rec, **over))
    return {x["id"]: x for x in checks.run(f)}["reach_status"]


def test_one_server_error_is_asked_again_before_it_is_held_against_the_page(no_pause):
    f = Answers((503, b"<html>down</html>", None), (200, GOOD_HTML, None))
    rec = pg.fetch("https://acme-dental.com/lp", f)
    assert f.pages == 2 and rec["status"] == 200 and rec["retried"] is True and rec["isHtml"] and not rec["blocked"]


def test_a_page_that_keeps_answering_with_a_server_error_is_asked_twice_and_fails(no_pause):
    f = Answers((500, b"<html>down</html>", None))
    rec = pg.fetch("https://acme-dental.com/lp", f)
    assert f.pages == 2 and rec["status"] == 500 and rec["retried"] is True and not rec["blocked"]
    assert _status(rec)["result"] == "fail"


def test_every_server_error_status_is_asked_again(no_pause):
    for status in (500, 502, 503, 504, 599):
        f = Answers((status, b"x", None), (200, GOOD_HTML, None))
        assert pg.fetch("https://acme-dental.com/lp", f)["status"] == 200 and f.pages == 2, status


def test_a_missing_page_or_a_refusal_is_not_asked_again(no_pause):
    for status in (404, 410, 403, 429, 401):
        f = Answers((status, b"<html>x</html>", None))
        rec = pg.fetch("https://acme-dental.com/lp", f)
        assert f.pages == 1 and rec["retried"] is False, status


def test_the_pause_before_asking_again_is_half_a_second():
    assert pg.RETRY_PAUSE_S == 0.5


def test_the_pause_is_taken_before_the_second_request(monkeypatch):
    slept = []
    monkeypatch.setattr(pg.time, "sleep", slept.append)
    pg.fetch("https://acme-dental.com/lp", Answers((502, b"x", None), (200, GOOD_HTML, None)))
    assert slept == [pg.RETRY_PAUSE_S]
    slept.clear()
    pg.fetch("https://acme-dental.com/lp", Answers((200, GOOD_HTML, None)))
    assert slept == []
    pg.fetch("https://acme-dental.com/lp", Answers((503, b"x", None)))
    assert slept == [pg.RETRY_PAUSE_S]                              # asked twice in all, so one pause, never one after the last answer


def test_a_bot_check_is_not_a_broken_page_whatever_its_status(no_pause):
    challenge = {"Content-Type": "text/html", "cf-mitigated": "challenge", "Server": "cloudflare"}
    for status in (403, 503):
        f = Answers((status, b"<html>Just a moment...</html>", challenge))
        rec = pg.fetch("https://acme-dental.com/lp", f)
        assert rec["blocked"] is True and f.pages == 1 and rec["retried"] is False, status
        got = _status(rec)
        assert got["result"] == "unknown" and "refuses automated requests" in got["found"]
    rec = pg.fetch("https://acme-dental.com/lp", Answers((200, b"<html>Just a moment...</html>", challenge)))
    assert rec["blocked"] is True                                   # a check page is not the site's page, whatever status it came with
    assert pg._challenged({"cf-mitigated": "Challenge"}) and not pg._challenged({"cf-mitigated": "other"}) and not pg._challenged({}) and not pg._challenged(None)


def test_a_server_error_that_is_not_a_bot_check_is_not_called_one(no_pause):
    rec = pg.fetch("https://acme-dental.com/lp", Answers((503, b"x", {"Content-Type": "text/html", "Server": "cloudflare"})))
    assert rec["blocked"] is False


@pytest.mark.parametrize("title", ["404 - Page Not Found", "Page not found | Acme Dental", "Not Found", "Not found - Acme", "Not found " + chr(0x2013) + " Acme", "Not found " + chr(0x2014) + " Acme", "Not found: Acme", "This page doesn't exist", "We can’t find that page",
                                   "Sorry, this offer is no longer available", "Page unavailable", "Error 404"])
def test_a_page_that_answers_200_but_says_it_is_gone_is_a_warning(title):
    f = page(fetch_over={"status": 200, "ms": 300})
    f["fetch"]["facts"]["title"] = title
    out = {x["id"]: x for x in checks.run(f)}["reach_status"]
    assert out["result"] == "warn" and out["severity"] == "high" and "reads like an error page" in out["found"] and title[:20] in out["found"]


def test_a_gone_headline_counts_when_the_title_does_not_say_it():
    f = page(fetch_over={"status": 200, "ms": 300})
    f["fetch"]["facts"].update(title="Acme Dental", h1=["Oops, page not found"])
    out = {x["id"]: x for x in checks.run(f)}["reach_status"]
    assert out["result"] == "warn" and "its headline reads like an error page" in out["found"]


@pytest.mark.parametrize("title", ["Emergency Dentist Boston | Acme Dental", "Not Found Yet? Call us", "Find a dentist who cannot be beaten", "Free consultation, no exist-ing patients needed"])
def test_an_ordinary_title_is_not_taken_for_an_error_page(title):
    f = page(fetch_over={"status": 200, "ms": 300})
    f["fetch"]["facts"].update(title=title, h1=["Welcome"])
    assert {x["id"]: x for x in checks.run(f)}["reach_status"]["result"] == "pass"
