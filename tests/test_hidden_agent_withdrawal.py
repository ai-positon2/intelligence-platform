"""An agent can be pulled from the listings without being retired.

LinkedIn Strategy Researcher was withdrawn on 2026-08-14 and is expected back in
a few days, so nothing about it was deleted: the route, the embedded tool, the
APP_AGENTS entry, the client's own curated agent order and every past run all
stay exactly as they were. Only the surfaces that *advertise* an agent skip it.

That split is the thing worth pinning, because it is easy to get wrong in either
direction. Filter too little and the agent is still on a page the owner asked it
off. Filter too much -- APP_AGENTS_BY_SLUG, or the run-history name lookup -- and
a bookmarked link 404s and last month's runs start rendering as a bare slug.

The roster is also famously three independent lists that drift (the hand-written
cards in b2b_agents.html, APP_AGENTS, and each client's ordered slug list), so
each surface is asserted on the rendered page rather than on the data behind it,
and each assertion has a mirror that fails if the filter starts hiding
everything. A test that passes because the page is empty proves nothing.
"""

import os
import re
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import app as appmod  # noqa: E402

_SLUG = "linkedin-strategy-researcher"
_NAME = "LinkedIn Strategy Researcher"
_CLIENT = "northstaranesthesia"


@pytest.fixture
def client(monkeypatch):
    """Signed in as staff, with the two Google Sheets reads on these pages held
    still so the assertions are about the roster and nothing else."""
    monkeypatch.setattr(appmod, "_agent_run_counts", lambda email: {})
    monkeypatch.setattr(appmod, "_agent_access_requested_slugs", lambda email: set())
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T",
                               "given_name": "T"}
    return c


def _body(client, path):
    r = client.get(path)
    assert r.status_code == 200, "%s returned %s" % (path, r.status_code)
    return r.get_data(as_text=True)


def _rendered(client, path):
    """The page as a reader sees it. The b2b dashboard's cards are hand-written
    markup, and the house convention for pulling one is to comment it out rather
    than delete it (see Sentiment Pulse), so the withdrawn card and its palette
    entry are still in the bytes on the wire. Neither renders nor runs, but a
    plain substring check would read them as present -- the same reason
    test_hub_card_counts.py strips comments before counting cards."""
    body = _body(client, path)
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    return re.sub(r"/\*.*?\*/", "", body, flags=re.S)


# ── The three surfaces the owner named ──────────────────────────────────────

def test_the_b2b_agents_dashboard_does_not_list_it(client):
    assert _NAME not in _rendered(client, "/p2/b2b-agents")


def test_the_b2b_agents_dashboard_still_lists_the_others(client):
    """The mirror. A card grid that lost every card would pass the test above."""
    body = _rendered(client, "/p2/b2b-agents")
    for name in ("LinkedIn Intelligence", "Contact Finder", "Anonymous Visitors"):
        assert name in body, "%s went missing too" % name


def test_the_command_palette_does_not_offer_it(client):
    """Ctrl+K is a second listing on the same page and was missed the first time
    a card was pulled from it. The card and the palette entry hide together."""
    body = _rendered(client, "/p2/b2b-agents")
    palette = body.split("var BASE=", 1)[1].split("];", 1)[0]
    assert _SLUG not in palette
    assert "/p2/b2b-agents/linkedin-intelligence" in palette, "the palette emptied out"


def test_the_app_workspace_does_not_list_it(client):
    assert _NAME not in _body(client, "/app")


def test_the_app_workspace_still_lists_the_others(client):
    body = _body(client, "/app")
    for name in ("Keyword Finder", "Content Brief Generator", "Content Enhancer"):
        assert name in body, "%s went missing too" % name


def test_the_app_sidebar_does_not_offer_it(client):
    """app_base.html's sidebar reads the app_agents context processor, not the
    view's own list, so hiding it in one place and not the other leaves the
    agent one click away from every page in the workspace."""
    body = _body(client, "/app/history")
    assert _NAME not in body
    assert "Keyword Finder" in body, "the sidebar emptied out"


def test_the_client_portal_does_not_list_it(client):
    assert _NAME not in _body(client, "/" + _CLIENT)


def test_the_client_portal_still_lists_the_others(client):
    body = _body(client, "/" + _CLIENT)
    for name in ("ABM Signal Tracker", "LinkedIn Intelligence"):
        assert name in body, "%s went missing too" % name


def test_the_client_portal_sidebar_and_related_strip_skip_it(client):
    """Both are built from _client_agents, and the related strip on a sibling
    agent's page is the one that survives a fix applied only to the home page."""
    body = _body(client, "/%s/agents/%s" % (_CLIENT, "signal-tracker"))
    assert _NAME not in body
    assert "Content Enhancer" in body, "the related strip emptied out"


# ── Hidden means unlisted, not gone ─────────────────────────────────────────

def test_the_agent_page_itself_still_resolves():
    """Deliberate: someone who already has the link keeps their tool. Withdrawing
    it from the listings is a merchandising decision, not a shutdown -- compare
    Sentiment Pulse, which was pulled because its data was fake and therefore
    also had its route abort(404)ed."""
    rules = {str(r) for r in appmod.app.url_map.iter_rules()}
    assert "/p2/b2b-agents/" + _SLUG in rules


def test_the_registry_entry_is_untouched():
    assert _SLUG in appmod.APP_AGENTS_BY_SLUG
    assert appmod.APP_AGENTS_BY_SLUG[_SLUG]["name"] == _NAME
    assert appmod.APP_AGENTS_BY_SLUG[_SLUG].get("external_url"), (
        "the tool it embeds was removed, which is a retirement, not a hide")


def test_the_clients_own_agent_order_is_untouched():
    """The ordered slug list is the client's curation. Editing it to hide an
    agent would silently lose the position the agent goes back into."""
    assert _SLUG in appmod.CLIENTS[_CLIENT]["agents"]
    assert appmod.CLIENTS[_CLIENT]["external_tools"].get(_SLUG)


def test_run_history_can_still_name_the_hidden_agent():
    """History and the admin run dashboards look agents up by slug. If they read
    the filtered list, a run logged last week starts rendering as a raw slug."""
    assert appmod.APP_AGENTS_BY_SLUG.get(_SLUG, {}).get("name") == _NAME
    assert _SLUG not in {a["slug"] for a in appmod._visible_app_agents()}
    assert len(appmod._visible_app_agents()) == len(appmod.APP_AGENTS) - 1


# ── Restoring it is one edit ────────────────────────────────────────────────

def test_one_set_drives_every_data_driven_surface():
    assert appmod.HIDDEN_AGENT_SLUGS == {_SLUG}


def test_the_hand_written_card_says_how_to_come_back():
    """b2b_agents.html is not driven by HIDDEN_AGENT_SLUGS -- the cards are typed
    out by hand -- so the commented-out card has to carry the restore note itself
    or the two halves of this change come apart."""
    with open(os.path.join(_ROOT, "templates", "b2b_agents.html")) as fh:
        body = fh.read()
    hidden = re.findall(r"<!--(.*?)-->", body, flags=re.S)
    note = [h for h in hidden if _NAME in h]
    assert note, "the card is not commented out"
    assert "HIDDEN_AGENT_SLUGS" in note[0], "no pointer to the other half of the hide"
    assert 'class="dash-card active c-lir"' in note[0], (
        "the card markup was deleted rather than commented out")
