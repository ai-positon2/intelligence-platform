"""tracker/sci_video.resolve_playable_url -- deciding what ffmpeg can open.

The bug: this routed only youtube.com/youtu.be through yt-dlp and handed
everything else straight to ffmpeg as "already a direct CDN link". That is
not true of every adapter. tracker/sci_source_tiktok.py falls back to
`webVideoUrl` -- a TikTok watch page -- whenever the scrape returns no
downloadable address, which is the normal case since the actor is called
with shouldDownloadVideos false. ffmpeg cannot open a watch page, so every
one of those posts failed frame extraction outright.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_video  # noqa: E402


def test_a_direct_media_url_is_returned_untouched_without_shelling_out(monkeypatch):
    def no_subprocess(*a, **k):
        raise AssertionError("yt-dlp must not be run for a direct media URL")
    monkeypatch.setattr(sci_video.subprocess, "run", no_subprocess)
    for url in ("https://scontent.cdninstagram.com/v/clip.mp4?_nc_ht=x&oe=68",
                "https://video.twimg.com/ext_tw_video/1/vid/720x1280/a.mp4",
                "https://cdn.example/a/b.MOV",
                "https://cdn.example/stream/index.m3u8?token=abc"):
        assert sci_video.resolve_playable_url(url) == url


def test_a_tiktok_watch_page_is_resolved_rather_than_handed_to_ffmpeg(monkeypatch):
    calls = []

    class _R:
        returncode = 0
        stdout = "https://v16.tiktokcdn.com/resolved.mp4\n"
        stderr = ""

    def fake_run(cmd, **k):
        calls.append(cmd)
        return _R()
    monkeypatch.setattr(sci_video.subprocess, "run", fake_run)
    out = sci_video.resolve_playable_url("https://www.tiktok.com/@brand/video/7300000000")
    assert out == "https://v16.tiktokcdn.com/resolved.mp4"
    assert calls and calls[0][0] == "yt-dlp"


def test_a_youtube_watch_page_is_still_resolved(monkeypatch):
    class _R:
        returncode = 0
        stdout = "https://rr3---sn-x.googlevideo.com/videoplayback?x=1\n"
        stderr = ""
    monkeypatch.setattr(sci_video.subprocess, "run", lambda cmd, **k: _R())
    assert sci_video.resolve_playable_url("https://www.youtube.com/watch?v=abc") \
        == "https://rr3---sn-x.googlevideo.com/videoplayback?x=1"


def test_a_failed_resolution_still_degrades_to_none(monkeypatch):
    """The caller then falls back to the platform's own cover image (see
    tests/test_sci_pipeline_creative_target.py), which is why returning
    None here is safe rather than a lost post."""
    class _R:
        returncode = 1
        stdout = ""
        stderr = "ERROR: unsupported URL"
    monkeypatch.setattr(sci_video.subprocess, "run", lambda cmd, **k: _R())
    assert sci_video.resolve_playable_url("https://example.com/watch/1") is None


def test_direct_media_detection_reads_the_path_not_the_query():
    assert sci_video._is_direct_media_url("https://cdn/a.mp4?sig=b.jpg")
    assert not sci_video._is_direct_media_url("https://cdn/watch?v=a.mp4")
    assert not sci_video._is_direct_media_url("https://www.tiktok.com/@b/video/7300000000")
    assert not sci_video._is_direct_media_url("")
    assert not sci_video._is_direct_media_url("https://cdn/v1.2/clip")
