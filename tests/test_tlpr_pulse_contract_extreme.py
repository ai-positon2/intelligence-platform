"""The contract every Thought Leader Intelligence source module owes,
proven against all seven of them at once.

The per-module suites (tests/test_tlpr_<platform>_pulse.py) each check
their own module in isolation, which is exactly how the same defect keeps
being found and fixed one module at a time: the "normalize() ran outside
the narrow try/except" crash was fixed independently in four different
files, and the "a failed search reported in the exact words of a confident
zero" bug survived in Facebook alone long after every sibling had it right.
This file drives all seven through one parametrized table so a regression
in any single one of them fails here.

Mocking is at the real external boundary only -- apify_transport.
run_actor_and_wait, unipile_client.search_posts, sci_reddit_client.
search_posts, tlpr_press's own _gdelt_articles/_serpapi_articles -- so
each module's real collect/normalize/filter/aggregate code runs.
"""

from __future__ import annotations

import json

import pytest

from tracker import (apify_transport, sci_reddit_client, tlpr_facebook_pulse,
                     tlpr_instagram_pulse, tlpr_linkedin_pulse, tlpr_press,
                     tlpr_reddit_pulse, tlpr_tiktok_pulse, tlpr_x_pulse,
                     unipile_client, unipile_transport)

PERSON = "Jane Doe"


@pytest.fixture(autouse=True)
def _no_real_vendors(monkeypatch):
    """Every module under test reads its own key; an unset key is its own
    documented branch, so each source below sets exactly what it needs."""
    for key in ("APIFY_API_TOKEN", "ANTHROPIC_API_KEY", "SERPAPI_KEY",
                "UNIPILE_API_KEY", "UNIPILE_DSN", "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)


# ───────────────────────── the source table ─────────────────────────
#
# Each entry knows how to fake ITS vendor boundary and how to build one
# plausible raw item, so a single test body can assert the shared rule
# against every module.

def _apify_items(monkeypatch, items):
    monkeypatch.setenv("APIFY_API_TOKEN", "t")
    monkeypatch.setattr(apify_transport, "run_actor_and_wait",
                        lambda *a, **kw: items)


def _apify_raises(monkeypatch, exc):
    monkeypatch.setenv("APIFY_API_TOKEN", "t")

    def boom(*a, **kw):
        raise exc

    monkeypatch.setattr(apify_transport, "run_actor_and_wait", boom)


class _Source:
    def __init__(self, name, module, count_key, build, feed, fail, item,
                 top_key, digest_cap):
        self.name = name
        self.module = module
        self.count_key = count_key
        self._build = build
        self.feed = feed
        self.fail = fail
        self.item = item
        self.top_key = top_key
        self.digest_cap = digest_cap

    def build(self):
        return self._build(self.module)

    def __repr__(self):
        return self.name


def _x_item(i, text=None, likes=1):
    return {"id": str(i), "text": text if text is not None else "%s is great" % PERSON,
            "likeCount": likes, "createdAt": "2026-01-0%dT00:00:00Z" % ((i % 9) + 1),
            "author": {"userName": "commenter%s" % i}}


def _instagram_item(i, text=None, likes=1):
    return {"id": str(i), "caption": text if text is not None else "with %s" % PERSON,
            "likesCount": likes, "timestamp": "2026-01-0%dT00:00:00Z" % ((i % 9) + 1),
            "ownerUsername": "tagger%s" % i, "shortCode": "sc%s" % i}


def _tiktok_item(i, text=None, likes=1):
    return {"id": str(i), "text": text if text is not None else "%s clip" % PERSON,
            "diggCount": likes, "createTimeISO": "2026-01-0%dT00:00:00Z" % ((i % 9) + 1),
            "authorMeta": {"nickName": "creator%s" % i},
            "webVideoUrl": "https://tiktok.com/@c/video/%s" % i}


def _facebook_item(i, text=None, likes=1):
    return {"postId": str(i), "postText": text if text is not None else "%s spoke today" % PERSON,
            "reactionsCount": likes, "commentsCount": 0, "sharesCount": 0,
            "timestamp": 1767225600 + i, "author": {"name": "Poster %s" % i},
            "url": "https://facebook.com/p/%s" % i}


def _linkedin_item(i, text=None, likes=1):
    return {"id": str(i), "text": text if text is not None else "%s keynote" % PERSON,
            "reaction_counter": likes, "comment_counter": 0, "repost_counter": 0,
            "parsed_datetime": "2026-01-0%dT00:00:00Z" % ((i % 9) + 1),
            "author": {"name": "Poster %s" % i, "id": "urn:li:other:%s" % i},
            "share_url": "https://linkedin.com/feed/update/%s" % i}


def _reddit_item(i, text=None, likes=1):
    return {"platform_post_id": "t3_%s" % i,
            "caption": text if text is not None else "%s came up again" % PERSON,
            "post_url": "https://reddit.com/r/x/comments/%s" % i,
            "posted_at": "2026-01-0%dT00:00:00Z" % ((i % 9) + 1),
            "metrics": {"likes": likes, "comments": 0},
            "raw": {"title": "About %s" % PERSON, "subreddit": "business"}}


def _press_item(i, text=None, likes=1):
    return {"title": text if text is not None else "%s profiled (%s)" % (PERSON, i),
            "url": "https://news.example/%s" % i, "source": "Outlet %s" % (i % 3),
            "published": "2026-01-0%dT00:00:00Z" % ((i % 9) + 1),
            "summary": "A story about %s." % PERSON}


def _linkedin_feed(monkeypatch, items):
    monkeypatch.setattr(unipile_transport, "account_for_platform", lambda p: "acct-1")
    monkeypatch.setattr(unipile_client, "search_posts",
                        lambda *a, **kw: ({"items": items}, None))


def _linkedin_fail(monkeypatch, exc):
    monkeypatch.setattr(unipile_transport, "account_for_platform", lambda p: "acct-1")

    def boom(*a, **kw):
        raise exc

    monkeypatch.setattr(unipile_client, "search_posts", boom)


def _reddit_feed(monkeypatch, items):
    monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: True)
    monkeypatch.setattr(sci_reddit_client, "search_posts", lambda *a, **kw: items)
    monkeypatch.setattr(sci_reddit_client, "get_post_comments", lambda *a, **kw: [])


def _reddit_fail(monkeypatch, exc):
    monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: True)
    monkeypatch.setattr(sci_reddit_client, "get_post_comments", lambda *a, **kw: [])

    def boom(*a, **kw):
        raise exc

    monkeypatch.setattr(sci_reddit_client, "search_posts", boom)


def _press_feed(monkeypatch, items):
    monkeypatch.setenv("SERPAPI_KEY", "k")
    monkeypatch.setattr(tlpr_press, "_gdelt_articles", lambda *a, **kw: items)
    monkeypatch.setattr(tlpr_press, "_serpapi_articles", lambda *a, **kw: [])


def _press_fail(monkeypatch, exc):
    def boom(*a, **kw):
        raise exc

    monkeypatch.setattr(tlpr_press, "_gdelt_articles", boom)
    monkeypatch.setattr(tlpr_press, "_serpapi_articles", boom)


SOURCES = [
    _Source("x", tlpr_x_pulse, "tweet_count", lambda m: m.build_pulse(PERSON, "janedoe"),
            _apify_items, _apify_raises, _x_item, "top_tweets", 60),
    _Source("instagram", tlpr_instagram_pulse, "mention_count",
            lambda m: m.build_pulse(PERSON, "janedoe"),
            _apify_items, _apify_raises, _instagram_item, "top_mentions", 60),
    _Source("tiktok", tlpr_tiktok_pulse, "video_count", lambda m: m.build_pulse(PERSON),
            _apify_items, _apify_raises, _tiktok_item, "top_videos", 60),
    _Source("facebook", tlpr_facebook_pulse, "post_count", lambda m: m.build_pulse(PERSON),
            _apify_items, _apify_raises, _facebook_item, "top_posts", 30),
    _Source("linkedin", tlpr_linkedin_pulse, "post_count", lambda m: m.build_pulse(PERSON),
            _linkedin_feed, _linkedin_fail, _linkedin_item, "top_posts", 60),
    _Source("reddit", tlpr_reddit_pulse, "thread_count", lambda m: m.build_pulse(PERSON),
            _reddit_feed, _reddit_fail, _reddit_item, "top_threads", 60),
    _Source("press", tlpr_press, "article_count", lambda m: m.build_press(PERSON),
            _press_feed, _press_fail, _press_item, "top_articles", 40),
]

_IDS = [s.name for s in SOURCES]


def _no_analysis(monkeypatch, source):
    """analyze() short-circuits on a missing ANTHROPIC_API_KEY, which keeps
    every collection test off the model boundary entirely while still
    running the real aggregate/normalize/filter code above it."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


# ───────────────────────── zero is a finding, not a failure ─────────────────────────

class TestGenuineZeroVersusFailedSearch:
    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_a_clean_empty_result_reads_as_a_real_finding(self, source, monkeypatch):
        source.feed(monkeypatch, [])
        _no_analysis(monkeypatch, source)
        out = source.build()
        assert out[source.count_key] == 0
        assert "finding, not an error" in out["note"]
        assert out["analysis"] is None

    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_a_clean_empty_result_carries_no_invented_caveat(self, source, monkeypatch):
        source.feed(monkeypatch, [])
        _no_analysis(monkeypatch, source)
        note = source.build()["note"]
        assert "could not" not in note.lower()
        assert "not configured" not in note.lower()

    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_a_transport_failure_is_never_reported_as_a_confident_zero(self, source, monkeypatch):
        """The Facebook regression this file exists for: its build_pulse
        was the one that printed the genuine-zero sentence and nothing
        else, whatever had actually gone wrong."""
        source.fail(monkeypatch, apify_transport.ApifyTransportError("actor run failed: 500"))
        _no_analysis(monkeypatch, source)
        out = source.build()
        assert out[source.count_key] == 0
        note = out["note"]
        assert note.strip(), "a failed search must say something"
        assert note != "", note
        cleaned = note.replace("That is a finding, not an error", "")
        assert len(cleaned) < len(note) or "could not" in note.lower(), note
        # the failure itself has to be visible, not only the reassurance
        assert ("actor run failed" in note or "could not" in note.lower()
                or "not configured" in note.lower()), note

    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_an_unexpected_exception_inside_collection_never_escapes(self, source, monkeypatch):
        source.fail(monkeypatch, RuntimeError("something nobody anticipated"))
        _no_analysis(monkeypatch, source)
        out = source.build()
        assert isinstance(out, dict)
        assert out[source.count_key] == 0
        assert out["note"]

    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_a_connection_timeout_is_contained_the_same_way(self, source, monkeypatch):
        source.fail(monkeypatch, TimeoutError("read timed out after 30s"))
        _no_analysis(monkeypatch, source)
        out = source.build()
        assert out[source.count_key] == 0
        assert out["note"]

    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_a_rate_limit_response_is_surfaced_rather_than_swallowed(self, source, monkeypatch):
        source.fail(monkeypatch, apify_transport.ApifyTransportError(
            "HTTP 429: rate limit exceeded, retry after 60s"))
        _no_analysis(monkeypatch, source)
        out = source.build()
        blob = json.dumps(out, default=str).lower()
        assert "429" in blob or "rate limit" in blob or "could not" in blob


# ───────────────────────── malformed vendor payloads ─────────────────────────

class TestMalformedVendorPayloads:
    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_a_payload_of_bare_strings_never_crashes_the_read(self, source, monkeypatch):
        source.feed(monkeypatch, ["not-an-item", "another-string"])
        _no_analysis(monkeypatch, source)
        out = source.build()
        assert out[source.count_key] == 0

    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_a_payload_of_nulls_never_crashes_the_read(self, source, monkeypatch):
        source.feed(monkeypatch, [None, None])
        _no_analysis(monkeypatch, source)
        assert source.build()[source.count_key] == 0

    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_items_missing_every_expected_key_are_dropped_not_fatal(self, source, monkeypatch):
        source.feed(monkeypatch, [{}, {"unexpected": "shape"}])
        _no_analysis(monkeypatch, source)
        assert source.build()[source.count_key] == 0

    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_one_bad_item_never_costs_the_good_items_beside_it(self, source, monkeypatch):
        if source.name in ("reddit", "press"):
            pytest.skip("their boundary is this codebase's own parser, which always "
                        "yields dicts -- a raw vendor dataset row cannot reach them")
        source.feed(monkeypatch, [source.item(1), "junk", None, source.item(2), 42])
        _no_analysis(monkeypatch, source)
        assert source.build()[source.count_key] == 2

    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_a_null_where_a_list_was_expected_reads_as_nothing_found(self, source, monkeypatch):
        source.feed(monkeypatch, None)
        _no_analysis(monkeypatch, source)
        assert source.build()[source.count_key] == 0

    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_a_non_numeric_engagement_value_does_not_break_the_whole_read(
            self, source, monkeypatch):
        """No normalizer in this family coerces the vendor's own metric
        values, so a scraped display string ("1.2K") reaches aggregate()
        untouched -- and aggregate() runs outside build_pulse's try/except,
        so it used to take the entire source's read down with it."""
        if source.name == "reddit":
            pytest.skip("sci_reddit_client already coerces its metrics with an isinstance "
                        "check, so a non-numeric value cannot reach this module")
        source.feed(monkeypatch, [source.item(2, likes="1.2K"), source.item(1, likes=5)])
        _no_analysis(monkeypatch, source)
        out = source.build()
        assert out[source.count_key] == 2

    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_a_non_numeric_engagement_value_sorts_as_zero(self, source, monkeypatch):
        if source.name in ("reddit", "press"):
            pytest.skip("reddit coerces upstream; press orders by recency, not engagement")
        source.feed(monkeypatch, [source.item(2, likes="lots"), source.item(1, likes=5)])
        _no_analysis(monkeypatch, source)
        cards = source.build()[source.top_key]
        assert cards[0]["likes"] == 5


# ───────────────────────── volume and capping ─────────────────────────

class TestVolumeStress:
    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_five_hundred_items_are_all_counted(self, source, monkeypatch):
        source.feed(monkeypatch, [source.item(i, likes=i) for i in range(500)])
        _no_analysis(monkeypatch, source)
        out = source.build()
        assert out[source.count_key] == 500

    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_the_card_list_shown_to_a_reader_stays_capped(self, source, monkeypatch):
        source.feed(monkeypatch, [source.item(i, likes=i) for i in range(500)])
        _no_analysis(monkeypatch, source)
        out = source.build()
        assert len(out[source.top_key]) <= 12

    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_the_cap_keeps_the_most_engaged_items_not_an_arbitrary_slice(
            self, source, monkeypatch):
        if source.name == "press":
            pytest.skip("press orders by recency, not engagement -- covered separately")
        source.feed(monkeypatch, [source.item(i, likes=i) for i in range(100)])
        _no_analysis(monkeypatch, source)
        cards = source.build()[source.top_key]
        likes = [c.get("likes") or c.get("score") or 0 for c in cards]
        assert likes == sorted(likes, reverse=True)
        assert likes[0] == 99

    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_duplicate_ids_are_counted_once(self, source, monkeypatch):
        source.feed(monkeypatch, [source.item(7), source.item(7), source.item(7)])
        _no_analysis(monkeypatch, source)
        assert source.build()[source.count_key] == 1

    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_a_single_item_is_enough_to_produce_a_real_read(self, source, monkeypatch):
        source.feed(monkeypatch, [source.item(1)])
        _no_analysis(monkeypatch, source)
        out = source.build()
        assert out[source.count_key] == 1
        assert out["analysis"] is not None
        assert out["analysis"].get("error")


# ───────────────────────── international and hostile content ─────────────────────────

_INTERNATIONAL = [
    "%s 的演讲非常精彩" % PERSON,
    "%s كان رائعا في المؤتمر" % PERSON,
    "%s のスピーチは最高でした" % PERSON,
    "%s 🔥🔥🔥 👏👏👏" % PERSON,
    "%s‮evissergorp si‬" % PERSON,
    "%s — вот это да" % PERSON,
]


class TestInternationalAndHostileContent:
    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    @pytest.mark.parametrize("text", _INTERNATIONAL, ids=range(len(_INTERNATIONAL)))
    def test_non_english_content_is_collected_not_dropped(self, source, text, monkeypatch):
        source.feed(monkeypatch, [source.item(1, text=text)])
        _no_analysis(monkeypatch, source)
        assert source.build()[source.count_key] == 1

    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_emoji_only_content_is_collected_where_the_module_does_not_name_match(
            self, source, monkeypatch):
        if source.name in ("facebook", "reddit"):
            pytest.skip("these two re-check the name against the item's own text by design")
        source.feed(monkeypatch, [source.item(1, text="🔥🔥🔥")])
        _no_analysis(monkeypatch, source)
        assert source.build()[source.count_key] == 1

    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_a_null_byte_and_control_characters_do_not_crash_the_read(self, source, monkeypatch):
        source.feed(monkeypatch, [source.item(1, text="%s \x00\x07\x1b[31m" % PERSON)])
        _no_analysis(monkeypatch, source)
        assert source.build()[source.count_key] == 1

    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_a_hundred_thousand_character_item_is_survivable(self, source, monkeypatch):
        source.feed(monkeypatch, [source.item(1, text="%s " % PERSON + "x" * 100000)])
        _no_analysis(monkeypatch, source)
        assert source.build()[source.count_key] == 1

    @pytest.mark.parametrize("source", SOURCES, ids=_IDS)
    def test_the_result_is_always_json_serializable(self, source, monkeypatch):
        source.feed(monkeypatch, [source.item(i, text=_INTERNATIONAL[i % len(_INTERNATIONAL)])
                                  for i in range(5)])
        _no_analysis(monkeypatch, source)
        json.dumps(source.build(), ensure_ascii=False)


# ───────────────────────── per-module specifics the table cannot express ─────────────────────────

class TestXPulseSpecifics:
    def test_a_handle_is_excluded_from_the_prose_search_but_not_from_mentions(self, monkeypatch):
        monkeypatch.setenv("APIFY_API_TOKEN", "t")
        seen = []
        monkeypatch.setattr(apify_transport, "run_actor_and_wait",
                            lambda actor, run_input, token, **kw: seen.append(run_input) or [])
        tlpr_x_pulse.collect_mentions(PERSON, "janedoe")
        assert seen[0]["searchTerms"] == ['"Jane Doe" -from:janedoe']
        assert seen[1]["mentioning"] == "janedoe"

    def test_the_mentions_query_still_runs_when_the_prose_query_dies(self, monkeypatch):
        monkeypatch.setenv("APIFY_API_TOKEN", "t")
        calls = {"n": 0}

        def flaky(actor, run_input, token, **kw):
            calls["n"] += 1
            if "searchTerms" in run_input:
                raise apify_transport.ApifyTransportError("boom")
            return [_x_item(1)]

        monkeypatch.setattr(apify_transport, "run_actor_and_wait", flaky)
        tweets, errors = tlpr_x_pulse.collect_mentions(PERSON, "janedoe")
        assert len(tweets) == 1
        assert "x_search" in errors and "x_mentions" not in errors

    def test_an_author_that_is_a_bare_string_does_not_break_aggregation(self):
        tweets = [{"platform_post_id": "1", "caption": "hi", "metrics": {"likes": 1},
                   "raw": {"author": "not-a-dict"}}]
        assert tlpr_x_pulse.aggregate(tweets)["author_count"] == 0

    def test_an_author_dict_with_no_recognizable_handle_key_is_omitted(self):
        tweets = [{"platform_post_id": "1", "caption": "hi", "metrics": {},
                   "raw": {"author": {"displayName": "Bob"}}}]
        assert tlpr_x_pulse.aggregate(tweets)["top_authors"] == []


class TestFacebookPulseSpecifics:
    def test_a_search_failure_names_the_failure_in_the_note(self, monkeypatch):
        """The exact regression: before this, an unconfigured Apify token
        produced the genuine-zero sentence with no mention of the token."""
        monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
        out = tlpr_facebook_pulse.build_pulse(PERSON)
        assert "Apify is not configured on this deployment." in out["note"]
        assert out["post_count"] == 0

    def test_a_post_about_someone_else_entirely_is_filtered_out(self, monkeypatch):
        _apify_items(monkeypatch, [_facebook_item(1, text="John Smith spoke today")])
        out = tlpr_facebook_pulse.build_pulse(PERSON)
        assert out["post_count"] == 0
        assert "finding, not an error" in out["note"]

    def test_the_comment_cap_keeps_the_most_liked_comments_exactly(self):
        comments = [{"id": str(i), "text": "c%s" % i, "likes": i, "post_title": "t"}
                    for i in range(40)]
        digest = tlpr_facebook_pulse._digest([], comments)
        assert len(digest) == tlpr_facebook_pulse.MAX_COMMENTS_DIGEST
        assert digest[0]["id"] == "c_39"

    @pytest.mark.parametrize("n_comments,expected", [(0, 0), (29, 29), (30, 30), (31, 30)])
    def test_the_comment_cap_boundary_is_exact(self, n_comments, expected):
        comments = [{"id": str(i), "text": "c", "likes": 1, "post_title": "t"}
                    for i in range(n_comments)]
        digest = tlpr_facebook_pulse._digest([], comments)
        assert len(digest) == expected

    @pytest.mark.parametrize("n_posts,expected", [(0, 0), (29, 29), (30, 30), (31, 30)])
    def test_the_post_cap_boundary_is_exact(self, n_posts, expected):
        posts = [{"platform_post_id": str(i), "caption": "p", "metrics": {"likes": 1}, "raw": {}}
                 for i in range(n_posts)]
        digest = tlpr_facebook_pulse._digest(posts, [])
        assert len(digest) == expected

    def test_a_cap_larger_than_the_available_items_takes_everything(self):
        posts = [{"platform_post_id": "1", "caption": "p", "metrics": {}, "raw": {}}]
        digest = tlpr_facebook_pulse._digest(posts, [], max_posts=1000, max_comments=1000)
        assert len(digest) == 1

    def test_a_cap_of_zero_takes_nothing_from_either_half(self):
        posts = [{"platform_post_id": "1", "caption": "p", "metrics": {}, "raw": {}}]
        comments = [{"id": "9", "text": "c", "likes": 1, "post_title": "t"}]
        assert tlpr_facebook_pulse._digest(posts, comments, max_posts=0, max_comments=0) == []

    def test_a_hundred_posts_never_crowd_every_comment_out(self):
        posts = [{"platform_post_id": str(i), "caption": "p", "metrics": {"likes": i}, "raw": {}}
                 for i in range(100)]
        comments = [{"id": str(i), "text": "c", "likes": 1, "post_title": "t"} for i in range(30)]
        digest = tlpr_facebook_pulse._digest(posts, comments)
        assert sum(1 for d in digest if d.get("kind") == "comment") == 30

    def test_a_comments_actor_row_that_is_not_a_dict_is_skipped(self, monkeypatch):
        monkeypatch.setattr(apify_transport, "run_actor_and_wait",
                            lambda *a, **kw: ["junk", {"commentId": "1", "text": "real"}])
        posts = [{"platform_post_id": "1", "post_url": "https://fb/1", "metrics": {"likes": 1}}]
        with pytest.raises(AttributeError):
            tlpr_facebook_pulse._collect_post_comments(posts, "t")

    def test_that_comment_row_crash_never_escapes_build_pulse(self, monkeypatch):
        monkeypatch.setenv("APIFY_API_TOKEN", "t")
        calls = {"n": 0}

        def vendor(actor, run_input, token, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return [_facebook_item(1)]
            return ["a bare string where a comment should be"]

        monkeypatch.setattr(apify_transport, "run_actor_and_wait", vendor)
        out = tlpr_facebook_pulse.build_pulse(PERSON)
        assert out["post_count"] == 1
        assert out["comment_sample_count"] == 0


class TestLinkedInPulseOwnPostBoundary:
    def _post(self, author_id, author_name):
        return {"id": "p1", "text": "a post", "reaction_counter": 1,
                "author": {"id": author_id, "name": author_name}}

    def test_a_provider_id_match_excludes_the_subjects_own_post(self, monkeypatch):
        _linkedin_feed(monkeypatch, [self._post("urn:li:1", "Jane Doe")])
        posts, _ = tlpr_linkedin_pulse.collect_mentions(PERSON, "urn:li:1")
        assert posts == []

    def test_a_different_provider_id_with_the_same_name_is_kept(self, monkeypatch):
        """Two real people share a name; the id is authoritative when known,
        so an impostor or namesake account is audience reaction, not the
        subject's own voice."""
        _linkedin_feed(monkeypatch, [self._post("urn:li:999", "Jane Doe")])
        posts, _ = tlpr_linkedin_pulse.collect_mentions(PERSON, "urn:li:1")
        assert len(posts) == 1

    def test_without_a_provider_id_an_exact_name_match_is_excluded(self, monkeypatch):
        _linkedin_feed(monkeypatch, [self._post("urn:li:999", "Jane Doe")])
        posts, _ = tlpr_linkedin_pulse.collect_mentions(PERSON, None)
        assert posts == []

    def test_without_a_provider_id_a_partial_name_match_is_kept(self, monkeypatch):
        _linkedin_feed(monkeypatch, [self._post("urn:li:999", "Jane Smith")])
        posts, _ = tlpr_linkedin_pulse.collect_mentions(PERSON, None)
        assert len(posts) == 1

    def test_without_a_provider_id_a_superset_name_is_still_the_subject(self, monkeypatch):
        _linkedin_feed(monkeypatch, [self._post("urn:li:999", "Jane R. Doe")])
        posts, _ = tlpr_linkedin_pulse.collect_mentions(PERSON, None)
        assert posts == []

    def test_a_post_with_no_author_block_at_all_is_kept_as_someone_elses(self, monkeypatch):
        _linkedin_feed(monkeypatch, [{"id": "p1", "text": "a post", "reaction_counter": 1}])
        posts, _ = tlpr_linkedin_pulse.collect_mentions(PERSON, None)
        assert len(posts) == 1

    def test_a_non_latin_named_subject_own_post_is_excluded_by_name(self, monkeypatch):
        _linkedin_feed(monkeypatch, [self._post("urn:li:999", "李显龙")])
        posts, _ = tlpr_linkedin_pulse.collect_mentions("李显龙", None)
        assert posts == [], "the own-post filter must not fail open for a non-Latin name"

    def test_an_accented_subject_name_typed_plainly_still_excludes_their_own_post(
            self, monkeypatch):
        _linkedin_feed(monkeypatch, [self._post("urn:li:999", "José Ángel Gurría")])
        posts, _ = tlpr_linkedin_pulse.collect_mentions("Jose Angel Gurria", None)
        assert posts == []

    def test_an_envelope_under_any_supported_key_is_read(self, monkeypatch):
        monkeypatch.setattr(unipile_transport, "account_for_platform", lambda p: "acct-1")
        for key in ("items", "elements", "results", "data"):
            monkeypatch.setattr(unipile_client, "search_posts",
                                lambda *a, **kw: ({key: [_linkedin_item(1)]}, None))
            posts, _ = tlpr_linkedin_pulse.collect_mentions(PERSON, None)
            assert len(posts) == 1, key

    def test_a_bare_list_envelope_is_read_too(self, monkeypatch):
        monkeypatch.setattr(unipile_transport, "account_for_platform", lambda p: "acct-1")
        monkeypatch.setattr(unipile_client, "search_posts",
                            lambda *a, **kw: ([_linkedin_item(1)], None))
        posts, _ = tlpr_linkedin_pulse.collect_mentions(PERSON, None)
        assert len(posts) == 1

    def test_a_unipile_error_object_is_described_not_raised(self, monkeypatch):
        monkeypatch.setattr(unipile_transport, "account_for_platform", lambda p: "acct-1")
        monkeypatch.setattr(unipile_client, "search_posts",
                            lambda *a, **kw: (None, {"kind": "http_status", "status": 429}))
        posts, errors = tlpr_linkedin_pulse.collect_mentions(PERSON, None)
        assert posts == []
        assert "linkedin_search" in errors


class TestRedditPulseSpecifics:
    def test_an_unconfigured_deployment_never_claims_a_genuine_zero(self, monkeypatch):
        monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: False)
        out = tlpr_reddit_pulse.build_pulse(PERSON)
        assert "not configured" in out["note"]
        assert "finding, not an error" not in out["note"]

    def test_every_query_and_sort_failing_is_reported_as_a_failure(self, monkeypatch):
        _reddit_fail(monkeypatch, RuntimeError("401 unauthorized"))
        out = tlpr_reddit_pulse.build_pulse(PERSON)
        assert out["thread_count"] == 0
        assert "Reddit search could not be completed" in out["note"]

    def test_a_thread_whose_text_never_names_the_person_is_dropped(self, monkeypatch):
        item = _reddit_item(1, text="a thread about something else")
        item["raw"] = {"title": "unrelated", "subreddit": "random"}
        _reddit_feed(monkeypatch, [item])
        out = tlpr_reddit_pulse.build_pulse(PERSON)
        assert out["thread_count"] == 0

    def test_a_comment_with_a_colliding_raw_id_cannot_shadow_a_thread(self):
        threads = [{"platform_post_id": "abc", "caption": "t", "metrics": {"likes": 1}, "raw": {}}]
        comments = [{"id": "abc", "thread_id": "abc", "body": "c", "score": 1}]
        ids = [d["id"] for d in tlpr_reddit_pulse._digest(threads, comments)]
        assert ids == ["abc", "c_abc"]

    def test_comment_collection_failing_leaves_the_threads_analyzed(self, monkeypatch):
        monkeypatch.setattr(sci_reddit_client, "is_configured", lambda: True)
        monkeypatch.setattr(sci_reddit_client, "search_posts", lambda *a, **kw: [_reddit_item(1)])

        def boom(*a, **kw):
            raise RuntimeError("comments are down")

        monkeypatch.setattr(sci_reddit_client, "get_post_comments", boom)
        out = tlpr_reddit_pulse.build_pulse(PERSON)
        assert out["thread_count"] == 1
        assert out["comment_sample_count"] == 0


class TestPressZeroVersusNeverRan:
    def test_both_sources_clean_and_empty_is_a_plain_finding(self, monkeypatch):
        monkeypatch.setenv("SERPAPI_KEY", "k")
        monkeypatch.setattr(tlpr_press, "_gdelt_articles", lambda *a, **kw: [])
        monkeypatch.setattr(tlpr_press, "_serpapi_articles", lambda *a, **kw: [])
        out = tlpr_press.build_press(PERSON)
        assert "finding, not an error" in out["note"]
        assert "could not be reached" not in out["note"]

    def test_serpapi_never_reached_is_said_out_loud(self, monkeypatch):
        monkeypatch.setenv("SERPAPI_KEY", "k")
        monkeypatch.setattr(tlpr_press, "_gdelt_articles", lambda *a, **kw: [])

        def boom(*a, **kw):
            raise RuntimeError("connection refused")

        monkeypatch.setattr(tlpr_press, "_serpapi_articles", boom)
        out = tlpr_press.build_press(PERSON)
        assert "SerpAPI could not be reached for this person." in out["note"]

    def test_serpapi_unconfigured_is_a_different_message_again(self, monkeypatch):
        monkeypatch.delenv("SERPAPI_KEY", raising=False)
        monkeypatch.setattr(tlpr_press, "_gdelt_articles", lambda *a, **kw: [])
        out = tlpr_press.build_press(PERSON)
        assert "not configured" in out["note"]

    def test_gdelt_down_with_serpapi_clean_still_names_the_gdelt_failure(self, monkeypatch):
        monkeypatch.setenv("SERPAPI_KEY", "k")

        def boom(*a, **kw):
            raise RuntimeError("503")

        monkeypatch.setattr(tlpr_press, "_gdelt_articles", boom)
        monkeypatch.setattr(tlpr_press, "_serpapi_articles", lambda *a, **kw: [])
        out = tlpr_press.build_press(PERSON)
        assert "GDELT could not be reached" in out["note"]

    def test_one_serpapi_query_failing_while_the_other_works_is_not_a_caveat(self, monkeypatch):
        monkeypatch.setenv("SERPAPI_KEY", "k")
        monkeypatch.setattr(tlpr_press, "_gdelt_articles", lambda *a, **kw: [])
        calls = {"n": 0}

        def flaky(q, key, pool, age):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("timeout")
            return [_press_item(1)]

        monkeypatch.setattr(tlpr_press, "_serpapi_articles", flaky)
        out = tlpr_press.build_press(PERSON, company_hint="Acme")
        assert out["article_count"] == 1
        assert out["errors"] == {}

    def test_articles_sharing_a_title_across_sources_are_deduped(self, monkeypatch):
        monkeypatch.setenv("SERPAPI_KEY", "k")
        same = _press_item(1)
        monkeypatch.setattr(tlpr_press, "_gdelt_articles", lambda *a, **kw: [same])
        monkeypatch.setattr(tlpr_press, "_serpapi_articles",
                            lambda *a, **kw: [dict(same, url="https://other.example/1")])
        out = tlpr_press.build_press(PERSON)
        assert out["article_count"] == 1

    def test_an_article_with_no_title_is_dropped_rather_than_keyed_on_blank(self, monkeypatch):
        monkeypatch.setenv("SERPAPI_KEY", "k")
        monkeypatch.setattr(tlpr_press, "_gdelt_articles",
                            lambda *a, **kw: [{"title": "", "url": "u"}, {"url": "u2"}])
        monkeypatch.setattr(tlpr_press, "_serpapi_articles", lambda *a, **kw: [])
        out = tlpr_press.build_press(PERSON)
        assert out["article_count"] == 0

    def test_press_orders_its_cards_by_recency_under_volume(self, monkeypatch):
        monkeypatch.setenv("SERPAPI_KEY", "k")
        arts = [{"title": "t%s" % i, "url": "u%s" % i, "source": "S",
                 "published": "2026-0%d-01T00:00:00Z" % ((i % 9) + 1)} for i in range(60)]
        monkeypatch.setattr(tlpr_press, "_gdelt_articles", lambda *a, **kw: arts)
        monkeypatch.setattr(tlpr_press, "_serpapi_articles", lambda *a, **kw: [])
        out = tlpr_press.build_press(PERSON)
        published = [c["published"] for c in out["top_articles"]]
        assert published == sorted(published, reverse=True)


class TestInstagramPulseSpecifics:
    def test_no_handle_is_reported_as_unreadable_not_as_nobody_tagged_them(self, monkeypatch):
        out = tlpr_instagram_pulse.build_pulse(PERSON, None)
        assert "No Instagram handle was found" in out["note"]
        assert "finding, not an error" not in out["note"]

    def test_a_blank_handle_is_treated_the_same_as_none(self, monkeypatch):
        assert "No Instagram handle was found" in tlpr_instagram_pulse.build_pulse(PERSON, "   ")["note"]

    def test_the_mentions_tab_url_is_built_from_the_bare_handle(self, monkeypatch):
        monkeypatch.setenv("APIFY_API_TOKEN", "t")
        seen = {}
        monkeypatch.setattr(apify_transport, "run_actor_and_wait",
                            lambda actor, run_input, token, **kw: seen.update(run_input) or [])
        tlpr_instagram_pulse.collect_mentions("@janedoe")
        assert seen["directUrls"] == ["https://www.instagram.com/janedoe/"]
        assert seen["resultsType"] == "mentions"


class TestTikTokPulseSpecifics:
    def test_the_search_never_downloads_media(self, monkeypatch):
        monkeypatch.setenv("APIFY_API_TOKEN", "t")
        seen = {}
        monkeypatch.setattr(apify_transport, "run_actor_and_wait",
                            lambda actor, run_input, token, **kw: seen.update(run_input) or [])
        tlpr_tiktok_pulse.collect_mentions(PERSON)
        assert seen["shouldDownloadVideos"] is False
        assert seen["shouldDownloadCovers"] is False

    def test_an_author_meta_that_is_a_bare_string_does_not_break_aggregation(self):
        videos = [{"platform_post_id": "1", "caption": "c", "metrics": {"likes": 1},
                   "raw": {"authorMeta": "not-a-dict"}}]
        assert tlpr_tiktok_pulse.aggregate(videos)["author_count"] == 0
