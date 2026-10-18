"""Ad Copy & Creative Generator: one build from start to finish (tracker/paid_adcopy_job.py), on fakes: a small website, an in-memory store and a model that answers by rule."""
import pytest

from tests import adcopy_fakes as A, strategy_fakes as F
from tracker import paid_adcopy as pa, paid_adcopy_job as job
from tracker.market_radar_llm import ModelError


def run(model=None, store=None, resume=False, sid=None, make=None, **kw):
    store = store or F.FakeStrategyStore()
    if sid is None:
        inputs = A.clean(**(make or {}))
        sid = store.create(by="ana@position2.com", inputs=inputs, account_name="Acme Dental", steps=pa.steps_for(inputs))["id"]
        store.begin(sid)
    model = model or A.Model()
    deps = A.deps(model, **kw)
    job.run_build(sid, store=store, deps=deps, resume=resume)
    return store, sid, store.get(sid), model


def test_a_full_build_writes_every_format_and_checks_it():
    store, sid, rec, model = run()
    assert rec["status"] == "done" and rec["error"] == ""
    plan = rec["plan"]
    assert plan["order"] == ["google_rsa", "meta_feed"] and set(plan["formats"]) == {"google_rsa", "meta_feed"} and plan["model"]
    rsa = plan["formats"]["google_rsa"]
    assert rsa["state"] == "ok" and len(rsa["fields"][0]["items"]) == 15 and rsa["paths"]["path1"] == "Dental-Implants"
    meta = plan["formats"]["meta_feed"]
    assert meta["brief"]["hook"] and meta["specs"] and meta["cta"] == "Book now"
    assert sorted(model.formats()) == ["google_rsa", "meta_feed"]
    steps = {s["key"]: s["state"] for s in rec["progress"]["steps"]}
    assert steps == {"r:website": "done", "r:account": "done", "a:google_rsa": "done", "a:meta_feed": "done", "f:finish": "done"}
    assert set(rec["research"]) == {"website", "account"} and rec["research"]["website"]["state"] in ("ok", "partial")


def test_the_model_is_shown_what_the_research_read():
    _store, _sid, _rec, model = run()
    u = model.calls[0]["user"]
    assert u["clientPages"]["pages"] and "Acme Dental" in u["clientPages"]["pages"][0]["title"]
    assert u["account"]["bestAds"][0]["headlines"][0] == "Dental Implants Austin" and u["account"]["keywordsTheAccountBidsOn"] is not None
    assert u["notAvailable"] == []


def test_the_checks_count_lines_claims_drops_and_keyword_coverage():
    over = A.answer("google_rsa")
    over["headlines"] += [{"text": "X" * 40, "angle": "benefit"}]
    over["headlines"][3] = {"text": "Free Whitening Today", "angle": "offer"}                  # a claim the pages and facts do not hold
    _store, _sid, rec, _m = run(A.Model({"google_rsa": over}))
    c = rec["plan"]["checks"]
    assert c["totals"]["confirm"] >= 1 and any(x["text"] == "Free Whitening Today" and x["claims"] for x in c["claims"])
    assert c["dropped"].get("extra", 0) + c["dropped"].get("long", 0) >= 1
    assert c["keywords"]["covered"] == ["dental implants austin", "invisalign cost"] and c["keywords"]["missing"] == []
    assert c["factsUsed"]["confirmed"] == ["Rated 4.9 stars from 312 reviews."] and c["factsUsed"]["pagesRead"] >= 1
    assert "claim the client has not confirmed" in rec["note"]


def test_a_format_that_stays_short_is_kept_and_said():
    _store, _sid, rec, model = run(A.Model({"google_rsa": A.answer("google_rsa", n=1)}))
    assert rec["status"] == "done"
    res = rec["plan"]["formats"]["google_rsa"]
    assert res["state"] == "short" and rec["plan"]["checks"]["shortfalls"] and "came out short" in rec["note"]
    assert model.formats().count("google_rsa") == 2                                            # asked once more for what was missing


def test_a_format_that_fails_stops_the_build_but_what_was_written_is_kept_and_resume_asks_only_the_rest():
    store, sid, rec, model = run(A.Model({"meta_feed": ModelError("refused", "the model declined")}))
    assert rec["status"] == "error" and "1 of 2 formats could not be written" in rec["error"] and "Meta feed" in rec["error"]
    assert set(rec["plan"]["formats"]) == {"google_rsa"}
    assert {s["key"]: s["state"] for s in rec["progress"]["steps"]}["a:meta_feed"] == "failed"
    store.begin(sid, resume=True)
    _s, _i, rec2, model2 = run(A.Model(), store=store, sid=sid, resume=True)
    assert rec2["status"] == "done" and model2.formats() == ["meta_feed"] and set(rec2["plan"]["formats"]) == {"google_rsa", "meta_feed"}
    assert {s["key"]: s["state"] for s in rec2["progress"]["steps"]}["a:google_rsa"] == "done"
    assert next(s for s in rec2["progress"]["steps"] if s["key"] == "a:google_rsa")["note"].startswith("Kept from the earlier run.")


def test_a_format_that_came_out_short_is_not_asked_again_on_resume():
    store, sid, rec, _m = run(A.Model({"google_rsa": A.answer("google_rsa", n=1), "meta_feed": ModelError("refused", "no")}))
    assert rec["status"] == "error" and rec["plan"]["formats"]["google_rsa"]["state"] == "short"
    store.begin(sid, resume=True)
    _s, _i, rec2, model2 = run(A.Model(), store=store, sid=sid, resume=True)
    assert model2.formats() == ["meta_feed"] and rec2["plan"]["formats"]["google_rsa"]["state"] == "short"


def test_the_website_failing_is_said_and_the_copy_is_still_written():
    _store, _sid, rec, model = run(fetch=F.fetcher(sites={}))
    assert rec["status"] == "done" and rec["research"]["website"]["state"] == "failed"
    assert any("website" in x for x in model.calls[0]["user"]["notAvailable"]) and model.calls[0]["user"]["clientPages"] is None
    assert any("website" in x for x in rec["plan"]["checks"]["missing"])
    assert rec["plan"]["checks"]["factsUsed"]["pagesRead"] == 0


def test_an_unreadable_account_is_said_and_the_copy_is_still_written():
    def boom(*a, **k):
        raise RuntimeError("quota spent")
    _store, _sid, rec, _m = run(ads_report=boom, sqr_read=boom)
    assert rec["status"] == "done" and rec["research"]["account"]["state"] == "failed" and rec["plan"]["checks"]["unreadAccount"] is True
    _store, _sid, rec, _m = run(account=False)
    assert rec["research"]["account"]["state"] == "failed" and "not one this platform can read" in rec["research"]["account"]["note"]


def test_no_account_and_no_website_means_no_research_and_only_writing_steps():
    store, sid, rec, model = run(make={"account": "", "website": "", "formats": ["meta_stories_reels"]})
    assert rec["status"] == "done" and rec["research"] == {} and [s["key"] for s in rec["progress"]["steps"]] == ["a:meta_stories_reels", "f:finish"]
    assert model.calls[0]["user"]["clientPages"] is None and rec["plan"]["checks"]["unreadAccount"] is False


def test_an_account_card_that_could_not_be_read_is_read_again_on_resume():
    calls = []

    def flaky(cid):
        calls.append(cid)
        if len(calls) == 1:
            raise RuntimeError("down")
        return A.ads_report()
    store, sid, rec, _m = run(ads_report=flaky)
    assert rec["research"]["account"]["state"] == "partial" and rec["research"]["account"]["data"]["adsFailed"] is True
    first = len(calls)
    store.begin(sid, resume=True)
    run(store=store, sid=sid, resume=True, ads_report=flaky)
    assert len(calls) == first + 1                                                              # read again, not kept


def test_without_the_ai_the_build_says_so():
    store = F.FakeStrategyStore()
    inputs = A.clean()
    sid = store.create(by="ana@position2.com", inputs=inputs, steps=pa.steps_for(inputs))["id"]
    store.begin(sid)
    deps = A.F.deps(model_ready=False, ads_report=A.ads_report)
    deps.pop("call")
    job.run_build(sid, store=store, deps=deps)
    rec = store.get(sid)
    assert rec["status"] == "error" and "no Anthropic key" in rec["error"]


def test_every_format_can_be_written_in_one_build():
    store, sid, rec, model = run(make={"formats": pa.FORMAT_KEYS})
    assert rec["status"] == "done" and rec["plan"]["order"] == pa.FORMAT_KEYS
    for key, res in rec["plan"]["formats"].items():
        assert res["state"] == "ok", (key, res["shortfalls"])
    assert rec["plan"]["checks"]["totals"]["ready"] > 40
