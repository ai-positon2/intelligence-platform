"""Six-category discovery: the countermeasure to famous-event bias.

The model call is stubbed throughout. What is under test is everything the
skill's Step 2 actually depends on: that each category is searched on its own,
that a category which found nothing is distinguishable from one that failed,
and that the same event cannot occupy three slots under three labels.
"""

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


def test_the_category_prompt_names_which_side_of_the_floor_to_look_at():
    sys_prompt = D._SYSTEM.format(
        category_label=R.CATEGORY_LABELS[R.CAT_FREE_VENDOR],
        category_brief=R.CATEGORY_BRIEF[R.CAT_FREE_VENDOR],
        profile=D.profile_brief(PROFILE),
        where_buyers=R.CLASSIFICATION_WHERE_BUYERS_ARE[R.CLASS_B2B_TO_MARKETING])
    assert "Behind the booths" in sys_prompt
    assert "Free vendor conference" in sys_prompt
    assert "under-utilised" in sys_prompt


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
    by = {R.CAT_INDUSTRY_FLAGSHIP: [_e("Northwind Field Day", "https://ops.example")],
          R.CAT_SIDE_EVENT: [_e("Revenue Leaders Dinner", "https://www.ops.example/x",
                                R.CAT_SIDE_EVENT)]}
    assert D.name_key("Northwind Field Day") != D.name_key("Revenue Leaders Dinner")
    out = D.merge(by)
    assert len(out) == 1
    assert out[0]["name"] == "Northwind Field Day"


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

def _stub(monkeypatch, payload=None, error=None, text=None):
    def fake_ask(system, user, **kw):
        if error:
            return {"text": "", "error": error}
        import json
        return {"text": text if text is not None else json.dumps(payload),
                "error": None, "text_block_count": 1, "stop_reason": "end_turn"}
    monkeypatch.setattr(claude_websearch, "ask", fake_ask)


def test_a_category_that_finds_events_reports_ok(monkeypatch):
    _stub(monkeypatch, {"events": [{"name": "PMM Summit",
                                    "website": "https://pmm.example"}],
                        "note": "found one"})
    r = D.search_category(R.CAT_VERTICAL_SUMMIT, PROFILE)
    assert r["status"] == D.STATUS_OK
    assert r["events"][0]["category"] == R.CAT_VERTICAL_SUMMIT


def test_a_category_that_genuinely_has_nothing_reports_empty(monkeypatch):
    _stub(monkeypatch, {"events": [],
                        "note": "No free vendor conferences serve this niche."})
    r = D.search_category(R.CAT_FREE_VENDOR, PROFILE)
    assert r["status"] == D.STATUS_EMPTY
    assert "niche" in r["note"]


def test_a_category_whose_search_failed_reports_error_not_empty(monkeypatch):
    """The distinction the module exists for. 'Nothing serves this niche' is a
    finding about the market; 'the search failed' is a hole in the analysis.
    Both render as an absence unless they are kept apart here."""
    _stub(monkeypatch, error={"kind": "transport", "detail": "HTTP 503"})
    r = D.search_category(R.CAT_FREE_VENDOR, PROFILE)
    assert r["status"] == D.STATUS_ERROR
    assert "503" in r["detail"]


def test_an_unreadable_reply_is_an_error_rather_than_an_empty_category(monkeypatch):
    _stub(monkeypatch, text="I could not find anything useful, sorry.")
    r = D.search_category(R.CAT_EMERGING, PROFILE)
    assert r["status"] == D.STATUS_ERROR


def test_clean_event_rejects_a_non_http_website_and_sources(monkeypatch):
    _stub(monkeypatch, {"events": [{
        "name": "X", "website": "javascript:alert(1)",
        "sources": ["https://ok.example", "javascript:x", 7],
        "attendees": "9,000+", "organizer_run": True,
        "matchmaking_evidence": "Hosted buyer programme."}]})
    ev = D.search_category(R.CAT_EMERGING, PROFILE)["events"][0]
    assert ev["website"] is None
    assert ev["sources"] == ["https://ok.example"]
    assert ev["attendees"] == "9,000+"
    assert ev["organizer_run"] is True


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
