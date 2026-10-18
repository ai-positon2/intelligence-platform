"""Landing Page & Relevancy Check: the report (tracker/paid_lp.py): account naming, the shared cache, scoring the account."""
import datetime as dt
import io

import pytest

from tests.gads_fakes import FakeStore
from tests.test_paid_lp_read import ad_row, kw_row, page_row, term_row
from tracker import paid_geo, paid_gads
from tracker import paid_gads_segments as gads
from tracker import paid_lp as lp
from tracker import paid_lp_model as model

NOW = dt.datetime(2026, 10, 17, 12, 0, tzinfo=dt.timezone.utc)
ACCOUNT_ID = "1112223334"
ACCOUNT = {"id": ACCOUNT_ID, "name": "Acme Dental", "currency": "USD", "timeZone": "America/New_York"}
GOOD_HTML = ("""<!doctype html><html lang="en"><head><title>Emergency Dentist Boston | Acme Dental</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><script src="//www.googletagmanager.com/gtm.js?id=GTM-ABC1234" async></script></head>
<body><h1>Emergency Dentist in Boston</h1><h2>Same day care</h2>
<p>Call (617) 555-0123 or visit 12 Main Street. Cleanings from $99. Free consultation. Licensed and insured, 5-star rated, since 1998. Tooth pain and broken teeth seen today.</p>
""" + "<p>We look after families across Boston and Cambridge with gentle, modern dentistry. </p>" * 30 + """
<a href="tel:+16175550123">Call now</a> <a href="/privacy">Privacy policy</a>
<form action="/send" method="post"><input name="name" required><input type="tel" name="phone" required><input type="email" name="email"><button>Book now</button></form>
</body></html>""").encode()


def fresh():
    paid_geo.clear_cache()
    paid_gads.forget()


@pytest.fixture(autouse=True)
def fresh_caches():
    fresh()
    yield
    fresh()


class Raw:
    def __init__(self, body):
        self.io = io.BytesIO(body)

    def read(self, n=-1, decode_content=False):
        return self.io.read(n)


class Resp:
    def __init__(self, status, url, body, headers=None):
        self.status_code, self.url, self.raw, self.headers = status, url, Raw(body), dict(headers or {"Content-Type": "text/html", "Content-Encoding": "gzip"})

    def close(self):
        pass


class Fetcher:
    """A page fetcher double. `answers`: path -> (status, body) ; counts calls; robots.txt answers 404 (nothing blocked)."""

    def __init__(self, answers=None):
        self.answers, self.calls = answers or {}, []

    def __call__(self, url, **kw):
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            return Resp(404, url, b"", {"Content-Type": "text/plain"})
        path = url.split("//", 1)[1].split("/", 1)[1].split("?")[0] if "/" in url.split("//", 1)[1] else ""
        status, body = self.answers.get("/" + path, (200, GOOD_HTML))
        return Resp(status, url, body)


def search(pages=None, ads=None, keywords=None, terms=None, fail=None, calls=None):
    parts = {"landing_page_view": "pages", "FROM ad_group_ad": "ads", "FROM keyword_view": "keywords", "FROM search_term_view": "terms"}
    data = {"pages": pages if pages is not None else [page_row("https://acme-dental.com/lp", 800.0, "MOBILE", 600, 20, speed=8), page_row("https://acme-dental.com/lp", 200.0, "DESKTOP", 250, 10)],
            "ads": ads if ads is not None else [ad_row("g1", "Emergency", "Boston", "https://acme-dental.com/lp", 900.0, ("Emergency Dentist Boston", "Same Day Care"), ("Cleanings from $99. Free consultation.",))],
            "keywords": keywords if keywords is not None else [kw_row("g1", "emergency dentist boston", 50.0, lp="ABOVE_AVERAGE"), kw_row("g1", "tooth pain", 20.0, lp="AVERAGE")],
            "terms": terms if terms is not None else [term_row("g1", "emergency dentist boston", 9), term_row("g1", "dentist boston", 5), term_row("g1", "tooth pain boston", 4)]}

    def fn(cid, gaql):
        if calls is not None:
            calls.append(gaql)
        part = next(v for k, v in parts.items() if k in gaql)
        if fail and part in fail:
            raise fail[part]
        return data[part]
    return fn


def run(account=ACCOUNT_ID, **kw):
    kw.setdefault("store", FakeStore())
    kw.setdefault("accounts_fn", lambda: [ACCOUNT])
    kw.setdefault("search_fn", search())
    kw.setdefault("fetcher", Fetcher())
    kw.setdefault("now", NOW)
    return lp.report_for(account, **kw)


# ── Naming the account ──────────────────────────────────────────────────────

def test_an_account_the_platform_cannot_read_is_a_bad_request():
    with pytest.raises(lp.BadRequest):
        run(account="9998887776")


# ── A report ────────────────────────────────────────────────────────────────

def test_a_healthy_account_has_a_page_a_score_and_the_account_around_it():
    out = run()
    assert out["account"] == {"id": ACCOUNT_ID, "name": "Acme Dental", "currency": "USD"} and out["window"] == "the last 30 days"
    assert [p["id"] for p in out["pages"]] == ["acme-dental.com/lp"]
    p = out["pages"][0]
    assert p["spend"] == 1000.0 and p["clicks"] == 850 and p["checked"] is True and p["rated"] is True and p["grade"] in ("A", "B") and p["score"] >= 85
    assert out["summary"]["score"] == p["score"] and out["summary"]["checked"] == 1 and out["summary"]["pagesOfSpend"] == 1
    assert out["summary"]["spend"] == 1000.0 and out["summary"]["spendChecked"] == 1000.0 and out["summary"]["spendUnverified"] == 0.0 and out["summary"]["spendAtRisk"] == 0.0
    assert out["warnings"] == [] and out["unread"] == [] and "stale" not in out
    assert out["readAt"] == NOW.isoformat() and out["generatedAt"] == NOW.isoformat()
    assert [s["key"] for s in out["sections"]] == ["reach", "match", "path", "mobile", "speed", "trust"]


def test_a_page_carries_its_numbers_and_what_was_seen_on_it():
    p = run()["pages"][0]
    assert p["convRate"] == 3.53 and p["cpa"] == 33.33 and p["speed"] == 8
    seen = p["seen"]
    assert seen["status"] == 200 and seen["title"] == "Emergency Dentist Boston | Acme Dental" and seen["h1"] == ["Emergency Dentist in Boston"]
    assert seen["forms"] == [{"fields": 3, "required": 2, "phone": True, "email": True}] and seen["tel"] == ["+16175550123"] and "Google Tag Manager" in seen["tools"]
    assert seen["encoding"] == "gzip" and seen["adsbot"] is True


def test_findings_are_sent_small_and_the_wording_once_in_a_catalog():
    out = run()
    assert len(out["catalog"]) == 39 and set(out["catalog"]["reach_status"]) == {"section", "title", "severity", "why", "fix"}
    for s in out["pages"][0]["sections"]:
        for f in s["findings"]:
            assert set(f) <= set(lp.FINDING_KEYS) and "why" not in f and "fix" not in f and f["id"] in out["catalog"]
    assert sum(len(s["findings"]) for s in out["pages"][0]["sections"]) == 35


def test_the_ad_groups_show_what_the_page_carries():
    g = run()["pages"][0]["adGroups"][0]
    assert g["name"] == "Emergency" and g["cost"] == 900.0 and g["echo"] == 1.0 and g["bestHeadline"] == "Emergency Dentist Boston"
    kw = {k["text"]: k for k in g["keywords"]}
    assert kw["emergency dentist boston"]["on"] == "headline" and kw["tooth pain"]["on"] == "page" and kw["tooth pain"]["landingPage"] == "average"
    assert {t["text"]: t["on"] for t in g["terms"]}["dentist boston"] in ("headline", "page")
    assert [(o["text"], o["on"]) for o in g["offers"]] == [("same day", True), ("$99", True), ("free consultation", True)]


def test_a_keyword_the_page_lacks_is_marked_none():
    out = run(search_fn=search(keywords=[kw_row("g1", "emergency dentist boston", 50.0), kw_row("g1", "orthodontist braces", 40.0)]))
    kw = {k["text"]: k["on"] for k in out["pages"][0]["adGroups"][0]["keywords"]}
    assert kw["orthodontist braces"] == "none"
    f = {x["id"]: x for s in out["pages"][0]["sections"] for x in s["findings"]}
    assert f["match_keywords_page"]["result"] in ("warn", "fail") and "orthodontist braces" in f["match_keywords_page"]["found"]


def test_the_fix_first_list_is_one_row_per_check_worst_first_with_the_pages_and_what_they_spent():
    out = run(fetcher=Fetcher({"/lp": (200, b"<html><body>" + b"<p>nothing useful here</p>" * 20 + b"</body></html>")}))
    fix = out["fixFirst"]
    assert fix and fix[0]["severity"] == "critical" and fix[0]["spend"] == 1000.0 and fix[0]["pages"][0]["url"] == "https://acme-dental.com/lp" and fix[0]["pages"][0]["pageId"] == "acme-dental.com/lp"
    assert len({r["id"] for r in fix}) == len(fix) and fix[0]["id"] in out["catalog"]
    sev = [model.SEVERITIES.index(f["severity"]) for f in fix]
    assert sev == sorted(sev)


def test_the_same_check_failing_on_several_pages_is_one_row_that_names_them():
    pages = [page_row("https://acme-dental.com/p%d" % i, float(500 - i), "MOBILE", 100, 2) for i in range(3)]
    ads = [ad_row("g%d" % i, "G%d" % i, "C", "https://acme-dental.com/p%d" % i, 100.0, ("Zebra Grooming",), ("Free zebra stripes",)) for i in range(3)]
    out = run(search_fn=search(pages=pages, ads=ads), fetcher=Fetcher({"/p0": (200, GOOD_HTML), "/p1": (200, GOOD_HTML), "/p2": (200, GOOD_HTML)}))
    offers = next(r for r in out["fixFirst"] if r["id"] == "match_offers")
    assert offers["failed"] == 3 and offers["spend"] == 500.0 + 499.0 + 498.0 and [x["pageId"] for x in offers["pages"]] == ["acme-dental.com/p0", "acme-dental.com/p1", "acme-dental.com/p2"]


# ── Pages that could not be verified ────────────────────────────────────────

def two_pages():
    return [page_row("https://acme-dental.com/lp", 600.0, "MOBILE", 600, 20), page_row("https://acme-dental.com/blocked", 400.0, "MOBILE", 400, 8)]


def test_a_page_that_refused_us_is_not_rated_and_does_not_move_the_account_score():
    ads = [ad_row("g1", "Emergency", "Boston", "https://acme-dental.com/lp", 600.0), ad_row("g2", "Other", "Boston", "https://acme-dental.com/blocked", 400.0)]
    out = run(search_fn=search(pages=two_pages(), ads=ads), fetcher=Fetcher({"/blocked": (403, b"no")}))
    good, blocked = out["pages"]
    assert good["rated"] is True and blocked["rated"] is False and blocked["seen"]["blocked"] is True
    assert out["summary"]["score"] == good["score"] and out["summary"]["rated"] == 1 and out["summary"]["checked"] == 2
    assert out["summary"]["spendUnverified"] == 400.0
    assert any("refused our request" in n for n in out["notes"])
    ids = [f["id"] for f in blocked["sections"][0]["findings"]]
    assert "reach_status" in ids
    assert out["summary"]["capReasons"] == ["pages that took 40% of the spend could not be verified"] and out["summary"]["grade"] == "C"


def test_a_page_that_does_not_open_scores_nothing_and_fails_the_account_with_it():
    out = run(fetcher=Fetcher({"/lp": (404, b"<html>gone</html>")}))
    p = out["pages"][0]
    assert p["summary"]["critical"] == 1 and p["summary"]["opens"] is False and p["rated"] is True        # a missing page is a verified failure, not an unverified page
    assert (p["score"], p["grade"]) == (0, "F")
    assert (out["summary"]["score"], out["summary"]["grade"]) == (0, "F") and out["summary"]["spendAtRisk"] == 1000.0 and out["summary"]["pagesAtRisk"] == 1
    assert out["summary"]["capReasons"] == ["a critical check failed on 1 page"] and out["summary"]["critical"] == 1


def test_a_page_that_opens_has_opens_true_and_keeps_its_score():
    p = run()["pages"][0]
    assert p["summary"]["opens"] is True and p["score"] > 0


def test_the_account_score_weights_pages_by_spend():
    pages = [page_row("https://acme-dental.com/lp", 900.0, "MOBILE", 600, 20), page_row("https://acme-dental.com/bad", 100.0, "MOBILE", 100, 1)]
    ads = [ad_row("g1", "Emergency", "Boston", "https://acme-dental.com/lp", 900.0), ad_row("g2", "Other", "Boston", "https://acme-dental.com/bad", 100.0)]
    bad_html = b"<html><body>" + b"<p>zzz</p>" * 40 + b"</body></html>"
    out = run(search_fn=search(pages=pages, ads=ads), fetcher=Fetcher({"/bad": (200, bad_html)}))
    good, bad = out["pages"]
    assert bad["score"] < good["score"]
    assert out["summary"]["score"] == round((good["score"] * 900 + bad["score"] * 100) / 1000)
    assert out["summary"]["spendAtRisk"] in (0.0, 100.0)


def test_pages_beyond_the_cap_are_not_fetched_and_the_report_says_how_much_was_checked():
    pages = [page_row("https://acme-dental.com/p%d" % i, float(100 - i)) for i in range(6)]
    f = Fetcher()
    out = run(search_fn=search(pages=pages, ads=[]), fetcher=f, max_pages=3)
    assert out["summary"]["pagesOfSpend"] == 6 and out["summary"]["checked"] == 3
    checked = [p for p in out["pages"] if p["checked"]]
    assert [p["id"] for p in checked] == ["acme-dental.com/p0", "acme-dental.com/p1", "acme-dental.com/p2"]
    assert len([c for c in f.calls if not c.endswith("robots.txt")]) == 3
    assert any("6 landing pages took money" in n and "The 3 that spent most were checked" in n for n in out["notes"])
    assert out["pages"][3]["seen"] is None and out["pages"][3]["rated"] is False


def test_an_account_with_no_landing_page_spend_has_an_empty_report_not_an_error():
    out = run(search_fn=search(pages=[], ads=[]))
    assert out["pages"] == [] and out["summary"]["score"] is None and out["summary"]["grade"] is None and out["summary"]["checked"] == 0 and out["fixFirst"] == []


def test_a_google_part_that_could_not_be_read_is_a_warning_and_its_checks_say_so():
    out = run(search_fn=search(fail={"keywords": gads.GadsError("api", "Google answered 500")}))
    assert out["warnings"] and "could not be read" in out["warnings"][0] and out["unread"] == ["Google Ads: keywords"]
    f = {x["id"]: x for s in out["pages"][0]["sections"] for x in s["findings"]}
    assert f["match_keywords_page"]["result"] == "unknown" and "keywords could not be read" in f["match_keywords_page"]["found"]


def test_without_the_landing_pages_there_is_no_report_and_the_error_is_raised():
    with pytest.raises(gads.GadsError):
        run(search_fn=search(fail={"pages": gads.GadsError("auth", "sign-in rejected")}))


# ── The shared cache ────────────────────────────────────────────────────────

def test_a_second_report_is_made_from_the_saved_read_without_asking_google_or_the_sites_again():
    store, calls, f = FakeStore(), [], Fetcher()
    run(store=store, search_fn=search(calls=calls), fetcher=f)
    asked, fetched = len(calls), len(f.calls)
    assert asked == 4 and fetched >= 1
    again = run(store=store, search_fn=search(calls=calls), fetcher=f)
    assert len(calls) == asked and len(f.calls) == fetched and again["pages"]


def test_the_saved_read_is_per_account():
    store, calls = FakeStore(), []
    run(store=store, search_fn=search(calls=calls))
    other = dict(ACCOUNT, id="2223334445", name="Other")
    lp.report_for("2223334445", store=store, accounts_fn=lambda: [ACCOUNT, other], search_fn=search(calls=calls), fetcher=Fetcher(), now=NOW)
    assert len(calls) == 8


def test_refresh_reads_again_but_not_twice_in_the_cooldown():
    store, calls = FakeStore(), []
    run(store=store, search_fn=search(calls=calls))
    run(store=store, search_fn=search(calls=calls), force=True)
    assert len(calls) == 8
    with pytest.raises(paid_gads.RefreshTooSoon) as e:
        run(store=store, search_fn=search(calls=calls), force=True)
    assert e.value.retry_in >= 1 and len(calls) == 8


def test_when_the_allowance_is_spent_the_last_saved_read_is_served_with_its_date():
    store = FakeStore()
    first = run(store=store)
    key = lp.cache_key(ACCOUNT_ID)
    store.old[key] = store.cache.pop(key)
    paid_gads.forget()
    out = run(store=store, search_fn=search(fail={"pages": gads.GadsError("quota", "allowance spent")}))
    assert out["stale"] == [{"platform": "google", "savedAt": "2026-10-10T08:00:00+00:00"}] and out["pages"][0]["id"] == first["pages"][0]["id"]


def test_a_spent_allowance_with_nothing_saved_is_the_error():
    with pytest.raises(gads.GadsError) as e:
        run(search_fn=search(fail={"pages": gads.GadsError("quota", "allowance spent")}))
    assert e.value.kind == "quota"


def test_only_a_quota_error_falls_back_to_a_saved_read():
    store = FakeStore()
    run(store=store)
    key = lp.cache_key(ACCOUNT_ID)
    store.old[key] = store.cache.pop(key)
    paid_gads.forget()
    with pytest.raises(gads.GadsError):
        run(store=store, search_fn=search(fail={"pages": gads.GadsError("api", "Google answered 500")}))


def test_a_read_with_a_part_missing_is_kept_only_five_minutes():
    store, calls = FakeStore(), []
    run(store=store, search_fn=search(calls=calls, fail={"terms": gads.GadsError("api", "x")}))
    assert len(calls) == 4
    run(store=store, search_fn=search(calls=calls, fail={"terms": gads.GadsError("api", "x")}))
    assert len(calls) == 4                                          # still fresh
    paid_gads.forget()
    store.at[lp.cache_key(ACCOUNT_ID)] -= lp.PARTIAL_TTL_S + 5
    run(store=store, search_fn=search(calls=calls))
    assert len(calls) == 8
    paid_gads.forget()
    store.at[lp.cache_key(ACCOUNT_ID)] -= 1800                       # a complete read is still good half an hour on
    run(store=store, search_fn=search(calls=calls))
    assert len(calls) == 8


def test_a_page_not_read_in_time_makes_the_read_partial_too(monkeypatch):
    store, calls = FakeStore(), []
    from tracker import paid_lp_page
    real = paid_lp_page.fetch_many
    monkeypatch.setattr(paid_lp_page, "fetch_many", lambda urls, fetcher=None, **kw: [{"url": u, "status": 0, "error": "not read in time", "skipped": True, "blocked": False, "isHtml": False, "facts": None, "tech": None} for u in urls])
    out = run(store=store, search_fn=search(calls=calls))
    assert out["pages"][0]["checked"] is False and out["pages"][0]["rated"] is False
    monkeypatch.setattr(paid_lp_page, "fetch_many", real)
    paid_gads.forget()
    store.at[lp.cache_key(ACCOUNT_ID)] -= lp.PARTIAL_TTL_S + 5
    out2 = run(store=store, search_fn=search(calls=calls))
    assert len(calls) == 8 and out2["pages"][0]["checked"] is True


def test_a_failed_save_never_costs_the_report():
    class Broken(FakeStore):
        def put_gads_cache(self, key, value):
            raise RuntimeError("db down")
    out = run(store=Broken())
    assert out["pages"]


def test_two_people_asking_at_once_share_one_read():
    import threading
    store, calls = FakeStore(), []
    results = []

    def go():
        results.append(run(store=store, search_fn=search(calls=calls)))
    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(results) == 4 and len(calls) == 4


def test_the_report_is_plain_data_that_can_be_sent_as_json():
    import json
    out = run()
    json.dumps(out)
    assert "_findings" not in json.dumps(out)


def test_no_em_or_en_dash_anywhere_in_a_report():
    import json
    text = json.dumps(run(), ensure_ascii=False)
    assert "—" not in text and "–" not in text
