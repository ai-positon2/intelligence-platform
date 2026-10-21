"""Paid Marketing Agents: Cross-Channel & Attribution Analysis, how each platform counts a conversion.

Two platforms that count differently cannot be compared on their own numbers. This module reads, read only, the settings that decide what each one counts and for how long:

  Google Ads   every enabled conversion action: its category, whether it is a primary action counted in the Conversions column, its attribution model, its click and view lookback windows
               (tracker/paid_audit_google_read.py reads and parses them for the Account Audit; the same query is used here), with the conversions each recorded in the period.
  Meta Ads     the attribution setting of every ad set that spent in the period (a click window and a view window, in days), weighted by what the ad set spent. An ad set that states none
               is counted at Meta's default (7 days after a click, 1 day after a view), and the page says the window was assumed.

`compare` then puts the two side by side and says, in words, what makes the platforms' numbers incomparable. A platform that cannot be read leaves its half out and says so.
"""
from __future__ import annotations

import json

from . import paid_audit_google_read as gread

SPEND_FIELDS = "adset_id,spend"
ADSET_FIELDS = "id,name,campaign_id,effective_status,attribution_spec"
PAGE = 500
DEFAULT_CLICK, DEFAULT_VIEW = 7, 1               # what Meta applies to an ad set that states no window

MODEL_LABEL = {
    "GOOGLE_ADS_LAST_CLICK": "Last click", "GOOGLE_SEARCH_ATTRIBUTION_DATA_DRIVEN": "Data-driven", "EXTERNAL": "Set outside Google Ads (imported)",
    "GOOGLE_SEARCH_ATTRIBUTION_FIRST_CLICK": "First click", "GOOGLE_SEARCH_ATTRIBUTION_LINEAR": "Linear", "GOOGLE_SEARCH_ATTRIBUTION_TIME_DECAY": "Time decay",
    "GOOGLE_SEARCH_ATTRIBUTION_POSITION_BASED": "Position based"}
# What a Google conversion action's category says the account is after, as the kinds Meta's objectives name.
LEAD_CATEGORIES = {"SUBMIT_LEAD_FORM", "CONTACT", "BOOK_APPOINTMENT", "REQUEST_QUOTE", "GET_DIRECTIONS", "SIGNUP", "QUALIFIED_LEAD", "CONVERTED_LEAD", "IMPORTED_LEAD", "PHONE_CALL_LEAD"}
SALES_CATEGORIES = {"PURCHASE", "ADD_TO_CART", "BEGIN_CHECKOUT", "SUBSCRIBE_PAID", "STORE_SALE", "CLOSED_WON"}
META_KIND = {"META_SALES": "sales", "META_LEADS": "leads"}


def _i(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ── Google Ads ──────────────────────────────────────────────────────────────

def volume_gaql(start, end):
    return ("SELECT segments.conversion_action, metrics.conversions, metrics.all_conversions, metrics.conversions_value, metrics.all_conversions_value "
            "FROM customer WHERE segments.date BETWEEN '%s' AND '%s'" % (start, end))


def read_google(search, start, end):
    """The account's conversion actions with their volume over start..end. `search(gaql)` returns the rows of a GAQL query. The actions are the point: a failure there is raised. The volumes
    are a nicety: without them every action's count is None (never 0) and the page says so."""
    actions = search(gread.CONVERSIONS_GAQL)
    try:
        volume = search(volume_gaql(start, end))
    except Exception:                                    # noqa: BLE001 (the settings still stand without the counts)
        volume = None
    return gread.parse_conversions(actions, volume)


# How long after the click Google's conversions came (click through only): Google's own buckets, which are named by their lower and upper day ("SIX_TO_SEVEN_DAYS").
_WORD = {"ONE": 1, "TWO": 2, "THREE": 3, "FOUR": 4, "FIVE": 5, "SIX": 6, "SEVEN": 7, "EIGHT": 8, "NINE": 9, "TEN": 10, "ELEVEN": 11, "TWELVE": 12, "THIRTEEN": 13, "FOURTEEN": 14,
         "FIFTEEN": 15, "SIXTEEN": 16, "SEVENTEEN": 17, "EIGHTEEN": 18, "NINETEEN": 19, "TWENTY": 20, "THIRTY": 30, "FORTY": 40, "FIFTY": 50, "SIXTY": 60, "SEVENTY": 70, "EIGHTY": 80, "NINETY": 90}


def lag_gaql(start, end):
    return "SELECT segments.conversion_lag_bucket, metrics.conversions FROM customer WHERE segments.date BETWEEN '%s' AND '%s'" % (start, end)


def bucket_days(name):
    """The day a lag bucket starts on (LESS_THAN_ONE_DAY is 0, ONE_TO_TWO_DAYS 1, TWENTY_ONE_TO_THIRTY_DAYS 21), or None when the name is not one this understands."""
    n = str(name or "").upper()
    if n.startswith("LESS_THAN"):
        return 0
    head = n.split("_TO_")[0]
    if head == n:
        return None
    parts = [_WORD.get(w) for w in head.split("_")]
    return sum(parts) if parts and all(p is not None for p in parts) else None


def read_lag(search, start, end):
    """{buckets: {start day: conversions}, total} for Google's conversions over start..end by how long after the click they came, or None when there is nothing to say."""
    rows = search(lag_gaql(start, end))
    out, total = {}, 0.0
    for r in rows:
        n = _f((r.get("metrics") or {}).get("conversions"))
        day = bucket_days((r.get("segments") or {}).get("conversionLagBucket"))
        total += n
        if day is not None:
            out[day] = out.get(day, 0.0) + n
    return {"buckets": {str(k): round(v, 4) for k, v in sorted(out.items())}, "total": round(total, 4)} if total > 0 and out else None


def window_share(lag, days):
    """The share of Google's conversions that came `days` days or more after the click (what a window of that many days would not count), or None when it cannot be told."""
    if not lag or not lag.get("total") or days is None:
        return None
    later = sum(v for k, v in lag["buckets"].items() if int(k) >= days)
    return round(later / lag["total"], 4)


def category_kind(category):
    c = str(category or "").upper()
    return "leads" if c in LEAD_CATEGORIES else "sales" if c in SALES_CATEGORIES else "other"


def summarise_google(actions):
    """What Google counts: the enabled actions that are primary and in the Conversions column, grouped by lookback window, model and category, each weighted by the conversions it
    recorded (or counted once each when the volumes could not be read)."""
    live = [a for a in actions if a["status"] == "ENABLED"]
    counted = [a for a in live if a["primary"] and a["included"]]
    known = bool(counted) and all(a["conversions"] is not None for a in counted)

    def weight(a):
        return (a["conversions"] or 0.0) if known else 1.0

    def group(key):
        out = {}
        for a in counted:
            g = out.setdefault(key(a), {"actions": 0, "conversions": 0.0, "names": []})
            g["actions"] += 1
            g["conversions"] += weight(a)
            g["names"].append(a["name"])
        return out

    clicks = group(lambda a: a["clickWindow"])
    views = group(lambda a: a["viewWindow"])
    models = group(lambda a: a["attribution"] or "UNKNOWN")
    cats = group(lambda a: a["category"] or "UNKNOWN")

    def top(g):
        return max(g, key=lambda k: (g[k]["conversions"], g[k]["actions"], -(k if isinstance(k, int) else 0))) if g else None

    kinds = {}
    for a in counted:
        k = category_kind(a["category"])
        kinds[k] = kinds.get(k, 0.0) + weight(a)
    return {"counted": [{"id": a["id"], "name": a["name"], "category": a["category"], "type": a["type"], "model": MODEL_LABEL.get(a["attribution"]) or ("Not stated" if not a["attribution"] else a["attribution"].replace("_", " ").title()),
                         "clickWindow": a["clickWindow"], "viewWindow": a["viewWindow"], "conversions": a["conversions"], "origin": a["origin"]} for a in counted],
            "secondary": [{"id": a["id"], "name": a["name"], "category": a["category"]} for a in live if a not in counted],
            "volumeKnown": known, "enabled": len(live),
            "clickWindows": [{"days": k, "actions": v["actions"], "conversions": round(v["conversions"], 2) if known else None} for k, v in sorted(clicks.items())],
            "viewWindows": [{"days": k, "actions": v["actions"]} for k, v in sorted(views.items())],
            "models": [{"model": k, "label": MODEL_LABEL.get(k, k.replace("_", " ").title()), "actions": v["actions"], "conversions": round(v["conversions"], 2) if known else None} for k, v in sorted(models.items())],
            "categories": [{"category": k, "actions": v["actions"], "kind": category_kind(k)} for k, v in sorted(cats.items())],
            "dominantClick": top(clicks), "dominantModel": top(models), "kinds": {k: round(v, 2) for k, v in kinds.items()}}


# ── Meta Ads ────────────────────────────────────────────────────────────────

def _spec_days(spec, event):
    """The window in days Meta's attribution_spec gives for an event ("CLICK_THROUGH", "VIEW_THROUGH"), or None when it does not list it."""
    for s in spec or []:
        if isinstance(s, dict) and s.get("event_type") == event:
            d = _i(s.get("window_days"), None)
            if d is not None:
                return d
    return None


def parse_meta(adset_rows, spend_rows):
    """The ad sets that spent, each with its window: {"adsets": [...], "groups": [{click, view, spend, share, assumed, adsets}], "viewShare", "spend", "unmatched"}. An ad set Meta reports spend for but did
    not list (deleted since) is counted at the default and counted in `unmatched`."""
    spent = {}
    for r in spend_rows or []:
        sid = str(r.get("adset_id") or "")
        if sid:
            spent[sid] = spent.get(sid, 0.0) + _f(r.get("spend"))
    by_id = {str(a.get("id")): a for a in adset_rows or [] if a.get("id")}
    adsets, unmatched = [], 0
    for sid, spend in spent.items():
        if spend <= 0:
            continue
        a = by_id.get(sid)
        if a is None:
            unmatched += 1
        spec = (a or {}).get("attribution_spec")
        click, view = _spec_days(spec, "CLICK_THROUGH"), _spec_days(spec, "VIEW_THROUGH")
        stated = click is not None or view is not None
        adsets.append({"id": sid, "name": (a or {}).get("name") or sid, "spend": round(spend, 2), "click": DEFAULT_CLICK if click is None and not stated else (click or 0),
                       "view": DEFAULT_VIEW if view is None and not stated else (view or 0), "assumed": not stated})
    total = sum(a["spend"] for a in adsets)
    groups = {}
    for a in adsets:
        g = groups.setdefault((a["click"], a["view"], a["assumed"]), {"click": a["click"], "view": a["view"], "assumed": a["assumed"], "spend": 0.0, "adsets": 0})
        g["spend"] += a["spend"]
        g["adsets"] += 1
    out = sorted(groups.values(), key=lambda g: -g["spend"])
    for g in out:
        g["spend"] = round(g["spend"], 2)
        g["share"] = round(g["spend"] / total * 100, 1) if total else None
    view_spend = sum(a["spend"] for a in adsets if a["view"] > 0)
    return {"adsets": sorted(adsets, key=lambda a: -a["spend"])[:50], "groups": out, "adsetCount": len(adsets), "spend": round(total, 2),
            "viewShare": round(view_spend / total * 100, 1) if total else None, "unmatched": unmatched,
            "dominant": ({"click": out[0]["click"], "view": out[0]["view"], "assumed": out[0]["assumed"]} if out else None)}


def read_meta(pages, start, end):
    """The ad sets of the account and what each spent over start..end. `pages(path, params)` is the Graph list call bound to an account path prefix by the caller."""
    adsets = pages("adsets", {"fields": ADSET_FIELDS, "limit": PAGE})
    spend = pages("insights", {"level": "adset", "time_range": json.dumps({"since": start, "until": end}), "fields": SPEND_FIELDS, "limit": PAGE})
    return parse_meta(adsets, spend)


# ── Side by side ────────────────────────────────────────────────────────────

def _days(n):
    return "%d day%s" % (n, "" if n == 1 else "s")


def compare(google, meta, *, meta_kinds=None, google_view=None):
    """In words, what makes the two platforms' numbers incomparable. `google` is summarise_google's answer and `meta` parse_meta's (either may be None when it could not be read);
    `meta_kinds` is {"sales": spend, "leads": spend, ...} of the Meta campaigns' objectives in the period and `google_view` Google's view-through conversions in it.
    Returns [{"id", "level", "title", "text"}], the most important first; level is high, medium or info."""
    notes = []
    if not google or not meta or not google.get("counted") or not meta.get("dominant"):
        return notes
    gc, mc = google["dominantClick"], meta["dominant"]["click"]
    if gc is not None and mc is not None and gc and gc != mc:
        longer, shorter = ("Google", "Meta") if gc > mc else ("Meta", "Google")
        notes.append({"id": "click-window", "level": "high", "title": "The platforms count conversions over different click windows",
                      "text": "Google counts a conversion up to %s after a click (most of its conversions), Meta up to %s. %s's longer window counts conversions that happen later, so for the same people it reports more "
                              "than %s does. Part of any gap between the two platforms' cost per conversion is the window, not the ads." % (_days(gc), _days(mc), longer, shorter)})
    view = meta["dominant"]["view"]
    if meta.get("viewShare"):
        extra = "" if google_view is None else " Google's own view-through conversions in this period (%s) are a separate column and are not in its Conversions." % _fmt(google_view)
        notes.append({"id": "view-through", "level": "high", "title": "Meta counts people who saw the ad and did not click",
                      "text": "%.0f%% of Meta's spend is in ad sets that also count a conversion up to %s after someone saw the ad without clicking. Google's Conversions column counts clicks only.%s "
                              "That makes Meta's number larger than a count of clicks alone would be." % (meta["viewShare"], _days(max(view, 1)), extra)})
    mk = google.get("dominantModel")
    if mk:
        label = MODEL_LABEL.get(mk, mk.replace("_", " ").title())
        notes.append({"id": "models", "level": "medium", "title": "Each platform gives itself the credit",
                      "text": "Google's main model for what it counts is %s; Meta credits its own ads. Neither platform sees the other's ads, so a person who clicked both is counted by both. The blended "
                              "total here is the two platforms' claims added, so it counts that person twice: type what your own system recorded, at the top of the page, to see by how much." % label.lower()})
    if meta_kinds:
        total = sum(meta_kinds.values())
        mk_name = max(("sales", "leads"), key=lambda k: meta_kinds.get(k, 0.0))
        share = meta_kinds.get(mk_name, 0.0) / total if total else 0
        gk = google.get("kinds") or {}
        gmain = max(gk, key=gk.get) if gk else None
        if gmain and share >= 0.5 and gmain in ("sales", "leads") and gmain != mk_name:
            notes.append({"id": "goal-mismatch", "level": "high", "title": "The platforms are counting different goals",
                          "text": "Google's main conversion actions are %s, while %.0f%% of Meta's spend is on %s campaigns. A cost per conversion across the two compares different outcomes." % (gmain, share * 100, mk_name)})
    if google["secondary"]:
        notes.append({"id": "secondary", "level": "info", "title": "Some Google conversion actions are not in the Conversions column",
                      "text": "%d enabled action%s count%s under All conversions only (%s). The page's primary measure leaves %s out; the all-conversions measure includes %s." % (
                          len(google["secondary"]), "" if len(google["secondary"]) == 1 else "s", "s" if len(google["secondary"]) == 1 else "", ", ".join(a["name"] for a in google["secondary"][:4]),
                          "it" if len(google["secondary"]) == 1 else "them", "it" if len(google["secondary"]) == 1 else "them")})
    order = {"high": 0, "medium": 1, "info": 2}
    notes.sort(key=lambda n: order[n["level"]])
    return notes


def _fmt(n):
    return "{:,.0f}".format(n) if abs(n - round(n)) < 0.05 or abs(n) >= 100 else "{:,.1f}".format(n)
