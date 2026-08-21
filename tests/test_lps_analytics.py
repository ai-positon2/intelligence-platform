"""tracker/lps_analytics.py -- the derived-intelligence layer for LinkedIn
Strategy Researcher runs.

Three separate jobs are covered here, each of which fixes something the
report was previously getting wrong against real vendor responses:

  1. Mojibake repair. The vendor double-decodes text (UTF-8 read as Latin-1),
     so 122 strings on the real Google run and 78 on Myntra arrived garbled,
     including 97 of Google's 100 post bodies. The report rendered them
     verbatim.
  2. Nested-field backfill. `messagingagent.messaging`/`.stats` are top-level
     keys on some runs and nested inside `.summary` on others; the report
     renders by top-level key, so the Messaging tab lost its keyword cloud,
     pains, benefits and stat tiles entirely on the nested shape.
  3. Computed metrics. The vendor's own engagement fields come back empty on
     every real run while the same response carries up to 100 full posts, so
     cadence, engagement rate and format performance are computed here.

The post fixtures below are modelled on the real vendor shape (field names
confirmed from production runs: parsed_datetime, reaction_counter,
comment_counter, repost_counter, attachments[].type of img/video/file,
author.is_company) rather than on what the shape might plausibly be.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import lps_analytics as analytics  # noqa: E402

# Mojibake built from real bytes rather than pasted literals, so this file
# stays plain ASCII: "we<U+2019>re" as UTF-8 misread as Latin-1.
_RIGHT_QUOTE = "’"
_MOJI_QUOTE = _RIGHT_QUOTE.encode("utf-8").decode("latin-1")
_NBSP_ORPHAN = chr(0xC2)  # lead byte whose C2 A0 tail the vendor flattened


def _post(dt="2026-08-10T09:00:00.000Z", text="hello world", reactions=10,
          comments=2, reposts=1, attachments=(), is_company=True, **extra):
    post = {
        "parsed_datetime": dt,
        "text": text,
        "reaction_counter": reactions,
        "comment_counter": comments,
        "repost_counter": reposts,
        "attachments": [{"type": t, "url": "https://x/y"} for t in attachments],
        "author": {"name": "Acme", "is_company": is_company},
        "is_repost": False,
        "share_url": "https://linkedin.com/p/1",
    }
    post.update(extra)
    return post


def _output(posts=None, followers=1000, employees=100, profile=None):
    out = {
        "getcompanypost.items": list(posts or []),
        "getcompanyprofile.followers_count": followers,
        "getcompanyprofile.employee_count": employees,
    }
    if profile is not None:
        out["getcompanyprofile.profile"] = profile
    return out


# ── Mojibake repair ──────────────────────────────────────────────────────────

def test_repair_undoes_utf8_read_as_latin1():
    assert analytics._repair_text("we" + _MOJI_QUOTE + "re") == "we" + _RIGHT_QUOTE + "re"


def test_repair_leaves_clean_ascii_untouched():
    assert analytics._repair_text("plain text, nothing to fix") == "plain text, nothing to fix"


def test_repair_leaves_already_correct_unicode_untouched():
    """Text that is already decoded correctly has no lead+continuation pair,
    so it must pass through rather than being mangled by a second round."""
    clean = "café — already fine"
    assert analytics._repair_text(clean) == clean


def test_repair_drops_an_orphaned_lead_byte():
    """A C2 whose A0 tail was flattened to a space can't decode; it is the
    residue of a non-breaking space and is dropped rather than displayed."""
    assert analytics._repair_text(
        "corridor." + _NBSP_ORPHAN + " Learn more" + _MOJI_QUOTE) == (
        "corridor. Learn more" + _RIGHT_QUOTE)


def test_repair_falls_back_to_per_token_when_the_whole_string_cannot_decode():
    """The vendor truncates long posts mid-character. One broken sequence must
    not block the rest of the string from being repaired."""
    broken_tail = chr(0xF0) + chr(0x9F) + chr(0x9B)  # truncated 4-byte emoji
    out = analytics._repair_text("we" + _MOJI_QUOTE + "re fine " + broken_tail)
    assert "we" + _RIGHT_QUOTE + "re fine" in out


def test_repair_strings_walks_nested_structures_and_leaves_non_strings_alone():
    src = {
        "a": "we" + _MOJI_QUOTE + "re",
        "b": [{"c": "it" + _MOJI_QUOTE + "s"}, 7, None, True],
        "d": {"e": {"f": 1.5}},
    }
    out = analytics.repair_strings(src)
    assert out["a"] == "we" + _RIGHT_QUOTE + "re"
    assert out["b"][0]["c"] == "it" + _RIGHT_QUOTE + "s"
    assert out["b"][1:] == [7, None, True]
    assert out["d"]["e"]["f"] == 1.5


def test_repair_strings_does_not_rewrite_dict_keys():
    """Keys are namespace identifiers, never display text."""
    out = analytics.repair_strings({"strategyagent.strategy": "x"})
    assert list(out) == ["strategyagent.strategy"]


def test_augment_repairs_post_text_and_leaves_the_input_untouched():
    raw = _output([_post(text="we" + _MOJI_QUOTE + "re shipping")])
    out = analytics.augment(raw)
    assert out["getcompanypost.items"][0]["text"] == "we" + _RIGHT_QUOTE + "re shipping"
    assert raw["getcompanypost.items"][0]["text"] == "we" + _MOJI_QUOTE + "re shipping"


# ── Nested-field backfill ────────────────────────────────────────────────────

def test_backfill_hoists_messaging_and_stats_out_of_summary():
    out = {"messagingagent.summary": {
        "text": "prose", "messaging": {"pains": ["p"]}, "stats": [{"h": "H", "v": 1}]}}
    analytics.backfill_nested_fields(out)
    assert out["messagingagent.messaging"] == {"pains": ["p"]}
    assert out["messagingagent.stats"] == [{"h": "H", "v": 1}]


def test_backfill_removes_the_nested_copy_it_hoisted():
    """The report renders one section per top-level key AND renders summary
    itself, so leaving the copy behind showed the whole keyword cloud, pains,
    benefits and stat tiles twice on the Messaging tab."""
    out = {"messagingagent.summary": {"text": "prose", "messaging": {"pains": ["p"]}}}
    analytics.backfill_nested_fields(out)
    assert "messaging" not in out["messagingagent.summary"]
    assert out["messagingagent.summary"]["text"] == "prose"


def test_backfill_does_not_overwrite_an_existing_top_level_value():
    out = {
        "messagingagent.messaging": {"pains": ["real"]},
        "messagingagent.summary": {"messaging": {"pains": ["nested"]}},
    }
    analytics.backfill_nested_fields(out)
    assert out["messagingagent.messaging"] == {"pains": ["real"]}


def test_backfill_is_a_no_op_when_summary_is_a_plain_string():
    out = {"messagingagent.summary": "just prose"}
    analytics.backfill_nested_fields(out)
    assert out == {"messagingagent.summary": "just prose"}


def test_backfill_ignores_empty_nested_values():
    out = {"messagingagent.summary": {"messaging": {}, "stats": []}}
    analytics.backfill_nested_fields(out)
    assert "messagingagent.messaging" not in out
    assert "messagingagent.stats" not in out


# ── Activity / cadence ───────────────────────────────────────────────────────

def test_activity_reports_the_real_window_and_rate():
    posts = [_post(dt="2026-08-03T09:00:00.000Z"), _post(dt="2026-08-10T09:00:00.000Z"),
             _post(dt="2026-08-17T09:00:00.000Z")]
    act = analytics.compute(_output(posts))["derived.activity"]
    assert act["windowStart"] == "2026-08-03"
    assert act["windowEnd"] == "2026-08-17"
    assert act["windowDays"] == 15  # inclusive
    assert act["postsAnalyzed"] == 3
    assert act["longestGapDays"] == 7


def test_cadence_includes_the_weeks_with_no_posts():
    """A company that went dark for a month must show a gap in the cadence
    chart, not a straight line between the two weeks that had posts."""
    posts = [_post(dt="2026-08-03T09:00:00.000Z"), _post(dt="2026-08-24T09:00:00.000Z")]
    cadence = analytics.compute(_output(posts))["derived.activity"]["cadence"]
    assert [b["v"] for b in cadence] == [1, 0, 0, 1]


def test_a_single_day_of_posts_is_a_one_day_window_not_a_divide_by_zero():
    posts = [_post(dt="2026-08-10T09:00:00.000Z"), _post(dt="2026-08-10T17:00:00.000Z")]
    act = analytics.compute(_output(posts))["derived.activity"]
    assert act["windowDays"] == 1
    assert act["postsPerWeek"] == 14


def test_day_of_week_always_covers_all_seven_days():
    """A weekday with no posts has to appear as a measured zero -- "nothing
    on weekends" is a finding, and it is invisible if the bucket is missing."""
    act = analytics.compute(_output([_post(dt="2026-08-10T09:00:00.000Z")]))["derived.activity"]
    assert [d["l"] for d in act["dayOfWeek"]] == ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    assert act["dayOfWeek"][0]["v"] == 1  # 2026-08-10 is a Monday
    assert act["dayOfWeek"][6]["v"] == 0


def test_hour_of_day_covers_all_twenty_four_buckets():
    act = analytics.compute(_output([_post()]))["derived.activity"]
    assert len(act["hourOfDay"]) == 24
    assert act["hourOfDay"][9]["v"] == 1


def test_unparseable_timestamps_are_dropped_not_defaulted_to_now():
    """Defaulting a bad timestamp to the current time would invent activity on
    a date the company never posted."""
    out = analytics.compute(_output([_post(dt="not a date")]))
    assert "derived.activity" not in out or out["derived.activity"] == {}


# ── Engagement ───────────────────────────────────────────────────────────────

def test_engagement_totals_and_averages():
    posts = [_post(reactions=10, comments=2, reposts=1),
             _post(reactions=30, comments=4, reposts=3)]
    eng = analytics.compute(_output(posts))["derived.engagement"]
    assert eng["totalReactions"] == 40
    assert eng["avgReactions"] == 20
    assert eng["avgTotal"] == 25  # (13 + 37) / 2
    assert eng["bestPostEngagement"] == 37


def test_engagement_rate_is_omitted_without_a_real_follower_count():
    """Dividing by a guessed follower base would produce a confident-looking
    number with nothing behind it."""
    eng = analytics.compute(_output([_post()], followers=0))["derived.engagement"]
    assert "engagementRatePct" not in eng
    assert "followers" not in eng


def test_engagement_rate_is_computed_against_the_real_follower_count():
    eng = analytics.compute(_output([_post(reactions=8, comments=1, reposts=1)],
                                    followers=1000))["derived.engagement"]
    assert eng["engagementRatePct"] == 1.0
    assert eng["followers"] == 1000


def test_posts_with_no_engagement_are_counted():
    posts = [_post(reactions=0, comments=0, reposts=0), _post()]
    eng = analytics.compute(_output(posts))["derived.engagement"]
    assert eng["zeroEngagementPosts"] == 1


# ── Format classification ────────────────────────────────────────────────────

@pytest.mark.parametrize("post,expected", [
    (_post(attachments=("video", "img")), "Video"),
    (_post(attachments=("file",)), "Document"),
    (_post(attachments=("img", "img")), "Multi-image"),
    (_post(attachments=("img",)), "Image"),
    (_post(text="read https://example.com now"), "Text + link"),
    (_post(text="just words"), "Text only"),
    (_post(attachments=("img",), poll={"question": "q"}), "Poll"),
    (_post(attachments=("img",), job_posting={"title": "t"}), "Job posting"),
    (_post(attachments=("img",), article={"title": "t"}), "Article share"),
])
def test_post_format_precedence(post, expected):
    """A post with both a video and images is a video post; a poll or job
    posting stays that even when it also carries an image. The report's own
    postFormatLabel() mirrors this ordering, so the format chart and the post
    cards can never disagree about what a post is."""
    assert analytics._post_format(post) == expected


def test_format_performance_is_ranked_by_average_and_carries_its_sample_size():
    posts = [_post(attachments=("video",), reactions=1, comments=0, reposts=0),
             _post(attachments=("video",), reactions=1, comments=0, reposts=0),
             _post(attachments=("img",), reactions=100, comments=0, reposts=0)]
    perf = analytics.compute(_output(posts))["derived.formatPerformance"]
    assert perf[0]["label"] == "Image"
    assert perf[0]["posts"] == 1
    assert perf[-1]["label"] == "Video"
    assert perf[-1]["posts"] == 2


# ── Content signals, voice, top posts ────────────────────────────────────────

def test_hashtags_are_extracted_and_counted():
    posts = [_post(text="ship it #Launch #ai"), _post(text="again #launch")]
    content = analytics.compute(_output(posts))["derived.content"]
    assert content["hashtags"][0] == ["launch", 2]
    assert content["hashtagsUsed"] == 2


def test_content_signals_are_percentages_of_posts():
    posts = [_post(text="why though?"), _post(text="no question here")]
    content = analytics.compute(_output(posts))["derived.content"]
    signals = {s["l"]: s["v"] for s in content["signals"]}
    assert signals["Asks a question"] == 50.0


def test_voice_mix_separates_people_from_the_company_page():
    posts = [_post(is_company=True), _post(is_company=False)]
    voice = analytics.compute(_output(posts))["derived.voice"]
    assert {m["l"]: m["v"] for m in voice["mix"]} == {"Company page": 1, "People": 1}


def test_repost_share_is_reported():
    posts = [_post(), _post(is_repost=True)]
    voice = analytics.compute(_output(posts))["derived.voice"]
    assert voice["repostSharePct"] == 50.0


def test_top_posts_are_ranked_by_total_engagement():
    posts = [_post(text="quiet", reactions=1, comments=0, reposts=0),
             _post(text="loud", reactions=90, comments=5, reposts=5)]
    top = analytics.compute(_output(posts))["derived.topPosts"]
    assert top[0]["excerpt"] == "loud"
    assert top[0]["engagement"] == 100
    assert top[0]["url"] == "https://linkedin.com/p/1"


def test_hiring_polls_and_articles_are_surfaced_from_the_post_feed():
    """None of these have a vendor field of their own; an open role posted to
    a company feed is a GTM signal that was previously discarded."""
    posts = [
        _post(job_posting={"title": "Data Analyst", "location": "Remote",
                           "company": {"name": "Acme"}}),
        _post(poll={"question": "Which?", "total_votes_count": 10, "is_open": True,
                    "options": [{"text": "A", "votes_count": 7},
                                {"text": "B", "votes_count": 3}]}),
        _post(article={"title": "A piece", "url": "https://x/a"}),
    ]
    out = analytics.compute(_output(posts))
    assert out["derived.hiring"][0]["title"] == "Data Analyst"
    assert out["derived.polls"][0]["totalVotes"] == 10
    assert out["derived.polls"][0]["options"][0]["votes"] == 7
    assert out["derived.articles"][0]["url"] == "https://x/a"


# ── Footprint ────────────────────────────────────────────────────────────────

def test_footprint_summarizes_offices_and_headquarters():
    profile = {"locations": [
        {"city": "Mountain View", "country": "US", "is_headquarter": True},
        {"city": "Dublin", "country": "IE"},
        {"city": "Austin", "country": "US"},
    ], "industry": ["Software Development"]}
    foot = analytics.compute(_output([_post()], profile=profile))["derived.footprint"]
    assert foot["officeCount"] == 3
    assert foot["countryCount"] == 2
    assert foot["headquarters"] == "Mountain View, US"
    assert foot["followersPerEmployee"] == 10
    assert foot["industry"] == "Software Development"


def test_a_run_with_no_posts_still_reports_its_footprint():
    """The Boat-shaped case: no post feed at all. Profile facts are still real
    and worth showing, so the section is present while every post-derived
    block is absent rather than zero-filled."""
    out = analytics.compute(_output([], profile={"industry": ["Retail"]}))
    assert out == {"derived.footprint": {"followersPerEmployee": 10, "industry": "Retail"}}


def test_compute_returns_nothing_when_there_is_no_post_feed_and_no_profile():
    assert analytics.compute({"strategyagent.strategy": "text"}) == {}


# ── Insights ─────────────────────────────────────────────────────────────────

def test_insights_are_grounded_in_the_computed_numbers():
    posts = [_post(dt="2026-08-0%dT09:00:00.000Z" % d, attachments=("video",),
                   reactions=5, comments=0, reposts=0) for d in range(3, 8)]
    insights = analytics.compute(_output(posts))["derived.insights"]
    joined = " ".join(insights)
    assert "posts per week" in joined
    assert "heaviest posting day" in joined


def test_no_insights_are_generated_without_the_numbers_to_support_them():
    assert analytics.compute(_output([]))  # footprint only
    assert "derived.insights" not in analytics.compute(_output([]))


def test_the_zero_engagement_insight_is_singular_for_one_post():
    posts = [_post(reactions=0, comments=0, reposts=0), _post()]
    insights = analytics.compute(_output(posts))["derived.insights"]
    assert any("1 post in the window drew no engagement" in s for s in insights)


# ── augment / compact_for_llm ────────────────────────────────────────────────

def test_augment_adds_derived_keys_without_touching_vendor_keys():
    raw = _output([_post()])
    raw["strategyagent.strategy"] = "keep me"
    out = analytics.augment(raw)
    assert out["strategyagent.strategy"] == "keep me"
    assert any(k.startswith("derived.") for k in out)
    assert not any(k.startswith("derived.") for k in raw)


def test_augment_returns_the_output_unchanged_when_computation_fails(monkeypatch):
    """A bug in a derived metric must never cost a reader the vendor data that
    was perfectly fine."""
    monkeypatch.setattr(analytics, "compute", lambda output: 1 / 0)
    raw = {"strategyagent.strategy": "text"}
    assert analytics.augment(raw) == raw


def test_augment_passes_through_a_non_dict():
    assert analytics.augment(None) is None
    assert analytics.augment([1, 2]) == [1, 2]


def test_compact_for_llm_drops_the_raw_post_feed_and_permission_blob():
    """The naive payload was ~320KB per run, mostly attachment URLs and ~72
    viewer-permission booleans."""
    profile = {"tagline": "keep", "viewer_permissions": {"a": True, "b": False}}
    raw = _output([_post() for _ in range(50)], profile=profile)
    raw["_sseDebug"] = {"noise": 1}
    compact = analytics.compact_for_llm(raw)
    assert "getcompanypost.items" not in compact
    assert "getcompanyprofile.profile" not in compact
    assert "_sseDebug" not in compact
    assert compact["getcompanyprofile.profileHighlights"] == {"tagline": "keep"}
    assert compact["postFeedSummary"]["postsAvailable"] == 50
    assert len(compact["postFeedSummary"]["topPosts"]) == 8


def test_compact_for_llm_is_much_smaller_than_the_full_output():
    import json
    raw = _output([_post(text="x" * 800) for _ in range(60)])
    assert len(json.dumps(analytics.compact_for_llm(raw))) < len(json.dumps(raw)) / 4


def test_compact_for_llm_handles_a_non_dict():
    assert analytics.compact_for_llm(None) == {}
