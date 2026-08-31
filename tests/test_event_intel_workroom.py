"""Work-the-room: the four rules the play refuses to break.

The point of these tests is not that the module produces openers. It is that
the three things event-radar says NEVER, and the one thing it says MUST, are
enforced by code that throws work away, rather than requested in a prompt that
a model is free to ignore.
"""

import datetime
import json

import pytest

from tracker import claude_websearch
from tracker import event_intel_store as store
from tracker import event_intel_workroom as W

UTC = datetime.timezone.utc
PROFILE = {"client_name": "Northwind Analytics", "buyer_roles": "VP Data",
           "verticals": "fintech", "geo_scope": "North America"}
EVENT = {"name": "FinovateFall", "ends_on": "2026-08-20",
         "location": "New York", "organizer": "Finovate"}


def _row(org, person=None, role="exhibitor", opener=None, fit=80, **kw):
    d = {"org_name": org, "person_name": person, "role": role,
         "opener": opener, "fit": fit, "fit_note": "n", "angle": "a",
         "unqualified": False}
    d.update(kw)
    return d


# ── Step 1. The event class is declared, never inferred ───────────────────

def test_every_event_class_has_a_complete_play():
    for key in W.EVENT_CLASSES:
        play = W.play_for(key)
        for field in ("label", "signal", "why", "play", "opener_rule"):
            assert play.get(field), "%s is missing %s" % (key, field)


@pytest.mark.parametrize("bad", ["", None, "booth", "OWNED", "industry", "owned "])
def test_an_undeclared_event_class_raises_rather_than_defaulting(bad):
    """The rule that protects everything else.

    A default here writes a competitor-event follow-up in the voice of an
    owned-event follow-up, and nothing downstream would look wrong.
    """
    with pytest.raises(ValueError) as e:
        W.play_for(bad)
    assert "never" in str(e.value).lower()
    for key in W.EVENT_CLASSES:
        assert key in str(e.value)


def test_the_five_classes_are_exactly_the_skills_five():
    assert set(W.EVENT_CLASSES) == {"owned", "exhibited", "attended",
                                    "competitor", "partner"}


# ── The window ────────────────────────────────────────────────────────────

def test_a_missing_end_date_never_reports_an_open_window():
    """Silence about the date must not read as "you are inside the window"."""
    state = W.window_state(None)
    assert state["known"] is False
    assert state["state"] is None
    assert "not assumed to be open" in state["note"]


@pytest.mark.parametrize("hours,expected", [
    (-40, W.WINDOW_EARLY),
    (2, W.WINDOW_PRIME),
    (47, W.WINDOW_PRIME),
    (60, W.WINDOW_CLOSING),
    (71, W.WINDOW_CLOSING),
    (100, W.WINDOW_EXPIRED),
    (24 * 30, W.WINDOW_EXPIRED),
])
def test_the_window_state_tracks_the_48_and_72_hour_boundaries(hours, expected):
    ended = datetime.datetime(2026, 8, 20, 23, 59, tzinfo=UTC)
    assert W.window_state("2026-08-20", ended + datetime.timedelta(hours=hours)
                          )["state"] == expected


def test_an_expired_window_says_so_instead_of_blocking_the_work():
    state = W.window_state("2026-01-01", datetime.datetime(2026, 3, 1, tzinfo=UTC))
    assert state["state"] == W.WINDOW_EXPIRED
    assert "has passed" in state["note"]
    # It labels; it does not refuse. A deliberately late follow-up is the
    # user's call to make.
    assert "cannot" not in state["note"].lower()


def test_a_malformed_date_is_unknown_rather_than_guessed():
    assert W.window_state("not a date")["known"] is False
    assert W.window_state("2026-13-45")["known"] is False


def test_a_real_date_object_is_accepted_as_well_as_a_string():
    d = datetime.date(2026, 8, 20)
    now = datetime.datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
    assert W.window_state(d, now)["state"] == W.WINDOW_PRIME


# ── org_key, which decides whether a booth note is found at all ───────────

@pytest.mark.parametrize("a,b", [
    ("Acme Technologies, Inc.", "Acme Technologies"),
    ("Acme Ltd", "acme"),
    ("The Widget Group", "Widget"),
    ("Foo  Bar   GmbH", "foo bar"),
])
def test_two_spellings_of_one_company_share_a_key(a, b):
    """A note written against one spelling has to be found against the other,
    or the booth rule refuses a conversation that really happened."""
    assert W.org_key(a) == W.org_key(b)


def test_a_name_made_only_of_noise_words_does_not_collapse_to_empty():
    """Two different generic names must not become the same company."""
    assert W.org_key("The Group") != ""
    assert W.org_key("The Group") != W.org_key("The Systems")


def test_different_companies_do_not_share_a_key():
    assert W.org_key("Acme") != W.org_key("Acmey")


# ── Booth notes ───────────────────────────────────────────────────────────

def test_booth_notes_parse_into_a_lookup_by_company():
    notes = W.index_booth_notes("Acme Inc: asked about SOC2\nBeta Ltd: wants pricing")
    assert notes[W.org_key("Acme")] == "asked about SOC2"
    assert notes[W.org_key("beta")] == "wants pricing"


def test_two_notes_about_one_company_are_joined_not_dropped():
    """A rep who wrote twice said two things, and losing one loses evidence."""
    notes = W.index_booth_notes("Acme: asked about SOC2\nAcme Inc: also wants pricing")
    assert "SOC2" in notes[W.org_key("Acme")]
    assert "pricing" in notes[W.org_key("Acme")]


@pytest.mark.parametrize("raw", ["", None, "no colon here", "   ", ": empty name",
                                 "Acme:   "])
def test_unparseable_note_lines_are_ignored_rather_than_stored_empty(raw):
    """An empty note must not count as evidence a conversation happened."""
    assert W.index_booth_notes(raw) == {}


# ── Rule 1: NEVER pretend you spoke to someone at the booth ───────────────

@pytest.mark.parametrize("opener", [
    "Great chatting at the show about your pipeline.",
    "Good to meet you at FinovateFall.",
    "As promised, here is the benchmark deck.",
    "You mentioned your team is on Snowflake.",
    "We spoke about SOC2 last week.",
    "Following up on our conversation at the booth.",
    "Thanks for stopping by our stand.",
    "Picking up where we left off.",
    "After we spoke, I pulled the numbers.",
    "You were asking about pricing tiers.",
])
def test_every_flavour_of_invented_conversation_is_detected(opener):
    assert W.claims_contact(opener), "undetected fabrication: %r" % opener


@pytest.mark.parametrize("opener", [
    "I saw Acme on the exhibitor list at FinovateFall.",
    "Acme was at FinovateFall last week and so were we.",
    "The most surprising thing from the week was the payments track.",
    "Curious how your team is thinking about data residency.",
])
def test_an_honest_opener_is_not_flagged_as_a_fabrication(opener):
    assert W.claims_contact(opener) == []


def test_a_fabricated_conversation_is_replaced_and_the_reason_is_kept():
    rows = [_row("Acme", person="Dana Lee",
                 opener="Great chatting at the booth about SOC2.")]
    out = W.enforce(rows, event_class=W.CLASS_EXHIBITED, notes={},
                    event_name="FinovateFall", client_name="Northwind")
    row = out["rows"][0]
    assert row["draft_status"] == W.DRAFT_NO_EVIDENCE
    assert W.claims_contact(row["opener"]) == []
    assert "Acme" in row["draft_reason"]
    assert "nobody recorded" in row["draft_reason"]
    # The offending phrase is named, so the user can see what was caught.
    assert row["draft_flagged"]
    assert out["rewritten_count"] == 1


def test_a_booth_note_licenses_the_conversation_it_records():
    """The rule is about evidence, not about vocabulary. A rep who wrote the
    note gets to reference the conversation."""
    rows = [_row("Acme", person="Dana Lee",
                 opener="Great chatting at the booth about SOC2.")]
    out = W.enforce(rows, event_class=W.CLASS_EXHIBITED,
                    notes={W.org_key("Acme"): "asked about SOC2"},
                    event_name="FinovateFall", client_name="Northwind")
    row = out["rows"][0]
    assert row["draft_status"] == W.DRAFT_OK
    assert row["opener"] == "Great chatting at the booth about SOC2."
    assert row["booth_note"] == "asked about SOC2"
    assert out["rewritten_count"] == 0


@pytest.mark.parametrize("event_class", list(W.EVENT_CLASSES))
def test_the_booth_rule_applies_to_every_class_not_just_the_exhibited_one(event_class):
    """The class does not create the evidence. The note does.

    An owned event is the tempting exception, and it is not one: a sponsor of
    your own conference is someone you have a contract with, not someone you
    necessarily spoke to.
    """
    rows = [_row("Acme", person="Dana Lee", opener="As promised, the deck.")]
    out = W.enforce(rows, event_class=event_class, notes={},
                    event_name="FinovateFall", client_name="Northwind")
    assert out["rows"][0]["draft_status"] in (W.DRAFT_NO_EVIDENCE,)
    assert W.claims_contact(out["rows"][0]["opener"]) == []


def test_a_note_for_a_different_company_does_not_license_this_one():
    rows = [_row("Acme", person="Dana", opener="We spoke about SOC2.")]
    out = W.enforce(rows, event_class=W.CLASS_EXHIBITED,
                    notes={W.org_key("Beta"): "wants pricing"},
                    event_name="FinovateFall")
    assert out["rows"][0]["draft_status"] == W.DRAFT_NO_EVIDENCE


# ── Rule 2: NEVER aggressive displacement on a competitor's event ─────────

@pytest.mark.parametrize("opener", [
    "Teams usually switch once they hit this ceiling.",
    "We are faster than what you saw last week.",
    "Unlike them, we do not charge per seat.",
    "Most people are stuck with a tool that cannot do this.",
    "Tired of waiting on your vendor?",
    "Worth a look before you rip and replace.",
])
def test_displacement_language_is_detected(opener):
    assert W.is_aggressive(opener), "undetected displacement: %r" % opener


def test_a_competitor_event_draft_with_displacement_is_replaced():
    rows = [_row("Acme", person="Dana",
                 opener="Most teams switch once they outgrow it.")]
    out = W.enforce(rows, event_class=W.CLASS_COMPETITOR, notes={},
                    event_name="RivalCon", client_name="Northwind")
    row = out["rows"][0]
    assert row["draft_status"] == W.DRAFT_AGGRESSIVE
    assert W.is_aggressive(row["opener"]) == []
    assert "soft angle" in row["draft_reason"]
    assert row["draft_flagged"]


def test_the_same_language_is_left_alone_on_a_non_competitor_event():
    """The ban is specific to a competitor's event, and over-applying it would
    censor a legitimate migration pitch at a neutral conference."""
    rows = [_row("Acme", person="Dana",
                 opener="Most teams switch once they outgrow it.")]
    out = W.enforce(rows, event_class=W.CLASS_ATTENDED, notes={},
                    event_name="DataCon", client_name="Northwind")
    assert out["rows"][0]["draft_status"] == W.DRAFT_OK
    assert out["rows"][0]["opener"] == "Most teams switch once they outgrow it."


def test_the_replacement_for_a_competitor_event_is_itself_soft():
    """The fallback must not be the thing it is rescuing the user from."""
    text = W.fallback_opener(org="Acme", event_name="RivalCon",
                             role_label="Exhibitor",
                             event_class=W.CLASS_COMPETITOR,
                             client_name="Northwind")
    assert W.is_aggressive(text) == []
    assert W.claims_contact(text) == []


@pytest.mark.parametrize("event_class", list(W.EVENT_CLASSES))
def test_no_fallback_opener_ever_claims_a_conversation(event_class):
    """The replacement is the last line of defence and cannot re-offend."""
    text = W.fallback_opener(org="Acme Ltd", event_name="FinovateFall",
                             role_label="Exhibitor", event_class=event_class,
                             client_name="Northwind")
    assert W.claims_contact(text) == []
    assert text.strip()


# ── Rule 3: anonymous rows get an account play, not a message ─────────────

def test_a_row_with_no_named_person_becomes_an_account_play():
    rows = [_row("Acme", person=None, opener="I saw Acme at the show.")]
    out = W.enforce(rows, event_class=W.CLASS_ATTENDED, notes={},
                    event_name="FinovateFall")
    row = out["rows"][0]
    assert row["draft_status"] == W.DRAFT_ACCOUNT
    assert "no person" in row["account_note"]
    assert "second step" in row["account_note"]


def test_a_named_person_gets_no_account_note():
    rows = [_row("Acme", person="Dana Lee", opener="I saw Acme at the show.")]
    out = W.enforce(rows, event_class=W.CLASS_ATTENDED, notes={},
                    event_name="FinovateFall")
    assert out["rows"][0]["draft_status"] == W.DRAFT_OK
    assert out["rows"][0]["account_note"] is None


def test_a_fabrication_outranks_the_account_flag_when_a_row_has_both():
    """Order matters. Both are true, but the fabricated conversation is the
    one that would go out under the user's name and be false, so it must be
    the status the user sees and the reason they read.
    """
    rows = [_row("Acme", person=None, opener="Great chatting at the booth.")]
    out = W.enforce(rows, event_class=W.CLASS_EXHIBITED, notes={},
                    event_name="FinovateFall")
    row = out["rows"][0]
    assert row["draft_status"] == W.DRAFT_NO_EVIDENCE
    assert W.claims_contact(row["opener"]) == []
    # And the account guidance is still attached, because it is still true.
    assert row["account_note"]


# ── Rule 4: qualify to ICP, and count what was cut ────────────────────────

def test_the_non_icp_tail_is_cut_and_counted_not_padded():
    rows = [_row("A", fit=90), _row("B", fit=54), _row("C", fit=70),
            _row("D", fit=10)]
    split = W.split_by_fit(rows)
    assert [r["org_name"] for r in split["kept"]] == ["A", "C"]
    assert split["counts"] == {"kept": 2, "cut": 2, "unqualified": 0, "roster": 4}


def test_the_floor_is_inclusive_at_its_own_value():
    assert W.split_by_fit([_row("A", fit=W.ICP_FLOOR)])["counts"]["kept"] == 1
    assert W.split_by_fit([_row("A", fit=W.ICP_FLOOR - 1)])["counts"]["cut"] == 1


def test_an_unqualified_row_is_neither_kept_nor_cut():
    """Silence from the model is not evidence about the company. Scoring it
    zero would rank it last, which reads as a judgement that was never made.
    """
    rows = [_row("A", fit=None, unqualified=True), _row("B", fit=90)]
    split = W.split_by_fit(rows)
    assert split["counts"]["unqualified"] == 1
    assert [r["org_name"] for r in split["kept"]] == ["B"]
    assert split["cut"] == []


def test_kept_rows_come_back_best_first():
    rows = [_row("A", fit=60), _row("B", fit=95), _row("C", fit=80)]
    assert [r["org_name"] for r in W.split_by_fit(rows)["kept"]] == ["B", "C", "A"]


# ── The CRM step, replaced honestly ───────────────────────────────────────

def test_the_missing_crm_is_stated_rather_than_faked():
    sig = W.repeat_signal(["Acme"], {})
    assert sig["crm"] is None
    assert "No CRM is connected" in sig["crm_note"]
    assert "before anyone sends" in sig["crm_note"]


def test_no_history_says_so_instead_of_reporting_zero_repeats():
    sig = W.repeat_signal(["Acme"], {})
    assert sig["measured"] is False
    assert sig["why_not_measured"]


def test_a_company_seen_at_two_prior_events_is_surfaced():
    prior = {W.org_key("Acme"): ["FinovateFall", "Money20/20"],
             W.org_key("Beta"): ["FinovateFall"]}
    sig = W.repeat_signal(["Acme Inc", "Beta Ltd"], prior)
    assert sig["measured"] is True
    assert [r["org"] for r in sig["repeats"]] == ["Acme Inc"]
    assert sig["repeats"][0]["count"] == 2


def test_a_company_seen_once_before_is_not_called_a_repeat():
    prior = {W.org_key("Beta"): ["FinovateFall"]}
    assert W.repeat_signal(["Beta"], prior)["repeats"] == []


# ── The qualification pass ────────────────────────────────────────────────

def _stub(monkeypatch, payload=None, error=None, text=None):
    def fake_ask(system, user, **kw):
        if error:
            return {"text": "", "error": error}
        return {"text": text if text is not None else json.dumps(payload),
                "error": None}
    monkeypatch.setattr(claude_websearch, "ask", fake_ask)


def test_a_company_the_model_skipped_is_marked_not_scored_zero(monkeypatch):
    _stub(monkeypatch, {"companies": [
        {"org": "Acme", "fit": 80, "fit_note": "n", "angle": "a", "opener": "o"}]})
    out = W.draft_all([_row("Acme"), _row("Ghost")], PROFILE, EVENT,
                      W.CLASS_ATTENDED, {})
    ghost = [r for r in out["rows"] if r["org_name"] == "Ghost"][0]
    assert ghost["unqualified"] is True
    assert ghost["fit"] is None
    assert "unscored rather than scored low" in ghost["qualify_note"]
    assert out["missing"] == 1


def test_a_failed_qualification_pass_is_an_error_not_an_empty_roster(monkeypatch):
    _stub(monkeypatch, error={"kind": "overloaded", "detail": "529"})
    out = W.draft_all([_row("Acme")], PROFILE, EVENT, W.CLASS_ATTENDED, {})
    assert out["errors"]
    assert out["rows"][0]["unqualified"] is True


def test_an_unreadable_reply_is_an_error_not_an_empty_result(monkeypatch):
    _stub(monkeypatch, text="I could not do that.")
    res = W.draft_batch([_row("Acme")], PROFILE, EVENT, W.CLASS_ATTENDED, {})
    assert res["drafts"] == {}
    assert "could not be read" in res["error"]


def test_a_fit_outside_zero_to_one_hundred_is_clamped(monkeypatch):
    _stub(monkeypatch, {"companies": [
        {"org": "Acme", "fit": 400, "fit_note": "n", "angle": "a", "opener": "o"},
        {"org": "Beta", "fit": -20, "fit_note": "n", "angle": "a", "opener": "o"}]})
    out = W.draft_all([_row("Acme"), _row("Beta")], PROFILE, EVENT,
                      W.CLASS_ATTENDED, {})
    fits = {r["org_name"]: r["fit"] for r in out["rows"]}
    assert fits == {"Acme": 100, "Beta": 0}


def test_the_booth_note_reaches_the_prompt_only_for_its_own_company(monkeypatch):
    seen = {}

    def fake_ask(system, user, **kw):
        seen["user"] = user
        return {"text": json.dumps({"companies": []}), "error": None}
    monkeypatch.setattr(claude_websearch, "ask", fake_ask)
    W.draft_batch([_row("Acme"), _row("Beta")], PROFILE, EVENT,
                  W.CLASS_EXHIBITED, {W.org_key("Acme"): "asked about SOC2"})
    lines = seen["user"].split("- ")
    acme = [b for b in lines if b.startswith("Acme")][0]
    beta = [b for b in lines if b.startswith("Beta")][0]
    assert "SOC2" in acme
    assert "SOC2" not in beta


def test_the_prompt_names_the_declared_class_and_its_rule(monkeypatch):
    seen = {}

    def fake_ask(system, user, **kw):
        seen["system"] = system
        return {"text": json.dumps({"companies": []}), "error": None}
    monkeypatch.setattr(claude_websearch, "ask", fake_ask)
    W.draft_batch([_row("Acme")], PROFILE, EVENT, W.CLASS_COMPETITOR, {})
    assert W.CLASS_PLAY[W.CLASS_COMPETITOR]["label"] in seen["system"]
    assert "Soft angle only" in seen["system"]


def test_a_non_competitor_prompt_does_not_carry_the_competitor_rule(monkeypatch):
    seen = {}

    def fake_ask(system, user, **kw):
        seen["system"] = system
        return {"text": json.dumps({"companies": []}), "error": None}
    monkeypatch.setattr(claude_websearch, "ask", fake_ask)
    W.draft_batch([_row("Acme")], PROFILE, EVENT, W.CLASS_OWNED, {})
    assert "COMPETITOR'S event" not in seen["system"]


def test_the_roster_brief_uses_the_stores_role_wording(monkeypatch):
    """The honesty contract is one wording, in one place. An exhibitor must
    not become an "attendee" on its way into a prompt."""
    seen = {}

    def fake_ask(system, user, **kw):
        seen["user"] = user
        return {"text": json.dumps({"companies": []}), "error": None}
    monkeypatch.setattr(claude_websearch, "ask", fake_ask)
    W.draft_batch([_row("Acme", role=store.ROLE_EXHIBITOR)], PROFILE, EVENT,
                  W.CLASS_ATTENDED, {})
    assert store.ROLE_LABELS[store.ROLE_EXHIBITOR] in seen["user"]


# ── Persistence ───────────────────────────────────────────────────────────

def test_an_unexplained_rewrite_is_labelled_rather_than_stored_silently():
    """A silent edit to a message someone is about to send under their own
    name is the thing this whole play exists to prevent."""
    row = store.normalise_outreach(
        {"org_name": "Acme", "draft_status": W.DRAFT_NO_EVIDENCE,
         "draft_reason": ""}, 1, 2, "FinovateFall", W.CLASS_EXHIBITED)
    assert "reason was lost" in row["draft_reason"]
    assert "unverified" in row["draft_reason"]


def test_a_stored_row_keeps_the_status_the_enforcement_pass_set():
    row = store.normalise_outreach(
        {"org_name": "Acme", "draft_status": W.DRAFT_AGGRESSIVE,
         "draft_reason": "displacement"}, 1, 2, "RivalCon", W.CLASS_COMPETITOR)
    assert row["draft_status"] == W.DRAFT_AGGRESSIVE
    assert row["draft_reason"] == "displacement"


def test_an_invented_draft_status_falls_back_to_ok_rather_than_stored_raw():
    row = store.normalise_outreach(
        {"org_name": "Acme", "draft_status": "totally_fine"}, 1, 2, "E",
        W.CLASS_OWNED)
    assert row["draft_status"] == W.DRAFT_OK


def test_an_unknown_event_class_is_refused_at_the_storage_boundary():
    with pytest.raises(ValueError):
        store.normalise_outreach({"org_name": "Acme"}, 1, 2, "E", "sponsored")


def test_a_row_with_no_company_name_is_dropped():
    assert store.normalise_outreach({"org_name": "  "}, 1, 2, "E",
                                    W.CLASS_OWNED) is None


def test_an_invented_role_is_dropped_rather_than_stored():
    """ROLE_LABELS is the one place that decides what a row is called, and a
    role it does not know would render as raw text next to real labels."""
    row = store.normalise_outreach(
        {"org_name": "Acme", "role": "attendee"}, 1, 2, "E", W.CLASS_OWNED)
    assert row["role"] is None


def test_a_real_role_survives_storage():
    row = store.normalise_outreach(
        {"org_name": "Acme", "role": store.ROLE_SPEAKER}, 1, 2, "E",
        W.CLASS_OWNED)
    assert row["role"] == store.ROLE_SPEAKER


def test_the_stored_field_list_matches_what_normalise_returns():
    row = store.normalise_outreach({"org_name": "Acme"}, 1, 2, "E", W.CLASS_OWNED)
    assert set(row) == set(store._OUTREACH_FIELDS)


@pytest.mark.parametrize("given,expected", [(400, 100), (-20, 0), (55, 55),
                                            ("70", 70), ("high", None),
                                            (None, None)])
def test_the_store_clamps_a_fit_score_on_its_own_account(given, expected):
    """The qualification pass clamps too, but this is a separate boundary and
    a row can reach it from somewhere else: a re-run, an import, a later
    export path. A boundary that trusts its caller is not a boundary.
    """
    row = store.normalise_outreach({"org_name": "Acme", "fit": given}, 1, 2,
                                   "E", W.CLASS_OWNED)
    assert row["fit"] == expected
