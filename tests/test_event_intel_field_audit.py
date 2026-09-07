"""The seven defects a field-by-field audit of the shipped agent found.

Every one of them was a thing the page promised and the code did not do, and
every one passed the whole suite. They are grouped by the promise they broke.
"""

import json

import pytest

from tracker import claude_websearch
from tracker import event_intel_audit as AU
from tracker import event_intel_discover as D
from tracker import event_intel_report as RP
from tracker import event_intel_rubric as R
from tracker import event_intel_store as store

PROF = {"client_name": "C", "classification": R.CLASS_B2B_TO_MARKETING}
GENERIC = {"measured": False, "flagged": False, "checked": 0,
           "why_not_measured": "nothing to compare", "comparisons": [], "worst": None,
           "advice": ""}


def _ask(monkeypatch, payload=None, error=None):
    monkeypatch.setattr(claude_websearch, "ask",
                        lambda s, u, **k: {"text": json.dumps(payload or {}),
                                           "error": error, "search_count": 1})


def _assume(**kw):
    base = dict(shortfall=[], audit={"checked": 0}, generic=GENERIC, candidates=[],
                scoring_errors=[], interchangeable=[], banned=[], thin=[], unscored=[])
    base.update(kw)
    return RP.assumptions(**base)


# ── F1. A marquee event must never vanish with "0 were cut" beside it ─────

FAMOUS = [{"name": "Dreamforce", "famous": True}, {"name": "CES", "famous": True},
          {"name": "Data Council", "famous": False}]


def _per_event(monkeypatch, by_name: dict, default=None):
    """Reply to each per-event audit call according to which event it names.

    The audit is one call per famous event now, so a single canned payload
    would answer every event identically and could not express "this one
    worked and that one did not", which is the case these tests are about.
    """
    def fake_ask(system, user, **kw):
        for name, reply in by_name.items():
            if name in user:
                return reply
        return default if default is not None else {
            "text": "", "error": {"kind": "transport", "detail": "no stub"}}
    monkeypatch.setattr(claude_websearch, "ask", fake_ask)


def _ok(payload):
    return {"text": json.dumps(payload), "error": None, "search_count": 2}


def test_an_event_whose_own_audit_broke_is_counted_and_named(monkeypatch):
    """The audit is one call per marquee event, so one broken call must cost
    that event's verdict and nothing else.

    It is NOT cut. The skill's rule is that a marquee name justifies its place
    or goes, but the justification it lost is a comparison that never
    happened: nothing was weighed against it, so there is no result to act on.
    Cutting it would delete a real recommendation from a paying client's list
    on a transport error, which is exactly what the single-call shape refused
    to do and what splitting the call would otherwise have made routine.

    What it must never do is leave silently. The reader has to be told which
    marquee event was left unweighed and why.
    """
    _per_event(monkeypatch, {
        "Dreamforce": _ok({"verdict": "kept", "alternative": "Data Council",
                           "why": "denser"}),
        "CES": {"text": "", "error": {"kind": "transport", "detail": "HTTP 503"}},
    })
    a = AU.audit_famous(FAMOUS, PROF)

    assert a["error"] is None, (
        "one broken call out of two reported the whole audit as failed")
    assert list(a["failed"]) == [AU.event_key(FAMOUS[1])]
    assert a["failed"][AU.event_key(FAMOUS[1])]["name"] == "CES"

    survivors = AU.apply_audit(FAMOUS, a)
    assert "CES" in [c["name"] for c in survivors], (
        "a marquee event was cut for losing a comparison that never ran")
    ces = [c for c in survivors if c["name"] == "CES"][0]
    assert ces["audit_verdict"] == AU.VERDICT_UNAUDITED
    assert "could not be completed" in ces["audit_note"]

    line = [l for l in _assume(audit=a, candidates=survivors)
            if "marquee" in l][0]
    assert "1 marquee event was audited" in line, (
        "2 were sent and 1 was weighed; claiming 2 is a report describing "
        "what the stage intended: %r" % line)
    assert "CES" in line, "an unaudited event that is never named cannot be checked"


def test_a_weighed_cut_reads_differently_from_an_unaudited_one(monkeypatch):
    """"We compared it and it lost" and "its audit never completed" are
    different facts and the report keeps them apart."""
    _per_event(monkeypatch, {
        "Dreamforce": _ok({"verdict": "cut", "alternative": "Data Council",
                           "why": "broad"}),
        "CES": {"text": "", "error": {"kind": "transport", "detail": "HTTP 503"}},
    })
    a = AU.audit_famous(FAMOUS, PROF)
    line = [l for l in _assume(audit=a, candidates=AU.apply_audit(FAMOUS, a))
            if "marquee" in l][0]
    assert "Cut after weighing: Dreamforce" in line
    assert "could not be audited" in line and "CES" in line
    assert "Cut after weighing: Dreamforce, CES" not in line


@pytest.mark.parametrize("payload", [
    {"audits": []},                                            # parsed, empty
    {"verdicts": [{"name": "Dreamforce", "verdict": "kept"}]},  # foreign envelope
    {"note": "both look fine to me"},                          # no verdict at all
])
def test_an_audit_that_yields_nothing_usable_cuts_nobody(monkeypatch, payload):
    """A call that succeeded and produced no readable verdict has audited
    nothing. Cutting every flagship on it is the same silent wrong answer the
    module already refuses to give on a transport error.

    The middle payload is the one worth keeping. A `{"verdicts": [...]}`
    envelope is not a shape this prompt asks for, and a parser that scans
    forward for a "verdict" key anywhere would pull the first row out of it
    and cut a real event on a reply it never understood.
    """
    _ask(monkeypatch, payload)
    a = AU.audit_famous(FAMOUS, PROF)
    assert a["error"], "a useless reply was accepted as a clean audit"
    survivors = [c["name"] for c in AU.apply_audit(FAMOUS, a)]
    assert "Dreamforce" in survivors and "CES" in survivors
    line = [l for l in _assume(audit=a, candidates=[]) if "audit" in l][0]
    assert "no usable result" in line


def test_a_legacy_envelope_reply_is_still_read_and_still_enforced(monkeypatch):
    """`{"audits": [{...}]}` was the shape the single-call prompt asked for.

    A reply in it is a complete answer to a one-event question, and refusing
    it would throw away a live search already paid for. It gets no exemption
    from the enforcement, though: this one claims "kept" and names nothing it
    was weighed against, which the module downgrades to a cut. Before the
    split that same reply was refused wholesale, because there was no way to
    tell which event an unnamed verdict belonged to; a call about one known
    event has nothing to match up.
    """
    _ask(monkeypatch, {"audits": [{"verdict": "kept"}]})
    a = AU.audit_famous(FAMOUS[:1], PROF)
    assert a["error"] is None
    v = a["verdicts"][AU.event_key(FAMOUS[0])]
    assert v["verdict"] == AU.VERDICT_CUT
    assert "no more targeted alternative was named" in v["why"]
    assert v["name"] == "Dreamforce", (
        "the verdict must be keyed and labelled from the candidate, not from "
        "a name the model never sent")


def test_a_run_with_no_famous_events_is_not_reported_as_a_failed_audit(monkeypatch):
    """Over-applying the check would put a scary line on every clean run."""
    called = []
    monkeypatch.setattr(claude_websearch, "ask",
                        lambda s, u, **k: called.append(1) or {"text": "{}", "error": None})
    a = AU.audit_famous([{"name": "X", "famous": False}], PROF)
    assert a["error"] is None and a["checked"] == 0
    assert not called, "the model was called with nothing to audit"


def test_the_report_never_says_an_audit_did_not_run_when_it_did(monkeypatch):
    _ask(monkeypatch, {"audits": []})
    a = AU.audit_famous(FAMOUS, PROF)
    line = [l for l in _assume(audit=a) if "audit" in l][0]
    assert "did not run" not in line, "self-contradictory: %r" % line


def test_singular_and_plural_are_correct_in_the_audit_line(monkeypatch):
    for n, cands in ((1, FAMOUS[:1]), (2, FAMOUS[:2])):
        _ask(monkeypatch, {"audits": [{"name": c["name"], "verdict": "kept",
                                       "alternative": "Data Council", "why": "w"}
                                      for c in cands]})
        a = AU.audit_famous(cands, PROF)
        line = [l for l in _assume(audit=a) if "marquee" in l][0]
        assert ("1 marquee event was" if n == 1 else "2 marquee events were") in line, line


# ── F2. Events that cleared the bar must not vanish to the cap ────────────

def test_events_dropped_only_for_list_length_are_named(monkeypatch):
    """rank() computes over_cap precisely so this can be said. Truncating in
    silence reads as "nothing else qualified"."""
    rows = [{"name": "E%02d" % i, "total": 100 - i, "tier": "P1"} for i in range(20)]
    ranked = R.rank(rows, cap=15)
    assert len(ranked["over_cap"]) == 5
    s = RP.executive_summary(profile=PROF, ranked=ranked, shortfall=[],
                             audit={"checked": 0}, generic=GENERIC, scoring_errors=[],
                             interchangeable=[], banned=[], thin=[], unscored=[])
    line = [a for a in s["assumptions"] if "maximum list length" in a]
    assert line, "five events cleared the bar and were dropped with nothing said"
    assert "E15" in line[0] and "E19" in line[0]


def test_nothing_is_said_about_the_cap_when_nothing_was_dropped():
    rows = [{"name": "E%d" % i, "total": 90, "tier": "P1"} for i in range(3)]
    s = RP.executive_summary(profile=PROF, ranked=R.rank(rows, cap=15), shortfall=[],
                             audit={"checked": 0}, generic=GENERIC, scoring_errors=[],
                             interchangeable=[], banned=[], thin=[], unscored=[])
    assert not [a for a in s["assumptions"] if "maximum list length" in a]


# ── F6. force_include: an event already paid for ──────────────────────────

def test_a_committed_event_reaches_the_discovery_prompt():
    brief = D.profile_brief({**PROF, "force_include": "Money20/20"})
    assert "Money20/20" in brief
    assert "ALREADY COMMITTED" in brief


def test_committed_events_are_flagged_in_code_not_by_the_model():
    merged = D.merge({R.CATEGORIES[0]: [{"name": "Money20/20 USA 2026"},
                                        {"name": "Data Council"}]},
                     None, "Money20/20")
    flags = {c["name"]: c["committed"] for c in merged}
    assert flags == {"Money20/20 USA 2026": True, "Data Council": False}


def test_force_exclude_still_wins_over_everything():
    merged = D.merge({R.CATEGORIES[0]: [{"name": "CES"}]}, "CES", "CES")
    assert merged == [], "an excluded event came back because it was also committed"


def test_a_committed_event_below_the_bar_is_kept_and_marked():
    """Money already spent on an event that does not clear the bar is the most
    actionable thing this analysis produces. Cutting it hides exactly that."""
    ranked = R.rank([{"name": "PaidFor", "total": 45, "tier": "P3", "committed": True},
                     {"name": "Junk", "total": 45, "tier": "P3"},
                     {"name": "Good", "total": 88, "tier": "P1"}])
    assert [c["name"] for c in ranked["kept"]] == ["Good", "PaidFor"]
    assert [e["name"] for e in ranked["excluded"]] == ["Junk"]
    assert ranked["committed_below_bar"] == [{"name": "PaidFor", "total": 45}]
    assert ranked["counts"]["committed_below_bar"] == 1


def test_the_cap_never_drops_a_committed_event():
    rows = ([{"name": "G%02d" % i, "total": 95 - i, "tier": "P1"} for i in range(18)]
            + [{"name": "PaidFor", "total": 71, "tier": "P2", "committed": True}])
    ranked = R.rank(rows, cap=5)
    assert "PaidFor" in [c["name"] for c in ranked["kept"]]
    assert "PaidFor" not in [c["name"] for c in ranked["over_cap"]]


def test_the_committed_flag_survives_the_store_round_trip():
    """Set in code at discovery, read back at ranking. If the column does not
    carry it the rescue silently stops happening."""
    row = store.normalise_candidate({"name": "M", "category": R.CATEGORIES[0],
                                     "relevance": 10, "dm_access": 10,
                                     "engagement": 5, "committed": True})
    assert row["committed"] is True
    assert set(store._CANDIDATE_FIELDS) == set(row)


def test_a_model_cannot_invent_a_commitment():
    """The flag is derived from the user's own profile, so a discovered event
    that claims committed:true without being on the list stays false."""
    merged = D.merge({R.CATEGORIES[0]: [{"name": "Sneaky", "committed": True}]},
                     None, "Money20/20")
    assert merged[0]["committed"] is False


@pytest.mark.parametrize("written,found,expected", [
    ("Money20/20", "Money20/20 USA 2026", True),
    ("SaaStr Annual", "SaaStr Annual 2026", True),
    # Containment in the other direction: what the user typed is SHORTER than
    # what came back. Equality alone would miss this, and it is the common
    # case, because people write the event, not the edition.
    ("SaaStr", "SaaStr Annual", True),
    ("Money20/20", "Money20/20 Europe Amsterdam", True),
    ("Money20/20", "Data Council", False),
    ("", "Anything", False),
])
def test_a_commitment_matches_across_editions(written, found, expected):
    assert D.is_committed(found, D.committed_keys(written)) is expected


def test_every_line_of_the_commitment_list_is_its_own_event():
    """Parsed as one blob, "Money20/20\nSaaStr" becomes a single key that
    matches neither, and both commitments silently stop being honoured."""
    keys = D.committed_keys("Money20/20\nSaaStr Annual\nWeb Summit")
    assert len(keys) == 3
    for name in ("Money20/20 USA", "SaaStr Annual 2026", "Web Summit Lisbon"):
        assert D.is_committed(name, keys), "%s stopped matching" % name
    assert not D.is_committed("Data Council", keys)


@pytest.mark.parametrize("sep", ["\n", ",", ";"])
def test_the_commitment_list_accepts_the_separators_people_actually_type(sep):
    keys = D.committed_keys(sep.join(["Money20/20", "SaaStr Annual"]))
    assert len(keys) == 2


# ── The three findings that live in the report renderer ───────────────────
#
# Executed in node against the page's real script, not grepped: every one of
# these was a promise the form made that the renderer did not keep, and a
# grep for the fix would pass against code that never runs.

import os
import re
import shutil
import subprocess
import sys

import app as appmod

_PAGE = "/p2/b2b-agents/event-conference-intelligence"
_IIFE_CLOSE = "\n  })();"
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

nodeonly = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


def _script():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "harness@position2.com", "name": "T"}
    resp = c.get(_PAGE)
    assert resp.status_code == 200
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>",
                        resp.get_data(as_text=True), re.S)
    return blocks[0]


def _render(run):
    """Run the page's real render() over a run payload, return the drawer HTML."""
    script = _script()
    at = script.index(_IIFE_CLOSE)
    probe = ("\n    render(%s);\n    console.log(__html['drawerBody'] || '');\n"
             % json.dumps(run))
    body = """
var __html = {};
function __el(id){
  return {
    _id: id,
    set innerHTML(v){ __html[id] = v; }, get innerHTML(){ return __html[id] || ''; },
    set textContent(v){ __html[id] = v; }, get textContent(){ return __html[id] || ''; },
    style: {}, classList: {add:function(){},remove:function(){},toggle:function(){},
                           contains:function(){return false;}},
    getAttribute: function(){ return null; }, setAttribute: function(){},
    disabled: false, value: '', focus: function(){}, scrollIntoView: function(){}
  };
}
var document = { readyState: 'complete', getElementById: __el,
  addEventListener: function(){}, querySelector: function(){ return null; },
  querySelectorAll: function(){ return []; }, createElement: function(){ return __el('x'); } };
var window = {addEventListener: function(){}};
var location = {hash: '', pathname: '/'};
var fetch = function(){ return Promise.resolve({ok:true, json:function(){return Promise.resolve({});}}); };
var setInterval = function(){ return 0; };
var clearInterval = function(){};
""" + script[:at] + probe + script[at:]
    path = os.path.join(_ROOT, ".pytest_field_probe.js")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(body)
        proc = subprocess.run(["node", path], capture_output=True, text=True, timeout=40)
        assert proc.returncode == 0, "node failed:\n%s" % proc.stderr[-2000:]
        return proc.stdout
    finally:
        if os.path.exists(path):
            os.remove(path)


def _recommend(**over):
    run = {"id": 7, "mode": "recommend", "status": "complete", "stage": "done",
           "error": None, "query": "C", "credits_spent": 0,
           "events": [], "participants": [], "sources": [], "candidates": [],
           "role_labels": {},
           "profile": {"client_name": "C", "max_events": 15},
           "summary": {"title": "C: Conference Analysis", "client_profile": "p",
                       "assumptions": [], "top_five": [], "counts": {"kept": 0},
                       "shortfall": [], "statuses": {}}}
    prof = over.pop("profile", None)
    if prof:
        run["profile"].update(prof)
    run["summary"].update(over.pop("summary", {}))
    run.update(over)
    return run


@nodeonly
def test_the_budget_the_user_typed_is_actually_shown():
    """The field's own hint promises it is shown. It was collected, stored,
    and rendered nowhere."""
    html = _render(_recommend(profile={"budget_note": "about $40k for the year"}))
    assert "about $40k for the year" in html
    assert "Never used to score" in html


@nodeonly
def test_no_budget_block_appears_when_none_was_recorded():
    assert "Budget you recorded" not in _render(_recommend())


@nodeonly
def test_a_failed_qualification_batch_is_surfaced_in_work_the_room():
    """Companies showing as "not qualified" after an API failure is the
    empty-versus-error conflation this agent refuses everywhere else."""
    run = {"id": 8, "mode": "workroom", "status": "complete", "stage": "done",
           "error": None, "query": "E", "credits_spent": 0, "role_labels": {},
           "events": [], "participants": [], "sources": [], "outreach": [],
           "summary": {"event_name": "E", "counts": {"kept": 0, "roster": 0},
                       "window": None, "qualify_errors": ["overloaded: 529"],
                       "repeats": {}, "floor": 55}}
    html = _render(run)
    assert "did not run" in html
    assert "overloaded: 529" in html
    assert "failure to look rather than a judgement" in html


@nodeonly
def test_no_qualification_warning_when_every_batch_succeeded():
    run = {"id": 8, "mode": "workroom", "status": "complete", "stage": "done",
           "error": None, "query": "E", "credits_spent": 0, "role_labels": {},
           "events": [], "participants": [], "sources": [], "outreach": [],
           "summary": {"event_name": "E", "counts": {"kept": 0, "roster": 0},
                       "window": None, "qualify_errors": [], "repeats": {}, "floor": 55}}
    assert "failure to look" not in _render(run)


def _discover(**summary):
    s = {"discovered_events": 4, "harvested_events": 2, "sources_tried": 1,
         "by_role": {}, "target_overlap": {}, "harvested_event_ids": []}
    s.update(summary)
    return {"id": 9, "mode": "discover", "status": "complete", "stage": "done",
            "error": None, "query": "audience", "credits_spent": 0,
            "role_labels": {}, "participants": [], "sources": [], "summary": s,
            "events": [{"id": i, "name": "Ev%d" % i, "fit_score": 50,
                        "website": "https://e%d.example" % i} for i in (1, 2, 3, 4)]}


@nodeonly
def test_discover_renders_in_the_order_the_pipeline_ranked():
    """The form promises events are ranked by how many of your accounts are in
    the roster. They were rendered in discovery order."""
    html = _render(_discover(event_order=[3, 1, 4, 2],
                             harvested_event_ids=[1, 3],
                             target_overlap={"3": ["Acme", "Beta"], "1": ["Gamma"]}))
    pos = [html.index("Ev%d" % i) for i in (3, 1, 4, 2)]
    assert pos == sorted(pos), "the ranked order was not used"


@nodeonly
def test_a_roster_nobody_read_never_looks_like_one_with_no_hits():
    """The two are identical in words and mean opposite things.

    Both states used to be a paragraph under the card, which meant the same
    twenty words printed under four cards in a row. They are chips in the
    card's own tag row now. The claim is unchanged and is what this asserts:
    the two states must be distinguishable, and neither may be claimed for
    the event whose roster actually held a hit.
    """
    html = _render(_discover(event_order=[1, 2, 3, 4],
                             harvested_event_ids=[1, 2],
                             target_overlap={"1": ["Acme"]}))
    read = "Roster read, none of yours"
    unread = "Roster not read"
    assert read in html and unread in html
    assert read != unread
    # Ev1's roster was read AND held a hit, so it must claim neither.
    cards = re.split(r'(?=<article class="evi-ev )', html)
    ev1 = [c for c in cards if c.startswith("<article") and "Ev1" in c]
    assert len(ev1) == 1
    assert read not in ev1[0] and unread not in ev1[0]
    assert "Acme" in ev1[0]


@nodeonly
def test_the_list_on_screen_is_as_long_as_the_count_printed_above_it():
    """The exec summary prints a kept count from rank(); the renderer used to
    slice with its own defaulted max_events, so the two could disagree and the
    difference was invisible."""
    cands = [{"name": "E%02d" % i, "tier": "P1", "total": 100 - i,
              "relevance": 36, "dm_access": 34, "engagement": 17,
              "matchmaking": 0, "category": "industry_flagship", "gaps": []}
             for i in range(9)]
    run = _recommend(candidates=cands,
                     profile={"max_events": 15},
                     summary={"counts": {"kept": 4, "P1": 4, "P2": 0, "excluded": 0}})
    html = _render(run)
    shown = [n for n in ["E%02d" % i for i in range(9)] if n in html]
    assert len(shown) == 4, "summary said 4 kept, the list showed %d" % len(shown)


@nodeonly
def test_nothing_is_claimed_about_sampling_when_no_targets_were_given():
    html = _render(_discover())
    assert "was not read" not in html
    assert "none of your target accounts" not in html


@nodeonly
def test_a_committed_event_below_the_bar_is_actually_rendered():
    """It is P3 by definition. The renderer filtered P3 unconditionally, so
    the one row this whole feature exists to show never reached the screen."""
    cands = [
        {"name": "Good", "tier": "P1", "total": 88, "relevance": 36, "dm_access": 34,
         "engagement": 18, "matchmaking": 0, "category": "industry_flagship", "gaps": []},
        {"name": "PaidFor", "tier": "P3", "total": 43, "committed": True,
         "relevance": 18, "dm_access": 16, "engagement": 9, "matchmaking": 0,
         "category": "industry_flagship", "gaps": []},
        {"name": "Junk", "tier": "P3", "total": 41, "relevance": 18, "dm_access": 14,
         "engagement": 9, "matchmaking": 0, "category": "industry_flagship", "gaps": []},
    ]
    html = _render(_recommend(candidates=cands,
                              summary={"counts": {"kept": 2, "P1": 1, "P2": 0}}))
    assert "PaidFor" in html, "the committed event was filtered out as a P3"
    assert "already committed" in html, "it rendered without its marker"
    assert "Junk" not in html, "an uncommitted P3 was let onto the list"


@nodeonly
def test_an_uncommitted_p3_never_reaches_the_ranked_list():
    """Separate from the test above, with the list length set wide enough that
    the slice cannot be what excludes it. Otherwise "we filter P3" and "the
    list happened to be short" are indistinguishable."""
    cands = [
        {"name": "Good", "tier": "P1", "total": 88, "relevance": 36, "dm_access": 34,
         "engagement": 18, "matchmaking": 0, "category": "industry_flagship", "gaps": []},
        {"name": "Junk", "tier": "P3", "total": 41, "relevance": 18, "dm_access": 14,
         "engagement": 9, "matchmaking": 0, "category": "industry_flagship", "gaps": []},
    ]
    html = _render(_recommend(candidates=cands,
                              summary={"counts": {"kept": 9, "P1": 1, "P2": 0}}))
    assert "Good" in html
    assert "Junk" not in html, "a P3 that nobody committed to reached the list"
