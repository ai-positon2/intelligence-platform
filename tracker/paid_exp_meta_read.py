"""Paid Marketing Agents: Test & Experiment Tracker, what Meta says is being tested.

The same job as tracker/paid_exp_read.py for a Meta ad account, in the same shape so the same arithmetic (tracker/paid_exp_stats.py) and the same report can hold it.
Read only. Two kinds of test, each read on its own:

  split tests   Meta's own A/B tests (ad studies): the test, its dates, its cells (the cell is Meta's word for an arm) and what each cell's ad sets or campaigns did on
                every day of the test. Meta splits the audience between the cells at random, so two cells are a fair test, like Google's experiment arms. The first cell is the
                control and the others are trials (the report compares the first two). A test that is not an A/B test (a conversion lift or brand lift study) is listed without a result.
  ad tests      every ad set that runs two or more live ads, over the last 30 days, as Google's ad groups are. Meta moves delivery towards the ad it expects to do best, so the
                ads of one ad set do not share the audience evenly: this is what happened, not a fair test, and the report says so.

A part that could not be read is a warning and its `ok` flag is False; both failing is raised. What Meta calls the fields of an ad study could not be tried against a live account
from the development machine, so if Meta names one differently the page says so in Meta's own words (a warning, or a test "not measured" with the reason) and the ad tests still stand.
"""
from __future__ import annotations

import json

from . import paid_exp_read as rd
from . import paid_lp_meta_read as lpm
from . import paid_meta as meta
from .paid_audit_meta_read import parse_time

WINDOW = "last_30d"
STUDY_BASE = "id,name,description,type,start_time,end_time,canceled_time"
STUDY_FIELDS = STUDY_BASE + ",cells{id,name,treatment_percentage,campaigns{id,name},adsets{id,name}}"
STUDY_FIELDS_PLAIN = STUDY_BASE + ",cells{id,name,treatment_percentage}"
ADS_FIELDS = "id,name,adset_id,campaign_id,effective_status,creative{object_story_spec,asset_feed_spec,title,body,object_url,template_url,effective_object_story_id}"
ADS_FIELDS_PLAIN = "id,name,adset_id,campaign_id,effective_status"
INSIGHT_FIELDS = "ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,objective,spend,impressions,inline_link_clicks,actions,action_values"
DAY_FIELDS = "campaign_id,adset_id,objective,spend,impressions,inline_link_clicks,actions,action_values"
ENTITIES_PER_QUERY = 50
ADS_PAGE = 100
ADS_PAGE_SMALL = 25
SPLIT_TEST = "SPLIT_TEST"
TYPES = {SPLIT_TEST: "Meta A/B test", "LIFT": "Conversion lift study", "BRAND_LIFT": "Brand lift study", "CONTINUOUS_LIFT_CONFIG": "Continuous lift study",
         "GEO_LIFT": "Geo lift study"}


# ── Split tests ─────────────────────────────────────────────────────────────

def _when(v):
    """A date from Meta's "2026-10-01T09:00:00+0000" (or a plain date), or None."""
    t = parse_time(v)
    return t.date() if t else None


def _entities(cell, key):
    return [{"id": str(e.get("id")), "name": rd._norm(e.get("name")) or str(e.get("id"))} for e in ((cell.get(key) or {}).get("data") or []) if isinstance(e, dict) and e.get("id")]


def parse_studies(rows, today):
    """[{resource, id, name, description, status, state, type, typeLabel, start, end, cells: [{id, name, split, campaigns, adsets}], cellsRead}], newest first. `state` is running,
    setting up or ended (a test Meta cancelled has ended). `cellsRead` is False when the answer carried no list of cells at all (Meta did not say what the test compares)."""
    out = []
    for r in rows:
        sid = str(r.get("id") or "")
        if not sid:
            continue
        start, end = _when(r.get("start_time")), _when(r.get("end_time"))
        typ = str(r.get("type") or "")
        if r.get("canceled_time") or (end and end < today):
            state, status = "ended", "CANCELED" if r.get("canceled_time") else "ENDED"
        elif start and start > today:
            state, status = "setting up", "SCHEDULED"
        else:
            state, status = "running", "ACTIVE"
        raw = (r.get("cells") or {}).get("data") if isinstance(r.get("cells"), dict) else None
        cells = [{"id": str(c.get("id") or ""), "name": rd._norm(c.get("name")) or "Cell", "split": rd._i(c.get("treatment_percentage"), None),
                  "campaigns": _entities(c, "campaigns"), "adsets": _entities(c, "adsets")} for c in raw or [] if isinstance(c, dict) and c.get("id")]
        cells.sort(key=lambda c: (rd._i(c["id"]), c["name"]))
        out.append({"resource": "meta:" + sid, "id": "meta:" + sid, "name": rd._norm(r.get("name")) or "Test", "description": rd._norm(r.get("description")), "status": status,
                    "state": state, "type": typ, "typeLabel": TYPES.get(typ, typ.replace("_", " ").capitalize() if typ else ""), "start": rd._day(start), "end": rd._day(end),
                    "cells": cells, "cellsRead": raw is not None, "platform": "meta"})
    out.sort(key=lambda x: (x["start"] or "", x["name"]), reverse=True)
    return out


def parse_days(rows):
    """{entity id: {date: [impressions, clicks, cost, conversions, value]}}, the entity being the ad set when the row has one and the campaign when it has not."""
    out = {}
    for r in rows:
        eid = str(r.get("adset_id") or r.get("campaign_id") or "")
        day = str(r.get("date_start") or "")[:10]
        if not eid or not day:
            continue
        n = meta.numbers(r)
        out.setdefault(eid, {})[day] = [n[0], n[1], round(n[2], 2), round(n[3], 4), round(n[4], 2)]
    return out


def _entity_ids(study):
    return sorted({e["id"] for c in study["cells"] for e in c["campaigns"] + c["adsets"]})


def build_studies(studies, days, today):
    """Each test in the shape of tracker/paid_exp_read.build_experiments: {..., window, clipped, arms: [{name, control, split, campaigns, results}], measured, reason}. `reason` is why a
    test has no result when it is a split test that could have had one. The first cell is the control."""
    out = []
    for s in studies:
        win = rd._window(s, today)
        ab = s["type"] == SPLIT_TEST
        arms = []
        for i, c in enumerate(s["cells"]):
            ids = [e["id"] for e in c["campaigns"] + c["adsets"]]
            arms.append({"name": c["name"], "control": i == 0, "split": c["split"], "campaigns": [dict(e, type="campaign") for e in c["campaigns"]] + [dict(e, type="ad set") for e in c["adsets"]],
                         "results": rd.sum_arm(days, ids, win[0], win[1]) if win and ab and ids else None})
        start = rd._date(s["start"])
        reason = ""
        if ab and not s["cellsRead"]:
            reason = "Meta did not list this test's cells."
        elif ab and len(s["cells"]) < 2:
            reason = "Meta lists fewer than two cells for this test."
        elif ab and not all(c["campaigns"] or c["adsets"] for c in s["cells"][:2]):
            reason = "Meta did not say which campaigns or ad sets each cell holds."
        out.append(dict(s, window=[rd._day(win[0]), rd._day(win[1])] if win else None, clipped=bool(win and start and win[0] > start), arms=arms, reason=reason,
                        measured=bool(win) and ab and len(arms) >= 2 and all(a["results"] is not None for a in arms[:2])))
    return out


# ── Ad tests ────────────────────────────────────────────────────────────────

def parse_live_ads(rows):
    """{ad id: {id, name, adSet, campaign, type, headlines, descriptions, url}} for the ads that are on."""
    out = {}
    for r in rows:
        aid = str(r.get("id") or "")
        if not aid:
            continue
        c = r.get("creative") or {}
        urls, _on_meta = lpm.creative_urls(c)
        heads, descs = lpm.creative_text(c)
        out[aid] = {"id": aid, "name": rd._norm(r.get("name")), "adSet": str(r.get("adset_id") or ""), "campaign": str(r.get("campaign_id") or ""), "type": lpm.creative_kind(c),
                    "headlines": heads, "descriptions": descs, "url": urls[0] if urls else ""}
    return out


def build_groups(live, insights):
    """{ad set id: {id, name, campaign, campaignId, campaignType, platform, ads: [{id, name, type, headlines, descriptions, url, strength, impressions, clicks, cost, conversions, value}]}}
    for the live ads, with what each took in the last 30 days (an ad that was not shown has zeros). `insights` is lpm.parse_insights."""
    groups = {}
    for aid, ad in live.items():
        if not ad["adSet"]:
            continue
        i = insights.get(aid)
        g = groups.setdefault("meta:" + ad["adSet"], {"id": "meta:" + ad["adSet"], "name": (i or {}).get("adSetName") or ad["adSet"], "campaign": (i or {}).get("campaignName") or "",
                                                       "campaignId": ad["campaign"], "campaignType": "META_" + (i or {}).get("kind", "OTHER"), "platform": "meta", "ads": []})
        if i:                                                  # the names Meta gave when it counted the ad, which a live ad that was never shown cannot give
            g["name"], g["campaign"] = i["adSetName"] or g["name"], i["campaignName"] or g["campaign"]
            if g["campaignType"] == "META_OTHER":
                g["campaignType"] = "META_" + i["kind"]
        g["ads"].append({"id": aid, "name": ad["name"], "type": ad["type"], "headlines": ad["headlines"], "descriptions": ad["descriptions"], "url": ad["url"], "strength": "",
                         "impressions": (i or {}).get("impressions", 0), "clicks": (i or {}).get("clicks", 0), "cost": (i or {}).get("cost", 0.0),
                         "conversions": (i or {}).get("conversions", 0.0), "value": (i or {}).get("value", 0.0)})
    return groups


# ── The whole read ──────────────────────────────────────────────────────────

def _q(**kw):
    return kw


def _chunks(items, n):
    return [items[i:i + n] for i in range(0, len(items), n)]


def read_days(pages, act, studies, today):
    """{entity id: {date: [...]}} for every campaign and ad set a measurable test's cells hold, from the earliest start to the latest end (or yesterday)."""
    first = last = None
    camps, sets = set(), set()
    for s in studies:
        win = rd._window(s, today)
        if win is None or s["type"] != SPLIT_TEST:
            continue
        got = False
        for c in s["cells"]:
            camps |= {e["id"] for e in c["campaigns"]}
            sets |= {e["id"] for e in c["adsets"]}
            got = got or bool(c["campaigns"] or c["adsets"])
        if got:
            first = win[0] if first is None else min(first, win[0])
            last = win[1] if last is None else max(last, win[1])
    days = {}
    if first is None:
        return days
    window = json.dumps({"since": first.isoformat(), "until": last.isoformat()})
    for level, field, ids in (("campaign", "campaign.id", sorted(camps)), ("adset", "adset.id", sorted(sets))):
        for chunk in _chunks(ids, ENTITIES_PER_QUERY):
            rows = pages(act + "/insights", _q(level=level, fields=DAY_FIELDS, time_increment=1, time_range=window, limit=500,
                                               filtering=json.dumps([{"field": field, "operator": "IN", "value": chunk}])))
            days.update(parse_days(rows))
    return days


def read_account(act, *, pages, today=None):
    """{experiments, adGroups, adTests, ok, warnings, today}: the Meta side of the tracker, in the shape of tracker/paid_exp_read.read_account. The split tests and the ad tests are
    read on their own: one that fails is a warning, both failing raises the first error. Meta asking to wait (paid_meta.OverQuota) stops the read and is raised."""
    today = rd._today(today)
    ok, warnings, errors = {"experiments": True, "ads": True}, [], {}
    exps = []

    def studies_rows():
        try:
            return pages(act + "/ad_studies", _q(fields=STUDY_FIELDS, limit=100))
        except meta.OverQuota:
            raise
        except meta.AdsError:                                 # an answer without the cells' campaigns and ad sets still lists the tests
            return pages(act + "/ad_studies", _q(fields=STUDY_FIELDS_PLAIN, limit=100))
    try:
        studies = parse_studies(studies_rows(), today)
        days, days_ok = {}, True
        try:
            days = read_days(pages, act, studies, today)
        except meta.OverQuota:
            raise
        except meta.AdsError:
            days_ok = False
        if not days_ok:
            ok["experiments"] = False
            warnings.append("The split tests were read but not what their cells did, so none of them has a result here.")
        exps = build_studies(studies, days, today)
    except meta.OverQuota:
        raise
    except meta.AdsError as e:
        errors["experiments"] = e
        ok["experiments"] = False
        warnings.append("The split tests could not be read (%s)." % e)
    groups, tests = [], 0
    try:
        live_rows = None
        for limit, fields in ((ADS_PAGE, ADS_FIELDS), (ADS_PAGE_SMALL, ADS_FIELDS), (ADS_PAGE, ADS_FIELDS_PLAIN)):
            try:
                live_rows = pages(act + "/ads", _q(fields=fields, effective_status=json.dumps(["ACTIVE"]), limit=limit))
                break
            except meta.OverQuota:
                raise
            except meta.AdsError as e:
                last = e
        if live_rows is None:
            raise last
        stats = lpm.parse_insights(pages(act + "/insights", _q(level="ad", date_preset=WINDOW, fields=INSIGHT_FIELDS, limit=500)))
        groups, tests = rd.compact_groups(build_groups(parse_live_ads(live_rows), stats))
    except meta.OverQuota:
        raise
    except meta.AdsError as e:
        errors["ads"] = e
        ok["ads"] = False
        warnings.append("The ads could not be read (%s)." % e)
    if len(errors) == 2:
        raise errors["experiments"]
    return {"experiments": exps, "adGroups": groups, "adTests": tests, "ok": ok, "warnings": warnings, "today": today.isoformat()}
