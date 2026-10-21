"""Paid Marketing Agents: Test & Experiment Tracker, how to act on a verdict and what acting on it is worth (no network).

The report already says what to do ("apply it", "end it", "pause the ads behind"). This says how, in the Google Ads or Meta screens, and what it is worth: for a winner the
conversions a month the lift would add to the traffic the control had, with the low end of the 95 percent range for the gap so the figure is not the luckiest reading; for a
loser what keeping it costs; for a test that is not there yet the date to look again and how much more it needs. A worth is always an estimate and says what it assumes.

experiment_plan() and ad_plan() return {"steps": [...], "worth": text or None}, or None when there is nothing to do (a record, a test not started).
"""
from __future__ import annotations

import datetime as dt
import math

from . import paid_exp_stats as st

Z95 = 1.959964
MONTH = 30.0
FAR_DAYS = 180
SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "CAD": "$", "AUD": "$"}


def money(v, cur=""):
    sym = SYMBOLS.get(cur or "USD", "")
    body = "{:,.0f}".format(v) if abs(v) >= 100 else "{:,.2f}".format(v)
    return (sym + body) if sym or not cur else "%s %s" % (cur, body)


def _count(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _day(d):
    return "%s %d" % (d.strftime("%b"), d.day)


def _spec(metric):
    return st.METRICS[metric]


def _rate(a, metric):
    s = _spec(metric)
    return st.rate(a[s["hits"]], a[s["of"]])


def gap_range(control, trial, metric):
    """(gap, low, high): the trial's rate minus the control's and the 95 percent range around it, in rate units (0.01 is one point). None when a rate cannot be worked out."""
    s = _spec(metric)
    nc, nt = control[s["of"]], trial[s["of"]]
    rc, rt = st.rate(control[s["hits"]], nc), st.rate(trial[s["hits"]], nt)
    if rc is None or rt is None:
        return None
    se = math.sqrt(max(rc * (1 - rc), 0.0) / nc + max(rt * (1 - rt), 0.0) / nt)
    gap = rt - rc
    return gap, gap - Z95 * se, gap + Z95 * se


def _per_month(a, key, days):
    return a[key] / float(days) * MONTH if days > 0 else 0.0


def _control_share(control_arm, trial_arm):
    sc, stt = control_arm.get("split"), trial_arm.get("split")
    return sc / float(sc + stt) if sc and stt else 0.5


def _hits(n, metric, adj=""):
    """"86 conversions", "1 click", "86 more conversions"."""
    n = int(round(n))
    noun = "conversion" if metric == "convRate" else "click"
    return "%d %s%s%s" % (n, adj + " " if adj else "", noun, "" if n == 1 else "s")


def _estimate_tail(meta):
    """What a winner's estimate assumes."""
    return (" This assumes the traffic stays as it was and the gap holds; a gap measured in a test usually shrinks a little once it is applied." if not meta else
            " This assumes the audience stays as it was and the gap holds; a gap measured in a test usually shrinks a little once it is the only thing running.")


# ── Experiments ─────────────────────────────────────────────────────────────

def _after_apply_google(name):
    return ["Open Google Ads, then Campaigns, then Experiments in the left menu, and open \"%s\"." % name,
            "Click Apply. Choose Update your original campaign to carry the trial's changes into the campaign you were testing against (it keeps the campaign's history), or "
            "Convert the trial into a new campaign if you want both to carry on side by side.",
            "For the next week compare conversions a day with what the campaign did before the test. The first days after a change can swing, so judge it on the week."]


def _end_google(name, keep):
    return ["Open Google Ads, then Campaigns, then Experiments in the left menu, and open \"%s\"." % name,
            "Click End experiment. %s" % keep]


def _check_google():
    return ["Open Google Ads, then Campaigns, filter the list by Experiment type, and check each arm's campaign: its status is Enabled, an ad is approved and running, and the budget is not Limited.",
            "Open the experiment (Campaigns, Experiments) and check the traffic split is what you meant. If one arm is far behind on impressions, give its campaign the budget the other has.",
            "Do not change bids, budgets or ads in either arm once it is running: an edit mid-test starts the count again."]


def _check_meta():
    return ["Open Ads Manager and check the ad set of each cell: it is on, has budget, its ads are approved and it is out of the learning phase.",
            "If one cell is far behind on reach, give its ad set the budget the other has.",
            "Do not edit either cell's ads, audience or budget while the test runs: an edit makes the cells unlike each other."]


def _meta_decision(trial, control, ahead, name):
    keep = trial if ahead else control
    gone = control if ahead else trial
    return ["Open Ads Manager, then Experiments (under Business tools), and open \"%s\"." % name,
            "End the test, so the cells stop competing for the same audience.",
            "Turn off the ad set of %s and put its budget on the ad set of %s. Raise a budget by no more than about 20%% at a time so the ad set stays out of the learning phase." % (gone, keep)]


def _winner_worth(control, trial, cmp_, days, meta, shares):
    metric = cmp_["metric"]
    gr = gap_range(control, trial, metric)
    if gr is None:
        return None
    gap, low, _high = gr
    s = _spec(metric)
    base = _per_month(control, s["of"], days) / shares
    if gap <= 0 or base <= 0:
        return None
    est, floor = gap * base, max(0.0, low * base)
    whole = "the whole campaign" if not meta else "all of the budget"
    text = "If %s ran as the trial does, its lead of %s points on %s is worth about %s a month (%s gets about %s %s a month at this pace)" % (
        whole, ("%.2f" % (gap * 100)).rstrip("0").rstrip("."), cmp_["metricLabel"], _hits(est, metric, "more"), whole, "{:,.0f}".format(round(base)), s["of"])
    if floor >= 1:
        text += ", and at least about %s at the low end of the 95%% range." % "{:,.0f}".format(round(floor))
    else:
        text += ", though the low end of the 95%% range is close to nothing, so the gain could be small."
    return text + _estimate_tail(meta)


def _behind_worth(control, trial, cmp_, days, cur, meta):
    metric = cmp_["metric"]
    gr = gap_range(control, trial, metric)
    if gr is None:
        return None
    gap, _low, _high = gr
    s = _spec(metric)
    lost = -gap * _per_month(trial, s["of"], days)
    spend = _per_month(trial, "cost", days)
    parts = []
    if lost >= 1:
        parts.append("At this gap the trial gets about %s a month than the original would on the same traffic" % _hits(lost, metric, "fewer"))
    if spend >= 1:
        parts.append("it spends about %s a month" % money(spend, cur))
    if not parts:
        return None
    return "; ".join(parts) + ". Ending it stops both. This assumes the traffic stays as it was and the gap holds."


def _more_days(cmp_, control, trial, days):
    """The text for how long it would take to see a gap as small as the one found, or None."""
    need = cmp_.get("needed")
    if not need or days <= 0:
        return None
    of = _spec(cmp_["metric"])["of"]
    pace = min(control[of], trial[of]) / float(days)
    if pace <= 0:
        return None
    left = max(0.0, need - min(control[of], trial[of]))
    d = int(math.ceil(left / pace))
    word = "clicks" if of == "clicks" else "impressions"
    if d > FAR_DAYS:
        return "To tell a gap as small as this one from chance each arm would need about %s %s, which at the current pace is more than six months." % ("{:,.0f}".format(need), word)
    return "To tell a gap as small as this one from chance each arm would need about %s %s, which at the current pace is about %s more." % ("{:,.0f}".format(need), word, _count(d, "day"))


def experiment_plan(e, cmp_, control, trial, action, days, today, cur=""):
    """The plan for one experiment. `control` and `trial` are the arms' totals (impressions, clicks, conversions, cost, value) and `cmp_` the comparison, either may be None."""
    meta = e.get("platform") == "meta"
    state = e["state"]
    if state != "running":
        return None
    name = e["name"]
    cname = next((a["name"] for a in e["arms"] if a["control"]), "the first cell")
    tname = next((a["name"] for a in e["arms"] if not a["control"]), "the other cell")
    kind = action["kind"]
    if cmp_ is None:
        if meta:
            return {"steps": ["Open Ads Manager, then Experiments (under Business tools), and open \"%s\"." % name, "Read the result there: the tracker cannot work this kind of test out." ], "worth": None}
        return {"steps": ["Open Google Ads, then Campaigns, then Experiments in the left menu, and open \"%s\"." % name,
                          "Read the result on its page: Google does not report this kind of experiment's arms to the tracker. When you apply or end it, the buttons are on the same page."], "worth": None}
    v = cmp_["verdict"]
    ctl = next((a for a in e["arms"] if a["control"]), {})
    trl = next((a for a in e["arms"] if not a["control"]), {})
    shares = _control_share(ctl, trl) if ctl and trl else 0.5
    if v == "winner":
        steps = _meta_decision(tname, cname, True, name) if meta else _after_apply_google(name)
        return {"steps": steps, "worth": _winner_worth(control, trial, cmp_, days, meta, shares)}
    if v == "behind":
        if meta:
            steps = _meta_decision(tname, cname, False, name)
        else:
            steps = _end_google(name, "The original campaign carries on exactly as it was and nothing from the trial is applied.")
        return {"steps": steps, "worth": _behind_worth(control, trial, cmp_, days, cur, meta)}
    if v == "no_difference":
        more = _more_days(cmp_, control, trial, days)
        if kind in ("decide", "end"):
            steps = (["Open Ads Manager, then Experiments (under Business tools), and open \"%s\"." % name, "End the test, and keep whichever cell is cheaper to run and simpler to manage."] if meta else
                     _end_google(name, "Then keep whichever version is simpler to run: if that is the trial, apply it first (Apply, then Update your original campaign), otherwise leave the original as it is."))
            return {"steps": steps, "worth": None, "note": more}
        steps = ["Leave both arms alone: an edit mid-test starts the count again.",
                 "Look again on %s." % _day(today + dt.timedelta(days=7))]
        return {"steps": steps, "worth": None, "note": more}
    if v == "too_early":
        wait = cmp_.get("wait") or {}
        if kind == "stuck":
            return {"steps": _check_meta() if meta else _check_google(), "worth": None}
        if kind == "decide":
            if meta:
                steps = ["Open Ads Manager, then Experiments (under Business tools), and open \"%s\"." % name,
                         "Either end the test, or move its end date later so it can gather what it still needs."]
            else:
                steps = ["Open Google Ads, then Campaigns, then Experiments in the left menu, and open \"%s\"." % name,
                         "To give it more time, edit the end date (the pencil beside the dates) to a later day. Otherwise click End experiment."]
            return {"steps": steps, "worth": None}
        eta = wait.get("eta")
        steps = ["Leave both arms alone: an edit mid-test starts the count again."]
        if eta:
            steps.append("Look again on %s, when it should have enough to call." % _day(today + dt.timedelta(days=int(eta))))
        return {"steps": steps, "worth": None}
    if kind == "check":
        return {"steps": _check_meta() if meta else _check_google(), "worth": None}
    return None


# ── Ad tests ────────────────────────────────────────────────────────────────

def _names(ads, n=3):
    out = [a["label"] for a in ads[:n]]
    more = len(ads) - n
    return ", ".join('"%s"' % x for x in out) + (" and %d more" % more if more > 0 else "")


def _ad_where(meta, group):
    if meta:
        return "Open Ads Manager, then the ad set \"%s\", and its Ads tab." % group["name"]
    return "Open Google Ads, then Ads (under Ads & assets), and filter to the ad group \"%s\" in \"%s\"." % (group["name"], group["campaign"])


def _challenger(meta):
    if meta:
        return "Duplicate the leading ad and change one thing in the copy (the main benefit or the first line). Run the pair as an A/B test (Experiments under Business tools) so the next result is a fair one."
    return "Write one challenger against the leader: copy the leader's ad (the plus button, then Responsive search ad) and change one thing, the first headline or the main benefit, so the next result says why."


def ad_plan(group, rank, ads, action, cur=""):
    """The plan for one ad group's test. `ads` are the report's ad rows (label, numbers, verdict, rate) leader first; `rank` the group's verdict. None when there is nothing to do."""
    meta = group.get("platform") == "meta"
    v = rank["verdict"]
    metric = rank.get("metric")
    leader = next((a for a in ads if a["id"] == rank.get("leader")), None)
    behind = [a for a in ads if a["verdict"] == "behind"]
    if v in ("winner", "partial") and leader and behind:
        pause = "Select %s, then Edit, then Pause." % _names(behind) if not meta else "Switch off %s." % _names(behind)
        steps = [_ad_where(meta, group), pause, _challenger(meta)]
        return {"steps": steps, "worth": _ad_worth(leader, behind, metric, meta)}
    if v == "no_difference":
        ranked = sorted([a for a in ads if a.get("rate") is not None], key=lambda a: -a["rate"])
        if group["adCount"] > 4 and len(ranked) > 3:
            drop = ranked[3:]
            steps = [_ad_where(meta, group), "Keep the three with the best %s (%s) and pause the rest: %s." % (rank["metricLabel"], _names(ranked[:3]), _names(drop)),
                     "Fewer ads share the traffic, so each one gets enough to be judged."]
            return {"steps": steps, "worth": None}
        weakest = ranked[-1:] if len(ranked) >= 2 else []
        steps = ["Leave the ads alone until the test has more to count, or replace the weakest now."]
        if weakest:
            steps = [_ad_where(meta, group), "Replace the weakest, %s: pause it, then add a sharper challenger." % _names(weakest), _challenger(meta)]
        return {"steps": steps, "worth": None}
    return None


def _ad_worth(leader, behind, metric, meta):
    s = _spec(metric) if metric else None
    if not s:
        return None
    rate_key = "convRate" if metric == "convRate" else "ctr"
    lead = leader["numbers"].get(rate_key)
    if lead is None:
        return None
    extra = 0.0
    for a in behind:
        r = a["numbers"].get(rate_key)
        if r is None:
            continue
        extra += a["numbers"][s["of"]] * max(0.0, (lead - r) / 100.0)
    if extra < 1:
        return None
    text = "Moving the traffic of the ads behind onto the leader would add about %s over 30 days (their %s in the last 30 days, at the leader's %s)." % (
        _hits(extra, metric), s["of"], s["label"])
    return text + (" Meta shows the ad it expects to do best more often, so this is a lead, not a fair test." if meta else
                   " This assumes the leader keeps its rate with more traffic, which it usually does not quite.")
