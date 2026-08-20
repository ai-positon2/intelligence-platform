"""tracker/linkedin_playbook_store.py owns the fix for a prior standalone
tool's IDOR: its saved-run lookup trusted a bare id with no check that the
caller actually owned it. Here every single-row read is ownership-scoped in
the SQL itself (`WHERE id = %s AND email = %s`), so the critical property to
pin is that a run created for one email is invisible to every other email --
not just "the happy path returns the right row".

There's no real Postgres available to test against here, so this uses a small
fake connection/cursor that interprets the actual SQL text this module issues
(column list out of "SELECT ... FROM", AND-chained "col = %s" conditions out
of "WHERE ..."), rather than hand-simulating "correct" behavior independently
of the query. That's what makes this mutation-testable: removing the
"AND email = %s" clause from get_run's SQL changes the WHERE columns the fake
actually filters on, so the cross-user isolation test fails for real, not just
because a hardcoded fake happened to agree with the original code.
"""

import os
import re
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import linkedin_playbook_store as store  # noqa: E402

_FIXED_TS = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)


def _select_columns(sql):
    m = re.search(r"SELECT (.+?) FROM lps_(runs|playbooks)", sql)
    return [c.strip() for c in m.group(1).split(",")]


def _where_columns(sql):
    m = re.search(r"WHERE (.+?)(?: ORDER BY| LIMIT|$)", sql)
    if not m:
        return []
    return re.findall(r"(\w+)\s*=\s*%s", m.group(1))


def _unwrap(v):
    """psycopg2.extras.Json wraps a value for the adapter protocol; unwrap it
    the way a real Postgres round-trip would hand back a plain dict."""
    return v.adapted if hasattr(v, "adapted") else v


class _FakeCursor:
    def __init__(self, db):
        self.db = db
        self._result = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        sql = " ".join(sql.split())
        params = tuple(_unwrap(p) for p in params)

        if sql.startswith("INSERT INTO lps_runs"):
            row = {
                "id": self.db.next_run_id, "email": params[0], "parent_run_id": params[1],
                "run_type": params[2], "company_id": params[3], "company_name": params[4],
                "company_logo": params[5], "status": "running", "error": None,
                "summary": None, "scorecard_score": None, "output": {},
                "created_at": _FIXED_TS, "updated_at": _FIXED_TS,
            }
            self.db.runs.append(row)
            self.db.next_run_id += 1
            self._result = [(row["id"],)]
            return

        if sql.startswith("UPDATE lps_runs SET status"):
            status, output, error, summary, score, run_id = params
            for r in self.db.runs:
                if r["id"] == run_id:
                    r["status"] = status
                    if output is not None:
                        r["output"] = output
                    r["error"] = error
                    if summary is not None:
                        r["summary"] = summary
                    if score is not None:
                        r["scorecard_score"] = score
            self._result = []
            return

        if sql.startswith("SELECT") and "FROM lps_runs" in sql:
            cols = _select_columns(sql)
            where_cols = _where_columns(sql)
            order_desc = "ORDER BY created_at DESC" in sql
            matches = [r for r in self.db.runs
                      if all(r.get(c) == v for c, v in zip(where_cols, params))]
            if order_desc:
                matches = sorted(matches, key=lambda r: r["id"], reverse=True)
            self._result = [tuple(r.get(c) for c in cols) for r in matches]
            return

        if sql.startswith("INSERT INTO lps_playbooks"):
            run_id, email, mode, content = params
            existing = next((p for p in self.db.playbooks
                             if p["run_id"] == run_id and p["mode"] == mode), None)
            if existing:
                existing["content"] = content
                existing["email"] = email
                new_id = existing["id"]
            else:
                new_id = self.db.next_pb_id
                self.db.playbooks.append({
                    "id": new_id, "run_id": run_id, "email": email,
                    "mode": mode, "content": content, "created_at": _FIXED_TS,
                })
                self.db.next_pb_id += 1
            self._result = [(new_id,)]
            return

        if sql.startswith("SELECT") and "FROM lps_playbooks" in sql:
            where_cols = _where_columns(sql)
            matches = [p for p in self.db.playbooks
                      if all(p.get(c) == v for c, v in zip(where_cols, params))]
            self._result = [(p["id"], p["run_id"], p["mode"], p["content"], p["created_at"])
                            for p in matches]
            return

        raise AssertionError("FakeCursor doesn't know how to handle: %s" % sql)

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class _FakeConn:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return _FakeCursor(self.db)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class _FakeDB:
    def __init__(self):
        self.runs = []
        self.playbooks = []
        self.next_run_id = 1
        self.next_pb_id = 1


@pytest.fixture
def fake_db(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(store, "_pg_conn", lambda: _FakeConn(db))
    store._TABLES_READY = True  # skip DDL against the fake -- nothing to create
    return db


# ── Ownership scoping -- the property this module exists to guarantee ───────

def test_a_run_is_invisible_to_a_different_email(fake_db):
    run_id = store.save_run("alice@position2.com", "OWN", "c1", "Acme")
    assert store.get_run(run_id, "bob@position2.com") is None


def test_a_run_is_visible_to_its_own_owner(fake_db):
    run_id = store.save_run("alice@position2.com", "OWN", "c1", "Acme")
    run = store.get_run(run_id, "alice@position2.com")
    assert run is not None
    assert run["company_name"] == "Acme"


def test_email_matching_is_case_insensitive_but_still_scoped(fake_db):
    run_id = store.save_run("Alice@Position2.com", "OWN", "c1", "Acme")
    assert store.get_run(run_id, "ALICE@POSITION2.COM") is not None
    assert store.get_run(run_id, "bob@position2.com") is None


def test_list_runs_only_returns_the_calling_users_own_runs(fake_db):
    store.save_run("alice@position2.com", "OWN", "c1", "Acme")
    store.save_run("bob@position2.com", "OWN", "c2", "Globex")
    alice_runs = store.list_runs("alice@position2.com")
    assert len(alice_runs) == 1
    assert alice_runs[0]["company_name"] == "Acme"


def test_get_children_is_scoped_to_the_caller_not_just_the_parent_id(fake_db):
    parent_id = store.save_run("alice@position2.com", "OWN", "c1", "Acme")
    store.save_run("alice@position2.com", "COMPETITOR", "c2", "Globex", parent_run_id=parent_id)
    assert len(store.get_children(parent_id, "alice@position2.com")) == 1
    assert store.get_children(parent_id, "bob@position2.com") == []


def test_a_nonexistent_run_id_returns_none_not_an_error(fake_db):
    assert store.get_run(999, "alice@position2.com") is None


# ── Playbooks: ownership re-verified, not just inherited from the caller ────

def test_save_playbook_refuses_to_write_against_someone_elses_run(fake_db):
    run_id = store.save_run("alice@position2.com", "OWN", "c1", "Acme")
    result = store.save_playbook(run_id, "bob@position2.com", "OWN", {"headline": "steal this"})
    assert result is None
    assert store.get_playbook(run_id, "alice@position2.com", "OWN") is None


def test_save_playbook_succeeds_for_the_runs_actual_owner(fake_db):
    run_id = store.save_run("alice@position2.com", "OWN", "c1", "Acme")
    result = store.save_playbook(run_id, "alice@position2.com", "OWN", {"headline": "real"})
    assert result is not None
    playbook = store.get_playbook(run_id, "alice@position2.com", "OWN")
    assert playbook["content"] == {"headline": "real"}


def test_regenerating_a_playbook_upserts_rather_than_accumulates(fake_db):
    run_id = store.save_run("alice@position2.com", "OWN", "c1", "Acme")
    store.save_playbook(run_id, "alice@position2.com", "OWN", {"headline": "v1"})
    store.save_playbook(run_id, "alice@position2.com", "OWN", {"headline": "v2"})
    assert len(fake_db.playbooks) == 1
    assert store.get_playbook(run_id, "alice@position2.com", "OWN")["content"] == {"headline": "v2"}


def test_get_playbook_is_scoped_by_email_even_though_run_id_and_mode_match(fake_db):
    run_id = store.save_run("alice@position2.com", "OWN", "c1", "Acme")
    store.save_playbook(run_id, "alice@position2.com", "OWN", {"headline": "real"})
    assert store.get_playbook(run_id, "bob@position2.com", "OWN") is None


# ── Status updates and no-DB degradation ─────────────────────────────────────

def test_update_run_status_sets_status_and_output(fake_db):
    run_id = store.save_run("alice@position2.com", "OWN", "c1", "Acme")
    store.update_run_status(run_id, "complete", output={"strategyagent.strategy": "text"},
                            summary="A short summary", scorecard_score=7.5)
    run = store.get_run(run_id, "alice@position2.com")
    assert run["status"] == "complete"
    assert run["output"] == {"strategyagent.strategy": "text"}
    assert run["summary"] == "A short summary"
    assert run["scorecard_score"] == 7.5


def test_everything_degrades_to_none_or_empty_without_a_database(monkeypatch):
    monkeypatch.setattr(store, "_pg_conn", lambda: None)
    assert store.save_run("alice@position2.com", "OWN", "c1", "Acme") is None
    assert store.get_run(1, "alice@position2.com") is None
    assert store.list_runs("alice@position2.com") == []
    assert store.get_children(1, "alice@position2.com") == []
    assert store.save_playbook(1, "alice@position2.com", "OWN", {}) is None
    assert store.get_playbook(1, "alice@position2.com", "OWN") is None
    assert store.update_run_status(1, "error") is False
