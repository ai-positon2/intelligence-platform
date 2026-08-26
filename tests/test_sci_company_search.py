"""tracker/sci_company_search.py -- SCI's native (Apollo-backed) company
search. Verifies the normalized company shape matches arena_client's own
(so the frontend needed no changes when the vendor was swapped), the typed
error contract (kind/status/detail/retryable), and that a missing
APOLLO_API_KEY degrades to a stated reason rather than a raised exception.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_company_search as scs  # noqa: E402
from tracker import apollo_client  # noqa: E402


@pytest.fixture(autouse=True)
def _configured_key(monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")


# ── _to_company normalization ───────────────────────────────────────────────

def test_to_company_maps_apollo_fields_onto_the_arena_shape():
    org = {"id": "org1", "name": "Acme Inc", "primary_domain": "acme.com",
          "industry": "software", "short_description": "Makes widgets",
          "logo_url": "https://cdn/acme.png", "linkedin_url": "https://linkedin.com/company/acme",
          "city": "Austin", "state": "TX", "country": "United States"}
    c = scs._to_company(org)
    assert c == {
        "id": "org1", "name": "Acme Inc", "logo": "https://cdn/acme.png",
        "industry": "software", "location": "Austin, TX, United States",
        "description": "Makes widgets", "summary": None, "followers_count": None,
        "profile_url": "https://linkedin.com/company/acme", "website": "https://acme.com",
    }


def test_to_company_falls_back_to_website_url_over_a_bare_domain():
    org = {"id": 5, "name": "Beta", "primary_domain": "beta.com",
          "website_url": "https://www.beta.com/home"}
    c = scs._to_company(org)
    assert c["id"] == "5"
    assert c["website"] == "https://www.beta.com/home"


def test_to_company_returns_none_without_a_usable_name():
    assert scs._to_company({"id": "1", "name": ""}) is None
    assert scs._to_company({"id": "1"}) is None


def test_to_company_tolerates_a_bare_row_with_only_a_name():
    assert scs._to_company({"name": "Solo Co"}) == {
        "id": "", "name": "Solo Co", "logo": None, "industry": None, "location": None,
        "description": None, "summary": None, "followers_count": None,
        "profile_url": None, "website": None,
    }


# ── search_companies_result ─────────────────────────────────────────────────

def test_degrades_to_not_configured_without_a_key(monkeypatch):
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    result = scs.search_companies_result("Acme")
    assert result["companies"] == []
    assert result["error"]["kind"] == scs.ERR_NOT_CONFIGURED
    assert result["elapsed_ms"] == 0


def test_a_successful_search_normalizes_and_reports_no_error(monkeypatch):
    monkeypatch.setattr(apollo_client, "search_companies",
                        lambda filters, key, **kw: [
                            {"id": "org1", "name": "Acme Inc", "primary_domain": "acme.com"},
                            {"id": "org2", "name": "Acme Records"},
                        ])
    result = scs.search_companies_result("Acme")
    assert result["error"] is None
    assert [c["name"] for c in result["companies"]] == ["Acme Inc", "Acme Records"]
    assert result["source"] == "mixed_companies/search"


def test_a_genuine_zero_result_has_no_error(monkeypatch):
    monkeypatch.setattr(apollo_client, "search_companies", lambda filters, key, **kw: [])
    result = scs.search_companies_result("zzz-no-such-company")
    assert result == {"companies": [], "error": None, "elapsed_ms": result["elapsed_ms"],
                      "source": "mixed_companies/search"}


def _http_error(status, body):
    resp = MagicMock()
    resp.status_code = status
    resp.text = body
    err = requests.HTTPError("%s error" % status)
    err.response = resp
    return err


def test_an_http_error_carries_status_and_body(monkeypatch):
    def _raise(filters, key, **kw):
        raise _http_error(401, '{"error":"invalid api key"}')
    monkeypatch.setattr(apollo_client, "search_companies", _raise)
    result = scs.search_companies_result("Acme")
    assert result["companies"] == []
    err = result["error"]
    assert err["kind"] == scs.ERR_HTTP
    assert err["status"] == 401
    assert "invalid api key" in err["detail"]


def test_a_timeout_is_reported_distinctly_from_an_http_error(monkeypatch):
    def _raise(filters, key, **kw):
        raise requests.Timeout("no response in 30s")
    monkeypatch.setattr(apollo_client, "search_companies", _raise)
    result = scs.search_companies_result("Acme")
    assert result["error"]["kind"] == scs.ERR_TIMEOUT
    assert result["error"]["status"] is None


def test_a_connection_error_is_reported_as_network(monkeypatch):
    def _raise(filters, key, **kw):
        raise requests.ConnectionError("dns failure")
    monkeypatch.setattr(apollo_client, "search_companies", _raise)
    result = scs.search_companies_result("Acme")
    assert result["error"]["kind"] == scs.ERR_NETWORK


def test_search_companies_is_best_effort_and_never_raises(monkeypatch):
    def _raise(filters, key, **kw):
        raise requests.ConnectionError("dns failure")
    monkeypatch.setattr(apollo_client, "search_companies", _raise)
    assert scs.search_companies("Acme") == []


# ── describe_error / is_retryable ───────────────────────────────────────────

def test_a_dead_key_is_described_clearly_and_is_not_retryable():
    err = scs._err(scs.ERR_HTTP, "bad key", status=401)
    assert "rejected our API key" in scs.describe_error(err)
    assert scs.is_retryable(err) is False


def test_a_rate_limit_is_retryable():
    err = scs._err(scs.ERR_HTTP, "slow down", status=429)
    assert scs.is_retryable(err) is True
    assert "rate-limiting" in scs.describe_error(err)


def test_a_billing_failure_is_described_and_not_retryable():
    err = scs._err(scs.ERR_HTTP, "no credit", status=402)
    assert "billing" in scs.describe_error(err)
    assert scs.is_retryable(err) is False


def test_timeout_and_network_errors_are_retryable():
    assert scs.is_retryable(scs._err(scs.ERR_TIMEOUT)) is True
    assert scs.is_retryable(scs._err(scs.ERR_NETWORK)) is True


def test_not_configured_is_neither_retryable_nor_a_crash():
    err = scs._err(scs.ERR_NOT_CONFIGURED)
    assert scs.is_retryable(err) is False
    assert "APOLLO_API_KEY" in scs.describe_error(err)


# ── probe ────────────────────────────────────────────────────────────────

def test_probe_without_a_key_reports_unconfigured(monkeypatch):
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    out = scs.probe()
    assert out["configured"] is False
    assert out["error_kind"] == scs.ERR_NOT_CONFIGURED


def test_probe_reports_a_successful_match(monkeypatch):
    monkeypatch.setattr(apollo_client, "search_companies",
                        lambda filters, key, **kw: [{"id": "1", "name": "Microsoft"}])
    out = scs.probe("Microsoft")
    assert out["configured"] is True
    assert out["companies"] == 1
    assert out["sample"] == ["Microsoft"]
    assert out["error"] == ""


def test_probe_flags_a_surprising_zero_result_as_a_data_question(monkeypatch):
    monkeypatch.setattr(apollo_client, "search_companies", lambda filters, key, **kw: [])
    out = scs.probe("Microsoft")
    assert out["companies"] == 0
    assert "Apollo's own data or plan limits" in out["error"]
