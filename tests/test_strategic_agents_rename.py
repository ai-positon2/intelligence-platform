"""The "B2B Agents" section was renamed to "Strategic Agents", 2026-09-11.

Second rename of this section (GTM -> B2B Agents -> Strategic Agents; the
first hop is covered by test_b2b_agents_rename.py). Two things have to
survive a rename like this, and neither is the new name itself:

  1. Links that already exist. Bookmarks, browser history, links pasted into
     Slack, and the previous JS bundle a browser is still holding in the
     minutes after a deploy. Every old /p2/b2b-agents/* path therefore still
     resolves, with a 308 rather than a 301 because several of those paths
     are POST endpoints: a 301 lets the browser retry them as GET, silently
     dropping the body.

  2. Analytics already written under the old name. Page views are appended to
     a sheet with whatever title and path the page had at the time, and every
     "top pages" view groups by that string, so without folding the old label
     into the new one a rename forks one page into two rows that each
     undercount. Nothing errors; the numbers just quietly stop matching
     reality. test_b2b_agents_rename.py proves this fold reaches all the way
     back to the ORIGINAL "GTM" name too, not just the immediately-prior one.

Internal identifiers are deliberately left alone: templates/b2b_agents.html
(the file), the b2b_agents() view function, the "card-gtm" CSS hook, the
sci_*/lps_*-style module names elsewhere in this repo. What must not survive
is the NAME shown to a reader -- the URL a reader types or clicks, and the
text a reader sees.
"""

import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402


@pytest.fixture
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    return c


# ── The new canonical paths ─────────────────────────────────────────────────

def test_the_section_is_served_at_its_new_path(client):
    assert client.get("/p2/strategic-agents").status_code == 200


def test_the_page_says_strategic_agents_not_b2b_agents(client):
    body = client.get("/p2/strategic-agents").get_data(as_text=True)
    assert "Strategic Agents" in body
    assert ">B2B Agents<" not in body
    assert ">GTM<" not in body


def test_the_hub_card_is_renamed_and_points_at_the_new_path(client):
    body = client.get("/p2/hub").get_data(as_text=True)
    assert '<div class="card-title">Strategic Agents</div>' in body
    assert 'href="/p2/strategic-agents"' in body
    assert '<div class="card-title">B2B Agents</div>' not in body


@pytest.mark.parametrize("path", [
    "/p2/strategic-agents",
    "/p2/strategic-agents/company-people-intelligence",
    "/p2/strategic-agents/anonymous-visitors",
    "/p2/strategic-agents/linkedin-intelligence",
    "/p2/strategic-agents/ad-intelligence",
    "/p2/strategic-agents/linkedin-strategy-researcher",
    "/p2/strategic-agents/42-north-dental-slot-checker",
    "/p2/strategic-agents/job-change-alert",
    "/p2/strategic-agents/social-media-intelligence",
    "/p2/strategic-agents/event-conference-intelligence",
])
def test_every_agent_page_is_routed_at_the_new_prefix(path):
    """Registered, not necessarily 200 (some need live upstreams or POST
    bodies). A missing rule is the regression this catches."""
    rules = {str(r) for r in appmod.app.url_map.iter_rules()}
    assert path in rules


# ── Old links keep working ──────────────────────────────────────────────────

def test_the_old_section_root_redirects(client):
    r = client.get("/p2/b2b-agents")
    assert r.status_code == 308
    assert r.headers["Location"].endswith("/p2/strategic-agents")


@pytest.mark.parametrize("rest", [
    "company-people-intelligence",
    "anonymous-visitors",
    "linkedin-intelligence",
    "ad-intelligence",
    "company-people-intelligence/history/7",
])
def test_any_old_sub_path_redirects(client, rest):
    """One catch-all covers the whole old tree, so a route added later
    inherits the alias instead of quietly 404ing for anyone with an old
    link."""
    r = client.get("/p2/b2b-agents/" + rest)
    assert r.status_code == 308
    assert r.headers["Location"].endswith("/p2/strategic-agents/" + rest)


def test_a_query_string_survives_the_redirect(client):
    r = client.get("/p2/b2b-agents/linkedin-intelligence/data?fresh=1")
    assert r.headers["Location"].endswith("/p2/strategic-agents/linkedin-intelligence/data?fresh=1")


def test_a_post_keeps_its_method_and_body(client):
    """The reason this is 308 and not 301. A browser still holding the
    previous JS bundle POSTs to the old URL; a 301 would retry it as a GET
    and lose the question the user just typed."""
    r = client.post("/p2/b2b-agents/company-people-intelligence/chat", json={"message": "x"})
    assert r.status_code == 308, "301 would let the browser downgrade this to GET"
    assert r.headers["Location"].endswith("/p2/strategic-agents/company-people-intelligence/chat")


def test_a_delete_keeps_its_method(client):
    r = client.delete("/p2/b2b-agents/company-people-intelligence/history/1")
    assert r.status_code == 308


def test_the_bare_hub_path_with_trailing_slash_redirects_too(client):
    r = client.get("/p2/b2b-agents/")
    assert r.status_code == 308
    assert r.headers["Location"].endswith("/p2/strategic-agents")


def test_the_ad_intel_bundle_asset_paths_still_serve_at_every_generation():
    """The built React app requests one absolute path, baked in at build
    time; whichever generation's path index.html currently references must
    keep SERVING. Every prior generation's path is kept alongside it rather
    than being replaced, in case an older cached copy of index.html is still
    in a browser somewhere."""
    rules = {str(r) for r in appmod.app.url_map.iter_rules()}
    assert "/p2/strategic-agents/ad-intelligence/assets/<path:filename>" in rules
    assert "/p2/b2b-agents/ad-intelligence/assets/<path:filename>" in rules
    assert "/b2b-agents/ad-intelligence/assets/<path:filename>" in rules
    assert "/gtm/ad-intelligence/assets/<path:filename>" in rules


def test_an_old_asset_request_still_serves_rather_than_redirecting(client):
    """The catch-all above must not shadow these: Werkzeug should keep
    preferring the more specific asset route. A 404 (file not found) proves
    it was matched and served, not redirected (which would be a 308)."""
    r = client.get("/p2/b2b-agents/ad-intelligence/assets/does-not-exist.js")
    assert r.status_code == 404


def test_the_current_bundle_references_the_new_asset_path():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    html = open(os.path.join(root, "ad_intelligence", "index.html"), encoding="utf-8").read()
    assert "/p2/strategic-agents/ad-intelligence/assets/" in html
    assert "/p2/b2b-agents/ad-intelligence/assets/" not in html


def test_the_older_ppc_links_now_land_on_the_new_name(client):
    """These were already redirecting to /p2/gtm, then /p2/b2b-agents; they
    must not now redirect to a path that no longer exists."""
    r = client.get("/ppc")
    assert r.headers["Location"].endswith("/p2/strategic-agents")


# ── Analytics written under the old name ────────────────────────────────────

def test_the_old_page_title_folds_into_the_new_one():
    assert appmod._page_label("B2B Agents Dashboards") == "Strategic Agents Dashboards"


def test_an_old_recorded_path_folds_too():
    assert (appmod._page_label("/p2/b2b-agents/company-people-intelligence")
            == "/p2/strategic-agents/company-people-intelligence")


def test_a_descendant_alias_recorded_under_the_old_prefix_still_folds():
    """A sub-agent's OWN historical rename (e.g. gentle-dental-slot-checker ->
    42-north-dental-slot-checker) produced rows under
    /p2/b2b-agents/42-north-dental-slot-checker before this section-level
    rename existed. Those rows must fold all the way to the current prefix
    too, not get stranded one rename behind."""
    assert (appmod._page_label("/p2/b2b-agents/gentle-dental-slot-checker")
            == "/p2/strategic-agents/42-north-dental-slot-checker")


def test_an_unrelated_label_is_untouched():
    assert appmod._page_label("SEO Studio") == "SEO Studio"


def test_top_pages_would_not_fork_across_the_rename():
    """The actual failure mode, stated as a test: two rows recorded either
    side of the rename have to count as one page."""
    from collections import Counter
    rows = ["B2B Agents Dashboards"] * 3 + ["Strategic Agents Dashboards"] * 2
    folded = Counter(appmod._page_label(r) for r in rows)
    assert folded == {"Strategic Agents Dashboards": 5}, "a rename must not split its own history"
