"""What the report says about scoring: no developer detail, no contradictions.

Covers the over-cap note (a recommended overflow and a worth-a-look overflow
are different claims), "could not be scored" against "set aside by your
rules", the scrub of developer detail from stored runs, and the note level
the page has no label for.
"""

from tracker import event_intel_report as REP
from tracker import event_intel_rubric as R

LIVE_SCORING = ("Max_tokens: Ran out of output budget before finishing "
                "(stop_reason=max_tokens). Raise max_tokens or lower max_uses..")
LIVE_AUDIT = ("no marquee event could be audited: for the one it was given, "
              "max_tokens: Ran out of output budget before finishing "
              "(stop_reason=max_tokens). Raise max_tokens or lower max_uses.")
# Built from its code point so this file never contains the character.
EM_DASH = chr(0x2014)
PLUMBING = ("max_tokens", "max_uses", "stop_reason", "Raise", "Max_tokens",
            "..", EM_DASH)


def _notes(**kw):
    base = dict(shortfall=[], audit={}, generic={}, candidates=[],
                scoring_errors=[], interchangeable=[], banned=[], thin=[],
                unscored=[])
    base.update(kw)
    return REP.notes(**base)


def _clean(text):
    for token in PLUMBING:
        assert token not in text, "%r leaked into %r" % (token, text)


# ── 6. developer detail ──────────────────────────────────────────────────

def test_the_two_live_strings_read_as_english():
    s = REP.reader_text(LIVE_SCORING)
    _clean(s)
    assert s.startswith("The answer ran past the length")
    a = REP.reader_text(LIVE_AUDIT)
    _clean(a)
    assert "for the one it was given, the answer ran past" in a


def test_ordinary_prose_is_left_alone():
    t = "This client skipped 3 of them, so it is ordered lower. Nothing else."
    assert REP.reader_text(t) == t


def test_em_dashes_become_commas():
    assert REP.reader_text("A %s B." % EM_DASH) == "A, B."


def test_doubled_full_stops_collapse():
    assert REP.reader_text("The search failed.. It was retried.") == \
        "The search failed. It was retried."


def test_a_stored_scoring_error_is_scrubbed_where_it_is_rendered():
    [fact] = [f for f in _notes(scoring_errors=[LIVE_SCORING])
              if f["head"].startswith("Scoring reported")]
    _clean(fact["detail"])
    assert "ran past the length" in fact["detail"]


def test_a_stored_audit_error_is_scrubbed_where_it_is_rendered():
    [fact] = [f for f in _notes(audit={"error": LIVE_AUDIT, "checked": 1})
              if "famous-event audit" in f["head"]]
    _clean(fact["detail"])


def _stored_run(notes, assumptions=()):
    return {"status": "complete", "created_at": "2026-09-01", "summary": {
        "notes": notes, "assumptions": list(assumptions),
        "unscored": [{"name": "X", "note": LIVE_SCORING}]}}


def test_an_old_stored_run_is_scrubbed_on_the_way_out():
    run = REP.present_run(_stored_run(
        [{"level": "gap", "head": "Scoring reported 1 error", "detail": LIVE_SCORING}],
        ["Scoring reported 1 error. " + LIVE_SCORING]), {}, [], {})
    s = run["summary"]
    _clean(s["notes"][0]["detail"])
    _clean(s["assumptions"][0])
    _clean(s["unscored"][0]["note"])


def test_the_warn_level_is_rendered_as_a_level_the_page_knows():
    """pipeline.py appends 'Research is incomplete' at level 'warn', which
    the page's NOTE_LABEL map has no entry for."""
    run = REP.present_run(_stored_run(
        [{"level": "warn", "head": "Research is incomplete", "detail": "Some checks."}]),
        {}, [], {})
    [n] = run["summary"]["notes"]
    assert n["level"] == REP.LEVEL_GAP
    assert n["level"] in REP.KNOWN_LEVELS


# ── 9. the over-cap note ─────────────────────────────────────────────────

def _row(name, rel, dm, eng):
    sc = R.score(rel, dm, eng)
    return {"name": name, R.DIM_RELEVANCE: rel, R.DIM_DM_ACCESS: dm,
            R.DIM_ENGAGEMENT: eng, "total": sc["total"], "tier": sc["tier"],
            "category": R.CAT_VERTICAL_SUMMIT, "starts_on": "2099-01-01"}


def test_an_option_past_the_cap_is_not_said_to_have_cleared_the_bar():
    """Reproduced: "2 further events cleared the bar but fell outside the
    maximum list length: Gamma Summit, Zeta Fair" when Zeta Fair scored 58."""
    ranked = R.rank([_row("Alpha", 36, 34, 16), _row("Gamma Summit", 34, 30, 12),
                     _row("Beta Fair", 30, 24, 10), _row("Zeta Fair", 28, 20, 10)],
                    cap=1)
    assert {c["name"] for c in ranked["over_cap"]} == {"Gamma Summit", "Zeta Fair"}
    facts = _notes(over_cap=ranked["over_cap"])
    cleared = [f for f in facts if "cleared the bar" in f["head"]]
    options = [f for f in facts if "worth a look" in f["head"]]
    assert len(cleared) == 1 and "Gamma Summit" in cleared[0]["detail"]
    assert "Zeta Fair" not in cleared[0]["detail"]
    assert len(options) == 1 and "Zeta Fair" in options[0]["detail"]
    for f in facts:
        assert len(f["head"]) <= REP.HEAD_CHARS, f["head"]


# ── 9. could not be scored, against set aside by the client's rules ──────

def test_a_rule_the_client_set_is_not_reported_as_a_scoring_failure():
    scorer_miss = {"name": "Ghost Expo", "unscored": True,
                   "scoring_note": "The scoring pass returned no result."}
    policy = {"name": "Sold Out Summit",
              "scoring_note": "This edition is sold out; access must be "
                              "resolved before recommending attendance."}
    facts = _notes(unscored=[scorer_miss, policy])
    gap = [f for f in facts if "could not be scored" in f["head"]]
    aside = [f for f in facts if "set aside" in f["head"]]
    assert len(gap) == 1 and "Ghost Expo" in gap[0]["detail"]
    assert "Sold Out Summit" not in gap[0]["detail"]
    assert gap[0]["level"] == REP.LEVEL_GAP
    assert len(aside) == 1 and "Sold Out Summit (This edition is sold out" in aside[0]["detail"]
    assert aside[0]["level"] == REP.LEVEL_NOTE


def test_a_bare_unscored_row_is_still_reported_as_the_gap():
    """A caller predating the split passes names only; the safe reading of an
    unexplained miss is a hole in the analysis."""
    facts = _notes(unscored=[{"name": "Phantom"}])
    assert any("could not be scored" in f["head"] for f in facts)


def test_an_explicit_set_aside_marker_is_honoured():
    facts = _notes(unscored=[{"name": "Excluded Expo", "set_aside": True,
                              "unscored": True, "scoring_note": "On the exclusion list."}])
    assert any("set aside" in f["head"] for f in facts)
    assert not any("could not be scored" in f["head"] for f in facts)


# ── 3. a duplicate promotion is named ────────────────────────────────────

def test_a_duplicate_promotion_is_named_in_the_report():
    facts = _notes(promoted={"promoted": [], "unconfirmed": [], "not_attempted": [],
                             "duplicates": [{"name": "PAY360 Awards and Conference",
                                             "same_as": "PAY360"}]},
                   audit={"comparison_only": True, "checked": 1})
    text = " ".join(f["detail"] for f in facts)
    assert "PAY360 Awards and Conference is PAY360" in text
