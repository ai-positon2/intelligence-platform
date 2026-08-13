"""The table view, executed rather than read.

Cards read one prospect at a time, which is right for judging a single person
and wrong for comparing a page of them. The table is the same rows with the same
selection and the same actions, so almost everything worth testing here is about
that sameness holding up.

The claim that matters most is the index mapping. Every action on this page
addresses a row by its POSITION in STATE.results (cpiToggleSelect, cpiOpenDetails,
cpiOpenEnrich), so a sort that reordered the underlying array would leave every
button pointing at the wrong person: you would tick the row for the 22,000-person
company and enrich somebody else, spending a credit on the wrong contact. The
sort therefore permutes indices and carries the original index into each rendered
row, and the test drives a real sort, ticks the top row and reads back what the
export actually sends.

The rest: a doubled Apollo title collapses only when it is genuinely doubled,
and sorting says it covers the loaded rows rather than the whole result set.

Skipped, not failed, where node is unavailable.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = os.path.join(_ROOT, "static", "js", "company_people_intelligence.js")
_CSS = os.path.join(_ROOT, "static", "css", "company_people_intelligence.css")
_TPL = os.path.join(_ROOT, "templates", "company_people_intelligence.html")


# ── cleanTitle, run on its own ───────────────────────────────────────────────
# Apollo really returns "Marketing Director, Marketing Director" and
# "Director, Marketing & Director, Marketing" (both from one search). Only an
# exact repeat may collapse: a title that merely CONTAINS a separator is a
# different, real title and must survive untouched.

_TITLE_CASES = [
    # (input, expected) -- collapses
    ("Marketing Director, Marketing Director", "Marketing Director"),
    ("Director, Marketing & Director, Marketing", "Director, Marketing"),
    ("VP Sales / VP Sales", "VP Sales"),
    ("Head of Growth, Head of Growth, Head of Growth", "Head of Growth"),
    ("Marketing Director, marketing director", "Marketing Director"),
    # ...and must NOT collapse
    ("Director, Marketing", "Director, Marketing"),
    ("Director, Marketing & Sales", "Director, Marketing & Sales"),
    ("VP Sales / EMEA", "VP Sales / EMEA"),
    ("CMO", "CMO"),
    ("", ""),
]

_TITLE_DRIVER = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const m = src.match(/function cleanTitle\(t\)\{[\s\S]*?\n\}/);
if (!m) { console.error("cleanTitle not found"); process.exit(2); }
eval(m[0]);
const cases = JSON.parse(process.argv[3]);
console.log(JSON.stringify(cases.map(c => cleanTitle(c[0]))));
"""


def _node():
    n = shutil.which("node")
    if not n:
        pytest.skip("node is not available")
    return n


@pytest.fixture(scope="module")
def titles():
    node = _node()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.js")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(_TITLE_DRIVER)
        proc = subprocess.run([node, p, _JS, json.dumps(_TITLE_CASES)],
                              capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        pytest.fail(proc.stderr[-2000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("idx", range(len(_TITLE_CASES)))
def test_a_title_is_cleaned_only_when_it_really_repeats(titles, idx):
    src, want = _TITLE_CASES[idx]
    assert titles[idx] == want, "cleanTitle(%r)" % src


# There is deliberately no test pinning the ORDER of the separator list. An
# earlier version of this file had one, on the belief that " & " had to be tried
# before the comma or "Director, Marketing & Director, Marketing" would be
# missed. That is true of a single-regex implementation and false of this one: a
# split that does not come out all-equal falls through to the next separator, so
# reversing the list leaves every case above unchanged. Pinning it would have
# been a change-detector test, failing on a harmless reorder while catching no
# defect the parametrized cases do not already catch.


def test_the_stored_title_is_never_rewritten():
    """A display cleanup only. Details and every export still carry Apollo's own
    string, so nothing downstream disagrees with what Apollo actually said."""
    js = open(_JS, encoding="utf-8").read()
    assert "r.title=cleanTitle" not in js.replace(" ", "")
    assert "p.title=cleanTitle" not in js.replace(" ", "")


# ── The table itself, driven in node ─────────────────────────────────────────

_DRIVER = r"""
const fs = require("fs");
const bundle = fs.readFileSync(process.argv[2], "utf8");

function makeEl(tag, id){
  const el = {
    tagName: tag||"div", id: id||"", _html: "", value: "", checked: false,
    textContent: "", title: "", style: {}, options: [], dataset: {}, _on: {}, _kids: [],
    classList: { _s: new Set(),
      add(c){ this._s.add(c); }, remove(c){ this._s.delete(c); },
      contains(c){ return this._s.has(c); },
      toggle(c,on){ if(on===undefined) on=!this._s.has(c);
                    if(on) this._s.add(c); else this._s.delete(c); return on; } },
    getAttribute(n){ return this["_a_"+n]===undefined?null:this["_a_"+n]; },
    setAttribute(n,v){ this["_a_"+n]=v; },
    remove(){}, appendChild(c){ this._kids.push(c); }, removeChild(){},
    contains(o){ return o===this || this._kids.indexOf(o)>=0; },
    addEventListener(e,f){ (this._on[e]=this._on[e]||[]).push(f); },
    querySelectorAll(){ return []; }, querySelector(){ return null; },
    closest(){ return null; }, scrollIntoView(){}, focus(){}, click(){},
    getBoundingClientRect(){ return {top:0,left:0,bottom:0,right:0,width:0,height:0}; },
  };
  el.lastChild = { textContent: "" };
  Object.defineProperty(el, "innerHTML", { get(){ return el._html; },
                                           set(v){ el._html = String(v); } });
  return el;
}
const IDS = ["cpiResultsWrap","cpiToolbar","cpiCount","cpiLoadMore","cpiSelectAll",
  "cpiBulk","cpiBulkN","cpiBulkEnrich","cpiToast","cpiFiltersPeople","cpiListN",
  "cpiFiltersCompanies","cpiEntityToggle","fpCompanyDetail","cpiViewToggle","cpiQbar",
  "cpiLiveCount","cpiLiveCountCo","cpiSpend","fpAdvanced","fpMoreBtn","fcAdvanced","fcMoreBtn"];
const els = {};
IDS.forEach(id => els[id] = makeEl("div", id));
els.fpCompanyDetail.checked = true;

const STORE = {};
global.localStorage = { getItem: k => (k in STORE ? STORE[k] : null),
                        setItem: (k,v) => { STORE[k] = String(v); } };
global.window = global;
global.addEventListener = function(){};
global.matchMedia = () => ({matches:false, addEventListener(){}});
global.requestAnimationFrame = cb => setTimeout(cb,0);
global.getComputedStyle = () => ({});
global.innerWidth = 1600; global.innerHeight = 900;
global.confirm = () => true;
global.document = {
  getElementById(id){ return els[id]||null; },
  querySelectorAll(){ return []; }, querySelector(){ return null; },
  createElement(t){ return makeEl(t); }, addEventListener(){},
  body:{style:{},appendChild(){},removeChild(){}}, documentElement:{style:{}},
};
global.navigator = { clipboard:{ writeText(){ return Promise.resolve(); } } };
global.URL = { createObjectURL(){ return "blob:x"; }, revokeObjectURL(){} };
global.setTimeout = setTimeout;
["HISTORY","EXPORT","VOCAB","COUNT","CREDITS","LIST","PARSE","SEARCH"].forEach(n => {
  global["__CPI_"+n+"_URL__"] = "/"+n.toLowerCase();
});

let SEARCH = {};
const SENT = [];
global.fetch = function(url, opts){
  const u = String(url);
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  SENT.push({url:u, body:body});
  if (u.indexOf("/export") >= 0)
    return Promise.resolve({ok:true, headers:{get(){return "";}}, blob(){ return Promise.resolve({}); }});
  let payload = {};
  if (u.indexOf("/search") >= 0) payload = SEARCH;
  else if (u.indexOf("/list") >= 0) payload = {rows:[],count:0,available:true};
  else if (u.indexOf("/credits") >= 0) payload = {available:false};
  return Promise.resolve({ok:true, json(){ return Promise.resolve(payload); }});
};

eval(bundle);
const tick = () => new Promise(r => setTimeout(r,0));
const settle = async () => { for (let i=0;i<20;i++) await tick(); };

// Deliberately NOT in size order, so a sort really has to reorder something.
const PEOPLE = [
  {id:"p0", full_name:"Ann Small",  title:"CMO", organization_name:"Small Co",
   organization_employees:95},
  {id:"p1", full_name:"Bob Big",    title:"Marketing Director, Marketing Director",
   organization_name:"Big Co", organization_employees:22000},
  {id:"p2", full_name:"Cy Middle",  title:"Director, Marketing & Director, Marketing",
   organization_name:"Mid Co", organization_employees:610},
];

(async function(){
  const out = {};
  SEARCH = {results: PEOPLE, total: 943, has_more: true, page: 1};
  window.cpiRunSearch(true);
  await settle();

  window.cpiSetView("table");
  out.tableHtml = els.cpiResultsWrap.innerHTML;
  out.viewStored = STORE["cpi-view"];

  // A doubled title is collapsed in the rendered cell.
  out.hasDoubled = /Marketing Director, Marketing Director/.test(out.tableHtml);
  out.hasAmpDoubled = /Director, Marketing &amp; Director, Marketing/.test(out.tableHtml);

  // Sort by size, descending, then read the rendered order.
  window.cpiSortBy("organization_employees");
  window.cpiSortBy("organization_employees");
  const html = els.cpiResultsWrap.innerHTML;
  // Only the bold display name: the row also carries the name in a title
  // attribute, and counting both makes every row appear twice.
  out.sortedOrder = (html.match(/<b>(?:Ann Small|Bob Big|Cy Middle)<\/b>/g) || [])
                      .map(s => s.replace(/<\/?b>/g, ""));
  out.note = /Sorted within the/.test(html);

  // The row rendered FIRST after the sort is Bob (22,000). Ticking it must
  // select Bob, not whoever sits at index 0 of the unsorted array (Ann).
  const firstIdx = (html.match(/cpiToggleSelect\((\d+)\)/) || [])[1];
  out.firstRenderedIndex = firstIdx;
  window.cpiToggleSelect(Number(firstIdx));
  await settle();
  window.cpiExport("csv", true);
  await settle();
  const exp = SENT.filter(s => s.url.indexOf("/export") >= 0).pop();
  out.exportedNames = (exp && exp.body.rows || []).map(r => r.full_name);

  // Cards and table must agree about what is selected.
  window.cpiSetView("cards");
  out.cardsHtml = els.cpiResultsWrap.innerHTML.slice(0, 400);
  out.stillSelected = /cpi-card-check on/.test(els.cpiResultsWrap.innerHTML);

  console.log(JSON.stringify(out));
})();
"""


def _run():
    node = _node()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "driver.js")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(_DRIVER)
        proc = subprocess.run([node, p, _JS], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        pytest.fail("driver failed: %s" % (proc.stderr or proc.stdout)[-3000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def out():
    return _run()


def test_the_table_renders_rows(out):
    assert "<table" in out["tableHtml"]
    assert out["tableHtml"].count("cpiToggleSelect(") == 3


def test_a_doubled_title_is_collapsed_in_the_cell(out):
    assert out["hasDoubled"] is False
    assert out["hasAmpDoubled"] is False


def test_sorting_reorders_the_rendered_rows(out):
    """Descending by size: 22,000 then 610 then 95."""
    assert out["sortedOrder"][:3] == ["Bob Big", "Cy Middle", "Ann Small"]


def test_sorting_says_it_only_covers_the_loaded_rows(out):
    """943 matched and 3 are loaded, so "sorted by size" describes the three,
    not the top of the result set."""
    assert out["note"] is True


def test_a_sorted_row_still_addresses_its_own_person(out):
    """The defect this design exists to prevent: sorting a copy of the array
    would leave every button pointing at the wrong row, and enriching the top
    row would spend a credit on somebody else."""
    assert out["firstRenderedIndex"] == "1", "Bob is index 1 in the unsorted array"
    assert out["exportedNames"] == ["Bob Big"]


def test_the_layout_choice_is_remembered(out):
    assert out["viewStored"] == "table"


def test_switching_back_to_cards_keeps_the_selection(out):
    """Same rows and same selection: the view is layout, not state."""
    assert "cpi-card" in out["cardsHtml"]
    assert out["stillSelected"] is True


# ── Markup and styling contracts ─────────────────────────────────────────────

def test_the_toggle_offers_both_layouts():
    tpl = open(_TPL, encoding="utf-8").read()
    assert 'data-view="cards"' in tpl
    assert 'data-view="table"' in tpl


def test_the_table_scrolls_itself_rather_than_the_page():
    """A wide table must never make the whole page scroll sideways."""
    css = open(_CSS, encoding="utf-8").read()
    wrap = css[css.index(".cpi-tbl-wrap{"):css.index(".cpi-tbl-wrap{") + 300]
    assert "overflow-x:auto" in wrap


def test_the_header_stays_put_while_the_rows_scroll():
    css = open(_CSS, encoding="utf-8").read()
    assert "position:sticky" in css[css.index(".cpi-tbl thead th{"):
                                    css.index(".cpi-tbl thead th{") + 220]


def test_no_column_is_hidden_at_any_width():
    """The wrapper scrolls instead. Hiding a column silently drops a field the
    reader may be looking for, which is the failure this page is built against."""
    css = open(_CSS, encoding="utf-8").read()
    assert not re.search(r"\.w-(ind|loc|mail|title)\s*\{[^}]*display:\s*none", css)
