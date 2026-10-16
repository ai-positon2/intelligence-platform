"""Search Query Analysis: the page script checked against its template and the rest of the wiring.

The rules and the arithmetic are in sqr_core.js (tests/test_sqr_core_js.py runs them); the page script only wires controls and draws. It is
syntax-checked, every element it reaches for must exist in the template, the scripts load in the order they depend on, the page needs no
export to start (Google Ads is read), and none of the copy has an em dash in it.
"""
import os
import re
import shutil
import subprocess
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
JS = os.path.join(ROOT, "static", "js")
PAGE, CORE = os.path.join(JS, "sqr_agent.js"), os.path.join(JS, "sqr_core.js")
TEMPLATE = os.path.join(ROOT, "templates", "sqr_agent.html")
FILES = [PAGE, CORE, TEMPLATE, os.path.join(ROOT, "tracker", "paid_sqr_data.py"), os.path.join(ROOT, "tracker", "paid_sqr.py")]


def read(p):
    return open(p, encoding="utf-8").read()


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
@pytest.mark.parametrize("path", [PAGE, CORE])
def test_the_scripts_parse(path):
    r = subprocess.run(["node", "--check", path], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr


def test_every_element_the_page_reaches_for_is_in_the_template():
    html, js = read(TEMPLATE), read(PAGE)
    ids = set(re.findall(r'\$id\("([A-Za-z0-9_\-]+)"\)', js)) | set(re.findall(r'getElementById\("([A-Za-z0-9_\-]+)"\)', js))
    ids |= {"file-" + k for k in ("terms", "keywords", "negs")} | {"drop-" + k for k in ("terms", "keywords", "negs")} | {"file-" + k + "-name" for k in ("terms", "keywords", "negs")}
    made_by_the_page = {"sqToast", "pane-negs", "pane-check", "pane-kws", "pane-review", "pane-all", "panes"}
    missing = sorted(i for i in ids if i not in made_by_the_page and 'id="%s"' % i not in html)
    assert missing == []


def test_the_scripts_load_in_the_order_they_depend_on():
    html = read(TEMPLATE)
    order = [html.index(n) for n in ("pm_picker.js", "pm_platform.js", "ps_data.js", "sqr_core.js", "sqr_agent.js")]
    assert order == sorted(order)
    assert "?v=" in html[html.index("sqr_core.js"):html.index("sqr_core.js") + 80]


def test_the_page_reads_google_ads_and_keeps_the_csv_as_a_fallback():
    html, js = read(TEMPLATE), read(PAGE)
    assert 'id="acctPick"' in html and "/sqr/data?account=" in js and 'allow: ["google"]' in js
    assert 'id="csvFallback"' in html and "Can't read Google Ads?" in html
    assert 'class="req">Required</span>' in html          # the search terms CSV is required only inside the fallback
    assert ".get(\"account\")" in js                        # an old link names a ruleset, a new one a Google Ads account


def test_the_page_never_asks_the_model_anything_but_the_ruleset_account_profile_and_terms():
    js = read(PAGE)
    assert "JSON.stringify({ ruleset: rulesetId, account: account, terms: batch, profile: profile || undefined })" in js


def test_the_page_opens_for_staff_and_the_scripts_are_served():
    import app as appmod
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "someone@position2.com", "name": "T"}
    page = c.get("/p2/paid-marketing/search-query-analysis")
    assert page.status_code == 200 and b"sqr_core.js" in page.data and b"sqr_agent.js" in page.data
    assert c.get("/static/js/sqr_core.js").status_code == 200


def test_the_open_account_list_stacks_above_the_panels_under_it():
    # The account list opens inside .ps-cmd. The rules bar below it (.sq-bar) is z-index 5 and later in the page, so an equal z-index
    # painted the bar over the open list. .ps-cmd must be higher on this page, and the CSS the template loads is the one that says so.
    css = read(os.path.join(ROOT, "static", "css", "paid_marketing.css"))
    ps = read(os.path.join(ROOT, "static", "css", "performance_summarizer.css"))
    cmd = re.search(r"\.pm-sqr \.ps-cmd\{[^}]*z-index:(\d+)", css)
    bar = re.search(r"\.sq-bar\{[^}]*z-index:(\d+)", css)
    assert cmd and bar, "the stacking rules are missing"
    assert int(cmd.group(1)) > int(bar.group(1))
    assert re.search(r"\.ps-cmd\{[^}]*z-index:\d+", ps), "the base panel no longer sets a z-index the page override is raising"
    base = read(os.path.join(ROOT, "templates", "_paid_base.html"))
    assert re.search(r"paid_marketing\.css'\) \}\}\?v=(\d+)", base), "the stylesheet is loaded without a version"


def test_there_is_no_em_dash_in_any_of_it():
    for p in FILES:
        assert "—" not in read(p), p


def test_the_rules_list_says_it_is_not_a_list_of_accounts_and_nothing_is_called_partial():
    html, js = read(TEMPLATE), read(PAGE)
    assert "The rules are not a list of accounts" in html and "Automatic</b> works for every Google Ads account" in html
    assert "Partial" not in js and "Model only" not in js and "placeholder" not in js.lower()
    assert 'Automatic</span>' in js and 'Client rulebook</span>' in js


def test_automatic_sends_the_account_profile_and_an_empty_range_offers_a_longer_one():
    js = read(PAGE)
    assert "Core.autoRuleset({ name: inp.account, keywords: inp.keywords })" in js and "const profile = auto ? accountProfile(inp) : null;" in js
    assert "0.32 + 0.6 * d / n), profile);" in js                          # and it is what the batches are sent with
    assert 'id="tryLonger"' in js and 'S.preset = "last90"' in js
