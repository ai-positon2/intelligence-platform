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

from . import paid_keywords as pk, paid_keywords_ai as kai, paid_keywords_data as kd, paid_keywords_meta as km, paid_keywords_meta_ai as mai, paid_keywords_plans as kplans
from . import paid_keywords_research as kr, paid_strategy_ai as ai, paid_strategy_job as job, paid_strategy_research as rs

log = logging.getLogger(__name__)

CHUNK_WORKERS = 3
MIN_JUDGED = 0.8        # a chunk the model answered for fewer than this share of its keywords is not kept: asking again is cheaper than judging by hand


class KeywordBuild(job.Build):
    USABLE = {
        "themes": lambda x: bool(x.get("themes")),
        "plan": lambda x: bool(x.get("summary") or x.get("questions") or x.get("quickWins") or x.get("negatives")),
        "seeds": lambda x: bool(x.get("phrases")),
        "audiences": lambda x: bool(x.get("judged")),
    }
    EMPTY_ANSWER = {
        "themes": "The answer about the themes had nothing in it. Build again to ask once more.",
        "classify": "The keywords were not judged. Build again to ask once more.",
        "plan": "The answer for the plan had nothing in it. Build again to ask once more.",
        "seeds": "The answer about what to look up in Meta's interests had no phrase in it. Build again to ask once more.",
        "audiences": "The interests were not judged. Build again to ask once more.",
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
            if on.get("metaaccount"):
                lanes["metaaccount"] = ex.submit(self.piece, "metaaccount", lambda: km.read_meta_account(d["meta_account"], pages_fn=d["meta_pages"], today=d.get("today")))
            if on.get("competitors"):
                lanes["competitors"] = ex.submit(self.piece, "competitors", lambda: rs.read_competitors(inp, fetcher=d.get("fetcher"), ask=d.get("ask"), want=pk.RIVALS_READ))
            for f in lanes.values():
                f.result()
        if pk.platforms_of(inp)[0]:
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

    def write_google(self):
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

    def write_meta(self):
        """The Meta side: which phrases to look up, Meta's interests for them, then the judging of every interest and the audiences to test."""
        seeds = self.stage("seeds", "metaSeeds", lambda: mai.write_seeds(self.inputs, self.research, self.call))
        pc = self.piece("interests", lambda: km.read_interests(seeds, self.research, get_fn=self.deps["meta_get"]))
        if pc.get("state") not in ("ok", "partial"):
            raise RuntimeError(pc.get("note") or "Meta's interests could not be read.")
        cands = (pc.get("data") or {}).get("candidates") or []
        aud = self.stage("audiences", "metaAudiences", lambda: mai.write_audiences(self.inputs, self.research, cands, seeds, self.call))
        return seeds, cands, aud

    def write_all(self):
        """(google, meta, errors): what each platform asked for wrote. One platform failing, when both were asked for, is said and the other is kept; when only one was asked
        for, or both fail, the build fails with the reason."""
        want_google, want_meta = pk.platforms_of(self.inputs)
        google = meta = None
        errors = {}
        if want_google:
            try:
                google = self.write_google()
            except Exception as e:                              # noqa: BLE001
                if not want_meta:
                    raise
                log.warning("keywords %s: the Google part failed: %s", self.sid, job._note_of(e))
                errors["Google"] = job._note_of(e)
        if want_meta:
            try:
                meta = self.write_meta()
            except Exception as e:                              # noqa: BLE001
                if not want_google:
                    raise
                log.warning("keywords %s: the Meta part failed: %s", self.sid, job._note_of(e))
                errors["Meta"] = job._note_of(e)
        if google is None and meta is None:
            raise RuntimeError(" ".join("%s: %s" % (k, v[:140]) for k, v in errors.items()))      # each reason is cut so the build's one error line (300 characters) holds both
        return google, meta, errors

    def google_part(self, theme_def, assembled, plan, stats, dropped):
        """(what the Google part adds to the saved result, the notes about it)."""
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
        cur = planner.get("currency") or inputs.get("currency") or "USD"
        out = {"keywords": rows, "themes": themes, "coverage": coverage, "seasonal": {"head": head, "rows": season}, "editor": kd.editor_rows(rows, themes), "stats": stats, "checks": checks, "currency": cur}
        out["plans"] = kplans.build(rows, coverage, plan, cur, unread)
        notes = []
        if checks["unjudged"]:
            notes.append("%d keyword%s could not be judged: they are listed as not judged." % (checks["unjudged"], "" if checks["unjudged"] == 1 else "s"))
        if checks["editorWithheld"]:
            notes.append("The account's keywords could not be read: no keyword is recommended for adding and the Google Ads Editor file was not made.")
        if checks["numbers"]:
            notes.append("%d number%s in the write-up %s not in the research: see the checks." % (len(checks["numbers"]), "" if len(checks["numbers"]) == 1 else "s", "is" if len(checks["numbers"]) == 1 else "are"))
        if checks["convertingNotFit"]:
            notes.append("%d converting search term%s judged not a fit: see the checks." % (len(checks["convertingNotFit"]), "" if len(checks["convertingNotFit"]) == 1 else "s"))
        return out, notes

    def meta_part(self, seeds, cands, aud):
        """(what the Meta part adds under `meta`, the notes about it)."""
        inputs, research = self.inputs, self.research
        macct = ((research.get("metaaccount") or {}).get("data")) or {}
        idata = ((research.get("interests") or {}).get("data")) or {}
        rows = km.assemble(cands, aud["judged"])
        fit_by = {r["id"]: r["fit"] for r in rows}
        in_use = [dict(u, fit=fit_by.get(u["id"], "unjudged")) for u in macct.get("inUse") or []]
        cov = km.coverage(rows, in_use)
        cur = (macct.get("account") or {}).get("currency") or inputs.get("currency") or "USD"
        adsets = macct.get("adsets") or []
        plan = aud["plan"]
        checks = {"unjudged": cov["unjudged"], "droppedAnswers": aud.get("dropped") or 0, "numbers": plan.get("numbers") or [], "emptyPhrases": idata.get("emptyPhrases") or [],
                  "interestNotes": idata.get("notes") or [], "accountNotes": macct.get("notes") or [], "cut": bool(idata.get("cut")), "found": idata.get("found") or len(cands),
                  "missing": ai.missing(research, inputs), "assumptions": (seeds.get("assumptions") or []) + (plan.get("assumptions") or []),
                  "advantageAdsets": macct.get("advantageAdsets") or 0}
        out = {"account": macct.get("account") or {}, "window": macct.get("window") or {}, "totals": macct.get("totals") or {}, "seeds": seeds, "interests": rows, "inUse": in_use,
               "adsets": adsets, "adsetCount": macct.get("adsetCount") or len(adsets), "audiences": aud["audiences"], "exclusions": aud["exclusions"], "plan": plan, "coverage": cov,
               "headlines": km.headlines(cov, in_use, rows, macct.get("adsetCount") or len(adsets), macct.get("advantageAdsets") or 0, cur),
               "plans": km.plans(rows, cov, in_use, aud["audiences"], aud["exclusions"], adsets, plan.get("lookalikes") or [], cur), "checks": checks, "currency": cur}
        notes = []
        if cov["unjudged"]:
            notes.append("%d interest%s could not be judged: listed as not judged." % (cov["unjudged"], "" if cov["unjudged"] == 1 else "s"))
        if checks["numbers"]:
            notes.append("%d number%s in the Meta write-up %s not in the research: see the checks." % (len(checks["numbers"]), "" if len(checks["numbers"]) == 1 else "s", "is" if len(checks["numbers"]) == 1 else "are"))
        return out, notes

    def finish(self, google, meta, errors):
        """Check the whole result and save it."""
        self.step("f:finish", "running", "", phase="Checking")
        inputs, research = self.inputs, self.research
        out = dict(self.plan)
        notes, said = [], []
        if google:
            upd, n = self.google_part(*google)
            out.update(upd)
            notes += n
            rows, themes, coverage = google[1]["keywords"], google[1]["themes"], google[1]["coverage"]
            said.append("%d keywords judged, %d to add in %d themes" % (len(rows), coverage["include"], len(themes)))
        if meta:
            upd, n = self.meta_part(*meta)
            out["meta"] = upd
            notes += n
            said.append("%d interests judged, %d audiences to test" % (upd["coverage"]["judged"], len(upd["audiences"])))
        for plat, why in errors.items():
            notes.append("The %s part could not be written (%s). The other part is kept: build again to try this one." % (plat, why[:200]))
        out["errors"] = dict(errors)
        if not google:
            out["currency"] = (out.get("meta") or {}).get("currency") or inputs.get("currency") or "USD"
        out.update({"generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "model": ai.MODEL})
        self.plan = out
        self.store.save_plan(self.sid, out)
        cost = self.cost.total(research)
        self.step("f:finish", "done", "; ".join(said))
        self.store.finish(self.sid, cost=cost, note=" ".join(notes))


def run_build(sid, *, store, deps, resume=False):
    """Do the build and save it. Never raises: a failure is saved on the build for the page to show."""
    job.run_build(sid, store=store, deps=deps, resume=resume, build_class=KeywordBuild)
