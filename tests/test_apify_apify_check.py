"""The Apify self-test: _sci_apify_selftest (app.py) and its two free,
read-only vendor calls (tracker/apify_transport.probe_token, .check_actor).

Free on every leg, on purpose: /v2/users/me confirms a token without
starting anything, and /v2/acts/<id> is a metadata read, never a run --
this whole check must never start or pay for a single Apify scrape. Also
covers the "owner/name" -> "owner~name" actor-id normalization fix
(_normalize_actor_id), confirmed live and unauthenticated against
api.apify.com: the REST API 404s on a literal slash in that path segment,
which every DEFAULT_ACTOR_ID in the sci_source_*.py adapters is written in.
"""

import os
import sys
from unittest.mock import MagicMock, patch

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
from tracker import apify_transport  # noqa: E402

ROUTE = "/p2/admin/external-usage/sci-apify-check"


def _client(email):
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": email, "name": "T"}
    return c


def _resp(json_data, status_code=200):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = json_data
    m.raise_for_status.return_value = None
    return m


# ── _normalize_actor_id ─────────────────────────────────────────────────

def test_normalize_replaces_only_the_first_slash():
    assert apify_transport._normalize_actor_id("apify/facebook-posts-scraper") == "apify~facebook-posts-scraper"


def test_normalize_leaves_a_no_slash_id_untouched():
    assert apify_transport._normalize_actor_id("apify~facebook-posts-scraper") == "apify~facebook-posts-scraper"
    assert apify_transport._normalize_actor_id("aBcD1234EfGh5678") == "aBcD1234EfGh5678"


def test_normalize_handles_empty_and_none():
    assert apify_transport._normalize_actor_id("") == ""
    assert apify_transport._normalize_actor_id(None) is None


@patch("tracker.apify_transport.requests.post")
def test_start_run_sends_the_normalized_id_in_the_url(mock_post):
    mock_post.return_value = _resp({"data": {"id": "run1"}})
    apify_transport._start_run("apify/facebook-posts-scraper", {}, "tok")
    url = mock_post.call_args[0][0]
    assert url == "https://api.apify.com/v2/acts/apify~facebook-posts-scraper/runs"
    assert "/" not in url.split("/acts/")[1].split("/runs")[0]


# ── probe_token ──────────────────────────────────────────────────────────

def test_probe_token_with_no_token_fails_without_a_network_call():
    data, err = apify_transport.probe_token("")
    assert data is None
    assert err


@patch("tracker.apify_transport.requests.get")
def test_probe_token_success_hits_users_me(mock_get):
    mock_get.return_value = _resp({"data": {"username": "position2"}})
    data, err = apify_transport.probe_token("tok")
    assert err is None
    assert data == {"username": "position2"}
    assert mock_get.call_args[0][0] == "https://api.apify.com/v2/users/me"


@patch("tracker.apify_transport.requests.get")
def test_probe_token_401_is_a_clear_rejection_not_a_generic_error(mock_get):
    mock_get.return_value = _resp({}, status_code=401)
    data, err = apify_transport.probe_token("bad-tok")
    assert data is None
    assert "401" in err


@patch("tracker.apify_transport.requests.get")
def test_probe_token_network_failure_never_raises(mock_get):
    import requests
    mock_get.side_effect = requests.RequestException("boom")
    data, err = apify_transport.probe_token("tok")
    assert data is None
    assert "boom" in err


# ── check_actor ──────────────────────────────────────────────────────────

@patch("tracker.apify_transport.requests.get")
def test_check_actor_success_normalizes_the_id_in_the_url(mock_get):
    mock_get.return_value = _resp({"data": {"name": "facebook-posts-scraper"}})
    data, err = apify_transport.check_actor("apify/facebook-posts-scraper", "tok")
    assert err is None
    assert data["name"] == "facebook-posts-scraper"
    assert mock_get.call_args[0][0] == "https://api.apify.com/v2/acts/apify~facebook-posts-scraper"


@patch("tracker.apify_transport.requests.get")
def test_check_actor_404_names_the_actor_it_could_not_find(mock_get):
    mock_get.return_value = _resp({}, status_code=404)
    data, err = apify_transport.check_actor("apify/nonexistent-scraper", "tok")
    assert data is None
    assert "apify/nonexistent-scraper" in err


def test_check_actor_with_no_id_fails_without_a_network_call():
    data, err = apify_transport.check_actor("", "tok")
    assert data is None
    assert err


# ── _sci_apify_selftest ─────────────────────────────────────────────────

def test_selftest_reports_not_configured_without_calling_the_network(monkeypatch):
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    out = appmod._sci_apify_selftest()
    assert out["configured"] is False
    assert out["ok"] is False
    assert "APIFY_API_TOKEN" in out["error"]


def test_selftest_surfaces_a_rejected_token(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "bad-tok")
    monkeypatch.setattr(apify_transport, "probe_token", lambda tok: (None, "Apify rejected this token (401 Unauthorized)."))
    out = appmod._sci_apify_selftest()
    assert out["configured"] is True
    assert out["ok"] is False
    assert "401" in out["error"]
    assert out["actors"] == {}


def test_selftest_reports_every_platform_when_the_token_is_good(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "good-tok")
    monkeypatch.setattr(apify_transport, "probe_token", lambda tok: ({"username": "position2"}, None))

    def fake_check_actor(actor_id, tok):
        if actor_id == "apidojo/tweet-scraper":
            return None, "Actor apidojo/tweet-scraper not found or not accessible with this token."
        return {"name": actor_id.split("/")[-1]}, None
    monkeypatch.setattr(apify_transport, "check_actor", fake_check_actor)

    out = appmod._sci_apify_selftest()
    assert out["ok"] is True
    assert out["account"] == "position2"

    # Facebook/TikTok/Instagram have real defaults and are accessible.
    for platform in ("facebook", "tiktok", "instagram"):
        entry = out["actors"][platform]
        assert entry["enabled"] is True
        assert entry["accessible"] is True

    # X's default is (in this fake) not accessible -- a real failure, distinct
    # from LinkedIn's "not configured at all".
    assert out["actors"]["x"]["enabled"] is True
    assert out["actors"]["x"]["accessible"] is False
    assert "not found" in out["actors"]["x"]["error"]

    # LinkedIn is feature-flagged off by design -- SCI_APIFY_LINKEDIN_ACTOR_ID
    # has no default (see tracker/sci_source_linkedin.py). This must render
    # as "not used", never as a failure alongside X's genuine one.
    li = out["actors"]["linkedin"]
    assert li["enabled"] is False
    assert li["accessible"] is False
    assert li["error"] == ""


def test_selftest_honors_a_manually_set_linkedin_actor_id(monkeypatch):
    """LinkedIn stays off unless someone deliberately sets the env var --
    when they do, the self-test must check it like any other platform."""
    monkeypatch.setenv("APIFY_API_TOKEN", "good-tok")
    monkeypatch.setenv("SCI_APIFY_LINKEDIN_ACTOR_ID", "someone/linkedin-actor")
    monkeypatch.setattr(apify_transport, "probe_token", lambda tok: ({"username": "position2"}, None))
    monkeypatch.setattr(apify_transport, "check_actor", lambda actor_id, tok: ({"name": "linkedin-actor"}, None))
    out = appmod._sci_apify_selftest()
    assert out["actors"]["linkedin"]["enabled"] is True
    assert out["actors"]["linkedin"]["accessible"] is True


def test_selftest_never_raises_when_the_transport_blows_up(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "tok")

    def boom(tok):
        raise RuntimeError("unexpected")
    monkeypatch.setattr(apify_transport, "probe_token", boom)
    out = appmod._sci_apify_selftest()
    assert out["ok"] is False
    assert "RuntimeError" in out["error"]


# ── the route ────────────────────────────────────────────────────────────

def test_route_is_admin_only():
    c = _client("nobody@position2.com")
    r = c.post(ROUTE)
    assert r.status_code == 403


def test_route_requires_login():
    c = appmod.app.test_client()
    r = c.post(ROUTE)
    assert r.status_code in (302, 401)


def test_route_get_is_not_allowed():
    """POST-only, matching every other self-test next to it -- so a crawler
    or a prefetch can never trigger a (free, but still real) vendor call."""
    admin = sorted(appmod.ADMIN_EMAILS)[0]
    c = _client(admin)
    r = c.get(ROUTE)
    assert r.status_code == 405


def test_route_returns_the_selftest_json_for_an_admin(monkeypatch):
    monkeypatch.delenv("APIFY_API_TOKEN", raising=False)
    admin = sorted(appmod.ADMIN_EMAILS)[0]
    c = _client(admin)
    body = c.post(ROUTE).get_json()
    assert body["configured"] is False
    assert "actors" in body
