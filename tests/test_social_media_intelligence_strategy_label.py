"""Messaging & strategy used to render its own bespoke header -- a smaller
label baked into the top-left corner of its own card, unable to carry a link
chip -- while every sibling section (Content patterns, Evidence from real
posts, Every account, in one place) rendered a shared external label row via
renderSection(), complete with a right-aligned "open the source" chip. Two
sections of identical shape sitting back to back in the report disagreed on
where their heading sat, what it looked like, and whether it could link
anywhere at all.

These tests execute the real renderStrategy()/renderSection() functions in
node (not a text grep) and assert the two now share one structural shape:
same wrapping class, same label row, same link-chip behaviour. Only the
accent hue is still allowed to differ.

Skipped, not failed, where node is unavailable.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "templates", "social_media_intelligence.html")
_CSS = os.path.join(_ROOT, "static", "css", "social_media_intelligence.css")
_FUNCS = ("esc", "toPoints", "sectionLink", "renderSection", "renderStrategy")
_CONSTS = ("VIEW_GLYPH", "GLOBE_ICON")


def _extract_fn(name, text):
    m = re.search(r"^\s*function %s\s*\(" % re.escape(name), text, re.M)
    assert m, "function %s is gone from social_media_intelligence.html" % name
    i = text.index("{", m.start())
    depth, j = 0, i
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[m.start():j + 1]
        j += 1
    raise AssertionError("unbalanced braces in %s" % name)


def _extract_const(name, text):
    m = re.search(r"^\s*var %s\s*=\s*.*?;\s*$" % re.escape(name), text, re.M)
    assert m, "var %s is gone from social_media_intelligence.html" % name
    return m.group(0)


@pytest.fixture(scope="module")
def run():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    src = open(_SRC, encoding="utf-8").read()
    js = "\n\n".join(_extract_const(n, src) for n in _CONSTS)
    js += "\n\n" + "\n\n".join(_extract_fn(n, src) for n in _FUNCS)

    def _run(expr):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.js")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(js + "\n\nconsole.log(JSON.stringify(" + expr + "));\n")
            r = subprocess.run([node, p], capture_output=True, text=True, timeout=60)
            assert r.returncode == 0, r.stderr
            return json.loads(r.stdout.strip())

    return _run


LINK = "{href: 'https://acme.com', label: 'acme.com', icon: GLOBE_ICON, title: 'https://acme.com'}"


def test_renderStrategy_uses_the_shared_section_wrapper(run):
    html = run("renderStrategy(['A point.'], null)")
    assert 'class="sci-section"' in html, html
    assert "sci-strategy\"" not in html, "the old bespoke wrapper div should be gone"


def test_renderStrategy_and_renderSection_agree_on_the_label_row(run):
    strategy = run("renderStrategy(['A point.'], null)")
    section = run("renderSection('Content patterns', String.fromCharCode(9670), '<ul><li>x</li></ul>', null)")
    assert 'class="sci-section-label' in strategy
    assert 'class="sci-section-label' in section
    # Messaging & strategy keeps its own accent as an *additional* class, not
    # a replacement for the shared one.
    assert 'sci-section-label sci-section-label-strategy' in strategy, strategy


def test_renderStrategy_can_carry_a_link_chip_like_every_sibling_section(run):
    """This is the concrete regression: renderStrategy() used to take no link
    argument at all, so Messaging & strategy could never show the same
    "open the source" chip that Content patterns and Evidence always could."""
    html = run("renderStrategy(['A point.'], %s)" % LINK)
    assert 'class="sci-seclink"' in html, html
    assert "acme.com" in html


def test_renderStrategy_without_a_link_shows_no_chip(run):
    html = run("renderStrategy(['A point.'], null)")
    assert "sci-seclink" not in html


def test_renderStrategy_returns_empty_for_no_content(run):
    assert run("renderStrategy([], null)") == ""
    assert run("renderStrategy(null, null)") == ""


# ── the CSS shape backing the JS above ────────────────────────────────────

def test_the_bespoke_strategy_label_class_is_gone_from_the_stylesheet():
    css = open(_CSS, encoding="utf-8").read()
    assert ".sci-strategy-label" not in css, (
        "renderStrategy no longer emits .sci-strategy-label -- a leftover rule "
        "here would be dead CSS nobody notices drifting from the markup")
    assert ".sci-strategy {" not in css, (
        "the old wrapper div's box rule should have moved onto .sci-strategy-list "
        "itself, the same way .sci-summary-list carries its own box")


def test_the_strategy_list_carries_its_own_box_like_the_summary_list_does():
    css = open(_CSS, encoding="utf-8").read()
    summary = re.search(r"\.sci-summary-list\s*\{([^}]*)\}", css)
    strategy = re.search(r"\.sci-strategy-list\s*\{([^}]*)\}", css)
    assert summary and strategy
    for prop in ("position: relative", "border-radius", "background:", "border:"):
        assert prop in summary.group(1), summary.group(1)
        assert prop in strategy.group(1), strategy.group(1)


def test_the_strategy_label_override_is_scoped_to_colour_only():
    """The one deliberate difference left: .sci-section-label-strategy exists
    purely to retint the shared label, not to reintroduce its own layout."""
    css = open(_CSS, encoding="utf-8").read()
    rule = re.search(r"\.sci-section-label-strategy\s*\{([^}]*)\}", css)
    assert rule, ".sci-section-label-strategy rule is missing"
    body = rule.group(1)
    assert "color" in body
    for layout_prop in ("display", "padding", "margin", "min-height", "gap"):
        assert layout_prop not in body, (
            "a layout property here would fork Messaging & strategy's row "
            "shape away from every other section again: " + body)
