"""The vocabulary pickers, executed rather than read.

Reported: the location picker "only shows locations till Czech Republic and not
the entire list", and other filters stop the same way. Czech Republic was the
40th location alphabetically and the picker returned 40 entries, so the list was
a hard alphabetical stop: 164 of the 204 places this app knows could not be
browsed to at all, and the same cap hid 107 of Apollo's 147 industries, 81 NAICS
codes, 95 SIC codes and 128 technologies. A list that simply stops reads as the
end of the vocabulary rather than the end of one page of it, which is this
codebase's recurring defect: a surface asserting something its data does not
support.

Two claims are behavioural and cannot be checked by reading the bundle, since
`if(false)` would still contain the text a grep looks for, so they run the real
bundle against a DOM shim and read what it produced:

  1. Opening a picker renders every entry the server returned, not a prefix.
  2. When the server does cap the list, the picker says so, and it still says so
     when the same list is served from the client's own cache.

Skipped, not failed, where node is unavailable: this is extra assurance over the
text and endpoint assertions in test_cpi_picker_completeness.py, not a
replacement for them.
"""

import json
import os
import shutil
import subprocess
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = os.path.join(_ROOT, "static", "js", "company_people_intelligence.js")

# Only the combobox surface: the input, its list, and the chip strip. Everything
# else is null, which the bundle already tolerates because the same code serves
# both tabs and neither has every control.
_DRIVER = r"""
const fs = require("fs");
const bundle = fs.readFileSync(process.argv[2], "utf8");

function makeEl(tag){
  const el = {
    tagName: tag || "div", _html: "", value: "", checked: false,
    textContent: "", style: {}, options: [], dataset: {}, _on: {},
    classList: {
      _s: new Set(),
      add(c){ this._s.add(c); }, remove(c){ this._s.delete(c); },
      contains(c){ return this._s.has(c); },
      toggle(c, on){ if(on === undefined) on = !this._s.has(c);
                     if(on) this._s.add(c); else this._s.delete(c); return on; },
    },
    getAttribute(){ return null; }, setAttribute(){}, remove(){},
    appendChild(){}, removeChild(){},
    addEventListener(ev, fn){ (this._on[ev] = this._on[ev] || []).push(fn); },
    fire(ev, arg){ (this._on[ev] || []).forEach(function(f){ f(arg || {}); }); },
    querySelectorAll(){ return []; }, querySelector(){ return null; },
    closest(){ return null; }, scrollIntoView(){}, focus(){}, click(){},
    getBoundingClientRect(){ return {top:100,left:0,bottom:130,right:200,
                                     width:200,height:30}; },
  };
  el.lastChild = { textContent: "" };
  Object.defineProperty(el, "innerHTML", {
    get(){ return el._html; }, set(v){ el._html = String(v); },
  });
  return el;
}

// fpLocation is the combo the bug was reported against.
const IDS = ["fpLocation", "fpLocationList", "fpLocationChips", "fpLocationCombo",
             "cpiToast", "cpiResultsWrap", "cpiFiltersPeople", "cpiFiltersCompanies"];
const els = {};
IDS.forEach(function(id){ els[id] = makeEl(); });

global.window = global;
global.addEventListener = function(){};
global.matchMedia = function(){ return { matches: false, addEventListener(){} }; };
global.requestAnimationFrame = function(cb){ return setTimeout(cb, 0); };
global.getComputedStyle = function(){ return {}; };
global.innerWidth = 1440; global.innerHeight = 900;
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
global.setTimeout = setTimeout;
global.__CPI_HISTORY_URL__ = "/history";
global.__CPI_VOCAB_URL__ = "/vocab";
global.__CPI_INDUSTRIES_URL__ = "/industries";

// Programmable vocabulary endpoint. VOCAB is the next response; URLS records
// every request so a cache hit is distinguishable from a second fetch.
let VOCAB = {};
const URLS = [];
global.fetch = function(url){
  URLS.push(String(url));
  return Promise.resolve({ ok: true, json(){ return Promise.resolve(VOCAB); } });
};

eval(bundle);

const tick = () => new Promise(function(r){ setTimeout(r, 0); });

// The real focus listener initCombo attached, which is what a user opening the
// picker triggers. Driving this rather than an internal function keeps the test
// honest about the IIFE: only what the page itself can reach is reachable here.
async function openPicker(typed){
  els.fpLocation.value = typed || "";
  els.fpLocation.fire("focus");
  for (let i = 0; i < 6; i++) await tick();
  return els.fpLocationList.innerHTML;
}
function entries(n, prefix){
  const out = [];
  for (let i = 0; i < n; i++) out.push({ value: (prefix || "Place ") + i,
                                         kind: "location", confirmed: false,
                                         covers: [], note: "" });
  return out;
}

(async function(){
  const out = {};

  // 1. A full, uncapped list: every entry the server sent must be rendered.
  VOCAB = { entries: entries(203), total: 203, truncated: false };
  out.full_html = await openPicker("");
  out.full_options = (out.full_html.match(/data-combo=/g) || []).length;

  // 2. A capped list must say it is capped.
  VOCAB = { entries: entries(300), total: 412, truncated: true };
  out.capped_html = await openPicker("a");
  out.capped_options = (out.capped_html.match(/data-combo=/g) || []).length;

  // 3. Re-opening the SAME query is served from the client cache. The cap notice
  //    has to survive that, or the second look claims to be the whole list.
  const before = URLS.length;
  out.cached_html = await openPicker("a");
  out.refetched = URLS.length > before;

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


# ── The reported bug ─────────────────────────────────────────────────────────

def test_every_entry_the_server_returned_is_rendered(out):
    """The whole list, not a prefix of it. 203 locations went in, so 203 options
    have to come out: rendering 40 of them is what made the picker stop at
    "Czech Republic"."""
    assert out["full_options"] == 203


def test_a_complete_list_says_nothing_about_being_capped(out):
    """The notice must describe reality rather than appear by default."""
    assert "Showing the first" not in out["full_html"]


# ── When the cap really is hit ───────────────────────────────────────────────

def test_a_capped_list_says_how_much_it_is_showing(out):
    assert "Showing the first 300 of 412 locations" in out["capped_html"]


def test_a_capped_list_still_renders_its_whole_page(out):
    assert out["capped_options"] == 300


def test_the_cap_notice_is_not_selectable(out):
    """It is a statement about the list, not a value that can be chosen: the
    option handler keys off .cpi-opt, so the notice must not carry that class."""
    assert 'class="cpi-opt cpi-opt-more"' not in out["capped_html"]
    assert "cpi-opt-none cpi-opt-more" in out["capped_html"]


def test_the_cap_notice_survives_the_client_cache(out):
    """Re-opening the same query is answered from COMBO_CACHE without a second
    request. Caching only the entries dropped the notice, so the second look at
    a capped list silently claimed to be the whole vocabulary."""
    assert out["refetched"] is False, "the second open should hit the cache"
    assert "Showing the first 300 of 412 locations" in out["cached_html"]
