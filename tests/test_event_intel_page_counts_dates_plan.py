"""The bar count, one date format per event, and the plan page's states and
labels. Report-page halves are executed in node through the real render()."""

import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_event_intel_event_view import page_script, _SHIM, _IIFE_CLOSE  # noqa: E402,F401
from test_event_intel_charts import _render, _render_parts, _recommend, _cand, _lookup, _part  # noqa: E402

from tracker import event_intel_planning as PL

BASE = "/p2/strategic-agents/event-conference-intelligence"


# ── the bar counts only what cleared it ─────────────────────────────────

def _with_committed():
    kept = [_cand("A", 92, "P1"), _cand("B", 85, "P1"), _cand("C", 74, "P2"),
            _cand("Committed Show", 43, "P3", committed=True)]
    run = _recommend(kept, counts={"P1": 2, "P2": 1, "kept": 4, "excluded": 0,
                                   "finished": 0, "committed_below_bar": 1},
                     selection={"kept": kept},
                     committed_note="1 event you are already committed to scored "
                                    "below 70 and was kept on the list anyway, "
                                    "marked: Committed Show at 43.")
    return run


def test_a_committed_event_below_the_bar_is_not_counted_as_clearing_it(page_script):
    parts = _render_parts(page_script, _with_committed())
    body = parts["body"]
    import re
    n = re.search(r'<b>(\d+)</b></div><div class="fl">Cleared the bar of 70', body).group(1)
    assert n == "3"
    assert "also carries 1 event you are already committed to" in body
    assert "Everything below is what these 4 are" not in body
    assert parts["sub"].startswith("3 events cleared the bar")


# ── one date format for one event ───────────────────────────────────────

def test_the_card_and_the_answer_write_a_range_the_same_way(page_script):
    body = _render(page_script, _recommend([_cand("A", 92, "P1")]))
    assert body.count("May 4 to 6, 2027") >= 2
    assert "May 4, 2027 to May 6, 2027" not in body


@pytest.mark.parametrize("when", ["May 30, 2027 to Jun 2, 2027", "2027-05-30 to 2027-06-02"])
def test_a_top_five_entry_without_a_card_uses_the_same_format(page_script, when):
    kept = [_cand("A%d" % i, 90 - i, "P1") for i in range(6)]
    five = [{"name": "Elsewhere", "total": 88, "tier": "P1", "where": "Berlin",
             "when": when, "case": "c"}] + [
        {"name": c["name"], "total": c["total"], "tier": "P1", "where": "Berlin",
         "when": "May 4, 2027 to May 6, 2027", "case": "c"} for c in kept[:4]]
    body = _render(page_script, _recommend(kept, top_five=five))
    top = body[body.index("Top five"):body.index("The ranked list")]
    assert "May 30 to Jun 2, 2027" in top
    assert "May 4 to 6, 2027" in top and "May 4, 2027 to" not in top


def test_a_lookup_event_line_uses_the_same_format(page_script):
    # The lookup heading is written as markup, so it is read as markup.
    at = page_script.index(_IIFE_CLOSE)
    probe = ("\ncurrent = __RUN;\nrender(__RUN);\n"
             "console.log(JSON.stringify({sub: document.getElementById('drawerSub').innerHTML}));\n")
    src = "var __PAGE_IDS = %s;\n%s\nvar __RUN = %s;\n%s" % (
        json.dumps(page_script.ids), _SHIM,
        json.dumps(_lookup([_part("Acme", "exhibitor")], [])),
        page_script.script[:at] + probe + page_script.script[at:])
    r = subprocess.run(["node"], input=src, capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stderr[-2000:]
    sub = json.loads(r.stdout.strip().splitlines()[-1])["sub"]
    assert "May 4 to 6, 2027" in sub
    assert "May 4, 2027 to May 6, 2027" not in sub


def test_a_top_five_entry_with_a_card_takes_the_card_s_dates(page_script):
    """The card is the event's own record; the stored line is text."""
    kept = [_cand("A%d" % i, 90 - i, "P1") for i in range(6)]
    five = [{"name": c["name"], "total": c["total"], "tier": "P1", "where": "Berlin",
             "when": "dates not announced", "case": "c"} for c in kept[:5]]
    body = _render(page_script, _recommend(kept, top_five=five))
    top = body[body.index("Top five"):body.index("The ranked list")]
    assert top.count("May 4 to 6, 2027") == 5 and "dates not announced" not in top


@pytest.mark.parametrize("s,e,text", [
    ("2027-05-04", "2027-05-06", "May 4 to 6, 2027"),
    ("2027-05-30", "2027-06-02", "May 30 to Jun 2, 2027"),
    ("2027-12-30", "2028-01-02", "Dec 30, 2027 to Jan 2, 2028"),
    ("2027-05-04", None, "May 4, 2027"),
    ("", None, ""),
])
def test_the_plan_page_writes_dates_as_the_report_does(s, e, text):
    assert PL.date_range_text(s, e) == text


# ── the plan link only when there is something to plan ──────────────────

def _plan_link_hidden(page_script, run):
    at = page_script.index(_IIFE_CLOSE)
    probe = ("\ncurrent = __RUN;\nrender(__RUN);\n"
             "console.log(JSON.stringify({hidden: document.getElementById('eventPlanLink').hidden}));\n")
    src = "var __PAGE_IDS = %s;\n%s\nvar __RUN = %s;\n%s" % (
        json.dumps(page_script.ids), _SHIM, json.dumps(run),
        page_script.script[:at] + probe + page_script.script[at:])
    r = subprocess.run(["node"], input=src, capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])["hidden"]


def test_the_plan_link_is_hidden_on_a_report_with_nothing_to_plan(page_script):
    assert "eventPlanLink" in page_script.ids
    assert _plan_link_hidden(page_script, _recommend([], no_candidates=True, note="n")) is True
    assert _plan_link_hidden(page_script, _recommend([_cand("A", 92, "P1")])) is False


# ── the plan page ───────────────────────────────────────────────────────

def _http():
    import app as appmod
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "planner@position2.com", "name": "T"}
    return c


def test_an_empty_report_opens_on_a_page_state_not_a_bare_400(monkeypatch):
    monkeypatch.setattr(PL.S, "get_run", lambda rid, email: {"id": rid, "mode": "recommend",
                                                            "status": "complete", "profile_id": 7})
    monkeypatch.setattr(PL.S, "list_profiles", lambda email: [{"id": 7, "client_name": "Acme"}])
    monkeypatch.setattr(PL.S, "get_profile", lambda pid, email: {"id": 7, "client_name": "Acme"})
    monkeypatch.setattr(PL.S, "get_candidates", lambda rid: [])
    r = _http().get(BASE + "/runs/81/plan")
    html = r.get_data(as_text=True)
    assert r.status_code == 200 and "text/html" in r.headers["Content-Type"]
    assert "Nothing to plan" in html and "no events to plan" in html
    assert 'id="planForm"' not in html
    with pytest.raises(ValueError):
        PL.save(81, "planner@position2.com", {"profile_id": 7, "version": 0, "event_identity": "x",
                                              "action": "meetings", "currency": "USD"})


def test_an_unknown_event_is_a_page_with_a_way_back(monkeypatch):
    def refuse(*a, **k):
        raise ValueError("That event is not in this report. Choose one from the list.")
    monkeypatch.setattr(PL, "context", refuse)
    r = _http().get(BASE + "/runs/81/plan", query_string={"event_identity": "nope"})
    html = r.get_data(as_text=True)
    assert r.status_code == 400 and "<html" in html
    assert "That event is not in this report" in html and "Back to the report list" in html


def test_the_plan_page_labels_its_stored_tokens(monkeypatch):
    event = {"name": "Forum", "event_identity": "k", "starts_on": "2027-05-04",
             "ends_on": "2027-05-06", "city": "Boston",
             "when": PL.date_range_text("2027-05-04", "2027-05-06")}
    view = dict(run_id=1, profile={"id": 7, "client_name": "Acme"},
                profiles=[{"id": 7, "client_name": "Acme"}], events=[event], event=event,
                plan=None, participants=[],
                overlay={"matches": [{"company": "Acme", "domain": "acme.com", "role": "exhibitor",
                                      "source_url": "https://f.example/x",
                                      "timing": "edition_not_established",
                                      "support": "literal_support_only"}],
                         "suggested_action": None, "not_observed": [], "limitation": "L",
                         "next_check": "N"},
                fit={"limitation": "L", "topic_matches": [{"topics": ["abm"], "timing": "announced",
                                                           "text": "t", "source_url": "u",
                                                           "observed_at": "o"}],
                     "unmatched_topics": [], "people": []},
                access_links=[{"url": "https://f.example/r", "label": "Register",
                               "kind": "registration", "observed_at": "o", "source_url": "s"}],
                access_checks=[{"kind": "registration", "status": "blocked", "note": "n",
                                "url": "u", "checked_at": "c", "claims": [], "scope": {}},
                               {"kind": "a_future_kind", "status": "a_future_status", "note": "n",
                                "url": "u", "checked_at": "c", "claims": [], "scope": {}}],
                assessment={"status": "blocked_for_review", "checks": ["c"]})
    monkeypatch.setattr(PL, "context", lambda *a, **k: view)
    html = _http().get(BASE + "/runs/1/plan").get_data(as_text=True)
    for raw in ("literal support only", "literal_support_only", "edition not established",
                "Blocked for review", "a_future_kind", "a_future_status", "· registration<",
                "2027-05-04"):
        assert raw not in html, raw
    for shown in ("Registration page · Could not be read", "Organizer page · Checked",
                  "Named on the published page", "Edition not established",
                  "Announced for this edition", "Blocked until reviewed", "Exhibitor",
                  "May 4 to 6, 2027"):
        assert shown in html, shown


# ── the cut list says which cut it was (relevance cut, 2026-10-01) ──────

def test_the_cut_list_separates_a_low_score_from_the_wrong_audience(page_script):
    excluded = [{"name": "Low Score Expo", "total": 41, "relevance": 30,
                 "excluded_reason": "Scored 41, under the 50 needed to be shown as an option."},
                {"name": "Wrong Crowd Summit", "total": 77, "relevance": 18,
                 "excluded_reason": "Scored 77, but its audience is not mainly your buyers."}]
    body = _render(page_script, _recommend([_cand("A", 92, "P1")], excluded=excluded,
                                           counts={"P1": 1, "kept": 1, "excluded": 2, "finished": 0}))
    cut = body[body.index("Scored and cut"):]
    low, aud = cut.index("Low Score Expo"), cut.index("Wrong Crowd Summit")
    assert cut.index("too low to offer") < low < cut.index("Cut for audience") < aud
    assert "Below 70" not in cut
    assert 'title="Scored 77, but its audience is not mainly your buyers."' in cut
