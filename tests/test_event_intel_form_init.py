"""The first-run path: a brand new account with nothing saved yet.

This is the state every user is in exactly once, and it was broken. With no
saved profiles the client-profile select's only option is "+ Lock a new client
profile", so its `change` event never fires, so the intake form stayed hidden,
so the only visible control refused the run and pointed at a form that was not
on screen. Nothing in the suite noticed, because every unit test constructed
its own profile and every route test posted one directly.

These tests EXECUTE the page's real script in node against a DOM shim shaped
like that first run, rather than reading the source for the fix. A grep would
pass against a call that is never reached.
"""

import json
import re
import subprocess
import shutil

import pytest

import app as appmod

_PAGE = "/p2/b2b-agents/event-conference-intelligence"
_IIFE_CLOSE = "\n  })();"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to execute the page script")


def _page_script():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "harness@position2.com", "name": "T"}
    resp = c.get(_PAGE)
    assert resp.status_code == 200, "the page did not render (%s)" % resp.status_code
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                        resp.get_data(as_text=True), re.S)
    assert blocks, "the page has no inline script"
    return blocks[0]


# A DOM shaped like a first run: one profile option ("new"), no saved rosters,
# and both radiogroups rendering their first card as aria-checked, exactly as
# the template does.
_SHIM = """
var __state = {};
function __node(attrs){
  var a = attrs || {};
  return {
    _a: a, style: {}, value: a.value || '', disabled: false,
    textContent: '', innerHTML: '', firstChild: null, children: [],
    getAttribute: function(k){ return this._a[k] === undefined ? null : this._a[k]; },
    setAttribute: function(k, v){ this._a[k] = String(v); },
    classList: {add:function(){},remove:function(){},toggle:function(){},
                contains:function(){return false;}},
    focus: function(){ __state.focused = this._a.id || true; },
    scrollIntoView: function(){ __state.scrolled = true; },
    appendChild: function(){}, insertBefore: function(){},
    addEventListener: function(){}
  };
}

var __els = {
  profilePick: __node({id: 'profilePick', value: 'new'}),
  profileForm: __node({id: 'profileForm'}),
  clientName:  __node({id: 'clientName'}),
  formError:   __node({id: 'formError'}),
  runBtn:      __node({id: 'runBtn'}),
  runningWrap: __node({id: 'runningWrap'}),
  runningText: __node({id: 'runningText'}),
  sourceRun:   __node({id: 'sourceRun', value: ''}),
  wrProfile:   __node({id: 'wrProfile', value: ''})
};
// The template starts profileForm hidden.
__els.profileForm.style.display = 'none';

var __classCards = CLASS_KEYS.map(function(k, i){
  return __node({'data-classification': k, 'aria-checked': i === 0 ? 'true' : 'false'});
});
var __eventCards = EVENT_KEYS.map(function(k, i){
  return __node({'data-eventclass': k, 'aria-checked': i === 0 ? 'true' : 'false'});
});

function __match(sel, cards, attr){
  if (sel.indexOf(attr) === -1) return [];
  if (sel.indexOf('aria-checked="true"') !== -1) {
    return cards.filter(function(c){ return c.getAttribute('aria-checked') === 'true'; });
  }
  return cards;
}

global.document = {
  readyState: 'complete',
  getElementById: function(id){ return __els[id] || __node({id: id}); },
  querySelectorAll: function(sel){
    return __match(sel, __classCards, 'data-classification')
       .concat(__match(sel, __eventCards, 'data-eventclass'));
  },
  querySelector: function(sel){
    var m = document.querySelectorAll(sel);
    return m.length ? m[0] : null;
  },
  createElement: function(){ return __node({}); },
  addEventListener: function(){}
};
global.window = {addEventListener: function(){}};
global.location = {hash: '', pathname: '/'};
global.fetch = function(){
  return Promise.resolve({ok: true, json: function(){ return Promise.resolve({}); }});
};
global.setInterval = function(){ return 0; };
global.clearInterval = function(){};
"""


def _run(probe, class_keys, event_keys):
    script = _page_script()
    assert _IIFE_CLOSE in script, (
        "the page's IIFE no longer closes with a two-space-indented `})();`")
    at = script.index(_IIFE_CLOSE)
    # Spliced INSIDE the IIFE and AFTER the init block, so it observes the
    # state the browser is left in once the page has loaded.
    script = script[:at] + "\n" + probe + script[at:]
    js = ("var CLASS_KEYS = %s;\nvar EVENT_KEYS = %s;\n%s\n%s"
          % (json.dumps(class_keys), json.dumps(event_keys), _SHIM, script))
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, "the page script threw:\n%s" % r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def keys():
    from tracker import event_intel_rubric, event_intel_workroom
    return (list(event_intel_rubric.CLASSIFICATIONS),
            list(event_intel_workroom.EVENT_CLASSES))


def test_the_intake_form_is_open_on_a_first_run(keys):
    """With nothing saved, the select's only option is "new" and `change`
    never fires. If nothing opens the form on load, the page shows a button
    that refuses and a form that is not there."""
    out = _run("console.log(JSON.stringify({display: "
               "document.getElementById('profileForm').style.display}));", *keys)
    assert out["display"] == "", \
        "the profile intake form is still hidden on a first run"


def test_the_visible_classification_selection_is_the_one_the_script_holds(keys):
    """The template renders the first card aria-checked, so the page SHOWS a
    selection. If the script's own variable is null, saving fails on a
    classification the user can see is highlighted."""
    out = _run("console.log(JSON.stringify({picked: pickedClass}));", *keys)
    assert out["picked"] == keys[0][0], \
        "pickedClass is %r while the first card renders as checked" % out["picked"]


def test_the_visible_event_class_selection_is_the_one_the_script_holds(keys):
    out = _run("console.log(JSON.stringify({picked: pickedEventClass}));", *keys)
    assert out["picked"] == keys[1][0], \
        "pickedEventClass is %r while the first card renders as checked" % out["picked"]


def test_choosing_a_saved_profile_closes_the_intake_form(keys):
    """The other direction. Opening it on load must not pin it open."""
    out = _run("document.getElementById('profilePick').value = '7';"
               "onProfilePick();"
               "console.log(JSON.stringify({display: "
               "document.getElementById('profileForm').style.display}));", *keys)
    assert out["display"] == "none"


def test_running_without_a_locked_profile_lands_the_cursor_in_the_form(keys):
    """A refusal that does not show the way out is a dead end with a
    paragraph attached."""
    out = _run(
        "setMode('recommend');"
        "startRun({preventDefault: function(){}});"
        "console.log(JSON.stringify({"
        "  display: document.getElementById('profileForm').style.display,"
        "  focused: __state.focused || null,"
        "  error: document.getElementById('formError').textContent}));", *keys)
    assert out["display"] == "", "the refusal left the intake form hidden"
    assert out["focused"] == "clientName", "the refusal did not put the cursor in the form"
    assert "Lock this profile" in out["error"], \
        "the refusal does not name the control that fixes it: %r" % out["error"]


def test_the_selection_is_read_back_from_the_card_that_is_checked(keys):
    """Not from the first card. Those are the same thing on load and different
    the moment anyone clicks, and the difference is what makes the fallback in
    startRun correct rather than coincidentally right."""
    classes, events = keys
    assert len(classes) > 1 and len(events) > 1, "need more than one card to tell these apart"
    out = _run(
        "pickClass(%s); pickEventClass(%s);"
        "console.log(JSON.stringify({"
        "  cls: checkedValue('data-classification'),"
        "  ev: checkedValue('data-eventclass')}));"
        % (json.dumps(classes[-1]), json.dumps(events[-1])), *keys)
    assert out["cls"] == classes[-1], \
        "read back %r after selecting %r" % (out["cls"], classes[-1])
    assert out["ev"] == events[-1], \
        "read back %r after selecting %r" % (out["ev"], events[-1])


def test_the_workroom_refusal_names_the_missing_roster(keys):
    out = _run(
        "setMode('workroom');"
        "startRun({preventDefault: function(){}});"
        "console.log(JSON.stringify({error: "
        "document.getElementById('formError').textContent}));", *keys)
    assert "roster" in out["error"].lower(), out["error"]
