"""The live query check (tracker/paid_gaql_check.py) and what the real platform taught about the recent agents' queries. No network."""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module  # noqa: E402
from tracker import paid_gaql_check as qc  # noqa: E402

# Fields Google's API refused on the live platform (2026-10-22): a fake that accepts anything would let them back in.
REFUSED = ["campaign.start_date,", "campaign.start_date ", "campaign.start_date FROM"]


def test_every_query_of_the_recent_agents_builds_and_names_its_agent():
    qs = qc.google_queries()
    agents = {a for a, _l, _q in qs}
    assert agents == {"brandis", "brandspend", "disapproved", "copycheck", "anomaly", "geoconf", "kpi", "kvis", "track", "watch"} and len(qs) >= 40
    for agent, label, q in qs:
        assert q.startswith("SELECT ") and "%s" not in q and "%d" not in q, (agent, label)
    assert len({(a, l) for a, l, _q in qs}) == len(qs)                                       # no two queries share a name


def test_a_field_google_refused_is_in_no_query():
    for agent, label, q in qc.google_queries():
        for bad in REFUSED:
            assert bad not in q, (agent, label, bad)


def test_a_query_that_filters_on_a_status_of_an_asset_resource_selects_it():
    for agent, label, q in qc.google_queries():
        for field in ("campaign.status", "campaign_asset.status", "customer_asset.status"):
            if re.search(r"FROM (campaign_asset|customer_asset)\b", q) and re.search(r"WHERE .*\b%s\b" % re.escape(field), q):
                assert re.search(r"SELECT .*\b%s\b.* FROM" % re.escape(field), q), (agent, label, field)


def test_every_query_is_cut_to_one_row():
    assert qc._one_row("SELECT a FROM b") == "SELECT a FROM b LIMIT 1" and qc._one_row("SELECT a FROM b ORDER BY a DESC LIMIT 5000 ") == "SELECT a FROM b ORDER BY a DESC LIMIT 1"
    assert qc._one_row("SELECT a FROM b WHERE c = 'LIMIT 5'") == "SELECT a FROM b WHERE c = 'LIMIT 5' LIMIT 1"


def test_a_query_google_refuses_is_named_with_the_reason_and_the_rest_pass():
    sent = []

    def search(cid, q):
        sent.append((cid, q))
        if "campaign.start_date_time" in q:
            raise RuntimeError("Google Ads answered 400: Unrecognized field in the query: 'campaign.start_date_time'.")
        return [{"x": 1}]
    got = qc.check_google("1234567890", search_fn=search, workers=1)
    bad = [r for r in got if not r["ok"]]
    assert {(r["agent"], r["label"]) for r in bad} == {("anomaly", "campaign days"), ("kpi", "campaigns")} and all("Unrecognized field" in r["error"] for r in bad)
    assert len(got) == len(sent) and all(q.endswith(" LIMIT 1") for _c, q in sent) and all(c == "1234567890" for c, _q in sent)
    assert [r["rows"] for r in got if r["ok"]][0] == 1 and all(isinstance(r["ms"], int) and r["ms"] >= 0 for r in got)


def test_the_meta_reads_are_built_for_every_agent_and_cut_to_one_row():
    reqs = qc.meta_requests("act_1234567")
    assert {a for a, _l, _p, _q in reqs} == {"geoconf", "copycheck", "kpi", "anomaly", "disapproved", "metacreative", "metahistory", "metainterests", "metaqa", "track", "watch"}
    for agent, label, path, params in reqs:
        assert path and (path.startswith("/act_1234567/") or (agent == "metainterests" and path == "/search") or (agent == "watch" and path == "/act_1234567")) and params["limit"] == 1, (agent, label)      # Meta's interest search is not under an account, nor is the ad account's own status
    geo = [r for r in reqs if r[0] == "geoconf"][0]
    assert "location_types" not in geo[3]["fields"].replace("geo_locations", "")                # Meta keeps it inside geo_locations: asking for it beside is an error


def test_a_meta_read_that_fails_is_named_and_one_that_cannot_be_built_is_too():
    def pages(path, params):
        if path.endswith("/adsets"):
            raise RuntimeError("Meta answered 400: (#100) nonexistent field")
        return [{"id": "1"}]
    got = qc.check_meta("act_1", pages_fn=pages)
    bad = [r for r in got if not r["ok"]]
    assert {r["label"] for r in bad} == {"geoconf", "kpi adsets", "ad sets with targeting", "ad sets"} and all("nonexistent field" in r["error"] for r in bad)
    odd = qc.check_meta("act_1", pages_fn=lambda p, q: [], requests=[("kpi", "campaigns", None, {"_build_error": "boom"})])
    assert odd == [{"agent": "kpi", "label": "campaigns", "ok": False, "error": "could not be built: boom", "rows": 0, "ms": 0}]


def client_as(email):
    c = app_module.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


def test_the_route_is_for_admins_only_and_asks_for_an_account(monkeypatch):
    admin = sorted(app_module.ADMIN_EMAILS)[0]
    assert client_as(None).get("/p2/paid-marketing/api/query-check?account=1234567890").status_code == 302
    assert "ana@position2.com" not in app_module.ADMIN_EMAILS
    assert client_as("ana@position2.com").get("/p2/paid-marketing/api/query-check?account=1234567890").status_code == 403
    r = client_as(admin).get("/p2/paid-marketing/api/query-check")
    assert r.status_code == 400 and "Give ?account=" in r.get_json()["error"]
    monkeypatch.setattr(qc, "check_google", lambda acct, queries=None: [{"agent": "kpi", "label": "days", "ok": False, "error": "no", "rows": 0}, {"agent": "kpi", "label": "campaigns", "ok": True, "error": "", "rows": 1}])
    r = client_as(admin).get("/p2/paid-marketing/api/query-check?account=123-456-7890")
    j = r.get_json()
    assert r.status_code == 200 and j["google"]["account"] == "1234567890" and [f["label"] for f in j["google"]["failed"]] == ["days"] and "meta" not in j


def test_a_meta_failure_reaches_the_page_as_a_message_not_a_crash(monkeypatch):
    from monitor_fakes import MemStore
    from tracker import paid_geoconf as gc
    from tracker import paid_meta
    store = MemStore()
    store.StoreUnavailable = type("StoreUnavailable", (Exception,), {})
    monkeypatch.setattr(app_module, "_mon_store", lambda: store)

    def boom(*a, **k):
        raise paid_meta.AdsError("Meta answered 400: (#100) Tried accessing nonexisting field.")
    for module, url in ((gc, "/p2/paid-marketing/api/geoconf/report?account=act_1234567"),):
        monkeypatch.setattr(module, "report_for", boom)
        r = client_as("ana@position2.com").get(url)
        assert r.status_code == 502 and r.get_json() == {"error": "Meta answered 400: (#100) Tried accessing nonexisting field.", "kind": "meta"}


def test_the_meta_reads_run_a_few_at_a_time_and_keep_their_order_and_a_failure_in_one_does_not_stop_the_rest():
    import threading
    seen, active, peak, lock = [], [0], [0], threading.Lock()

    def pages(path, params):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        import time
        time.sleep(0.02)
        with lock:
            active[0] -= 1
            seen.append(path)
        if path.endswith("/bad"):
            raise RuntimeError("Meta answered 400: bad field")
        return [1]
    reqs = [("a", "r%d" % i, "/act_1/%s" % ("bad" if i == 3 else "ok"), {}) for i in range(8)]
    got = qc.check_meta("act_1", pages_fn=pages, requests=reqs)
    assert [r["label"] for r in got] == ["r%d" % i for i in range(8)] and [r["ok"] for r in got] == [True, True, True, False, True, True, True, True]
    assert "bad field" in got[3]["error"] and peak[0] > 1 and peak[0] <= qc.META_WORKERS


def test_the_route_can_check_one_platform_or_some_agents_so_an_answer_stays_inside_the_gateways_wait(monkeypatch):
    from tracker import paid_meta
    seen = {"g": [], "m": []}
    monkeypatch.setattr(qc, "check_google", lambda acct, queries=None, **k: seen["g"].append(sorted({q[0] for q in queries})) or [{"agent": q[0], "label": q[1], "ok": True, "rows": 1, "ms": 1, "error": ""} for q in queries[:1]])
    monkeypatch.setattr(qc, "check_meta", lambda act, pages_fn=None, requests=None, **k: seen["m"].append(sorted({r[0] for r in requests})) or [{"agent": r[0], "label": r[1], "ok": False, "rows": 0, "ms": 1, "error": "x"} for r in requests[:1]])
    monkeypatch.setattr(paid_meta, "norm_id", lambda v: v)
    cl = client_as(sorted(app_module.ADMIN_EMAILS)[0])
    base = "/p2/paid-marketing/api/query-check?account=1234567890&meta=act_1"
    assert "meta" not in cl.get(base + "&only=google").get_json() and seen["m"] == []
    assert "google" not in cl.get(base + "&only=meta").get_json() and seen["g"][-1] == seen["g"][-1] and len(seen["m"]) == 1
    r = cl.get(base + "&agents=kpi,metacreative").get_json()
    assert seen["g"][-1] == ["kpi"] and seen["m"][-1] == ["kpi", "metacreative"] and r["meta"]["failed"][0]["agent"] == "kpi"       # an agent that reads both platforms is in both halves
    assert cl.get("/p2/paid-marketing/api/query-check?account=1234567890&only=meta").status_code == 400


def test_the_route_reads_only_the_first_page_of_each_meta_request_and_never_follows_the_paging_links(monkeypatch):
    """With limit 1 every row has a next link: following them reads the whole account one row at a time, which is what made the check run for minutes."""
    from tracker import paid_meta
    gets = []

    def get(path, params, store, **k):
        gets.append((path, params.get("limit")))
        return {"data": [{"id": "1"}], "paging": {"next": "https://graph.example/next"}}
    monkeypatch.setattr(paid_meta, "_get", get)
    monkeypatch.setattr(paid_meta, "_pages", lambda *a, **k: (_ for _ in ()).throw(AssertionError("followed the paging links")))
    monkeypatch.setattr(paid_meta, "norm_id", lambda v: v)
    monkeypatch.setattr(qc, "meta_requests", lambda act: [("kpi", "insights", "/%s/insights" % act, {"limit": 1}), ("metacreative", "ads", "/%s/ads" % act, {"limit": 1})])
    r = client_as(sorted(app_module.ADMIN_EMAILS)[0]).get("/p2/paid-marketing/api/query-check?meta=act_1&only=meta").get_json()
    assert [x["ok"] for x in r["meta"]["results"]] == [True, True] and [x["rows"] for x in r["meta"]["results"]] == [1, 1] and len(gets) == 2
