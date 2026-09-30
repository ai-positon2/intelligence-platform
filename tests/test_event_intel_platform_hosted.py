"""An event whose website is a listing on a shared platform (Luma,
Eventbrite) keeps its own links, and only its own.

organizer_url became strict on shared platform hosts on 2026-09-30: without
the listing's path nothing there is the organizer's. Every caller that only
passed the hostname then lost the event's own registration and agenda
links, so the callers now pass the website itself."""
from tracker import event_intel_access as ACC
from tracker import event_intel_harvest as H
from tracker import event_intel_store as store

LISTING = "https://lu.ma/cmo-summit"
TEXT = ("Register now [https://lu.ma/cmo-summit/register]\n"
        "Buy tickets [https://lu.ma/someone-elses-meetup]\n")


def test_the_listing_keeps_its_own_links_and_only_those():
    got = ACC.discover(TEXT, LISTING, LISTING)
    assert [l["url"] for l in got] == ["https://lu.ma/cmo-summit/register"]


def test_a_bare_platform_host_proves_nothing():
    assert ACC.discover(TEXT, LISTING, "lu.ma") == []


def test_harvest_passes_the_website_to_the_organizer_checks(monkeypatch):
    monkeypatch.setattr(H, "fetch_page", lambda url: {
        "url": url, "final_url": url, "status": store.SOURCE_OK, "http_status": 200,
        "text": TEXT, "note": "", "truncated": False, "spa": None, "redirected": False})
    monkeypatch.setattr(H, "extract_participants", lambda *a, **k: {"rows": [], "note": "", "error": None})
    got = H.harvest_page({"url": LISTING, "kind": "speakers", "event_website": LISTING},
                         "CMO Summit", "lu.ma")
    assert [l["url"] for l in got["source"]["access_links"]] == ["https://lu.ma/cmo-summit/register"]
