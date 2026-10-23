"""Business Research: one build from start to finish (tracker/paid_business_job.py), on fakes."""
import json

from tests import business_fakes as B, strategy_fakes as F
from tracker import paid_business as pb, paid_business_job as job

EM, EN = chr(0x2014), chr(0x2013)


def start(store=None, **kw):
    store = store or F.FakeStrategyStore()
    inp = B.clean(**kw)
    rec = store.create(by="ana@position2.com", inputs=inp, account_name="Acme Dental", steps=pb.steps_for(inp))
    assert store.begin(rec["id"])
    return store, rec["id"]


def run(store, sid, resume=False, **dkw):
    job.run_build(sid, store=store, deps=B.deps(**dkw), resume=resume)
    return store.get(sid)


def steps(rec):
    return {s["key"]: s for s in rec["progress"]["steps"]}


def test_a_build_researches_writes_checks_and_saves_everything():
    calls = []
    store, sid = start()
    rec = run(store, sid, call=B.model(calls=calls))
    assert rec["status"] == "done" and rec["error"] == "" and rec["note"] == ""
    assert {k: v["state"] for k, v in rec["research"].items()} == {k: "ok" for k in ("website", "reputation", "social", "history", "competitors", "market")}
    assert [c[0] for c in calls] == ["core", "audience", "implications"]                         # one after the other: each is asked with the answer before it
    assert json.loads(calls[1][1])["firstReader"]["usps"][0]["usp"] == "Free implant consultation"
    assert json.loads(calls[2][1])["audience"]["audiences"][0]["name"] == "Missing teeth" and json.loads(calls[2][1])["firstReader"]["offerings"]
    plan = rec["plan"]
    assert set(plan) == {"core", "audience", "implications", "tech", "claims", "checks", "generatedAt", "model"}
    assert plan["core"]["offerings"][0]["priority"] == "high" and plan["audience"]["audiences"] and plan["implications"]["questions"]
    assert plan["tech"]["pages"] >= 5 and plan["checks"] == {"unconfirmedFacts": 1, "unconfirmedUsps": 1, "missing": []}
    assert [u["strength"] for u in plan["core"]["usps"]] == ["proven", "claimed", "generic"]            # the quote for "Voted best in Texas" is not on the fake site, so it is only a claim
    assert {s["state"] for s in rec["progress"]["steps"]} == {"done"} and rec["cost"]["calls"] == 0
    assert rec["progress"]["steps"][-1]["note"] == "2 offerings, 2 audiences, 3 questions for the client"


def test_no_more_than_three_competitors_are_read():
    fetched = []
    five = [{"name": "Rival %d" % i, "url": "https://rival%d.com/" % i, "why": "nearby"} for i in range(5)]
    run(*start(), fetch=F.fetcher(calls=fetched), ask=B.ask_search(competitors=five))
    hosts = {u.split("/")[2].removeprefix("www.") for u in fetched} - {"acmedental.com"}
    assert len(hosts) == pb.RIVALS_READ == 3 and all(h.startswith("rival") for h in hosts)
    fetched.clear()
    run(*start(competitors=["rival0.com", "rival1.com", "rival2.com"]), fetch=F.fetcher(calls=fetched))
    assert {u.split("/")[2] for u in fetched} - {"acmedental.com", "www.acmedental.com"} == {"rival0.com", "rival1.com", "rival2.com"}


def test_a_claim_not_on_the_pages_or_in_what_the_search_found_is_listed():
    def risky(user):
        raw = json.loads(json.dumps(B.IMPLICATIONS))
        raw["angles"][0]["example"] = "Save 40% on whitening, rated 5 stars by 9,999 patients"
        return raw
    rec = run(*start(), call=B.model({"implications": risky}))
    claims = {c["claim"] for c in rec["plan"]["claims"]}
    assert {"40%", "9,999 patients"} <= claims and all(c["where"] == "Angle" for c in rec["plan"]["claims"])
    assert "claims in the write-up are not on the client's pages" in rec["note"]


def test_a_rating_the_search_found_is_not_a_claim_to_confirm():
    def rated(user):
        raw = json.loads(json.dumps(B.IMPLICATIONS))
        raw["angles"][0]["example"] = "Rated 4.8 stars by 1,204 patients"
        return raw
    rec = run(*start(), call=B.model({"implications": rated}))
    assert rec["plan"]["claims"] == [] and rec["note"] == ""


def test_a_research_card_that_fails_does_not_stop_the_build_and_the_model_is_told():
    calls = []
    rec = run(*start(), call=B.model(calls=calls), fetch=F.fetcher({}), ask=B.ask_search(reputation=None))
    assert rec["status"] == "done" and rec["research"]["website"]["state"] == "failed" and rec["research"]["reputation"]["state"] == "failed"
    assert steps(rec)["r:website"]["state"] == "failed" and steps(rec)["r:reputation"]["state"] == "failed"
    first = json.loads(calls[0][1])
    assert first["website"] is None and first["reputation"] is None and any(s.startswith("website: ") for s in first["notAvailable"]) and any(s.startswith("reputation: ") for s in first["notAvailable"])
    assert "reputation:" in " ".join(rec["plan"]["checks"]["missing"])
    assert rec["plan"]["implications"]["reputation"] == {} and rec["plan"]["implications"]["competitive"] == {} and rec["plan"]["tech"] == {}


def test_a_research_card_that_raises_is_caught():
    def boom(system, user, **kw):
        raise RuntimeError("search is down")
    rec = run(*start(), ask=boom)
    assert rec["status"] == "done" and rec["research"]["reputation"]["state"] == "failed" and "search is down" in rec["research"]["reputation"]["note"]


def test_a_stage_that_fails_stops_the_build_but_keeps_what_was_read_and_resume_carries_on():
    store, sid = start()

    def flaky(system, user, schema, *, max_tokens, stage):
        if stage == "audience":
            raise RuntimeError("the model is down")
        return B.model()(system, user, schema, max_tokens=max_tokens, stage=stage)
    rec = run(store, sid, call=flaky)
    assert rec["status"] == "error" and "the model is down" in rec["error"] and set(rec["research"]) >= {"website", "reputation"}
    assert rec["plan"]["core"] and "audience" not in rec["plan"] and steps(rec)["a:core"]["state"] == "done" and steps(rec)["a:audience"]["state"] == "failed"

    fetched, asked, calls = [], [], []
    assert store.begin(sid, resume=True)
    rec = run(store, sid, resume=True, call=B.model(calls=calls), fetch=F.fetcher(calls=fetched), ask=lambda *a, **k: asked.append(a) or {})
    assert rec["status"] == "done" and [c[0] for c in calls] == ["audience", "implications"] and fetched == [] and asked == []        # nothing was read again
    assert rec["plan"]["audience"]["audiences"] and steps(rec)["a:core"]["note"] == "Kept from the earlier run."


def test_an_empty_answer_is_not_a_stage_done():
    for stage, empty in (("core", {}), ("audience", {"audiences": [], "journey": []}), ("implications", {"summary": [], "questions": []})):
        rec = run(*start(), call=B.model({stage: empty}))
        assert rec["status"] == "error" and "had nothing in it" in rec["error"] and steps(rec)["a:" + stage]["state"] == "failed", stage


def test_a_fresh_build_starts_over():
    store, sid = start()
    run(store, sid)
    assert store.begin(sid)
    assert store.get(sid)["research"] == {} and store.get(sid)["plan"] == {}
    calls = []
    run(store, sid, call=B.model(calls=calls))
    assert [c[0] for c in calls] == ["core", "audience", "implications"]


def test_no_dash_survives_anywhere_in_the_report():
    def dashy(user):
        raw = json.loads(json.dumps(B.IMPLICATIONS))
        raw["summary"][0] = "Acme is a dentist " + EM + " in Austin " + EN + " Texas"
        return raw
    rec = run(*start(), call=B.model({"implications": dashy}))
    text = json.dumps(rec["plan"], ensure_ascii=False) + json.dumps(rec["research"], ensure_ascii=False)
    assert EM not in text and EN not in text and rec["plan"]["implications"]["summary"][0] == "Acme is a dentist, in Austin, Texas"


def test_the_ai_not_being_set_up_is_said_in_words_and_nothing_is_read():
    store, sid = start()
    job.run_build(sid, store=store, deps=lambda: B.deps(call=None, model_ready=False) | {"call": None}, resume=False)
    rec = store.get(sid)
    assert rec["status"] == "error" and "no Anthropic key" in rec["error"] and "the business cannot be written up" in rec["error"] and rec["research"] == {}


def test_the_cost_of_the_writing_and_of_the_paid_research_is_booked():
    store, sid = start()
    rec = run(store, sid)
    assert rec["cost"]["research"] > 0 and rec["cost"]["usd"] == rec["cost"]["research"] + rec["cost"]["model"] and rec["cost"]["calls"] == 0


def test_a_build_that_is_not_there_or_removed_is_left_alone():
    store = F.FakeStrategyStore()
    job.run_build("0" * 32, store=store, deps=B.deps())
    store2, sid = start()
    store2.archive(sid)
    job.run_build(sid, store=store2, deps=B.deps())
    assert store2.get(sid)["status"] == "running"


def test_a_build_without_a_website_or_account_still_reads_the_web_and_writes():
    store, sid = start(website="", account="", brief="They sell implants to families in Austin and want more consultations a month.")
    rec = run(store, sid, account=False)
    assert rec["status"] == "done" and set(rec["research"]) == {"reputation", "social", "competitors", "market"} and rec["plan"]["tech"] == {}


def test_facts_not_on_a_page_are_counted_as_what_they_are():
    def three(user):
        raw = json.loads(json.dumps(B.CORE))
        raw["facts"] = [{"fact": "On a page", "quote": "Free consultation for dental implants", "source": ""},
                        {"fact": "Not on a page", "quote": "Ranked number one in all of Texas", "source": ""}, {"fact": "Not either", "quote": "Awarded the best of the best twice", "source": ""}]
        return raw
    rec = run(*start(), call=B.model({"core": three}))
    assert rec["plan"]["checks"]["unconfirmedFacts"] == 2


def test_a_percentage_the_search_found_is_not_a_claim_to_confirm():
    rep = dict(B.REPUTATION, awards=["Ranked in the top 5% of dentists in Texas by the state dental board"])

    def sounds_like_it(user):
        raw = json.loads(json.dumps(B.IMPLICATIONS))
        raw["angles"][0]["example"] = "Ranked in the top 5% of dentists in Texas"
        return raw
    rec = run(*start(), call=B.model({"implications": sounds_like_it}), ask=B.ask_search(reputation=rep))
    assert "5%" not in [c["claim"] for c in rec["plan"]["claims"]]
    nowhere = run(*start(), call=B.model({"implications": sounds_like_it}), ask=B.ask_search(reputation=dict(B.REPUTATION, awards=[])))
    assert "5%" in [c["claim"] for c in nowhere["plan"]["claims"]]


def test_a_point_backed_only_by_the_search_is_not_counted_as_unconfirmed_and_what_the_short_read_says_is_scanned():
    def answer(user):
        raw = json.loads(json.dumps(B.CORE))
        raw["usps"] = [{"usp": "Award", "why_it_matters": "Trust", "evidence": "Austin Chronicle Best Dentist 2024", "source": "", "strength": "proven"}]
        return raw

    def short(user):
        raw = json.loads(json.dumps(B.IMPLICATIONS))
        raw["summary"][0] = "Save 70% on every treatment"
        return raw
    rec = run(*start(), call=B.model({"core": answer, "implications": short}))
    assert rec["plan"]["checks"]["unconfirmedUsps"] == 0 and rec["plan"]["core"]["usps"][0]["viaSearch"] is True
    assert ("In short", "70%") in [(c["where"], c["claim"]) for c in rec["plan"]["claims"]]
