"""Geo Performance: the wording in static/js/geo_performance.js that depends on the platform, with the functions cut out of the page script and run in Node."""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "static", "js", "geo_performance.js")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def _function(src, name):
    start = src.index("function %s(" % name)
    depth, i = 0, src.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
        i += 1


def run(expr, platform):
    src = open(PAGE, encoding="utf-8").read()
    cols = re.search(r"var COLS = .*?\];\n", src, re.S).group(0)
    prelude = "var S={report:{account:{platform:%s}}};" % json.dumps(platform) + _function(src, "budgetWords") + cols + _function(src, "colLabel")
    r = subprocess.run(["node", "-e", "%s;console.log(JSON.stringify(%s))" % (prelude, expr)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("platform,want", [("meta", "Suggested budget"), ("google", "Suggested bid"), ("both", "Suggested bid")])
def test_the_suggestion_column_is_named_for_what_the_platform_can_do(platform, want):
    assert run("COLS.map(colLabel).slice(-1)[0]", platform) == want


def test_only_meta_gets_budget_wording_and_the_other_columns_are_untouched():
    assert [run("budgetWords()", p) for p in ("meta", "google", "both")] == [True, False, False]
    assert run("COLS.map(colLabel).slice(0,3)", "meta") == ["Place", "Spend", "% spend"]


def test_the_page_words_its_tags_and_notes_by_platform():
    page = open(PAGE, encoding="utf-8").read()
    assert 'push:budgetWords()?"More budget":"Raise bids"' in page and "Meta's region report" in page
    assert '(budgetWords()?"budget change":"bid adjustment")' in page and "esc(colLabel(c))" in page
