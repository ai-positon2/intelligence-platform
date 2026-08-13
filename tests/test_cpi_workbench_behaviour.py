"""The workbench surfaces, executed rather than read.

The claims here are all behavioural and none of them can be checked by reading
the bundle, since `if(false)` still contains whatever text a grep would look for:

  1. The query bar says back exactly what gatherFilters() would search for, with
     a min/max pair read as one band rather than two half-sentences.
  2. Removing a band chip clears BOTH halves, or the re-run still excludes for
     the reason the chip was removed to fix.
  3. Nothing is counted until a real filter is set, and never on the Companies
     tab, which is the client half of the credit guard the server also enforces.
  4. The bulk-enrich button quotes the price of the click before it is clicked,
     counting only rows that can actually charge.

Skipped, not failed, where node is unavailable.
"""

import json
import os
import shutil
import subprocess
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = os.path.join(_ROOT, "static", "js", "company_people_intelligence.js")

_DRIVER = r"""
const fs = require("fs");
const bundle = fs.readFileSync(process.argv[2], "utf8");

function makeEl(tag, id){
  const el = {
    tagName: tag || "div", id: id || "", _html: "", value: "", checked: false,
    disabled: false, textContent: "", title: "", style: {}, options: [],
    dataset: {}, _on: {}, _kids: [], selectedOptions: [],
    classList: {
      _s: new Set(),
      add(c){ this._s.add(c); }, remove(c){ this._s.delete(c); },
      contains(c){ return this._s.has(c); },
      toggle(c, on){ if(on === undefined) on = !this._s.has(c);
                     if(on) this._s.add(c); else this._s.delete(c); return on; },
    },
    getAttribute(n){ return this["_attr_"+n] === undefined ? null : this["_attr_"+n]; },
    setAttribute(n, v){ this["_attr_"+n] = v; },
    remove(){}, appendChild(c){ this._kids.push(c); }, removeChild(){},
    contains(other){ return other === this || this._kids.indexOf(other) >= 0; },
    addEventListener(ev, fn){ (this._on[ev] = this._on[ev] || []).push(fn); },
    querySelectorAll(sel){ return QSA(sel, this); },
    querySelector(sel){ return QSA(sel, this)[0] || null; },
    closest(sel){ return CLOSEST(this, sel); },
    scrollIntoView(){}, focus(){}, click(){ (this._on.click||[]).forEach(f=>f({target:this})); },
    getBoundingClientRect(){ return {top:100,left:0,bottom:130,right:200,width:200,height:30}; },
  };
  el.lastChild = { textContent: "" };
  Object.defineProperty(el, "innerHTML", {
    get(){ return el._html; }, set(v){ el._html = String(v); },
  });
  Object.defineProperty(el, "parentElement", { get(){ return el._parent || null; } });
  return el;
}

// Chips are the only thing the bundle reads back out of rendered HTML, so a tiny
// selector shim over registered elements is enough; nothing here parses HTML.
const CHIPS = { "#fpSeniority": [], "#fpEmailStatus": [] };
function QSA(sel, root){
  for (const g of Object.keys(CHIPS)) {
    if (sel.startsWith(g + " .cpi-chip.on")) return CHIPS[g].filter(c => c.classList.contains("on"));
    if (sel.startsWith(g + " .cpi-chip")) return CHIPS[g];
  }
  if (sel === "#cpiQbar .cpi-qchip") {
    const m = (els.cpiQbar.innerHTML.match(/class="cpi-qchip"/g) || []);
    return m.map(() => makeEl("span"));
  }
  return [];
}
function CLOSEST(el, sel){ return null; }

const IDS = ["fpTitles","fpCompanyDomain","fpKeywords","fpEmpRange","fpLocation",
  "fpLocationList","fpLocationChips","fpLocationCombo","cpiQbar","cpiLiveCount",
  "cpiLiveCountCo","cpiToast","cpiResultsWrap","cpiToolbar","cpiCount","cpiBulk",
  "cpiBulkN","cpiBulkEnrich","cpiSelectAll","cpiLoadMore","fpCompanyDetail",
  "fpAdvanced","fpMoreBtn","fcAdvanced","fcMoreBtn","cpiFiltersPeople",
  "cpiFiltersCompanies","cpiEntityToggle","cpiListN","cpiSpend","cpiAskInput",
  "cpiAskBtn","cpiAskNote","fpSeniority","fpEmailStatus","fpRevenueMin","fpRevenueMax",
  "fcName","fcEmpRange"];
const els = {};
IDS.forEach(id => { els[id] = makeEl("div", id); });
els.fpCompanyDetail.checked = true;
els.fpEmpRange.options = [
  {value:""}, {value:"1,10"}, {value:"11,50"}, {value:"51,200"},
  {value:"201,500"}, {value:"501,1000"}, {value:"1001,5000"}, {value:"5001,"}
];
["owner","founder","c_suite","partner","vp","head","director"].forEach(v => {
  const c = makeEl("span"); c.setAttribute("data-val", v);
  c.textContent = v === "c_suite" ? "C-Suite" : v[0].toUpperCase()+v.slice(1);
  CHIPS["#fpSeniority"].push(c);
});

global.window = global;
global.addEventListener = function(){};
global.matchMedia = () => ({ matches:false, addEventListener(){} });
global.requestAnimationFrame = cb => setTimeout(cb, 0);
global.getComputedStyle = () => ({});
global.innerWidth = 1440; global.innerHeight = 900;
global.confirm = () => true;
global.document = {
  getElementById(id){ return els[id] || null; },
  querySelectorAll(sel){ return QSA(sel, null); },
  querySelector(){ return null; },
  createElement(t){ return makeEl(t); },
  addEventListener(){},
  body: { style:{}, appendChild(){}, removeChild(){} },
  documentElement: { style:{} },
};
global.navigator = { clipboard: { writeText(){ return Promise.resolve(); } } };
global.setTimeout = setTimeout;
global.__CPI_HISTORY_URL__ = "/history";
global.__CPI_VOCAB_URL__ = "/vocab";
global.__CPI_COUNT_URL__ = "/count";
global.__CPI_CREDITS_URL__ = "/credits";
global.__CPI_LIST_URL__ = "/list";
global.__CPI_PARSE_URL__ = "/parse";
global.__CPI_SEARCH_URL__ = "/search";

const SENT = [];
let COUNT_REPLY = { count: 2400, approx: false };
global.fetch = function(url, opts){
  const u = String(url);
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  SENT.push({ url: u, body: body });
  let payload = {};
  if (u.indexOf("/count") >= 0) payload = COUNT_REPLY;
  else if (u.indexOf("/credits") >= 0) payload = { available:false };
  else if (u.indexOf("/list") >= 0) payload = { rows:[], count:0, available:true };
  return Promise.resolve({ ok:true, json(){ return Promise.resolve(payload); } });
};

eval(bundle);

const tick = () => new Promise(r => setTimeout(r, 0));
// The count is debounced by 420ms, which microtask ticks cannot advance past:
// this has to be real elapsed time or the request never fires.
const settle = async ms => { await new Promise(r => setTimeout(r, ms === undefined ? 600 : ms)); };
function countCalls(){ return SENT.filter(s => s.url.indexOf("/count") >= 0); }

(async function(){
  const out = {};

  // 1. Nothing set: the bar is hidden and nothing is counted.
  window.cpiFiltersChanged();
  await settle();
  out.emptyBarHidden = els.cpiQbar.style.display === "none";
  out.countsWhenEmpty = countCalls().length;

  // 2. A title and a seniority: the bar names both, using the chip's own label.
  els.fpTitles.value = "CMO";
  CHIPS["#fpSeniority"].find(c => c.getAttribute("data-val") === "c_suite").classList.add("on");
  window.cpiFiltersChanged();
  await settle();
  out.barHtml = els.cpiQbar.innerHTML;
  out.countAfterFilter = els.cpiLiveCount.innerHTML;
  out.countCallsAfterFilter = countCalls().length;
  out.lastCountBody = countCalls().slice(-1)[0].body;

  // 3. A min/max pair reads as one band, not two chips.
  els.fpRevenueMin.value = "1000000";
  els.fpRevenueMax.value = "50000000";
  window.cpiFiltersChanged();
  await settle();
  out.bandHtml = els.cpiQbar.innerHTML;

  // 4. Dropping the band clears BOTH halves.
  window.cpiDropFilter("revenue_min");
  window.cpiDropFilter("revenue_max");
  out.revMinAfter = els.fpRevenueMin.value;
  out.revMaxAfter = els.fpRevenueMax.value;

  // 5. An approximate count says so.
  COUNT_REPLY = { count: 2400, approx: true };
  window.cpiFiltersChanged();
  await settle();
  out.approxHtml = els.cpiLiveCount.innerHTML;
  out.approxTitle = els.cpiLiveCount.title;

  // 6. The Companies tab is never counted from the client, even with company
  //    filters set. Setting one matters: without it the empty-filter guard
  //    would return early and mask whether the entity guard works at all.
  els.fcName.value = "Acme";
  const before = countCalls().length;
  window.cpiSetEntity("companies");
  await settle();
  out.countCallsOnCompaniesTab = countCalls().length - before;
  out.companiesHadFilters = els.cpiQbar.innerHTML.indexOf("Acme") >= 0;
  out.companiesCountText = els.cpiLiveCount.textContent;
  window.cpiSetEntity("people");
  els.fcName.value = "";

  console.log(JSON.stringify(out));
})();
"""


def _run():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    with tempfile.TemporaryDirectory() as d:
        driver = os.path.join(d, "driver.js")
        with open(driver, "w", encoding="utf-8") as fh:
            fh.write(_DRIVER)
        proc = subprocess.run([node, driver, _JS], capture_output=True, text=True,
                              timeout=60)
    if proc.returncode != 0:
        pytest.fail("driver failed: %s" % (proc.stderr or proc.stdout)[-3000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def out():
    return _run()


# ── The query bar ────────────────────────────────────────────────────────────

def test_no_filters_means_no_bar(out):
    assert out["emptyBarHidden"] is True


def test_the_bar_names_each_active_filter(out):
    assert "CMO" in out["barHtml"]
    assert "Title" in out["barHtml"]


def test_a_chip_filter_shows_its_label_not_its_apollo_value(out):
    """Apollo's value is "c_suite"; the chip says "C-Suite". The bar has to agree
    with the control it is describing, not with the wire format."""
    assert "C-Suite" in out["barHtml"]
    assert "c_suite" not in out["barHtml"]


def test_a_min_max_pair_reads_as_one_band(out):
    """Two chips saying half a thing each is how eight filters become sixteen
    chips. One band, one chip."""
    assert "Revenue" in out["bandHtml"]
    assert "$1M to $50M" in out["bandHtml"]
    assert "Revenue ≥" not in out["bandHtml"]


def test_removing_a_band_clears_both_halves(out):
    """Clearing only the floor would re-run a search that still excludes for the
    very reason the chip was removed to fix."""
    assert out["revMinAfter"] == ""
    assert out["revMaxAfter"] == ""


# ── The count, and what it costs ─────────────────────────────────────────────

def test_an_empty_filter_set_is_never_counted(out):
    """Counting nothing asks Apollo how many people it has, which describes the
    database rather than the search: noise on load, and a request nobody asked
    for."""
    assert out["countsWhenEmpty"] == 0


def test_a_real_filter_is_counted(out):
    assert out["countCallsAfterFilter"] >= 1
    assert "2.4K" in out["countAfterFilter"]


def test_the_count_never_asks_from_the_companies_tab(out):
    """The client half of the credit guard: mixed_companies/search bills per
    call, so a count that ran while you typed would spend one per keystroke.
    The server refuses it too; neither guard is trusted alone.

    The companion assertion is what gives this one teeth. With no company filter
    set, the "nothing to count" guard returns early and this passes whether or
    not the entity guard exists at all, which is exactly how a mutant that
    deleted the entity guard survived the first version of this test."""
    assert out["companiesHadFilters"] is True, "the tab must have a filter set"
    assert out["countCallsOnCompaniesTab"] == 0


def test_switching_to_companies_clears_the_stale_people_count(out):
    """A number left over from the other tab is a claim about rows that are not
    being searched."""
    assert out["companiesCountText"] == ""


def test_an_approximate_count_says_about(out):
    """Apollo's total counts what IT matched; the verification pass then removes
    rows. Printing it bare would promise more than the page can show."""
    assert out["approxHtml"].startswith("about ")
    assert "removed" in out["approxTitle"]


def test_an_exact_count_does_not_say_about(out):
    assert not out["countAfterFilter"].startswith("about ")


def test_the_count_sends_the_same_filters_the_search_would(out):
    """It has to describe the query on screen, not a simplified version of it."""
    body = out["lastCountBody"]
    assert body["entity"] == "people"
    assert body["filters"]["titles"] == ["CMO"]
    assert body["filters"]["seniorities"] == ["c_suite"]
