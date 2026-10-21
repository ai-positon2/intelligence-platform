"""Seasonality Analysis: static/js/sa_data.js run in Node, and the page script checked against its template.

The page script only draws; which figure a month shows, the rows of the year-over-year chart, the headline totals, how an
index is worded and the CSV all come from sa_data.js, so a wrong figure on a client's account would come from here. The file
is executed, not read. The page script is syntax-checked and every element it reaches for must exist in the template.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "static", "js", "sa_data.js")
PAGE = os.path.join(ROOT, "static", "js", "seasonality_analysis.js")
TEMPLATE = os.path.join(ROOT, "templates", "seasonality_analysis.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def run(expr, **data):
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    code = "const D=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(DATA), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def row(y, m, days=30, cost=300.0, conv=30.0, value=1500.0, clicks=300.0, impr=3000.0, full=True):
    return {"y": y, "m": m, "days": days, "cost": cost, "conv": conv, "value": value, "clicks": clicks, "impr": impr, "full": full}


# ── One figure ─────────────────────────────────────────────────────────────

def test_a_sum_is_shown_per_day_and_a_rate_is_one_total_over_another():
    r = row(2026, 2, days=28, cost=280.0, conv=56.0, value=2800.0, clicks=560.0, impr=5600.0)
    assert run('D.valueOf("cost", r)', r=r) == 10.0 and run('D.valueOf("conv", r)', r=r) == 2.0
    assert run('D.valueOf("cpa", r)', r=r) == 5.0 and run('D.valueOf("roas", r)', r=r) == 10.0
    assert run('D.valueOf("cvr", r)', r=r) == pytest.approx(0.1) and run('D.valueOf("ctr", r)', r=r) == pytest.approx(0.1)
    assert run('D.valueOf("cpc", r)', r=r) == 0.5


def test_a_rate_with_nothing_underneath_is_nothing_not_zero_or_infinity():
    r = row(2026, 2, conv=0.0, value=0.0, clicks=0.0, impr=0.0)
    for k in ("cpa", "cvr", "cpc", "ctr"):
        assert run('D.valueOf(k, r)', k=k, r=r) is None
    assert run('D.valueOf("roas", r)', r=row(2026, 2, cost=0.0)) is None
    assert run('D.valueOf("cost", null)') is None
    assert run('D.fmt("cpa", null, "USD")') == "–"


def test_conversion_value_and_return_on_it_are_offered_only_when_the_account_has_value():
    keys = run('D.available(rows).map(m=>m.key)', rows=[row(2026, 1, value=0.0)])
    assert "value" not in keys and "roas" not in keys and "cpa" in keys and "conv" in keys
    assert "value" in run('D.available(rows).map(m=>m.key)', rows=[row(2026, 1)])


# ── The year-over-year chart ───────────────────────────────────────────────

def test_the_overlay_has_a_line_per_year_and_a_point_per_month():
    rows = [row(2024, m, cost=100.0 * m, days=30) for m in range(1, 13)] + [row(2025, m, cost=200.0 * m, days=30) for m in range(1, 13)]
    o = run('D.overlay(rows, [], "cost", "month")', rows=rows)
    assert o["labels"][:3] == ["Jan", "Feb", "Mar"] and len(o["labels"]) == 12
    assert [y["y"] for y in o["years"]] == [2024, 2025]
    assert o["years"][0]["data"][2] == pytest.approx(10.0) and o["years"][1]["data"][2] == pytest.approx(20.0)


def test_a_month_that_is_not_whole_is_left_out_of_the_overlay_and_a_missing_one_is_null():
    rows = [row(2025, 1, days=30), row(2025, 2, days=6, full=False), row(2025, 4, days=30)]
    d = run('D.overlay(rows, [], "cost", "month").years[0].data', rows=rows)
    assert d[0] == 10.0 and d[1] is None and d[2] is None and d[3] == 10.0


def test_the_weekly_overlay_is_by_iso_week_and_per_day():
    weekly = [{"y": 2025, "w": 1, "start": "2024-12-30", "cost": 70.0, "conv": 7.0, "value": 0, "clicks": 70.0, "impr": 700.0},
              {"y": 2026, "w": 53, "start": "2026-12-28", "cost": 140.0, "conv": 14.0, "value": 0, "clicks": 140.0, "impr": 1400.0}]
    o = run('D.overlay([], weekly, "cost", "week")', weekly=weekly)
    assert len(o["labels"]) == 53 and o["labels"][0] == "1" and o["labels"][52] == "53"
    assert o["years"][0]["data"][0] == 10.0 and o["years"][1]["data"][52] == 20.0


# ── Headline totals ────────────────────────────────────────────────────────

def test_the_trailing_year_and_the_year_before_it_are_whole_months_only():
    rows = [row(2024 + (i // 12), i % 12 + 1, cost=100.0) for i in range(24)]
    rows.append(row(2026, 1, days=10, cost=999.0, full=False))
    t = run("D.trailing(rows)", rows=rows)
    assert t["months"] == 12 and t["cur"]["cost"] == 1200.0 and t["prev"]["cost"] == 1200.0 and t["from"] == "2025-01" and t["to"] == "2025-12"
    young = run("D.trailing(rows)", rows=[row(2025, m, cost=50.0) for m in range(1, 8)])
    assert young["months"] == 7 and young["prev"] is None
    assert run("D.trailing(rows)", rows=[row(2025, 1), row(2025, 2)]) is None
    fifteen = run("D.trailing(rows)", rows=[row(2025 + (i // 12), i % 12 + 1, cost=100.0) for i in range(15)])
    assert fifteen["months"] == 12 and fifteen["prev"] is None          # a year before needs 24 whole months, not 13


def test_change_and_percent_wording():
    assert run("D.change(150, 100)") == 0.5 and run("D.change(5, 0)") is None and run("D.change(5, null)") is None
    assert run("D.pct(0.254)") == "+25%" and run("D.pct(-0.04)") == "-4%" and run("D.pct(0)") == "0%" and run("D.pct(0.054)") == "+5.4%"
    assert run("D.pct(null)") == "–"
    assert run("D.pct(-0.0004)") == "0%" and run("D.pct(0.0004)") == "0%" and run("D.pct(-0.004)") == "-0.4%" and run("D.pct(-0.0551)") == "-5.5%"


# ── Words ──────────────────────────────────────────────────────────────────

def test_how_far_to_trust_an_index_is_said_in_words():
    assert "lead" in run('D.confidence({index: 130, n: 1, agree: 1})')
    assert run('D.confidence({index: 130, n: 3, agree: 3})') == "The same direction in all 3 years."
    assert run('D.confidence({index: 130, n: 3, agree: 2})') == "2 of 3 years agree, so the pattern is mixed."
    assert run('D.confidence({index: null, n: 0, agree: 0})') == ""


def test_peak_and_trough_follow_the_servers_thresholds():
    assert run('[D.tone(110, th), D.tone(109.9, th), D.tone(90, th), D.tone(90.1, th), D.tone(null, th)]', th={"peak": 110, "trough": 90}) == ["peak", "ordinary", "trough", "ordinary", "none"]
    assert run('D.tone(125, {peak: 130, trough: 70})') == "ordinary"


def test_a_high_cost_is_drawn_as_bad_news_and_a_high_conversion_count_as_good():
    green, red = "#34d399", "#fb7185"
    assert run('D.toneColor("peak", "conv")') == green and run('D.toneColor("peak", "cpa")') == red
    assert run('D.toneColor("trough", "cpa")') == green and run('D.toneColor("trough", "conv")') == red
    assert run('D.toneColor("ordinary", "cpa")') == run('D.toneColor("ordinary", "conv")')
    assert run('[D.toneColor("peak", "cost"), D.toneColor("trough", "cost"), D.toneColor("peak", "impr")]') == ["#fbbf24"] * 3      # no good or bad in spend


def test_the_budget_wording_and_every_verdict_the_server_can_send_has_a_label():
    assert run("D.budgetText(20)") == "+20% on an average month's daily budget" and run("D.budgetText(-15)").startswith("-15%") and run("D.budgetText(0)") == "No change"
    for code in ("peak", "peak_costly", "typical", "quiet", "quiet_costly", "unknown"):
        assert run("D.VERDICT[c].label", c=code)
    assert run('D.windowText("2026-11-26", "2026-11-30")') == "Nov 26 to Nov 30" and run('D.windowText("2026-07-04", "2026-07-04")') == "Jul 4"
    assert run('D.monthsText("2023-10-01", "2026-10-15")') == "Oct 2023 to Oct 2026" and run('D.monthsText("2026-10-01", "2026-10-15")') == "Oct 2026"


# ── CSV ────────────────────────────────────────────────────────────────────

def test_the_csv_has_the_months_then_the_indexes_and_quotes_what_needs_quoting():
    idx = {k: [{"m": m, "index": 100.0 + m, "n": 2} for m in range(1, 13)] for k in ("conv", "cost", "cpa", "cvr")}
    rep = {"monthly": [row(2026, 1, cost=300.0, conv=30.0, value=1500.0)], "index": idx}
    out = run("D.csv(rep)", rep=rep).split("\r\n")
    assert out[0].startswith("Month,Days,Spend") and out[1] == "2026-01,30,300,3000,300,30,1500,10,5"
    assert "Calendar month,Conversions index" in out[3] and out[4] == "January,101,101,101,101,2" and out[15].startswith("December,112")
    assert run('D.csv({monthly: [], index: null})', ) == "Month,Days,Spend,Impressions,Clicks,Conversions,Conversion value,Cost per conversion,Return on ad spend"


def test_a_cell_with_a_comma_or_a_quote_is_quoted():
    assert run('D.csv({monthly: [], index: null}).length') > 0
    assert run('(function(){const f=D.csv; return f({monthly:[{y:2026,m:1,days:1,cost:1,impr:1,clicks:1,conv:0,value:0}], index:null}).split("\\r\\n")[1];})()') == "2026-01,1,1,1,1,0,0,,"


# ── The page script and its template ───────────────────────────────────────

def test_the_page_script_is_valid_javascript():
    for f in (PAGE, DATA):
        r = subprocess.run(["node", "--check", f], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr


def test_every_element_the_page_script_reaches_for_is_in_the_template():
    js, html = open(PAGE).read(), open(TEMPLATE).read()
    made = set(re.findall(r'id="([A-Za-z0-9_-]+)"', js))               # ids the script writes into its own markup
    wanted = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', js)) | set(re.findall(r"\$\$\(\"#([A-Za-z0-9_-]+)", js))
    in_html = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html))
    missing = wanted - in_html - made - {"psToast"}
    assert not missing, missing


def test_the_page_loads_its_scripts_in_dependency_order_and_with_a_version():
    html = open(TEMPLATE).read()
    order = [html.index(n) for n in ("chart.umd.js", "pm_picker.js", "pm_platform.js", "ps_data.js", "sa_data.js", "seasonality_analysis.js")]
    assert order == sorted(order)
    assert "sa_data.js') }}?v=" in html and "seasonality_analysis.js') }}?v=" in html and "seasonality_analysis.css') }}?v=" in html


def test_no_page_copy_uses_an_em_dash():
    for f in (PAGE, DATA, TEMPLATE):
        assert "—" not in open(f).read(), f


def test_the_current_part_month_is_not_laid_against_whole_months():
    """17 days of October at 10 a day is not a collapse against last year's whole October: the partial month is not drawn at all."""
    rows = [row(2025, 10, days=31, cost=3100.0), row(2026, 10, days=17, cost=170.0, full=False)]
    years = run('D.overlay(rows, [], "cost", "month").years', rows=rows)
    assert years == [{"y": 2025, "data": [None] * 9 + [100.0, None, None]}]


def test_a_missing_figure_is_not_a_change_of_minus_one_hundred_percent():
    assert run("D.change(null, 100)") is None and run("D.change(0, 100)") == -1                # no figure is not the same as none of it


# ── The plan under a month ahead, run from the page script ────────────────

def _page_fn(expr, **data):
    """monthPlanHtml cut out of the page script (an IIFE that needs a browser) and run with the page's own escaping."""
    code = ("const fs=require('fs');const src=fs.readFileSync(%s,'utf8');"
            "const a=src.indexOf('/* What the change comes to in dollars'), b=src.indexOf('function paintAhead(){');"
            "if(a<0||b<a) throw new Error('functions not found');"
            "const esc=function(s){return String(s==null?'':s).replace(/[&<>\"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c];});};"
            "const f=new Function('esc',src.slice(a,b)+';return {monthPlanHtml:monthPlanHtml};')(esc);" % json.dumps(PAGE))
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    r = subprocess.run(["node", "-e", code + pre + "console.log(JSON.stringify(" + expr + "));"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


PLAN = {"steps": ["Open Campaigns.", "Raise <budgets>."], "worth": "About $260 a day.", "note": None,
        "events": [{"name": "Black Friday", "eligible": True, "modifier": 20, "steps": ["Open the tool.", "Set +20%."]},
                   {"name": "Back to school", "eligible": False, "why": "It runs 28 days."},
                   {"name": "No reason", "eligible": False}]}


def test_the_worth_is_shown_and_the_steps_and_each_adjustment_are_folded_away():
    html = _page_fn("f.monthPlanHtml(P)", P=PLAN)
    assert html.startswith('<p class="sa-worth"><b>Worth</b> About $260 a day.</p>')
    assert '<details class="sa-plan"><summary>How to make the change</summary><ol><li>Open Campaigns.</li><li>Raise &lt;budgets&gt;.</li></ol></details>' in html
    assert '<details class="sa-plan"><summary>Seasonality adjustment for Black Friday (+20%)</summary><ol><li>Open the tool.</li><li>Set +20%.</li></ol></details>' in html
    assert '<p class="sa-small"><b>Back to school.</b> It runs 28 days.</p>' in html and "No reason" not in html


def test_a_note_replaces_the_steps_and_no_plan_draws_nothing():
    html = _page_fn("f.monthPlanHtml(P)", P={"steps": [], "worth": None, "note": "Spend already follows demand.", "events": []})
    assert html == '<p class="sa-small">Spend already follows demand.</p>'
    assert _page_fn("f.monthPlanHtml(P)", P=None) == ""
    assert _page_fn("f.monthPlanHtml(P)", P={"steps": [], "events": []}) == ""
    assert _page_fn("f.monthPlanHtml(P)", P={"steps": ["One."], "events": [{"name": "X", "eligible": True, "modifier": -15, "steps": ["Two."]}]}).count("(-15%)") == 1


def test_each_month_card_draws_its_plan_after_the_budget_line():
    src = open(PAGE, encoding="utf-8").read()
    assert "esc(D.budgetText(m.budget))+'</b></p>'+monthPlanHtml(m.plan)+" in src
