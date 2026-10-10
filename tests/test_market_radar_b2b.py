"""Market Radar, Phase 10: B2B and manufacturer detectors
(tracker/market_radar_b2b, tracker/market_radar_linkedin)."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracker import market_radar_b2b as B  # noqa: E402
from tracker import market_radar_linkedin as L  # noqa: E402
from tracker import market_radar_collect as mc  # noqa: E402

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


def ok(body):
    return {"status": "ok", "http": 200, "body": body if isinstance(body, str) else json.dumps(body),
            "note": "", "final_url": "", "truncated": False}


def miss(status="blocked", note="refused (HTTP 503)"):
    return {"status": status, "http": 503, "body": "", "note": note, "final_url": "",
            "truncated": False}


class Web:
    """url prefix -> answer; records every request with its headers."""

    def __init__(self, routes):
        self.routes, self.asked = routes, []

    def get(self, url, **kw):
        self.asked.append((url, kw.get("headers") or {}))
        for prefix, ans in self.routes.items():
            if url.startswith(prefix):
                return ans(url) if callable(ans) else ans
        return miss("not_found", "not found (HTTP 404)")


def ctx(web=None, entity=None, prev=None, site=None, hooks=None, run_id=None):
    return {"entity": entity or {"id": 1, "domain": "gorgias.com", "name": "Gorgias"},
            "get": (web or Web({})).get, "now": NOW, "prev": prev or {}, "site": site,
            "hooks": hooks or {}, "run_id": run_id}


# == certificate-log subdomains =======================================================

def test_host_names_are_cleaned_to_the_companys_own():
    raw = [("*.app.gorgias.com", "2026-09-01T00:00:00"), ("app.gorgias.com", "2024-01-01"),
           ("APP.gorgias.com.", "2026-09-03"), ("gorgias.com", "2020-01-01"),
           ("www.gorgias.com", "2020-01-01"), ("evil.com", "2026-01-01"),
           ("notgorgias.com", "2026-01-01"), ("bad name.gorgias.com", "2026-01-01")]
    assert B._names(raw, "gorgias.com") == {"app.gorgias.com": "2024-01-01"}


@pytest.mark.parametrize("name,worth", [
    ("mcp.gorgias.com", True), ("evals.gorgias.com", True), ("uk.app.gorgias.com", True),
    ("mail.gorgias.com", False), ("autodiscover.gorgias.com", False), ("em1234.gorgias.com", False),
    ("staging.gorgias.com", False), ("api-dev.gorgias.com", False), ("studioxstg.gorgias.com", True),
    ("a1b2c3d4e5.gorgias.com", False), ("x.y.z.gorgias.com", False), ("url9876.gorgias.com", False),
    ("primary.realtime.services.gorgias.com", False), ("demo.gorgias.com", False)])
def test_only_names_that_could_say_something_are_reported(name, worth):
    assert B.worth_reporting(name, "gorgias.com") is worth


class Conn:
    def __init__(self, rows=None, fail=None):
        self.rows, self.fail, self.sql, self.closed = rows or [], fail, [], False
        self.autocommit = False

    def cursor(self):
        conn = self

        class Cur:
            def execute(self, sql, args=None):
                if conn.fail and "SELECT" in sql:
                    raise conn.fail
                conn.sql.append((sql, args))

            def fetchall(self):
                return conn.rows
        return Cur()

    def close(self):
        self.closed = True


def test_crtsh_history_tells_new_names_from_old_on_a_first_read():
    conn = Conn([("mcp.gorgias.com", datetime(2026, 9, 17)), ("app.gorgias.com", datetime(2019, 1, 1)),
                 ("staging2.gorgias.com", datetime(2026, 10, 1)), ("*.evals.gorgias.com", datetime(2026, 9, 4))])
    r = B.read_subdomains(ctx(hooks={"crt_connect": lambda **kw: conn}))
    assert r["status"] == "ok" and conn.closed and conn.autocommit
    assert conn.sql[-1][1] == ("gorgias.com", "%.gorgias.com")
    assert conn.sql[0][0] == "SET statement_timeout = '%ds'" % B.CRT_TIMEOUT_S
    assert r["payload"]["history"] and r["payload"]["source"] == "crt.sh"
    assert "4 host names in certificate logs (crt.sh), 3 worth watching" == r["note"]
    events = B.baseline_subdomains(r["payload"], NOW)
    assert [e["title"] for e in events] == ["New subdomain: mcp.gorgias.com", "New subdomain: evals.gorgias.com"]
    assert events[0]["type"] == "new_subdomain" and events[0]["date"] == "2026-09-17"
    assert events[0]["url"] == "https://mcp.gorgias.com" and "First certified 2026-09-17" in events[0]["summary"]


def certspotter(pages):
    def answer(url):
        after = url.split("&after=")[1] if "&after=" in url else None
        return ok(pages.get(after, []))
    return answer


def test_when_crtsh_fails_cert_spotter_answers_without_history_and_a_first_read_reports_nothing():
    page1 = [{"id": str(i), "dns_names": ["host%d.gorgias.com" % i], "not_before": "2026-10-01T00:00:00Z"}
             for i in range(100)]
    page2 = [{"id": "500", "dns_names": ["mcp.gorgias.com", "*.mcp.gorgias.com"], "not_before": "2026-09-30"}]
    web = Web({"https://api.certspotter.com/": certspotter({None: page1, "99": page2})})
    boom = lambda **kw: Conn(fail=RuntimeError("too many connections"))  # noqa: E731
    r = B.read_subdomains(ctx(web, hooks={"crt_connect": boom}))
    assert r["payload"]["source"] == "Cert Spotter" and r["payload"]["history"] is False
    assert len(r["payload"]["names"]) == 101 and len(web.asked) == 2
    assert "current certificates only" in r["note"] and "crt.sh did not answer (RuntimeError)" in r["note"]
    assert B.baseline_subdomains(r["payload"], NOW) == []


def test_a_full_crtsh_pool_is_asked_again_before_falling_back():
    tries, slept = [], []
    conn = Conn([("mcp.gorgias.com", datetime(2026, 9, 17))])

    def connect(**kw):
        tries.append(1)
        if len(tries) < 3:
            raise RuntimeError('ERROR:  no more connections allowed (max_client_conn)')
        return conn
    r = B.read_subdomains(ctx(hooks={"crt_connect": connect, "sleep": slept.append}))
    assert r["payload"]["source"] == "crt.sh" and len(tries) == 3 and slept == [4, 12]
    tries.clear(); slept.clear()
    web = Web({"https://api.certspotter.com/": certspotter({None: []})})
    always = lambda **kw: tries.append(1) or (_ for _ in ()).throw(RuntimeError("max_client_conn"))  # noqa: E731
    r = B.read_subdomains(ctx(web, hooks={"crt_connect": always, "sleep": slept.append}))
    assert len(tries) == 3 and r["payload"]["source"] == "Cert Spotter"
    tries.clear(); slept.clear()
    other = lambda **kw: tries.append(1) or (_ for _ in ()).throw(OSError("unreachable"))  # noqa: E731
    B.read_subdomains(ctx(web, hooks={"crt_connect": other, "sleep": slept.append}))
    assert len(tries) == 1 and slept == []          # only a full pool is worth waiting for


def test_both_logs_failing_is_a_failure_with_both_reasons():
    web = Web({"https://api.certspotter.com/": miss()})
    r = B.read_subdomains(ctx(web, hooks={"crt_connect": lambda **kw: (_ for _ in ()).throw(OSError("x"))}))
    assert r["status"] == "failed" and "crt.sh did not answer (OSError)" in r["note"]
    assert "Cert Spotter: refused (HTTP 503)" in r["note"]


def test_a_name_is_new_when_it_is_missing_before_and_certified_since():
    prev = {"domain": "gorgias.com", "read_at": "2026-10-01", "names": {"app.gorgias.com": "2019-01-01"}}
    cur = {"domain": "gorgias.com", "read_at": "2026-10-08", "names": {
        "app.gorgias.com": "2019-01-01", "mcp.gorgias.com": "2026-10-05",
        # missing before only because the earlier read was the current-only log
        "legacy.gorgias.com": "2021-05-01", "mail2.gorgias.com": "2026-10-06"}}
    assert [e["title"] for e in B.compare_subdomains(prev, cur, NOW)] == ["New subdomain: mcp.gorgias.com"]
    many = dict(cur, names={"n%d.gorgias.com" % i: "2026-10-07" for i in range(12)})
    ev = B.compare_subdomains(prev, many, NOW)
    assert len(ev) == B.MAX_NEW_EVENTS + 1 and ev[-1]["title"] == "4 more new subdomains"


# == SEC EDGAR ========================================================================

def fts(*hits):
    return ok({"hits": {"hits": [{"_source": {"ciks": [c], "display_names": [n]}} for c, n in hits]}})


def submissions(name, filings, former=(), website=""):
    keys = ("form", "filingDate", "items", "accessionNumber", "primaryDocument")
    recent = {k: [f[i] for f in filings] for i, k in enumerate(keys)}
    return ok({"name": name, "tickers": ["BIRD"], "formerNames": [{"name": n} for n in former],
               "website": website, "filings": {"recent": recent}})


ALLBIRDS = {"id": 2, "domain": "allbirds.com", "name": "Allbirds"}
FILINGS = [
    ("8-K", "2026-10-01", "5.02,9.01", "0001437749-26-031745", "bird8k.htm"),
    ("4", "2026-10-05", "", "0001437749-26-032077", "f4.xml"),
    ("8-K", "2026-09-15", "7.01,9.01", "0001437749-26-030000", "fd.htm"),
    ("10-Q", "2026-08-07", "", "0001437749-26-020000", "q.htm"),
    ("8-K", "2026-08-01", "2.01", "0001437749-26-019000", "deal.htm"),
    ("S-4", "2025-02-01", "", "0001437749-25-000001", "s4.htm"),     # older than 400 days
]


def sec_web(fts_answer, subs):
    return Web({"https://efts.sec.gov/": fts_answer, "https://data.sec.gov/submissions/": subs,
                "https://www.sec.gov/Archives/edgar/data/1653909/000143774926031745/bird8k.htm":
                    ok("<p>Item 5.02 Departure of Directors.</p><p>On September 29, 2026, Jane Roe "
                       "resigned as Chief Financial Officer.</p><p>Item 9.01 Exhibits</p>"),
                "https://www.sec.gov/Archives/": ok("<p>nothing useful</p>")})


def test_a_listed_company_is_found_by_its_domain_even_under_a_new_name():
    web = sec_web(fts(("0001653909", "Smartbird, Inc.  (BIRD)  (CIK 0001653909)"),
                      ("0001653909", "Smartbird, Inc.  (BIRD)  (CIK 0001653909)")),
                  submissions("Smartbird, Inc.", FILINGS, former=["Allbirds, Inc."]))
    r = B.read_filings(ctx(web, entity=ALLBIRDS))
    assert r["status"] == "ok" and r["payload"]["cik"] == 1653909
    assert all(h.get("User-Agent") == B.SEC_UA for _u, h in web.asked)
    assert [f["acc"][-6:] for f in r["payload"]["filings"]] == ["031745", "019000"]
    assert r["note"].startswith("Smartbird, Inc. (CIK 1653909, BIRD): 2 material filings")
    ev = B.baseline_filings(r["payload"], NOW)
    assert [(e["type"], e["title"]) for e in ev] == [
        ("leadership_change", "Smartbird, Inc. reported a director or officer change (SEC 8-K)")]
    assert "Jane Roe resigned as Chief Financial Officer" in ev[0]["summary"]
    assert ev[0]["url"].endswith("/1653909/000143774926031745/bird8k.htm")


def test_another_companys_report_naming_the_domain_is_not_the_company():
    web = Web({"https://efts.sec.gov/": fts(*[("0000999999", "Foot Locker, Inc.")] * 3 + [("0000320187", "NIKE, Inc.")]),
               "https://data.sec.gov/submissions/CIK0000999999": submissions("Foot Locker, Inc.", []),
               "https://data.sec.gov/submissions/CIK0000320187": submissions("NIKE, Inc.", [])})
    r = B.read_filings(ctx(web, entity={"id": 3, "domain": "nike.com", "name": "Nike"}))
    assert r["payload"]["cik"] == 320187


@pytest.mark.parametrize("answer,status,words", [
    (fts(), "none", "not a US-listed company"),
    (fts(("0000999999", "Foot Locker, Inc.")), "none", "none filed by a company of that name"),
    (miss(), "failed", "EDGAR search: refused (HTTP 503)"),
    (ok("<html>"), "failed", "not JSON")])
def test_unlisted_unknown_or_unreachable_says_which(answer, status, words):
    web = Web({"https://efts.sec.gov/": answer,
               "https://data.sec.gov/": submissions("Foot Locker, Inc.", [])})
    r = B.read_filings(ctx(web, entity=ALLBIRDS))
    assert r["status"] == status and words in r["note"]


def test_a_company_that_stopped_filing_is_not_called_listed():
    web = sec_web(fts(("0001463172", "Zendesk, Inc.")),
                  submissions("Zendesk, Inc.", [("10-Q", "2022-08-04", "", "0001-22-1", "q.htm")]))
    r = B.read_filings(ctx(web, entity={"id": 4, "domain": "zendesk.com", "name": "Zendesk"}))
    assert r["status"] == "none" and r["note"] == "Zendesk, Inc. no longer files with the SEC (last filing 2022-08-04)"


def test_a_certificate_log_timeout_says_why():
    class QueryCanceled(Exception):
        pass
    web = Web({"https://api.certspotter.com/": miss()})
    r = B.read_subdomains(ctx(web, hooks={"crt_connect": lambda **kw: Conn(fail=QueryCanceled("cancel"))}))
    assert "crt.sh did not answer (too many certificates to read in time)" in r["note"]


def test_a_known_company_is_not_searched_again_and_only_new_filings_are_news():
    prev_payload = {"cik": 1653909, "name": "Smartbird, Inc.", "filings": [
        {"form": "8-K", "date": "2026-08-01", "items": ["2.01"], "acc": "0001437749-26-019000", "doc": "deal.htm"}]}
    web = sec_web(miss(), submissions("Smartbird, Inc.", FILINGS))
    r = B.read_filings(ctx(web, entity=ALLBIRDS, prev={"filings": prev_payload}))
    assert not any("efts" in u for u, _ in web.asked)
    # the filing already seen is not fetched again for its words
    assert not any("deal.htm" in u for u, _ in web.asked)
    ev = B.compare_filings(prev_payload, r["payload"], NOW)
    assert [e["key"] for e in ev] == ["sec:0001437749-26-031745"]
    # the same filings under another company number: read afresh, as a first read
    assert [e["key"] for e in B.compare_filings(dict(r["payload"], cik=1), r["payload"], NOW)] == \
        ["sec:0001437749-26-031745"]


def test_items_pick_the_strongest_type_and_forms_name_themselves():
    p = {"cik": 1, "name": "Acme Corp"}
    e = B._filing_event({"form": "8-K", "date": "2026-10-01", "items": ["5.02", "2.01", "9.01"],
                         "acc": "a-1", "doc": "d.htm"}, p)
    assert e["type"] == "acquisition"
    assert e["title"] == ("Acme Corp completed an acquisition or sale of assets; reported a director or "
                          "officer change (SEC 8-K)")          # strongest first
    e = B._filing_event({"form": "S-1", "date": "2026-10-01", "items": [], "acc": "a-2", "doc": "s.htm"}, p)
    assert (e["type"], e["title"]) == ("funding", "Acme Corp filed to sell shares to the public (S-1) (SEC S-1)")


# == headcount ========================================================================

def apollo(n, g6=None, g12=None):
    return lambda domain: {"organization": {"estimated_num_employees": n,
                                            "organization_headcount_six_month_growth": g6,
                                            "organization_headcount_twelve_month_growth": g12}}


def test_without_apollo_headcount_is_skipped_and_says_why(monkeypatch):
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    r = B.read_headcount(ctx())
    assert r["status"] == "skipped" and "APOLLO_API_KEY" in r["note"]


def test_headcount_and_its_growth_become_a_line_once():
    r = B.read_headcount(ctx(hooks={"apollo_enrich": apollo(820, 0.183, 0.4)}))
    assert r["payload"] == {"employees": 820, "growth_6m": 0.183, "growth_12m": 0.4, "read": "2026-10-10"}
    assert r["note"] == "about 820 employees (Apollo estimate), +18% in six months"
    (ev,) = B.baseline_headcount(r["payload"], NOW)
    assert ev["type"] == "hiring_surge" and ev["title"] == "Headcount up 18% in six months, to about 820 (Apollo estimate)"
    # a month later still growing fast: not news again
    assert B.compare_headcount(r["payload"], dict(r["payload"], employees=850, growth_6m=0.2), NOW) == []
    # falling across the line is
    (ev,) = B.compare_headcount(dict(r["payload"], growth_6m=0.01),
                                dict(r["payload"], employees=800, growth_6m=-0.1), NOW)
    assert ev["type"] == "hiring_slowdown" and "down 10%" in ev["title"]
    (ev,) = B.compare_headcount({"employees": 400, "read": "2026-09-10"}, {"employees": 460}, NOW)
    assert ev["title"] == "Headcount up from about 400 to 460 since 2026-09-10 (Apollo estimate)"
    assert B.compare_headcount({"employees": 400}, {"employees": 415}, NOW) == []     # under 10%
    # +33% of a company of 8 is two people, not a hiring surge
    assert B.baseline_headcount({"employees": 8, "growth_6m": 0.33}, NOW) == []
    assert B.baseline_headcount({"employees": 80, "growth_6m": 0.33}, NOW)


@pytest.mark.parametrize("answer,status", [({"organization": {}}, "none"), ({}, "none"),
                                            ({"organization": {"estimated_num_employees": 0}}, "none")])
def test_no_estimate_is_none(answer, status):
    assert B.read_headcount(ctx(hooks={"apollo_enrich": lambda d: answer}))["status"] == status


def test_an_apollo_error_is_a_failure():
    def boom(d):
        raise ConnectionError("down")
    r = B.read_headcount(ctx(hooks={"apollo_enrich": boom}))
    assert r["status"] == "failed" and "ConnectionError" in r["note"]


# == UK company register ==============================================================

@pytest.mark.parametrize("text,number", [
    ("Gymshark Limited. Registered in England and Wales. Company No. 08130873", "08130873"),
    ("Registered office: 1 High St. Company number: 1234567", "01234567"),
    ("Registered in Scotland, Registration number SC123456", "SC123456"),
    ("Company No. 08130873", None),                                   # no sign it is a UK register number
    ("Registered in England and Wales.", None)])
def test_the_company_number_comes_from_the_sites_own_footer(text, number):
    assert B.company_number({"texts": {"home": text}}) == number


def test_without_a_key_a_uk_company_is_skipped_and_named(monkeypatch):
    monkeypatch.delenv("COMPANIES_HOUSE_API_KEY", raising=False)
    site = {"texts": {"home": "Registered in England and Wales. Company No. 08130873"}}
    r = B.read_registry(ctx(site=site))
    assert r["status"] == "skipped" and "08130873" in r["note"] and "COMPANIES_HOUSE_API_KEY" in r["note"]
    assert B.read_registry(ctx(site={"texts": {}}))["status"] == "none"


def test_register_filings_become_moves(monkeypatch):
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "k")
    hist = {"items": [
        {"transaction_id": "t1", "date": "2026-10-01", "type": "AP01", "category": "officers",
         "description_values": {"officer_name": "Jane Roe"}},
        {"transaction_id": "t2", "date": "2026-09-20", "type": "SH01", "category": "capital"},
        {"transaction_id": "t3", "date": "2026-09-01", "type": "AA", "category": "accounts"},
        {"transaction_id": "t4", "date": "2026-09-30", "type": "NM01", "category": "change-of-name",
         "description_values": {"new_name": "Acme Two Ltd"}},
        {"transaction_id": "t5", "date": "2026-08-01", "type": "MR01", "category": "mortgage"},
        {"transaction_id": "t6", "date": "2026-10-02", "type": "LIQ02", "category": "insolvency"}]}
    web = Web({B.CH + "/company/08130873/filing-history": ok(hist),
               B.CH + "/company/08130873": ok({"company_name": "ACME LTD", "company_status": "active"})})
    site = {"texts": {"home": "Registered in England and Wales. Company No. 08130873"}}
    r = B.read_registry(ctx(web, site=site))
    assert r["status"] == "ok" and web.asked[0][1]["Authorization"] == "Basic azo="   # "k:" 
    ev = {e["key"]: e for e in B.baseline_registry(r["payload"], NOW)}
    assert ev["ch:t1"]["title"] == "ACME LTD appointed a director: Jane Roe (Companies House)"
    assert ev["ch:t2"]["type"] == "funding" and ev["ch:t4"]["title"].endswith("to Acme Two Ltd (Companies House)")
    assert ev["ch:t6"]["type"] == "legal_regulatory" and "ch:t3" not in ev and "ch:t5" not in ev   # t5: too old
    later = dict(r["payload"], status="liquidation")
    out = B.compare_registry(r["payload"], dict(later, items=later["items"] + [
        {"id": "t7", "date": "2026-10-09", "type": "TM01", "officer": "John Doe"}]), NOW)
    assert [e["key"] for e in out] == ["ch:t7", "ch-status:liquidation"]
    assert "now listed as liquidation at Companies House (was active)" in out[1]["title"]


# == LinkedIn posts ===================================================================

def li_site(handle="gorgias"):
    return {"status": "ok", "signals": {"socials": {"linkedin": handle} if handle else {}}}


POSTS = [{"platform_post_id": "7001", "caption": "We are launching Gorgias AI Agent in Germany today. More soon.",
          "posted_at": "2026-10-08T10:00:00Z", "post_url": "https://www.linkedin.com/feed/update/7001"},
         {"platform_post_id": "6900", "caption": "Throwback to our 2025 summit", "posted_at": "2026-07-01"},
         {"platform_post_id": "7002", "caption": "", "posted_at": "2026-10-09"}]


def test_linkedin_posts_from_the_page_the_site_links_to():
    asked = []
    hooks = {"linkedin_collect": lambda h: asked.append(h) or (POSTS, {"page": "linkedin.com/company/gorgias",
                                                                         "verification": "domain"}),
             "linkedin_available": lambda: True}
    r = L.read_linkedin(ctx(site=li_site(), hooks=hooks))
    assert asked == ["gorgias"] and r["status"] == "ok" and r["items"] == 2
    assert r["payload"]["handle"] == "gorgias" and r["payload"]["verification"] == "domain"
    (ev,) = L.baseline_linkedin(r["payload"], NOW)
    assert ev["type"] == "announcement" and ev["key"] == "post:7001"
    assert ev["title"] == "We are launching Gorgias AI Agent in Germany today."
    # the remembered page is read even when the site refuses us later
    again = L.read_linkedin(ctx(site={"status": "failed"}, prev={"linkedin": r["payload"]}, hooks=hooks))
    assert again["status"] == "ok" and asked == ["gorgias", "gorgias"]
    assert L.compare_linkedin(r["payload"], dict(r["payload"], posts=r["payload"]["posts"] + [
        {"id": "6000", "title": "An old post now in view", "date": "2026-06-01"}]), NOW) == []


def test_linkedin_says_why_it_read_nothing():
    hooks = {"linkedin_collect": lambda h: pytest.fail("read"), "linkedin_available": lambda: False}
    assert L.read_linkedin(ctx(site=li_site(None), hooks=hooks))["status"] == "none"
    assert L.read_linkedin(ctx(site={"status": "failed"}, hooks=hooks))["status"] == "failed"
    r = L.read_linkedin(ctx(site=li_site(), hooks=hooks))
    assert r["status"] == "skipped" and "no LinkedIn account" in r["note"]

    def wrong(h):
        raise RuntimeError("gorgias does not look like Gorgias: the page reads linkedin.com/company/gorgias (Gorgias Pizza)")
    r = L.read_linkedin(ctx(site=li_site(), hooks={"linkedin_collect": wrong}))
    assert r["status"] == "failed" and "Gorgias Pizza" in r["note"]

    def gone(h):
        raise RuntimeError("Could not resolve LinkedIn company 'gorgias': Unipile returned 404 for this route "
                           "-- the API path may have changed.")
    r = L.read_linkedin(ctx(site=li_site(), hooks={"linkedin_collect": gone}))
    assert r["note"] == "no LinkedIn page at linkedin.com/company/gorgias: the link on its site is out of date"


# == which detectors run for whom =====================================================

def test_specialist_detectors_run_only_for_the_kinds_of_business_they_fit():
    run = lambda a: {n for n in mc.NAMES if mc.applies(n, a)}  # noqa: E731
    assert run("local_single") == run("multi_location") == set(mc.NAMES) - {"subdomains", "headcount", "linkedin"}
    assert run("ecommerce") == set(mc.NAMES) - {"headcount", "linkedin"}
    assert run("b2b_product") == run("b2b_services") == set(mc.NAMES)
    assert run("manufacturer") == set(mc.NAMES) - {"subdomains"}
    assert run(None) == run("local_single")


def test_slow_or_paid_reads_are_kept_longer():
    snap = {"last_seen_at": NOW - timedelta(days=3)}
    assert mc._fresh(snap, NOW, "subdomains") and mc._fresh(snap, NOW, "headcount")
    assert not mc._fresh(snap, NOW, "catalog")
    assert not mc._fresh({"last_seen_at": NOW - timedelta(days=8)}, NOW, "linkedin")
    assert mc._fresh({"last_seen_at": NOW - timedelta(days=29)}, NOW, "headcount")


# == end to end on Postgres ===========================================================

from test_market_radar_store import OWNER, pg  # noqa: E402,F401


def test_a_b2b_clients_collection_runs_every_detector_and_stores_their_moves(pg, monkeypatch):
    from test_market_radar_collect import world
    from tracker import market_radar_ledger as ledger
    from tracker import market_radar_signals as S
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.delenv("COMPANIES_HOUSE_API_KEY", raising=False)
    me = pg.upsert_entity("client.example", name="Client", country="US", archetype="b2b_product")
    pg.set_profile(me, {"name": "Client", "archetype": "b2b_product"})
    client = pg.upsert_client(OWNER, me)
    rival = pg.upsert_entity("acme.com", name="Acme")
    pg.propose_competitor(client, OWNER, rival, "direct", confidence=0.9)
    io, _net = world([], [], [])
    base_get = io["get"]
    sub = submissions("Acme Corp", [("8-K", (NOW - timedelta(days=3)).date().isoformat(), "5.02",
                                     "0000000001-26-000001", "k.htm")])

    def get(url, **kw):
        if url.startswith("https://efts.sec.gov/"):
            return fts(("0000000042", "Acme Corp (CIK 0000000042)"))
        if url.startswith("https://data.sec.gov/"):
            return sub
        if url.startswith("https://www.sec.gov/"):
            return ok("Item 5.02 On October 7 Jane Roe was appointed Chief Executive Officer.")
        return base_get(url, **kw)
    now = datetime.now(timezone.utc)
    recent = (now - timedelta(days=5)).replace(tzinfo=None)
    io.update(get=get,
              crt_connect=lambda **kw: Conn([("mcp.acme.com", recent), ("app.acme.com", datetime(2019, 1, 1))]),
              apollo_enrich=apollo(300, 0.25),
              linkedin_collect=lambda h: ([{"platform_post_id": "9", "caption": "Acme opens a Berlin office for "
                                            "its European customers.", "posted_at": now.isoformat()}], {"page": "p"}),
              linkedin_available=lambda: True)
    orig = io["read_site"]
    io["read_site"] = lambda url: dict(orig(url), signals=dict(orig(url)["signals"], socials={"linkedin": "acme"}))
    run = pg.create_run(client, OWNER, "collect")
    out = mc.collect_client(client, OWNER, run_id=run, io=io, store=pg, now=now)
    rows = {r["detector"]: r for r in out["companies"][0]["rows"]}
    assert set(rows) == set(mc.NAMES)
    assert {k: rows[k]["status"] for k in ("filings", "subdomains", "headcount", "linkedin", "registry")} == \
        {"filings": "ok", "subdomains": "ok", "headcount": "ok", "linkedin": "ok", "registry": "none"}
    types = {e["type"]: e for e in pg.recent_events([rival])}
    assert {"new_subdomain", "leadership_change", "hiring_surge", "announcement"} <= set(types)
    assert "Jane Roe was appointed" in types["leadership_change"]["summary"]
    lines = {l["detector"]: l["text"] for l in out["coverage"]}
    assert lines["filings"] == "Stock-market filings read for 1 of 1 competitors."
    assert lines["registry"] == "UK company register read for 0 of 1 competitors; 1 has no UK company number on its site."
    # the Apollo credit is in the ledger, as credits rather than dollars
    led = ledger.summary(run)
    assert led["by_provider"].get("apollo") == 0 and led["calls_by_status"].get("done") == 1
    # the LinkedIn post reaches the signal engine as a post from its LinkedIn page
    posts = [e for e in pg.recent_events([rival]) if e["type"] == "announcement"]
    items, _ = S.items_to_read([], posts, {}, now)
    assert items[0]["publisher"] == "its LinkedIn page" and items[0]["detector"] == "linkedin"
    # a dentist's competitors get none of it
    dent = pg.upsert_client(OWNER, pg.upsert_entity("dent.example", archetype="local_single"))
    pg.propose_competitor(dent, OWNER, pg.upsert_entity("rivaldent.example"), "direct", confidence=0.9)
    io2, _ = world([], [], [])
    io2["get"] = get
    io2["crt_connect"] = lambda **kw: pytest.fail("certificate logs read for a dentist")
    out = mc.collect_client(dent, OWNER, io=io2, store=pg, now=now)
    assert {r["detector"] for r in out["companies"][0]["rows"]} == set(mc.NAMES) - {"subdomains", "headcount", "linkedin"}
    assert {l["detector"] for l in out["coverage"]} == set(mc.NAMES) - {"subdomains", "headcount", "linkedin"}


def test_linkedin_is_read_for_a_few_companies_per_collection(pg, monkeypatch):
    from test_market_radar_collect import world
    monkeypatch.setattr(L, "PER_COLLECTION", 2)
    me = pg.upsert_entity("client.example", name="Client", archetype="b2b_services")
    pg.set_profile(me, {"name": "Client", "archetype": "b2b_services"})
    client = pg.upsert_client(OWNER, me)
    for i in range(4):
        pg.propose_competitor(client, OWNER, pg.upsert_entity("rival%d.example" % i), "direct",
                              confidence=0.9 - i / 10)
    io, _ = world([], [], [])
    orig = io["read_site"]
    io["read_site"] = lambda url: dict(orig(url), signals=dict(orig(url)["signals"], socials={"linkedin": "x"}))
    read = []
    io.update(linkedin_collect=lambda h: read.append(h) or ([], {"page": "p"}), linkedin_available=lambda: True,
              crt_connect=lambda **kw: Conn([]), apollo_enrich=apollo(10))
    out = mc.collect_client(client, OWNER, io=io, store=pg, parallel=1)
    assert len(read) == 2
    rows = [next(r for r in c["rows"] if r["detector"] == "linkedin") for c in out["companies"]]
    assert [r["status"] for r in rows] == ["empty", "empty", "skipped", "skipped"]
    assert "at most 2 companies per collection" in rows[-1]["note"]
