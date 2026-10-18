"""Paid Marketing Agents: Ad Copy & Creative Generator, one build from start to finish.

    research (the website and the account, in parallel)  ->  one writing step per ad format (in parallel)  ->  the checks

It is a Strategy & Campaign Book build (tracker/paid_strategy_job.py) with other research and other writing: it runs in a background thread, saves a piece at a time in
Postgres so a build interrupted by a deploy resumes from what it kept, and takes everything it needs through `deps`, so tests run it on fakes. Each format is saved as it is
finished: a build that stops after three of four formats asks the model about the fourth only.
"""
from __future__ import annotations

import datetime as dt
import logging
from concurrent.futures import ThreadPoolExecutor

from . import paid_adcopy as pa, paid_adcopy_ai as aai, paid_adcopy_check as ck, paid_adcopy_research as rr
from . import paid_strategy_ai as ai, paid_strategy_job as job, paid_strategy_research as rs

log = logging.getLogger(__name__)

FORMAT_WORKERS = 3


class AdCopyBuild(job.Build):
    NO_MODEL = "The AI is not available on this server (no Anthropic key is set), so ad copy cannot be written."
    USABLE, EMPTY_ANSWER = {}, {}

    def default_steps(self):
        return pa.steps_for(self.inputs)

    # research -----------------------------------------------------------------
    def done_before(self, key):
        """A card read in part is kept, except an account card whose ads or keywords could not be read: that is read again."""
        d = (self.research.get(key) or {}).get("data") or {}
        if key == "account" and (d.get("adsFailed") or d.get("keywordsFailed")):
            return False
        return super().done_before(key)

    def read_account(self):
        d = self.deps
        if not d.get("account"):
            return rs.failed("account", "That Google Ads account is not one this platform can read.")
        return rr.read_account(d["account"], ads_report=d["ads_report"], sqr_read=d["sqr_read"], store=d.get("gads_store"), today=d.get("today"))

    def research_all(self):
        d, inp, on = self.deps, self.inputs, self.inputs["research"]
        with ThreadPoolExecutor(max_workers=2) as ex:
            lanes = []
            if on.get("website"):
                lanes.append(ex.submit(self.piece, "website", lambda: rs.read_website(inp, fetcher=d.get("fetcher"))))
            if on.get("account"):
                lanes.append(ex.submit(self.piece, "account", self.read_account))
            for f in lanes:
                f.result()

    # writing ------------------------------------------------------------------
    @staticmethod
    def format_note(res):
        t = ck.totals([res])
        parts = ["%d line%s ready" % (t["ready"], "" if t["ready"] == 1 else "s")]
        if t["confirm"]:
            parts.append("%d to confirm" % t["confirm"])
        if res["shortfalls"]:
            parts.append("short: " + "; ".join(res["shortfalls"][:2]))
        return ", ".join(parts)

    def write_all(self):
        """Write every format that is not already written. A format that fails stops the build after the others have finished and been saved, naming what is left."""
        formats = self.inputs["formats"]
        find = ck.claims_finder(*ck.evidence(self.research, self.inputs))
        saved = {k: v for k, v in (self.plan.get("formats") or {}).items() if k in formats and isinstance(v, dict) and v.get("state") in ("ok", "short")}
        for f in formats:
            if f in saved:
                self.step("a:" + f, "done", "Kept from the earlier run. " + self.format_note(saved[f]))
        errors = []

        def one(fmt):
            self.step("a:" + fmt, "running", "", phase="Writing")
            try:
                res = aai.write_and_check(fmt, self.inputs, self.research, self.call, find)
            except Exception as e:                          # noqa: BLE001 (the other formats go on and are kept)
                log.warning("adcopy %s: %s failed: %s", self.sid, fmt, job._note_of(e))
                errors.append("%s: %s" % (pa.FORMATS[fmt]["short"], job._note_of(e)))
                self.step("a:" + fmt, "failed", "Could not be written: %s" % job._note_of(e))
                return
            with self.lock:                                  # saved under the lock: two formats finishing together must not leave the older set as the last write
                saved[fmt] = res
                self.plan["formats"] = dict(saved)
                self.store.save_plan_part(self.sid, "formats", dict(saved))
            self.step("a:" + fmt, "done", self.format_note(res))

        with ThreadPoolExecutor(max_workers=FORMAT_WORKERS) as ex:
            list(ex.map(one, [f for f in formats if f not in saved]))
        if errors:
            raise RuntimeError("%d of %d formats could not be written (%s). What was written is kept: build again to ask only about the rest." % (len(errors), len(formats), "; ".join(sorted(errors))[:300]))
        return ([saved[f] for f in formats],)

    # the checks ---------------------------------------------------------------
    def finish(self, results):
        self.step("f:finish", "running", "", phase="Checking")
        inputs, research = self.inputs, self.research
        totals, claims = ck.totals(results), ck.claims_list(results)
        dropped = {}
        for r in results:
            for k, n in (r.get("dropped") or {}).items():
                dropped[k] = dropped.get(k, 0) + n
        site = ((research.get("website") or {}).get("data") or {}).get("facts") or {}
        shortfalls = ["%s: %s" % (r["short"], s) for r in results for s in r["shortfalls"]]
        checks = {"totals": totals, "claims": claims, "dropped": dropped, "shortfalls": shortfalls,
                  "keywords": ck.keyword_coverage(inputs.get("keywords") or [], results) if inputs.get("keywords") else None,
                  "missing": ai.missing(research, inputs),
                  "factsUsed": {"confirmed": pa.lines_of(inputs.get("facts"), 30, 300), "siteOffers": site.get("offers") or [], "siteRating": site.get("rating"),
                                "pagesRead": site.get("pagesRead") or 0},
                  "unreadAccount": bool(inputs["research"].get("account")) and (research.get("account") or {}).get("state") not in ("ok", "partial")}
        out = dict(self.plan)
        out.update({"order": [r["key"] for r in results], "formats": {r["key"]: r for r in results}, "checks": checks,
                    "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "model": ai.MODEL})
        self.plan = out
        self.store.save_plan(self.sid, out)
        cost = self.cost.total(research)
        notes = []
        if totals["confirm"]:
            notes.append("%d line%s state%s a claim the client has not confirmed: see the checks." % (totals["confirm"], "" if totals["confirm"] == 1 else "s", "s" if totals["confirm"] == 1 else ""))
        if shortfalls:
            n = len({r["key"] for r in results if r["shortfalls"]})
            notes.append("%d format%s came out short of what it needs: see the checks." % (n, "" if n == 1 else "s"))
        self.step("f:finish", "done", "%d lines ready, %d to confirm" % (totals["ready"], totals["confirm"]))
        self.store.finish(self.sid, cost=cost, note=" ".join(notes))


def run_build(sid, *, store, deps, resume=False):
    """Do the build and save it. Never raises: a failure is saved on the build for the page to show."""
    job.run_build(sid, store=store, deps=deps, resume=resume, build_class=AdCopyBuild)
