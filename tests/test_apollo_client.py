"""Tests for apollo_client.py — uses mocked HTTP responses."""

import pytest
from unittest.mock import patch, MagicMock

from tracker import apollo_client


_FAKE_API_KEY = "test-key"


def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    mock = MagicMock()
    mock.status_code = status_code
    mock.json.return_value = json_data
    mock.raise_for_status.return_value = None
    return mock


def _mock_rate_limit_then_ok(json_data: dict) -> list[MagicMock]:
    rate_limit = MagicMock()
    rate_limit.status_code = 429
    ok = _mock_response(json_data)
    return [rate_limit, ok]


@patch("tracker.apollo_client.requests.post")
def test_search_companies_basic(mock_post):
    mock_post.return_value = _mock_response({
        "organizations": [
            {"id": "org1", "name": "Acme Health", "primary_domain": "acme.com", "short_description": ""},
            {"id": "org2", "name": "Beta Care", "primary_domain": "betacare.com", "short_description": ""},
        ],
        "pagination": {"total_pages": 1},
    })

    filters = {
        "employee_min": 100,
        "employee_max": 2000,
        "industries": ["Hospital & Health Care"],
        "locations": ["United States"],
        "max_companies": 500,
    }
    result = apollo_client.search_companies(filters, _FAKE_API_KEY)
    assert len(result) == 2
    assert result[0]["name"] == "Acme Health"


@patch("tracker.apollo_client.requests.post")
def test_search_companies_excludes_keywords(mock_post):
    mock_post.return_value = _mock_response({
        "organizations": [
            {"id": "org1", "name": "Acme Health", "short_description": ""},
            {"id": "org2", "name": "Federal Health Foundation", "short_description": "nonprofit services"},
        ],
        "pagination": {"total_pages": 1},
    })

    filters = {
        "employee_min": 100,
        "employee_max": 2000,
        "exclude_keywords": ["federal", "foundation", "nonprofit"],
        "max_companies": 500,
    }
    result = apollo_client.search_companies(filters, _FAKE_API_KEY)
    assert len(result) == 1
    assert result[0]["id"] == "org1"


@patch("tracker.apollo_client.requests.post")
def test_enrich_company_returns_org(mock_post):
    mock_post.return_value = _mock_response({
        "organization": {
            "id": "org1",
            "name": "Acme Health",
            "num_employees": 500,
            "annual_revenue_printed": "$50M-$100M",
        }
    })

    result = apollo_client.enrich_company("acme.com", _FAKE_API_KEY)
    assert result["id"] == "org1"
    assert result["num_employees"] == 500


@patch("tracker.apollo_client.requests.post")
def test_enrich_company_api_error_returns_empty(mock_post):
    import requests as req_lib
    mock_post.side_effect = req_lib.RequestException("Network error")
    result = apollo_client.enrich_company("bad.com", _FAKE_API_KEY)
    assert result == {}


@patch("tracker.apollo_client.requests.post")
def test_get_leadership_filters_results(mock_post):
    mock_post.return_value = _mock_response({
        "people": [
            {"id": "p1", "name": "Jane Doe", "title": "CEO", "linkedin_url": "https://linkedin.com/in/jane", "employment_history": []},
            {"id": "p2", "name": "John Smith", "title": "VP of Engineering", "linkedin_url": None, "employment_history": []},
        ]
    })

    result = apollo_client.get_leadership("org1", _FAKE_API_KEY)
    assert len(result) == 2
    assert result[0]["full_name"] == "Jane Doe"
    assert result[0]["title"] == "CEO"


@patch("tracker.apollo_client.requests.post")
def test_get_leadership_uses_first_last_name(mock_post):
    """first_name + last_name should be combined into full_name."""
    mock_post.return_value = _mock_response({
        "people": [
            {"id": "p1", "first_name": "Alice", "last_name": "Smith", "title": "CFO",
             "linkedin_url": None, "email": "alice@example.com", "employment_history": []},
        ]
    })
    result = apollo_client.get_leadership("org1", _FAKE_API_KEY)
    assert result[0]["full_name"] == "Alice Smith"
    assert result[0]["first_name"] == "Alice"
    assert result[0]["last_name"] == "Smith"
    assert result[0]["email"] == "alice@example.com"


@patch("tracker.apollo_client.requests.post")
def test_get_leadership_name_fallback_when_no_first_last(mock_post):
    """When first_name/last_name are absent, name field is used as full_name."""
    mock_post.return_value = _mock_response({
        "people": [
            {"id": "p1", "name": "Bob Jones", "title": "CTO", "employment_history": []},
        ]
    })
    result = apollo_client.get_leadership("org1", _FAKE_API_KEY)
    assert result[0]["full_name"] == "Bob Jones"
    assert result[0]["first_name"] is None
    assert result[0]["last_name"] is None



@patch("tracker.apollo_client.time.sleep")
@patch("tracker.apollo_client.requests.post")
def test_rate_limit_retries(mock_post, mock_sleep):
    rate_limit = MagicMock()
    rate_limit.status_code = 429

    ok = _mock_response({"organization": {"id": "org1"}})
    mock_post.side_effect = [rate_limit, ok]

    result = apollo_client.enrich_company("acme.com", _FAKE_API_KEY)
    assert result.get("id") == "org1"
    assert mock_sleep.called


@patch("tracker.apollo_client.requests.post")
def test_search_companies_builds_real_payload(mock_post):
    mock_post.return_value = _mock_response({"organizations": []})

    filters = {
        "name": "Acme",
        "domains": ["acme.com"],
        "locations": ["United States"],
        "industries": ["Hospital & Health Care"],
        "employee_min": 100,
        "employee_max": 500,
    }
    apollo_client.search_companies(filters, _FAKE_API_KEY, page=2, per_page=50)

    sent = mock_post.call_args.kwargs["json"]
    assert sent["q_organization_name"] == "Acme"
    assert sent["q_organization_domains_list"] == ["acme.com"]
    assert sent["organization_locations"] == ["United States"]
    assert sent["q_organization_keyword_tags"] == ["Hospital & Health Care"]
    assert "101,200" in sent["organization_num_employees_ranges"]
    assert "201,500" in sent["organization_num_employees_ranges"]
    assert sent["page"] == 2
    assert sent["per_page"] == 50


@patch("tracker.apollo_client.requests.post")
def test_search_companies_normalizes_accounts_bucket_id(mock_post):
    """mixed_companies/search splits results into "organizations" (id IS the org
    id) and "accounts" (id is an ACCOUNT id; the real org id is a separate
    organization_id field, and domain lives in `domain` not `primary_domain`).
    Feeding an account's raw id into organization_ids elsewhere would silently
    match nothing or the wrong org, so both buckets must come out normalized."""
    mock_post.return_value = _mock_response({
        "organizations": [{"id": "org-real-1", "name": "Acme Health", "primary_domain": "acme.com"}],
        "accounts": [{"id": "acct-999", "organization_id": "org-real-2", "name": "Acme Care", "domain": "acmecare.com"}],
    })
    result = apollo_client.search_companies({}, _FAKE_API_KEY)
    assert len(result) == 2
    by_name = {o["name"]: o for o in result}
    assert by_name["Acme Health"]["id"] == "org-real-1"
    assert by_name["Acme Care"]["id"] == "org-real-2"
    assert by_name["Acme Care"]["primary_domain"] == "acmecare.com"


@patch("tracker.apollo_client.requests.post")
def test_search_companies_max_companies_caps_results(mock_post):
    mock_post.return_value = _mock_response({
        "organizations": [{"id": "o1"}, {"id": "o2"}, {"id": "o3"}],
    })
    result = apollo_client.search_companies({"max_companies": 2}, _FAKE_API_KEY)
    assert len(result) == 2


@patch("tracker.apollo_client.requests.post")
def test_search_people_builds_real_payload(mock_post):
    mock_post.return_value = _mock_response({"people": []})

    filters = {
        "titles": ["CMO", "Chief Marketing Officer"],
        "seniorities": ["c_suite"],
        "company_domains": ["acme.com"],
        "employee_min": 51,
        "employee_max": 200,
        "keywords": "marketing",
    }
    apollo_client.search_people(filters, _FAKE_API_KEY, page=1, per_page=10)

    sent = mock_post.call_args.kwargs["json"]
    assert sent["person_titles"] == ["CMO", "Chief Marketing Officer"]
    assert sent["include_similar_titles"] is True
    assert sent["person_seniorities"] == ["c_suite"]
    assert sent["q_organization_domains_list"] == ["acme.com"]
    assert "51,100" in sent["organization_num_employees_ranges"]
    assert "101,200" in sent["organization_num_employees_ranges"]
    assert sent["q_keywords"] == "marketing"
    assert sent["per_page"] == 10


@patch("tracker.apollo_client.requests.post")
def test_search_people_normalizes_and_caps_results(mock_post):
    mock_post.return_value = _mock_response({
        "people": [
            {"id": "p1", "first_name": "Jane", "last_name": "Doe", "title": "CMO",
             "linkedin_url": "https://linkedin.com/in/jane", "seniority": "c_suite",
             "city": "Austin", "state": "TX", "country": "US",
             "organization": {"id": "org1", "name": "Acme Health", "primary_domain": "acme.com"}},
            {"id": "p2", "name": "John Smith", "title": "VP Marketing"},
        ],
    })
    result = apollo_client.search_people({"max_people": 1}, _FAKE_API_KEY)
    assert len(result) == 1
    assert result[0]["full_name"] == "Jane Doe"
    assert result[0]["organization_name"] == "Acme Health"
    assert result[0]["organization_domain"] == "acme.com"


@patch("tracker.apollo_client.requests.post")
def test_search_people_api_error_returns_empty(mock_post):
    import requests as req_lib
    mock_post.side_effect = req_lib.RequestException("Network error")
    result = apollo_client.search_people({"titles": ["CEO"]}, _FAKE_API_KEY)
    assert result == []


def test_employee_ranges_mapping():
    ranges = apollo_client._employee_ranges_for(100, 500)
    assert "101,200" in ranges
    assert "201,500" in ranges
    assert "1,10" not in ranges
    assert "1001,2000" not in ranges


def test_employee_ranges_overlap():
    ranges = apollo_client._employee_ranges_for(50, 200)
    assert "51,100" in ranges
    assert "101,200" in ranges
