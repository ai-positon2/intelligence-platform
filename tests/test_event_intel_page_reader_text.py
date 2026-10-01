"""Developer detail never reaches a client, at the writer or at the page.

The writers fixed here stored "max_tokens: Ran out of output budget ... Raise
max_tokens or lower max_uses", an exception's text ("HTTPSConnectionPool(...)")
and a model's first 400 characters as the reason a page was unread. Stored
runs keep what they were written with, so the page cleans it on the way out
too, and the page half is executed in node through the real render().
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_event_intel_event_view import page_script  # noqa: E402,F401
from test_event_intel_charts import (_render, _recommend, _cand, _lookup,  # noqa: E402
                                     _part, _workroom)

from tracker import claude_websearch, event_intel_harvest as H
from tracker import event_intel_workroom as WR, event_intel_pipeline as P

PLUMBING = ("max_tokens", "stop_reason", "max_uses", "HTTPSConnectionPool",
            "org_domain", "null", "access_review", "—", "Unexpected failure",
            "Traceback")

MAXTOK = ("max_tokens: Ran out of output budget before finishing "
          "(stop_reason=max_tokens). Raise max_tokens or lower max_uses.")


def _clean(html):
    for p in PLUMBING:
        assert p not in html, p


# ── writers ──────────────────────────────────────────────────────────────

def test_a_failed_qualification_batch_is_stored_as_a_sentence(monkeypatch):
    monkeypatch.setattr(claude_websearch, "ask", lambda *a, **k: {
        "text": "", "error": {"kind": claude_websearch.ERR_MAX_TOKENS, "detail": MAXTOK}})
    out = WR.draft_batch([{"org_name": "Acme", "role": "exhibitor"}],
                         {"client_name": "N"}, {"name": "E"}, WR.EVENT_CLASSES[0], {})
    assert out["error"].startswith("One batch of 1 company could not be qualified: ")
    assert claude_websearch.READER_REASON[claude_websearch.ERR_MAX_TOKENS] in out["error"]
    _clean(out["error"])


def test_a_crashed_qualification_batch_is_stored_as_a_sentence(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("HTTPSConnectionPool(host='x', port=443)")
    monkeypatch.setattr(WR, "draft_batch", boom)
    out = WR.draft_all([{"org_name": "Acme", "role": "exhibitor"}],
                       {"client_name": "N"}, {"name": "E"}, WR.EVENT_CLASSES[0], {})
    assert out["errors"] and all("could not be qualified" in e for e in out["errors"])
    _clean(" ".join(out["errors"]))


def test_a_fetch_exception_is_not_the_page_note(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("HTTPSConnectionPool(host='e.example', port=443): "
                              "Read timed out. (read timeout=20)")
    monkeypatch.setattr(H, "public_get", boom)
    out = H.fetch_page("https://e.example/sponsors")
    assert out["note"] == "The page could not be reached."


def test_an_unparsable_extraction_does_not_store_the_raw_reply(monkeypatch):
    monkeypatch.setattr(claude_websearch, "ask", lambda *a, **k: {
        "text": "Sure! Here are the rows you asked for, in a shape...", "error": None})
    out = H._extract_chunk("Acme Corp, exhibitor, booth 5", "https://e.example/x",
                           "exhibitors", "E")
    assert "Sure!" not in out["error"]["detail"]


def test_a_page_whose_extraction_broke_says_so_in_reader_words(monkeypatch):
    monkeypatch.setattr(H, "fetch_page", lambda url: {
        "url": url, "final_url": url, "status": "ok", "http_status": 200,
        "text": "Exhibitors " * 80, "note": "", "truncated": False, "spa": None})
    monkeypatch.setattr("tracker.event_intel_cache.extract", lambda *a, **k: {
        "rows": [], "note": "", "error": {"kind": claude_websearch.ERR_MAX_TOKENS,
                                          "detail": MAXTOK}})
    got = H.harvest_page({"url": "https://e.example/exhibitors", "kind": "exhibitors"}, "E", "e.example")
    note = got["source"]["note"]
    assert note.startswith("The page was fetched but its list could not be read: ")
    _clean(note)


def test_the_extraction_prompt_asks_for_a_reader_note():
    assert "never a field name" in H._SYSTEM


def test_a_crashed_harvest_is_stored_as_a_sentence(monkeypatch):
    saved = []
    monkeypatch.setattr(P.store, "save_source", lambda *a, **k: saved.append((a, k)))
    monkeypatch.setattr(P, "durable_stage", lambda name, fn, *a, **k: (_ for _ in ()).throw(
        RuntimeError("KeyError: 'rows'")))
    P._harvest_event(1, 2, {"name": "E", "website": "https://e.example"},
                     [{"url": "https://e.example/x", "kind": "exhibitors"}])
    note = saved[0][1]["note"]
    assert "stopped unexpectedly" in note and "KeyError" not in note


# ── the page, over runs stored before the writers were fixed ─────────────

def test_the_source_ledger_speaks_the_reader_s_language(page_script):
    srcs = [
        {"url": "https://e.example/speakers", "kind": "speakers", "status": "ok",
         "rows_found": 0, "http_status": 200,
         "note": "This is the Speakers landing page, but it contains no individually "
                 "named speakers—only marketing copy (no tiers or links to their "
                 "sites, so org_domain is null for all)."},
        {"url": "https://e.example/exhibitors", "kind": "exhibitors", "status": "error",
         "note": "The page was fetched but could not be read: " + MAXTOK[12:]},
        {"url": "https://e.example/sponsors", "kind": "sponsors", "status": "error",
         "note": "Request failed: HTTPSConnectionPool(host='e.example', port=443): "
                 "Read timed out. (read timeout=20)"},
        {"url": "https://e.example/register", "kind": "access_review", "status": "blocked",
         "note": "Destination redirected outside the organizer host."},
        {"url": "https://e.example/new", "kind": "some_future_kind", "status": "a_new_status",
         "note": ""},
    ]
    html = _render(page_script, _lookup([_part("Acme", "exhibitor")], srcs))
    ledger = html[html.index("evi-sources"):]
    _clean(ledger)
    assert ("contains no individually named speakers, only marketing copy." in ledger)
    assert "Registration or access page" in ledger and "Speaker list" in ledger
    assert "some_future_kind" not in ledger and "a_new_status" not in ledger
    assert ledger.count("This page could not be read.") == 2
    assert ">Read<" in ledger and ">Could not be read<" in ledger and ">ok<" not in ledger


def test_qualification_errors_are_cleaned_and_said_once(page_script):
    html = _render(page_script, _workroom([], qualify_errors=[MAXTOK, MAXTOK]))
    _clean(html)
    assert html.count("One batch of companies could not be qualified.") == 1


def test_a_note_detail_that_is_all_diagnostics_becomes_a_sentence(page_script):
    html = _render(page_script, _recommend([_cand("Winner", 90, "P1")], notes=[
        {"level": "gap", "head": "Scoring reported 1 error", "detail": MAXTOK},
        {"level": "note", "head": "Worth it", "detail": "Plain—dash (see <cite index=\"1\">x</cite>)."}]))
    notes = html[html.index("What was not measured"):]
    _clean(notes)
    assert "One of the research calls behind this stopped before it finished." in notes
    assert "Plain, dash" in notes and "<cite" not in notes


def test_an_unexpected_failure_reason_is_never_printed_raw(page_script):
    st = {"side_event": {"proposed": 3, "found": 2, "kept": 2, "rejected": [],
                         "unverified": [{"name": "U", "reason": "Unexpected failure: "
                                         "KeyError('event')"}],
                         "label": "Side event", "status": "partial"}}
    html = _render(page_script, _recommend([_cand("Winner", 90, "P1")], statuses=st,
        unscored=[{"name": "V", "note": "Unexpected failure: ValueError('x')"}]))
    _clean(html)
    assert "The check stopped before it reached a conclusion." in html


# ── the one billed button says what happened ─────────────────────────────

import json  # noqa: E402
import subprocess  # noqa: E402
from test_event_intel_event_view import _SHIM, _IIFE_CLOSE  # noqa: E402


def _press_resolve(page_script, run, reply, status=200):
    """Render a roster, press Match companies, answer the POST with `reply`,
    answer the reload with the run, and read back what was drawn."""
    at = page_script.index(_IIFE_CLOSE)
    probe = ("\ncurrent = __RUN;\nrender(__RUN);\n"
             "global.fetch = function(url, opts){\n"
             "  if (opts && opts.method === 'POST') return Promise.resolve({ok: %s,"
             " json: function(){ return Promise.resolve(%s); }});\n"
             "  return Promise.resolve({ok: true, json: function(){ return Promise.resolve(__RUN); }});\n"
             "};\n"
             "process.on('unhandledRejection', function(e){ console.error(e); process.exit(3); });\n"
             # The drawer chrome openRun touches is not in this DOM; the
             # reload it does is a render() of the run it fetches.
             "openRun = function(){ render(__RUN); };\n"
             "window.eviResolve(__RUN.id);\n"
             "(function wait(n){ if (n) return setImmediate(function(){ wait(n - 1); });\n"
             "  console.log(JSON.stringify({body: document.getElementById('drawerBody').innerHTML}));\n"
             "})(8);\n"
             % ("true" if status < 400 else "false", json.dumps(reply)))
    src = "var __PAGE_IDS = %s;\n%s\nvar __RUN = %s;\n%s" % (
        json.dumps(page_script.ids), _SHIM, json.dumps(run),
        page_script.script[:at] + probe + page_script.script[at:])
    r = subprocess.run(["node"], input=src, capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stderr[-2500:]
    return json.loads(r.stdout.strip().splitlines()[-1])["body"]


def _roster():
    return _lookup([_part("Acme", "exhibitor")], [],
                   cost_estimate={"domains": 1, "note": "One credit per company."})


def test_a_resolve_error_is_shown_rather_than_swallowed(page_script):
    msg = ("Company matching is not switched on for this workspace, so no "
           "company was looked up and no credits were spent.")
    body = _press_resolve(page_script, _roster(), {"resolved": 0, "credits": 0, "error": msg})
    assert msg in body


def test_a_resolve_success_says_what_it_matched_and_cost(page_script):
    body = _press_resolve(page_script, _roster(),
                          {"resolved": 3, "unmatched": 1, "credits": 3, "people": 5, "error": None})
    assert "3 companies matched, 1 with no Apollo record, 5 contacts found, costing 3 credits." in body


def test_a_refused_resolve_request_is_said_in_reader_words(page_script):
    body = _press_resolve(page_script, _roster(), {"error": "internal"}, status=500)
    assert "could not be started" in body and "internal" not in body


def test_the_route_s_resolve_error_is_a_reader_sentence(monkeypatch):
    from tracker import event_intel_enrich as E
    monkeypatch.setattr(P.store, "get_run", lambda rid, email: {"id": rid})
    monkeypatch.setattr(P.store, "get_participants", lambda rid: [
        {"id": 1, "org_domain": "acme.com"}, {"id": 2, "org_domain": "beta.com"}])
    monkeypatch.setattr(P.store, "update_run", lambda *a, **k: None)
    monkeypatch.setattr(P.store, "update_participant_resolution", lambda *a, **k: None)
    monkeypatch.setattr(E, "api_key", lambda: None)
    out = P.resolve_run_companies(1, "me@p2.example")
    assert out["error"].startswith("Company matching is not switched on")
    assert "APOLLO_API_KEY" not in out["error"]
    monkeypatch.setattr(E, "resolve_companies", lambda d, **k: {
        "by_domain": {"acme.com": {"name": "Acme"}}, "credits": 1, "unmatched": [],
        "unattempted": ["beta.com"], "error": "Apollo company lookup failed: HTTP 503"})
    monkeypatch.setattr(E, "find_people", lambda d, titles=None: {"by_domain": {}, "total": 0, "error": None})
    monkeypatch.setattr(P.store, "add_credits", lambda *a: None)
    out = P.resolve_run_companies(1, "me@p2.example")
    assert "1 company was not looked up" in out["error"] and "503" not in out["error"]
