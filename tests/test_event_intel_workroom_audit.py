"""The workroom audit of 2026-10-01: each finding, as a test.

The workroom is the play whose output goes out under the user's name, so
the theme is the same throughout: nothing it writes may claim something
nobody recorded, and nothing it was given may silently fall out of it.
"""
import datetime
import json
import os
import sys

import pytest

from tracker import event_intel_jobs as J
from tracker import event_intel_pipeline as P
from tracker import event_intel_store as S
from tracker import event_intel_workroom as W

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))


def row(org="Acme", person="Dana", role="exhibitor", **kw):
    r = {"org_name": org, "person_name": person, "role": role, "fit": 80,
         "fit_note": "Fits.", "angle": "An angle.", "opener": "Curious how you see X.",
         "unqualified": False}
    r.update(kw)
    return r


def enforce(rows, cls, notes=None, site=None):
    return W.enforce(rows, event_class=cls, notes=notes or {}, event_name="RSA 2026",
                     client_name="Northwind", client_site=site)["rows"]


# ── 1. a booth that did not exist ──

@pytest.mark.parametrize("cls", [W.CLASS_ATTENDED, W.CLASS_COMPETITOR])
def test_a_booth_claim_is_replaced_where_the_client_had_no_booth_even_with_a_note(cls):
    out = enforce([row(opener="Thanks for stopping by our booth, here is the pricing.")],
                  cls, notes={W.org_key("Acme"): "grabbed a brochure, did not talk"})[0]
    assert out["draft_status"] == W.DRAFT_NO_BOOTH
    assert "our booth" not in out["opener"].lower()
    assert "our booth" in out["draft_flagged"]


def test_a_booth_claim_with_a_note_stands_where_the_client_exhibited():
    out = enforce([row(opener="Thanks for stopping by our booth yesterday.")],
                  W.CLASS_EXHIBITED, notes={W.org_key("Acme"): "stopped by, liked the demo"})[0]
    assert out["draft_status"] == W.DRAFT_REVIEW
    assert out["opener"].startswith("Thanks for stopping by our booth")


def test_specifics_the_note_does_not_support_are_named_for_review():
    out = enforce([row(opener="Great chatting yesterday, and as promised here is the pricing.")],
                  W.CLASS_EXHIBITED, notes={W.org_key("Acme"): "talked about onboarding"})[0]
    assert out["draft_status"] == W.DRAFT_REVIEW
    assert "pricing" in out["draft_flagged"] and "promised" in out["draft_flagged"]
    assert "your note does not mention" in out["draft_reason"]


def test_specifics_the_note_does_support_are_not_flagged():
    out = enforce([row(opener="Great chatting yesterday, and as promised here is the pricing.")],
                  W.CLASS_EXHIBITED, notes={W.org_key("Acme"): "promised to send pricing"})[0]
    assert out["draft_flagged"] == []
    assert out["draft_reason"].startswith("Review recipient")


# ── 2. conversation claims that passed unflagged ──

@pytest.mark.parametrize("phrase", [
    "Thanks for the great conversation", "It was a pleasure meeting you",
    "Glad we got to talk", "Thanks for the chat", "Here is the deck I promised",
    "Since we last spoke", "You'd mentioned onboarding", "Thanks for visiting",
    "Appreciated your time at the show", "Nice to finally connect in person",
    "Following up from our booth chat", "Loved hearing about your roadmap",
    "Jordan, saw you at RSA 2026",
])
def test_each_claim_the_audit_found_is_now_replaced(phrase):
    out = enforce([row(opener=phrase + ".")], W.CLASS_ATTENDED)[0]
    # Replaced; a phrase that also claims a booth is caught by that rule first.
    assert out["draft_status"] in (W.DRAFT_NO_EVIDENCE, W.DRAFT_NO_BOOTH), phrase


def test_a_company_name_ending_in_a_domain_is_not_a_link():
    out = enforce([row(opener="Curious how Salesforce.com sees onboarding.")], W.CLASS_ATTENDED)[0]
    assert out["draft_status"] == W.DRAFT_REVIEW


@pytest.mark.parametrize("phrase", [
    "I saw Acme on the exhibitor list at RSA 2026.",
    "Curious how your team is approaching onboarding this year.",
    "Happy to compare notes on what you are seeing.",
])
def test_an_opener_that_claims_nothing_is_kept(phrase):
    assert enforce([row(opener=phrase)], W.CLASS_ATTENDED)[0]["opener"] == phrase


# ── 8. displacement on a competitor's event ──

@pytest.mark.parametrize("phrase", ["We are replacing tools like theirs", "an alternative to the host",
                                    "frustrated with the incumbent", "a better fit than the host",
                                    "Compared to the host's platform", "time to ditch it"])
def test_displacement_the_audit_found_is_now_replaced(phrase):
    out = enforce([row(opener=phrase + ".")], W.CLASS_COMPETITOR)[0]
    assert out["draft_status"] == W.DRAFT_AGGRESSIVE, phrase


# ── 15. a link nobody supplied ──

def test_a_link_in_an_opener_is_replaced():
    out = enforce([row(opener="Congrats on the round, book here: x.example/book")],
                  W.CLASS_ATTENDED)[0]
    assert out["draft_status"] == W.DRAFT_LINK and "x.example/book" in out["draft_flagged"]
    assert "x.example" not in out["opener"]


def test_the_clients_own_site_is_not_a_foreign_link():
    out = enforce([row(opener="Our write-up is at https://www.northwind.example/report")],
                  W.CLASS_ATTENDED, site="https://www.northwind.example")[0]
    assert out["draft_status"] == W.DRAFT_REVIEW


# ── 10. an unreadable fit ──

@pytest.mark.parametrize("fit,expected", [("72.5", 72), (72.5, 72), ("80", 80), (101, 100)])
def test_a_numeric_fit_in_any_shape_is_read(fit, expected):
    assert W._clean_draft({"org": "A", "fit": fit})["fit"] == expected


def test_a_row_returned_without_a_usable_fit_is_unqualified_not_lost(monkeypatch):
    monkeypatch.setattr(W, "draft_batch", lambda *a, **k: {
        "drafts": {W.org_key("Beta"): W._clean_draft({"org": "Beta", "fit": "high", "opener": "x"})},
        "error": None})
    out = W.draft_all([{"org_name": "Beta"}], {}, {}, W.CLASS_ATTENDED, {})
    assert out["rows"][0]["unqualified"] is True and out["missing"] == 1
    assert W.split_by_fit(out["rows"])["counts"]["unqualified"] == 1


# ── 6. Apollo contacts never reach the drafting prompt ──

def test_apollo_contacts_are_not_in_the_drafting_prompt():
    brief = W._roster_brief([{"org_name": "Acme", "role": "exhibitor",
                              "apollo": {"industry": "Software",
                                         "contacts": [{"name": "Jordan Lee"}]}}], {})
    assert "Software" in brief and "Jordan" not in brief


def test_the_roster_is_fenced_as_data_and_the_edition_is_given(monkeypatch):
    seen = {}

    def ask(system, user, **kw):
        seen.update(system=system, user=user)
        return {"text": '{"companies": []}', "error": None}
    monkeypatch.setattr(W.claude_websearch, "ask", ask)
    W.draft_batch([{"org_name": "Acme", "role": "exhibitor"}], {"client_name": "N"},
                  {"name": "RSA", "edition": "2026"}, W.CLASS_ATTENDED, {})
    assert "<<<ROSTER" in seen["user"] and "ROSTER>>>" in seen["user"]
    assert "edition: 2026" in seen["system"]


# ── 13. fallback openers ──

def test_a_declared_attendee_is_not_called_a_list():
    o = W.fallback_opener(org="Acme", event_name="RSA", event_class=W.CLASS_ATTENDED,
                          role_label=S.ROLE_LABELS[S.ROLE_ATTENDEE_DECLARED])
    assert "list" not in o and "say publicly it would be at RSA" in o


def test_the_owned_fallback_never_asserts_attendance():
    o = W.fallback_opener(org="Acme", event_name="RSA", event_class=W.CLASS_OWNED,
                          role_label="Exhibitor")
    assert "joined us" not in o and "registration" not in o
    assert o.startswith("I saw Acme on the exhibitor list at RSA")


def test_the_partner_fallback_refers_to_nothing_undefined():
    o = W.fallback_opener(org="Acme", event_name="RSA", event_class=W.CLASS_PARTNER,
                          role_label="Partner", client_name="Northwind")
    assert "that problem" not in o and "Northwind works on an adjacent problem" in o


# ── 4 and 5. booth notes and dedup, through the real pipeline ──

class _Fake:
    def __init__(self, participants, prior=None):
        self.participants, self.prior, self.runs, self.saved = participants, prior, {}, []

    def update_run(self, run_id, **f):
        self.runs.setdefault(run_id, {}).update(f)


def _wire(monkeypatch, participants, prior=None, ends_on="2026-09-30"):
    fake = _Fake(participants, prior)
    monkeypatch.setattr(P.store, "update_run", fake.update_run)
    monkeypatch.setattr(P.store, "get_participants", lambda rid: [dict(p) for p in participants])
    monkeypatch.setattr(P.store, "get_events", lambda rid: [{"name": "RSA 2026", "ends_on": ends_on}])
    monkeypatch.setattr(P.store, "prior_participant_events", lambda email, exclude_run_id=None: prior)

    def save(run_id, source, name, cls, rows):
        fake.saved = rows
        return len(rows)
    monkeypatch.setattr(P.store, "save_outreach", save)
    monkeypatch.setattr(P.store, "get_outreach", lambda rid: fake.saved)
    seen = {}

    def draft_all(rows, profile, event, cls, notes):
        seen["rows"], seen["notes"] = rows, notes
        return {"rows": [dict(r, fit=80, fit_note="f", angle="a", opener="Curious.",
                              unqualified=False) for r in rows],
                "errors": [], "missing": 0, "batches": 1}
    monkeypatch.setattr(P.event_intel_workroom, "draft_all", draft_all)
    return fake, seen


def test_notes_in_every_common_shape_reach_their_company(monkeypatch):
    fake, seen = _wire(monkeypatch, [{"org_name": "Acme Inc", "org_domain": "acme.com"},
                                     {"org_name": "Beta Corp"}, {"org_name": "Gamma Labs"}])
    P._run_workroom(1, "me@x", 9, {"client_name": "N"}, W.CLASS_EXHIBITED,
                    "Spoke to Dana at Acme: wants pricing\n10:30 Beta Corp: demo next week\n"
                    "Gamma - liked the API\nZeta Systems: nobody by that name\n")
    assert set(seen["notes"]) == {W.org_key("Acme Inc"), W.org_key("Beta Corp"), W.org_key("Gamma Labs")}
    s = fake.runs[1]["summary"]
    assert s["booth_notes_given"] == 3
    assert s["booth_notes_unmatched"] == ["zeta systems"]


def test_one_company_under_two_names_with_one_domain_is_one_draft(monkeypatch):
    fake, seen = _wire(monkeypatch, [{"org_name": "Meta", "org_domain": "meta.com"},
                                     {"org_name": "Facebook", "org_domain": "meta.com", "person_name": "Ann"},
                                     {"org_name": "株式会社リコー"}, {"org_name": "Apex Systems"},
                                     {"org_name": "Apex Group"}])
    P._run_workroom(1, "me@x", 9, {"client_name": "N"}, W.CLASS_ATTENDED, None)
    names = sorted(r["org_name"] for r in seen["rows"])
    assert names == ["Apex Group", "Apex Systems", "Facebook", "株式会社リコー"]
    assert fake.runs[1]["summary"]["merged_rows"] == 1


def test_the_window_is_stored_with_its_end_date(monkeypatch):
    fake, _ = _wire(monkeypatch, [{"org_name": "Acme"}], ends_on="2026-09-30")
    P._run_workroom(1, "me@x", 9, {"client_name": "N"}, W.CLASS_ATTENDED, None)
    assert fake.runs[1]["summary"]["window_ends_on"] == "2026-09-30"


# ── 12. history that could not be read ──

def test_unreadable_history_is_not_called_a_first_roster():
    sig = W.repeat_signal(["Acme"], None)
    assert sig["measured"] is False
    assert "could not be read" in sig["why_not_measured"]
    assert "first event roster" in W.repeat_signal(["Acme"], {})["why_not_measured"]


# ── 14. what a failed run says ──

@pytest.mark.parametrize("exc,expected", [
    (RuntimeError("A completed stage has different code or inputs; start a new run"),
     "no longer matches what it was started with"),
    (RuntimeError("Worker code or runtime changed after submission. Start a new run."),
     "The platform was updated"),
    (AttributeError("'int' object has no attribute 'strip'"), "The run stopped unexpectedly."),
])
def test_a_failure_is_said_for_its_reader(exc, expected):
    text = J.reader_failure(exc)
    assert expected in text and "attribute" not in text


# ── 3, 7, 9 and the page: through the routes ──

@pytest.fixture
def client(monkeypatch):
    os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
    os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
    import app as appmod
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "harness@position2.com", "name": "T"}
    return c


def test_the_csv_gives_no_opener_to_a_company_below_the_floor(client, monkeypatch):
    run = {"id": 7, "mode": "workroom", "status": "complete", "query": "q",
           "summary": {"event_class": "attended", "event_name": "RSA 2026", "floor": 55}}
    rows = [row("Tail Co", fit=12, opener="Tail opener.", event_class="attended", event_name="RSA 2026"),
            row("Kept Co", fit=80, opener="Kept opener.", event_class="attended", event_name="RSA 2026"),
            row("Lost Co", fit=None, unqualified=True, opener="Lost opener.", event_class="attended")]
    monkeypatch.setattr(S, "get_run", lambda rid, email: run)
    monkeypatch.setattr(S, "get_outreach", lambda rid: [dict(r) for r in rows])
    text = client.get("/p2/strategic-agents/event-conference-intelligence/runs/7/outreach.csv"
                      ).get_data(as_text=True)
    lines = text.splitlines()
    assert "Cleared the ICP floor" in lines[0]
    assert lines[1].startswith("Kept Co") and "Kept opener." in lines[1]
    assert lines[2].startswith("Tail Co") and "No, cut below 55" in lines[2]
    assert "Tail opener." not in text and "Lost opener." not in text
    assert "Not qualified" in lines[3]


def test_a_retired_audience_search_run_is_not_a_workroom_source(client, monkeypatch):
    monkeypatch.setattr(S, "get_run", lambda rid, email: {
        "id": 2, "mode": "discover", "status": "complete"})
    r = client.post("/p2/strategic-agents/event-conference-intelligence/run",
                    json={"mode": "workroom", "source_run_id": 2, "event_class": "attended",
                          "profile_id": 1, "request_key": "k1"},
                    headers={"Origin": "http://localhost"})
    assert r.status_code == 400 and "completed event roster" in r.get_json()["error"]


def test_the_window_is_read_when_the_page_is_viewed(client, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    stale = {"state": "prime", "known": True, "hours": 12,
             "note": "About 12 hours since this event ended."}
    monkeypatch.setattr(S, "get_run", lambda rid, email: {
        "id": 7, "mode": "workroom", "status": "complete", "query": "q",
        "summary": {"window": stale, "window_ends_on": "2026-01-01"}})
    for name in ("get_outreach", "get_events", "get_participants", "get_sources", "get_candidates"):
        if hasattr(S, name):
            monkeypatch.setattr(S, name, lambda *a, **k: [])
    j = client.get("/p2/strategic-agents/event-conference-intelligence/runs/7",
                   headers={"Accept": "application/json"}).get_json()
    run = j.get("run", j)
    assert run["summary"]["window"]["state"] == W.WINDOW_EXPIRED


# ── 10, 11 and 4 on the page itself ──

from test_event_intel_event_view import page_script  # noqa: E402,F401  (fixture)
from test_event_intel_charts import _render_parts  # noqa: E402


def _page(page_script, summary, rows):
    s = {"event_name": "RSA 2026", "floor": 55, "event_class_label": "Attended",
         "event_class_signal": "Medium", "event_class_why": "w", "play": "p",
         "send_note": "n", "repeats": {"crm_note": "", "repeats": []},
         "qualify_errors": [], "rewritten_count": 0, "booth_notes_given": 0}
    s.update(summary)
    run = {"id": 7, "mode": "workroom", "status": "complete", "query": "q", "summary": s,
           "outreach": rows, "events": [], "participants": [], "sources": [],
           "role_labels": {"exhibitor": "Exhibitor"}}
    return _render_parts(page_script, run)["body"]


def test_a_stored_row_with_no_fit_is_listed_as_not_qualified(page_script):
    body = _page(page_script, {"counts": {"roster": 2, "kept": 1, "cut": 0, "unqualified": 1}},
                 [row("Alpha", fit=80, draft_status="review_required"),
                  row("Beta", fit=None, unqualified=False, draft_status="review_required")])
    assert "Not qualified" in body and "Beta" in body


def test_the_rewrite_note_promises_only_what_the_page_shows(page_script):
    rows = [row("Kept Co", fit=80, draft_status="rewritten_no_booth_note"),
            row("Tail Co", fit=20, draft_status="rewritten_no_booth_note")]
    body = _page(page_script, {"rewritten_count": 2,
                               "counts": {"roster": 2, "kept": 1, "cut": 1, "unqualified": 0}}, rows)
    assert "shown on the company below that cleared the floor" in body
    assert "1 was on companies cut below the floor" in body


def test_notes_that_matched_nobody_are_listed(page_script):
    body = _page(page_script, {"booth_notes_unmatched": ["zeta systems"],
                               "counts": {"roster": 1, "kept": 1, "cut": 0, "unqualified": 0}},
                 [row("Acme", fit=80, draft_status="review_required")])
    assert "1 booth note matched no single company on the roster" in body
    assert "zeta systems" in body


def test_a_rewritten_row_with_nobody_named_still_says_it_is_an_account_play(page_script):
    r = row("Acme", person=None, fit=80, draft_status="rewritten_no_booth_note",
            draft_reason="This draft claimed a conversation.",
            account_note="The roster names Acme but no person at it.")
    body = _page(page_script, {"counts": {"roster": 1, "kept": 1, "cut": 0, "unqualified": 0}}, [r])
    assert "This draft claimed a conversation." in body
    assert "The roster names Acme but no person at it." in body


@pytest.mark.parametrize("status,label", [("rewritten_no_booth", "claimed a booth you did not have"),
                                          ("rewritten_link", "contained a link nobody supplied")])
def test_the_new_statuses_have_reader_labels(page_script, status, label):
    import re
    body = _page(page_script, {"counts": {"roster": 1, "kept": 1, "cut": 0, "unqualified": 0}},
                 [row("Acme", fit=80, draft_status=status, draft_reason="r")])
    text = re.sub(r"<[^>]+>", " ", body)   # what a reader sees, not class names
    assert label in text and status not in text
