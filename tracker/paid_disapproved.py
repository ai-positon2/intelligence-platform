"""Paid Marketing Agents: Disapproved Ads Tracker, the reading and the report.

For a Google Ads account (or every account of an MCC) this reads the enabled ads that Google has disapproved or limited, with the policy topics it named, and then, only for the
ad groups those ads sit in, how many approved ads each has (the cover) and its clicks and cost by week for the last five whole weeks (the weight). The ads are the point (a
failure there is raised); the cover and the weight are soft: a refusal leaves that part out and says so. The arithmetic, the findings and the plan are
tracker/paid_disapproved_math.py and paid_disapproved_plan.py. Google Ads only. Nothing is changed in the account.

The read is cached like the other Google agents (tracker/paid_geo.py _cached): one read per account a day-ish, Refresh limited, a spent quota served from the last saved read with
its date.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import paid_brandis_math as bis
from . import paid_disapproved_math as m
from . import paid_gads_segments as gads
from . import paid_segments as seg

BadRequest = seg.BadRequest
MAX_MEMBERS = seg.MAX_MEMBERS
MAX_GROUPS = 300                # ad groups whose cover and weight are read in an account; the rest are said not to be checked
BATCH = 100                     # ad group ids in one query
MAX_ADS = 2000                  # flagged ads kept for an account (a read past it says so)

APPROVAL = "'DISAPPROVED', 'APPROVED_LIMITED', 'AREA_OF_INTEREST_ONLY'"
GAQL_ADS = ("SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, ad_group_ad.ad.id, ad_group_ad.ad.type, ad_group_ad.policy_summary.approval_status, "
            "ad_group_ad.policy_summary.review_status, ad_group_ad.policy_summary.policy_topic_entries, ad_group_ad.ad.final_urls, ad_group_ad.ad.responsive_search_ad.headlines "
            "FROM ad_group_ad WHERE campaign.status = ENABLED AND ad_group.status = ENABLED AND ad_group_ad.status = ENABLED "
            "AND ad_group_ad.policy_summary.approval_status IN (%s)" % APPROVAL)


def gaql_cover(ids):
    return ("SELECT ad_group.id, ad_group_ad.ad.id FROM ad_group_ad WHERE campaign.status = ENABLED AND ad_group.status = ENABLED AND ad_group_ad.status = ENABLED "
            "AND ad_group_ad.policy_summary.approval_status IN ('APPROVED', 'APPROVED_LIMITED', 'AREA_OF_INTEREST_ONLY') AND ad_group.id IN (%s)" % ", ".join(ids))


def gaql_weekly(ids, first, last):
    return ("SELECT ad_group.id, segments.week, metrics.clicks, metrics.cost_micros FROM ad_group WHERE segments.date BETWEEN '%s' AND '%s' AND ad_group.id IN (%s)" % (first, last, ", ".join(ids)))


def weeks(today):
    return bis.weeks(today, m.WEEKS)


# ── Reading the API's rows ──────────────────────────────────────────────────

def _get(row, *path, default=None):
    for p in path:
        if not isinstance(row, dict):
            return default
        row = row.get(p)
    return default if row is None else row


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def read_ads(api_rows):
    """The flagged ads (the shape tracker/paid_disapproved_math.py reads) from Google's answer. A row without an ad or an ad group is dropped."""
    out = []
    for r in api_rows:
        ad = _get(r, "adGroupAd", "ad", default={}) or {}
        aid, gid = str(ad.get("id") or ""), str(_get(r, "adGroup", "id", default="") or "")
        if not aid or not gid:
            continue
        pol = _get(r, "adGroupAd", "policySummary", default=None) or {}
        topics = []
        for e in pol.get("policyTopicEntries") or []:
            t = str(e.get("topic") or "")
            if not t or any(x["t"] == t for x in topics):
                continue
            ev = []
            for x in e.get("evidences") or []:
                for text in _get(x, "textList", "texts", default=[]) or []:
                    if text and text not in ev and len(ev) < 3:
                        ev.append(str(text))
            topics.append({"t": t, "ty": str(e.get("type") or "").upper(), "ev": ev})
        heads = [str(h.get("text") or "") for h in _get(ad, "responsiveSearchAd", "headlines", default=[]) or [] if h.get("text")][:3]
        out.append({"id": aid, "campaignId": str(_get(r, "campaign", "id", default="") or ""), "campaign": _get(r, "campaign", "name", default="") or "",
                    "adGroupId": gid, "adGroup": _get(r, "adGroup", "name", default="") or "", "type": str(ad.get("type") or ""),
                    "approval": str(pol.get("approvalStatus") or ""), "review": str(pol.get("reviewStatus") or ""), "topics": topics, "headlines": heads,
                    "url": (ad.get("finalUrls") or [""])[0]})
    return out


def read_cover(api_rows):
    """{ad group id: approved enabled ads} from the cover read; only groups that have one appear (the caller fills the rest with 0)."""
    out = {}
    for r in api_rows:
        gid = str(_get(r, "adGroup", "id", default="") or "")
        if gid:
            out[gid] = out.get(gid, 0) + 1
    return out


def read_weekly(api_rows, wk):
    """{ad group id: [{clicks, cost}] one per whole week, oldest first}: a week Google gave no row for is a week of nothing."""
    keys = [w[0].isoformat() for w in wk]
    out = {}
    for r in api_rows:
        gid = str(_get(r, "adGroup", "id", default="") or "")
        week = str(_get(r, "segments", "week", default="") or "")
        if not gid or week not in keys:
            continue
        slot = out.setdefault(gid, [{"clicks": 0.0, "cost": 0.0} for _ in keys])[keys.index(week)]
        mt = r.get("metrics") or {}
        slot["clicks"] += _num(mt.get("clicks"))
        slot["cost"] += _num(mt.get("costMicros")) / 1e6
    return out


def _batches(ids):
    for n in range(0, len(ids), BATCH):
        yield ids[n:n + BATCH]


def read_account(account_id, today, search):
    """{ads, cover, weekly, warnings, truncated}: one account. The ads are raised on failure; the cover and the weight are soft."""
    ads = read_ads(search(account_id, GAQL_ADS))
    truncated = len(ads) > MAX_ADS
    ads = ads[:MAX_ADS]
    warnings, cover, weekly = [], None, None
    if not ads:
        return {"ads": [], "cover": {}, "weekly": {}, "warnings": [], "truncated": False}
    groups = sorted({a["adGroupId"] for a in ads})
    checked = groups[:MAX_GROUPS]
    wk = weeks(today)
    try:
        counts = {}
        for ids in _batches(checked):
            counts.update(read_cover(search(account_id, gaql_cover(ids))))
        cover = {g: (counts.get(g, 0) if g in checked else None) for g in groups}
        if len(groups) > MAX_GROUPS:
            warnings.append("Only the first %d of %d ad groups with a flagged ad were checked for another approved ad." % (MAX_GROUPS, len(groups)))
    except gads.GadsError as e:
        warnings.append("Whether other ads cover the flagged ones could not be read (%s)." % e)
    try:
        weekly = {}
        for ids in _batches(checked):
            weekly.update(read_weekly(search(account_id, gaql_weekly(ids, wk[0][0], wk[-1][1])), wk))
    except gads.GadsError as e:
        weekly = None
        warnings.append("What the ad groups used to bring could not be read (%s)." % e)
    if truncated:
        warnings.append("More than %d flagged ads: the first %d are shown." % (MAX_ADS, MAX_ADS))
    return {"ads": ads, "cover": cover, "weekly": weekly, "warnings": warnings, "truncated": truncated}


def google_raw(account, today, *, search_fn=None, workers=6):
    """{accounts: [{id, name, currency, ads, cover, weekly, warnings}], warnings, members}: the raw read of one account or an MCC's accounts together. An account whose ads cannot be read
    is left out and said so; every one failing is raised."""
    search = search_fn or gads.search
    members = account.get("members") or [account]

    def run(member):
        try:
            return member, read_account(member["id"], today, search), None
        except gads.GadsError as e:
            return member, None, e
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(members)))) as ex:
        done = list(ex.map(run, members))
    ok = [(mem, r) for mem, r, e in done if r is not None]
    if not ok:
        raise next(e for _m, _r, e in done if e)
    return {"accounts": [dict(r, id=mem["id"], name=mem["name"], currency=mem.get("currency") or account.get("currency") or "USD") for mem, r in ok],
            "warnings": ["%s could not be read: %s" % (mem["name"], e) for mem, r, e in done if e],
            "members": [mem["name"] for mem in members] if account.get("members") else []}


# ── The report ──────────────────────────────────────────────────────────────

_RANK = {"stopped": 0, "disapproved": 1, "limited": 2, "clear": 3}


def assemble(raw, account, today):
    accounts, warnings = [], list(raw.get("warnings") or [])
    for a in raw["accounts"]:
        result = m.analyse(a["ads"], a["cover"], a["weekly"], currency=a["currency"])
        accounts.append(dict(result, id=a["id"], name=a["name"], currency=a["currency"]))
        warnings += ["%s: %s" % (a["name"], w) if account.get("members") else w for w in a.get("warnings") or []]
    accounts.sort(key=lambda a: (_RANK[a["status"]], -a["counts"]["stoppedGroups"], -a["counts"]["disapproved"], -a["counts"]["limited"], a["name"].lower()))
    counts = {k: 0 for k in _RANK}
    for a in accounts:
        counts[a["status"]] += 1
    wk = weeks(today)
    return {
        "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or "USD", "platform": "google",
                    "isGroup": bool(account.get("members")), "accountCount": len(account.get("members") or [])},
        "window": {"from": wk[0][0].isoformat(), "to": wk[-1][1].isoformat(), "weeks": len(wk)},
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "warnings": warnings, "accounts": accounts,
        "summary": {"counts": counts, "accounts": len(accounts), "needsLook": counts["stopped"] + counts["disapproved"],
                    "stoppedGroups": sum(a["counts"]["stoppedGroups"] for a in accounts), "disapproved": sum(a["counts"]["disapproved"] for a in accounts),
                    "limited": sum(a["counts"]["limited"] for a in accounts), "waiting": sum(a["counts"]["appeal"] + a["counts"]["reviewing"] for a in accounts),
                    "lostClicks": sum((g["weight"] or {}).get("lostClicks") or 0 for a in accounts for g in a["deadGroups"])},
    }


def report_for(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, force=False, store=None, today=None):
    """The Disapproved Ads report for a Google Ads account or an MCC the root MCC holds. `force` (Refresh) skips the saved read, at most once per
    paid_gads.REFRESH_COOLDOWN_S for the account."""
    from . import paid_geo, paid_store
    store = store or paid_store
    day = today or dt.date.today()
    account = seg.resolve_target(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn)
    raw, stale = paid_geo._cached("disapproved", account["id"], "ads", day, day, lambda: google_raw(account, day, search_fn=search_fn), force=force, store=store, now=now)
    report = assemble(raw, account, day)
    if raw.get("readAt"):
        report["readAt"] = raw["readAt"]
    return dict(report, stale=stale) if stale else report
