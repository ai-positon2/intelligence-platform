"""One AI review of a Meta campaign start to finish (tracker/paid_qa_job.py on a launch of a Meta ad account), on fakes."""
from tests.test_paid_qa import FakeModel, MemoryStore
from tests.test_paid_qa_meta import ACT, CAMP, adset, ad, world
from tracker import paid_qa as qa, paid_qa_job as job


def new_qc(mem, campaign=CAMP["id"], channel="META_LEADS", types=("Meta Leads",), account=ACT):
    launch, _ = mem.start_launch(by="u@position2.com", account_id=account, account_name="Acme Meta", campaign_id=campaign, campaign_name="P2_MetaLeads_Implants_20261102", channel=channel, campaign_types=list(types))
    mem.begin_review(launch["id"])
    return launch["id"]


def run(mem, lid, model=None, meta="default", **kw):
    get, pages = world(**kw)
    job.run_review(lid, store=mem, search=lambda *a: [], web_fn=lambda urls: [{"url": u, "status": 200} for u in urls], call=model or FakeModel({}),
                   meta={"get_fn": get, "pages_fn": pages} if meta == "default" else meta)


def test_a_review_reads_meta_runs_the_rules_asks_the_ai_and_saves_one_finding_per_check_of_the_meta_checklist():
    mem = MemoryStore()
    lid = new_qc(mem)
    model = FakeModel({"3.2": ("flag", "The primary text of Ad 1 starts with a lower case letter.", "Missing a tooth? Book a visit today", "Start the sentence with a capital.")})
    run(mem, lid, model)
    l = mem.launches[lid]
    ids = {i["id"] for i in qa.applicable([], ["Meta Leads"], "meta")}
    assert l["review_status"] == "done" and l["auto_at"] and l["review_error"] == "" and set(l["auto"]) == ids and len(ids) == 37 and l["channel"] == "META_LEADS" and l["campaign_types"] == ["Meta Leads"]
    assert l["auto"]["1.1"]["source"] == "rule" and l["auto"]["1.1"]["result"] == "pass" and l["auto"]["3.6"]["result"] == "pass"
    assert l["auto"]["3.2"]["source"] == "ai" and l["auto"]["3.2"]["result"] == "fail" and "capital" in l["auto"]["3.2"]["fix"]
    assert l["auto"]["4.1"]["unverified"] is True and l["auto"]["4.1"]["source"] == "ai"                  # no rule and the reviewer cannot see it: a person must look


def test_checks_the_rules_settled_are_not_put_to_the_ai_and_the_reviewer_is_the_meta_one():
    mem = MemoryStore()
    lid = new_qc(mem)
    seen = []

    class Spy(FakeModel):
        def __call__(self, system, user, *, max_tokens):
            seen.append(system)
            return super().__call__(system, user, max_tokens=max_tokens)
    model = Spy({})
    run(mem, lid, model)
    asked = {c["id"] for req in model.asked for c in req["checks"]}
    settled = {i for i, f in mem.launches[lid]["auto"].items() if f["source"] == "rule" and f["result"] in ("pass", "fail")}
    assert settled and not (settled & asked) and {"1.1", "1.4", "1.8", "1.9", "2.6", "3.6", "3.7", "3.9"} <= settled
    assert seen and all("Meta Ads (Facebook and Instagram) specialist" in s and "Google Ads specialist" not in s for s in seen)
    evidence = model.asked[0]["evidence"]
    assert set(evidence) >= {"campaign", "adSets", "ads", "accountSettingsNotVisibleToTheApi"} and evidence["campaign"]["name"] == CAMP["name"]


def test_a_failed_rule_gets_the_steps_to_fix_it_in_ads_manager():
    mem = MemoryStore()
    lid = new_qc(mem)
    run(mem, lid, ads=[ad("1"), ad("2")])
    f = mem.launches[lid]["auto"]["1.8"]
    assert f["result"] == "fail" and f["source"] == "rule" and f["fix"].startswith("In Ads Manager add ads to any ad set with fewer than 3 active ads")
    assert "The checklist expects" in f["fix"]


def test_a_traffic_campaign_is_not_asked_the_conversion_checks():
    mem = MemoryStore()
    lid = new_qc(mem, channel="META_TRAFFIC", types=("Meta Traffic",))
    run(mem, lid, camp={"objective": "OUTCOME_TRAFFIC", "name": "P2_MetaTraffic_X_20261102"}, adsets=[adset(optimization_goal="LINK_CLICKS", destination_type="WEBSITE")])
    got = set(mem.launches[lid]["auto"])
    assert "2.1" in got and "2.3" not in got and "2.4" not in got and "4.1" not in got and mem.launches[lid]["campaign_types"] == ["Meta Traffic"]


def test_the_ai_failing_or_a_read_failing_is_said_in_meta_words_and_nothing_is_ticked_for_it():
    mem = MemoryStore()
    lid = new_qc(mem)
    run(mem, lid, FakeModel({}, boom=RuntimeError("model down")))
    l = mem.launches[lid]
    assert l["review_status"] == "done" and "could not review" in l["review_note"] and "Google Ads" not in l["review_note"]
    assert l["auto"]["1.1"]["result"] == "pass" and l["auto"]["3.2"]["unverified"] is True
    mem2 = MemoryStore()
    lid2 = new_qc(mem2)
    run(mem2, lid2, fail=("adsets",))
    assert "Meta could not be asked for: adsets" in mem2.launches[lid2]["review_note"] and mem2.launches[lid2]["auto"]["2.1"]["result"] is None


def test_a_campaign_that_is_not_in_the_account_a_missing_meta_and_a_crash_are_saved_for_the_page():
    mem = MemoryStore()
    lid = new_qc(mem)
    job.run_review(lid, store=mem, search=lambda *a: [], call=FakeModel({}), meta={"get_fn": lambda p, q: {}, "pages_fn": world()[1]})
    assert mem.launches[lid]["review_status"] == "error" and "was not found in this ad account" in mem.launches[lid]["review_error"]
    mem2 = MemoryStore()
    lid2 = new_qc(mem2)
    run(mem2, lid2, meta=None)
    assert mem2.launches[lid2]["review_status"] == "error" and "Meta is not set up" in mem2.launches[lid2]["review_error"]
    mem3 = MemoryStore()
    lid3 = new_qc(mem3)
    job.run_review(lid3, store=mem3, search=lambda *a: [], call=FakeModel({}), meta={"get_fn": lambda p, q: (_ for _ in ()).throw(RuntimeError("Meta rejected the access token")), "pages_fn": world()[1]})
    assert mem3.launches[lid3]["review_status"] == "error" and "rejected the access token" in mem3.launches[lid3]["review_error"]
