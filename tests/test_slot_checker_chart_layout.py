"""Layout invariants for the Slot Checker's capacity charts.

These assert against the CSS and template *source*, not against a rendered
layout, because the suite has no browser. That is a real limit: these tests
prove a guard is still declared, not that the guard works. They exist because
the guards they cover are each the direct fix for a shipped visual bug that a
green suite happily allowed:

  * "Where the capacity is" rendered as a list of numbers with no bars at all.
    The card lived in the narrow column of a 3-column grid that only ever held
    2 cards, and every sibling in a bar row had a fixed width. Their sum
    exceeded the row, so the one flexible child -- the bar track -- absorbed the
    entire overflow and computed to 0px wide. The bars were in the DOM the whole
    time, which is why nothing looked broken from the code's side.

  * The peak-day badge printed itself over the card header, because the tallest
    bar plus the badge was taller than the fixed-height plot it sat in.

  * Fourteen "12 Aug" x-axis labels overlapped into an unreadable smear.

Anything that measures pixels belongs in a browser check; see the round-4 notes
in the dashboard's memory entry for how that was verified.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = os.path.join(ROOT, "static", "css", "gentle_dental_slot_checker.css")
TPL = os.path.join(ROOT, "templates", "gentle_dental_slot_checker.html")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _strip_comments(css):
    return re.sub(r"/\*.*?\*/", " ", css, flags=re.S)


def _strip_at_blocks(css):
    """Drop @media/@supports blocks so a base rule is not confused with its
    responsive override."""
    out, i = [], 0
    while i < len(css):
        at = css.find("@", i)
        if at < 0:
            out.append(css[i:])
            break
        brace = css.find("{", at)
        if brace < 0:
            out.append(css[i:])
            break
        # A plain at-rule (@import/@charset) has no block; leave it be.
        if css[at:brace].strip().split()[0] not in ("@media", "@supports"):
            out.append(css[i:brace + 1])
            i = brace + 1
            continue
        out.append(css[i:at])
        depth, j = 0, brace
        while j < len(css):
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        i = j + 1
    return "".join(out)


def _decl(css, selector, prop, base_only=True):
    """The value of `prop` in the rule whose selector list contains `selector`.

    Comments are stripped first: a comment sitting above a rule is otherwise
    swept into that rule's selector text and the lookup silently misses.
    """
    css = _strip_comments(css)
    if base_only:
        css = _strip_at_blocks(css)
    found = None
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        sels = [" ".join(s.split()) for s in match.group(1).split(",")]
        if selector not in sels:
            continue
        for decl in match.group(2).split(";"):
            if ":" not in decl:
                continue
            name, _, value = decl.partition(":")
            if name.strip() == prop:
                found = " ".join(value.split())
    return found


def test_the_capacity_grid_declares_no_more_columns_than_it_has_cards():
    """A phantom third column is what squeezed the state rows in the first place."""
    tracks = _decl(_read(CSS), ".gd-grid-3", "grid-template-columns")
    assert tracks, "gd-grid-3 must declare its columns explicitly"
    assert len(tracks.split()) == 2, (
        "row one holds two cards (day chart, states) with brand mix spanning "
        "row two, so a third track is dead space: got %r" % tracks
    )


def test_the_bar_track_cannot_collapse_to_zero_width():
    css = _read(CSS)
    min_width = _decl(css, ".gd-bar-track", "min-width")
    assert min_width, (
        "the bar track is the only flexible child of a bar row, so without a "
        "min-width it absorbs all overflow and the bars silently vanish"
    )
    assert int(re.match(r"(\d+)", min_width).group(1)) >= 40, min_width


def test_the_bar_name_gives_way_before_the_bar_does():
    """The label can ellipsise; the data mark cannot disappear."""
    flex = _decl(_read(CSS), ".gd-bar-name", "flex")
    assert flex, ".gd-bar-name must declare flex, not a bare width"
    grow, shrink = flex.split()[0], flex.split()[1]
    assert grow == "0", "the name must not grow at the track's expense: %r" % flex
    assert shrink != "0", "the name must be allowed to shrink: %r" % flex


def test_a_non_zero_bar_always_keeps_a_visible_nub():
    """4 slots against 4,804 rounds to 0.08% of the track."""
    assert _decl(_read(CSS), '.gd-bar-fill[data-w]:not([data-w="0"])', "min-width")


def test_the_peak_badge_has_headroom_above_the_tallest_bar():
    css = _read(CSS)
    head = _decl(css, ":root", "--gd-bar-head")
    assert head and int(re.match(r"(\d+)", head).group(1)) > 0, head
    height = _decl(css, ".gd-cols", "height")
    assert "--gd-bar-head" in height and "--gd-bar-max" in height, (
        "the plot must reserve badge headroom on top of the bar area, or the "
        "tallest column overflows onto the card header: %r" % height
    )


def test_the_chart_geometry_has_a_single_source_of_truth():
    """The template positions gridlines with calc() over the same variables the
    CSS sizes the plot with, so the two cannot drift apart."""
    css, tpl = _read(CSS), _read(TPL)
    for var in ("--gd-bar-max", "--gd-bar-head", "--gd-lbl-h", "--gd-lbl-gap"):
        assert _decl(css, ":root", var), "%s must be declared on :root" % var
    for var in ("--gd-lbl-h", "--gd-lbl-gap", "--gd-bar-max"):
        assert var in tpl, "%s must be referenced by the chart renderer" % var


def test_the_x_axis_prints_the_month_on_its_own_row():
    """Fourteen day-plus-month labels do not fit; the month band is why."""
    css, tpl = _read(CSS), _read(TPL)
    assert _decl(css, ".gd-months", "display") == "flex"
    assert "gd-months" in tpl and "gd-mo" in tpl
    # The band and the columns must share a gap and a left offset or the month
    # sits under the wrong day.
    assert _decl(css, ".gd-months", "gap") == _decl(css, ".gd-cols", "gap")
    assert _decl(css, ".gd-months", "padding-left") == _decl(css, ".gd-plot", "padding-left")


def test_html_root_has_an_explicit_background_color_in_both_themes():
    """`background: <gradient-list>` is a shorthand: writing only images into it
    resets the longhand background-color to transparent, because gradients are
    images, not a colour. That is not cosmetic here -- the root element's
    background-*color* (never its background-image) is what a browser paints
    behind the document during macOS's rubber-band overscroll and during the
    brief unpainted gap on a fast trackpad fling. A transparent one means that
    region falls through to the browser's own default canvas: a white flash
    popping in from the top or bottom on a fast scroll, in dark mode too, since
    the gradient was never the layer being shown there. Confirmed live via
    getComputedStyle(html).backgroundColor before this test existed: rgba(0,0,0,0)."""
    css = _read(CSS)
    dark = _decl(css, "html:root", "background-color")
    light = _decl(css, 'html:root[data-theme="light"]', "background-color")
    assert dark and dark != "transparent", dark
    assert light and light != "transparent", light
    assert dark != light, "the two themes must not share one fallback colour"


def test_the_ribbon_palette_is_themed_rather_than_inlined():
    """Inline background colours from JS cannot be restyled per theme, and the
    dark ramp tops out near 2:1 on a white card."""
    css, tpl = _read(CSS), _read(TPL)
    for cls in ("r1", "r2", "r3", "r0"):
        assert _decl(css, ".gd-ribbon-seg.%s" % cls, "background"), cls
        assert _decl(
            css, ':root[data-theme="light"] .gd-ribbon-seg.%s' % cls, "background"
        ), "light theme needs its own step for %s" % cls
    assert "style=\"background:'+RIB" not in tpl, (
        "ribbon colours must come from a class, not an inline style"
    )
