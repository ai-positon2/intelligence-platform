"""Every filter on the page, audited against what Apollo actually does with it.

The industry bug was not a one-off. Apollo accepts several parameters that read
like filters and behave like relevance hints, so it returns rows that do not
satisfy them. Each filter was checked and falls into one of three groups, and this
file pins the group each one is in so a future change cannot quietly move it.

STRICT SERVER-SIDE, trusted: person_seniorities, NAICS/SIC codes,
  organization_ids, and the numeric ranges (revenue, founded year, funding, open
  jobs, headcount growth, tenure, years of experience). Apollo compares its own
  structured fields numerically or by exact code. Tests here assert only that
  these reach the payload correctly, since there is nothing to re-check.

  contact_email_status was in this group and does not belong there. Measured
  against this account, only two of its four documented values filter anything;
  see test_cpi_vocab_pickers.py, which pins the two that work being the only two
  offered.

RELEVANCE MATCHES, verified in code: industries, employee range, HQ location,
  technologies, and titles when similar titles are switched off. Tests assert a
  row Apollo returned that does not satisfy the filter is removed and counted.

UNVERIFIABLE ON THIS PLAN, labelled honestly: person_locations and email status
  describe fields the free people search does not return, and market_segments is
  documented as matching "the organization's tags and name" with no canonical
  field behind it. Tests assert the UI does not claim otherwise.
"""

import os
import re
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import tracker.apollo_client as ac  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    appmod._CPI_INDUSTRY_SEEN.clear()
    monkeypatch.setattr(appmod, "_pg_conn", lambda: None)
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")
    yield
    appmod._CPI_FIRMO_CACHE.clear()
    appmod._CPI_INDUSTRY_SEEN.clear()


# A page Apollo might plausibly return for "healthcare companies in the US with
# 201-500 employees": one right answer, and one row failing on each separate
# ground, so a check that stops working shows up as a specific row surviving.
_GOOD = {"id": "ok", "name": "Right Answer Health", "primary_domain": "ra.example",
         "industry": "hospital & health care", "estimated_num_employees": 420,
         "city": "Austin", "state": "Texas", "country": "United States",
         "technology_names": ["Google Analytics", "Salesforce"]}
_WRONG_INDUSTRY = {"id": "ind", "name": "Venture Firm", "primary_domain": "vf.example",
                   "industry": "venture capital & private equity",
                   "estimated_num_employees": 380, "city": "Menlo Park",
                   "state": "California", "country": "United States",
                   "technology_names": ["Google Analytics"]}
_TOO_SMALL = {"id": "sml", "name": "Tiny Clinic", "primary_domain": "tc.example",
              "industry": "medical practice", "estimated_num_employees": 60,
              "city": "Austin", "state": "Texas", "country": "United States",
              "technology_names": ["Google Analytics"]}
_TOO_BIG = {"id": "big", "name": "Giant Hospital Group", "primary_domain": "gh.example",
            "industry": "hospital & health care", "estimated_num_employees": 90000,
            "city": "Austin", "state": "Texas", "country": "United States",
            "technology_names": ["Google Analytics"]}
_WRONG_COUNTRY = {"id": "geo", "name": "Bern Medical", "primary_domain": "bm.example",
                  "industry": "medical devices", "estimated_num_employees": 410,
                  "city": "Bern", "state": "Bern", "country": "Switzerland",
                  "technology_names": ["Google Analytics"]}
_WRONG_TECH = {"id": "tec", "name": "No Analytics Health", "primary_domain": "na.example",
               "industry": "medical practice", "estimated_num_employees": 300,
               "city": "Austin", "state": "Texas", "country": "United States",
               "technology_names": ["Adobe Analytics"]}
_PAGE = [_GOOD, _WRONG_INDUSTRY, _TOO_SMALL, _TOO_BIG, _WRONG_COUNTRY, _WRONG_TECH]


def _stub(monkeypatch, orgs=None, people=None):
    orgs = _PAGE if orgs is None else orgs
    sent = {"people": [], "company_payloads": []}

    def _sp(filters, api_key, page=1, per_page=25, strict=False, meta=None):
        sent["people"].append(dict(filters))
        if meta is not None:
            meta["total_entries"] = 9900
            meta["total_pages"] = 400
        return [dict(p) for p in (people or [])]

    def _post(endpoint, payload, api_key, retries=3):
        sent["company_payloads"].append(dict(payload))
        wanted = set(payload.get("organization_ids") or [])
        rows = [dict(o) for o in orgs if not wanted or o.get("id") in wanted]
        return {"organizations": rows,
                "pagination": {"total_entries": 9900, "total_pages": 400}}

    monkeypatch.setattr(ac, "search_people", _sp)
    monkeypatch.setattr(ac, "_post", _post)
    return sent


def _companies(client, **filters):
    r = client.post("/p2/b2b-agents/company-people-intelligence/search",
                    json={"entity": "companies", "filters": filters})
    assert r.status_code == 200
    return r.get_json()


def _people(client, **filters):
    r = client.post("/p2/b2b-agents/company-people-intelligence/search",
                    json={"entity": "people", "filters": filters})
    assert r.status_code == 200
    return r.get_json()


def _person(pid, org_id, title="Chief Marketing Officer"):
    return {"id": pid, "full_name": "P" + pid, "title": title,
            "organization_id": org_id, "organization_name": "Co" + org_id}


# ── The four employer checks, one filter at a time ────────────────────────────

def test_the_size_range_is_enforced_against_the_real_headcount(client, monkeypatch):
    """Apollo only filters by discrete buckets, so a request for 201-500 sends
    every overlapping bucket and companies of 60 and 90,000 come back for it."""
    _stub(monkeypatch)
    out = _companies(client, employee_min=201, employee_max=500)
    names = [c["name"] for c in out["results"]]
    assert "Tiny Clinic" not in names and "Giant Hospital Group" not in names
    assert out["rejected"]["employees"] == 2
    assert out["rejected_labels"]["employees"] == "outside the size range"


@pytest.mark.parametrize("employees,lo,hi,ok", [
    (420, 201, 500, True), (201, 201, 500, True), (500, 201, 500, True),
    (200, 201, 500, False), (501, 201, 500, False),
    (5, 201, None, False), (900000, 201, None, True),
    (5, None, 500, True), (900000, None, 500, False),
    (None, 201, 500, False), ("", 201, 500, False), ("many", 201, 500, False),
])
def test_the_size_check_is_inclusive_and_handles_one_sided_ranges(employees, lo, hi, ok):
    """Boundaries included, an open end left open, and an unknown headcount treated
    as unverified rather than as a pass."""
    assert appmod._cpi_size_ok(employees, lo, hi) is ok


def test_the_hq_filter_is_enforced_against_the_real_location(client, monkeypatch):
    """Apollo matches organization_locations loosely, so a request for one country
    returns companies in others."""
    _stub(monkeypatch)
    out = _companies(client, locations=["United States"])
    assert "Bern Medical" not in [c["name"] for c in out["results"]]
    assert out["rejected"]["hq"] == 1
    assert out["rejected_labels"]["hq"] == "headquartered elsewhere"


@pytest.mark.parametrize("wanted,ok", [
    (["United States"], True), (["united states"], True), (["Texas"], True),
    (["Austin"], True), (["Austin, Texas"], True), (["Austin, TX"], True),
    (["austin, tx"], True), (["New York, NY"], False),
    (["Switzerland"], False), (["Bern, Switzerland"], False),
    (["Switzerland", "United States"], True),
    # The accidental substring matches are gone: "ca" no longer rides along
    # inside "Chicago", and a state that simply is not this one still misses.
    (["Chicago, IL"], False), (["Dallas, TX"], False),
])
def test_the_hq_check_reads_city_state_and_country(wanted, ok):
    """A location can be typed at any level, and "Austin, Texas" spans two fields.

    "Austin, TX" was called an honest miss here, on the reasoning that Apollo
    stores the state unabbreviated and that reading TX as Texas is how a filter
    starts inventing matches. Measured against this account, that reasoning does
    not survive: Apollo's OWN matcher accepts "Austin, TX" and returns Austin
    companies, so refusing the abbreviation did not decline to guess, it threw
    away rows Apollo had already matched and then told the reader they were
    "headquartered elsewhere". The two most natural ways to type a US location,
    "Austin, TX" and "New York, NY", returned nothing at all.

    The state table is a closed, unambiguous list, so expanding it is the same
    normalization _clean_domain already does for www and protocol rather than a
    guess. Matching is by whole word now, which also retires the accidents that
    made this look like it worked: "Boston, MA" and "San Diego, CA" only ever
    passed because "ma" and "ca" happen to sit inside "Massachusetts" and
    "California"."""
    org = {"city": "Austin", "state": "Texas", "country": "United States"}
    assert appmod._cpi_place_matches(org, wanted) is ok


def test_a_company_with_no_location_is_not_waved_through():
    assert appmod._cpi_place_matches({}, ["United States"]) is False


def test_the_technology_filter_is_enforced_against_the_real_stack(client, monkeypatch):
    _stub(monkeypatch)
    out = _companies(client, technologies=["Google Analytics"])
    assert "No Analytics Health" not in [c["name"] for c in out["results"]]
    assert out["rejected"]["technology"] == 1
    assert out["rejected_labels"]["technology"] == "not using the technology"


@pytest.mark.parametrize("typed,expected_uid", [
    ("Google Analytics", "google_analytics"),
    ("google analytics", "google_analytics"),
    ("WordPress.org", "wordpress_org"),
    ("Salesforce", "salesforce"),
    ("  Adobe   Experience Manager ", "adobe_experience_manager"),
    ("Node.js", "node_js"),
    ("", ""),
])
def test_a_technology_becomes_apollos_uid_spelling(typed, expected_uid):
    """Apollo documents these as uids with underscores for spaces and periods.
    Sending "Google Analytics" verbatim is not an error and raises no warning, it
    simply matches no company: the filter looked applied and narrowed nothing."""
    assert ac.tech_uid(typed) == expected_uid


@pytest.mark.parametrize("param,key", [
    ("currently_using_any_of_technology_uids", "technologies"),
    ("currently_using_all_of_technology_uids", "technologies_all"),
    ("currently_not_using_any_of_technology_uids", "exclude_technologies"),
])
def test_every_technology_parameter_is_normalized(param, key, monkeypatch, client):
    """All three, not just the one the UI happens to exercise most."""
    sent = _stub(monkeypatch)
    _companies(client, **{key: ["Google Analytics", "WordPress.org"]})
    payload = sent["company_payloads"][0]
    assert payload[param] == ["google_analytics", "wordpress_org"]


def test_technology_names_are_deduplicated_after_normalizing(monkeypatch, client):
    """"Google Analytics" and "google  analytics" are one technology, and sending
    it twice is a longer request for the same answer."""
    sent = _stub(monkeypatch)
    _companies(client, technologies=["Google Analytics", "google  analytics"])
    assert sent["company_payloads"][0]["currently_using_any_of_technology_uids"] \
        == ["google_analytics"]


def test_every_check_fires_at_once_on_the_reported_shape(client, monkeypatch):
    """The whole audit in one request: the only row satisfying all four filters is
    the only row returned, and every rejection is attributed."""
    _stub(monkeypatch)
    out = _companies(client, industries=["Healthcare"], employee_min=201,
                     employee_max=500, locations=["United States"],
                     technologies=["Google Analytics"])
    assert [c["name"] for c in out["results"]] == ["Right Answer Health"]
    assert out["rejected"] == {"industry": 1, "employees": 2, "hq": 1, "technology": 1}
    assert out["rejected_total"] == 5


def test_a_filter_nobody_asked_for_never_removes_anything(client, monkeypatch):
    """The other half: verification must not narrow a search on its own."""
    _stub(monkeypatch)
    out = _companies(client, name="Health")
    assert len(out["results"]) == len(_PAGE)
    assert "rejected" not in out
    assert out["total"] == 9900


# ── Titles: the checkbox decides ─────────────────────────────────────────────

def test_similar_titles_off_means_the_title_is_enforced(client, monkeypatch):
    """Apollo's person_titles is fuzzy, and with include_similar_titles false it is
    still loose, so asking strictly for a CMO returns Marketing Managers."""
    _stub(monkeypatch, people=[_person("1", "ok"),
                               _person("2", "ok", "Marketing Manager")])
    out = _people(client, titles=["Chief Marketing Officer"],
                  include_similar_titles=False)
    assert [p["id"] for p in out["results"]] == ["1"]
    assert out["rejected"]["title"] == 1
    assert out["rejected_labels"]["title"] == "the wrong title"


def test_similar_titles_on_is_a_request_for_apollos_fuzzy_match(client, monkeypatch):
    """Leaving the box checked asks for near-misses on purpose. Enforcing anyway
    would make the checkbox do nothing, which is its own kind of lie."""
    _stub(monkeypatch, people=[_person("1", "ok"),
                               _person("2", "ok", "Marketing Manager")])
    out = _people(client, titles=["Chief Marketing Officer"],
                  include_similar_titles=True)
    assert [p["id"] for p in out["results"]] == ["1", "2"]
    assert "rejected" not in out


def test_the_default_leaves_apollos_recall_alone(client, monkeypatch):
    """A caller that says nothing about similar titles gets Apollo's behaviour, not
    a silent strict mode."""
    _stub(monkeypatch, people=[_person("1", "ok", "Marketing Manager")])
    out = _people(client, titles=["Chief Marketing Officer"])
    assert len(out["results"]) == 1


# ── People inherit every employer check ──────────────────────────────────────

def test_a_person_is_judged_by_their_employer_on_every_ground(client, monkeypatch):
    """The two tabs must agree about the same company, so the people path runs the
    identical checks against the employer attached to each row."""
    people = [_person(o["id"], o["id"]) for o in _PAGE]
    _stub(monkeypatch, people=people)
    out = _people(client, titles=["CMO"], industries=["Healthcare"],
                  employee_min=201, employee_max=500,
                  company_locations=["United States"],
                  technologies=["Google Analytics"])
    assert [p["id"] for p in out["results"]] == ["ok"]
    assert out["rejected"] == {"industry": 1, "employees": 2, "hq": 1, "technology": 1}


@pytest.mark.parametrize("filter_kwargs", [
    {"industries": ["Healthcare"]},
    {"employee_min": 201},
    {"employee_max": 500},
    {"company_locations": ["United States"]},
    {"technologies": ["Google Analytics"]},
])
def test_any_employer_filter_turns_the_company_lookup_back_on(client, monkeypatch,
                                                              filter_kwargs):
    """None of these can be checked without the employer's own record, and the free
    people search returns none of those fields. Every one of them has to force the
    lookup, not just the industry that happened to be reported."""
    _stub(monkeypatch, people=[_person("ok", "ok")])
    out = _people(client, titles=["CMO"], company_detail=False, **filter_kwargs)
    assert out["company_detail"] is True
    assert out["industry_forced_company_detail"] is True


def test_a_title_only_search_leaves_the_toggle_alone(client, monkeypatch):
    """Titles are on the person, so nothing needs buying to check them."""
    _stub(monkeypatch, people=[_person("ok", "ok")])
    out = _people(client, titles=["CMO"], company_detail=False)
    assert out["company_detail"] is False
    assert "industry_forced_company_detail" not in out


# ── Filters that are strict server-side: assert the payload, trust Apollo ─────

@pytest.mark.parametrize("sent,param,expected", [
    ({"seniorities": ["c_suite", "vp"]}, "person_seniorities", ["c_suite", "vp"]),
    ({"email_status": ["verified"]}, "contact_email_status", ["verified"]),
    ({"person_locations": ["Austin, TX"]}, "person_locations", ["Austin, TX"]),
    ({"linkedin_urls": ["https://linkedin.com/in/x"]}, "person_linkedin_urls",
     ["https://linkedin.com/in/x"]),
    ({"keywords": "oncology"}, "q_keywords", "oncology"),
    ({"naics_codes": ["5415"]}, "organization_naics_codes", ["5415"]),
    ({"sic_codes": ["7372"]}, "organization_sic_codes", ["7372"]),
    ({"market_segments": ["B2B"]}, "market_segments", ["B2B"]),
])
def test_a_strict_filter_reaches_apollo_unchanged(sent, param, expected):
    from unittest.mock import MagicMock, patch
    with patch("tracker.apollo_client.requests.post") as post:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"people": []}
        post.return_value = resp
        ac.search_people(sent, "k")
        assert post.call_args.kwargs["json"][param] == expected


@pytest.mark.parametrize("lo,hi,param,expected", [
    ("revenue_min", "revenue_max", "revenue_range", {"min": 1, "max": 2}),
    ("founded_min", "founded_max", "organization_founded_year_range",
     {"min": 1, "max": 2}),
    ("yoe_min", "yoe_max", "person_total_yoe_range", {"min": 1, "max": 2}),
    ("num_jobs_min", "num_jobs_max", "organization_num_jobs_range",
     {"min": 1, "max": 2}),
])
def test_a_numeric_range_reaches_apollo_as_a_range(lo, hi, param, expected):
    from unittest.mock import MagicMock, patch
    with patch("tracker.apollo_client.requests.post") as post:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"people": []}
        post.return_value = resp
        ac.search_people({lo: 1, hi: 2}, "k")
        assert post.call_args.kwargs["json"][param] == expected


def test_tenure_is_sent_in_days_though_the_ui_collects_months():
    from unittest.mock import MagicMock, patch
    with patch("tracker.apollo_client.requests.post") as post:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"people": []}
        post.return_value = resp
        ac.search_people({"days_in_title_min": 90, "days_in_title_max": 730}, "k")
        assert post.call_args.kwargs["json"]["person_days_in_current_title_range"] \
            == {"min": 90, "max": 730}


# ── Filters that cannot be verified must not claim to be ─────────────────────

def test_market_segments_is_labelled_as_the_keyword_match_it_is():
    """Apollo documents this as matching "the organization's tags and name", which
    is the same mechanism that returned a venture firm for healthcare. There is no
    canonical field behind it, so it cannot be verified and must not be presented
    as a segment filter."""
    html = open(os.path.join(_ROOT, "templates",
                             "company_people_intelligence.html")).read()
    import re as _re
    for pre in ("fp", "fc"):
        tag = _re.search(r'<input[^>]*id="%sSegments"[^>]*>' % pre, html).group(0)
        assert "keyword tags" in tag and "NAME" in tag, \
            "%sSegments must say it matches names and tags" % pre


def _combo_keys(js):
    """The input ids the page turns into pickers, read from the registry itself so
    a new picker is covered by these tests without touching them."""
    block = js[js.index("var COMBO_SPECS"):js.index("var COMBO_FORMATS")]
    return re.findall(r'\["(\w+)",\s*"', block)


def test_the_person_location_filter_does_not_promise_verification():
    """Apollo's free people search returns no city or country, so a person-level
    location cannot be checked without paying per person."""
    html = open(os.path.join(_ROOT, "templates",
                            "company_people_intelligence.html")).read()
    chunk = html[html.index('id="fpLocation"') - 200:
                 html.index('id="fpLocation"') + 500]
    assert "Apollo" in chunk


def test_the_industry_input_is_a_picker_not_a_free_text_box():
    html = open(os.path.join(_ROOT, "templates",
                            "company_people_intelligence.html")).read()
    for pre in ("fp", "fc"):
        assert 'id="%sIndustryList"' % pre in html
        assert 'id="%sIndustryChips"' % pre in html


def test_the_picker_chips_are_what_gets_searched():
    """The input box holds a half-typed word; the chips hold the filter.

    Asserted through the combo registry rather than against one hand-written line
    per filter, so adding a picker cannot leave this test passing while the new
    filter quietly reads its half-typed box instead of its chips.
    """
    js = open(os.path.join(_ROOT, "static", "js",
                          "company_people_intelligence.js")).read()
    assert 'applyCombos("fp", f)' in js
    assert 'applyCombos("fc", f)' in js
    # Every picker's id must be absent from the plain-text field specs, or
    # applySpecs would set the same Apollo key from the box and overwrite the
    # chips with whatever was mid-word when Search was pressed.
    specs = js[js.index("var PEOPLE_FIELDS"):js.index("window.cpiClearFilters")]
    for key in _combo_keys(js):
        assert '["%s"' % key not in specs, \
            "%s is a picker, so it must not also be a free-text spec" % key


def test_the_result_count_is_grammatical_in_the_singular():
    """A company-scoped search often returns exactly one row, and "1 companies" is
    the kind of small wrongness that makes the rest look unchecked."""
    js = open(os.path.join(_ROOT, "static", "js",
                          "company_people_intelligence.js")).read()
    assert 'n===1 ? "company" : "companies"' in js
    assert 'n===1 ? "person" : "people"' in js
    assert '(STATE.entity==="people"?"people":"companies")' not in js, \
        "the unconditional plural must be gone"
