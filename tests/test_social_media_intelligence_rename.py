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
_SLUG = "social-creative-intelligence"


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


def test_the_only_place_that_still_says_the_old_name_is_the_alias_table():
    src = _read("app.py")
    start = src.index("_PAGE_LABEL_ALIASES = (")
    end = src.index("\ndef _page_label(", start)
    stray = [n for n, line in enumerate(src.splitlines(), 1)
             if _OLD in line and not (start <= src.index(line) < end)]
    assert not stray, "app.py names the old agent outside the alias table, at lines %s" % stray


def test_the_registry_entry_carries_the_new_name():
    e = _entry()
    assert e["name"] == _NEW
    assert "Creative" not in e["tagline"]
    assert "Creative" not in e["pill1"]


def test_the_page_names_itself_the_new_way_in_all_four_places():
    page = _read("templates", "social_creative_intelligence.html")
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
    page = _read("templates", "social_creative_intelligence.html")
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

def test_the_slug_and_the_modules_are_untouched():
    """Renaming the route would break every existing link and bookmark, and
    renaming the tracker modules or the Postgres tables would be churn with
    a migration attached. The display name is the only thing that changed."""
    assert any(r.rule == "/p2/b2b-agents/%s" % _SLUG for r in appmod.app.url_map.iter_rules())
    assert os.path.exists(os.path.join(_ROOT, "templates", "social_creative_intelligence.html"))
    assert os.path.exists(os.path.join(_ROOT, "tracker", "sci_pipeline.py"))


def test_the_precise_measurement_labels_are_left_alone():
    """"Creative described" counts posts with a real vision reading and
    "Recurring creative themes" charts words from those readings. Both are
    exactly what they say; broadening them to "social" would make them less
    true, not more general."""
    page = _read("templates", "social_creative_intelligence.html")
    assert "Creative described" in page
    assert "Recurring creative themes" in page
