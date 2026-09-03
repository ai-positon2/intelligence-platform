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

def test_a_sparse_spread_does_not_pin_its_columns_to_the_edges(page_script):
    """One kept event and one cut one drew as two blocks at opposite ends of
    an empty band, which reads as a broken chart rather than as two events.
    Two now draws nothing at all, so this checks three."""
    html = _render(page_script, _recommend(
        [_cand("A", 74, "P2"), _cand("B", 72, "P2")], discovered=4,
        counts={"P1": 0, "P2": 2, "kept": 2, "excluded": 1, "finished": 0},
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


def test_two_events_draw_no_spread_at_all(page_script):
    """The band was 138px of empty with two blocks in it, and the cut event
    is named under "Scored and cut" regardless, so the chart was costing
    height and paying nothing."""
    html = _render(page_script, _recommend(
        [_cand("A", 74, "P2")], discovered=2,
        counts={"P1": 0, "P2": 1, "kept": 1, "excluded": 1, "finished": 0},
        excluded=[{"name": "Cut one", "tier": "P3", "total": 61}]))
    assert "Against the bar" not in html
    assert "Cut one" in html, "the cut event still has to be named somewhere"


# ── the report says each thing once ──────────────────────────────────────

def test_the_answer_comes_before_the_workings(page_script):
    """A reader had to pass the profile, the counts, the funnel, everything
    that was not measured and the spread before learning which event to
    book."""
    html = _render(page_script, _recommend(
        [_cand("ATTD", 74, "P2")], discovered=2,
        counts={"P1": 0, "P2": 1, "kept": 1, "excluded": 1, "finished": 0},
        notes=[{"level": "gap", "head": "1 of 6 category searches did not run",
                "detail": "Side event: the call failed."}]))
    answer = html.index("The answer")
    assert answer < html.index("What was not measured")
    assert answer < html.index("Scored against the rubric")
    assert "ATTD" in html[answer:answer + 900], (
        "the answer band does not name the event it is the answer about")


def test_a_top_five_that_is_the_whole_list_earns_no_section(page_script):
    """One kept event printed three times: in the answer band, under "Top
    five", and again under "The ranked list" immediately below it."""
    html = _render(page_script, _recommend(
        [_cand("ATTD", 74, "P2")], discovered=2,
        counts={"P1": 0, "P2": 1, "kept": 1, "excluded": 1, "finished": 0},
        top_five=[{"name": "ATTD", "tier": "P2", "total": 74, "when": "w",
                   "where": "x", "case": "c"}]))
    assert "Top five" not in html
    assert html.count("ATTD") < 6, "the same event is still printed several times"
    assert "The ranked list" in html


def test_a_top_five_that_shortlists_a_longer_list_still_renders(page_script):
    cands = [_cand("E%d" % i, 90 - i, "P1") for i in range(7)]
    html = _render(page_script, _recommend(
        cands, discovered=12,
        counts={"P1": 7, "P2": 0, "kept": 7, "excluded": 0, "finished": 0},
        top_five=[{"name": "E%d" % i, "tier": "P1", "total": 90 - i, "when": "w",
                   "where": "x", "case": "c"} for i in range(5)]))
    assert "Top five" in html


# ── the pointers ─────────────────────────────────────────────────────────

def _notes_payload(page_script, notes):
    return _render(page_script, _recommend(
        [_cand("ATTD", 74, "P2")], discovered=2,
        counts={"P1": 0, "P2": 1, "kept": 1, "excluded": 1, "finished": 0},
        notes=notes))


def test_a_note_is_one_scannable_line_with_its_reason_behind_it(page_script):
    """Fifteen possible paragraphs printed in full became a 180-word block
    that the person it was written for would not read. The facts all stay;
    the reading does not."""
    html = _notes_payload(page_script, [
        {"level": "gap", "head": "1 of 6 category searches did not run",
         "detail": "A long reason that nobody needs on first read."}])
    assert "1 of 6 category searches did not run" in html
    assert "A long reason" in html, (
        "the reason has to still be reachable, just not in the way")
    # The head has to BE the summary, not merely sit above the detail inside
    # a details element. A mutant that swapped <summary> for <span> left the
    # reason permanently on screen and passed a looser version of this.
    row = re.search(r'<details class="nr[^"]*"[^>]*>(.*?)</details>', html, re.S)
    assert row, "the note is not a disclosure at all"
    summary = re.search(r'<summary>(.*?)</summary>', row.group(1), re.S)
    assert summary, "the head is not the summary, so the reason never folds"
    assert "1 of 6 category searches did not run" in summary.group(1)
    assert "A long reason" not in summary.group(1), (
        "the reason is inside the summary, which is the same as not folding it")


def test_a_note_with_no_reason_is_not_a_disclosure_that_opens_onto_nothing(page_script):
    html = _notes_payload(page_script, [
        {"level": "ok", "head": "Nothing material was left unmeasured",
         "detail": ""}])
    assert "Nothing material was left unmeasured" in html
    block = html[html.index("Nothing material") - 200:html.index("Nothing material")]
    assert "<details" not in block, "an empty disclosure invites a pointless click"


def test_the_notes_are_severity_coded_so_a_column_can_be_scanned(page_script):
    html = _notes_payload(page_script, [
        {"level": "gap", "head": "A hole", "detail": ""},
        {"level": "thin", "head": "Measured on less", "detail": ""},
        {"level": "note", "head": "Worth knowing", "detail": ""}])
    for level in ("n-gap", "n-thin", "n-note"):
        assert level in html, "%s rows are not marked" % level
    # The dot is the thing scanned, and it takes its colour from its own
    # class. A mutant that stripped the level from the dot and left it on the
    # row wrapper passed the three checks above.
    dots = re.findall(r'<span class="nd ([^"]*)"></span>', html)
    assert sorted(dots) == ["n-gap", "n-note", "n-thin"], (
        "the severity never reaches the dot itself: %r" % dots)


def test_the_notes_carry_a_tally_so_the_count_is_readable_without_counting(page_script):
    html = _notes_payload(page_script, [
        {"level": "gap", "head": "A", "detail": ""},
        {"level": "gap", "head": "B", "detail": ""},
        {"level": "thin", "head": "C", "detail": ""}])
    tally = html[html.index("ntally"):html.index("ntally") + 400]
    assert ">2<" in tally and "Not measured" in tally
    assert ">1<" in tally and "Measured on less" in tally


def test_a_run_stored_before_the_pointers_existed_still_renders(page_script):
    """The prose shape lives in every finished run's stored summary, and no
    stored text is ever cut to make a head: a head invented by chopping a
    sentence in half can say the opposite of the paragraph under it.

    A paragraph that opens with a short sentence of its own is a different
    case, and that sentence becomes the head with the paragraph behind it.
    That is not a guess, it is the paragraph's own first sentence, and it is
    the only way a run stored before the split reads like the ones after it.
    """
    html = _render(page_script, _recommend(
        [_cand("ATTD", 74, "P2")], discovered=2,
        counts={"P1": 0, "P2": 1, "kept": 1, "excluded": 1, "finished": 0},
        assumptions=["An old stored paragraph that was never split up."]))
    assert "An old stored paragraph that was never split up." in html
    assert "What was not measured" in html


def test_a_stored_paragraph_with_no_short_sentence_is_printed_whole(page_script):
    """Verbatim from the run this was reported on. Its first full stop is
    inside a parenthetical thirty words in, so there is no sentence to head it
    with and it keeps its paragraph."""
    para = ("1 discovery category did not run, so this list is missing a kind "
            "of event rather than having found none: Regional flagship (the "
            "answer ran past the length it was allowed). That is a hole in "
            "the analysis, not a finding about the market.")
    html = _render(page_script, _recommend(
        [_cand("ATTD", 74, "P2")], discovered=2,
        counts={"P1": 0, "P2": 1, "kept": 1, "excluded": 1, "finished": 0},
        assumptions=[para]))
    assert para in html
    assert "nfull" in html, "the shortened row was not used"
    assert "\u2026" not in html.split("What was not measured")[1][:900], \
        "a sentence was cut to make a head"
    # Shortened by the stylesheet, not by the markup: the whole paragraph is
    # in the row, and every fold is opened for the print.
    css = _decommented(_CSS)
    # rindex: the print block carries a rule with the same selector, and it
    # sits earlier in the file.
    at = css.rindex(".evi-exec .nr.nfull .nx")
    assert "-webkit-line-clamp: 2" in css[at:at + 260]
    assert "-webkit-line-clamp: none" in css[css.index(".nr.nfull[open] .nx"):][:120]
    rules = _rules(_print_block())
    clamp = [d for sel, d in rules.items()
             if "nfull" in sel and "line-clamp" in d]
    assert clamp and all("none" in d for d in clamp), \
        "the print keeps a CSS clamp on a paragraph nobody can click open"


def test_a_stored_paragraph_that_opens_with_a_short_sentence_is_folded(page_script):
    para = ("Cross-client check: not measured. There are no completed "
            "recommendations for a different client to compare this list "
            "against yet, so the cross-client check could not run. It becomes "
            "meaningful from the second client onwards.")
    html = _render(page_script, _recommend(
        [_cand("ATTD", 74, "P2")], discovered=2,
        counts={"P1": 0, "P2": 1, "kept": 1, "excluded": 1, "finished": 0},
        assumptions=[para]))
    at = html.index("What was not measured")
    block = html[at:at + 1400]
    assert "<details" in block, "a paragraph with a short opener stayed flat"
    assert "Cross-client check: not measured." in block
    assert para in block, "the paragraph behind the fold lost text"


def test_every_fold_is_opened_for_the_print():
    """Folding is what made the report readable on screen. On paper it would
    make it incomplete: a "Why" nobody can click is a fact deleted from the
    page, and paper has no interaction and no shortage of room."""
    tpl = _read(_TPL)
    at = tpl.index("addEventListener('beforeprint'")
    body = tpl[at:at + 1200]
    assert "details:not([open])" in body
    assert "setAttribute('open'" in body
    back = tpl[tpl.index("addEventListener('afterprint'"):][:900]
    assert "removeAttribute('open')" in back, (
        "a section the reader had closed would stay open after printing")


def test_only_the_folds_the_print_opened_are_closed_again():
    """A reader who had opened a reason before printing should still find it
    open afterwards."""
    tpl = _read(_TPL)
    at = tpl.index("addEventListener('beforeprint'")
    body = tpl[at:at + 1200]
    assert "_printOpened = []" in body
    assert "details:not([open])" in body, (
        "collecting every details, not only the shut ones, would close the "
        "reader's own open sections")


def test_paper_does_not_promise_an_ellipsis_it_cannot_keep():
    """"ATTD 2027 (Advanced Technologies..." printed exactly that way. On
    screen the rest is a hover away; on paper it is gone."""
    rules = _rules(_print_block())
    sel = [s for s in rules if ".evi-answer .an" in s]
    assert sel, "the answer band's event name is still ellipsized on paper"
    assert "white-space: normal" in rules[sel[0]]


def test_a_severity_dot_survives_background_graphics_being_off(page_script):
    """The dot is the only thing scanned in that column and it is painted as
    a background, so with Chrome's default print setting the whole column
    read as one flat list."""
    rules = _rules(_print_block())
    sel = [s for s in rules if ".nd" in s and "outline:" in rules[s]]
    assert sel, "the severity dots print as nothing"
    assert re.search(r"outline:\s*\d+px\s+solid\s+#", rules[sel[0]])
    # And the levels have to stay distinguishable, or three identical rings
    # replace three colours.
    tinted = [s for s in rules if ".nd" in s and "outline-color" in rules[s]]
    assert len(tinted) >= 2, (
        "every severity prints the same ring, so the coding is gone")


def _coverage(page_script, shortfall, **kw):
    """A report whose only content is what the coverage section is built on."""
    run = _recommend(
        [_cand("ATTD", 74, "P2")], discovered=2,
        counts={"P1": 0, "P2": 1, "kept": 1, "excluded": 1, "finished": 0},
        shortfall=shortfall, **kw)
    html = _render(page_script, run)
    at = html.index("Category coverage")
    return html[at:]


def test_a_paragraph_length_reason_is_folded_and_not_dropped(page_script):
    """Six categories with a paragraph under each is the section nobody read,
    and it is the section a reader complained about twice. The reasons live
    under the chart now, one line each, and the paragraph is behind the
    line."""
    long_why = ("Searched for manufacturer, hospital and patient-association "
                "days that carry a diabetes-technology audience, and "
                "separately for free expo halls attached to the clinical "
                "meetings. Nothing with a published date came back.")
    html = _coverage(page_script, [
        {"category": "free_vendor", "label": "Free sponsor-funded event",
         "status": "empty", "found": 0, "quota": 2, "why": long_why}])
    assert long_why[:40] in html, "the reason was dropped rather than folded"
    # Not on a bar row. The chart is a picture and a paragraph inside it is
    # what stopped it being one.
    bars = html[:html.index("Why they fell short")]
    assert long_why[:40] not in bars
    row = re.search(r'<details class="nr n-thin"[^>]*>(.*?)</details>', html, re.S)
    assert row, "the reason is not behind a fold of its own"
    assert long_why[:40] in row.group(1)
    assert "Free sponsor-funded event" in row.group(1), "unattributed"


def test_one_reason_shared_by_three_categories_is_said_once(page_script):
    """The run this was reported on recorded the same sentence for three
    categories, and the report printed it three times, in three wrapped
    paragraphs, between three bars."""
    same = ("The search for this category reported that it could not be "
            "finished, so an empty result here is a gap in the search rather "
            "than a fact about the market.")
    html = _coverage(page_script, [
        {"category": "vertical_summit", "label": "Vertical summit",
         "status": "error", "found": 0, "quota": 2, "why": same},
        {"category": "emerging", "label": "Emerging event",
         "status": "error", "found": 0, "quota": 2, "why": same},
        {"category": "side_event", "label": "Side event",
         "status": "error", "found": 0, "quota": 2, "why": same}])
    why = html[html.index("Why they fell short"):]
    assert why.count("The search did not finish") == 1, \
        "one statement, three times"
    assert why.count(same) == 1, "the same paragraph more than once"
    for name in ("Vertical summit", "Emerging event", "Side event"):
        assert name in why, "%s lost its place in the group" % name


def test_two_categories_short_for_different_reasons_keep_both_reasons(page_script):
    """The other half of grouping. Merging the statement must not merge the
    evidence: a reader has to be able to tell which reason belongs to which
    category."""
    html = _coverage(page_script, [
        {"category": "free_vendor", "label": "Free sponsor-funded event",
         "status": "empty", "found": 0, "quota": 2,
         "why": "Every city day on this calendar is aimed at practitioners "
                "rather than at the people who sign for a booth."},
        {"category": "emerging", "label": "Emerging event",
         "status": "empty", "found": 0, "quota": 2,
         "why": "No first-to-third edition event serves this buyer at all, "
                "and the two that came close are consumer meet-ups."}])
    why = html[html.index("Why they fell short"):]
    assert why.count("Nothing here for this client") == 1
    assert "aimed at practitioners" in why and "consumer meet-ups" in why
    # Attributed, because one statement now covers two categories.
    assert "<b>Free sponsor-funded event.</b>" in why
    assert "<b>Emerging event.</b>" in why


def test_a_cut_off_search_is_never_reported_as_an_empty_market(page_script):
    """`partial` means the search ran and did not finish. Read as anything
    else it prints "nothing here for this client" under a category whose own
    stored reason says the opposite, which is the one mistake this section
    exists to avoid."""
    html = _coverage(page_script, [
        {"category": "regional_flagship", "label": "Regional flagship",
         "status": "partial", "found": 0, "quota": 2,
         "why": "The search for this category reported that it could not be "
                "finished."}])
    why = html[html.index("Why they fell short"):]
    assert "Nothing here for this client" not in why
    assert "The search did not finish" in why
    assert 'class="nr n-gap"' in why, "a hole was drawn as a thin market"


def test_a_spent_search_budget_is_re_read_as_a_budget_and_not_a_fault(page_script):
    """Runs made before that was understood recorded a spent budget as a
    broken search, so the report told a client a category had gone
    unsearched when it had been searched to the limit we paid for. The stored
    row cannot be fixed, so it is corrected on the way out."""
    html = _coverage(page_script, [
        {"category": "vertical_summit", "label": "Vertical summit",
         "status": "error", "found": 0, "quota": 2,
         "why": "search_limit: The web_search tool stopped returning results "
                "part-way through (max_uses_exceeded) after 6 searches, so "
                "this answer was written from an incomplete search rather "
                "than a finished one."}])
    why = html[html.index("Why they fell short"):]
    assert "used every search it was allowed" in why
    assert 'class="nr n-gap"' not in why, "a spent budget is not a hole"
    # And the sentence that contradicted it, in the tool's own vocabulary,
    # does not survive behind the fold either.
    for token in ("web_search", "max_uses_exceeded", "search_limit"):
        assert token not in html, "%s reached the report" % token
    assert "budget being enforced rather than a fault" in why


def test_a_stored_reason_written_for_the_log_stays_off_the_surface(page_script):
    """Runs stored before the write path was fixed carry an error kind and
    the wrapper's developer detail. The surface line is composed here, so it
    is clean whatever the row holds, and the stored text is kept behind
    it."""
    stored = ("unparsable: the reply could not be read (stop_reason=max_tokens, "
              "blocks=3)")
    html = _coverage(page_script, [
        {"category": "side_event", "label": "Side event",
         "status": "error", "found": 0, "quota": 2, "why": stored}])
    why = html[html.index("Why they fell short"):]
    head = why[:why.index("</summary>")] if "</summary>" in why else why
    for token in ("unparsable", "stop_reason", "max_tokens", "blocks="):
        assert token not in head, "%s is on the surface of the report" % token
    assert "The search did not finish" in head


def test_a_short_reason_about_the_market_is_the_line_itself(page_script):
    """The one sentence in this section that is about the client's market is
    better than anything composed from a status, so it is promoted to the
    line rather than hidden behind one."""
    html = _coverage(page_script, [
        {"category": "side_event", "label": "Side event",
         "status": "empty", "found": 0, "quota": 2,
         "why": "Nothing published yet."}])
    why = html[html.index("Why they fell short"):]
    assert "Nothing published yet." in why
    assert "<details" not in why, "a fold that hides four words costs more " \
        "than it saves"


def test_a_reason_lifted_from_a_stored_row_is_capitalised(page_script):
    """Every other line here is composed and starts with a capital. A real
    stored row reads "the market is thin"."""
    html = _coverage(page_script, [
        {"category": "side_event", "label": "Side event",
         "status": "short", "found": 1, "quota": 2, "why": "the market is thin"}])
    why = html[html.index("Why they fell short"):]
    assert "The market is thin" in why
    assert ">the market is thin" not in why


def test_a_reason_cut_off_mid_word_is_marked_as_cut(page_script):
    """The old write path stored the reason at a flat character count, so a
    real report ended a paragraph on "caregivers/clinicia"."""
    cut = ("Results confirmed this category consists of enterprise software "
           "vendor road-shows whose audiences are IT and business budget "
           "owners, and this client sells directly to people with type 1 "
           "diabetes and their caregivers/clinicia")
    html = _coverage(page_script, [
        {"category": "free_vendor", "label": "Free sponsor-funded event",
         "status": "empty", "found": 0, "quota": 2, "why": cut}])
    assert "caregivers/clinicia\u2026" in html or \
        "caregivers/clinicia&hellip;" in html or \
        "caregivers/clinicia…" in html


def test_diagnostics_inside_a_stored_paragraph_do_not_reach_the_page(page_script):
    """Verbatim from a run a client printed. The parenthetical is entirely
    this codebase's own diagnostics and the sentence after it is an
    instruction to us, and both were on paper."""
    stored = ("1 discovery category did not run, so this list is missing a "
              "kind of event rather than having found none: Regional flagship "
              "(max_tokens: Ran out of output budget before finishing "
              "(stop_reason=max_tokens). Raise max_tokens or lower "
              "max_uses.). That is a hole in the analysis, not a finding "
              "about the market.")
    html = _render(page_script, _recommend(
        [_cand("ATTD", 74, "P2")], discovered=2,
        counts={"P1": 0, "P2": 1, "kept": 1, "excluded": 1, "finished": 0},
        assumptions=[stored]))
    for token in ("max_tokens", "stop_reason", "max_uses", "Ran out of output"):
        assert token not in html, "%s reached the page" % token
    # The finding it was buried in survives, and reads as it should.
    assert "having found none: Regional flagship." in html
    assert "not a finding about the market" in html


def test_a_plumbing_sentence_inside_a_stored_paragraph_goes_but_the_rest_stays(page_script):
    """The model's account of a search tool, mid-paragraph, with a real
    finding in front of it."""
    stored = ("2 categories came back under the two-event quota after "
              "searching: Emerging event and Side event. However, the "
              "web_search tool hit a hard per-turn call limit partway "
              "through and returned 'server tool use limit exceeded'.")
    html = _render(page_script, _recommend(
        [_cand("ATTD", 74, "P2")], discovered=2,
        counts={"P1": 0, "P2": 1, "kept": 1, "excluded": 1, "finished": 0},
        assumptions=[stored]))
    assert "web_search" not in html and "server tool use limit" not in html
    assert "came back under the two-event quota" in html


def test_a_stored_paragraph_that_is_all_plumbing_is_kept_rather_than_emptied(page_script):
    """The conservative half, and the one that matters more. Every fact in
    this block is one the agent's value rests on, so losing a real finding to
    a keyword match is worse than showing a reader a token they will
    ignore."""
    stored = "The web_search tool returned max_uses_exceeded after 6 searches."
    html = _render(page_script, _recommend(
        [_cand("ATTD", 74, "P2")], discovered=2,
        counts={"P1": 0, "P2": 1, "kept": 1, "excluded": 1, "finished": 0},
        assumptions=[stored]))
    assert "What was not measured" in html
    assert "6 searches" in html, "the paragraph was deleted, not scrubbed"


def test_the_report_does_not_talk_about_a_model_even_in_stored_text(page_script):
    """A reader of this report has no model. What they can act on is what the
    search found and what it could not reach, and runs stored before the
    write path said so keep their own wording."""
    html = _render(page_script, _recommend(
        [_cand("ATTD", 74, "P2")], discovered=2,
        counts={"P1": 0, "P2": 1, "kept": 1, "excluded": 1, "finished": 0},
        assumptions=["The model reported that it could not finish searching "
                     "this category."],
        shortfall=[{"category": "side_event", "label": "Side event",
                    "status": "error", "found": 0, "quota": 2,
                    "why": "The model could not finish this one."}]))
    assert "model" not in html.lower()
    assert "The search reported that it could not finish" in html


def test_the_verdict_names_the_categories_that_did_not_finish(page_script):
    """"3 category searches did not run" left the reader to work out which
    three by cross-reading the chart above it."""
    html = _coverage(page_script, [
        {"category": "emerging", "label": "Emerging event", "status": "error",
         "found": 0, "quota": 2, "why": "Cut off."},
        {"category": "side_event", "label": "Side event", "status": "error",
         "found": 0, "quota": 2, "why": "Cut off."}])
    top = html[:html.index("Why they fell short")]
    assert "2 category searches did not finish" in top
    assert "Emerging event, Side event" in top
