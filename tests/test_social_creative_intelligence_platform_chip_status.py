"""renderPlatforms: the live per-platform status chip shown while a run is
still going (polled every 4s by pollRun -- see the page's own <script>).

Before this fix, a platform's chip flipped to its terminal collection status
('ok' or 'low_activity') the moment collection finished and stayed there for
the whole rest of the run while run_platform_creative_analysis kept working
through that platform's posts -- two vendor vision calls each -- in the
background. Nothing distinguished "collected, now analyzing creatives" from
"fully done", which is exactly the ambiguity a long run's "is it stuck?"
question comes from. analyzed_at (sci_pipeline.run_platform_creative_analysis
sets it only once that step actually finishes) is what tells the two apart;
this page's own CSS already had a pulsing style for
data-status="analyzing" grouped with pending/identifying/collecting, unused
until this fix wired it up.

Runs the REAL text of renderPlatforms (extracted from the page's own inline
script, not retyped) in node against a minimal document shim.
"""

import json
import os
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

from test_social_creative_intelligence_datasources import (  # noqa: E402
    _extract_fn, _extract_var, _extract_line_fn, _page_html,
)

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is needed to execute the page script")

_SHIM = """
var __grid = {innerHTML: ''};
global.document = { getElementById: function(id){ return id === 'platformGrid' ? __grid : null; } };
"""


def _platform(**overrides):
    base = {"platform": "instagram", "status": "identifying", "post_count": 0, "analyzed_at": None}
    base.update(overrides)
    return base


def _render(platforms):
    html = _page_html()
    src = "\n".join([
        _extract_var(html, "PLATFORM_META"),
        _extract_var(html, "DEFAULT_META"),
        _extract_var(html, "PLATFORM_ORDER"),
        _extract_var(html, "PLATFORM_LABEL"),
        _extract_line_fn(html, "platformMeta"),
        _extract_fn(html, "platformLabel"),
        _extract_line_fn(html, "esc"),
        _extract_fn(html, "renderPlatforms"),
    ])
    js = (_SHIM + "\n" + src +
         "\nrenderPlatforms(" + json.dumps(platforms) + ");" +
         "\nconsole.log(__grid.innerHTML);")
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, "the extracted script threw:\n%s" % r.stderr[-2000:]
    return r.stdout


def test_collected_but_not_yet_analyzed_shows_analyzing_not_ok():
    html = _render([_platform(status="ok", post_count=25, analyzed_at=None)])
    assert 'data-status="analyzing"' in html
    assert "analyzing creatives" in html
    assert 'data-status="ok"' not in html


def test_low_activity_but_not_yet_analyzed_also_shows_analyzing():
    html = _render([_platform(status="low_activity", post_count=2, analyzed_at=None)])
    assert 'data-status="analyzing"' in html
    assert "analyzing creatives" in html


def test_fully_analyzed_shows_the_real_terminal_status():
    html = _render([_platform(status="ok", post_count=25, analyzed_at="2026-09-07T10:00:00Z")])
    assert 'data-status="ok"' in html
    assert '<span class="pf-status">ok</span>' in html
    assert "analyzing" not in html


def test_a_platform_with_no_posts_is_never_shown_as_analyzing():
    # no_presence/scrape_failed/handle_not_found/error never accumulate
    # posts, so there is nothing for run_platform_creative_analysis to work
    # through -- these must never be relabeled 'analyzing' just because
    # analyzed_at happens to still be null at the moment of a poll.
    for status in ("no_presence", "scrape_failed", "handle_not_found", "error", "collecting", "identifying"):
        html = _render([_platform(status=status, post_count=0, analyzed_at=None)])
        assert 'data-status="%s"' % status in html
        assert "analyzing creatives" not in html
