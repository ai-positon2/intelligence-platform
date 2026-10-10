"""Market Radar tables, pricing and cost ledger.

Pricing is pure arithmetic and is tested exactly. The store and ledger run
against a real, throwaway Postgres (skipped when the binaries are missing),
because the rules that matter live in SQL: upserts that must not undo a
user's edit, a cap that parallel callers must not slip past, snapshots that
must notice a change.
"""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from decimal import Decimal

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import market_radar_costs as costs  # noqa: E402

OWNER = "owner@position2.com"
OTHER = "other@position2.com"


# == pricing (no database) =====================================================

def test_haiku_call_is_priced_exactly():
    usd, notes = costs.model_cost("claude-haiku-5-5", {"input_tokens": 1000, "output_tokens": 200})
    assert usd == Decimal("0.0002") and notes == []


def test_sonnet_cache_read_is_five_percent_and_writes_use_the_reported_split():
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000,
             "cache_read_input_tokens": 1_000_000, "cache_creation_input_tokens": 300_000,
             "cache_creation": {"ephemeral_5m_input_tokens": 200_000, "ephemeral_1h_input_tokens": 100_000}}
    usd, notes = costs.model_cost("claude-sonnet-5-5", usage)
    # 1M x $2 + 1M x $0.10 + 0.2M x $2.50 + 0.1M x $4 + 0.1M x $10
    assert usd == Decimal("2") + Decimal("0.10") + Decimal("0.5") + Decimal("0.4") + Decimal("1")
    assert notes == []


def test_unsplit_cache_writes_are_priced_high_never_low():
    usd, notes = costs.model_cost("claude-opus-5-5", {"input_tokens": 0, "output_tokens": 0,
                                                     "cache_creation_input_tokens": 1_000_000})
    assert usd == Decimal("8")          # the 1-hour rate, not the 5-minute $5
    assert any("1-hour" in n for n in notes)


def test_a_cache_split_that_does_not_add_up_is_refused():
    with pytest.raises(ValueError):
        costs.model_cost("claude-opus-5-5", {"input_tokens": 0, "output_tokens": 0,
                                            "cache_creation_input_tokens": 10,
                                            "cache_creation": {"ephemeral_5m_input_tokens": 3}})


def test_batch_halves_every_token_price():
    full, _ = costs.model_cost("claude-opus-5-5", {"input_tokens": 1000, "output_tokens": 1000})
    half, notes = costs.model_cost("claude-opus-5-5", {"input_tokens": 1000, "output_tokens": 1000}, batch=True)
    assert half * 2 == full and "Batch API rates" in notes


def test_haiku_long_prompts_pay_the_higher_rate_for_the_whole_request():
    short, _ = costs.model_cost("claude-haiku-5-5", {"input_tokens": 100_000, "output_tokens": 0})
    long_, notes = costs.model_cost("claude-haiku-5-5", {"input_tokens": 90_000, "output_tokens": 1000,
                                                        "cache_read_input_tokens": 20_000})
    assert short == Decimal("0.01")
    assert long_ == (Decimal(90_000) * Decimal("0.50") + Decimal(20_000) * Decimal("0.05")
                     + Decimal(1000) * Decimal("2.50")) / 1_000_000
    assert notes and "long-prompt" in notes[0]


def test_web_searches_are_added_at_a_cent_each():
    usd, _ = costs.model_cost("claude-sonnet-5-5", {"input_tokens": 0, "output_tokens": 0,
                                                   "server_tool_use": {"web_search_requests": 3}})
    assert usd == Decimal("0.03")


def test_dated_model_ids_use_their_family_rate():
    a, _ = costs.model_cost("claude-sonnet-5-5", {"input_tokens": 10, "output_tokens": 10})
    b, _ = costs.model_cost("claude-sonnet-5-5-20261001", {"input_tokens": 10, "output_tokens": 10})
    assert a == b


@pytest.mark.parametrize("model, usage", [
    ("claude-unknown-9", {"input_tokens": 1, "output_tokens": 1}),
    ("claude-haiku-5-5", {"input_tokens": -1, "output_tokens": 1}),
    ("claude-haiku-5-5", {"input_tokens": "12", "output_tokens": 1}),
    ("claude-haiku-5-5", None),
])
def test_unpriceable_usage_raises_instead_of_guessing(model, usage):
    with pytest.raises((ValueError, costs.UnknownPrice)):
        costs.model_cost(model, usage)


def test_worst_case_assumes_full_input_rate_and_the_whole_output_budget():
    assert costs.worst_case_model_usd("claude-opus-5-5", 10_000, 2_000, searches=2) == \
        Decimal("0.04") + Decimal("0.04") + Decimal("0.02")
    assert costs.worst_case_model_usd("claude-haiku-5-5", 200_000, 0) == Decimal("0.1")   # long tier


def test_provider_prices_and_credit_providers():
    assert costs.provider_cost("google_places_pro", 10) == Decimal("0.32")
    assert costs.provider_cost("apollo", 5) is None
    with pytest.raises(costs.UnknownPrice):
        costs.provider_cost("some_search_api", 1)


def test_token_estimate_rounds_up():
    assert costs.estimate_tokens("") == 0
    assert costs.estimate_tokens("abcd") == 2
    assert costs.estimate_tokens("歯科") == 2      # 6 bytes


# == domains (no database) =====================================================

from tracker import market_radar_store as store  # noqa: E402


@pytest.mark.parametrize("given, expected", [
    ("https://www.AspenDental.com/dentist/ca/merced/", "aspendental.com"),
    ("aspendental.com", "aspendental.com"),
    ("http://shop.brand.co.uk:8080/x", "shop.brand.co.uk"),
    ("www.brand.com.", "brand.com"),
    ("https://bücher.de", "xn--bcher-kva.de"),
])
def test_normalize_domain(given, expected):
    assert store.normalize_domain(given) == expected


@pytest.mark.parametrize("bad", ["", "   ", "localhost", "https://", "not a url"])
def test_normalize_domain_refuses_hosts_that_cannot_name_a_company(bad):
    with pytest.raises(ValueError):
        store.normalize_domain(bad)


# == Postgres ==================================================================

@pytest.fixture
def pg(monkeypatch):
    initdb, pg_ctl = shutil.which("initdb"), shutil.which("pg_ctl")
    if not (initdb and pg_ctl):
        pytest.skip("Postgres binaries not installed")
    tmp = tempfile.mkdtemp()
    data = os.path.join(tmp, "data")
    env = dict(os.environ, LC_ALL="C")
    # UTF-8, as production is: under LC_ALL=C initdb picks SQL_ASCII, which
    # refuses the "\u2014" in a quoted page and the "ã" in São Paulo.
    subprocess.run([initdb, "-D", data, "-U", "t", "--auth=trust", "-E", "UTF8", "--locale=C"],
                   check=True, capture_output=True, env=env)
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]; sock.close()
    subprocess.run([pg_ctl, "-D", data, "-l", os.path.join(tmp, "log"), "-o",
                    "-p %d -c listen_addresses=127.0.0.1 -c unix_socket_directories=''" % port,
                    "-w", "start"], check=True, capture_output=True, env=env)
    try:
        monkeypatch.setenv("DATABASE_URL", "postgresql://t@127.0.0.1:%d/postgres" % port)
        monkeypatch.setattr(store, "_TABLES_READY", False)
        yield store
    finally:
        subprocess.run([pg_ctl, "-D", data, "-m", "immediate", "stop"], capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def world(pg):
    """A company, a client owned by OWNER, and a baseline run capped at $0.10."""
    entity = pg.upsert_entity("https://www.acme-dental.com", name="Acme Dental")
    client = pg.upsert_client(OWNER, entity, radius_km=5)
    run = pg.create_run(client, OWNER, "baseline", cost_cap_usd="0.10")
    return {"entity": entity, "client": client, "run": run}


def test_schema_is_created_once_and_is_idempotent(pg):
    counts = pg.table_counts()
    assert set(counts) == set(pg.TABLES) and all(v == 0 for v in counts.values())
    pg._TABLES_READY = False
    assert pg.table_counts() == counts      # running the DDL again is harmless


def test_the_same_company_from_different_urls_is_one_row(pg):
    a = pg.upsert_entity("https://www.acme.com/about", name="Acme")
    b = pg.upsert_entity("acme.com", country="US")
    assert a == b
    row = pg.get_entity(a)
    assert (row["name"], row["country"]) == ("Acme", "US")   # omitted fields left alone


def test_clients_are_scoped_to_their_owner(pg, world):
    with pytest.raises(PermissionError):
        pg.competitors(world["client"], OTHER)
    with pytest.raises(PermissionError):
        pg.create_run(world["client"], OTHER, "refresh")
    assert pg.get_run(world["run"], OTHER) is None
    assert pg.get_run(world["run"], OWNER)["status"] == "queued"


def test_a_user_edit_survives_the_competitor_being_found_again(pg, world):
    rival = pg.upsert_entity("rival.com")
    keep = pg.upsert_entity("keeper.com")
    pg.propose_competitor(world["client"], OWNER, rival, "direct", confidence=0.9)
    pg.propose_competitor(world["client"], OWNER, keep, "indirect", confidence=0.5)
    pg.set_competitor_status(world["client"], OWNER, rival, "removed")
    pg.set_competitor_status(world["client"], OWNER, keep, "confirmed", kind="direct")
    # a rerun finds both again with different labels
    pg.propose_competitor(world["client"], OWNER, rival, "local", confidence=0.95)
    pg.propose_competitor(world["client"], OWNER, keep, "aspirational", confidence=0.4)
    shown = pg.competitors(world["client"], OWNER)
    assert [(c["domain"], c["status"], c["kind"]) for c in shown] == [("keeper.com", "confirmed", "direct")]
    every = {c["domain"]: c for c in pg.competitors(world["client"], OWNER, include_removed=True)}
    assert every["rival.com"]["status"] == "removed" and every["rival.com"]["kind"] == "direct"


def test_a_proposed_competitor_takes_the_newer_label(pg, world):
    rival = pg.upsert_entity("rival.com")
    pg.propose_competitor(world["client"], OWNER, rival, "indirect", confidence=0.4)
    pg.propose_competitor(world["client"], OWNER, rival, "direct", confidence=0.8)
    [c] = pg.competitors(world["client"], OWNER)
    assert (c["kind"], float(c["confidence"])) == ("direct", 0.8)


def test_snapshots_report_first_unchanged_changed_and_a_revert(pg, world):
    e = world["entity"]
    first = pg.save_snapshot(e, "sitemap_locations", {"urls": ["/a", "/b"]}, item_count=2)
    same = pg.save_snapshot(e, "sitemap_locations", {"urls": ["/a", "/b"]})
    grown = pg.save_snapshot(e, "sitemap_locations", {"urls": ["/a", "/b", "/c"]})
    back = pg.save_snapshot(e, "sitemap_locations", {"urls": ["/a", "/b"]})
    assert first["first"] and not first["changed"]
    assert not same["first"] and not same["changed"]
    assert grown["changed"] and grown["previous"] == {"urls": ["/a", "/b"]}
    assert back["changed"] and back["previous"] == {"urls": ["/a", "/b", "/c"]}   # a closure
    # The week after a revert is unchanged. Picking "latest" by when content
    # was FIRST seen would compare against /c's row and report a false change
    # every week from here on.
    steady = pg.save_snapshot(e, "sitemap_locations", {"urls": ["/a", "/b"]})
    assert not steady["changed"] and steady["previous"] is None
    assert pg.latest_snapshot(e, "sitemap_locations")["payload"] == {"urls": ["/a", "/b"]}
    assert pg.table_counts()["mr_snapshots"] == 2    # unchanged content adds no row


def test_snapshot_hash_ignores_key_order(pg, world):
    pg.save_snapshot(world["entity"], "d", {"a": 1, "b": 2})
    assert not pg.save_snapshot(world["entity"], "d", {"b": 2, "a": 1})["changed"]


def test_one_event_reported_by_many_sources_counts_distinct_detectors(pg, world):
    e = world["entity"]
    ev, created = pg.record_event(e, "open-merced", type="new_location", title="Opens in Merced",
                                  status="announced", source={"url": "https://wire/1", "detector": "news"})
    for url in ("https://wire/1", "https://repost/2", "https://repost/3"):   # syndicated copies
        pg.record_event(e, "open-merced", type="new_location", title="Opens in Merced",
                        source={"url": url, "detector": "news"})
    again, created_again = pg.record_event(e, "open-merced", type="new_location", title="x",
                                           status="opened", event_date="2026-10-01",
                                           source={"url": "https://acme/sitemap", "detector": "sitemap"})
    pg.record_event(e, "open-merced", type="new_location", title="x",       # vague status: ignored
                    source={"url": "https://jobs/4", "detector": "jobs"})
    assert created and not created_again and ev == again
    import psycopg2
    with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT evidence_count, status, event_date, jsonb_array_length(sources), title "
                    "FROM mr_events WHERE id=%s", (ev,))
        evidence, status, date, n_sources, title = cur.fetchone()
    assert evidence == 3                       # news, sitemap, jobs
    assert n_sources == 5                      # wire/1 stored once
    assert status == "opened" and str(date) == "2026-10-01"
    assert title == "Opens in Merced"          # the first title stays


def test_an_event_source_needs_a_url_and_a_detector(pg, world):
    with pytest.raises(ValueError):
        pg.record_event(world["entity"], "k", type="t", title="x", source={"url": "https://a"})


def test_rescoring_never_touches_the_clients_feedback(pg, world):
    ev, _ = pg.record_event(world["entity"], "k", type="t", title="x",
                            source={"url": "https://a", "detector": "news"})
    pg.link_client_event(world["client"], ev, score=5, severity="LOW", run_id=world["run"])
    pg.set_feedback(world["client"], OWNER, ev, "down", "not relevant")
    pg.link_client_event(world["client"], ev, score=9, severity="HIGH")
    import psycopg2
    with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT score, severity, feedback, feedback_reason FROM mr_client_events")
        assert cur.fetchone() == (Decimal("9.000"), "HIGH", "down", "not relevant")
    with pytest.raises(PermissionError):
        pg.set_feedback(world["client"], OTHER, ev, "up")


def test_industry_source_remembers_last_success_and_last_error(pg):
    sid = pg.save_industry_source("dental", "https://trade.example/feed", country="US", ok=True, item_count=20)
    pg.save_industry_source("dental", "https://trade.example/feed", country="US", ok=False, error="HTTP 503")
    pg.save_industry_source("dental", "https://trade.example/feed", country="US")      # not read this time
    import psycopg2
    with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, last_ok_at IS NOT NULL, last_error, item_count FROM mr_industry_sources")
        assert cur.fetchone() == (sid, True, "HTTP 503", 20)
    pg.save_industry_source("dental", "https://trade.example/feed", country="US", ok=True)
    with psycopg2.connect(os.environ["DATABASE_URL"]) as conn, conn.cursor() as cur:
        cur.execute("SELECT last_error FROM mr_industry_sources")
        assert cur.fetchone() == (None,)        # a success clears the old error


def test_pulses_are_shared_by_industry_and_country_and_the_registry_reads_back(pg):
    a = pg.save_pulse("naics:621210", "GB", {"status": "ok", "themes": [1]}, run_id=None)
    b = pg.save_pulse("naics:621210", "GB", {"status": "failed", "themes": []})
    pg.save_pulse("naics:621210", "US", {"status": "ok", "themes": [2]})
    last = pg.latest_pulse("naics:621210", "GB")
    assert last["id"] == b > a and last["payload"]["status"] == "failed"
    assert last["created_at"].tzinfo is not None
    assert pg.latest_pulse("naics:621210", "DE") is None
    pg.save_industry_source("naics:621210", "https://dentistry.co.uk/feed/", country="GB",
                            site_domain="dentistry.co.uk", kind="trade", discovered_from="Dentistry.co.uk",
                            ok=True, item_count=9)
    pg.save_industry_source("naics:621210", "https://www.bbc.co.uk/", country="GB",
                            site_domain="bbc.co.uk", kind="none", ok=False, error="no feed found")
    rows = pg.industry_sources("naics:621210", "GB")
    assert [(r["kind"], r["item_count"], r["last_error"]) for r in rows] == [
        ("trade", 9, None), ("none", None, "no feed found")]
    assert rows[0]["created_at"].tzinfo is not None and pg.industry_sources("naics:621210", "US") == []


# == the ledger ================================================================

from tracker import market_radar_ledger as ledger  # noqa: E402


def test_a_measured_call_replaces_its_reservation(pg, world):
    call = ledger.reserve(world["run"], "profile", "anthropic", model="claude-sonnet-5-5",
                          prompt_tokens=20_000, max_output_tokens=3_000)
    s = ledger.summary(world["run"])
    assert s["total_usd"] == 0.07 and s["partial"]            # 20k x $2 + 3k x $10, in flight
    assert ledger.record(call, usage={"input_tokens": 15_000, "output_tokens": 1_000}) == "done"
    s = ledger.summary(world["run"])
    assert s["total_usd"] == 0.04 and not s["partial"]        # 15k x $2 + 1k x $10
    assert s["by_stage"] == {"profile": 0.04} and s["remaining_usd"] == 0.06


def test_a_call_that_could_pass_the_cap_is_refused_and_leaves_no_row(pg, world):
    ledger.reserve(world["run"], "a", "anthropic", model="claude-sonnet-5-5",
                   prompt_tokens=20_000, max_output_tokens=3_000)          # $0.07 of $0.10
    with pytest.raises(ledger.BudgetExceeded) as err:
        ledger.reserve(world["run"], "b", "anthropic", model="claude-sonnet-5-5",
                       prompt_tokens=10_000, max_output_tokens=1_000)      # $0.03 more: exactly fits
        ledger.reserve(world["run"], "c", "anthropic", model="claude-haiku-5-5",
                       prompt_tokens=1, max_output_tokens=1)               # anything more does not
    assert "cap" in str(err.value)
    assert ledger.summary(world["run"])["calls"] == 2


def test_parallel_reservations_cannot_slip_past_the_cap_together(pg, world):
    # Ten threads each try to book $0.02 against a $0.10 cap at the same time.
    results = []

    def book():
        try:
            ledger.reserve(world["run"], "parallel", "anthropic", model="claude-sonnet-5-5",
                           prompt_tokens=10_000, max_output_tokens=0)
            results.append("ok")
        except ledger.BudgetExceeded:
            results.append("refused")

    threads = [threading.Thread(target=book) for _ in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 5 and results.count("refused") == 5
    assert ledger.summary(world["run"])["total_usd"] == 0.10


def test_a_lost_reply_counts_at_its_reservation_never_zero(pg, world):
    with pytest.raises(TimeoutError):
        with ledger.track(world["run"], "news", "anthropic", model="claude-haiku-5-5",
                          prompt_tokens=10_000, max_output_tokens=2_000):
            raise TimeoutError("reply lost")
    s = ledger.summary(world["run"])
    assert s["partial"] and s["calls_by_status"] == {"unmeasured": 1}
    assert s["total_usd"] == s["counted_at_reservation_usd"] == 0.002


def test_a_block_that_forgets_to_record_is_unmeasured_too(pg, world):
    with ledger.track(world["run"], "news", "anthropic", model="claude-haiku-5-5",
                      prompt_tokens=10, max_output_tokens=10):
        pass
    assert ledger.summary(world["run"])["calls_by_status"] == {"unmeasured": 1}


def test_a_refused_request_known_to_be_unbilled_costs_nothing(pg, world):
    with ledger.track(world["run"], "x", "anthropic", model="claude-opus-5-5",
                      prompt_tokens=1000, max_output_tokens=1000) as call:
        call.record(error="400 invalid_request_error", billed=False)
    s = ledger.summary(world["run"])
    assert s["total_usd"] == 0 and not s["partial"] and s["calls_by_status"] == {"failed": 1}


def test_unpriceable_usage_is_unmeasured_not_zero(pg, world):
    call = ledger.reserve(world["run"], "x", "anthropic", model="claude-haiku-5-5",
                          prompt_tokens=1000, max_output_tokens=1000)
    assert ledger.record(call, usage={"input_tokens": "lots"}) == "unmeasured"
    assert ledger.summary(world["run"])["total_usd"] == 0.0006


def test_provider_reported_charges_and_credits(pg, world):
    a = ledger.reserve(world["run"], "places", "apify", reserved_usd="0.02")
    ledger.record(a, actual_usd="0.0123")
    b = ledger.reserve(world["run"], "headcount", "apollo", units=3)
    ledger.record(b, units=3)
    c = ledger.reserve(world["run"], "verify", "google_places_pro", units=2)
    ledger.record(c, units=2)
    s = ledger.summary(world["run"])
    assert s["by_provider"] == {"apify": 0.0123, "apollo": 0.0, "google_places_pro": 0.064}
    assert s["credits"] == {"apollo": 3} and not s["partial"]


def test_an_unknown_provider_cannot_be_booked(pg, world):
    with pytest.raises(costs.UnknownPrice):
        ledger.reserve(world["run"], "x", "mystery_api", units=1)


def test_a_call_cannot_be_settled_twice(pg, world):
    call = ledger.reserve(world["run"], "x", "anthropic", model="claude-haiku-5-5",
                          prompt_tokens=10, max_output_tokens=10)
    ledger.record(call, usage={"input_tokens": 10, "output_tokens": 10})
    with pytest.raises(ValueError):
        ledger.record(call, usage={"input_tokens": 10, "output_tokens": 10})


def test_abandoned_calls_keep_counting_and_can_still_be_measured_later(pg, world):
    call = ledger.reserve(world["run"], "x", "anthropic", model="claude-haiku-5-5",
                          prompt_tokens=10_000, max_output_tokens=0)
    assert ledger.abandon_open(world["run"]) == 1
    assert ledger.summary(world["run"])["calls_by_status"] == {"abandoned": 1}
    assert ledger.record(call, usage={"input_tokens": 5_000, "output_tokens": 0}) == "done"
    assert ledger.summary(world["run"])["total_usd"] == 0.0005


def test_a_finished_run_takes_no_new_calls(pg, world):
    store.update_run(world["run"], status="complete", summary={"ok": True})
    with pytest.raises(RuntimeError):
        ledger.reserve(world["run"], "late", "anthropic", model="claude-haiku-5-5",
                       prompt_tokens=1, max_output_tokens=1)
    assert store.get_run(world["run"], OWNER)["finished_at"] is not None


def test_default_cap_comes_from_the_environment_or_one_dollar(pg, world, monkeypatch):
    monkeypatch.delenv("MR_RUN_COST_CAP_USD", raising=False)
    r1 = store.create_run(world["client"], OWNER, "refresh")
    monkeypatch.setenv("MR_RUN_COST_CAP_USD", "0.25")
    r2 = store.create_run(world["client"], OWNER, "refresh")
    assert ledger.summary(r1)["cap_usd"] == 1.0 and ledger.summary(r2)["cap_usd"] == 0.25


# == the production self-test ==================================================

def test_selftest_passes_on_a_real_database_and_leaves_nothing_behind(pg):
    from tracker import market_radar_selftest
    out = market_radar_selftest.run(OWNER)
    assert out["failed"] == [], out["checks"]
    assert out["passed"] == len(out["checks"]) >= 7
    assert all(v == 0 for v in pg.table_counts().values())    # rolled back


# == the route =================================================================

ROUTE = "/p2/admin/external-usage/market-radar-schema-check"


def _client(email):
    import app as appmod
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


def test_route_runs_the_selftest_for_an_admin(monkeypatch):
    from tracker import market_radar_selftest
    seen = {}
    monkeypatch.setattr(market_radar_selftest, "run", lambda email: seen.setdefault("email", email) and {"passed": 7})
    resp = _client("reporting@position2.com").post(ROUTE)
    assert resp.status_code == 200 and resp.get_json() == {"passed": 7}
    assert seen["email"] == "reporting@position2.com"


def test_route_says_when_there_is_no_database(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(store, "_TABLES_READY", False)
    resp = _client("reporting@position2.com").post(ROUTE)
    assert resp.status_code == 503 and resp.get_json() == {"error": "DATABASE_URL is not set"}


@pytest.mark.parametrize("email, headers, status", [
    (None, {}, 302),
    ("someone@position2.com", {}, 403),
    ("reporting@position2.com", {"Origin": "https://evil.example"}, 403),
])
def test_route_access(monkeypatch, email, headers, status):
    from tracker import market_radar_selftest
    monkeypatch.setattr(market_radar_selftest, "run", lambda email: {"passed": 1})
    assert _client(email).post(ROUTE, headers=headers).status_code == status
