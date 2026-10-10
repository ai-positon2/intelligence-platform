"""Market Radar, Phase 9: the cost report (tracker/market_radar_costreport)."""
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracker import market_radar_costreport as CR  # noqa: E402

T0 = datetime(2026, 10, 9, 8, tzinfo=timezone.utc)


def row(run, client, mode, minutes, stage=None, cstatus="done", reserved="0.05", actual=None,
        status="complete"):
    return (run, client, mode, status, T0 + timedelta(minutes=minutes), stage, cstatus,
            Decimal(reserved) if reserved is not None else None,
            Decimal(actual) if actual is not None else None)


def test_a_first_run_is_the_latest_search_plus_the_first_collection_after_it():
    raw = [
        row(1, 7, "baseline", 0, "profile", actual="0.30"),       # an old search, replaced
        row(2, 7, "collect", 5, "report_write", actual="0.04"),   # before the latest search
        row(3, 7, "baseline", 10, "profile", actual="0.05"),
        row(3, 7, "baseline", 10, "rivals_rank", actual="0.03"),
        row(4, 7, "collect", 20, "report_write", actual="0.04"),
        row(5, 7, "collect", 30, "report_write", actual="0.09"),  # a later weekly one
        row(6, 8, "collect", 0),                                  # no search: not a first run
    ]
    out = CR.build(raw)
    assert out["first_run"]["companies"] == [
        {"client_id": 7, "search_run": 3, "collect_run": 4, "usd": 0.12}]
    assert out["first_run"]["median_usd"] == 0.12
    assert out["search"]["runs"] == 2 and out["search"]["max_usd"] == 0.30
    assert out["collect"]["runs"] == 4
    assert out["collect"]["median_usd"] == 0.04   # 0, 0.04, 0.04, 0.09: nearest rank
    assert out["collect"]["max_usd"] == 0.09


def test_an_unmeasured_call_counts_at_its_reservation_and_is_reported():
    raw = [row(1, 7, "collect", 0, "signals_triage", cstatus="reserved", reserved="0.20"),
           row(1, 7, "collect", 0, "report_write", actual="0.05"),
           row(2, 7, "collect", 9, "report_write", cstatus="abandoned", reserved="0.10")]
    out = CR.build(raw)
    assert out["collect"]["max_usd"] == 0.25
    assert out["runs_with_unmeasured_calls"] == 2
    triage = next(s for s in out["stages"] if s["stage"] == "signals_triage")
    assert triage["unmeasured_calls"] == 1 and triage["usd"] == 0.20
    assert any("reservation" in line for line in out["verdict"])


def test_stages_are_ranked_by_spend_with_their_share():
    raw = [row(1, 7, "collect", 0, "report_write", actual="0.06"),
           row(1, 7, "collect", 0, "report_check", actual="0.02"),
           row(2, 7, "collect", 9, "report_write", actual="0.02")]
    out = CR.build(raw)
    assert [s["stage"] for s in out["stages"]] == ["report_write", "report_check"]
    w = out["stages"][0]
    assert (w["calls"], w["runs"], w["usd"], w["usd_per_run"], w["share"]) == (2, 2, 0.08, 0.04, 0.8)


def test_failed_runs_count_but_do_not_price_a_kind_of_run_and_the_cap_is_checked():
    raw = [row(1, 7, "baseline", 0, "profile", actual="1.20"),
           row(2, 7, "collect", 9, "report_write", actual="0.30", status="failed")]
    out = CR.build(raw)
    assert out["failed_runs"] == 1 and out["collect"] == {"runs": 0}
    assert out["over_cap"] == [1]
    assert any("over the $1.0 cap" in line or "over the" in line for line in out["verdict"])
    assert any("cannot be priced" in line for line in out["verdict"])


def test_a_run_with_no_paid_call_is_a_free_run():
    out = CR.build([row(1, 7, "collect", 0, None, cstatus=None, reserved=None)])
    assert out["collect"]["median_usd"] == 0.0 and out["stages"] == []


def test_an_empty_ledger_reports_without_crashing():
    out = CR.build([])
    assert out["search"] == {"runs": 0} and out["first_run"]["companies"] == []


# == Postgres: the real ledger and the route =================================================

from test_market_radar_store import OWNER, OTHER, pg, world  # noqa: E402,F401


def test_the_report_reads_the_real_ledger_and_only_this_persons_runs(pg, world, monkeypatch):
    from tracker import market_radar_ledger as L
    run = world["run"]
    a = L.reserve(run, "profile", "anthropic", model="claude-sonnet-5-5", prompt_tokens=1000,
                  max_output_tokens=500)
    L.record(a, actual_usd="0.04")
    L.reserve(run, "rivals_rank", "anthropic", model="claude-sonnet-5-5", prompt_tokens=1000,
              max_output_tokens=500)                                    # never settled
    pg.update_run(run, status="complete")
    col = pg.create_run(world["client"], OWNER, "collect")
    b = L.reserve(col, "report_write", "anthropic", model="claude-sonnet-5-5", prompt_tokens=1000,
                  max_output_tokens=500)
    L.record(b, actual_usd="0.03")
    pg.update_run(col, status="complete")

    out = CR.report(OWNER)
    assert out["runs_with_unmeasured_calls"] == 1
    assert out["first_run"]["companies"][0]["collect_run"] == col
    assert out["first_run"]["companies"][0]["usd"] > 0.07   # 0.04 + reservation + 0.03
    assert CR.report(OTHER)["search"] == {"runs": 0}

    import app as appmod
    monkeypatch.setattr(appmod, "ADMIN_EMAILS", set(appmod.ADMIN_EMAILS) | {OWNER})
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": OWNER, "name": "T"}
    r = c.get("/p2/admin/market-radar/api/cost-report")
    assert r.status_code == 200 and r.get_json()["first_run"]["runs"] == 1
