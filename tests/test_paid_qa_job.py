"""Paid Marketing Agents: QA Agent, one AI review start to finish.

Pinned here: the order (read, rules, AI, combine, save), that progress is reported, that
the checks the exact rules settled are not put to the AI, that every check that fits the
campaign's type is reviewed whatever the client type, that failures are saved on the QC
instead of lost in a thread, and the sentence shown when the AI could only do part of it.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_qa as qa  # noqa: E402
from tracker import paid_qa_job as job  # noqa: E402
from tests.test_paid_qa import (ACCOUNT, CAMPAIGN, DEFAULT_ANSWERS, PAGE, FakeModel, MemoryStore,  # noqa: E402
                                fake_google)


def new_qc(mem):
    launch, _ = mem.start_launch(by="u@position2.com", account_id=ACCOUNT, account_name="Example Co", campaign_id=CAMPAIGN,
                                 campaign_name="Example_Brand_Search", channel="SEARCH", campaign_types=["Search"])
    mem.begin_review(launch["id"])
    return launch["id"]


def pages(urls):
    return [dict(PAGE, url=u) for u in urls[:1]]


def run(mem, lid, model=None, search=None, **kw):
    job.run_review(lid, store=mem, search=search or fake_google(), geo_fn=lambda n: {}, web_fn=kw.pop("web_fn", pages),
                   call=model or FakeModel(DEFAULT_ANSWERS))


def test_a_review_reads_runs_the_rules_asks_the_ai_and_saves_one_finding_per_check():
    mem = MemoryStore()
    lid = new_qc(mem)
    run(mem, lid)
    l = mem.launches[lid]
    assert l["review_status"] == "done" and l["auto_at"] and l["review_error"] == "" and l["review_note"] == ""
    ids = {i["id"] for i in qa.applicable([], ["Search"])}
    assert set(l["auto"]) == ids and len(ids) == 61                                           # every check that fits the campaign
    assert l["auto"]["1.5"]["source"] == "rule" and l["auto"]["1.4"]["source"] == "ai" and l["auto"]["1.4"]["result"] == "fail"
    assert l["auto"]["1.25"]["source"] == "rule" and l["auto"]["1.25"]["result"] == "pass"    # the page rules ran on the fetched page


def test_checks_the_exact_rules_settled_are_not_put_to_the_ai():
    mem = MemoryStore()
    lid = new_qc(mem)
    model = FakeModel(DEFAULT_ANSWERS)
    run(mem, lid, model)
    asked = {c["id"] for req in model.asked for c in req["checks"]}
    saved = mem.launches[lid]["auto"]
    settled = {i for i, f in saved.items() if f["source"] == "rule" and f["result"] in ("pass", "fail")}
    assert settled and not (settled & asked)


def test_every_check_that_fits_the_campaign_type_is_reviewed_and_the_search_only_ones_are_not_asked_of_pmax():
    mem = MemoryStore()
    lid = new_qc(mem)
    model = FakeModel(DEFAULT_ANSWERS)
    run(mem, lid, model)
    asked = {c["id"] for req in model.asked for c in req["checks"]}
    assert {"1.4", "1.6", "3.4", "5.2"} <= asked
    mem2 = MemoryStore()
    launch, _ = mem2.start_launch(by="u", account_id=ACCOUNT, account_name="E", campaign_id=CAMPAIGN, campaign_name="P2_PMax_20261014",
                                  channel="PERFORMANCE_MAX", campaign_types=["PMax"])
    mem2.begin_review(launch["id"])
    model2 = FakeModel(DEFAULT_ANSWERS)
    pm = fake_google(**{"FROM campaign WHERE campaign.id": [{"campaign": {"id": CAMPAIGN, "name": "P2_PMax_20261014", "status": "ENABLED",
                                                                         "advertisingChannelType": "PERFORMANCE_MAX"}}]})
    run(mem2, launch["id"], model2, search=pm)
    asked2 = {c["id"] for req in model2.asked for c in req["checks"]}
    assert not ({"1.3", "1.5", "1.23"} & asked2) and "1.6" in asked2 and not ({"1.3", "1.5", "1.23"} & set(mem2.launches[launch["id"]]["auto"]))


def test_progress_is_saved_as_the_review_goes():
    mem = MemoryStore()
    lid = new_qc(mem)
    seen = []
    orig = mem.set_progress
    mem.set_progress = lambda l, phase, done, total: (seen.append((phase, done, total)), orig(l, phase, done, total))
    run(mem, lid)
    phases = [p for p, _, _ in seen]
    assert phases[0] == "Reading the campaign from Google Ads" and phases[1] == "The AI is reviewing the checklist"
    sections = seen[1][2]
    assert seen[-1] == ("The AI is reviewing the checklist", sections, sections) and sections >= 2


def test_google_ads_failing_is_saved_on_the_qc_not_lost():
    mem = MemoryStore()
    lid = new_qc(mem)

    def refused(cid, gaql):
        from tracker import paid_gads_segments as gads
        raise gads.GadsError("quota", "Google Ads says the daily limit is used up.")
    run(mem, lid, search=refused)
    l = mem.launches[lid]
    assert l["review_status"] == "error" and "daily limit" in l["review_error"] and not l["auto"]


def test_a_campaign_not_in_the_account_is_saved_as_an_error():
    mem = MemoryStore()
    lid = new_qc(mem)
    run(mem, lid, search=fake_google(**{"FROM campaign WHERE campaign.id": []}))
    assert mem.launches[lid]["review_status"] == "error" and "not found in this account" in mem.launches[lid]["review_error"]


def test_landing_pages_failing_does_not_stop_the_review():
    mem = MemoryStore()
    lid = new_qc(mem)

    def down(urls):
        raise RuntimeError("DNS is down")
    run(mem, lid, web_fn=down)
    l = mem.launches[lid]
    assert l["review_status"] == "done" and l["auto"]["1.25"]["result"] is None                # no page, so no tick


def test_a_model_that_fails_for_every_section_is_said_so_and_nothing_passes_on_its_word():
    from tracker.market_radar_llm import ModelError
    mem = MemoryStore()
    lid = new_qc(mem)
    run(mem, lid, FakeModel(boom=ModelError("not_configured", "no key")))
    l = mem.launches[lid]
    assert l["review_status"] == "done" and "no Anthropic key" in l["review_note"]
    assert not [i for i, f in l["auto"].items() if f["source"] == "ai" and f["result"]]


def test_a_model_that_fails_for_some_sections_says_how_many():
    from tracker.market_radar_llm import ModelError
    n = {"i": 0}

    class Some(FakeModel):
        def __call__(self, system, user, *, max_tokens):
            n["i"] += 1
            if n["i"] == 1:
                raise ModelError("truncated", "cut off")
            return super().__call__(system, user, max_tokens=max_tokens)
    mem = MemoryStore()
    lid = new_qc(mem)
    run(mem, lid, Some(DEFAULT_ANSWERS))
    note = mem.launches[lid]["review_note"]
    assert note.startswith("The AI could not review 1 of ") and "truncated (1)" in note


def test_an_archived_qc_is_left_alone():
    mem = MemoryStore()
    lid = new_qc(mem)
    mem.archive_launch(lid)
    run(mem, lid)
    assert mem.launches[lid]["review_status"] == "running" and not mem.launches[lid]["auto"]


def test_the_note_wording():
    assert job._note({}, 10) == ""
    assert "no Anthropic key" in job._note({"not_configured": 10}, 10)
    assert "run the review again" in job._note({"refused": 10}, 10)
    assert job._note({"refused": 2}, 10).startswith("The AI could not review 2 of 10 sections")


# ── A Performance Max campaign is not checked against the campaign book ──────

def pmax_google():
    camp = {"id": CAMPAIGN, "name": "P2_PMax_20261014", "status": "ENABLED", "advertisingChannelType": "PERFORMANCE_MAX",
            "servingStatus": "SERVING", "biddingStrategyType": "MAXIMIZE_CONVERSIONS", "startDateTime": "2026-10-14 09:00:00",
            "geoTargetTypeSetting": {"positiveGeoTargetType": "PRESENCE", "negativeGeoTargetType": "PRESENCE"}}
    return fake_google(**{
        "FROM campaign WHERE campaign.id": [{"campaign": camp, "campaignBudget": {"amountMicros": "30000000"}}],
        "FROM asset_group WHERE": [{"assetGroup": {"id": "7", "name": "Design Build", "adStrength": "AVERAGE", "finalUrls": ["https://example.com/"]}}],
        "FROM asset_group_asset": [],
        "FROM asset_group_signal": [{"assetGroup": {"id": "7", "name": "Design Build"}, "assetGroupSignal": {"searchTheme": {"text": "patio builder"}}}],
        "FROM ad_group": [], "FROM ad_group_ad": [], "FROM ad_group_criterion": []})


def new_pmax_qc(mem):
    launch, _ = mem.start_launch(by="u@position2.com", account_id=ACCOUNT, account_name="Example Co", campaign_id=CAMPAIGN,
                                 campaign_name="P2_PMax_20261014", channel="PERFORMANCE_MAX", campaign_types=["PMax"])
    mem.begin_review(launch["id"])
    mem.set_book(launch["id"], {"filename": "book.xlsx", "rows": [{"field": "Bid Strategy Type", "value": "Manual CPC", "other": ""}], "text": "Bid Strategy Type Manual CPC"})
    return launch["id"]


def test_a_performance_max_review_never_shows_the_ai_the_book_and_asks_it_to_judge_on_the_spot():
    mem = MemoryStore()
    lid = new_pmax_qc(mem)
    model = FakeModel(DEFAULT_ANSWERS)
    run(mem, lid, model=model, search=pmax_google())
    l = mem.launches[lid]
    assert l["review_status"] == "done" and l["channel"] == "PERFORMANCE_MAX" and l["campaign_types"] == ["PMax"]
    asked = {c["id"]: c for req in model.asked for c in req["checks"]}
    assert asked["1.8"]["fromBook"] is False and "no plan to compare with" in asked["1.8"]["judgeOnTheSpot"].lower()
    ev = [req["evidence"] for req in model.asked if "campaignBook" in req["evidence"]]
    assert ev and all("not used for this campaign type (Performance Max)" in e["campaignBook"] for e in ev)
    assert "Manual CPC" not in str(model.asked)                                           # the book's content never reached the model


def test_a_performance_max_review_runs_the_on_the_spot_checks_and_not_the_search_only_ones():
    mem = MemoryStore()
    lid = new_pmax_qc(mem)
    run(mem, lid, search=pmax_google())
    auto = mem.launches[lid]["auto"]
    assert auto["6.1"]["result"] == "fail" and "No audience signal on Design Build" in auto["6.1"]["found"]         # exact: nothing set
    assert auto["6.3"]["result"] == "fail" and "average" in auto["6.3"]["found"]
    assert "6.4" not in auto and not ({"1.3", "1.5", "1.23"} & set(auto))
    assert auto["1.8"]["result"] is None or auto["1.8"]["source"] != "rule"                                         # never ticked from a book
