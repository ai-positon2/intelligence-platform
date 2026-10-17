"""Strategy & Campaign Book: what is saved (tracker/paid_strategy_store.py), run against a real Postgres.

A throwaway cluster is started in a temporary folder for the whole module (initdb and pg_ctl from the machine). Where those programs are not
installed these tests are SKIPPED, loudly: the store's SQL then has not been run by anything, which the skip message says.
"""
import datetime as dt
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time

import pytest

from tests import strategy_fakes as F
from tracker import paid_store, paid_strategy as ps, paid_strategy_store as store

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
        pytest.skip("Postgres (initdb, pg_ctl) is not installed here, so the strategy store's SQL was NOT run by any test")
    root = tempfile.mkdtemp(prefix="strategy-pg-")
    data = os.path.join(root, "data")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, LC_ALL="C", LANG="C")                    # a macOS postmaster refuses to start under an unset or invalid locale
    subprocess.run([initdb, "-D", data, "-A", "trust", "-U", "test", "--no-sync", "-E", "UTF8", "--locale=C"], check=True, capture_output=True, env=env)
    subprocess.run([ctl, "-D", data, "-o", "-p %d -c unix_socket_directories= -c listen_addresses=127.0.0.1 -c fsync=off" % port, "-l", os.path.join(root, "log"), "-w", "start"], check=True, capture_output=True, env=env)
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


def new(by="ana@position2.com", **kw):
    return store.create(by=by, inputs=F.clean(**kw), account_name="Acme Dental")


def test_a_build_is_saved_and_read_back_whole(pg):
    rec = new()
    got = store.get(rec["id"])
    assert got["name"] == "Acme Dental" and got["status"] == "queued" and got["created_by"] == "ana@position2.com" and got["account_id"] == "1234567890" and got["account_name"] == "Acme Dental"
    assert got["inputs"]["website"] == "https://acmedental.com/" and got["inputs"]["research"]["website"] is True and got["archived"] is False and got["research"] == {} and got["plan"] == {}
    assert [s["key"] for s in got["progress"]["steps"]][:2] == ["r:website", "r:competitors"] and got["created_at"].endswith("+00:00") or "T" in got["created_at"]
    assert store.get("0" * 32) is None


def test_only_one_run_can_hold_a_build_and_a_stale_run_can_be_taken_over(pg):
    rec = new()
    assert store.begin(rec["id"]) is True and store.begin(rec["id"]) is False
    assert store.get(rec["id"])["status"] == "running"
    with store._tx() as cur:
        cur.execute("UPDATE pm_strategies SET updated_at = now() - make_interval(secs => %s) WHERE id = %s", (ps.STALE_AFTER_S + 5, rec["id"]))
    assert store.begin(rec["id"]) is True                       # the process that held it died: nothing was saved for too long
    store.set_step(rec["id"], "r:website", "running")           # a build that keeps saving is alive, however long it has been running
    with store._tx() as cur:
        cur.execute("UPDATE pm_strategies SET started = now() - make_interval(secs => %s) WHERE id = %s", (ps.STALE_AFTER_S * 3, rec["id"]))
    assert store.begin(rec["id"]) is False
    store.finish(rec["id"], cost={"usd": 1.0})
    assert store.begin(rec["id"]) is True


def test_a_fresh_run_clears_what_was_kept_and_a_resume_keeps_it(pg):
    rec = new()
    store.begin(rec["id"])
    store.save_research(rec["id"], "website", {"key": "website", "state": "ok", "data": {"pages": [1]}})
    store.save_plan_part(rec["id"], "profile", {"summary": "s"})
    store.fail(rec["id"], "stopped", cost={"usd": 0.5})
    assert store.begin(rec["id"], resume=True) is True
    got = store.get(rec["id"])
    assert got["research"]["website"]["data"] == {"pages": [1]} and got["plan"]["profile"] == {"summary": "s"} and got["error"] == "" and got["cost"] == {"usd": 0.5}
    store.fail(rec["id"], "stopped again")
    assert store.begin(rec["id"]) is True
    got = store.get(rec["id"])
    assert got["research"] == {} and got["plan"] == {} and got["cost"] == {}


def test_pieces_saved_from_several_threads_all_survive(pg):
    rec = new()
    keys = ["website", "competitors", "social", "history", "keywords", "seasonality", "market"]
    errors = []

    def put(k):
        try:
            for i in range(5):
                store.save_research(rec["id"], k, {"key": k, "state": "ok", "n": i})
        except Exception as e:                              # noqa: BLE001
            errors.append(e)
    ts = [threading.Thread(target=put, args=(k,)) for k in keys]
    [t.start() for t in ts]
    [t.join() for t in ts]
    got = store.get(rec["id"])["research"]
    assert not errors and sorted(got) == sorted(keys) and all(v["n"] == 4 for v in got.values())


def test_steps_changed_on_several_threads_all_land(pg):
    rec = new()
    steps = ps.steps_for(F.clean())
    store.set_steps(rec["id"], steps, phase="Researching")
    errors = []

    def go(s):
        try:
            store.set_step(rec["id"], s["key"], "done", "note for " + s["key"])
        except Exception as e:                              # noqa: BLE001
            errors.append(e)
    ts = [threading.Thread(target=go, args=(s,)) for s in steps]
    [t.start() for t in ts]
    [t.join() for t in ts]
    got = store.get(rec["id"])["progress"]
    assert not errors and got["phase"] == "Researching" and {s["state"] for s in got["steps"]} == {"done"} and got["steps"][0]["note"] == "note for r:website"
    store.set_step(rec["id"], "r:website", "failed", "x" * 900, phase="Writing")
    assert len(store.get(rec["id"])["progress"]["steps"][0]["note"]) == 400 and store.get(rec["id"])["progress"]["phase"] == "Writing"
    store.set_step("0" * 32, "r:website", "done")            # a build that is gone is not an error


def test_plan_parts_and_whole_plans_round_trip_with_unicode_and_dates(pg):
    rec = new()
    store.save_plan_part(rec["id"], "profile", {"summary": "Café 日本語 \U0001F600"})
    store.save_plan_part(rec["id"], "when", {"at": dt.datetime(2026, 10, 16, 9, 0)})
    got = store.get(rec["id"])["plan"]
    assert got["profile"]["summary"] == "Café 日本語 \U0001F600" and got["when"]["at"].startswith("2026-10-16")
    store.save_plan(rec["id"], {"campaigns": [{"name": "A"}]})
    assert store.get(rec["id"])["plan"] == {"campaigns": [{"name": "A"}]}


def test_finishing_and_failing(pg):
    rec = new()
    store.begin(rec["id"])
    store.finish(rec["id"], cost={"usd": 2.5, "calls": 3}, note="n" * 700)
    got = store.get(rec["id"])
    assert got["status"] == "done" and got["cost"] == {"usd": 2.5, "calls": 3} and len(got["note"]) == 600 and got["error"] == ""
    store.fail(rec["id"], "e" * 900)
    got = store.get(rec["id"])
    assert got["status"] == "error" and len(got["error"]) == 600 and got["cost"] == {"usd": 2.5, "calls": 3}


def test_the_light_status_leaves_out_the_plan_and_an_archived_build_disappears(pg):
    rec = new()
    store.begin(rec["id"])
    light = store.get_light(rec["id"])
    assert set(light) == {"id", "status", "progress", "error", "note", "updatedAt", "started", "stale", "kind"} and light["status"] == "running" and light["started"] and light["stale"] is False
    store.archive(rec["id"])
    assert store.get_light(rec["id"]) is None and rec["id"] not in [r["id"] for r in store.list_recent()] and store.get(rec["id"])["archived"] is True
    assert store.begin(rec["id"]) is False


def test_recent_builds_are_newest_first_and_limited(pg):
    ids = []
    for i in range(3):
        ids.append(new(name="Client %d" % i)["id"])
        time.sleep(0.01)
    got = [r["id"] for r in store.list_recent(limit=3)]
    assert got == ids[::-1]
    assert len(store.list_recent(limit=1)) == 1


def test_the_tables_are_made_once_and_again_safely(pg):
    store._READY = False
    store.ensure_tables()
    store.ensure_tables()
    with store._tx() as cur:
        cur.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = 'pm_strategies'")
        assert cur.fetchone()[0] == 17


def test_a_running_build_that_has_stopped_beating_is_stale_and_a_beat_makes_it_fresh_again(pg):
    rec = new()
    assert store.get(rec["id"])["stale"] is False                                  # queued is not running
    store.begin(rec["id"])
    with store._tx() as cur:
        cur.execute("UPDATE pm_strategies SET updated_at = now() - make_interval(secs => %s) WHERE id = %s", (ps.STALE_AFTER_S + 10, rec["id"]))
    assert store.get_light(rec["id"])["stale"] is True and store.get(rec["id"])["stale"] is True
    store.touch(rec["id"])
    assert store.get_light(rec["id"])["stale"] is False and store.get(rec["id"])["stale"] is False
    store.finish(rec["id"], cost={})
    with store._tx() as cur:
        cur.execute("UPDATE pm_strategies SET updated_at = now() - make_interval(secs => %s) WHERE id = %s", (ps.STALE_AFTER_S + 10, rec["id"]))
    assert store.get(rec["id"])["stale"] is False                                  # only a running build can be stale
    store.touch(rec["id"])
    with store._tx() as cur:
        cur.execute("SELECT updated_at < now() - interval '5 seconds' FROM pm_strategies WHERE id = %s", (rec["id"],))
        assert cur.fetchone()[0] is True                                           # a finished build is not touched


def test_the_list_does_not_carry_the_research_or_the_plan_only_the_campaigns_counted(pg):
    rec = new(name="Listed Client")
    store.begin(rec["id"])
    store.save_research(rec["id"], "website", {"state": "ok", "data": {"pages": ["x" * 1000]}})
    store.save_plan_part(rec["id"], "campaigns", [{"name": "A"}, {"name": "B"}, {"name": "C"}])
    store.finish(rec["id"], cost={"usd": 1.5})
    row = next(r for r in store.list_recent() if r["id"] == rec["id"])
    assert row["research"] == {} and row["plan"] == {} and row["campaign_count"] == 3 and row["cost"] == {"usd": 1.5} and row["name"] == "Listed Client"
    assert ps.brief(row)["campaigns"] == 3 and ps.brief(row)["cost"] == 1.5
    bare = next(r for r in store.list_recent() if r["id"] != rec["id"])
    assert bare["campaign_count"] == 0


def test_a_build_belongs_to_one_agent_and_each_agents_list_holds_only_its_own(pg):
    """Business Research saves its builds in the same table, with its own steps, and says so in its inputs (kind 'business')."""
    from tests import business_fakes as B
    from tracker import paid_business as pb
    mine = store.create(by="ana@position2.com", inputs=B.clean(name="Business Listed %d" % time.time_ns()), account_name="", steps=pb.steps_for(B.clean()))
    theirs = new(name="Strategy Listed %d" % time.time_ns())
    assert [s["key"] for s in store.get(mine["id"])["progress"]["steps"]][-4:] == ["a:core", "a:audience", "a:implications", "f:finish"]
    assert "a:strategy" in [s["key"] for s in store.get(theirs["id"])["progress"]["steps"]]                    # no steps given: Strategy & Campaign Book's own
    business = {r["id"] for r in store.list_recent(kind="business")}
    strategy = {r["id"] for r in store.list_recent()}
    assert mine["id"] in business and theirs["id"] not in business and theirs["id"] in strategy and mine["id"] not in strategy
    assert store.get_light(mine["id"])["kind"] == "business" and store.get_light(theirs["id"])["kind"] == "strategy"


def test_runs_are_counted_per_person_in_the_database_for_the_last_day(pg):
    who = "counted-%d@position2.com" % time.time_ns()
    assert store.runs_today(who) == 0
    store.record_run(who)
    store.record_run(who)
    store.record_run("someone-else@position2.com")
    assert store.runs_today(who) == 2
    with store._tx() as cur:
        cur.execute("UPDATE pm_strategy_runs SET at = now() - interval '25 hours' WHERE email = %s AND id = (SELECT min(id) FROM pm_strategy_runs WHERE email = %s)", (who, who))
    assert store.runs_today(who) == 1


def test_a_nul_character_in_a_web_page_does_not_stop_the_build_being_saved(pg):
    rec = new()
    nul = chr(0)
    store.save_research(rec["id"], "website", {"state": "ok", "data": {"pages": [{"text": "before" + nul + "after", "title": "T" + nul}]}})
    store.save_plan_part(rec["id"], "profile", {"summary": "a" + nul + "b"})
    store.set_step(rec["id"], "r:website", "failed", "bad" + nul + "note")
    store.fail(rec["id"], "error" + nul)
    got = store.get(rec["id"])
    assert got["research"]["website"]["data"]["pages"][0] == {"text": "beforeafter", "title": "T"} and got["plan"]["profile"]["summary"] == "ab" and got["error"] == "error"
