"""Search Query Analysis: how to apply what was found and what the negatives are worth (static/js/sqr_plan.js), run in Node.

The worth is arithmetic on the analysis' own numbers: what the priority negatives would have kept from terms that never converted, scaled to a month from the days read. It must not count
brand-routing negatives (that money goes to the Brand campaign, it is not wasted), must say when the range is unknown instead of guessing one, and must say the money is only saved if
the budget comes down. The steps must name the right place for each level and never tell anyone to block what the analysis flagged to check first.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
PLAN, PAGE = os.path.join(JS, "sqr_plan.js"), os.path.join(JS, "sqr_agent.js")
TEMPLATE = os.path.join(ROOT, "templates", "sqr_agent.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
EM, EN = chr(0x2014), chr(0x2013)


def run(expr, **data):
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    code = "const P=require(%s);const money=v=>'$'+Math.round(v).toLocaleString('en-US');%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(PLAN), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def neg(text="free", match="Phrase", level="Account", campaign="", wasted=100.0, cost=120.0, routing=False):
    return {"text": text, "matchType": match, "level": level, "campaign": campaign, "wasted": wasted, "cost": cost, "routing": routing}


def result(prio=(), review=(), kws=()):
    return {"priorityNegatives": list(prio), "reviewNegatives": list(review), "priorityKeywords": list(kws)}


RANGE = ["2026-09-01", "2026-09-30"]


# ── What the negatives are worth ──────────────────────────────────────────

def test_the_range_is_counted_in_days_both_ends_included_and_a_missing_one_is_not_guessed():
    assert run("P.days(R)", R=RANGE) == 30 and run("P.days(R)", R=["2026-10-01", "2026-10-01"]) == 1
    assert run("P.days(R)", R=None) is None and run("P.days(R)", R=["2026-10-05", "2026-10-01"]) is None and run("P.days(R)", R=["x", "y"]) is None and run("P.days(R)", R=["2026-10-01"]) is None


def test_the_worth_is_the_wasted_spend_scaled_to_thirty_days_and_says_what_it_assumes():
    r = result(prio=[neg(wasted=200.0), neg("cheap", wasted=100.0)], review=[neg("x", wasted=60.0)])
    w = run("P.saving(R,G,money)", R=r, G=["2026-09-01", "2026-09-15"])                         # 300 over 15 days is 600 over 30; 60 over 15 is 120
    assert w.startswith("These negatives would have kept about $600 a month from terms that never converted ($300 over the 15 days read), and up to $120 more if the ones to check first prove safe.")
    assert "same searches keep coming at the same cost" in w and "only if the budget comes down" in w
    assert run("P.saving(R,G,money)", R=result(prio=[neg(wasted=90.0)]), G=RANGE) .count("more if") == 0                  # nothing to check: no "more"
    assert "1 day read" in run("P.saving(R,G,money)", R=result(prio=[neg(wasted=10.0)]), G=["2026-09-01", "2026-09-01"])


def test_a_routing_negative_is_not_counted_as_waste_and_nothing_wasted_is_no_worth():
    r = result(prio=[neg(wasted=500.0, routing=True), neg("cheap", wasted=30.0)])
    assert "about $30 a month" in run("P.saving(R,G,money)", R=r, G=RANGE)
    assert run("P.saving(R,G,money)", R=result(prio=[neg(wasted=500.0, routing=True)]), G=RANGE) is None
    assert run("P.saving(R,G,money)", R=result(), G=RANGE) is None
    assert run("P.saving(R,G,money)", R=result(prio=[neg(wasted=50.0)]), G=None) is None                     # terms from files: no range, no monthly figure


# ── Negatives ─────────────────────────────────────────────────────────────

def test_each_level_of_negative_gets_its_own_step_and_the_campaigns_are_named():
    r = result(prio=[neg("free"), neg("cheap"), neg("jobs", level="Campaign", campaign="NB_Implants"), neg("careers", level="Campaign", campaign="NB_Implants"),
                     neg("acme", level="Campaign", campaign="NB_Braces", routing=True)])
    p = run("P.negativePlan(R,G,money)", R=r, G=RANGE)
    s = p["steps"]
    assert s[0].startswith("Take the rows from the table below") and "Negatives to Add (Priority)" in s[0]
    assert s[1].startswith("Account level (2 negatives)") and "Campaigns, then Keywords, then Negative keywords" in s[1] and "[exact], \"phrase\"" in s[1] and "Account settings" in s[1]
    assert s[2].startswith('Campaign level (2 negatives in "NB_Implants")') and "paste only that campaign's rows" in s[2]
    assert s[3].startswith("Brand searches in a non-brand campaign (1 negative)") and "Check the Brand campaign bids on the term first" in s[3]
    assert s[-1].startswith("Run this analysis again after a day or two") and len(s) == 5
    assert p["worth"] is not None


def test_many_negatives_are_pointed_at_google_ads_editor_and_few_are_not():
    few = run("P.negativePlan(R,G,money)", R=result(prio=[neg("n%d" % i) for i in range(30)]), G=RANGE)
    many = run("P.negativePlan(R,G,money)", R=result(prio=[neg("n%d" % i) for i in range(31)]), G=RANGE)
    assert not any("Google Ads Editor" in t for t in few["steps"]) and any("Google Ads Editor" in t and "Add/update negative keywords" in t for t in many["steps"])


def test_more_than_three_campaigns_are_counted_not_listed_and_the_same_one_is_named_once():
    prio = [neg("a", level="Campaign", campaign=c) for c in ("C1", "C2", "C3", "C4", "C5", "C1")]
    s = run("P.negativePlan(R,G,money)", R=result(prio=prio), G=RANGE)["steps"][1]
    assert s.startswith("Campaign level (6 negatives in ") and 'in "C1", "C2", "C3" and 2 more)' in s


def test_no_priority_negatives_means_no_plan_and_a_page_without_a_range_still_gets_steps():
    assert run("P.negativePlan(R,G,money)", R=result(), G=RANGE) is None
    p = run("P.negativePlan(R,G,money)", R=result(prio=[neg(wasted=50.0)]), G=None)
    assert p["worth"] is None and len(p["steps"]) == 3


# ── What to check, and keywords ───────────────────────────────────────────

def test_the_check_plan_never_tells_anyone_to_block_what_would_stop_a_keyword_or_a_competitor_they_bid_on():
    p = run("P.checkPlan(R)", R=result(review=[neg()]))
    t = " ".join(p["steps"])
    assert "exact match for the exact search" in t and "leave the negative out" in t and "competitor you bid on on purpose" in t and p["worth"] is None
    assert run("P.checkPlan(R)", R=result()) is None


def kw(term, kind="Keyword"):
    return {"term": term, "assetType": kind, "matchType": "Exact Match", "whereToAdd": "C / G"}


def test_keywords_and_search_themes_are_added_in_different_places():
    p = run("P.keywordPlan(R)", R=result(kws=[kw("a"), kw("b"), kw("c", "Search Theme")]))
    s = p["steps"]
    assert s[0].startswith("Keywords (2): in Google Ads open Campaigns, then Keywords, then Search keywords") and "[exact] or \"phrase\"" in s[0]
    assert s[1].startswith("Search themes (1, Performance Max): open the campaign, then Asset groups") and "take no match type" in s[1]
    assert "negatives first" in s[2] and "two weeks" in s[3] and len(s) == 4
    only = run("P.keywordPlan(R)", R=result(kws=[kw("a")]))["steps"]
    assert len(only) == 3 and not any("Search themes" in t for t in only)
    themes = run("P.keywordPlan(R)", R=result(kws=[kw("c", "Search Theme")]))["steps"]
    assert len(themes) == 3 and themes[0].startswith("Search themes (1,") and not any("Keywords (" in t for t in themes)
    assert run("P.keywordPlan(R)", R=result()) is None


# ── On the page ───────────────────────────────────────────────────────────

def test_the_page_draws_each_plan_above_its_tables_hint_and_loads_the_plan_before_the_page():
    js, html = open(PAGE, encoding="utf-8").read(), open(TEMPLATE, encoding="utf-8").read()
    assert "planHtml(Plan.negativePlan(r, (S.ran || {}).range, v => money(v))) + hint(" in js and "planHtml(Plan.checkPlan(r)) + hint(" in js and "planHtml(Plan.keywordPlan(r)) + hint(" in js
    assert html.index("sqr_core.js") < html.index("sqr_plan.js") < html.index("sqr_agent.js") and "sqr_plan.js') }}?v=" in html
    assert "What the priority negatives are worth" in js                                                      # and the workbook's summary carries it


def test_no_plan_uses_an_em_or_en_dash_or_leaves_a_placeholder():
    r = result(prio=[neg(), neg("jobs", level="Campaign", campaign="C"), neg("acme", level="Campaign", campaign="D", routing=True)] + [neg("n%d" % i) for i in range(40)], review=[neg()], kws=[kw("a"), kw("b", "Search Theme")])
    blob = json.dumps([run("P.negativePlan(R,G,money)", R=r, G=RANGE), run("P.checkPlan(R)", R=r), run("P.keywordPlan(R)", R=r)], ensure_ascii=False)
    assert EM not in blob and EN not in blob and "undefined" not in blob and "NaN" not in blob and "null," not in blob.replace('"worth": null', "")
    assert not re.search(r"\$NaN|\$undefined", blob)
