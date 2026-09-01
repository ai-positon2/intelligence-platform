"""The page furniture the redesign added, tested for the things that can drift.

The scoring model is now drawn on the page: four weights, a bonus, a floor and
the two tier thresholds. Every one of those is a number that also decides what
the scorer does, so the failure mode is not a broken layout, it is a page
confidently explaining a rubric it no longer uses. These tests read the numbers
off `event_intel_rubric` and demand the page agree, so changing a weight in the
module and not on the page fails here rather than shipping.

The node tests execute the page's real script, for the same reason
test_event_intel_form_init.py does: a grep passes against a call that is never
reached.
"""

import json
import re
import shutil
import subprocess

import pytest

import app as appmod
from tracker import event_intel_rubric as rubric
from tracker import event_intel_store

_PAGE = "/p2/b2b-agents/event-conference-intelligence"
_IIFE_CLOSE = "\n  })();"


def _page(monkeypatch=None):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "harness@position2.com", "name": "T"}
    resp = c.get(_PAGE)
    assert resp.status_code == 200, "the page did not render (%s)" % resp.status_code
    return resp.get_data(as_text=True)


# ── the scoring model on the page is the rubric's own ────────────────────

def test_every_dimension_weight_on_the_page_comes_from_the_rubric():
    html = _page()
    for key, mx in rubric.DIMENSION_MAX.items():
        label = rubric.DIMENSION_LABELS[key]
        assert label in html, "the scoring model does not name %r" % label
        # The weight is rendered in its own <span class="rb-w">, so a stray
        # "40" elsewhere on the page cannot satisfy this.
        assert re.search(r'<span class="rb-w">%d</span>' % mx, html), (
            "the page does not show %s as %d, which is what the rubric scores it out of"
            % (label, mx))


def test_the_bonus_and_the_total_on_the_page_are_the_rubric_s():
    html = _page()
    assert '<span class="rb-w">+%d</span>' % rubric.MATCHMAKING_BONUS in html
    assert re.search(r'<span class="rb-total">%d<i>' % rubric.TOTAL_MAX, html), (
        "the page does not show %d as the maximum" % rubric.TOTAL_MAX)


def test_the_tier_thresholds_on_the_page_are_the_rubric_s():
    html = _page()
    p1 = rubric.TIER_MIN[rubric.TIER_P1]
    floor = rubric.RANK_FLOOR
    assert "<b>P1</b>%d and up" % p1 in html
    assert "<b>P2</b>%d to %d" % (floor, p1 - 1) in html
    assert "<b>Cut</b>under %d" % floor in html


def test_the_bar_widths_are_the_weights_as_a_share_of_the_total():
    """A picture of a rubric that is not to scale is worse than no picture:
    it says 40 and draws it the same length as 20."""
    html = _page()
    for key, mx in rubric.DIMENSION_MAX.items():
        pct = round(mx * 100 / rubric.TOTAL_MAX, 1)
        assert "width:%s%%" % pct in html, (
            "%s is weighted %d of %d but no bar is drawn at %s%% of the track"
            % (rubric.DIMENSION_LABELS[key], mx, rubric.TOTAL_MAX, pct))


def test_the_report_cuts_at_the_rubric_s_floor_rather_than_a_typed_number():
    html = _page()
    assert "var RANK_FLOOR = %d;" % rubric.RANK_FLOOR in html, (
        "the report script does not take the floor from the rubric")
    # And nothing left behind hardcoding it.
    script = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)[0]
    for stale in ("Nothing cleared 70", "Below 70."):
        assert stale not in script, (
            "%r is still typed into the report, so it will keep saying 70 after "
            "the floor moves" % stale)


# The tests above compare the page against the rubric's CURRENT numbers, which
# a literal equal to today's value satisfies just as well as a real read. These
# move the module and require the page to move with it. That is the property
# being claimed, and it is the one a typed number fails.

def test_moving_the_floor_moves_every_place_the_page_prints_it(monkeypatch):
    monkeypatch.setattr(rubric, "RANK_FLOOR", 64)
    html = _page()
    assert "var RANK_FLOOR = 64;" in html, "the report still cuts at a typed number"
    assert "<b>Cut</b>under 64" in html, "the scoring card still shows a typed floor"


def test_moving_the_tiers_moves_the_bands_drawn_on_the_page(monkeypatch):
    monkeypatch.setattr(rubric, "TIER_MIN", {rubric.TIER_P1: 85, rubric.TIER_P2: 64})
    monkeypatch.setattr(rubric, "RANK_FLOOR", 64)
    html = _page()
    assert "<b>P1</b>85 and up" in html
    assert "<b>P2</b>64 to 84" in html


def test_reweighting_a_dimension_reweights_the_page(monkeypatch):
    monkeypatch.setattr(rubric, "DIMENSION_MAX",
                        {"relevance": 30, "dm_access": 45, "engagement": 25})
    monkeypatch.setattr(rubric, "TOTAL_MAX", 110)
    html = _page()
    for mx in (30, 45, 25):
        assert '<span class="rb-w">%d</span>' % mx in html, (
            "the card does not show the reweighted %d" % mx)
    assert "width:40.9%" in html, "the bars are not redrawn to the new weights"


def test_changing_the_bonus_changes_the_card(monkeypatch):
    monkeypatch.setattr(rubric, "MATCHMAKING_BONUS", 15)
    monkeypatch.setattr(rubric, "TOTAL_MAX", 115)
    html = _page()
    assert '<span class="rb-w">+15</span>' in html
    assert '<span class="rb-total">115<i>' in html


# ── the four plays ───────────────────────────────────────────────────────

def test_each_play_says_what_it_produces():
    """The mode was a two-word pill. Each card now carries a description, and
    an empty one would render as a card that explains nothing."""
    html = _page()
    cards = re.findall(r'<button class="evi-play".*?</button>', html, re.S)
    assert len(cards) == 4, "expected four plays, found %d" % len(cards)
    seen = set()
    for card in cards:
        name = re.search(r'<span class="pn">(.*?)</span>', card, re.S)
        desc = re.search(r'<span class="pd">(.*?)</span>', card, re.S)
        assert name and name.group(1).strip(), "a play card has no name"
        assert desc and len(desc.group(1).split()) >= 6, (
            "the %r card does not say what it produces" % (name and name.group(1)))
        seen.add(name.group(1).strip())
    assert len(seen) == 4, "two plays share a name: %s" % seen


def test_every_play_has_an_icon():
    html = _page()
    cards = re.findall(r'<button class="evi-play".*?</button>', html, re.S)
    for card in cards:
        name = re.search(r'<span class="pn">(.*?)</span>', card, re.S)
        icon = re.search(r'<span class="pi".*?</span>', card, re.S)
        assert icon, "the %r card has no icon slot" % (name and name.group(1))
        assert "<svg" in icon.group(0) and "</svg>" in icon.group(0), (
            "the %r card's icon is not a closed svg" % (name and name.group(1)))
        # An svg with nothing drawn in it is an empty box, not an icon.
        assert re.search(r"<(path|rect|circle|line|polyline)\b", icon.group(0)), (
            "the %r card's icon draws nothing" % (name and name.group(1)))


# ── the saved-profile row ────────────────────────────────────────────────

def test_a_saved_profile_is_listed_by_its_readable_classification(monkeypatch):
    """The stored value is an enum key. It was printed raw, so the picker
    offered "b2b_to_marketing" as a thing to choose."""
    key = rubric.CLASSIFICATIONS[2]
    monkeypatch.setattr(event_intel_store, "list_profiles",
                        lambda email: [{"id": 5, "client_name": "Northwind",
                                        "classification": key}])
    monkeypatch.setattr(event_intel_store, "list_runs", lambda email, limit=60: [])
    html = _page()
    opt = re.search(r'<option value="5">(.*?)</option>', html, re.S)
    assert opt, "the saved profile is not in the picker"
    text = opt.group(1)
    assert rubric.CLASSIFICATION_LABELS[key] in text, (
        "the picker does not name the classification readably: %r" % text)
    assert key not in text, "the picker still shows the raw enum key: %r" % text


def test_a_profile_whose_classification_is_unknown_still_lists(monkeypatch):
    """Falling back to the stored value beats rendering an empty option that
    the user cannot tell apart from any other."""
    monkeypatch.setattr(event_intel_store, "list_profiles",
                        lambda email: [{"id": 6, "client_name": "Harborline",
                                        "classification": "retired_key"}])
    monkeypatch.setattr(event_intel_store, "list_runs", lambda email, limit=60: [])
    html = _page()
    opt = re.search(r'<option value="6">(.*?)</option>', html, re.S)
    assert opt and "Harborline" in opt.group(1)
    assert "retired_key" in opt.group(1)


def test_each_kind_of_run_is_named_for_the_play_that_made_it(monkeypatch):
    """Every run that was not a lookup used to read "Audience search", which
    called a scored calendar and a set of drafted openers the same thing."""
    runs = [{"id": i, "mode": m, "query": "q%d" % i, "status": "complete",
             "created_at": "2026-08-31T10:00:00", "credits_spent": 0,
             "participant_count": 0, "event_name": "Run %d" % i}
            for i, m in enumerate(("recommend", "lookup", "discover", "workroom"))]
    monkeypatch.setattr(event_intel_store, "list_runs", lambda email, limit=60: runs)
    monkeypatch.setattr(event_intel_store, "list_profiles", lambda email: [])
    html = _page()
    rows = re.findall(r'<button class="evi-run m-(\w+)".*?</button>', html, re.S)
    assert len(rows) == 4, "expected one row per run, found %d" % len(rows)
    assert set(rows) == {"recommend", "lookup", "discover", "workroom"}
    # Read the label out of each row rather than off the whole page: the
    # report script further down also emits a `k` span.
    kinds = [re.search(r'<span class="k">(.*?)</span>', block, re.S).group(1).strip()
             for block in re.findall(r'<button class="evi-run m-\w+".*?</button>',
                                     html, re.S)]
    assert len(set(kinds)) == 4, (
        "four kinds of run share %d labels: %s" % (len(set(kinds)), kinds))


def test_a_run_with_nothing_listed_does_not_claim_a_count(monkeypatch):
    """A scored-calendar run has no participants. Printing "0 listed" beside
    it reads as a roster that came back empty."""
    monkeypatch.setattr(event_intel_store, "list_runs", lambda email, limit=60: [
        {"id": 1, "mode": "recommend", "query": "q", "status": "complete",
         "created_at": "2026-08-31T10:00:00", "credits_spent": 0,
         "participant_count": 0, "event_name": "Northwind"}])
    monkeypatch.setattr(event_intel_store, "list_profiles", lambda email: [])
    html = _page()
    row = re.search(r'<button class="evi-run m-recommend".*?</button>', html, re.S)
    assert row and "0 listed" not in row.group(0)


# ── behaviour, executed ──────────────────────────────────────────────────

pytestmark_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node is needed to execute the page script")

_SHIM = """
var __state = {focused: null};
function __node(attrs){
  var a = attrs || {};
  return {
    _a: a, style: {}, value: a.value || '', disabled: false, hidden: false,
    textContent: '', innerHTML: '', firstChild: null, children: [],
    getAttribute: function(k){ return this._a[k] === undefined ? null : this._a[k]; },
    setAttribute: function(k, v){ this._a[k] = String(v); },
    classList: {add:function(){},remove:function(){},toggle:function(){},
                contains:function(){return false;}},
    focus: function(){ __state.focused = this._a.id || true; },
    scrollIntoView: function(){}, appendChild: function(){},
    insertBefore: function(){}, addEventListener: function(){}
  };
}
var __els = {
  profilePick: __node({id: 'profilePick', value: PICK}),
  profileForm: __node({id: 'profileForm'}),
  clientName: __node({id: 'clientName'}), formError: __node({id: 'formError'}),
  runBtn: __node({id: 'runBtn'}), runningWrap: __node({id: 'runningWrap'}),
  runningText: __node({id: 'runningText'}),
  sourceRun: __node({id: 'sourceRun', value: ''}),
  wrProfile: __node({id: 'wrProfile', value: ''}),
  modeRecommend: __node({id: 'modeRecommend'}), modeLookup: __node({id: 'modeLookup'}),
  modeDiscover: __node({id: 'modeDiscover'}), modeWorkroom: __node({id: 'modeWorkroom'}),
  recommendFields: __node({id: 'recommendFields'}),
  lookupFields: __node({id: 'lookupFields'}),
  discoverFields: __node({id: 'discoverFields'}),
  workroomFields: __node({id: 'workroomFields'})
};
__els.profileForm.style.display = 'none';
var __sums = PROFILE_IDS.map(function(id){ return __node({'data-profile': id}); });
var __cards = CLASS_KEYS.map(function(k, i){
  return __node({'data-classification': k, 'aria-checked': i === 0 ? 'true' : 'false'});
});
var __ecards = EVENT_KEYS.map(function(k, i){
  return __node({'data-eventclass': k, 'aria-checked': i === 0 ? 'true' : 'false'});
});
function __pick(sel, cards, attr){
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
    return __pick(sel, __cards, 'data-classification')
      .concat(__pick(sel, __ecards, 'data-eventclass'))
      .concat(__pick(sel, __sums, 'data-profile'));
  },
  querySelector: function(sel){
    var m = document.querySelectorAll(sel); return m.length ? m[0] : null;
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


def _exec(probe, pick="new", profile_ids=("3", "4")):
    html = _page()
    script = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)[0]
    assert _IIFE_CLOSE in script, "the page's IIFE no longer closes as expected"
    at = script.index(_IIFE_CLOSE)
    script = script[:at] + "\n" + probe + script[at:]
    shim = (_SHIM.replace("PICK", json.dumps(pick))
                 .replace("PROFILE_IDS", json.dumps(list(profile_ids)))
                 .replace("CLASS_KEYS", json.dumps(list(rubric.CLASSIFICATIONS)))
                 .replace("EVENT_KEYS", json.dumps(
                     list(__import__("tracker.event_intel_workroom",
                                     fromlist=["x"]).EVENT_CLASSES))))
    r = subprocess.run(["node", "-e", shim + "\n" + script],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, "the page script threw:\n%s" % r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytestmark_node
def test_the_run_button_is_labelled_for_the_play_that_is_selected():
    """The template renders the first play as selected and the button with a
    generic label. Left unsynced the page showed "Which events to attend"
    beside a button that just said "Run"."""
    out = _exec("console.log(JSON.stringify({"
                "label: document.getElementById('runBtn').textContent}));")
    assert out["label"] and out["label"] != "Run →", (
        "the run button still carries the generic label: %r" % out["label"])
    assert "calendar" in out["label"].lower(), (
        "the button does not name what the selected play does: %r" % out["label"])


@pytestmark_node
def test_the_button_label_follows_the_play():
    out = _exec("setMode('workroom');console.log(JSON.stringify({"
                "label: document.getElementById('runBtn').textContent}));")
    assert "room" in out["label"].lower(), out["label"]


@pytestmark_node
def test_choosing_a_profile_shows_that_profile_s_terms_and_no_other():
    out = _exec("document.getElementById('profilePick').value='4';onProfilePick();"
                "console.log(JSON.stringify({shown: __sums.filter(function(s){"
                "return !s.hidden;}).map(function(s){"
                "return s.getAttribute('data-profile');})}));")
    assert out["shown"] == ["4"], (
        "expected only profile 4's terms on screen, got %r" % out["shown"])


@pytestmark_node
def test_locking_a_new_profile_shows_no_saved_terms():
    """"new" is not a profile id, so nothing should be restated under it."""
    out = _exec("console.log(JSON.stringify({shown: __sums.filter(function(s){"
                "return !s.hidden;}).length}));")
    assert out["shown"] == 0
