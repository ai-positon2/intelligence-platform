"""Paid Marketing Agents: QA Agent for a Meta campaign, what Meta can answer exactly.

The Meta launch checklist (tracker/qa_checklist_meta.json) is about one campaign's own settings: its name, objective, special ad category, budget and bid strategy, then each ad set's
conversion location, optimisation goal, dataset and event, attribution, places, ages, genders, audience and placements, then each ad's text, button, address, tracking parameters and
review status. This reads the campaign, its ad sets and its ads from Meta and runs a rule for every check whose answer Meta gives exactly.

The one rule that matters is the Google side's: **a rule ticks or crosses a box only when the answer is exact.** Where the right answer depends on the campaign book (the objective, the
special ad category, the conversion location, the performance goal, the audience) it prints what is set and compares it with the book only when the book names an unambiguous value. What Meta
does not show to a reader (whether the dataset received events, Conversions API, event match quality, domain verification) is never ticked: the AI reviewer and then a person take it from
there. A false tick is worse than an empty box.

Reading is `gather`, rules are `evaluate`, neither touches a database. The Meta transport is injected as `get_fn(path, params)` (one object) and `pages_fn(path, params)` (a list), so tests run
on Meta's documented shapes.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from urllib.parse import parse_qsl, urlsplit

from . import paid_meta as meta
from . import paid_qa_book

log = logging.getLogger(__name__)

ID_RE = re.compile(r"^\d{1,20}$")
MAX_FOUND = 1500
CAMPAIGN_FIELDS = "id,name,objective,status,effective_status,special_ad_categories,buying_type,daily_budget,lifetime_budget,bid_strategy,start_time,stop_time"
ADSET_FIELDS = ("id,name,effective_status,daily_budget,lifetime_budget,bid_strategy,bid_amount,optimization_goal,billing_event,destination_type,promoted_object,attribution_spec,"
                "start_time,end_time,targeting,learning_stage_info")
AD_FIELDS = ("id,name,effective_status,adset_id,ad_review_feedback,creative{id,object_type,body,title,call_to_action_type,object_story_spec,asset_feed_spec,url_tags,"
             "degrees_of_freedom_spec,instagram_actor_id,video_id,image_hash}")
LIVE = ["ACTIVE", "PAUSED", "CAMPAIGN_PAUSED", "ADSET_PAUSED", "WITH_ISSUES", "IN_PROCESS", "PENDING_REVIEW", "DISAPPROVED"]
KIND = {"LEADS": "Meta Leads", "SALES": "Meta Sales", "TRAFFIC": "Meta Traffic", "AWARENESS": "Meta Awareness"}
NAME_RE = re.compile(r"^P2_Meta(Leads|Sales|Traffic|Awareness)_(.+)_(\d{8})$", re.I)
LIMITS = {"primary": 125, "headline": 40, "description": 30}
MIN_ADS, MAX_ADS = 3, 10
DEST = {"WEBSITE": "website", "ON_AD": "instant forms", "PHONE_CALL": "calls", "MESSENGER": "messaging", "WHATSAPP": "messaging", "INSTAGRAM_DIRECT": "messaging", "APP": "app", "ON_POST": "post",
        "ON_VIDEO": "video", "ON_PAGE": "page", "ON_EVENT": "event"}
GOAL = {"LEAD_GENERATION": "leads", "QUALITY_LEAD": "quality leads", "OFFSITE_CONVERSIONS": "conversions", "LANDING_PAGE_VIEWS": "landing page views", "LINK_CLICKS": "link clicks",
        "REACH": "reach", "IMPRESSIONS": "impressions", "VALUE": "value", "CONVERSATIONS": "conversations", "THRUPLAY": "thruplay", "APP_INSTALLS": "app installs", "POST_ENGAGEMENT": "engagement"}


class Finding(dict):
    """result: 'pass', 'fail' or None (printed only); found: what was read."""

    def __init__(self, result, found):
        found = " ".join(str(found).split())
        if len(found) > MAX_FOUND:
            found = found[:MAX_FOUND - 3].rstrip() + "..."
        super().__init__(result=result, found=found)


F = Finding


def type_label(objective):
    return KIND.get(meta.objective_kind(objective), "")


# ── Which campaigns ─────────────────────────────────────────────────────────

def list_campaigns(act, pages_fn):
    """The live (active) campaigns of a Meta ad account, by name: [{id, name, channel, type}]. `channel` is META_ plus the objective, so the page and the store keep a type word."""
    rows = pages_fn("/%s/campaigns" % act, {"fields": "id,name,objective,effective_status", "effective_status": json.dumps(["ACTIVE"]), "limit": 200})
    out = []
    for r in rows:
        if r.get("id"):
            kind = meta.objective_kind(r.get("objective"))
            out.append({"id": str(r["id"]), "name": r.get("name") or ("Campaign " + str(r["id"])), "channel": "META_" + kind, "type": KIND.get(kind, "")})
    return sorted(out, key=lambda c: c["name"].lower())


# ── Reading one campaign ────────────────────────────────────────────────────

def _list(items, limit=6):
    items = [str(i) for i in items]
    return ", ".join(items[:limit]) + (" and %d more" % (len(items) - limit) if len(items) > limit else "")


def ad_info(ad):
    """One ad as plain facts: its texts (every variant), button, addresses, page and Instagram account, format, tracking parameters and the creative enhancements that are on."""
    c = ad.get("creative") or {}
    oss = c.get("object_story_spec") or {}
    afs = c.get("asset_feed_spec") or {}
    link = oss.get("link_data") or {}
    video = oss.get("video_data") or {}
    photo = oss.get("photo_data") or {}
    texts = lambda key: [t.get("text") for t in afs.get(key) or [] if isinstance(t, dict) and t.get("text")]          # noqa: E731
    primary = texts("bodies") or [x for x in (c.get("body"), link.get("message"), video.get("message"), photo.get("caption")) if x][:1]
    heads = texts("titles") or [x for x in (c.get("title"), link.get("name"), video.get("title")) if x][:1]
    descs = texts("descriptions") or [x for x in (link.get("description"), video.get("link_description")) if x][:1]
    urls = [u.get("website_url") for u in afs.get("link_urls") or [] if isinstance(u, dict) and u.get("website_url")] or [x for x in (link.get("link"), (video.get("call_to_action") or {}).get("value", {}).get("link")) if x][:1]
    ctas = [str(x) for x in afs.get("call_to_action_types") or []] or [x for x in (c.get("call_to_action_type"), (link.get("call_to_action") or {}).get("type"), (video.get("call_to_action") or {}).get("type")) if x][:1]
    kids = link.get("child_attachments") or []
    fmt = "video" if (c.get("video_id") or video or str(c.get("object_type")).upper() == "VIDEO") else "carousel" if len(kids) >= 2 else "image" if (c.get("image_hash") or photo or link or c.get("object_type")) else "unknown"
    feats = ((c.get("degrees_of_freedom_spec") or {}).get("creative_features_spec") or {})
    return {"id": str(ad.get("id") or ""), "name": ad.get("name") or "", "status": str(ad.get("effective_status") or ""), "adset": str(ad.get("adset_id") or ""), "primary": primary, "headlines": heads,
            "descriptions": descs, "urls": urls, "ctas": ctas, "format": fmt, "page": str(oss.get("page_id") or ""), "instagram": str(c.get("instagram_actor_id") or oss.get("instagram_actor_id") or oss.get("instagram_user_id") or ""),
            "urlTags": c.get("url_tags") or "", "enhancements": sorted(k for k, v in feats.items() if isinstance(v, dict) and str(v.get("enroll_status")).upper() == "OPT_IN"),
            "feedback": ad.get("ad_review_feedback")}


def gather(act, campaign_id, get_fn, pages_fn, *, web_fn=None, book=None):
    """Everything the rules and the reviewer read about one campaign. The campaign itself failing is raised; the ad sets or the ads failing leaves the checks that need them unread (`_errors`)."""
    if not ID_RE.match(str(campaign_id)):
        raise ValueError("That is not a campaign id")
    camp = get_fn("/" + str(campaign_id), {"fields": CAMPAIGN_FIELDS})
    errors = {}
    status = json.dumps(LIVE)
    adsets, ads = [], []
    try:
        adsets = pages_fn("/%s/adsets" % campaign_id, {"fields": ADSET_FIELDS, "effective_status": status, "limit": 200})
    except Exception as e:                                  # noqa: BLE001
        errors["adsets"] = str(e)[:200]
    try:
        ads = [ad_info(a) for a in pages_fn("/%s/ads" % campaign_id, {"fields": AD_FIELDS, "effective_status": status, "limit": 200})]
    except Exception as e:                                  # noqa: BLE001
        errors["ads"] = str(e)[:200]
    data = {"campaign": camp or {}, "adsets": adsets, "ads": ads, "_errors": errors, "_pages": [], "_book": book or None}
    urls = sorted({u for a in ads if a["status"] == "ACTIVE" for u in a["urls"]})
    if web_fn and urls:
        try:
            data["_pages"] = web_fn(urls)
        except Exception as e:                              # noqa: BLE001 (the pages are an extra: the rules say they were not opened)
            errors["pages"] = str(e)[:200]
    return data


# ── The rules ───────────────────────────────────────────────────────────────

class Ctx:
    def __init__(self, data):
        self.d = data
        self.camp = data.get("campaign") or {}
        self.adsets = data.get("adsets") or []
        self.ads = data.get("ads") or []
        self.book = data.get("_book")
        self.errors = data.get("_errors") or {}

    def unread(self, what):
        return F(None, "Not available: the %s could not be read from Meta (%s)." % (what, self.errors[what])) if what in self.errors else None

    def live_sets(self):
        return [a for a in self.adsets if str(a.get("effective_status")) == "ACTIVE"]

    def live_ads(self):
        return [a for a in self.ads if a["status"] == "ACTIVE"]

    def lookup(self, *names):
        return paid_qa_book.lookup(self.book, *names)


RULES = {}


def rule(*ids):
    def deco(fn):
        for i in ids:
            RULES[i] = fn
        return fn
    return deco


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _money(v, cur=""):
    try:
        return "%s%s" % ("{:,.2f}".format(int(v) / 100.0), (" " + cur) if cur else "")
    except (TypeError, ValueError):
        return str(v)


@rule("1.1")
def r_name(c):
    name = c.camp.get("name") or ""
    if not name:
        return F(None, "Not available.")
    m = NAME_RE.match(name)
    if not m:
        return F("fail", "The name is \"%s\": it does not follow P2_<MetaLeads, MetaSales, MetaTraffic or MetaAwareness>_<theme>_<launch date as 8 digits>." % name)
    return F("pass", "The name is \"%s\" (%s campaign, launch date %s)." % (name, m.group(1), m.group(3)))


@rule("1.2")
def r_objective(c):
    obj = str(c.camp.get("objective") or "")
    if not obj:
        return F(None, "Not available.")
    kind = meta.objective_kind(obj)
    word = {"LEADS": "leads", "SALES": "sales", "TRAFFIC": "traffic", "AWARENESS": "awareness"}.get(kind)
    text = "The objective is %s (%s)." % (obj, word or kind.lower())
    m = NAME_RE.match(c.camp.get("name") or "")
    if m and word and m.group(1).lower() != word:
        return F("fail", "%s The name says %s." % (text, m.group(1)))
    book = c.lookup("Campaign Objective")
    if book and word:
        said = _norm(book)
        if word in said.split() or word in said:
            return F("pass", "%s The campaign book says %s." % (text, book))
        if any(w in said for w in ("leads", "sales", "traffic", "awareness")):
            return F("fail", "%s The campaign book says %s." % (text, book))
    return F(None, text + (" The name says the same." if m and word else ""))


@rule("1.3")
def r_special(c):
    cats = c.camp.get("special_ad_categories")
    if cats is None:
        return F(None, "Not available.")
    live = [x for x in cats if str(x).upper() != "NONE"]
    text = "Special ad categories: %s." % (_list(live) if live else "none")
    book = c.lookup("Special Ad Categories")
    if book:
        none_book = _norm(book).startswith("none")
        if none_book and live:
            return F("fail", "%s The campaign book says none." % text)
        if none_book and not live:
            return F("pass", "%s The campaign book says none." % text)
    return F(None, text)


@rule("1.4")
def r_buying(c):
    bt = str(c.camp.get("buying_type") or "")
    if not bt:
        return F(None, "Not available.")
    return F("pass" if bt.upper() == "AUCTION" else "fail", "The buying type is %s." % bt)


@rule("1.5")
def r_budget(c):
    cur = ""
    lines = []
    if c.camp.get("daily_budget") or c.camp.get("lifetime_budget"):
        lines.append("campaign budget: %s %s" % ("daily" if c.camp.get("daily_budget") else "lifetime", _money(c.camp.get("daily_budget") or c.camp.get("lifetime_budget"), cur)))
    for a in c.live_sets()[:6]:
        if a.get("daily_budget") or a.get("lifetime_budget"):
            lines.append("%s: %s %s" % (a.get("name"), "daily" if a.get("daily_budget") else "lifetime", _money(a.get("daily_budget") or a.get("lifetime_budget"), cur)))
    if not lines:
        return F(None, "No budget was read on the campaign or on its active ad sets.")
    return F(None, "Budget (in the account's currency): " + "; ".join(lines) + ".")


@rule("1.6")
def r_bid(c):
    strat = {str(c.camp.get("bid_strategy") or "")} | {str(a.get("bid_strategy") or "") for a in c.live_sets()}
    strat.discard("")
    if not strat:
        return F(None, "No bid strategy was read.")
    return F(None, "Bid strategy: %s." % _list(sorted(strat)))


@rule("1.7")
def r_dates(c):
    if not c.camp:
        return F(None, "Not available.")
    stops = [x for x in [c.camp.get("stop_time")] + [a.get("end_time") for a in c.adsets] if x]
    lifetime = bool(c.camp.get("lifetime_budget")) or any(a.get("lifetime_budget") for a in c.adsets)
    start = c.camp.get("start_time") or ""
    text = "Started %s." % (start[:10] or "on a date Meta did not show")
    if stops and not lifetime:
        return F("fail", "%s There is an end date: %s." % (text, _list(sorted({s[:10] for s in stops}))))
    if stops and lifetime:
        return F(None, "%s It has a lifetime budget, which Meta needs an end date for: %s." % (text, _list(sorted({s[:10] for s in stops}))))
    return F("pass", text + " No end date.")


@rule("1.8")
def r_counts(c):
    if c.unread("adsets") or c.unread("ads"):
        return c.unread("adsets") or c.unread("ads")
    sets = c.live_sets()
    if not sets:
        return F("fail", "The campaign has no active ad set.")
    per = {a["id"]: 0 for a in sets}
    for ad in c.live_ads():
        if ad["adset"] in per:
            per[ad["adset"]] += 1
    names = {str(a["id"]): a.get("name") for a in sets}
    short = ["%s (%d)" % (names[str(k)], v) for k, v in per.items() if v < MIN_ADS]
    long = ["%s (%d)" % (names[str(k)], v) for k, v in per.items() if v > MAX_ADS]
    text = "%d active ad set%s with %d active ads." % (len(sets), "" if len(sets) == 1 else "s", sum(per.values()))
    if short:
        return F("fail", "%s Fewer than %d ads in: %s." % (text, MIN_ADS, _list(short)))
    if long:
        return F("fail", "%s More than %d ads in: %s." % (text, MAX_ADS, _list(long)))
    return F("pass", text + " Every ad set has between %d and %d." % (MIN_ADS, MAX_ADS))


@rule("1.9")
def r_identity(c):
    if c.unread("ads"):
        return c.unread("ads")
    ads = c.live_ads()
    if not ads:
        return F(None, "No active ad was read.")
    no_page = [a["name"] for a in ads if not a["page"]]
    no_ig = [a["name"] for a in ads if not a["instagram"]]
    if no_page:
        return F("fail", "No Facebook Page is set on: %s." % _list(no_page))
    if no_ig:
        return F("fail", "No Instagram account is set on: %s." % _list(no_ig))
    return F("pass", "All %d active ads have a Facebook Page and an Instagram account." % len(ads))


@rule("2.1")
def r_location(c):
    if c.unread("adsets"):
        return c.unread("adsets")
    sets = c.live_sets()
    if not sets:
        return F(None, "No active ad set was read.")
    words = sorted({DEST.get(str(a.get("destination_type") or "WEBSITE").upper(), str(a.get("destination_type")).lower()) for a in sets})
    text = "Conversion location: %s." % _list(words)
    book = c.lookup("Conversion Location")
    if book:
        said = _norm(book)
        if all(w in said for w in words):
            return F("pass", "%s The campaign book says %s." % (text, book))
        if any(w in said for w in DEST.values()):
            return F("fail", "%s The campaign book says %s." % (text, book))
    return F(None, text)


@rule("2.2")
def r_goal(c):
    if c.unread("adsets"):
        return c.unread("adsets")
    sets = c.live_sets()
    if not sets:
        return F(None, "No active ad set was read.")
    goals = sorted({str(a.get("optimization_goal") or "") for a in sets} - {""})
    words = [GOAL.get(g, g.lower().replace("_", " ")) for g in goals]
    text = "Performance goal: %s." % _list(words)
    book = _norm(c.lookup("Performance Goal (optimisation)") or "")
    if book and words:
        if all(w in book for w in words):
            return F("pass", "%s The campaign book says the same." % text)
        if any(w in book for w in GOAL.values()):
            return F("fail", "%s The campaign book says %s." % (text, c.lookup("Performance Goal (optimisation)")))
    kind = meta.objective_kind(c.camp.get("objective"))
    if kind in ("LEADS", "SALES") and goals and all(g in ("LINK_CLICKS", "IMPRESSIONS", "REACH", "LANDING_PAGE_VIEWS") for g in goals):
        return F("fail", "%s That counts clicks, views or reach on a %s campaign, which is meant to bring results." % (text, kind.lower()))
    return F(None, text)


@rule("2.3")
def r_dataset(c):
    if c.unread("adsets"):
        return c.unread("adsets")
    sets = c.live_sets()
    if not sets:
        return F(None, "No active ad set was read.")
    missing = [a.get("name") for a in sets if not (a.get("promoted_object") or {}).get("pixel_id") and not (a.get("promoted_object") or {}).get("custom_conversion_id")]
    events = sorted({str((a.get("promoted_object") or {}).get("custom_event_type") or "") for a in sets} - {""})
    text = "Events set: %s." % (_list(events) if events else "none")
    if missing:
        return F("fail", "No dataset (pixel) is set on: %s. %s" % (_list(missing), text))
    book = _norm(c.lookup("Dataset (pixel) and Conversion Event") or "")
    if book and events:
        if all(_norm(e) in book for e in events):
            return F("pass", "A dataset is set on every active ad set. %s The campaign book names the same event." % text)
        return F("fail", "A dataset is set. %s The campaign book says %s." % (text, c.lookup("Dataset (pixel) and Conversion Event")))
    return F(None, "A dataset is set on every active ad set. " + text)


def _window(spec):
    out = []
    for s in spec or []:
        kind = {"CLICK_THROUGH": "click", "VIEW_THROUGH": "view", "ENGAGED_VIDEO_VIEW": "engaged video view"}.get(str(s.get("event_type")), str(s.get("event_type")).lower())
        out.append("%s-day %s" % (s.get("window_days"), kind))
    return " and ".join(out)


@rule("2.4")
def r_attribution(c):
    if c.unread("adsets"):
        return c.unread("adsets")
    sets = c.live_sets()
    if not sets:
        return F(None, "No active ad set was read.")
    wins = sorted({_window(a.get("attribution_spec")) for a in sets} - {""})
    if not wins:
        return F(None, "Meta showed no attribution setting: it uses its default.")
    text = "Attribution: %s." % _list(wins)
    book = c.lookup("Attribution Setting")
    if book and all(_norm(w) in _norm(book) for w in wins):
        return F("pass", "%s The campaign book says %s." % (text, book))
    if book and re.search(r"\d+ day", _norm(book)) and not all(_norm(w) in _norm(book) for w in wins):
        return F("fail", "%s The campaign book says %s." % (text, book))
    return F(None, text)


def _geo(t):
    g = (t or {}).get("geo_locations") or {}
    parts = []
    parts += [str(x) for x in g.get("countries") or []]
    parts += [str(r.get("name") or r.get("key")) for r in g.get("regions") or []]
    parts += ["%s (%s %s)" % (r.get("name"), r.get("radius"), r.get("distance_unit") or "mile") if r.get("radius") else str(r.get("name")) for r in g.get("cities") or []]
    parts += [str(z.get("name") or z.get("key")) for z in g.get("zips") or []]
    parts += ["%s (%s %s)" % (x.get("name") or x.get("address_string") or "a point", x.get("radius"), x.get("distance_unit") or "mile") for x in g.get("custom_locations") or []]
    return parts


@rule("2.5")
def r_places(c):
    if c.unread("adsets"):
        return c.unread("adsets")
    sets = c.live_sets()
    if not sets:
        return F(None, "No active ad set was read.")
    lines = ["%s: %s" % (a.get("name"), _list(_geo(a.get("targeting"))) or "none") for a in sets[:6]]
    return F(None, "Places: " + "; ".join(lines) + ".")


@rule("2.6")
def r_location_type(c):
    if c.unread("adsets"):
        return c.unread("adsets")
    sets = c.live_sets()
    if not sets:
        return F(None, "No active ad set was read.")
    types = sorted({tuple(sorted((a.get("targeting") or {}).get("geo_locations", {}).get("location_types") or ["home", "recent"])) for a in sets})
    ok = [("home", "recent")]
    if all(t in ok for t in types):
        return F("pass", "People living in or recently in the location, on every active ad set.")
    return F("fail", "Location type set to %s." % _list(["+".join(t) for t in types]))


def _ints(text):
    return [int(x) for x in re.findall(r"\d+", str(text or ""))]


@rule("2.7")
def r_age(c):
    if c.unread("adsets"):
        return c.unread("adsets")
    sets = c.live_sets()
    if not sets:
        return F(None, "No active ad set was read.")
    ranges = sorted({(int((a.get("targeting") or {}).get("age_min") or 18), int((a.get("targeting") or {}).get("age_max") or 65)) for a in sets})
    text = "Age: %s." % _list(["%d to %d" % r for r in ranges])
    book = c.lookup("Age")
    if book:
        nums = _ints(book)
        if _norm(book).startswith("all"):
            return F("pass" if ranges == [(18, 65)] else "fail", "%s The campaign book says %s." % (text, book))
        if len(nums) >= 2:
            return F("pass" if ranges == [(nums[0], nums[1])] else "fail", "%s The campaign book says %s." % (text, book))
    return F(None, text)


@rule("2.8")
def r_gender(c):
    if c.unread("adsets"):
        return c.unread("adsets")
    sets = c.live_sets()
    if not sets:
        return F(None, "No active ad set was read.")
    word = {(): "all genders", (1,): "men", (2,): "women", (1, 2): "men and women"}
    seen = sorted({word.get(tuple(sorted((a.get("targeting") or {}).get("genders") or [])), "a custom set") for a in sets})
    text = "Gender: %s." % _list(seen)
    book = _norm(c.lookup("Gender") or "")
    if book.startswith("all"):
        return F("pass" if seen == ["all genders"] else "fail", "%s The campaign book says %s." % (text, c.lookup("Gender")))
    return F(None, text)


@rule("2.9")
def r_languages(c):
    if c.unread("adsets"):
        return c.unread("adsets")
    sets = c.live_sets()
    if not sets:
        return F(None, "No active ad set was read.")
    locs = sorted({str(x) for a in sets for x in (a.get("targeting") or {}).get("locales") or []})
    if not locs:
        return F("pass", "No language is set on the active ad sets (all languages).")
    book = c.lookup("Languages")
    if book and not _norm(book).startswith("all"):
        return F(None, "Languages set (Meta's own codes): %s. The campaign book says %s." % (_list(locs), book))
    return F("fail", "Languages are set (Meta's own codes: %s) though the standard is none." % _list(locs))


def _audience(t):
    t = t or {}
    inc, exc = [], []
    for g in t.get("flexible_spec") or []:
        for key, items in (g.items() if isinstance(g, dict) else []):
            inc += ["%s: %s" % (key, i.get("name")) for i in items or [] if isinstance(i, dict)]
    inc += ["audience: %s" % i.get("name") for i in t.get("custom_audiences") or [] if isinstance(i, dict)]
    for i in t.get("excluded_custom_audiences") or []:
        exc.append("audience: %s" % i.get("name"))
    for key, items in (t.get("exclusions") or {}).items():
        exc += ["%s: %s" % (key, i.get("name")) for i in items or [] if isinstance(i, dict)]
    adv = bool(((t.get("targeting_automation") or {}).get("advantage_audience")))
    return inc, exc, adv


@rule("2.10")
def r_audience(c):
    if c.unread("adsets"):
        return c.unread("adsets")
    sets = c.live_sets()
    if not sets:
        return F(None, "No active ad set was read.")
    lines = []
    for a in sets[:6]:
        inc, _exc, adv = _audience(a.get("targeting"))
        lines.append("%s: %s%s" % (a.get("name"), _list(inc, 8) or "no interests or audiences named", " (Advantage+ audience on: these are suggestions to Meta)" if adv else ""))
    return F(None, "Audiences: " + "; ".join(lines) + ".")


@rule("2.11")
def r_exclusions(c):
    if c.unread("adsets"):
        return c.unread("adsets")
    sets = c.live_sets()
    if not sets:
        return F(None, "No active ad set was read.")
    lines = ["%s: %s" % (a.get("name"), _list(_audience(a.get("targeting"))[1], 6) or "nothing excluded") for a in sets[:6]]
    return F(None, "Exclusions: " + "; ".join(lines) + ".")


@rule("2.12")
def r_placements(c):
    if c.unread("adsets"):
        return c.unread("adsets")
    sets = c.live_sets()
    if not sets:
        return F(None, "No active ad set was read.")
    manual = [a for a in sets if (a.get("targeting") or {}).get("publisher_platforms")]
    if not manual:
        return F("pass", "Advantage+ placements on every active ad set (no placement is chosen by hand).")
    book = _norm(c.lookup("Placements") or "")
    text = "Placements chosen by hand on: %s." % _list(["%s (%s)" % (a.get("name"), "+".join(a["targeting"]["publisher_platforms"])) for a in manual])
    if book.startswith("advantage"):
        return F("fail", "%s The campaign book says %s." % (text, c.lookup("Placements")))
    return F(None, text)


@rule("3.1")
def r_formats(c):
    if c.unread("ads"):
        return c.unread("ads")
    ads = c.live_ads()
    if not ads:
        return F(None, "No active ad was read.")
    counts = {}
    for a in ads:
        counts[a["format"]] = counts.get(a["format"], 0) + 1
    return F(None, "Formats: %s." % ", ".join("%d %s" % (n, k) for k, n in sorted(counts.items())))


def _over(ads, key, limit):
    out = []
    for a in ads:
        for t in a[key]:
            if len(t) > limit:
                out.append("%s (%d)" % (a["name"], len(t)))
                break
    return out


def _text_rule(key, label, limit):
    def fn(c):
        if c.unread("ads"):
            return c.unread("ads")
        ads = c.live_ads()
        if not ads:
            return F(None, "No active ad was read.")
        if key == "primary":
            n = sum(1 for a in ads if a["primary"])
        else:
            n = sum(1 for a in ads if a[key])
        over = _over(ads, key, limit)
        text = "%d of %d active ads have a %s." % (n, len(ads), label)
        if over:
            return F("fail", "%s Over %d characters: %s." % (text, limit, _list(over)))
        sample = [t for a in ads for t in a[key]][:3]
        return F(None, "%s None is over %d characters. First texts: %s." % (text, limit, " | ".join('"%s"' % t for t in sample) or "none"))     # spelling and grammar need the reviewer
    return fn


RULES["3.2"] = _text_rule("primary", "primary text", LIMITS["primary"])
RULES["3.3"] = _text_rule("headlines", "headline", LIMITS["headline"])
RULES["3.4"] = _text_rule("descriptions", "description", LIMITS["description"])


@rule("3.5")
def r_cta(c):
    if c.unread("ads"):
        return c.unread("ads")
    ads = c.live_ads()
    if not ads:
        return F(None, "No active ad was read.")
    none = [a["name"] for a in ads if not a["ctas"] or all(x == "NO_BUTTON" for x in a["ctas"])]
    seen = sorted({x for a in ads for x in a["ctas"]})
    if none:
        return F("fail", "No button on: %s. Buttons used: %s." % (_list(none), _list(seen) or "none"))
    return F(None, "Buttons used: %s. Whether they fit the objective is for the reviewer." % _list(seen))


@rule("3.6")
def r_urls(c):
    if c.unread("ads"):
        return c.unread("ads")
    ads = c.live_ads()
    urls = sorted({u for a in ads for u in a["urls"]})
    if not urls:
        return F(None, "No destination address was read on the active ads.")
    text = "%d address%s: %s." % (len(urls), "" if len(urls) == 1 else "es", _list(urls, 4))
    http = [u for u in urls if not u.lower().startswith("https://")]
    if http:
        return F("fail", "Not HTTPS: %s. %s" % (_list(http), text))
    pages = c.d.get("_pages") or []

    def gone(p):
        st = p.get("status") or 0
        return not p.get("error") and (st in (404, 410) or (500 <= st < 600 and st != 503) or (400 <= st < 500 and st not in (401, 403, 407, 408, 429)))
    broken = ["%s (%s)" % (p["url"], p.get("status")) for p in pages if gone(p)]
    if broken:
        return F("fail", "These pages do not load: %s. %s" % (_list(broken, 4), text))
    unsure = ["%s (%s)" % (p["url"], p.get("status") or (p.get("error") or "unreadable")[:60]) for p in pages if p.get("error") or (p.get("status") or 0) >= 400]
    if unsure:
        return F(None, "%s All HTTPS. These could not be confirmed to load, and the site may only be refusing automated visits: %s." % (text, _list(unsure, 4)))
    if pages and len(pages) >= len(urls):
        return F("pass", text + " All HTTPS and all load.")
    return F(None, text + (" All HTTPS; %d of %d were opened and loaded." % (len(pages), len(urls)) if pages else " All HTTPS. The pages could not be opened to confirm they load."))


@rule("3.7")
def r_params(c):
    if c.unread("ads"):
        return c.unread("ads")
    ads = c.live_ads()
    if not ads:
        return F(None, "No active ad was read.")

    def tags(a):
        have = {k for k, _ in parse_qsl(a["urlTags"].lstrip("?&"))}
        for u in a["urls"]:
            have |= {k for k, _ in parse_qsl(urlsplit(u).query)}
        return have
    need = {"utm_source", "utm_medium", "utm_campaign"}
    missing = [a["name"] for a in ads if not need <= tags(a)]
    if missing:
        return F("fail", "These ads do not set utm_source, utm_medium and utm_campaign (in the URL parameters or the address): %s." % _list(missing))
    return F("pass", "All %d active ads set utm_source, utm_medium and utm_campaign." % len(ads))


@rule("3.8")
def r_enhancements(c):
    if c.unread("ads"):
        return c.unread("ads")
    ads = c.live_ads()
    if not ads:
        return F(None, "No active ad was read.")
    on = sorted({e for a in ads for e in a["enhancements"]})
    text = "Creative enhancements opted into: %s." % (_list(on) if on else "none")
    book = _norm(c.lookup("Advantage+ creative enhancements") or "")
    if book.startswith("off"):
        return F("fail" if on else "pass", "%s The campaign book says off." % text)
    return F(None, text)


@rule("3.9")
def r_review(c):
    if c.unread("ads"):
        return c.unread("ads")
    ads = [a for a in c.ads if a["status"] != "PAUSED" and a["status"] not in ("CAMPAIGN_PAUSED", "ADSET_PAUSED")]
    if not ads:
        return F(None, "No ad that should be running was read.")
    bad = ["%s (%s)" % (a["name"], a["status"].lower().replace("_", " ")) for a in ads if a["status"] in ("DISAPPROVED", "WITH_ISSUES")]
    pending = [a["name"] for a in ads if a["status"] in ("PENDING_REVIEW", "IN_PROCESS")]
    if bad:
        return F("fail", "Not approved: %s." % _list(bad))
    if pending:
        return F(None, "Still in review: %s." % _list(pending))
    return F("pass", "All %d ads are active and approved." % len(ads))


def evaluate(data):
    """{check_id: {'result', 'found'}} for every rule that has something to say."""
    c = Ctx(data)
    out = {}
    for cid, fn in RULES.items():
        try:
            f = fn(c)
        except Exception as e:                              # noqa: BLE001 (one rule never sinks the rest)
            log.exception("qa meta: rule %s failed", cid)
            f = F(None, "This check could not be read automatically (%s)." % type(e).__name__)
        if f:
            out[cid] = f
    return out


def campaign_summary(data):
    camp = data.get("campaign") or {}
    kind = meta.objective_kind(camp.get("objective"))
    return {"id": str(camp.get("id") or ""), "name": camp.get("name") or "", "channel": "META_" + kind if camp.get("id") else "", "type": KIND.get(kind, ""), "dsa": False,
            "status": camp.get("effective_status") or camp.get("status") or ""}


def run(act, campaign_id, get_fn, pages_fn, *, web_fn=None, book=None):
    """Read one Meta campaign and run every rule on it."""
    data = gather(act, campaign_id, get_fn, pages_fn, web_fn=web_fn, book=book)
    if not (data.get("campaign") or {}).get("id"):
        return {"data": {}, "findings": {}, "campaign": {"id": "", "name": "", "channel": "", "type": "", "dsa": False, "status": ""}, "errors": {}}
    return {"data": data, "findings": evaluate(data), "campaign": campaign_summary(data), "errors": data.get("_errors") or {}}


# ── What the reviewer is shown ──────────────────────────────────────────────

def evidence_bundle(data):
    """The evidence for the AI reviewer, in named pieces small enough to read."""
    camp = data.get("campaign") or {}
    ev = {"campaign": {"name": camp.get("name"), "objective": camp.get("objective"), "status": camp.get("effective_status") or camp.get("status"), "specialAdCategories": camp.get("special_ad_categories"),
                       "buyingType": camp.get("buying_type"), "dailyBudget": camp.get("daily_budget"), "lifetimeBudget": camp.get("lifetime_budget"), "bidStrategy": camp.get("bid_strategy"),
                       "start": camp.get("start_time"), "stop": camp.get("stop_time")}}
    sets = []
    for a in (data.get("adsets") or [])[:20]:
        inc, exc, adv = _audience(a.get("targeting"))
        t = a.get("targeting") or {}
        sets.append({"name": a.get("name"), "status": a.get("effective_status"), "dailyBudget": a.get("daily_budget"), "bidStrategy": a.get("bid_strategy"), "optimizationGoal": a.get("optimization_goal"),
                     "conversionLocation": a.get("destination_type"), "dataset": (a.get("promoted_object") or {}).get("pixel_id"), "event": (a.get("promoted_object") or {}).get("custom_event_type"),
                     "attribution": _window(a.get("attribution_spec")), "places": _geo(t), "ages": [t.get("age_min"), t.get("age_max")], "genders": t.get("genders") or "all", "audience": inc[:20],
                     "excluded": exc[:10], "advantagePlusAudience": adv, "placements": t.get("publisher_platforms") or "Advantage+ placements", "learning": (a.get("learning_stage_info") or {}).get("status")})
    ev["adSets"] = sets
    ev["ads"] = [{"name": a["name"], "status": a["status"], "adSet": a["adset"], "format": a["format"], "primaryText": a["primary"][:5], "headline": a["headlines"][:5], "description": a["descriptions"][:5],
                  "button": a["ctas"], "address": a["urls"], "facebookPage": bool(a["page"]), "instagramAccount": bool(a["instagram"]), "urlParameters": a["urlTags"],
                  "enhancementsOn": a["enhancements"], "reviewFeedback": a["feedback"]} for a in (data.get("ads") or [])[:40]]
    ev["accountSettingsNotVisibleToTheApi"] = ["whether the dataset received events", "Conversions API", "event match quality", "domain verification and aggregated event measurement"]
    pages = []
    for p in data.get("_pages") or []:
        q = dict(p)
        q["text"] = (q.get("text") or "")[:1800]
        pages.append(q)
    ev["landingPages"] = pages
    book = data.get("_book")
    ev["campaignBook"] = ({"filename": book.get("filename"), "fields": book.get("rows"), "everythingOnItsSheets": book.get("text")} if book else "none uploaded")
    return ev
