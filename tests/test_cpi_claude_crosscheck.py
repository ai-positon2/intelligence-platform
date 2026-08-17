"""A second model checking the first one's homework on Contact Finder's
_CPI_INTENT_SYSTEM parse (Fill-filters and chat share it).

_CPI_INTENT_SYSTEM already spells out, in prose, the exact three mistakes a
live user hit in one afternoon (see test_cpi_fill_filters_audit.py): a
numeric bucket that did not satisfy the stated cutoff, a seniority word
echoed into keywords, and a location silently dropped. That prompt fix closes
each case it names, but a single model call can still mis-follow instructions
on a case the prompt does not already describe -- there is no way to write a
prompt that pre-empts every future slip. _cpi_verify_intent_with_claude sends
the SAME request and the first model's OWN answer to a second, independently
trained model and asks it to catch exactly this family of mistake, with the
one architectural rule that makes it safe to bolt on: anything going wrong
with the second opinion (no key, a timeout, a bad reply) has to fall back to
the first model's original, unverified answer, never to a broken response of
its own. Every test below either proves that fallback or proves the
correction actually reaches the user when the second model does its job.
"""

import json
import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402


class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeMessages:
    def __init__(self, response_text=None, exc=None, capture=None):
        self._text = response_text
        self._exc = exc
        self._capture = capture

    def create(self, **kwargs):
        if self._capture is not None:
            self._capture.append(kwargs)
        if self._exc:
            raise self._exc
        return type("FakeResponse", (), {"content": [_FakeBlock(self._text)]})()


class _FakeAnthropicClient:
    def __init__(self, response_text=None, exc=None, capture=None):
        self.messages = _FakeMessages(response_text, exc, capture)


# ── _cpi_anthropic: the client factory degrades like _cpi_oai does ──────────

def test_cpi_anthropic_returns_none_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert appmod._cpi_anthropic() is None


def test_cpi_anthropic_returns_a_real_client_with_a_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-only")
    client = appmod._cpi_anthropic()
    assert client is not None
    assert type(client).__name__ == "Anthropic"


# ── _cpi_verify_intent_with_claude: the fallback contract ───────────────────

def test_noop_when_claude_is_not_configured(monkeypatch):
    monkeypatch.setattr(appmod, "_cpi_anthropic", lambda: None)
    intent = {"intent": "people_list", "employee_min": 5001}
    out = appmod._cpi_verify_intent_with_claude("more than 500 employees", intent)
    assert out is intent, "absent a key this must not even build a payload, let alone touch the dict"


def test_applies_claudes_correction_when_it_disagrees(monkeypatch):
    corrected = {"intent": "people_list", "employee_min": 500,
                 "seniorities": ["c_suite", "vp", "director"]}
    fake = _FakeAnthropicClient(response_text=json.dumps(corrected))
    monkeypatch.setattr(appmod, "_cpi_anthropic", lambda: fake)
    original = {"intent": "people_list", "employee_min": 5001, "keywords": "top executives",
                "seniorities": ["c_suite", "vp", "director"]}
    out = appmod._cpi_verify_intent_with_claude(
        "top executives ... companies with employees more than 500", original)
    assert out == corrected
    assert out["employee_min"] == 500
    assert "keywords" not in out


def test_keeps_the_original_on_a_network_or_api_failure(monkeypatch):
    fake = _FakeAnthropicClient(exc=RuntimeError("connection reset"))
    monkeypatch.setattr(appmod, "_cpi_anthropic", lambda: fake)
    original = {"intent": "people_list", "employee_min": 5001}
    out = appmod._cpi_verify_intent_with_claude("text", original)
    assert out == original


def test_keeps_the_original_on_a_non_json_reply(monkeypatch):
    fake = _FakeAnthropicClient(response_text="sure, that all looks correct to me")
    monkeypatch.setattr(appmod, "_cpi_anthropic", lambda: fake)
    original = {"intent": "people_list"}
    out = appmod._cpi_verify_intent_with_claude("text", original)
    assert out == original


def test_keeps_the_original_when_the_reply_is_not_a_json_object(monkeypatch):
    fake = _FakeAnthropicClient(response_text=json.dumps(["not", "an", "object"]))
    monkeypatch.setattr(appmod, "_cpi_anthropic", lambda: fake)
    original = {"intent": "people_list"}
    out = appmod._cpi_verify_intent_with_claude("text", original)
    assert out == original


def test_context_is_included_in_the_payload_when_given(monkeypatch):
    captured = []
    fake = _FakeAnthropicClient(response_text=json.dumps({"intent": "unclear"}), capture=captured)
    monkeypatch.setattr(appmod, "_cpi_anthropic", lambda: fake)
    appmod._cpi_verify_intent_with_claude(
        "and their CFO?", {"intent": "person_at_company"},
        context="user: tell me about Acme")
    assert len(captured) == 1
    body = json.loads(captured[0]["messages"][0]["content"])
    assert body["conversation_so_far"] == "user: tell me about Acme"
    assert body["request"] == "and their CFO?"


def test_context_key_is_omitted_when_not_given(monkeypatch):
    captured = []
    fake = _FakeAnthropicClient(response_text=json.dumps({"intent": "unclear"}), capture=captured)
    monkeypatch.setattr(appmod, "_cpi_anthropic", lambda: fake)
    appmod._cpi_verify_intent_with_claude("hello", {"intent": "unclear"})
    body = json.loads(captured[0]["messages"][0]["content"])
    assert "conversation_so_far" not in body


# ── Wired into the parse-query route end to end ──────────────────────────────

_PARSE = "/p2/b2b-agents/company-people-intelligence/parse-query"


@pytest.fixture
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    return c


def test_parse_query_applies_claudes_fix_to_the_exact_reported_bug(client, monkeypatch):
    """The live-reported shape: seniorities correct, but a 5,001+-shaped bucket
    and a self-defeating keyword both survive the first model's own answer.
    The deterministic guard in _cpi_filters_from_intent already catches the
    keyword; this proves the second-opinion path independently would too, and
    that its numeric correction reaches the response."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setattr(appmod, "_cpi_oai", lambda: object())
    buggy = {"intent": "people_list", "seniorities": ["c_suite", "vp", "director"],
             "industries": ["tech"], "keywords": "top executives",
             "employee_min": 5001}
    monkeypatch.setattr(appmod, "_vimi_chat_json",
                         lambda oai, msgs, mt: (json.dumps(buggy), "m"))
    corrected = {"intent": "people_list", "seniorities": ["c_suite", "vp", "director"],
                 "industries": ["tech"], "person_locations": ["San Francisco"],
                 "employee_min": 500}
    fake = _FakeAnthropicClient(response_text=json.dumps(corrected))
    monkeypatch.setattr(appmod, "_cpi_anthropic", lambda: fake)
    resp = client.post(_PARSE, json={
        "q": "find me top executives in tech industry in san francisco in "
             "companies with employees more than 500"})
    out = resp.get_json()["filters"]
    assert out["employee_min"] == 500
    assert "keywords" not in out
    assert out.get("person_locations") == ["San Francisco"]


def test_parse_query_is_unaffected_when_claude_is_not_configured(client, monkeypatch):
    """No ANTHROPIC_API_KEY anywhere in this test -- confirms the whole feature
    is invisible, not just inert, when Contact Finder has no Claude key: same
    request, same response shape as before this change existed."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setattr(appmod, "_cpi_oai", lambda: object())
    monkeypatch.setattr(appmod, "_vimi_chat_json", lambda oai, msgs, mt: (
        json.dumps({"intent": "people_list", "seniorities": ["c_suite"],
                    "employee_min": 500}), "m"))
    resp = client.post(_PARSE, json={"q": "c-suite at companies with 500+ employees"})
    out = resp.get_json()["filters"]
    assert out["employee_min"] == 500
    assert out["seniorities"] == ["c_suite"]


# ── Wired into chat, with conversation history as context ───────────────────

_CHAT = "/p2/b2b-agents/company-people-intelligence/chat"


def test_chat_passes_recent_history_as_context_to_the_verifier(client, monkeypatch):
    # Mocks everything downstream of the intent parse so this test cannot ever
    # reach a real Apollo/OpenAI network call regardless of what the (fake)
    # intent claims -- see tests/test_cpi_chat_history.py's `_chat` helper for
    # the same convention, established after an earlier version of a test like
    # this one leaked a real outbound call to Apollo using a fake key.
    import tracker.apollo_client as ac
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setattr(appmod, "_vimi_chat_json", lambda oai, msgs, mt: (
        json.dumps({"intent": "person_at_company", "company_name": "Acme",
                    "titles": ["CFO"]}), "m"))
    monkeypatch.setattr(appmod, "_cpi_probe_company_free", lambda *a, **k: {
        "id": "org-acme", "name": "Acme", "primary_domain": "acme.com"})
    monkeypatch.setattr(ac, "search_people", lambda f, k, **kw: [])
    monkeypatch.setattr(ac, "search_companies", lambda *a, **k: [])
    monkeypatch.setattr(appmod, "_cpi_reveal_names", lambda p, k, spend=None: p)
    monkeypatch.setattr(appmod, "_cpi_role_lookup", lambda *a, **k: None)
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("", False))
    monkeypatch.setattr(appmod, "_cpi_grounded_answer",
                        lambda oai, facts, q, research="": "No CFO on record.")
    captured = []
    fake = _FakeAnthropicClient(
        response_text=json.dumps({"intent": "person_at_company", "company_name": "Acme",
                                   "titles": ["CFO"]}),
        capture=captured)
    monkeypatch.setattr(appmod, "_cpi_anthropic", lambda: fake)
    resp = client.post(_CHAT, json={
        "message": "and their CFO?",
        "history": [{"role": "user", "content": "tell me about Acme"},
                    {"role": "assistant", "content": "Acme is a widget maker."}],
    })
    assert resp.status_code == 200
    assert len(captured) == 1
    body = json.loads(captured[0]["messages"][0]["content"])
    assert "tell me about Acme" in body["conversation_so_far"]
    assert body["request"] == "and their CFO?"


def test_chat_degrades_to_a_plain_error_on_a_non_dict_intent(client, monkeypatch):
    """json.loads(raw) is not guaranteed to hand back an object just because
    the system prompt asks for one; parse-query already guarded this, chat did
    not. A stray top-level list must not turn into an unhandled 500."""
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setattr(appmod, "_vimi_chat_json",
                         lambda oai, msgs, mt: (json.dumps(["not", "a", "dict"]), "m"))
    resp = client.post(_CHAT, json={"message": "who is the CFO of Acme?"})
    assert resp.status_code == 200
    assert "try rephrasing" in (resp.get_json().get("answer") or "")
