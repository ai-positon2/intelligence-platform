"""Tests for the depth of a search result.

Reported as "the output from selecting the filters and then searching is not as
good as we get on Apollo. A lot of things are missing that we get for free on
apollo but can't see here."

The cause is a hard limit of Apollo's API rather than of this page. Verified live
against this account, one free mixed_people/api_search row is exactly:

    id, first_name, last_name, title, last_refreshed_at, linkedin_url,
    organization{id, name, domain}

Everything Apollo's own web UI puts next to a person (industry, headcount, HQ,
revenue, funding, tech stack) is COMPANY data, and every endpoint that returns
company data is paid. So the grid was not hiding fields it had; the fields were
never in the response.

Two ways to close the gap without turning a free search into an expensive one,
and these tests pin both:

1. mixed_companies/search charges per CALL, not per company. One call filtered to
   a page's distinct organization_ids describes every employer on that page for a
   single credit, cached by org id afterwards. So the tests here care about: one
   call not N, the cache actually saving the credit, the cost being reported, and
   a failure never costing a row the fields it already had.

2. Seniority and business function are already inside the job title Apollo does
   return, so they are read off it for free -- under separate *_from_title keys,
   because a derived value must never be presentable as one Apollo asserted.
"""

import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402


@pytest.fixture
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com",
                               "name": "Test User", "given_name": "Test"}
    return c


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    """No database and an empty process cache, so every test starts cold and
    nothing leaks a cached employer into the next one."""
    appmod._CPI_FIRMO_CACHE.clear()
    monkeypatch.setattr(appmod, "_pg_conn", lambda: None)
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")
    yield
    appmod._CPI_FIRMO_CACHE.clear()


# One Apollo organization as mixed_companies/search really returns it: the field
# names here are the ones proven in production by _apollo_org_normalize and
# _cpi_company_row, not invented for the test.
def _org(org_id="o1", name="Acme", domain="acme.com"):
    return {
        "id": org_id, "name": name, "primary_domain": domain,
        "logo_url": "https://logo.example/acme.png",
        "website_url": "https://acme.com", "linkedin_url": "https://lnkd.in/acme",
        "twitter_url": "https://x.com/acme",
        "industry": "information technology", "industries": ["software", "saas"],
        "estimated_num_employees": 2400, "founded_year": 2009,
        "annual_revenue": 480000000, "organization_revenue_printed": "480M",
        "total_funding": 92000000, "latest_funding_round_date": "2025-03-14",
        "publicly_traded_symbol": "ACME",
        "phone": "+1 555 0100", "raw_address": "1 Acme Way, San Francisco, CA",
        "city": "San Francisco", "state": "California", "country": "United States",
        "short_description": "Acme builds things.",
        "keywords": ["b2b", "devtools"], "technology_names": ["Salesforce", "AWS"],
        "organization_headcount_six_month_growth": 0.08,
        "organization_headcount_twelve_month_growth": 0.19,
    }


def _person(pid="p1", org_id="o1", title="VP Finance"):
    """A row shaped like the free endpoint's output: no company detail at all."""
    return {"id": pid, "full_name": "Ada Lovelace", "title": title,
            "linkedin_url": "https://lnkd.in/ada",
            "organization_id": org_id, "organization_name": "Acme"}


def _stub_search(monkeypatch, people, orgs, org_fail=False):
    """Stub both Apollo search endpoints and record the calls each one got."""
    import tracker.apollo_client as ac
    calls = {"people": [], "companies": []}

    def _sp(filters, api_key, page=1, per_page=25, strict=False, meta=None):
        calls["people"].append(dict(filters))
        if meta is not None:
            meta["total_entries"] = len(people)
            meta["total_pages"] = 1
        return [dict(p) for p in people]

    def _sc(filters, api_key, page=1, per_page=25, strict=False, meta=None):
        calls["companies"].append(dict(filters))
        if org_fail:
            raise RuntimeError("apollo unreachable")
        wanted = set(filters.get("organization_ids") or [])
        return [dict(o) for o in orgs if not wanted or o.get("id") in wanted]

    monkeypatch.setattr(ac, "search_people", _sp)
    monkeypatch.setattr(ac, "search_companies", _sc)
    return calls


def _search(client, **filters):
    r = client.post("/p2/b2b-agents/company-people-intelligence/search",
                    json={"entity": "people", "filters": filters or {"titles": ["VP"]}})
    assert r.status_code == 200
    return r.get_json()


# ── The staff toggle ─────────────────────────────────────────────────────────
# Describing employers is the only part of a people search that can spend a
# credit, so staff can switch it off. These pin the two things that matter: off
# really means no billable call, and off does not quietly cost the free detail.

def test_turning_company_details_off_makes_the_search_free(client, monkeypatch):
    calls = _stub_search(monkeypatch, [_person()], [_org()])
    out = _search(client, titles=["VP"], company_detail=False)
    assert calls["companies"] == [], "no billable company call may be made"
    assert not out.get("credits")
    assert "companies_described" not in out
    assert out["company_detail"] is False
    # The people themselves still come back in full.
    assert out["results"][0]["full_name"] == "Ada Lovelace"
    assert out["results"][0]["organization_name"] == "Acme"
    assert not out["results"][0].get("organization_industry")


def test_the_free_classification_survives_the_toggle(client, monkeypatch):
    """Reading a title costs nothing, so switching off the paid company lookup
    must not also throw away the seniority and function that come free."""
    _stub_search(monkeypatch, [_person(title="Chief Financial Officer")], [_org()])
    row = _search(client, titles=["CFO"], company_detail=False)["results"][0]
    assert row["seniority_from_title"] == "C-suite"
    assert row["functions_from_title"] == ["finance"]


def test_company_details_default_to_on(client, monkeypatch):
    """The thin card is what this feature replaced, so an untouched page, and any
    caller that does not know about the toggle, gets the full one."""
    calls = _stub_search(monkeypatch, [_person()], [_org()])
    out = _search(client, titles=["VP"])
    assert len(calls["companies"]) == 1
    assert out["company_detail"] is True
    assert out["results"][0]["organization_industry"] == "information technology"


@pytest.mark.parametrize("sent,expected", [
    (False, False), (True, True), ("no", True), (None, True),
])
def test_only_an_explicit_false_switches_it_off(client, monkeypatch, sent, expected):
    """A missing or malformed value must fail towards the richer result, not
    towards silently showing less than the page promises."""
    _stub_search(monkeypatch, [_person()], [_org()])
    out = _search(client, titles=["VP"], company_detail=sent)
    assert out["company_detail"] is expected


def test_the_toggle_never_reaches_apollo(client, monkeypatch):
    """It rides inside `filters` so it is saved and restored with the search, but
    everything left in `filters` goes on to build an Apollo payload, so it has to
    be removed before that happens."""
    calls = _stub_search(monkeypatch, [_person()], [_org()])
    _search(client, titles=["VP"], company_detail=True)
    assert calls["people"], "the people search must still have run"
    for sent in calls["people"] + calls["companies"]:
        assert "company_detail" not in sent


def test_the_companies_tab_has_nothing_to_switch_off(client, monkeypatch):
    """That tab pays for full records either way, so echoing a toggle state for it
    would imply a choice that does not exist."""
    _stub_search(monkeypatch, [], [_org()])
    r = client.post("/p2/b2b-agents/company-people-intelligence/search",
                    json={"entity": "companies", "filters": {"name": "Acme"}})
    assert "company_detail" not in r.get_json()


def test_the_toggle_is_wired_end_to_end():
    """Three separate places have to agree or the control is decorative: the
    checkbox exists, the search sends its state, and the Load more button stops
    quoting a price when it is off."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    html = open(os.path.join(root, "templates",
                             "company_people_intelligence.html")).read()
    js = open(os.path.join(root, "static", "js",
                           "company_people_intelligence.js")).read()
    assert 'id="fpCompanyDetail" checked' in html, "must exist and default to on"
    assert "f.company_detail = companyDetailOn();" in js
    assert "return el ? !!el.checked : true;" in js, "a missing control means on"
    assert "companyDetailOn() ? 'Load more <s>&middot; up to 1 Apollo credit</s>'" in js
    # Restoring a saved search must not read a missing value as "off".
    assert "(f.company_detail===undefined) ? true : !!f.company_detail" in js


def test_the_export_records_whether_company_details_were_on():
    """The spreadsheet's "Search details" sheet is how someone later works out why
    one file has company columns and another does not."""
    out = dict(appmod._cpi_filters_readable({"titles": ["CFO"], "company_detail": False}))
    assert out["Employer details fetched"] == "No"
    on = dict(appmod._cpi_filters_readable({"company_detail": True}))
    assert on["Employer details fetched"] == "Yes"


# ── One credit for the whole page ────────────────────────────────────────────

def test_a_page_of_people_gets_its_employers_described(client, monkeypatch):
    """The whole point: a person row comes back carrying their employer's
    firmographics, which the free people endpoint never returns."""
    _stub_search(monkeypatch, [_person()], [_org()])
    row = _search(client)["results"][0]
    assert row["organization_industry"] == "information technology"
    assert row["organization_employees"] == 2400
    assert row["organization_city"] == "San Francisco"
    assert row["organization_revenue"] == 480000000
    assert row["organization_funding"] == 92000000
    assert row["organization_technologies"] == ["Salesforce", "AWS"]
    assert row["organization_description"] == "Acme builds things."
    assert row["organization_phone"] == "+1 555 0100"
    assert row["organization_growth12"] == 0.19


def test_many_employers_cost_one_lookup_not_one_each(client, monkeypatch):
    """mixed_companies/search bills per call, so a page of people at five
    different companies must be one request with five ids -- looping it would
    multiply the cost by five for identical data."""
    people = [_person("p%d" % i, "o%d" % i) for i in range(5)]
    orgs = [_org("o%d" % i, "Co%d" % i, "co%d.example" % i) for i in range(5)]
    calls = _stub_search(monkeypatch, people, orgs)
    out = _search(client)
    assert len(calls["companies"]) == 1
    assert sorted(calls["companies"][0]["organization_ids"]) == \
        ["o0", "o1", "o2", "o3", "o4"]
    assert out["credits"] == 1
    # Each row gets ITS OWN employer's detail, not the first one's.
    assert {r["organization_domain"] for r in out["results"]} == \
        {"co0.example", "co1.example", "co2.example", "co3.example", "co4.example"}


def test_repeated_employers_are_asked_about_once(client, monkeypatch):
    """Twenty-four people at one company is the common case (a scoped search),
    and it must not send the same id twenty-four times."""
    people = [_person("p%d" % i, "o1") for i in range(24)]
    calls = _stub_search(monkeypatch, people, [_org()])
    _search(client)
    assert calls["companies"][0]["organization_ids"] == ["o1"]


def test_the_second_search_of_the_same_company_is_free(client, monkeypatch):
    """The cache is the reason this is affordable at all: headcount and industry
    do not change between two searches in the same afternoon."""
    calls = _stub_search(monkeypatch, [_person()], [_org()])
    first = _search(client)
    second = _search(client)
    assert len(calls["companies"]) == 1, "the second search must not re-ask Apollo"
    assert first.get("credits") == 1
    assert "credits" not in second or not second["credits"]
    # And the rows are just as complete the second time.
    assert second["results"][0]["organization_employees"] == 2400


def test_the_page_says_whether_it_spent_a_credit(client, monkeypatch):
    """A search that cost a credit and one served from cache must be
    distinguishable on screen, not both silently claiming to be free."""
    _stub_search(monkeypatch, [_person()], [_org()])
    first = _search(client)["companies_described"]
    assert first == {"orgs": 1, "cached": 0, "fetched": 1}
    second = _search(client)["companies_described"]
    assert second == {"orgs": 1, "cached": 1, "fetched": 0}


def test_apollo_returning_nothing_costs_nothing(client, monkeypatch):
    """search_companies bills only a call that matched something, so an id it
    knows nothing about must not be reported as a credit spent."""
    calls = _stub_search(monkeypatch, [_person(org_id="ghost")], [])
    out = _search(client)
    assert len(calls["companies"]) == 1
    assert not out.get("credits")
    assert out["results"][0]["organization_name"] == "Acme"


# ── Never make a row worse ───────────────────────────────────────────────────

def test_a_failed_company_lookup_leaves_the_rows_alone(client, monkeypatch):
    """Apollo being unreachable for the company call must degrade to the old,
    thinner card -- never to a failed search or a blanked row."""
    _stub_search(monkeypatch, [_person()], [_org()], org_fail=True)
    out = _search(client)
    assert out["results"][0]["full_name"] == "Ada Lovelace"
    assert out["results"][0]["organization_name"] == "Acme"
    assert not out.get("credits")


def test_the_persons_own_fields_win_over_their_employers(client, monkeypatch):
    """An enriched row already holds this person's own city. Overwriting it with
    the head-office city would turn a fact about a person into a fact about a
    building while still being labelled as theirs."""
    rows = [{"id": "p1", "full_name": "Ada", "title": "CFO",
             "organization_id": "o1", "organization_name": "Acme",
             "organization_industry": "healthcare", "city": "Bengaluru"}]
    _stub_search(monkeypatch, rows, [_org()])
    row = _search(client)["results"][0]
    assert row["city"] == "Bengaluru"
    assert row["organization_industry"] == "healthcare", \
        "a field the row already had must not be overwritten"


def test_rows_without_an_employer_id_are_skipped_not_dropped(client, monkeypatch):
    """A person Apollo holds no organization for is still a valid result."""
    people = [_person("p1", "o1"), {"id": "p2", "full_name": "Grace Hopper",
                                    "title": "CTO"}]
    calls = _stub_search(monkeypatch, people, [_org()])
    out = _search(client)
    assert calls["companies"][0]["organization_ids"] == ["o1"]
    assert [r["full_name"] for r in out["results"]] == ["Ada Lovelace", "Grace Hopper"]


def test_no_employers_means_no_company_call_at_all(client, monkeypatch):
    """Nothing to look up must not become a request that could still be billed."""
    calls = _stub_search(monkeypatch, [{"id": "p1", "full_name": "Ada"}], [])
    out = _search(client)
    assert calls["companies"] == []
    assert "companies_described" not in out


def test_a_company_search_is_untouched_by_any_of_this(client, monkeypatch):
    """The Companies tab already pays for full records; it must not get a second
    lookup bolted on."""
    calls = _stub_search(monkeypatch, [], [_org()])
    r = client.post("/p2/b2b-agents/company-people-intelligence/search",
                    json={"entity": "companies", "filters": {"name": "Acme"}})
    assert r.status_code == 200
    assert len(calls["companies"]) == 1
    assert "organization_ids" not in calls["companies"][0]


def test_the_employer_id_list_is_capped(monkeypatch):
    """A future larger page size must not silently send an unbounded id list."""
    rows = [_person("p%d" % i, "o%d" % i) for i in range(appmod._CPI_FIRMO_MAX_ORGS + 12)]
    seen = {}
    import tracker.apollo_client as ac
    monkeypatch.setattr(ac, "search_companies",
                        lambda f, k, **kw: seen.setdefault("ids", f["organization_ids"]) and [])
    appmod._cpi_attach_employer_facts(rows, "k", {"credits": 0})
    assert len(seen["ids"]) == appmod._CPI_FIRMO_MAX_ORGS


# ── Read off the title, and labelled as such ─────────────────────────────────

@pytest.mark.parametrize("title,seniority,functions", [
    ("Chief Financial Officer", "C-suite", ["finance"]),
    ("VP Marketing", "VP", ["marketing"]),
    ("Head of Engineering", "Head of function", ["engineering and technology"]),
    ("Director of Analytics", "Director", ["data and analytics"]),
    ("Sales Manager", "Manager", ["sales"]),
    ("Founder", "Founder", []),
])
def test_a_title_yields_its_own_seniority_and_function(title, seniority, functions):
    out = appmod._cpi_derive_role(title)
    assert out.get("seniority_from_title") == seniority
    assert out.get("functions_from_title", []) == functions


@pytest.mark.parametrize("title", [
    "VP Marketing", "VP of Sales", "Vice President of Sales",
    "SVP Marketing", "Vice President, Finance",
])
def test_a_vice_president_is_not_ranked_as_c_suite(title):
    """Found by this file. _cpi_title_tokens expands "vp" to "vice president",
    and "president" is a C-suite token checked before "vp", so every VP came out
    ranked level with the CEO -- both in the label under their name and in the
    chat's ordering of who to contact first."""
    assert appmod._cpi_derive_role(title)["seniority_from_title"] == "VP"
    assert appmod._cpi_seniority_rank({"title": title}) == \
        appmod._CPI_SENIORITY_ORDER.index("vp")
    assert appmod._cpi_seniority_rank({"title": title}) > \
        appmod._cpi_seniority_rank({"title": "Chief Executive Officer"})


@pytest.mark.parametrize("title", [
    "Chief Executive Officer", "President", "Chairman", "Chief Revenue Officer",
])
def test_a_real_c_suite_title_still_ranks_as_c_suite(title):
    """The other half of the fix: blocking on "vice" must not cost the rows that
    were always right."""
    assert appmod._cpi_seniority_rank({"title": title}) <= \
        appmod._CPI_SENIORITY_ORDER.index("c_suite")


@pytest.mark.parametrize("title", ["President", "Founder", "Chief Executive Officer"])
def test_the_executive_band_is_not_printed_as_a_function(title):
    """"the executive team" is a seniority band phrased for chat prose. As a chip
    beside "C-suite" it says the same thing twice and names a department nobody
    works in."""
    funcs = appmod._cpi_derive_role(title).get("functions_from_title", [])
    assert "the executive team" not in funcs
    # Still classified as executive where that matters, for the chat's fallback.
    assert "executive" in appmod._cpi_person_functions({"title": title})


def test_an_unplaceable_title_derives_nothing(client, monkeypatch):
    """A blank is worth more than a guess here: the pair is only useful because a
    reader can trust it."""
    assert appmod._cpi_derive_role("Associate") == {}
    assert appmod._cpi_derive_role("") == {}
    assert appmod._cpi_derive_role(None) == {}


def test_derived_values_never_pose_as_apollos_own(client, monkeypatch):
    """Written to *_from_title only. Putting them in `seniority`/`departments`
    would make a derivation indistinguishable from an assertion, in the grid and
    in every exported spreadsheet after it."""
    _stub_search(monkeypatch, [_person(title="Chief Financial Officer")], [_org()])
    row = _search(client)["results"][0]
    assert row["seniority_from_title"] == "C-suite"
    assert row["functions_from_title"] == ["finance"]
    assert not row.get("seniority")
    assert not row.get("departments")


def test_the_grid_and_the_chat_classify_a_title_the_same_way():
    """Both read the one taxonomy, so a Chief Revenue Officer cannot be marketing
    in a chat answer and not marketing in the grid beside it."""
    assert "marketing" in appmod._cpi_derive_role("Chief Revenue Officer")["functions_from_title"]
    assert "marketing" in appmod._cpi_title_functions("Chief Revenue Officer")
    # Sales and operations both, and marketing NOT: the CRO crossover is gated on
    # seniority, so the revenue TEAM is not offered as a marketing contact.
    assert appmod._cpi_derive_role("Revenue Operations Manager")[
        "functions_from_title"] == ["sales", "operations"]


# ── Export and enrichment carry the same fields ──────────────────────────────

def test_the_export_carries_the_company_detail_and_labels_the_derived_columns(client):
    """A spreadsheet outlives the screen it came from, so a derived value needs
    its warranty written into the column header."""
    keys = dict(appmod._CPI_PERSON_COLS)
    assert keys["seniority_from_title"] == "Seniority (from title)"
    assert keys["functions_from_title"] == "Function (from title)"
    assert keys["seniority"] == "Seniority"
    for k in ("organization_revenue", "organization_technologies",
              "organization_phone", "organization_description",
              "organization_founded", "organization_city"):
        assert k in keys, "%s is fetched but never exported" % k


def test_an_enriched_row_carries_the_same_company_fields():
    """people/bulk_match returns the employer as a full org record, so the
    enriched path must map it through the same helper -- otherwise enriching a
    person would strip company detail the free search had already shown."""
    row = appmod._cpi_person_row({
        "id": "p1", "first_name": "Ada", "last_name": "Lovelace",
        "title": "VP Finance", "organization": _org(),
    })
    assert row["organization_technologies"] == ["Salesforce", "AWS"]
    assert row["organization_description"] == "Acme builds things."
    assert row["organization_phone"] == "+1 555 0100"
    assert row["seniority_from_title"] == "VP"
    assert row["enriched"] is True


def test_a_company_row_shows_the_depth_it_already_paid_for():
    """These fields were in the paid response all along and were being dropped."""
    row = appmod._cpi_company_row(_org())
    assert row["phone"] == "+1 555 0100"
    assert row["raw_address"] == "1 Acme Way, San Francisco, CA"
    assert row["revenue_printed"] == "480M"
    assert row["growth12"] == 0.19
    assert row["industries"] == ["software", "saas"]


@pytest.mark.parametrize("org,expected", [
    ({"phone": "+1 555 0100"}, "+1 555 0100"),
    ({"primary_phone": {"number": "+1 555 0200"}}, "+1 555 0200"),
    ({"sanitized_phone": "+15550300"}, "+15550300"),
    ({"primary_phone": None}, ""),
    ({}, ""),
])
def test_a_company_phone_is_read_from_whichever_shape_apollo_used(org, expected):
    assert appmod._cpi_org_phone(org) == expected
