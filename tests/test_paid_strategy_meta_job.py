"""Strategy & Campaign Book with Meta (tracker/paid_strategy_job.py): a Meta build, a build for both platforms, and what the checks say, on fakes."""
import datetime as dt

from tests import business_fakes as B, strategy_fakes as F
from tests.test_paid_keywords_meta import ACCT as KM_ACCT, pages as km_pages
from tests.test_paid_meta_interests import getter
from tracker import paid_qa, paid_strategy as ps, paid_strategy_job as job


def pages(fail=()):
    base = B.meta_pages()
    adsets = km_pages()

    def fn(path, params):
        if path.endswith("/adsets") or (path.endswith("/insights") and params.get("level") == "adset"):
            return adsets(path, params)
        return base(path, params)
    return fn


def start(**kw):
    store = F.FakeStrategyStore()
    base = dict(F.inputs(), metaAccount="act_1234567", platform="meta", account="", campaignTypes=[], services="insulin pump, diabetes")
    base.update(kw)
    rec = store.create(by="ana@position2.com", inputs=ps.clean_inputs(base, today=dt.date(2026, 10, 16)), account_name="Acme Meta")
    assert store.begin(rec["id"])
    return store, rec["id"]


def run(store, sid, resume=False, **dkw):
    dkw.setdefault("meta_account", KM_ACCT)
    dkw.setdefault("meta_pages", pages())
    dkw.setdefault("meta_get", getter())
    job.run_build(sid, store=store, deps=F.deps(**dkw), resume=resume)
    return store.get(sid)


def test_a_meta_build_researches_meta_writes_two_campaigns_and_builds_each_with_its_own_book():
    calls = []
    store, sid = start()
    rec = run(store, sid, call=F.model(calls=calls))
    assert rec["status"] == "done" and rec["error"] == ""
    assert set(rec["research"]) == {"website", "competitors", "social", "metahistory", "metaaudience", "market"}
    assert {k: v["state"] for k, v in rec["research"].items()} == {k: "ok" for k in rec["research"]}
    assert sorted(calls) == sorted(["profile", "competitive", "strategy", "architecture:meta", "build:metaleadsimplants", "build:metaleadsretargeting"])
    p = rec["plan"]
    assert [c["name"] for c in p["campaigns"]] == ["P2_MetaLeads_Implants_20261102", "P2_MetaLeads_Retargeting_20261102"]
    assert all(c["platform"] == "meta" and c["build"]["adSets"] and len(c["book"]) == len(paid_qa.applicable([], ["Meta Leads"], "meta")) for c in p["campaigns"])
    assert p["forecast"] is None and {s["state"] for s in rec["progress"]["steps"]} == {"done"}
    assert [s["key"] for s in rec["progress"]["steps"]][-3:] == ["b:metaleadsimplants", "b:metaleadsretargeting", "f:finish"]
    alloc = p["strategy"]["budget"]["allocation"]
    assert round(sum(a["daily"] for a in alloc), 2) == round(6000 / 30.4, 2) and alloc[0]["share"] == 70.0


def test_the_interests_in_a_build_are_looked_up_and_one_meta_does_not_have_is_dropped_and_said():
    store, sid = start()
    rec = run(store, sid)
    first = rec["plan"]["campaigns"][0]["build"]["adSets"][0]
    assert "Made Up Interest" not in first["interests"] and [x["verified"] for x in first["interestsChecked"]] == [True] * len(first["interests"])
    assert rec["plan"]["campaigns"][0]["drops"]["interests"] == 1 and rec["plan"]["checks"]["dropped"]["interests"] == 2         # one in each campaign's first ad set


def test_an_ad_set_that_cannot_reach_the_learning_bar_and_the_cost_of_a_result_it_was_judged_by_are_in_the_checks():
    store, sid = start()
    rec = run(store, sid)
    short = rec["plan"]["checks"]["shortfalls"]
    assert [s for s in short if "Meta wants about 50 a week" in s] and any("last 90 days on Leads campaigns" in s for s in short)
    assert rec["note"].endswith("the writing came up short on: see the checks.") and rec["plan"]["checks"]["learningNotChecked"] == 0


def test_with_no_way_to_ask_meta_the_interests_are_kept_unverified_and_the_check_says_so():
    store, sid = start()
    rec = run(store, sid, meta_get=None)
    first = rec["plan"]["campaigns"][0]["build"]["adSets"][0]
    assert [x["verified"] for x in first["interestsChecked"]] == [False] and first["interests"] == ["Made Up Interest"]            # nothing could be looked up, so the model had no list to choose from
    assert any("could not be asked" in n for n in rec["plan"]["checks"]["interestNotes"])


def test_a_build_for_both_plans_each_platform_with_its_share_of_the_budget_and_keeps_googles_forecast_on_googles_share():
    calls = []
    store, sid = start(platform="both", account="1234567890")
    rec = run(store, sid, call=F.model(calls=calls))
    assert rec["status"] == "done" and set(rec["research"]) >= {"history", "metahistory", "keywords", "metaaudience"}
    assert sorted(c for c in calls if c.startswith("architecture")) == ["architecture", "architecture:meta"]
    camps = rec["plan"]["campaigns"]
    google = [c for c in camps if c["platform"] != "meta"] if all("platform" in c for c in camps) else [c for c in camps if ps.platform_of_kind(c["kind"]) == "google"]
    meta = [c for c in camps if ps.platform_of_kind(c["kind"]) == "meta"]
    assert len(google) == 2 and len(meta) == 2
    assert round(sum(c["daily"] for c in google), 2) == round(3600 / 30.4, 2) and round(sum(c["daily"] for c in meta), 2) == round(2400 / 30.4, 2)
    assert rec["plan"]["forecast"]["monthly"] == 3600.0
    assert {len(c["book"]) for c in google} == {61} and {len(c["book"]) for c in meta} == {37}


def test_a_platform_asked_for_that_got_no_campaign_is_said():
    store, sid = start(platform="both", account="1234567890")
    only_google = F.model(overrides={"architecture:meta": {"campaigns": []}})
    rec = run(store, sid, call=only_google)
    assert rec["status"] == "done" and "No Meta campaign was planned, though Meta was asked for." in " ".join(rec["plan"]["checks"]["shortfalls"])


def test_a_google_build_is_unchanged_and_asks_nothing_of_meta():
    asked = []
    store = F.FakeStrategyStore()
    inp = F.clean()
    rec = store.create(by="ana@position2.com", inputs=inp, account_name="Acme Dental")
    store.begin(rec["id"])
    job.run_build(rec["id"], store=store, deps=F.deps(meta_get=lambda p, q: asked.append(p) or [], meta_pages=lambda p, q: asked.append(p) or []))
    out = store.get(rec["id"])
    assert out["status"] == "done" and asked == [] and "metahistory" not in out["research"] and "metaaudience" not in out["research"]
    assert out["plan"]["checks"]["missing"] == [] and out["plan"]["checks"]["learningNotChecked"] == 0


def test_a_meta_campaign_whose_build_fails_does_not_lose_the_others():
    calls = []
    base = F.model()

    def flaky(system, user, schema, *, max_tokens, stage):
        if stage == "build:metaleadsretargeting":
            raise RuntimeError("model down")
        return base(system, user, schema, max_tokens=max_tokens, stage=stage)
    store, sid = start()
    rec = run(store, sid, call=flaky)
    camps = rec["plan"]["campaigns"]
    assert rec["status"] == "done" and camps[0]["build"]["adSets"] and camps[1]["build"] == {} and "model down" in camps[1]["buildError"] and rec["note"].startswith("1 of 2 campaigns could not be written")


def test_each_meta_campaign_says_how_many_ad_sets_and_ads_it_wrote():
    store, sid = start()
    rec = run(store, sid)
    notes = {s["key"]: s["note"] for s in rec["progress"]["steps"] if s["key"].startswith("b:")}
    assert notes == {"b:metaleadsimplants": "2 ad sets, 6 ads", "b:metaleadsretargeting": "2 ad sets, 6 ads"}
