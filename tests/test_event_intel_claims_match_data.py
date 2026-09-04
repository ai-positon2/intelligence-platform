"""Claims this report makes, against the data underneath them.

Every defect in this file is the same shape: the report said something true of
what a stage INTENDED and false of what it actually produced. None of them is
a crash, none showed up in a log, and the suite was green through all of them.

  1. A grader that returns two of three sub-scores. `_clean` ran the value
     through `clamp_subscore`, which turns a missing field into 0, so
     `rubric.gaps_for` could no longer tell "we looked and it scores nothing"
     from "nobody scored this at all". A 40-point dimension went unmeasured
     and the row was stored at 48/110, tier P3, and cut from the ranked list.
     `rubric.read_subscore` exists precisely to keep those apart, and it was
     being consulted one step too late to matter.

  2. The famous-event audit's replacement. `promote_alternatives` inherits the
     category of the marquee event it stands in for, by looking that event up
     in a candidate list. The pipeline handed it `survivors`, and `apply_audit`
     removes cut events from `survivors`, so the lookup could not succeed in
     production ever. The promoted event was built with `category=None` and
     dropped by `normalise_candidate`, after a live confirmation search and a
     live scoring call had been spent on it, while the summary went on saying
     it had been "added to this list". Every existing test passed the cut event
     in by hand, which is the one thing production cannot do.

  3. A `partial` category search, described as a finished-but-short search in
     the summary and as an unfinished one in the coverage chart, in the same
     report. And the chart's verdict sentence claimed the categories it named
     "found none" while standing next to a bar reading 1 of 2.

  4. "1 event carry an unmeasured field."

  5. Two regional editions of one series. `merge` keeps "Money20/20 USA" and
     "Money20/20 Europe" apart on purpose, because they are two continents
     with two buyer sets. The scorer keyed its replies on the stripped name,
     which strips region words, so both editions landed in one slot and both
     rows were stored with whichever was graded second. The Las Vegas row
     rendered at relevance 12 with a description reading "Amsterdam".
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SECRET_KEY", "test-only")

from tracker import event_intel_audit as A  # noqa: E402
from tracker import event_intel_pipeline as P  # noqa: E402
from tracker import event_intel_report as REP  # noqa: E402
from tracker import event_intel_rubric as R  # noqa: E402
from tracker import event_intel_scorer as SC  # noqa: E402
from tracker import event_intel_store as ST  # noqa: E402
from test_event_intel_event_view import page_script  # noqa: E402,F401
from test_event_intel_charts import _recommend, _render  # noqa: E402

PROFILE = {"client_name": "Cadence Health", "classification": R.CLASS_B2B_TO_MARKETING,
           "buyer_roles": "VP Marketing", "verticals": "digital health",
           "window_months": 12}

_FULL = {"relevance": 33, "relevance_note": "dense",
         "dm_access": 30, "dm_access_note": "reachable",
         "engagement": 15, "engagement_note": "buying",
         "description": "A description.", "client_line": "A client line."}


def _reply(name="Payer Growth Forum", **over):
    d = dict(_FULL, name=name)
    d.update(over)
    return d


# ── 1. a sub-score nobody returned ───────────────────────────────────────

@pytest.mark.parametrize("dim", R.DIMENSIONS)
def test_a_dimension_the_grader_never_returned_is_not_stored_as_a_zero(dim):
    """The distinction rubric.read_subscore was written for, checked at the
    place that used to destroy it. clamp_subscore(None) is 0, and 0 is a
    verdict: gaps_for reads the stored value and reports nothing wrong."""
    raw = _reply()
    del raw[dim]
    got = SC._clean(raw)
    assert got[dim] is None, (
        "%s came back as %r, so the row now claims a grader scored it"
        % (dim, got[dim]))
    row = dict(got, category=R.CAT_VERTICAL_SUMMIT, website="https://x.example",
               attendees="500", starts_on="2027-05-04", format="in_person",
               sources=["https://x.example"])
    assert any("never scored" in g for g in R.gaps_for(row)), (
        "nothing in the row's gaps says a %d-point dimension went unmeasured: %s"
        % (R.DIMENSION_MAX[dim], R.gaps_for(row)))


@pytest.mark.parametrize("dim", R.DIMENSIONS)
def test_a_grader_that_actually_scored_zero_still_gets_a_zero(dim):
    """The other half, and the reason this is read_subscore and not a
    truthiness check. A dimension genuinely judged worthless is a verdict and
    has to survive as one."""
    got = SC._clean(_reply(**{dim: 0}))
    assert got[dim] == 0
    assert not [g for g in R.gaps_for(dict(got, **{dim + "_note": "x"}))
                if "never scored" in g]


@pytest.mark.parametrize("dim", R.DIMENSIONS)
def test_an_event_missing_a_dimension_is_unranked_and_names_the_dimension(dim, monkeypatch):
    """Not ranked on a partial total. A row scored 33/-/15 totals 48, which
    is under the floor, so presenting it as a score deletes the event from the
    client's list on the strength of a grader's truncated JSON."""
    raw = _reply()
    del raw[dim]
    monkeypatch.setattr(SC, "score_batch", lambda batch, profile: {
        "scores": {SC.score_key(raw["name"]): SC._clean(raw)}, "error": None})
    out = SC.score_all([{"name": raw["name"], "category": R.CAT_VERTICAL_SUMMIT}],
                       PROFILE)
    assert not out["scored"], "an event missing a whole dimension was ranked"
    assert len(out["unscored"]) == 1
    note = out["unscored"][0]["scoring_note"]
    assert R.DIMENSION_LABELS[dim].lower() in note, note
    assert str(R.DIMENSION_MAX[dim]) in note, (
        "the note does not say how many points went unmeasured: %s" % note)


# ── the grader's own wording of a name ───────────────────────────────────

def test_a_grader_that_rewords_a_name_still_scores_its_event():
    """merge() and _dedupe_proposals() already treat two names that contain
    one another as one event. An exact-key lookup here disagreed with them,
    and the disagreement sent an event with its own scores to the unscored
    bucket.

    The pair matters: "SaaStr" and "SaaStr Annual" reduce to the same key
    already, because name_key strips "annual", so they exercise nothing. This
    one adds a word the stripper does not know, which is the shape a grader
    actually produces when it answers with the edition or the city."""
    assert SC.score_key("GTM Unbound") != SC.score_key("GTM Unbound Festival"), (
        "the fixture is wrong: these two already share a key, so the loose "
        "path is never reached")
    scores = {SC.score_key("GTM Unbound Festival"):
              SC._clean(_reply("GTM Unbound Festival"))}
    assert SC._lookup(scores, "GTM Unbound") is not None, (
        "an event the grader named slightly differently was reported unscored")


def test_two_regional_editions_do_not_share_one_set_of_scores():
    """The collision this file was opened by. merge() deliberately keeps
    "Money20/20 USA" and "Money20/20 Europe" as two events, because they are
    two continents with two buyer sets and merging them deletes one from the
    client's year. The scorer keyed its replies on the stripped name alone,
    which is region-blind, so the second edition graded overwrote the first
    and BOTH rows were stored with one edition's scores, notes and
    description: the Las Vegas row rendered at relevance 12 with a
    description reading "Amsterdam"."""
    us = SC._clean(_reply("Money20/20 USA", relevance=38, description="Las Vegas."))
    eu = SC._clean(_reply("Money20/20 Europe", relevance=12, description="Amsterdam."))
    scores = {SC.score_key(us["name"]): us, SC.score_key(eu["name"]): eu}
    assert len(scores) == 2, "the two editions still share one key"
    assert SC._lookup(scores, "Money20/20 USA")["description"] == "Las Vegas."
    assert SC._lookup(scores, "Money20/20 Europe")["description"] == "Amsterdam."


def test_a_reply_that_dropped_the_region_is_refused_rather_than_guessed():
    """One event wearing another edition's sub-scores is worse than the miss
    the loose match fixes, so it is only taken when exactly one candidate
    fits. A grader that answers "Money20/20" while both editions are in the
    dict fits both, and the event is reported unscored instead."""
    scores = {SC.score_key("Money20/20 USA"): SC._clean(_reply("Money20/20 USA")),
              SC.score_key("Money20/20 Europe"): SC._clean(_reply("Money20/20 Europe"))}
    assert SC._lookup(scores, "Money20/20") is None


# ── 2. the audit's replacement ───────────────────────────────────────────

def _cut_audit():
    key = A.name_key("MarTech Conference")
    rec = {"verdict": A.VERDICT_CUT, "alternative": "INBOUND",
           "alternative_website": "https://inbound.example",
           "alternative_note": "It has a real exhibit floor.",
           "why": "Fully online.", "name": "MarTech Conference"}
    return {"verdicts": {key: rec}, "cut": [dict(rec)], "kept": [],
            "checked": 1, "error": None}


def _resolver(name, year_hint=None):
    return {"ok": True, "confidence": "high", "pages": [
        {"url": "https://inbound.example/exhibitors"}], "event": {
        "name": "INBOUND", "website": "https://inbound.example",
        "starts_on": "2027-09-01", "ends_on": "2027-09-03",
        "organizer": "HubSpot", "location": "Boston, MA",
        "stated_size": "11,000 attendees", "format": "in_person",
        "audience_note": "Marketing leaders", "confidence": "high"}}


def _survivors_and_pre_audit():
    """The two lists the pipeline holds at the moment it promotes."""
    pre = [{"name": "MarTech Conference", "famous": True,
            "category": R.CAT_INDUSTRY_FLAGSHIP, "city": "Online"},
           {"name": "Local Growth Meetup", "famous": False,
            "category": R.CAT_SIDE_EVENT}]
    audit = _cut_audit()
    survivors = A.apply_audit(pre, audit)
    assert [c["name"] for c in survivors] == ["Local Growth Meetup"], (
        "the fixture is wrong: apply_audit is supposed to remove the cut event")
    return audit, survivors, pre


def test_a_promotion_takes_its_category_from_the_pre_audit_list():
    """Called exactly as the pipeline calls it. The cut event is not in
    `survivors` by construction, so the category can only come from the list
    that still holds it."""
    audit, survivors, pre = _survivors_and_pre_audit()
    out = A.promote_alternatives(audit, survivors, resolver=_resolver,
                                 replaced_from=pre)
    assert out["promoted"], (
        "the replacement was refused: %s" % out["unconfirmed"])
    assert out["promoted"][0]["category"] == R.CAT_INDUSTRY_FLAGSHIP


def test_a_promoted_event_survives_the_store():
    """The step that used to eat it. normalise_candidate returns None for a
    row with no category, which is a silent drop: the row is logged and the
    summary carries on saying the event was added to the list."""
    audit, survivors, pre = _survivors_and_pre_audit()
    got = A.promote_alternatives(audit, survivors, resolver=_resolver,
                                 replaced_from=pre)["promoted"][0]
    row = ST.normalise_candidate(dict(got, **_FULL, name=got["name"]))
    assert row is not None, "the promoted event is dropped by the store"
    assert row["audit_verdict"] == A.VERDICT_PROMOTED


def test_a_promotion_with_no_category_slot_is_named_rather_than_dropped():
    """The backstop. If the replaced event cannot be found at all, the
    promotion must not be built half-formed and handed to a store that
    discards it without telling anyone."""
    audit, survivors, _pre = _survivors_and_pre_audit()
    out = A.promote_alternatives(audit, survivors, resolver=_resolver,
                                 replaced_from=[])
    assert not out["promoted"]
    assert out["unconfirmed"] and out["unconfirmed"][0]["name"] == "INBOUND"
    assert "categor" in out["unconfirmed"][0]["why"]


def test_the_pipeline_hands_promotion_a_list_that_still_holds_the_cut_event(monkeypatch):
    """The wiring, which is where this lived. promote_alternatives was correct
    and the pipeline called it with the one list that could not work, and the
    existing wiring test stubbed the whole function out so it never saw the
    arguments."""
    seen = {}

    def spy(audit, candidates, **kw):
        seen["candidates"] = [c.get("name") for c in candidates]
        seen["replaced_from"] = [c.get("name")
                                 for c in (kw.get("replaced_from") or [])]
        return {"promoted": [], "unconfirmed": [], "not_attempted": [],
                "considered": 0}

    pre = [{"name": "MarTech Conference", "famous": True,
            "category": R.CAT_INDUSTRY_FLAGSHIP}]
    monkeypatch.setattr(P.event_intel_audit, "promote_alternatives", spy)
    monkeypatch.setattr(P.event_intel_discover, "discover", lambda profile: {
        "candidates": pre, "shortfall": [], "statuses": {},
        "categories_failed": 0, "found": 1, "by_category": {},
        "categories_searched": 6})
    monkeypatch.setattr(P.event_intel_audit, "audit_famous",
                        lambda c, p: _cut_audit())
    monkeypatch.setattr(P.event_intel_scorer, "score_all", lambda s, p: {
        "scored": [], "unscored": [], "errors": [], "batches": 0})
    monkeypatch.setattr(P.store, "update_run", lambda *a, **k: None)
    monkeypatch.setattr(P.store, "save_candidates", lambda *a, **k: 0)
    monkeypatch.setattr(P.store, "get_candidates", lambda *a, **k: [])
    monkeypatch.setattr(P.store, "prior_candidate_names", lambda *a, **k: [])
    monkeypatch.setattr(P.store, "get_outcomes", lambda *a, **k: {
        "counts": {}, "ruled_on": 0, "note": "", "by_name": {}})

    P._run_recommend(1, "e@x.com", dict(PROFILE))
    assert "MarTech Conference" not in seen["candidates"], (
        "the fixture is wrong: the cut event should be gone from survivors")
    assert "MarTech Conference" in seen["replaced_from"], (
        "the pipeline gave promotion no list containing the event being "
        "replaced, so no promoted event can ever carry a category")


# ── 3. one category, two descriptions ────────────────────────────────────

def _notes(**kw):
    base = dict(shortfall=[], audit={}, generic={}, candidates=[],
                scoring_errors=[], interchangeable=[], banned=[], thin=[],
                unscored=[])
    base.update(kw)
    return REP.notes(**base)


def _heads(facts):
    return " | ".join(f["head"] for f in facts)


def test_a_partial_category_search_is_reported_as_unfinished():
    """The page's coverage chart reads `partial` as a gap and says so. The
    summary folded it in with the categories that finished and came up short,
    so one report described one category both ways."""
    sf = [{"category": R.CAT_REGIONAL_FLAGSHIP, "label": "Regional flagship",
           "found": 1, "quota": 2, "short_by": 1, "status": "partial",
           "why": "The finder used every search it was given."}]
    heads = _heads(_notes(shortfall=sf))
    assert "did not finish" in heads, heads
    assert "under the two-event quota" not in heads, (
        "a search that did not finish is still being reported as one that "
        "finished and found little: %s" % heads)


def test_a_category_that_finished_short_is_still_reported_as_short():
    sf = [{"category": R.CAT_EMERGING, "label": "Emerging event", "found": 1,
           "quota": 2, "short_by": 1, "status": "empty",
           "why": "Only one such event serves this buyer."}]
    heads = _heads(_notes(shortfall=sf))
    assert "under the two-event quota" in heads, heads
    assert "did not finish" not in heads, heads


def test_a_partial_search_is_a_gap_and_not_merely_thin():
    sf = [{"category": R.CAT_REGIONAL_FLAGSHIP, "label": "Regional flagship",
           "found": 1, "quota": 2, "short_by": 1, "status": "partial",
           "why": "Cut short."}]
    levels = {f["level"] for f in _notes(shortfall=sf)
              if "did not finish" in f["head"]}
    assert levels == {REP.LEVEL_GAP}, levels


# ── 4. the sentence itself ───────────────────────────────────────────────

@pytest.mark.parametrize("n,want", [(1, "1 event carries"), (2, "2 events carry")])
def test_the_unmeasured_field_head_agrees_with_its_own_count(n, want):
    """It read "1 event carry an unmeasured field" in the report a client
    opens, and careful copy is most of what this agent is selling."""
    cands = [{"name": "E%d" % i, "gaps": ["something"]} for i in range(n)]
    heads = _heads(_notes(candidates=cands))
    assert want in heads, heads


def test_the_coverage_verdict_does_not_say_a_category_found_nothing(page_script):
    """Rendered, because the sentence is written in the page script. It said
    the gap categories were "missing a kind of event rather than having found
    none" while one of them sat next to a bar reading 1 of 2."""
    run = _recommend([], shortfall=[
        {"category": R.CAT_REGIONAL_FLAGSHIP, "label": "Regional flagship",
         "found": 1, "quota": 2, "short_by": 1, "status": "partial",
         "why": "The search was cut short."}])
    html = _render(page_script, run)
    assert "Regional flagship" in html, "the coverage chart did not render"
    assert "having found none" not in html, (
        "the report tells a client this category found nothing, beside a bar "
        "saying it found one")
    assert "did not finish" in html
