"""What Event & Conference Intelligence actually asks Apollo for.

These assert the PAYLOAD, not the return value. A filter with a plausible name
that answers a different question is invisible from the outside: the call
succeeds, rows come back, and they are the wrong rows. The only place the
mistake is visible is the request body, so that is what is tested.
"""

import pytest

from tracker import apollo_client
from tracker import event_intel_enrich as E


@pytest.fixture()
def sent(monkeypatch):
    """Capture the payload instead of calling Apollo."""
    box = {}

    def fake_post(path, payload, key, **kw):
        box["path"] = path
        box["payload"] = payload
        return {"people": [], "organizations": []}

    monkeypatch.setattr(apollo_client, "_post", fake_post)
    return box


def test_a_title_filter_asks_for_people_with_that_title(sent):
    """The regression. "job_titles" is an EMPLOYER-level key that Apollo maps
    to q_organization_job_titles, meaning "companies with open job postings for
    these titles". Sending it in place of the person-title key returned
    arbitrary employees, at only the subset of exhibitors currently hiring a VP
    Marketing, and nobody at all from every exhibitor that was not hiring.
    On the one step in this agent that spends credits."""
    E.find_people(["acme.com", "beta.io"],
                  titles=["VP Marketing", "Head of Demand Gen"], key="k")
    payload = sent["payload"]
    assert payload.get("person_titles") == ["VP Marketing", "Head of Demand Gen"]
    assert "q_organization_job_titles" not in payload, (
        "asked which companies are HIRING a VP Marketing, not who IS one")


def test_the_domains_are_still_scoped_to_the_roster(sent):
    E.find_people(["acme.com", "beta.io"], titles=["VP Marketing"], key="k")
    assert sent["payload"].get("q_organization_domains_list") == ["acme.com",
                                                                 "beta.io"]


def test_no_titles_means_no_title_filter_at_all(sent):
    E.find_people(["acme.com"], titles=None, key="k")
    payload = sent["payload"]
    assert "person_titles" not in payload
    assert "q_organization_job_titles" not in payload


def test_a_missing_key_is_reported_rather_than_called():
    out = E.find_people(["acme.com"], titles=["VP Marketing"], key="")
    assert out["error"] and "APOLLO_API_KEY" in out["error"]
    assert out["by_domain"] == {}


def test_no_domains_is_not_an_error_and_calls_nothing(sent):
    out = E.find_people([], titles=["VP Marketing"], key="k")
    assert out["error"] is None
    assert out["by_domain"] == {}
    assert "payload" not in sent, "Apollo was called with nothing to look up"


# ── a failed batch is not a verdict on the companies it never reached ─────

def test_a_batch_failure_does_not_report_later_companies_as_unmatched(monkeypatch):
    """resolve_companies stops at the first failing batch, and every batch
    after it never runs. Those domains used to be reported as unmatched, which
    the pipeline writes as resolution="no_match" and the report renders with
    the wording reserved for a real negative: "we looked and Apollo has no
    record". On a 200-domain roster failing at batch 3 that is 125 companies
    given a verdict nobody reached."""
    calls = {"n": 0}

    def fake_search(filters, key, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"primary_domain": "a1.com", "name": "A1"}]
        raise RuntimeError("HTTP 503 from Apollo")

    monkeypatch.setattr(E.apollo_client if hasattr(E, "apollo_client") else
                        __import__("tracker.apollo_client", fromlist=["x"]),
                        "search_companies", fake_search)

    domains = ["a%d.com" % i for i in range(1, 60)]
    out = E.resolve_companies(domains, key="k")

    assert out["error"] and "503" in out["error"]
    assert out["unattempted"], "the never-queried domains were not reported"
    # Nothing may appear in both buckets.
    assert not (set(out["unmatched"]) & set(out["unattempted"]))
    # Every domain is accounted for exactly once.
    seen = set(out["by_domain"]) | set(out["unmatched"]) | set(out["unattempted"])
    assert seen == set(domains)


def test_no_api_key_reports_nothing_as_unmatched():
    """Nothing was looked up, so nothing is unmatched. Saying otherwise claims
    Apollo has no record of companies Apollo was never asked about."""
    out = E.resolve_companies(["a.com", "b.com"], key="")
    assert out["error"] and "APOLLO_API_KEY" in out["error"]
    assert out["unmatched"] == []
    assert out["unattempted"] == ["a.com", "b.com"]
