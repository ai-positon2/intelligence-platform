"""tracker/sci_pipeline.py -- WHICH image each vendor is actually shown.

Two bugs of one shape, both of which left creative_analysis empty on posts
whose creative was sitting right there on the row:

1. The fallback for a video whose frames could not be extracted read only
   `raw.snippet.thumbnails`, the YouTube Data API's shape. YouTube is not
   the only platform whose frame extraction fails (yt-dlp and ffmpeg get
   blocked from a datacenter IP, and a signed CDN link expires), but it was
   the only one given a fallback: an Instagram reel, a TikTok video, a
   LinkedIn video or a Facebook video that failed extraction was recorded as
   "Could not extract any video frames" by BOTH vendors while its cover
   image went unused. On an account that posts mostly reels, that is most of
   the creative in the report.

2. A post not typed as a video was analyzed at `media_urls[0]`
   unconditionally, which is a video URL more often than it sounds: an
   Instagram carousel is typed "carousel" the moment it has childPosts even
   when the parent item carries a videoUrl, and an X or Facebook post with
   several media is typed "carousel" whichever kind leads. Sending an .mp4
   URL to two vision models fails at both of them, every time.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_pipeline  # noqa: E402


def _post(pid=1, platform="instagram", post_type="reel", media_urls=None, raw=None):
    return {"id": pid, "platform": platform, "post_type": post_type,
            "caption": "a caption", "media_urls": media_urls or [],
            "raw": raw or {}}


def _drive(monkeypatch, post, frames=None):
    """Run the real run_platform_creative_analysis over one post with the
    vendor call and frame extraction faked, and report what it was shown
    and what got stored."""
    from tracker import sci_store
    seen = {"claude_urls": [], "claude_frames": 0, "extract_urls": [], "transcribed": []}
    stored = {}

    monkeypatch.setattr(sci_store, "get_posts", lambda run_id, platform: [post])
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)
    monkeypatch.setattr(sci_store, "log_spend", lambda *a, **k: None)
    monkeypatch.setattr(sci_store, "update_post_creative_analysis",
                        lambda post_id, analysis, status="ok", error=None:
                        stored.update(claude=(analysis, status, error)))

    def fake_extract(url, n):
        seen["extract_urls"].append(url)
        return frames or []
    monkeypatch.setattr("tracker.sci_video.extract_frames", fake_extract)

    def ok(field):
        return {"subject": "a real read", "summary": "s", "messaging": field}

    monkeypatch.setattr("tracker.sci_vision.analyze_image",
                        lambda url, context=None: (seen["claude_urls"].append(url),
                                                   ok("claude"))[1])
    monkeypatch.setattr("tracker.sci_vision.analyze_image_bytes",
                        lambda b, context=None: (seen.__setitem__("claude_frames",
                                                                  seen["claude_frames"] + 1),
                                                 ok("claude"))[1])
    monkeypatch.setattr("tracker.sci_audio.transcribe_video",
                        lambda url: (seen["transcribed"].append(url), "a transcript")[1])

    sci_pipeline.run_platform_creative_analysis(1, post["platform"])
    return seen, stored


# ── 1. Every platform's own cover image, not just YouTube's ────────────────

def test_an_instagram_reel_falls_back_to_its_display_image(monkeypatch):
    post = _post(platform="instagram", post_type="reel",
                 media_urls=["https://scontent.cdninstagram.com/v/clip.mp4?_nc_ht=x&oe=1"],
                 raw={"displayUrl": "https://scontent.cdninstagram.com/v/cover.jpg?oe=2"})
    seen, stored = _drive(monkeypatch, post)
    assert seen["claude_urls"] == ["https://scontent.cdninstagram.com/v/cover.jpg?oe=2"]
    assert stored["claude"][1] == "ok"
    assert "frame_extraction_note" in stored["claude"][0]


def test_a_tiktok_video_falls_back_to_its_cover(monkeypatch):
    post = _post(platform="tiktok", post_type="video",
                 media_urls=["https://www.tiktok.com/@brand/video/12345"],
                 raw={"videoMeta": {"coverUrl": "https://p16.tiktokcdn.com/cover.jpeg"}})
    seen, stored = _drive(monkeypatch, post)
    assert seen["claude_urls"] == ["https://p16.tiktokcdn.com/cover.jpeg"]
    assert stored["claude"][1] == "ok"


def test_a_linkedin_video_falls_back_to_its_attachment_poster(monkeypatch):
    post = _post(platform="linkedin", post_type="video",
                 media_urls=["https://dms.licdn.com/playlist/vid/x.mp4"],
                 raw={"attachments": [
                     {"type": "video", "url": "https://dms.licdn.com/playlist/vid/x.mp4",
                      "preview_url": "https://media.licdn.com/dms/image/poster.jpg"}]})
    seen, stored = _drive(monkeypatch, post)
    assert seen["claude_urls"] == ["https://media.licdn.com/dms/image/poster.jpg"]
    assert stored["claude"][1] == "ok"


def test_a_facebook_video_falls_back_to_its_thumbnail(monkeypatch):
    post = _post(platform="facebook", post_type="video",
                 media_urls=["https://video.xx.fbcdn.net/v/clip.mp4"],
                 raw={"thumbnail": "https://scontent.xx.fbcdn.net/v/thumb.jpg"})
    seen, stored = _drive(monkeypatch, post)
    assert seen["claude_urls"] == ["https://scontent.xx.fbcdn.net/v/thumb.jpg"]


def test_an_x_video_falls_back_to_its_poster_frame(monkeypatch):
    post = _post(platform="x", post_type="video",
                 media_urls=["https://video.twimg.com/ext_tw_video/1/vid/720x1280/a.mp4"],
                 raw={"media": [{"type": "video",
                                 "media_url_https": "https://pbs.twimg.com/ext_tw_video_thumb/1/img/a.jpg"}]})
    seen, stored = _drive(monkeypatch, post)
    assert seen["claude_urls"] == ["https://pbs.twimg.com/ext_tw_video_thumb/1/img/a.jpg"]


def test_youtubes_own_fallback_still_works_and_still_prefers_the_largest(monkeypatch):
    post = _post(platform="youtube", post_type="video",
                 media_urls=["https://www.youtube.com/watch?v=v1"],
                 raw={"snippet": {"thumbnails": {
                     "default": {"url": "https://i.ytimg.com/vi/v1/default.jpg"},
                     "maxres": {"url": "https://i.ytimg.com/vi/v1/maxresdefault.jpg"}}}})
    seen, _ = _drive(monkeypatch, post)
    assert seen["claude_urls"] == ["https://i.ytimg.com/vi/v1/maxresdefault.jpg"]


def test_a_video_with_no_frames_and_no_cover_anywhere_is_still_marked_failed(monkeypatch):
    post = _post(platform="instagram", post_type="reel",
                 media_urls=["https://scontent.cdninstagram.com/v/clip.mp4"], raw={})
    seen, stored = _drive(monkeypatch, post)
    assert seen["claude_urls"] == []
    assert stored["claude"][1] == "failed"
    assert stored["claude"][2] == "Could not extract any video frames."


def test_a_poster_lookup_never_hands_back_a_video(monkeypatch):
    """The entire point is to produce a still a vision model can read."""
    for raw in ({"displayUrl": "https://cdn/x.mp4"},
                {"videoMeta": {"coverUrl": "https://cdn/c.webm"}},
                {"thumbnail": "https://cdn/t.mov"},
                {"media": [{"media_url_https": "https://cdn/m.m3u8"}]}):
        assert sci_pipeline._poster_image_url({"raw": raw}) is None


# ── 2. What gets analyzed when the post is not read frame by frame ─────────

def test_a_carousel_whose_leading_media_is_a_video_is_read_as_a_video(monkeypatch):
    """Instagram types a post "carousel" the moment it has childPosts, even
    when the parent item carries a videoUrl. That post used to have its
    .mp4 URL handed to two vision models."""
    post = _post(platform="instagram", post_type="carousel",
                 media_urls=["https://scontent.cdninstagram.com/v/clip.mp4"],
                 raw={"videoUrl": "https://scontent.cdninstagram.com/v/clip.mp4",
                      "childPosts": [{"videoUrl": "https://scontent.cdninstagram.com/v/clip.mp4"}]})
    seen, stored = _drive(monkeypatch, post, frames=[b"f1", b"f2"])
    assert seen["claude_urls"] == []      # never sent an mp4 as a still
    assert seen["claude_frames"] == 2
    assert seen["extract_urls"] == ["https://scontent.cdninstagram.com/v/clip.mp4"]
    assert stored["claude"][1] == "ok"


def test_a_carousel_leading_with_a_video_still_prefers_a_real_image_slide(monkeypatch):
    """When the post does carry an image, that image is the still to read:
    no need to decode a video for a post that has a photo in it."""
    post = _post(platform="x", post_type="carousel",
                 media_urls=["https://video.twimg.com/a.mp4",
                             "https://pbs.twimg.com/media/b.jpg"])
    seen, stored = _drive(monkeypatch, post)
    assert seen["claude_urls"] == ["https://pbs.twimg.com/media/b.jpg"]
    assert seen["extract_urls"] == []
    assert stored["claude"][1] == "ok"


def test_an_ordinary_image_post_is_unchanged(monkeypatch):
    post = _post(platform="instagram", post_type="image",
                 media_urls=["https://scontent.cdninstagram.com/v/photo.jpg?oe=9"])
    seen, _ = _drive(monkeypatch, post)
    assert seen["claude_urls"] == ["https://scontent.cdninstagram.com/v/photo.jpg?oe=9"]
    assert seen["extract_urls"] == []


def test_an_extensionless_cdn_url_is_still_analyzed(monkeypatch):
    """Plenty of CDN image URLs carry no extension at all. Refusing those
    would trade one silent gap for another."""
    post = _post(platform="linkedin", post_type="image",
                 media_urls=["https://media.licdn.com/dms/image/D4E22AQ/feedshare"])
    seen, stored = _drive(monkeypatch, post)
    assert seen["claude_urls"] == ["https://media.licdn.com/dms/image/D4E22AQ/feedshare"]
    assert stored["claude"][1] == "ok"


def test_a_post_with_no_media_at_all_is_skipped(monkeypatch):
    post = _post(platform="x", post_type="text", media_urls=[])
    seen, stored = _drive(monkeypatch, post)
    assert seen["claude_urls"] == [] and seen["extract_urls"] == []
    assert stored["claude"][1] == "skipped"


# ── 3. Frame extraction and transcription read the video, not whatever leads ─

def test_frame_extraction_is_fed_the_video_even_when_an_image_leads(monkeypatch):
    post = _post(platform="linkedin", post_type="video",
                 media_urls=["https://media.licdn.com/dms/image/poster.jpg",
                             "https://dms.licdn.com/playlist/vid/clip.mp4"])
    seen, _ = _drive(monkeypatch, post, frames=[b"f1"])
    assert seen["extract_urls"] == ["https://dms.licdn.com/playlist/vid/clip.mp4"]
    assert seen["transcribed"] == ["https://dms.licdn.com/playlist/vid/clip.mp4"]


# ── 4. Reading a URL's own kind ────────────────────────────────────────────

def test_a_urls_kind_is_read_from_its_path_not_its_query_string():
    assert sci_pipeline._is_image_url("https://cdn/a/b.JPG?w=1080&sig=.mp4")
    assert sci_pipeline._is_video_url("https://cdn/a/b.mp4?token=x.jpg&oe=68")
    assert not sci_pipeline._is_image_url("https://cdn/a/b?format=jpg")
    assert not sci_pipeline._is_video_url("https://cdn/a/b?format=mp4")
    # A dot in a directory name is not an extension.
    assert not sci_pipeline._is_image_url("https://cdn/v1.2/photo")
    assert not sci_pipeline._is_video_url("https://cdn/v1.2/clip")


def test_the_still_chooser_never_returns_a_video():
    for urls in (["https://cdn/a.mp4"], ["https://cdn/a.mp4", "https://cdn/b.webm"]):
        assert sci_pipeline._still_image_url({"raw": {}}, urls) is None


def test_the_video_types_tuple_is_named_once():
    assert sci_pipeline.VIDEO_POST_TYPES == ("video", "reel", "short")


# ── The two resolvers for one question must know the same shapes ───────────
#
# templates/social_media_intelligence.html's postThumbnail() answers the
# same question in JS, to draw each post's card. That one is proven -- the
# cards render -- and this one was written to match it. They cannot share
# code across the language boundary, so this is the join: if the page learns
# a raw field this module does not know, the analysis step goes back to
# reporting "could not extract any frames" on a post whose cover image is
# visibly on screen. Two copies of one policy is this codebase's most
# repeated bug (see the CSV-vs-page divergence on the events agent, and the
# analyzed-count and notification-list duplications before it).

def _page_thumbnail_fields():
    """The raw-payload field names postThumbnail() reads, from the page's
    real text."""
    import re
    html = io.open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "templates", "social_media_intelligence.html"),
                   encoding="utf-8").read()
    start = html.index("function postThumbnail(post)")
    body = html[start:start + html[start:].index("\n    }")]
    names = set()
    # raw.foo / vm.foo / media0.foo / at.foo style reads, plus ['name'] lookups.
    for m in re.finditer(r"\b(?:raw|vm|media0|at|im|photo|thumbs|t|imgs)\.([A-Za-z_][A-Za-z0-9_]*)", body):
        names.add(m.group(1))
    for m in re.finditer(r"\['([A-Za-z_][A-Za-z0-9_]*)'\]", body):
        names.add(m.group(1))
    # JS-only plumbing, not payload fields.
    names -= {"length", "toLowerCase", "url", "uri", "media_url_https", "post_type",
              "media_urls", "platform", "raw"}
    return names


import io  # noqa: E402  (used by _page_thumbnail_fields above)


def test_this_module_knows_every_raw_field_the_page_resolver_does():
    import inspect
    source = inspect.getsource(sci_pipeline._poster_candidates)
    missing = sorted(f for f in _page_thumbnail_fields() if f not in source)
    assert not missing, (
        "templates/social_media_intelligence.html's postThumbnail() reads %s, which "
        "_poster_candidates does not know. A post whose cover image the page can draw "
        "must not be reported as unanalyzable." % missing)


def test_a_reddit_post_falls_back_to_its_thumbnail(monkeypatch):
    post = _post(platform="reddit", post_type="video",
                 media_urls=["https://v.redd.it/abc/DASH_720.mp4"],
                 raw={"thumbnail_url": "https://b.thumbs.redditmedia.com/x.jpg"})
    seen, stored = _drive(monkeypatch, post)
    assert seen["claude_urls"] == ["https://b.thumbs.redditmedia.com/x.jpg"]
    assert stored["claude"][1] == "ok"


def test_a_linkedin_video_uses_the_image_attachment_beside_it(monkeypatch):
    """Unipile puts the displayable image in an attachment of type 'img',
    whose `url` IS the image. A video row's url is the mp4 and must never
    be picked."""
    post = _post(platform="linkedin", post_type="video",
                 media_urls=["https://dms.licdn.com/playlist/vid/clip.mp4"],
                 raw={"attachments": [
                     {"type": "video", "url": "https://dms.licdn.com/playlist/vid/clip.mp4"},
                     {"type": "img", "url": "https://media.licdn.com/dms/image/still.jpg"}]})
    seen, _ = _drive(monkeypatch, post)
    assert seen["claude_urls"] == ["https://media.licdn.com/dms/image/still.jpg"]


def test_an_unavailable_linkedin_attachment_is_never_used(monkeypatch):
    """LinkedIn marks expired media unavailable and leaves the row in place,
    so an unfiltered read hands a vision model a URL that 404s."""
    post = _post(platform="linkedin", post_type="video",
                 media_urls=["https://dms.licdn.com/playlist/vid/clip.mp4"],
                 raw={"attachments": [
                     {"type": "img", "url": "https://media.licdn.com/gone.jpg", "unavailable": True}]})
    seen, stored = _drive(monkeypatch, post)
    assert seen["claude_urls"] == []
    assert stored["claude"][1] == "failed"


def test_a_linkedin_link_post_falls_back_to_the_article_cover(monkeypatch):
    post = _post(platform="linkedin", post_type="video",
                 media_urls=["https://dms.licdn.com/playlist/vid/clip.mp4"],
                 raw={"article": {"picture_url": "https://media.licdn.com/article-cover.jpg"}})
    seen, _ = _drive(monkeypatch, post)
    assert seen["claude_urls"] == ["https://media.licdn.com/article-cover.jpg"]


def test_tiktoks_other_cover_fields_are_read_too(monkeypatch):
    for raw, expected in (
        ({"videoMeta": {"originCover": "https://p16.tiktokcdn.com/origin.jpeg"}},
         "https://p16.tiktokcdn.com/origin.jpeg"),
        ({"videoMeta": {"dynamicCover": "https://p16.tiktokcdn.com/dyn.jpeg"}},
         "https://p16.tiktokcdn.com/dyn.jpeg"),
        ({"covers": {"default": "https://p16.tiktokcdn.com/default.jpeg"}},
         "https://p16.tiktokcdn.com/default.jpeg"),
    ):
        post = _post(platform="tiktok", post_type="video",
                     media_urls=["https://www.tiktok.com/@b/video/1"], raw=raw)
        seen, _ = _drive(monkeypatch, post)
        assert seen["claude_urls"] == [expected], raw
