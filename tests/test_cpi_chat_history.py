"""Chat exchanges and enriched contacts are saved to history, server-side.

Requested: "all these chats to the chatbot and the outputs/contact details
should be saved in the history." Before this, the history drawer only held
result sets the RESULTS GRID chose to save; a question asked in the chat panel
and a contact bought with a credit left no trace, so closing the tab lost both.

Saved on the server rather than from the browser on purpose: a saved search is
something a user opts into keeping, but an answer and a purchased contact are
things they would be annoyed to lose, and leaving that to the client means a
closed tab loses the record of a credit already spent.

The chat hook lives in _cpi_chat_reply because every branch of cpi_chat returns
through it. Threading a save through ten separate return statements is how some
answers silently stop being recorded, which these tests pin down.
"""

import json as _json
import os
import sys
import types

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402


@pytest.fixture
def saved(monkeypatch):
    """Capture what would be written to history instead of needing Postgres."""
    captured = []

    # Signature mirrors the real _cpi_history_save exactly, so a caller that
    # drifts from it fails here rather than being silently swallowed by the
    # hook's own except clause.
    def _save(email, entity, label, rows, answer="", filters=None, total=None):
        captured.append({"email": email, "entity": entity, "label": label,
                         "rows": rows, "answer": answer,
                         "filters": filters or {}, "total": total})
        return len(captured)

    monkeypatch.setattr(appmod, "_cpi_history_save", _save)
    return captured


def _chat(monkeypatch, message="CMO of tealium", people=None, role=None,
          intent="person_at_company"):
    import tracker.apollo_client as ac
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setattr(appmod, "OpenAI", lambda **kw: types.SimpleNamespace(),
                        raising=False)
    monkeypatch.setattr(appmod, "_vimi_chat_json", lambda oai, msgs, mt: (_json.dumps({
        "intent": intent, "titles": ["CMO"], "company_name": "Tealium",
        "seniorities": [], "max_results": 10}), "m"))
    monkeypatch.setattr(appmod, "_cpi_probe_company_free", lambda *a, **k: {
        "id": "org-t", "name": "Tealium", "primary_domain": "tealium.com"})
    monkeypatch.setattr(ac, "search_people", lambda f, k, **kw: list(people or []))
    monkeypatch.setattr(ac, "search_companies", lambda *a, **k: [])
    monkeypatch.setattr(appmod, "_cpi_reveal_names", lambda p, k, spend=None: p)
    monkeypatch.setattr(appmod, "_cpi_role_lookup", lambda *a, **k: role)
    monkeypatch.setattr(appmod, "_cpi_research", lambda oai, q, note="": ("", False))
    monkeypatch.setattr(appmod, "_cpi_grounded_answer",
                        lambda oai, facts, q, research="": "Binal Shah is the CMO.")
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "Reporting@Position2.com", "name": "T"}
    r = c.post("/p2/b2b-agents/company-people-intelligence/chat", json={"message": message})
    assert r.status_code == 200
    return r.get_json()


_BINAL = {"id": "p-binal", "full_name": "Binal Shah", "title": "CMO",
          "organization_domain": "tealium.com"}


# ── Chat exchanges ──────────────────────────────────────────────────────────

def test_a_chat_answer_is_saved(saved, monkeypatch):
    _chat(monkeypatch, people=[_BINAL])
    assert len(saved) == 1
    e = saved[0]
    assert e["entity"] == "chat"
    assert e["label"] == "CMO of tealium"
    assert e["answer"] == "Binal Shah is the CMO."


def test_the_question_is_saved_alongside_the_answer(saved, monkeypatch):
    """An answer with no question attached is close to useless later."""
    _chat(monkeypatch, people=[_BINAL])
    assert saved[0]["filters"]["question"] == "CMO of tealium"


def test_the_company_and_cost_are_recorded(saved, monkeypatch):
    _chat(monkeypatch, people=[_BINAL])
    f = saved[0]["filters"]
    assert f["company"] == "Tealium"
    assert f["domain"] == "tealium.com"
    assert f["credits"] == 0


def test_how_the_answer_was_produced_is_recorded(saved, monkeypatch):
    """Both provenance flags, so a reopened answer describes how it was
    ORIGINALLY produced. Saving only "researched" made every replay claim
    "background knowledge, no live web" even when the original cited a live
    source, which is a false statement about our own answer."""
    _chat(monkeypatch, people=[_BINAL])
    f = saved[0]["filters"]
    assert "researched" in f and "web_search" in f


def test_the_people_named_are_saved_so_they_stay_enrichable(saved, monkeypatch):
    _chat(monkeypatch, people=[_BINAL])
    assert saved[0]["rows"] == [{"name": "Binal Shah", "title": "CMO",
                                 "domain": "tealium.com", "apollo_id": "p-binal"}]


def test_the_entry_is_owned_by_the_signed_in_user(saved, monkeypatch):
    """History is per-user, so the owner is taken from the session rather than
    from anything the request body could claim."""
    _chat(monkeypatch, people=[_BINAL])
    assert saved[0]["email"] == "Reporting@Position2.com"


def test_a_records_gap_answer_is_saved_too(saved, monkeypatch):
    """A different branch of cpi_chat entirely. Every branch returns through the
    same reply function, which is why this needs no extra wiring."""
    role = {"name": "Heidi Bullock", "title": "CMO",
            "source": "https://tealium.com/x", "exact_title_match": True}
    _chat(monkeypatch, people=[], role=role)
    assert len(saved) == 1
    assert saved[0]["entity"] == "chat"


def test_a_list_answer_is_saved_too(saved, monkeypatch):
    _chat(monkeypatch, people=[_BINAL], intent="people_list",
          message="CMOs of tealium")
    assert len(saved) == 1
    assert saved[0]["label"] == "CMOs of tealium"


def test_a_disambiguation_prompt_is_not_saved(saved, monkeypatch):
    """It is a question back to the user, not an answer to theirs, and they are
    about to re-ask and get the real one. Saving both puts two entries in the
    drawer for one thing the user asked."""
    import tracker.apollo_client as ac
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setattr(appmod, "OpenAI", lambda **kw: types.SimpleNamespace(),
                        raising=False)
    monkeypatch.setattr(appmod, "_vimi_chat_json", lambda oai, msgs, mt: (_json.dumps({
        "intent": "person_at_company", "titles": ["CMO"], "company_name": "Delta",
        "seniorities": [], "max_results": 10}), "m"))
    monkeypatch.setattr(appmod, "_cpi_probe_company_free", lambda *a, **k: None)
    monkeypatch.setattr(appmod, "_cpi_resolve_company", lambda *a, **k: (None, [
        {"name": "Delta Air Lines", "domain": "delta.com", "id": "o1"},
        {"name": "Delta Dental", "domain": "deltadental.com", "id": "o2"}]))
    monkeypatch.setattr(ac, "search_people", lambda f, k, **kw: [])
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    r = c.post("/p2/b2b-agents/company-people-intelligence/chat",
               json={"message": "CMO of Delta"})
    assert r.status_code == 200
    assert r.get_json().get("choices")
    assert saved == [], "a which-did-you-mean turn is not an answer to save"


def test_an_empty_question_saves_nothing(saved, monkeypatch):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    c.post("/p2/b2b-agents/company-people-intelligence/chat", json={"message": "   "})
    assert saved == []


def test_the_history_hook_never_breaks_the_answer(monkeypatch):
    """The answer is what the user is waiting for. A history failure must be
    invisible to them, not a 500."""
    def _boom(**kw):
        raise RuntimeError("postgres is down")

    monkeypatch.setattr(appmod, "_cpi_history_save", _boom)
    body = _chat(monkeypatch, people=[_BINAL])
    assert body["answer"] == "Binal Shah is the CMO."


def test_the_saved_fields_do_not_leak_into_the_reply(saved, monkeypatch):
    """The history hook reads the reply; it must not add anything to it."""
    body = _chat(monkeypatch, people=[_BINAL])
    assert set(body) <= {"answer", "context", "researched", "web_search",
                         "credits", "enrich", "choices"}


# ── Enriched contacts ───────────────────────────────────────────────────────

def _enrich(monkeypatch, profile, kind="person"):
    monkeypatch.setenv("APOLLO_API_KEY", "k")
    monkeypatch.setattr(appmod, "_cpi_enrich_person", lambda *a, **k: profile)
    monkeypatch.setattr(appmod, "_cpi_enrich_company", lambda *a, **k: profile)
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    r = c.post("/p2/b2b-agents/company-people-intelligence/enrich",
               json={"type": kind, "name": "Binal Shah", "domain": "tealium.com",
                     "apollo_id": "p-binal"})
    assert r.status_code == 200
    return r.get_json()


_PROFILE = {"matched": True, "name": "Binal Shah", "title": "CMO",
            "emails": [{"email": "binal@tealium.com", "status": "Verified"}],
            "phones": [{"number": "+1 858 555 0000"}],
            "company": {"name": "Tealium", "domain": "tealium.com"}}


def test_an_enriched_contact_is_saved(saved, monkeypatch):
    """This is the one action that definitely spent a credit, and the contact
    details are what it bought."""
    _enrich(monkeypatch, _PROFILE)
    assert len(saved) == 1
    assert saved[0]["entity"] == "contact"
    assert saved[0]["label"] == "Binal Shah · CMO"


def test_the_contact_details_themselves_are_saved(saved, monkeypatch):
    """Not just the name: the point is to not pay twice for the same email."""
    _enrich(monkeypatch, _PROFILE)
    stored = saved[0]["rows"][0]
    assert stored["emails"][0]["email"] == "binal@tealium.com"
    assert stored["phones"][0]["number"] == "+1 858 555 0000"


def test_a_failed_enrichment_is_not_saved(saved, monkeypatch):
    """A miss costs no credit and holds nothing worth keeping."""
    _enrich(monkeypatch, {"matched": False})
    assert saved == []


def test_a_company_profile_is_saved_under_its_own_kind(saved, monkeypatch):
    _enrich(monkeypatch, {"matched": True, "name": "Tealium",
                          "industry": "Software", "domain": "tealium.com"},
            kind="company")
    assert len(saved) == 1
    assert saved[0]["entity"] == "company_profile"


def test_the_enrich_reply_says_nothing_about_having_been_saved(saved, monkeypatch):
    """Saving is a side effect, so nothing about the history row may leak into the
    reply: no row id, no saved flag, nothing for the client to start depending on.

    Asserted as an absence rather than as an exact key set. The previous version
    pinned {"profile", "apollo"} and so failed the moment the reply started
    reporting what the enrichment actually cost, which is a change this test has
    no business objecting to.
    """
    body = _enrich(monkeypatch, _PROFILE)
    assert body["profile"]["name"] == "Binal Shah"
    for leaked in ("saved", "history", "history_id", "entry_id", "label", "rows"):
        assert leaked not in body, leaked


# ── The saver itself ────────────────────────────────────────────────────────

def test_no_database_means_no_history_and_no_error(monkeypatch):
    monkeypatch.setattr(appmod, "_pg_conn", lambda: None)
    assert appmod._cpi_history_save("a@b.com", "chat", "q", [], "answer") is None


def test_a_missing_label_saves_nothing(monkeypatch):
    """Guards the insert rather than storing an unidentifiable row."""
    called = []
    monkeypatch.setattr(appmod, "_pg_conn", lambda: called.append(1))
    assert appmod._cpi_history_save("a@b.com", "chat", "", [], "answer") is None
    assert called == []


def test_a_missing_email_saves_nothing(monkeypatch):
    """History is per-user; a row with no owner could be shown to anyone."""
    called = []
    monkeypatch.setattr(appmod, "_pg_conn", lambda: called.append(1))
    assert appmod._cpi_history_save("", "chat", "q", [], "answer") is None
    assert called == []


# ── The real saver's SQL, against a fake connection ─────────────────────────

class _FakeCursor:
    def __init__(self, log):
        self.log = log

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchone(self):
        return [42]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self):
        self.log = []
        self.committed = False
        self.rolled_back = False

    def cursor(self):
        return _FakeCursor(self.log)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


@pytest.fixture
def fake_pg(monkeypatch):
    conn = _FakeConn()
    monkeypatch.setattr(appmod, "_pg_conn", lambda: conn)
    # The table is created once per process and the flag may already be set by
    # another test, so force the DDL to run here.
    monkeypatch.setattr(appmod, "_CPI_HISTORY_TABLE_READY", False, raising=False)
    return conn


def _inserts(conn):
    return [(s, p) for s, p in conn.log if s.startswith("INSERT INTO cpi_search_history")]


def test_the_owner_email_is_stored_lower_cased(fake_pg):
    """Per-user scoping keys on this, so it has to be stable however the session
    happened to case it."""
    appmod._cpi_history_save("Reporting@Position2.COM", "chat", "q", [], "a")
    assert _inserts(fake_pg)[0][1][0] == "reporting@position2.com"


def test_the_new_id_is_returned(fake_pg):
    assert appmod._cpi_history_save("a@b.com", "chat", "q", [], "a") == 42
    assert fake_pg.committed is True


def test_the_answer_column_is_migrated_in_not_assumed(fake_pg):
    """The table shipped before chat entries existed, so the column is added by
    ALTER rather than being part of the CREATE."""
    appmod._cpi_history_save("a@b.com", "chat", "q", [], "a")
    ddl = [s for s, _ in fake_pg.log if s.startswith("ALTER TABLE")]
    assert any("ADD COLUMN IF NOT EXISTS answer" in s for s in ddl)


def test_a_long_label_is_truncated_to_fit(fake_pg):
    appmod._cpi_history_save("a@b.com", "chat", "x" * 500, [], "a")
    assert len(_inserts(fake_pg)[0][1][2]) == 160


def test_a_very_long_answer_is_truncated(fake_pg):
    appmod._cpi_history_save("a@b.com", "chat", "q", [], "y" * 20000)
    assert len(_inserts(fake_pg)[0][1][6]) == 8000


def test_the_stored_rows_are_capped(fake_pg):
    appmod._cpi_history_save("a@b.com", "chat", "q",
                             [{"n": i} for i in range(500)], "a")
    stored = _inserts(fake_pg)[0][1][5].adapted
    assert len(stored) == appmod._CPI_HISTORY_MAX_ROWS


def test_saving_also_prunes_old_and_excess_entries(fake_pg):
    """These rows hold revealed emails and phone numbers, so the TTL is a
    retention rule in its own right, not just a size cap."""
    appmod._cpi_history_save("a@b.com", "chat", "q", [], "a")
    deletes = [s for s, _ in fake_pg.log if s.startswith("DELETE FROM cpi_search_history")]
    assert any("created_at < now() - make_interval" in s for s in deletes)
    assert any("id NOT IN" in s for s in deletes)


def test_a_database_error_is_swallowed_and_rolled_back(monkeypatch):
    class _Boom(_FakeConn):
        def cursor(self):
            raise RuntimeError("connection reset")

    conn = _Boom()
    monkeypatch.setattr(appmod, "_pg_conn", lambda: conn)
    assert appmod._cpi_history_save("a@b.com", "chat", "q", [], "a") is None
    assert conn.rolled_back is True
