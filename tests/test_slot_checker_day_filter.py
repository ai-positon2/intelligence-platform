"""Picking a day in the chart, executed rather than read.

The table already narrowed to the right ROWS when a day was clicked, and went
on printing the fortnight's numbers in them. That is the worst version of the
bug: the rows are correct, so the figures look trustworthy, and a practice with
three slots on Thursday reads as 500 while the header says nothing about a day.

A text assertion on the bundle cannot tell that apart from a fix, because both
contain the same identifiers. So this runs the real inline script against a
small DOM, clicks a day, and reads what the table actually produced.

Skipped, not failed, where node is unavailable.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not available")

# Two practices with deliberately opposed shapes. Alder is a big practice whose
# Thursday is a quiet day; Birch is small but does almost everything on that
# Thursday. Under the old code both printed their fortnight total, so Alder
# looked like the better Thursday option by a factor of six. It is not.
DAY = 2   # index of the day under test

ALDER = {
    "office": "001", "name": "Alder", "account": "Alder", "brand": "Gentle Dental",
    "state": "MA", "city": "Boston", "status": "open", "url": "", "system": "x",
    "booking": "Calendar View", "checked_at": "2026-08-12T00:00:00", "runs": 1,
    "counts": [100, 100, 5, 100, 100], "total": 405, "peak": 100,
    "days_open": 5, "first_open_index": 0, "lead_days": 0, "service_count": 3,
    "zero_services": 0,
    "services": [
        {"name": "Cleaning", "counts": [50, 50, 5, 50, 50], "total": 205},
        {"name": "Exam", "counts": [50, 50, 0, 50, 50], "total": 200},
        {"name": "Ortho", "counts": [0, 0, 0, 0, 0], "total": 0},
    ],
}
BIRCH = {
    "office": "002", "name": "Birch", "account": "Birch", "brand": "Other Brand",
    "state": "NH", "city": "Dover", "status": "open", "url": "", "system": "x",
    "booking": "Calendar View", "checked_at": "2026-08-12T00:00:00", "runs": 1,
    "counts": [1, 1, 60, 1, 1], "total": 64, "peak": 60,
    "days_open": 5, "first_open_index": 0, "lead_days": 0, "service_count": 2,
    "zero_services": 0,
    "services": [
        {"name": "Cleaning", "counts": [1, 1, 40, 1, 1], "total": 44},
        {"name": "Exam", "counts": [0, 0, 20, 0, 0], "total": 20},
    ],
}
# Closed on the day under test: must disappear from the table entirely.
CEDAR = {
    "office": "003", "name": "Cedar", "account": "Cedar", "brand": "Other Brand",
    "state": "CT", "city": "Hartford", "status": "open", "url": "", "system": "x",
    "booking": "Calendar View", "checked_at": "2026-08-12T00:00:00", "runs": 1,
    "counts": [7, 7, 0, 7, 7], "total": 28, "peak": 7,
    "days_open": 4, "first_open_index": 0, "lead_days": 0, "service_count": 1,
    "zero_services": 0,
    "services": [{"name": "Cleaning", "counts": [7, 7, 0, 7, 7], "total": 28}],
}

DATES = [{"date": "2026-08-1%d" % i, "weekday": w, "label": "1%d Aug" % i}
         for i, w in enumerate(["Wed", "Thu", "Fri", "Sat", "Sun"])]

DASHBOARD = {
    "practices": [ALDER, BIRCH, CEDAR],
    "dates": DATES,
    "by_date": [dict(d, slots=0, practices_open=0, weekend=False) for d in DATES],
    "by_state": [], "by_brand": [], "by_service": [], "by_weekday": [],
    "alerts": [], "totals": {}, "freshness": {}, "generated_at": "", "source": "test",
}

_DRIVER = r"""
const fs = require("fs");
const bundle = fs.readFileSync(process.argv[2], "utf8");
const data = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const day = parseInt(process.argv[4], 10);

function makeEl(tag){
  const el = {
    tagName: tag || "div", _html: "", value: "", textContent: "", style: {},
    dataset: {}, options: [],
    classList: { _s: new Set(),
      add(c){this._s.add(c);}, remove(c){this._s.delete(c);},
      contains(c){return this._s.has(c);},
      toggle(c,on){ if(on===undefined) on=!this._s.has(c);
                    if(on) this._s.add(c); else this._s.delete(c); return on; } },
    getAttribute(){ return null; }, setAttribute(){}, remove(){},
    appendChild(){}, removeChild(){}, addEventListener(){},
    querySelectorAll(){ return []; }, querySelector(){ return null; },
    closest(){ return null; }, scrollIntoView(){}, focus(){}, click(){},
    getBoundingClientRect(){ return {top:0,left:0,width:0,height:0}; },
  };
  Object.defineProperty(el, "innerHTML", {
    get(){ return el._html; }, set(v){ el._html = String(v); } });
  return el;
}
const els = {};
function byId(id){ if(!els[id]) els[id] = makeEl(); return els[id]; }

global.document = {
  getElementById: byId,
  querySelectorAll(){ return []; },
  querySelector(){ return null; },
  createElement: makeEl,
  addEventListener(){},
  documentElement: makeEl(),
  body: makeEl(),
};
global.window = { addEventListener(){}, matchMedia(){ return {matches:false, addEventListener(){}}; },
                  localStorage: { getItem(){return null;}, setItem(){} } };
global.fetch = () => new Promise(() => {});
global.requestAnimationFrame = (f) => f();
global.setTimeout = (f) => { try { f(); } catch(e){} return 0; };

eval(bundle);

// Drive it: seed the payload the way the page's own fetch would, then click.
DATA = data;
VIEW = data.practices.slice();
const out = {};
onFilter();
out.unfiltered = { table: byId("practicesBody").innerHTML,
                   count: byId("resultCount").textContent };
setDateFilter(day);
out.filtered = { table: byId("practicesBody").innerHTML,
                 count: byId("resultCount").textContent,
                 chip: byId("dateChipLabel").textContent,
                 sortOptions: byId("sortField").innerHTML };
// Sort by the day-only column BEFORE clearing, so the clear has a stale key
// to deal with. Without this step the guard is never exercised.
sortBy("day_share");
out.sortedByShare = { table: byId("practicesBody").innerHTML,
                      sortKey: SORT.key, sortValue: byId("sortField").value };
clearDateFilter();
out.cleared = { table: byId("practicesBody").innerHTML,
                count: byId("resultCount").textContent,
                sortOptions: byId("sortField").innerHTML,
                sortKey: SORT.key, sortValue: byId("sortField").value };
console.log(JSON.stringify(out));
"""


def _rendered_bundle():
    """The page's main inline script, as the browser receives it."""
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    body = c.get("/p2/b2b-agents/42-north-dental-slot-checker").get_data(as_text=True)
    scripts = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", body, re.S)
    # By content, not by size. The largest inline script on this page belongs
    # to an unrelated assistant widget, and picking it produced a driver that
    # failed with "onFilter is not defined", which reads like a broken test
    # rather than the wrong script.
    owns = [s for s in scripts if "function setDateFilter" in s]
    assert len(owns) == 1, "expected exactly one dashboard script, got %d" % len(owns)
    return owns[0]


@pytest.fixture(scope="module")
def run():
    tmp = tempfile.mkdtemp()
    js = os.path.join(tmp, "bundle.js")
    with open(js, "w", encoding="utf-8") as f:
        f.write(_rendered_bundle())
    drv = os.path.join(tmp, "drive.js")
    with open(drv, "w", encoding="utf-8") as f:
        f.write(_DRIVER)
    dat = os.path.join(tmp, "data.json")
    with open(dat, "w", encoding="utf-8") as f:
        json.dump(DASHBOARD, f)
    r = subprocess.run(["node", drv, js, dat, str(DAY)],
                       capture_output=True, text=True)
    if r.returncode:
        pytest.fail("driver failed:\n%s" % (r.stderr[-2500:]))
    return json.loads(r.stdout.strip().splitlines()[-1])


def _cells(table_html, office):
    """The <td> texts of one practice's row, tags stripped."""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.S)
    for row in rows:
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        texts = [re.sub(r"<[^>]+>", " ", t) for t in tds]
        texts = [re.sub(r"\s+", " ", t).strip() for t in texts]
        if texts and texts[0] == office:
            return texts
    return None


def test_the_slot_number_becomes_that_days_slots(run):
    """The regression. Alder has 405 slots over the fortnight and 5 on the
    chosen day; Birch has 64 and 60. Printing the totals under a day filter
    ranked Alder six times above Birch for a day Alder is almost closed."""
    alder = _cells(run["filtered"]["table"], "001")
    birch = _cells(run["filtered"]["table"], "002")
    assert alder is not None and birch is not None
    assert "5" in alder[5] and "405" not in alder[5], (
        "Alder still printed its fortnight total: %r" % alder[5])
    assert "60" in birch[5] and "64" not in birch[5], (
        "Birch still printed its fortnight total: %r" % birch[5])


def test_the_service_count_becomes_services_open_that_day(run):
    """Alder runs three services but only one of them on the chosen day."""
    alder = _cells(run["filtered"]["table"], "001")
    assert alder[4].startswith("1"), (
        "expected 1 service open that day, got %r" % alder[4])
    assert "of 3" in alder[4], "the practice's full service count was dropped"


def test_a_practice_closed_that_day_is_not_in_the_table(run):
    assert _cells(run["filtered"]["table"], "003") is None


def test_the_header_says_which_day_the_numbers_are_for(run):
    """A column of day numbers under a header reading "Open slots" is the same
    lie as printing the totals, just moved one row up."""
    head = re.search(r"<thead>(.*?)</thead>", run["filtered"]["table"], re.S).group(1)
    assert "Slots Fri 12 Aug" in re.sub(r"<[^>]+>", " ", head), head
    assert "Share of total" in head


def test_the_slot_total_beside_the_row_count_is_the_days_total(run):
    """5 + 60, not 405 + 64. Summing the fortnight beside a row count already
    narrowed to one day made one number mean two things."""
    txt = run["filtered"]["count"]
    assert "65 slots" in txt, txt
    assert "on Fri 12 Aug" in txt, txt
    assert "469" not in txt


def test_clearing_the_day_restores_the_fortnight_numbers(run):
    """The filter has to be reversible, or the only way back is a reload."""
    alder = _cells(run["cleared"]["table"], "001")
    assert "405" in alder[5]
    assert _cells(run["cleared"]["table"], "003") is not None
    assert "Open slots" in run["cleared"]["table"]


def test_the_unfiltered_table_is_unchanged_by_any_of_this(run):
    alder = _cells(run["unfiltered"]["table"], "001")
    assert "405" in alder[5] and "Open slots" in run["unfiltered"]["table"]
    assert "Share of total" not in run["unfiltered"]["table"]


def test_the_sort_dropdown_follows_the_columns(run):
    """The columns and the dropdown used to be two hand-kept lists. A day-only
    sort key surviving the filter being cleared would leave the dropdown on a
    blank value, sorting by a field with no column."""
    assert "day_share" in run["filtered"]["sortOptions"]
    assert "day_share" not in run["cleared"]["sortOptions"]
    assert "lead_days" in run["cleared"]["sortOptions"]


def test_the_chosen_day_is_marked_in_every_rows_pattern(run):
    """One number for a day, next to a chart of a fortnight, with nothing
    tying them together."""
    assert 'class="hi"' in run["filtered"]["table"] or \
           ' hi"' in run["filtered"]["table"], "the selected day is not marked"
    assert "hi" not in re.findall(r'<i class="([^"]*)"',
                                  run["cleared"]["table"])[0]


def _order(table_html):
    """Office numbers in the order the table printed them."""
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.S)
    out = []
    for row in rows:
        m = re.search(r'<span class="gd-office">([^<]*)</span>', row)
        if m:
            out.append(m.group(1))
    return out


def test_the_rows_are_ordered_by_that_days_slots(run):
    """The default sort is by slots, descending. Sorting by the fortnight
    total while printing day numbers puts Alder (5 that day, 405 overall)
    above Birch (60 that day), so the table is ordered by figures that are not
    on the screen and the best option for that day sits second."""
    assert _order(run["filtered"]["table"]) == ["002", "001"], (
        "ordered by the fortnight total, not by the chosen day")


def test_clearing_the_day_restores_the_fortnight_ordering(run):
    assert _order(run["cleared"]["table"]) == ["001", "002", "003"]


def test_a_day_only_sort_key_does_not_survive_the_filter_being_cleared(run):
    """Sorting by "Share of total" and then clearing the day left SORT.key
    pointing at a column that no longer exists. The dropdown lands on a blank
    value and the table is ordered by a field with no header, which looks like
    the sort silently broke."""
    assert run["sortedByShare"]["sortKey"] == "day_share"
    assert run["cleared"]["sortKey"] == "total", (
        "a day-only sort key outlived the day filter: %r"
        % run["cleared"]["sortKey"])
    assert run["cleared"]["sortValue"] == "total", (
        "the sort dropdown was left on a value it no longer offers: %r"
        % run["cleared"]["sortValue"])
