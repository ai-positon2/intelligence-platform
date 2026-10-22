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
    assert agents == {"brandis", "brandspend", "disapproved", "copycheck", "anomaly", "geoconf", "kpi", "kvis"} and len(qs) >= 40
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
    assert [r["rows"] for r in got if r["ok"]][0] == 1


def test_the_meta_reads_are_built_for_every_agent_and_cut_to_one_row():
    reqs = qc.meta_requests("act_1234567")
    assert {a for a, _l, _p, _q in reqs} == {"geoconf", "copycheck", "kpi", "anomaly", "disapproved"}
    for agent, label, path, params in reqs:
        assert path and path.startswith("/act_1234567/") and params["limit"] == 1, (agent, label)
    geo = [r for r in reqs if r[0] == "geoconf"][0]
    assert "location_types" not in geo[3]["fields"].replace("geo_locations", "")                # Meta keeps it inside geo_locations: asking for it beside is an error


def test_a_meta_read_that_fails_is_named_and_one_that_cannot_be_built_is_too():
    def pages(path, params):
        if path.endswith("/adsets"):
            raise RuntimeError("Meta answered 400: (#100) nonexistent field")
        return [{"id": "1"}]
    got = qc.check_meta("act_1", pages_fn=pages)
    bad = [r for r in got if not r["ok"]]
    assert {r["label"] for r in bad} == {"geoconf", "kpi adsets"} and all("nonexistent field" in r["error"] for r in bad)
    odd = qc.check_meta("act_1", pages_fn=lambda p, q: [], requests=[("kpi", "campaigns", None, {"_build_error": "boom"})])
    assert odd == [{"agent": "kpi", "label": "campaigns", "ok": False, "error": "could not be built: boom", "rows": 0}]


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
    monkeypatch.setattr(qc, "check_google", lambda acct: [{"agent": "kpi", "label": "days", "ok": False, "error": "no", "rows": 0}, {"agent": "kpi", "label": "campaigns", "ok": True, "error": "", "rows": 1}])
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
