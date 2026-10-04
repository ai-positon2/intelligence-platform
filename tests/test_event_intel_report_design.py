"""The report drawer's redesign (2026-10-04): what each new block shows.

Each test runs the page's real renderer in node over a payload, and asserts
on what a reader would see, not on how it is styled.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_event_intel_charts import _cand, _out, _recommend, _render, _workroom, _part, _src, _lookup  # noqa: E402
from test_event_intel_event_view import page_script  # noqa: E402,F401  (fixture)

CSS = os.path.join(os.path.dirname(__file__), "..", "static", "css", "event_intel_report.css")


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def _kept(*rows):
    return [_cand(n, t, tier, relevance=r, dm_access=d, engagement=e, matchmaking=m,
                  client_line="Why %s." % n)
            for (n, t, tier, r, d, e, m) in rows]


THREE = [("Alpha Summit", 98, "P1", 38, 32, 18, 10), ("Beta Expo", 88, "P1", 37, 33, 18, 0),
         ("Gamma Forum", 76, "P2", 34, 26, 16, 0), ("Delta Days", 72, "P2", 32, 24, 16, 0)]


def test_the_answer_shows_the_first_three_as_a_podium_each_opening_its_card(page_script):
    kept = _kept(*THREE)
    body = _render(page_script, _recommend(kept, counts={"P1": 2, "P2": 2, "kept": 4},
                                           selection={"kept": kept}))
    pod = re.search(r'<div class="apod n3">(.*?)</div></div>', body, re.S).group(1)
    assert re.findall(r'class="an">([^<]+)', pod) == ["Alpha Summit", "Beta Expo", "Gamma Forum"]
    assert re.findall(r"eviReveal\('([^']+)'\)", pod) == ["cand-1", "cand-2", "cand-3"]
    assert "Delta Days" not in pod and "Why Alpha Summit." in pod


def test_a_single_recommendation_is_one_card_without_a_repeated_case(page_script):
    kept = _kept(THREE[0])
    body = _render(page_script, _recommend(kept, counts={"P1": 1, "kept": 1}, selection={"kept": kept}))
    assert 'class="apod n1"' in body and 'class="ac"' not in body


def test_each_score_is_drawn_as_its_parts_and_the_list_is_read_for_its_lean(page_script):
    kept = _kept(*THREE)
    body = _render(page_script, _recommend(kept, counts={"P1": 2, "P2": 2, "kept": 4},
                                           selection={"kept": kept}))
    sec = body[body.index("What each score is made of"):]
    rows = re.findall(r'<button type="button" class="xr[^"]*"[^>]*>(.*?)</button>', sec, re.S)
    assert len(rows) == 4
    widths = [float(w) for w in re.findall(r'width:([\d.]+)%', rows[0])]
    # 38 + 32 + 18 + 10 of 110, each drawn at its own share of the bar.
    assert [round(w * 1.1) for w in widths] == [38, 32, 18, 10]
    text = _text(sec)
    assert "leans on relevance" in text and "thinnest on decision-maker access" in text
    assert "1 event earned the matchmaking bonus" in text


def test_one_scored_event_draws_no_composition_chart(page_script):
    kept = _kept(THREE[0])
    body = _render(page_script, _recommend(kept, counts={"P1": 1, "kept": 1}, selection={"kept": kept}))
    assert "What each score is made of" not in body


def test_the_sub_scores_carry_their_dimension(page_script):
    kept = _kept(THREE[0])
    body = _render(page_script, _recommend(kept, counts={"P1": 1, "kept": 1}, selection={"kept": kept}))
    assert all(("evi-sub" in body and k in body) for k in ("x-rel", "x-dm", "x-eng"))


def _att(name, basis, edition, quote=None, kind="linkedin_post", status="attending"):
    return {"name": name, "basis": basis, "edition": edition, "status": status,
            "title": "Founder", "company": name + " Co",
            "evidence": {"proof": [{"kind": kind, "label": "Their own LinkedIn post",
                                    "quote": quote, "url": "https://www.linkedin.com/posts/x"}]}}


def _with_people(people):
    run = _lookup([_part("Acme", "exhibitor")], [_src("https://e.example/x", "ok")])
    run["attendees"] = people
    run["attendee_scan"] = {"state": "done", "result": {"events": []}}
    return run


def test_attendees_open_with_how_the_list_was_made(page_script):
    people = [_att("Ana Silva", "self", "this", "Going to A Conference!"),
              _att("Bo Chen", "others", "this", "Bo Chen is speaking at A Conference"),
              _att("Cy Doe", "self", "earlier", "Loved A Conference 2025"),
              _att("Di Roe", "staff", None, None, kind="apollo", status="staff")]
    body = _render(page_script, _with_people(people))
    mix = _text(body[body.index('class="evi-att-mix"'):body.index('class="evi-att-spot"')])
    assert "2 Said so publicly" in mix and "1 Named by someone who was there" in mix
    assert "1 At a listed company, not confirmed" in mix
    assert "2 with proof for this edition" in mix and "3 proved by a LinkedIn post" in mix


def test_the_spotlight_is_this_editions_people_in_their_own_words_first(page_script):
    people = [_att("Web Person", "others", "this", "Confirmed speakers include Web Person", kind="web_page"),
              _att("Own Words", "self", "this", "I will be at A Conference"),
              _att("Past Person", "self", "earlier", "Loved it last year"),
              _att("No Quote", "event", "this", None, kind="event_page"),
              _att("Self Silent", "self", "this", None)]
    body = _render(page_script, _with_people(people))
    spot = body[body.index('class="evi-att-spot"'):]
    spot = spot[:spot.index('class="evi-rolebar"')] if 'class="evi-rolebar"' in spot else spot
    names = re.findall(r'class="asn"><(?:a[^>]*|b)>([^<]+)', spot)
    assert names == ["Own Words", "Web Person", "Self Silent", "No Quote"]
    assert "Past Person" not in spot


def test_the_spotlight_needs_two_people_to_be_worth_a_block(page_script):
    body = _render(page_script, _with_people([_att("Solo", "self", "this", "Going!")]))
    assert 'class="evi-att-spot"' not in body


def test_a_draft_reads_as_a_message_to_someone(page_script):
    rows = [_out("Acme", 80, person_name="Dana Lee", opener="Hello Dana."),
            _out("Beta", 70, opener="Hello Beta.", draft_status="account_play")]
    body = _render(page_script, _workroom(rows))
    assert body.count('class="evi-av org"') == 2
    assert "To Dana Lee" in _text(body) and "To the right person at Beta" in _text(body)
    assert re.search(r'class="obody"><div class="ocol">.*?</div><div class="oo">', body, re.S)


def test_the_design_layer_is_screen_only_and_loaded_last():
    css = open(CSS).read()
    body = re.sub(r"/\*.*?\*/", "", css, flags=re.S).strip()
    assert body.startswith("@media screen {") and body.rstrip().endswith("}")
    page = open(os.path.join(os.path.dirname(__file__), "..", "templates",
                             "event_conference_intelligence.html")).read()
    links = re.findall(r'href="/static/css/([^"?]+)', page)
    assert links[-1] == "event_intel_report.css"
    # Every colour the layer uses is defined for both themes.
    dark = re.search(r"\.evi-drawer \{\s*--r-ink:(.*?)\}", css, re.S).group(1)
    light = re.search(r':root\[data-theme="light"\] \.evi-drawer \{\s*--r-ink:(.*?)\}', css, re.S).group(1)
    assert set(re.findall(r"--[\w-]+", dark)) == set(re.findall(r"--[\w-]+", light))


def test_the_spotlight_shows_the_freshest_posts_first(page_script):
    people = []
    for i, d in enumerate(["2026-05-14", "2026-10-02", "2026-09-28", "2026-08-01", "2026-10-03",
                           "2026-09-01", "2026-07-01"]):
        p = _att("Person %d" % i, "self", "this", "I will be at A Conference (%d)" % i)
        p["evidence"]["proof"][0]["posted_at"] = d
        people.append(p)
    body = _render(page_script, _with_people(people))
    spot = body[body.index('class="evi-att-spot"'):]
    names = re.findall(r'class="asn"><(?:a[^>]*|b)>([^<]+)', spot)[:6]
    assert names == ["Person 4", "Person 1", "Person 2", "Person 5", "Person 3", "Person 6"]
