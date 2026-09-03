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

def _stub_fetch(monkeypatch, pages, redirects=None):
    """pages: {url: (text, status)}. redirects: {requested: landed_on}.

    Mirrors the real fetch_page's shape, `final_url` included. A stub that
    omitted it would let every test here pass with the redirect handling
    deleted, because the caller falls back to the requested URL.
    """
    redirects = redirects or {}

    def fake(url):
        final = redirects.get(url, url)
        text, status = pages.get(final, pages.get(url, ("", store.SOURCE_ERROR)))
        return {"url": url, "final_url": final, "status": status,
                "http_status": 200 if status == store.SOURCE_OK else 500,
                "text": text, "note": "", "truncated": False, "spa": None,
                "redirected": final != url}
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


# ── a reply cut off mid-array is unreadable, not an empty page ────────────

def _truncated_rows_reply(n=200, cut=9000):
    """What stop_reason=max_tokens leaves behind: the outer envelope never
    closes, so the first BALANCED object in the reply is the first row."""
    rows = [{"org_name": "Exhibitor Company Number %d Ltd" % i,
             "org_domain": "exhibitor%d.example.com" % i,
             "role": "exhibitor", "booth": "A%d" % i} for i in range(n)]
    return json.dumps({"rows": rows, "note": "page 1 of 3"})[:cut]


def test_a_truncated_reply_does_not_parse_into_its_own_first_row():
    """The defect: that first row is a dict, so every caller's
    isinstance(parsed, dict) guard passed, .get("rows") was None, and a
    300-exhibitor page was recorded as an event that publishes no exhibitors,
    counted under sources_read as successfully read, with no error and no
    recovery attempted. It fires hardest on the densest pages, because those
    are the ones that overflow."""
    cut = _truncated_rows_reply()
    assert claude_websearch.extract_json(cut, require="rows") is None

    loose = claude_websearch.extract_json(cut)
    assert isinstance(loose, dict) and "rows" not in loose, (
        "without require, the first row still parses and looks like an envelope")


def test_a_complete_reply_is_unaffected():
    whole = json.dumps({"rows": [{"org_name": "Acme", "role": "exhibitor"}],
                        "note": "ok"})
    got = claude_websearch.extract_json(whole, require="rows")
    assert got["rows"][0]["org_name"] == "Acme"


@pytest.mark.parametrize("raw", [
    'Here you go: {"rows":[{"org_name":"Acme"}]} hope that helps',
    '```json\n{"rows":[{"org_name":"Acme"}]}\n```',
])
def test_prose_and_fences_still_parse(raw):
    """Models wrap JSON in prose even when told not to, and with web_search on
    the citation-bearing prose is often unavoidable."""
    assert claude_websearch.extract_json(raw, require="rows")["rows"]


def test_the_wrong_envelope_is_refused_rather_than_partly_accepted():
    """And it must not fall through to a bare array found inside it."""
    assert claude_websearch.extract_json('{"scores":[]}', require="rows") is None
    assert claude_websearch.extract_json('[{"a":1}]', require="rows") is None


def test_harvest_reports_a_truncated_reply_as_unreadable_not_as_an_empty_page(
        monkeypatch):
    """The integration point. It is not enough that extract_json can refuse a
    truncated reply; extract_participants has to ask it to. Without the
    envelope name, a dense page comes back rows=0, error=None, which the
    caller records as "this event publishes no exhibitors" and which
    should_recover() then declines to retry because the status is ok."""
    _stub_ask(monkeypatch, text=_truncated_rows_reply())
    out = H.extract_participants("some page text", "https://ev.example/exhibitors",
                                 "exhibitor_list", "Money20/20", "ev.example")
    assert out["rows"] == []
    assert out["error"], "a cut-off reply was reported as a page listing nobody"
    assert out["error"]["kind"] == claude_websearch.ERR_UNPARSABLE


def test_harvest_still_reads_a_complete_reply(monkeypatch):
    _stub_ask(monkeypatch, payload={"rows": [
        {"org_name": "Acme Payments", "org_domain": "acme.com",
         "role": "exhibitor"}], "note": "page 1 of 1"})
    out = H.extract_participants("text", "https://ev.example/exhibitors",
                                 "exhibitor_list", "Money20/20", "ev.example")
    assert out["error"] is None
    assert [r["org_name"] for r in out["rows"]] == ["Acme Payments"]


# ── Pagination carried in the path (WordPress and friends) ────────────────

def test_a_wordpress_archive_paginates_in_its_path():
    """`/exhibitors/page/2/` is how WordPress serves page two of any archive,
    and a query-string-only reader stopped at page one on every one of them."""
    text = _t("https://ev.com/exhibitors/page/2/", "https://ev.com/exhibitors/page/3/")
    assert H.next_page_links(text, "https://ev.com/exhibitors/") == [
        "https://ev.com/exhibitors/page/2/", "https://ev.com/exhibitors/page/3/"]


def test_the_listing_front_page_is_not_followed_back_from_page_two():
    text = _t("https://ev.com/exhibitors/", "https://ev.com/exhibitors/page/3/")
    assert H.next_page_links(text, "https://ev.com/exhibitors/page/2/") == [
        "https://ev.com/exhibitors/page/3/"]


@pytest.mark.parametrize("href", [
    "https://ev.com/exhibitors/page/2/",
    "https://ev.com/exhibitors/page/2",
    "https://ev.com/exhibitors/page-2",
    "https://ev.com/exhibitors/page_2",
    "https://ev.com/exhibitors/pg/2",
])
def test_the_common_path_pagination_shapes_are_recognised(href):
    assert H.next_page_links(_t(href), "https://ev.com/exhibitors/") == [href]


def test_a_bare_numbered_path_is_never_treated_as_a_page():
    """On /speakers/, the link /speakers/42/ is speaker forty-two. Following
    it would pull a profile into the roster AND count it as a directory page
    that had been read, so the roster would look longer and be wronger."""
    assert H.next_page_links(_t("https://ev.com/speakers/42/"),
                             "https://ev.com/speakers/") == []
    assert H.next_page_links(_t("https://ev.com/speakers/2/"),
                             "https://ev.com/speakers/") == []


def test_a_path_paginated_link_on_another_listing_is_not_the_next_page():
    assert H.next_page_links(_t("https://ev.com/speakers/page/2/"),
                             "https://ev.com/exhibitors/") == []


def test_path_and_query_pagination_of_one_listing_still_agree():
    """A site that links its own pages both ways must not produce two rosters."""
    text = _t("https://ev.com/exhibitors/page/2/")
    assert H.next_page_links(text, "https://ev.com/exhibitors") == [
        "https://ev.com/exhibitors/page/2/"]


def test_a_query_filter_still_blocks_a_path_paginated_next_page():
    text = _t("https://ev.com/exhibitors/page/2/?category=fintech")
    assert H.next_page_links(text, "https://ev.com/exhibitors/") == []


# ── Redirects ─────────────────────────────────────────────────────────────

def test_a_redirected_listing_still_follows_its_own_pagination(monkeypatch):
    """/exhibitors -> /2026/exhibitors/ is the normal shape of a site that has
    run for more than one year. Before the final URL was read, every next-page
    link failed the same-listing test and the walk stopped at page one with
    nothing on screen saying so."""
    asked = "https://ev.com/exhibitors"
    landed = "https://ev.com/2026/exhibitors/"
    p2 = "https://ev.com/2026/exhibitors/page/2/"
    _stub_fetch(monkeypatch,
                {landed: (_t(p2), store.SOURCE_OK), p2: ("", store.SOURCE_OK)},
                redirects={asked: landed})
    _stub_extract(monkeypatch, {landed: [_row("Acme")], p2: [_row("Beta")]})
    got = H.harvest_page({"url": asked, "kind": "exhibitors"}, "Ev")
    assert sorted(r["org_name"] for r in got["rows"]) == ["Acme", "Beta"]
    assert got["source"]["pages_read"] == 2


def test_the_ledger_keeps_the_published_url_and_names_the_redirect(monkeypatch):
    """The URL a reader can check against the event's own site is the one the
    event published, so that stays the ledger row. Where it actually went is
    recorded beside it rather than swapped in."""
    asked = "https://ev.com/exhibitors"
    landed = "https://ev.com/2026/exhibitors/"
    _stub_fetch(monkeypatch, {landed: ("text", store.SOURCE_OK)},
                redirects={asked: landed})
    _stub_extract(monkeypatch, {landed: [_row("Acme")]})
    got = H.harvest_page({"url": asked, "kind": "exhibitors"}, "Ev")
    assert got["source"]["url"] == asked
    assert got["source"]["final_url"] == landed


def test_a_row_points_at_the_page_it_was_actually_read_from(monkeypatch):
    """Same rule the recovery path already keeps: the citation is where the
    row was seen, not where we went looking."""
    asked = "https://ev.com/exhibitors"
    landed = "https://ev.com/2026/exhibitors/"
    _stub_fetch(monkeypatch, {landed: ("text", store.SOURCE_OK)},
                redirects={asked: landed})
    _stub_extract(monkeypatch, {landed: [_row("Acme")]})
    got = H.harvest_page({"url": asked, "kind": "exhibitors"}, "Ev")
    assert got["rows"][0]["source_url"] == landed


def test_an_unredirected_page_records_no_redirect(monkeypatch):
    p1 = "https://ev.com/exh"
    _stub_fetch(monkeypatch, {p1: ("text", store.SOURCE_OK)})
    _stub_extract(monkeypatch, {p1: [_row("Acme")]})
    got = H.harvest_page({"url": p1, "kind": "exhibitors"}, "Ev")
    assert got["source"]["final_url"] is None


@pytest.mark.parametrize("a,b", [
    ("https://ev.com/exh", "https://ev.com/exh/"),
    ("https://ev.com/exh", "https://EV.com/exh"),
])
def test_a_cosmetic_redirect_is_not_reported_as_one(a, b):
    """A trailing slash or a host case change has not moved the page, and
    reporting those would put a redirect note on most of the web."""
    assert H._same_page(a, b) is True


def test_a_redirect_to_another_path_is_reported():
    assert H._same_page("https://ev.com/exh", "https://ev.com/2026/exh") is False
    assert H._same_page("https://ev.com/exh", "https://other.com/exh") is False


# ── A listing that declares its own size ──────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("Showing exhibitors. Page 1 of 14", 14),
    ("page 1 / 9", 9),
    ("1 of 23 pages", 23),
    ("Page 1 of 1", None),
    ("no pagination here", None),
    ("Copyright 2026 of 2026", None),
])
def test_a_listing_that_declares_its_page_count_is_read(text, expected):
    assert H.declared_page_count(text) == expected


def test_the_largest_declared_count_wins():
    """A directory footer often carries more than one counter, and the roster
    is short by the bigger of them."""
    assert H.declared_page_count("Page 1 of 3 ... page 1 of 14") == 14


def test_a_listing_we_could_not_walk_says_how_short_it_is(monkeypatch):
    """The case this exists for: the page numbers are a cursor, or a button
    that posts, so there is no link to follow and the old code stopped at page
    one in silence. The page told us it had fourteen."""
    p1 = "https://ev.com/exh"
    _stub_fetch(monkeypatch, {p1: ("Page 1 of 14", store.SOURCE_OK)})
    _stub_extract(monkeypatch, {p1: [_row("Acme")]})
    got = H.harvest_page({"url": p1, "kind": "exhibitors"}, "Ev")
    note = got["source"]["note"]
    assert "says it has 14 pages and 1 was read" in note
    assert "could not be followed" in note
    assert got["source"]["pages_declared"] == 14


def test_a_listing_read_to_its_declared_end_says_nothing_about_it(monkeypatch):
    p1, p2 = "https://ev.com/exh?page=1", "https://ev.com/exh?page=2"
    _stub_fetch(monkeypatch, {p1: ("Page 1 of 2 " + _t(p2), store.SOURCE_OK),
                              p2: ("Page 2 of 2", store.SOURCE_OK)})
    _stub_extract(monkeypatch, {p1: [_row("Acme")], p2: [_row("Beta")]})
    got = H.harvest_page({"url": p1, "kind": "exhibitors"}, "Ev")
    assert "is incomplete" not in got["source"]["note"]
    assert "pages_declared" not in got["source"]


def test_a_declared_shortfall_does_not_blame_the_links_when_links_ran_out(monkeypatch):
    """Two different holes. Followable links that hit the cap is one story;
    no followable links at all is another, and they must not share wording."""
    urls = ["https://ev.com/exh?page=%d" % n for n in range(1, 8)]
    pages = {u: ("Page 1 of 14 " + _t(*urls), store.SOURCE_OK) for u in urls}
    _stub_fetch(monkeypatch, pages)
    _stub_extract(monkeypatch, {u: [_row("Org%d" % i)] for i, u in enumerate(urls)})
    got = H.harvest_page({"url": urls[0], "kind": "exhibitors"}, "Ev", max_pages=3)
    note = got["source"]["note"]
    assert "says it has 14 pages and 3 were read" in note
    assert "could not be followed" not in note
