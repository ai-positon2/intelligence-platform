"""Every agent page sits on the same responsive grid.

`grid-tokens.css` and `ds-tokens.css` define Arena's page side-margin
(`--margin`, stepping 20/32/48/80/120/160/200 by viewport), its column gutter
(`--gutter`) and `--bleed`, the offset that lines a full-width bar up with the
centered container beneath it. Pages load those sheets and then sometimes
hand-type a number anyway.

That regression is invisible on the page it happens to: it only shows up beside
a sibling, which is why nothing caught it for so long. Two pages had a topbar on
a flat 32px while their content sat at 120px, and one had a container on 30px
while every neighbour kept a real margin.

These are file-level checks rather than rendered ones because the suite has no
browser. They catch the thing that actually regresses: someone typing a px value
back into one of these declarations.
"""

import os
import re

import pytest

CSS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "static", "css")

# (stylesheet, container selector). One row per agent page, listed explicitly so
# a new page has to be added here on purpose rather than quietly skipped.
PAGES = [
    ("gtm.css", ".main"),                          # the B2B Agents listing
    ("social_creative_intelligence.css", ".main"),
    ("company_people_intelligence.css", ".shell"),  # Contact Finder
    ("linkedin.css", ".shell"),                     # LinkedIn Intelligence
    ("job_change_alert.css", ".main"),
    ("42_north_dental_slot_checker.css", ".main"),
    ("anonymous_visitors.css", ".main"),
    ("linkedin_playbook_studio.css", ".main"),
    ("event_conference_intelligence.css", ".main"),
    ("seo.css", ".main"),
]

# Both are the shared scale: --margin-app is documented in grid-tokens.css as an
# alias of --margin, kept for existing call sites.
MARGIN_TOKENS = ("var(--margin)", "var(--margin-app)")


def _rule(css, selector):
    """The base rule for `selector`: comments stripped, media-query copies
    excluded by refusing `{` as the opening delimiter."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    m = re.search(r"(?:^|[};])\s*%s\s*\{([^{}]*)\}" % re.escape(selector), css, re.S)
    return m.group(1) if m else None


def _padding(body):
    m = re.search(r"padding:\s*([^;}]+)", body or "")
    return " ".join(m.group(1).split()) if m else None


@pytest.mark.parametrize("sheet,container", PAGES,
                         ids=[p[0].replace(".css", "") for p in PAGES])
def test_the_page_container_takes_its_side_margin_from_the_token(sheet, container):
    path = os.path.join(CSS_DIR, sheet)
    assert os.path.exists(path), "%s is gone; update PAGES" % sheet
    body = _rule(open(path, encoding="utf-8").read(), container)
    assert body is not None, "no base %s rule in %s" % (container, sheet)
    pad = _padding(body)
    assert pad, "%s sets no padding in %s" % (container, sheet)
    assert any(t in pad for t in MARGIN_TOKENS), (
        "%s %s hand-types its side padding instead of taking the shared "
        "responsive margin: %r" % (sheet, container, pad))


@pytest.mark.parametrize("sheet,container", PAGES,
                         ids=[p[0].replace(".css", "") for p in PAGES])
def test_the_full_width_bar_bleeds_with_the_shared_offset(sheet, container):
    """A bar on a flat number does not move when the margin under it does, so
    the logo stops sitting above the content it belongs to."""
    body = _rule(open(os.path.join(CSS_DIR, sheet), encoding="utf-8").read(), ".topbar")
    assert body is not None, "no base .topbar rule in %s" % sheet
    pad = _padding(body)
    assert pad, ".topbar sets no padding in %s" % sheet
    assert "var(--bleed)" in pad, (
        "%s .topbar does not use --bleed, so it will not line up with the "
        "content container: %r" % (sheet, pad))


@pytest.mark.parametrize("sheet,container", PAGES,
                         ids=[p[0].replace(".css", "") for p in PAGES])
def test_the_bar_is_the_same_height_on_every_agent_page(sheet, container):
    """Chrome that changes height between agents reads as a page reload."""
    body = _rule(open(os.path.join(CSS_DIR, sheet), encoding="utf-8").read(), ".topbar")
    m = re.search(r"height:\s*(\d+)px", body or "")
    assert m, "%s .topbar sets no explicit height" % sheet
    assert m.group(1) == "62", (
        "%s .topbar is %spx tall; every other agent page is 62px"
        % (sheet, m.group(1)))


def test_the_embed_wrapper_matches_that_height_without_borrowing_bleed():
    """embed.html is a full-bleed iframe with no content container. It shares
    the bar height, but --bleed would inset its bar by the centering offset of
    a box that is not there (520px at 2560px wide), so it keeps --margin-app."""
    tpl = os.path.join(os.path.dirname(CSS_DIR), "..", "templates", "embed.html")
    tpl = os.path.normpath(tpl)
    css = open(tpl, encoding="utf-8").read()
    body = _rule(css, ".topbar")
    assert body, "embed.html has no .topbar rule"
    assert re.search(r"height:\s*62px", body), "the embed bar is not 62px"
    pad = _padding(body)
    assert "var(--margin-app)" in pad, (
        "the embed bar should keep --margin-app, not --bleed: %r" % pad)
