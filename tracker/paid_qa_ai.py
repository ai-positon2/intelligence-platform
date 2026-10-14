"""Paid Marketing Agents: QA Agent, the AI reviewer.

The QC is done by the AI, not by a person ticking boxes. For one live campaign this
takes the evidence gathered by tracker/paid_qa_auto.py (Google Ads) and
tracker/paid_qa_web.py (the landing pages), and has Claude go through every check on
the team's checklist, section by section. For each check it says:

    pass            what it saw, and the evidence it rests on
    flag            what is wrong, the evidence, and the fix
    cannot_verify   what is missing (the client's sign-off, the media plan, a test
                    conversion, a balance): a person has to look

The rules it is held to, and that this module enforces rather than hopes for:

  * It sees only the evidence. Anything not in it is cannot_verify, never a guessed pass.
  * A pass or flag must quote the evidence, and the quote must really be in the evidence
    it was given: an answer that cannot be tied to it is turned into cannot_verify.
  * Exact rules beat the AI. Where Google Ads gives an exact answer (auto-tagging is off,
    the Display Network is on) the rule's result stands; the AI cannot overturn it.
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

SYSTEM = """You are a senior Google Ads specialist doing the quality check of ONE campaign for a PPC agency. You are given the agency's QC checklist rows for one section, the evidence read from Google Ads and from the campaign's landing pages, and the findings of exact rules that already ran. Decide each check yourself; nobody else is going to tick these boxes.

For every check answer with:
- verdict: "pass" if the evidence shows the check is met; "flag" if the evidence shows a real problem; "cannot_verify" if the evidence cannot answer it.
- finding: one or two plain sentences saying what you saw, with the actual values (names, numbers, settings). For a flag, say what is wrong.
- evidence: a short quote or value copied exactly from the evidence that your verdict rests on. Required for pass and flag. Empty for cannot_verify.
- fix: for a flag, the specific change to make and where (campaign, ad group, setting). Empty otherwise.

Rules you must follow:
1. Use only the evidence given. If it is not in the evidence, say cannot_verify and name what is missing in the finding. Never guess a pass, and never flag something just because you cannot see it.
2. You do not have the media plan or the client's approvals. For anything that depends on them (budget amount, target locations, bid targets, client sign-off, a test conversion, an account balance) choose cannot_verify, unless the evidence itself shows an inconsistency worth flagging: a daily budget far above what the campaign spends, locations that contradict the ad copy or the landing page, an offer in an ad that the landing page does not mention, and so on.
3. Be strict on Critical checks and fair on Minor ones. Flag real problems, not style preferences.
4. Findings from exact rules (ruleFindings) are facts read straight from Google Ads. Use them as evidence. Where a rule has already ticked or crossed a check you will not be asked about it.
5. Judge copy only on the text you were given. Check spelling, capitalisation, punctuation, claims that cannot be proven, offers and dates against the landing page text, and whether the keyword and the location appear where the check asks.
6. Answer every check id you were given, exactly once, using the id as given. Do not use em dashes."""


# ── The evidence ────────────────────────────────────────────────────────────

def _rows(data, name):
    return data.get(name) or []


def _cap(items, n):
    items = list(items)
    return items[:n] + ([{"more": len(items) - n}] if len(items) > n else [])


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
        "enhancedConversionsForLeads": (cust.get("conversionTrackingSetting") or {}).get("enhancedConversionsForLeadsEnabled"),
        "billingSetups": [{"status": (r.get("billingSetup") or {}).get("status")} for r in _rows(data, "billing")],
        "autoApplyEnabledTypes": [(r.get("recommendationSubscription") or {}).get("type") for r in _rows(data, "recs")
                                  if (r.get("recommendationSubscription") or {}).get("status") == "ENABLED"],
        "userAccess": [{"email": (r.get("customerUserAccess") or {}).get("emailAddress"), "role": (r.get("customerUserAccess") or {}).get("accessRole")}
                       for r in _rows(data, "user_access")][:20],
    }
    ev["campaign"] = {
        "name": camp.get("name"), "type": auto.type_label(ctx.channel) or ctx.channel, "subType": camp.get("advertisingChannelSubType"),
        "status": camp.get("status"), "servingStatus": camp.get("servingStatus"), "primaryStatus": camp.get("primaryStatus"),
        "start": camp.get("startDateTime"), "end": camp.get("endDateTime"), "experimentType": camp.get("experimentType"),
        "biddingStrategy": camp.get("biddingStrategyType"),
        "targetCpa": (camp.get("targetCpa") or camp.get("maximizeConversions") or {}).get("targetCpaMicros"),
        "targetRoas": (camp.get("targetRoas") or camp.get("maximizeConversionValue") or {}).get("targetRoas"),
        "dailyBudget": auto._money(ctx.budget.get("amountMicros"), ctx.currency), "budgetShared": ctx.budget.get("explicitlyShared"),
        "networks": camp.get("networkSettings"), "locationOptions": camp.get("geoTargetTypeSetting"),
        "assetAutomation": camp.get("assetAutomationSettings"), "aiMax": camp.get("aiMaxSetting"),
        "trackingTemplate": camp.get("trackingUrlTemplate"), "finalUrlSuffix": camp.get("finalUrlSuffix"),
        "customParameters": camp.get("urlCustomParameters"), "shopping": camp.get("shoppingSetting"),
        "dynamicSearchAds": camp.get("dynamicSearchAdsSetting"), "audienceTargeting": camp.get("targetingSetting"),
        "labels": [(r.get("label") or {}).get("name") for r in _rows(data, "labels")],
    }
    inc, exc, devices, demo, audiences, places, sched, prox, negs = [], [], [], [], [], [], [], [], []
    for r in _rows(data, "criteria"):
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
        elif t == "DEVICE":
            devices.append({"device": (cc.get("device") or {}).get("type"), "bidModifier": cc.get("bidModifier"), "negative": cc.get("negative")})
        elif t in ("AGE_RANGE", "GENDER", "INCOME_RANGE", "PARENTAL_STATUS"):
            d = cc.get(t.lower().replace("_range", "Range").replace("_status", "Status")) or {}
            demo.append({"type": t, "value": (d or {}).get("type"), "excluded": bool(cc.get("negative")), "bidModifier": cc.get("bidModifier")})
        elif t in ("USER_LIST", "USER_INTEREST", "CUSTOM_AUDIENCE", "COMBINED_AUDIENCE"):
            audiences.append({"type": t, "excluded": bool(cc.get("negative"))})
        elif t == "PLACEMENT":
            places.append({"url": (cc.get("placement") or {}).get("url"), "excluded": bool(cc.get("negative"))})
        elif t == "KEYWORD" and cc.get("negative"):
            k = cc.get("keyword") or {}
            negs.append("%s [%s]" % (k.get("text"), (k.get("matchType") or "").lower()))
    ev["targeting"] = {"locationsTargeted": _cap(inc, 40), "locationsExcluded": _cap(exc, 20), "radius": prox,
                       "languages": [lang.get(re.sub(r"\D", "", ((r["campaignCriterion"].get("language") or {}).get("languageConstant")) or "")) or "?"
                                     for r in _rows(data, "criteria") if (r.get("campaignCriterion") or {}).get("type") == "LANGUAGE"] or "all languages",
                       "adSchedule": sched or "none (all hours)", "devices": devices, "demographics": demo,
                       "audiences": audiences, "placements": _cap(places, 20),
                       "adGroupAudienceSettings": [{"adGroup": (r.get("adGroup") or {}).get("name"),
                                                    "restrictions": (r["adGroup"].get("targetingSetting") or {}).get("targetRestrictions")}
                                                   for r in _rows(data, "adgroups")][:6]}
    ev["negatives"] = {"campaignNegativeKeywords": _cap(negs, 60),
                       "listsAttachedToCampaign": [{"name": (r.get("sharedSet") or {}).get("name"), "type": (r.get("sharedSet") or {}).get("type"),
                                                    "terms": (r.get("sharedSet") or {}).get("memberCount"), "status": (r.get("campaignSharedSet") or {}).get("status")}
                                                   for r in _rows(data, "shared")],
                       "allSharedListsInAccount": [{"name": (r.get("sharedSet") or {}).get("name"), "type": (r.get("sharedSet") or {}).get("type"),
                                                    "members": (r.get("sharedSet") or {}).get("memberCount")} for r in _rows(data, "shared_all")]}
    conv30 = {}
    for r in _rows(data, "camp_conv"):
        conv30[(r.get("segments") or {}).get("conversionActionName") or "?"] = round(float((r.get("metrics") or {}).get("conversions") or 0), 1)
    ev["conversions"] = {
        "actions": [{"name": a.get("name"), "type": a.get("type"), "status": a.get("status"), "primary": a.get("primaryForGoal"),
                     "counting": a.get("countingType"), "attribution": (a.get("attributionModelSettings") or {}).get("attributionModel")}
                    for a in (r.get("conversionAction") or {} for r in _rows(data, "conv"))][:40],
        "campaignConversionsLast30Days": conv30,
        "audienceListsInAccount": [{"name": (r.get("userList") or {}).get("name"), "type": (r.get("userList") or {}).get("type"),
                                    "sizeSearch": (r.get("userList") or {}).get("sizeForSearch"), "sizeDisplay": (r.get("userList") or {}).get("sizeForDisplay")}
                                   for r in _rows(data, "user_lists")][:30],
        "dataExclusions": [(r.get("biddingDataExclusion") or {}) for r in _rows(data, "exclusions")][:10],
        "seasonalityAdjustments": [(r.get("biddingSeasonalityAdjustment") or {}) for r in _rows(data, "seasonality")][:10],
    }
    groups = {}
    for r in _rows(data, "adgroups"):
        g = r.get("adGroup") or {}
        groups[str(g.get("id"))] = {"name": g.get("name"), "type": g.get("type"), "status": g.get("status"),
                                    "adRotation": g.get("adRotationMode"), "defaultBid": g.get("cpcBidMicros"), "keywords": [], "ads": []}
    for r in _rows(data, "keywords"):
        k = (r.get("adGroupCriterion") or {})
        g = groups.get(str((r.get("adGroup") or {}).get("id")))
        if g is not None and len(g["keywords"]) < 60:
            kw = k.get("keyword") or {}
            g["keywords"].append({"text": kw.get("text"), "match": kw.get("matchType"), "status": k.get("status"), "negative": bool(k.get("negative")),
                                  "serving": k.get("systemServingStatus"), "approval": k.get("approvalStatus")})
    for r in _rows(data, "ads"):
        ad = r.get("adGroupAd") or {}
        g = groups.get(str((r.get("adGroup") or {}).get("id")))
        if g is None:
            continue
        rsa = (ad.get("ad") or {}).get("responsiveSearchAd") or {}
        g["ads"].append({"status": ad.get("status"), "strength": ad.get("adStrength"), "type": (ad.get("ad") or {}).get("type"),
                         "approval": (ad.get("policySummary") or {}).get("approvalStatus"), "finalUrls": (ad.get("ad") or {}).get("finalUrls"),
                         "headlines": [{"text": h.get("text"), **({"pinned": h["pinnedField"]} if h.get("pinnedField") else {})} for h in rsa.get("headlines") or []],
                         "descriptions": [{"text": h.get("text"), **({"pinned": h["pinnedField"]} if h.get("pinnedField") else {})} for h in rsa.get("descriptions") or []],
                         "path1": rsa.get("path1"), "path2": rsa.get("path2")})
    ev["adGroups"] = list(groups.values())[:25]

    def assets(rows, key):
        out = []
        for r in rows:
            a, link = r.get("asset") or {}, r.get(key) or {}
            if link.get("status") == "REMOVED":
                continue
            item = {"field": link.get("fieldType"), "status": link.get("status"), "type": a.get("type")}
            if a.get("sitelinkAsset"):
                s = a["sitelinkAsset"]
                item.update(text=s.get("linkText"), d1=s.get("description1"), d2=s.get("description2"), urls=a.get("finalUrls"))
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
    pm = {}
    for r in _rows(data, "asset_groups"):
        g = r.get("assetGroup") or {}
        pm[str(g.get("id"))] = {"name": g.get("name"), "status": g.get("status"), "adStrength": g.get("adStrength"), "finalUrls": g.get("finalUrls"),
                                "texts": {}, "counts": {}, "audienceSignals": 0, "searchThemes": []}
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
    for r in _rows(data, "asset_group_signals"):
        g = pm.get(str((r.get("assetGroup") or {}).get("id")))
        s = r.get("assetGroupSignal") or {}
        if g is not None:
            if (s.get("audience") or {}).get("audience"):
                g["audienceSignals"] += 1
            if (s.get("searchTheme") or {}).get("text"):
                g["searchThemes"].append(s["searchTheme"]["text"])
    ev["performanceMaxAssetGroups"] = list(pm.values())[:10]
    ev["searchTermsLast30Days"] = [{"term": (r.get("searchTermView") or {}).get("searchTerm"), "status": (r.get("searchTermView") or {}).get("status"),
                                    "clicks": (r.get("metrics") or {}).get("clicks"), "cost": auto._money((r.get("metrics") or {}).get("costMicros"), ctx.currency),
                                    "conversions": (r.get("metrics") or {}).get("conversions")} for r in _rows(data, "search_terms")][:40]
    by_user, by_type = {}, {}
    for r in _rows(data, "changes"):
        e = r.get("changeEvent") or {}
        by_user[e.get("userEmail") or "?"] = by_user.get(e.get("userEmail") or "?", 0) + 1
        k = "%s %s" % (e.get("resourceChangeOperation"), e.get("changeResourceType"))
        by_type[k] = by_type.get(k, 0) + 1
    ev["changeHistoryLast28Days"] = {"events": len(_rows(data, "changes")), "byUser": by_user, "byKind": dict(sorted(by_type.items(), key=lambda kv: -kv[1])[:12])}
    ev["spendLast7Days"] = [{"date": (r.get("segments") or {}).get("date"), "cost": auto._money((r.get("metrics") or {}).get("costMicros"), ctx.currency),
                             "conversions": (r.get("metrics") or {}).get("conversions")} for r in _rows(data, "days")]
    pages = []
    for p in data.get("_pages") or []:
        q = dict(p)
        q["text"] = (q.get("text") or "")[:1800]
        pages.append(q)
    ev["landingPages"] = pages
    return ev


# Which evidence each section reads. Anything not listed here reads everything.
SECTION_EVIDENCE = {
    "1": ["account", "campaign", "assets", "negatives", "conversions", "changeHistoryLast28Days"],
    "2": ["account", "campaign", "conversions", "landingPages", "searchTermsLast30Days"],
    "3": ["account", "campaign", "targeting", "negatives", "adGroups", "spendLast7Days", "changeHistoryLast28Days", "landingPages"],
    "4": ["campaign", "negatives", "adGroups", "searchTermsLast30Days"],
    "5": ["campaign", "targeting", "adGroups", "negatives"],
    "6": ["campaign", "adGroups", "landingPages", "targeting", "assets"],
    "7": ["campaign", "assets", "adGroups", "landingPages"],
    "8": ["campaign", "performanceMaxAssetGroups", "assets", "negatives", "landingPages", "targeting"],
    "9": ["campaign", "account", "performanceMaxAssetGroups", "adGroups"],
    "10": ["campaign", "targeting", "assets", "negatives"],
    "11": ["campaign", "targeting", "assets"],
    "12": ["campaign", "conversions"],
    "13": ["campaign", "landingPages", "adGroups", "account"],
    "14": ["campaign", "targeting", "adGroups", "landingPages", "account"],
    "15": ["campaign", "account", "adGroups", "conversions", "searchTermsLast30Days", "spendLast7Days", "changeHistoryLast28Days",
           "performanceMaxAssetGroups", "negatives"],
}


def section_key(check):
    return check["id"].split(".")[0]


def _slice(ev, key):
    wanted = SECTION_EVIDENCE.get(key)
    return {k: ev[k] for k in (wanted or ev) if k in ev}


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


def _check_rows(checks, rules):
    out = []
    for it in checks:
        row = {"id": it["id"], "check": it["check"], "severity": it["severity"], "expectedSettingOrHowToVerify": it["verify"]}
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
    user = json.dumps({"section": section, "campaign": {"name": meta.get("name"), "type": meta.get("type"),
                                                          "clientTypesChosen": meta.get("clientTypes") or "not chosen"},
                       "today": meta.get("today"), "evidence": sliced, "checks": _check_rows(checks, rules)}, indent=None, default=str)
    try:
        parsed = call(SYSTEM, user, max_tokens=min(9000, 450 * len(asked) + 600))
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
