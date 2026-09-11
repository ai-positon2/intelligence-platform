"""/42northdental -- the client portal for 42 North Dental's own Slot Checker.

Modeled on the existing CLIENTS pattern (see /northstaranesthesia), but with a
single wrinkle: Slot Checker is not an APP_AGENTS entry (see
app._SLOT_CHECKER_CLIENT_AGENT's own comment for why -- APP_AGENTS backs /app,
the public catalog shown to every signed-in Google user, and this dashboard is
meant for 42 North Dental and Position2 staff only). So what's pinned here is
not just "does the portal render", but that the agent stays entirely off the
public catalog while still working like a normal client-portal agent: listed,
gated, and backed by a live dashboard whose data/insights routes read the same
underlying sheet as the internal @position2_required ones, just through the
client's own gate instead.
"""
import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
from tracker import slot_checker as sc  # noqa: E402
from tracker import slot_checker_insights as sci  # noqa: E402

CLIENT = "42northdental"
HOME = "/" + CLIENT
AGENT = "slot-checker"
DETAIL = "%s/agents/%s" % (HOME, AGENT)
USE = DETAIL + "/use"
DASHBOARD = DETAIL + "/dashboard"
DATA = DASHBOARD + "/data"
INSIGHTS = DASHBOARD + "/insights"
NAME = "42 North Dental Slot Checker"


@pytest.fixture(autouse=True)
def _clean_caches():
    sc.reset_cache()
    sci.reset_cache()
    yield
    sc.reset_cache()
    sci.reset_cache()


def _fake():
    return {
        "generated_at": "2026-08-21T00:00:00+00:00",
        "source": {"file": "x.xlsx"},
        "dates": ["2026-08-12", "2026-08-13"],
        "locations": [{
            "office": "007", "name": "Quincy", "account": "Gentle Dental",
            "brand": "Gentle Dental", "state": "MA", "city": "Quincy",
            "url": "https://example.test/ols", "system": "gentledental",
            "booking": "Calendar View", "checked_at": "2026-08-11T09:00:00",
            "services": [{"name": "Emergency Exam",
                          "observations": [{"at": "2026-08-11T09:00:00", "counts": [7, 0]}]}],
        }],
    }


@pytest.fixture
def snapshot(monkeypatch, tmp_path):
    def write(snap):
        import json
        p = tmp_path / "slot_checker_snapshot.json"
        p.write_text(json.dumps(snap))
        monkeypatch.setattr(sc, "SNAPSHOT_PATH", p)
        sc.reset_cache()
        return p
    return write


def _client(email=None):
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T", "given_name": "T"}
    return c


# ── gating: only @42northdental.com and @position2.com ──────────────────────

def test_signed_out_is_bounced_to_login():
    resp = _client().get(HOME, follow_redirects=False)
    assert resp.status_code in (302, 401, 403)


def test_an_unrelated_domain_is_denied():
    resp = _client("someone@gmail.com").get(HOME)
    assert resp.status_code == 403
    assert b"This workspace is private" in resp.data


def test_a_42northdental_account_is_let_in():
    resp = _client("front.desk@42northdental.com").get(HOME)
    assert resp.status_code == 200
    assert NAME.encode() in resp.data


def test_a_position2_staff_account_is_also_let_in():
    resp = _client("reporting@position2.com").get(HOME)
    assert resp.status_code == 200


def test_it_is_not_open_to_all_unlike_northstar():
    """NorthStar Anesthesia opted into open_to_all; this client did not, since
    the ask was explicitly to restrict it to the two named domains."""
    assert not appmod.CLIENTS[CLIENT].get("open_to_all")
    assert appmod.CLIENTS[CLIENT]["domains"] == ["42northdental.com"]


def test_gating_extends_to_every_route_under_the_slug():
    """The gate is per-view (_client_gate called from each handler), not a
    blueprint-wide before_request -- so it's worth pinning that a stray new
    route under this slug can't accidentally skip it."""
    c = _client("someone@gmail.com")
    for path in (HOME, DETAIL, USE, DASHBOARD, DATA, INSIGHTS, HOME + "/history"):
        resp = c.get(path)
        assert resp.status_code == 403, path + " leaked past the domain gate"


# ── the portal lists exactly one agent ───────────────────────────────────────

def test_the_portal_lists_only_the_slot_checker():
    body = _client("front.desk@42northdental.com").get(HOME).data.decode()
    assert NAME in body
    # None of NorthStar's agents should leak into this client's own portal.
    for other in ("ABM Signal Tracker", "LinkedIn Intelligence", "Keyword Finder",
                  "Content Brief Generator", "Content Enhancer"):
        assert other not in body


def test_the_agent_shows_as_live_not_in_setup():
    body = _client("front.desk@42northdental.com").get(HOME).data.decode()
    assert "Live" in body


def test_the_detail_page_renders_with_no_related_agents_strip():
    resp = _client("front.desk@42northdental.com").get(DETAIL)
    assert resp.status_code == 200
    body = resp.data.decode()
    assert NAME in body
    assert "Use agent" in body
    assert "More agents in your workspace" not in body


def test_unknown_agent_slug_redirects_home():
    resp = _client("front.desk@42northdental.com").get(HOME + "/agents/not-a-real-agent")
    assert resp.status_code in (301, 302)


# ── the agent is deliberately NOT on the public /app catalog ────────────────

def test_slot_checker_is_not_an_app_agents_entry():
    assert AGENT not in appmod.APP_AGENTS_BY_SLUG


def test_the_public_app_catalog_does_not_list_it():
    c = _client("front.desk@42northdental.com")
    assert NAME not in c.get("/app").data.decode()


def test_app_detail_for_the_slug_redirects_away():
    """/app/<slug> falls back to /app for any slug not in APP_AGENTS_BY_SLUG --
    this must stay true for "slot-checker" specifically, or the bespoke agent
    would be reachable through the generic catalog after all."""
    c = _client("front.desk@42northdental.com")
    resp = c.get("/app/" + AGENT, follow_redirects=False)
    assert resp.status_code in (301, 302)


# ── the "Use" page embeds the dashboard, uncapped ────────────────────────────

def test_use_page_embeds_the_dashboard_not_a_serp_tool(snapshot):
    snapshot(_fake())
    body = _client("front.desk@42northdental.com").get(USE).data.decode()
    assert DASHBOARD in body
    assert "Live" in body


# ── the dashboard route: same page, client chrome hidden ────────────────────

def test_dashboard_renders_the_real_slot_checker_template(snapshot):
    snapshot(_fake())
    resp = _client("front.desk@42northdental.com").get(DASHBOARD)
    assert resp.status_code == 200
    assert NAME.encode() in resp.data


def test_dashboard_hides_internal_only_chrome(snapshot):
    """client_mode must strip the internal topbar (Hub / Strategic Agents
    breadcrumb, the admin dropdown) and the cross-tool Ctrl+K palette -- none
    of that should ever reach a client's browser."""
    snapshot(_fake())
    resp = _client("front.desk@42northdental.com").get(DASHBOARD)
    # A broken template (e.g. an unbalanced {% if client_mode %}) 500s, and a
    # 500's error body also happens to lack every one of these strings -- so
    # this has to pin a real 200 first, or a template that stopped rendering
    # at all would pass this test for the wrong reason.
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "/p2/hub" not in body
    assert "/p2/strategic-agents" not in body
    assert "kpal" not in body


def test_dashboard_points_its_fetches_at_the_client_scoped_routes(snapshot):
    snapshot(_fake())
    resp = _client("front.desk@42northdental.com").get(DASHBOARD)
    assert resp.status_code == 200
    body = resp.data.decode()
    assert DATA in body
    assert INSIGHTS in body
    assert "/p2/strategic-agents/42-north-dental-slot-checker/data" not in body
    assert "/p2/strategic-agents/42-north-dental-slot-checker/insights" not in body


def test_dashboard_404s_for_an_agent_this_client_has_no_live_dashboard_for():
    resp = _client("front.desk@42northdental.com").get(
        HOME + "/agents/linkedin-intelligence/dashboard")
    assert resp.status_code == 404


def test_northstar_cannot_reach_this_dashboard_route_for_slot_checker():
    """slot_checker_live is only set on the 42 North Dental entry -- another
    client's portal hitting the same relative path must 404, not silently
    serve 42 North Dental's data under someone else's brand."""
    resp = _client("someone@position2.com").get(
        "/northstaranesthesia/agents/slot-checker/dashboard")
    assert resp.status_code == 404


def test_northstar_cannot_reach_the_data_or_insights_routes_either(snapshot):
    """The stronger version of the test above. _client_agent_dashboard's own
    "agent_slug not in client.get('agents', [])" check would already catch a
    request for /northstaranesthesia/.../slot-checker/dashboard, since NorthStar
    never listed slot-checker as one of its agents -- so that test alone could
    pass even if _client_dashboard_data/_client_dashboard_insights forgot to
    check _client_live_dashboard themselves (they have no such agents-list
    check of their own). This is the layer that actually has to hold: NorthStar
    is open_to_all, so any signed-in Google account could otherwise reach 42
    North Dental's practice data through this path."""
    snapshot(_fake())
    c = _client("someone@position2.com")
    assert c.get("/northstaranesthesia/agents/slot-checker/dashboard/data").status_code == 404
    assert c.get("/northstaranesthesia/agents/slot-checker/dashboard/insights").status_code == 404


# ── data endpoint: same payload as the internal route, gated differently ────

def test_data_returns_the_same_derived_dashboard(snapshot):
    snapshot(_fake())
    body = _client("front.desk@42northdental.com").get(DATA).get_json()
    assert body["totals"]["slots"] == 7
    assert [p["name"] for p in body["practices"]] == ["Quincy"]


def test_data_requires_the_client_gate_not_position2_required():
    """This must be reachable by @42northdental.com even though the internal
    route it mirrors is @position2_required staff-only."""
    resp = _client("front.desk@42northdental.com").get(DATA)
    assert resp.status_code == 200
    resp = _client("someone@gmail.com").get(DATA)
    assert resp.status_code == 403


def test_data_fresh_param_bypasses_the_cache():
    calls = {"n": 0}

    def fake_load(path=None):
        calls["n"] += 1
        return _fake()

    import unittest.mock as mock
    with mock.patch.object(sc, "load_snapshot", fake_load):
        c = _client("front.desk@42northdental.com")
        c.get(DATA)
        c.get(DATA)
        assert calls["n"] == 1
        c.get(DATA + "?fresh=1")
        assert calls["n"] == 2


# ── insights endpoint: same contract as the internal route ──────────────────

def test_insights_degrades_when_the_key_is_not_configured(monkeypatch, snapshot):
    snapshot(_fake())
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    body = _client("front.desk@42northdental.com").get(INSIGHTS).get_json()
    assert body["configured"] is False
    assert body["ok"] is False


def test_insights_returns_the_generated_briefing_on_success(monkeypatch, snapshot):
    snapshot(_fake())
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-only")
    fake_result = {"headline": "H", "synthesis": "S", "topActions": []}
    monkeypatch.setattr(sci, "generate_insights_result", lambda dashboard: (fake_result, None))
    body = _client("front.desk@42northdental.com").get(INSIGHTS).get_json()
    assert body["ok"] is True
    assert body["insights"]["headline"] == "H"


def test_insights_requires_the_client_gate():
    resp = _client("someone@gmail.com").get(INSIGHTS)
    assert resp.status_code == 403


def test_insights_404s_for_any_other_agent_slug():
    """Only Slot Checker has an insights companion route; a client with, say, a
    live LinkedIn Intelligence dashboard has no equivalent endpoint."""
    resp = _client("someone@position2.com").get(
        "/northstaranesthesia/agents/linkedin-intelligence/dashboard/insights")
    assert resp.status_code == 404


# ── history: no runs, since a dashboard is never run-metered ────────────────

def test_history_page_renders_empty_rather_than_erroring():
    """The route itself still resolves -- hide_history only pulls the sidebar
    link, the same "hidden means unlisted, not gone" principle HIDDEN_AGENT_SLUGS
    already established for agents (see test_hidden_agent_withdrawal.py)."""
    resp = _client("front.desk@42northdental.com").get(HOME + "/history")
    assert resp.status_code == 200


def test_the_sidebar_hides_the_history_link_for_this_client():
    body = _client("front.desk@42northdental.com").get(HOME).data.decode()
    assert 'href="/42northdental/history"' not in body
    assert appmod.CLIENTS[CLIENT]["hide_history"] is True


def test_northstar_still_shows_history_unaffected():
    """The mirror: hide_history is opt-in per client, not a global default that
    quietly took NorthStar's History tab away too."""
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "someone@position2.com", "name": "T"}
    body = c.get("/northstaranesthesia").data.decode()
    assert 'href="/northstaranesthesia/history"' in body
    assert not appmod.CLIENTS["northstaranesthesia"].get("hide_history")


# ── the real 42 North Dental wordmark, not a placeholder ─────────────────────

def test_the_portal_shows_the_clients_own_logo():
    body = _client("front.desk@42northdental.com").get(HOME).data.decode()
    assert appmod.CLIENTS[CLIENT]["logo"] in body


def test_the_logo_is_served_locally_not_hotlinked():
    """Same reasoning as northstaranesthesia's own logo: never depend on the
    client's live site for this portal's own chrome to render correctly."""
    logo = appmod.CLIENTS[CLIENT]["logo"]
    assert logo.startswith("/static/")
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         logo.lstrip("/"))
    assert os.path.isfile(path), "CLIENTS points at a logo file that isn't committed"


# ── the crawl cadence is daily, not weekly ───────────────────────────────────

def test_the_agent_description_says_daily_not_weekly():
    a = appmod._SLOT_CHECKER_CLIENT_AGENT
    assert "daily" in a["pill2"].lower()
    assert "weekly" not in a["pill2"].lower()
    hiw = next(t["d"] for t in a["trips"] if t["t"] == "How it works")
    assert "daily crawl" in hiw.lower()
    assert "weekly" not in hiw.lower()
    assert not any("weekly" in t.lower() for t in a["tags"])


def test_the_detail_page_renders_daily_not_weekly():
    body = _client("front.desk@42northdental.com").get(DETAIL).data.decode()
    assert "checked daily" in body
    assert "weekly" not in body.lower()


# ── the internal @position2_required route is untouched by the refactor ─────

def test_the_internal_route_still_matches_the_client_route_byte_for_byte(snapshot):
    """_slot_checker_data_json/_slot_checker_insights_json are now shared by both
    callers -- the property worth pinning is that neither payload drifted from
    the other when the code was split out."""
    snapshot(_fake())
    internal = _client("someone@position2.com").get(
        "/p2/strategic-agents/42-north-dental-slot-checker/data").get_json()
    client_side = _client("front.desk@42northdental.com").get(DATA).get_json()
    assert internal == client_side
