"""What comes out of the printer, and the report heading above it.

This file exists because a user sent back a PDF. Everything in it was found
by printing the page with Chrome rather than by looking at the page, and none
of it was visible on screen:

  * three blank pages at exactly the height of the report that should have
    been on them, because the drawer draws in with `animation-fill-mode:
    both` over a `from { opacity: 0 }` keyframe and a print taken before that
    220ms animation runs applies the `from` frame;
  * a report clipped mid-word with half a page of white under it, because
    `.evi-drawer` keeps `overflow: hidden` and the flex body inside it was
    cut at the bottom of the first box;
  * a headline printed as "Know who is        before you book the booth",
    because `background-clip: text` has nothing to clip when Chrome's print
    dialog leaves background graphics off, which is its default;
  * two pages of landing copy in front of the report somebody meant to send;
  * "1 events cleared the bar of 2 found".

The CSS assertions read the declarations out of the `@media print` block
rather than grepping the file, and the last one checks every class the block
hides against the template, because the first draft of it hid `.evi-spend`,
a class that does not exist.
"""

import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_event_intel_event_view import page_script  # noqa: E402,F401
from test_event_intel_charts import _render, _render_parts, _recommend, _cand  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CSS = os.path.join(_ROOT, "static", "css", "event_conference_intelligence.css")
_TPL = os.path.join(_ROOT, "templates", "event_conference_intelligence.html")


_COMMENT = re.compile(r"/\*.*?\*/", re.S)


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _decommented(path):
    """CSS with comments removed.

    Not cosmetic. A comment sitting between two rules is absorbed into the
    next selector by any flat parse, so the rule it documents becomes
    unfindable, and this file's own rules are heavily commented.
    """
    return _COMMENT.sub("", _read(path))


def _print_block():
    """The body of `@media print`, brace-matched rather than guessed."""
    css = _decommented(_CSS)
    at = css.index("@media print")
    i = css.index("{", at)
    depth, j = 0, i
    while j < len(css):
        if css[j] == "{":
            depth += 1
        elif css[j] == "}":
            depth -= 1
            if depth == 0:
                return css[i + 1:j]
        j += 1
    raise AssertionError("@media print is not brace-balanced")


def _rules(block):
    """{selector: declarations} for one flat media block."""
    out = {}
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", block):
        sel = " ".join(sel.split())
        if sel.startswith("@") or not sel:
            continue
        out[sel] = " ".join(body.split())
    return out


# ── the report reaches the paper ─────────────────────────────────────────

def test_the_report_does_not_print_as_blank_pages():
    """The drawer's own draw-in animation, frozen. The existing freeze rule
    reached `.evi-anim *`, which is the charts inside the report and not the
    report itself, so the element kept its layout height and painted nothing.
    """
    rules = _rules(_print_block())
    sel = [s for s in rules if ".evi-drawer" in s and "animation" in rules[s]]
    assert sel, "nothing in @media print stops the drawer animating"
    decl = " ".join(rules[s] for s in sel)
    assert "animation: none !important" in decl
    assert "opacity: 1 !important" in decl, (
        "freezing the animation is not enough: fill-mode both leaves the "
        "from-frame's opacity 0 applied")


def test_the_printed_report_is_not_clipped_at_the_first_page():
    """`overflow: hidden` on the drawer cut a live report off mid-word."""
    rules = _rules(_print_block())
    assert "overflow: visible" in rules.get(".evi-drawer", ""), (
        "the drawer keeps overflow:hidden from the screen rule")
    body = rules.get(".evi-drawer-body", "")
    assert "overflow: visible" in body
    assert "flex: none" in body, (
        "a flex child with flex:1 and min-height:0 can still be squeezed "
        "shorter than its content")


def test_gradient_text_prints_as_ink_rather_than_as_nothing():
    """Chrome's print dialog defaults background graphics OFF, and text
    painted out of a background needs that background to exist."""
    rules = _rules(_print_block())
    sel = [s for s in rules if ".evi-hero h1 em" in s]
    assert sel, "the hero's gradient words are not reset for print"
    decl = rules[sel[0]]
    assert "background: none !important" in decl
    assert "background-clip: border-box !important" in decl
    assert re.search(r"color:\s*#[0-9a-f]{3,6}\s*!important", decl), (
        "the words need an actual colour, not just the clip removed")
    assert ".evi-tile .tv" in sel[0], (
        "the summary's big numbers use the same trick and break the same way")


# ── what a person is printing ────────────────────────────────────────────

def test_printing_a_report_does_not_print_the_landing_page_first():
    rules = _rules(_print_block())
    hidden = [s for s in rules
              if "body.evi-reading" in s and "display: none" in rules[s]]
    assert hidden, "the landing copy is printed in front of the report"
    assert "evi-hero" in hidden[0] and "evi-plays" in hidden[0]


def test_every_class_the_print_block_hides_exists_in_the_template():
    """The check that would have caught the first draft of the rule above,
    which hid `.evi-spend`. A selector naming a class nobody uses hides
    nothing and says nothing, and no other test can see the difference."""
    tpl = _read(_TPL)
    rules = _rules(_print_block())
    named = set()
    for sel in rules:
        if "evi-reading" not in sel:
            continue
        for cls in re.findall(r"\.([a-z][a-z0-9-]+)", sel):
            if cls != "evi-reading":
                named.add(cls)
    assert named, "the reading-mode rule names no classes"
    for cls in sorted(named):
        assert re.search(r'class="[^"]*\b%s\b' % re.escape(cls), tpl), (
            "@media print hides .%s, which no element on the page has" % cls)


def test_the_page_knows_when_a_report_is_open():
    """The class the rule above depends on, set next to the one that opens
    the drawer so the two cannot drift."""
    tpl = _read(_TPL)
    assert "classList.toggle('evi-reading'" in tpl
    open_at = tpl.index("function openRun(")
    assert "readingMode(true)" in tpl[open_at:open_at + 900]
    close_at = tpl.index("function closeDrawer(")
    assert "readingMode(false)" in tpl[close_at:close_at + 500], (
        "a report closed on screen would still hide the page in print")


# ── the prose above the report ───────────────────────────────────────────

def test_a_single_kept_event_is_not_called_one_events(page_script):
    """What a paying client was shown: '1 events cleared the bar of 2 found'."""
    parts = _render_parts(page_script, _recommend(
        [_cand("ATTD", 74, "P2")], discovered=2,
        counts={"P1": 0, "P2": 1, "kept": 1, "excluded": 1, "finished": 0}))
    assert parts["sub"] == "1 event cleared the bar of 2 found", parts["sub"]


def test_more_than_one_kept_event_still_reads_as_plural(page_script):
    parts = _render_parts(page_script, _recommend(
        [_cand("A", 90, "P1"), _cand("B", 74, "P2")], discovered=9,
        counts={"P1": 1, "P2": 1, "kept": 2, "excluded": 3, "finished": 0}))
    assert parts["sub"] == "2 events cleared the bar of 9 found"


def test_the_funnel_counts_categories_rather_than_a_typed_six(page_script):
    """It said 'Found across the six searches'. A run makes one finding call
    per category and then one confirmation call per candidate, so the number
    of searches is neither six nor knowable from the page."""
    html = _render(page_script, _recommend(
        [_cand("A", 90, "P1")], discovered=4,
        counts={"P1": 1, "P2": 0, "kept": 1, "excluded": 1, "finished": 0}))
    assert "six searches" not in html
    assert "Found across the 6 discovery categories" in html


def test_the_category_count_is_never_spelled_categorys(page_script):
    html = _render(page_script, _recommend(
        [_cand("A", 90, "P1")], discovered=4,
        counts={"P1": 1, "P2": 0, "kept": 1, "excluded": 1, "finished": 0}))
    assert "categorys" not in html


# ── a chart with two things in it ────────────────────────────────────────

def test_a_two_event_spread_does_not_pin_its_columns_to_the_edges(page_script):
    """One kept event and one cut one drew as two blocks at opposite ends of
    an empty band, which reads as a broken chart rather than as two events."""
    html = _render(page_script, _recommend(
        [_cand("A", 74, "P2")], discovered=2,
        counts={"P1": 0, "P2": 1, "kept": 1, "excluded": 1, "finished": 0},
        excluded=[{"name": "Cut one", "tier": "P3", "total": 61}]))
    assert 'class="evi-cols few"' in html
    css = _decommented(_CSS)
    rules = _rules(css[:css.index("@media print")])
    assert "justify-content: flex-start" in rules.get(".evi-cols .cs", "")
    assert "max-width: 72px" in rules.get(".evi-cols.few .cx", "")


def test_a_crowded_spread_keeps_the_narrow_columns(page_script):
    """The 34px cap is what makes forty columns read as a distribution. The
    wider column is for the sparse case only."""
    cands = [_cand("E%d" % i, 90 - i, "P1") for i in range(6)]
    html = _render(page_script, _recommend(
        cands, discovered=9,
        counts={"P1": 6, "P2": 0, "kept": 6, "excluded": 0, "finished": 0}))
    assert 'class="evi-cols few"' not in html
    assert 'class="evi-cols"' in html


# ── the spelling of every counted noun ───────────────────────────────────

def test_no_counted_noun_is_pluralised_by_bolting_an_s_onto_a_y():
    """Three live strings said "2 categorys", "2 companys" and "3 other
    industrys". Each was one call to plural() with no plural form supplied,
    and no test could see them because they only appear at counts above one.

    So this checks the calls rather than the output: a word ending in a
    consonant plus y has to be given its plural, because -s is never right.
    """
    tpl = _read(_TPL)
    calls = re.findall(r"plural\(([^()]*(?:\([^()]*\))?[^()]*)\)", tpl)
    bad = []
    for call in calls:
        words = re.findall(r"'([^']*)'", call)
        if not words:
            continue
        singular = words[0]
        if re.search(r"[^aeiou]y$", singular) and len(words) < 2:
            bad.append(singular)
    assert not bad, (
        "plural() would spell these by adding an s: %s" % ", ".join(sorted(set(bad))))


def test_the_plural_helper_takes_the_irregular_form():
    tpl = _read(_TPL)
    assert "function plural(n, word, many)" in tpl
    at = tpl.index("function plural(n, word, many)")
    body = tpl[at:at + 200]
    assert "many || word + 's'" in body


# ── the reader's print settings are not ours to assume ───────────────────

def test_the_light_palette_is_used_on_paper_whatever_the_reader_is_using():
    """The dark palette is a design for a backlit screen. On paper the print
    rules turn every colour to dark ink while the card fills stay near-black,
    so the labels under the summary's big numbers came out grey on charcoal.

    Swapping the attribute hands the job to the light palette that already
    exists, rather than restating three dozen surfaces inside @media print
    and missing one."""
    tpl = _read(_TPL)
    assert "addEventListener('beforeprint'" in tpl
    at = tpl.index("addEventListener('beforeprint'")
    assert "setAttribute('data-theme', 'light')" in tpl[at:at + 400]


def test_the_readers_theme_comes_back_after_printing():
    tpl = _read(_TPL)
    at = tpl.index("addEventListener('afterprint'")
    body = tpl[at:at + 700]
    assert "removeAttribute('data-theme')" in body, (
        "no attribute is not the same as dark, and writing dark back would "
        "pin a reader who had never chosen one")
    assert "setAttribute('data-theme', _printTheme)" in body


@pytest.mark.parametrize("sel", [
    ".evi-funnel .fb",     # the found / scored / cleared bars
    ".evi-cols .cl",       # every scored event against the bar
    ".evi-sub .st span",   # relevance, decision-maker access, engagement
    ".evi-bars .bt .fl",   # category coverage
])
def test_a_bar_whose_length_is_the_number_survives_backgrounds_being_off(sel):
    """Chrome's print dialog leaves "Background graphics" unchecked by
    default, and that is how the PDF this file exists for was made. Every
    fill on the page disappears in that mode, so a shape whose LENGTH is the
    number prints as nothing and the reader loses the measurement, not just
    the colour."""
    rules = _rules(_print_block())
    # Not `"outline" in decl`: that also matches outline-offset on its own,
    # which paints nothing at all. A mutant that deleted the width, style and
    # colour and left the offset behind survived this test's first draft.
    outlined = [s for s in rules
                if sel in s and re.search(r"outline:\s*\d+px\s+solid\s+#", rules[s])]
    assert outlined, "%s prints as nothing with background graphics off" % sel


def test_the_score_track_is_outlined_too_or_every_bar_looks_full():
    """Only the inner span carries a width. With the track invisible a 30/40
    and a 38/40 are two lines of different length against nothing, which is
    not a comparison."""
    rules = _rules(_print_block())
    assert re.search(r"outline:\s*\d+px\s+solid\s+#",
                     rules.get(".evi-sub .st", ""))
