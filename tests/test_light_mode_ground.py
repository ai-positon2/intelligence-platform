"""The light-mode page ground, and the selector shape that broke it.

aurora-app.css shipped its light ground as
`:root[data-theme="light"] html,[data-theme="light"] html`. Both halves are
unmatchable. `:root` IS `html`, so the first asks for an html nested inside a
light-themed html; the second asks for an html nested inside anything at all,
and html is never a descendant. Nothing errors, no build warns, and light mode
just keeps the dark ground on every page that does not define its own. Measured
on Contact Finder before the fix: body text rgb(23,33,58) on ground rgb(7,9,18),
a contrast ratio of 1.24:1 against the 4.5:1 that AA asks for. After: 14.12:1.

What these tests can and cannot see: the specificity claims below are computed
arithmetically from the selector text, not by rendering. The rendered check was
done in a browser against the real pages (Contact Finder, and all three pages
that carry their own ground, in both themes) and is not reproducible in pytest
without a headless browser, so it is recorded here rather than automated.
"""

import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CSS = os.path.join(_ROOT, "static", "css")


def _read(name):
    with open(os.path.join(_CSS, name), encoding="utf-8") as fh:
        return fh.read()


def _strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


# Selectors that can never match, because html is the root element and :root is
# html. Any of these reappearing means the bug is back.
_DEAD = (
    ':root[data-theme="light"] html',
    ':root[data-theme="dark"] html',
    '[data-theme="light"] html',
    '[data-theme="dark"] html',
)


def _is_dead(sel):
    sel = " ".join(sel.split())
    return any(sel == d or sel.startswith(d + " ") or sel.startswith(d + ":")
               for d in _DEAD)


def test_no_rule_is_made_entirely_of_selectors_that_cannot_match():
    """Scoped to whole rules on purpose. `[data-theme="light"] html` appearing
    in a list next to `[data-theme="light"] body` is dead weight but the rule
    still applies through its other members, and two scrollbar rules are
    written exactly that way. What broke light mode was a rule where EVERY
    selector was of this shape, so nothing applied it and nothing complained."""
    doomed = []
    for f in sorted(os.listdir(_CSS)):
        if not f.endswith(".css"):
            continue
        for sel in re.findall(r"(?:^|[}\n])\s*([^{}@\n][^{}@]*?)\s*\{",
                              _strip_comments(_read(f))):
            parts = [p for p in sel.split(",") if p.strip()]
            if parts and all(_is_dead(p) for p in parts):
                doomed.append("%s: %s" % (f, " ".join(sel.split())[:80]))
    assert not doomed, (
        "every selector on these rules targets html as a descendant of a "
        "themed element, which can never match, so the whole rule is inert: "
        "%s" % doomed)


def test_aurora_defines_a_light_ground_on_the_themed_element_itself():
    """Either compound form is correct, because :root IS html: what must never
    come back is the DESCENDANT form. Accepting both keeps this from rejecting
    a valid refactor while still failing the bug."""
    body = _strip_comments(_read("aurora-app.css"))
    m = re.search(r'(?:html|:root)\[data-theme="light"\]\s*\{([^}]*)\}', body)
    assert m, ("aurora-app.css no longer sets a light ground on the themed "
               "element, so every page without its own falls back to the dark one")
    assert "background" in m.group(1), m.group(1)


def _specificity(sel):
    """(ids, classes+attrs+pseudo-classes, elements). Enough for these rules."""
    ids = len(re.findall(r"#[\w-]+", sel))
    cls = (len(re.findall(r"\.[\w-]+", sel))
           + len(re.findall(r"\[[^\]]+\]", sel))
           + len(re.findall(r":(?!:)[\w-]+", sel)))
    els = len(re.findall(r"(?:^|[\s>+~])([a-z][\w-]*)", sel))
    return (ids, cls, els)


def test_the_light_ground_outranks_the_unconditional_one():
    """It must not depend on source order. Both rules live in the same file
    today, but a later `html{...}` anywhere would otherwise silently win."""
    assert _specificity('html[data-theme="light"]') > _specificity("html")


@pytest.mark.parametrize("page", [
    "event_conference_intelligence.css",
    "social_creative_intelligence.css",
    "42_north_dental_slot_checker.css",
])
def test_a_page_with_its_own_light_ground_still_outranks_auroras(page):
    """These three paint gradients, not aurora's flat colour. Fixing aurora's
    selector raised it from unmatchable to 0,1,1, so anything at 0,1,1 or below
    in these files would now lose and the page would render aurora's flat grey
    instead of its own gradient."""
    body = _strip_comments(_read(page))
    grounds = [s for s in re.findall(r"([^{}]+)\{[^}]*background", body)
               if 'data-theme="light"' in s and "html" in s]
    assert grounds, "%s no longer defines its own light ground" % page
    aurora = _specificity('html[data-theme="light"]')
    for sel in grounds:
        assert _specificity(sel.strip()) > aurora, (
            "%s: `%s` no longer beats aurora's light ground, so this page loses "
            "its gradient in light mode" % (page, sel.strip()))


def test_no_page_sets_a_body_background_that_is_silently_discarded():
    """aurora-app.css loads after every page stylesheet and carries
    `body{background:transparent!important}`, so a body background in a page
    file has no effect whatsoever. Two files carried one for a long time and
    read as though the page controlled its own ground. A ground belongs on
    `html:root`, which outranks aurora's `html{...}` regardless of order."""
    offenders = []
    for f in sorted(os.listdir(_CSS)):
        if not f.endswith(".css") or f == "aurora-app.css":
            continue
        for m in re.finditer(r"(?:^|\})\s*body\s*\{([^}]*)\}",
                             _strip_comments(_read(f))):
            for d in re.finditer(r"\bbackground(?:-color|-image)?\s*:([^;]*)",
                                 m.group(1)):
                # `transparent` agrees with aurora instead of fighting it, so
                # it is not a silent loss. Anything else is.
                # A suffix, removed as a suffix: str.rstrip takes a character
                # SET, so rstrip("!important") turns "transparent" into
                # "transpare" and this test fails on the very thing it allows.
                val = re.sub(r"\s*!important\s*$", "", d.group(1).strip()).strip()
                if val != "transparent":
                    offenders.append("%s: %s" % (f, " ".join(d.group(0).split())[:70]))
    assert not offenders, (
        "these declarations are discarded by aurora-app.css's !important and "
        "have no effect: %s" % offenders)


def test_aurora_still_forces_body_transparent():
    """The rule above depends on this one existing. If the particle-canvas fix
    is ever removed, page files may set a body background again and the
    previous test should be deleted with it rather than left to mislead."""
    body = _strip_comments(_read("aurora-app.css"))
    assert re.search(r"body\{background:transparent!important\}", body), (
        "the body-transparent rule is gone; revisit "
        "test_no_page_sets_a_body_background_that_is_silently_discarded")


@pytest.mark.parametrize("page", [
    "admin.css", "anonymous_visitors.css", "gtm.css", "job_change_alert.css",
    "linkedin_playbook_studio.css", "seo.css",
])
def test_a_moved_ground_never_carries_important(page):
    """These six deliberately do NOT define a light ground: they rely on
    aurora's `html[data-theme="light"]` (0,1,1) outranking their own
    `html:root` (0,0,2) on class level, which is what gives them a light mode
    for free. An `!important` on the page's ground defeats that comparison
    outright and pins the page to a dark gradient in light mode. admin.css
    carried one before the move, so this is a real regression path."""
    body = _strip_comments(_read(page))
    m = re.search(r"html:root\s*\{([^}]*)\}", body)
    assert m, "%s no longer sets its ground on html:root" % page
    assert "!important" not in m.group(1), (
        "%s: !important on the page ground beats aurora's light rule and "
        "removes this page's light mode: %s" % (page, m.group(1).strip()[:80]))
    assert "background-color" in m.group(1), (
        "%s: a gradient-only `background` shorthand resets background-color to "
        "transparent, and macOS overscroll then paints white past the document "
        "edge" % page)
