"""The enrich flow, audited the way the filters and the chat were.

Enrichment is the one path here that cannot be probed live: every call spends a
credit from a shared pool, so this audit is Apollo's own parameter schema read
against our code, plus the arithmetic of what a click costs.

What the schema says, and what we do about it:

  people/bulk_match takes at most 10 people per request. We chunk to 10.

  reveal_phone_number is ASYNC. The response carries a top-level request_id and
  NO phone numbers; the numbers arrive only by polling a webhook result. So
  setting it would spend credits for data this app never collects, and we
  correctly never set it. The consequence is that enrichment returns a phone only
  when Apollo already holds one inline, which per _pe_phones' own docstring means
  people already in the connected Apollo or CRM account. The UI promised "direct
  and mobile phone numbers" anyway, which is the mismatch fixed here.

  reveal_personal_emails and the two waterfall flags are likewise never set. The
  waterfall ones have variable, plan-dependent cost, so they are not something to
  switch on quietly.

Four defects found by reading, all about money rather than data:

  1. The by-id cache could never return a hit. It stores RAW Apollo records and
     the read gated on "sv", a stamp only _apollo_person_normalize applies, so
     every row failed the gate. Bulk enrich re-bought people it had already paid
     for and reported "cached: 0" while doing so, which reads as an honest number
     rather than a dead mechanism.
  2. The single Enrich button never consulted that cache at all, so enriching from
     the grid and then opening the same person paid twice.
  3. The route never passed a spend dict, so the one action on the page that
     definitely costs a credit reported nothing, and the button's static "1 credit"
     was wrong on a miss (free) and on a cache hit (free).
  4. Enrich-by-email never counted its credit even where the caller asked for the
     count.

And one about data: a masked surname was sent to people/match as an identifying
detail. Apollo masks withheld surnames as asterisks, so it was being asked to
match a person whose surname is punctuation. A miss costs nothing and returns
"not matched", so that failure was free, silent, and indistinguishable from Apollo
genuinely not holding the person.
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
_JS = os.path.join(_ROOT, "static", "js", "company_people_intelligence.js")


@pytest.fixture
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com",
                               "name": "Test User", "given_name": "Test"}
    return c


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(appmod, "_pg_conn", lambda: None)
    monkeypatch.setattr(appmod, "_cpi_history_save", lambda **kw: None)
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")
    yield


_PERSON = {"id": "p1", "first_name": "Binal", "last_name": "Shah",
           "email": "binal@tealium.com", "title": "CMO",
           "organization": {"name": "Tealium", "primary_domain": "tealium.com"}}


def _js():
    return open(_JS, encoding="utf-8").read()


def _match_stub(monkeypatch, person=None):
    """Records every people/match payload and hands back one person."""
    sent = []

    def _post(endpoint, payload, api_key, retries=3):
        sent.append((endpoint, dict(payload)))
        return {"person": dict(_PERSON if person is None else person)}

    monkeypatch.setattr(ac, "_post", _post)
    return sent


# ── What we send Apollo ───────────────────────────────────────────────────────

def test_a_masked_surname_is_never_sent_as_an_identifying_detail(monkeypatch):
    """Apollo withholds surnames as asterisks on the free search. "Vivek Sh***a"
    is not a name, and with an exact id there is nothing it can add."""
    sent = _match_stub(monkeypatch)
    appmod._cpi_enrich_person("Vivek Sh***a", "acme.com", "abc123")
    _endpoint, payload = sent[0]
    assert payload["id"] == "abc123"
    assert "*" not in repr(payload)
    # No name field of ANY kind alongside an exact id. Sending half a name as a
    # second signal can only dilute an identifier that is already unambiguous.
    for field in ("name", "first_name", "last_name"):
        assert field not in payload, field
    assert set(payload) == {"id", "domain"}


def test_without_an_id_the_unmasked_part_is_still_sent_as_a_first_name(monkeypatch):
    """A first name plus a domain is a weak signal but a real one, and first_name
    is Apollo's own field for it. Better than sending nothing, and far better than
    sending asterisks."""
    sent = _match_stub(monkeypatch)
    appmod._cpi_enrich_person("Vivek Sh***a", "acme.com", "")
    _endpoint, payload = sent[0]
    assert payload["first_name"] == "Vivek"
    assert "name" not in payload
    assert "*" not in repr(payload)


def test_an_unmasked_name_is_sent_exactly_as_it_is(monkeypatch):
    """The fix must not start mangling ordinary names."""
    sent = _match_stub(monkeypatch)
    appmod._cpi_enrich_person("Binal Shah", "tealium.com", "")
    _endpoint, payload = sent[0]
    assert payload["name"] == "Binal Shah"
    assert "first_name" not in payload


def test_a_website_url_is_reduced_to_a_bare_domain(monkeypatch):
    sent = _match_stub(monkeypatch)
    appmod._cpi_enrich_person("Binal Shah", "https://www.Tealium.com/", "")
    assert sent[0][1]["domain"] == "www.tealium.com"


def test_we_never_ask_for_the_asynchronous_reveals():
    """reveal_phone_number and the waterfall flags return a request_id and no data,
    retrievable only by polling a webhook this app does not run. Setting one would
    spend credits for something we never collect. Waterfall cost is also variable
    and plan-dependent, so it is not a thing to enable quietly."""
    src = open(os.path.join(_ROOT, "app.py"), encoding="utf-8").read()
    client_src = open(os.path.join(_ROOT, "tracker", "apollo_client.py"),
                      encoding="utf-8").read()
    for flag in ("reveal_phone_number", "run_waterfall_email", "run_waterfall_phone"):
        for where, text in (("app.py", src), ("apollo_client.py", client_src)):
            assert ('"%s": True' % flag) not in text, (where, flag)
            assert ("'%s': True" % flag) not in text, (where, flag)


def test_bulk_match_is_chunked_to_apollos_documented_maximum(monkeypatch):
    """The schema caps it at 10 per request. An 11th person in one call is not a
    partial success, it is a rejected request."""
    chunks = []

    def _post(endpoint, payload, api_key, retries=3):
        details = payload["details"]
        chunks.append(len(details))
        return {"matches": [{"id": d["id"]} for d in details]}

    monkeypatch.setattr(ac, "_post", _post)
    out = ac.bulk_match_people(["id%02d" % i for i in range(25)], "k")
    assert chunks == [10, 10, 5]
    assert max(chunks) <= 10
    assert len(out) == 25


# ── The cache that was never returning anything ───────────────────────────────

def test_a_cached_row_is_written_with_a_stamp_the_reader_accepts():
    """The defect: the read gated on "sv", which only _apollo_person_normalize
    applies, and this cache stores raw Apollo records. Every row failed the gate,
    so every bulk enrich re-bought people already paid for.
    """
    stamped = dict(_PERSON)
    stamped[appmod._CPI_ID_CACHE_SV_KEY] = appmod._CPI_ID_CACHE_VERSION
    assert int(stamped.get(appmod._CPI_ID_CACHE_SV_KEY) or 0) >= \
        appmod._CPI_ID_CACHE_VERSION
    # And the stamp the old gate looked for is genuinely absent from a raw record,
    # which is why the gate could never pass.
    assert "sv" not in _PERSON


def test_the_cache_stamp_does_not_collide_with_an_apollo_field():
    """It shares a dict with Apollo's own keys, so it has to be unmistakably ours."""
    assert appmod._CPI_ID_CACHE_SV_KEY.startswith("_")


def test_the_read_gate_and_the_write_stamp_are_the_same_constant():
    """Two constants that drift apart give back the original bug silently."""
    src = open(os.path.join(_ROOT, "app.py"), encoding="utf-8").read()
    read = src[src.index("def _cpi_id_cache_read"):src.index("def _cpi_id_cache_write")]
    write = src[src.index("def _cpi_id_cache_write"):]
    write = write[:write.index("\n\n\n")]
    assert "_CPI_ID_CACHE_SV_KEY" in read and "_CPI_ID_CACHE_VERSION" in read
    assert "_CPI_ID_CACHE_SV_KEY" in write and "_CPI_ID_CACHE_VERSION" in write
    assert "_PE_SHAPE_VERSION" not in read, \
        "this cache holds raw records, so the normalized shape stamp does not apply"


def test_a_person_already_bought_is_not_bought_again(monkeypatch):
    """The single Enrich button went straight to Apollo whatever the cache held, so
    enriching from the grid and then opening the same person paid twice."""
    calls = []

    def _post(endpoint, payload, api_key, retries=3):
        calls.append(endpoint)
        return {"person": dict(_PERSON)}

    monkeypatch.setattr(ac, "_post", _post)
    monkeypatch.setattr(appmod, "_cpi_id_cache_read",
                        lambda ids: {"p1": dict(_PERSON)})
    spend = {"credits": 0}
    profile = appmod._cpi_enrich_person("Binal Shah", "tealium.com", "p1", spend=spend)
    assert calls == [], "a cached person must not reach Apollo"
    assert spend["credits"] == 0
    assert profile.get("matched") is True
    assert profile.get("name") == "Binal Shah"


def test_a_person_bought_one_at_a_time_lands_in_the_shared_cache(monkeypatch):
    """Otherwise the two paths keep separate books and a bulk enrich re-buys
    someone the modal already paid for."""
    written = {}
    _match_stub(monkeypatch)
    monkeypatch.setattr(appmod, "_cpi_id_cache_read", lambda ids: {})
    monkeypatch.setattr(appmod, "_cpi_id_cache_write", lambda profiles: written.update(profiles))
    appmod._cpi_enrich_person("Binal Shah", "tealium.com", "p1")
    assert "p1" in written
    assert written["p1"]["first_name"] == "Binal"


# ── What a click actually costs ───────────────────────────────────────────────

def test_the_enrich_route_reports_the_credit_it_spent(client, monkeypatch):
    _match_stub(monkeypatch)
    monkeypatch.setattr(appmod, "_cpi_id_cache_read", lambda ids: {})
    monkeypatch.setattr(appmod, "_cpi_id_cache_write", lambda profiles: None)
    r = client.post("/p2/b2b-agents/company-people-intelligence/enrich",
                    json={"type": "person", "name": "Binal Shah",
                          "domain": "tealium.com", "apollo_id": "p1"})
    body = r.get_json()
    assert body["profile"]["matched"] is True
    assert body["credits"] == 1


def test_a_miss_is_reported_as_costing_nothing(client, monkeypatch):
    """Apollo bills for a match, not for a request. The button's static price said
    1 credit either way."""
    monkeypatch.setattr(ac, "_post", lambda *a, **k: {"person": {}})
    monkeypatch.setattr(appmod, "_cpi_id_cache_read", lambda ids: {})
    r = client.post("/p2/b2b-agents/company-people-intelligence/enrich",
                    json={"type": "person", "name": "Nobody At All",
                          "domain": "example.com", "apollo_id": "zz"})
    body = r.get_json()
    assert body["profile"]["matched"] is False
    assert body["credits"] == 0


def test_a_cache_hit_is_reported_as_costing_nothing(client, monkeypatch):
    monkeypatch.setattr(appmod, "_cpi_id_cache_read", lambda ids: {"p1": dict(_PERSON)})
    monkeypatch.setattr(ac, "_post", lambda *a, **k: pytest.fail("must not call Apollo"))
    r = client.post("/p2/b2b-agents/company-people-intelligence/enrich",
                    json={"type": "person", "name": "Binal Shah",
                          "domain": "tealium.com", "apollo_id": "p1"})
    body = r.get_json()
    assert body["profile"]["matched"] is True
    assert body["credits"] == 0


def test_enriching_by_email_counts_its_credit_like_every_other_path(monkeypatch):
    """It bills identically and was the one path that never said so."""
    monkeypatch.setattr(appmod, "_enrich_people",
                        lambda emails: {"a@b.com": {"matched": True, "name": "A B"}})
    spend = {"credits": 0}
    profile = appmod._cpi_enrich_person("", "", "", email="a@b.com", spend=spend)
    assert profile["matched"] is True
    assert spend["credits"] == 1


def test_an_email_that_matched_nothing_is_still_free(monkeypatch):
    monkeypatch.setattr(appmod, "_enrich_people", lambda emails: {})
    spend = {"credits": 0}
    appmod._cpi_enrich_person("", "", "", email="a@b.com", spend=spend)
    assert spend["credits"] == 0


def test_bulk_enrich_is_capped_so_one_click_cannot_drain_the_pool(client, monkeypatch):
    monkeypatch.setattr(appmod, "_cpi_id_cache_read", lambda ids: {})
    monkeypatch.setattr(appmod, "_cpi_id_cache_write", lambda profiles: None)
    seen = {}

    def _bulk(ids, api_key, **_kw):
        seen["n"] = len(ids)
        return {i: dict(_PERSON, id=i) for i in ids}

    monkeypatch.setattr(ac, "bulk_match_people", _bulk)
    r = client.post("/p2/b2b-agents/company-people-intelligence/enrich-bulk",
                    json={"ids": ["id%03d" % i for i in range(120)]})
    body = r.get_json()
    assert seen["n"] == appmod._CPI_BULK_ENRICH_CAP
    assert body["capped"] is True
    assert body["fetched"] == appmod._CPI_BULK_ENRICH_CAP


def test_bulk_enrich_reports_cache_hits_separately_from_purchases(client, monkeypatch):
    """This number was structurally always zero. It is the only signal of what a
    click cost, so it has to be able to be non-zero."""
    monkeypatch.setattr(appmod, "_cpi_id_cache_read",
                        lambda ids: {"a": dict(_PERSON, id="a")})
    monkeypatch.setattr(appmod, "_cpi_id_cache_write", lambda profiles: None)
    monkeypatch.setattr(ac, "bulk_match_people",
                        lambda ids, api_key, **_kw: {i: dict(_PERSON, id=i) for i in ids})
    r = client.post("/p2/b2b-agents/company-people-intelligence/enrich-bulk",
                    json={"ids": ["a", "b"]})
    body = r.get_json()
    assert body["cached"] == 1
    assert body["fetched"] == 1


# ── What the UI promises ──────────────────────────────────────────────────────

def test_the_enrich_promise_does_not_offer_phone_numbers_as_a_reason_to_spend():
    """_pe_phones already documents the truth: Apollo returns a number inline only
    for people in the connected Apollo or CRM account, and revealing any other is a
    separate metered async request this app does not make. The promise said "direct
    and mobile phone numbers" regardless, so someone read the promise, spent a
    credit, and then read the honest empty state."""
    js = _js()
    assert "direct and mobile phone numbers" not in js
    assert "Enrich for email, phone" not in js


def test_the_promise_explains_when_a_phone_does_arrive():
    """Removing the overpromise must not remove the information: phones do come
    back sometimes, and the reader should know for whom."""
    js = _js()
    assert "connected Apollo or CRM account" in js


def test_the_backend_and_the_copy_now_agree():
    """The empty state was already truthful. The bug was that the promise above it
    contradicted the function that fills it in."""
    src = open(os.path.join(_ROOT, "app.py"), encoding="utf-8").read()
    phones = src[src.index("def _pe_phones"):src.index("def _pe_emails")]
    assert "asynchronous" in phones and "webhook" in phones
    js = _js()
    assert "separate metered request this tool does not make" in js


def test_the_modal_reports_the_real_cost_rather_than_the_button_price():
    js = _js()
    block = js[js.index("function cpiRunEnrich"):]
    block = block[:block.index("\n}\n")]
    assert "d.credits" in block
    assert "cost nothing" in block


def test_both_bundles_moved_for_this_change():
    """The copy is in the script and the repo requires the pair to move together."""
    html = open(os.path.join(_ROOT, "templates",
                             "company_people_intelligence.html"), encoding="utf-8").read()
    versions = set(re.findall(r"company_people_intelligence\.(?:css|js)"
                              r"(?:'\s*\)\s*}})?\?v=(\d+)", html))
    assert len(versions) == 1, versions
    assert int(versions.pop()) >= 17
