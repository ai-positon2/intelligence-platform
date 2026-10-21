"""Ad Copy Checker: what to fix and in what order (tracker/paid_copycheck_plan.py). No network."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests.test_paid_copycheck_analyse import ad, run  # noqa: E402
from tracker import paid_copycheck_plan as plan  # noqa: E402
from tracker import paid_copycheck_rules as rules  # noqa: E402


def test_every_rule_has_a_title_that_takes_a_count_and_a_how():
    assert set(plan.TITLES) == set(rules.RULES) == set(plan.HOW)
    for k, t in plan.TITLES.items():
        assert t.count("%s") == 1 and "—" not in t + plan.HOW[k] and plan.HOW[k].endswith(".")


def test_nothing_found_is_no_steps_and_no_recheck():
    assert plan.build(run([ad(1, ("headline", "Quality Dental Care"), ("headline", "Book Today"), ("description", "We offer implants."))])) == {"steps": []}


def test_the_steps_follow_the_rules_by_level_name_the_ads_and_the_first_places_to_open():
    ads = [ad(1, ("headline", "{Brand} implants"), ("description", "We offer implants."), camp="Core", group="Implants"),
           ad(2, ("headline", "Dentst near you"), ("description", "We offer implants."), camp="Core", group="Cleanings"),
           ad(3, ("headline", "Dentst near you!!"), ("description", "We offer implants."), camp="Brand", group=""),
           ad(4, ("headline", "Call Now For FREEBIES today"), ("description", "We offer implants."), camp="Brand", group="Offers")]
    steps = plan.build(run(ads))["steps"]
    assert [(s["id"], s["level"], s["ads"]) for s in steps] == [("placeholder", "high", 1), ("spelling", "medium", 2), ("punct", "medium", 1), ("caps_word", "low", 1), ("recheck", "info", 0)]
    assert steps[0]["title"] == "Fill in or remove the placeholders in 1 ad" and steps[1]["title"] == "Correct the misspelt words in 2 ads"
    assert steps[1]["where"] == ["Brand", "Core / Cleanings"] and steps[1]["examples"] == ["Dentst", "Dentst"] and steps[0]["where"] == ["Core / Implants"] and steps[0]["examples"] == ["{Brand}"]
    assert steps[1]["how"] == plan.HOW["spelling"] and "Refresh" in steps[-1]["how"] and steps[-1]["where"] == []


def test_at_most_three_places_are_named_for_a_step():
    ads = [ad(i, ("headline", "Quality Care!!"), ("description", "We offer implants."), group="G%d" % i) for i in range(1, 8)]
    s = plan.build(run(ads))["steps"][0]
    assert s["id"] == "punct" and s["ads"] == 7 and len(s["where"]) == 3 and len(s["examples"]) == 3
