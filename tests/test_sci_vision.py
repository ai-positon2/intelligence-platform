"""tracker/sci_vision.py -- the degrade-to-error-dict contract (never raise;
one bad image must not fail the platform or the run), mirroring
tests/test_lps_enrichment.py's fake-Anthropic-client approach.
"""

import json
import os
import sys

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_vision  # noqa: E402
import app as appmod  # noqa: E402

_CLAUDE_VISION_ROUTE = "/p2/admin/external-usage/sci-vision-claude-check"


def _admin_client(email):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": email, "name": "T"}
    return c


class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeMessages:
    def __init__(self, response_text=None, exc=None, stop_reason=None):
        self._text = response_text
        self._exc = exc
        self._stop_reason = stop_reason
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc:
            raise self._exc
        return type("FakeResponse", (), {"content": [_FakeBlock(self._text)],
                                         "stop_reason": self._stop_reason})()


class _FakeClient:
    def __init__(self, response_text=None, exc=None, stop_reason=None):
        self.messages = _FakeMessages(response_text, exc, stop_reason)


_GOOD_REPLY = json.dumps({
    "subject": "a pair of running shoes", "setting": "studio, white background",
    "people": "", "product": "running shoes", "style": "clean product photography",
    "on_screen_text": "30% OFF", "summary": "A product shot promoting a discount.",
})

_RICH_REPLY = json.dumps({
    "subject": "a pair of running shoes", "setting": "studio, white background",
    "people": "", "product": "running shoes", "style": "clean product photography",
    "on_screen_text": "30% OFF", "messaging": "Discount urgency on the new running line",
    "cta": "Shop now", "tone": "urgent", "hook": "Bold 30% OFF text over the product",
    "format_technique": "studio product shot", "branding": "logo bottom-right",
    "summary": "A product shot promoting a discount.",
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


def test_analyze_image_parses_the_new_messaging_fields(monkeypatch):
    monkeypatch.setattr(sci_vision, "_anthropic", lambda: _FakeClient(response_text=_RICH_REPLY))
    result = sci_vision.analyze_image("https://cdn/x.jpg", context={"caption": "30% off shoes!"})
    assert result["messaging"] == "Discount urgency on the new running line"
    assert result["cta"] == "Shop now"
    assert result["tone"] == "urgent"
    assert result["hook"] == "Bold 30% OFF text over the product"
    assert result["format_technique"] == "studio product shot"
    assert result["branding"] == "logo bottom-right"


def test_analyze_image_defaults_messaging_fields_to_empty_string_when_absent(monkeypatch):
    monkeypatch.setattr(sci_vision, "_anthropic", lambda: _FakeClient(response_text=_GOOD_REPLY))
    result = sci_vision.analyze_image("https://cdn/x.jpg")
    assert result["messaging"] == ""
    assert result["cta"] == ""
    assert result["tone"] == ""


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


def test_summarize_frames_dedupes_messaging_level_fields_across_frames():
    frames = [
        {"subject": "logo intro", "messaging": "New running line launch", "cta": "Shop now",
         "tone": "urgent", "format_technique": "studio product shot", "branding": "logo intro card",
         "hook": "Logo animates in over a bold headline", "summary": "Opens on the logo."},
        {"subject": "product in hand", "messaging": "New running line launch", "cta": "",
         "tone": "urgent", "format_technique": "studio product shot", "branding": "",
         "hook": "", "summary": "Shows the product."},
    ]
    result = sci_vision.summarize_frames(frames)
    assert result["messaging"] == "New running line launch"
    assert result["cta"] == "Shop now"
    assert result["tone"] == "urgent"
    assert result["format_technique"] == "studio product shot"


def test_summarize_frames_takes_hook_only_from_the_opening_frame():
    frames = [
        {"subject": "logo intro", "hook": "Bold opening headline", "summary": "s1"},
        {"subject": "product in hand", "hook": "should be ignored, not the opener", "summary": "s2"},
    ]
    result = sci_vision.summarize_frames(frames)
    assert result["hook"] == "Bold opening headline"


# ── A reply that says nothing is a failure, not a success ───────────────────
#
# The bug: _parse coerces whatever came back into all thirteen FIELDS with
# `str(x or "")`, so `{}` -- or a reply using different key names -- produced
# a complete, well-formed dict of thirteen empty strings and was stored with
# creative_analysis_status='ok'. The post then counted toward "Creative
# described", the detail panel printed "Not noted" thirteen times, and the
# synthesis step received a reading carrying no information that looked
# exactly like one that did.

def test_a_reply_describing_nothing_is_an_error_not_a_blank_success(monkeypatch):
    monkeypatch.setattr(sci_vision, "_anthropic", lambda: _FakeClient(response_text="{}"))
    assert sci_vision.analyze_image("https://cdn/x.jpg") == {"error": "empty_response"}


def test_a_reply_with_none_of_the_asked_for_keys_is_an_error(monkeypatch):
    reply = json.dumps({"description": "a lovely photo", "notes": "very nice"})
    monkeypatch.setattr(sci_vision, "_anthropic", lambda: _FakeClient(response_text=reply))
    assert sci_vision.analyze_image("https://cdn/x.jpg") == {"error": "empty_response"}


def test_a_frame_reply_describing_nothing_is_an_error_too(monkeypatch):
    monkeypatch.setattr(sci_vision, "_anthropic", lambda: _FakeClient(response_text="{}"))
    assert sci_vision.analyze_image_bytes(b"jpeg") == {"error": "empty_response"}


def test_one_populated_field_is_enough_to_count_as_a_real_reading(monkeypatch):
    reply = json.dumps({"summary": "A blank loading placeholder, nothing depicted."})
    monkeypatch.setattr(sci_vision, "_anthropic", lambda: _FakeClient(response_text=reply))
    result = sci_vision.analyze_image("https://cdn/x.jpg")
    assert "error" not in result
    assert result["summary"].startswith("A blank loading placeholder")


def test_usable_reads_every_field_not_just_the_summary():
    assert not sci_vision._usable(None)
    assert not sci_vision._usable({})
    assert not sci_vision._usable({f: "" for f in sci_vision.FIELDS})
    assert not sci_vision._usable({f: "   " for f in sci_vision.FIELDS})
    for field in sci_vision.FIELDS:
        one = {f: "" for f in sci_vision.FIELDS}
        one[field] = "something"
        assert sci_vision._usable(one), field


# ── A cut-off reply is named, not blamed on the model's formatting ──────────

def test_a_truncated_reply_is_reported_as_truncated_not_unparsable(monkeypatch):
    # Valid-looking text, but the model ran out of budget: the real thing
    # would be an unterminated object. Either way the cause is ours, and
    # "unparsable_response" sends the reader looking in the wrong place.
    monkeypatch.setattr(sci_vision, "_anthropic",
                        lambda: _FakeClient(response_text=_RICH_REPLY, stop_reason="max_tokens"))
    assert sci_vision.analyze_image("https://cdn/x.jpg") == {"error": "response_truncated"}


def test_a_truncated_frame_reply_is_reported_as_truncated(monkeypatch):
    monkeypatch.setattr(sci_vision, "_anthropic",
                        lambda: _FakeClient(response_text=_RICH_REPLY, stop_reason="max_tokens"))
    assert sci_vision.analyze_image_bytes(b"jpeg") == {"error": "response_truncated"}


def test_a_normal_stop_reason_is_not_treated_as_truncation(monkeypatch):
    monkeypatch.setattr(sci_vision, "_anthropic",
                        lambda: _FakeClient(response_text=_RICH_REPLY, stop_reason="end_turn"))
    assert "error" not in sci_vision.analyze_image("https://cdn/x.jpg")


def test_both_calls_ask_for_the_full_output_budget(monkeypatch):
    for call in (lambda: sci_vision.analyze_image("https://cdn/x.jpg"),
                 lambda: sci_vision.analyze_image_bytes(b"jpeg")):
        client = _FakeClient(response_text=_RICH_REPLY)
        monkeypatch.setattr(sci_vision, "_anthropic", lambda c=client: c)
        call()
        assert client.messages.calls[0]["max_tokens"] == sci_vision.MAX_TOKENS
    assert sci_vision.MAX_TOKENS >= 1500


# ── probe(): the primary vendor gets the self-test the second opinion had ───

def test_probe_says_so_when_no_key_is_configured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = sci_vision.probe()
    assert out["configured"] is False and out["ok"] is False
    assert "ANTHROPIC_API_KEY" in out["error"]


def test_probe_passes_on_a_real_reading(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(sci_vision, "_anthropic", lambda: _FakeClient(response_text=_RICH_REPLY))
    out = sci_vision.probe()
    assert out["ok"] is True
    assert out["sample_summary"].startswith("A product shot")


def test_probe_fails_when_the_vendor_describes_nothing(monkeypatch):
    """A probe that goes green on a vendor returning nothing would be worse
    than no probe at all."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(sci_vision, "_anthropic", lambda: _FakeClient(response_text="{}"))
    out = sci_vision.probe()
    assert out["ok"] is False
    assert out["error"] == "empty_response"


# ── The self-test route, gated exactly like the ChatGPT one beside it ──────

def test_route_requires_admin():
    r = _admin_client("nobody@position2.com").post(_CLAUDE_VISION_ROUTE)
    assert r.status_code == 403


def test_route_requires_login():
    r = appmod.app.test_client().post(_CLAUDE_VISION_ROUTE)
    assert r.status_code in (302, 401)


def test_route_get_is_not_allowed():
    """POST only, so no crawler or link prefetch can spend a vision call."""
    admin = sorted(appmod.ADMIN_EMAILS)[0]
    assert _admin_client(admin).get(_CLAUDE_VISION_ROUTE).status_code == 405


def test_route_returns_the_probe_json_for_an_admin(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    admin = sorted(appmod.ADMIN_EMAILS)[0]
    body = _admin_client(admin).post(_CLAUDE_VISION_ROUTE).get_json()
    assert body["configured"] is False
    assert body["ok"] is False


def test_every_admin_can_reach_the_new_check(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for admin in sorted(appmod.ADMIN_EMAILS):
        r = _admin_client(admin).post(_CLAUDE_VISION_ROUTE)
        assert r.status_code == 200, admin
