"""Agent Usage: which pages count as using an agent, and how time, sessions,
runs and run titles add up per person and per agent. Made-up rows only."""

import os
import sys

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402

CLIENT = next(iter(appmod.CLIENTS))


def _pv(email, url, secs, ts="2026-11-01 10:00:00 IST", title=""):
    r = [""] * len(appmod._PV_HEADER)
    for k, v in (("Timestamp (IST)", ts), ("Email", email), ("Page URL", url), ("Seconds", str(secs)), ("Page Title", title)):
        r[appmod._PV_HEADER.index(k)] = v
    return r


def test_only_pages_where_an_agent_is_used_count():
    page = appmod._agent_usage_page
    assert page("/app/keyword-finder/use") == ("keyword-finder", "Public app")
    assert page("/app/keyword-compass/use") == ("keyword-finder", "Public app")          # old slug folded forward
    assert page("/p2/strategic-agents/linkedin-intelligence") == ("linkedin-intelligence", "Internal")
    assert page("/ppc/anonymous-visitors?x=1") == ("anonymous-visitors", "Internal")
    assert page("/p2/abm-signal-tracker/accounts") == ("abm-signal-tracker", "Internal")
    assert page("/%s/agents/signal-tracker/dashboard" % CLIENT) == ("signal-tracker", appmod.CLIENTS[CLIENT]["name"])
    for not_use in ("/app/keyword-finder", "/app", "/p2/hub", "/p2/strategic-agents", "/p2/admin/internal-usage", "/app/history"):
        assert page(not_use) is None, not_use


def test_time_sessions_runs_and_titles_add_up():
    staff, ext = "a@position2.com", "b@example.com"
    pages = [appmod._PV_HEADER,
             _pv(staff, "/p2/strategic-agents/linkedin-intelligence", 600, title="LinkedIn Intelligence · Intelligence by Position2"),
             _pv(staff, "/p2/strategic-agents/linkedin-intelligence", 60),
             _pv(ext, "/app/keyword-finder/use", 300, ts="2026-11-02 09:00:00 IST"),
             _pv(ext, "/app/keyword-finder", 900),                       # description page: not use
             _pv("", "/app/keyword-finder/use", 50)]                     # no email: skipped
    runs = [appmod._AR_HEADER,
            ["2026-11-02 09:01:00 IST", "2026-11-02", ext, "B", "keyword-finder", "Keyword Finder"],
            ["2026-11-02 09:03:00 IST", "2026-11-02", ext, "B", "keyword-compass", "Keyword Compass"]]
    titles = [{"email": ext, "slug": "keyword-finder", "title": "running shoes", "at": "2026-11-02 09:01:30"},
              {"email": ext, "slug": "keyword-finder", "title": "trail shoes", "at": "2026-11-02 09:03:30"}]
    d = appmod._agent_usage_from_rows(pages, runs, titles, {"keyword-finder": "Keyword Finder"}, {staff: "Asha Rao"})
    us = {u["email"]: u for u in d["users"]}
    assert (us[staff]["name"], us[ext]["name"]) == ("Asha Rao", "B")      # sign-in log name, else the run log's
    assert us[staff]["staff"] and not us[ext]["staff"]
    li = us[staff]["agents"][0]
    assert (li["name"], li["secs"], li["views"], li["runs"], li["where"]) == ("LinkedIn Intelligence", 660, 2, 0, ["Internal"])
    kf = us[ext]["agents"][0]
    assert (kf["secs"], kf["views"], kf["runs"], kf["last"]) == (300, 1, 2, "2026-11-02 09:03:00")
    assert [r["title"] for r in kf["ran"]] == ["trail shoes", "running shoes"]                # newest first
    assert [u["email"] for u in d["users"]] == [ext, staff]                                    # most recent first
    assert {a["slug"]: (a["users"], a["secs"]) for a in d["agents"]} == {"linkedin-intelligence": (1, 660), "keyword-finder": (1, 300)}


def test_page_is_admin_only():
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["google_user"] = {"email": "not-an-admin@position2.com", "name": "T"}
    assert c.get("/p2/admin/agent-usage").status_code == 403
    with c.session_transaction() as s:
        s["google_user"] = {"email": "divith.k@position2.com", "name": "T"}
    r = c.get("/p2/admin/agent-usage")
    assert r.status_code == 200 and b"Agent <em>usage</em>" in r.data and b'href="/p2/admin/agent-usage"' in r.data
