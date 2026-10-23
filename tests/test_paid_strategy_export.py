"""Strategy & Campaign Book: the downloads (tracker/paid_strategy_export.py): Excel, CSV and PDF drawn from one set of tables."""
import copy
import csv
import io
import zipfile

import openpyxl
import pdfplumber
import pytest

from tests import strategy_fakes as F
from tracker import paid_strategy_export as export, paid_strategy_job as job


@pytest.fixture(scope="module")
def rec():
    store = F.FakeStrategyStore()
    r = store.create(by="ana@position2.com", inputs=F.clean(), account_name="Acme Dental")
    store.begin(r["id"])
    job.run_build(r["id"], store=store, deps=F.deps())
    return store.get(r["id"])


def test_the_tables_cover_every_part_of_the_plan_in_a_fixed_order(rec):
    t = export.tables(rec)
    assert list(t) == ["Overview", "Forecast", "Strategy", "Business", "Competitors", "Competitive read", "Social", "Account history", "Account detail", "Keyword research", "Market",
                       "Campaigns", "Ad groups and keywords", "Negatives", "Ad copy", "Assets", "Search themes", "Checks"]
    for name, (header, rows) in t.items():
        assert rows and all(len(r) == len(header) for r in rows), name


def test_the_overview_and_forecast_say_what_was_asked_and_what_it_buys(rec):
    t = export.tables(rec)
    ov = dict((r[0], r[1]) for r in t["Overview"][1] if r[0] != "Summary")
    assert ov["Client"] == "Acme Dental" and ov["Monthly budget"] == "6,000.00 USD" and ov["Launch date"] == "2026-11-02" and ov["Goal"] == "Leads (forms and enquiries)" and ov["Built by"] == "ana@position2.com"
    assert [r[0] for r in t["Forecast"][1][:3]] == ["Cautious", "Expected", "Strong"]


def test_ad_copy_is_one_row_per_line_with_its_length_and_limit(rec):
    header, rows = export.tables(rec)["Ad copy"]
    heads = [r for r in rows if r[0] == "P2_Search_Implants_20261102" and r[3] == "Headline"]
    assert len(heads) == 15 and heads[0][5] == "Dental Implants Austin" and heads[0][6] == 22 and heads[0][7] == 30
    assert all(r[6] <= r[7] for r in rows)
    assert ("P2_Search_Implants_20261102", "Implants Core", "Ad 1", "Path 1", "", "implants", 8, 15, "") in [tuple(r) for r in rows]
    assert any(r[3] == "Long headline" and r[7] == 90 for r in rows) and any(r[3] == "Business name" for r in rows)


def test_keywords_negatives_assets_and_themes_are_listed_per_campaign(rec):
    t = export.tables(rec)
    kw = t["Ad groups and keywords"][1]
    assert ["P2_Search_Implants_20261102", "Implants Core", "Dental implants", "service", "https://acmedental.com/services/dental-implants", "dental implants austin", "Exact", "service", "Planner"] in kw
    neg = t["Negatives"][1]
    assert ["P2_Search_Implants_20261102", "Campaign", "free", "Phrase", "Not a buyer"] in neg and ["P2_Search_Implants_20261102", "List: Jobs and careers", "careers", "Phrase", "Not buyers"] in neg
    assets = t["Assets"][1]
    assert any(r[2] == "Sitelink" and r[3] == "Dental Implants" and r[5].endswith("/services/dental-implants") for r in assets) and any(r[2] == "Callout" for r in assets)
    assert any(r[2] == "Image brief" and r[3] == "1.91:1" for r in assets) and any(r[2].startswith("Audience signal") for r in assets)
    assert [r[2] for r in t["Search themes"][1]] == ["dental implants austin", "family dentist austin"]


def test_the_checks_sheet_says_what_was_held_back(rec):
    rows = export.tables(rec)["Checks"][1]
    assert ["Negative removed", "dental implants [phrase] would block the keyword 'dental implants austin' (P2_Search_Implants_20261102)"] in rows
    assert ["Facts not found on a page", "1"] in rows


def test_the_workbook_has_every_table_and_a_book_sheet_per_campaign(rec):
    wb = openpyxl.load_workbook(io.BytesIO(export.xlsx(rec)))
    assert wb.sheetnames[:3] == ["Overview", "Forecast", "Strategy"] and wb.sheetnames[-2:] == ["Book Search_Implants_20261102", "Book PMax_Local_20261102"]
    ws = wb["Ad copy"]
    assert ws.freeze_panes == "A2" and ws["A1"].font.bold and ws["A1"].fill.fgColor.rgb.endswith("1F2A44") and ws.auto_filter.ref
    book = wb["Book Search_Implants_20261102"]
    rows = list(book.iter_rows(values_only=True))
    assert rows[0] == ("Field", "Value", "Other things to check") and rows[1] == ("1. Campaign Setup", None, None) and rows[2][:2] == ("Campaign Name", "P2_Search_Implants_20261102")
    assert book["A2"].font.bold and book.column_dimensions["B"].width == 60


def test_a_value_that_looks_like_a_formula_stays_text_in_every_format(rec):
    bad = copy.deepcopy(rec)
    bad["plan"]["campaigns"][0]["purpose"] = '=HYPERLINK("http://evil.example","click")'
    bad["plan"]["competitive"]["competitors"][0]["name"] = "+cmd|' /C calc'!A0"
    bad["plan"]["campaigns"][0]["build"]["adGroups"][0]["keywords"][0]["note"] = "@SUM(1+1)"
    bad["plan"]["campaigns"][0]["book"][0]["value"] = "-2+3"
    wb = openpyxl.load_workbook(io.BytesIO(export.xlsx(bad)))
    cell = next(c for row in wb["Campaigns"].iter_rows(min_row=2) for c in row if str(c.value).startswith("=HYPERLINK"))
    assert cell.data_type == "s" and cell.value == '=HYPERLINK("http://evil.example","click")'
    assert not any(c.data_type == "f" for ws in wb for row in ws.iter_rows() for c in row)
    z = zipfile.ZipFile(io.BytesIO(export.csv_zip(bad)))
    for name in z.namelist():
        for row in csv.reader(io.StringIO(z.read(name).decode())):
            assert not any(cell[:1] in ("=", "@") or (cell[:1] in "+-" and not cell.lstrip("+-")[:1].isdigit() and cell[:1] != "") for cell in row if not cell.startswith("'")), (name, row)
    assert "'=HYPERLINK" in z.read("campaigns.csv").decode() and "'@SUM" in z.read("ad-groups-and-keywords.csv").decode()
    assert export.safe("-2") == "-2" and export.safe(5) == 5 and export.safe(None) == "" and export.safe(True) == "Yes" and export.safe("=1") == "'=1" and export.safe("hello") == "hello"


def test_the_csv_book_is_one_flat_table_and_a_single_campaigns_is_in_the_qa_layout(rec):
    rows = list(csv.reader(io.StringIO(export.csv_book(rec))))
    assert rows[0] == ["Campaign", "Section", "Field", "Value", "Other things to check"] and len(rows) == 1 + 61 * 2
    one = list(csv.reader(io.StringIO(export.csv_campaign(rec, "pmaxlocal"))))
    assert one[0] == ["Field", "Value", "Other things to check"] and one[1] == ["1. Campaign Setup", "", ""] and export.csv_campaign(rec, "nope") is None and export.xlsx_campaign(rec, "nope") is None


def test_the_zip_has_every_table_and_every_book(rec):
    names = zipfile.ZipFile(io.BytesIO(export.csv_zip(rec))).namelist()
    assert "overview.csv" in names and "ad-copy.csv" in names and "campaign-book-all-campaigns.csv" in names
    assert "campaign-book-p2-search-implants-20261102.csv" in names and "campaign-book-p2-pmax-local-20261102.csv" in names and len(names) == len(set(names))


def test_sheet_names_are_legal_and_unique():
    used = set()
    a = export.book_sheet_name({"name": "P2_Search_" + "Very/Long:Name?" * 5}, used)
    used.add(a)
    b = export.book_sheet_name({"name": "P2_Search_" + "Very/Long:Name?" * 5}, used)
    assert len(a) <= 31 and not set("[]*?/\\:") & set(a) and a != b and len(b) <= 31


def test_the_pdf_is_a_document_with_the_whole_plan_in_it(rec):
    data = export.pdf(rec)
    assert data[:5] == b"%PDF-"
    pdf = pdfplumber.open(io.BytesIO(data))
    text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert len(pdf.pages) >= 8
    for want in ("Strategy and Campaign Book", "Acme Dental", "What the budget buys", "P2_Search_Implants_20261102", "P2_PMax_Local_20261102", "Campaign Book: P2_Search_Implants_20261102",
                 "Dental Implants Austin", "Open questions and what is needed from the client", "Which insurance plans are accepted?"):
        assert want in text, want


def test_the_pdf_survives_unusual_text_and_a_campaign_that_failed(rec):
    odd = copy.deepcopy(rec)
    odd["plan"]["campaigns"][0]["purpose"] = "Dentist éè Łukasz Яндекс 日本語 \U0001F600 <b>bold</b> & more"
    odd["plan"]["campaigns"][1]["build"] = {}
    odd["plan"]["campaigns"][1]["buildError"] = "the model was down"
    odd["plan"]["claims"] = [{"campaign": "x", "where": "w", "text": "Best dentist", "claim": "Best"}]
    text = "\n".join(p.extract_text() or "" for p in pdfplumber.open(io.BytesIO(export.pdf(odd))).pages)
    assert "Dentist éè" in text and "the model was down" in text and "Claims to confirm with the client before launch" in text and "<b>bold</b>" in text


def test_a_plan_with_nothing_in_it_still_makes_files():
    empty = {"name": "Empty", "inputs": {"name": "Empty", "currency": "USD", "goal": "leads"}, "plan": {}, "research": {}}
    assert export.tables(empty)["Overview"][1][0] == ["Client", "Empty"]
    assert openpyxl.load_workbook(io.BytesIO(export.xlsx(empty))).sheetnames == ["Overview"]
    assert export.pdf(empty)[:5] == b"%PDF-" and export.csv_book(empty).startswith("Campaign,Section")
    assert export.file_stem({"name": "A/B  Dental!!"}) == "A-B-Dental-strategy-and-campaign-book" and export.file_stem({}) == "client-strategy-and-campaign-book"


def test_a_control_character_in_a_caption_does_not_stop_the_workbook_or_the_files(rec):
    bad = copy.deepcopy(rec)
    bell, nul = chr(8), chr(0)
    bad["plan"]["campaigns"][0]["purpose"] = "Purpose" + bell + "with" + nul + "controls"
    bad["research"]["social"]["data"]["topPosts"][0]["caption"] = "A caption" + bell
    wb = openpyxl.load_workbook(io.BytesIO(export.xlsx(bad)))
    assert any(c.value == "Purposewithcontrols" for row in wb["Campaigns"].iter_rows(min_row=2) for c in row)
    assert b"Purposewithcontrols" in export.csv_zip(bad) or "Purposewithcontrols" in zipfile.ZipFile(io.BytesIO(export.csv_zip(bad))).read("campaigns.csv").decode()
    assert export.pdf(bad)[:5] == b"%PDF-"


def test_one_enormous_row_does_not_stop_the_pdf(rec):
    big = copy.deepcopy(rec)
    comp = big["plan"]["competitive"]["competitors"][0]
    long = ["An offer or message of about one hundred and twenty characters written out in full so that this row is far taller than a page " * 1] * 40
    for key in ("offers", "messages", "strengths", "weaknesses", "themes", "ctas"):
        comp[key] = long
    big["plan"]["strategy"]["summary"] = ["x " * 4000]
    data = export.pdf(big)
    assert data[:5] == b"%PDF-" and len(pdfplumber.open(io.BytesIO(data)).pages) > 10


def test_a_header_that_looks_like_a_formula_is_quoted_in_a_csv_too(rec):
    assert export._csv_text(["=bad", "fine"], [["+also", "ok"]]) == "'=bad,fine\r\n'+also,ok\r\n"


def test_what_was_dropped_for_a_limit_is_in_the_checks_sheet(rec):
    marked = copy.deepcopy(rec)
    marked["plan"]["checks"]["dropped"] = {"headlines": 3, "sitelinks": 1}
    rows = export.tables(marked)["Checks"][1]
    assert ["Dropped for being over a limit", "headlines: 3"] in rows and ["Dropped for being over a limit", "sitelinks: 1"] in rows


def test_a_cell_too_long_for_a_page_is_cut_in_the_pdf_only(rec):
    long = copy.deepcopy(rec)
    def words(r):
        return "\n".join(p.extract_text() or "" for p in pdfplumber.open(io.BytesIO(export.pdf(r))).pages)
    long["plan"]["strategy"]["objectives"][0]["basis"] = "Q" * 5000
    assert words(long).count("Q") - words(rec).count("Q") == export.CELL_CHARS
    assert any(r[3] == "Q" * 5000 for r in export.tables(long)["Strategy"][1])                    # every other format keeps all of it


# ── Meta ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def mrec():
    from tests import test_paid_strategy_meta_job as MJ
    store, sid = MJ.start()
    MJ.run(store, sid)
    return store.get(sid)


def test_a_meta_plan_has_meta_sheets_and_none_of_googles(mrec):
    t = export.tables(mrec)
    assert list(t) == ["Overview", "Strategy", "Business", "Competitors", "Competitive read", "Social", "Meta account history", "Meta interests", "Market", "Campaigns", "Meta ad sets", "Meta ads", "Checks"]
    for name, (header, rows) in t.items():
        assert rows and all(len(r) == len(header) for r in rows), name
    ov = dict(t["Overview"][1])
    assert ov["Meta ad account"] == "Acme Meta" and "Google Ads account" not in ov


def test_the_meta_sheets_list_each_ad_set_with_its_interests_and_budget_and_each_ad_with_its_lengths(mrec):
    head, rows = export.tables(mrec)["Meta ad sets"]
    first = dict(zip(head, rows[0]))
    assert first["Campaign"] == "P2_MetaLeads_Implants_20261102" and first["Ad set"] == "Missing teeth" and first["Audience type"] == "interests" and first["Interests"] == "Insulin pump (1.0M to 1.2M), Diabetes (20.0M to 24.0M)"
    assert first["Excluded"] == "Dental students" and first["Share of the campaign %"] == 70.0 and first["Daily budget"] == round(mrec["plan"]["campaigns"][0]["daily"] * 0.7, 2)
    head, rows = export.tables(mrec)["Meta ads"]
    ad = dict(zip(head, rows[0]))
    assert ad["Format"] == "video" and ad["Primary text"] == "Missing a tooth? Free consultation for dental implants" and ad["Characters (limit 125)"] == len(ad["Primary text"]) <= 125
    assert ad["Characters (limit 40)"] == len(ad["Headline"]) and ad["Button"] == "Book Now" and ad["Final URL"].startswith("https://acmedental.com")
    assert len(rows) == 12


def test_an_interest_that_could_not_be_checked_says_so_in_the_ad_sets_sheet_and_meta_history_and_interests_are_listed():
    from tests import test_paid_strategy_meta_job as MJ
    store, sid = MJ.start()
    unchecked = MJ.run(store, sid, meta_get=None)
    head, rows = export.tables(unchecked)["Meta ad sets"]
    assert "Made Up Interest (not checked)" in dict(zip(head, rows[0]))["Interests"]
    t = export.tables(MJ.run(*MJ.start()))
    h, r = t["Meta account history"]
    assert [x[0] for x in r][:2] == ["Campaign", "Placement"] and any(x[0] == "Placement" and x[1] == "Facebook" for x in r) and any(x[0] == "Device" for x in r) and any(x[0] == "Live ad" for x in r)
    h, r = t["Meta interests"]
    assert r[0][0] == "Insulin pump" and r[0][4] == 1 and r[0][5] == 600.0


def test_the_meta_checks_are_in_the_checks_sheet(mrec):
    rec = copy.deepcopy(mrec)
    rec["plan"]["checks"].update(learningNotChecked=2, interestNotes=["Meta's interest list could not be asked, so no interest is checked."])
    rows = export.tables(rec)["Checks"][1]
    assert ["Meta interests", "Meta's interest list could not be asked, so no interest is checked."] in rows and any(r[0] == "Meta learning bar not checked" and r[1].startswith("2 Meta campaigns") for r in rows)


def test_the_files_of_a_meta_plan_open_and_the_pdf_has_the_ad_sets_and_the_meta_book(mrec):
    wb = openpyxl.load_workbook(io.BytesIO(export.xlsx(mrec)))
    assert "Meta ad sets" in wb.sheetnames and "Meta ads" in wb.sheetnames and any(n.startswith("Book MetaLeads") for n in wb.sheetnames)
    z = zipfile.ZipFile(io.BytesIO(export.csv_zip(mrec)))
    assert "meta-ad-sets.csv" in z.namelist() and "meta-ads.csv" in z.namelist()
    with pdfplumber.open(io.BytesIO(export.pdf(mrec))) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert "Missing teeth (interests, 70.0% of the campaign)" in text and "Primary text: Missing a tooth?" in text and "Campaign Objective" in text and "Meta account history" in text
