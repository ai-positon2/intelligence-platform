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


def test_the_methodology_is_the_rubric_as_applied_to_this_client():
    m = _summary()["methodology"]
    assert "40" in m and "110" in m
    assert "exhibitor booths" in m
    assert "never an input to a score" in m


def test_the_client_profile_element_reads_as_a_sentence():
    cp = _summary()["client_profile"]
    assert "Northwind" in cp and "fintech" in cp and "VP Marketing" in cp
    assert cp.endswith(".")


def test_assumptions_separate_a_failed_category_from_an_empty_one():
    s = _summary(shortfall=[
        {"category": R.CAT_FREE_VENDOR, "label": "Free vendor conference",
         "status": "empty", "why": "No vendor runs city events in this vertical."},
        {"category": R.CAT_SIDE_EVENT, "label": "Side event",
         "status": "error", "why": "transport: HTTP 503"}])
    text = " ".join(s["assumptions"])
    assert "hole in the analysis" in text
    assert "503" in text
    assert "under the two-event quota" in text


def test_assumptions_say_when_the_cross_client_check_could_not_run():
    s = _summary(generic={"measured": False,
                          "why_not_measured": "no earlier client to compare against"})
    assert any("no earlier client" in a for a in s["assumptions"])


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
    assert "No case was written" in top[0]["case"]
    assert top[0]["when"] == "dates not announced"


# ── the whole play ────────────────────────────────────────────────────────

class _FakeStore:
    """Just enough of the store to run the pipeline without Postgres, keeping
    the real normalise_candidate so totals are still recomputed from
    sub-scores rather than trusted."""

    def __init__(self):
        self.runs, self.rows = {}, []

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


def _wire(monkeypatch, fake):
    for name in ("update_run", "save_candidates", "get_candidates",
                 "prior_candidate_names"):
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
                       "label": "Free vendor conference", "why": "HTTP 503"}],
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
