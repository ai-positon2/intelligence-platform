"""Paid Marketing Agents: Asset Coverage Audit, what each asset actually is.

paid_assets.py judges from counts (3 sitelinks live, 4 wanted). This keeps the assets themselves so the page can show them: the
picture of an image, the text and lines of a sitelink, a snippet's values, a promotion's terms, a video's thumbnail, with where each
is linked, whether it is live, disapproved, paused or past its end date, and the impressions it earned in the last 30 days.

    Collector       filled while an account is read (one pass over the rows paid_assets already fetches); to_dict() is what is saved
    campaign_detail every asset one campaign, its Performance Max asset groups and its Display or Demand Gen ads are built from
    library         every asset of one kind (or every problem asset) across an account or an MCC's accounts, with who uses it
    load_detail     the saved detail of an account, or a fresh read of just that account when none is saved

The detail is saved per account in its own entry and held for a few minutes in memory: a read of 17,000 links is too big to ride
inside the report, which only needs the counts. Nothing here judges anything: the states come from paid_assets_rules.link_state and
the sections follow the same kinds the audit judges (so a row that says "3 live" opens onto exactly those 3).
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from urllib.parse import urlparse

from . import paid_assets_rules as rules

DETAIL_V = 1
LIBRARY_LIMIT = 400                    # assets returned for one library; `total` says how many there are
DETAIL_TTL_S = 2 * 3600                # a little longer than the report's own hour, so the two stay together
MEM_TTL_S = 300
MEM_MAX = 4
MAX_TEXT = 400
MAX_OFFERS = 8
CAMPAIGNS_NAMED = 8                    # campaigns named under one asset in a library

# Pictures are shown only from Google's own hosts: the detail view never loads an image from anywhere else.
IMAGE_HOSTS = ("googlesyndication.com", "googleusercontent.com", "ggpht.com")
STATE_RANK = {"disapproved": 4, "expired": 3, "paused": 2, "pending": 1, "live": 0}
PROBLEM_STATES = ("disapproved", "expired", "paused")

FIELD_LABELS = {
    "SITELINK": "Sitelinks", "CALLOUT": "Callouts", "STRUCTURED_SNIPPET": "Structured snippets", "CALL": "Call", "AD_IMAGE": "Images",
    "BUSINESS_NAME": "Business name", "LOGO": "Logo", "BUSINESS_LOGO": "Business logo", "LANDSCAPE_LOGO": "Landscape logo",
    "PROMOTION": "Promotions", "PRICE": "Prices", "LEAD_FORM": "Lead forms", "HEADLINE": "Headlines", "LONG_HEADLINE": "Long headlines",
    "DESCRIPTION": "Descriptions", "MARKETING_IMAGE": "Landscape images", "SQUARE_MARKETING_IMAGE": "Square images",
    "PORTRAIT_MARKETING_IMAGE": "Portrait images", "TALL_PORTRAIT_MARKETING_IMAGE": "Tall portrait images", "YOUTUBE_VIDEO": "Videos",
    "CALL_TO_ACTION_SELECTION": "Call to action", "LANDING_PAGE_PREVIEW": "Landing page preview", "CLASSIC_DISPLAY_IMAGE": "Display images",
    "SQUARE_LOGO": "Square logo", "MOBILE_APP": "Mobile app", "HOTEL_CALLOUT": "Hotel callouts", "DEMAND_GEN_CAROUSEL_CARD": "Carousel cards",
}
REASON_WORDS = {"ASSET_DISAPPROVED": "Disapproved by Google", "ASSET_LINK_PAUSED": "Paused", "ASSET_UNDER_REVIEW": "Under review",
                "ASSET_LINK_REMOVED": "Removed", "ASSET_APPROVED_LABELED": "", "ASSET_APPROVED": "", "ASSET_LINKED_TO_DISAPPROVED": "Linked to a disapproved asset"}


def _g(row, *path, default=None):
    for p in path:
        if not isinstance(row, dict):
            return default
        row = row.get(p)
    return default if row is None else row


def _i(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _t(v, n=MAX_TEXT):
    return str(v).strip()[:n] if v not in (None, "") else ""


# Which kind an asset is, when Google did not say, from the part of it that is filled in.
KIND_OF_NODE = {"imageAsset": "IMAGE", "sitelinkAsset": "SITELINK", "calloutAsset": "CALLOUT", "structuredSnippetAsset": "STRUCTURED_SNIPPET", "callAsset": "CALL",
                "promotionAsset": "PROMOTION", "priceAsset": "PRICE", "leadFormAsset": "LEAD_FORM", "textAsset": "TEXT", "youtubeVideoAsset": "YOUTUBE_VIDEO",
                "callToActionAsset": "CALL_TO_ACTION"}


def field_label(field):
    return FIELD_LABELS.get(field) or str(field or "").replace("_", " ").capitalize()


def safe_image(url):
    """The URL if it is an https picture on one of Google's own hosts, else ''."""
    try:
        u = urlparse(str(url or ""))
    except ValueError:
        return ""
    host = (u.hostname or "").lower()
    plain = u.netloc.lower() == host and not any(c.isspace() for c in str(url))                                 # no port, no user name, no spaces
    ok = u.scheme == "https" and plain and any(host == h or host.endswith("." + h) for h in IMAGE_HOSTS)
    return str(url)[:300] if ok else ""


def safe_link(url):
    """The URL if it is plain http(s), else ''."""
    try:
        u = urlparse(str(url or ""))
    except ValueError:
        return ""
    return str(url)[:500] if u.scheme in ("http", "https") and u.hostname else ""


def money(m):
    """'USD 12.5' from {currencyCode, amountMicros}; '' when there is no amount."""
    if not isinstance(m, dict) or m.get("amountMicros") in (None, ""):
        return ""
    amount = _i(m.get("amountMicros")) / 1e6
    return ("%s %s" % (m.get("currencyCode") or "", ("%.2f" % amount).rstrip("0").rstrip("."))).strip()


def _words(code):
    return str(code or "").replace("_", " ").capitalize()


def _promotion(p):
    """'10% off', 'USD 20 off, up to', 'Orders over USD 50' from a promotion asset."""
    bits = []
    if _i(p.get("percentOff")):
        bits.append("%s%% off" % ("%g" % (_i(p["percentOff"]) / 1e6)))
    if money(p.get("moneyAmountOff")):
        bits.append("%s off" % money(p["moneyAmountOff"]))
    if money(p.get("ordersOverAmount")):
        bits.append("on orders over %s" % money(p["ordersOverAmount"]))
    out = " ".join(bits)
    if str(p.get("discountModifier") or "").upper() == "UP_TO" and out:
        out = "Up to " + out
    return out


def content(asset):
    """What an asset is, as a small dict: t (type), then whichever of
        n name · x main text · d lines under it · v values · u link · w h b picture size and bytes · img picture url · vid YouTube id
        s e start and end date · o offerings · off promotion terms · code · occ occasion · cta · bn business name · cc country
    Empty values are left out. A picture url is kept only from Google's own hosts."""
    a = asset if isinstance(asset, dict) else {}
    kind = str(a.get("type") or "").upper() or next((t for node, t in KIND_OF_NODE.items() if node in a), "")
    out = {"t": kind}
    url = safe_link((a.get("finalUrls") or [""])[0] if isinstance(a.get("finalUrls"), list) and a.get("finalUrls") else "")
    if kind == "IMAGE":
        full = _g(a, "imageAsset", "fullSize", default={}) or {}
        out.update(n=_t(a.get("name"), 160), img=safe_image(full.get("url")), w=_i(full.get("widthPixels")), h=_i(full.get("heightPixels")),
                   b=_i(_g(a, "imageAsset", "fileSize")))
    elif kind == "SITELINK":
        s = a.get("sitelinkAsset") or {}
        out.update(x=_t(s.get("linkText")), d=[x for x in (_t(s.get("description1")), _t(s.get("description2"))) if x], u=url,
                   s=_t(s.get("startDate"), 10), e=_t(s.get("endDate"), 10))
    elif kind == "CALLOUT":
        out["x"] = _t(_g(a, "calloutAsset", "calloutText"))
    elif kind == "STRUCTURED_SNIPPET":
        s = a.get("structuredSnippetAsset") or {}
        out.update(x=_t(s.get("header")), v=[_t(v, 80) for v in (s.get("values") or [])][:20])
    elif kind == "CALL":
        c = a.get("callAsset") or {}
        out.update(x=_t(c.get("phoneNumber")), cc=_t(c.get("countryCode"), 4))
    elif kind == "PROMOTION":
        p = a.get("promotionAsset") or {}
        out.update(x=_t(p.get("promotionTarget")), off=_promotion(p), code=_t(p.get("promotionCode"), 40), occ=_words(p.get("occasion")) if p.get("occasion") else "",
                   s=_t(p.get("startDate"), 10), e=_t(p.get("endDate"), 10), u=url)
    elif kind == "PRICE":
        p = a.get("priceAsset") or {}
        offers = [{"h": _t(o.get("header"), 60), "d": _t(o.get("description"), 120), "p": money(o.get("price")), "u": safe_link(o.get("finalUrl"))}
                  for o in (p.get("priceOfferings") or []) if isinstance(o, dict)][:MAX_OFFERS]
        out.update(x=_words(p.get("type")), o=offers)
    elif kind == "LEAD_FORM":
        f = a.get("leadFormAsset") or {}
        out.update(x=_t(f.get("headline")), d=[x for x in (_t(f.get("description")),) if x], bn=_t(f.get("businessName"), 80), cta=_words(f.get("callToActionType")) if f.get("callToActionType") else "", u=url)
    elif kind == "TEXT":
        out["x"] = _t(_g(a, "textAsset", "text"))
    elif kind == "YOUTUBE_VIDEO":
        v = a.get("youtubeVideoAsset") or {}
        out.update(x=_t(v.get("youtubeVideoTitle"), 160), vid=_t(v.get("youtubeVideoId"), 20))
    elif kind == "CALL_TO_ACTION":
        out["cta"] = _words(_g(a, "callToActionAsset", "callToAction"))
    else:
        out["n"] = _t(a.get("name"), 160)
    out = {k: v for k, v in out.items() if v not in ("", None, 0, [])}
    out["t"] = kind
    return out


# ── Filled while an account is read ─────────────────────────────────────────

class Collector:
    """What a read of one account saw of each asset: a catalog of asset contents (each stored once) and the links to them."""

    def __init__(self, member, today):
        self.member, self.today = member, today
        self.assets, self.campaigns, self.c, self.u, self.g, self.ads, self.sets = {}, {}, {}, [], {}, [], []

    def asset(self, row):
        aid = str(_g(row, "asset", "id", default="") or "")
        if aid and aid not in self.assets:
            self.assets[aid] = content(row.get("asset"))
        return aid

    def _link(self, link, node, row, shown=None, scope=None):
        aid = self.asset(row)
        if not aid or not link.get("f"):
            return None
        out = {"a": aid, "f": link["f"], "s": rules.link_state(link, self.today)}
        if out["s"] != "live":
            reasons = [r for r in link.get("rs") or [] if REASON_WORDS.get(r, r)]               # the quiet ones (approved with a label) say nothing
            if reasons:
                out["r"] = reasons[:4]
        if link.get("e"):
            out["e"] = link["e"]
        if str(node.get("source") or "").upper() == "AUTOMATICALLY_CREATED":
            out["auto"] = 1
        seen = shown.get((scope, aid)) if isinstance(shown, dict) else None
        if isinstance(seen, dict):
            if seen.get("i"):
                out["i"] = seen["i"]
            if seen.get("k"):
                out["k"] = seen["k"]
        return out

    def campaign_link(self, cid, link, node, row, shown):
        l = self._link(link, node, row, shown, cid)
        if l:
            self.c.setdefault(cid, []).append(l)

    def customer_link(self, link, node, row):
        l = self._link(link, node, row)
        if l:
            self.u.append(l)

    def group_link(self, gid, link, node, row):
        l = self._link(link, node, row)
        if l and gid in self.g:
            self.g[gid]["l"].append(l)

    def group(self, gid, name, cid):
        self.g.setdefault(gid, {"n": _t(name, 120), "c": cid, "l": []})

    def ad(self, ad_id, cid, ad_group, ad_type, fields):
        self.ads.append({"id": ad_id, "c": cid, "g": _t(ad_group, 120), "t": ad_type, "f": fields})

    def set_(self, name, set_type, cid=None):
        self.sets.append({"n": _t(name, 120), "t": set_type, "c": cid or ""})

    def to_dict(self):
        return {"v": DETAIL_V, "id": self.member["id"], "name": self.member["name"], "day": self.today.isoformat(), "assets": self.assets,
                "campaigns": self.campaigns, "c": self.c, "u": self.u, "g": self.g, "ads": self.ads, "sets": self.sets}


# ── The saved detail of an account ──────────────────────────────────────────

_mem, _mem_lock = {}, threading.Lock()


def mem_get(account_id, now=None):
    with _mem_lock:
        hit = _mem.get(account_id)
        return hit[1] if hit and (now or time.time()) - hit[0] < MEM_TTL_S else None


def mem_put(account_id, detail, now=None):
    with _mem_lock:
        _mem[account_id] = (now or time.time(), detail)
        while len(_mem) > MEM_MAX:
            _mem.pop(min(_mem, key=lambda k: _mem[k][0]))


def mem_clear():
    with _mem_lock:
        _mem.clear()


def store_key(account_id):
    from . import paid_gads as base
    return base.CACHE_V + "assetdetail:" + str(account_id)


def save_detail(store, detail):
    """Keep a read's detail: in memory, and in the store (a failed save never costs the report)."""
    mem_put(detail["id"], detail)
    try:
        store.put_gads_cache(store_key(detail["id"]), detail)
    except Exception:                                               # noqa: BLE001
        pass


_locks, _locks_guard = {}, threading.Lock()


def _lock_for(account_id):
    with _locks_guard:
        return _locks.setdefault(account_id, threading.Lock())


def load_detail(member, *, read, store, today=None):
    """The detail of one account: from memory, from the store, or else read now. `read(member, sink)` reads the account into a
    Collector. When Google cannot answer for quota the last saved copy of any age is served. Two people opening the same account at
    once share one read."""
    from . import paid_gads_segments as gads
    hit = mem_get(member["id"])
    if hit:
        return hit
    with _lock_for(member["id"]):
        hit = mem_get(member["id"])                                  # the one who held the lock before us may have just read it
        if hit:
            return hit
        key = store_key(member["id"])
        try:
            saved = store.get_gads_cache(key, DETAIL_TTL_S)
        except Exception:                                           # noqa: BLE001
            saved = None
        if saved and saved.get("v") == DETAIL_V:
            mem_put(member["id"], saved)
            return saved
        sink = Collector(member, today or dt.date.today())
        try:
            read(member, sink)
        except gads.GadsError as err:
            old = None
            if err.kind == "quota":
                try:
                    got = store.get_gads_cache_any(key)
                    old = got[0] if got else None
                except Exception:                                   # noqa: BLE001
                    old = None
            if old and old.get("v") == DETAIL_V:
                return old
            raise
        detail = sink.to_dict()
        save_detail(store, detail)
        return detail


# ── Showing assets ──────────────────────────────────────────────────────────

def _why(link):
    out = []
    for r in link.get("r") or []:
        w = REASON_WORDS.get(r, _words(r))
        if w and w not in out:
            out.append(w)
    if link.get("s") == "expired" and link.get("e"):
        out.append("Ended %s but still switched on" % link["e"])
    if link.get("s") == "disapproved" and not out:
        out.append("Disapproved by Google")
    if link.get("s") == "paused" and not out:
        out.append("Paused")
    if link.get("s") == "pending" and not out:
        out.append("Under review")
    return out


def tile(detail, link, level):
    """One asset as the page draws it: its contents, its state and why, where it is linked from and how it did."""
    c = dict(detail["assets"].get(link["a"]) or {"t": ""})
    c.update(id=link["a"], st=link["s"], lv=level)
    why = _why(link)
    if why:
        c["why"] = why
    for k in ("i", "k"):
        if link.get(k):
            c[k] = link[k]
    if link.get("auto"):
        c["auto"] = 1
    if link.get("e") and not c.get("e"):
        c["e"] = link["e"]
    return c


def _tiles(detail, links, level):
    out, seen = [], set()
    for l in sorted(links, key=lambda x: (STATE_RANK.get(x["s"], 0) > 1, -(x.get("i") or 0))):     # live and under review first
        if (l["a"], l["f"]) in seen:
            continue
        seen.add((l["a"], l["f"]))
        out.append(tile(detail, l, level))
    return out


def _count(links):
    return sum(1 for l in links if l["s"] in ("live", "pending"))


def _section(key, label, level, links, detail):
    return {"key": key, "label": label, "level": level, "live": _count(links), "tiles": _tiles(detail, links, level)}


def _by_field(links):
    out = {}
    for l in links:
        out.setdefault(l["f"], []).append(l)
    return out


def _of(by, fields):
    return [l for f in fields for l in by.get(f, [])]


def _spec_sections(order, specs, own_by, inherit=None, inherit_level="account"):
    """Sections for the kinds a spec judges: the object's own links, or the inherited ones when it has no live link of its own.
    `inherit(key, fields)` returns (links, level) to fall back on."""
    out, used = [], set()
    for key in order:
        spec = specs[key]
        fields = spec.get("fields") or (key,)
        used.update(fields)
        own = _of(own_by, fields)
        links, level = own, None
        if inherit and not _count(own):
            more, lv = inherit(key, fields)
            if _count(more):
                links, level = own + more, lv
        out.append((key, spec["label"], level, links, own))
    return out, used


def campaign_detail(detail, campaign_id):
    """Every asset one campaign carries, in the same kinds the audit judges: {sections, groups, ads}. A kind with no live asset of its
    own shows the account's (level 'account'), as the audit counts them."""
    camp = (detail.get("campaigns") or {}).get(campaign_id) or {}
    ctype = camp.get("type") or ""
    own_by, acct_by = _by_field(detail["c"].get(campaign_id, [])), _by_field(detail["u"])
    sections, groups, ads = [], [], []

    def place(key, label, level, links, own):
        # level for the section; a tile from the account carries "account", the campaign's own "campaign"
        tiles = _tiles(detail, own, "campaign") + _tiles(detail, [l for l in links if l not in own], level or "account")
        return {"key": key, "label": label, "level": level or "campaign", "live": _count(links), "tiles": tiles}

    if ctype in ("SEARCH", "PERFORMANCE_MAX"):
        order = tuple(k for k in rules.CAMPAIGN_ASSET_ORDER if ctype in rules.CAMPAIGN_ASSETS[k].get("channels", ("SEARCH", "PERFORMANCE_MAX")))
        rows, used = _spec_sections(order, rules.CAMPAIGN_ASSETS, own_by, lambda key, fields: (_of(acct_by, fields), "account"))
        sections = [place(*r) for r in rows]
        extra = [f for f in own_by if f not in used]
        sections += [_section(f, field_label(f), "campaign", own_by[f], detail) for f in sorted(extra)]
        sets = [s for s in detail.get("sets") or [] if s.get("c") == campaign_id] or [s for s in detail.get("sets") or [] if not s.get("c")]
        if sets:
            sections.append({"key": "LOCATION", "label": "Location", "level": "campaign" if any(s.get("c") for s in sets) else "account", "live": len(sets),
                             "tiles": [{"t": "SET", "x": s.get("n") or "Location set", "n": _words(s.get("t")), "st": "live", "lv": "campaign" if s.get("c") else "account"} for s in sets]})
    if ctype == "PERFORMANCE_MAX":
        for gid, g in detail["g"].items():
            if g.get("c") != campaign_id:
                continue
            gby = _by_field(g["l"])

            def from_campaign(key, fields, _gby=gby):
                src = rules.PMAX_FROM_CAMPAIGN.get(key)
                if not src:
                    return [], None
                own = _of(own_by, src)
                if _count(own):
                    return own, "campaign"
                return (_of(acct_by, ("BUSINESS_LOGO",)) if key == "LOGO" else []), "account"
            rows, used = _spec_sections(rules.PMAX_GROUP_ORDER, rules.PMAX_GROUP, gby, from_campaign)
            secs = []
            for key, label, level, links, own in rows:
                tiles = _tiles(detail, own, "asset group") + _tiles(detail, [l for l in links if l not in own], level or "campaign")
                secs.append({"key": key, "label": label, "level": level or "asset group", "live": _count(links), "tiles": tiles})
            secs += [_section(f, field_label(f), "asset group", gby[f], detail) for f in sorted(f for f in gby if f not in used)]
            groups.append({"id": gid, "name": g.get("n") or gid, "sections": secs})
    if ctype in ("DISPLAY", "DEMAND_GEN"):
        for ad in detail.get("ads") or []:
            if ad.get("c") != campaign_id or ad["t"] not in rules.AD_SPECS:
                continue
            specs, order, label = rules.AD_SPECS[ad["t"]]
            secs = []
            for key in order:
                spec = specs[key]
                tiles = []
                for f in spec.get("fields") or (key,):
                    for item in ad["f"].get(f) or []:
                        if item.get("a"):
                            c = dict(detail["assets"].get(item["a"]) or {"t": ""})
                            c.update(id=item["a"], st="live", lv="ad")
                            tiles.append(c)
                        elif item.get("x"):
                            tiles.append({"t": "TEXT", "x": item["x"], "st": "live", "lv": "ad"})
                secs.append({"key": key, "label": spec["label"], "level": "ad", "live": len(tiles), "tiles": tiles})
            ads.append({"id": ad["id"], "adGroup": ad.get("g") or "", "typeLabel": label, "sections": secs})
    return {"campaign": camp.get("name") or "", "type": ctype, "sections": sections, "groups": groups, "ads": ads}


# ── Every asset of one kind ─────────────────────────────────────────────────

# What makes two assets "the same" for the page: the same words, picture or link, in the same state. Google keeps one asset per
# upload, so one sitelink copied into 40 campaigns is 40 assets; the page shows it once, with every campaign that uses a copy.
_SAME_KEYS = ("t", "st", "x", "d", "v", "u", "img", "vid", "n", "off", "code", "s", "e", "bn", "cc", "cta", "o", "account")


def _same_together(rows):
    import json
    out, seen = [], {}
    for r in rows:
        sig = json.dumps([r.get(k) for k in _SAME_KEYS], sort_keys=True)
        if sig not in seen:
            r["copies"] = 1
            seen[sig] = r
            out.append(r)
            continue
        m = seen[sig]
        m["copies"] += 1
        m["_camps"].update(r["_camps"])
        m["_groups"] |= r["_groups"]
        for k in ("i", "k"):
            if r.get(k):
                m[k] = m.get(k, 0) + r[k]
        for k in ("acct", "auto"):
            if r.get(k):
                m[k] = 1
        m["fl"] += [f for f in r["fl"] if f not in m["fl"]]
    for r in out:
        camps, groups = r.pop("_camps"), r.pop("_groups")
        r["nc"], r["ng"] = len(camps), len(groups)
        r["camps"] = [{"key": k, "name": n} for k, n in list(camps.items())[:CAMPAIGNS_NAMED]]
        if r["copies"] == 1:
            del r["copies"]
    return out


def kind_fields(kind):
    """The Google Ads field types that make up one kind of campaign asset (None for every kind). Raises KeyError for an unknown kind."""
    if not kind:
        return None
    if kind == "LOCATION":
        return ()
    spec = rules.CAMPAIGN_ASSETS[kind]
    return tuple(spec.get("fields") or (kind,))


def library(details, kind=None, state="all", limit=LIBRARY_LIMIT, level=None):
    """Every asset of one kind (or of every kind) linked anywhere in these accounts: its contents, its worst state, how many campaigns,
    asset groups and the account use it, and its last-30-day numbers where Google reports them. Identical assets (the same sitelink
    copied into many campaigns) are one entry with `copies` and every campaign that uses one.
    `state`: all | live (live or under review) | problem (disapproved, past end date or paused) | disapproved | expired | paused | pending.
    `level="account"` keeps only what is linked to the whole account."""
    fields = kind_fields(kind)
    agg = {}
    for d in details:
        def add(link, level, cid=None, gid=None, _d=d):
            if fields is not None and link["f"] not in fields:
                return
            a = agg.setdefault((_d["id"], link["a"]), {"detail": _d, "links": [], "camps": {}, "groups": set(), "account": False, "fields": []})
            a["links"].append(link)
            if cid:
                a["camps"].setdefault(cid, (_d.get("campaigns") or {}).get(cid, {}).get("name") or cid)
            if gid:
                a["groups"].add(gid)
            if level == "account":
                a["account"] = True
            if link["f"] not in a["fields"]:
                a["fields"].append(link["f"])
        for l in d["u"]:
            add(l, "account")
        if level == "account":
            continue
        for cid, links in d["c"].items():
            for l in links:
                add(l, "campaign", cid=cid)
        for gid, g in d["g"].items():
            for l in g["l"]:
                add(l, "asset group", cid=g.get("c"), gid=gid)
    rows = []
    for (aid_acct, aid), a in agg.items():
        worst = max(a["links"], key=lambda l: STATE_RANK.get(l["s"], 0))
        c = dict(a["detail"]["assets"].get(aid) or {"t": ""})
        c.update(id=aid, st=worst["s"], fl=[field_label(f) for f in a["fields"]])
        why = _why(worst)
        if why:
            c["why"] = why
        imps, clicks = sum(l.get("i") or 0 for l in a["links"]), sum(l.get("k") or 0 for l in a["links"])
        if imps:
            c["i"] = imps
        if clicks:
            c["k"] = clicks
        if a["account"]:
            c["acct"] = 1
        if len(details) > 1:
            c["account"] = a["detail"]["name"]
        if any(l.get("auto") for l in a["links"]):
            c["auto"] = 1
        c["_camps"] = {"%s:%s" % (a["detail"]["id"], cid): name for cid, name in a["camps"].items()}
        c["_groups"] = {"%s:%s" % (a["detail"]["id"], gid) for gid in a["groups"]}
        rows.append(c)
    rows = _same_together(rows)
    if kind == "LOCATION":                                  # location sets are not asset links: they are listed from the sets themselves
        rows = []
        for d in details:
            for s in d.get("sets") or []:
                if level == "account" and s.get("c"):
                    continue
                rows.append({"t": "SET", "x": s.get("n") or "Location set", "n": _words(s.get("t")), "st": "live", "id": "", "nc": 1 if s.get("c") else 0,
                             "acct": 0 if s.get("c") else 1, "fl": ["Location"], **({"account": d["name"]} if len(details) > 1 else {})})
    counts = {"all": len(rows), "live": 0, "problem": 0, "disapproved": 0, "expired": 0, "paused": 0, "pending": 0}
    for r in rows:
        counts["problem" if r["st"] in PROBLEM_STATES else "live"] += 1          # under review counts as live, as the audit does
        if r["st"] != "live":
            counts[r["st"]] += 1
    if state == "live":
        rows = [r for r in rows if r["st"] in ("live", "pending")]
    elif state == "problem":
        rows = [r for r in rows if r["st"] in PROBLEM_STATES]
    elif state in ("disapproved", "expired", "paused", "pending"):
        rows = [r for r in rows if r["st"] == state]
    rows.sort(key=lambda r: (-STATE_RANK.get(r["st"], 0) if state in ("problem", "disapproved", "expired", "paused") else 0, -(r.get("i") or 0),
                             str(r.get("x") or r.get("n") or "").lower(), r.get("id") or ""))
    return {"kind": kind or "", "state": state, "total": len(rows), "assetCount": sum(r.get("copies", 1) for r in rows), "counts": counts, "assets": rows[:limit]}
