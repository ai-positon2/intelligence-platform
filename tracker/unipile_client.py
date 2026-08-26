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

Base URL is configurable (UNIPILE_DSN), not hardcoded to the shared
api.unipile.com gateway: Unipile issues each account a dedicated DSN
(https://apiNN.unipile.com:PORT) from their dashboard, and a key that is
valid for that DSN is not necessarily valid against the shared gateway (see
this repo's plan file for the live probe that surfaced this).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# Falls back to the shared gateway if UNIPILE_DSN isn't set. Known, as of
# this module's introduction, NOT to authenticate the key this deployment
# was first given -- set UNIPILE_DSN explicitly to the account's own
# dashboard-issued host once that's available. Kept as a fallback rather
# than making DSN required so probe() still reports a clear "wrong host,
# not just wrong key" style error instead of "not configured".
_DEFAULT_BASE = "https://api.unipile.com"

_TIMEOUT = 30

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
    return (os.environ.get("UNIPILE_DSN", "") or _DEFAULT_BASE).rstrip("/")


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
        return ("Unipile rejected our API key (HTTP 401). This usually means the key needs "
                "UNIPILE_DSN set to this account's own dashboard-issued host -- the shared "
                "api.unipile.com gateway does not accept every account's key.")
    if kind == ERR_HTTP and status == 404:
        return "Unipile returned 404 for this route -- the API path may have changed."
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
    """Unipile's list endpoints paginate under one of a few common key names.
    Tolerant on purpose: a bare list is also accepted, since which shape this
    endpoint actually uses hasn't been confirmed against a real connected
    account yet (see this feature's plan file)."""
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
    data, err = _request("GET", "/v2/accounts")
    if err is not None:
        return None, err
    accounts = _accounts_from(data)
    if accounts is None:
        return None, _err(ERR_SHAPE, "HTTP 200 but no account list. Top-level keys: %s" %
                          (sorted(data.keys())[:12] if _is_dict(data) else type(data).__name__))
    return accounts, None


def accounts_by_platform(accounts: list[dict]) -> dict[str, list[dict]]:
    """Group a list_accounts() result by platform, lower-cased, for the admin
    Data Sources panel and for sci_pipeline's per-platform availability
    check. Unipile's own field name for the platform (`type` vs `provider`)
    hasn't been confirmed against a real account yet -- both are checked."""
    by_platform: dict[str, list[dict]] = {}
    for acct in accounts or []:
        platform = str(acct.get("type") or acct.get("provider") or "").lower()
        if not platform:
            continue
        by_platform.setdefault(platform, []).append(acct)
    return by_platform


def is_available(platform: str) -> bool:
    """Whether SOME connected account can serve this platform right now --
    the gate tracker/sci_pipeline.py's per-platform collectors check before
    ever calling list_posts. A live call, not a cached flag, by design: see
    this feature's plan file on why no local connection-state table exists."""
    accounts, err = list_accounts()
    if err is not None or not accounts:
        return False
    return platform.lower() in accounts_by_platform(accounts)


def create_hosted_auth_link(providers: list[str], success_redirect_url: str | None = None,
                            failure_redirect_url: str | None = None,
                            name: str | None = None) -> tuple[dict | None, dict | None]:
    """Generate a Unipile hosted-auth URL for a human to open and log into one
    of `providers` (e.g. ["LINKEDIN", "INSTAGRAM"]) through. This is the only
    way an account gets connected -- nothing in this codebase can complete
    that login on someone's behalf, by design (see the plan file on why).
    The exact request body Unipile expects has not been confirmed against a
    real 200 response yet (the probing done while planning this could only
    confirm the route exists, not its schema, since the supplied key wasn't
    authenticating) -- treat this as a best-effort shape pending that
    confirmation, not a fully verified contract."""
    body: dict = {"type": "create", "providers": providers, "api_url": _dsn()}
    if success_redirect_url:
        body["success_redirect_url"] = success_redirect_url
    if failure_redirect_url:
        body["failure_redirect_url"] = failure_redirect_url
    if name:
        body["name"] = name
    data, err = _request("POST", "/v2/hosted/accounts/link", json_body=body)
    if err is not None:
        return None, err
    if not _is_dict(data) or not data.get("url"):
        return None, _err(ERR_SHAPE, "HTTP 200 but no url in response. Keys: %s" %
                          (sorted(data.keys())[:12] if _is_dict(data) else type(data).__name__))
    return data, None


# The live v2 posts-listing path hasn't been confirmed against a real
# connected account -- Unipile's own docs describe /api/v1/users/{id}/posts,
# but this client's other two endpoints (/v2/accounts, /v2/hosted/accounts/
# link) proved the docs lag the live v2 API by at least one path segment.
# CONFIRM THIS against a real 200 response before relying on it; see the
# plan file's Verification section.
_POSTS_PATH = "/v2/users/{identifier}/posts"


def list_posts(account_id: str, identifier: str, is_company: bool = True,
               cursor: str | None = None, limit: int = 50) -> tuple[dict | None, dict | None]:
    """Recent posts for `identifier` (a LinkedIn internal id or an Instagram
    username), fetched through `account_id`'s connected session. Returns the
    raw parsed response (caller normalizes) since the exact envelope shape
    (a bare list vs. {"items": [...], "cursor": ...}) is one of the things
    _POSTS_PATH's docstring flags as unconfirmed."""
    params: dict[str, Any] = {"account_id": account_id, "limit": max(1, min(limit, 100))}
    if is_company:
        params["is_company"] = "true"
    if cursor:
        params["cursor"] = cursor
    return _request("GET", _POSTS_PATH.format(identifier=identifier), params=params)


def probe() -> dict:
    """Prove the Unipile integration end to end and report exactly where it
    fails, in the shape app.py's other vendor self-tests (_apollo_selftest,
    _arena_selftest) established. Free -- list_accounts() needs no connected
    account and spends nothing."""
    key = _api_key()
    out: dict = {"configured": bool(key), "key_len": len(key), "dsn": _dsn(),
                "elapsed_ms": 0, "ok": False, "accounts": [], "by_platform": {},
                "error_kind": "", "error": ""}
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
    by_platform = accounts_by_platform(accounts or [])
    out["ok"] = True
    out["accounts"] = [{"id": a.get("id"), "platform": (a.get("type") or a.get("provider") or "").lower(),
                        "status": a.get("status") or a.get("state")} for a in (accounts or [])]
    out["by_platform"] = {p: len(rows) for p, rows in by_platform.items()}
    return out
