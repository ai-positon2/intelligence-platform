"""Paid Marketing Agents: Test & Experiment Tracker, the report.

Reads a Google Ads account's experiments and the ad groups that run two or more ads (tracker/paid_exp_read.py), a Meta ad account's A/B tests and ad sets that run two or more
ads (tracker/paid_exp_meta_read.py) or one of each, holds each test against the arithmetic in tracker/paid_exp_stats.py and gives every test a verdict and a plain next step, and the
account the list of tests that need a decision.

The account is named the way Account Audit names it (tracker/paid_accounts.py): a 10-digit Google Ads customer id, a Meta ad account id (act_...) or both joined by "+". An MCC is not
read (a test belongs to one account). What is read is saved in the shared Postgres cache
(tracker/paid_gads.py helpers) as one raw read per account per hour, so the report is arithmetic on a saved read and costs nothing to make again: Refresh
is limited per account, a spent quota serves the last saved read of any age with its date, a read with a part missing is kept five minutes only.
Read only: nothing is changed in Google Ads.

Every figure and verdict is arithmetic. No model is asked whether a test won.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading

from . import paid_exp_merge as mg
from . import paid_exp_meta_read as mrd
from . import paid_exp_read as rd
from . import paid_exp_stats as st
from . import paid_gads as base
from . import paid_gads_segments as gads
from . import paid_segments as seg
from .paid_accounts import NAMES, targets as _targets_of

BadRequest = seg.BadRequest

log = logging.getLogger(__name__)

CACHE_VERSION = "2"                                 # bump when the shape of a saved read changes
TTL_S = 3600
PARTIAL_TTL_S = 300
LONG_RUN_DAYS = 28                                  # a running experiment with no difference after this many days is not going to say more
ENDS_SOON_DAYS = 7
MANY_ADS = 4                                        # ad groups with more live ads than this are too many to learn from
SPLIT_SLACK = 0.15                                  # how far an arm's share of impressions may sit from its share of the traffic split before it is flagged
SPLIT_MIN_IMPRESSIONS = 1000
ACTION_ORDER = ("decide", "apply", "end", "pause", "stuck", "check")
NOTES = [
    "A test has a result only when there is enough to count: at least 7 days, and for conversion rates 100 clicks in each arm and 30 conversions together (click rates need 1,000 impressions "
    "in each arm). Until then the verdict is too early and says what is still needed.",
    "Confidence is 95 percent. When several ads in an ad group are held against the best one, each comparison needs a stricter bar so picking the best of many is not read as a win.",
    "No difference is not a tie: it means the data cannot tell the arms apart. The page says the smallest gap this much data could have seen.",
]
NOTES_GOOGLE = [
    "Conversions are Google's own and may be fractional or counted late. Cost per conversion and conversion value are shown but never decide a verdict.",
    "Google rotates ads to favour the one it expects to do better, so a lopsided split between ads in one ad group is normal and is not held against the test.",
    "Experiments are read for the days they ran. Ad variations experiments report no arms in Google's API, so they are listed without a result.",
]
NOTES_META = [
    "A Meta A/B test splits the audience between its cells at random, so two cells are a fair test and are held to the same arithmetic as a Google experiment: the first cell is the control and "
    "the second the trial. A test of three or more cells is compared on its first two. Lift studies are listed without a result.",
    "Meta shows the ad it expects to do best more often, so the ads of one ad set do not share the audience evenly. An ad test between ads in one ad set reads what happened, which is a lead and "
    "not a fair test. For a fair one, run the ads as a Meta A/B test.",
    "What Meta counts as a conversion depends on the campaign's objective (a lead, a purchase or an install), and its link clicks are the clicks used here. A campaign that counts no result "
    "has nothing to compare on conversion rate, so it is judged on click rate.",
    "Meta's A/B tests are read from the ad account's list of tests. A test made under another account of the same business is not listed. The way Meta describes a test's cells could not be tried "
    "against a live account from the development machine, so a test whose cells Meta would not describe is listed as not measured with the reason.",
]


def _read_google(target, *, search_fn, today):
    return rd.read_account(target["id"], search_fn=search_fn, today=today)


def _read_meta(target, *, store, pages_fn, today):
    from . import paid_meta
    pages_fn = pages_fn or (lambda path, params: paid_meta._pages(path, params, store))
    try:
        return mrd.read_account(target["id"], pages=pages_fn, today=today)
    except paid_meta.AdsError as e:
        raise gads.translate(e) from e


def raw_read(targets, *, search_fn=None, now=None, today=None, store=None, meta_pages=None):
    """The raw read of one account: each platform's side, put together (tracker/paid_exp_merge.py). `targets` is [{platform, id, name, currency}], one per platform. Reading the only platform
    failing is raised; with two, the one that could not be read is a warning ("failed" in the read says which and why) and the report is the other's alone. Both failing raises the first error."""
    reads, failed = {}, []
    for t in targets:
        try:
            if t["platform"] == "meta":
                reads["meta"] = _read_meta(t, store=store, pages_fn=meta_pages, today=today)
            else:
                reads["google"] = _read_google(t, search_fn=search_fn, today=today)
        except gads.GadsError as e:
            failed.append((t, e))
    if not reads:
        raise failed[0][1]
    read = mg.merge(reads.get("google"), reads.get("meta"))
    read["failed"] = [{"platform": t["platform"], "kind": e.kind, "error": str(e)} for t, e in failed]
    for t, e in failed:
        read["warnings"] = list(read["warnings"]) + ["%s could not be read (%s). This report is %s alone." % (NAMES[t["platform"]], e, NAMES[next(iter(reads))])]
    accounts = [{"platform": t["platform"], "id": t["id"], "name": t["name"], "currency": t.get("currency") or ""} for t in targets]
    return {"read": read, "accounts": accounts, "account": {"id": "+".join(a["id"] for a in accounts), "name": " + ".join(dict.fromkeys(a["name"] for a in accounts)),
                                                          "currency": accounts[0]["currency"]},
            "readAt": (now or dt.datetime.now(dt.timezone.utc)).isoformat()}


_LOCKS, _LOCKS_GUARD = {}, threading.Lock()


def cache_key(account_id):
    return base.CACHE_V + "exp%s:%s" % (CACHE_VERSION, account_id)


def _usable(hit):
    raw, saved = hit
    return not (raw["read"].get("warnings") and base._age_s(saved) > PARTIAL_TTL_S)


def _quota_failed(raw):
    """True when a platform of the read was not read because its allowance is spent."""
    return any(f.get("kind") == "quota" for f in raw["read"].get("failed") or [])


def _cached(account_id, build, *, force, store):
    """(raw read, saved-at or None), shared between people asking at once; see paid_lp._cached, which this follows (a whole saved read is preferred to a fresh one that has lost a platform
    to its allowance)."""
    key = cache_key(account_id)
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(key, threading.Lock())
    if force:
        base.refresh_guard(store, "exp:%s" % account_id)
        base.forget(key)
    with lock:
        if not force:
            hit = base._entry(store, key, TTL_S)
            if hit is not None and _usable(hit):
                return hit[0], None
        try:
            raw = build()
        except gads.GadsError as err:
            old = base._saved_copy(store, key) if err.kind == "quota" else None
            if old is None:
                raise
            return old[0], old[1]
        if _quota_failed(raw):
            old = base._saved_copy(store, key)
            if old is not None and not _quota_failed(old[0]):
                return old[0], old[1]
        saved = base._now_iso()
        try:
            store.put_gads_cache(key, raw)
        except Exception as err:                          # noqa: BLE001 (a failed save never costs the report)
            log.warning("experiment tracker: could not save the read (%s)", err)
        base._mem_put(key, raw, saved)
        return raw, None


# ── Words ───────────────────────────────────────────────────────────────────

def _pct(v):
    return ("%.1f" % v).rstrip("0").rstrip(".") if v is not None else ""


def _sure(c):
    """"97.3% sure", and "over 99.9% sure" rather than a claim of certainty when the rounding reaches 100."""
    return "over 99.9% sure" if c is not None and c >= 100 else "%s%% sure" % _pct(c)


def _date_text(d):
    """"Oct 14" for a date, or an ISO string."""
    d = rd._date(d) if not isinstance(d, dt.date) else d
    return "%s %d" % (d.strftime("%b"), d.day) if d else ""


def _count(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def _more(wait):
    """"100 more clicks in each arm and 23 more conversions" from a wait record."""
    parts = []
    more = (wait or {}).get("more") or {}
    for key, label in (("clicks", "clicks in each arm"), ("impressions", "impressions in each arm"), ("conversions", "conversions")):
        if more.get(key):
            parts.append("%d more %s" % (more[key], label))
    if (wait or {}).get("days"):
        parts.append(_count(wait["days"], "more day"))
    return " and ".join(parts)


def _label(ad):
    """What an ad is called: the name it was given (a Meta ad has one), else its headlines, else its number."""
    heads = ad.get("headlines") or []
    return ad.get("name") or (" | ".join(heads[:3]) if heads else ("Ad %s" % ad.get("id")))


# ── Experiments ─────────────────────────────────────────────────────────────

def _split_note(control, trial):
    """A warning when the arms did not get the traffic their split promised, or None."""
    sc, stt = control.get("split"), trial.get("split")
    ic, it = control["results"]["total"]["impressions"], trial["results"]["total"]["impressions"]
    if not sc or not stt or ic + it < SPLIT_MIN_IMPRESSIONS:
        return None
    want, got = stt / float(sc + stt), it / float(ic + it)
    if round(abs(got - want), 6) <= SPLIT_SLACK:
        return None
    return ("The trial got %d%% of the impressions against a %d%% share of the traffic split. When one arm runs out of budget or is paused the arms are not comparable, so read the result with care."
            % (round(100 * got), round(100 * want)))


def _experiment_action(e, cmp_, today):
    """{kind, text[, label]}: what to do about the experiment. Kinds: decide, apply, end, wait, stuck, check, manual, record. A Meta test is worded in terms of its cells."""
    meta = e.get("platform") == "meta"
    state = e["state"]
    end = rd._date(e["end"])
    start = rd._date(e["start"])
    control = next((a["name"] for a in e["arms"] if a["control"]), "the first cell")
    trial = next((a["name"] for a in e["arms"] if not a["control"]), "the other cell")
    if state == "setting up":
        return {"kind": "wait", "text": "Not started yet%s." % (" (set to start on %s)" % _date_text(e["start"]) if e["start"] else "")}
    if state in ("ended", "applied"):
        what = "Applied to the campaign." if state == "applied" else "Ended."
        if cmp_ and cmp_["verdict"] in ("winner", "behind", "no_difference"):
            if meta:
                what += {"winner": " %s finished ahead of %s." % (trial, control), "behind": " %s finished behind %s." % (trial, control), "no_difference": " It found no difference."}[cmp_["verdict"]]
            else:
                what += {"winner": " The trial finished ahead.", "behind": " The trial finished behind.", "no_difference": " It found no difference."}[cmp_["verdict"]]
        return {"kind": "record", "text": what}
    if state != "running":
        return {"kind": "record", "text": "Status: %s." % state}
    overdue = bool(end and end < today)
    soon = bool(end and not overdue and (end - today).days <= ENDS_SOON_DAYS)
    lead = ""
    if overdue:
        lead = "Past its end date (%s) and still running. " % _date_text(end)
    elif soon:
        lead = "Ends today. " if end == today else "Ends in %s. " % _count((end - today).days, "day")
    if cmp_ is None:
        if meta:
            why = e.get("reason") or ("This is a %s, and the tracker reads Meta A/B tests only." % (e.get("typeLabel") or "test").lower())
            return {"kind": "manual", "text": lead + "%s Read the result in Meta's Experiments tool." % why}
        return {"kind": "manual", "text": lead + "Google does not report this kind of experiment's arms through its API. Read the result in the Google Ads screen."}
    v = cmp_["verdict"]
    metric = cmp_["metricLabel"]
    both = "%s%% against %s%%" % (_pct(cmp_["control"]), _pct(cmp_["trial"])) if cmp_["control"] is not None and cmp_["trial"] is not None else ""
    enabled = "both cells' ad sets are on and have budget" if meta else "both campaigns are enabled and have budget"
    if v == "winner":
        if meta:
            return {"kind": "decide" if overdue else "apply", "label": "Keep the winner",
                    "text": lead + "%s is ahead of %s on %s (%s, %s). End the test and put the budget behind %s." % (trial, control, metric, both, _sure(cmp_["confidence"]), trial)}
        return {"kind": "decide" if overdue else "apply", "text": lead + "The trial is ahead on %s (%s, %s). Apply it to the campaign, or end the experiment and keep the original." % (metric, both, _sure(cmp_["confidence"]))}
    if v == "behind":
        if meta:
            return {"kind": "decide" if overdue else "end", "text": lead + "%s is behind %s on %s (%s, %s). End the test and put the budget behind %s: waiting will not turn a result this clear." % (
                trial, control, metric, both, _sure(cmp_["confidence"]), control)}
        return {"kind": "decide" if overdue else "end", "text": lead + "The trial is behind on %s (%s, %s). End it: waiting will not turn a result this clear." % (metric, both, _sure(cmp_["confidence"]))}
    if v == "no_difference":
        long_ = start is not None and (today - start).days >= LONG_RUN_DAYS
        gap = " It could only have told apart gaps of %s%% or more." % _pct(cmp_["detectable"]) if cmp_.get("detectable") else ""
        if overdue or long_:
            return {"kind": "decide" if overdue else "end", "text": lead + "No difference on %s after %s.%s End it and keep whichever version is simpler to run." % (
                metric, _count((today - start).days, "day") if start else "this long", gap)}
        return {"kind": "wait", "text": lead + "No difference on %s yet.%s Let it run." % (metric, gap)}
    if v == "too_early":
        wait = cmp_.get("wait") or {}
        need = _more(wait)
        if wait.get("eta") is None:
            return {"kind": "stuck", "text": lead + "Too early to call, and at the current pace an arm will not get there (it needs %s). Check that %s." % (need, enabled)}
        if overdue:
            return {"kind": "decide", "text": lead + "Too early to call%s. End it or extend its dates." % (": it still needs %s" % need if need else "")}
        eta = " At the current pace that is about %s." % _count(wait["eta"], "day") if wait.get("eta") else ""
        return {"kind": "wait", "text": lead + "Too early to call%s.%s Let it run." % (": it needs %s" % need if need else "", eta)}
    return {"kind": "check", "text": lead + "%s Check that %s." % (cmp_["note"], enabled)}


def build_experiment(e, today):
    """One experiment's report row, with its comparison and what to do."""
    arms = []
    for a in e["arms"]:
        res = a.get("results")
        arms.append({"name": a["name"], "control": a["control"], "split": a["split"], "campaigns": a["campaigns"],
                     "total": st.summarise(st.arm(**res["total"])) if res else None,
                     "days": [[d] + v for d, v in sorted(res["days"].items())] if res else []})
    control = next((a for a in e["arms"] if a["control"] and a.get("results")), None)
    trials = [a for a in e["arms"] if not a["control"] and a.get("results")]
    trial = trials[0] if trials else None
    first, last = (rd._date(e["window"][0]), rd._date(e["window"][1])) if e.get("window") else (None, None)
    days = (last - first).days + 1 if first and last else 0
    cmp_ = None
    note = None
    if control and trial:
        cmp_ = st.compare(control["results"]["total"], trial["results"]["total"], days)
        note = _split_note(control, trial)
    start, end = rd._date(e["start"]), rd._date(e["end"])
    spend = round(sum((a["total"] or {}).get("cost", 0.0) for a in arms), 2)
    action = _experiment_action(e, cmp_, today)
    return {"id": e["id"], "platform": e.get("platform", "google"), "reason": e.get("reason") or "", "name": e["name"], "description": e["description"], "state": e["state"], "status": e["status"],
            "type": e["type"], "typeLabel": e["typeLabel"],
            "start": e["start"], "end": e["end"], "daysRunning": (today - start).days if start and start <= today else 0,
            "daysLeft": (end - today).days if end and e["state"] == "running" else None, "overdue": bool(end and e["state"] == "running" and end < today),
            "window": e.get("window"), "clipped": bool(e.get("clipped")), "daysRead": days, "arms": arms, "armCount": len(trials) + (1 if control else 0), "extraTrials": max(0, len(trials) - 1),
            "measured": bool(cmp_), "comparison": cmp_, "verdict": cmp_["verdict"] if cmp_ else "not_measured", "splitNote": note, "spend": spend,
            "action": action}


# ── Ad copy tests ───────────────────────────────────────────────────────────

def _ad_action(group, rank, ads):
    """{kind, text} for an ad group's test."""
    v = rank["verdict"]
    metric = rank["metricLabel"]
    behind = [a for a in ads if a["verdict"] == "behind"]
    leader = next((a for a in ads if a["id"] == rank.get("leader")), None)
    if v == "winner":
        return {"kind": "pause", "text": "%s is ahead of every other ad on %s. Pause or replace the %s behind it, then write a new challenger against it." % (
            leader["label"] if leader else "One ad", metric, _count(len(behind), "ad"))}
    if v == "partial":
        return {"kind": "pause", "text": "%s is ahead of %d of the other ads on %s. Pause or replace the %s that %s behind it." % (
            leader["label"] if leader else "One ad", len(behind), metric, _count(len(behind), "ad"), "is" if len(behind) == 1 else "are")}
    if v == "no_difference":
        if group["adCount"] > MANY_ADS:
            return {"kind": "end", "text": "%d live ads and no difference on %s between them: more ads than a test can learn from. Keep the best two or three and pause the rest." % (group["adCount"], metric)}
        gap = " It could only have told apart gaps of %s%% or more." % _pct(rank["detectable"]) if rank.get("detectable") else ""
        return {"kind": "wait", "text": "No difference on %s between the ads.%s Let it run, or replace the weakest with a sharper challenger." % (metric, gap)}
    if v == "too_early":
        of, need = ("clicks", st.MIN_CLICKS) if rank["metric"] == "convRate" else ("impressions", st.MIN_IMPRESSIONS)
        few = [a for a in ads if a["numbers"][of] < need]
        if not few:
            return {"kind": "wait", "text": "Too early to call: between them the ads have too few conversions to compare (a test needs %d)." % st.MIN_CONVERSIONS}
        return {"kind": "wait", "text": "Too early to call: %s %s not had enough %s yet (%d each)." % (_count(len(few), "ad"), "has" if len(few) == 1 else "have", of, need)}
    return {"kind": "wait", "text": "Fewer than two of these ads were shown in this period, so there is nothing to compare."}


def build_ad_test(group):
    """One ad group's test report row."""
    rank = st.rank_group(group["ads"], rd.ADS_DAYS)
    ads = []
    for a in group["ads"]:
        r = rank["ads"].get(str(a["id"]), {})
        ads.append({"id": a["id"], "label": _label(a), "name": a.get("name") or "", "headlines": a["headlines"], "descriptions": a["descriptions"], "url": a["url"], "strength": a["strength"].lower().replace("_", " "),
                    "type": a["type"], "numbers": st.summarise(st.arm(**{k: a[k] for k in ("impressions", "clicks", "conversions", "cost", "value")})), "verdict": r.get("verdict", "too_early"),
                    "rate": r.get("rate"), "relative": r.get("relative"), "confidence": r.get("confidence")})
    order = {"winner": 0, "partial": 0, "no_difference": 1, "too_early": 2, "behind": 3, "no_data": 4}
    ads.sort(key=lambda a: (0 if a["id"] == rank.get("leader") else 1, order.get(a["verdict"], 5), -a["numbers"]["cost"], str(a["id"])))
    if rank["verdict"] == "no_difference" and rank.get("leader"):
        lead = next(a for a in group["ads"] if str(a["id"]) == rank["leader"])
        elig = [a for a in group["ads"] if rank["ads"][str(a["id"])]["verdict"] != "too_early" and rank["ads"][str(a["id"])]["verdict"] != "no_data"]
        key = st.METRICS[rank["metric"]]["of"]
        gap = st.detectable_gap(st.metric_rate(st.arm(**{k: lead[k] for k in ("impressions", "clicks", "conversions")}), rank["metric"]), min(a[key] for a in elig)) if elig else None
        rank["detectable"] = None if gap is None else round(100 * gap, 1)
    action = _ad_action(group, rank, ads)
    return {"id": group["id"], "platform": group.get("platform", "google"), "name": group["name"], "campaign": group["campaign"], "campaignType": group["campaignType"], "adCount": group["adCount"], "shown": len(ads), "spend": group["cost"],
            "verdict": rank["verdict"], "metric": rank["metric"], "metricLabel": rank["metricLabel"], "leader": rank.get("leader"), "eligible": rank["eligible"], "bar": rank.get("bar"),
            "note": rank["note"], "detectable": rank.get("detectable"), "ads": ads, "action": action}


# ── The whole report ────────────────────────────────────────────────────────

def _needs(items):
    """The tests that need a decision, ordered by what the decision is and then by what is spent."""
    rows = [dict(kind=i["action"]["kind"], title=i["title"], text=i["action"]["text"], ref=i["ref"], spend=i["spend"], **({"label": i["action"]["label"]} if i["action"].get("label") else {}))
            for i in items if i["action"]["kind"] in ACTION_ORDER]
    rows.sort(key=lambda r: (ACTION_ORDER.index(r["kind"]), -r["spend"], r["title"]))
    return rows


def assemble(raw, now=None, today=None):
    read = raw["read"]
    today = today or dt.date.fromisoformat(base._pacific_day())
    exps = [build_experiment(e, today) for e in read["experiments"]]
    tests = [build_ad_test(g) for g in read["adGroups"]]
    by = lambda rows, v: sum(1 for r in rows if r["verdict"] == v)       # noqa: E731
    running = [e for e in exps if e["state"] == "running"]
    needs = _needs([{"action": e["action"], "title": e["name"], "ref": {"type": "experiment", "id": e["id"]}, "spend": e["spend"]} for e in exps] +
                   [{"action": t["action"], "title": "%s, %s" % (t["name"], t["campaign"]), "ref": {"type": "adTest", "id": t["id"]}, "spend": t["spend"]} for t in tests])
    platforms = read.get("platforms") or ["google"]
    notes = list(NOTES) + (NOTES_GOOGLE if "google" in platforms else []) + (NOTES_META if "meta" in platforms else [])
    if read["adTests"] > len(tests):
        notes.append("%d %s run two or more live ads. The %d that spent most in the last 30 days are shown." % (read["adTests"], "ad groups and ad sets" if "meta" in platforms else "ad groups", len(tests)))
    clipped = [e for e in exps if e["clipped"]]
    if clipped:
        notes.append("%s started more than %d days ago, so only the last %d days of %s are read." % (
            _count(len(clipped), "experiment"), rd.MAX_LOOKBACK_DAYS, rd.MAX_LOOKBACK_DAYS, "it" if len(clipped) == 1 else "each"))
    if any(e["extraTrials"] for e in exps):
        notes.append("An experiment with more than one trial arm is compared on its first trial arm only.")
    return {
        "account": raw["account"], "accounts": raw.get("accounts") or [], "window": "the last 30 days",
        "summary": {"platforms": platforms, "experiments": len(exps), "running": len(running), "ended": sum(1 for e in exps if e["state"] == "ended"), "applied": sum(1 for e in exps if e["state"] == "applied"),
                    "settingUp": sum(1 for e in exps if e["state"] == "setting up"), "measured": sum(1 for e in exps if e["measured"]),
                    "winners": by(running, "winner"), "behind": by(running, "behind"), "noDifference": by(running, "no_difference"), "tooEarly": by(running, "too_early"),
                    "overdue": sum(1 for e in exps if e["overdue"]), "endsSoon": sum(1 for e in running if e["daysLeft"] is not None and 0 <= e["daysLeft"] <= ENDS_SOON_DAYS),
                    "adTests": read["adTests"], "adTestsShown": len(tests), "adConclusive": sum(1 for t in tests if t["verdict"] in ("winner", "partial")),
                    "adNoDifference": by(tests, "no_difference"), "adTooEarly": by(tests, "too_early"), "adSpend": round(sum(t["spend"] for t in tests), 2),
                    "experimentSpend": round(sum(e["spend"] for e in exps), 2), "needsAction": len(needs)},
        "experiments": exps, "adTests": tests, "needs": needs, "warnings": read["warnings"], "notes": notes,
        "unread": _unread(read), "readAt": raw.get("readAt"),
        "generatedAt": (now or dt.datetime.now(dt.timezone.utc)).isoformat()}


def _unread(read):
    """The parts of the read that could not be read, named by platform."""
    out = [(NAMES["meta"] + ": " + k[5:]) if k.startswith("meta:") else (NAMES["google"] + ": " + k) for k, v in read["ok"].items() if not v]
    return out + ["%s: the whole account" % NAMES[f["platform"]] for f in read.get("failed") or []]


def report_for(account, *, force=False, store=None, accounts_fn=None, search_fn=None, now=None, today=None, meta_pages=None):
    """The report for the account `account` (see tracker/paid_accounts.py). `force` (Refresh) skips the saved read, at most once per paid_gads.REFRESH_COOLDOWN_S per account. Raises
    BadRequest for a bad or unknown account, paid_gads_segments.GadsError for a platform that would not answer (with two platforms, one that would not is a warning and the report is the
    other's)."""
    from . import paid_store
    store = store or paid_store
    key, targets = _targets_of(account, store=store, accounts_fn=accounts_fn)
    raw, old = _cached(key, lambda: raw_read(targets, search_fn=search_fn, now=now, today=today, store=store, meta_pages=meta_pages), force=force, store=store)
    out = assemble(raw, now=now, today=today)
    if old:
        out["stale"] = [{"platform": a["platform"], "savedAt": old} for a in raw["accounts"]]
    return out
