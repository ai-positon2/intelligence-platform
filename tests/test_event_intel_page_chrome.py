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
import os
import re
import shutil
import subprocess

import pytest

import app as appmod
from tracker import event_intel_rubric as rubric
from tracker import event_intel_store

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PAGE = "/p2/b2b-agents/event-conference-intelligence"
_IIFE_CLOSE = "\n  })();"


def _page(monkeypatch=None):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "harness@position2.com", "name": "T"}
    resp = c.get(_PAGE)
    assert resp.status_code == 200, "the page did not render (%s)" % resp.status_code
    return resp.get_data(as_text=True)


# ── the rubric still reaches the report ─────────────────────────────────
#
# The scoring-model card was removed from the hero at the user's request, so
# the tests that asserted its weights, bars and tier bands went with it. The
# floor still reaches the page, because the report prints it, and it is still
# read from the module rather than typed.

def test_the_report_cuts_at_the_rubric_s_floor_rather_than_a_typed_number():
    html = _page()
    assert "var RANK_FLOOR = %d;" % rubric.RANK_FLOOR in html, (
        "the report script does not take the floor from the rubric")
    script = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)[0]
    for stale in ("Nothing cleared 70", "Below 70."):
        assert stale not in script, (
            "%r is still typed into the report, so it will keep saying 70 after "
            "the floor moves" % stale)


def test_moving_the_floor_moves_what_the_report_cuts_at(monkeypatch):
    """Comparing against the rubric's CURRENT value passes just as well for a
    literal 70. Move the module and require the page to follow."""
    monkeypatch.setattr(rubric, "RANK_FLOOR", 64)
    html = _page()
    assert "var RANK_FLOOR = 64;" in html, "the report still cuts at a typed number"


def test_the_scoring_model_card_is_gone_from_the_hero():
    """Removed deliberately. If it comes back it needs its guards back with
    it, because its numbers can drift from the rubric that does the scoring."""
    html = _page()
    for gone in ('class="evi-rubric"', 'class="rb-total"', 'class="rb-tier'):
        assert gone not in html, "%s is back on the page without its guard" % gone

# ── the plays ────────────────────────────────────────────────────────────
#
# Three of them since `discover` was retired. These read the count off the
# page rather than hardcoding it, so adding or removing a play does not
# need a sweep through this file: what they actually assert is that every
# play on the page is complete and distinct from the others.

def test_each_play_says_what_it_produces():
    """The mode was a two-word pill. Each card now carries a description, and
    an empty one would render as a card that explains nothing."""
    html = _page()
    cards = re.findall(r'<button class="evi-play[^"]*".*?</button>', html, re.S)
    assert len(cards) >= 2, "expected several plays, found %d" % len(cards)
    seen = set()
    for card in cards:
        name = re.search(r'<span class="pn">(.*?)</span>', card, re.S)
        desc = re.search(r'<span class="pd">(.*?)</span>', card, re.S)
        assert name and name.group(1).strip(), "a play card has no name"
        assert desc and len(desc.group(1).split()) >= 6, (
            "the %r card does not say what it produces" % (name and name.group(1)))
        seen.add(name.group(1).strip())
    assert len(seen) == len(cards), "two plays share a name: %s" % seen


def test_every_play_has_an_icon():
    html = _page()
    cards = re.findall(r'<button class="evi-play[^"]*".*?</button>', html, re.S)
    for card in cards:
        name = re.search(r'<span class="pn">(.*?)</span>', card, re.S)
        icon = re.search(r'<span class="pi".*?</span>', card, re.S)
        assert icon, "the %r card has no icon slot" % (name and name.group(1))
        assert "<svg" in icon.group(0) and "</svg>" in icon.group(0), (
            "the %r card's icon is not a closed svg" % (name and name.group(1)))
        # An svg with nothing drawn in it is an empty box, not an icon.
        assert re.search(r"<(path|rect|circle|line|polyline)\b", icon.group(0)), (
            "the %r card's icon draws nothing" % (name and name.group(1)))


def _play_cards(html=None):
    cards = re.findall(r'<button class="evi-play[^"]*".*?</button>', html or _page(), re.S)
    assert len(cards) >= 2, "expected several play cards, found %d" % len(cards)
    out = []
    for card in cards:
        title = re.search(r'<span class="pn">(.*?)</span>', card, re.S)
        io = re.search(r'<span class="pio">(.*?)</span>\s*<span class="pd">', card, re.S)
        desc = re.search(r'<span class="pd">(.*?)</span>', card, re.S)
        act = re.search(r'data-action="([^"]+)"', card)
        assert title and desc and act, "a play card is missing its title, blurb or action"
        out.append({"raw": card, "title": title.group(1).strip(),
                    "io": io.group(1) if io else "", "desc": desc.group(1).strip(),
                    "action": act.group(1)})
    return out


def test_every_play_says_what_it_takes_what_it_returns_and_what_it_runs():
    """The cards are the page's primary control. Each says what you hand it
    and what it hands back, on its own line, because two of them used to read
    as "find me events" from the title alone.

    This replaced a step number, which looked like an order and was not one:
    these are alternatives, and the one real dependency between them (work
    the room needs a roster) is not what the numbering was describing.
    """
    cards = _play_cards()
    starts, actions = [], []
    for c in cards:
        assert c["io"], "the %r card does not say what it takes" % c["title"]
        a = re.search(r'<span class="a">(.*?)</span>', c["io"], re.S)
        b = re.search(r'<span class="b">(.*?)</span>', c["io"], re.S)
        assert a and b, "the %r card's line is not a from and a to" % c["title"]
        starts.append(re.sub(r"<[^>]+>", "", a.group(1)).strip())
        go = re.search(r'<span class="pgo"[^>]*>(.*?)<i>', c["raw"], re.S)
        assert go, "the %r card does not show the action it runs" % c["title"]
        assert go.group(1).strip() == c["action"], (
            "the card shows %r but would run %r" % (go.group(1).strip(), c["action"]))
        actions.append(c["action"])
    assert len(set(starts)) == len(cards), (
        "two plays claim to start from the same thing, which is the confusion "
        "this line exists to remove: %s" % starts)
    assert len(set(actions)) >= 3, (
        "the plays barely distinguish their actions: %s" % actions)


def test_the_arrow_in_that_line_is_not_the_only_thing_carrying_the_meaning():
    """It is a picture of a word. A reader who is hearing the page rather than
    seeing it gets "rightwards arrow" in the middle of a sentence, or nothing
    at all, unless the word is there too."""
    for c in _play_cards():
        assert 'aria-hidden="true"' in re.search(
            r'<i[^>]*>&rarr;</i>', c["io"]).group(0), (
            "the %r card's arrow is announced as a glyph" % c["title"])
        assert 'class="evi-sr"' in c["io"], (
            "the %r card's line has no spoken word where the arrow is" % c["title"])


def test_the_entry_cards_are_free_of_the_jargon_that_made_them_unreadable():
    """These cards are the first thing somebody sees on this page, and
    every one of these terms was on them: correct, and meaningless to anybody
    who had not already used the tool.

    Scoped to the cards on purpose. The intake form deeper in the page says
    ICP, and should: by then the reader has chosen a play and has context.
    """
    banned = ["icp", "under the bar", "48 hour", "opener per company",
              "target accounts"]
    for c in _play_cards():
        text = re.sub(r"<[^>]+>", " ", c["title"] + " " + c["io"] + " " + c["desc"])
        low = text.lower()
        for word in banned:
            assert word not in low, (
                "the %r card is back to saying %r" % (c["title"], word))


def test_no_entry_card_outruns_the_others_in_length():
    """The deep play's card kept growing back into a dense paragraph: six
    searches, one bar, famous names earning their place, every cut explaining
    itself, all before the reader had decided anything. Every clause was true
    and the card was still the one people said they could not read.

    The caps are absolute rather than relative to the shortest card, because a
    relative rule passes if every card inflates together, which is the way
    this copy actually drifts. A description that will not fit inside them is
    a description that belongs on the screen after the click.
    """
    for c in _play_cards():
        desc = re.sub(r"<[^>]+>", " ", c["desc"])
        desc = re.sub(r"&\w+;", " ", desc).strip()
        words = desc.split()
        sentences = [x.strip() for x in re.split(r"(?<=[.!?])\s+", desc) if x.strip()]
        assert len(words) <= 35, (
            "the %r card is back to a paragraph: %d words" % (c["title"], len(words)))
        assert len(sentences) <= 2, (
            "the %r card stacks %d sentences on somebody who has not chosen a "
            "play yet" % (c["title"], len(sentences)))
        longest = max(sentences, key=lambda x: len(x.split()))
        assert len(longest.split()) <= 30, (
            "the %r card has a %d word sentence in it: %r"
            % (c["title"], len(longest.split()), longest))


def _card_text(card):
    """Everything on a card that a reader actually reads."""
    return re.sub(r"<[^>]+>", " ",
                  card["title"] + " " + card["io"] + " " + card["desc"])


def test_the_deep_play_says_how_long_it_takes():
    """Twenty to forty minutes, measured over live runs. It is the single most
    decision-relevant fact about choosing between these two and it used to be
    nowhere on the page."""
    deep = [c for c in _play_cards()
            if 'data-play="recommend"' in c["raw"]][0]
    assert re.search(r"\bminutes\b", _card_text(deep)), (
        "nothing on the card says what a full run costs in time")


def test_a_play_that_names_another_play_names_one_that_exists():
    """Work the room refuses to run without a roster and tells you which play
    builds one, by title, in two places: a hint under the field and the error
    the form throws. Retitling that play without updating both points the user
    at something that is not on the page, and nothing else would notice.
    """
    html = _page()
    titles = {c["title"] for c in _play_cards(html)}
    # Both spellings: the hint is HTML with curly quotes and a line break
    # after "Run", the error is a JavaScript string with straight ones.
    quoted = set(re.findall(r'Run\s+(?:&ldquo;|")(.+?)(?:&rdquo;|")', html, re.S))
    quoted = {q.strip() for q in quoted if q.strip()}
    assert quoted, "nothing on the page points at another play by name any more"
    unknown = quoted - {" ".join(t.split()) for t in titles}
    assert not unknown, (
        "the page tells the user to run %s, which is not the title of any card "
        "on it. The cards are %s" % (sorted(unknown), sorted(titles)))



def test_no_play_falls_back_to_a_generic_run_label():
    """Two of the modes used to show a bare "Run", which told the user
    nothing about what pressing it would do."""
    html = _page()
    for card in re.findall(r'<button class="evi-play[^"]*".*?</button>', html, re.S):
        act = re.search(r'data-action="([^"]+)"', card).group(1)
        assert act.strip().lower() != "run", "a play still runs under a bare 'Run'"
        assert len(act.split()) >= 2, "%r does not say what it does" % act


def test_the_mode_cards_do_not_share_a_class_with_the_report_s_play_block():
    """Both were called .evi-play. Every rule written for the hero cards was
    landing on the workroom report's play block and the other way round, which
    is invisible until one of them is restyled and the other moves with it."""
    html = _page()
    card_classes = set()
    for card in re.findall(r'<button class="(evi-play[^"]*)"', html):
        card_classes.update(card.split())
    assert card_classes, "no mode cards found"
    script = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)[0]
    rendered = set()
    for cls in re.findall(r"""<div class=\\?["']([a-z0-9 _-]+)""", script):
        rendered.update(cls.split())
    overlap = card_classes & rendered
    assert not overlap, (
        "the mode cards and the report share %s, so their styles run together"
        % sorted(overlap))


def test_the_intro_counts_the_plays_that_are_actually_on_the_page():
    """It said "Four ways to use it" for the first ten minutes after the
    fourth play was retired. A sentence that miscounts the things directly
    under it is the cheapest possible way to look careless."""
    html = _page()
    n = len(re.findall(r'<button class="evi-play', html))
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}
    assert n in words, "unexpected number of plays: %d" % n
    intro = re.search(r'<div class="evi-hero">.*?<p>(.*?)</p>', html, re.S)
    assert intro, "the page has no intro paragraph any more"
    text = re.sub(r"\s+", " ", intro.group(1)).strip().lower()
    assert words[n] in text, (
        "the page shows %d plays and its intro says %r" % (n, text[:80]))


def test_the_play_row_does_not_hardcode_how_many_plays_there_are():
    """It was a four-track grid. The day the fourth play was retired that
    left a quarter of the row empty, and the cards kept the width they had
    when there were four of them. auto-fit did not fix it either: the 1fr
    distribution is settled before the empty track collapses."""
    css = open(os.path.join(_ROOT, "static/css/event_conference_intelligence.css")).read()
    block = re.search(r"\.evi-plays\s*\{([^}]*)\}", css)
    assert block, ".evi-plays has no rule any more"
    fixed = re.search(r"repeat\(\s*(\d+)", block.group(1))
    assert not fixed, (
        "the play row is laid out in a fixed %s tracks, so it will not divide "
        "itself correctly the next time a play is added or retired"
        % fixed.group(1))


def test_each_play_has_its_own_accent_class():
    """The cards are told apart by hue as well as by wording; a shared class
    would paint all four the same."""
    html = _page()
    hues = re.findall(r'<button class="evi-play (p-\w+)"', html)
    assert hues and len(set(hues)) == len(hues), (
        "two plays share an accent class, so the page paints them the same: %s" % hues)


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
    called a scored calendar and a set of drafted openers the same thing.

    `discover` is in this list on purpose even though the play is retired.
    Runs made before it was retired are still in history, and dropping the
    label would print them with no kind at all."""
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
  modeRecommend: __node(PLAY_ATTRS.recommend), modeLookup: __node(PLAY_ATTRS.lookup),
  modeWorkroom: __node(PLAY_ATTRS.workroom),
  recommendFields: __node({id: 'recommendFields'}),
  lookupFields: __node({id: 'lookupFields'}),
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
    # The play cards' data-action read straight off the rendered page: a shim
    # carrying its own copy could pass while the markup said something else.
    attrs = {}
    for card in re.findall(r'<button class="evi-play[^"]*".*?>', html, re.S):
        key = re.search(r'data-play="([^"]+)"', card)
        act = re.search(r'data-action="([^"]+)"', card)
        eid = re.search(r'id="([^"]+)"', card)
        assert key and act and eid, "a play card is missing data-play/data-action/id"
        attrs[key.group(1)] = {"id": eid.group(1), "data-action": act.group(1)}
    assert len(attrs) >= 2, "expected several play cards, found %d" % len(attrs)
    shim = (_SHIM.replace("PLAY_ATTRS", json.dumps(attrs))
                 .replace("PICK", json.dumps(pick))
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
def test_the_button_label_is_read_off_the_selected_card():
    """One copy of a play's action wording, on the card. Change the card and
    the button has to follow, or the two can promise different things."""
    out = _exec("document.getElementById('modeLookup')"
                ".setAttribute('data-action', 'Sweep the calendar');"
                "setMode('lookup');console.log(JSON.stringify({"
                "label: document.getElementById('runBtn').textContent}));")
    assert out["label"].startswith("Sweep the calendar"), (
        "the button kept its own copy of the label: %r" % out["label"])


@pytestmark_node
def test_every_play_s_button_label_matches_its_card():
    out = _exec("var o={};['recommend','lookup','workroom']"
                ".forEach(function(m){setMode(m);"
                "o[m]=document.getElementById('runBtn').textContent;});"
                "console.log(JSON.stringify(o));")
    html = _page()
    cards = re.findall(r'<button class="evi-play[^"]*".*?</button>', html, re.S)
    assert len(out) == len(cards), (
        "the page has %d plays but the probe drove %d" % (len(cards), len(out)))
    for card in cards:
        key = re.search(r'data-play="([^"]+)"', card).group(1)
        act = re.search(r'data-action="([^"]+)"', card).group(1)
        assert out[key].startswith(act), (
            "the %s card says %r but its button says %r" % (key, act, out[key]))


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


# ── the shared responsive grid ───────────────────────────────────────────
#
# grid-tokens.css defines Arena's page side-margin and column gutter, and every
# other agent page reads them. This one loaded the sheet and then hand-typed its
# own padding, so it ran nearly edge to edge on a laptop while its neighbours
# kept a margin. These tests fail if a raw px value creeps back in, because the
# regression is invisible in a screenshot of a single page: it only shows up
# next to a sibling.

_CSS = "static/css/event_conference_intelligence.css"


def _css():
    with open(_CSS, encoding="utf-8") as fh:
        return fh.read()


def _rule(css, selector):
    """The base rule for `selector`, not a media-query override of it.

    Comments are stripped first: several of these rules are introduced by a
    banner comment, which otherwise sits between the previous `}` and the
    selector. The opening delimiter excludes `{`, so a rule nested inside an
    `@media` block is never what comes back.
    """
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    m = re.search(r"(?:^|[};])\s*%s\s*\{([^{}]*)\}" % re.escape(selector),
                  css, re.S)
    assert m, "no base %s rule in %s" % (selector, _CSS)
    return m.group(1)


def test_the_page_loads_the_shared_grid_tokens():
    """`var(--margin)` with no sheet defining it makes the whole padding
    declaration invalid, which silently computes to zero."""
    html = _page()
    assert "grid-tokens.css" in html, (
        "the page uses the grid tokens but does not load the sheet that defines them")


def test_the_content_container_takes_its_side_margin_from_the_token():
    body = _rule(_css(), ".main")
    pad = re.search(r"padding:([^;]+);", body)
    assert pad, ".main sets no padding"
    assert "var(--margin)" in pad.group(1), (
        ".main hand-types its side padding instead of taking the responsive "
        "margin every other agent page uses: %r" % pad.group(1).strip())


def test_the_content_container_is_capped_in_the_same_band_as_its_siblings():
    """Uncapped, or capped far wider than the family, is the "too broad" the
    redesign was reported for."""
    body = _rule(_css(), ".main")
    m = re.search(r"max-width:\s*(\d+)px", body)
    assert m, ".main sets no max-width, so it runs to the full viewport"
    assert 1150 <= int(m.group(1)) <= 1340, (
        ".main caps at %spx, outside the 1180 to 1320 band the other agent "
        "pages use" % m.group(1))


def test_the_topbar_bleeds_with_the_shared_offset():
    body = _rule(_css(), ".topbar")
    pad = re.search(r"padding:([^;]+);", body)
    assert pad and "var(--bleed)" in pad.group(1), (
        "the topbar does not use --bleed, so it will not line up with the "
        "content container: %r" % (pad and pad.group(1).strip()))


def test_the_page_level_column_grids_share_one_gutter():
    """Three column grids with three separately-chosen gaps drift apart."""
    css = _css()
    # .evi-hero is a single block since the scoring card was removed; the
    # two grids that still split the page into columns are these.
    for sel in (".evi-layout", ".evi-plays"):
        body = _rule(css, sel)
        gap = re.search(r"gap:([^;]+);", body)
        assert gap, "%s sets no gap" % sel
        assert "var(--gutter)" in gap.group(1), (
            "%s hand-types its gap rather than using the shared gutter: %r"
            % (sel, gap.group(1).strip()))


def test_the_choice_grids_are_not_left_to_auto_fit():
    """auto-fit picks its column count off whatever width the container
    happens to have, which dealt four cards as 3 + 1 and five as 4 + 1."""
    css = _css()
    for sel in (".evi-classes", ".evi-eclasses"):
        body = _rule(css, sel)
        cols = re.search(r"grid-template-columns:([^;]+);", body)
        assert cols, "%s sets no columns" % sel
        assert "auto-fit" not in cols.group(1) and "auto-fill" not in cols.group(1), (
            "%s is back on auto-fit, so its cards can strand on a second row: %r"
            % (sel, cols.group(1).strip()))
