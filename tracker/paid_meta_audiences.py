"""Paid Marketing Agents: Audience Segment Analysis for a Meta ad account.

Google reports results per audience; Meta does not. What Meta gives is the results of each AD SET and the audiences each ad set is aimed at
(its targeting: the custom audiences and lookalikes it includes or excludes, the interests and behaviors it names). So an audience's row here is
the results of the ad sets that use it, and an ad set aimed at two audiences counts under both (the page says so, as it does for Google's audiences
used in several places). The numbers are therefore as honest as Google's, but they answer "how do the ad sets built on this audience do", not
"how do the people in it do".

The raw read has exactly the shape tracker/paid_audiences.google_raw gives, so the same page, judging and caching apply:

    audiences   {key: {label, kind, detail, account, ref, refKind, uses: [...], all, types}} (one per audience, a use per ad set)
    totals      {campaign type: sums} for the whole account (the ad sets that use no audience too), so coverage is honest
    lists       the account's custom audiences (size Meta reports, whether it can deliver, whether any ad set uses it)
    exclusions  the audiences and interests ad sets exclude

An audience's kind is worked out from Meta's own subtype: a customer file is "customer_match", website, app and engagement audiences are
"remarketing", a lookalike is "similar", an interest is "interest", a behavior is "behavior", the rest "custom" or "other". An ad set on Advantage+
audience treats the audiences it names as SUGGESTIONS, not limits, so those uses are marked "observation" (the others "targeting").

Meta has no audience bid adjustment, so no use is ever adjustable: the advice is an ad set of its own, a budget, or taking the audience out
of the targeting (tracker/paid_audiences_insights.py words it for Meta). Read only: nothing here changes an account.
"""
from __future__ import annotations

import json

from . import paid_gads as base
from . import paid_gads_segments as gads
from . import paid_geo as geo
from . import paid_meta as meta
from . import paid_meta_segments as mseg

zero, add, is_empty = geo.zero, geo.add, geo.is_empty

AUDIENCE_FIELDS = ("id,name,subtype,approximate_count_lower_bound,approximate_count_upper_bound,delivery_status,operation_status,retention_days")
ADSET_FIELDS = "id,name,campaign_id,effective_status,targeting"
INSIGHT_FIELDS = "adset_id,adset_name,campaign_id,campaign_name,objective,spend,impressions,inline_link_clicks,actions,action_values"
READY = 200                                   # Meta's delivery status code for an audience that can be used

# Meta's audience subtype -> (kind, what to call it)
SUBTYPE_KIND = {
    "LOOKALIKE": ("similar", "Lookalike audience"),
    "CUSTOM": ("customer_match", "Customer list"),
    "WEBSITE": ("remarketing", "Website visitors"),
    "APP": ("remarketing", "App activity"),
    "ENGAGEMENT": ("remarketing", "Engagement (video, page, form, Instagram)"),
    "VIDEO": ("remarketing", "Video viewers"),
    "OFFLINE_CONVERSION": ("remarketing", "Offline activity"),
    "DATA_SET": ("remarketing", "Dataset audience"),
    "BAG_OF_ACCOUNTS": ("custom", "Combined accounts"),
    "CLAIM": ("custom", "Custom audience"),
}


def _vals(n):
    return {"imp": float(n[0]), "clk": float(n[1]), "cost": float(n[2]), "conv": float(n[3]), "val": float(n[4])}


def parse_audiences(rows):
    """{audience id: {name, subtype, kind, detail, size, ready, why}}: the account's custom audiences and lookalikes."""
    out = {}
    for r in rows:
        aid = str(r.get("id") or "")
        if not aid:
            continue
        kind, detail = SUBTYPE_KIND.get(str(r.get("subtype") or "").upper(), ("custom", "Custom audience"))
        status = r.get("delivery_status") if isinstance(r.get("delivery_status"), dict) else {}
        try:
            size = int(r.get("approximate_count_lower_bound"))
        except (TypeError, ValueError):
            size = None
        if size is not None and size < 0:
            size = None                                           # Meta says -1 when it will not say
        code = status.get("code")
        out[aid] = {"name": str(r.get("name") or ""), "subtype": str(r.get("subtype") or ""), "kind": kind, "detail": detail, "size": size,
                    "ready": code is None or code == READY, "why": str(status.get("description") or "")}
    return out


def _named(items):
    return [(str(i.get("id") or ""), str(i.get("name") or "")) for i in items or [] if isinstance(i, dict) and (i.get("id") or i.get("name"))]


def parse_targeting(t):
    """({"include": [(type, id, name)], "exclude": [...], "suggestion": bool}) from an ad set's targeting. A type is `audience`, `interest`, `behavior` or
    `other` (a life event or a demographic Meta lets an ad set name)."""
    t = t if isinstance(t, dict) else {}
    inc, exc = [], []
    for i, n in _named(t.get("custom_audiences")):
        inc.append(("audience", i, n))
    for i, n in _named(t.get("excluded_custom_audiences")):
        exc.append(("audience", i, n))
    for group in t.get("flexible_spec") or []:
        for key, items in (group.items() if isinstance(group, dict) else []):
            typ = {"interests": "interest", "behaviors": "behavior"}.get(key, "other")
            inc += [(typ, i, n) for i, n in _named(items)]
    ex = t.get("exclusions") if isinstance(t.get("exclusions"), dict) else {}
    for key, items in ex.items():
        typ = {"interests": "interest", "behaviors": "behavior"}.get(key, "other")
        exc += [(typ, i, n) for i, n in _named(items)]
    auto = t.get("targeting_automation") if isinstance(t.get("targeting_automation"), dict) else {}
    return {"include": inc, "exclude": exc, "suggestion": bool(auto.get("advantage_audience"))}


def parse_adsets(rows):
    """[{id, name, campaignId, status, include, exclude, suggestion}] for the ad sets that have a name and an id."""
    out = []
    for r in rows:
        sid = str(r.get("id") or "")
        if sid:
            out.append(dict(parse_targeting(r.get("targeting")), id=sid, name=str(r.get("name") or sid), campaignId=str(r.get("campaign_id") or ""),
                            status=str(r.get("effective_status") or "")))
    return out


def parse_insights(rows):
    """{ad set id: {campaign, campaignId, ctype, m}}: what each ad set did, with the campaign type its objective makes it."""
    out = {}
    for r in rows:
        sid = str(r.get("adset_id") or "")
        if not sid:
            continue
        vals = _vals(meta.numbers(r))
        o = out.setdefault(sid, {"campaign": str(r.get("campaign_name") or ""), "campaignId": str(r.get("campaign_id") or ""),
                                 "ctype": mseg.campaign_type(r.get("objective")), "m": zero()})
        add(o["m"], vals)
    return out


def identify(typ, ref, name, audiences):
    """(key, label, kind, detail) of one thing an ad set names."""
    if typ == "audience":
        info = audiences.get(ref) or {}
        kind, detail = info.get("kind") or "custom", info.get("detail") or "Custom audience"
        return "audience:" + ref, info.get("name") or name or "Audience " + ref, kind, detail
    kind, detail = {"interest": ("interest", "Interest"), "behavior": ("behavior", "Behavior")}.get(typ, ("other", "Targeting detail"))
    return "%s:%s" % (typ, ref or name), name or "%s %s" % (detail, ref), kind, detail


def _every_include(adsets, members, audiences):
    """(type, id, name) of each thing in `members` (their identify() keys), as the ad sets name them."""
    seen, out = set(), []
    for s in adsets:
        for typ, ref, name in s["include"]:
            if identify(typ, ref, name, audiences)[0] in members and (typ, ref) not in seen:
                seen.add((typ, ref))
                out.append((typ, ref, name))
    return sorted(out, key=lambda t: identify(*t, audiences)[0])


def build(account, adsets, stats, audiences):
    """The raw read from the parsed parts (see the module's doc). An ad set that shows no results in the range adds no use but still counts
    as nothing to the totals."""
    aid, acct = account["id"], account["name"]
    out, totals = {}, {}
    for sid, s in stats.items():
        if not is_empty(s["m"]):
            add(totals.setdefault(s["ctype"], zero()), s["m"])
    attached = set()
    users = {}                                    # what an ad set names -> the ad sets (with results) that name it
    for s in adsets:
        st = stats.get(s["id"])
        for typ, ref, name in s["include"] + s["exclude"]:
            if typ == "audience":
                attached.add(ref)
        if st is not None and not is_empty(st["m"]):
            for typ, ref, name in s["include"]:
                users.setdefault(identify(typ, ref, name, audiences)[0], set()).add(s["id"])
    # Audiences that exactly the same ad sets use cannot be told apart: Meta gives results per ad set, so they share one set of numbers. One row says that.
    together = {}
    for key, ids in users.items():
        together.setdefault(frozenset(ids), []).append(key)
    group_of = {k: sorted(keys) for keys in together.values() if len(keys) > 1 for k in keys}
    for s in adsets:
        st = stats.get(s["id"])
        if st is None or is_empty(st["m"]):
            continue
        for typ, ref, name in s["include"]:
            key, label, kind, detail = identify(typ, ref, name, audiences)
            members = group_of.get(key)
            if members:
                if key != members[0]:
                    continue                        # the group's first member carries the row for all of them
                parts = [identify(t2, r2, n2, audiences) for t2, r2, n2 in _every_include(adsets, members, audiences)]
                names = [p[1] for p in parts]
                kinds = {p[2] for p in parts}
                key = "group:" + "+".join(members)                  # the type stays in it: audience 10 and interest 10 are not the same thing
                label = " + ".join(names[:3]) + (" and %d more" % (len(names) - 3) if len(names) > 3 else "")
                kind = kinds.pop() if len(kinds) == 1 else "other"
                detail = "%d audiences always used together" % len(names)
            else:
                names = None
            a = out.setdefault("%s|%s" % (aid, key), {"label": label, "kind": kind, "detail": detail, "account": acct, "ref": ref, "refKind": typ,
                                                     "uses": [], "all": zero(), "types": {}, **({"members": names} if names else {})})
            a["uses"].append({"campaign": st["campaign"], "campaignId": st["campaignId"], "ctype": st["ctype"], "adGroup": s["name"], "level": "ad_group",
                              "mode": "observation" if s["suggestion"] else "targeting", "bid": None, "bidding": "", "platform": "meta", "m": dict(st["m"])})
            add(a["all"], st["m"])
            add(a["types"].setdefault(st["ctype"], zero()), st["m"])
    lists = []
    for lid, info in audiences.items():
        lists.append({"key": "%s|audience:%s" % (aid, lid), "id": lid, "label": info["name"] or "Audience " + lid, "kind": info["kind"], "detail": info["detail"],
                      "account": acct, "status": "OPEN" if info["ready"] else "NOT_READY", "sizeSearch": info["size"], "sizeDisplay": info["size"],
                      "readOnly": False, "used": lid in attached, "platform": "meta", "why": info["why"]})
    excl = []
    for s in adsets:
        for typ, ref, name in s["exclude"]:
            key, label, kind, detail = identify(typ, ref, name, audiences)
            camp = (stats.get(s["id"]) or {}).get("campaign") or ""
            excl.append({"label": label, "kind": kind, "detail": detail, "account": acct, "campaign": camp, "adGroup": s["name"], "level": "ad_group"})
    return {"platform": "meta", "audiences": out, "totals": totals, "lists": lists, "exclusions": excl, "members": [], "warnings": []}


def read_account(account, start, end, *, store, fetch=None):
    """The raw read of one Meta ad account. The ad sets and their results are the point, so a failure there is raised; the account's audience lists are an
    extra (a missing permission is a warning, and the audiences are then named from the ad sets alone)."""
    act = account["id"]
    pages = fetch or (lambda path, params: meta._pages(path, params, store))
    window = json.dumps({"since": start.isoformat(), "until": end.isoformat()})
    adsets = parse_adsets(pages(act + "/adsets", {"fields": ADSET_FIELDS, "limit": 200, "effective_status": json.dumps(
        ["ACTIVE", "PAUSED", "ARCHIVED", "CAMPAIGN_PAUSED", "ADSET_PAUSED", "WITH_ISSUES", "IN_PROCESS", "PENDING_REVIEW", "DISAPPROVED", "PENDING_BILLING_INFO"])}))
    stats = parse_insights(pages(act + "/insights", {"level": "adset", "time_range": window, "fields": INSIGHT_FIELDS, "limit": meta.PAGE_LIMIT}))
    warnings, audiences = [], {}
    try:
        audiences = parse_audiences(pages(act + "/customaudiences", {"fields": AUDIENCE_FIELDS, "limit": 200}))
    except meta.OverQuota:
        raise
    except base.AdsError as e:
        warnings.append("%s: the account's audience lists could not be read (%s). The audiences are named from the ad sets alone and their sizes are not shown." % (
            account["name"], e))
    raw = build(account, adsets, stats, audiences)
    raw["warnings"] = warnings
    return raw


def meta_raw(account, start, end, *, store=None, fetch=None):
    """read_account with Meta's errors turned into the ones the pages already handle."""
    from . import paid_store
    try:
        return read_account(account, start, end, store=store or paid_store, fetch=fetch)
    except base.AdsError as e:
        raise gads.translate(e) from e
