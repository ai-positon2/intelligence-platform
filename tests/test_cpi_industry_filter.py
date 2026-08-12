"""Tests for the industry filter actually filtering by industry.

Reported against a real search: Industry "Healthcare", United States, 201-500
employees returned American Society of Clinical Oncology (genuinely healthcare),
then Sprinto (compliance automation), Sequoia Capital Operations LLC (a venture
firm) and Calm.com (a meditation app).

The cause is that Apollo has no industry filter. `industries` was mapped onto
q_organization_keyword_tags, which is a free-text RELEVANCE match over a
company's name and keyword tags. Verified live on this account through the free
people endpoint: q_organization_keyword_tags=["Healthcare"] returns SCALE
Healthcare, Hummingbird Healthcare, Voca Healthcare and LiquidAgents Healthcare,
companies picked for having the word in their NAME. A venture firm that lists
healthcare among its investment themes matches just as well.

So the parameter stays as a recall net and the filter is enforced in code against
`industry`/`industries`, the fields holding Apollo's own classification. Same
pattern the domain filter and the title check already use: ask Apollo broadly,
guarantee the answer ourselves.

The tests below use the four companies from the report, with the Apollo industry
values those companies actually carry.
"""

import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import tracker.apollo_client as ac  # noqa: E402


@pytest.fixture
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com",
                               "name": "Test User", "given_name": "Test"}
    return c


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    appmod._CPI_FIRMO_CACHE.clear()
    monkeypatch.setattr(appmod, "_pg_conn", lambda: None)
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")
    yield
    appmod._CPI_FIRMO_CACHE.clear()


# The reported page. Every "keywords" entry is why Apollo matched the company for
# "Healthcare"; every "industry" is what the company actually is.
_ASCO = {"id": "o1", "name": "American Society of Clinical Oncology",
         "primary_domain": "asco.org", "industry": "hospital & health care",
         "keywords": ["oncology", "healthcare", "research"]}
_SPRINTO = {"id": "o2", "name": "Sprinto", "primary_domain": "sprinto.com",
            "industry": "computer software",
            "keywords": ["compliance", "healthcare", "hipaa", "saas"]}
_SEQUOIA = {"id": "o3", "name": "Sequoia Capital Operations LLC",
            "primary_domain": "sequoiacap.com",
            "industry": "venture capital & private equity",
            "keywords": ["investing", "healthcare", "fintech"]}
_CALM = {"id": "o4", "name": "Calm.com, Inc.", "primary_domain": "calm.com",
         "industry": "health, wellness & fitness",
         "keywords": ["meditation", "wellness", "healthcare"]}
_REPORTED_PAGE = [_ASCO, _SPRINTO, _SEQUOIA, _CALM]


def _stub(monkeypatch, orgs, people=None):
    calls = {"people": [], "companies": []}

    def _sp(filters, api_key, page=1, per_page=25, strict=False, meta=None):
        calls["people"].append(dict(filters))
        if meta is not None:
            meta["total_entries"] = 9900
            meta["total_pages"] = 400
        return [dict(p) for p in (people or [])]

    monkeypatch.setattr(ac, "search_people", _sp)
    # search_companies is NOT stubbed: its own industry enforcement is part of
    # what these tests cover, so only its transport is replaced.
    def _post(endpoint, payload, api_key, retries=3):
        calls["companies"].append(dict(payload))
        wanted = set(payload.get("organization_ids") or [])
        rows = [dict(o) for o in orgs if not wanted or o.get("id") in wanted]
        return {"organizations": rows,
                "pagination": {"total_entries": 9900, "total_pages": 400}}

    monkeypatch.setattr(ac, "_post", _post)
    return calls


# ── The matcher ──────────────────────────────────────────────────────────────

def test_the_reported_page_keeps_only_the_healthcare_companies():
    kept, dropped = ac.filter_by_industry(_REPORTED_PAGE, ["Healthcare"])
    assert [o["name"] for o in kept] == ["American Society of Clinical Oncology",
                                         "Calm.com, Inc."]
    assert dropped == 2


def test_a_company_is_judged_by_its_industry_not_its_keywords():
    """All four rows list "healthcare" in keywords. Only two are healthcare
    companies. Reading keywords is precisely the bug."""
    for org in (_SPRINTO, _SEQUOIA):
        assert "healthcare" in org["keywords"]
        kept, _ = ac.filter_by_industry([org], ["Healthcare"])
        assert kept == [], "%s is not a healthcare company" % org["name"]


@pytest.mark.parametrize("apollo_industry", [
    "hospital & health care", "medical practice", "medical devices",
    "pharmaceuticals", "biotechnology", "mental health care",
    "health, wellness & fitness", "veterinary",
])
def test_healthcare_covers_the_industries_apollo_files_it_under(apollo_industry):
    """Nothing in Apollo's taxonomy is spelled "healthcare", so a strict string
    match on the typed word would have returned nothing at all."""
    kept, _ = ac.filter_by_industry([{"industry": apollo_industry}], ["healthcare"])
    assert len(kept) == 1


@pytest.mark.parametrize("typed,industry,expected", [
    ("healthcare", "computer software", False),
    ("software", "computer software", True),
    ("computer software", "computer software", True),
    ("saas", "internet", True),
    ("fintech", "banking", True),
    ("finance", "venture capital & private equity", True),
    ("finance", "hospital & health care", False),
    ("Technology", "INFORMATION TECHNOLOGY & SERVICES", True),
    ("hospital and health care", "hospital & health care", True),
    ("legal", "law practice", True),
    ("legal", "legal services", True),
    ("nonprofit", "nonprofit organization management", True),
    ("non-profit", "nonprofit organization management", True),
])
def test_terms_map_onto_apollos_values(typed, industry, expected):
    kept, _ = ac.filter_by_industry([{"industry": industry}], [typed])
    assert bool(kept) is expected


def test_an_unknown_term_still_matches_by_substring():
    """The families table cannot list every industry, so a term that names none of
    them must still work rather than silently matching nothing."""
    kept, _ = ac.filter_by_industry([{"industry": "wine & spirits"}], ["wine"])
    assert len(kept) == 1
    kept, _ = ac.filter_by_industry([{"industry": "wine & spirits"}], ["mining"])
    assert kept == []


def test_the_secondary_industries_list_counts_too():
    """Apollo files some companies under a primary industry that is not the one
    they would be searched by, with the rest in `industries`."""
    org = {"industry": "information technology & services",
           "industries": ["hospital & health care"]}
    kept, _ = ac.filter_by_industry([org], ["Healthcare"])
    assert len(kept) == 1


def test_a_stored_value_broader_than_the_request_still_matches():
    """The other matching direction. A request for the exact Apollo value
    "Hospital & Health Care" has to keep a record filed under the terser "health
    care": one is a narrower spelling of the other, not a different industry."""
    kept, _ = ac.filter_by_industry([{"industry": "health care"}],
                                    ["Hospital & Health Care"])
    assert len(kept) == 1
    # And still says no to something that merely shares a word.
    kept, _ = ac.filter_by_industry([{"industry": "personal care"}],
                                    ["Hospital & Health Care"])
    assert kept == []


def test_a_company_with_no_classification_is_dropped():
    """An unverifiable row is the exact row this check exists to stop. Keeping it
    would put the original bug back for that record."""
    kept, dropped = ac.filter_by_industry(
        [{"name": "Mystery Co"}, {"industry": ""}, _ASCO], ["Healthcare"])
    assert [o["name"] for o in kept] == ["American Society of Clinical Oncology"]
    assert dropped == 2


def test_no_industry_asked_for_means_nothing_is_touched():
    for terms in ([], None, [""], ["  "]):
        kept, dropped = ac.filter_by_industry(_REPORTED_PAGE, terms)
        assert len(kept) == 4 and dropped == 0


# ── The company search ───────────────────────────────────────────────────────

def test_the_company_search_still_asks_apollo_broadly(client, monkeypatch):
    """The keyword-tag parameter is a useful recall net and stays. It is the
    guarantee that moves into code, not the request."""
    calls = _stub(monkeypatch, _REPORTED_PAGE)
    client.post("/p2/b2b-agents/company-people-intelligence/search",
                json={"entity": "companies", "filters": {"industries": ["Healthcare"]}})
    assert calls["companies"][0]["q_organization_keyword_tags"] == ["Healthcare"]


def test_the_company_search_returns_only_the_industry_asked_for(client, monkeypatch):
    _stub(monkeypatch, _REPORTED_PAGE)
    r = client.post("/p2/b2b-agents/company-people-intelligence/search",
                    json={"entity": "companies",
                          "filters": {"industries": ["Healthcare"]}})
    out = r.get_json()
    assert [c["name"] for c in out["results"]] == \
        ["American Society of Clinical Oncology", "Calm.com, Inc."]
    assert out["industry_dropped"] == 2
    assert out["industry_wanted"] == ["Healthcare"]


def test_apollos_total_is_dropped_once_we_filter_it_ourselves(client, monkeypatch):
    """"9.9K matches in Apollo" counted Apollo's looser match. Reporting it beside
    a page we just pruned overstates the real number by whatever we removed."""
    _stub(monkeypatch, _REPORTED_PAGE)
    r = client.post("/p2/b2b-agents/company-people-intelligence/search",
                    json={"entity": "companies",
                          "filters": {"industries": ["Healthcare"]}})
    assert r.get_json()["total"] is None


def test_an_unfiltered_company_search_keeps_its_total(client, monkeypatch):
    """The other side of that: without an industry filter there is nothing to
    prune, so Apollo's own count is still the honest one."""
    _stub(monkeypatch, _REPORTED_PAGE)
    r = client.post("/p2/b2b-agents/company-people-intelligence/search",
                    json={"entity": "companies", "filters": {"name": "Acme"}})
    out = r.get_json()
    assert out["total"] == 9900
    assert len(out["results"]) == 4
    assert "industry_dropped" not in out


# ── The people search ────────────────────────────────────────────────────────

def _person(pid, org_id):
    return {"id": pid, "full_name": "P" + pid, "title": "VP Marketing",
            "organization_id": org_id, "organization_name": "Co" + org_id}


def test_people_are_filtered_by_their_employers_industry(client, monkeypatch):
    people = [_person("1", "o1"), _person("2", "o2"), _person("3", "o3"),
              _person("4", "o4")]
    _stub(monkeypatch, _REPORTED_PAGE, people)
    r = client.post("/p2/b2b-agents/company-people-intelligence/search",
                    json={"entity": "people",
                          "filters": {"titles": ["VP"], "industries": ["Healthcare"]}})
    out = r.get_json()
    assert [p["id"] for p in out["results"]] == ["1", "4"]
    assert out["industry_dropped"] == 2


def test_a_persons_employer_is_judged_on_its_full_classification(client, monkeypatch):
    """Same rule as the Companies tab: the secondary industries count. A person at
    an IT-services company that Apollo also files under hospital & health care is a
    real answer to a healthcare search, and dropping them would make the two tabs
    disagree about the same employer."""
    org = {"id": "o9", "name": "Mixed Co", "primary_domain": "mixed.example",
           "industry": "information technology & services",
           "industries": ["hospital & health care"]}
    _stub(monkeypatch, [org], [_person("9", "o9")])
    r = client.post("/p2/b2b-agents/company-people-intelligence/search",
                    json={"entity": "people",
                          "filters": {"titles": ["VP"], "industries": ["Healthcare"]}})
    out = r.get_json()
    assert [p["id"] for p in out["results"]] == ["9"]
    assert not out.get("industry_dropped")


def test_an_industry_search_turns_the_company_lookup_back_on(client, monkeypatch):
    """Apollo's free people search returns no industry, so the employer has to be
    described before the filter can be honored. Asking for an industry and no
    company detail is a contradiction, and the filter that was typed wins."""
    _stub(monkeypatch, _REPORTED_PAGE, [_person("1", "o1"), _person("3", "o3")])
    r = client.post("/p2/b2b-agents/company-people-intelligence/search",
                    json={"entity": "people",
                          "filters": {"titles": ["VP"], "industries": ["Healthcare"],
                                      "company_detail": False}})
    out = r.get_json()
    assert out["company_detail"] is True
    assert out["industry_forced_company_detail"] is True
    assert [p["id"] for p in out["results"]] == ["1"]


def test_the_toggle_is_left_alone_when_no_industry_is_asked_for(client, monkeypatch):
    """Forcing it must be specific to the contradiction, not a way to quietly
    ignore the toggle."""
    _stub(monkeypatch, _REPORTED_PAGE, [_person("1", "o1")])
    r = client.post("/p2/b2b-agents/company-people-intelligence/search",
                    json={"entity": "people",
                          "filters": {"titles": ["VP"], "company_detail": False}})
    out = r.get_json()
    assert out["company_detail"] is False
    assert "industry_forced_company_detail" not in out


def test_the_page_says_what_it_removed():
    """A page of 24 arriving as 18 looks like Apollo is thin on matches unless the
    header explains it."""
    js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "static", "js", "company_people_intelligence.js")).read()
    assert "function industryNote()" in js
    assert '" outside "' in js
    assert "firmoNote() + industryNote()" in js
