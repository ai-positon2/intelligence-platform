"""Paid Marketing Agents: Keyword Visibility Tracker, the reading and the report.

For a Google Ads account (or every account of an MCC) this reads the highest-spend Search keywords of the last 28 days (always the top MAX_READ, so what the team saves for how many to track is
applied to the saved read), each one's last five whole weeks (impressions, cost, the shares at the top of the page and lost to rank), the Quality Score Google kept for each week and, softly, the
auction insights of the live Search campaigns by week; the rules (tracker/paid_kvis_math.py) hold the last whole week against the four before it. Nothing is changed in an account.

The keywords and their weeks are the point (a failure there is raised); the Quality Score history and the auction insights are soft (a refusal leaves those checks out and says so). Every
warning is kept at the top of the read, so a read that lost a part is shown but never saved for hours (tracker/paid_geo.py _cached). Google only: Meta has no keywords. The field names are the
API's; none of the new queries has been run against the live service from here.
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import paid_brandis_math as bm
from . import paid_gads_segments as gads
from . import paid_kvis_math as math
from . import paid_kvis_plan as plan
from . import paid_segments as seg

BadRequest = seg.BadRequest
MAX_READ = 100                      # keywords always read; the team's number to track cuts the saved read
ID_CHUNK = 100
AGENT = "kvis"

_KW = ("ad_group.id, ad_group_criterion.criterion_id")
GAQL_TOP = ("SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, ad_group_criterion.criterion_id, ad_group_criterion.keyword.text, ad_group_criterion.keyword.match_type, "
            "ad_group_criterion.quality_info.quality_score, metrics.cost_micros FROM keyword_view WHERE campaign.advertising_channel_type = 'SEARCH' AND campaign.status = ENABLED "
            "AND ad_group.status = ENABLED AND ad_group_criterion.status = ENABLED AND ad_group_criterion.negative = FALSE AND segments.date BETWEEN '%s' AND '%s' "
            "AND metrics.cost_micros > 0 ORDER BY metrics.cost_micros DESC LIMIT " + str(MAX_READ))
GAQL_WEEKS = ("SELECT " + _KW + ", segments.week, metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.search_impression_share, "
              "metrics.search_top_impression_share, metrics.search_absolute_top_impression_share, metrics.search_rank_lost_impression_share FROM keyword_view "
              "WHERE segments.date BETWEEN '%s' AND '%s' AND ad_group_criterion.criterion_id IN (%s)")
GAQL_QS = ("SELECT " + _KW + ", segments.week, metrics.historical_quality_score FROM keyword_view WHERE segments.date BETWEEN '%s' AND '%s' AND ad_group_criterion.criterion_id IN (%s)")
GAQL_AUCTION = ("SELECT campaign.id, campaign.name, segments.week, segments.auction_insight_domain, metrics.auction_insight_search_overlap_rate, metrics.auction_insight_search_impression_share, "
                "metrics.auction_insight_search_outranking_share, metrics.auction_insight_search_position_above_rate, metrics.auction_insight_search_top_impression_percentage "
                "FROM campaign WHERE campaign.advertising_channel_type = 'SEARCH' AND campaign.status = ENABLED AND segments.date BETWEEN '%s' AND '%s'")


def _get(row, *path, default=None):
    for p in path:
        if not isinstance(row, dict):
            return default
        row = row.get(p)
    return default if row is None else row


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x == x else None


def key_of(ad_group_id, criterion_id):
    return "%s-%s" % (ad_group_id, criterion_id)


# ── Reading Google's rows ───────────────────────────────────────────────────

def read_top(rows):
    """[{id, campaignId, campaign, adGroupId, adGroup, criterionId, text, match, qs, cost}], highest spend first."""
    out = []
    for r in rows:
        crit = r.get("adGroupCriterion") or {}
        gid, cid = str(_get(r, "adGroup", "id", default="") or ""), str(crit.get("criterionId") or "")
        text = _get(crit, "keyword", "text", default="") or ""
        if not gid or not cid or not text:
            continue
        micros = _num(_get(r, "metrics", "costMicros")) or 0.0
        out.append({"id": key_of(gid, cid), "campaignId": str(_get(r, "campaign", "id", default="") or ""), "campaign": _get(r, "campaign", "name", default="") or "", "adGroupId": gid,
                    "adGroup": _get(r, "adGroup", "name", default="") or "", "criterionId": cid, "text": text, "match": _get(crit, "keyword", "matchType", default="") or "",
                    "qs": _num(_get(crit, "qualityInfo", "qualityScore")), "cost": round(micros / 1e6, 2)})
    out.sort(key=lambda k: (-k["cost"], k["text"].lower()))
    return out


def read_weeks(rows):
    """{keyword id: [{week, impressions, clicks, cost, conv, share, top, absTop, lostRank}]}."""
    out = {}
    for r in rows:
        crit = r.get("adGroupCriterion") or {}
        gid, cid, week = str(_get(r, "adGroup", "id", default="") or ""), str(crit.get("criterionId") or ""), str(_get(r, "segments", "week", default="") or "")
        if not gid or not cid or not week:
            continue
        m = r.get("metrics") or {}
        out.setdefault(key_of(gid, cid), []).append({"week": week, "impressions": int(_num(m.get("impressions")) or 0), "clicks": int(_num(m.get("clicks")) or 0),
                                                    "cost": round((_num(m.get("costMicros")) or 0.0) / 1e6, 2), "conv": _num(m.get("conversions")) or 0.0, "share": _num(m.get("searchImpressionShare")),
                                                    "top": _num(m.get("searchTopImpressionShare")), "absTop": _num(m.get("searchAbsoluteTopImpressionShare")),
                                                    "lostRank": _num(m.get("searchRankLostImpressionShare"))})
    return out


def read_qs(rows):
    """{keyword id: {week: Quality Score}}: the score Google kept for each week (no score is left out, and a score of 0 is no score)."""
    out = {}
    for r in rows:
        crit = r.get("adGroupCriterion") or {}
        gid, cid, week = str(_get(r, "adGroup", "id", default="") or ""), str(crit.get("criterionId") or ""), str(_get(r, "segments", "week", default="") or "")
        v = _num(_get(r, "metrics", "historicalQualityScore"))
        if gid and cid and week and v:
            out.setdefault(key_of(gid, cid), {})[week] = v
    return out


def read_auction(rows):
    """[{campaignId, campaign, domain, week, overlap, outranking, above, topPct, share}]: the auction insights by campaign, domain and week."""
    out = []
    for r in rows:
        dom = str(_get(r, "segments", "auctionInsightDomain", default="") or "").strip()
        week = str(_get(r, "segments", "week", default="") or "")
        cid = str(_get(r, "campaign", "id", default="") or "")
        if not dom or not week or not cid:
            continue
        m = r.get("metrics") or {}
        out.append({"campaignId": cid, "campaign": _get(r, "campaign", "name", default="") or "", "domain": dom, "week": week, "overlap": _num(m.get("auctionInsightSearchOverlapRate")),
                    "outranking": _num(m.get("auctionInsightSearchOutrankingShare")), "above": _num(m.get("auctionInsightSearchPositionAboveRate")),
                    "topPct": _num(m.get("auctionInsightSearchTopImpressionPercentage")), "share": _num(m.get("auctionInsightSearchImpressionShare"))})
    return out


def _soft(warnings, what, fn, default):
    try:
        return fn()
    except gads.GadsError as e:
        warnings.append("%s could not be read (%s), so that check was skipped." % (what, e))
        return default


LIMIT_AUCTION = ("Auction insights are not available to this app: Google Ads refuses the auction insight metrics to its developer access, so competitors are not checked. "
                 "The keywords are still followed week by week. The same metrics are in the Auction insights report in Google Ads.")


def _auction(account_id, search, first, last, warnings, limits):
    """The auction insights, or [] with a standing limit when Google refuses the metrics to this app's developer access (a 403 does not pass: asking again changes nothing), or a warning on any other failure."""
    try:
        return read_auction(search(account_id, GAQL_AUCTION % (first, last)))
    except gads.GadsError as e:
        if e.kind == "permission":
            if LIMIT_AUCTION not in limits:
                limits.append(LIMIT_AUCTION)
            return []
        warnings.append("The auction insights could not be read (%s), so that check was skipped." % e)
        return []


def _ids(keywords):
    return sorted({k["criterionId"] for k in keywords}, key=int)


def read_account(account_id, search, today=None):
    """{keywords: [...with rows and qsWeeks], auction, brandIds, today, warnings}: one Google Ads account. The keywords and their weeks are raised on failure; the rest is soft."""
    today = today or dt.date.today()
    wk = bm.weeks(today, math.WEEKS)
    first, last = wk[0][0].isoformat(), wk[-1][1].isoformat()
    top_first = (wk[-1][1] - dt.timedelta(days=27)).isoformat()
    keywords = read_top(search(account_id, GAQL_TOP % (top_first, last)))
    warnings = []
    weeks, qs = {}, {}
    ids = _ids(keywords)
    for i in range(0, len(ids), ID_CHUNK):
        chunk = ", ".join(ids[i:i + ID_CHUNK])
        weeks.update(read_weeks(search(account_id, GAQL_WEEKS % (first, last, chunk))))
        got = _soft(warnings, "The Quality Score history", lambda chunk=chunk: read_qs(search(account_id, GAQL_QS % (first, last, chunk))), {})
        for kid, v in got.items():
            qs.setdefault(kid, {}).update(v)
    for k in keywords:
        k["rows"] = weeks.get(k["id"], [])
        k["qsWeeks"] = qs.get(k["id"], {})
    limits = []
    auction = _auction(account_id, search, first, last, warnings, limits)
    names = {a["campaignId"]: a["campaign"] for a in auction}
    names.update({k["campaignId"]: k["campaign"] for k in keywords})
    return {"keywords": keywords, "auction": auction, "brandIds": sorted(cid for cid, n in names.items() if bm.is_brand(n)), "today": today.isoformat(), "warnings": warnings, "limits": limits}


def google_raw(account, *, search_fn=None, workers=6, today=None):
    """{accounts: [{id, name, currency, platform, ...the read}], warnings, members}: one account or an MCC's accounts. An account whose keywords cannot be read is left out and said so; every one
    failing is raised. Every soft warning is also up here (prefixed with the account's name for an MCC)."""
    search = search_fn or gads.search
    members = account.get("members") or [account]

    def run(member):
        try:
            return member, read_account(member["id"], search, today), None
        except gads.GadsError as e:
            return member, None, e
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(members)))) as pool:
        done = list(pool.map(run, members))
    ok = [(mem, r) for mem, r, e in done if r is not None]
    if not ok:
        raise next(e for _m, _r, e in done if e)
    many = bool(account.get("members"))
    return {"accounts": [dict(r, id=mem["id"], name=mem["name"], currency=mem.get("currency") or account.get("currency") or "USD", platform="google") for mem, r in ok],
            "warnings": ["%s could not be read: %s" % (mem["name"], e) for mem, r, e in done if e] + [("%s: %s" % (mem["name"], w)) if many else w for mem, r in ok for w in r["warnings"]],
            "limits": sorted({x for mem, r in ok for x in r.get("limits") or []}),
            "members": [mem["name"] for mem in members] if many else []}                  # every warning is up here, so a read that lost a part is not kept for hours (tracker/paid_geo.py _cached)


# ── The report ──────────────────────────────────────────────────────────────

_RANK = {"fix": 0, "check": 1, "clean": 2}


def assemble(raw, account, *, settings=None, now=None):
    """The report from the raw read. `settings` is what the team saved (tracker/paid_kvis_settings.py load)."""
    from . import paid_kvis_settings as st
    settings = settings or st.empty()
    now = now or dt.datetime.now(dt.timezone.utc)
    accounts = []
    for a in raw["accounts"]:
        cfg = st.account_of(settings, a["id"])
        n = int(cfg["topN"] or math.TOP_N)
        keywords = sorted(a["keywords"], key=lambda k: (-k["cost"], k["text"].lower()))[:n]
        res = math.analyse(dict(a, keywords=keywords), config=cfg, today=dt.date.fromisoformat(a.get("today") or now.date().isoformat()))
        accounts.append(dict(res, id=a["id"], name=a["name"], currency=a.get("currency") or "USD", platform="google", today=a.get("today") or "", tracked=n, read=len(a["keywords"]),
                             saved={"topIs": cfg["topIs"], "qsMin": cfg["qsMin"], "topN": cfg["topN"], "ignore": len(cfg["ignore"])}, plan=plan.build(res)))
    accounts.sort(key=lambda a: (_RANK[a["status"]], -a["counts"]["high"], -a["counts"]["medium"], -a["counts"]["low"], a["name"].lower()))
    counts = {k: 0 for k in _RANK}
    for a in accounts:
        counts[a["status"]] += 1
    return {
        "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or "USD", "platform": "google", "isGroup": bool(account.get("members")),
                    "accountCount": len(account.get("members") or [])},
        "generatedAt": now.isoformat(timespec="seconds"), "warnings": list(raw.get("warnings") or []), "limits": list(raw.get("limits") or []), "accounts": accounts,
        "rules": [{"rule": k, "label": math.RULES[k]["label"], "level": math.RULES[k]["level"], "fix": math.RULES[k]["fix"]} for k in math.ORDER],
        "minimums": {"eligible": math.MIN_ELIGIBLE, "topIs": int(math.DEFAULT_TOP_IS * 100), "qsMin": math.DEFAULT_QS_MIN, "topN": math.TOP_N, "weeks": math.WEEKS - 1},
        "summary": {"counts": counts, "accounts": len(accounts), "keywords": sum(a["keywords"] for a in accounts), "judged": sum(a["judged"] for a in accounts),
                    "notJudged": sum(a["notJudged"] for a in accounts), "competitors": sum(len(a["competitorFindings"]) for a in accounts),
                    "high": sum(a["counts"]["high"] for a in accounts), "medium": sum(a["counts"]["medium"] for a in accounts), "low": sum(a["counts"]["low"] for a in accounts),
                    "findings": sum(a["findingCount"] for a in accounts)},
    }


def load_raw(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, at=None, force=False, store=None, today=None):
    """(raw, stale, account): the saved read of an account's keywords, read from Google when there is none (or when `force`). `now` is the cache's clock (seconds), `today` the date the weeks end before."""
    from . import paid_geo, paid_meta, paid_store
    store = store or paid_store
    if paid_meta.is_meta(str(account_id or "")):
        raise BadRequest("Keyword Visibility reads Google Ads accounts only: a Meta ad account has no keywords.")
    day = today or dt.date.today()
    account = seg.resolve_target(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn)
    raw, stale = paid_geo._cached("kvis", account["id"], "weeks", day, day, lambda: google_raw(account, search_fn=search_fn, today=day), force=force, store=store, now=now)
    return raw, stale, account


def report_for(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, at=None, force=False, store=None, today=None, mon=None):
    """The Keyword Visibility report for a Google Ads account or an MCC the root MCC holds. `force` (Refresh) skips the saved read, at most once per paid_gads.REFRESH_COOLDOWN_S for the account.
    `mon` is the monitors' store: with it what the team saved here is applied."""
    from . import paid_kvis_settings as st
    warnings, settings = [], None
    if mon is not None:
        try:
            settings = st.load(mon)
        except Exception as e:                                                       # noqa: BLE001
            warnings.append("What the team saved (targets, ignored domains) could not be read (%s), so it was not applied." % e)
    raw, stale, account = load_raw(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn, search_fn=search_fn, now=now, force=force, store=store, today=today)
    report = assemble(raw, account, settings=settings, now=at)
    report["warnings"] = warnings + report["warnings"]
    if raw.get("readAt"):
        report["readAt"] = raw["readAt"]
    return dict(report, stale=stale) if stale else report
