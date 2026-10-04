"""Recommend audit, 2026-10-04: a third-party review of the recommend play.

Each test is one way a client was shown fewer events than exist, or a
broken run as a finding about their market.
"""

import itertools
import json
from datetime import date, timedelta

import pytest

from tracker import claude_websearch
from tracker import event_intel_admission as A
from tracker import event_intel_audit as AU
from tracker import event_intel_discover as D
from tracker import event_intel_pipeline as P
from tracker import event_intel_policy as POL
from tracker import event_intel_report as REP
from tracker import event_intel_rubric as R
from tests.test_event_intel_recommend import PROFILE, _FakeStore, _cand, _wire


# ── D1. geography ──

def _ev(city="", country="", location=""):
    soon = (date.today() + timedelta(days=90)).isoformat()
    site = "https://ev.example/"
    return {"name": "Ev", "starts_on": soon, "ends_on": soon, "city": city, "country": country,
            "location": location, "website": site, "sources": [site], "confidence": "high"}


GEO = "The location could not be verified against the client geography."


@pytest.mark.parametrize("scope,event,ok", [
    ("Mainly US", _ev("Las Vegas", "United States"), True),
    ("Primarily the United States", _ev("Austin", ""), True),
    ("US-based", _ev(location="Mandalay Bay, Las Vegas, NV"), True),
    ("Mainly US", _ev(location="Grand Sierra Resort, Reno, NV"), True),
    ("Mainly US", _ev("Boise, Idaho", ""), True),
    ("USA & Canada", _ev("Toronto", ""), True),
    ("Middle East", _ev("Dubai", ""), True),
    ("GCC", _ev("Riyadh", "Saudi Arabia"), True),
    ("DACH", _ev("Cologne", ""), True),
    ("LATAM", _ev("Sao Paulo", "Brazil"), True),
    ("EMEA", _ev("Cape Town", ""), True),
    ("Nordics", _ev("Stockholm", ""), True),
    ("APAC", _ev("Manila", ""), True),
    ("APAC", _ev("Hong Kong", ""), True),
    ("UK", _ev("London", "England"), True),
    ("Europe", _ev("Prague", "Czech Republic"), True),
    ("Bay Area", _ev("San Francisco Bay Area", ""), True),
    ("Global", _ev("Anywhere", ""), True),
    # Still refused where it really is outside.
    ("DACH", _ev("Paris", "France"), False),
    ("Mainly US", _ev("London", "UK"), False),
    ("Middle East", _ev("", ""), False),
])
def test_a_scope_is_read_for_the_places_it_means(scope, event, ok):
    reasons = POL.eligibility(event, {"geo_scope": scope})
    assert (GEO not in reasons) is ok, (scope, event, reasons)


# ── D2. a broken check is a hole, not a market ──

def _ask(confirm):
    n = itertools.count()

    def fake(system, user, **kw):
        if user.startswith('Confirm "'):
            return confirm(user.split('"')[1])
        k = next(n)
        return {"text": json.dumps({"candidates": [{"name": "Real Event %d" % k,
                                                    "website": "https://re%d.example/" % k}],
                                    "search_complete": True}),
                "error": None, "search_count": 3}
    return fake


def test_every_confirmation_cut_off_is_not_a_finding_about_the_market(monkeypatch):
    fake = _FakeStore()
    _wire(monkeypatch, fake)
    monkeypatch.setattr(claude_websearch, "ask", _ask(lambda name: {
        "text": '{"confirmed": true, "event": {"name": "X", "starts_on": "2027-0',
        "error": {"kind": claude_websearch.ERR_MAX_TOKENS, "detail": "stop_reason=max_tokens"},
        "search_count": 6, "stop_reason": "max_tokens"}))
    P._run_recommend(1, "me@p2.example", PROFILE)
    s = fake.runs[1]["summary"]
    assert s["completion_state"] != "complete"
    assert "a finding about this client's market" not in s["note"]
    assert "did not finish" in s["note"]


def test_events_found_without_dates_are_not_a_finding_about_the_market(monkeypatch):
    fake = _FakeStore()
    _wire(monkeypatch, fake)

    def confirm(name):
        site = "https://%s.example/" % name.replace(" ", "").lower()
        ev = {"name": name, "website": site, "starts_on": None, "ends_on": None,
              "edition": "2027", "country": "USA", "city": "Austin", "confidence": "high",
              "availability": "open", "sources": [site], "category_fit": "Dates TBA."}
        return {"text": json.dumps({"confirmed": True, "facts_complete": True, "event": ev}),
                "error": None, "search_count": 2, "result_urls": [site]}
    monkeypatch.setattr(P.event_intel_discover, "_recover_dates", lambda ev, d: None)
    monkeypatch.setattr(claude_websearch, "ask", _ask(confirm))
    P._run_recommend(1, "me@p2.example", dict(PROFILE, geo_scope="North America"))
    note = fake.runs[1]["summary"]["note"]
    assert "a finding about this client's market" not in note
    assert "could not be confirmed" in note and "not proof the market has none" in note


def test_a_broken_check_is_unfinished_and_an_unsettled_one_is_not():
    broke = {"status": "partial", "detail": "1 of the 2 candidates found here could not be "
             "checked to a conclusion (The check could not be completed: it ran out of room)."}
    unsettled = {"status": "partial", "detail": "1 of the 2 candidates found here could not be "
                 "checked to a conclusion (The edition dates need confirmation.)."}
    assert P.category_gap_kind(broke) == P.GAP_UNFINISHED
    assert P.category_gap_kind(unsettled) == P.GAP_UNRESOLVED


def test_the_report_does_not_call_a_finished_search_unfinished():
    row = {"label": "Side event", "status": "partial",
           "why": "1 of the 2 candidates found here could not be checked to a conclusion "
                  "(This edition is sold out; access must be resolved before recommending "
                  "attendance.)."}
    got = REP.notes(shortfall=[row], audit={}, generic={}, candidates=[], scoring_errors=[],
                    interchangeable=[], banned=[], thin=[], unscored=[])
    heads = [n["head"] for n in got]
    assert not any("did not finish" in h for h in heads)
    assert any("could not be confirmed" in h for h in heads)


# ── D3. a region the client named is a place ──

def test_excluding_a_regional_edition_keeps_the_others():
    excl = "Money20/20 Europe, Shoptalk Europe, Web Summit Vancouver"
    usa = {"name": "Money20/20", "website": "https://us.money2020.com/", "city": "Las Vegas"}
    lisbon = {"name": "Web Summit", "website": "https://websummit.com/", "city": "Lisbon"}
    assert not D._excluded("Money20/20", excl, usa)
    assert not D._excluded("Web Summit", excl, lisbon)
    assert D._excluded("Money20/20 Europe 2027", excl)
    assert D._excluded("Money20/20", "Money20/20 Europe", {"city": "Amsterdam"})
    # A line with no region still means the whole series.
    assert D._excluded("Money20/20 Europe", "Money20/20")
    # Where the event is held unknown: not excluded on a region it may not be in.
    assert not D._excluded("Money20/20", "Money20/20 Europe")
    assert not D._excluded("Money20/20", "Money20/20 Europe", {})


def test_merge_reads_where_each_event_is_held():
    cat = R.CATEGORIES[0]
    ams = {"name": "Money20/20", "website": "https://europe.money2020.com/", "city": "Amsterdam"}
    vegas = {"name": "Money20/20", "website": "https://us.money2020.com/", "city": "Las Vegas"}
    assert D.merge({cat: [ams]}, force_exclude="Money20/20 Europe") == []
    assert len(D.merge({cat: [vegas]}, force_exclude="Money20/20 Europe")) == 1
    assert D.merge({cat: [ams]}, force_include="Money20/20 Europe")[0]["committed"] is True
    assert D.merge({cat: [vegas]}, force_include="Money20/20 Europe")[0]["committed"] is False


def test_a_commitment_to_one_edition_is_not_a_commitment_to_another():
    keys = D.committed_keys("Money20/20 Europe")
    assert not D.is_committed("Money20/20", keys, {"city": "Las Vegas", "country": "USA"})
    m = D.merge({R.CATEGORIES[0]: [{"name": "Money20/20", "website": "https://us.money2020.com/",
                                    "city": "Las Vegas"}]}, force_include="Money20/20 Europe")
    assert m[0]["committed"] is False


def test_a_regional_alternative_is_looked_up_beside_the_series_flagship():
    audit = {"cut": [{"name": "Sibos", "alternative": "Money20/20 Europe"}]}
    alts = AU.alternatives_to_promote(audit, [{"name": "Money20/20", "city": "Las Vegas"}])
    assert [a["name"] for a in alts] == ["Money20/20 Europe"]
    assert AU.alternatives_to_promote(audit, [{"name": "Money20/20 Europe"}]) == []


# ── D4 and D5. one events page is many events ──

@pytest.mark.parametrize("a,b,site", [
    ("Dreamforce", "Salesforce World Tour NYC", "https://www.salesforce.com/events/"),
    ("Revenue Leaders Dinner Austin", "GTM Happy Hour SF", "https://lu.ma/"),
])
def test_two_events_on_one_listing_page_stay_two(a, b, site):
    props = [{"name": a, "website": site, "why": "w"}, {"name": b, "website": site, "why": "w"}]
    assert [p["name"] for p in D._dedupe_proposals(props)] == [a, b]


def test_one_event_page_still_dedupes_a_renamed_side_event():
    props = [{"name": "Northwind Field Day", "website": "https://ops.example/dinner", "why": "w"},
             {"name": "Revenue Leaders Dinner", "website": "https://www.ops.example/dinner/", "why": "w"}]
    assert len(D._dedupe_proposals(props)) == 1


def test_a_confirmation_cannot_swap_in_another_event_from_the_same_company(monkeypatch):
    def fake(system, user, **kw):
        ev = {"name": "Dreamforce", "website": "https://www.salesforce.com/dreamforce/",
              "starts_on": (date.today() + timedelta(days=60)).isoformat(),
              "ends_on": (date.today() + timedelta(days=62)).isoformat(),
              "country": "USA", "city": "San Francisco", "confidence": "high",
              "sources": ["https://www.salesforce.com/dreamforce/"]}
        return {"text": json.dumps({"confirmed": True, "facts_complete": True, "event": ev}),
                "error": None, "search_count": 2,
                "result_urls": ["https://www.salesforce.com/dreamforce/"]}
    monkeypatch.setattr(claude_websearch, "ask", fake)
    prop = {"name": "Salesforce World Tour NYC",
            "website": "https://www.salesforce.com/events/world-tour/nyc/"}
    r = D._confirm_event(prop, "free_vendor", {"geo_scope": "North America"}, {})
    assert r["kind"] == D.CONFIRM_UNCHECKED and "identity changed" in r["reason"]


# ── D6. a long address is not a failed run ──

def test_a_venue_address_in_the_city_field_does_not_fail_the_save(monkeypatch):
    long_city = "Mandalay Bay Resort and Casino Convention Center, 3950 S Las Vegas Blvd, " \
                "Las Vegas, NV 89119, United States of America, North Hall Level 2"
    assert len(long_city) > 120
    fake = _FakeStore()
    _wire(monkeypatch, fake)
    good = _cand("Good Event", city=long_city)
    monkeypatch.setattr(P.event_intel_discover, "discover", lambda profile: {
        "candidates": [good], "by_category": {}, "statuses": {}, "shortfall": [],
        "categories_searched": 6, "categories_failed": 0, "found": 1})
    monkeypatch.setattr(P.event_intel_audit, "audit_famous", lambda c, p: {
        "checked": 0, "error": None, "cut": [], "kept": [], "verdicts": {}})
    monkeypatch.setattr(P.event_intel_scorer, "score_all", lambda c, p: {
        "scored": [dict(x, relevance=36, dm_access=34, engagement=17) for x in c],
        "unscored": [], "errors": [], "batches": 1})
    P._run_recommend(1, "me@p2.example", dict(PROFILE, geo_scope="Global"))
    assert fake.runs[1]["status"] == "complete", fake.runs[1].get("error")


# ── D7. no programme is no bonus ──

@pytest.mark.parametrize("text,bonus", [
    ("Matchmaking is not offered by the organizer.", 0),
    ("There is no hosted buyer programme at this edition.", 0),
    ("Organizer-run matchmaking was discontinued after the 2024 edition.", 0),
    ("A no-cost hosted buyer programme: the organizer matches each buyer with pre-qualified suppliers.", 10),
    ("Hosted buyer programme where the organizer pre-schedules one-to-one meetings with exhibitors.", 10),
])
def test_matchmaking_the_evidence_says_is_not_there_earns_nothing(text, bonus):
    assert R.matchmaking_bonus(True, text)["bonus"] == bonus


# ── D8 and live run 29. organizer pages in the formats organizers use ──

def _page(text):
    return lambda url: {"status": "ok", "text": text, "http_status": 200}


@pytest.mark.parametrize("name,start,end,text", [
    ("DMEXCO", "2026-09-16", "2026-09-17", "DMEXCO 2026: 16. und 17. September 2026, Koelnmesse"),
    ("Zukunft Personal Europe", "2026-09-15", "2026-09-17",
     "Zukunft Personal Europe | 15.09. - 17.09.2026 | Koelnmesse"),
    ("VivaTech", "2027-06-16", "2027-06-19", "VivaTech 2027 : du 16 au 19 juin 2027, Paris"),
    ("MEDICA", "2026-11-16", "2026-11-19", "MEDICA 2026 | 16.-19.11.2026 | Duesseldorf"),
    ("Salon Tech", "2027-03-03", "2027-03-05", "Salon Tech: del 3 al 5 de marzo de 2027"),
    ("HR Tech", "2026-10-20", "2026-10-22", "HR Tech, October 20-22 | Mandalay Bay. Register for 2026."),
    ("HITEC® (Hospitality Industry Technology Exposition and Conference)", "2027-06-28",
     "2027-07-01", "Future Locations\nHITEC 2027 - Orlando\nJun 28\n–Jul 1, 2027\n"),
])
def test_an_organizer_page_is_read_in_its_own_language_and_name(name, start, end, text):
    site = "https://ev.example/"
    r = A.inspect({"name": name, "starts_on": start, "ends_on": end, "website": site,
                   "sources": [site]}, _page(text))
    assert r["support"] != "unverified", (name, r["reasons"])


def test_a_year_less_date_on_a_page_naming_two_years_is_not_enough():
    site = "https://ev.example/"
    r = A.inspect({"name": "HR Tech", "starts_on": "2026-10-20", "ends_on": "2026-10-22",
                   "website": site, "sources": [site]},
                  _page("HR Tech, October 20-22 | Mandalay Bay. 2025 recap and 2026 tickets."))
    assert r["support"] == "unverified"


# ── live run 39 (Cvent, 2026-10-04): organizer pages that date the event plainly ──

def _inspect(name, start, end, city, text, site="https://ev.example/"):
    return A.inspect({"name": name, "starts_on": start, "ends_on": end, "website": site,
                      "sources": [site], "city": city}, _page(text))


IMEX_SHOWS = ("Our shows IMEX America Where the global industry converges in a standout Las "
              "Vegas setting, for the world's largest meetings industry trade show. Next show "
              "dates: October 13-15, 2026. IMEX Frankfurt Where global meeting planners connect. "
              "Next show dates: May 11-13, 2027.")


def test_a_dates_label_belongs_to_the_event_named_just_before_it():
    assert _inspect("IMEX America", "2026-10-13", "2026-10-15", "Las Vegas",
                    IMEX_SHOWS)["support"] != "unverified"
    assert _inspect("IMEX Frankfurt", "2027-05-11", "2027-05-13", "Frankfurt",
                    IMEX_SHOWS)["support"] != "unverified"
    # Another show's dates under the label are not this one's.
    assert _inspect("IMEX Frankfurt", "2026-10-13", "2026-10-15", "Frankfurt",
                    IMEX_SHOWS)["support"] == "unverified"


@pytest.mark.parametrize("name,start,end,city,text", [
    ("PCMA Convening Leaders", "2027-01-10", "2027-01-13", "Miami Beach",
     "Translate this page: PCMA Convening Leaders 2027 Miami Beach Miami Beach, Florida • "
     "January 10-13, 2027 From Fluency to Influence"),
    ("FICP Annual Conference", "2026-11-15", "2026-11-18", "New Orleans",
     "Mark your calendars to join us for the 2026 FICP Annual Conference at the Hyatt Regency "
     "New Orleans, New Orleans from November 15-18, 2026."),
])
def test_the_events_own_city_and_venue_between_name_and_dates_are_its_own(name, start, end, city, text):
    assert _inspect(name, start, end, city, text)["support"] != "unverified"


def test_another_event_at_the_same_city_is_still_not_this_one():
    text = "Join us for the 2026 FICP Payments Summit at the Hyatt Las Vegas from November 15-18, 2026."
    assert _inspect("FICP", "2026-11-15", "2026-11-18", "Las Vegas", text)["support"] == "unverified"
    text = "Acme Summit 2026 Chicago, IL November 15-18, 2026"
    assert _inspect("Acme Summit", "2026-11-15", "2026-11-18", "Las Vegas", text)["support"] == "unverified"


def test_one_invite_only_session_or_a_booked_diary_does_not_close_the_event():
    d = (date(2027, 1, 10), date(2027, 1, 13))
    assert A._access("Chapter Leaders Workshop (Invite Only)\n9:00 AM", "PCMA Convening Leaders", d)[1] == []
    assert A._access("“Our calendar has been fully booked every day from 10am to 4pm.”",
                     "IMEX Frankfurt", d)[1] == []
    assert A._access("PCMA Convening Leaders (Invite Only)\n9:00 AM", "PCMA Convening Leaders", d)[1]
    assert A._access("This summit is invite only for CFOs.", "CFO Summit", d)[1]
    assert A._access("The show is fully booked for 2027.", "IMEX Frankfurt", d)[1]


def test_the_new_rules_stay_narrow():
    # A dates label after a sentence naming another event too.
    text = ("Acme Summit is co-located with the Beta Expo 2026 conference. "
            "Next show dates: November 15-18, 2026.")
    assert _inspect("Acme Summit", "2026-11-15", "2026-11-18", "Austin", text)["support"] == "unverified"
    # A venue in another city is not this edition's own.
    text = "Acme Summit 2026 at the Hilton Chicago from November 15-18, 2026."
    assert _inspect("Acme Summit", "2026-11-15", "2026-11-18", "Las Vegas", text)["support"] == "unverified"
    # More than a venue between the name and the dates.
    text = ("2026 Acme Annual Conference after our partners and sponsors meet at New Orleans "
            "from November 15-18, 2026.")
    assert _inspect("Acme Annual Conference", "2026-11-15", "2026-11-18", "New Orleans",
                    text)["support"] == "unverified"
    # "Sold out" after a meetings word is still the event selling out.
    d = (date(2027, 1, 10), date(2027, 1, 13))
    assert A._access("Our meetings programme is sold out.", "Acme Summit", d)[1]
