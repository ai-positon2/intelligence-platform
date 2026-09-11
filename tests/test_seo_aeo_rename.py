"""The "SEO" section was renamed to "SEO + AEO", 2026-09-11.

First rename for this section (unlike GTM -> B2B Agents -> Strategic Agents,
there is no earlier generation to also fold forward). Two things have to
survive a rename like this, and neither is the new name itself:

  1. Links that already exist. Bookmarks, browser history, links pasted into
     Slack, and a page already open in a browser mid-session whose own JS
     pushState's /p2/seo/<tool> sub-paths as a visitor switches between the
     embedded tools (see templates/embed.html's route-change listener). Every
     old /p2/seo/* path therefore still resolves, with a 308 rather than a
     301 because a page open at the time of this deploy can still POST under
     this prefix: a 301 lets the browser retry it as GET, silently dropping
     the body.

  2. Analytics already written under the old name. Page views are appended to
     a sheet with whatever title and path the page had at the time, and every
     "top pages" view groups by that string, so without folding the old label
     into the new one a rename forks one page into two rows that each
     undercount. Nothing errors; the numbers just quietly stop matching
     reality.

Also pinned here: three unrelated meanings of "SEO" that must NOT be renamed,
because none of them name this section:

  - The public marketing catalog (templates/agents.html, served from a
    different surface) uses "SEO" as one of several independent category
    tags (SEO / GEO / Web / Signals) for individual public tools, and as a
    generic industry term throughout its competitor-comparison copy. That
    page is not part of /p2/seo-aeo and was not touched.
  - Individual tool names under this section -- "SEO & GEO Audit", "On-Page
    SEO Auditor" -- are their own identities, the same way "Ad Intelligence"
    is its own identity under Strategic Agents. Renaming the section must not
    rename its tools.
  - "competitor-seo-intelligence" on /app (a completely separate registry,
    see test_seo_competitor_analysis.py) merely contains the substring "seo"
    in its own slug; it has nothing to do with this section.
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
def client(monkeypatch):
    # The live manifest fetch always fails in this environment (no network),
    # which is also true in production today per test_seo_competitor_analysis.py
    # -- pin it explicitly so this file doesn't depend on that being unchanged.
    monkeypatch.setattr(appmod, "_SEO_MANIFEST", {"ts": 0.0, "tools": None})
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    return c


# ── The new canonical path ───────────────────────────────────────────────────

def test_the_section_is_served_at_its_new_path(client):
    assert client.get("/p2/seo-aeo").status_code == 200


def test_the_page_says_seo_plus_aeo_not_bare_seo(client):
    body = client.get("/p2/seo-aeo").get_data(as_text=True)
    assert "SEO + AEO" in body
    assert '<span class="bc-cur">SEO</span>' not in body


def test_the_hub_card_is_renamed_and_points_at_the_new_path(client):
    body = client.get("/p2/hub").get_data(as_text=True)
    assert '<div class="card-title">SEO + AEO</div>' in body
    assert 'href="/p2/seo-aeo"' in body
    assert '<div class="card-title">SEO</div>' not in body


def test_a_real_tool_slug_is_routed_at_the_new_prefix():
    rules = {str(r) for r in appmod.app.url_map.iter_rules()}
    assert "/p2/seo-aeo" in rules
    assert "/p2/seo-aeo/<tool_slug>" in rules


def test_the_tool_cards_link_to_the_new_prefix(client):
    body = client.get("/p2/seo-aeo").get_data(as_text=True)
    tool = appmod._SEO_TOOLS_FALLBACK[0]
    assert '/p2/seo-aeo/%s"' % tool["slug"] in body
    assert '/p2/seo/%s"' % tool["slug"] not in body


# ── Old links keep working ──────────────────────────────────────────────────

def test_the_old_section_root_redirects(client):
    r = client.get("/p2/seo")
    assert r.status_code == 308
    assert r.headers["Location"].endswith("/p2/seo-aeo")


def test_the_bare_path_with_trailing_slash_redirects_too(client):
    r = client.get("/p2/seo/")
    assert r.status_code == 308
    assert r.headers["Location"].endswith("/p2/seo-aeo")


def test_an_old_tool_sub_path_redirects(client):
    tool = appmod._SEO_TOOLS_FALLBACK[0]
    r = client.get("/p2/seo/%s" % tool["slug"])
    assert r.status_code == 308
    assert r.headers["Location"].endswith("/p2/seo-aeo/%s" % tool["slug"])


def test_a_query_string_survives_the_redirect(client):
    r = client.get("/p2/seo/keyword-opportunity-engine?fresh=1")
    assert r.headers["Location"].endswith("/p2/seo-aeo/keyword-opportunity-engine?fresh=1")


def test_a_post_keeps_its_method_and_body(client):
    """The reason this is 308 and not 301: a page open in a browser at deploy
    time can still POST under the old prefix, and a 301 would let the browser
    retry it as a GET, dropping the body."""
    r = client.post("/p2/seo/keyword-opportunity-engine", json={"x": 1})
    assert r.status_code == 308, "301 would let the browser downgrade this to GET"
    assert r.headers["Location"].endswith("/p2/seo-aeo/keyword-opportunity-engine")


def test_a_delete_keeps_its_method(client):
    r = client.delete("/p2/seo/keyword-opportunity-engine")
    assert r.status_code == 308


# ── The client-side route sync (embed.html) points at the new prefix ────────

def test_the_embedded_tool_page_pushes_state_to_the_new_prefix(client):
    """The embedded-tool page's own JS keeps the address bar in sync as a
    visitor switches tools inside the cross-origin iframe (see embed.html) --
    it has to construct the NEW prefix, or every tool switch after this
    rename would silently rewrite the URL back to a path that only exists as
    a redirect."""
    tool = appmod._SEO_TOOLS_FALLBACK[0]
    body = client.get("/p2/seo-aeo/%s" % tool["slug"]).get_data(as_text=True)
    assert "'/p2/seo-aeo/' + d.tool" in body
    assert "'/p2/seo/' + d.tool" not in body


# ── Analytics written under the old name ────────────────────────────────────

def test_the_old_page_title_folds_into_the_new_one():
    assert appmod._page_label("SEO Dashboards") == "SEO + AEO Dashboards"


def test_an_old_recorded_path_folds_too():
    assert (appmod._page_label("/p2/seo/keyword-opportunity-engine")
            == "/p2/seo-aeo/keyword-opportunity-engine")


def test_an_unrelated_label_is_untouched():
    assert appmod._page_label("SEO Studio") == "SEO Studio"


def test_top_pages_would_not_fork_across_the_rename():
    """The actual failure mode, stated as a test: two rows recorded either
    side of the rename have to count as one page."""
    from collections import Counter
    rows = ["SEO Dashboards"] * 4 + ["SEO + AEO Dashboards"] * 1
    folded = Counter(appmod._page_label(r) for r in rows)
    assert folded == {"SEO + AEO Dashboards": 5}, "a rename must not split its own history"


# ── The other meanings of "SEO" that are NOT this section ───────────────────

def test_the_public_agents_catalog_keeps_its_own_seo_category():
    """templates/agents.html uses "SEO" as an independent category tag
    (alongside GEO/Web/Signals) for public marketing-catalog tools -- a
    different surface with its own taxonomy, not this section."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "templates", "agents.html"), encoding="utf-8").read()
    assert '"cat": "SEO"' not in src  # rendered from Python, not literal in the template
    assert 'a.cat' in src  # confirms the category is still driven from AGENTS as before


def test_an_individual_tool_name_is_not_renamed():
    """Renaming the SECTION must not rename its TOOLS."""
    tool = next(t for t in appmod._SEO_TOOLS_FALLBACK if t["slug"] == "seo-geo-audit")
    assert tool["name"] == "SEO & GEO Audit"


def test_the_unrelated_app_registry_slug_is_untouched():
    """"competitor-seo-intelligence" merely contains the substring "seo" in
    its own slug on a completely different registry (/app, not /p2)."""
    agent = appmod.APP_AGENTS_BY_SLUG.get("competitor-seo-intelligence")
    assert agent is not None
