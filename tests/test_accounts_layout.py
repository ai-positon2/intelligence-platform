"""ABM Signal Tracker's account picker (/p2/abm-signal-tracker/accounts, templates/accounts.html).

Reported live via screenshot: with exactly three accounts (Healthcare, CSG,
NorthStar Anesthesia), the card grid showed two cards on the first row and the
third orphaned alone on a second row with a large empty gap beside it. Root
causes, both fixed the same session:

1. `.grid` used `minmax(380px,460px)` columns inside a `max-width:1000px` box,
   which only ever has room for 2 of those columns -- any card count that
   isn't a multiple of 2 leaves the last row's single card left-anchored with
   an empty second column beside it. Switched to `minmax(320px,1fr)` inside a
   wider box, so auto-fit settles on however many columns actually fit and
   stretches them to share the row evenly; 3 columns hold together from
   ~1000px of available width upward, which covers the account count as of
   this fix.

2. A second, independent bug surfaced while fixing the first: raising the
   grid's max-width exposed a pre-existing latent overflow. `.main` (a
   column-flex item of `body`, with no `width` of its own) was silently
   rendering ~40% wider than the viewport, because the LUX kit's scrolling
   marquee band -- injected as a child of `.main`, a non-wrapping
   `width:max-content` track meant to scroll infinitely inside an
   `overflow:hidden` wrapper -- was still contributing its full un-clipped
   text width to `.main`'s automatic min-width fallback. Invisible before
   because `body{overflow-x:hidden}` clips the resulting overflow rather than
   showing a scrollbar, and because the old, narrower grid never grew close
   enough to that inflated width to visibly overflow the true viewport.
   Confirmed live (getBoundingClientRect/scrollWidth in a real browser,
   toggling the marquee band on and off) before landing the fix: an explicit
   `width:100%` on `.main` pins it to its real containing block.

3. `_build_account_card`'s `thumb_map` (app.py) only had gradient entries for
   the Healthcare and CSG accent colors; NorthStar's accent fell through to
   the flat grey placeholder gradient meant for a not-yet-configured account,
   which is also why its card looked visually duller than its siblings in the
   reported screenshot.

These are source/logic-level regression tests: no headless browser here, so
the two CSS-side facts are pinned by inspecting the template's own text for
the specific properties that fix them, rather than actually rendering and
measuring layout (that verification was done live in a real browser during
this fix, not repeatable in this suite).
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
_TEMPLATE = os.path.join(_ROOT, "templates", "accounts.html")


def _template_text():
    with open(_TEMPLATE, encoding="utf-8") as f:
        return f.read()


# ── Card thumb gradients: every configured account gets a real one ──────────

def test_every_account_gets_a_non_placeholder_thumb_gradient():
    """The flat grey fallback (`#0d0f17`/`#1a1d27`) is meant for an account
    whose accent color was never given a matching gradient -- it should never
    fire for one of the three accounts this page actually ships today."""
    placeholder_stops = ("#0d0f17", "#1a1d27")
    for account_id, cfg in appmod.ACCOUNTS.items():
        html = appmod._build_account_card(account_id, cfg)
        m = re.search(r"--thumb:([^;]+);", html)
        assert m, f"{account_id} card has no --thumb value at all"
        thumb = m.group(1)
        assert not all(stop in thumb for stop in placeholder_stops), (
            f"{account_id} (accent {cfg['accent']}) fell through to the generic "
            f"placeholder gradient -- add its accent to thumb_map")


def test_northstar_gets_a_blue_gradient_distinct_from_healthcare():
    """Locks in the specific fix: NorthStar's #5b9dff accent now has its own
    entry, and it isn't just a copy of Healthcare's #3b82f6 gradient."""
    healthcare_html = appmod._build_account_card("healthcare", appmod.ACCOUNTS["healthcare"])
    northstar_html = appmod._build_account_card("northstar", appmod.ACCOUNTS["northstar"])
    healthcare_thumb = re.search(r"--thumb:([^;]+);", healthcare_html).group(1)
    northstar_thumb = re.search(r"--thumb:([^;]+);", northstar_html).group(1)
    assert northstar_thumb != healthcare_thumb


# ── Grid: no fixed-column trap that strands a last-row card ─────────────────

def test_grid_columns_use_a_flexible_upper_bound_not_a_fixed_pair():
    """minmax(min,max) with a fixed pixel max is what created the 2-column
    ceiling that stranded a 3rd card; minmax(min,1fr) lets auto-fit's columns
    share whatever row width is actually available instead of always maxing
    out at a hardcoded number of columns."""
    text = _template_text()
    grid_rules = re.findall(r"\.grid\{[^}]*grid-template-columns:([^;]+);", text)
    assert grid_rules, "no .grid grid-template-columns rule found"
    last = grid_rules[-1]  # later rules win the cascade for this property
    assert "1fr" in last, f"expected a flexible (1fr) column max, got: {last!r}"


def test_main_has_an_explicit_width_not_left_to_the_marquee_to_decide():
    """Without this, the scrolling marquee band (a child of .main) silently
    inflates .main past the real viewport width, and anything sized as a
    percentage of .main -- including the card grid -- inherits that inflated
    width and overflows the visible page."""
    text = _template_text()
    main_rules = re.findall(r"(?<!-)\.main\{([^}]*)\}", text)
    assert main_rules, "no .main rule found"
    assert any(re.search(r"(?<!-)width:100%", rule) for rule in main_rules), (
        ".main never gets an explicit width:100% -- the marquee-inflation "
        "bug is unguarded again")
