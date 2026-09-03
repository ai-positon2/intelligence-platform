"""The client picker, and the two ways the card around it used to mislead.

This page cannot do anything until a client is chosen, and the card that
chooses one was the least finished thing on the screen. Two faults, both
visible in a screenshot a reader sent back:

  * it opened on a client nobody had picked. "Set up a new client" was the
    LAST option in the select, so a browser selected the first saved profile,
    and the client a half-hour search was about had been chosen by list order.
  * two identical gradient buttons, one above the other. The one that fills
    the form in for you was the loudest thing on the card; the one that saves
    the client was the quietest; and the one that starts the search looked
    ready while it would have been refused.

And a third that only a rendered page shows: three of the rules written to fix
this lost to source order. A duplicate `.evi-field select` block 2000 lines
further down used the `background` shorthand and silently zeroed the arrow;
`.evi-btn-save` sat above `.evi-btn` and got the page gradient back; and an
author `display` beat a `hidden` attribute, so the "set up a client first"
note stayed on screen next to a run button that had already turned primary.
Those three are pinned here as source-order facts, because a passing unit test
proves nothing about a rule that never applies.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SECRET_KEY", "test-only")

import app as appmod  # noqa: E402
# The node harness the rest of the form tests drive, imported rather than
# copied: two DOM shims drift, and the last time one did it fabricated a node
# per id and made half a file pass vacuously.
from test_event_intel_form_init import _run, keys  # noqa: E402,F401

_PAGE = "/p2/b2b-agents/event-conference-intelligence"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CSS = os.path.join(_ROOT, "static", "css", "event_conference_intelligence.css")
_TPL = os.path.join(_ROOT, "templates", "event_conference_intelligence.html")
_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


@pytest.fixture(scope="module")
def html():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "harness@position2.com", "name": "T"}
    resp = c.get(_PAGE)
    assert resp.status_code == 200, resp.status_code
    return resp.get_data(as_text=True)


@pytest.fixture(scope="module")
def css():
    """The stylesheet with comments stripped, so a rule's position in the file
    is measurable. A comment between two rules is absorbed into the next
    selector by any flat parse, and this file is heavily commented."""
    return _COMMENT.sub("", _read(_CSS))


def _select(html, sid):
    m = re.search(r'<select id="%s".*?</select>' % sid, html, re.S)
    assert m, "no select #%s on the page" % sid
    return m.group(0)


def _options(block):
    return [(v, " ".join(t.split()))
            for v, t in re.findall(r'<option value="([^"]*)"[^>]*>(.*?)</option>',
                                   block, re.S)]


# ── what the page opens on ───────────────────────────────────────────────

def test_the_picker_opens_on_setting_up_a_new_client(html):
    block = _select(html, "profilePick")
    opts = _options(block)
    assert opts, "the picker has no options"
    assert opts[0][0] == "new", (
        "the new-client option is not first, so a browser will select "
        "whatever is: %s" % [o[0] for o in opts])
    assert "new client" in opts[0][1].lower()
    marked = re.findall(r'<option value="([^"]*)"[^>]*\bselected\b', block)
    assert marked == ["new"], (
        "exactly one option must be marked selected, and it must be the "
        "new-client one: %s" % marked)


def test_a_saved_client_is_never_chosen_for_the_user(monkeypatch):
    """The fault this fixes only shows with saved clients, so it is tested
    with two of them. Nothing about a run this expensive should be decided by
    the order a list came back in."""
    from tracker import event_intel_store as store
    monkeypatch.setattr(store, "list_profiles", lambda *a, **k: [
        {"id": 7, "client_name": "Northwind Analytics",
         "classification": "b2b_to_marketing", "website": None,
         "buyer_roles": None, "verticals": None, "acv_band": None,
         "sales_cycle": None, "geo_scope": None, "window_months": None,
         "max_events": None, "force_include": None, "force_exclude": None,
         "budget_note": None},
        {"id": 8, "client_name": "Beta Bionics",
         "classification": "b2c_consumer", "website": None,
         "buyer_roles": None, "verticals": None, "acv_band": None,
         "sales_cycle": None, "geo_scope": None, "window_months": None,
         "max_events": None, "force_include": None, "force_exclude": None,
         "budget_note": None},
    ])
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "harness@position2.com", "name": "T"}
    page = c.get(_PAGE).get_data(as_text=True)
    opts = _options(_select(page, "profilePick"))
    assert [o[0] for o in opts] == ["new", "7", "8"], opts
    assert re.findall(r'<option value="([^"]*)"[^>]*\bselected\b',
                      _select(page, "profilePick")) == ["new"]
    assert "Northwind Analytics" in page, "the saved clients are gone"


def test_the_setup_panel_is_what_the_page_opens_into(html):
    """The picker's own handler opens it, and it runs on load. Without that
    the page opens on "set up a new client" with nothing to set up."""
    script = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                        html, re.S)
    script = [b for b in script if "function onProfilePick" in b]
    assert len(script) == 1
    body = script[0]
    at = body.index("function initForm")
    assert "onProfilePick()" in body[at:at + 900], (
        "nothing opens the setup panel on load")


# ── the three buttons, in three different weights ────────────────────────

def test_the_helper_that_fills_the_form_in_is_not_a_primary_button(html):
    """It was the same gradient pill as the button that starts the search,
    one above the other. Two identical buttons is a decision nobody asked
    for."""
    m = re.search(r'<button[^>]*id="draftBtn"[^>]*>', html, re.S)
    assert m, "the draft button is gone"
    assert "evi-btn-ghost" in m.group(0), (
        "the draft helper is styled as a primary again: %s" % m.group(0))


def test_saving_the_client_is_the_loudest_thing_in_the_panel(html, css):
    m = re.search(r'<button[^>]*id="saveProfileBtn"[^>]*>', html, re.S)
    assert m, "the save button is gone"
    assert "evi-btn-save" in m.group(0), m.group(0)
    assert "evi-btn-ghost" not in m.group(0), (
        "the button that finishes the form is a ghost again")


def test_the_save_buttons_colour_can_actually_win(css):
    """Placed above `.evi-btn` it lost on source order and came back as the
    page gradient: the one thing it exists not to be. Same specificity, so
    position in the file IS the behaviour."""
    base = css.index(".evi-btn {")
    save = css.index(".evi-btn-save {")
    assert save > base, (
        "`.evi-btn-save` is above `.evi-btn`, so the gradient wins and the "
        "save button is indistinguishable from the run button")
    rule = css[save:css.index("}", save)]
    assert "background:" in rule and "linear-gradient" not in rule, rule


def test_the_run_button_says_it_is_waiting_before_it_is_pressed(html):
    """A refusal after the click was the only signal, under a button that had
    looked ready the whole time. The button stays live, because a refusal
    that puts the cursor in the right field beats one that cannot be
    pressed."""
    m = re.search(r'<span class="evi-actions-wait"[^>]*id="runWait"[^>]*>'
                  r'(.*?)</span>', html, re.S)
    assert m, "the waiting note is gone"
    assert "hidden" in m.group(0), "the note starts visible"
    said = " ".join(m.group(1).split())
    assert "client" in said.lower(), said
    # Position-free: the panel is above the button on a wide screen and below
    # it on a phone, and the note used to say "below".
    assert "below" not in said.lower() and "above" not in said.lower(), said


def test_the_waiting_note_can_be_hidden_at_all(css):
    """An author `display` beats the hidden attribute, which is only
    `display: none` in the UA sheet. Without this rule the note stayed on
    screen next to a run button that had already turned primary, telling
    somebody who had just chosen a client to go and choose one."""
    assert ".evi-actions-wait[hidden]" in css, (
        "nothing re-hides the waiting note, so `hidden` does nothing to it")
    at = css.index(".evi-actions-wait[hidden]")
    assert "display: none" in css[at:css.index("}", at)]


# ── and the same thing, executed ─────────────────────────────────────────
#
# The three tests above assert that the note exists, that the CSS can hide it
# and that setMode calls the handler. All three passed against a handler that
# never showed the note and a button that never calmed down, because none of
# them ran the code. These do: the page's own script, over the shim the rest
# of the form tests drive.

def test_on_load_the_run_button_is_visibly_not_the_next_step(keys):
    out = _run(
        "console.log(JSON.stringify({"
        "  waitHidden: document.getElementById('runWait').hidden,"
        "  runClass: document.getElementById('runBtn').className}));", *keys)
    assert out["waitHidden"] is False, (
        "the page opens with no client and says nothing about it")
    assert "is-wait" in out["runClass"], (
        "the run button looks ready with nothing to run: %r" % out["runClass"])


def test_choosing_a_client_hands_the_page_back_to_the_run_button(keys):
    out = _run(
        "document.getElementById('profilePick').value = '7';\n"
        "onProfilePick();\n"
        "console.log(JSON.stringify({"
        "  waitHidden: document.getElementById('runWait').hidden,"
        "  runClass: document.getElementById('runBtn').className,"
        "  formDisplay: document.getElementById('profileForm').style.display}));",
        *keys)
    assert out["waitHidden"] is True, (
        "the note still tells somebody who just chose a client to choose one")
    assert "is-wait" not in out["runClass"], out["runClass"]
    assert out["formDisplay"] == "none", "the setup panel stayed open"


def test_a_play_that_needs_no_client_is_not_told_to_pick_one(keys):
    """Naming an event and working a roster have no picker of their own on
    this card. Left to the picker's own change event the waiting state stuck,
    and switching plays carried "finish setting up the client" onto a form
    with no client in it."""
    out = _run(
        "setMode('lookup');\n"
        "console.log(JSON.stringify({"
        "  waitHidden: document.getElementById('runWait').hidden,"
        "  runClass: document.getElementById('runBtn').className}));", *keys)
    assert out["waitHidden"] is True, out
    assert "is-wait" not in out["runClass"], out["runClass"]


def test_the_waiting_state_is_re_asked_when_the_play_changes(html):
    """Only one of the three plays needs a client. Left to the picker's own
    change event the state stuck, and switching to naming an event carried a
    "set up the client" note onto a form with no client in it."""
    script = [b for b in re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                                    html, re.S) if "function setMode" in b]
    assert len(script) == 1
    body = script[0]
    at = body.index("function setMode")
    end = body.index("window.setMode", at)
    assert "onProfilePick()" in body[at:end], (
        "setMode does not re-ask the picker, so the waiting note outlives "
        "the play that needed it")


# ── the select, which had no arrow at all ────────────────────────────────

def test_there_is_exactly_one_rule_styling_a_select(css):
    """There were two, 2000 lines apart. The second used the `background`
    shorthand, which reset the first one's arrow, so the control rendered
    with appearance:none and no arrow of any kind. One rule, one place."""
    # Exact selector only. A theme-scoped override
    # (`:root[data-theme="light"] .evi-field select`) is a different rule
    # doing a different job, and counting it here would make this test fail
    # for the wrong reason.
    selectors = [" ".join(sel.split())
                 for sel in re.findall(r"([^{}]+)\{", css)]
    hits = [sel for sel in selectors if sel == ".evi-field select"]
    assert len(hits) == 1, (
        "%d unscoped rules style `.evi-field select`; the later one silently "
        "resets whatever the earlier one set" % len(hits))


def test_the_select_draws_its_own_arrow(css):
    at = css.index(".evi-field select {")
    rule = css[at:css.index("}", at)]
    assert "appearance: none" in rule, rule
    assert "background-image:" in rule, "the native arrow is off and none was drawn"
    assert "background:" not in rule, (
        "the shorthand is back, and it is what zeroed the arrow last time")
    # Room for it, or the longest option name sits under it.
    pad = re.search(r"padding:\s*[\d.]+px\s+([\d.]+)px", rule)
    assert pad and float(pad.group(1)) >= 30, rule


def test_the_arrow_is_visible_in_both_themes(css):
    """A data URI cannot inherit `currentColor`, so one grey is either too
    pale on white or too dark on navy."""
    assert ':root[data-theme="light"] .evi-field select' in css, (
        "the light theme keeps the dark theme's arrow colour")


# ── the words ────────────────────────────────────────────────────────────

def test_the_intake_form_never_says_lock(html):
    """"Lock" is this codebase's word for a profile that cannot be edited.
    To somebody setting up their first client it is a word about a padlock.
    """
    form = html[html.index('id="recommendFields"'):html.index('id="lookupFields"')]
    text = re.sub(r"<[^>]+>", " ", form)
    assert not re.search(r"\block(ed|s|ing)?\b", text, re.I), (
        "the setup form still talks about locking: %s"
        % re.findall(r"[^.]*\block[^.]*\.", text, re.I)[:2])


def test_the_classification_question_is_asked_once(html):
    """It was a step heading and a field label, three lines apart, both
    asking where the client's buyers stand. That is how a short form starts
    feeling long."""
    form = html[html.index('id="recommendFields"'):html.index('id="lookupFields"')]
    at = form.index('id="classField"')
    field = form[at:at + 400]
    assert "<label" not in field, (
        "the classification field has a label again, and the step above it "
        "already asks the question")


def test_every_step_in_the_panel_is_numbered(html):
    """Four sections with four uppercase captions read as four captions. The
    numbers say what a long form is really being asked, which is how much of
    it is left."""
    panel = html[html.index('id="profileForm"'):html.index('id="lookupFields"')]
    nums = re.findall(r'<span class="gn">(\d+)</span>', panel)
    assert nums == ["1", "2", "3", "4"], nums
    # And the numbered heads have to look different from the unnumbered ones,
    # or the number is decoration on a caption.
    heads = re.findall(r'<div class="evi-group( step)?">', panel)
    assert heads.count(" step") == 4, heads


def test_a_numbered_step_head_is_not_a_caption(css):
    at = css.index(".evi-group.step .gh {")
    rule = css[at:css.index("}", at)]
    assert "text-transform: none" in rule, rule
    assert re.search(r"font-size:\s*1[3-9]", rule), (
        "a step heading is still caption-sized: %s" % rule)
