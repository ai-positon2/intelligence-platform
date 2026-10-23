"""Client Feedback Agent page: the Platform switch changes what the model is told to look for, and Both adds nothing (the line is executed in Node)."""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "static", "js", "client_feedback_agent.js")
TEMPLATE = os.path.join(ROOT, "templates", "client_feedback_agent.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def _function(src, name):
    start = src.index("function %s(" % name)
    depth, i = 0, src.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
        i += 1


def run(expr, client="Acme", when="any", plat="both"):
    src = open(PAGE, encoding="utf-8").read()
    prelude = "".join(re.search(r"var %s *=.*?;\n" % v, src, re.S).group(0) for v in ("PLAT_TEXT", "PLAT_CHIP"))
    prelude += "const esc=function(s){return String(s)};const hueOf=function(){return 1};const inits=function(s){return s[0]};const WHEN={week:{label:'This week',days:7},any:{label:'Any time'}};const iso=function(){return '2026-10-22'};"
    prelude += "var client=%s,when=%s,plat=%s;" % (json.dumps(client), json.dumps(when), json.dumps(plat))
    prelude += "".join(_function(src, n) + "\n" for n in ("contextLine", "chipsHtml"))
    r = subprocess.run(["node", "-e", "%sconsole.log(JSON.stringify(%s))" % (prelude, expr)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_both_adds_nothing_to_what_the_model_reads():
    assert run("contextLine()", plat="both") == "[Context for this question: client: Acme]"
    assert run("contextLine()", client="", plat="both") == ""


def test_meta_tells_the_model_to_leave_google_out_and_google_the_reverse():
    meta, google = run("contextLine()", plat="meta"), run("contextLine()", plat="google")
    assert "platform: Meta Ads only" in meta and "leave out Google Ads" in meta and "Google Ads only" not in meta
    assert "platform: Google Ads only" in google and "leave out Meta" in google


def test_the_platform_alone_is_enough_context_and_sits_with_the_client_and_timeframe():
    assert run("contextLine()", client="", plat="meta").startswith("[Context for this question: platform: Meta Ads only")
    full = run("contextLine()", when="week", plat="google")
    assert full.index("client: Acme") < full.index("platform:") < full.index("timeframe:")


def test_the_question_shows_a_chip_for_the_platform_but_not_for_both():
    assert "Meta Ads" in run("chipsHtml('', 'any', 'meta')") and "Google Ads" in run("chipsHtml('', 'any', 'google')")
    assert run("chipsHtml('', 'any', 'both')") == "" and run("chipsHtml('', 'any')") == ""


def test_the_page_has_the_switch_and_declares_the_platform_before_the_switch_reads_it():
    page, tpl = open(PAGE, encoding="utf-8").read(), open(TEMPLATE, encoding="utf-8").read()
    assert "PMPlatform.switcher" in page and 'id="platSw"' in tpl and "pm_platform.js" in tpl
    assert page.index('var plat="both";') < page.index("PMPlatform.switcher")      # declared after the switch ran it would reset the saved choice
    assert "addUser(text, client, when, plat)" in page
