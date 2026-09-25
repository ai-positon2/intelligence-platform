"""The lead-form funnel on /p2/admin/anonymous-traffic reads the "Form Stage"
column that static/js/visitor_track.js beacons. These tests execute the real
tracker in Node against a minimal DOM stand-in and read the beaconed payload,
so they measure what the dashboard will actually receive."""
import json
import os
import shutil
import subprocess

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TRACKER = os.path.join(_ROOT, "static", "js", "visitor_track.js")
_AGENTS = os.path.join(_ROOT, "templates", "agents.html")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

_HARNESS = r"""
const vm = require("vm");
const fs = require("fs");
// node -e has no script-path slot, so the first real argument is argv[1].
const src = fs.readFileSync(process.argv[1], "utf8");
const steps = JSON.parse(process.argv[2]);

function matchOne(el, sel) {
  sel = sel.trim();
  const m = sel.match(/^([a-z]*)(?:#([\w-]+))?(?:\.([\w-]+))?(?:\[([\w-]+)(?:='([^']*)')?\])?$/);
  if (!m) throw new Error("harness cannot parse selector: " + sel);
  const [, tag, id, cls, attr, val] = m;
  if (tag && el.tag !== tag) return false;
  if (id && el.attrs.id !== id) return false;
  if (cls && !(el.attrs.class || "").split(" ").includes(cls)) return false;
  if (attr && !(attr in el.attrs)) return false;
  if (val !== undefined && el.attrs[attr] !== val) return false;
  return true;
}
function mk(tag, attrs, parent) {
  const el = { tag, attrs: attrs || {}, parent: parent || null };
  el.getAttribute = (k) => (k in el.attrs ? el.attrs[k] : null);
  el.closest = (sel) => {
    for (let n = el; n; n = n.parent)
      if (sel.split(",").some((s) => matchOne(n, s))) return n;
    return null;
  };
  return el;
}
const body = mk("body");
const dom = {
  demo: mk("button", { "data-demo": "", "data-interest": "Build a custom agent" }, body),
  fov: null, form: null, name: null, outside: null,
};
dom.fov = mk("div", { id: "nvfov", class: "nv-fov" }, body);
dom.form = mk("form", { id: "nvDemoForm" }, dom.fov);
dom.name = mk("input", { id: "nvN" }, dom.form);
dom.outside = mk("input", { id: "dirSearch", type: "search" }, body);

const winL = {}, docL = {};
const on = (bag) => (type, fn) => { (bag[type] = bag[type] || []).push(fn); };
const store = () => { const m = {}; return {
  getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); },
  removeItem: (k) => { delete m[k]; }, clear: () => { for (const k in m) delete m[k]; } }; };
const beacons = [];
const document = {
  addEventListener: on(docL), cookie: "", referrer: "", title: "Test",
  visibilityState: "visible", readyState: "complete",
  documentElement: { scrollHeight: 1000 }, body: { scrollHeight: 1000 },
  querySelectorAll: () => [],
};
const sandbox = {
  document, navigator: { language: "en" }, location: {
    pathname: "/", search: "?p2geo=off", hostname: "intelligence.position2.com" },
  localStorage: store(), sessionStorage: store(), screen: { width: 1, height: 1 },
  innerWidth: 1, innerHeight: 1, scrollY: 0, URL, URLSearchParams, crypto,
  setInterval: () => 0, setTimeout: () => 0, clearTimeout: () => {},
  fetch: (url, opts) => { beacons.push(JSON.parse(opts.body)); return Promise.resolve(); },
  addEventListener: on(winL),
};
sandbox.window = sandbox;
vm.runInNewContext(src, sandbox);

function fire(type, target, extra) {
  const ev = Object.assign({ target, clientX: 0, clientY: 0 }, extra || {});
  (winL[type] || []).forEach((f) => f(ev));
  (docL[type] || []).forEach((f) => f(ev));
}
for (const [type, key] of steps) {
  if (type === "hide" || type === "show") {
    document.visibilityState = type === "hide" ? "hidden" : "visible";
    fire("visibilitychange", document);
  } else fire(type, key ? dom[key] : document);
}
fire("pagehide", document);
process.stdout.write(JSON.stringify(beacons));
"""


def _beacons(steps):
    out = subprocess.run(["node", "-e", _HARNESS, _TRACKER, json.dumps(steps)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _run(steps):
    return _beacons(steps)[-1]


def test_opening_the_form_is_only_open_even_though_its_first_field_gets_focus():
    # agents.html focuses #nvN 360ms after the form opens, so the visitor
    # "focuses" a field without doing anything. That must not count as started.
    beacon = _run([["click", "demo"], ["focusin", "name"]])
    assert beacon["form"] == "open"


def test_typing_in_a_lead_form_field_is_started():
    beacon = _run([["click", "demo"], ["focusin", "name"], ["input", "name"]])
    assert beacon["form"] == "started"


def test_a_change_event_alone_also_counts_as_started():
    beacon = _run([["click", "demo"], ["change", "name"]])
    assert beacon["form"] == "started"


def test_submit_is_the_furthest_stage_and_is_not_downgraded_by_later_input():
    beacon = _run([["click", "demo"], ["input", "name"], ["p2:lead_submit", None],
                   ["input", "name"]])
    assert beacon["form"] == "submitted"


def test_typing_outside_the_lead_form_does_not_start_it():
    beacon = _run([["click", "demo"], ["input", "outside"]])
    assert beacon["form"] == "open"


def test_a_session_that_never_opens_the_form_reports_no_stage():
    beacon = _run([["input", "outside"]])
    assert beacon["form"] == ""


def test_the_click_is_recorded_under_the_buttons_interest_label():
    beacon = _run([["click", "demo"]])
    assert beacon["cta"] == {"lead:Build a custom agent": 1}


def test_the_lead_form_still_autofocuses_so_the_focus_trap_stays_relevant():
    # If this ever stops being true the input/change rule is still correct,
    # but the reason for it (and the first test above) should be revisited.
    html = open(_AGENTS, encoding="utf-8").read()
    assert "document.getElementById('nvN'); if(n) n.focus();" in html


def test_the_tracker_script_is_cache_busted_past_the_focusin_version():
    html = open(_AGENTS, encoding="utf-8").read()
    assert "filename='js/visitor_track.js') }}?v=7" in html


def test_a_visitor_who_tabs_away_mid_form_and_comes_back_is_recorded_as_submitted():
    sent = _beacons([["click", "demo"], ["input", "name"], ["hide", None], ["show", None],
                     ["input", "name"], ["p2:lead_submit", None]])
    assert [b["form"] for b in sent] == ["started", "submitted"]
    assert len({b["pvid"] for b in sent}) == 1
    assert [b["seq"] for b in sent] == [1, 2]
    assert sent[-1]["clicks"] == 1 and sent[-1]["cta"] == {"lead:Build a custom agent": 1}


def test_hidden_then_pagehide_with_nothing_new_sends_one_snapshot():
    assert len(_beacons([["click", "demo"], ["hide", None]])) == 1


def test_each_page_load_gets_its_own_page_view_id():
    a, b = _run([["click", "demo"]]), _run([["click", "demo"]])
    assert a["pvid"] and b["pvid"] and a["pvid"] != b["pvid"]
