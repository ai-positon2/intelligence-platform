"""One malformed row must never cost a whole batch, in EVERY source
normalizer this platform has.

This exact defect has been found and fixed one module at a time: a bare
media URL string where a {"type": ...} dict was expected crashed
sci_source_x.normalize on a live run, and the same unguarded .get() shape
was then patched separately in Instagram, TikTok, LinkedIn and
tlpr_facebook_pulse. Checking them one file at a time is how
sci_source_facebook.normalize and apify_x_replies.normalize were still
missing the guard afterwards. This file drives all seven through one table.

Nothing is mocked: normalize() is pure, so these call it directly with the
exact row shapes a vendor can actually push into an Apify dataset.
"""

from __future__ import annotations

import pytest

from tracker import (apify_x_replies, sci_source_facebook, sci_source_instagram,
                     sci_source_linkedin_unipile, sci_source_tiktok, sci_source_x,
                     tlpr_facebook_pulse)


class _Hostile:
    """A row that raises from .get() itself -- the case sci_source_x's own
    handler is written never to re-touch, because describing the object
    would raise a second time inside the except block."""

    def get(self, *a, **kw):
        raise ValueError("this row refuses to be read")


def _norm(module, items):
    if module is apify_x_replies:
        return module.normalize("999", items)
    return module.normalize(items)


# module, a valid row, the key its media list lives under
MEDIA_MODULES = [
    (sci_source_x, {"id": "1", "text": "hi"}, "media"),
    (sci_source_instagram, {"id": "1", "caption": "hi"}, "childPosts"),
    (sci_source_tiktok, {"id": "1", "text": "hi"}, "mediaUrls"),
    (sci_source_linkedin_unipile, {"id": "1", "text": "hi"}, "attachments"),
    (sci_source_facebook, {"postId": "1", "text": "hi"}, "media"),
]
_MEDIA_IDS = [m.__name__.rsplit(".", 1)[-1] for m, _row, _key in MEDIA_MODULES]

ALL_MODULES = [(m, row) for m, row, _key in MEDIA_MODULES] + [
    (tlpr_facebook_pulse, {"postId": "1", "postText": "hi"}),
    (apify_x_replies, {"id": "1", "text": "hi"}),
]
_ALL_IDS = _MEDIA_IDS + ["tlpr_facebook_pulse", "apify_x_replies"]


class TestBareStringMediaField:
    @pytest.mark.parametrize("module,row,key", MEDIA_MODULES, ids=_MEDIA_IDS)
    def test_a_media_list_of_bare_url_strings_does_not_lose_the_row(self, module, row, key):
        item = dict(row, **{key: ["https://cdn.example/a.jpg", "https://cdn.example/b.mp4"]})
        out = _norm(module, [item])
        assert len(out) == 1, "the row itself must survive a media shape surprise"

    @pytest.mark.parametrize("module,row,key", MEDIA_MODULES, ids=_MEDIA_IDS)
    def test_a_media_list_mixing_strings_and_dicts_does_not_lose_the_row(self, module, row, key):
        item = dict(row, **{key: ["https://cdn.example/a.jpg",
                                  {"type": "photo", "media_url_https": "https://cdn/b.jpg",
                                   "url": "https://cdn/b.jpg", "displayUrl": "https://cdn/b.jpg"}]})
        assert len(_norm(module, [item])) == 1

    @pytest.mark.parametrize("module,row,key", MEDIA_MODULES, ids=_MEDIA_IDS)
    def test_a_media_field_that_is_a_bare_string_does_not_lose_the_row(self, module, row, key):
        item = dict(row, **{key: "https://cdn.example/a.jpg"})
        assert len(_norm(module, [item])) == 1

    @pytest.mark.parametrize("module,row,key", MEDIA_MODULES, ids=_MEDIA_IDS)
    def test_a_media_field_of_nulls_does_not_lose_the_row(self, module, row, key):
        assert len(_norm(module, [dict(row, **{key: [None, None]})])) == 1

    @pytest.mark.parametrize("module,row,key", MEDIA_MODULES, ids=_MEDIA_IDS)
    def test_a_null_media_field_does_not_lose_the_row(self, module, row, key):
        assert len(_norm(module, [dict(row, **{key: None})])) == 1

    @pytest.mark.parametrize("module,row,key", MEDIA_MODULES, ids=_MEDIA_IDS)
    def test_a_bare_string_media_row_never_costs_the_good_rows_beside_it(
            self, module, row, key):
        good = dict(row)
        good[list(row)[0]] = "2"
        bad = dict(row, **{key: ["https://cdn.example/a.jpg"]})
        out = _norm(module, [bad, good])
        assert len(out) == 2

    @pytest.mark.parametrize("module,row,key", MEDIA_MODULES, ids=_MEDIA_IDS)
    def test_media_urls_are_always_a_list_whatever_the_vendor_sent(self, module, row, key):
        out = _norm(module, [dict(row, **{key: "https://cdn.example/a.jpg"})])
        assert isinstance(out[0]["media_urls"], list)


class TestMalformedRowsAcrossEveryNormalizer:
    @pytest.mark.parametrize("module,row", ALL_MODULES, ids=_ALL_IDS)
    def test_a_bare_string_row_is_skipped_not_fatal(self, module, row):
        assert _norm(module, ["a bare string"]) == []

    @pytest.mark.parametrize("module,row", ALL_MODULES, ids=_ALL_IDS)
    def test_a_null_row_is_skipped_not_fatal(self, module, row):
        assert _norm(module, [None]) == []

    @pytest.mark.parametrize("module,row", ALL_MODULES, ids=_ALL_IDS)
    def test_a_numeric_row_is_skipped_not_fatal(self, module, row):
        assert _norm(module, [42, 3.14]) == []

    @pytest.mark.parametrize("module,row", ALL_MODULES, ids=_ALL_IDS)
    def test_a_list_row_is_skipped_not_fatal(self, module, row):
        assert _norm(module, [["nested"]]) == []

    @pytest.mark.parametrize("module,row", ALL_MODULES, ids=_ALL_IDS)
    def test_a_row_that_raises_from_its_own_get_is_skipped_not_fatal(self, module, row):
        assert _norm(module, [_Hostile()]) == []

    @pytest.mark.parametrize("module,row", ALL_MODULES, ids=_ALL_IDS)
    def test_a_hostile_row_never_costs_the_valid_rows_beside_it(self, module, row):
        out = _norm(module, [_Hostile(), row, "junk", None])
        assert len(out) == 1

    @pytest.mark.parametrize("module,row", ALL_MODULES, ids=_ALL_IDS)
    def test_a_row_with_no_id_is_skipped(self, module, row):
        stripped = {k: v for k, v in row.items() if k not in ("id", "postId")}
        assert _norm(module, [stripped]) == []

    @pytest.mark.parametrize("module,row", ALL_MODULES, ids=_ALL_IDS)
    def test_a_null_batch_is_an_empty_list(self, module, row):
        assert _norm(module, None) == []

    @pytest.mark.parametrize("module,row", ALL_MODULES, ids=_ALL_IDS)
    def test_an_empty_batch_is_an_empty_list(self, module, row):
        assert _norm(module, []) == []

    @pytest.mark.parametrize("module,row", ALL_MODULES, ids=_ALL_IDS)
    def test_a_thousand_rows_with_every_tenth_one_broken_keeps_nine_hundred(self, module, row):
        key = "postId" if "postId" in row else "id"
        batch = []
        for i in range(1000):
            batch.append("junk" if i % 10 == 0 else dict(row, **{key: str(i)}))
        assert len(_norm(module, batch)) == 900


class TestXRepliesNormalizeSpecifics:
    def test_a_bare_string_author_does_not_lose_the_reply(self):
        out = apify_x_replies.normalize("1", [{"id": "9", "text": "hi", "author": "bob"}])
        assert len(out) == 1
        assert out[0]["author"] is None

    def test_a_dict_author_is_read_for_its_handle(self):
        out = apify_x_replies.normalize("1", [{"id": "9", "text": "hi",
                                               "author": {"userName": "bob"}}])
        assert out[0]["author"] == "bob"

    def test_a_reply_to_a_different_tweet_is_excluded(self):
        out = apify_x_replies.normalize("1", [{"id": "9", "text": "hi", "inReplyToId": "2"}])
        assert out == []

    def test_a_reply_to_this_tweet_is_kept(self):
        out = apify_x_replies.normalize("1", [{"id": "9", "text": "hi", "inReplyToId": "1"}])
        assert len(out) == 1

    def test_a_reply_with_no_in_reply_to_field_is_kept(self):
        assert len(apify_x_replies.normalize("1", [{"id": "9", "text": "hi"}])) == 1

    def test_a_numeric_tweet_id_matches_its_string_form(self):
        out = apify_x_replies.normalize(1, [{"id": "9", "text": "hi", "inReplyToId": 1}])
        assert len(out) == 1

    def test_one_hostile_reply_never_costs_the_rest_of_the_thread(self):
        batch = [{"id": "1", "text": "a"}, _Hostile(), {"id": "2", "text": "b"}]
        assert len(apify_x_replies.normalize("1", batch)) == 2


class TestFacebookNormalizeSpecifics:
    def test_a_bare_string_media_entry_no_longer_crashes_the_post_type_read(self):
        out = sci_source_facebook.normalize([
            {"postId": "1", "text": "hi", "media": ["https://cdn.example/a.jpg"]}])
        assert len(out) == 1
        assert out[0]["post_type"] in ("text", "image", "video", "carousel")

    def test_a_dict_media_entry_is_still_read_for_its_url(self):
        out = sci_source_facebook.normalize([
            {"postId": "1", "text": "hi",
             "media": [{"__typename": "Photo", "url": "https://cdn.example/a.jpg"}]}])
        assert out[0]["media_urls"] == ["https://cdn.example/a.jpg"]

    def test_a_mixed_media_list_keeps_only_the_readable_entries(self):
        out = sci_source_facebook.normalize([
            {"postId": "1", "text": "hi",
             "media": ["https://cdn/bare.jpg", {"url": "https://cdn/real.jpg"}]}])
        assert out[0]["media_urls"] == ["https://cdn/real.jpg"]

    def test_the_tlpr_search_normalizer_survives_a_bare_string_timestamp(self):
        out = tlpr_facebook_pulse.normalize([
            {"postId": "1", "postText": "hi", "timestamp": "not a number"}])
        assert out[0]["posted_at"] == "not a number"

    def test_the_tlpr_search_normalizer_survives_an_out_of_range_timestamp(self):
        out = tlpr_facebook_pulse.normalize([
            {"postId": "1", "postText": "hi", "timestamp": 10 ** 20}])
        assert len(out) == 1

    def test_the_tlpr_search_normalizer_coerces_a_string_reaction_count(self):
        out = tlpr_facebook_pulse.normalize([
            {"postId": "1", "postText": "hi", "reactionsCount": "12"}])
        assert out[0]["metrics"]["likes"] == 12

    def test_the_tlpr_search_normalizer_treats_an_unreadable_count_as_zero(self):
        out = tlpr_facebook_pulse.normalize([
            {"postId": "1", "postText": "hi", "reactionsCount": "1.2K"}])
        assert out[0]["metrics"]["likes"] == 0
