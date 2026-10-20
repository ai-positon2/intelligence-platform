"""QA Agent: the steps that come with a failed check (tracker/paid_qa_fixes.py)."""
import json
import os

import pytest

from tracker import paid_qa_auto, paid_qa_fixes as fx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def checks():
    d = json.load(open(os.path.join(ROOT, "tracker", "qa_checklist.json"), encoding="utf-8"))
    out = []

    def walk(x):
        if isinstance(x, list):
            for y in x:
                walk(y)
        elif isinstance(x, dict):
            if "id" in x and "check" in x:
                out.append(x)
            for v in x.values():
                walk(v)
    walk(d)
    return {c["id"]: c for c in out}


def test_every_check_a_rule_can_fail_has_steps():
    assert set(fx.HOW) == set(paid_qa_auto.RULES)
    for cid in paid_qa_auto.RULES:
        assert fx.how(cid, "SEARCH") and fx.how(cid, "PERFORMANCE_MAX"), cid


def test_the_steps_are_written_for_a_person_in_the_google_ads_screens_and_have_no_dashes_or_stray_marks():
    for cid, v in fx.HOW.items():
        for text in (v.values() if isinstance(v, dict) else [v]):
            assert len(text) > 40 and text.endswith("."), cid
            assert "\u2014" not in text and "\u2013" not in text and "%s" not in text and "{}" not in text, cid
            assert any(w in text for w in ("Open", "Create", "Set ", "Google Ads does not show")), cid


def test_the_final_url_expansion_steps_depend_on_the_campaign_type():
    assert "untick Use final URL expansion" in fx.how("2.1", "PERFORMANCE_MAX") and "AI Max" in fx.how("2.1", "SEARCH") and "AI Max" in fx.how("2.1", "")
    assert fx.how("9.9", "SEARCH") == ""


def test_a_failed_check_gets_the_steps_and_the_checklists_expected_value():
    rows = checks()
    found = {"1.5": {"result": "fail", "found": "AI Max is On.", "fix": "", "source": "rule"}}
    out = fx.attach(found, [rows["1.5"]], "SEARCH")
    assert out["1.5"]["fix"].startswith("Open the campaign, then Settings, then AI Max") and out["1.5"]["fix"].endswith("The checklist expects: Turned OFF.")
    assert out["1.5"]["found"] == "AI Max is On." and found["1.5"]["fix"] == ""                   # the input is not changed


def test_only_a_failed_check_with_no_fix_gets_one():
    rows = [checks()["1.5"], checks()["1.3"], checks()["1.4"], checks()["1.19"]]
    found = {"1.5": {"result": "pass", "fix": ""}, "1.3": {"result": None, "fix": "", "unverified": True},
             "1.4": {"result": "fail", "fix": "Write the ad yourself."}, "1.19": {"result": "fail", "fix": "   "}}
    out = fx.attach(found, rows, "SEARCH")
    assert out["1.5"]["fix"] == "" and out["1.3"]["fix"] == "" and out["1.4"]["fix"] == "Write the ad yourself." and out["1.19"]["fix"].startswith("Open the campaign, then Settings, then EU political ads")


@pytest.mark.parametrize("expected,adds", [("Depends on the campaign book", False), ("From the campaign book", False), ("None", False), ("", False), (None, False), ("Check the age", False),
                                           ("English", True), ("Turned OFF", True), ("Optimize: Prefer best performing ads", True)])
def test_an_expected_value_that_says_nothing_is_not_repeated(expected, adds):
    out = fx.attach({"1.15": {"result": "fail", "fix": ""}}, [{"id": "1.15", "expected": expected}], "SEARCH")
    assert ("The checklist expects" in out["1.15"]["fix"]) is adds


def test_a_failed_check_with_no_known_steps_is_left_as_it_is_and_a_missing_checklist_row_is_fine():
    out = fx.attach({"9.9": {"result": "fail", "fix": ""}, "1.5": {"result": "fail", "fix": ""}}, [], "SEARCH")
    assert out["9.9"]["fix"] == "" and out["1.5"]["fix"] and "checklist expects" not in out["1.5"]["fix"]


def test_the_auto_apply_recommendations_all_say_where_to_turn_them_off_and_name_themselves():
    for cid, label in fx.AUTO_APPLY.items():
        assert ('find "%s" in the list, and turn it off' % label) in fx.how(cid, "SEARCH") and "Auto-apply" in fx.how(cid, "SEARCH")
    names = {c["id"]: c["check"] for c in checks().values() if c["id"].startswith("4.")}
    assert set(names) >= set(fx.AUTO_APPLY)
    assert fx.AUTO_APPLY["4.11"].lower().replace("use ", "") .startswith("bid more efficiently with target impression share")


def test_the_automatic_asset_switches_name_the_setting_and_the_value_the_checklist_wants():
    assert fx.how("3.4").endswith('set "Dynamic business logos" to On.') and fx.how("3.6").endswith('set "Dynamic callouts" to Off.')


def test_the_review_job_attaches_the_steps_to_the_saved_findings():
    src = open(os.path.join(ROOT, "tracker", "paid_qa_job.py"), encoding="utf-8").read()
    assert 'paid_qa_fixes.attach(paid_qa_ai.combine(rules, ai, ids), checks, camp["channel"])' in src
