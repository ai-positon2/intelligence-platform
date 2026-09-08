""""Social Creative Intelligence Analyst" -> "Social Media Intelligence",
2026-09-08, because the agent had grown well past creative analysis: it
reports posting mix and cadence, engagement, messaging and tone, the Reddit
conversation about the brand, and what every image and video shows.

Two things this file is actually guarding, neither of which a normal
"does the page render" test would catch:

1. The display name lives in ELEVEN copies of the command-palette roster,
   one per template that includes it, plus the agent registry, the
   breadcrumb, the <title> and the /api/track call. This repo's agent roster
   has drifted between those lists before.
2. Page views are appended to a sheet with whatever title the page had AT
   THE TIME, and every "top pages" view groups by that string, so a rename
   silently forks one agent's history into two undercounting rows. app.py's
   _PAGE_LABEL_ALIASES exists for exactly this and has to be updated in the
   same change as the rename, not after someone notices the totals.
"""

import io
import os
import re
import sys

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OLD = "Social Creative Intelligence"
_NEW = "Social Media Intelligence"
_SLUG = "social-media-intelligence"
_OLD_SLUG = "social-creative-intelligence"


def _read(*parts):
    return io.open(os.path.join(_ROOT, *parts), encoding="utf-8").read()


def _entry():
    return [a for a in appmod.APP_AGENTS if a["slug"] == _SLUG][0]


# ── The name, everywhere ───────────────────────────────────────────────────

def test_the_old_name_is_gone_from_the_whole_repo():
    hits = []
    for base, dirs, files in os.walk(_ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules", ".venv")]
        for name in files:
            if not name.endswith((".py", ".html", ".css", ".js", ".md", ".txt", ".toml")):
                continue
            path = os.path.join(base, name)
            # This file necessarily quotes the old name to test for it.
            if os.path.abspath(path) == os.path.abspath(__file__):
                continue
            try:
                text = io.open(path, encoding="utf-8").read()
            except (UnicodeDecodeError, OSError):
                continue
            if _OLD in text:
                hits.append(os.path.relpath(path, _ROOT))
    # app.py is the one legitimate exception, checked properly below: the
    # analytics alias table has to quote the old name to fold it forward.
    assert sorted(hits) == ["app.py"], "the old agent name is still in %s" % sorted(hits)


def test_the_only_places_that_still_say_the_old_name_are_the_two_alias_tables():
    """Two tables have to quote the old name to fold it forward:
    _PAGE_LABEL_ALIASES (analytics titles and paths) and _LEGACY_AGENT_SLUGS
    (the /app 301 and every 'Agent Runs' read). Anywhere else is a miss."""
    src = _read("app.py")
    regions = []
    for opener, closer in (("_PAGE_LABEL_ALIASES = (", "\ndef _page_label("),
                           ("_LEGACY_AGENT_SLUGS = {", "\n}")):
        start = src.index(opener)
        regions.append((start, src.index(closer, start)))
    stray = []
    offset = 0
    for line in src.splitlines(keepends=True):
        if _OLD in line and not any(a <= offset < b for a, b in regions):
            stray.append(line.strip()[:90])
        offset += len(line)
    assert not stray, "app.py names the old agent outside both alias tables: %s" % stray


def test_the_registry_entry_carries_the_new_name():
    e = _entry()
    assert e["name"] == _NEW
    assert "Creative" not in e["tagline"]
    assert "Creative" not in e["pill1"]


def test_the_page_names_itself_the_new_way_in_all_four_places():
    page = _read("templates", "social_media_intelligence.html")
    assert "<title>%s, Platform</title>" % _NEW in page          # browser tab
    assert '<span class="bc-cur">%s</span>' % _NEW in page       # breadcrumb
    assert "title:'%s'" % _NEW in page                           # /api/track
    assert "{t:'%s'" % _NEW in page                              # command palette


def test_every_template_carrying_the_roster_names_it_the_same_way():
    """Eleven independent copies of one list. The failure mode is not a
    crash, it is one page in the product still calling the agent by a name
    no other page uses."""
    checked = 0
    for base, dirs, files in os.walk(os.path.join(_ROOT, "templates")):
        for name in sorted(files):
            if not name.endswith(".html"):
                continue
            text = io.open(os.path.join(base, name), encoding="utf-8").read()
            for m in re.finditer(r"\{t:'([^']*)',d:'([^']*)',u:'/p2/b2b-agents/%s'" % _SLUG, text):
                title, desc = m.group(1), m.group(2)
                assert title == _NEW, "%s calls it %r" % (name, title)
                assert "creative" not in desc.lower(), "%s still describes it as %r" % (name, desc)
                checked += 1
    assert checked >= 10, "expected the roster in at least 10 templates, found %d" % checked


# ── The analytics history must not fork ────────────────────────────────────

def test_historical_page_views_fold_into_the_new_name():
    assert appmod._page_label("Social Creative Intelligence Analyst") == _NEW
    assert appmod._page_label(_OLD) == _NEW


def test_the_alias_is_idempotent_on_the_new_name():
    """The bare-form rule is a substring match, so applying it to the name
    it produces must not mangle it into "Social Media Intelligence Analyst"
    or rewrite it twice."""
    assert appmod._page_label(_NEW) == _NEW
    assert appmod._page_label(appmod._page_label("Social Creative Intelligence Analyst")) == _NEW


def test_the_url_is_deliberately_not_aliased():
    """The slug is unchanged, so every historical row already carries the
    current path. An alias here would be rewriting correct data."""
    path = "/p2/b2b-agents/%s" % _SLUG
    assert appmod._page_label(path) == path


def test_unrelated_labels_are_untouched():
    for s in ("SEO Studio", "Contact Finder", "Event & Conference Intelligence", ""):
        assert appmod._page_label(s) == s


# ── The copy says what the agent actually does ─────────────────────────────

def test_the_registry_copy_is_no_longer_only_about_creative():
    """The rename was asked for because the description undersold the agent.
    A rename that left the copy saying "look at every image and video to
    report what the creative shows" would have missed the point of it."""
    e = _entry()
    blob = " ".join([e["lead"]] + [t["d"] for t in e["trips"]]).lower()
    for topic in ("engagement", "messaging", "tone", "reddit"):
        assert topic in blob, "the registry copy never mentions %s" % topic


def test_the_registry_copy_names_every_platform_it_actually_covers():
    """It listed six and covers seven: Reddit was missing, which is also the
    one platform the agent reads even when the company has no account there."""
    from tracker import sci_identify
    lead = _entry()["lead"].lower()
    for platform in sci_identify.PLATFORMS:
        assert platform in lead, "the lead never names %s" % platform


def test_the_page_hero_is_no_longer_only_about_creative():
    page = _read("templates", "social_media_intelligence.html")
    hero = page[page.index('<div class="eyebrow">'):page.index('<div class="ph-platforms"')]
    low = hero.lower()
    assert "creative analysis" not in low
    assert "engagement" in low and "messaging" in low


def test_the_listing_card_says_seven_platforms_not_six():
    card = _read("templates", "b2b_agents.html")
    block = card[card.index('class="dash-card active c-sci"'):]
    block = block[:block.index("card-footer")]
    assert "seven platforms" in block
    assert "six platforms" not in block


# ── What must NOT have been renamed ────────────────────────────────────────

def test_the_precise_measurement_labels_are_left_alone():
    """"Creative described" counts posts with a real vision reading and
    "Recurring creative themes" charts words from those readings. Both are
    exactly what they say; broadening them to "social" would make them less
    true, not more general."""
    page = _read("templates", "social_media_intelligence.html")
    assert "Creative described" in page
    assert "Recurring creative themes" in page


# ── The slug moved too (2026-09-08), after the user finished testing ───────
#
# The slug is a PERSISTED key in three places, not just a URL: the 'Agent
# Runs' sheet rows, the 'Agent Access Requests' rows, and every page-view
# row's path. All three have to be aliased in the same change, or the move
# silently resets run counts, re-offers "Request access" to people who
# already asked, and forks the traffic history.

def test_the_new_url_serves_the_page():
    rules = {r.rule for r in appmod.app.url_map.iter_rules()}
    assert "/p2/b2b-agents/%s" % _SLUG in rules


def test_the_old_page_url_permanently_redirects_to_the_new_one():
    c = appmod.app.test_client()
    r = c.get("/p2/b2b-agents/%s" % _OLD_SLUG, follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["Location"].endswith("/p2/b2b-agents/%s" % _SLUG)


def test_the_redirect_shim_carries_no_auth_decorator_of_its_own():
    """Same rule as the nine legacy /p2/admin/* shims: the gate answers at
    the destination. A logged-out visitor on an old link should land on the
    new URL and be sent to login from there, not be bounced from the shim."""
    c = appmod.app.test_client()
    r = c.get("/p2/b2b-agents/%s" % _OLD_SLUG, follow_redirects=False)
    assert r.status_code == 301  # not 302-to-login


def test_the_four_api_paths_answer_on_both_slugs():
    """Deliberately NOT redirects. A 301 is not reliably replayed as a POST
    (that would break /analyze), and a page already open in a browser when
    this deploys is still polling the old paths from the BASE baked into the
    JS it loaded. Answering both keeps a mid-analysis run alive."""
    rules = {r.rule for r in appmod.app.url_map.iter_rules()}
    for tail in ("/search", "/analyze", "/runs/<int:run_id>/status", "/runs/<int:run_id>"):
        for slug in (_SLUG, _OLD_SLUG):
            assert "/p2/b2b-agents/%s%s" % (slug, tail) in rules, (slug, tail)


def test_the_api_paths_are_not_redirects():
    """If the old API paths were shims, /analyze would break on POST."""
    c = appmod.app.test_client()
    r = c.post("/p2/b2b-agents/%s/analyze" % _OLD_SLUG, json={}, follow_redirects=False)
    assert r.status_code != 301


def test_the_page_javascript_calls_the_new_paths():
    page = _read("templates", "social_media_intelligence.html")
    assert "var BASE = '/p2/b2b-agents/%s'" % _SLUG in page


def test_run_counts_and_caps_still_see_runs_logged_under_the_old_slug():
    """Un-aliased, a renamed agent's run history vanishes from the cap and
    from admin usage, which also silently hands everyone a fresh quota."""
    assert appmod._canonical_agent_slug(_OLD_SLUG) == _SLUG
    assert appmod._canonical_agent_slug(_SLUG) == _SLUG


def test_the_access_request_dedupe_reads_through_the_slug_alias(monkeypatch):
    """It did not, before this change: a rename reset everyone's "Request
    sent" state and let a duplicate request through. Latent for the three
    renames already in _LEGACY_AGENT_SLUGS; this would have been the fourth."""
    monkeypatch.setattr(appmod, "_agent_access_requests_raw",
                        lambda *a, **k: [{"email": "someone@example.com", "slug": _OLD_SLUG}])
    assert appmod._agent_access_requested_slugs("someone@example.com") == {_SLUG}


def test_page_view_history_folds_on_the_path_axis_too():
    old = "/p2/b2b-agents/%s" % _OLD_SLUG
    new = "/p2/b2b-agents/%s" % _SLUG
    assert appmod._page_label(old) == new
    assert appmod._page_label(old + "/runs/4") == new + "/runs/4"
    assert appmod._page_label(new) == new       # idempotent


def test_the_registry_slug_is_the_new_one():
    assert _entry()["slug"] == _SLUG


def test_nothing_still_points_at_the_old_slug_outside_the_shims():
    """Templates, CSS and the tracker modules must all name the new path.
    Only app.py is allowed to mention the old one, and only in its shims
    and alias tables."""
    hits = []
    for base, dirs, files in os.walk(_ROOT):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules",
                                                ".venv", ".pytest_cache")]
        for name in files:
            if not name.endswith((".html", ".css", ".js")) and not (
                    name.endswith(".py") and "tracker" in base):
                continue
            text = io.open(os.path.join(base, name), encoding="utf-8").read()
            if _OLD_SLUG in text:
                hits.append(os.path.relpath(os.path.join(base, name), _ROOT))
    assert not hits, "these still point at the old slug: %s" % sorted(hits)


def test_the_renamed_files_are_where_the_route_expects_them():
    assert os.path.exists(os.path.join(_ROOT, "templates", "social_media_intelligence.html"))
    assert os.path.exists(os.path.join(_ROOT, "static", "css", "social_media_intelligence.css"))
    page = _read("templates", "social_media_intelligence.html")
    assert "/static/css/social_media_intelligence.css" in page


def test_the_tracker_modules_and_tables_are_deliberately_untouched():
    """sci_* modules and sci_* Postgres tables keep their names: renaming
    them buys nothing and costs a migration on live run history."""
    assert os.path.exists(os.path.join(_ROOT, "tracker", "sci_pipeline.py"))
    store = io.open(os.path.join(_ROOT, "tracker", "sci_store.py"), encoding="utf-8").read()
    for table in ("sci_runs", "sci_platform_runs", "sci_posts"):
        assert "CREATE TABLE IF NOT EXISTS %s" % table in store
