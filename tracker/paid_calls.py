"""Paid Marketing Agents: Call Transcript Analyzer, the CallRail side.

Reads the calls CallRail tracks for the agency's accounts (read-only: every request is a GET) and turns them into two views:

  * the OVERVIEW, from CallRail's own summary endpoint, which counts every call of the range however many there are
    (45,000 a month in one account): calls, answered, missed, first-time callers and CallRail's leads, by source, by
    location, by Google Ads campaign and by keyword;
  * the ANALYSIS, from a sample of the calls that have a recording and a transcript (CallRail's Conversation Intelligence):
    what callers wanted, how many ended in an appointment and why the others did not (tracker/paid_calls_topics.py).

The API key (CALLRAIL_API_KEY on Railway) is sent only in an Authorization header; it is never put in a URL, logged or
returned. CallRail allows 1,000 requests an hour and 10,000 a day for a key; the headers say how many are used and this
stops asking at 90% of either. A view costs about 4 requests (overview) and 7 (analysis) and is saved for 15 minutes.

Privacy: the accounts are HIPAA accounts. Only counts and call metadata are kept or returned; a call's summary, transcript,
caller name and phone number are read in memory to be counted and are dropped (parse_call keeps no caller field at all).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import re
import threading
import time

from . import paid_gads as g
from . import paid_calls_topics as topics
from .paid_gads import AdsError, OverQuota, RefreshTooSoon, NotConfigured  # noqa: F401  (the page handles one set of errors)

log = logging.getLogger(__name__)

BASE = "https://api.callrail.com/v3"
ENV = "CALLRAIL_API_KEY"
ACCOUNT_RE = re.compile(r"^ACC[0-9a-f]{32}$")
COMPANY_RE = re.compile(r"^COM[0-9a-f]{32}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_DAYS = 92
PER_PAGE = 100
SAMPLE_PAGES = 6               # pages of 100 calls read for the analysis, spread evenly across the range
MIN_SAMPLE_SECONDS = 20        # shorter calls have nothing to transcribe
TOP = 25                       # rows kept for the campaign and keyword tables
HOURLY_STOP, DAILY_STOP = 900, 9000     # of the 1,000 an hour and 10,000 a day CallRail allows
TTL_VIEW = 15 * 60
TTL_LISTS = 60 * 60
CACHE_V = "v1:"
SUMMARY_FIELDS = "total_calls,answered_calls,missed_calls,abandoned_calls,first_time_callers,average_duration,leads"
SAMPLE_FIELDS = ("transcription,call_summary,sentiment,lead_score,tags,source,campaign,company_name,duration,answered,call_type,"
                 "start_time,timeline_url")
# A source counts as paid when its name says so. CallRail's own names: "Google Ads", "Google Ads Maps", "Bing Ads", "Yelp Ads",
# "PPC-Yelp". Directories, organic and direct are not.
PAID_SOURCE = re.compile(r"(\bads?\b|\bppc\b|\bpaid\b|\bcpc\b|\bsem\b)", re.I)

_RL = {"hourly": None, "daily": None, "hourlyAllowed": 1000, "dailyAllowed": 10000, "at": 0.0}
_RL_LOCK = threading.Lock()


def key():
    return (os.environ.get(ENV) or "").strip()


def config_problem():
    """None when CallRail can be called, else what is missing."""
    return None if key() else "CallRail is not connected yet: CALLRAIL_API_KEY is not set on the server."


def is_paid(source):
    return bool(PAID_SOURCE.search(str(source or "")))


# ── One request ─────────────────────────────────────────────────────────────

def _note_limits(headers):
    """Remember how much of CallRail's allowance is used, from the x-rate-limit-* headers of an answer."""
    def num(name):
        try:
            return int(headers.get(name))
        except (TypeError, ValueError):
            return None
    with _RL_LOCK:
        for field, name in (("hourly", "x-rate-limit-hourly-used"), ("daily", "x-rate-limit-daily-used"),
                            ("hourlyAllowed", "x-rate-limit-hourly-allowed"), ("dailyAllowed", "x-rate-limit-daily-allowed")):
            v = num(name)
            if v is not None:
                _RL[field] = v
        _RL["at"] = time.time()


def limits():
    with _RL_LOCK:
        return dict(_RL)


def _guard():
    """Stop before CallRail does: at 90% of the hourly or daily allowance, as last seen (the hour resets, so a reading over
    an hour old is not trusted)."""
    with _RL_LOCK:
        fresh = time.time() - _RL["at"] < 3600
        hour, day = _RL["hourly"], _RL["daily"]
    if fresh and hour is not None and hour >= min(HOURLY_STOP, int(_RL["hourlyAllowed"] * 0.9)):
        raise OverQuota("CallRail allows %d requests an hour and this app has used %d of them. Calls come back within the hour; "
                        "views already saved still open." % (_RL["hourlyAllowed"], hour))
    if fresh and day is not None and day >= min(DAILY_STOP, int(_RL["dailyAllowed"] * 0.9)):
        raise OverQuota("CallRail allows %d requests a day and this app has used %d of them. Calls come back tomorrow; "
                        "views already saved still open." % (_RL["dailyAllowed"], day))


def _get(path, params=None, *, http=None):
    """One CallRail GET (v3). Retries a hiccup once; a rate limit or a refused key is an error with a message staff can act on."""
    import requests
    http = http or requests.get
    if not key():
        raise NotConfigured(config_problem())
    url = BASE + "/" + path.lstrip("/")
    params = {k: v for k, v in (params or {}).items() if v not in (None, "")}
    for attempt in (0, 1):
        _guard()
        try:
            r = http(url, headers={"Authorization": 'Token token="%s"' % key()}, params=params, timeout=60)
        except Exception as e:
            raise AdsError("Could not reach CallRail (%s)." % type(e).__name__) from e
        _note_limits(getattr(r, "headers", None) or {})
        if r.status_code == 200:
            return r.json()
        try:
            body = r.json()
        except Exception:
            body = {}
        msg = str(body.get("error") or body.get("message") or "")[:300]
        if r.status_code == 401:
            raise AdsError("CallRail rejected the API key. A new CALLRAIL_API_KEY is needed.", status=401, detail=msg)
        if r.status_code == 403:
            raise AdsError("The CallRail key cannot read this account.", status=403, detail=msg)
        if r.status_code == 429:
            raise OverQuota("CallRail is rate limiting this app right now (it allows 1,000 requests an hour). Try again in a few minutes.")
        if r.status_code in (500, 502, 503, 504) and attempt == 0:
            time.sleep(1.5)
            continue
        raise AdsError("CallRail answered %s%s" % (r.status_code, (": " + msg) if msg else "."), status=r.status_code, detail=msg)
    raise AdsError("CallRail did not answer.")


# ── Accounts and locations ──────────────────────────────────────────────────

def parse_accounts(j):
    out, seen = [], set()
    for a in (j or {}).get("accounts") or []:
        aid = str(a.get("id") or "")
        if not ACCOUNT_RE.match(aid) or aid in seen:
            continue
        seen.add(aid)
        out.append({"id": aid, "name": str(a.get("name") or aid)[:80], "hipaa": bool(a.get("hipaa_account")),
                    "recording": bool(a.get("inbound_recording_enabled"))})
    out.sort(key=lambda a: a["name"].lower())
    return out


def list_accounts(store, *, http=None, force=False, notes=None, stamps=None):
    return g._cached(store, CACHE_V + "calls:accounts", TTL_LISTS, lambda: parse_accounts(_get("a.json", http=http)),
                     notes, force=force, stamps=stamps)


def parse_companies(rows):
    out = [{"id": c["id"], "name": str(c.get("name") or c["id"])[:80], "active": c.get("status") == "active"}
           for c in rows if COMPANY_RE.match(str(c.get("id") or ""))]
    out.sort(key=lambda c: (not c["active"], c["name"].lower()))
    return out


def list_companies(store, account_id, *, http=None, force=False, notes=None, stamps=None):
    def make():
        rows, page = [], 1
        while True:
            j = _get("a/%s/companies.json" % account_id, {"per_page": PER_PAGE, "page": page}, http=http)
            rows.extend(j.get("companies") or [])
            if page >= int(j.get("total_pages") or 1) or page >= 20:
                return parse_companies(rows)
            page += 1
    return g._cached(store, CACHE_V + "calls:companies:" + account_id, TTL_LISTS, make, notes, force=force, stamps=stamps)


def account_of(store, account_id, *, http=None):
    """The account named by `account_id` if the key can read it, else a ValueError the page can show."""
    if not ACCOUNT_RE.match(str(account_id or "")):
        raise ValueError("account should be a CallRail account id (ACC...)")
    found = {a["id"]: a for a in list_accounts(store, http=http)}.get(account_id)
    if not found:
        raise ValueError("that account is not one the CallRail key can read")
    return found


def check_range(start, end):
    if not (isinstance(start, str) and isinstance(end, str) and DATE_RE.match(start) and DATE_RE.match(end)):
        raise ValueError("the range should be two YYYY-MM-DD dates")
    try:
        a, b = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    except ValueError:
        raise ValueError("the range has a date that does not exist")
    if a > b:
        raise ValueError("the range starts after it ends")
    if (b - a).days + 1 > MAX_DAYS:
        raise ValueError("the range is longer than %d days" % MAX_DAYS)
    if b > dt.date.today():
        raise ValueError("the range ends in the future")
    return start, end


def check_company(account_companies, company_id):
    if not company_id:
        return ""
    if not COMPANY_RE.match(company_id) or company_id not in {c["id"] for c in account_companies}:
        raise ValueError("that location is not one of this account's companies")
    return company_id


# ── The overview: CallRail's own counts, every call of the range ────────────

def _metrics(row):
    def n(k):
        try:
            return int(row.get(k) or 0)
        except (TypeError, ValueError):
            return 0
    calls = n("total_calls")
    return {"calls": calls, "answered": n("answered_calls"), "missed": n("missed_calls"), "abandoned": n("abandoned_calls"),
            "firstTime": n("first_time_callers"), "leads": n("leads"),
            "avgDuration": round(float(row.get("average_duration") or 0), 1)}


def parse_summary(j, group):
    """[{key, calls, answered, missed, abandoned, firstTime, leads, avgDuration, missRate, leadRate}] and the range's totals."""
    rows = []
    for r in (j or {}).get("grouped_results") or []:
        m = _metrics(r)
        m["key"] = str(r.get("key") or "")
        rows.append(_rates(m))
    return rows, _rates(_metrics((j or {}).get("total_results") or {}))


def _rates(m):
    m["missRate"] = round(m["missed"] / m["calls"], 4) if m["calls"] else None
    m["leadRate"] = round(m["leads"] / m["calls"], 4) if m["calls"] else None
    return m


def _summary(account_id, group, start, end, company_id, search, http):
    return parse_summary(_get("a/%s/calls/summary.json" % account_id,
                              {"start_date": start, "end_date": end, "group_by": group, "fields": SUMMARY_FIELDS,
                               "company_id": company_id, "search": search}, http=http), group)


def previous_range(start, end):
    """The stretch of equal length that ends the day before `start`."""
    a, b = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    n = (b - a).days + 1
    return (a - dt.timedelta(days=n)).isoformat(), (a - dt.timedelta(days=1)).isoformat()


def overview(store, account_id, start, end, *, company_id="", source="", http=None, force=False, notes=None, stamps=None):
    """Every call of the range, counted by CallRail: totals and the tables by source, location, campaign and keyword. With a
    `source`, the location, campaign and keyword tables are of the calls CallRail's search finds for that source's name (a
    name that is part of another, like Google Ads and Google Ads Maps, brings both)."""
    ck = CACHE_V + "calls:overview:%s:%s:%s:%s:%s" % (account_id, company_id or "-", source or "-", start, end)

    def make():
        ps, pe = previous_range(start, end)
        search = source if source and source != "Unknown" else ""
        jobs = [lambda: _summary(account_id, "source", start, end, company_id, "", http),
                lambda: _summary(account_id, "source", ps, pe, company_id, "", http),
                lambda: _summary(account_id, "campaign", start, end, company_id, search, http),
                lambda: _summary(account_id, "keywords", start, end, company_id, search, http)]
        if not company_id:
            jobs.append(lambda: _summary(account_id, "company", start, end, "", search, http))
        res = g._par(jobs)                  # independent counts, each a second or two: ask together
        (srcs, total), (psrcs, prev), (camps, _c), (kws, _k) = res[:4]
        if source:              # the key numbers are the chosen source's own
            def pick(rows):
                return next((r for r in rows if (r["key"] or "Unknown") == source), None) or _rates(_metrics({}))
            total, prev = pick(srcs), pick(psrcs)
        out = {"start": start, "end": end, "totals": total, "previous": prev, "previousRange": [ps, pe], "company": company_id, "source": source,
               "sources": [dict(s, paid=is_paid(s["key"]), key=s["key"] or "Unknown") for s in sorted(srcs, key=lambda x: -x["calls"])],
               "campaigns": [r for r in sorted(camps, key=lambda x: -x["calls"]) if r["key"] and not r["key"].isdigit()][:TOP],
               "keywords": [r for r in sorted(kws, key=lambda x: -x["calls"]) if r["key"]][:TOP]}
        if not company_id:
            out["locations"] = [dict(r, key=r["key"] or "Unknown") for r in sorted(res[4][0], key=lambda x: -x["calls"])]
        return out
    return g._cached(store, ck, TTL_VIEW, make, notes, force=force, stamps=stamps)


# ── The analysis: a sample of the calls that were transcribed ───────────────

def parse_call(c):
    """The few fields of a CallRail call this keeps. Caller name, number and city are never read from it."""
    return {"id": str(c.get("id") or ""), "start": str(c.get("start_time") or "")[:19], "company": str(c.get("company_name") or "")[:80],
            "source": str(c.get("source") or ""), "campaign": str(c.get("campaign") or "")[:80], "duration": int(c.get("duration") or 0),
            "answered": bool(c.get("answered")), "summary": c.get("call_summary") or "", "transcript": c.get("transcription") or "",
            "tags": [str(t.get("name") if isinstance(t, dict) else t) for t in (c.get("tags") or []) if (t.get("name") if isinstance(t, dict) else t)],
            "sentiment": c.get("sentiment") or "", "score": c.get("lead_score"), "url": _link(c.get("timeline_url"))}


def _link(url):
    """CallRail's link to the call, only if it really is one of theirs."""
    u = str(url or "")
    return u if re.match(r"^https://[a-z0-9.\-]*callrail\.com/", u) else ""


def sample_pages(total_pages, k=SAMPLE_PAGES):
    """Up to k page numbers spread evenly from the first page to the last."""
    total_pages = max(1, int(total_pages))
    if total_pages <= k:
        return list(range(1, total_pages + 1))
    return sorted({1 + int(round(i * (total_pages - 1) / (k - 1))) for i in range(k)})


def analysis(store, account_id, start, end, *, company_id="", source="", http=None, force=False, notes=None, stamps=None):
    """What the sampled calls were about (see paid_calls_topics.analyze), with how much of the range was sampled."""
    ck = CACHE_V + "calls:analysis:%s:%s:%s:%s:%s" % (account_id, company_id or "-", source or "-", start, end)

    def make():
        params = {"per_page": PER_PAGE, "start_date": start, "end_date": end, "min_duration": MIN_SAMPLE_SECONDS,
                  "fields": SAMPLE_FIELDS, "company_id": company_id, "search": source if source and source != "Unknown" else ""}
        first = _get("a/%s/calls.json" % account_id, dict(params, page=1), http=http)
        total = int(first.get("total_records") or 0)
        pages = sample_pages(first.get("total_pages") or 1)
        rows = list(first.get("calls") or [])
        rest = [p for p in pages if p != 1]
        if rest:             # the pages are independent and each is a few seconds of transcripts: ask for them together
            for j in g._par([(lambda p=p: _get("a/%s/calls.json" % account_id, dict(params, page=p), http=http)) for p in rest]):
                rows.extend(j.get("calls") or [])
        calls = [parse_call(c) for c in rows]
        if source:
            calls = [c for c in calls if c["source"] == (source if source != "Unknown" else "")]
        out = topics.analyze(calls)
        out.update({"start": start, "end": end, "company": company_id, "source": source, "matching": total, "minSeconds": MIN_SAMPLE_SECONDS, "pages": len(pages),
                    "withTranscript": sum(1 for c in calls if c["transcript"]), "withSummary": sum(1 for c in calls if c["summary"])})
        return out
    return g._cached(store, ck, TTL_VIEW, make, notes, force=force, stamps=stamps)
