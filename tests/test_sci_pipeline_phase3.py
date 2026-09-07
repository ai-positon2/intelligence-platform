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
                        lambda run_id, platform, handle, **kw: order.append(("collect", platform)))
    monkeypatch.setattr(sci_pipeline, "run_platform_creative_analysis",
                        lambda run_id, platform: order.append(("analyze", platform)))
    monkeypatch.setattr(sci_pipeline, "run_reddit_pulse",
                        lambda run_id, name, url: order.append(("reddit_pulse",)))
    monkeypatch.setattr(sci_pipeline, "run_synthesis", lambda run_id: order.append(("synthesize",)))
    monkeypatch.setattr(sci_store, "update_run_status",
                        lambda run_id, status, **k: order.append(("status", status)))

    sci_pipeline._sci_run_analysis_job(1, _OWNER, "Acme Inc", None)

    # The Reddit pulse runs outside the per-platform loop and before the run
    # is marked done: it is the one signal that exists for companies with no
    # Reddit account at all, so it must never be gated on a platform row.
    assert order == [
        ("collect", "instagram"),
        ("analyze", "instagram"),
        ("reddit_pulse",),
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

    def failing_collection(run_id, platform, handle, **kw):
        raise RuntimeError("actor blocked")
    monkeypatch.setattr(sci_pipeline, "run_platform_collection", failing_collection)
    monkeypatch.setattr(sci_pipeline, "run_platform_creative_analysis", lambda run_id, platform: None)
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)
    monkeypatch.setattr(sci_pipeline, "run_reddit_pulse",
                        lambda run_id, name, url: order.append("reddit_pulse"))
    monkeypatch.setattr(sci_pipeline, "run_synthesis", lambda run_id: order.append("synthesize"))
    monkeypatch.setattr(sci_store, "update_run_status",
                        lambda run_id, status, **k: order.append(status))

    sci_pipeline._sci_run_analysis_job(1, _OWNER, "Acme Inc", None)
    assert order == ["reddit_pulse", "synthesize", "done"]


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


# ── Claude and ChatGPT vision now run CONCURRENTLY, not sequentially ────────
# (2026-09-07, following a real production run that took 30+ minutes with
# only the first platform's posts populated -- see run_platform_creative_
# analysis's own docstring for the diagnosis).

def test_claude_and_openai_analysis_actually_run_at_the_same_time(monkeypatch):
    """Deadlock-if-sequential proof: each fake vendor function signals it
    has STARTED, then blocks until the OTHER vendor's signal fires. If
    run_platform_creative_analysis still called them one after another (as
    it did before this fix), the second one would never even be entered
    until the first returns -- and the first can't return because it's
    waiting on a signal only the second one sends. Only genuine concurrency
    lets both proceed. A short, wall-clock timeout on this test itself is
    the actual assertion: run_platform_creative_analysis must not hang."""
    import threading
    from tracker import sci_store, sci_pipeline as pl
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id, platform: [_post(1, "image")])
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)

    claude_started = threading.Event()
    openai_started = threading.Event()

    def fake_claude(post_id, post_type, media_urls, context, frames, thumbnail_url):
        claude_started.set()
        assert openai_started.wait(timeout=3), "openai never started -- these ran sequentially"

    def fake_openai(post_id, run_id, platform, post_type, media_urls, context, frames, thumbnail_url):
        openai_started.set()
        assert claude_started.wait(timeout=3), "claude never started -- these ran sequentially"

    monkeypatch.setattr(pl, "_run_claude_creative_analysis", fake_claude)
    monkeypatch.setattr(pl, "_run_openai_creative_analysis", fake_openai)

    pl.run_platform_creative_analysis(1, "instagram")  # must return promptly, not hang/timeout


def test_frame_extraction_runs_exactly_once_shared_by_both_vendors(monkeypatch):
    """The whole point of hoisting frame extraction out of each vendor's own
    function: there is one real video, read once, regardless of how many
    vendors go on to analyze the same frames concurrently."""
    from tracker import sci_store, sci_pipeline as pl
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id, platform: [_post(1, "video")])
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)
    monkeypatch.setattr(sci_store, "update_post_creative_analysis", lambda *a, **k: None)
    monkeypatch.setattr(sci_store, "update_post_creative_analysis_openai", lambda *a, **k: None)

    extract_calls = []
    monkeypatch.setattr("tracker.sci_video.extract_frames",
                        lambda url, n: extract_calls.append(1) or [b"frame1"])
    monkeypatch.setattr("tracker.sci_vision.analyze_image_bytes",
                        lambda frame, context=None: {"subject": "x", "summary": "s"})
    monkeypatch.setattr("tracker.sci_vision.summarize_frames",
                        lambda analyses, context=None: {"frame_count": len(analyses), "summary": "s"})
    monkeypatch.setattr("tracker.sci_audio.transcribe_video", lambda url: None)
    monkeypatch.setattr("tracker.sci_vision_openai.analyze_image_bytes",
                        lambda frame, context=None: {"subject": "y", "summary": "s2"})

    pl.run_platform_creative_analysis(1, "instagram")
    assert extract_calls == [1]


def test_a_hung_or_slow_openai_call_does_not_delay_claudes_own_stored_result(monkeypatch):
    """The concrete motivation for this fix: a slow/stuck vendor call must
    not hold up the other vendor's result from being written."""
    import time
    from tracker import sci_store, sci_pipeline as pl
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id, platform: [_post(1, "image")])
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)

    claude_write_time = {}

    def fake_claude(post_id, post_type, media_urls, context, frames, thumbnail_url):
        sci_store.update_post_creative_analysis(post_id, {"subject": "x"}, status="ok")
        claude_write_time["t"] = time.monotonic()

    def fake_slow_openai(post_id, run_id, platform, post_type, media_urls, context, frames, thumbnail_url):
        time.sleep(0.3)  # stands in for a genuinely slow vendor call
        sci_store.update_post_creative_analysis_openai(post_id, {"subject": "y"}, status="ok")

    written = {}
    monkeypatch.setattr(sci_store, "update_post_creative_analysis",
                        lambda post_id, analysis, status="ok", error=None: written.setdefault("claude", analysis))
    monkeypatch.setattr(sci_store, "update_post_creative_analysis_openai",
                        lambda post_id, analysis, status="ok", error=None: written.setdefault("openai", analysis))
    monkeypatch.setattr(pl, "_run_claude_creative_analysis", fake_claude)
    monkeypatch.setattr(pl, "_run_openai_creative_analysis", fake_slow_openai)

    started = time.monotonic()
    pl.run_platform_creative_analysis(1, "instagram")
    # Claude's own write happened well before the slow OpenAI call finished
    # (and thus well before run_platform_creative_analysis itself returned),
    # proving Claude was never made to wait on it.
    assert claude_write_time["t"] - started < 0.15
    assert written["claude"]["subject"] == "x"
    assert written["openai"]["subject"] == "y"


# ── run_platform_creative_analysis: the ChatGPT-vision second opinion ────────
#
# 2026-09-07, on explicit user request: ChatGPT vision runs as a second,
# independent pass on the SAME already-fetched creative Claude just
# analyzed, storing to its own columns (creative_analysis_openai*) so it can
# never overwrite or be blocked by Claude's own result.

def test_openai_pass_reuses_the_same_frames_claude_already_extracted(monkeypatch):
    """The whole point of running the OpenAI pass right after Claude's,
    inside the same loop iteration, is never re-extracting/re-downloading
    the video a second time."""
    from tracker import sci_store
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id, platform: [_post(1, "video")])
    extract_calls = []
    monkeypatch.setattr("tracker.sci_video.extract_frames",
                        lambda url, n: extract_calls.append(1) or [b"frame1", b"frame2"])
    monkeypatch.setattr("tracker.sci_vision.analyze_image_bytes",
                        lambda frame, context=None: {"subject": "x", "summary": "s"})
    monkeypatch.setattr("tracker.sci_vision.summarize_frames",
                        lambda analyses, context=None: {"frame_count": len(analyses), "summary": "s"})
    monkeypatch.setattr("tracker.sci_audio.transcribe_video", lambda url: None)

    openai_frames_seen = []
    monkeypatch.setattr("tracker.sci_vision_openai.analyze_image_bytes",
                        lambda frame, context=None: openai_frames_seen.append(frame) or
                        {"subject": "y", "summary": "s2"})
    monkeypatch.setattr(sci_store, "update_post_creative_analysis", lambda *a, **k: None)
    written_openai = {}
    monkeypatch.setattr(sci_store, "update_post_creative_analysis_openai",
                        lambda post_id, analysis, status="ok", error=None:
                        written_openai.update(analysis=analysis, status=status))
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)
    monkeypatch.setattr(sci_store, "log_spend", lambda *a, **k: None)

    sci_pipeline.run_platform_creative_analysis(1, "instagram")

    assert extract_calls == [1], "extract_frames was called more than once -- frames were not reused"
    assert openai_frames_seen == [b"frame1", b"frame2"]
    assert written_openai["status"] == "ok"
    # summarize_frames is mocked to ignore its input's content and just
    # count it, so this proves the OpenAI-analyzed frames (not Claude's, and
    # not a stale/empty list) are what actually got folded.
    assert written_openai["analysis"]["frame_count"] == 2


def test_openai_pass_runs_on_an_image_post_too(monkeypatch):
    from tracker import sci_store
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id, platform: [_post(1, "image")])
    monkeypatch.setattr("tracker.sci_vision.analyze_image",
                        lambda url, context=None: {"subject": "claude sees x", "summary": "s"})
    monkeypatch.setattr(sci_store, "update_post_creative_analysis", lambda *a, **k: None)

    openai_urls_seen = []
    monkeypatch.setattr("tracker.sci_vision_openai.analyze_image",
                        lambda url, context=None: openai_urls_seen.append(url) or
                        {"subject": "gpt sees x", "summary": "s2"})
    written_openai = {}
    monkeypatch.setattr(sci_store, "update_post_creative_analysis_openai",
                        lambda post_id, analysis, status="ok", error=None:
                        written_openai.update(analysis=analysis, status=status))
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)
    monkeypatch.setattr(sci_store, "log_spend", lambda *a, **k: None)

    sci_pipeline.run_platform_creative_analysis(1, "instagram")

    assert openai_urls_seen == ["https://cdn/m"]
    assert written_openai["analysis"]["subject"] == "gpt sees x"


def test_a_claude_failure_never_blocks_the_openai_pass(monkeypatch):
    """The two vendors' results are genuinely independent -- Claude erroring
    on a post must not prevent ChatGPT vision from still being tried on it."""
    from tracker import sci_store
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id, platform: [_post(1, "image")])

    def claude_boom(url, context=None):
        raise RuntimeError("claude exploded")
    monkeypatch.setattr("tracker.sci_vision.analyze_image", claude_boom)
    monkeypatch.setattr(sci_store, "update_post_creative_analysis", lambda *a, **k: None)

    called = []
    monkeypatch.setattr("tracker.sci_vision_openai.analyze_image",
                        lambda url, context=None: called.append(1) or {"subject": "gpt still ran", "summary": "s"})
    monkeypatch.setattr(sci_store, "update_post_creative_analysis_openai", lambda *a, **k: None)
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)
    monkeypatch.setattr(sci_store, "log_spend", lambda *a, **k: None)

    sci_pipeline.run_platform_creative_analysis(1, "instagram")
    assert called == [1]


def test_an_openai_failure_never_touches_claudes_already_stored_result(monkeypatch):
    from tracker import sci_store
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id, platform: [_post(1, "image")])
    monkeypatch.setattr("tracker.sci_vision.analyze_image",
                        lambda url, context=None: {"subject": "claude's real result", "summary": "s"})
    claude_written = {}
    monkeypatch.setattr(sci_store, "update_post_creative_analysis",
                        lambda post_id, analysis, status="ok", error=None:
                        claude_written.update(analysis=analysis, status=status))

    def openai_boom(url, context=None):
        raise RuntimeError("gpt exploded")
    monkeypatch.setattr("tracker.sci_vision_openai.analyze_image", openai_boom)
    openai_written = {}
    monkeypatch.setattr(sci_store, "update_post_creative_analysis_openai",
                        lambda post_id, analysis, status="ok", error=None:
                        openai_written.update(analysis=analysis, status=status, error=error))
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)
    monkeypatch.setattr(sci_store, "log_spend", lambda *a, **k: None)

    sci_pipeline.run_platform_creative_analysis(1, "instagram")

    assert claude_written["analysis"]["subject"] == "claude's real result"
    assert claude_written["status"] == "ok"
    assert openai_written["status"] == "failed"
    assert openai_written["analysis"] is None


def test_openai_not_configured_is_skipped_not_failed(monkeypatch):
    """A missing OPENAI_API_KEY is a deployment fact, not a per-post error --
    the status must read 'skipped', the same distinction every other
    missing-key case in this codebase makes."""
    from tracker import sci_store
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id, platform: [_post(1, "image")])
    monkeypatch.setattr("tracker.sci_vision.analyze_image",
                        lambda url, context=None: {"subject": "x", "summary": "s"})
    monkeypatch.setattr(sci_store, "update_post_creative_analysis", lambda *a, **k: None)
    monkeypatch.setattr("tracker.sci_vision_openai.analyze_image",
                        lambda url, context=None: {"error": "not_configured"})
    written_openai = {}
    monkeypatch.setattr(sci_store, "update_post_creative_analysis_openai",
                        lambda post_id, analysis, status="ok", error=None:
                        written_openai.update(status=status, error=error))
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)
    spend_calls = []
    monkeypatch.setattr(sci_store, "log_spend", lambda *a, **k: spend_calls.append(1))

    sci_pipeline.run_platform_creative_analysis(1, "instagram")

    assert written_openai["status"] == "skipped"
    assert spend_calls == [], "spend must never be logged for a call that never actually ran"


def test_a_successful_openai_analysis_logs_spend(monkeypatch):
    from tracker import sci_store
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id, platform: [_post(1, "image")])
    monkeypatch.setattr("tracker.sci_vision.analyze_image",
                        lambda url, context=None: {"subject": "x", "summary": "s"})
    monkeypatch.setattr(sci_store, "update_post_creative_analysis", lambda *a, **k: None)
    monkeypatch.setattr("tracker.sci_vision_openai.analyze_image",
                        lambda url, context=None: {"subject": "y", "summary": "s2"})
    monkeypatch.setattr(sci_store, "update_post_creative_analysis_openai", lambda *a, **k: None)
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)
    spend_calls = []
    monkeypatch.setattr(sci_store, "log_spend",
                        lambda run_id, platform, vendor, operation, **k: spend_calls.append(
                            (run_id, platform, vendor, operation)))

    sci_pipeline.run_platform_creative_analysis(7, "tiktok")
    assert spend_calls == [(7, "tiktok", "openai", "vision_second_opinion")]


def test_openai_pass_also_falls_back_to_the_thumbnail_when_frames_failed(monkeypatch):
    from tracker import sci_store
    post = _yt_post(1, thumbnails={"high": {"url": "https://i.ytimg.com/vi/v1/hqdefault.jpg"}})
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id, platform: [post])
    monkeypatch.setattr("tracker.sci_video.extract_frames", lambda url, n: [])
    monkeypatch.setattr("tracker.sci_vision.analyze_image",
                        lambda url, context=None: {"subject": "claude thumb", "summary": "s"})
    monkeypatch.setattr(sci_store, "update_post_creative_analysis", lambda *a, **k: None)

    openai_urls_seen = []
    monkeypatch.setattr("tracker.sci_vision_openai.analyze_image",
                        lambda url, context=None: openai_urls_seen.append(url) or
                        {"subject": "gpt thumb", "summary": "s2"})
    written_openai = {}
    monkeypatch.setattr(sci_store, "update_post_creative_analysis_openai",
                        lambda post_id, analysis, status="ok", error=None:
                        written_openai.update(analysis=analysis, status=status))
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)
    monkeypatch.setattr(sci_store, "log_spend", lambda *a, **k: None)

    sci_pipeline.run_platform_creative_analysis(1, "youtube")

    assert openai_urls_seen == ["https://i.ytimg.com/vi/v1/hqdefault.jpg"]
    assert written_openai["analysis"]["frame_extraction_note"]


def test_openai_pass_is_also_marked_failed_with_no_frames_and_no_thumbnail(monkeypatch):
    from tracker import sci_store
    post = _yt_post(1, thumbnails={})
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id, platform: [post])
    monkeypatch.setattr("tracker.sci_video.extract_frames", lambda url, n: [])
    monkeypatch.setattr("tracker.sci_vision.analyze_image", lambda *a, **k: None)
    monkeypatch.setattr(sci_store, "update_post_creative_analysis", lambda *a, **k: None)

    called = []
    monkeypatch.setattr("tracker.sci_vision_openai.analyze_image", lambda *a, **k: called.append(1))
    written_openai = {}
    monkeypatch.setattr(sci_store, "update_post_creative_analysis_openai",
                        lambda post_id, analysis, status="ok", error=None:
                        written_openai.update(analysis=analysis, status=status, error=error))
    monkeypatch.setattr(sci_store, "upsert_platform_run", lambda *a, **k: None)

    sci_pipeline.run_platform_creative_analysis(1, "youtube")

    assert called == [], "must not attempt an image analysis with no image to analyze"
    assert written_openai["status"] == "failed"
    assert written_openai["analysis"] is None


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
