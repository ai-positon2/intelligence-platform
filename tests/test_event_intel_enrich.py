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


# ── what the LIVE Apollo API actually returns ────────────────────────────
#
# Every row below is the real shape observed against api.apollo.io on
# 2026-09-02: organization_name populated, organization_domain null,
# last_name null, name null, full_name carrying a masked surname, and
# name_masked true. Under the domain-only grouping these tests were written
# for, all 25 live rows were discarded and find_people returned
# {"by_domain": {}, "total": 0, "error": None}: a dead lookup that reported
# success, on every roster, for every client.

def _live_row(org, full, title, **kw):
    row = {
        "id": "x", "full_name": full, "first_name": full.split(" ")[0],
        "last_name": None, "name_masked": True, "title": title,
        "seniority": None, "linkedin_url": None, "city": None,
        "organization_id": None, "organization_name": org,
        "organization_domain": None, "organization_website": None,
        "employer_unconfirmed": True,
    }
    row.update(kw)
    return row


@pytest.fixture()
def live_rows(monkeypatch):
    """Serve a fixed set of live-shaped rows from apollo_client.search_people."""
    box = {"rows": []}

    def fake_search_people(filters, key, **kw):
        if kw.get("page", 1) > 1:
            return []
        return list(box["rows"])

    from tracker import apollo_client
    monkeypatch.setattr(apollo_client, "search_people", fake_search_people)
    return box


def test_a_person_with_no_employer_domain_is_still_placed_by_employer_name(live_rows):
    """The regression. Apollo returned the right people and the grouping threw
    every one of them away, because it keyed on a field the API left null."""
    live_rows["rows"] = [
        _live_row("HubSpot", "Kipp Bo***r", "CMO"),
        _live_row("Gong", "Emily He", "Chief Marketing Officer"),
    ]
    out = E.find_people(["hubspot.com", "gong.io"], titles=["CMO"], key="k")
    assert out["error"] is None
    assert out["total"] == 2, "live-shaped rows were dropped again"
    assert [p["title"] for p in out["by_domain"]["hubspot.com"]] == ["CMO"]
    assert out["by_domain"]["gong.io"][0]["name"] == "Emily He"


def test_a_masked_surname_is_kept_rather_than_truncated_to_a_first_name(live_rows):
    """last_name is null on a masked record while full_name still carries the
    surname, so reading first+last rendered "Emily He" as "Emily"."""
    live_rows["rows"] = [_live_row("Gong", "Emily He", "CMO")]
    out = E.find_people(["gong.io"], titles=["CMO"], key="k")
    assert out["by_domain"]["gong.io"][0]["name"] == "Emily He"


def test_a_masked_record_says_it_is_masked(live_rows):
    """A partial name must not be rendered as if it were the whole name."""
    live_rows["rows"] = [_live_row("Gong", "Anita Go***y", "Chief of Staff, CMO")]
    out = E.find_people(["gong.io"], titles=["CMO"], key="k")
    assert out["by_domain"]["gong.io"][0]["name_masked"] is True
    assert out["names_masked"] == 1


def test_company_suffixes_and_punctuation_do_not_block_the_match(live_rows):
    live_rows["rows"] = [
        _live_row("HubSpot, Inc.", "A B***c", "CMO"),
        _live_row("Acme Group Ltd", "C D***e", "VP Marketing"),
    ]
    out = E.find_people(["hubspot.com", "acme.co.uk"], titles=["CMO"], key="k")
    assert set(out["by_domain"]) == {"hubspot.com", "acme.co.uk"}


def test_a_person_nobody_asked_about_is_reported_not_silently_dropped(live_rows):
    """q_organization_domains_list is a relevance hint, so strangers come back.
    Dropping them is right; dropping them SILENTLY is how a broken grouping
    hid for two releases."""
    live_rows["rows"] = [
        _live_row("HubSpot", "A B***c", "CMO"),
        _live_row("Some Other Company", "E F***g", "CMO"),
    ]
    out = E.find_people(["hubspot.com"], titles=["CMO"], key="k")
    assert out["total"] == 1
    assert out["unattributed"] == {"Some Other Company": 1}


def test_people_returned_but_none_attributable_reads_as_a_gap_not_a_finding(live_rows):
    """The exact sentence the old code could not say. Zero contacts because
    the lookup broke must never look like zero contacts because the companies
    have nobody in these roles."""
    live_rows["rows"] = [_live_row("Totally Unrelated Co", "A B***c", "CMO")]
    out = E.find_people(["hubspot.com"], titles=["CMO"], key="k")
    assert out["total"] == 0
    assert out["returned"] == 1
    assert out["note"] and "gap in the lookup" in out["note"]


def test_an_employer_domain_still_wins_when_apollo_does_return_one(live_rows):
    """The name fallback is a LAST resort and must never override a real
    domain. The two disagree in the wild: an acquired brand keeps its own
    domain under the parent's name, so a row can carry organization_domain
    gong.io and organization_name HubSpot while BOTH are on the roster. The
    domain is the identifier; the name is the guess. Getting this backwards
    files a person under a company they do not work for, which is worse than
    not finding them, because it reads as a confirmed contact."""
    live_rows["rows"] = [
        _live_row("HubSpot", "A B***c", "CMO", organization_domain="gong.io"),
    ]
    out = E.find_people(["gong.io", "hubspot.com"], titles=["CMO"], key="k")
    assert list(out["by_domain"]) == ["gong.io"], (
        "the employer NAME overrode the employer DOMAIN and filed this person "
        "under the wrong company")
