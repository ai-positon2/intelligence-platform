"""The hide switch (PAID_HIDDEN_AGENTS in app.py). It is EMPTY now (the five agents
hidden on 2026-10-18 were restored on 2026-10-20), so the first tests pin the
restored state and the rest put the five back in the switch to prove it still
hides everything it should. Unlisted, not gone: hidden pages still answer.
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


def _raw(name):
    return open(H.os.path.join(H.ROOT, "templates", name), encoding="utf-8").read()


# Every card the template can show, hidden or not; and the two totals the hub
# template subtracts the hidden ones from (so a new agent bumps them there).
TOTAL = len(re.findall(r'<a href="/p2/paid-marketing/[a-z\-]+" class="dash-card', re.sub(r"<!--.*?-->", "", _raw("paid_marketing.html"), flags=re.S)))
_hub_raw = _raw("hub.html")
BAND = int(re.search(r'data-lxn="\{\{ (\d+) - paid_hidden\|length \}\}"', _hub_raw).group(1))
CARD = int(re.search(r'<span>\{\{ (\d+) - paid_hidden\|length \}\}</span> dashboards', _hub_raw).group(1))


def test_the_hub_totals_are_the_number_of_cards_the_template_holds():
    assert CARD == TOTAL


def _cards(html):
    return re.findall(r'<a href="(/p2/paid-marketing/[a-z\-]+)" class="dash-card', html)


FIVE = {s: PLAIN[s] for s in NAMES}


@pytest.fixture
def hide(monkeypatch):
    monkeypatch.setattr(appmod, "PAID_HIDDEN_AGENTS", dict(FIVE))
    monkeypatch.setattr(appmod, "PAID_HIDDEN_SLUGS", frozenset(FIVE))


def test_nothing_is_hidden_now_and_every_card_is_listed():
    assert appmod.PAID_HIDDEN_AGENTS == {} and appmod.PAID_HIDDEN_SLUGS == frozenset()
    page = H.rendered("/p2/paid-marketing")
    assert len(_cards(page)) == TOTAL and {u.rsplit("/", 1)[1] for u in _cards(page)} >= set(NAMES)
    for slug in NAMES:
        assert "u:'/p2/paid-marketing/%s'" % slug in page, slug
    hub = re.sub(r"<!--.*?-->", "", H.rendered("/p2/hub"), flags=re.S)
    assert "<span>%d</span> dashboards" % TOTAL in hub and 'data-lxn="%d"' % BAND in hub
    assert "ad copy checked against" in hub and "Google and Meta compared side by side" in hub
    for slug, title in PLAIN.items():
        assert "%s (/p2/paid-marketing/%s)" % (title, slug) in appmod._VIMI_PLATFORM_KNOWLEDGE, slug


def test_the_page_shows_none_of_the_five_and_every_other_card(hide):
    page = H.rendered("/p2/paid-marketing")
    shown = _cards(page)
    assert not [u for u in shown if u.rsplit("/", 1)[1] in NAMES]
    assert len(shown) == TOTAL - 5
    for slug, title in NAMES.items():
        assert title not in re.sub(r"<!--.*?-->|<script.*?</script>", "", page, flags=re.S), title


def test_the_command_palette_lists_none_of_the_five(hide):
    page = H.rendered("/p2/paid-marketing")
    for slug, title in PLAIN.items():
        assert "u:'/p2/paid-marketing/%s'" % slug not in page, slug
    assert "u:'/p2/paid-marketing/account-audit'" in page and "u:'/p2/paid-marketing/pacing-desk'" in page
    # and the include the other Paid pages carry
    assert "u:'/p2/paid-marketing/ad-copy'" not in H.rendered("/p2/paid-marketing/pacing-desk")


def test_the_hub_counts_and_text_follow_the_switch(hide):
    hub = re.sub(r"<!--.*?-->", "", H.rendered("/p2/hub"), flags=re.S)
    assert "<span>%d</span> dashboards" % (TOTAL - 5) in hub and "<span>%d</span> live" % (TOTAL - 5) in hub
    assert 'data-lxn="%d"' % (BAND - 5) in hub
    assert "ad copy checked against" not in hub and "compared side by side" not in hub


def test_the_assistant_no_longer_describes_them_and_still_describes_the_rest(hide):
    k = appmod._without_hidden_paid(appmod._VIMI_PLATFORM_KNOWLEDGE)
    for slug, title in PLAIN.items():
        assert "(/p2/paid-marketing/%s)" % slug not in k, slug
    for kept in ("Account Audit (/p2/paid-marketing/account-audit)", "Business Research (/p2/paid-marketing/business-research)",
                 "Strategy & Campaign Book (/p2/paid-marketing/campaign-book)", "QA Agent (/p2/paid-marketing/qa-agent)",
                 "Asset Coverage Audit (/p2/paid-marketing/asset-coverage)"):
        assert kept in k, kept


def test_filtering_only_removes_the_hidden_entries(hide):
    text = ("Intro.\nBusiness Research (/p2/paid-marketing/business-research): kept one. Keyword Research (/p2/paid-marketing/keyword-research): gone, "
            "with Sentence two. Account Audit (/p2/paid-marketing/account-audit): kept two.\n"
            "Ad Copy & Creative Generator (/p2/paid-marketing/ad-copy): gone too.\nTail.")
    out = appmod._without_hidden_paid(text)
    assert "gone" not in out and "Sentence two" not in out
    assert "kept one" in out and "kept two" in out and "Intro." in out and "Tail." in out


def test_the_pages_and_apis_still_answer_so_nothing_is_lost(hide):
    for slug in NAMES:
        r = appmod.app.test_client().get("/p2/paid-marketing/%s" % slug)
        assert r.status_code == 302                    # signed out is sent to sign in, as for any page: the route is there
        assert H.rendered("/p2/paid-marketing/%s" % slug)


def test_the_withdrawn_paragraphs_are_still_in_the_source_to_restore_from():
    src = open(appmod.__file__, encoding="utf-8").read()
    for slug, title in PLAIN.items():
        assert "%s (/p2/paid-marketing/%s):" % (title, slug) in src, slug
