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


def _page_html():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "harness@position2.com", "name": "T"}
    resp = c.get(_PAGE)
    assert resp.status_code == 200, "the page did not render (%s)" % resp.status_code
    return resp.get_data(as_text=True)


def _page_script(html=None):
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                        html if html is not None else _page_html(), re.S)
    assert blocks, "the page has no inline script"
    return blocks[0]


_ID = re.compile(r'\bid="([^"]+)"')
_HIDDEN = re.compile(r'(?:^|\s)hidden(?=[\s>=])')


def _hidden_ids(html):
    """Every element the template renders with a bare `hidden` attribute.

    Fed to the shim so its starting state is the TEMPLATE'S starting state.
    The alternative is writing it out by hand, and the last hand-written
    assumption in this shim (that the template pre-checked a radio card)
    stopped being true and left two tests asserting fiction against a DOM
    nobody rendered. A derived value cannot drift.
    """
    out = []
    for tag in re.findall(r"<[a-zA-Z][^>]*>", html):
        m = _ID.search(tag)
        if not m:
            continue
        # Strip every quoted value first, so the word "hidden" inside a class
        # name, an onclick or a placeholder is not read as the attribute.
        bare = re.sub(r'="[^"]*"', "=", tag)
        if _HIDDEN.search(bare):
            out.append(m.group(1))
    return sorted(set(out))


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
  if (!__made[id]) {
    __made[id] = __node({id: id});
    // Exactly what the template rendered. `profileRest` arrives hidden, and a
    // shim that handed it back visible would let the whole fold be deleted
    // with every test still green.
    if (HIDDEN_IDS.indexOf(id) !== -1) __made[id].hidden = true;
  }
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
  // The card's own label, which the refusal message quotes back ("We read
  // them as X"). Without it the page reads `undefined` here and the branch
  // that names the suggestion can never be exercised.
  n._cl = __node({});
  n._cl.textContent = (typeof CLASS_LABELS !== 'undefined' && CLASS_LABELS[key]) || key;
  n.querySelector = function(sel){
    if (sel === '.cs') return this._cs;
    if (sel === '.cl') return this._cl;
    return null;
  };
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
// Counted, because "the save was refused before it went anywhere" is a claim
// about a request that did NOT happen, and only a counter can check that.
__state.fetches = 0;
global.fetch = function(){
  __state.fetches++;
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
    html = _page_html()
    script = _page_script(html)
    assert _IIFE_CLOSE in script, (
        "the page's IIFE no longer closes with a two-space-indented `})();`")
    at = script.index(_IIFE_CLOSE)
    # Spliced INSIDE the IIFE and AFTER the init block, so it observes the
    # state the browser is left in once the page has loaded.
    script = script[:at] + "\n" + probe + script[at:]
    from tracker import event_intel_rubric
    js = ("var CLASS_KEYS = %s;\nvar EVENT_KEYS = %s;\nvar EV_FIELDS = %s;\n"
          "var HIDDEN_IDS = %s;\nvar CLASS_LABELS = %s;\n%s\n%s"
          % (json.dumps(class_keys), json.dumps(event_keys),
             json.dumps(_EV_FIELDS), json.dumps(_hidden_ids(html)),
             json.dumps(event_intel_rubric.CLASSIFICATION_LABELS),
             _SHIM, script))
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
    "pages": [{"url": "https://northwind.example/customers", "status": "ok",
               "note": ""},
              {"url": "https://northwind.example/pricing", "status": "blocked",
               "note": "Server refused the request (HTTP 403)."}],
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


def test_the_button_counts_while_it_reads_and_resets_afterwards(keys):
    """A measured draft took 450 seconds. A button reading "Reading the site…"
    unchanged for seven minutes is indistinguishable from a hung page, so it
    shows the time passing instead."""
    out = _run(
        "__fetchReply = {ok: true, body: %s};\n"
        "document.getElementById('clientName').value = 'Northwind';\n"
        "document.getElementById('clientSite').value = 'https://a.example';\n"
        "var btn = document.getElementById('draftBtn');\n"
        "var p = draftProfile();\n"
        "var during = {text: btn.textContent, disabled: btn.disabled};\n"
        "p.then(function(){\n"
        "  console.log(JSON.stringify({during: during,\n"
        "    after: {text: btn.textContent, disabled: btn.disabled}}));\n"
        "});" % json.dumps(_DRAFT_OK), *keys)
    assert "Reading the site" in out["during"]["text"]
    assert "0s" in out["during"]["text"], (
        "the button showed no elapsed time, so a long wait looks like a hang")
    assert out["during"]["disabled"] is True
    assert out["after"]["text"] == "Read the site and fill this in"
    assert out["after"]["disabled"] is False, (
        "the button stayed disabled, so a failed draft could never be retried")


def test_the_button_is_usable_again_after_a_failed_draft(keys):
    out = _run(
        "__fetchReply = {ok: false, body: {error: 'HTTP 503'}};\n"
        "document.getElementById('clientName').value = 'Northwind';\n"
        "document.getElementById('clientSite').value = 'https://a.example';\n"
        "var btn = document.getElementById('draftBtn');\n"
        "draftProfile().then(function(){\n"
        "  console.log(JSON.stringify({disabled: btn.disabled,\n"
        "    text: btn.textContent}));\n"
        "});", *keys)
    assert out["disabled"] is False
    assert out["text"] == "Read the site and fill this in"


def test_a_page_that_refused_us_is_named_next_to_the_field_it_explains(keys):
    """Without this a blank deal size is just blank. With it, "we asked for
    their pricing page and it refused us" is on screen, and the reader knows
    whether to go and look themselves or to accept that it is not published."""
    out = _run(_draft_probe(_DRAFT_OK), *keys)
    assert "1 page we tried could not be read" in out["read"]["html"]
    assert "northwind.example/pricing" in out["read"]["html"]
    assert "403" in out["read"]["html"]


def test_nothing_is_said_when_every_page_was_read(keys):
    """The control. A line that always appears stops carrying information."""
    clean = dict(_DRAFT_OK, pages=[{"url": "https://northwind.example/customers",
                                    "status": "ok", "note": ""}])
    out = _run(_draft_probe(clean), *keys)
    assert "could not be read" not in out["read"]["html"]


# ── The fold: what a first-time reader is actually asked for ──────────────
#
# The intake had five numbered steps, twelve inputs and a four-card question
# on screen before it had done anything for you. The draft filled them in, but
# you met the wall first, and "this asks for a lot" was the fair reading.
#
# Everything past the name and the website is folded away now. These tests pin
# both halves: that it starts folded, and that it always opens again, because
# a fold with no way out is worse than the wall it replaced.

def test_the_form_opens_asking_for_two_things(keys):
    out = _run("console.log(JSON.stringify({"
               "  rest: document.getElementById('profileRest').hidden,"
               "  name: !!document.getElementById('clientName'),"
               "  site: !!document.getElementById('clientSite')}));", *keys)
    assert out["rest"] is True, "the rest of the intake is on screen from the start"
    assert out["name"] and out["site"]


def test_a_finished_draft_opens_the_rest(keys):
    """It opens as a thing to correct, which is the entire point of folding
    it: the same fields, arriving filled in."""
    out = _run(
        "applyDraft({draft: {buyer_roles: 'CMO'}, evidence: {}, sources: [],"
        " classification: CLASS_KEYS[1], classification_why: 'because'});"
        "console.log(JSON.stringify({"
        "  rest: document.getElementById('profileRest').hidden,"
        "  roles: document.getElementById('buyerRoles').value,"
        "  focused: __state.focused || null}));", *keys)
    assert out["rest"] is False
    assert out["roles"] == "CMO"
    # The page has just written into those fields. Moving the cursor as well
    # would fight the reader's eye at the exact moment they are reading.
    assert out["focused"] is None, "the draft stole the cursor"


def test_a_draft_that_fails_is_not_a_dead_end(keys):
    """No website, a site that refuses us, or the wrong company. The error
    stays on screen AND the form opens, because the alternative is a page
    whose only control has just failed."""
    out = _run(
        "showDraftError('The site could not be read.');"
        "console.log(JSON.stringify({"
        "  rest: document.getElementById('profileRest').hidden,"
        "  err: document.getElementById('draftError').style.display}));", *keys)
    assert out["rest"] is False
    assert out["err"] == "", "the reason it failed was cleared by the reveal"


def test_somebody_who_knows_the_answers_can_skip_the_read(keys):
    out = _run(
        "revealRest(true);"
        "console.log(JSON.stringify({"
        "  rest: document.getElementById('profileRest').hidden,"
        "  manual: document.getElementById('manualBtn').hidden,"
        "  focused: __state.focused || null}));", *keys)
    assert out["rest"] is False
    assert out["manual"] is True, "the way in is still offered after it was taken"
    assert out["focused"] == "buyerRoles", "opened it and left the cursor nowhere"


def test_opening_the_rest_answers_nothing_on_the_users_behalf(keys):
    """The guarantee the whole form rests on. Revealing is not answering, and
    a classification the code refuses to infer must still be unanswered after
    the block it lives in appears."""
    out = _run(
        "revealRest(true);"
        "applyDraft({draft: {}, evidence: {}, sources: [],"
        " classification: CLASS_KEYS[2], classification_why: 'because'});"
        "console.log(JSON.stringify({"
        "  picked: pickedClass,"
        "  checked: checkedValue('data-classification')}));", *keys)
    assert out["picked"] is None
    assert out["checked"] is None


def test_reopening_does_not_move_the_cursor_a_second_time(keys):
    """A draft arriving after somebody already opened it by hand must not
    yank the cursor out of the field they are typing in."""
    out = _run(
        "revealRest(true); __state.focused = null;"
        "revealRest(true);"
        "console.log(JSON.stringify({focused: __state.focused || null}));", *keys)
    assert out["focused"] is None


# ── The one question that can stop a save ─────────────────────────────────
#
# Pressing "Lock this profile" without choosing a side of the floor used to
# POST anyway and paint the server's own words under the button:
#
#   Unknown classification ''. It must be one of: b2c_general,
#   b2c_booth_density, b2b_to_marketing, b2b_other_function. ...
#
# Right for an API client, wrong for a person: internal keys, four hundred
# pixels from the question, and no statement of what to do. The server keeps
# refusing, which is the hard stop the whole play depends on. It just stops
# being the first thing a person meets.

def test_locking_without_an_answer_never_reaches_the_server(keys):
    out = _run(
        "saveProfile();"
        "console.log(JSON.stringify({"
        "  fetches: __state.fetches,"
        "  shown: !document.getElementById('classError').hidden,"
        "  msg: document.getElementById('classError').textContent,"
        "  focused: __state.focused || null,"
        "  bottom: document.getElementById('profileError').style.display}));", *keys)
    assert out["fetches"] == 0, "the save was sent knowing it would be refused"
    assert out["shown"] is True
    assert out["focused"] == "classGroup", "refused the save and pointed nowhere"
    assert out["bottom"] == "none", "the message also appeared under the button"


def test_the_refusal_says_what_to_do_and_names_no_internal_keys(keys):
    out = _run(
        "saveProfile();"
        "console.log(JSON.stringify({"
        "  msg: document.getElementById('classError').textContent}));", *keys)
    msg = out["msg"]
    assert "choose where" in msg
    for key in ("b2c_general", "b2c_booth_density", "b2b_to_marketing",
                "b2b_other_function", "Unknown classification"):
        assert key not in msg, "the enum leaked into what a person reads: %r" % msg


def test_the_refusal_points_at_the_card_we_suggested(keys):
    """It is one click away and the reader has already been given a reason.
    Naming the card turns "answer this" into "confirm this"."""
    from tracker import event_intel_rubric
    key = keys[0][2]
    label = event_intel_rubric.CLASSIFICATION_LABELS[key]
    out = _run(
        "applyDraft({draft: {}, evidence: {}, sources: [],"
        " classification: CLASS_KEYS[2], classification_why: 'Because.'});"
        "saveProfile();"
        "console.log(JSON.stringify({"
        "  msg: document.getElementById('classError').textContent}));", *keys)
    assert label in out["msg"], out["msg"]
    assert "Click that card" in out["msg"]


def test_with_nothing_suggested_the_refusal_invents_no_card(keys):
    out = _run(
        "saveProfile();"
        "console.log(JSON.stringify({"
        "  msg: document.getElementById('classError').textContent}));", *keys)
    assert "Click that card" not in out["msg"]
    assert "We read them as" not in out["msg"]


def test_answering_clears_the_refusal(keys):
    out = _run(
        "saveProfile();"
        "pickClass(CLASS_KEYS[1]);"
        "console.log(JSON.stringify({"
        "  hidden: document.getElementById('classError').hidden,"
        "  msg: document.getElementById('classError').textContent}));", *keys)
    assert out["hidden"] is True
    assert out["msg"] == ""


def test_an_answered_form_is_actually_sent(keys):
    """The guard must refuse exactly one thing and get out of the way."""
    out = _run(
        "pickClass(CLASS_KEYS[1]);"
        "document.getElementById('clientName').value = 'Northwind';"
        "saveProfile();"
        "console.log(JSON.stringify({fetches: __state.fetches,"
        "  shown: !document.getElementById('classError').hidden}));", *keys)
    assert out["fetches"] == 1
    assert out["shown"] is False


def test_the_servers_enum_never_reaches_the_page(keys):
    """Belt and braces. The guard above makes this unreachable, and
    "unreachable" rests on the page and the server agreeing about a list of
    keys. If they ever disagree, a person still gets told what to do."""
    out = _run(
        "pickClass(CLASS_KEYS[1]);"
        "__fetchReply = {ok: false, body: {error: \"Unknown classification ''. \""
        "  + 'It must be one of: b2c_general, b2c_booth_density.'}};"
        "saveProfile();"
        "setTimeout(function(){ console.log(JSON.stringify({"
        "  bottom: document.getElementById('profileError').textContent,"
        "  inline: document.getElementById('classError').textContent}));}, 0);",
        *keys)
    assert "b2c_general" not in out["bottom"], out["bottom"]
    assert "Unknown classification" not in out["bottom"]
    assert "choose where" in out["inline"]


def test_an_unrelated_save_failure_is_still_reported_as_itself(keys):
    """The translation must not swallow every other reason a save can fail."""
    out = _run(
        "pickClass(CLASS_KEYS[1]);"
        "__fetchReply = {ok: false, body: {error: 'Storage is unavailable.'}};"
        "saveProfile();"
        "setTimeout(function(){ console.log(JSON.stringify({"
        "  bottom: document.getElementById('profileError').textContent}));}, 0);",
        *keys)
    assert out["bottom"] == "Storage is unavailable."


# ── The card carries a reason, not an essay ───────────────────────────────

def test_the_card_shows_one_sentence_of_a_paragraph(keys):
    """Printed in full, the reasoning tripled the height of one card in a
    four-card grid and left the other three with a hole above their footer."""
    long_why = ("The iLet is a prescription device marketed to people with "
                "type 1 diabetes. A physician's prescription is part of the "
                "purchase pathway. The end buyer is the individual consumer.")
    out = _run(
        "applyDraft({draft: {}, evidence: {}, sources: [],"
        " classification: CLASS_KEYS[0], classification_confidence: 'medium',"
        " classification_why: %s});"
        "var card = document.querySelector('[data-suggested]');"
        "console.log(JSON.stringify({cs: card.querySelector('.cs').textContent}));"
        % json.dumps(long_why), *keys)
    cs = out["cs"]
    assert "medium confidence" in cs
    assert "The iLet is a prescription device marketed to people with type 1 " \
           "diabetes." in cs
    assert "purchase pathway" not in cs, "the whole paragraph is on the card"
    assert cs.endswith("Click to confirm.")


def test_the_whole_reasoning_is_still_on_the_page(keys):
    """Shortening the card must not lose it. It moves to What we read, where
    there is room for prose."""
    long_why = ("First sentence. Second sentence that the card will not show "
                "because it only shows one.")
    out = _run(
        "applyDraft({draft: {}, evidence: {}, sources: [],"
        " classification: CLASS_KEYS[0], classification_why: %s});"
        "console.log(JSON.stringify({"
        "  read: document.getElementById('draftRead').innerHTML}));"
        % json.dumps(long_why), *keys)
    assert "Second sentence that the card will not show" in out["read"]
    assert "Why we read them as" in out["read"]


@pytest.mark.parametrize("text,expected", [
    ("One. Two.", "One."),
    ("No full stop here", "No full stop here"),
    ("Priced at 4.5 units. Next.", "Priced at 4.5 units."),
    ("", ""),
])
def test_only_a_real_sentence_end_ends_the_sentence(keys, text, expected):
    """A decimal point and an abbreviation are not sentence ends, and cutting
    at one would print half a clause on the card."""
    out = _run("console.log(JSON.stringify({v: firstSentence(%s, 150)}));"
               % json.dumps(text), *keys)
    assert out["v"] == expected


def test_a_single_endless_sentence_is_still_cut_to_fit(keys):
    out = _run("console.log(JSON.stringify({v: firstSentence('%s', 60)}));"
               % ("word " * 40).strip(), *keys)
    assert len(out["v"]) <= 61
    assert out["v"].endswith("\u2026")
