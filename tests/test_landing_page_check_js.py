"""Landing Page & Relevancy Check: static/js/lp_data.js run in Node, and the page script checked against its template.

The server reads, fetches, checks and scores (tests/test_paid_lp*.py). The page only draws, filters and words things, so which pages a view shows, in what order, how money and
rates read and what a label means come from lp_data.js: the file is executed here, not read. The page script is syntax-checked and every element it reaches for must exist in the template.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "static", "js", "lp_data.js")
AUDIT = os.path.join(ROOT, "static", "js", "au_data.js")
PAGE = os.path.join(ROOT, "static", "js", "landing_page_check.js")
TEMPLATE = os.path.join(ROOT, "templates", "landing_page_check.html")
CSS = os.path.join(ROOT, "static", "css", "landing_page_check.css")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def run(expr, **data):
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    code = "const L=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(DATA), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def pg(pid="a.test/x", spend=100.0, score=90, grade="A", rated=True, critical=0, high=0, fail=0, warn=0, clicks=100, conv=4.0, title="", groups=(), h1=()):
    return {"id": pid, "url": "https://" + pid, "spend": spend, "clicks": clicks, "conversions": conv, "score": score, "grade": grade, "rated": rated,
            "summary": {"critical": critical, "high": high, "tally": {"fail": fail, "warn": warn, "pass": 10, "na": 0, "unknown": 0}},
            "seen": {"title": title, "h1": list(h1)}, "adGroups": [{"name": n, "campaign": "C", "keywords": [{"text": k} for k in ks]} for n, ks in groups]}


PAGES = [pg("a.test/one", 500, 95, "A"), pg("a.test/two", 300, 55, "D", high=1, fail=3), pg("a.test/three", 200, None, None, rated=False), pg("a.test/four", 100, 72, "C", warn=4),
         pg("a.test/five", 50, 80, "B", critical=1, fail=1)]


# ── A page's state, the views, the counts ───────────────────────────────────

def test_a_page_is_unverified_when_it_has_no_score_attention_when_it_is_poor_or_has_a_serious_failure_and_healthy_otherwise():
    assert [run("L.pageState(p)", p=p) for p in PAGES] == ["healthy", "attention", "unverified", "healthy", "attention"]
    assert run("L.pageState(p)", p=pg(grade="D", score=50)) == "attention" and run("L.pageState(p)", p=pg(grade="F", score=0)) == "attention"
    assert run("L.pageState(p)", p=pg(high=1)) == "attention" and run("L.pageState(p)", p=pg(grade="C", score=70)) == "healthy"
    assert run("L.pageState(p)", p=pg(rated=False, critical=3)) == "unverified"                       # nothing could be judged well enough to call it either way


def test_the_views_are_counted_and_the_first_one_opened_is_what_needs_attention_else_everything():
    assert run("L.viewCounts(P)", P=PAGES) == {"attention": 2, "unverified": 1, "healthy": 2, "all": 5}
    assert run("L.defaultView(P)", P=PAGES) == "attention"
    assert run("L.defaultView(P)", P=[PAGES[0], PAGES[3]]) == "all" and run("L.defaultView(P)", P=[]) == "all"
    assert run("L.viewCounts(P)", P=[]) == {"attention": 0, "unverified": 0, "healthy": 0, "all": 0}


def test_a_view_shows_only_its_pages_and_the_text_filter_looks_at_the_address_title_headline_ad_groups_and_keywords():
    assert [p["id"] for p in run("L.filterPages(P,'attention','')", P=PAGES)] == ["a.test/two", "a.test/five"]
    assert [p["id"] for p in run("L.filterPages(P,'unverified','')", P=PAGES)] == ["a.test/three"]
    assert [p["id"] for p in run("L.filterPages(P,'all','')", P=PAGES)] == [p["id"] for p in PAGES]
    rich = [pg("a.test/p1", title="Emergency Dentist Boston", h1=("Same day care",), groups=[("Emergency", ["tooth pain"])]), pg("a.test/p2", title="Braces", groups=[("Ortho", ["invisalign"])])]
    for q, want in (("emergency", ["a.test/p1"]), ("SAME DAY", ["a.test/p1"]), ("tooth pain", ["a.test/p1"]), ("ortho", ["a.test/p2"]), ("invisalign", ["a.test/p2"]), ("a.test/p", ["a.test/p1", "a.test/p2"]), ("  ", ["a.test/p1", "a.test/p2"]),
                    ("zzz", [])):
        assert [p["id"] for p in run("L.filterPages(P,'all',q)", P=rich, q=q)] == want, q
    assert [p["id"] for p in run("L.filterPages(P,'attention','a.test/f')", P=PAGES)] == ["a.test/five"]            # the view and the text both apply


def test_pages_sort_by_spend_by_lowest_score_with_unscored_last_or_by_most_problems():
    assert [p["id"] for p in run("L.sortPages(P,'spend')", P=PAGES)] == ["a.test/one", "a.test/two", "a.test/three", "a.test/four", "a.test/five"]
    assert [p["id"] for p in run("L.sortPages(P,'score')", P=PAGES)] == ["a.test/two", "a.test/four", "a.test/five", "a.test/one", "a.test/three"]
    assert [p["id"] for p in run("L.sortPages(P,'problems')", P=PAGES)] == ["a.test/four", "a.test/two", "a.test/five", "a.test/one", "a.test/three"]
    assert [p["id"] for p in run("L.sortPages(P,'nonsense')", P=PAGES)] == [p["id"] for p in PAGES]
    original = [p["id"] for p in PAGES]
    assert [p["id"] for p in run("(()=>{const c=P.slice();L.sortPages(c,'score');return c;})()", P=PAGES)] == original            # sorting never reorders the report


def test_problems_are_failures_plus_warnings():
    assert run("L.problems(p)", p=pg(fail=3, warn=4)) == 7 and run("L.problems({})") == 0


# ── Wording ─────────────────────────────────────────────────────────────────

def test_money_reads_in_the_accounts_currency_with_cents_only_for_small_amounts():
    assert run("L.moneyText(1234.5,'USD')") == "$1,235" and run("L.moneyText(12.5,'USD')") == "$12.50" and run("L.moneyText(0,'USD')") == "$0.00"
    assert run("L.moneyText(500,'GBP')") == "£500" and run("L.moneyText(500,'ZZZ9')") == "$500" and run("L.moneyText(null)") == "n/a" and run("L.moneyText(NaN)") == "n/a"


def test_rates_numbers_and_shares_read_plainly():
    assert run("L.rateText(3.456)") == "3.5%" and run("L.rateText(0)") == "0%" and run("L.rateText(null)") == "n/a"
    assert run("L.numText(12345.6)") == "12,346" and run("L.numText(null)") == "n/a"
    assert run("L.shareText(58,100)") == "58%" and run("L.shareText(1,0)") == "0%"


def test_a_long_page_address_is_cut_in_the_middle_and_the_scheme_and_www_are_dropped():
    assert run("L.shortPage('https://www.a.test/x')") == "a.test/x"
    long = "gentledental.com/lp/dental-offices/ma/boston/some-very-long-location-name-that-goes-on"
    out = run("L.shortPage(s, 40)", s=long)
    assert len(out) == 40 and out.startswith("gentledental.com/lp/") and out.endswith("-goes-on") and "…" in out
    assert run("L.shortPage(s, 200)", s=long) == long and run("L.shortPage(null)") == ""
    assert run("L.hostOf('a.test/x/y')") == "a.test"


def test_device_rates_say_how_phones_do_against_desktops_and_how_much_of_the_traffic_they_are():
    d = {"mobile": {"clicks": 300, "cost": 60, "conversions": 6}, "desktop": {"clicks": 100, "cost": 40, "conversions": 4}}
    r = run("L.deviceRates(d)", d=d)
    assert r["mobile"]["rate"] == 2.0 and r["desktop"]["rate"] == 4.0 and r["gap"] == 0.5 and r["mobileShare"] == 75.0 and r["tablet"] is None
    none = run("L.deviceRates(d)", d={"mobile": {"clicks": 50, "cost": 1, "conversions": 1}, "desktop": {"clicks": 50, "cost": 1, "conversions": 0}})
    assert none["gap"] is None                                                       # desktop never converted: nothing to compare with
    assert run("L.deviceRates(null)")["gap"] is None


def test_the_answer_a_page_gave_reads_in_words():
    assert run("L.statusText(null)") == "Not fetched" and run("L.statusText({skipped:true})") == "Not read in time"
    assert run("L.statusText({templated:true,status:0})") == "Not fetched (the address is filled in by Google when someone clicks)"
    assert run("L.statusText({blocked:true,status:403})") == "Refused our request (HTTP 403)" and run("L.statusText({status:404})") == "HTTP 404"
    assert run("L.statusText({status:0,error:'timed out'})") == "Could not be reached (timed out)" and run("L.statusText({status:0})") == "Could not be reached"
    assert run("L.statusText({status:200,responseMs:340})") == "HTTP 200 in 340 ms" and run("L.statusText({status:200,responseMs:1800})") == "HTTP 200 in 1.8 s"


SEEN = {"status": 200, "responseMs": 300, "redirected": True, "finalUrl": "https://a.test/y", "title": "T", "h1": ["Main"], "h1Count": 2, "words": 1200, "scriptBuilt": False,
        "forms": [{"fields": 3, "required": 2}], "embeds": [], "ctas": [{"text": "Book now"}, {"text": "Call"}], "tel": ["+1617"], "phones": [], "links": {"total": 20, "nav": 5},
        "images": {"count": 10, "noSize": 2, "lazy": 6}, "scripts": 12, "thirdParty": 3, "blockingScripts": 1, "bytes": 51200, "truncated": False, "encoding": "gzip", "proof": ["reviews"],
        "privacyLink": True, "tools": ["Google Tag Manager"]}


def test_what_was_seen_on_a_page_is_laid_out_as_label_and_value_rows():
    rows = dict(run("L.seenRows(s)", s=SEEN))
    assert rows["Answer"] == "HTTP 200 in 300 ms" and rows["Ended on"] == "https://a.test/y" and rows["Title"] == "T" and rows["Main headline"] == "Main (2 H1 headings)"
    assert rows["Forms"] == "3 fields (2 required)" and rows["Calls to action"] == "Book now, Call" and rows["Phone"] == "tappable (+1617)" and rows["Links"] == "20 (5 in the header or menu)"
    assert rows["Images"] == "10 (2 without a set size, 6 lazy-loaded)" and rows["Scripts"] == "12 (3 from other sites, 1 hold up the first paint)"
    assert rows["Size and compression"] == "50 KB, gzip" and rows["Proof"] == "reviews" and rows["Privacy policy link"] == "yes" and rows["Tags seen"] == "Google Tag Manager"


def test_the_rows_say_none_where_there_is_none_and_never_pretend_a_page_that_was_not_read_has_facts():
    bare = dict(SEEN, forms=[], embeds=["forms.hubspot.com"], ctas=[], tel=[], phones=["(617) 555-0123"], proof=[], privacyLink=False, tools=[], encoding="", truncated=True, scriptBuilt=True, redirected=False)
    rows = dict(run("L.seenRows(s)", s=bare))
    assert rows["Forms"] == "an embedded form (forms.hubspot.com)" and rows["Calls to action"] == "none found" and rows["Phone"] == "(617) 555-0123 (text only)"
    assert rows["Size and compression"] == "50 KB (cut at the reading limit), not compressed" and rows["Privacy policy link"] == "no" and rows["Tags seen"] == "none seen" and "Ended on" not in rows
    assert "built by scripts" in rows["Words"]
    assert run("L.seenRows(null)") == [] and run("L.seenRows({status:403,blocked:true})") == [["Answer", "Refused our request (HTTP 403)"]]


def test_a_finding_gets_its_catalog_wording_back_and_a_missing_catalog_entry_does_not_break_it():
    cat = {"x_1": {"title": "The title", "why": "Because.", "fix": "Do it.", "section": "match", "severity": "high"}}
    f = run("L.full(f,c)", f={"id": "x_1", "result": "fail", "found": "Found.", "items": ["a"], "count": 3}, c=cat)
    assert (f["title"], f["why"], f["fix"], f["section"], f["severity"], f["count"], f["items"]) == ("The title", "Because.", "Do it.", "match", "high", 3, ["a"])
    g = run("L.full(f,c)", f={"id": "zz", "result": "warn", "severity": "low"}, c=cat)
    assert g["title"] == "zz" and g["items"] == [] and g["count"] == 0 and g["found"] == ""
    assert run("L.full(f,c)", f={"id": "x_1", "result": "fail", "severity": "critical"}, c=cat)["severity"] == "critical"          # a finding's own severity wins


def test_page_findings_carry_their_section_label():
    page = {"sections": [{"label": "The page opens", "findings": [{"id": "a", "result": "pass"}]}, {"label": "Loads fast", "findings": [{"id": "b", "result": "fail"}, {"id": "c", "result": "warn"}]}]}
    out = run("L.pageFindings(p,{})", p=page)
    assert [(f["id"], f["sectionLabel"]) for f in out] == [("a", "The page opens"), ("b", "Loads fast"), ("c", "Loads fast")]


def test_what_a_keyword_is_doing_on_the_page_has_a_label_and_a_tone_for_every_answer_the_server_can_give():
    for key, label, tone in (("headline", "In the headline", "good"), ("page", "On the page", "good"), ("partial", "Partly there", "warn"), ("none", "Not on the page", "bad"), (None, "Not checked", "unk")):
        assert run("L.onInfo(k)", k=key) == {"label": label, "tone": tone}
    assert run("L.onInfo(true)")["label"] == "On the page" and run("L.onInfo(false)")["label"] == "Not on the page" and run("L.onInfo('weird')")["label"] == "Not checked"
    assert run("L.ratingInfo('below')") == {"label": "Below average", "tone": "bad"} and run("L.ratingInfo(null)")["label"] == "Not rated"


def test_the_lab_test_reads_in_seconds_with_a_tone_and_rows_for_the_page_to_draw():
    assert run("L.secText(3100)") == "3.1 s" and run("L.secText(410)") == "410 ms" and run("L.secText(null)") == "n/a" and run("L.secText(999.6)") == "1000 ms"
    assert [run("L.labTone(s)", s=s) for s in (100, 90, 89, 50, 49, 0, None)] == ["good", "good", "warn", "warn", "bad", "bad", "unk"]
    lab = {"score": 62, "lcpMs": 3100, "tbtMs": 410, "cls": 0.123, "fcpMs": 1800, "speedIndexMs": 3300, "field": {"category": "average", "lcpMs": 2800, "inpMs": 190, "cls": 0.12}}
    rows = dict(run("L.labRows(l)", l=lab))
    assert rows["Performance score"] == "62 out of 100" and rows["Largest content shown after"] == "3.1 s" and rows["Page blocked for"] == "410 ms" and rows["Layout shift"] == "0.123"
    assert rows["Real visitors (Chrome UX Report)"] == "rated average, largest content after 2.8 s, interaction delay 190 ms, layout shift 0.12"
    bare = dict(run("L.labRows(l)", l={"score": None}))
    assert bare["Performance score"] == "not scored" and bare["Largest content shown after"] == "n/a" and "Real visitors (Chrome UX Report)" not in bare
    assert run("L.labRows(null)") == [] and dict(run("L.labRows(l)", l={"score": 1, "field": {"category": None}}))["Real visitors (Chrome UX Report)"] == "no figures"


def test_every_verdict_the_model_can_give_has_a_label_and_a_tone():
    from tracker import paid_lp_ai
    for v, tone in (("matches", "good"), ("partly", "warn"), ("mismatch", "bad")):
        assert run("L.verdictInfo(v).tone", v=v) == tone and run("L.verdictInfo(v).label", v=v)
    assert set(run("Object.keys(L.VERDICT)")) == set(paid_lp_ai.VERDICTS) and run("L.verdictInfo(null)") is None and run("L.verdictInfo('great')") is None


def test_the_headline_tiles_say_what_is_at_stake_and_are_calm_when_nothing_is():
    s = {"checked": 24, "pagesOfSpend": 93, "spend": 85000, "spendChecked": 49000, "spendAtRisk": 12000, "pagesAtRisk": 3, "critical": 2, "high": 5, "tally": {"warn": 9}, "spendUnverified": 3000}
    t = {x["key"]: x for x in run("L.tiles(s,'USD')", s=s)}
    assert t["checked"]["value"] == "24 of 93" and t["checked"]["hint"] == "58% of the spend"
    assert t["risk"]["value"] == "$12,000" and t["risk"]["tone"] == "bad" and "3 pages" in t["risk"]["hint"]
    assert t["critical"]["tone"] == "bad" and t["high"]["value"] == 5 and t["warn"]["tone"] == "warn" and t["unverified"]["value"] == "$3,000" and t["unverified"]["tone"] == "unk"
    calm = {x["key"]: x for x in run("L.tiles(s,'USD')", s={"checked": 5, "pagesOfSpend": 5, "spend": 100, "spendChecked": 100, "tally": {}})}
    assert all(calm[k]["tone"] == "good" for k in ("risk", "critical", "high", "warn", "unverified")) and calm["unverified"]["hint"] == "Every page was verified"
    empty = {x["key"]: x for x in run("L.tiles(s,'USD')", s={})}
    assert empty["checked"]["value"] == "0 of 0" and empty["checked"]["hint"] == "no page took money"


def test_the_account_is_named_with_its_id():
    assert run("L.readNote(r)", r={"account": {"name": "Acme", "id": "1112223334"}}) == "Acme (1112223334)"
    assert run("L.readNote(r)", r={"account": {"id": "1112223334"}}) == "1112223334" and run("L.readNote(null)") == ""


# ── The server and the page agree ───────────────────────────────────────────

def test_the_server_and_the_page_use_the_same_words_for_where_a_keyword_sits_and_how_it_is_rated():
    from tracker import paid_lp_export as ex
    assert {k: run("L.onInfo(k).label", k=k) for k in ("headline", "page", "partial", "none")} == {k: ex.ON_TEXT[k] for k in ("headline", "page", "partial", "none")}
    for k in ("below", "average", "above"):
        assert run("L.ratingInfo(k).label", k=k) == ex.RATING_TEXT[k]
    assert run("L.onInfo(null).label") == ex.ON_TEXT[None]


def test_every_state_the_server_can_report_for_a_keyword_has_a_label_in_the_page():
    from tracker import paid_lp_match as words
    for state in ("headline", "page", "partial", "none"):
        assert run("L.ON[k]!==undefined", k=state) is True
    prom = words.vocab("emergency dentist")
    full = prom | words.vocab("boston")
    got = {words.hit("emergency dentist", prom, full), words.hit("boston", prom, full), words.hit("emergency crowns", prom, full), words.hit("zebra", prom, full)}
    assert got == {"headline", "page", "partial", "none"}


# ── The page against its template ───────────────────────────────────────────

def test_the_page_script_and_data_are_valid_javascript():
    for p in (PAGE, DATA):
        r = subprocess.run(["node", "--check", p], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr


def test_every_element_the_page_script_reaches_for_is_in_the_template():
    js, html = open(PAGE, encoding="utf-8").read(), open(TEMPLATE, encoding="utf-8").read()
    ids = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', js)) | set(re.findall(r'getElementById\("([A-Za-z0-9_-]+)"\)', js))
    assert ids >= {"acctPick", "msg", "empty", "skel", "out", "fresh", "stale", "score", "tiles", "secs", "sectionsCard", "fix", "fixNote", "fixCard", "pagesCard", "pagesNote", "viewChips", "sortSel",
                   "pageFind", "pageList", "moreBtn", "notes", "notesCard", "foot", "refreshBtn", "dlXlsx", "dlPdf", "dlZip"}
    for i in sorted(ids):
        assert 'id="%s"' % i in html, i
    for attr in ("data-open", "data-page", "data-tab", "data-passed", "data-view", "data-lab", "data-read"):
        assert attr in js, attr


def test_the_page_loads_its_scripts_in_order_and_both_stylesheets():
    html = open(TEMPLATE, encoding="utf-8").read()
    order = [html.index(s) for s in ("js/pm_picker.js", "js/pm_platform.js", "js/au_data.js", "js/lp_data.js", "js/landing_page_check.js")]
    assert order == sorted(order) and "css/account_audit.css" in html and "css/landing_page_check.css" in html
    assert "root.LpData = api" in open(DATA, encoding="utf-8").read() and "window.LpData" in open(PAGE, encoding="utf-8").read() and "window.AuditData" in open(PAGE, encoding="utf-8").read()


def test_the_page_never_puts_a_page_address_into_a_link_it_has_not_checked():
    js = open(PAGE, encoding="utf-8").read()
    assert "safeUrl" in js and 'rel="noopener noreferrer"' in js
    assert "innerHTML" in js and "esc(" in js


def test_there_is_no_em_or_en_dash_in_any_of_the_page():
    for p in (DATA, PAGE, TEMPLATE, CSS):
        text = open(p, encoding="utf-8").read()
        assert "—" not in text and "–" not in text, p


# ── The playbook: how the fix-first cards and the check cards are laid out ──

def test_the_fix_first_cards_know_their_place_and_how_much_money_each_carries():
    rows = [{"spend": 400.0}, {"spend": 100.0}, {"spend": 1.0}, {"spend": 0}, {"spend": 400.0}]
    got = run("L.impact(rows)", rows=rows)
    assert [g["rank"] for g in got] == [1, 2, 3, 4, 5]
    assert [g["share"] for g in got] == [100, 25, 4, 4, 100]                  # a bar is never empty while there is a biggest row, and never over the whole
    assert [g["start"] for g in got] == [True, True, True, False, False]


def test_a_playbook_with_no_money_has_empty_bars_and_with_no_rows_nothing():
    assert [g["share"] for g in run("L.impact(rows)", rows=[{"spend": 0}, {}])] == [0, 0]
    assert run("L.impact([])") == [] and run("L.impact(null)") == [] and run("L.impact(undefined)") == []
    assert run("L.START_HERE") == 3


def test_the_counts_under_a_card_are_pills_that_name_what_is_failing_and_what_is_a_warning():
    assert run("L.fixPills(r)", r={"failed": 3, "warned": 1}) == [{"text": "4 pages", "tone": ""}, {"text": "3 failing", "tone": "bad"}, {"text": "1 warning", "tone": "warn"}]
    assert run("L.fixPills(r)", r={"failed": 1, "warned": 0}) == [{"text": "1 page", "tone": ""}, {"text": "1 failing", "tone": "bad"}]
    assert run("L.fixPills(r)", r={"failed": 0, "warned": 2}) == [{"text": "2 pages", "tone": ""}, {"text": "2 warnings", "tone": "warn"}]


def test_a_checks_weight_is_worded_for_a_person():
    assert [run("L.sevWord(s)", s=s) for s in ("critical", "high", "medium", "low", "odd", None)] == ["Critical", "High impact", "Medium", "Minor", "Minor", "Minor"]


def test_the_playbook_is_drawn_from_the_report_the_template_has_the_list_for_and_the_copy_button_is_wired():
    js = open(PAGE, encoding="utf-8").read()
    tpl = open(TEMPLATE, encoding="utf-8").read()
    assert 'id="fix"' in tpl and 'class="lp-fixlist"' in tpl
    assert "L.impact(" in js and "L.fixPills(" in js and "data-copy" in js and "function copyText" in js
    css = open(CSS, encoding="utf-8").read()
    for cls in (".lp-fx", ".lp-f2", ".lp-rd", ".lp-do2", ".lp-ad", ".lp-copy", ".lp-fixlist"):
        assert cls in css, cls
    assert "prefers-reduced-motion" in css


# ── Meta ────────────────────────────────────────────────────────────────────

def test_a_page_with_no_platforms_listed_is_a_google_page_and_the_platforms_are_named():
    assert run("L.platformsOf({})") == ["google"] and run("L.platformsOf(null)") == ["google"] and run("L.platformsOf({platforms:[]})") == ["google"]
    assert run("L.platformsOf({platforms:['meta']})") == ["meta"]
    assert run("[L.reaches({platforms:['meta']}, 'meta'), L.reaches({platforms:['meta']}, 'google'), L.reaches({}, 'google'), L.reaches({platforms:['google','meta']}, 'meta')]") == [True, False, True, True]
    assert run("L.platformNames(['google','meta'])") == "Google Ads and Meta Ads" and run("L.platformNames(['google','meta'], ' or ')") == "Google Ads or Meta Ads"
    assert run("L.platformNames(undefined)") == "Google Ads" and run("L.platformNames(['tiktok'])") == "tiktok"


def test_a_saved_copy_says_which_platform_is_not_answering_and_a_google_report_reads_as_before():
    assert run("L.staleText({platform:'meta'}, '3 hours ago')") == "Meta Ads is not answering now, so this report is built from the copy saved 3 hours ago."
    assert run("L.staleText({platform:'google'}, 'x')").startswith("Google Ads is not answering now") and run("L.staleText({}, 'x')").startswith("Google Ads is not")
    assert run("L.staleText({platform:'tiktok'}, 'x')").startswith("The platform is not answering now")
    foot = run("L.sourceText(['google'])")
    assert foot.startswith("Source: Google Ads (read only) and the pages themselves, saved for an hour.") and foot.endswith("Nothing is changed in Google Ads: make the changes there and on the site.")
    both = run("L.sourceText(['google','meta'])")
    assert both.startswith("Source: Google Ads and Meta Ads (read only)") and both.endswith("Nothing is changed in Google Ads or Meta Ads: make the changes there and on the site.")
    assert run("L.sourceText(undefined)") == foot


def test_a_page_nothing_reaches_is_explained_in_the_words_of_the_platforms_read():
    google = run("L.noGroupsText(['google'])")
    assert google.startswith("No Search ad group sends people to this page.") and "Meta" not in google
    both = run("L.noGroupsText(['google','meta'])")
    assert both.startswith("No Search ad group or Meta ad set sends people to this page.") and "Meta ads that carry no words" in both
    assert run("L.noGroupsText(undefined)") == google


def test_a_report_of_two_accounts_names_both_ids():
    assert run("L.readNote(r)", r={"account": {"name": "Acme", "id": "1112223334+act_123"}, "accounts": [{"id": "1112223334"}, {"id": "act_123"}]}) == "Acme (1112223334 and act_123)"
    assert run("L.readNote(r)", r={"account": {"name": "Acme", "id": "1112223334"}, "accounts": [{"id": "1112223334"}]}) == "Acme (1112223334)"


def test_the_page_offers_all_three_platforms_and_every_new_class_it_draws_is_styled():
    page, css = open(PAGE, encoding="utf-8").read(), open(CSS, encoding="utf-8").read()
    assert 'allow: ["google", "meta", "both"]' in page and "#pf=" in page
    for cls in (".lp-plat", ".lp-plat.meta", ".lp-plat.google"):
        assert cls in css
    assert "lp-plat" in page and "metaAds(g)" in page
