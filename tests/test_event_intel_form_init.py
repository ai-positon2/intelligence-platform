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
# and both radiogroups rendering EVERY card aria-checked="false", which is
# what the template actually emits.
#
# It used to check the first card of each group and say in this comment that
# the template did too. That stopped being true when the template was fixed to
# pre-check nothing, and the two tests reading it back were left asserting
# that the page arrives with a selection it does not have. Both questions
# these groups ask, which side of the trade-show floor to score and what your
# relationship to an event was, are ones this agent refuses to answer for you,
# so "nothing is chosen yet" is the state worth pinning.
_SHIM = """
var __state = {};
function __node(attrs){
  var a = attrs || {};
  return {
    _a: a, style: {}, value: a.value || '', disabled: false,
    textContent: '', innerHTML: '', firstChild: null, children: [],
    // `hidden` and `className` are real properties the page sets on real
    // nodes. Without them here a test reads back undefined and passes on a
    // line that never ran.
    hidden: false, className: '',
    getAttribute: function(k){ return this._a[k] === undefined ? null : this._a[k]; },
    setAttribute: function(k, v){ this._a[k] = String(v); },
    removeAttribute: function(k){ delete this._a[k]; },
    querySelector: function(){ return null; },
    classList: {add:function(){},remove:function(){},toggle:function(){},
                contains:function(){return false;}},
    focus: function(){ __state.focused = this._a.id || true; },
    scrollIntoView: function(){ __state.scrolled = true; },
    appendChild: function(){}, insertBefore: function(){},
    addEventListener: function(){}
  };
}

/* Every id the page asks for resolves to the SAME node every time.

   The version of this shim that returned a fresh node for an unfamiliar id
   let a whole class of test pass vacuously: the script wrote to one object
   and the assertion read another, so the value was always the default and
   the default was always what the test expected. Memoising it means a test
   that reads back an unset value is looking at a line that genuinely never
   ran. */
var __made = {};
function __el(id){
  if (!__made[id]) __made[id] = __node({id: id});
  return __made[id];
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

function __cardNode(attr, key, checked){
  var n = __node({'aria-checked': checked ? 'true' : 'false'});
  n._a[attr] = key;
  // The suggestion slot the template renders inside every classification
  // card. A drafted classification writes its reasoning here and must NOT
  // touch aria-checked.
  n._cs = __node({});
  n._cs.hidden = true;
  n.querySelector = function(sel){ return sel === '.cs' ? this._cs : null; };
  return n;
}

var __classCards = CLASS_KEYS.map(function(k){
  return __cardNode('data-classification', k, false);
});
var __eventCards = EVENT_KEYS.map(function(k){
  return __cardNode('data-eventclass', k, false);
});

// The per-field evidence slots under each drafted input.
var __evNodes = {};
(typeof EV_FIELDS === 'undefined' ? [] : EV_FIELDS).forEach(function(f){
  var n = __node({'data-ev': f});
  n.hidden = true;
  __evNodes[f] = n;
});

/* Understands `[attr]`, `[attr="value"]` and the aria-checked filter, which
   is as much CSS as the page actually uses on these groups. Parsing the value
   matters: a matcher that returned every card for `[data-classification="x"]`
   handed back the FIRST card, so a test asserting on one specific card
   silently asserted on a different one. */
function __attrValue(sel, attr){
  var key = '[' + attr + '="';
  var at = sel.indexOf(key);
  if (at === -1) return null;
  var start = at + key.length, end = sel.indexOf('"', start);
  return end === -1 ? null : sel.slice(start, end);
}

function __match(sel, cards, attr){
  if (sel.indexOf(attr) === -1) return [];
  var want = __attrValue(sel, attr);
  var out = want === null ? cards
    : cards.filter(function(c){ return c.getAttribute(attr) === want; });
  if (sel.indexOf('aria-checked="true"') !== -1) {
    out = out.filter(function(c){ return c.getAttribute('aria-checked') === 'true'; });
  }
  return out;
}

function __matchEv(sel){
  var f = __attrValue(sel, 'data-ev');
  return (f && __evNodes[f]) ? [__evNodes[f]] : [];
}

// The `.cs` slots, reachable as a document-wide query the way clearDraftMarks
// reaches for them.
function __matchCs(sel){
  if (sel.indexOf('.cs') === -1) return [];
  if (sel.indexOf('data-classification') === -1) return [];
  return __classCards.map(function(c){ return c._cs; });
}

global.document = {
  readyState: 'complete',
  getElementById: function(id){ return __els[id] || __el(id); },
  querySelectorAll: function(sel){
    if (sel.indexOf('.cs') !== -1) return __matchCs(sel);
    // `data-ev="`, with the quote. `data-ev` alone is a prefix of
    // `data-eventclass`, so the shorter test routed the event-class group
    // into the evidence matcher and every event-class query came back empty.
    if (sel.indexOf('data-ev="') !== -1) return __matchEv(sel);
    // The mark a drafted classification leaves on its card. Only ever set on
    // a classification card, so it does not need the value parsing above.
    if (sel.indexOf('data-suggested') !== -1) {
      return __classCards.filter(function(c){
        return c.getAttribute('data-suggested') !== null; });
    }
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
// Overridable, so a test can stand in a draft reply. The default stays the
// empty-but-successful answer every existing test was written against.
var __fetchReply = {ok: true, body: {}};
global.fetch = function(){
  return Promise.resolve({
    ok: __fetchReply.ok,
    json: function(){ return Promise.resolve(__fetchReply.body); }
  });
};
global.setInterval = function(){ return 0; };
global.clearInterval = function(){};
"""


# The fields the draft may fill, named the way the page names them. Read off
# the module rather than retyped, so a field added there without a slot on the
# form fails here instead of silently never being shown.
def _ev_fields():
    from tracker import event_intel_intake
    return list(event_intel_intake.DRAFT_FIELDS)


_EV_FIELDS = _ev_fields()


def _run(probe, class_keys, event_keys):
    script = _page_script()
    assert _IIFE_CLOSE in script, (
        "the page's IIFE no longer closes with a two-space-indented `})();`")
    at = script.index(_IIFE_CLOSE)
    # Spliced INSIDE the IIFE and AFTER the init block, so it observes the
    # state the browser is left in once the page has loaded.
    script = script[:at] + "\n" + probe + script[at:]
    js = ("var CLASS_KEYS = %s;\nvar EVENT_KEYS = %s;\nvar EV_FIELDS = %s;\n%s\n%s"
          % (json.dumps(class_keys), json.dumps(event_keys),
             json.dumps(_EV_FIELDS), _SHIM, script))
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


def test_no_side_of_the_floor_is_chosen_before_anybody_chooses_one(keys):
    """The page must arrive with this question open.

    An earlier template pre-checked the first card, which meant every form
    nobody answered arrived answered, the guards downstream could not fire,
    and runs went at the wrong crowd. The template was fixed; this test was
    not, and went on asserting that the page loads with a selection, against a
    shim that supplied one. It now checks the thing that is actually true and
    actually matters.
    """
    out = _run("console.log(JSON.stringify({"
               "  picked: pickedClass,"
               "  checked: checkedValue('data-classification')}));", *keys)
    assert out["picked"] is None, (
        "the page loaded with a classification already chosen (%r)" % out["picked"])
    assert out["checked"] is None


def test_no_relationship_to_the_event_is_chosen_either(keys):
    """The same guarantee on the other group, where getting it wrong is worse:
    the first event class is "Our own event", so an unanswered form used to
    draft follow-ups to a competitor's guests in the voice of their host."""
    out = _run("console.log(JSON.stringify({"
               "  picked: pickedEventClass,"
               "  checked: checkedValue('data-eventclass')}));", *keys)
    assert out["picked"] is None
    assert out["checked"] is None


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


# ── drafting the profile from the client's own site ───────────────────────
#
# The form now offers to read the client's website and fill itself in. It is
# an accelerator, and the line it must not cross is doing the deciding.
#
# One answer matters more than the rest. `checkedValue` refuses to return a
# classification nobody chose, because a version of this form pre-checked the
# first card and every unanswered form therefore arrived answered, which sent
# runs at the wrong side of the trade-show floor. A drafted classification is
# a model's reading, and a model's reading that lands pre-accepted is the same
# defect with a better excuse.

_DRAFT_OK = {
    "draft": {"buyer_roles": "VP Claims, Head of Claims Ops",
              "verticals": "insurance, insurtech",
              "acv_band": None, "sales_cycle": None,
              "geo_scope": "North America"},
    "evidence": {"buyer_roles": "Their customers page names claims leaders.",
                 "verticals": "Every case study is an insurer.",
                 "geo_scope": "Offices listed in Boston and Toronto only."},
    "unknown": ["acv_band", "sales_cycle"],
    "what_they_sell": "Analytics for insurance claims teams.",
    "classification": "b2b_other_function",
    "classification_why": "They sell to claims operations, not to marketing.",
    "classification_confidence": "high",
    "sources": ["https://northwind.example/customers"],
    "note": "",
}


def _draft_probe(reply, before="", after="", ok=True):
    """Type a name and a site, run one draft, then report the form's state."""
    return (
        "__fetchReply = {ok: %s, body: %s};\n"
        "document.getElementById('clientName').value = 'Northwind';\n"
        "document.getElementById('clientSite').value = 'northwind.example';\n"
        "%s\n"
        "draftProfile().then(function(){\n"
        "%s\n"
        "  console.log(JSON.stringify({\n"
        "    picked: checkedValue('data-classification'),\n"
        "    site: document.getElementById('clientSite').value,\n"
        "    suggested: (function(){\n"
        "      var c = document.querySelector('[data-classification=\"b2b_other_function\"]');\n"
        "      return {mark: c.getAttribute('data-suggested'),\n"
        "              text: c.querySelector('.cs').textContent,\n"
        "              hidden: c.querySelector('.cs').hidden};\n"
        "    })(),\n"
        "    fields: {roles: document.getElementById('buyerRoles').value,\n"
        "             verts: document.getElementById('verticals').value,\n"
        "             acv: document.getElementById('acvBand').value,\n"
        "             geo: document.getElementById('geoScope').value},\n"
        "    ev: {roles: __evNodes.buyer_roles.textContent,\n"
        "         rolesCls: __evNodes.buyer_roles.className,\n"
        "         acv: __evNodes.acv_band.textContent,\n"
        "         acvCls: __evNodes.acv_band.className},\n"
        "    read: {html: document.getElementById('draftRead').innerHTML,\n"
        "           hidden: document.getElementById('draftRead').hidden},\n"
        "    err: {text: document.getElementById('draftError').innerHTML,\n"
        "          shown: document.getElementById('draftError').style.display}\n"
        "  }));\n"
        "});" % ("true" if ok else "false", json.dumps(reply), before, after))


def test_a_drafted_classification_does_not_answer_the_question(keys):
    """The whole boundary in one assertion. The draft may argue for a side of
    the floor; only a click may choose one."""
    out = _run(_draft_probe(_DRAFT_OK), *keys)
    assert out["picked"] is None, (
        "a drafted classification arrived already selected, so a form nobody "
        "answered would run against the model's guess")


def test_a_drafted_classification_shows_its_reasoning_on_the_card(keys):
    """The other half. A suggestion nobody can see is not a suggestion, it is
    a field left blank at the user's expense."""
    out = _run(_draft_probe(_DRAFT_OK), *keys)
    assert out["suggested"]["mark"] == "1"
    assert out["suggested"]["hidden"] is False
    assert "claims operations" in out["suggested"]["text"]
    assert "high confidence" in out["suggested"]["text"]
    assert "Click to confirm" in out["suggested"]["text"]


def test_clicking_the_suggested_card_is_what_selects_it(keys):
    out = _run(_draft_probe(_DRAFT_OK,
                            after="  pickClass('b2b_other_function');"), *keys)
    assert out["picked"] == "b2b_other_function"


def test_a_drafted_field_arrives_with_what_was_read(keys):
    out = _run(_draft_probe(_DRAFT_OK), *keys)
    assert out["fields"]["roles"] == "VP Claims, Head of Claims Ops"
    assert out["fields"]["geo"] == "North America"
    assert "claims leaders" in out["ev"]["roles"]
    assert "read" in out["ev"]["rolesCls"]


def test_a_field_the_site_does_not_answer_says_so_rather_than_sitting_blank(keys):
    """An empty box reads as something the user forgot. Pricing and sales
    cycle are usually not published, so these two are empty most of the time
    and the difference has to be visible."""
    out = _run(_draft_probe(_DRAFT_OK), *keys)
    assert out["fields"]["acv"] == ""
    assert "Their site does not say" in out["ev"]["acv"]
    assert "gap" in out["ev"]["acvCls"]


def test_a_field_the_user_typed_is_never_overwritten(keys):
    """A tool that quietly replaces your answer is the black box this exists
    to be the opposite of."""
    out = _run(_draft_probe(
        _DRAFT_OK,
        before="document.getElementById('buyerRoles').value = 'Chief Claims Officer';"),
        *keys)
    assert out["fields"]["roles"] == "Chief Claims Officer"
    assert "Kept what you typed" in out["ev"]["roles"]
    assert "kept" in out["ev"]["rolesCls"]
    assert "1 field you had already filled in" in out["read"]["html"]


def test_the_page_shows_what_it_read_before_what_it_wrote(keys):
    """Two firms sharing a name produces a complete, confident, well-sourced
    profile of the wrong business. One sentence the reader can check at a
    glance is the cheapest way to catch it."""
    out = _run(_draft_probe(_DRAFT_OK), *keys)
    assert out["read"]["hidden"] is False
    assert "Analytics for insurance claims teams." in out["read"]["html"]
    assert "not your client" in out["read"]["html"]
    assert "northwind.example/customers" in out["read"]["html"]


def test_a_bare_domain_is_corrected_in_the_field_rather_than_refused(keys):
    """The server refuses a scheme-less site, correctly. Bouncing the user off
    that is a worse answer than fixing it where they can see it."""
    out = _run(_draft_probe(_DRAFT_OK), *keys)
    assert out["site"] == "https://northwind.example"


def test_a_failed_draft_fills_nothing_and_says_why(keys):
    out = _run(_draft_probe({"error": "That site sells garden furniture.",
                             "kind": "wrong_company",
                             "sources": ["https://northwind.example/about"]},
                            ok=False), *keys)
    assert out["fields"]["roles"] == ""
    assert out["picked"] is None
    assert "garden furniture" in out["err"]["text"]
    assert "northwind.example/about" in out["err"]["text"]
    assert out["err"]["shown"] == ""


def test_drafting_twice_does_not_leave_the_first_answer_behind(keys):
    """A second read of a corrected URL must not show the first company's
    evidence under a field the second one left blank."""
    second = dict(_DRAFT_OK, draft=dict(_DRAFT_OK["draft"], buyer_roles=None),
                  evidence={}, classification=None, classification_why="")
    out = _run(
        "__fetchReply = {ok: true, body: %s};\n"
        "document.getElementById('clientName').value = 'Northwind';\n"
        "document.getElementById('clientSite').value = 'https://a.example';\n"
        "draftProfile().then(function(){\n"
        "  document.getElementById('buyerRoles').value = '';\n"
        "  __fetchReply = {ok: true, body: %s};\n"
        "  return draftProfile();\n"
        "}).then(function(){\n"
        "  var c = document.querySelector('[data-classification=\"b2b_other_function\"]');\n"
        "  console.log(JSON.stringify({\n"
        "    ev: __evNodes.buyer_roles.textContent,\n"
        "    cls: __evNodes.buyer_roles.className,\n"
        "    suggested: c.getAttribute('data-suggested'),\n"
        "    cs: c.querySelector('.cs').textContent}));\n"
        "});" % (json.dumps(_DRAFT_OK), json.dumps(second)), *keys)
    assert "claims leaders" not in out["ev"], (
        "the first draft's evidence survived under a field the second left blank")
    assert "Their site does not say" in out["ev"]
    assert "gap" in out["cls"]
    assert out["suggested"] is None, "a withdrawn suggestion stayed on the card"
    assert out["cs"] == ""


def test_the_call_to_action_disappears_once_the_card_is_clicked(keys):
    """"Click to confirm" left standing under a card somebody just clicked
    reads as though the click did not take."""
    out = _run(_draft_probe(_DRAFT_OK,
                            after="  pickClass('b2b_other_function');"), *keys)
    assert "Click to confirm" not in out["suggested"]["text"]
    assert "claims operations" in out["suggested"]["text"], (
        "the reasoning went with the call to action")


def test_choosing_a_different_side_keeps_our_reading_on_screen(keys):
    """Disagreeing with the draft is the point of asking. What we read stays
    visible next to the answer they gave rather than vanishing at the moment
    it becomes interesting."""
    out = _run(_draft_probe(_DRAFT_OK,
                            after="  pickClass('b2b_to_marketing');"), *keys)
    assert out["picked"] == "b2b_to_marketing"
    assert "claims operations" in out["suggested"]["text"]
    assert "Click to confirm" not in out["suggested"]["text"]
