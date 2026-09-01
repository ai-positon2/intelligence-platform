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

def _live_account(account_id="a1", platform="LINKEDIN", status="OK", name="Acme Bot"):
    """One account in the shape /api/v1/accounts really returns. The nesting
    is the point: status lives under sources[], NOT at the top level, and a
    fixture that flattens it cannot express the difference between a working
    account and one whose login has lapsed -- which is the difference this
    module exists to act on."""
    return {"object": "Account", "id": account_id, "name": name, "type": platform,
            "created_at": "2026-08-24T12:57:21.706Z", "groups": [],
            "sources": [{"id": account_id + "_MESSAGING", "status": status}]}


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
                        _request_returning(_FakeResp(200, json.dumps([_live_account()]))))
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


# ── The live contract, confirmed against a real DSN on 2026-09-01 ────────
# Every test below locks in something that was WRONG in this module's first
# version. They are not redundant with the shape-tolerance tests above: those
# prove the client survives a surprise, these prove it agrees with reality.

def test_a_dsn_pasted_straight_from_the_dashboard_gets_a_scheme(monkeypatch):
    """Unipile's dashboard shows the DSN as a bare host:port, and requests
    raises MissingSchema on that, which reads as a network failure rather
    than as a configuration mistake."""
    monkeypatch.setenv("UNIPILE_DSN", "api42.unipile.com:13900")
    assert uc._dsn() == "https://api42.unipile.com:13900"


def test_an_explicit_scheme_is_never_doubled(monkeypatch):
    monkeypatch.setenv("UNIPILE_DSN", "http://localhost:3000/")
    assert uc._dsn() == "http://localhost:3000"


def test_every_route_lives_under_api_v1(monkeypatch):
    """/v2 and /v1 both answer 404 on a live DSN; only /api/v1 exists. This
    asserts the URL actually requested, not the constant, because a path
    built by f-string in one place and a constant in another is exactly how
    one route gets left behind."""
    monkeypatch.setenv("UNIPILE_API_KEY", "k")
    monkeypatch.setenv("UNIPILE_DSN", "https://api42.unipile.com:13900")
    req = _request_returning(_FakeResp(200, json.dumps({"items": [], "url": "u", "id": "1"})))
    monkeypatch.setattr(uc.requests, "request", req)
    uc.list_accounts()
    uc.create_hosted_auth_link(["LINKEDIN"])
    uc.get_company("position2", "a1")
    uc.list_posts("a1", "60223")
    paths = [c["url"].replace("https://api42.unipile.com:13900", "") for c in req.calls]
    assert paths == ["/api/v1/accounts", "/api/v1/hosted/accounts/link",
                     "/api/v1/linkedin/company/position2", "/api/v1/users/60223/posts"]


def test_account_status_is_read_from_sources_not_from_a_top_level_field():
    """The live payload has no top-level status. Reading one returns None for
    every real account, so a caller trusting it treats a signed-out account
    as healthy."""
    assert "status" not in _live_account()
    assert uc.account_status(_live_account(status="OK")) == "OK"
    assert uc.account_status(_live_account(status="CREDENTIALS")) == "CREDENTIALS"
    assert uc.is_connected(_live_account(status="OK")) is True
    assert uc.is_connected(_live_account(status="CREDENTIALS")) is False


def test_an_account_with_any_working_source_counts_as_connected():
    acct = _live_account()
    acct["sources"] = [{"status": "CREDENTIALS"}, {"status": "OK"}]
    assert uc.is_connected(acct) is True


def test_is_available_ignores_accounts_whose_login_has_lapsed(monkeypatch):
    """The failure this prevents: a signed-out account is listed by /accounts
    forever, so counting it reports LinkedIn as available and then fails
    every single collection made through it."""
    monkeypatch.setenv("UNIPILE_API_KEY", "k")
    body = json.dumps([_live_account("a1", status="CREDENTIALS"),
                       _live_account("a2", status="CREDENTIALS")])
    monkeypatch.setattr(uc.requests, "request", _request_returning(_FakeResp(200, body)))
    assert uc.is_available("linkedin") is False


def test_accounts_by_platform_can_keep_or_drop_the_lapsed_ones():
    accounts = [_live_account("a1", status="OK"), _live_account("a2", status="CREDENTIALS")]
    assert len(uc.accounts_by_platform(accounts)["linkedin"]) == 2
    assert len(uc.accounts_by_platform(accounts, connected_only=True)["linkedin"]) == 1


def test_the_hosted_auth_link_always_sends_an_expiry(monkeypatch):
    """Omitting expiresOn returns 400 for every request, so the Connect
    button did nothing before this. Confirmed against a real 201."""
    monkeypatch.setenv("UNIPILE_API_KEY", "k")
    req = _request_returning(_FakeResp(201, json.dumps({"object": "HostedAuthUrl", "url": "https://x"})))
    monkeypatch.setattr(uc.requests, "request", req)
    data, err = uc.create_hosted_auth_link(["LINKEDIN"])
    assert err is None and data["url"] == "https://x"
    assert "expiresOn" in req.calls[0]["json"]


def test_the_expiry_matches_unipile_s_own_pattern():
    """Unipile's schema pins expiresOn to millisecond precision with a
    literal Z. datetime.isoformat() emits microseconds and +00:00, both of
    which that regex rejects, so this is asserted against their pattern
    rather than against 'looks like a date'."""
    import re
    from datetime import datetime, timezone
    pattern = r"^[1-2]\d{3}-[0-1]\d-[0-3]\dT\d{2}:\d{2}:\d{2}.\d{3}Z$"
    assert re.match(pattern, uc._expires_on())
    # A microsecond value that rounds badly is the case a "looks like a date"
    # check would wave through.
    stamp = uc._expires_on(datetime(2026, 1, 2, 3, 4, 5, 6, tzinfo=timezone.utc))
    assert re.match(pattern, stamp), stamp


def test_the_auth_link_tells_unipile_which_host_to_bind_the_account_to(monkeypatch):
    monkeypatch.setenv("UNIPILE_API_KEY", "k")
    monkeypatch.setenv("UNIPILE_DSN", "api42.unipile.com:13900")
    req = _request_returning(_FakeResp(201, json.dumps({"url": "https://x"})))
    monkeypatch.setattr(uc.requests, "request", req)
    uc.create_hosted_auth_link(["LINKEDIN"])
    assert req.calls[0]["json"]["api_url"] == "https://api42.unipile.com:13900"


def test_get_company_turns_a_vanity_slug_into_the_numeric_id(monkeypatch):
    """The posts endpoint answers 422 for a slug and 200 for the number, so
    this resolve step is load-bearing, not a nicety."""
    monkeypatch.setenv("UNIPILE_API_KEY", "k")
    body = json.dumps({"object": "CompanyProfile", "id": "60223", "name": "Position2"})
    monkeypatch.setattr(uc.requests, "request", _request_returning(_FakeResp(200, body)))
    company, err = uc.get_company("position2", "acct-1")
    assert err is None and company["id"] == "60223"


def test_get_company_reports_a_shape_error_rather_than_a_company_with_no_id(monkeypatch):
    monkeypatch.setenv("UNIPILE_API_KEY", "k")
    monkeypatch.setattr(uc.requests, "request",
                        _request_returning(_FakeResp(200, json.dumps({"name": "Position2"}))))
    company, err = uc.get_company("position2", "acct-1")
    assert company is None and err["kind"] == uc.ERR_SHAPE


def test_an_unreachable_profile_is_explained_as_such_not_as_a_server_error():
    assert "identifier" in uc.describe_error(uc._err(uc.ERR_HTTP, "", status=422))


def test_list_posts_never_asks_for_more_than_the_vendor_s_page_cap(monkeypatch):
    monkeypatch.setenv("UNIPILE_API_KEY", "k")
    req = _request_returning(_FakeResp(200, json.dumps({"items": []})))
    monkeypatch.setattr(uc.requests, "request", req)
    uc.list_posts("a1", "60223", limit=5000)
    assert req.calls[0]["params"]["limit"] == 100
    uc.list_posts("a1", "60223", limit=0)
    assert req.calls[1]["params"]["limit"] == 1


def test_probe_separates_accounts_that_work_from_accounts_that_are_merely_listed(monkeypatch):
    """The admin panel prints one of these as "connected". Reporting the
    listed count there would have claimed 17 working LinkedIn accounts on a
    workspace where 6 of them fail every call."""
    monkeypatch.setenv("UNIPILE_API_KEY", "k")
    body = json.dumps([_live_account("a1", status="OK"),
                       _live_account("a2", status="CREDENTIALS"),
                       _live_account("a3", status="OK")])
    monkeypatch.setattr(uc.requests, "request", _request_returning(_FakeResp(200, body)))
    result = uc.probe()
    assert result["by_platform"] == {"linkedin": 3}
    assert result["connected_by_platform"] == {"linkedin": 2}
    assert [a["connected"] for a in result["accounts"]] == [True, False, True]
