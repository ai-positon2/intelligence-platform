"""tracker/sci_vision.fetch_image_bytes and the retry it exists for.

Both vendors accept an image as a URL and fetch it themselves. That is the
cheap path, and it is the one tried first, but it makes reading a creative
depend on a third party being able to reach a link we did not issue -- and
nearly everything this pipeline collects is a signed, expiring, sometimes
geo-fenced CDN URL. When one of those refuses a vendor's fetcher the call
returns a plain API error, the post is stored as "vendor_call_failed", and
because the two vendors hit the same wall for the same reason it presents as
a broken integration rather than as the CDN saying no.

So a failed URL call is retried once, with bytes fetched from the app itself.
"""

import json
import os
import sys

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_vision, sci_vision_openai  # noqa: E402

_REPLY = json.dumps({"subject": "a pair of running shoes", "summary": "A product shot.",
                     "messaging": "Discount urgency", "cta": "Shop now", "tone": "urgent"})


class _FakeRaw:
    def __init__(self, data):
        self._data = data

    def read(self, n, decode_content=True):
        return self._data[:n]


class _FakeResponse:
    def __init__(self, status=200, ctype="image/jpeg", data=b"\xff\xd8\xff\xd9"):
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        self.raw = _FakeRaw(data)
        self.closed = False

    def close(self):
        self.closed = True


def _fake_get(resp=None, exc=None, seen=None):
    def get(url, **kwargs):
        if seen is not None:
            seen.append((url, kwargs))
        if exc:
            raise exc
        return resp
    return get


def _patch_get(monkeypatch, get):
    monkeypatch.setattr("tracker.event_intel_http.public_get", get)


# ── fetch_image_bytes on its own ───────────────────────────────────────────

def test_it_returns_the_bytes_and_the_media_type(monkeypatch):
    _patch_get(monkeypatch, _fake_get(_FakeResponse(data=b"JPEGDATA")))
    data, media_type = sci_vision.fetch_image_bytes("https://scontent.cdninstagram.com/a.jpg")
    assert data == b"JPEGDATA"
    assert media_type == "image/jpeg"


def test_it_normalises_the_media_types_the_vendors_actually_accept(monkeypatch):
    for served, expected in (("image/jpg", "image/jpeg"), ("image/pjpeg", "image/jpeg"),
                             ("IMAGE/PNG", "image/png"), ("image/webp; charset=binary", "image/webp"),
                             ("image/gif", "image/gif")):
        _patch_get(monkeypatch, _fake_get(_FakeResponse(ctype=served, data=b"X")))
        assert sci_vision.fetch_image_bytes("https://cdn/a")[1] == expected


def test_a_type_neither_vendor_reads_is_not_sent_on(monkeypatch):
    """Sending it would come back as the very error we are retrying."""
    for ctype in ("text/html", "image/svg+xml", "image/tiff", "application/json", ""):
        _patch_get(monkeypatch, _fake_get(_FakeResponse(ctype=ctype, data=b"X")))
        assert sci_vision.fetch_image_bytes("https://cdn/a") == (None, "")


def test_a_non_200_is_a_failure(monkeypatch):
    for status in (403, 404, 410, 429, 500):
        _patch_get(monkeypatch, _fake_get(_FakeResponse(status=status)))
        assert sci_vision.fetch_image_bytes("https://cdn/a.jpg") == (None, "")


def test_an_empty_body_is_a_failure(monkeypatch):
    _patch_get(monkeypatch, _fake_get(_FakeResponse(data=b"")))
    assert sci_vision.fetch_image_bytes("https://cdn/a.jpg") == (None, "")


def test_an_oversized_image_is_refused_rather_than_sent(monkeypatch):
    big = b"x" * (sci_vision.MAX_IMAGE_BYTES + 1)
    _patch_get(monkeypatch, _fake_get(_FakeResponse(data=big)))
    assert sci_vision.fetch_image_bytes("https://cdn/a.jpg") == (None, "")


def test_a_transport_failure_never_raises(monkeypatch):
    _patch_get(monkeypatch, _fake_get(exc=RuntimeError("connection reset")))
    assert sci_vision.fetch_image_bytes("https://cdn/a.jpg") == (None, "")


def test_an_empty_url_never_reaches_the_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not fetch an empty URL")
    _patch_get(monkeypatch, boom)
    assert sci_vision.fetch_image_bytes("") == (None, "")


def test_the_response_is_always_closed(monkeypatch):
    resp = _FakeResponse()
    _patch_get(monkeypatch, _fake_get(resp))
    sci_vision.fetch_image_bytes("https://cdn/a.jpg")
    assert resp.closed is True


def test_it_goes_through_the_ssrf_guarded_helper_with_a_real_user_agent(monkeypatch):
    """These URLs come from third-party scrapers, so fetching one here is an
    SSRF surface; public_get is this repo's reviewed answer to that."""
    seen = []
    _patch_get(monkeypatch, _fake_get(_FakeResponse(), seen=seen))
    sci_vision.fetch_image_bytes("https://cdn/a.jpg")
    url, kwargs = seen[0]
    assert url == "https://cdn/a.jpg"
    assert "Position2-Intelligence" in kwargs["headers"]["User-Agent"]
    assert kwargs["timeout"] == sci_vision.IMAGE_FETCH_TIMEOUT


# ── The retry, on both vendors ─────────────────────────────────────────────

class _FailingThenBytes:
    """A client whose URL call raises the way a refused vendor-side fetch
    does, and whose base64 call succeeds."""

    def __init__(self, reply):
        self.reply = reply
        self.url_calls = 0
        self.bytes_calls = 0
        outer = self

        class _Messages:
            def create(self, **kwargs):
                block = kwargs["messages"][0]["content"][0]
                if block["source"]["type"] == "url":
                    outer.url_calls += 1
                    raise RuntimeError("Error code: 400 - could not fetch the supplied image URL")
                outer.bytes_calls += 1
                b = type("B", (), {"type": "text", "text": outer.reply})()
                return type("R", (), {"content": [b], "stop_reason": "end_turn"})()

        self.messages = _Messages()


def test_claude_retries_with_our_own_bytes_when_its_fetch_is_refused(monkeypatch):
    client = _FailingThenBytes(_REPLY)
    monkeypatch.setattr(sci_vision, "_anthropic", lambda: client)
    _patch_get(monkeypatch, _fake_get(_FakeResponse(data=b"JPEGDATA")))
    result = sci_vision.analyze_image("https://scontent.cdninstagram.com/a.jpg?oe=68")
    assert "error" not in result
    assert result["subject"] == "a pair of running shoes"
    assert (client.url_calls, client.bytes_calls) == (1, 1)


def test_claude_gives_up_cleanly_when_the_image_is_unreachable_here_too(monkeypatch):
    client = _FailingThenBytes(_REPLY)
    monkeypatch.setattr(sci_vision, "_anthropic", lambda: client)
    _patch_get(monkeypatch, _fake_get(_FakeResponse(status=403)))
    assert sci_vision.analyze_image("https://cdn/a.jpg") == {"error": "vendor_call_failed"}
    assert client.bytes_calls == 0


def test_the_retry_happens_once_and_does_not_loop(monkeypatch):
    """analyze_image_bytes must never route back into analyze_image."""
    calls = {"n": 0}

    class _AlwaysFails:
        def __init__(self):
            outer = calls

            class _M:
                def create(self, **kwargs):
                    outer["n"] += 1
                    raise RuntimeError("nope")
            self.messages = _M()

    monkeypatch.setattr(sci_vision, "_anthropic", lambda: _AlwaysFails())
    _patch_get(monkeypatch, _fake_get(_FakeResponse(data=b"JPEGDATA")))
    assert sci_vision.analyze_image("https://cdn/a.jpg") == {"error": "vendor_call_failed"}
    assert calls["n"] == 2  # the URL attempt, then exactly one bytes attempt


def test_chatgpt_retries_the_same_way_with_the_same_fetcher(monkeypatch):
    state = {"url": 0, "bytes": 0}

    class _Completions:
        def create(self, **kwargs):
            part = kwargs["messages"][1]["content"][0]
            if part["image_url"]["url"].startswith("data:"):
                state["bytes"] += 1
                choice = type("C", (), {"message": type("M", (), {"content": _REPLY})(),
                                        "finish_reason": "stop"})()
                return type("R", (), {"choices": [choice]})()
            state["url"] += 1
            raise RuntimeError("Error code: 400 - Timeout while downloading the image")

    class _Client:
        def __init__(self):
            self.chat = type("Chat", (), {"completions": _Completions()})()

    monkeypatch.setattr(sci_vision_openai, "_openai", lambda: _Client())
    _patch_get(monkeypatch, _fake_get(_FakeResponse(data=b"JPEGDATA")))
    result = sci_vision_openai.analyze_image("https://pbs.twimg.com/media/a.jpg")
    assert "error" not in result
    assert result["cta"] == "Shop now"
    assert (state["url"], state["bytes"]) == (1, 1)


def test_chatgpt_reuses_claudes_fetcher_rather_than_keeping_its_own():
    assert not hasattr(sci_vision_openai, "fetch_image_bytes")
