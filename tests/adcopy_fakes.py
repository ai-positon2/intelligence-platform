"""Stand-ins for the tests of the Ad Copy & Creative Generator. It shares the website, the store and the platform fakes of the Strategy & Campaign Book agent
(tests/strategy_fakes.py) and adds a model that answers each ad format with plausible, valid copy, and an Ad Strength report."""
import json
import threading

from tests import strategy_fakes as F

HEADS = ["Dental Implants Austin", "Invisalign Cost Guide", "Rated 4.9 Stars", "Book Your Visit", "Gentle Implant Care", "Local Austin Dentist", "Clear Pricing Upfront",
         "Meet The Team", "Family Friendly Office", "Modern Dental Technology", "Easy Online Booking", "Dental Care Near You", "Smile With Confidence", "Plan Your Implants",
         "Austin Implant Dentist", "Free Consultation"]
DESCS = ["Plan your dental implants with a team that explains every step. Book a visit today.", "See what invisalign costs and how treatment works. Ask us anything.",
         "Rated 4.9 stars from 312 reviews. Gentle care for the whole family in Austin.", "Questions about implants? We answer them in plain words. Call or book online.",
         "Free consultation for dental implants. Book a visit and meet the team."]
LONG = ["Dental implants in Austin with a team that explains every step before you start", "Invisalign clear aligners planned around your life, with a free consultation"]
BRIEF = {"concept": "A day with the team at Acme Dental", "hook": "Meet the people who will plan your implants", "visual": "Warm office light, real staff.",
         "script": [{"beat": "The team at the door", "onScreen": "Meet Acme Dental", "voiceover": "Meet the team."}, {"beat": "A consultation", "onScreen": "Book a visit", "voiceover": "Book a visit."}],
         "assets": ["Photo of the team", "Short clip of the reception"], "dos": ["Show real staff"], "donts": ["No stock photos"]}


def rows(texts, angle="benefit"):
    return [{"text": t, "angle": angle} for t in texts]


def answer(fmt, *, n=None):
    """A valid answer for the format; `n` cuts every field to that many lines."""
    cut = (lambda xs: xs[:n]) if n is not None else (lambda xs: xs)
    if fmt == "google_rsa":
        return {"headlines": rows(cut(HEADS[:15])), "descriptions": rows(cut(DESCS[:4]), "proof"), "paths": {"path1": "Dental Implants", "path2": "Austin"}}
    if fmt == "google_pmax":
        return {"headlines": rows(cut(HEADS[:5])), "long_headlines": rows(cut(LONG)), "descriptions": rows(cut(DESCS[:3])),
                "short_description": rows(cut(["Implants planned in plain words. Book in Austin."])), "cta": "Book now"}
    if fmt == "google_demand_gen":
        return {"headlines": rows(cut(HEADS[:4])), "descriptions": rows(cut(DESCS[:3])), "cta": "Learn more", "brief": BRIEF}
    if fmt == "google_display":
        return {"headlines": rows(cut(HEADS[:3])), "long_headlines": rows(cut(LONG[:1])), "descriptions": rows(cut(DESCS[:3])), "cta": "Learn more", "brief": BRIEF}
    if fmt == "meta_feed":
        return {"primary_texts": rows(cut(["Dental implants, explained simply. Meet the team in Austin.", "Book a visit and see how your plan would work."])),
                "headlines": rows(cut(["Meet Acme Dental", "Book Your Visit", "Implants Planned Simply"])), "descriptions": rows(cut(["Austin implant dentist"])), "cta": "Book now", "brief": BRIEF}
    if fmt == "meta_stories_reels":
        return {"primary_texts": rows(cut(["Meet the team behind your smile.", "Implants, explained in plain words."])), "headlines": rows(cut(["Meet Acme Dental", "Book Your Visit"])), "cta": "Book now", "brief": BRIEF}
    raise KeyError(fmt)


class Model:
    """The model. `override[fmt]` replaces the answer for a format: a dict, a list of dicts taken in turn, or an exception to raise. Every call is kept in `calls`."""

    def __init__(self, override=None):
        self.override, self.calls, self.lock = dict(override or {}), [], threading.Lock()

    def __call__(self, system, user, schema, *, max_tokens, stage):
        fmt = stage.split(":", 1)[1]
        with self.lock:
            self.calls.append({"fmt": fmt, "user": json.loads(user), "stage": stage})
            got = self.override.get(fmt)
            if isinstance(got, list):
                got = got.pop(0) if len(got) > 1 else got[0]
        if isinstance(got, Exception):
            raise got
        return got if got is not None else answer(fmt)

    def formats(self):
        return [c["fmt"] for c in self.calls]


def ads_report(*_a, **_k):
    return {"search": {"items": [{"campaign": "P2_Search_Old", "adGroup": "Implants", "clicks": 60, "impressions": 900, "strengthLabel": "Good",
                                  "headlines": [{"text": "Dental Implants Austin"}, {"text": "Free Consultation"}], "descriptions": [{"text": "Book a free consultation for dental implants."}]}]},
            "warnings": []}


def deps(model=None, **kw):
    kw.setdefault("ads_report", ads_report)
    return F.deps(call=model or Model(), **kw)


def inputs(**kw):
    base = {"name": "Acme Dental", "website": "acmedental.com", "account": "1234567890", "industry": "Dental", "goal": "leads", "locations": "Austin, TX",
            "product": "dental implants and invisalign", "keywords": "dental implants austin, invisalign cost", "facts": "Rated 4.9 stars from 312 reviews.",
            "formats": ["google_rsa", "meta_feed"], "angles": ["benefit", "proof", "action"], "competitors": "Smile Austin", "avoid": "cheap"}
    base.update(kw)
    return base


def clean(**kw):
    from tracker import paid_adcopy as pa
    return pa.clean_inputs(inputs(**kw))
