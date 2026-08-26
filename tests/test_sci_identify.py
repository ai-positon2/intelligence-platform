"""tracker/sci_identify.py -- the refuse-to-guess contract is under test
here more than anything else: a 'low'/'none' confidence platform must never
carry a handle through to the caller, since sci_pipeline maps confidence
straight to sci_platform_runs.status without a second check.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_identify  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_cached_web_search_tool_version():
    """_WEB_SEARCH_TOOL is a process-wide cache of whichever dated
    web_search tool version last worked -- reset it around every test so
    the fallback-ordering tests below don't leak into each other or into
    the plain-success tests above them."""
    sci_identify._WEB_SEARCH_TOOL = None
    yield
    sci_identify._WEB_SEARCH_TOOL = None


class _FakeAPIError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeMessages:
    """`fail_types` maps a web_search tool-type string to the exception that
    call should raise, so a test can simulate one dated version being
    rejected while another succeeds. `calls` records the tool type used on
    every attempt, in order, so a test can assert exactly which versions
    were (or weren't) tried."""
    def __init__(self, response_text=None, exc=None, fail_types=None):
        self._text = response_text
        self._exc = exc
        self._fail_types = fail_types or {}
        self.calls = []

    def create(self, **kwargs):
        tool_type = kwargs.get("tools", [{}])[0].get("type")
        self.calls.append(tool_type)
        if tool_type in self._fail_types:
            raise self._fail_types[tool_type]
        if self._exc:
            raise self._exc
        return type("FakeResponse", (), {"content": [_FakeBlock(self._text)]})()


class _FakeClient:
    def __init__(self, response_text=None, exc=None, fail_types=None):
        self.messages = _FakeMessages(response_text, exc, fail_types)


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


# --- web_search tool version fallback -- regression coverage for the bug
# where a single hardcoded (and since-sunset) tool version made every run
# fail identification on all six platforms at once. ---------------------

def test_identify_handles_falls_back_when_the_newest_tool_version_is_rejected(monkeypatch):
    newest = sci_identify._WEB_SEARCH_TOOL_VERSIONS[0]
    next_best = sci_identify._WEB_SEARCH_TOOL_VERSIONS[1]
    client = _FakeClient(response_text=_MIXED_REPLY,
                          fail_types={newest: _FakeAPIError("unsupported tool type", status_code=400)})
    monkeypatch.setattr(sci_identify, "_anthropic", lambda: client)
    result = sci_identify.identify_handles("Acme Inc")
    assert result["instagram"]["handle"] == "acmeinc"
    assert client.messages.calls == [newest, next_best]
    assert sci_identify._WEB_SEARCH_TOOL == next_best


def test_identify_handles_does_not_burn_through_versions_on_a_transient_error(monkeypatch):
    newest = sci_identify._WEB_SEARCH_TOOL_VERSIONS[0]
    client = _FakeClient(response_text=_MIXED_REPLY,
                          fail_types={newest: _FakeAPIError("rate limited", status_code=429)})
    monkeypatch.setattr(sci_identify, "_anthropic", lambda: client)
    result = sci_identify.identify_handles("Acme Inc")
    # A 429 is a bad minute, not proof the tool version itself is gone --
    # retrying it against two more versions right now wouldn't help and
    # just burns quota, so this call should fail after exactly one attempt.
    assert client.messages.calls == [newest]
    assert all(v["confidence"] == "none" for v in result.values())
    assert sci_identify._WEB_SEARCH_TOOL is None


def test_identify_handles_caches_the_working_tool_version(monkeypatch):
    client = _FakeClient(response_text=_MIXED_REPLY)
    monkeypatch.setattr(sci_identify, "_anthropic", lambda: client)
    sci_identify.identify_handles("Acme Inc")
    assert sci_identify._WEB_SEARCH_TOOL == sci_identify._WEB_SEARCH_TOOL_VERSIONS[0]


def test_identify_handles_self_heals_when_a_previously_cached_version_stops_working(monkeypatch):
    """The exact production scenario: version A worked yesterday and got
    cached, then Anthropic sunsets it -- this call must still succeed by
    falling through to the next known version, not fail every run the way
    a single hardcoded version did."""
    stale = sci_identify._WEB_SEARCH_TOOL_VERSIONS[0]
    recovery = sci_identify._WEB_SEARCH_TOOL_VERSIONS[1]
    sci_identify._WEB_SEARCH_TOOL = stale
    client = _FakeClient(response_text=_MIXED_REPLY,
                          fail_types={stale: _FakeAPIError("deprecated tool version", status_code=400)})
    monkeypatch.setattr(sci_identify, "_anthropic", lambda: client)
    result = sci_identify.identify_handles("Acme Inc")
    assert result["instagram"]["handle"] == "acmeinc"
    assert client.messages.calls == [stale, recovery]
    assert sci_identify._WEB_SEARCH_TOOL == recovery


def test_identify_handles_reports_the_real_failure_when_every_version_fails(monkeypatch):
    fail_types = {v: _FakeAPIError("unsupported: %s" % v, status_code=400)
                  for v in sci_identify._WEB_SEARCH_TOOL_VERSIONS}
    client = _FakeClient(fail_types=fail_types)
    monkeypatch.setattr(sci_identify, "_anthropic", lambda: client)
    result = sci_identify.identify_handles("Acme Inc")
    assert client.messages.calls == list(sci_identify._WEB_SEARCH_TOOL_VERSIONS)
    assert all(v["confidence"] == "none" for v in result.values())
    # The old blind "failed unexpectedly" gave no way to tell this apart from
    # any other failure -- the real error now travels with the result.
    assert "unsupported" in result["instagram"]["reasoning"].lower()


# --- probe() -- the admin self-test ------------------------------------

def test_probe_without_a_key_reports_unconfigured(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = sci_identify.probe("Nike")
    assert result["configured"] is False
    assert "error" in result


def test_probe_reports_found_platforms_on_success(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(sci_identify, "_anthropic", lambda: _FakeClient(response_text=_MIXED_REPLY))
    result = sci_identify.probe("Nike")
    assert result["ok"] is True
    assert result["found"]["instagram"] == "acmeinc"
    assert result["web_search_tool"] == sci_identify._WEB_SEARCH_TOOL_VERSIONS[0]


def test_probe_surfaces_reasoning_when_nothing_is_found(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(sci_identify, "_anthropic",
                        lambda: _FakeClient(exc=_FakeAPIError("boom", status_code=500)))
    result = sci_identify.probe("Nike")
    assert result["ok"] is False
    assert "boom" in (result.get("reasoning") or "")
