"""The client-name autocomplete: pick a real company before "Read their site"
ever runs.

The feature this replaces a guess with: a client name typed as "Amazom" (a
typo) against https://amazon.com made it all the way to a model deciding "no
single company can be identified" -- a real, live failure. Choosing from a
company search dropdown means the domain is settled by a name someone
actually confirmed, before any model ever has to reconcile a typo against
search noise.

Two things are under test. The Flask route (`/search`), which is Social
Media Intelligence's own company-search adapter reused as-is -- it has no
SCI-specific coupling, it is a generic Apollo wrapper -- with a fallback to
this agent's OWN already-set-up client profiles rather than SCI's run
history. And the page's real script, executed in node, which renders the
dropdown and fills the form on a pick.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SECRET_KEY", "test-only")

import app as appmod  # noqa: E402
from tracker import sci_company_search, event_intel_store  # noqa: E402
# The node harness the rest of the form tests drive, imported rather than
# copied: two DOM shims drift, and the last time one did it fabricated a node
# per id and made half a file pass vacuously.
from test_event_intel_form_init import _run, keys  # noqa: E402,F401

_PAGE = "/p2/strategic-agents/event-conference-intelligence"
_SEARCH = _PAGE + "/search"


def _client(email=None):
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


# ── the route ───────────────────────────────────────────────────────────

def test_an_empty_query_returns_no_companies_without_calling_apollo(monkeypatch):
    def unexpected(*a, **k):
        pytest.fail("Apollo was called for an empty query")
    monkeypatch.setattr(sci_company_search, "search_companies_result", unexpected)
    out = _client("evi-search@position2.com").get(_SEARCH + "?q=").get_json()
    assert out == {"companies": []}


def test_a_successful_search_is_passed_through_as_is(monkeypatch):
    companies = [{"id": "1", "name": "Northwind Analytics", "logo": None,
                 "industry": "Analytics", "location": "Boston, MA, USA",
                 "description": None, "summary": None, "followers_count": None,
                 "profile_url": None, "website": "https://northwind.example"}]
    monkeypatch.setattr(sci_company_search, "search_companies_result",
                       lambda q: {"companies": companies, "error": None,
                                 "elapsed_ms": 40, "source": "mixed_companies/search"})
    out = _client("evi-search@position2.com").get(_SEARCH + "?q=Northwind").get_json()
    assert out == {"companies": companies}


def test_a_provider_failure_falls_back_to_this_users_own_profiles(monkeypatch):
    """The route wiring only -- search_known_profiles' own real-Postgres
    behaviour (name matching, account scoping, dedup) is covered directly in
    test_event_intel_store_postgres.py, so this stays independent of a real
    database like every other test in this file."""
    seen = {}
    known = [{"id": "", "name": "Northwind Analytics", "logo": None,
             "industry": None, "location": None, "description": None,
             "summary": None, "followers_count": None, "profile_url": None,
             "website": "https://northwind.example", "from_history": True}]
    def fake_search_known(email, q, limit=8):
        seen["email"], seen["q"] = email, q
        return known
    monkeypatch.setattr(event_intel_store, "search_known_profiles", fake_search_known)
    monkeypatch.setattr(
        sci_company_search, "search_companies_result",
        lambda q: {"companies": [], "error": {"kind": "timeout", "detail": "..."},
                  "elapsed_ms": 5000, "source": ""})
    out = _client("evi-search-fallback@position2.com").get(_SEARCH + "?q=North").get_json()
    assert out["error"]["code"] == "timeout"
    assert out["companies"] == known
    assert seen == {"email": "evi-search-fallback@position2.com", "q": "North"}


def test_a_provider_failure_detail_is_withheld_from_a_non_admin(monkeypatch):
    monkeypatch.setattr(
        sci_company_search, "search_companies_result",
        lambda q: {"companies": [], "error": {"kind": "timeout", "detail": "secret backend detail"},
                  "elapsed_ms": 5000, "source": ""})
    out = _client("not-an-admin@position2.com").get(_SEARCH + "?q=North").get_json()
    assert "detail" not in out["error"]


def test_search_is_refused_without_a_session():
    assert _client(None).get(_SEARCH + "?q=Northwind").status_code in (302, 401, 403)


# ── the page's real script ─────────────────────────────────────────────

def _search_probe(reply, extra=""):
    return (
        "__fetchReply = {ok: true, body: %s};\n"
        "document.getElementById('clientName').value = %r;\n"
        "%s\n"
        "runClientSearch().then(function(){\n"
        "  var panel = document.getElementById('eviSuggestPanel');\n"
        "  console.log(JSON.stringify({\n"
        "    fetches: __state.fetches,\n"
        "    panelHtml: panel.innerHTML,\n"
        "    panelShown: panel.style.display,\n"
        "    name: document.getElementById('clientName').value,\n"
        "    site: document.getElementById('clientSite').value\n"
        "  }));\n"
        "});" % (__import__("json").dumps(reply), "Northwind", extra))


_ONE_COMPANY = {"companies": [{
    "id": "1", "name": "Northwind Analytics", "logo": "https://cdn.example/n.png",
    "industry": "Analytics", "location": "Boston, MA, USA",
    "website": "https://northwind.example"}]}


def test_below_two_characters_never_calls_the_server(keys):
    out = _run(
        "document.getElementById('clientName').value = 'N';\n"
        "runClientSearch().then(function(){\n"
        "  console.log(JSON.stringify({fetches: __state.fetches, "
        "shown: document.getElementById('eviSuggestPanel').style.display}));"
        "});",
        *keys)
    assert out["fetches"] == 0
    assert out["shown"] == "none"


def test_a_match_is_rendered_with_its_name_and_website(keys):
    out = _run(_search_probe(_ONE_COMPANY), *keys)
    assert out["fetches"] == 1
    assert out["panelShown"] == "block"
    assert "Northwind Analytics" in out["panelHtml"]
    assert "northwind.example" in out["panelHtml"]


def test_a_company_with_no_logo_falls_back_to_an_initial_letter(keys):
    body = {"companies": [dict(_ONE_COMPANY["companies"][0], logo=None)]}
    out = _run(_search_probe(body), *keys)
    assert "evi-suggest-nologo" in out["panelHtml"]
    assert ">N<" in out["panelHtml"]


def test_an_existing_profile_is_marked_as_already_set_up(keys):
    body = {"companies": [dict(_ONE_COMPANY["companies"][0], from_history=True,
                               industry=None, location=None)]}
    out = _run(_search_probe(body), *keys)
    assert "Already set up as a client" in out["panelHtml"]


def test_no_matches_says_so_without_treating_it_as_an_error(keys):
    out = _run(_search_probe({"companies": []}), *keys)
    assert "No matches" in out["panelHtml"]
    assert "evi-suggest-error" not in out["panelHtml"]


def test_a_provider_error_is_shown_as_an_error_not_a_silent_empty_list(keys):
    out = _run(_search_probe({"companies": [], "error": {
        "code": "timeout", "message": "The company search timed out. Try again."}}),
        *keys)
    assert "evi-suggest-error" in out["panelHtml"]
    assert "timed out" in out["panelHtml"]


def test_picking_a_result_fills_the_name_and_website_and_closes_the_panel(keys):
    probe = _search_probe(_ONE_COMPANY).replace(
        "runClientSearch().then(function(){",
        "runClientSearch().then(function(){\n  pickClient(0);")
    out = _run(probe, *keys)
    assert out["name"] == "Northwind Analytics"
    assert out["site"] == "https://northwind.example"
    assert out["panelShown"] == "none"
    assert out["panelHtml"] == ""


def test_a_reply_for_an_abandoned_query_does_not_overwrite_a_newer_one(keys):
    """No request is cancelled on a fresh keystroke, so the response itself
    has to notice it arrived late. Without the guard, typing past a slow
    reply for an earlier, shorter query would silently repopulate the panel
    with stale results for text nobody is looking at anymore."""
    out = _run(
        "__fetchReply = {ok: true, body: %s};\n"
        "document.getElementById('clientName').value = 'Northwind';\n"
        "var p = runClientSearch();\n"
        "document.getElementById('clientName').value = 'Something Else';\n"
        "p.then(function(){\n"
        "  console.log(JSON.stringify({\n"
        "    panelHtml: document.getElementById('eviSuggestPanel').innerHTML\n"
        "  }));\n"
        "});" % __import__("json").dumps(_ONE_COMPANY),
        *keys)
    assert "Northwind Analytics" not in out["panelHtml"], (
        "a stale reply overwrote the panel after the input had already changed")
