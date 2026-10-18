"""Audience Segment Analysis: the platform-dependent parts of static/js/audience_segments.js (cut out of the page script and run in Node) and the page's markup."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "static", "js", "audience_segments.js")
TEMPLATE = os.path.join(ROOT, "templates", "audience_segments.html")
node = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def _function(src, name):
    start = src.index("function %s(" % name)
    depth, i = 0, src.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
        i += 1


def run(expr, report=None):
    src = open(PAGE, encoding="utf-8").read()
    prelude = 'var MODES={observation:"Observation",targeting:"Targeting",mixed:"Mixed"};var S={report:%s};' % json.dumps(report) + "".join(
        _function(src, n) + "\n" for n in ("modeText", "platName", "showBid"))
    r = subprocess.run(["node", "-e", "%s;console.log(JSON.stringify(%s))" % (prelude, expr)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@node
def test_an_advantage_audience_use_is_called_a_suggestion_only_on_meta():
    assert run('[modeText("observation","meta"),modeText("observation","google"),modeText("targeting","meta"),modeText("mixed",undefined)]') == \
        ["Suggestion", "Observation", "Targeting", "Mixed"]


@node
def test_the_platform_is_named_in_full():
    assert run('["google","meta","both",undefined].map(platName)') == ["Google Ads", "Meta Ads", "Google Ads and Meta Ads", "Google Ads"]


@node
def test_the_bid_columns_are_shown_unless_the_report_is_all_meta():
    for platform, want in (("google", True), ("both", True), ("meta", False)):
        assert run("showBid()", {"account": {"platform": platform}}) is want, platform
    assert run("showBid()", None) is False


def test_the_page_has_the_platform_switch_and_both_pickers_and_loads_their_script_first():
    html = open(TEMPLATE, encoding="utf-8").read()
    for el in ('id="platPick"', 'id="acctPick"', 'id="metaPick"'):
        assert el in html, el
    assert html.index("pm_picker.js") < html.index("pm_platform.js") < html.index("audience_segments.js")
    assert "Meta Ads" in html and "ad sets aimed at it" in html or "numbers are those of the ad sets aimed at it" in html


def test_the_page_script_asks_for_meta_accounts_and_sends_a_combined_account_string():
    page = open(PAGE, encoding="utf-8").read()
    assert "/p2/paid-marketing/api/gads/accounts?platform=meta" in page and "S.g+\"+\"+S.m" in page
    assert 'PL.switcher($("#platPick"),{allow:["google","meta","both"]' in page
