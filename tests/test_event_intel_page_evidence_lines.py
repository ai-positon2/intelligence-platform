"""Summary fields the pipeline stores and the page never showed: a follow-up
search that broke, which organizer evidence a card's dates stand on, a
restriction judged to belong to something else, and dates recovered from the
organizer's own listing. One restrained line each, executed in node."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_event_intel_event_view import page_script  # noqa: E402,F401
from test_event_intel_charts import _render, _recommend, _cand  # noqa: E402

RAW = ("organizer_structured_name_and_dates", "literal_name_and_dates_only",
       "organizer_title", "named_other_programme", "timed_session", "concession_pass",
       "hypothetical_question", "other_inventory", "a_future", "organizer_structured_data",
       "—")


def _clean(html):
    for r in RAW:
        assert r not in html, r


def _card(html, name):
    """The first card in the ranked list (every test here has one event)."""
    i = html.index('id="cand-1"')
    assert name in html[i:i + 2000]
    j = html.find('<div class="evi-dec">', i)
    return html[i:j]


@pytest.mark.parametrize("support,phrase", [
    ("organizer_structured_name_and_dates", "the organizer's event listing"),
    ("literal_name_and_dates_only", "the organizer's page text"),
    ("organizer_title_name_and_dates", "the organizer's page title"),
    ("organizer_title_name_and_self_referenced_dates", "the organizer's page title and text"),
    ("a_future_support_level", "the organizer's own pages"),
])
def test_each_card_says_what_its_dates_stand_on(page_script, support, phrase):
    html = _render(page_script, _recommend([_cand("Alpha", 90, "P1")], source_admission=[
        {"name": "Alpha", "support": support, "reasons": [], "checks": []}]))
    assert "Dates confirmed from: " + phrase.replace("'", "&#39;") + "." in _card(html, "Alpha")
    _clean(html)


def test_no_line_when_the_source_check_said_nothing(page_script):
    html = _render(page_script, _recommend([_cand("Alpha", 90, "P1")], source_admission=[
        {"name": "Alpha", "support": "unverified", "reasons": [], "checks": []}]))
    assert "Dates confirmed from" not in html


def test_recovered_dates_say_they_came_from_the_organizer_listing(page_script):
    html = _render(page_script, _recommend([_cand("Alpha", 90, "P1")],
                                           dates_from={"Alpha": "organizer_structured_data"}))
    assert "Dates confirmed from: the organizer&#39;s event listing." in _card(html, "Alpha")
    _clean(html)


def test_a_restriction_judged_to_be_another_programme_is_said_once(page_script):
    checks = [{"access_observations": [
        {"scope": "named_other_programme", "text": "x"},
        {"scope": "named_other_programme", "text": "y"},
        {"scope": "concession_pass", "text": "z"},
        {"scope": "unresolved_event_access", "text": "w"},
        {"scope": "a_future_scope", "text": "v"}]}]
    html = _render(page_script, _recommend([_cand("Alpha", 90, "P1")], source_admission=[
        {"name": "Alpha", "support": "literal_name_and_dates_only", "reasons": [], "checks": checks}]))
    card = _card(html, "Alpha")
    assert card.count("mentions restricted access") == 1
    assert ("applying to another named programme, a discounted or concession pass and "
            "something other than this event rather than to this event.") in card
    _clean(html)


def test_a_failed_follow_up_search_is_said_in_category_coverage(page_script):
    st = {"industry_flagship": {"status": "ok", "kept": 2, "label": "Industry flagship",
                                "later_searches": [{"status": "error", "added": 0,
          "detail": "The search for this category could not be completed: the "
                    "connection to the search service failed part-way."}]},
          "side_event": {"status": "ok", "kept": 2, "label": "Side event",
                         "later_searches": [{"status": "ok", "added": 1, "detail": ""}]}}
    html = _render(page_script, _recommend([_cand("Alpha", 90, "P1")], statuses=st))
    cov = html[html.index("Category coverage"):]
    assert ("A follow-up search for more events did not finish in Industry flagship, "
            "so it lists what the first search found. It stopped because the connection "
            "to the search service failed part-way.") in cov
    assert "Side event, so" not in cov


def test_no_follow_up_line_when_every_follow_up_finished(page_script):
    st = {"side_event": {"status": "ok", "kept": 2, "label": "Side event",
                         "later_searches": [{"status": "ok", "added": 0, "detail": ""}]}}
    html = _render(page_script, _recommend([_cand("Alpha", 90, "P1")], statuses=st))
    assert "A follow-up search" not in html


def test_an_unresolved_restriction_is_not_described_as_someone_else_s(page_script):
    """unresolved_event_access is the one scope that is NOT another thing;
    an event carrying it is kept out, and it must never read as cleared."""
    checks = [{"access_observations": [{"scope": "unresolved_event_access", "text": "w"}]}]
    html = _render(page_script, _recommend([_cand("Alpha", 90, "P1")], source_admission=[
        {"name": "Alpha", "support": "literal_name_and_dates_only", "reasons": [], "checks": checks}]))
    assert "mentions restricted access" not in html
