"""Paid Marketing Agents: Audience Segment Analysis, the reading.

How the audiences an account uses perform, for a Google Ads account or an MCC's accounts together, over one date range. Google
reports an audience's results in two views, one for the audiences attached to an ad group (`ad_group_audience_view`: Demand Gen,
Display, Video, and Search where an ad group carries them) and one for the audiences attached to a campaign
(`campaign_audience_view`: how Search campaigns usually carry them). Both are read, and an audience used in several ad groups or
campaigns is one row with every use under it.

An audience is one of these kinds, worked out from its criterion type and, for the two that need it, a lookup:

    in_market        a user interest whose taxonomy is IN_MARKET
    affinity         a user interest whose taxonomy is AFFINITY
    remarketing      a user list of type REMARKETING, RULE_BASED, LOGICAL (website and app visitors, combined lists)
    customer_match   a user list of type CRM_BASED
    similar          a user list of type SIMILAR or LOOKALIKE
    custom           a custom segment (custom audience, custom intent, custom affinity)
    other            everything else Google attaches (your-data and combined segments, life events, demographics)

Each use carries whether the audience only observes (adds a bid adjustment but does not limit who sees the ad) or targets (only
that audience sees it), read from the targeting setting (an ad group's own setting wins over its campaign's), and the bid adjustment
in force. The excluded audiences are read too, and every user list the account has (its size, and whether anything uses it).

Performance Max is not here: the API gives its audience signals no results.

What the numbers mean (judging, suggested adjustments, what to do) is tracker/paid_audiences_insights.py. This module reads, adds up
and caches (the shared cache, tracker/paid_geo.py _cached: one read per account and range, Refresh limited, a spent quota served from
the last saved read with its date).
"""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

from . import paid_audiences_insights as insights
from . import paid_gads_segments as gads
from . import paid_geo as geo
from . import paid_segments as seg

BadRequest = seg.BadRequest
ALL = seg.ALL
zero, add, is_empty, derive = geo.zero, geo.add, geo.is_empty, geo.derive
LOOKUP_CHUNK = 300

KINDS = [("in_market", "In-market"), ("affinity", "Affinity"), ("interest", "Interests"), ("behavior", "Behaviors"), ("remarketing", "Remarketing"),
         ("customer_match", "Customer match"), ("similar", "Similar"), ("custom", "Custom segments"), ("other", "Other")]
KIND_LABEL = dict(KINDS)
LIST_KIND = {"CRM_BASED": ("customer_match", "Customer match list"), "SIMILAR": ("similar", "Similar audience"),
             "LOOKALIKE": ("similar", "Lookalike audience"), "REMARKETING": ("remarketing", "Remarketing list"),
             "RULE_BASED": ("remarketing", "Website visitors (rule based)"), "LOGICAL": ("remarketing", "Combined list")}
INTEREST_KIND = {"IN_MARKET": ("in_market", "In-market segment"), "AFFINITY": ("affinity", "Affinity segment")}
CRITERION_KIND = {"CUSTOM_AUDIENCE": ("custom", "Custom segment"), "CUSTOM_INTENT": ("custom", "Custom segment (intent)"),
                  "CUSTOM_AFFINITY": ("custom", "Custom segment (affinity)"), "COMBINED_AUDIENCE": ("other", "Combined segment"),
                  "AUDIENCE": ("other", "Your data segment")}
AUDIENCE_TYPES = ("USER_LIST", "USER_INTEREST", "CUSTOM_AUDIENCE", "CUSTOM_INTENT", "CUSTOM_AFFINITY", "COMBINED_AUDIENCE", "AUDIENCE")
OBSERVE_BY_DEFAULT = ("SEARCH", "SHOPPING")          # a campaign with no audience setting observes on these channels, targets on the rest

_METRICS = "metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions, metrics.conversions_value"
_REFS = ("{p}.user_list.user_list, {p}.user_interest.user_interest_category, {p}.custom_audience.custom_audience, {p}.display_name, {p}.type, "
         "{p}.criterion_id, {p}.bid_modifier")
_AD_GROUP_ONLY = "{p}.custom_intent.custom_intent, {p}.combined_audience.combined_audience, {p}.audience.audience"      # campaign criteria have no such fields


def _range(start, end):
    return "segments.date BETWEEN '%s' AND '%s'" % (start.isoformat(), end.isoformat())


def ad_group_gaql(start, end):
    return ("SELECT campaign.id, campaign.name, campaign.advertising_channel_type, campaign.bidding_strategy_type, ad_group.id, ad_group.name, %s, "
            "%s, %s FROM ad_group_audience_view WHERE %s") % (_REFS.format(p="ad_group_criterion"), _AD_GROUP_ONLY.format(p="ad_group_criterion"),
                                                                _METRICS, _range(start, end))


def campaign_gaql(start, end):
    return ("SELECT campaign.id, campaign.name, campaign.advertising_channel_type, campaign.bidding_strategy_type, %s, %s "
            "FROM campaign_audience_view WHERE %s") % (
        _REFS.format(p="campaign_criterion"), _METRICS, _range(start, end))


SETTINGS_GAQL = ("SELECT campaign.id, campaign.targeting_setting.target_restrictions, ad_group.id, ad_group.targeting_setting.target_restrictions "
                 "FROM ad_group WHERE ad_group.status != REMOVED")
LISTS_GAQL = ("SELECT user_list.id, user_list.name, user_list.type, user_list.membership_status, user_list.size_for_search, "
              "user_list.size_for_display, user_list.read_only FROM user_list")
_TYPES = ", ".join(AUDIENCE_TYPES)
AD_GROUP_EXCLUDED_GAQL = ("SELECT campaign.name, ad_group.name, ad_group_criterion.type, ad_group_criterion.display_name, ad_group_criterion.user_list.user_list, "
                          "ad_group_criterion.user_interest.user_interest_category FROM ad_group_criterion WHERE ad_group_criterion.negative = TRUE "
                          "AND ad_group_criterion.type IN (%s)" % _TYPES)
CAMPAIGN_EXCLUDED_GAQL = ("SELECT campaign.name, campaign_criterion.type, campaign_criterion.display_name, campaign_criterion.user_list.user_list, "
                          "campaign_criterion.user_interest.user_interest_category FROM campaign_criterion WHERE campaign_criterion.negative = TRUE "
                          "AND campaign_criterion.type IN (%s)" % _TYPES)


def interests_gaql(ids):
    return ("SELECT user_interest.user_interest_id, user_interest.name, user_interest.taxonomy_type FROM user_interest "
            "WHERE user_interest.user_interest_id IN (%s)" % ", ".join(str(int(i)) for i in ids))


# ── Reading the API's rows ──────────────────────────────────────────────────

def _get(row, *path, default=None):
    for p in path:
        if not isinstance(row, dict):
            return default
        row = row.get(p)
    return default if row is None else row


def _tail(resource):
    return str(resource or "").rsplit("/", 1)[-1]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _vals(row):
    m = row.get("metrics") or {}
    return {"imp": _f(m.get("impressions")), "clk": _f(m.get("clicks")), "cost": _f(m.get("costMicros")) / 1e6,
            "conv": _f(m.get("conversions")), "val": _f(m.get("conversionsValue"))}


def _ctype(row):
    c = row.get("campaign") or {}
    return seg.campaign_type(c.get("name"), c.get("advertisingChannelType"))


def reference(crit):
    """('list' | 'interest' | 'custom' | ..., id) of what a criterion points at, from its type."""
    t = str(crit.get("type") or "")
    if t == "USER_LIST":
        return "list", _tail(_get(crit, "userList", "userList"))
    if t == "USER_INTEREST":
        return "interest", _tail(_get(crit, "userInterest", "userInterestCategory"))
    ref = {"CUSTOM_AUDIENCE": ("customAudience", "customAudience"), "CUSTOM_INTENT": ("customIntent", "customIntent"),
           "COMBINED_AUDIENCE": ("combinedAudience", "combinedAudience"), "AUDIENCE": ("audience", "audience")}.get(t)
    return (t.lower(), _tail(_get(crit, *ref)) if ref else "") if t else ("", "")


def mode_of(restrictions, channel):
    """'observation' or 'targeting' from a targeting setting's restrictions, or None when it says nothing about audiences."""
    for r in restrictions or []:
        if str(r.get("targetingDimension")) == "AUDIENCE":
            return "observation" if r.get("bidOnly") else "targeting"
    return None


def read_settings(rows):
    """({campaign id: restrictions}, {ad group id: restrictions}) of the targeting settings."""
    camps, groups = {}, {}
    for r in rows:
        cid, gid = str(_get(r, "campaign", "id", default="")), str(_get(r, "adGroup", "id", default=""))
        c = _get(r, "campaign", "targetingSetting", "targetRestrictions")
        g = _get(r, "adGroup", "targetingSetting", "targetRestrictions")
        if cid and c is not None:
            camps[cid] = c
        if gid and g is not None:
            groups[gid] = g
    return camps, groups


def read_uses(rows, level, camp_settings, group_settings):
    """One dict per audience attached somewhere, with its results over the range (rows nothing is attached to are not in the view)."""
    out = []
    for r in rows:
        crit = r.get("adGroupCriterion" if level == "ad_group" else "campaignCriterion") or {}
        kind, ref = reference(crit)
        channel = str(_get(r, "campaign", "advertisingChannelType", default=""))
        cid = str(_get(r, "campaign", "id", default=""))
        gid = str(_get(r, "adGroup", "id", default="")) if level == "ad_group" else ""
        mode = (mode_of(group_settings.get(gid), channel) if gid else None) or mode_of(camp_settings.get(cid), channel) \
            or ("observation" if channel in OBSERVE_BY_DEFAULT else "targeting")
        bid = crit.get("bidModifier")
        adj = None if bid is None else round(float(bid) - 1.0, 2)          # Google's bid_modifier is a multiplier (1.25 is +25%, 1 is no adjustment)
        out.append({"type": str(crit.get("type") or ""), "ref": ref, "refKind": kind, "name": str(crit.get("displayName") or ""),
                    "campaign": _get(r, "campaign", "name", default=""), "campaignId": cid, "ctype": _ctype(r), "channel": channel,
                    "bidding": str(_get(r, "campaign", "biddingStrategyType", default="")),
                    "adGroup": _get(r, "adGroup", "name", default="") if level == "ad_group" else "", "level": level, "mode": mode,
                    "bid": adj or None, "m": _vals(r)})
    return out


def read_lists(rows):
    """{list id: {name, type, status, sizeSearch, sizeDisplay}}."""
    return {str(_get(r, "userList", "id", default="")): {
        "name": _get(r, "userList", "name", default=""), "type": str(_get(r, "userList", "type", default="")),
        "status": str(_get(r, "userList", "membershipStatus", default="")), "sizeSearch": int(_f(_get(r, "userList", "sizeForSearch"))),
        "sizeDisplay": int(_f(_get(r, "userList", "sizeForDisplay"))), "readOnly": bool(_get(r, "userList", "readOnly"))}
        for r in rows if _get(r, "userList", "id")}


def read_interests(rows):
    return {str(_get(r, "userInterest", "userInterestId", default="")): {
        "name": _get(r, "userInterest", "name", default=""), "taxonomy": str(_get(r, "userInterest", "taxonomyType", default=""))}
        for r in rows if _get(r, "userInterest", "userInterestId")}


def identify(use, lists, interests):
    """(key, label, kind, detail) of the audience a use belongs to."""
    t, ref = use["type"], use["ref"]
    if t == "USER_LIST":
        info = lists.get(ref) or {}
        kind, detail = LIST_KIND.get(info.get("type"), ("remarketing", "User list"))
        return "list:" + ref, info.get("name") or use["name"] or "List " + ref, kind, detail
    if t == "USER_INTEREST":
        info = interests.get(ref) or {}
        kind, detail = INTEREST_KIND.get(info.get("taxonomy"), ("other", "Interest segment"))
        return "interest:" + ref, info.get("name") or use["name"] or "Interest " + ref, kind, detail
    kind, detail = CRITERION_KIND.get(t, ("other", "Audience"))
    return "%s:%s" % (t.lower(), ref or use["name"]), use["name"] or "%s %s" % (detail, ref), kind, detail


# ── One account ─────────────────────────────────────────────────────────────

def read_account(member, start, end, *, search_fn=None):
    """The raw read of one Google account: {audiences, totals, lists, exclusions, warnings}. The two audience views are the point,
    so a failure there is raised; the settings, lookups and exclusions are extras, and a failure there is a warning."""
    search = search_fn or gads.search
    cid, name = member["id"], member["name"]
    ag_rows, cp_rows = search(cid, ad_group_gaql(start, end)), search(cid, campaign_gaql(start, end))
    totals = geo.read_google_totals(search(cid, geo.totals_gaql(start, end)))
    warnings = []

    def extra(label, gaql, read, default):
        try:
            return read(search(cid, gaql))
        except gads.GadsError as e:
            warnings.append("%s: %s could not be read (%s)." % (name, label, e))
            return default
    camp_settings, group_settings = extra("whether each audience observes or targets (it is assumed from the campaign type)", SETTINGS_GAQL,
                                          read_settings, ({}, {}))
    uses = read_uses(ag_rows, "ad_group", camp_settings, group_settings) + read_uses(cp_rows, "campaign", camp_settings, group_settings)
    lists = extra("the audience lists", LISTS_GAQL, read_lists, {})
    excluded = []
    for label, gaql, level in (("the excluded audiences of ad groups", AD_GROUP_EXCLUDED_GAQL, "ad_group"),
                               ("the excluded audiences of campaigns", CAMPAIGN_EXCLUDED_GAQL, "campaign")):
        excluded += extra(label, gaql, lambda rows, level=level: read_exclusions(rows, level), [])
    ids = sorted({u["ref"] for u in uses + excluded if u["type"] == "USER_INTEREST" and u["ref"].isdigit()})
    interests = {}
    for i in range(0, len(ids), LOOKUP_CHUNK):
        interests.update(extra("the interest names", interests_gaql(ids[i:i + LOOKUP_CHUNK]), read_interests, {}))
    audiences = {}
    for u in uses:
        key, label, kind, detail = identify(u, lists, interests)
        a = audiences.setdefault("%s|%s" % (cid, key), {"label": label, "kind": kind, "detail": detail, "account": name, "ref": u["ref"],
                                                       "refKind": u["refKind"], "uses": [], "all": zero(), "types": {}})
        a["uses"].append({k: u[k] for k in ("campaign", "campaignId", "ctype", "adGroup", "level", "mode", "bid", "bidding", "m")})
        if not is_empty(u["m"]):
            add(a["all"], u["m"])
            add(a["types"].setdefault(u["ctype"], zero()), u["m"])
    attached = {u["ref"] for u in uses + excluded if u["type"] == "USER_LIST"}
    list_rows = []
    for lid, info in lists.items():
        kind, detail = LIST_KIND.get(info["type"], ("remarketing", "User list"))
        list_rows.append({"key": "%s|list:%s" % (cid, lid), "id": lid, "label": info["name"] or "List " + lid, "kind": kind, "detail": detail,
                          "account": name, "status": info["status"], "sizeSearch": info["sizeSearch"], "sizeDisplay": info["sizeDisplay"],
                          "readOnly": info["readOnly"], "used": lid in attached})
    excl = []
    for e in excluded:
        key, label, kind, detail = identify(e, lists, interests)
        excl.append({"label": label, "kind": kind, "detail": detail, "account": name, "campaign": e["campaign"], "adGroup": e["adGroup"], "level": e["level"]})
    return {"audiences": audiences, "totals": totals, "lists": list_rows, "exclusions": excl, "warnings": warnings}


def read_exclusions(rows, level):
    out = []
    for r in rows:
        crit = r.get("adGroupCriterion" if level == "ad_group" else "campaignCriterion") or {}
        kind, ref = reference(crit)
        out.append({"type": str(crit.get("type") or ""), "ref": ref, "refKind": kind, "name": str(crit.get("displayName") or ""),
                    "campaign": _get(r, "campaign", "name", default=""), "adGroup": _get(r, "adGroup", "name", default="") if level == "ad_group" else "",
                    "level": level})
    return out


def google_raw(account, start, end, *, search_fn=None, workers=6):
    """The raw read of one account or an MCC's accounts together. An account that cannot be read is left out and said so; every one
    failing is raised."""
    members = account.get("members") or [{"id": account["id"], "name": account["name"]}]

    def run(m):
        try:
            return m, read_account(m, start, end, search_fn=search_fn), None
        except gads.GadsError as e:
            return m, None, e
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(members)))) as ex:
        done = list(ex.map(run, members))
    ok = [(m, r) for m, r, e in done if r is not None]
    if not ok:
        raise next(e for _m, _r, e in done if e)
    out = {"audiences": {}, "totals": {}, "lists": [], "exclusions": [], "members": [m["name"] for m in members] if account.get("members") else [],
           "warnings": ["%s could not be read: %s" % (m["name"], e) for m, r, e in done if e]}
    for _m, r in ok:
        out["audiences"].update(r["audiences"])
        for t, m in r["totals"].items():
            add(out["totals"].setdefault(t, zero()), m)
        out["lists"] += r["lists"]
        out["exclusions"] += r["exclusions"]
        out["warnings"] += r["warnings"]
    return out


# ── The report ──────────────────────────────────────────────────────────────

def _bid_summary(uses):
    bids = {u["bid"] for u in uses if u["bid"] is not None}
    return {"bid": next(iter(bids)) if len(bids) == 1 else None, "bidVaries": len(bids) > 1, "hasBid": bool(bids)}


def _view(audiences, ctype, account, multi):
    picked, total = [], zero()
    for key, a in audiences.items():
        m = a["all"] if ctype == ALL else a["types"].get(ctype)
        if m and not is_empty(m):
            picked.append((key, a, m))
            add(total, m)
    picked.sort(key=lambda p: (-p[2]["cost"], p[1]["label"]))
    rows = []
    for key, a, m in picked:
        uses = [u for u in a["uses"] if ctype == ALL or u["ctype"] == ctype]
        row = derive(m, account["cost"], account["conv"])
        modes = sorted({u["mode"] for u in uses})
        row.update(key=key, label=a["label"], kind=a["kind"], kindLabel=KIND_LABEL[a["kind"]], detail=a["detail"], account=a["account"] if multi else "",
                   members=a.get("members") or [],
                   platform="meta" if uses and all(u.get("platform") == "meta" for u in uses) else "google",
                   mode=modes[0] if len(modes) == 1 else "mixed", types=sorted({u["ctype"] for u in uses}), **_bid_summary(uses),
                   uses=[dict(u, m=derive(u["m"], account["cost"], account["conv"])) for u in sorted(uses, key=lambda u: -u["m"]["cost"])])
        rows.append(row)
    return {"rows": rows, "total": derive(total, account["cost"], account["conv"]), "account": derive(account["m"], account["cost"], account["conv"])}


def assemble(raw, account, start, end):
    """The report the page reads, from one raw read: a table per campaign type, each judged against its own average."""
    totals = raw["totals"]
    acct = zero()
    for m in totals.values():
        add(acct, m)
    seen = {t for a in raw["audiences"].values() for t in a["types"]}
    types = [t for t, m in sorted(totals.items(), key=lambda p: -p[1]["cost"]) if t in seen]
    types += sorted(t for t in seen if t not in types)
    multi = bool(raw.get("members"))
    platform = raw.get("platform") or "google"
    views = {}
    for t in [ALL] + types:
        base = acct if t == ALL else totals.get(t, zero())
        v = _view(raw["audiences"], t, {"cost": base["cost"], "conv": base["conv"], "m": base}, multi)
        views[t] = insights.analyse(v, currency=account.get("currency") or "USD", ctype=t,
                                    mixed=t == ALL and platform == "meta" and geo._mixed_results(totals), force_cpa=t == ALL and platform == "both")
    lists = insights.judge_lists(raw["lists"])
    report = {
        "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or "USD", "platform": platform,
                    "isGroup": multi, "accountCount": len(raw.get("members") or []) or 1},
        "range": {"from": start.isoformat(), "to": end.isoformat(), "days": (end - start).days + 1},
        "kinds": [{"key": k, "label": l, "count": sum(1 for r in views[ALL]["rows"] if r["kind"] == k)} for k, l in KINDS],
        "kindTotals": insights.kind_totals(views[ALL]),
        "types": types, "views": views, "lists": lists, "exclusions": raw["exclusions"], "warnings": raw.get("warnings") or [],
        "generatedAt": raw.get("readAt") or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),         # when the numbers were read, not when this page was built
    }
    report["views"][ALL]["insights"] = insights.report_insights(report, views[ALL])
    return report


def merge_raw(g, m):
    """One raw read from a Google and a Meta one of the same range: the audiences of the two accounts side by side (their keys carry the account),
    the totals and the lists added. Neither input is changed."""
    totals = {}
    for src in (g["totals"], m["totals"]):
        for t, v in src.items():
            add(totals.setdefault(t, zero()), v)
    return {"platform": "both", "audiences": {**g["audiences"], **m["audiences"]}, "totals": totals, "lists": list(g["lists"]) + list(m["lists"]),
            "exclusions": list(g["exclusions"]) + list(m["exclusions"]), "members": g.get("members") or [],
            "warnings": list(g["warnings"]) + list(m["warnings"]), "readAt": min(filter(None, (g.get("readAt"), m.get("readAt"))), default=None)}


PLATFORM_NAME = {"google": "Google Ads", "meta": "Meta Ads"}


def _raw_for(raw_id, s, e, *, force, store, now, accounts_fn, groups_fn, search_fn, meta_fetch):
    """(raw read, account, stale) for one Google account / MCC group or one Meta account."""
    from . import paid_meta, paid_meta_audiences
    if paid_meta.is_meta(raw_id):
        account = seg._meta_target(raw_id, store)
        raw, stale = geo._cached("audiences", paid_meta.day_key(account["id"]), "all", s, e,
                                 lambda: paid_meta_audiences.meta_raw(account, s, e, store=store, fetch=meta_fetch), force=force, store=store, now=now)
    else:
        account = seg.resolve_target(raw_id, accounts_fn=accounts_fn, groups_fn=groups_fn)
        raw, stale = geo._cached("audiences", account["id"], "all", s, e, lambda: google_raw(account, s, e, search_fn=search_fn),
                                 force=force, store=store, now=now)
    return raw, account, stale


def report_for(account_id, start, end, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, force=False, store=None, meta_fetch=None):
    """The audience report for a Google Ads account or an MCC the root MCC holds, a Meta ad account (act_...), or a Google and a Meta account together
    ("<google id>+<act_ id>") over a date range. `force` (Refresh) skips the saved read, at most once per paid_gads.REFRESH_COOLDOWN_S for the account."""
    from . import paid_meta, paid_store
    store = store or paid_store
    raw_id = str(account_id or "").strip()
    s, e = seg.parse_range(start, end)
    kw = dict(force=force, store=store, now=now, accounts_fn=accounts_fn, groups_fn=groups_fn, search_fn=search_fn, meta_fetch=meta_fetch)
    if "+" in raw_id:
        parts = raw_id.split("+")
        if len(parts) != 2 or not geo.base.ID_RE.match(parts[0]) or not paid_meta.is_meta(parts[1]):
            raise BadRequest("A combined account is one Google Ads account and one Meta ad account.")

        def half(rid):
            try:
                return _raw_for(rid, s, e, **kw), None
            except geo.base.RefreshTooSoon:
                return _raw_for(rid, s, e, **dict(kw, force=False)), None        # just refreshed: it is current
            except gads.GadsError as err:
                return None, err
        (gres, gerr), (mres, merr) = half(parts[0]), half(parts[1])
        if gres is None and mres is None:
            raise gerr
        if gres is None or mres is None:
            ok, bad, who = (mres, gerr, "google") if gres is None else (gres, merr, "meta")
            raw, account, stale = ok
            raw = dict(raw, warnings=list(raw.get("warnings") or []) + ["%s could not be read: %s Showing the other platform only." % (PLATFORM_NAME[who], bad)])
            report = assemble(raw, account, s, e)
            return dict(report, stale=stale) if stale else report
        (graw, gacct, gstale), (mraw, macct, mstale) = gres, mres
        if (gacct.get("currency") or "USD") != (macct.get("currency") or "USD"):
            raise BadRequest("The two accounts bill in different currencies, so they cannot be added together.")
        account = {"id": raw_id, "name": gacct["name"] + " + " + macct["name"], "currency": gacct.get("currency")}
        report = assemble(merge_raw(graw, mraw), account, s, e)
        stale = gstale or mstale
        return dict(report, stale=stale) if stale else report
    raw, account, stale = _raw_for(raw_id, s, e, **kw)
    report = assemble(raw, account, s, e)
    return dict(report, stale=stale) if stale else report
