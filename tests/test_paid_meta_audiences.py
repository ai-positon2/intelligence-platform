"""Audience Segment Analysis for Meta: tracker/paid_meta_audiences.py (the read), and the report it feeds (tracker/paid_audiences.py, its insights).

Pinned here: what an ad set's targeting names (custom audiences, lookalikes, interests, behaviors, what it excludes, Advantage+ audience as a suggestion); an
audience's numbers are those of the ad sets that use it, and an ad set aimed at two audiences counts under both; the account's totals include the ad sets
that use no audience, so coverage is honest; the audience lists (size, ready, in use); a part that cannot be read is a warning, the ad sets and their results
are raised; Meta's advice never offers a bid adjustment Meta does not have; and a Google and a Meta account are one report only when they bill in the same
currency. Nothing here talks to Meta.
"""
import datetime as dt
import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tests.gads_fakes import FakeStore  # noqa: E402
from tracker import paid_audiences as a  # noqa: E402
from tracker import paid_audiences_insights as ins  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_geo  # noqa: E402
from tracker import paid_meta  # noqa: E402
from tracker import paid_meta_audiences as ma  # noqa: E402

START, END = dt.date(2026, 6, 1), dt.date(2026, 6, 30)
ACCT = {"id": "act_5550001", "name": "Acme Landscaping", "currency": "USD", "tz": "America/New_York", "platform": "meta"}


def lead_row(adset, name, spend, leads, campaign="C1", camp_name="Leads", imp=20000, clicks=900, objective="OUTCOME_LEADS"):
    return {"adset_id": adset, "adset_name": name, "campaign_id": campaign, "campaign_name": camp_name, "objective": objective, "spend": str(spend),
            "impressions": str(imp), "inline_link_clicks": str(clicks), "actions": [{"action_type": "lead", "value": str(leads)}] if leads else [], "action_values": []}


def adset(sid, name, custom=(), excluded=(), interests=(), behaviors=(), advantage=False, campaign="C1", exclusions=None):
    t = {"custom_audiences": [{"id": i, "name": n} for i, n in custom], "excluded_custom_audiences": [{"id": i, "name": n} for i, n in excluded]}
    flex = {}
    if interests:
        flex["interests"] = [{"id": i, "name": n} for i, n in interests]
    if behaviors:
        flex["behaviors"] = [{"id": i, "name": n} for i, n in behaviors]
    if flex:
        t["flexible_spec"] = [flex]
    if exclusions:
        t["exclusions"] = exclusions
    if advantage:
        t["targeting_automation"] = {"advantage_audience": 1}
    return {"id": sid, "name": name, "campaign_id": campaign, "effective_status": "ACTIVE", "targeting": t}


def audience(aid, name, subtype="WEBSITE", size=5000, code=200, desc="Ready"):
    return {"id": aid, "name": name, "subtype": subtype, "approximate_count_lower_bound": size, "delivery_status": {"code": code, "description": desc}}


def graph(adsets=None, insights=None, audiences=None, fail=()):
    def fetch(path, params):
        part = "adsets" if path.endswith("/adsets") else "insights" if path.endswith("/insights") else "audiences"
        if part in fail:
            raise base.AdsError("Meta answered 400: (#100) permission denied")
        return {"adsets": adsets, "insights": insights, "audiences": audiences}[part] or []
    return fetch


# ── Reading what Meta says ──────────────────────────────────────────────────

@pytest.mark.parametrize("subtype,kind", [("LOOKALIKE", "similar"), ("CUSTOM", "customer_match"), ("WEBSITE", "remarketing"), ("ENGAGEMENT", "remarketing"),
                                          ("APP", "remarketing"), ("NEVER_SEEN", "custom")])
def test_an_audiences_kind_comes_from_its_subtype(subtype, kind):
    assert ma.parse_audiences([audience("1", "A", subtype)])["1"]["kind"] == kind


def test_an_audience_has_a_size_and_whether_it_can_be_used():
    out = ma.parse_audiences([audience("1", "Ready", size=5000), audience("2", "Not ready", code=300, desc="Too small"), audience("3", "Hidden", size=-1)])
    assert out["1"]["size"] == 5000 and out["1"]["ready"] is True
    assert out["2"]["ready"] is False and out["2"]["why"] == "Too small"
    assert out["3"]["size"] is None                                        # Meta says -1 when it will not say
    assert ma.parse_audiences([{"name": "no id"}]) == {}


def test_targeting_names_what_an_ad_set_includes_and_excludes():
    t = ma.parse_targeting(adset("1", "S", custom=[("10", "Site")], excluded=[("11", "Customers")], interests=[("20", "Gardening")], behaviors=[("30", "Travelers")],
                                 exclusions={"interests": [{"id": "21", "name": "Cooking"}]})["targeting"])
    assert t["include"] == [("audience", "10", "Site"), ("interest", "20", "Gardening"), ("behavior", "30", "Travelers")]
    assert t["exclude"] == [("audience", "11", "Customers"), ("interest", "21", "Cooking")] and t["suggestion"] is False
    assert ma.parse_targeting({"targeting_automation": {"advantage_audience": 1}})["suggestion"] is True
    assert ma.parse_targeting(None) == {"include": [], "exclude": [], "suggestion": False}


def test_an_ad_sets_results_carry_the_campaign_type_its_objective_makes_it():
    out = ma.parse_insights([lead_row("1", "S", 100, 10), lead_row("1", "S", 50, 5), lead_row("2", "T", 80, 0, objective="OUTCOME_TRAFFIC"), {"spend": "5"}])
    assert out["1"]["ctype"] == "Meta Leads" and out["1"]["m"]["cost"] == 150.0 and out["1"]["m"]["conv"] == 15.0
    assert out["2"]["ctype"] == "Meta Traffic" and set(out) == {"1", "2"}


# ── The raw read ────────────────────────────────────────────────────────────

def raw_of(**kw):
    return ma.read_account(ACCT, START, END, store=FakeStore(), fetch=graph(**kw))


def two_sets():
    return dict(adsets=[adset("1", "Site visitors", custom=[("10", "Site")], interests=[("20", "Gardening")]), adset("2", "Broad"),
                        adset("3", "Both", custom=[("10", "Site"), ("11", "Customers")], excluded=[("12", "Staff")], advantage=True)],
                insights=[lead_row("1", "Site visitors", 300, 30), lead_row("2", "Broad", 500, 20), lead_row("3", "Both", 200, 10)],
                audiences=[audience("10", "Site visitors 30d", "WEBSITE"), audience("11", "Customer list", "CUSTOM", size=400), audience("12", "Staff", "CUSTOM"),
                           audience("13", "Unused list", "LOOKALIKE")])


def test_an_audiences_numbers_are_those_of_the_ad_sets_that_use_it_and_an_ad_set_counts_under_each_audience_it_names():
    raw = raw_of(**two_sets())
    site = raw["audiences"]["act_5550001|audience:10"]
    assert site["label"] == "Site visitors 30d" and site["kind"] == "remarketing" and len(site["uses"]) == 2
    assert site["all"]["cost"] == 500.0 and site["all"]["conv"] == 40.0                      # ad sets 1 and 3, both aimed at it
    assert raw["audiences"]["act_5550001|audience:11"]["all"]["cost"] == 200.0               # ad set 3 is also under the customer list
    interest = raw["audiences"]["act_5550001|interest:20"]
    assert interest["kind"] == "interest" and interest["all"]["cost"] == 300.0
    assert all(u["platform"] == "meta" and u["bid"] is None and u["bidding"] == "" and u["level"] == "ad_group" for u in site["uses"])
    assert sorted(u["adGroup"] for u in site["uses"]) == ["Both", "Site visitors"]


def test_an_ad_set_on_advantage_audience_treats_its_audiences_as_suggestions():
    uses = raw_of(**two_sets())["audiences"]["act_5550001|audience:10"]["uses"]
    modes = {u["adGroup"]: u["mode"] for u in uses}
    assert modes == {"Site visitors": "targeting", "Both": "observation"}


def test_the_accounts_totals_include_the_ad_sets_that_use_no_audience():
    raw = raw_of(**two_sets())
    assert raw["totals"]["Meta Leads"]["cost"] == 1000.0 and raw["totals"]["Meta Leads"]["conv"] == 60.0       # the broad ad set's $500 is in it
    assert raw["platform"] == "meta" and raw["warnings"] == [] and raw["members"] == []


def test_the_lists_say_their_size_whether_they_are_ready_and_whether_anything_uses_them():
    lists = {l["id"]: l for l in raw_of(**two_sets())["lists"]}
    assert lists["10"]["used"] is True and lists["12"]["used"] is True                       # used by exclusion counts as used
    assert lists["13"]["used"] is False and lists["13"]["kind"] == "similar" and lists["11"]["sizeSearch"] == 400
    assert all(l["platform"] == "meta" for l in lists.values())


def test_what_ad_sets_exclude_is_listed_with_the_ad_set_it_belongs_to():
    excl = raw_of(**two_sets())["exclusions"]
    assert excl == [{"label": "Staff", "kind": "customer_match", "detail": "Customer list", "account": "Acme Landscaping", "campaign": "Leads", "adGroup": "Both",
                     "level": "ad_group"}]


def test_an_ad_set_with_no_results_in_the_range_is_not_a_row():
    raw = raw_of(adsets=[adset("1", "Quiet", custom=[("10", "Site")])], insights=[], audiences=[audience("10", "Site")])
    assert raw["audiences"] == {} and raw["lists"][0]["used"] is True                         # the audience exists and is attached, it just spent nothing


# ── Parts that fail ─────────────────────────────────────────────────────────

def test_the_audience_lists_failing_is_a_warning_and_the_audiences_are_named_from_the_ad_sets():
    raw = raw_of(fail={"audiences"}, **{k: v for k, v in two_sets().items() if k != "audiences"})
    assert len(raw["warnings"]) == 1 and "audience lists could not be read" in raw["warnings"][0] and raw["lists"] == []
    assert raw["audiences"]["act_5550001|audience:10"]["label"] == "Site"                    # no list to name it from: the ad set's own targeting names it
    assert raw["audiences"]["act_5550001|audience:10"]["kind"] == "custom"                   # and its kind is unknown, so a plain custom audience


def test_the_ad_sets_or_their_results_failing_is_raised():
    for part in ("adsets", "insights"):
        with pytest.raises(gads.GadsError):
            ma.meta_raw(ACCT, START, END, store=FakeStore(), fetch=graph(fail={part}, **two_sets()))


def test_meta_asking_to_wait_stops_the_read_and_is_not_a_warning():
    def fetch(path, params):
        if path.endswith("/customaudiences"):
            raise paid_meta.OverQuota("Meta is rate limiting this app right now.")
        return graph(**two_sets())(path, params)
    with pytest.raises(gads.GadsError):
        ma.meta_raw(ACCT, START, END, store=FakeStore(), fetch=fetch)


# ── The report ──────────────────────────────────────────────────────────────

@pytest.fixture
def meta_on(monkeypatch):
    monkeypatch.setenv("META_ADS_ACCESS_TOKEN", "x")
    monkeypatch.setattr(paid_meta, "account_by_id", lambda store, act: dict(ACCT))
    paid_geo.clear_cache()
    base.reset_state()
    base.forget("")


def report(**kw):
    return a.report_for("act_5550001", "2026-06-01", "2026-06-30", store=FakeStore(), meta_fetch=graph(**two_sets()), **kw)


def test_a_meta_account_gets_a_report_in_the_same_shape_with_its_own_kinds(meta_on):
    rep = report()
    assert rep["account"]["platform"] == "meta" and rep["types"] == ["Meta Leads"]
    labels = [r["label"] for r in rep["views"]["All"]["rows"]]
    assert labels[0] == "Site visitors 30d" and set(labels) == {"Site visitors 30d", "Gardening", "Customer list"}
    assert {k["key"] for k in rep["kinds"]} >= {"interest", "behavior", "remarketing"} and all(r["platform"] == "meta" for r in rep["views"]["All"]["rows"])


def test_meta_advice_never_offers_a_bid_adjustment_and_says_how_the_numbers_are_made(meta_on):
    rep = report()
    texts = " ".join(r["action"]["text"] for r in rep["views"]["All"]["rows"]) + " ".join(i["action"] + i["headline"] for i in rep["views"]["All"]["insights"])
    assert "bid adjustment" not in texts.lower() and "Smart Bidding" not in texts
    kinds = [i["kind"] for i in rep["views"]["All"]["insights"]]
    assert "meta" in kinds and "pmax" not in kinds and all(not r["adjustable"] for r in rep["views"]["All"]["rows"])
    assert all(r["bid"] is None for r in rep["views"]["All"]["rows"])


def test_a_meta_audience_list_that_is_too_small_or_not_ready_is_said_so(meta_on):
    lists = {l["id"]: l for l in report()["lists"]}
    assert lists["11"]["serves"] is False and "Meta estimates 400 people" in lists["11"]["problems"][0]
    assert lists["10"]["serves"] is True and lists["10"]["problems"] == []
    weak = ins.judge_lists([{"platform": "meta", "status": "NOT_READY", "why": "Too small", "sizeSearch": None, "sizeDisplay": None, "used": True, "label": "X"}])[0]
    assert weak["problems"] == ["Meta says the audience is not ready: Too small."]


def test_a_second_ask_does_not_read_meta_again_and_refresh_does(meta_on):
    calls = []
    fetch = graph(**two_sets())

    def counting(path, params):
        calls.append(path)
        return fetch(path, params)
    store = FakeStore()
    a.report_for("act_5550001", "2026-06-01", "2026-06-30", store=store, meta_fetch=counting)
    n = len(calls)
    a.report_for("act_5550001", "2026-06-01", "2026-06-30", store=store, meta_fetch=counting)
    assert len(calls) == n


def test_a_meta_account_the_token_cannot_read_is_refused(monkeypatch):
    monkeypatch.setenv("META_ADS_ACCESS_TOKEN", "x")
    monkeypatch.setattr(paid_meta, "account_by_id", lambda store, act: None)
    with pytest.raises(a.BadRequest):
        a.report_for("act_9999999", "2026-06-01", "2026-06-30", store=FakeStore(), meta_fetch=graph(**two_sets()))


# ── A Google and a Meta account together ────────────────────────────────────

GOOGLE = {"id": "1234567890", "name": "Acme Landscaping", "currency": "USD", "timeZone": "America/New_York"}


def google_search(cid, q):
    from tests.test_paid_audiences import AG, INTERESTS, LISTS, SETTINGS, TOTALS, searcher
    return searcher(ag=AG, settings=SETTINGS, lists=LISTS, interests=INTERESTS, totals=TOTALS)(cid, q)


def both(**kw):
    return a.report_for("1234567890+act_5550001", "2026-06-01", "2026-06-30", store=FakeStore(), accounts_fn=lambda: [GOOGLE], groups_fn=lambda: [],
                        search_fn=google_search, meta_fetch=graph(**two_sets()), **kw)


def test_a_google_and_a_meta_account_are_one_report_with_each_audience_on_its_own_platform(meta_on):
    rep = both()
    rows = rep["views"]["All"]["rows"]
    assert rep["account"]["platform"] == "both" and {r["platform"] for r in rows} == {"google", "meta"}
    assert any(r["label"] == "Site visitors 30d" for r in rows) and any(r["label"] == "CRM list" for r in rows)
    kinds = [i["kind"] for i in rep["views"]["All"]["insights"]]
    assert "meta" in kinds and "pmax" in kinds                                              # both notes: each platform's own caveat
    google_row = next(r for r in rows if r["platform"] == "google" and r["adjustable"])
    assert "bid adjustment" in google_row["action"]["text"]                                  # Google's advice is still Google's


def test_the_two_must_bill_in_the_same_currency(meta_on, monkeypatch):
    monkeypatch.setattr(paid_meta, "account_by_id", lambda store, act: dict(ACCT, currency="CAD"))
    with pytest.raises(a.BadRequest, match="different currencies"):
        both()


def test_if_one_platform_fails_the_other_is_shown_with_a_warning(meta_on):
    def broken(path, params):
        raise base.AdsError("Meta answered 500: down")
    rep = a.report_for("1234567890+act_5550001", "2026-06-01", "2026-06-30", store=FakeStore(), accounts_fn=lambda: [GOOGLE], groups_fn=lambda: [],
                       search_fn=google_search, meta_fetch=broken)
    assert rep["account"]["platform"] == "google" and any("Meta Ads could not be read" in w and "Showing the other platform only" in w for w in rep["warnings"])


def test_a_combined_account_is_one_google_and_one_meta(meta_on):
    for bad in ("act_1+act_2", "1234567890+1234567891", "1234567890+act_5550001+act_2"):
        with pytest.raises(a.BadRequest):
            a.report_for(bad, "2026-06-01", "2026-06-30", store=FakeStore(), accounts_fn=lambda: [GOOGLE], groups_fn=lambda: [])


def test_google_and_meta_added_are_judged_on_cost_per_conversion_not_on_a_return_only_google_has(meta_on):
    """Google's results carry value and Meta's lead results do not: judged on ROAS every Meta audience was "Weak, index 0", whatever it cost per lead."""
    every = both()["views"]["All"]
    assert every["basis"] == "cpa"
    meta_rows = [r for r in every["rows"] if r["platform"] == "meta"]
    site = next(r for r in meta_rows if r["label"] == "Site visitors 30d")
    assert site["verdict"] in ("star", "good") and site["index"] > 100                       # $12.50 a lead against a $16.67 average
    assert all(r["index"] != 0 for r in meta_rows if r["conversions"] >= 5)
    google_only = a.report_for("1234567890", "2026-06-01", "2026-06-30", store=FakeStore(), accounts_fn=lambda: [GOOGLE], groups_fn=lambda: [], search_fn=google_search)
    assert google_only["views"]["All"]["basis"] == "roas"                                     # Google alone is judged as before


def sale_row(adset, name, spend, purchases, value, campaign="C2", camp_name="Sales"):
    return {"adset_id": adset, "adset_name": name, "campaign_id": campaign, "campaign_name": camp_name, "objective": "OUTCOME_SALES", "spend": str(spend),
            "impressions": "30000", "inline_link_clicks": "1200", "actions": [{"action_type": "purchase", "value": str(purchases)}],
            "action_values": [{"action_type": "purchase", "value": str(value)}]}


def test_a_meta_account_that_mixes_sales_and_leads_does_not_rank_its_audiences_on_one_measure(meta_on):
    sets = two_sets()
    sets["adsets"] = sets["adsets"] + [adset("4", "Buyers", custom=[("10", "Site")], campaign="C2")]
    sets["insights"] = sets["insights"] + [sale_row("4", "Buyers", 400, 20, 4000)]
    rep = a.report_for("act_5550001", "2026-06-01", "2026-06-30", store=FakeStore(), meta_fetch=graph(**sets))
    every = rep["views"]["All"]
    kinds = [i["kind"] for i in every["insights"]]
    assert {r["verdict"] for r in every["rows"]} == {"none"} and "mixed" in kinds and not {"push", "trim", "exclude"} & set(kinds)
    assert all(r["adjustment"] is None for r in every["rows"]) and every["basis"] is None
    assert "Meta Sales" in rep["types"] and rep["views"]["Meta Sales"]["basis"] == "roas" and rep["views"]["Meta Leads"]["basis"] == "cpa"      # inside one type they are judged


def test_audiences_that_exactly_the_same_ad_sets_use_are_one_row_because_meta_cannot_tell_them_apart():
    """Live, every ad set of an account aimed at the same seven audiences: seven rows with identical numbers, adding up to 373% of the account."""
    bundle = [("10", "Page visitors"), ("11", "Followers"), ("12", "Video viewers")]
    sets = dict(adsets=[adset("1", "A", custom=bundle + [("13", "Only A")]), adset("2", "B", custom=bundle)],
                insights=[lead_row("1", "A", 300, 30), lead_row("2", "B", 200, 20)],
                audiences=[audience("10", "Page visitors"), audience("11", "Followers", "ENGAGEMENT"), audience("12", "Video viewers", "VIDEO"), audience("13", "Only A", "CUSTOM")])
    raw = raw_of(**sets)
    keys = sorted(raw["audiences"])
    assert keys == ["act_5550001|audience:13", "act_5550001|group:audience:10+audience:11+audience:12"]
    g = raw["audiences"]["act_5550001|group:audience:10+audience:11+audience:12"]
    assert g["label"] == "Page visitors + Followers + Video viewers" and g["members"] == ["Page visitors", "Followers", "Video viewers"]
    assert g["detail"] == "3 audiences always used together" and g["kind"] == "remarketing" and len(g["uses"]) == 2
    assert g["all"]["cost"] == 500.0 and g["all"]["conv"] == 50.0                            # each ad set counted once, not three times
    assert raw["audiences"]["act_5550001|audience:13"]["all"]["cost"] == 300.0 and "members" not in raw["audiences"]["act_5550001|audience:13"]
    assert all(l["used"] for l in raw["lists"])                                              # every audience is still listed and in use


def test_a_joined_row_is_shown_with_its_members_and_the_note_says_why(meta_on):
    bundle = [("10", "Page visitors"), ("11", "Followers")]
    sets = dict(adsets=[adset("1", "A", custom=bundle), adset("2", "B", custom=bundle)], insights=[lead_row("1", "A", 300, 30), lead_row("2", "B", 200, 20)],
                audiences=[audience("10", "Page visitors"), audience("11", "Followers")])
    rep = a.report_for("act_5550001", "2026-06-01", "2026-06-30", store=FakeStore(), meta_fetch=graph(**sets))
    rows = rep["views"]["All"]["rows"]
    assert len(rows) == 1 and rows[0]["members"] == ["Page visitors", "Followers"] and rows[0]["cost"] == 500.0
    note = next(i for i in rep["views"]["All"]["insights"] if i["kind"] == "meta")
    assert "1 of these rows join audiences that exactly the same ad sets use" in note["evidence"]
