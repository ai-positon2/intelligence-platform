"""What the report is allowed to say, and in whose words.

Every string in this agent's report reaches a paying client, and three
different kinds of writing were arriving there unedited:

  * machine tokens. A category key printed as ``famous_flagship`` in a card's
    meta line, and as ``virtual`` in the slot where a category label belongs,
    one line above a chip reading "In person".
  * developer detail. ``claude_websearch`` writes an error detail for the log,
    and one kind of it ends "Raise max_tokens or lower max_uses". Callers
    interpolated it into report prose, so a live report told a client to raise
    a token limit.
  * the model's own narration. ``note`` is a free-prose field, and a live run
    filled 600 characters of it with "i attempted to research emerging
    (1st-3rd edition) b2b marketing/growth/sales events for position2 ...
    however, the web_search tool hit a hard per-turn call limit", every
    character of which was printed under a heading, cut off mid-word.

None of the three is a rendering bug and none is fixed by making the box
bigger. They are all the same mistake: text written for one audience shown to
another. These tests hold the line at each point where such a string enters
the report.
"""

import json
import os
import re
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SECRET_KEY", "test-only")

from tracker import claude_websearch as W  # noqa: E402
from tracker import event_intel_discover as D  # noqa: E402
from tracker import event_intel_report as REP  # noqa: E402
from test_event_intel_event_view import _SHIM, _IIFE_CLOSE, page_script  # noqa: E402,F401
from test_event_intel_charts import _cand, _recommend, _render  # noqa: E402


# ── the error clause ──────────────────────────────────────────────────────

def _error_kinds():
    """Every failure kind the search wrapper can put in an error dict."""
    return sorted(v for k, v in vars(W).items()
                  if k.startswith("ERR_") and isinstance(v, str))


def test_every_error_kind_has_a_sentence_written_for_a_reader():
    """Exhaustive on purpose. A kind added later with no clause of its own
    would fall through to the fallback silently, and the fallback says nothing
    about what went wrong."""
    missing = [k for k in _error_kinds() if k not in W.READER_REASON]
    assert not missing, "no reader clause for: %s" % ", ".join(missing)


def test_the_reader_clause_is_never_the_developer_detail():
    detail = ("Ran out of output budget before finishing "
              "(stop_reason=max_tokens). Raise max_tokens or lower max_uses.")
    said = W.reader_reason({"kind": W.ERR_MAX_TOKENS, "detail": detail})
    assert "max_tokens" not in said
    assert "max_uses" not in said
    assert "stop_reason" not in said
    assert said and said[0].islower(), "it is a clause, dropped into a sentence"


def test_an_unknown_kind_falls_back_to_words_and_not_to_the_detail():
    """The fallback matters more than the mapping. Falling back to `detail`
    is the leak this whole mechanism exists to close, so an unrecognised kind
    has to be handled by saying less, never by saying more."""
    said = W.reader_reason({"kind": "some_kind_invented_tomorrow",
                            "detail": "HTTP 500: internal thing"})
    assert said
    assert "500" not in said and "HTTP" not in said
    assert W.reader_reason(None)
    assert W.reader_reason({})


@pytest.mark.parametrize("kind", _error_kinds())
def test_no_error_kind_leaks_its_token_or_its_detail_into_the_report(monkeypatch, kind):
    """Driven through the real category search, once per kind, because the
    leak was in the caller and not in the wrapper. A test on the mapping alone
    passes while the caller keeps printing `detail`."""
    from test_event_intel_discover import _stages, PROFILE
    import tracker.event_intel_rubric as R
    detail = "SENTINEL-9137 raise max_tokens or lower max_uses"
    _stages(monkeypatch, find_error={"kind": kind, "detail": detail})
    r = D.search_category(R.CAT_SIDE_EVENT, PROFILE)
    said = r["detail"]
    assert "SENTINEL-9137" not in said, "the developer detail reached the report"
    assert kind not in said, "the machine token reached the report"
    assert said[0].isupper() and said.endswith(".")


# ── the model's note ──────────────────────────────────────────────────────

REAL_NOTE = (
    "i attempted to research emerging (1st-3rd edition) b2b marketing/growth/"
    "sales events for position2 \u2014 candidates i intended to verify "
    "included "
    "newer community-driven events such as mops-apalooza (marketing operations), "
    "pavilion's gtm-focused gatherings, clay's gtm summit, 6sense's breakthrough "
    "conference, and exit five's b2b marketing events, all of which have "
    "plausible 1-3 year histories in the demand-gen/revops space where position2 "
    "sells. however, the web_search tool hit a hard per-turn call limit partway "
    "through this research session and returned 'server tool use limit exceeded' "
    "on every su")


def test_the_note_that_shipped_to_a_client_is_now_dropped_entirely():
    """Verbatim from the run the complaint was about. Every sentence in it is
    about this run's plumbing or about what the model meant to do, so there is
    nothing in it to keep: an emptied note falls back to a sentence of the
    module's own, which is always true."""
    assert D._reader_note(REAL_NOTE) == ""


def test_a_note_about_the_market_survives_whole():
    said = ("Every city day on this calendar is aimed at practitioners rather "
            "than budget owners.")
    assert D._reader_note(said) == said


def test_a_note_is_split_sentence_by_sentence_not_taken_or_left():
    """The useful half of a mixed note is kept. Dropping the whole thing
    because one sentence mentions a tool would throw away the only line about
    the client's market."""
    out = D._reader_note("Only one dinner series published a date. "
                         "I could not confirm the rest.")
    assert out == "Only one dinner series published a date."


def test_a_long_note_is_cut_at_a_word_and_marked():
    """A report that ends a paragraph on "on every su" is how this started."""
    long = ("Regional buyers in this market gather at " + "chains of small "
            "practitioner meetups " * 20 + "and nowhere else.")
    out = D._reader_note(long)
    assert len(out) <= D.NOTE_CHARS
    assert out.endswith("…")
    assert not out.rstrip("…").endswith(" ")
    # The cut lands between words: the character before the ellipsis is the
    # end of a word the reader can read.
    assert re.search(r"\w…$", out)


def test_the_note_carries_no_em_dash():
    out = D._reader_note("Buyers gather in two places \u2014 trade shows "
                         "and vendor days.")
    # Escaped rather than typed: the character is banned from this
    # codebase's copy, and the gate that enforces that is a grep.
    assert "\u2014" not in out and "\u2013" not in out
    assert out.startswith("Buyers gather in two places, trade shows")


def test_the_note_is_punctuated_and_capitalised():
    assert D._reader_note("nothing here fits this buyer") == \
        "Nothing here fits this buyer."


def test_an_absent_note_stays_absent():
    for raw in (None, "", "   ", 0):
        assert D._reader_note(raw) == ""


def test_the_stored_note_is_the_cleaned_one_and_not_the_reply(monkeypatch):
    """The cleaner has to be reached, not merely present. A unit test on
    `_reader_note` passes in full against a `propose_category` that stores
    the raw 600 characters, which is how it shipped in the first place."""
    from test_event_intel_discover import _stages, _find_reply, _ONE, PROFILE
    import tracker.event_intel_rubric as R
    _stages(monkeypatch, find=_find_reply(_ONE, note=REAL_NOTE, complete=True))
    r = D.propose_category(R.CAT_EMERGING, PROFILE)
    assert r["note"] == "", r["note"]
    assert "web_search" not in r["note"]
    assert r["proposals"], "the candidates were dropped along with the note"


def test_a_market_note_survives_the_round_trip(monkeypatch):
    """The other half. A cleaner wired in as `note = ""` would pass the test
    above and silently delete every usable note in the product."""
    from test_event_intel_discover import _stages, _find_reply, _ONE, PROFILE
    import tracker.event_intel_rubric as R
    said = "Only two vendors run city days for this buyer."
    _stages(monkeypatch, find=_find_reply(_ONE, note=said, complete=True))
    r = D.propose_category(R.CAT_EMERGING, PROFILE)
    assert r["note"] == said


def test_the_prompt_asks_for_a_market_finding_rather_than_a_diary():
    """The filter is a backstop. The fix is asking for the right thing, and
    the prompt has to keep saying so or every note arrives needing surgery."""
    system = D.find_system("emerging", {"client_name": "X",
                                        "classification": "b2b_saas"})
    low = system.lower()
    assert "printed in the client's report" in low
    assert "do not narrate" in low
    assert "return an empty string" in low


# ── the sentence that replaces a dropped note ─────────────────────────────

def _shortfall_why(found, note, monkeypatch):
    """What the report prints under a short category, from the real loop.

    Driven through `discover` rather than reproduced here. An earlier version
    of this helper rebuilt the fallback inline and passed against a mutant
    that deleted the real one, which is the exact failure the file it sits in
    was opened to prevent.
    """
    import tracker.event_intel_rubric as R
    target = R.CAT_EMERGING
    events = [{"name": "E-%d" % i, "category": target} for i in range(found)]

    def fake(cat, profile):
        if cat == target:
            return {"category": cat, "status": D.STATUS_OK, "events": events,
                    "note": note, "detail": ""}
        return {"category": cat, "status": D.STATUS_OK, "note": "", "detail": "",
                "events": [{"name": "A-" + cat, "category": cat},
                           {"name": "B-" + cat, "category": cat}]}

    monkeypatch.setattr(D, "search_category", fake)
    from test_event_intel_discover import PROFILE
    out = D.discover(PROFILE)
    rows = {s["category"]: s for s in out["shortfall"]}
    assert target in rows, "the short category never reached the shortfall list"
    return rows[target]["why"]


def test_a_category_that_found_one_is_not_described_as_finding_none(monkeypatch):
    """The fallback and the bar beside it have to agree. Under a bar reading
    "1 of 2", "this category returned nothing for this client" contradicts the
    picture it is captioning. It was unreachable while every category came
    back with a note, and dropping useless notes made it reachable."""
    said = _shortfall_why(1, "", monkeypatch)
    assert "nothing" not in said
    assert "one event" in said


def test_a_category_that_really_found_none_still_says_so(monkeypatch):
    assert "nothing" in _shortfall_why(0, "", monkeypatch)


def test_a_usable_note_still_wins_over_the_fallback(monkeypatch):
    assert _shortfall_why(1, "Only one such event serves this buyer.",
                          monkeypatch) == "Only one such event serves this buyer."


# ── dates, written once ───────────────────────────────────────────────────

def test_the_stored_summary_writes_dates_the_way_the_list_below_it_does():
    """Element 5 is stored as finished text and was stored as ISO, so one
    report showed "2027-06-08 to 2027-06-10" in Top five and
    "Jun 8, 2027 to Jun 10, 2027" for the same event a few lines below."""
    said = REP._fmt_when({"starts_on": "2027-06-08", "ends_on": "2027-06-10"})
    assert said == "Jun 8, 2027 to Jun 10, 2027"
    assert REP._fmt_when({"starts_on": "2027-06-08",
                          "ends_on": "2027-06-08"}) == "Jun 8, 2027"
    assert REP._fmt_when({}) == "dates not announced"


@pytest.mark.parametrize("bad", ["garbage", "2027-13-01", "2027-06", ""])
def test_an_unparsable_date_is_passed_through_rather_than_invented(bad):
    out = REP._day(bad)
    assert out == bad, "a date this cannot read must not be reshaped"


# ── the same rules, in the rendered page ──────────────────────────────────

ISO = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")


def _report(**kw):
    """A one-event report carrying every string this file is about."""
    cand = _cand("SaaStr Annual", 87, "P1",
                 category="famous_flagship",   # not a key the page knows
                 attendees="600 attendees (their claim)",
                 matchmaking=0, format="virtual",
                 description="The largest floor on this list.",
                 client_line="Go for volume, not precision.")
    cand.update(kw.pop("cand", {}))
    run = _recommend([cand], **kw)
    return run


def test_a_category_key_the_page_has_no_label_for_is_still_spelled_for_a_person(page_script):
    html = _render(page_script, _report())
    assert "famous_flagship" not in html, \
        "an internal key reached the page a client reads"
    assert "Famous flagship" in html


def test_the_humanised_label_reaches_the_coverage_chart_too(page_script):
    """Three call sites shared the `LABELS[k] || k` fallback and each one
    could leak on its own, so the key is checked in the chart as well as in
    the card."""
    run = _report(shortfall=[{"category": "famous_flagship",
                              "label": "", "found": 0, "quota": 2,
                              "short_by": 2, "status": "empty",
                              "why": "Nothing here for this buyer."}],
                  statuses={"famous_flagship": {
                      "status": "empty", "label": "", "note": "", "detail": "",
                      "found": 0, "kept": 0, "merged_away": 0, "proposed": 2,
                      # The ruled-on list tags each cut candidate with its
                      # category, and it reads the label through a third call
                      # site of its own. With no rejected candidate the list
                      # is not drawn at all and that site goes unexercised,
                      # which is how a mutant survived here once.
                      "rejected": [{"name": "GTM Unbound",
                                    "reason": "no second edition is dated"}]}})
    html = _render(page_script, run)
    assert "famous_flagship" not in html
    assert "Famous flagship" in html


def test_an_attendance_claim_is_labelled_once(page_script):
    html = _render(page_script, _report())
    assert "(their claim) (their claim)" not in html
    assert html.count("their claim") >= 1


def test_a_value_with_no_number_in_it_is_not_shown_as_an_attendance_figure(page_script):
    """One run stored "Free, virtual" in the attendance field and the report
    presented it as this event's published attendance."""
    html = _render(page_script, _report(cand={"attendees": "Free, virtual"}))
    assert "Free, virtual" not in html


def test_no_iso_date_is_printed_anywhere_in_the_report(page_script):
    """Both remaining sites at once: the stored Top five line, and the ended
    date on an event kept out of the ranking."""
    run = _report(
        top_five=[{"name": "SaaStr Annual", "total": 87, "tier": "P1",
                   "where": "San Mateo, CA, United States",
                   "when": "2027-05-12 to 2027-05-14",
                   "case": "The largest floor on this list."},
                  {"name": "Not In The List", "total": 80, "tier": "P2",
                   "where": "Austin, TX", "when": "2027-04-01",
                   "case": "A second one, so the section is drawn."}],
        finished=[{"name": "B2B Marketing Exchange 2026",
                   "ends_on": "2026-02-26"}])
    html = _render(page_script, run)
    found = ISO.findall(html)
    assert not found, "ISO dates printed to a reader: %s" % found
    assert "May 12, 2027 to May 14, 2027" in html
    assert "ended Feb 26, 2026" in html


def test_an_event_with_no_end_date_says_so_rather_than_printing_a_mark(page_script):
    html = _render(page_script, _report(
        finished=[{"name": "Undated Thing", "ends_on": None}]))
    assert "ended date unknown" in html
    assert "ended ?" not in html


def test_the_penalty_label_is_written_like_its_siblings(page_script):
    """It sat under three sentence-case sub-score labels in lowercase."""
    html = _render(page_script, _report())
    assert "No matchmaking bonus" in html
    assert "no matchmaking bonus" not in html
