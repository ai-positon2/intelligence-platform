"""URL Checker: how to put a link right (fixSteps in static/js/url_checker.js, run in Node) and how the table shows it."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "static", "js", "url_checker.js")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def _src():
    src = open(JS, encoding="utf-8").read()
    a = src.index("function fixSteps(f){")
    b = src.index("function ", a + 10)
    return src[a:b]


def steps(**f):
    base = {"platform": "google", "where": "ad", "objectName": "Acme Ad 1", "campaign": "Search Core", "url": "https://acme.test/old", "target": "", "kind": "broken", "status": "open"}
    base.update(f)
    r = subprocess.run(["node", "-e", "%s\nconsole.log(JSON.stringify(fixSteps(%s)));" % (_src(), json.dumps(base))], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_a_google_ad_is_edited_in_ads_and_warns_that_it_goes_back_to_review():
    s = steps()
    assert s[0] == "In Google Ads open Ads, find the ad (Acme Ad 1) in Search Core, click the pencil, and change the Final URL. An edited ad goes back through review for about a day."


def test_where_the_link_is_held_decides_where_to_edit_it():
    assert steps(where="asset group", objectName="Spring")[0] == "In Google Ads open Campaigns, then Search Core, then Asset groups, open the asset group (Spring), and change its Final URL."
    assert "Asset groups, open the asset group (Spring)" in steps(where="asset group", objectName="Spring", campaign="")[0] and "then Asset groups" in steps(where="asset group", campaign="")[0]
    site = steps(where="sitelink (campaign level)", objectName="Contact")[0]
    assert site == "In Google Ads open Ads & assets, then Assets, find the sitelink (Contact), and change its final URL. It is attached at campaign level, so the change applies everywhere it is used."
    assert "attached at a level" in steps(where="callout")[0]
    assert steps(where="tracking template (campaign)")[0] == "In Google Ads open the Settings of the level named in the finding (campaign), then Tracking template, and correct it."
    assert steps(platform="meta", objectName="Social 1")[0] == "In Meta Ads Manager open the ad (Social 1), then Edit, and change the Website URL under Destination."


def test_a_broken_link_is_confirmed_first_and_the_ad_paused_if_the_fix_will_take_a_day():
    s = steps(kind="broken")
    assert "Open https://acme.test/old in a browser first to confirm: some sites refuse automated checks." in s[1] and "pause the ad until it is fixed if that will take more than a day" in s[2] and len(s) == 3


def test_a_redirect_offers_its_target_as_the_new_link_and_another_domain_is_called_out():
    s = steps(kind="redirect", target="https://acme.test/new")
    assert len(s) == 2 and s[1].startswith("The link redirects to https://acme.test/new. Use that address as the ad's link") and "short link or a tracking step" in s[1]
    d = steps(kind="redirect_domain", target="https://new.test/home")
    assert len(d) == 3 and "ends on another domain (https://new.test/home)" in d[1] and "Unknown Domains check to flag the new domain until it is approved" in d[2]
    assert "redirects to another page." in steps(kind="redirect", target="")[1]


def test_no_step_has_a_dash_or_a_stray_mark():
    for kind in ("broken", "redirect", "redirect_domain"):
        for where in ("ad", "asset group", "sitelink (account level)", "tracking template (ad)"):
            for platform in ("google", "meta"):
                for s in steps(kind=kind, where=where, platform=platform, target="https://x.test"):
                    assert chr(0x2014) not in s and chr(0x2013) not in s and "undefined" not in s and "%s" not in s, s


def test_the_table_shows_the_steps_for_an_open_finding_escaped_and_not_for_a_dismissed_one():
    src = open(JS, encoding="utf-8").read()
    start = src.index("return '<tr class=\"ud-'")
    end = src.index("</td></tr>';", start) + len("</td></tr>';")
    code = ("const esc=s=>String(s==null?'':s).replace(/[&<>\"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}[c]));"
            "const KIND_PILL={broken:['Broken','critical']}; const plat=()=>'', ago=()=>'now', alertCell=()=>'', actionCell=()=>'';"
            "%s\nfunction row(f){ var pill=KIND_PILL[f.kind]||[f.kind,''], place=[f.campaign,f.adGroup].filter(Boolean).join(' / '); %s }"
            "const base={kind:'broken',problem:'Page not found (404) <i>',platform:'google',where:'ad',objectName:'A <b>',campaign:'C',url:'https://a.test',accountName:'Acme'};"
            "console.log(JSON.stringify([row(Object.assign({status:'open'},base)), row(Object.assign({},base,{status:'dismissed'}))]));") % (_src(), src[start:end])
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    open_row, dismissed = json.loads(r.stdout)
    assert '<details class="pd-how"><summary>How to fix</summary><ol><li>' in open_row and "Page not found (404) &lt;i&gt;" in open_row and "(A &lt;b&gt;)" in open_row
    assert "<details" not in dismissed and "Page not found (404) &lt;i&gt;" in dismissed
