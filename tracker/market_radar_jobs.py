"""Market Radar, Phase 3: a competitor's open jobs, from the public job-board
APIs its careers page links to. No model here.

Hiring is the earliest public sign of a move: store-manager jobs in a city
with no branch yet come before the branch, and a first "Head of" role in a
function comes before the strategy. So the detector keeps every open role
(title, place, team, date posted), counts them by function and place, and
compares week to week.

Boards are found by the site reader (market_radar_site.ats_boards) on the
company's own pages. When it finds none, Greenhouse is asked under the
domain's name and kept only if the board's own name matches the company:
two firms can share a word, and a wrong board would invent a hiring spree.

Readers exist for the boards with a public listing: Greenhouse, Lever,
Ashby, SmartRecruiters, Workable, Recruitee, Personio, Breezy, BambooHR and
Workday. A board without one (iCIMS, Jobvite, Teamtailor and others) is
named in the coverage line as "found but not readable", never as "no jobs".
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone

MAX_WORKDAY_PAGES = 10            # 20 a page: 200 roles, then totals only
MAX_SR_PAGES = 5                  # 100 a page

FUNCTIONS = [   # (function, title pattern), first match wins
    ("leadership", r"\b(chief|ceo|cfo|coo|cto|cmo|president|founder|general manager|"
                   r"managing director|country manager|geschäftsführer|diretor geral)\b"),
    ("engineering", r"\b(engineer|developer|software|devops|sre|data scien|machine learning|"
                    r"\bml\b|\bai\b|architect|qa|test automation|entwickler|desenvolvedor)"),
    ("product_design", r"\b(product manager|product owner|designer|ux|ui|research)"),
    ("data", r"\b(data|analyst|analytics|bi\b|insights)"),
    ("sales", r"\b(sales|account executive|account manager|business development|bdr|sdr|"
              r"partnership|vertrieb|vendas|ventas|commercial)"),
    ("marketing", r"\b(marketing|brand|content|seo|social media|growth|communications|pr\b|"
                  r"creative|copywriter|e-?commerce)"),
    ("clinical", r"\b(dentist|dental|hygienist|nurse|physician|doctor|clinical|therap|"
                 r"pharmac|optometr|veterinar|medical|orthodont|zahnarzt|dentista)"),
    ("store_ops", r"\b(store|retail|shop|sales associate|cashier|barista|crew|team member|"
                  r"front desk|receptionist|showroom|studio|coach|trainer|instructor|"
                  r"filiale|loja|tienda|verkäufer|vendedor)"),
    ("customer", r"\b(customer|support|service|success|care|help ?desk|call center)"),
    ("operations", r"\b(operations|logistics|supply chain|warehouse|fulfil|procurement|"
                   r"inventory|driver|manufactur|production|planner|merchandis)"),
    ("finance_legal", r"\b(finance|accountant|accounting|controller|legal|counsel|tax|audit|"
                      r"payroll|compliance)"),
    ("people", r"\b(recruit|talent|hr\b|human resources|people|learning)"),
]
SENIOR = re.compile(r"\b(head of|vp\b|vice president|director|chief|svp|evp|general manager|"
                    r"managing director|country manager|leiter|diretor|director[a]?)\b", re.I)
REMOTE = re.compile(r"\b(remote|anywhere|hybrid|home ?office|distributed|multiple locations)\b",
                    re.I)
UNREADABLE = {"icims", "jobvite", "teamtailor", "jazzhr", "darwinbox", "keka"}


def function_of(title):
    t = (title or "").lower()
    for name, pattern in FUNCTIONS:
        if re.search(pattern, t):
            return name
    return "other"


def _place(value):
    return " ".join(str(value or "").split())[:120]


def _ms_date(ms):
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _iso(value):
    m = re.match(r"\s*(\d{4}-\d{2}-\d{2})", str(value or ""))
    return m.group(1) if m else None


def workday_posted(text, now):
    """Workday says "Posted Today", "Posted Yesterday", "Posted 5 Days Ago"
    and, past a month, only "Posted 30+ Days Ago" (no date at all)."""
    t = (text or "").lower()
    if "today" in t:
        days = 0
    elif "yesterday" in t:
        days = 1
    else:
        m = re.search(r"(\d+)(\+)?\s*days?", t)
        if not m or m.group(2):
            return None
        days = int(m.group(1))
    return (now - timedelta(days=days)).strftime("%Y-%m-%d")


# == one reader per board ============================================================
# Each returns (jobs, total, complete, note); jobs maps id -> [title, place,
# team, date posted]. jobs is None when the board did not answer.

def _greenhouse(get_json, slug, now):
    data, read = get_json("https://boards-api.greenhouse.io/v1/boards/%s/jobs" % slug)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        return None, 0, False, read["note"] or "no board"
    jobs = {str(j.get("id")): [j.get("title", ""), _place((j.get("location") or {}).get("name")),
                                "", _iso(j.get("first_published") or j.get("updated_at"))]
            for j in data["jobs"] if isinstance(j, dict)}
    return jobs, len(jobs), True, ""


def _lever(get_json, slug, now):
    for host in ("api.lever.co", "api.eu.lever.co"):
        data, read = get_json("https://%s/v0/postings/%s?mode=json" % (host, slug))
        if isinstance(data, list):
            jobs = {}
            for j in data:
                if not isinstance(j, dict):
                    continue
                c = j.get("categories") or {}
                jobs[str(j.get("id"))] = [j.get("text", ""), _place(c.get("location")),
                                          c.get("team") or c.get("department") or "",
                                          _ms_date(j.get("createdAt"))]
            return jobs, len(jobs), True, ""
    return None, 0, False, read["note"] or "no board"


def _ashby(get_json, slug, now):
    data, read = get_json("https://api.ashbyhq.com/posting-api/job-board/%s" % slug)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        return None, 0, False, read["note"] or "no board"
    jobs = {str(j.get("id")): [j.get("title", "").strip(), _place(j.get("location")),
                                j.get("department") or j.get("team") or "",
                                _iso(j.get("publishedAt"))]
            for j in data["jobs"] if isinstance(j, dict) and j.get("isListed", True)}
    return jobs, len(jobs), True, ""


def _smartrecruiters(get_json, slug, now):
    jobs, total = {}, None
    for page in range(MAX_SR_PAGES):
        data, read = get_json("https://api.smartrecruiters.com/v1/companies/%s/postings"
                              "?limit=100&offset=%d" % (slug, page * 100))
        if not isinstance(data, dict) or not isinstance(data.get("content"), list):
            if page == 0:
                return None, 0, False, read["note"] or "no board"
            return jobs, total or len(jobs), False, "stopped at page %d" % (page + 1)
        total = int(data.get("totalFound") or 0)
        for j in data["content"]:
            loc = j.get("location") or {}
            jobs[str(j.get("id"))] = [j.get("name", ""), _place(", ".join(
                x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x)),
                (j.get("department") or {}).get("label") or "", _iso(j.get("releasedDate"))]
        if len(jobs) >= total:
            return jobs, total, True, ""
    return jobs, total, False, "stopped at %d of %d roles" % (len(jobs), total)


def _workable(get_json, slug, now):
    data, read = get_json("https://apply.workable.com/api/v1/widget/accounts/%s" % slug)
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), list):
        return None, 0, False, read["note"] or "no board"
    jobs = {str(j.get("shortcode") or j.get("url")): [
        j.get("title", ""), _place(", ".join(x for x in (j.get("city"), j.get("country")) if x)),
        j.get("department") or "", _iso(j.get("published_on"))] for j in data["jobs"]}
    return jobs, len(jobs), True, ""


def _recruitee(get_json, slug, now):
    data, read = get_json("https://%s.recruitee.com/api/offers/" % slug)
    if not isinstance(data, dict) or not isinstance(data.get("offers"), list):
        return None, 0, False, read["note"] or "no board"
    jobs = {str(j.get("id")): [j.get("title", ""), _place(j.get("location")),
                                j.get("department") or "", _iso(j.get("published_at"))]
            for j in data["offers"]}
    return jobs, len(jobs), True, ""


def _breezy(get_json, slug, now):
    data, read = get_json("https://%s.breezy.hr/json" % slug)
    if not isinstance(data, list):
        return None, 0, False, read["note"] or "no board"
    jobs = {str(j.get("id")): [j.get("name", ""), _place((j.get("location") or {}).get("name")),
                                (j.get("department") or ""), _iso(j.get("published_date"))]
            for j in data if isinstance(j, dict)}
    return jobs, len(jobs), True, ""


def _bamboohr(get_json, slug, now):
    data, read = get_json("https://%s.bamboohr.com/careers/list" % slug)
    if not isinstance(data, dict) or not isinstance(data.get("result"), list):
        return None, 0, False, read["note"] or "no board"
    jobs = {}
    for j in data["result"]:
        loc = j.get("location") or {}
        jobs[str(j.get("id"))] = [j.get("jobOpeningName", ""), _place(", ".join(
            x for x in (loc.get("city"), loc.get("state")) if x)), j.get("departmentLabel") or "",
            None]
    return jobs, len(jobs), True, ""


def _personio(get, slug, now):
    for tld in ("de", "com"):
        read = get("https://%s.jobs.personio.%s/xml" % (slug, tld), accept="application/xml")
        if read["status"] == "ok" and "<position" in read["body"]:
            jobs = {}
            for block in re.findall(r"<position>(.*?)</position>", read["body"], re.S):
                def tag(name):
                    m = re.search(r"<%s>(.*?)</%s>" % (name, name), block, re.S)
                    return re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", m.group(1), flags=re.S).strip() \
                        if m else ""
                jobs[tag("id")] = [tag("name"), _place(tag("office")), tag("department"),
                                   _iso(tag("createdAt"))]
            return jobs, len(jobs), True, ""
    return None, 0, False, read["note"] or "no board"


def workday_site(url):
    """(tenant, wd host, site) from a Workday careers address, or None."""
    m = re.search(r"([\w-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)",
                  url or "")
    return (m.group(1), m.group(2), m.group(3)) if m else None


def _workday(get, url, now):
    parts = workday_site(url)
    if not parts:
        return None, 0, False, "the careers address names no Workday site"
    tenant, wd, site_name = parts
    api = "https://%s.%s.myworkdayjobs.com/wday/cxs/%s/%s/jobs" % (tenant, wd, tenant, site_name)
    jobs, total = {}, None
    for page in range(MAX_WORKDAY_PAGES):
        read = get(api, post={"appliedFacets": {}, "limit": 20, "offset": page * 20,
                              "searchText": ""}, accept="application/json")
        try:
            data = json.loads(read["body"]) if read["status"] == "ok" else None
        except ValueError:
            data = None
        if not isinstance(data, dict) or not isinstance(data.get("jobPostings"), list):
            if page == 0:
                return None, 0, False, read["note"] or "the Workday listing did not answer"
            return jobs, total or len(jobs), False, "stopped at page %d" % (page + 1)
        if page == 0:
            total = int(data.get("total") or 0)
        for j in data["jobPostings"]:
            key = (j.get("bulletFields") or [None])[0] or j.get("externalPath")
            jobs[str(key)] = [j.get("title", ""), _place(j.get("locationsText")), "",
                              workday_posted(j.get("postedOn"), now)]
        if len(jobs) >= (total or 0) or not data["jobPostings"]:
            return jobs, max(total or 0, len(jobs)), True, ""
    return jobs, total, False, "read %d of %d roles" % (len(jobs), total)


READERS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby,
           "smartrecruiters": _smartrecruiters, "workable": _workable, "recruitee": _recruitee,
           "breezy": _breezy, "bamboohr": _bamboohr}


def _fold(text):
    t = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", t)


def guess_greenhouse(get_json, entity):
    """A Greenhouse board under the domain's own name, kept only when the
    board calls itself by the company's name."""
    label = entity["domain"].split(".")[0]
    if len(label) < 4:
        return None
    data, _ = get_json("https://boards-api.greenhouse.io/v1/boards/%s" % label)
    name = _fold((data or {}).get("name") if isinstance(data, dict) else "")
    if name and name in (_fold(entity.get("name")), _fold(label)):
        return {"vendor": "greenhouse", "board": label, "url": "", "guessed": True}
    return None


# == Phenom career sites ===========================================================
# Phenom runs the careers sites of many large chains (careers.aspendental.com,
# 2026-10-09). Its search page embeds the first results AND the counts of
# every open role by city, state and category in one public read, so the
# places a company is hiring in are known exactly even when its 1,772 roles
# are not read one by one.

PHENOM_MARK = re.compile(r"phenompeople\.com|phApp\.ddo", re.I)


def phenom_search(html):
    """The embedded search result of a Phenom page, or None."""
    i = (html or "").find('"eagerLoadRefineSearch"')
    if i < 0:
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(html, i + len('"eagerLoadRefineSearch":'))
    except ValueError:
        return None
    return obj if isinstance(obj, dict) and isinstance(obj.get("data"), dict) else None


def _agg(data, field):
    for a in data.get("aggregations") or []:
        if a.get("field") == field and isinstance(a.get("value"), dict):
            return {str(k): int(v) for k, v in a["value"].items() if str(k).strip()}
    return {}


def read_phenom(get, careers_url, now):
    """(board, rows, places, regions, categories, note) from a Phenom careers
    site, or None when the page is not one."""
    base = careers_url.split("?", 1)[0].rstrip("/")
    root = re.match(r"https?://[^/]+", base).group(0)
    for url in (base + "/search-results", root + "/search-results"):
        read = get(url)
        found = phenom_search(read["body"]) if read["status"] == "ok" else None
        if not found:
            continue
        data = found["data"]
        jobs = {}
        for j in data.get("jobs") or []:
            jobs[str(j.get("jobSeqNo") or j.get("jobId"))] = [
                j.get("title", ""), _place(j.get("cityState") or j.get("city")),
                j.get("category") or "", _iso(j.get("postedDate") or j.get("dateCreated"))]
        board = {"vendor": "phenom", "board": urlsplit_host(url), "open": int(
            found.get("totalHits") or 0), "complete": len(jobs) >= int(found.get("totalHits") or 0),
                 "guessed": False}
        return board, jobs, _agg(data, "city"), _agg(data, "state"), _agg(data, "category"), url
    return None


def urlsplit_host(url):
    return (re.match(r"https?://([^/]+)", url or "") or [None, ""])[1]


def _from_careers_pages(ctx):
    """When the site links no known board: open its careers pages and look
    there, for a board's address or a Phenom site."""
    links = (ctx["site"].get("signals") or {}).get("careers_links") or []
    for url in links[:2]:
        page = ctx["fetch"](url)
        if page["status"] != "ok":
            continue
        from .market_radar_site import ats_boards, parse_html
        from urllib.parse import urljoin
        hrefs = " ".join(urljoin(page["final_url"], h) for h, _ in parse_html(page["html"]).links)
        boards = ats_boards(hrefs + " " + page["html"][:2_000_000])
        if boards:
            return boards, None
        if PHENOM_MARK.search(page["html"]):
            return [], page["final_url"]
    return [], None


def read_jobs(ctx):
    get, get_json, now = ctx["get"], ctx["get_json"], ctx["now"]
    boards = list(((ctx["site"].get("signals") or {}).get("ats")) or [])
    phenom_url = None
    if not boards:
        boards, phenom_url = _from_careers_pages(ctx)
    if not boards and not phenom_url:
        guess = guess_greenhouse(get_json, ctx["entity"])
        if guess:
            boards = [guess]
    if not boards and not phenom_url:
        careers = (ctx["site"].get("signals") or {}).get("careers_links") or []
        note = ("careers pages found (%s), but no jobs board we can read" % careers[0]
                if careers else "no public jobs board linked from the site")
        return {"status": "none", "note": note, "payload": None, "items": 0, "complete": False}
    jobs, read_boards, notes = {}, [], []
    places, regions, categories, places_complete = {}, {}, {}, True
    if phenom_url:
        got = read_phenom(get, phenom_url, now)
        if got is None:
            notes.append("Phenom careers site found but its search page could not be read")
        else:
            board, rows, places, regions, categories, _url = got
            jobs.update(("phenom:%s" % k, v) for k, v in rows.items())
            read_boards.append(board)
    for b in boards[:3]:
        vendor, slug = b["vendor"], b.get("board")
        if vendor in UNREADABLE:
            notes.append("%s board found but it has no public listing" % vendor)
            continue
        if vendor == "workday":
            got = _workday(get, b.get("url") or "", now)
        elif vendor == "personio":
            got = _personio(get, slug, now)
        elif vendor in READERS and slug:
            got = READERS[vendor](get_json, slug, now)
        else:
            notes.append("%s board found but not readable" % vendor)
            continue
        found, total, complete, note = got
        if found is None:
            notes.append("%s/%s: %s" % (vendor, slug, note))
            continue
        for jid, row in found.items():
            jobs["%s:%s" % (vendor, jid)] = row
            if row[1]:
                places[row[1]] = places.get(row[1], 0) + 1
        places_complete = places_complete and complete
        read_boards.append({"vendor": vendor, "board": slug, "open": total,
                            "complete": complete, "guessed": bool(b.get("guessed"))})
        if note:
            notes.append("%s: %s" % (vendor, note))
    if not read_boards:
        return {"status": "failed", "note": "; ".join(notes) or "no board answered",
                "payload": None, "items": 0, "complete": False}
    open_total = sum(int(b["open"] or 0) for b in read_boards)
    by_function, senior, recent = {}, [], 0
    for jid, (title, place, team, posted) in jobs.items():
        f = function_of(title)
        by_function[f] = by_function.get(f, 0) + 1
        if SENIOR.search(title or ""):
            senior.append(title)
        if posted and (now - datetime.strptime(posted, "%Y-%m-%d").replace(
                tzinfo=timezone.utc)).days <= 30:
            recent += 1
    complete = all(b["complete"] for b in read_boards)
    payload = {"boards": read_boards, "open": open_total, "jobs": jobs, "complete": complete,
               "by_function": dict(sorted(by_function.items(), key=lambda kv: -kv[1])),
               "places": dict(sorted(places.items())), "regions": dict(sorted(regions.items())),
               "categories": dict(sorted(categories.items(), key=lambda kv: -kv[1])),
               "places_complete": places_complete,
               "senior_open": sorted(senior)[:20]}
    # "Posted in the last 30 days" is said in the note, not stored: it changes
    # as days pass, and the snapshot must only change when the jobs do.
    note = "%d open roles on %s" % (open_total, ", ".join(
        "%s%s" % (b["vendor"], " (guessed board)" if b["guessed"] else "") for b in read_boards))
    if places:
        note += " in %d places" % len(places)
    if recent:
        note += ", %d posted in the last 30 days" % recent
    if not complete:
        note += ("; roles are counted by place but not read one by one" if places_complete
                 else "; only part of the list was read, so role-level changes are not reported")
    if notes:
        note += "; " + "; ".join(notes)
    return {"status": "ok" if open_total else "empty", "note": note, "payload": payload,
            "items": len(jobs), "complete": complete}


def _place_key(place):
    return " ".join(re.sub(r"[^\w\s]", " ", (place or "").lower()).split())


def compare_jobs(prev, cur, now):
    day = now.strftime("%Y-%m-%d")
    a, b = int(prev.get("open") or 0), int(cur.get("open") or 0)
    events = []
    if b - a >= max(5, 0.25 * a):
        events.append({"key": "hiring+:%s" % day, "type": "hiring_surge",
                       "title": "Open roles up from %d to %d" % (a, b), "status": "unknown",
                       "date": day, "summary": _function_shift(prev, cur), "url": None,
                       "location": None})
    elif a - b >= max(5, 0.30 * a):
        events.append({"key": "hiring-:%s" % day, "type": "hiring_slowdown",
                       "title": "Open roles down from %d to %d" % (a, b), "status": "unknown",
                       "date": day, "summary": _function_shift(prev, cur), "url": None,
                       "location": None})
    # New places: from the place counts, which a Phenom site gives in full
    # even when its roles are not read one by one.
    if prev.get("places_complete") and cur.get("places_complete"):
        for level, word in (("regions", "region"), ("places", "place")):
            old = {_place_key(k) for k in (prev.get(level) or {})}
            for place, n in sorted((cur.get(level) or {}).items()):
                pk = _place_key(place)
                if pk and pk not in old and not REMOTE.search(place):
                    events.append({
                        "key": "job%s:%s" % (word, pk), "type": "new_job_location",
                        "title": "Hiring in a new %s: %s (%d role%s)" % (
                            word, place, n, "" if n == 1 else "s"),
                        "status": "unknown", "date": day,
                        "summary": "First open roles there since we started reading this board.",
                        "url": None, "location": {"label": place}})
    if prev.get("complete") and cur.get("complete"):
        old_jobs = prev.get("jobs") or {}
        for k, (title, place, team, posted) in sorted((cur.get("jobs") or {}).items()):
            if k not in old_jobs and SENIOR.search(title or ""):
                events.append({"key": "senior:%s" % k, "type": "senior_hire_search",
                               "title": "Senior role opened: %s%s" % (
                                   title, (" (%s)" % place) if place else ""),
                               "status": "unknown", "date": posted or day, "summary": None,
                               "url": None, "location": None})
    return events[:30]


def _function_shift(prev, cur):
    a, b = prev.get("by_function") or {}, cur.get("by_function") or {}
    moves = sorted(((b.get(f, 0) - a.get(f, 0), f) for f in set(a) | set(b)), reverse=True)
    parts = ["%s %+d" % (f.replace("_", " "), d) for d, f in moves if d][:5]
    return ("By function: " + ", ".join(parts)) if parts else None
