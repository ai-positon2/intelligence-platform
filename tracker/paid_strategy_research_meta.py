"""Paid Marketing Agents: what a Meta ad account has already done, for the agents that start from a client (Business Research, Strategy & Campaign Book, Keyword Research).

The Google Ads history (tracker/paid_strategy_research.read_history) says what the account paid for and what converted. This is the same question put to a Meta ad account for the last
90 days: which campaigns (by objective) took the money and what each brought, where the ads were shown (Facebook, Instagram, Messenger, Audience Network), which devices, and what the
live ads say. The answer has the same top-level shape ({account, window, totals, campaigns, ...}) so the writing stages read it the same way. Read only.

Every number is Meta's own, taken as the page's campaign figures take it (tracker/paid_meta.py numbers): a result is what the campaign's objective counts (purchases, leads, installs).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import time

from . import paid_meta as meta
from . import paid_strategy_research as rs

log = logging.getLogger(__name__)

HISTORY_DAYS = 90
MAX_CAMPAIGNS = 40
MAX_ADS = 15
LABELS = {"facebook": "Facebook", "instagram": "Instagram", "messenger": "Messenger", "audience_network": "Audience Network", "threads": "Threads"}
DEVICES = {"mobile_app": "Mobile", "mobile_web": "Mobile", "desktop": "Desktop", "tablet": "Tablet"}

CAMPAIGN_FIELDS = "campaign_id,campaign_name,objective,spend,impressions,inline_link_clicks,actions,action_values"
SPLIT_FIELDS = "spend,impressions,inline_link_clicks,actions,action_values"
ADS_FIELDS = "id,name,effective_status,campaign{name,objective},creative{title,body,call_to_action_type,object_type}"


def window(today=None, days=HISTORY_DAYS):
    end = (today or dt.date.today()) - dt.timedelta(days=1)
    return (end - dt.timedelta(days=days - 1)).isoformat(), end.isoformat()


def _range(start, end):
    return json.dumps({"since": start, "until": end})


def requests_for(act, start, end):
    """{name: (path, params)}: the four reads of one Meta ad account."""
    ins = "/%s/insights" % act
    tr = _range(start, end)
    return {"campaigns": (ins, {"level": "campaign", "time_range": tr, "fields": CAMPAIGN_FIELDS, "limit": 500}),
            "placements": (ins, {"level": "account", "time_range": tr, "breakdowns": "publisher_platform", "fields": SPLIT_FIELDS, "limit": 100}),
            "devices": (ins, {"level": "account", "time_range": tr, "breakdowns": "device_platform", "fields": SPLIT_FIELDS, "limit": 100}),
            "ads": ("/%s/ads" % act, {"fields": ADS_FIELDS, "effective_status": json.dumps(["ACTIVE"]), "limit": 100})}


def _rate(num, den, nd=2):
    return round(num / den, nd) if den else None


def _row(r):
    """(impressions, clicks, cost, conversions, value) of one insights row, the way the pages take them."""
    n = meta.numbers(r)
    return {"impr": int(n[0]), "clicks": int(n[1]), "cost": round(n[2], 2), "conv": round(n[3], 1), "value": round(n[4], 2)}


def parse_campaigns(rows):
    out = []
    for r in rows:
        v = _row(r)
        if not (v["impr"] or v["cost"]):
            continue
        kind = meta.objective_kind(r.get("objective"))
        out.append(dict(v, name=str(r.get("campaign_name") or ""), type=meta.OBJECTIVE_LABEL.get(str(r.get("objective") or "").upper()) or kind.title(), objective=kind,
                        cpa=_rate(v["cost"], v["conv"]), cvr=_rate(v["conv"] * 100, v["clicks"], 1)))
    out.sort(key=lambda c: -c["cost"])
    return out


def parse_split(rows, key, names):
    """[{name, cost, share, impr, clicks, conv, cpa}] biggest spend first, one entry per label (several of Meta's device words are one label)."""
    by = {}
    for r in rows:
        v = _row(r)
        label = names.get(str(r.get(key) or "").lower()) or str(r.get(key) or "other").replace("_", " ").title()
        a = by.setdefault(label, {"name": label, "cost": 0.0, "impr": 0, "clicks": 0, "conv": 0.0})
        for k in ("cost", "impr", "clicks", "conv"):
            a[k] += v[k]
    total = sum(a["cost"] for a in by.values())
    out = [dict(a, cost=round(a["cost"], 2), conv=round(a["conv"], 1), share=_rate(a["cost"] * 100, total, 1), cpa=_rate(a["cost"], a["conv"])) for a in by.values() if a["cost"] or a["impr"]]
    out.sort(key=lambda a: -a["cost"])
    return out


def parse_ads(rows):
    """The live ads' own words: title, text and button, with the campaign they sit in. An ad with no words (a plain post or a video with no caption) is left out."""
    out = []
    for r in rows:
        c = r.get("creative") or {}
        title, body = str(c.get("title") or "").strip(), str(c.get("body") or "").strip()
        if not (title or body):
            continue
        out.append({"name": str(r.get("name") or ""), "campaign": str((r.get("campaign") or {}).get("name") or ""), "title": title[:120], "body": body[:300],
                    "cta": str(c.get("call_to_action_type") or "").replace("_", " ").title(), "format": str(c.get("object_type") or "").title()})
        if len(out) >= MAX_ADS:
            break
    return out


def read_meta_history(account, *, pages_fn, today=None):
    """What one Meta ad account did in the last 90 days, as a research piece. `pages_fn(path, params)` reads a Graph list. One read failing leaves the others and is said; the
    campaigns failing is the failure of the piece."""
    started = time.monotonic()
    start, end = window(today)
    notes = []
    raw = {}
    for name, (path, params) in requests_for(account["id"], start, end).items():
        try:
            raw[name] = pages_fn(path, params)
        except Exception as e:                                   # noqa: BLE001 (one read failing must not lose the rest)
            log.info("meta history: %s failed: %s", name, e)
            raw[name] = None
            notes.append("%s could not be read (%s)." % (name.capitalize(), rs._clip(e, 100)))
    if raw["campaigns"] is None:
        return rs.failed("metahistory", "The campaigns of this Meta ad account could not be read. " + " ".join(notes), time.monotonic() - started)
    campaigns = parse_campaigns(raw["campaigns"])
    tot = {"impr": sum(c["impr"] for c in campaigns), "clicks": sum(c["clicks"] for c in campaigns), "cost": round(sum(c["cost"] for c in campaigns), 2),
           "conv": round(sum(c["conv"] for c in campaigns), 1), "value": round(sum(c["value"] for c in campaigns), 2)}
    tot.update(cpa=_rate(tot["cost"], tot["conv"]), cvr=_rate(tot["conv"] * 100, tot["clicks"], 1), cpc=_rate(tot["cost"], tot["clicks"]), ctr=_rate(tot["clicks"] * 100, tot["impr"], 1))
    data = {"account": {"id": account["id"], "name": account.get("name") or "", "currency": account.get("currency") or "USD", "platform": "meta"}, "window": {"start": start, "end": end},
            "totals": tot, "campaigns": campaigns[:MAX_CAMPAIGNS], "campaignCount": len(campaigns), "placements": parse_split(raw["placements"] or [], "publisher_platform", LABELS),
            "devices": parse_split(raw["devices"] or [], "device_platform", DEVICES), "ads": parse_ads(raw["ads"] or []), "notes": notes}
    spent = "No spend in the last 90 days." if not tot["cost"] else "%d campaigns, %s spent and %s results in the last 90 days." % (len(campaigns), "{:,.0f}".format(tot["cost"]), "{:,.0f}".format(tot["conv"]))
    return rs.piece("metahistory", "partial" if notes else "ok", " ".join([spent] + notes), data, time.monotonic() - started)


def evidence(research):
    """What the writing stages are shown of it (None when it was not asked for, failed or has nothing)."""
    d = ((research or {}).get("metahistory") or {}).get("data") or {}
    if not d or not (d.get("campaigns") or d.get("totals", {}).get("cost")):
        return None
    return {k: d.get(k) for k in ("account", "window", "totals", "campaigns", "placements", "devices", "ads")}


# ── Interests and audiences ─────────────────────────────────────────────────

MAX_SEED_PHRASES = 12
MAX_AUDIENCE_CANDIDATES = 60


def audience_phrases(inputs, research):
    """What to look up in Meta's interest list, from what was typed and read (no model): the things the client sells, the headings of its pages, the competitors' names and the industry.
    At most MAX_SEED_PHRASES, short ones only (Meta's interests have short, standard names)."""
    raw = [p.strip() for p in str(inputs.get("services") or "").replace(";", ",").replace("\n", ",").split(",")]
    for pg in ((((research or {}).get("website") or {}).get("data") or {}).get("pages") or [])[:8]:
        raw += [h for h in (pg.get("h1") or [])[:1]]
    raw += [c.get("name") for c in inputs.get("competitors") or []][:3]
    raw.append(inputs.get("industry"))
    out = []
    for p in raw:
        p = " ".join(str(p or "").split())
        if p and 1 <= len(p.split()) <= 4 and len(p) <= 40 and p.lower() not in [x.lower() for x in out]:
            out.append(p)
    return out[:MAX_SEED_PHRASES]


def read_meta_audience(inputs, research, *, get_fn, account=None, pages_fn=None, today=None):
    """The interests Meta has for what the client sells, and (when there is a Meta ad account) which interests its ad sets already aim at and how each does: the evidence the Meta plan's
    ad sets are written from. Meta's own list is the only source an interest may come from. Never raises: a piece that cannot be read says so."""
    from . import paid_keywords_meta as km, paid_meta_interests as mi
    started = time.monotonic()
    phrases = audience_phrases(inputs, research)
    notes, in_use, adset_count, totals = [], [], 0, {}
    if account and pages_fn:
        pc = km.read_meta_account(account, pages_fn=pages_fn, today=today)
        d = pc.get("data") or {}
        if pc["state"] == "failed":
            notes.append(pc.get("note") or "The ad account's ad sets could not be read.")
        else:
            in_use, adset_count, totals = d.get("inUse") or [], d.get("adsetCount") or 0, d.get("totals") or {}
            notes += d.get("notes") or []
    used = {u["id"]: u for u in in_use}
    if not phrases and not in_use:
        return rs.failed("metaaudience", "There was nothing to look up in Meta's interests: no service, page heading or competitor name.", time.monotonic() - started)
    found, n1 = mi.find(phrases, get_fn)
    names = [u["name"] for u in in_use if u["kind"] == "interest"][:4]
    for p in phrases[:4]:
        top = (found.get(p) or [None])[0]
        if top:
            names.append(top["name"])
    sugg, n2 = mi.suggestions(names, get_fn)
    notes += n1 + n2
    cands = mi.candidates(found, sugg, [], used, avoid=str(inputs.get("avoid") or "").replace(";", ",").split(","))
    cands = [c for c in cands if c["inUse"]] + [c for c in cands if not c["inUse"]]
    data = {"phrases": phrases, "candidates": cands[:MAX_AUDIENCE_CANDIDATES], "found": len(cands), "inUse": in_use, "adsetCount": adset_count, "totals": totals,
            "emptyPhrases": [p for p in phrases if not found.get(p)], "notes": notes}
    if not cands and not in_use:
        return rs.failed("metaaudience", "Meta has no interest for these phrases (%s). Meta keeps none for some topics, health conditions among them." % ", ".join('"%s"' % p for p in phrases[:6]), time.monotonic() - started)
    note = "%d interests from %d phrases%s." % (len(data["candidates"]), len(phrases), (", and %d interests in use by the ad sets" % len(in_use)) if in_use else "")
    return rs.piece("metaaudience", "partial" if notes else "ok", " ".join([note] + notes), data, time.monotonic() - started)


def audience_evidence(research):
    """What the writing stages are shown of it: Meta's interests with their sizes and the ones in use (None when it was not asked for, failed or has nothing)."""
    d = ((research or {}).get("metaaudience") or {}).get("data") or {}
    if not d or not (d.get("candidates") or d.get("inUse")):
        return None
    return {"interestsMetaHas": ["%s | %s | %s%s" % (c["name"], c.get("sizeText") or "size not given", " > ".join(c.get("path") or []) or "no path",
                                                    (" | in use: %d ad sets, %s spent, %s results" % (c["inUse"]["adsets"], c["inUse"]["cost"], c["inUse"]["conv"])) if c.get("inUse") else "")
                                  for c in d.get("candidates") or []],
            "interestsInUse": [{"name": u["name"], "adSets": u["adsets"], "spent": u["cost"], "results": u["conv"], "verdict": u["verdict"]} for u in d.get("inUse") or []],
            "adSets": d.get("adsetCount"), "totals": d.get("totals")}
