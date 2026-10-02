"""Company websites read off an event's own profile pages, and the roster
drawer that shows them (live run 35, Web Summit, 2026-10-02).

Three of the first four live rosters printed no website on any row, because
their lists link each company to a profile on the event's site and print the
website there. With nothing to look companies up by, every row said "not
looked up" and no button could change it. These pin the reader that fixes
that, the rules that stop it guessing, and the drawer's layout.
"""
import json
import os
import sys
import time

import pytest

from tracker import event_intel_harvest as H
from tracker import event_intel_pipeline as P
from tracker import event_intel_profiles as PR
from tracker import event_intel_resolve as RS

sys.path.insert(0, os.path.dirname(__file__))
from test_event_intel_honesty import _node_available, _run_fixture, _run_in_node  # noqa: E402

LIST = "https://ev.example/startups/"


def _listing(*pairs):
    return [(LIST, "Startups\n" + "\n".join("%s [%s]" % p for p in pairs) +
             "\nAbout us [https://about.ev.example/]\nSister event [https://sister.example/]")]


def _profile(*links):
    return "<html><body>%s</body></html>" % "".join(
        '<a href="%s"%s>%s</a>' % (h, (' aria-label="%s"' % lab) if lab else "", txt)
        for h, lab, txt in links)


# ── which profile belongs to which company ──

def test_a_company_is_matched_to_its_profile_by_name_or_slug():
    rows = [{"org_name": "Acrab AI"}, {"org_name": "Checkout.com"}, {"org_name": "Has Site", "org_domain": "x.io"}]
    links = PR.profile_links(rows, _listing(
        ("Acrab AI", "https://ev.example/appearances/1/acrab-ai"),
        ("Checkout.com Payments & Payments Technology Checkout.com",
         "https://ev.example/sponsors/checkoutcom"),
        ("Has Site", "https://ev.example/appearances/2/has-site")))
    assert links == {0: "https://ev.example/appearances/1/acrab-ai",
                     1: "https://ev.example/sponsors/checkoutcom"}


def test_a_link_off_the_listing_host_is_never_a_profile():
    rows = [{"org_name": "Acme"}]
    assert PR.profile_links(rows, _listing(("Acme", "https://acme.example/"))) == {}


def test_a_company_linked_to_two_profiles_is_left_alone():
    rows = [{"org_name": "Acme"}]
    assert PR.profile_links(rows, _listing(("Acme", "https://ev.example/p/1/acme"),
                                           ("Acme", "https://ev.example/p/2/acme"))) == {}


def test_a_profile_claimed_by_two_companies_is_left_alone():
    rows = [{"org_name": "Acme"}, {"org_name": "Globex"}]
    assert PR.profile_links(rows, _listing(("Globex", "https://ev.example/p/acme"))) == {}


def test_one_company_listed_under_two_roles_shares_its_profile():
    rows = [{"org_name": "Acme", "role": "exhibitor"}, {"org_name": "ACME", "role": "sponsor"}]
    links = PR.profile_links(rows, _listing(("Acme", "https://ev.example/p/acme")))
    assert links == {0: "https://ev.example/p/acme", 1: "https://ev.example/p/acme"}


def test_a_label_that_only_contains_the_name_does_not_match():
    """"ABC" is not "ABC Technologies": the label has to be the name, open or
    close with it AND the slug carry it, or the slug has to be the name."""
    rows = [{"org_name": "ABC"}]
    assert PR.profile_links(rows, _listing(
        ("ABC Technologies", "https://ev.example/p/abc-technologies"))) == {}


# ── which outside link is the website ──

def test_the_one_outside_link_is_the_website():
    links = PR.outside_links(_profile(("https://www.linkedin.com/company/acrab/", "", ""),
                                      ("https://acrab.ai", "", ""),
                                      ("https://x.com/acrab", "", "")), "https://ev.example/p")
    assert PR.choose_website(links, set()) == "acrab.ai"


def test_a_link_labelled_website_wins_over_others():
    links = PR.outside_links(_profile(("https://acme.com/", "website", ""),
                                      ("https://partner.example/", "", "Our partner")),
                             "https://ev.example/p")
    assert PR.choose_website(links, set()) == "acme.com"


def test_two_unlabelled_outside_links_are_not_guessed_between():
    links = PR.outside_links(_profile(("https://a.example/", "", ""),
                                      ("https://b.example/", "", "")), "https://ev.example/p")
    assert PR.choose_website(links, set()) is None


def test_a_footer_link_mentioning_website_is_not_a_website_label():
    """Money20/20's footer carries "Website Terms of Use" on the organiser's
    own domain: a word match would have picked it on every profile."""
    links = PR.outside_links(_profile(("https://organiser.example/terms", "", "Website Terms of Use"),
                                      ("https://acme.com/", "", "")), "https://ev.example/p")
    # Not labelled, so two unlabelled domains: neither is chosen.
    assert PR.choose_website(links, set()) is None
    # With the organiser's domain known as the site's own, the company's is.
    assert PR.choose_website(links, {"organiser.example"}) == "acme.com"


def test_the_event_site_under_a_subdomain_counts_as_the_event():
    assert PR._with_parent("us.money2020.com") == {"us.money2020.com", "money2020.com"}
    assert PR._with_parent("shop.x.co.uk") == {"shop.x.co.uk", "x.co.uk"}
    assert PR._with_parent("x.co.uk") == {"x.co.uk"}


# ── the whole pass ──

def test_fill_sets_only_published_websites_and_records_every_outcome():
    rows = [{"org_name": "Acrab AI"}, {"org_name": "Blank"}, {"org_name": "Gone"},
            {"org_name": "Banner One"}, {"org_name": "Banner Two"}]
    pages = {
        "https://ev.example/p/acrab-ai": _profile(("https://acrab.ai", "", ""),
                                                  ("https://sister.example/", "", "")),
        "https://ev.example/p/blank": _profile(("https://www.linkedin.com/company/blank", "", "")),
        "https://ev.example/p/banner-one": _profile(("https://bigsponsor.example/", "", "")),
        "https://ev.example/p/banner-two": _profile(("https://bigsponsor.example/", "", "")),
    }
    calls = []

    def fetch(url):
        calls.append(url)
        return pages.get(url)
    st = PR.fill_websites(rows, _listing(*[(r["org_name"], "https://ev.example/p/" +
                                            r["org_name"].lower().replace(" ", "-")) for r in rows]),
                          "ev.example", fetch=fetch)
    # sister.example is on the listing itself, so it is the site's furniture.
    assert rows[0]["org_domain"] == "acrab.ai"
    assert rows[0]["evidence"]["profile_lookup"]["status"] == PR.FOUND
    assert not rows[1].get("org_domain")
    assert rows[1]["evidence"]["profile_lookup"]["status"] == PR.NO_WEBSITE
    assert rows[2]["evidence"]["profile_lookup"]["status"] == PR.UNREADABLE
    # One domain on two companies' profiles belongs to neither.
    assert not rows[3].get("org_domain") and not rows[4].get("org_domain")
    assert st == {"profiles": 5, "read": 4, "websites": 1, "unreadable": 1, "left": 0,
                  "no_profile": 0, "details": 1}
    # The profile with no website still gave its LinkedIn page.
    assert rows[1]["evidence"]["profile_detail"] == {
        "linkedin": "https://www.linkedin.com/company/blank"}
    # The unreadable page is asked twice before it is written off.
    assert calls.count("https://ev.example/p/gone") == 2
    assert "found 1 website" in PR.note(st) and "1 profile page could not be read" in PR.note(st)


def test_a_settled_row_is_not_read_again_but_an_unreadable_one_is():
    rows = [{"org_name": "Done", "evidence": {"profile_lookup": {"status": PR.NO_WEBSITE}}},
            {"org_name": "Retry", "evidence": {"profile_lookup": {"status": PR.UNREADABLE}}}]
    calls = []
    PR.fill_websites(rows, _listing(("Done", "https://ev.example/p/done"),
                                    ("Retry", "https://ev.example/p/retry")),
                     fetch=lambda u: calls.append(u) or _profile(("https://retry.io", "", "")))
    assert calls == ["https://ev.example/p/retry"] and rows[1]["org_domain"] == "retry.io"


def test_a_row_its_listing_links_to_no_profile_is_settled_not_offered_forever():
    """A partner logo wall links nobody to a profile. Unrecorded, those rows
    kept the offer to look on screen after every press (harness, run 35)."""
    rows = [{"org_name": "Logo Only", "source_url": LIST},
            {"org_name": "Other Page", "source_url": "https://ev.example/partners/"}]
    st = PR.fill_websites(rows, _listing(), fetch=lambda u: pytest.fail("fetched"))
    assert rows[0]["evidence"]["profile_lookup"]["status"] == PR.NO_PROFILE
    # A row from a listing that was not read this time is left open.
    assert "profile_lookup" not in (rows[1].get("evidence") or {})
    assert st["no_profile"] == 1
    assert P.websites_pending([dict(r, provenance="page") for r in rows]) == 1


def test_a_passed_deadline_opens_nothing_and_says_what_is_left():
    rows = [{"org_name": "Acme"}]
    st = PR.fill_websites(rows, _listing(("Acme", "https://ev.example/p/acme")),
                          deadline=time.monotonic() - 1, fetch=lambda u: pytest.fail("fetched"))
    assert st["left"] == 1 and "1 more was not opened yet" in PR.note(st)
    assert not (rows[0].get("evidence") or {}).get("profile_lookup")


def test_a_reader_that_raises_never_takes_the_roster_down(monkeypatch):
    monkeypatch.setattr(PR, "profile_links", lambda *a: 1 / 0)
    assert PR.fill_websites([{"org_name": "A"}], _listing())["websites"] == 0


def test_the_harvest_reads_profiles_for_rows_without_a_website(monkeypatch):
    text = "Exhibitors\nAcme [https://e.example/p/acme]\n" + "x" * 500
    monkeypatch.setattr(H, "fetch_page", lambda url: {
        "url": url, "final_url": url, "status": "ok", "http_status": 200, "text": text,
        "note": "", "truncated": False, "spa": None, "redirected": False,
        "structured_events": [], "titles": []})
    monkeypatch.setattr(H, "_extract_chunk", lambda t, *a, **k: {
        "rows": [dict(org_name="Acme", role="exhibitor")], "note": "", "error": None})
    monkeypatch.setattr(PR, "_fetch", lambda url: _profile(("https://acme.io/", "", "")))
    got = H.harvest_page({"url": "https://e.example/x", "kind": "exhibitors"}, "E", "e.example")
    assert got["rows"][0]["org_domain"] == "acme.io"
    assert got["source"]["profile_websites"]["websites"] == 1
    assert "found 1 website published there" in got["source"]["note"]


# ── a finished run, backfilled ──

def test_a_finished_run_is_backfilled_and_its_estimate_recomputed(monkeypatch):
    parts = [{"id": 1, "event_id": 9, "org_name": "Acme", "org_domain": None,
              "provenance": "page", "evidence": {}, "role": "exhibitor"},
             {"id": 2, "event_id": 9, "org_name": "Found Elsewhere", "org_domain": None,
              "provenance": "search", "evidence": {}, "role": "exhibitor"}]
    written, updated = [], []
    S = P.store
    monkeypatch.setattr(S, "get_run", lambda rid, em: {"id": rid, "summary": {"keep": 1}})
    monkeypatch.setattr(S, "get_participants", lambda rid: parts)
    monkeypatch.setattr(S, "get_events", lambda rid: [{"id": 9, "website": "https://e.example"}])
    monkeypatch.setattr(S, "get_sources", lambda rid: [
        {"event_id": 9, "url": LIST, "kind": "exhibitors", "status": "ok",
         "metadata": {"snapshots": [{"url": LIST}, {"url": LIST + "?page=2"}]}},
        {"event_id": 9, "url": "https://e.example/reg", "kind": "access_review", "status": "ok"}])
    fetched = []

    def page(url):
        fetched.append(url)
        return {"status": "ok", "final_url": url,
                "text": "Acme [https://ev.example/p/acme]\nFound Elsewhere [https://ev.example/p/found-elsewhere]"}
    monkeypatch.setattr(H, "fetch_page", page)
    monkeypatch.setattr(PR, "_fetch", lambda url: _profile(("https://acme.io/", "", "")) +
                        "<p>Singapore</p>")

    def upd(u):
        written.extend(u)
        for pid, dom, rec, det in u:
            for p in parts:
                if p["id"] == pid:
                    p["org_domain"], p["evidence"] = dom, {"profile_lookup": rec,
                                                           "profile_detail": det}
        return len(u)
    monkeypatch.setattr(S, "update_participant_websites", upd)
    monkeypatch.setattr(S, "update_run", lambda rid, **f: updated.append(f))
    monkeypatch.setattr(P, "_summarise", lambda rid: {"cost_estimate": {"domains": 1}})
    out = P.find_run_websites(5, "a@position2.com")
    assert fetched == [LIST, LIST + "?page=2"]
    # A row recovered by search did not come from these listings.
    assert [w[0] for w in written] == [1] and written[0][1] == "acme.io"
    # What the profile said is saved with the website, not dropped.
    assert written[0][3] == {"tags": ["Singapore"]}
    assert updated == [{"summary": {"keep": 1, "cost_estimate": {"domains": 1}}}]
    assert out["websites"] == 1 and out["pending"] == 0


def test_the_route_refuses_a_run_still_in_progress(monkeypatch):
    import app as appmod
    from tracker import event_intel_store
    monkeypatch.setattr(event_intel_store, "get_run", lambda rid, em: {"id": rid, "status": "running"})
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "harness@position2.com", "name": "T"}
    r = c.post("/p2/strategic-agents/event-conference-intelligence/runs/5/websites")
    assert r.status_code == 409


# ── the organiser line ──

@pytest.mark.parametrize("raw,clean", [
    ("Web Summit (company founded by Paddy Cosgrave)", "Web Summit"),
    ("Informa (UBM)", "Informa (UBM)"),
    ("Money20/20", "Money20/20"),
    ("(a b c)", "(a b c)"),
])
def test_a_description_after_the_organiser_name_is_dropped(raw, clean):
    assert RS.organizer_name(raw) == clean


# ── the drawer ──

def _render(run):
    out = _run_in_node("render(%s); console.log(JSON.stringify(__html));" % json.dumps(run), None)
    return json.loads(out)


def _web_summit_like():
    run = _run_fixture()
    for p in run["participants"]:
        p["org_domain"] = None
    run["events"][0]["organizer"] = "Widget Media (a company founded by somebody)"
    run["summary"]["cost_estimate"] = {"domains": 0, "batches": 0, "max_credits": 0, "note": ""}
    return run


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_columns_with_nothing_in_them_are_not_drawn():
    html = _render(_web_summit_like())["drawerBody"]
    assert ">Person<" not in html and ">Firmographics<" not in html
    assert "not looked up" not in html and "no website published" not in html
    assert "No website on record for any of these 2" in html


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_a_roster_with_no_websites_offers_to_find_them():
    html = _render(_web_summit_like())["drawerBody"]
    assert 'id="websitesBtn"' in html and "Read 2 company profiles" in html
    assert 'id="eviCompanyData"' in html and "Read their profiles" in html


@pytest.mark.skipif(not _node_available(), reason="node is not available")
@pytest.mark.parametrize("settled", ["no_website", "no_profile", "found"])
def test_settled_rows_are_not_offered_again(settled):
    run = _web_summit_like()
    for p in run["participants"]:
        p["evidence"] = {"profile_lookup": {"status": settled}}
    assert 'id="websitesBtn"' not in _render(run)["drawerBody"]


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_an_unreadable_profile_is_offered_again():
    run = _web_summit_like()
    for p in run["participants"]:
        p["evidence"] = {"profile_lookup": {"status": "unreadable"}}
    assert 'id="websitesBtn"' in _render(run)["drawerBody"]


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_a_few_named_people_go_under_their_company_not_in_a_column():
    run = _web_summit_like()
    extra = [dict(run["participants"][0], id=10 + i, org_name="Co %d" % i) for i in range(6)]
    run["participants"] += extra
    run["participants"][0].update(person_name="Pat Doe", person_title="CTO")
    html = _render(run)["drawerBody"]
    assert ">Person<" not in html and '<div class="evi-person">Pat Doe' in html
    for p in run["participants"][:4]:
        p["person_name"] = "Someone"
    assert ">Person<" in _render(run)["drawerBody"]


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_a_website_read_from_a_profile_says_where_it_came_from():
    run = _run_fixture()
    run["participants"][1].update(org_domain="globex.io", evidence={
        "profile_lookup": {"status": "found", "profile_url": "https://widgetexpo.com/p/globex"}})
    html = _render(run)["drawerBody"]
    assert 'href="https://widgetexpo.com/p/globex"' in html and "via profile" in html


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_one_source_is_said_once_and_many_get_a_column():
    run = _run_fixture()
    html = _render(run)["drawerBody"]
    assert ">From<" in html and ">Exhibitor list<" in html and ">Sponsor list<" in html
    run["participants"][1]["source_url"] = run["participants"][0]["source_url"]
    html = _render(run)["drawerBody"]
    assert ">From<" not in html and "All from the event's" in html


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_the_organiser_line_drops_the_description():
    out = _render(_web_summit_like())
    assert "organised by Widget Media" in out["drawerSub"]
    assert "founded" not in out["drawerSub"]


def test_the_header_links_sit_in_their_own_row():
    """As bare inline anchors their padding painted the buttons over the event
    line (live run 35). They sit in a flex row that hides when both are."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    page = open(os.path.join(here, "templates", "event_conference_intelligence.html")).read()
    css = open(os.path.join(here, "static", "css", "event_conference_intelligence.css")).read()
    head = page[page.index('<div class="evi-drawer-head">'):page.index('id="drawerBody"')]
    assert head.index('class="evi-drawer-actions"') < head.index('id="eventPlanLink"')
    assert ".evi-drawer-actions .evi-btn[hidden] { display: none; }" in css
    assert "text-decoration: none" in css[css.index(".evi-drawer-actions .evi-btn {"):][:300]


# ── what the profile says about the company (2026-10-03) ──

def test_a_doubled_scheme_in_a_published_link_is_repaired():
    """Web Summit Qatar's Accenture, Google and Snapchat profiles publish
    "http:// http://www.accenture.com"; read as written, three real
    websites were reported as none."""
    assert PR.repair_href("http:// http://www.accenture.com") == "http://www.accenture.com"
    assert PR.repair_href("www.acme.com/x") == "https://www.acme.com/x"
    assert PR.repair_href("https://a.com/?next=https://b.com") == "https://a.com/?next=https://b.com"
    links = PR.outside_links(_profile(("http:// http://www.accenture.com", "website", "")),
                             "https://ev.example/p")
    assert PR.choose_website(links, set()) == "accenture.com"


def test_a_company_that_is_a_platform_keeps_its_own_labelled_site():
    links = PR.outside_links(_profile(("https://www.tiktok.com/", "website", "")), "https://ev.example/p")
    assert PR.choose_website(links, set(), org_name="TikTok") == "tiktok.com"
    links = PR.outside_links(_profile(("https://cloud.google.com/", "website", "")), "https://ev.example/p")
    assert PR.choose_website(links, set(), org_name="Google") == "google.com"
    # Another company's page on that platform is never its website.
    links = PR.outside_links(_profile(("https://www.tiktok.com/@acme", "website", "")), "https://ev.example/p")
    assert PR.choose_website(links, set(), org_name="Acme") is None


NAV = ["Home [https://ev.example/]", "Meet our partners [https://ev.example/partners/]", "Company"]


def _ws_lines(name, country, industry):
    return NAV + [name, "website [https://%s.io]" % name.lower(), "See all partners [https://ev.example/partners/]",
                  "PAST PARTNER", "Share", "Share on X", "Copy link", name, country, industry] + NAV


def test_site_lines_go_and_what_the_event_says_about_the_company_stays():
    pages = [_ws_lines("Acme", "Singapore", "Fintech"), _ws_lines("Globex", "Qatar", "AI"),
             _ws_lines("Initech", "Qatar", "AI")]
    chrome = PR.chrome_lines(pages, [(LIST, "\n".join(NAV + ["Acme", "Globex"]))])
    assert set(NAV) <= chrome and "PAST PARTNER" not in chrome
    d = PR.profile_details(pages[0], "Acme", chrome)
    assert d == {"tags": ["PAST PARTNER", "Singapore", "Fintech"]}


def test_labelled_fields_and_an_about_paragraph_are_read_as_printed():
    about = "Checkout.com runs a global payments network for enterprise merchants, " * 3
    lines = ["Back", "Sessions", "Checkout.com", "Payments & Payments Technology", "Checkout.com",
             "Location:", "Money Row - Gjelina - Monday + Tuesday", "About Checkout.com:",
             about.strip(), "Second paragraph of the about text.", "Connect"]
    d = PR.profile_details(lines, "Checkout.com", set())
    assert d["tags"] == ["Payments & Payments Technology"]
    assert d["fields"] == [["Location", "Money Row - Gjelina - Monday + Tuesday"]]
    assert d["about"].startswith("Checkout.com runs") and d["about"].endswith("about text.")
    long_label = PR.profile_details(["About Silicon Valley Bank, A Division of First Citizens Bank:",
                                     "SVB is a bank."], "SVB", set())
    assert long_label == {"about": "SVB is a bank."}


def test_the_events_own_social_links_are_not_the_companys():
    links = [{"href": "https://www.linkedin.com/company/the-event/", "label": "", "text": ""},
             {"href": "https://www.linkedin.com/company/acme/", "label": "", "text": ""},
             {"href": "https://x.com/acme", "label": "", "text": ""}]
    d = PR.profile_details([], "Acme", set(), links, {"https://www.linkedin.com/company/the-event"})
    assert d == {"linkedin": "https://www.linkedin.com/company/acme/", "x": "https://x.com/acme"}


def test_a_profile_read_before_details_existed_is_read_again_without_touching_its_website():
    rows = [{"org_name": "Acme", "org_domain": "acme.io",
             "evidence": {"profile_lookup": {"status": PR.FOUND, "profile_url": "https://ev.example/p/acme"}}}]
    assert P.websites_pending([dict(rows[0], provenance="page")]) == 1
    st = PR.fill_websites(rows, _listing(), fetch=lambda u: _profile(("https://other.io", "", "")) +
                          "<p>Singapore</p>")
    assert rows[0]["org_domain"] == "acme.io" and st["websites"] == 0 and st["read"] == 1
    assert rows[0]["evidence"]["profile_lookup"]["status"] == PR.FOUND
    assert "profile_detail" in rows[0]["evidence"]
    assert P.websites_pending([dict(rows[0], provenance="page")]) == 0


def _with_details(run):
    p0, p1 = run["participants"]
    p0["evidence"] = {"profile_lookup": {"status": "found", "profile_url": "https://widgetexpo.com/p/acme"},
                      "profile_detail": {"tags": ["PAST PARTNER", "Singapore"],
                                         "fields": [["Location", "Hall 2 - B14"]],
                                         "about": "Acme builds robots for warehouses.",
                                         "linkedin": "https://www.linkedin.com/company/acme/"}}
    p1["evidence"] = {"profile_lookup": {"status": "no_website", "profile_url": "https://widgetexpo.com/p/globex"},
                      "profile_detail": {"tags": ["PAST PARTNER", "Qatar"]}}
    return run


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_the_roster_shows_what_the_event_says_and_opens_into_details():
    html = _render(_with_details(_run_fixture()))["drawerBody"]
    assert '<div class="evi-tags-line">PAST PARTNER · Singapore</div>' in html
    assert "Location: Hall 2 - B14" in html
    assert 'onclick="eviRow(1, this)"' in html and 'id="evid-1" hidden' in html
    assert "Acme builds robots for warehouses." in html
    assert 'href="https://www.linkedin.com/company/acme/"' in html
    assert "Not matched yet." in html


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_a_label_on_every_company_is_said_once_above_the_list():
    """All 50 of Web Summit Qatar's partners read PAST PARTNER (live run 36)."""
    run = _with_details(_run_fixture())
    base = dict(run["participants"][0], role="partner")
    run["participants"] = [dict(base, id=20 + i, org_name="Co %d" % i) for i in range(5)]
    run["participants"][0]["evidence"] = dict(run["participants"][0]["evidence"], profile_detail={
        "tags": ["PAST PARTNER", "Singapore"]})
    for p in run["participants"][1:]:
        p["evidence"] = dict(p["evidence"], profile_detail={"tags": ["PAST PARTNER", "Qatar"]})
    html = _render(run)["drawerBody"]
    assert "The event labels every one of these <b>PAST PARTNER</b>" in html
    assert "partners of an earlier edition, not confirmed for this one" in html
    assert '<div class="evi-tags-line">Singapore</div>' in html
    assert 'evi-tags-line">PAST PARTNER' not in html
    # In a mixed list the label is said per role, and still taken off the rows.
    run["participants"].append(dict(base, id=40, role="speaker", org_name="Pat Doe",
                                    evidence={"profile_detail": {"tags": ["Past Featured Speaker"]}}))
    html = _render(run)["drawerBody"]
    assert "The event labels every partner <b>PAST PARTNER</b>" in html
    assert 'evi-tags-line">PAST PARTNER' not in html
    assert '<div class="evi-tags-line">Past Featured Speaker</div>' in html


def test_the_csv_carries_what_the_event_profile_says(monkeypatch):
    import csv
    import io
    import app as appmod
    from tracker import event_intel_store
    run = _with_details(_run_fixture())
    monkeypatch.setattr(event_intel_store, "get_run", lambda rid, em: run)
    monkeypatch.setattr(event_intel_store, "get_participants", lambda rid: run["participants"])
    monkeypatch.setattr(event_intel_store, "get_sources", lambda rid: run["sources"])
    monkeypatch.setattr(event_intel_store, "get_events", lambda rid: run["events"])
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "harness@position2.com", "name": "T"}
    r = c.get("/p2/strategic-agents/event-conference-intelligence/runs/7/export.csv")
    row = next(csv.DictReader(io.StringIO(r.get_data(as_text=True))))
    assert row["Event profile says"] == "PAST PARTNER; Singapore"
    assert row["Event profile details"] == "Location: Hall 2 - B14"
    assert row["About (event profile)"] == "Acme builds robots for warehouses."
    assert row["LinkedIn"] == "https://www.linkedin.com/company/acme/"
    assert row["Event profile page"] == "https://widgetexpo.com/p/acme"


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_a_profile_read_before_details_existed_is_offered_again():
    run = _run_fixture()
    run["participants"][0]["evidence"] = {"profile_lookup": {
        "status": "found", "profile_url": "https://widgetexpo.com/p/acme"}}
    run["participants"][1]["evidence"] = {"profile_lookup": {"status": "no_profile"}}
    html = _render(run)["drawerBody"]
    assert 'id="websitesBtn"' in html and "Read 1 company profile<" in html
