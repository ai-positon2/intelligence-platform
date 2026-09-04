"""The company avatar's fallback chain, executed rather than read.

A text assertion on the bundle cannot tell a working fallback from a broken one:
the string `icons.duckduckgo.com` is present either way, and the thing that
actually matters is what `avatarHtml` returns for a company with no Apollo logo.
So the four real functions are lifted out of the template and run in node.

Two claims are load-bearing and neither is visible in the text:

  1. The initials are in the returned markup even when an image is layered over
     them. That is the whole fallback: the image removes itself on error and
     uncovers what is already there. A version that emitted only the image, and
     rebuilt the letters inside onerror, passes every text assertion and shows
     an empty box the moment the handler misfires.
  2. The favicon source must be DuckDuckGo specifically. It answers 404 for a
     domain with no icon, which is what makes onerror fire. Services that always
     answer 200 serve their own generated glyph, so onerror never fires and the
     initials below are unreachable. Icon Horse was measured doing exactly that
     and is the reason this test names the host.

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
_SRC = os.path.join(_ROOT, "tracker", "dashboard_builder.py")
_WANT = ("esc", "initials", "faviconUrl", "avatarHtml")


def _extract(name, text):
    """Pull one JS function out of the template by brace matching.

    Regex cannot do this: every one of these bodies contains braces inside
    template literals, and a greedy or lazy match lands mid-function.
    """
    m = re.search(r"^function %s\s*\(" % re.escape(name), text, re.M)
    assert m, "function %s is gone from dashboard_builder.py" % name
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


@pytest.fixture(scope="module")
def run():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    src = open(_SRC, encoding="utf-8").read()
    js = "\n\n".join(_extract(n, src) for n in _WANT)

    def _run(expr):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.js")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(js + "\n\nconsole.log(JSON.stringify(" + expr + "));\n")
            r = subprocess.run([node, p], capture_output=True, text=True, timeout=60)
            assert r.returncode == 0, r.stderr
            return json.loads(r.stdout.strip())

    return _run


# ── the fallback chain ────────────────────────────────────────────────────

def test_apollo_logo_wins_when_present(run):
    html = run("avatarHtml({name:'Acme Corp', logo_url:'https://cdn.example/a.png',"
               " domain:'acme.com'})")
    assert "cdn.example/a.png" in html
    assert "icons.duckduckgo.com" not in html, (
        "a company with a real Apollo logo should not also fetch a favicon")
    assert "ca-fav" not in html, "an Apollo logo must keep cover, not favicon padding"


def test_favicon_is_used_when_apollo_has_no_logo(run):
    html = run("avatarHtml({name:'Acme Corp', domain:'acme.com'})")
    assert "https://icons.duckduckgo.com/ip3/acme.com.ico" in html, html
    assert "ca-fav" in html, "a favicon needs contain+padding or it renders cropped"


def test_initials_survive_underneath_every_image(run):
    """The point of the whole arrangement. If the letters are not already in the
    markup there is nothing for a failed image to uncover."""
    for company in ("{name:'Acme Corp', logo_url:'https://cdn.example/a.png'}",
                    "{name:'Acme Corp', domain:'acme.com'}",
                    "{name:'Acme Corp'}"):
        html = run("avatarHtml(%s)" % company)
        assert "<span>AC</span>" in html, (company, html)


def test_an_image_removes_itself_rather_than_rebuilding_the_parent(run):
    html = run("avatarHtml({name:'Acme Corp', domain:'acme.com'})")
    assert "onerror=\"this.remove()\"" in html, html
    assert "parentElement.innerHTML" not in html, (
        "rebuilding the parent from onerror throws away the initials that are "
        "already rendered, and cannot fall through to a further source")


def test_no_image_at_all_without_a_logo_or_domain(run):
    html = run("avatarHtml({name:'Acme Corp'})")
    assert "<img" not in html, html
    assert "<span>AC</span>" in html


# ── the source is not interchangeable ─────────────────────────────────────

def test_the_favicon_host_answers_404_for_a_missing_icon(run):
    """Named deliberately. DuckDuckGo 404s a domain with no icon, so onerror
    fires and the initials show. Icon Horse answers 200 with a generated glyph
    for any input, including nonsense domains, which would silently replace
    every missing logo and make the initials unreachable. Swapping the host is
    therefore a behaviour change, not a preference."""
    url = run("faviconUrl('acme.com')")
    assert url.startswith("https://icons.duckduckgo.com/ip3/"), url
    assert "icon.horse" not in url


# ── domain normalisation ──────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expect", [
    ("acme.com", "acme.com"),
    ("ACME.COM", "acme.com"),
    ("  acme.com  ", "acme.com"),
    ("https://acme.com", "acme.com"),
    ("http://www.acme.com", "acme.com"),
    ("https://www.acme.com/careers/all", "acme.com"),
    ("acme.co.uk", "acme.co.uk"),
    ("sub.acme.com", "sub.acme.com"),
])
def test_a_domain_is_normalised_before_it_becomes_a_url(run, raw, expect):
    url = run("faviconUrl(%s)" % json.dumps(raw))
    assert url == "https://icons.duckduckgo.com/ip3/%s.ico" % expect, (raw, url)


@pytest.mark.parametrize("raw", ["", "   ", "localhost", "not a domain",
                                 "acme", "/", "https://"])
def test_anything_that_is_not_a_domain_yields_no_url(run, raw):
    """An avatar is worth less than a request to a made-up host on every row of
    a dashboard, so the guard rejects rather than guesses."""
    assert run("faviconUrl(%s)" % json.dumps(raw)) == "", raw


def test_a_null_or_missing_domain_does_not_throw(run):
    assert run("faviconUrl(null)") == ""
    assert run("faviconUrl(undefined)") == ""
    assert run("avatarHtml({name:'Acme Corp', domain:null}).indexOf('<img')") == -1


# ── the CSS the layering depends on ───────────────────────────────────────

def test_the_avatar_box_anchors_its_absolute_image():
    """`position:absolute; inset:0` on the image is meaningless unless the box
    around it establishes a containing block. Without this the logo positions
    against the page and lands somewhere else entirely."""
    src = open(_SRC, encoding="utf-8").read()
    box = re.search(r"\.company-avatar\{([^}]*)\}", src)
    assert box, ".company-avatar rule is gone"
    assert "position:relative" in box.group(1).replace(" ", ""), box.group(1)
    img = re.search(r"\.company-avatar img\{([^}]*)\}", src)
    assert img, ".company-avatar img rule is gone"
    body = img.group(1).replace(" ", "").replace("\n", "")
    assert "position:absolute" in body and "inset:0" in body, body
