"""tracker/sci_reddit_client.py -- app-only OAuth, listing normalization and
the refuse-to-guess resolution rule, all against mocked HTTP. No live calls
and no real credentials.

Reddit's unauthenticated .json endpoints return a 403 block page to server
traffic, so the OAuth path these tests cover is the only one that can work
in production; there is no unauthenticated fallback to fall back to.
"""

import os
import sys
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_reddit_client as rc  # noqa: E402


def _resp(json_data, status=200):
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = json_data
    return mock


def _listing(*items):
    return {"data": {"children": [{"kind": "t3", "data": i} for i in items], "after": None}}


def _configured(monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    rc.reset_token_cache()


# ── credentials + token ──────────────────────────────────────────────────

def test_is_configured_needs_both_halves(monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "cid")
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    assert rc.is_configured() is False
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "s")
    assert rc.is_configured() is True


def test_no_token_is_requested_when_unconfigured(monkeypatch):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    rc.reset_token_cache()
    with patch("tracker.sci_reddit_client.requests.post") as mock_post:
        assert rc._access_token() is None
        assert mock_post.call_count == 0


def test_the_token_is_minted_once_and_then_reused(monkeypatch):
    """Reddit counts token requests against the same rate budget as data
    requests, so a six-call collection must not mint six tokens."""
    _configured(monkeypatch)
    with patch("tracker.sci_reddit_client.requests.post") as mock_post:
        mock_post.return_value = _resp({"access_token": "tok", "expires_in": 86400})
        assert rc._access_token() == "tok"
        assert rc._access_token() == "tok"
        assert mock_post.call_count == 1


def test_a_rejected_credential_pair_yields_no_token(monkeypatch):
    _configured(monkeypatch)
    with patch("tracker.sci_reddit_client.requests.post") as mock_post:
        mock_post.return_value = _resp({"message": "Unauthorized", "error": 401}, status=401)
        assert rc._access_token() is None


def test_every_request_sends_raw_json(monkeypatch):
    """Without raw_json=1 Reddit HTML-escapes &, < and > in every text field,
    so a caption arrives as "Q&amp;A" and carries that into the report."""
    _configured(monkeypatch)
    with patch("tracker.sci_reddit_client.requests.post") as mock_post, \
         patch("tracker.sci_reddit_client.requests.get") as mock_get:
        mock_post.return_value = _resp({"access_token": "tok", "expires_in": 3600})
        mock_get.return_value = _resp({"data": {}})
        rc._get("/r/x/about")
        assert mock_get.call_args.kwargs["params"]["raw_json"] == 1


def test_a_descriptive_user_agent_is_always_sent(monkeypatch):
    """Reddit throttles and blocks generic/default User-Agents far harder
    than well-identified ones."""
    _configured(monkeypatch)
    monkeypatch.delenv("REDDIT_USER_AGENT", raising=False)
    with patch("tracker.sci_reddit_client.requests.post") as mock_post, \
         patch("tracker.sci_reddit_client.requests.get") as mock_get:
        mock_post.return_value = _resp({"access_token": "tok", "expires_in": 3600})
        mock_get.return_value = _resp({"data": {}})
        rc._get("/r/x/about")
        agent = mock_get.call_args.kwargs["headers"]["User-Agent"]
        assert "position2" in agent.lower()
        assert mock_post.call_args.kwargs["headers"]["User-Agent"] == agent


def test_a_401_on_a_data_call_clears_the_cached_token(monkeypatch):
    """A revoked or early-expired token must not fail every call forever."""
    _configured(monkeypatch)
    with patch("tracker.sci_reddit_client.requests.post") as mock_post, \
         patch("tracker.sci_reddit_client.requests.get") as mock_get:
        mock_post.return_value = _resp({"access_token": "tok", "expires_in": 3600})
        mock_get.return_value = _resp({}, status=401)
        assert rc._get("/r/x/about") is None
        assert rc._TOKEN is None


# ── normalization ────────────────────────────────────────────────────────

def test_normalize_maps_a_self_post():
    post = rc.normalize({
        "id": "abc123", "title": "We shipped a thing", "selftext": "Long body here.",
        "permalink": "/r/sysadmin/comments/abc123/we_shipped/", "subreddit": "sysadmin",
        "author": "acme", "score": 412, "num_comments": 57, "upvote_ratio": 0.93,
        "created_utc": 1735689600, "is_self": True,
    })
    assert post["platform_post_id"] == "abc123"
    assert post["post_url"] == "https://www.reddit.com/r/sysadmin/comments/abc123/we_shipped/"
    assert post["post_type"] == "text"
    # Score is Reddit's own headline number and the closest thing it has to a like.
    assert post["metrics"] == {"likes": 412, "comments": 57}
    assert post["caption"].startswith("We shipped a thing")
    assert "Long body here." in post["caption"]
    assert post["posted_at"].startswith("2025-01-01")
    assert post["raw"]["upvote_ratio"] == 0.93


def test_normalize_requires_an_id():
    assert rc.normalize({"title": "no id"}) is None


def test_post_type_distinguishes_reddits_own_formats():
    """link and text are genuinely different content strategies on Reddit,
    so they are kept rather than flattened into the other platforms' set."""
    assert rc._post_type({"is_gallery": True}) == "carousel"
    assert rc._post_type({"is_video": True}) == "video"
    assert rc._post_type({"post_hint": "image"}) == "image"
    assert rc._post_type({"post_hint": "rich:video"}) == "video"
    assert rc._post_type({"is_self": True}) == "text"
    assert rc._post_type({"url": "https://example.com/x"}) == "link"
    assert rc._post_type({}) == "text"


def test_thumbnail_rejects_reddits_placeholder_strings():
    """Reddit puts the literal words "self"/"default"/"nsfw" in `thumbnail`
    where there is no image, which renders as a broken image if trusted."""
    for placeholder in ("self", "default", "nsfw", "spoiler", ""):
        assert rc._thumbnail({"thumbnail": placeholder}) is None
    assert rc._thumbnail({"thumbnail": "https://b.thumbs.redditmedia.com/x.jpg"}) \
        == "https://b.thumbs.redditmedia.com/x.jpg"


def test_thumbnail_falls_back_to_the_preview_image():
    got = rc._thumbnail({"thumbnail": "self", "preview": {
        "images": [{"source": {"url": "https://preview.redd.it/a.jpg?w=1&amp;s=2"}}]}})
    assert got == "https://preview.redd.it/a.jpg?w=1&s=2"


def test_media_urls_unescape_reddits_html_entities():
    """Preview URLs arrive HTML-escaped even under raw_json, and an &amp; in
    a query string makes the URL 403 when the vision step fetches it."""
    urls = rc._media_urls({"preview": {"images": [
        {"source": {"url": "https://preview.redd.it/a.jpg?w=640&amp;crop=smart"}}]}})
    assert urls == ["https://preview.redd.it/a.jpg?w=640&crop=smart"]


def test_media_urls_prefer_the_playable_video_over_the_still():
    urls = rc._media_urls({
        "is_video": True,
        "secure_media": {"reddit_video": {"fallback_url": "https://v.redd.it/x/DASH_720.mp4?source=fallback"}},
        "preview": {"images": [{"source": {"url": "https://preview.redd.it/still.jpg"}}]},
    })
    assert urls[0] == "https://v.redd.it/x/DASH_720.mp4"


# ── resolution: the refuse-to-guess rule ─────────────────────────────────

def test_resolve_company_account_never_searches(monkeypatch):
    """The load-bearing decision in this module. Reddit's search ranks by
    engagement, so searching a company name returns whichever redditor talks
    about it most -- a person -- and collecting their submissions would
    report a stranger's posts as the company's own content."""
    _configured(monkeypatch)
    calls = []

    def fake_get(path, **params):
        calls.append(path)
        return None
    monkeypatch.setattr(rc, "_get", fake_get)
    assert rc.resolve_company_account("Harborview Compliance Systems") is None
    assert calls, "expected at least one handle lookup"
    assert not any("search" in c for c in calls), "resolution must never hit search"


def test_resolve_company_account_accepts_an_exact_handle(monkeypatch):
    _configured(monkeypatch)
    monkeypatch.setattr(rc, "_get", lambda path, **p: {
        "data": {"name": "position2", "link_karma": 12, "comment_karma": 30}})
    got = rc.resolve_company_account("Position2")
    assert got["handle"] == "u/position2"
    assert got["profile_url"] == "https://www.reddit.com/user/position2/"
    assert got["karma"] == 42


def test_resolve_company_account_rejects_a_squatted_handle(monkeypatch):
    """u/notion could be a person who registered the word years before the
    company existed, so even an exact handle hit is verified by name."""
    _configured(monkeypatch)
    monkeypatch.setattr(rc, "_get", lambda path, **p: {"data": {"name": "xkcdfan1998"}})
    assert rc.resolve_company_account("Harborview Compliance Systems") is None


def test_resolve_company_subreddit_reports_the_community(monkeypatch):
    _configured(monkeypatch)
    monkeypatch.setattr(rc, "_get", lambda path, **p: {"data": {
        "display_name": "Notion", "title": "Notion", "public_description": "All things Notion",
        "subscribers": 380000}})
    got = rc.resolve_company_subreddit("Notion")
    assert got["name"] == "r/Notion"
    assert got["subscribers"] == 380000


def test_a_missing_subreddit_is_not_a_match(monkeypatch):
    """Reddit answers a nonexistent subreddit with 200 and a Listing that has
    no display_name, not with a 404, so presence of the field is the real
    existence check."""
    _configured(monkeypatch)
    monkeypatch.setattr(rc, "_get", lambda path, **p: {"kind": "Listing", "data": {"children": []}})
    assert rc.resolve_company_subreddit("Harborview Compliance Systems") is None


# ── fetching ─────────────────────────────────────────────────────────────

def test_list_user_posts_strips_a_u_prefix(monkeypatch):
    _configured(monkeypatch)
    seen = {}

    def fake_get(path, **params):
        seen["path"] = path
        return _listing({"id": "a1", "title": "t", "permalink": "/r/x/comments/a1/",
                         "created_utc": 1735689600, "is_self": True})
    monkeypatch.setattr(rc, "_get", fake_get)
    posts = rc.list_user_posts("u/position2")
    assert seen["path"] == "/user/position2/submitted"
    assert len(posts) == 1


def test_list_user_posts_is_empty_without_a_username(monkeypatch):
    _configured(monkeypatch)
    assert rc.list_user_posts("") == []


def test_search_posts_stops_when_a_page_has_no_cursor(monkeypatch):
    """Guards against paging forever on a malformed `after`."""
    _configured(monkeypatch)
    pages = []

    def fake_get(path, **params):
        pages.append(params.get("after"))
        return _listing({"id": "s%d" % len(pages), "title": "t",
                         "permalink": "/r/x/", "created_utc": 1735689600})
    monkeypatch.setattr(rc, "_get", fake_get)
    posts = rc.search_posts('"Acme"', limit=100)
    assert len(pages) == 1  # after is None in _listing, so it stops
    assert len(posts) == 1


def test_search_posts_needs_a_query(monkeypatch):
    _configured(monkeypatch)
    assert rc.search_posts("   ") == []


# ── probe ────────────────────────────────────────────────────────────────

def test_probe_reports_unconfigured_without_calling_out(monkeypatch):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)
    with patch("tracker.sci_reddit_client.requests.post") as mock_post:
        out = rc.probe()
        assert mock_post.call_count == 0
    assert out["configured"] is False
    assert out["ok"] is False
    assert out["error_kind"] == rc.ERR_NOT_CONFIGURED


def test_probe_separates_a_bad_credential_from_a_blocked_read(monkeypatch):
    """"Configured" and "actually answers" are genuinely different questions
    for Reddit, and the admin panel has to be able to tell them apart."""
    _configured(monkeypatch)
    monkeypatch.setattr(rc, "_access_token", lambda: None)
    assert rc.probe()["error_kind"] == rc.ERR_AUTH

    rc.reset_token_cache()
    monkeypatch.setattr(rc, "_access_token", lambda: "tok")
    monkeypatch.setattr(rc, "get_subreddit_about", lambda name: None)
    out = rc.probe()
    assert out["token"] is True
    assert out["error_kind"] == rc.ERR_HTTP
    assert "user-agent" in out["error"].lower()


def test_probe_never_raises(monkeypatch):
    _configured(monkeypatch)
    monkeypatch.setattr(rc, "_access_token", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert rc.probe()["error_kind"] == "exception"
