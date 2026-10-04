"""Lookup audit, 2026-10-04: the roster defects a third-party review found.

Each test is one way the roster showed a client something other than what
the event published: an unread directory reported as empty, last year's list
kept for an edition with no start date, a whole page dropped for one stray
year, companies merged through a shared link host or a shared first word.
"""

from tracker import event_intel_evidence as E
from tracker import event_intel_harvest as H
from tracker import event_intel_store as store
from tracker import event_intel_workroom as W


def _fetch(monkeypatch, text, spa=None):
    def fake(url):
        return {"url": url, "status": store.SOURCE_OK, "http_status": 200, "text": text,
                "note": "", "truncated": False, "spa": spa}
    monkeypatch.setattr(H, "fetch_page", fake)


def _extract(monkeypatch, rows):
    def fake(page_text, page_url, page_kind, event_name, event_host=""):
        return {"rows": [dict(r, source_url=page_url) for r in rows], "note": "", "error": None}
    monkeypatch.setattr(H, "extract_participants", fake)


def test_a_directory_whose_list_could_not_be_seen_is_unread_not_empty(monkeypatch):
    """exhibitors.gitex.com: filter labels, the list behind a search box, no
    framework marker. Live run 37 said "No participants were published"."""
    _fetch(monkeypatch, "Search exhibitors\nCountry\nQatar\nIndia\nCategory\nRobotics\n" * 300)
    _extract(monkeypatch, [])
    got = H.harvest_page({"url": "https://exhibitors.gitex.com/x", "kind": "exhibitors"}, "GITEX")
    assert got["source"]["status"] == store.SOURCE_BLOCKED
    assert "not evidence the event has none" in got["source"]["note"]


def test_a_listing_that_says_its_names_are_to_come_is_a_real_empty(monkeypatch):
    for said in ("Exhibitors coming soon.", "Speakers to be announced",
                 "The sponsor list will be published in June."):
        _fetch(monkeypatch, said)
        _extract(monkeypatch, [])
        got = H.harvest_page({"url": "https://ev.com/exh", "kind": "exhibitors"}, "Ev")
        assert got["source"]["status"] == store.SOURCE_OK, said


def test_an_edition_named_without_a_start_date_still_has_its_year_checked(monkeypatch):
    _fetch(monkeypatch, "Our 2025 Exhibitors\nAcme\nGlobex")
    _extract(monkeypatch, [{"org_name": "Acme", "role": "exhibitor"}])
    for edition in ("Spring 2026", "ACME Congress 2026", "2026-03-10"):
        got = H.harvest_page({"url": "https://ev.com/exh", "kind": "exhibitors",
                              "edition": edition}, "Ev")
        assert got["rows"] == [] and got["source"]["coverage"]["edition_mismatch"], edition


def test_the_pipeline_hands_the_harvest_the_year_from_any_field(monkeypatch):
    from tracker import event_intel_pipeline as P
    seen = []

    def stage(key, fn, page, *a):
        seen.append(page["edition"])
        return {"source": {"status": "error", "url": page["url"], "kind": "exhibitors"},
                "rows": []}
    monkeypatch.setattr(P, "durable_stage", stage)
    monkeypatch.setattr(P.store, "save_source", lambda *a, **k: None)
    monkeypatch.setattr(P.event_intel_recover, "should_recover", lambda src: False)
    for event in ({"starts_on": None, "edition": "Spring 2026"},
                  {"starts_on": None, "edition": None, "name": "ACME Congress 2027"},
                  {"starts_on": "2026-03-10", "edition": "Spring 2027"}):
        P._harvest_event(1, 1, event, [{"url": "https://ev.com/exh", "kind": "exhibitors"}])
    assert seen == ["2026", "2027", "2026"]


def test_one_bio_line_naming_last_year_does_not_withhold_this_years_page():
    text = ("Meet our 2027 speakers\nJane Doe, CEO of Foo\n"
            "Jane was among the 2026 speakers and returns.\n")
    assert E.roster_years(text) == ["2027"]
    assert E.roster_years("Thank you to our 2026 Sponsors\nAcme") == ["2026"]


def test_a_link_in_bio_or_chat_link_is_nobodys_website():
    for url in ("https://linktr.ee/falcondrones", "https://wa.me/97150000000",
                "https://api.whatsapp.com/send?phone=1", "https://t.me/oasis",
                "https://beacons.ai/desertai"):
        assert H.clean_domain(url) is None, url
    assert H.clean_domain("https://falcondrones.ae") == "falcondrones.ae"


def test_companies_that_share_a_first_word_stay_apart_on_the_roster():
    assert W.roster_key("Blue Ocean Technologies") != W.roster_key("Blue Ocean Systems")
    assert W.roster_key("Delta Health Software") != W.roster_key("Delta Health Group")
    assert W.roster_key("Acme Technologies, Inc.") == W.roster_key("Acme Technologies")
    assert W.roster_key("Salesforce.com") == W.roster_key("Salesforce")
    # A booth note still finds its company under either spelling.
    assert W.org_key("Acme Data Technologies") == W.org_key("Acme Data")
