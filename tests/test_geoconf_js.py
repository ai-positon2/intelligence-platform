"""Location Conflicts: static/js/geoconf_data.js run in Node, and the page script run against a stub page and the real report the server builds.

The server reads where each campaign shows and works out the conflicts (tests/test_paid_geoconf*.py). The page only draws, so how a status and a level read, which accounts a view shows, how a
campaign's places are said and the CSV come from geoconf_data.js (executed here, not read), and geoconf.js is run for real: it loads the accounts and what the team saved, draws the report the
server built, saves places and says what went wrong in words. The HTML it writes is read back.
"""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tests import test_paid_geoconf as TG  # noqa: E402
from tests.test_copycheck_js import HARNESS  # noqa: E402
from tracker import paid_geoconf as gc  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js")
DATA, PAGE = os.path.join(JS, "geoconf_data.js"), os.path.join(JS, "geoconf.js")
TEMPLATE = os.path.join(ROOT, "templates", "location_conflicts.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")

GROUP = "mcc-9923720096-USD"
ACCOUNTS = [TG.ACCT, TG.ACCT2]
BAD_CAMP = {"campaign": {"id": "5", "name": "Search <b>Quincy</b>", "advertisingChannelType": "SEARCH",
                         "geoTargetTypeSetting": {"positiveGeoTargetType": "PRESENCE_OR_INTEREST", "negativeGeoTargetType": "PRESENCE_OR_INTEREST"}}}
EXTRA = {"ads": [TG.rsa(ad="1", gname="Dover_Emergency", heads=("Emergency dentist in Quincy",))],
         "groups": [{"campaign": {"id": "5"}, "adGroup": {"id": "10", "name": "Dover_Emergency"}}],
         "keywords": [{"campaign": {"id": "5"}, "adGroup": {"id": "10", "name": "Dover_Emergency"}, "adGroupCriterion": {"keyword": {"text": "dentist dover"}}}]}


def run(expr, **data):
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    code = "const X=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(DATA), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.fixture(scope="module")
def reports():
    mp = pytest.MonkeyPatch()
    from tracker import paid_gads as base, paid_geo, paid_store
    saved = {}
    mp.setattr(paid_store, "get_gads_cache", lambda key, ttl: saved.get(key))
    mp.setattr(paid_store, "put_gads_cache", lambda key, value: saved.update({key: value}))
    mp.setattr(paid_store, "get_gads_entry", lambda key, ttl: None, raising=False)
    group = {"id": GROUP, "name": "Group", "currency": "USD", "accountIds": [a["id"] for a in ACCOUNTS]}

    def report(account, search, **kw):
        paid_geo.clear_cache()
        base.reset_state()
        base.forget("")
        return gc.report_for(account, accounts_fn=lambda: ACCOUNTS, groups_fn=lambda: [group], search_fn=search, today=TG.TODAY, **kw)
    locs = {TG.ACCT["id"]: [TG.loc_row("5", TG.QUINCY), TG.loc_row("6", TG.DOVER)], TG.ACCT2["id"]: [TG.loc_row("5", TG.QUINCY)]}
    camps = {TG.ACCT["id"]: [BAD_CAMP, TG.campaign("6", "Search_Dover")], TG.ACCT2["id"]: [TG.campaign()]}
    out = {"one": report(TG.ACCT["id"], TG.searcher(camps=camps, locs=locs, extra=EXTRA)),
           "mcc": report(GROUP, TG.searcher(camps=camps, locs=locs, extra=EXTRA)),
           "clean": report(TG.ACCT2["id"], TG.searcher(camps=camps, locs=locs)),
           "nonames": report(TG.ACCT2["id"], TG.searcher(camps=camps, locs=locs, fail_kinds=("names",)))}
    adsets = [{"id": "77", "name": "Quincy adset", "campaign": {"id": "9", "name": "Dover Leads"}, "targeting": {"geo_locations": {"cities": [{"name": "Quincy", "region": "Massachusetts", "country": "US"}], "location_types": ["home", "travel_in"]}}},
              {"id": "78", "name": "Dover adset", "campaign": {"id": "10", "name": "B"}, "targeting": {"geo_locations": {"cities": [{"name": "Dover", "region": "New Hampshire", "country": "US"}]}}}]
    ads = [{"id": "500", "campaign": {"id": "9", "name": "Dover Leads"}, "adset": {"id": "77", "name": "Quincy adset"}, "creative": {"body": "Dentist in Quincy"}}]
    out["meta"] = report("act_1234567", TG.searcher(), meta_pages_fn=lambda path, params: adsets if path.endswith("/adsets") else ads, meta_account_fn=lambda act: {"name": "Beta Meta", "currency": "EUR"})
    yield out
    mp.undo()


# ── the data helpers ────────────────────────────────────────────────────────

def test_a_status_a_level_a_scope_and_an_option_read_in_words_and_an_unknown_one_reads_as_itself():
    assert run("X.statusInfo('fix')") == {"label": "Fix these", "tone": "bad"} and run("X.statusInfo('check')")["label"] == "Small settings" and run("X.statusInfo('clean')")["tone"] == "ok"
    assert run("X.statusInfo('zz')") == {"label": "zz", "tone": "unk"}
    assert run("X.levelInfo('high')") == {"label": "High", "tone": "bad"} and run("X.levelInfo('low')")["tone"] == "wait" and run("X.levelInfo('q')") == {"label": "q", "tone": "unk"}
    assert [run("X.scopeText(%s)" % json.dumps(k)) for k in ("campaign", "adgroup", "ad", "account", "zz", None)] == ["Campaign", "Ad group", "Ad", "Account", "zz", ""]
    assert [run("X.optionText(%s)" % json.dumps(k)) for k in ("PRESENCE", "PRESENCE_OR_INTEREST", "TRAVEL_IN", "", "OTHER")] == [
        "People in or regularly in the places", "People in, regularly in or interested in the places", "Also people travelling in the places", "Not read", "OTHER"]
    assert [run("X.channelText(%s)" % json.dumps(k)) for k in ("SEARCH", "PERFORMANCE_MAX", "META", "", "ZZ")] == ["Search", "Performance Max", "Meta ad set", "Campaign", "ZZ"]


def test_the_tiles_say_what_to_fix_and_what_only_needs_a_look(reports):
    t = run("X.tiles(R)", R=reports["mcc"])
    assert [x[0] for x in t] == ["Campaigns read", "To fix", "Small settings", "Findings", "Accounts to fix"]
    s = reports["mcc"]["summary"]
    assert t[0][1] == str(s["campaigns"]) and t[1] == ["To fix", str(s["high"] + s["medium"]), "%d high, %d medium" % (s["high"], s["medium"]), "warn"] and t[4][1] == "1" and t[4][3] == "warn"
    c = run("X.tiles(R)", R=reports["clean"])
    assert c[1] == ["To fix", "0", "no campaign shows where it should not", "good"] and c[2] == ["Small settings", "0", "nothing to check", ""] and c[4] == ["Accounts to fix", "0", "of 1 account read", "good"]
    low = json.loads(json.dumps(reports["clean"]))
    low["summary"]["low"], low["summary"]["counts"] = 2, {"fix": 0, "check": 1, "clean": 0}
    assert run("X.tiles(R)", R=low)[2][2] == "shared places, schedules and devices to check" and run("X.tiles(R)", R=low)[4][2] == "1 with small settings only"


def test_the_views_hold_the_accounts_that_need_a_look(reports):
    assert [a["name"] for a in run("X.shown(R, 'attention')", R=reports["mcc"])] == ["Acme Dental"] and len(run("X.shown(R, 'all')", R=reports["mcc"])) == 2
    assert run("X.firstView(R)", R=reports["mcc"]) == "attention" and run("X.firstView(R)", R=reports["clean"]) == "all" and run("X.viewCount(R, 'all')", R=reports["mcc"]) == 2
    assert run("X.needsLook({status: 'check'})") is True and run("X.needsLook({status: 'clean'})") is False
    assert run("X.query('1234567890', true)") == "account=1234567890&refresh=1" and run("X.query('act_1', false)") == "account=act_1" and run("X.query('a b', false)") == "account=a%20b"


def test_the_places_of_a_campaign_are_said_in_a_sentence():
    assert run("X.targetsText({targets: []})") == "no location set" and run("X.targetsText({targets: ['A', 'B']})") == "A, B" and run("X.targetsText({targets: ['A', 'B', 'C', 'D', 'E']})") == "A, B, C and 2 more"
    assert run("X.targetsText({targets: ['A', 'B', 'C']})") == "A, B, C" and run("X.excludedText({excluded: []})") == "" and run("X.excludedText({excluded: ['A']})") == "not in A"
    assert run("X.excludedText({excluded: ['A', 'B', 'C', 'D']})") == "not in A, B, C and 1 more"


def test_the_campaigns_with_something_to_look_at_are_told_from_the_quiet_ones(reports):
    a = reports["one"]["accounts"][0]
    assert [c["name"] for c in run("X.flagged(A)", A=a)] == ["Search <b>Quincy</b>"] and [c["name"] for c in run("X.quiet(A)", A=a)] == ["Search_Dover"]


def test_a_finding_that_names_places_can_be_answered_with_this_is_not_a_place(reports):
    f = {"rule": "copy_place", "places": ["Orange", "orange", "", "Dover"]}
    assert run("X.ignorable(F)", F=f) == ["Orange", "Dover"] and run("X.ignorable(F)", F={"rule": "setting", "places": ["Quincy"]}) == [] and run("X.ignorable(F)", F={"rule": "kw_place"}) == []


def test_what_shows_only_across_the_campaigns_is_said_in_sentences(reports):
    ins = run("X.insights(R)", R=reports["one"])
    assert [i["kind"] for i in ins] == ["option"] and ins[0]["text"] == "1 campaign of 2 are set to show to people interested in the places, not only people in them."
    assert run("X.insights(R)", R=reports["clean"]) == []
    names = run("X.insights(R)", R=reports["nonames"])
    assert [i["kind"] for i in names] == ["names"] and names[0]["text"].startswith("The names of the places could not be read")
    mcc = run("X.insights(R)", R=reports["mcc"])
    assert mcc[0]["text"].startswith("Acme Dental: ")
    every = json.loads(json.dumps(reports["one"]))
    every["accounts"][0]["items"][1]["positive"] = "TRAVEL_IN"
    assert run("X.insights(R)", R=every)[0]["text"].startswith("Every campaign is set to show to people interested")
    assert run("X.insights(R)", R={"account": {"isGroup": False}, "accounts": [{"name": "A", "campaigns": 0, "namesRead": True, "items": []}]}) == [{"kind": "none", "text": "No enabled campaign was found."}]


def test_the_csv_has_one_line_per_finding_and_one_per_account_with_none(reports):
    rows = run("X.csvRows(R)", R=reports["mcc"])
    assert rows[0] == ["Location Conflicts", "Group"] and rows[3] == run("X.HEAD") and len(rows[3]) == 11
    body = rows[4:]
    found = sum(a["findingCount"] for a in reports["mcc"]["accounts"])
    assert found > 0 and len([r for r in body if r[1] == "Google Ads" and r[2] != "(nothing found)"]) == found and [r for r in body if r[2] == "(nothing found)"][0][0] == "Beta Clinic"
    gc_row = [r for r in body if r[5] == "Ad group and its ad name different places"][0]
    assert gc_row[4] == "High" and gc_row[6] == "Ad" and gc_row[8] == "Dover, Quincy" and gc_row[9] == "Emergency dentist in Quincy" and gc_row[3] == "Dover_Emergency" and gc_row[10].startswith("Make the ad say")
    assert run("X.csvRows(R)[2]", R=reports["one"]) == [] and run("X.csvRows(R)[1]", R=reports["one"]) == ["Read", reports["one"]["readAt"]]
    meta = run("X.csvRows(R)", R=reports["meta"])
    assert all(r[1] == "Meta" for r in meta[4:])


def test_what_the_team_saved_is_shown_as_the_form_holds_it():
    assert run("X.savedView(S)", S={"areas": ["Greater Boston", "South Shore"], "ignore": ["Orange", "Reading"], "acceptPresence": True}) == {"areas": "Greater Boston\nSouth Shore", "ignore": "Orange, Reading", "acceptPresence": True}
    assert run("X.savedView(null)") == {"areas": "", "ignore": "", "acceptPresence": False}


# ── the page script ─────────────────────────────────────────────────────────

SCRIPTS = [DATA, PAGE]
ACCTS = {"accounts": [dict(a, group=GROUP) for a in ACCOUNTS], "groups": [{"id": GROUP, "name": "Group", "accountIds": [a["id"] for a in ACCOUNTS]}]}
SETTINGS = {"accounts": {"1234567890": {"areas": ["Greater Boston"], "ignore": ["Orange"], "acceptPresence": True, "name": "Acme Dental"}}, "changes": []}
HARNESS_PAGE = HARNESS.replace("/p2/paid-marketing/ad-copy-checker", "/p2/paid-marketing/location-conflicts")


def page(scenario):
    out = subprocess.run(["node", "-e", HARNESS_PAGE, json.dumps(SCRIPTS), json.dumps(scenario)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def opened(report, steps=None, **kw):
    sc = {"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/geoconf/report", "body": report}, {"match": "/geoconf/settings", "body": SETTINGS}],
          "steps": [{"pick": "1234567890"}] + (steps or [])}
    sc.update(kw)
    return page(sc)


def test_every_element_the_script_reaches_for_is_in_the_template_and_the_scripts_are_loaded():
    html = open(TEMPLATE, encoding="utf-8").read()
    js = open(PAGE, encoding="utf-8").read()
    for sel in sorted(set(re.findall(r'\$\("(#[A-Za-z]+)"\)', js))):
        assert 'id="%s"' % sel[1:] in html, sel
    assert html.index("pm_picker.js") < html.index("monitor_panel.js") < html.index("geoconf_data.js") < html.index("geoconf.js")
    assert "—" not in html and "–" not in html and "—" not in js and "—" not in open(DATA, encoding="utf-8").read()


def test_the_accounts_and_what_the_team_saved_are_loaded_and_nothing_is_read_until_one_is_chosen():
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/geoconf/settings", "body": SETTINGS}]})
    items = out["sets"][-1]["items"]
    assert items[1]["label"] == "All of Group" and "MCC" in items[1]["badge"] and len(items) == 4 and out["panels"] == [{"agent": "geoconf", "label": "Location Conflicts"}]
    assert any(c.endswith("/geoconf/settings") for c in out["calls"]) and not any("/geoconf/report" in c for c in out["calls"]) and out["el"]["#empty"]["hidden"] is False and out["el"]["#out"]["hidden"] is True


def test_choosing_an_account_reads_the_report_and_remembers_the_account(reports):
    out = opened(reports["one"])
    assert [c for c in out["calls"] if "/geoconf/report" in c] == ["/p2/paid-marketing/api/geoconf/report?account=1234567890"]
    assert out["store"]["gc_account"] == "1234567890" and out["hist"][-1] == "/p2/paid-marketing/location-conflicts?account=1234567890" and out["el"]["#out"]["hidden"] is False


def test_the_report_is_drawn_worst_first_with_the_places_the_evidence_and_markup_escaped(reports):
    out = opened(reports["one"])
    kp, lst = out["el"]["#kpis"]["html"], out["el"]["#list"]["html"]
    for text in ("Campaigns read", "To fix", "Small settings", "Findings", "Accounts to fix"):
        assert text in kp, text
    assert 'class="bi-row stopped open"' in lst and "bi-pill bad" in lst and "Fix these" in lst and "What was found" in lst and "Fix plan" in lst and "Check again the next day" in lst
    assert lst.index("Ad group and its ad name different places") < lst.index("Keywords name a place the campaign does not target")                       # the table of what was found, high first
    assert "Search &lt;b&gt;Quincy&lt;/b&gt;" in lst and "<b>Quincy</b>" not in lst and "Search <b>" not in lst
    assert '<span>Shows in</span> Quincy' in lst and "People in, regularly in or interested in the places" in lst and "Emergency dentist in Quincy" in lst and "dentist dover" in lst
    assert 'Ignore "Dover" as a place</button>' in lst and "Search_Dover" in lst and "1 campaign with nothing wrong" in lst and '<details class="gc-quiet">' in lst and "<form class=\"cc-set gc-set\" data-account=\"1234567890\"" in lst
    assert "read " in out["el"]["#fresh"]["html"] and "Acme Dental" in out["el"]["#fresh"]["html"] and "the ones to fix first" in out["el"]["#listNote"]["text"]
    assert "Across the campaigns" in out["el"]["#insights"]["html"] and "set to show to people interested" in out["el"]["#insights"]["html"]


def test_the_form_holds_what_is_saved_and_the_checkbox_is_checked_when_the_option_was_accepted(reports):
    lst = opened(reports["one"])["el"]["#list"]["html"]
    assert ">Greater Boston</textarea>" in lst and ">Orange</textarea>" in lst and 'data-k="acceptPresence" checked' in lst
    empty = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/geoconf/report", "body": reports["one"]}, {"match": "/geoconf/settings", "body": {"accounts": {}, "changes": []}}], "steps": [{"pick": "1234567890"}]})
    assert 'data-k="acceptPresence" checked' not in empty["el"]["#list"]["html"] and 'data-k="acceptPresence">' in empty["el"]["#list"]["html"]


def test_a_clean_account_says_so_and_a_group_opens_to_the_accounts_that_need_a_look(reports):
    out = opened(reports["clean"])
    assert "Clean" in out["el"]["#list"]["html"] and "nothing wrong in 1 campaign" in out["el"]["#list"]["html"] and "Nothing to flag" in out["el"]["#list"]["html"]
    mcc = opened(reports["mcc"], [{"pick": GROUP}])
    lst = mcc["el"]["#list"]["html"]
    assert "Acme Dental" in lst and "Beta Clinic" not in lst and 'class="bi-row stopped"' in lst
    assert "Needs a look <span class=\"n\">1</span>" in mcc["el"]["#views"]["html"] and "MCC · 2 accounts checked together" in mcc["el"]["#fresh"]["html"]
    allv = opened(reports["mcc"], [{"pick": GROUP}, {"fire": ["#views", {"button": {"dataset": {"v": "all"}}}]}])["el"]["#list"]["html"]
    assert "Beta Clinic" in allv and "bi-pill ok" in allv and allv.index("Acme Dental") < allv.index("Beta Clinic")


def test_a_row_opens_and_closes_when_its_header_is_pressed(reports):
    steps = [{"pick": GROUP}, {"fire": ["#list", {".bi-head": {}, ".bi-row": {"dataset": {"id": "1234567890"}}}]}]
    out = opened(reports["mcc"], steps)
    assert 'class="bi-row stopped open"' in out["el"]["#list"]["html"]
    closed = opened(reports["mcc"], steps + steps[1:])
    assert 'class="bi-row stopped"' in closed["el"]["#list"]["html"] and "What was found" not in closed["el"]["#list"]["html"]


def test_an_account_whose_names_were_not_read_says_so_in_the_row_and_the_page(reports):
    out = opened(reports["nonames"])
    assert "place names not read" in out["el"]["#list"]["html"] and "compared with a name" in out["el"]["#msg"]["html"]


def test_a_meta_account_is_drawn_with_its_ad_sets(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/geoconf/report", "body": reports["meta"]}, {"match": "/geoconf/settings", "body": SETTINGS}], "steps": [{"pick": "1234567890"}]})
    lst = out["el"]["#list"]["html"]
    assert 'class="bi-mk">Meta</span>' in lst and "Meta ad set" in lst and "ad set" in lst and "Also people travelling in the places" in lst and "Dover Leads" in lst


def test_a_word_that_is_not_a_place_is_saved_for_the_account_and_the_report_is_read_again_without_the_skeleton(reports):
    steps = [{"fire": ["#list", {".gc-ign": {"dataset": {"account": "1234567890", "name": "Acme Dental", "word": "Dover"}}}]}]
    out = opened(reports["one"], steps, responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/geoconf/report", "body": reports["one"]}, {"match": "/geoconf/settings", "body": SETTINGS},
                                                  {"match": "/geoconf/settings/ignore", "method": "POST", "body": {"account": {"areas": [], "ignore": ["Dover"], "acceptPresence": False}}}])
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/geoconf/settings/ignore", "body": {"account": "1234567890", "words": "Dover"}}]
    assert len([c for c in out["calls"] if "/geoconf/report" in c]) == 2 and out["el"]["#out"]["hidden"] is False and out["el"]["#skel"]["hidden"] is True


def test_a_word_that_cannot_be_saved_says_why_and_a_blank_word_is_not_sent(reports):
    out = opened(reports["one"], [{"fire": ["#list", {".gc-ign": {"dataset": {"account": "1234567890", "name": "A", "word": "x1"}}}]}],
                 responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/geoconf/report", "body": reports["one"]}, {"match": "/geoconf/settings", "body": SETTINGS},
                            {"match": "/geoconf/settings/ignore", "method": "POST", "status": 400, "body": {"error": "x1 is not a word (use letters, like Quincy, MA)."}}])
    assert "x1 is not a word" in out["el"]["#msg"]["html"] and len([c for c in out["calls"] if "/geoconf/report" in c]) == 1
    blank = opened(reports["one"], [{"fire": ["#list", {".gc-ign": {"dataset": {"account": "1234567890", "name": "A", "word": ""}}}]}])
    assert blank["posts"] == []


def test_saving_the_places_of_an_account_sends_what_was_typed_and_reads_the_report_again(reports):
    areas = [{"k": "areas", "value": "Greater Boston"}, {"k": "ignore", "value": "Orange, Reading"}, {"k": "acceptPresence", "value": True}]
    out = opened(reports["one"], [{"submit": ["#list", {".gc-set": {"dataset": {"account": "1234567890", "name": "Acme Dental"}, "areas": areas}}]}],
                 responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/geoconf/report", "body": reports["one"]}, {"match": "/geoconf/settings", "body": SETTINGS},
                            {"match": "/geoconf/settings/account", "method": "POST", "body": {"account": {"areas": ["Greater Boston"], "ignore": ["Orange", "Reading"], "acceptPresence": True}}}])
    assert out["posts"] == [{"url": "/p2/paid-marketing/api/geoconf/settings/account", "body": {"account": "1234567890", "name": "Acme Dental", "areas": "Greater Boston", "ignore": "Orange, Reading", "acceptPresence": True}}]
    assert out["outs"][0]["[data-out]"] == "Saved. Checking again…" and len([c for c in out["calls"] if "/geoconf/report" in c]) == 2


def test_a_list_the_server_refuses_says_why_and_nothing_is_read_again(reports):
    out = opened(reports["one"], [{"submit": ["#list", {".gc-set": {"dataset": {"account": "1234567890", "name": "A"}, "areas": [{"k": "areas", "value": "12"}]}}]}],
                 responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/geoconf/report", "body": reports["one"]}, {"match": "/geoconf/settings", "body": SETTINGS},
                            {"match": "/geoconf/settings/account", "method": "POST", "status": 400, "body": {"error": "12 is not a place name (use letters, like Quincy, MA)."}}])
    assert out["outs"][0]["[data-out]"] == "12 is not a place name (use letters, like Quincy, MA)." and len([c for c in out["calls"] if "/geoconf/report" in c]) == 1


def test_more_campaigns_are_shown_by_the_button_and_the_rest_are_said_to_be_in_the_csv(reports):
    rep = json.loads(json.dumps(reports["one"]))
    a = rep["accounts"][0]
    f = a["items"][0]["findings"][0]
    a["items"] = [dict(a["items"][0], id=str(i), name="Camp %02d" % i, findings=[dict(f, key="k%d" % i)]) for i in range(23)]
    out = opened(rep)
    assert "show 3 more" in out["el"]["#list"]["html"] and "Camp 19" in out["el"]["#list"]["html"] and "Camp 20" not in out["el"]["#list"]["html"] and "The first 20 of 23" in out["el"]["#list"]["html"]
    more = opened(rep, [{"fire": ["#list", {".cc-more": {"dataset": {"account": "1234567890"}}}]}])
    assert "Camp 22" in more["el"]["#list"]["html"] and "show" not in more["el"]["#list"]["html"].split("Camp 22")[1]


def test_a_failed_read_says_why_and_a_cooldown_is_a_note(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/geoconf/report", "status": 429, "body": {"error": "The platform limits how fast this app can read.", "kind": "quota"}}, {"match": "/geoconf/settings", "body": SETTINGS}],
                "steps": [{"pick": "1234567890"}]})
    assert "limits how fast" in out["el"]["#msg"]["html"] and "this clears on its own" in out["el"]["#msg"]["html"] and out["el"]["#empty"]["hidden"] is False
    cool = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/geoconf/report", "status": 429, "body": {"error": "Refreshed a moment ago.", "kind": "cooldown"}}, {"match": "/geoconf/settings", "body": SETTINGS}],
                 "steps": [{"pick": "1234567890"}]})
    assert 'class="pm-note"' in cool["el"]["#msg"]["html"] and "Refreshed a moment ago." in cool["el"]["#msg"]["html"]
    down = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/geoconf/settings", "body": SETTINGS}], "steps": [{"pick": "1234567890"}], "reject": "/geoconf/report"})
    assert "server could not be reached" in down["el"]["#msg"]["html"]


def test_refresh_reads_again_asking_for_a_fresh_read_and_the_csv_downloads(reports):
    out = opened(reports["one"], [{"click": "#refreshBtn"}, {"click": "#exportBtn"}])
    assert [c for c in out["calls"] if "/geoconf/report" in c] == ["/p2/paid-marketing/api/geoconf/report?account=1234567890", "/p2/paid-marketing/api/geoconf/report?account=1234567890&refresh=1"]
    assert out["downloads"][0]["name"].startswith("locations-Acme-Dental-") and out["downloads"][0]["name"].endswith(".csv")
    assert out["blob"].startswith("﻿Location Conflicts,Acme Dental") and "Ad group and its ad name different places" in out["blob"] and "\"Dover, Quincy\"" in out["blob"]


def test_a_cell_that_starts_like_a_formula_is_quoted_in_the_csv(reports):
    rep = json.loads(json.dumps(reports["one"]))
    rep["accounts"][0]["items"][0]["findings"][0]["detail"] = "=HYPERLINK(\"x\")"
    out = opened(rep, [{"click": "#exportBtn"}])
    assert "\"'=HYPERLINK(\"\"x\"\")\"" in out["blob"]


def test_a_long_list_of_campaigns_is_shown_twenty_at_a_time(reports):
    rep = json.loads(json.dumps(reports["one"]))
    a = rep["accounts"][0]
    f = a["items"][0]["findings"][0]
    a["items"] = [dict(a["items"][0], id=str(i), name="Camp %02d" % i, findings=[dict(f, key="k%d" % i)]) for i in range(45)]
    more = opened(rep, [{"fire": ["#list", {".cc-more": {"dataset": {"account": "1234567890"}}}]}])
    lst = more["el"]["#list"]["html"]
    assert "Camp 39" in lst and "Camp 40" not in lst and "The first 40 of 45" in lst and "show 5 more" in lst


def test_pressing_a_button_on_a_finding_keeps_the_open_rows_open(reports):
    steps = [{"pick": GROUP}, {"fire": ["#list", {".bi-head": {}, ".bi-row": {"dataset": {"id": "1234567890"}}}]}, {"fire": ["#list", {".gc-ign": {"dataset": {"account": "1234567890", "name": "Acme Dental", "word": "Dover"}}}]}]
    out = opened(reports["mcc"], steps, responses=[{"match": "/segments/accounts", "body": ACCTS}, {"match": "/geoconf/report", "body": reports["mcc"]}, {"match": "/geoconf/settings", "body": SETTINGS},
                                                   {"match": "/geoconf/settings/ignore", "method": "POST", "body": {"account": {"areas": [], "ignore": ["Dover"], "acceptPresence": False}}}])
    assert len([c for c in out["calls"] if "/geoconf/report" in c]) == 3 and 'class="bi-row stopped open"' in out["el"]["#list"]["html"]


def test_a_read_that_arrives_after_a_newer_one_is_ignored(reports):
    out = page({"responses": [{"match": "/segments/accounts", "body": ACCTS}, {"match": "/geoconf/report?account=1234567890", "hold": True, "body": reports["one"]},
                              {"match": "/geoconf/report?account=mcc-", "body": reports["mcc"]}, {"match": "/geoconf/settings", "body": SETTINGS}],
                "steps": [{"pick": "1234567890"}, {"pick": GROUP}, {"release": True}]})
    assert "MCC · 2 accounts checked together" in out["el"]["#fresh"]["html"] and "Beta Clinic" not in out["el"]["#list"]["html"] and "1 account, the ones" not in out["el"]["#listNote"]["text"]


def test_the_list_note_counts_accounts_and_a_saved_copy_is_said(reports):
    one = opened(reports["one"])
    assert one["el"]["#listNote"]["text"] == "1 account, the ones to fix first" and "copy saved" not in one["el"]["#fresh"]["html"]
    mcc = opened(reports["mcc"], [{"pick": GROUP}])
    assert mcc["el"]["#listNote"]["text"] == "2 accounts, the ones to fix first"
    stale = json.loads(json.dumps(reports["one"]))
    stale["stale"] = "2026-10-21T09:00:00+00:00"
    assert "The platform is not answering, so this is the copy saved" in opened(stale)["el"]["#fresh"]["html"]
