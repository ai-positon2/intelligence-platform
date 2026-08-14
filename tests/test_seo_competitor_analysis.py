"""Competitor Analysis went live on the SEO Studio SERP app at /competitor-analysis
(confirmed live 2026-08-14: real SEMrush data, a client picker, traffic/keyword/
backlink/authority comparisons against competitors) and this pins its wiring into
/p2/seo, the internal staff SEO Suite listing -- a different registry from the
dormant "Competitor Analysis" entry in APP_AGENTS (slug competitor-seo-intelligence,
still no_request/lock_label on /app), which this change does not touch.

_seo_tools() prefers a live /tools.json manifest from the SERP app and only falls
back to _SEO_TOOLS_FALLBACK when that fetch fails (it currently always fails: the
SERP app is a client-routed SPA with no such endpoint), so the fallback list is
the actual, only source of truth for what /p2/seo can show today.
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


def test_it_does_not_touch_the_dormant_app_agents_entry_of_the_same_name():
    """The name collision is real but the two are unrelated registries -- this
    guards against a future edit accidentally wiring the wrong one live, which
    would jump the gun on the /app entry's own "needs extensive testing" gate."""
    dormant = appmod.APP_AGENTS_BY_SLUG.get("competitor-seo-intelligence")
    assert dormant is not None
    assert not dormant.get("seo_slug") and not dormant.get("external_url"), (
        "the /app placeholder got connected as a side effect of this change")


def test_an_unknown_seo_slug_still_404s(client):
    r = client.get("/p2/seo/not-a-real-tool")
    assert r.status_code == 404
