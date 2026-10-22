"""Paid Marketing Agents: Location Conflicts, the reading and the report.

For a Google Ads account (or every account of an MCC) or a Meta ad account this reads where each live campaign is set to show and the words that name a place: the campaigns with their
location option, the locations and radiuses each targets or excludes (with the place names Google gives them), the ad schedule and the device adjustments, the ad group names, the
responsive Search ads and the keywords. What is wrong is tracker/paid_geoconf_math.py; nothing is changed in an account.

The campaigns and their locations are the point (a failure there is raised); everything else is soft: a refusal leaves that check out and says so. If the names of the places cannot be read
the place checks cannot run, and the report says that instead of showing a clean account. A Meta ad set is the unit there (its targeting is on the ad set), so an ad set stands in the
place of a campaign and its Meta campaign in the place of an ad group.

The read is cached like the other agents (tracker/paid_geo.py _cached); what the team saved (areas, words to ignore, the presence option) is applied to the saved read each time, so a change
on the page shows at once without another read of Google. The field names of the Google queries are the API's and the Meta ones are Meta's documented ones; none has been run against the
live service from here.
"""
from __future__ import annotations

import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor

from . import paid_copycheck as cc
from . import paid_geoconf_math as math
from . import paid_geoconf_plan as plan
from . import paid_gads_segments as gads
from . import paid_segments as seg

BadRequest = seg.BadRequest
MAX_KEYWORDS = 5000
NAME_CHUNK = 200

GAQL_CAMPAIGNS = ("SELECT campaign.id, campaign.name, campaign.advertising_channel_type, campaign.geo_target_type_setting.positive_geo_target_type, "
                  "campaign.geo_target_type_setting.negative_geo_target_type FROM campaign WHERE campaign.status = ENABLED")
GAQL_LOCATIONS = ("SELECT campaign.id, campaign_criterion.criterion_id, campaign_criterion.type, campaign_criterion.negative, campaign_criterion.location.geo_target_constant, "
                  "campaign_criterion.proximity.radius, campaign_criterion.proximity.radius_units, campaign_criterion.proximity.address.city_name, "
                  "campaign_criterion.proximity.address.province_code FROM campaign_criterion WHERE campaign.status = ENABLED AND campaign_criterion.type IN ('LOCATION', 'PROXIMITY')")
GAQL_NAMES = ("SELECT geo_target_constant.resource_name, geo_target_constant.name, geo_target_constant.canonical_name, geo_target_constant.target_type FROM geo_target_constant "
              "WHERE geo_target_constant.resource_name IN (%s)")
GAQL_SCHEDULES = ("SELECT campaign.id, campaign_criterion.ad_schedule.day_of_week, campaign_criterion.ad_schedule.start_hour, campaign_criterion.ad_schedule.start_minute, "
                  "campaign_criterion.ad_schedule.end_hour, campaign_criterion.ad_schedule.end_minute FROM campaign_criterion WHERE campaign.status = ENABLED "
                  "AND campaign_criterion.type = AD_SCHEDULE")
GAQL_DEVICES = ("SELECT campaign.id, campaign_criterion.device.type, campaign_criterion.bid_modifier FROM campaign_criterion WHERE campaign.status = ENABLED "
                "AND campaign_criterion.type = DEVICE")
GAQL_GROUPS = "SELECT campaign.id, ad_group.id, ad_group.name FROM ad_group WHERE campaign.status = ENABLED AND ad_group.status = ENABLED"
GAQL_KEYWORDS = ("SELECT campaign.id, ad_group.id, ad_group.name, ad_group_criterion.keyword.text FROM ad_group_criterion WHERE campaign.status = ENABLED AND ad_group.status = ENABLED "
                 "AND ad_group_criterion.status = ENABLED AND ad_group_criterion.type = KEYWORD AND ad_group_criterion.negative = FALSE LIMIT %d" % MAX_KEYWORDS)

META_ADSETS_FIELDS = "id,name,effective_status,campaign{id,name},targeting{geo_locations,excluded_geo_locations,location_types}"
_MINUTE = {"ZERO": 0.0, "FIFTEEN": 0.25, "THIRTY": 0.5, "FORTY_FIVE": 0.75}
_COUNTRY = {"US": "United States", "CA": "Canada", "GB": "United Kingdom", "AU": "Australia", "IN": "India", "MX": "Mexico"}


def _get(row, *path, default=None):
    for p in path:
        if not isinstance(row, dict):
            return default
        row = row.get(p)
    return default if row is None else row


# ── Reading Google's rows ───────────────────────────────────────────────────

def read_campaigns(rows):
    out = []
    for r in rows:
        cid = str(_get(r, "campaign", "id", default="") or "")
        if cid:
            out.append({"id": cid, "name": _get(r, "campaign", "name", default="") or "", "channel": _get(r, "campaign", "advertisingChannelType", default="") or "",
                        "positive": _get(r, "campaign", "geoTargetTypeSetting", "positiveGeoTargetType", default="") or "",
                        "negative": _get(r, "campaign", "geoTargetTypeSetting", "negativeGeoTargetType", default="") or ""})
    return out


def read_locations(rows):
    """[{campaignId, negative, kind, geoName (resource name), name, canonical, targetType, city, radiusText}]: the names are filled in from geo_target_constant by apply_names."""
    out = []
    for r in rows:
        cid = str(_get(r, "campaign", "id", default="") or "")
        crit = r.get("campaignCriterion") or {}
        if not cid:
            continue
        if crit.get("type") == "PROXIMITY" or crit.get("proximity"):
            px = crit.get("proximity") or {}
            addr = px.get("address") or {}
            radius = px.get("radius")
            unit = "km" if str(px.get("radiusUnits") or "").upper().startswith("KILO") else "miles"
            text = ("%g %s" % (float(radius), unit)) if radius is not None else "radius"
            out.append({"campaignId": cid, "negative": bool(crit.get("negative")), "kind": "proximity", "geoName": "", "name": addr.get("cityName") or "", "city": addr.get("cityName") or "",
                        "canonical": "", "targetType": "Proximity", "radiusText": text})
        else:
            geo = _get(crit, "location", "geoTargetConstant", default="") or ""
            if geo:
                out.append({"campaignId": cid, "negative": bool(crit.get("negative")), "kind": "location", "geoName": geo, "name": "", "city": "", "canonical": "", "targetType": ""})
    return out


def read_names(rows):
    """{resource name: {name, canonical, targetType}}."""
    out = {}
    for r in rows:
        g = r.get("geoTargetConstant") or {}
        rn = g.get("resourceName")
        if rn:
            out[rn] = {"name": g.get("name") or "", "canonical": g.get("canonicalName") or "", "targetType": g.get("targetType") or ""}
    return out


def apply_names(locations, names):
    """The locations with the names Google gave them; how many stayed without a name."""
    out, missing = [], 0
    for loc in locations:
        if loc.get("kind") == "location":
            n = names.get(loc.get("geoName")) or {}
            if not n.get("name"):
                missing += 1
            loc = dict(loc, name=n.get("name") or "", canonical=n.get("canonical") or "", targetType=n.get("targetType") or "")
        out.append(loc)
    return out, missing


def read_schedules(rows):
    out = []
    for r in rows:
        s = _get(r, "campaignCriterion", "adSchedule", default=None)
        cid = str(_get(r, "campaign", "id", default="") or "")
        if s and cid:
            out.append({"campaignId": cid, "day": s.get("dayOfWeek") or "", "start": float(s.get("startHour") or 0) + _MINUTE.get(str(s.get("startMinute") or "ZERO"), 0.0),
                        "end": float(s.get("endHour") or 0) + _MINUTE.get(str(s.get("endMinute") or "ZERO"), 0.0)})
    return out


def read_devices(rows):
    out = []
    for r in rows:
        crit = r.get("campaignCriterion") or {}
        dev = _get(crit, "device", "type", default="")
        cid = str(_get(r, "campaign", "id", default="") or "")
        if dev and cid and crit.get("bidModifier") is not None:
            out.append({"campaignId": cid, "device": dev, "modifier": float(crit["bidModifier"])})
    return out


def read_groups(rows):
    out = []
    for r in rows:
        gid = str(_get(r, "adGroup", "id", default="") or "")
        if gid:
            out.append({"id": gid, "campaignId": str(_get(r, "campaign", "id", default="") or ""), "name": _get(r, "adGroup", "name", default="") or ""})
    return out


def read_keywords(rows):
    out = []
    for r in rows:
        text = _get(r, "adGroupCriterion", "keyword", "text", default="") or ""
        if text:
            out.append({"campaignId": str(_get(r, "campaign", "id", default="") or ""), "adGroupId": str(_get(r, "adGroup", "id", default="") or ""),
                        "adGroup": _get(r, "adGroup", "name", default="") or "", "text": text})
    return out


# ── Reading Meta's ad sets ──────────────────────────────────────────────────

def read_meta_adsets(rows):
    """(campaigns, locations) from Meta ad sets: an ad set is a campaign of the model, its geo locations are the locations. The setting is "TRAVEL_IN" when the ad set also reaches people
    travelling in the place, and "PRESENCE" otherwise."""
    camps, locs = [], []
    for r in rows:
        sid = str(r.get("id") or "")
        if not sid:
            continue
        tg = r.get("targeting") or {}
        types = tg.get("location_types") or []
        camps.append({"id": sid, "name": r.get("name") or "", "channel": "META", "positive": "TRAVEL_IN" if "travel_in" in types else "PRESENCE", "negative": "",
                      "metaCampaign": (r.get("campaign") or {}).get("name") or "", "metaCampaignId": str((r.get("campaign") or {}).get("id") or "")})
        for key, negative in (("geo_locations", False), ("excluded_geo_locations", True)):
            geo = tg.get(key) or {}
            for code in geo.get("countries") or []:
                nm = _COUNTRY.get(str(code).upper(), str(code))
                locs.append({"campaignId": sid, "negative": negative, "kind": "location", "name": nm, "canonical": nm, "targetType": "Country", "city": ""})
            for reg in geo.get("regions") or []:
                locs.append({"campaignId": sid, "negative": negative, "kind": "location", "name": reg.get("name") or "", "targetType": "State", "city": "",
                             "canonical": ",".join(x for x in (reg.get("name"), _COUNTRY.get(str(reg.get("country") or "").upper(), reg.get("country"))) if x)})
            for city in geo.get("cities") or []:
                radius = city.get("radius")
                locs.append({"campaignId": sid, "negative": negative, "kind": "location", "name": city.get("name") or "", "targetType": "City", "city": "",
                             "canonical": ",".join(x for x in (city.get("name"), city.get("region"), _COUNTRY.get(str(city.get("country") or "").upper(), city.get("country"))) if x),
                             "radiusText": ("%g %s" % (radius, city.get("distance_unit") or "miles")) if radius else ""})
            for z in geo.get("zips") or []:
                locs.append({"campaignId": sid, "negative": negative, "kind": "location", "name": z.get("name") or z.get("key") or "", "canonical": "", "targetType": "Postal Code", "city": ""})
            for cl in geo.get("custom_locations") or []:
                locs.append({"campaignId": sid, "negative": negative, "kind": "proximity", "name": cl.get("name") or cl.get("address_string") or "", "city": "", "canonical": "",
                             "targetType": "Proximity", "radiusText": ("%g %s" % (cl["radius"], cl.get("distance_unit") or "miles")) if cl.get("radius") else "radius"})
    return camps, locs


def meta_request(act):
    return "/%s/adsets" % act, {"fields": META_ADSETS_FIELDS, "effective_status": json.dumps(["ACTIVE"]), "limit": 100}


# ── One account ─────────────────────────────────────────────────────────────

def _soft(warnings, what, fn, default):
    try:
        return fn()
    except gads.GadsError as e:
        warnings.append("%s could not be read (%s), so that check was skipped." % (what, e))
        return default


def read_account(account_id, search):
    """{campaigns, locations, schedules, devices, groups, ads, keywords, warnings}: one Google Ads account. The campaigns and their locations are raised on failure; the rest is soft."""
    campaigns = read_campaigns(search(account_id, GAQL_CAMPAIGNS))
    locations = read_locations(search(account_id, GAQL_LOCATIONS))
    warnings = []
    names = {}
    wanted = sorted({loc["geoName"] for loc in locations if loc.get("geoName")})
    for i in range(0, len(wanted), NAME_CHUNK):
        chunk = wanted[i:i + NAME_CHUNK]
        names.update(_soft(warnings, "The names of the places", lambda chunk=chunk: read_names(search(account_id, GAQL_NAMES % ", ".join("'%s'" % w for w in chunk))), {}))
    locations, missing = apply_names(locations, names)
    if missing:
        warnings.append("%d of the %d targeted places have no name, so the place checks cover only the ones that do." % (missing, sum(1 for x in locations if x.get("kind") == "location")))
    schedules = _soft(warnings, "The ad schedules", lambda: read_schedules(search(account_id, GAQL_SCHEDULES)), [])
    devices = _soft(warnings, "The device adjustments", lambda: read_devices(search(account_id, GAQL_DEVICES)), [])
    groups = _soft(warnings, "The ad group names", lambda: read_groups(search(account_id, GAQL_GROUPS)), [])
    ads = _soft(warnings, "The Search ads", lambda: cc.read_search_ads(search(account_id, cc.GAQL_SEARCH_PLAIN)), [])
    keywords = _soft(warnings, "The keywords", lambda: read_keywords(search(account_id, GAQL_KEYWORDS)), [])
    if len(keywords) >= MAX_KEYWORDS:
        warnings.append("Only the first %d keywords were read." % MAX_KEYWORDS)
    return {"campaigns": campaigns, "locations": locations, "schedules": schedules, "devices": devices, "groups": groups, "ads": ads, "keywords": keywords, "warnings": warnings}


def read_meta_account(act, pages):
    path, params = meta_request(act)
    camps, locs = read_meta_adsets(pages(path, params))
    warnings = []
    ads = []
    try:
        for a in cc.read_meta_ads(pages(*cc.meta_request(act))):
            ads.append(dict(a, campaignId=a["adGroupId"], adGroupId=a["campaignId"], adGroup=a["campaign"], campaign=a["adGroup"]))
    except Exception as e:                                                           # noqa: BLE001 (Meta has its own errors)
        warnings.append("The Meta ads could not be read (%s), so the ad text was not checked." % str(e)[:160])
    return {"campaigns": camps, "locations": locs, "schedules": [], "devices": [], "groups": [], "ads": ads, "keywords": [], "warnings": warnings}


def google_raw(account, *, search_fn=None, workers=6):
    """{accounts: [{id, name, currency, platform, ...the read}], warnings, members}: one account or an MCC's accounts. An account whose campaigns cannot be read is left out and said so; every
    one failing is raised."""
    search = search_fn or gads.search
    members = account.get("members") or [account]

    def run(member):
        try:
            return member, read_account(member["id"], search), None
        except gads.GadsError as e:
            return member, None, e
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(members)))) as pool:
        done = list(pool.map(run, members))
    ok = [(mem, r) for mem, r, e in done if r is not None]
    if not ok:
        raise next(e for _m, _r, e in done if e)
    return {"accounts": [dict(r, id=mem["id"], name=mem["name"], currency=mem.get("currency") or account.get("currency") or "USD", platform="google") for mem, r in ok],
            "warnings": ["%s could not be read: %s" % (mem["name"], e) for mem, r, e in done if e], "members": [mem["name"] for mem in members] if account.get("members") else []}


def meta_raw(act, *, pages_fn, account_fn):
    acct = account_fn(act) or {}
    got = read_meta_account(act, pages_fn)
    return {"accounts": [dict(got, id=act, name=acct.get("name") or act, currency=acct.get("currency") or "USD", platform="meta")], "warnings": [], "members": []}


# ── The report ──────────────────────────────────────────────────────────────

_RANK = {"fix": 0, "check": 1, "clean": 2}


def other_places(copy_settings, account_id):
    """{place: account name} from the Ad Copy Checker's saved places of every other account: places this account's campaigns may not name."""
    out = {}
    for aid, a in ((copy_settings or {}).get("accounts") or {}).items():
        if aid == str(account_id):
            continue
        for p in a.get("places") or []:
            out.setdefault(p, a.get("name") or aid)
    return out


def assemble(raw, account, *, settings=None, copy_settings=None, issues=None, now=None):
    """The report from the raw read. `settings` is what the team saved here (tracker/paid_geoconf_settings.py load), `copy_settings` the Ad Copy Checker's, for the other accounts' places."""
    from . import paid_geoconf_settings as st
    settings = settings or st.empty()
    accounts, warnings = [], list(raw.get("warnings") or [])
    for a in raw["accounts"]:
        cfg = st.account_of(settings, a["id"])
        own = [p for p in ((copy_settings or {}).get("accounts") or {}).get(str(a["id"]), {}).get("places") or []]
        cfg = dict(cfg, areas=cfg["areas"] + [p for p in own if p.lower() not in [x.lower() for x in cfg["areas"]]])        # where the Ad Copy Checker was told this account's clients are
        res = math.analyse(a, config=cfg, other_places=other_places(copy_settings, a["id"]), platform=a.get("platform") or "google")
        names_read = any(x.get("name") or x.get("canonical") for x in a.get("locations") or [])
        accounts.append(dict(res, id=a["id"], name=a["name"], currency=a.get("currency") or "USD", platform=a.get("platform") or "google", namesRead=names_read or not a.get("locations"),
                             saved={"areas": len(cfg["areas"]), "ignore": len(cfg["ignore"]), "acceptPresence": bool(cfg["acceptPresence"])}, plan=plan.build(res)))
        warnings += ["%s: %s" % (a["name"], w) if account.get("members") else w for w in a.get("warnings") or []]
        if a.get("locations") and not names_read:
            warnings.append("%sThe names of the places could not be read, so no place was compared with a name." % ("%s: " % a["name"] if account.get("members") else ""))
    accounts.sort(key=lambda a: (_RANK[a["status"]], -a["counts"]["high"], -a["counts"]["medium"], -a["counts"]["low"], a["name"].lower()))
    counts = {k: 0 for k in _RANK}
    for a in accounts:
        counts[a["status"]] += 1
    return {
        "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or "USD", "platform": account.get("platform") or "google",
                    "isGroup": bool(account.get("members")), "accountCount": len(account.get("members") or [])},
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "warnings": warnings, "accounts": accounts,
        "rules": [{"rule": k, "label": math.RULES[k]["label"], "level": math.RULES[k]["level"], "fix": math.RULES[k]["fix"]} for k in math.ORDER],
        "summary": {"counts": counts, "accounts": len(accounts), "campaigns": sum(a["campaigns"] for a in accounts), "ads": sum(a["ads"] for a in accounts),
                    "high": sum(a["counts"]["high"] for a in accounts), "medium": sum(a["counts"]["medium"] for a in accounts), "low": sum(a["counts"]["low"] for a in accounts),
                    "findings": sum(a["findingCount"] for a in accounts)},
    }


def load_raw(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, force=False, store=None, today=None, meta_pages_fn=None, meta_account_fn=None):
    """(raw, stale, account): the saved read of an account's targeting, read from the platform when there is none (or when `force`)."""
    from . import paid_geo, paid_meta, paid_store
    store = store or paid_store
    day = today or dt.date.today()
    if paid_meta.is_meta(str(account_id or "")):
        act = paid_meta.norm_id(account_id)
        pages = meta_pages_fn or (lambda path, params: paid_meta._pages(path, params, store))
        acct_fn = meta_account_fn or (lambda a: paid_meta.account_by_id(store, a) or {})
        account = {"id": act, "name": (acct_fn(act) or {}).get("name") or act, "currency": (acct_fn(act) or {}).get("currency") or "USD", "platform": "meta"}
        raw, stale = paid_geo._cached("geoconf", act, "targeting", day, day, lambda: meta_raw(act, pages_fn=pages, account_fn=acct_fn), force=force, store=store, now=now)
    else:
        account = seg.resolve_target(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn)
        raw, stale = paid_geo._cached("geoconf", account["id"], "targeting", day, day, lambda: google_raw(account, search_fn=search_fn), force=force, store=store, now=now)
    return raw, stale, account


def report_for(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, force=False, store=None, today=None, mon=None, meta_pages_fn=None, meta_account_fn=None):
    """The Location Conflicts report for a Google Ads account or an MCC the root MCC holds, or for a Meta ad account (act_...). `force` (Refresh) skips the saved read, at most once per
    paid_gads.REFRESH_COOLDOWN_S for the account. `mon` is the monitors' store: with it what the team saved here and the places saved for the Ad Copy Checker are applied."""
    from . import paid_copycheck_settings as copy_st
    from . import paid_geoconf_settings as st
    warnings, settings, copy_settings = [], None, None
    if mon is not None:
        try:
            settings = st.load(mon)
            copy_settings = copy_st.load(mon)
        except Exception as e:                                                       # noqa: BLE001
            warnings.append("What the team saved (areas, words to ignore) could not be read (%s), so it was not applied." % e)
    raw, stale, account = load_raw(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn, search_fn=search_fn, now=now, force=force, store=store, today=today,
                                   meta_pages_fn=meta_pages_fn, meta_account_fn=meta_account_fn)
    report = assemble(raw, account, settings=settings, copy_settings=copy_settings)
    report["warnings"] = warnings + report["warnings"]
    if raw.get("readAt"):
        report["readAt"] = raw["readAt"]
    return dict(report, stale=stale) if stale else report
