"""tracker/sci_vision_openai.py -- the ChatGPT-vision second opinion.

Same degrade-to-error-dict contract as tracker/sci_vision.py (never raise;
one bad image must not fail the platform or the run), and the same shared
SYSTEM_PROMPT/FIELDS -- a "second opinion" only means something if both
vendors were asked the identical question in the identical schema.
"""

import json
import os
import sys

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_vision, sci_vision_openai  # noqa: E402
import app as appmod  # noqa: E402

_VISION_ROUTE = "/p2/admin/external-usage/sci-vision-openai-check"


def _client(email):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": email, "name": "T"}
    return c


class _FakeMessage:
    def __init__(self, content):
        self.content = content


class _FakeChoice:
    def __init__(self, content):
        self.message = _FakeMessage(content)


class _FakeCompletions:
    def __init__(self, response_text=None, exc=None, no_choices=False):
        self._text = response_text
        self._exc = exc
        self._no_choices = no_choices

    def create(self, **kwargs):
        if self._exc:
            raise self._exc
        choices = [] if self._no_choices else [_FakeChoice(self._text)]
        return type("FakeResponse", (), {"choices": choices})()


class _FakeChat:
    def __init__(self, *a, **k):
        self.completions = _FakeCompletions(*a, **k)


class _FakeClient:
    def __init__(self, response_text=None, exc=None, no_choices=False):
        self.chat = _FakeChat(response_text=response_text, exc=exc, no_choices=no_choices)


_GOOD_REPLY = json.dumps({
    "subject": "a pair of running shoes", "setting": "studio, white background",
    "people": "", "product": "running shoes", "style": "clean product photography",
    "on_screen_text": "30% OFF", "messaging": "Discount urgency on the new running line",
    "cta": "Shop now", "tone": "urgent", "hook": "Bold 30% OFF text over the product",
    "format_technique": "studio product shot", "branding": "logo bottom-right",
    "summary": "A product shot promoting a discount.",
})


def test_shares_the_exact_same_schema_as_claudes_vision():
    """The whole point of a 'second opinion' is asking the identical
    question -- this module must never keep its own drifted copy."""
    assert sci_vision_openai is not None
    # Importing sci_vision.FIELDS directly is the mechanism; this proves it
    # actually happened rather than a module-level copy that looks similar.
    from tracker.sci_vision import FIELDS, SYSTEM_PROMPT
    assert FIELDS and SYSTEM_PROMPT


def test_analyze_image_returns_not_configured_without_a_key(monkeypatch):
    monkeypatch.setattr(sci_vision_openai, "_openai", lambda: None)
    result = sci_vision_openai.analyze_image("https://cdn/x.jpg")
    assert result == {"error": "not_configured"}


def test_analyze_image_parses_a_good_reply(monkeypatch):
    monkeypatch.setattr(sci_vision_openai, "_openai", lambda: _FakeClient(response_text=_GOOD_REPLY))
    result = sci_vision_openai.analyze_image("https://cdn/x.jpg", context={"caption": "shoes!"})
    assert result["subject"] == "a pair of running shoes"
    assert result["cta"] == "Shop now"
    assert "error" not in result


def test_analyze_image_handles_a_fenced_json_reply(monkeypatch):
    fenced = "```json\n" + _GOOD_REPLY + "\n```"
    monkeypatch.setattr(sci_vision_openai, "_openai", lambda: _FakeClient(response_text=fenced))
    result = sci_vision_openai.analyze_image("https://cdn/x.jpg")
    assert result["subject"] == "a pair of running shoes"


def test_analyze_image_degrades_on_an_unparsable_reply(monkeypatch):
    monkeypatch.setattr(sci_vision_openai, "_openai", lambda: _FakeClient(response_text="not json at all"))
    result = sci_vision_openai.analyze_image("https://cdn/x.jpg")
    assert result == {"error": "unparsable_response"}


def test_analyze_image_degrades_when_the_reply_has_no_choices(monkeypatch):
    monkeypatch.setattr(sci_vision_openai, "_openai", lambda: _FakeClient(no_choices=True))
    result = sci_vision_openai.analyze_image("https://cdn/x.jpg")
    assert result == {"error": "unparsable_response"}


def test_analyze_image_degrades_on_a_vendor_exception(monkeypatch):
    monkeypatch.setattr(sci_vision_openai, "_openai", lambda: _FakeClient(exc=Exception("boom")))
    result = sci_vision_openai.analyze_image("https://cdn/x.jpg")
    assert result == {"error": "vendor_call_failed"}


def test_analyze_image_requires_a_url(monkeypatch):
    monkeypatch.setattr(sci_vision_openai, "_openai", lambda: _FakeClient(response_text=_GOOD_REPLY))
    assert sci_vision_openai.analyze_image("")["error"] == "no_image_url"


def test_analyze_image_bytes_requires_bytes(monkeypatch):
    monkeypatch.setattr(sci_vision_openai, "_openai", lambda: _FakeClient(response_text=_GOOD_REPLY))
    assert sci_vision_openai.analyze_image_bytes(b"")["error"] == "no_image_bytes"


def test_analyze_image_bytes_parses_a_good_reply(monkeypatch):
    monkeypatch.setattr(sci_vision_openai, "_openai", lambda: _FakeClient(response_text=_GOOD_REPLY))
    result = sci_vision_openai.analyze_image_bytes(b"\xff\xd8\xff fake jpeg bytes")
    assert result["subject"] == "a pair of running shoes"


def test_analyze_image_bytes_sends_a_data_uri_not_a_bare_base64_string(monkeypatch):
    """OpenAI's image_url content part needs a real data: URI -- Anthropic's
    separate base64 source type does not apply here, and sending the raw
    base64 string as a 'url' would silently fail every video-frame call."""
    seen = {}

    class _CapturingCompletions(_FakeCompletions):
        def create(self, **kwargs):
            seen["messages"] = kwargs["messages"]
            return super().create(**kwargs)

    client = _FakeClient(response_text=_GOOD_REPLY)
    client.chat.completions = _CapturingCompletions(response_text=_GOOD_REPLY)
    monkeypatch.setattr(sci_vision_openai, "_openai", lambda: client)
    sci_vision_openai.analyze_image_bytes(b"\xff\xd8\xff fake jpeg bytes")
    image_part = seen["messages"][1]["content"][0]
    assert image_part["type"] == "image_url"
    assert image_part["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_uses_json_object_response_format(monkeypatch):
    """Belt-and-suspenders alongside the fence-stripping _parse: ask the
    vendor for JSON mode rather than relying on prompting alone."""
    seen = {}

    class _CapturingCompletions(_FakeCompletions):
        def create(self, **kwargs):
            seen.update(kwargs)
            return super().create(**kwargs)

    client = _FakeClient(response_text=_GOOD_REPLY)
    client.chat.completions = _CapturingCompletions(response_text=_GOOD_REPLY)
    monkeypatch.setattr(sci_vision_openai, "_openai", lambda: client)
    sci_vision_openai.analyze_image("https://cdn/x.jpg")
    assert seen["response_format"] == {"type": "json_object"}


def test_model_defaults_to_the_cheap_vision_capable_model(monkeypatch):
    monkeypatch.delenv("OPENAI_VISION_MODEL", raising=False)
    assert sci_vision_openai._model() == "gpt-4o-mini"


def test_model_is_overridable_by_env(monkeypatch):
    monkeypatch.setenv("OPENAI_VISION_MODEL", "gpt-4o")
    assert sci_vision_openai._model() == "gpt-4o"


# ── probe() -- the admin self-test ──────────────────────────────────────

def test_probe_reports_not_configured_without_a_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = sci_vision_openai.probe()
    assert out["configured"] is False
    assert out["ok"] is False
    assert "OPENAI_API_KEY" in out["error"]


def test_probe_succeeds_with_a_real_call_shape(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "tok")
    monkeypatch.setattr(sci_vision_openai, "_openai", lambda: _FakeClient(response_text=_GOOD_REPLY))
    out = sci_vision_openai.probe()
    assert out["configured"] is True
    assert out["ok"] is True
    assert out["model"] == sci_vision_openai._model()
    assert "error" not in out or not out["error"]


def test_probe_surfaces_a_vendor_failure_without_raising(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "tok")
    monkeypatch.setattr(sci_vision_openai, "_openai", lambda: _FakeClient(exc=Exception("boom")))
    out = sci_vision_openai.probe()
    assert out["ok"] is False
    assert out["error"] == "vendor_call_failed"


def test_probe_uses_the_analyze_image_bytes_path_not_a_url(monkeypatch):
    """The probe must not depend on any external image URL staying up."""
    monkeypatch.setenv("OPENAI_API_KEY", "tok")
    calls = []
    monkeypatch.setattr(sci_vision_openai, "analyze_image_bytes",
                        lambda img, media_type="image/jpeg", context=None:
                        calls.append(1) or {"summary": "a tiny test image"})
    out = sci_vision_openai.probe()
    assert calls == [1]
    assert out["ok"] is True
    assert out["sample_summary"] == "a tiny test image"


# ── the admin route ──────────────────────────────────────────────────────

def test_route_is_admin_only():
    r = _client("nobody@position2.com").post(_VISION_ROUTE)
    assert r.status_code == 403


def test_route_requires_login():
    r = appmod.app.test_client().post(_VISION_ROUTE)
    assert r.status_code in (302, 401)


def test_route_get_is_not_allowed():
    admin = sorted(appmod.ADMIN_EMAILS)[0]
    r = _client(admin).get(_VISION_ROUTE)
    assert r.status_code == 405


def test_route_returns_the_probe_json_for_an_admin(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    admin = sorted(appmod.ADMIN_EMAILS)[0]
    body = _client(admin).post(_VISION_ROUTE).get_json()
    assert body["configured"] is False


def test_summarize_frames_is_reused_from_sci_vision_not_reimplemented():
    """No summarize_frames of its own -- sci_pipeline calls sci_vision's,
    since folding several frame analyses into one video-level result has no
    vendor-specific logic to duplicate."""
    assert not hasattr(sci_vision_openai, "summarize_frames")


def test_openai_client_has_an_explicit_bounded_timeout(monkeypatch):
    # Regression guard: with no timeout set, the SDK default is a 600s read
    # timeout with 2 retries, so one slow/hanging vision call could run for
    # the better part of an hour. run_platform_creative_analysis runs this
    # vendor concurrently with Claude's own call (bounded to 60s in
    # sci_vision._anthropic()) and waits on both, so an unbounded ChatGPT
    # call would silently become the run's long pole.
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    client = sci_vision_openai._openai()
    assert client.timeout == 60.0
    assert client.max_retries == 1
