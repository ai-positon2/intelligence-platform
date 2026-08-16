"""Audit of "Fill filters", the sentence-to-filter-panel feature on Contact
Finder's People tab.

Reported live against "find me top executives in tech industry in san
francisco in companies with employees more than 500": the panel filled
EMPLOYEES as "5,001+" (not the ~500 asked for), added a KEYWORDS chip of "top
executives", and the resulting search matched nothing.

Two separate defects, each capable of emptying that search on its own, and
together they explain both symptoms in the report -- the visibly wrong filter
AND the zero results:

  1. snapEmployeeBucket (company_people_intelligence.js) picks the Apollo
     headcount bucket by raw interval-overlap width. For an OPEN-ENDED query
     (a min with no max -- exactly what "more than 500" parses to), the
     top-most bucket ("5,001+") has no upper bound either, so its overlap with
     the query is infinite and it wins the comparison against every other
     bucket no matter how small the actual number asked for was. Every
     "X+ employees" / "more than X" query was landing on 5,001+ regardless of
     X, and that wrong bucket is what was actually sent to Apollo.

  2. _CPI_INTENT_SYSTEM never explained what belongs in `keywords`, so the
     parser would sometimes echo the user's own wording ("top executives",
     "decision makers") into `keywords` on top of correctly mapping it to
     `seniorities`. Apollo's keyword filter (q_keywords) is a literal text
     match against free-text fields, not a role match, and no real person's
     title reads "top executives" -- ANDed against the (correct) seniority
     filter, that keyword guaranteed zero rows. _cpi_filters_from_intent now
     drops a keywords value that is nothing but a restatement of a
     seniority/role word, as a deterministic backstop independent of whatever
     the model does; the prompt was also given explicit guidance on what
     keywords is and is not for, and on defaulting an ambiguous place mention
     to person_locations rather than silently dropping it.
"""

import json
import os
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

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = os.path.join(_ROOT, "static", "js", "company_people_intelligence.js")


# ── _cpi_filters_from_intent: the generic-keyword guard ──────────────────────

def test_a_bare_seniority_word_is_dropped_from_keywords():
    out = appmod._cpi_filters_from_intent({
        "intent": "people_list", "seniorities": ["c_suite", "vp", "director"],
        "keywords": "top executives",
    })
    assert "keywords" not in out
    assert out["seniorities"] == ["c_suite", "vp", "director"]


def test_the_guard_is_case_and_whitespace_insensitive():
    out = appmod._cpi_filters_from_intent({
        "intent": "people_list", "keywords": "  Decision Makers  ",
    })
    assert "keywords" not in out


def test_a_real_keyword_survives():
    """The guard must only catch a bare restatement of a role, not any keyword
    that happens to share a word with one."""
    out = appmod._cpi_filters_from_intent({
        "intent": "people_list", "keywords": "keto",
    })
    assert out["keywords"] == "keto"


def test_a_phrase_that_merely_contains_a_stoplist_word_survives():
    """"leadership" alone is dropped; a real, more specific phrase built around
    it is not the same value and must not be caught by a substring match."""
    out = appmod._cpi_filters_from_intent({
        "intent": "people_list", "keywords": "thought leadership content",
    })
    assert out["keywords"] == "thought leadership content"


def test_the_guard_also_applies_when_keywords_comes_back_as_a_list():
    out = appmod._cpi_filters_from_intent({
        "intent": "people_list", "keywords": ["leaders", "keto"],
    })
    assert out["keywords"] == ["keto"]


def test_a_list_of_only_generic_words_is_dropped_entirely():
    out = appmod._cpi_filters_from_intent({
        "intent": "people_list", "keywords": ["executives", "leadership"],
    })
    assert "keywords" not in out


def test_the_reported_query_no_longer_sets_a_self_defeating_keyword():
    """The exact intent shape reported live: seniorities correctly extracted,
    but keywords also echoing the same ask in words Apollo cannot match."""
    out = appmod._cpi_filters_from_intent({
        "intent": "people_list",
        "titles": [], "seniorities": ["c_suite", "vp", "director"],
        "industries": ["tech"], "keywords": "top executives",
        "employees": {"min": 500},
    })
    assert "keywords" not in out
    assert out["seniorities"] == ["c_suite", "vp", "director"]
    assert out["industries"] == ["tech"]
    assert out["employee_min"] == 500


# ── The parse-query route wires the guard in ─────────────────────────────────

_PARSE = "/p2/b2b-agents/company-people-intelligence/parse-query"


@pytest.fixture
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    return c


def test_the_route_never_returns_a_generic_keyword(client, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setattr(appmod, "_cpi_oai", lambda: object())
    monkeypatch.setattr(appmod, "_vimi_chat_json", lambda oai, msgs, mt: (
        json.dumps({"intent": "people_list", "seniorities": ["c_suite"],
                    "keywords": "top executives"}), "m"))
    body = client.post(_PARSE, json={"q": "top executives"}).get_json()
    assert "keywords" not in body["filters"]
    assert body["filters"]["seniorities"] == ["c_suite"]


# ── snapEmployeeBucket, exercised through the real bundle ────────────────────
#
# The bundle wraps everything in an IIFE and exposes only window.cpi*, so the
# bucket-snapping function itself cannot be called directly -- it is driven the
# same way the browser drives it, through window.cpiParseQuery(), with fetch
# stubbed to hand back the filters an intent parse would have produced.

_DRIVER = r"""
const fs = require("fs");
const bundle = fs.readFileSync(process.argv[2], "utf8");

function makeEl(tag, id){
  const el = {
    tagName: tag || "div", id: id || "", _html: "", value: "", checked: false,
    disabled: false, textContent: "", title: "", style: {}, options: [],
    dataset: {}, _on: {}, _kids: [],
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
    addEventListener(){}, querySelectorAll(){ return []; }, querySelector(){ return null; },
    closest(){ return null; }, scrollIntoView(){}, focus(){}, click(){},
  };
  Object.defineProperty(el, "innerHTML", {
    get(){ return el._html; }, set(v){ el._html = String(v); },
  });
  return el;
}

const IDS = ["fpTitles","fpCompanyDomain","fpKeywords","fpEmpRange","fcEmpRange",
  "cpiQbar","cpiLiveCount","cpiLiveCountCo","cpiToast","cpiAskInput","cpiAskBtn",
  "cpiAskNote","fpSeniority","fpEmailStatus","fpCompanyDetail","fcName",
  "fpAdvanced","fpMoreBtn","fcAdvanced","fcMoreBtn"];
const els = {};
IDS.forEach(id => { els[id] = makeEl("div", id); });
els.fpCompanyDetail.checked = true;
const EMP_OPTIONS = [
  {value:""}, {value:"1,10"}, {value:"11,50"}, {value:"51,200"},
  {value:"201,500"}, {value:"501,1000"}, {value:"1001,5000"}, {value:"5001,"}
];
els.fpEmpRange.options = EMP_OPTIONS.map(o => ({value:o.value}));
els.fcEmpRange.options = EMP_OPTIONS.map(o => ({value:o.value}));

global.window = global;
global.addEventListener = function(){};
global.matchMedia = () => ({ matches:false, addEventListener(){} });
global.requestAnimationFrame = cb => setTimeout(cb, 0);
global.getComputedStyle = () => ({});
global.innerWidth = 1440; global.innerHeight = 900;
global.confirm = () => true;
global.document = {
  getElementById(id){ return els[id] || null; },
  querySelectorAll(){ return []; },
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

let PARSE_REPLY = { filters: {} };
global.fetch = function(url){
  const u = String(url);
  let payload = {};
  if (u.indexOf("/parse") >= 0) payload = PARSE_REPLY;
  else if (u.indexOf("/count") >= 0) payload = { count: null };
  else if (u.indexOf("/credits") >= 0) payload = { available:false };
  return Promise.resolve({ ok:true, json(){ return Promise.resolve(payload); } });
};

eval(bundle);

const settle = ms => new Promise(r => setTimeout(r, ms === undefined ? 60 : ms));

(async function(){
  const out = {};

  // Open-ended, small: "more than 50 employees" must not land on 5,001+.
  PARSE_REPLY = { filters: { employee_min: 50 } };
  els.cpiAskInput.value = "more than 50 employees";
  window.cpiParseQuery();
  await settle();
  out.openSmall = els.fpEmpRange.value;

  // Open-ended, the value reported live: "employees more than 500".
  PARSE_REPLY = { filters: { employee_min: 300 } };
  els.cpiAskInput.value = "employees more than 300";
  window.cpiParseQuery();
  await settle();
  out.openMid = els.fpEmpRange.value;

  // Open-ended, genuinely large: 5,001+ must still be reachable when it is
  // actually the right answer, not just avoided reflexively.
  PARSE_REPLY = { filters: { employee_min: 6000 } };
  els.cpiAskInput.value = "more than 6000 employees";
  window.cpiParseQuery();
  await settle();
  out.openLarge = els.fpEmpRange.value;

  // Bounded range, the pre-existing documented case: "50 to 200 people".
  PARSE_REPLY = { filters: { employee_min: 50, employee_max: 200 } };
  els.cpiAskInput.value = "50 to 200 people";
  window.cpiParseQuery();
  await settle();
  out.bounded = els.fpEmpRange.value;

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


def test_more_than_50_does_not_snap_to_the_top_bucket(out):
    assert out["openSmall"] == "11,50"


def test_more_than_300_does_not_snap_to_the_top_bucket(out):
    """The value actually reported live (500) falls exactly on a bucket
    boundary; 300 pins the same bug unambiguously."""
    assert out["openMid"] == "201,500"


def test_an_open_ended_query_that_really_is_top_bucket_still_lands_there(out):
    assert out["openLarge"] == "5001,"


def test_a_bounded_range_still_snaps_to_its_best_overlap(out):
    """Regression: the pre-existing documented behaviour for a query that
    states BOTH ends must be unaffected by the open-ended-query fix."""
    assert out["bounded"] == "51,200"
