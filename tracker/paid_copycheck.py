"""Paid Marketing Agents: Ad Copy Checker, the reading and the report.

For a Google Ads account (or every account of an MCC) or a Meta ad account this reads the text of the live ads: the Search ads (responsive and the old expanded kind), the Display ads,
the text assets of Performance Max asset groups, the sitelinks, callouts and snippets of live campaigns and of the account, and for Meta the primary text, headline and description of
every active ad. The Search ads are the point (a failure there is raised); the other kinds are soft: a refusal leaves that kind out and says so. What is wrong with the text is
tracker/paid_copycheck_rules.py and paid_copycheck_analyse.py. Nothing is changed in an account.

The read is cached like the other agents (tracker/paid_geo.py _cached) but what the team saved (phone numbers, places, brand spellings, allowed words) is applied to the saved read each
time, so a change in the Settings panel shows at once without another read of Google.

The field names of the Google queries are the API's and the Meta ones are Meta's documented ones; none has been run against the live service from here, which is why every read but the
Search ads is soft.
"""
from __future__ import annotations

import datetime as dt
import json
from concurrent.futures import ThreadPoolExecutor

from . import paid_copycheck_analyse as an
from . import paid_copycheck_plan as plan
from . import paid_copycheck_rules as rules
from . import paid_gads_segments as gads
from . import paid_segments as seg

BadRequest = seg.BadRequest
MAX_MEMBERS = seg.MAX_MEMBERS
MAX_TEXT = 600                      # characters kept of one text (a Meta primary text can be long)

_ENABLED = "campaign.status = ENABLED AND ad_group.status = ENABLED AND ad_group_ad.status = ENABLED"
GAQL_SEARCH = ("SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, ad_group_ad.ad.id, ad_group_ad.ad.type, ad_group_ad.ad.final_urls, "
               "ad_group_ad.ad.responsive_search_ad.headlines, ad_group_ad.ad.responsive_search_ad.descriptions, ad_group_ad.ad.responsive_search_ad.path1, ad_group_ad.ad.responsive_search_ad.path2, "
               "ad_group_ad.ad.expanded_text_ad.headline_part1, ad_group_ad.ad.expanded_text_ad.headline_part2, ad_group_ad.ad.expanded_text_ad.headline_part3, "
               "ad_group_ad.ad.expanded_text_ad.description, ad_group_ad.ad.expanded_text_ad.description2, ad_group_ad.ad.expanded_text_ad.path1, ad_group_ad.ad.expanded_text_ad.path2 "
               "FROM ad_group_ad WHERE %s AND ad_group_ad.ad.type IN ('RESPONSIVE_SEARCH_AD', 'EXPANDED_TEXT_AD')" % _ENABLED)
GAQL_SEARCH_PLAIN = ("SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, ad_group_ad.ad.id, ad_group_ad.ad.type, ad_group_ad.ad.final_urls, "
                     "ad_group_ad.ad.responsive_search_ad.headlines, ad_group_ad.ad.responsive_search_ad.descriptions, ad_group_ad.ad.responsive_search_ad.path1, "
                     "ad_group_ad.ad.responsive_search_ad.path2 FROM ad_group_ad WHERE %s AND ad_group_ad.ad.type = 'RESPONSIVE_SEARCH_AD'" % _ENABLED)
GAQL_DISPLAY = ("SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, ad_group_ad.ad.id, ad_group_ad.ad.final_urls, ad_group_ad.ad.responsive_display_ad.headlines, "
                "ad_group_ad.ad.responsive_display_ad.long_headline, ad_group_ad.ad.responsive_display_ad.descriptions FROM ad_group_ad "
                "WHERE %s AND ad_group_ad.ad.type = 'RESPONSIVE_DISPLAY_AD'" % _ENABLED)
GAQL_PMAX = ("SELECT campaign.id, campaign.name, asset_group.id, asset_group.name, asset_group_asset.field_type, asset.id, asset.text_asset.text FROM asset_group_asset "
             "WHERE campaign.status = ENABLED AND asset_group.status = ENABLED AND asset_group_asset.status = ENABLED "
             "AND asset_group_asset.field_type IN ('HEADLINE', 'LONG_HEADLINE', 'DESCRIPTION')")
_ASSET_FIELDS = ("asset.id, asset.type, asset.sitelink_asset.link_text, asset.sitelink_asset.description1, asset.sitelink_asset.description2, asset.callout_asset.callout_text, "
                 "asset.structured_snippet_asset.header, asset.structured_snippet_asset.values")
GAQL_CAMPAIGN_ASSETS = ("SELECT campaign.id, campaign.name, campaign_asset.field_type, %s FROM campaign_asset WHERE campaign.status = ENABLED AND campaign_asset.status = ENABLED "
                        "AND campaign_asset.field_type IN ('SITELINK', 'CALLOUT', 'STRUCTURED_SNIPPET')" % _ASSET_FIELDS)
GAQL_CUSTOMER_ASSETS = ("SELECT customer_asset.field_type, %s FROM customer_asset WHERE customer_asset.status = ENABLED "
                        "AND customer_asset.field_type IN ('SITELINK', 'CALLOUT', 'STRUCTURED_SNIPPET')" % _ASSET_FIELDS)

META_ADS_FIELDS = ("id,name,effective_status,adset{id,name},campaign{id,name},creative{title,body,link_url,object_story_spec,asset_feed_spec}")


# ── Reading the API's rows ──────────────────────────────────────────────────

def _get(row, *path, default=None):
    for p in path:
        if not isinstance(row, dict):
            return default
        row = row.get(p)
    return default if row is None else row


def _add(texts, slot, value):
    t = " ".join(str(value or "").split())[:MAX_TEXT] if value else ""
    if t and not any(x["slot"] == slot and x["text"] == t for x in texts):
        texts.append({"slot": slot, "text": t})


def _lines(items):
    return [str(i.get("text") if isinstance(i, dict) else i or "") for i in items or []]


def read_search_ads(rows):
    """Search ads as {id, platform, kind, campaignId, campaign, adGroupId, adGroup, url, texts} from Google's answer. An ad without an id or without any text is dropped."""
    out = []
    for r in rows:
        ad = _get(r, "adGroupAd", "ad", default={}) or {}
        aid = str(ad.get("id") or "")
        if not aid:
            continue
        texts = []
        rsa, eta = ad.get("responsiveSearchAd") or {}, ad.get("expandedTextAd") or {}
        for h in _lines(rsa.get("headlines")):
            _add(texts, "headline", h)
        for d in _lines(rsa.get("descriptions")):
            _add(texts, "description", d)
        for k in ("headlinePart1", "headlinePart2", "headlinePart3"):
            _add(texts, "headline", eta.get(k))
        for k in ("description", "description2"):
            _add(texts, "description", eta.get(k))
        for src in (rsa, eta):
            for k in ("path1", "path2"):
                _add(texts, "path", src.get(k))
        if texts:
            out.append({"id": aid, "platform": "google", "kind": "eta" if eta and not rsa else "rsa", "campaignId": str(_get(r, "campaign", "id", default="") or ""),
                        "campaign": _get(r, "campaign", "name", default="") or "", "adGroupId": str(_get(r, "adGroup", "id", default="") or ""), "adGroup": _get(r, "adGroup", "name", default="") or "",
                        "url": (ad.get("finalUrls") or [""])[0], "texts": texts})
    return out


def read_display_ads(rows):
    out = []
    for r in rows:
        ad = _get(r, "adGroupAd", "ad", default={}) or {}
        aid, rda, texts = str(ad.get("id") or ""), ad.get("responsiveDisplayAd") or {}, []
        for h in _lines(rda.get("headlines")):
            _add(texts, "headline", h)
        _add(texts, "longheadline", (rda.get("longHeadline") or {}).get("text") if isinstance(rda.get("longHeadline"), dict) else rda.get("longHeadline"))
        for d in _lines(rda.get("descriptions")):
            _add(texts, "description", d)
        if aid and texts:
            out.append({"id": aid, "platform": "google", "kind": "display", "campaignId": str(_get(r, "campaign", "id", default="") or ""), "campaign": _get(r, "campaign", "name", default="") or "",
                        "adGroupId": str(_get(r, "adGroup", "id", default="") or ""), "adGroup": _get(r, "adGroup", "name", default="") or "", "url": (ad.get("finalUrls") or [""])[0], "texts": texts})
    return out


_PMAX_SLOT = {"HEADLINE": "headline", "LONG_HEADLINE": "longheadline", "DESCRIPTION": "description"}


def read_pmax_text(rows):
    """One 'ad' per Performance Max asset group holding its text assets."""
    by = {}
    for r in rows:
        gid = str(_get(r, "assetGroup", "id", default="") or "")
        slot = _PMAX_SLOT.get(str(_get(r, "assetGroupAsset", "fieldType", default="") or ""))
        if not gid or not slot:
            continue
        g = by.setdefault(gid, {"id": "pmax:" + gid, "platform": "google", "kind": "pmax", "campaignId": str(_get(r, "campaign", "id", default="") or ""),
                                "campaign": _get(r, "campaign", "name", default="") or "", "adGroupId": gid, "adGroup": _get(r, "assetGroup", "name", default="") or "Asset group %s" % gid, "url": "", "texts": []})
        _add(g["texts"], slot, _get(r, "asset", "textAsset", "text", default=""))
    return [g for g in by.values() if g["texts"]]


def read_assets(rows):
    """One 'ad' per sitelink, callout or snippet (an asset used by several campaigns is one, naming the first campaign)."""
    out = {}
    for r in rows:
        a = r.get("asset") or {}
        aid = str(a.get("id") or "")
        if not aid or aid in out:
            continue
        texts, kind = [], str(a.get("type") or "").lower()
        sl, co, sn = a.get("sitelinkAsset") or {}, a.get("calloutAsset") or {}, a.get("structuredSnippetAsset") or {}
        _add(texts, "sitelink", sl.get("linkText"))
        _add(texts, "sitelinkdesc", sl.get("description1"))
        _add(texts, "sitelinkdesc", sl.get("description2"))
        _add(texts, "callout", co.get("calloutText"))
        for v in sn.get("values") or []:
            _add(texts, "snippet", v)
        if texts:
            out[aid] = {"id": "asset:" + aid, "platform": "google", "kind": {"sitelink": "sitelink", "callout": "callout", "structured_snippet": "snippet"}.get(kind, kind or "asset"), "campaignId": str(_get(r, "campaign", "id", default="") or ""),
                        "campaign": _get(r, "campaign", "name", default="") or "Whole account", "adGroupId": "", "adGroup": "", "url": "", "texts": texts}
    return list(out.values())


def read_meta_ads(rows):
    """Active Meta ads as the same shape: the primary text, headline and description of the creative, wherever Meta keeps them (the plain creative, a link, video or photo post, a carousel
    card, or the lists of a flexible ad)."""
    out = []
    for r in rows:
        if not r.get("id"):
            continue
        cr = r.get("creative") or {}
        story, feed = cr.get("object_story_spec") or {}, cr.get("asset_feed_spec") or {}
        link, video, photo = story.get("link_data") or {}, story.get("video_data") or {}, story.get("photo_data") or {}
        texts = []
        _add(texts, "primary", cr.get("body"))
        _add(texts, "title", cr.get("title"))
        for src, pairs in ((link, (("message", "primary"), ("name", "title"), ("description", "link"))), (video, (("message", "primary"), ("title", "title"), ("link_description", "link"))), (photo, (("caption", "primary"),))):
            for k, slot in pairs:
                _add(texts, slot, src.get(k))
        for card in link.get("child_attachments") or []:
            _add(texts, "title", card.get("name"))
            _add(texts, "link", card.get("description"))
        for key, slot in (("bodies", "primary"), ("titles", "title"), ("descriptions", "link")):
            for item in feed.get(key) or []:
                _add(texts, slot, item.get("text") if isinstance(item, dict) else item)
        if texts:
            out.append({"id": "meta:" + str(r["id"]), "platform": "meta", "kind": "meta", "campaignId": str((r.get("campaign") or {}).get("id") or ""), "campaign": (r.get("campaign") or {}).get("name") or "",
                        "adGroupId": str((r.get("adset") or {}).get("id") or ""), "adGroup": (r.get("adset") or {}).get("name") or "", "url": cr.get("link_url") or link.get("link") or "", "texts": texts})
    return out


def meta_request(act):
    return "/%s/ads" % act, {"fields": META_ADS_FIELDS, "effective_status": json.dumps(["ACTIVE"]), "limit": 100}


# ── One account ─────────────────────────────────────────────────────────────

def _soft(warnings, what, fn, default):
    try:
        return fn()
    except gads.GadsError as e:
        warnings.append("%s could not be read (%s), so they were not checked." % (what, e))
        return default


def read_account(account_id, search):
    """{ads, warnings}: one Google Ads account. The Search ads are raised on failure (a refusal of the expanded-text fields is retried without them); the rest is soft."""
    try:
        search_rows = search(account_id, GAQL_SEARCH)
    except gads.GadsError as e:
        if e.kind in ("quota", "auth", "not_configured", "permission"):
            raise
        search_rows = search(account_id, GAQL_SEARCH_PLAIN)
    warnings = []
    ads = read_search_ads(search_rows)
    ads += _soft(warnings, "The Display ads", lambda: read_display_ads(search(account_id, GAQL_DISPLAY)), [])
    ads += _soft(warnings, "The text of Performance Max asset groups", lambda: read_pmax_text(search(account_id, GAQL_PMAX)), [])
    assets = _soft(warnings, "The sitelinks, callouts and snippets of campaigns", lambda: search(account_id, GAQL_CAMPAIGN_ASSETS), [])
    assets += _soft(warnings, "The sitelinks, callouts and snippets of the account", lambda: search(account_id, GAQL_CUSTOMER_ASSETS), [])
    ads += read_assets(assets)
    return {"ads": ads, "warnings": warnings}


def read_meta_account(act, pages):
    path, params = meta_request(act)
    return {"ads": read_meta_ads(pages(path, params)), "warnings": []}


def google_raw(account, *, search_fn=None, workers=6):
    """{accounts: [{id, name, currency, ads, warnings}], warnings, members}: one account or an MCC's accounts. An account whose Search ads cannot be read is left out and said so; every one
    failing is raised."""
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


def ages_of(issues):
    return {i["fingerprint"]: {"firstSeen": i["firstSeen"], "reopened": i.get("reopened") or 0} for i in issues if i.get("status") == "open"}


def other_places(settings, account_id):
    """{place: name of the account it was saved for} for every other account: the places this account's copy must not name."""
    out = {}
    for aid, a in (settings.get("accounts") or {}).items():
        if aid == str(account_id):
            continue
        for p in a.get("places") or []:
            out.setdefault(p, a.get("name") or aid)
    return out


def assemble(raw, account, *, settings=None, brand_terms=None, speller=None, issues=None, now=None):
    """The report from the raw read. `settings` is what the team saved (tracker/paid_copycheck_settings.py load), `brand_terms` {account id: [words]} from the Brand Spend Alert."""
    from . import paid_copycheck_settings as st
    settings = settings or st.empty()
    accounts, warnings = [], list(raw.get("warnings") or [])
    ages = ages_of(issues) if issues is not None else None
    allowed_all = settings.get("allow") or []
    for a in raw["accounts"]:
        cfg = st.account_of(settings, a["id"])
        res = an.analyse(a["ads"], account={"id": a["id"], "name": a["name"]}, config=cfg, brand_terms=(brand_terms or {}).get(a["id"]) or [], speller=speller, ages=ages, now=now,
                         other_places=other_places(settings, a["id"]), allowed_extra=allowed_all)
        accounts.append(dict(res, id=a["id"], name=a["name"], currency=a.get("currency") or "USD", platform=a.get("platform") or "google",
                             saved={k: len(cfg.get(k) or []) for k in ("phones", "places", "brand", "allow")}, plan=plan.build(res)))
        warnings += ["%s: %s" % (a["name"], w) if account.get("members") else w for w in a.get("warnings") or []]
        if res["truncated"]:
            warnings.append("%s: more than %d ads: the first %d were checked." % (a["name"], an.MAX_ADS, an.MAX_ADS))
    accounts.sort(key=lambda a: (_RANK[a["status"]], -a["counts"]["high"], -a["counts"]["medium"], -a["counts"]["low"], a["name"].lower()))
    counts = {k: 0 for k in _RANK}
    for a in accounts:
        counts[a["status"]] += 1
    if speller is None or not speller.ready:
        warnings.append("The spelling check could not run (no dictionary is installed on the server), so only the other rules were applied.")
    return {
        "account": {"id": account["id"], "name": account["name"], "currency": account.get("currency") or "USD", "platform": account.get("platform") or "google",
                    "isGroup": bool(account.get("members")), "accountCount": len(account.get("members") or [])},
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "warnings": warnings, "accounts": accounts, "agesKnown": issues is not None,
        "rules": [{"rule": k, "label": rules.RULES[k]["label"], "level": rules.RULES[k]["level"], "fix": rules.RULES[k]["fix"]} for k in rules.ORDER],
        "summary": {"counts": counts, "accounts": len(accounts), "ads": sum(a["ads"] for a in accounts), "clean": sum(a["clean"] for a in accounts),
                    "high": sum(a["counts"]["high"] for a in accounts), "medium": sum(a["counts"]["medium"] for a in accounts), "low": sum(a["counts"]["low"] for a in accounts),
                    "findings": sum(a["findingCount"] for a in accounts)},
    }


def report_for(account_id, *, accounts_fn=None, groups_fn=None, search_fn=None, now=None, force=False, store=None, today=None, mon=None, meta_pages_fn=None, meta_account_fn=None,
               asof=None, speller=None):
    """The Ad Copy Checker report for a Google Ads account or an MCC the root MCC holds, or for a Meta ad account (act_...). `force` (Refresh) skips the saved read, at most once per
    paid_gads.REFRESH_COOLDOWN_S for the account. `mon` is the monitors' store: with it the team's saved phone numbers, places, brand spellings and allowed words are applied and each
    open problem has its age."""
    from . import paid_copycheck_settings as st
    from . import paid_geo, paid_meta, paid_store
    store = store or paid_store
    day = today or dt.date.today()
    stamp = asof or dt.datetime.now(dt.timezone.utc)
    warnings, settings, issues, brand_terms = [], None, None, {}
    if mon is not None:
        try:
            settings = st.load(mon)
            issues = mon.issues_all("copycheck")
            from . import paid_brandspend_settings as bs
            brand_terms = {aid: (a.get("brandTerms") or []) for aid, a in (bs.load(mon).get("accounts") or {}).items()}
        except Exception as e:                                                       # noqa: BLE001
            warnings.append("What the team saved (phone numbers, places, brand spellings) and the history could not be read (%s), so those checks were skipped." % e)
    if paid_meta.is_meta(str(account_id or "")):
        act = paid_meta.norm_id(account_id)
        pages = meta_pages_fn or (lambda path, params: paid_meta._pages(path, params, store))
        acct_fn = meta_account_fn or (lambda a: paid_meta.account_by_id(store, a) or {})
        account = {"id": act, "name": (acct_fn(act) or {}).get("name") or act, "currency": (acct_fn(act) or {}).get("currency") or "USD", "platform": "meta"}
        raw, stale = paid_geo._cached("copycheck", act, "ads", day, day, lambda: meta_raw(act, pages_fn=pages, account_fn=acct_fn), force=force, store=store, now=now)
    else:
        account = seg.resolve_target(account_id, accounts_fn=accounts_fn, groups_fn=groups_fn)
        raw, stale = paid_geo._cached("copycheck", account["id"], "ads", day, day, lambda: google_raw(account, search_fn=search_fn), force=force, store=store, now=now)
    report = assemble(raw, account, settings=settings, brand_terms=brand_terms, speller=speller or rules.shared_speller(), issues=issues, now=stamp)
    report["warnings"] = warnings + report["warnings"]
    if raw.get("readAt"):
        report["readAt"] = raw["readAt"]
    return dict(report, stale=stale) if stale else report
