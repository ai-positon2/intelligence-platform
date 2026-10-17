"""Stand-ins for the tests of the Business Research agent. It shares the website, the store, Google Ads and Social Media Intelligence fakes of the
Strategy & Campaign Book agent (tests/strategy_fakes.py) and adds a reputation search and a model that answers its three stages."""
import copy
import datetime
import json
import threading

from tests import strategy_fakes as F

SITE = F.SITE

REPUTATION = {
    "ratings": [{"platform": "Google", "rating": "4.8", "count": "1,204", "url": "https://www.google.com/maps/place/acme"},
                {"platform": "Yelp", "rating": "4.5", "count": "212", "url": "https://yelp.com/biz/acme-dental-austin"}],
    "praise": ["Kind, patient staff (very common)", "Easy online booking (common)"], "complaints": ["Parking is hard to find (a few)"],
    "customer_questions": ["Does Acme Dental accept Delta Dental?", "How long do implants take?"],
    "press": [{"headline": "Acme Dental opens a second office", "source": "Austin Business Journal", "when": "March 2025"}],
    "awards": ["Austin Chronicle Best Dentist 2024"], "people": [{"name": "Dr. Maria Lopez", "role": "Founder"}],
    "company": {"founded": "2009", "size": "Two offices, about 30 staff", "ownership": "Owner operated", "locations": "Austin, TX"},
    "sources": ["https://yelp.com/biz/acme-dental-austin"]}


def ask_search(reputation=REPUTATION, **kw):
    """claude_websearch.ask for the reputation search, the competitor search and the market search."""
    base = F.ask_search(**kw)
    seen = []

    def ask(system, user, **k):
        seen.append(system)
        if "reputation" in system and "ratings" in system:
            if reputation is None:
                return {"text": "no", "usage": {"input_tokens": 1000, "output_tokens": 200}, "search_count": 1}
            return {"text": json.dumps(reputation), "usage": {"input_tokens": 1000, "output_tokens": 200}, "search_count": 3, "error": None,
                    "result_urls": ["https://example.com/reviews", "https://yelp.com/biz/acme-dental-austin"]}
        return base(system, user, **k)
    ask.seen = seen
    return ask


CORE = {
    "summary": "Acme Dental is a family dental practice in Austin that sells implants, Invisalign and everyday care to local families.",
    "snapshot": [{"item": "Business model", "detail": "Private practice, patients pay or use insurance"}, {"item": "Where it operates", "detail": "Austin, Texas"}],
    "offerings": [{"name": "Dental implants", "description": "Single and full-arch implants", "audience": "Adults missing teeth", "pricing": "From $1,900 per tooth",
                   "page": "/services/dental-implants", "priority": "high", "why": "Clearest page and the highest value"},
                  {"name": "Invisalign", "description": "Clear aligners", "audience": "Adults and teens", "pricing": "not shown", "page": "https://elsewhere.example/invisalign",
                   "priority": "urgent", "why": "Good demand"}],
    "usps": [{"usp": "Free implant consultation", "why_it_matters": "Removes the first barrier", "evidence": "Free consultation for dental implants", "source": SITE, "strength": "proven"},
             {"usp": "Voted best in Texas", "why_it_matters": "Trust", "evidence": "Voted best dentist in Texas three years running", "source": SITE, "strength": "proven"},
             {"usp": "Friendly team", "why_it_matters": "Comfort", "evidence": "", "source": "", "strength": "generic"}],
    "proof": [{"point": "4.9 rating from 312 reviews", "source": SITE}],
    "positioning": {"promise": "Your Austin family dentist", "tone": "Warm and plain", "wordsUsed": ["family dentist", "implants"], "wordsToAvoid": ["painless"]},
    "compliance": [{"topic": "Healthcare advertising", "rule": "No guaranteed results", "impact": "Avoid cure or painless claims"}],
    "facts": [{"fact": "Free consultation for dental implants", "quote": "Free consultation for dental implants", "source": SITE},
              {"fact": "A fact not on any page", "quote": "Ranked number one in all of Texas", "source": SITE}],
    "siteGaps": [{"issue": "No phone on the implants page", "why": "Urgent buyers want to call", "fix": "Add the number to the header"}], "assumptions": ["One location"]}

AUDIENCE = {
    "audiences": [{"name": "Missing teeth", "who": "Adults 45 plus", "situation": "Lost a tooth", "goal": "Replace it for good", "worries": "Cost and pain", "searches": "dental implants austin", "priority": "high"},
                  {"name": "Families", "who": "Parents", "situation": "Need a regular dentist", "goal": "One practice for everyone", "worries": "Insurance", "searches": "family dentist near me", "priority": "sometimes"}],
    "journey": [{"stage": "Compare", "what_happens": "Reads reviews", "friction": "No prices", "opportunity": "Show financing"}],
    "triggers": ["A broken tooth", "A wedding"], "objections": [{"objection": "Implants cost too much", "answer": "Financing from $89 a month", "proof": "Financing page"}],
    "voice": {"style": "Warm and plain", "vocabulary": ["smile", "family"], "avoid": ["cure"]}, "geography": {"summary": "Mostly Austin and Round Rock", "areas": [{"area": "Round Rock", "note": "North of Austin"}]},
    "seasonality": [{"window": "January", "why": "New insurance year"}]}

IMPLICATIONS = {
    "summary": ["Acme is an Austin family dentist", "Free consultation sets it apart", "Reach adults missing teeth", "Advertise implants first", "Hold back cosmetic work",
                "Add call tracking", "Biggest risk is thin pages", "First move: fix the implants page"],
    "advertiseFirst": [{"what": "Dental implants", "why": "Highest value and the clearest page", "page": "/services/dental-implants", "evidence": "Page quotes $1,900"}],
    "holdBack": [{"what": "Veneers", "why": "No page on the site"}],
    "angles": [{"angle": "Free consultation", "audience": "Missing teeth", "proof": "The implants page", "example": "Free Implant Consultation in Austin"}],
    "tests": [{"hypothesis": "A price in the ad lifts clicks", "how": "Two ads, one with the price", "metric": "Click through rate"}],
    "competitive": {"summary": "Smile Austin leads with cosmetic work.", "edge": ["Implant financing"], "exposed": ["No online booking"]},
    "reputation": {"summary": "Patients rate it highly.", "strengths": ["Kind staff"], "risks": ["Parking"], "actions": ["Show parking details on the contact page"]},
    "social": [{"insight": "Short reels of the team", "use": "Use the team in video ads"}],
    "tracking": [{"item": "Analytics", "seen": "Google Analytics 4 seen on 3 pages", "action": "Confirm the property and who has access"}],
    "questions": [{"area": "Goals and targets", "question": "How many new implant patients a month do you want?", "why": "Sets the budget", "urgency": "Before launch"},
                  {"area": "Offers and pricing", "question": "Is the free consultation still on?", "why": "It is the lead offer", "urgency": "whenever"},
                  {"area": "Goals and targets", "question": "What is a patient worth?", "why": "Sets the cost per lead", "urgency": "First month"}],
    "risks": [{"risk": "Thin pages", "mitigation": "Write the missing pages"}], "nextSteps": [{"step": "Fix the implants page", "owner": "client", "when": "Week 1"}]}


def model(overrides=None, calls=None):
    """A model that answers the three stages with valid replies. `overrides` maps a stage to a dict (or a function of the user text)."""
    overrides = overrides or {}
    lock = threading.Lock()

    def call(system, user, schema, *, max_tokens, stage):
        with lock:
            if calls is not None:
                calls.append((stage, user))
        ov = overrides.get(stage)
        if callable(ov):
            return copy.deepcopy(ov(user))
        if ov is not None:
            return copy.deepcopy(ov)
        return copy.deepcopy({"core": CORE, "audience": AUDIENCE, "implications": IMPLICATIONS}[stage])
    return call


def deps(**kw):
    kw.setdefault("ask", ask_search())
    kw.setdefault("call", model())
    return F.deps(**kw)


def inputs(**kw):
    base = {"name": "Acme Dental", "website": "acmedental.com", "account": "1234567890", "industry": "Dental", "goal": "leads", "locations": "Austin, TX",
            "services": "dental implants, invisalign", "competitors": []}
    base.update(kw)
    return base


def clean(**kw):
    from tracker import paid_business as pb
    return pb.clean_inputs(inputs(**kw))
