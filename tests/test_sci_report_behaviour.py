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
// The charts install one delegated tooltip listener and a print-theme pair on
// window at load. They are not what these tests exercise, but the script does
// not get to run at all without somewhere to hang them.
global.window = {
  location: { pathname: "/" },
  addEventListener(){}, innerWidth: 1400, innerHeight: 900,
};
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

def test_rhythm_plots_silent_buckets_as_zero_instead_of_closing_the_gap():
    """A week with nothing published IS the finding. Skipping empty buckets
    would draw a straight line over a six-week silence."""
    probe = """
      var posts = [
        {platform:'a', posted_at:'2026-01-05T00:00:00Z', metrics:{}},
        {platform:'a', posted_at:'2026-01-06T00:00:00Z', metrics:{}},
        {platform:'a', posted_at:'2026-02-20T00:00:00Z', metrics:{}},
        {platform:'a', posted_at:'2026-02-21T00:00:00Z', metrics:{}}
      ];
      var b = bucketPosts(posts);
      var html = renderRhythm(posts);
      ({buckets: b.buckets.length, weekly: b.weekly,
        counted: b.buckets.reduce(function(a, x){ return a + x.n; }, 0),
        silent: b.buckets.filter(function(x){ return x.n === 0; }).length,
        // Every bucket, silent or not, becomes a point on the plotted line.
        hits: (html.match(/class="sci-tr-col"/g)||[]).length,
        saysSilent: html.indexOf('with nothing published') >= 0});
    """
    got = _run(probe)
    assert got["weekly"] is True
    assert got["buckets"] >= 6
    assert got["counted"] == 4
    assert got["silent"] == got["buckets"] - 2
    assert got["hits"] == got["buckets"], "a silent week is a plotted zero, not a missing point"
    assert got["saysSilent"] is True, "the chart says in words how many buckets were empty"


def test_rhythm_buckets_by_month_once_weeks_would_be_unreadable():
    """A 100-post backfill can span a year; 50-odd weekly points is a smear."""
    probe = """
      var posts = [];
      for(var m = 1; m <= 12; m++)
        posts.push({platform:'a', posted_at:'2026-' + ('0'+m).slice(-2) + '-10T00:00:00Z', metrics:{}});
      var b = bucketPosts(posts);
      var html = renderRhythm(posts);
      ({buckets: b.buckets.length, weekly: b.weekly,
        monthly: html.indexOf('in one month') >= 0, saysWeek: html.indexOf('in one week') >= 0});
    """
    got = _run(probe)
    assert got["weekly"] is False
    assert got["monthly"] is True and got["saysWeek"] is False
    assert got["buckets"] == 12


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


# ══ Charts ══════════════════════════════════════════════════════════════════
#
# Every chart in the report is built from these primitives, and the failure
# mode they share is that a broken one still renders. A stacked bar with the
# wrong denominators, an axis whose labels repeat, a hue that moves between
# panes: all of them look like charts. So these read the numbers back.


def test_a_tooltip_cannot_be_talked_into_becoming_markup():
    """Tooltip bodies are escaped twice: once as values inside the markup,
    once because that markup then lives in an HTML attribute, where
    getAttribute hands innerHTML exactly one layer of decoding back. Escape
    only once and a post caption containing a tag becomes a tag."""
    got = _run("""
      var t = tipHtml('<img src=x onerror=alert(1)>',
                      [['Format', '<svg onload=alert(2)>'], ['Posts', '<b>7</b>']]);
      // What the browser stores on the attribute, i.e. one decode of ours.
      var decoded = t.replace(/&quot;/g,'"').replace(/&lt;/g,'<')
                     .replace(/&gt;/g,'>').replace(/&amp;/g,'&');
      ({attr: t, decoded: decoded});
    """)
    # The attribute itself carries no raw angle brackets at all.
    assert "<" not in got["attr"] and ">" not in got["attr"]
    # After the browser's own decode, the only live tags are the ones tipHtml
    # writes. The caption's tag has survived as visible text.
    assert got["decoded"].startswith("<b>")
    # Neither the title nor any row value survives as a live tag.
    assert "<img" not in got["decoded"] and "<svg" not in got["decoded"]
    assert "&lt;img src=x onerror=alert(1)&gt;" in got["decoded"]
    assert "&lt;svg onload=alert(2)&gt;" in got["decoded"]
    # The tooltip's own markup is still markup: it is a formatted body, not
    # a flattened string.
    assert got["decoded"].count("<i>") == 2 and got["decoded"].count("<s>") == 2


def test_axis_maxima_round_up_to_a_readable_step():
    got = _run("""({
      a: niceMax(37), b: niceMax(4), c: niceMax(1), d: niceMax(1400),
      e: niceMax(0), f: niceMax(0.4)
    })""")
    assert got["a"] == 50
    assert got["b"] == 5
    assert got["c"] == 1
    assert got["d"] == 2000
    assert got["e"] == 1          # an empty series still needs a scale
    assert got["f"] == 0.5


def test_a_tiny_axis_does_not_print_the_same_number_twice():
    """With a peak of one post a week the three gridlines are 0, 0.5 and 1,
    which rounded to "0 / 1 / 1" and made the axis look broken."""
    got = _run(r"""
      function pts(vals){ return vals.map(function(v, i){
        return {label: 'w' + i, value: v, tip: ''}; }); }
      var tiny = trendSvg(pts([0,1,0,1,1]), {});
      var big  = trendSvg(pts([0,4,9,3,7]), {});
      function axisLabels(html){
        return (html.match(/class="sci-tr-axis"[^>]*>([^<]*)</g) || [])
          .map(function(m){ return m.slice(m.lastIndexOf('>') + 1, -1); });
      }
      ({tiny: axisLabels(tiny), big: axisLabels(big)});
    """)
    assert got["tiny"] == ["0", "1"], "a duplicate tick label is dropped, not printed twice"
    assert len(got["big"]) == 3 and len(set(got["big"])) == 3


def test_format_performance_is_an_average_not_a_total():
    """A total hands the win to whatever format was published most, which is
    already what the mix bar beside it says. The question here is different."""
    got = _run("""
      var posts = [];
      for(var i = 0; i < 10; i++)
        posts.push({platform:'ig', post_type:'image', metrics:{likes: 100}});
      posts.push({platform:'ig', post_type:'video', metrics:{likes: 900}});
      var html = formatPerf(posts, 'ig');
      ({rows: (html.match(/class="sci-rank-row"/g)||[]).length,
        firstLabel: html.split('sci-rank-l">')[1].split('<')[0],
        values: (html.match(/class="sci-rank-v">([^<]*)</g)||[])
          .map(function(m){ return m.slice(m.indexOf('>') + 1, -1); })});
    """)
    assert got["rows"] == 2
    # Video: one post at 900. Image: ten posts at 100 each, 1000 in total.
    assert got["firstLabel"] == "Video", "the format with the best average leads, not the loudest total"
    assert got["values"] == ["900", "100"]


def test_a_format_keeps_one_hue_in_every_chart_in_every_pane():
    """Colour follows the entity, never its rank in some local array. Keying
    off array position meant "video" was orange on the overview and cyan on
    LinkedIn purely because LinkedIn posts fewer of them."""
    got = _run(r"""
      var run = {id:1, company_name:'A', status:'done', created_at:'2026-08-29T10:00:00+00:00',
        synthesis:null, reddit_pulse:null,
        platforms:[{platform:'instagram', status:'ok', post_count:3},
                   {platform:'linkedin', status:'ok', post_count:2}],
        // "image" is the FIRST post_type any consumer meets, and "video" is
        // the bigger share. Assigning on first sight and assigning on share
        // give different answers here, which is the whole point of the run.
        posts:[
          {id:1, platform:'instagram', post_type:'image', post_url:'u1', media_urls:[], raw:{}, metrics:{likes:5}, posted_at:'2026-08-01T00:00:00Z'},
          {id:2, platform:'instagram', post_type:'video', post_url:'u2', media_urls:[], raw:{}, metrics:{likes:5}, posted_at:'2026-08-02T00:00:00Z'},
          {id:3, platform:'instagram', post_type:'video', post_url:'u3', media_urls:[], raw:{}, metrics:{likes:5}, posted_at:'2026-08-03T00:00:00Z'},
          {id:4, platform:'linkedin',  post_type:'image', post_url:'u4', media_urls:[], raw:{}, metrics:{likes:5}, posted_at:'2026-08-04T00:00:00Z'},
          {id:5, platform:'linkedin',  post_type:'video', post_url:'u5', media_urls:[], raw:{}, metrics:{likes:5}, posted_at:'2026-08-05T00:00:00Z'}]};
      renderReport(run);
      // The class carrying each format's hue, read out of the LinkedIn pane,
      // where "image" outnumbers "video" and a rank-based scheme would flip.
      function ciOf(html, label){
        var i = html.indexOf('>' + label + '<b>');
        if(i < 0) return null;
        var open = html.lastIndexOf('<span', i);
        return (html.slice(open, i).match(/sci-c(\d)/) || [])[1];
      }
      var html = document.getElementById('drawerPanel').innerHTML;
      ({video: FORMAT_CI.video, image: FORMAT_CI.image,
        keyVideo: ciOf(html, 'Video'), keyImage: ciOf(html, 'Image'),
        // A format the seeder never saw takes the ramp's last step rather
        // than colliding with whatever already sits at index 0.
        unseen: formatCi('livestream'),
        // And the map is a lookup: asking about a format must not quietly
        // hand it a hue that some other format already owns.
        stillTwo: Object.keys(FORMAT_CI).length});
    """)
    # Video is the bigger share of the whole run, so it takes the first hue.
    assert got["video"] == 0 and got["image"] == 1
    # And the key in the pane where image leads still paints them that way.
    assert got["keyVideo"] == "0" and got["keyImage"] == "1"
    assert got["unseen"] == 5
    assert got["stillTwo"] == 2


def test_the_trend_offers_only_metrics_that_have_something_in_them():
    """A dead "Views" tab that draws a flat zero teaches the reader the
    toggle is broken. LinkedIn reports no view count at all."""
    got = _run("""
      function feed(withViews){
        var posts = [];
        for(var i = 0; i < 12; i++){
          var m = {likes: 3};
          if(withViews) m.views = 500;
          posts.push({platform:'p', post_type:'text', metrics: m,
                      posted_at: '2026-0' + (1 + Math.floor(i/4)) + '-' + (5 + (i%4)*7) + 'T00:00:00Z'});
        }
        return posts;
      }
      TREND_METRIC = {};
      var noViews = renderRhythm(feed(false), 'a');
      var hasViews = renderRhythm(feed(true), 'b');
      ({noViewsTabs: (noViews.match(/sci-seg-btn/g)||[]).length,
        noViewsHasViews: noViews.indexOf('>Views<') >= 0,
        withViewsTabs: (hasViews.match(/sci-seg-btn/g)||[]).length,
        withViewsHasViews: hasViews.indexOf('>Views<') >= 0});
    """)
    assert got["noViewsTabs"] == 2 and got["noViewsHasViews"] is False
    assert got["withViewsTabs"] == 3 and got["withViewsHasViews"] is True


def test_the_scorecard_sorts_by_the_column_that_was_clicked():
    got = _run(r"""
      CURRENT_RUN = {platforms:[{platform:'instagram'},{platform:'linkedin'}],
        posts:[
          {platform:'instagram', post_type:'image', metrics:{likes: 10}, posted_at:'2026-08-01T00:00:00Z'},
          {platform:'instagram', post_type:'image', metrics:{likes: 10}, posted_at:'2026-08-02T00:00:00Z'},
          {platform:'instagram', post_type:'image', metrics:{likes: 10}, posted_at:'2026-08-03T00:00:00Z'},
          {platform:'linkedin',  post_type:'text',  metrics:{likes: 900}, posted_at:'2026-08-04T00:00:00Z'}]};
      function order(html){
        return (html.match(/sci-sc-body" data-platform="(\w+)"/g)||[])
          .map(function(m){ return m.match(/data-platform="(\w+)"/)[1]; });
      }
      SCORE_SORT = {key:'count', dir:-1};
      var byCount = order(renderScorecard(CURRENT_RUN));
      SCORE_SORT = {key:'avgInteractions', dir:-1};
      var byInter = order(renderScorecard(CURRENT_RUN));
      SCORE_SORT = {key:'count', dir:1};
      var ascending = order(renderScorecard(CURRENT_RUN));
      ({byCount: byCount, byInter: byInter, ascending: ascending,
        onePlatform: renderScorecard({platforms:[{platform:'instagram'}], posts:[
          {platform:'instagram', post_type:'image', metrics:{likes:1}, posted_at:'2026-08-01T00:00:00Z'}]})});
    """)
    assert got["byCount"] == ["instagram", "linkedin"]
    assert got["byInter"] == ["linkedin", "instagram"], "sorting must actually reorder, not just move the arrow"
    assert got["ascending"] == ["linkedin", "instagram"]
    # One platform is not a comparison.
    assert got["onePlatform"] == ""


def test_a_cell_the_platform_does_not_report_is_a_dash_not_a_zero():
    """LinkedIn publishes no view count. Drawing a zero-length bar there says
    "nobody watched", which is a different and false claim."""
    got = _run("""
      function feed(platform, views){
        var out = [];
        for(var i = 0; i < 4; i++){
          var m = {likes: 5};
          if(views) m.views = 900;
          out.push({platform: platform, post_type:'image', metrics: m,
                    posted_at: '2026-08-0' + (1 + i * 2) + 'T00:00:00Z'});
        }
        return out;
      }
      CURRENT_RUN = {platforms:[{platform:'instagram'},{platform:'linkedin'}],
        posts: feed('instagram', true).concat(feed('linkedin', false))};
      SCORE_SORT = {key:'count', dir:-1};
      var html = renderScorecard(CURRENT_RUN);
      var rows = html.split('sci-sc-body');
      ({liDashes: (rows[2].match(/sci-sc-na"/g)||[]).length,
        igDashes: (rows[1].match(/sci-sc-na"/g)||[]).length,
        explains: html.indexOf('does not report that number') >= 0});
    """)
    assert got["liDashes"] == 1, "exactly the one column LinkedIn does not report"
    assert got["igDashes"] == 0
    assert got["explains"] is True


def test_filtering_the_post_grid_narrows_it_and_resets_the_show_all_cap():
    """Carrying "show all" across a filter change means picking one format
    silently expands the grid to every post of it, which is not what the
    click asked for."""
    got = _run("""
      var posts = [];
      for(var i = 0; i < 30; i++)
        posts.push({id: i, platform:'x', post_type: i < 20 ? 'text' : 'image',
                    caption:'c' + i, post_url:'u' + i, media_urls:[], raw:{},
                    metrics:{likes: 30 - i}, posted_at:'2026-08-01T00:00:00Z'});
      CURRENT_RUN = {posts: posts};
      POST_SORT = {}; POST_SHOWALL = {}; POST_FILTER = {}; FORMAT_CI = {};
      var capped = renderPostsSection('x');
      showAllPlatformPosts('x');
      var expanded = renderPostsSection('x');
      setPostFilter('x', 'image');
      var filtered = renderPostsSection('x');
      function cards(h){ return (h.match(/class="sci-postcard[ "]/g)||[]).length; }
      function types(h){ var s = new Set((h.match(/sci-postcard-type">([^<]*)</g)||[])
        .map(function(m){ return m.slice(m.indexOf('>') + 1, -1); })); return [].concat(Array.from(s)); }
      ({capped: cards(capped), expanded: cards(expanded), filtered: cards(filtered),
        filteredTypes: types(filtered), showAllAfterFilter: !!POST_SHOWALL.x,
        chips: (capped.match(/class="sci-fchip[ "]/g)||[]).length,
        cleared: cards((setPostFilter('x', ''), renderPostsSection('x')))});
    """)
    assert got["capped"] == 24 and got["expanded"] == 30
    assert got["filtered"] == 10 and got["filteredTypes"] == ["Image"]
    assert got["showAllAfterFilter"] is False
    # "All", plus one chip per format that actually exists.
    assert got["chips"] == 3
    assert got["cleared"] == 24, "clearing the filter goes back to the capped grid, not to all 30"


def test_a_single_format_gets_no_mix_chart():
    """A full-width bar reading "Video 100%" is a whole section spent
    restating the dominant-format tile above it."""
    got = _run("""
      function post(t){ return {platform:'yt', post_type: t, metrics:{likes: 5}}; }
      ({one: renderFormatBlock([post('video'), post('video')], 'yt'),
        two: renderFormatBlock([post('video'), post('image')], 'yt').indexOf('sci-mixbar') >= 0,
        none: renderFormatBlock([], 'yt')});
    """)
    assert got["one"] == ""
    assert got["two"] is True
    assert got["none"] == ""


def test_a_pair_of_charts_with_nothing_in_either_half_renders_no_heading():
    """An empty wrapper is still a truthy string, and renderSection will
    dutifully draw a section heading over blank space."""
    got = _run("""({
      both: duo('', ''), one: duo('', '<i>x</i>'), two: duo('<i>a</i>', '<i>b</i>')
    })""")
    assert got["both"] == ""
    assert "sci-chart-solo" in got["one"], "a lone chart caps its width instead of stretching"
    assert "sci-chart-solo" not in got["two"]


def test_a_theme_seen_once_is_not_a_recurring_theme():
    got = _run("""
      function post(words){
        return {platform:'ig', creative_analysis_status:'ok',
                creative_analysis: {subject: words[0], setting: words[1]}};
      }
      var recurring = [post(['office','desk']), post(['office','desk']),
                       post(['office','studio']), post(['portrait','desk'])];
      var allSingletons = [post(['a','b']), post(['c','d'])];
      var html = renderThemeChips(recurring, 'ig');
      ({rows: (html.match(/class="sci-rank-row"/g)||[]).length,
        hasStudio: html.indexOf('>studio<') >= 0,
        hasOffice: html.indexOf('>office<') >= 0,
        nothingRecurs: renderThemeChips(allSingletons, 'ig'),
        noAnalysis: renderThemeChips([{platform:'ig', creative_analysis_status:'error'}], 'ig')});
    """)
    # office x3 and desk x2 recur; studio and portrait appear once and go.
    assert got["rows"] == 2
    assert got["hasOffice"] is True and got["hasStudio"] is False
    assert got["nothingRecurs"] == ""
    assert got["noAnalysis"] == ""


def test_the_posting_clock_bins_by_day_and_four_hour_block():
    got = _run(r"""
      function at(iso){ return {platform:'p', metrics:{likes: 4}, posted_at: iso}; }
      var posts = [];
      // Ten posts all in one local Wednesday-morning block, then four more
      // spread across three other blocks.
      for(var i = 0; i < 10; i++) posts.push(at('2026-08-05T09:30:00'));
      posts.push(at('2026-08-02T21:00:00')); posts.push(at('2026-08-02T22:00:00'));
      posts.push(at('2026-08-03T13:00:00')); posts.push(at('2026-08-07T02:00:00'));
      var html = renderClock(posts);
      // The legend below the grid carries one swatch of every level, so the
      // grid is read on its own or every count comes out one too high.
      var grid = html.split('sci-heat-legend')[0];
      var levels = (grid.match(/sci-heat-c sci-heat-(\d)/g)||[])
        .map(function(m){ return +m.slice(-1); });
      ({cells: levels.length, peak: levels.filter(function(l){ return l === 5; }).length,
        empty: levels.filter(function(l){ return l === 0; }).length,
        tooFew: renderClock(posts.slice(0, 5)),
        oneBlockOnly: renderClock(posts.slice(0, 10)),
        saysTimezone: html.indexOf('in your timezone') >= 0});
    """)
    assert got["cells"] == 7 * 6, "seven days by six four-hour blocks, every cell drawn"
    assert got["peak"] == 1, "one Wednesday-morning block holds ten of the fourteen posts"
    assert got["empty"] == 7 * 6 - 4
    assert got["tooFew"] == "", "five posts is not a posting pattern"
    assert got["oneBlockOnly"] == "", "one filled cell in a 42-cell grid is a spike, not a pattern"
    assert got["saysTimezone"] is True, "the grid must say whose clock it is drawn in"


def test_the_engagement_donut_reports_the_share_that_cost_something():
    got = _run("""
      var posts = [{platform:'p', metrics:{likes: 800, comments: 150, shares: 50}}];
      var html = renderEngageMix(posts, null);
      ({centre: (html.match(/sci-dn-v">([^<]*)</)||[])[1],
        arcs: (html.match(/class="sci-dn-arc/g)||[]).length,
        likesOnly: renderEngageMix([{platform:'p', metrics:{likes: 5}}], null),
        nothing: renderEngageMix([{platform:'p', metrics:{}}], null)});
    """)
    assert got["centre"] == "20%", "(150 + 50) of 1000"
    assert got["arcs"] == 3
    # One component is not a composition.
    assert got["likesOnly"] == ""
    assert got["nothing"] == ""


def test_performance_spread_reports_the_median_not_just_the_average():
    """The gap between the best post and the median is the finding. An
    average alone cannot tell an even library from one carried by one post."""
    got = _run("""
      var scores = [1000, 10, 10, 10, 10, 10, 10];
      var posts = scores.map(function(v, i){
        return {id: i, platform:'p', post_type:'text', caption:'c' + i, post_url:'u' + i,
                media_urls:[], raw:{}, metrics:{likes: v}, posted_at:'2026-08-01T00:00:00Z'}; });
      var html = renderPerfSpread(posts, null);
      ({note: (html.match(/sci-chart-note">([^<]*)</)||[])[1],
        rows: (html.match(/class="sci-rank-row"/g)||[]).length,
        tooFew: renderPerfSpread(posts.slice(0, 4), null)});
    """)
    assert "Median post earns 10." in got["note"]
    # 1000 of 1060 interactions sit in the top 10% (one post of seven).
    assert "top 10% of posts take 94% of all interactions" in got["note"]
    assert got["rows"] == 7
    assert got["tooFew"] == ""


def test_no_chart_paints_a_categorical_hue_inline():
    """Inline styles beat the stylesheet, so a hue written inline is one the
    light theme cannot re-step -- and the dark steps measure under 3:1 on
    paper. The only thing a chart may put in a style attribute is geometry."""
    got = _run("""
      var posts = [];
      for(var i = 0; i < 12; i++)
        posts.push({id: i, platform:'p', post_type: ['video','image','text'][i % 3],
                    caption:'c' + i, post_url:'u' + i, media_urls:[], raw:{},
                    metrics:{likes: 10 + i, comments: 3, shares: 1},
                    posted_at: '2026-0' + (1 + Math.floor(i/4)) + '-' + (5 + (i%4)*7) + 'T14:00:00Z'});
      CURRENT_RUN = {posts: posts}; FORMAT_CI = {video:0, image:1, text:2}; TREND_METRIC = {};
      var html = [renderRhythm(posts, 'all'), renderClock(posts),
                  renderFormatBlock(posts, null), renderEngageMix(posts, null),
                  renderPerfSpread(posts, null), renderThemeChips(posts, null)].join('');
      // Every style attribute in every chart, so a stray colour cannot hide.
      ({styles: (html.match(/style="[^"]*"/g)||[]).map(function(s){
          return s.slice(7, -1); })});
    """)
    assert got["styles"], "the probe found no style attributes at all, so it proves nothing"
    for style in got["styles"]:
        assert re.fullmatch(r"(--w|width|height):[\d.]+%?;?", style), (
            "charts may only inline geometry, found: %r" % style)


# ── Links out ──────────────────────────────────────────────────────────────
#
# Every URL the report renders is one the collection returned. Nothing is
# built from a handle, because "https://instagram.com/" + handle produces a
# link indistinguishable from a real one that lands on a 404 or on somebody
# else's account, and a wrong link costs more than an absent one.

def _run_row(platform, **kw):
    row = {"platform": platform, "status": "ok", "post_count": 3,
           "handle": "@acme", "profile_url": "https://example.invalid/%s/acme" % platform}
    row.update(kw)
    return row


def test_a_url_with_another_scheme_is_dropped_not_prefixed():
    """company_url is free text a user typed. Prefixing "https://" onto
    anything that is not already absolute would turn `javascript:alert(1)`
    into a link that runs on click, so a foreign scheme is refused outright
    and only a bare domain is completed."""
    got = _run("""({
      bare: absUrl('acme.com'),
      path: absUrl('acme.com/social'),
      https: absUrl('https://acme.com'),
      http: absUrl('http://acme.com'),
      padded: absUrl('  acme.com  '),
      js: absUrl('javascript:alert(1)'),
      jsCased: absUrl('JavaScript:alert(1)'),
      data: absUrl('data:text/html,<b>x'),
      blank: absUrl(''), nul: absUrl(null)
    })""")
    assert got["bare"] == "https://acme.com"
    assert got["path"] == "https://acme.com/social"
    assert got["https"] == "https://acme.com"
    assert got["http"] == "http://acme.com"
    assert got["padded"] == "https://acme.com"
    for key in ("js", "jsCased", "data", "blank", "nul"):
        assert got[key] is None, "%s should not have become a link: %r" % (key, got[key])


def test_the_directory_lists_the_platforms_with_no_account_too():
    """A directory that shows only the accounts that resolved reads as "they
    are on two platforms", which is a different and unearned claim. The dead
    ones are listed, carrying their reason, and are not anchors."""
    probe = """
      var run = {company_name: 'Acme', company_url: 'acme.com', platforms: [
        %s,
        %s,
        {platform:'tiktok', status:'no_presence', post_count:0, handle:null, profile_url:null}
      ]};
      seedAccountUrls(run);
      var html = renderAccounts(run);
      ({cards: (html.match(/class="sci-acc[ "]/g)||[]).length,
        off: (html.match(/sci-acc-off/g)||[]).length,
        anchors: (html.match(/<a class="sci-acc/g)||[]).length,
        names: (html.match(/sci-acc-n">([^<]*)</g)||[]),
        html: html});
    """ % (json.dumps(_run_row("instagram")),
           json.dumps(_run_row("x", status="handle_not_found", post_count=0,
                               handle=None, profile_url=None)))
    got = _run(probe)
    # site + instagram + x + tiktok
    assert got["cards"] == 4
    assert got["off"] == 2                      # x and tiktok
    assert got["anchors"] == 2                  # site and instagram only
    assert "No account found" in got["html"]
    assert "example.invalid/instagram/acme" in got["html"]


def test_a_platform_we_could_not_identify_leaks_no_url():
    """handle_not_found means we do not have this account. A profile_url still
    sitting on the row is a half-resolved guess, and every surface that could
    render it -- the directory, the rail, a section chip -- must refuse it."""
    probe = """
      var run = {company_url: 'acme.com', platforms: [
        {platform:'youtube', status:'handle_not_found', post_count: 0,
         handle: '@maybe', profile_url: 'https://example.invalid/youtube/maybe'}
      ]};
      seedAccountUrls(run);
      ({accounts: socialAccounts(run).map(function(a){ return [a.platform, a.url, a.handle]; }),
        lookup: accountUrl('youtube'),
        rail: renderSocialRail(run),
        chip: sectionLink(profileLink('youtube')),
        directory: renderAccounts(run)});
    """
    got = _run(probe)
    assert got["accounts"] == [["youtube", None, None]]
    assert got["lookup"] is None
    assert got["rail"] == ""                    # nothing live, so no rail at all
    assert got["chip"] == ""
    assert "example.invalid/youtube/maybe" not in got["directory"]
    assert "@maybe" not in got["directory"]


def test_one_platforms_url_is_never_served_for_another():
    probe = """
      var run = {platforms: [%s, %s]};
      seedAccountUrls(run);
      ({ig: accountUrl('instagram'), li: accountUrl('linkedin'),
        missing: accountUrl('tiktok'), chip: sectionLink(profileLink('tiktok'))});
    """ % (json.dumps(_run_row("instagram")), json.dumps(_run_row("linkedin")))
    got = _run(probe)
    assert got["ig"] == "https://example.invalid/instagram/acme"
    assert got["li"] == "https://example.invalid/linkedin/acme"
    # An unseeded platform gets nothing, never the first entry in the map.
    assert got["missing"] is None
    assert got["chip"] == ""


def test_a_section_chip_is_never_a_dead_link():
    """The chip exists to go somewhere. A post with no post_url produces no
    chip rather than one pointing at "#" -- which looks identical until it is
    clicked, and then does nothing."""
    got = _run("""
      var withUrl = {platform:'x', post_url:'https://example.invalid/x/1',
                     caption:'a real post', raw:{}, metrics:{likes:5}};
      var without = {platform:'x', post_url: null, caption:'no link', raw:{}, metrics:{likes:5}};
      ({ok: sectionLink(postLink(withUrl, 'Best post')),
        // The factory's own contract, not just what the renderer does with it:
        // a link object carrying no destination is a worse value to hand
        // around than null, and sectionLink's href guard hides the difference.
        builtOk: postLink(withUrl, 'Best post'),
        built: postLink(without, 'Best post'),
        builtNull: postLink(null, 'Best post'),
        none: sectionLink(postLink(without, 'Best post')),
        nullPost: sectionLink(postLink(null, 'Best post')),
        noLink: sectionLink(null)});
    """)
    assert 'href="https://example.invalid/x/1"' in got["ok"]
    assert "Best post" in got["ok"]
    assert got["builtOk"]["href"] == "https://example.invalid/x/1"
    assert got["built"] is None
    assert got["builtNull"] is None
    assert got["none"] == ""
    assert got["nullPost"] == ""
    assert got["noLink"] == ""


def test_section_chips_point_at_the_post_each_section_is_about():
    """The whole reason these are per-section is that they are not the profile
    link eight times. Cadence goes to the newest post, format to a post of the
    dominant format, response to the one that earned most."""
    got = _run("""
      var posts = [
        {id:1, platform:'ig', post_type:'video', post_url:'u-video-old', caption:'v1',
         raw:{}, metrics:{likes: 5}, posted_at:'2026-01-01T00:00:00Z'},
        {id:2, platform:'ig', post_type:'image', post_url:'u-image-huge', caption:'i1',
         raw:{}, metrics:{likes: 9000}, posted_at:'2026-02-01T00:00:00Z'},
        {id:3, platform:'ig', post_type:'video', post_url:'u-video-new', caption:'v2',
         raw:{}, metrics:{likes: 7}, posted_at:'2026-06-01T00:00:00Z'}
      ];
      ({newest: latestPost(posts, 'ig').post_url,
        best: bestPost(posts, 'ig').post_url,
        typical: dominantFormatPost(posts, 'ig').post_url,
        otherPlatform: latestPost(posts, 'linkedin')});
    """)
    assert got["newest"] == "u-video-new"
    assert got["best"] == "u-image-huge"
    # Two videos to one image: the dominant format is video, and of the two
    # videos it is the better-performing one that gets linked.
    assert got["typical"] == "u-video-new"
    assert got["otherPlatform"] is None


def test_the_theme_chip_follows_the_theme_charts_own_rule():
    """The chart drops themes seen once, so the chip must too -- otherwise the
    section links to "an example of" a theme the chart itself refuses to draw,
    and the example is the single post that coined it."""
    got = _run("""
      function post(id, subject, likes){
        return {id: id, platform:'ig', post_type:'image', post_url:'u' + id, caption:'c',
                raw:{}, metrics:{likes: likes}, posted_at:'2026-0' + id + '-01T00:00:00Z',
                creative_analysis_status:'ok',
                creative_analysis:{subject: subject, setting:'', style:'', tone:'',
                                   format_technique:''}};
      }
      var repeated = [post(1,'server rack',10), post(2,'server rack',80), post(3,'a lone kite',5)];
      var singles  = [post(1,'server rack',10), post(2,'a lone kite',5)];
      ({repeated: themePost(repeated, 'ig'), singles: themePost(singles, 'ig')});
    """)
    assert got["repeated"]["theme"] == "server rack"
    # Of the two posts carrying it, the stronger one is the example.
    assert got["repeated"]["post"]["post_url"] == "u2"
    assert got["singles"] is None


def test_every_outbound_link_opens_in_a_new_tab_and_drops_the_opener():
    """The report lives in a modal over a long-running analysis. A link that
    navigates the tab away loses it, and target=_blank without rel=noopener
    hands the destination a handle on this window."""
    probe = """
      var run = {company_name:'Acme', company_url:'acme.com', platforms: [%s]};
      seedAccountUrls(run);
      var html = renderSocialRail(run) + renderAccounts(run) +
        renderPlatformCards(['instagram'], {instagram: %s}, []) +
        sectionLink(profileLink('instagram')) +
        sectionLink(siteLink(run));
      var anchors = html.match(/<a [^>]*>/g) || [];
      ({total: anchors.length,
        bad: anchors.filter(function(a){
          return !/target="_blank"/.test(a) || !/rel="noopener"/.test(a); })});
    """ % (json.dumps(_run_row("instagram")), json.dumps(_run_row("instagram")))
    got = _run(probe)
    assert got["total"] >= 5, "the probe found almost no anchors, so it proves nothing"
    assert got["bad"] == [], got["bad"]


# ── The period that has not finished happening ─────────────────────────────
#
# Buckets run to the week or month holding the newest post, which is usually
# the one we are standing in. Drawn solid, that half-finished bucket reads as
# a collapse in output when it is only Tuesday.

def test_a_bucket_still_in_progress_is_reported_as_partial():
    probe = """
      var DAY = 86400000, now = Date.now();
      function at(daysAgo){
        return {platform:'x', post_type:'image', metrics:{likes:5},
                posted_at: new Date(now - daysAgo * DAY).toISOString()};
      }
      // Both series are the same shape and the same length. The only
      // difference is whether the last one landed inside the current week.
      ({open: bucketPosts([40,32,24,16,8,0].map(at)).partial,
        closed: bucketPosts([72,64,56,48,40,32].map(at)).partial})
    """
    got = _run(probe)
    assert got["open"] is True, "a series running up to today reported its last week as finished"
    assert got["closed"] is False, "a series that stopped a month ago claimed to be mid-week"


def test_only_the_unfinished_leg_is_dashed_and_only_when_it_is_unfinished():
    """The dashed tail is a claim about one period. If it swallowed a closed
    period too, the chart would disown a week that really did happen."""
    probe = """
      var pts = [3, 5, 4, 6, 2].map(function(v, i){
        return {label: 'w' + i, value: v, tip: ''};
      });
      function d(svg, cls){
        var m = svg.match(new RegExp('class="' + cls + '" d="([^"]+)"'));
        return m ? m[1] : null;
      }
      var open = trendSvg(pts, {partial: true}), closed = trendSvg(pts, {partial: false});
      function verts(p){ return p == null ? null : p.split(/(?=[ML])/).length; }
      ({openLine: verts(d(open, 'sci-tr-line')), openTail: verts(d(open, 'sci-tr-tail')),
        closedLine: verts(d(closed, 'sci-tr-line')), closedTail: d(closed, 'sci-tr-tail')})
    """
    got = _run(probe)
    # Five points: solid through the first four, dashed across the last leg,
    # and the two share the vertex so no gap opens between them.
    assert got["openLine"] == 4
    assert got["openTail"] == 2
    # Nothing partial: one unbroken line over all five, and no tail at all.
    assert got["closedLine"] == 5
    assert got["closedTail"] is None


def test_two_trends_on_one_page_do_not_share_gradient_ids():
    """The fill and the dot floor are referenced by id. A shared id means the
    second chart on a pane silently repaints the first, which is invisible
    until two charts with different hues sit next to each other."""
    probe = """
      var pts = [1, 2, 3].map(function(v, i){ return {label: 'w' + i, value: v, tip: ''}; });
      function ids(svg){ return (svg.match(/id="[^"]+"/g) || []); }
      var a = ids(trendSvg(pts, {})), b = ids(trendSvg(pts, {}));
      ({a: a, shared: a.filter(function(x){ return b.indexOf(x) >= 0; })})
    """
    got = _run(probe)
    assert len(got["a"]) >= 3, "the trend defines almost no ids, so this proves nothing"
    assert got["shared"] == [], got["shared"]


# ── Radar ──────────────────────────────────────────────────────────────────

def _series(key, *values):
    return {"key": key, "label": key.title(), "values": list(values),
            "display": [str(v) for v in values]}


def test_a_radar_needs_something_to_compare_and_an_area_to_compare_it_on():
    """Two axes is not a shape, it is a line with a fold in it, and one series
    on a normalised radar is a polygon touching every edge that says nothing."""
    probe = """
      var three = [{label:'A'},{label:'B'},{label:'C'}];
      ({oneSeries: radarSvg([%s], three, {}),
        twoAxes: radarSvg([%s, %s], [{label:'A'},{label:'B'}], {}),
        ok: radarSvg([%s, %s], three, {}).indexOf('sci-rd-poly') >= 0})
    """ % (json.dumps(_series("a", 1, 1, 1)), json.dumps(_series("a", 1, .5, .2)),
           json.dumps(_series("b", .3, 1, .6)), json.dumps(_series("a", 1, .5, .2)),
           json.dumps(_series("b", .3, 1, .6)))
    got = _run(probe)
    assert got["oneSeries"] == ""
    assert got["twoAxes"] == ""
    assert got["ok"] is True


def test_past_three_profiles_the_radar_becomes_small_multiples():
    """Overlaid, four or more translucent polygons stop being separable. The
    same numbers become one plot each, on the same axes and the same scale."""
    probe = """
      var axes = [{label:'A'},{label:'B'},{label:'C'}];
      function mk(n){
        var out = [];
        for(var i = 0; i < n; i++){
          out.push({key: 'p' + i, label: 'P' + i, values: [.2 + i * .1, .5, .9 - i * .1],
                    display: ['x', 'y', 'z']});
        }
        return out;
      }
      function count(s, re){ return (s.match(re) || []).length; }
      var three = radarSvg(mk(3), axes, {}), four = radarSvg(mk(4), axes, {});
      ({threeOverlaid: count(three, /class="sci-rd-wrap"/g), threeTiles: count(three, /sci-rd-tile/g),
        fourTiles: count(four, /class="sci-rd-tile"/g), fourGrids: count(four, /class="sci-rd-grid"/g),
        threeGhosts: count(three, /sci-rd-ghost/g), fourGhosts: count(four, /sci-rd-ghost/g)})
    """
    got = _run(probe)
    assert got["threeOverlaid"] == 1 and got["threeTiles"] == 0
    assert got["fourTiles"] == 4 and got["fourGrids"] == 1
    # The group average is the reference a lone tile is read against. Overlaid,
    # the other polygons already are that reference, so it would be clutter.
    assert got["threeGhosts"] == 0
    assert got["fourGhosts"] == 4


def test_a_series_at_the_bottom_of_every_axis_is_still_a_hittable_shape():
    """Plotted honestly at zero, every vertex lands on the centre and several
    such series collapse into one dot nobody can hover or tell apart."""
    probe = """
      var axes = [{label:'A'},{label:'B'},{label:'C'}];
      var svg = radarSvg([%s, %s], axes, {});
      var polys = svg.match(/class="sci-rd-poly" points="([^"]+)"/g) || [];
      var zero = polys[0].match(/points="([^"]+)"/)[1].split(' ');
      ({polys: polys.length, distinct: zero.filter(function(v, i, s){ return s.indexOf(v) === i; }).length,
        dots: (svg.match(/class="sci-rd-pt"/g) || []).length})
    """ % (json.dumps(_series("floor", 0, 0, 0)), json.dumps(_series("peak", 1, 1, 1)))
    got = _run(probe)
    assert got["polys"] == 2
    assert got["distinct"] == 3, "the all-zero series collapsed into a single point"
    # Every vertex of every series keeps its own hit target and its own value.
    assert got["dots"] == 6


def test_the_radar_only_draws_measures_every_platform_reports():
    """A platform that does not publish view counts is unmeasured, not silent.
    Plotting it at zero views would be a different claim entirely, so the axis
    goes for everybody rather than the platform being libelled on it."""
    probe = """
      var run = {status:'done', platforms: [
        {platform:'youtube', status:'ok', post_count:3},
        {platform:'linkedin', status:'ok', post_count:3}], posts: []};
      var DAY = 86400000, now = Date.now();
      ['youtube', 'linkedin'].forEach(function(pf){
        for(var i = 0; i < 3; i++){
          var m = {likes: 10, comments: 1};
          // Only YouTube reports views.
          if(pf === 'youtube') m.views = 900;
          run.posts.push({id: pf + i, platform: pf, post_type: 'video', post_url: 'https://e.invalid/' + pf + i,
            posted_at: new Date(now - (i * 9 + 4) * DAY).toISOString(), metrics: m,
            media_urls: [], raw: {}, caption: 'c'});
        }
      });
      CURRENT_RUN = run;
      SCORE_VIEW = 'shape';
      var html = renderScorecard(run);
      ({axes: (html.match(/class="sci-rd-ax"[^>]*>([^<]+)</g) || []).map(function(s){
          return s.replace(/.*>/, '').slice(0, -1); })})
    """
    got = _run(probe)
    axes = got["axes"]
    # Four axes are drawn once per plot; with two platforms it is one overlaid
    # plot, so each label appears exactly once.
    assert "Avg views" not in axes, "views became an axis although LinkedIn reports none: %r" % (axes,)
    assert "Avg interactions" in axes and "Posts" in axes
    assert len(axes) >= 3, "fewer than three axes survived, so the radar should not have drawn: %r" % (axes,)


# ── LinkedIn thumbnails: two vendors, one raw field ────────────────────────

def test_a_unipile_linkedin_post_shows_its_real_image_not_the_platform_icon():
    """Unipile describes a LinkedIn post's media as attachments[] of
    {type,url}; the Apify actor describes it as a flat images[]. Both land in
    the same raw field, so a resolver that reads only one leaves every post
    from the other vendor with a bare platform icon where its creative
    should be."""
    probe = """
      var unipile = {platform:'linkedin', raw:{attachments:[
        {type:'img', unavailable:false, url:'https://example.invalid/real.jpg'}]}};
      var apify   = {platform:'linkedin', raw:{images:['https://example.invalid/apify.jpg']}};
      ({unipile: postThumbnail(unipile), apify: postThumbnail(apify)});
    """
    got = _run(probe)
    assert got["unipile"] == "https://example.invalid/real.jpg"
    assert got["apify"] == "https://example.invalid/apify.jpg"


def test_a_linkedin_video_attachment_is_never_used_as_a_thumbnail():
    """A video attachment's url is the mp4 itself. Putting it in an <img src>
    is exactly the bug that once broke the thumbnail on every video post on
    every platform: it 404s and hides itself, so it fails invisibly."""
    probe = """
      var video = {platform:'linkedin', raw:{attachments:[
        {type:'video', unavailable:false, url:'https://example.invalid/clip.mp4'}]}};
      var mixed = {platform:'linkedin', raw:{attachments:[
        {type:'video', unavailable:false, url:'https://example.invalid/clip.mp4'},
        {type:'img', unavailable:false, url:'https://example.invalid/poster.jpg'}]}};
      ({video: postThumbnail(video), mixed: postThumbnail(mixed)});
    """
    got = _run(probe)
    assert got["video"] is None
    assert got["mixed"] == "https://example.invalid/poster.jpg"


def test_an_expired_linkedin_attachment_is_skipped_for_the_next_one():
    probe = """
      var post = {platform:'linkedin', raw:{attachments:[
        {type:'img', unavailable:true,  url:'https://example.invalid/gone.jpg'},
        {type:'img', unavailable:false, url:'https://example.invalid/live.jpg'}]}};
      ({thumb: postThumbnail(post)});
    """
    assert _run(probe)["thumb"] == "https://example.invalid/live.jpg"


def test_a_linkedin_link_post_falls_back_to_the_article_cover():
    probe = """
      var post = {platform:'linkedin', raw:{attachments:[],
        article:{title:'x', picture_url:'https://example.invalid/cover.jpg'}}};
      ({thumb: postThumbnail(post)});
    """
    assert _run(probe)["thumb"] == "https://example.invalid/cover.jpg"
