"""Unipile post-fetching transport: resolve an account for a platform, then
paginate its posts endpoint to a raw item list. Mirrors
tracker/apify_transport.py's role -- actor-agnostic mechanics there,
account/pagination-agnostic mechanics here -- so
tracker/sci_source_linkedin_unipile.py and
tracker/sci_source_instagram_unipile.py stay as thin as the Apify adapters,
owning only their identifier resolution and normalize().
"""

from __future__ import annotations

import logging

from tracker import unipile_client

logger = logging.getLogger(__name__)


class UnipileTransportError(Exception):
    """Raised by fetch_posts(strict=True) so callers can distinguish 'the
    vendor call failed' from 'the vendor call succeeded with no rows' --
    same contract as apify_transport.ApifyTransportError."""


def _items_from(payload) -> list[dict] | None:
    """Same tolerant-shape reasoning as unipile_client._accounts_from: the
    live posts response envelope hasn't been confirmed yet (see
    unipile_client._POSTS_PATH's docstring), so this accepts a bare list or
    any of the common paginated-list key names rather than assuming one."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "posts", "data"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return None


def _next_cursor(payload) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("cursor", "next_cursor", "paging_cursor"):
        v = payload.get(key)
        if isinstance(v, str) and v:
            return v
    return None


def account_for_platform(platform: str) -> str | None:
    """The first connected account id that can serve `platform`, or None if
    none is connected. A live lookup (see unipile_client.is_available's
    docstring on why this repo deliberately has no local connection-state
    table to go stale)."""
    accounts, err = unipile_client.list_accounts()
    if err is not None or not accounts:
        return None
    by_platform = unipile_client.accounts_by_platform(accounts)
    matches = by_platform.get(platform.lower()) or []
    return str(matches[0]["id"]) if matches and matches[0].get("id") is not None else None


def fetch_posts(identifier: str, platform: str, is_company: bool = True,
                max_posts: int = 40, max_pages: int = 5, strict: bool = False) -> list[dict]:
    """Resolve a connected account for `platform`, then page
    unipile_client.list_posts up to `max_posts` raw items (or `max_pages`
    pages, whichever comes first -- a page-count ceiling, not just a post-
    count one, since a malformed response that never advances the cursor
    must not loop forever).

    strict=True re-raises any resolution/transport failure instead of
    returning [], for the exact reason apify_transport.run_actor_and_wait's
    strict flag exists: an empty result must never be shown to a person as
    "this company has no posts here" when the real story is "the vendor call
    never actually ran"."""
    try:
        account_id = account_for_platform(platform)
        if not account_id:
            raise UnipileTransportError(
                "No Unipile account is connected for %s." % platform)
        items: list[dict] = []
        cursor = None
        for _ in range(max_pages):
            data, err = unipile_client.list_posts(
                account_id, identifier, is_company=is_company, cursor=cursor,
                limit=min(max_posts - len(items), 100) or 100)
            if err is not None:
                raise UnipileTransportError(unipile_client.describe_error(err))
            page_items = _items_from(data)
            if page_items is None:
                raise UnipileTransportError(
                    "Unipile posts response for %r was not in a recognised shape." % identifier)
            items.extend(page_items)
            if len(items) >= max_posts:
                break
            cursor = _next_cursor(data)
            if not cursor:
                break
        return items[:max_posts]
    except Exception as e:
        logger.warning("unipile_transport: fetch_posts failed for %s/%s: %s", platform, identifier, e)
        if strict:
            raise UnipileTransportError(str(e)) from e
        return []
