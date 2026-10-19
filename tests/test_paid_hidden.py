"""Five Paid Marketing agents are withdrawn from the listings and come back when
PAID_HIDDEN_AGENTS in app.py is emptied. Unlisted, not gone: their pages answer.
"""
import re

import pytest

from tests import hidden_paid as H

appmod = H.appmod

NAMES = {
    "experiment-tracker": "Test &amp; Experiment Tracker",
    "ad-copy": "Ad Copy &amp; Creative Generator",
    "landing-page-check": "Landing Page &amp; Relevancy Check",
    "cross-channel": "Cross-Channel &amp; Attribution Analysis",
    "keyword-research": "Keyword Research",
}
PLAIN = {k: v.replace("&amp;", "&") for k, v in NAMES.items()}


def _cards(html):
    return re.findall(r'<a href="(/p2/paid-marketing/[a-z\-]+)" class="dash-card', html)


def test_the_switch_names_exactly_the_five_the_owner_withdrew():
    assert set(appmod.PAID_HIDDEN_AGENTS) == set(NAMES)
    assert appmod.PAID_HIDDEN_SLUGS == frozenset(NAMES)


def test_the_page_shows_none_of_the_five_and_every_other_card():
    page = H.rendered("/p2/paid-marketing")
    shown = _cards(page)
    assert not [u for u in shown if u.rsplit("/", 1)[1] in NAMES]
    assert len(shown) == 23 - 5
    for slug, title in NAMES.items():
        assert title not in re.sub(r"<!--.*?-->|<script.*?</script>", "", page, flags=re.S), title


def test_the_command_palette_lists_none_of_the_five():
    page = H.rendered("/p2/paid-marketing")
    for slug, title in PLAIN.items():
        assert "u:'/p2/paid-marketing/%s'" % slug not in page, slug
    assert "u:'/p2/paid-marketing/account-audit'" in page and "u:'/p2/paid-marketing/pacing-desk'" in page
    # and the include the other Paid pages carry
    assert "u:'/p2/paid-marketing/ad-copy'" not in H.rendered("/p2/paid-marketing/pacing-desk")


def test_the_hub_counts_and_text_follow_the_switch():
    hub = re.sub(r"<!--.*?-->", "", H.rendered("/p2/hub"), flags=re.S)
    assert "<span>18</span> dashboards" in hub and "<span>18</span> live" in hub
    assert 'data-lxn="45"' in hub
    assert "ad copy checked against" not in hub and "compared side by side" not in hub


def test_the_assistant_no_longer_describes_them_and_still_describes_the_rest():
    k = appmod._VIMI_PLATFORM_KNOWLEDGE
    for slug, title in PLAIN.items():
        assert "(/p2/paid-marketing/%s)" % slug not in k, slug
    for kept in ("Account Audit (/p2/paid-marketing/account-audit)", "Business Research (/p2/paid-marketing/business-research)",
                 "Strategy & Campaign Book (/p2/paid-marketing/campaign-book)", "QA Agent (/p2/paid-marketing/qa-agent)",
                 "Asset Coverage Audit (/p2/paid-marketing/asset-coverage)"):
        assert kept in k, kept


def test_filtering_only_removes_the_hidden_entries():
    text = ("Intro.\nBusiness Research (/p2/paid-marketing/business-research): kept one. Keyword Research (/p2/paid-marketing/keyword-research): gone, "
            "with Sentence two. Account Audit (/p2/paid-marketing/account-audit): kept two.\n"
            "Ad Copy & Creative Generator (/p2/paid-marketing/ad-copy): gone too.\nTail.")
    out = appmod._without_hidden_paid(text)
    assert "gone" not in out and "Sentence two" not in out
    assert "kept one" in out and "kept two" in out and "Intro." in out and "Tail." in out


def test_the_pages_and_apis_still_answer_so_nothing_is_lost():
    for slug in NAMES:
        r = appmod.app.test_client().get("/p2/paid-marketing/%s" % slug)
        assert r.status_code == 302                    # signed out is sent to sign in, as for any page: the route is there
        assert H.rendered("/p2/paid-marketing/%s" % slug)


def test_emptying_the_switch_brings_every_card_and_count_back(monkeypatch):
    monkeypatch.setattr(appmod, "PAID_HIDDEN_SLUGS", frozenset())
    page = H.rendered("/p2/paid-marketing")
    assert {u.rsplit("/", 1)[1] for u in _cards(page)} >= set(NAMES) and len(_cards(page)) == 23
    for slug in NAMES:
        assert "u:'/p2/paid-marketing/%s'" % slug in page, slug
    hub = re.sub(r"<!--.*?-->", "", H.rendered("/p2/hub"), flags=re.S)
    assert "<span>23</span> dashboards" in hub and 'data-lxn="50"' in hub
    assert "ad copy checked against" in hub and "Google and Meta compared side by side" in hub


def test_the_withdrawn_paragraphs_are_still_in_the_source_to_restore_from():
    src = open(appmod.__file__, encoding="utf-8").read()
    for slug, title in PLAIN.items():
        assert "%s (/p2/paid-marketing/%s):" % (title, slug) in src, slug
