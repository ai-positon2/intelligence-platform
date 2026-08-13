"""The four surfaces added to make Contact Finder a workbench rather than a
lookup: the free match count, the credit ledger, the working list, and the
sentence-to-filters parse.

The count is the one with teeth. It runs on a debounce while somebody types, so
anything it does per keystroke it does hundreds of times a day, and three things
on the search path cost Apollo credits: the Companies endpoint (per call), the
employer lookup (per page), and resolving a typed company NAME to an id. A count
that reached any of them would quietly drain the shared pool. The tests below
pin all three refusals, and they assert on the Apollo calls actually made rather
than on the reply, because a reply saying "0 credits" proves nothing about what
was called.

The ledger is deliberately not called a balance. Apollo's usage endpoint reports
per-endpoint rate limits and needs a master key this app does not hold, so the
account total cannot be read from here; what the app can prove is its own
spending, recorded at the five places a spend is reported to the user so the
header and the screen can never disagree.
"""

import json
import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import tracker.apollo_client as ac  # noqa: E402

_COUNT = "/p2/b2b-agents/company-people-intelligence/count"
_CREDITS = "/p2/b2b-agents/company-people-intelligence/credits"
_LIST = "/p2/b2b-agents/company-people-intelligence/list"
_PARSE = "/p2/b2b-agents/company-people-intelligence/parse-query"


@pytest.fixture
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    return c


@pytest.fixture
def apollo(monkeypatch):
    """Records every Apollo call. `people` is free; everything else in `billed`
    costs credits and must never be reached by a count."""
    seen = {"people": [], "billed": []}
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")

    def _people(filters, api_key, page=1, per_page=25, strict=False, meta=None):
        seen["people"].append({"filters": dict(filters), "per_page": per_page})
        if meta is not None:
            meta["total_entries"] = 2400
            meta["total_pages"] = 100
        return []

    monkeypatch.setattr(ac, "search_people", _people)
    for name in ("search_companies", "bulk_match_people"):
        monkeypatch.setattr(ac, name,
                            lambda *a, **k: seen["billed"].append(name) or [])
    monkeypatch.setattr(appmod, "_cpi_attach_employer_facts",
                        lambda *a, **k: seen["billed"].append("employer") or None)
    monkeypatch.setattr(appmod, "_cpi_resolve_company_name",
                        lambda *a, **k: seen["billed"].append("resolve") or
                        ("o1", "Acme", None, True))
    return seen


# ── The count must not spend ─────────────────────────────────────────────────

def test_the_companies_tab_gets_no_count_at_all(client, apollo):
    """mixed_companies/search bills per call, so there is no free way to count
    companies. Refused with a reason rather than counted."""
    r = client.post(_COUNT, json={"entity": "companies", "filters": {}})
    body = r.get_json()
    assert body["count"] is None
    assert "credit" in body["reason"].lower()
    assert apollo["billed"] == []
    assert apollo["people"] == [], "a refused count must not search either"


def test_counting_never_runs_the_employer_lookup(client, apollo):
    """_cpi_attach_employer_facts is 1 credit per page. company_detail is
    dropped rather than forwarded, so it cannot be reached even when asked for."""
    client.post(_COUNT, json={"entity": "people",
                              "filters": {"titles": ["CMO"], "company_detail": True}})
    assert apollo["billed"] == []
    assert "company_detail" not in apollo["people"][0]["filters"]


def test_a_typed_company_name_is_refused_rather_than_resolved(client, apollo):
    """Resolving a name to an Apollo id costs a credit. A count must not."""
    r = client.post(_COUNT, json={"entity": "people",
                                  "filters": {"company_domains": ["Acme Inc"]}})
    assert r.get_json()["count"] is None
    assert apollo["billed"] == []


def test_a_typed_company_domain_needs_no_resolution_and_counts(client, apollo):
    """The other half: a real domain is exact already, so it counts for free."""
    r = client.post(_COUNT, json={"entity": "people",
                                  "filters": {"company_domains": ["acme.com"]}})
    assert r.get_json()["count"] == 2400
    assert apollo["billed"] == []


def test_the_count_asks_for_one_row_not_a_page(client, apollo):
    """Only Apollo's own total is wanted; the rows are thrown away."""
    client.post(_COUNT, json={"entity": "people", "filters": {"titles": ["CMO"]}})
    assert apollo["people"][0]["per_page"] == 1


# ── ...and must not overstate ────────────────────────────────────────────────

def test_a_verified_filter_makes_the_count_approximate(client, apollo):
    """Apollo's total counts what IT matched; _cpi_verify_rows then drops rows
    that do not really qualify. With such a filter set the total is an upper
    bound, and the reply has to say so or the page will promise 2,400 and show
    300."""
    r = client.post(_COUNT, json={"entity": "people",
                                  "filters": {"industries": ["computer software"]}})
    body = r.get_json()
    assert body["count"] == 2400
    assert body["approx"] is True


def test_an_unverified_filter_set_is_reported_exactly(client, apollo):
    r = client.post(_COUNT, json={"entity": "people",
                                  "filters": {"person_locations": ["Texas"]}})
    assert r.get_json()["approx"] is False


def test_no_total_from_apollo_means_no_number(client, apollo, monkeypatch):
    """search_people blanks its totals when it enforces a domain itself, because
    Apollo's pagination describes the unfiltered call. No honest number to give."""
    monkeypatch.setattr(ac, "search_people",
                        lambda f, k, page=1, per_page=25, strict=False, meta=None: [])
    r = client.post(_COUNT, json={"entity": "people", "filters": {"titles": ["CMO"]}})
    assert r.get_json()["count"] is None


def test_an_apollo_failure_is_not_a_zero(client, apollo, monkeypatch):
    """0 matches and "could not ask" are different facts."""
    def _boom(*a, **k):
        raise RuntimeError("apollo 502")
    monkeypatch.setattr(ac, "search_people", _boom)
    body = client.post(_COUNT, json={"entity": "people",
                                     "filters": {"titles": ["CMO"]}}).get_json()
    assert body["count"] is None
    assert body["reason"]


def test_a_malformed_code_is_dropped_the_same_way_the_search_drops_it(client, apollo):
    """Otherwise the count describes a filter set the search would not run."""
    client.post(_COUNT, json={"entity": "people",
                              "filters": {"naics_codes": ["541511"]}})
    assert "naics_codes" not in apollo["people"][0]["filters"]


# ── The ledger ───────────────────────────────────────────────────────────────

class _Cur:
    def __init__(self, log, rows):
        self.log, self.rows = log, rows
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else [0, 0]

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, rows=None):
        self.log, self.rows, self.committed = [], list(rows or []), False

    def cursor(self):
        return _Cur(self.log, self.rows)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def pg(monkeypatch):
    def _make(rows=None):
        conn = _Conn(rows)
        monkeypatch.setattr(appmod, "_pg_conn", lambda: conn)
        monkeypatch.setattr(appmod, "_CPI_LEDGER_TABLE_READY", True, raising=False)
        monkeypatch.setattr(appmod, "_CPI_LIST_TABLE_READY", True, raising=False)
        return conn
    return _make


def _inserts(conn):
    return [(s, p) for s, p in conn.log if s.startswith("INSERT INTO cpi_credit_ledger")]


def _record(action, credits):
    """Called the way it really is: inside a request, with a signed-in user, so
    the email it stamps on the row is the one the session holds."""
    with appmod.app.test_request_context("/"):
        from flask import session
        session["google_user"] = {"email": "reporting@position2.com"}
        appmod._cpi_credit_record(action, credits)


def test_a_spend_is_recorded(pg):
    conn = pg()
    _record("enrich", 3)
    got = _inserts(conn)
    assert len(got) == 1
    assert got[0][1] == ("reporting@position2.com", "enrich", 3)


def test_a_zero_is_not_recorded(pg):
    """A cache hit is not a purchase. Rows of zeroes would make the ledger read
    as activity rather than as spend."""
    conn = pg()
    _record("enrich", 0)
    assert not _inserts(conn)


def test_a_junk_value_is_not_recorded(pg):
    conn = pg()
    _record("enrich", None)
    _record("enrich", "lots")
    assert not _inserts(conn)


def test_a_missing_request_context_is_survived_not_raised(pg):
    """The guard that matters: this runs after the credit is spent and just
    before the reply that reports it, so nothing it touches may throw. Reading
    the session outside a request is the cheapest way to prove the whole body,
    not only the SQL, is inside the guard."""
    pg()
    appmod._cpi_credit_record("enrich", 2)      # no request context: must not raise


def test_the_ledger_never_raises(monkeypatch):
    """It runs on the paid path, after the money is spent. A logging failure must
    not turn a successful purchase into a 500."""
    class _Boom:
        def cursor(self):
            raise RuntimeError("pg down")

        def rollback(self):
            pass

        def close(self):
            pass
    monkeypatch.setattr(appmod, "_pg_conn", lambda: _Boom())
    monkeypatch.setattr(appmod, "_CPI_LEDGER_TABLE_READY", True, raising=False)
    appmod._cpi_credit_record("enrich", 2)      # must not raise


def _app_src():
    return open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "app.py"), encoding="utf-8").read()


def test_every_paid_reply_records_what_it_reported():
    """The header and the screen must come from the same event. Each of the five
    places that attaches `credits` to a reply also records it."""
    src = _app_src()
    for action in ("company-resolve", "search-", "enrich", "enrich-bulk", "chat"):
        assert '_cpi_credit_record("%s' % action in src, action


def test_the_ledger_comment_counts_its_own_call_sites():
    """The comment above the ledger names how many places write to it, and that
    number was wrong on the first pass: it said four when there were five. A
    comment that miscounts the thing it documents is the same defect class this
    page keeps finding in its own UI, so the two are pinned together. Adding a
    sixth spend path is fine; leaving the sentence saying five is not."""
    src = _app_src()
    sites = src.count("_cpi_credit_record(") - src.count("def _cpi_credit_record(")
    words = {4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight"}
    assert sites in words, "unexpected number of ledger call sites: %d" % sites
    assert ("Written at the %s places" % words[sites]) in src, (
        "the ledger comment does not say %r; there are %d call sites"
        % (words[sites], sites))


def test_the_summary_is_not_called_a_balance():
    """The wording is the honesty: no endpoint here reports the pool's total, and
    the same key funds de-anon and External Usage too."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "app.py"), encoding="utf-8").read()
    fn = src[src.index("def cpi_credits("):src.index("def cpi_credits(") + 1400]
    assert "Deliberately not called a balance" in fn


def test_no_postgres_means_the_header_says_nothing(client, monkeypatch):
    monkeypatch.setattr(appmod, "_pg_conn", lambda: None)
    assert client.get(_CREDITS).get_json() == {"available": False}


# ── The working list ─────────────────────────────────────────────────────────

def test_a_row_is_keyed_by_its_apollo_id():
    assert appmod._cpi_list_key({"id": "p1", "full_name": "A"}, "people") == "p1"


def test_a_row_without_an_id_falls_back_to_name_and_employer():
    """The same fallback the enrich history uses, so one person cannot end up
    under two keys and be added twice."""
    k = appmod._cpi_list_key({"full_name": "Binal Shah",
                              "organization_name": "Tealium"}, "people")
    assert k == "binal shah|tealium"


def test_a_company_row_is_keyed_by_name_and_domain():
    assert appmod._cpi_list_key({"name": "Tealium",
                                 "primary_domain": "tealium.com"}, "companies") \
        == "tealium|tealium.com"


def test_an_empty_row_still_gets_a_key():
    assert appmod._cpi_list_key({}, "people") == "?"


def test_adding_a_row_already_on_the_list_does_not_overwrite_it(pg, client):
    """ON CONFLICT DO NOTHING, not DO UPDATE: a row on the list may have been
    enriched since it was added, and replacing it with the un-enriched search row
    would throw away something that cost a credit."""
    conn = pg([[0]])
    client.post(_LIST, json={"entity": "people", "rows": [{"id": "p1"}]})
    ins = [s for s, _ in conn.log if s.startswith("INSERT INTO cpi_list_rows")]
    assert ins and "ON CONFLICT DO NOTHING" in ins[0]
    assert "DO UPDATE" not in ins[0]


def test_the_list_is_scoped_to_the_signed_in_user(pg, client):
    conn = pg([[0]])
    client.get(_LIST)
    sel = [(s, p) for s, p in conn.log if s.startswith("SELECT entity")]
    assert sel and sel[0][1][0] == "reporting@position2.com"


def test_deleting_by_key_is_scoped_to_the_user_too(pg, client):
    """email in the WHERE clause is the authorization check: a guessed key
    belonging to someone else removes nothing."""
    conn = pg()
    client.delete(_LIST, json={"keys": ["p1"]})
    dels = [(s, p) for s, p in conn.log
            if s.startswith("DELETE FROM cpi_list_rows WHERE email = %s AND dedupe_key")]
    assert dels and dels[0][1][0] == "reporting@position2.com"


def test_the_list_expires_like_the_history_does(pg, client):
    """These rows can hold revealed contact details, so ninety days has to mean
    ninety days even for someone who stopped using the tool."""
    conn = pg([[0]])
    client.get(_LIST)
    exp = [(s, p) for s, p in conn.log if "make_interval" in s]
    assert exp and exp[0][1] == (appmod._CPI_LIST_TTL_DAYS,)


def test_no_postgres_means_the_list_degrades_quietly(client, monkeypatch):
    monkeypatch.setattr(appmod, "_pg_conn", lambda: None)
    assert client.get(_LIST).get_json()["available"] is False


# ── Sentence to filters ──────────────────────────────────────────────────────

def _intent(monkeypatch, payload):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setattr(appmod, "_cpi_oai", lambda: object())
    monkeypatch.setattr(appmod, "_vimi_chat_json",
                        lambda oai, msgs, mt: (json.dumps(payload), "m"))


def test_a_sentence_becomes_filters(client, monkeypatch):
    _intent(monkeypatch, {"intent": "people_list", "titles": ["VP Marketing"],
                          "industries": ["hospital & health care"],
                          "person_locations": ["Texas"]})
    body = client.post(_PARSE, json={"q": "VPs of marketing at healthcare cos in Texas"}).get_json()
    assert body["filters"]["titles"] == ["VP Marketing"]
    assert body["filters"]["person_locations"] == ["Texas"]
    assert body["entity"] == "people"


def test_chat_shaped_keys_never_become_filters(client, monkeypatch):
    """The intent parser answers a chat question and carries fields the panel has
    no control for. Setting a filter the user cannot see is the one thing this
    must not do."""
    _intent(monkeypatch, {"intent": "person_at_company", "titles": ["CMO"],
                          "person_name": "Heidi Bullock", "max_results": 25,
                          "selected_org_id": "o1"})
    f = client.post(_PARSE, json={"q": "who is the CMO"}).get_json()["filters"]
    assert f == {"titles": ["CMO"]}


def test_a_company_name_lands_in_the_company_field(client, monkeypatch):
    _intent(monkeypatch, {"intent": "person_at_company", "company_name": "Tealium"})
    f = client.post(_PARSE, json={"q": "cmo of tealium"}).get_json()["filters"]
    assert f["company_domains"] == ["Tealium"]


def test_a_size_range_becomes_the_numeric_filters(client, monkeypatch):
    _intent(monkeypatch, {"intent": "people_list",
                          "employees": {"min": 50, "max": 200}})
    f = client.post(_PARSE, json={"q": "50 to 200 people"}).get_json()["filters"]
    assert f["employee_min"] == 50 and f["employee_max"] == 200


def test_parsing_spends_no_apollo_credits(client, monkeypatch, apollo):
    _intent(monkeypatch, {"intent": "people_list", "titles": ["CMO"]})
    client.post(_PARSE, json={"q": "cmos"})
    assert apollo["billed"] == []
    assert apollo["people"] == []


def test_an_empty_question_asks_nothing(client, monkeypatch):
    called = []
    monkeypatch.setattr(appmod, "_cpi_oai", lambda: called.append(1) or object())
    assert client.post(_PARSE, json={"q": "   "}).get_json()["filters"] == {}
    assert called == []


def test_a_model_failure_is_reported_not_guessed(client, monkeypatch):
    monkeypatch.setattr(appmod, "_cpi_oai", lambda: object())

    def _boom(*a, **k):
        raise RuntimeError("openai down")
    monkeypatch.setattr(appmod, "_vimi_chat_json", _boom)
    body = client.post(_PARSE, json={"q": "cmos in texas"}).get_json()
    assert body["filters"] == {}
    assert body["error"]
