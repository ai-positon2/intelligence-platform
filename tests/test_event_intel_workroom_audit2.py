"""Workroom audit, 2026-10-04: a third-party review, and live run 32.

Each test is one way a draft or a count said something the roster, the
notes or the calendar do not support.
"""

import os
import sys

import pytest

from tracker import event_intel_pipeline as P
from tracker import event_intel_store as S
from tracker import event_intel_workroom as W

sys.path.insert(0, os.path.dirname(__file__))
from test_event_intel_workroom_audit import _page, _wire, row  # noqa: E402
from test_event_intel_event_view import page_script  # noqa: E402,F401  (fixture)


# ── booth notes ──

def test_a_note_written_against_a_company_exactly_finds_it_among_longer_names():
    rows = [{"org_name": "Google"}, {"org_name": "Google Cloud"},
            {"org_name": "Acme"}, {"org_name": "Acme Analytics"}]
    notes = W.index_booth_notes("Google Cloud: wants pricing\nAcme: asked for a demo")
    got = W.match_booth_notes(notes, rows)
    assert got == {"by_org": {"google cloud": "wants pricing", "acme": "asked for a demo"},
                   "unmatched": []}


@pytest.mark.parametrize("line", ["Splunk (a Cisco company): wants a demo of SOAR",
                                  "Contoso, a Microsoft partner: asked for pricing"])
def test_a_note_about_another_company_is_not_given_to_one_it_names(line):
    rows = [{"org_name": "Cisco"}, {"org_name": "Microsoft"}]
    got = W.match_booth_notes(W.index_booth_notes(line), rows)
    assert got["by_org"] == {} and len(got["unmatched"]) == 1


def test_a_note_naming_the_company_in_a_sentence_still_finds_it():
    got = W.match_booth_notes(W.index_booth_notes("Spoke to Dana at Acme: wants pricing"),
                              [{"org_name": "Acme Inc"}, {"org_name": "Beta"}])
    assert got["by_org"] == {"acme": "wants pricing"}


# ── what a draft may claim ──

@pytest.mark.parametrize("opener", [
    "Loved your keynote on AI governance at RSA.",
    "I caught your panel on zero trust and wanted to follow up.",
    "Your session on data residency was the highlight of the week for our team.",
    "Since your team was at the show last week, curious what you took away.",
    "Enjoyed your talk on agentic SOC workflows.",
    "Saw HSBC speaking at Money20/20 USA this year and wanted to reach out.",
])
def test_a_talk_described_as_heard_or_attendance_assumed_is_replaced(opener):
    r = W.enforce([row("Acme", person="Dana Lee", role="speaker", fit=80, opener=opener)],
                  event_class=W.CLASS_ATTENDED, notes={}, event_name="RSA")["rows"][0]
    assert r["draft_status"] == W.DRAFT_NO_EVIDENCE and r["opener"] != opener


@pytest.mark.parametrize("opener", [
    "I saw Acme listed as a sponsor at RSA, curious how payments are built into your products.",
    "Your team's work on fraud models is why I am writing.",
])
def test_an_opener_that_states_only_the_roster_is_kept(opener):
    r = W.enforce([row("Acme", person="Dana Lee", fit=80, opener=opener)],
                  event_class=W.CLASS_ATTENDED, notes={}, event_name="RSA")["rows"][0]
    assert r["opener"] == opener


def test_a_harmless_stuck_with_me_is_not_displacement_on_a_competitors_event():
    assert W.is_aggressive("One question from the analyst panel stuck with me.") == []
    assert W.is_aggressive("Are you stuck with a vendor that does not scale?")


# ── an event that has not happened yet ──

@pytest.mark.parametrize("cls", W.EVENT_CLASSES)
def test_a_replacement_opener_for_an_upcoming_event_says_nothing_happened(cls):
    o = W.fallback_opener(org="Acme", event_name="AWS re:Invent 2026", role_label="Exhibitor",
                          event_class=cls, client_name="Northwind", upcoming=True)
    for past in ("had a stand", "was in the audience", "were in the audience",
                 "would have asked", "list at AWS", "that week"):
        assert past not in o, (cls, o)
    assert "I saw Acme on the exhibitor list for AWS re:Invent 2026" in o


def test_enforce_writes_upcoming_replacements_when_told_the_event_is_ahead():
    r = W.enforce([row("Acme", fit=80, opener="Great chatting at the booth!")],
                  event_class=W.CLASS_EXHIBITED, notes={}, event_name="RSA",
                  client_name="Northwind", upcoming=True)["rows"][0]
    assert "will have a stand there too" in r["opener"]


def test_the_drafter_is_not_told_a_future_end_date_has_passed():
    assert "ended: 2099-12-04" not in W.event_brief({"name": "E", "ends_on": "2099-12-04"})
    assert "ends (not yet held): 2099-12-04" in W.event_brief({"name": "E", "ends_on": "2099-12-04"})
    assert "ended: 2020-01-02" in W.event_brief({"name": "E", "ends_on": "2020-01-02"})


def test_the_pipeline_tells_enforce_when_the_event_is_ahead(monkeypatch):
    fake, _ = _wire(monkeypatch, [{"org_name": "Acme", "person_name": "Dana"}], ends_on="2099-01-01")
    seen = {}
    real = W.enforce

    def spy(rows, **kw):
        seen.update(kw)
        return real(rows, **kw)
    monkeypatch.setattr(P.event_intel_workroom, "enforce", spy)
    P._run_workroom(1, "me@x", 9, {"client_name": "N"}, W.CLASS_EXHIBITED, None)
    assert seen["upcoming"] is True


# ── which rows are companies to contact ──

def test_the_client_and_people_without_a_company_are_left_out_and_said(monkeypatch):
    """Live run 32: Stripe on Money20/20's sponsor list got an opener to
    itself, the model's refusal stored as the text."""
    fake, seen = _wire(monkeypatch, [
        {"org_name": "Stripe", "org_domain": "stripe.com"},
        {"org_name": "Stripe, Inc."},
        {"org_name": "Priya Raman", "person_name": "Priya Raman", "role": "speaker"},
        {"org_name": "Adyen"}])
    P._run_workroom(1, "me@x", 9, {"client_name": "Stripe", "website": "https://stripe.com"},
                    W.CLASS_EXHIBITED, None)
    assert [r["org_name"] for r in seen["rows"]] == ["Adyen"]
    s = fake.runs[1]["summary"]
    assert (s["own_rows"], s["people_only"], s["merged_rows"]) == (2, 1, 0)


def test_one_name_on_two_websites_is_two_companies_and_two_people_are_two_rows(monkeypatch):
    fake, seen = _wire(monkeypatch, [
        {"org_name": "Mercury", "org_domain": "mercury.com"},
        {"org_name": "Mercury", "org_domain": "mrcy.com"},
        {"org_name": "Acme", "person_name": "Jo"},
        {"org_name": "Acme", "person_name": "Bo"},
        {"org_name": "Acme"}])
    P._run_workroom(1, "me@x", 9, {"client_name": "N"}, W.CLASS_ATTENDED, None)
    got = sorted((r["org_name"], r.get("org_domain"), r.get("person_name")) for r in seen["rows"])
    assert got == [("Acme", None, "Bo"), ("Acme", None, "Jo"),
                   ("Mercury", "mercury.com", None), ("Mercury", "mrcy.com", None)]
    assert fake.runs[1]["summary"]["merged_rows"] == 1


def test_a_qualification_that_never_ran_is_not_called_an_answer(monkeypatch, page_script):
    fake, _ = _wire(monkeypatch, [{"org_name": "Acme"}, {"org_name": "Beta"}])
    monkeypatch.setattr(P.event_intel_workroom, "draft_all", lambda rows, *a: {
        "rows": [dict(r, unqualified=True, fit=None) for r in rows],
        "errors": ["One batch of companies could not be qualified."], "missing": 2, "batches": 1})
    P._run_workroom(1, "me@x", 9, {"client_name": "N"}, W.CLASS_ATTENDED, None)
    assert fake.runs[1]["summary"]["qualify_failed"] is True
    body = _page(page_script, {"qualify_failed": True, "qualify_errors": ["x"],
                               "counts": {"roster": 2, "kept": 0, "cut": 0, "unqualified": 2}},
                 [row("Acme", fit=None, unqualified=True)])
    assert "nothing here is a verdict" in body and "That is the answer" not in body


def test_rows_set_aside_are_said_on_the_page(page_script):
    body = _page(page_script, {"own_rows": 1, "people_only": 2, "merged_rows": 3,
                               "counts": {"roster": 6, "kept": 0, "cut": 0, "unqualified": 0}}, [])
    assert "1 roster row is your own company" in body
    assert "2 people are listed with no company" in body
    assert "3 rows were the same company listed again and merged" in body


# ── stored and shown alike ──

@pytest.mark.parametrize("status", [W.DRAFT_NO_BOOTH, W.DRAFT_LINK])
def test_every_rewrite_status_is_stored_as_itself(status):
    n = S.normalise_outreach({"org_name": "Acme", "draft_status": status, "draft_reason": "why"},
                             1, 2, "E", "attended")
    assert n["draft_status"] == status


def test_the_page_checks_drafts_with_the_runs_own_client(monkeypatch):
    stored = [row("Acme", fit=80, opener="Dana, our benchmark is at northwind.io/report.",
                  draft_status="review_required", event_class="exhibited")]
    run = {"summary": {"event_class": "exhibited", "event_name": "RSA"},
           "profile": {"client_name": "Northwind", "website": "https://northwind.io"}}
    assert W.present_outreach(run, stored)[0]["opener"] == stored[0]["opener"]
    monkeypatch.setattr(S, "get_profile", lambda pid, email: run["profile"])
    run2 = {"summary": run["summary"], "profile_id": 4, "email": "me@x"}
    assert W.present_outreach(run2, stored)[0]["opener"] == stored[0]["opener"]


# ── seen before ──

def test_seen_before_counts_one_company_at_other_events():
    prior = {W.org_key("Mercury"): [{"event": "AUSA 2025", "domains": ["mrcy.com"]},
                                    {"event": "DSEI 2025", "domains": ["mrcy.com"]}],
             W.org_key("Acme"): [{"event": "RSA Conference 2025", "domains": []},
                                 {"event": "RSA Conference", "domains": []},
                                 {"event": "Black Hat", "domains": []}]}
    sig = W.repeat_signal([("Mercury", "mercury.com"), ("Acme", None)], prior,
                          event_name="RSA Conference 2026")
    # Mercury the bank is not the defence firm, and Acme's two RSA rows are
    # this event again: Black Hat alone is one other event, not a repeat.
    assert sig["repeats"] == []
    sig = W.repeat_signal([("Mercury", "mrcy.com")], prior, event_name="RSA Conference 2026")
    assert [(r["org"], r["count"]) for r in sig["repeats"]] == [("Mercury", 2)]
    assert W.event_series_key("Money20/20 USA 2026") == W.event_series_key("money20/20 usa")
