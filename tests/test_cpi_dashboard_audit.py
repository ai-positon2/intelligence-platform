"""Audit of the dashboard flow: what the results surface claims about its rows.

The other flows fetch, buy, or store. This one is where a value becomes a
statement on a screen, so its failures are all of one kind: the page asserting
something the rows do not support.

  - The entity toggle was also the variable that said what the rows on screen
    ARE, so flipping the tab relabelled a grid of people as companies without
    refetching. The next re-render drew them through the company card, and an
    export built company columns out of person rows.
  - "No matches. Try widening the filters." was said even when Apollo had
    matched and the verification pass had removed everything it returned. The
    breakdown that explained it lived in the toolbar, which hides itself when
    there are no rows.
  - "Select all", next to a line reading "Showing 24 of 79,421", selects 24.
  - Headcount growth was rendered by guessing its unit per value, so a company
    that grew 150% was printed as "+1.5%".
  - The same growth figure left the app as three different values, and on the
    People sheet as no value at all.
  - Every company on the page was announced to Google's favicon service, one
    request per card.
"""

import csv
import io
import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = os.path.join(_ROOT, "static", "js", "company_people_intelligence.js")
_EXPORT = "/p2/b2b-agents/company-people-intelligence/export"


def _js():
    return open(_JS, encoding="utf-8").read()


def _fn(name, until):
    """The body of one client function, so an assertion cannot pass by matching
    the same text somewhere else in the file."""
    js = _js()
    return js.split(name)[1].split(until)[0]


@pytest.fixture
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    return c


# ── The rows know what they are ──────────────────────────────────────────────

def test_what_the_rows_are_is_separate_from_what_the_panel_is_set_to():
    js = _js()
    assert "shownEntity: null" in js
    assert "entity: \"people\"" in js


def test_the_card_renderer_follows_the_rows_not_the_tab():
    """Otherwise a re-render after a tab switch draws person rows through the
    company card, and every name on the page becomes "Unknown"."""
    body = _fn("function renderResults(", "function row(svg")
    assert 'STATE.shownEntity==="people" ? personCard(r,i) : companyCard(r,i)' in body
    assert "STATE.entity" not in body


def test_the_count_line_names_the_rows_on_screen():
    body = _fn("function noun(n){", "function renderResults")
    assert "STATE.shownEntity" in body
    assert "STATE.entity" not in body


def test_the_employer_note_belongs_to_the_fetched_page():
    body = _fn("function firmoNote(){", "/* Several of Apollo's filters")
    assert 'STATE.shownEntity!=="people"' in body


def test_bulk_enrich_offers_itself_for_the_people_on_screen():
    body = _fn("function updateBulk(){", "window.cpiToggleMenu")
    assert 'STATE.shownEntity==="people"' in body


def test_an_export_is_labelled_with_the_rows_it_contains():
    """The export route picks its column set from this, so a mismatch here is a
    spreadsheet of empty cells under the wrong headers."""
    body = _fn("window.cpiExport = function", "/* ── History ── */")
    assert "doCpiDownload(STATE.shownEntity||STATE.entity" in body


def test_a_history_entry_records_the_kind_its_rows_actually_are():
    body = _fn("function saveHistory(", "function applyFiltersToForm")
    assert "entity: STATE.shownEntity||STATE.entity" in body


def test_the_kind_is_stamped_where_the_rows_arrive():
    body = _fn("window.cpiRunSearch = function", "function renderCompanyChoicePicker")
    assert "STATE.shownEntity = STATE.entity" in body


def test_reopening_a_saved_entry_stamps_the_kind_it_recorded():
    body = _fn("window.cpiRestoreHistory = function", "window.cpiDeleteHistory")
    assert "STATE.shownEntity = STATE.entity" in body


def test_switching_tabs_stops_load_more_continuing_the_other_search():
    """Load more continues the DISPLAYED search. From the other panel it appended
    companies to the bottom of a list of people."""
    body = _fn("window.cpiSetEntity = function", "function companyDetailOn")
    assert "STATE.shownEntity!==entity" in body
    assert 'more.style.display="none"' in body


def test_switching_tabs_no_longer_throws_away_the_selection():
    """The selection belongs to the rows, and the rows survive the switch now."""
    body = _fn("window.cpiSetEntity = function", "function companyDetailOn")
    assert "STATE.selected={}" not in body


# ── An empty page says why it is empty ───────────────────────────────────────

def test_an_empty_page_does_not_blame_the_filters_when_apollo_matched():
    body = _fn("function renderResults(", "function row(svg")
    assert "Apollo returned " in body
    assert "none of them matched" in body


def test_the_plain_no_matches_message_survives_for_a_real_no_match():
    body = _fn("function renderResults(", "function row(svg")
    assert "No matches. Try widening the filters." in body


def test_both_places_that_report_removals_read_the_same_source():
    """The count line and the empty state have to agree about what happened."""
    js = _js()
    assert js.count("function rejectedReasons(") == 1
    assert "rejectedReasons()" in _fn("function rejectedNote(){", "/* \"1 companies\"")
    assert "rejectedReasons()" in _fn("function renderResults(", "function row(svg")


def test_a_reason_that_removed_nothing_is_not_reported():
    """A filter that ran and dropped nothing must not show up as "0 removed"."""
    body = _fn("function rejectedReasons(){", "function rejectedNote")
    assert "filter(function(k){ return r[k]; })" in body


# ── The selection button names its scope ─────────────────────────────────────

def test_select_all_says_how_many_it_will_select():
    body = _fn("function syncSelectAllLabel(){", "function updateBulk")
    assert '" Select these "+n' in body
    assert '" Clear "+n' in body
    assert '" Select all"' not in body


def test_the_button_starts_out_named_for_a_page_not_for_everything():
    """The static markup is what a user sees before any count exists."""
    html = open(os.path.join(_ROOT, "templates",
                             "company_people_intelligence.html"),
                encoding="utf-8").read()
    bar = html.split('id="cpiSelectAll"')[1][:400]
    assert "Select all" in bar  # replaced by the counted label as soon as rows land


# ── Headcount growth has one unit ────────────────────────────────────────────

def test_growth_is_read_as_the_fraction_apollo_sends():
    """Every observed value in this repo's fixtures is a fraction (0.08, 0.19), and
    the External Usage export has multiplied the same field by 100 since long
    before this page existed."""
    body = _fn("function pmGrowth(pct){", "/* City, state and country")
    assert "n = n * 100;" in body


def test_growth_no_longer_guesses_the_unit_per_value():
    """The guess was one-directional: a company that grew 150% arrives as 1.5 and
    was printed as "+1.5%", so the fastest-growing employers looked flattest."""
    body = _fn("function pmGrowth(pct){", "/* City, state and country")
    assert "Math.abs(n)<=1" not in body


def test_the_observed_apollo_values_are_still_fractions():
    """If this ever fails, the convention above needs re-checking against live
    data before the cards are trusted."""
    fixture = open(os.path.join(_ROOT, "tests", "test_cpi_employer_detail.py"),
                   encoding="utf-8").read()
    assert '"organization_headcount_twelve_month_growth": 0.19' in fixture


# ── The same number leaves the app as one number ─────────────────────────────

def test_a_growth_fraction_exports_as_the_percent_its_header_promises():
    assert appmod._cpi_export_percent(0.19) == "19"


def test_a_whole_percent_has_no_pointless_decimal():
    assert appmod._cpi_export_percent(1.5) == "150"


def test_an_uneven_percent_keeps_one_decimal():
    assert appmod._cpi_export_percent(0.1234) == "12.3"


def test_a_negative_growth_stays_negative():
    assert appmod._cpi_export_percent(-0.08) == "-8"


def test_a_missing_growth_stays_empty():
    assert appmod._cpi_export_percent(None) == ""
    assert appmod._cpi_export_percent("") == ""


def test_an_unparseable_growth_is_passed_through_not_dropped():
    """Losing a value Apollo did send is worse than printing something odd."""
    assert appmod._cpi_export_percent("n/a") == "n/a"


def test_every_percent_column_exists_in_a_column_list():
    """A typo in the set would silently leave a column unconverted."""
    keys = {k for k, _ in appmod._CPI_PERSON_COLS} | \
           {k for k, _ in appmod._CPI_COMPANY_COLS}
    assert appmod._CPI_EXPORT_PERCENT_COLS <= keys


def test_every_percent_column_says_percent_in_its_header():
    labels = dict(appmod._CPI_PERSON_COLS)
    labels.update(dict(appmod._CPI_COMPANY_COLS))
    for key in appmod._CPI_EXPORT_PERCENT_COLS:
        assert labels[key].endswith("%"), key


def test_the_people_sheet_carries_the_growth_the_card_leads_with():
    keys = {k for k, _ in appmod._CPI_PERSON_COLS}
    assert "organization_growth12" in keys


def test_a_real_export_writes_the_percent_not_the_fraction(client):
    """End to end through the route, because the conversion is only worth having
    if the cell formatter actually reaches it."""
    r = client.post(_EXPORT, json={
        "entity": "people", "format": "csv",
        "rows": [{"full_name": "Binal Shah", "organization_growth12": 0.19}]})
    assert r.status_code == 200
    # Read as CSV, not split on commas: cells are quoted and some contain commas.
    rows = list(csv.reader(io.StringIO(r.data.decode("utf-8-sig"))))
    idx = rows[0].index("Company headcount growth 12mo %")
    assert rows[1][idx] == "19"


def test_the_company_sheet_converts_its_own_growth_columns(client):
    r = client.post(_EXPORT, json={
        "entity": "companies", "format": "csv",
        "rows": [{"name": "Tealium", "growth12": 0.08}]})
    rows = list(csv.reader(io.StringIO(r.data.decode("utf-8-sig"))))
    idx = rows[0].index("Headcount growth 12mo %")
    assert rows[1][idx] == "8"


def test_an_xlsx_gets_the_same_treatment(client):
    """The three cell sites in the workbook builder share one formatter; this pins
    that they still do."""
    openpyxl = pytest.importorskip("openpyxl")
    r = client.post(_EXPORT, json={
        "entity": "companies", "format": "xlsx",
        "rows": [{"name": "Tealium", "growth12": 0.08}]})
    wb = openpyxl.load_workbook(io.BytesIO(r.data))
    ws = wb[wb.sheetnames[0]]
    header = [c.value for c in ws[1]]
    assert ws.cell(row=2, column=header.index("Headcount growth 12mo %") + 1).value == "8"


# ── Nothing about the page leaves the building ───────────────────────────────

def test_no_company_on_the_page_is_announced_to_a_third_party():
    """One favicon request per card disclosed the prospect list being worked, and
    when it was scrolled, to a third party."""
    js = _js()
    assert "s2/favicons" not in js
    assert "google.com" not in js


def test_the_favicon_helper_is_gone_rather_than_left_dead():
    js = _js()
    assert "logoFor" not in js


def test_apollos_own_logo_is_still_used_where_it_has_one():
    js = _js()
    assert "var lg=safeUrl(p.organization_logo);" in js
    assert "var src=safeUrl(c.logo_url);" in js


def test_the_bundles_move_together():
    import re
    html = open(os.path.join(_ROOT, "templates",
                             "company_people_intelligence.html"),
                encoding="utf-8").read()
    versions = set(re.findall(
        r"company_people_intelligence\.(?:js|css)'?\s*\)?\s*}}?\?v=(\d+)", html))
    assert len(versions) == 1, versions
