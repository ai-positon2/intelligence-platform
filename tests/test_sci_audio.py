"""tracker/sci_audio.py -- extract_audio/transcribe/transcribe_video's
degrade-to-None contract. A failure anywhere in the chain (no playable URL,
ffmpeg failure, no OPENAI_API_KEY, a Whisper exception) must collapse to
None, never raise -- one video's missing transcript must never fail that
post's creative_analysis as a whole.
"""

import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_audio  # noqa: E402


def test_extract_audio_returns_none_when_the_url_does_not_resolve(monkeypatch):
    monkeypatch.setattr("tracker.sci_video.resolve_playable_url", lambda url: None)
    assert sci_audio.extract_audio("https://youtube.com/watch?v=x") is None


def test_extract_audio_returns_none_when_ffmpeg_fails(monkeypatch):
    monkeypatch.setattr("tracker.sci_video.resolve_playable_url", lambda url: url)
    fake_result = MagicMock(returncode=1)
    with patch("tracker.sci_audio.subprocess.run", return_value=fake_result):
        assert sci_audio.extract_audio("https://cdn/v.mp4") is None


def test_extract_audio_returns_bytes_on_success(monkeypatch):
    monkeypatch.setattr("tracker.sci_video.resolve_playable_url", lambda url: url)
    fake_result = MagicMock(returncode=0)

    def fake_run(cmd, **kwargs):
        # The real ffmpeg call writes to the tempfile path passed as the
        # last cmd arg -- simulate that so the read-back finds real bytes.
        with open(cmd[-1], "wb") as f:
            f.write(b"fake mp3 bytes")
        return fake_result

    with patch("tracker.sci_audio.subprocess.run", side_effect=fake_run):
        data = sci_audio.extract_audio("https://cdn/v.mp4")
    assert data == b"fake mp3 bytes"


def test_transcribe_returns_none_without_audio_bytes():
    assert sci_audio.transcribe(b"") is None


def test_transcribe_returns_none_without_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert sci_audio.transcribe(b"some bytes") is None


def test_transcribe_returns_the_text_on_success(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_client = MagicMock()
    fake_client.audio.transcriptions.create.return_value = type(
        "FakeTranscript", (), {"text": "Hello world"})()
    monkeypatch.setattr(sci_audio, "_openai", lambda: fake_client)
    assert sci_audio.transcribe(b"some bytes") == "Hello world"


def test_transcribe_degrades_to_none_on_a_vendor_exception(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    fake_client = MagicMock()
    fake_client.audio.transcriptions.create.side_effect = Exception("boom")
    monkeypatch.setattr(sci_audio, "_openai", lambda: fake_client)
    assert sci_audio.transcribe(b"some bytes") is None


def test_transcribe_video_returns_none_when_extraction_fails(monkeypatch):
    monkeypatch.setattr(sci_audio, "extract_audio", lambda url: None)
    called = []
    monkeypatch.setattr(sci_audio, "transcribe", lambda audio: called.append(1) or "should not run")
    assert sci_audio.transcribe_video("https://cdn/v.mp4") is None
    assert called == []


def test_transcribe_video_chains_extract_and_transcribe(monkeypatch):
    monkeypatch.setattr(sci_audio, "extract_audio", lambda url: b"audio bytes")
    monkeypatch.setattr(sci_audio, "transcribe", lambda audio: "transcribed text")
    assert sci_audio.transcribe_video("https://cdn/v.mp4") == "transcribed text"
