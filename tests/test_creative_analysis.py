"""Creative Analysis: the Google Ads read, the report for either platform, the background read, the routes, the page's judging (in Node) and where the agent is listed.
No network."""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module  # noqa: E402
from tracker import paid_creative_roas as ca  # noqa: E402
from tracker import paid_creative_roas_google as goog  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_geo  # noqa: E402

STAFF = "ana@position2.com"
PAGE = "/p2/paid-marketing/creative-analysis"
API = "/p2/paid-marketing/api/creative-analysis/data"
TODAY = dt.date(2026, 11, 26)
ACCT = {"id": "1234567890", "name": "Acme Skin", "currency": "USD", "tz": "America/New_York"}


def client_as(email):
    c = app_module.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


def ad_row(day, ad_id, cost, conv=1.0, value=50.0, *, ad_status="ENABLED", camp_status="ENABLED", camp="Search Brand", ad_type="RESPONSIVE_SEARCH_AD", ad=None):
    a = {"id": ad_id, "type": ad_type, "name": "", "finalUrls": ["https://acme.example/kit"],
         "responsiveSearchAd": {"headlines": [{"text": "Second"}, {"text": "Acme  Skin Care", "pinnedField": "HEADLINE_1"}], "descriptions": [{"text": "Calm skin."}]}}
    a.update(ad or {})
    return {"segments": {"date": day}, "campaign": {"id": "9", "name": camp, "status": camp_status}, "adGroup": {"id": "7", "name": "Brand", "status": "ENABLED"},
            "adGroupAd": {"status": ad_status, "ad": a},
            "metrics": {"costMicros": str(int(cost * 1e6)), "impressions": "1000", "clicks": "40", "conversions": conv, "conversionsValue": value}}


def searcher(ads=None, groups=None, assets=None, pics=None, fail=()):
    calls = []

    def search(customer, q):
        calls.append(q)
        if "FROM asset_group_asset" in q:
            return assets or []
        if "FROM asset_group " in q:
            if "pmax" in fail:
                raise base.AdsError("Google Ads answered 400: no")
            return groups or []
        if "FROM asset " in q:
            return pics or []
        raise AssertionError(q)

    def pruned(customer, build, optional, bad):
        q = build([f for f in optional if f not in bad])
        calls.append(q)
        return ads or [], optional
    return search, pruned, calls


def test_each_google_ad_and_asset_group_is_one_creative_with_its_days_its_status_and_what_it_shows():
    ads = [ad_row("2026-11-24", "11", 20), ad_row("2026-11-25", "11", 30, value=0),
           ad_row("2026-11-25", "12", 15, ad_status="PAUSED"), ad_row("2026-11-25", "13", 10, camp_status="PAUSED", camp="Old"),
           ad_row("2026-11-25", "14", 5, ad_type="DEMAND_GEN_VIDEO_RESPONSIVE_AD",
                  ad={"responsiveSearchAd": None, "demandGenVideoResponsiveAd": {"videos": [{"asset": "customers/1/assets/55"}], "headlines": [{"text": "Watch this"}]}})]
    ads[-1]["metrics"].update({"videoTrueviewViews": "200", "videoQuartileP100Rate": 0.05})
    groups = [{"segments": {"date": "2026-11-25"}, "campaign": {"id": "8", "name": "PMax", "status": "ENABLED"}, "assetGroup": {"id": "3", "name": "Kits", "status": "ENABLED"},
               "metrics": {"costMicros": "40000000", "impressions": "900", "clicks": "9", "conversions": 2, "conversionsValue": 120}}]
    assets = [{"assetGroup": {"id": "3"}, "assetGroupAsset": {"fieldType": "HEADLINE"}, "asset": {"textAsset": {"text": "Kit  sale"}}},
              {"assetGroup": {"id": "3"}, "assetGroupAsset": {"fieldType": "MARKETING_IMAGE"}, "asset": {"resourceName": "customers/1/assets/66"}}]
    pics = [{"asset": {"resourceName": "customers/1/assets/55", "youtubeVideoAsset": {"youtubeVideoId": "abc"}}},
            {"asset": {"resourceName": "customers/1/assets/66", "imageAsset": {"fullSize": {"url": "https://img.example/66.jpg"}}}}]
    search, pruned, calls = searcher(ads, groups, assets, pics)
    raw = goog.read_account("1234567890", dt.date(2026, 8, 29), TODAY, search_fn=search, search_pruned_fn=pruned)
    a = raw["ads"]
    assert set(a) == {"11", "12", "13", "14", "pmax-3"} and raw["warnings"] == [] and "BETWEEN '2026-08-29' AND '2026-11-26'" in calls[0]
    assert a["11"]["status"] == "ACTIVE" and a["12"]["status"] == "PAUSED" and a["13"]["status"] == "PAUSED" and a["13"]["campaign_status"] == "PAUSED"
    assert a["11"]["hook"] == "Acme Skin Care" and a["11"]["format"] == "Search (responsive)" and a["11"]["landing"] == "https://acme.example/kit" and a["11"]["name"] == "Brand"
    assert a["14"]["video_id"] == "abc" and a["14"]["thumb"].endswith("/abc/mqdefault.jpg") and a["14"]["format"] == "Demand Gen video" and "_video" not in a["14"]
    assert a["pmax-3"]["hook"] == "Kit sale" and a["pmax-3"]["thumb"] == "https://img.example/66.jpg" and a["pmax-3"]["format"] == "Performance Max"
    r = [x for x in raw["rows"] if x["ad_id"] == "11"]
    assert [x["spend"] for x in r] == [20.0, 30.0] and r[0]["purch"] == 1.0 == r[0]["leads"] and r[1]["rev"] == 0.0
    v = [x for x in raw["rows"] if x["ad_id"] == "14"][0]
    assert v["v3"] == 200.0 and v["thru"] == 50.0


def test_performance_max_or_the_pictures_failing_is_said_and_the_ads_still_show():
    search, pruned, _ = searcher([ad_row("2026-11-25", "11", 20)], fail=("pmax",))
    raw = goog.read_account("1234567890", dt.date(2026, 8, 29), TODAY, search_fn=search, search_pruned_fn=pruned)
    assert set(raw["ads"]) == {"11"} and "Performance Max could not be read" in raw["warnings"][0]


def test_the_report_reads_a_google_account_up_to_today_in_its_time_zone_and_a_whole_mcc_is_refused():
    paid_geo.clear_cache()
    search, pruned, calls = searcher([ad_row("2026-11-25", "11", 20), ad_row("2026-11-25", "13", 10, camp_status="PAUSED", camp="Old")])
    rep = ca.report_for("1234567890", google_accounts_fn=lambda: [ACCT], search_fn=search, search_pruned_fn=pruned, today=TODAY)
    assert rep["platform"] == "google" and rep["account"] == dict(ACCT, platform="google") and rep["today"] == "2026-11-26" and rep["until"] == "2026-11-26"
    assert rep["tooEarlyX"] == 1 and ca.TOO_EARLY_X == {"act_261215873024315": 1.5, "9557969390": 1.5}     # 1.5 × CPA is Soteri's alone
    assert rep["since"] == "2026-08-29" and len(rep["ads"]) == 2 and rep["rowFields"][-1] == "leads" and "landing" in rep["ads"][0]
    with pytest.raises(ca.BadRequest):
        ca.report_for("mcc-1234567890", google_accounts_fn=lambda: [ACCT], today=TODAY)
    with pytest.raises(ca.BadRequest):
        ca.report_for("", today=TODAY)
    paid_geo.clear_cache()


def test_a_google_failure_is_the_platforms_google_error_not_metas():
    paid_geo.clear_cache()

    def pruned(*a):
        raise base.AdsError("Google Ads answered 403: no access")
    with pytest.raises(gads.GadsError):
        ca.report_for("1234567891", google_accounts_fn=lambda: [dict(ACCT, id="1234567891")], search_fn=lambda c, q: [], search_pruned_fn=pruned, today=TODAY)
    paid_geo.clear_cache()


def test_today_is_counted_in_the_accounts_time_zone():
    assert isinstance(ca.local_today("America/New_York"), dt.date) and ca.local_today("Nowhere/Nope") == dt.date.today() and ca.local_today("") == dt.date.today()


def test_a_long_read_answers_loading_and_the_next_ask_gets_the_finished_report(monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(ca, "report_for", lambda acct, force=False, **kw: gate.wait(5) and {"account": {"id": acct}, "force": force})
    first = ca.report_or_loading("act_9", wait=0.05)
    assert first["loading"] is True and first["account"] == "act_9" and first["started"]
    assert ca.report_or_loading("act_9", wait=0.05)["loading"] is True                     # still the same read, not a second one
    gate.set()
    done = ca.report_or_loading("act_9", wait=2)
    assert done == {"account": {"id": "act_9"}, "force": False} and "act_9" not in ca._JOBS


def test_the_page_and_its_data_are_for_signed_in_staff_only_and_the_route_passes_what_the_page_sent(monkeypatch):
    html = client_as(STAFF).get(PAGE).get_data(as_text=True)
    assert "Creative Analysis" in html and 'id="acctPick"' in html and html.index("creative_analysis_data.js") < html.index("js/creative_analysis.js") and "css/creative_analysis.css" in html
    assert client_as(None).get(PAGE).status_code == 302 and client_as("someone@gmail.com").get(API + "?account=1234567890").status_code == 302
    seen = []
    monkeypatch.setattr(ca, "report_or_loading", lambda *x, **kw: seen.append((x, kw)) or {"ads": []})
    r = client_as(STAFF).get(API + "?account=1234567890&refresh=1")
    assert r.status_code == 200 and r.get_json() == {"ads": []} and seen[0] == (("1234567890",), {"force": True})
    monkeypatch.setattr(ca, "report_or_loading", lambda *x, **kw: {"loading": True, "started": "x"})
    assert client_as(STAFF).get(API + "?account=act_1").status_code == 202
    monkeypatch.setattr(ca, "report_or_loading", lambda *x, **kw: (_ for _ in ()).throw(ca.BadRequest("Pick one")))
    r = client_as(STAFF).get(API + "?account=mcc-1")
    assert r.status_code == 400 and r.get_json()["error"] == "Pick one"


NODE = """
const CA = require(process.argv[1]);
const D = {since:"2026-08-29", until:"2026-11-26", today:"2026-11-26", platform:"google", account:{tz:"America/New_York"},
  ads:[{id:"1", name:"A", status:"ACTIVE", campaign:"On", campaign_status:"ACTIVE", format:"Search (responsive)"},
       {id:"2", name:"B", status:"ACTIVE", campaign:"On", campaign_status:"ACTIVE", format:"Search (responsive)"},
       {id:"3", name:"C", status:"PAUSED", campaign:"Off", campaign_status:"PAUSED", format:"Shopping"}],
  rows:[["2026-11-25",0,100,1000,50,4,400,0,0,0,4], ["2026-11-25",1,100,1000,50,1,50,0,0,0,1], ["2026-11-25",2,50,500,10,1,50,0,0,0,1],
        ["2026-11-26",0,999,1,1,0,0,0,0,0,0]]};
const r = CA.presetRange(D, 30);
const C = CA.compute(D, {since:r[0], until:r[1], goal:"roas"});
const list = CA.creatives(C, "ad");
const C15 = CA.compute(Object.assign({}, D, {tooEarlyX:1.5}), {since:r[0], until:r[1], goal:"roas"});
console.log(JSON.stringify({range:r, spend:C.total.spend, paused:C.total.paused.spend, autoMin:C.rules.autoMin, autoMin15:C15.rules.autoMin, avg:+C.rules.avg.toFixed(3),
  verdicts:list.map(t => t.ad.id + ":" + t.verdict), goal:CA.defaultGoal(D), kind:CA.kindOf(D.ads[2], true), metaKind:CA.kindOf({format:"Flexible (videos)"}, false)}));
"""


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_page_counts_paused_campaigns_ends_on_yesterday_and_waits_for_one_cost_per_purchase_or_what_the_client_asked():
    out = subprocess.run(["node", "-e", NODE, os.path.join(ROOT, "static", "js", "creative_analysis_data.js")], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    j = json.loads(out.stdout)
    assert j["range"] == ["2026-10-27", "2026-11-25"]                                    # Last 30 days ends yesterday, like Google Ads
    assert j["spend"] == 250 and j["paused"] == 50                                       # the paused campaign counts in the total; today's 999 does not
    assert j["avg"] == 2.0 and j["autoMin"] == 42 and j["autoMin15"] == 63               # one $41.67 cost per purchase; 1.5 × it for Soteri
    assert j["verdicts"] == ["1:Scale", "2:Cut"] and j["goal"] == "roas" and j["kind"] == "Shopping" and j["metaKind"] == "Video"


def test_the_agent_is_on_the_paid_marketing_page_the_palettes_the_hub_counts_the_catalog_and_what_the_assistant_knows():
    from tests import hidden_paid
    from tracker import vimi_catalog
    paid = client_as(STAFF).get("/p2/paid-marketing").get_data(as_text=True)
    assert 'href="/p2/paid-marketing/creative-analysis"' in paid and "c-cra" in paid and "u:'/p2/paid-marketing/creative-analysis'" in paid
    kpal = open(os.path.join(ROOT, "templates", "_paid_kpal.html"), encoding="utf-8").read()
    assert "u:'/p2/paid-marketing/creative-analysis'" in kpal
    css = open(os.path.join(ROOT, "static", "css", "paid_marketing.css"), encoding="utf-8").read()
    assert ".c-cra{" in css
    cards = len(re.findall(r'class="dash-card', paid))
    hub = re.sub(r"<!--.*?-->", "", hidden_paid.rendered("/p2/hub"), flags=re.S)
    assert ("<span>%d</span> dashboards" % cards) in hub and ("<span>%d</span> live" % cards) in hub
    kn = app_module._VIMI_PLATFORM_KNOWLEDGE
    mine = kn.split("Creative Analysis (", 1)[1].split("\n", 1)[0]
    assert "Creative Analysis (/p2/paid-marketing/creative-analysis)" in kn and "Soteri Skin's accounts wait for 1.5 times" in mine and "—" not in mine and "–" not in mine
    card = paid.split('href="/p2/paid-marketing/creative-analysis" class="dash-card', 1)[1].split("</a>", 1)[0]
    assert "—" not in card and "–" not in card
    a = vimi_catalog.BY_ID["creative-analysis"]
    assert a["page"] == PAGE and any(r["path"] == API for r in a["reads"])
