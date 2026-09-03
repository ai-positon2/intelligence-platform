"""Six-category discovery: the countermeasure to famous-event bias.

The model call is stubbed throughout. What is under test is everything the
skill's Step 2 actually depends on: that each category is searched on its own,
that a category which found nothing is distinguishable from one that failed,
and that the same event cannot occupy three slots under three labels.
"""

import json
import re

import pytest

from tracker import claude_websearch
from tracker import event_intel_discover as D
from tracker import event_intel_rubric as R


PROFILE = {"client_name": "Northwind", "website": "https://northwind.example",
           "classification": R.CLASS_B2B_TO_MARKETING,
           "buyer_roles": "VP Marketing", "verticals": "fintech",
           "geo_scope": "North America", "window_months": 12,
           "budget_note": "about $40k", "acv_band": "$60k",
           "force_exclude": "Dreamforce\nCES"}


# ── the prompt's view of the client ───────────────────────────────────────

def test_profile_brief_carries_the_classification_and_the_icp():
    b = D.profile_brief(PROFILE)
    assert "Northwind" in b
    assert R.CLASSIFICATION_LABELS[R.CLASS_B2B_TO_MARKETING] in b
    assert "VP Marketing" in b and "fintech" in b
    assert "next 12 months" in b


def test_budget_never_reaches_the_prompt():
    """Recorded on the profile, shown in the report, never seen by a model
    that is describing or scoring an event."""
    b = D.profile_brief(PROFILE)
    assert "40k" not in b and "budget" not in b.lower()


def test_the_category_prompt_names_which_side_of_the_floor_to_look_at(monkeypatch):
    monkeypatch.setattr(D, "_today", lambda: "2026-09-02")
    sys_prompt = D.find_system(R.CAT_FREE_VENDOR, PROFILE)
    assert "Behind the booths" in sys_prompt
    assert "TODAY IS 2026-09-02" in sys_prompt, (
        "without an anchor the model measures the client's window from "
        "whenever it believes now is")
    assert R.CATEGORY_LABELS[R.CAT_FREE_VENDOR] in sys_prompt
    assert R.CATEGORY_BRIEF[R.CAT_FREE_VENDOR][:40] in sys_prompt


def test_the_confirm_prompt_carries_the_same_anchors_as_the_find_prompt(monkeypatch):
    """Both stages search, so both need the date anchor and the side of the
    floor. The confirm prompt was added second and is the one that would
    silently drift."""
    monkeypatch.setattr(D, "_today", lambda: "2026-09-02")
    sys_prompt = D.confirm_system(
        {"name": "SaaStr Annual", "why": "dense with the buyer role"},
        R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert "SaaStr Annual" in sys_prompt
    assert "Behind the booths" in sys_prompt
    assert "TODAY IS 2026-09-02" in sys_prompt


def test_the_confirmer_is_told_it_may_say_no():
    """The whole point of the second stage. A confirmer that reads as an
    advocate would rubber-stamp whatever the finder proposed, and the split
    would buy nothing but latency."""
    sys_prompt = D.confirm_system({"name": "X"}, R.CAT_EMERGING, PROFILE)
    assert "YOU ARE THE CHECK, NOT THE ADVOCATE" in sys_prompt
    assert "reject_reason" in sys_prompt


def test_both_prompts_are_built_by_the_shipped_code_not_by_the_test():
    """Three tests broke on a new placeholder because each rebuilt the format
    call by hand. A prompt assembled anywhere but in the module is a prompt
    nobody is really testing."""
    import inspect
    src = inspect.getsource(D)
    body = src[src.index("def propose_category"):]
    assert "_FIND_SYSTEM.format" not in body
    assert "_CONFIRM_SYSTEM.format" not in body


# ── dedup keys ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("a,b", [
    ("SaaStr Annual 2026", "SaaStr Annual"),
    ("SaaStr Annual", "saastr annual conference"),
    ("MarTech Summit Europe", "Martech Summit"),
])
def test_the_same_event_named_three_ways_collapses_to_one_key(a, b):
    assert D.name_key(a) == D.name_key(b)


@pytest.mark.parametrize("a,b", [
    ("Web Summit", "Web Summit Rio"),
    ("AWS Summit", "AWS re:Invent"),
    ("HIMSS", "HLTH"),
])
def test_genuinely_different_events_keep_different_keys(a, b):
    assert D.name_key(a) != D.name_key(b)


def test_an_all_generic_name_still_produces_a_key():
    """Stripping every show word from "The 2026 Conference" leaves nothing, and
    an empty key would make merge() drop a real event silently."""
    assert D.name_key("The 2026 Conference")
    assert D.name_key("The Annual Summit 2027")


def test_host_key_ignores_www():
    assert D.host_key("https://www.x.example/a") == "x.example"
    assert D.host_key("http://x.example") == "x.example"
    assert D.host_key("") == "" and D.host_key(None) == ""


# ── merge ─────────────────────────────────────────────────────────────────

def _e(name, website=None, cat=R.CAT_INDUSTRY_FLAGSHIP):
    return {"name": name, "website": website, "category": cat}


def test_merge_keeps_one_row_when_two_categories_find_the_same_event():
    """Built vertical-summit-first on purpose. merge() walks the canonical
    category order, not the dict's, so the flagship label wins wherever the
    finders happened to finish: a flagship wearing a vertical-summit label is
    the exact bias this stage exists to prevent."""
    by = {R.CAT_VERTICAL_SUMMIT: [_e("SaaStr Annual", "https://saastr.example",
                                     R.CAT_VERTICAL_SUMMIT)],
          R.CAT_INDUSTRY_FLAGSHIP: [_e("SaaStr Annual 2026", "https://saastr.example")]}
    assert list(by) == [R.CAT_VERTICAL_SUMMIT, R.CAT_INDUSTRY_FLAGSHIP]
    out = D.merge(by)
    assert len(out) == 1
    assert out[0]["category"] == R.CAT_INDUSTRY_FLAGSHIP


def test_merge_dedupes_on_the_website_when_the_names_share_nothing():
    """Side events in particular get renamed year to year while keeping the
    same registration page, so the host is the only thing tying them together."""
    by = {R.CAT_INDUSTRY_FLAGSHIP: [_e("Northwind Field Day",
                                      "https://ops.example/dinner")],
          R.CAT_SIDE_EVENT: [_e("Revenue Leaders Dinner",
                                "https://www.ops.example/dinner/",
                                R.CAT_SIDE_EVENT)]}
    assert D.name_key("Northwind Field Day") != D.name_key("Revenue Leaders Dinner")
    out = D.merge(by)
    assert len(out) == 1
    assert out[0]["name"] == "Northwind Field Day"


def test_one_host_running_many_events_is_not_one_event():
    """The dedup that used to empty two of the six categories. Every AWS Summit
    is on aws.amazon.com and every side event is on lu.ma, so deduping on the
    bare host deleted the free-vendor and side-event circuits, which are the
    two the category split exists to surface."""
    by = {R.CAT_INDUSTRY_FLAGSHIP: [
              _e("AWS re:Invent", "https://aws.amazon.com/reinvent")],
          R.CAT_REGIONAL_FLAGSHIP: [
              _e("AWS Summit London", "https://aws.amazon.com/summits/london",
                 R.CAT_REGIONAL_FLAGSHIP)],
          R.CAT_FREE_VENDOR: [
              _e("AWS Summit New York", "https://aws.amazon.com/summits/nyc",
                 R.CAT_FREE_VENDOR)]}
    assert len(D.merge(by)) == 3


def test_two_side_events_on_one_ticketing_host_stay_two_events():
    by = {R.CAT_SIDE_EVENT: [
        _e("RevOps Breakfast", "https://lu.ma/revops-bfast", R.CAT_SIDE_EVENT),
        _e("CMO Dinner", "https://lu.ma/cmo-dinner", R.CAT_SIDE_EVENT)]}
    assert len(D.merge(by)) == 2


def test_two_continents_of_the_same_brand_are_two_events():
    """Money20/20 USA and Money20/20 Europe are different dates, different
    cities and different buyers. Stripping the region collapsed them into one
    row and silently removed a real event from the client's year."""
    by = {R.CAT_INDUSTRY_FLAGSHIP: [
        _e("Money20/20 USA", "https://us.money2020.example"),
        _e("Money20/20 Europe", "https://eu.money2020.example")]}
    assert [e["name"] for e in D.merge(by)] == ["Money20/20 USA",
                                                "Money20/20 Europe"]


def test_a_region_and_no_region_are_still_one_event():
    """The other direction, which must keep working: a name that states no
    region is compatible with any region."""
    by = {R.CAT_VERTICAL_SUMMIT: [
        _e("MarTech Summit Europe", "https://a.example", R.CAT_VERTICAL_SUMMIT),
        _e("MarTech Summit", "https://b.example", R.CAT_VERTICAL_SUMMIT)]}
    assert len(D.merge(by)) == 1


@pytest.mark.parametrize("excluded,kept", [
    ("CES", "Processing Summit"),
    ("CES", "Access Live"),
    ("AI", "Retail Week"),
    ("SaaS", "Sales Enablement Summit"),
])
def test_an_exclusion_never_matches_a_word_it_merely_sits_inside(excluded, kept):
    """"CES" used to exclude "ProCESsing Summit" and "Access Live". A false
    exclusion deletes a real recommendation and leaves no trace of it anywhere
    in the report."""
    by = {R.CAT_INDUSTRY_FLAGSHIP: [_e(kept)]}
    assert [e["name"] for e in D.merge(by, force_exclude=excluded)] == [kept]


def test_a_commitment_never_matches_a_word_it_merely_sits_inside():
    """Worse than a false exclusion: a falsely committed event is kept below
    the scoring floor and then named in the executive summary as money the
    client has already spent."""
    by = {R.CAT_INDUSTRY_FLAGSHIP: [_e("Retail Week")]}
    out = D.merge(by, force_include="AI")
    assert out[0]["committed"] is False


def test_a_commitment_still_matches_the_edition_it_names():
    by = {R.CAT_INDUSTRY_FLAGSHIP: [_e("Money20/20 USA 2026")]}
    assert D.merge(by, force_include="Money20/20 USA")[0]["committed"] is True


def test_a_category_answered_without_searching_is_discarded(monkeypatch):
    """The refusal event_intel_recover already applies to a recovered roster,
    for the same reason and with more at stake: here the thing being recalled
    is whole conferences rather than rows on a page somebody can check."""
    def fake_ask(system, user, **kw):
        return {"text": json.dumps({"events": [{"name": "Ghost Expo"}],
                                    "note": ""}),
                "error": None, "text_block_count": 1, "stop_reason": "end_turn",
                "search_count": 0}
    monkeypatch.setattr(claude_websearch, "ask", fake_ask)
    r = D.search_category(R.CAT_EMERGING, PROFILE)
    assert r["status"] == D.STATUS_ERROR
    assert r["events"] == []
    assert "without running a single search" in r["detail"]


def test_merge_honours_the_force_exclude_list_in_code_not_just_the_prompt():
    """A model asked to skip something returns it anyway often enough that a
    second pass here is worth its cost."""
    by = {R.CAT_INDUSTRY_FLAGSHIP: [_e("Dreamforce 2026"), _e("Keeper Summit")]}
    out = D.merge(by, force_exclude="Dreamforce\nCES")
    assert [e["name"] for e in out] == ["Keeper Summit"]


def test_merge_survives_rows_with_no_usable_name():
    by = {R.CAT_EMERGING: [_e(""), _e("   "), _e("Real Event")]}
    assert [e["name"] for e in D.merge(by)] == ["Real Event"]


# ── one category ──────────────────────────────────────────────────────────

# ── stubbing two stages ───────────────────────────────────────────────────
#
# One canned reply can no longer serve a whole category. Finding names and
# confirming one of them are separate calls parsing different envelopes, so
# the stub tells them apart the way the module does: by the prompt it sent.

_EVENT = {"name": "Real Event", "starts_on": "2027-03-01",
          "sources": ["https://example.com/e"], "category_fit": "fits",
          "confidence": "high"}


def _find_reply(candidates, note="n", complete=None):
    body = {"candidates": candidates, "note": note}
    if complete is not None:
        body["search_complete"] = complete
    return json.dumps(body)


def _confirm_reply(event=None, confirmed=True, reject_reason=None,
                   facts_complete=None):
    body = {"confirmed": confirmed, "event": event}
    if reject_reason is not None:
        body["reject_reason"] = reject_reason
    if facts_complete is not None:
        body["facts_complete"] = facts_complete
    return json.dumps(body)


def _named(**over):
    """One candidate the finder proposes, and the event it confirms to."""
    ev = dict(_EVENT)
    ev.update(over)
    return ev


_CONFIRM_TARGET = re.compile(r"THE EVENT TO CONFIRM: (.+)")


def _stages(monkeypatch, find=None, confirm=None, find_error=None,
            confirm_error=None, find_searches=3, confirm_searches=3,
            find_text=None, confirm_text=None,
            find_budget=False, confirm_budget=False):
    """Stub both discovery stages.

    `find` and `confirm` are the decoded reply bodies. `confirm` may instead
    be a callable taking the candidate name, so one test can reject one
    candidate and confirm another.
    """
    if find is None:
        find = _find_reply([{"name": "Real Event",
                             "website": "https://example.com/e", "why": "w"}])
    if confirm is None:
        confirm = _confirm_reply(_EVENT)

    def fake_ask(system, user, **kw):
        finding = "YOUR ONLY JOB IS TO NAME CANDIDATES" in system
        if finding:
            if find_error:
                return _res("", find_error, find_searches, find_budget)
            return _res(find_text if find_text is not None else find,
                        None, find_searches, find_budget)
        if confirm_error:
            return _res("", confirm_error, confirm_searches, confirm_budget)
        if confirm_text is not None:
            return _res(confirm_text, None, confirm_searches, confirm_budget)
        body = confirm
        if callable(body):
            m = _CONFIRM_TARGET.search(system)
            body = body(m.group(1).strip() if m else "")
        return _res(body, None, confirm_searches, confirm_budget)

    def _res(text, error, searches, budget):
        return {"text": text, "raw": text, "error": error,
                "stop_reason": "end_turn", "text_block_count": 1,
                "tool_version": "v", "tool_errors": [], "usage": {},
                # A real reply carries the number of searches it ran. Both
                # stages discard an answer that ran none, so a stub omitting
                # this is stubbing an ungrounded answer.
                "search_count": searches,
                # And whether the model wanted a search it was not given.
                # ask() sets this on every reply, so a stub that leaves it out
                # is stubbing a reply shape that cannot occur.
                "budget_spent": budget}

    monkeypatch.setattr(claude_websearch, "ask", fake_ask)


# ── a spent search budget is not a failed search ─────────────────────────
#
# The Beta Bionics regression. `max_uses_exceeded` is how the web_search tool
# enforces the caller's own max_uses, and every call here saturates its budget
# by design, so the wrapper used to report a complete reply as a failed search
# and this module discarded it whole. Four of six categories died that way in
# one live run, after half an hour of searching, and the run shipped one event.

def test_a_finder_that_spends_its_whole_budget_keeps_the_events_it_found(monkeypatch):
    """The single most expensive line in this module's history. The finder
    named a real event, the confirmer confirmed it, and the only thing that
    went 'wrong' was the finder reaching for a seventh search under a budget
    of six."""
    _stages(monkeypatch,
            find=_find_reply([{"name": "ATTD", "website": "https://attd.example",
                               "why": "dense with the buyer role"}],
                             complete=True),
            confirm=_confirm_reply(_named(name="ATTD")),
            find_budget=True)
    r = D.search_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert [e["name"] for e in r["events"]] == ["ATTD"], (
        "a category that spent its search budget had its events thrown away")
    assert r["status"] == D.STATUS_OK
    assert r["budget_spent"] is True


def test_a_spent_budget_does_not_downgrade_a_finished_category(monkeypatch):
    """It is not a status. A category that named its candidates and confirmed
    them did a complete piece of work, and reporting it as partial would put
    'this category fell short' in a report about a search that did not."""
    _stages(monkeypatch,
            find=_find_reply([{"name": "A", "website": "https://a.example", "why": "w"},
                              {"name": "B", "website": "https://b.example", "why": "w"}],
                             complete=True),
            confirm=lambda nm: _confirm_reply(_named(name=nm)),
            find_budget=True)
    r = D.search_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert r["status"] == D.STATUS_OK
    assert r["detail"] == ""


def test_a_confirmer_that_spends_its_budget_still_confirms(monkeypatch):
    """Same bug, one stage later, and worse: here the discarded reply is a
    confirmation of a real event that was about to be dropped from the list."""
    _stages(monkeypatch,
            find=_find_reply([{"name": "ATTD", "website": "https://attd.example",
                               "why": "w"}], complete=True),
            confirm=_confirm_reply(_named(name="ATTD")),
            confirm_budget=True)
    r = D.search_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert [e["name"] for e in r["events"]] == ["ATTD"]


def test_a_finder_out_of_searches_says_so_in_our_words_not_as_a_fault(monkeypatch):
    """When the model itself reports the cap as a cut-off, the reason shown to
    a client must describe our own budget rather than send somebody hunting
    for a broken tool."""
    _stages(monkeypatch,
            find=_find_reply([{"name": "ATTD", "website": "https://attd.example",
                               "why": "w"}], complete=False),
            confirm=_confirm_reply(_named(name="ATTD")),
            find_budget=True)
    r = D.search_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert r["status"] == D.STATUS_PARTIAL
    assert "all %d of the searches" % D.FIND_MAX_USES in r["detail"]
    assert "could not finish searching" not in r["detail"]


def test_a_failed_search_explains_itself_without_naming_the_error_kind(monkeypatch):
    """The kind is a machine token. Rendered under a category label in the
    report it read "Side event: Transport: peer closed connection", a double
    colon around a word that means nothing to the reader."""
    _stages(monkeypatch,
            find_error={"kind": "transport",
                        "detail": "peer closed connection without a body."})
    r = D.search_category(R.CAT_SIDE_EVENT, PROFILE)
    assert r["status"] == D.STATUS_ERROR
    assert "peer closed connection" in r["detail"]
    assert not r["detail"].startswith("transport"), (
        "the error kind leaked into a sentence a client reads")
    assert r["detail"][0].isupper()


def test_a_genuinely_cut_off_finder_still_reads_as_a_gap(monkeypatch):
    """The other half of the pair. No budget was spent, so the model saying it
    could not finish means a search actually broke, and that wording has to
    survive."""
    _stages(monkeypatch,
            find=_find_reply([], complete=False),
            find_budget=False)
    r = D.search_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert r["status"] == D.STATUS_ERROR
    assert "gap in the search" in r["detail"]


def test_a_short_category_says_whether_it_had_searches_left(monkeypatch):
    """The one place the spent budget changes a decision: 'we looked with
    everything we had' and 'we looked with searches to spare' are different
    findings, and only the first is worth spending more on."""
    _stages(monkeypatch, find=_find_reply([], complete=True), find_budget=True)
    out = D.discover(PROFILE)
    short = {s["category"]: s for s in out["shortfall"]}
    row = short[R.CAT_VERTICAL_SUMMIT]
    assert row["budget_spent"] is True
    assert "every one of the %d searches" % D.FIND_MAX_USES in row["why"]


def test_a_short_category_with_searches_left_does_not_claim_it_ran_out(monkeypatch):
    _stages(monkeypatch, find=_find_reply([], complete=True), find_budget=False)
    out = D.discover(PROFILE)
    row = {s["category"]: s for s in out["shortfall"]}[R.CAT_VERTICAL_SUMMIT]
    assert row["budget_spent"] is False
    assert "searches" not in row["why"]


def test_the_budget_sentence_is_not_said_twice(monkeypatch):
    """The detail already names the budget when the model reported the cap as
    a cut-off. Appending the shortfall sentence on top would print the same
    fact twice in one paragraph."""
    _stages(monkeypatch, find=_find_reply([], complete=False), find_budget=True)
    out = D.discover(PROFILE)
    row = {s["category"]: s for s in out["shortfall"]}[R.CAT_VERTICAL_SUMMIT]
    assert "searches it was given" in row["why"]
    assert "every one of the" not in row["why"], (
        "the shortfall sentence was appended on top of a detail that had "
        "already said the same thing")


def test_the_prompts_tell_the_model_its_budget_is_not_a_fault():
    """Half the fix is the prompt. A model told to flag being 'cut off' will
    flag its own budget cap, and the report then says a category went
    unsearched when it was searched to the limit we paid for."""
    find = D.find_system(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert "%d SEARCHES" % D.FIND_MAX_USES in find
    assert "max_uses_exceeded" in find
    confirm = D.confirm_system({"name": "X"}, R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert "%d SEARCHES" % D.CONFIRM_MAX_USES in confirm


def test_a_category_that_finds_events_reports_ok(monkeypatch):
    _stages(monkeypatch,
            find=_find_reply([{"name": "PMM Summit",
                               "website": "https://pmm.example", "why": "w"}],
                             note="found one", complete=True),
            confirm=_confirm_reply(_named(name="PMM Summit")))
    r = D.search_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert r["status"] == D.STATUS_OK
    assert r["events"][0]["category"] == R.CAT_VERTICAL_SUMMIT
    assert r["proposed"] == 1


def test_a_category_that_genuinely_has_nothing_reports_empty(monkeypatch):
    _stages(monkeypatch,
            find=_find_reply([], note="No free vendor conferences serve this "
                                      "niche.", complete=True))
    r = D.search_category(R.CAT_FREE_VENDOR, PROFILE)
    assert r["status"] == D.STATUS_EMPTY
    assert "niche" in r["note"]


def test_a_category_whose_search_failed_reports_error_not_empty(monkeypatch):
    """The distinction the module exists for. 'Nothing serves this niche' is a
    finding about the market; 'the search failed' is a hole in the analysis.
    Both render as an absence unless they are kept apart here."""
    _stages(monkeypatch, find_error={"kind": "transport", "detail": "HTTP 503"})
    r = D.search_category(R.CAT_FREE_VENDOR, PROFILE)
    assert r["status"] == D.STATUS_ERROR
    assert "503" in r["detail"]


def test_an_unreadable_reply_is_an_error_rather_than_an_empty_category(monkeypatch):
    _stages(monkeypatch, find_text="I could not find anything useful, sorry.")
    r = D.search_category(R.CAT_EMERGING, PROFILE)
    assert r["status"] == D.STATUS_ERROR


def test_clean_event_rejects_a_non_http_website_and_sources(monkeypatch):
    _stages(monkeypatch, confirm=_confirm_reply({
        "name": "X", "website": "javascript:alert(1)",
        "sources": ["https://ok.example", "javascript:x", 7],
        "attendees": "9,000+", "organizer_run": True,
        "matchmaking_evidence": "Hosted buyer programme."}))
    ev = D.search_category(R.CAT_EMERGING, PROFILE)["events"][0]
    assert ev["website"] is None
    assert ev["sources"] == ["https://ok.example"]
    assert ev["attendees"] == "9,000+"
    assert ev["organizer_run"] is True


def test_a_proposal_carrying_a_javascript_url_never_reaches_the_confirm_prompt(monkeypatch):
    """The finder's website is interpolated into the confirmer's system
    prompt. Anything that is not http(s) is dropped at the proposal, for the
    same reason `_clean_event` drops it at the event."""
    seen = {}
    _stages(monkeypatch,
            find=_find_reply([{"name": "X", "website": "javascript:alert(1)",
                               "why": "w"}], complete=True))
    real = claude_websearch.ask

    def spy(system, user, **kw):
        if "THE EVENT TO CONFIRM" in system:
            seen["system"] = system
        return real(system, user, **kw)

    monkeypatch.setattr(claude_websearch, "ask", spy)
    D.search_category(R.CAT_EMERGING, PROFILE)
    assert "javascript:" not in seen["system"]
    assert "Their link for it" not in seen["system"]


# ── the whole sweep ───────────────────────────────────────────────────────

def test_discover_searches_every_one_of_the_six_categories(monkeypatch):
    seen = []

    def fake(cat, profile):
        seen.append(cat)
        return {"category": cat, "status": D.STATUS_OK, "note": "", "detail": "",
                "events": [{"name": "E-%s-1" % cat, "category": cat,
                            "website": "https://%s1.example" % cat},
                           {"name": "E-%s-2" % cat, "category": cat,
                            "website": "https://%s2.example" % cat}]}
    monkeypatch.setattr(D, "search_category", fake)
    out = D.discover(PROFILE)
    assert set(seen) == set(R.CATEGORIES)
    assert out["categories_searched"] == 6 and out["categories_failed"] == 0
    assert out["shortfall"] == []
    assert out["found"] == 12


def test_shortfall_separates_an_empty_market_from_a_failed_search(monkeypatch):
    def fake(cat, profile):
        if cat == R.CAT_FREE_VENDOR:
            return {"category": cat, "status": D.STATUS_EMPTY, "events": [],
                    "note": "No vendor runs city events in this vertical.",
                    "detail": ""}
        if cat == R.CAT_SIDE_EVENT:
            return {"category": cat, "status": D.STATUS_ERROR, "events": [],
                    "note": "", "detail": "transport: HTTP 503"}
        return {"category": cat, "status": D.STATUS_OK, "note": "", "detail": "",
                "events": [{"name": "A-" + cat, "category": cat},
                           {"name": "B-" + cat, "category": cat}]}
    monkeypatch.setattr(D, "search_category", fake)
    out = D.discover(PROFILE)

    by = {s["category"]: s for s in out["shortfall"]}
    assert set(by) == {R.CAT_FREE_VENDOR, R.CAT_SIDE_EVENT}
    assert by[R.CAT_FREE_VENDOR]["status"] == D.STATUS_EMPTY
    assert "vendor runs city events" in by[R.CAT_FREE_VENDOR]["why"]
    assert by[R.CAT_SIDE_EVENT]["status"] == D.STATUS_ERROR
    assert "503" in by[R.CAT_SIDE_EVENT]["why"]
    assert out["categories_failed"] == 1


def test_one_category_crashing_does_not_cost_the_other_five(monkeypatch):
    def fake(cat, profile):
        if cat == R.CAT_EMERGING:
            raise RuntimeError("boom")
        return {"category": cat, "status": D.STATUS_OK, "note": "", "detail": "",
                "events": [{"name": "A-" + cat, "category": cat},
                           {"name": "B-" + cat, "category": cat}]}
    monkeypatch.setattr(D, "search_category", fake)
    out = D.discover(PROFILE)
    assert out["found"] == 10
    assert out["statuses"][R.CAT_EMERGING]["status"] == D.STATUS_ERROR
    assert "boom" in out["statuses"][R.CAT_EMERGING]["detail"]


def test_discovery_returns_facts_and_scores_nothing(monkeypatch):
    """Scoring is a separate pass so that one consistent standard is applied
    across all six categories, rather than each finder grading its own."""
    def fake(cat, profile):
        return {"category": cat, "status": D.STATUS_OK, "note": "", "detail": "",
                "events": [{"name": "A-" + cat, "category": cat}]}
    monkeypatch.setattr(D, "search_category", fake)
    for c in D.discover(PROFILE)["candidates"]:
        for field in ("total", "tier", "relevance", "dm_access", "engagement"):
            assert field not in c


# ── a starved search is not an empty market ──────────────────────────────
#
# Observed live on 2026-09-02. The server-side search tool stopped answering
# part-way through two of six categories, one of them after an initial batch
# of eight parallel queries, and the model wrote its reply from what it
# already had. Both were recorded status="empty", and the report renders that
# as "this category has nothing for you". The truth was that the search never
# finished. This is the exact conflation the six-category split exists to
# prevent, running in the direction nobody checked.

_ONE = [{"name": "Real Event", "website": "https://example.com/e", "why": "w"}]


def _run_category(monkeypatch, find, confirm=None):
    _stages(monkeypatch, find=find,
            confirm=confirm if confirm is not None else _confirm_reply(_EVENT))
    return D.search_category("industry_flagship", {"classification": "b2b_to_marketing"})


def test_an_unfinished_search_with_nothing_found_is_an_error_not_an_empty_category(monkeypatch):
    r = _run_category(monkeypatch, _find_reply([], note="I could not finish.",
                                               complete=False))
    assert r["status"] == D.STATUS_ERROR, (
        "a search that was cut off was reported as a category with nothing in it")
    assert "gap in the search" in r["detail"]


def test_an_unfinished_search_that_still_found_events_keeps_them_and_says_so(monkeypatch):
    r = _run_category(monkeypatch, _find_reply(_ONE, complete=False))
    assert r["status"] == D.STATUS_PARTIAL
    assert len(r["events"]) == 1, "confirmed events were thrown away with the gap"
    assert r["detail"]


def test_a_finished_search_with_nothing_found_is_still_a_real_empty(monkeypatch):
    """The other half of the distinction. A properly searched category that
    genuinely has nothing must keep saying so, or the fix above would turn
    every honest empty into a scary error."""
    r = _run_category(monkeypatch, _find_reply([], note="Searched, nothing fits.",
                                               complete=True))
    assert r["status"] == D.STATUS_EMPTY
    assert r["note"] == "Searched, nothing fits."


def test_no_completeness_declaration_reports_what_could_not_be_measured(monkeypatch):
    """Silence is not a claim of success. An older reply with no
    search_complete field must not be read as a finished search."""
    r = _run_category(monkeypatch, _find_reply([], note="nothing"))
    assert r["status"] == D.STATUS_EMPTY
    assert "did not say" in r["detail"] and "cut off" in r["detail"]


def test_the_shortfall_reason_for_a_partial_search_describes_the_SEARCH(monkeypatch):
    """The bug as a reader experiences it. `why` must not print the model's
    description of the market when the market was never fully looked at."""
    _stages(monkeypatch,
            find=_find_reply([{"name": "Only One", "website": "https://e.example",
                               "why": "w"}],
                             note="This market looks quiet.", complete=False),
            confirm=_confirm_reply(_named(name="Only One")))
    out = D.discover({"classification": "b2b_to_marketing"})
    reasons = {s["category"]: s["why"] for s in out["shortfall"]}
    for cat, why in reasons.items():
        assert "This market looks quiet." not in why, (
            "category %s reported an unfinished search as a fact about the "
            "market" % cat)


# ── the two stages ────────────────────────────────────────────────────────
#
# The first live end-to-end run gave each category one call with a budget of
# eight searches to both find events and confirm them. Every one of the six
# saturated it, five reported nothing, and three events survived the run. The
# input bill for a server-side search call grows with the square of its search
# count, so the fix was to split the call rather than raise the budget.
#
# These tests pin the split itself and the honesty it has to preserve. The
# dangerous new failure is the one the third status exists for: a candidate
# the confirmer SEARCHED and ruled out is a fact about the market, and a
# candidate it could not check is a hole, and they are now produced by the
# same code path.

def _budgets(monkeypatch, **kw):
    """Run one category and record the budget every call was given."""
    calls = []
    _stages(monkeypatch, **kw)
    real = claude_websearch.ask

    def spy(system, user, **kwargs):
        calls.append({"find": "YOUR ONLY JOB IS TO NAME CANDIDATES" in system,
                      "max_uses": kwargs.get("max_uses"),
                      "system": system, "user": user})
        return real(system, user, **kwargs)

    monkeypatch.setattr(claude_websearch, "ask", spy)
    return calls


def test_no_single_call_is_given_a_budget_big_enough_to_starve_again():
    """The fix, stated as a number.

    Input cost for one search call grows with the SQUARE of its search count,
    because every result it has already read is re-sent on every later turn.
    Eight searches in one call cost between 163k and 549k input tokens in the
    live run and took up to nineteen minutes. Raising that budget is the one
    change that cannot work, so no stage may quietly grow back into it.
    """
    assert D.FIND_MAX_USES <= 8
    assert D.CONFIRM_MAX_USES <= 8


def test_a_category_now_gets_more_searches_than_the_single_call_ever_did():
    """The other half. Splitting the call is only a fix if the category ends
    up able to look HARDER than before, not merely more cheaply."""
    per_category = D.FIND_MAX_USES + D.CONFIRM_MAX_USES * D.PER_CATEGORY
    assert per_category > 8, (
        "the split reduced total search coverage, which is the problem it "
        "exists to solve")


def test_finding_and_confirming_are_separate_calls(monkeypatch):
    calls = _budgets(monkeypatch,
                     find=_find_reply(_ONE, complete=True),
                     confirm=_confirm_reply(_EVENT))
    D.search_category(R.CAT_EMERGING, PROFILE)
    assert [c["find"] for c in calls] == [True, False]
    assert calls[0]["max_uses"] == D.FIND_MAX_USES
    assert calls[1]["max_uses"] == D.CONFIRM_MAX_USES


def test_a_confirmation_never_sees_the_other_candidates(monkeypatch):
    """Why the split is cheap. Each confirmation carries only its own pages,
    so a category's input bill grows with the number of candidates instead of
    with its square. A prompt naming all of them would undo that."""
    calls = _budgets(monkeypatch,
                     find=_find_reply(
                         [{"name": "Alpha Summit", "website": "https://a.example",
                           "why": "w"},
                          {"name": "Beta Forum", "website": "https://b.example",
                           "why": "w"}], complete=True),
                     confirm=lambda n: _confirm_reply(_named(name=n)))
    D.search_category(R.CAT_EMERGING, PROFILE)
    confirms = [c for c in calls if not c["find"]]
    assert len(confirms) == 2
    for c in confirms:
        named = [n for n in ("Alpha Summit", "Beta Forum") if n in c["system"]]
        assert named == [c["system"].split("THE EVENT TO CONFIRM: ")[1]
                         .splitlines()[0].strip()], (
            "a confirmation prompt carried a candidate it was not confirming")


def test_candidates_the_confirmer_ruled_out_make_an_empty_category(monkeypatch):
    """Checked and rejected is a finished piece of work. Three plausible names
    that all turn out to have no upcoming edition is a real finding about the
    client's year, not a failed search."""
    r = _run_category(
        monkeypatch, _find_reply(_ONE, complete=True),
        confirm=_confirm_reply(None, confirmed=False,
                               reject_reason="the 2025 edition was the last one"))
    assert r["status"] == D.STATUS_EMPTY
    assert r["rejected"] == [{"name": "Real Event",
                              "reason": "the 2025 edition was the last one"}]
    assert "last one" in r["detail"]


def test_candidates_the_confirmer_could_not_check_are_an_error_not_an_empty(monkeypatch):
    """The mirror image, and the one that costs a client money. A confirmation
    that never ran says nothing about the market, so it must never be able to
    produce the sentence 'this category has nothing for you'."""
    r = _run_category(monkeypatch, _find_reply(_ONE, complete=True),
                      confirm=None)
    assert r["status"] == D.STATUS_OK  # control: the same shape, confirmed

    _stages(monkeypatch, find=_find_reply(_ONE, complete=True),
            confirm_error={"kind": "transport", "detail": "HTTP 503"})
    r = D.search_category(R.CAT_EMERGING, PROFILE)
    assert r["status"] == D.STATUS_ERROR
    assert r["rejected"] == []
    assert "could not be checked" in r["detail"] and "503" in r["detail"]


def test_a_confirmation_that_cites_nothing_is_not_a_confirmation(monkeypatch):
    """This stage exists so that 'confirmed' means a second search actually
    saw the event. A reply with no source is an assertion, and an assertion
    from a model is the thing the whole module refuses to publish."""
    r = _run_category(monkeypatch, _find_reply(_ONE, complete=True),
                      confirm=_confirm_reply(_named(sources=[])))
    assert r["events"] == []
    assert r["status"] == D.STATUS_ERROR
    assert "without citing" in r["detail"]


def test_a_confirmation_that_ran_no_search_is_discarded(monkeypatch):
    """Same refusal the finder already makes. Here the thing being recalled is
    a whole conference rather than a row on a page somebody can check."""
    _stages(monkeypatch, find=_find_reply(_ONE, complete=True),
            confirm=_confirm_reply(_EVENT), confirm_searches=0)
    r = D.search_category(R.CAT_EMERGING, PROFILE)
    assert r["events"] == []
    assert r["status"] == D.STATUS_ERROR
    assert "without running a single search" in r["detail"]


def test_a_refusal_with_no_reason_is_unchecked_rather_than_a_market_finding(monkeypatch):
    """A confirmer that says no and will not say why has not told us anything
    about the market, so its silence must not be promoted into one."""
    r = _run_category(monkeypatch, _find_reply(_ONE, complete=True),
                      confirm=_confirm_reply(None, confirmed=False))
    assert r["status"] == D.STATUS_ERROR
    assert r["rejected"] == []
    assert "without saying what it found" in r["detail"]


def test_an_event_whose_numbers_were_cut_short_keeps_its_confirmation(monkeypatch):
    """Confirmed and fully-read are different claims. The event exists and was
    seen; some of its published numbers were not. Throwing it away would lose
    a real event, and printing it silently would overstate what we read."""
    r = _run_category(monkeypatch, _find_reply(_ONE, complete=True),
                      confirm=_confirm_reply(_EVENT, facts_complete=False))
    assert len(r["events"]) == 1
    assert r["events"][0]["facts_complete"] is False
    assert r["status"] == D.STATUS_PARTIAL
    assert "could not finish reading" in r["detail"]


def test_a_fully_read_event_is_not_flagged_as_partial(monkeypatch):
    """The control for the test above. If every confirmation were treated as
    incomplete, the flag would stop meaning anything."""
    r = _run_category(monkeypatch, _find_reply(_ONE, complete=True),
                      confirm=_confirm_reply(_EVENT, facts_complete=True))
    assert r["status"] == D.STATUS_OK
    assert r["events"][0]["facts_complete"] is True
    assert r["detail"] == ""


def test_one_event_proposed_twice_is_only_confirmed_once(monkeypatch):
    """A finder asked for four candidates sometimes names the same event twice.
    Confirming both spends a whole extra call for a row `merge` then throws
    away."""
    calls = _budgets(
        monkeypatch,
        find=_find_reply([{"name": "SaaStr Annual 2027",
                           "website": "https://saastr.example", "why": "w"},
                          {"name": "SaaStr Annual",
                           "website": "https://saastr.example/", "why": "w"}],
                         complete=True),
        confirm=lambda n: _confirm_reply(_named(name=n)))
    D.search_category(R.CAT_EMERGING, PROFILE)
    assert len([c for c in calls if not c["find"]]) == 1


def test_two_real_events_are_both_confirmed(monkeypatch):
    """The control. A dedup rule with no counterweight would collapse a
    category to one event and look like a working saving."""
    calls = _budgets(
        monkeypatch,
        find=_find_reply([{"name": "Money20/20 USA", "website": "https://a.example",
                           "why": "w"},
                          {"name": "Money20/20 Europe", "website": "https://b.example",
                           "why": "w"}], complete=True),
        confirm=lambda n: _confirm_reply(_named(name=n)))
    r = D.search_category(R.CAT_EMERGING, PROFILE)
    assert len([c for c in calls if not c["find"]]) == 2
    assert len(r["events"]) == 2


def test_one_candidate_crashing_does_not_cost_the_others(monkeypatch):
    boom = {"n": 0}

    def confirm(name):
        boom["n"] += 1
        if name == "Bad One":
            raise RuntimeError("kaboom")
        return _confirm_reply(_named(name=name))

    _stages(monkeypatch,
            find=_find_reply([{"name": "Bad One", "website": "https://a.example",
                               "why": "w"},
                              {"name": "Good One", "website": "https://b.example",
                               "why": "w"}], complete=True),
            confirm=confirm)
    r = D.search_category(R.CAT_EMERGING, PROFILE)
    assert [e["name"] for e in r["events"]] == ["Good One"]
    assert r["status"] == D.STATUS_ERROR or r["status"] == D.STATUS_PARTIAL
    assert "could not be checked" in r["detail"]


def test_a_confirmed_event_survives_a_finder_that_was_cut_short(monkeypatch):
    """A gap in the FINDING must not retroactively discredit an event a
    separate search confirmed. It shortens the list; it does not make the
    survivors suspect."""
    r = _run_category(monkeypatch, _find_reply(_ONE, complete=False),
                      confirm=_confirm_reply(_EVENT))
    assert len(r["events"]) == 1
    assert r["status"] == D.STATUS_PARTIAL
    assert "could not finish searching" in r["detail"]


def test_the_run_never_puts_more_calls_in_flight_than_the_cap(monkeypatch):
    """Six concurrent multi-search calls reliably tripped rate limiting. The
    module now makes many more calls than it used to, and they are submitted
    from two nested pools, so the only thing standing between it and a burst
    is the semaphore.

    The stubbed call has to actually TAKE time. A first version of this test
    returned instantly, so no two calls ever overlapped and the measured peak
    was one whether the semaphore was there or not: it passed with the cap
    deleted. The floor assertion at the end is what stops that happening
    again, by failing when the test did not manage to exercise concurrency at
    all rather than reporting a peak it never reached.
    """
    import threading
    import time
    live = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def fake_ask(system, user, **kw):
        with lock:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
        try:
            time.sleep(0.03)
            finding = "YOUR ONLY JOB IS TO NAME CANDIDATES" in system
            body = (_find_reply([{"name": "E%d" % i, "website": "https://e%d.example" % i,
                                  "why": "w"} for i in range(D.PER_CATEGORY)],
                                complete=True)
                    if finding else _confirm_reply(_named(name="E")))
            return {"text": body, "raw": body, "error": None,
                    "stop_reason": "end_turn", "text_block_count": 1,
                    "tool_version": "v", "search_count": 3, "tool_errors": [],
                    "usage": {}}
        finally:
            with lock:
                live["now"] -= 1

    monkeypatch.setattr(claude_websearch, "ask", fake_ask)
    D.discover({"classification": "b2b_to_marketing"})
    assert live["peak"] <= D.MAX_INFLIGHT, (
        "%d calls were in flight at once against a cap of %d"
        % (live["peak"], D.MAX_INFLIGHT))
    assert live["peak"] == D.MAX_INFLIGHT, (
        "this run only ever reached %d concurrent calls, so it never tested "
        "the cap of %d and would pass with the semaphore deleted"
        % (live["peak"], D.MAX_INFLIGHT))


def test_the_page_can_say_how_many_candidates_were_looked_at(monkeypatch):
    """Showing two events without saying five were checked lets a reader
    assume two was all there ever was."""
    _stages(monkeypatch,
            find=_find_reply([{"name": "A", "website": "https://a.example", "why": "w"},
                              {"name": "B", "website": "https://b.example", "why": "w"}],
                             complete=True),
            confirm=lambda n: (_confirm_reply(_named(name=n)) if n == "A"
                               else _confirm_reply(None, confirmed=False,
                                                   reject_reason="no future edition")))
    out = D.discover({"classification": "b2b_to_marketing"})
    st = out["statuses"][R.CAT_INDUSTRY_FLAGSHIP]
    assert st["proposed"] == 2
    assert st["found"] == 1
    assert st["rejected"] == [{"name": "B", "reason": "no future edition"}]


def test_the_budget_never_reaches_either_prompt_that_is_actually_sent(monkeypatch):
    """The wiring, not the template.

    `profile_brief` has always left the budget out, and a test asserting that
    on a prompt IT built passed happily while the code path that really sends
    the prompt appended the budget itself. So this one reads the strings that
    went out.

    The rule matters most at the confirm stage, which is the one that reads an
    event's published cost. A model that knows the client has $40k will
    describe a $60k sponsorship differently, and the skill is explicit that a
    cheap event reaching the wrong buyers is worse than an expensive one
    reaching the right ones.
    """
    sent = []
    _stages(monkeypatch, find=_find_reply(_ONE, complete=True),
            confirm=_confirm_reply(_EVENT))
    real = claude_websearch.ask

    def spy(system, user, **kw):
        sent.append(system)
        return real(system, user, **kw)

    monkeypatch.setattr(claude_websearch, "ask", spy)
    D.search_category(R.CAT_EMERGING, PROFILE)

    assert len(sent) == 2, "expected one find and one confirm"
    # The value, not the word. The find prompt legitimately says a fake
    # conference "costs somebody a travel budget", and a test that banned the
    # word would have to be weakened until it stopped testing anything.
    for system in sent:
        assert PROFILE["budget_note"] not in system
        assert "40k" not in system


def test_both_prompts_that_are_sent_carry_todays_date(monkeypatch):
    """Also the wiring. Every stage that searches measures the client's window
    from an anchor, and a stage wired without one measures it from whatever
    the model believes now is."""
    import datetime
    sent = []
    _stages(monkeypatch, find=_find_reply(_ONE, complete=True),
            confirm=_confirm_reply(_EVENT))
    real = claude_websearch.ask

    def spy(system, user, **kw):
        sent.append(system)
        return real(system, user, **kw)

    monkeypatch.setattr(claude_websearch, "ask", spy)
    D.search_category(R.CAT_EMERGING, PROFILE)

    today = datetime.date.today().isoformat()
    assert len(sent) == 2
    for system in sent:
        assert today in system
