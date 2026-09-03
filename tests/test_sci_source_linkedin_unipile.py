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


# ── Which page did we actually read? ─────────────────────────────────────
# LinkedIn hands out one vanity slug per page and reuses names freely.
# /company/notion is a 39-person IT consultancy with no posts and no website;
# Notion Labs is /company/notionhq. Both are called "Notion", both resolve
# cleanly, and the wrong one answers with an empty list that reads as "this
# company does not post on LinkedIn". Both pages below are real.

_IMPOSTOR = {"id": "120213", "public_identifier": "notion", "name": "Notion",
             "followers_count": 882, "employee_count": 39}
_REAL = {"id": "30898036", "public_identifier": "notionhq", "name": "Notion",
         "website": "https://notion.com", "followers_count": 1107455}


def test_a_matching_name_is_not_enough_to_confirm_a_page():
    """The two pages share a name exactly, so a name check alone confirms the
    impostor. Only the website separates them, which is why the domain is the
    decisive signal here and the name is the weak one."""
    assert src.verify_company_page(_REAL, "Notion", "https://notion.com") == src.VERIFIED_DOMAIN
    assert src.verify_company_page(_IMPOSTOR, "Notion", "https://notion.com") == src.VERIFIED_NAME


def test_a_site_confirms_across_subdomains_and_schemes():
    """A company's own URL is whatever someone typed into the form: a blog
    subdomain, http, a trailing www. None of those make it a different site."""
    page = {"name": "HubSpot", "website": "https://hubspot.com"}
    for url in ("https://blog.hubspot.com", "http://www.hubspot.com",
                "hubspot.com", "https://hubspot.com/pricing"):
        assert src.verify_company_page(page, "HubSpot", url) == src.VERIFIED_DOMAIN, url


def test_a_www_url_still_matches_a_page_listing_a_different_subdomain():
    """Two subdomains of one site are not suffixes of each other, so the www
    has to come off before they are compared: a form filled in as
    www.acme.com against a page listing shop.acme.com is the same company."""
    page = {"name": "Acme Corp", "website": "https://shop.acme.com"}
    assert src.verify_company_page(page, "Acme Corp", "https://www.acme.com") == src.VERIFIED_DOMAIN


def test_two_different_companies_on_a_multi_part_suffix_are_not_the_same_site():
    """Comparing the last two labels would read acme.co.uk and rival.co.uk as
    one site, which is a false confirmation in exactly the case this check
    exists to catch."""
    page = {"name": "Rival Ltd", "website": "https://rival.co.uk"}
    assert src.verify_company_page(page, "Acme Ltd", "https://acme.co.uk") == src.VERIFIED_MISMATCH


def test_a_different_domain_alone_is_not_called_a_mismatch():
    """A real company page listing a parent, group or campaign domain is
    ordinary. Rejecting on the domain alone would fail live runs, so the name
    has to disagree too before this refuses to collect."""
    page = {"name": "Acme Corp", "website": "https://acme-group.com"}
    assert src.verify_company_page(page, "Acme Corp", "https://acme.com") == src.VERIFIED_NAME


def test_a_page_with_no_website_falls_back_to_the_name():
    assert src.verify_company_page({"name": "Acme Corp"}, "Acme Corp", "https://acme.com") == src.VERIFIED_NAME
    assert src.verify_company_page({"name": "Zeta Ltd"}, "Acme Corp", "https://acme.com") == src.VERIFIED_MISMATCH


def test_nothing_to_check_against_is_reported_as_such_not_as_confirmed():
    """A run with no company name cannot verify anything. Reporting that as
    verified would silence the warning on exactly the runs least able to
    afford it."""
    assert src.verify_company_page(_REAL, None, None) == src.VERIFIED_NONE


def test_the_page_description_names_what_a_person_needs_to_spot_a_wrong_one():
    described = src.describe_company_page(_IMPOSTOR)
    assert "linkedin.com/company/notion" in described
    assert "882 followers" in described
    assert "no website listed" in described


@patch("tracker.sci_source_linkedin_unipile.unipile_transport.fetch_posts")
@patch("tracker.sci_source_linkedin_unipile.unipile_client.get_company")
@patch("tracker.sci_source_linkedin_unipile.unipile_transport.account_for_platform")
def test_a_wrong_company_page_is_refused_before_a_single_post_is_read(
        mock_account, mock_company, mock_fetch):
    mock_account.return_value = "acct-1"
    mock_company.return_value = ({"id": "999", "public_identifier": "stripe", "name": "Stripe",
                                  "website": "https://stripe.com", "followers_count": 1671976}, None)
    try:
        src.collect_with_page("stripe", company_name="Acme Dental Group",
                              company_url="https://acmedental.com")
        assert False, "expected CompanyMismatch"
    except src.CompanyMismatch as e:
        assert "Acme Dental Group" in str(e)
        assert "linkedin.com/company/stripe" in str(e)
    assert mock_fetch.call_count == 0


def test_a_company_mismatch_is_not_a_transport_error():
    """sci_pipeline retries a transport error through the other vendor. A
    wrong handle points at the same wrong page on any vendor, so this must
    not be catchable as one."""
    assert not issubclass(src.CompanyMismatch, unipile_transport.UnipileTransportError)


@patch("tracker.sci_source_linkedin_unipile.unipile_transport.fetch_posts")
@patch("tracker.sci_source_linkedin_unipile.unipile_client.get_company")
@patch("tracker.sci_source_linkedin_unipile.unipile_transport.account_for_platform")
def test_an_unconfirmed_page_still_collects_and_says_which_page_it_was(
        mock_account, mock_company, mock_fetch):
    """Unconfirmed is not rejected: most real runs land here. The note is what
    lets the caller describe an empty result honestly."""
    mock_account.return_value = "acct-1"
    mock_company.return_value = (_IMPOSTOR, None)
    mock_fetch.return_value = []
    posts, note = src.collect_with_page("notion", company_name="Notion",
                                        company_url="https://notion.com")
    assert posts == []
    assert note["verification"] == src.VERIFIED_NAME
    assert "linkedin.com/company/notion" in note["page"]


@patch("tracker.sci_source_linkedin_unipile.unipile_transport.fetch_posts")
@patch("tracker.sci_source_linkedin_unipile.unipile_client.get_company")
@patch("tracker.sci_source_linkedin_unipile.unipile_transport.account_for_platform")
def test_posts_are_always_fetched_from_the_page_that_was_verified(
        mock_account, mock_company, mock_fetch):
    """Verifying one page and then fetching another would make the check
    decorative."""
    mock_account.return_value = "acct-1"
    mock_company.return_value = (_REAL, None)
    mock_fetch.return_value = []
    src.collect_with_page("notionhq", company_name="Notion", company_url="https://notion.com")
    assert mock_fetch.call_args.args[0] == "30898036"
