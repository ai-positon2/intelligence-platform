"""Audit of the history flow: what the drawer records, keeps, and admits to.

History is the only part of this tool whose data outlives the request that made
it, which is what makes its failures different in kind from the other flows':

  - A purchase that is not recorded is money the user cannot get back to. The
    single-person reveal was saved and the fifty-person bulk reveal was not, so
    closing a tab lost exactly the contacts that had cost the most.
  - An entry that does not say what it cost is a receipt with no total. The
    drawer's credit badge read a key only the chat wrote, so the entries that
    definitely spent credits were the ones showing none.
  - Duplicates are not free: the list is capped per user, so four identical
    entries for one person evict four real ones.
  - A retention rule that only runs when the same person comes back is not a
    retention rule. These rows hold revealed emails and phone numbers.
  - A label that says "All people" for a search filtered by NAICS code, revenue
    band and technology makes the drawer unreadable and, worse, makes different
    searches look like the same one.
  - Reopening an entry must not carry the previous search's caveats onto these
    rows, and "Cleared history" must not be said when nothing was cleared.
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

_BULK = "/p2/b2b-agents/company-people-intelligence/enrich-bulk"
_ENRICH = "/p2/b2b-agents/company-people-intelligence/enrich"
_HISTORY = "/p2/b2b-agents/company-people-intelligence/history"


def _js():
    return open(_JS, encoding="utf-8").read()


@pytest.fixture
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    return c


@pytest.fixture
def saved(monkeypatch):
    """Capture history writes. The signature mirrors the real one, so a caller
    that drifts from it fails here rather than being swallowed by the save's own
    except clause."""
    captured = []

    def _save(email, entity, label, rows, answer="", filters=None, total=None,
              dedupe=""):
        captured.append({"email": email, "entity": entity, "label": label,
                         "rows": rows, "answer": answer, "total": total,
                         "filters": filters or {}, "dedupe": dedupe})
        return len(captured)

    monkeypatch.setattr(appmod, "_cpi_history_save", _save)
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")
    monkeypatch.setattr(appmod, "_pg_conn", lambda: None)
    return captured


def _person(pid, name="Binal Shah"):
    first, _, last = name.partition(" ")
    return {"id": pid, "first_name": first, "last_name": last, "name": name,
            "email": "%s@tealium.com" % first.lower(), "title": "CMO",
            "organization": {"name": "Tealium", "primary_domain": "tealium.com"}}


def _bulk_stub(monkeypatch, fetched=None, cached=None):
    monkeypatch.setattr(appmod, "_cpi_id_cache_read", lambda ids: dict(cached or {}))
    monkeypatch.setattr(appmod, "_cpi_id_cache_write", lambda p: None)
    monkeypatch.setattr(ac, "bulk_match_people",
                        lambda ids, key: dict(fetched or {}))


# ── The purchase the drawer used to lose ─────────────────────────────────────

def test_a_bulk_reveal_is_recorded(client, saved, monkeypatch):
    """The biggest spend on the page went unrecorded while a one-credit reveal was
    always saved."""
    _bulk_stub(monkeypatch, fetched={"p1": _person("p1"), "p2": _person("p2", "Ann Lee")})
    r = client.post(_BULK, json={"ids": ["p1", "p2"]})
    assert r.status_code == 200
    assert len(saved) == 1
    assert saved[0]["entity"] == "revealed"


def test_the_reveal_entry_says_who_was_revealed(client, saved, monkeypatch):
    _bulk_stub(monkeypatch, fetched={"p1": _person("p1"), "p2": _person("p2", "Ann Lee")})
    client.post(_BULK, json={"ids": ["p1", "p2"]})
    label = saved[0]["label"]
    assert label.startswith("2 contacts revealed")
    assert "Binal Shah" in label and "Ann Lee" in label


def test_one_revealed_contact_is_not_called_contacts(client, saved, monkeypatch):
    _bulk_stub(monkeypatch, fetched={"p1": _person("p1")})
    client.post(_BULK, json={"ids": ["p1"]})
    assert saved[0]["label"].startswith("1 contact revealed")


def test_a_long_reveal_names_the_first_few_and_counts_the_rest(client, saved,
                                                               monkeypatch):
    people = {"p%d" % i: _person("p%d" % i, "Person %d" % i) for i in range(6)}
    _bulk_stub(monkeypatch, fetched=people)
    client.post(_BULK, json={"ids": list(people)})
    assert "+3 more" in saved[0]["label"]


def test_the_reveal_records_only_what_apollo_charged_for(client, saved, monkeypatch):
    """Cached ids were paid for on an earlier click. Counting them again would
    inflate the total the drawer reports."""
    _bulk_stub(monkeypatch, fetched={"p1": _person("p1")},
               cached={"p2": _person("p2", "Ann Lee")})
    client.post(_BULK, json={"ids": ["p1", "p2"]})
    assert saved[0]["filters"]["credits"] == 1
    assert saved[0]["filters"]["from_cache"] == 1


def test_a_reveal_served_entirely_from_cache_costs_nothing(client, saved, monkeypatch):
    _bulk_stub(monkeypatch, cached={"p1": _person("p1")})
    client.post(_BULK, json={"ids": ["p1"]})
    assert saved[0]["filters"]["credits"] == 0


def test_the_revealed_rows_are_saved_in_the_shape_the_grid_uses(client, saved,
                                                               monkeypatch):
    """This is what lets a reveal entry reopen into the grid and export like a
    saved search, rather than needing a fourth kind of reopen."""
    _bulk_stub(monkeypatch, fetched={"p1": _person("p1")})
    client.post(_BULK, json={"ids": ["p1"]})
    row = saved[0]["rows"][0]
    assert row["full_name"] == "Binal Shah"
    assert row["title"] == "CMO"
    assert row["email"] == "binal@tealium.com"


def test_the_reveal_entry_counts_its_own_rows(client, saved, monkeypatch):
    _bulk_stub(monkeypatch, fetched={"p1": _person("p1"), "p2": _person("p2", "Ann Lee")})
    client.post(_BULK, json={"ids": ["p1", "p2"]})
    assert saved[0]["total"] == 2


def test_a_reveal_that_matched_nobody_saves_nothing(client, saved, monkeypatch):
    """Nothing was bought and nothing is worth keeping."""
    _bulk_stub(monkeypatch)
    r = client.post(_BULK, json={"ids": ["p1"]})
    assert r.get_json()["fetched"] == 0
    assert saved == []


def test_an_empty_selection_saves_nothing(client, saved, monkeypatch):
    _bulk_stub(monkeypatch)
    client.post(_BULK, json={"ids": []})
    assert saved == []


def test_the_reveal_reply_says_nothing_about_having_been_saved(client, saved,
                                                              monkeypatch):
    """Saving is bookkeeping, not part of the answer, so the response shape is
    unchanged by it."""
    _bulk_stub(monkeypatch, fetched={"p1": _person("p1")})
    body = client.post(_BULK, json={"ids": ["p1"]}).get_json()
    assert set(body) == {"profiles", "fetched", "cached", "capped"}


def test_a_history_failure_does_not_lose_the_reveal(client, monkeypatch):
    """The credits are already spent by this point: an unwritable history must
    never turn a successful reveal into an error."""
    _bulk_stub(monkeypatch, fetched={"p1": _person("p1")})

    def _boom(**kw):
        raise RuntimeError("no database")

    monkeypatch.setattr(appmod, "_cpi_history_save", _boom)
    monkeypatch.setattr(appmod, "_pg_conn", lambda: None)
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")
    r = client.post(_BULK, json={"ids": ["p1"]})
    assert r.status_code == 200
    assert r.get_json()["profiles"]["p1"]["full_name"] == "Binal Shah"


# ── What an entry cost, and not saying it twice ──────────────────────────────

def _enrich_stub(monkeypatch, person=None, credits=1):
    def _fake(name, domain, apollo_id, email, spend=None):
        if spend is not None:
            spend["credits"] += credits
        return dict(person or {"matched": True, "id": "p1", "name": "Binal Shah",
                               "title": "CMO", "email": "binal@tealium.com",
                               "company": {"name": "Tealium",
                                           "domain": "tealium.com"}})

    monkeypatch.setattr(appmod, "_cpi_enrich_person", _fake)


def test_an_enriched_contact_records_what_it_cost(client, saved, monkeypatch):
    """The drawer shows a credit count per entry and was reading a key only the
    chat wrote, so the one entry that definitely spent a credit showed none."""
    _enrich_stub(monkeypatch)
    client.post(_ENRICH, json={"type": "person", "name": "Binal Shah",
                               "domain": "tealium.com"})
    assert saved[0]["filters"]["credits"] == 1


def test_a_cached_enrichment_records_no_cost(client, saved, monkeypatch):
    """0 is a real answer: it is the difference between a credit counter that can
    be trusted and one that always says 1."""
    _enrich_stub(monkeypatch, credits=0)
    client.post(_ENRICH, json={"type": "person", "apollo_id": "p1"})
    assert saved[0]["filters"]["credits"] == 0


def test_the_same_person_is_keyed_by_their_apollo_id(client, saved, monkeypatch):
    """Whether the id came back on the profile or was the thing asked for: either
    one names the person, and dropping either source silently un-keys the entry."""
    _enrich_stub(monkeypatch)
    client.post(_ENRICH, json={"type": "person", "apollo_id": "asked-for"})
    assert saved[0]["dedupe"] == "p1"
    _enrich_stub(monkeypatch, person={"matched": True, "name": "Binal Shah",
                                      "company": {}})
    client.post(_ENRICH, json={"type": "person", "apollo_id": "asked-for"})
    assert saved[1]["dedupe"] == "asked-for"


def test_without_an_apollo_id_the_name_and_employer_are_the_key(client, saved,
                                                               monkeypatch):
    """Which is what the match itself was made on."""
    _enrich_stub(monkeypatch, person={"matched": True, "name": "Binal Shah",
                                      "company": {}})
    client.post(_ENRICH, json={"type": "person", "name": "Binal Shah",
                               "domain": "Tealium.com"})
    assert saved[0]["dedupe"] == "binal shah|tealium.com"


def test_two_people_at_one_employer_are_not_the_same_entry(client, saved,
                                                           monkeypatch):
    _enrich_stub(monkeypatch, person={"matched": True, "name": "A", "company": {}})
    client.post(_ENRICH, json={"type": "person", "name": "Ann Lee",
                               "domain": "tealium.com"})
    _enrich_stub(monkeypatch, person={"matched": True, "name": "B", "company": {}})
    client.post(_ENRICH, json={"type": "person", "name": "Bo Ray",
                               "domain": "tealium.com"})
    assert saved[0]["dedupe"] != saved[1]["dedupe"]


def test_a_nameless_unmatched_key_falls_back_to_a_plain_insert(client, saved,
                                                              monkeypatch):
    """An empty key must not collide with the next record that also has nothing
    to key on."""
    _enrich_stub(monkeypatch, person={"matched": True, "company": {}})
    client.post(_ENRICH, json={"type": "person"})
    assert saved[0]["dedupe"] == ""


def test_a_history_failure_does_not_lose_an_enriched_contact(client, monkeypatch):
    """Same rule as the reveal: the credit is spent by the time this runs, so the
    user must get their contact and not an error about bookkeeping."""
    _enrich_stub(monkeypatch)

    def _boom(**kw):
        raise RuntimeError("no database")

    monkeypatch.setattr(appmod, "_cpi_history_save", _boom)
    monkeypatch.setattr(appmod, "_pg_conn", lambda: None)
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")
    r = client.post(_ENRICH, json={"type": "person", "apollo_id": "p1"})
    assert r.status_code == 200
    assert r.get_json()["profile"]["email"] == "binal@tealium.com"


def test_a_company_profile_is_keyed_too(client, saved, monkeypatch):
    monkeypatch.setattr(appmod, "_cpi_enrich_company",
                        lambda domain, apollo_id, spend=None: {
                            "matched": True, "id": "org-1", "name": "Tealium",
                            "domain": "tealium.com"})
    client.post(_ENRICH, json={"type": "company", "domain": "tealium.com"})
    assert saved[0]["dedupe"] == "org-1"


# ── The saver's own SQL ──────────────────────────────────────────────────────

class _FakeCursor:
    def __init__(self, log, found=True):
        self.log = log
        self.found = found

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchone(self):
        last = self.log[-1][0] if self.log else ""
        if last.startswith("UPDATE") and not self.found:
            return None
        return [42, None]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, found=True):
        self.log = []
        self.committed = False
        self.found = found

    def cursor(self):
        return _FakeCursor(self.log, self.found)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


@pytest.fixture
def fake_pg(monkeypatch):
    def _make(found=True):
        conn = _FakeConn(found)
        monkeypatch.setattr(appmod, "_pg_conn", lambda: conn)
        monkeypatch.setattr(appmod, "_CPI_HISTORY_TABLE_READY", False, raising=False)
        return conn

    return _make


def _stmts(conn, verb):
    return [(s, p) for s, p in conn.log if s.startswith(verb)]


def test_a_keyed_save_updates_in_place_instead_of_inserting(fake_pg):
    """Enriching the same person four times wrote four identical entries, each
    taking one of the sixty slots and evicting real history."""
    conn = fake_pg()
    appmod._cpi_history_save("a@b.com", "contact", "Binal", [{"n": 1}], dedupe="p1")
    assert _stmts(conn, "UPDATE")
    assert not _stmts(conn, "INSERT INTO cpi_search_history")


def test_a_key_with_no_existing_entry_still_gets_inserted(fake_pg):
    conn = fake_pg(found=False)
    appmod._cpi_history_save("a@b.com", "contact", "Binal", [{"n": 1}], dedupe="p1")
    assert _stmts(conn, "UPDATE")
    assert _stmts(conn, "INSERT INTO cpi_search_history")


def test_an_unkeyed_save_never_tries_to_update(fake_pg):
    """A chat exchange and a bulk reveal are new events every time; only a named
    thing can be refreshed in place."""
    conn = fake_pg()
    appmod._cpi_history_save("a@b.com", "chat", "q", [], "a")
    assert not _stmts(conn, "UPDATE")
    assert _stmts(conn, "INSERT INTO cpi_search_history")


def test_the_key_is_stored_so_the_next_save_can_find_it(fake_pg):
    conn = fake_pg(found=False)
    appmod._cpi_history_save("a@b.com", "contact", "Binal", [], dedupe="p1")
    stored = _stmts(conn, "INSERT INTO cpi_search_history")[0][1][3].adapted
    assert stored["dedupe"] == "p1"


def test_only_this_users_own_entry_can_be_refreshed(fake_pg):
    """A guessed key belonging to someone else must update nothing."""
    conn = fake_pg()
    appmod._cpi_history_save("a@b.com", "contact", "Binal", [], dedupe="p1")
    sql, params = _stmts(conn, "UPDATE")[0]
    assert "WHERE email = %s AND entity = %s AND filters->>'dedupe' = %s" in sql
    assert "a@b.com" in params


def test_the_refresh_touches_exactly_one_row(fake_pg):
    """Entries written before keying existed can be duplicates; updating all of
    them would leave several identical rows rather than one."""
    conn = fake_pg()
    appmod._cpi_history_save("a@b.com", "contact", "Binal", [], dedupe="p1")
    sql = _stmts(conn, "UPDATE")[0][0]
    assert "ORDER BY created_at DESC LIMIT 1" in sql


def test_credits_are_added_on_a_refresh_not_replaced(fake_pg):
    """Re-enriching is normally a free cache hit. Letting that 0 overwrite the 1
    really spent would erase the record of the purchase the entry exists for."""
    conn = fake_pg()
    appmod._cpi_history_save("a@b.com", "contact", "Binal", [], dedupe="p1",
                             filters={"credits": 0})
    sql = _stmts(conn, "UPDATE")[0][0]
    assert "COALESCE((filters->>'credits')::numeric, 0) + %s" in sql


def test_a_refresh_moves_the_entry_back_to_the_top(fake_pg):
    conn = fake_pg()
    appmod._cpi_history_save("a@b.com", "contact", "Binal", [], dedupe="p1")
    assert "created_at = now()" in _stmts(conn, "UPDATE")[0][0]


def test_a_refreshed_entry_still_prunes(fake_pg):
    conn = fake_pg()
    appmod._cpi_history_save("a@b.com", "contact", "Binal", [], dedupe="p1")
    assert _stmts(conn, "DELETE FROM cpi_search_history")


# ── Retention that does not depend on coming back ────────────────────────────

def test_expiry_is_not_scoped_to_one_user(fake_pg):
    """The per-user prune only runs when that user writes, so someone who stopped
    using the tool kept their revealed emails and phone numbers indefinitely."""
    conn = fake_pg()
    with conn.cursor() as cur:
        appmod._cpi_history_expire(cur)
    sql = conn.log[0][0]
    assert "email" not in sql
    assert "created_at < now() - make_interval" in sql


def test_expiry_uses_the_stated_retention_period(fake_pg):
    conn = fake_pg()
    with conn.cursor() as cur:
        appmod._cpi_history_expire(cur)
    assert conn.log[0][1] == (appmod._CPI_HISTORY_TTL_DAYS,)


def test_the_retention_period_is_the_one_the_comment_claims():
    assert appmod._CPI_HISTORY_TTL_DAYS == 90


def test_listing_the_drawer_expires_before_it_lists(client, monkeypatch):
    """An expired entry must not be shown once and swept afterwards."""
    conn = _FakeConn()

    class _Rows(_FakeCursor):
        def fetchall(self):
            return []

    monkeypatch.setattr(conn, "cursor", lambda: _Rows(conn.log), raising=False)
    monkeypatch.setattr(appmod, "_pg_conn", lambda: conn)
    monkeypatch.setattr(appmod, "_CPI_HISTORY_TABLE_READY", True, raising=False)
    r = client.get(_HISTORY)
    assert r.status_code == 200
    order = [s for s, _ in conn.log
             if s.startswith("DELETE FROM cpi_search_history") or s.startswith("SELECT")]
    assert order[0].startswith("DELETE")
    assert order[1].startswith("SELECT")


# ── What an entry is called ──────────────────────────────────────────────────

def _label(**filters):
    return appmod._cpi_history_label("people", filters)


def test_a_code_filtered_search_is_not_called_all_people():
    """It read nine keys out of fifty, so a NAICS search was labelled exactly the
    same as an unfiltered one."""
    assert _label(naics_codes=["54151"]) == "NAICS 54151"


def test_a_sic_search_says_which_scheme_it_used():
    assert _label(sic_codes=["7372"]) == "SIC 7372"


def test_a_technology_search_reads_as_one():
    assert _label(technologies=["Salesforce"]) == "uses Salesforce"


def test_an_employee_band_is_shown():
    assert _label(employee_min=50, employee_max=200) == "50-200 employees"


def test_an_open_ended_employee_band_is_not_printed_as_a_sentinel():
    """The top bucket carries 999999999 rather than nothing, and printing it would
    put a nine-digit number in the drawer."""
    assert _label(employee_min=10000, employee_max=999999999) == "10K+ employees"


def test_a_maximum_only_band_reads_as_a_maximum():
    assert _label(employee_max=200) == "under 200 employees"


def test_large_numbers_are_compacted_the_way_the_cards_do_it():
    assert _label(revenue_min=1500000, revenue_max=50000000) == "1.5M-50M revenue"


def test_funding_and_growth_are_shown():
    assert appmod._cpi_history_label("companies", {"total_funding_min": 10000000}) \
        == "10M+ funding"
    assert _label(headcount_growth_min=20) == "20%+ headcount growth"


def test_a_founding_window_reads_as_a_founding_window():
    assert _label(founded_min=2010, founded_max=2020) == "founded 2010-2020"


def test_the_pieces_are_joined_in_a_readable_order():
    assert _label(titles=["CMO"], person_locations=["United States"],
                  employee_min=50, employee_max=200) == \
        "CMO · United States · 50-200 employees"


def test_only_one_place_is_named():
    """A line listing the company, its country and the person's country reads as
    three filters when it is one search."""
    label = _label(company_domains=["tealium.com"], person_locations=["India"],
                   company_locations=["United States"])
    assert label == "tealium.com"


def test_a_pinned_company_is_described_rather_than_shown_as_an_id():
    assert _label(organization_ids=["5f3e2a1b9c"]) == "one specific company"


def test_a_genuinely_unfiltered_search_still_says_so():
    assert _label() == "All people"
    assert appmod._cpi_history_label("companies", {}) == "All companies"


def test_the_bookkeeping_that_travels_with_filters_is_not_a_filter():
    """company_detail and include_similar_titles ride along in every people
    search; neither narrows it."""
    assert _label(company_detail=True, include_similar_titles=True) == "All people"


def test_a_label_stays_short_enough_for_the_column_it_lives_in():
    label = _label(titles=["x" * 300], keywords="y" * 300)
    assert len(label) == 160


def test_at_most_three_values_of_one_kind_are_listed():
    label = _label(titles=["CMO", "CTO", "CFO", "COO"])
    assert label == "CMO, CTO, CFO"


# ── The drawer's own bookkeeping is not a search filter ──────────────────────

def test_the_credit_count_is_not_exported_as_a_filter():
    """A reveal entry exported from the drawer would otherwise print "Credits: 3"
    in the Search details sheet, where every other row is a constraint."""
    readable = dict(appmod._cpi_filters_readable(
        {"credits": 3, "from_cache": 1, "dedupe": "p1", "titles": ["CMO"]}))
    assert list(readable.values()) == ["CMO"]


# ── Reopening an entry, in the client ────────────────────────────────────────

def test_reopening_an_entry_drops_the_previous_searchs_caveats():
    """rejected/firmo describe what the LAST FETCH did. Left standing, the export
    of a reopened entry printed another search's "Removed on checking" counts as
    if they were this one's."""
    js = _js()
    body = js.split("window.cpiRestoreHistory")[1].split("window.cpiDeleteHistory")[0]
    assert "STATE.rejected = null" in body
    assert "STATE.firmo = null" in body


def test_a_reveal_entry_is_not_treated_as_a_search_of_the_panel():
    """Nobody typed filters to get these people, so reopening one must not wipe
    what the user has in the filter panel or claim filters for the rows."""
    js = _js()
    body = js.split("window.cpiRestoreHistory")[1].split("window.cpiDeleteHistory")[0]
    assert 'd.entity==="revealed"' in body
    assert "STATE.lastFilters = {}" in body


def test_a_reveal_entry_can_be_exported_but_a_contact_cannot():
    """Reveal rows are in search-row shape; a chat answer and a single enriched
    profile are not, and offering the button would hand back a broken file."""
    js = _js()
    assert 'var exportBtn = (isChat||isContact) ? "" :' in js
    assert 'var isRevealed=e.entity==="revealed"' in js


def test_a_reveal_entry_says_it_is_already_paid_for():
    js = _js()
    assert '" · already paid for · "+when' in js


def test_clearing_history_does_not_claim_success_it_did_not_have():
    """Each delete is its own request. It swallowed every failure and then said
    "Cleared history." regardless, which told the user their contact data was gone
    when it was still there."""
    js = _js()
    body = js.split("window.cpiClearAllHistory")[1].split("window.cpiCloseHistory")[0]
    assert "could not be deleted" in body
    assert "d.deleted" in body


def test_deleting_one_entry_checks_that_it_went():
    js = _js()
    body = js.split("window.cpiDeleteHistory")[1].split("window.cpiReopenChat")[0]
    assert "d.deleted" in body


def test_the_bundles_move_together():
    """A JS change without a CSS bump ships a stale pair to anyone with the old
    file cached."""
    html = open(os.path.join(_ROOT, "templates",
                             "company_people_intelligence.html"),
                encoding="utf-8").read()
    import re
    versions = set(re.findall(r"company_people_intelligence\.(?:js|css)'?\s*\)?\s*}}?\?v=(\d+)",
                              html))
    assert len(versions) == 1, versions
