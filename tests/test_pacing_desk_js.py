"""Pacing Desk: the portfolio figures in static/js/pacing_desk.js, run in Node (the functions are cut out of the page script and executed, not read)."""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESK = os.path.join(ROOT, "static", "js", "pacing_desk.js")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def _function(src, name):
    """The source of `function name(...) {...}`, up to its closing brace."""
    start = src.index("function %s(" % name)
    depth, i = 0, src.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
        i += 1


def run(expr, **data):
    src = open(DESK, encoding="utf-8").read()
    prelude = re.search(r"var sum = .*?;\n", src).group(0) + "".join(_function(src, n) + "\n" for n in ("cpaOf", "orphanSplit"))
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    r = subprocess.run(["node", "-e", "%s%s;console.log(JSON.stringify(%s))" % (prelude, pre, expr)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_the_cost_per_conversion_counts_the_spend_of_every_account_whose_conversions_it_counts():
    # Shop's allocation is unclear, so the "Spent to date" tile leaves it out; its $1,500 and its 60 conversions are still real
    A = [{"spend": 1500, "conv": 15, "ambiguous": False}, {"spend": 1500, "conv": 60, "ambiguous": True}]
    assert run("cpaOf(A)", A=A) == 40


def test_no_conversions_means_no_cost_per_conversion():
    assert run("cpaOf(A)", A=[{"spend": 500, "conv": 0}]) is None


def test_a_budget_with_no_data_is_not_reported_as_money_spent():
    ws = ["Dormant Co / 'Dormant' has a $5,000 budget but no campaign data.", "'Acme West' spent $1,250 MTD but has no Search budget row for this month in Allocations."]
    out = run("orphanSplit(W)", W=ws)
    assert out["total"] == 1250 and out["spent"] == [ws[1]] and out["unread"] == [ws[0]]


def test_no_warnings_is_an_empty_split():
    assert run("orphanSplit(W)", W=None) == {"spent": [], "unread": [], "total": 0}


def test_the_page_says_when_a_run_was_read_from_meta_too_and_marks_a_meta_account():
    page = open(DESK, encoding="utf-8").read()
    assert 'src.channels.indexOf("Meta")>=0' in page and "Google Ads'+(withMeta?' and Meta Ads':'')" in page
    assert 'a.channel==="Meta"?\'<span class="pm-tag pd-meta"' in page
    assert 'a.channel||a.channels||"Google"' in page                                              # the CSV and the account detail say the channel
    assert "This desk paces Google Ads and Meta Ads only" in page and "This desk reads Google Ads only" not in page
    html = open(os.path.join(ROOT, "templates", "pacing_desk.html"), encoding="utf-8").read()
    assert "Google Ads and Meta Ads" in html and "straight from Google Ads and Meta Ads" in html
