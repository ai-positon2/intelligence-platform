"""Paid Marketing Agents: QA Agent, the AI reviewer.

The QC is done by the AI, not by a person ticking boxes. For one live campaign this
takes the evidence gathered by tracker/paid_qa_auto.py (Google Ads), tracker/paid_qa_web.py
(the landing pages) and the campaign book the team uploaded (tracker/paid_qa_book.py),
and has Claude go through every Campaign Book check, section by section. For each check
it says:

    pass            what it saw, and the evidence it rests on
    flag            what is wrong, the evidence, and the fix
    cannot_verify   what is missing (the campaign book, a switch the API does not show,
                    something only the account screen has): a person has to look

The rules it is held to, and that this module enforces rather than hopes for:

  * It sees only the evidence. Anything not in it is cannot_verify, never a guessed pass.
  * A pass or flag must quote the evidence, and the quote must really be in the evidence
    it was shown: an answer that cannot be tied to it is turned into cannot_verify.
  * Exact rules beat the AI. Where Google Ads gives an exact answer (AI Max is on, the
    Display Network is on) the rule's result stands; the AI cannot overturn it.
  * A model that is down, refuses or returns rubbish never produces a pass: those checks
    stay cannot_verify and the page says why.

No database here: `review` takes the evidence and returns findings.
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from . import paid_qa

log = logging.getLogger(__name__)

MODEL = "claude-sonnet-5-5"
MAX_QUOTE = 400
MIN_QUOTE = 6
GROUNDED = 0.6
UNAVAILABLE = "The AI review of this check could not be completed"

SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "verdict": {"type": "string", "enum": ["pass", "flag", "cannot_verify"]},
            "finding": {"type": "string"},
            "evidence": {"type": "string"},
            "fix": {"type": "string"},
        },
        "required": ["id", "verdict", "finding", "evidence", "fix"],
        "additionalProperties": False}}},
    "required": ["results"],
    "additionalProperties": False,
}

SYSTEM = """You are a senior Google Ads specialist doing the quality check of ONE campaign for a PPC agency, against the agency's Campaign Book. You are given the Campaign Book checks for one section (each with the expected value and anything else to look at), the evidence read from Google Ads and from the campaign's landing pages, the findings of exact rules that already ran, and the client's campaign book if the team uploaded one. Decide each check yourself; nobody else is going to tick these boxes.

For every check answer with:
- verdict: "pass" if the evidence shows the campaign matches the expected value; "flag" if the evidence shows it does not or shows a real problem; "cannot_verify" if the evidence cannot answer it.
- finding: one or two plain sentences saying what you saw, with the actual values (names, numbers, settings), next to what was expected. For a flag, say what is wrong.
- evidence: a short quote or value copied exactly from the evidence that your verdict rests on. Required for pass and flag. Empty for cannot_verify.
- fix: for a flag, the specific change to make and where (campaign, ad group, setting). Empty otherwise.

Rules you must follow:
1. Use only the evidence given. If it is not in the evidence, say cannot_verify and name what is missing in the finding. Never guess a pass, and never flag something just because you cannot see it.
2. Some checks take their expected value from the campaign book (fromBook is true: bid strategy, devices, genders, household income, locations, age, ad schedule, campaign type). Compare the live campaign with the matching row of campaignBook. If there is no campaign book, or it does not say, answer cannot_verify and say the campaign book is needed; do not guess the plan. A check that has a fixed expected value does not need the book.
2b. The campaign book is a Search campaign book. When campaignBook says it is not used for this campaign type (Performance Max, Demand Gen and the like), the checks that would have used it carry judgeOnTheSpot instead of fromBook. Follow judgeOnTheSpot: judge the setting on its own merits from the evidence and the landing page, flag what is clearly wrong, and answer cannot_verify only when nothing in the evidence can settle it. Never say the campaign book is needed for such a check.
2c. Section 6 checks (audience signals, search themes, audiences) are done on the spot: read what is set against the landing page text and the campaign name. Flag a signal, theme or audience that does not fit what the page sells, names a competitor, is a duplicate, or is off-topic; pass when they fit. Quote the signal or theme you rely on.
3. Switches that Google Ads does not show to the API (the account's automatically created asset switches, auto migration) cannot be verified: answer cannot_verify and say the switch is not visible, unless an automatically created asset in the evidence proves it is on, which is a flag.
4. Findings from exact rules (ruleFinding) are facts read straight from Google Ads. Use them as evidence. Where a rule has already ticked or crossed a check you will not be asked about it.
5. For ad copy and other assets, judge the text you were given: spelling, grammar, Start Case where the agency uses it, brand name present and spelled right, claims that cannot be proven, and the minimum numbers Google recommends (about 15 headlines and 4 descriptions per responsive search ad; at least 4 sitelinks and 4 callouts; at least 1 structured snippet). Where a check asks about the account's gender focus, you may use the landing page text to say who the business serves.
5b. A list in the evidence that ends with {"more": n} was cut short: n items were not shown to you. Judge what you can see, say that n more were not shown, and never pass a check about every item of that list from part of it.
6. Be strict on what the check says and fair on style. Flag real problems, not preferences.
7. Answer every check id you were given, exactly once, using the id as given. Do not use em dashes."""


# ── The evidence ────────────────────────────────────────────────────────────

def _rows(data, name):
    return data.get(name) or []


def _cap(items, n):
    items = list(items)
    return items[:n] + ([{"more": len(items) - n}] if len(items) > n else [])


def _demo(rows, key, field):
    """[{'value', 'excluded'}] for one demographic from criterion rows."""
    out = []
    for r in rows:
        cc = r.get(key) or {}
        if cc.get("type") == field:
            sub = {"AGE_RANGE": "ageRange", "GENDER": "gender", "INCOME_RANGE": "incomeRange", "PARENTAL_STATUS": "parentalStatus"}[field]
            out.append({"value": (cc.get(sub) or {}).get("type"), "excluded": bool(cc.get("negative"))})
    return out


def evidence_bundle(data, ctx):
    """The evidence, in named pieces small enough to read. `ctx` is paid_qa_auto.Ctx."""
    from . import paid_qa_auto as auto
    camp, cust = ctx.camp, ctx.customer
    geo = data.get("_geo") or {}
    lang = data.get("_lang") or {}
    ev = {}
    ev["account"] = {
        "name": cust.get("descriptiveName"), "currency": cust.get("currencyCode"), "timeZone": cust.get("timeZone"),
        "autoTagging": cust.get("autoTaggingEnabled"), "trackingTemplate": cust.get("trackingUrlTemplate"),
        "finalUrlSuffix": cust.get("finalUrlSuffix"), "euPoliticalAds": cust.get("containsEuPoliticalAdvertising"),
    }
    ev["campaign"] = {
        "name": camp.get("name"), "type": auto.type_label(ctx.channel) or ctx.channel, "subType": camp.get("advertisingChannelSubType"),
        "status": camp.get("status"), "servingStatus": camp.get("servingStatus"),
        "start": camp.get("startDateTime"), "end": camp.get("endDateTime"),
        "biddingStrategy": camp.get("biddingStrategyType"),
        "targetCpa": (camp.get("targetCpa") or camp.get("maximizeConversions") or {}).get("targetCpaMicros"),
        "targetRoas": (camp.get("targetRoas") or camp.get("maximizeConversionValue") or {}).get("targetRoas"),
        "dailyBudget": auto._money(ctx.budget.get("amountMicros"), ctx.currency),
        "networks": camp.get("networkSettings"), "locationOptions": camp.get("geoTargetTypeSetting"),
        "assetAutomation": camp.get("assetAutomationSettings"), "aiMax": camp.get("aiMaxSetting"),
        "trackingTemplate": camp.get("trackingUrlTemplate"), "finalUrlSuffix": camp.get("finalUrlSuffix"),
        "customParameters": camp.get("urlCustomParameters"),
    }
    inc, exc, places, sched, prox, negs, auds = [], [], [], [], [], [], []
    crit = _rows(data, "criteria")
    for r in crit:
        cc = r.get("campaignCriterion") or {}
        t = cc.get("type")
        if t == "LOCATION":
            g = (cc.get("location") or {}).get("geoTargetConstant") or ""
            nm = geo.get(g) or {}
            (exc if cc.get("negative") else inc).append(nm.get("canonical") or nm.get("name") or g)
        elif t == "PROXIMITY":
            p = cc.get("proximity") or {}
            prox.append("%s %s" % (p.get("radius"), p.get("radiusUnits")))
        elif t == "AD_SCHEDULE":
            a = cc.get("adSchedule") or {}
            sched.append("%s %s-%s%s" % (a.get("dayOfWeek"), a.get("startHour", 0), a.get("endHour", 24),
                                          " (bid %s)" % cc["bidModifier"] if cc.get("bidModifier") else ""))
        elif t in ("USER_LIST", "USER_INTEREST", "CUSTOM_AUDIENCE", "COMBINED_AUDIENCE"):
            auds.append({"level": "campaign", "type": t, "excluded": bool(cc.get("negative"))})
        elif t == "PLACEMENT":
            places.append({"url": (cc.get("placement") or {}).get("url"), "excluded": bool(cc.get("negative"))})
        elif t == "KEYWORD" and cc.get("negative"):
            k = cc.get("keyword") or {}
            negs.append("%s [%s]" % (k.get("text"), (k.get("matchType") or "").lower()))
    agc = _rows(data, "ag_criteria")
    for r in agc:
        cc = r.get("adGroupCriterion") or {}
        if cc.get("type") in ("USER_LIST", "USER_INTEREST", "CUSTOM_AUDIENCE", "COMBINED_AUDIENCE"):
            auds.append({"level": "ad group " + (r.get("adGroup") or {}).get("name", "?"), "type": cc.get("type"), "excluded": bool(cc.get("negative"))})
    by_group = {}
    for r in agc:
        cc = r.get("adGroupCriterion") or {}
        if cc.get("type") in ("AGE_RANGE", "GENDER", "INCOME_RANGE", "PARENTAL_STATUS"):
            g = by_group.setdefault((r.get("adGroup") or {}).get("name") or "?", [])
            sub = {"AGE_RANGE": "ageRange", "GENDER": "gender", "INCOME_RANGE": "incomeRange", "PARENTAL_STATUS": "parentalStatus"}[cc["type"]]
            g.append({"kind": cc["type"], "value": (cc.get(sub) or {}).get("type"), "excluded": bool(cc.get("negative"))})
    devices = []
    for r in crit:
        cc = r.get("campaignCriterion") or {}
        if cc.get("type") == "DEVICE":
            devices.append({"device": (cc.get("device") or {}).get("type"), "bidModifier": cc.get("bidModifier", 1), "note": "0 means excluded"})
    ev["targeting"] = {
        "locationsTargeted": _cap(inc, 40), "locationsExcluded": _cap(exc, 20), "radius": prox,
        "languages": [lang.get(re.sub(r"\D", "", ((r["campaignCriterion"].get("language") or {}).get("languageConstant")) or "")) or "?"
                      for r in crit if (r.get("campaignCriterion") or {}).get("type") == "LANGUAGE"] or "all languages",
        "adSchedule": sched or "none (all hours)", "devices": devices,
        "campaignLevelAges": _demo(crit, "campaignCriterion", "AGE_RANGE"), "campaignLevelGenders": _demo(crit, "campaignCriterion", "GENDER"),
        "campaignLevelIncome": _demo(crit, "campaignCriterion", "INCOME_RANGE"),
        "adGroupDemographics": by_group, "audiences": auds, "placements": _cap(places, 20),
    }
    ev["negatives"] = {
        "campaignNegativeKeywords": _cap(negs, 60),
        "listsAttachedToCampaign": [{"name": (r.get("sharedSet") or {}).get("name"), "type": (r.get("sharedSet") or {}).get("type"),
                                     "terms": (r.get("sharedSet") or {}).get("memberCount"), "status": (r.get("campaignSharedSet") or {}).get("status")}
                                    for r in _rows(data, "shared")],
        "allNegativeListsInAccount": [{"name": (r.get("sharedSet") or {}).get("name"), "type": (r.get("sharedSet") or {}).get("type"),
                                       "terms": (r.get("sharedSet") or {}).get("memberCount")} for r in _rows(data, "shared_all")],
    }

    def goals(rows, key):
        return sorted("%s/%s %s" % (r[key].get("category"), r[key].get("origin"), "optimised" if r[key].get("biddable") else "not optimised")
                      for r in rows if r.get(key))
    ev["conversionGoals"] = {"campaign": goals(_rows(data, "goals"), "campaignConversionGoal"),
                             "accountDefaults": goals(_rows(data, "cust_goals"), "customerConversionGoal")}
    groups = {}
    for r in _rows(data, "adgroups"):
        g = r.get("adGroup") or {}
        groups[str(g.get("id"))] = {"name": g.get("name"), "type": g.get("type"), "status": g.get("status"), "adRotation": g.get("adRotationMode"), "ads": []}
    for r in _rows(data, "ads"):
        ad = r.get("adGroupAd") or {}
        g = groups.get(str((r.get("adGroup") or {}).get("id")))
        if g is None:
            continue
        rsa = (ad.get("ad") or {}).get("responsiveSearchAd") or {}
        dg = (ad.get("ad") or {}).get("demandGenMultiAssetAd") or (ad.get("ad") or {}).get("demandGenVideoResponsiveAd") or {}
        car = (ad.get("ad") or {}).get("demandGenCarouselAd") or {}
        heads = [h.get("text") for h in (rsa.get("headlines") or dg.get("headlines") or [])] + [h.get("text") for h in dg.get("longHeadlines") or []]
        descs = [h.get("text") for h in (rsa.get("descriptions") or dg.get("descriptions") or [])]
        if car:
            heads, descs = heads + [car.get("headline")], descs + [car.get("description")]
        g["ads"].append({"status": ad.get("status"), "strength": ad.get("adStrength"), "type": (ad.get("ad") or {}).get("type"),
                         "approval": (ad.get("policySummary") or {}).get("approvalStatus"), "finalUrls": (ad.get("ad") or {}).get("finalUrls"),
                         "headlines": [h for h in heads if h], "descriptions": [d for d in descs if d],
                         "path1": rsa.get("path1"), "path2": rsa.get("path2")})
    ev["adGroups"] = _cap(groups.values(), 25)          # a list cut short says how many it left out, so the model never judges "all" from some

    def assets(rows, key):
        out = []
        for r in rows:
            a, link = r.get("asset") or {}, r.get(key) or {}
            if link.get("status") == "REMOVED":
                continue
            item = {"field": link.get("fieldType"), "status": link.get("status"), "source": link.get("source"), "type": a.get("type")}
            if a.get("sitelinkAsset"):
                sl = a["sitelinkAsset"]
                item.update(text=sl.get("linkText"), d1=sl.get("description1"), d2=sl.get("description2"), urls=a.get("finalUrls"))
            elif a.get("calloutAsset"):
                item["text"] = a["calloutAsset"].get("calloutText")
            elif a.get("structuredSnippetAsset"):
                item.update(header=a["structuredSnippetAsset"].get("header"), values=a["structuredSnippetAsset"].get("values"))
            elif a.get("callAsset"):
                item["phone"] = a["callAsset"].get("phoneNumber")
            elif a.get("textAsset"):
                item["text"] = a["textAsset"].get("text")
            elif a.get("name"):
                item["name"] = a.get("name")
            out.append(item)
        return out
    ev["assets"] = {"campaignLevel": _cap(assets(_rows(data, "camp_assets"), "campaignAsset"), 50),
                    "accountLevelInherited": _cap(assets(_rows(data, "cust_assets"), "customerAsset"), 50)}
    ev["automaticallyCreatedAssets"] = [{"level": lv, "field": f} for lv, f in auto._auto_created(ctx)] or "none found at campaign or account level"
    pm = {}
    for r in _rows(data, "asset_groups"):
        g = r.get("assetGroup") or {}
        pm[str(g.get("id"))] = {"name": g.get("name"), "status": g.get("status"), "adStrength": g.get("adStrength"), "finalUrls": g.get("finalUrls"),
                                "texts": {}, "counts": {}}
    for r in _rows(data, "asset_group_assets"):
        g = pm.get(str((r.get("assetGroup") or {}).get("id")))
        link = r.get("assetGroupAsset") or {}
        if g is None or link.get("status") == "REMOVED":
            continue
        f = link.get("fieldType")
        g["counts"][f] = g["counts"].get(f, 0) + 1
        t = ((r.get("asset") or {}).get("textAsset") or {}).get("text")
        if t:
            g["texts"].setdefault(f, []).append(t)
    ev["performanceMaxAssetGroups"] = _cap(pm.values(), 10)
    ev["performanceMaxSignals"] = ev["demandGenTargeting"] = None            # only a Performance Max or a Demand Gen campaign has them
    if ctx.channel == "PERFORMANCE_MAX":
        ev["performanceMaxSignals"] = [{"assetGroup": g["name"], "audienceSignals": g["audiences"] or "none", "searchThemes": g["themes"] or "none"}
                                       for g in auto._pmax_groups(ctx)]
        ev["performanceMaxSignals"] = _cap(ev["performanceMaxSignals"], 10)
    if ctx.channel == "DEMAND_GEN":
        by_group = {}
        for r in _rows(data, "ag_audiences"):
            cc = r.get("adGroupCriterion") or {}
            ref = ((cc.get("userList") or {}).get("userList") or (cc.get("userInterest") or {}).get("userInterestCategory")
                   or (cc.get("customAudience") or {}).get("customAudience") or (cc.get("customIntent") or {}).get("customIntent")
                   or (cc.get("combinedAudience") or {}).get("combinedAudience") or (cc.get("audience") or {}).get("audience"))
            slot = by_group.setdefault((r.get("adGroup") or {}).get("name") or "?", {"audiences": [], "excluded": []})
            slot["excluded" if cc.get("negative") else "audiences"].append(ctx.names.get(ref) or cc.get("displayName") or ctx.label(ref))
        ev["demandGenTargeting"] = {"audiencesByAdGroup": by_group or "none",
                                    "optimizedTargeting": (camp.get("demandGenCampaignSettings") or {}).get("upgradedTargeting")}
    ev["autoApplyRecommendationsEnabled"] = auto._enabled_recs(ctx) or "none: no recommendation type is subscribed to auto-apply"
    ev["accountSettingsNotVisibleToTheApi"] = ["automatically created assets switches (apps, locations, promotions, logos, names, callouts, images, sitelinks, snippets, longer headlines, seller ratings)",
                                                "automatically generated assets switch", "auto migration and upgrades"]
    pages = []
    for p in data.get("_pages") or []:
        q = dict(p)
        q["text"] = (q.get("text") or "")[:1800]
        pages.append(q)
    ev["landingPages"] = pages
    book = data.get("_book")
    ev["campaignBook"] = ({"filename": book.get("filename"), "fields": book.get("rows"), "everythingOnItsSheets": book.get("text")}
                          if book else
                          "not used for this campaign type (%s): the checks that depend on it are judged on the spot" % data["_bookNotUsed"]
                          if data.get("_bookNotUsed") else "none uploaded")
    return ev


# Which evidence each section reads. Anything not listed here reads everything.
SECTION_EVIDENCE = {
    "1": ["account", "campaign", "targeting", "negatives", "conversionGoals", "adGroups", "assets", "performanceMaxAssetGroups",
          "performanceMaxSignals", "demandGenTargeting", "landingPages", "campaignBook"],
    "6": ["campaign", "targeting", "adGroups", "performanceMaxAssetGroups", "performanceMaxSignals", "demandGenTargeting", "landingPages",
          "campaignBook"],
    "2": ["campaign", "campaignBook"],
    "3": ["assets", "automaticallyCreatedAssets", "accountSettingsNotVisibleToTheApi", "campaignBook"],
    "4": ["autoApplyRecommendationsEnabled", "campaignBook"],
    "5": ["automaticallyCreatedAssets", "campaign", "accountSettingsNotVisibleToTheApi", "campaignBook"],
}


def section_key(check):
    return check["id"].split(".")[0]


def _slice(ev, key):
    wanted = SECTION_EVIDENCE.get(key)
    return {k: ev[k] for k in (wanted or ev) if ev.get(k) is not None}


# ── Asking, and checking what comes back ────────────────────────────────────

def _tokens(text):
    return re.findall(r"[a-z0-9_.@/%-]{3,}", (text or "").lower())


def grounded(quote, evidence_text):
    """True when most of the words and numbers in `quote` really are in the evidence it
    was given. A quote the model made up fails; a quote it trimmed or reformatted passes."""
    toks = _tokens(quote)
    if len(toks) < 2 and len(quote.strip()) < MIN_QUOTE:
        return False
    low = evidence_text.lower()
    hit = sum(1 for t in toks if t in low)
    return not toks or hit / len(toks) >= GROUNDED


def _clean(text):
    return re.sub(r"\s*—\s*", ", ", " ".join(str(text or "").split()))


def validate(results, asked, evidence_text):
    """{check_id: finding} for every id asked, whatever the model sent. A check the model
    skipped, answered twice in conflict, or answered without evidence is cannot_verify."""
    by_id = {}
    for r in results if isinstance(results, list) else []:
        if isinstance(r, dict) and str(r.get("id")) in asked and str(r["id"]) not in by_id:
            by_id[str(r["id"])] = r
    out = {}
    for cid in asked:
        r = by_id.get(cid)
        if r is None:
            out[cid] = _unverified("The AI did not answer this check.")
            continue
        verdict, finding = r.get("verdict"), _clean(r.get("finding"))
        quote, fix = _clean(r.get("evidence"))[:MAX_QUOTE], _clean(r.get("fix"))
        if verdict == "cannot_verify" or verdict not in ("pass", "flag"):
            out[cid] = _unverified(finding or "The evidence cannot answer this check.")
        elif len(finding) < 8:
            out[cid] = _unverified("The AI gave no usable finding for this check.")
        elif not quote or not grounded(quote, evidence_text):
            out[cid] = _unverified("The AI's answer could not be tied to the evidence, so it is not counted. It said: " + finding)
        else:
            out[cid] = {"result": "pass" if verdict == "pass" else "fail", "found": finding, "evidence": quote,
                        "fix": fix if verdict == "flag" else "", "source": "ai", "unverified": False}
    return out


def _unverified(why):
    return {"result": None, "found": why, "evidence": "", "fix": "", "source": "ai", "unverified": True}


def _check_rows(checks, rules, book_used=True):
    out = []
    for it in checks:
        row = {"id": it["id"], "check": it["check"], "expectedValue": it.get("expected") or "", "otherThingsToCheck": it.get("otherThingsToCheck") or "",
               "fromBook": bool(it.get("fromBook")) and book_used}
        if it.get("fromBook") and not book_used and it.get("onTheSpot"):
            row["judgeOnTheSpot"] = it["onTheSpot"]
        if it.get("condition"):
            row["appliesOnlyIf"] = it["condition"]
        if rules.get(it["id"]):
            row["ruleFinding"] = rules[it["id"]]["found"]
        out.append(row)
    return out


def review_section(section, checks, ev, rules, meta, *, call):
    """Ask about one section. Returns {check_id: finding}; raises nothing the caller must handle."""
    asked = [i["id"] for i in checks]
    key = section_key(checks[0])
    sliced = _slice(ev, key)
    user = json.dumps({"section": section, "campaign": {"name": meta.get("name"), "type": meta.get("type")},
                       "today": meta.get("today"), "evidence": sliced, "checks": _check_rows(checks, rules, meta.get("bookUsed", True))}, indent=None, default=str)
    try:
        parsed = call(SYSTEM, user, max_tokens=min(9000, 380 * len(asked) + 600))
    except Exception as e:                                  # noqa: BLE001 (kinds in ModelError; a section never stops the rest)
        kind = getattr(e, "kind", type(e).__name__)
        log.warning("qa ai: section %s failed: %s", key, str(e)[:200])
        why = "%s (%s)." % (UNAVAILABLE, kind)
        return {cid: _unverified(why) for cid in asked}, kind
    seen = json.dumps(sliced, default=str) + json.dumps([rules[c] for c in asked if rules.get(c)], default=str)
    return validate((parsed or {}).get("results"), asked, seen), None


def default_call(system, user, *, max_tokens):
    from .market_radar_llm import call_json
    parsed, _meta = call_json(system, user, SCHEMA, model=MODEL, max_tokens=max_tokens, stage="qa_review")
    return parsed


def review(checks, ev, rules, meta, *, call=None, progress=None, workers=4):
    """Review every check in `checks` (checklist rows). Returns (findings, problems) where
    problems is {kind: how many sections hit it}. `progress(done, total)` after each section."""
    call = call or default_call
    sections = {}
    for it in checks:
        sections.setdefault(it["section"], []).append(it)
    order = list(sections.items())
    findings, problems, done = {}, {}, [0]

    def one(item):
        section, rows = item
        return review_section(section, rows, ev, rules, meta, call=call)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for got, kind in ex.map(one, order):
            findings.update(got)
            if kind:
                problems[kind] = problems.get(kind, 0) + 1
            done[0] += 1
            if progress:
                progress(done[0], len(order))
    return findings, problems


def combine(rules, ai, ids):
    """One finding per check. An exact rule's pass or fail stands. Otherwise the AI's
    answer, with the rule's printed facts kept beside it. With neither, the check is
    unverified (a person must look)."""
    out = {}
    for cid in ids:
        r, a = rules.get(cid), ai.get(cid)
        if r and r["result"] in ("pass", "fail"):
            out[cid] = {"result": r["result"], "found": r["found"], "evidence": "", "fix": "", "source": "rule", "unverified": False}
        elif a:
            out[cid] = dict(a, facts=r["found"] if r else "")
        elif r:
            out[cid] = {"result": None, "found": r["found"], "evidence": "", "fix": "", "source": "rule", "unverified": True}
        else:
            out[cid] = _unverified("Nothing was read that answers this check.")
    return out
