"""The SCI report's client-side rendering, executed rather than read.

The report was rebuilt from one long scroll into a rail of platform panes, and
the logic that came with it is real logic: adaptive time bucketing, a
per-platform statistics pass, and a set of guards about what may be claimed
for a platform whose account was never identified. A text assertion cannot
tell a working guard from a disabled one, so this runs the page's own script
in node against a stub DOM and reads what it actually produced.

Follows tests/test_cpi_dashboard_behaviour.py: skipped, not failed, where node
is unavailable, since the point is extra assurance rather than the only cover.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TEMPLATE = os.path.join(_ROOT, "templates", "social_creative_intelligence.html")

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def _report_script():
    """The page's main <script> block, with Jinja expressions resolved.

    The report JS is inline in the template rather than in static/js, so it is
    pulled out by locating the block that defines renderReport."""
    html = open(_TEMPLATE, encoding="utf-8").read()
    for block in re.findall(r"<script>(.*?)</script>", html, re.S):
        if "function renderReport(" in block:
            # `IS_ADMIN = {{ 'true' if is_admin else 'false' }}` and friends are
            # Jinja, not JavaScript. Admin-only branches are not under test.
            return re.sub(r"\{\{.*?\}\}", "false", block)
    raise AssertionError("could not find the report script block in the template")


_DRIVER = r"""
const fs = require("fs");
const src = fs.readFileSync(process.argv[2], "utf8");
const probe = fs.readFileSync(process.argv[3], "utf8");

// A DOM just big enough for the module-level calls the script makes on load
// (a history-timestamp pass and an admin panel that returns early) plus the
// single element renderReport writes into.
const captured = {};
function el(id){
  return {
    id: id, style: {}, textContent: "", title: "",
    set innerHTML(v){ captured[id] = v; }, get innerHTML(){ return captured[id] || ""; },
    classList: { add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    getAttribute(){ return null; }, setAttribute(){}, appendChild(){},
    querySelectorAll(){ return []; }, querySelector(){ return null; },
  };
}
const nodes = {};
global.document = {
  getElementById(id){ return nodes[id] || (nodes[id] = el(id)); },
  querySelectorAll(){ return []; },
  querySelector(){ return null; },
  createElement(t){ return el(t); },
  addEventListener(){}, head: { appendChild(){} }, body: { appendChild(){}, classList: {add(){},remove(){}} },
};
global.window = { location: { pathname: "/" } };
global.fetch = () => new Promise(() => {});
global.alert = () => {};

const out = (function(){
  eval(src);
  return eval(probe);
})();
console.log(JSON.stringify(out));
"""


def _run(probe_js):
    with tempfile.TemporaryDirectory() as d:
        sp = os.path.join(d, "src.js")
        pp = os.path.join(d, "probe.js")
        dp = os.path.join(d, "driver.js")
        open(sp, "w", encoding="utf-8").write(_report_script())
        open(pp, "w", encoding="utf-8").write(probe_js)
        open(dp, "w", encoding="utf-8").write(_DRIVER)
        r = subprocess.run(["node", dp, sp, pp], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, "node failed:\n%s\n%s" % (r.stdout, r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])


def _posts(platform, n, **kw):
    """n posts on one platform, one per day, newest first."""
    rows = []
    for i in range(n):
        rows.append({
            "id": kw.get("base_id", 1) + i, "platform": platform,
            "post_url": "https://example.invalid/%d" % i,
            "post_type": kw.get("post_type", "image"),
            "caption": "caption %d" % i,
            "posted_at": "2026-08-%02dT10:00:00+00:00" % (28 - i),
            "media_urls": [], "raw": {},
            "metrics": kw.get("metrics", {"likes": 10, "comments": 2}),
            "creative_analysis": None, "creative_analysis_status": None,
        })
    return rows


# ── Brand names ────────────────────────────────────────────────────────────

def test_platform_names_are_spelled_the_way_the_brands_spell_them():
    """CSS capitalize produced Linkedin, Tiktok and Youtube -- three brands
    misspelled in every heading, rail item and card on the page."""
    got = _run("""({
      linkedin: platformLabel('linkedin'), tiktok: platformLabel('tiktok'),
      youtube: platformLabel('youtube'), x: platformLabel('x'),
      unknown: platformLabel('mastodon'), blank: platformLabel(null)
    })""")
    assert got["linkedin"] == "LinkedIn"
    assert got["tiktok"] == "TikTok"
    assert got["youtube"] == "YouTube"
    assert got["x"] == "X"
    # An unknown platform still renders rather than coming out blank.
    assert got["unknown"] == "Mastodon"
    assert got["blank"] == ""


# ── Per-platform statistics ────────────────────────────────────────────────

def test_average_interactions_excludes_views():
    """A view is delivery, not response. Folding it in made every video
    platform's "average engagement" a restatement of its reach."""
    probe = """
      var posts = [
        {platform:'youtube', post_type:'video', posted_at:'2026-08-01T00:00:00Z',
         metrics:{views: 100000, likes: 10, comments: 5}},
        {platform:'youtube', post_type:'video', posted_at:'2026-08-08T00:00:00Z',
         metrics:{views: 200000, likes: 20, comments: 5}}
      ];
      var s = platformStats('youtube', posts);
      ({avgInteractions: s.avgInteractions, avgViews: s.avgViews});
    """
    got = _run(probe)
    assert got["avgInteractions"] == 20        # (15 + 25) / 2, views untouched
    assert got["avgViews"] == 150000


def test_dominant_format_and_share_come_from_the_posts_not_the_narrative():
    probe = """
      var posts = [].concat(
        [{platform:'ig', post_type:'video', metrics:{likes:1}, posted_at:'2026-08-01T00:00:00Z'}],
        [{platform:'ig', post_type:'carousel', metrics:{likes:1}, posted_at:'2026-08-02T00:00:00Z'}],
        [{platform:'ig', post_type:'carousel', metrics:{likes:1}, posted_at:'2026-08-03T00:00:00Z'}],
        [{platform:'ig', post_type:'carousel', metrics:{likes:1}, posted_at:'2026-08-04T00:00:00Z'}]);
      var s = platformStats('ig', posts);
      ({top: s.topType, share: s.topTypeShare, count: s.count});
    """
    got = _run(probe)
    assert got["top"] == "carousel"
    assert got["share"] == 75
    assert got["count"] == 4


def test_platform_stats_is_null_when_the_platform_collected_nothing():
    got = _run("({empty: platformStats('tiktok', []), other: platformStats('tiktok', "
               "[{platform:'x', metrics:{likes:1}}])})")
    assert got["empty"] is None
    assert got["other"] is None


# ── Publishing rhythm ──────────────────────────────────────────────────────

def test_rhythm_draws_silent_buckets_instead_of_closing_the_gap():
    """A month with nothing published IS the finding. Skipping empty buckets
    would draw a steady cadence straight over a two-month silence."""
    probe = """
      var posts = [
        {platform:'a', posted_at:'2026-01-05T00:00:00Z', metrics:{}},
        {platform:'a', posted_at:'2026-01-06T00:00:00Z', metrics:{}},
        {platform:'a', posted_at:'2026-02-20T00:00:00Z', metrics:{}},
        {platform:'a', posted_at:'2026-02-21T00:00:00Z', metrics:{}}
      ];
      var html = renderRhythm(posts);
      ({cols: (html.match(/sci-rh-col/g)||[]).length,
        zeros: (html.match(/sci-rh-zero/g)||[]).length,
        bars: (html.match(/sci-rh-bar"/g)||[]).length});
    """
    got = _run(probe)
    # Six-ish weeks span; only two of them carry posts, the rest are drawn flat.
    assert got["cols"] >= 6
    assert got["bars"] == 2
    assert got["zeros"] == got["cols"] - 2


def test_rhythm_buckets_by_month_once_weeks_would_be_unreadable():
    """A 100-post backfill can span a year; 50-odd weekly columns is a smear."""
    probe = """
      var posts = [];
      for(var m = 1; m <= 12; m++)
        posts.push({platform:'a', posted_at:'2026-' + ('0'+m).slice(-2) + '-10T00:00:00Z', metrics:{}});
      var html = renderRhythm(posts);
      ({cols: (html.match(/sci-rh-col/g)||[]).length, monthly: html.indexOf('in a month') >= 0,
        weekly: html.indexOf('in a week') >= 0});
    """
    got = _run(probe)
    assert got["monthly"] is True and got["weekly"] is False
    assert got["cols"] == 12


def test_rhythm_declines_to_draw_a_chart_from_too_little_data():
    """Three posts over four days is not a rhythm, and a three-bar chart
    implies a cadence that was never measured."""
    probe = """
      var few = [{platform:'a', posted_at:'2026-08-01T00:00:00Z', metrics:{}},
                 {platform:'a', posted_at:'2026-08-02T00:00:00Z', metrics:{}},
                 {platform:'a', posted_at:'2026-08-03T00:00:00Z', metrics:{}}];
      ({few: renderRhythm(few), none: renderRhythm([])});
    """
    got = _run(probe)
    assert got["few"] == ""
    assert got["none"] == ""


# ── Post cards ─────────────────────────────────────────────────────────────

def test_a_text_post_renders_no_media_box():
    """A post with no image is not a broken image: the 16:9 box gave every
    text post a large empty rectangle that reads as a failed thumbnail."""
    probe = """
      var text = {platform:'linkedin', post_type:'text', caption:'no image here',
                  post_url:'https://example.invalid/1', media_urls:[], raw:{}, metrics:{likes:3}};
      var img  = {platform:'linkedin', post_type:'image', caption:'has one',
                  post_url:'https://example.invalid/2', media_urls:['https://example.invalid/i.jpg'],
                  raw:{images:['https://example.invalid/i.jpg']}, metrics:{likes:3}};
      ({textHasMedia: renderPostCard(text).indexOf('sci-postcard-media') >= 0,
        textFlagged:  renderPostCard(text).indexOf('sci-postcard-text') >= 0,
        imgHasMedia:  renderPostCard(img).indexOf('sci-postcard-media') >= 0});
    """
    got = _run(probe)
    assert got["textHasMedia"] is False
    assert got["textFlagged"] is True
    assert got["imgHasMedia"] is True


def test_a_platforms_own_posts_are_not_stamped_with_its_platform_name():
    """Regression: the grid was built with `.map(renderPostCard)`, and
    Array.map passes (element, INDEX, array) -- so the index landed in the
    showPlatform argument and every card after the first grew a redundant
    platform chip inside that platform's own pane."""
    probe = """
      CURRENT_RUN = {posts: [
        {id:1, platform:'x', post_type:'text', caption:'one', post_url:'u1', media_urls:[], raw:{}, metrics:{likes:9}},
        {id:2, platform:'x', post_type:'text', caption:'two', post_url:'u2', media_urls:[], raw:{}, metrics:{likes:8}},
        {id:3, platform:'x', post_type:'text', caption:'three', post_url:'u3', media_urls:[], raw:{}, metrics:{likes:7}}
      ]};
      POST_SORT = {}; POST_SHOWALL = {};
      var section = renderPostsSection('x');
      ({cards: (section.match(/class="sci-postcard[ "]/g)||[]).length,
        stamps: (section.match(/sci-postcard-plat/g)||[]).length});
    """
    got = _run(probe)
    assert got["cards"] == 3
    assert got["stamps"] == 0, "posts in their own platform's pane must not repeat the platform name"


def test_cross_platform_top_posts_do_name_their_platform():
    """The other half of the same rule: on the overview, where posts from
    seven platforms sit in one grid, the platform is the point."""
    probe = """
      var posts = [
        {id:1, platform:'x', post_type:'text', caption:'a', post_url:'u1', media_urls:[], raw:{}, metrics:{likes:90}},
        {id:2, platform:'tiktok', post_type:'video', caption:'b', post_url:'u2', media_urls:[], raw:{}, metrics:{likes:80}},
        {id:3, platform:'youtube', post_type:'video', caption:'c', post_url:'u3', media_urls:[], raw:{}, metrics:{likes:70}}
      ];
      var html = renderTopPostsAnywhere(posts);
      ({stamps: (html.match(/sci-postcard-plat"/g)||[]).length, hasTikTok: html.indexOf('TikTok') >= 0});
    """
    got = _run(probe)
    assert got["stamps"] == 3
    assert got["hasTikTok"] is True


# ── Tiles ──────────────────────────────────────────────────────────────────

def test_tiles_with_no_value_are_dropped_not_rendered_holding_a_dash():
    """An empty tile reads as a broken metric; an absent one reads as "this
    platform does not report that", which is what is actually true -- YouTube
    has no share count."""
    probe = """
      var html = renderTiles([
        {label:'Posts', value: 12},
        {label:'Shares', value: null},
        {label:'Views', value: ''},
        {label:'Likes', value: 0}
      ]);
      ({tiles: (html.match(/class="sci-tile"/g)||[]).length,
        hasShares: html.indexOf('Shares') >= 0,
        hasZero: html.indexOf('Likes') >= 0,
        empty: renderTiles([{label:'x', value:null}])});
    """
    got = _run(probe)
    assert got["tiles"] == 2
    assert got["hasShares"] is False
    # A real zero is a measurement, not a missing value, and must survive.
    assert got["hasZero"] is True
    assert got["empty"] == ""


def test_a_word_valued_tile_is_marked_so_it_does_not_hyphenate():
    got = _run("""({
      word: renderTiles([{label:'Format', value:'Multi-image'}]).indexOf('sci-tile-word') >= 0,
      num:  renderTiles([{label:'Posts', value:'1.6K'}]).indexOf('sci-tile-word') >= 0
    })""")
    assert got["word"] is True
    assert got["num"] is False


# ── Pane wiring and the unidentified-account guards ────────────────────────

def _render_run_probe(extra=""):
    return """
      var run = {
        id: 1, company_name: 'Acme', company_url: 'acme.test', status: 'done',
        created_at: '2026-08-29T10:00:00+00:00', reddit_pulse: null,
        synthesis: {platforms: {}, cross_platform: {summary: [], messaging_and_strategy: [], claims: []}},
        platforms: [
          {platform:'instagram', status:'ok', post_count:2, handle:'@acme',
           source_vendor:'unipile', profile_url:'https://example.invalid/acme'},
          {platform:'linkedin', status:'handle_not_found', post_count:0, handle:'@guessed',
           source_vendor:'unipile', profile_url:'https://example.invalid/guessed',
           status_detail:'Nothing matched confidently.'}
        ],
        posts: [
          {id:1, platform:'instagram', post_type:'image', caption:'one', post_url:'u1',
           media_urls:[], raw:{}, metrics:{likes:5}, creative_analysis_status:'ok'},
          {id:2, platform:'instagram', post_type:'image', caption:'two', post_url:'u2',
           media_urls:[], raw:{}, metrics:{likes:4}, creative_analysis_status:'ok'}
        ]
      };
      renderReport(run);
      var html = document.getElementById('drawerPanel').innerHTML;
      %s
    """ % extra


def test_exactly_one_pane_is_active_on_open():
    got = _run(_render_run_probe("""({
      panes: (html.match(/class="sci-pane[ "]/g)||[]).length,
      active: (html.match(/class="sci-pane active"/g)||[]).length,
      railItems: (html.match(/class="sci-nav-item/g)||[]).length
    })"""))
    assert got["active"] == 1, "opening the report must show one pane, not zero and not all of them"
    # Overview plus the two platforms; no cross pane, since the fixture's
    # cross-platform synthesis is empty.
    assert got["panes"] == 3
    assert got["railItems"] == 3


def test_an_unidentified_account_is_not_given_a_profile_link_or_a_vendor_badge():
    """The row can still carry a half-resolved handle and a vendor from the
    attempt. Rendering them claims an account the report just said it could
    not confidently identify."""
    got = _run(_render_run_probe("""
      function paneOf(ns){
        var chunks = html.split('<section class="sci-pane');
        for(var i = 1; i < chunks.length; i++)
          if(chunks[i].slice(0, 80).indexOf('data-ns="' + ns + '"') >= 0) return chunks[i];
        return '';
      }
      var li = paneOf('linkedin'), ig = paneOf('instagram');
      ({liHasProfile: li.indexOf('Open profile') >= 0,
        liHasVendor: li.indexOf('via Unipile') >= 0,
        liHasHandle: li.indexOf('@guessed') >= 0,
        liExplains: li.indexOf('Account not confidently identified') >= 0,
        igHasProfile: ig.indexOf('Open profile') >= 0,
        igHasVendor: ig.indexOf('via Unipile') >= 0});
    """))
    assert got["liHasProfile"] is False
    assert got["liHasVendor"] is False
    assert got["liHasHandle"] is False
    assert got["liExplains"] is True
    # The platform that WAS identified still gets both.
    assert got["igHasProfile"] is True
    assert got["igHasVendor"] is True


def test_every_platform_gets_a_pane_even_when_synthesis_produced_nothing():
    """Panes are driven by run.platforms, not by synthesis.platforms. Keying
    off synthesis meant a platform with collected posts but no narrative
    vanished from the report entirely."""
    got = _run(_render_run_probe("""
      function paneOf(ns){
        var chunks = html.split('<section class="sci-pane');
        for(var i = 1; i < chunks.length; i++)
          if(chunks[i].slice(0, 80).indexOf('data-ns="' + ns + '"') >= 0) return chunks[i];
        return '';
      }
      ({instagram: paneOf('instagram') !== '', linkedin: paneOf('linkedin') !== '',
        postsRendered: (paneOf('instagram').match(/class="sci-postcard[ "]/g)||[]).length});
    """))
    assert got["instagram"] is True
    assert got["linkedin"] is True
    assert got["postsRendered"] == 2
