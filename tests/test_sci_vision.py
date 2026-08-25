"""tracker/sci_vision.py -- the degrade-to-error-dict contract (never raise;
one bad image must not fail the platform or the run), mirroring
tests/test_lps_enrichment.py's fake-Anthropic-client approach.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_vision  # noqa: E402


class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeMessages:
    def __init__(self, response_text=None, exc=None):
        self._text = response_text
        self._exc = exc

    def create(self, **kwargs):
        if self._exc:
            raise self._exc
        return type("FakeResponse", (), {"content": [_FakeBlock(self._text)]})()


class _FakeClient:
    def __init__(self, response_text=None, exc=None):
        self.messages = _FakeMessages(response_text, exc)


_GOOD_REPLY = json.dumps({
    "subject": "a pair of running shoes", "setting": "studio, white background",
    "people": "", "product": "running shoes", "style": "clean product photography",
    "on_screen_text": "30% OFF", "summary": "A product shot promoting a discount.",
})


def test_analyze_image_returns_not_configured_without_a_key(monkeypatch):
    monkeypatch.setattr(sci_vision, "_anthropic", lambda: None)
    result = sci_vision.analyze_image("https://cdn/x.jpg")
    assert result == {"error": "not_configured"}


def test_analyze_image_parses_a_good_reply(monkeypatch):
    monkeypatch.setattr(sci_vision, "_anthropic", lambda: _FakeClient(response_text=_GOOD_REPLY))
    result = sci_vision.analyze_image("https://cdn/x.jpg", context={"caption": "shoes!"})
    assert result["subject"] == "a pair of running shoes"
    assert result["on_screen_text"] == "30% OFF"
    assert "error" not in result


def test_analyze_image_handles_a_fenced_json_reply(monkeypatch):
    fenced = "```json\n" + _GOOD_REPLY + "\n```"
    monkeypatch.setattr(sci_vision, "_anthropic", lambda: _FakeClient(response_text=fenced))
    result = sci_vision.analyze_image("https://cdn/x.jpg")
    assert result["subject"] == "a pair of running shoes"


def test_analyze_image_degrades_on_an_unparsable_reply(monkeypatch):
    monkeypatch.setattr(sci_vision, "_anthropic", lambda: _FakeClient(response_text="not json at all"))
    result = sci_vision.analyze_image("https://cdn/x.jpg")
    assert result == {"error": "unparsable_response"}


def test_analyze_image_degrades_on_a_vendor_exception(monkeypatch):
    monkeypatch.setattr(sci_vision, "_anthropic", lambda: _FakeClient(exc=Exception("boom")))
    result = sci_vision.analyze_image("https://cdn/x.jpg")
    assert result == {"error": "vendor_call_failed"}


def test_analyze_image_requires_a_url(monkeypatch):
    monkeypatch.setattr(sci_vision, "_anthropic", lambda: _FakeClient(response_text=_GOOD_REPLY))
    assert sci_vision.analyze_image("")["error"] == "no_image_url"


def test_analyze_image_bytes_requires_bytes(monkeypatch):
    monkeypatch.setattr(sci_vision, "_anthropic", lambda: _FakeClient(response_text=_GOOD_REPLY))
    assert sci_vision.analyze_image_bytes(b"")["error"] == "no_image_bytes"


def test_analyze_image_bytes_parses_a_good_reply(monkeypatch):
    monkeypatch.setattr(sci_vision, "_anthropic", lambda: _FakeClient(response_text=_GOOD_REPLY))
    result = sci_vision.analyze_image_bytes(b"\xff\xd8\xff fake jpeg bytes")
    assert result["subject"] == "a pair of running shoes"


def test_summarize_frames_reports_when_every_frame_failed():
    result = sci_vision.summarize_frames([{"error": "vendor_call_failed"}, {"error": "vendor_call_failed"}])
    assert result["error"] == "no_frames_analyzed"
    assert result["frame_count"] == 2


def test_summarize_frames_folds_successful_frames_only():
    frames = [
        {"subject": "logo intro", "setting": "", "on_screen_text": "", "summary": "Opens on the logo."},
        {"error": "vendor_call_failed"},
        {"subject": "product in hand", "setting": "kitchen", "on_screen_text": "NEW", "summary": "Shows the product."},
    ]
    result = sci_vision.summarize_frames(frames)
    assert result["frame_count"] == 3
    assert result["frames_analyzed"] == 2
    assert result["subjects"] == ["logo intro", "product in hand"]
