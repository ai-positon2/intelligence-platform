"""The scroll rail on /p2/b2b-agents.

gtm.css zeroes the native scrollbar on every element it touches, so this page
ships its own hairline rail instead. Its behaviour (sizing, dragging, going
quiet, staying off a page that does not scroll) is exercised in a browser; what
is checked here is the wiring, which is the part that breaks silently: a
renamed file or a dropped <link> leaves the page loading fine and the rail
simply absent, with nothing in any log to say so.
"""

import os
import re
import sys

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CSS = "static/css/scroll-rail.css"
_JS = "static/js/scroll-rail.js"


def _client(email="staff@position2.com"):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": email, "name": "T"}
    return c


def _page(monkeypatch):
    # Via monkeypatch, never by assigning onto the module: this is the same
    # process every other test file runs in, and a permanent stub here is a
    # stub in tests/test_tracked_company_count.py too.
    monkeypatch.setattr(appmod, "_tracked_company_floor", lambda *a, **k: 1200)
    r = _client().get("/p2/b2b-agents")
    assert r.status_code == 200, r.status_code
    return r.get_data(as_text=True)


def test_the_page_links_both_halves_of_the_rail(monkeypatch):
    """Either half alone is a silent no-op: the stylesheet without the script
    draws an empty track that never moves, the script without the stylesheet
    builds an invisible element."""
    html = _page(monkeypatch)
    assert "css/scroll-rail.css" in html
    assert "js/scroll-rail.js" in html


def test_both_rail_files_are_actually_served():
    """A <link> to a path that 404s looks identical to a working one in the
    HTML, and the page renders without complaint either way."""
    c = _client()
    for path in ("/" + _CSS, "/" + _JS):
        r = c.get(path)
        assert r.status_code == 200, "%s served %s" % (path, r.status_code)
        assert r.get_data(), "%s served an empty body" % path


def test_the_rail_script_is_deferred_so_it_finds_a_body_to_attach_to(monkeypatch):
    """It is linked in <head> and appends to document.body. Without defer it
    runs against a document that has no body yet."""
    html = _page(monkeypatch)
    tag = re.search(r"<script[^>]*js/scroll-rail\.js[^>]*>", html)
    assert tag, "no script tag for the rail"
    assert "defer" in tag.group(0), tag.group(0)


def test_the_rail_is_scoped_to_this_page_only():
    """It is a fix for one page's hidden scrollbar, not a platform-wide
    change. Picking it up elsewhere would put a second scroll indicator on
    pages that already show the native one."""
    tpl = os.path.join(_ROOT, "templates")
    linking = [n for n in os.listdir(tpl)
               if n.endswith(".html")
               and "scroll-rail" in open(os.path.join(tpl, n), encoding="utf-8").read()]
    assert linking == ["b2b_agents.html"], linking


def test_the_rail_never_takes_layout_space():
    """The page reserves no scrollbar gutter, so a rail that was not fixed and
    overlaid would reflow the whole card grid inward."""
    css = open(os.path.join(_ROOT, _CSS), encoding="utf-8").read()
    block = css[css.index(".p2-rail {"):css.index(".p2-rail.on {")]
    assert "position: fixed" in block, block
