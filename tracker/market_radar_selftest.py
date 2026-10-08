"""Market Radar: prove the tables and the cost ledger work on THIS database.

Unit tests run against a throwaway local Postgres. Production runs a version
and configuration nobody checked by hand, so this exercises the real tables
end to end (a company, a client, a run, two priced calls, a refused call, a
snapshot pair and a merged event) inside one transaction and then ROLLS IT
BACK. Nothing is left behind except the empty tables themselves, which
ensure_tables() creates and commits first.

Every check reports pass or fail with what it saw, so a failure names itself.
"""
from __future__ import annotations

from decimal import Decimal

from . import market_radar_ledger as ledger
from . import market_radar_store as store

SELFTEST_DOMAIN = "mr-selftest.example"   # .example is reserved; never a real company


def run(owner_email):
    checks = []

    def check(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    store.ensure_tables()
    out = {"tables": store.table_counts()}

    conn = store._connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW server_version")
            out["postgres_version"] = cur.fetchone()[0]

        entity = store.upsert_entity(SELFTEST_DOMAIN, name="Self-test Co", conn=conn)
        rival = store.upsert_entity("https://www.rival." + SELFTEST_DOMAIN + "/about", conn=conn)
        client = store.upsert_client(owner_email, entity, radius_km=5, conn=conn)
        run_id = store.create_run(client, owner_email, "baseline", cost_cap_usd="0.05", conn=conn)
        check("company, client and run created", entity and client and run_id)

        store.propose_competitor(client, owner_email, rival, "direct", confidence=0.9,
                                 found_via=["selftest"], conn=conn)
        store.set_competitor_status(client, owner_email, rival, "removed", conn=conn)
        store.propose_competitor(client, owner_email, rival, "indirect", conn=conn)
        listed = store.competitors(client, owner_email, include_removed=True, conn=conn)
        check("a removed competitor stays removed when found again",
              listed and listed[0]["status"] == "removed" and listed[0]["kind"] == "direct",
              str([(c["domain"], c["status"], c["kind"]) for c in listed]))

        with ledger.track(run_id, "selftest", "anthropic", model="claude-haiku-5-5",
                          prompt_tokens=2000, max_output_tokens=500, conn=conn) as call:
            call.record(usage={"input_tokens": 1000, "output_tokens": 200})
        expected = Decimal("0.0002")   # 1,000 x $0.10/M + 200 x $0.50/M
        s = ledger.summary(run_id, conn=conn)
        check("a measured Haiku call is priced exactly",
              Decimal(str(s["total_usd"])) == expected and not s["partial"],
              "total %s, partial %s" % (s["total_usd"], s["partial"]))

        try:
            ledger.reserve(run_id, "selftest", "anthropic", model="claude-opus-5-5",
                           prompt_tokens=100_000, max_output_tokens=10_000, conn=conn)
            check("a call that could pass the cap is refused", False, "it was booked")
        except ledger.BudgetExceeded as e:
            check("a call that could pass the cap is refused", True, str(e))

        try:
            with ledger.track(run_id, "selftest", "anthropic", model="claude-haiku-5-5",
                              prompt_tokens=1000, max_output_tokens=100, conn=conn):
                raise TimeoutError("simulated lost reply")
        except TimeoutError:
            pass
        s = ledger.summary(run_id, conn=conn)
        check("a call that lost its reply counts at its reservation",
              s["partial"] and s["calls_by_status"].get("unmeasured") == 1
              and s["counted_at_reservation_usd"] > 0, str(s["calls_by_status"]))

        first = store.save_snapshot(entity, "selftest", {"locations": ["a", "b"]}, conn=conn)
        same = store.save_snapshot(entity, "selftest", {"locations": ["a", "b"]}, conn=conn)
        moved = store.save_snapshot(entity, "selftest", {"locations": ["a", "b", "c"]}, conn=conn)
        check("snapshots: first seen, unchanged, then changed",
              first["first"] and not same["changed"] and moved["changed"]
              and moved["previous"] == {"locations": ["a", "b"]},
              "first=%s same.changed=%s moved.changed=%s" % (first["first"], same["changed"],
                                                             moved["changed"]))

        ev, created = store.record_event(entity, "selftest-opening", type="new_location",
                                         title="Opened in Testville", status="announced",
                                         source={"url": "https://news.example/1", "detector": "news"},
                                         conn=conn)
        ev2, created2 = store.record_event(entity, "selftest-opening", type="new_location",
                                           title="Opened in Testville", status="opened",
                                           source={"url": "https://x.example/sitemap", "detector": "sitemap"},
                                           conn=conn)
        with conn.cursor() as cur:
            cur.execute("SELECT evidence_count, status FROM mr_events WHERE id=%s", (ev,))
            evidence, status = cur.fetchone()
        check("one event from two detectors has evidence 2 and the later status",
              created and not created2 and ev == ev2 and evidence == 2 and status == "opened",
              "evidence %s, status %s" % (evidence, status))
    except Exception as e:
        check("self-test finished", False, "%s: %s" % (type(e).__name__, e))
    finally:
        conn.rollback()
        conn.close()

    out["checks"] = checks
    out["passed"] = sum(c["ok"] for c in checks)
    out["failed"] = [c["name"] for c in checks if not c["ok"]]
    out["rolled_back"] = True
    return out
