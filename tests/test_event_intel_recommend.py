"""Step 4, 7 and 8: scoring, the report, and the whole recommend play.

The model is stubbed everywhere. What is under test is the wiring the source
skill's output contract depends on: that the summary has exactly five
elements, that an unscored event is not silently ranked last, and that the
run refuses to start without a locked classification.
"""

import json

import pytest

from tracker import claude_websearch
from tracker import event_intel_audit as A
from tracker import event_intel_discover as D
from tracker import event_intel_pipeline as P
from tracker import event_intel_report as REP
from tracker import event_intel_rubric as R
from tracker import event_intel_scorer as SC
from tracker import event_intel_store as S

PROFILE = {"id": 1, "client_name": "Northwind", "website": "https://nw.example",
           "classification": R.CLASS_B2B_TO_MARKETING, "orientation": R.ORIENTATION_BOOTH,
           "buyer_roles": "VP Marketing", "verticals": "fintech",
           "geo_scope": "North America", "window_months": 12, "max_events": 15,
           "budget_note": "$40k for the year"}


def _stub(monkeypatch, payload=None, error=None, text=None):
    def fake_ask(system, user, **kw):
        fake_ask.systems.append(system)
        fake_ask.users.append(user)
        if error:
            return {"text": "", "error": error}
        return {"text": text if text is not None else json.dumps(payload),
                "error": None}
    fake_ask.systems, fake_ask.users = [], []
    monkeypatch.setattr(claude_websearch, "ask", fake_ask)
    return fake_ask


def _cand(name, **kw):
    d = {"name": name, "category": R.CAT_VERTICAL_SUMMIT, "famous": False,
         "website": "https://%s.example" % name.replace(" ", "").lower(),
         "cost_note": "$28,000 for a 3x3 booth", "attendees": "600"}
    d.update(kw)
    return d


# ── the scoring pass ──────────────────────────────────────────────────────

def test_scores_are_clamped_and_notes_kept(monkeypatch):
    _stub(monkeypatch, {"scores": [{"name": "PMM Summit", "relevance": 99,
                                    "relevance_note": "dense", "dm_access": 31,
                                    "dm_access_note": "booths", "engagement": 44,
                                    "engagement_note": "buying",
                                    "description": "600 PMMs.",
                                    "client_line": "Northwind sells to exactly these."}]})
    out = SC.score_all([_cand("PMM Summit")], PROFILE)
    c = out["scored"][0]
    assert c["relevance"] == 40 and c["engagement"] == 20
    assert c["relevance_note"] == "dense"
    assert c["client_line"].startswith("Northwind")


def test_the_scorer_never_returns_a_total_or_a_tier(monkeypatch):
    """The total is derived downstream from the sub-scores, so a model that
    writes its own cannot make the headline disagree with the bars."""
    _stub(monkeypatch, {"scores": [{"name": "PMM Summit", "relevance": 30,
                                    "dm_access": 30, "engagement": 15,
                                    "total": 99, "tier": "P1"}]})
    c = SC.score_all([_cand("PMM Summit")], PROFILE)["scored"][0]
    assert "total" not in c and "tier" not in c


def test_the_scorer_shapes_away_a_total_before_it_can_travel(monkeypatch):
    """Checked on the shaping function itself, not just on what reaches the
    candidate. A total that survives shaping is one copy-paste away from
    overwriting the recomputed one."""
    got = SC._clean({"name": "X", "relevance": 30, "dm_access": 30,
                     "engagement": 15, "total": 99, "tier": "P1",
                     "description": "d", "client_line": "c"})
    assert "total" not in got and "tier" not in got


def test_an_unreadable_scoring_reply_is_an_error_not_an_empty_result(monkeypatch):
    """Silence and prose look identical downstream: both produce no scores.
    Only one of them means the pass ran correctly."""
    _stub(monkeypatch, text="Happy to help. These all look like strong events.")
    out = SC.score_all([_cand("A")], PROFILE)
    assert out["errors"], "an unparsable reply must be reported, not swallowed"
    assert "could not be read" in out["errors"][0]
    assert len(out["unscored"]) == 1


def test_cost_is_withheld_from_the_scoring_prompt(monkeypatch):
    ask = _stub(monkeypatch, {"scores": []})
    SC.score_all([_cand("PMM Summit")], PROFILE)
    blob = " ".join(ask.systems + ask.users)
    assert "28,000" not in blob and "40k" not in blob
    assert "Cost never moves a score" in blob


def test_the_scoring_prompt_names_the_side_of_the_floor(monkeypatch):
    ask = _stub(monkeypatch, {"scores": []})
    SC.score_all([_cand("PMM Summit")], PROFILE)
    assert "Behind the booths" in " ".join(ask.systems)


def test_an_event_the_scorer_skipped_is_unranked_not_ranked_low(monkeypatch):
    """Zeroing it would read as "we judged this and it is bad"; dropping it
    would read as "this does not exist". Neither is what happened."""
    _stub(monkeypatch, {"scores": [{"name": "PMM Summit", "relevance": 30,
                                    "dm_access": 30, "engagement": 15}]})
    out = SC.score_all([_cand("PMM Summit"), _cand("Ghost Expo")], PROFILE)
    assert [c["name"] for c in out["scored"]] == ["PMM Summit"]
    assert [c["name"] for c in out["unscored"]] == ["Ghost Expo"]
    assert out["unscored"][0]["scoring_note"]


def test_a_failed_scoring_batch_is_reported(monkeypatch):
    _stub(monkeypatch, error={"kind": "transport", "detail": "HTTP 503"})
    out = SC.score_all([_cand("A")], PROFILE)
    assert out["errors"] and "503" in out["errors"][0]
    assert out["scored"] == [] and len(out["unscored"]) == 1


def test_candidates_are_split_into_batches(monkeypatch):
    ask = _stub(monkeypatch, {"scores": []})
    SC.score_all([_cand("E%d" % i) for i in range(13)], PROFILE)
    assert len(ask.users) == 3


# ── Step 8's anti-patterns, measured ──────────────────────────────────────

def test_a_shared_second_sentence_is_flagged_as_interchangeable():
    line = "Northwind helps these teams attribute pipeline across paid channels."
    hits = SC.flag_interchangeable([{"name": "A", "client_line": line},
                                    {"name": "B", "client_line": line},
                                    {"name": "C", "client_line": "Wholly other words here."}])
    assert len(hits) == 1
    assert {hits[0]["a"], hits[0]["b"]} == {"A", "B"}


def test_genuinely_different_second_sentences_are_not_flagged():
    assert SC.flag_interchangeable([
        {"name": "A", "client_line": "Payments CMOs here already run paid social."},
        {"name": "B", "client_line": "Regional banks buy compliance tooling first."}]) == []


def test_marketing_superlatives_are_flagged():
    hits = SC.flag_banned_language([{"name": "A", "description": "The premier event."},
                                    {"name": "B", "description": "600 PMMs.",
                                     "client_line": "Specific."}])
    assert [h["name"] for h in hits] == ["A"]
    assert "premier" in hits[0]["words"]


def test_a_one_sentence_entry_is_flagged_as_thin():
    hits = SC.flag_thin_descriptions([{"name": "A", "description": "600 PMMs.",
                                       "client_line": ""},
                                      {"name": "B", "description": "x", "client_line": "y"}])
    assert [h["name"] for h in hits] == ["A"]
    assert hits[0]["missing"] == ["client-specific case"]


# ── the executive summary ─────────────────────────────────────────────────

def _summary(**over):
    kw = dict(profile=PROFILE,
              ranked={"kept": [], "counts": {"kept": 0}},
              shortfall=[], audit={"checked": 0}, generic={"measured": False,
                                                           "why_not_measured": "first run"},
              scoring_errors=[], interchangeable=[], banned=[], thin=[], unscored=[])
    kw.update(over)
    return REP.executive_summary(**kw)


def test_the_summary_has_the_five_elements_and_no_sixth():
    """The skill specifies five, in order, and forbids meta-sections about the
    scoring process. Assembling it in code is what makes that structural."""
    s = _summary()
    for key in ("title", "client_profile", "methodology", "assumptions", "top_five"):
        assert key in s
    assert not any("process" in k or "meta" in k for k in s)


def test_the_title_uses_a_colon_not_a_dash():
    assert _summary()["title"] == "Northwind: Conference Analysis"


def test_the_grading_pass_note_counts_every_event_it_graded():
    """It used to count the kept list plus the unscored, which is wrong in
    the same direction the whole agent was wrong: the grading pass sees every
    candidate, so on a run that graded seven and kept one it printed "The 1
    events were graded in 2 separate passes"."""
    s = _summary(scoring_batches=2,
                 ranked={"kept": [{"name": "K"}],
                         "worth_a_look": [{"name": "L1"}, {"name": "L2"}],
                         "excluded": [{"name": "X1"}, {"name": "X2"}],
                         "counts": {}})
    note = [n for n in s["notes"] if "separate passes" in n["head"]][0]
    assert "5 events were graded" in note["head"], note["head"]
    assert "1 event" not in note["head"]


def test_the_grading_pass_note_agrees_with_itself_on_one_event():
    """"The 1 events" shipped to a client. A count and its verb come from one
    place now."""
    s = _summary(scoring_batches=2,
                 ranked={"kept": [{"name": "K"}], "counts": {}})
    note = [n for n in s["notes"] if "separate passes" in n["head"]][0]
    assert "1 event was graded" in note["head"], note["head"]


def test_the_methodology_is_the_rubric_as_applied_to_this_client():
    m = _summary()["methodology"]
    assert "40" in m and "110" in m
    assert "exhibitor booths" in m
    assert "never an input to a score" in m


def test_the_client_profile_element_reads_as_a_sentence():
    cp = _summary()["client_profile"]
    assert "Northwind" in cp and "fintech" in cp and "VP Marketing" in cp
    assert cp.endswith(".")


# ── a category's own explanation, quoted rather than folded ──────────────
#
# The live Beta Bionics report ended its assumptions paragraph mid-word, ran
# to 180 words, and printed "AWS Summit city tours" as "aws summit city
# tours". All three came from one line: several 600-character model notes
# lowercased and folded into a single sentence.

_LIVE_NOTE = (
    "Searched for AWS/Salesforce/HubSpot/ServiceNow/Databricks/Snowflake free "
    "city vendor events in connection with Beta Bionics, and separately "
    "searched for any diabetes tie-in to this event category. Results "
    "confirmed this category consists of enterprise B2B software vendor "
    "road-shows (e.g., AWS Summit city tours, Salesforce city events) whose "
    "audiences are IT and business budget owners for cloud software.")


def test_a_quoted_reason_keeps_the_capitals_the_model_wrote():
    """The failure that made a paying client's report look careless. Folding a
    note into a sentence needed a lowercase clause, and lowercasing a whole
    paragraph destroys every brand name in it."""
    r = REP._reason(_LIVE_NOTE)
    assert "AWS" in r and "aws" not in r


def test_a_quoted_reason_is_capped_and_says_where_it_was_cut():
    r = REP._reason(_LIVE_NOTE)
    assert len(r) <= REP.REASON_CHARS + 1
    assert r.endswith("\u2026"), "a trimmed reason has to show that it was trimmed"


def test_a_quoted_reason_never_cuts_mid_word():
    """'published on t' in a live report reads as a model that stopped
    mid-thought. It was our own cap, and only one of those two is worth
    going to investigate."""
    r = REP._reason("a " + "x" * 400)
    assert r == "A " + "x" * 198 + "\u2026", r


def test_a_short_reason_is_left_alone():
    assert REP._reason("No edition falls in the window.") == \
        "No edition falls in the window."


def test_a_cut_on_a_sentence_boundary_is_still_marked_as_a_cut():
    """The one shape that could pass for a complete explanation. The reader
    sees a tidy full stop and has no way to know a second sentence said what
    the search actually concluded."""
    r = REP._reason(_LIVE_NOTE)
    assert r.endswith("\u2026")
    assert not r.endswith(".\u2026")


def test_a_reason_keeps_whole_sentences_while_they_fit():
    """A note usually says what it searched first and what it concluded
    second. Keeping only the first sentence throws away the half a reader
    actually wants."""
    r = REP._reason("Searched three listings. No edition falls in the window.")
    assert r.endswith("window.")
    assert "Searched three listings." in r


def test_a_reason_does_not_split_on_a_full_stop_inside_a_url():
    r = REP._reason("The organiser publishes it at attd.kenes.com only.")
    assert r.endswith("only.")


def test_a_reason_gets_a_full_stop_it_was_missing():
    assert REP._reason("No events found").endswith(".")


def test_an_empty_reason_stays_empty_rather_than_becoming_a_full_stop():
    assert REP._reason("") == "" and REP._reason(None) == ""


def test_the_assumptions_paragraph_quotes_each_category_after_a_colon():
    """Not folded into one run-on sentence: one label, one colon, one reason."""
    s = _summary(shortfall=[
        {"category": R.CAT_FREE_VENDOR, "label": "Free sponsor-funded event",
         "status": "empty", "why": _LIVE_NOTE},
        {"category": R.CAT_EMERGING, "label": "Emerging event",
         "status": "empty", "why": "Nothing in years one to three here."}])
    text = " ".join(s["assumptions"])
    assert "Free sponsor-funded event: Searched for AWS" in text
    assert "Emerging event: Nothing in years one to three here." in text
    assert "AWS" in text


def test_assumptions_separate_a_failed_category_from_an_empty_one():
    s = _summary(shortfall=[
        {"category": R.CAT_FREE_VENDOR, "label": "Free sponsor-funded event",
         "status": "empty", "why": "No vendor runs city events in this vertical."},
        {"category": R.CAT_SIDE_EVENT, "label": "Side event",
         "status": "error", "why": "transport: HTTP 503"}])
    text = " ".join(s["assumptions"])
    assert "hole in the analysis" in text
    assert "503" in text
    assert "under the two-event quota" in text


def test_assumptions_say_when_the_cross_client_check_could_not_run():
    """Case-insensitive on purpose. The reason arrives from the caller in
    whatever case it was written, and a note now starts its detail with a
    capital, because "Cross-client check: not measured" followed by "first
    run for this client" read like two half-finished sentences."""
    s = _summary(generic={"measured": False,
                          "why_not_measured": "no earlier client to compare against"})
    assert any("no earlier client" in a.lower() for a in s["assumptions"])
    assert any("not measured" in a for a in s["assumptions"])


def test_assumptions_carry_the_genericness_warning_when_flagged():
    s = _summary(generic={"measured": True, "flagged": True, "checked": 1,
                          "advice": "75% of this list was also recommended to Acme."})
    assert any("also recommended to Acme" in a for a in s["assumptions"])


def test_assumptions_say_when_the_famous_event_audit_did_not_run():
    s = _summary(audit={"checked": 3, "error": "transport: HTTP 503", "cut": []})
    assert any("out of habit" in a for a in s["assumptions"])


def test_assumptions_never_come_back_empty():
    """With every stage silent, the section still has to say something. An
    empty "assumptions and notes" reads as "nothing was assumed", which is
    never true of a run built on web search."""
    s = REP.executive_summary(
        profile=PROFILE, ranked={"kept": [], "counts": {}},
        shortfall=[], audit={}, generic={}, scoring_errors=[],
        interchangeable=[], banned=[], thin=[], unscored=[])
    assert s["assumptions"] == ["Nothing material was left unmeasured on this run."]


def test_assumptions_name_the_events_that_could_not_be_scored():
    s = _summary(unscored=[{"name": "Ghost Expo"}, {"name": "Phantom Summit"}])
    text = " ".join(s["assumptions"])
    assert "Ghost Expo" in text and "Phantom Summit" in text
    assert "rather than ranked low" in text


def test_attendance_is_described_as_the_events_own_unverified_claim():
    s = _summary(ranked={"kept": [{"name": "A", "attendees": "12,000+"},
                                  {"name": "B"}], "counts": {}})
    assert any("own published claims" in a for a in s["assumptions"])


def test_top_five_is_capped_and_carries_the_client_specific_line():
    kept = [{"name": "E%d" % i, "total": 90 - i, "tier": "P1", "city": "NYC",
             "country": "US", "starts_on": "2026-05-0%d" % (i + 1),
             "client_line": "Case %d." % i} for i in range(8)]
    top = REP.top_five(kept)
    assert len(top) == 5
    assert top[0]["case"] == "Case 0."
    assert top[0]["where"] == "NYC, US"


def test_the_top_five_case_is_the_client_line_not_the_conference_blurb():
    """Sentence one is about the event and would fit any client. Sentence two
    is the only line in the report that is supposed to be about this one, so
    it is the line the summary leads with."""
    top = REP.top_five([{"name": "E", "total": 90,
                         "description": "4,000 attendees over three days.",
                         "client_line": "Northwind's attribution pitch lands here."}])
    assert top[0]["case"] == "Northwind's attribution pitch lands here."


def test_top_five_says_so_when_no_case_was_written():
    top = REP.top_five([{"name": "E", "total": 90}])
    assert "no written case" in top[0]["case"]
    assert top[0]["when"] == "dates not announced"
    assert top[0]["case_is_generic"] is False


def test_top_five_marks_a_case_that_is_really_the_generic_description():
    """client_line is the only sentence written about THIS client. Falling
    back to the event description is sometimes the best available answer;
    doing it silently puts the deliberately generic line in the flagship slot
    with nothing on screen to say so."""
    top = REP.top_five([{"name": "E", "total": 90,
                         "description": "Six hundred payments people."}])
    assert top[0]["case"] == "Six hundred payments people."
    assert top[0]["case_is_generic"] is True


def test_top_five_carries_the_outcome_and_cross_client_signals():
    """A live end-to-end check against real Postgres is what caught this
    field set being dropped here in the first place: every OTHER test of
    this feature asserted on ordering or on the full candidate list, never
    on top_five's own fixed field set, which is the one the report's
    flagship "top five" element actually reads."""
    top = REP.top_five([{"name": "E", "total": 76, "outcome_adjustment": -5,
                         "outcome_adjustment_reason": "Skipped before.",
                         "cross_client_count": 3,
                         "cross_client_note": "Watched by 3 other clients."}])
    assert top[0]["outcome_adjustment"] == -5
    assert top[0]["outcome_adjustment_reason"] == "Skipped before."
    assert top[0]["cross_client_count"] == 3
    assert top[0]["cross_client_note"] == "Watched by 3 other clients."


def test_top_five_does_not_mark_a_real_client_case_as_generic():
    top = REP.top_five([{"name": "E", "total": 90,
                         "description": "Six hundred payments people.",
                         "client_line": "Northwind sells to exactly these."}])
    assert top[0]["case"] == "Northwind sells to exactly these."
    assert top[0]["case_is_generic"] is False


# ── the whole play ────────────────────────────────────────────────────────

class _FakeStore:
    """Just enough of the store to run the pipeline without Postgres, keeping
    the real normalise_candidate so totals are still recomputed from
    sub-scores rather than trusted.

    outcome_pattern_data/cross_client_counts/population default to empty/zero
    -- the same safe defaults the real store returns with no DATABASE_URL --
    so every existing test that does not care about these two features is
    unaffected. Tests that DO care set them directly on the instance before
    calling _run_recommend.
    """

    def __init__(self):
        self.runs, self.rows = {}, []
        self.outcomes = {}
        self.outcome_pattern_data = {"by_category": {}, "by_format": {}}
        self.cross_client_counts = {}
        self.population = 0

    def update_run(self, run_id, **f):
        self.runs.setdefault(run_id, {}).update(f)

    def save_candidates(self, run_id, rows):
        for r in rows:
            c = S.normalise_candidate(r)
            if c:
                self.rows.append(c)
        return len(self.rows)

    def get_candidates(self, run_id):
        return list(self.rows)

    def prior_candidate_names(self, email, exclude_run_id=None):
        return [{"id": 7, "client_name": "Acme", "names": ["PMM Summit"]}]

    def get_outcomes(self, email):
        return dict(self.outcomes)

    def outcome_pattern(self, email, profile_id, exclude_run_id=None):
        return self.outcome_pattern_data

    def classification_population(self, classification, window_days, exclude_email):
        return self.population

    def cross_client_interest(self, name_keys, classification, window_days,
                              exclude_email):
        # Mirrors the real function's contract: only rows whose key was
        # actually asked about come back.
        return {k: v for k, v in self.cross_client_counts.items()
                if k in (name_keys or [])}


def _wire(monkeypatch, fake):
    for name in ("update_run", "save_candidates", "get_candidates",
                 "prior_candidate_names", "get_outcomes", "outcome_pattern",
                 "classification_population", "cross_client_interest"):
        monkeypatch.setattr(P.store, name, getattr(fake, name))


def test_a_full_recommend_run_produces_a_ranked_list_and_a_summary(monkeypatch):
    fake = _FakeStore()
    _wire(monkeypatch, fake)
    monkeypatch.setattr(P.event_intel_discover, "discover", lambda profile: {
        "candidates": [_cand("PMM Summit"), _cand("Dreamforce", famous=True,
                                                  category=R.CAT_INDUSTRY_FLAGSHIP),
                       _cand("Weak Expo")],
        "by_category": {}, "statuses": {}, "shortfall": [],
        "categories_searched": 6, "categories_failed": 0, "found": 3})
    monkeypatch.setattr(P.event_intel_audit, "audit_famous", lambda c, p: {
        "checked": 1, "error": None, "cut": [], "kept": [],
        "verdicts": {D.name_key("Dreamforce"): {
            "verdict": A.VERDICT_KEPT, "alternative": "MarTechFest",
            "why": "Buyers staff the booths here."}}})
    # PMM Summit 87, Dreamforce 74, Weak Expo 20. Distinct on purpose: equal
    # totals fall back to alphabetical, which would make this assert the
    # tie-break rather than the ranking.
    marks = {"PMM Summit": (36, 34, 17), "Dreamforce": (30, 30, 14),
             "Weak Expo": (8, 8, 4)}
    monkeypatch.setattr(P.event_intel_scorer, "score_all", lambda c, p: {
        "scored": [dict(x, relevance=marks[x["name"]][0],
                        dm_access=marks[x["name"]][1],
                        engagement=marks[x["name"]][2],
                        relevance_note="n", dm_access_note="n", engagement_note="n",
                        description="Texture.", client_line="Case for %s." % x["name"])
                   for x in c],
        "unscored": [], "errors": [], "batches": 1})

    P._run_recommend(1, "me@p2.example", PROFILE)
    run = fake.runs[1]
    assert run["status"] == "complete"
    s = run["summary"]
    assert s["title"] == "Northwind: Conference Analysis"
    assert [t["name"] for t in s["top_five"]] == ["PMM Summit", "Dreamforce"]
    assert [t["total"] for t in s["top_five"]] == [87, 74]
    assert [t["tier"] for t in s["top_five"]] == [R.TIER_P1, R.TIER_P2]
    assert s["counts"]["kept"] == 2
    # 8 + 8 + 4 = 20, below the floor of 70, so it is excluded and SAID to be.
    assert [e["name"] for e in s["excluded"]] == ["Weak Expo"]
    assert s["orientation"] == R.ORIENTATION_BOOTH


def test_outcome_pattern_reorders_top_five_without_touching_score_or_tier(monkeypatch):
    """End to end, because outcome_adjustment, apply_outcome_pattern and the
    pipeline wiring were each unit-tested separately, and the wiring between
    them is exactly where a real defect this session found (the CSV/page
    disagreement) actually lived. "Higher Score, Disliked" outscores "Lower
    Score, Liked" on the raw rubric (76 vs 74) but its category carries a
    real 4-of-4 skip pattern (-5 -> 71), which drops it behind the
    neutral-history event's untouched 74. Numbers verified against the real
    rubric.score() before being pinned here, not derived by hand."""
    fake = _FakeStore()
    fake.outcome_pattern_data = {
        "by_category": {R.CAT_VERTICAL_SUMMIT:
                        {"decisions": 4, "skipped": 4, "went_or_going": 0}},
        "by_format": {}}
    _wire(monkeypatch, fake)
    monkeypatch.setattr(P.event_intel_discover, "discover", lambda profile: {
        "candidates": [_cand("Higher Score, Disliked", category=R.CAT_VERTICAL_SUMMIT),
                       _cand("Lower Score, Liked", category=R.CAT_SIDE_EVENT)],
        "by_category": {}, "statuses": {}, "shortfall": [],
        "categories_searched": 6, "categories_failed": 0, "found": 2})
    monkeypatch.setattr(P.event_intel_audit, "audit_famous", lambda c, p: {
        "checked": 0, "error": None, "cut": [], "kept": [], "verdicts": {}})
    marks = {"Higher Score, Disliked": (32, 30, 14), "Lower Score, Liked": (30, 30, 14)}
    monkeypatch.setattr(P.event_intel_scorer, "score_all", lambda c, p: {
        "scored": [dict(x, relevance=marks[x["name"]][0],
                        dm_access=marks[x["name"]][1],
                        engagement=marks[x["name"]][2],
                        relevance_note="n", dm_access_note="n", engagement_note="n",
                        description="Texture.", client_line="Case for %s." % x["name"])
                   for x in c],
        "unscored": [], "errors": [], "batches": 1})

    P._run_recommend(1, "me@p2.example", PROFILE)
    s = fake.runs[1]["summary"]
    names = [t["name"] for t in s["top_five"]]
    totals = {t["name"]: t["total"] for t in s["top_five"]}
    assert names == ["Lower Score, Liked", "Higher Score, Disliked"], (
        "the outcome pattern did not reorder the disliked, higher-scoring "
        "event behind the neutral, lower-scoring one: %s" % names)
    assert totals == {"Higher Score, Disliked": 76, "Lower Score, Liked": 74}, (
        "total was mutated by an order-only signal that must never touch it: %s"
        % totals)


def test_cross_client_signal_reaches_the_executive_summary(monkeypatch):
    fake = _FakeStore()
    fake.population = A.CROSS_CLIENT_MIN_POPULATION
    _wire(monkeypatch, fake)
    monkeypatch.setattr(P.event_intel_discover, "discover", lambda profile: {
        "candidates": [_cand("Watched Summit")],
        "by_category": {}, "statuses": {}, "shortfall": [],
        "categories_searched": 6, "categories_failed": 0, "found": 1})
    monkeypatch.setattr(P.event_intel_audit, "audit_famous", lambda c, p: {
        "checked": 0, "error": None, "cut": [], "kept": [], "verdicts": {}})
    monkeypatch.setattr(P.event_intel_scorer, "score_all", lambda c, p: {
        "scored": [dict(c[0], relevance=34, dm_access=30, engagement=14,
                        relevance_note="n", dm_access_note="n", engagement_note="n",
                        description="Texture.", client_line="Case.")],
        "unscored": [], "errors": [], "batches": 1})
    # Shaped like the REAL store.cross_client_interest()'s return value
    # ({"name", "distinct_clients"}), which is what event_intel_audit.
    # cross_client_signal() actually takes as input -- not that function's
    # OWN output shape ({"count", "fires"}), which is a different contract.
    fake.cross_client_counts = {
        D.name_key("Watched Summit"): {"name": "Watched Summit",
                                       "distinct_clients": A.CROSS_CLIENT_MIN_DISTINCT}}

    P._run_recommend(1, "me@p2.example", PROFILE)
    s = fake.runs[1]["summary"]
    assert any("watched by other clients" in a for a in s["assumptions"]), (
        s["assumptions"])
    assert any("Watched Summit" in a and "3" in a for a in s["assumptions"])
    # No identity anywhere in the finished summary -- the fake's own count
    # dict was already shaped like the real one, and this confirms nothing
    # downstream added one.
    import json as _json
    assert "@position2.com" not in _json.dumps(s), (
        "an email address reached the client-facing summary")


def test_a_confidential_profile_never_queries_cross_client_signal(monkeypatch):
    fake = _FakeStore()
    called = []
    fake.classification_population = lambda *a, **k: called.append("population") or 0
    fake.cross_client_interest = lambda *a, **k: called.append("interest") or {}
    _wire(monkeypatch, fake)
    monkeypatch.setattr(P.event_intel_discover, "discover", lambda profile: {
        "candidates": [_cand("Any Event")],
        "by_category": {}, "statuses": {}, "shortfall": [],
        "categories_searched": 6, "categories_failed": 0, "found": 1})
    monkeypatch.setattr(P.event_intel_audit, "audit_famous", lambda c, p: {
        "checked": 0, "error": None, "cut": [], "kept": [], "verdicts": {}})
    monkeypatch.setattr(P.event_intel_scorer, "score_all", lambda c, p: {
        "scored": [dict(c[0], relevance=34, dm_access=30, engagement=14,
                        relevance_note="n", dm_access_note="n", engagement_note="n",
                        description="Texture.", client_line="Case.")],
        "unscored": [], "errors": [], "batches": 1})

    confidential_profile = dict(PROFILE, confidential=True)
    P._run_recommend(1, "me@p2.example", confidential_profile)
    assert called == [], (
        "a confidential profile's run still queried cross-client data: %s"
        % called)


def test_a_marquee_event_whose_audit_broke_survives_the_whole_run(monkeypatch):
    """End to end, because every part of this was already unit-tested and the
    field it depends on was still being dropped on the way to storage.

    The audit is one call per marquee event now. When one of those calls
    fails, the event is kept and reported unweighed rather than cut, and the
    run has to carry that fact all the way to the stored summary: `checked`
    counts what was SENT, so a stored run that keeps no record of the failures
    reads as five clean audits when two of them never happened.
    """
    fake = _FakeStore()
    _wire(monkeypatch, fake)
    monkeypatch.setattr(P.event_intel_discover, "discover", lambda profile: {
        "candidates": [_cand("Dreamforce", famous=True,
                             category=R.CAT_INDUSTRY_FLAGSHIP),
                       _cand("CES", famous=True,
                             category=R.CAT_INDUSTRY_FLAGSHIP)],
        "by_category": {}, "statuses": {}, "shortfall": [],
        "categories_searched": 6, "categories_failed": 0, "found": 2})
    # Dreamforce audited and kept; CES's own call broke.
    monkeypatch.setattr(P.event_intel_audit, "audit_famous", lambda c, p: {
        "checked": 2, "error": None, "cut": [], "kept": [],
        "failed": {D.name_key("CES"): {"name": "CES",
                                       "why": "transport: HTTP 503"}},
        "verdicts": {D.name_key("Dreamforce"): {
            "verdict": A.VERDICT_KEPT, "alternative": "MarTechFest",
            "why": "Buyers staff the booths here."}}})
    marks = {"Dreamforce": (36, 34, 17), "CES": (34, 32, 16)}
    monkeypatch.setattr(P.event_intel_scorer, "score_all", lambda c, p: {
        "scored": [dict(x, relevance=marks[x["name"]][0],
                        dm_access=marks[x["name"]][1],
                        engagement=marks[x["name"]][2],
                        relevance_note="n", dm_access_note="n",
                        engagement_note="n", description="Texture.",
                        client_line="Case for %s." % x["name"])
                   for x in c],
        "unscored": [], "errors": [], "batches": 1})

    P._run_recommend(1, "me@p2.example", PROFILE)
    s = fake.runs[1]["summary"]

    assert "CES" in [t["name"] for t in s["top_five"]], (
        "a marquee event was cut for losing a comparison that never ran")
    assert s["audit"]["failed"], (
        "the run kept no record of which audits failed")
    assert s["audit"]["failed"][D.name_key("CES")]["name"] == "CES"
    line = [a for a in s["assumptions"] if "marquee" in a][0]
    assert "1 marquee event was audited" in line, (
        "2 were sent and 1 was weighed; the report claims what the stage "
        "intended: %r" % line)
    assert "CES" in line


def test_the_totals_on_screen_are_recomputed_from_the_sub_scores(monkeypatch):
    fake = _FakeStore()
    _wire(monkeypatch, fake)
    monkeypatch.setattr(P.event_intel_discover, "discover", lambda profile: {
        "candidates": [_cand("PMM Summit")], "by_category": {}, "statuses": {},
        "shortfall": [], "categories_searched": 6, "categories_failed": 0, "found": 1})
    monkeypatch.setattr(P.event_intel_audit, "audit_famous",
                        lambda c, p: {"checked": 0, "error": None, "cut": [],
                                      "kept": [], "verdicts": {}})
    monkeypatch.setattr(P.event_intel_scorer, "score_all", lambda c, p: {
        "scored": [dict(c[0], relevance=36, dm_access=34, engagement=17,
                        relevance_note="n", dm_access_note="n", engagement_note="n",
                        total=110, tier="P1")],
        "unscored": [], "errors": [], "batches": 1})
    P._run_recommend(1, "me@p2.example", PROFILE)
    assert fake.rows[0]["total"] == 87
    assert fake.runs[1]["summary"]["top_five"][0]["total"] == 87


def test_a_run_that_discovers_nothing_explains_itself(monkeypatch):
    fake = _FakeStore()
    _wire(monkeypatch, fake)
    monkeypatch.setattr(P.event_intel_discover, "discover", lambda profile: {
        "candidates": [], "by_category": {},
        "statuses": {R.CAT_FREE_VENDOR: {"status": "error"}},
        "shortfall": [{"category": R.CAT_FREE_VENDOR, "status": "error",
                       "label": "Free sponsor-funded event", "why": "HTTP 503"}],
        "categories_searched": 5, "categories_failed": 1, "found": 0})
    P._run_recommend(1, "me@p2.example", PROFILE)
    s = fake.runs[1]["summary"]
    assert s["no_candidates"] is True
    assert s["categories_failed"] == 1
    assert "told apart from a market" in s["note"]


def test_recommend_without_a_locked_classification_fails_the_run(monkeypatch):
    """The skill's HARD STOP, enforced on the worker as well as at the route,
    so a run started any other way still cannot score the wrong crowd."""
    fake = _FakeStore()
    _wire(monkeypatch, fake)
    P.run_job(1, "recommend", "Northwind", profile={"client_name": "Northwind"})
    assert fake.runs[1]["status"] == "failed"
    assert "which side of the event floor" in fake.runs[1]["error"]


def test_an_unexpected_crash_never_leaves_a_run_stuck_on_running(monkeypatch):
    fake = _FakeStore()
    _wire(monkeypatch, fake)

    def boom(profile):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(P.event_intel_discover, "discover", boom)
    P.run_job(1, "recommend", "Northwind", profile=PROFILE, email="me@p2.example")
    assert fake.runs[1]["status"] == "failed"
    assert "kaboom" in fake.runs[1]["error"]


# ── a finished edition travels all the way to the reader ──────────────────

def test_a_finished_edition_is_named_in_the_assumptions():
    """rank() keeps it out of the list; this is the other half. A bucket
    computed and then dropped between rank() and the page reads as "we did not
    find it", which is a different and false claim from "we found it and it is
    over"."""
    ranked = {"kept": [], "over_cap": [], "counts": {},
              "finished": [{"name": "Analytics Leaders Forum 2019",
                            "total": 84, "ends_on": "2019-03-03"}]}
    summary = REP.executive_summary(
        profile={"client_name": "Northwind",
                 "classification": R.CLASS_B2B_TO_MARKETING},
        ranked=ranked, shortfall=[], audit={"checked": 0},
        generic={"measured": False, "why_not_measured": ""},
        scoring_errors=[], interchangeable=[], banned=[], thin=[], unscored=[])
    joined = " ".join(summary["assumptions"])
    assert "already finished" in joined
    assert "Analytics Leaders Forum 2019" in joined
    assert "2019-03-03" in joined


def test_a_run_with_nothing_finished_says_nothing_about_it():
    """A caveat that fires on every run is noise, not candour."""
    summary = REP.executive_summary(
        profile={"client_name": "Northwind",
                 "classification": R.CLASS_B2B_TO_MARKETING},
        ranked={"kept": [], "over_cap": [], "finished": [], "counts": {}},
        shortfall=[], audit={"checked": 0},
        generic={"measured": False, "why_not_measured": ""},
        scoring_errors=[], interchangeable=[], banned=[], thin=[], unscored=[])
    assert not any("already finished" in a for a in summary["assumptions"])


# ── one list of facts, two renderings ────────────────────────────────────

def test_the_pointers_and_the_prose_are_the_same_facts():
    """Two builders would eventually disagree, and the one nobody reads would
    be the one that stayed right."""
    s = _summary(shortfall=[
        {"category": R.CAT_SIDE_EVENT, "label": "Side event",
         "status": "error", "why": "The call failed."}])
    assert len(s["notes"]) == len(s["assumptions"])
    for note, prose in zip(s["notes"], s["assumptions"]):
        assert note["head"].rstrip(".") in prose
        if note["detail"]:
            assert note["detail"] in prose


def test_every_pointer_head_fits_on_one_line():
    """A head is read in a column. One that wraps to three lines on a phone
    is a paragraph again, which is the thing this replaced."""
    s = _summary(shortfall=[
        {"category": R.CAT_SIDE_EVENT, "label": "Side event", "status": "error",
         "why": "x" * 400},
        {"category": R.CAT_FREE_VENDOR, "label": "Free sponsor-funded event",
         "status": "empty", "why": "y" * 400, "budget_spent": True}],
        unscored=[{"name": "N%d" % i} for i in range(9)],
        banned=[{"name": "B%d" % i} for i in range(5)],
        thin=[{"name": "T%d" % i} for i in range(4)],
        interchangeable=[{"a": "A", "b": "B"}],
        scoring_errors=["boom", "bang"],
        scoring_batches=4,
        ranked={"kept": [{"name": "K", "attendees": "9,000", "gaps": ["format"]}],
                "over_cap": [{"name": "O%d" % i} for i in range(3)],
                "finished": [{"name": "F", "ends_on": "2026-01-01"}],
                "counts": {}})
    assert len(s["notes"]) >= 8, "expected most of the note kinds to fire"
    long = [n["head"] for n in s["notes"] if len(n["head"]) > REP.HEAD_CHARS]
    assert not long, "these heads are too long to scan: %r" % long


def test_a_pointer_detail_is_a_sentence_not_a_fragment():
    """"Cross-client check: not measured" followed by "first run for this
    client" read like two half-finished thoughts."""
    s = _summary(generic={"measured": False,
                          "why_not_measured": "first run for this client"})
    note = [n for n in s["notes"] if "Cross-client" in n["head"]][0]
    assert note["detail"] == "First run for this client."


def test_a_run_with_nothing_to_report_says_so_once():
    s = _summary()
    assert len(s["notes"]) >= 1
    clean = [n for n in s["notes"] if n["level"] == REP.LEVEL_OK]
    assert len(clean) <= 1


def test_a_spent_budget_is_its_own_pointer_not_folded_into_the_short_one():
    """"Short with searches to spare" and "short having spent everything it
    was given" are different findings, and only the second is worth spending
    more on."""
    s = _summary(shortfall=[
        {"category": R.CAT_VERTICAL_SUMMIT, "label": "Vertical summit",
         "status": "empty", "why": "Nothing found.", "budget_spent": True}])
    heads = [n["head"] for n in s["notes"]]
    assert any("used every search allowed" in h for h in heads)
    assert any("under the two-event quota" in h for h in heads)
