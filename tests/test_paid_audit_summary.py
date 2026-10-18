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


def test_only_numbers_the_audit_gave_are_free_and_a_small_invented_count_is_not(report):
    """The page says every number in the briefing is one the audit gave. With 1 to 10 free, "9 checks failed" passed whatever the audit found."""
    ok = model_says("Three things matter, and the score is %s." % report["summary"]["score"])            # a number written as a word is not a figure
    assert s.summarise(report, call=ok)["source"] == "model"
    for invented in ("9 checks failed and 7 are warnings.", "Eleven things matter, in 11 places.", "It failed 8 checks."):
        assert s.summarise(report, call=model_says(invented))["source"] == "rules", invented
    given = report["summary"]["tally"]["fail"]
    assert s.summarise(report, call=model_says("%d checks failed." % given))["source"] == "model"         # the audit's own count is fine


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
    out = s.summarise(report, call=model_says(steps=["step " + chr(97 + i) for i in range(20)], asks=["question " + chr(97 + i) for i in range(20)]))
    assert len(out["firstSteps"]) == 6 and len(out["questionsForClient"]) == 6


def test_one_model_call_per_audit_however_many_ask(report):
    call = model_says("It scored %s." % report["summary"]["score"])
    a = s.cached_summary("k", report, now=1000.0, call=call)
    b = s.cached_summary("k", report, now=1000.0 + s.TTL_S - 1, call=call)
    assert a == b and len(call.calls) == 1
    s.cached_summary("k", report, now=1000.0 + s.TTL_S, call=call)
    assert len(call.calls) == 2                                                    # at exactly the limit it is stale: asked again
    s.cached_summary("k", dict(report, readAt="2026-10-18T00:00:00+00:00"), now=1000.0 + s.TTL_S + 2, call=call)
    assert len(call.calls) == 3                                                    # an audit made from a new read is a new briefing
    s.cached_summary("other", report, now=1.0, call=call)
    assert len(call.calls) == 4


def test_the_cache_is_bounded(report):
    call = model_says("It scored %s." % report["summary"]["score"])
    for i in range(80):
        s.cached_summary("k%d" % i, report, now=float(i), call=call)
    assert len(s._summaries) == 64 and "k0" not in s._summaries and "k79" in s._summaries


def test_the_same_read_is_the_same_audit_even_though_every_report_has_a_new_time_on_it(report):
    call = model_says("It scored %s." % report["summary"]["score"])
    again = dict(report, generatedAt="2026-10-17T12:00:09+00:00")                      # the report is made afresh on each request, from the same saved read
    s.cached_summary("k", report, now=10.0, call=call)
    s.cached_summary("k", again, now=11.0, call=call)
    assert len(call.calls) == 1
    assert s.read_marker(report) == report["readAt"] and s.read_marker({"generatedAt": "g"}) == "g" and s.read_marker({"readAt": None, "generatedAt": "g"}) == "g"


def test_identifiers_are_masked_before_the_model_sees_a_sentence(report):
    r = make_report()
    first = r["fixFirst"][0]
    first["found"] = "The account has a Google tag (AW-1057982098) and Merchant Center 101609788 is linked; USD 24,240 was spent."
    shown = s.summary_input(r)
    assert shown["fixFirst"][0]["found"] == "The account has a Google tag (an id) and Merchant Center an id is linked; USD 24,240 was spent."
    text = json.dumps(shown)
    assert "AW-1057982098" not in text and "101609788" not in text and "24,240" in text
    unknown = next(f for sec in r["sections"] for f in sec["findings"] if f["result"] == "unknown")
    unknown["found"] = "Could not be checked: account 1112223334 refused"
    assert "1112223334" not in json.dumps(s.summary_input(r)) and s._mask(None) == "" and s._mask("12345 and 123456") == "12345 and 123456"
