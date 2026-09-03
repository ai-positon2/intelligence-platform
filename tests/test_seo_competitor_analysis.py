"""Competitor Analysis went live on the SEO Studio SERP app at /competitor-analysis
(confirmed live 2026-08-14: real SEMrush data, a client picker, traffic/keyword/
backlink/authority comparisons against competitors), wired into /p2/seo (the
internal staff SEO Suite listing) and, one step later the same day, opened up for
requests on the dormant "Competitor Analysis" placeholder in APP_AGENTS (slug
competitor-seo-intelligence, on /app, the public member workspace) -- two
different registries that happen to share a display name.

_seo_tools() prefers a live /tools.json manifest from the SERP app and only falls
back to _SEO_TOOLS_FALLBACK when that fetch fails (it currently always fails: the
SERP app is a client-routed SPA with no such endpoint), so the fallback list is
the actual, only source of truth for what /p2/seo can show today.

The /app placeholder is deliberately requestable but NOT connected. The live
tool's client picker shows every client's data (Tealium, Beta Bionics, ...) with
no per-member scoping -- self-serve access for any signed-in Google account would
be a cross-client data leak, not just an unfinished feature, so a staff review
via the request queue stays in the loop until that's addressed.
"""

import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402

_SLUG = "competitor-analysis"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(appmod, "_SEO_MANIFEST", {"ts": 0.0, "tools": None})
    # /app reads both of these on every page; unpatched they'd fall through to
    # {} / set() anyway with no LOGIN_LOG_SHEET_ID set, but pinning them keeps
    # this file's /app assertions independent of that env detail.
    monkeypatch.setattr(appmod, "_agent_run_counts", lambda email: {})
    monkeypatch.setattr(appmod, "_agent_access_requested_slugs", lambda email: set())
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    return c


def test_it_is_registered_in_the_fallback_list():
    tool = next((t for t in appmod._SEO_TOOLS_FALLBACK if t["slug"] == _SLUG), None)
    assert tool, "competitor-analysis is missing from the SEO tool roster"
    assert tool["path"] == "/competitor-analysis"
    assert tool["name"] == "Competitor Analysis"
    assert "—" not in tool["desc"], "no em dashes in written copy"


def test_the_suite_page_lists_it(client):
    body = client.get("/p2/seo").get_data(as_text=True)
    assert "Competitor Analysis" in body
    assert '/p2/seo/%s"' % _SLUG in body


def test_opening_it_embeds_the_real_serp_apps_page(client):
    r = client.get("/p2/seo/%s" % _SLUG)
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert appmod._SERP_BASE + "/competitor-analysis" in body


def test_an_unknown_seo_slug_still_404s(client):
    r = client.get("/p2/seo/not-a-real-tool")
    assert r.status_code == 404


# ── The /app placeholder: requestable, but deliberately not connected ───────

_APP_SLUG = "competitor-seo-intelligence"


def test_the_app_placeholder_is_not_wired_live():
    """The name collision with the SEO Studio tool above is real but the two are
    unrelated registries -- this guards against a future edit accidentally
    wiring the wrong one live, which would open the tool's cross-client picker
    to any signed-in Google account with no staff review in between."""
    agent = appmod.APP_AGENTS_BY_SLUG.get(_APP_SLUG)
    assert agent is not None
    assert not agent.get("seo_slug") and not agent.get("external_url"), (
        "the /app placeholder got connected live, bypassing the request queue")


def test_the_app_placeholder_now_accepts_requests():
    agent = appmod.APP_AGENTS_BY_SLUG.get(_APP_SLUG)
    assert not agent.get("no_request"), (
        "the card still refuses requests for a tool that now really exists")


def test_requesting_it_on_app_actually_logs_and_notifies(client, monkeypatch):
    logged = {}
    monkeypatch.setattr(appmod, "_log_agent_access_request",
                        lambda user, agent, message="": logged.setdefault("ok", True))
    monkeypatch.setattr(appmod, "_agent_access_request_to_slack", lambda *a, **k: True)
    r = client.post("/app/%s/request-access" % _APP_SLUG, json={})
    assert r.status_code == 200
    assert r.get_json()["ok"] is True
    assert logged.get("ok") is True, "no_request must not have silently blocked this"


def test_the_locked_card_offers_request_access_not_just_view_details(client):
    """app.html's own rule: a locked card reads "View details" instead of
    "Request access" exactly when no_request is set. This is the reader-facing
    proof that removing the flag actually changed what the button says.

    The sidebar (app_base.html) also links every agent, connected or not, by
    the same /app/<slug> href, so the "More agents" grid has to be isolated
    first or a match there would silently grade the sidebar link instead."""
    body = client.get("/app").get_data(as_text=True)
    locked_grid = body.split('class="cards cards-locked"', 1)[1]
    card = locked_grid.split('href="/app/%s"' % _APP_SLUG, 1)[1].split("</a>", 1)[0]
    assert "Request access" in card
    assert "View details" not in card
