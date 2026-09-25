"""Numbers shown on the admin analytics dashboards, driven through the real
aggregation code with small hand-built sheet fixtures. Each test pins a
figure that was verifiably wrong in production on 2026-09-25."""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402

LOGIN_TAB = "Login Log"


class _Exec:
    def __init__(self, v):
        self._v = v

    def execute(self):
        return self._v


class _Sheets:
    def __init__(self, tabs):
        self.tabs = tabs

    def spreadsheets(self):
        return self

    def values(self):
        return self

    def get(self, spreadsheetId=None, range=None):
        tab = range.split("!")[0] if "!" in range else LOGIN_TAB
        return _Exec({"values": [list(r) for r in self.tabs.get(tab, [])]})


def _row(header, **cols):
    r = [""] * len(header)
    for k, v in cols.items():
        r[header.index(k)] = v
    return r


def _va(vid, sid, ts, top=0, cta="", ip=""):
    return _row(appmod._VA_HEADER, **{"Timestamp (IST)": ts, "Date": ts[:10], "Visitor ID": vid,
                                      "Session ID": sid, "Page Title": "Home", "Page URL": "/",
                                      "Time On Page (s)": str(top), "CTA Clicks": cta,
                                      "Bot": "No", "IP": ip})


def _ms(email, vid, ts):
    return _row(appmod._MS_HEADER, **{"Timestamp (IST)": ts, "Date": ts[:10], "Email": email,
                                      "Visitor ID": vid})


def _pv(ts, email, url, secs=10, vid=""):
    return [ts, ts[:10], ts[11:19], "Monday", email, "Page", url, str(secs), "", "", "Chrome",
            "macOS", "Desktop", vid]


def _anon(monkeypatch, va_rows, ms_rows=(), idmap=None):
    svc = _Sheets({"Visitor Analytics": [appmod._VA_HEADER] + list(va_rows),
                   appmod._MEMBER_TAB: [appmod._MS_HEADER] + list(ms_rows)})
    monkeypatch.setattr(appmod, "_va_sheets_service", lambda: svc)
    monkeypatch.setattr(appmod, "_va_sheets_service_st", lambda: svc)
    monkeypatch.setattr(appmod, "_read_access_requests", lambda *a, **k: [])
    monkeypatch.setattr(appmod, "_va_identity_map", lambda *a, **k: dict(idmap or {}))
    monkeypatch.setattr(appmod, "_VI_OK", False)
    monkeypatch.setattr(appmod, "_ip_company", lambda ip: "")
    return appmod._fetch_visitor_analytics_uncached()


# ── Anonymous Traffic ───────────────────────────────────────────────────────

def test_signup_funnel_counts_visitors_who_signed_in_after_clicking(monkeypatch):
    d = _anon(monkeypatch, [
        # v1 clicks Sign up; the row is logged on leave, 20s AFTER the sign-in it caused
        _va("v1", "s1", "2026-09-10 10:00:40 IST", top=40, cta="signup×1"),
        # v2 clicks, but their only sign-in is from BEFORE this visit
        _va("v2", "s2", "2026-09-10 11:00:00 IST", cta="signup×2"),
        # v3 never clicks Sign up but signs in later
        _va("v3", "s3", "2026-09-10 12:00:00 IST"),
        _va("v4", "s4", "2026-09-10 13:00:00 IST"),
    ], ms_rows=[
        _ms("one@x.com", "v1", "2026-09-10 10:00:20 IST"),
        _ms("two@x.com", "v2", "2026-09-01 09:00:00 IST"),
        _ms("three@x.com", "v3", "2026-09-12 09:00:00 IST"),
    ])
    assert d["signup_funnel"] == {"clicked": 2, "signed_in": 1}
    assert d["kpis"]["signup_cvr"] == 50.0
    assert d["kpis"]["signup_clicks"] == 3
    assert d["kpis"]["signed_in"] == 2
    converted = {x["vid"] for x in d["all_visitors"] if x.get("converted")}
    assert not any(v.startswith("v2") for v in converted)


def test_average_time_on_page_caps_a_tab_left_open(monkeypatch):
    d = _anon(monkeypatch, [
        _va("a", "s1", "2026-09-10 10:00:00 IST", top=30),
        _va("b", "s2", "2026-09-10 10:05:00 IST", top=10),
        _va("c", "s3", "2026-09-10 10:10:00 IST", top=2 * 86400),
    ])
    assert d["kpis"]["avg_time"] == "10m 13s"   # (30 + 10 + 1800) / 3


def test_identified_kpi_is_not_capped_at_the_60_row_display_list(monkeypatch):
    rows = [_va("v%03d" % i, "s%03d" % i, "2026-09-10 10:%02d:00 IST" % (i % 60)) for i in range(70)]
    idmap = {"v%03d" % i: {"name": "Person %d" % i} for i in range(70)}
    d = _anon(monkeypatch, rows, idmap=idmap)
    assert d["kpis"]["identified"] == 70
    assert len(d["identified"]) == 60


def test_companies_are_looked_up_past_the_150_oldest_visitors(monkeypatch):
    rows = [_va("v%03d" % i, "s%03d" % i, "2026-09-10 10:00:00 IST", ip="10.0.%d.%d" % (i // 250, i % 250))
            for i in range(200)]
    svc = _Sheets({"Visitor Analytics": [appmod._VA_HEADER] + rows})
    monkeypatch.setattr(appmod, "_va_sheets_service", lambda: svc)
    monkeypatch.setattr(appmod, "_va_sheets_service_st", lambda: svc)
    monkeypatch.setattr(appmod, "_read_access_requests", lambda *a, **k: [])
    monkeypatch.setattr(appmod, "_va_identity_map", lambda *a, **k: {})
    monkeypatch.setattr(appmod, "_VI_OK", False)
    monkeypatch.setattr(appmod, "_ip_company", lambda ip: "Co " + ip)
    d = appmod._fetch_visitor_analytics_uncached()
    assert d["kpis"]["companies"] == 200


# ── Public Page Analytics ───────────────────────────────────────────────────

def _members(monkeypatch, ms_rows, va_rows, pv_rows):
    svc = _Sheets({appmod._MEMBER_TAB: [appmod._MS_HEADER] + list(ms_rows),
                   "Visitor Analytics": [appmod._VA_HEADER] + list(va_rows),
                   "Page Views": [["Timestamp (IST)"] + [""] * 13] + list(pv_rows)})
    monkeypatch.setattr(appmod, "_va_sheets_service", lambda: svc)
    monkeypatch.setattr(appmod, "_va_sheets_service_st", lambda: svc)
    monkeypatch.setattr(appmod, "_read_access_requests", lambda *a, **k: [])
    monkeypatch.setattr(appmod, "_va_identity_map", lambda *a, **k: {})
    return appmod._fetch_member_analytics_uncached()


def test_page_view_only_members_get_no_invented_sign_in(monkeypatch):
    d = _members(monkeypatch,
                 ms_rows=[_ms("m1@x.com", "mv1", "2026-09-10 12:00:00 IST")],
                 va_rows=[],
                 pv_rows=[_pv("2026-09-05 10:00:00 IST", "pvonly@y.com", "/app"),
                          _pv("2026-09-06 10:00:00 IST", "pvonly@y.com", "/app/agents")])
    assert d["kpis"]["members"] == 2
    assert d["kpis"]["signins"] == 1
    pvonly = next(m for m in d["members"] if m["email"] == "pvonly@y.com")
    assert pvonly["signins"] == 0
    assert pvonly["status"] == "returning"
    assert not any(e.get("kind") == "signin" for e in pvonly.get("timeline", []))
    assert sum(n for _, n in d["series"]) == 1


def test_pre_login_journey_excludes_browsing_after_sign_in_and_drives_linked(monkeypatch):
    d = _members(monkeypatch,
                 ms_rows=[_ms("m1@x.com", "mv1", "2026-09-10 12:00:00 IST"),
                          _ms("m2@x.com", "mv2", "2026-09-10 12:00:00 IST")],
                 va_rows=[_va("mv1", "a", "2026-09-10 11:00:00 IST"),
                          _va("mv1", "b", "2026-09-11 09:00:00 IST"),
                          _va("mv2", "c", "2026-09-12 09:00:00 IST")],
                 pv_rows=[])
    m1 = next(m for m in d["members"] if m["email"] == "m1@x.com")
    m2 = next(m for m in d["members"] if m["email"] == "m2@x.com")
    assert m1["prelogin_pages"] == 1 and m1["linked"] is True
    assert m2["prelogin_pages"] == 0 and m2["linked"] is False
    assert d["kpis"]["linked"] == 1
    assert d["kpis"]["avg_pre"] == 1.0


# ── Public Agent Usage ──────────────────────────────────────────────────────

def test_agent_runs_split_staff_from_external_and_bars_add_up(monkeypatch):
    live = next(a["slug"] for a in appmod.APP_AGENTS if a.get("seo_slug"))
    ar = lambda e, slug: ["2026-09-10 10:00:00 IST", "2026-09-10", e, "N", slug, "Agent"]
    rows = [list(appmod._AR_HEADER)] + [ar("a@position2.com", live)] * 3 + \
           [ar("b@x.com", live)] * 2 + [ar("c@x.com", "some-portal-tool")]
    monkeypatch.setattr(appmod, "_agent_run_rows", lambda *a, **k: rows)
    monkeypatch.setattr(appmod, "_va_sheets_service", lambda: object())
    d = appmod._fetch_agent_run_stats()
    assert d["total_runs"] == 6
    assert (d["staff_runs"], d["staff_users"]) == (3, 1)
    assert (d["external_runs"], d["external_users"]) == (3, 2)
    assert sum(a["runs"] for a in d["agents"]) == d["total_runs"]


# ── Internal / External Usage ───────────────────────────────────────────────

def _patch_build(monkeypatch, tabs):
    import googleapiclient.discovery as _disc
    import google.oauth2.service_account as _sa
    monkeypatch.setattr(appmod, "LOGIN_LOG_SHEET_ID", "fake")
    monkeypatch.setenv("GOOGLE_SA_JSON", '{"type": "service_account"}')
    monkeypatch.setattr(_disc, "build", lambda *a, **k: _Sheets(tabs))

    class _Creds:
        @staticmethod
        def from_service_account_info(*a, **k):
            return object()
    monkeypatch.setattr(_sa, "Credentials", _Creds)


def test_logins_chart_is_the_last_14_calendar_days_zero_filled(monkeypatch):
    today = datetime.now(appmod.IST).date()
    day = lambda n: (today - timedelta(days=n)).isoformat()
    login = lambda d: [d + " 10:00:00 IST", d, "10:00:00", "Mon", "10", "s@position2.com", "S"] + [""] * 14
    _patch_build(monkeypatch, {LOGIN_TAB: [["Timestamp (IST)"] + [""] * 20,
                                           login(day(0)), login(day(0)), login(day(20)), login(day(40))]})
    d = appmod._fetch_usage_data(internal=True)
    days = d["login_days"]
    assert [x[0] for x in days] == [day(n) for n in range(13, -1, -1)]
    assert days[-1][1] == 2 and sum(n for _, n in days) == 2


def test_a_run_with_no_saved_title_does_not_shift_later_titles(monkeypatch):
    ar = lambda ts: [ts, ts[:10], "b@x.com", "B", "keyword-finder", "Keyword Finder"]
    _patch_build(monkeypatch, {
        appmod._MEMBER_TAB: [appmod._MS_HEADER, _ms("b@x.com", "v", "2026-09-10 09:00:00 IST")],
        appmod._AR_TAB: [list(appmod._AR_HEADER), ar("2026-09-10 10:00:00 IST"), ar("2026-09-10 10:30:00 IST")],
    })
    monkeypatch.setattr(appmod, "_list_agent_run_titles", lambda *a, **k: [
        {"email": "b@x.com", "slug": "keyword-finder", "title": "Second run", "at": "2026-09-10 10:31:00"}])
    d = appmod._fetch_usage_data(internal=False)
    by_ts = {r["ts"]: r["detail"] for r in d["agent_runs_table"]}
    assert by_ts["2026-09-10 10:00:00 IST"] == ""
    assert by_ts["2026-09-10 10:30:00 IST"] == "Second run"


def test_views_per_user_uses_the_final_user_count(monkeypatch):
    ar = ["2026-09-10 10:00:00 IST", "2026-09-10", "runner@x.com", "R", "keyword-finder", "KF"]
    _patch_build(monkeypatch, {
        appmod._MEMBER_TAB: [appmod._MS_HEADER, _ms("b@x.com", "v", "2026-09-10 09:00:00 IST")],
        "Page Views": [["Timestamp (IST)"] + [""] * 13] + [_pv("2026-09-10 09:01:00 IST", "b@x.com", "/app")] * 4,
        appmod._AR_TAB: [list(appmod._AR_HEADER), ar],
    })
    monkeypatch.setattr(appmod, "_list_agent_run_titles", lambda *a, **k: [])
    d = appmod._fetch_usage_data(internal=False)
    assert d["unique_users"] == 2
    assert d["views_per_user"] == 2.0


# ── Client Usage ────────────────────────────────────────────────────────────

@pytest.fixture
def acme(monkeypatch):
    monkeypatch.setattr(appmod, "_CU_CACHE", {})
    monkeypatch.setattr(appmod, "_CU_ALL_CACHE", {"data": None, "ts": 0.0})
    monkeypatch.setitem(appmod.CLIENTS, "acme", {"name": "Acme", "domains": ["acme.com"], "agents": []})


def _cu(monkeypatch, tabs):
    def read(rng):
        key = rng.split("!")[0] if "!" in rng else LOGIN_TAB
        return [list(r) for r in tabs.get(key, [])]
    monkeypatch.setattr(appmod, "_cu_read_tab", read)
    return appmod._fetch_client_usage("acme", force=True)


def test_a_staff_sign_in_written_to_both_tabs_counts_once(monkeypatch, acme):
    staff = "s@position2.com"
    d = _cu(monkeypatch, {
        LOGIN_TAB: [["Timestamp (IST)"] + [""] * 20,
                    ["2026-09-10 10:00:00 IST", "2026-09-10", "", "", "", staff, "S"] + [""] * 14],
        appmod._MEMBER_TAB: [appmod._MS_HEADER, _ms(staff, "v", "2026-09-10 10:00:01 IST")],
        "Page Views": [["Timestamp (IST)"] + [""] * 13, _pv("2026-09-10 10:01:00 IST", staff, "/acme")],
    })
    assert d["kpis"]["total_logins"] == 1


def test_page_viewers_are_counted_from_every_view_not_the_recent_list(monkeypatch, acme):
    # Five heavy recent users fill the 400-event "recent" list on their own,
    # so a sixth person who viewed the same page earlier falls off it.
    pv = [["Timestamp (IST)"] + [""] * 13,
          _pv("2026-09-01 09:00:00 IST", "early@acme.com", "/acme")]
    for i in range(5):
        for j in range(90):
            pv.append(_pv("2026-09-%02d 10:%02d:00 IST" % (10 + j % 15, j % 60), "p%d@acme.com" % i, "/acme"))
    d = _cu(monkeypatch, {"Page Views": pv})
    top = next(p for p in d["top_pages"] if p["url"] == "/acme")
    assert top["views"] == 451
    assert top["viewers"] == 6


# ── Public pages: the tracker must skip signed-in visitors ──────────────────

def test_visitor_tracker_is_not_loaded_for_a_signed_in_visitor():
    c = appmod.app.test_client()
    assert "visitor_track.js" in c.get("/terms").get_data(as_text=True)
    with c.session_transaction() as s:
        s["google_user"] = {"email": "m@x.com", "name": "M"}
    assert "visitor_track.js" not in c.get("/terms").get_data(as_text=True)


# ── Agent feedback, against a real Postgres ─────────────────────────────────

@pytest.fixture
def postgres(monkeypatch):
    initdb, pg_ctl = shutil.which("initdb"), shutil.which("pg_ctl")
    if not (initdb and pg_ctl):
        pytest.skip("Postgres binaries not installed")
    tmp = tempfile.mkdtemp()
    data = os.path.join(tmp, "data")
    env = dict(os.environ, LC_ALL="C")   # macOS postmaster refuses to start without a valid locale
    subprocess.run([initdb, "-D", data, "-U", "t", "--auth=trust"], check=True, capture_output=True, env=env)
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    # TCP only: macOS temp paths overflow the ~103-char Unix socket path limit.
    subprocess.run([pg_ctl, "-D", data, "-l", os.path.join(tmp, "log"), "-o",
                    "-p %d -c listen_addresses=127.0.0.1 -c unix_socket_directories=''" % port,
                    "-w", "start"], check=True, capture_output=True, env=env)
    try:
        monkeypatch.setenv("DATABASE_URL", "postgresql://t@127.0.0.1:%d/postgres" % port)
        from tracker import agent_feedback as fb
        monkeypatch.setattr(fb, "_TABLES_READY", False)
        yield fb
    finally:
        subprocess.run([pg_ctl, "-D", data, "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def test_feedback_summary_counts_each_persons_latest_vote(postgres):
    fb = postgres
    vote = lambda email, rating: fb.save(email=email, agent_slug="sci", run_id="r1", section_key="overview",
                                         section_label="Overview", rating=rating, reason=None)
    vote("a@x.com", "up"); time.sleep(0.01); vote("a@x.com", "down")   # changed their mind
    vote("b@x.com", "up"); vote("b@x.com", "up")                      # double tap
    [row] = fb.summary(days=30)
    assert (row["up"], row["down"], row["total"]) == (1, 1, 2)
