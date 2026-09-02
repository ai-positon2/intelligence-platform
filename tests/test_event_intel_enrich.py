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
