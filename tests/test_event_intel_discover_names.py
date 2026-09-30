"""What counts as one event: names_match, same_event, merge and the
proposal dedupe.

The defect this file was opened by: name_key strips the show words, so
"Fintech Summit" reduced to the single topic word "fintech", and whole-token
containment then matched it inside every other fintech event. A force-exclude
of "Fintech Conference" dropped Fintech Meetup, FinTech Connect and FinTech
Festival Asia; a commitment to "Fintech Summit" marked two unrelated events as
paid for; and a finder that named three fintech events kept one.
"""

import pytest

from tracker import event_intel_discover as D
from tracker import event_intel_rubric as R


@pytest.mark.parametrize("a,b", [
    ("Fintech Summit", "Fintech Meetup"),
    ("Fintech Conference", "FinTech Connect"),
    ("Fintech Conference", "FinTech Festival Asia"),
    ("AI Expo", "The AI Summit New York"),
    ("Fintech Summit", "Fintech Conference"),
    ("Payments Summit", "Payments Expo"),
])
def test_one_topic_word_no_longer_matches_every_event_in_the_field(a, b):
    assert not D.names_match(a, b)
    assert not D.names_match(b, a)


@pytest.mark.parametrize("a,b", [
    ("SaaStr", "SaaStr Annual"),
    ("SaaStr Annual 2026", "SaaStr Annual"),
    ("MarTech Summit", "MarTech Summit Europe"),
    ("Money20/20", "Money20/20 USA 2026"),
    ("Web Summit", "Web Summit Lisbon"),
    ("Fintech Summit", "Fintech Summit 2026"),
    ("AI Summit", "The AI Summit London"),
    ("Fintech Meetup", "Fintech Meetup Las Vegas"),
    ("GTM Unbound", "GTM Unbound Festival"),
])
def test_the_matches_that_were_right_still_match(a, b):
    assert D.names_match(a, b)


@pytest.mark.parametrize("a,b", [
    ("Money20/20 USA", "Money20/20 Europe"),
    ("Money20/20 Europe", "Money20/20 Asia"),
    ("FinovateFall", "FinovateSpring"),
    ("The AI Summit London", "The AI Summit New York"),
])
def test_distinct_editions_stay_distinct(a, b):
    assert not D.names_match(a, b)


def test_two_hosts_split_a_topic_word_name_from_its_spin_off():
    """"Web Summit" and "Web Summit Vancouver" by name alone cannot be told
    from "Web Summit Lisbon", the flagship's home city. Their websites can."""
    assert D.names_match("Web Summit", "Web Summit Vancouver")
    assert not D.names_match("Web Summit", "Web Summit Vancouver",
                             "https://websummit.com/",
                             "https://vancouver.websummit.com/")
    # A region word is already handled by region compatibility, so two hosts
    # alone do not split a name from its regional form.
    assert D.names_match("AI Summit", "AI Summit Europe",
                         "https://a.example", "https://b.example")


def _e(name, website=None, cat=R.CAT_INDUSTRY_FLAGSHIP, **kw):
    return dict({"name": name, "website": website, "category": cat}, **kw)


def test_an_exclusion_no_longer_empties_the_field():
    by = {R.CAT_INDUSTRY_FLAGSHIP: [_e("Fintech Meetup"), _e("FinTech Connect"),
                                    _e("FinTech Festival Asia"),
                                    _e("Fintech Conference 2026")]}
    out = D.merge(by, force_exclude="Fintech Conference")
    assert [e["name"] for e in out] == ["Fintech Meetup", "FinTech Connect",
                                        "FinTech Festival Asia"]


def test_a_commitment_marks_only_the_event_it_names():
    by = {R.CAT_INDUSTRY_FLAGSHIP: [_e("Fintech Summit 2026"), _e("Fintech Meetup"),
                                    _e("FinTech Connect")]}
    flags = {e["name"]: e["committed"]
             for e in D.merge(by, force_include="Fintech Summit")}
    assert flags == {"Fintech Summit 2026": True, "Fintech Meetup": False,
                     "FinTech Connect": False}


def test_the_proposal_dedupe_keeps_three_fintech_events():
    props = [{"name": n, "website": None, "why": "w"}
             for n in ("Fintech Summit", "Fintech Meetup", "FinTech Connect")]
    assert [p["name"] for p in D._dedupe_proposals(props)] == [
        "Fintech Summit", "Fintech Meetup", "FinTech Connect"]


def test_the_proposal_dedupe_still_drops_one_event_named_twice():
    props = [{"name": "SaaStr Annual", "website": "https://saastr.example", "why": "w"},
             {"name": "SaaStr", "website": None, "why": "w"},
             {"name": "Other Name", "website": "https://saastr.example/", "why": "w"}]
    assert [p["name"] for p in D._dedupe_proposals(props)] == ["SaaStr Annual"]


# ── dates that drift by a day ─────────────────────────────────────────────

def test_a_one_day_drift_on_the_same_site_is_one_event():
    by = {R.CAT_INDUSTRY_FLAGSHIP: [
        _e("Money20/20 USA", "https://us.money2020.example",
           starts_on="2026-10-25", ends_on="2026-10-28"),
        _e("Money20/20 USA 2026", "https://us.money2020.example/",
           starts_on="2026-10-26", ends_on="2026-10-29")]}
    assert [e["name"] for e in D.merge(by)] == ["Money20/20 USA"]


def test_near_dates_merge_by_name_too():
    by = {R.CAT_INDUSTRY_FLAGSHIP: [
        _e("Money20/20 USA", starts_on="2026-10-25"),
        _e("Money20/20 USA 2026", starts_on="2026-10-28")]}
    assert len(D.merge(by)) == 1


@pytest.mark.parametrize("second", ["2027-10-25", "2026-11-05"])
def test_editions_well_apart_stay_two_even_on_one_site(second):
    by = {R.CAT_INDUSTRY_FLAGSHIP: [
        _e("Money20/20 USA", "https://us.money2020.example", starts_on="2026-10-25"),
        _e("Money20/20 USA", "https://us.money2020.example", starts_on=second)]}
    assert len(D.merge(by)) == 2


def test_two_regions_on_one_site_stay_two():
    by = {R.CAT_INDUSTRY_FLAGSHIP: [
        _e("Money20/20 USA", "https://money2020.example"),
        _e("Money20/20 Europe", "https://money2020.example")]}
    assert len(D.merge(by)) == 2
