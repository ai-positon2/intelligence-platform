"""Competitor Analysis: static/js/competitor_analysis_data.js run in Node, and the page script run against a stub page and the real report the server builds.

The server decides (tests/test_paid_compete*.py). The page only words and draws it, so the shapes come from competitor_analysis_data.js (executed here, not read), the report is the server's own
`report_for` over a store holding real-shaped competitor ads, and competitor_analysis.js is run for real against a stub page (the same one the Ad Copy Checker's tests use).
"""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import MemStore  # noqa: E402
from test_paid_compete import keep, mad, own_raw_ads, picture_only, raw_of  # noqa: E402
from test_paid_compete_math import GOOGLE_REAL, META_REAL, ad, pool_with_price  # noqa: E402
from tests.test_copycheck_js import HARNESS  # noqa: E402
from tracker import paid_compete as pc  # noqa: E402
from tracker import paid_compete_fetch as cf  # noqa: E402
from tracker import paid_compete_math as cm  # noqa: E402
from tracker import paid_compete_settings as cs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
DATA, PAGE, SCOPE = os.path.join(JS, "competitor_analysis_data.js"), os.path.join(JS, "competitor_analysis.js"), os.path.join(JS, "pm_scope.js")
TEMPLATE = os.path.join(ROOT, "templates", "competitor_analysis.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
NOW = dt.datetime(2026, 10, 27, 12, 0, tzinfo=dt.timezone.utc)
G = "1234567890"
ACCTS = {"accounts": [{"id": G, "name": "Acme <b>Dental</b>", "currency": "USD", "group": None}], "groups": []}
HARNESS_PAGE = (HARNESS.replace("/p2/paid-marketing/ad-copy-checker", "/p2/paid-marketing/competitor-analysis")
                .replace("hidden: els[k].hidden, value:", "hidden: els[k].hidden, disabled: els[k].disabled, title: els[k].title || \"\", value:")
                .replace("const make = s => ({ dataset: s.dataset || {},", "const make = s => ({ value: s.value, dataset: s.dataset || {},")
                .replace("    for (let i = 0; i < 6; i++) await tick();\n  }\n  const out", "    if (step.change) { const [sel, closest] = step.change; const t = target({ closest }); targets.push(t); el(sel).handlers.change({ target: t }); }\n    for (let i = 0; i < 6; i++) await tick();\n  }\n  const out"))
MA = "act_1234567"


def run(expr, **data):
    pre = "".join("const %s=%s;" % (key, json.dumps(v)) for key, v in data.items())
    code = "const X=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(DATA), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.fixture
def own(monkeypatch):
    from tracker import paid_copycheck
    monkeypatch.setattr(paid_copycheck, "load_raw", lambda account_id, **kw: (raw_of("meta" if account_id.startswith("act_") else "google", own_raw_ads("meta" if account_id.startswith("act_") else "google")), False,
                                                                               {"id": account_id, "name": "Acme <b>Dental</b>"}))


def read_store():
    """A store with three competitors saved and read: Tandem (the two real rows on Google and Meta) and two price-led ones that give the report gaps, offers and a competitor with no Facebook page."""
    s = MemStore()
    cs.save_account(s, G, "Acme", {"competitors": "Tandem | tandemdiabetes.com | TandemDiabetes\nCheap Co | cheap.example\nCheaper | cheaper.example", "region": "US"}, "ana@position2.com", NOW)
    cs.save_account(s, MA, "Acme", {"competitors": "Tandem | tandemdiabetes.com | TandemDiabetes\nCheap Co | cheap.example\nCheaper | cheaper.example", "region": "US"}, "ana@position2.com", NOW)
    t = [cm.meta_ad(META_REAL, asof=NOW.date())]
    g = [cm.google_ad(GOOGLE_REAL, asof=NOW.date())]
    read = {"error": "", "skipped": "", "fetchedAt": "2026-10-26T00:00:00+00:00"}
    s.state_set(cf.cache_key("google", {"domain": "tandemdiabetes.com"}, "US"), dict(read, ads=g, items=1))
    for domain, who in (("cheap.example", "Cheap <i>Co</i>"), ("cheaper.example", "Cheaper")):
        ads = pool_with_price(n_each=5, who=(who,))
        for a in ads:
            a["platform"] = "google"
        s.state_set(cf.cache_key("google", {"domain": domain}, "US"), dict(read, ads=ads, items=5))
    s.state_set(cf.cache_key("meta", {"facebook": "https://www.facebook.com/TandemDiabetes"}, "US"), dict(read, ads=t, items=1))
    s.state_set("compete:job:google:%s+meta:%s" % (G, MA), {"status": "done", "startedAt": "2026-10-26T00:00:00+00:00", "finishedAt": "2026-10-26T00:01:00+00:00", "by": "ana@position2.com", "total": 3, "skippedFresh": 0,
                                                             "steps": [{"name": "Tandem", "platform": "google", "status": "done", "ads": 1, "error": ""}, {"name": "Cheap Co", "platform": "meta", "status": "failed", "ads": 0, "error": "The Meta read failed: x"}]})
    return s


@pytest.fixture
def report(own):
    return json.loads(json.dumps(dict(pc.report_for(G, MA, mon=read_store(), now=NOW), competitorsText=cs.lines_of(cs.account_of(cs.load(read_store()), G)["competitors"]), senders={"apify": True})))


@pytest.fixture
def empty_report(own):
    return json.loads(json.dumps(dict(pc.report_for(G, MA, mon=MemStore(), now=NOW), competitorsText="", senders={"apify": True})))


# ── the wording and the shapes ──────────────────────────────────────────────

def test_the_tiles_count_competitors_ads_gaps_ideas_and_how_old_the_read_is(report, empty_report):
    t = run("X.tiles(R)", R=report)
    assert [x[0] for x in t] == ["Competitors", "Competitor ads read", "Message gaps", "Ideas", "Last read"]
    assert t[0][1:3] == ["3", "saved for this account"] and t[1][1] == str(report["counts"]["competitorAds"]) and t[1][2] == "from 4 reads" and t[2][3] == "warn" and t[2][2] == "things they say more than we do"
    assert t[3][1] == str(report["counts"]["ideas"]) and t[4][1:3] == ["1 day ago", "the oldest of the competitors' reads"]
    e = run("X.tiles(R)", R=empty_report)
    assert e[0][1:] == ["0", "none saved yet: add the first", "warn"] and e[1][2] == "press Refresh to read them" and e[2][2] == "needs their ads first" and e[4][1:3] == ["never", "no competitor has been read"]
    clean = run("X.tiles(R)", R={"counts": {"competitorAds": 8, "reads": 2, "gaps": 0, "ideas": 0}, "competitors": [{}], "analysis": {"meta": {"competitors": [{"ageDays": 0}]}}})
    assert clean[2][1:] == ["0", "no clear gap against our ads", "good"] and clean[3][2] == "none yet" and clean[4][1] == "today"


SLOW = "(Google shows each text ad as a picture, so those reads are slow)"


def test_the_cost_of_a_refresh_is_said_before_it_is_paid():
    assert run("X.estimateText(E, 7)", E={"reads": 4, "ads": 120, "usd": 0.45}) == "A refresh reads 4 competitor pages, up to 120 ads, about $0.45 at most and a few minutes %s. Reads newer than 7 days are not paid for again." % SLOW
    assert run("X.estimateText(E, 1)", E={"reads": 1, "ads": 100, "usd": 0.16, "minutes": 15}) == "A refresh reads 1 competitor page, up to 100 ads, about $0.16 at most and up to about 15 minutes %s. Reads newer than 1 day are not paid for again." % SLOW
    assert "up to about 1 minute " in run("X.estimateText(E, 7)", E={"reads": 1, "ads": 10, "usd": 0.05, "minutes": 1})
    assert run("X.estimateText({}, 7)") == "Nothing can be read yet: a competitor needs a website or a Facebook page." and run("X.estimateText(null, 7)").startswith("Nothing can be read")
    assert run("X.money(1.005)") in ("$1.00", "$1.01") and run("X.money(0.3)") == "$0.30"


def test_a_refresh_is_worded_while_it_runs_when_it_fails_and_when_it_is_done():
    steps = [{"name": "A", "platform": "meta", "status": "done", "ads": 3, "error": ""}, {"name": "B", "platform": "google", "status": "reading", "ads": 0, "error": ""}, {"name": "C", "platform": "meta", "status": "waiting", "ads": 0, "error": ""}]
    assert run("X.jobText(J)", J={"status": "running", "steps": steps}) == "Reading competitor ads: 1 of 3 done. This takes a minute or two."
    assert run("X.jobText(J)", J={"status": "failed", "error": "RuntimeError: x", "steps": []}) == "The refresh failed: RuntimeError: x."
    assert run("X.jobText(J)", J={"status": "failed", "steps": []}) == "The refresh failed: no reason given."
    done = [{"name": "A", "platform": "meta", "status": "done", "ads": 3, "error": ""}, {"name": "B", "platform": "google", "status": "failed", "ads": 0, "error": "x"}]
    assert run("X.jobText(J)", J={"status": "done", "finishedAt": "2026-10-26T00:01:00+00:00", "by": "ana", "steps": done}) == "Last refresh 2026-10-26 by ana: 1 read done, 1 failed."
    assert run("X.jobText(J)", J={"status": "done", "steps": [], "skippedFresh": 3}) == "Every competitor was read within the last week, so nothing was read again (3 reads kept)."
    assert run("X.jobText(J)", J={"status": "done", "steps": [], "skippedFresh": 0}).endswith("again.") and run("X.jobText(null)") == ""
    assert run("X.stepLine(S)", S=done[0]) == {"text": "A on Meta: 3 ads", "tone": "ok"} and run("X.stepLine(S)", S=done[1]) == {"text": "B on Google: x", "tone": "bad"}
    assert run("X.stepLine(S)", S=steps[1]) == {"text": "B on Google: reading", "tone": "wait"} and run("X.stepLine(S)", S=steps[2])["text"] == "C on Meta: waiting"
    assert run("X.stepLine(S)", S=dict(done[0], ads=150, capped=True, limit=150)) == {"text": "A on Meta: 150 ads (limit of 150 reached, they run more)", "tone": "warn"}
    assert run("X.stepLine(S)", S=dict(done[0], ads=40, partial="x")) == {"text": "A on Meta: 40 ads (stopped early, only part was read)", "tone": "warn"}


def test_the_competitors_are_a_table_per_platform_with_what_failed_or_was_skipped_in_words(report):
    rows = run("X.competitorRows(R, 'google')", R=report)
    assert [r["name"] for r in rows] == ["Tandem", "Cheap Co", "Cheaper"]
    by = {r["name"]: r for r in rows}
    t = by["Tandem"]
    assert t["status"] == "read" and t["note"] == "read 1 day ago" and t["ads"] == 1 and t["longest"] == 497 and t["formats"] == "1 text" and t["median"] == 497 and t["landing"] == "tandemdiabetes.com (1)"
    meta = {r["name"]: r for r in run("X.competitorRows(R, 'meta')", R=report)}
    assert meta["Tandem"]["formats"] == "1 dynamic" and meta["Tandem"]["ctas"] == "See details (1)" and meta["Tandem"]["landing"].startswith("tandemdiabetes.com/landing-pages/tandem-pump-rx")
    assert meta["Cheap Co"]["status"] == "skipped" and "No Facebook page" in meta["Cheap Co"]["note"]
    bad = run("X.competitorRows(R, 'meta')", R={"analysis": {"meta": {"competitors": [{"name": "X", "error": "boom", "skipped": "", "count": 0, "formats": {}, "ctas": {}, "landing": [], "longest": []}]}}})
    assert bad[0]["status"] == "failed" and bad[0]["note"] == "boom"
    empty = run("X.competitorRows(R, 'meta')", R={"analysis": {"meta": {"competitors": [{"name": "X", "error": "", "skipped": "", "count": 0, "ageDays": 0, "formats": {}, "ctas": {}, "landing": [], "longest": []}]}}})
    assert empty[0]["status"] == "empty" and empty[0]["note"] == "read, no ads found" and run("X.competitorRows(R, 'meta')", R={"analysis": {}}) == []


def test_the_gaps_are_bars_with_the_example_and_a_platform_that_could_not_be_judged_says_why(report):
    g = run("X.gapRows(R, 'google')", R=report)
    assert g["judged"] is True and g["gaps"] and g["gaps"][0]["points"] >= 25 and g["gaps"][0]["example"]["line"]
    price = [x for x in g["gaps"] if x["label"] == "A price or a saving"][0]
    assert run("X.gapText(G)", G={"their": price["their"], "own": price["own"], "ads": price["ads"], "who": price["who"]})== "83% of their lines, 0% of ours (10 ads from 2 competitors)"
    m = run("X.gapRows(R, 'meta')", R=report)
    assert m["judged"] is False and m["why"].startswith("Only 3 lines of the competitors' copy were read") and m["gaps"] == []
    assert run("X.gapRows(R, 'tiktok')", R=report) == {"judged": False, "why": "", "gaps": [], "edges": []}
    assert run("X.exampleText(E)", E={"line": "Save $10", "who": "A", "days": 300}) == "\"Save $10\" (A, running 300 days)" and run("X.exampleText(E)", E={"line": "x", "who": "A", "days": None}) == "\"x\" (A)" and run("X.exampleText(null)") == ""


def test_the_ideas_are_cards_with_the_level_the_platform_and_the_example(report):
    cards = run("X.ideaCards(R)", R=report)
    assert cards and cards[0]["tone"] == "bad" and cards[0]["level"] == "High" and cards[0]["platform"] == "Google Ads" and cards[0]["title"].startswith("Google Ads: ") and cards[0]["tryThis"] and cards[0]["example"].startswith("\"")
    assert {c["tone"] for c in cards} <= {"bad", "warn", "wait"} and run("X.ideaCards({})") == [] and run("X.levelInfo('weird')") == {"label": "weird", "tone": "unk"}


def test_offers_the_longest_runners_and_the_newest_ads_are_rows(report):
    offers = run("X.offerRows(R, 'google')", R=report)
    assert offers and offers[0]["lines"] >= 1 and "Cheap" in offers[0]["who"]
    win = run("X.winnerRows(R, 'google')", R=report)
    assert win[0]["days"] == 497 and win[0]["who"] == "Tandem Diabetes Care, Inc." and win[0]["headline"] == "Connected with Dexcom - Get Started Today"
    news = run("X.newestRows(R, 'google')", R=report)
    assert all(n["since"] for n in news) and [n["since"] for n in news] == sorted([n["since"] for n in news], reverse=True)
    assert run("X.offerRows(R, 'x')", R=report) == [] and run("X.winnerRows(R, 'x')", R=report) == [] and run("X.newestRows(R, 'x')", R=report) == []


def test_what_the_page_sends_for_each_change_and_the_line_that_says_where_it_stands(report, empty_report):
    co = {"name": "Tandem", "domain": "tandemdiabetes.com", "facebook": "", "fbSource": "", "logo": "https://l.example/t.png", "industry": "devices"}
    assert run("X.addBody('1234567890', C, N)", C=co, N={G: "Acme"}) == {"account": G, "op": "add", "competitor": co, "names": {G: "Acme"}} and run("X.addBody('1', C, null)", C=co)["names"] == {}
    assert run("X.removeBody('1234567890', 'Tandem', N)", N={G: "Acme"}) == {"account": G, "op": "remove", "competitor": {"name": "Tandem"}, "names": {G: "Acme"}}
    assert run("X.pageBody('1234567890', C, '  TandemDiabetes ', N)", C=dict(co, googleAdvertiser="AR1"), N={}) == {"account": G, "op": "update", "names": {}, "competitor": {"name": "Tandem", "domain": "tandemdiabetes.com", "googleAdvertiser": "AR1",
                                                                                                                                                      "logo": "https://l.example/t.png", "industry": "devices", "facebook": "TandemDiabetes", "fbSource": "manual"}}
    assert run("X.regionBody('1234567890', ' de ', N)", N={G: "Acme"}) == {"account": G, "op": "region", "region": "DE", "names": {G: "Acme"}} and run("X.regionBody('1', '', null)")["region"] == "US"
    assert run("X.findBody('1', 'Tandem', N, true)", N={"1": "A"}) == {"account": "1", "name": "Tandem", "names": {"1": "A"}, "force": True} and run("X.findBody('1', 'T', null)") == {"account": "1", "name": "T", "names": {}, "force": False}
    assert run("X.standing(R)", R=empty_report).startswith("No competitors are saved for this account yet.") and run("X.standing({status: 'no_competitor_data'})").startswith("The competitors are saved, but none of their ads")
    assert run("X.standing(R)", R=report) == report["headline"]


def test_the_company_search_is_cards_with_what_tells_the_companies_apart_and_a_note_that_says_what_was_found():
    res = {"companies": [{"name": "Tandem Diabetes Care", "domain": "tandemdiabetes.com", "website": "https://tandemdiabetes.com", "facebook": "https://www.facebook.com/TandemDiabetes", "fbSource": "apollo", "logo": "https://l.example/t.png",
                          "industry": "medical devices", "location": "San Diego, California, United States", "description": "Insulin pumps.", "added": False},
                         {"name": "Tandem Bank", "domain": "tandem.co.uk", "website": "https://tandem.co.uk", "facebook": "", "fbSource": "", "logo": "", "industry": "", "location": "", "description": "A bank.", "added": True}], "error": None}
    cards = run("X.resultCards(R)", R=res)
    assert cards[0] == {"name": "Tandem Diabetes Care", "domain": "tandemdiabetes.com", "website": "https://tandemdiabetes.com", "logo": "https://l.example/t.png", "sub": "medical devices \u00b7 San Diego, California, United States", "description": "Insulin pumps.",
                        "added": False, "hasFacebook": True, "company": {"name": "Tandem Diabetes Care", "domain": "tandemdiabetes.com", "facebook": "https://www.facebook.com/TandemDiabetes", "fbSource": "apollo", "logo": "https://l.example/t.png", "industry": "medical devices"}}
    assert cards[1]["sub"] == "" and cards[1]["added"] is True and cards[1]["hasFacebook"] is False and cards[1]["company"]["facebook"] == "" and run("X.resultCards(null)") == []
    assert run("X.searchNote(R, 'tandem')", R=res) == "2 companies found. Pick the ones that are competitors."
    assert run("X.searchNote(R, 'zzz')", R={"companies": [], "error": None}) == "No company matched \"zzz\". Try a shorter name, or type its website address."
    assert run("X.searchNote(R, 'x')", R={"companies": [], "error": {"message": "Apollo is rate-limiting us. Try again in a minute."}}) == "Apollo is rate-limiting us. Try again in a minute."
    assert run("X.searchNote(R, 'x')", R={"companies": [{"name": "A"}], "error": {"message": "Apollo did not respond."}}) == "Apollo did not respond. 1 company found. Pick the ones that are competitors." and run("X.searchNote(null, 'x')") == ""


def test_each_competitor_says_whether_its_meta_ads_can_be_read_and_where_the_page_came_from():
    rep = {"competitors": [{"name": "A", "domain": "a.com", "facebook": "https://www.facebook.com/AAA", "fbSource": "search", "logo": "", "industry": "devices"}, {"name": "B", "domain": "b.com", "facebook": "https://www.facebook.com/BBB", "fbSource": "apollo"},
                           {"name": "C", "domain": "c.com", "facebook": "https://www.facebook.com/CCC", "fbSource": "manual"}, {"name": "D", "domain": "d.com", "facebook": "", "googleAdvertiser": "AR1"}]}
    cards = run("X.competitorCards(R, F)", R=rep, F={"D": True})
    assert [c["fbText"] for c in cards] == ["Facebook page, found by searching for the company", "Facebook page, from the company's own record", "Facebook page, typed by the team", "Looking for its Facebook page\u2026"]
    assert [c["fbTone"] for c in cards] == ["ok", "ok", "ok", "wait"] and [c["canFind"] for c in cards] == [False, False, False, False] and cards[3]["finding"] is True and cards[3]["advertiser"] == "AR1"
    idle = run("X.competitorCards(R)", R=rep)[3]
    assert idle["fbText"] == "No Facebook page yet, so its Meta ads cannot be read." and idle["fbTone"] == "warn" and idle["canFind"] is True and idle["finding"] is False
    assert run("X.facebookText({facebook: 'https://www.facebook.com/X'}, false)") == {"text": "Facebook page", "tone": "ok"} and run("X.competitorCards({})") == []
    assert run("X.forText(R)", R={"names": {"google": "Acme <b>Dental</b>", "meta": "Acme Meta"}}) == "These competitors are saved for Acme <b>Dental</b> + Acme Meta only. Each ad account has its own list." and run("X.forText({})").startswith("These competitors are saved for this ad account only")


def test_a_competitor_read_to_its_limit_or_stopped_early_is_flagged_in_the_table_and_the_csv():
    rep = {"platforms": ["meta"], "ideas": [], "analysis": {"meta": {"compare": {"gaps": []}, "offers": [], "winners": [], "competitors": [
        stats_of("A", [], age=0, count=150, active=150, capped=True, limit=150, read=150, partial=""), stats_of("B", [], age=0, count=40, active=40, capped=False, limit=150, read=40, partial="The Meta read stopped before it finished (x), so only part of the ads were read."),
        stats_of("C", [], age=0, count=5, active=5, capped=False, limit=150, read=5, partial="")]}}}
    rows = {r["name"]: r for r in run("X.competitorRows(R, 'meta')", R=rep)}
    assert rows["A"]["capped"] is True and rows["A"]["flag"] == "Limit of 150 ads reached: they run more than were read." and rows["B"]["partial"] is True and rows["B"]["flag"] == "Stopped early: only part of their ads were read."
    assert rows["C"]["flag"] == "" and rows["C"]["capped"] is False and rows["C"]["noText"] == ""
    nt = run("X.competitorRows(R, 'meta')", R={"analysis": {"meta": {"competitors": [stats_of("N", [], count=3, noText=9), stats_of("O", [], count=3, noText=1), stats_of("P", [], error="boom", noText=4)]}}})
    assert [r["noText"] for r in nt] == ["9 ads have no text to read (a picture or a video only).", "1 ad has no text to read (a picture or a video only).", ""]
    csv = {r[2]: r for r in run("X.csvRows(R)", R=dict(rep, platforms=["meta"])) if r and r[0] == "Competitor"}
    assert csv["A"][4].endswith(". Limit of 150 ads reached: they run more than were read") and csv["B"][4].endswith(". Stopped early: only part was read") and "Limit" not in csv["C"][4]
    withtext = run("X.csvRows(R)", R={"platforms": ["meta"], "ideas": [], "analysis": {"meta": {"compare": {"gaps": []}, "offers": [], "winners": [], "competitors": [stats_of("N", [], count=3, active=3, noText=2)]}}})
    assert [r for r in withtext if r and r[0] == "Competitor"][0][4].endswith(". 2 ads without text (picture or video only) not counted")
    bad = run("X.competitorRows(R, 'meta')", R={"analysis": {"meta": {"competitors": [stats_of("X", [], error="boom", capped=True, limit=150, count=0)]}}})
    assert bad[0]["flag"] == ""                                                                      # a failed read says its error, not that it hit a limit


def test_the_csv_has_the_ideas_the_gaps_the_competitors_the_offers_and_the_longest_runners(report):
    rows = run("X.csvRows(R)", R=report)
    assert rows[0] == ["Competitor Analysis"] and rows[2] == run("X.HEAD")
    sections = [r[0] for r in rows[3:]]
    assert sections[0] == "Idea" and {"Idea", "Gap", "Competitor", "Offer", "Longest running"} <= set(sections)
    comp = [r for r in rows[3:] if r[0] == "Competitor" and r[2] == "Tandem" and r[1] == "Google Ads"][0]
    assert comp[3] == "ads" and comp[4] == "1 active, 0 new in 30 days, median 497 days running" and comp[5] == 1
    skipped = [r for r in rows[3:] if r[0] == "Competitor" and r[2] == "Cheap Co" and r[1] == "Meta Ads"][0]
    assert skipped[3].startswith("No Facebook page") and skipped[4] == ""


# ── the page script, run against a stub page ────────────────────────────────

def page(scenario):
    sc = dict(scenario)
    out = subprocess.run(["node", "-e", HARNESS_PAGE, json.dumps([SCOPE, DATA, PAGE]), json.dumps(sc)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def opened(rep, steps=None, more=(), **kw):
    sc = {"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/compete/report", "body": rep}] + list(more), "steps": [{"pick": G}] + (steps or [])}
    sc.update(kw)
    return page(sc)


def test_every_element_the_script_reaches_for_is_in_the_template_and_the_scripts_are_loaded():
    html = open(TEMPLATE, encoding="utf-8").read()
    js = open(PAGE, encoding="utf-8").read()
    for sel in sorted(set(re.findall(r'\$\("(#[A-Za-z]+)"\)', js))):
        assert 'id="%s"' % sel[1:] in html, sel
    assert html.index("pm_picker.js") < html.index("pm_platform.js") < html.index("pm_scope.js") < html.index("competitor_analysis_data.js") < html.index("competitor_analysis.js")
    assert "—" not in html and "–" not in html and "—" not in js and "—" not in open(DATA, encoding="utf-8").read()


def test_nothing_is_read_until_an_account_is_chosen():
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}]})
    assert not any("/compete/report" in c for c in out["calls"]) and out["el"]["#empty"]["hidden"] is False and out["el"]["#out"]["hidden"] is True


def test_choosing_an_account_reads_the_report_and_remembers_it(report):
    out = opened(report)
    assert [c for c in out["calls"] if "/compete/report" in c] == ["/p2/paid-marketing/api/compete/report?account=1234567890"] and out["store"]["ca_account"] == G
    assert out["hist"][-1] == "/p2/paid-marketing/competitor-analysis?account=1234567890" and out["el"]["#out"]["hidden"] is False and out["el"]["#empty"]["hidden"] is True


def test_the_report_is_drawn_with_the_tiles_the_competitors_the_ideas_the_gaps_and_the_ads_and_markup_escaped(report):
    out = opened(report)
    el = out["el"]
    assert "Competitor ads read" in el["#kpis"]["html"] and "Message gaps" in el["#kpis"]["html"] and report["headline"] == el["#standing"]["text"]
    assert "Acme &lt;b&gt;Dental&lt;/b&gt;" in el["#fresh"]["html"] and "Google Ads" in el["#fresh"]["html"] and "<b>Dental</b>" not in el["#fresh"]["html"]
    assert "fComp" not in el and el["#fRegion"]["value"] == "US" and el["#compNote"]["text"] == "3 competitors" and "A refresh reads 4 competitor pages" in el["#estimate"]["text"]
    assert el["#compFor"]["text"] == "These competitors are saved for Acme <b>Dental</b> + Acme <b>Dental</b> only. Each ad account has its own list."
    comp = el["#compTables"]["html"]
    assert "<h4>Google Ads</h4>" in comp and "<h4>Meta Ads</h4>" in comp and "Tandem" in comp and "Cheap Co" in comp and "Median days running" in comp and "No Facebook page is set" in comp
    assert el["#ideasCard"]["hidden"] is False and "Try this:" in el["#ideas"]["html"] and 'class="bi-lv bad">High' in el["#ideas"]["html"] and "Example from them:" in el["#ideas"]["html"]
    assert el["#gapsCard"]["hidden"] is False and 'class="ca-bar"' in el["#gaps"]["html"] and "Bars: them, then us." in el["#gaps"]["html"] and "Only 3 lines of the competitors&#39; copy" in el["#gaps"]["html"]
    ads = el["#ads"]["html"]
    assert el["#adsCard"]["hidden"] is False and "Running longest" in ads and "Connected with Dexcom - Get Started Today" in ads and "<i>Co</i>" not in ads
    blind = el["#blind"]["html"]
    assert "<b>This time:</b>" in blind and "Google shows a text ad as a picture" in blind and "How the competitors&#39; ads perform" in blind
    assert "kept for 7 days" in el["#foot"]["html"] and "Last refresh 2026-10-26 by ana@position2.com" in el["#jobBox"]["html"]
    assert el["#refreshBtn"]["disabled"] is False


def test_an_account_with_no_competitor_says_so_and_cannot_refresh(empty_report):
    out = opened(empty_report)
    el = out["el"]
    assert el["#standing"]["text"].startswith("No competitors are saved for this account yet.") and el["#compNote"]["text"] == "none yet" and el["#ideasCard"]["hidden"] is True and el["#gapsCard"]["hidden"] is True
    assert el["#refreshBtn"]["disabled"] is True and "Nothing can be read yet" in el["#estimate"]["text"] and el["#compTables"]["html"] == ""


def test_a_server_without_an_apify_token_cannot_refresh_and_says_why(report):
    report["senders"] = {"apify": False}
    out = opened(report)
    assert out["el"]["#refreshBtn"]["disabled"] is True and out["el"]["#refreshBtn"]["title"] == "APIFY_API_TOKEN is not set on this server"
    ok = opened(dict(report, senders={"apify": True}))
    assert ok["el"]["#refreshBtn"]["disabled"] is False and ok["el"]["#refreshBtn"]["title"] == ""


TANDEM_CO = {"name": "Tandem Diabetes Care", "domain": "tandemdiabetes.com", "website": "https://tandemdiabetes.com", "facebook": "https://www.facebook.com/TandemDiabetes", "fbSource": "apollo", "logo": "https://l.example/t.png",
             "industry": "medical devices", "location": "San Diego, California, United States", "description": "Insulin pumps.", "added": False}
INSULET_CO = {"name": "Insulet", "domain": "insulet.com", "website": "https://insulet.com", "facebook": "", "fbSource": "", "logo": "", "industry": "medical devices", "location": "", "description": "", "added": False}
SEARCH = {"companies": [TANDEM_CO, INSULET_CO], "error": None}
NAMES = {G: "Acme <b>Dental</b>", MA: "Acme <b>Dental</b>"}


def picking(report, steps, more=(), search=SEARCH, query="tandem", **kw):
    """The account is chosen, a name typed and searched, then `steps`."""
    return opened(report, [{"set": ["#caQ", query]}, {"submit": ["#caForm", {}]}] + steps, more=[{"match": "/compete/search", "body": search}] + list(more), **kw)


def change_ok(competitors, region="US"):
    return {"match": "/compete/competitors", "method": "POST", "body": {"competitors": competitors, "region": region}}


ADD_FIRST = {"fire": ["#caResults", {"[data-add]": {"dataset": {"add": "0"}}}]}
ADD_SECOND = {"fire": ["#caResults", {"[data-add]": {"dataset": {"add": "1"}}}]}


def test_a_name_is_searched_for_the_account_and_the_companies_found_are_shown_to_pick_from(report):
    out = picking(report, [])
    assert [c for c in out["calls"] if "/compete/search" in c] == ["/p2/paid-marketing/api/compete/search?account=1234567890&q=tandem"]
    html = out["el"]["#caResults"]["html"]
    assert "2 companies found. Pick the ones that are competitors." in html and html.count("Add as competitor") == 2 and 'data-add="0"' in html and 'data-add="1"' in html
    assert "<b>Tandem Diabetes Care</b>" in html and "medical devices \u00b7 San Diego, California, United States" in html and "tandemdiabetes.com \u00b7 Facebook page known" in html and 'src="https://l.example/t.png"' in html
    assert '<span class="ca-logo ca-logo-x">I</span>' in html and out["el"]["#caSearch"]["disabled"] is False                  # a company without a logo shows its first letter


def test_what_the_search_returns_is_escaped_and_a_company_already_added_is_marked(report):
    evil = dict(TANDEM_CO, name="<img src=x onerror=alert(1)>", logo="https://l.example/\"><script>", added=True)
    out = picking(report, [], search={"companies": [evil, INSULET_CO], "error": None})
    html = out["el"]["#caResults"]["html"]
    assert "<img src=x" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html and "<script>" not in html and '<span class="ca-added">Added</span>' in html and html.count("Add as competitor") == 1


def test_a_search_that_found_nothing_or_failed_says_so_in_words(report):
    none = picking(report, [], search={"companies": [], "error": None}, query="zzzz")
    assert 'No company matched &quot;zzzz&quot;.' in none["el"]["#caResults"]["html"]
    down = picking(report, [], search={"companies": [], "error": {"kind": "not_configured", "message": "Company search is not configured on this deployment: APOLLO_API_KEY is missing. Type a website to add a competitor.", "retryable": False}})
    assert "APOLLO_API_KEY is missing" in down["el"]["#caResults"]["html"] and "No company matched" not in down["el"]["#caResults"]["html"]
    refused = picking(report, [], more=[], search={"companies": []})
    refused2 = opened(report, [{"set": ["#caQ", "tandem"]}, {"submit": ["#caForm", {}]}], more=[{"match": "/compete/search", "status": 400, "body": {"error": "Pick one Google Ads account."}}])
    assert "Pick one Google Ads account." in refused2["el"]["#msg"]["html"] and refused2["el"]["#caResults"]["html"] == "" and refused2["el"]["#caSearch"]["disabled"] is False
    down2 = opened(report, [{"set": ["#caQ", "tandem"]}, {"submit": ["#caForm", {}]}], reject="/compete/search")
    assert "The server could not be reached. Try again." in down2["el"]["#msg"]["html"] and down2["el"]["#caSearch"]["disabled"] is False and refused["el"]["#caResults"]["html"]


def test_a_name_too_short_or_no_account_chosen_searches_nothing_and_says_why(report):
    short = picking(report, [], query="t")
    assert not any("/compete/search" in c for c in short["calls"]) and "Type at least two letters" in short["el"]["#msg"]["html"]
    none = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}], "steps": [{"set": ["#caQ", "tandem"]}, {"submit": ["#caForm", {}]}]})
    assert not any("/compete/search" in c for c in none["calls"]) and "Pick an ad account first" in none["el"]["#msg"]["html"]


def test_picking_a_company_adds_it_to_this_account_with_its_page_and_marks_it_added(report):
    both = report["competitors"] + [{"name": "Tandem Diabetes Care", "domain": "tandemdiabetes.com", "facebook": "https://www.facebook.com/TandemDiabetes", "fbSource": "apollo", "logo": "https://l.example/t.png", "industry": "medical devices", "googleAdvertiser": ""}]
    out = picking(report, [ADD_FIRST], more=[change_ok(both)])
    [post] = out["posts"]
    assert post["url"] == "/p2/paid-marketing/api/compete/competitors" and post["body"] == {"account": G, "op": "add", "names": NAMES, "competitor": {"name": "Tandem Diabetes Care", "domain": "tandemdiabetes.com",
            "facebook": "https://www.facebook.com/TandemDiabetes", "fbSource": "apollo", "logo": "https://l.example/t.png", "industry": "medical devices"}}
    assert not any("/find-facebook" in c for c in out["calls"]) and len([c for c in out["calls"] if "/compete/report" in c]) == 2           # the page was known: nothing to look for; the report is read again
    assert '<span class="ca-added">Added</span>' in out["el"]["#caResults"]["html"] and out["el"]["#caResults"]["html"].count("Add as competitor") == 1


def test_a_company_with_no_known_facebook_page_has_it_looked_for_by_name_and_website_once_added(report):
    both = report["competitors"] + [{"name": "Insulet", "domain": "insulet.com", "facebook": "", "fbSource": "", "logo": "", "industry": "medical devices", "googleAdvertiser": ""}]
    found = [dict(c) for c in both[:-1]] + [dict(both[-1], facebook="https://www.facebook.com/InsuletCorp", fbSource="search")]
    more = [change_ok(both), {"match": "/compete/find-facebook", "method": "POST", "body": {"kept": True, "facebook": "https://www.facebook.com/InsuletCorp", "confidence": "high", "reasoning": "r", "competitors": found, "region": "US"}}]
    out = picking(report, [ADD_SECOND], more=more)
    assert [p["url"].rsplit("/", 1)[1] for p in out["posts"]] == ["competitors", "find-facebook"] and out["posts"][1]["body"] == {"account": G, "name": "Insulet", "names": NAMES, "force": False}
    assert len([c for c in out["calls"] if "/compete/report" in c]) == 3 and "Paste its page address" not in out["el"]["#msg"]["html"]


def test_a_page_that_could_not_be_checked_is_said_and_the_team_is_told_to_paste_it(report):
    both = report["competitors"] + [{"name": "Insulet", "domain": "insulet.com", "facebook": "", "fbSource": "", "logo": "", "industry": "", "googleAdvertiser": ""}]
    more = [change_ok(both), {"match": "/compete/find-facebook", "method": "POST", "body": {"kept": False, "facebook": "", "confidence": "low", "reasoning": "only a fan page was found", "error": "", "competitors": both, "region": "US"}}]
    msg = picking(report, [ADD_SECOND], more=more)["el"]["#msg"]["html"]
    assert "Insulet: only a fan page was found" in msg and "Paste its page address in the box if you know it, or Meta ads cannot be read for it." in msg and "pm-note" in msg
    err = [change_ok(both), {"match": "/compete/find-facebook", "method": "POST", "body": {"kept": False, "facebook": "", "error": "ANTHROPIC_API_KEY is not configured on this deployment.", "competitors": both, "region": "US"}}]
    assert "ANTHROPIC_API_KEY is not configured" in picking(report, [ADD_SECOND], more=err)["el"]["#msg"]["html"]
    refused = [change_ok(both), {"match": "/compete/find-facebook", "method": "POST", "status": 400, "body": {"error": "Insulet is not one of this account's competitors."}}]
    assert "not one of this account" in picking(report, [ADD_SECOND], more=refused)["el"]["#msg"]["html"]
    down = picking(report, [ADD_SECOND], more=[change_ok(both)], reject="/find-facebook")
    assert "The Facebook page was not looked for." in down["el"]["#msg"]["html"]


def test_while_the_page_is_looked_for_the_card_says_so_and_its_box_is_locked_and_it_is_freed_after(report):
    hold = {"match": "/compete/find-facebook", "method": "POST", "hold": True, "body": {"kept": False, "facebook": "", "reasoning": "x", "competitors": report["competitors"], "region": "US"}}
    find = {"fire": ["#caList", {"[data-find]": {"dataset": {"find": "1"}}}]}
    waiting = opened(report, [find], more=[hold])
    html = waiting["el"]["#caList"]["html"]
    assert html.count("Looking for its Facebook page\u2026") == 1 and html.count(" disabled") == 1 and html.count("Find its Facebook page") == 1                   # only the competitor being looked for is locked, and it has no find button
    freed = opened(report, [find, {"release": True}], more=[hold])
    assert "Looking for its Facebook page" not in freed["el"]["#caList"]["html"] and freed["el"]["#caList"]["html"].count("Find its Facebook page") == 2
    twice = opened(report, [find, find], more=[hold])
    assert len([p for p in twice["posts"] if p["url"].endswith("/find-facebook")]) == 1                                                       # a second click while it is looked for starts nothing


def test_a_company_that_cannot_be_added_says_why_and_can_be_tried_again(report):
    out = picking(report, [ADD_FIRST], more=[{"match": "/compete/competitors", "method": "POST", "status": 400, "body": {"error": "Tandem Diabetes Care is already in this account's competitors."}}])
    assert "already in this account" in out["el"]["#msg"]["html"] and out["targets"][0]["disabled"] is False if "targets" in out else "already in this account" in out["el"]["#msg"]["html"]
    assert len([c for c in out["calls"] if "/compete/report" in c]) == 1 and '<span class="ca-added">Added</span>' not in out["el"]["#caResults"]["html"]
    down = picking(report, [ADD_FIRST], reject="/compete/competitors")
    assert "Nothing was saved." in down["el"]["#msg"]["html"]


def test_the_competitors_of_the_account_are_cards_with_their_page_a_box_to_correct_it_and_a_remove_button(report):
    html = opened(report)["el"]["#caList"]["html"]
    assert "<h4>This account's competitors</h4>" in html and html.count("data-rm=") == 3 and html.count("Remove") >= 3 and 'data-rm="0"' in html and 'data-rm="2"' in html
    assert 'value="https://www.facebook.com/TandemDiabetes"' in html and html.count("Find its Facebook page") == 2 and "No Facebook page yet, so its Meta ads cannot be read." in html
    assert "Facebook page, typed by the team" in html and 'data-fb="0"' in html and 'data-find="1"' in html and 'data-find="0"' not in html
    empty = opened(dict(report, competitors=[]))["el"]["#caList"]["html"]
    assert "No competitors yet for this account." in empty and "data-rm" not in empty


def test_removing_a_competitor_posts_its_name_and_reads_the_report_again(report):
    left = [c for c in report["competitors"] if c["name"] != "Cheap Co"]
    out = opened(report, [{"fire": ["#caList", {"[data-rm]": {"dataset": {"rm": "1"}}}]}], more=[change_ok(left)])
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/compete/competitors", "body": {"account": G, "op": "remove", "competitor": {"name": "Cheap Co"}, "names": NAMES}}]
    assert len([c for c in out["calls"] if "/compete/report" in c]) == 2
    refused = opened(report, [{"fire": ["#caList", {"[data-rm]": {"dataset": {"rm": "1"}}}]}], more=[{"match": "/compete/competitors", "method": "POST", "status": 400, "body": {"error": "Cheap Co is not one of this account's competitors."}}])
    assert "not one of this account" in refused["el"]["#msg"]["html"] and len([c for c in refused["calls"] if "/compete/report" in c]) == 1


def test_a_page_typed_in_the_box_is_saved_as_typed_for_that_competitor(report):
    typed = [dict(c) for c in report["competitors"]]
    typed[1]["facebook"] = "https://www.facebook.com/CheapCo"
    out = opened(report, [{"change": ["#caList", {"[data-fb]": {"dataset": {"fb": "1"}, "value": "cheapco"}}]}], more=[change_ok(typed)])
    [post] = out["posts"]
    assert post["body"] == {"account": G, "op": "update", "names": NAMES, "competitor": {"name": "Cheap Co", "domain": "cheap.example", "googleAdvertiser": "", "logo": "", "industry": "", "facebook": "cheapco", "fbSource": "manual"}}
    assert len([c for c in out["calls"] if "/compete/report" in c]) == 2
    bad = opened(report, [{"change": ["#caList", {"[data-fb]": {"dataset": {"fb": "1"}, "value": "https://example.com/x"}}]}], more=[{"match": "/compete/competitors", "method": "POST", "status": 400, "body": {"error": "https://example.com/x is not a Facebook page address."}}])
    assert "not a Facebook page address" in bad["el"]["#msg"]["html"]


def test_the_find_button_looks_for_that_competitors_page(report):
    cheap = dict(report["competitors"][1], facebook="https://www.facebook.com/CheapCo", fbSource="search")
    comps = [report["competitors"][0], cheap, report["competitors"][2]]
    out = opened(report, [{"fire": ["#caList", {"[data-find]": {"dataset": {"find": "1"}}}]}], more=[{"match": "/compete/find-facebook", "method": "POST", "body": {"kept": True, "facebook": cheap["facebook"], "competitors": comps, "region": "US"}}])
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/compete/find-facebook", "body": {"account": G, "name": "Cheap Co", "names": NAMES, "force": False}}]


def test_the_country_is_saved_for_the_account_when_it_is_changed(report):
    out = opened(report, [{"set": ["#fRegion", " gb "]}, {"change": ["#fRegion", {}]}], more=[change_ok(report["competitors"], "GB")])
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/compete/competitors", "body": {"account": G, "op": "region", "region": "GB", "names": NAMES}}] and len([c for c in out["calls"] if "/compete/report" in c]) == 2
    bad = opened(report, [{"set": ["#fRegion", "USA"]}, {"change": ["#fRegion", {}]}], more=[{"match": "/compete/competitors", "method": "POST", "status": 400, "body": {"error": "The country is two letters, like US."}}])
    assert "two letters" in bad["el"]["#msg"]["html"]


def test_refresh_starts_the_read_polls_the_job_and_reads_the_report_when_it_is_done(report):
    running = {"status": "running", "steps": [{"name": "Tandem", "platform": "meta", "status": "reading", "ads": 0, "error": ""}], "total": 1}
    done = dict(running, status="done", steps=[{"name": "Tandem", "platform": "meta", "status": "done", "ads": 30, "error": ""}], finishedAt="2026-10-27T12:01:00+00:00", by="ana@position2.com")
    more = [{"match": "/compete/refresh", "method": "POST", "status": 202, "body": {"started": True}}, {"match": "/compete/job", "sequence": [{"job": running}, {"job": done}]}]
    out = opened(report, [{"click": "#refreshBtn"}], more=more)
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/compete/refresh", "body": {"account": G}}]
    assert [c for c in out["calls"] if "/compete/job" in c] == ["/p2/paid-marketing/api/compete/job?account=1234567890"] * 2
    assert len([c for c in out["calls"] if "/compete/report" in c]) == 2


def test_a_refresh_that_is_refused_says_why(report):
    out = opened(report, [{"click": "#refreshBtn"}], more=[{"match": "/compete/refresh", "method": "POST", "status": 400, "body": {"error": "APIFY_API_TOKEN is not set on this server, so competitor ads cannot be read."}}])
    assert "APIFY_API_TOKEN is not set" in out["el"]["#msg"]["html"] and not any("/compete/job" in c for c in out["calls"])
    busy = opened(report, [{"click": "#refreshBtn"}], more=[{"match": "/compete/refresh", "method": "POST", "status": 409, "body": {"error": "A refresh is already running for this account."}}])
    assert "already running" in busy["el"]["#msg"]["html"]
    down = opened(report, [{"click": "#refreshBtn"}], reject="/compete/refresh")
    assert "Nothing was read." in down["el"]["#msg"]["html"]


def test_a_refresh_already_running_when_the_page_opens_is_followed_until_it_is_done(report):
    running = dict(report, job={"status": "running", "steps": [{"name": "Tandem", "platform": "meta", "status": "reading", "ads": 0, "error": ""}], "total": 1})
    done_job = {"status": "done", "steps": [{"name": "Tandem", "platform": "meta", "status": "done", "ads": 3, "error": ""}], "finishedAt": "2026-10-27T12:02:00+00:00", "by": "ana@position2.com"}
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/compete/report", "sequence": [running, dict(report, job=done_job)]},
                              {"match": "/compete/job", "sequence": [{"job": running["job"]}, {"job": done_job}]}], "steps": [{"pick": G}]})
    assert [c for c in out["calls"] if "/compete/job" in c] == ["/p2/paid-marketing/api/compete/job?account=1234567890"] * 2 and len([c for c in out["calls"] if "/compete/report" in c]) == 2
    assert "Last refresh 2026-10-27 by ana@position2.com: 1 read done." in out["el"]["#jobBox"]["html"] and out["el"]["#refreshBtn"]["disabled"] is False


def test_the_page_stops_following_a_job_that_never_finishes(report):
    running = dict(report, job={"status": "running", "steps": [], "total": 1})
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/compete/report", "body": running}, {"match": "/compete/job", "body": {"job": running["job"]}}], "steps": [{"pick": G}]})
    assert len([c for c in out["calls"] if "/compete/job" in c]) == 600 and len([c for c in out["calls"] if "/compete/report" in c]) == 1 and out["el"]["#refreshBtn"]["disabled"] is True


def test_a_report_that_cannot_be_built_is_said_in_words(report):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/compete/report", "status": 400, "body": {"error": "Acme is a group of accounts. Pick one account: each client has its own competitors."}}], "steps": [{"pick": G}]})
    assert "Pick one account" in out["el"]["#msg"]["html"] and out["el"]["#empty"]["hidden"] is False
    down = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}], "reject": "/compete/report", "steps": [{"pick": G}]})
    assert "The server could not be reached. Try again." in down["el"]["#msg"]["html"]


def test_the_csv_is_downloaded_and_a_cell_that_starts_like_a_formula_is_defused(report):
    report["ideas"][0]["title"] = "=HYPERLINK(\"x\")"
    out = opened(report, [{"click": "#exportBtn"}])
    assert out["blob"].startswith("﻿Competitor Analysis") and "'=HYPERLINK" in out["blob"] and "Section,Platform,Competitor" in out["blob"] and out["downloads"][0]["name"].startswith("competitor-analysis-")


def stats_of(name, newest, age=0, **kw):
    return dict({"name": name, "error": "", "skipped": "", "count": len(newest), "active": len(newest), "new": len(newest), "medianDays": 5, "ageDays": age, "formats": {}, "ctas": {}, "landing": [], "longest": [], "newest": newest}, **kw)


def new_ad(who, since, n=0):
    return {"id": "%s%d" % (who, n), "who": who, "headline": "H%d" % n, "body": "", "format": "image", "cta": "", "days": 2, "firstShown": since, "url": "", "copies": 1}


def test_a_competitor_read_today_says_so_and_the_newest_ads_are_the_latest_first_and_six_at_most():
    rep = {"analysis": {"meta": {"competitors": [stats_of("A", [new_ad("A", "2026-10-20", 1), new_ad("A", "2026-10-25", 2)], age=0), stats_of("B", [new_ad("B", "2026-10-22", i) for i in range(3, 9)], age=3)]}}}
    rows = run("X.competitorRows(R, 'meta')", R=rep)
    assert [r["note"] for r in rows] == ["read today", "read 3 days ago"]
    news = run("X.newestRows(R, 'meta')", R=rep)
    assert len(news) == 6 and news[0]["since"] == "2026-10-25" and [n["since"] for n in news] == sorted([n["since"] for n in news], reverse=True) and news[1]["since"] == "2026-10-22"
    assert run("X.ageText(0)") == "read today" and run("X.ageText(1)") == "read 1 day ago" and run("X.ageText(null)") == "not read yet"


def test_the_line_that_says_where_it_stands_is_exact_for_each_state(empty_report):
    assert run("X.standing(R)", R=empty_report) == "No competitors are saved for this account yet. Type a company name below and pick the ones that are competitors, then press Refresh competitor ads."
    assert run("X.standing(R)", R={"status": "no_competitor_data"}) == "The competitors are saved, but none of their ads has been read yet. Press Refresh competitor ads."


# ── the ads themselves ──────────────────────────────────────────────────────

def gallery_res(**kw):
    """The page of ads the server builds for a Meta account with Tandem read: one ad with words and a picture, one that is only a picture."""
    s = MemStore()
    cs.save_account(s, MA, "Acme", {"competitors": "Tandem | tandemdiabetes.com | TandemDiabetes"}, "ana@position2.com", NOW)
    keep(s, MA, "Tandem", "meta", [mad(1, "Tandem Diabetes Care", "Pump delivered", "2026-09-23")], pictures=[picture_only(2, "Tandem Diabetes Care", "2026-10-20")])
    return json.loads(json.dumps(pc.ads_for("", MA, mon=s, **kw)))


def test_the_query_of_the_gallery_carries_the_account_encoded_and_only_the_choices_made():
    assert run("X.adsQuery('1234567890+act_1', {}, 0)") == "?account=1234567890%2Bact_1"
    assert run("X.adsQuery('a', {platform: 'meta', competitor: 'Tandem Diabetes', format: 'video', sort: 'newest', q: ' t:slim  '}, 24, 24)") == "?account=a&platform=meta&competitor=Tandem%20Diabetes&format=video&sort=newest&q=t%3Aslim&offset=24&limit=24"
    assert run("X.adsQuery('a', {platform: '', q: '   '}, 0)") == "?account=a"


def test_a_card_has_the_words_the_picture_the_button_the_link_and_how_long_it_has_run():
    ad = gallery_res()["ads"]
    card = run("X.adCard(A)", A=[a for a in ad if a["id"] == "meta:1"][0])
    assert card["title"] == "Pump delivered" and card["competitor"] == "Tandem" and card["advertiser"] == "Tandem Diabetes Care" and card["platformLabel"] == "Meta Ads" and card["format"] == "Image"
    assert card["cta"] == "See details" and card["link"].startswith("https://www.tandemdiabetes.com/") and card["landing"] == "tandemdiabetes.com/landing-pages/tandem-pump-rx" and card["libraryUrl"].endswith("ads/library/?id=1")
    assert card["status"] == "Active" and card["running"] == "34 days running, since 2026-09-23" and card["pictureOnly"] is False and card["extra"] == 0
    assert card["media"][0]["kind"] == "image" and card["media"][0]["url"].startswith("https://scontent") and card["lines"][0].startswith("With a sleek touchscreen") and "Important Safety Information" in card["text"]


def test_a_card_for_an_ad_that_is_only_a_picture_says_so_and_a_dynamic_ad_says_how_many_more_variants_it_has():
    ad = gallery_res()["ads"]
    pic = run("X.adCard(A)", A=[a for a in ad if a["id"] == "meta:2"][0])
    assert pic["pictureOnly"] is True and pic["title"] == "" and pic["lines"] == [] and pic["media"][0]["kind"] == "image"
    many = run("X.adCard(A)", A={"id": "x", "platform": "meta", "media": [{"kind": "image", "url": "https://a.example/1.jpg"}] * 6, "mediaCount": 14, "headlines": ["H"], "bodies": [], "format": "dynamic", "active": False, "days": 1, "copies": 3})
    assert many["extra"] == 8 and many["format"] == "Dynamic" and many["status"] == "Stopped" and many["running"] == "1 day running" and many["copies"] == "Shown 3 times"


def test_the_button_is_the_text_the_ad_gives_or_the_kind_of_button_in_words():
    assert run("X.adCard({headlines: [], bodies: [], media: [], cta: '', ctaType: 'LEARN_MORE'}).cta") == "Learn more"
    assert run("X.adCard({headlines: [], bodies: [], media: [], cta: 'Shop now', ctaType: 'LEARN_MORE'}).cta") == "Shop now"
    assert run("X.adCard({headlines: [], bodies: [], media: []}).cta") == ""


@pytest.mark.parametrize("bad", ["javascript:alert(1)", "http://insecure.example/a.jpg", "data:image/png;base64,AAAA", "//x.example/a.jpg", "https://has space.example/a b.jpg", "", None, 5])
def test_a_picture_or_a_link_that_is_not_safe_is_not_drawn_even_if_the_server_let_it_through(bad):
    card = run("X.adCard(A)", A={"headlines": ["H"], "bodies": [], "url": bad, "adUrl": bad, "media": [{"kind": "image", "url": bad, "poster": bad, "link": bad}, {"kind": "video", "url": "https://v.example/a.mp4", "poster": bad, "link": bad}]})
    plain = bad if str(bad).startswith("http://insecure") else ""                                                                        # a plain http landing page is still a page
    assert [m["url"] for m in card["media"]] == ["https://v.example/a.mp4"] and card["media"][0]["poster"] == "" and card["media"][0]["link"] == plain and card["libraryUrl"] == "" and card["link"] == plain


def test_the_choices_count_what_each_competitor_and_format_has_and_a_platform_is_offered_only_when_there_are_two():
    res = {"facets": {"platforms": {"meta": 3}, "competitors": {"Tandem": 2, "Insulet": 5}, "formats": {"image": 6, "video": 1}}}
    o = run("X.filterOptions(R)", R=res)
    assert o["competitors"] == [["", "All competitors (7)"], ["Insulet", "Insulet (5)"], ["Tandem", "Tandem (2)"]] and o["formats"] == [["", "All formats"], ["image", "Image (6)"], ["video", "Video (1)"]] and o["platforms"] is None
    two = run("X.filterOptions(R)", R={"facets": {"platforms": {"google": 4, "meta": 3}, "competitors": {}, "formats": {}}})
    assert two["platforms"] == [["", "Google and Meta"], ["google", "Google Ads (4)"], ["meta", "Meta Ads (3)"]]


def test_the_gallery_says_what_is_missing_why_it_is_empty_and_how_much_is_shown(own):
    res = gallery_res()
    assert run("X.pageText(R)", R=res) == "Showing 2 of 2 ads" and run("X.pageText({total: 0, ads: [], offset: 0})") == "" and run("X.pageText({total: 50, ads: new Array(24), offset: 24})") == "Showing 48 of 50 ads"
    assert run("X.creativeNote(R)", R=res) == "" and run("X.creativeNote({creativeMissing: 1})").startswith("1 ad was read before pictures and links were kept, so it shows words only. Press Refresh")
    assert run("X.creativeNote({creativeMissing: 3})").startswith("3 ads were read before pictures and links were kept, so they show words only.")
    assert run("X.emptyText({reads: [], facets: {platforms: {}}})") == "No competitors are saved for this account yet."
    assert run("X.emptyText({reads: [{}], facets: {platforms: {}}})") == "No competitor ads have been read yet. Press Refresh competitor ads to read them."
    assert run("X.emptyText({reads: [{}], facets: {platforms: {meta: 4}}})") == "No ad matches these filters."


# ── the gallery on the page ─────────────────────────────────────────────────

def with_ads(rep, ads_res, steps=None, **kw):
    return opened(rep, steps, more=[{"match": "/compete/ads", "body": ads_res}], **kw)


def ads_calls(out):
    return [c for c in out["calls"] if "/compete/ads" in c]


def test_the_gallery_is_drawn_from_the_ads_the_server_lists_with_pictures_words_buttons_and_links(report):
    out = with_ads(report, gallery_res())
    assert ads_calls(out) == ["/p2/paid-marketing/api/compete/ads?account=1234567890&sort=longest&limit=24"] and out["el"]["#galCard"]["hidden"] is False
    grid = out["el"]["#galGrid"]["html"]
    assert grid.count('<article class="ca-ad"') == 2 and "Pump delivered" in grid and 'src="https://scontent-hou1-1.xx.fbcdn.net/' in grid and 'referrerpolicy="no-referrer"' in grid and "See details" in grid
    assert 'href="https://www.tandemdiabetes.com/landing-pages/tandem-pump-rx" target="_blank" rel="noopener noreferrer"' in grid and "https://www.facebook.com/ads/library/?id=1" in grid
    assert grid.count('target="_blank"') >= 3 and grid.count('target="_blank"') == grid.count('target="_blank" rel="noopener noreferrer"')            # every link that opens a new tab keeps the page it came from out of reach
    assert "Picture or video only" in grid and "34 days running, since 2026-09-23" in grid and "Active" in grid and "Important Safety Information" in grid
    assert out["el"]["#galNote"]["text"] == "Showing 2 of 2 ads" and out["el"]["#galMore"]["hidden"] is True and out["el"]["#galMsg"]["html"] == ""
    tools = out["el"]["#galTools"]["html"]
    assert 'data-f="competitor"' in tools and "All competitors (2)" in tools and 'data-f="format"' in tools and 'data-f="sort"' in tools and 'data-f="q"' in tools and 'data-f="platform"' not in tools


def test_what_an_ad_says_is_never_drawn_as_markup(report):
    res = gallery_res()
    res["ads"][0].update(headlines=["<img src=x onerror=alert(1)>"], bodies=['"><script>alert(2)</script>'], text="a <b>bold</b> claim", who="<i>Evil</i>", competitor="<u>C</u>", cta="<b>Buy</b>")
    res["ads"][0]["media"][0].update(title="<svg onload=alert(3)>")
    grid = with_ads(report, res)["el"]["#galGrid"]["html"]
    for raw in ("<img src=x", "<script>", "<b>bold</b>", "<i>Evil</i>", "<u>C</u>", "<b>Buy</b>", "<svg onload"):
        assert raw not in grid, raw
    assert "&lt;img src=x onerror=alert(1)&gt;" in grid and "&lt;i&gt;Evil&lt;/i&gt;" in grid


def test_show_more_asks_for_the_next_page_and_adds_its_ads_below(report):
    first = dict(gallery_res(), total=3)
    nxt = dict(gallery_res(), total=3, offset=2, ads=[dict(gallery_res()["ads"][0], id="meta:9", headlines=["Third ad"])])
    out = opened(report, [{"click": "#galMore"}], more=[{"match": "/compete/ads", "sequence": [first, nxt]}])
    assert ads_calls(out)[1].endswith("&sort=longest&offset=2&limit=24")
    grid = out["el"]["#galGrid"]["html"]
    assert grid.count('<article class="ca-ad"') == 3 and "Third ad" in grid and out["el"]["#galNote"]["text"] == "Showing 3 of 3 ads" and out["el"]["#galMore"]["hidden"] is True
    assert with_ads(report, first)["el"]["#galMore"]["hidden"] is False


def test_a_filter_asks_again_from_the_first_page_with_the_choice_in_the_request(report):
    res = gallery_res()
    pick = {"[data-f]": {"dataset": {"f": "competitor"}, "value": "Tandem"}}
    out = with_ads(report, res, [{"change": ["#galTools", pick]}])
    assert ads_calls(out)[1] == "/p2/paid-marketing/api/compete/ads?account=1234567890&competitor=Tandem&sort=longest&limit=24"
    out = with_ads(report, res, [{"change": ["#galTools", {"[data-f]": {"dataset": {"f": "q"}, "value": "t:slim"}}]}, {"change": ["#galTools", {"[data-f]": {"dataset": {"f": "sort"}, "value": "newest"}}]}])
    assert ads_calls(out)[-1] == "/p2/paid-marketing/api/compete/ads?account=1234567890&sort=newest&q=t%3Aslim&limit=24"


def test_a_thumbnail_shows_that_picture_of_a_dynamic_ad_and_says_how_many_more_there_are(report):
    res = gallery_res()
    res["ads"][0].update(media=[{"kind": "image", "url": "https://a.example/%d.jpg" % i, "title": "V%d" % i, "cta": "Go %d" % i} for i in range(1, 4)], mediaCount=9)
    grid = with_ads(report, res)["el"]["#galGrid"]["html"]
    assert grid.count("ca-ad-thumb") >= 3 and "+6 more variants" in grid and 'src="https://a.example/1.jpg"' in grid
    out = with_ads(report, res, [{"fire": ["#galGrid", {"[data-m]": {"dataset": {"m": "2"}}, "[data-i]": {"dataset": {"i": "0"}}}]}])
    grid = out["el"]["#galGrid"]["html"]
    assert 'class="ca-ad-m" src="https://a.example/3.jpg"' in grid and 'class="ca-ad-m" src="https://a.example/1.jpg"' not in grid and "V3" in grid


def test_a_video_is_a_player_that_loads_nothing_until_played_and_an_ad_with_no_picture_says_so(report):
    res = gallery_res()
    res["ads"][0]["media"] = [{"kind": "video", "url": "https://video.example/a.mp4", "poster": "https://video.example/p.jpg"}]
    res["ads"][1]["media"] = []
    grid = with_ads(report, res)["el"]["#galGrid"]["html"]
    assert '<video class="ca-ad-m" controls preload="none" playsinline poster="https://video.example/p.jpg" src="https://video.example/a.mp4"></video>' in grid and "No picture or video was kept for this ad." in grid


def test_the_gallery_says_when_ads_were_read_before_pictures_were_kept_and_when_it_could_not_be_listed(report):
    res = dict(gallery_res(), creativeMissing=2)
    assert "2 ads were read before pictures and links were kept" in with_ads(report, res)["el"]["#galMsg"]["html"]
    out = opened(report, more=[{"match": "/compete/ads", "status": 400, "body": {"error": "Pick one Google Ads account."}}])
    assert "Pick one Google Ads account." in out["el"]["#galMsg"]["html"] and out["el"]["#galGrid"]["html"] == ""
    out = opened(report, more=[{"match": "/compete/ads", "body": {"ads": [], "total": 0, "offset": 0, "limit": 24, "reads": [], "facets": {"platforms": {}, "competitors": {}, "formats": {}}, "creativeMissing": 0}}])
    assert "No competitors are saved for this account yet." in out["el"]["#galGrid"]["html"] and out["el"]["#galMore"]["hidden"] is True


def test_an_account_with_no_competitor_has_no_gallery(empty_report):
    out = opened(empty_report)
    assert out["el"]["#galCard"]["hidden"] is True and ads_calls(out) == []


def test_a_google_card_shows_the_picture_and_the_words_of_the_variant_shown_not_the_same_words_again(report):
    res = gallery_res()
    g = dict(res["ads"][0], platform="google", id="google:1", headlines=["Head one", "Head two"], bodies=["Description read from the picture"], text="", url="https://tandemdiabetes.com", landing="tandemdiabetes.com",
             adUrl="https://adstransparency.google.com/advertiser/AR1/creative/CR1", cta="", ctaType="", format="text",
             media=[{"kind": "image", "url": "https://tpc.googlesyndication.com/archive/simgad/%d" % i, "title": "Head %d" % i, "body": "Words %d" % i, "link": "https://tandemdiabetes.com"} for i in (1, 2)], mediaCount=2)
    res["ads"] = [g]
    grid = with_ads(report, res)["el"]["#galGrid"]["html"]
    assert "Head one" in grid and "<b>Head 1</b> Words 1" in grid and "Description read from the picture" not in grid and "Head two" not in grid and "Open in the Ads Transparency Center" in grid
    grid = with_ads(report, res, [{"fire": ["#galGrid", {"[data-m]": {"dataset": {"m": "1"}}, "[data-i]": {"dataset": {"i": "0"}}}]}])["el"]["#galGrid"]["html"]
    assert "<b>Head 2</b> Words 2" in grid and 'class="ca-ad-m" src="https://tpc.googlesyndication.com/archive/simgad/2"' in grid
    one = dict(g, media=g["media"][:1], mediaCount=1)
    assert "<b>Head 1</b> Words 1" in with_ads(report, dict(res, ads=[one]))["el"]["#galGrid"]["html"]                    # one picture still shows its words


YT = "https://i.ytimg.com/vi/T84TitSO-qg/hqdefault.jpg"


def test_a_picture_that_is_a_youtube_thumbnail_gets_the_link_to_the_video_and_nothing_else_does():
    item = lambda url: run("X.adCard(A).media[0]", A={"headlines": ["H"], "bodies": [], "media": [{"kind": "image", "url": url}]})
    assert item(YT)["watch"] == "https://www.youtube.com/watch?v=T84TitSO-qg" and item("https://i.ytimg.com/vi/T84TitSO-qg/maxresdefault.jpg")["watch"] == "https://www.youtube.com/watch?v=T84TitSO-qg"
    for other in ("https://tpc.googlesyndication.com/archive/simgad/1", "https://i.ytimg.com/vi/short/hqdefault.jpg", "https://evil.example/i.ytimg.com/vi/T84TitSO-qg/hqdefault.jpg", "https://i.ytimg.com.evil.example/vi/T84TitSO-qg/hqdefault.jpg",
                  "https://i.ytimg.com/vi/T84TitSO-qg%22%3E/hqdefault.jpg", "https://evil.example/?next=https://i.ytimg.com/vi/T84TitSO-qg/hqdefault.jpg", "https://i.ytimg.com/vi/T84TitSO/hqdefault.jpg",
                  "https://i.ytimg.com/vi/T84TitSO-qgXY/hqdefault.jpg"):
        assert item(other)["watch"] == "", other


def test_a_video_creative_is_drawn_as_its_thumbnail_with_a_link_that_plays_it_on_youtube(report):
    res = gallery_res()
    res["ads"][0].update(platform="google", format="video", media=[{"kind": "image", "url": YT, "title": "Automatically Adjusts Insulin", "body": "", "link": "https://www.omnipod.com/simplify/omnipod-5"}], mediaCount=1)
    grid = with_ads(report, res)["el"]["#galGrid"]["html"]
    assert '<a class="ca-ad-watch" href="https://www.youtube.com/watch?v=T84TitSO-qg" target="_blank" rel="noopener noreferrer">' in grid and 'class="ca-ad-m" src="%s"' % YT in grid and "Watch on YouTube" in grid
    assert grid.count('target="_blank"') == grid.count('target="_blank" rel="noopener noreferrer"')


# ── the kinds of ads and the ideas Claude writes ────────────────────────────

MIX = {"meta": {"platform": "meta", "ownError": "", "ownUnits": 4, "theirAds": 10, "automatic": 2, "pmaxNoVideo": 0,
                "kinds": {"format": {"kind": "format", "judged": True, "why": "", "rows": [{"value": "video", "label": "Video", "their": 60.0, "own": 0.0, "ads": 6, "who": 2, "ownN": 0},
                                                                                        {"value": "image", "label": "Single <b>image</b>", "their": 40.0, "own": 100.0, "ads": 4, "who": 2, "ownN": 4}],
                                     "gaps": [{"value": "video", "level": "high"}], "edges": [{"value": "image"}]},
                          "cta": {"kind": "cta", "judged": False, "why": "Only 2 ads of theirs say it, too few to compare (at least 8 are needed).", "rows": [], "gaps": [], "edges": []}},
                "durability": [{"format": "video", "label": "Video", "ads": 6, "median": 120.4, "longest": 300}], "fresh": {"their": 60.0, "theirNew": 6, "own": 0.0, "ownNew": 0, "gap": "high"}},
       "google": {"platform": "google", "ownError": "", "automatic": 0, "pmaxNoVideo": 1, "kinds": {"format": {"judged": True, "why": "", "rows": [], "gaps": [], "edges": []}},
                  "durability": [], "fresh": {"their": 10.0, "theirNew": 1, "own": None, "ownNew": None, "gap": None}}}
BRIEF = {"status": "ready", "at": "2026-10-27T12:00:00+00:00", "model": "claude-opus-5-5", "images": 3, "shown": 8,
         "ideas": [{"title": "A <script>video</script>", "platform": "meta", "format": "Video", "placement": "Reels", "hook": "A parent at breakfast", "firstLine": "[your offer] today", "why": "Their videos run longest.",
                    "basedOn": ["meta:1"], "ads": [{"id": "meta:1", "who": "Tandem", "format": "video", "days": 120, "adUrl": "https://www.facebook.com/ads/library/?id=1", "headline": "Pump delivered"},
                                                   {"id": "meta:2", "who": "Evil", "format": "image", "days": 3, "adUrl": "javascript:alert(1)", "headline": ""}]}]}


def test_the_kinds_of_ads_are_rows_with_the_gaps_marked_the_freshness_and_the_notes_in_words():
    m = run("X.mixRows(R, 'meta')", R={"mix": MIX})
    assert [k["kind"] for k in m["kinds"]] == ["format", "cta"] and m["kinds"][0]["title"] == "Formats" and m["kinds"][1]["why"].startswith("Only 2 ads")
    assert m["kinds"][0]["rows"][0] == {"label": "Video", "their": 60.0, "own": 0.0, "gap": "high", "edge": False, "text": "6 of their ads from 2 competitors, 0 of ours"}
    assert m["kinds"][0]["rows"][1]["edge"] is True and m["durability"] == [{"label": "Video", "ads": 6, "median": 120, "longest": 300}]
    assert m["fresh"] == "60% of their ads started in the last 30 days (6 ads), 0% of ours." and m["notes"] == ["2 of our ads use automatic placements, so it can show on Facebook, Instagram, Messenger and Audience Network."]
    g = run("X.mixRows(R, 'google')", R={"mix": MIX})
    assert g["fresh"].endswith("Google gives no start day for our ads, so ours are not compared.") and "1 Performance Max asset group has no video of our own" in g["notes"][1]
    assert run("X.mixRows({}, 'meta')") is None


def test_claudes_ideas_are_cards_that_link_only_safe_library_pages_and_the_note_says_they_are_a_models():
    c = run("X.briefCards(B)", B=BRIEF)
    assert c[0]["platform"] == "Meta Ads" and c[0]["ads"][0] == {"label": "Tandem · Video · 120 days · “Pump delivered”", "url": "https://www.facebook.com/ads/library/?id=1"} and c[0]["ads"][1]["url"] == ""
    assert run("X.briefCards(null)") == [] and "only when you press the button" in run("X.briefNote(null)")
    assert run("X.briefNote(B)", B=BRIEF) == "Written by Claude on 2026-10-27 from 8 of their ads and 3 pictures. These are a model's ideas: check each one before you brief it."
    assert run("X.briefNote(B)", B=dict(BRIEF, status="old")).startswith("The competitors or our ads were read again since these were written.")


def test_the_kinds_of_ads_panel_is_drawn_with_bars_and_escaped_labels(report):
    out = opened(dict(report, mix=MIX))
    el = out["el"]
    assert el["#mixCard"]["hidden"] is False and "<h5>Formats</h5>" in el["#mix"]["html"] and 'class="ca-mix gap"' in el["#mix"]["html"] and ">Gap</span>" in el["#mix"]["html"]
    assert "Single &lt;b&gt;image&lt;/b&gt;" in el["#mix"]["html"] and ">Ours</span>" in el["#mix"]["html"] and "Which of their formats run longest" in el["#mix"]["html"] and "Only 2 ads of theirs" in el["#mix"]["html"]


def test_saved_ideas_are_drawn_and_the_button_writes_new_ones(report):
    out = opened(report, [{"click": "#briefBtn"}], more=[{"match": "/compete/brief", "body": BRIEF}, {"match": "/compete/brief", "method": "POST", "body": dict(BRIEF, ideas=[dict(BRIEF["ideas"][0], title="Fresh idea")])}])
    el = out["el"]
    assert "/p2/paid-marketing/api/compete/brief?account=1234567890" in out["calls"] and out["posts"] == [{"url": "/p2/paid-marketing/api/compete/brief", "body": {"account": G}}]
    assert "Fresh idea" in el["#brief"]["html"] and "<script>" not in el["#brief"]["html"] and "javascript:" not in el["#brief"]["html"] and 'rel="noopener noreferrer"' in el["#brief"]["html"]
    assert el["#briefBtn"]["text"] == "Write them again" and el["#briefMsg"]["html"] == "" and "Written by Claude on 2026-10-27" in el["#briefNote"]["text"]


def test_a_failed_ask_for_ideas_is_said_and_no_saved_ideas_means_the_button_explains_the_cost(report):
    out = opened(report, [{"click": "#briefBtn"}], more=[{"match": "/compete/brief", "body": {"status": "none"}}, {"match": "/compete/brief", "method": "POST", "body": {"status": "failed", "error": "Claude could not be asked (overloaded)."}}])
    assert "Claude could not be asked (overloaded)." in out["el"]["#briefMsg"]["html"] and out["el"]["#brief"]["html"] == "" and "costs a few cents" in out["el"]["#briefNote"]["text"]
    busy = opened(report, [{"click": "#briefBtn"}], more=[{"match": "/compete/brief", "method": "POST", "status": 400, "body": {"error": "Claude is already writing ideas for this account."}}])
    assert "already writing" in busy["el"]["#briefMsg"]["html"]
