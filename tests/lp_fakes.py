"""Landing Page & Relevancy Check: the shapes the checks and the report are given, built from a good page that each test spoils in one way."""
import copy

GOOD_TEXT = ("Emergency dentist in Boston. Same day appointments for tooth pain, broken teeth and dental emergencies. Our dentists are licensed and kind. "
             "Cleanings start at $99 for new patients. Free consultation. " + "We care for families across Boston and Cambridge. " * 40)


def facts(**over):
    f = {"title": "Emergency Dentist Boston | Acme Dental", "metaDescription": "Same day care.", "viewport": "width=device-width, initial-scale=1", "noindex": False, "lang": "en",
         "h1": ["Emergency Dentist in Boston"], "h1Count": 1, "h2": ["Why choose Acme"], "h3": [], "text": GOOD_TEXT, "wordCount": 400,
         "forms": [{"fields": [{"kind": "text", "name": "name", "required": True, "label": ""}, {"kind": "tel", "name": "phone", "required": True, "label": ""},
                               {"kind": "email", "name": "email", "required": False, "label": ""}], "visible": 3, "required": 2, "checks": 0, "search": False,
                    "action": "/send", "method": "post", "phone": True, "email": True}],
         "embeds": [], "booking": False, "commerce": False,
         "ctas": [{"text": "Book now", "kind": "strong", "at": 200, "tag": "a"}], "tel": ["+16175550123"], "mailto": [], "phones": ["(617) 555-0123"], "address": True, "emails": [],
         "links": {"total": 20, "nav": 5, "outbound": 1}, "images": {"count": 10, "noSize": 1, "lazy": 6, "noAlt": 0},
         "head": {"blockingScripts": 1, "stylesheets": 3, "scripts": 8, "thirdParty": ["www.googletagmanager.com", "cdn.x.com"]},
         "appRoot": False, "proof": ["reviews", "star rating"], "privacyLink": True, "consent": False, "scriptBuilt": False}
    f.update(over)
    return f


def tech(**over):
    t = {"tools": [{"name": "Google Tag Manager", "category": "tag manager", "ids": ["GTM-ABC1234"]}], "mobile": True, "schema": [], "https": True}
    t.update(over)
    return t


def fetch(facts_over=None, tech_over=None, **over):
    r = {"url": "https://acme-dental.com/lp", "status": 200, "error": "", "blocked": False, "ms": 700, "responseMs": 400, "bytes": 120_000, "truncated": False, "encoding": "gzip",
         "contentType": "text/html", "finalUrl": "https://acme-dental.com/lp", "redirected": False, "collapsed": False, "retried": False, "isHtml": True,
         "facts": facts(**(facts_over or {})), "tech": tech(**(tech_over or {})), "paramsSurvive": None, "adsbot": True}
    r.update(over)
    return r


def keyword(text, cost=10.0, **over):
    k = {"text": text, "match": "phrase", "cost": cost, "clicks": 5, "impressions": 100, "conversions": 0.0, "qs": 7, "expectedCtr": "average", "adRelevance": "above",
         "landingPage": "above", "url": ""}
    k.update(over)
    return k


def term(text, cost=5.0, clicks=4):
    return {"text": text, "cost": cost, "clicks": clicks, "conversions": 0.0}


def group(gid="g1", name="Emergency", campaign="Boston Search", keywords=None, terms=None, headlines=None, descriptions=None, urls=("https://acme-dental.com/lp",), cost=100.0, ctype="SEARCH"):
    return {"id": gid, "name": name, "campaign": campaign, "campaignType": ctype, "cost": cost, "clicks": 50, "conversions": 2.0,
            "ads": [{"headlines": headlines if headlines is not None else ["Emergency Dentist Boston", "Same Day Appointments"],
                     "descriptions": descriptions if descriptions is not None else ["Cleanings from $99. Free consultation."], "urls": list(urls), "cost": cost, "type": "RESPONSIVE_SEARCH_AD"}],
            "keywords": keywords if keywords is not None else [keyword("emergency dentist boston", 50.0), keyword("tooth pain", 20.0)],
            "terms": terms if terms is not None else [term("emergency dentist boston"), term("dentist boston"), term("tooth pain boston")],
            "pages": {}}


def page(fetch_over=None, groups=None, **over):
    p = {"id": "acme-dental.com/lp", "url": "https://acme-dental.com/lp", "name": "acme-dental.com/lp", "spend": 1000.0, "clicks": 500, "conversions": 25.0, "impressions": 20000,
         "devices": {"mobile": {"clicks": 350, "cost": 700.0, "conversions": 18.0}, "desktop": {"clicks": 150, "cost": 300.0, "conversions": 10.0}},
         "speed": 8, "fetch": fetch(**(fetch_over or {})), "groups": copy.deepcopy(groups) if groups is not None else [group()], "lab": None,
         "ok": {"pages": True, "ads": True, "keywords": True, "terms": True}}
    p.update(over)
    return p


def blocked_fetch(status=403):
    return fetch(status=status, blocked=True, isHtml=False, facts=None, tech=None, adsbot=None)


def meta_ad(name="Ad 1", headlines=None, descriptions=None, urls=("https://acme-dental.com/lp",), cost=100.0, ranks=None):
    return {"name": name, "headlines": headlines if headlines is not None else ["Emergency Dentist Boston"],
            "descriptions": descriptions if descriptions is not None else ["Cleanings from $99. Free consultation."], "urls": list(urls), "cost": cost, "type": "LINK",
            "ranks": ranks if ranks is not None else {"quality": "above", "engagement": "average", "conversion": "above"}}


def meta_group(gid="meta:s1", name="Set 1", ads=None, cost=100.0, campaign="Meta Leads"):
    return {"id": gid, "name": name, "campaign": campaign, "campaignType": "META_LEADS", "cost": cost, "clicks": 50, "conversions": 2.0,
            "ads": ads if ads is not None else [meta_ad(cost=cost)], "keywords": [], "terms": [], "pages": {}, "platform": "meta"}


def meta_page(fetch_over=None, groups=None, **over):
    """A page only Meta's ads reach: the good page, its ad sets, Meta's click id and pixel."""
    f = dict(fetch_over or {})
    f.setdefault("tech_over", {"tools": [{"name": "Meta Pixel", "category": "advertising", "ids": ["1234567890"]}]})
    ok = {"pages": True, "ads": True, "keywords": True, "terms": True, "meta:ads": True, "meta:insights": True, "meta:rankings": True}
    over = dict({"platforms": ["meta"], "ok": ok, "speed": None}, **over)
    return page(fetch_over=f, groups=groups if groups is not None else [meta_group()], **over)
