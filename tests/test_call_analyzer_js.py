"""Call Transcript Analyzer: static/js/cr_data.js run in Node, and the page script checked against its template.

The page script only draws; how a rate is written, which location's missed calls stand out, how this range compares with the last,
what the sample covered and the CSV come from cr_data.js, so a wrong figure about a client's calls would come from here. The file is
executed, not read. The page script is syntax-checked, every element it reaches for must exist in the template, and nothing it
draws may be a caller's name, number, summary or transcript.
"""
import json
import os
import re
import shutil
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "static", "js", "cr_data.js")
PAGE = os.path.join(ROOT, "static", "js", "call_analyzer.js")
TEMPLATE = os.path.join(ROOT, "templates", "call_analyzer.html")
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")


def run(expr, **data):
    pre = "".join("const %s=%s;" % (k, json.dumps(v)) for k, v in data.items())
    code = "const D=require(%s);%sconst out=(%s);console.log(JSON.stringify(out===undefined?null:out));" % (json.dumps(DATA), pre, expr)
    r = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def loc(key, calls, missed, leads):
    return {"key": key, "calls": calls, "missed": missed, "leads": leads, "missRate": missed / calls if calls else None, "leadRate": leads / calls if calls else None}


def test_numbers_rates_and_lengths_are_written_the_same_way_everywhere():
    assert run("[D.num(45230), D.num(null), D.num(0), D.num(1234.6)]") == ["45,230", "–", "0", "1,235"]
    assert run("[D.pct(0.0602), D.pct(0.5), D.pct(0.075), D.pct(null), D.pct(1), D.pct(0.0602, 2)]") == ["6.0%", "50%", "7.5%", "–", "100%", "6.02%"]
    assert run("[D.dur(188), D.dur(65), D.dur(9), D.dur(null), D.dur(3600)]") == ["3:08", "1:05", "0:09", "–", "60:00"]


def test_change_and_signed_percent():
    assert run("[D.change(150,100), D.change(5,0), D.change(5,null), D.change(null,5)]") == [0.5, None, None, None]
    assert run("[D.change(5,0)===null, D.change(0,5)]") == [True, -1]                  # not Infinity, which JSON would hide
    assert run("[D.signed(0.254), D.signed(-0.04), D.signed(0), D.signed(-0.0004), D.signed(0.054), D.signed(null)]") == ["+25%", "-4%", "0%", "0%", "+5.4%", "–"]


def test_a_location_whose_missed_calls_stand_out_is_flagged_but_a_small_one_is_not():
    total = loc("all", 1000, 60, 80)                                                  # 6% missed, 8% leads
    rows = [loc("big miss", 200, 30, 20),        # 15%: stands out
            loc("exactly 1.5x", 200, 18, 20),    # 9%: at the line
            loc("just under", 200, 17, 20),      # 8.5%
            loc("few calls", 20, 10, 2),         # 50% but only 20 calls
            loc("few misses", 100, 4, 8),        # not enough misses
            loc("tiny", 30, 3, 2),               # 10% missed, over the line, but only 3 misses
            loc("fine", 300, 12, 40)]
    f = run("D.flags(rows, total)", rows=rows, total=total)
    assert f["big miss"] == "miss" and f["exactly 1.5x"] == "miss"
    assert "just under" not in f and "few calls" not in f and "few misses" not in f and "fine" not in f and "tiny" not in f


def test_a_location_with_far_fewer_leads_than_the_account_is_flagged_on_enough_calls():
    total = loc("all", 1000, 60, 80)
    f = run("D.flags(rows, total)", rows=[loc("quiet", 100, 3, 1), loc("small", 30, 1, 0), loc("ok", 100, 3, 6)], total=total)
    assert f == {"quiet": "lowLeads"}


def test_flags_without_data_are_empty():
    assert run("D.flags(null, null)") == {} and run("D.flags([], {})") == {}
    assert run("D.flags(rows, total)", rows=[loc("x", 100, 20, 0)], total={"missRate": None, "leadRate": None}) == {}


def test_what_the_sample_covered_is_said_in_words_and_a_thin_sample_is_marked():
    assert run('D.coverage({sampled: 462, read: 82, matching: 4781, minSeconds: 20})') == "Read 82 calls that had a transcript, from a sample of 462 spread across the 4,781 calls of 20 seconds or more in the range."
    assert run('D.coverage({sampled: 422, read: 64, matching: 4744, minSeconds: 20, source: "Google Ads"})') == "Read 64 calls that had a transcript, from a sample of 422 spread across the 4,744 calls of 20 seconds or more that CallRail\'s search finds for this source (a similarly named source is left out of the read)."
    assert "None of the 300 calls sampled" in run('D.coverage({sampled: 300, read: 0, matching: 3000})')
    assert run("D.coverage(null)") == "" and run("[D.thin({read: 29}), D.thin({read: 30}), D.thin({read: 0}), D.thin(null)]") == [True, False, False, False]


def test_every_intent_and_reason_the_server_can_send_has_a_colour():
    for k in ("new_patient", "emergency", "service", "appointment", "insurance", "records", "not_a_patient", "no_conversation", "other"):
        assert re.match(r"^#[0-9a-f]{6}$", run("D.INTENT_COLOR[k]", k=k)), k
    for k in ("no_availability", "insurance", "transfer", "callback", "price", "other"):
        assert re.match(r"^#[0-9a-f]{6}$", run("D.REASON_COLOR[k]", k=k)), k


def test_the_csv_has_each_table_then_the_reading_and_quotes_what_needs_quoting():
    ov = {"sources": [dict(loc("Google Ads", 100, 5, 10), answered=95, firstTime=30, avgDuration=120)],
          "campaigns": [dict(loc('brand, "core"', 40, 2, 8), answered=38, firstTime=10, avgDuration=99)], "keywords": [], "locations": None}
    an = {"intents": [{"label": "New patient", "n": 5, "booked": 2, "notBooked": 1, "lost": 3}], "topics": [{"label": "Price or cost", "n": 4, "share": 0.1234}]}
    out = run("D.csv(ov, an)", ov=ov, an=an).split("\r\n")
    assert out[0] == "Sources" and out[1].startswith("Name,Calls,Answered,Missed,Missed rate") and out[2] == "Google Ads,100,95,5,5%,30,10,10%,120"
    assert 'brand, ""core""' in run("D.csv(ov, null)", ov=ov)
    text = "\r\n".join(out)
    assert "Locations" not in text and "Keywords" not in text and "Campaigns" in text
    assert "New patient,5,2,1,3" in out and "Price or cost,4,12.34%" in out
    assert run("D.csv(null, null)") == ""


def test_the_page_script_is_valid_javascript():
    for f in (PAGE, DATA):
        r = subprocess.run(["node", "--check", f], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr


def test_every_element_the_page_script_reaches_for_is_in_the_template():
    js, html = open(PAGE).read(), open(TEMPLATE).read()
    made = set(re.findall(r'id="([A-Za-z0-9_-]+)"', js))
    wanted = set(re.findall(r'\$\("#([A-Za-z0-9_-]+)"\)', js)) | set(re.findall(r"\$\$\(\"#([A-Za-z0-9_-]+)", js))
    in_html = set(re.findall(r'id="([A-Za-z0-9_-]+)"', html))
    assert not (wanted - in_html - made - {"psToast"}), wanted - in_html - made


def test_the_page_draws_only_the_fields_the_server_is_allowed_to_send():
    """The server sends no summary, transcript, name or number; the script must not go looking for them."""
    js = open(PAGE).read()
    assert not re.findall(r"[.\[\"'](summary|transcript|transcription|customer\w*|phone\w*|callerName|caller_name)\b", re.sub(r"/\*.*?\*/", "", js, flags=re.S))


def test_the_page_loads_its_scripts_in_order_with_versions_and_says_it_is_private():
    html = open(TEMPLATE).read()
    order = [html.index(n) for n in ("chart.umd.js", "ps_data.js", "cr_data.js", "call_analyzer.js")]
    assert order == sorted(order)
    assert "cr_data.js') }}?v=" in html and "call_analyzer.js') }}?v=" in html and "call_analyzer.css') }}?v=" in html
    assert "HIPAA" in html and "no caller name" in html


def test_no_page_copy_uses_an_em_dash():
    for f in (PAGE, DATA, TEMPLATE):
        assert "—" not in open(f).read(), f
