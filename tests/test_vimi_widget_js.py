"""The Vimi widget's own code, executed in Node: answers render as markdown without ever becoming markup, a table inside prose keeps its prose, a question is started and polled with its steps shown, and a failed or missing analyst falls back or says so."""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "templates", "ppc_chat_widget.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def _function(src, name):
    start = src.index("function %s(" % name)
    depth, i = 0, src.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
        i += 1


SRC = open(TEMPLATE, encoding="utf-8").read()
FUNCS = ("esc", "citeify", "fmt", "mdInline", "mdCells", "mdBlock", "maybeTable", "askVimi", "legacyChat", "pollJob", "sourceRow")


def node(script, timeout=30):
    prelude = "var _abort=false,_jobId='';var setTimeout=function(f){f()};\n" + "".join(_function(SRC, n) + "\n" for n in FUNCS)
    r = subprocess.run(["node", "-e", prelude + script], capture_output=True, text=True, timeout=timeout)
    assert r.returncode == 0, r.stderr
    return r.stdout


def render(text):
    return node("process.stdout.write(fmt(citeify(esc(%s))))" % json.dumps(text))


# ── markdown ──────────────────────────────────────────────────────────────────

def test_bold_bullets_numbers_headings_and_paragraphs():
    html = render("### Summary\nSpend is **up 12%**.\n\n- first\n- second\n\n1. one\n2. two")
    assert '<h4 class="k-h">Summary</h4>' in html and "<strong>up 12%</strong>" in html
    assert "<ul><li>first</li><li>second</li></ul>" in html and "<ol><li>one</li><li>two</li></ol>" in html


def test_a_table_inside_prose_renders_as_a_table_and_keeps_its_prose():
    html = render("Here is the split:\n\n| Campaign | Cost |\n|---|---|\n| Brand | **$100** |\n| Generic | $300 |\n\nGeneric is the problem.")
    assert "<p>Here is the split:</p>" in html and "<p>Generic is the problem.</p>" in html
    assert "<th>Campaign</th><th>Cost</th>" in html and "<td>Brand</td><td><strong>$100</strong></td>" in html and html.count("<tr>") == 3


def test_a_ragged_table_row_is_padded_not_dropped():
    html = render("| a | b | c |\n|---|---|---|\n| 1 | 2 |")
    assert "<td>1</td><td>2</td><td></td>" in html


def test_italics_do_not_eat_snake_case_or_star_bullets_or_multiplication():
    html = render("The *key* metric is cost_per_conversion, 2*3 = 6.\n* a bullet")
    assert "<em>key</em>" in html and "cost_per_conversion" in html and "2*3 = 6" in html and "<li>a bullet</li>" in html


def test_inline_code_and_fenced_code_are_not_interpreted():
    html = render("Use `account` here.\n```\n**not bold** | a | b |\n```")
    assert "<code>account</code>" in html and '<pre class="k-code">' in html and "<strong>not bold</strong>" not in html


def test_links_are_made_only_for_web_and_platform_addresses():
    html = render("[page](/p2/paid-marketing/kpi-monitoring) [site](https://example.com/x?a=1&b=2) [bad](javascript:alert(1))")
    assert 'href="/p2/paid-marketing/kpi-monitoring"' in html and 'href="https://example.com/x?a=1&amp;b=2"' in html and 'href="javascript' not in html
    assert "[bad](javascript:alert(1))" in html


def test_nothing_in_an_answer_can_become_markup():
    for hostile in ('<img src=x onerror=alert(1)>', '<script>alert(1)</script>', '| <b>x</b> | y |\n|---|---|\n| <i>1</i> | 2 |', '[<img src=x onerror=1>](/p2/x)', '**<u>x</u>**'):
        html = render(hostile)
        assert not re.search(r"<(img|script|b|i|u)[ >]", html), (hostile, html)


def test_the_cite_chips_and_the_vimi_card_block_still_work():
    assert 'class="k-cite"' in render("Seen in [ABM Signal Tracker] data.")
    assert "vimi-card" not in render('Dossier\n```vimi-card {"company":"A"}```')


# ── a message that is only a table still gets the rich table; one with prose does not lose it ──

def maybe(text):
    out = node("var bub={innerHTML:'kept'},got=null;var renderTable=function(b,rows){got=rows;};maybeTable(bub,%s);process.stdout.write(JSON.stringify({got:got,html:bub.innerHTML}))" % json.dumps(text))
    return json.loads(out)


def test_a_message_that_is_only_a_table_becomes_the_rich_table():
    assert maybe("| a | b |\n|---|---|\n| 1 | 2 |")["got"] == [["a", "b"], ["1", "2"]]


def test_a_table_with_prose_around_it_is_left_alone_so_the_prose_survives():
    got = maybe("Here is the split:\n| a | b |\n|---|---|\n| 1 | 2 |\nThat is it.")
    assert got["got"] is None and got["html"] == "kept"


# ── the job: start, poll, steps, fallback ─────────────────────────────────────

def run_ask(responses, payload=None):
    """responses: a list of [status, json] answered in order to every fetch. Returns what askVimi resolved with, the fetched urls, the painted steps."""
    script = """
    var R=%s, urls=[], painted=[];
    var fetch=function(u,o){ urls.push(u); var x=R.shift(); if(x==='throw'){ return Promise.reject(new Error('net')); }
      return Promise.resolve({ok:x[0]>=200&&x[0]<300,status:x[0],json:function(){ return Promise.resolve(x[1]); }}); };
    var paintSteps=function(row,steps){ painted.push(steps.map(function(s){return s.label;})); };
    askVimi(%s,{}).then(function(d){ process.stdout.write(JSON.stringify({d:d,urls:urls,painted:painted,job:_jobId})); },
                        function(e){ process.stdout.write(JSON.stringify({err:String(e.message),urls:urls})); });
    """ % (json.dumps(responses), json.dumps(payload or {"message": "hi"}))
    return json.loads(node(script))


def test_a_question_is_started_then_polled_until_done_and_its_steps_and_sources_come_back():
    out = run_ask([[202, {"job_id": "j1"}],
                   [200, {"status": "running", "steps": [{"label": "Finding the account"}]}],
                   [200, {"status": "running", "steps": [{"label": "Finding the account"}, {"label": "Reading KPI Monitoring"}]}],
                   [200, {"status": "done", "steps": [], "answer": "It is fine.", "sources": [{"agent": "KPI Monitoring"}]}]])
    assert out["d"] == {"answer": "It is fine.", "sources": [{"agent": "KPI Monitoring"}]} and out["job"] == "j1"
    assert out["urls"] == ["/api/vimi/ask", "/api/vimi/jobs/j1", "/api/vimi/jobs/j1", "/api/vimi/jobs/j1"]
    assert out["painted"][1] == ["Finding the account", "Reading KPI Monitoring"]


def test_a_failed_job_is_shown_as_a_warning_and_a_stopped_one_is_not_shown_at_all():
    assert run_ask([[202, {"job_id": "j"}], [200, {"status": "failed", "error": "Boom"}]])["d"] == {"answer": "⚠️ Boom"}
    assert run_ask([[202, {"job_id": "j"}], [200, {"status": "failed", "error": "Stopped."}]])["d"] == {"stopped": True}


def test_a_missing_analyst_or_a_server_error_falls_back_to_the_older_route_with_the_same_message():
    out = run_ask([[503, {"fallback": True, "error": "not configured"}], [200, {"answer": "old answer"}]], {"message": "hi", "page": "/p2/hub"})
    assert out["d"] == {"answer": "old answer"} and out["urls"] == ["/api/vimi/ask", "/api/ppc-chat"]
    assert run_ask([[500, {}], [200, {"answer": "old"}]])["urls"] == ["/api/vimi/ask", "/api/ppc-chat"]


def test_a_refusal_such_as_one_question_at_a_time_is_said_and_not_retried_elsewhere():
    out = run_ask([[429, {"error": "Your previous question is still being answered."}]])
    assert out["d"] == {"answer": "⚠️ Your previous question is still being answered."} and out["urls"] == ["/api/vimi/ask"]


def test_polling_survives_a_few_dropped_requests_but_gives_up_after_many():
    out = run_ask([[202, {"job_id": "j"}], "throw", "throw", [200, {"status": "done", "answer": "ok", "sources": []}]])
    assert out["d"]["answer"] == "ok"
    assert "poll" in run_ask([[202, {"job_id": "j"}]] + ["throw"] * 8)["err"]


def test_pressing_stop_ends_the_poll_without_an_answer():
    script = """
    var polls=0;_abort=false;
    var fetch=function(u){ polls++; if(polls===2){ _abort=true; } return Promise.resolve({ok:true,status:200,json:function(){return Promise.resolve({status:'running',steps:[]});}}); };
    var paintSteps=function(){};
    pollJob('j',{}).then(function(d){ process.stdout.write(JSON.stringify(d)); });
    """
    assert json.loads(node(script)) == {"stopped": True}


def test_the_stop_button_also_cancels_the_job_on_the_server():
    line = re.search(r"window\.kStop = function\(\)\{.*?\};", SRC, re.S).group(0)
    script = "var window={},setBusy=function(){},_abort=false,_jobId='abc',calls=[];var fetch=function(u,o){calls.push([u,o.method]);};" + line + "window.kStop();process.stdout.write(JSON.stringify({calls:calls,abort:_abort}))"
    out = json.loads(node(script))
    assert out == {"calls": [["/api/vimi/jobs/abc/cancel", "POST"]], "abort": True}


def test_the_sources_row_names_each_agent_and_account_and_marks_what_could_not_be_read():
    script = "var document={createElement:function(){return {};}};process.stdout.write(sourceRow([{agent:'KPI Monitoring',account:'Beta (1)',fetchedAt:'2026-10-30T09:00Z'},{agent:'Pacing <b>',failed:true,error:'down'}]).innerHTML)"
    html = node(script)
    assert "KPI Monitoring · Beta (1)" in html and "read 2026-10-30T09:00Z" in html
    assert 'class="k-src failed"' in html and "Pacing &lt;b&gt;" in html and "<b>" not in html
    assert node("process.stdout.write(String(sourceRow([])))") == "null"


def test_the_widget_as_the_server_renders_it_is_well_formed_javascript():
    import app as appmod
    with appmod.app.test_request_context("/p2/hub"):
        from flask import render_template
        html = render_template("ppc_chat_widget.html", user={"email": "ana@position2.com", "name": "Ana"})
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.S)
    assert scripts and "askVimi" in html
    for body in scripts:
        r = subprocess.run(["node", "--check", "-"], input=body, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
