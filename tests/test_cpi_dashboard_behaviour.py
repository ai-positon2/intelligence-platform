"""The dashboard's rendering, executed rather than read.

Every other client-side test in this repo asserts on the text of the bundle, which
cannot tell a working guard from a disabled one: `if(false) STATE.shownEntity =
STATE.entity;` still contains the line a text assertion looks for. The two claims
that matter most here are behavioural, so they are checked by running the real
bundle against a small DOM and reading what it produced:

  1. Rows keep their own identity when the tab is switched, so an export of
     person rows is still labelled "people".
  2. An empty page says whether Apollo matched nothing or whether the
     verification pass removed everything it did match.

Skipped, not failed, where node is unavailable: this is extra assurance over the
text assertions in test_cpi_dashboard_audit.py, not a replacement for them.
"""

import json
import os
import shutil
import subprocess
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = os.path.join(_ROOT, "static", "js", "company_people_intelligence.js")

# A DOM just big enough for the results surface: every element the render path
# touches, and null for everything else (the bundle already guards for absent
# controls, which is how the same code serves both tabs).
_DRIVER = r"""
const fs = require("fs");
const bundle = fs.readFileSync(process.argv[2], "utf8");

function makeEl(tag){
  const el = {
    tagName: tag || "div", _html: "", value: "", checked: false, disabled: false,
    textContent: "", style: {}, options: [], dataset: {}, children: [],
    classList: {
      _s: new Set(),
      add(c){ this._s.add(c); }, remove(c){ this._s.delete(c); },
      contains(c){ return this._s.has(c); },
      toggle(c, on){ if(on === undefined) on = !this._s.has(c);
                     if(on) this._s.add(c); else this._s.delete(c); return on; },
    },
    getAttribute(){ return null; }, setAttribute(){}, remove(){},
    appendChild(){}, removeChild(){}, addEventListener(){},
    querySelectorAll(){ return []; }, querySelector(){ return null; },
    closest(){ return null; }, scrollIntoView(){}, focus(){}, click(){},
    getBoundingClientRect(){ return {top:0,left:0,bottom:0,right:0,width:0,height:0}; },
  };
  el.lastChild = { textContent: "" };
  Object.defineProperty(el, "innerHTML", {
    get(){ return el._html; }, set(v){ el._html = String(v); },
  });
  return el;
}

const IDS = ["cpiResultsWrap","cpiToolbar","cpiCount","cpiLoadMore","cpiSelectAll",
  "cpiBulk","cpiBulkN","cpiBulkEnrich","cpiSearchBtn","cpiSearchBtnCo","cpiToast",
  "cpiFiltersPeople","cpiFiltersCompanies","cpiEntityToggle","fpCompanyDetail",
  "cpiDrawer","cpiDrawerOvl","cpiDrawerBody","cpiHistClearAll"];
const els = {};
IDS.forEach(function(id){ els[id] = makeEl(); });
els.fpCompanyDetail.checked = true;

global.window = global;
global.addEventListener = function(){};
global.removeEventListener = function(){};
global.matchMedia = function(){ return { matches: false, addEventListener(){} }; };
global.requestAnimationFrame = function(cb){ return setTimeout(cb, 0); };
global.getComputedStyle = function(){ return {}; };
global.innerWidth = 1440; global.innerHeight = 900;
global.confirm = function(){ return true; };
global.document = {
  getElementById(id){ return els[id] || null; },
  querySelectorAll(){ return []; },
  querySelector(){ return null; },
  createElement(t){ return makeEl(t); },
  addEventListener(){},
  body: { style: {}, appendChild(){}, removeChild(){} },
  documentElement: { style: {} },
};
global.navigator = { clipboard: { writeText(){ return Promise.resolve(); } } };
global.URL = { createObjectURL(){ return "blob:x"; }, revokeObjectURL(){} };
global.setTimeout = setTimeout;
global.__CPI_HISTORY_URL__ = "/history";
global.__CPI_EXPORT_URL__ = "/export";
global.__CPI_VOCAB_URL__ = "/vocab";
global.__CPI_ENRICH_BULK_URL__ = "/enrich-bulk";

// Programmable server. SEARCH holds the next search response; SENT records every
// request body so a test can see what the client claimed about its own rows.
let SEARCH = {};
const SENT = [];
global.fetch = function(url, opts){
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  SENT.push({ url: String(url), body: body });
  let payload = {};
  if (String(url).indexOf("/export") >= 0) {
    return Promise.resolve({ ok: true, headers: { get(){ return ""; } },
                             blob(){ return Promise.resolve({}); } });
  }
  if (String(url).indexOf("/history") >= 0) payload = { entries: [], available: false };
  else payload = SEARCH;
  return Promise.resolve({ ok: true, json(){ return Promise.resolve(payload); } });
};

eval(bundle);

const tick = () => new Promise(function(r){ setTimeout(r, 0); });
async function search(response){
  SEARCH = response;
  window.cpiRunSearch(true);
  for (let i = 0; i < 12; i++) await tick();
}
function lastExportEntity(){
  for (let i = SENT.length - 1; i >= 0; i--) {
    if (SENT[i].url.indexOf("/export") >= 0) return SENT[i].body.entity;
  }
  return null;
}

const PEOPLE = [
  { id: "p1", full_name: "Binal Shah", title: "CMO", organization_name: "Tealium",
    organization_domain: "tealium.com", organization_growth12: 1.5 },
  { id: "p2", full_name: "Ann Lee", title: "VP Marketing",
    organization_name: "Tealium", organization_growth12: 0.19 },
];

(async function(){
  const out = {};

  // 1. Two person rows, then the tab is switched to Companies underneath them.
  await search({ results: PEOPLE, total: 79421, has_more: true });
  out.cards_html = els.cpiResultsWrap.innerHTML;
  out.count_html = els.cpiCount.innerHTML;
  out.select_all_label = els.cpiSelectAll.lastChild.textContent;
  window.cpiExport("csv", false);
  await tick();
  out.export_entity_before_switch = lastExportEntity();

  window.cpiSetEntity("companies");
  window.cpiToggleSelect(0);            // forces a full re-render
  out.html_after_switch = els.cpiResultsWrap.innerHTML;
  out.load_more_after_switch = els.cpiLoadMore.style.display;
  window.cpiExport("csv", false);
  await tick();
  out.export_entity_after_switch = lastExportEntity();

  // 2. Apollo matched, and the verification pass removed all of it.
  // industry (18) and seniority (8) deliberately sum to more than
  // rejected_total (20): some rows fail both checks, so _cpi_verify_rows
  // tallies them under both reasons (see app.py) while the row itself is
  // still only removed once. rejected_total (20), not 26, is what the client
  // must show -- summing `rejected`'s own values here would both overstate
  // the real removal count AND exceed the 20 rows Apollo actually returned,
  // which is the exact regression this fixture exists to catch.
  window.cpiSetEntity("people");
  await search({ results: [], total: 24, has_more: false,
                 rejected: { industry: 18, seniority: 8, tenure: 0 },
                 rejected_total: 20,
                 rejected_labels: { industry: "outside the industry",
                                    seniority: "wrong seniority",
                                    tenure: "too new in role" } });
  out.empty_with_rejections = els.cpiResultsWrap.innerHTML;

  // 3. Apollo really had nothing.
  await search({ results: [], total: 0, has_more: false });
  out.empty_plain = els.cpiResultsWrap.innerHTML;

  process.stdout.write(JSON.stringify(out));
})().catch(function(e){
  process.stdout.write(JSON.stringify({ error: String(e && e.stack || e) }));
});
"""


@pytest.fixture(scope="module")
def ran():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available on this environment")
    with tempfile.TemporaryDirectory() as tmp:
        driver = os.path.join(tmp, "driver.js")
        with open(driver, "w", encoding="utf-8") as fh:
            fh.write(_DRIVER)
        proc = subprocess.run([node, driver, _JS], capture_output=True, text=True,
                              timeout=60)
    assert proc.returncode == 0, proc.stderr[-4000:]
    out = json.loads(proc.stdout)
    assert "error" not in out, out.get("error")
    return out


# ── Rows keep their own identity ─────────────────────────────────────────────

def test_the_rows_render_as_what_they_are(ran):
    assert "Binal Shah" in ran["cards_html"]
    assert "Ann Lee" in ran["cards_html"]


def test_an_export_of_person_rows_says_people(ran):
    assert ran["export_entity_before_switch"] == "people"


def test_switching_the_tab_does_not_relabel_the_rows_underneath_it(ran):
    """The whole point of separating shownEntity from entity: this export used to
    go out as "companies" and come back a sheet of empty cells."""
    assert ran["export_entity_after_switch"] == "people"


def test_a_re_render_after_switching_still_draws_person_cards(ran):
    """Drawn through the company card, every one of these became "Unknown"."""
    assert "Binal Shah" in ran["html_after_switch"]
    assert "Unknown" not in ran["html_after_switch"]


def test_load_more_stops_offering_to_continue_the_wrong_search(ran):
    assert ran["load_more_after_switch"] == "none"


# ── The page's own claims about the rows ─────────────────────────────────────

def test_a_company_that_grew_150_percent_says_so(ran):
    """1.5 is the fraction Apollo sends for a company that grew 150%. It used to
    print as "+1.5%"."""
    assert "+150%" in ran["cards_html"]
    assert "+19%" in ran["cards_html"]
    assert "+1.5%" not in ran["cards_html"]


def test_the_selection_button_names_the_page_it_selects(ran):
    assert ran["select_all_label"].strip() == "Select these 2"


def test_the_count_line_reports_the_page_and_the_total(ran):
    assert "Showing <b>2</b> of <b>79K</b>" in ran["count_html"]


def test_no_card_reaches_out_to_a_third_party(ran):
    assert "google.com" not in ran["cards_html"]
    assert "favicon" not in ran["cards_html"]


# ── An empty page explains itself ────────────────────────────────────────────

def test_an_empty_page_says_apollo_matched_when_it_did(ran):
    html = ran["empty_with_rejections"]
    # 20 (rejected_total), not 24 (Apollo's own grand total across all pages)
    # and not 26 (18+8, what summing the overlapping per-reason counts in
    # `rejected` would wrongly produce -- see the fixture above).
    assert "Apollo returned 20 people" in html
    assert "Apollo returned 24 people" not in html
    assert "Apollo returned 26 people" not in html
    assert "none of them matched" in html


def test_the_empty_page_breaks_the_removals_down_by_reason(ran):
    html = ran["empty_with_rejections"]
    assert "18 outside the industry" in html
    assert "8 wrong seniority" in html


def test_a_reason_that_removed_nothing_is_not_listed(ran):
    assert "too new in role" not in ran["empty_with_rejections"]


def test_the_reasons_are_ordered_worst_first(ran):
    html = ran["empty_with_rejections"]
    assert html.index("18 outside the industry") < html.index("8 wrong seniority")


def test_a_real_no_match_still_says_no_match(ran):
    assert "No matches. Try widening the filters." in ran["empty_plain"]
    assert "Apollo returned" not in ran["empty_plain"]
