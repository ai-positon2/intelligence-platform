"""The charts in all three reports, executed rather than grepped.

Every test here runs the page's own inline script in node over a run payload
and asserts on the HTML it produced. Nothing re-implements a chart: a grep for
a function name passes against a function nobody calls, and half the defects
this file was opened for were exactly that, a rule or a helper that existed
and never reached the markup.

What is being defended is not that the charts are pretty. It is that a
picture is a claim, and these particular pictures are drawn over a report
whose whole reason to exist is not overstating what was found:

  * a donut's centre number is the sum of its own segments, so the picture
    and the number beside it cannot disagree;
  * what is MISSING is drawn, in a muted band, rather than dropped for being
    awkward, so a roster nobody could look up does not draw as a clean one;
  * an unscored row is a stub, never a zero-height column, because zero is a
    score and not-scored is not;
  * a failed category search is red, not short, because a short bar reads as
    a finding about the market and a failure is a hole in the analysis;
  * every chart renders at full size with NO animation, because the timeline
    does not run in a background tab and a chart that needs a keyframe to
    become visible is invisible exactly when somebody screenshots it.
"""

import json
import os
import re
import subprocess
import sys

import pytest

# The node harness lives next to the tests it was written for. Imported
# rather than copied: two copies of a DOM shim drift, and the last time one
# did it fabricated a node for every id and made half a file pass vacuously.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_event_intel_event_view import _SHIM, _IIFE_CLOSE, page_script  # noqa: E402,F401


def _render_parts(page_script, run):
    """Run the page's real render() over `run` and return everything it wrote.

    Both the report body and the heading above it, because the heading is
    prose too and has had its own defects: it once read "1 events cleared the
    bar of 2 found" to a paying client.
    """
    at = page_script.index(_IIFE_CLOSE)
    probe = ("\ncurrent = __RUN;\nrender(__RUN);\n"
             "console.log(JSON.stringify({body: "
             "document.getElementById('drawerBody').innerHTML, "
             "title: document.getElementById('drawerTitle').textContent, "
             "sub: document.getElementById('drawerSub').textContent}));\n")
    src = page_script.script
    js = "var __PAGE_IDS = %s;\n%s\nvar __RUN = %s;\n%s" % (
        json.dumps(page_script.ids), _SHIM, json.dumps(run),
        src[:at] + probe + src[at:])
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, "the page script threw:\n%s" % r.stderr[-2500:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def _render(page_script, run):
    """Just the report body. What most tests here assert on."""
    return _render_parts(page_script, run)["body"]


# ── payloads ──────────────────────────────────────────────────────────────

def _cand(name, total, tier, **kw):
    c = {"name": name, "total": total, "tier": tier, "relevance": 30,
         "dm_access": 30, "engagement": 15, "matchmaking": 0, "category": "emerging",
         "starts_on": "2027-05-04", "ends_on": "2027-05-06", "city": "Berlin",
         "country": "Germany", "website": None, "committed": False, "gaps": []}
    c.update(kw)
    return c


def _recommend(cands, **summary):
    s = {"title": "T", "client_profile": "P", "methodology": "M", "assumptions": [],
         # Every finished run now carries both shapes. A stub with only the
         # prose one is stubbing a summary the pipeline no longer produces.
         "notes": [],
         "discovered": 20, "counts": {"P1": 1, "P2": 1, "kept": len(cands),
                                      "excluded": 0, "finished": 0},
         "top_five": [], "excluded": [], "finished": [], "unscored": [],
         "shortfall": [], "statuses": {}, "outcomes": {}}
    s.update(summary)
    return {"id": 5, "mode": "recommend", "status": "complete", "query": "q",
            "summary": s, "candidates": cands, "events": [], "participants": [],
            "sources": [], "role_labels": {}}


def _lookup(parts, sources, **summary):
    by_role = {}
    for p in parts:
        by_role[p["role"]] = by_role.get(p["role"], 0) + 1
    s = {"participants": len(parts), "organisations": len(parts),
         "sources_tried": len(sources),
         "sources_read": len([x for x in sources if x["status"] == "ok"]),
         "sources_unreadable": len([x for x in sources if x["status"] != "ok"]),
         "by_role": by_role, "roster_note": "Published listings only."}
    s.update(summary)
    return {"id": 6, "mode": "lookup", "status": "complete", "query": "q",
            "events": [{"id": 1, "name": "A Conference", "starts_on": "2027-05-04",
                        "ends_on": "2027-05-06", "location": "Berlin",
                        "format": "in_person"}],
            "participants": parts, "sources": sources, "summary": s,
            "role_labels": {"exhibitor": "Exhibitor", "sponsor": "Sponsor",
                            "speaker": "Speaker", "partner": "Partner",
                            "media": "Media",
                            "attendee_declared": "Publicly said they are attending"}}


def _part(org, role, **kw):
    p = {"org_name": org, "org_domain": org.lower() + ".com", "role": role,
         "tier": None, "booth": None, "person_name": None, "person_title": None,
         "source_url": "https://e.example/x", "provenance": "page",
         "resolution": "unresolved", "apollo": {}}
    p.update(kw)
    return p


def _src(url, status, **kw):
    s = {"url": url, "status": status, "kind": "list", "rows_found": 0,
         "http_status": 200, "note": None}
    s.update(kw)
    return s


def _out(org, fit, **kw):
    r = {"org_name": org, "role": "exhibitor", "fit": fit, "fit_note": "n",
         "angle": "a", "opener": "o", "person_name": None, "person_title": None,
         "booth_note": None, "draft_status": "ok", "draft_reason": None,
         "draft_flagged": [], "account_note": None, "unqualified": False}
    r.update(kw)
    return r


def _workroom(rows, **summary):
    s = {"event_name": "A Conference", "floor": 55,
         "counts": {"roster": len(rows) + 4, "kept": 0, "cut": 0, "unqualified": 0},
         "event_class_label": "Vendor floor", "event_class_signal": "strong",
         "event_class_why": "w", "play": "p", "send_note": "Nothing has been sent.",
         "repeats": {"crm_note": "", "repeats": []}, "qualify_errors": [],
         "rewritten_count": 0, "booth_notes_given": 0}
    s.update(summary)
    s["counts"]["kept"] = len([r for r in rows
                               if not r["unqualified"] and r["fit"] is not None
                               and r["fit"] >= s["floor"]])
    return {"id": 7, "mode": "workroom", "status": "complete", "query": "q",
            "summary": s, "outreach": rows, "events": [], "participants": [],
            "sources": [], "role_labels": {"exhibitor": "Exhibitor",
                                           "sponsor": "Sponsor"}}


# ── the rule every donut on the page obeys ────────────────────────────────

def _donuts(html):
    """(centre, [segment counts]) for every donut in the markup.

    Also returns how many donuts it could NOT read. The centre pattern only
    matches a bare integer, so a donut whose centre is written as anything
    else was silently skipped and its invariant went unchecked, which is a
    checker quietly exempting the one chart most likely to be wrong. The
    count comes back so the test can fail on it instead.
    """
    out, skipped = [], 0
    for d in re.findall(r'<div class="evi-donut">.*?</div>\s*</div>', html, re.S):
        centre = re.search(r'<span class="dc"><b>(\d+)</b>', d)
        segs = [int(x) for x in re.findall(r'<title>[^<]*?: (\d+)</title>', d)]
        if centre:
            out.append((int(centre.group(1)), segs))
        else:
            skipped += 1
    return out, skipped


def test_a_donut_centre_is_the_sum_of_its_own_segments(page_script):
    """The number in the hole is the one number the chart is about. A centre
    that counts something narrower than the ring around it, the tiered rows
    inside a ring of every row, is a picture that argues with its own label,
    and it is the mistake this shape invites."""
    parts = ([_part("A%d" % i, "exhibitor", tier="Gold") for i in range(3)] +
             [_part("B%d" % i, "sponsor") for i in range(4)] +
             [_part("C", "speaker")])
    # Mixed on purpose. Every donut here has a majority slice and a minority
    # one, so a centre that quietly counts only the biggest group, only the
    # tiered rows, only the pages that opened, is a different number from the
    # sum and this test can see it.
    srcs = [_src("https://a", "ok"), _src("https://b", "ok"),
            _src("https://c", "blocked"), _src("https://d", "not_found")]
    html = _render(page_script, _lookup(parts, srcs))
    found, skipped = _donuts(html)
    assert len(found) >= 3, "expected a donut per panel, found %d" % len(found)
    assert not skipped, (
        "%d donut(s) had a centre this test cannot read, so their segments "
        "went unchecked. Either the centre is a bare count, or this checker "
        "has to learn the new shape: a chart nobody checks is worse than no "
        "chart." % skipped)
    for centre, segs in found:
        assert sum(segs) == centre, (
            "a donut's segments add to %d inside a centre that says %d"
            % (sum(segs), centre))


def test_the_donut_reader_reports_a_centre_it_cannot_read():
    """The guard above only fires on a shape no chart on the page currently
    produces, so nothing exercised it and a mutant that deleted the counter
    survived. Driven directly instead: an unreadable centre has to come back
    as a skip, not as an absence."""
    ok = ('<div class="evi-donut"><span class="dc"><b>3</b></span>'
          '<title>A: 1</title><title>B: 2</title></div>\n</div>')
    found, skipped = _donuts(ok)
    assert found == [(3, [1, 2])] and skipped == 0
    odd = ok.replace("<b>3</b>", "<b>1 of 6</b>")
    found, skipped = _donuts(odd)
    assert found == [] and skipped == 1, (
        "a donut whose centre this reader cannot parse was dropped in "
        "silence, so its segments went unchecked")


def test_the_rows_with_no_tier_are_a_slice_rather_than_a_gap(page_script):
    """Most of a roster usually has no published tier. Charting only the rows
    that do would draw a sponsorship pyramid over a roster that is mostly not
    sponsored at all."""
    parts = [_part("Paid", "exhibitor", tier="Gold")] + \
            [_part("Free%d" % i, "exhibitor") for i in range(7)]
    html = _render(page_script, _lookup(parts, [_src("https://a", "ok")]))
    assert "No tier published" in html, "the untiered rows are not on the chart"
    tier_donut = [d for d in re.findall(r'<div class="evi-donut">.*?</div>\s*</div>',
                                        html, re.S) if "No tier published" in d]
    assert tier_donut, "expected a tier donut"
    segs = [int(x) for x in re.findall(r'<title>[^<]*?: (\d+)</title>', tier_donut[0])]
    assert sum(segs) == len(parts), (
        "the tier chart covers %d of %d rows" % (sum(segs), len(parts)))


# ── the pages that could not be read ──────────────────────────────────────

def test_every_attempted_page_is_on_the_coverage_chart(page_script):
    srcs = [_src("https://a", "ok"), _src("https://b", "ok"),
            _src("https://c", "blocked"), _src("https://d", "not_found"),
            _src("https://e", "recovered")]
    html = _render(page_script, _lookup([_part("A", "exhibitor")], srcs))
    donut = [d for d in re.findall(r'<div class="evi-donut">.*?</div>\s*</div>',
                                   html, re.S) if "attempted" in d]
    assert donut, "the sources were not drawn"
    segs = [int(x) for x in re.findall(r'<title>[^<]*?: (\d+)</title>', donut[0])]
    assert sum(segs) == len(srcs), (
        "%d of %d attempted pages made it onto the chart" % (sum(segs), len(srcs)))


def test_a_page_recovered_by_search_is_not_drawn_as_a_page_we_read(page_script):
    """The store keeps `recovered` apart from `ok` because a list a model
    reassembled out of search results is weaker evidence than a page that was
    parsed. A chart that colours them the same throws that away."""
    srcs = [_src("https://a", "ok"), _src("https://b", "recovered")]
    html = _render(page_script, _lookup([_part("A", "exhibitor")], srcs))
    donut = [d for d in re.findall(r'<div class="evi-donut">.*?</div>\s*</div>',
                                   html, re.S) if "attempted" in d][0]
    ok_cls = re.search(r'class="sg ([\w-]+)"[^>]*>\s*<title>Read', donut)
    rec_cls = re.search(r'class="sg ([\w-]+)"[^>]*>\s*<title>Recovered', donut)
    assert ok_cls and rec_cls, "expected both a read and a recovered slice"
    assert ok_cls.group(1) != rec_cls.group(1), (
        "a recovered page is drawn in the same band as one that was read")


def test_an_unreadable_page_is_said_in_words_next_to_the_chart(page_script):
    html = _render(page_script, _lookup(
        [_part("A", "exhibitor")],
        [_src("https://a", "ok"), _src("https://b", "blocked")]))
    assert "did not come back readable" in html, (
        "the coverage panel does not say that a page could not be read")


def test_the_firmographics_chart_says_how_many_were_never_looked_up(page_script):
    """Four industries drawn over twenty companies is a picture of a lookup
    that happened to everyone. It happened to four."""
    parts = ([_part("A", "exhibitor", apollo={"industry": "ABM", "employees": "1-10"})] +
             [_part("B%d" % i, "exhibitor") for i in range(9)])
    html = _render(page_script, _lookup(parts, [_src("https://a", "ok")]))
    assert "have not been looked up" in html, (
        "the industry chart does not account for the rows Apollo never saw")
    assert "9" in re.search(r"The other (\d+) have not been looked up",
                            html).group(0)


# ── the recommendation ────────────────────────────────────────────────────

def test_the_funnel_never_widens(page_script):
    """Found, scored, cleared. Each is a subset of the one above it, so a
    later step reporting a bigger number means the report is drawing three
    unrelated counts as a containment."""
    html = _render(page_script, _recommend(
        [_cand("A", 90, "P1"), _cand("B", 80, "P2")],
        discovered=20, counts={"P1": 1, "P2": 1, "kept": 2, "excluded": 3,
                               "finished": 2}))
    nums = [int(x) for x in re.findall(r'<div class="fb"[^>]*><b>(\d+)</b>', html)]
    assert len(nums) == 3, "expected three funnel steps, found %s" % nums
    assert nums == sorted(nums, reverse=True), "the funnel widens: %s" % nums
    assert nums[0] == 20 and nums[2] == 2, nums


def test_the_ranked_list_is_drawn_on_the_calendar_by_tier(page_script):
    """A recommendation scores out of 110 and carries a tier. Colouring its
    markers with the fit bands, which are a 0 to 100 scale, would put a 99 in
    the same band as a 90."""
    html = _render(page_script, _recommend(
        [_cand("A", 99, "P1", starts_on="2027-05-04", ends_on="2027-05-05"),
         _cand("B", 71, "P2", starts_on="2027-09-01", ends_on="2027-09-02")]))
    marks = re.findall(r'<button type="button" class="mk ([\w-]+)', html)
    assert marks, "the ranked list was not drawn on the rail"
    assert any(m.startswith("t-") for m in marks), (
        "the rail is using fit bands for a tiered list: %s" % marks)


def test_a_candidate_with_no_announced_date_is_named_beside_the_rail(page_script):
    """Same rule the event set has kept from the beginning: a date nobody has
    announced is never drawn at a plausible-looking position."""
    html = _render(page_script, _recommend(
        [_cand("Dated", 90, "P1"), _cand("Also dated", 80, "P2",
                                         starts_on="2027-08-01", ends_on="2027-08-02"),
         _cand("No date yet", 75, "P2", starts_on=None, ends_on=None)]))
    tail = re.search(r'<div class="evi-undated">.*?</div>', html, re.S)
    assert tail, "an undated candidate was not reported beside the rail"
    assert "No date yet" in tail.group(0)
    assert "guessed" in tail.group(0)


def test_every_column_in_the_spread_that_offers_a_jump_lands_on_a_card(page_script):
    html = _render(page_script, _recommend(
        [_cand("A", 99, "P1"), _cand("B", 80, "P2"), _cand("C", 71, "P2")],
        excluded=[{"name": "Cut one", "tier": "P3", "total": 40}]))
    ids = set(re.findall(r'<div class="evi-cand [^"]*" id="([^"]+)"', html))
    assert ids, "no candidate card carries an id"
    for target in re.findall(r"eviReveal\('(cand-\d+)'\)", html):
        assert target in ids, (
            "something jumps to %r, which is not on the page. Cards are %s"
            % (target, sorted(ids)))


def test_the_cut_events_are_in_the_spread_and_offer_no_jump(page_script):
    """The most useful thing about a floor is seeing what it stopped. The cut
    list is stored as names and totals with no card of its own, so those
    columns are shapes rather than controls."""
    html = _render(page_script, _recommend(
        [_cand("A", 99, "P1"), _cand("B", 80, "P2")],
        excluded=[{"name": "Cut one", "tier": "P3", "total": 40}]))
    spread = re.search(r'<div class="evi-cols[^"]*">.*?</div>\s*</div>', html, re.S)
    assert spread and "Cut one (cut)" in spread.group(0), (
        "the cut events are missing from the spread")
    cut_col = re.search(r'<(\w+)[^>]*title="Cut one \(cut\)[^"]*"', spread.group(0))
    assert cut_col and cut_col.group(1) == "span", (
        "a cut event is drawn as a button that has nowhere to go")


def test_the_bar_is_drawn_at_the_rubric_s_floor(page_script):
    """Three events, not two: the spread needs three to draw at all, because
    two columns carry no distribution to read."""
    html = _render(page_script, _recommend(
        [_cand("A", 99, "P1"), _cand("B", 80, "P2"), _cand("C", 74, "P2")]))
    floor = re.search(r'<span class="fo" style="bottom:([\d.]+)%"><b>([^<]+)</b>', html)
    assert floor, "the floor is not drawn on the spread"
    from tracker import event_intel_rubric as rubric
    assert str(rubric.RANK_FLOOR) in floor.group(2), (
        "the line is labelled %r, and the rubric cuts at %s"
        % (floor.group(2), rubric.RANK_FLOOR))
    want = rubric.RANK_FLOOR / 110.0 * 100
    assert abs(float(floor.group(1)) - want) < 0.5, (
        "the line sits at %s%% and the floor is %s of 110"
        % (floor.group(1), rubric.RANK_FLOOR))


def test_a_top_five_entry_only_becomes_a_control_when_it_has_a_card(page_script):
    """The cap can cut the ranked list shorter than five, and a name with no
    card below it must render as text rather than as a button that goes
    nowhere."""
    html = _render(page_script, _recommend(
        [_cand("A", 99, "P1")],
        top_five=[{"name": "A", "tier": "P1", "total": 99, "when": "w",
                   "where": "x", "case": "c"},
                  {"name": "Not in the list", "tier": "P2", "total": 80,
                   "when": "w", "where": "x", "case": "c"}]))
    lis = re.findall(r'<li class="[^"]*">(.*?)</li>', html, re.S)
    assert len(lis) == 2, "expected two top-five entries"
    with_card = [x for x in lis if "A</div>" in x or ">A<" in x]
    assert 'class="tw"' in with_card[0], "the entry with a card is not a control"
    missing = [x for x in lis if "Not in the list" in x][0]
    assert 'class="tw"' not in missing, (
        "an entry with no card below it is still rendered as a button")


# ── category coverage ─────────────────────────────────────────────────────

def test_a_failed_category_search_is_not_drawn_as_a_thin_market(page_script):
    """A search that crashed and a category the market genuinely has nothing
    in both return zero events. Drawing them the same way is the single
    easiest way for this report to mislead."""
    html = _render(page_script, _recommend(
        [_cand("A", 99, "P1")],
        shortfall=[{"category": "emerging", "label": "Emerging event", "found": 0,
                    "quota": 2, "status": "error", "why": "the tool died"},
                   {"category": "side_event", "label": "Side event", "found": 1,
                    "quota": 2, "status": "short", "why": "the market is thin"}],
        statuses={"emerging": {"kept": 0}, "side_event": {"kept": 1}}))
    bars = re.findall(r'<div class="br (\w*)"[^>]*>(.*?)(?=<div class="br |\Z)',
                      html, re.S)
    by_state = {}
    for state, body in bars:
        name = re.search(r'<div class="bl">([^<]*)</div>', body)
        if name:
            by_state[name.group(1)] = state
    assert by_state.get("Emerging event") == "error", by_state
    assert by_state.get("Side event") == "short", by_state
    assert "The search did not run" in html


def test_a_category_with_no_stored_count_reports_a_word_not_a_number(page_script):
    """Meeting the quota is recorded; how many it found is not always. Filling
    that column with the quota would be inventing a count so the chart looks
    finished."""
    html = _render(page_script, _recommend(
        [_cand("A", 99, "P1")],
        shortfall=[{"category": "emerging", "label": "Emerging event", "found": 0,
                    "quota": 2, "status": "short", "why": "thin"}],
        statuses={"emerging": {"kept": 0}}))
    assert "met the quota" in html, (
        "a category with no stored count is not saying so")
    met = re.findall(r'<div class="br met"[^>]*>.*?<div class="bn">(.*?)</div>',
                     html, re.S)
    assert met, "expected at least one category that met its quota"
    assert any("met the quota" in m for m in met), met


# ── the workroom ──────────────────────────────────────────────────────────

def test_the_window_is_drawn_against_the_hours_the_pipeline_cuts_on(page_script):
    """48 and 72 used to be typed into the page while the pipeline cut on its
    own constants. An arc drawn against a hardcoded 72 beside a pipeline
    working to something else is a picture that disagrees with the run."""
    from tracker import event_intel_workroom as wr
    html = _render(page_script, _workroom(
        [_out("A", 80), _out("B", 40)],
        window={"known": True, "state": "prime", "hours": 20.0, "note": "n"}))
    ring = re.search(r'<div class="evi-ring [^"]*" role="img" aria-label="'
                     r'(\d+) out of (\d+)', html)
    assert ring, "the window was not drawn"
    assert int(ring.group(2)) == wr.WINDOW_HOURS, (
        "the arc is out of %s and the pipeline's window is %s hours"
        % (ring.group(2), wr.WINDOW_HOURS))
    assert str(wr.PRIME_HOURS) + "-hour window" in html


def test_an_unknown_window_draws_no_arc(page_script):
    """A ring at zero would read as "no time has passed", which is the
    opposite of not knowing when the event ended."""
    html = _render(page_script, _workroom(
        [_out("A", 80), _out("B", 40)],
        window={"known": False, "state": None, "hours": None,
                "note": "No end date was published."}))
    assert "Follow-up window unknown" in html
    assert '<div class="wr">' not in html, (
        "an arc was drawn for a window nobody knows the length of")


def test_an_unqualified_company_is_not_a_zero_on_the_spread(page_script):
    """Zero is a score. Not qualified is not, and the two cannot share a
    column height."""
    rows = [_out("A", 80), _out("B", 70), _out("C", 30),
            _out("D", None, unqualified=True)]
    html = _render(page_script, _workroom(rows))
    spread = re.search(r'<div class="evi-cols[^"]*">.*?<div class="cn">', html, re.S)
    assert spread, "the fit spread was not drawn"
    assert "D" not in re.findall(r'title="([^"]*)"', spread.group(0)), (
        "an unqualified company was given a column")
    assert "Not qualified" in html, "the unqualified rows are not reported at all"


def test_every_workroom_jump_lands_on_a_card_that_exists(page_script):
    html = _render(page_script, _workroom(
        [_out("A", 90), _out("B", 70), _out("C", 30)]))
    ids = set(re.findall(r'<div class="evi-out [^"]*" id="([^"]+)"', html))
    assert ids, "no outreach card carries an id"
    for target in re.findall(r"eviReveal\('(out-\d+)'\)", html):
        assert target in ids, (
            "a column jumps to %r, which is not on the page. Cards are %s"
            % (target, sorted(ids)))


def test_no_scored_company_falls_between_the_two_lists(page_script):
    """Kept and cut are drawn from one pair of filters now. One pair can
    still be wrong in a way two could not: a gap between them drops a company
    out of the chart AND out of both lists at once, which looks tidy and is a
    company nobody was told about."""
    rows = [_out("A", 90), _out("B", 56), _out("C", 54), _out("D", 20)]
    html = _render(page_script, _workroom(rows, floor=55))
    named = set(re.findall(r'<div class="on">([A-D])<', html))
    named |= set(re.findall(r'<span class="cutp">([A-D])<b>', html))
    assert named == {"A", "B", "C", "D"}, (
        "%s were scored and only %s are reported anywhere"
        % (sorted("ABCD"), sorted(named)))


def test_the_charts_and_the_lists_cut_at_the_same_floor(page_script):
    """The spread and the "worth contacting" list used to filter the rows
    separately. Two filters are two chances to disagree about where the floor
    is, and the drawing would be the one nobody checked."""
    rows = [_out("A", 90), _out("B", 56), _out("C", 54), _out("D", 20)]
    html = _render(page_script, _workroom(rows, floor=55))
    spread = re.search(r'<div class="evi-cols[^"]*">.*?<div class="cn">', html, re.S)
    titles = re.findall(r'title="([^"]*)"', spread.group(0))
    cut_in_chart = {t.split(" (cut)")[0] for t in titles if "(cut)" in t}
    cut_in_list = set(re.findall(r'<span class="cutp">([A-D])<b>', html))
    assert cut_in_chart == cut_in_list, (
        "the chart cuts %s and the list cuts %s" % (cut_in_chart, cut_in_list))


def test_a_segment_with_nothing_in_it_is_not_drawn(page_script):
    """A legend reading "Media 0" beside an arc of no length is noise, and
    the arc is worse than noise: a zero-length stroke with a round cap still
    paints a dot on the ring at whatever offset it was given."""
    at = page_script.index(_IIFE_CLOSE)
    probe = ("\nconsole.log(JSON.stringify({html: evDonut(["
             "{k:'a',n:3,label:'Three',cls:'f-hi'},"
             "{k:'b',n:0,label:'Nothing',cls:'f-na'}], 3, 'rows')}));\n")
    src = page_script.script
    js = "var __PAGE_IDS = %s;\n%s\n%s" % (
        json.dumps(page_script.ids), _SHIM, src[:at] + probe + src[at:])
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stderr[-2000:]
    html = json.loads(r.stdout.strip().splitlines()[-1])["html"]
    assert "Three" in html, "the donut drew nothing at all"
    assert "Nothing" not in html, "an empty segment was given a slice and a legend"


# ── the animation is never what makes a chart visible ─────────────────────

def test_nothing_is_drawn_by_an_animation(page_script):
    """The node harness has no requestAnimationFrame, which is the same
    position a background tab, a print and a screenshot-before-first-paint
    are in. Every chart must already be at its full size in the markup: a bar
    grown from a keyframe is a bar at zero for as long as nobody is looking,
    which is also exactly when a page gets captured."""
    html = _render(page_script, _recommend(
        [_cand("A", 99, "P1"), _cand("B", 80, "P2")],
        excluded=[{"name": "Cut", "tier": "P3", "total": 40}],
        top_five=[{"name": "A", "tier": "P1", "total": 99, "when": "w",
                   "where": "x", "case": "c"}]))
    heights = [float(x) for x in re.findall(r'<span class="cl [^"]*" style="height:([\d.]+)%', html)]
    assert heights and all(h > 0 for h in heights), (
        "a column is written at zero height: %s" % heights)
    widths = [float(x) for x in re.findall(r'<div class="fb" style="width:([\d.]+)%', html)]
    assert widths and all(w > 0 for w in widths), (
        "a funnel step is written at zero width: %s" % widths)
    dashes = re.findall(r'stroke-dasharray="([\d.]+) ', html)
    assert dashes and any(float(d) > 0 for d in dashes), (
        "every arc is written at zero length")
    assert "evi-anim" not in html, (
        "the armed class is in the markup, so the animation is running before "
        "anything has confirmed the page is painting")


def test_the_armer_is_reached_and_needs_a_frame_to_fire(page_script):
    """The class that turns the animations on is added from inside a
    requestAnimationFrame callback, so an environment that never paints never
    gets it and keeps the finished chart."""
    at = page_script.index(_IIFE_CLOSE)
    probe = ("\nvar __frames = [];\n"
             "global.requestAnimationFrame = function(fn){ __frames.push(fn); return 1; };\n"
             "current = __RUN;\nrender(__RUN);\n"
             "var el = document.getElementById('drawerBody');\n"
             "var before = __frames.length;\n"
             "__frames.forEach(function(f){ f(); });\n"
             "console.log(JSON.stringify({queued: before, cls: String(el.className || '')}));\n")
    src = page_script.script
    js = ("var __PAGE_IDS = %s;\n%s\nvar __RUN = %s;\n%s"
          % (json.dumps(page_script.ids), _SHIM, json.dumps(
              _recommend([_cand("A", 99, "P1")])), src[:at] + probe + src[at:]))
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=90)
    assert r.returncode == 0, r.stderr[-2000:]
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got["queued"] == 1, (
        "render() queued %d animation frames, expected exactly one"
        % got["queued"])


# ── the search funnel ─────────────────────────────────────────────────────
#
# Discovery names candidates and then confirms each with a separate search.
# Only survivors reach the list, so a reader shown one event cannot tell
# whether one was all there ever was or the only one of eleven that held up.
# The funnel is what makes those two reports look different.

def _funnel_bars(body):
    """The three numbers in the search funnel, and only those.

    The recommendation draws a funnel of its own higher up the page. A bare
    match on the bar markup reads whichever one comes first, which is how the
    first version of these tests asserted on the wrong chart.
    """
    at = body.index("What the search looked at")
    return [int(n) for n in
            re.findall(r'<div class="fb"[^>]*><b>(\d+)</b>', body[at:])]


def _statuses(**cats):
    """statuses as discover() writes them, one entry per named category."""
    out = {}
    for cat, (proposed, found, kept, rejected) in cats.items():
        out[cat] = {"status": "ok", "note": "", "detail": "", "label": cat,
                    "proposed": proposed, "found": found, "kept": kept,
                    "rejected": rejected}
    return out


def test_the_funnel_says_how_many_candidates_were_looked_at(page_script):
    body = _render(page_script, _recommend(
        [_cand("Kept One", 71, "P2")],
        statuses=_statuses(
            industry_flagship=(4, 1, 1, [{"name": "Gone Show",
                                          "reason": "the 2026 edition was the last"}]),
            vertical_summit=(3, 1, 1, [{"name": "Wrong Crowd",
                                        "reason": "no edition in the window"}]))))
    assert "What the search looked at" in body
    # 7 named, 2 confirmed, 2 kept. Scoped to this section: the recommendation
    # draws a funnel of its own further up, and an unscoped match would read
    # that one's bars and pass on the wrong chart.
    nums = _funnel_bars(body)
    assert nums == [7, 2, 2], nums
    assert "Gone Show" in body and "the 2026 edition was the last" in body
    assert "Wrong Crowd" in body


def test_a_run_recorded_before_the_split_draws_no_funnel(page_script):
    """The numbers a stored run does not carry are not invented. "named 3,
    confirmed 3" is a claim that run never made."""
    body = _render(page_script, _recommend(
        [_cand("Kept One", 71, "P2")],
        statuses={"industry_flagship": {"status": "ok", "note": "", "detail": "",
                                        "label": "Industry flagship", "found": 1,
                                        "kept": 1}}))
    assert "What the search looked at" not in body


def test_a_candidate_nothing_could_check_is_not_reported_as_ruled_out(page_script):
    """The distinction the whole module is built around, in the one place a
    reader actually sees it. Ruled out is a fact about the client's year.
    Could not be checked is a hole in the analysis."""
    body = _render(page_script, _recommend(
        [_cand("Kept One", 71, "P2")],
        # 4 named, 1 confirmed, 1 ruled out. The other 2 are unchecked.
        statuses=_statuses(industry_flagship=(4, 1, 1, [
            {"name": "Gone Show", "reason": "discontinued"}]))))
    assert "2 candidates could not be checked" in body
    assert "not because they were found wanting" in body
    ruled = body[body.index('class="evi-ruled"'):]
    assert "Gone Show" in ruled
    assert ruled.count('class="rr"') == 1, (
        "an unchecked candidate was listed as one a search ruled out")


def test_no_warning_when_every_candidate_reached_a_verdict(page_script):
    """The control. A banner that always fires stops carrying information."""
    body = _render(page_script, _recommend(
        [_cand("Kept One", 71, "P2")],
        statuses=_statuses(industry_flagship=(2, 1, 1, [
            {"name": "Gone Show", "reason": "discontinued"}]))))
    assert "could not be checked" not in body


def test_the_funnel_never_shows_more_kept_than_confirmed(page_script):
    """Dedup happens after confirmation, so kept is confirmed minus the events
    already listed under another category. A funnel that widened would mean
    the page had invented a row."""
    body = _render(page_script, _recommend(
        [_cand("Kept One", 71, "P2")],
        statuses=_statuses(industry_flagship=(5, 3, 1, []),
                           vertical_summit=(2, 2, 1, []))))
    nums = _funnel_bars(body)
    assert nums == [7, 5, 2], nums
    assert nums[0] >= nums[1] >= nums[2]
    assert "3 events already listed under another category" in body
