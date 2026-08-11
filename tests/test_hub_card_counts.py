"""The hub card's "N dashboards / N live" must match the dashboard page.

These two numbers are hand-written in templates/hub.html while the cards they
describe live in templates/b2b_agents.html, so they drift the moment someone
adds a dashboard and forgets. That is exactly what happened: Contact Finder was
added, the card kept saying "6 dashboards / 5 live", and nothing complained,
because a stale number is not an error, just a quiet lie on the busiest page in
the app.

So the counts are derived from the dashboard page here and compared to what the
hub advertises. Adding a dashboard now fails this test until the card is updated,
which is the only way a hand-maintained number stays honest.

Also checked: every capability the card's prose claims corresponds to a card that
is actually live, so the description cannot promise something unbuilt (it used to
advertise job-change alerts, which is still "Coming soon").
"""

import os
import re

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_HUB = os.path.join(_ROOT, "templates", "hub.html")
_B2B = os.path.join(_ROOT, "templates", "b2b_agents.html")


def _strip_comments(html: str) -> str:
    """Cards that have been retired are commented out rather than deleted (see
    Sentiment Pulse), and a commented-out card is not on the page. Counting it
    would overstate the dashboard count."""
    return re.sub(r"<!--.*?-->", "", html, flags=re.S)


@pytest.fixture(scope="module")
def dashboard_cards():
    with open(_B2B) as fh:
        body = _strip_comments(fh.read())
    classes = re.findall(r'class="dash-card ([^"]*)"', body)
    names = [re.sub(r"\s+", " ", n).strip()
             for n in re.findall(r'class="card-name">(.*?)</div>', body, re.S)]
    return {
        "live": [n for c, n in zip(classes, names) if c.split()[0] == "active"],
        "soon": [n for c, n in zip(classes, names) if c.split()[0] == "soon"],
        "total": len(classes),
    }


@pytest.fixture(scope="module")
def hub_card():
    with open(_HUB) as fh:
        body = _strip_comments(fh.read())
    # The B2B Agents card, up to the start of the next card.
    block = body.split('href="/p2/b2b-agents"', 1)[1].split("</a>", 1)[0]
    stats = dict((label, int(n)) for n, label in
                 re.findall(r'class="card-stat"><span>(\d+)</span>\s*(\w+)', block))
    desc = re.sub(r"\s+", " ",
                  re.search(r'class="card-desc">(.*?)</div>', block, re.S).group(1)).strip()
    title = re.search(r'class="card-title">(.*?)</div>', block, re.S).group(1).strip()
    return {"stats": stats, "desc": desc, "title": title}


# ── The numbers ─────────────────────────────────────────────────────────────

def test_the_advertised_dashboard_count_matches_the_page(hub_card, dashboard_cards):
    assert hub_card["stats"]["dashboards"] == dashboard_cards["total"], (
        "hub says %d dashboards, the page shows %d (%s)"
        % (hub_card["stats"]["dashboards"], dashboard_cards["total"],
           ", ".join(dashboard_cards["live"] + dashboard_cards["soon"])))


def test_the_advertised_live_count_matches_the_page(hub_card, dashboard_cards):
    assert hub_card["stats"]["live"] == len(dashboard_cards["live"]), (
        "hub says %d live, the page has %d: %s"
        % (hub_card["stats"]["live"], len(dashboard_cards["live"]),
           ", ".join(dashboard_cards["live"])))


def test_live_is_never_more_than_total(hub_card):
    assert hub_card["stats"]["live"] <= hub_card["stats"]["dashboards"]


def test_contact_finder_is_one_of_the_live_dashboards(dashboard_cards):
    """The dashboard whose addition caused the drift. Pinned so the counts have
    a concrete reason to be what they are."""
    assert "Contact Finder" in dashboard_cards["live"]


def test_a_coming_soon_card_is_counted_as_a_dashboard_but_not_as_live(dashboard_cards):
    assert dashboard_cards["soon"], "expected at least one Coming soon card"
    assert dashboard_cards["total"] == len(dashboard_cards["live"]) + len(dashboard_cards["soon"])


# ── The prose ───────────────────────────────────────────────────────────────

def test_the_card_is_named_b2b_agents(hub_card):
    assert hub_card["title"] == "B2B Agents"


def test_the_description_mentions_contact_lookup(hub_card):
    """A whole live dashboard was missing from the copy."""
    assert "contact lookup" in hub_card["desc"].lower()


def test_the_description_does_not_promise_the_unbuilt_dashboard(hub_card, dashboard_cards):
    """Job Change Alert is still "Coming soon", so listing it alongside shipped
    capabilities oversells the card."""
    assert "job-change" not in hub_card["desc"].lower()
    assert "job change" not in hub_card["desc"].lower()
    assert "Job Change Alert" in dashboard_cards["soon"], \
        "if this shipped, the description should say so and this test should change"


def test_the_description_does_not_call_it_scraping(hub_card):
    """The tool is LinkedIn Intelligence; "LinkedIn scraping" both misnamed it
    and read badly on the busiest page in the app."""
    assert "scraping" not in hub_card["desc"].lower()


def test_the_description_has_no_em_dash(hub_card):
    assert "—" not in hub_card["desc"]


# ── The other card, so this file covers the whole hub ───────────────────────

def test_the_seo_card_count_matches_the_tool_list():
    """Same class of drift, different card: this one is generated from
    _seo_tools(), so it can be checked against the source of truth directly."""
    os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
    os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
    os.environ.setdefault("FLASK_SECRET_KEY", "test")
    import sys
    sys.path.insert(0, _ROOT)
    import app as appmod
    with open(_HUB) as fh:
        body = _strip_comments(fh.read())
    block = body.split('href="/p2/seo"', 1)[1].split("</a>", 1)[0]
    stats = dict((label, int(n)) for n, label in
                 re.findall(r'class="card-stat"><span>(\d+)</span>\s*(\w+)', block))
    assert stats["dashboards"] == len(appmod._seo_tools())


# ── The "by the numbers" band, same hand-maintained-number risk ─────────────

def _hub_band():
    """Parsed from the RENDERED page, not the template: the companies figure is a
    Jinja expression now, so the template source no longer holds the number."""
    os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
    os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
    os.environ.setdefault("FLASK_SECRET_KEY", "test")
    import sys
    sys.path.insert(0, _ROOT)
    import app as appmod
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    body = _strip_comments(c.get("/p2/hub").get_data(as_text=True))
    band = body.split('class="lx-stats2"', 1)[1].split("</section>", 1)[0]
    return dict((label.strip(), int(n)) for n, label in
                re.findall(r'data-lxn="(\d+)"[^>]*>0</b><span>([^<]+)</span>', band))


def test_the_hub_band_dashboard_total_matches_the_live_cards(hub_card, dashboard_cards):
    """The band counts LIVE dashboards across both workspaces (today 6 + 15), so
    it drifts on exactly the same trigger as the card stats above. If someone
    later decides it should count every card including "Coming soon" ones, this
    is the test to change deliberately rather than discover by accident."""
    os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
    os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
    os.environ.setdefault("FLASK_SECRET_KEY", "test")
    import sys
    sys.path.insert(0, _ROOT)
    import app as appmod
    expected = len(dashboard_cards["live"]) + len(appmod._seo_tools())
    assert _hub_band()["dashboards"] == expected, (
        "band says %d dashboards, live cards total %d"
        % (_hub_band()["dashboards"], expected))


def test_the_card_copy_makes_no_unverifiable_headcount_claim(hub_card):
    """The B2B Agents card cites no company figure of its own. The two places that
    do quote one (the band below, and the ABM card on the dashboard page) now both
    derive it from the dashboards, which is what stopped them disagreeing; adding a
    third hardcoded figure here would restart the problem."""
    assert not re.search(r"\d[\d,]*\+", hub_card["desc"]), \
        "quote the derived tracked_companies value or no figure at all"
