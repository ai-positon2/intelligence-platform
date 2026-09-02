"""The discover/lookup drawer: what the reader is actually shown.

Every test here EXECUTES the page's own inline script in node and asserts on
the HTML it produces. Nothing re-implements the view. The defect this file
was opened for was citation markup printed as literal text mid-sentence, and
a grep for the fix would pass against a helper that is never called on the
field that carries it.

The rest of the file is the honesty the drawing has to keep. A chart is a
claim: an event placed on a calendar rail is a claim about when it happens,
so an event whose date nobody has announced has to be visibly absent from
that rail rather than quietly plotted somewhere plausible.
"""

import json
import re
import shutil
import subprocess

import pytest

import app as appmod

_PAGE = "/p2/b2b-agents/event-conference-intelligence"
_IIFE_CLOSE = "\n  })();"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to execute the page script")

# Assembled at import time rather than written out as a literal. This is the
# one string the whole file turns on, and a literal is exactly what an editor,
# a formatter or a paste is most likely to soften into something harmless.
_OPEN = "<" + 'cite index="1-2">'
_CLOSE = "<" + "/cite>"
_CITED = ("This is the flagship gathering, where a recap noted attendees came from "
          + _OPEN + '"47 states and 9 countries"' + _CLOSE + ", evenly split.")

_SHIM = """
function __node(id){
  return {
    _id: id, style: {}, value: '', disabled: false, textContent: '', innerHTML: '',
    children: [], firstChild: null,
    getAttribute: function(){ return null; }, setAttribute: function(){},
    classList: {add:function(){},remove:function(){},toggle:function(){},
                contains:function(){return false;}},
    focus: function(){}, scrollIntoView: function(){}, appendChild: function(){},
    insertBefore: function(){}, addEventListener: function(){}
  };
}
var __els = {};
global.document = {
  readyState: 'complete',
  getElementById: function(id){
    if (!__els[id]) __els[id] = __node(id);
    return __els[id];
  },
  querySelectorAll: function(){ return []; },
  querySelector: function(){ return null; },
  createElement: function(){ return __node('x'); },
  addEventListener: function(){}
};
global.window = {addEventListener: function(){}};
global.location = {hash: '', pathname: '/'};
global.fetch = function(){
  return Promise.resolve({ok: true, json: function(){ return Promise.resolve({}); }});
};
global.setInterval = function(){ return 0; };
global.clearInterval = function(){};
global.setTimeout = function(){ return 0; };
global.clearTimeout = function(){};
"""


@pytest.fixture(scope="module")
def page_script():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "harness@position2.com", "name": "T"}
    resp = c.get(_PAGE)
    assert resp.status_code == 200, "the page did not render (%s)" % resp.status_code
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                        resp.get_data(as_text=True), re.S)
    hits = [b for b in blocks if "function eventsHtml" in b]
    assert len(hits) == 1, \
        "expected exactly one inline block defining eventsHtml, found %d" % len(hits)
    return hits[0]


def _render(page_script, run, sort="ranked"):
    """Run the page's real render() over `run` and return what it wrote."""
    at = page_script.index(_IIFE_CLOSE)
    probe = ("\nEVENT_SORT = %s;\ncurrent = __RUN;\nrender(__RUN);\n"
             "console.log(JSON.stringify({body: "
             "document.getElementById('drawerBody').innerHTML}));\n" % json.dumps(sort))
    js = "%s\nvar __RUN = %s;\n%s" % (
        _SHIM, json.dumps(run), page_script[:at] + probe + page_script[at:])
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, "the page script threw:\n%s" % r.stderr[-2500:]
    return json.loads(r.stdout.strip().splitlines()[-1])["body"]


def _event(**kw):
    base = {"id": 1, "name": "A Conference", "edition": None, "website": None,
            "organizer": None, "starts_on": "2027-05-04", "ends_on": "2027-05-06",
            "location": "Berlin, Germany", "format": "in_person", "stated_size": None,
            "audience_note": None, "fit_score": 80, "confidence": "high",
            "fit_reasoning": "It draws the buyer."}
    base.update(kw)
    return base


def _run(events, **kw):
    run = {"id": 9, "mode": "discover", "status": "complete", "query": "an audience",
           "participants": [], "sources": [], "events": events,
           "summary": {"discovered_events": len(events)}}
    run["summary"].update(kw.pop("summary", {}))
    run.update(kw)
    return run


def _cards(html):
    """(id, rank, name) for each card, in the order they are written."""
    out = []
    for m in re.finditer(
            r'<article class="evi-ev [^"]*" id="([^"]+)">'
            r'<div class="rk">(\d+)</div>.*?<h4 class="nm">(.*?)</h4>', html, re.S):
        out.append((m.group(1), int(m.group(2)), re.sub(r"<[^>]+>", "", m.group(3))))
    return out


# -- the citation tag ------------------------------------------------------

def test_a_citation_tag_never_reaches_the_page(page_script):
    """The defect this file was opened for: the tag was escaped and printed,
    so the reader saw the opening tag in the middle of a sentence."""
    html = _render(page_script, _run([_event(fit_reasoning=_CITED)]))
    assert "cite" not in html.lower(), "citation markup is still reaching the page"
    assert "index=" not in html


def test_the_quotation_inside_a_citation_tag_survives_it(page_script):
    """Deleting the tag is not enough. What it wraps is a direct quote from
    someone else's page, and the sentence around it reads as one. Dropping the
    marks turns a quotation into our own claim about their event."""
    html = _render(page_script, _run([_event(fit_reasoning=_CITED)]))
    assert "47 states and 9 countries" in html, "the quoted words were dropped"
    assert "\u201c47 states and 9 countries\u201d" in html, \
        "the quotation lost its marks and now reads as our own claim"
    assert '""' not in html and '\u201c"' not in html, \
        "the model's own quote marks were doubled up by ours"


def test_an_unclosed_citation_tag_is_removed_rather_than_shown(page_script):
    dangling = "It draws " + "<" + 'cite index="3">' + "them."
    html = _render(page_script, _run([_event(fit_reasoning=dangling)]))
    assert "cite" not in html.lower()
    assert "them." in html


def test_prose_that_is_not_a_citation_tag_is_still_escaped(page_script):
    """citeText returns HTML, so it is the one place in this view where an
    escape could silently go missing."""
    html = _render(page_script, _run([_event(
        name="Ops <script>alert(1)</script> Summit",
        fit_reasoning='They say <b onclick="x">buy now</b>.')]))
    assert "<script>" not in html and "<b onclick" not in html, \
        "model prose reached the page as live markup"
    assert "&lt;script&gt;" in html and "&lt;b onclick" in html


# -- the section that was rendering as nothing ----------------------------

def test_a_discover_run_that_found_one_event_shows_it(page_script):
    """The old guard suppressed the list below two events, because in lookup
    mode the single event IS the drawer heading. In discover mode the heading
    is the query, so a search that found exactly one event showed nothing."""
    html = _render(page_script, _run([_event(name="The Only One")]))
    assert "The Only One" in html, "a one-event discover run rendered no events"


def test_a_lookup_run_does_not_repeat_its_own_subject(page_script):
    """The other direction: in lookup mode the heading already names it."""
    html = _render(page_script, _run([_event(name="The Only One")], mode="lookup"))
    assert "evi-ev" not in html, \
        "the lookup drawer now lists the event its own heading names"


# -- the rail is a claim about time ---------------------------------------

def test_an_event_with_no_date_is_not_drawn_on_the_rail(page_script):
    dated = [_event(id=1, starts_on="2027-05-04", ends_on="2027-05-06"),
             _event(id=2, name="Second", starts_on="2027-09-01", ends_on="2027-09-02")]
    undated = _event(id=3, name="Undated Summit", starts_on=None, ends_on=None)
    html = _render(page_script, _run(dated + [undated]))
    assert 'class="evi-rail"' in html, "the rail was not drawn for two dated events"
    assert html.count('class="mk ') == 2, \
        "the rail drew %d markers for 2 dated events" % html.count('class="mk ')
    assert "Undated Summit" in html.split('class="evi-events"')[0], \
        "the undated event is not named beside the rail"
    assert "guessed one" in html, "nothing tells the reader why it is missing"


def test_the_rail_is_not_drawn_at_all_when_almost_nothing_is_dated(page_script):
    """One point is not a calendar. Drawing an axis for it invites the reader
    to read a spread off a single mark."""
    html = _render(page_script, _run([
        _event(id=1, starts_on="2027-05-04", ends_on="2027-05-06"),
        _event(id=2, name="Second", starts_on=None, ends_on=None)]))
    assert 'class="evi-rail"' not in html


def test_an_undated_event_says_so_on_its_own_card(page_script):
    html = _render(page_script, _run([_event(starts_on=None, ends_on=None)]))
    assert "No date announced" in html


def test_a_start_date_in_the_past_is_called_finished(page_script):
    """discover returns a past edition when no future one is announced. The
    reader has to be able to see that from the card."""
    html = _render(page_script, _run([_event(starts_on="2001-03-01",
                                             ends_on="2001-03-02")]))
    assert "already finished" in html


def test_the_rail_scrolls_in_its_own_box_rather_than_squeezing(page_script):
    """Every position on the rail is a percentage of its width. Below a
    minimum the markers pile into one smudge and the month labels print over
    each other, so the rail has a floor and a scroller of its own."""
    html = _render(page_script, _run([
        _event(id=1, starts_on="2027-05-04", ends_on="2027-05-06"),
        _event(id=2, name="Second", starts_on="2027-09-01", ends_on="2027-09-02")]))
    assert 'class="evi-railwrap"' in html


# -- order ----------------------------------------------------------------

def _three():
    return [_event(id=1, name="First", starts_on="2027-11-01", ends_on="2027-11-02",
                   fit_score=95),
            _event(id=2, name="Second", starts_on="2027-06-01", ends_on="2027-06-02",
                   fit_score=88),
            _event(id=3, name="Third", starts_on=None, ends_on=None, fit_score=70)]


def test_the_default_order_is_the_ranking_the_form_promised(page_script):
    cards = _cards(_render(page_script, _run(_three())))
    assert [c[2] for c in cards] == ["First", "Second", "Third"]
    assert [c[1] for c in cards] == [1, 2, 3]


def test_sorting_by_date_reorders_the_cards(page_script):
    cards = _cards(_render(page_script, _run(_three()), sort="date"))
    assert [c[2] for c in cards] == ["Second", "First", "Third"], \
        "the date order is %r" % [c[2] for c in cards]


def test_a_re_sort_does_not_renumber_the_ranking(page_script):
    """The badge is the position in the ranking, not the position in the list.
    Renumbering it under a date sort quietly relabels the third best fit as
    the first."""
    cards = _cards(_render(page_script, _run(_three()), sort="date"))
    byname = {c[2]: c[1] for c in cards}
    assert byname == {"First": 1, "Second": 2, "Third": 3}, byname


def test_an_undated_event_sorts_last_by_date_rather_than_first(page_script):
    cards = _cards(_render(page_script, _run(_three()), sort="date"))
    assert cards[-1][2] == "Third"


# -- read against unread --------------------------------------------------

def _sampled_run():
    return _run([_event(id=1, name="Read One"),
                 _event(id=2, name="Read Two", starts_on="2027-07-01",
                        ends_on="2027-07-02"),
                 _event(id=3, name="Unread", starts_on="2027-08-01",
                        ends_on="2027-08-02")],
                summary={"harvested_event_ids": [1, 2],
                         "target_overlap": {"1": ["Acme", "Globex"]}})


def test_a_roster_that_was_read_and_one_that_was_not_do_not_read_alike(page_script):
    """The distinction this agent exists for. "We read it and none of yours
    were on it" and "we never read it" are opposite findings that render as
    the same absence unless something says which happened."""
    html = _render(page_script, _sampled_run())
    assert "Roster read, none of yours" in html
    assert "Roster not read" in html


def test_the_unread_marker_lands_on_the_card_that_owns_it(page_script):
    html = _render(page_script, _sampled_run())
    cards = re.split(r'(?=<article class="evi-ev )', html)
    unread = [c for c in cards if c.startswith("<article") and "Unread" in c]
    assert len(unread) == 1
    assert "Roster not read" in unread[0]
    assert "Roster read, none of yours" not in unread[0], \
        "the unread event is also claiming its roster was read"


def test_an_event_whose_roster_holds_target_accounts_names_them(page_script):
    html = _render(page_script, _sampled_run())
    assert "Acme, Globex" in html
    assert "evi-overlap" in html


def test_nothing_claims_a_roster_state_when_no_roster_was_sampled(page_script):
    """With no harvest at all, every one of these lines would be a guess."""
    html = _render(page_script, _run(_three()))
    assert "Roster not read" not in html and "Roster read" not in html


# -- the charts are the numbers -------------------------------------------

def test_the_fit_spread_has_one_bar_for_every_event(page_script):
    html = _render(page_script, _run(_three()))
    bars = re.findall(r'<div class="tbars">(.*?)</div>', html, re.S)
    assert len(bars) == 1
    assert bars[0].count("<i ") == 3


def test_an_unscored_event_still_gets_a_bar_and_is_not_drawn_as_a_zero(page_script):
    html = _render(page_script, _run([_event(id=1, fit_score=None),
                                      _event(id=2, name="B", fit_score=90)]))
    bars = re.findall(r'<div class="tbars">(.*?)</div>', html, re.S)[0]
    assert bars.count("<i ") == 2
    assert "not scored" in bars, "an unscored event is drawn as if it had a score"


def test_a_format_the_organiser_never_stated_is_its_own_segment(page_script):
    """Folding it into "in person" because most events are would be inventing
    the answer for every event that did not publish one."""
    html = _render(page_script, _run([_event(id=1, format="in_person"),
                                      _event(id=2, name="B", format=None)]))
    assert "Not stated 1" in html
    assert "In person 1" in html
    assert "Format not stated" in html, "the card does not say the format is unknown"


def test_the_stated_size_is_marked_as_the_events_own_claim(page_script):
    html = _render(page_script, _run([_event(stated_size="12,000+ attendees")]))
    assert "12,000+ attendees" in html
    assert "Nothing here estimates one" in html


# -- links ----------------------------------------------------------------

def test_a_website_that_is_not_http_is_not_turned_into_a_link(page_script):
    html = _render(page_script, _run([_event(website="javascript:alert(1)")]))
    assert "javascript:" not in html
    assert "A Conference" in html


def test_a_real_website_is_linked_and_opens_safely(page_script):
    html = _render(page_script, _run([_event(website="https://example.com/e")]))
    assert 'href="https://example.com/e"' in html
    assert 'rel="noopener noreferrer"' in html


# -- the handlers behind the controls -------------------------------------

def test_every_onclick_this_view_writes_is_a_function_that_exists(page_script):
    """An inline handler naming a function that was never put on window is a
    dead control that looks alive: the marker takes a hover, takes a click,
    and throws into the console. Nothing else in this suite would notice."""
    run = _run([_event(id=1, name="First", starts_on="2027-11-01",
                       ends_on="2027-11-02"),
                _event(id=2, name="Second", starts_on="2027-06-01",
                       ends_on="2027-06-02")])
    html = _render(page_script, run)
    names = sorted(set(re.findall(r'onclick="([A-Za-z_$][\w$]*)\(', html)))
    assert names, "the view writes no handlers at all any more"

    at = page_script.index(_IIFE_CLOSE)
    probe = ("\ncurrent = __RUN;\nrender(__RUN);\n"
             "console.log(JSON.stringify(%s.map(function(n){"
             " return [n, typeof window[n]]; })));\n" % json.dumps(names))
    js = "%s\nvar __RUN = %s;\n%s" % (
        _SHIM, json.dumps(run), page_script[:at] + probe + page_script[at:])
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stderr[-2000:]
    kinds = json.loads(r.stdout.strip().splitlines()[-1])
    missing = [n for n, t in kinds if t != "function"]
    assert not missing, "these handlers are named on the page but never exported: %r" % missing


def test_every_rail_marker_points_at_a_card_that_is_on_the_page(page_script):
    """The marker's whole job is to take you to the card. An anchor that does
    not resolve is a click that silently does nothing."""
    html = _render(page_script, _run([
        _event(id=1, name="First", starts_on="2027-11-01", ends_on="2027-11-02"),
        _event(id=2, name="Second", starts_on="2027-06-01", ends_on="2027-06-02")]))
    targets = set(re.findall(r"onclick=\"eviJump\('([^']+)'\)\"", html))
    ids = {c[0] for c in _cards(html)}
    assert targets, "the rail wrote no jump targets"
    assert targets <= ids, "markers point at %r, cards are %r" % (targets, ids)
