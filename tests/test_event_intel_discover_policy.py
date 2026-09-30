"""A finished edition is flagged and let through, so rank() can file it.

The policy used to refuse any edition that ended before today, before
ranking, as "outside the requested date window". rank() files an edition
that is over under `finished` ("Already over" in the report), but it never
saw one: the section could not be populated, and a real edition that had
just happened was shown under "Not scored" as if the search had a gap.
"""

from datetime import date, timedelta

from tracker import event_intel_discover as D
from tracker import event_intel_rubric as R
from tracker.event_intel_policy import eligibility

PROFILE = dict(client_name="C", classification="b2b_to_marketing",
               geo_scope="USA only", window_months=12)


def _ev(start, end, **over):
    row = dict(name="Forum", starts_on=start.isoformat(), ends_on=end.isoformat(),
               country="USA", city="Boston", confidence="high",
               website="https://event.example", sources=["https://event.example/a"],
               category="vertical_summit", total=80, tier="P1", relevance=32)
    row.update(over)
    return row


def test_a_finished_edition_is_flagged_not_refused():
    ev = _ev(date.today() - timedelta(days=12), date.today() - timedelta(days=10))
    assert eligibility(ev, PROFILE) == []
    assert ev["finished"] is True


def test_the_flagged_edition_lands_in_already_over():
    ev = _ev(date.today() - timedelta(days=12), date.today() - timedelta(days=10))
    assert eligibility(ev, PROFILE) == []
    ranked = R.rank([ev])
    assert [c["name"] for c in ranked["finished"]] == ["Forum"]
    assert ranked["kept"] == []


def test_an_edition_on_its_final_day_is_not_finished():
    ev = _ev(date.today() - timedelta(days=2), date.today())
    assert eligibility(ev, PROFILE) == []
    assert "finished" not in ev


def test_a_future_edition_outside_the_window_is_still_refused():
    ev = _ev(date.today() + timedelta(days=800), date.today() + timedelta(days=801))
    assert any("outside the requested date window" in r for r in eligibility(ev, PROFILE))
    assert "finished" not in ev


def test_an_impossible_range_is_still_refused():
    ev = _ev(date.today() - timedelta(days=5), date.today() - timedelta(days=9))
    assert any("invalid dates" in r for r in eligibility(ev, PROFILE))


def test_a_finished_edition_still_needs_everything_else():
    ev = _ev(date.today() - timedelta(days=12), date.today() - timedelta(days=10),
             confidence="low")
    assert eligibility(ev, PROFILE) == ["The event identity has insufficient confidence."]
