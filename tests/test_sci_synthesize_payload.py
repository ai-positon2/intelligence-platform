"""tracker/sci_synthesize.py -- what actually reaches the model, and what
happens when the reply runs out of room.

The bug these exist for: the payload was assembled and then sliced with
`json.dumps(payload)[:180000]`. That does not send less evidence, it sends
BROKEN evidence -- a JSON document ending inside a quoted string, which the
model has to guess at. With sci_pipeline.MAX_POSTS_PER_PLATFORM at 25 and two
vision readings stored per post, four active platforms already serialize past
that limit, so on any busy account the synthesis step was reading a corrupted
digest. The report it wrote is the "insights" half of this agent.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_synthesize  # noqa: E402

_FIELD_VALUE = "a reasonably long and descriptive value for this field of the creative"
_VISION = {f: _FIELD_VALUE for f in (
    "subject", "setting", "people", "product", "style", "on_screen_text",
    "messaging", "cta", "tone", "hook", "format_technique", "branding", "summary")}


def _post(pid, readings=2, likes=100, platform="instagram"):
    p = {"id": pid, "platform": platform, "post_type": "image",
         "post_url": "https://www.instagram.com/p/C%08d/" % pid,
         "metrics": {"likes": likes, "comments": 4}}
    p["creative_analysis"] = dict(_VISION) if readings >= 1 else {"error": "vendor_call_failed"}
    p["creative_analysis_openai"] = dict(_VISION) if readings >= 2 else {"error": "vendor_call_failed"}
    return p


class _FakeMessages:
    def __init__(self, reply, stop_reason=None):
        self._reply = reply
        self._stop_reason = stop_reason
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        block = type("B", (), {"type": "text", "text": self._reply})()
        return type("R", (), {"content": [block], "stop_reason": self._stop_reason})()


class _FakeClient:
    def __init__(self, reply, stop_reason=None):
        self.messages = _FakeMessages(reply, stop_reason)


def _reply_for(ids):
    return json.dumps({
        "platforms": {"instagram": {
            "summary": ["Bright studio product photography throughout."],
            "messaging_and_strategy": ["Discount urgency is the recurring pillar."],
            "claims": [{"text": "Product shots dominate", "post_ids": list(ids)[:3]}],
        }},
        "cross_platform": {"summary": ["One consistent look."],
                           "messaging_and_strategy": [], "claims": []},
    })


def _run(posts, monkeypatch, stop_reason=None, reply=None):
    """Drive the real synthesize_report against a fake client, and hand back
    both its result and the exact string the model was sent."""
    from tracker import sci_store
    client = _FakeClient(reply if reply is not None else _reply_for([p["id"] for p in posts]),
                         stop_reason)
    monkeypatch.setattr(sci_synthesize, "_anthropic", lambda: client)
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id: posts)
    monkeypatch.setattr(sci_store, "get_platform_runs", lambda run_id: [
        {"platform": "instagram", "status": "ok", "status_detail": None, "post_count": len(posts)}])
    result = sci_synthesize.synthesize_report(1, {"_all": {}, "instagram": {}})
    sent = client.messages.calls[0]["messages"][0]["content"] if client.messages.calls else None
    return result, sent


# ── The load-bearing property: never send a document that does not parse ────

def test_the_payload_is_always_valid_json_however_many_posts_there_are(monkeypatch):
    for n in (0, 1, 25, 100, 175, 400, 900):
        posts = [_post(i) for i in range(n)]
        if not posts:
            continue
        _, sent = _run(posts, monkeypatch)
        json.loads(sent)  # the assertion: raises if we ever cut mid-structure
        assert len(sent) <= sci_synthesize.PAYLOAD_CHAR_BUDGET


def test_a_run_far_past_the_budget_drops_whole_posts_and_says_how_many(monkeypatch):
    posts = [_post(i) for i in range(900)]
    _, sent = _run(posts, monkeypatch)
    payload = json.loads(sent)
    assert payload["evidence"]["posts_total"] == 900
    assert payload["evidence"]["posts_included"] == len(payload["posts"])
    assert 0 < len(payload["posts"]) < 900
    # Every digest that did make it is complete, not a truncated fragment.
    for d in payload["posts"]:
        assert set(d) == {"id", "platform", "post_type", "post_url", "metrics", "vision"}


def test_a_normal_run_is_sent_whole_with_nothing_omitted(monkeypatch):
    """The budget exists as a backstop, not as routine behaviour: a maximal
    seven-platform run (sci_pipeline.MAX_POSTS_PER_PLATFORM x 7) has to fit."""
    posts = [_post(i) for i in range(175)]
    _, sent = _run(posts, monkeypatch)
    payload = json.loads(sent)
    assert payload["evidence"] == {"posts_total": 175, "posts_included": 175}


# ── Which evidence survives when it cannot all fit ─────────────────────────

def test_posts_with_a_real_vision_reading_beat_posts_without_one(monkeypatch):
    """A post no vendor could read supports no claim about the creative,
    however popular it was, so it must not crowd out one that can."""
    unread = [_post(i, readings=0, likes=10 ** 6) for i in range(400)]
    read = [_post(1000 + i, readings=2, likes=1) for i in range(400)]
    digests, omitted = sci_synthesize._select_digests(unread + read, 90000, 400)
    kept = {d["id"] for d in digests}
    with_reading = [d for d in digests if any(d["vision"].values())]
    assert omitted > 0
    assert len(with_reading) >= 25
    assert any(i >= 1000 for i in kept)


def test_two_readings_of_a_post_beat_one(monkeypatch):
    both = [_post(i, readings=2, likes=1) for i in range(200)]
    one = [_post(1000 + i, readings=1, likes=10 ** 6) for i in range(200)]
    digests, _ = sci_synthesize._select_digests(one + both, 40000, 400)
    two_reading = sum(1 for d in digests if all(d["vision"].values()))
    one_reading = sum(1 for d in digests if len([v for v in d["vision"].values() if v]) == 1)
    assert two_reading > one_reading


def test_the_posts_that_survive_stay_in_the_runs_own_order(monkeypatch):
    posts = [_post(i, readings=2, likes=i) for i in range(400)]
    digests, _ = sci_synthesize._select_digests(posts, 60000, 400)
    ids = [d["id"] for d in digests]
    assert ids == sorted(ids)


def test_the_prompt_tells_the_model_when_it_is_not_seeing_every_post():
    """Otherwise a partial digest gets written up as if it were the whole
    account, counts included."""
    assert "posts_included" in sci_synthesize._SYSTEM
    assert "posts_total" in sci_synthesize._SYSTEM


# ── A cut-off reply loses the whole report, so it gets its own name ────────

def test_a_truncated_reply_is_reported_as_truncated_not_unparsable(monkeypatch):
    posts = [_post(i) for i in range(3)]
    result, _ = _run(posts, monkeypatch, stop_reason="max_tokens")
    assert result == {"error": "response_truncated"}


def test_a_normal_reply_is_parsed(monkeypatch):
    posts = [_post(i) for i in range(3)]
    result, _ = _run(posts, monkeypatch, stop_reason="end_turn")
    assert "error" not in result
    assert result["platforms"]["instagram"]["summary"]


def test_the_reply_gets_more_room_than_the_old_six_thousand(monkeypatch):
    posts = [_post(i) for i in range(3)]
    from tracker import sci_store
    client = _FakeClient(_reply_for([0, 1, 2]))
    monkeypatch.setattr(sci_synthesize, "_anthropic", lambda: client)
    monkeypatch.setattr(sci_store, "get_posts", lambda run_id: posts)
    monkeypatch.setattr(sci_store, "get_platform_runs", lambda run_id: [])
    sci_synthesize.synthesize_report(1, {"_all": {}})
    assert client.messages.calls[0]["max_tokens"] == sci_synthesize.MAX_TOKENS
    assert sci_synthesize.MAX_TOKENS > 6000
