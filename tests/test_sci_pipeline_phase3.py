"""tracker/sci_pipeline.py -- Phase 3 additions: run_synthesis() wiring
(classify -> synthesize -> written onto sci_runs.synthesis, never fails the
run itself), the end-to-end call order in _sci_run_analysis_job, and
run_platform_creative_analysis's new dialogue_transcript attachment for
video/reel/short posts only.

sci_classify/sci_synthesize/sci_video/sci_vision/sci_audio are all imported
LAZILY inside sci_pipeline's own functions (repo convention -- see
sci_pipeline.py's other lazy `from tracker import ...` lines), so they are
never attributes of the sci_pipeline module itself. Patches below target the
real tracker.* modules by dotted string path, which sci_pipeline's lazy
imports re-resolve from sys.modules on every call.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_pipeline  # noqa: E402

_OWNER = "owner@position2.com"


# ── run_synthesis ────────────────────────────────────────────────────────────

def test_run_synthesis_writes_the_synthesized_result_onto_the_run(monkeypatch):
    from tracker import sci_store
    monkeypatch.setattr("tracker.sci_classify.classify_patterns", lambda run_id: {"_all": {}})
    monkeypatch.setattr("tracker.sci_synthesize.synthesize_report",
                        lambda run_id, classify_result: {"platforms": {}, "cross_platform": {}})
    calls = []
    monkeypatch.setattr(sci_store, "update_run_status",
                        lambda run_id, status, **k: calls.append((status, k)))
    sci_pipeline.run_synthesis(1)
    assert calls[0][0] == "running"
    assert calls[0][1]["synthesis"] == {"platforms": {}, "cross_platform": {}}


def test_run_synthesis_never_raises_when_classify_blows_up(monkeypatch):
    def boom(run_id):
        raise RuntimeError("classify exploded")
    monkeypatch.setattr("tracker.sci_classify.classify_patterns", boom)
    sci_pipeline.run_synthesis(1)  # must not raise


def test_run_synthesis_never_raises_when_synthesize_blows_up(monkeypatch):
    monkeypatch.setattr("tracker.sci_classify.classify_patterns", lambda run_id: {})

    def boom(run_id, classify_result):
        raise RuntimeError("synthesize exploded")
    monkeypatch.setattr("tracker.sci_synthesize.synthesize_report", boom)
    sci_pipeline.run_synthesis(1)  # must not raise


# ── _sci_run_analysis_job call order ─────────────────────────────────────────

def test_the_full_job_runs_synthesis_after_every_platform_and_before_done(monkeypatch):
    from tracker import sci_store
    order = []

    monkeypatch.setattr(sci_pipeline, "run_identify", lambda run_id, name, url: {
        "instagram": {"handle": "acme", "confidence": "high", "profile_url": None, "reasoning": ""},
    })
    monkeypatch.setattr(sci_pipeline, "run_platform_collection",
                        lambda run_id, platform, handle: order.append(("collect", platform)))
    monkeypatch.setattr(sci_pipeline, "run_platform_creative_analysis",
                        lambda run_id, platform: order.append(("analyze", platform)))
    monkeypatch.setattr(sci_pipeline, "run_synthesis", lambda run_id: order.append(("synthesize",)))
    monkeypatch.setattr(sci_store, "update_run_status",
                        lambda run_id, status, **k: order.append(("status", status)))

    sci_pipeline._sci_run_analysis_job(1, _OWNER, "Acme Inc", None)

    assert order == [
        ("collect", "instagram"),
        ("analyze", "instagram"),
        ("synthesize",),
        ("status", "done"),
    ]


def test_the_full_job_still_reaches_synthesis_when_a_platform_errors(monkeypatch):
    """Synthesis should run on whatever partial data exists -- a scrape
    failure on one platform must not skip the report entirely."""
    from tracker import sci_store
    order = []

    monkeypatch.setattr(sci_pipeline, "run_identify", lambda run_id, name, url: {
        "instagram": {"handle": "acme", "confidence": "high", "profile_url": None, "reasoning": ""},
    })

    def failing_collection(run_id, platform, handle):
        raise RuntimeError("actor blocked")
    monkeypatch.setattr(sci_pipeline, "run_platform_collection", failing_collection)
    monkeypatch.setattr(sci_pipeline, "run_platform_creative_analysis", lambda run_id, platform: None)
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)
    monkeypatch.setattr(sci_pipeline, "run_synthesis", lambda run_id: order.append("synthesize"))
    monkeypatch.setattr(sci_store, "update_run_status",
                        lambda run_id, status, **k: order.append(status))

    sci_pipeline._sci_run_analysis_job(1, _OWNER, "Acme Inc", None)
    assert order == ["synthesize", "done"]


# ── run_platform_creative_analysis: dialogue_transcript ──────────────────────

def _post(id_, post_type, media_urls=("https://cdn/m",)):
    return {"id": id_, "post_type": post_type, "caption": "", "media_urls": list(media_urls)}


def test_video_posts_get_a_dialogue_transcript_attached(monkeypatch):
    from tracker import sci_store
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id, platform: [_post(1, "video")])
    monkeypatch.setattr("tracker.sci_video.extract_frames", lambda url, n: [b"frame1"])
    monkeypatch.setattr("tracker.sci_vision.analyze_image_bytes",
                        lambda frame, context=None: {"subject": "x", "summary": "s"})
    monkeypatch.setattr("tracker.sci_vision.summarize_frames",
                        lambda analyses, context=None: {"frame_count": 1, "summary": "s"})
    monkeypatch.setattr("tracker.sci_audio.transcribe_video", lambda url: "spoken words here")

    written = {}
    monkeypatch.setattr(sci_store, "update_post_creative_analysis",
                        lambda post_id, analysis, status="ok", error=None: written.update(analysis=analysis))
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)

    sci_pipeline.run_platform_creative_analysis(1, "instagram")
    assert written["analysis"]["dialogue_transcript"] == "spoken words here"


def test_image_posts_never_call_transcription(monkeypatch):
    from tracker import sci_store
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id, platform: [_post(1, "image")])
    monkeypatch.setattr("tracker.sci_vision.analyze_image",
                        lambda url, context=None: {"subject": "x", "summary": "s"})
    called = []
    monkeypatch.setattr("tracker.sci_audio.transcribe_video", lambda url: called.append(1))
    monkeypatch.setattr(sci_store, "update_post_creative_analysis", lambda *a, **k: None)
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)

    sci_pipeline.run_platform_creative_analysis(1, "instagram")
    assert called == []


def _yt_post(id_, thumbnails=None, media_urls=("https://www.youtube.com/watch?v=v1",)):
    return {"id": id_, "post_type": "video", "caption": "Our launch",
           "media_urls": list(media_urls),
           "raw": {"snippet": {"thumbnails": thumbnails or {}}}}


def test_video_falls_back_to_the_platform_thumbnail_when_frame_extraction_fails(monkeypatch):
    """The bug this round exists to fix: YouTube's yt-dlp/ffmpeg frame
    extraction commonly gets blocked from a datacenter IP, so extract_frames
    returns [] for every video -- creative_analysis must not just go null;
    it should fall back to the real thumbnail the Data API already gave us."""
    from tracker import sci_store
    post = _yt_post(1, thumbnails={"high": {"url": "https://i.ytimg.com/vi/v1/hqdefault.jpg"}})
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id, platform: [post])
    monkeypatch.setattr("tracker.sci_video.extract_frames", lambda url, n: [])

    seen_urls = []
    def fake_analyze_image(url, context=None):
        seen_urls.append(url)
        return {"subject": "a product demo", "messaging": "New launch", "summary": "s"}
    monkeypatch.setattr("tracker.sci_vision.analyze_image", fake_analyze_image)

    called = []
    monkeypatch.setattr("tracker.sci_vision.analyze_image_bytes", lambda *a, **k: called.append(1))
    monkeypatch.setattr("tracker.sci_audio.transcribe_video", lambda url: called.append(1))

    written = {}
    monkeypatch.setattr(sci_store, "update_post_creative_analysis",
                        lambda post_id, analysis, status="ok", error=None:
                        written.update(analysis=analysis, status=status))
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)

    sci_pipeline.run_platform_creative_analysis(1, "youtube")

    assert seen_urls == ["https://i.ytimg.com/vi/v1/hqdefault.jpg"]
    assert called == []  # never fell through to frame-based analysis or transcription
    assert written["status"] == "ok"
    assert written["analysis"]["subject"] == "a product demo"
    assert "frame_extraction_note" in written["analysis"]


def test_video_with_no_frames_and_no_thumbnail_is_marked_failed(monkeypatch):
    from tracker import sci_store
    post = _yt_post(1, thumbnails={})
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id, platform: [post])
    monkeypatch.setattr("tracker.sci_video.extract_frames", lambda url, n: [])
    called = []
    monkeypatch.setattr("tracker.sci_vision.analyze_image", lambda *a, **k: called.append(1))

    written = {}
    monkeypatch.setattr(sci_store, "update_post_creative_analysis",
                        lambda post_id, analysis, status="ok", error=None:
                        written.update(analysis=analysis, status=status, error=error))
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)

    sci_pipeline.run_platform_creative_analysis(1, "youtube")

    assert called == []
    assert written["status"] == "failed"
    assert written["analysis"] is None


def test_a_failed_frame_analysis_never_calls_transcription(monkeypatch):
    """summarize_frames returning an error dict (every frame failed) must
    skip transcription entirely -- there is nothing to attach it to."""
    from tracker import sci_store
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id, platform: [_post(1, "reel")])
    monkeypatch.setattr("tracker.sci_video.extract_frames", lambda url, n: [b"frame1"])
    monkeypatch.setattr("tracker.sci_vision.analyze_image_bytes",
                        lambda frame, context=None: {"error": "vendor_call_failed"})
    monkeypatch.setattr("tracker.sci_vision.summarize_frames",
                        lambda analyses, context=None: {"error": "no_frames_analyzed", "frame_count": 1})
    called = []
    monkeypatch.setattr("tracker.sci_audio.transcribe_video", lambda url: called.append(1))
    monkeypatch.setattr(sci_store, "update_post_creative_analysis", lambda *a, **k: None)
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)

    sci_pipeline.run_platform_creative_analysis(1, "instagram")
    assert called == []
