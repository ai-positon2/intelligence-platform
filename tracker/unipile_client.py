"""Unipile API client -- data fetching only, no business logic.

Unipile is architecturally different from every other vendor in this repo: it
does not scrape anonymously. It connects one real, authenticated account per
platform (via a hosted auth link a human clicks through) and then answers API
calls "as" that account. tracker/sci_source_linkedin_unipile.py and
tracker/sci_source_instagram_unipile.py are the only callers of the
collection-facing functions here; tracker/sci_pipeline.py decides per platform
whether a Unipile account is actually available before ever calling them.

Follows tracker/arena_client.py's shape, not tracker/apollo_client.py's: a
module-level _api_key()/_dsn() rather than an explicit parameter on every
call, because this is a single self-contained feature's vendor, not a key
shared across unrelated features. Never raises -- every public function
returns (data, err) exactly like arena_client's (parsed, error) pairs, with
the same typed-error-kind shape, so app.py/sci_pipeline.py can reuse the same
"describe this to a human" pattern already established there.

Base URL is configurable (UNIPILE_DSN) and REQUIRED in practice: Unipile
issues each workspace a dedicated host (https://apiNN.unipile.com:PORT) from
their dashboard, and a key valid for that host is not valid against the
shared api.unipile.com gateway, which answers 401 for it.

Every route below was confirmed against a real 200/201 from a live DSN on
2026-09-01, replacing the guesses this module shipped with:

  GET  /api/v1/accounts                     -> {"object":"AccountList","items":[...],"cursor":...}
  GET  /api/v1/linkedin/company/{slug}      -> {"object":"CompanyProfile","id":"60223",...}
  GET  /api/v1/users/{id}/posts             -> {"object":"PostList","items":[...],"cursor":...}
  POST /api/v1/hosted/accounts/link         -> 201 {"object":"HostedAuthUrl","url":...}

Two of those corrected a wrong assumption, so they are worth naming: the live
API is on /api/v1, NOT the /v2 this module first guessed (the earlier probe
that suggested /v2 was run against the shared gateway, a different service),
and the hosted-auth link REQUIRES expiresOn -- without it every Connect
button returned 400.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Falls back to the shared gateway if UNIPILE_DSN isn't set. Known NOT to
# authenticate a workspace-issued key -- kept as a fallback rather than
# making DSN required so probe() still reports a clear "wrong host, not just
# wrong key" style error instead of "not configured".
_DEFAULT_BASE = "https://api.unipile.com"

# Every route lives under this prefix. Confirmed live; /v2 and /v1 both 404.
_API = "/api/v1"

_TIMEOUT = 30

# How long a generated hosted-auth link is asked to stay valid. Unipile's own
# schema notes every link dies at their daily restart regardless of what is
# asked for here, and that a fresh link must be generated per click -- which
# app.py already does -- so this is a ceiling, not a lifetime.
_AUTH_LINK_TTL = timedelta(hours=1)

# The value sources[].status carries for an account that can actually serve
# requests. Anything else (notably "CREDENTIALS", meaning the login has
# lapsed and a human must reconnect it) is a connected-in-name-only account:
# it is listed by /accounts but every call made through it fails.
ACCOUNT_OK = "OK"

# Failure kinds -- same vocabulary as arena_client's, so any caller that
# already knows how to render one vendor's error dict knows how to render
# this one too.
ERR_NOT_CONFIGURED = "not_configured"
ERR_TIMEOUT = "timeout"
ERR_HTTP = "http_status"
ERR_NETWORK = "network"
ERR_UNPARSABLE = "unparsable"
ERR_SHAPE = "unexpected_shape"


def _api_key() -> str:
    return os.environ.get("UNIPILE_API_KEY", "")


def _dsn() -> str:
    """The configured host, normalized to something requests can actually
    call. Unipile's dashboard shows the DSN as a bare host:port
    ("api42.unipile.com:13900"), and pasting that verbatim into the env var
    is the obvious thing to do -- but requests rejects a schemeless URL with
    MissingSchema, which would surface as a network error rather than as
    "your DSN is missing https://". Prepending it here means the value can
    be copied straight out of the dashboard."""
    raw = (os.environ.get("UNIPILE_DSN", "") or "").strip().rstrip("/")
    if not raw:
        return _DEFAULT_BASE
    if "://" not in raw:
        raw = "https://" + raw
    return raw.rstrip("/")


def _is_dict(v: Any) -> bool:
    return isinstance(v, dict)


def _err(kind: str, detail: str = "", status: int | None = None) -> dict:
    """One failure, described. `detail` is for operators (logs, the admin
    self-test) and never carries the API key, which only ever lives in a
    request header."""
    return {"kind": kind, "status": status, "detail": detail[:500]}


def describe_error(err: dict | None) -> str:
    """A failure kind rendered for whoever is looking at the screen."""
    if not _is_dict(err):
        return ""
    kind, status = err.get("kind"), err.get("status")
    if kind == ERR_NOT_CONFIGURED:
        return "Unipile is not configured on this deployment: UNIPILE_API_KEY is missing."
    if kind == ERR_HTTP and status == 401:
        return ("Unipile rejected our API key (HTTP 401). This usually means UNIPILE_DSN is "
                "missing or wrong -- a key is valid only against its own workspace host "
                "(https://apiNN.unipile.com:PORT), never the shared api.unipile.com gateway.")
    if kind == ERR_HTTP and status == 404:
        return "Unipile returned 404 for this route -- the API path may have changed."
    if kind == ERR_HTTP and status == 422:
        return ("Unipile could not reach that profile. The identifier may be wrong, or the "
                "profile may be private or restricted to the connected account.")
    if kind == ERR_HTTP and status == 429:
        return "Unipile is rate-limiting us. Try again in a minute."
    if kind == ERR_HTTP:
        return "Unipile returned an error (HTTP %s). This is on their side, not ours." % status
    if kind == ERR_TIMEOUT:
        return "Unipile did not respond in time. Try again in a moment."
    if kind == ERR_NETWORK:
        return "Unipile could not be reached. Try again in a moment."
    if kind == ERR_UNPARSABLE:
        return "Unipile's response could not be read."
    if kind == ERR_SHAPE:
        return "Unipile answered, but not in the shape this client expects."
    return "Unipile is unavailable right now."


def _request(method: str, path: str, json_body: dict | None = None,
            params: dict | None = None, timeout: int = _TIMEOUT) -> tuple[Any, dict | None]:
    """One HTTP call to Unipile, parsed. Returns (data, None) or (None, err).
    Never raises."""
    key = _api_key()
    if not key:
        return None, _err(ERR_NOT_CONFIGURED, "UNIPILE_API_KEY is not set on this deployment.")
    url = f"{_dsn()}{path}"
    headers = {"X-API-KEY": key, "accept": "application/json"}
    try:
        resp = requests.request(method, url, headers=headers, json=json_body,
                                params=params, timeout=timeout)
    except requests.Timeout as e:
        return None, _err(ERR_TIMEOUT, "No response within %ss: %s" % (timeout, e))
    except requests.RequestException as e:
        return None, _err(ERR_NETWORK, "%s: %s" % (type(e).__name__, e))
    try:
        status = int(getattr(resp, "status_code", 200) or 200)
        if status >= 400:
            body = (getattr(resp, "text", "") or "").strip()
            return None, _err(ERR_HTTP, "HTTP %s. Body: %s" % (status, body[:300] or "(empty)"), status=status)
        if not (resp.text or "").strip():
            return {}, None
        return resp.json(), None
    except Exception as e:
        return None, _err(ERR_UNPARSABLE, "%s: %s" % (type(e).__name__, e))


def _accounts_from(payload: Any) -> list[dict] | None:
    """The live envelope is {"object":"AccountList","items":[...]}. A bare
    list and the other common key names stay accepted: this costs nothing
    and means a paginated-shape change reads as a shape error at one place
    rather than as "no accounts connected" everywhere."""
    if isinstance(payload, list):
        return payload
    if _is_dict(payload):
        for key in ("items", "accounts", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return None


def list_accounts() -> tuple[list[dict] | None, dict | None]:
    """Every account connected to this Unipile workspace, across all
    platforms. Free -- no connected account required to call this, so it's
    also this client's cheapest possible connectivity check (see probe())."""
    data, err = _request("GET", f"{_API}/accounts")
    if err is not None:
        return None, err
    accounts = _accounts_from(data)
    if accounts is None:
        return None, _err(ERR_SHAPE, "HTTP 200 but no account list. Top-level keys: %s" %
                          (sorted(data.keys())[:12] if _is_dict(data) else type(data).__name__))
    return accounts, None


def account_platform(acct: dict) -> str:
    """The platform an account serves, lower-cased. Live accounts carry it
    as `type` ("LINKEDIN"); `provider` is kept as a fallback only."""
    return str((acct or {}).get("type") or (acct or {}).get("provider") or "").lower()


def account_status(acct: dict) -> str:
    """An account's real usability, read from sources[].status.

    This is NOT a top-level field, which matters: reading acct["status"]
    returns None for every live account, so a caller that trusted it would
    treat a lapsed login exactly like a healthy one. In a real workspace
    that is not a rare edge case -- 6 of the 17 accounts on the deployment
    this was confirmed against were sitting at "CREDENTIALS"."""
    sources = (acct or {}).get("sources")
    if isinstance(sources, list):
        statuses = [str(s.get("status") or "").upper() for s in sources if isinstance(s, dict)]
        if any(s == ACCOUNT_OK for s in statuses):
            return ACCOUNT_OK
        if statuses:
            return statuses[0]
    return str((acct or {}).get("status") or (acct or {}).get("state") or "").upper()


def is_connected(acct: dict) -> bool:
    """Whether calls made through this account will actually work."""
    return account_status(acct) == ACCOUNT_OK


def accounts_by_platform(accounts: list[dict], connected_only: bool = False) -> dict[str, list[dict]]:
    """Group a list_accounts() result by platform, lower-cased, for the admin
    Data Sources panel and for sci_pipeline's per-platform availability
    check. connected_only drops accounts whose login has lapsed."""
    by_platform: dict[str, list[dict]] = {}
    for acct in accounts or []:
        platform = account_platform(acct)
        if not platform:
            continue
        if connected_only and not is_connected(acct):
            continue
        by_platform.setdefault(platform, []).append(acct)
    return by_platform


def is_available(platform: str) -> bool:
    """Whether some WORKING account can serve this platform right now -- the
    gate tracker/sci_pipeline.py's per-platform collectors check before ever
    calling list_posts. A live call, not a cached flag, by design: see this
    feature's plan file on why no local connection-state table exists.

    Lapsed accounts do not count. They are still listed by /accounts, so
    counting them would report LinkedIn as available and then fail every
    single collection through it."""
    accounts, err = list_accounts()
    if err is not None or not accounts:
        return False
    return platform.lower() in accounts_by_platform(accounts, connected_only=True)


def _expires_on(now: datetime | None = None) -> str:
    """Unipile's expiresOn, in the exact format its schema's regex demands:
    millisecond-precision ISO 8601 in UTC with a literal Z. datetime's own
    isoformat() emits microseconds and +00:00, both of which it rejects."""
    at = (now or datetime.now(timezone.utc)) + _AUTH_LINK_TTL
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (at.microsecond // 1000)


def create_hosted_auth_link(providers: list[str], success_redirect_url: str | None = None,
                            failure_redirect_url: str | None = None,
                            name: str | None = None) -> tuple[dict | None, dict | None]:
    """Generate a Unipile hosted-auth URL for a human to open and log into one
    of `providers` (e.g. ["LINKEDIN", "INSTAGRAM"]) through. This is the only
    way an account gets connected -- nothing in this codebase can complete
    that login on someone's behalf, by design (see the plan file on why).

    expiresOn is required, not optional: omitting it returns 400 for every
    request. Confirmed against a real 201 response."""
    body: dict = {"type": "create", "providers": providers, "api_url": _dsn(),
                  "expiresOn": _expires_on()}
    if success_redirect_url:
        body["success_redirect_url"] = success_redirect_url
    if failure_redirect_url:
        body["failure_redirect_url"] = failure_redirect_url
    if name:
        body["name"] = name
    data, err = _request("POST", f"{_API}/hosted/accounts/link", json_body=body)
    if err is not None:
        return None, err
    if not _is_dict(data) or not data.get("url"):
        return None, _err(ERR_SHAPE, "HTTP 200 but no url in response. Keys: %s" %
                          (sorted(data.keys())[:12] if _is_dict(data) else type(data).__name__))
    return data, None


def get_company(identifier: str, account_id: str) -> tuple[dict | None, dict | None]:
    """A LinkedIn company page by its vanity slug ("position2"), fetched
    through `account_id`'s connected session.

    This exists because the posts endpoint below does NOT accept a vanity
    slug for a company: /users/position2/posts answers 422
    invalid_recipient, while /users/60223/posts answers 200. This call is
    how the slug becomes that number."""
    data, err = _request("GET", f"{_API}/linkedin/company/{identifier}",
                         params={"account_id": account_id})
    if err is not None:
        return None, err
    if not _is_dict(data) or not data.get("id"):
        return None, _err(ERR_SHAPE, "HTTP 200 but no company id in response. Keys: %s" %
                          (sorted(data.keys())[:12] if _is_dict(data) else type(data).__name__))
    return data, None


def list_posts(account_id: str, identifier: str, is_company: bool = True,
               cursor: str | None = None, limit: int = 50) -> tuple[dict | None, dict | None]:
    """Recent posts for `identifier`, fetched through `account_id`'s
    connected session. For a company `identifier` is its NUMERIC id, not its
    vanity slug (see get_company). Returns the raw parsed response, since
    normalizing a platform's post shape is each adapter's job.

    Confirmed live: the envelope is {"object":"PostList","items":[...],
    "cursor":...}; limit is capped at 100 by the vendor (asking for 100
    returns 99-100); the cursor pages cleanly with no overlap between
    pages."""
    params: dict[str, Any] = {"account_id": account_id, "limit": max(1, min(limit, 100))}
    if is_company:
        params["is_company"] = "true"
    if cursor:
        params["cursor"] = cursor
    return _request("GET", f"{_API}/users/{identifier}/posts", params=params)


def probe() -> dict:
    """Prove the Unipile integration end to end and report exactly where it
    fails, in the shape app.py's other vendor self-tests (_apollo_selftest,
    _arena_selftest) established. Free -- list_accounts() needs no connected
    account and spends nothing.

    Reports each account's real status and counts working accounts
    separately from listed ones, because "LinkedIn is connected" and
    "LinkedIn collection will work" are different claims and the admin panel
    must not print the first while meaning the second."""
    key = _api_key()
    out: dict = {"configured": bool(key), "key_len": len(key), "dsn": _dsn(),
                "elapsed_ms": 0, "ok": False, "accounts": [], "by_platform": {},
                "connected_by_platform": {}, "error_kind": "", "error": ""}
    if not key:
        out["error_kind"] = ERR_NOT_CONFIGURED
        out["error"] = describe_error(_err(ERR_NOT_CONFIGURED))
        return out
    started = time.monotonic()
    try:
        accounts, err = list_accounts()
    except Exception as e:  # defensive: probe must never 500 the admin page
        out["error_kind"] = "exception"
        out["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
        return out
    out["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    if err is not None:
        out["error_kind"] = err.get("kind") or ""
        out["error"] = describe_error(err)
        return out
    accounts = accounts or []
    out["ok"] = True
    out["accounts"] = [{"id": a.get("id"), "name": a.get("name"),
                        "platform": account_platform(a), "status": account_status(a),
                        "connected": is_connected(a)} for a in accounts]
    out["by_platform"] = {p: len(rows) for p, rows in accounts_by_platform(accounts).items()}
    out["connected_by_platform"] = {
        p: len(rows) for p, rows in accounts_by_platform(accounts, connected_only=True).items()}
    return out
