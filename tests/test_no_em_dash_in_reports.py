"""House style has no em dashes in anything the model writes for a client to
read. That rule had one enforcement point (event_intel_intake.py's private
`_FIELD_DASH`, for the intake's own draft fields) before this file existed.

A live rendering of a real, successfully completed run surfaced the dash in
five more fields across three more modules that had never been checked for
it: a candidate's own NAME (an em dash sat inside a real, model-chosen event
name, and that one field then propagated the dash into the audit's cut list
and the run's own assumptions text, three places on screen for one uncleaned
field), the audit's `why`, the scorer's `description` and `relevance_note`,
and a resolved event's `edition`. Discovery also had a SECOND private copy of
the same fix (`_reader_note`'s `_NOTE_DASH`) for one field, so by the time
this was found there were three independent, incomplete copies of the same
one-line fix.

All of it now goes through claude_websearch.strip_em_dash, the one place the
substitution lives. These tests exist so the next module that calls
claude_websearch.ask does not have to rediscover this, and so a future edit
that stops calling it (or moves the call after the length cap, where a
one-character dash can turn into a two-character ", " and overrun it) fails
loudly instead of shipping quietly.

Every literal em/en dash in this file is written as \\u2014 / \\u2013, never
as the character itself, for the same reason the fields under test do not
carry one either.
"""

import json

from tracker import claude_websearch as CW
from tracker import event_intel_audit as A
from tracker import event_intel_discover as D
from tracker import event_intel_harvest as H
from tracker import event_intel_intake as I
from tracker import event_intel_recover as RC
from tracker import event_intel_resolve as RS
from tracker import event_intel_rubric as R
from tracker import event_intel_scorer as SC
from tracker import event_intel_workroom as WR

EM = "\u2014"
EN = "\u2013"

PROFILE = {"client_name": "Northwind", "classification": R.CLASS_B2B_TO_MARKETING,
           "buyer_roles": "VP Marketing", "verticals": "fintech",
           "window_months": 12}


# ── the shared function itself ─────────────────────────────────────────────

def test_an_em_dash_becomes_a_comma():
    assert CW.strip_em_dash("scale" + EM + "and reach") == "scale, and reach"


def test_an_en_dash_becomes_a_comma_too():
    assert CW.strip_em_dash("2026" + EN + "2027") == "2026, 2027"


def test_surrounding_whitespace_is_absorbed_not_doubled():
    # No space before the dash, one after: the substitution must not leave
    # "word,  , word" or "word ,word".
    assert CW.strip_em_dash("word" + EM + " word") == "word, word"
    assert CW.strip_em_dash("word " + EM + "word") == "word, word"


def test_multiple_dashes_in_one_string_all_go():
    s = "A" + EM + "B" + EN + "C"
    out = CW.strip_em_dash(s)
    assert EM not in out and EN not in out
    assert out == "A, B, C"


def test_a_string_with_no_dash_is_unchanged():
    assert CW.strip_em_dash("nothing to change here.") == "nothing to change here."


def test_none_and_empty_input_do_not_raise():
    assert CW.strip_em_dash(None) == ""
    assert CW.strip_em_dash("") == ""


def test_a_direct_quote_is_not_this_functions_job():
    """strip_citation_markup already turned a <cite> pair into a real
    quotation by the time any caller sees the text; strip_em_dash is applied
    afterward, per-field, by callers cleaning their OWN prose fields, not
    blanket over a whole raw reply. This just confirms the function itself
    does not special-case quotation marks -- the callers own that boundary,
    documented on strip_em_dash itself."""
    assert CW.strip_em_dash("she said “we grew" + EM + "a lot”") == \
        "she said “we grew, a lot”"


# ── event_intel_discover: a candidate's own name and why ───────────────────

def test_a_proposed_candidates_name_is_cleaned():
    out = D._clean_proposal({"name": "SaaStr AI Annual " + EM + " Parties",
                             "why": "found" + EM + "linked"})
    assert EM not in out["name"]
    assert out["name"] == "SaaStr AI Annual, Parties"
    assert EM not in out["why"]


def test_a_proposal_with_only_a_dash_and_whitespace_is_still_rejected():
    """The cleaning must happen before the emptiness check, not change which
    proposals are accepted."""
    out = D._clean_proposal({"name": "   " + EM + "   "})
    assert out is None


def test_a_confirmed_events_free_text_fields_are_cleaned():
    raw = {"name": "Fin.Tech Marketing Conference", "confirmed": True,
           "edition": "APAC Fin.Tech " + EM + " Hong Kong 2026",
           "organizer": "Org" + EM + "Group",
           "audience_note": "CMOs" + EM + "and VPs",
           "category_fit": "found" + EM + "here",
           "matchmaking_evidence": "yes" + EM + "confirmed"}
    event = D._clean_event(raw, R.CAT_REGIONAL_FLAGSHIP)
    assert event is not None
    for field in ("edition", "organizer", "audience_note", "category_fit",
                 "matchmaking_evidence"):
        assert EM not in (event[field] or ""), (field, event[field])
    assert event["edition"] == "APAC Fin.Tech, Hong Kong 2026"


def test_a_confirmed_events_own_name_is_cleaned():
    raw = {"name": "Big Show " + EM + " Regional Edition", "confirmed": True}
    event = D._clean_event(raw, R.CAT_REGIONAL_FLAGSHIP)
    assert EM not in event["name"]


def test_a_category_note_still_has_no_dash_after_the_shared_refactor():
    """_reader_note used to carry its own private _NOTE_DASH regex. Same
    substitution, different source, and this is the regression check that
    the refactor did not silently change behaviour."""
    out = D._reader_note("Searched" + EM + "found nothing published.")
    assert EM not in out
    assert "Searched, found nothing published." in out


def test_a_rejection_reason_is_cleaned(monkeypatch):
    def fake_ask(system, user, **kw):
        return {"text": json.dumps({"confirmed": False,
                                    "reject_reason": "too broad" + EM + "wrong audience"}),
                "error": None, "search_count": 2}
    monkeypatch.setattr(D.claude_websearch, "ask", fake_ask)
    out = D.confirm_event({"name": "X", "website": None},
                          R.CAT_REGIONAL_FLAGSHIP, PROFILE)
    assert out["kind"] == D.CONFIRM_REJECTED
    assert EM not in out["reason"]
    assert out["reason"] == "too broad, wrong audience"


# ── event_intel_scorer ─────────────────────────────────────────────────────

def test_a_scored_events_description_and_notes_are_cleaned():
    raw = {"name": "X", "relevance": 30, "dm_access": 28, "engagement": 14,
           "relevance_note": "dense" + EM + "on target",
           "dm_access_note": "good" + EN + "reach",
           "engagement_note": "buying" + EM + "mindset",
           "description": "1,000 attendees" + EM + "demand-gen managers.",
           "client_line": "needs this" + EM + "now."}
    out = SC._clean(raw)
    for field in ("relevance_note", "dm_access_note", "engagement_note",
                 "description", "client_line"):
        assert EM not in out[field] and EN not in out[field], (field, out[field])
    assert out["description"] == "1,000 attendees, demand-gen managers."


# ── event_intel_audit ───────────────────────────────────────────────────────

def test_an_audit_verdicts_alternative_and_why_are_cleaned():
    cand = {"name": "Adobe Summit"}
    rec = A._record(cand, {"verdict": "cut",
                           "alternative": "MAICON " + EM + " AI conference",
                           "why": "diluted" + EM + "narrow beats broad",
                           "alternative_note": "unverified" + EM + "recent"})
    assert EM not in rec["alternative"]
    assert EM not in rec["why"]
    assert EM not in (rec["alternative_note"] or "")
    assert rec["alternative"] == "MAICON, AI conference"


def test_the_kept_no_alternative_downgrade_message_still_has_no_dash():
    """The static prefix this module writes itself when downgrading a bare
    'kept' claim must also stay clean when concatenated with a cleaned why."""
    cand = {"name": "X"}
    rec = A._record(cand, {"verdict": "kept", "alternative": None,
                           "why": "everyone attends" + EM + "scale wins"})
    assert rec["verdict"] == A.VERDICT_CUT
    assert EM not in rec["why"]


# ── event_intel_resolve ─────────────────────────────────────────────────────

def _ask_resolve(monkeypatch, payload, search_count=2):
    monkeypatch.setattr(RS.claude_websearch, "ask",
                        lambda system, user, **kw: {
                            "text": json.dumps(payload), "error": None,
                            "search_count": search_count})


def test_a_resolved_events_free_text_fields_are_cleaned(monkeypatch):
    _ask_resolve(monkeypatch, {
        "confidence": "high", "name": "APAC Fin.Tech " + EM + " Hong Kong",
        "edition": "2026" + EM + "APAC", "organizer": "Org" + EM + "Group",
        "location": "Hong Kong" + EM + "Central", "venue": "Hall " + EM + "A",
        "stated_size": "500" + EM + "600", "audience_note": "CMOs" + EM + "VPs",
        "reasoning": "found" + EM + "confirmed", "website": "https://x.example"})
    out = RS.resolve_event("APAC Fin.Tech")
    assert out["ok"], out["reasoning"]
    ev = out["event"]
    for field in ("name", "edition", "organizer", "location", "venue",
                 "stated_size", "audience_note"):
        assert EM not in (ev[field] or ""), (field, ev[field])
    assert EM not in out["reasoning"]
    assert ev["name"] == "APAC Fin.Tech, Hong Kong"


def test_a_failed_resolve_reasoning_is_cleaned_too(monkeypatch):
    _ask_resolve(monkeypatch, {"confidence": "low", "name": "",
                              "reasoning": "no strong match" + EM + "try again"})
    out = RS.resolve_event("Ambiguous Event")
    assert not out["ok"]
    assert EM not in out["reasoning"]


# ── event_intel_workroom ────────────────────────────────────────────────────

def test_a_drafted_openers_three_fields_are_cleaned():
    raw = {"org": "Acme", "fit": 80,
           "fit_note": "strong match" + EM + "ICP",
           "angle": "recently expanded" + EM + "worth a note",
           "opener": "Saw Acme was exhibiting" + EM + "curious about your stack."}
    out = WR._clean_draft(raw)
    for field in ("fit_note", "angle", "opener"):
        assert EM not in out[field], (field, out[field])
    assert out["opener"] == ("Saw Acme was exhibiting, curious about your "
                             "stack.")


# ── event_intel_recover / event_intel_harvest ───────────────────────────────

def test_a_recovered_rosters_note_is_cleaned(monkeypatch):
    monkeypatch.setattr(RC.claude_websearch, "ask",
                        lambda system, user, **kw: {
                            "text": json.dumps({
                                "rows": [], "note": "found nothing" + EM + "tried twice"}),
                            "error": None, "search_count": 2})
    out = RC.recover_page("https://event.example/sponsors", "sponsors", "Event")
    assert EM not in out["source"]["note"]


def test_an_extracted_page_note_is_cleaned(monkeypatch):
    monkeypatch.setattr(H.claude_websearch, "ask",
                        lambda system, user, **kw: {
                            "text": json.dumps({
                                "rows": [], "note": "no roster found" + EM + "page was thin"}),
                            "error": None, "search_count": 0})
    out = H.extract_participants("some page text", "https://event.example/sponsors",
                                 "sponsors", "Event")
    assert EM not in out["note"]


# ── event_intel_intake: refactor regression check ───────────────────────────

def test_intake_still_cleans_its_own_fields_after_the_shared_refactor():
    """The live example that first surfaced this bug in a single field
    (geo_scope). Same input, same expected output, after _FIELD_DASH became
    a call to the shared function."""
    raw = ("Global " + EM + " client logos and case studies reference "
          "international brands though no specific office locations are "
          "listed on these pages.")
    out = I._field(raw)
    assert out is not None
    assert EM not in out
