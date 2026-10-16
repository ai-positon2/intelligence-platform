"""Strategy & Campaign Book: one build from start to finish (tracker/paid_strategy_job.py), on fakes."""
import copy
import json

EM, EN = chr(0x2014), chr(0x2013)

import pytest

from tests import strategy_fakes as F
from tracker import paid_strategy as ps, paid_strategy_job as job


def start(store=None, **kw):
    store = store or F.FakeStrategyStore()
    inp = F.clean(**kw)
    rec = store.create(by="ana@position2.com", inputs=inp, account_name="Acme Dental")
    assert store.begin(rec["id"])
    return store, rec["id"]


def run(store, sid, resume=False, **dkw):
    job.run_build(sid, store=store, deps=F.deps(**dkw), resume=resume)
    return store.get(sid)


def steps(rec):
    return {s["key"]: s for s in rec["progress"]["steps"]}


def test_a_build_researches_writes_checks_and_saves_everything():
    calls = []
    store, sid = start()
    rec = run(store, sid, call=F.model(calls=calls))
    assert rec["status"] == "done" and rec["error"] == "" and rec["note"] == ""
    assert set(rec["research"]) == {"website", "competitors", "social", "history", "keywords", "seasonality", "market"}
    assert {k: v["state"] for k, v in rec["research"].items()} == {k: "ok" for k in rec["research"]}
    assert sorted(calls) == sorted(["profile", "competitive", "strategy", "architecture", "build:searchimplants", "build:pmaxlocal"])
    plan = rec["plan"]
    assert set(plan) >= {"profile", "competitive", "strategy", "architecture", "campaigns", "forecast", "claims", "checks", "generatedAt", "model"}
    assert [c["name"] for c in plan["campaigns"]] == ["P2_Search_Implants_20261102", "P2_PMax_Local_20261102"]
    assert all(len(c["book"]) == 61 for c in plan["campaigns"]) and all(c["build"] for c in plan["campaigns"])
    assert {s["state"] for s in rec["progress"]["steps"]} == {"done"}
    assert [s["key"] for s in rec["progress"]["steps"]][-3:] == ["b:searchimplants", "b:pmaxlocal", "f:finish"]


def test_the_budget_is_allocated_to_the_campaigns_and_adds_up():
    rec = run(*start())
    alloc = rec["plan"]["strategy"]["budget"]["allocation"]
    assert [a["campaign"] for a in alloc] == [c["name"] for c in rec["plan"]["campaigns"]] and round(sum(a["daily"] for a in alloc), 2) == 197.37
    assert round(sum(a["share"] for a in alloc), 1) == 100.0 and alloc[0]["share"] == 70.0


def test_facts_not_on_a_page_and_claims_not_on_a_page_are_listed():
    rec = run(*start())
    checks = rec["plan"]["checks"]
    assert checks["unconfirmedFacts"] == 1 and checks["shortfalls"] == [] and checks["missing"] == []
    assert checks["removedNegatives"][0]["negative"] == "dental implants"
    assert rec["plan"]["claims"] == []            # every number in the fake ads is on the fake site

    def risky(user):
        raw = F.model()("s", user, None, max_tokens=1, stage="build:searchimplants")
        raw["adGroups"][0]["ads"][0]["headlines"][0] = "Best Dentist In Texas"
        raw["adGroups"][0]["ads"][0]["descriptions"][0] = "Save 40% on whitening today and get it free."
        return raw
    rec = run(*start(), call=F.model({"build:searchimplants": risky}))
    claims = {c["claim"] for c in rec["plan"]["claims"]}
    assert {"Best", "40%"} <= claims and any(c["campaign"] == "P2_Search_Implants_20261102" for c in rec["plan"]["claims"])


def test_no_dash_survives_anywhere_in_the_plan():
    def dashy(user):
        raw = F.model()("s", user, None, max_tokens=1, stage="strategy")
        raw["summary"] = ["Win implant patients " + EM + " first", "Then PMax " + EN + " later"]
        return raw
    rec = run(*start(), call=F.model({"strategy": dashy}))
    text = json.dumps(rec["plan"], ensure_ascii=False) + json.dumps(rec["research"], ensure_ascii=False)
    assert EM not in text and EN not in text and rec["plan"]["strategy"]["summary"][0] == "Win implant patients, first"


def test_a_research_card_that_fails_does_not_stop_the_build_and_the_model_is_told():
    seen = []

    def spy(system, user, schema, *, max_tokens, stage):
        seen.append((stage, json.loads(user)))
        return F.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
    rec = run(*start(), call=spy, fetch=F.fetcher({}))             # no site resolves, not even the competitors'
    assert rec["status"] == "done" and rec["research"]["website"]["state"] == "failed" and rec["research"]["competitors"]["state"] == "failed"
    assert steps(rec)["r:website"]["state"] == "failed" and steps(rec)["a:competitive"]["state"] == "skipped"
    profile_ask = dict(seen)["profile"]
    assert profile_ask["website"] is None and any(s.startswith("website: ") for s in profile_ask["notAvailable"])
    assert "website: The home page could not be read" in " ".join(rec["plan"]["checks"]["missing"])
    assert not any(stage == "competitive" for stage, _u in seen)


def test_a_research_card_that_raises_is_caught():
    rec = run(*start(), post=lambda p, b: (_ for _ in ()).throw(RuntimeError("boom")))
    assert rec["status"] == "done" and rec["research"]["keywords"]["state"] == "failed"


def test_a_stage_that_fails_stops_the_build_but_keeps_what_was_read_and_resume_carries_on():
    store, sid = start()
    n = {"profile": 0}

    def flaky(system, user, schema, *, max_tokens, stage):
        if stage == "strategy":
            raise RuntimeError("the model is down")
        return F.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
    rec = run(store, sid, call=flaky)
    assert rec["status"] == "error" and "the model is down" in rec["error"] and set(rec["research"]) >= {"website", "competitors"}
    assert rec["plan"]["profile"] and rec["plan"]["competitive"] and "strategy" not in rec["plan"]
    assert steps(rec)["a:profile"]["state"] == "done"

    fetched = []
    calls = []
    assert store.begin(sid, resume=True)
    rec = run(store, sid, resume=True, call=F.model(calls=calls), fetch=F.fetcher(calls=fetched))
    assert rec["status"] == "done" and "profile" not in calls and "competitive" not in calls and "strategy" in calls
    assert fetched == [] and rec["research"]["website"]["state"] == "ok"                        # nothing was read again
    assert steps(rec)["a:profile"]["state"] == "done" and steps(rec)["a:strategy"]["state"] == "done"


def test_a_fresh_build_starts_over():
    store, sid = start()
    run(store, sid)
    assert store.begin(sid)
    assert store.get(sid)["research"] == {} and store.get(sid)["plan"] == {}
    calls = []
    run(store, sid, call=F.model(calls=calls))
    assert "profile" in calls and "build:searchimplants" in calls


def test_a_campaign_that_cannot_be_written_is_named_and_resume_writes_only_it():
    store, sid = start()

    def breaks(system, user, schema, *, max_tokens, stage):
        if stage == "build:pmaxlocal":
            raise RuntimeError("could not write this one")
        return F.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
    rec = run(store, sid, call=breaks)
    assert rec["status"] == "done" and "1 of 2 campaigns could not be written" in rec["note"]
    c = {c["key"]: c for c in rec["plan"]["campaigns"]}
    assert c["searchimplants"]["build"] and c["pmaxlocal"]["build"] == {} and "could not write" in c["pmaxlocal"]["buildError"]
    assert steps(rec)["b:pmaxlocal"]["state"] == "failed" and rec["plan"]["checks"]["shortfalls"]
    assert len(c["pmaxlocal"]["book"]) == 61                       # the settings are still written, only the ads are missing

    assert store.begin(sid, resume=True)
    calls = []
    rec = run(store, sid, resume=True, call=F.model(calls=calls))
    assert calls == ["build:pmaxlocal"] and rec["note"] == "" and all(c["build"] for c in rec["plan"]["campaigns"])
    assert [c["name"] for c in rec["plan"]["campaigns"]] == ["P2_Search_Implants_20261102", "P2_PMax_Local_20261102"]


def test_a_build_that_came_up_short_is_asked_again_with_what_to_fix():
    store, sid = start()
    asked = []

    def short_then_good(system, user, schema, *, max_tokens, stage):
        if stage == "build:searchimplants":
            asked.append(json.loads(user)["fixTheseFromLastTime"])
            if len(asked) == 1:
                raw = F.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
                raw["adGroups"][0]["ads"][0]["headlines"] = ["Only One"]
                return raw
        return F.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
    rec = run(store, sid, call=short_then_good)
    assert asked[0] is None and "1 headlines" in asked[1][0] and len(asked) == 2
    assert rec["plan"]["checks"]["shortfalls"] == [] and len(rec["plan"]["campaigns"][0]["build"]["adGroups"][0]["ads"][0]["headlines"]) == 15


def test_a_build_that_stays_short_after_the_second_try_says_so():
    def always_short(user):
        raw = F.model()("s", user, None, max_tokens=1, stage="build:searchimplants")
        raw["adGroups"][0]["ads"][0]["headlines"] = ["Only One"]
        return raw
    rec = run(*start(), call=F.model({"build:searchimplants": always_short}))
    assert rec["status"] == "done" and "came up short" in rec["note"] and "needs at least 3" in rec["plan"]["checks"]["shortfalls"][0]
    assert steps(rec)["b:searchimplants"]["state"] == "done" and "headlines" in steps(rec)["b:searchimplants"]["note"]


def test_no_campaign_that_fits_stops_the_build():
    store, sid = start(campaignTypes=["video"])
    rec = run(store, sid)
    assert rec["status"] == "error" and "did not plan any campaign" in rec["error"]


def test_without_a_model_the_build_says_so_before_spending_anything():
    fetched = []
    store, sid = start()
    d = F.deps(fetch=F.fetcher(calls=fetched))
    d.pop("call")
    d["model_ready"] = False
    job.run_build(sid, store=store, deps=d)
    rec = store.get(sid)
    assert rec["status"] == "error" and "no Anthropic key" in rec["error"] and fetched == [] and rec["research"] == {}


def test_social_media_intelligence_is_reused_or_started():
    sci = F.FakeSci(runs={5: {"email": "ana@position2.com", "company_name": "Acme Dental", "company_url": "https://www.acmedental.com/", "status": "done",
                              "updated_at": "2026-10-14T09:00:00+00:00", "synthesis": F.SYNTHESIS}})
    rec = run(*start(), sci=sci)
    assert rec["research"]["social"]["data"]["runId"] == 5 and rec["research"]["social"]["data"]["reused"] is True and sci.started == []
    sci = F.FakeSci(runs={})
    rec = run(*start(), sci=sci)
    assert sci.started == [101] and rec["research"]["social"]["data"]["reused"] is False


def test_a_social_run_still_going_is_not_waited_for_forever_and_is_picked_up_on_resume():
    store, sid = start()
    sci = F.FakeSci(runs={}, finish=False)
    rec = run(store, sid, sci=sci)
    assert rec["status"] == "done" and rec["research"]["social"]["state"] == "partial" and rec["research"]["social"]["data"]["pending"] is True
    sci.runs[101].update(status="done", synthesis=F.SYNTHESIS)
    assert store.begin(sid, resume=True)
    rec = run(store, sid, resume=True, sci=sci)
    assert rec["research"]["social"]["state"] == "ok" and sci.started == [101]               # the same run, not a second one


def test_what_it_cost_is_booked_by_stage():
    cost = job.Cost()
    cost.add("profile", {"usage": {"input_tokens": 10000, "output_tokens": 2000}, "model_served": "claude-sonnet-5-5"})
    cost.add("strategy", {"usage": {"input_tokens": 20000, "output_tokens": 4000}, "model_served": "claude-sonnet-5-5"})
    cost.add("odd", {"usage": {}, "model_served": "model-nobody-priced"})
    total = cost.total({"market": {"usd": 0.12}})
    assert total["calls"] == 3 and total["research"] == 0.12 and total["model"] > 0 and total["usd"] == round(total["model"] + 0.12, 2)
    assert [s["stage"] for s in total["stages"]] == ["profile", "strategy", "odd"] and total["stages"][2]["usd"] == 0.0


def test_the_progress_page_is_never_lost_to_a_failing_store():
    store, sid = start()
    real = store.set_step

    def broken(*a, **k):
        raise RuntimeError("db down")
    store.set_step = broken
    rec = run(store, sid)
    assert rec["status"] == "done"
    store.set_step = real


def test_a_build_that_was_archived_is_not_run():
    store, sid = start()
    store.archive(sid)
    calls = []
    job.run_build(sid, store=store, deps=F.deps(call=F.model(calls=calls)))
    assert calls == [] and store.get(sid)["status"] == "running"


def test_a_failure_to_wire_up_the_dependencies_is_saved_on_the_build():
    store, sid = start()

    def broken():
        raise RuntimeError("Google Ads is not set up on this server")
    job.run_build(sid, store=store, deps=broken)
    rec = store.get(sid)
    assert rec["status"] == "error" and "Google Ads is not set up on this server" in rec["error"]


def test_dependencies_may_be_given_as_a_function():
    store, sid = start()
    rec = run(store, sid)
    assert rec["status"] == "done"
    store2, sid2 = start()
    job.run_build(sid2, store=store2, deps=lambda: F.deps())
    assert store2.get(sid2)["status"] == "done"


def test_an_unexpected_failure_anywhere_is_saved_for_the_page():
    store, sid = start()
    store.get = lambda s: (_ for _ in ()).throw(RuntimeError("store gone"))
    store.fail = lambda s, e, cost=None: store.rows[s].update(status="error", error=e)
    job.run_build(sid, store=store, deps=F.deps())
    assert store.rows[sid]["status"] == "error" and "store gone" in store.rows[sid]["error"]


# ── what the review of the first version found ──────────────────────────────

def test_an_empty_answer_is_not_a_stage_done_and_is_asked_again_on_resume():
    store, sid = start()
    rec = run(store, sid, call=F.model({"architecture": {"campaigns": []}}))
    assert rec["status"] == "error" and "did not plan any campaign that fits" in rec["error"] and "architecture" not in rec["plan"]
    assert store.begin(sid, resume=True)
    calls = []
    rec = run(store, sid, resume=True, call=F.model(calls=calls))
    assert rec["status"] == "done" and calls.count("architecture") == 1 and "profile" not in calls
    store2, sid2 = start()
    rec = run(store2, sid2, call=F.model({"profile": {"summary": "", "offerings": []}}))
    assert rec["status"] == "error" and "answer for the business had nothing in it" in rec["error"] and "profile" not in rec["plan"]
    # a plan saved with an empty architecture by an older run is asked for again
    store3, sid3 = start()
    store3.save_plan_part(sid3, "profile", {"summary": "kept"})
    store3.save_plan_part(sid3, "architecture", {"campaigns": []})
    calls = []
    run(store3, sid3, resume=True, call=F.model(calls=calls))
    assert "architecture" in calls and "profile" not in calls


def test_each_campaign_is_saved_the_moment_it_is_written(monkeypatch):
    monkeypatch.setattr(job, "BUILD_WORKERS", 1)
    store, sid = start()
    seen = {}

    def second(user):
        seen["saved"] = [(c["key"], bool(c.get("build"))) for c in store.rows[sid]["plan"]["campaigns"]]
        return F.model()("s", user, None, max_tokens=1, stage="build:pmaxlocal")
    run(store, sid, call=F.model({"build:pmaxlocal": second}))
    assert seen["saved"] == [("searchimplants", True), ("pmaxlocal", False)]                # the first was already kept when the second began


def test_a_second_try_that_fails_keeps_the_first_build():
    store, sid = start()
    n = []

    def short_then_down(system, user, schema, *, max_tokens, stage):
        if stage == "build:searchimplants":
            n.append(1)
            if len(n) >= 2:
                raise RuntimeError("the model was down")
            raw = F.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
            raw["adGroups"][0]["ads"][0]["headlines"] = ["Only One"]
            return raw
        return F.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
    rec = run(store, sid, call=short_then_down)
    c = rec["plan"]["campaigns"][0]
    assert len(n) == 3 and c["build"] and "buildError" not in c and c["build"]["shortfalls"] and steps(rec)["b:searchimplants"]["state"] == "done"


def test_a_resumed_build_keeps_what_the_first_run_spent():
    store, sid = start()
    store.rows[sid]["cost"] = {"stages": [{"stage": "profile", "model": "claude-sonnet-5-5", "usd": 1.25, "inputTokens": 5, "outputTokens": 5}], "usd": 1.25}
    store.fail(sid, "stopped")
    store.begin(sid, resume=True)
    rec = run(store, sid, resume=True)
    assert rec["cost"]["calls"] == 1 and rec["cost"]["model"] == 1.25 and rec["cost"]["stages"][0]["stage"] == "profile"
    fresh, sid2 = start()
    assert run(fresh, sid2)["cost"]["calls"] == 0


def test_a_social_run_that_is_still_going_is_not_started_a_second_time_on_resume():
    store, sid = start()
    sci = F.FakeSci(runs={}, finish=False)
    run(store, sid, sci=sci)
    run(store, sid, resume=True, sci=sci)
    store.begin(sid, resume=True)
    run(store, sid, resume=True, sci=sci)
    assert list(sci.runs) == [101] and sci.started == [101]


def test_a_running_build_says_it_is_alive_and_stops_when_it_is_done():
    import time
    store, sid = start()

    def slow(system, user, schema, *, max_tokens, stage):
        time.sleep(0.12)
        return F.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
    run(store, sid, call=slow, heartbeat_s=0.02)
    time.sleep(0.08)
    beats = store.beats
    assert beats >= 3
    time.sleep(0.15)
    assert store.beats == beats


def test_a_budget_too_small_for_a_search_campaign_to_learn_is_said():
    rec = run(*start(budget="300"))
    short = rec["plan"]["checks"]["shortfalls"]
    assert any("P2_Search_Implants_20261102 gets" in s and "too few for a Search campaign to learn" in s for s in short)
    assert not any("PMax" in s and "too few" in s for s in short)                      # the rule is for Search
    assert not any("too few" in s for s in run(*start(budget="6000"))["plan"]["checks"]["shortfalls"])
    assert not any("too few" in s for s in run(*start(budget=None))["plan"]["checks"]["shortfalls"])


def test_a_percentage_in_the_ad_that_is_not_on_the_site_is_flagged_even_when_the_number_is():
    def risky(user):
        raw = F.model()("s", user, None, max_tokens=1, stage="build:searchimplants")
        raw["adGroups"][0]["ads"][0]["headlines"][0] = "Save 2000% Today"
        return raw
    rec = run(*start(), call=F.model({"build:searchimplants": risky}))
    assert "2000%" in {c["claim"] for c in rec["plan"]["claims"]}


def test_research_that_was_only_partly_read_is_kept_on_resume_and_not_read_again():
    store, sid = start()
    run(store, sid, call=F.model({"strategy": {"summary": [], "objectives": [], "channels": []}}))                # stops at the strategy
    store.rows[sid]["research"]["competitors"]["state"] = "partial"
    assert store.begin(sid, resume=True)
    fetched = []
    run(store, sid, resume=True, fetch=F.fetcher(calls=fetched))
    assert not any("smileaustin" in u for u in fetched) and store.get(sid)["research"]["competitors"]["state"] == "partial"


def test_a_build_asked_for_more_tries_stops_trying_when_it_has_what_it_needs(monkeypatch):
    monkeypatch.setattr(job, "MAX_REPAIRS", 3)
    n = []

    def short_twice(system, user, schema, *, max_tokens, stage):
        raw = F.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
        if stage == "build:searchimplants":
            n.append(1)
            if len(n) <= 2:
                raw["adGroups"][0]["ads"][0]["headlines"] = ["Only One"]
        return raw
    rec = run(*start(), call=short_twice)
    assert len(n) == 3 and rec["plan"]["checks"]["shortfalls"] == []                     # short, short, right: and no fourth


def test_the_claims_scan_knows_a_number_is_on_the_site_but_not_with_a_percent_sign():
    def risky(user):
        raw = F.model()("s", user, None, max_tokens=1, stage="build:searchimplants")
        raw["adGroups"][0]["ads"][0]["headlines"][0] = "Save 12% Today"                    # the site says "12 to 18 months", never 12%
        return raw
    rec = run(*start(), call=F.model({"build:searchimplants": risky}))
    assert "12%" in {c["claim"] for c in rec["plan"]["claims"]}


def test_a_fresh_build_does_not_inherit_the_steps_of_the_one_before():
    store, sid = start()
    run(store, sid)
    assert all(s["state"] == "done" for s in store.get(sid)["progress"]["steps"])
    assert store.begin(sid)
    rec = run(store, sid, call=F.model({"strategy": {"summary": [], "objectives": [], "channels": []}}))
    got = steps(rec)
    assert got["a:profile"]["state"] == "done" and got["a:strategy"]["state"] == "failed" and "had nothing in it" in got["a:strategy"]["note"] and got["a:architecture"]["state"] == "pending" and got["f:finish"]["state"] == "pending"
    assert not any(k.startswith("b:") for k in got)


def test_campaigns_are_written_at_the_same_time():
    import threading
    gate = threading.Barrier(2, timeout=5)

    def together(system, user, schema, *, max_tokens, stage):
        if stage.startswith("build:"):
            gate.wait()                                         # each build waits for the other: one worker at a time would never get past this
        return F.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
    rec = run(*start(), call=together)
    assert rec["status"] == "done" and all(c["build"] for c in rec["plan"]["campaigns"])


def test_a_second_try_that_is_worse_than_the_first_is_not_kept():
    n = []

    def worse(system, user, schema, *, max_tokens, stage):
        raw = F.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
        if stage == "build:searchimplants":
            n.append(1)
            ad = raw["adGroups"][0]["ads"][0]
            ad["headlines"] = ["Only One"]                                          # the first try: one ad short
            if len(n) > 1:
                raw["adGroups"] = [dict(raw["adGroups"][0], name="Second"), dict(raw["adGroups"][0], name="Third")]      # the second: two ad groups, both short
        return raw
    rec = run(*start(), call=worse)
    built = rec["plan"]["campaigns"][0]["build"]
    assert len(n) == 2 and [g["name"] for g in built["adGroups"]] == ["Implants Core"] and len(built["shortfalls"]) == 1
