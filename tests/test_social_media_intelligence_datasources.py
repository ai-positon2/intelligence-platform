"""The Data sources panel's Apify block: dsApifyRow / loadApifyDataSources.

Runs the REAL text of these functions (extracted from the page's own inline
script, not retyped) in node against a minimal fetch/document shim, rather
than asserting on the source with a grep -- a text match cannot tell a
working status-class branch from a disabled one, and this file exists
specifically to prove the three renderable states (not configured, token
rejected, mixed per-platform results) come out right.

Deliberately does NOT boot the whole ~2700-line inline script (which runs
renderHistoryTimestamps()/renderDataSourcesPanel() at load and would need a
much larger shim to survive unrelated to this change) -- narrow, anchored
extraction of just the functions this feature added plus their direct,
already-shipped dependencies (platformMeta/esc/PLATFORM_META/DEFAULT_META/
APIFY_PLATFORMS), each located by a real match against the live template so
a rename or removal fails this test loudly instead of silently.
"""

import json
import os
import re
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

import app as appmod  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to execute the page script")

_PAGE = "/p2/b2b-agents/social-media-intelligence"


def _page_html():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "harness@position2.com", "name": "T"}
    resp = c.get(_PAGE)
    assert resp.status_code == 200, "the page did not render (%s)" % resp.status_code
    return resp.get_data(as_text=True)


def _balanced(html, start_at, open_ch, close_ch):
    """From `start_at` (the index of the first `open_ch`), return the index
    just past its matching `close_ch`, tracking string literals so a brace
    inside a quoted SVG path or a message string is never miscounted.

    Also skips `//` line comments: an apostrophe in a comment's own prose
    ("a video's folded fields...", and this codebase's comments are full of
    exactly that contraction) is not a string delimiter, but a naive scan
    reads it as one, desyncs the quote-tracking state for everything after
    it, and silently over-runs the real end of the function -- this bit a
    ~10-line function once already, extracting 12000+ characters instead."""
    depth = 0
    i = start_at
    in_str = None
    n = len(html)
    while i < n:
        c = html[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
        elif c == "/" and i + 1 < n and html[i + 1] == "/":
            nl = html.find("\n", i)
            i = n if nl == -1 else nl
            continue
        elif c in ("'", '"'):
            in_str = c
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    raise AssertionError("unbalanced %s/%s starting at %d" % (open_ch, close_ch, start_at))


def _extract_var(html, name):
    """`var NAME = { ... };` or `var NAME = [ ... ];`, real text from the
    page, terminated at the statement's own closing `;` (not merely the
    matching brace, since `esc()`'s object-literal argument sits INSIDE a
    trailing `.replace(...)` call)."""
    m = re.search(r"var %s = ([\[{])" % re.escape(name), html)
    assert m, "no `var %s = ...` on the page" % name
    open_ch = m.group(1)
    close_ch = "}" if open_ch == "{" else "]"
    end = _balanced(html, m.end() - 1, open_ch, close_ch)
    semi = html.index(";", end)
    return html[m.start():semi + 1]


def _extract_fn(html, name):
    """`function NAME(...) { ... }`, real text from the page."""
    m = re.search(r"function %s\(" % re.escape(name), html)
    assert m, "no `function %s(...)` on the page" % name
    brace = html.index("{", m.end())
    end = _balanced(html, brace, "{", "}")
    return html[m.start():end]


def _extract_line_fn(html, name):
    """`function NAME(...) { ... }` confined to ONE source line. `esc()`'s
    body contains a `/[&<>"]/g` regex literal whose bracketed `"` is not a
    string delimiter at all, which desyncs `_balanced`'s naive quote
    tracking; every function this small on this page is one physical line,
    so matching to end-of-line sidesteps the regex-vs-string ambiguity
    entirely rather than trying to parse it correctly."""
    m = re.search(r"function %s\(.*" % re.escape(name), html)
    assert m, "no `function %s(...)` on the page" % name
    return m.group(0)


_SHIM = """
global.esc2 = function(s){ return s; }; // unrelated same-file helper, never called here
var __body = {innerHTML: ''};
global.document = {
  getElementById: function(id){ return id === 'dsApifyBody' ? __body : null; }
};
var __fetchCalls = [];
global.fetch = function(url, opts){
  __fetchCalls.push(url);
  return Promise.resolve({
    json: function(){ return Promise.resolve(__FETCH_REPLY__); }
  });
};
"""


def _run(fetch_reply):
    html = _page_html()
    src = "\n".join([
        _extract_var(html, "PLATFORM_META"),
        _extract_var(html, "DEFAULT_META"),
        _extract_line_fn(html, "platformMeta"),
        _extract_line_fn(html, "esc"),
        _extract_var(html, "APIFY_PLATFORMS"),
        _extract_fn(html, "dsApifyRow"),
        _extract_fn(html, "loadApifyDataSources"),
    ])
    js = (_SHIM.replace("__FETCH_REPLY__", json.dumps(fetch_reply))
          + "\n" + src +
          "\nloadApifyDataSources();"
          "\nsetTimeout(function(){"
          "  console.log(JSON.stringify({html: __body.innerHTML, fetched: __fetchCalls}));"
          "}, 0);")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, "the extracted script threw:\n%s" % r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_hits_the_real_apify_check_route():
    out = _run({"configured": True, "ok": True, "account": "position2", "actors": {}})
    assert out["fetched"] == ["/p2/admin/external-usage/sci-apify-check"]


def test_not_configured_state_names_the_env_var():
    out = _run({"configured": False})
    assert "APIFY_API_TOKEN" in out["html"]
    assert "sci-datasrc-status" not in out["html"]


def test_a_rejected_token_shows_the_servers_own_error_not_a_generic_one():
    out = _run({"configured": True, "ok": False, "error": "Apify rejected this token (401 Unauthorized)."})
    assert "401" in out["html"]


def test_each_platform_gets_the_right_status_class_and_nothing_is_unescaped():
    out = _run({
        "configured": True, "ok": True, "account": "position2",
        "actors": {
            "facebook": {"enabled": True, "accessible": True, "name": "Facebook Posts Scraper"},
            "x": {"enabled": True, "accessible": False, "error": "<script>evil</script>"},
            "linkedin": {"enabled": False},
        },
    })
    html = out["html"]
    assert 'class="sci-datasrc-status ok">actor reachable' in html
    assert "Facebook Posts Scraper" in html
    assert 'class="sci-datasrc-status err"' in html
    # LinkedIn's absence-by-design must render as its own neutral state, not
    # be silently dropped from the row list and not look like a failure.
    assert 'class="sci-datasrc-status off">not used here (by design)</span>' in html
    # A vendor-supplied error string must never reach the page unescaped.
    assert "<script>evil</script>" not in html
    assert "&lt;script&gt;evil&lt;/script&gt;" in html


def test_the_note_reports_which_account_the_token_belongs_to():
    out = _run({"configured": True, "ok": True, "account": "position2-agency", "actors": {}})
    assert "position2-agency" in out["html"]
