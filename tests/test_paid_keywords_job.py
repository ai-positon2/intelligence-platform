"""Keyword Research: one build from start to finish (tracker/paid_keywords_job.py) on stand-ins: the research, the judging in chunks, the resume, and every way it can stop."""
import threading
import time

import pytest

from tests import keyword_fakes as K, strategy_fakes as F
from tracker import paid_keywords as pk, paid_keywords_data as kd, paid_keywords_job as job


def make(store=None, **kw):
    store = store or F.FakeStrategyStore()
    inp = K.clean(**kw)
    rec = store.create(by="ana@position2.com", inputs=inp, account_name="Acme Dental", steps=pk.steps_for(inp))
    store.begin(rec["id"])
    return store, rec["id"]


def run(store, sid, deps=None, resume=False):
    job.run_build(sid, store=store, deps=deps or K.deps(), resume=resume)
    return store.get(sid)


def states(rec):
    return {s["key"]: s["state"] for s in rec["progress"]["steps"]}


# ── the whole build ─────────────────────────────────────────────────────────

def test_a_build_reads_judges_and_saves_everything_and_every_step_is_done():
    store, sid = make()
    rec = run(store, sid)
    assert rec["status"] == "done" and rec["error"] == "" and rec["note"] == ""
    assert states(rec) == {"r:business": "done", "r:website": "done", "r:account": "done", "r:competitors": "done", "r:planner": "done", "a:themes": "done", "a:classify": "done", "a:plan": "done", "f:finish": "done"}
    assert {k: v["state"] for k, v in rec["research"].items()} == {"business": "ok", "website": "ok", "account": "ok", "competitors": "ok", "planner": "ok"}
    p = rec["plan"]
    assert p["generatedAt"] and p["model"] and p["currency"] == "USD" and len(p["keywords"]) == p["coverage"]["judged"] == p["stats"]["judged"]
    assert [t["key"] for t in p["themes"]][0] in ("emergency", "implants", "brand") and p["plan"]["summary"] and p["themeDef"]["themes"]
    assert p["coverage"]["include"] == len(p["editor"]) == 4 and all(r[4] == "Paused" for r in p["editor"])
    assert p["seasonal"]["head"][0] == "Jan 2026" and p["seasonal"]["rows"]
    assert rec["progress"]["steps"][-1]["note"] == "23 keywords judged, 4 to add in 6 themes"


def test_the_cost_of_every_model_call_is_booked(monkeypatch):
    monkeypatch.setattr(kd, "CHUNK", 10)
    cost = job.job.Cost()
    real = K.model()

    def booked(system, user, schema, *, max_tokens, stage):
        cost.add(stage, {"usage": {"input_tokens": 1000, "output_tokens": 500}, "model_served": "claude-sonnet-5-5"})
        return real(system, user, schema, max_tokens=max_tokens, stage=stage)
    store, sid = make()
    rec = run(store, sid, K.deps(call=booked, cost=cost))
    c = rec["cost"]
    assert c["calls"] == 5 and sorted(s["stage"] for s in c["stages"]) == ["classify", "classify", "classify", "plan", "themes"] and c["usd"] == c["model"] > 0 and c["research"] == 0


def test_the_build_asks_the_model_for_themes_then_each_chunk_then_the_plan(monkeypatch):
    monkeypatch.setattr(kd, "CHUNK", 10)
    calls = []
    store, sid = make()
    run(store, sid, K.deps(call=K.model(calls=calls)))
    stages = [s for s, _ in calls]
    assert stages[0] == "themes" and stages[-1] == "plan" and stages.count("classify") == 3 and len(stages) == 5          # 23 keywords in chunks of 10


def test_the_model_is_shown_the_themes_before_it_judges_and_the_judgement_before_it_plans(monkeypatch):
    import json
    calls = []
    store, sid = make()
    run(store, sid, K.deps(call=K.model(calls=calls)))
    by = {s: json.loads(u) for s, u in calls}
    assert [t["key"] for t in by["classify"]["themes"]] == ["implants", "invisalign", "emergency", "family", "brand"]
    cov = by["plan"]["coverage"]
    assert cov["keywordsJudged"] == 23 and cov["keywordsThatFit"] > 0 and by["plan"]["topNewKeywords"] and isinstance(cov["percentOfTheMarketsSearchesTheAccountAlreadyCovers"], int)


def test_a_build_without_a_website_an_account_or_business_research_still_works():
    store, sid = make(website="", account="", research={"business": False, "competitors": False})
    deps = K.deps(account=False)
    rec = run(store, sid, deps)
    assert rec["status"] == "done" and set(rec["research"]) == {"planner"} and states(rec)["r:planner"] == "done"
    assert all(r["status"] == "new" for r in rec["plan"]["keywords"]) and rec["plan"]["coverage"]["pct"] == 0 and rec["plan"]["coverage"]["bidding"] == 0


def test_business_research_of_the_same_client_is_used_and_a_missing_one_is_skipped():
    store, sid = make()
    rec = run(store, sid, K.deps(store=K.Store([])))
    assert rec["status"] == "done" and rec["research"]["business"]["state"] == "skipped" and states(rec)["r:business"] == "skipped"
    store2, sid2 = make()
    rec2 = run(store2, sid2)
    assert rec2["research"]["business"]["state"] == "ok" and rec2["research"]["business"]["data"]["offerings"][0]["name"] == "Dental implants"


def test_a_card_that_fails_is_named_and_the_build_goes_on_without_it():
    store, sid = make()
    rec = run(store, sid, K.deps(sqr_read=K.sqr_read(fail="403 PERMISSION_DENIED")))
    assert rec["status"] == "done" and rec["research"]["account"]["state"] == "failed" and states(rec)["r:account"] == "failed"
    assert all(r["status"] == "new" for r in rec["plan"]["keywords"]) and any("account" in m for m in rec["plan"]["checks"]["missing"])


def test_competitors_are_read_up_to_three_and_planner_is_asked_about_their_sites():
    calls = []
    store, sid = make()
    run(store, sid, K.deps(post=K.planner(calls)))
    assert any((b.get("urlSeed") or {}).get("url", "").startswith("https://smileaustin.com") for p, b in calls)


# ── what is kept in the answer ──────────────────────────────────────────────

def test_the_judgement_is_checked_before_it_is_kept():
    def judging(kw):
        j = K.judge(kw)
        return dict(j, fit="superb") if kw == "emergency dentist near me" else j
    store, sid = make()
    rec = run(store, sid, K.deps(call=K.model(judging=judging)))
    row = next(r for r in rec["plan"]["keywords"] if r["kw"] == "emergency dentist near me")
    assert row["fit"] == "maybe" and row["include"] is False


def test_brand_and_competitor_are_set_by_code_even_when_the_model_says_otherwise():
    store, sid = make()
    rec = run(store, sid, K.deps(call=K.model(judging=lambda kw: {"fit": "yes", "intent": "transactional", "theme": "implants", "match": "exact", "priority": "high", "note": ""})))
    by = {r["kw"]: r for r in rec["plan"]["keywords"]}
    assert by["acme dental austin"]["intent"] == "brand" and by["smile austin dentistry"]["intent"] == "competitor" and by["smile austin dentistry"]["include"] is False
    assert "smile austin dentistry" not in [r[2] for r in rec["plan"]["editor"]]


def test_a_negative_that_would_block_a_recommended_keyword_is_left_out_and_said_so():
    store, sid = make()
    rec = run(store, sid)
    plan = rec["plan"]["plan"]
    assert [n["kw"] for n in plan["negatives"]] == ["dental assistant", "how long", "gem"]
    assert {r["kw"] for r in plan["removedNegatives"]} == {"free", "dental implants", "jobs", "dogs"} and rec["plan"]["checks"]["removedNegatives"] == plan["removedNegatives"]


def test_what_the_checks_page_says_about_the_run():
    store, sid = make()
    rec = run(store, sid, K.deps(post=K.planner(empty=("invisalign",))))
    chk = rec["plan"]["checks"]
    assert "invisalign" in chk["emptySeeds"] and chk["unjudged"] == 0 and chk["convertingNotFit"] == [] and chk["blockedInSomeCampaigns"] == 1 and chk["assumptions"] == ["Implants earn the most per patient"]
    assert rec["research"]["planner"]["state"] == "partial" and states(rec)["r:planner"] == "done"


def test_a_converting_search_term_judged_not_a_fit_is_flagged_for_the_client_and_noted():
    store, sid = make()
    rec = run(store, sid, K.deps(call=K.model(judging=lambda kw: dict(K.judge(kw), fit="no") if kw == "cheap tooth pulling" else K.judge(kw))))
    assert rec["plan"]["checks"]["convertingNotFit"] == ["cheap tooth pulling"] and "1 converting search term judged not a fit" in rec["note"] and rec["status"] == "done"


def test_keywords_the_model_left_out_of_an_otherwise_good_answer_are_listed_as_not_judged():
    def drop_some(stage, user):
        return None

    def model(system, user, schema, *, max_tokens, stage):
        r = K.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
        if stage == "classify":
            r["rows"] = [x for x in r["rows"] if x["kw"] not in ("dentist dallas", "tooth gem", "family dentist austin")]
        return r
    store, sid = make()
    rec = run(store, sid, K.deps(call=model))
    assert rec["status"] == "done" and rec["plan"]["checks"]["unjudged"] == 2 and "2 keywords could not be judged" in rec["note"]
    assert {r["kw"] for r in rec["plan"]["keywords"] if r["fit"] == "unjudged"} == {"dentist dallas", "family dentist austin"} and all(not r["include"] for r in rec["plan"]["keywords"] if r["fit"] == "unjudged")


# ── stopping and carrying on ────────────────────────────────────────────────

def test_without_the_ai_the_build_says_so_and_nothing_is_read():
    d = K.deps()
    d.pop("call")
    d["model_ready"] = False
    store, sid = make()
    rec = run(store, sid, d)
    assert rec["status"] == "error" and "no Anthropic key is set" in rec["error"] and rec["research"] == {}


def test_when_planner_gives_nothing_the_build_stops_with_the_reason_and_keeps_the_research():
    store, sid = make()
    rec = run(store, sid, K.deps(post=K.planner(empty=("",))))
    assert rec["status"] == "error" and rec["error"].startswith("Keyword Planner gave no ideas, so there is nothing to judge. ") and "Keyword Planner had no ideas" in rec["error"]
    assert rec["research"]["account"]["state"] == "ok" and rec["research"]["planner"]["state"] == "failed" and states(rec)["r:planner"] == "failed" and states(rec)["a:themes"] == "pending"


def test_planner_failing_does_not_leave_a_list_made_of_only_the_account_s_converting_terms():
    def down(path, body):
        if path.endswith("geoTargetConstants:suggest"):
            return {}
        raise RuntimeError("RESOURCE_EXHAUSTED")
    store, sid = make()
    calls = []
    rec = run(store, sid, K.deps(post=down, call=K.model(calls=calls)))
    assert rec["status"] == "error" and "nothing to judge" in rec["error"] and calls == [] and "plan" not in rec["plan"] and "keywords" not in rec["plan"]


def test_planner_not_asked_at_all_is_also_a_reason_to_stop():
    store, sid = make()
    rec = run(store, sid, K.deps(planner_account=None))
    assert rec["status"] == "error" and "There is no Google Ads account to ask Keyword Planner through" in rec["error"]


def test_when_everything_is_filtered_out_the_build_says_so():
    store, sid = make()
    rec = run(store, sid, K.deps(post=K.planner(results=["free dental implants"]), sqr_read=K.sqr_read(terms=[])))
    assert rec["status"] == "error" and "There are no keywords to judge: every idea Keyword Planner gave was below the smallest search volume" in rec["error"] and rec["research"]["planner"]["state"] == "ok"


def test_a_chunk_that_fails_stops_the_build_after_the_others_are_saved_and_a_resume_asks_only_about_it(monkeypatch):
    import json
    monkeypatch.setattr(kd, "CHUNK", 10)
    healthy_store, healthy = make()
    second_first = run(healthy_store, healthy)["plan"]["keywords"][10]["kw"]                     # the first keyword of the second group of ten
    calls = []

    def flaky(system, user, schema, *, max_tokens, stage):
        calls.append(stage)
        if stage == "classify" and json.loads(user)["keywords"][0]["kw"] == second_first:
            raise RuntimeError("overloaded")                                                       # every try of that group fails
        return K.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
    store, sid = make()
    rec = run(store, sid, K.deps(call=flaky))
    assert rec["status"] == "error" and "1 of 3 groups of keywords could not be judged" in rec["error"] and "group 2 of 3: overloaded" in rec["error"] and "build again to ask only about the rest" in rec["error"]
    assert states(rec)["a:classify"] == "failed" and states(rec)["a:themes"] == "done" and states(rec)["a:plan"] == "pending"
    assert sorted(rec["plan"]["classify"]["parts"]) == ["0", "2"] and rec["plan"]["classify"]["total"] == 3 and rec["plan"]["themeDef"]["themes"] and "plan" not in rec["plan"]
    again = []
    rec2 = run(store, sid, K.deps(call=K.model(calls=again)), resume=True)
    assert rec2["status"] == "done" and [s for s, _ in again] == ["classify", "plan"]                  # only the missing group, then the plan: not the themes, not the others
    assert len(rec2["plan"]["keywords"]) == 23 and rec2["plan"]["checks"]["unjudged"] == 0 and states(rec2)["a:classify"] == "done" and set(rec2["plan"]["classify"]["parts"]) == {"0", "1", "2"}


def test_a_chunk_the_model_answered_for_too_few_of_its_keywords_is_not_kept(monkeypatch):
    monkeypatch.setattr(kd, "CHUNK", 10)
    state = {"n": 0}

    def thin(system, user, schema, *, max_tokens, stage):
        r = K.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
        if stage == "classify":
            state["n"] += 1
            if state["n"] == 1:
                r["rows"] = r["rows"][:3]                                            # 3 of 10: less than 80 percent
        return r
    store, sid = make()
    rec = run(store, sid, K.deps(call=thin))
    assert rec["status"] == "error" and "the answer judged only 3 of 10 keywords" in rec["error"] and len(rec["plan"]["classify"]["parts"]) == 2


def test_a_resume_after_the_judging_was_done_does_not_ask_the_model_for_it_again():
    store, sid = make()
    run(store, sid)
    calls = []
    rec = run(store, sid, K.deps(call=K.model(calls=calls)), resume=True)
    assert rec["status"] == "done" and calls == []                                                   # nothing was left to ask: the themes, every chunk and the plan are kept


def test_a_resume_does_not_read_the_research_again():
    store, sid = make()
    run(store, sid)
    asked = []
    run(store, sid, K.deps(post=K.planner(asked)), resume=True)
    assert asked == []


def test_a_resume_with_different_candidates_judges_them_again_and_writes_the_plan_again():
    store, sid = make()
    run(store, sid)
    rec = store.get(sid)
    sig = rec["plan"]["classify"]["sig"]
    rec["research"]["planner"]["data"]["ideas"] = rec["research"]["planner"]["data"]["ideas"][:-3]
    store.rows[sid]["research"] = rec["research"]
    store.rows[sid]["status"] = "error"
    calls = []
    rec2 = run(store, sid, K.deps(call=K.model(calls=calls)), resume=True)
    assert rec2["status"] == "done" and [s for s, _ in calls] == ["classify", "plan"]                  # themes kept; the judging and the plan made from it are not
    assert rec2["plan"]["classify"]["sig"] != sig


def test_an_empty_themes_answer_stops_the_build_at_that_step_with_a_sentence():
    store, sid = make()
    rec = run(store, sid, K.deps(call=K.model(overrides={"themes": {"themes": [], "assumptions": []}})))
    assert rec["status"] == "error" and "The answer about the themes had nothing in it" in rec["error"] and states(rec)["a:themes"] == "failed" and states(rec)["a:classify"] == "pending"
    assert rec["research"]["planner"]["state"] == "ok"


def test_an_empty_plan_answer_stops_the_build_but_keeps_the_judging():
    store, sid = make()
    rec = run(store, sid, K.deps(call=K.model(overrides={"plan": {}})))
    assert rec["status"] == "error" and "The answer for the plan had nothing in it" in rec["error"] and states(rec)["a:plan"] == "failed" and states(rec)["a:classify"] == "done"
    calls = []
    rec2 = run(store, sid, K.deps(call=K.model(calls=calls)), resume=True)
    assert rec2["status"] == "done" and [s for s, _ in calls] == ["plan"]


def test_a_model_that_raises_stops_the_build_with_its_message():
    def down(system, user, schema, *, max_tokens, stage):
        raise RuntimeError("the model is down")
    store, sid = make()
    rec = run(store, sid, K.deps(call=down))
    assert rec["status"] == "error" and "the model is down" in rec["error"] and states(rec)["a:themes"] == "failed"


def test_the_chunks_are_judged_in_parallel_but_never_more_than_three_at_once(monkeypatch):
    monkeypatch.setattr(kd, "CHUNK", 3)
    live, peak, lock = [0], [0], threading.Lock()

    def slow(system, user, schema, *, max_tokens, stage):
        if stage == "classify":
            with lock:
                live[0] += 1
                peak[0] = max(peak[0], live[0])
            time.sleep(0.05)
            with lock:
                live[0] -= 1
        return K.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
    store, sid = make()
    rec = run(store, sid, K.deps(call=slow))
    assert rec["status"] == "done" and 2 <= peak[0] <= 3 and len(rec["plan"]["classify"]["parts"]) == 8


def test_the_progress_of_the_judging_is_said_as_it_goes(monkeypatch):
    monkeypatch.setattr(kd, "CHUNK", 10)
    store, sid = make()
    notes = []
    original = store.set_step

    def spy(sid_, key, state, note="", phase=None):
        if key == "a:classify":
            notes.append((state, note))
        return original(sid_, key, state, note, phase)
    store.set_step = spy
    run(store, sid)
    assert notes[0][0] == "running" and "0 of 3 groups judged" in notes[0][1] and notes[-1] == ("done", "23 keywords judged in 3 groups") and any("2 of 3 groups judged" in n for _, n in notes)


def test_the_build_beats_its_heart_while_it_runs_and_never_raises():
    store, sid = make()
    rec = run(store, sid, K.deps(heartbeat_s=0.01, call=lambda *a, **k: (time.sleep(0.05), K.model()(*a, **k))[1]))
    assert rec["status"] == "done" and getattr(store, "beats", 0) >= 1
    assert job.run_build("0" * 32, store=store, deps=K.deps()) is None


def test_a_build_that_was_removed_while_it_waited_is_not_run():
    store, sid = make()
    store.archive(sid)
    calls = []
    job.run_build(sid, store=store, deps=K.deps(call=K.model(calls=calls)))
    assert calls == [] and store.get(sid)["status"] == "running"


def test_two_groups_finishing_together_never_leave_the_older_list_of_groups_as_the_last_write(monkeypatch):
    monkeypatch.setattr(kd, "CHUNK", 3)
    store, sid = make()
    original, writes, first = store.save_plan_part, [], [True]

    def slow_first(sid_, key, value):
        if key == "classify":
            if first[0]:
                first[0] = False
                time.sleep(0.15)                                                                   # the first save is slow: a later one would overtake it if saves were not in turn
            original(sid_, key, value)
            writes.append(len(value["parts"]))
        else:
            original(sid_, key, value)
    store.save_plan_part = slow_first
    rec = run(store, sid)
    assert rec["status"] == "done" and writes == sorted(writes) and writes[-1] == 8 and len(rec["plan"]["classify"]["parts"]) == 8


# ── what the mutation tests of this file found missing ─────────────────────

def test_without_competitors_named_the_three_the_search_finds_are_read_and_no_more():
    found = [{"name": "Rival %d" % i, "url": "https://rival%d.example/" % i, "why": "a rival"} for i in range(1, 6)]
    sites = {"acmedental.com": F.PAGES}
    sites.update({"rival%d.example" % i: F.RIVAL_PAGES for i in range(1, 6)})
    store, sid = make(competitors=[])
    rec = run(store, sid, K.deps(ask=F.ask_search(competitors=found), fetch=F.fetcher(sites)))
    assert rec["status"] == "done" and len(rec["research"]["competitors"]["data"]["competitors"]) == 3 and rec["research"]["competitors"]["note"] == "Read 3 of 3 competitor websites."


def test_an_answer_about_a_keyword_that_was_never_asked_is_counted_for_the_checks():
    def model(system, user, schema, *, max_tokens, stage):
        r = K.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
        if stage == "classify":
            r["rows"].append(dict(r["rows"][0], kw="a keyword nobody asked about"))
        return r
    store, sid = make()
    rec = run(store, sid, K.deps(call=model))
    assert rec["status"] == "done" and rec["plan"]["checks"]["droppedAnswers"] == 1 and "a keyword nobody asked about" not in [r["kw"] for r in rec["plan"]["keywords"]]


# ── what the independent review found ───────────────────────────────────────

def test_keywords_the_model_skipped_are_asked_about_again_once_and_the_answers_are_kept(monkeypatch):
    import json
    monkeypatch.setattr(kd, "CHUNK", 10)
    asked, skipped_before = [], set()
    leave_out = {"dentist dallas", "family dentist austin"}

    def model(system, user, schema, *, max_tokens, stage):
        r = K.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
        if stage == "classify":
            names = [k["kw"] for k in json.loads(user)["keywords"]]
            asked.append(names)
            r["rows"] = [x for x in r["rows"] if x["kw"] not in leave_out - skipped_before]            # each is left out the first time it is asked about, answered the second
            skipped_before.update(n for n in names if n in leave_out)
        return r
    store, sid = make()
    rec = run(store, sid, K.deps(call=model))
    steps = {s["key"]: s for s in rec["progress"]["steps"]}
    assert rec["status"] == "done" and rec["plan"]["checks"]["unjudged"] == 0 and steps["a:classify"]["note"] == "23 keywords judged in 3 groups"
    assert len(asked) == 4 and set(asked[-1]) == leave_out and any(k.startswith("r") for k in rec["plan"]["classify"]["parts"])
    assert {r["kw"]: r["fit"] for r in rec["plan"]["keywords"]}["family dentist austin"] == "maybe"


def test_keywords_the_model_keeps_skipping_stay_not_judged_and_the_build_still_finishes(monkeypatch):
    def always(system, user, schema, *, max_tokens, stage):
        r = K.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
        if stage == "classify":
            r["rows"] = [x for x in r["rows"] if x["kw"] != "family dentist austin"]
        return r
    store, sid = make()
    rec = run(store, sid, K.deps(call=always))
    assert rec["status"] == "done" and rec["plan"]["checks"]["unjudged"] == 1 and "1 keyword could not be judged" in rec["note"]
    assert rec["progress"]["steps"][[s["key"] for s in rec["progress"]["steps"]].index("a:classify")]["note"] == "22 keywords judged in 1 groups, 1 could not be judged"


def test_a_repair_that_fails_does_not_stop_the_build():
    calls = {"n": 0}

    def model(system, user, schema, *, max_tokens, stage):
        r = K.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
        if stage == "classify":
            calls["n"] += 1
            if calls["n"] == 1:
                r["rows"] = r["rows"][:-3]                          # the first answer leaves three out
            else:
                raise RuntimeError("overloaded")                   # and asking again for them fails
        return r
    store, sid = make()
    rec = run(store, sid, K.deps(call=model))
    assert rec["status"] == "done" and rec["plan"]["checks"]["unjudged"] == 3


def test_a_resume_asks_again_about_what_is_still_left_out():
    def thin(system, user, schema, *, max_tokens, stage):
        r = K.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
        if stage == "classify":
            r["rows"] = [x for x in r["rows"] if x["kw"] != "family dentist austin"]
        return r
    store, sid = make()
    run(store, sid, K.deps(call=thin))
    store.rows[sid]["status"] = "error"
    calls = []
    rec = run(store, sid, K.deps(call=K.model(calls=calls)), resume=True)
    assert rec["status"] == "done" and [s for s, _ in calls] == ["classify", "plan"] and rec["plan"]["checks"]["unjudged"] == 0         # only that one keyword, then the plan written afresh


def test_an_account_whose_keywords_could_not_be_read_is_read_again_on_a_resume_and_nothing_is_recommended_meanwhile():
    def read(unknown):
        base = K.sqr_read()

        def f(store, account, start, end):
            return dict(base(store, account, start, end), keywords=[] if unknown else K.ACCOUNT_KEYWORDS, keywordsFailed=unknown)
        return f
    store, sid = make()
    rec = run(store, sid, K.deps(sqr_read=read(True)))
    p = rec["plan"]
    assert rec["status"] == "done" and rec["research"]["account"]["state"] == "partial" and rec["research"]["account"]["data"]["keywordsUnknown"] is True
    assert p["editor"] == [] and p["coverage"]["include"] == 0 and p["coverage"]["accountUnread"] is True and not any(r["include"] for r in p["keywords"]) and all(r["status"] == "new" for r in p["keywords"])
    assert "could not be read, so every keyword is shown as new" in p["checks"]["editorWithheld"] and "no keyword is recommended for adding and the Google Ads Editor file was not made" in rec["note"]
    store.rows[sid]["status"] = "error"
    again = run(store, sid, K.deps(sqr_read=read(False)), resume=True)
    assert again["status"] == "done" and again["research"]["account"]["data"]["keywordsUnknown"] is False and again["plan"]["checks"]["editorWithheld"] == "" and len(again["plan"]["editor"]) == 4
    assert [r for r in again["plan"]["keywords"] if r["status"] == "bidding"]


def test_an_account_that_failed_outright_withholds_the_editor_file_too_but_not_when_the_account_was_never_asked_for():
    store, sid = make()
    rec = run(store, sid, K.deps(sqr_read=K.sqr_read(fail="403")))
    assert rec["research"]["account"]["state"] == "failed" and rec["plan"]["editor"] == [] and rec["plan"]["checks"]["editorWithheld"]
    store, sid = make(account="", website="", research={"business": False, "competitors": False})
    rec = run(store, sid, K.deps(account=False))
    assert rec["plan"]["checks"]["editorWithheld"] == "" and len(rec["plan"]["editor"]) > 0                          # no account was asked for: the ideas are new on purpose


def test_a_planner_some_of_whose_requests_did_not_answer_is_asked_again_on_a_resume():
    store, sid = make()
    rec = run(store, sid, K.deps(post=K.planner(fail=("invisalign",))))
    assert rec["research"]["planner"]["state"] == "partial" and any(c["note"].startswith("did not answer") for c in rec["research"]["planner"]["data"]["calls"])
    store.rows[sid]["status"] = "error"
    asked = []
    again = run(store, sid, K.deps(post=K.planner(asked)), resume=True)
    assert sum(1 for p, _ in asked if p.endswith("generateKeywordIdeas")) == 6 and again["research"]["planner"]["state"] == "ok"
    store.rows[sid]["status"] = "error"
    third = []
    run(store, sid, K.deps(post=K.planner(third)), resume=True)
    assert third == []                                                                                                # an empty answer is not a failure to ask: it is kept


def test_the_coverage_the_page_states_is_over_the_whole_market_the_clients_words_reach():
    store, sid = make()
    rec = run(store, sid)
    c = rec["plan"]["coverage"]
    stats = rec["plan"]["stats"]
    assert c["poolPct"] == round(stats["poolCovered"] / stats["poolSearches"] * 100) and c["poolSearches"] == stats["poolSearches"] and c["poolIdeas"] == stats["poolIdeas"] and c["poolIdeas"] > c["judged"] // 2
    judged_only = round(c["coveredSearches"] / c["searches"] * 100)
    assert c["pct"] == judged_only and c["poolPct"] != 0


def test_numbers_the_model_was_not_given_are_listed_for_a_person_to_check_and_noted():
    plan = dict(K.PLAN, summary=["The market is big", "That brings 450 leads a month"])
    store, sid = make()
    rec = run(store, sid, K.deps(call=K.model(overrides={"plan": plan})))
    nums = rec["plan"]["checks"]["numbers"]
    assert [n["number"] for n in nums] == ["450"] and nums[0]["where"] == "In short" and "1 number in the write-up is not in the research: see the checks." in rec["note"]


def test_themes_made_without_the_accounts_campaigns_are_made_again_with_them_and_so_is_what_came_from_them():
    store, sid = make()
    run(store, sid, K.deps(sqr_read=K.sqr_read(fail="403")))
    rec = store.get(sid)
    assert rec["plan"]["themeDef"]["campaignsKnown"] is False
    store.rows[sid]["status"] = "error"
    calls = []
    again = run(store, sid, K.deps(call=K.model(calls=calls)), resume=True)
    assert [s for s, _ in calls] == ["themes", "classify", "plan"] and again["plan"]["themeDef"]["campaignsKnown"] is True
    assert {t["campaign"] for t in again["plan"]["themes"]} >= {"P2_Search_Implants_20250101"}


# ── what the second round of mutation tests found missing ──────────────────

def test_an_account_whose_negatives_alone_could_not_be_read_is_read_again_on_a_resume():
    store, sid = make()
    rec = run(store, sid, K.deps(sqr_read=K.sqr_read(negativesFailed=True)))
    assert rec["research"]["account"]["data"]["negativesUnknown"] is True and rec["research"]["account"]["data"]["keywordsUnknown"] is False and rec["plan"]["checks"]["editorWithheld"] == ""
    store.rows[sid]["status"] = "error"
    seen = []
    again = K.sqr_read()

    def counting(s, a, st, en):
        seen.append(1)
        return again(s, a, st, en)
    run(store, sid, K.deps(sqr_read=counting), resume=True)
    assert seen == [1]


def test_the_keywords_left_out_of_a_big_answer_are_asked_for_in_groups_and_every_group_is_kept(monkeypatch):
    store, sid = make()
    b = job.KeywordBuild(sid, store.get(sid), store, K.deps())
    b.research_all()
    cands, _ = kd.candidates(b.inputs, b.research)
    themes = b.stage("themes", "themeDef", lambda: __import__("tracker.paid_keywords_ai", fromlist=["x"]).write_themes(b.inputs, b.research, cands, b.call))["themes"]
    monkeypatch.setattr(kd, "CHUNK", 2)
    missing, parts = cands[:5], {}
    judged, dropped = b.repair(missing, themes, parts, "sig", 3, {}, 0)
    assert sorted(parts) == ["r0", "r1", "r2"] and set(judged) == {c["kw"] for c in missing} and dropped == 0                      # three groups of two, two and one: none overwrites another
    assert {kw for p in parts.values() for kw in p["judged"]} == {c["kw"] for c in missing} and store.get(sid)["plan"]["classify"]["parts"].keys() == parts.keys()
    again = {"r0": {"judged": {"x": {}}, "dropped": 0}}
    b.repair(missing[:1], themes, again, "sig", 3, {}, 0)
    assert sorted(again) == ["r0", "r1"] and again["r0"]["judged"] == {"x": {}}                                                    # a later repair adds a part and does not replace an earlier one


def test_a_repair_that_judges_nothing_leaves_no_trace_and_the_plan_is_kept():
    state = {"n": 0}

    def model(system, user, schema, *, max_tokens, stage):
        r = K.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
        if stage == "classify":
            state["n"] += 1
            r["rows"] = r["rows"][:-2] if state["n"] == 1 else []
        return r
    store, sid = make()
    rec = run(store, sid, K.deps(call=model))
    assert rec["status"] == "done" and rec["plan"]["checks"]["unjudged"] == 2 and not [k for k in rec["plan"]["classify"]["parts"] if k.startswith("r")]


def test_themes_made_again_take_the_judging_and_the_plan_with_them_even_when_the_keywords_are_the_same():
    store, sid = make()
    only_keywords = dict(terms=[], negatives=[], lists=[])
    run(store, sid, K.deps(sqr_read=K.sqr_read(fail="403")))
    first = store.get(sid)["plan"]["classify"]["sig"]
    store.rows[sid]["status"] = "error"
    calls = []
    again = run(store, sid, K.deps(sqr_read=K.sqr_read(**only_keywords), call=K.model(calls=calls)), resume=True)
    assert again["plan"]["classify"]["sig"] == first and [s for s, _ in calls] == ["themes", "classify", "plan"]


def test_what_the_account_read_warned_of_and_could_not_tell_reaches_the_checks():
    store, sid = make()
    rec = run(store, sid, K.deps(sqr_read=K.sqr_read(warnings=["The account has more keywords than were read."], notes=["Account-level negative keywords are not available."])))
    chk = rec["plan"]["checks"]
    assert chk["accountWarnings"] == ["The account has more keywords than were read."] and chk["caveats"] == ["Account-level negative keywords are not available."]
    assert chk["geography"] == "Searches are counted in Austin,Texas,United States." and rec["plan"]["coverage"]["poolPct"] is not None
