"""Pacing Desk: static/js/pacing_budgets.js (the Allocations editor) run in Node, and both page scripts checked against their template.

The server checks every row and decides what is saved (tests/test_paid_pacing_budgets.py, test_paid_pacing_routes.py). The page only edits and merges, so how pasted rows
join the table, how a number is read out of a cell and how the total is added up come from the script: it is executed here, not read.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
BUDGETS = os.path.join(JS, "pacing_budgets.js")
DESK = os.path.join(JS, "pacing_desk.js")
TEMPLATE = os.path.join(ROOT, "templates", "pacing_desk.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def run(expr, **data):
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    code = "const M=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(BUDGETS), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def row(client="Acme", account="Acme Dental", allocated="3000", target="", days="", status="", am=""):
    return {"client": client, "account": account, "allocated": allocated, "targetCpa": target, "daysRunning": days, "status": status, "am": am}


# ── Reading a cell ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("cell,want", [("3000", 3000), ("$3,000.50", 3000.5), (" 1 200 ", 1200), ("", None), (None, None), (0, 0)])
def test_a_money_cell_is_read_as_a_number_or_nothing(cell, want):
    assert run("M.num(C)", C=cell) == want


def test_text_in_a_money_cell_is_not_a_number():
    assert run("Number.isNaN(M.num('lots'))") is True


def test_the_month_is_named_in_words():
    assert run("[M.monthName('2026-10'), M.monthName('2027-01'), M.monthName('2026-12'), M.monthName('')]") == ["October 2026", "January 2027", "December 2026", ""]


def test_the_total_counts_only_real_numbers():
    assert run("M.totalOf(R)", R=[row(allocated="1,000"), row(allocated=""), row(allocated="oops"), row(allocated="$250.5")]) == 1250.5
    assert run("M.totalOf([])") == 0


def test_two_rows_are_the_same_one_ignoring_case_and_spacing():
    assert run("M.key(' Acme ', 'Acme   Dental') === M.key('acme', 'ACME DENTAL')") is True
    assert run("M.key('A', 'B') === M.key('B', 'A')") is False


# ── Pasting ─────────────────────────────────────────────────────────────────

def pasted(account, allocated, client="Acme", target=None, days="", status="Active"):
    return {"client": client, "account": account, "allocated": allocated, "targetCpa": target, "daysRunning": days, "status": status}


def test_replacing_makes_the_pasted_rows_the_whole_table():
    got = run("M.merge(C, P, true)", C=[row(account="Old")], P=[pasted("New", 5.5, target=45, days="Monday - Friday")])
    assert got == [{"client": "Acme", "account": "New", "allocated": 5.5, "targetCpa": 45, "daysRunning": "Monday - Friday", "status": "Active", "am": ""}]


def test_a_missing_target_becomes_an_empty_cell_not_the_word_null():
    assert run("M.merge([], P, true)[0].targetCpa", P=[pasted("A", 1)]) == ""


def test_merging_updates_matching_rows_in_place_and_adds_the_rest():
    current = [row(account="A", allocated="1"), row(account="B", allocated="2", am="Sam")]
    got = run("M.merge(C, P, false)", C=current, P=[pasted("b", 20), pasted("C", 30)])
    assert [(r["account"], r["allocated"]) for r in got] == [("A", "1"), ("b", 20), ("C", 30)]
    assert got[1]["am"] == ""                                       # a matched row is replaced by the pasted one, not blended with it


def test_merging_the_same_account_twice_in_one_paste_does_not_add_it_twice():
    got = run("M.merge([], P, false)", P=[pasted("A", 1), pasted("a", 2)])
    assert [(r["account"], r["allocated"]) for r in got] == [("a", 2)]


def test_merging_leaves_the_table_it_was_given_alone():
    current = [row(account="A", allocated="1")]
    run("M.merge(C, P, false)", C=current, P=[pasted("A", 9), pasted("B", 9)])
    assert current == [row(account="A", allocated="1")]


# ── The page scripts and the template ───────────────────────────────────────

@pytest.mark.parametrize("path", [BUDGETS, DESK])
def test_the_scripts_are_valid_javascript(path):
    r = subprocess.run(["node", "--check", path], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr


def test_every_element_the_scripts_reach_for_at_load_is_in_the_template():
    html = open(TEMPLATE).read()
    ids = set(re.findall(r'id="([^"]+)"', html))
    for needed in ("alPanel", "alBtn", "refreshBtn", "refreshBox", "refreshLabel", "refreshNote", "fresh", "pickers", "strip", "list", "orphans", "foot"):
        assert needed in ids, needed
    desk = open(DESK).read()
    for used in set(re.findall(r'\$\("#([A-Za-z]+)"\)', desk)):
        assert used in ids or used in re.findall(r'id="([A-Za-z]+)"', desk) or used in re.findall(r"id=\\?\"([A-Za-z]+)\\?\"", desk), used


def test_the_allocations_script_loads_before_the_desk_script_that_uses_it():
    html = open(TEMPLATE).read()
    assert html.index("pacing_budgets.js") < html.index("pacing_desk.js")
    assert "PDAllocations.init" in open(DESK).read()


def test_the_page_talks_to_routes_that_exist():
    from app import app
    rules = {r.rule for r in app.url_map.iter_rules()}
    budgets, desk = open(BUDGETS).read(), open(DESK).read()
    assert re.search(r'var API = "(/p2/paid-marketing/api/pacing/budgets)"', budgets).group(1) in rules
    assert "/p2/paid-marketing/api/pacing/budgets/parse" in rules and 'API+"/parse"' in budgets.replace(" ", "")
    assert re.search(r'var API = "(/p2/paid-marketing/api/pacing)"', desk).group(1) + "/refresh" in rules
    assert "{auto:true}" in desk and "{recompute:true}" in desk                  # the two flags the route understands
    assert 'method:"POST"' in desk.replace(" ", "") or "method:\"POST\"" in desk


def test_no_copy_on_the_page_still_sends_people_to_a_google_sheet():
    for path in (DESK, BUDGETS, TEMPLATE):
        text = open(path).read()
        for gone in ("PACING_SPEND_SHEET_ID", "Share both Google Sheets", "spend sheet", "serviceEmail"):
            assert gone not in text, (path, gone)
