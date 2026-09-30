"""The relevance-cut policy as the report renders it, and the outcome reorder.

rank() is tested in test_event_intel_rubric.py. These pin the two report
functions that label or reorder what rank() decided: that a row cut for its
audience is labelled as such, that the committed label reads the bar from
RANK_FLOOR rather than a typed 70, and that a client's history reorders
within a tier and never across one.
"""

from tracker import event_intel_report as REP
from tracker import event_intel_rubric as R


def _row(name, rel, dm, eng, **over):
    sc = R.score(rel, dm, eng)
    row = {"name": name, R.DIM_RELEVANCE: rel, R.DIM_DM_ACCESS: dm,
           R.DIM_ENGAGEMENT: eng, "total": sc["total"], "tier": sc["tier"],
           "category": R.CAT_VERTICAL_SUMMIT, "starts_on": "2099-06-01",
           "city": "Boston"}
    row.update(over)
    return row


def _snapshot(rows, cap=15):
    return REP.selection_snapshot(R.rank(rows, cap=cap), {})


def test_an_event_cut_for_its_audience_is_labelled_for_its_audience():
    snap = _snapshot([_row("Busy wrong floor", 14, 38, 20),
                      _row("Just weak", 30, 6, 4)])
    labels = {r["name"]: r["status_label"] for r in snap["excluded"]}
    assert labels["Busy wrong floor"] == REP.OFF_AUDIENCE_LABEL
    assert labels["Just weak"] == "Excluded, below the bar"


def test_the_export_labels_agree_with_the_snapshot():
    rows = [_row("Busy wrong floor", 14, 38, 20), _row("Good", 34, 34, 16)]
    labels = REP.status_labels(rows)
    snap = _snapshot(rows)
    from tracker.event_intel_discover import name_key
    for bucket in ("kept", "excluded"):
        for r in snap[bucket]:
            assert labels[name_key(r["name"])] == r["status_label"]


def test_the_committed_label_reads_the_bar_from_rank_floor(monkeypatch):
    """report.py compared against a typed 70. Move the bar and the label
    has to move with it."""
    monkeypatch.setattr(R, "RANK_FLOOR", 80)
    row = _row("Paid for", 30, 30, 15, committed=True)   # 75
    snap = _snapshot([row])
    assert snap["kept"][0]["status_label"] == REP.COMMITTED_BELOW_LABEL


def test_a_committed_event_with_the_wrong_audience_says_so():
    snap = _snapshot([_row("Paid, wrong crowd", 12, 38, 20, committed=True)])
    assert snap["kept"][0]["status_label"] == REP.COMMITTED_OFF_AUDIENCE_LABEL


# ── the outcome nudge stays inside a tier ────────────────────────────────

def _k(name, total, category):
    return {"name": name, "total": total, "tier": R.tier_for(total),
            "category": category}


def test_the_nudge_never_lifts_a_p2_above_a_p1():
    """Reproduced: a P2 at 76+5 sorted above a P1 at 82-5, and the P1 fell
    out of the top five, which is the first five rows of `kept`."""
    kept = [_k("P1 disliked", 82, R.CAT_VERTICAL_SUMMIT),
            _k("P2 liked", 76, R.CAT_SIDE_EVENT)]
    pattern = {"by_category": {
        R.CAT_VERTICAL_SUMMIT: {"decisions": 4, "skipped": 4, "went_or_going": 0},
        R.CAT_SIDE_EVENT: {"decisions": 4, "skipped": 0, "went_or_going": 4}},
        "by_format": {}}
    out = REP.apply_outcome_pattern(kept, pattern)
    assert [c["name"] for c in out] == ["P1 disliked", "P2 liked"]
    assert [c["tier"] for c in out] == [R.TIER_P1, R.TIER_P2]


def test_the_nudge_still_reorders_within_a_tier():
    kept = [_k("A", 78, R.CAT_VERTICAL_SUMMIT), _k("B", 74, R.CAT_SIDE_EVENT)]
    pattern = {"by_category": {
        R.CAT_VERTICAL_SUMMIT: {"decisions": 3, "skipped": 3, "went_or_going": 0}},
        "by_format": {}}
    out = REP.apply_outcome_pattern(kept, pattern)
    assert [c["name"] for c in out] == ["B", "A"]


def test_the_top_five_keeps_every_p1_ahead_of_any_p2():
    kept = ([_k("P1-%d" % i, 81, R.CAT_VERTICAL_SUMMIT) for i in range(5)]
            + [_k("P2-liked", 79, R.CAT_SIDE_EVENT)])
    pattern = {"by_category": {
        R.CAT_VERTICAL_SUMMIT: {"decisions": 5, "skipped": 5, "went_or_going": 0},
        R.CAT_SIDE_EVENT: {"decisions": 5, "skipped": 0, "went_or_going": 5}},
        "by_format": {}}
    top = REP.top_five(REP.apply_outcome_pattern(kept, pattern))
    assert all(t["tier"] == R.TIER_P1 for t in top)
