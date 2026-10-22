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
from . import paid_disapproved_extra as ex
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


def _soft(warnings, what, fn, default):
    """fn() or `default`, with a sentence in `warnings` saying what could not be read when Google refused."""
    try:
        return fn()
    except gads.GadsError as e:
        warnings.append("%s could not be read (%s)." % (what, e))
        return default


def read_account(account_id, today, search):
    """{ads, cover, weekly, warnings, truncated}: one account. The ads are raised on failure; the Performance Max assets, the keywords, the cover and the weight are soft.
    `ads` holds every flagged item (ads, assets, keywords); `cover` is {ad group id or "pmax:<asset group>:<FIELD>": approved ones}."""
    ads = read_ads(search(account_id, GAQL_ADS))
    warnings = []
    assets = _soft(warnings, "The assets of Performance Max asset groups", lambda: ex.read_assets(search(account_id, ex.GAQL_ASSETS)), [])
    kws = _soft(warnings, "The disapproved keywords", lambda: ex.read_keywords(search(account_id, ex.GAQL_KEYWORDS)), [])
    items = ads + assets + kws
    truncated = len(items) > MAX_ADS
    items = items[:MAX_ADS]
    if not items:
        return {"ads": [], "cover": {}, "weekly": {}, "warnings": warnings, "truncated": False}
    ad_groups = sorted({i["adGroupId"] for i in items if i.get("kind", "ad") == "ad"})
    asset_groups = sorted({i["adGroupId"].split(":")[1] for i in items if i.get("kind") == "asset"})
    checked = ad_groups[:MAX_GROUPS]
    wk = weeks(today)
    cover, weekly = None, None
    try:
        counts, acounts = {}, {}
        for ids in _batches(checked):
            counts.update(read_cover(search(account_id, gaql_cover(ids))))
        for ids in _batches(asset_groups):
            acounts.update(ex.read_asset_cover(search(account_id, ex.gaql_asset_cover(ids))))
        cover = {g: (counts.get(g, 0) if g in checked else None) for g in ad_groups}
        cover.update({i["adGroupId"]: acounts.get(i["adGroupId"], 0) for i in items if i.get("kind") == "asset"})
        if len(ad_groups) > MAX_GROUPS:
            warnings.append("Only the first %d of %d ad groups with a flagged ad were checked for another approved ad." % (MAX_GROUPS, len(ad_groups)))
    except gads.GadsError as e:
        warnings.append("Whether other ads cover the flagged ones could not be read (%s)." % e)
    try:
        weekly = {}
        for ids in _batches(checked):
            weekly.update(read_weekly(search(account_id, gaql_weekly(ids, wk[0][0], wk[-1][1])), wk))
        by_group = {}
        for i in items:
            if i.get("kind") == "asset":
                by_group.setdefault(i["adGroupId"].split(":")[1], []).append(i["adGroupId"])
        for ids in _batches(asset_groups):
            weekly.update(ex.read_asset_weekly(search(account_id, ex.gaql_asset_weekly(ids, wk[0][0], wk[-1][1])), wk, {g: sorted(set(k)) for g, k in by_group.items()}))
    except gads.GadsError as e:
        weekly = None
        warnings.append("What the ad groups used to bring could not be read (%s)." % e)
    if truncated:
        warnings.append("More than %d flagged items: the first %d are shown." % (MAX_ADS, MAX_ADS))
    return {"ads": items, "cover": cover, "weekly": weekly, "warnings": warnings, "truncated": truncated}


def read_meta_account(act, pages):
    """{ads, cover, weekly, warnings, truncated} for a Meta ad account. `pages(path, params)` returns the list Meta's answer holds (all its pages). The rejected ads are raised on failure;
    the active ads that cover them are soft. Meta's clicks per ad set are not read, so nothing is sized."""
    path, params = ex.meta_ads_request(act)
    items = ex.read_meta_ads(pages(path, params))
    warnings, cover = [], None
    if not items:
        return {"ads": [], "cover": {}, "weekly": None, "warnings": [], "truncated": False}
    truncated = len(items) > MAX_ADS
    items = items[:MAX_ADS]
    try:
        path, params = ex.meta_active_request(act)
        counts = ex.read_meta_cover(pages(path, params))
        cover = {i["adGroupId"]: counts.get(i["adGroupId"], 0) for i in items}
    except Exception as e:                                                          # noqa: BLE001 (Meta's errors have their own class; a soft read never costs the report)
        warnings.append("Whether other active ads cover the rejected ones could not be read (%s)." % e)
    warnings.append("Meta's clicks per ad set are not read, so what a stopped ad set used to bring is not sized.")
    if truncated:
        warnings.append("More than %d flagged ads: the first %d are shown." % (MAX_ADS, MAX_ADS))
    return {"ads": items, "cover": cover, "weekly": None, "warnings": warnings, "truncated": truncated}


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
TOP_PATTERNS = 5


def ages_of(issues):
    """{fingerprint: {firstSeen, reopened}} of the open issues the monitor keeps: what gives a stopped ad group and a policy its age."""
    return {i["fingerprint"]: {"firstSeen": i["firstSeen"], "reopened": i.get("reopened") or 0} for i in issues if i.get("status") == "open"}


def fix_times(issues):
    """How long the problems the monitor saw were open before they were fixed: {fixed, median, longest} in days over the resolved issues, or None when none was fixed yet."""
    from . import paid_monitor
    days = []
    for i in issues:
        if i.get("status") == "resolved" and i.get("resolvedAt"):
            d = paid_monitor.age_days(i["firstSeen"], paid_monitor._parse(i["resolvedAt"]))
            if d is not None:
                days.append(d)
    if not days:
        return None
    return {"fixed": len(days), "median": m.median(days), "longest": max(days)}


def patterns(accounts):
    """The policies that flagged ads in more than one account, the most accounts first: [{topic, label, accounts: [names], ads}]. The same policy in many accounts is usually one cause:
    a policy change, or one piece of ad copy that is shared between clients."""
    by = {}
    for a in accounts:
        for t in a["topics"]:
            if t["topic"] == m.NO_TOPIC:
                continue
            p = by.setdefault(t["topic"], {"topic": t["topic"], "label": t["label"], "accounts": [], "ads": 0})
            p["accounts"].append(a["name"])
            p["ads"] += t["ads"]
    out = [p for p in by.values() if len(p["accounts"]) >= 2]
    out.sort(key=lambda p: (-len(p["accounts"]), -p["ads"], p["label"].lower()))
    return out[:TOP_PATTERNS]


def assemble(raw, account, today, issues=None, now=None):
    """The report from the raw read. `issues` are the monitor's saved ones (paid_monitor_store.issues_all): they give the age of what is open, how often it came back and how long fixes took."""
    accounts, warnings = [], list(raw.get("warnings") or [])
    ages = ages_of(issues) if issues is not None else None
    for a in raw["accounts"]:
        result = m.analyse(a["ads"], a["cover"], a["weekly"], currency=a["currency"], account_id=a["id"], ages=ages, now=now if issues is not None else None)
        accounts.append(dict(result, id=a["id"], name=a["name"], currency=a["currency"], platform=a.get("platform") or "google"))
        warnings += ["%s: %s" % (a["name"], w) if account.get("members") else w for w in a.get("warnings") or []]
    accounts.sort(key=lambda a: (_RANK[a["status"]], -a["counts"]["stoppedGroups"], -a["counts"]["disapproved"], -a["counts"]["keywords"], -a["counts"]["limited"], a["name"].lower()))
    counts = {k: 0 for k in _RANK}
    for a in accounts:
        counts[a["status"]] += 1
    wk = weeks(today)
    return {
        "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or "USD", "platform": account.get("platform") or "google",
                    "isGroup": bool(account.get("members")), "accountCount": len(account.get("members") or [])},
        "window": {"from": wk[0][0].isoformat(), "to": wk[-1][1].isoformat(), "weeks": len(wk)},
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "warnings": warnings, "accounts": accounts,
        "patterns": patterns(accounts), "fixTimes": fix_times(issues or []) if issues is not None else None, "agesKnown": issues is not None,
        "summary": {"counts": counts, "accounts": len(accounts), "needsLook": counts["stopped"] + counts["disapproved"],
                    "stoppedGroups": sum(a["counts"]["stoppedGroups"] for a in accounts), "disapproved": sum(a["counts"]["disapproved"] for a in accounts),
                    "keywords": sum(a["counts"]["keywords"] for a in accounts), "limited": sum(a["counts"]["limited"] for a in accounts),
                    "waiting": sum(a["counts"]["appeal"] + a["counts"]["reviewing"] for a in accounts),
                    "lostClicks": sum((g["weight"] or {}).get("lostClicks") or 0 for a in accounts for g in a["deadGroups"])},
    }


def meta_raw(act, *, pages_fn, account_fn):
    """The raw read of one Meta ad account in the shape google_raw gives, or the error of the ads read."""
    acct = account_fn(act)
    got = read_meta_account(act, pages_fn)
    return {"accounts": [dict(got, id=act, name=acct.get("name") or act, currency=acct.get("currency") or "USD", platform="meta")], "warnings": [], "members": []}


def report_for(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, force=False, store=None, today=None, mon=None, meta_pages_fn=None, meta_account_fn=None, asof=None):
    """The Disapproved Ads report for a Google Ads account or an MCC the root MCC holds, or for a Meta ad account (act_...). `force` (Refresh) skips the saved read, at most once per
    paid_gads.REFRESH_COOLDOWN_S for the account. `mon` is the monitors' store: with it each stopped ad group and policy has its age (counted to `asof`, now by default), how often it came back and how long fixes took."""
    from . import paid_geo, paid_meta, paid_store
    store = store or paid_store
    day = today or dt.date.today()
    stamp = asof or dt.datetime.now(dt.timezone.utc)
    if paid_meta.is_meta(str(account_id or "")):
        act = paid_meta.norm_id(account_id)
        pages = meta_pages_fn or (lambda path, params: paid_meta._pages(path, params, store))
        acct_fn = meta_account_fn or (lambda a: paid_meta.account_by_id(store, a) or {})
        account = {"id": act, "name": (acct_fn(act) or {}).get("name") or act, "currency": (acct_fn(act) or {}).get("currency") or "USD", "platform": "meta"}
        raw, stale = paid_geo._cached("disapproved", act, "ads", day, day, lambda: meta_raw(act, pages_fn=pages, account_fn=acct_fn), force=force, store=store, now=now)
    else:
        account = seg.resolve_target(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn)
        raw, stale = paid_geo._cached("disapproved", account["id"], "ads", day, day, lambda: google_raw(account, day, search_fn=search_fn), force=force, store=store, now=now)
    warnings, issues = [], None
    if mon is not None:
        try:
            issues = mon.issues_all("disapproved")
        except Exception as e:                                                       # noqa: BLE001
            warnings.append("The history of what was flagged before could not be read (%s), so no age is shown." % e)
    report = assemble(raw, account, day, issues=issues, now=stamp)
    report["warnings"] = warnings + report["warnings"]
    if raw.get("readAt"):
        report["readAt"] = raw["readAt"]
    return dict(report, stale=stale) if stale else report
