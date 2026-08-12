"""The four filters that were free text over a closed Apollo vocabulary.

Industry got a picker first. NAICS, SIC, technologies and locations had exactly
the same problem and none: Apollo either recognizes a value or it does not, and it
says nothing either way, so a typed guess produced an empty page that read as
"nobody matches" instead of "that is not a value Apollo knows".

Measured live against this account on the free people endpoint, baseline
person_titles=["chief marketing officer"] returning 79,421 people:

  an invented technology uid            0 people
  a real one                           25,172
  the display name of the real one     25,172   (Apollo normalizes it itself)
  an invented location                  0
  a location misspelled in one part      826    (Apollo recovers, silently)
  "Texas" and "TX"                      1,914 each

  contact_email_status "verified"      44,635
  contact_email_status "unavailable"   31,819
  contact_email_status "unverified"    79,421   <- unchanged: Apollo ignored it
  "likely to engage"                   79,421   <- unchanged: Apollo ignored it

The last two are why the email-status chips changed. A chip that lights up and
changes nothing is the mismatch this page exists not to have, and the UI was also
sending "likely_to_engage", which is not even Apollo's spelling.

NAICS is the other measured trap: Apollo takes 2 to 5 digits, but real NAICS codes
are SIX, so pasting one from any government source is rejected by Apollo's own
schema. That used to happen silently.
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
from tracker import apollo_vocab as av  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TPL = os.path.join(_ROOT, "templates", "company_people_intelligence.html")
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
    appmod._CPI_VOCAB_SEEN.clear()
    appmod._CPI_INDUSTRY_SEEN.clear()
    monkeypatch.setattr(appmod, "_pg_conn", lambda: None)
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")
    yield
    appmod._CPI_VOCAB_SEEN.clear()


def _html():
    return open(_TPL, encoding="utf-8").read()


def _js():
    return open(_JS, encoding="utf-8").read()


def _combo_specs():
    """The picker registry as [(input id, filter key, vocabulary)]."""
    js = _js()
    block = js[js.index("var COMBO_SPECS"):js.index("var COMBO_FORMATS")]
    return re.findall(r'\["(\w+)",\s*"(\w+)",\s*"(\w+)"', block)


# ── The vocabularies themselves ───────────────────────────────────────────────

def test_every_vocabulary_the_ui_asks_for_is_one_the_server_serves():
    """A picker pointed at a vocabulary the endpoint rejects would render an empty
    list forever, which looks exactly like a vocabulary with no matches."""
    served = set(av.kinds()) | {"industry"}
    for _key, _filter, vocab in _combo_specs():
        assert vocab in served, vocab


def test_a_six_digit_naics_code_is_refused_rather_than_sent():
    """The measured trap: official NAICS codes are 6 digits and Apollo takes 2 to
    5, so the real code from any government source silently matched nothing."""
    assert av.validate("naics", "541511") is False
    assert av.validate("naics", "54151") is True
    assert av.validate("naics", "5415") is True
    assert av.validate("naics", "54") is True
    assert av.validate("naics", "5") is False


def test_a_sic_code_must_be_exactly_four_digits():
    assert av.validate("sic", "7372") is True
    for bad in ("737", "73720", "abcd", ""):
        assert av.validate("sic", bad) is False


def test_the_naics_hint_says_what_to_do_not_only_that_it_is_wrong():
    """A rejection that does not name the fix just moves the dead end."""
    hint = av.hint("naics")
    assert "6 digits" in hint and "54151" in hint


def test_split_valid_hands_back_the_bad_codes_instead_of_dropping_them():
    """Silently discarding half a filter runs a different search than was asked
    for, which is the failure this whole file is about."""
    good, bad = av.split_valid("naics", ["5415", "541511", "", "abc", "62"])
    assert good == ["5415", "62"]
    assert bad == ["541511", "abc"]


def test_a_free_text_vocabulary_accepts_anything_non_empty():
    """Technologies and locations have no shape Apollo enforces. Guessing which
    names exist is the guess the picker removes, not one to enforce."""
    assert av.validate("technology", "Some New CRM") is True
    assert av.validate("location", "Ulaanbaatar, Mongolia") is True
    assert av.validate("technology", "   ") is False


def test_an_unknown_vocabulary_is_refused_everywhere():
    assert av.validate("colour", "blue") is False
    assert av.suggest("colour", "b") == []


# ── Searching a vocabulary by the words people actually type ──────────────────

@pytest.mark.parametrize("kind,query,expected_first", [
    # Nothing in NAICS is titled "software": it is 5132, filed under publishing.
    ("naics", "software", "5132"),
    ("naics", "healthcare", "62"),
    ("naics", "hospital", "622"),
    ("naics", "5415", "5415"),
    ("sic", "software", "7372"),
    ("sic", "hospital", "8062"),
    ("sic", "7372", "7372"),
])
def test_a_code_is_found_by_the_ordinary_word_for_it(kind, query, expected_first):
    entries = av.suggest(kind, query)
    assert entries, "no entry at all for %r" % query
    assert entries[0]["value"] == expected_first


def test_a_partial_alias_word_never_outranks_a_real_title_match():
    """"hospital" is a fragment of "hospitality", which put eating places and
    hotels above general medical and surgical hospitals."""
    values = [e["value"] for e in av.suggest("sic", "hospital")]
    assert values[0] == "8062"
    # And the reverse query still finds hotels first, so the fix did not simply
    # break the alias.
    assert [e["value"] for e in av.suggest("sic", "hospitality")][0] == "7011"


def test_no_alias_points_at_a_code_that_is_not_in_the_vocabulary():
    """An alias only reorders. One naming a code that was never written down is a
    silent no-op, and worse, would look like the picker had offered it."""
    aliased = {c for m in av._CODE_ALIASES.values() for codes in m.values()
               for c in codes}
    known = {c for c, _ in av.NAICS} | {c for c, _ in av.SIC}
    assert aliased - known == set()


def test_a_code_entry_carries_its_official_title():
    """The title is what stops a code being a guess."""
    entry = [e for e in av.suggest("naics", "5415") if e["value"] == "5415"][0]
    assert "computer systems design" in entry["note"].lower()


def test_digits_typed_directly_match_by_prefix_and_not_by_accident():
    """Apollo prefix-matches NAICS, so a shorter code is a BROADER filter, not an
    arbitrary substring. Typing "21" means mining, and offering 521 (central
    banks), 621 (physicians) or 721 (hotels) because they happen to contain those
    digits would hand someone a filter meaning something else entirely.
    """
    values = [e["value"] for e in av.suggest("naics", "21", limit=500)]
    assert values == ["21", "211", "212", "213"]
    for accidental in ("521", "621", "721", "221", "321", "5221"):
        assert accidental not in values, accidental
    # And the broadening direction still works.
    wide = [e["value"] for e in av.suggest("naics", "54", limit=500)]
    assert "54" in wide and "5415" in wide


def test_technologies_are_offered_in_the_display_spelling_apollo_returns():
    """Apollo accepts both spellings and returns the display name, so seeding
    display names means a learned value lands as the same value, not a second
    spelling of one already on file."""
    values = [e["value"] for e in av.suggest("technology", "google analytics")]
    assert "Google Analytics" in values


def test_a_location_offers_the_country_before_the_city_inside_it():
    """Someone typing a country name means the country."""
    values = [e["value"] for e in av.suggest("location", "united")]
    assert values[0] in ("United Arab Emirates", "United Kingdom", "United States")
    assert "United States" in values


def test_a_learned_value_is_marked_confirmed_and_not_duplicated():
    """Apollo having returned a value is stronger evidence than this file."""
    entries = av.suggest("technology", "salesforce", learned=["Salesforce"])
    hits = [e for e in entries if e["value"].lower() == "salesforce"]
    assert len(hits) == 1
    assert hits[0]["confirmed"] is True


def test_a_value_apollo_returned_that_is_not_seeded_is_still_offered():
    entries = av.suggest("technology", "quirk", learned=["Quirk Analytics"])
    assert [e["value"] for e in entries] == ["Quirk Analytics"]
    assert entries[0]["confirmed"] is True


def test_a_seeded_value_never_seen_is_not_claimed_as_confirmed():
    entry = [e for e in av.suggest("technology", "mimecast")][0]
    assert entry["confirmed"] is False


def test_every_entry_has_the_shape_the_one_widget_renders():
    """The same list markup draws industries and these, so a missing key would
    render as the string "undefined" in a dropdown."""
    for kind in av.kinds():
        for e in av.suggest(kind, "", limit=5):
            assert set(e) == {"value", "kind", "confirmed", "covers", "note"}
            assert e["kind"] == kind
            assert e["covers"] == []


# ── The endpoint ──────────────────────────────────────────────────────────────

def test_the_endpoint_serves_each_vocabulary(client):
    for kind in av.kinds():
        r = client.get("/p2/b2b-agents/company-people-intelligence/vocab?kind=%s" % kind)
        assert r.status_code == 200
        body = r.get_json()
        assert body["kind"] == kind
        assert body["entries"], kind


def test_the_endpoint_refuses_a_vocabulary_it_does_not_have(client):
    r = client.get("/p2/b2b-agents/company-people-intelligence/vocab?kind=colour")
    assert r.status_code == 400
    assert "naics" in r.get_json()["kinds"]


def test_the_endpoint_returns_the_format_hint_for_a_code_vocabulary(client):
    r = client.get("/p2/b2b-agents/company-people-intelligence/vocab?kind=naics")
    assert "6 digits" in r.get_json()["hint"]
    r = client.get("/p2/b2b-agents/company-people-intelligence/vocab?kind=technology")
    assert r.get_json()["hint"] == ""


def test_the_endpoint_costs_nothing_and_calls_apollo_for_nothing(client, monkeypatch):
    """A dropdown that spent a credit per keystroke would be worse than the text
    box it replaced."""
    def _boom(*a, **k):
        raise AssertionError("the picker must not call Apollo")
    monkeypatch.setattr(ac, "_post", _boom)
    monkeypatch.setattr(ac, "search_companies", _boom)
    monkeypatch.setattr(ac, "search_people", _boom)
    for kind in av.kinds():
        r = client.get("/p2/b2b-agents/company-people-intelligence/vocab"
                       "?kind=%s&q=a" % kind)
        assert r.status_code == 200


# ── The search route ──────────────────────────────────────────────────────────

def _stub(monkeypatch):
    sent = {"payloads": []}

    def _post(endpoint, payload, api_key, retries=3):
        sent["payloads"].append(dict(payload))
        return {"organizations": [], "pagination": {"total_entries": 0}}

    monkeypatch.setattr(ac, "_post", _post)
    return sent


def _companies(client, **filters):
    r = client.post("/p2/b2b-agents/company-people-intelligence/search",
                    json={"entity": "companies", "filters": filters})
    assert r.status_code == 200
    return r.get_json()


def test_a_malformed_code_is_stripped_before_apollo_sees_it(client, monkeypatch):
    sent = _stub(monkeypatch)
    out = _companies(client, naics_codes=["5415", "541511"])
    assert sent["payloads"], "no search ran"
    assert sent["payloads"][0].get("organization_naics_codes") == ["5415"]
    assert out["invalid_codes"]["naics"]["codes"] == ["541511"]
    assert "6 digits" in out["invalid_codes"]["naics"]["hint"]


def test_a_filter_of_only_bad_codes_is_dropped_rather_than_sent_empty(client, monkeypatch):
    """A filter left behind as an empty list is not the same thing as a filter that
    was never asked for. It reaches the payload builder, gets saved into the search
    history, and comes back on restore as a filter that is present but means
    nothing, so the key is removed outright.
    """
    seen = {}

    def _sc(filters, api_key, page=1, per_page=25, strict=False, meta=None):
        seen.update(dict(filters))
        if meta is not None:
            meta["total_entries"] = 0
        return []

    monkeypatch.setattr(ac, "search_companies", _sc)
    out = _companies(client, sic_codes=["12345"], name="Acme")
    assert "sic_codes" not in seen, \
        "an all-invalid filter must be removed, not passed on as []"
    assert seen.get("name") == "Acme", "the rest of the search must be untouched"
    assert out["invalid_codes"]["sic"]["codes"] == ["12345"]


def test_good_codes_alone_are_reported_as_no_problem(client, monkeypatch):
    sent = _stub(monkeypatch)
    out = _companies(client, naics_codes=["5415"], sic_codes=["7372"])
    assert "invalid_codes" not in out
    assert sent["payloads"][0]["organization_naics_codes"] == ["5415"]
    assert sent["payloads"][0]["organization_sic_codes"] == ["7372"]


def test_the_exclude_variants_are_validated_too(client, monkeypatch):
    """Half the code filters on the page are exclusions, and a malformed exclusion
    silently excludes nothing, which is the more dangerous direction."""
    sent = _stub(monkeypatch)
    out = _companies(client, exclude_naics_codes=["541511"],
                     exclude_sic_codes=["737"])
    assert "not_organization_naics_codes" not in sent["payloads"][0]
    assert "not_organization_sic_codes" not in sent["payloads"][0]
    assert set(out["invalid_codes"]) == {"naics", "sic"}


# ── Learning from what Apollo returns ─────────────────────────────────────────

def test_technologies_and_places_on_a_record_join_their_pickers():
    appmod._cpi_record_vocab([{
        "technology_names": ["Quirk Analytics", "Salesforce"],
        "city": "Leeds", "state": "England", "country": "United Kingdom",
    }])
    techs = appmod._cpi_vocab_seen("technology")
    places = appmod._cpi_vocab_seen("location")
    assert "Quirk Analytics" in techs
    assert "United Kingdom" in places
    assert "England, United Kingdom" in places
    assert "Leeds, England" in places


def test_a_person_row_teaches_the_same_pickers_as_a_company_row():
    """The two tabs carry the employer under different key names, and a value is
    no less real for having arrived on a person."""
    appmod._cpi_record_vocab([{
        "organization_technologies": ["Odd Vendor"],
        "organization_city": "Pune", "organization_state": "Maharashtra",
        "organization_country": "India",
    }])
    assert "Odd Vendor" in appmod._cpi_vocab_seen("technology")
    assert "Pune, Maharashtra" in appmod._cpi_vocab_seen("location")


def test_learning_never_raises_on_junk_records():
    """Best-effort throughout: this only improves a dropdown, so it must never be
    able to fail a search."""
    appmod._cpi_record_vocab(None)
    appmod._cpi_record_vocab([None, "not a dict", 7, {}])
    appmod._cpi_record_vocab([{"technology_names": None, "city": None}])


def test_a_learned_value_is_never_the_whole_page_of_junk():
    """A cap exists because this set is fed by third-party strings."""
    assert appmod._CPI_VOCAB_SEEN_MAX >= 1000


# ── The UI ────────────────────────────────────────────────────────────────────

def test_every_filter_with_a_closed_vocabulary_is_a_picker_not_a_text_box():
    html = _html()
    for key, _filter, _vocab in _combo_specs():
        assert 'id="%sList"' % key in html, key
        assert 'id="%sChips"' % key in html, key
        assert 'id="%sCombo"' % key in html, key


def test_both_tabs_get_a_picker_for_the_same_filter():
    """A filter that is a picker on one tab and a text box on the other is how the
    two tabs come to disagree about the same Apollo parameter."""
    specs = _combo_specs()
    people = {f for k, f, _ in specs if k.startswith("fp")}
    company = {f for k, f, _ in specs if k.startswith("fc")}
    for shared in ("industries", "technologies", "technologies_all",
                   "exclude_technologies", "job_locations"):
        assert shared in people, shared
        assert shared in company, shared


def test_the_code_pickers_are_the_only_ones_that_refuse_a_typed_value():
    js = _js()
    block = js[js.index("var COMBO_FORMATS"):js.index("var COMBO = {}")]
    assert "naics" in block and "sic" in block
    assert "technology" not in block and "location" not in block


def test_the_client_side_code_shapes_agree_with_the_server():
    """Two copies of a rule that disagree is worse than one: the browser would
    accept a chip the search then threw away."""
    js = _js()
    block = js[js.index("var COMBO_FORMATS"):js.index("var COMBO = {}")]
    assert "/^[0-9]{2,5}$/" in block
    assert "/^[0-9]{4}$/" in block
    assert av._KINDS["naics"]["pattern"] == r"^[0-9]{2,5}$"
    assert av._KINDS["sic"]["pattern"] == r"^[0-9]{4}$"


def test_only_the_email_statuses_apollo_honors_are_offered():
    """Measured: "unverified" and "likely to engage" both returned the untouched
    79,421 baseline, so Apollo ignored them. A chip that lights up and changes
    nothing is exactly the mismatch this page is meant not to have."""
    html = _html()
    chunk = html[html.index('id="fpEmailStatus"'):
                 html.index('id="fpEmailStatus"') + 700]
    assert '"verified"' in chunk
    assert '"unavailable"' in chunk
    assert "likely_to_engage" not in chunk
    assert '"unverified"' not in chunk


def test_the_reason_the_two_dead_statuses_are_gone_is_written_down():
    """Otherwise a future pass restores them from Apollo's own documentation,
    which lists all four."""
    html = _html()
    head = html[:html.index('id="fpEmailStatus"')]
    note = head[head.rindex("{#"):]
    assert "79,421" in note and "ignored" in note


def test_enter_will_not_commit_an_option_from_an_earlier_query():
    """Found in a browser, not by reading the code. Typing "software" highlights
    5132; typing the real 6-digit NAICS code 541511 straight after and pressing
    Enter added 5132, because the list still held the previous query's options and
    Enter trusted the highlight. The list now says which query it answers, and
    Enter refuses to speak for any other.
    """
    js = _js()
    block = js[js.index('if(e.key==="Enter")'):]
    block = block[:block.index("return;") + 8]
    assert "dataset.q===typed" in block, \
        "Enter must compare the list's query against the box before using it"
    assert "renderComboList" not in block
    render = js[js.index("function renderComboList"):js.index("function comboUrl")]
    assert "list.dataset.q=String(query" in render, \
        "the list must record the query it was rendered for"


def test_a_refusal_survives_the_response_that_was_already_in_flight():
    """Also found in a browser. A code refused for being the wrong shape showed its
    explanation, and a moment later the pending suggestion request for the same
    text replaced it with an ordinary list of near matches."""
    js = _js()
    load = js[js.index("function loadCombo"):js.index("function moveComboCur")]
    assert 'dataset.warn==="1"' in load and "dataset.q===q" in load
    note = js[js.index("function comboNote"):js.index("function liftComboGroup")]
    assert 'dataset.warn="1"' in note


def test_only_one_list_is_ever_open():
    """These panels overlap each other, so a second open list floats over the field
    being typed into. Enforced where a list opens, not only on focus, because a
    list can open without the field being focused."""
    js = _js()
    for fn in ("function renderComboList", "function comboNote"):
        block = js[js.index(fn):]
        block = block[:block.index("\n}\n") + 3]
        assert "closeCombo(other[0])" in block, fn


def test_learning_writes_are_bounded_per_search():
    """A page of 100 companies carries over a thousand technology names, and the
    first search after a deploy must not do a thousand inserts inside the request."""
    assert appmod._CPI_VOCAB_WRITE_MAX <= 200
    appmod._cpi_record_vocab([{"technology_names": ["T%d" % i for i in range(900)]}])
    assert len(appmod._CPI_VOCAB_SEEN["technology"]) <= appmod._CPI_VOCAB_WRITE_MAX


def test_the_pickers_are_all_initialised(client):
    """A combo whose input is never wired shows no list at all, which is
    indistinguishable from a vocabulary with nothing in it."""
    js = _js()
    assert "COMBO_SPECS.forEach(function(spec){ initCombo(spec[0]); });" in js


def test_clearing_the_filters_empties_every_picker():
    js = _js()
    block = js[js.index("window.cpiClearFilters"):js.index("function splitCsv")]
    assert "COMBO_SPECS.forEach" in block
    assert "setComboValues(spec[0], [])" in block


def test_reopening_a_saved_search_refills_every_picker():
    """Chips showing an empty filter bar over results produced by a full one is a
    quiet lie about what was searched."""
    js = _js()
    assert "function restoreCombos(" in js
    assert "restoreCombos(STATE.entity" in js


def test_the_page_tells_the_browser_where_the_vocabulary_endpoint_is(client):
    r = client.get("/p2/b2b-agents/company-people-intelligence")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    assert "__CPI_VOCAB_URL__" in body
    assert "/vocab" in body


def test_the_assets_are_cache_busted_past_the_version_that_had_text_boxes():
    """The pickers are new markup AND new script; a stale cached pair renders
    half-wired combos."""
    html = _html()
    for asset in ("company_people_intelligence.css", "company_people_intelligence.js"):
        m = re.search(re.escape(asset) + r"['\"]?\s*\)?\s*}}?\?v=(\d+)", html) \
            or re.search(re.escape(asset) + r"\?v=(\d+)", html)
        assert m, asset
        assert int(m.group(1)) >= 16, asset
