"""Apify actor transport: start a run, poll it to completion, fetch its
dataset. Actor-agnostic; every platform-specific scraper (sci_source_*.py)
builds its own actor input and normalizes its own output on top of this.

Mirrors tracker/apollo_client.py's convention: the token is an explicit
parameter on every public function, never read from the environment in here
-- the caller (app.py / sci_pipeline.py) reads APIFY_API_TOKEN and decides
what an unset token means for that call site (a cheap check degrades to [],
a user-initiated action returns a clear "not configured" error).
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.apify.com/v2"


class ApifyTransportError(Exception):
    """Raised by run_actor_and_wait(strict=True) so callers can distinguish
    'the vendor call failed' from 'the vendor call succeeded with no rows'."""


def _headers(token: str) -> dict:
    return {"Authorization": "Bearer " + token, "Content-Type": "application/json"}


def _normalize_actor_id(actor_id: str) -> str:
    """Every DEFAULT_ACTOR_ID in sci_source_*.py is written in Apify's own
    "owner/name" console-URL form (e.g. "apify/facebook-posts-scraper"),
    because that is the form Apify's docs and store pages show and the one a
    human would type into an env var override. The REST API does not accept
    that form as a single path segment: /v2/acts/<actor_id> and
    /v2/acts/<actor_id>/runs both 404 on a literal slash and require the
    owner and name joined with "~" instead (confirmed live, unauthenticated,
    against api.apify.com). Normalize at the transport boundary so every
    caller and every env var can keep writing the readable slash form."""
    return actor_id.replace("/", "~", 1) if actor_id and "/" in actor_id else actor_id


def _start_run(actor_id: str, run_input: dict, token: str) -> str:
    url = f"{_BASE_URL}/acts/{_normalize_actor_id(actor_id)}/runs"
    resp = requests.post(url, json=run_input, headers=_headers(token), timeout=30)
    resp.raise_for_status()
    return resp.json()["data"]["id"]


def _poll_run(run_id: str, token: str, timeout: int, poll_interval: int) -> dict:
    url = f"{_BASE_URL}/actor-runs/{run_id}"
    deadline = time.monotonic() + timeout
    while True:
        resp = requests.get(url, headers=_headers(token), timeout=30)
        resp.raise_for_status()
        data = resp.json()["data"]
        status = data.get("status")
        if status in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            return data
        if time.monotonic() >= deadline:
            raise ApifyTransportError(
                "Apify run %s did not finish within %ss (last status: %s)" % (run_id, timeout, status))
        time.sleep(poll_interval)


def _fetch_dataset_items(dataset_id: str, token: str) -> list[dict]:
    url = f"{_BASE_URL}/datasets/{dataset_id}/items"
    resp = requests.get(url, headers=_headers(token), params={"format": "json"}, timeout=60)
    resp.raise_for_status()
    items = resp.json()
    return items if isinstance(items, list) else []


def run_actor_and_wait(actor_id: str, run_input: dict, token: str, timeout: int = 300,
                       poll_interval: int = 5, strict: bool = False) -> list[dict]:
    """Start `actor_id` with `run_input`, poll until it finishes (or `timeout`
    seconds elapse), and return its dataset items as a list of raw dicts.

    strict=True re-raises any transport/actor failure instead of returning [].
    Use strict=True anywhere an empty result would otherwise be shown to a
    person as "this company has no posts here" -- [] must never conflate a
    scraper that couldn't run with a platform confirmed to have nothing.
    On strict=False, any failure is logged and swallowed to [] so the caller
    can mark that platform scrape_failed and move on to the next platform.
    """
    try:
        run_id = _start_run(actor_id, run_input, token)
        run = _poll_run(run_id, token, timeout, poll_interval)
        if run.get("status") != "SUCCEEDED":
            raise ApifyTransportError(
                "Apify actor %s run %s ended with status %s" % (actor_id, run_id, run.get("status")))
        dataset_id = run.get("defaultDatasetId")
        if not dataset_id:
            raise ApifyTransportError("Apify run %s succeeded with no defaultDatasetId" % run_id)
        return _fetch_dataset_items(dataset_id, token)
    except Exception as e:
        # Deliberately broad: a transport error, a malformed vendor response
        # (KeyError/ValueError), or anything else must all collapse to the
        # same "this scrape did not produce data" outcome -- never let an
        # unanticipated failure shape escape and take the whole platform
        # (or the whole run) down with it.
        logger.warning("apify_transport: run_actor_and_wait failed for actor %s: %s", actor_id, e)
        if strict:
            raise ApifyTransportError(str(e)) from e
        return []


def probe_token(token: str) -> tuple[dict | None, str | None]:
    """GET /v2/users/me: confirms `token` is valid without starting or
    paying for anything. Returns (account_info, None) on success, or
    (None, error_message) on any failure -- never raises, so a self-test
    route can call this directly and always get a renderable result."""
    if not token:
        return None, "No token given."
    try:
        resp = requests.get(f"{_BASE_URL}/users/me", headers=_headers(token), timeout=15)
    except requests.RequestException as e:
        return None, "Could not reach Apify: %s" % e
    if resp.status_code == 401:
        return None, "Apify rejected this token (401 Unauthorized)."
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        return None, "Apify returned HTTP %s: %s" % (resp.status_code, str(e)[:200])
    return (resp.json().get("data") or {}), None


def check_actor(actor_id: str, token: str) -> tuple[dict | None, str | None]:
    """GET /v2/acts/<actor_id>: confirms the actor exists and this token can
    see it. A metadata READ, never a run -- costs nothing and starts
    nothing, unlike run_actor_and_wait. Returns (actor_info, None) or
    (None, error_message)."""
    if not actor_id:
        return None, "No actor id given."
    try:
        resp = requests.get(f"{_BASE_URL}/acts/{_normalize_actor_id(actor_id)}",
                            headers=_headers(token), timeout=15)
    except requests.RequestException as e:
        return None, "Could not reach Apify: %s" % e
    if resp.status_code == 404:
        return None, "Actor %s not found or not accessible with this token." % actor_id
    if resp.status_code == 401:
        return None, "Apify rejected this token (401 Unauthorized)."
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        return None, "Apify returned HTTP %s: %s" % (resp.status_code, str(e)[:200])
    return (resp.json().get("data") or {}), None
