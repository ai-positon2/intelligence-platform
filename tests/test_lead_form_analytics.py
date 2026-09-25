"""Anonymous Traffic dashboard: lead-form interest labels and the stray
header rows the sheet writers used to plant mid-data. Everything runs through
the real app code against an in-memory stand-in for the Sheets API."""
import json
import os
import re
import sys

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Exec:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class _FakeSheets:
    """Just enough of spreadsheets().values().get/append + batchUpdate."""

    def __init__(self, tabs=None, fail_reads=False, tab_exists=True):
        self.tabs = {k: [list(r) for r in v] for k, v in (tabs or {}).items()}
        self.fail_reads = fail_reads
        self.tab_exists = tab_exists
        self.appends = []

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId=None, range=None):
        def run():
            if self.fail_reads:
                raise RuntimeError("transient Sheets error")
            tab = range.split("!")[0] if "!" in range else "Login Log"
            rows = self.tabs.get(tab, [])
            return {"values": rows[:1]} if range.endswith("A1:A1") or range.endswith("A1:G1") \
                else {"values": rows}
        return _Exec(run)

    def append(self, spreadsheetId=None, range=None, valueInputOption=None,
               insertDataOption=None, body=None):
        def run():
            tab = range.split("!")[0]
            self.appends.append((tab, body["values"][0]))
            self.tabs.setdefault(tab, []).append(body["values"][0])
            return {}
        return _Exec(run)

    def update(self, **kw):
        return _Exec(lambda: {})

    def batchUpdate(self, spreadsheetId=None, body=None):
        def run():
            if self.tab_exists:
                raise RuntimeError("A sheet with that name already exists")
            return {}
        return _Exec(run)


# ── _ensure_tab_header ──────────────────────────────────────────────────────

def test_a_transient_read_failure_on_an_existing_tab_writes_no_header():
    svc = _FakeSheets(fail_reads=True, tab_exists=True)
    assert appmod._ensure_tab_header(svc, "sid", "Visitor Analytics", ["Timestamp (IST)", "x"]) is None
    assert svc.appends == []


def test_a_tab_this_call_creates_gets_its_header():
    svc = _FakeSheets(fail_reads=True, tab_exists=False)
    hdr = ["Timestamp (IST)", "x"]
    assert appmod._ensure_tab_header(svc, "sid", "New Tab", hdr) == hdr
    assert svc.appends == [("New Tab", hdr)]


def test_an_empty_existing_tab_gets_its_header_once():
    svc = _FakeSheets(tabs={"T": []})
    hdr = ["Timestamp (IST)", "x"]
    appmod._ensure_tab_header(svc, "sid", "T", hdr)
    appmod._ensure_tab_header(svc, "sid", "T", hdr)
    assert svc.appends == [("T", hdr)]


def test_a_tab_with_a_header_is_left_alone_and_its_header_is_returned():
    svc = _FakeSheets(tabs={"T": [["Timestamp (IST)", "old"]]})
    assert appmod._ensure_tab_header(svc, "sid", "T", ["Timestamp (IST)", "new"]) == ["Timestamp (IST)", "old"]
    assert svc.appends == []


def test_atrack_appends_only_the_row_when_the_header_check_fails(monkeypatch):
    svc = _FakeSheets(tabs={"Visitor Analytics": [list(appmod._VA_HEADER)]},
                      fail_reads=True, tab_exists=True)
    monkeypatch.setattr(appmod, "_va_sheets_service", lambda: svc)
    body = json.dumps({"vid": "v1", "sid": "s1", "page": "/", "cta": {}, "form": ""})
    r = appmod.app.test_client().post("/api/atrack", data=body, content_type="text/plain",
                                      headers={"User-Agent": "Mozilla/5.0 Chrome/120"})
    assert r.status_code == 200
    assert len(svc.appends) == 1
    assert svc.appends[0][1][0] != "Timestamp (IST)"


def test_no_sheet_writer_still_appends_a_header_on_any_read_error():
    # The old shape: `raise Exception("empty")` inside a try whose except
    # unconditionally appended the header.
    assert 'raise Exception("empty")' not in open(os.path.join(_ROOT, "app.py"), encoding="utf-8").read()


# ── _strip_repeated_headers ─────────────────────────────────────────────────

def test_stray_header_rows_are_dropped_and_real_rows_kept():
    hdr = ["Timestamp (IST)", "Page"]
    rows = [hdr, ["2026-09-01 10:00:00 IST", "/"], hdr, ["2026-09-02 10:00:00 IST", "/a"], hdr]
    assert appmod._strip_repeated_headers(rows) == [hdr, rows[1], rows[3]]


def test_strip_leaves_empty_and_header_only_tabs_unchanged():
    assert appmod._strip_repeated_headers([]) == []
    assert appmod._strip_repeated_headers([["Timestamp (IST)"]]) == [["Timestamp (IST)"]]


# ── dashboard aggregation ───────────────────────────────────────────────────

def _va_row(ts, vid, sid, cta="", form=""):
    row = [""] * len(appmod._VA_HEADER)
    ix = {n: i for i, n in enumerate(appmod._VA_HEADER)}
    row[ix["Timestamp (IST)"]] = ts
    row[ix["Visitor ID"]] = vid
    row[ix["Session ID"]] = sid
    row[ix["Page Title"]] = "Home"
    row[ix["CTA Clicks"]] = cta
    row[ix["Form Stage"]] = form
    row[ix["Bot"]] = "No"
    return row


def _dashboard(monkeypatch, va_rows):
    svc = _FakeSheets(tabs={"Visitor Analytics": va_rows})
    monkeypatch.setattr(appmod, "_va_sheets_service", lambda: svc)
    monkeypatch.setattr(appmod, "_va_sheets_service_st", lambda: svc)
    monkeypatch.setattr(appmod, "_read_access_requests", lambda *a, **k: [])
    return appmod._fetch_visitor_analytics_uncached()


def test_retired_request_access_clicks_get_their_own_row(monkeypatch):
    hdr = list(appmod._VA_HEADER)
    d = _dashboard(monkeypatch, [
        hdr,
        _va_row("2026-07-01 10:00:00 IST", "v1", "s1", "request_access:Request access×3", "open"),
        _va_row("2026-07-02 10:00:00 IST", "v2", "s2", "request_access:×1", "open"),
        _va_row("2026-07-03 10:00:00 IST", "v3", "s3", "request_access:Build a custom agent×2", "open"),
        _va_row("2026-09-20 10:00:00 IST", "v4", "s4",
                "lead:Talk to us×1 · lead:Agent not in directory×1", "started"),
    ])
    li = dict(d["lead_interests"])
    assert li["Request access (retired button)"] == 4
    assert li["Build a custom agent"] == 2
    assert li["Talk to us"] == 1
    assert li["Agent not in directory"] == 1


def test_stray_header_rows_in_the_tab_are_not_counted_as_traffic(monkeypatch):
    hdr = list(appmod._VA_HEADER)
    real = [_va_row("2026-09-0%d 10:00:00 IST" % i, "v%d" % i, "s%d" % i) for i in (1, 2, 3)]
    d = _dashboard(monkeypatch, [hdr, real[0], hdr, real[1], hdr, hdr, real[2]])
    assert d["kpis"]["pageviews"] == 3
    assert d["kpis"]["visitors"] == 3
    assert all(r["vid"] != "Visitor " for r in d["recent"])


# ── the public page ─────────────────────────────────────────────────────────

def test_every_lead_form_trigger_declares_its_interest():
    # An unlabelled trigger is recorded under the "Talk to us" fallback, which
    # is how that row came to mean "a button nobody can find".
    html = open(os.path.join(_ROOT, "templates", "agents.html"), encoding="utf-8").read()
    tags = [m.group(0) for m in re.finditer(r"<(?:a|button)\b[^>]*\bdata-demo\b[^>]*>", html)]
    assert len(tags) >= 12
    missing = [t[:80] for t in tags if not re.search(r'data-interest="[^"]+"', t)]
    assert not missing, missing
