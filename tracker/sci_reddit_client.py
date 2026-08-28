"""Reddit API client for Social Creative Intelligence Analyst.

WHY OAUTH AND NOT THE PUBLIC .json ENDPOINTS: Reddit's old
"append .json to any URL" trick is gone for server traffic. Probed live
while building this, every one of https://www.reddit.com/search.json,
/user/<name>/submitted.json and /r/<sub>/about.json returned 403 with an
HTML block page rather than JSON -- from an ordinary residential IP, so a
datacenter host like Railway will not do better. The app-only OAuth token
route, by contrast, answered a deliberately bogus credential pair with a
clean {"message": "Unauthorized", "error": 401}: the route is real and
recognized, and the only missing piece is real credentials. So this module
is built on the sanctioned API and degrades honestly when unconfigured,
rather than on a scrape path that is already blocked.

Follows tracker/arena_client.py's shape rather than tracker/apollo_client
.py's: Reddit's credentials belong to this one feature, so the module reads
its own env and owns a probe() self-test, instead of taking an api_key
parameter on every call the way a key shared across many features does.

Credentials come from a Reddit "script"/"web app" registered at
https://www.reddit.com/prefs/apps:
    REDDIT_CLIENT_ID       -- the app id under the app name
    REDDIT_CLIENT_SECRET   -- the app secret
    REDDIT_USER_AGENT      -- optional; Reddit REQUIRES a descriptive,
                              unique User-Agent and throttles or blocks
                              generic/default ones far harder than it
                              throttles a well-identified client.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone

import requests

from tracker.sci_name_match import plausible_match

logger = logging.getLogger(__name__)

PLATFORM = "reddit"

_TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
_API_BASE = "https://oauth.reddit.com"
_DEFAULT_USER_AGENT = "python:position2-intelligence-platform:v1.0 (Social Creative Intelligence Analyst)"

# App-only tokens last 24h. Cached module-level with a safety margin so a
# 6-call collection does not fetch 6 tokens; Reddit counts token requests
# against the same rate budget as data requests.
_TOKEN: str | None = None
_TOKEN_EXPIRES_AT: float = 0.0
_TOKEN_MARGIN_S = 120

ERR_NOT_CONFIGURED = "not_configured"
ERR_AUTH = "auth_failed"
ERR_HTTP = "http_error"
ERR_NETWORK = "network_error"


class RedditError(RuntimeError):
    """Raised only by the strict collection path, so sci_pipeline can mark
    that one platform scrape_failed with a real reason. The resolve/search
    helpers never raise -- they degrade to None/[]."""


def _credentials() -> tuple[str, str]:
    import os
    return (os.environ.get("REDDIT_CLIENT_ID", "").strip(),
            os.environ.get("REDDIT_CLIENT_SECRET", "").strip())


def _user_agent() -> str:
    import os
    return os.environ.get("REDDIT_USER_AGENT", "").strip() or _DEFAULT_USER_AGENT


def is_configured() -> bool:
    client_id, secret = _credentials()
    return bool(client_id and secret)


def reset_token_cache() -> None:
    """Drop the cached token. Used by the tests, and by probe() so an admin
    check always exercises a real token fetch rather than reporting on a
    token minted before the credentials were last changed."""
    global _TOKEN, _TOKEN_EXPIRES_AT
    _TOKEN, _TOKEN_EXPIRES_AT = None, 0.0


def _access_token() -> str | None:
    """App-only ("client_credentials") bearer token, cached until it nearly
    expires. None when unconfigured or rejected -- never raises."""
    global _TOKEN, _TOKEN_EXPIRES_AT
    if _TOKEN and time.time() < _TOKEN_EXPIRES_AT:
        return _TOKEN
    client_id, secret = _credentials()
    if not (client_id and secret):
        return None
    try:
        resp = requests.post(
            _TOKEN_URL,
            auth=(client_id, secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": _user_agent()},
            timeout=20,
        )
        if resp.status_code != 200:
            logger.warning("sci_reddit_client: token request rejected (HTTP %s)", resp.status_code)
            return None
        payload = resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("sci_reddit_client: token request failed: %s", e)
        return None
    token = payload.get("access_token")
    if not token:
        return None
    _TOKEN = token
    _TOKEN_EXPIRES_AT = time.time() + max(60, int(payload.get("expires_in") or 3600) - _TOKEN_MARGIN_S)
    return _TOKEN


def _get(path: str, **params) -> dict | None:
    """One authenticated GET. None on any failure -- callers decide whether
    that is fatal. `raw_json=1` is always sent: without it Reddit HTML-escapes
    &, < and > inside every text field, so captions arrive as "Q&amp;A" and
    carry that straight into the report and into Claude's analysis of it."""
    token = _access_token()
    if not token:
        return None
    params = {k: v for k, v in params.items() if v is not None}
    params["raw_json"] = 1
    try:
        resp = requests.get(
            f"{_API_BASE}{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}", "User-Agent": _user_agent()},
            timeout=25,
        )
        if resp.status_code == 401:
            # The cached token was revoked or expired early; drop it so the
            # next call mints a fresh one instead of failing forever.
            reset_token_cache()
            logger.warning("sci_reddit_client: 401 on %s, token cache cleared", path)
            return None
        if resp.status_code != 200:
            logger.warning("sci_reddit_client: HTTP %s on %s", resp.status_code, path)
            return None
        return resp.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("sci_reddit_client: request failed for %s: %s", path, e)
        return None


# ── Normalization ────────────────────────────────────────────────────────
#
# Reddit's listing shape is {"data": {"children": [{"kind": "t3", "data":
# {...}}, ...]}} at every endpoint that returns posts, so one unwrapper
# serves search, /user/*/submitted and /r/*/new alike.

def _children(payload: dict | None) -> list[dict]:
    if not isinstance(payload, dict):
        return []
    listing = payload.get("data") or {}
    out = []
    for child in listing.get("children") or []:
        data = (child or {}).get("data")
        if isinstance(data, dict):
            out.append(data)
    return out


def _post_type(item: dict) -> str:
    """Reddit's own format vocabulary, kept rather than flattened into the
    other platforms' image/video/carousel set. "link" and "text" are real,
    distinct content strategies on Reddit (a link drop reads very
    differently from a long self-post), and collapsing them would hide that
    from the format-mix breakdown the report draws per platform."""
    if item.get("is_gallery"):
        return "carousel"
    if item.get("is_video") or (item.get("secure_media") or {}).get("reddit_video"):
        return "video"
    hint = item.get("post_hint") or ""
    if hint == "image":
        return "image"
    if hint in ("hosted:video", "rich:video"):
        return "video"
    if item.get("is_self"):
        return "text"
    return "link" if item.get("url") else "text"


def _media_urls(item: dict) -> list[str]:
    """Fetchable media for the vision step, best first. Reddit's `preview`
    URLs are HTML-escaped even under raw_json for legacy reasons in some
    responses, so they are unescaped here rather than at the call site."""
    urls: list[str] = []
    if item.get("is_video"):
        video = ((item.get("secure_media") or {}).get("reddit_video")
                 or (item.get("media") or {}).get("reddit_video") or {})
        fallback = video.get("fallback_url")
        if fallback:
            urls.append(fallback.split("?")[0])
    for image in ((item.get("preview") or {}).get("images") or []):
        source = (image or {}).get("source") or {}
        url = source.get("url")
        if url:
            urls.append(url.replace("&amp;", "&"))
    if item.get("is_gallery"):
        media_meta = item.get("media_metadata") or {}
        for entry in media_meta.values():
            source = (entry or {}).get("s") or {}
            url = source.get("u") or source.get("gif")
            if url:
                urls.append(url.replace("&amp;", "&"))
    url = item.get("url") or ""
    if (item.get("post_hint") == "image" or re.search(r"\.(jpg|jpeg|png|gif|webp)$", url, re.I)) and url:
        urls.append(url)
    seen, deduped = set(), []
    for u in urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    return deduped


def _thumbnail(item: dict) -> str | None:
    """Reddit puts the literal strings "self", "default", "nsfw" and "spoiler"
    in `thumbnail` where there is no real image, so a naive read of that
    field renders a broken image for every text post."""
    thumb = item.get("thumbnail") or ""
    if thumb.startswith("http"):
        return thumb.replace("&amp;", "&")
    for image in ((item.get("preview") or {}).get("images") or []):
        source = (image or {}).get("source") or {}
        if source.get("url"):
            return source["url"].replace("&amp;", "&")
    return None


def _iso(created_utc) -> str | None:
    try:
        return datetime.fromtimestamp(float(created_utc), tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def normalize(item: dict) -> dict | None:
    """One Reddit listing item -> the shared post dict every SCI adapter
    returns (see tracker/sci_pipeline.py's module docstring)."""
    post_id = item.get("id")
    if not post_id:
        return None
    title = (item.get("title") or "").strip()
    body = (item.get("selftext") or "").strip()
    # Title alone is often the whole post on a link submission, but on a
    # self-post the argument lives in the body -- fold both in, the same way
    # sci_youtube_client folds a video's description into its caption, so the
    # vision/synthesis steps see the actual copy rather than a headline.
    caption = f"{title}\n\n{body[:1500]}" if body else title
    permalink = item.get("permalink") or ""
    score = item.get("score")
    metrics = {}
    if isinstance(score, (int, float)):
        # Reddit's score is upvotes net of downvotes -- the closest thing it
        # has to a "like", and what the platform itself shows on a post.
        metrics["likes"] = int(score)
    if isinstance(item.get("num_comments"), (int, float)):
        metrics["comments"] = int(item["num_comments"])
    if isinstance(item.get("view_count"), (int, float)):
        # Almost always null on app-only auth (Reddit only exposes it to a
        # post's own moderators), so this is present-if-offered, not relied on.
        metrics["views"] = int(item["view_count"])
    return {
        "platform_post_id": str(post_id),
        "post_url": f"https://www.reddit.com{permalink}" if permalink else item.get("url"),
        "post_type": _post_type(item),
        "caption": caption,
        "posted_at": _iso(item.get("created_utc")),
        "media_urls": _media_urls(item),
        "metrics": metrics,
        "raw": {
            "title": title,
            "subreddit": item.get("subreddit"),
            "author": item.get("author"),
            "over_18": bool(item.get("over_18")),
            "upvote_ratio": item.get("upvote_ratio"),
            "num_crossposts": item.get("num_crossposts"),
            "link_flair_text": item.get("link_flair_text"),
            "domain": item.get("domain"),
            "is_self": bool(item.get("is_self")),
            "thumbnail_url": _thumbnail(item),
            "permalink": permalink,
        },
    }


# ── Resolving a company to its OWNED Reddit presence ──────────────────────
#
# A deliberate distinction that shapes this whole integration: on Reddit,
# "the company's own organic content" means submissions authored by the
# company's account (u/Acme). It does NOT mean r/Acme -- a brand's
# subreddit is overwhelmingly written by its users, not by the brand, so
# treating it as owned creative would report the community's posts as the
# company's own marketing. r/Acme is captured instead as part of the brand
# conversation (tracker/sci_reddit_pulse.py), where it belongs.

def _slug_candidates(company_name: str) -> list[str]:
    """Handle guesses, most-likely first. Reddit usernames allow letters,
    digits, underscore and hyphen, and are case-insensitive for lookup."""
    name = (company_name or "").strip()
    if not name:
        return []
    flat = re.sub(r"[^a-z0-9]", "", name.lower())
    underscored = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    out = []
    for candidate in (flat, underscored):
        if candidate and candidate not in out:
            out.append(candidate)
    return out


def get_user_about(username: str) -> dict | None:
    payload = _get(f"/user/{username}/about")
    data = (payload or {}).get("data")
    return data if isinstance(data, dict) else None


def get_subreddit_about(name: str) -> dict | None:
    payload = _get(f"/r/{name}/about")
    data = (payload or {}).get("data")
    # A missing subreddit still returns 200 with a kind of "Listing" and no
    # display_name, rather than a 404 -- so presence of the field is the
    # real existence check here.
    if isinstance(data, dict) and data.get("display_name"):
        return data
    return None


def resolve_company_account(company_name: str) -> dict | None:
    """The company's own Reddit ACCOUNT, by exact-handle lookup only.

    No fuzzy search fallback on purpose. Reddit's search ranks by
    engagement, so searching a company name returns whichever redditor
    talks about it most -- a person, not the brand -- and that account's
    submissions would then be collected and reported as the company's own
    content. An exact /user/<name>/about hit cannot be someone else's
    account in that way. Returns None (never raises) when nothing matches."""
    for slug in _slug_candidates(company_name):
        about = get_user_about(slug)
        if not about:
            continue
        name = about.get("name") or slug
        # Verify even an exact-handle hit: u/notion could be a person who
        # registered the word years before the company existed. The check is
        # cheap and the failure it prevents is silent and total.
        if not plausible_match(company_name, name):
            logger.info("sci_reddit_client: rejected u/%s for %r (name does not plausibly match)",
                        name, company_name)
            continue
        return {
            "kind": "user",
            "handle": f"u/{name}",
            "profile_url": f"https://www.reddit.com/user/{name}/",
            "title": name,
            "karma": (about.get("link_karma") or 0) + (about.get("comment_karma") or 0),
            "created_utc": about.get("created_utc"),
            "is_employee": bool(about.get("is_employee")),
            "verified": bool(about.get("verified")),
        }
    return None


def resolve_company_subreddit(company_name: str) -> dict | None:
    """The community that carries this brand's name, if one exists. Not
    owned content -- reported as part of the brand conversation."""
    for slug in _slug_candidates(company_name):
        about = get_subreddit_about(slug)
        if not about:
            continue
        display = about.get("display_name") or slug
        if not plausible_match(company_name, display) and not plausible_match(company_name, about.get("title") or ""):
            continue
        return {
            "kind": "subreddit",
            "name": f"r/{display}",
            "url": f"https://www.reddit.com/r/{display}/",
            "title": about.get("title") or display,
            "description": (about.get("public_description") or "").strip(),
            "subscribers": about.get("subscribers") or 0,
            "active_users": about.get("accounts_active"),
            "created_utc": about.get("created_utc"),
            "over_18": bool(about.get("over18")),
        }
    return None


# ── Fetching ──────────────────────────────────────────────────────────────

def list_user_posts(username: str, limit: int = 100) -> list[dict]:
    """Submissions authored by one account, newest first. [] on failure."""
    username = (username or "").lstrip("@").strip()
    if username.lower().startswith("u/"):
        username = username[2:]
    if not username:
        return []
    out: list[dict] = []
    after = None
    # Reddit caps a page at 100 and paginates by fullname cursor. The guard
    # is on pages as well as count so a malformed `after` can never spin.
    for _ in range(5):
        payload = _get(f"/user/{username}/submitted", limit=min(100, limit), after=after, sort="new")
        items = _children(payload)
        if not items:
            break
        for item in items:
            post = normalize(item)
            if post:
                out.append(post)
        if len(out) >= limit:
            break
        after = (payload.get("data") or {}).get("after")
        if not after:
            break
    return out[:limit]


def search_posts(query: str, sort: str = "relevance", time_filter: str = "year",
                 limit: int = 100, subreddit: str | None = None) -> list[dict]:
    """Reddit-wide (or one-subreddit) search, normalized. [] on failure."""
    if not (query or "").strip():
        return []
    path = f"/r/{subreddit}/search" if subreddit else "/search"
    out: list[dict] = []
    after = None
    for _ in range(5):
        payload = _get(path, q=query, sort=sort, t=time_filter, type="link",
                       limit=min(100, limit), after=after,
                       restrict_sr="true" if subreddit else None)
        items = _children(payload)
        if not items:
            break
        for item in items:
            post = normalize(item)
            if post:
                out.append(post)
        if len(out) >= limit:
            break
        after = (payload.get("data") or {}).get("after")
        if not after:
            break
    return out[:limit]


def probe() -> dict:
    """Prove the Reddit integration end to end, in the shape app.py's other
    vendor self-tests (_apollo_selftest, _arena_selftest, unipile_client
    .probe) established. Free: one token mint plus one tiny public read."""
    client_id, secret = _credentials()
    out: dict = {"configured": bool(client_id and secret), "client_id_len": len(client_id),
                 "secret_len": len(secret), "user_agent": _user_agent(),
                 "elapsed_ms": 0, "ok": False, "token": False, "sample": None,
                 "error_kind": "", "error": ""}
    if not (client_id and secret):
        out["error_kind"] = ERR_NOT_CONFIGURED
        out["error"] = ("REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are not set. "
                        "Create a script app at https://www.reddit.com/prefs/apps.")
        return out
    started = time.monotonic()
    try:
        reset_token_cache()
        token = _access_token()
        out["token"] = bool(token)
        if not token:
            out["error_kind"] = ERR_AUTH
            out["error"] = ("Reddit rejected these credentials when minting an app-only token "
                            "(check the client id/secret, and that the app type is 'script').")
            out["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            return out
        about = get_subreddit_about("announcements")
        out["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        if not about:
            out["error_kind"] = ERR_HTTP
            out["error"] = ("Token minted, but an authenticated read failed. This is usually a "
                            "blocked or rate-limited User-Agent -- set REDDIT_USER_AGENT to a "
                            "unique descriptive string.")
            return out
        out["ok"] = True
        out["sample"] = {"subreddit": about.get("display_name"),
                         "subscribers": about.get("subscribers")}
        return out
    except Exception as e:  # defensive: probe must never 500 the admin page
        out["error_kind"] = "exception"
        out["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
        out["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return out
