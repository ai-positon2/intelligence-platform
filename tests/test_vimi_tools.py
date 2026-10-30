"""Vimi's tools: an account is found by name, an agent is read only through its catalogued reads as GET, a big result comes back as a digest to drill into, and every failure is said in words."""
import pytest

from tracker import vimi_catalog as cat
from tracker import vimi_data as vd
from tracker import vimi_tools as vt

GOOGLE = [{"id": "1234567890", "name": "Beta Bionics", "currency": "USD"}, {"id": "2222222222", "name": "Riccobene Associates", "currency": "USD"},
          {"id": "3333333333", "name": "Beta Imaging", "currency": "USD"}]
META = [{"id": "act_111222333", "name": "Beta Bionics Meta", "currency": "USD"}]
CLIENTS = [{"id": "c1", "name": "Beta Bionics", "accounts": {"1234567890": {}, "act_111222333": {}}}]


def campaigns(n=40):
    return [{"name": "C%02d" % i, "type": "SEARCH" if i % 2 else "PMAX", "cost": float(i), "conv": float(i % 3)} for i in range(n)]


class Fake:
    """A route that records its calls and answers what the test says."""

    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    def __call__(self, path, params):
        self.calls.append((path, dict(params)))
        a = self.answers.get(path, (200, {"campaigns": campaigns(), "fetchedAt": "2026-10-30T09:00:00Z"}))
        if isinstance(a, Exception):
            raise a
        return a


def tools(route=None, *, admin=False, local=None, web=None, **kw):
    return vt.Tools(route=route or Fake(), accounts=lambda: {"google": GOOGLE, "meta": META, "clients": CLIENTS}, web=web, local=local, is_admin=admin, **kw)


# ── accounts ──────────────────────────────────────────────────────────────────

def test_an_account_is_found_by_the_words_of_its_name():
    out = tools().find_account({"query": "beta bionics"})
    assert "google | 1234567890 | Beta Bionics" in out and "meta | act_111222333 | Beta Bionics Meta" in out
    assert "Beta Imaging" not in out                       # one shared word of two is not a match


def test_a_client_with_both_platforms_gets_the_joined_account_string():
    assert "account=1234567890+act_111222333" in tools().find_account({"query": "beta bionics"})


def test_one_distinctive_word_is_enough_and_a_miss_says_how_to_look_again():
    assert "2222222222" in tools().find_account({"query": "riccobene"})
    out = tools().find_account({"query": "zzz"})
    assert "No account name contains 'zzz'" in out and "no query" in out


def test_no_query_lists_every_account():
    out = tools().find_account({})
    assert "4 accounts" in out and "Riccobene Associates" in out and "CLIENTS" in out


def test_a_failed_account_list_is_said_not_hidden():
    t = vt.Tools(route=Fake(), accounts=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert "boom" in t.find_account({"query": "x"})


# ── agents ────────────────────────────────────────────────────────────────────

def test_no_argument_gives_the_roster_of_every_agent():
    out = tools().agent_info({})
    for a in cat.AGENTS:
        assert a["id"] in out


def test_an_agent_is_described_with_its_views_and_parameters_and_its_knowledge():
    t = tools(knowledge={"kpi-monitoring": "KPI text about targets."})
    out = t.agent_info({"agent": "KPI Monitoring"})
    assert "view 'report'" in out and "KPI text about targets." in out and "account:" in out


def test_an_unknown_agent_names_the_closest_ones():
    out = tools().agent_info({"agent": "impression share"})
    assert "Brand IS Monitor" in out or "brand-is" in out


def test_a_hidden_agent_is_not_listed_or_readable():
    t = tools(hidden={"kpi-monitoring"})
    assert "kpi-monitoring" not in t.agent_info({}) and "No agent" in t.read_agent({"agent": "kpi-monitoring", "account": "1234567890"})


# ── reading ───────────────────────────────────────────────────────────────────

def test_a_read_calls_the_pages_own_route_by_path_with_only_the_declared_parameters():
    route = Fake()
    out = tools(route).read_agent({"agent": "kpi-monitoring", "account": "1234567890"})
    assert route.calls == [("/p2/paid-marketing/api/kpi/report", {"account": "1234567890"})]
    assert out.startswith("[r1] KPI Monitoring (report) for Beta Bionics (1234567890)") and "campaigns: 40 rows" in out and "fetchedAt" in out


def test_the_account_may_come_in_params_and_other_params_pass_through():
    route = Fake()
    tools(route).read_agent({"agent": "performance-summarizer", "view": "performance", "params": {"account": "1234567890", "start": "2026-10-01", "end": "2026-10-28"}})
    assert route.calls[0][1] == {"account": "1234567890", "start": "2026-10-01", "end": "2026-10-28"}


def test_a_refresh_parameter_is_refused_not_passed_on():
    route = Fake()
    out = tools(route).read_agent({"agent": "kpi-monitoring", "account": "1234567890", "params": {"refresh": "1"}})
    assert route.calls == [] and "does not take refresh" in out


def test_an_undeclared_parameter_is_refused_with_the_ones_that_exist():
    out = tools().read_agent({"agent": "brand-is", "view": "report", "account": "1234567890", "params": {"nope": 1}})
    assert "does not take nope" in out and "threshold" in out


def test_an_account_read_without_an_account_or_with_a_made_up_one_is_refused():
    route = Fake()
    t = tools(route)
    assert "needs an account" in t.read_agent({"agent": "kpi-monitoring"})
    assert "not an account id" in t.read_agent({"agent": "kpi-monitoring", "account": "Beta Bionics"})
    assert "not an account id" in t.read_agent({"agent": "kpi-monitoring", "account": "../../admin"})
    assert route.calls == []


def test_both_platforms_are_one_account_string():
    route = Fake()
    tools(route).read_agent({"agent": "account-audit", "account": "1234567890+act_111222333"})
    assert route.calls[0][1] == {"account": "1234567890+act_111222333"}


def test_a_path_value_is_filled_and_quoted_so_it_cannot_climb_out_of_its_route():
    route = Fake()
    t = tools(route)
    t.read_agent({"agent": "pacing-desk", "view": "snapshot", "params": {"date": "2026-10-01"}})
    t.read_agent({"agent": "pacing-desk", "view": "snapshot", "params": {"date": "../../admin/x"}})
    assert route.calls[0] == ("/p2/paid-marketing/api/pacing/snapshots/2026-10-01", {})
    assert route.calls[1][0] == "/p2/paid-marketing/api/pacing/snapshots/..%2F..%2Fadmin%2Fx"


def test_a_missing_path_value_says_what_it_needs():
    assert "needs date" in tools().read_agent({"agent": "pacing-desk", "view": "snapshot"})


def test_an_unknown_view_lists_the_views():
    out = tools().read_agent({"agent": "kpi-monitoring", "view": "bogus", "account": "1234567890"})
    assert "no view 'bogus'" in out and "report" in out and "settings" in out


def test_an_admin_read_is_refused_for_a_person_who_is_not_one_without_calling_the_route():
    route = Fake()
    out = tools(route, admin=False).read_agent({"agent": "market-radar", "view": "clients"})
    assert "admins only" in out and route.calls == []
    assert tools(route, admin=True).read_agent({"agent": "market-radar", "view": "clients"}).startswith("[r1]")


def test_an_agent_with_nothing_to_read_points_to_its_page():
    assert "/p2/paid-marketing/client-feedback-agent" in tools().read_agent({"agent": "client-feedback-agent"})


@pytest.mark.parametrize("status,body,want", [
    (429, {"error": "Google Ads allowance spent", "blockedUntil": 1798000000}, "Do not retry"),
    (429, {"error": "cooling", "retryIn": 90, "cooldown": True}, "cooling down"),
    (409, {"error": "not connected", "configProblem": "not connected"}, "not set up"),
    (502, {"error": "Google Ads answered 500"}, "Report this to the person plainly"),
    (403, {"error": "admin"}, "admins only"),
    (404, {"error": "No snapshot"}, "nothing there"),
    (302, "", "not signed in"),
])
def test_every_failure_is_said_in_words_and_never_becomes_a_result(status, body, want):
    t = tools(Fake({"/p2/paid-marketing/api/kpi/report": (status, body)}))
    out = t.read_agent({"agent": "kpi-monitoring", "account": "1234567890"})
    assert want in out and t.results.labels() == [] and t.sources[-1]["failed"]


def test_a_route_that_raises_is_reported_as_a_failed_read():
    t = tools(Fake({"/p2/paid-marketing/api/kpi/report": TimeoutError("took too long")}))
    assert "could not be read: TimeoutError: took too long" in t.read_agent({"agent": "kpi-monitoring", "account": "1234567890"})


def test_a_200_that_carries_an_error_and_data_is_still_a_result_with_the_warning_shown():
    t = tools(Fake({"/p2/paid-marketing/api/kpi/report": (200, {"campaigns": campaigns(3), "warnings": ["Meta was unreachable"]})}))
    out = t.read_agent({"agent": "kpi-monitoring", "account": "1234567890"})
    assert out.startswith("[r1]") and "Meta was unreachable" in out


def test_a_200_that_is_only_an_error_is_a_failure():
    t = tools(Fake({"/p2/paid-marketing/api/kpi/report": (200, {"error": "bad range"})}))
    assert "failed" in t.read_agent({"agent": "kpi-monitoring", "account": "1234567890"})


def test_a_local_source_is_read_through_its_function():
    t = tools(local={"visitors": lambda p: {"visitors": [{"name": "A"}], "echo": p}})
    out = t.read_agent({"agent": "anonymous-visitors", "params": {"company": "Acme"}})
    assert out.startswith("[r1] Anonymous Website Visitors (visitors)") and "Acme" in out
    assert "not available" in tools().read_agent({"agent": "anonymous-visitors"})


# ── drilling ──────────────────────────────────────────────────────────────────

def test_inspect_result_sums_and_sorts_in_code():
    t = tools()
    t.read_agent({"agent": "kpi-monitoring", "account": "1234567890"})
    out = t.inspect_result({"id": "r1", "path": "campaigns", "group_by": "type", "aggregate": [{"field": "cost", "fn": "sum", "as": "spend"}]})
    assert '"PMAX"' in out and '"spend":380.0' in out and '"spend":400.0' in out          # even i: 0+2+...+38 = 380; odd i: 400


def test_inspect_result_errors_say_what_is_there():
    t = tools()
    t.read_agent({"agent": "kpi-monitoring", "account": "1234567890"})
    assert "has no 'campaign'" in t.inspect_result({"id": "r1", "path": "campaign"}) and "campaigns" in t.inspect_result({"id": "r1", "path": "campaign"})
    assert "no result 'r9'" in t.inspect_result({"id": "r9"})
    assert "op 'like'" in t.inspect_result({"id": "r1", "path": "campaigns", "where": [{"field": "name", "op": "like", "value": "x"}]})


# ── the web and the record of what was read ───────────────────────────────────

def test_web_research_goes_through_the_injected_search_and_is_recorded():
    t = tools(web=lambda q: "Answer for " + q)
    assert t.web_research({"question": "cpc benchmark"}) == "Answer for cpc benchmark"
    assert t.sources[-1]["agent"] == "Web search"
    assert "not available" in tools().web_research({"question": "x"}) and "Give the question" in tools(web=lambda q: "").web_research({})


def test_sources_record_every_agent_read_with_its_account_and_freshness():
    t = tools()
    t.read_agent({"agent": "kpi-monitoring", "account": "1234567890"})
    t.read_agent({"agent": "ad-strength", "account": "act_111222333"})
    assert [(s["agent"], s["account"]) for s in t.sources] == [("KPI Monitoring", "Beta Bionics (1234567890)"), ("Ad Strength Analysis", "Beta Bionics Meta (act_111222333)")]
    assert t.sources[0]["fetchedAt"] == "2026-10-30T09:00:00Z"


def test_step_labels_say_what_is_being_done():
    t = tools()
    assert vt.step_label("read_agent", {"agent": "kpi-monitoring", "account": "1234567890"}, t) == "Reading KPI Monitoring for Beta Bionics (1234567890)"
    assert vt.step_label("find_account", {"query": "beta"}) == 'Finding the account "beta"'
    assert vt.step_label("inspect_result", {"id": "r3"}) == "Looking closer at r3"


def test_every_tool_spec_has_a_method_and_a_schema():
    for spec in vt.TOOL_SPECS:
        assert callable(getattr(vt.Tools, spec["name"])) and spec["input_schema"]["type"] == "object" and len(spec["description"]) > 80


def test_empty_parameters_are_dropped_not_sent_as_blank_values():
    route = Fake()
    tools(route).read_agent({"agent": "performance-summarizer", "view": "performance", "params": {"account": "1234567890", "start": "2026-10-01", "end": "", "compare_start": None}})
    assert route.calls[0][1] == {"account": "1234567890", "start": "2026-10-01"}
