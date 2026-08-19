"""Ad Intelligence is a built React app (apps/ad-intelligence/) whose compiled
index.html hardcodes absolute URLs for its JS/CSS bundle and favicon. Those
URLs are baked in at `npm run build` time from vite.config.ts's `base`, they
are NOT relative to wherever the page happens to be loaded from.

This broke in production: the page rendered a blank/broken app because the
built index.html referenced /assets/index-*.js (site root) while Flask only
served that file under a path-prefixed route (originally
/gtm/ad-intelligence/assets/..., later needed at
/p2/b2b-agents/ad-intelligence/assets/...). A prior fix hand-patched the
committed index.html to the right prefix, but the CI "rebuild frontend"
workflow (.github/workflows/build-frontend.yml) regenerates index.html from
vite.config.ts on every push to apps/ad-intelligence/**, silently reverting
any hand-edit that isn't also in vite.config.ts's `base`.

So the only regression test that actually holds is one that reads whatever
index.html currently says and confirms Flask serves exactly those URLs,
not one that hardcodes an expected path.
"""

import os
import re
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402

PAGE_PATH = "/p2/b2b-agents/ad-intelligence"


@pytest.fixture
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    return c


def _referenced_urls(html):
    urls = re.findall(r'(?:src|href)="(/[^"]+)"', html)
    # Only ones the page load itself depends on: same-origin JS/CSS/icon, not
    # the Google-fonts stylesheets or any anchor hrefs to other pages.
    return [u for u in urls if u.startswith("/p2/b2b-agents/ad-intelligence/")]


def test_the_page_itself_loads(client):
    r = client.get(PAGE_PATH)
    assert r.status_code == 200


def test_every_same_origin_asset_the_page_references_actually_resolves(client):
    body = client.get(PAGE_PATH).get_data(as_text=True)
    referenced = _referenced_urls(body)
    # If this is empty the regex/markup assumption above is stale, not that
    # there's nothing to check.
    assert referenced, "expected the built index.html to reference its own JS/CSS/favicon"
    for url in referenced:
        r = client.get(url)
        assert r.status_code == 200, f"{url} is referenced by index.html but not served (would render a blank page)"


def test_the_script_and_stylesheet_are_specifically_covered():
    """Guards against the referenced-urls check above passing vacuously
    because the JS/CSS tags got dropped from the markup entirely."""
    html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                              "ad_intelligence", "index.html"), encoding="utf-8").read()
    assert re.search(r'<script[^>]+src="/p2/b2b-agents/ad-intelligence/assets/[^"]+\.js"', html)
    assert re.search(r'<link[^>]+href="/p2/b2b-agents/ad-intelligence/assets/[^"]+\.css"', html)


def test_vite_base_matches_the_flask_mount_path():
    """The root cause: vite.config.ts's `base` must equal the Flask route
    this app is served from, since every rebuild bakes `base` into
    index.html's asset URLs and Flask must have a route to match."""
    cfg = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "apps", "ad-intelligence", "vite.config.ts"), encoding="utf-8").read()
    assert "base: '/p2/b2b-agents/ad-intelligence/'" in cfg


# ── Favicon must match every other page, not this app's own bundled icon ────
#
# This app used to ship its own favicon.svg (apps/ad-intelligence/public/) and
# reference it as href="/favicon.svg", which vite's `base` then rewrote to
# /p2/b2b-agents/ad-intelligence/favicon.svg at build time. That routed to a
# different icon than every other page's plain /favicon.svg, so the browser
# tab looked wrong specifically on this page. Fixed by dropping the app's own
# public/favicon.svg entirely, so nothing in apps/ad-intelligence/public/
# collides with the href and vite has nothing local to rewrite it against,
# leaving the reference as the site-wide root path.

def test_the_favicon_reference_is_the_plain_site_root_path(client):
    body = client.get(PAGE_PATH).get_data(as_text=True)
    m = re.search(r'<link[^>]+rel="icon"[^>]+href="([^"]+)"', body)
    assert m, "expected an <link rel=\"icon\"> tag in index.html"
    href = m.group(1)
    assert href == "/favicon.svg?v=4", (
        f"favicon href is {href!r}; every other page on the site uses "
        "/favicon.svg?v=4 (see templates/*.html) -- this one has drifted "
        "and will show a different browser-tab icon"
    )


def test_the_favicon_byte_matches_the_site_wide_static_file(client):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    served = client.get("/favicon.svg?v=4").get_data()
    on_disk = open(os.path.join(root, "static", "favicon.svg"), "rb").read()
    assert served == on_disk


def test_the_app_no_longer_bundles_its_own_favicon():
    """Guards against a future edit re-adding apps/ad-intelligence/public/
    favicon.svg, which would make vite base-prefix the href again and bring
    back the mismatched icon."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert not os.path.exists(os.path.join(root, "apps", "ad-intelligence", "public", "favicon.svg"))
    assert not os.path.exists(os.path.join(root, "ad_intelligence", "favicon.svg"))


# ── Header must use the Arena logo (not a lightning-bolt icon) and the
#    current "B2B Agents" naming (not the stale "PPC" name it shipped with) ──
#
# App.tsx's PlatformBar (its site-wide nav strip), sidebar logo, and page
# header all used a lucide-react `Zap` (lightning bolt) icon as a stand-in
# brand mark, and PlatformBar's breadcrumb hardcoded "Hub › PPC › Ad
# Intelligence" from when this section was still called PPC. Both went stale:
# every other page uses the real static/logo-lockup.svg ("arena by
# Position2") / static/logo-mark.svg, and the section has been "B2B Agents"
# since #22-26 above. Checked at both the TSX source (authoritative, survives
# a rebuild) and the compiled bundle actually being served (catches a source
# edit that was never rebuilt into ad_intelligence/) -- string literals like
# these survive minification intact, unlike logic/structure, so grepping the
# bundle for them is reliable here.

def _read(*parts):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return open(os.path.join(root, *parts), encoding="utf-8").read()


def test_source_has_no_lightning_bolt_icon():
    src = _read("apps", "ad-intelligence", "src", "App.tsx")
    assert "Zap" not in src, "App.tsx should no longer import or render the lucide 'Zap' (lightning bolt) icon"


def test_source_uses_the_arena_logo_files():
    src = _read("apps", "ad-intelligence", "src", "App.tsx")
    assert "/static/logo-lockup.svg" in src, "top brand slot should render the arena wordmark, like every other page"
    assert "/static/logo-mark.svg" in src, "the sidebar/header icon badges should render the arena mark"


def test_source_breadcrumb_says_b2b_agents_not_ppc():
    src = _read("apps", "ad-intelligence", "src", "App.tsx")
    assert 'href="/p2/b2b-agents"' in src
    assert '>B2B Agents<' in src
    assert '>PPC<' not in src, "breadcrumb still shows the pre-rename section label"
    assert 'href="/ppc"' not in src, "breadcrumb link should point straight at the canonical path, not the legacy alias"


def test_compiled_bundle_matches_the_source_fix():
    """Guards against editing App.tsx without rebuilding+copying the output
    into ad_intelligence/ -- the exact gap that caused the original asset-path
    bug this test file is named after."""
    assets_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ad_intelligence", "assets")
    js_files = [f for f in os.listdir(assets_dir) if f.endswith(".js")]
    assert js_files, "expected a built JS bundle in ad_intelligence/assets/"
    bundle = open(os.path.join(assets_dir, js_files[0]), encoding="utf-8").read()
    assert "/static/logo-lockup.svg" in bundle
    assert "/static/logo-mark.svg" in bundle
    assert "B2B Agents" in bundle
    assert ">PPC<" not in bundle
