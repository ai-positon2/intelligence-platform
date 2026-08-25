"""tracker/sci_identify.py -- the refuse-to-guess contract is under test
here more than anything else: a 'low'/'none' confidence platform must never
carry a handle through to the caller, since sci_pipeline maps confidence
straight to sci_platform_runs.status without a second check.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_identify  # noqa: E402


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


_MIXED_REPLY = json.dumps({
    "instagram": {"handle": "acmeinc", "profile_url": "https://instagram.com/acmeinc",
                 "confidence": "high", "reasoning": "Bio links to acme.com."},
    "linkedin": {"handle": None, "profile_url": None, "confidence": "none",
                "reasoning": "No verifiable company page found."},
    "x": {"handle": "acme_hq", "profile_url": "https://x.com/acme_hq",
         "confidence": "low", "reasoning": "Name matches but bio doesn't confirm ownership."},
    "tiktok": {"handle": None, "profile_url": None, "confidence": "none", "reasoning": "Not found."},
    "youtube": {"handle": "AcmeIncOfficial", "profile_url": "https://youtube.com/@AcmeIncOfficial",
               "confidence": "medium", "reasoning": "Channel matches, no bio link to verify."},
    "facebook": {"handle": None, "profile_url": None, "confidence": "none", "reasoning": "Not found."},
})


def test_identify_handles_returns_all_none_without_a_key(monkeypatch):
    monkeypatch.setattr(sci_identify, "_anthropic", lambda: None)
    result = sci_identify.identify_handles("Acme Inc")
    assert set(result.keys()) == set(sci_identify.PLATFORMS)
    assert all(v["confidence"] == "none" and v["handle"] is None for v in result.values())


def test_identify_handles_requires_a_company_name(monkeypatch):
    monkeypatch.setattr(sci_identify, "_anthropic", lambda: _FakeClient(response_text=_MIXED_REPLY))
    result = sci_identify.identify_handles("")
    assert all(v["confidence"] == "none" for v in result.values())


def test_identify_handles_keeps_high_and_medium_confidence_handles(monkeypatch):
    monkeypatch.setattr(sci_identify, "_anthropic", lambda: _FakeClient(response_text=_MIXED_REPLY))
    result = sci_identify.identify_handles("Acme Inc")
    assert result["instagram"]["handle"] == "acmeinc"
    assert result["instagram"]["confidence"] == "high"
    assert result["youtube"]["handle"] == "AcmeIncOfficial"
    assert result["youtube"]["confidence"] == "medium"


def test_identify_handles_never_returns_a_handle_for_low_or_none_confidence(monkeypatch):
    monkeypatch.setattr(sci_identify, "_anthropic", lambda: _FakeClient(response_text=_MIXED_REPLY))
    result = sci_identify.identify_handles("Acme Inc")
    # x came back "low" confidence WITH a handle in the model's reply -- this
    # module must still null it out rather than trusting the model's own
    # confidence label to have already enforced that.
    assert result["x"]["confidence"] == "low"
    assert result["x"]["handle"] is None
    assert result["linkedin"]["handle"] is None
    assert result["tiktok"]["handle"] is None
    assert result["facebook"]["handle"] is None


def test_identify_handles_degrades_on_an_unparsable_reply(monkeypatch):
    monkeypatch.setattr(sci_identify, "_anthropic", lambda: _FakeClient(response_text="not json"))
    result = sci_identify.identify_handles("Acme Inc")
    assert all(v["confidence"] == "none" for v in result.values())


def test_identify_handles_degrades_on_a_vendor_exception(monkeypatch):
    monkeypatch.setattr(sci_identify, "_anthropic", lambda: _FakeClient(exc=Exception("boom")))
    result = sci_identify.identify_handles("Acme Inc")
    assert all(v["confidence"] == "none" for v in result.values())


def test_identify_handles_covers_exactly_the_six_platforms(monkeypatch):
    monkeypatch.setattr(sci_identify, "_anthropic", lambda: _FakeClient(response_text=_MIXED_REPLY))
    result = sci_identify.identify_handles("Acme Inc")
    assert set(result.keys()) == {"instagram", "linkedin", "x", "tiktok", "youtube", "facebook"}
