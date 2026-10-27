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
from test_paid_compete import own_raw_ads, raw_of  # noqa: E402
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
                .replace("hidden: els[k].hidden, value:", "hidden: els[k].hidden, disabled: els[k].disabled, title: els[k].title || \"\", value:"))
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


def test_the_cost_of_a_refresh_is_said_before_it_is_paid():
    assert run("X.estimateText(E, 7)", E={"reads": 4, "ads": 120, "usd": 0.45}) == "A refresh reads 4 competitor pages, up to 120 ads, about $0.45 at most. Reads newer than 7 days are not paid for again."
    assert run("X.estimateText(E, 1)", E={"reads": 1, "ads": 30, "usd": 0.05}) == "A refresh reads 1 competitor page, up to 30 ads, about $0.05 at most. Reads newer than 1 day are not paid for again."
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


def test_what_the_page_sends_to_save_and_the_line_that_says_where_it_stands(report, empty_report):
    assert run("X.saveBody('1234567890', 'A | a.com', ' de ', N)", N={G: "Acme"}) == {"account": G, "competitors": "A | a.com", "region": "DE", "names": {G: "Acme"}}
    assert run("X.saveBody('1', 'x', '', null)") == {"account": "1", "competitors": "x", "region": "US", "names": {}}
    assert run("X.standing(R)", R=empty_report).startswith("No competitors are saved for this account yet.") and run("X.standing({status: 'no_competitor_data'})").startswith("The competitors are saved, but none of their ads")
    assert run("X.standing(R)", R=report) == report["headline"]


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
    assert el["#fComp"]["value"] == report["competitorsText"] and el["#fRegion"]["value"] == "US" and el["#compNote"]["text"] == "3 competitors" and "A refresh reads 4 competitor pages" in el["#estimate"]["text"]
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


def test_saving_posts_the_box_the_country_and_the_account_names_and_reads_the_report_again(report):
    steps = [{"set": ["#fComp", "A | a.com | AFacebook"]}, {"set": ["#fRegion", "gb"]}, {"click": "#saveBtn"}]
    out = opened(report, steps, more=[{"match": "/compete/settings", "method": "POST", "body": {"saved": {}}}])
    [post] = out["posts"]
    assert post["url"] == "/p2/paid-marketing/api/compete/settings" and post["body"] == {"account": G, "competitors": "A | a.com | AFacebook", "region": "GB", "names": {G: "Acme <b>Dental</b>", MA: "Acme <b>Dental</b>"}}
    assert len([c for c in out["calls"] if "/compete/report" in c]) == 2 and "Saved. Press Refresh competitor ads" in out["el"]["#msg"]["html"] and "pm-note" in out["el"]["#msg"]["html"]


def test_a_refused_save_says_why_and_an_unreachable_server_says_nothing_was_saved(report):
    bad = opened(report, [{"set": ["#fComp", "A"]}, {"click": "#saveBtn"}], more=[{"match": "/compete/settings", "method": "POST", "status": 400, "body": {"error": "A needs a website, a Facebook page or an advertiser id, or there is nowhere to look."}}])
    assert "nowhere to look" in bad["el"]["#msg"]["html"] and len([c for c in bad["calls"] if "/compete/report" in c]) == 1
    down = opened(report, [{"click": "#saveBtn"}], reject="/compete/settings")
    assert "Nothing was saved." in down["el"]["#msg"]["html"] and down["el"]["#saveOut"]["text"] == ""


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
    assert len([c for c in out["calls"] if "/compete/job" in c]) == 90 and len([c for c in out["calls"] if "/compete/report" in c]) == 1 and out["el"]["#refreshBtn"]["disabled"] is True


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
    assert run("X.standing(R)", R=empty_report) == "No competitors are saved for this account yet. Write them below, then press Refresh competitor ads."
    assert run("X.standing(R)", R={"status": "no_competitor_data"}) == "The competitors are saved, but none of their ads has been read yet. Press Refresh competitor ads."
