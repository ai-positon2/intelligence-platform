"""Business Research: what a website carries for measuring and selling, read from its tags (tracker/paid_site_tech.py)."""
from tests import strategy_fakes as F
from tracker import paid_site_tech as tech, paid_strategy_research as rs
from tracker.paid_qa_web import _Page


def parse(html):
    p = _Page()
    p.feed(html)
    p.close()
    return p


GTM = "<script>(function(w,d,s,l,i){w[l]=w[l]||[];})(window,document,'script','dataLayer','GTM-ABC1234');</script>"
GA4 = '<script async src="https://www.googletagmanager.com/gtag/js?id=G-ABCDE12345"></script><script>gtag("config","G-ABCDE12345");</script>'
META = "<script>!function(f,b){}(window);fbq('init','1234567890123');fbq('track','PageView');</script>"


def test_the_common_tags_are_named_with_their_ids():
    page = parse("<html><head><meta name='viewport' content='width=device-width, initial-scale=1'>%s%s%s"
                 "<script src='https://cdn.callrail.com/companies/1/x/12/swap.js'></script></head><body>x</body></html>" % (GTM, GA4, META))
    got = tech.detect(page, "https://acme.test/")
    by = {t["name"]: t for t in got["tools"]}
    assert by["Google Tag Manager"]["ids"] == ["GTM-ABC1234"] and by["Google Analytics 4"]["ids"] == ["G-ABCDE12345"]
    assert by["Meta Pixel"]["ids"] == ["1234567890123"] and by["CallRail"]["category"] == "call tracking"
    assert got["mobile"] is True and got["https"] is True


def test_a_page_with_no_tags_says_so_and_is_not_marked_mobile_or_secure():
    got = tech.detect(parse("<html><body>hello</body></html>"), "http://acme.test/")
    assert got == {"tools": [], "mobile": False, "schema": [], "https": False}


def test_schema_types_are_read_from_json_ld():
    page = parse('<html><head><script type="application/ld+json">{"@type":"Dentist","aggregateRating":{"@type":"AggregateRating"},"@type":"Dentist"}</script></head></html>')
    assert tech.detect(page)["schema"] == ["Dentist", "AggregateRating"]


def test_a_few_ids_at_most_and_each_once():
    inline = "".join("<script>fbq('init','%d');</script>" % (10000000 + i) for i in range(6)) + "<script>fbq('init','10000000');</script>"
    ids = [t for t in tech.detect(parse("<html><head>%s</head></html>" % inline))["tools"] if t["name"] == "Meta Pixel"][0]["ids"]
    assert len(ids) == tech.MAX_IDS and len(set(ids)) == len(ids)


def test_each_tool_is_only_named_when_its_own_tag_is_there():
    for html, name in (("<script src='https://snap.licdn.com/li.lms-analytics/insight.min.js'></script>", "LinkedIn Insight Tag"), ("<script src='https://bat.bing.com/bat.js'></script>", "Microsoft Advertising UET tag"),
                       ("<script src='https://static.hotjar.com/c/hotjar-1.js?sv=6'></script>", "Hotjar"), ("<script src='https://js.hs-scripts.com/12345678.js'></script>", "HubSpot"),
                       ("<script src='https://assets.calendly.com/assets/external/widget.js'></script>", "Online booking"), ("<script src='https://cdn.cookielaw.org/x.js'></script>", "Cookie consent banner"),
                       ("<script>gtag('config','AW-123456789')</script>", "Google Ads tag"), ("<script src='https://www.google-analytics.com/analytics.js'></script>", "Universal Analytics (switched off by Google)")):
        names = [t["name"] for t in tech.detect(parse("<html><head>%s</head></html>" % html))["tools"]]
        assert names == [name], (html, names)
    assert tech.detect(parse("<html><head><script src='https://example.com/jquery.js'></script></head></html>"))["tools"] == []


def page(tools=(), mobile=True, https=True, schema=()):
    return {"tech": {"tools": [{"name": n, "category": c, "ids": i} for n, c, i in tools], "mobile": mobile, "schema": list(schema), "https": https}}


def test_the_site_summary_counts_pages_and_says_what_was_not_seen_as_not_seen():
    s = tech.summarise([page([("Google Analytics 4", "analytics", ["G-AAAA1111"])], schema=["Dentist"]), page([("Google Analytics 4", "analytics", ["G-AAAA1111", "G-BBBB2222"])], mobile=False)])
    assert s["pages"] == 2 and s["mobilePages"] == 1 and s["httpsPages"] == 2 and s["schema"] == ["Dentist"]
    assert s["tools"] == [{"name": "Google Analytics 4", "category": "analytics", "ids": ["G-AAAA1111", "G-BBBB2222"], "pages": 2}]
    gaps = " ".join(s["gaps"])
    assert "No Google Ads conversion tag was seen" in gaps and "No call tracking script was seen" in gaps and "1 of 2 pages read did not declare a mobile viewport" in gaps
    assert "No Google Analytics" not in gaps and "structured data" not in gaps and "https" not in gaps
    assert "not seen" in s["note"] and "confirm each with the client" in s["note"]


def test_no_analytics_at_all_and_only_the_retired_one_are_different_findings():
    none = tech.summarise([page()])
    assert none["gaps"][0] == "No Google Analytics or Google Tag Manager tag was seen on the 1 page read."
    old = tech.summarise([page([("Universal Analytics (switched off by Google)", "analytics", [])])])
    assert "stopped collecting data" in old["gaps"][0] and "No Google Analytics" not in " ".join(old["gaps"])
    tagmanager = tech.summarise([page([("Google Tag Manager", "tag manager", ["GTM-ABC1234"])])])
    assert not any("Google Ads conversion tag" in g or "Google Analytics" in g for g in tagmanager["gaps"])       # a tag manager may load both
    assert tech.summarise([page(https=False)])["gaps"][-1] == "1 of 1 pages read were not served over https."


def test_nothing_read_is_nothing_said():
    assert tech.summarise([]) == {"pages": 0, "tools": [], "schema": [], "mobilePages": 0, "httpsPages": 0, "gaps": [], "note": ""}
    assert tech.summarise([{"title": "a page read before tags were looked for"}, None])["pages"] == 0


def test_the_pages_the_website_research_reads_carry_their_tags():
    pg = rs.read_page(F.HOME, "https://www.acmedental.com/", 2600)
    assert pg["tech"]["https"] is True and pg["tech"]["schema"] == ["Dentist"] and pg["tech"]["tools"] == []


def test_a_viewport_is_mobile_only_when_it_follows_the_device_width():
    assert tech.detect(parse("<html><head><meta name='viewport' content='width=device-width,initial-scale=1'></head></html>"))["mobile"] is True
    assert tech.detect(parse("<html><head><meta name='viewport' content='initial-scale=1'></head></html>"))["mobile"] is False
    assert tech.detect(parse("<html><head><meta name='viewport' content='width=1024'></head></html>"))["mobile"] is False


def test_the_summary_keeps_sixteen_schema_types_at_most_and_lists_tools_in_a_steady_order():
    many = ["Type%d" % i for i in range(20)]
    s = tech.summarise([page(schema=many)])
    assert s["schema"] == many[:16]
    mixed = tech.summarise([page([("Meta Pixel", "advertising", []), ("Hotjar", "behaviour", []), ("Google Ads tag", "advertising", []), ("CallRail", "call tracking", [])])])
    assert [t["name"] for t in mixed["tools"]] == ["Google Ads tag", "Meta Pixel", "Hotjar", "CallRail"]            # by category (advertising, behaviour, call tracking), then name


def test_no_structured_data_is_a_finding_and_its_presence_is_not():
    assert "No structured data (schema.org) was seen on the pages read." in tech.summarise([page()])["gaps"]
    assert not any("structured data" in g for g in tech.summarise([page(schema=["Dentist"])])["gaps"])


def test_an_id_must_be_written_as_an_id_not_just_look_like_one():
    for html in ("<script>var el = document.getElementById('g-recaptcha-response'); el.addClass('gtm-sidebar'); var ua = 'ua-12345-1'; aw-123456789;</script>",):
        assert tech.detect(parse("<html><head>%s</head></html>" % html))["tools"] == []
    real = [t["name"] for t in tech.detect(parse("<html><head><script>x('G-ABCDE12345'); y('GTM-ABC1234'); z('UA-123456-1'); w('AW-123456789')</script></head></html>"))["tools"]]
    assert real == ["Google Tag Manager", "Google Analytics 4", "Universal Analytics (switched off by Google)", "Google Ads tag"]


def test_a_vendor_address_inside_the_pages_data_is_not_a_widget():
    ld = '<script type="application/ld+json">{"@type":"Dentist","sameAs":["https://www.zocdoc.com/practice/acme","https://www.birdeye.com/acme","https://onetrust.com/x"]}</script>'
    assert tech.detect(parse("<html><head>%s</head></html>" % ld))["tools"] == []
    loaded = [t["name"] for t in tech.detect(parse("<html><head><script src='https://www.zocdoc.com/widget.js'></script><script src='https://cdn.cookielaw.org/x.js'></script></head></html>"))["tools"]]
    assert sorted(loaded) == ["Cookie consent banner", "Online booking"]
    assert [t["name"] for t in tech.detect(parse("<html><head><script src='https://WWW.GOOGLETAGMANAGER.COM/gtm.js?id=GTM-ABC1234'></script></head></html>"))["tools"]] == ["Google Tag Manager"]
