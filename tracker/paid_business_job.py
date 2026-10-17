"""Paid Marketing Agents: Business Research, one build from start to finish.

    research (in parallel lanes)  ->  the business  ->  who buys and why  ->  what it means and what to ask  ->  the checks

It is a Strategy & Campaign Book build (tracker/paid_strategy_job.py) with other research and three writing stages instead of the strategy and the
campaigns: it runs in a background thread, saves a piece at a time in Postgres so a build interrupted by a deploy resumes from what it kept, and takes
everything it needs through `deps`, so tests run it on fakes.
"""
from __future__ import annotations

import datetime as dt
import logging
from concurrent.futures import ThreadPoolExecutor

from . import paid_business as pb, paid_business_ai as bai, paid_business_check as bck, paid_business_research as brs, paid_strategy_ai as ai
from . import paid_strategy_check as ck, paid_strategy_job as job, paid_strategy_research as rs

log = logging.getLogger(__name__)


class BusinessBuild(job.Build):
    USABLE = {
        "core": lambda x: bool(x.get("summary") or x.get("offerings")),
        "audience": lambda x: bool(x.get("audiences") or x.get("journey")),
        "implications": lambda x: bool(x.get("summary") or x.get("questions")),
    }
    EMPTY_ANSWER = {
        "core": "The answer about the business had nothing in it. Build again to ask once more.",
        "audience": "The answer about who buys had nothing in it. Build again to ask once more.",
        "implications": "The answer about what it means for paid media had nothing in it. Build again to ask once more.",
    }
    NO_MODEL = "The AI is not available on this server (no Anthropic key is set), so the business cannot be written up."

    def default_steps(self):
        return pb.steps_for(self.inputs)

    def research_all(self):
        d, inp, on = self.deps, self.inputs, self.inputs["research"]
        social_run = self.start_social() if on.get("social") else None
        lanes = {}
        with ThreadPoolExecutor(max_workers=6) as ex:
            if on.get("website"):
                lanes["website"] = ex.submit(self.piece, "website", lambda: brs.read_website_and_tech(inp, fetcher=d.get("fetcher")))
            if on.get("reputation"):
                lanes["reputation"] = ex.submit(self.piece, "reputation", lambda: brs.read_reputation(inp, ask=d.get("ask")))
            if on.get("competitors"):
                lanes["competitors"] = ex.submit(self.piece, "competitors", lambda: rs.read_competitors(inp, fetcher=d.get("fetcher"), ask=d.get("ask"), want=pb.RIVALS_READ))
            if on.get("history"):
                lanes["history"] = ex.submit(self.piece, "history", lambda: rs.read_history(
                    d["account"], search=d["search"], geo_names=d.get("geo_names"), sqr_read=d.get("sqr_read"), store=d.get("gads_store"), cached=d.get("cached"),
                    today=d.get("today")))
            if on.get("market"):
                lanes["market"] = ex.submit(self.piece, "market", lambda: rs.read_market(inp, ask=d.get("ask")))
            if on.get("social"):
                lanes["social"] = ex.submit(self.piece, "social", lambda: self.collect_social(social_run))
            for f in lanes.values():
                f.result()

    def write_all(self):
        core = self.stage("core", "core", lambda: bai.write_core(self.inputs, self.research, self.call))
        audience = self.stage("audience", "audience", lambda: bai.write_audience(self.inputs, self.research, core, self.call))
        impl = self.stage("implications", "implications", lambda: bai.write_implications(self.inputs, self.research, core, audience, self.call))
        return core, audience, impl

    def finish(self, core, audience, impl):
        """Check the whole report and save it."""
        self.step("f:finish", "running", "", phase="Checking")
        inputs, research = self.inputs, self.research
        site = ck.norm(rs.corpus(research))
        outside = ck.norm(brs.corpus(research))
        said = " ".join([inputs.get("brief") or "", inputs.get("services") or "", " ".join(c["name"] for c in inputs.get("competitors") or [])])
        raw = " ".join((rs.corpus(research) + " " + brs.corpus(research) + " " + said).lower().split())
        claims = ck.claims_to_confirm(bck.claim_items(core, audience, impl), site + " " + outside, ck.norm(said), raw)
        tech = (((research.get("website") or {}).get("data") or {}).get("tech")) or {}
        unconfirmed_facts = sum(1 for f in core.get("facts", []) if not f["verified"])
        unconfirmed_usps = sum(1 for u in core.get("usps", []) if u.get("evidence") and not u["verified"] and not u.get("viaSearch"))
        plan = dict(self.plan)
        plan.update({"core": core, "audience": audience, "implications": impl, "tech": tech, "claims": claims,
                     "checks": {"unconfirmedFacts": unconfirmed_facts, "unconfirmedUsps": unconfirmed_usps, "missing": ai.missing(research, inputs)},
                     "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "model": ai.MODEL})
        self.plan = plan
        self.store.save_plan(self.sid, plan)
        cost = self.cost.total(research)
        note = ""
        if claims:
            note = "%d claim%s in the write-up %s not on the client's pages or in what the search found: see the checks." % (len(claims), "" if len(claims) == 1 else "s", "is" if len(claims) == 1 else "are")
        self.step("f:finish", "done", "%d offerings, %d audiences, %d questions for the client" % (len(core.get("offerings") or []), len(audience.get("audiences") or []),
                                                                                                    len(impl.get("questions") or [])))
        self.store.finish(self.sid, cost=cost, note=note)


def run_build(sid, *, store, deps, resume=False):
    """Do the build and save it. Never raises: a failure is saved on the build for the page to show."""
    job.run_build(sid, store=store, deps=deps, resume=resume, build_class=BusinessBuild)
