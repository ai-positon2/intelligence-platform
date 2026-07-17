#!/usr/bin/env python3
"""Offline tests for the visitor_intelligence engine (no network, no Apollo).
Run:  python3 -m visitor_intelligence.tests
"""
from visitor_intelligence.resolver import (classify_connection, domain_from_host,
                                        _score, _org_to_domain)
from visitor_intelligence.intent import score_intent

P = F = 0
def ck(name, cond):
    global P, F
    if cond: P += 1; print("  ok  ", name)
    else: F += 1; print("  FAIL", name)

def test_gate():
    for org, exp in [("Comcast Cable", "isp"), ("T-Mobile USA", "mobile"),
                    ("Amazon.com Inc", "hosting"), ("Stripe, Inc.", "business"),
                    ("Boston University", "education"), ("City of Boston", "government")]:
        ct, _ = classify_connection(org, None, None, 1024)
        ck("classify %s -> %s" % (org, exp), ct == exp)
    # explicit IPinfo privacy flags force hosting
    ct, _ = classify_connection("Whatever Corp", None, None, 256, privacy={"vpn": True})
    ck("privacy.vpn -> hosting", ct == "hosting")
    # explicit ipinfo asn type wins
    ct, _ = classify_connection("Foo", None, None, 256, ipinfo_type="isp")
    ck("ipinfo type=isp respected", ct == "isp")

def test_domain_extraction():
    ck("corp PTR", domain_from_host("smtp.acme-robotics.com") == "acme-robotics.com")
    ck("ISP PTR None", domain_from_host("pool-71-1-2-3.bstnma.fios.verizon.net") is None)
    ck("AWS PTR None", domain_from_host("ec2-52-1-2-3.compute-1.amazonaws.com") is None)
    ck("co.uk", domain_from_host("mail.acme.co.uk") == "acme.co.uk")

def test_gate_blocks_scoring():
    for ct in ("isp", "mobile", "hosting"):
        conf, dom, *_ = _score(ct, [("reverse_dns", "acme.com")], 1024)
        ck("gate blocks %s (conf 0)" % ct, conf == 0.0 and dom is None)

def test_corroboration():
    c1 = _score("business", [("ipinfo_org", "acme.com")], 1024)[0]
    c2 = _score("business", [("ipinfo_org", "acme.com"), ("reverse_dns", "acme.com")], 1024)[0]
    ck("two agreeing methods > one", c2 > c1)

def test_org_to_domain():
    ck("org->domain strips suffix", _org_to_domain("Stripe, Inc.") == "stripe.com")

def test_intent():
    s, stage, _ = score_intent(["/pricing", "/demo", "/product"], pageviews=3)
    ck("high intent -> decision/consideration", s >= 40)
    s2, stage2, _ = score_intent(["/blog"], pageviews=1)
    ck("low intent -> awareness", stage2 == "awareness")

def test_identity_graph():
    from visitor_intelligence.identity_graph import (IdentityGraph, GraphStore,
                                                    CoopFileProvider, sha256_email)
    g = IdentityGraph(store=GraphStore(":memory:"))
    g.observe("vidA", ip="199.47.216.10")
    ck("cold cookie stays anonymous", g.resolve_person("vidA").resolved is False)
    g.identify("vidA", email="jordan@acme.com", name="Jordan Lee", source="login")
    pm = g.resolve_person("vidA")
    ck("login retro-stitches to person", pm.resolved and pm.full_name == "Jordan Lee")
    ck("first-party method + conf 1.0", pm.method == "first_party" and pm.confidence == 1.0)
    # co-op provider resolves a cold cookie via hashed email
    import tempfile, os, csv
    hem = sha256_email("dana@globex.com")
    tf = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
    w = csv.DictWriter(tf, fieldnames=["hashed_email", "full_name", "email", "title", "company", "linkedin_url"])
    w.writeheader(); w.writerow({"hashed_email": hem, "full_name": "Dana Kim",
                                "email": "dana@globex.com", "title": "CTO", "company": "Globex", "linkedin_url": ""})
    tf.close()
    g2 = IdentityGraph(store=GraphStore(":memory:"), providers=[CoopFileProvider(tf.name)])
    pm2 = g2.resolve_person("vidCold", extra_signals={"hashed_email": hem})
    ck("co-op resolves cold cookie", pm2.resolved and pm2.full_name == "Dana Kim")
    ck("co-op basis recorded", pm2.lawful_basis == "coop")
    os.unlink(tf.name)


def test_free_enrich_offline():
    from visitor_intelligence.free_enrich import (
        _extract_jsonld_org, _extract_og, detect_technologies)

    html = '''<html><head>
    <title>Acme Robotics | Industrial Automation</title>
    <meta property="og:description" content="Acme builds robots.">
    <meta property="og:site_name" content="Acme Robotics">
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Organization","name":"Acme Robotics",
     "legalName":"Acme Robotics, Inc.","description":"We build robots.",
     "sameAs":["https://www.linkedin.com/company/acme-robotics","https://twitter.com/acme"],
     "address":{"addressLocality":"Boston","addressCountry":"US"}}
    </script>
    <script src="https://cdn.segment.com/analytics.js/v1/x/analytics.min.js"></script>
    </head><body><div id="app"></div>
    <script src="/_next/static/chunks/main.js"></script>
    </body></html>'''

    org = _extract_jsonld_org(html)
    ck("jsonld org name", org.get("legalName") == "Acme Robotics, Inc.")
    ck("jsonld sameAs has linkedin", any("linkedin.com" in u for u in org.get("sameAs", [])))

    og = _extract_og(html)
    ck("og description extracted", og.get("description") == "Acme builds robots.")

    techs = detect_technologies(html)
    ck("detects Segment from script src", "Segment" in techs)
    ck("detects React from _next/static asset path", "React" in techs)
    ck("does not hallucinate unrelated tech", "Shopify" not in techs)


def test_team_page_title_filter():
    from visitor_intelligence.free_enrich import _TITLE_KEYWORDS
    ck("real title matches", bool(_TITLE_KEYWORDS.search("VP of Engineering")))
    ck("real title matches (CEO)", bool(_TITLE_KEYWORDS.search("Co-founder & CEO")))
    ck("marketing copy does not match", not _TITLE_KEYWORDS.search("500M+ API requests per day"))
    ck("generic heading does not match", not _TITLE_KEYWORDS.search("See the latest from Acme"))


def test_resolve_visitor_no_apollo_by_default():
    """resolve_visitor must never accept/require an apollo_key -- that call is
    a completely separate, explicit function (deepen_with_apollo)."""
    import inspect
    from visitor_intelligence.pipeline import resolve_visitor, deepen_with_apollo
    sig = inspect.signature(resolve_visitor)
    ck("resolve_visitor has no apollo_key param", "apollo_key" not in sig.parameters)
    ck("deepen_with_apollo exists as a separate opt-in function", callable(deepen_with_apollo))


if __name__ == "__main__":
    for t in [test_gate, test_domain_extraction, test_gate_blocks_scoring,
            test_corroboration, test_org_to_domain, test_intent, test_identity_graph,
            test_free_enrich_offline, test_team_page_title_filter,
            test_resolve_visitor_no_apollo_by_default]:
        print(t.__name__); t()
    print("\n%d passed, %d failed" % (P, F))
    raise SystemExit(1 if F else 0)
