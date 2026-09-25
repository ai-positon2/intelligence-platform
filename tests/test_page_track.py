"""Signed-in page-view tracking (static/js/page_track.js -> /api/track ->
"Page Views" tab). The script is executed in Node with a controllable clock,
and the server/readers are driven through the real app code."""
import glob
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import app as appmod  # noqa: E402

_SCRIPT = os.path.join(_ROOT, "static", "js", "page_track.js")

_HARNESS = r"""
const vm = require("vm"), fs = require("fs");
const src = fs.readFileSync(process.argv[1], "utf8");
const cfg = JSON.parse(process.argv[2]);
let clock = 1000000;
class FakeDate { static now() { return clock; } }
const winL = {}, docL = {};
const on = (bag) => (t, fn) => { (bag[t] = bag[t] || []).push(fn); };
const sent = [];
const document = {
  addEventListener: on(docL), title: "Doc Title", visibilityState: "visible",
  body: { dataset: { email: "body@x.com" } },
  currentScript: { getAttribute: (k) => (k in cfg.attrs ? cfg.attrs[k] : null) },
};
const sandbox = {
  document, location: { pathname: "/p2/hub" }, crypto, Date: FakeDate, Math, JSON,
  fetch: (u, o) => { sent.push(Object.assign({url: u}, JSON.parse(o.body))); return Promise.resolve(); },
  addEventListener: on(winL),
};
sandbox.window = sandbox;
vm.runInNewContext(src, sandbox);
const fire = (bag, t) => (bag[t] || []).forEach((f) => f({}));
for (const [op, arg] of cfg.steps) {
  if (op === "wait") clock += arg * 1000;
  else if (op === "hide") { document.visibilityState = "hidden"; fire(docL, "visibilitychange"); }
  else if (op === "show") { document.visibilityState = "visible"; fire(docL, "visibilitychange"); }
  else if (op === "pagehide") fire(winL, "pagehide");
}
process.stdout.write(JSON.stringify(sent));
"""

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _run(steps, attrs=None):
    cfg = {"steps": steps, "attrs": attrs if attrs is not None else {"data-title": "Hub", "data-email": "k@position2.com"}}
    out = subprocess.run(["node", "-e", _HARNESS, _SCRIPT, json.dumps(cfg)],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_time_after_tabbing_away_and_back_is_reported_and_background_time_is_not():
    sent = _run([["wait", 5], ["hide", None], ["wait", 3600], ["show", None], ["wait", 3], ["pagehide", None]])
    assert [b["seconds"] for b in sent] == [5, 8]
    assert [b["seq"] for b in sent] == [1, 2]
    assert len({b["pvid"] for b in sent}) == 1
    assert all(b["url"] == "/api/track" and b["title"] == "Hub" and b["email"] == "k@position2.com" for b in sent)


@needs_node
def test_hidden_then_pagehide_on_navigation_sends_one_snapshot():
    assert len(_run([["wait", 4], ["hide", None], ["pagehide", None]])) == 1


@needs_node
def test_without_data_attributes_it_reports_the_document_title_and_body_email():
    [b] = _run([["wait", 2], ["pagehide", None]], attrs={})
    assert (b["title"], b["email"]) == ("Doc Title", "body@x.com")


@needs_node
def test_an_empty_data_email_is_sent_as_empty_not_replaced():
    [b] = _run([["wait", 2], ["pagehide", None]], attrs={"data-title": "Anonymous Traffic", "data-email": ""})
    assert b["email"] == ""


# ── templates ───────────────────────────────────────────────────────────────

def _templates():
    return {os.path.basename(f): open(f, encoding="utf-8").read()
            for f in glob.glob(os.path.join(_ROOT, "templates", "*.html"))}


def test_no_template_carries_its_own_copy_of_the_page_tracker():
    offenders = [n for n, s in _templates().items() if "/api/track" in s]
    assert not offenders, offenders


def test_every_previously_tracked_page_loads_the_shared_tracker():
    tracked = [n for n, s in _templates().items() if "js/page_track.js" in s]
    assert len(tracked) == 23, sorted(tracked)


def test_hub_renders_the_tracker_with_this_pages_title_and_the_users_email():
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["google_user"] = {"email": "k@position2.com", "name": "K"}
    html = c.get("/p2/hub").get_data(as_text=True)
    tag = re.search(r'<script src="[^"]*page_track\.js[^"]*"[^>]*>', html).group(0)
    assert 'data-title="Hub"' in tag and 'data-email="k@position2.com"' in tag


# ── /api/track and the readers ──────────────────────────────────────────────

class _Exec:
    def __init__(self, v):
        self._v = v

    def execute(self):
        return self._v


class _Sheets:
    def __init__(self, tabs):
        self.tabs, self.appends, self.updates = tabs, [], []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId=None, range=None):
        tab = range.split("!")[0] if "!" in range else "Login Log"
        rows = [list(r) for r in self.tabs.get(tab, [])]
        return _Exec({"values": rows[:1] if range.endswith("1") and ":" in range and range.split(":")[-1][-1] == "1" else rows})

    def append(self, spreadsheetId=None, range=None, valueInputOption=None, insertDataOption=None, body=None):
        self.appends.append((range.split("!")[0], body["values"][0]))
        return _Exec({})

    def update(self, **kw):
        self.updates.append(kw)
        return _Exec({})

    def batchUpdate(self, spreadsheetId=None, body=None):
        raise RuntimeError("exists")


def _patch_build(monkeypatch, svc):
    import googleapiclient.discovery as _disc
    import google.oauth2.service_account as _sa
    monkeypatch.setattr(appmod, "LOGIN_LOG_SHEET_ID", "fake")
    monkeypatch.setenv("GOOGLE_SA_JSON", '{"type": "service_account"}')
    monkeypatch.setattr(_disc, "build", lambda *a, **k: svc)

    class _Creds:
        @staticmethod
        def from_service_account_info(*a, **k):
            return object()
    monkeypatch.setattr(_sa, "Credentials", _Creds)


def test_track_stores_page_view_id_and_seq_and_labels_the_new_columns(monkeypatch):
    svc = _Sheets({"Page Views": [appmod._PV_HEADER[:14]]})
    _patch_build(monkeypatch, svc)
    appmod.app.test_client().post("/api/track", json={
        "page": "/p2/hub", "title": "Hub", "seconds": 8, "email": "k@position2.com",
        "pvid": "0f3c2a9e-1111-4222-8333-444455556666", "seq": 2})
    row = svc.appends[-1][1]
    assert row[14:] == ["0f3c2a9e-1111-4222-8333-444455556666", 2]
    assert svc.updates and svc.updates[0]["body"]["values"][0] == appmod._PV_HEADER


def test_track_ignores_a_malformed_seconds_or_page_view_id(monkeypatch):
    svc = _Sheets({"Page Views": [appmod._PV_HEADER]})
    _patch_build(monkeypatch, svc)
    r = appmod.app.test_client().post("/api/track", json={"page": "/", "seconds": "abc", "pvid": "=cmd()"})
    assert r.status_code == 200 and svc.appends == []
    appmod.app.test_client().post("/api/track", json={"page": "/", "seconds": 3, "pvid": "=cmd()"})
    assert svc.appends[-1][1][14] == ""


def _pv(ts, email, secs, pvid="", seq=""):
    return [ts, ts[:10], ts[11:19], "Mon", email, "Hub", "/p2/hub", str(secs), "", "", "Chrome",
            "macOS", "Desktop", "", pvid, str(seq)]


def test_usage_counts_one_page_view_per_id_with_its_final_time(monkeypatch):
    staff = "k@position2.com"
    svc = _Sheets({"Page Views": [appmod._PV_HEADER,
                                  _pv("2026-09-25 10:00:00 IST", staff, 600),
                                  _pv("2026-09-25 11:05:00 IST", staff, 300, "pv-11111111", 1),
                                  _pv("2026-09-25 11:30:00 IST", staff, 480, "pv-11111111", 2)]})
    _patch_build(monkeypatch, svc)
    d = appmod._fetch_usage_data(internal=True)
    assert d["total_page_views"] == 2
    assert d["total_time_fmt"] == "18m"   # 600 + 480; counting both snapshots would read 23m


def test_every_page_views_reader_keeps_one_row_per_page_view():
    src = open(os.path.join(_ROOT, "app.py"), encoding="utf-8").read()
    reads = [m.start() for m in re.finditer(r'"Page Views!A:P"', src)]
    assert len(reads) == 5
    for pos in reads:
        assert "_pv_latest_snapshots(" in src[pos - 120:pos + 900], src[pos - 200:pos + 400]
    assert not re.search(r'"Page Views!A:[A-O]"', src)
