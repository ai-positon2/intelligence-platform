"""Market Radar, Phase 3 collection: one company, then a client's list.

The orchestrator's rules are tested against an in-memory store that keeps
the real store's snapshot contract (store.content_hash, first / changed /
same, the previous payload on a change), and once end to end against a
throwaway Postgres: two collections a day apart must store a baseline,
then exactly the week's differences as events.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracker import market_radar_collect as mc  # noqa: E402
from tracker import market_radar_store as real_store  # noqa: E402
from test_market_radar_detectors import Net, ok, site  # noqa: E402

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
ENTITY = {"id": 7, "domain": "acme.com", "name": "Acme", "country": None}


class MemStore:
    def __init__(self):
        self.snaps, self.events, self.entities = {}, {}, {}
        self.saved = []

    def latest_snapshot(self, entity_id, detector):
        rows = self.snaps.get((entity_id, detector))
        return rows[-1] if rows else None

    def save_snapshot(self, entity_id, detector, payload, *, item_count=None, run_id=None):
        self.saved.append(detector)
        rows = self.snaps.setdefault((entity_id, detector), [])
        h = real_store.content_hash(payload)
        latest = rows[-1] if rows else None
        rows.append({"payload": payload, "hash": h, "last_seen_at": self.clock,
                     "item_count": item_count})
        if latest is None:
            return {"first": True, "changed": False, "previous": None}
        changed = latest["hash"] != h
        return {"first": False, "changed": changed, "previous": latest["payload"] if changed else None}

    def record_event(self, entity_id, key, *, type, title, source, status="unknown",
                     event_date=None, summary=None, location=None):
        created = (entity_id, key) not in self.events
        self.events.setdefault((entity_id, key), {"type": type, "title": title, "sources": []})
        self.events[(entity_id, key)]["sources"].append(source)
        return len(self.events), created

    def upsert_entity(self, domain, **kw):
        self.entities[domain] = kw
        return 1


def shop_site(home="Welcome\n20% off everything"):
    return site(pages=[{"kind": "home", "status": "ok", "url": "https://acme.com/"}],
                texts={"https://acme.com/": home},
                country={"code": "GB", "confidence": 0.9},
                signals={"platforms": [{"name": "shopify", "kind": "commerce"}],
                         "ats": [{"vendor": "greenhouse", "board": "acme", "url": ""}]})


def world(products, jobs_list, sitemap_locs, home="Welcome\n20% off everything"):
    net = Net({
        "https://acme.com/products.json?limit=250&page=1": {"products": products},
        "https://boards-api.greenhouse.io/v1/boards/acme/jobs": {"jobs": jobs_list},
        "https://acme.com/sitemap.xml": "<urlset>" + "".join(
            "<url><loc>%s</loc></url>" % l for l in sitemap_locs) + "</urlset>",
    })
    for host in ("news.google.com",):
        pass

    def get(url, **kw):
        if url.startswith("https://news.google.com/"):
            return ok("<rss><channel></channel></rss>")
        return net.get(url, **kw)
    io = {"read_site": lambda url: shop_site(home), "get": get, "get_json": net.get_json,
          "fetch": lambda u: {"status": "ok", "html": "<html></html>", "final_url": u, "note": ""}}
    return io, net


def product(pid, title, price, created="2020-01-01"):
    return {"id": pid, "title": title, "handle": "h%d" % pid, "created_at": created,
            "variants": [{"price": str(price), "available": True}]}


def job(i, title, place):
    return {"id": i, "title": title, "location": {"name": place}}


def run(store, io, now, **kw):
    store.clock = now
    return mc.collect_entity(dict(ENTITY), now=now, io=io, store=store, **kw)


def test_first_collection_is_a_baseline_and_the_next_one_reports_the_difference():
    store = MemStore()
    io, _ = world([product(1, "Runner", 100, created="2026-09-30"), product(2, "Old", 50)],
                  [job(1, "Barista", "Austin")], ["https://acme.com/locations/austin"])
    first = run(store, io, NOW)
    rows = {r["detector"]: r for r in first["rows"]}
    assert rows["catalog"]["snapshot"] == "first" and rows["locations"]["snapshot"] == "first"
    # Only the product the shop itself dates recent is news on a first read.
    assert [e["title"] for e in first["events"]] == ["New product: Runner"]
    assert store.entities == {"acme.com": {"country": "GB"}}   # learnt from its own site

    io, _ = world([product(1, "Runner", 110, created="2026-09-30"), product(2, "Old", 50),
                   product(3, "Trail", 120, created="2026-10-08")],
                  [job(1, "Barista", "Austin")] + [job(i, "Barista", "Merced") for i in range(2, 8)],
                  ["https://acme.com/locations/austin", "https://acme.com/locations/merced"],
                  home="Welcome\nFree shipping over $50")
    later = run(store, io, NOW + timedelta(days=7))
    types = sorted(e["type"] for e in later["events"])
    assert types == ["hiring_surge", "new_job_location", "new_location", "price_increase",
                     "product_launch", "promotion", "promotion_ended"]
    rows = {r["detector"]: r for r in later["rows"]}
    assert rows["jobs"]["snapshot"] == "changed" and rows["jobs"]["events"] == 2


def test_a_shop_that_appears_is_read_as_a_baseline_not_as_every_product_launching():
    store = MemStore()
    io, _ = world([], [], [])
    io["read_site"] = lambda url: site(pages=[{"kind": "home", "status": "ok",
                                               "url": "https://acme.com/"}],
                                       texts={"https://acme.com/": "Hello"})
    rows = {r["detector"]: r for r in run(store, io, NOW)["rows"]}
    assert rows["catalog"]["status"] == "none" and rows["catalog"]["snapshot"] == "first"
    io, _ = world([product(i, "P%d" % i, 10) for i in range(5)], [], [])
    later = run(store, io, NOW + timedelta(days=7))
    rows = {r["detector"]: r for r in later["rows"]}
    assert rows["catalog"]["snapshot"] == "first"
    assert [e for e in later["events"] if e["detector"] == "catalog"] == []


def test_an_unchanged_company_adds_no_events():
    store = MemStore()
    io, _ = world([product(1, "A", 1)], [], [])
    run(store, io, NOW)
    again = run(store, io, NOW + timedelta(days=7))
    assert again["events"] == []
    assert {r["snapshot"] for r in again["rows"] if r["snapshot"]} == {"same"}


def test_a_failed_read_stores_no_snapshot_so_the_next_good_read_compares_with_the_last_good():
    store = MemStore()
    io, _ = world([product(1, "A", 10)], [], [])
    run(store, io, NOW)
    broken, _ = world([product(1, "A", 10)], [], [])
    broken["get_json"] = lambda url, **kw: (None, {"status": "blocked", "note": "refused (HTTP 429)"})
    store.saved.clear()
    mid = run(store, broken, NOW + timedelta(days=7))
    assert {r["detector"]: r["status"] for r in mid["rows"]}["catalog"] == "failed"
    assert "catalog" not in store.saved
    io, _ = world([product(1, "A", 12)], [], [])
    last = run(store, io, NOW + timedelta(days=14))
    assert [e["type"] for e in last["events"] if e["detector"] == "catalog"] == ["price_increase"]


def test_fresh_reads_are_reused_without_fetching():
    store = MemStore()
    io, net = world([product(1, "A", 1)], [], [])
    run(store, io, NOW)
    calls = []
    io["read_site"] = lambda url: calls.append(url) or shop_site()
    again = run(store, io, NOW + timedelta(hours=2))
    assert calls == [] and {r["status"] for r in again["rows"]} == {"reused"}


def test_an_unreadable_site_fails_the_site_detectors_but_news_still_runs():
    store = MemStore()
    io, _ = world([], [], [])
    io["read_site"] = lambda url: {"status": "blocked", "home_url": "https://acme.com/",
                                   "pages": [{"note": "server refused (HTTP 403)", "status": "blocked"}]}
    out = run(store, io, NOW)
    rows = {r["detector"]: r for r in out["rows"]}
    assert rows["news"]["status"] == "empty"
    assert rows["catalog"]["status"] == "failed" and "HTTP 403" in rows["catalog"]["note"]


def test_an_archived_copy_is_not_read_as_this_weeks_page():
    store = MemStore()
    io, _ = world([product(1, "A", 1)], [], [])
    io["read_site"] = lambda url: dict(shop_site(), via_archive=True)
    rows = {r["detector"]: r for r in run(store, io, NOW)["rows"]}
    assert rows["promotions"]["status"] == "skipped" and rows["pages"]["status"] == "skipped"
    assert rows["catalog"]["status"] == "ok"


def test_a_detector_that_breaks_is_one_failed_row(monkeypatch):
    store = MemStore()
    io, _ = world([product(1, "A", 1)], [], [])

    def boom(ctx):
        raise RuntimeError("x")
    monkeypatch.setattr(mc, "DETECTORS", [(n, boom if n == "catalog" else r, c, b, l)
                                          for n, r, c, b, l in mc.DETECTORS])
    rows = {r["detector"]: r for r in run(store, io, NOW)["rows"]}
    assert rows["catalog"]["status"] == "failed" and "RuntimeError" in rows["catalog"]["note"]
    assert rows["promotions"]["status"] == "ok"


def test_out_of_time_detectors_are_skipped_not_empty():
    store = MemStore()
    io, _ = world([product(1, "A", 1)], [], [])
    rows = run(store, io, NOW, deadline=0)["rows"]
    assert {r["status"] for r in rows} == {"skipped"}


def test_coverage_says_how_many_were_read_and_why_not_the_rest():
    results = [{"rows": [{"detector": "jobs", "status": "ok"}]},
               {"rows": [{"detector": "jobs", "status": "none"}]},
               {"rows": [{"detector": "jobs", "status": "failed"}]}]
    line = next(l for l in mc.coverage(results) if l["detector"] == "jobs")
    assert line["text"] == ("Hiring read for 1 of 3 competitors; 1 have no public jobs board, "
                            "1 could not be read.")


# == end to end on Postgres ===========================================================

from test_market_radar_store import pg, OWNER  # noqa: E402,F401


def test_a_client_collection_on_postgres_stores_a_baseline_then_the_weeks_moves(pg):
    me = pg.upsert_entity("client.com", name="Client", country="US")
    client = pg.upsert_client(OWNER, me)
    rival = pg.upsert_entity("acme.com", name="Acme")
    pg.propose_competitor(client, OWNER, rival, "direct", confidence=0.8)
    removed = pg.upsert_entity("gone.com", name="Gone")
    pg.propose_competitor(client, OWNER, removed, "direct", confidence=0.9)
    pg.set_competitor_status(client, OWNER, removed, "removed")
    run1 = pg.create_run(client, OWNER, "collect")
    io, _ = world([product(1, "A", 10)], [job(1, "Barista", "Austin")],
                  ["https://acme.com/locations/austin"])
    first = mc.collect_client(client, OWNER, run_id=run1, io=io, store=pg)
    assert [c["domain"] for c in first["companies"]] == ["acme.com"]   # removed: never collected
    assert first["new_events"] == 0
    assert pg.get_entity(rival)["country"] == "GB"

    io, _ = world([product(1, "A", 10)], [job(1, "Barista", "Austin")],
                  ["https://acme.com/locations/austin", "https://acme.com/locations/merced"])
    later = mc.collect_client(client, OWNER, io=io, store=pg, now=datetime.now(timezone.utc)
                              + timedelta(hours=13))
    assert later["new_events"] == 1
    (ev,) = pg.recent_events([rival])
    assert ev["type"] == "new_location" and ev["title"] == "New location page: Merced"
    summary = {(s["detector"], s["versions"]) for s in pg.snapshot_summaries([rival])}
    assert ("locations", 2) in summary and ("catalog", 1) in summary

    # A third collection inside the reuse window fetches nothing again. (The
    # database stamps real time; "now" only moves the reader's clock.)
    io["read_site"] = lambda url: pytest.fail("the site was read again")
    third = mc.collect_client(client, OWNER, io=io, store=pg, now=datetime.now(timezone.utc)
                              + timedelta(hours=1))
    rows = third["companies"][0]["rows"]
    assert {r["status"] for r in rows if r["detector"] in ("catalog", "locations")} == {"reused"}


def test_collection_runs_do_not_replace_the_last_search(pg):
    me = pg.upsert_entity("client.com", name="Client")
    client = pg.upsert_client(OWNER, me)
    search = pg.create_run(client, OWNER, "baseline")
    collect = pg.create_run(client, OWNER, "collect")
    assert pg.latest_run(client, OWNER)["id"] == search
    assert pg.latest_run(client, OWNER, collect=True)["id"] == collect
    (row,) = pg.list_clients(OWNER)
    assert row["last_run"]["id"] == search and row["last_collect"]["id"] == collect


def test_an_older_table_gains_the_collect_mode_once(pg):
    pg.ensure_tables()
    with pg._tx() as cur:
        cur.execute("ALTER TABLE mr_runs DROP CONSTRAINT mr_runs_mode_check")
        cur.execute("ALTER TABLE mr_runs ADD CONSTRAINT mr_runs_mode_check "
                    "CHECK (mode IN ('baseline','refresh'))")
    pg._TABLES_READY = False
    me = pg.upsert_entity("client.com")
    client = pg.upsert_client(OWNER, me)
    assert pg.create_run(client, OWNER, "collect")
    with pg._tx() as cur:
        cur.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname='mr_runs_mode_check'")
        assert "collect" in cur.fetchone()[0]
