"""tracker/sci_source_linkedin_unipile.py -- normalize() against the shape
Unipile really returns, plus collect()'s slug-to-id resolve step.

_LIVE_* below are trimmed but otherwise verbatim rows from a real 200 on
2026-09-01 (a company page's own posts). Copied rather than invented on
purpose: the previous version of this file asserted a payload nobody had
ever seen, and every one of its assertions passed while collect() could not
fetch a single post, because the endpoint rejects the identifier it was
being handed. A fixture that cannot express the real shape cannot fail.
"""

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tracker import sci_source_linkedin_unipile as src  # noqa: E402
from tracker import unipile_transport  # noqa: E402


_LIVE_IMAGE = {
    "object": "Post", "provider": "LINKEDIN",
    "social_id": "urn:li:activity:7500213493552427008",
    "share_url": "https://www.linkedin.com/posts/position2_startups-activity-7500213493552427008-o2gk",
    "date": "15h", "parsed_datetime": "2026-08-31T15:30:44.091Z",
    "comment_counter": 1, "impressions_counter": 0, "reaction_counter": 4, "repost_counter": 0,
    "text": "How do you turn an accidental product launch into a platform?",
    "is_repost": False, "mentions": [],
    "attachments": [{"id": "D5610AQG", "sticker": False, "size": {"height": 270, "width": 480},
                     "unavailable": False, "type": "img",
                     "url": "https://media.licdn.com/dms/image/v2/D5610AQG/image-shrink_480"}],
    "author": {"public_identifier": "position2", "id": "60223", "name": "Position2",
               "is_company": True, "profile_picture_url": "https://media.licdn.com/logo.png"},
    "id": "7500213493552427008",
}

_LIVE_VIDEO = {
    "object": "Post", "provider": "LINKEDIN", "id": "7499141933278113793",
    "share_url": "https://www.linkedin.com/posts/position2_x-activity-7499141933278113793-aaaa",
    "date": "3d", "parsed_datetime": "2026-08-28T16:32:44.213Z",
    "comment_counter": 0, "impressions_counter": 0, "reaction_counter": 11, "repost_counter": 1,
    "text": "EBITDA has gone up, but Growth has collapsed",
    "attachments": [{"type": "video", "gif": False, "id": "D5610AQG0", "unavailable": False,
                     "size": {"height": 1280, "width": 720},
                     "url": "https://dms.licdn.com/playlist/vid/v2/D5610AQG0/mp4-720p-30fp-crf28"}],
}

_LIVE_ARTICLE = {
    "object": "Post", "provider": "LINKEDIN", "id": "7495513603874217984",
    "share_url": "https://www.linkedin.com/posts/position2_y-activity-7495513603874217984-bbbb",
    "date": "1w", "parsed_datetime": "2026-08-18T16:15:03.072Z",
    "comment_counter": 0, "impressions_counter": 0, "reaction_counter": 6, "repost_counter": 0,
    "text": "Worth a read.", "attachments": [],
    "article": {"id": "7493026334634344448",
                "title": "Rethinking AI: why the future of software isn't SaaS",
                "author": "Spark Of Ages podcast",
                "url": "https://www.linkedin.com/pulse/rethinking-ai-nabvc",
                "published_at": "2026-08-11T19:31:31.874Z",
                "picture_url": "https://media.licdn.com/dms/image/v2/D5612AQFQIY/article-cover"},
}

_LIVE_CAROUSEL = {
    "object": "Post", "provider": "LINKEDIN", "id": "7491222958514851840",
    "date": "3w", "parsed_datetime": "2026-08-06T20:05:33.502Z",
    "reaction_counter": 18, "comment_counter": 2, "repost_counter": 0, "impressions_counter": 0,
    "text": "Scenes from the summit.",
    "attachments": [{"type": "img", "unavailable": False, "url": "https://media.licdn.com/a.jpg"},
                    {"type": "img", "unavailable": False, "url": "https://media.licdn.com/b.jpg"},
                    {"type": "img", "unavailable": False, "url": "https://media.licdn.com/c.jpg"}],
}


# ── The identifier: three of the four shapes identify emits are a 422 ────

def test_every_shape_the_identify_step_emits_reduces_to_the_same_slug():
    """identify_handles is a language model told to return "a handle", and
    it returns all of these for the same page. Only the bare slug resolves;
    the rest are 422 invalid_recipient."""
    for handle in ("position2", "@position2", "company/position2",
                   "https://www.linkedin.com/company/position2/",
                   "https://www.linkedin.com/company/position2/?trk=nav",
                   "www.linkedin.com/company/position2"):
        assert src.company_slug(handle) == "position2", handle


def test_showcase_and_school_pages_resolve_the_same_way():
    assert src.company_slug("https://www.linkedin.com/showcase/acme-cloud/") == "acme-cloud"
    assert src.company_slug("https://www.linkedin.com/school/acme-university/") == "acme-university"


def test_a_numeric_id_is_used_as_is_and_costs_no_lookup():
    with patch("tracker.sci_source_linkedin_unipile.unipile_client.get_company") as mock_company:
        assert src.resolve_identifier("60223", "acct-1") == "60223"
        assert mock_company.call_count == 0


@patch("tracker.sci_source_linkedin_unipile.unipile_client.get_company")
def test_a_slug_is_resolved_to_the_numeric_id_the_posts_endpoint_needs(mock_company):
    mock_company.return_value = ({"id": "60223", "name": "Position2"}, None)
    assert src.resolve_identifier("https://www.linkedin.com/company/position2/", "acct-1") == "60223"
    assert mock_company.call_args.args == ("position2", "acct-1")


@patch("tracker.sci_source_linkedin_unipile.unipile_client.get_company")
def test_an_unresolvable_company_fails_loudly_rather_than_fetching_nothing(mock_company):
    """The whole reason this resolve exists is that the wrong identifier
    returns an error, not an empty list. Swallowing it here would put "this
    company posts nothing on LinkedIn" in front of a reader."""
    from tracker import unipile_client
    mock_company.return_value = (None, {"kind": unipile_client.ERR_HTTP, "status": 422, "detail": ""})
    try:
        src.resolve_identifier("nosuchcompany", "acct-1")
        assert False, "expected UnipileTransportError"
    except unipile_transport.UnipileTransportError as e:
        assert "nosuchcompany" in str(e)


# ── normalize() against the real payload ─────────────────────────────────

def test_normalize_maps_a_real_image_post():
    out = src.normalize([_LIVE_IMAGE])
    assert len(out) == 1
    post = out[0]
    assert post["platform_post_id"] == "7500213493552427008"
    assert post["post_type"] == "image"
    assert post["media_urls"] == ["https://media.licdn.com/dms/image/v2/D5610AQG/image-shrink_480"]
    assert post["metrics"]["likes"] == 4
    assert post["metrics"]["comments"] == 1
    assert post["post_url"].startswith("https://www.linkedin.com/posts/")


def test_the_relative_date_string_is_never_stored_as_a_timestamp():
    """`date` is "15h"/"3w", not a date. Falling back to it would put an
    unparseable string into posted_at and silently corrupt every
    date-ordered chart downstream."""
    assert _LIVE_IMAGE["date"] == "15h"
    assert src.normalize([_LIVE_IMAGE])[0]["posted_at"] == "2026-08-31T15:30:44.091Z"
    undated = {k: v for k, v in _LIVE_IMAGE.items() if k != "parsed_datetime"}
    assert src.normalize([undated])[0]["posted_at"] is None


def test_a_video_post_leads_with_the_video_not_a_still():
    """sci_pipeline analyzes media_urls[0] and nothing else, so on a post
    carrying both the clip has to come first or the whole video is judged
    from one frame of its poster."""
    mixed = dict(_LIVE_VIDEO)
    mixed["attachments"] = [{"type": "img", "unavailable": False, "url": "https://cdn/poster.jpg"},
                            _LIVE_VIDEO["attachments"][0]]
    out = src.normalize([mixed])[0]
    assert out["post_type"] == "video"
    assert out["media_urls"][0].endswith("mp4-720p-30fp-crf28")


def test_unavailable_attachments_are_not_handed_to_the_vision_step():
    """LinkedIn leaves expired media in place with unavailable:true. Passing
    one on sends the vision step a URL that 404s."""
    dead = dict(_LIVE_IMAGE)
    dead["attachments"] = [{"type": "img", "unavailable": True, "url": "https://cdn/gone.jpg"}]
    out = src.normalize([dead])[0]
    assert out["media_urls"] == []
    assert out["post_type"] == "text"


def test_a_link_post_is_analyzed_through_its_article_cover():
    """With no attachments this post has no media_urls, and sci_pipeline
    skips any post with none, so the only creative a link post has would go
    unanalyzed."""
    out = src.normalize([_LIVE_ARTICLE])[0]
    assert out["post_type"] == "article"
    assert out["media_urls"] == ["https://media.licdn.com/dms/image/v2/D5612AQFQIY/article-cover"]
    assert "Rethinking AI" in out["caption"]


def test_an_article_headline_is_not_repeated_when_the_post_already_says_it():
    post = dict(_LIVE_ARTICLE)
    post["text"] = "Rethinking AI: why the future of software isn't SaaS"
    assert src.normalize([post])[0]["caption"].count("Rethinking AI") == 1


def test_normalize_flags_carousel_for_multiple_image_attachments():
    out = src.normalize([_LIVE_CAROUSEL])[0]
    assert out["post_type"] == "carousel"
    assert len(out["media_urls"]) == 3


def test_normalize_falls_back_to_text_with_no_attachments():
    out = src.normalize([{"id": "4", "text": "just words"}])[0]
    assert out["post_type"] == "text"
    assert out["media_urls"] == []


def test_impressions_are_dropped_rather_than_reported_as_a_real_zero():
    """Through a session that does not administer the page every post reads
    0 impressions. Reporting that would print "0 impressions" beside a post
    with 4 reactions and 1 comment."""
    assert _LIVE_IMAGE["impressions_counter"] == 0
    assert "impressions" not in src.normalize([_LIVE_IMAGE])[0]["metrics"]
    seen = dict(_LIVE_IMAGE, impressions_counter=1820)
    assert src.normalize([seen])[0]["metrics"]["impressions"] == 1820


def test_a_genuine_zero_engagement_count_survives():
    """The impressions rule must not spread: 0 reactions is a measured 0."""
    quiet = dict(_LIVE_IMAGE, reaction_counter=0, comment_counter=0, repost_counter=0)
    metrics = src.normalize([quiet])[0]["metrics"]
    assert metrics["likes"] == 0 and metrics["comments"] == 0 and metrics["shares"] == 0


def test_normalize_skips_items_with_no_id():
    assert src.normalize([{"text": "no id"}]) == []


# ── collect() ────────────────────────────────────────────────────────────

@patch("tracker.sci_source_linkedin_unipile.unipile_transport.fetch_posts")
@patch("tracker.sci_source_linkedin_unipile.unipile_client.get_company")
@patch("tracker.sci_source_linkedin_unipile.unipile_transport.account_for_platform")
def test_collect_resolves_then_fetches_through_the_same_account(
        mock_account, mock_company, mock_fetch):
    mock_account.return_value = "acct-1"
    mock_company.return_value = ({"id": "60223"}, None)
    mock_fetch.return_value = [_LIVE_IMAGE]
    out = src.collect("https://www.linkedin.com/company/position2/", strict=True)
    assert mock_fetch.call_args.args[0] == "60223"
    assert mock_fetch.call_args.kwargs.get("is_company") is True
    assert mock_fetch.call_args.kwargs.get("strict") is True
    # Both calls go through one account: resolving as one identity and
    # fetching as another is how a lookup that works alone fails in place.
    assert mock_fetch.call_args.kwargs.get("account_id") == "acct-1"
    assert len(out) == 1


@patch("tracker.sci_source_linkedin_unipile.unipile_client.get_company")
@patch("tracker.sci_source_linkedin_unipile.unipile_transport.account_for_platform")
def test_collect_raises_when_no_working_account_is_connected(mock_account, mock_company):
    """Asserted on the vendor call NOT happening, not just on some exception
    coming out: without the account check, resolve_identifier still raises,
    just with a message blaming the company slug for a problem that is
    actually a missing account. Same exception type, wrong story."""
    mock_account.return_value = None
    try:
        src.collect("position2", strict=True)
        assert False, "expected UnipileTransportError"
    except unipile_transport.UnipileTransportError as e:
        assert "account" in str(e).lower()
    assert mock_company.call_count == 0


@patch("tracker.sci_source_linkedin_unipile.unipile_client.get_company")
@patch("tracker.sci_source_linkedin_unipile.unipile_transport.account_for_platform")
def test_collect_degrades_to_empty_when_not_strict(mock_account, mock_company):
    mock_account.return_value = None
    assert src.collect("position2", strict=False) == []
    assert mock_company.call_count == 0
