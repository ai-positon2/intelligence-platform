"""Market Radar, Phase 6: the signal engine, no network and no model.

Headlines are shaped like the live ones read on 2026-10-10 (Aspen Dental's
openings, Lululemon's C-suite overhaul reported 13 ways, Hoka's reviews and
deal posts, crime at a Planet Fitness).
"""
import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import market_radar_signals as S  # noqa: E402

NOW = datetime(2026, 10, 10, 9, 0, tzinfo=timezone.utc)
ASPEN = {"id": 1, "domain": "aspendental.com", "name": "Aspen Dental", "country": "US"}


def art(i, title, day="2026-10-08", pub="PR Newswire", copies=1):
    return {"id": "a%d" % i, "title": title, "date": day, "publisher": pub,
            "link": "https://news.google.com/rss/articles/%d" % i, "copies": copies}


class Store:
    """The parts of market_radar_store the engine uses, with the same
    merge rule for events (one per company and key; evidence counts
    detectors)."""

    def __init__(self):
        self.events, self.snaps, self.links, self.clients, self.comps = {}, {}, {}, {}, {}
        self.first = {}
        self.next_id = 1

    def latest_snapshot(self, entity_id, detector):
        rows = self.snaps.get((entity_id, detector))
        return {"payload": rows[-1], "item_count": None} if rows else None

    def save_snapshot(self, entity_id, detector, payload, *, item_count=None, run_id=None):
        self.snaps.setdefault((entity_id, detector), []).append(payload)

    def first_snapshot(self, entity_id, detector):
        return self.first.get((entity_id, detector))

    def recent_events(self, ids, *, days=120, limit=400):
        return sorted([dict(e) for e in self.events.values() if e["entity_id"] in ids],
                      key=lambda e: e["id"], reverse=True)

    def record_event(self, entity_id, key, *, type, title, source, status="unknown",
                     event_date=None, summary=None, location=None):
        k = (entity_id, key)
        if k not in self.events:
            self.events[k] = {"id": self.next_id, "entity_id": entity_id, "dedupe_key": key,
                              "type": type, "title": title, "status": status,
                              "event_date": event_date, "summary": summary, "location": location,
                              "sources": [source], "evidence_count": 1, "first_seen_at": NOW,
                              "hidden_reason": None}
            self.next_id += 1
            return self.events[k]["id"], True
        e = self.events[k]
        if not any(s["url"] == source["url"] for s in e["sources"]):
            e["sources"].append(source)
        e["evidence_count"] = len({s["detector"] for s in e["sources"]})
        e["status"] = e["status"] if status == "unknown" else status
        return e["id"], False

    def hide_event(self, event_id, reason):
        for e in self.events.values():
            if e["id"] == event_id:
                e["hidden_reason"] = reason

    def get_client(self, client_id, owner):
        return self.clients.get(client_id)

    def competitors(self, client_id, owner, include_removed=False):
        return self.comps.get(client_id, [])

    def get_entity(self, entity_id):
        return {}

    def client_scores(self, client_id):
        return {eid: v for (cid, eid), v in self.links.items() if cid == client_id}

    def link_client_events(self, client_id, rows, *, run_id=None):
        for eid, score, sev, km in rows:
            old = self.links.get((client_id, eid), {})
            self.links[(client_id, eid)] = {"score": score, "severity": sev, "distance_km": km,
                                            "feedback": old.get("feedback")}


class LLM:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def call_json(self, system, user, schema, *, model, max_tokens, run_id=None, stage, client=None):
        self.calls.append({"user": user, "stage": stage, "max_tokens": max_tokens})
        if isinstance(self.answer, Exception):
            raise self.answer
        return (self.answer(user) if callable(self.answer) else self.answer), {}


class ModelError(Exception):
    kind = "truncated"


def ev(items, type="new_location", existing="", status="opened", title="Aspen Dental opens in Whiteville",
       place="", day=""):
    return {"existing": existing, "type": type, "status": status, "title": title, "place": place,
            "date": day, "items": items}


# == which headlines are read ============================================================

def test_only_new_headlines_from_the_window_are_read_newest_first_and_capped(monkeypatch):
    items = [art(1, "old", day="2026-06-01"), art(2, "seen"), art(3, "new", day="2026-10-09"),
             art(4, "undated", day=None)]
    posts = [{"id": 7, "title": "Our charity day", "event_date": date(2026, 10, 1), "sources": [
        {"url": "https://aspendental.com/blog/charity"}], "hidden_reason": None},
             {"id": 8, "title": "hidden", "event_date": None, "sources": [], "hidden_reason": "x"}]
    got, later = S.items_to_read(items, posts, {"a2": "news:a2"}, NOW)
    assert [g["id"] for g in got] == ["a3", "post:7", "a4"] and later == 0
    assert got[1]["detector"] == "newsroom" and got[1]["event_id"] == 7
    monkeypatch.setattr(S, "MAX_ITEMS", 2)
    got, later = S.items_to_read(items, posts, {}, NOW)
    assert len(got) == 2 and later == 2


# == the model's answer is checked =========================================================

ITEMS = [dict(art(i, "Headline %d" % i), detector="news") for i in range(6)]
EXISTING = [{"key": "locations:loc+:/dentist/nc/whiteville", "type": "new_location",
             "title": "New location page: Whiteville, NC", "date": "2026-10-09"},
            {"key": "catalog:prod+:x", "type": "product_launch", "title": "New product: X", "date": None},
            {"key": "jobs:place+:selma", "type": "new_job_location", "title": "Hiring in Selma", "date": None}]


def test_events_use_real_headlines_once_and_join_only_compatible_existing_events():
    answer = {"events": [
        ev([0, 1, 1, 99], existing="E1", title="Aspen Dental opens — Whiteville", place="Whiteville, NC"),
        ev([1, 2], type="acquisition", existing="E2", title="Aspen buys X"),     # 1 already used
        ev([3], existing="E3", title="Aspen opens in Selma", day="2027-01-15"),
        ev([], type="funding"),
        ev([4], type="nonsense")],
        "left_out": [{"item": 5, "reason": "review_or_deal"}, {"item": 0, "reason": "other"}]}
    got = S.triage(ASPEN, ITEMS, EXISTING, now=NOW, llm=LLM(answer))
    e = got["events"]
    assert [x["existing"] for x in e] == ["locations:loc+:/dentist/nc/whiteville", None,
                                          "jobs:place+:selma"]
    assert [len(x["items"]) for x in e] == [2, 1, 1]
    assert e[0]["title"] == "Aspen Dental opens, Whiteville" and e[0]["date"] == "2026-10-08"
    assert e[2]["date"] == "2027-01-15"
    assert [(l["item"]["id"], l["reason"]) for l in got["left_out"]] == [("a5", "review_or_deal")]
    assert got["unanswered"] == 1 and "1 headlines got no verdict" in got["note"]
    assert "2 events were malformed" in got["note"]


def test_a_date_far_from_now_is_not_believed():
    got = S.triage(ASPEN, ITEMS[:1], [], now=NOW, llm=LLM({"events": [ev([0], day="1999-01-01")],
                                                           "left_out": []}))
    assert got["events"][0]["date"] == "2026-10-08"


def test_a_failed_read_is_none_and_names_why():
    got = S.triage(ASPEN, ITEMS, [], now=NOW, llm=LLM(ModelError("x")))
    assert got["events"] is None and "truncated" in got["note"] and got["unanswered"] == 6


def test_the_prompt_lists_the_company_its_open_events_and_numbered_headlines():
    llm = LLM({"events": [], "left_out": []})
    S.triage(dict(ASPEN, sells="dental care"), [dict(ITEMS[0], copies=3)], EXISTING, now=NOW, llm=llm)
    u = llm.calls[0]["user"]
    assert "COMPANY: Aspen Dental (aspendental.com), US. What it sells: dental care" in u
    assert "E1 | 2026-10-09 | new_location | New location page: Whiteville, NC" in u
    assert "0 | 2026-10-08 | PR Newswire (3 outlets) | Headline 0" in u
    assert llm.calls[0]["stage"] == "signals_triage" and "—" not in S._system()


# == one company ========================================================================

def lulu_world():
    store = Store()
    heads = [art(i, "Lululemon C-suite shake-up, take %d" % i, pub="Outlet %d" % i) for i in range(13)]
    heads += [art(20, "I tested the Lululemon Align leggings", pub="Tom's Guide"),
              art(21, "Lululemon stock: has it lost its mojo?", pub="Yahoo Finance")]
    store.snaps[(2, "news")] = [{"items": heads}]
    store.record_event(2, "newsroom:post:p1", type="announcement", title="Our run club turns 10",
                       source={"url": "https://lululemon.com/blog/run", "detector": "newsroom"},
                       event_date="2026-10-02")
    store.record_event(2, "newsroom:post:p2", type="announcement", title="New Glow collection",
                       source={"url": "https://lululemon.com/blog/glow", "detector": "newsroom"},
                       event_date="2026-10-03")
    return store


def lulu_answer(user):
    lines = [l for l in user.split("HEADLINES:\n")[1].splitlines() if l.strip()]
    num = {l.split(" | ")[-1]: int(l.split(" | ")[0]) for l in lines}
    shake = [num["Lululemon C-suite shake-up, take %d" % i] for i in range(13)]
    return {"events": [
        ev(shake, type="leadership_change", status="completed", title="Lululemon overhauls its C-suite"),
        ev([num["New Glow collection"]], type="product_launch", status="announced",
           title="Lululemon launches its Glow collection")],
        "left_out": [{"item": num["I tested the Lululemon Align leggings"], "reason": "review_or_deal"},
                     {"item": num["Lululemon stock: has it lost its mojo?"], "reason": "stock_chatter"},
                     {"item": num["Our run club turns 10"], "reason": "community"}]}


def test_thirteen_headlines_of_one_move_are_one_event_and_posts_are_folded_or_left_out():
    store = lulu_world()
    llm = LLM(lulu_answer)
    row = S.triage_company({"id": 2, "domain": "lululemon.com", "name": "Lululemon"}, store=store,
                           now=NOW, llm=llm)
    assert row["status"] == "ok" and row["read"] == 17 and row["events"] == 2 and row["left_out"] == 3
    typed = [e for e in store.events.values() if e["type"] != "announcement"]
    shake = next(e for e in typed if e["type"] == "leadership_change")
    assert len(shake["sources"]) == 13 and shake["evidence_count"] == 1 and shake["status"] == "completed"
    assert shake["dedupe_key"].startswith("news:a")
    glow = next(e for e in typed if e["type"] == "product_launch")
    assert glow["dedupe_key"] == "post-typed:" + str(store.events[(2, "newsroom:post:p2")]["id"])
    posts = {e["title"]: e["hidden_reason"] for e in store.events.values() if e["type"] == "announcement"}
    assert posts["New Glow collection"].startswith("folded into: Lululemon launches")
    assert posts["Our run club turns 10"].startswith("not a move: charity")
    memo = store.snaps[(2, "signals")][-1]["triaged"]
    assert memo["a20"] == "left_out:review_or_deal" and len(memo) == 17

    # The next collection has nothing new to read and asks the model nothing.
    row = S.triage_company({"id": 2, "domain": "lululemon.com"}, store=store, now=NOW, llm=llm)
    assert row["note"] == "no new headlines" and len(llm.calls) == 1


def test_a_headline_joins_the_location_page_event_and_the_place_was_already_served():
    store = Store()
    store.snaps[(1, "news")] = [{"items": [art(1, "Aspen Dental Opens New Practice in Merced", day="2026-10-01")]}]
    store.record_event(1, "locations:loc+:/dentist/ca/merced", type="new_location",
                       title="New location page: Merced, CA",
                       source={"url": "https://aspendental.com/dentist/ca/merced", "detector": "locations"})
    store.first[(1, "locations")] = {"payload": {"places": ["/dentist/ca/merced", "/dentist/ca/fresno"]},
                                     "first_seen_at": datetime(2024, 5, 2, tzinfo=timezone.utc)}
    llm = LLM({"events": [ev([0], existing="E1", place="Merced, CA", title="Aspen Dental opens in Merced")],
               "left_out": []})
    S.triage_company(ASPEN, store=store, now=NOW, llm=llm)
    (e,) = store.events.values()
    assert e["evidence_count"] == 2 and [s["detector"] for s in e["sources"]] == ["locations", "news"]


def test_prior_presence_needs_the_page_listed_well_before_the_news():
    store = Store()
    store.first[(1, "locations")] = {"payload": {"places": ["/dentist/ca/merced"]},
                                     "first_seen_at": datetime(2024, 5, 2, tzinfo=timezone.utc)}
    note = S.prior_presence("Merced, CA", 1, "2026-10-01", store=store)
    assert "already listed 1 Merced location page on 2024-05-02" in note and "not a new market" in note
    assert S.prior_presence("Selma, NC", 1, "2026-10-01", store=store) is None
    store.first[(1, "locations")]["first_seen_at"] = datetime(2026, 9, 25, tzinfo=timezone.utc)
    assert S.prior_presence("Merced, CA", 1, "2026-10-01", store=store) is None
    assert S.prior_presence("", 1, "2026-10-01", store=store) is None


def test_a_company_whose_read_failed_keeps_its_headlines_for_next_time():
    store = lulu_world()
    row = S.triage_company({"id": 2, "domain": "lululemon.com"}, store=store, now=NOW,
                           llm=LLM(ModelError("x")))
    assert row["status"] == "failed" and (2, "signals") not in store.snaps
    assert not any(e["hidden_reason"] for e in store.events.values())


# == scoring =============================================================================

def event(i, type, *, entity=10, day="2026-10-08", evidence=1, sources=None, location=None, hidden=None):
    return {"id": i, "entity_id": entity, "type": type, "event_date": date.fromisoformat(day) if day else None,
            "first_seen_at": NOW, "evidence_count": evidence, "sources": sources or [],
            "location": location, "hidden_reason": hidden}


def by_id(rows):
    return {r["id"]: r for r in rows}


def test_an_acquisition_outranks_a_review_and_old_news_fades():
    rows, _ = S.score_events([event(1, "acquisition"), event(2, "review_growth"),
                              event(3, "acquisition", entity=11, day="2026-06-10")],
                             tiers={}, now=NOW)
    r = by_id(rows)
    assert r[1]["score"] > r[3]["score"] > r[2]["score"]
    assert r[1]["severity"] == "HIGH" and r[2]["severity"] == "LOW"


def test_distance_tier_evidence_and_feedback_move_the_score():
    point = {"lat": 30.27, "lon": -97.74}
    near = event(1, "nearby_opening", location={"lat": 30.28, "lon": -97.75, "distance_km": 1.2})
    far = event(2, "nearby_opening", entity=11, location={"lat": 40.7, "lon": -74.0})
    rows, _ = S.score_events([near, far], tiers={}, now=NOW, point=point)
    assert by_id(rows)[1]["km"] == 1.2 and by_id(rows)[2]["km"] > 2000
    assert by_id(rows)[1]["score"] > 2 * by_id(rows)[2]["score"]
    rows, _ = S.score_events([event(1, "funding"), event(2, "funding", entity=11)],
                             tiers={10: ("direct", "confirmed"), 11: ("aspirational", "proposed")}, now=NOW)
    assert by_id(rows)[1]["score"] == pytest.approx(by_id(rows)[2]["score"] / (0.5 * 0.85))
    many = [{"url": "u%d" % i, "detector": "news"} for i in range(8)]
    rows, _ = S.score_events([event(1, "funding", evidence=2, sources=many + [{"url": "x", "detector": "jobs"}]),
                              event(2, "funding", entity=11)], tiers={}, now=NOW)
    assert by_id(rows)[1]["score"] == pytest.approx(by_id(rows)[2]["score"] * 1.55)
    rows, _ = S.score_events([event(1, "promotion")], tiers={}, now=NOW, feedback={"promotion": (0, 2)})
    assert rows[0]["score"] == pytest.approx(S.WEIGHTS["promotion"] * S.recency(date(2026, 10, 8), NOW) * 0.6)


def test_a_place_named_in_the_news_is_located_only_for_local_clients_and_only_so_often(monkeypatch):
    calls = []

    def geo(q):
        calls.append(q)
        return {"lat": 30.3, "lon": -97.7}
    evs = [event(i, "new_location", entity=10 + i, location={"label": "Round Rock, TX"}) for i in range(4)]
    point = {"lat": 30.27, "lon": -97.74}
    rows, _ = S.score_events(evs, tiers={}, now=NOW, point=point, local=False, geocode=geo)
    assert calls == [] and all(r["km"] is None for r in rows)
    monkeypatch.setattr(S, "GEOCODE_MAX", 2)
    rows, _ = S.score_events(evs, tiers={}, now=NOW, point=point, local=True, geocode=geo)
    assert len(calls) == 2 and sum(1 for r in rows if r["km"] is not None) == 2


def test_one_companys_hundred_launches_count_less_each_and_high_is_kept_rare():
    launches = [event(i, "product_launch") for i in range(100)]
    rows, _ = S.score_events(launches, tiers={}, now=NOW)
    scores = sorted((r["score"] for r in rows), reverse=True)
    assert scores[1] == pytest.approx(scores[0] * 0.7) and scores[10] < 0.2
    many = [event(i, "acquisition", entity=i) for i in range(40)]
    rows, demoted = S.score_events(many, tiers={}, now=NOW)
    assert sum(1 for r in rows if r["severity"] == "HIGH") == 6 and demoted == 34
    rows, demoted = S.score_events(many[:2], tiers={}, now=NOW)
    assert demoted == 0 and all(r["severity"] == "HIGH" for r in rows)


def test_hidden_events_are_not_scored():
    rows, _ = S.score_events([event(1, "announcement", hidden="not a move")], tiers={}, now=NOW)
    assert rows == []


def test_a_client_scores_its_competitors_and_only_the_radars_finds_on_itself(monkeypatch):
    from tracker import market_radar_views as views
    monkeypatch.setattr(views, "effective_profile", lambda p, s: p)
    store = Store()
    store.clients[1] = {"entity_id": 99, "profile": {"archetype": "ecommerce"}, "settings": {}}
    store.comps[1] = [{"entity_id": 10, "kind": "direct", "status": "confirmed", "domain": "a.example"}]
    a, _ = store.record_event(10, "k1", type="funding", title="t", source={"url": "u", "detector": "news"})
    b, _ = store.record_event(99, "k2", type="new_entrant", title="t", source={"url": "u", "detector": "radar"})
    store.record_event(99, "k3", type="page_changed", title="t", source={"url": "u", "detector": "pages"})
    store.record_event(10, "k4", type="promotion", title="t", source={"url": "u", "detector": "promotions"})
    store.links[(1, 4)] = {"feedback": "down"}
    out = S.score_client(1, "o", store=store, now=NOW)
    assert out["scored"] == 3 and sorted(eid for (_, eid) in store.links) == [a, b, 4]
    assert store.links[(1, 4)]["feedback"] == "down"


def test_signals_that_break_do_not_lose_the_collection():
    from tracker import market_radar_run as mrun
    import tracker.market_radar_store as real
    saved = {}
    orig = real.update_run
    real.update_run = lambda run_id, **kw: saved.update(kw)
    try:
        def boom(*a, **k):
            raise RuntimeError("model down")
        mrun.collect_job(1, 2, "o", collect=lambda *a, **k: {"companies": []}, report=lambda *a, **k: {},
                         radar=lambda *a, **k: {}, pulse=lambda *a, **k: {}, signals=boom)
    finally:
        real.update_run = orig
    assert saved["status"] == "complete" and "model down" in saved["summary"]["signals"]["error"]


def test_a_catalog_flood_does_not_push_a_new_branch_out_of_the_models_context(monkeypatch):
    monkeypatch.setattr(S, "CONTEXT_EVENTS", 5)
    store = Store()
    store.record_event(1, "locations:loc+:/dentist/ca/merced", type="new_location",
                       title="New location page: Merced, CA", source={"url": "u", "detector": "locations"})
    for i in range(20):
        store.record_event(1, "catalog:prod+:%d" % i, type="product_launch", title="New product %d" % i,
                           source={"url": "p%d" % i, "detector": "catalog"})
    store.snaps[(1, "news")] = [{"items": [art(1, "Aspen Dental opens in Merced")]}]
    llm = LLM({"events": [], "left_out": []})
    S.triage_company(ASPEN, store=store, now=NOW, llm=llm)
    assert "New location page: Merced, CA" in llm.calls[0]["user"]


def test_a_left_out_verdict_cannot_overrule_an_event():
    got = S.triage(ASPEN, ITEMS[:2], [], now=NOW, llm=LLM({
        "events": [ev([0])], "left_out": [{"item": 0, "reason": "other"}, {"item": 1, "reason": "bogus"}]}))
    assert len(got["events"]) == 1 and [(l["item"]["id"], l["reason"]) for l in got["left_out"]] == [("a1", "other")]


def test_the_rules_learnt_from_the_first_live_reads_are_in_the_prompt():
    sysp = S._system()
    for rule in ("Alo Drink", "Spur Intelligence", "Exhibiting at a trade show", "acquisition only when ownership changes", "leadership_change only for",
                 "partnership only when a partnership begins", "plans to open", "marketing_campaign",
                 "discount-code", "earnings date"):
        assert rule in sysp, rule


def test_a_chain_is_scored_without_distance_and_a_single_site_with_it(monkeypatch):
    from tracker import market_radar_views as views
    monkeypatch.setattr(views, "effective_profile", lambda p, s: p)
    point = {"lat": 26.36, "lon": -80.08}
    for archetype, expect_km in (("multi_location", None), ("local_single", True)):
        store = Store()
        store.clients[1] = {"entity_id": 99, "profile": {"archetype": archetype, "hq_point": point},
                            "settings": {}}
        store.comps[1] = [{"entity_id": 10, "kind": "direct", "status": "confirmed"}]
        store.record_event(10, "k", type="new_location", title="t", source={"url": "u", "detector": "news"},
                           location={"label": "Four Corners, FL"})
        S.score_client(1, "o", store=store, now=NOW, geocode=lambda q: {"lat": 28.33, "lon": -81.64})
        km = store.links[(1, 1)]["distance_km"]
        assert (km is None) if expect_km is None else (km > 100)
        score = store.links[(1, 1)]["score"]
        # A chain's view of one more rival branch is routine; a single site
        # weighs it by distance (here 250 km: 0.8).
        expected = 8 * S.recency(NOW.date(), NOW) * (0.6 if expect_km is None else 0.8)
        assert score == pytest.approx(expected, abs=0.01)


def test_a_rumour_counts_less_than_a_done_deal():
    rows, _ = S.score_events([dict(event(1, "acquisition"), status="rumored"),
                              dict(event(2, "acquisition", entity=11), status="completed")], tiers={}, now=NOW)
    assert by_id(rows)[1]["score"] == pytest.approx(by_id(rows)[2]["score"] * 0.6)


def test_for_a_chain_one_more_rival_branch_is_routine():
    evs = [event(1, "new_location"), event(2, "funding", entity=11)]
    single, _ = S.score_events(evs, tiers={}, now=NOW)
    chain, _ = S.score_events(evs, tiers={}, now=NOW, chain=True)
    assert by_id(chain)[1]["score"] == pytest.approx(by_id(single)[1]["score"] * 0.6)
    assert by_id(chain)[2]["score"] == pytest.approx(by_id(single)[2]["score"])


def test_a_move_dated_ahead_is_as_fresh_as_its_news_not_fresher():
    ahead = event(1, "new_location", day="2026-10-17",
                  sources=[{"url": "u", "detector": "news", "date": "2026-09-20"}])
    same_news = event(2, "new_location", entity=11, day="2026-09-20")
    rows, _ = S.score_events([ahead, same_news], tiers={}, now=NOW)
    assert by_id(rows)[1]["score"] == pytest.approx(by_id(rows)[2]["score"])
    # With no dated source, the day it was first seen.
    undated = event(3, "new_location", entity=12, day="2026-12-01")
    assert S.news_day(undated) == NOW.date()
    assert S.news_day(event(4, "funding", day=None)) == NOW.date()
