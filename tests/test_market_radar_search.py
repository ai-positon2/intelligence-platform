"""Market Radar's Apify search (tracker/market_radar_search.py) and the
confirm-gated admin route that runs one real search. No network: a fake
Apify stands in, so what is tested is the bookkeeping. Every way a search can
end must leave the ledger right: charged as Apify reported, zero only when
Apify refused the run, and otherwise counted at its booking.
"""

import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests  # noqa: E402

from tracker import market_radar_search as mrs  # noqa: E402
from tests.test_market_radar_store import OWNER, pg, world  # noqa: E402,F401  (fixtures)

PAGE = {
    "searchQuery": {"term": "dentist Austin TX", "page": 1},
    "organicResults": [
        {"position": 1, "title": "Best Dentist", "url": "https://www.smiles.example/austin",
         "description": "Family dentistry"},
        {"position": 2, "title": "Dental Care", "url": "https://care.example/", "description": None},
    ],
    "relatedQueries": [{"title": "cheap dentist"}],
}


class FakeApify:
    def __init__(self, *, start_error=None, statuses=("RUNNING", "SUCCEEDED"), charge=0.00255,
                 items=(PAGE,), run_error=None):
        self.start_error, self.statuses, self.charge = start_error, list(statuses), charge
        self.items_, self.run_error = list(items), run_error
        self.started_with, self.aborted, self.reads = None, False, 0

    def start(self, actor, run_input, params):
        if self.start_error:
            raise self.start_error
        self.started_with = (actor, run_input, params)
        return {"id": "run1"}

    def run(self, run_id):
        self.reads += 1
        if self.run_error and self.reads > 1:
            raise self.run_error
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        data = {"id": run_id, "status": status, "defaultDatasetId": "ds1"}
        if self.charge is not None:
            data["usageTotalUsd"] = self.charge
            data["chargedEventCounts"] = {"serp-page": 1, "actor-start": 4}
        return data

    def items(self, dataset_id):
        return self.items_

    def abort(self, run_id):
        self.aborted = True


def go(api, **kw):
    kw.setdefault("token", "t")
    return mrs.search(["dentist Austin TX"], api=api, sleep=lambda s: None, **kw)


# -- input and parsing ---------------------------------------------------------

def test_input_turns_off_html_saving_and_every_paid_add_on():
    run_input = mrs.build_input(["a", "b"], country="gb", search_language="en")
    assert run_input["queries"] == "a\nb" and run_input["maxPagesPerQuery"] == 1
    assert run_input["saveHtmlToKeyValueStore"] is False      # the actor defaults to True
    assert run_input["saveHtml"] is False and run_input["focusOnPaidAds"] is False
    assert run_input["maximumLeadsEnrichmentRecords"] == 0
    assert run_input["countryCode"] == "gb" and run_input["searchLanguage"] == "en"
    assert "searchLanguage" not in mrs.build_input(["a"])


@pytest.mark.parametrize("queries", [[], ["", "  "], ["x"] * 51, [" ".join(["w"] * 33)]])
def test_bad_query_lists_are_refused_before_anything_is_spent(queries):
    api = FakeApify()
    with pytest.raises(ValueError):
        mrs.search(queries, token="t", api=api, sleep=lambda s: None)
    assert api.started_with is None


def test_organic_results_are_flattened_with_their_domain():
    rows = mrs.organic_results([PAGE, "junk", {"searchQuery": {"term": "q"}}])
    assert [(r["position"], r["domain"]) for r in rows] == [(1, "smiles.example"), (2, "care.example")]
    assert rows[0]["query"] == "dentist Austin TX" and rows[0]["snippet"] == "Family dentistry"


# -- search outcomes, no ledger ------------------------------------------------

def test_a_successful_search_reports_results_charge_and_page_fields():
    api = FakeApify()
    out = go(api)
    assert out["status"] == "SUCCEEDED" and out["error"] is None
    assert out["pages"] == 1 and len(out["results"]) == 2
    assert out["charged_usd"] == 0.00255
    assert out["fields_seen"] == ["organicResults", "relatedQueries", "searchQuery"]
    actor, _, params = api.started_with
    assert actor == mrs.ACTOR
    assert params["maxTotalChargeUsd"] == "0.50"          # never below Apify's minimum
    assert out["booked_usd"] == 0.0047                     # 1 page at $0.0045 + start events


def test_apify_cap_is_never_below_the_actors_minimum_or_the_booking():
    api = FakeApify()
    mrs.search(["q"], token="t", api=api, sleep=lambda s: None, apify_cap_usd="0.05")
    assert api.started_with[2]["maxTotalChargeUsd"] == "0.50"
    api = FakeApify()
    mrs.search(["q"] * 50, token="t", api=api, sleep=lambda s: None, apify_cap_usd="1.00")
    assert api.started_with[2]["maxTotalChargeUsd"] == "1.00"
    assert mrs.booking_usd(50) == mrs.Decimal("0.2252")


def test_a_refused_start_keeps_apifys_explanation(monkeypatch):
    class Resp:
        status_code, text = 400, '{"error":{"type":"invalid-input","message":"maxTotalChargeUsd too low"}}'
    monkeypatch.setattr(mrs.requests, "post", lambda *a, **k: Resp())
    out = mrs.search(["q"], token="t", sleep=lambda s: None)
    assert out["start"] == "refused"
    assert "maxTotalChargeUsd too low" in out["error"] and "HTTP 400" in out["error"]


def test_a_run_that_never_finishes_is_aborted_and_its_charge_still_read():
    api = FakeApify(statuses=("RUNNING",))
    out = mrs.search(["q"], token="t", api=api, sleep=lambda s: None, timeout=-31)
    assert api.aborted and "did not finish" in out["error"]
    assert out["charged_usd"] == 0.00255


def test_a_failed_run_is_an_error_even_with_pages():
    out = go(FakeApify(statuses=("FAILED",)))
    assert out["error"] == "Apify run ended FAILED" and out["pages"] == 1


def test_a_missing_token_is_refused():
    with pytest.raises(mrs.SearchError):
        mrs.search(["q"], token="", api=FakeApify(), sleep=lambda s: None)


# -- the ledger, against a real Postgres -----------------------------------------

from tracker import market_radar_ledger as ledger  # noqa: E402


def test_the_charge_apify_reports_replaces_the_booking(world):
    out = go(FakeApify(charge=0.00255), run_id=world["run"])
    s = ledger.summary(world["run"])
    assert out["error"] is None
    assert s["by_provider"] == {"apify": 0.00255} and not s["partial"]


def test_a_refused_start_costs_nothing(world):
    resp = requests.Response(); resp.status_code = 402
    out = go(FakeApify(start_error=requests.HTTPError("402 Payment Required", response=resp)),
             run_id=world["run"])
    s = ledger.summary(world["run"])
    assert out["start"] == "refused" and s["total_usd"] == 0 and not s["partial"]


def test_an_unanswered_start_counts_at_its_booking(world):
    out = go(FakeApify(start_error=requests.Timeout("read timed out")), run_id=world["run"])
    s = ledger.summary(world["run"])
    assert out["start"] == "unknown"
    assert s["partial"] and s["total_usd"] == 0.0047    # the run may exist and may have charged


def test_a_run_whose_charge_cannot_be_read_counts_at_its_booking(world):
    out = go(FakeApify(charge=None), run_id=world["run"])
    s = ledger.summary(world["run"])
    assert out["charged_usd"] is None and s["partial"] and s["total_usd"] == 0.0047


def test_a_search_that_could_pass_the_cap_never_starts(world):
    ledger.reserve(world["run"], "earlier", "anthropic", model="claude-sonnet-5-5",
                   prompt_tokens=48_000, max_output_tokens=0)                  # $0.096 of $0.10
    api = FakeApify()
    with pytest.raises(ledger.BudgetExceeded):
        go(api, run_id=world["run"])                                          # books $0.0047
    assert api.started_with is None


# -- the route -------------------------------------------------------------------

ROUTE = "/p2/admin/external-usage/market-radar-apify-check"
ADMIN = "reporting@position2.com"


def _client(email):
    import app as appmod
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


@pytest.fixture
def no_spend(monkeypatch):
    """Fail the test if anything tries to search."""
    def refuse(*a, **k):
        raise AssertionError("the route tried to spend money")
    monkeypatch.setattr(mrs, "search", refuse)


@pytest.mark.parametrize("body", [None, {}, {"confirm_spend": "yes"}, {"confirm_spend": 1}])
def test_route_will_not_spend_without_an_explicit_confirmation(no_spend, monkeypatch, body):
    monkeypatch.setenv("APIFY_API_TOKEN", "t")
    resp = _client(ADMIN).post(ROUTE, json=body) if body is not None else _client(ADMIN).post(ROUTE)
    assert resp.status_code == 400 and "confirm_spend" in resp.get_json()["error"]


@pytest.mark.parametrize("email, headers, status", [
    (None, {}, 302),
    ("someone@position2.com", {}, 403),
    (ADMIN, {"Origin": "https://evil.example"}, 403),
])
def test_route_access(no_spend, email, headers, status):
    assert _client(email).post(ROUTE, json={"confirm_spend": True}, headers=headers).status_code == status


def test_route_without_a_token_spends_nothing(no_spend, monkeypatch):
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    resp = _client(ADMIN).post(ROUTE, json={"confirm_spend": True})
    assert resp.status_code == 503


def test_route_runs_one_search_and_returns_plan_cost_and_ledger(world, monkeypatch):
    from tracker import apify_transport
    monkeypatch.setenv("APIFY_API_TOKEN", "t")
    monkeypatch.setattr(apify_transport, "probe_token", lambda token: (
        {"username": "p2", "email": "secret@x.com", "plan": {"id": "STARTER", "tier": "BRONZE",
                                                            "proxyPassword": "nope"}}, None))
    real = mrs.search
    monkeypatch.setattr(mrs, "search", lambda q, **kw: real(q, api=FakeApify(), sleep=lambda s: None, **kw))
    resp = _client(ADMIN).post(ROUTE, json={"confirm_spend": True})
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body["account"] == {"username": "p2", "plan": {"id": "STARTER", "tier": "BRONZE"}}
    assert body["search"]["charged_usd"] == 0.00255 and len(body["top_results"]) == 2
    assert body["ledger"]["by_provider"] == {"apify": 0.00255} and not body["ledger"]["partial"]


def _http_error(code):
    resp = requests.Response()
    resp.status_code = code
    return requests.HTTPError("%s error" % code, response=resp)


class FlakyApify(FakeApify):
    """Fails the first `fails` reads of each kind, then behaves."""

    def __init__(self, error, fails=2, **kw):
        super().__init__(**kw)
        self.error, self.left = error, {"run": fails, "items": fails}

    def run(self, run_id):
        if self.left["run"]:
            self.left["run"] -= 1
            raise self.error
        return super().run(run_id)

    def items(self, dataset_id):
        if self.left["items"]:
            self.left["items"] -= 1
            raise self.error
        return super().items(dataset_id)


@pytest.mark.parametrize("error", [_http_error(502), _http_error(429), requests.ConnectionError("reset"),
                                   requests.Timeout("slow")])
def test_a_brief_apify_outage_while_the_run_works_is_waited_out(error):
    # 2026-10-09, drcjagadeesh.com: one 502 on a status read aborted a run
    # that had already been charged, and its results were thrown away.
    api = FlakyApify(error)
    out = go(api)
    assert out["error"] is None and len(out["results"]) == 2 and not api.aborted


def test_a_refusal_while_polling_is_final():
    api = FlakyApify(_http_error(404), fails=1)
    out = go(api)
    assert out["error"] and "404" in out["error"] and api.aborted


def test_an_outage_that_does_not_end_still_gives_up():
    api = FlakyApify(_http_error(503), fails=99)
    out = go(api)
    assert out["error"] and api.aborted
