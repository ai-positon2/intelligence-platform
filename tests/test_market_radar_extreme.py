"""Market Radar, Phase 9: extreme cases, end to end on Postgres.

The real collection (tracker/market_radar_run.collect_job, every stage) runs
with the outside world switched off: no DNS for any host but the database,
no map data, no model key. Whatever cannot be done must be said, the run
must finish, and the page, report and weekly update must still render.
"""
import json
import os
import socket
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tracker import market_radar_run as mrun  # noqa: E402
from tracker import market_radar_views as views  # noqa: E402

from test_market_radar_store import OWNER, pg  # noqa: E402,F401

LOCAL = ("127.0.0.1", "localhost", "::1")
PROFILE = {"name": "Acme Dental", "one_liner": "Family dentist in Austin.", "archetype": "local_single",
           "industry": {"plain_label": "Dentist", "naics_code": "621210"},
           "facts": {"website": "https://acme-dental.com/"},
           "hq": {"city": "Austin", "country_code": "US"}, "markets": ["US"],
           "hq_point": {"lat": 30.27, "lon": -97.74}, "offerings": ["implants", "checkups"],
           "keywords": ["dentist", "dental implants"]}


@pytest.fixture
def offline(monkeypatch):
    """No network but the database's, no Overture, no model."""
    real = socket.getaddrinfo
    asked = []

    def getaddrinfo(host, *a, **k):
        if host in LOCAL:
            return real(host, *a, **k)
        asked.append(host)
        raise socket.gaierror(socket.EAI_NONAME, "offline test: %s" % host)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    from tracker import market_radar_places as places

    def no_map(*a, **k):
        raise RuntimeError("offline test: no map data")

    for name in ("releases", "latest_release", "query"):
        monkeypatch.setattr(places, name, no_map)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    return asked


def collect(pg, client, monitor=None):
    run = pg.create_run(client, OWNER, "collect")
    mrun.collect_job(run, client, OWNER, monitor=monitor)
    return run, pg.get_run(run, OWNER)


def everything_renders(client):
    """The page's views, the report page and a weekly update preview, as the
    routes build them, all as JSON."""
    from tracker import market_radar_digest as D
    moves = views.moves_view(client, OWNER)
    report = views.report_view(client, OWNER)
    digest_id, digest = D.make_digest(client, OWNER)
    for part in (moves, report, digest, views.client_view(client, OWNER)
                 if hasattr(views, "client_view") else None):
        json.dumps(part, default=str)
    return moves, report, digest


def test_a_company_whose_site_could_never_be_read_still_gets_a_finished_run(pg, offline):
    """clovedental.in refuses datacenter addresses: its profile never got
    written and it has no competitors."""
    me = pg.upsert_entity("clovedental.in", name="Clove Dental")
    client = pg.upsert_client(OWNER, me)
    run, row = collect(pg, client)
    assert row["status"] == "complete", row["error"]
    s = row["summary"]
    assert s["status"] == "empty" and s["companies"] == []
    for stage in ("radar", "pulse", "signals", "report"):
        assert stage in s, stage
    # nothing pretends to have looked, and the reason is one plain sentence
    assert s["coverage"] == []
    note = s["report"]["note"]
    assert s["report"]["status"] == "empty" and "0 of 0" not in note
    assert note.startswith("No competitors are being followed yet")
    assert "website has not been read yet" in note
    moves, report, digest = everything_renders(client)
    assert moves["events"] == [] and digest["status"] in ("quiet", "failed", "no_data")


def test_the_world_switched_off_is_reported_as_not_read_never_as_nothing_found(pg, offline):
    me = pg.upsert_entity("acme-dental.com", name="Acme Dental", country="US",
                          archetype="local_single")
    pg.set_profile(me, PROFILE)
    client = pg.upsert_client(OWNER, me, radius_km=5)
    for domain in ("rival-one.com", "rival-two.com"):
        rival = pg.upsert_entity(domain, name=domain.split(".")[0])
        pg.propose_competitor(client, OWNER, rival, "direct", confidence=0.8)
    run, row = collect(pg, client, monitor="preview")
    assert row["status"] == "complete", row["error"]
    s = row["summary"]
    assert offline, "the run never tried the network"
    # The weekly update does not call this a quiet week.
    assert s["digest"]["status"] == "unread", s["digest"]
    # Every competitor read failed, and the coverage says so.
    text = " ".join(l["text"] for l in s["coverage"])
    assert "could not be read" in text or "failed" in text
    # The radar and pulse could not run: each says why, none says "nothing new".
    radar = json.dumps(s["radar"]).lower()
    assert "nothing new" not in radar and "no new" not in radar
    pulse = s["pulse"]
    assert "error" in pulse or all(p.get("status") != "ok" for p in (pulse.get("markets") or [pulse]))
    # Nothing to write about, and the report says why as it happened: a
    # collection ran and could not read, it is not "nothing collected yet".
    rep = s["report"]
    assert rep["status"] == "empty"
    assert "collected yet" not in rep["note"] and "could not be read" in rep["note"]
    assert "What was and was not read: " in rep["note"]
    moves, report, digest = everything_renders(client)
    assert moves["events"] == [] and report["status"] == "empty"
    # the page's list of updates carries what was not read
    (u,) = [u for u in views.monitor_view(client, OWNER)["updates"] if u["status"] == "unread"]
    assert u["gaps"] and any("could not be read" in g for g in u["gaps"])


def test_a_failed_report_does_not_take_the_last_written_one_away(pg):
    me = pg.upsert_entity("acme-dental.com", name="Acme Dental")
    client = pg.upsert_client(OWNER, me)
    brief = {"summary": {"text": "Good week.", "cites": ["M1"]}, "top": []}
    good = pg.save_report(client, {"status": "ok", "brief": brief, "pack": {"moves": []}})
    assert views.report_view(client, OWNER)["latest_attempt"] is None
    pg.save_report(client, {"status": "failed", "brief": None,
                            "note": "The report could not be written (not_configured: no key)."})
    v = views.report_view(client, OWNER)
    assert v["report_id"] == (good[0] if isinstance(good, tuple) else good)
    assert v["brief"] == brief and v["status"] == "ok"
    assert v["latest_attempt"]["status"] == "failed" and "no key" in v["latest_attempt"]["note"]
    # with no written report at all, the failure itself is what is shown
    other = pg.upsert_client(OWNER, pg.upsert_entity("b.example"))
    pg.save_report(other, {"status": "empty", "brief": None, "note": "nothing"})
    v = views.report_view(other, OWNER)
    assert v["status"] == "empty" and v["note"] == "nothing" and v["latest_attempt"] is None


def test_a_spent_cap_refuses_every_model_call_before_it_is_made(pg, offline, monkeypatch):
    """Moves to write about, a model key, and a cap already used up: every
    model call is refused by the ledger before it is sent, nothing is spent,
    and the report says the cap stopped it."""
    from datetime import date, timedelta
    from tracker import market_radar_ledger as L
    from tracker import market_radar_llm as llm
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-a-key")
    sent = []

    class Client:
        class messages:
            @staticmethod
            def create(**kw):
                sent.append(kw)
                raise AssertionError("a model call was sent past the cap")

    monkeypatch.setattr(llm, "default_client", lambda: Client)
    me = pg.upsert_entity("acme-dental.com", name="Acme Dental", country="US", archetype="local_single")
    pg.set_profile(me, PROFILE)
    client = pg.upsert_client(OWNER, me, radius_km=5)
    rival = pg.upsert_entity("rival-one.com", name="Rival One")
    pg.propose_competitor(client, OWNER, rival, "direct", confidence=0.9)
    pg.set_competitor_status(client, OWNER, rival, "confirmed")
    pg.record_event(rival, "news:x1", type="acquisition", title="Rival One buys Smile Co",
                    status="completed", event_date=(date.today() - timedelta(days=2)).isoformat(),
                    source={"url": "https://news.example/a", "detector": "news",
                            "headline": "Rival One buys Smile Co", "publisher": "Pub"})
    run = pg.create_run(client, OWNER, "collect", cost_cap_usd="0.000001")
    mrun.collect_job(run, client, OWNER, monitor="preview")
    row = pg.get_run(run, OWNER)
    assert row["status"] == "complete", row["error"]
    s = row["summary"]
    assert sent == []
    assert L.summary(run)["total_usd"] == 0
    rep = s["report"]
    assert rep["status"] == "failed" and "budget" in rep["note"] and "cap" in rep["note"]
    # the digest had a move to write about and was refused the same way
    assert s["digest"]["status"] == "failed"
    v = views.report_view(client, OWNER)
    assert v["status"] == "failed" and v["latest_attempt"] is None


def test_clients_sharing_a_competitor_collected_at_once_store_each_fact_once(pg):
    """Six clients track the same shop and are collected at the same moment
    (a Monday 07:00 slot for all of them): no collection breaks, and each
    launch and snapshot is stored once."""
    import concurrent.futures
    from datetime import datetime, timezone
    from test_market_radar_collect import world, product, job
    from tracker import market_radar_collect as mc
    rival = pg.upsert_entity("acme.com", name="Acme")
    clients = []
    for i in range(6):
        me = pg.upsert_entity("client%d.example" % i, name="Client %d" % i, country="US")
        c = pg.upsert_client(OWNER, me)
        pg.propose_competitor(c, OWNER, rival, "direct", confidence=0.8)
        clients.append(c)
    now = datetime.now(timezone.utc)
    recent = now.strftime("%Y-%m-%dT00:00:00Z")

    def one(c):
        io, _ = world([product(1, "Runner", 100, created=recent), product(2, "Old", 50)],
                      [job(1, "Barista", "Austin")], ["https://acme.com/locations/austin"])
        return mc.collect_client(c, OWNER, io=io, store=pg, now=now)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(one, clients))
    for r in results:
        rows = r["companies"][0]["rows"]
        assert all(x["status"] in ("ok", "empty", "none") for x in rows), rows
    events = pg.recent_events([rival])
    keys = [e["dedupe_key"] for e in events]
    assert len(keys) == len(set(keys)) and any("prod+" in k for k in keys)
    with pg._tx() as cur:
        cur.execute("""SELECT detector, count(DISTINCT content_hash), count(*) FROM mr_snapshots
                       WHERE entity_id=%s GROUP BY detector""", (rival,))
        for detector, distinct, rows in cur.fetchall():
            assert distinct == rows, detector
