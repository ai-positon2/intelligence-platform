"""Market Radar, Phase 8: the weekly "what changed" update.

    make_digest(client_id, owner_email, run_id=run)   # build, store
    deliver(digest_id)                                # email and Slack

"What changed" is measured, not asked: the update covers what was FIRST
SEEN since the previous update (or the last seven days for the first one):

  * competitor moves first recorded since then, as scored for this client
    (hidden ones never), strongest first;
  * new businesses nearby and new brands the radar recorded since then;
  * each competitor's open roles now against the read current at "since"
    (a change of at least HIRING_CHANGE roles and HIRING_SHARE of the
    board, so a board that grows from 300 to 303 is not news);
  * the industry pulse's themes, when it was read again since then.

Then the same two steps as the report: Sonnet writes a headline and the
changes that matter with "so what", every line citing those items; Haiku
removes any line whose facts are not in its evidence. A week with nothing
new is said plainly without a model call. The update is stored before it
is sent, and each delivery's result (sent, failed and why, or not set up)
is stored with it.
"""
from __future__ import annotations

import html
import logging
import os
import re
from datetime import datetime, timedelta, timezone

from . import market_radar_report as rep

logger = logging.getLogger(__name__)

FIRST_WINDOW_DAYS = 7
MAX_MOVES = 25
HIRING_CHANGE = 5
HIRING_SHARE = 0.10
WRITER_MODEL = os.environ.get("MR_DIGEST_MODEL", rep.WRITER_MODEL)
PUBLIC_BASE = os.environ.get("PUBLIC_BASE_URL", "https://intelligence.position2.com")


def _iso(v):
    return v.isoformat(timespec="seconds") if hasattr(v, "isoformat") else v


# == what changed ==========================================================================

def changes(client_id, owner_email, *, store, now, since):
    """A pack (the report's shape and references) holding only what is new
    since `since`, plus "hiring" changes."""
    pack = rep.build_pack(client_id, owner_email, store=store, now=now)
    fresh = []
    for m in pack["moves"]:
        seen = m.get("first_seen")
        if seen is None or seen > since:
            fresh.append(m)
    # build_pack ranks a window of 120 days; keep only what is new.
    pack["moves"] = fresh[:MAX_MOVES]
    # New nearby businesses and brands: the radar's events on the client,
    # first recorded since then (the latest scan's list would repeat them).
    me = store.get_client(client_id, owner_email)["entity_id"]
    pack["nearby"], pack["entrants"] = [], []
    for e in store.recent_events([me], days=60):
        if e.get("hidden_reason") or not e.get("first_seen_at") or e["first_seen_at"] <= since:
            continue
        loc = e.get("location") or {}
        url = next((s.get("url") for s in e.get("sources") or [] if s.get("url")), None)
        if e["type"] == "nearby_opening":
            pack["nearby"].append({"ref": "N%d" % (len(pack["nearby"]) + 1),
                                   "name": loc.get("label") or e["title"], "category": "",
                                   "distance_km": loc.get("distance_km"), "lat": loc.get("lat"),
                                   "lon": loc.get("lon"), "status": e["status"],
                                   "certain": not e["title"].startswith("Possibly new"),
                                   "evidence": [x for x in (e.get("summary") or "").split("; ") if x],
                                   "website": url, "title": e["title"]})
        elif e["type"] == "new_entrant":
            pack["entrants"].append({"ref": "B%d" % (len(pack["entrants"]) + 1),
                                     "name": e["title"].replace("New brand in your category: ", ""),
                                     "evidence": [x for x in (e.get("summary") or "").split("; ") if x],
                                     "website": url})
    # Hiring: open roles now against the read current at "since".
    hires = []
    names = {c["entity_id"]: c["name"] for c in pack["competitors"]}
    refs = {c["entity_id"]: c["ref"] for c in pack["competitors"]}
    for eid in names:
        now_snap = store.latest_snapshot(eid, "jobs")
        then = store.snapshot_at(eid, "jobs", since)
        a = ((then or {}).get("payload") or {}).get("open")
        b = ((now_snap or {}).get("payload") or {}).get("open")
        if a is None or b is None:
            continue
        d = b - a
        if hiring_moved(a, b):
            hires.append({"ref": "H%d" % (len(hires) + 1), "company": names[eid],
                          "company_ref": refs[eid], "open": b, "before": a, "change": d,
                          "places": len((now_snap["payload"].get("places") or {})),
                          "functions": list((now_snap["payload"].get("by_function") or {}).items())[:4],
                          "senior": (now_snap["payload"].get("senior_open") or [])[:4],
                          "read": (now_snap.get("last_seen_at").isoformat()[:10]
                                   if now_snap.get("last_seen_at") else None)})
    pack["hiring"] = hires
    # Industry themes only when the pulse was read since.
    if not pack.get("pulse_read_at") or pack["pulse_read_at"] <= since.isoformat():
        pack["themes"], pack["rules"] = [], []
    pack["since"] = since.isoformat(timespec="seconds")
    return pack


def hiring_moved(before, now):
    """A change in open roles worth an update: at least HIRING_CHANGE roles
    and HIRING_SHARE of the board (300 to 303 is noise, 40 to 60 is not)."""
    d = now - before
    return abs(d) >= HIRING_CHANGE and abs(d) >= HIRING_SHARE * max(before, 1)


def nothing_new(pack):
    return not (pack["moves"] or pack["nearby"] or pack["entrants"] or pack["hiring"])


def read_gaps(collection):
    """What this week's collection could not read, as (gaps, read_nothing).
    gaps: the coverage lines where a source failed or was skipped (a
    competitor with no jobs board is not a gap). read_nothing: there were
    competitors and not one source answered for any of them. Without this a
    week whose every read failed was announced as "No new competitor
    moves" (found with the network switched off, 2026-10-10)."""
    if not collection:
        return [], False
    lines = collection.get("coverage") or []
    gaps = [str(l.get("text") or "") for l in lines
            if (l.get("counts") or {}).get("failed") or (l.get("counts") or {}).get("skipped")]
    companies = collection.get("companies") or []
    read_any = any(r.get("status") in ("ok", "empty")
                   for c in companies for r in c.get("rows") or [])
    if collection.get("news_breaker_open"):
        gaps.append("Google News stopped answering during the collection; some news was not read.")
    return [g for g in gaps if g], bool(companies) and not read_any


QUIET = "No new competitor moves, openings or hiring changes since the last update."
QUIET_WITH_GAPS = ("No new competitor moves, openings or hiring changes were found since the "
                   "last update, but some sources could not be read this week (listed below), so "
                   "this is not a full all-clear.")
UNREAD = ("This week's collection could not read any competitor's website, shop, jobs board or "
          "news, so there is nothing to report. This is not an all-clear: the next collection "
          "will try again.")


# == the words ============================================================================

CITED = rep.CITED
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["headline", "items"],
    "properties": {
        "headline": {"type": "object", "additionalProperties": False, "required": ["text", "cites"],
                     "properties": {"text": {"type": "string"}, "cites": CITED}},
        "items": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["text", "so_what", "cites"],
            "properties": {"text": {"type": "string"}, "so_what": {"type": "string"},
                           "cites": CITED}}}}}
SYSTEM = """You write a short weekly update for the business named as CLIENT: what changed in its market since the last update. The evidence pack holds ONLY what is new since then: competitor moves first seen this period (with how much each matters to the client), new businesses nearby or new brands, changes in competitors' open roles (HIRING: before and now), and this period's industry themes if they were read again.

Use only the evidence pack. Every line cites the references it rests on (M3, N1, H2, T1...). Never state a fact that is not in the evidence you cite. A planned or announced move has not happened yet: say "plans to", "will", "announced".

Write:
- headline: one sentence, the single most important change for the client.
- items: 2 to 6 changes that matter to the client, most important first. text: what changed, in one sentence. so_what: one sentence on what it means for the client, specific to its location, offer or customers.

Leave out routine items (new colours in a shop, a page wording change, a small promotion) unless nothing else happened. Facts about the client come only from the CLIENT line. Give a price only with the currency the evidence states. Plain English, short sentences. Never use em dashes or en dashes."""


def write(pack, *, run_id=None, client=None, llm=None):
    if llm is None:
        from . import market_radar_llm as llm
    data, _meta = llm.call_json(SYSTEM, rep.pack_text(pack), SCHEMA, model=WRITER_MODEL,
                                max_tokens=4000, run_id=run_id, stage="digest_write",
                                client=client, effort="medium")
    refs = rep.items_by_ref(pack)
    out = {"headline": None, "items": []}
    h = data.get("headline") or {}
    cites = rep._cites(h.get("cites"), refs)
    if cites and h.get("text"):
        out["headline"] = {"text": rep._clean(h["text"], 300), "cites": cites}
    for it in data.get("items") or []:
        cites = rep._cites(it.get("cites"), refs)
        if cites and it.get("text"):
            out["items"].append({"text": rep._clean(it["text"], 400),
                                 "so_what": rep._clean(it.get("so_what"), 400), "cites": cites})
    out["items"] = out["items"][:6]
    return out


def check(words, pack, *, run_id=None, client=None, llm=None):
    """The report's citation check, on the update's lines."""
    as_brief = {"summary": words["headline"], "top": [], "competitors": [], "local": [],
                "industry": [], "opportunities": [],
                "threats": [], "watch": [{"text": "%s %s" % (i["text"], i["so_what"]),
                                          "cites": i["cites"]} for i in words["items"]]}
    checked, removed, note = rep.check_brief(as_brief, pack, run_id=run_id, client=client, llm=llm)
    kept = {(w["text"]) for w in checked["watch"]}
    items = [i for i in words["items"] if "%s %s" % (i["text"], i["so_what"]) in kept]
    for r in removed:
        if r["where"].startswith("watch."):
            r["where"] = "item %d" % (int(r["where"].split(".")[1]) + 1)
    return {"headline": checked["summary"], "items": items}, removed, note


# == one update ===========================================================================

def make_digest(client_id, owner_email, *, run_id=None, store=None, now=None, llm=None,
                client=None, collection=None):
    """Build and store the update. Returns (digest_id, payload). `collection`
    is this run's collection result, so the update can say what it could
    not read."""
    if store is None:
        from . import market_radar_store as store
    now = now or datetime.now(timezone.utc)
    last = store.latest_digest(client_id)
    since = last["created_at"] if last else now - timedelta(days=FIRST_WINDOW_DAYS)
    pack = changes(client_id, owner_email, store=store, now=now, since=since)
    payload = {"status": "ok", "since": since.isoformat(timespec="seconds"),
               "written_at": now.isoformat(timespec="seconds"), "pack": pack,
               "first": last is None, "client_name": pack["client"]["name"]}
    gaps, read_nothing = read_gaps(collection)
    payload["gaps"] = gaps[:8]
    if nothing_new(pack):
        headline = UNREAD if read_nothing else QUIET_WITH_GAPS if gaps else QUIET
        payload.update(status="unread" if read_nothing else "quiet",
                       words={"headline": {"text": headline, "cites": []}, "items": []},
                       removed=[])
    else:
        try:
            words = write(pack, run_id=run_id, client=client, llm=llm)
        except Exception as e:
            payload.update(status="failed", words=None,
                           note="The update could not be written (%s)." % getattr(
                               e, "kind", type(e).__name__))
        else:
            words, removed, note = check(words, pack, run_id=run_id, client=client, llm=llm)
            payload.update(words=words, removed=removed, check_note=note)
    digest_id, created = store.save_digest(client_id, payload, run_id=run_id, since=since)
    payload["created_at"] = _iso(created)
    return digest_id, payload


# == how it reads ==========================================================================

SEV_WORD = {"HIGH": "High", "MEDIUM": "Medium", "LOW": "Low"}


def _ref_line(item):
    """A short plain-text name for a cited item, and its link when it is a
    web address (a source can hold anything a page linked to)."""
    name, url = _ref_line_raw(item)
    return name, url if url and re.match(r"^https?://", str(url)) else None


def _ref_line_raw(item):
    r = item["ref"]
    if r[0] == "M":
        src = next((s.get("url") for s in item.get("sources") or [] if s.get("url")), None)
        return "%s: %s" % (item["company"], item["title"]), src
    if r[0] == "N":
        return "%s, %s km away" % (item["name"], item.get("distance_km")), item.get("website")
    if r[0] == "B":
        return "New brand: %s" % item["name"], item.get("website")
    if r[0] == "H":
        return "%s: %s open roles, was %s" % (item["company"], item["open"], item["before"]), None
    if r[0] == "T":
        return "Industry: %s" % item["title"], (item.get("articles") or [{}])[0].get("link")
    return r, None


def report_url(client_id):
    return "%s/p2/admin/market-radar/report/%s" % (PUBLIC_BASE, client_id)


def render_text(payload, client_id):
    w = payload.get("words") or {}
    refs = rep.items_by_ref(payload["pack"])
    lines = ["Market Radar weekly update: %s" % payload["client_name"],
             "Changes since %s" % payload["since"][:10], ""]
    if payload["status"] == "failed":
        lines.append(payload.get("note") or "The update could not be written this week.")
    if w.get("headline"):
        lines += [w["headline"]["text"], ""]
    for i, it in enumerate(w.get("items") or []):
        lines.append("%d. %s" % (i + 1, it["text"]))
        if it.get("so_what"):
            lines.append("   So what: " + it["so_what"])
        for r in it["cites"]:
            if r in refs:
                name, url = _ref_line(refs[r])
                lines.append("   - %s%s" % (name, " (%s)" % url if url else ""))
        lines.append("")
    if payload.get("gaps"):
        lines += ["Not read this week:"] + ["- " + g for g in payload["gaps"]] + [""]
    lines += ["Full report: " + report_url(client_id), "",
              "Every line above was checked against its sources; %d were removed." %
              len(payload.get("removed") or [])]
    return "\n".join(lines)


def render_html(payload, client_id):
    """Plain, inline-styled HTML that mail clients keep."""
    e = html.escape
    w = payload.get("words") or {}
    refs = rep.items_by_ref(payload["pack"])

    def link(url, text):
        if url and re.match(r"^https?://", url):
            return '<a href="%s" style="color:#4f46e5;text-decoration:none">%s</a>' % (
                e(url, quote=True), e(text))
        return e(text)

    parts = ['<div style="font-family:Arial,Helvetica,sans-serif;color:#17213a;max-width:640px;'
             'line-height:1.5">',
             '<div style="font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:#4f55d6;'
             'font-weight:bold">Market Radar weekly update</div>',
             '<h1 style="font-size:22px;margin:4px 0 2px">%s</h1>' % e(payload["client_name"]),
             '<div style="font-size:13px;color:#6b7598">Changes since %s</div>' % e(payload["since"][:10])]
    if payload["status"] == "failed":
        parts.append('<p>%s</p>' % e(payload.get("note") or "The update could not be written."))
    if w.get("headline"):
        parts.append('<p style="font-size:16px;margin:16px 0;padding:12px 14px;background:#eef0ff;'
                     'border-radius:8px">%s</p>' % e(w["headline"]["text"]))
    for i, it in enumerate(w.get("items") or []):
        srcs = []
        for r in it["cites"]:
            if r in refs:
                name, url = _ref_line(refs[r])
                srcs.append("<li>%s</li>" % link(url, name))
        parts.append('<div style="margin:14px 0;padding-left:12px;border-left:3px solid #4f55d6">'
                     '<div style="font-weight:bold">%d. %s</div>%s<ul style="margin:6px 0 0;'
                     'padding-left:18px;font-size:13px;color:#45507a">%s</ul></div>' % (
                         i + 1, e(it["text"]),
                         '<div style="margin-top:4px"><b>So what:</b> %s</div>' % e(it["so_what"])
                         if it.get("so_what") else "", "".join(srcs)))
    if payload.get("gaps"):
        parts.append('<div style="margin-top:16px;font-size:13px;color:#45507a"><b>Not read this '
                     'week:</b><ul style="margin:4px 0 0;padding-left:18px">%s</ul></div>' %
                     "".join("<li>%s</li>" % e(g) for g in payload["gaps"]))
    parts.append('<p style="margin-top:20px">%s</p>' % link(report_url(client_id),
                                                            "Open the full report"))
    parts.append('<p style="font-size:12px;color:#6b7598">Every line was checked against its '
                 'sources; %d were removed. You get this because weekly updates are switched on '
                 'for this company in Market Radar.</p></div>' % len(payload.get("removed") or []))
    return "".join(parts)


def render_slack(payload, client_id):
    """(fallback text, blocks)."""
    w = payload.get("words") or {}
    refs = rep.items_by_ref(payload["pack"])

    def esc(t):
        return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    blocks = [{"type": "header", "text": {"type": "plain_text", "text": (
        "Market Radar: %s" % payload["client_name"])[:150]}},
              {"type": "context", "elements": [{"type": "mrkdwn", "text": "Changes since %s" %
                                                payload["since"][:10]}]}]
    if w.get("headline"):
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
                                                   "text": "*%s*" % esc(w["headline"]["text"])}})
    for i, it in enumerate(w.get("items") or []):
        srcs = []
        for r in it["cites"]:
            if r in refs:
                name, url = _ref_line(refs[r])
                srcs.append("<%s|%s>" % (url, esc(name)[:150]) if url and url.startswith("http")
                            else esc(name))
        text = "%d. %s" % (i + 1, esc(it["text"]))
        if it.get("so_what"):
            text += "\n_So what:_ " + esc(it["so_what"])
        if srcs:
            text += "\n" + " · ".join(srcs)
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}})
    if payload.get("gaps"):
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": (
            "*Not read this week:* " + " ".join(esc(g) for g in payload["gaps"]))[:2900]}]})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "<%s|Open the full "
                                                    "report>" % report_url(client_id)}]})
    fallback = "Market Radar update for %s: %s" % (
        payload["client_name"], (w.get("headline") or {}).get("text") or "see the report")
    return fallback, blocks[:48]


# == delivery =============================================================================

def deliver(digest_id, client_id, payload, settings, *, store=None, send_email=None,
            send_slack=None):
    """Send to every destination the client set up. Returns the delivery
    record, which is also stored: {"email": {...}, "slack": {...}}."""
    if store is None:
        from . import market_radar_store as store
    from . import market_radar_deliver as dl
    send_email = send_email or dl.send_email
    send_slack = send_slack or dl.send_slack
    mon = (settings or {}).get("monitor") or {}
    out = {}
    to = [a for a in mon.get("email") or [] if a]
    if to:
        subject = "Market Radar: %s, week of %s" % (payload["client_name"],
                                                    payload["written_at"][:10])
        out["email"] = dl.attempt(lambda: send_email(subject, render_text(payload, client_id),
                                                     render_html(payload, client_id), to))
        out["email"]["to"] = to
    else:
        out["email"] = {"status": "not_set_up"}
    channel = (mon.get("slack_channel") or "").strip()
    if channel:
        text, blocks = render_slack(payload, client_id)
        out["slack"] = dl.attempt(lambda: send_slack(channel, text, blocks))
        out["slack"]["channel"] = channel
    else:
        out["slack"] = {"status": "not_set_up"}
    out["at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    store.set_digest_delivery(digest_id, out)
    return out
