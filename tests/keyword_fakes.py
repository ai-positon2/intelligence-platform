"""Stand-ins for the tests of the Keyword Research agent. It shares the website, the store and the model-call fakes of the Strategy & Campaign Book agent
(tests/strategy_fakes.py) and adds Keyword Planner with a dental market in it, an account with keywords, negatives and search terms, and a model that answers
the three stages by plain rules (so a test can say what each keyword is judged to be)."""
import copy
import json
import re
import threading

from tests import strategy_fakes as F

MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]
STEADY = [100] * 12
SPRING = [60, 60, 80, 120, 200, 120, 80, 60, 60, 60, 60, 60]          # peaks in May
RISING = [50, 50, 55, 60, 70, 80, 90, 100, 110, 120, 130, 140]
FALLING = [140, 130, 120, 110, 100, 90, 80, 70, 60, 55, 50, 50]

# keyword: (searches a month, competition, low bid, high bid, shape of the year)
MARKET = {
    "dental implants austin": (1900, "MEDIUM", 5, 12, STEADY),
    "dental implants": (22000, "HIGH", 6, 18, STEADY),
    "dental implant": (22000, "HIGH", 6, 18, STEADY),                        # the same keyword in the other number: merged with the one above
    "cost of dental implants": (8100, "MEDIUM", 3, 9, STEADY),
    "dental implants cost austin": (720, "MEDIUM", 4, 10, STEADY),
    "implant dentist near me": (1300, "MEDIUM", 4, 11, STEADY),
    "all on 4 dental implants austin": (390, "LOW", 5, 14, RISING),
    "same day dental implants": (590, "MEDIUM", 5, 13, STEADY),
    "invisalign austin": (1600, "HIGH", 4, 10, SPRING),
    "invisalign cost": (6600, "MEDIUM", 3, 8, SPRING),
    "invisalign for teens": (480, "LOW", 2, 6, SPRING),
    "emergency dentist austin": (2400, "MEDIUM", 6, 15, STEADY),
    "emergency dentist near me": (9900, "HIGH", 7, 20, STEADY),
    "family dentist austin": (1000, "MEDIUM", 3, 8, STEADY),
    "teeth whitening austin": (210, "LOW", 2, 5, FALLING),
    "acme dental austin": (320, "LOW", 1, 3, STEADY),                          # the client's own name
    "acme dental": (260, "LOW", 1, 3, STEADY),
    "smile austin dentistry": (170, "LOW", 1, 4, STEADY),                       # a competitor named in the form
    "smile austin veneers": (50, "LOW", 2, 5, STEADY),
    "free dental implants": (2900, "LOW", 1, 3, STEADY),
    "dental assistant jobs austin": (1300, "LOW", 1, 2, STEADY),
    "dentist dallas": (5400, "HIGH", 5, 12, STEADY),
    "how long do dental implants last": (3600, "LOW", 1, 3, STEADY),
    "dental implants for dogs": (30, "LOW", 1, 2, STEADY),
    "dentist for kids": (12, "LOW", 1, 3, STEADY),
    "tooth gem": (5, "LOW", 1, 2, STEADY),                                      # under any floor
}


def months(shape, base):
    return [{"month": m, "year": "2026" if i < 12 else "2025", "monthlySearches": str(round(base * v / 100))} for i, (m, v) in enumerate(zip(MONTHS, shape))]


def mk(text, key="keywordIdeaMetrics"):
    a, comp, lo, hi, shape = MARKET[text]
    return {"text": text, key: {"avgMonthlySearches": str(a), "competition": comp, "competitionIndex": "40", "lowTopOfPageBidMicros": str(lo * 1000000),
                                "highTopOfPageBidMicros": str(hi * 1000000), "monthlySearchVolumes": months(shape, a)}}


def planner(calls=None, empty=(), fail=(), extra=None, results=None):
    """Google Ads' API call: place lookup, keyword ideas (every request answers the whole market unless it is in `empty` or `fail`) and historical metrics.
    `calls` collects (path, body)."""
    lock = threading.Lock()

    def post(path, body):
        with lock:
            if calls is not None:
                calls.append((path, copy.deepcopy(body)))
        if path.endswith("geoTargetConstants:suggest"):
            return {"geoTargetConstantSuggestions": [{"geoTargetConstant": {"id": "1026201", "name": "Austin", "canonicalName": "Austin,Texas,United States", "targetType": "City"}}]}
        if path.endswith(":generateKeywordIdeas"):
            label = ",".join((body.get("keywordSeed") or {}).get("keywords") or []) or (body.get("urlSeed") or {}).get("url", "")
            if any(f in label for f in fail):
                raise RuntimeError("RESOURCE_EXHAUSTED: too many requests")
            if any(e in label for e in empty):
                return {}
            keys = results if results is not None else list(MARKET)
            return {"results": [mk(k) for k in keys] + list(extra or [])}
        if path.endswith(":generateKeywordHistoricalMetrics"):
            out = []
            for t in body.get("keywords") or []:
                if t in MARKET:
                    out.append(mk(t, "keywordMetrics"))
                else:
                    out.append({"text": t, "keywordMetrics": {"avgMonthlySearches": "70", "competition": "LOW", "monthlySearchVolumes": months(STEADY, 70)}})
            return {"results": out}
        raise AssertionError("unexpected path " + path)
    return post


ACCOUNT_KEYWORDS = [
    {"t": "dental implants austin", "m": "EXACT", "c": "P2_Search_Implants_20250101", "g": "Implants Exact"},
    {"t": "dental implants", "m": "PHRASE", "c": "P2_Search_Implants_20250101", "g": "Implants Phrase"},
    {"t": "invisalign", "m": "BROAD", "c": "P2_Search_Invisalign_20250101", "g": "Invisalign"},
    {"t": "emergency dentist austin", "m": "EXACT", "c": "P2_Search_Emergency_20250101", "g": "Emergency"},
    {"t": "acme dental", "m": "EXACT", "c": "P2_Search_Brand_20250101", "g": "Brand"},
]
ACCOUNT_NEGATIVES = [{"t": "jobs", "m": "PHRASE", "c": ["P2_Search_Implants_20250101", "P2_Search_Invisalign_20250101", "P2_Search_Emergency_20250101", "P2_Search_Brand_20250101"]},
                     {"t": "dallas", "m": "PHRASE", "c": ["P2_Search_Implants_20250101"]}]
ACCOUNT_LISTS = [{"n": "Shared negatives", "c": ["P2_Search_Implants_20250101", "P2_Search_Invisalign_20250101", "P2_Search_Emergency_20250101", "P2_Search_Brand_20250101"],
                  "k": [["dogs", "PHRASE"], ["free", "PHRASE"]]}]
TERMS = [
    {"t": "all on 4 implants austin tx", "s": "", "c": "P2_Search_Implants_20250101", "ct": "SEARCH", "g": "Implants Phrase", "i": 600, "k": 44, "x": 380.0, "v": 4.0},
    {"t": "implant dentist near me", "s": "", "c": "P2_Search_Implants_20250101", "ct": "SEARCH", "g": "Implants Phrase", "i": 800, "k": 55, "x": 300.0, "v": 3.0},
    {"t": "dental implants austin", "s": "ADDED", "c": "P2_Search_Implants_20250101", "ct": "SEARCH", "g": "Implants Exact", "i": 900, "k": 90, "x": 540.0, "v": 9.0},
    {"t": "dentist jobs austin", "s": "", "c": "P2_Search_Implants_20250101", "ct": "SEARCH", "g": "Implants Phrase", "i": 400, "k": 30, "x": 120.0, "v": 0.0},
    {"t": "cheap tooth pulling", "s": "", "c": "P2_Search_Emergency_20250101", "ct": "SEARCH", "g": "Emergency", "i": 100, "k": 10, "x": 90.0, "v": 1.0},
]


def sqr_read(keywords=None, negatives=None, lists=None, terms=None, warnings=None, fail=None, notes=None, **flags):
    def read(store, account, start, end):
        if fail:
            raise RuntimeError(fail)
        return {"terms": copy.deepcopy(TERMS if terms is None else terms), "truncated": False, "keywords": copy.deepcopy(ACCOUNT_KEYWORDS if keywords is None else keywords),
                "negatives": copy.deepcopy(ACCOUNT_NEGATIVES if negatives is None else negatives), "lists": copy.deepcopy(ACCOUNT_LISTS if lists is None else lists),
                "warnings": list(warnings or []), "notes": list(notes or []), "keywordsFailed": False, "negativesFailed": False, "keywordsCut": False, "negativesCut": False, **flags}
    return read


THEMES = {"themes": [
    {"key": "implants", "name": "Dental Implants", "intent": "transactional", "why": "People ready to replace missing teeth, the client's highest value service.", "existingCampaign": "P2_Search_Implants_20250101", "priority": "high"},
    {"key": "invisalign", "name": "Invisalign", "intent": "commercial", "why": "Adults and parents comparing clear aligners.", "existingCampaign": "p2_search_invisalign_20250101", "priority": "medium"},
    {"key": "emergency", "name": "Emergency Dentist", "intent": "local", "why": "Urgent, close by, high intent.", "existingCampaign": "", "priority": "high"},
    {"key": "family", "name": "Family Dentist", "intent": "local", "why": "Everyday care for local families.", "existingCampaign": "A campaign the account does not have", "priority": "medium"},
    {"key": "brand", "name": "Brand", "intent": "brand", "why": "Searches for the practice by name.", "existingCampaign": "", "priority": "low"}],
    "assumptions": ["Implants earn the most per patient"]}

PLAN = {"summary": ["The market is mostly implants and Invisalign", "The account already covers the biggest implant searches", "Emergency and family searches are open",
                    "Invisalign peaks in spring"],
        "quickWins": [{"what": "Add the converting search term all on 4 implants austin tx as exact", "why": "It converted four times"}],
        "negatives": [{"kw": "free", "match": "phrase", "why": "Not a buyer"}, {"kw": "dental assistant", "match": "phrase", "why": "A job seeker"}, {"kw": "dental implants", "match": "phrase", "why": "A mistake: it would block the main keyword"},
                      {"kw": "jobs", "match": "phrase", "why": "The account has it already"}, {"kw": "how long", "match": "phrase", "why": "Information only"}, {"kw": "dogs", "match": "phrase", "why": "Already in the shared list"},
                      {"kw": "gem", "match": "exact", "why": "Not dental"}],
        "tests": [{"hypothesis": "Exact match on implants lowers cost per lead", "how": "Split the ad group", "metric": "Cost per lead"}],
        "questions": [{"area": "Brand terms and competitors", "question": "May we bid on competitor names?", "why": "It is the client's decision", "urgency": "Before launch."},
                      {"area": "", "question": "Which places do you serve?", "why": "Sets the geography", "urgency": "whenever"}],
        "risks": [{"risk": "Implant terms are broad", "mitigation": "Start with exact match"}],
        "seasonal": [{"window": "April to June", "action": "Raise the Invisalign budget before May"}]}

RULES = [("jobs", "no", "jobs"), ("free ", "no", "other"), ("dallas", "no", "other"), ("dogs", "no", "other"), ("how long", "no", "informational"), ("gem", "no", "other")]


def judge(kw):
    """What the stand-in model says of one keyword (fit, intent, theme, match, priority, note)."""
    for word, fit, intent in RULES:
        if word in kw + " ":
            return {"fit": fit, "intent": intent, "theme": "other", "match": "phrase", "priority": "low", "note": "Not a customer"}
    if "acme" in kw:
        return {"fit": "yes", "intent": "brand", "theme": "brand", "match": "exact", "priority": "low", "note": "The practice's own name"}
    if "smile austin" in kw:
        return {"fit": "maybe", "intent": "competitor", "theme": "other", "match": "exact", "priority": "low", "note": "A rival's name"}
    if "implant" in kw:
        return {"fit": "yes", "intent": "transactional" if "cost" not in kw else "commercial", "theme": "implants", "match": "exact" if "austin" in kw else "phrase", "priority": "high", "note": ""}
    if "invisalign" in kw:
        return {"fit": "yes", "intent": "commercial", "theme": "Invisalign", "match": "phrase", "priority": "medium", "note": ""}
    if "emergency" in kw:
        return {"fit": "yes", "intent": "local", "theme": "emergency", "match": "phrase", "priority": "high", "note": ""}
    if "family" in kw or "kids" in kw:
        return {"fit": "maybe", "intent": "local", "theme": "family", "match": "phrase", "priority": "medium", "note": "Check the practice sees children"}
    return {"fit": "maybe", "intent": "other", "theme": "nothing like this", "match": "broad", "priority": "low", "note": "Unclear"}


def model(overrides=None, calls=None, judging=None):
    """A model that answers the three stages. `overrides` maps a stage to a dict (or a function of the user text); `judging` replaces judge()."""
    overrides = overrides or {}
    lock = threading.Lock()
    judging = judging or judge

    def call(system, user, schema, *, max_tokens, stage):
        with lock:
            if calls is not None:
                calls.append((stage, user))
        ov = overrides.get(stage)
        if callable(ov):
            return copy.deepcopy(ov(user))
        if ov is not None:
            return copy.deepcopy(ov)
        if stage == "themes":
            return copy.deepcopy(THEMES)
        if stage == "plan":
            return copy.deepcopy(PLAN)
        if stage == "classify":
            rows = []
            for item in json.loads(user)["keywords"]:
                kw = item["kw"]
                rows.append(dict(judging(kw), kw=kw))
            return {"rows": rows}
        raise AssertionError("unexpected stage " + stage)
    return call


BUSINESS_PLAN = {"generatedAt": "2026-10-10T09:00:00+00:00", "core": {"summary": "A family dental practice in Austin.", "positioning": {"wordsUsed": ["family dentist"], "wordsToAvoid": ["painless"]},
                                                                      "offerings": [{"name": "Dental implants", "description": "Single and full arch", "priority": "high", "page": "/services/dental-implants"},
                                                                                    {"name": "Teeth whitening", "description": "In chair", "priority": "low", "page": ""}],
                                                                      "compliance": [{"topic": "Healthcare advertising", "rule": "No guaranteed results", "impact": "x"}]},
                 "audience": {"audiences": [{"name": "Missing teeth", "searches": "dental implants austin, all on 4"}], "voice": {"vocabulary": ["smile"]}, "seasonality": [{"window": "January", "why": "New insurance year"}]}}


class Store:
    """The two calls the research makes on the saved builds: the list (newest first) and one whole build."""

    def __init__(self, rows=None):
        self.rows = rows if rows is not None else [{"id": "b" * 32, "name": "Acme Dental", "status": "done", "inputs": {"kind": "business", "website": "https://www.acmedental.com/"}, "stale": False}]
        self.asked = []

    def list_recent(self, kind=None):
        self.asked.append(kind)
        return copy.deepcopy(self.rows)

    def get(self, sid):
        r = next((r for r in self.rows if r["id"] == sid), None)
        return dict(copy.deepcopy(r), plan=copy.deepcopy(BUSINESS_PLAN)) if r else None


def deps(store=None, post=None, call=None, **kw):
    store = store or Store()
    kw.setdefault("sqr_read", sqr_read())
    kw.setdefault("business_list", store.list_recent)
    kw.setdefault("business_get", store.get)
    kw.setdefault("planner_currency", "USD")
    return F.deps(post=post or planner(), call=call or model(), **kw)


def inputs(**kw):
    base = {"name": "Acme Dental", "website": "acmedental.com", "account": "1234567890", "industry": "Dental", "goal": "leads", "locations": "Austin, TX",
            "services": "dental implants, invisalign", "competitors": ["smileaustin.com"], "minSearches": 10}
    base.update(kw)
    return base


def clean(**kw):
    from tracker import paid_keywords as pk
    return pk.clean_inputs(inputs(**kw))
