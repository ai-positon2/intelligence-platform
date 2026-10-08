"""Which event did you mean (2026-10-08).

A roster lookup for a name several events share ("ADA Conference": dental,
diabetes, disability) used to end in a paragraph explaining the ambiguity and
nothing to do about it. The lookup now hands back the events it found, the
drawer shows them as cards, and picking one starts a lookup pinned to that
event. The pick is read back from the reader's own stored run by position,
so the page can only choose among what the lookup itself found.
"""

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_event_intel_charts import _render  # noqa: E402
from test_event_intel_event_view import page_script  # noqa: E402,F401  (fixture)

import app as appmod
from tracker import event_intel_pipeline as P
from tracker import event_intel_resolve as RS
from tracker import event_intel_store as ST

ROOT = os.path.join(os.path.dirname(__file__), "..")
TEMPLATE = os.path.join(ROOT, "templates", "event_conference_intelligence.html")

DENTAL = {"name": "SmileCon", "organizer": "American Dental Association", "edition": "2026",
          "starts_on": "2026-10-22", "location": "Indianapolis", "website": "https://smilecon.org/",
          "about": "The ADA's annual meeting for dentists and dental teams."}
DIABETES = {"name": "ADA Scientific Sessions", "organizer": "American Diabetes Association",
            "starts_on": "2026-06-19", "location": "New Orleans",
            "website": "https://professional.diabetes.org/scientific-sessions",
            "about": "Research meeting for diabetes clinicians and scientists."}
COORD = {"name": "NAADAC Annual Conference", "organizer": "National Association of ADA Coordinators",
         "website": "https://www.adacoordinator.org/conference", "about": "For ADA coordinators."}


def _reply(monkeypatch, reply, seen=None):
    def ask(system, user, **k):
        if seen is not None:
            seen.append(user)
        return {"text": json.dumps(reply), "error": None, "search_count": 4, "usage": {}}
    monkeypatch.setattr(RS.claude_websearch, "ask", ask)


# ── the lookup hands back what it found ─────────────────────────────────────

def test_an_ambiguous_name_comes_back_with_the_events_it_could_mean(monkeypatch):
    _reply(monkeypatch, {"confidence": "low", "reasoning": "Several ADAs.", "name": None,
                         "choices": [DENTAL, DIABETES, COORD]})
    res = RS.resolve_event("ADA Conference")
    assert res["ok"] is False and res["confidence"] == "low"
    assert [c["name"] for c in res["choices"]] == ["SmileCon", "ADA Scientific Sessions",
                                                   "NAADAC Annual Conference"]
    assert res["choices"][0] == dict(DENTAL)


def test_the_prompt_asks_for_the_choices():
    assert "`choices`" in RS._SYSTEM and '"choices": [{"name": str' in RS._SYSTEM


@pytest.mark.parametrize("bad", [
    {"name": "No site", "website": None},
    {"name": "Script", "website": "javascript:alert(1)"},
    {"name": "", "website": "https://e.example"},
    "not a dict",
])
def test_a_choice_that_cannot_pin_a_lookup_is_dropped(bad):
    assert RS._clean_choices([bad, DENTAL]) == [dict(DENTAL)]


def test_choices_are_deduped_capped_and_cleaned():
    raw = [dict(DENTAL), dict(DENTAL, name="SmileCon 2027", website="https://www.smilecon.org")]
    raw += [{"name": "Event %d" % i, "website": "https://e%d.example" % i} for i in range(10)]
    raw.append({"name": "Dash \u2014 name", "website": "https://dash.example", "starts_on": "soon",
                "organizer": "Informa (a company founded by somebody long ago)"})
    got = RS._clean_choices(raw)
    assert len(got) == RS.MAX_CHOICES
    assert [c["name"] for c in got][:2] == ["SmileCon", "Event 0"]
    dash = RS._clean_choices(raw[-1:])[0]
    assert "\u2014" not in dash["name"] and dash["starts_on"] is None
    assert dash["organizer"] == "Informa"


def test_a_confident_lookup_offers_no_choices(monkeypatch):
    _reply(monkeypatch, {"confidence": "high", "name": "SmileCon", "website": "https://smilecon.org",
                         "starts_on": "2026-10-22", "choices": [DIABETES]})
    res = RS.resolve_event("SmileCon")
    assert res["ok"] is True and "choices" not in res


def test_a_picked_lookup_is_pinned_to_that_event(monkeypatch):
    seen = []
    _reply(monkeypatch, {"confidence": "high", "name": "SmileCon", "website": "https://smilecon.org",
                         "starts_on": "2026-10-22"}, seen)
    assert RS.resolve_event("SmileCon", None, DENTAL)["ok"] is True
    assert "picked THIS one" in seen[0] and "Website: https://smilecon.org/" in seen[0]
    assert "Organiser: American Dental Association" in seen[0]


def test_a_picked_lookup_that_still_cannot_choose_offers_no_second_list(monkeypatch):
    _reply(monkeypatch, {"confidence": "low", "name": None, "choices": [DENTAL, DIABETES]})
    res = RS.resolve_event("SmileCon", None, DENTAL)
    assert res["ok"] is False and res["choices"] == []


# ── the run waits on the reader ─────────────────────────────────────────────

def _lookup_run(monkeypatch, res):
    runs, calls = {}, []
    monkeypatch.setattr(P.store, "update_run", lambda rid, **f: runs.setdefault(rid, {}).update(f))
    monkeypatch.setattr(P, "durable_stage", lambda name, fn, *a, **k: fn(*a, **k))
    monkeypatch.setattr(P.event_intel_resolve, "resolve_event",
                        lambda *a: calls.append(a) or dict(res))
    return runs, calls


@pytest.mark.parametrize("choices,says", [
    ([DENTAL, DIABETES], "matches more than one event"),
    ([DENTAL], "did not match one event exactly"),
])
def test_an_ambiguous_run_stores_the_choices_and_says_pick(monkeypatch, choices, says):
    runs, _ = _lookup_run(monkeypatch, {"ok": False, "confidence": "low",
                                        "reasoning": "Several ADAs.", "choices": choices})
    P._run_lookup(3, "ADA Conference", None)
    r = runs[3]
    assert r["status"] == "failed" and r["summary"]["choices"] == choices
    assert says in r["error"] and "“ADA Conference”" in r["error"]
    assert r["summary"]["choice_note"] == "Several ADAs."


def test_an_unfindable_run_without_choices_fails_as_before(monkeypatch):
    runs, _ = _lookup_run(monkeypatch, {"ok": False, "confidence": "none",
                                        "reasoning": "Nothing by that name.", "choices": []})
    P._run_lookup(3, "Zentrix", None)
    assert runs[3]["error"] == "Nothing by that name." and "choices" not in runs[3]["summary"]


def test_the_pick_reaches_the_resolver_and_only_when_there_is_one(monkeypatch):
    _, calls = _lookup_run(monkeypatch, {"ok": False, "confidence": "none", "choices": []})
    P.run_job(4, "lookup", "SmileCon", pick=DENTAL)
    P.run_job(5, "lookup", "Web Summit", year_hint="2026")
    assert calls == [("SmileCon", None, DENTAL), ("Web Summit", "2026")]


# ── the route reads the pick back from the reader's own run ─────────────────

def _client():
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["google_user"] = {"email": "pick@position2.com", "name": "T"}
    return c


OFFERED = {"id": 3, "mode": "lookup", "status": "failed",
           "summary": {"choices": [DENTAL, DIABETES]}}


def _route(monkeypatch, runs):
    started = []
    from tracker import event_intel_jobs as J
    monkeypatch.setattr(ST, "get_run", lambda rid, email: runs.get((rid, email)))
    monkeypatch.setattr(J, "start", lambda email, mode, query, kw, key: started.append(
        (email, mode, query, kw)) or 9)
    return started


def test_a_pick_starts_a_lookup_for_that_event(monkeypatch):
    started = _route(monkeypatch, {(3, "pick@position2.com"): OFFERED})
    r = _client().post("/p2/strategic-agents/event-conference-intelligence/run",
                       json={"mode": "lookup", "pick": {"run_id": 3, "index": 1},
                             "query": "ignored", "request_key": "k1"})
    assert r.status_code == 200 and r.get_json()["run_id"] == 9
    email, mode, query, kw = started[0]
    assert (mode, query, kw["pick"]) == ("lookup", "ADA Scientific Sessions", DIABETES)


@pytest.mark.parametrize("pick,runs", [
    ({"run_id": 3, "index": 2}, {(3, "pick@position2.com"): OFFERED}),
    ({"run_id": 3, "index": -1}, {(3, "pick@position2.com"): OFFERED}),
    ({"run_id": 3, "index": "x"}, {(3, "pick@position2.com"): OFFERED}),
    # Another account's run: get_run is scoped by email, so it is not found.
    ({"run_id": 3, "index": 0}, {(3, "someone@else.com"): OFFERED}),
    ({"run_id": 3, "index": 0}, {(3, "pick@position2.com"): dict(OFFERED, mode="recommend")}),
    ({"run_id": 3, "index": 0}, {(3, "pick@position2.com"): dict(OFFERED, summary={})}),
])
def test_a_pick_that_was_never_offered_is_refused(monkeypatch, pick, runs):
    started = _route(monkeypatch, runs)
    r = _client().post("/p2/strategic-agents/event-conference-intelligence/run",
                       json={"mode": "lookup", "pick": pick, "request_key": "k2"})
    assert r.status_code == 400 and "no longer on offer" in r.get_json()["error"]
    assert started == []


def test_an_ordinary_lookup_carries_no_pick(monkeypatch):
    started = _route(monkeypatch, {})
    _client().post("/p2/strategic-agents/event-conference-intelligence/run",
                   json={"mode": "lookup", "query": "Web Summit", "request_key": "k3"})
    assert started[0][2] == "Web Summit" and started[0][3]["pick"] is None


@pytest.mark.parametrize("run,needs", [
    (OFFERED, True), (dict(OFFERED, summary={}), False), (dict(OFFERED, status="complete"), False)])
def test_the_status_route_says_the_run_is_waiting_on_a_pick(monkeypatch, run, needs):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(ST, "get_run", lambda rid, email: dict(run, credits_spent=0))
    r = _client().get("/p2/strategic-agents/event-conference-intelligence/runs/3/status")
    assert r.get_json()["needs_pick"] is needs


# ── the drawer ──────────────────────────────────────────────────────────────

def _ambiguous(choices):
    return {"id": 3, "mode": "lookup", "status": "failed", "stage": "resolving", "query": "ADA Conference",
            "error": "“ADA Conference” matches more than one event. Pick the one you meant.",
            "events": [], "participants": [], "sources": [],
            "summary": {"choices": choices, "choice_note": "Several <b>ADAs</b>."}}


def test_the_drawer_shows_one_card_per_event_with_a_pick_button(page_script):
    body = _render(page_script, _ambiguous([DENTAL, DIABETES, COORD]))
    assert 'class="evi-pick"' in body and 'class="evi-err"' not in body
    cards = re.findall(r'<article class="evi-pick-card">(.*?)</article>', body, re.S)
    assert len(cards) == 3
    for i, (card, c) in enumerate(zip(cards, [DENTAL, DIABETES, COORD])):
        assert c["name"] in card and 'onclick="pickEvent(%d, this)"' % i in card
        assert 'href="%s"' % c["website"] in card
    # The date when the lookup found one, the edition's own label when not.
    assert "American Dental Association · Oct 22, 2026 · Indianapolis" in cards[0]
    assert "Jun 19, 2026" in cards[1]
    undated = _render(page_script, _ambiguous([dict(COORD, edition="Winter 2027"), DENTAL]))
    assert "National Association of ADA Coordinators · Winter 2027</div>" in undated
    # The model's reasoning is text, folded behind a summary.
    assert "Several &lt;b&gt;ADAs&lt;/b&gt;." in body and "Why the lookup could not choose" in body


def test_a_choice_is_printed_as_text_and_only_web_links_are_links(page_script):
    evil = {"name": "<img src=x onerror=alert(1)>", "website": "javascript:alert(1)",
            "about": "<script>x</script>"}
    body = _render(page_script, _ambiguous([evil, DENTAL]))
    assert "<img src=x" not in body and "<script>x" not in body
    assert "javascript:" not in body


def test_a_failed_run_with_no_choices_still_shows_its_error(page_script):
    run = _ambiguous([])
    body = _render(page_script, dict(run, error="Nothing by that name."))
    assert 'class="evi-err">Nothing by that name.' in body and "evi-pick" not in body


# ── the page wiring ─────────────────────────────────────────────────────────

def _src():
    return open(TEMPLATE).read()


def test_picking_posts_the_position_not_the_event():
    src = _src()
    fn = src[src.index("    function pickEvent("):src.index("    window.pickEvent = pickEvent;")]
    assert "pick: {run_id: run.id, index: i}" in fn
    assert "watch(res.j.run_id, 'lookup')" in fn and "addRunRow(res.j.run_id, 'lookup', c.name)" in fn
    assert "closeDrawer()" in fn
    assert fn.index("setMode('lookup')") < fn.index("showRunning(true)")


def test_the_row_says_pick_one_not_failed():
    src = _src()
    assert "{{ 'pick one' if r.needs_pick else r.status }}" in src
    assert "setRunRowStatus(runId, s.status, s.needs_pick)" in src
    store = open(os.path.join(ROOT, "tracker", "event_intel_store.py")).read()
    assert "ELSE false END AS needs_pick" in store
    css = open(os.path.join(ROOT, "static", "css", "event_intel_intake.css")).read()
    assert ".evi-layout .evi-run .evi-badge.pick {" in css
