"""The pinned company, on the client, executed rather than read.

The client pins whatever company the server resolved and re-sends it with every
later turn, which is what makes "and their VP of Sales?" work without naming the
company again. Nothing ever un-pinned it. So the server dropping a stale company
for one answer fixes one answer: the client hands the same company straight back
on the following question, and the conversation is stuck on it again.

The server now says `clear_context` on the turn it dropped one. That the client
acts on it is a behavioural claim about the bundle, and a text assertion cannot
tell a working guard from `if(false)`, so this drives the real bundle over three
turns through the same window.cpiSendChat the send button calls, and reads the
request bodies it produced.

Skipped, not failed, where node is unavailable: the server half is covered by
test_cpi_chat_subject.py either way.
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

function makeEl(tag){
  const el = {
    tagName: tag || "div", _html: "", value: "", checked: false, disabled: false,
    textContent: "", style: {}, options: [], dataset: {}, _on: {},
    scrollTop: 0, scrollHeight: 0,
    classList: {
      _s: new Set(),
      add(c){ this._s.add(c); }, remove(c){ this._s.delete(c); },
      contains(c){ return this._s.has(c); },
      toggle(c, on){ if(on === undefined) on = !this._s.has(c);
                     if(on) this._s.add(c); else this._s.delete(c); return on; },
    },
    getAttribute(){ return null; }, setAttribute(){}, remove(){},
    appendChild(){}, removeChild(){}, insertAdjacentHTML(pos, html){ el._html += html; },
    addEventListener(ev, fn){ (this._on[ev] = this._on[ev] || []).push(fn); },
    fire(ev, arg){ (this._on[ev] || []).forEach(function(f){ f(arg || {}); }); },
    querySelectorAll(){ return []; }, querySelector(){ return null; },
    closest(){ return null; }, scrollIntoView(){}, focus(){}, click(){},
    getBoundingClientRect(){ return {top:0,left:0,bottom:30,right:200,
                                     width:200,height:30}; },
  };
  el.lastChild = { textContent: "" };
  el.lastElementChild = { querySelectorAll(){ return []; } };
  Object.defineProperty(el, "innerHTML", {
    get(){ return el._html; }, set(v){ el._html = String(v); },
  });
  return el;
}

const IDS = ["cpiChatInput", "cpiChatSend", "cpiChatBody", "cpiToast",
             "cpiResultsWrap", "cpiFiltersPeople", "cpiFiltersCompanies"];
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
global.__CPI_CHAT_URL__ = "/chat";
global.__CPI_USER_NAME__ = "T";

// One canned reply per turn, and every CHAT request body kept so the next turn's
// body can be inspected for what the client decided to carry forward. Filtered
// by URL because the bundle also fires its own requests on load (the spend pill,
// the page-view ping), and those are not turns in this conversation.
const SENT = [];
let REPLIES = [];
global.fetch = function(url, opts){
  if (String(url).indexOf("/chat") !== -1) {
    SENT.push(JSON.parse((opts || {}).body || "{}"));
    const reply = REPLIES.shift() || {answer: "ok"};
    return Promise.resolve({ ok: true, json(){ return Promise.resolve(reply); } });
  }
  return Promise.resolve({ ok: true, json(){ return Promise.resolve({}); } });
};

eval(bundle);

const tick = () => new Promise(function(r){ setTimeout(r, 0); });

async function ask(text){
  els.cpiChatInput.value = text;
  els.cpiChatSend.disabled = false;
  window.cpiSendChat();
  for (let i = 0; i < 8; i++) await tick();
}

(async function(){
  REPLIES = [
    // 1. A company question. The server resolved Snowflake, so the client pins it.
    {answer: "Snowflake is a cloud data platform.",
     context: {org_id: "org-snow", name: "Snowflake", domain: "snowflake.com"}},
    // 2. The reported second question. The server dropped the stale pin.
    {answer: "Here are VPs of Sales at healthcare companies in Texas.",
     clear_context: true},
    // 3. Another population question, to see what the client carried forward.
    {answer: "Here are VPs of Sales at fintech companies."},
  ];

  await ask("Tell me about Snowflake");
  await ask("List VPs of Sales at healthcare companies in Texas");
  await ask("List VPs of Sales at fintech companies");

  console.log(JSON.stringify({
    sent: SENT.map(function(b){
      return {message: b.message, context_org_id: b.context_org_id,
              context_name: b.context_name, context_domain: b.context_domain};
    }),
    turns: SENT.length,
  }));
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


def test_all_three_turns_were_sent(out):
    assert out["turns"] == 3


def test_the_first_question_carries_no_company(out):
    """Nothing is pinned yet, so there is nothing to inherit."""
    assert out["sent"][0]["context_org_id"] == ""


def test_the_second_question_still_carries_the_pin(out):
    """The client cannot know the subject changed: it sends what it has, and the
    server is the one that decides the company no longer applies. This is pinned
    so the fix stays on the server, where the parsed filters actually are."""
    assert out["sent"][1]["context_org_id"] == "org-snow"
    assert out["sent"][1]["context_name"] == "Snowflake"


def test_the_third_question_no_longer_carries_it(out):
    """The whole point. Before this, the pin outlived the subject and the next
    question put the same company straight back on the wire."""
    assert out["sent"][2]["context_org_id"] == ""
    assert out["sent"][2]["context_name"] == ""
    assert out["sent"][2]["context_domain"] == ""
