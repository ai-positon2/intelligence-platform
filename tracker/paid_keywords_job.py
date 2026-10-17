"""Paid Marketing Agents: Keyword Research, one build from start to finish.

    research (business, website, account, competitors in parallel)  ->  Keyword Planner  ->  themes  ->  judge each keyword (in chunks)  ->  the plan  ->  the checks

It is a Strategy & Campaign Book build (tracker/paid_strategy_job.py) with other research and other writing stages: it runs in a background thread, saves a piece
at a time in Postgres so a build interrupted by a deploy resumes from what it kept, and takes everything it needs through `deps`, so tests run it on fakes. The
judging of the keywords is saved a chunk at a time: a build that stops after three of four chunks asks the model about the fourth only.
"""
from __future__ import annotations

import datetime as dt
import logging
from concurrent.futures import ThreadPoolExecutor

from . import paid_keywords as pk, paid_keywords_ai as kai, paid_keywords_data as kd, paid_keywords_research as kr, paid_strategy_ai as ai
from . import paid_strategy_job as job, paid_strategy_research as rs

log = logging.getLogger(__name__)

CHUNK_WORKERS = 3
MIN_JUDGED = 0.8        # a chunk the model answered for fewer than this share of its keywords is not kept: asking again is cheaper than judging by hand


class KeywordBuild(job.Build):
    USABLE = {
        "themes": lambda x: bool(x.get("themes")),
        "plan": lambda x: bool(x.get("summary") or x.get("questions") or x.get("quickWins") or x.get("negatives")),
    }
    EMPTY_ANSWER = {
        "themes": "The answer about the themes had nothing in it. Build again to ask once more.",
        "classify": "The keywords were not judged. Build again to ask once more.",
        "plan": "The answer for the plan had nothing in it. Build again to ask once more.",
    }
    NO_MODEL = "The AI is not available on this server (no Anthropic key is set), so the keywords cannot be judged."

    def default_steps(self):
        return pk.steps_for(self.inputs)

    # research -----------------------------------------------------------------
    def done_before(self, key):
        """A card read in part is kept, except where the part that is missing is what the result stands on: an account whose keywords or negatives could not be read
        (every idea would be called new), and a Keyword Planner of which some request did not answer."""
        d = (self.research.get(key) or {}).get("data") or {}
        if key == "account" and (d.get("keywordsUnknown") or d.get("negativesUnknown")):
            return False
        if key == "planner" and any((c.get("note") or "").startswith("did not answer") for c in d.get("calls") or []):
            return False
        return super().done_before(key)

    def research_all(self):
        d, inp, on = self.deps, self.inputs, self.inputs["research"]
        lanes = {}
        with ThreadPoolExecutor(max_workers=4) as ex:
            if on.get("business"):
                lanes["business"] = ex.submit(self.piece, "business", lambda: kr.read_business(inp, find=lambda i: kr.find_business(i, list_recent=d["business_list"], get=d["business_get"])))
            if on.get("website"):
                lanes["website"] = ex.submit(self.piece, "website", lambda: rs.read_website(inp, fetcher=d.get("fetcher")))
            if on.get("account"):
                lanes["account"] = ex.submit(self.piece, "account", lambda: kr.read_account(d["account"], sqr_read=d["sqr_read"], store=d.get("gads_store"), today=d.get("today")))
            if on.get("competitors"):
                lanes["competitors"] = ex.submit(self.piece, "competitors", lambda: rs.read_competitors(inp, fetcher=d.get("fetcher"), ask=d.get("ask"), want=pk.RIVALS_READ))
            for f in lanes.values():
                f.result()
        self.piece("planner", lambda: kr.read_planner(inp, self.research, post=d["post"], planner_account=d.get("planner_account"), currency=d.get("planner_currency")))

    # writing ------------------------------------------------------------------
    def judge(self, cands, theme_list):
        """Judge every candidate in chunks, saving each chunk as it finishes. Returns ({keyword: judgement}, answers dropped). A chunk that fails stops the build after the
        others have finished and been saved, naming what is left; a later run asks only about those."""
        sig = kd.signature(cands)
        saved = self.plan.get("classify") or {}
        parts = dict(saved.get("parts") or {}) if saved.get("sig") == sig else {}
        if saved.get("parts") and saved.get("sig") != sig:
            self.plan.pop("plan", None)                              # judged about other keywords: the plan written from that judgement is not this one's
        groups = kd.chunks(cands)
        todo = [i for i in range(len(groups)) if str(i) not in parts]
        if not todo:
            self.step("a:classify", "done", "Kept from the earlier run.")
        else:
            self.step("a:classify", "running", "%d of %d groups judged" % (len(parts), len(groups)), phase="Writing")
            errors = []

            def one(i):
                try:
                    judged, dropped = kai.write_classify(self.inputs, self.research, theme_list, groups[i], self.call, i)
                    if len(judged) < MIN_JUDGED * len(groups[i]):
                        raise RuntimeError("the answer judged only %d of %d keywords" % (len(judged), len(groups[i])))
                except Exception as e:                      # noqa: BLE001 (the other groups go on and are kept)
                    log.warning("keywords %s: group %d failed: %s", self.sid, i, job._note_of(e))
                    errors.append("group %d of %d: %s" % (i + 1, len(groups), job._note_of(e)))
                    return
                with self.lock:                                      # saved under the lock: two groups finishing together must not leave the older list of groups as the last write
                    parts[str(i)] = {"judged": judged, "dropped": dropped}
                    self.plan["classify"] = {"sig": sig, "parts": dict(parts), "total": len(groups)}
                    self.store.save_plan_part(self.sid, "classify", self.plan["classify"])
                    done = len(parts)
                self.step("a:classify", "running", "%d of %d groups judged" % (done, len(groups)))

            with ThreadPoolExecutor(max_workers=CHUNK_WORKERS) as ex:
                list(ex.map(one, todo))
            if errors:
                msg = "%d of %d groups of keywords could not be judged (%s). What was judged is kept: build again to ask only about the rest." % (len(errors), len(groups), "; ".join(sorted(errors))[:300])
                self.step("a:classify", "failed", msg)
                raise RuntimeError(msg)
            self.step("a:classify", "done", "%d keywords judged in %d groups" % (len(cands), len(groups)))
        judged, dropped = {}, 0
        for p in parts.values():
            judged.update(p.get("judged") or {})
            dropped += p.get("dropped") or 0
        missing = [c for c in cands if c["kw"] not in judged]
        if missing:
            judged, dropped = self.repair(missing, theme_list, parts, sig, len(groups), judged, dropped)
        return judged, dropped

    def repair(self, missing, theme_list, parts, sig, total, judged, dropped):
        """Ask once more about the keywords the model left out of its answers (a group answered for 80 percent of its keywords is kept, so up to a fifth of it is asked for
        here). A group that fails again is not fatal: those keywords stay not judged and say so."""
        self.step("a:classify", "running", "%d keywords were left out of the answers: asking about them again" % len(missing))
        for j, group in enumerate(kd.chunks(missing)):
            try:
                got, drop = kai.write_classify(self.inputs, self.research, theme_list, group, self.call, j)
            except Exception as e:                              # noqa: BLE001
                log.warning("keywords %s: asking again about %d left-out keywords failed: %s", self.sid, len(group), job._note_of(e))
                continue
            if not got:
                continue
            with self.lock:
                parts["r%d" % sum(1 for k in parts if k.startswith("r"))] = {"judged": got, "dropped": drop}
                self.plan["classify"] = {"sig": sig, "parts": dict(parts), "total": total}
                self.store.save_plan_part(self.sid, "classify", self.plan["classify"])
            judged = dict(judged, **got)
            dropped += drop
            self.plan.pop("plan", None)                          # a plan written before these were judged did not take them into account
        still = [c for c in missing if c["kw"] not in judged]
        self.step("a:classify", "done", "%d keywords judged in %d groups" % (len(judged), total) + (", %d could not be judged" % len(still) if still else ""))
        return judged, dropped

    def write_all(self):
        planner = self.research.get("planner") or {}
        if planner.get("state") not in ("ok", "partial"):             # a list built only from what the account's search terms hold is not a keyword research
            raise RuntimeError("Keyword Planner gave no ideas, so there is nothing to judge. %s" % (planner.get("note") or "It was not asked."))
        cands, stats = kd.candidates(self.inputs, self.research)
        if not cands:
            raise RuntimeError("There are no keywords to judge: every idea Keyword Planner gave was below the smallest search volume, on the avoid list, or blocked in every campaign by the account's negative keywords.")
        known = bool(((self.research.get("account") or {}).get("data") or {}).get("campaigns"))
        if self.plan.get("themeDef") and bool(self.plan["themeDef"].get("campaignsKnown")) != known:
            for key in ("themeDef", "classify", "plan"):          # the themes were made without the account's campaigns (or with them, and now they are gone): everything made from them is made again
                self.plan.pop(key, None)
        theme_def = self.stage("themes", "themeDef", lambda: kai.write_themes(self.inputs, self.research, cands, self.call))
        judged, dropped = self.judge(cands, theme_def["themes"])
        assembled = kd.assemble(cands, judged, theme_def["themes"])
        assembled["coverage"].update(kd.pool_coverage(stats))
        plan = self.stage("plan", "plan", lambda: kai.write_plan(self.inputs, self.research, assembled, self.call))
        return theme_def, assembled, plan, stats, dropped

    def finish(self, theme_def, assembled, plan, stats, dropped):
        """Check the whole result and save it."""
        self.step("f:finish", "running", "", phase="Checking")
        inputs, research = self.inputs, self.research
        rows, themes, coverage = assembled["keywords"], assembled["themes"], assembled["coverage"]
        planner = ((research.get("planner") or {}).get("data")) or {}
        account = research.get("account") or {}
        adata = account.get("data") or {}
        asked = bool(inputs["research"].get("account"))
        unread = asked and (account.get("state") not in ("ok", "partial") or bool(adata.get("keywordsUnknown")))
        if unread:                                           # every idea is called new when the account's keywords are not known: nothing is recommended for adding on that guess
            for r in rows:
                r["include"] = False
            coverage["include"], coverage["accountUnread"] = 0, True
        head, season = kd.seasonal_table(themes)
        stats = dict(stats, belowFloor=stats.get("belowFloor", 0) + planner.get("belowFloor", 0))             # the planner piece drops the small ideas before the candidates are made
        checks = {"unjudged": coverage["unjudged"], "droppedAnswers": dropped, "removedNegatives": plan.get("removedNegatives") or [],
                  "convertingNotFit": [r["kw"] for r in rows if r.get("conv") and r["fit"] == "no"][:20], "blockedInSomeCampaigns": sum(1 for r in rows if (r.get("neg") or {}).get("scope")),
                  "emptySeeds": planner.get("emptySeeds") or [], "missing": ai.missing(research, inputs), "assumptions": theme_def.get("assumptions") or [], "numbers": plan.get("numbers") or [],
                  "accountWarnings": adata.get("warnings") or [], "caveats": adata.get("caveats") or [], "geography": planner.get("geoNote") or "",
                  "editorWithheld": "The account's keywords could not be read, so every keyword is shown as new and the file for Google Ads Editor was not made. Build again to read the account." if unread else ""}
        out = dict(self.plan)
        out.update({"keywords": rows, "themes": themes, "coverage": coverage, "seasonal": {"head": head, "rows": season}, "editor": kd.editor_rows(rows, themes), "stats": stats, "checks": checks,
                    "currency": planner.get("currency") or inputs.get("currency") or "USD", "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "model": ai.MODEL})
        self.plan = out
        self.store.save_plan(self.sid, out)
        cost = self.cost.total(research)
        notes = []
        if checks["unjudged"]:
            notes.append("%d keyword%s could not be judged: they are listed as not judged." % (checks["unjudged"], "" if checks["unjudged"] == 1 else "s"))
        if checks["editorWithheld"]:
            notes.append("The account's keywords could not be read: no keyword is recommended for adding and the Google Ads Editor file was not made.")
        if checks["numbers"]:
            notes.append("%d number%s in the write-up %s not in the research: see the checks." % (len(checks["numbers"]), "" if len(checks["numbers"]) == 1 else "s", "is" if len(checks["numbers"]) == 1 else "are"))
        if checks["convertingNotFit"]:
            notes.append("%d converting search term%s judged not a fit: see the checks." % (len(checks["convertingNotFit"]), "" if len(checks["convertingNotFit"]) == 1 else "s"))
        self.step("f:finish", "done", "%d keywords judged, %d to add in %d themes" % (len(rows), coverage["include"], len(themes)))
        self.store.finish(self.sid, cost=cost, note=" ".join(notes))


def run_build(sid, *, store, deps, resume=False):
    """Do the build and save it. Never raises: a failure is saved on the build for the page to show."""
    job.run_build(sid, store=store, deps=deps, resume=resume, build_class=KeywordBuild)
