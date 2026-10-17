"""Account Audit: the saved audits (tracker/paid_audit_store.py), run against a real Postgres.

A throwaway cluster is started in a temporary folder for the whole module (initdb and pg_ctl from the machine). Where those programs are not
installed these tests are SKIPPED, loudly: the store's SQL then has not been run by anything, which the skip message says.
"""
import os
import shutil
import socket
import subprocess
import tempfile

import pytest

from tests.test_paid_audit_history import report
from tracker import paid_audit_history as history
from tracker import paid_audit_store as store

HOMEBREW = ("/opt/homebrew/opt/postgresql@16/bin", "/opt/homebrew/bin", "/usr/local/opt/postgresql@16/bin", "/usr/lib/postgresql/16/bin", "/usr/lib/postgresql/15/bin")


def _bin(name):
    found = shutil.which(name)
    if found:
        return found
    for d in HOMEBREW:
        if os.path.exists(os.path.join(d, name)):
            return os.path.join(d, name)
    return None


@pytest.fixture(scope="module")
def pg():
    initdb, ctl = _bin("initdb"), _bin("pg_ctl")
    if not (initdb and ctl):
        pytest.skip("Postgres (initdb, pg_ctl) is not installed here, so the audit store's SQL was NOT run by any test")
    root = tempfile.mkdtemp(prefix="audit-pg-")
    data = os.path.join(root, "data")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, LC_ALL="C", LANG="C")
    subprocess.run([initdb, "-D", data, "-A", "trust", "-U", "test", "--no-sync", "-E", "UTF8", "--locale=C"], check=True, capture_output=True, env=env)
    subprocess.run([ctl, "-D", data, "-o", "-p %d -c unix_socket_directories= -c listen_addresses=127.0.0.1 -c fsync=off" % port, "-l", os.path.join(root, "log"), "-w", "start"],
                   check=True, capture_output=True, env=env)
    old = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = "postgresql://test@127.0.0.1:%d/postgres" % port
    store._READY = False
    try:
        yield os.environ["DATABASE_URL"]
    finally:
        subprocess.run([ctl, "-D", data, "-m", "immediate", "stop"], capture_output=True, env=env)
        shutil.rmtree(root, ignore_errors=True)
        if old is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = old
        store._READY = False


def rep(score=80, grade="B", result="pass"):
    return report(("G-1", result, "high", "Found it."), ("G-2", "fail", "critical", "Broken."), score=score, grade=grade)


def test_an_audit_is_saved_and_read_back_with_its_results(pg):
    row = store.save("1112223334", "Acme Dental", "ana@position2.com", rep(71, "C"), note="  Onboarding   review  ")
    assert row["accountKey"] == "1112223334" and row["accountName"] == "Acme Dental" and row["createdBy"] == "ana@position2.com" and row["score"] == 71 and row["grade"] == "C"
    assert row["note"] == "Onboarding review" and row["critical"] == 1 and row["high"] == 0 and row["tally"]["fail"] == 1 and "results" not in row and "T" in row["createdAt"]
    got = store.get(row["id"])
    assert got["results"]["G-2"] == {"r": "fail", "s": "critical", "t": "Title G-2", "f": "Broken."} and got["results"]["G-1"]["r"] == "pass"
    assert store.get("0" * 32) is None


def test_a_saved_audit_belongs_to_its_account(pg):
    row = store.save("act_1234567890", "Meta", "ana@position2.com", rep())
    assert store.get(row["id"], "act_1234567890")["id"] == row["id"] and store.get(row["id"], "1112223334") is None


def test_the_list_is_newest_first_per_account_and_carries_no_results(pg):
    a = store.save("2223334445", "Other", "ana@position2.com", rep(60, "C"))
    b = store.save("2223334445", "Other", "ben@position2.com", rep(70, "C"))
    c = store.save("9998887776", "Elsewhere", "ana@position2.com", rep(90, "A"))
    got = store.list_saved("2223334445")
    assert [r["id"] for r in got] == [b["id"], a["id"]] and all("results" not in r for r in got) and got[0]["createdBy"] == "ben@position2.com"
    assert store.list_saved("9998887776")[0]["id"] == c["id"] and store.list_saved("nothing") == []
    assert len(store.list_saved("2223334445", limit=1)) == 1
    assert store.latest("2223334445")["id"] == b["id"] and store.latest("2223334445")["results"]["G-1"]["r"] == "pass" and store.latest("nothing") is None


def test_only_whoever_saved_an_audit_or_an_admin_can_remove_it(pg):
    row = store.save("3334445556", "X", "ana@position2.com", rep())
    assert store.delete(row["id"], "ben@position2.com") is False and store.get(row["id"]) is not None
    assert store.delete(row["id"], "ana@position2.com") is True and store.get(row["id"]) is None and store.delete(row["id"], "ana@position2.com") is False
    other = store.save("3334445556", "X", "ana@position2.com", rep())
    assert store.delete(other["id"], "ben@position2.com", is_admin=True) is True


def test_the_oldest_audits_past_the_limit_are_dropped(pg, monkeypatch):
    monkeypatch.setattr(store, "MAX_KEPT", 3)
    ids = [store.save("4445556667", "Y", "ana@position2.com", rep(60 + i))["id"] for i in range(5)]
    kept = [r["id"] for r in store.list_saved("4445556667")]
    assert kept == ids[::-1][:3] and store.get(ids[0]) is None and store.get(ids[4]) is not None
    assert len(store.list_saved("1112223334")) == 1                                   # another account is untouched


def test_the_note_is_one_short_line(pg):
    assert store.clean_note("  a \n b\t c  ") == "a b c" and store.clean_note(None) == "" and len(store.clean_note("x" * 500)) == 120
    row = store.save("5556667778", "Z", "ana@position2.com", rep(), note="x" * 500)
    assert len(row["note"]) == 120


def test_a_saved_audit_compares_with_a_new_one_end_to_end(pg):
    row = store.save("6667778889", "W", "ana@position2.com", rep(60, "C", result="fail"))
    out = history.compare(rep(80, "B", result="pass"), store.latest("6667778889"))
    assert [r["id"] for r in out["fixed"]] == ["G-1"] and out["scoreChange"] == 20 and out["note"] == "" and out["by"] == "ana@position2.com" and out["since"] == row["createdAt"]
