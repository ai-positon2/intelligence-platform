"""Strategy & Campaign Book: the Campaign Book of a campaign, in the layout the QA Agent reads (tracker/paid_strategy_book.py)."""
import io

import pytest

from tests import strategy_fakes as F
from tracker import paid_qa, paid_qa_book, paid_strategy as ps, paid_strategy_book as book, paid_strategy_export as export, paid_strategy_job as job

INPUTS = F.clean()


@pytest.fixture(scope="module")
def rec():
    store = F.FakeStrategyStore()
    r = store.create(by="ana@position2.com", inputs=INPUTS, account_name="Acme Dental")
    store.begin(r["id"])
    job.run_build(r["id"], store=store, deps=F.deps())
    return store.get(r["id"])


def camp(rec, kind):
    return next(c for c in rec["plan"]["campaigns"] if c["kind"] == kind)


def test_a_book_has_a_row_for_every_check_that_applies_to_the_campaign_type(rec):
    for kind, qa in (("search", "Search"), ("pmax", "PMax")):
        rows = camp(rec, kind)["book"]
        want = paid_qa.applicable([], [qa])
        assert [r["id"] for r in rows] == [i["id"] for i in want] and [r["field"] for r in rows] == [i["check"] for i in want]
    assert len(camp(rec, "search")["book"]) == 61 and len(camp(rec, "pmax")["book"]) == 61
    dg = book.rows_for({"kind": "demandgen", "name": "P2_DemandGen_X_20261102", "kindLabel": "Demand Gen", "settings": {}, "build": {}}, INPUTS)
    assert len(dg) == 59 and {"6.4"} <= {r["id"] for r in dg} and not {"6.1", "6.2", "6.3"} & {r["id"] for r in dg}


def test_what_depends_on_the_plan_comes_from_the_plan_and_the_rest_is_the_teams_standing_answer(rec):
    rows = {r["id"]: r for r in camp(rec, "search")["book"]}
    assert rows["1.1"]["value"] == "P2_Search_Implants_20261102" and rows["1.1"]["source"] == "plan"
    assert rows["1.2"]["value"] == "Search" and rows["1.8"]["value"].startswith("Maximize conversions, then target CPA")
    assert rows["1.14"]["value"] == "Austin, Texas" and rows["1.10"]["value"] == "All devices, no bid adjustments" and rows["1.22"]["value"] == "Start 2026-11-02, no end date"
    assert rows["1.25"]["value"] == "https://acmedental.com/services/dental-implants"
    assert rows["1.4"]["value"] == "Minimum 1 ad per ad group (planned: 1 responsive search ad in 1 ad group)"
    assert rows["1.6"]["value"] == "Added: 4 sitelinks, 4 callouts, 1 structured snippets, a call asset"
    assert rows["1.18"]["value"] == "Negative Keyword List Added (lists: Jobs and careers; 2 campaign-level negatives)"
    assert rows["1.9"]["value"] == "Conversion (Account Level Default Goals): Form submit"
    for cid in ("1.5", "2.1", "3.4", "4.1", "5.1"):
        assert rows[cid]["source"] == "standard" and rows[cid]["value"] == paid_qa.item(cid)["expected"], cid
    assert rows["1.20"]["value"].startswith("Tracking template or final URL suffix") and "{campaignid}" in rows["1.20"]["value"]


def test_the_reason_for_a_setting_goes_in_the_last_column_as_a_sentence(rec):
    rows = {r["id"]: r for r in camp(rec, "search")["book"]}
    assert rows["1.8"]["other"].startswith("Standard start. Move to a target only after") and rows["1.14"]["other"].startswith("Practice location. Presence targeting")
    assert rows["1.11"]["other"].startswith("The offer is not gender specific.")
    assert all("  " not in r["other"] for r in camp(rec, "search")["book"])


def test_a_performance_max_book_describes_its_signals_and_themes(rec):
    rows = {r["id"]: r for r in camp(rec, "pmax")["book"]}
    assert "dental implants cost" in rows["6.1"]["value"] and "dental implants austin" in rows["6.2"]["value"] and rows["6.3"]["source"] == "standard"
    assert rows["1.25"]["value"] == "https://www.acmedental.com/"
    assert "1.5" not in rows and "1.3" not in rows                 # the Search-only checks are not in a Performance Max book


def test_an_unwritten_campaign_still_gets_its_settings_and_says_what_is_missing():
    rows = {r["id"]: r for r in book.rows_for({"kind": "search", "name": "P2_Search_X_20261102", "kindLabel": "Search", "settings": {}, "build": {}}, INPUTS)}
    assert rows["1.25"]["value"] == "Working final URL for every ad (to be confirmed)" and rows["1.6"]["value"] == "Added: to be written (none planned yet)"
    assert rows["1.4"]["value"] == "Minimum 1 ad per ad group (planned: 0 responsive search ads in 0 ad groups)"


def test_the_sheet_reads_like_the_template_with_a_heading_per_section(rec):
    rows = book.sheet_rows(camp(rec, "search")["book"])
    assert rows[0] == ["Field", "Value", "Other things to check"] and rows[1] == ["1. Campaign Setup", "", ""] and rows[2][0] == "Campaign Name"
    headings = [r[0] for r in rows if r[1] == "" and r[2] == "" and r[0]]
    assert headings == ["1. Campaign Setup", "2. Automation", "3. Automatically Created Assets", "4. Auto-Apply Recommendations", "5. Account Automation"]
    assert ["", "", ""] in rows


def test_the_qa_agent_reads_the_book_back_exactly(rec):
    c = camp(rec, "search")
    x = paid_qa_book.parse_xlsx(export.xlsx_campaign(rec, c["key"]), "book.xlsx")
    assert len(x["rows"]) == 61 and x["sheets"] == ["Campaign Book"]
    want = {r["field"]: r["value"] for r in c["book"]}
    got = {r["field"]: r["value"] for r in x["rows"]}
    assert got == want
    for field in ("Bid Strategy Type", "Devices", "Genders", "Household Income", "Location Settings", "Age", "Ad scheduling", "Campaign Type"):
        value = paid_qa_book.lookup(x, field)
        assert value == want[field] and paid_qa_book.filled(value), field                    # a real answer, not the template's placeholder
    csv = paid_qa_book.parse_csv(book.to_csv(c["book"]).encode(), "book.csv")
    assert {r["field"]: r["value"] for r in csv["rows"]} == want


def test_every_plan_fed_value_is_a_real_answer_not_a_placeholder(rec):
    for kind in ("search", "pmax"):
        for r in camp(rec, kind)["book"]:
            if r["source"] == "plan":
                assert paid_qa_book.filled(r["value"]), (kind, r["field"], r["value"])


def test_all_rows_is_one_flat_table():
    t = book.all_rows([{"name": "A", "book": [{"section": "s", "field": "f", "value": "v", "other": "o"}]}, {"name": "B"}])
    assert t == [["Campaign", "Section", "Field", "Value", "Other things to check"], ["A", "s", "f", "v", "o"]]


# ── Meta ────────────────────────────────────────────────────────────────────

def meta_campaign(kind="metaleads", **kw):
    from tests.test_paid_strategy_meta_check import CTX, INPUTS as MI, adset, ad
    from tracker import paid_strategy_meta_check as mc
    skel = mc.architecture({"campaigns": [{"kind": kind, "theme": "Implants", "purpose": "p", "share": 1, "landingPage": "/implants", "audience": "adults", "notes": "n",
                                           "settings": {"bidStrategy": {"value": "Highest volume", "why": "No history yet"}, "ages": {"value": "25 to 64", "why": "The offer is for adults of working age"}}}]},
                           dict(MI, launch="2026-11-02"))["campaigns"][0]
    built = mc.build({"adSets": [adset(interests=["Dentistry", "Teeth whitening"], excludes=["Dental school"]), adset(name="Lookalike", audienceType="lookalike", interests=[], lookalikes=["Form fills 1%"])]}, CTX, {})
    skel["build"] = built
    skel.update(kw)
    return skel


def test_a_meta_book_has_a_row_for_every_meta_check_that_applies_to_the_objective():
    for kind, qa in (("metaleads", "Meta Leads"), ("metasales", "Meta Sales"), ("metatraffic", "Meta Traffic"), ("metaawareness", "Meta Awareness")):
        rows = book.rows_for(meta_campaign(kind), INPUTS)
        want = paid_qa.applicable([], [qa], "meta")
        assert [r["id"] for r in rows] == [i["id"] for i in want] and [r["field"] for r in rows] == [i["check"] for i in want], kind
    assert len(book.rows_for(meta_campaign("metaleads"), INPUTS)) == 37 and len(book.rows_for(meta_campaign("metaawareness"), INPUTS)) < 37


def test_what_depends_on_the_meta_plan_comes_from_the_plan_and_the_rest_is_the_standard_answer():
    rows = {r["id"]: r for r in book.rows_for(meta_campaign(), INPUTS)}
    assert rows["1.1"]["value"] == "P2_MetaLeads_Implants_20261102" and rows["1.1"]["source"] == "plan" and rows["1.2"]["value"] == "Leads" and rows["1.3"]["value"] == "None"
    assert rows["1.5"]["value"] == "Advantage campaign budget, daily, %s a day (about %s a month)" % ("{:,.2f}".format(round(3000 / 30.4, 2)), "{:,.2f}".format(round(round(3000 / 30.4, 2) * 30.4)))
    assert rows["1.6"]["value"] == "Highest volume" and rows["1.7"]["value"] == "Start 2026-11-02, no end date" and rows["1.8"]["value"] == "Planned: 2 ad sets and 6 ads (at least 3 in each ad set)"
    assert rows["2.2"]["value"] == "Maximize number of conversions (leads)" and rows["2.3"]["value"] == "A dataset (pixel) is set, and the event is Lead" and rows["2.4"]["value"] == "7-day click and 1-day view"
    assert rows["2.5"]["value"] == "Austin, TX" and rows["2.7"]["value"] == "25 to 64" and rows["2.12"]["value"] == "Advantage+ placements" and rows["3.1"]["value"] == "image" and rows["3.8"]["value"] == "Off"
    assert rows["2.10"]["value"] == "Missing teeth: interests: Dentistry, Teeth whitening; Lookalike: lookalikes: Form fills 1%" and rows["2.11"]["value"] == "Missing teeth: Dental school"
    assert rows["3.6"]["value"] == "https://acme.com/implants" and rows["3.7"]["value"].startswith("Set on every ad: utm_source=meta&utm_medium=paid_social&utm_campaign={{campaign.name}}")
    for cid in ("1.4", "1.9", "2.6", "3.2", "3.9", "4.1", "5.1"):
        assert rows[cid]["source"] == "standard" and rows[cid]["value"] == paid_qa.item(cid, "meta")["expected"][:300], cid


def test_the_reason_for_a_meta_setting_goes_in_the_last_column_as_a_sentence_before_the_checklists_note():
    rows = {r["id"]: r for r in book.rows_for(meta_campaign(), INPUTS)}
    assert rows["2.7"]["other"] == "The offer is for adults of working age. Meta only lets a limit on age hold when Advantage+ audience is off." and rows["1.6"]["other"].startswith("No history yet.")
    assert rows["3.2"]["other"] == paid_qa.item("3.2", "meta")["otherThingsToCheck"]                    # a row with no reason carries the checklist's own note


def test_a_traffic_campaign_has_no_conversion_event_row_and_a_missing_plan_falls_back_to_the_standard_answer():
    rows = {r["id"]: r for r in book.rows_for(meta_campaign("metatraffic"), INPUTS)}
    assert "2.3" not in rows and "2.4" not in rows and "4.1" not in rows and rows["2.2"]["value"] == "Maximize number of landing page views"
    bare = {r["id"]: r for r in book.rows_for({"kind": "metaleads", "name": "P2_MetaLeads_X_20261102", "kindLabel": "Meta Leads", "objective": "Leads", "settings": {}, "build": {}}, INPUTS)}
    assert bare["1.1"]["value"] == "P2_MetaLeads_X_20261102" and bare["1.5"]["source"] == "standard" and bare["1.8"]["source"] == "standard" and bare["2.10"]["source"] == "standard" and bare["3.6"]["source"] == "standard"


def test_a_meta_book_is_the_same_layout_as_a_google_one_and_a_google_campaign_still_uses_its_own_checks(rec):
    sheet = book.sheet_rows(book.rows_for(meta_campaign(), INPUTS))
    assert sheet[0] == ["Field", "Value", "Other things to check"] and sheet[1][0] == "1. Campaign Setup" and any(r[0] == "Campaign Objective" and r[1] == "Leads" for r in sheet)
    assert [r["id"] for r in camp(rec, "search")["book"]][:3] == ["1.1", "1.2", "1.3"] and camp(rec, "search")["book"][2]["field"] == "Networks (Search & Video network)"


def test_the_audience_row_names_behaviors_and_a_broad_audience_and_the_formats_and_addresses_planned():
    from tests.test_paid_strategy_meta_check import CTX, adset, ad
    from tracker import paid_strategy_meta_check as mc
    c = meta_campaign()
    ads = [ad(name="V%d" % i, format="video" if i == 0 else "carousel" if i == 1 else "image", headline="Head %d" % i, finalUrl="/implants") for i in range(3)]
    c["build"] = mc.build({"adSets": [adset(name="Wide", audienceType="broad", interests=[], ads=ads), adset(name="Behave", interests=["Dentistry"], behaviors=["Frequent travellers"])]}, CTX, {})
    rows = {r["id"]: r for r in book.rows_for(c, INPUTS)}
    assert rows["2.10"]["value"] == "Wide: a broad audience, Advantage+ audience on; Behave: interests: Dentistry; behaviors: Frequent travellers"
    assert rows["3.1"]["value"] == "image, video, carousel"
    many = meta_campaign()
    many_ads = [ad(name="U%d" % i, headline="Head %d" % i, finalUrl="https://acme.com/implants") for i in range(3)]
    many["build"] = mc.build({"adSets": [adset(ads=many_ads)]}, CTX, {})
    many["build"]["adSets"][0]["ads"] = [dict(a, finalUrl="https://acme.com/p%d" % i) for i, a in enumerate(many["build"]["adSets"][0]["ads"] * 3)]
    assert rows["3.6"]["value"] == "https://acme.com/implants"
    assert book.rows_for(many, INPUTS)[[r["id"] for r in book.rows_for(many, INPUTS)].index("3.6")]["value"].count("https://") == 6               # at most six addresses are listed
