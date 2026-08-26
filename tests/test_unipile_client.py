"""tracker/unipile_client.py -- no network calls. Mirrors
tests/test_arena_client.py's fake-response style, since this client follows
arena_client's own shape (module-level key, (data, err) tuples, never raises).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import unipile_client as uc  # noqa: E402


class _FakeResp:
    def __init__(self, status=200, body="{}"):
        self.status_code = status
        self.text = body

    def json(self):
        return json.loads(self.text)


def _request_returning(*responses):
    calls = []

    def _request(method, url, **kw):
        calls.append({"method": method, "url": url, **kw})
        item = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    _request.calls = calls
    return _request


# ── Configuration / auth plumbing ────────────────────────────────────────

def test_dsn_defaults_to_the_shared_gateway(monkeypatch):
    monkeypatch.delenv("UNIPILE_DSN", raising=False)
    assert uc._dsn() == "https://api.unipile.com"


def test_dsn_prefers_an_explicit_account_specific_host(monkeypatch):
    monkeypatch.setenv("UNIPILE_DSN", "https://api9.unipile.com:14650/")
    assert uc._dsn() == "https://api9.unipile.com:14650"


def test_list_accounts_reports_not_configured_without_a_key(monkeypatch):
    monkeypatch.delenv("UNIPILE_API_KEY", raising=False)
    accounts, err = uc.list_accounts()
    assert accounts is None
    assert err["kind"] == uc.ERR_NOT_CONFIGURED
    assert "UNIPILE_API_KEY" in uc.describe_error(err)


def test_request_sends_the_key_as_the_x_api_key_header(monkeypatch):
    monkeypatch.setenv("UNIPILE_API_KEY", "secret-key")
    req = _request_returning(_FakeResp(200, "[]"))
    monkeypatch.setattr(uc.requests, "request", req)
    uc.list_accounts()
    assert req.calls[0]["headers"]["X-API-KEY"] == "secret-key"


# ── list_accounts: tolerant response shapes ──────────────────────────────

def test_list_accounts_accepts_a_bare_list(monkeypatch):
    monkeypatch.setenv("UNIPILE_API_KEY", "k")
    monkeypatch.setattr(uc.requests, "request",
                        _request_returning(_FakeResp(200, json.dumps([{"id": "a1", "type": "LINKEDIN"}]))))
    accounts, err = uc.list_accounts()
    assert err is None
    assert accounts == [{"id": "a1", "type": "LINKEDIN"}]


def test_list_accounts_accepts_an_items_envelope(monkeypatch):
    monkeypatch.setenv("UNIPILE_API_KEY", "k")
    body = json.dumps({"items": [{"id": "a1", "type": "LINKEDIN"}], "cursor": None})
    monkeypatch.setattr(uc.requests, "request", _request_returning(_FakeResp(200, body)))
    accounts, err = uc.list_accounts()
    assert err is None
    assert accounts == [{"id": "a1", "type": "LINKEDIN"}]


def test_list_accounts_reports_unexpected_shape_rather_than_guessing(monkeypatch):
    monkeypatch.setenv("UNIPILE_API_KEY", "k")
    monkeypatch.setattr(uc.requests, "request",
                        _request_returning(_FakeResp(200, json.dumps({"unexpected": "shape"}))))
    accounts, err = uc.list_accounts()
    assert accounts is None
    assert err["kind"] == uc.ERR_SHAPE


# ── accounts_by_platform / is_available ──────────────────────────────────

def test_accounts_by_platform_groups_by_lowercased_type():
    accounts = [{"id": "a1", "type": "LINKEDIN"}, {"id": "a2", "provider": "instagram"},
               {"id": "a3", "type": "LINKEDIN"}]
    grouped = uc.accounts_by_platform(accounts)
    assert len(grouped["linkedin"]) == 2
    assert len(grouped["instagram"]) == 1


def test_is_available_true_when_a_matching_account_exists(monkeypatch):
    monkeypatch.setenv("UNIPILE_API_KEY", "k")
    monkeypatch.setattr(uc.requests, "request",
                        _request_returning(_FakeResp(200, json.dumps([{"id": "a1", "type": "LINKEDIN"}]))))
    assert uc.is_available("linkedin") is True
    assert uc.is_available("instagram") is False


def test_is_available_false_on_any_error(monkeypatch):
    monkeypatch.delenv("UNIPILE_API_KEY", raising=False)
    assert uc.is_available("linkedin") is False


# ── Failure kinds: a rejected key must be distinguishable from "not configured" ──

def test_a_rejected_key_is_reported_as_an_http_error_with_its_status(monkeypatch):
    monkeypatch.setenv("UNIPILE_API_KEY", "bad-key")
    monkeypatch.setattr(uc.requests, "request",
                        _request_returning(_FakeResp(401, '{"type":"api/invalid_credentials"}')))
    accounts, err = uc.list_accounts()
    assert accounts is None
    assert err["kind"] == uc.ERR_HTTP
    assert err["status"] == 401
    assert "DSN" in uc.describe_error(err) or "dashboard" in uc.describe_error(err)


def test_a_network_failure_never_raises(monkeypatch):
    monkeypatch.setenv("UNIPILE_API_KEY", "k")

    def _boom(*a, **kw):
        raise uc.requests.RequestException("connection refused")

    monkeypatch.setattr(uc.requests, "request", _boom)
    accounts, err = uc.list_accounts()
    assert accounts is None
    assert err["kind"] == uc.ERR_NETWORK


# ── create_hosted_auth_link ───────────────────────────────────────────────

def test_create_hosted_auth_link_reports_shape_error_without_a_url(monkeypatch):
    monkeypatch.setenv("UNIPILE_API_KEY", "k")
    monkeypatch.setattr(uc.requests, "request",
                        _request_returning(_FakeResp(200, json.dumps({"object": "HostedAuthLink"}))))
    data, err = uc.create_hosted_auth_link(["LINKEDIN"])
    assert data is None
    assert err["kind"] == uc.ERR_SHAPE


def test_create_hosted_auth_link_returns_the_url_on_success(monkeypatch):
    monkeypatch.setenv("UNIPILE_API_KEY", "k")
    monkeypatch.setattr(uc.requests, "request",
                        _request_returning(_FakeResp(200, json.dumps({"url": "https://connect.unipile.com/x"}))))
    data, err = uc.create_hosted_auth_link(["LINKEDIN"])
    assert err is None
    assert data["url"] == "https://connect.unipile.com/x"


# ── probe() -- never raises, mirrors arena_client.probe()'s output shape ──

def test_probe_without_a_key_reports_unconfigured(monkeypatch):
    monkeypatch.delenv("UNIPILE_API_KEY", raising=False)
    result = uc.probe()
    assert result["configured"] is False
    assert result["ok"] is False
    assert result["error_kind"] == uc.ERR_NOT_CONFIGURED


def test_probe_reports_accounts_grouped_by_platform_on_success(monkeypatch):
    monkeypatch.setenv("UNIPILE_API_KEY", "k")
    body = json.dumps([{"id": "a1", "type": "LINKEDIN", "status": "OK"}])
    monkeypatch.setattr(uc.requests, "request", _request_returning(_FakeResp(200, body)))
    result = uc.probe()
    assert result["ok"] is True
    assert result["by_platform"] == {"linkedin": 1}
    assert result["accounts"][0]["platform"] == "linkedin"


def test_probe_never_raises_on_an_unexpected_exception(monkeypatch):
    monkeypatch.setenv("UNIPILE_API_KEY", "k")

    def _boom(*a, **kw):
        raise RuntimeError("something unexpected")

    monkeypatch.setattr(uc, "list_accounts", _boom)
    result = uc.probe()
    assert result["ok"] is False
    assert result["error_kind"] == "exception"
