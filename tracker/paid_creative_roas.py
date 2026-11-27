"""Paid Marketing Agents: Creative Analysis, the reading (Meta here, Google Ads in paid_creative_roas_google.py).

Which creatives to scale, keep running or cut, judged against the account's own average return, and which hooks, formats, partnership ads and call-to-action buttons
work. Built first as a stand-alone dashboard for Soteri Skin (ai-positon2/soteri-creative-analysis); here it reads any Meta ad account the shared token can open and any
Google Ads account under Position2's manager account.

What is read, read only, for the last LOOKBACK_DAYS days:
  * /insights at ad level, one row per ad per day, every ad that spent: spend, impressions, link clicks, purchases and purchase value (the platform's own purchase reading,
    tracker/paid_meta.py purchases), leads (the first lead action Meta reports, for an account that sells nothing online), add-to-carts, 3-second video plays and ThruPlays;
  * every ad in those rows (the ads edge filtered to their ids, a hundred at a time: Graph v26 dropped the ?ids= lookup): its name, status, campaign and ad set with their
    statuses, and its creative: the format (single image, video, carousel, catalog or a flexible creative of several), the primary text and its first line (the hook), the
    headline, the call-to-action button and a link to the post;
  * for the creatives of the ads that spend most (EXTRAS_MAX, the running ones first): a larger thumbnail and the Partnership-ad (branded content) setting. Meta reads these
    one creative at a time, so the rest are shown as "Not known" rather than read with hundreds of calls.

The page (static/js/creative_analysis*.js) does the judging for whatever dates, campaign and ad filters the reader picks, so the one read here serves every view. The read
is kept with the shared cache (tracker/paid_geo.py _cached), once per account and day; Refresh reads Meta again (at most once per paid_gads.REFRESH_COOLDOWN_S).
Nothing is changed in an ad account.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from . import paid_meta as meta
from . import paid_segments as seg

BadRequest = seg.BadRequest
AGENT = "creative-analysis"

LOOKBACK_DAYS = 90
# How many average costs per result an ad spends before it is judged ("Too early" until then): one, unless a client asked otherwise.
# Soteri Skin asked to wait for 1.5 x CPA (its Meta and Google Ads accounts).
TOO_EARLY_X = {"act_261215873024315": 1.5, "9557969390": 1.5}
CHUNK_DAYS = 10                  # insights are read ten days at a time so no one answer is huge
EXTRAS_MAX = 300                 # creatives whose thumbnail and partnership setting are read one by one
WORKERS = 6

INSIGHT_FIELDS = "ad_id,ad_name,adset_name,campaign_name,spend,impressions,inline_link_clicks,actions,action_values,video_thruplay_watched_actions"
AD_FIELDS = ("id,name,effective_status,created_time,campaign{id,name,effective_status},adset{id,name,effective_status},"
             "creative{id,object_type,title,body,image_url,thumbnail_url,video_id,call_to_action_type,object_story_spec,asset_feed_spec,"
             "instagram_permalink_url,effective_object_story_id,product_set_id}")
# every status an ad that spent can be in now, archived included (the ads edge leaves archived ones out unless asked)
ALL_STATUSES = ["ACTIVE", "PAUSED", "CAMPAIGN_PAUSED", "ADSET_PAUSED", "ARCHIVED", "PENDING_REVIEW", "IN_PROCESS", "WITH_ISSUES", "DISAPPROVED", "PREAPPROVED",
                "PENDING_BILLING_INFO"]
ATC = ("omni_add_to_cart", "offsite_conversion.fb_pixel_add_to_cart", "add_to_cart")
CTA = {"LEARN_MORE": "Learn more", "SHOP_NOW": "Shop now", "SIGN_UP": "Sign up", "BOOK_NOW": "Book now", "BOOK_TRAVEL": "Book now", "CONTACT_US": "Contact us",
       "GET_OFFER": "Get offer", "GET_OFFER_VIEW": "Get offer", "SUBSCRIBE": "Subscribe", "ORDER_NOW": "Order now", "BUY_NOW": "Buy now", "GET_STARTED": "Get started",
       "SEE_MORE": "See more", "SEE_DETAILS": "See details", "WATCH_MORE": "Watch more", "APPLY_NOW": "Apply now", "DOWNLOAD": "Download", "GET_QUOTE": "Get quote",
       "SEND_MESSAGE": "Send message", "MESSAGE_PAGE": "Send message", "WHATSAPP_MESSAGE": "Send WhatsApp message", "CALL_NOW": "Call now", "REQUEST_TIME": "Request time",
       "REGISTER_NOW": "Register now", "INSTALL_MOBILE_APP": "Install now", "NO_BUTTON": "No button"}


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    return x if x == x else 0.0


# ── One insights row ────────────────────────────────────────────────────────

def parse_row(r):
    """One day of one ad: {date, ad_id, ad_name, adset, campaign, spend, impr, clicks, purch, rev, atc, v3, thru, leads}."""
    purch, rev = meta.purchases(r.get("actions"), r.get("action_values"))
    return {"date": str(r.get("date_start") or "")[:10], "ad_id": str(r.get("ad_id") or ""), "ad_name": r.get("ad_name") or "", "adset": r.get("adset_name") or "",
            "campaign": r.get("campaign_name") or "", "spend": _num(r.get("spend")), "impr": int(_num(r.get("impressions"))), "clicks": int(_num(r.get("inline_link_clicks"))),
            "purch": purch, "rev": rev, "atc": meta._max(r.get("actions"), ATC), "v3": meta._first(r.get("actions"), ("video_view",)),
            "thru": meta._first(r.get("video_thruplay_watched_actions"), ("video_view",)), "leads": meta._first(r.get("actions"), meta.LEAD)}


# ── One creative ────────────────────────────────────────────────────────────

def first_line(text):
    """The hook of the primary text: its first non-empty line, or its first sentence when that line runs long."""
    for line in str(text or "").splitlines():
        line = " ".join(line.split())
        if line:
            if len(line) > 110:
                m = re.match(r"(.{20,110}?[.!?])(\s|$)", line)
                line = m.group(1) if m else line[:107].rstrip() + "…"
            return line
    return ""


def describe_creative(c):
    """{format, cta, body, hook, headline, thumb, video_id, creative_id, post_url} of one creative, wherever Meta keeps each."""
    c = c if isinstance(c, dict) else {}
    oss = c.get("object_story_spec") or {}
    afs = c.get("asset_feed_spec") or {}
    link, video, photo = oss.get("link_data") or {}, oss.get("video_data") or {}, oss.get("photo_data") or {}
    tmpl = oss.get("template_data") or {}
    body = c.get("body") or link.get("message") or video.get("message") or photo.get("caption") or tmpl.get("message") or ""
    title = c.get("title") or link.get("name") or video.get("title") or tmpl.get("name") or ""
    if afs:
        body = body or next((b.get("text") for b in afs.get("bodies") or [] if isinstance(b, dict) and b.get("text")), "")
        title = title or next((t.get("text") for t in afs.get("titles") or [] if isinstance(t, dict) and t.get("text")), "")

    if c.get("product_set_id") or tmpl or "product_set" in json.dumps(afs.get("ad_formats") or []).lower():
        fmt = "Catalog"
    elif afs and len(afs.get("videos") or []) + len(afs.get("images") or []) + bool(c.get("video_id") or video) > 0:
        n_vid, n_img = len(afs.get("videos") or []), len(afs.get("images") or [])
        if not n_vid and (c.get("video_id") or video):      # Advantage+ creatives often keep their one video on the creative itself, not in the asset feed
            n_vid = 1
        if "CAROUSEL" in (afs.get("ad_formats") or []):
            fmt = "Carousel"
        elif n_vid and n_img:
            fmt = "Flexible (mixed)"
        elif n_vid:
            fmt = "Video" if n_vid == 1 else "Flexible (videos)"
        else:
            fmt = "Image" if n_img <= 1 else "Flexible (images)"
    elif link.get("child_attachments"):
        fmt = "Carousel"
    elif video or c.get("video_id") or c.get("object_type") == "VIDEO":
        fmt = "Video"
    elif link or photo or c.get("object_type") in ("SHARE", "PHOTO", "STATUS"):
        fmt = "Image"
    else:
        fmt = "Other"

    afs_cta = afs.get("call_to_action_types") or [x.get("type") for x in afs.get("call_to_actions") or [] if isinstance(x, dict)]
    cta = (c.get("call_to_action_type") or (link.get("call_to_action") or {}).get("type") or (video.get("call_to_action") or {}).get("type")
           or (tmpl.get("call_to_action") or {}).get("type") or (afs_cta[0] if afs_cta else "") or "")
    story = c.get("effective_object_story_id") or ""
    return {"format": fmt, "cta": CTA.get(cta, cta.replace("_", " ").capitalize()) if cta else "No button", "body": str(body).strip(), "hook": first_line(body),
            "headline": " ".join(str(title).split()), "thumb": c.get("image_url") or video.get("image_url") or c.get("thumbnail_url") or "",
            "video_id": str(c.get("video_id") or video.get("video_id") or ""), "creative_id": str(c.get("id") or ""),
            "post_url": c.get("instagram_permalink_url") or ("https://www.facebook.com/" + story if story else "")}


def _picked(d):
    """Any id-like value under a branded-content field: Meta does not document the names a partnership ad is read back under."""
    d = d if isinstance(d, dict) else {}
    return next((str(v) for v in d.values() if isinstance(v, (str, int)) and str(v)), "")


def branded(c):
    """{on, ids}: whether a creative runs as a Partnership ad, and with which identities."""
    fb, ig = _picked(c.get("facebook_branded_content")), _picked(c.get("instagram_branded_content"))
    return {"on": bool(fb or ig), "ids": "Facebook + Instagram" if fb and ig else "Facebook" if fb else "Instagram" if ig else ""}


# ── The reads ───────────────────────────────────────────────────────────────

def window(today, days=LOOKBACK_DAYS):
    return today - dt.timedelta(days=days - 1), today


def insight_requests(act, since, until):
    """[(path, params)]: the daily ad rows, CHUNK_DAYS at a time."""
    out, d = [], since
    while d <= until:
        end = min(d + dt.timedelta(days=CHUNK_DAYS - 1), until)
        out.append(("%s/insights" % act, {"level": "ad", "time_increment": 1, "fields": INSIGHT_FIELDS, "limit": 500, "action_breakdowns": "action_type",
                                          "time_range": json.dumps({"since": d.isoformat(), "until": end.isoformat()}),
                                          "filtering": json.dumps([{"field": "spend", "operator": "GREATER_THAN", "value": 0}])}))
        d = end + dt.timedelta(days=1)
    return out


def ad_requests(act, ids):
    """[(path, params)]: the ads behind the rows, a hundred ids at a time."""
    ids = sorted(set(ids))
    return [("%s/ads" % act, {"fields": AD_FIELDS, "limit": 100, "filtering": json.dumps([{"field": "id", "operator": "IN", "value": ids[i:i + 100]},
                                                                                            {"field": "effective_status", "operator": "IN", "value": ALL_STATUSES}])})
            for i in range(0, len(ids), 100)]


def parse_ad(a):
    info = describe_creative(a.get("creative"))
    camp, adset = a.get("campaign") or {}, a.get("adset") or {}
    info.update({"id": str(a.get("id") or ""), "name": a.get("name") or "", "status": a.get("effective_status") or "", "created": str(a.get("created_time") or "")[:10],
                 "campaign": camp.get("name") or "", "adset": adset.get("name") or "", "campaign_status": camp.get("effective_status") or "",
                 "adset_status": adset.get("effective_status") or "", "partnership": None, "partnership_ids": ""})
    return info


def extras_for(ads, spend):
    """The creatives whose thumbnail and partnership setting are read: the running ads' first, then by spend, EXTRAS_MAX at most."""
    order = sorted(ads.values(), key=lambda a: (a.get("status") != "ACTIVE", -spend.get(a["id"], 0.0)))
    out = []
    for a in order:
        cid = a.get("creative_id")
        if cid and cid not in out:
            out.append(cid)
        if len(out) >= EXTRAS_MAX:
            break
    return out


def read_extra(get, cid):
    """(creative id, thumbnail or None, branded or None). An account that refuses the branded-content fields still gets its thumbnail."""
    q = {"thumbnail_width": 400, "thumbnail_height": 400}
    try:
        c = get(cid, dict(q, fields="thumbnail_url,facebook_branded_content,instagram_branded_content"))
        return cid, c.get("thumbnail_url"), branded(c)
    except meta.OverQuota:
        raise
    except meta.AdsError:
        try:
            return cid, get(cid, dict(q, fields="thumbnail_url")).get("thumbnail_url"), None
        except meta.OverQuota:
            raise
        except meta.AdsError:
            return cid, None, None


def read_account(act, *, pages, get, today, workers=WORKERS):
    """{rows, ads, since, until, warnings, extrasRead}: the raw read of one Meta ad account. The daily rows are the point (a failure there is raised); the ads' details
    and the extras are soft: a part that could not be read is said in `warnings` and the rows still show, named from the insights."""
    since, until = window(today)
    rows = [parse_row(r) for path, params in insight_requests(act, since, until) for r in pages(path, params)]
    rows = [r for r in rows if r["ad_id"] and r["date"]]
    warnings, ads = [], {}
    try:
        for path, params in ad_requests(act, [r["ad_id"] for r in rows]):
            for a in pages(path, params):
                p = parse_ad(a)
                if p["id"]:
                    ads[p["id"]] = p
    except meta.OverQuota:
        raise
    except meta.AdsError as e:
        warnings.append("The ads' details (format, copy, thumbnails) could not be read (%s), so the ads are named from the results only." % str(e)[:160])
    spend = {}
    for r in rows:
        spend[r["ad_id"]] = spend.get(r["ad_id"], 0.0) + r["spend"]
    need = extras_for(ads, spend)
    got = []
    if need:
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(need)))) as pool:
            got = list(pool.map(lambda c: read_extra(get, c), need))
    big = {c: t for c, t, _b in got if t}
    brand = {c: b for c, _t, b in got if b is not None}
    for a in ads.values():
        b = brand.get(a["creative_id"])
        if b is not None:
            a["partnership"], a["partnership_ids"] = b["on"], b["ids"]
        if a["format"] != "Image" or not a["thumb"]:
            a["thumb"] = big.get(a["creative_id"]) or a["thumb"]
    return {"rows": rows, "ads": ads, "since": since.isoformat(), "until": until.isoformat(), "warnings": warnings,
            "extrasRead": len(need), "extrasCapped": len({a["creative_id"] for a in ads.values() if a["creative_id"]}) > len(need)}


# ── What the page gets ──────────────────────────────────────────────────────

ROW_FIELDS = ["date", "ad", "spend", "impr", "clicks", "purch", "rev", "atc", "v3", "thru", "leads"]
AD_KEYS = ["id", "name", "status", "campaign", "adset", "campaign_status", "adset_status", "format", "cta", "partnership", "partnership_ids", "hook", "body", "headline",
           "thumb", "video_id", "creative_id", "post_url", "created", "landing"]


def compact(raw):
    """{ads: [...], rows: [[date, ad index, spend, impr, clicks, purch, rev, atc, v3, thru, leads]], rowFields}: small enough to send whole, so the page can cut it any way."""
    ads = dict(raw.get("ads") or {})
    for r in raw.get("rows") or []:              # an ad Meta no longer returns (deleted) keeps the name the results gave it
        if r["ad_id"] not in ads:
            ads[r["ad_id"]] = {"id": r["ad_id"], "name": r["ad_name"], "status": "DELETED", "campaign": r["campaign"], "adset": r["adset"], "format": "Unknown",
                               "cta": "Not known", "partnership": None}
    ids = sorted(ads, key=str)
    idx = {k: i for i, k in enumerate(ids)}
    rows = sorted([r["date"], idx[r["ad_id"]], round(r["spend"], 2), r["impr"], r["clicks"], round(r["purch"], 2), round(r["rev"], 2), round(r["atc"], 2),
                   round(r["v3"], 2), round(r["thru"], 2), round(r["leads"], 2)] for r in raw.get("rows") or [])
    return {"ads": [{k: ads[i].get(k, "" if k != "partnership" else None) for k in AD_KEYS} for i in ids], "rows": rows, "rowFields": ROW_FIELDS}


def local_today(tz):
    """Today in the account's own time zone (Ads Manager and Google Ads count days there), else on the server's clock."""
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo(tz)).date() if tz else dt.date.today()
    except Exception:
        return dt.date.today()


def report_for(account_id, *, force=False, store=None, today=None, now=None, pages_fn=None, get_fn=None, account_fn=None, google_accounts_fn=None, search_fn=None,
               search_pruned_fn=None):
    """The data of the Creative Analysis page for one Meta ad account (act_...) or one Google Ads account (10 digits). `force` (Refresh) reads the platform again, at most
    once per paid_gads.REFRESH_COOLDOWN_S."""
    from . import paid_geo, paid_store
    store = store or paid_store
    target = str(account_id or "").strip()
    if not target:
        raise BadRequest("Pick a Google Ads or Meta ad account.")
    if meta.is_meta(target):
        act = meta.norm_id(target)
        problem = meta.config_problem()
        if problem and not pages_fn:
            raise BadRequest(problem)
        pages = pages_fn or (lambda path, params: meta._pages(path, params, store))
        get = get_fn or (lambda path, params: meta._get(path, params, store))
        acct = (account_fn or (lambda a: meta.account_by_id(store, a) or {}))(act) or {}
        account = {"id": act, "name": acct.get("name") or act, "currency": acct.get("currency") or "USD", "tz": acct.get("tz") or "", "platform": "meta"}
        day = today or local_today(account["tz"])
        build = lambda: read_account(act, pages=pages, get=get, today=day)
    else:
        from . import paid_creative_roas_google as goog
        if seg.gads.GROUP_ID_RE.match(target):
            raise BadRequest("Pick one Google Ads account: this agent reads one account at a time, not a whole MCC.")
        acct = seg.resolve_target(target, accounts_fn=google_accounts_fn)
        account = {"id": acct["id"], "name": acct.get("name") or acct["id"], "currency": acct.get("currency") or "USD", "tz": acct.get("tz") or "", "platform": "google"}
        day = today or local_today(account["tz"])
        since0, until0 = window(day)
        build = lambda: goog.read_account(account["id"], since0, until0, store=store, search_fn=search_fn, search_pruned_fn=search_pruned_fn)
    since, until = window(day)
    try:
        raw, stale = paid_geo._cached(AGENT + "-" + account["platform"], account["id"], "days", since, until, build, force=force, store=store, now=now)
    except seg.base.RefreshTooSoon:
        raise
    except seg.base.AdsError as e:               # a Google read: said as the platform's Google error (a Meta one stays an AdsError, which the page calls Meta's)
        if account["platform"] == "google":
            raise seg.gads.translate(e) from e
        raise
    out = dict(compact(raw), account=account, platform=account["platform"], since=raw.get("since") or since.isoformat(), until=raw.get("until") or until.isoformat(),
               today=day.isoformat(), lookbackDays=LOOKBACK_DAYS, tooEarlyX=TOO_EARLY_X.get(account["id"], 1), warnings=list(raw.get("warnings") or []), extrasRead=raw.get("extrasRead", 0),
               extrasCapped=bool(raw.get("extrasCapped")), extrasMax=EXTRAS_MAX, readAt=raw.get("readAt") or "")
    if stale:
        out["stale"] = stale
    return out


# ── A read that outlasts one request ────────────────────────────────────────
# The first 90-day read of a large Meta account can take longer than the web server lets a request run (gunicorn --timeout 120). The page's request starts the read in the
# background and waits WAIT_S for it; if it is not done the page is told {loading: true} and asks again, and the finished read is answered from the saved copy.

WAIT_S = 25
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="creative-analysis")
_JOBS = {}
_JOBS_LOCK = __import__("threading").Lock()


def report_or_loading(account_id, *, force=False, wait=WAIT_S, **kw):
    """report_for, or {loading, started, name} while the read is still running in the background. An error the read ends with is raised to the request that sees it end."""
    key = str(account_id or "").strip()
    with _JOBS_LOCK:
        job = _JOBS.get(key)
        if job is None:
            job = _JOBS[key] = {"future": _POOL.submit(report_for, key, force=force, **kw), "started": dt.datetime.now(dt.timezone.utc).isoformat()}
    try:
        return job["future"].result(timeout=wait)
    except FutureTimeout:
        return {"loading": True, "started": job["started"], "account": key}
    finally:
        if job["future"].done():
            with _JOBS_LOCK:
                if _JOBS.get(key) is job:
                    _JOBS.pop(key, None)
