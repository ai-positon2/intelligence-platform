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


class _FakeStreamManager:
    """Stands in for anthropic's MessageStreamManager -- production code
    only ever does `with client.messages.stream(...) as stream:
    stream.get_final_message()`, so that's all this needs to support.

    Takes a LIST of block texts, not one string: with the web_search tool
    active the real API splits the answer into one text block per cited
    span, and a single-block fixture cannot reproduce that. Getting this
    wrong is precisely what let the fragmented-reply bug ship green."""
    def __init__(self, texts, stop_reason="end_turn"):
        self._texts = texts
        self._stop_reason = stop_reason

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def get_final_message(self):
        return type("FakeResponse", (), {
            "content": [_FakeBlock(t) for t in self._texts],
            "stop_reason": self._stop_reason,
        })()


class _FakeMessages:
    """`fail_types` maps a web_search tool-type string to the exception that
    call should raise, so a test can simulate one dated version being
    rejected while another succeeds. `calls` records the tool type used on
    every attempt, in order, so a test can assert exactly which versions
    were (or weren't) tried. Pass `response_blocks` for a multi-text-block
    reply, or `response_text` for the single-block shorthand."""
    def __init__(self, response_text=None, exc=None, fail_types=None,
                 response_blocks=None, stop_reason="end_turn"):
        if response_blocks is None:
            response_blocks = [] if response_text is None else [response_text]
        self._texts = response_blocks
        self._stop_reason = stop_reason
        self._exc = exc
        self._fail_types = fail_types or {}
        self.calls = []

    def stream(self, **kwargs):
        tool_type = kwargs.get("tools", [{}])[0].get("type")
        self.calls.append(tool_type)
        if tool_type in self._fail_types:
            raise self._fail_types[tool_type]
        if self._exc:
            raise self._exc
        return _FakeStreamManager(self._texts, self._stop_reason)


class _FakeClient:
    def __init__(self, response_text=None, exc=None, fail_types=None,
                 response_blocks=None, stop_reason="end_turn"):
        self.messages = _FakeMessages(response_text, exc, fail_types,
                                      response_blocks, stop_reason)


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


def test_identify_handles_treats_a_real_anthropic_timeout_as_transient(monkeypatch):
    """The actual production failure: a 6-platform, up-to-15-search call
    genuinely running long enough to hit anthropic.APITimeoutError. This
    must not be misread as "this tool version is unsupported" (which would
    pointlessly retry it against two more versions, each equally likely to
    also time out) and the real timeout detail must reach the caller."""
    import anthropic
    import httpx
    timeout_err = anthropic.APITimeoutError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
    newest = sci_identify._WEB_SEARCH_TOOL_VERSIONS[0]
    client = _FakeClient(fail_types={newest: timeout_err})
    monkeypatch.setattr(sci_identify, "_anthropic", lambda: client)
    result = sci_identify.identify_handles("Acme Inc")
    assert client.messages.calls == [newest]
    assert all(v["confidence"] == "none" for v in result.values())
    assert "timed out" in result["instagram"]["reasoning"].lower()


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


# --- the reply shapes web_search actually produces ----------------------
#
# Every test in this section fails against the pre-fix code, which read only
# text_blocks[-1] and required that one block to be exactly a JSON document.
# In production that surfaced as "The identification step returned an
# unreadable response" on all six platforms at once, on every single run.

def test_identify_handles_reads_a_reply_split_across_text_blocks(monkeypatch):
    """The regression that mattered: with web_search on, the API returns one
    text block per cited span, so the JSON arrives in pieces and the last
    block on its own is a meaningless tail."""
    head, tail = _MIXED_REPLY[:60], _MIXED_REPLY[60:]
    monkeypatch.setattr(sci_identify, "_anthropic",
                        lambda: _FakeClient(response_blocks=[head, tail]))
    result = sci_identify.identify_handles("Acme Inc")
    assert result["instagram"]["handle"] == "acmeinc"
    assert result["youtube"]["confidence"] == "medium"


def test_identify_handles_reads_json_after_a_preamble_block(monkeypatch):
    """The model narrating its search before answering must not cost us the
    answer -- nor should the narration itself be mistaken for the answer."""
    monkeypatch.setattr(sci_identify, "_anthropic", lambda: _FakeClient(
        response_blocks=["I'll search for each platform now.", _MIXED_REPLY]))
    result = sci_identify.identify_handles("Acme Inc")
    assert result["instagram"]["handle"] == "acmeinc"


def test_identify_handles_reads_json_wrapped_in_prose_and_a_code_fence(monkeypatch):
    monkeypatch.setattr(sci_identify, "_anthropic", lambda: _FakeClient(
        response_text="Here is what I found:\n```json\n" + _MIXED_REPLY +
                      "\n```\nLet me know if you need more detail."))
    result = sci_identify.identify_handles("Acme Inc")
    assert result["instagram"]["handle"] == "acmeinc"


def test_identify_handles_tolerates_braces_inside_reasoning_text(monkeypatch):
    """The JSON extractor is string-aware, so a brace inside a value can't
    unbalance the scan and truncate the object."""
    # Deliberately an UNBALANCED brace: a balanced "{like_this}" would leave
    # a naive depth counter correct by luck, so it proves nothing. A lone '{'
    # inside a string is what actually strands the count above zero and makes
    # a non-string-aware scan return None for a perfectly valid document.
    reply = json.dumps({p: {"handle": None, "profile_url": None, "confidence": "none",
                            "reasoning": "Bio had a stray { in it."}
                        for p in sci_identify.PLATFORMS})
    monkeypatch.setattr(sci_identify, "_anthropic",
                        lambda: _FakeClient(response_text="Result:\n" + reply))
    result = sci_identify.identify_handles("Acme Inc")
    assert all(v["confidence"] == "none" for v in result.values())
    assert "stray {" in result["instagram"]["reasoning"]


def test_identify_handles_names_truncation_instead_of_calling_it_unreadable(monkeypatch):
    """Running out of output budget is a different failure from writing
    something unreadable, and must say so -- an operator who can't tell them
    apart cannot fix either."""
    monkeypatch.setattr(sci_identify, "_anthropic", lambda: _FakeClient(
        response_blocks=["I'll search for each platform now.", '{"instagram": {"han'],
        stop_reason="max_tokens"))
    result = sci_identify.identify_handles("Acme Inc")
    assert all(v["confidence"] == "none" for v in result.values())
    assert "max_tokens" in result["instagram"]["reasoning"]


def test_identify_handles_distinguishes_a_reply_with_no_text_at_all(monkeypatch):
    monkeypatch.setattr(sci_identify, "_anthropic",
                        lambda: _FakeClient(response_blocks=[]))
    result = sci_identify.identify_handles("Acme Inc")
    assert all(v["confidence"] == "none" for v in result.values())
    assert "no text at all" in result["instagram"]["reasoning"]


def test_probe_exposes_the_raw_reply_when_parsing_failed(monkeypatch):
    """Admin-only diagnostic: without the actual body, a parse failure is
    indistinguishable from the model genuinely finding nothing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(sci_identify, "_anthropic",
                        lambda: _FakeClient(response_blocks=["total gibberish"]))
    result = sci_identify.probe("Nike")
    assert result["ok"] is False
    assert result["last_response"]["text_block_count"] == 1
    assert "total gibberish" in result["last_response"]["excerpt"]


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
