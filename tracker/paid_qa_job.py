"""Paid Marketing Agents: QA Agent, one AI review of one campaign, start to finish.

    read the campaign (Google Ads) and its landing pages  ->  run the exact rules
    ->  the AI reviews every check the rules did not settle, section by section
    ->  combine, and save

It runs in a background thread (the routes start it), because it makes a couple of
dozen Google Ads reads, fetches pages and asks the model about up to fifteen sections:
a minute or so, far too long for one web request. Its state lives in Postgres, so
whichever server answers the page's polling can report progress. Everything it needs is
passed in, so tests run it on fakes.
"""
from __future__ import annotations

import datetime as dt
import logging

from . import paid_qa, paid_qa_ai, paid_qa_auto, paid_qa_book

log = logging.getLogger(__name__)


def _note(problems, asked_sections):
    """A sentence for the page when the AI could not do some or all of its work."""
    if not problems:
        return ""
    kinds = ", ".join("%s (%d)" % kv for kv in sorted(problems.items()))
    failed = sum(problems.values())
    if failed >= asked_sections:
        if "not_configured" in problems:
            return ("The AI review is not available on this server (no Anthropic key is set). Only the exact Google Ads rules ran; "
                    "every other check needs a person.")
        return "The AI could not review any section (%s). Only the exact Google Ads rules ran; run the review again." % kinds
    return "The AI could not review %d of %d sections (%s). Those checks need a person, or run the review again." % (
        failed, asked_sections, kinds)


def run_review(launch_id, *, store, search, geo_fn=None, web_fn=None, call=None, today=None):
    """Do the review and save it. Never raises: a failure is saved on the QC for the page to show."""
    try:
        launch = store.get_launch(launch_id)
        if not launch or launch["archived"]:
            return
        store.set_progress(launch_id, "Reading the campaign from Google Ads", 0, 0)
        out = paid_qa_auto.run(launch["account_id"], launch["campaign_id"], search, geo_fn=geo_fn, web_fn=web_fn,
                               book=launch.get("book") or None)
        camp = out["campaign"]
        if not camp["id"]:
            store.fail_review(launch_id, "That campaign was not found in this account.")
            return
        data, rules = out["data"], out["findings"]
        types = paid_qa.campaign_types_for(camp["channel"], camp["dsa"])
        checks = paid_qa.applicable([], types)
        ids = [i["id"] for i in checks]
        settled = {c for c in ids if rules.get(c) and rules[c]["result"] in ("pass", "fail")}
        ask = [i for i in checks if i["id"] not in settled]
        sections = len({i["section"] for i in ask})
        ev = paid_qa_ai.evidence_bundle(data, paid_qa_auto.Ctx(data))
        meta = {"name": camp["name"] or launch["campaign_name"], "type": camp["type"], "today": (today or dt.date.today()).isoformat(),
                "bookUsed": paid_qa_book.applies_to(camp["channel"])}
        store.set_progress(launch_id, "The AI is reviewing the checklist", 0, sections)
        ai, problems = paid_qa_ai.review(
            ask, ev, rules, meta, call=call,
            progress=lambda done, total: store.set_progress(launch_id, "The AI is reviewing the checklist", done, total))
        combined = paid_qa_ai.combine(rules, ai, ids)
        store.finish_review(launch_id, combined, campaign_name=camp["name"] or launch["campaign_name"], channel=camp["channel"],
                            campaign_types=types, note=_note(problems, sections))
    except Exception as e:                                  # noqa: BLE001 (saved for the page, never lost in a thread)
        log.exception("qa review %s failed", launch_id)
        msg = str(e) or type(e).__name__
        try:
            store.fail_review(launch_id, msg)
        except Exception:                                   # noqa: BLE001
            log.exception("qa review %s: could not save the failure", launch_id)
