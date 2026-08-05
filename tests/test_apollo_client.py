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


@patch("tracker.apollo_client.requests.post")
def test_search_companies_apollo_parity_filters(mock_post):
    mock_post.return_value = _mock_response({"organizations": []})

    filters = {
        "exclude_locations": ["Ireland"],
        "technologies": ["salesforce", "hubspot"],
        "revenue_min": 1000000,
        "revenue_max": 50000000,
        "founded_min": 2010,
        "founded_max": 2020,
    }
    apollo_client.search_companies(filters, _FAKE_API_KEY)

    sent = mock_post.call_args.kwargs["json"]
    assert sent["organization_not_locations"] == ["Ireland"]
    assert sent["currently_using_any_of_technology_uids"] == ["salesforce", "hubspot"]
    assert sent["revenue_range"] == {"min": 1000000, "max": 50000000}
    assert sent["organization_founded_year_range"] == {"min": 2010, "max": 2020}


@patch("tracker.apollo_client.requests.post")
def test_search_companies_meta_captures_pagination(mock_post):
    mock_post.return_value = _mock_response({
        "organizations": [{"id": "o1"}],
        "pagination": {"total_entries": 137, "total_pages": 6},
    })
    meta = {}
    apollo_client.search_companies({}, _FAKE_API_KEY, meta=meta)
    assert meta["total_entries"] == 137
    assert meta["total_pages"] == 6


@patch("tracker.apollo_client.requests.post")
def test_search_people_apollo_parity_filters(mock_post):
    mock_post.return_value = _mock_response({"people": []})

    filters = {
        "titles": ["CMO"],
        "include_similar_titles": False,
        "company_locations": ["United States"],
        "email_status": ["verified"],
        "technologies": ["salesforce"],
        "revenue_min": 500000,
        "revenue_max": 2000000,
    }
    apollo_client.search_people(filters, _FAKE_API_KEY)

    sent = mock_post.call_args.kwargs["json"]
    assert sent["include_similar_titles"] is False
    assert sent["organization_locations"] == ["United States"]
    assert sent["contact_email_status"] == ["verified"]
    assert sent["currently_using_any_of_technology_uids"] == ["salesforce"]
    assert sent["revenue_range"] == {"min": 500000, "max": 2000000}


@patch("tracker.apollo_client.requests.post")
def test_search_people_meta_captures_pagination(mock_post):
    mock_post.return_value = _mock_response({
        "people": [{"id": "p1", "name": "Jane Doe"}],
        "pagination": {"total_entries": 48, "total_pages": 2},
    })
    meta = {}
    apollo_client.search_people({}, _FAKE_API_KEY, meta=meta)
    assert meta["total_entries"] == 48
    assert meta["total_pages"] == 2


@patch("tracker.apollo_client.requests.post")
def test_bulk_match_people_builds_id_payload(mock_post):
    mock_post.return_value = _mock_response({
        "matches": [
            {"id": "p1", "first_name": "Sanjeev", "last_name": "Dhanaraj"},
            {"id": "p2", "first_name": "Sudheer", "last_name": "Reddy"},
        ],
    })
    result = apollo_client.bulk_match_people(["p1", "p2"], _FAKE_API_KEY)

    sent = mock_post.call_args.kwargs["json"]
    assert sent["details"] == [{"id": "p1"}, {"id": "p2"}]
    assert result["p1"]["last_name"] == "Dhanaraj"
    assert result["p2"]["last_name"] == "Reddy"


@patch("tracker.apollo_client.requests.post")
def test_bulk_match_people_chunks_over_ten_ids(mock_post):
    """Apollo caps bulk_match at 10 details per call, so 15 ids must be two calls."""
    mock_post.side_effect = [
        _mock_response({"matches": [{"id": "p%d" % i} for i in range(10)]}),
        _mock_response({"matches": [{"id": "p%d" % i} for i in range(10, 15)]}),
    ]
    ids = ["p%d" % i for i in range(15)]
    result = apollo_client.bulk_match_people(ids, _FAKE_API_KEY)

    assert mock_post.call_count == 2
    assert len(mock_post.call_args_list[0].kwargs["json"]["details"]) == 10
    assert len(mock_post.call_args_list[1].kwargs["json"]["details"]) == 5
    assert set(result.keys()) == set(ids)


@patch("tracker.apollo_client.requests.post")
def test_bulk_match_people_skips_unmatched_and_dedupes(mock_post):
    mock_post.return_value = _mock_response({"matches": [{"id": "p1", "name": "Jane"}, None]})
    result = apollo_client.bulk_match_people(["p1", "p1", "p2"], _FAKE_API_KEY)
    # Deduped to 2 unique ids sent; p2 (the null slot) never made it into the result.
    sent = mock_post.call_args.kwargs["json"]
    assert sent["details"] == [{"id": "p1"}, {"id": "p2"}]
    assert list(result.keys()) == ["p1"]


def test_bulk_match_people_empty_ids_returns_empty():
    assert apollo_client.bulk_match_people([], _FAKE_API_KEY) == {}


@patch("tracker.apollo_client.requests.post")
def test_bulk_match_people_bad_response_shape_returns_empty_for_chunk(mock_post):
    mock_post.return_value = _mock_response({"error": "scope missing"})
    result = apollo_client.bulk_match_people(["p1"], _FAKE_API_KEY)
    assert result == {}


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


# ── Full Apollo filter parity (org-level filters shared by both endpoints) ────

@patch("tracker.apollo_client.requests.post")
def test_search_people_full_org_filter_parity(mock_post):
    """Every org-level filter the UI can set reaches Apollo under its real name."""
    mock_post.return_value = _mock_response({"people": []})

    apollo_client.search_people({
        "industries": ["SaaS"],
        "market_segments": ["B2B"],
        "naics_codes": ["5415"],
        "exclude_naics_codes": ["7372"],
        "sic_codes": ["7372"],
        "exclude_sic_codes": ["5045"],
        "technologies": ["salesforce"],
        "technologies_all": ["hubspot"],
        "exclude_technologies": ["marketo"],
        "job_titles": ["sales manager"],
        "job_locations": ["atlanta"],
        "num_jobs_min": 5, "num_jobs_max": 50,
        "job_posted_after": "2026-01-01", "job_posted_before": "2026-06-01",
        "founded_min": 2015, "founded_max": 2020,
        "include_unknown_founded_year": True,
        "headcount_growth_min": 10, "headcount_growth_max": 90,
        "headcount_growth_months": 6,
        "department_counts": {"master_marketing": {"min": 5, "max": 40}},
        "yoe_min": 5, "yoe_max": 20,
        "days_in_title_min": 90, "days_in_title_max": 730,
        "linkedin_urls": ["https://www.linkedin.com/in/x"],
    }, _FAKE_API_KEY)

    sent = mock_post.call_args.kwargs["json"]
    assert sent["q_organization_keyword_tags"] == ["SaaS"]
    assert sent["market_segments"] == ["B2B"]
    assert sent["organization_naics_codes"] == ["5415"]
    assert sent["not_organization_naics_codes"] == ["7372"]
    assert sent["organization_sic_codes"] == ["7372"]
    assert sent["not_organization_sic_codes"] == ["5045"]
    assert sent["currently_using_any_of_technology_uids"] == ["salesforce"]
    assert sent["currently_using_all_of_technology_uids"] == ["hubspot"]
    assert sent["currently_not_using_any_of_technology_uids"] == ["marketo"]
    assert sent["q_organization_job_titles"] == ["sales manager"]
    assert sent["organization_job_locations"] == ["atlanta"]
    assert sent["organization_num_jobs_range"] == {"min": 5, "max": 50}
    assert sent["organization_job_posted_at_range"] == {"min": "2026-01-01", "max": "2026-06-01"}
    assert sent["organization_founded_year_range"] == {"min": 2015, "max": 2020}
    assert sent["organization_include_unknown_founded_year"] is True
    assert sent["organization_headcount_growth_range"] == {"min": 10, "max": 90}
    assert sent["organization_headcount_growth_past_n_months"] == 6
    assert sent["organization_department_or_subdepartment_counts"] == {
        "master_marketing": {"min": 5, "max": 40}}
    assert sent["person_total_yoe_range"] == {"min": 5, "max": 20}
    assert sent["person_days_in_current_title_range"] == {"min": 90, "max": 730}
    assert sent["person_linkedin_urls"] == ["https://www.linkedin.com/in/x"]


@patch("tracker.apollo_client.requests.post")
def test_search_companies_funding_filters(mock_post):
    mock_post.return_value = _mock_response({"organizations": []})

    apollo_client.search_companies({
        "total_funding_min": 50_000_000, "total_funding_max": 350_000_000,
        "latest_funding_min": 5_000_000, "latest_funding_max": 15_000_000,
        "funded_after": "2025-07-25", "funded_before": "2026-09-25",
        "label_ids": ["6605a710bd01d100a506d4ae"],
    }, _FAKE_API_KEY)

    sent = mock_post.call_args.kwargs["json"]
    assert sent["total_funding_range"] == {"min": 50_000_000, "max": 350_000_000}
    assert sent["latest_funding_amount_range"] == {"min": 5_000_000, "max": 15_000_000}
    assert sent["latest_funding_date_range"] == {"min": "2025-07-25", "max": "2026-09-25"}
    assert sent["account_label_ids"] == ["6605a710bd01d100a506d4ae"]


@patch("tracker.apollo_client.requests.post")
def test_open_ended_range_sends_only_the_given_bound(mock_post):
    """A min with no max must not send max:null, which Apollo rejects."""
    mock_post.return_value = _mock_response({"organizations": []})
    apollo_client.search_companies({"revenue_min": 1000}, _FAKE_API_KEY)
    assert mock_post.call_args.kwargs["json"]["revenue_range"] == {"min": 1000}


@patch("tracker.apollo_client.requests.post")
def test_unset_ranges_are_omitted_entirely(mock_post):
    mock_post.return_value = _mock_response({"organizations": []})
    apollo_client.search_companies({"name": "Acme"}, _FAKE_API_KEY)
    sent = mock_post.call_args.kwargs["json"]
    for absent in ("revenue_range", "organization_founded_year_range",
                   "total_funding_range", "organization_num_jobs_range"):
        assert absent not in sent


# ── Richer person normalisation ──────────────────────────────────────────────

@patch("tracker.apollo_client.requests.post")
def test_search_people_surfaces_all_free_fields(mock_post):
    mock_post.return_value = _mock_response({"people": [{
        "id": "p1", "first_name": "Ada", "last_name": "Lovelace",
        "title": "CMO", "headline": "Marketing leader", "seniority": "c_suite",
        "departments": ["marketing"], "subdepartments": ["brand"],
        "functions": ["marketing"], "email_status": "verified",
        "photo_url": "https://cdn/x.jpg", "linkedin_url": "http://li/in/ada",
        "twitter_url": "http://tw/ada",
        "city": "Austin", "state": "TX", "country": "United States",
        "employment_history": [
            {"organization_name": "Acme", "title": "CMO", "current": True,
             "start_date": "2023-04-01"},
            {"organization_name": "Globex", "title": "VP", "current": False},
            {"organization_name": "Initech", "title": "Dir", "current": False},
        ],
        "organization": {
            "id": "o1", "name": "Acme", "primary_domain": "acme.com",
            "logo_url": "https://cdn/logo.png", "industry": "software",
            "estimated_num_employees": 240, "founded_year": 2015,
            "annual_revenue": 4_200_000, "total_funding": 9_000_000,
            "technology_names": ["Salesforce", "HubSpot"], "keywords": ["saas"],
            "city": "Austin", "country": "United States",
        },
    }]})

    row = apollo_client.search_people({}, _FAKE_API_KEY)[0]
    assert row["full_name"] == "Ada Lovelace"
    assert row["name_masked"] is False
    assert row["headline"] == "Marketing leader"
    assert row["seniority"] == "c_suite"
    assert row["departments"] == ["marketing"]
    assert row["email_status"] == "verified"
    assert row["photo_url"] == "https://cdn/x.jpg"
    assert row["title_start_date"] == "2023-04-01"
    assert row["past_companies"] == ["Globex", "Initech"]
    assert row["past_roles_count"] == 2
    assert row["organization_logo"] == "https://cdn/logo.png"
    assert row["organization_industry"] == "software"
    assert row["organization_employees"] == 240
    assert row["organization_revenue"] == 4_200_000
    assert row["organization_technologies"] == ["Salesforce", "HubSpot"]


@patch("tracker.apollo_client.requests.post")
def test_search_people_handles_restricted_response_shape(mock_post):
    """Some plans withhold the surname and return only an obfuscated form.

    The row must still render (masked surname shown, flagged) rather than
    collapsing to a bare first name with no explanation.
    """
    mock_post.return_value = _mock_response({"people": [{
        "id": "p2", "first_name": "Celine", "last_name_obfuscated": "D.",
        "title": "Chief Marketing Officer (CMO)",
        "organization": {"name": "BTS"},
    }]})

    row = apollo_client.search_people({}, _FAKE_API_KEY)[0]
    assert row["full_name"] == "Celine D."
    assert row["name_masked"] is True
    assert row["last_name"] is None
    assert row["organization_name"] == "BTS"
    # Absent fields must be falsy, never the string "None".
    for key in ("photo_url", "seniority", "city", "organization_industry"):
        assert not row[key]


@patch("tracker.apollo_client.requests.post")
def test_search_people_missing_fields_do_not_raise(mock_post):
    """A minimal row (all optional fields absent) normalises without error."""
    mock_post.return_value = _mock_response({"people": [{"id": "p3"}]})
    row = apollo_client.search_people({}, _FAKE_API_KEY)[0]
    assert row["id"] == "p3"
    assert row["full_name"] is None
    assert row["departments"] == [] and row["past_companies"] == []


def test_field_coverage_counts_without_leaking_values():
    """The diagnostic log line must carry counts only, never personal data."""
    rows = [{"last_name": "Lovelace", "linkedin_url": "http://li/in/ada"},
            {"last_name": None, "linkedin_url": None}]
    out = apollo_client._field_coverage(rows)
    assert "last_name 1/2" in out
    assert "photo_url 0/2" in out
    assert "Lovelace" not in out and "li/in/ada" not in out
    assert apollo_client._field_coverage([]) == "no rows"
