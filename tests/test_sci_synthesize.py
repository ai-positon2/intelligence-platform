"""tracker/sci_synthesize.py -- the degrade-to-error-dict contract (mirrors
tracker/sci_vision.py's) plus the post_ids validation that makes "every claim
cites 2-3 real posts" an enforced guarantee, not a prompt-only request:
_parse() must strip any post id the model didn't actually receive, and drop
a claim entirely if nothing it cited survives.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_synthesize  # noqa: E402


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


def _posts():
    return [
        {"id": 101, "platform": "instagram", "post_type": "image", "post_url": "u1",
         "metrics": {"likes": 10}, "creative_analysis": {"subject": "shoes"}},
        {"id": 102, "platform": "instagram", "post_type": "video", "post_url": "u2",
         "metrics": {"views": 500}, "creative_analysis": {"subjects": ["runner"]}},
    ]


_GOOD_REPLY = json.dumps({
    "platforms": {"instagram": {"summary": "Product-forward.", "claims": [
        {"text": "Frequent product close-ups.", "post_ids": [101, 102]},
    ]}},
    "cross_platform": {"summary": "Consistent product focus.", "claims": [
        {"text": "Product-first across platforms.", "post_ids": [101]},
    ]},
})


def test_synthesize_report_returns_not_configured_without_a_key(monkeypatch):
    monkeypatch.setattr(sci_synthesize, "_anthropic", lambda: None)
    result = sci_synthesize.synthesize_report(1, {})
    assert result == {"error": "not_configured"}


def test_synthesize_report_returns_no_posts_error_when_run_has_nothing(monkeypatch):
    from tracker import sci_store
    monkeypatch.setattr(sci_synthesize, "_anthropic", lambda: _FakeClient(response_text=_GOOD_REPLY))
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id: [])
    result = sci_synthesize.synthesize_report(1, {})
    assert result == {"error": "no_posts_to_synthesize"}


def test_synthesize_report_parses_a_good_reply(monkeypatch):
    from tracker import sci_store
    monkeypatch.setattr(sci_synthesize, "_anthropic", lambda: _FakeClient(response_text=_GOOD_REPLY))
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id: _posts())
    monkeypatch.setattr(sci_store, "get_platform_runs", lambda run_id: [])
    result = sci_synthesize.synthesize_report(1, {})
    assert "error" not in result
    claim = result["platforms"]["instagram"]["claims"][0]
    assert claim["post_ids"] == [101, 102]
    assert result["cross_platform"]["claims"][0]["post_ids"] == [101]


def test_synthesize_report_defaults_summary_and_strategy_to_an_empty_list_when_the_model_omits_them(monkeypatch):
    from tracker import sci_store
    monkeypatch.setattr(sci_synthesize, "_anthropic", lambda: _FakeClient(response_text=_GOOD_REPLY))
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id: _posts())
    monkeypatch.setattr(sci_store, "get_platform_runs", lambda run_id: [])
    result = sci_synthesize.synthesize_report(1, {})
    assert result["platforms"]["instagram"]["messaging_and_strategy"] == []
    assert result["cross_platform"]["messaging_and_strategy"] == []


def test_synthesize_report_parses_summary_and_strategy_as_bullet_lists(monkeypatch):
    from tracker import sci_store
    reply = json.dumps({
        "platforms": {"instagram": {
            "summary": ["Mostly studio product shots.", "A handful of UGC-style clips."],
            "messaging_and_strategy": ["Leans on urgency and discounts.", "CTAs always point to the bio link."],
            "claims": [{"text": "claim", "post_ids": [101]}]}},
        "cross_platform": {"summary": [], "messaging_and_strategy": ["Consistent urgency-led messaging."], "claims": []},
    })
    monkeypatch.setattr(sci_synthesize, "_anthropic", lambda: _FakeClient(response_text=reply))
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id: _posts())
    monkeypatch.setattr(sci_store, "get_platform_runs", lambda run_id: [])
    result = sci_synthesize.synthesize_report(1, {})
    assert result["platforms"]["instagram"]["summary"] == ["Mostly studio product shots.", "A handful of UGC-style clips."]
    assert result["platforms"]["instagram"]["messaging_and_strategy"] == [
        "Leans on urgency and discounts.", "CTAs always point to the bio link."]
    assert result["cross_platform"]["messaging_and_strategy"] == ["Consistent urgency-led messaging."]


def test_synthesize_report_tolerates_a_bare_string_for_summary_or_strategy(monkeypatch):
    """Defensive: a model slip (or an older stored run's shape) that returns
    a plain string instead of a list must degrade to a single bullet, never
    crash the parse."""
    from tracker import sci_store
    reply = json.dumps({
        "platforms": {"instagram": {"summary": "One paragraph instead of a list.",
                                    "messaging_and_strategy": "Also a bare string.",
                                    "claims": []}},
        "cross_platform": {"summary": "", "messaging_and_strategy": "", "claims": []},
    })
    monkeypatch.setattr(sci_synthesize, "_anthropic", lambda: _FakeClient(response_text=reply))
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id: _posts())
    monkeypatch.setattr(sci_store, "get_platform_runs", lambda run_id: [])
    result = sci_synthesize.synthesize_report(1, {})
    assert result["platforms"]["instagram"]["summary"] == ["One paragraph instead of a list."]
    assert result["platforms"]["instagram"]["messaging_and_strategy"] == ["Also a bare string."]
    assert result["cross_platform"]["summary"] == []


def test_synthesize_report_strips_post_ids_the_run_never_actually_had(monkeypatch):
    from tracker import sci_store
    reply = json.dumps({
        "platforms": {"instagram": {"summary": "s", "claims": [
            {"text": "claim", "post_ids": [101, 9999]},
        ]}},
        "cross_platform": {"summary": "", "claims": []},
    })
    monkeypatch.setattr(sci_synthesize, "_anthropic", lambda: _FakeClient(response_text=reply))
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id: _posts())
    monkeypatch.setattr(sci_store, "get_platform_runs", lambda run_id: [])
    result = sci_synthesize.synthesize_report(1, {})
    assert result["platforms"]["instagram"]["claims"][0]["post_ids"] == [101]


def test_synthesize_report_drops_a_claim_when_every_cited_id_is_invalid(monkeypatch):
    from tracker import sci_store
    reply = json.dumps({
        "platforms": {"instagram": {"summary": "s", "claims": [
            {"text": "fabricated", "post_ids": [9999]},
        ]}},
        "cross_platform": {"summary": "", "claims": []},
    })
    monkeypatch.setattr(sci_synthesize, "_anthropic", lambda: _FakeClient(response_text=reply))
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id: _posts())
    monkeypatch.setattr(sci_store, "get_platform_runs", lambda run_id: [])
    result = sci_synthesize.synthesize_report(1, {})
    assert result["platforms"]["instagram"]["claims"] == []


def test_synthesize_report_caps_post_ids_at_three(monkeypatch):
    from tracker import sci_store
    posts = _posts() + [{"id": 103, "platform": "instagram", "post_type": "image",
                         "post_url": "u3", "metrics": {}, "creative_analysis": {}}]
    reply = json.dumps({
        "platforms": {"instagram": {"summary": "s", "claims": [
            {"text": "claim", "post_ids": [101, 102, 103, 101]},
        ]}},
        "cross_platform": {"summary": "", "claims": []},
    })
    monkeypatch.setattr(sci_synthesize, "_anthropic", lambda: _FakeClient(response_text=reply))
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id: posts)
    monkeypatch.setattr(sci_store, "get_platform_runs", lambda run_id: [])
    result = sci_synthesize.synthesize_report(1, {})
    assert len(result["platforms"]["instagram"]["claims"][0]["post_ids"]) == 3


def test_synthesize_report_degrades_on_unparsable_reply(monkeypatch):
    from tracker import sci_store
    monkeypatch.setattr(sci_synthesize, "_anthropic", lambda: _FakeClient(response_text="not json"))
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id: _posts())
    monkeypatch.setattr(sci_store, "get_platform_runs", lambda run_id: [])
    result = sci_synthesize.synthesize_report(1, {})
    assert result == {"error": "unparsable_response"}


def test_synthesize_report_degrades_on_a_vendor_exception(monkeypatch):
    from tracker import sci_store
    monkeypatch.setattr(sci_synthesize, "_anthropic", lambda: _FakeClient(exc=Exception("boom")))
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id: _posts())
    monkeypatch.setattr(sci_store, "get_platform_runs", lambda run_id: [])
    result = sci_synthesize.synthesize_report(1, {})
    assert result == {"error": "vendor_call_failed"}


# ── Merging both vendors' vision into the payload the model sees (2026-09-07) ──

def _capture_payload(monkeypatch, posts, reply=None):
    from tracker import sci_store
    captured = {}

    class _CapturingMessages(_FakeMessages):
        def create(self, **kwargs):
            captured["content"] = kwargs["messages"][0]["content"]
            return super().create(**kwargs)

    class _CapturingClient(_FakeClient):
        def __init__(self, response_text):
            self.messages = _CapturingMessages(response_text)

    monkeypatch.setattr(sci_synthesize, "_anthropic", lambda: _CapturingClient(reply or _GOOD_REPLY))
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id: posts)
    monkeypatch.setattr(sci_store, "get_platform_runs", lambda run_id: [])
    sci_synthesize.synthesize_report(1, {})
    return json.loads(captured["content"])


def test_the_payload_carries_both_vendors_readings_for_one_post(monkeypatch):
    posts = [{"id": 101, "platform": "instagram", "post_type": "image", "post_url": "u1",
             "metrics": {"likes": 10}, "creative_analysis": {"subject": "shoes", "tone": "urgent"},
             "creative_analysis_openai": {"subject": "sneakers", "tone": "excited"}}]
    payload = _capture_payload(monkeypatch, posts)
    vision = payload["posts"][0]["vision"]
    assert vision["claude"]["subject"] == "shoes"
    assert vision["chatgpt"]["subject"] == "sneakers"


def test_a_vendor_that_was_never_run_is_null_in_the_payload_not_omitted():
    """The model must be able to tell 'this vendor said nothing' apart from
    'this vendor was never asked' -- both collapse to null, but the KEY is
    always present so the model never has to guess which case it's in."""
    from tracker import sci_synthesize as mod
    digest = mod._post_digest({"id": 1, "platform": "x", "post_type": "image",
                               "post_url": "u", "metrics": {},
                               "creative_analysis": {"subject": "shoes"}})
    assert "chatgpt" in digest["vision"]
    assert digest["vision"]["chatgpt"] is None
    assert digest["vision"]["claude"]["subject"] == "shoes"


def test_a_failed_vendor_reading_is_null_not_the_raw_error_dict(monkeypatch):
    posts = [{"id": 101, "platform": "instagram", "post_type": "image", "post_url": "u1",
             "metrics": {}, "creative_analysis": {"subject": "shoes"},
             "creative_analysis_openai": {"error": "vendor_call_failed"}}]
    payload = _capture_payload(monkeypatch, posts)
    assert payload["posts"][0]["vision"]["chatgpt"] is None


def test_a_post_with_only_an_openai_reading_still_reaches_the_model(monkeypatch):
    """Claude failing on a post must not silently drop it from the payload
    -- ChatGPT's own successful read is still real evidence to cite."""
    posts = [{"id": 101, "platform": "instagram", "post_type": "image", "post_url": "u1",
             "metrics": {}, "creative_analysis": None,
             "creative_analysis_openai": {"subject": "sneakers"}}]
    payload = _capture_payload(monkeypatch, posts)
    vision = payload["posts"][0]["vision"]
    assert vision["claude"] is None
    assert vision["chatgpt"]["subject"] == "sneakers"


def test_the_system_prompt_instructs_treating_agreement_as_stronger_evidence():
    assert "independent" in sci_synthesize._SYSTEM.lower()
    assert "chatgpt" in sci_synthesize._SYSTEM.lower()
    assert "disagree" in sci_synthesize._SYSTEM.lower()


def test_synthesize_report_includes_scrape_failed_and_low_activity_platforms_in_the_payload(monkeypatch):
    """The narrative must be able to flag platforms with little or no
    activity rather than silently omit them -- the payload the model sees
    has to actually carry that status."""
    from tracker import sci_store
    captured = {}

    class _CapturingMessages(_FakeMessages):
        def create(self, **kwargs):
            captured["content"] = kwargs["messages"][0]["content"]
            return super().create(**kwargs)

    class _CapturingClient(_FakeClient):
        def __init__(self, response_text):
            self.messages = _CapturingMessages(response_text)

    monkeypatch.setattr(sci_synthesize, "_anthropic", lambda: _CapturingClient(_GOOD_REPLY))
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id: _posts())
    monkeypatch.setattr(sci_store, "get_platform_runs", lambda run_id: [
        {"platform": "tiktok", "status": "scrape_failed", "status_detail": "actor blocked", "post_count": 0},
        {"platform": "linkedin", "status": "handle_not_found", "status_detail": None, "post_count": 0},
    ])
    sci_synthesize.synthesize_report(1, {})
    payload = json.loads(captured["content"])
    assert payload["platform_status"]["tiktok"]["status"] == "scrape_failed"
    assert payload["platform_status"]["linkedin"]["status"] == "handle_not_found"
