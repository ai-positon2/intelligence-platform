"""The Vimi widget's company list and badge come from the signal database, never from the OpenAI insights route it used to call on every page view."""
import json
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone

import pytest

import app as appmod

ROOT = appmod.Path(appmod.__file__).parent
WIDGET = (ROOT / "templates" / "ppc_chat_widget.html").read_text(encoding="utf-8")


def client(email="ana@position2.com"):
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["google_user"] = {"email": email, "name": "A"}
    return c


def db(tmp_path, rows):
    path = tmp_path / "t.db"
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE companies (apollo_id TEXT, name TEXT)")
    con.execute("CREATE TABLE alerts_sent (id INTEGER, apollo_id TEXT, signal_type TEXT, signal_detail TEXT, severity TEXT, sent_at TEXT, signal_date TEXT, source_url TEXT, dry_run INTEGER)")
    for i, (aid, name, sev, ago_days, dry) in enumerate(rows):
        con.execute("INSERT OR IGNORE INTO companies VALUES (?, ?)", (aid, name))
        con.execute("INSERT INTO alerts_sent VALUES (?, ?, 'Funding Round', '', ?, ?, 'Unknown', '', ?)",
                    (i, aid, sev, (datetime.now(timezone.utc) - timedelta(days=ago_days)).isoformat(), dry))
    con.commit()
    con.close()
    return path


def test_the_list_puts_the_most_high_signals_first_and_the_badge_counts_fresh_high_ones(tmp_path, monkeypatch):
    path = db(tmp_path, [("1", "Acme", "HIGH", 1, 0), ("1", "Acme", "HIGH", 30, 0), ("2", "Beta", "HIGH", 2, 0), ("3", "Gamma", "LOW", 1, 0),
                         ("4", "Delta", "HIGH", 20, 0), ("5", "Dry", "HIGH", 1, 1)])
    monkeypatch.setitem(appmod._VIMI_SIGNAL_DBS, "healthcare", path)
    d = client().get("/api/vimi/companies/healthcare").get_json()
    assert d["companies"][0] == "Acme" and set(d["companies"]) == {"Acme", "Beta", "Gamma", "Delta"}      # a dry run is not a signal
    assert d["fresh"] == 2                                                                                  # Acme and Beta: HIGH within 7 days


def test_the_real_databases_answer_quickly_with_names_only():
    for acct in ("healthcare", "csg"):
        r = client().get("/api/vimi/companies/" + acct)
        d = r.get_json()
        assert r.status_code == 200 and isinstance(d["fresh"], int) and len(d["companies"]) <= 60
        assert all(isinstance(n, str) and n for n in d["companies"])


def test_an_unknown_account_and_someone_outside_position2_are_refused():
    assert client().get("/api/vimi/companies/nope").status_code == 404
    assert client("x@gmail.com").get("/api/vimi/companies/healthcare").status_code == 302


def test_no_model_is_called(monkeypatch):
    monkeypatch.setattr(appmod, "_vimi_chat_json", lambda *a, **k: pytest.fail("a model was called"))
    assert client().get("/api/vimi/companies/healthcare").status_code == 200


def test_the_widget_never_calls_the_insights_route():
    assert not re.search(r"fetch\(\s*['\"]/api/insights", WIDGET)


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_widget_asks_once_per_page_and_fills_the_list_and_the_badge():
    def fn(name):
        start = WIDGET.index("function %s(" % name)
        depth, i = 0, WIDGET.index("{", start)
        while True:
            depth += {"{": 1, "}": -1}.get(WIDGET[i], 0)
            if depth == 0:
                return WIDGET[start:i + 1]
            i += 1
    script = """
    var _acct='all', _companies=['seed'], _coCache={}, calls=[], badge={textContent:'',classList:{add:function(c){this.c=c;}}};
    var LS={nud:'n'}, saved={}; function load(k,d){return d;} function save(k,v){saved=v;}
    var document={getElementById:function(){return badge;}};
    var fetch=function(u){ calls.push(u); return Promise.resolve({json:function(){return Promise.resolve({companies:['Acme','Beta',''],fresh:3});}}); };
    %s %s %s
    loadCompanies(); checkNudge();
    setTimeout(function(){ process.stdout.write(JSON.stringify({calls:calls, companies:_companies, badge:badge.textContent})); }, 20);
    """ % (fn("companiesOf"), fn("loadCompanies"), fn("checkNudge"))
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out == {"calls": ["/api/vimi/companies/healthcare"], "companies": ["Acme", "Beta"], "badge": 3}
