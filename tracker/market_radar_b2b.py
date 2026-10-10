"""Market Radar, Phase 10: detectors for B2B companies and manufacturers.

Each follows the detector contract in tracker/market_radar_collect.py:
read(ctx) -> {"status", "note", "payload", "items", "complete"}, then
compare(prev, cur, now) and baseline(cur, now) -> events. None of them needs
the competitor's own website to answer (big B2B sites often refuse a
datacenter address), except the UK registry, which takes the company number
from the site's footer.

  * subdomains: new host names in public certificate logs ("mcp.gorgias.com"
    first certified 2026-05-07). crt.sh's public Postgres keeps every
    certificate ever logged, so a first read can tell a new name from an old
    one; its web page answered 502 on every try on 2026-10-10. Cert Spotter
    is the fallback: it lists only certificates still valid, so a name
    renewed every 90 days always looks recent there, and a first read from
    it reports nothing.
  * filings: SEC EDGAR for US-listed competitors. The company is found by
    its own domain in its own annual and quarterly reports (full-text
    search), not by name: Allbirds files as "Smartbird, Inc." now and is no
    longer in SEC's ticker list. Material 8-K items, listings and
    delistings become events; insider-trading forms and routine reports do
    not.
  * headcount: Apollo's estimate and its 6- and 12-month growth. Spends one
    Apollo credit per company per read, so it is read at most monthly.
  * registry: the UK company register (Companies House) for UK companies
    whose site prints a company number: new directors, share allotments
    (equity raised), charges (secured lending), name changes, insolvency.
    Needs COMPANIES_HOUSE_API_KEY (a free key).

India has no free official filings API (the MCA portal has none and the
exchanges' announcement feeds refuse automated reads), so Indian
competitors rely on news and their own sites.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from .market_radar_detectors import _capped, _date, _event, _fail, _h

logger = logging.getLogger(__name__)

BASELINE_DAYS = 60


def _days_ago(day, now, days):
    try:
        d = datetime.fromisoformat(str(day)[:10]).date()
    except ValueError:
        return False
    return now.date() - timedelta(days=days) <= d <= now.date() + timedelta(days=1)


# == certificate-log subdomains =======================================================

CRT = {"host": "crt.sh", "port": 5432, "user": "guest", "dbname": "certwatch",
       "connect_timeout": 15}
CRT_SQL = """
SELECT lower(ci.name_value), min(x509_notBefore(ci.certificate))
FROM certificate_and_identities ci
WHERE plainto_tsquery('certwatch', %s) @@ identities(ci.certificate)
  AND ci.name_value ILIKE %s AND ci.name_type = 'san:dNSName'
GROUP BY 1"""
CRT_TIMEOUT_S = 100
# crt.sh is a free public service with a small connection limit: never more
# than two of our queries at a time, from this process.
_crt_gate = threading.BoundedSemaphore(2)
# Its guest pool is often full ("no more connections allowed
# (max_client_conn)" from Railway, 2026-10-10): a refused connection is
# asked again after these pauses before Cert Spotter is used.
CRT_RETRY_S = (4, 12)
CERTSPOTTER = "https://api.certspotter.com/v1/issuances?domain=%s&include_subdomains=true" \
              "&expand=dns_names"
CERTSPOTTER_PAGES = 5
MAX_NAMES = 3000
MAX_NEW_EVENTS = 8

NOISE_LABEL = re.compile(
    r"^(www\d*|mail\d*|smtp\d*|imap|pop3?|webmail|autodiscover|autoconfig|cpanel|whm|webdisk|"
    r"cpcalendars|cpcontacts|mx\d*|ns\d*|dns\d*|ftp|sftp|localhost|email|em\d+|e\.\w+|click|"
    r"links?|track(ing)?|bounces?|url\d+|o\d+|s\d+|m\d+|_dmarc|_domainkey|lyncdiscover|sip|"
    r"enterpriseregistration|enterpriseenrollment|msoid|owa|vpn\d*|remote)$")
TEST_LABEL = re.compile(r"(^|[-_.])(dev|develop|stg|stage|staging|test|testing|qa|uat|sandbox|"
                        r"preprod|pre-prod|demo|tmp|temp|old|backup|local)\d*($|[-_.])")
HASHY = re.compile(r"^[0-9a-f]{8,}$|^[a-z0-9]{24,}$|\d{4,}")


def _names(raw, domain):
    """Host names under `domain`, lower-cased, without wildcards, the
    domain itself, its www, or anything not under it."""
    out = {}
    for name, first in raw:
        n = str(name or "").strip().lower().rstrip(".")
        if n.startswith("*."):
            n = n[2:]
        if not n.endswith("." + domain) or n in (domain, "www." + domain):
            continue
        if not re.fullmatch(r"[a-z0-9._-]+", n):
            continue
        d = _date(first)
        if d and (n not in out or d < out[n]):
            out[n] = d
    return out


def worth_reporting(name, domain):
    """A host name that could say something about the business: not mail,
    DNS or tracking plumbing, not a test or staging host, not a generated
    tenant or machine name, and at most two levels below the domain
    (customer-a.app.example.com is one tenant of a product, not news)."""
    labels = name[:-len(domain) - 1].split(".")
    if len(labels) > 2:
        return False
    if any(NOISE_LABEL.match(l) or HASHY.search(l) or len(l) > 40 for l in labels):
        return False
    return not TEST_LABEL.search(".".join(labels))


def _connect_with_retry(connect, sleep):
    for pause in CRT_RETRY_S + (None,):
        try:
            return connect(**CRT)
        except Exception as e:
            if pause is None or "max_client_conn" not in str(e) and \
                    "too many" not in str(e).lower():
                raise
            sleep(pause)


def crtsh_names(domain, *, connect=None, sleep=None):
    """{name: first certified (YYYY-MM-DD)} from crt.sh's full history.
    Raises when crt.sh cannot answer."""
    if connect is None:
        import psycopg2
        connect = psycopg2.connect
    if sleep is None:
        import time
        sleep = time.sleep
    with _crt_gate:
        conn = _connect_with_retry(connect, sleep)
        try:
            conn.autocommit = True
            cur = conn.cursor()
            cur.execute("SET statement_timeout = '%ds'" % CRT_TIMEOUT_S)
            cur.execute(CRT_SQL, (domain, "%." + domain))
            return _names(cur.fetchall(), domain)
        finally:
            conn.close()


def certspotter_names(domain, get):
    """{name: earliest not-before among certificates still valid} from Cert
    Spotter, and a note. Raises when it does not answer."""
    raw, after, pages = [], None, 0
    while pages < CERTSPOTTER_PAGES:
        url = CERTSPOTTER % quote(domain) + ("&after=%s" % quote(after) if after else "")
        read = get(url, accept="application/json")
        if read["status"] != "ok":
            if pages == 0:
                raise RuntimeError("Cert Spotter: %s" % (read["note"] or read["status"]))
            break
        try:
            rows = json.loads(read["body"])
        except ValueError:
            raise RuntimeError("Cert Spotter answered something that is not JSON")
        pages += 1
        if not rows:
            break
        for r in rows:
            for n in r.get("dns_names") or []:
                raw.append((n, r.get("not_before")))
        after = str(rows[-1].get("id") or "")
        if len(rows) < 100 or not after:
            break
    return _names(raw, domain), pages


def read_subdomains(ctx, *, connect=None, sleep=None):
    connect = connect or (ctx.get("hooks") or {}).get("crt_connect")
    sleep = sleep or (ctx.get("hooks") or {}).get("sleep")
    domain = ctx["entity"]["domain"]
    now = ctx["now"]
    tried = []
    try:
        names = crtsh_names(domain, connect=connect, sleep=sleep)
        source, history = "crt.sh", True
    except Exception as e:
        tried.append("crt.sh did not answer (%s)" % type(e).__name__)
        try:
            names, _pages = certspotter_names(domain, ctx["get"])
            source, history = "Cert Spotter", False
        except Exception as e2:
            tried.append(str(e2)[:120])
            return _fail("failed", "; ".join(tried))
    complete = len(names) <= MAX_NAMES
    if not complete:
        names = dict(sorted(names.items(), key=lambda kv: kv[1], reverse=True)[:MAX_NAMES])
    shown = sum(1 for n in names if worth_reporting(n, domain))
    note = "%d host names in certificate logs (%s%s), %d worth watching" % (
        len(names), source, "" if history else ", current certificates only", shown)
    if tried:
        note += "; " + "; ".join(tried)
    payload = {"domain": domain, "source": source, "history": history, "names": names,
               "read_at": now.date().isoformat(), "complete": complete}
    return {"status": "ok" if names else "empty", "note": note, "payload": payload,
            "items": len(names), "complete": complete}


def _subdomain_events(domain, fresh, day):
    events = [_event("sub+:" + n, "new_subdomain", "New subdomain: " + n, status="announced",
                     date=first, url="https://" + n,
                     summary="First certified %s; a new host name often comes before a new "
                             "product, region or tool is announced." % first)
              for n, first in sorted(fresh.items(), key=lambda kv: kv[1], reverse=True)]
    if len(events) > MAX_NEW_EVENTS:
        rest = len(events) - MAX_NEW_EVENTS
        events = events[:MAX_NEW_EVENTS] + [_event(
            "sub+many:%s" % day, "new_subdomain", "%d more new subdomains" % rest, date=day)]
    return events


def baseline_subdomains(cur, now):
    """On a first read, names first certified in the last 60 days, and only
    from a full history: a current-certificates list cannot tell new from
    renewed."""
    domain = cur.get("domain")
    if not cur.get("history") or not domain:
        return []
    fresh = {n: d for n, d in (cur.get("names") or {}).items()
             if _days_ago(d, now, BASELINE_DAYS) and worth_reporting(n, domain)}
    return _subdomain_events(domain, fresh, now.date().isoformat())


def compare_subdomains(prev, cur, now):
    """Names not in the previous read and first certified since it (minus
    two days of log delay). The date test keeps a switch between the two
    logs from reporting old names as new."""
    domain = cur.get("domain")
    if not domain:
        return []
    before = set((prev or {}).get("names") or {})
    since = (datetime.fromisoformat(prev["read_at"]) - timedelta(days=2)).date().isoformat() \
        if (prev or {}).get("read_at") else None
    fresh = {n: d for n, d in (cur.get("names") or {}).items()
             if n not in before and (since is None or d >= since) and worth_reporting(n, domain)}
    return _subdomain_events(domain, fresh, now.date().isoformat())


# == SEC EDGAR ========================================================================

SEC_UA = os.environ.get("SEC_USER_AGENT") or \
    "Position2 Market Radar (+https://intelligence.position2.com)"
FTS = "https://efts.sec.gov/LATEST/search-index?q=%s&forms=10-K,10-Q,20-F,40-F,S-1,F-1"
SUBMISSIONS = "https://data.sec.gov/submissions/CIK%010d.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/%d/%s/%s"
FILING_DAYS = 400
MAX_DOCS = 5
NAME_NOISE = {"inc", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "llc",
              "holdings", "holding", "group", "the", "sa", "ag", "nv", "se", "lp", "of", "and"}
ITEMS = {
    "1.01": ("partnership", "signed a material agreement"),
    "1.03": ("legal_regulatory", "filed for bankruptcy or receivership"),
    "2.01": ("acquisition", "completed an acquisition or sale of assets"),
    "2.02": ("financial_results", "reported results"),
    "2.03": ("funding", "took on a material financial obligation"),
    "2.05": ("layoffs", "announced exit or restructuring costs"),
    "3.01": ("legal_regulatory", "received a stock-exchange delisting notice"),
    "5.01": ("acquisition", "reported a change in control"),
    "5.02": ("leadership_change", "reported a director or officer change"),
}
FORMS = {
    "S-1": ("funding", "filed to sell shares to the public (S-1)"),
    "F-1": ("funding", "filed to sell shares to the public (F-1)"),
    "S-4": ("acquisition", "registered shares for a merger or acquisition (S-4)"),
    "15-12B": ("other_move", "ended its SEC registration (15-12B)"),
    "15-12G": ("other_move", "ended its SEC registration (15-12G)"),
    "25-NSE": ("other_move", "is being removed from its stock exchange (25-NSE)"),
}
TYPE_RANK = ["acquisition", "layoffs", "funding", "leadership_change", "legal_regulatory",
             "partnership", "financial_results", "other_move"]


def _sec_get(ctx, url, accept="application/json"):
    return ctx["get"](url, accept=accept, headers={"User-Agent": SEC_UA})


def _tokens(text):
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if t not in NAME_NOISE and len(t) > 1}


def find_cik(ctx):
    """(cik, display name, note). The company whose own annual or quarterly
    reports name its domain most often, and whose name (or a former name)
    shares a word with the competitor's name or domain: another company's
    report can mention nike.com, but it is not Nike."""
    entity = ctx["entity"]
    domain = entity["domain"]
    read = _sec_get(ctx, FTS % quote('"%s"' % domain))
    if read["status"] != "ok":
        raise RuntimeError("EDGAR search: %s" % (read["note"] or read["status"]))
    try:
        hits = (json.loads(read["body"]).get("hits") or {}).get("hits") or []
    except ValueError:
        raise RuntimeError("EDGAR search answered something that is not JSON")
    counts, names = {}, {}
    for h in hits:
        s = h.get("_source") or {}
        for i, cik in enumerate(s.get("ciks") or []):
            counts[cik] = counts.get(cik, 0) + 1
            dn = (s.get("display_names") or [""])[i] if i < len(s.get("display_names") or []) \
                else ""
            names.setdefault(cik, dn)
    if not counts:
        return None, None, "no SEC filing names %s, so it is not a US-listed company" % domain
    want = _tokens(entity.get("name")) | _tokens(domain.split(".")[0])
    for cik, _n in sorted(counts.items(), key=lambda kv: -kv[1]):
        display = re.sub(r"\s*\(CIK \d+\)\s*$", "", names.get(cik) or "").strip()
        if _tokens(display) & want:
            return int(cik), display, None
        sub = _sec_get(ctx, SUBMISSIONS % int(cik))
        if sub["status"] == "ok":
            try:
                d = json.loads(sub["body"])
            except ValueError:
                continue
            former = " ".join(f.get("name", "") for f in d.get("formerNames") or [])
            if _tokens(former) & want or domain in (d.get("website") or "").lower():
                return int(cik), display, None
    return None, None, ("SEC filings name %s, but none filed by a company of that name"
                        % domain)


def _filings(d, now):
    r = (d.get("filings") or {}).get("recent") or {}
    since = (now - timedelta(days=FILING_DAYS)).date().isoformat()
    out = []
    for i, form in enumerate(r.get("form") or []):
        day = (r.get("filingDate") or [""])[i] if i < len(r.get("filingDate") or []) else ""
        if day < since:
            continue
        items = [x.strip() for x in str((r.get("items") or [""] * (i + 1))[i] or "").split(",")
                 if x.strip()]
        if form == "8-K" and any(x in ITEMS for x in items) or form in FORMS:
            out.append({"form": form, "date": day, "items": items,
                        "acc": r["accessionNumber"][i], "doc": r["primaryDocument"][i]})
    return out


def read_filings(ctx):
    prev = ctx["prev"].get("filings") or {}
    try:
        if prev.get("cik"):
            cik, name, why = prev["cik"], prev.get("name"), None
        else:
            cik, name, why = find_cik(ctx)
        if cik is None:
            return {"status": "none", "note": why, "payload": None, "items": 0,
                    "complete": True}
        read = _sec_get(ctx, SUBMISSIONS % cik)
        if read["status"] != "ok":
            return _fail("failed", "EDGAR filings list: %s" % (read["note"] or read["status"]))
        d = json.loads(read["body"])
    except Exception as e:
        return _fail("failed", str(e)[:200])
    filings = _filings(d, ctx["now"])
    payload = {"cik": cik, "name": d.get("name") or name, "tickers": d.get("tickers") or [],
               "filings": filings}
    note = "%s (CIK %d%s): %d material filings in the last %d days" % (
        payload["name"], cik, ", " + "/".join(payload["tickers"]) if payload["tickers"] else "",
        len(filings), FILING_DAYS)
    # The words of the newest material filings, so an event says what was
    # agreed or who left, not just which item number was filed.
    for f in [f for f in filings if f["acc"] not in {x["acc"] for x in prev.get("filings") or []}
              ][:MAX_DOCS]:
        f["text"] = filing_excerpt(ctx, cik, f)
    return {"status": "ok" if filings else "empty", "note": note, "payload": payload,
            "items": len(filings), "complete": True}


def filing_excerpt(ctx, cik, f):
    """The first sentences under the filing's first material item, without
    markup, or None."""
    read = _sec_get(ctx, ARCHIVE % (cik, f["acc"].replace("-", ""), f["doc"]), accept="text/html")
    if read["status"] != "ok":
        return None
    text = re.sub(r"<[^>]+>", " ", read["body"])
    text = re.sub(r"&#160;|&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = " ".join(text.split())
    for item in f["items"]:
        if item in ITEMS:
            m = re.search(r"Item\s*%s\.?\s*(.{40,700}?)(?=Item\s*\d\.\d\d|SIGNATURE|$)"
                          % re.escape(item), text, re.I)
            if m:
                return m.group(1).strip()[:400]
    return text[:300] if f["form"] in FORMS else None


def _filing_event(f, payload):
    if f["form"] in FORMS:
        typ, what = FORMS[f["form"]]
    else:
        found = [ITEMS[x] for x in f["items"] if x in ITEMS]
        found.sort(key=lambda tw: TYPE_RANK.index(tw[0]))
        typ, what = found[0][0], "; ".join(dict.fromkeys(w for _t, w in found))
    name = payload.get("name") or "The company"
    url = "https://www.sec.gov/Archives/edgar/data/%d/%s/%s" % (
        payload["cik"], f["acc"].replace("-", ""), f["doc"])
    return _event("sec:" + f["acc"], typ, "%s %s (SEC %s)" % (name, what, f["form"]),
                  status="completed" if typ in ("acquisition", "leadership_change") else
                  "announced", date=f["date"], url=url, summary=f.get("text"))


def baseline_filings(cur, now):
    return [_filing_event(f, cur) for f in cur.get("filings") or []
            if _days_ago(f["date"], now, 45)]


def compare_filings(prev, cur, now):
    if prev.get("cik") != cur.get("cik"):
        return baseline_filings(cur, now)
    seen = {f["acc"] for f in prev.get("filings") or []}
    return [_filing_event(f, cur) for f in cur.get("filings") or [] if f["acc"] not in seen]


# == headcount (Apollo) ===============================================================

HEADCOUNT_UP, HEADCOUNT_DOWN = 0.15, -0.08       # six-month growth worth a line
CHANGE_SHARE, CHANGE_MIN = 0.10, 20              # between two of our reads


def _apollo_key():
    return os.environ.get("APOLLO_API_KEY", "")


def read_headcount(ctx, *, enrich=None):
    """Apollo's employee estimate and growth. One credit, booked in the
    ledger as an Apollo call."""
    enrich = enrich or (ctx.get("hooks") or {}).get("apollo_enrich")
    key = _apollo_key()
    if not key and enrich is None:
        return {"status": "skipped", "note": "Apollo is not set up on this server "
                "(APOLLO_API_KEY)", "payload": None, "items": 0}
    domain = ctx["entity"]["domain"]
    run_id = ctx.get("run_id")
    if enrich is None:
        from . import apollo_client

        def enrich(d):
            return apollo_client._post("organizations/enrich", {"domain": d}, key)
    try:
        if run_id:
            from . import market_radar_ledger as ledger
            with ledger.track(run_id, "headcount", "apollo", units=1) as call:
                data = enrich(domain)
                call.record(units=1)
        else:
            data = enrich(domain)
    except Exception as e:
        return _fail("failed", "Apollo did not answer (%s)" % type(e).__name__)
    org = (data or {}).get("organization") or {}
    n = org.get("estimated_num_employees")
    if not isinstance(n, (int, float)) or n <= 0:
        return {"status": "none", "note": "Apollo has no employee estimate for %s" % domain,
                "payload": None, "items": 0, "complete": True}

    def g(k):
        v = org.get(k)
        return round(float(v), 4) if isinstance(v, (int, float)) else None

    payload = {"employees": int(n), "growth_6m": g("organization_headcount_six_month_growth"),
               "growth_12m": g("organization_headcount_twelve_month_growth"),
               "read": ctx["now"].date().isoformat()}
    bits = ["about %d employees (Apollo estimate)" % payload["employees"]]
    if payload["growth_6m"] is not None:
        bits.append("%+.0f%% in six months" % (100 * payload["growth_6m"]))
    return {"status": "ok", "note": ", ".join(bits), "payload": payload, "items": 1,
            "complete": True}


def _growth_event(cur, day):
    g6 = cur.get("growth_6m")
    if g6 is None:
        return []
    if g6 >= HEADCOUNT_UP:
        typ, word = "hiring_surge", "up"
    elif g6 <= HEADCOUNT_DOWN:
        typ, word = "hiring_slowdown", "down"
    else:
        return []
    return [_event("growth6:%s:%s" % (word, day[:7]), typ,
                   "Headcount %s %.0f%% in six months, to about %d (Apollo estimate)" % (
                       word, abs(100 * g6), cur["employees"]), status="completed", date=day)]


def baseline_headcount(cur, now):
    return _growth_event(cur, now.date().isoformat())


def compare_headcount(prev, cur, now):
    a, b = prev.get("employees"), cur.get("employees")
    day = now.date().isoformat()
    if a and b and abs(b - a) >= CHANGE_MIN and abs(b - a) >= CHANGE_SHARE * a:
        word = "up" if b > a else "down"
        return [_event("hc:%s:%s" % (word, day), "hiring_surge" if b > a else "hiring_slowdown",
                       "Headcount %s from about %d to %d since %s (Apollo estimate)" % (
                           word, a, b, prev.get("read") or "the last read"),
                       status="completed", date=day)]
    # Six-month growth crossing a threshold is news once, when it crosses.
    return [] if _growth_event(prev, day) else _growth_event(cur, day)


# == UK company register (Companies House) =============================================

CH = "https://api.company-information.service.gov.uk"
CH_NUMBER = re.compile(
    r"(?:company|registration|registered|reg\.?)\s*(?:no\.?|number|num\.?|nr\.?)?\s*[:#.]?\s*"
    r"((?:SC|NI|OC|SO|NC|R0|LP|NF|FC)?\s?\d{6,8})\b", re.I)
UK_HINT = re.compile(r"registered in (england|scotland|wales|northern ireland)|companies house|"
                     r"registered office", re.I)
CH_TYPES = {
    "AP01": ("leadership_change", "appointed a director"),
    "AP02": ("leadership_change", "appointed a corporate director"),
    "TM01": ("leadership_change", "a director left"),
    "SH01": ("funding", "allotted new shares (new equity)"),
    "MR01": ("funding", "registered a charge (secured lending)"),
    "NM01": ("rebrand", "changed its name"),
    "CERTNM": ("rebrand", "changed its name"),
    "AA01": None, "AD01": None,
}
CH_CATEGORIES = {"insolvency": ("legal_regulatory", "an insolvency filing"),
                 "liquidation": ("legal_regulatory", "a liquidation filing")}


def _ch_key():
    return os.environ.get("COMPANIES_HOUSE_API_KEY", "")


def company_number(rs):
    """The UK company number a site prints (footers must, by law), or None."""
    texts = " ".join(str(t) for t in ((rs or {}).get("texts") or {}).values())
    if not UK_HINT.search(texts):
        return None
    m = CH_NUMBER.search(texts)
    if not m:
        return None
    n = m.group(1).replace(" ", "").upper()
    prefix = re.match(r"[A-Z]+", n)
    digits = n[len(prefix.group(0)):] if prefix else n
    return (prefix.group(0) if prefix else "") + digits.zfill(8 - (len(prefix.group(0))
                                                                   if prefix else 0))


def read_registry(ctx):
    rs = ctx.get("site") or {}
    entity = ctx["entity"]
    prev = ctx["prev"].get("registry") or {}
    number = prev.get("number") or company_number(rs)
    if not number:
        return {"status": "none", "note": "no UK company number on its site", "payload": None,
                "items": 0, "complete": True}
    key = _ch_key()
    if not key:
        return {"status": "skipped", "note": "UK company %s: the Companies House key is not "
                "set on this server (COMPANIES_HOUSE_API_KEY)" % number, "payload": None,
                "items": 0}
    auth = {"Authorization": "Basic " + base64.b64encode((key + ":").encode()).decode()}
    prof = ctx["get"]("%s/company/%s" % (CH, number), accept="application/json", headers=auth)
    if prof["status"] != "ok":
        return _fail("failed", "Companies House %s: %s" % (number, prof["note"] or
                                                           prof["status"]))
    hist = ctx["get"]("%s/company/%s/filing-history?items_per_page=60" % (CH, number),
                      accept="application/json", headers=auth)
    if hist["status"] != "ok":
        return _fail("failed", "Companies House filings for %s: %s" % (
            number, hist["note"] or hist["status"]))
    try:
        p, h = json.loads(prof["body"]), json.loads(hist["body"])
    except ValueError:
        return _fail("failed", "Companies House answered something that is not JSON")
    items = []
    for it in h.get("items") or []:
        if not isinstance(it, dict) or not it.get("transaction_id"):
            continue
        items.append({"id": it["transaction_id"], "date": _date(it.get("date")),
                      "type": it.get("type"), "category": it.get("category"),
                      "officer": ((it.get("description_values") or {}).get("officer_name")),
                      "name": ((it.get("description_values") or {}).get("company_name")
                               or (it.get("description_values") or {}).get("new_name"))})
    payload = {"number": number, "name": p.get("company_name"), "status": p.get("company_status"),
               "items": items}
    return {"status": "ok", "note": "%s, company %s (%s): %d filings read" % (
        p.get("company_name") or entity.get("name"), number, p.get("company_status") or "?",
        len(items)), "payload": payload, "items": len(items), "complete": True}


def _registry_event(it, payload):
    kind = CH_TYPES.get(it.get("type"))
    if kind is None:
        kind = CH_CATEGORIES.get(it.get("category"))
    if not kind:
        return None
    typ, what = kind
    who = payload.get("name") or "The company"
    title = "%s %s" % (who, what)
    if typ == "leadership_change" and it.get("officer"):
        title += ": %s" % it["officer"]
    if typ == "rebrand" and it.get("name"):
        title += " to %s" % it["name"]
    return _event("ch:" + it["id"], typ, title + " (Companies House)", status="completed",
                  date=it.get("date"),
                  url="https://find-and-update.company-information.service.gov.uk/company/%s/"
                      "filing-history" % payload["number"])


def baseline_registry(cur, now):
    return [e for e in (_registry_event(i, cur) for i in cur.get("items") or []
                        if _days_ago(i.get("date"), now, 45)) if e]


def compare_registry(prev, cur, now):
    seen = {i["id"] for i in prev.get("items") or []}
    out = [e for e in (_registry_event(i, cur) for i in cur.get("items") or []
                       if i["id"] not in seen) if e]
    if prev.get("status") and cur.get("status") and prev["status"] != cur["status"]:
        out.append(_event("ch-status:%s" % cur["status"], "legal_regulatory",
                          "%s is now listed as %s at Companies House (was %s)" % (
                              cur.get("name") or "The company", cur["status"], prev["status"]),
                          status="completed", date=now.date().isoformat()))
    return out
