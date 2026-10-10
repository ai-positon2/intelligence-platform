"""Market Radar, Phase 10: a competitor's LinkedIn company posts.

Through the platform's connected LinkedIn account on Unipile
(tracker/sci_source_linkedin_unipile, which also checks the page belongs to
this company: two firms share a name more often than not). The page comes
from the link on the competitor's own site; once found it is remembered,
so a site that later refuses us still gets its posts read.

Posts are stored as "announcement" events, like posts from a company's own
newsroom, and the signal engine (tracker/market_radar_signals) reads them
into typed moves or leaves them out with a reason. Each read goes through a
real person's LinkedIn session, so it is kept small: B2B companies only, at
most weekly, the latest 20 posts.
"""
from __future__ import annotations

import re

from .market_radar_detectors import _date, _event, _fail

MAX_POSTS = 20
WINDOW_DAYS = 30


def _title(caption):
    text = " ".join(str(caption or "").split())
    if not text:
        return None
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return (first if len(first) >= 25 else text)[:160]


def read_linkedin(ctx, *, collect=None, available=None):
    hooks = ctx.get("hooks") or {}
    collect = collect or hooks.get("linkedin_collect")
    available = available or hooks.get("linkedin_available")
    entity = ctx["entity"]
    prev = ctx["prev"].get("linkedin") or {}
    rs = ctx.get("site") or {}
    handle = prev.get("handle") or ((rs.get("signals") or {}).get("socials") or {}).get("linkedin")
    if not handle:
        if rs.get("status") != "ok":
            return _fail("failed", "its website could not be read, so its LinkedIn page is unknown")
        return {"status": "none", "note": "no LinkedIn company page linked from its site",
                "payload": None, "items": 0, "complete": True}
    if collect is None:
        from . import sci_source_linkedin_unipile as li
        from . import unipile_transport

        def available():
            return unipile_transport.account_for_platform("linkedin") is not None

        def collect(h):
            return li.collect_with_page(h, max_posts=MAX_POSTS, strict=True,
                                        company_name=entity.get("name"),
                                        company_url="https://" + entity["domain"])
    if available is not None and not available():
        return {"status": "skipped", "note": "no LinkedIn account is connected on Unipile",
                "payload": None, "items": 0}
    try:
        posts, page = collect(handle)
    except Exception as e:
        # A page that is some other company's is said as such, not retried.
        return _fail("failed", "LinkedIn page %s: %s" % (handle, str(e)[:200]))
    items = []
    for p in posts or []:
        title = _title(p.get("caption"))
        if not title or not p.get("platform_post_id"):
            continue
        items.append({"id": str(p["platform_post_id"]), "title": title,
                      "date": _date(p.get("posted_at")), "url": p.get("post_url")})
    payload = {"handle": handle, "page": (page or {}).get("page"),
               "verification": (page or {}).get("verification"), "posts": items}
    return {"status": "ok" if items else "empty",
            "note": "%d recent posts on %s" % (len(items), (page or {}).get("page") or handle),
            "payload": payload, "items": len(items), "complete": True}


def _post_event(p):
    return _event("post:" + p["id"], "announcement", p["title"], status="announced",
                  date=p.get("date"), url=p.get("url"))


def baseline_linkedin(cur, now):
    from datetime import timedelta
    since = (now - timedelta(days=WINDOW_DAYS)).date().isoformat()
    return [_post_event(p) for p in cur.get("posts") or [] if (p.get("date") or "") >= since]


def compare_linkedin(prev, cur, now):
    from datetime import timedelta
    since = (now - timedelta(days=WINDOW_DAYS)).date().isoformat()
    seen = {p["id"] for p in prev.get("posts") or []}
    # An older post that comes into view (a newer one was deleted) is not new.
    return [_post_event(p) for p in cur.get("posts") or []
            if p["id"] not in seen and (p.get("date") or "") >= since]
