"""The "GTM" section was renamed to "B2B Agents".

Two things have to survive a rename like this, and neither is the new name
itself, which is easy. What breaks quietly:

  1. Links that already exist. Bookmarks, browser history, links pasted into
     Slack, and the previous JS bundle a browser is still holding in the minutes
     after a deploy. Every old /p2/gtm/* path therefore still resolves, and it
     does so with a 308 rather than a 301 because several of those paths are
     POST endpoints: a 301 lets the browser retry them as GET, silently dropping
     the body.

  2. Analytics already written under the old name. Page views are appended to a
     sheet with whatever title and path the page had at the time, and every "top
     pages" view groups by that string, so without folding the old label into
     the new one a rename forks one page into two rows that each undercount.
     Nothing errors; the numbers just quietly stop matching reality.

Also pinned here: the two meanings of "GTM" that must NOT be renamed, because
"GTM" in an integrations list is Google Tag Manager, not this section.
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
    assert client.get("/p2/b2b-agents").status_code == 200


def test_the_page_says_b2b_agents_not_gtm(client):
    body = client.get("/p2/b2b-agents").get_data(as_text=True)
    assert "B2B Agents" in body
    # The CSS hook (card-gtm) and bucket keys are allowed to keep the old token;
    # what must not survive is the NAME shown to a reader.
    assert ">GTM<" not in body


def test_the_hub_card_is_renamed_and_points_at_the_new_path(client):
    body = client.get("/p2/hub").get_data(as_text=True)
    assert '<div class="card-title">B2B Agents</div>' in body
    assert 'href="/p2/b2b-agents"' in body
    assert '<div class="card-title">GTM</div>' not in body


@pytest.mark.parametrize("path", [
    "/p2/b2b-agents",
    "/p2/b2b-agents/company-people-intelligence",
    "/p2/b2b-agents/anonymous-visitors",
    "/p2/b2b-agents/linkedin-intelligence",
    "/p2/b2b-agents/ad-intelligence",
    "/p2/b2b-agents/linkedin-strategy-researcher",
    "/p2/b2b-agents/gentle-dental-slot-checker",
])
def test_every_renamed_page_is_routed(path):
    """Registered, not necessarily 200 (some need live upstreams). A missing
    rule is the regression this catches."""
    rules = {str(r) for r in appmod.app.url_map.iter_rules()}
    assert path in rules


# ── Old links keep working ──────────────────────────────────────────────────

def test_the_old_section_root_redirects(client):
    r = client.get("/p2/gtm")
    assert r.status_code == 308
    assert r.headers["Location"].endswith("/p2/b2b-agents")


@pytest.mark.parametrize("rest", [
    "company-people-intelligence",
    "anonymous-visitors",
    "linkedin-intelligence",
    "ad-intelligence",
    "company-people-intelligence/history/7",
])
def test_any_old_sub_path_redirects(client, rest):
    """One catch-all covers the whole old tree, so a route added later inherits
    the alias instead of quietly 404ing for anyone with an old link."""
    r = client.get("/p2/gtm/" + rest)
    assert r.status_code == 308
    assert r.headers["Location"].endswith("/p2/b2b-agents/" + rest)


def test_a_query_string_survives_the_redirect(client):
    r = client.get("/p2/gtm/linkedin-intelligence/data?fresh=1")
    assert r.headers["Location"].endswith("/p2/b2b-agents/linkedin-intelligence/data?fresh=1")


def test_a_post_keeps_its_method_and_body(client):
    """The reason this is 308 and not 301. A browser still holding the previous
    JS bundle POSTs to the old URL; a 301 would retry it as a GET and lose the
    question the user just typed."""
    r = client.post("/p2/gtm/company-people-intelligence/chat", json={"message": "x"})
    assert r.status_code == 308, "301 would let the browser downgrade this to GET"
    assert r.headers["Location"].endswith("/p2/b2b-agents/company-people-intelligence/chat")


def test_a_delete_keeps_its_method(client):
    r = client.delete("/p2/gtm/company-people-intelligence/history/1")
    assert r.status_code == 308


def test_the_older_ppc_links_now_land_on_the_new_name(client):
    """These were already redirecting to /p2/gtm; they must not now redirect to
    a path that no longer exists."""
    r = client.get("/ppc")
    assert r.headers["Location"].endswith("/p2/b2b-agents")


def test_the_ad_intel_bundle_asset_paths_still_serve():
    """The built React app requests these by absolute path, baked in at build
    time, so they must keep SERVING rather than redirecting. The new paths are
    added alongside instead of replacing them."""
    rules = {str(r) for r in appmod.app.url_map.iter_rules()}
    assert "/gtm/ad-intelligence/assets/<path:filename>" in rules
    assert "/b2b-agents/ad-intelligence/assets/<path:filename>" in rules


# ── Analytics written under the old name ────────────────────────────────────

def test_the_old_page_title_folds_into_the_new_one():
    assert appmod._page_label("GTM Dashboards") == "B2B Agents Dashboards"


def test_an_old_recorded_path_folds_too():
    assert (appmod._page_label("/p2/gtm/company-people-intelligence")
            == "/p2/b2b-agents/company-people-intelligence")


def test_an_unrelated_label_is_untouched():
    assert appmod._page_label("SEO Studio") == "SEO Studio"


@pytest.mark.parametrize("junk", ["", None, 0])
def test_a_missing_label_does_not_crash(junk):
    """A blank cell in the sheet is common. Every falsy value, including a 0
    that a spreadsheet reader can hand back for an empty cell, normalizes to an
    empty string rather than raising inside a dashboard aggregation."""
    assert appmod._page_label(junk) == ""


def test_top_pages_would_not_fork_across_the_rename():
    """The actual failure mode, stated as a test: two rows recorded either side
    of the rename have to count as one page."""
    from collections import Counter
    rows = ["GTM Dashboards"] * 3 + ["B2B Agents Dashboards"] * 2
    folded = Counter(appmod._page_label(r) for r in rows)
    assert folded == {"B2B Agents Dashboards": 5}, "a rename must not split its own history"


# ── The two things named GTM that are NOT this section ──────────────────────

def test_google_tag_manager_is_not_renamed():
    """"GTM" in an integrations list is Google Tag Manager, which is how the
    visitor script gets installed. Renaming it would be wrong, not just odd."""
    import pathlib
    agents = pathlib.Path(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "templates", "agents.html")).read_text()
    assert "Your website (GTM)" in agents


def test_the_tag_manager_detector_still_matches_on_gtm():
    from visitor_intelligence import free_enrich
    keys = " ".join(free_enrich.__dict__.get("_TECH_PATTERNS", {}).keys()) \
        if isinstance(free_enrich.__dict__.get("_TECH_PATTERNS"), dict) else ""
    src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "visitor_intelligence", "free_enrich.py")
    with open(src) as fh:
        body = fh.read()
    assert "Google Analytics / GTM" in body or "GTM" in keys
