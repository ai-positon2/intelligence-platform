"""The CSV downloads of the paid-marketing pages: a text cell that starts with = + - or @ would run as a formula when the file is opened in Excel (an old broad-match keyword
"+dental +implants", an offer "-20% off", a campaign someone named "=HYPERLINK(...)"). The writer is cut out of each page script and run in Node."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
WRITERS = [("ad_strength", "csv"), ("audience_segments", "csv"), ("asset_coverage", "csv"), ("geo_performance", "csv"), ("segment_analysis", "csv"),
           ("cr_data", "csvCell"), ("sa_data", "csvCell"), ("pacing_desk", "csvCell"), ("ps_data", "csvCell")]


def _function(src, name):
    start = src.index("function %s(" % name)
    depth, i = 0, src.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
        i += 1


def cell(page, name, value):
    src = open(os.path.join(ROOT, "static", "js", page + ".js"), encoding="utf-8").read()
    code = "%s;console.log(JSON.stringify(%s(%s)))" % (_function(src, name), name, json.dumps(value))
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.mark.parametrize("page,name", WRITERS)
def test_a_text_cell_that_starts_like_a_formula_is_written_as_text(page, name):
    assert cell(page, name, "+dental +implants") == "'+dental +implants"
    assert cell(page, name, "=HYPERLINK(\"http://x\")") == '"\'=HYPERLINK(""http://x"")"'
    assert cell(page, name, "-20% off") == "'-20% off" and cell(page, name, "@sum") == "'@sum"


@pytest.mark.parametrize("page,name", WRITERS)
def test_ordinary_text_and_the_quoting_rules_are_unchanged(page, name):
    assert cell(page, name, "Austin, TX") == '"Austin, TX"' and cell(page, name, 'say "hi"') == '"say ""hi"""' and cell(page, name, "plain") == "plain"
    assert cell(page, name, None) == "" and cell(page, name, "") == ""


@pytest.mark.parametrize("page,name", [w for w in WRITERS if w[0] != "ps_data"])
def test_a_negative_number_is_still_a_number(page, name):
    assert cell(page, name, -5) == "-5" and cell(page, name, 12.5) == "12.5"
