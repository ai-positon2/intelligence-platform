"""Account Audit: the Claude-written briefing (tracker/paid_audit_summary.py), on a stand-in for the model."""
import json

import pytest

from tests.test_paid_audit_export import make_report
from tracker import paid_audit_summary as s
from tracker.market_radar_llm import ModelError


@pytest.fixture(autouse=True)
def fresh():
    s.clear_cache()
    yield
    s.clear_cache()


@pytest.fixture(scope="module")
def report():
    return make_report()


def model_says(summary="Acme Dental scored a poor mark because the tracking records nothing.", steps=("Fix the tag first.",), asks=("Who owns the tracking?",)):
    calls = []

    def call(system, user):
        calls.append((system, user))
        return {"summary": summary, "firstSteps": list(steps), "questionsForClient": list(asks)}
    call.calls = calls
    return call


def test_the_model_is_shown_scores_and_the_checks_to_fix_first_and_nothing_named(report):
    shown = s.summary_input(report)
    assert shown["accounts"] == ["Acme Dental (Google Ads)", "Acme Meta (Meta Ads)"]
    assert shown["overall"]["score"] == report["summary"]["score"] and shown["overall"]["failedCritical"] == report["summary"]["critical"]
    assert set(shown["platforms"]) == {"google", "meta"} and shown["sections"][0] == {"section": "Account and settings", "platform": "google", "score": report["sections"][0]["score"], "grade": report["sections"][0]["grade"]}
    assert shown["fixFirst"][0]["check"] == "Conversions were recorded in the last 30 days" and shown["fixFirst"][0]["severity"] == "critical" and shown["fixFirst"][0]["result"] == "fail"
    assert all(set(f) == {"check", "severity", "result", "found", "whatToDo"} for f in shown["fixFirst"]) and len(shown["fixFirst"]) <= s.FIX_SHOWN
    assert any("GA4 link" in u["check"] or "Google Analytics 4" in u["check"] for u in shown["couldNotCheck"])
    text = json.dumps(shown)
    assert "acme.test" not in text and "implants near me" not in text and "=cmd" not in text                      # the affected pages, terms and campaigns are never sent
    assert "items" not in text


def test_a_clean_answer_is_returned_as_the_models(report):
    call = model_says("The account scored %s. Tracking needs attention first." % report["summary"]["score"])
    out = s.summarise(report, call=call)
    assert out["source"] == "model" and out["summary"].startswith("The account scored") and out["firstSteps"] == ["Fix the tag first."] and out["questionsForClient"] == ["Who owns the tracking?"]
    assert len(call.calls) == 1 and "Audit results:" in call.calls[0][1] and "never an em dash" in call.calls[0][0]


def test_a_number_that_was_not_given_sends_the_model_back_once_and_a_second_miss_falls_back_to_the_findings(report):
    answers = iter([{"summary": "The account lost 9,999 dollars.", "firstSteps": [], "questionsForClient": []},
                    {"summary": "It scored %s overall." % report["summary"]["score"], "firstSteps": [], "questionsForClient": []}])
    seen = []

    def call(system, user):
        seen.append(user)
        return next(answers)
    out = s.summarise(report, call=call)
    assert out["source"] == "model" and len(seen) == 2 and "numbers that were not in the results above (9999)" in seen[1]
    stubborn = lambda system, user: {"summary": "It lost 8,888 dollars.", "firstSteps": ["Spend 7,777."], "questionsForClient": []}
    out = s.summarise(report, call=stubborn)
    assert out["source"] == "rules" and "8,888" not in out["summary"] and out["questionsForClient"] == []


def test_numbers_up_to_ten_and_the_ones_in_the_results_are_free(report):
    ok = model_says("Three things matter, and the score is %s." % report["summary"]["score"])
    assert s.summarise(report, call=ok)["source"] == "model"
    bad = model_says("Eleven things matter, in 11 places.")
    assert s.summarise(report, call=bad)["source"] == "rules"                       # 11 is a number the audit never gave


def test_dashes_in_the_models_words_become_commas(report):
    out = s.summarise(report, call=model_says("Tracking is broken — fix it first – then the ads.", steps=["Start — here."], asks=["Who — owns it?"]))
    assert out["summary"] == "Tracking is broken, fix it first, then the ads." and out["firstSteps"] == ["Start, here."] and out["questionsForClient"] == ["Who, owns it?"]


def test_an_empty_answer_or_a_model_that_is_not_there_falls_back_in_the_findings_words(report):
    empty = s.summarise(report, call=lambda system, user: {"summary": "", "firstSteps": [], "questionsForClient": []})
    assert empty["source"] == "rules" and "scored" in empty["summary"] and len(empty["firstSteps"]) == 3
    def down(system, user):
        raise ModelError("not_configured", "no key")
    out = s.summarise(report, call=down)
    assert out["source"] == "rules" and out["unavailable"] == "not_configured"
    assert out["summary"].startswith("Acme Dental (Google Ads) and Acme Meta (Meta Ads) scored ") and out["firstSteps"][0].startswith("USD ")


def test_the_fallback_says_when_nothing_needs_fixing_first():
    from tracker import paid_audit as audit
    out = s.summarise(audit.assemble(None, None, [], now=None), call=lambda a, b: (_ for _ in ()).throw(ModelError("api_error", "x")))
    assert out["source"] == "rules" and "Nothing needs fixing first." in out["summary"] and out["firstSteps"] == []


def test_the_answers_lists_are_capped(report):
    out = s.summarise(report, call=model_says(steps=["s%d" % i for i in range(20)], asks=["q%d" % i for i in range(20)]))
    assert len(out["firstSteps"]) == 6 and len(out["questionsForClient"]) == 6


def test_one_model_call_per_audit_however_many_ask(report):
    call = model_says("It scored %s." % report["summary"]["score"])
    a = s.cached_summary("k", report, now=1000.0, call=call)
    b = s.cached_summary("k", report, now=1000.0 + s.TTL_S - 1, call=call)
    assert a == b and len(call.calls) == 1
    s.cached_summary("k", report, now=1000.0 + s.TTL_S, call=call)
    assert len(call.calls) == 2                                                    # at exactly the limit it is stale: asked again
    s.cached_summary("k", dict(report, generatedAt="2026-10-18T00:00:00+00:00"), now=1000.0 + s.TTL_S + 2, call=call)
    assert len(call.calls) == 3                                                    # a new audit is a new briefing
    s.cached_summary("other", report, now=1.0, call=call)
    assert len(call.calls) == 4


def test_the_cache_is_bounded(report):
    call = model_says("It scored %s." % report["summary"]["score"])
    for i in range(80):
        s.cached_summary("k%d" % i, report, now=float(i), call=call)
    assert len(s._summaries) == 64 and "k0" not in s._summaries and "k79" in s._summaries
