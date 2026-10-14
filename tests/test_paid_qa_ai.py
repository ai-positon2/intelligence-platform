"""Paid Marketing Agents: QA Agent, the AI reviewer's guard rails.

The model is never trusted on its own. Pinned here: it is told to use only the evidence
and to say cannot_verify rather than guess; every pass or flag must quote evidence that is
really in what it was shown, or it is turned into cannot_verify; exact Google Ads rules
outrank it; a section that fails (model down, refusal, junk) leaves its checks for a
person and never yields a pass; each section gets only its own checks and evidence.
Nothing here calls a model: `call` is a stand-in.
"""
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_qa as qa  # noqa: E402
from tracker import paid_qa_ai as ai  # noqa: E402
from tracker import paid_qa_auto as auto  # noqa: E402
from tests.test_paid_qa_auto import ad_group, campaign, customer, kw, rows, rsa  # noqa: E402

EVIDENCE = json.dumps({"campaign": {"name": "Example_Brand_Search", "dailyBudget": "30.00 USD"},
                       "adGroups": [{"ads": [{"headlines": [{"text": "Free estimates today!!"}]}]}]})


def answer(id_, verdict="pass", finding="The campaign is named Example_Brand_Search.", evidence="Example_Brand_Search", fix=""):
    return {"id": id_, "verdict": verdict, "finding": finding, "evidence": evidence, "fix": fix}


# ── The instructions ────────────────────────────────────────────────────────

def test_the_model_is_told_to_use_only_the_evidence_and_never_to_guess():
    s = ai.SYSTEM
    assert "Use only the evidence given" in s and "cannot_verify" in s and "Never guess a pass" in s
    assert "media plan" in s and "client's approvals" in s
    assert "ruleFindings" in s or "ruleFinding" in s
    assert "—" not in s


def test_the_answer_format_has_exactly_the_four_fields_and_three_verdicts():
    item = ai.SCHEMA["properties"]["results"]["items"]
    assert set(item["properties"]) == {"id", "verdict", "finding", "evidence", "fix"} and item["additionalProperties"] is False
    assert item["properties"]["verdict"]["enum"] == ["pass", "flag", "cannot_verify"]


# ── Quotes must be in the evidence ──────────────────────────────────────────

def test_a_quote_from_the_evidence_is_grounded_and_an_invented_one_is_not():
    assert ai.grounded("Free estimates today!!", EVIDENCE)
    assert ai.grounded("headline: free estimates today", EVIDENCE)                      # trimmed and reworded still passes
    assert not ai.grounded("Seasonal 50% off patio sale ends Friday", EVIDENCE)         # made up
    assert not ai.grounded("ok", EVIDENCE)                                              # too short to mean anything


# ── Turning the model's answer into findings ────────────────────────────────

def test_a_pass_with_grounded_evidence_is_a_pass_and_a_flag_keeps_its_fix():
    out = ai.validate([answer("3.2"), answer("6.2", "flag", "Headline 1 ends with two exclamation marks.", "Free estimates today!!",
                                             "Remove the exclamation marks from headline 1.")], ["3.2", "6.2"], EVIDENCE)
    assert out["3.2"] == {"result": "pass", "found": "The campaign is named Example_Brand_Search.", "evidence": "Example_Brand_Search",
                          "fix": "", "source": "ai", "unverified": False}
    assert out["6.2"]["result"] == "fail" and out["6.2"]["fix"].startswith("Remove the exclamation") and out["6.2"]["unverified"] is False


def test_a_pass_never_carries_a_fix():
    out = ai.validate([answer("3.2", fix="Do something")], ["3.2"], EVIDENCE)
    assert out["3.2"]["result"] == "pass" and out["3.2"]["fix"] == ""


def test_an_answer_that_cannot_be_tied_to_the_evidence_is_not_counted():
    out = ai.validate([answer("6.2", "flag", "Headline 2 promises a 50% discount that the page does not mention.", "Seasonal 50% off patio sale ends Friday")],
                      ["6.2"], EVIDENCE)
    assert out["6.2"]["result"] is None and out["6.2"]["unverified"] is True
    assert "could not be tied to the evidence" in out["6.2"]["found"] and "50% discount" in out["6.2"]["found"]


def test_a_pass_or_flag_without_evidence_or_without_a_finding_is_not_counted():
    out = ai.validate([answer("3.2", evidence=""), answer("6.2", "flag", finding="bad", evidence="Free estimates today!!")], ["3.2", "6.2"], EVIDENCE)
    assert out["3.2"]["unverified"] and out["6.2"]["unverified"] and out["3.2"]["result"] is None and out["6.2"]["result"] is None


def test_cannot_verify_passes_through_with_its_reason():
    out = ai.validate([answer("14.4", "cannot_verify", "The client's written sign-off is not something Google Ads shows.", "")], ["14.4"], EVIDENCE)
    assert out["14.4"]["result"] is None and out["14.4"]["unverified"] and "sign-off" in out["14.4"]["found"]


def test_a_skipped_check_an_unknown_id_and_a_bad_verdict_never_become_passes():
    out = ai.validate([answer("3.2"), answer("99.9"), {"id": "6.2", "verdict": "maybe", "finding": "x", "evidence": "", "fix": ""},
                       "junk", None, answer("3.2", "flag", "A second, conflicting answer for the same check.", "Example_Brand_Search")],
                      ["3.2", "6.2", "6.3"], EVIDENCE)
    assert set(out) == {"3.2", "6.2", "6.3"}                                           # exactly what was asked
    assert out["3.2"]["result"] == "pass"                                              # the first answer stands
    assert out["6.2"]["unverified"] and out["6.3"]["unverified"] and "did not answer" in out["6.3"]["found"]


def test_a_reply_that_is_not_a_list_leaves_everything_for_a_person():
    for bad in (None, {}, "text", 5):
        out = ai.validate(bad, ["3.2", "6.2"], EVIDENCE)
        assert all(f["unverified"] and f["result"] is None for f in out.values())


def test_em_dashes_are_cleaned_from_what_the_page_shows():
    out = ai.validate([answer("3.2", finding="Named Example_Brand_Search — follows the convention.")], ["3.2"], EVIDENCE)
    assert "—" not in out["3.2"]["found"] and "Example_Brand_Search, follows" in out["3.2"]["found"]


# ── Exact rules outrank the AI ──────────────────────────────────────────────

def test_an_exact_rules_pass_or_fail_stands_whatever_the_ai_said():
    rules = {"1.6": {"result": "fail", "found": "Auto-tagging is OFF on the account."}, "4.1": {"result": "pass", "found": "Display Network is OFF."}}
    said = {"1.6": {"result": "pass", "found": "Auto-tagging looks fine.", "evidence": "x", "fix": "", "source": "ai", "unverified": False},
            "4.1": {"result": "fail", "found": "Display looks on.", "evidence": "x", "fix": "Turn it off", "source": "ai", "unverified": False}}
    out = ai.combine(rules, said, ["1.6", "4.1"])
    assert out["1.6"]["result"] == "fail" and out["1.6"]["source"] == "rule" and out["1.6"]["found"].startswith("Auto-tagging is OFF")
    assert out["4.1"]["result"] == "pass" and out["4.1"]["source"] == "rule"


def test_where_a_rule_only_printed_a_value_the_ai_decides_and_the_printed_value_is_kept():
    rules = {"3.6": {"result": None, "found": "Daily budget 30.00 USD, not shared."}}
    said = {"3.6": {"result": "pass", "found": "Budget is in line with spend.", "evidence": "30.00 USD", "fix": "", "source": "ai", "unverified": False}}
    out = ai.combine(rules, said, ["3.6"])
    assert out["3.6"]["result"] == "pass" and out["3.6"]["source"] == "ai" and out["3.6"]["facts"] == "Daily budget 30.00 USD, not shared."


def test_with_no_ai_answer_a_printed_rule_is_unverified_and_nothing_at_all_is_unverified():
    out = ai.combine({"3.6": {"result": None, "found": "Daily budget 30.00 USD."}}, {}, ["3.6", "14.4"])
    assert out["3.6"]["unverified"] and out["3.6"]["result"] is None and out["3.6"]["found"] == "Daily budget 30.00 USD."
    assert out["14.4"]["unverified"] and out["14.4"]["result"] is None


# ── Asking section by section ───────────────────────────────────────────────

def checks(*ids):
    return [qa.item(i) for i in ids]


META = {"name": "Example_Brand_Search", "type": "Search", "clientTypes": [], "today": "2026-10-14"}


class Model:
    def __init__(self, fn=None, boom=None):
        self.fn, self.boom, self.asked = fn, boom, []

    def __call__(self, system, user, *, max_tokens):
        if self.boom:
            raise self.boom
        req = json.loads(user)
        self.asked.append((req, max_tokens))
        return {"results": [self.fn(c) for c in req["checks"]]}


def ok(c):
    return answer(c["id"])


def test_each_section_is_asked_separately_with_only_its_own_checks_and_evidence():
    m = Model(ok)
    ev = {"account": {"a": 1}, "campaign": {"name": "Example_Brand_Search"}, "adGroups": [{"x": 1}], "landingPages": [{"y": 1}],
          "assets": {}, "negatives": {}, "conversions": {}, "changeHistoryLast28Days": {}}
    findings, problems = ai.review(checks("1.1", "1.2", "6.1", "13.1"), ev, {"1.2": {"result": None, "found": "Billing setup APPROVED."}},
                                   META, call=m)
    assert problems == {} and set(findings) == {"1.1", "1.2", "6.1", "13.1"}
    by = {req["section"]: req for req, _ in m.asked}
    assert len(m.asked) == 3
    s1 = by["1. Account Setup & Access"]
    assert [c["id"] for c in s1["checks"]] == ["1.1", "1.2"] and "adGroups" not in s1["evidence"] and "account" in s1["evidence"]
    assert s1["checks"][1]["ruleFinding"] == "Billing setup APPROVED." and "ruleFinding" not in s1["checks"][0]
    s6 = next(r for k, r in by.items() if k.startswith("6."))
    assert "adGroups" in s6["evidence"] and "account" not in s6["evidence"]
    assert s6["campaign"]["clientTypesChosen"] == "not chosen" and s6["today"] == "2026-10-14"


def test_every_check_row_carries_its_severity_and_how_to_verify():
    m = Model(ok)
    ai.review(checks("2.10"), {"campaign": {}}, {}, META, call=m)
    row = m.asked[0][0]["checks"][0]
    assert row["severity"] == "Critical" and "Consent Mode" in row["check"] and row["expectedSettingOrHowToVerify"]
    assert "EEA" in row["appliesOnlyIf"]


def test_a_section_that_fails_leaves_its_checks_for_a_person_and_the_other_sections_still_run():
    def fn(c):
        return answer(c["id"])
    calls = {"n": 0}

    class Flaky(Model):
        def __call__(self, system, user, *, max_tokens):
            calls["n"] += 1
            if json.loads(user)["section"].startswith("6."):
                raise RuntimeError("boom")
            return super().__call__(system, user, max_tokens=max_tokens)
    findings, problems = ai.review(checks("1.1", "6.1", "6.2"), {"campaign": {"name": "Example_Brand_Search"}}, {}, META, call=Flaky(fn), workers=1)
    assert findings["1.1"]["result"] == "pass"
    assert findings["6.1"]["result"] is None and findings["6.1"]["unverified"] and "could not be completed" in findings["6.1"]["found"]
    assert findings["6.2"]["result"] is None and problems == {"RuntimeError": 1}


def test_a_model_that_is_down_or_not_configured_never_produces_a_pass():
    from tracker.market_radar_llm import ModelError
    for err in (ModelError("not_configured", "no key"), ModelError("refused", "declined"), ModelError("truncated", "cut off"),
                ModelError("bad_json", "not json")):
        findings, problems = ai.review(checks("1.1", "6.1"), {}, {}, META, call=Model(boom=err))
        assert all(f["result"] is None and f["unverified"] for f in findings.values())
        assert problems == {err.kind: 2}


def test_progress_is_reported_after_each_section():
    seen = []
    ai.review(checks("1.1", "3.1", "6.1"), {"campaign": {"name": "Example_Brand_Search"}}, {}, META, call=Model(ok),
              progress=lambda done, total: seen.append((done, total)))
    assert seen == [(1, 3), (2, 3), (3, 3)]


def test_the_reply_budget_grows_with_the_section_and_is_capped():
    m = Model(ok)
    ai.review(checks("1.1"), {}, {}, META, call=m)
    assert m.asked[0][1] == 450 * 1 + 600
    big = [i for i in qa.items() if i["id"].startswith("6.")] * 6
    m2 = Model(ok)
    ai.review_section("6. Ad Copy", big, {}, {}, META, call=lambda s, u, max_tokens: (m2.asked.append(max_tokens) or {"results": []}))
    assert m2.asked == [9000]


# ── The evidence it is shown ────────────────────────────────────────────────

def sample_data():
    d = rows(customer=customer(descriptiveName="Example Co", trackingUrlTemplate="{lpurl}?utm_source=google"),
             campaign=campaign(name="Example_Brand_Search", biddingStrategyType="MAXIMIZE_CONVERSIONS"),
             adgroups=[ad_group("11", "Brand")], ads=[rsa("11", headlines=3, descriptions=2)], keywords=[kw("11", "example", "EXACT")],
             criteria=[{"campaignCriterion": {"type": "LOCATION", "location": {"geoTargetConstant": "geoTargetConstants/10"}}},
                       {"campaignCriterion": {"type": "DEVICE", "bidModifier": 0.5, "device": {"type": "MOBILE"}}},
                       {"campaignCriterion": {"type": "AGE_RANGE", "negative": True, "ageRange": {"type": "AGE_RANGE_18_24"}}},
                       {"campaignCriterion": {"type": "KEYWORD", "negative": True, "keyword": {"text": "jobs", "matchType": "BROAD"}}}],
             camp_assets=[{"campaignAsset": {"fieldType": "SITELINK", "status": "ENABLED"},
                           "asset": {"type": "SITELINK", "finalUrls": ["https://example.com/a"], "sitelinkAsset": {"linkText": "Our work", "description1": "See it"}}}],
             search_terms=[{"searchTermView": {"searchTerm": "landscaping near me", "status": "NONE"},
                            "metrics": {"clicks": "4", "costMicros": "9000000", "conversions": 1}}],
             changes=[{"changeEvent": {"userEmail": "a@b.com", "resourceChangeOperation": "UPDATE", "changeResourceType": "AD"}}])
    d["_geo"] = {"geoTargetConstants/10": {"name": "Annapolis", "canonical": "Annapolis,Maryland,United States"}}
    d["_pages"] = [{"url": "https://example.com/", "text": "x" * 5000, "status": 200}]
    return d


def test_the_evidence_has_what_the_checks_need_to_be_judged():
    d = sample_data()
    ev = ai.evidence_bundle(d, auto.Ctx(d))
    assert ev["account"]["trackingTemplate"] == "{lpurl}?utm_source=google" and ev["campaign"]["name"] == "Example_Brand_Search"
    assert ev["targeting"]["locationsTargeted"] == ["Annapolis,Maryland,United States"]
    assert ev["targeting"]["devices"] == [{"device": "MOBILE", "bidModifier": 0.5, "negative": None}]
    assert ev["targeting"]["demographics"] == [{"type": "AGE_RANGE", "value": "AGE_RANGE_18_24", "excluded": True, "bidModifier": None}]
    assert ev["negatives"]["campaignNegativeKeywords"] == ["jobs [broad]"]
    g = ev["adGroups"][0]
    assert g["name"] == "Brand" and g["keywords"][0]["text"] == "example" and g["ads"][0]["headlines"][0]["text"] == "H0"
    assert ev["assets"]["campaignLevel"][0]["text"] == "Our work" and ev["assets"]["campaignLevel"][0]["urls"] == ["https://example.com/a"]
    assert ev["searchTermsLast30Days"][0]["term"] == "landscaping near me"
    assert ev["changeHistoryLast28Days"]["byUser"] == {"a@b.com": 1} and ev["changeHistoryLast28Days"]["byKind"] == {"UPDATE AD": 1}


def test_a_landing_pages_text_is_cut_so_the_evidence_stays_readable():
    d = sample_data()
    ev = ai.evidence_bundle(d, auto.Ctx(d))
    assert len(ev["landingPages"][0]["text"]) == 1800
    json.dumps(ev, default=str)                                                       # and it serialises


def test_every_section_has_an_evidence_list_that_names_real_evidence():
    d = sample_data()
    ev = ai.evidence_bundle(d, auto.Ctx(d))
    for key, wanted in ai.SECTION_EVIDENCE.items():
        assert {i["id"].split(".")[0] for i in qa.items()} >= {key}
        assert set(wanted) <= set(ev), (key, set(wanted) - set(ev))
    assert {i["id"].split(".")[0] for i in qa.items()} == set(ai.SECTION_EVIDENCE)     # no section forgotten


def test_an_empty_campaign_still_gives_evidence_without_errors():
    d = rows()
    ev = ai.evidence_bundle(d, auto.Ctx(d))
    assert ev["adGroups"] == [] and ev["landingPages"] == [] and ev["targeting"]["languages"] == "all languages"
