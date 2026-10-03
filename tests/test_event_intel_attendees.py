"""Attendees: the people with public proof of being at an event.

Every check here is about one promise: a person is listed only with proof,
the proof is the source's own words, and someone who merely works at an
exhibitor is never shown as an attendee.
"""

import csv
import io
import json
import os
import sys
from datetime import date

import pytest

from tracker import event_intel_attendees as A
from tracker import event_intel_pipeline as P

sys.path.insert(0, os.path.dirname(__file__))
from test_event_intel_honesty import _node_available, _run_fixture, _run_in_node  # noqa: E402

EVENT = {"name": "Widget Expo 2026", "starts_on": "2026-05-04", "ends_on": "2026-05-06",
         "city": "Chicago", "country": "US", "website": "https://widgetexpo.com"}
TODAY = date(2026, 3, 1)


def _post(pid, text, name="Jane Doe", headline="VP Marketing at Acme", slug="janedoe",
          posted="2026-02-20T10:00:00Z", **author):
    return {"social_id": pid, "share_url": "https://www.linkedin.com/feed/update/" + pid,
            "text": text, "parsed_datetime": posted,
            "author": dict({"name": name, "headline": headline, "public_identifier": slug,
                            "is_company": False}, **author)}


# ── checking words against the source ──

@pytest.mark.parametrize("frag,ok", [
    ("Excited to be at Widget Expo next week", True),
    ("excited   to be AT widget expo", True),            # case and spacing
    ("Excited to be at ... next week", True),             # an ellipsis the model put in
    ("next week ... Excited to be at", False),            # pieces out of order
    ("Excited to attend Widget Expo", False),             # a paraphrase
    ("Excited", False),                                   # too short to be proof
])
def test_a_quote_counts_only_when_it_is_in_the_text(frag, ok):
    assert A.in_text(frag, "Excited to be at Widget Expo next week! Booth 12.") is ok


def test_accents_do_not_stop_a_name_matching_itself():
    assert A.in_text("José Álvarez is speaking", "Jose Alvarez is speaking on day two")


@pytest.mark.parametrize("headline,title,company", [
    ("VP Marketing at Acme | Speaker", "VP Marketing", "Acme"),
    ("Founder @ Globex", "Founder", "Globex"),
    ("Helping founders scale | Keynote speaker", "Helping founders scale", None),
    ("", None, None),
])
def test_a_headline_gives_an_employer_only_when_it_names_one(headline, title, company):
    assert A.split_headline(headline) == (title, company)


def test_the_event_is_matched_by_name_or_hashtag_and_nothing_looser():
    terms = A.event_terms({"name": "Web Summit Qatar 2027"})
    assert '"Web Summit Qatar"' in A.linkedin_queries({"name": "Web Summit Qatar 2027"})
    assert A.mentions_event("See you at #WebSummitQatar", terms)
    assert A.mentions_event("On stage at web summit qatar tomorrow", terms)
    assert not A.mentions_event("A summit about the web, in Qatar", terms)


# ── which edition a post is about ──

def test_dates_overrule_the_model_on_which_edition():
    # Written a year before this edition: an earlier one, whatever the model said.
    assert A.settle_edition("this", "attending", "2025-04-01", EVENT, TODAY) == "earlier"
    # "I was there" about an edition that has not happened yet.
    assert A.settle_edition("this", "attended", "2026-02-01", EVENT, TODAY) == "earlier"
    # Written in the run-up and the model could not tell: this edition.
    assert A.settle_edition("unclear", "attending", "2026-02-20", EVENT, TODAY) == "this"
    # "I'll be there" written long after this edition ended proves nothing about it.
    assert A.settle_edition("this", "attending", "2026-12-20", EVENT, date(2027, 1, 1)) == "unclear"


# ── 1. named by the event ──

def test_people_the_event_names_are_attendees_with_the_page_as_proof():
    rows = A.named_by_event([
        {"event_id": 1, "role": "speaker", "person_name": "Ana Silva", "person_title": "CTO",
         "org_name": "Ana Silva", "source_url": "https://widgetexpo.com/speakers",
         "evidence": {"profile_detail": {"linkedin": "https://www.linkedin.com/in/anasilva",
                                         "tags": ["Past Featured Speaker"]},
                      "profile_lookup": {"profile_url": "https://widgetexpo.com/p/ana"}}},
        {"event_id": 1, "role": "exhibitor", "person_name": "Bo Chen", "org_name": "Acme",
         "org_domain": "acme.io", "source_url": "https://widgetexpo.com/exhibitors",
         "evidence": {"profile_detail": {"linkedin": "https://www.linkedin.com/in/acme-founder"}}},
        {"event_id": 1, "role": "exhibitor", "person_name": None, "org_name": "Globex"},
        {"event_id": 2, "role": "speaker", "person_name": "Other Event", "org_name": "X"},
    ], event_id=1)
    assert [r["name"] for r in rows] == ["Ana Silva", "Bo Chen"]
    ana, bo = rows
    assert ana["basis"] == "event" and ana["status"] == "speaking"
    assert ana["company"] is None                      # the row's org was the person
    assert ana["linkedin"] == "https://www.linkedin.com/in/anasilva"
    assert ana["edition"] == "earlier"                 # the event calls them past
    assert ana["proof"][0]["url"] == "https://widgetexpo.com/p/ana"
    # The LinkedIn link on a company's profile page is the company's, not Bo's.
    assert bo["company"] == "Acme" and bo["linkedin"] is None and bo["status"] == "listed"


# ── 2. LinkedIn posts ──

def test_a_company_page_post_or_an_empty_post_is_not_a_person():
    assert A.linkedin_post(_post("1", "We are exhibiting at Widget Expo", is_company=True)) is None
    assert A.linkedin_post(_post("2", "")) is None
    p = A.linkedin_post(_post("3", "Going to Widget Expo"))
    assert p["author"]["url"] == "https://www.linkedin.com/in/janedoe"
    assert p["author"]["headline"] == "VP Marketing at Acme"


def test_the_linkedin_search_pages_dedupes_and_keeps_only_posts_naming_the_event():
    pages = {
        None: {"items": [_post("1", "Off to Widget Expo!"), _post("2", "Unrelated summit post")],
               "cursor": "c2"},
        "c2": {"items": [_post("1", "Off to Widget Expo!"), _post("3", "#WidgetExpo here")],
               "cursor": None},
    }
    calls = []

    def search(account, keywords=None, cursor=None, limit=50):
        calls.append((keywords, cursor))
        return pages.get(cursor) if keywords.startswith('"') else {"items": []}, None
    out = A.search_linkedin(EVENT, search=search, account_id="acct")
    assert [p["id"] for p in out["posts"]] == ["1", "3"]
    assert calls[0] == ('"Widget Expo"', None) and ('"Widget Expo"', "c2") in calls
    assert out["error"] is None


def test_a_search_that_errors_says_so_and_keeps_what_it_had():
    def search(account, keywords=None, cursor=None, limit=50):
        if cursor:
            return None, {"kind": "http", "status": 429}
        return {"items": [_post("1", "At Widget Expo")], "cursor": "next"}, None
    out = A.search_linkedin(EVENT, search=search, account_id="acct")
    assert [p["id"] for p in out["posts"]] == ["1"]
    assert out["error"]


def test_no_connected_linkedin_account_is_recorded_not_raised(monkeypatch):
    from tracker import unipile_transport
    monkeypatch.setattr(unipile_transport, "account_for_platform", lambda p: None)
    out = A.search_linkedin(EVENT)
    assert out == {"posts": [], "searched": 0, "returned": 0, "error": "no_account"}
    assert "no LinkedIn account is connected" in A.note({"linkedin": out, "counts": {}})


def _reading(*rows):
    def classify(batch, event, today):
        return {"posts": [dict(r) for r in rows], "error": None, "spend": None}
    return classify


def _posts(*texts):
    return [A.linkedin_post(_post(str(i + 1), t)) for i, t in enumerate(texts)]


def test_the_author_is_kept_with_their_headline_when_the_quote_is_theirs():
    posts = _posts("Excited to be speaking at Widget Expo on May 5th about robots.")
    out = A.people_in_posts(posts, EVENT, TODAY, classify=_reading(
        {"id": "p1", "same_event": True, "edition": "this", "people": [
            {"who": "author", "status": "speaking", "quote": "speaking at Widget Expo on May 5th"}]}))
    (p,) = out["people"]
    assert (p["name"], p["title"], p["company"]) == ("Jane Doe", "VP Marketing", "Acme")
    assert p["basis"] == "self" and p["status"] == "speaking" and p["edition"] == "this"
    assert p["linkedin"] == "https://www.linkedin.com/in/janedoe"
    assert p["proof"][0]["quote"] == "speaking at Widget Expo on May 5th"


def test_a_quote_the_post_does_not_contain_keeps_nobody():
    posts = _posts("Widget Expo tickets are on sale now.")
    out = A.people_in_posts(posts, EVENT, TODAY, classify=_reading(
        {"id": "p1", "same_event": True, "people": [
            {"who": "author", "status": "attending", "quote": "I will be at Widget Expo"}]}))
    assert out["people"] == [] and out["rejected"] == 1


def test_a_named_person_must_be_in_the_post_and_keeps_only_words_the_post_says():
    posts = _posts("Great to meet Ravi Kumar from Initech at Widget Expo today!")
    out = A.people_in_posts(posts, EVENT, TODAY, classify=_reading(
        {"id": "p1", "same_event": True, "people": [
            {"who": "named", "name": "Ravi Kumar", "status": "attended", "title": "CEO",
             "company": "Initech", "quote": "Great to meet Ravi Kumar from Initech at Widget Expo"},
            {"who": "named", "name": "Priya Patel", "status": "attended",
             "quote": "Great to meet Ravi Kumar from Initech at Widget Expo"},
            {"who": "named", "name": "Ravi", "status": "attended",
             "quote": "Great to meet Ravi Kumar from Initech at Widget Expo"}]}))
    (p,) = out["people"]
    assert p["name"] == "Ravi Kumar" and p["basis"] == "others"
    assert p["company"] == "Initech" and p["title"] is None   # "CEO" is not in the post
    assert p["linkedin"] is None
    assert p["proof"][0]["label"] == "Named in a LinkedIn post by Jane Doe"
    assert out["rejected"] == 2


def test_another_edition_of_the_series_or_a_non_attendance_lists_nobody():
    posts = _posts("Loved Widget Expo Berlin last month, see you at Widget Expo soon.")
    out = A.people_in_posts(posts, EVENT, TODAY, classify=_reading(
        {"id": "p1", "same_event": False, "people": [
            {"who": "author", "status": "attended", "quote": "Loved Widget Expo Berlin last month"}]}))
    assert out["people"] == []
    out = A.people_in_posts(posts, EVENT, TODAY, classify=_reading(
        {"id": "p1", "same_event": True, "people": [
            {"who": "author", "status": "interested", "quote": "see you at Widget Expo soon"}]}))
    assert out["people"] == [] and out["rejected"] == 0


def test_a_batch_that_could_not_be_read_is_counted(monkeypatch):
    monkeypatch.setattr(A, "RETRY_PAUSE", 0)
    posts = _posts("At Widget Expo")
    out = A.people_in_posts(posts, EVENT, TODAY,
                            classify=lambda b, e, t: {"posts": None, "error": "unparsable"})
    assert out["failed"] == 1 and out["batches"] == 1 and out["unread_posts"] == 1
    assert "1 of the 3 posts found could not be read" in A.note(
        {"linkedin": {"unread_posts": 1, "posts": 1}, "x": {"posts": 2},
         "counts": {"confirmed": 1}})


def test_posts_are_read_in_batches_that_cover_every_post():
    posts = _posts(*["Post %d at Widget Expo" % i for i in range(45)])
    seen = []

    def classify(batch, event, today):
        seen.extend(ref for ref, _ in batch)
        return {"posts": [], "error": None}
    out = A.people_in_posts(posts, EVENT, TODAY, classify=classify)
    assert out["batches"] == 3 and sorted(seen, key=lambda r: int(r[1:])) == [
        "p%d" % i for i in range(1, 46)]


# ── 3. the public web ──

def _no_google(event):
    return {"results": [], "queries": 0, "error": "not_configured"}


def test_pages_are_found_from_every_angle_then_opened_and_read_whole():
    asked = []

    def ask(system, user, **kw):
        asked.append(user.split("Angle: ")[1][:12])
        return {"text": json.dumps({"pages": [
            {"url": "https://news.example.com/a"}, {"url": "https://made.up/never-returned"}]}),
            "error": None, "search_count": 6,
            "result_urls": ["https://news.example.com/a", "https://blog.example.com/b",
                            "https://www.linkedin.com/posts/x", "https://widgetexpo.com/speakers",
                            "https://dead.example.com/c", "https://off.example.com/d"]}
    pages = {"https://news.example.com/a": "At Widget Expo 2026 the speakers include Acme CEO "
                                           "Lee Park, who will speak at Widget Expo in May. "
                                           + "Filler sentence here. " * 40 +
                                           "Also on stage: Mia Wong, CTO of Globex. Bo Chen too.",
             "https://blog.example.com/b": "Recap: Ana Silva attended Widget Expo and loved it.",
             "https://off.example.com/d": "A page about something else entirely, naming Bo Chen."}
    opened = []

    def fetch(url):
        opened.append(url)
        text = pages.get(url)
        return {"status": "ok", "text": text} if text else {"status": "blocked"}

    def read(url, text, event, today):
        if "news" in url:
            return {"people": [
                {"name": "Lee Park", "title": "CEO", "company": "Globex", "status": "speaking",
                 "quote": "Acme CEO Lee Park, who will speak at Widget Expo in May"},
                {"name": "Mia Wong", "title": "CTO", "company": "Initech", "status": "speaking",
                 "quote": "Also on stage: Mia Wong, CTO of Globex"},
                {"name": "Bo Chen", "status": "speaking",
                 "quote": "Bo Chen gave the closing keynote at Widget Expo"},
                {"name": "Invented Person", "status": "speaking",
                 "quote": "the speakers include Acme CEO Lee Park"}]}
        return {"people": [{"name": "Ana Silva", "status": "attended",
                            "quote": "Ana Silva attended Widget Expo"}]}
    out = A.search_web(EVENT, "widgetexpo.com", TODAY, fetch=fetch, ask=ask, read=read,
                       google=_no_google)
    assert len(asked) == len(A.WEB_ANGLES) and out["searches"] == 6 * len(A.WEB_ANGLES)
    # Never the event's own site, LinkedIn, or an address no search returned.
    assert sorted(opened) == ["https://blog.example.com/b", "https://dead.example.com/c",
                              "https://news.example.com/a", "https://off.example.com/d"]
    assert opened.index("https://news.example.com/a") == 0     # the one the search named first
    people = {p["name"]: p for p in out["people"]}
    assert sorted(people) == ["Ana Silva", "Lee Park", "Mia Wong"]
    # Globex is on the page, but next to Mia Wong, not Lee Park.
    assert (people["Lee Park"]["title"], people["Lee Park"]["company"]) == ("CEO", None)
    # "Initech" is not on the page at all.
    assert (people["Mia Wong"]["title"], people["Mia Wong"]["company"]) == ("CTO", None)
    assert people["Ana Silva"]["proof"][0]["label"] == "Named on blog.example.com"
    assert (out["pages"], out["unopened"], out["on_event"], out["read"], out["rejected"]) == (
        4, 1, 2, 2, 2)


def test_a_title_far_from_the_name_on_a_long_page_is_not_theirs():
    text = "Lee Park spoke at Widget Expo. " + "x " * 400 + "Head of Sales at Globex."
    assert not A._near("Head of Sales", "Lee Park", text)
    assert A._near("spoke", "Lee Park", text)


def test_a_long_page_is_read_from_just_before_the_event_is_named():
    text = "menu " * 5000 + "Widget Expo speakers: Lee Park" + " more" * 5000
    part = A.page_excerpt(text, A.event_terms(EVENT))
    assert "Widget Expo speakers: Lee Park" in part and len(part) == A.PAGE_CHARS


def test_a_web_search_that_fails_is_reported():
    out = A.search_web(EVENT, ask=lambda s, u, **k: {"text": "", "error": {"kind": "transport"}},
                       fetch=lambda u: {}, google=_no_google)
    assert out["error"] == "transport" and out["people"] == []
    assert "public pages beyond LinkedIn and X were not checked" in A.note(
        {"web": out, "counts": {"confirmed": 1}})


# ── 4. people at the listed companies ──

def _roster():
    return [
        {"event_id": 1, "role": "exhibitor", "org_name": "Acme", "org_domain": "acme.io",
         "source_url": "https://widgetexpo.com/exhibitors"},
        {"event_id": 1, "role": "sponsor", "org_name": "Globex", "org_domain": "globex.com",
         "source_url": "https://widgetexpo.com/sponsors"},
        {"event_id": 1, "role": "speaker", "org_name": "Solo", "org_domain": "solo.dev"},
        {"event_id": 1, "role": "media", "org_name": "Press", "org_domain": "press.com"},
    ]


def test_staff_are_looked_up_at_exhibitors_and_sponsors_only_and_never_confirmed():
    asked = []

    def find(domains, per_company=5, seniorities=None, titles=None):
        asked.append((list(domains), per_company, seniorities))
        return {"by_domain": {"globex.com": [{"name": "Hank Scorpio", "title": "CEO",
                                              "linkedin": "https://www.linkedin.com/in/hank"}]},
                "error": None}
    out = A.staff_at_companies(_roster(), find=find)
    assert asked == [(["globex.com", "acme.io"], 3, A.STAFF_SENIORITIES)]   # sponsors first
    (p,) = out["people"]
    assert p["basis"] == "staff" and p["status"] == "staff" and p["edition"] == "unclear"
    assert p["proof"][0]["label"] == "Works at Globex, listed by the event as sponsor"
    assert A.BASIS_LABELS["staff"].endswith("(not confirmed)")


def test_staff_lookup_is_chunked_and_capped_and_stops_without_a_key():
    roster = [{"role": "exhibitor", "org_name": "C%d" % i, "org_domain": "c%d.com" % i}
              for i in range(25)]
    calls = []

    def find(domains, **kw):
        calls.append(len(domains))
        return {"by_domain": {}, "error": "APOLLO_API_KEY is not configured on this deployment."}
    out = A.staff_at_companies(roster, find=find, limit=22)
    assert calls == [10] and out["skipped"] == 3
    assert "not switched on" in A.note({"staff": out, "counts": {"confirmed": 1}})


# ── one row per person ──

def test_the_same_person_from_several_sources_is_one_row_with_every_proof():
    rows = A.merge([
        {"name": "Ana Silva", "title": "CTO", "company": None, "linkedin": None, "basis": "event",
         "status": "speaking", "edition": "this", "proof": [{"kind": "event_page"}]},
        {"name": "Ana Silva", "title": "CTO", "company": "Acme",
         "linkedin": "https://www.linkedin.com/in/anasilva", "basis": "self",
         "status": "attending", "edition": "unclear", "proof": [{"kind": "linkedin_post"}]},
        {"name": "Ana Silva", "company": "Acme", "linkedin": "https://linkedin.com/in/AnaSilva/",
         "basis": "staff", "status": "staff", "edition": "unclear", "proof": [{"kind": "apollo"}]},
        {"name": "Ana Silva", "company": "Initech", "linkedin": "https://www.linkedin.com/in/ana-2",
         "basis": "self", "status": "attended", "edition": "earlier", "proof": [{"kind": "x"}]},
    ])
    assert len(rows) == 2
    ana = next(r for r in rows if r["company"] == "Acme")
    assert ana["bases"] == ["event", "self", "staff"] and ana["basis"] == "event"
    assert ana["status"] == "speaking" and ana["edition"] == "this"
    assert [p["kind"] for p in ana["proof"]] == ["event_page", "linkedin_post", "apollo"]


def test_counts_put_each_person_under_their_strongest_proof():
    c = A.counts([{"basis": "event", "edition": "this"}, {"basis": "self", "edition": "earlier"},
                  {"basis": "staff", "edition": "unclear"}])
    assert (c["confirmed"], c["this_edition"], c["earlier_edition"], c["staff"]) == (2, 1, 1, 1)


def test_one_source_failing_never_stops_the_others():
    def boom(*a, **k):
        raise RuntimeError("down")
    got = A.gather(EVENT, [{"role": "speaker", "person_name": "Ana Silva", "org_name": "Acme",
                            "source_url": "https://widgetexpo.com/speakers"}],
                   "widgetexpo.com", today=TODAY,
                   sources={"linkedin": lambda e, d: {"posts": [], "error": "no_account"},
                            "web": boom, "staff": boom})
    assert [r["name"] for r in got["rows"]] == ["Ana Silva"]
    assert got["report"]["web"]["error"] == "error"
    assert got["report"]["staff"]["error"] == "down"
    assert got["rows"][0]["evidence"]["bases"] == ["event"]


# ── the run ──

def test_a_replayed_run_asks_the_attendee_search_the_same_question():
    event = dict(EVENT, id=3)
    a = [{"id": 10, "event_id": 3, "role": "speaker", "person_name": "Ana", "org_name": "X",
          "fetched_at": "2026-01-01", "evidence": {"profile_detail": {"tags": []}, "status": "x"}}]
    b = [dict(a[0], id=99, fetched_at="2026-01-02")]
    assert P.attendee_inputs(event, a) == P.attendee_inputs(dict(event, id=3), b)
    ev, rows = P.attendee_inputs(event, a + [dict(a[0], event_id=4)])
    assert "id" not in ev and len(rows) == 1 and "id" not in rows[0]
    assert rows[0]["evidence"] == {"profile_detail": {"tags": []}}


def test_a_lookup_finds_attendees_after_its_roster_and_stores_them(monkeypatch):
    S = P.store
    saved, finished, stages = [], [], []
    monkeypatch.setattr(S, "update_run", lambda rid, **kw: stages.append(kw.get("stage")))
    monkeypatch.setattr(S, "get_events", lambda rid: [dict(EVENT, id=1)])
    monkeypatch.setattr(S, "get_participants", lambda rid: [])
    monkeypatch.setattr(S, "save_attendees", lambda rid, eid, rows: saved.append((eid, rows)))
    monkeypatch.setattr(S, "finish_attendee_scan", lambda rid, st, res: finished.append((st, res)))
    monkeypatch.setattr(P, "_gather_attendees", lambda ev, rows, host: {
        "rows": [{"name": "Ana", "basis": "self"}], "report": {"usd": 0.2, "counts": {"confirmed": 1}}})
    P._find_attendees(5, 1)
    assert stages == ["finding_attendees"]
    assert saved == [(1, [{"name": "Ana", "basis": "self"}])]
    assert finished[0][0] == "done" and finished[0][1]["events"][0]["event_id"] == 1


def test_an_attendee_search_that_crashes_never_fails_the_roster(monkeypatch):
    S = P.store
    finished = []
    monkeypatch.setattr(S, "update_run", lambda rid, **kw: None)
    monkeypatch.setattr(S, "get_events", lambda rid: [dict(EVENT, id=1)])
    monkeypatch.setattr(S, "get_participants", lambda rid: [])
    monkeypatch.setattr(S, "finish_attendee_scan", lambda rid, st, res: finished.append(st))

    def boom(*a):
        raise RuntimeError("x")
    monkeypatch.setattr(P, "_gather_attendees", boom)
    P._find_attendees(5, 1)
    assert finished == ["failed"]


# ── routes ──

def _client():
    import app as appmod
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "harness@position2.com", "name": "T"}
    return c


@pytest.mark.parametrize("run,code", [
    ({"id": 5, "mode": "lookup", "status": "running"}, 409),
    ({"id": 5, "mode": "recommend", "status": "complete"}, 400),
])
def test_the_route_searches_only_a_finished_lookup(monkeypatch, run, code):
    from tracker import event_intel_store
    monkeypatch.setattr(event_intel_store, "get_run", lambda rid, em: run)
    r = _client().post("/p2/strategic-agents/event-conference-intelligence/runs/5/attendees")
    assert r.status_code == code


def test_a_second_press_while_searching_starts_nothing(monkeypatch):
    from tracker import event_intel_attendees, event_intel_store
    monkeypatch.setattr(event_intel_store, "get_run",
                        lambda rid, em: {"id": rid, "mode": "lookup", "status": "complete"})
    monkeypatch.setattr(event_intel_store, "begin_attendee_scan", lambda rid, em: "busy")
    started = []
    monkeypatch.setattr(event_intel_attendees, "find_for_run", lambda *a: started.append(a))
    r = _client().post("/p2/strategic-agents/event-conference-intelligence/runs/6/attendees")
    assert r.status_code == 202 and r.get_json()["state"] == "running" and not started


def test_the_csv_carries_each_persons_proof_and_never_calls_staff_attendees(monkeypatch):
    from tracker import event_intel_store
    monkeypatch.setattr(event_intel_store, "get_run", lambda rid, em: {"id": rid, "query": "W"})
    monkeypatch.setattr(event_intel_store, "get_events", lambda rid: [{"name": "Widget Expo"}])
    monkeypatch.setattr(event_intel_store, "get_attendees", lambda rid: [
        {"name": "Jane Doe", "title": "VP", "company": "Acme", "linkedin": "https://www.linkedin.com/in/j",
         "basis": "self", "status": "attending", "edition": "this",
         "evidence": {"x": "https://x.com/jane", "proof": [{"label": "Their own LinkedIn post", "quote": "=see you there",
                                 "url": "https://www.linkedin.com/feed/update/1",
                                 "posted_at": "2026-02-20"}]}},
        {"name": "Hank Scorpio", "company": "Globex", "basis": "staff", "status": "staff",
         "edition": "unclear", "evidence": {"proof": [{"label": "Works at Globex"}]}},
    ])
    r = _client().get("/p2/strategic-agents/event-conference-intelligence/runs/8/attendees.csv")
    assert 'widget-expo-attendees.csv' in r.headers["Content-Disposition"]
    jane, hank = list(csv.DictReader(io.StringIO(r.get_data(as_text=True))))
    assert jane["How we know"] == "Said so publicly" and jane["At the event as"] == "Going"
    assert jane["Quote"] == "'=see you there"            # a formula is defused
    assert jane["X profile"] == "https://x.com/jane" and hank["X profile"] == ""
    assert jane["Proof link"] == "https://www.linkedin.com/feed/update/1"
    assert hank["At the event as"] == "Not confirmed"
    assert hank["Caveat"].startswith("Not confirmed attending")


# ── storage ──

@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs Postgres")
def test_attendees_round_trip_and_a_second_save_replaces_the_first():
    from tracker import event_intel_store as S
    run_id = S.save_run("harness@position2.com", "lookup", "Widget Expo")
    rows = [{"name": "Ana", "basis": "event", "status": "speaking", "edition": "this",
             "evidence": {"proof": [{"kind": "event_page"}]}}]
    S.save_attendees(run_id, 1, rows)
    S.save_attendees(run_id, 1, rows + [{"name": "Bo", "basis": "staff"}])
    got = S.get_attendees(run_id)
    assert [g["name"] for g in got] == ["Ana", "Bo"]
    assert got[0]["evidence"]["proof"][0]["kind"] == "event_page"
    assert S.begin_attendee_scan(run_id, "harness@position2.com") == "go"
    assert S.begin_attendee_scan(run_id, "harness@position2.com") == "busy"
    S.finish_attendee_scan(run_id, "done", {"usd": 0.1})
    assert S.get_attendee_scan(run_id)["state"] == "done"
    assert S.begin_attendee_scan(run_id, "harness@position2.com") == "go"


# ── the drawer ──

def _render(run):
    out = _run_in_node("render(%s); console.log(JSON.stringify(__html));" % json.dumps(run), None)
    return json.loads(out)["drawerBody"]


def _with_attendees():
    run = _run_fixture()
    run["attendee_scan"] = {"state": "done", "result": {"events": [{"note": ""}]}}
    run["attendees"] = [
        {"name": "Ana Silva", "title": "CTO", "company": "Acme", "basis": "event",
         "status": "speaking", "edition": "this", "linkedin": "https://www.linkedin.com/in/ana",
         "evidence": {"proof": [{"kind": "event_page", "label": "Listed by the event as speaker",
                                 "url": "https://widgetexpo.com/speakers"}]}},
        {"name": "Jane <b>Doe</b>", "title": "VP", "company": "Initech", "basis": "self",
         "status": "attended", "edition": "earlier", "linkedin": "javascript:alert(1)",
         "evidence": {"proof": [{"kind": "linkedin_post", "label": "Their own LinkedIn post",
                                 "quote": "<script>x</script> was at Widget Expo",
                                 "url": "https://www.linkedin.com/feed/update/1",
                                 "posted_at": "2025-05-06"}]}},
        {"name": "Hank Scorpio", "title": "CEO", "company": "Globex", "basis": "staff",
         "status": "staff", "edition": "unclear",
         "evidence": {"proof": [{"kind": "apollo", "label": "Works at Globex, listed by the event as sponsor"}]}},
    ]
    return run


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_the_drawer_lists_attendees_with_their_proof_and_staff_apart():
    html = _render(_with_attendees())
    sec = html[html.index('id="eviAttendees"'):]
    assert "<b>2 people</b> with public proof" in sec
    assert "(1 for this edition, 1 from an earlier one)" in sec
    assert "Speaking" in sec and "This edition" in sec and "Earlier edition" in sec
    assert "View post" in sec and "May 6, 2025" in sec
    # Untrusted text is escaped and an unsafe link is not drawn as one.
    assert "<script>" not in sec and "&lt;script&gt;" in sec and "Jane &lt;b&gt;Doe" in sec
    assert 'href="javascript' not in sec
    staff = sec[sec.index('class="evi-att-staff"'):]
    assert "not confirmed attending" in staff and "Hank Scorpio" in staff
    assert "Hank Scorpio" not in sec[:sec.index('class="evi-att-staff"')]
    assert "attendees.csv" in sec and "Search again" in sec


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_a_run_never_searched_offers_the_search_and_says_what_it_uses():
    html = _render(_run_fixture())
    sec = html[html.index('id="eviAttendees"'):]
    assert "Find attendees" in sec and "no Apollo credits" in sec and "ten minutes" in sec
    assert "Events do not publish who bought a ticket" in sec


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_a_search_in_progress_shows_progress_not_a_button():
    run = _run_fixture()
    run["attendee_scan"] = {"state": "running"}
    sec = _render(run)
    sec = sec[sec.index('id="eviAttendees"'):]
    assert "Searching LinkedIn and X posts" in sec and "Find attendees" not in sec


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_a_search_that_found_nobody_says_so_and_what_it_could_not_do():
    run = _run_fixture()
    run["attendees"] = []
    run["attendee_scan"] = {"state": "done", "result": {"events": [{"note": (
        "No one was found with public proof of being at this event. LinkedIn was not "
        "searched: no LinkedIn account is connected to this workspace.")}]}}
    sec = _render(run)
    sec = sec[sec.index('id="eviAttendees"'):]
    assert "No one was found with public proof" in sec
    assert "no LinkedIn account is connected" in sec


def test_a_name_alone_never_joins_someone_to_an_exhibitors_staff_member():
    rows = A.merge([
        {"name": "John Smith", "company": "Globex", "linkedin": "https://www.linkedin.com/in/js-globex",
         "basis": "staff", "status": "staff", "edition": "unclear", "proof": [{"kind": "apollo"}]},
        {"name": "John Smith", "company": None, "linkedin": None, "basis": "others",
         "status": "attended", "edition": "this", "proof": [{"kind": "linkedin_post"}]},
        {"name": "John Smith", "company": "Globex", "linkedin": None, "basis": "others",
         "status": "attended", "edition": "this", "proof": [{"kind": "web_page"}]},
    ])
    assert sorted((r["basis"], r.get("company") or "") for r in rows) == [
        ("others", ""), ("others", "Globex")]
    globex = next(r for r in rows if r.get("company") == "Globex")
    assert globex["bases"] == ["others", "staff"]


@pytest.mark.parametrize("pages", [[{"url": "https://widgetexpo.com/exhibitors"}], []])
def test_every_lookup_ends_with_the_attendee_search(monkeypatch, pages):
    """With a roster to read and without one: people post about going to an
    event that publishes nothing."""
    S = P.store
    order = []
    monkeypatch.setattr(P, "durable_stage", lambda name, fn, *a, **k: {
        "ok": True, "event": dict(EVENT), "pages": pages})
    monkeypatch.setattr(S, "save_event", lambda rid, ev: 1)
    monkeypatch.setattr(S, "update_run", lambda rid, **kw: order.append(kw.get("status") or kw.get("stage")))
    monkeypatch.setattr(P, "_harvest_event", lambda *a: order.append("harvest"))
    monkeypatch.setattr(P, "_find_attendees", lambda rid, eid: order.append(("attendees", eid)))
    monkeypatch.setattr(P, "_summarise", lambda rid: {})
    monkeypatch.delenv("DATABASE_URL", raising=False)
    P._run_lookup(5, "Widget Expo", None)
    assert ("attendees", 1) in order
    assert order.index(("attendees", 1)) < order.index("complete")
    if pages:
        assert order.index("harvest") < order.index(("attendees", 1))


def test_a_replayed_run_clears_the_attendees_it_found_before():
    """The worker deletes a run's rows before replaying it from its saved
    stages. Attendees left behind would be listed twice after the replay
    saves them again. Read from the replay's own table list."""
    import re as _re
    src = open(os.path.join(os.path.dirname(__file__), "..", "tracker", "event_intel_jobs.py")).read()
    tables = _re.search(r"for table in \(([^)]*)\):\s*\n\s*cur.execute\('DELETE FROM '", src)
    assert tables and "'evi_attendees'" in tables.group(1)


def test_the_strongest_status_wins_whichever_source_came_first():
    rows = A.merge([
        {"name": "Ana Silva", "company": "Acme", "linkedin": "https://www.linkedin.com/in/ana",
         "basis": "self", "status": "attending", "edition": "this", "proof": [{"kind": "post"}]},
        {"name": "Ana Silva", "company": "Acme", "linkedin": None, "basis": "event",
         "status": "speaking", "edition": "this", "proof": [{"kind": "event_page"}]},
    ])
    (ana,) = rows
    assert ana["status"] == "speaking" and ana["basis"] == "event"


def test_a_failed_batch_is_tried_again_and_a_reply_out_of_room_is_halved(monkeypatch):
    monkeypatch.setattr(A, "RETRY_PAUSE", 0)
    posts = _posts(*["Off to Widget Expo, post %d" % i for i in range(4)])
    calls = []

    def flaky(batch, event, today):
        calls.append(len(batch))
        if len(calls) == 1:
            return {"posts": None, "error": "transport"}
        return {"posts": [{"id": ref, "same_event": True, "people": [
            {"who": "author", "status": "attending", "quote": "Off to Widget Expo, post"}]}
            for ref, _ in batch], "error": None}
    out = A.people_in_posts(posts, EVENT, TODAY, classify=flaky)
    assert calls == [4, 4] and out["failed"] == 0 and len(out["people"]) == 4
    assert out["failed_kinds"] == []

    calls.clear()

    def long(batch, event, today):
        calls.append(len(batch))
        if len(batch) > 2:
            return {"posts": None, "error": "max_tokens"}
        return {"posts": [], "error": None}
    out = A.people_in_posts(posts, EVENT, TODAY, classify=long)
    assert calls == [4, 2, 2] and out["failed"] == 0

    calls.clear()

    def half(batch, event, today):
        calls.append(len(batch))
        if len(batch) > 2 or len(calls) == 3:
            return {"posts": None, "error": "max_tokens"}
        return {"posts": [], "error": None}
    out = A.people_in_posts(posts, EVENT, TODAY, classify=half)
    # Half read is not all read: the half that failed is counted, not lost.
    assert calls == [4, 2, 2] and out["failed"] == 0 and out["unread_posts"] == 2



# ── X ──

def _tweet(tid, text, name="Sam Lee", handle="samlee", bio="Head of Growth at Initech",
           **extra):
    return dict({"id": tid, "text": text, "url": "https://x.com/%s/status/%s" % (handle, tid),
                 "createdAt": "Fri Feb 20 10:12:00 +0000 2026",
                 "author": {"name": name, "userName": handle, "description": bio}}, **extra)


def test_a_retweet_or_an_organisation_is_not_a_person_on_x():
    assert A.x_post(_tweet("1", "RT @widget: See you at Widget Expo")) is None
    assert A.x_post(_tweet("2", "At Widget Expo", isRetweet=True)) is None
    assert A.x_post(_tweet("3", "At Widget Expo", author={"name": "Acme", "userName": "acme",
                                                          "type": "Business"})) is None
    p = A.x_post(_tweet("4", "At Widget Expo"))
    assert p["id"] == "x:4" and p["platform"] == "x"
    assert p["author"]["x"] == "https://x.com/samlee" and p["author"]["url"] is None
    assert p["posted_at"].startswith("2026-02-20")


def test_the_x_search_is_one_run_with_both_names_and_keeps_tweets_naming_the_event():
    asked = []

    def run(terms):
        asked.append(terms)
        return [_tweet("1", "Landed for Widget Expo!"), _tweet("2", "Nice weather"),
                _tweet("1", "Landed for Widget Expo!"), _tweet("3", "#WidgetExpo day one")]
    out = A.search_x(EVENT, run=run)
    assert asked == [['"Widget Expo" -filter:retweets', "#WidgetExpo -filter:retweets",
                      "#WidgetExpo2026 -filter:retweets", "#WidgetExpo2025 -filter:retweets"]]
    assert [p["id"] for p in out["posts"]] == ["x:1", "x:3"] and out["returned"] == 4


def test_an_x_search_that_fails_or_is_not_set_up_says_so(monkeypatch):
    def boom(terms):
        raise RuntimeError("actor failed")
    assert A.search_x(EVENT, run=boom)["error"] == "search_failed"
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    out = A.search_x(EVENT)
    assert out["error"] == "not_configured"
    assert "X was not searched" in A.note({"x": out, "counts": {"confirmed": 1}})


def test_an_x_author_is_kept_with_their_profile_and_a_bio_is_not_a_job_title():
    posts = [A.x_post(_tweet("1", "Speaking at Widget Expo tomorrow on the main stage")),
             A.x_post(_tweet("2", "Off to Widget Expo next week, who else is going?",
                             name="Kai Moss", handle="kai", bio="Dad. Runner. Builder of things"))]
    out = A.people_in_posts(posts, EVENT, TODAY, classify=_reading(
        {"id": "p1", "same_event": True, "people": [
            {"who": "author", "status": "speaking", "quote": "Speaking at Widget Expo tomorrow"}]},
        {"id": "p2", "same_event": True, "people": [
            {"who": "author", "status": "attending", "quote": "Off to Widget Expo next week"}]}))
    sam, kai = out["people"]
    assert (sam["title"], sam["company"], sam["x"]) == ("Head of Growth", "Initech", "https://x.com/samlee")
    assert sam["proof"][0]["kind"] == "x_post" and sam["proof"][0]["label"] == "Their own post on X"
    assert (kai["title"], kai["company"]) == (None, None)


def test_the_same_person_on_linkedin_and_x_is_one_row_with_both_profiles():
    rows = A.merge([
        {"name": "Sam Lee", "company": "Initech", "linkedin": "https://www.linkedin.com/in/samlee",
         "basis": "self", "status": "attending", "edition": "this", "proof": [{"kind": "linkedin_post"}]},
        {"name": "Sam Lee", "company": None, "linkedin": None, "x": "https://x.com/samlee",
         "basis": "self", "status": "speaking", "edition": "this", "proof": [{"kind": "x_post"}]},
    ])
    (sam,) = rows
    assert sam["x"] == "https://x.com/samlee" and sam["linkedin"].endswith("/in/samlee")
    assert A.for_storage(rows)[0]["evidence"]["x"] == "https://x.com/samlee"


def test_x_posts_are_read_with_the_linkedin_ones_and_counted_apart():
    got = A.gather(EVENT, [], today=TODAY, sources={
        "linkedin": lambda e, d: {"posts": _posts("Going to Widget Expo next week, so excited")},
        "x": lambda e, d: {"posts": [A.x_post(_tweet("9", "Going to Widget Expo next week too"))],
                           "returned": 5},
        "classify": lambda batch, e, t: {"posts": [
            {"id": ref, "same_event": True, "people": [
                {"who": "author", "status": "attending", "quote": "Going to Widget Expo next week"}]}
            for ref, _ in batch]},
        "web": lambda e, h, t: {"people": []}, "staff": lambda p, eid: {"people": []}})
    x = dict(got["report"]["x"])
    assert isinstance(x.pop("seconds"), int)
    assert x == {"posts": 1, "returned": 5, "error": None, "people": 1}
    assert got["report"]["linkedin"]["people"] == 1
    assert sorted(r["name"] for r in got["rows"]) == ["Jane Doe", "Sam Lee"]


def test_one_failing_x_search_never_stops_the_others():
    def boom(*a):
        raise RuntimeError("down")
    got = A.gather(EVENT, [], today=TODAY, sources={
        "linkedin": lambda e, d: {"posts": []}, "x": boom,
        "web": lambda e, h, t: {"people": []}, "staff": lambda p, eid: {"people": []}})
    assert got["report"]["x"]["error"] == "search_failed"


@pytest.mark.skipif(not _node_available(), reason="node is not available")
def test_an_x_post_shows_their_profile_and_can_be_filtered_to():
    run = _with_attendees()
    run["attendees"].insert(1, {
        "name": "Sam Lee", "title": "Head of Growth", "company": "Initech", "basis": "self",
        "status": "attending", "edition": "this", "linkedin": None,
        "evidence": {"x": "https://x.com/samlee", "proof": [
            {"kind": "x_post", "label": "Their own post on X", "quote": "Off to Widget Expo",
             "url": "https://x.com/samlee/status/1"}]}})
    run["attendees"][2]["evidence"]["x"] = "javascript:alert(2)"
    sec = _render(run)
    sec = sec[sec.index('id="eviAttendees"'):]
    assert 'href="https://x.com/samlee"' in sec and ">on X</a>" in sec
    assert sec.count(">on X</a>") == 1                    # the unsafe one is not linked
    assert "From X<span class=\"n\">1</span>" in sec


def test_a_brand_account_on_x_is_not_listed_as_a_person():
    assert A.x_post(_tweet("5", "At Widget Expo", author={
        "name": "WhiteBIT", "userName": "whitebit", "verifiedType": "Business"})) is None
    # X rarely marks a brand, so the reader says so, and its own words list nobody.
    posts = [A.x_post(_tweet("6", "We are coming to Widget Expo to meet founders, with Ravi Kumar on stage",
                             name="Intrascope", handle="intrascope", bio=""))]
    out = A.people_in_posts(posts, EVENT, TODAY, classify=_reading(
        {"id": "p1", "same_event": True, "author_is_person": False, "people": [
            {"who": "author", "status": "exhibiting", "quote": "We are coming to Widget Expo"},
            {"who": "named", "name": "Ravi Kumar", "status": "speaking",
             "quote": "with Ravi Kumar on stage"}]}))
    assert [p["name"] for p in out["people"]] == ["Ravi Kumar"]


@pytest.mark.parametrize("raw,clean", [
    ("Tyler Denk \U0001F41D", "Tyler Denk"),
    ("Bellotti Alessandro \U0001F383 \U0001F47B", "Bellotti Alessandro"),
    ("Ana ✨ Silva", "Ana Silva"),
    ("Seán O'Brien-Murphy", "Seán O'Brien-Murphy"),
])
def test_names_lose_their_decoration_and_keep_their_letters(raw, clean):
    assert A.clean_name(raw) == clean
    assert A.x_post(_tweet("7", "At Widget Expo", name=raw))["author"]["name"] == clean


@pytest.mark.parametrize("bio,role", [
    ("Head of Growth at Initech", ("Head of Growth", "Initech")),
    ("cofounder/ceo @beehiiv. former product @ youtube, morning brew", (None, None)),
    ("Dad. Runner. Builder at heart", (None, None)),
])
def test_an_x_bio_gives_a_job_only_when_it_plainly_states_one(bio, role):
    assert A.author_role({"headline": bio}, on_x=True) == role


def test_x_the_web_and_staff_run_while_linkedin_is_searched():
    import threading
    li_started = threading.Event()
    overlapped = []

    def linkedin(e, d):
        li_started.set()
        return {"posts": [], "error": None}

    def beside(out):
        def fn(*a):
            # Run one after another, LinkedIn has not started yet and never
            # will until this returns.
            overlapped.append(li_started.wait(3))
            return out
        return fn
    A.gather(EVENT, [], today=TODAY, sources={
        "linkedin": linkedin, "x": beside({"posts": []}),
        "web": beside({"people": []}), "staff": beside({"people": []})})
    assert overlapped == [True, True, True]


def test_linkedin_posts_are_read_while_x_is_still_loading():
    import threading
    li_reading = threading.Event()
    waited = []

    def classify(batch, event, today):
        if any(p.get("platform") == "linkedin" for _, p in batch):
            li_reading.set()
        return {"posts": [], "error": None}

    def x(e, d):
        # Returns only once LinkedIn's posts are being read: read one after
        # the other, that never happens until this has returned.
        waited.append(li_reading.wait(3))
        return {"posts": [A.x_post(_tweet("1", "At Widget Expo today"))]}
    A.gather(EVENT, [], today=TODAY, sources={
        "linkedin": lambda e, d: {"posts": _posts("At Widget Expo today, booth 4")},
        "x": x, "classify": classify,
        "web": lambda e, h, t: {"people": []}, "staff": lambda p, eid: {"people": []}})
    assert waited == [True]


def test_searches_carry_the_hashtag_of_this_edition_and_the_last():
    assert A.linkedin_queries({"name": "Web Summit Qatar 2027"}) == [
        '"Web Summit Qatar"', "#WebSummitQatar", "#WebSummitQatar2027", "#WebSummitQatar2026"]
    assert A.linkedin_queries({"name": "Money20/20 USA", "starts_on": "2026-10-25"})[-2:] == [
        "#Money2020USA2026", "#Money2020USA2025"]



def test_every_search_and_reading_has_a_whole_call_limit():
    seen = []

    def ask(system, user, **kw):
        seen.append(kw)
        return {"text": "{}", "error": None, "result_urls": []}
    A.find_pages(EVENT, A.WEB_ANGLES[0], TODAY, ask=ask)
    assert seen[0]["deadline"] == A.FIND_SECONDS and seen[0]["max_uses"] > 0


def test_pages_left_unopened_at_the_time_limit_are_counted_and_said():
    import time as _t

    def ask(system, user, **kw):
        return {"text": "{}", "error": None, "result_urls": ["https://a.example.com/1",
                                                             "https://b.example.com/2"]}
    out = A.search_web(EVENT, ask=ask, fetch=lambda u: {"status": "ok", "text": "x"},
                       deadline=_t.monotonic() - 1, google=_no_google)
    assert out["skipped"] == 2 and out["opened"] == 0
    assert "2 of the 2 web pages found were not opened" in A.note(
        {"web": out, "counts": {"confirmed": 1}})



# ── Google ──

def test_google_is_asked_from_every_angle_with_the_years_and_hashtag():
    qs = A.google_queries({"name": "Web Summit Qatar 2027"})
    assert '"Web Summit Qatar" speakers' in qs and '"Web Summit Qatar 2027" speaker' in qs
    assert '"Web Summit Qatar 2026" highlights' in qs and '"see you at Web Summit Qatar"' in qs
    assert "#WebSummitQatar" in qs and len(qs) == len(set(qs)) >= 25


def _serp(term, *rows):
    return {"searchQuery": {"term": term}, "organicResults": [
        {"url": u, "title": t, "description": d} for u, t, d in rows]}


def test_google_results_are_read_off_every_results_page():
    out = A.google_results(EVENT, run=lambda qs: [
        _serp(qs[0], ("https://a.example.com/1", "Widget Expo speakers", "Lee Park")),
        _serp(qs[1], ("https://b.example.com/2", "Other", "")), "junk"])
    assert [r["url"] for r in out["results"]] == ["https://a.example.com/1", "https://b.example.com/2"]
    assert out["results"][0]["query"] == '"Widget Expo" speakers'

    def boom(qs):
        raise RuntimeError("actor failed")
    assert A.google_results(EVENT, run=boom)["error"] == "search_failed"


def test_results_naming_the_event_and_found_by_more_searches_are_opened_first():
    order = A.rank_results([
        {"url": "https://plain.example.com/", "title": "Nothing", "snippet": "", "query": "q1"},
        {"url": "https://often.example.com/x", "title": "Nothing", "snippet": "", "query": "q1"},
        {"url": "https://often.example.com/x/", "title": "Nothing", "snippet": "", "query": "q2"},
        {"url": "https://named.example.com/y", "title": "Widget Expo 2026 recap", "snippet": "",
         "query": "q3"},
        {"url": "https://widgetexpo.com/speakers", "title": "Widget Expo", "snippet": "", "query": "q1"},
        {"url": "https://www.linkedin.com/posts/z", "title": "Widget Expo", "snippet": "", "query": "q1"},
    ], EVENT, "widgetexpo.com")
    assert order == ["https://named.example.com/y", "https://often.example.com/x",
                     "https://plain.example.com/"]


def test_with_google_every_result_is_opened_and_only_pages_naming_the_event_are_read(monkeypatch):
    monkeypatch.setattr(A, "MAX_WEB_PAGES", 2)
    results = [{"url": "https://p%d.example.com/" % i, "title": "", "snippet": "", "query": "q"}
               for i in range(5)]
    texts = {"https://p0.example.com/": "Lee Park spoke at Widget Expo.",
             "https://p1.example.com/": "Nothing relevant here at all.",
             "https://p2.example.com/": "Mia Wong exhibited at Widget Expo.",
             "https://p3.example.com/": "Bo Chen attended Widget Expo."}
    opened, read_urls = [], []

    def fetch(url):
        opened.append(url)
        return {"status": "ok", "text": texts[url]} if url in texts else {"status": "blocked"}

    def read(url, text, event, today):
        read_urls.append(url)
        name = text.split(" ")[0] + " " + text.split(" ")[1]
        return {"people": [{"name": name, "status": "attended", "quote": text.rstrip(".")}]}

    def no_model(*a, **k):
        raise AssertionError("the model search is only the fallback")
    out = A.search_web(EVENT, "", TODAY, fetch=fetch, ask=no_model, read=read,
                       google=lambda e: {"results": results, "queries": 26, "error": None})
    assert sorted(opened) == sorted(r["url"] for r in results)
    assert read_urls == ["https://p0.example.com/", "https://p2.example.com/"]
    assert (out["via"], out["searches"], out["opened"], out["unopened"], out["on_event"],
            out["read"], out["not_read"]) == ("google", 26, 4, 1, 3, 2, 1)
    assert [p["name"] for p in out["people"]] == ["Lee Park", "Mia Wong"]
    assert "1 more web pages naming the event were found" in A.note(
        {"web": out, "counts": {"confirmed": 1}})


def test_when_google_cannot_be_searched_the_model_search_takes_over():
    asked = []

    def ask(system, user, **kw):
        asked.append(1)
        return {"text": "{}", "error": None, "result_urls": []}
    out = A.search_web(EVENT, ask=ask, fetch=lambda u: {},
                       google=lambda e: {"results": [], "error": "search_failed"})
    assert out["via"] == "model" and out["google_error"] == "search_failed"
    assert len(asked) == len(A.WEB_ANGLES)



@pytest.mark.parametrize("page,said,edition", [
    ("Widget Expo 2026 speakers: Lee Park", "this", "this"),
    ("Widget Expo 2024 recap: Lee Park spoke", "this", "earlier"),
    ("Widget Expo speakers: Lee Park", "this", "unclear"),
    ("Widget Expo 2025 and 2026: Lee Park", "earlier", "earlier"),
])
def test_a_web_page_is_about_this_edition_only_when_it_prints_its_year(page, said, edition):
    assert A.page_edition(said, page, EVENT) == edition


def test_a_person_read_off_a_page_takes_the_pages_edition_not_the_models():
    out = A.people_on_page(
        "https://en.example.org/wiki/Widget_Expo",
        "Widget Expo has hosted speakers such as Lee Park, who spoke at Widget Expo in 2023.",
        EVENT, TODAY,
        read=lambda u, t, e, d: {"people": [{"name": "Lee Park", "status": "speaking",
                                             "edition": "this",
                                             "quote": "Lee Park, who spoke at Widget Expo in 2023"}]})
    assert out["people"][0]["edition"] == "earlier"
