"""Keyword Research with a Meta ad account (tracker/paid_keywords_job.py): a Meta build, both platforms in one build, one failing without losing the other, and the resume."""
import json

from tests import keyword_fakes as K, strategy_fakes as F
from tests.test_paid_keywords_meta import ACCT, pages
from tests.test_paid_meta_interests import getter
from tracker import paid_keywords as pk, paid_keywords_job as job

SEEDS = {"phrases": ["insulin pump", "diabetes"], "behaviorWords": [], "avoid": [], "assumptions": ["leads are the goal"]}


def meta_model(calls=None, seeds=None, rows=None, judge_fails=False):
    google = K.model()

    def call(system, user, schema, *, max_tokens, stage):
        if calls is not None:
            calls.append(stage)
        if stage == "seeds":
            return dict(seeds or SEEDS)
        if stage == "audiences":
            items = json.loads(user)["interests"]
            r = rows if rows is not None else [{"id": c["id"], "fit": "no" if c["name"] == "Diabetes" else "yes", "intent": "core", "theme": "Pump users", "note": ""} for c in items]
            ids = [c["id"] for c in items if c["name"] != "Diabetes"][:3]
            return {"rows": r, "audiences": [{"name": "Pump users", "role": "prospecting", "interestIds": ids, "why": "Close to the product.", "test": "Cost per lead."}],
                    "exclusions": [], "lookalikes": [{"seed": "Form fills", "why": "Closest to buyers."}], "summary": ["The list is wide."],
                    "quickWins": [{"what": "Start Pump users", "why": "Not aimed at yet"}], "questions": [{"area": "Compliance", "question": "May we name the pump?", "why": "Rules", "urgency": "before launch"}],
                    "risks": [], "assumptions": []}
        if stage == "judge":
            if judge_fails:
                raise RuntimeError("model down")
            return {"rows": [{"id": c["id"], "fit": "yes", "intent": "related", "theme": "Pump users", "note": "judged on the second ask"} for c in json.loads(user)["interests"]]}
        return google(system, user, schema, max_tokens=max_tokens, stage=stage)
    return call


def deps(call=None, post=None, meta_get=None, fail_pages=(), **kw):
    kw.setdefault("meta_account", ACCT)
    kw.setdefault("meta_pages", pages(fail=fail_pages))
    kw.setdefault("meta_get", meta_get or getter())
    return K.deps(call=call or meta_model(), post=post, **kw)


def make(platform="meta", store=None, **kw):
    store = store or F.FakeStrategyStore()
    inp = K.clean(platform=platform, metaAccount="act_1234567", **kw)
    rec = store.create(by="ana@position2.com", inputs=inp, account_name="Acme", steps=pk.steps_for(inp))
    store.begin(rec["id"])
    return store, rec["id"]


def run(store, sid, d=None, resume=False):
    job.run_build(sid, store=store, deps=d or deps(), resume=resume)
    return store.get(sid)


def states(rec):
    return {s["key"]: s["state"] for s in rec["progress"]["steps"]}


def test_a_meta_build_reads_the_ad_account_looks_up_the_interests_judges_them_and_never_asks_keyword_planner():
    asked = []
    store, sid = make()
    rec = run(store, sid, deps(post=lambda path, body: asked.append(path) or {}))
    assert rec["status"] == "done" and rec["error"] == "" and asked == []
    assert states(rec) == {"r:business": "done", "r:website": "done", "r:metaaccount": "done", "r:competitors": "done", "a:seeds": "done", "r:interests": "done", "a:audiences": "done", "f:finish": "done"}
    assert {k: v["state"] for k, v in rec["research"].items()} == {"business": "ok", "website": "ok", "metaaccount": "ok", "competitors": "ok", "interests": "ok"}
    p = rec["plan"]
    assert p["generatedAt"] and "keywords" not in p and p["errors"] == {} and p["currency"] == "USD"
    m = p["meta"]
    assert m["account"]["id"] == "act_1234567" and m["coverage"]["judged"] == len(m["interests"]) and m["coverage"]["unjudged"] == 0
    assert [a["name"] for a in m["audiences"]] == ["Pump users"] and m["plan"]["lookalikes"][0]["seed"] == "Form fills" and m["headlines"] and m["plans"]["start"]
    assert m["coverage"]["misfitSpend"] == 600.0 and [u["id"] for u in m["inUse"] if u["fit"] == "no"] == ["3"]
    assert rec["progress"]["steps"][-1]["note"] == "%d interests judged, 1 audiences to test" % m["coverage"]["judged"]


def test_a_build_for_both_platforms_has_the_google_result_and_the_meta_one():
    store, sid = make("both")
    rec = run(store, sid)
    p = rec["plan"]
    assert rec["status"] == "done" and p["keywords"] and p["themes"] and p["meta"]["audiences"] and p["errors"] == {}
    assert rec["progress"]["steps"][-1]["note"].count(";") == 1 and "keywords judged" in rec["progress"]["steps"][-1]["note"] and "audiences to test" in rec["progress"]["steps"][-1]["note"]


def test_one_platform_failing_is_said_and_the_other_is_kept():
    def no_planner(path, body):
        raise RuntimeError("Google refused")
    store, sid = make("both")
    rec = run(store, sid, deps(post=no_planner))
    p = rec["plan"]
    assert rec["status"] == "done" and "keywords" not in p and p["meta"]["audiences"] and list(p["errors"]) == ["Google"] and "Keyword Planner gave no ideas" in p["errors"]["Google"]
    assert "The Google part could not be written" in rec["note"] and "build again" in rec["note"]
    store, sid = make("both")
    rec = run(store, sid, deps(meta_get=lambda path, params: []))
    assert rec["status"] == "done" and rec["plan"]["keywords"] and list(rec["plan"]["errors"]) == ["Meta"] and "Meta has no interest" in rec["plan"]["errors"]["Meta"] and "meta" not in rec["plan"]


def test_when_the_only_platform_asked_for_fails_the_build_fails_with_the_reason_and_both_failing_does_too():
    store, sid = make("meta")
    rec = run(store, sid, deps(meta_get=lambda path, params: []))
    assert rec["status"] == "error" and rec["error"].startswith("Meta has no interest") and "health conditions" in rec["error"] and states(rec)["a:audiences"] == "pending"

    def no_planner(path, body):
        raise RuntimeError("Google refused")
    store, sid = make("both")
    rec = run(store, sid, deps(post=no_planner, meta_get=lambda path, params: []))
    assert rec["status"] == "error" and "Google:" in rec["error"] and "Meta:" in rec["error"]


def test_a_build_resumed_after_the_seeds_and_the_interests_asks_only_for_the_judging():
    calls = []
    store, sid = make()
    run(store, sid, deps(call=meta_model(calls)))
    assert calls.count("seeds") == 1 and calls.count("audiences") == 1
    calls.clear()
    rec = store.get(sid)
    rec["plan"].pop("metaAudiences")                                    # the judging is what was lost
    store.save_plan(sid, rec["plan"])
    again = run(store, sid, deps(call=meta_model(calls), meta_get=lambda path, params: (_ for _ in ()).throw(AssertionError("Meta was asked again"))), resume=True)
    assert calls == ["audiences"] and again["status"] == "done" and again["plan"]["meta"]["audiences"]


def test_the_judgement_that_left_interests_out_is_asked_about_once_more():
    calls = []
    only_one = [{"id": "1", "fit": "yes", "intent": "core", "theme": "Pump users", "note": ""}]
    store, sid = make()
    rec = run(store, sid, deps(call=meta_model(calls, rows=only_one)))
    m = rec["plan"]["meta"]
    assert calls.count("audiences") == 1 and calls.count("judge") == 1 and rec["status"] == "done" and rec["note"] == ""
    assert m["coverage"]["unjudged"] == 0 and [r for r in m["interests"] if r["id"] == "2"][0]["note"] == "judged on the second ask"


def test_interests_still_not_judged_after_asking_again_are_listed_as_not_judged_and_the_note_says_so():
    store, sid = make()
    rec = run(store, sid, deps(call=meta_model(rows=[{"id": "1", "fit": "yes", "intent": "core", "theme": "Pump users", "note": ""}], judge_fails=True)))
    m = rec["plan"]["meta"]
    assert rec["status"] == "done" and m["coverage"]["unjudged"] == m["checks"]["unjudged"] > 0 and "could not be judged" in rec["note"]
    assert {r["fit"] for r in m["interests"] if r["id"] != "1"} == {"unjudged"}
