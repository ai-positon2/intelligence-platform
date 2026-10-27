"""Ad Strength Analysis page: Google, Meta and Both (static/js/ad_strength.js with the shared scope control and static/js/meta_creative.js), run against a stub page.

Google's report is drawn as it always was; Meta's ad quality view is the real report the server builds from Meta's answers (tests/test_paid_meta_creative.py shapes). Both asks each
platform once and draws the two one under the other, and one failing never hides the other.
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests import test_paid_meta_creative as M  # noqa: E402
from tests.test_copycheck_js import HARNESS as BASE  # noqa: E402
from tracker import paid_meta_creative_analyse as an  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
PAGE, SCOPE, MC = os.path.join(JS, "ad_strength.js"), os.path.join(JS, "pm_scope.js"), os.path.join(JS, "meta_creative.js")
TEMPLATE = os.path.join(ROOT, "templates", "ad_strength.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
HARNESS = BASE.replace("/p2/paid-marketing/ad-copy-checker", "/p2/paid-marketing/ad-strength")
assert "/ad-strength" in HARNESS

GROUP = "mcc-9923720096-USD"
ACCTS = {"accounts": [{"id": "1234567890", "name": "Acme", "currency": "USD", "group": GROUP}], "groups": [{"id": GROUP, "name": "Group", "accountIds": ["1234567890"]}]}
META_ACCTS = {"accounts": [{"id": "act_1234567", "name": "Acme", "currency": "USD", "platform": "meta"}]}


def google_report():
    item = {"key": "a1", "kind": "rsa", "name": "Implants ad", "campaign": "Core", "strength": "POOR", "strengthLabel": "Poor", "rank": 0, "belowGood": True, "impressions": 900, "clicks": 40, "cost": 50, "conversions": 2,
            "gaps": [{"severity": "action", "text": "Add 4 more headlines"}], "nextStep": "Add 4 more headlines", "tips": [], "headlines": [], "descriptions": [], "keywords": [], "keywordCoverage": {"headlines": {"covered": []}}}
    view = {"total": 1, "belowGood": 1, "counts": {"POOR": 1}, "items": [item]}
    return {"account": {"id": "1234567890", "name": "Acme", "currency": "USD", "isGroup": False}, "window": {"days": 30, "from": "2026-09-21", "to": "2026-10-20"}, "generatedAt": "2026-10-21T08:00:00+00:00",
            "warnings": [], "summary": {"reviewed": 1, "belowGood": 1, "impressionsBelowGood": 900, "impressions": 900, "good": 0, "excellent": 0, "unrated": 0}, "search": view,
            "pmax": {"total": 0, "belowGood": 0, "counts": {}, "items": []}}


def meta_report():
    r = M.raw([M.ad("1", name="Spring offer"), M.ad("2"), M.ad("3")], [M.ins("1", spend="500", q="BELOW_AVERAGE_10", freq="6.5"), M.ins("2", q="AVERAGE"), M.ins("3", q="ABOVE_AVERAGE")])
    return json.loads(json.dumps(an.quality(r, {"id": "act_1234567", "name": "Acme <b>Meta</b>", "currency": "USD", "platform": "meta"}, M.TODAY)))


def page(scenario):
    out = subprocess.run(["node", "-e", HARNESS, json.dumps([SCOPE, MC, PAGE]), json.dumps(scenario)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def scenario(steps, google=None, meta=None, search=""):
    g = google if google is not None else {"match": "/adstrength/report?account=1234567890", "body": google_report()}
    m = meta if meta is not None else {"match": "/adstrength/report?account=act_1234567", "body": meta_report()}
    return {"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/gads/accounts", "body": META_ACCTS}, g, m], "steps": steps, "search": search}


def reads(out):
    return [c for c in out["calls"] if "/adstrength/report" in c]


def test_every_element_the_script_reaches_for_is_in_the_template_and_the_scripts_load_in_order():
    html, js = open(TEMPLATE, encoding="utf-8").read(), open(PAGE, encoding="utf-8").read()
    for sel in sorted(set(re.findall(r'\$\("(#[A-Za-z]+)"\)', js))):
        assert 'id="%s"' % sel[1:] in html, sel
    assert html.index("pm_picker.js") < html.index("pm_platform.js") < html.index("pm_scope.js") < html.index("meta_creative.js") < html.index("ad_strength.js")
    assert "—" not in html and "—" not in js and "—" not in open(MC, encoding="utf-8").read()


def test_google_is_read_as_before_and_nothing_is_asked_of_meta():
    out = page(scenario([{"pick": "1234567890"}]))
    assert reads(out) == ["/p2/paid-marketing/api/adstrength/report?account=1234567890"]
    assert "Implants ad" in out["el"]["#list"]["html"] and out["el"]["#out"]["hidden"] is False and out["el"]["#metaOut"]["hidden"] is True


def test_meta_alone_reads_only_the_meta_account_and_draws_the_ad_quality_view():
    out = page(scenario([{"platform": "meta"}, {"pickMeta": "act_1234567"}]))
    assert reads(out) == ["/p2/paid-marketing/api/adstrength/report?account=act_1234567"]
    html = out["el"]["#metaOut"]["html"]
    assert out["el"]["#metaOut"]["hidden"] is False and out["el"]["#out"]["hidden"] is True and out["el"]["#skel"]["hidden"] is True
    assert "Spring offer" in html and "Quality: Below average (bottom 10%)" in html and "seen 6.5 times each" in html and "1 of 3 ranked ads are below average" in html
    assert "Acme &lt;b&gt;Meta&lt;/b&gt;" in html and "<b>Meta</b>" not in html                                   # an account name with markup is text
    assert 'class="mc-sev high"' in html and "What to try:" in html and "Meta Ads</h2>" not in html


def test_both_asks_each_platform_once_and_draws_the_two_one_under_the_other():
    out = page(scenario([{"platform": "both"}, {"pick": "1234567890"}, {"pickMeta": "act_1234567"}]))
    assert sorted(set(reads(out))) == ["/p2/paid-marketing/api/adstrength/report?account=1234567890", "/p2/paid-marketing/api/adstrength/report?account=act_1234567"]
    assert reads(out).count("/p2/paid-marketing/api/adstrength/report?account=act_1234567") == 1                  # Meta is asked once, when the second account completes the choice
    assert "Implants ad" in out["el"]["#list"]["html"] and out["el"]["#out"]["hidden"] is False
    assert "Spring offer" in out["el"]["#metaOut"]["html"] and '<h2 class="mc-title">Meta Ads</h2>' in out["el"]["#metaOut"]["html"] and out["el"]["#metaOut"]["hidden"] is False


def test_a_meta_failure_is_said_under_both_and_google_is_still_shown():
    meta = {"match": "/adstrength/report?account=act_1234567", "status": 502, "body": {"error": "Meta is rate limiting this app right now. Try again in a minute.", "kind": "meta"}}
    out = page(scenario([{"platform": "both"}, {"pick": "1234567890"}, {"pickMeta": "act_1234567"}], meta=meta))
    assert "Implants ad" in out["el"]["#list"]["html"] and out["el"]["#out"]["hidden"] is False
    assert "Meta Ads could not be read" in out["el"]["#metaMsg"]["html"] and "rate limiting" in out["el"]["#metaMsg"]["html"] and out["el"]["#metaOut"]["hidden"] is True


def test_a_google_failure_is_said_under_both_and_meta_is_still_shown():
    google = {"match": "/adstrength/report?account=1234567890", "status": 502, "body": {"error": "Google Ads answered 403"}}
    out = page(scenario([{"platform": "both"}, {"pick": "1234567890"}, {"pickMeta": "act_1234567"}], google=google))
    assert "Spring offer" in out["el"]["#metaOut"]["html"] and out["el"]["#metaOut"]["hidden"] is False
    assert "Google Ads could not be read" in out["el"]["#msg"]["html"] and "403" in out["el"]["#msg"]["html"]


def test_meta_alone_failing_says_so_and_shows_no_stale_google_report():
    meta = {"match": "/adstrength/report?account=act_1234567", "status": 502, "body": {"error": "Meta rejected the access token", "kind": "meta"}}
    out = page(scenario([{"platform": "meta"}, {"pickMeta": "act_1234567"}], meta=meta))
    assert "Meta Ads could not be read" in out["el"]["#metaMsg"]["html"] and "access token" in out["el"]["#metaMsg"]["html"] and out["el"]["#metaOut"]["hidden"] is True


def test_refresh_asks_the_meta_account_again_with_refresh_on():
    out = page(scenario([{"platform": "meta"}, {"pickMeta": "act_1234567"}, {"click": "#refreshBtn"}]))
    assert reads(out)[-1] == "/p2/paid-marketing/api/adstrength/report?account=act_1234567&refresh=1"


def test_the_account_chosen_is_remembered_and_in_the_link():
    out = page(scenario([{"platform": "meta"}, {"pickMeta": "act_1234567"}]))
    assert out["store"]["as_account"] == "act_1234567" and out["hist"][-1] == "/p2/paid-marketing/ad-strength?account=act_1234567"


# ── The renderer, on the real report ─────────────────────────────────────────

def render(expr, **data):
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    code = "const X=require(%s);%sconsole.log(JSON.stringify(%s))" % (json.dumps(MC), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_quality_view_names_what_to_do_for_each_flagged_ad_and_says_when_nothing_needs_work():
    html = render("X.quality(R)", R=meta_report())
    assert html.count('class="mc-row"') >= 1 and "Replace the image or video" in html and "The audience is wearing out" in html and html.count("What to try:") == 2    # one for each of the ad's two problems
    quiet = json.loads(json.dumps(an.quality(M.raw([M.ad("1")], [M.ins("1", q="ABOVE_AVERAGE", e="AVERAGE", v="AVERAGE")]), M.ACCT, M.TODAY)))
    assert "Nothing here needs work." in render("X.quality(R)", R=quiet) and "None of the 1 ranked ads is below average" in render("X.quality(R)", R=quiet)
    unread = json.loads(json.dumps(an.quality(M.raw([M.ad("1")], [], insights_read=False), M.ACCT, M.TODAY)))
    assert "no ad can be judged" in render("X.quality(R)", R=unread)


def test_markup_in_an_ad_or_campaign_name_is_text():
    r = json.loads(json.dumps(an.quality(M.raw([M.ad("1", name="<script>x</script>")], [M.ins("1", q="BELOW_AVERAGE_10")]), M.ACCT, M.TODAY)))
    html = render("X.quality(R)", R=r)
    assert "&lt;script&gt;" in html and "<script>" not in html


def test_the_ranked_tile_does_not_call_a_big_unranked_ad_under_500_impressions():
    r = json.loads(json.dumps(an.quality(M.raw([M.ad("1"), M.ad("2"), M.ad("3")], [M.ins("1", imp="400000", q="AVERAGE"), M.ins("2", imp="300000"), M.ins("3", imp="90")]), M.ACCT, M.TODAY)))
    html = render("X.quality(R)", R=r)
    assert "1 under 500 impressions" in html and "1 with 500 or more that Meta did not rank" in html and "2 under 500 impressions" not in html
    only_big = json.loads(json.dumps(an.quality(M.raw([M.ad("1")], [M.ins("1", imp="400000")]), M.ACCT, M.TODAY)))
    h = render("X.quality(R)", R=only_big)
    assert "1 with 500 or more that Meta did not rank" in h and "under 500 impressions" not in h
