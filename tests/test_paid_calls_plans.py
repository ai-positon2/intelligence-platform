"""Call Transcript Analyzer: what to do about the calls that did not become appointments (tracker/paid_calls_plans.py).

The accounts are HIPAA accounts, so the plans are built from counts alone and carry nothing a caller said. The figures are the risk: a share of a sample scaled up to the range
is an estimate and must say so, must not be made from too few calls, and must put the appointments it implies at the rate the practice's own callers booked at, not a rate invented
here. They are pinned to numbers worked out by hand.
"""
import json

import pytest

from tests.test_paid_calls import ACC, CallRail, END, FakeStore, START, SUMMARY, env, raw_call  # noqa: F401  (env: the autouse fixture that sets the key)
from tracker import paid_calls as C
from tracker import paid_calls_plans as pl
from tracker import paid_calls_topics as T

EM, EN = chr(0x2014), chr(0x2013)


def analysis(read=60, matching=1200, book=0.5, reasons=(("no_availability", 6),), intents=()):
    return {"read": read, "matching": matching, "bookRate": book, "reasons": [{"key": k, "label": T.REASON_LABEL[k], "n": n} for k, n in reasons],
            "intents": [{"key": k, "n": n} for k, n in intents]}


# ── Reasons ───────────────────────────────────────────────────────────────

def test_a_reason_is_scaled_to_the_range_and_priced_at_the_rate_callers_booked_at():
    out = pl.plans(analysis())
    p = out["reasons"]["no_availability"]
    # 6 of 60 read is a tenth; of the 1,200 calls in the range that is 120; at the 50% the callers who wanted care booked at, 60 appointments
    assert p["worth"] == ("In the sample, 6 of 60 calls read ended because no appointment time was free. Scaled up to the 1,200 calls in this range that is about 120. "
                          "If those callers booked at the rate other callers looking for care did (50%), that is about 60 more appointments. It is an estimate: the sample is not every call "
                          "and the reason is the likeliest one in the call, not a certain one.")
    assert "Ad schedule" in " ".join(p["steps"]) and "new-patient slots" in p["steps"][0]


def test_a_sample_too_small_to_scale_still_gets_its_steps_and_no_figure():
    p = pl.plans(analysis(read=29))["reasons"]["no_availability"]
    assert p["worth"] is None and len(p["steps"]) == 3
    assert pl.plans(analysis(read=30))["reasons"]["no_availability"]["worth"]                    # thirty is enough
    assert pl.plans(analysis(matching=0))["reasons"]["no_availability"]["worth"] is None


def test_without_a_book_rate_it_scales_the_calls_and_does_not_invent_appointments():
    w = pl.plans(analysis(book=None))["reasons"]["no_availability"]["worth"]
    assert "about 120." in w and "appointments" not in w.split("It is an estimate")[0]
    assert "appointments" not in pl.plans(analysis(book=0))["reasons"]["no_availability"]["worth"].split("It is an estimate")[0]


def test_a_reason_worth_less_than_a_call_is_not_given_a_figure():
    assert pl.plans(analysis(read=1000, matching=100, reasons=(("price", 5),)))["reasons"]["price"]["worth"] is None        # 5 of 1000 of 100 calls is half a call


def test_every_reason_the_analyzer_can_find_has_its_own_steps_and_wording():
    keys = [k for k, _l, _p in T.REASONS] + ["other"]
    out = pl.plans(analysis(reasons=tuple((k, 6) for k in keys)))
    assert set(out["reasons"]) == set(keys) and set(pl.WHAT) == set(keys)
    assert len({tuple(p["steps"]) for p in out["reasons"].values()}) == len(keys)                 # no two reasons share a plan
    assert "Policy" not in json.dumps(out)
    assert "insurance was not accepted" in out["reasons"]["insurance"]["worth"] and "negative keywords" in " ".join(out["reasons"]["insurance"]["steps"]) and "client has confirmed" in " ".join(out["reasons"]["insurance"]["steps"])
    assert "Call flow" in " ".join(out["reasons"]["transfer"]["steps"]) and "same business day" in " ".join(out["reasons"]["callback"]["steps"])
    assert "Financing available" in " ".join(out["reasons"]["price"]["steps"]) and "Tag each one in CallRail" in " ".join(out["reasons"]["other"]["steps"])


def test_a_reason_the_plans_do_not_know_is_left_out_and_no_reasons_is_empty():
    a = analysis()
    a["reasons"].append({"key": "something_new", "label": "x", "n": 9})
    assert set(pl.plans(a)["reasons"]) == {"no_availability"}
    assert pl.plans(analysis(reasons=())) == {"reasons": {}, "notAPatient": None}


# ── Vendors and wrong numbers ─────────────────────────────────────────────

def test_vendors_and_job_seekers_get_a_plan_from_a_tenth_of_the_calls_read():
    p = pl.plans(analysis(reasons=(), intents=(("not_a_patient", 6),)))["notAPatient"]
    assert p["worth"] == "In the sample, 6 of 60 calls read were vendors, wrong numbers or job seekers. Scaled up to the 1,200 calls in this range that is about 120 calls that were never patients: an estimate."
    assert "\"jobs\"" in " ".join(p["steps"]) and "mark the spam" in " ".join(p["steps"])
    assert pl.plans(analysis(reasons=(), intents=(("not_a_patient", 5),)))["notAPatient"] is None                 # 5 of 60 is under a tenth
    assert pl.plans(analysis(read=20, reasons=(), intents=(("not_a_patient", 10),)))["notAPatient"] is None          # too few read to say
    assert pl.plans(analysis(reasons=(), intents=(("new_patient", 40),)))["notAPatient"] is None


# ── Missed calls ──────────────────────────────────────────────────────────

def test_missed_calls_are_sized_against_what_answered_calls_became():
    p = pl.missed_plan({"calls": 1000, "answered": 900, "missed": 100, "leads": 180})
    assert p["worth"] == ("100 of 1,000 calls (10%) were missed. Answered calls became CallRail leads 20% of the time, so if the missed ones had been answered that is about 20 more leads: "
                          "an estimate, since people who ring and get no answer often ring someone else.")
    t = " ".join(p["steps"])
    assert "Call flow" in t and "same business day" in t and "ad schedule" in t and len(p["steps"]) == 4


def test_missed_calls_under_a_tenth_have_no_plan_and_without_leads_there_is_no_lead_figure():
    assert pl.missed_plan({"calls": 1000, "answered": 901, "missed": 99, "leads": 100}) is None
    assert pl.missed_plan({"calls": 0, "answered": 0, "missed": 0, "leads": 0}) is None and pl.missed_plan({}) is None
    w = pl.missed_plan({"calls": 1000, "answered": 800, "missed": 200, "leads": 0})["worth"]
    assert w == "200 of 1,000 calls (20%) were missed."
    assert pl.missed_plan({"calls": 100, "answered": 0, "missed": 100, "leads": 5})["worth"] == "100 of 100 calls (100%) were missed."          # nobody answered: no answered rate to borrow


# ── Through the reads ─────────────────────────────────────────────────────

def test_the_analysis_carries_its_plans_and_still_no_call_text():
    pages = {i: [raw_call(i * 10 + j) for j in range(4)] for i in C.sample_pages(100)}
    st = FakeStore()
    out = C.analysis(st, ACC, START, END, http=CallRail(calls={"pages": 100, "records": 9999, "by_page": pages}))
    assert set(out["plans"]) == {"reasons", "notAPatient"} and set(out["plans"]["reasons"]) <= set(pl.REASON_STEPS)
    blob = json.dumps(out) + json.dumps(st.cache)
    for leak in ("A new patient called to book", "I am a new patient", "Hello"):
        assert leak not in blob, leak


def test_the_overview_carries_the_missed_call_plan_only_when_it_is_over_the_bar():
    quiet = C.overview(FakeStore(), ACC, START, END, http=CallRail(summary=SUMMARY))
    assert quiet["totals"]["missRate"] < pl.MISS_BAR and quiet["plan"] is None                                   # 37 of 454 is 8%
    from tests.test_paid_calls import total
    loud = C.overview(FakeStore(), ACC, START, END, http=CallRail(summary=dict(SUMMARY, total=total(454, 90, 44))), force=True)
    assert loud["plan"]["worth"].startswith("90 of 454 calls (20%) were missed.") and len(loud["plan"]["steps"]) == 4


def test_no_plan_uses_an_em_or_en_dash_or_leaves_a_placeholder():
    keys = [k for k, _l, _p in T.REASONS] + ["other"]
    out = pl.plans(analysis(reasons=tuple((k, 6) for k in keys), intents=(("not_a_patient", 9),)))
    plans_ = list(out["reasons"].values()) + [out["notAPatient"], pl.missed_plan({"calls": 1000, "answered": 900, "missed": 100, "leads": 180})]
    for p in plans_:
        text = " ".join(p["steps"]) + " " + (p["worth"] or "")
        assert EM not in text and EN not in text and "None" not in text and "{" not in text and "%s" not in text and "%d" not in text, text[:120]
