"""Phase 7: the two reasons the first roster was unusable.

Both defects produced the same thing on screen, a short roster that looked
complete, and both are now visible. Pagination is followed rather than
silently stopping at page one, and a client-rendered shell is reported as a
page we could not read rather than an event with no exhibitors.
"""

import json

import pytest

from tracker import claude_websearch
from tracker import event_intel_harvest as H
from tracker import event_intel_recover as R
from tracker import event_intel_store as store


# ── Pagination ────────────────────────────────────────────────────────────

def _t(*hrefs):
    return " ".join("Page [%s]" % h for h in hrefs)


def test_later_pages_of_one_listing_are_followed_in_order():
    text = _t("https://ev.com/exhibitors?page=3",
              "https://ev.com/exhibitors?page=2",
              "https://ev.com/exhibitors?page=4")
    assert H.next_page_links(text, "https://ev.com/exhibitors?page=1") == [
        "https://ev.com/exhibitors?page=2",
        "https://ev.com/exhibitors?page=3",
        "https://ev.com/exhibitors?page=4"]


def test_a_page_with_no_pagination_parameter_starts_at_one():
    text = _t("https://ev.com/exhibitors?page=2")
    assert H.next_page_links(text, "https://ev.com/exhibitors") == [
        "https://ev.com/exhibitors?page=2"]


def test_earlier_pages_are_not_followed_back_into_a_loop():
    text = _t("https://ev.com/exhibitors?page=1", "https://ev.com/exhibitors?page=2",
              "https://ev.com/exhibitors?page=5")
    got = H.next_page_links(text, "https://ev.com/exhibitors?page=5")
    assert got == []


def test_another_host_is_never_treated_as_the_next_page():
    text = _t("https://other.com/exhibitors?page=2")
    assert H.next_page_links(text, "https://ev.com/exhibitors?page=1") == []


def test_another_path_is_never_treated_as_the_next_page():
    """A ?page=2 on a different path is a different listing, and merging it
    silently mixes two rosters into one."""
    text = _t("https://ev.com/speakers?page=2")
    assert H.next_page_links(text, "https://ev.com/exhibitors?page=1") == []


def test_a_filtered_view_is_not_the_next_page():
    """Only the pagination parameter may differ. `?page=2&category=fintech` is
    a slice of the directory, not the next page of the one being read."""
    text = _t("https://ev.com/exh?page=2&category=fintech")
    assert H.next_page_links(text, "https://ev.com/exh?page=1") == []
    same = _t("https://ev.com/exh?category=fintech&page=2")
    assert H.next_page_links(same, "https://ev.com/exh?category=fintech&page=1") == [
        "https://ev.com/exh?category=fintech&page=2"]


@pytest.mark.parametrize("param", ["page", "pg", "p", "paged", "offset", "start"])
def test_the_common_pagination_parameter_names_are_recognised(param):
    text = _t("https://ev.com/exh?%s=2" % param)
    assert H.next_page_links(text, "https://ev.com/exh?%s=1" % param) == [
        "https://ev.com/exh?%s=2" % param]


def test_the_page_limit_is_respected():
    text = _t(*["https://ev.com/exh?page=%d" % n for n in range(2, 40)])
    assert len(H.next_page_links(text, "https://ev.com/exh?page=1", limit=5)) == 5


def test_a_duplicate_link_to_one_page_is_followed_once():
    text = _t("https://ev.com/exh?page=2", "https://ev.com/exh?page=2")
    assert H.next_page_links(text, "https://ev.com/exh?page=1") == [
        "https://ev.com/exh?page=2"]


# ── harvest_page walking the listing ──────────────────────────────────────

def _stub_fetch(monkeypatch, pages):
    """pages: {url: (text, status)}"""
    def fake(url):
        text, status = pages.get(url, ("", store.SOURCE_ERROR))
        return {"url": url, "status": status, "http_status": 200 if status == store.SOURCE_OK else 500,
                "text": text, "note": "", "truncated": False, "spa": None}
    monkeypatch.setattr(H, "fetch_page", fake)


def _stub_extract(monkeypatch, by_url):
    def fake(page_text, page_url, page_kind, event_name, event_host=""):
        return {"rows": [dict(r, source_url=page_url) for r in by_url.get(page_url, [])],
                "note": "", "error": None}
    monkeypatch.setattr(H, "extract_participants", fake)


def _row(name):
    return {"org_name": name, "org_domain": None, "role": "exhibitor",
            "person_name": None, "person_title": None, "tier": None, "booth": None}


def test_a_paginated_directory_is_read_past_page_one(monkeypatch):
    """The single largest undercount in the first version of this agent."""
    p1 = "https://ev.com/exh?page=1"
    p2 = "https://ev.com/exh?page=2"
    _stub_fetch(monkeypatch, {p1: (_t(p2), store.SOURCE_OK),
                              p2: ("no more links", store.SOURCE_OK)})
    _stub_extract(monkeypatch, {p1: [_row("Acme")], p2: [_row("Beta")]})
    got = H.harvest_page({"url": p1, "kind": "exhibitors"}, "Ev")
    assert sorted(r["org_name"] for r in got["rows"]) == ["Acme", "Beta"]
    assert got["source"]["pages_read"] == 2
    assert "Followed 2 of 2 pages" in got["source"]["note"]


def test_a_company_repeated_on_every_page_is_counted_once(monkeypatch):
    p1, p2 = "https://ev.com/exh?page=1", "https://ev.com/exh?page=2"
    _stub_fetch(monkeypatch, {p1: (_t(p2), store.SOURCE_OK),
                              p2: ("", store.SOURCE_OK)})
    _stub_extract(monkeypatch, {p1: [_row("Acme"), _row("Beta")],
                                p2: [_row("Acme"), _row("Gamma")]})
    got = H.harvest_page({"url": p1, "kind": "exhibitors"}, "Ev")
    assert sorted(r["org_name"] for r in got["rows"]) == ["Acme", "Beta", "Gamma"]
    assert "1 duplicate rows across pages were merged" in got["source"]["note"]


def test_a_walk_that_stops_early_says_where_it_stopped(monkeypatch):
    """A partial read is fine. A partial read that looks complete is not."""
    p1, p2 = "https://ev.com/exh?page=1", "https://ev.com/exh?page=2"
    _stub_fetch(monkeypatch, {p1: (_t(p2), store.SOURCE_OK),
                              p2: ("", store.SOURCE_BLOCKED)})
    _stub_extract(monkeypatch, {p1: [_row("Acme")]})
    got = H.harvest_page({"url": p1, "kind": "exhibitors"}, "Ev")
    assert [r["org_name"] for r in got["rows"]] == ["Acme"]
    assert "Stopped following this listing at page 2 of 2" in got["source"]["note"]


def test_a_listing_longer_than_the_limit_says_it_is_incomplete(monkeypatch):
    urls = ["https://ev.com/exh?page=%d" % n for n in range(1, 8)]
    pages = {u: (_t(*urls), store.SOURCE_OK) for u in urls}
    _stub_fetch(monkeypatch, pages)
    _stub_extract(monkeypatch, {u: [_row("Org%d" % i)] for i, u in enumerate(urls)})
    got = H.harvest_page({"url": urls[0], "kind": "exhibitors"}, "Ev", max_pages=3)
    assert got["source"]["pages_read"] == 3
    assert "more pages than the 3-page limit" in got["source"]["note"]


def test_every_row_from_a_page_read_is_marked_as_page_read(monkeypatch):
    p1 = "https://ev.com/exh"
    _stub_fetch(monkeypatch, {p1: ("text", store.SOURCE_OK)})
    _stub_extract(monkeypatch, {p1: [_row("Acme")]})
    got = H.harvest_page({"url": p1, "kind": "exhibitors"}, "Ev")
    assert got["rows"][0]["provenance"] == store.VIA_PAGE


# ── Client-rendered shells ────────────────────────────────────────────────

@pytest.mark.parametrize("markup", [
    '<div id="root"></div>', "<script>window.__NEXT_DATA__={}</script>",
    '<div id="__next"></div>', '<html ng-app="x">', '<div data-reactroot>',
    '<div id="app"></div>', "<script>window.__NUXT__={}</script>",
])
def test_a_browser_rendered_shell_is_recognised(markup):
    assert H.client_render_marker(markup)


def test_ordinary_server_rendered_markup_carries_no_marker():
    assert H.client_render_marker(
        "<html><body><table><tr><td>Acme</td></tr></table></body></html>") is None


def test_an_empty_shell_is_reported_as_unread_not_as_an_event_with_nobody(monkeypatch):
    """The two look identical on screen and mean opposite things."""
    p1 = "https://ev.com/exh"

    def fake(url):
        return {"url": url, "status": store.SOURCE_OK, "http_status": 200,
                "text": "Home About Contact " * 40, "note": "",
                "truncated": False, "spa": "root"}
    monkeypatch.setattr(H, "fetch_page", fake)
    _stub_extract(monkeypatch, {})
    got = H.harvest_page({"url": p1, "kind": "exhibitors"}, "Ev")
    assert got["source"]["status"] == store.SOURCE_BLOCKED
    assert "built in the browser" in got["source"]["note"]
    assert "not evidence the event has no exhibitors" in got["source"]["note"]


def test_a_page_that_genuinely_lists_nobody_stays_readable(monkeypatch):
    """Over-applying the marker would turn every real empty page into a
    failure, which is the opposite error and just as wrong."""
    p1 = "https://ev.com/exh"

    def fake(url):
        return {"url": url, "status": store.SOURCE_OK, "http_status": 200,
                "text": "The exhibitor list will be published in June. " * 20,
                "note": "", "truncated": False, "spa": None}
    monkeypatch.setattr(H, "fetch_page", fake)
    _stub_extract(monkeypatch, {})
    got = H.harvest_page({"url": p1, "kind": "exhibitors"}, "Ev")
    assert got["source"]["status"] == store.SOURCE_OK
    assert got["rows"] == []


def test_a_page_that_lists_people_is_never_downgraded_for_carrying_a_widget(monkeypatch):
    p1 = "https://ev.com/exh"

    def fake(url):
        return {"url": url, "status": store.SOURCE_OK, "http_status": 200,
                "text": "Acme", "note": "", "truncated": False, "spa": "root"}
    monkeypatch.setattr(H, "fetch_page", fake)
    _stub_extract(monkeypatch, {p1: [_row("Acme")]})
    got = H.harvest_page({"url": p1, "kind": "exhibitors"}, "Ev")
    assert got["source"]["status"] == store.SOURCE_OK


# ── Recovery by search ────────────────────────────────────────────────────

def _stub_ask(monkeypatch, payload=None, searches=3, error=None, text=None):
    def fake_ask(system, user, **kw):
        return {"text": text if text is not None else json.dumps(payload or {}),
                "error": error, "search_count": searches}
    monkeypatch.setattr(claude_websearch, "ask", fake_ask)


def test_a_recovered_listing_is_marked_recovered_not_read(monkeypatch):
    _stub_ask(monkeypatch, {"rows": [
        {"org_name": "Acme", "role": "exhibitor",
         "found_at": "https://blog.example.com/exhibitors"}], "note": ""})
    got = R.recover_page("https://ev.com/exh", "exhibitors", "Ev")
    assert got["source"]["status"] == store.SOURCE_RECOVERED
    assert got["rows"][0]["provenance"] == store.VIA_SEARCH
    assert "found published elsewhere rather than parsed" in got["source"]["note"]


def test_a_recovered_row_points_at_the_page_it_was_actually_seen_on(monkeypatch):
    """A row has to point at something a reader can open, and the page that
    failed is not that."""
    _stub_ask(monkeypatch, {"rows": [
        {"org_name": "Acme", "role": "exhibitor",
         "found_at": "https://news.example.com/list"}]})
    got = R.recover_page("https://ev.com/exh", "exhibitors", "Ev")
    assert got["rows"][0]["source_url"] == "https://news.example.com/list"


def test_a_recovery_that_ran_no_searches_is_thrown_away(monkeypatch):
    """Zero searches means the answer came from training data, which is the
    one failure that would fill a roster with plausible companies."""
    _stub_ask(monkeypatch, {"rows": [
        {"org_name": "Acme", "role": "exhibitor",
         "found_at": "https://x.com/a"}]}, searches=0)
    got = R.recover_page("https://ev.com/exh", "exhibitors", "Ev")
    assert got["rows"] == []
    assert got["source"]["status"] != store.SOURCE_RECOVERED
    assert "without running a single search" in got["source"]["note"]


def test_a_recovered_row_with_no_citation_is_dropped_and_counted(monkeypatch):
    _stub_ask(monkeypatch, {"rows": [
        {"org_name": "Acme", "role": "exhibitor", "found_at": "https://x.com/a"},
        {"org_name": "Ghost", "role": "exhibitor"},
        {"org_name": "Phantom", "role": "exhibitor", "found_at": "somewhere"}]})
    got = R.recover_page("https://ev.com/exh", "exhibitors", "Ev")
    assert [r["org_name"] for r in got["rows"]] == ["Acme"]
    assert "2 recovered rows named no source page" in got["source"]["note"]


def test_a_recovered_row_with_an_unknown_role_is_dropped(monkeypatch):
    _stub_ask(monkeypatch, {"rows": [
        {"org_name": "Acme", "role": "attendee", "found_at": "https://x.com/a"}]})
    assert R.recover_page("https://ev.com/exh", "exhibitors", "Ev")["rows"] == []


def test_a_failed_recovery_says_so_rather_than_returning_an_empty_listing(monkeypatch):
    _stub_ask(monkeypatch, error={"kind": "overloaded", "detail": "529"})
    got = R.recover_page("https://ev.com/exh", "exhibitors", "Ev")
    assert "Recovery by search failed" in got["source"]["note"]
    assert got["source"]["status"] == store.SOURCE_ERROR


def test_an_unreadable_recovery_reply_is_an_error_not_an_empty_listing(monkeypatch):
    _stub_ask(monkeypatch, text="I could not find it.")
    got = R.recover_page("https://ev.com/exh", "exhibitors", "Ev")
    assert "could not be read" in got["source"]["note"]
    assert got["rows"] == []


def test_recovery_never_derives_a_domain_from_a_name(monkeypatch):
    _stub_ask(monkeypatch, {"rows": [
        {"org_name": "Acme", "role": "exhibitor", "org_domain": "linkedin.com/acme",
         "found_at": "https://x.com/a"}]})
    assert R.recover_page("https://ev.com/exh", "exhibitors", "Ev")[
        "rows"][0]["org_domain"] is None


@pytest.mark.parametrize("status,expected", [
    (store.SOURCE_BLOCKED, True), (store.SOURCE_ERROR, True),
    (store.SOURCE_OK, False), (store.SOURCE_RECOVERED, False),
    (store.SOURCE_NOT_FOUND, False),
])
def test_only_a_page_that_exists_and_will_not_open_is_worth_recovering(status, expected):
    """Searching for the contents of a 404 invites a model to find something
    adjacent and present it as the thing."""
    assert R.should_recover({"status": status}) is expected


# ── Provenance at the storage boundary ────────────────────────────────────

def test_the_two_provenance_grades_have_distinct_wording():
    labels = set(store.PROVENANCE_LABELS.values())
    assert len(labels) == len(store.PROVENANCE)
    assert any("could not be read" in v for v in labels)


def test_an_unrecognised_provenance_falls_back_to_the_weaker_grade():
    """Overstating the evidence is the dangerous direction. Understating it
    only makes a real row look softer than it is."""
    import inspect
    src = inspect.getsource(store.save_participants)
    assert "else VIA_SEARCH" in src
    assert "else VIA_PAGE" not in src


# ── the flattener, against the markup real event sites actually ship ──────
#
# Every test below was a live defect. The parser is what lookup, discover and
# workroom all stand on: a roster with no domains cannot be resolved, enriched,
# or matched against the client's own account list, so losing a link here
# empties three modes at once.

def test_a_logo_inside_its_own_link_keeps_the_link():
    """The single most common sponsor and exhibitor markup on the web: a grid
    of linked logos where the company name exists only in the alt text. The
    alt text was written past the open anchor, so the anchor had no label, and
    an anchor with no label had its href discarded. Every sponsor tier came
    back as names with no domains."""
    html = ('<ul class="sponsors">'
            '<li><a href="https://acme-corp.de/">'
            '<img src="/logos/acme.svg" alt="Acme Corp GmbH"></a></li>'
            '<li><a href="https://beta.io">'
            '<img src="/l/b.png" alt="Beta Ltd"></a></li></ul>')
    text = H.html_to_linked_text(html, "https://ev.example/sponsors")
    assert "Acme Corp GmbH [https://acme-corp.de/]" in text
    assert "Beta Ltd [https://beta.io]" in text


def test_a_logo_beside_a_link_still_contributes_its_alt_text():
    """The sibling case, which already worked and must keep working."""
    html = '<li><img alt="Initech Software" src="/l.png"><a href="/x">More</a></li>'
    text = H.html_to_linked_text(html, "https://ev.example/e")
    assert "Initech Software" in text


def test_a_card_wrapped_in_a_link_does_not_run_its_fields_together():
    """Card-layout directories are the norm. The block separators were written
    outside the label being collected, so the company name arrived as
    "Acme IncBooth 402Hall 4" and the booth number was swallowed into it."""
    html = ('<a href="https://acme.com"><div class="card"><h3>Acme Inc</h3>'
            '<span>Booth 402</span><p>Hall 4</p></div></a>')
    text = H.html_to_linked_text(html, "https://ev.example/e")
    assert "Acme Inc Booth 402 Hall 4 [https://acme.com]" in text
    assert "AcmeInc" not in text and "IncBooth" not in text


def test_an_href_carrying_entities_is_not_decoded_twice():
    """HTMLParser has already decoded the attribute. A second unescape reached
    the URLs this class had just inlined, and html.unescape resolves the legacy
    entity names without a trailing semicolon, so "&reg=" and "&sect=" were
    rewritten into symbols and the stored source_url was a dead link."""
    html = '<a href="https://x.com/dir?type=exh&amp;reg=EU&amp;sect=3">Acme</a>'
    text = H.html_to_linked_text(html, "https://ev.example/e")
    assert "https://x.com/dir?type=exh&reg=EU&sect=3" in text


def test_a_plain_anchor_is_unaffected():
    html = '<li><a href="https://plain.com">Plain Co</a> Booth 12</li>'
    text = H.html_to_linked_text(html, "https://ev.example/e")
    assert "Plain Co [https://plain.com]" in text
    assert "Booth 12" in text


# ── the host is not always a company ──────────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://web.cvent.com/event/abc/exhibitor/xyz",
    "https://acme.bizzabo.com/",
    "https://whova.com/portal/exhibitor/acme",
    "https://next.brella.io/companies/123",
    "https://myevent.sched.com/x",
    "https://lu.ma/revops-breakfast",
    "https://acme.eventbrite.co.uk/",
    "https://t.co/abc",
    "https://hubs.ly/x",
])
def test_an_event_platform_profile_is_never_a_company_domain(url):
    """A directory hosted on one of these links each exhibitor to its
    in-platform profile. Without this every row on the floor resolves to the
    platform, and the client is shown a roster where all two hundred
    exhibitors are Whova Inc, ~200 employees, Boston."""
    assert H.clean_domain(url) is None


def test_a_real_company_link_still_resolves():
    assert H.clean_domain("https://www.acme.com/about") == "acme.com"
