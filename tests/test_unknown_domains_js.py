"""Unknown Domains page: the Platform switch narrows the findings, links and domains in use to one platform and works the counts out again (the functions are run in Node)."""
import json
import os
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "static", "js", "unknown_domains.js")
TEMPLATE = os.path.join(ROOT, "templates", "unknown_domains.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def _function(src, name):
    start = src.index("function %s(" % name)
    depth, i = 0, src.index("{", start)
    while True:
        depth += {"{": 1, "}": -1}.get(src[i], 0)
        if depth == 0:
            return src[start:i + 1]
        i += 1


def view(plat, data):
    src = open(PAGE, encoding="utf-8").read()
    prelude = "".join(_function(src, n) + "\n" for n in ("countBy", "viewOf"))
    r = subprocess.run(["node", "-e", "%sconst D=%s;console.log(JSON.stringify(viewOf(D,%s)))" % (prelude, json.dumps(data), json.dumps(plat))], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def f(fp, platform, status="open"):
    return {"fingerprint": fp, "platform": platform, "status": status, "accountId": "x"}


DATA = {"findings": [f("1", "google"), f("2", "google", "approved"), f("3", "meta"), f("4", "meta", "dismissed"), f("5", "meta", "approved")],
        "links": [dict(f("a", "google"), status="open"), dict(f("b", "meta"), status="open"), dict(f("c", "meta"), status="fixed")],
        "inUse": [{"accountId": "1", "name": "G", "platform": "google", "domains": []}, {"accountId": "act_2", "name": "M", "platform": "meta", "domains": []}],
        "counts": {"approved": 2, "dismissed": 1, "gone": 0, "open": 2}, "linkCounts": {"dismissed": 0, "fixed": 1, "open": 2}, "known": [{"domain": "a.com"}], "runs": [{"read": 9}], "accounts": [{"id": "1"}, {"id": "act_2"}]}


def test_both_is_what_the_server_sent():
    assert view("both", DATA) == DATA


def test_meta_keeps_only_meta_findings_links_and_accounts_and_counts_them_again():
    out = view("meta", DATA)
    assert [x["fingerprint"] for x in out["findings"]] == ["3", "4", "5"] and [x["fingerprint"] for x in out["links"]] == ["b", "c"] and [x["name"] for x in out["inUse"]] == ["M"]
    assert out["counts"] == {"approved": 1, "dismissed": 1, "gone": 0, "open": 1} and out["linkCounts"] == {"dismissed": 0, "fixed": 1, "open": 1}


def test_google_keeps_the_rest_and_a_row_with_no_platform_field_is_google():
    out = view("google", dict(DATA, findings=DATA["findings"] + [{"fingerprint": "6", "status": "open"}]))
    assert [x["fingerprint"] for x in out["findings"]] == ["1", "2", "6"] and out["counts"]["open"] == 2 and out["counts"]["approved"] == 1


def test_what_is_not_about_a_platform_is_not_narrowed():
    out = view("meta", DATA)
    assert out["known"] == DATA["known"] and out["runs"] == DATA["runs"] and out["accounts"] == DATA["accounts"]


def test_the_page_has_the_switch_and_its_note():
    page, tpl = open(PAGE, encoding="utf-8").read(), open(TEMPLATE, encoding="utf-8").read()
    assert "PMPlatform.switcher" in page and 'id="platSw"' in tpl and 'id="platNote"' in tpl and tpl.index("pm_platform.js") < tpl.index("unknown_domains.js")
    assert "S.raw=d;S.data=viewOf(d,S.plat)" in page and "—" not in page
