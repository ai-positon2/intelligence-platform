"""Market Radar, Phase 3: the live self-test of every detector.

Each check runs one detector (and, since Phase 5, each industry-pulse
source) against a public source known to have what it reads (Aspen Dental's ~1,100 offices in its sitemap and its Phenom
careers site, Allbirds' Shopify catalog and review scores, NutriBullet's
WooCommerce store, known job boards) and says whether the answer looks
right. It writes nothing: the database rules are covered by the tests, and
this exists to answer "does each source still answer from Railway, in the
shape the code expects?".

A check that did not finish inside the time budget is reported as not
finished, never as passed. Free: no model, no paid search.
"""
from __future__ import annotations

import concurrent.futures
import time
from datetime import datetime, timezone

BUDGET_S = 95


def _ctx(entity, rs):
    from . import market_radar_http as http
    from . import market_radar_news as news
    from . import market_radar_site as site
    return {"entity": entity, "site": rs, "get": http.get, "get_json": http.get_json,
            "fetch": site.fetch, "now": datetime.now(timezone.utc), "prev": {}, "results": {},
            "client_country": "US", "news_breaker": news.Breaker()}


def _sites(domains):
    from . import market_radar_site as site
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(domains)) as pool:
        return dict(zip(domains, pool.map(lambda d: site.read_site("https://%s/" % d), domains)))


def run(budget_s=BUDGET_S):
    from . import market_radar_detectors as det
    from . import market_radar_http as http
    from . import market_radar_jobs as jobs
    from . import market_radar_news as news
    started = time.monotonic()
    sites = _sites(["aspendental.com", "allbirds.com", "nutribullet.com"])
    aspen = {"id": 0, "domain": "aspendental.com", "name": "Aspen Dental", "country": "US"}
    allbirds = {"id": 0, "domain": "allbirds.com", "name": "Allbirds", "country": "US"}
    nutri = {"id": 0, "domain": "nutribullet.com", "name": "NutriBullet", "country": "US"}

    def reviews_after_catalog():
        c = _ctx(allbirds, sites["allbirds.com"])
        c["results"]["catalog"] = det.read_catalog(c)
        return det.read_reviews(c)

    def phenom():
        got = jobs.read_phenom(http.get, "https://careers.aspendental.com/us/en",
                               datetime.now(timezone.utc))
        if not got:
            return {"status": "failed", "note": "the Phenom search page did not answer",
                    "items": 0, "payload": None}
        board = got[0]
        return {"status": "ok", "note": "%d open roles in %d places" % (board["open"], len(got[2])),
                "items": board["open"], "payload": {"places": len(got[2])}}

    def board(vendor, slug):
        def check():
            found, total, complete, note = jobs.READERS[vendor](http.get_json, slug,
                                                                datetime.now(timezone.utc))
            if found is None:
                return {"status": "failed", "note": note, "items": 0, "payload": None}
            return {"status": "ok", "note": "%d open roles" % total, "items": total,
                    "payload": {"complete": complete}}
        return check

    def pulse_news():
        from . import market_radar_pulse as mp
        plan = {"queries": ["NHS dentistry"], "match_terms": ["dental", "dentist", "dentistry"],
                "regulator_terms": []}
        items, per_query, failures, dropped = mp.read_google(plan, "GB", get=http.get,
                                                             breaker=news.Breaker())
        if failures:
            return {"status": "failed", "note": "; ".join(failures), "items": 0}
        return {"status": "ok", "items": len(items), "note": "%d of %d headlines kept; %d adverts "
                "and %d off topic left out" % (len(items), per_query[0]["items"],
                                               dropped["noise"], dropped["off_topic"])}

    def federal_register():
        from . import market_radar_pulse as mp
        docs, note = mp.federal_register(["medicare"], [], get_json=http.get_json,
                                         now=datetime.now(timezone.utc))
        if docs is None:
            return {"status": "failed", "note": note, "items": 0}
        return {"status": "ok", "items": len(docs), "note": note}

    def trade_feed():
        from . import market_radar_pulse as mp
        from . import market_radar_site as site
        feed, items, note = mp.find_feed("https://www.dentistrytoday.com/", fetch=site.fetch,
                                         get=http.get)
        if not feed:
            return {"status": "failed", "note": note, "items": 0}
        return {"status": "ok", "items": len(items), "note": "%d items from %s" % (len(items), feed)}

    # Phase 10: B2B sources. crt.sh's full query takes 70 to 80 s, more than
    # this budget allows, so the check proves its Postgres port answers from
    # here; a collection proves the query.
    def crtsh():
        import psycopg2
        from . import market_radar_b2b as b2b
        conn = b2b._connect_with_retry(psycopg2.connect, time.sleep)
        try:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            return {"status": "ok", "items": cur.fetchone()[0], "note": "connected"}
        finally:
            conn.close()

    def certspotter():
        from . import market_radar_b2b as b2b
        names, pages = b2b.certspotter_names("gorgias.com", http.get)
        return {"status": "ok", "items": len(names), "note": "%d host names in %d pages" % (
            len(names), pages)}

    def sec():
        from . import market_radar_b2b as b2b
        r = b2b.read_filings(_ctx(allbirds, None))
        return dict(r, items=1 if r["status"] in ("ok", "empty") else 0)

    def linkedin_account():
        from . import unipile_transport
        acct = unipile_transport.account_for_platform("linkedin")
        return {"status": "ok" if acct else "failed", "items": 1 if acct else 0,
                "note": "a connected LinkedIn account" if acct else
                "no connected LinkedIn account on Unipile"}

    def key(var, what):
        import os

        def check():
            got = bool(os.environ.get(var))
            return {"status": "ok" if got else "skipped", "items": 1 if got else 0,
                    "note": "%s is %s (%s)" % (var, "set" if got else "not set", what)}
        return check

    # (name, what it proves, function, minimum items to pass)
    checks = [
        ("news", "Google News, US edition, Aspen Dental",
         lambda: news.read_news(_ctx(aspen, None)), 1),
        ("locations", "aspendental.com sitemaps: ~1,100 offices",
         lambda: det.read_locations(_ctx(aspen, sites["aspendental.com"])), 500),
        ("catalog_shopify", "allbirds.com products.json",
         lambda: det.read_catalog(_ctx(allbirds, sites["allbirds.com"])), 50),
        ("catalog_woocommerce", "nutribullet.com WooCommerce store API",
         lambda: det.read_catalog(_ctx(nutri, sites["nutribullet.com"])), 20),
        ("reviews", "allbirds.com product review counts", reviews_after_catalog, 1),
        ("promotions", "aspendental.com homepage offers",
         lambda: det.read_promotions(_ctx(aspen, sites["aspendental.com"])), 0),
        ("pages", "aspendental.com home, offer and pricing pages",
         lambda: det.read_pages(_ctx(aspen, sites["aspendental.com"])), 2),
        ("newsroom", "allbirds.com news feed",
         lambda: det.read_newsroom(_ctx(allbirds, sites["allbirds.com"])), 1),
        ("jobs_phenom", "careers.aspendental.com (Phenom)", phenom, 100),
        ("jobs_greenhouse", "Greenhouse board: airbnb", board("greenhouse", "airbnb"), 1),
        ("jobs_lever", "Lever board: palantir", board("lever", "palantir"), 1),
        ("jobs_ashby", "Ashby board: ramp", board("ashby", "ramp"), 1),
        ("pulse_news", "Google News, UK edition, industry search with the advert filter",
         pulse_news, 10),
        ("federal_register", "Federal Register rules naming Medicare, last 90 days",
         federal_register, 1),
        ("trade_feed", "Feed discovery on a trade publication (dentistrytoday.com)",
         trade_feed, 5),
        ("crtsh", "crt.sh certificate-log database answers on port 5432", crtsh, 1),
        ("certspotter", "Cert Spotter certificate log, gorgias.com", certspotter, 10),
        ("sec_filings", "SEC EDGAR: allbirds.com found by its own filings", sec, 1),
        ("linkedin_account", "Unipile: a connected LinkedIn account for company posts",
         linkedin_account, 1),
        ("apollo_key", "Apollo key for headcount (checked, no credit spent)",
         key("APOLLO_API_KEY", "headcount"), 1),
        ("companies_house_key", "Companies House key for the UK company register",
         key("COMPANIES_HOUSE_API_KEY", "UK company register"), 1),
    ]
    results = []
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=6)
    futures = {pool.submit(fn): (name, proves, need) for name, proves, fn, need in checks}
    left = max(5.0, budget_s - (time.monotonic() - started))
    done, _ = concurrent.futures.wait(futures, timeout=left)
    for f, (name, proves, need) in futures.items():
        if f not in done:
            results.append({"check": name, "proves": proves, "passed": False,
                            "status": "not finished", "note": "did not finish in time"})
            continue
        try:
            r = f.result()
        except Exception as e:
            results.append({"check": name, "proves": proves, "passed": False, "status": "broke",
                            "note": "%s: %s" % (type(e).__name__, str(e)[:200])})
            continue
        passed = r["status"] in ("ok", "empty") and (r.get("items") or 0) >= need
        results.append({"check": name, "proves": proves, "passed": passed,
                        "status": r["status"], "items": r.get("items"),
                        "note": (r.get("note") or "")[:300]})
    pool.shutdown(wait=False, cancel_futures=True)
    order = [c[0] for c in checks]
    results.sort(key=lambda x: order.index(x["check"]))
    return {"passed": sum(1 for r in results if r["passed"]), "total": len(results),
            "seconds": round(time.monotonic() - started, 1),
            "sites": {d: {"status": rs.get("status"),
                          "pages": sum(1 for p in rs.get("pages") or [] if p["status"] == "ok")}
                      for d, rs in sites.items()},
            "checks": results}
