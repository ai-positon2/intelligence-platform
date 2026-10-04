"""The intake form's redesign (2026-10-04): what each new piece says.

The note counter is checked against the server's own parser, line by line,
because a counter that disagrees with what the drafter reads is worse than no
counter. The rest render the real template and read what a person would see.
"""

import json
import os
import re
import shutil
import subprocess

import pytest

import app as appmod
from tracker import event_intel_workroom as W

ROOT = os.path.join(os.path.dirname(__file__), "..")
CSS = os.path.join(ROOT, "static", "css", "event_intel_intake.css")
TEMPLATE = os.path.join(ROOT, "templates", "event_conference_intelligence.html")
_PAGE = "/p2/strategic-agents/event-conference-intelligence"

node = pytest.mark.skipif(shutil.which("node") is None, reason="node runs the page script")


def _script():
    html = open(TEMPLATE).read()
    return re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)[0]


def _fn(src, name):
    """One top-level function of the page script, by name, as source."""
    start = src.index("    function %s(" % name)
    end = src.index("\n    }\n", start) + len("\n    }\n")
    return src[start:end]


def _node(code):
    out = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ── the booth-note counter agrees with the drafter ──────────────────────────

NOTES = [
    "Acme Inc: asked about SOC2",
    "10:30 Acme: wants the deck",
    "10:30am - Globex - pricing for 200 seats",
    "Initech | follow up next week",
    "Hooli – met the CTO",
    "Initech was busy",
    "Pied Piper:",
    ": nobody",
    "!!!: punctuation only",
    "   ",
    "Société Générale: wants a call",
    "https://acme.com: the site",
    "Umbrella:no space after colon",
    "Wayne Enterprises - - double dash",
    "10:30: Acme",
    "9.15 pm | Stark: wants a demo",
]


@node
@pytest.mark.parametrize("line", NOTES)
def test_the_counter_reads_each_line_the_way_the_server_does(line):
    src = _script()
    head = src[src.index("    var NOTE_TIME"):src.index("    function countNotes(")]
    got = _node(head + "\nprocess.stdout.write(JSON.stringify(readNotes(%s)));" % json.dumps(line))
    read = 1 if W.index_booth_notes(line) else 0
    blank = not line.strip()
    assert got == {"notes": read, "skipped": 0 if (read or blank) else 1}, line


@node
def test_the_counter_totals_a_whole_day_of_notes():
    src = _script()
    head = src[src.index("    var NOTE_TIME"):src.index("    function countNotes(")]
    raw = "\n".join(NOTES)
    got = _node(head + "\nprocess.stdout.write(JSON.stringify(readNotes(%s)));" % json.dumps(raw))
    lines = [l for l in raw.splitlines() if l.strip()]
    read = sum(1 for l in lines if W.index_booth_notes(l))
    assert got == {"notes": read, "skipped": len(lines) - read}
    assert read and got["skipped"]


# ── the form says which play it is ──────────────────────────────────────────

@node
def test_the_form_head_is_copied_off_the_play_card():
    src = _script()
    code = """
var els = {};
global.document = {getElementById: function(id){
  return els[id] || (els[id] = {_a: {}, innerHTML: '', textContent: '',
    setAttribute: function(k, v){ this._a[k] = v; }});
}};
var MODES = ['recommend', 'lookup', 'workroom'];
%s
%s
var parts = {'.pi': {innerHTML: '<svg></svg>'}, '.pn': {textContent: 'Who will be at an event'},
             '.pio .a': {textContent: 'an event name'}, '.pio .b': {textContent: '\\u2192who will be there'}};
var card = {getAttribute: function(){ return 'lookup'; }, querySelector: function(s){ return parts[s] || null; }};
formHead(card, 1);
process.stdout.write(JSON.stringify({play: els.formCard._a['data-play'], kick: els.formKick.textContent,
  title: els.formTitle.textContent, icon: els.formIcon.innerHTML, io: els.formIO.innerHTML}));
""" % (_fn(src, "esc"), _fn(src, "formHead"))
    got = _node(code)
    assert got["play"] == "lookup" and got["kick"] == "Play 2 of 3"
    assert got["title"] == "Who will be at an event" and got["icon"] == "<svg></svg>"
    text = re.sub(r"<[^>]+>", " ", got["io"])
    assert "You give an event name" in " ".join(text.split())
    assert "You get who will be there" in " ".join(text.split()) and "→" not in got["io"]


def test_every_play_card_carries_what_the_form_head_reads():
    html = open(TEMPLATE).read()
    cards = re.findall(r'<button class="evi-play [^"]*".*?</button>', html, re.S)
    assert len(cards) == 3
    for c in cards:
        for cls in ('class="pi"', 'class="pn"', 'class="a"', 'class="b"', "data-play="):
            assert cls in c, cls


# ── rendered with saved clients ─────────────────────────────────────────────

PROFILE = {"id": 4, "client_name": "Northwind Analytics", "classification": "b2b_to_marketing",
           "website": "https://northwind.example", "force_include": "IMEX America",
           "force_exclude": "CES", "budget_note": "About $40k"}


def _render(monkeypatch, profiles=(PROFILE,), runs=()):
    from tracker import event_intel_store as ST
    monkeypatch.setattr(ST, "list_profiles", lambda e, limit=40: list(profiles))
    monkeypatch.setattr(ST, "list_runs", lambda e, limit=60: list(runs))
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["google_user"] = {"email": "harness@position2.com", "name": "T"}
    r = c.get(_PAGE)
    assert r.status_code == 200
    return r.get_data(as_text=True)


def test_a_saved_client_card_has_initials_a_side_and_tagged_rules(monkeypatch):
    html = _render(monkeypatch)
    card = html[html.index('data-profile="4"'):]
    card = card[:card.index("</div>\n            \n")] if "</div>\n            \n" in card else card[:4000]
    assert re.search(r'class="ps-av"[^>]*>NA<', card)
    assert 'class="ps-or" data-orient="booth"' in card
    assert re.search(r'class="ps-in"><b>Already committed', card)
    assert re.search(r'class="ps-out"><b>Never suggest', card)
    assert re.search(r'class="ps-bud"><b>Budget', card)


def test_a_one_word_client_takes_its_first_two_letters(monkeypatch):
    html = _render(monkeypatch, profiles=[dict(PROFILE, client_name="cvent")])
    assert re.search(r'class="ps-av"[^>]*>CV<', html)


def test_the_choice_cards_carry_their_side_and_signal(monkeypatch):
    html = _render(monkeypatch)
    sides = re.findall(r'data-classification="[^"]+" data-orient="(\w+)"', html)
    assert sides and set(sides) <= {"booth", "audience"} and len(set(sides)) == 2
    signals = re.findall(r'data-signal="([^"]+)"', html)
    assert signals == [W.CLASS_PLAY[k]["signal"].lower() for k in W.EVENT_CLASSES]
    # Every signal the play table uses lights its own bars, and a stronger
    # signal lights more of them.
    css = open(CSS).read()
    lit = {}
    for s in set(signals):
        m = re.search(r'\[data-signal="%s"\] \.sg i(?::nth-child\(-n\+(\d)\))?[,{\s]' % re.escape(s), css)
        assert m, s
        lit[s] = int(m.group(1) or 4)
    order = ["low-medium", "medium", "medium-high", "high"]
    assert [lit[s] for s in order if s in lit] == sorted(lit[s] for s in order if s in lit)


def test_the_run_count_is_the_list_it_heads(monkeypatch):
    runs = [{"id": i, "mode": "lookup", "query": "E%d" % i, "status": "complete",
             "participant_count": 3} for i in range(4)]
    html = _render(monkeypatch, runs=runs)
    assert re.search(r'<h3>Your runs<span class="evi-runcount">4</span></h3>', html)
    assert "evi-runcount" not in _render(monkeypatch, runs=[])


def test_the_example_events_only_fill_the_box(monkeypatch):
    html = _render(monkeypatch)
    tries = re.findall(r'<button type="button" onclick="([^"]+)">([^<]+)</button>',
                       html[html.index('class="evi-try"'):])
    assert len(tries) >= 3 and all(on == "tryEvent(this.textContent)" for on, _ in tries[:4])
    body = _fn(_script(), "tryEvent")
    assert "startRun" not in body and "submit" not in body


# ── the stylesheet ──────────────────────────────────────────────────────────

def test_the_intake_layer_is_screen_only_loaded_last_and_themed_both_ways():
    css = open(CSS).read()
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S).strip()
    assert body.startswith("@media screen {") and body.rstrip().endswith("}")
    links = re.findall(r'href="/static/css/([^"?]+)', open(TEMPLATE).read())
    assert links[-2:] == ["event_intel_report.css", "event_intel_intake.css"]
    dark = re.search(r"^\.evi-layout \{\s*(--i-ink:.*?)\}", css, re.S | re.M).group(1)
    light = re.search(r':root\[data-theme="light"\] \.evi-layout \{\s*(--i-ink:.*?)\}', css, re.S).group(1)
    assert set(re.findall(r"--[\w-]+", dark)) == set(re.findall(r"--[\w-]+", light))
    for v in set(re.findall(r"var\((--i-[\w-]+)", css)):
        assert v in dark, v
