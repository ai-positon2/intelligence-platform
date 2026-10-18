"""Paid Marketing Agents: Strategy & Campaign Book, one build from start to finish.

    research (in parallel lanes)  ->  profile  ->  competitors  ->  strategy  ->  campaigns  ->  one build per campaign (in parallel)  ->  the book

It runs in a background thread (the route starts it), because it reads a website and the competitors', asks Google Ads, may wait on a
Social Media Intelligence run and then asks the model a dozen times: several minutes, far too long for one web request. Its state is
saved in Postgres a piece at a time, so whichever server answers the page's polling can report progress, and a build interrupted by a
deploy is resumed from the pieces it kept instead of started again. Everything it needs comes in through `deps`, so tests run it on fakes.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import paid_strategy as ps, paid_strategy_ai as ai, paid_strategy_book as book, paid_strategy_check as ck, paid_strategy_research as rs

log = logging.getLogger(__name__)

MAX_REPAIRS = 1
BUILD_WORKERS = 3
MODEL_TIMEOUT_S = 600           # a campaign's whole copy deck is a long answer: the shared client's 240 seconds is not enough for it
BUDGET_CLICKS_A_DAY = 10        # what a Search campaign wants to learn from

# What an answer has to hold to be kept (and, on a resume, to be skipped). An empty answer is not a stage done.
USABLE = {
    "profile": lambda x: bool(x.get("summary") or x.get("offerings")),
    "competitive": lambda x: True,                                      # having nothing to say about the competitors is an answer
    "strategy": lambda x: bool(x.get("summary") or x.get("objectives") or x.get("channels")),
    "architecture": lambda x: bool(x.get("campaigns")),
}
EMPTY_ANSWER = {
    "profile": "The strategist's answer for the business had nothing in it. Build again to ask once more.",
    "competitive": "The strategist's answer for the competitors had nothing in it. Build again to ask once more.",
    "strategy": "The strategist's answer for the strategy had nothing in it. Build again to ask once more.",
    "architecture": "The strategist did not plan any campaign that fits the campaign types asked for. Build again, or let it choose the types.",
}


class Cost:
    """What a build spent on the model, stage by stage, and on the research that is paid for beyond it."""

    def __init__(self, prior=None):
        self.stages, self.lock = [dict(s) for s in prior or [] if isinstance(s, dict)], threading.Lock()      # a resumed build keeps what the first run spent

    def add(self, stage, meta):
        from . import market_radar_costs as costs
        usage = (meta or {}).get("usage") or {}
        model = (meta or {}).get("model_served") or (meta or {}).get("model_requested") or ai.MODEL
        try:
            usd, _notes = costs.model_cost(model, usage)
            usd = float(usd)
        except Exception:                                   # noqa: BLE001 (an unpriced model must not stop a build)
            usd = 0.0
        with self.lock:
            self.stages.append({"stage": stage, "model": model, "usd": round(usd, 4), "inputTokens": usage.get("input_tokens", 0), "outputTokens": usage.get("output_tokens", 0)})

    def total(self, research=None):
        extra = sum((pc or {}).get("usd", 0) or 0 for pc in (research or {}).values() if isinstance(pc, dict))
        return {"usd": round(sum(s["usd"] for s in self.stages) + extra, 2), "model": round(sum(s["usd"] for s in self.stages), 2), "research": round(extra, 2),
                "calls": len(self.stages), "stages": list(self.stages)}


def long_client():
    """The model's client, with room for a long answer (the shared one gives up after 240 seconds and asks again, and pays again)."""
    from .market_radar_llm import ModelError
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise ModelError("not_configured", "ANTHROPIC_API_KEY is not set")
    from anthropic import Anthropic
    return Anthropic(api_key=key, timeout=MODEL_TIMEOUT_S, max_retries=1)


def default_call(cost, effort="medium", client=None):
    """How the model is asked: a JSON schema answer through the platform's shared call, its cost booked in `cost`."""
    held = [client]

    def call(system, user, schema, *, max_tokens, stage):
        from .market_radar_llm import ModelError, call_json
        if held[0] is None:
            held[0] = long_client()
        try:
            parsed, meta = call_json(system, user, schema, model=ai.MODEL, max_tokens=max_tokens, stage=stage, effort=effort, client=held[0])
        except Exception as e:                              # noqa: BLE001 (call_json without a ledger run lets the SDK's own errors out unwrapped)
            if isinstance(e, ModelError):
                refused, detail = e.kind == "api_error", e.detail
            else:
                refused, detail = getattr(e, "status_code", None) == 400, "%s: %s" % (type(e).__name__, str(e)[:300])
            if not (refused and ai.schema_refused(detail)):
                raise
            log.warning("strategy: the API refused the schema of %s, asking with the schema in the prompt instead: %s", stage, detail[:200])
            parsed, meta = ai.text_json(system, user, schema, model=ai.MODEL, max_tokens=max_tokens, effort=effort, client=held[0])
        cost.add(stage, meta)
        return parsed
    return call


def _note_of(exc):
    return (getattr(exc, "detail", None) or str(exc) or type(exc).__name__)[:300]


class Build:
    """One build's state while it runs. Not shared between builds. Another agent that researches and writes the same way (Business Research) subclasses
    this: it says which steps it has (`default_steps`) and what makes one of its writing stages usable (`USABLE`, `EMPTY_ANSWER`)."""
    USABLE = USABLE
    EMPTY_ANSWER = EMPTY_ANSWER
    NO_MODEL = "The AI is not available on this server (no Anthropic key is set), so a strategy cannot be written."

    def __init__(self, sid, rec, store, deps, resume=False):
        self.sid, self.rec, self.store, self.deps = sid, rec, store, deps
        self.inputs = rec["inputs"]
        self.research = dict(rec.get("research") or {})
        self.plan = dict(rec.get("plan") or {})
        self.cost = deps.get("cost") or Cost(prior=(rec.get("cost") or {}).get("stages") if resume else None)
        self.built = {}
        self.call = deps.get("call") or default_call(self.cost)
        kept = [dict(s) for s in (rec.get("progress") or {}).get("steps") or []] if resume else []
        self.steps = [dict(s, state=s["state"] if s["state"] in ("done", "skipped") else "pending", note=s["note"] if s["state"] in ("done", "skipped") else "")
                      for s in kept] or self.default_steps()
        self.lock = threading.Lock()
        try:
            store.set_steps(sid, self.steps, phase="Starting")
        except Exception:                                   # noqa: BLE001 (progress is for the page)
            log.exception("strategy %s: could not save the steps", sid)

    def default_steps(self):
        return ps.steps_for(self.inputs)

    # progress -----------------------------------------------------------------
    def step(self, key, state, note="", phase=None):
        with self.lock:
            for s in self.steps:
                if s["key"] == key:
                    s["state"], s["note"] = state, note[:400]
        try:
            self.store.set_step(self.sid, key, state, note, phase)
        except Exception:                                   # noqa: BLE001 (progress is for the page: never lose the build over it)
            log.exception("strategy %s: could not save step %s", self.sid, key)

    def done_before(self, key):
        pc = self.research.get(key)
        return bool(pc and pc.get("state") in ("ok", "partial") and not (pc.get("data") or {}).get("pending"))

    # research -----------------------------------------------------------------
    def piece(self, key, fn):
        """Run one research card: skip it if it was finished before, never let it raise, save what it found."""
        if self.done_before(key):
            self.step("r:" + key, "done", self.research[key].get("note") or "Kept from the earlier run.")
            return self.research[key]
        self.step("r:" + key, "running", "", phase="Researching")
        try:
            pc = fn()
        except Exception as e:                              # noqa: BLE001 (a card that fails says so; the strategy goes on without it)
            log.exception("strategy %s: research %s failed", self.sid, key)
            pc = rs.failed(key, "This could not be read (%s)." % _note_of(e))
        with self.lock:
            self.research[key] = pc
        try:
            self.store.save_research(self.sid, key, pc)
        except Exception:                                   # noqa: BLE001
            log.exception("strategy %s: could not save research %s", self.sid, key)
        state = {"ok": "done", "partial": "done", "skipped": "skipped", "failed": "failed"}[pc["state"]]
        self.step("r:" + key, state, pc.get("note") or "")
        return pc

    def research_all(self):
        d, inp, on = self.deps, self.inputs, self.inputs["research"]
        social_run = None
        if on.get("social"):
            social_run = self.start_social()
        lanes = {}
        with ThreadPoolExecutor(max_workers=6) as ex:
            if on.get("website"):
                lanes["website"] = ex.submit(self.piece, "website", lambda: rs.read_website(inp, fetcher=d.get("fetcher")))
            if on.get("competitors"):
                lanes["competitors"] = ex.submit(self.piece, "competitors", lambda: rs.read_competitors(inp, fetcher=d.get("fetcher"), ask=d.get("ask")))
            if on.get("history"):
                lanes["history"] = ex.submit(self.piece, "history", lambda: rs.read_history(
                    d["account"], search=d["search"], geo_names=d.get("geo_names"), sqr_read=d.get("sqr_read"), store=d.get("gads_store"), cached=d.get("cached"),
                    today=d.get("today")))
            if on.get("seasonality"):
                lanes["seasonality"] = ex.submit(self.piece, "seasonality", lambda: rs.read_seasonality(d["account"], report=d["season_report"]))
            if on.get("market"):
                lanes["market"] = ex.submit(self.piece, "market", lambda: rs.read_market(inp, ask=d.get("ask")))
            if on.get("social"):
                lanes["social"] = ex.submit(self.piece, "social", lambda: self.collect_social(social_run))
            for f in lanes.values():
                f.result()
        if on.get("keywords"):
            self.piece("keywords", lambda: rs.read_keywords(
                inp, post=d["post"], planner_account=d.get("planner_account"), website_piece=self.research.get("website"),
                history_piece=self.research.get("history"), competitor_piece=self.research.get("competitors"),
                currency=((self.research.get("history") or {}).get("data") or {}).get("account", {}).get("currency") or inp.get("currency"),
                planner_currency=d.get("planner_currency")))

    def start_social(self):
        """Find or start the Social Media Intelligence run first, so it works while everything else is read. Returns (run id, reused) or None."""
        if self.done_before("social"):
            return None
        prior = ((self.research.get("social") or {}).get("data") or {})
        if prior.get("pending") and prior.get("runId") is not None:             # the run a first attempt started, still going: not a second paid one
            return prior["runId"], bool(prior.get("reused"))
        d = self.deps
        try:
            run = rs.find_social_run(d["email"], self.inputs, store=d["sci_store"], now=d.get("now"))
            if run:
                return run["id"], True
            run_id = rs.start_social(d["email"], self.inputs, store=d["sci_store"], pipeline=d["sci_pipeline"], spawn=d["spawn"])
            return (run_id, False) if run_id is not None else None
        except Exception:                                   # noqa: BLE001
            log.exception("strategy %s: could not start social", self.sid)
            return None

    def collect_social(self, started):
        if not started:
            return rs.failed("social", "A Social Media Intelligence run could not be found or started.")
        run_id, reused = started
        d = self.deps
        return rs.read_social(d["email"], run_id, store=d["sci_store"], reused=reused, wait_s=d.get("social_wait_s", rs.SOCIAL_WAIT_S),
                              poll_s=d.get("social_poll_s", 10), sleep=d.get("sleep", time.sleep))

    # writing ------------------------------------------------------------------
    def stage(self, key, plan_key, fn):
        """Run one writing stage unless it was finished before. Returns its result; a stage that raises fails the build."""
        usable = self.USABLE[key]
        if self.plan.get(plan_key) and usable(self.plan[plan_key]):
            self.step("a:" + key, "done", "Kept from the earlier run.")
            return self.plan[plan_key]
        self.step("a:" + key, "running", "", phase="Writing")
        try:
            out = fn()
            if not usable(out or {}):
                raise RuntimeError(self.EMPTY_ANSWER[key])
        except Exception as e:                              # noqa: BLE001 (the step must not be left turning for ever on a build that has stopped)
            self.step("a:" + key, "failed", _note_of(e))
            raise
        with self.lock:
            self.plan[plan_key] = out
        self.store.save_plan_part(self.sid, plan_key, out)
        self.step("a:" + key, "done", "")
        return out

    def build_one(self, camp, profile, strategy, competitive):
        key = "b:" + camp["key"]
        if camp.get("build"):
            self.step(key, "done", "Kept from the earlier run.")
            return camp
        self.step(key, "running", "", phase="Building")
        try:
            built, drops = ai.write_build(camp, self.inputs, self.research, profile, strategy, competitive, self.call)
            short = ck.shortfalls(camp["kind"], built)
            for _ in range(MAX_REPAIRS if short else 0):
                try:                                        # a second try that fails must not lose the first, which is usable
                    again, again_drops = ai.write_build(camp, self.inputs, self.research, profile, strategy, competitive, self.call, problems=short)
                except Exception:                           # noqa: BLE001
                    log.warning("strategy %s: the second try of %s failed; keeping the first", self.sid, camp["key"], exc_info=True)
                    break
                if len(ck.shortfalls(camp["kind"], again)) <= len(short):
                    built, drops = again, again_drops
                short = ck.shortfalls(camp["kind"], built)
                if not short:
                    break
            built["shortfalls"] = ck.shortfalls(camp["kind"], built)
            camp = dict(camp, build=built, drops=drops)
            note = "; ".join(built["shortfalls"][:2]) if built["shortfalls"] else self.build_note(camp["kind"], built)
            self.step(key, "done", note)
        except Exception as e:                              # noqa: BLE001 (one campaign failing must not lose the rest)
            log.exception("strategy %s: build of %s failed", self.sid, camp["key"])
            camp = dict(camp, build={}, buildError=_note_of(e))
            self.step(key, "failed", "Could not be written: %s" % _note_of(e))
        self.keep(camp)
        return camp

    def keep(self, camp):
        """Save the campaigns written so far, so a build that is cut off keeps every campaign that was finished."""
        with self.lock:
            self.built[camp["key"]] = camp
            ordered = [self.built.get(c["key"], c) for c in self.skeletons]
        try:
            self.store.save_plan_part(self.sid, "campaigns", ordered)
        except Exception:                                   # noqa: BLE001
            log.exception("strategy %s: could not save the campaigns so far", self.sid)

    @staticmethod
    def build_note(kind, built):
        if kind == "search":
            groups = built.get("adGroups") or []
            return "%d ad groups, %d keywords, %d ads" % (len(groups), sum(len(g["keywords"]) for g in groups), sum(len(g["ads"]) for g in groups))
        if kind == "pmax":
            return "%d asset groups" % len(built.get("assetGroups") or [])
        return "%d ad groups" % len(built.get("adGroups") or [])

    def write_all(self):
        profile = self.stage("profile", "profile", lambda: ai.write_profile(self.inputs, self.research, self.call))
        rivals = ai.rival_evidence(self.research)
        if rivals:
            competitive = self.stage("competitive", "competitive", lambda: ai.write_competitive(self.inputs, self.research, profile, self.call))
        else:
            competitive = {}
            self.step("a:competitive", "skipped", "No competitor website could be read.")
        strategy = self.stage("strategy", "strategy", lambda: ai.write_strategy(self.inputs, self.research, profile, competitive, self.call))
        arch = self.stage("architecture", "architecture", lambda: ai.write_architecture(self.inputs, self.research, profile, strategy, self.call))
        campaigns = arch["campaigns"]
        self.skeletons = campaigns
        with self.lock:
            self.steps = ps.with_builds(self.steps, campaigns)
        self.store.set_steps(self.sid, self.steps, phase="Building")
        existing = {c["key"]: c for c in (self.plan.get("campaigns") or []) if c.get("build")}
        todo = [dict(existing.get(c["key"], c)) if c["key"] in existing else c for c in campaigns]
        with ThreadPoolExecutor(max_workers=BUILD_WORKERS) as ex:
            built = list(ex.map(lambda c: self.build_one(c, profile, strategy, competitive), todo))
        with self.lock:
            self.plan["campaigns"] = built
        self.store.save_plan_part(self.sid, "campaigns", built)
        return profile, competitive, strategy, built

    def finish(self, profile, competitive, strategy, campaigns):
        """Check the whole plan, write each campaign's book, and save it."""
        self.step("f:finish", "running", "", phase="Checking")
        inputs, research = self.inputs, self.research
        corpus = ck.norm(rs.corpus(research))
        said = " ".join([inputs.get("brief") or "", inputs.get("services") or "", " ".join(c["name"] for c in inputs.get("competitors") or [])])
        brief = ck.norm(said)
        raw = " ".join((rs.corpus(research) + " " + said).lower().split())
        allocation, claims, drops_total, removed, short = [], [], {}, [], []
        for c in campaigns:
            built = c.get("build") or {}
            c["claims"] = ck.claims_to_confirm(ck.ad_texts(c["kind"], built), corpus, brief, raw) if built else []
            c["book"] = book.rows_for(c, inputs, strategy)
            allocation.append({"campaign": c["name"], "share": c["share"], "daily": c.get("daily"), "monthly": c.get("monthly"), "note": c.get("purpose") or ""})
            claims += [dict(x, campaign=c["name"]) for x in c["claims"]]
            for k, v in (c.get("drops") or {}).items():
                drops_total[k] = drops_total.get(k, 0) + v
            removed += [dict(x, campaign=c["name"]) for x in (built.get("negatives") or {}).get("removed", [])]
            short += ["%s: %s" % (c["name"], s) for s in (built.get("shortfalls") or [])] + (["%s: %s" % (c["name"], c["buildError"])] if c.get("buildError") else [])
        fc = ai.forecast(inputs, research)
        cpc = ((fc or {}).get("scenarios") or [{}, {}])[1].get("cpc")
        for c in campaigns:
            if cpc and c["kind"] == "search" and c.get("daily") and c["daily"] / cpc < BUDGET_CLICKS_A_DAY:
                short.append("%s gets %s a day, about %d clicks at the expected cost per click: too few for a Search campaign to learn (it wants about %d a day). "
                             "Give it more of the budget or plan fewer campaigns." % (c["name"], "{:,.2f}".format(c["daily"]), c["daily"] / cpc, BUDGET_CLICKS_A_DAY))
        strategy = dict(strategy)
        strategy["budget"] = dict(strategy.get("budget") or {}, allocation=allocation)
        plan = dict(self.plan)                              # keeps the architecture too, so a build that is resumed does not redesign the campaigns
        plan.update({"profile": profile, "competitive": competitive, "strategy": strategy, "campaigns": campaigns, "forecast": fc, "claims": claims,
                     "checks": {"dropped": drops_total, "removedNegatives": removed, "shortfalls": short,
                                "unconfirmedFacts": sum(1 for f in profile.get("facts", []) if not f["verified"]), "missing": ai.missing(research, inputs)},
                     "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "model": ai.MODEL})
        self.plan = plan
        self.store.save_plan(self.sid, plan)
        cost = self.cost.total(research)
        failed_builds = sum(1 for c in campaigns if not c.get("build"))
        note = ""
        if failed_builds:
            note = "%d of %d campaigns could not be written. Build again to try only those." % (failed_builds, len(campaigns))
        elif short:
            note = "%d thing%s the writing came up short on: see the checks." % (len(short), "" if len(short) == 1 else "s")
        self.step("f:finish", "done", "%d campaigns, %d Campaign Book rows each" % (len(campaigns), len(campaigns[0]["book"]) if campaigns else 0))
        self.store.finish(self.sid, cost=cost, note=note)


def heartbeat(store, sid, stop, every):
    """While a build runs, say so now and then: a build that has stopped saying so (a deploy killed it) can be taken over."""
    while not stop.wait(every):
        try:
            store.touch(sid)
        except Exception:                                   # noqa: BLE001 (a missed beat is not worth a build)
            log.warning("strategy %s: heartbeat failed", sid, exc_info=True)


def run_build(sid, *, store, deps, resume=False, build_class=None):
    """Do the build and save it. Never raises: a failure is saved on the build for the page to show. `build_class` is the agent's own Build subclass."""
    try:
        rec = store.get(sid)
        if not rec or rec.get("archived"):
            return
        deps = deps() if callable(deps) else deps                   # built here, so a failure to wire it up is saved on the build like any other
        b = (build_class or Build)(sid, rec, store, deps, resume=resume)
        if not deps.get("call") and not deps.get("model_ready", True):
            store.fail(sid, b.NO_MODEL)
            return
        stop = threading.Event()
        beat = threading.Thread(target=heartbeat, args=(store, sid, stop, deps.get("heartbeat_s", ps.HEARTBEAT_S)), daemon=True, name="strategy-heartbeat")
        beat.start()
        try:
            b.research_all()
            try:
                parts = b.write_all()
            except Exception as e:                          # noqa: BLE001 (the research stays saved; the stage that failed is named)
                log.exception("strategy %s: writing failed", sid)
                store.fail(sid, _note_of(e), cost=b.cost.total(b.research))
                return
            b.finish(*parts)
        finally:
            stop.set()
    except Exception as e:                                  # noqa: BLE001 (saved for the page, never lost in a thread)
        log.exception("strategy %s failed", sid)
        try:
            store.fail(sid, _note_of(e))
        except Exception:                                   # noqa: BLE001
            log.exception("strategy %s: could not save the failure", sid)
