"""tracker/sci_store.py, mirroring tests/test_linkedin_playbook_store.py's
approach: a fake connection/cursor that interprets the actual SQL text this
module issues (WHERE columns, INSERT/UPDATE targets), rather than
hand-simulating "correct" behavior independently of the query -- so removing
the "AND email = %s" clause from get_run's SQL would make the cross-user
isolation test below fail for real, not just because a hardcoded fake agreed
with the original code.
"""

import os
import re
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_store as store  # noqa: E402

_FIXED_TS = datetime(2026, 8, 25, 12, 0, 0, tzinfo=timezone.utc)


def _unwrap(v):
    return v.adapted if hasattr(v, "adapted") else v


def _where_conditions(sql):
    m = re.search(r"WHERE (.+?)(?: ORDER BY| LIMIT|$)", sql)
    if not m:
        return []
    return [(c, op.upper()) for c, op in re.findall(r"(\w+)\s*(=|ILIKE)\s*%s", m.group(1))]


def _row_matches(row, conds, params):
    for (col, op), val in zip(conds, params):
        actual = row.get(col)
        if op == "ILIKE":
            if str(val).strip("%").lower() not in str(actual or "").lower():
                return False
        elif actual != val:
            return False
    return True


def _select_columns(sql, table):
    m = re.search(r"SELECT (.+?) FROM %s" % table, sql)
    # A DISTINCT ON (col) prefix is not part of the column list.
    cols = re.sub(r"^DISTINCT ON \(\w+\)\s*", "", m.group(1))
    return [c.strip() for c in cols.split(",")]


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

        if sql.startswith("INSERT INTO sci_runs"):
            row = {"id": self.db.next_run_id, "email": params[0], "company_name": params[1],
                  "company_url": params[2], "status": "running", "error": None,
                  "identify_result": None, "synthesis": None,
                  "created_at": _FIXED_TS, "updated_at": _FIXED_TS}
            self.db.runs.append(row)
            self.db.next_run_id += 1
            self._result = [(row["id"],)]
            return

        if sql.startswith("UPDATE sci_runs SET status"):
            status, error, identify_result, synthesis, run_id = params
            for r in self.db.runs:
                if r["id"] == run_id:
                    r["status"] = status
                    r["error"] = error
                    if identify_result is not None:
                        r["identify_result"] = identify_result
                    if synthesis is not None:
                        r["synthesis"] = synthesis
            self._result = []
            return

        if sql.startswith("SELECT") and "FROM sci_runs" in sql:
            cols = _select_columns(sql, "sci_runs")
            conds = _where_conditions(sql)
            matches = [r for r in self.db.runs if _row_matches(r, conds, params)]
            if "created_at DESC" in sql:
                matches = sorted(matches, key=lambda r: r["id"], reverse=True)
            distinct_on = re.search(r"DISTINCT ON \((\w+)\)", sql)
            if distinct_on:
                seen, deduped = set(), []
                for r in matches:
                    marker = r.get(distinct_on.group(1))
                    if marker in seen:
                        continue
                    seen.add(marker)
                    deduped.append(r)
                matches = deduped
            if "LIMIT %s" in sql:
                matches = matches[:params[len(conds)]]
            self._result = [tuple(r.get(c) for c in cols) for r in matches]
            return

        if sql.startswith("INSERT INTO sci_platform_runs"):
            cols_m = re.search(r"\(([^)]+)\)\s*VALUES", sql)
            cols = [c.strip() for c in cols_m.group(1).split(",")]
            values = dict(zip(cols, params))
            existing = next((p for p in self.db.platform_runs
                             if p["run_id"] == values["run_id"] and p["platform"] == values["platform"]), None)
            if existing:
                existing.update(values)
                new_id = existing["id"]
            else:
                row = {"id": self.db.next_pr_id, "handle": None, "handle_confidence": None,
                      "status": "pending", "status_detail": None, "post_count": 0,
                      "last_post_at": None, "window_start": None, "window_end": None,
                      "collected_at": None, "analyzed_at": None, "error": None,
                      "created_at": _FIXED_TS, "updated_at": _FIXED_TS}
                row.update(values)
                self.db.platform_runs.append(row)
                new_id = row["id"]
                self.db.next_pr_id += 1
            self._result = [(new_id,)]
            return

        if sql.startswith("SELECT") and "FROM sci_platform_runs" in sql:
            cols_m = re.search(r"SELECT (.+?) FROM sci_platform_runs", sql)
            cols = [c.strip() for c in cols_m.group(1).split(",")]
            conds = _where_conditions(sql)
            matches = [r for r in self.db.platform_runs if _row_matches(r, conds, params)]
            self._result = [tuple(r.get(c) for c in cols) for r in matches]
            return

        if sql.startswith("INSERT INTO sci_posts"):
            (run_id, platform, pid, post_url, post_type, caption, posted_at,
             media_urls, metrics, raw) = params
            existing = next((p for p in self.db.posts
                             if p["run_id"] == run_id and p["platform"] == platform
                             and p["platform_post_id"] == pid), None)
            if existing:
                existing.update({"post_url": post_url, "metrics": metrics, "raw": raw})
            else:
                self.db.posts.append({
                    "id": self.db.next_post_id, "run_id": run_id, "platform": platform,
                    "platform_post_id": pid, "post_url": post_url, "post_type": post_type,
                    "caption": caption, "posted_at": posted_at, "media_urls": media_urls,
                    "metrics": metrics, "raw": raw, "creative_analysis": None,
                    "creative_analysis_status": "pending", "creative_analysis_error": None,
                    "created_at": _FIXED_TS, "updated_at": _FIXED_TS,
                })
                self.db.next_post_id += 1
            self._result = []
            return

        if sql.startswith("UPDATE sci_posts SET creative_analysis"):
            analysis, status, error, post_id = params
            for p in self.db.posts:
                if p["id"] == post_id:
                    p["creative_analysis"] = analysis
                    p["creative_analysis_status"] = status
                    p["creative_analysis_error"] = error
            self._result = []
            return

        if sql.startswith("SELECT") and "FROM sci_posts" in sql:
            cols_m = re.search(r"SELECT (.+?) FROM sci_posts", sql)
            cols = [c.strip() for c in cols_m.group(1).split(",")]
            conds = _where_conditions(sql)
            matches = [r for r in self.db.posts if _row_matches(r, conds, params)]
            self._result = [tuple(r.get(c) for c in cols) for r in matches]
            return

        if sql.startswith("INSERT INTO sci_spend_log"):
            self._result = []
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
        self.platform_runs = []
        self.posts = []
        self.next_run_id = 1
        self.next_pr_id = 1
        self.next_post_id = 1


@pytest.fixture
def fake_db(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(store, "_pg_conn", lambda: _FakeConn(db))
    store._TABLES_READY = True
    return db


# ── Ownership scoping ────────────────────────────────────────────────────────

def test_a_run_is_invisible_to_a_different_email(fake_db):
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    assert store.get_run(run_id, "bob@position2.com") is None


def test_a_run_is_visible_to_its_own_owner(fake_db):
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    run = store.get_run(run_id, "alice@position2.com")
    assert run is not None
    assert run["company_name"] == "Acme Inc"


def test_list_runs_only_returns_that_emails_runs(fake_db):
    store.save_run("alice@position2.com", "Acme Inc")
    store.save_run("bob@position2.com", "Globex")
    runs = store.list_runs("alice@position2.com")
    assert len(runs) == 1
    assert runs[0]["company_name"] == "Acme Inc"


def test_known_companies_match_by_partial_name(fake_db):
    store.save_run("alice@position2.com", "Google", "google.com")
    store.save_run("alice@position2.com", "Myntra", "myntra.com")
    found = store.search_known_companies("alice@position2.com", "goo")
    assert [c["name"] for c in found] == ["Google"]
    assert found[0]["website"] == "google.com"
    assert found[0]["from_history"] is True


def test_known_companies_are_scoped_to_the_asking_user(fake_db):
    """One user's analyzed-company list must never leak into another's
    search, the same ownership property every read in this module guarantees."""
    store.save_run("alice@position2.com", "Google", "google.com")
    assert store.search_known_companies("bob@position2.com", "goo") == []


def test_known_companies_collapse_repeat_analyses_of_one_company(fake_db):
    for _ in range(3):
        store.save_run("alice@position2.com", "Google", "google.com")
    found = store.search_known_companies("alice@position2.com", "google")
    assert len(found) == 1


def test_known_companies_is_case_insensitive(fake_db):
    store.save_run("alice@position2.com", "Google", "google.com")
    assert len(store.search_known_companies("alice@position2.com", "GOOGLE")) == 1


def test_known_companies_needs_a_query(fake_db):
    store.save_run("alice@position2.com", "Google", "google.com")
    assert store.search_known_companies("alice@position2.com", "  ") == []


def test_known_companies_honours_the_limit(fake_db):
    for i in range(5):
        store.save_run("alice@position2.com", "Acme %d" % i, None)
    assert len(store.search_known_companies("alice@position2.com", "acme", limit=2)) == 2


def test_known_companies_returns_empty_without_postgres(monkeypatch):
    monkeypatch.setattr(store, "_pg_conn", lambda: None)
    assert store.search_known_companies("alice@position2.com", "goo") == []


def test_update_run_status_is_not_ownership_scoped(fake_db):
    # By design -- the background worker knows its own run_id and never
    # accepts a caller-supplied one, so this call has no email to check.
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    assert store.update_run_status(run_id, "done") is True
    run = store.get_run(run_id, "alice@position2.com")
    assert run["status"] == "done"


# ── Platform runs ────────────────────────────────────────────────────────────

def test_upsert_platform_run_creates_then_updates_the_same_row(fake_db):
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    store.upsert_platform_run(run_id, "instagram", status="identifying", handle="acme")
    store.upsert_platform_run(run_id, "instagram", status="ok", post_count=12)
    rows = store.get_platform_runs(run_id)
    assert len(rows) == 1
    assert rows[0]["status"] == "ok"
    assert rows[0]["post_count"] == 12
    assert rows[0]["handle"] == "acme"  # earlier field survives a later partial update


def test_one_platform_failing_does_not_touch_another_platforms_row(fake_db):
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    store.upsert_platform_run(run_id, "instagram", status="ok", post_count=5)
    store.upsert_platform_run(run_id, "youtube", status="scrape_failed", error="blocked")
    rows = {r["platform"]: r for r in store.get_platform_runs(run_id)}
    assert rows["instagram"]["status"] == "ok"
    assert rows["youtube"]["status"] == "scrape_failed"


# ── Posts ─────────────────────────────────────────────────────────────────

def test_upsert_posts_then_update_creative_analysis_round_trips(fake_db):
    run_id = store.save_run("alice@position2.com", "Acme Inc")
    written = store.upsert_posts(run_id, "instagram", [
        {"platform_post_id": "p1", "post_url": "https://instagram.com/p/p1",
         "post_type": "image", "caption": "hello", "posted_at": None,
         "media_urls": ["https://cdn/p1.jpg"], "metrics": {"likes": 10}, "raw": {}},
    ])
    assert written == 1
    posts = store.get_posts(run_id, "instagram")
    assert len(posts) == 1
    assert posts[0]["creative_analysis_status"] == "pending"

    store.update_post_creative_analysis(posts[0]["id"], {"subject": "a product shot"}, status="ok")
    posts = store.get_posts(run_id, "instagram")
    assert posts[0]["creative_analysis_status"] == "ok"
    assert posts[0]["creative_analysis"]["subject"] == "a product shot"
