"""The light/dark page ground on the root element.

aurora-app.css carries `body{background:transparent!important}`, so on every page
that loads it the visible page ground can only come from a rule on the root
element. That makes the root background selectors load-bearing, and it makes them
easy to break in a way nothing else notices: a selector that is syntactically
valid but can never match produces no error, no warning, and no visual change in
the default (dark) theme -- only light theme silently keeps the dark ground.

That is exactly what happened. The light ground was written

    :root[data-theme="light"] html,[data-theme="light"] html

and shipped dead, because :root IS the html element -- both halves ask for an
html element *nested inside* a light-themed html, which no document contains.
Every page relying on it rendered light-mode (dark) text on the dark #070912
ground.

So these tests pin two things a browser confirmed but no other test would catch:
that the light selector can actually match a root element, and that the cascade
weights still land in the right order. The weights matter because :root is a
pseudo-CLASS, not a type selector, so the intuitive reading is wrong:

    html                            0,0,1
    :root                           0,1,0
    html[data-theme="light"]        0,1,1
    :root[data-theme="light"]       0,2,0
    html:root[data-theme="light"]   0,2,1

aurora's light ground must beat aurora's own `html{...}` dark base (so it wins on
specificity rather than on load order), and must NOT beat a page that declares
its own `html[data-theme="light"]{...}` later -- templates/context.html keeps its
light gradient that way, and spelling aurora's rule `:root[data-theme="light"]`
(0,2,0) instead flattens that gradient to a solid fill.
"""
import os
import re

CSS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "static", "css")
AURORA = os.path.join(CSS_DIR, "aurora-app.css")
GENTLE = os.path.join(CSS_DIR, "gentle_dental_slot_checker.css")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _strip_comments(css):
    """Comments are prose, not cascade.

    The comment above the fixed rule quotes the dead selector verbatim so the
    next reader knows what not to write again -- so anything scanning for that
    text has to look at declarations only, or it flags the explanation.
    """
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _specificity(selector):
    """(ids, classes, types) for the simple root selectors used in this file.

    Deliberately narrow: it handles #id, .class, :pseudo-class, [attr] and type
    names, which is everything the root-ground rules use. Descendant selectors
    are rejected outright -- a root ground with a combinator is the bug this
    module exists to catch, so silently scoring one would defeat the purpose.
    """
    assert not re.search(r"[\s>+~]", selector.strip()), (
        "root ground selector must be a single compound selector, got %r" % selector)
    s = selector.strip()
    ids = len(re.findall(r"#[\w-]+", s))
    # ::pseudo-elements count as types, :pseudo-classes as classes; only the
    # latter appear here, but strip element syntax first so it cannot be mistaken.
    s_no_el = re.sub(r"::[\w-]+", "", s)
    classes = (len(re.findall(r"\.[\w-]+", s_no_el))
               + len(re.findall(r"(?<!:):[\w-]+", s_no_el))
               + len(re.findall(r"\[[^\]]*\]", s_no_el)))
    types = len(re.findall(r"(?:^|[\s>+~])([a-zA-Z][\w-]*)", re.sub(r"\[[^\]]*\]", "", s_no_el)))
    return (ids, classes, types)


def _root_bg_rules(css):
    """[(selector, declarations)] for every rule that sets a background on the
    root element, in source order."""
    out = []
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", _strip_comments(css)):
        if "background" not in body:
            continue
        for part in sel.split(","):
            p = part.strip()
            if re.match(r"^(html|:root)[^\s>+~]*$", p):
                out.append((p, body.strip()))
    return out


def test_specificity_helper_matches_the_css_spec():
    """Guard the guard: if this drifts, every assertion below is meaningless."""
    assert _specificity("html") == (0, 0, 1)
    assert _specificity(":root") == (0, 1, 0)
    assert _specificity('html[data-theme="light"]') == (0, 1, 1)
    assert _specificity(':root[data-theme="light"]') == (0, 2, 0)
    assert _specificity('html:root[data-theme="light"]') == (0, 2, 1)


def test_aurora_light_ground_can_actually_match_a_root_element():
    """The regression: no root ground may be a descendant selector.

    `:root[data-theme="light"] html` parses and scores respectably; it just never
    matches anything. Requiring a single compound selector rules that out.
    """
    css = _read(AURORA)
    light = [(s, d) for s, d in _root_bg_rules(css) if "data-theme" in s]
    assert light, "aurora-app.css declares no light-theme root ground at all"
    for sel, _ in light:
        assert not re.search(r"[\s>+~]", sel), (
            "light ground %r is a descendant selector -- :root IS html, so this "
            "can never match" % sel)
        assert "light" in sel

    # And the dead form specifically must not come back. Declarations only --
    # the comment above the fixed rule quotes it on purpose.
    assert 'data-theme="light"] html' not in _strip_comments(css)


def test_aurora_light_ground_outweighs_its_own_dark_base():
    """Light must win on specificity, not on which line happens to come last."""
    rules = _root_bg_rules(_read(AURORA))
    dark = [s for s, _ in rules if "data-theme" not in s]
    light = [s for s, _ in rules if 'data-theme="light"' in s]
    assert dark and light
    for d in dark:
        for l in light:
            assert _specificity(l) > _specificity(d), (
                "light ground %r (%s) does not outweigh dark base %r (%s)"
                % (l, _specificity(l), d, _specificity(d)))


def test_aurora_light_ground_yields_to_a_pages_own_light_ground():
    """A page that sets `html[data-theme="light"]` later must still win.

    templates/context.html relies on this for its light gradient. If aurora's
    rule is raised to :root[data-theme="light"] (0,2,0) it outranks the page and
    silently replaces that gradient with a flat fill.
    """
    light = [s for s, _ in _root_bg_rules(_read(AURORA)) if 'data-theme="light"' in s]
    page_rule = _specificity('html[data-theme="light"]')
    for sel in light:
        assert _specificity(sel) <= page_rule, (
            "aurora light ground %r (%s) outranks a page's own "
            'html[data-theme="light"] (%s) and would override it'
            % (sel, _specificity(sel), page_rule))


def test_slot_checker_grounds_still_outrank_aurora():
    """gentle_dental_slot_checker.css loads BEFORE aurora-app.css.

    It therefore loses every specificity tie on load order, and has to beat
    aurora outright in both themes to keep its own ground.
    """
    aurora = _root_bg_rules(_read(AURORA))
    gentle = _root_bg_rules(_read(GENTLE))
    assert gentle, "slot checker declares no root ground"

    def best(rules, light):
        cand = [s for s, _ in rules
                if ('data-theme="light"' in s) is light]
        return max((_specificity(s) for s in cand), default=None)

    for light in (False, True):
        g, a = best(gentle, light), best(aurora, light)
        assert g is not None and a is not None
        assert g > a, ("slot checker ground %s does not outrank aurora %s for "
                       "light=%s; it loads first, so a tie means aurora wins" % (g, a, light))
