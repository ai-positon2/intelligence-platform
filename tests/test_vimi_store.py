"""Vimi's jobs in Postgres: the table is created, a question's steps append in order, it finishes with its answer and sources, and the counts say who has one running.

Needs a real Postgres: set VIMI_TEST_PG to a throwaway database url (the rest of the suite runs without one, so this is skipped when it is unset)."""
import os

import pytest

pg = os.environ.get("VIMI_TEST_PG")
pytestmark = pytest.mark.skipif(not pg, reason="set VIMI_TEST_PG to a throwaway postgres url to run the Vimi store tests")


@pytest.fixture()
def store(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg)
    from tracker import paid_store
    paid_store._READY = False
    paid_store.ensure_tables()
    with paid_store._tx() as cur:
        cur.execute("DELETE FROM pm_vimi_jobs")
    return paid_store


def test_a_job_runs_collects_steps_in_order_and_finishes_with_answer_and_sources(store):
    jid = store.create_vimi_job("how is Beta Bionics doing?", "ana@position2.com")
    job = store.get_vimi_job(jid)
    assert job["status"] == "running" and job["steps"] == [] and job["answer"] is None and job["createdBy"] == "ana@position2.com"
    store.add_vimi_step(jid, {"kind": "tool", "label": "Finding the account"})
    store.add_vimi_step(jid, {"kind": "tool", "label": "Reading KPI Monitoring"})
    assert [s["label"] for s in store.get_vimi_job(jid)["steps"]] == ["Finding the account", "Reading KPI Monitoring"]
    store.finish_vimi_job(jid, answer="It is fine.", sources=[{"agent": "KPI Monitoring", "view": "report", "account": "Beta (1)"}])
    job = store.get_vimi_job(jid)
    assert job["status"] == "done" and job["answer"] == "It is fine." and job["sources"][0]["agent"] == "KPI Monitoring" and job["finishedAt"]


def test_a_failed_job_keeps_its_reason(store):
    jid = store.create_vimi_job("q", "a@position2.com")
    store.finish_vimi_job(jid, error="RuntimeError: boom")
    job = store.get_vimi_job(jid)
    assert job["status"] == "failed" and job["error"] == "RuntimeError: boom" and job["answer"] is None


def test_the_counts_are_per_person_and_a_stale_running_job_is_not_counted(store):
    a1, _a2 = store.create_vimi_job("q1", "a@position2.com"), store.create_vimi_job("q2", "a@position2.com")
    store.create_vimi_job("q3", "b@position2.com")
    store.finish_vimi_job(a1, answer="x")
    assert store.vimi_job_counts("a@position2.com", 600) == (1, 2)
    assert store.vimi_job_counts("b@position2.com", 600) == (1, 1)
    with store._tx() as cur:
        cur.execute("UPDATE pm_vimi_jobs SET created_at = now() - interval '2 hours' WHERE created_by = 'b@position2.com'")
    assert store.vimi_job_counts("b@position2.com", 600) == (0, 1)


def test_an_unknown_job_is_none(store):
    assert store.get_vimi_job("nope") is None


def test_a_long_question_is_cut_not_refused(store):
    jid = store.create_vimi_job("x" * 20000, "a@position2.com")
    assert len(store.get_vimi_job(jid)["question"]) == 6000


def test_stop_closes_a_running_job_at_once_and_the_worker_finishing_later_changes_nothing(store):
    jid = store.create_vimi_job("q", "a@position2.com")
    assert store.cancel_vimi_job(jid) is True and store.cancel_vimi_job(jid) is False
    store.finish_vimi_job(jid, answer="too late", sources=[])
    job = store.get_vimi_job(jid)
    assert job["status"] == "failed" and job["error"] == "Stopped." and job["answer"] is None
    assert store.vimi_job_counts("a@position2.com", 600) == (0, 1)


def test_a_finished_job_cannot_be_stopped_or_rewritten(store):
    jid = store.create_vimi_job("q", "a@position2.com")
    store.finish_vimi_job(jid, answer="done")
    assert store.cancel_vimi_job(jid) is False
    store.finish_vimi_job(jid, error="later failure")
    assert store.get_vimi_job(jid)["status"] == "done"
