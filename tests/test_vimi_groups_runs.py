"""Vimi can name a whole MCC where an agent takes one, and finds a person's own saved runs of the run-based agents (which are otherwise readable only by an id nobody has)."""
import app as appmod
from tracker import vimi_catalog as cat
from tracker import vimi_tools as vt

GOOGLE = [{"id": "1234567890", "name": "OIA Orlando", "currency": "USD"}]
GROUPS = [{"id": "mcc-9923720096-USD", "name": "OIA (USD)", "currency": "USD", "accounts": 7}]


class Fake:
    def __init__(self):
        self.calls = []

    def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        return 200, {"rows": [{"a": 1}]}


def tools(route=None):
    return vt.Tools(route=route or Fake(), accounts=lambda: {"google": GOOGLE, "meta": [], "clients": [], "groups": GROUPS})


# ── MCC groups ────────────────────────────────────────────────────────────────

def test_a_group_id_is_an_account_string_for_a_read_that_takes_one():
    route = Fake()
    t = tools(route)
    t.read_agent({"agent": "segment-analysis", "view": "report", "account": "mcc-9923720096-USD", "params": {"from": "2026-10-01", "to": "2026-10-28"}})
    t.read_agent({"agent": "search-query-analysis", "account": "mcc-9923720096", "params": {"start": "2026-10-01", "end": "2026-10-28"}})
    assert [c[1]["account"] for c in route.calls] == ["mcc-9923720096-USD", "mcc-9923720096"]


def test_a_malformed_group_id_is_refused():
    route = Fake()
    out = tools(route).read_agent({"agent": "segment-analysis", "account": "mcc-12"})
    assert "not an account id" in out and route.calls == []


def test_find_account_lists_the_mccs_and_says_which_agents_take_them():
    out = tools().find_account({"query": "oia"})
    assert "mcc | mcc-9923720096-USD | OIA (USD)" in out and "google | 1234567890 | OIA Orlando" in out
    assert "whole MCC" in out
    assert "mcc | mcc-9923720096-USD" in tools().find_account({})


def test_a_group_has_a_readable_label():
    assert tools().label_for("mcc-9923720096-USD") == "OIA (USD) (mcc-9923720096-USD)"


def test_the_account_list_includes_the_mccs_from_the_segment_pages_own_list():
    def call(path, params):
        if path.endswith("/gads/accounts"):
            return 200, {"accounts": [{"id": "1234567890", "name": "OIA Orlando", "currency": "USD", "platform": "google"}]}
        if path.endswith("/segments/accounts"):
            return 200, {"accounts": [], "groups": [{"id": "mcc-9923720096-USD", "name": "OIA (USD)", "currency": "USD", "accountIds": ["1234567890", "2"]}]}
        return 200, {"clients": []}
    out = appmod._vimi_accounts(call)
    assert out["groups"] == [{"id": "mcc-9923720096-USD", "name": "OIA (USD)", "currency": "USD", "accounts": 2}]


def test_a_failing_group_list_does_not_hide_the_accounts():
    def call(path, params):
        if path.endswith("/segments/accounts"):
            return 503, {"error": "not configured"}
        if path.endswith("/gads/accounts"):
            return 200, {"accounts": [{"id": "1234567890", "name": "A", "platform": "google"}]}
        return 200, {"clients": []}
    out = appmod._vimi_accounts(call)
    assert out["google"][0]["id"] == "1234567890" and out["groups"] == []


# ── a person's own saved runs ─────────────────────────────────────────────────

def test_every_local_reader_a_catalog_read_names_exists():
    names = {r["local"] for a in cat.AGENTS for r in a["reads"] if r["local"]}
    assert names <= set(appmod._vimi_locals("a@position2.com")), names - set(appmod._vimi_locals("a@position2.com"))


def test_the_run_based_agents_can_list_their_runs_then_read_one():
    for agent_id in ("social-media-intelligence", "event-conference-intelligence", "thought-leader-pr"):
        views = {r["view"]: r for r in cat.BY_ID[agent_id]["reads"]}
        assert views["runs"]["local"] and "run" in views and "{run_id}" in views["run"]["path"], agent_id


def test_a_runs_reader_lists_only_the_askers_runs_without_the_result_blobs(monkeypatch):
    from tracker import sci_store
    seen = {}

    def list_runs(email, limit):
        seen.update(email=email, limit=limit)
        return [{"id": 7, "email": email, "company_name": "Acme", "company_logo": "http://x/l.png", "status": "done", "identify_result": {"big": "x" * 5000},
                 "synthesis": {"report": "y" * 9000}, "created_at": "2026-10-29T10:00:00", "error": None, "note": "n" * 500}]
    monkeypatch.setattr(sci_store, "list_runs", list_runs)
    out = appmod._vimi_locals("ana@position2.com")["sci_runs"]({"limit": "5"})
    assert seen == {"email": "ana@position2.com", "limit": 5}
    assert out["runs"] == [{"id": 7, "company_name": "Acme", "status": "done", "created_at": "2026-10-29T10:00:00", "error": None, "note": "n" * 200}]


def test_without_an_email_there_are_no_runs_and_no_store_is_asked(monkeypatch):
    from tracker import event_intel_store
    monkeypatch.setattr(event_intel_store, "list_runs", lambda *a: (_ for _ in ()).throw(AssertionError("asked the store with no person")))
    out = appmod._vimi_locals("")["event_runs"]({})
    assert out["runs"] == [] and "No saved runs" in out["note"]
