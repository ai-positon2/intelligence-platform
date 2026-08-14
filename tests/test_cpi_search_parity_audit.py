"""Second audit pass, asking the same question as the first: where does this
tool state as a fact about the world something that is only a fact about the
request, or about our own post-processing?

Four more places, found by walking every Apollo call site rather than waiting
for the next report:

  - get_leadership read one of Apollo's two people arrays, exactly as
    search_people did. A company whose people this team has already saved came
    back with no leadership at all, which is the worst possible answer from a
    lookup whose whole job is naming the people at the top. The merge now lives
    in one function both call sites share, because the same defect existing
    independently in two places is what a rule written twice eventually costs.

  - Local filtering was treated as invalidating Apollo's PAGE count as well as
    its row count. It does not: how many pages Apollo will serve is a fact about
    Apollo. Reading it as unknown made "is there more" fall back to counting the
    rows that survived filtering, so removing one stray row from a page of 24
    hid "Load more" completely. A company with 355 people in Apollo showed 23,
    with nothing on screen suggesting the other 332 existed. This is the
    quietest defect in the set: nothing fails, nothing is empty, the page just
    stops early.

  - The employer-HQ check tested each part of a typed location as a raw
    substring, so "Austin, TX" and "New York, NY" -- the two most natural ways
    to type a US location, both of which Apollo's own matcher accepts -- removed
    every row and reported them as "headquartered elsewhere".

  - A profile lookup that never reached Apollo rendered as "Apollo has no
    organization record for this company". A reader acts on that by giving up.
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

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = os.path.join(_ROOT, "static", "js", "company_people_intelligence.js")

_SEARCH = "/p2/b2b-agents/company-people-intelligence/search"
_DOMAIN = "betabionics.com"
_ORG = {"id": "57c4ab6ca6da98689038ddf4", "name": "Beta Bionics", "domain": _DOMAIN}


def _js():
    return open(_JS, encoding="utf-8").read()


@pytest.fixture
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    return c


@pytest.fixture(autouse=True)
def apollo_key(monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "")


def _person(pid, org=None):
    return {"id": pid, "first_name": "A", "last_name": pid, "title": "Engineer",
            "organization": org or _ORG}


def _apollo(monkeypatch, payload):
    monkeypatch.setattr(ac, "_post", lambda ep, b, k, retries=3: payload)


# ── get_leadership reads both buckets too ──────────────────────────────────

def test_leadership_includes_people_already_saved_as_contacts(monkeypatch):
    """The people most likely to be saved as contacts are the senior ones, so
    reading only `people` hit this lookup hardest of all."""
    _apollo(monkeypatch, {
        "people": [],
        "contacts": [{"id": "contact-id", "person_id": "person-id",
                      "first_name": "Steven", "last_name": "Russell",
                      "title": "Chief Medical Officer", "organization": _ORG}],
    })
    rows = ac.get_leadership("57c4ab6ca6da98689038ddf4", "k")
    assert [r["full_name"] for r in rows] == ["Steven Russell"]
    assert rows[0]["id"] == "person-id", "the contact id leaked into the person id"
    assert rows[0]["is_saved_contact"] is True


def test_both_people_call_sites_share_one_merge():
    """Written twice is how the same defect came to exist independently in two
    functions. This is the guard against a third."""
    import inspect
    for fn in (ac.search_people, ac.get_leadership):
        assert "_merge_people_buckets" in inspect.getsource(fn), (
            "%s hand-rolls its own bucket handling again" % fn.__name__)


# ── Load more must survive our own filtering ───────────────────────────────

def test_one_stray_row_does_not_hide_the_other_fourteen_pages(client, monkeypatch):
    """Apollo's relevance net lets the odd wrong-company row onto a page. We
    remove it. That must not also remove the reader's way to page 2."""
    other = {"id": "o2", "name": "Other", "domain": "other.com"}
    page = [_person("p%d" % i, _ORG if i else other) for i in range(24)]
    _apollo(monkeypatch, {"people": page,
                          "pagination": {"total_entries": 355, "total_pages": 15}})
    body = client.post(_SEARCH, json={"entity": "people", "page": 1,
                                      "filters": {"company_domains": [_DOMAIN],
                                                  "company_detail": False}}).get_json()
    assert len(body["results"]) == 23
    assert body["has_more"] is True, "Load more vanished with 14 pages still to serve"


def test_load_more_survives_when_apollo_sends_no_page_count(client, monkeypatch):
    """The other half of the same bug. With no pagination block to lean on,
    "is there more" falls back to counting rows -- and it has to count the rows
    APOLLO served, not the ones that survived our checks, or removing a single
    stray row again ends the list early."""
    other = {"id": "o2", "name": "Other", "domain": "other.com"}
    page = [_person("p%d" % i, _ORG if i else other) for i in range(24)]
    _apollo(monkeypatch, {"people": page})          # no pagination at all
    body = client.post(_SEARCH, json={"entity": "people", "page": 1,
                                      "filters": {"company_domains": [_DOMAIN],
                                                  "company_detail": False}}).get_json()
    assert len(body["results"]) == 23
    assert body["has_more"] is True, "a full page from Apollo was read as the last one"


def test_the_page_count_survives_local_filtering(monkeypatch):
    """It describes Apollo's paging, which our own checks do not change."""
    other = {"id": "o2", "name": "Other", "domain": "other.com"}
    meta = {}
    _apollo(monkeypatch, {"people": [_person("a"), _person("b", other)],
                          "pagination": {"total_entries": 355, "total_pages": 15}})
    ac.search_people({"company_domains": [_DOMAIN]}, "k", meta=meta)
    assert meta["total_pages"] == 15
    assert meta["total_entries"] is None, "the row count described the looser match"


def test_an_accurate_total_is_not_thrown_away(monkeypatch):
    """Measured live: a domain-scoped search returns Apollo's own total for that
    domain, and it is exact. Discarding a correct count is its own small lie,
    and it left every company-scoped search with no count at all."""
    meta = {}
    _apollo(monkeypatch, {"people": [_person("p%d" % i) for i in range(25)],
                          "pagination": {"total_entries": 355, "total_pages": 15}})
    ac.search_people({"company_domains": [_DOMAIN]}, "k", meta=meta)
    assert meta["total_entries"] == 355
    assert meta["company_dropped"] == 0


def test_a_total_that_does_not_describe_its_own_page_is_still_refused(monkeypatch):
    """One row returned out of a page of 25, while claiming 83 million entries,
    is Apollo describing a different query than these rows -- which is what an
    ignored filter looks like."""
    meta = {}
    _apollo(monkeypatch, {"people": [_person("p1")],
                          "pagination": {"total_entries": 83000000,
                                         "total_pages": 900000}})
    ac.search_people({"company_domains": [_DOMAIN]}, "k", meta=meta, per_page=25)
    assert meta["total_entries"] is None
    assert meta["total_pages"] == 900000


def test_a_genuinely_short_last_page_keeps_its_total(monkeypatch):
    """The mirror of the above: the final page is short for an honest reason."""
    meta = {}
    _apollo(monkeypatch, {"people": [_person("p%d" % i) for i in range(19)],
                          "pagination": {"total_entries": 355, "total_pages": 15}})
    ac.search_people({"company_domains": [_DOMAIN]}, "k", meta=meta,
                     page=15, per_page=24)
    assert meta["total_entries"] == 355


# ── The way people actually type a location ────────────────────────────────

@pytest.mark.parametrize("typed", ["Austin, TX", "austin, tx", "Austin, Texas",
                                   "Texas", "TX", "United States", "US"])
def test_the_normal_ways_to_type_one_place_all_reach_it(typed):
    org = {"city": "Austin", "state": "Texas", "country": "United States"}
    assert appmod._cpi_place_matches(org, [typed]) is True, (
        '"%s" removed a company that is in exactly that place' % typed)


@pytest.mark.parametrize("typed", ["Dallas, TX", "Chicago, IL", "New York, NY",
                                   "California", "Canada"])
def test_a_place_this_company_is_not_in_still_misses(typed):
    org = {"city": "Austin", "state": "Texas", "country": "United States"}
    assert appmod._cpi_place_matches(org, [typed]) is False


def test_an_abbreviation_does_not_ride_along_inside_a_longer_word():
    """"CA" used to match Chicago, and that kind of accident is what made the
    substring version look like it worked."""
    org = {"city": "Chicago", "state": "Illinois", "country": "United States"}
    assert appmod._cpi_place_matches(org, ["CA"]) is False


def test_a_country_code_that_doubles_as_a_state_code_reaches_both():
    """"CA" is California and Canada, and both readings are legitimate. Every
    comma-separated part still has to match, so the loose part cannot carry a
    row on its own."""
    toronto = {"city": "Toronto", "state": "Ontario", "country": "Canada"}
    assert appmod._cpi_place_matches(toronto, ["Toronto, CA"]) is True
    ohio = {"city": "Toronto", "state": "Ohio", "country": "United States"}
    assert appmod._cpi_place_matches(ohio, ["Toronto, CA"]) is False


# ── A lookup that failed is not a company Apollo has never heard of ────────

def test_a_failed_company_lookup_is_not_a_missing_company(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("apollo down")

    monkeypatch.setattr(ac, "enrich_company_by_id", _boom)
    monkeypatch.setattr(ac, "enrich_company", _boom)
    out = appmod._cpi_enrich_company("betabionics.com", "org1")
    assert out["matched"] is False
    assert out["lookup_failed"] is True


def test_a_company_apollo_really_has_no_record_of_says_only_that(monkeypatch):
    monkeypatch.setattr(ac, "enrich_company_by_id", lambda *a, **k: {})
    monkeypatch.setattr(ac, "enrich_company", lambda *a, **k: {})
    out = appmod._cpi_enrich_company("nosuchcompany.example", "")
    assert out["matched"] is False
    assert not out.get("lookup_failed")


def test_a_failed_person_lookup_is_not_a_missing_person(monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("apollo down")

    monkeypatch.setattr(ac, "_post", _boom)
    out = appmod._cpi_enrich_person("Steven Russell", _DOMAIN, "person-id")
    assert out["matched"] is False
    assert out["lookup_failed"] is True


@pytest.mark.parametrize("fn", ["personBody", "companyBody"])
def test_each_modal_tells_the_two_apart(fn):
    """Checked per function rather than per file: one of the two carrying the
    distinction is what "half fixed" looks like, and the file-wide version of
    this assertion passed while the company modal still said Apollo had no
    record of a company it had simply failed to ask about."""
    js = _js()
    start = js.index("function %s(" % fn)
    body = js[start:js.index("\n}", start)]
    assert "lookup_failed" in body, "%s does not branch on a failed lookup" % fn
    assert "neither found nor ruled out" in body, (
        "%s does not say the lookup failed" % fn)


# ── The free count must not overstate what the page will show ──────────────

def test_a_company_scoped_count_is_offered_but_marked_approximate(client, monkeypatch):
    """Two halves of one change. Apollo's total for a domain-scoped search is
    usually exact, so refusing to show it left every company search with no
    count at all. But this app re-checks the domain itself, so the number is an
    upper bound and has to read as one -- "about 355", not "355"."""
    _apollo(monkeypatch, {"people": [_person("p1")],
                          "pagination": {"total_entries": 355, "total_pages": 15}})
    body = client.post("/p2/b2b-agents/company-people-intelligence/count",
                       json={"entity": "people",
                             "filters": {"company_domains": [_DOMAIN]}}).get_json()
    assert body["count"] == 355
    assert body["approx"] is True, "an upper bound was presented as a final count"


def test_every_locally_rechecked_filter_is_marked_approximate():
    """The list and the checks have to stay in step: a filter that drops rows
    but is missing here turns an upper bound into a claim."""
    checked = {"industries", "employee_min", "employee_max", "revenue_min",
               "revenue_max", "company_locations", "technologies", "titles",
               "company_domains"}
    missing = checked - set(appmod._CPI_COUNT_VERIFIED_FILTERS)
    assert not missing, "these drop rows but do not mark the count approximate: %s" % missing


# ── A chunk that never got an answer is not fifty people Apollo lacks ──────
#
# The bulk reveal is the biggest purchase this page can make and it runs in
# chunks of ten, any one of which can fail on its own. A missing id meant two
# opposite things at once: Apollo has no record of this person, or these ten
# never got asked. The toast said "Revealed 40 profiles" either way, so the ten
# worth retrying for free read as ten dead ends.

_BULK_URL = "/p2/b2b-agents/company-people-intelligence/enrich-bulk"


def _chunk_failer(fail_on_second=True):
    """A _post that answers the first chunk of ten and dies on the next."""
    state = {"n": 0}

    def _post(endpoint, body, api_key, retries=3):
        state["n"] += 1
        if fail_on_second and state["n"] > 1:
            raise RuntimeError("apollo timed out")
        details = body.get("details") or []
        return {"matches": [{"id": d["id"], "first_name": "A", "last_name": "B",
                             "email": "a@b.com"} for d in details]}

    return _post


def test_the_ids_from_a_failed_chunk_come_back_named(monkeypatch):
    monkeypatch.setattr(ac, "_post", _chunk_failer())
    ids = ["p%02d" % i for i in range(25)]
    failed = []
    out = ac.bulk_match_people(ids, "k", failed=failed)
    assert len(out) == 10, "the chunk that answered should still be kept"
    assert failed == ids[10:], "the unanswered ids were not reported"


def test_an_unreadable_answer_counts_as_unanswered(monkeypatch):
    """A 200 whose body we cannot parse taught us nothing about those ten."""
    monkeypatch.setattr(ac, "_post", lambda *a, **k: {"unexpected": "shape"})
    failed = []
    assert ac.bulk_match_people(["p1", "p2"], "k", failed=failed) == {}
    assert failed == ["p1", "p2"]


def test_a_clean_run_reports_nothing_unreachable(monkeypatch):
    monkeypatch.setattr(ac, "_post", _chunk_failer(fail_on_second=False))
    failed = []
    out = ac.bulk_match_people(["p1", "p2"], "k", failed=failed)
    assert len(out) == 2 and failed == []


def test_a_person_apollo_simply_has_no_record_of_is_not_called_unreachable(monkeypatch):
    """The whole point of the distinction: a genuine miss must stay a miss."""
    monkeypatch.setattr(ac, "_post", lambda *a, **k: {"matches": [None, None]})
    failed = []
    assert ac.bulk_match_people(["p1", "p2"], "k", failed=failed) == {}
    assert failed == [], "a miss was reported as a failure"


def test_the_reveal_route_says_how_many_it_could_not_reach(client, monkeypatch):
    monkeypatch.setattr(appmod, "_cpi_id_cache_read", lambda ids: {})
    monkeypatch.setattr(appmod, "_cpi_id_cache_write", lambda p: None)
    monkeypatch.setattr(ac, "_post", _chunk_failer())
    body = client.post(_BULK_URL,
                       json={"ids": ["p%02d" % i for i in range(25)]}).get_json()
    assert body["fetched"] == 10
    assert body["unreachable"] == 15, (
        "15 people were neither revealed nor ruled out and the reply did not say so")


def test_a_total_failure_is_not_reported_as_nobody_being_found(client, monkeypatch):
    monkeypatch.setattr(appmod, "_cpi_id_cache_read", lambda ids: {})

    def _boom(*a, **k):
        raise RuntimeError("apollo down")

    monkeypatch.setattr(ac, "bulk_match_people", _boom)
    body = client.post(_BULK_URL, json={"ids": ["p1", "p2"]}).get_json()
    assert body["unreachable"] == 2
    assert "no credits were spent" in body["error"]


def test_a_failure_alongside_cached_rows_still_counts_the_unreached(client, monkeypatch):
    """The path that does NOT return early. With some people already in cache
    the reveal still succeeds overall, so the ones Apollo never answered for
    are the only thing standing between the reader and a wrong conclusion."""
    monkeypatch.setattr(appmod, "_cpi_id_cache_read",
                        lambda ids: {"p1": {"id": "p1", "first_name": "Cached",
                                            "last_name": "Person"}})
    monkeypatch.setattr(appmod, "_cpi_id_cache_write", lambda p: None)

    def _boom(*a, **k):
        raise RuntimeError("apollo down")

    monkeypatch.setattr(ac, "bulk_match_people", _boom)
    body = client.post(_BULK_URL, json={"ids": ["p1", "p2", "p3"]}).get_json()
    assert "error" not in body, "the cached rows were a success and should be served"
    assert body["cached"] == 1
    assert body["unreachable"] == 2, (
        "two people were neither revealed nor ruled out and the reply did not say so")


def test_the_toast_does_not_call_a_partial_reveal_a_success():
    js = _js()
    start = js.index("window.cpiEnrichSelected")
    body = js[start:js.index("\n};", start)]
    assert "if(d.unreachable){" in body, (
        "the reveal toast does not actually branch on unreachable people")
    assert "neither revealed nor ruled out" in body
    # Reported inside the one toast: a second call replaces the first before it
    # has been read, which is why `capped` and this cannot both be extra toasts.
    assert body.count('"err"') >= 2
