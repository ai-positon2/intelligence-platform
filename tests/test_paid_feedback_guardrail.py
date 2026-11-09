"""Client Feedback Agent: who may ask what.

Admins ask anything. Everyone else gets answers about the client accounts
only: a question about the team's people (who is leaving, who is unhappy,
someone's performance, HR) is refused on the server before any source is
read, by a keyword screen and then a model check; a question that passes is
answered under a prompt that keeps people matters out, with Slack direct
messages left out. Names here are made up.
"""
import os
import sys
from types import SimpleNamespace

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as appmod  # noqa: E402
from tracker import paid_feedback as fb  # noqa: E402
from tracker import paid_store as store  # noqa: E402

ADMIN = sorted(appmod.ADMIN_EMAILS)[0]
STAFF = "made.up.staffer@position2.com"
SAME = {"Origin": "http://localhost"}
CHAT = "/p2/paid-marketing/api/feedback/chat"

PEOPLE = [
    "Who is leaving the company?",
    "who's leaving next month",
    "Is anyone on the Acme team serving notice?",
    "What's our attrition this quarter?",
    "Who is unhappy in the PPC team?",
    "How is Jordan Pemberton performing? Any complaints about this employee?",
    "Which analysts are underperforming?",
    "Any HR issues this week?",
    "Did anyone resign?",
    "What does Riley think about her manager?",
    "Who got promoted last cycle?",
    "Has Casey been put on a PIP?",
    "What's the salary of the new account manager?",
]
ACCOUNTS = [
    "Summarise the last Acme Widgets meeting: decisions, asks and open items",
    "What's the latest budget for Northwind Gardens?",
    "What's been said in Slack about Globex this week?",
    "Is the client unhappy with the lead quality?",
    "Is Initech leaving us at the end of the contract?",
    "What promotions is Contoso running this month?",
    "How is the Fabrikam account performing against its target CPA?",
    "Did Tailspin complain about the landing page?",
    "Any open email threads with Wingtip?",
    "Should we pause or resume the brand campaign?",
]


@pytest.mark.parametrize("q", PEOPLE)
def test_questions_about_people_are_caught_by_the_keyword_screen(q):
    assert fb.people_question(q)


@pytest.mark.parametrize("q", ACCOUNTS)
def test_account_questions_pass_the_keyword_screen(q):
    assert not fb.people_question(q)


# ── start(): the job ─────────────────────────────────────────────────────────

class FakeClient:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        content, stop = reply
        return SimpleNamespace(content=content, stop_reason=stop)


def text(t):
    return SimpleNamespace(type="text", text=t)


@pytest.fixture
def jobs(monkeypatch):
    rec = {"steps": [], "done": None}
    monkeypatch.setattr(store, "create_job", lambda q, by: "j1")
    monkeypatch.setattr(store, "add_step", lambda j, s: rec["steps"].append(s))
    monkeypatch.setattr(store, "finish_job", lambda j, **k: rec.__setitem__("done", k))
    return rec


def test_a_restricted_people_question_never_reaches_the_model(jobs):
    def no_model():
        pytest.fail("the model must not be called")
    fb.start("Who is leaving the team?", [], STAFF, restricted=True, client_factory=no_model, spawn=False)
    assert jobs["done"] == {"answer": fb.RESTRICTED_REPLY, "tools": []}
    assert jobs["steps"][0]["kind"] == "note"


def test_the_model_screen_refuses_what_the_keywords_miss(jobs):
    client = FakeClient([([text("REFUSE")], "end_turn")])
    fb.start("Between us, which folks are polishing their CVs?", [], STAFF, restricted=True,
             client_factory=lambda: client, spawn=False)
    assert jobs["done"]["answer"] == fb.RESTRICTED_REPLY
    assert len(client.calls) == 1 and client.calls[0]["model"] == fb.SCREEN_MODEL
    assert "tools" not in client.calls[0]


def test_the_model_screen_reads_earlier_questions_for_follow_ups(jobs):
    client = FakeClient([([text("REFUSE")], "end_turn")])
    history = [{"role": "user", "content": "Tell me about the Acme team members"},
               {"role": "assistant", "content": "Sure."}]
    fb.start("and which of them might quit?", history, STAFF, restricted=True,
             client_factory=lambda: client, spawn=False)
    sent = client.calls[0]["messages"][0]["content"]
    assert "Acme team members" in sent and sent.rstrip().endswith("which of them might quit?")


def test_an_allowed_restricted_question_is_answered_under_the_restricted_prompt(jobs):
    client = FakeClient([([text("ALLOW")], "end_turn"), ([text("Acme asked for a budget increase.")], "end_turn")])
    fb.start("What did Acme ask for?", [], STAFF, restricted=True, client_factory=lambda: client, spawn=False)
    assert jobs["done"]["answer"] == "Acme asked for a budget increase."
    assert "ACCESS LEVEL" in client.calls[1]["system"]
    assert fb.RESTRICTED_REPLY in client.calls[1]["system"]


def test_a_failed_screen_fails_closed(jobs):
    client = FakeClient([RuntimeError("overloaded")])
    fb.start("What did Acme ask for?", [], STAFF, restricted=True, client_factory=lambda: client, spawn=False)
    assert "error" in jobs["done"] and "answer" not in jobs["done"]


def test_admins_are_not_screened(jobs):
    client = FakeClient([([text("Two people resigned.")], "end_turn")])
    fb.start("Who is leaving the company?", [], ADMIN, restricted=False, client_factory=lambda: client, spawn=False)
    assert jobs["done"]["answer"] == "Two people resigned."
    assert len(client.calls) == 1 and "ACCESS LEVEL" not in client.calls[0]["system"]


# ── Tools: restricted Slack leaves direct messages out ──────────────────────

def _slack(monkeypatch, seen):
    monkeypatch.setattr(fb, "slack_token", lambda: "tok")

    def get(url, params=None, **k):
        seen.append(params)
        return SimpleNamespace(json=lambda: {"ok": True, "results": {"messages": []}, "messages": []})
    return get


def test_restricted_slack_search_skips_direct_messages(monkeypatch):
    seen = []
    fb.Tools(get=_slack(monkeypatch, seen), restricted=True).search_slack({"query": "acme"})
    assert seen[0]["channel_types"] == "public_channel,private_channel"
    seen.clear()
    fb.Tools(get=_slack(monkeypatch, seen)).search_slack({"query": "acme"})
    assert "im" in seen[0]["channel_types"].split(",")


def test_restricted_channel_read_refuses_a_dm(monkeypatch):
    seen = []
    out = fb.Tools(get=_slack(monkeypatch, seen), restricted=True).read_slack_channel({"channel_id": "D0123"})
    assert "Direct messages" in out and not seen
    fb.Tools(get=_slack(monkeypatch, seen), restricted=True).read_slack_channel({"channel_id": "C0123"})
    assert seen[0]["channel"] == "C0123"


# ── The route ───────────────────────────────────────────────────────────────

def client_as(email):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": email, "name": "T"}
    return c


@pytest.mark.parametrize("email,restricted", [(STAFF, True), (ADMIN, False)])
def test_the_chat_route_restricts_everyone_but_admins(monkeypatch, email, restricted):
    assert STAFF not in appmod.ADMIN_EMAILS
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(store, "job_counts", lambda by, stale: (0, 0))
    got = {}
    monkeypatch.setattr(fb, "start", lambda q, h, by, **k: got.update(k) or "a" * 32)
    r = client_as(email).post(CHAT, json={"message": "Who is leaving?"}, headers=SAME)
    assert r.status_code == 202 and got["restricted"] is restricted


def test_the_page_tells_a_standard_user_what_they_can_ask():
    staff = client_as(STAFF).get("/p2/paid-marketing/client-feedback-agent").get_data(as_text=True)
    admin = client_as(ADMIN).get("/p2/paid-marketing/client-feedback-agent").get_data(as_text=True)
    assert 'id="scopeNote"' in staff and "admins only" in staff
    assert 'id="scopeNote"' not in admin
