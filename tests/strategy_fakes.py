"""Stand-ins shared by the tests of the Strategy & Campaign Book agent: a small website that can be crawled, an in-memory store with the
same calls as tracker/paid_strategy_store.py, and a model that answers every stage with a plausible, valid reply."""
import copy
import json
import threading

SITE = "https://www.acmedental.com/"

HOME = """<html><head><title>Acme Dental | Dentist in Austin, TX</title><meta name="description" content="Family and cosmetic dentistry in Austin. New patients welcome.">
<script type="application/ld+json">{"@type":"Dentist","aggregateRating":{"ratingValue":"4.9","reviewCount":"312"}}</script></head>
<body><nav><a href="/services/dental-implants">Dental Implants</a><a href="/services/invisalign">Invisalign</a><a href="/financing">Financing</a>
<a href="/about">About Us</a><a href="/contact">Contact</a><a href="/blog/flossing-tips">Blog</a><a href="/privacy">Privacy</a><a href="tel:+15125550100">Call (512) 555-0100</a></nav>
<h1>Your Austin Family Dentist</h1><h2>Implants, Invisalign and everyday care</h2>
<p>Acme Dental has served Austin families since 2009. Free consultation for dental implants. Same day emergency appointments. We accept most insurance plans.</p>
<a href="/contact">Book an Appointment</a><form action="/contact"><input name="name"><input name="phone"><input name="email"></form></body></html>"""

PAGES = {
    "/": HOME,
    "/services/dental-implants": """<html><head><title>Dental Implants Austin | Acme Dental</title></head><body><h1>Dental Implants in Austin</h1><h2>Replace missing teeth for good</h2>
<p>Dental implants from $1,900 per tooth with financing from $89 a month. Free consultation for dental implants. Our surgeon has placed over 2,000 implants.</p>
<a href="/contact">Book a Free Consultation</a></body></html>""",
    "/services/invisalign": """<html><head><title>Invisalign Austin | Acme Dental</title></head><body><h1>Invisalign Clear Aligners</h1>
<p>Straighten your teeth without braces. Most treatments take 12 to 18 months. Free Invisalign consultation.</p><a href="/contact">Get Started</a></body></html>""",
    "/financing": """<html><head><title>Financing | Acme Dental</title></head><body><h1>Dental Financing</h1><p>Monthly payments from $89 a month with approved credit. No interest if paid in 12 months.</p></body></html>""",
    "/about": """<html><head><title>About Acme Dental</title></head><body><h1>About Us</h1><p>Dr. Maria Lopez founded Acme Dental in 2009. Our team is trained in the latest techniques.</p></body></html>""",
    "/blog/flossing-tips": """<html><head><title>Flossing Tips | Acme Dental Blog</title></head><body><h1>Flossing Tips</h1><p>Flossing removes plaque between teeth.</p></body></html>""",
    "/contact": """<html><head><title>Contact Acme Dental</title></head><body><h1>Contact</h1><p>1200 Congress Ave, Austin, TX. Call (512) 555-0100.</p><form action="/x"><input name="name"><input name="phone"></form></body></html>""",
}
RIVAL_HOME = """<html><head><title>Smile Austin Dentistry</title></head><body><h1>Austin's Premier Cosmetic Dentist</h1><p>Veneers, whitening and implants. Book online in 60 seconds. $99 new patient special.</p>
<a href="/veneers">Porcelain Veneers</a><a href="/book">Book Online</a></body></html>"""
RIVAL_PAGES = {"/": RIVAL_HOME, "/veneers": "<html><head><title>Veneers | Smile Austin</title></head><body><h1>Porcelain Veneers</h1><p>Smile makeover in two visits.</p></body></html>",
               "/book": "<html><head><title>Book | Smile Austin</title></head><body><h1>Book Online</h1><p>Choose a time that suits you.</p></body></html>"}


class Resp:
    def __init__(self, url, body, status=200, ctype="text/html; charset=utf-8", raw=None):
        self.url, self.status_code = url, status
        self.encoding = "utf-8" if "charset" in ctype else ("ISO-8859-1" if ctype.startswith("text/") else None)       # what requests does with a page that names no charset
        self.headers = {"Content-Type": ctype}
        self.content = raw if raw is not None else body.encode("utf-8")

        class Raw:
            def __init__(s, b):
                s.b = b

            def read(s, n=None, decode_content=True):
                return s.b[:n] if n else s.b
        self.raw = Raw(self.content)

    def close(self):
        pass


def fetcher(sites=None, calls=None):
    """A fetcher over {host: {path: html}}. An unknown host or path is a 404; robots.txt allows everything unless a site has it."""
    sites = {"acmedental.com": PAGES, "smileaustin.com": RIVAL_PAGES} if sites is None else sites

    def fetch(url, timeout=10, headers=None, **kw):
        from urllib.parse import urlsplit
        if calls is not None:
            calls.append(url)
        p = urlsplit(url)
        host = p.hostname.removeprefix("www.")
        site = sites.get(host)
        if site is None:
            raise OSError("name not resolved: %s" % host)
        path = p.path or "/"
        if path == "/robots.txt":
            return Resp(url, site.get("/robots.txt", ""), 200 if "/robots.txt" in site else 404, "text/plain")
        if path == "/sitemap.xml":
            return Resp(url, site.get("/sitemap.xml", ""), 200 if "/sitemap.xml" in site else 404, "application/xml")
        body = site.get(path)
        return Resp(url, body, 200) if body is not None else Resp(url, "not found", 404)
    return fetch


class FakeStrategyStore:
    """tracker/paid_strategy_store.py, in memory."""
    StoreUnavailable = RuntimeError

    def __init__(self):
        self.rows, self.lock, self.n = {}, threading.RLock(), 0
        self.failures = {}

    def create(self, *, by, inputs, account_name="", steps=None):
        from tracker import paid_strategy as ps
        with self.lock:
            self.n += 1
            sid = "%032x" % self.n
            self.rows[sid] = {"id": sid, "created_by": by, "created_at": "2026-10-16T10:00:00+00:00", "updated_at": "2026-10-16T10:00:00+00:00", "name": inputs["name"],
                              "account_id": inputs.get("account") or "", "account_name": account_name, "inputs": copy.deepcopy(inputs), "status": "queued",
                              "progress": {"steps": steps if steps is not None else ps.steps_for(inputs), "phase": "Queued"}, "research": {}, "plan": {}, "cost": {}, "note": "", "error": "",
                              "started": None, "archived": False}
            return copy.deepcopy(self.rows[sid])

    def get(self, sid):
        with self.lock:
            return copy.deepcopy(self.rows.get(sid))

    def get_light(self, sid):
        r = self.get(sid)
        return None if not r or r["archived"] else {"id": sid, "status": r["status"], "progress": r["progress"], "error": r["error"], "note": r["note"],
                                                   "updatedAt": r["updated_at"], "started": r["started"], "kind": r["inputs"].get("kind", "strategy")}

    def list_recent(self, limit=100, kind="strategy"):
        with self.lock:
            return [copy.deepcopy(r) for r in reversed(list(self.rows.values())) if not r["archived"] and r["inputs"].get("kind", "strategy") == kind][:limit]

    def begin(self, sid, *, resume=False):
        with self.lock:
            r = self.rows[sid]
            if r["status"] == "running" and not self.failures.get("stale"):
                return False
            r["status"], r["error"], r["note"], r["started"] = "running", "", "", "now"
            if not resume:
                r["research"], r["plan"], r["cost"] = {}, {}, {}
            return True

    def set_steps(self, sid, steps, phase=None):
        with self.lock:
            p = self.rows[sid]["progress"]
            p["steps"] = copy.deepcopy(steps)
            if phase:
                p["phase"] = phase

    def set_step(self, sid, key, state, note="", phase=None):
        with self.lock:
            p = self.rows[sid]["progress"]
            for s in p["steps"]:
                if s["key"] == key:
                    s["state"], s["note"] = state, note
            if phase:
                p["phase"] = phase

    def save_research(self, sid, key, piece):
        with self.lock:
            self.rows[sid]["research"][key] = json.loads(json.dumps(piece, default=str))

    def save_plan_part(self, sid, key, value):
        with self.lock:
            self.rows[sid]["plan"][key] = json.loads(json.dumps(value, default=str))

    def save_plan(self, sid, plan):
        with self.lock:
            self.rows[sid]["plan"] = json.loads(json.dumps(plan, default=str))

    def finish(self, sid, *, cost, note=""):
        with self.lock:
            r = self.rows[sid]
            r["status"], r["cost"], r["note"], r["error"] = "done", cost, note, ""

    def fail(self, sid, error, cost=None):
        with self.lock:
            r = self.rows[sid]
            r["status"], r["error"] = "error", error
            if cost is not None:
                r["cost"] = cost

    def touch(self, sid):
        with self.lock:
            self.beats = getattr(self, "beats", 0) + 1

    def record_run(self, email):
        with self.lock:
            self.runs = getattr(self, "runs", []) + [email]

    def runs_today(self, email):
        return len([e for e in getattr(self, "runs", []) if e == email])

    def archive(self, sid):
        with self.lock:
            self.rows[sid]["archived"] = True


def head(n, base, limit):
    """n different strings, each at most `limit` characters, starting from base."""
    out, i = [], 1
    while len(out) < n:
        s = ("%s %d" % (base, i)) if i > 1 else base
        out.append(s[:limit])
        i += 1
    return out


GOOD_HEADLINES = ["Dental Implants Austin", "Free Implant Consultation", "Replace Missing Teeth", "Implants From $1,900", "Financing From $89 A Month", "Same Day Emergency Visits",
                  "Austin Family Dentist", "Book Your Visit Today", "Most Insurance Accepted", "Serving Austin Since 2009", "Dr. Maria Lopez And Team", "Call (512) 555-0100",
                  "Rated 4.9 By 312 Patients", "Trusted Implant Surgeon", "Smile With Confidence"]
GOOD_DESCRIPTIONS = ["Dental implants in Austin with a free consultation. Financing from $89 a month. Book now.",
                     "Family dentistry since 2009. Same day emergency visits and most insurance plans accepted.",
                     "Replace missing teeth with implants from our experienced surgeon. Call us to book.",
                     "Rated 4.9 stars by 312 patients. Book online or call (512) 555-0100 today."]


def model(overrides=None, calls=None):
    """A model that answers each stage with a valid reply. `overrides` maps a stage name to a dict (or a function of the user text) that replaces its reply."""
    overrides = overrides or {}
    lock = threading.Lock()

    def call(system, user, schema, *, max_tokens, stage):
        return copy.deepcopy(_answer(system, user, schema, max_tokens, stage))      # a test that edits a reply must not edit the next test's

    def _answer(system, user, schema, max_tokens, stage):
        with lock:
            if calls is not None:
                calls.append(stage)
        ov = overrides.get(stage) or overrides.get(stage.split(":")[0])
        if callable(ov):
            return ov(user)
        if ov is not None:
            return ov
        data = json.loads(user)
        if stage == "profile":
            return {"summary": "Acme Dental is a family dental practice in Austin selling implants, Invisalign and everyday care.",
                    "offerings": [{"name": "Dental implants", "description": "Single and full-arch implants", "priority": "high"},
                                  {"name": "Invisalign", "description": "Clear aligners", "priority": "medium"}],
                    "audiences": [{"name": "Missing teeth", "who": "Adults 45 plus", "need": "Replace a tooth", "intent": "researching"}],
                    "positioning": {"promise": "Your Austin family dentist", "differentiators": ["Since 2009", "Free implant consultation"], "tone": "Warm and plain"},
                    "proof": [{"point": "4.9 rating from 312 reviews", "source": SITE}], "conversionPaths": [{"action": "Form", "where": "/contact", "note": "Short form"}],
                    "compliance": [{"topic": "Healthcare advertising", "rule": "No guaranteed results", "impact": "Avoid cure or painless claims"}],
                    "siteGaps": [{"issue": "No phone on the implants page", "why": "Urgent buyers want to call", "fix": "Add the number to the header"}],
                    "facts": [{"fact": "Free consultation for dental implants", "quote": "Free consultation for dental implants", "source": SITE},
                              {"fact": "Implants from $1,900", "quote": "Dental implants from $1,900 per tooth with financing from $89 a month", "source": SITE + "services/dental-implants"},
                              {"fact": "A fact not on any page", "quote": "Voted best dentist in Texas three years running", "source": SITE}],
                    "assumptions": ["The practice has one location"], "questions": ["Which insurance plans are accepted?"]}
        if stage == "competitive":
            return {"landscape": "Smile Austin leads with cosmetic work and a $99 new patient special.",
                    "competitors": [{"name": "Smile Austin", "url": "https://smileaustin.com/", "positioning": "Premier cosmetic dentist", "offers": ["$99 new patient special"],
                                     "messages": ["Book online in 60 seconds"], "ctas": ["Book Online"], "strengths": ["Online booking"], "weaknesses": ["No implants page"], "themes": ["veneers austin"]},
                                    {"name": "Invented Dental", "url": "https://invented.example/", "positioning": "x", "offers": [], "messages": [], "ctas": [], "strengths": [], "weaknesses": [], "themes": []}],
                    "whiteSpace": [{"opportunity": "Implant financing", "why": "No competitor shows a monthly price"}], "differentiation": ["Lead with free implant consultation"], "watchOuts": ["Smile Austin may bid on the brand"]}
        if stage == "strategy":
            return {"summary": ["Win implant patients with Search first", "Use Performance Max once conversions arrive"],
                    "objectives": [{"objective": "Implant consultations", "kpi": "Cost per lead", "target": "Under 120", "basis": "Estimate from the forecast"}],
                    "funnel": [{"stage": "Capture demand", "role": "Search", "message": "Free consultation", "channels": "Search"}],
                    "channels": [{"channel": "Search", "why": "Intent", "share": 70, "priority": "High", "phase": "Launch"}, {"channel": "Performance Max", "why": "Reach", "share": 30, "priority": "Medium", "phase": "Month 2"}],
                    "budget": {"pacing": "Even across the month", "scaleRule": "Raise 20 percent when cost per lead holds for two weeks"},
                    "bidding": [{"phase": "Launch", "strategy": "Maximize conversions", "trigger": "30 conversions", "note": "Then target CPA"}],
                    "targeting": {"locations": [{"name": "Austin, Texas", "why": "Practice location"}], "devices": "All", "schedule": "All day", "demographics": "None excluded", "audiences": ["Implant researchers"]},
                    "messaging": {"pillars": [{"pillar": "Free consultation", "proof": "On the implants page", "example": "Free Implant Consultation"}], "tone": "Warm", "offers": ["Free consultation"], "avoid": ["Guaranteed results"]},
                    "social": [], "seasonality": [], "measurement": {"conversions": [{"name": "Form submit", "type": "Web", "primary": "yes", "how": "Google Tag"}], "tracking": [{"item": "Call tracking", "detail": "Forwarding number"}], "reporting": "Weekly"},
                    "tests": [{"test": "Headline offer", "hypothesis": "Free consultation lifts click through", "metric": "CTR", "when": "Week 3"}],
                    "roadmap": [{"phase": "Days 1 to 30", "actions": ["Launch Search", "Fix tracking"]}], "risks": [{"risk": "Low volume", "mitigation": "Broaden themes"}],
                    "assumptions": ["Forecast uses an assumed conversion rate"], "clientNeeds": ["Approve the offer"]}
        if stage == "architecture":
            st = {"value": "Maximize conversions, then target CPA after 30 conversions", "why": "Standard start"}
            loc = {"value": "Austin, Texas", "why": "Practice location"}
            s = {"bidStrategy": st, "devices": {"value": "", "why": ""}, "genders": {"value": "", "why": ""}, "income": {"value": "", "why": ""}, "ages": {"value": "", "why": ""},
                 "schedule": {"value": "", "why": ""}, "locations": loc, "language": {"value": "", "why": ""}}
            return {"campaigns": [{"kind": "search", "theme": "Implants", "purpose": "Capture implant searches", "share": 70, "landingPage": SITE + "services/dental-implants",
                                   "keywordFocus": ["dental implants austin"], "audience": "Adults looking to replace teeth", "notes": "Core campaign", "settings": s},
                                  {"kind": "pmax", "theme": "Local", "purpose": "Reach more of Austin", "share": 30, "landingPage": SITE, "keywordFocus": [], "audience": "Austin adults",
                                   "notes": "Starts month 2", "settings": s}]}
        if stage.startswith("build:"):
            camp = data["campaign"]
            assets = {"sitelinks": [{"text": "Dental Implants", "desc1": "Replace missing teeth", "desc2": "Free consultation", "url": "/services/dental-implants"},
                                    {"text": "Invisalign", "desc1": "Clear aligners", "desc2": "Free consultation", "url": "/services/invisalign"},
                                    {"text": "Financing", "desc1": "From $89 a month", "desc2": "Approved credit", "url": "/financing"},
                                    {"text": "About Us", "desc1": "Since 2009", "desc2": "Meet Dr. Lopez", "url": "/about"}],
                      "callouts": ["Free Consultation", "Same Day Emergencies", "Most Insurance Accepted", "Financing Available"],
                      "snippets": [{"header": "Services", "values": ["Dental Implants", "Invisalign", "Emergency Care"]}], "call": {"use": True, "note": "Forward to the front desk"}, "promotions": []}
            if camp["kind"] == "pmax":
                return {"assetGroups": [{"name": "Austin Adults", "theme": "Local", "finalUrl": SITE, "headlines": GOOD_HEADLINES[:8], "longHeadlines": ["Dental Implants And Invisalign In Austin With A Free Consultation"],
                                         "descriptions": GOOD_DESCRIPTIONS + ["Book your free consult today."], "businessName": "Acme Dental", "cta": "Book now", "images": [{"ratio": "1.91:1", "concept": "Smiling patient"}],
                                         "videos": [{"concept": "Tour of the practice"}], "signals": {"customSegments": ["dental implants cost"], "interests": ["Health"], "yourData": [], "demographics": "Adults 35 to 65"},
                                         "searchThemes": ["dental implants austin", "family dentist austin"]}], "assets": assets}
            return {"adGroups": [{"name": "Implants Core", "theme": "Dental implants", "intent": "service", "landingPage": "/services/dental-implants",
                                  "keywords": [{"text": "dental implants austin", "match": "exact", "intent": "service", "note": "Planner"}, {"text": "dental implants austin", "match": "phrase", "intent": "service", "note": ""},
                                               {"text": "implant dentist near me", "match": "phrase", "intent": "service", "note": ""}, {"text": "cost of dental implants austin", "match": "phrase", "intent": "price", "note": ""}],
                                  "ads": [{"headlines": GOOD_HEADLINES, "descriptions": GOOD_DESCRIPTIONS, "path1": "implants", "path2": "austin", "pins": []}]}],
                    "negatives": {"campaign": [{"text": "free", "match": "phrase", "reason": "Not a buyer"}, {"text": "dental implants", "match": "phrase", "reason": "Would block the keywords"}, {"text": "jobs", "match": "phrase", "reason": "Careers"}],
                    "lists": [{"name": "Jobs and careers", "keywords": ["jobs", "careers", "hiring"], "reason": "Not buyers"}]}, "assets": assets}
        raise AssertionError("unexpected stage " + stage)
    return call


def ask_search(competitors=None, market=True):
    """A stand-in for claude_websearch.ask that answers the competitor search and the market search."""
    comp = competitors if competitors is not None else [{"name": "Smile Austin", "url": "https://smileaustin.com/", "why": "Cosmetic dentist nearby"},
                                                        {"name": "Yelp", "url": "https://yelp.com/biz/x", "why": "directory"},
                                                        {"name": "Acme itself", "url": "https://www.acmedental.com/", "why": "the client"}]

    def ask(system, user, **kw):
        if "direct competitors" in system or "official website" in system:
            text = json.dumps({"competitors": comp})
        else:
            text = json.dumps({"demand": ["Implant searches peak in January"], "decision_factors": ["Price and financing"], "trends": ["More people finance care"],
                               "ad_policy": [{"rule": "Healthcare ads must not promise results", "applies_to": "All dental ads"}], "cost_signals": ["Implant clicks cost 6 to 12 dollars"],
                               "sources": ["https://example.com/report"]}) if market else "no"
        return {"text": text, "usage": {"input_tokens": 1000, "output_tokens": 200}, "search_count": 2, "result_urls": ["https://example.com/more"]}
    return ask


class FakeSci:
    """tracker/sci_store.py and tracker/sci_pipeline.py in one: runs by user, finishing instantly (or never, to test the wait)."""

    def __init__(self, runs=None, finish=True):
        self.runs = runs if runs is not None else {}
        self.started, self.finish, self.n = [], finish, 100

    def list_runs(self, email, limit=100):
        return [dict(r, id=i) for i, r in self.runs.items() if r["email"] == email]

    def save_run(self, email, name, url=None, logo=None):
        self.n += 1
        self.runs[self.n] = {"email": email, "company_name": name, "company_url": url, "status": "running", "updated_at": "2026-10-16T09:00:00+00:00", "synthesis": None,
                             "created_at": "2026-10-16T09:00:00+00:00"}
        return self.n

    def get_run(self, run_id, email):
        r = self.runs.get(run_id)
        return dict(r, id=run_id) if r and r["email"] == email else None

    def resolve_stale_run(self, run):
        return run

    def get_platform_runs(self, run_id):
        return [{"platform": "instagram", "handle": "acmedental", "status": "ok", "post_count": 12, "last_post_at": "2026-10-10", "profile_url": "https://instagram.com/acmedental"},
                {"platform": "tiktok", "handle": "", "status": "no_presence", "post_count": 0, "last_post_at": None, "profile_url": ""}]

    def get_posts(self, run_id):
        return [{"platform": "instagram", "post_type": "reel", "caption": "Meet Dr. Lopez and see how a smile makeover goes", "post_url": "https://instagram.com/p/1", "metrics": {"likes": 120, "comments": 9}, "posted_at": "2026-10-01"},
                {"platform": "instagram", "post_type": "image", "caption": "Free consultation this month", "post_url": "https://instagram.com/p/2", "metrics": {"likes": 20}, "posted_at": "2026-10-03"}]

    # the pipeline half
    def _sci_run_analysis_job(self, run_id, email, name, url):
        self.started.append(run_id)
        if self.finish:
            self.runs[run_id].update(status="done", synthesis=SYNTHESIS, updated_at="2026-10-16T09:10:00+00:00")


SYNTHESIS = {"platforms": {"instagram": {"summary": ["Short reels of the team", "Before and after smiles"], "messaging_and_strategy": ["Warm, people first", "Offers in captions"], "claims": []}},
             "cross_platform": {"summary": ["Video of people beats stills"], "messaging_and_strategy": ["Lead with the dentist, not the offer"], "claims": []}}

KEYWORD_ROWS = {"keyword_view": [{"campaign": {"name": "P2_Search_Old_20240101"}, "adGroup": {"name": "Implants"},
                                  "metrics": {"clicks": "200", "conversions": 12.0, "costMicros": "900000000", "impressions": "3000"},
                                  "adGroupCriterion": {"qualityInfo": {"qualityScore": 8}, "keyword": {"matchType": "EXACT", "text": "dental implants austin"}}},
                                 {"campaign": {"name": "P2_Search_Old_20240101"}, "adGroup": {"name": "General"},
                                  "metrics": {"clicks": "90", "conversions": 0, "costMicros": "400000000", "impressions": "2500"},
                                  "adGroupCriterion": {"keyword": {"matchType": "BROAD", "text": "dentist"}}}]}


def fake_search(customer_id, gaql):
    """Google Ads rows for the queries a build asks."""
    if "FROM keyword_view" in gaql:
        return KEYWORD_ROWS["keyword_view"]
    if "FROM campaign WHERE campaign.status" in gaql:
        return [{"campaign": {"id": "1", "name": "P2_Search_Old_20240101", "status": "ENABLED", "advertisingChannelType": "SEARCH", "biddingStrategyType": "MAXIMIZE_CONVERSIONS",
                              "startDateTime": "2024-01-01 10:00:00"}, "campaignBudget": {"amountMicros": "50000000"},
                 "metrics": {"impressions": "5500", "clicks": "290", "costMicros": "1300000000", "conversions": 12.0, "conversionsValue": 0}}]
    if "FROM conversion_action" in gaql:
        return [{"conversionAction": {"name": "Form submit", "type": "WEBPAGE", "category": "SUBMIT_LEAD_FORM", "primaryForGoal": True}}]
    if "segments.device" in gaql:
        return [{"segments": {"device": "MOBILE"}, "metrics": {"clicks": "200", "costMicros": "900000000", "conversions": 9.0}, "campaign": {}},
                {"segments": {"device": "DESKTOP"}, "metrics": {"clicks": "90", "costMicros": "400000000", "conversions": 3.0}, "campaign": {}}]
    if "user_location_view" in gaql:
        return [{"segments": {"geoTargetRegion": "geoTargetConstants/21176"}, "metrics": {"clicks": "280", "costMicros": "1250000000", "conversions": 12.0}}]
    if "responsive_search_ad" in gaql:
        return [{"campaign": {"name": "P2_Search_Old_20240101"}, "metrics": {"clicks": "290", "conversions": 12.0},
                 "adGroupAd": {"ad": {"finalUrls": ["https://www.acmedental.com/"], "responsiveSearchAd": {"headlines": [{"text": "Austin Dentist"}, {"text": "Book Today"}],
                                                                                                     "descriptions": [{"text": "Family dentistry in Austin."}]}}}}]
    return []


def fake_geo_names(resources):
    return {r: {"name": "Texas", "canonical": "Texas,United States"} for r in resources}


def fake_sqr_read(store, account, start, end):
    return {"terms": [{"t": "dental implants austin tx", "s": "", "c": "P2_Search_Old_20240101", "ct": "SEARCH", "g": "Implants", "i": 900, "k": 60, "x": 270.0, "v": 5.0},
                      {"t": "dentist jobs austin", "s": "", "c": "P2_Search_Old_20240101", "ct": "SEARCH", "g": "General", "i": 400, "k": 30, "x": 120.0, "v": 0.0}],
            "keywords": [{}] * 14, "negatives": [{}] * 3, "lists": [{}], "warnings": []}


def fake_post(path, body):
    if path.endswith("geoTargetConstants:suggest"):
        return {"geoTargetConstantSuggestions": [{"geoTargetConstant": {"id": "1026201", "name": "Austin", "canonicalName": "Austin,Texas,United States", "targetType": "City"}}]}
    months = [{"month": m, "year": "2026", "monthlySearches": str(n)} for m, n in
              zip(["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"], [900, 800, 700, 600, 600, 600, 600, 700, 700, 800, 800, 700])]
    mk = lambda t, a, lo, hi: {"text": t, "keywordIdeaMetrics": {"avgMonthlySearches": str(a), "competition": "MEDIUM", "competitionIndex": "50", "lowTopOfPageBidMicros": str(lo * 1000000),
                                                                  "highTopOfPageBidMicros": str(hi * 1000000), "monthlySearchVolumes": months}}
    return {"results": [mk("dental implants austin", 1900, 5, 12), mk("implant dentist near me", 1300, 4, 11), mk("cost of dental implants", 8100, 3, 9)]}


def fake_season(accts, months):
    return {"empty": False, "coverage": {"yearly": True, "monthsInARow": 24, "note": None},
            "index": {"conv": [{"index": 100 + i} for i in range(12)], "cost": [{"index": 100 - i} for i in range(12)]},
            "headlines": ["January is the strongest month"], "upcoming": [], "events": [], "start": "2024-10-01", "end": "2026-10-15"}


def deps(sci=None, ask=None, call=None, fetch=None, account=True, **extra):
    sci = sci or FakeSci(runs={})
    d = {"account": {"id": "1234567890", "name": "Acme Dental", "currency": "USD"} if account else None, "email": "ana@position2.com", "planner_account": "1234567890",
         "model_ready": True, "fetcher": fetch or fetcher(), "ask": ask or ask_search(), "call": call or model(), "search": fake_search, "geo_names": fake_geo_names,
         "sqr_read": fake_sqr_read, "post": fake_post, "season_report": fake_season, "sci_store": sci, "sci_pipeline": sci, "spawn": lambda fn: fn(),
         "social_wait_s": 0, "social_poll_s": 0, "sleep": lambda s: None, "cached": None, "gads_store": None,
         "now": __import__("datetime").datetime(2026, 10, 16, tzinfo=__import__("datetime").timezone.utc), "today": __import__("datetime").date(2026, 10, 16)}
    d.update(extra)
    return d


def clean(**kw):
    """The normalised inputs, as of the day the tests are written for (the launch date has to be ahead of today)."""
    import datetime
    from tracker import paid_strategy as ps
    return ps.clean_inputs(inputs(**kw), today=datetime.date(2026, 10, 16))


def inputs(**kw):
    base = {"name": "Acme Dental", "website": "acmedental.com", "account": "1234567890", "industry": "Dental", "goal": "leads", "budget": "6000", "locations": "Austin, TX",
            "services": "dental implants, invisalign", "launch": "2026-11-02", "campaignTypes": [], "competitors": []}
    base.update(kw)
    return base
