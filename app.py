"""Platform hub server — Google Sign-In + multi-dashboard routing."""

import os
import json
import uuid
import logging
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from functools import wraps

from flask import (
    Flask, send_file, abort, jsonify,
    request, session, redirect, url_for,
    make_response, render_template,
)

log = logging.getLogger(__name__)

# Indian Standard Time = UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cst-dev-secret-do-not-use-in-prod-abc123xyz")
app.permanent_session_lifetime = timedelta(days=7)

@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403

# ── Google OAuth ────────────────────────────────────────────────────────────────
# Set GOOGLE_CLIENT_ID in Railway → Variables.
# Setup: console.cloud.google.com → APIs & Services → Credentials
#        → Create OAuth 2.0 Client ID → Web application
#        → Authorised JavaScript origins: https://intelligence.position2.com
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

# Anonymous Visitors Google Sheet
ANON_VISITORS_SHEET_ID = "1y5_ef9Df5v8PVuGs60DzzlzE1ZJLxwvB5MQsB6ug458"

# Google Sheet ID for login tracking (set LOGIN_LOG_SHEET_ID in Railway Variables).
# Create a new Google Sheet, share it with the service account email (Editor),
# then paste the sheet ID here (from its URL: /spreadsheets/d/<ID>/edit).
LOGIN_LOG_SHEET_ID = os.environ.get("LOGIN_LOG_SHEET_ID", "")
_SA_JSON = str(Path(__file__).parent / "service_account.json")

# ── Login logger ────────────────────────────────────────────────────────────────
def _parse_ua(ua: str) -> tuple[str, str, str, str]:
    """Return (browser_name, browser_version, os_name, device_type) from User-Agent."""
    ua = ua or ""
    # Device type
    if re.search(r"Mobile|Android|iPhone|iPod", ua, re.I):
        device = "Mobile"
    elif re.search(r"iPad|Tablet", ua, re.I):
        device = "Tablet"
    else:
        device = "Desktop"
    # OS
    if re.search(r"Windows NT", ua):
        os_name = "Windows"
    elif re.search(r"Mac OS X", ua):
        os_name = "macOS"
    elif re.search(r"Android", ua):
        os_name = "Android"
    elif re.search(r"iPhone|iPad", ua):
        os_name = "iOS"
    elif re.search(r"Linux", ua):
        os_name = "Linux"
    else:
        os_name = "Unknown"
    # Browser (order matters — Chrome must come before Safari)
    m = re.search(r"Edg(?:e)?/([\d.]+)", ua)
    if m: return "Edge", m.group(1), os_name, device
    m = re.search(r"OPR/([\d.]+)", ua)
    if m: return "Opera", m.group(1), os_name, device
    m = re.search(r"Firefox/([\d.]+)", ua)
    if m: return "Firefox", m.group(1), os_name, device
    m = re.search(r"Chrome/([\d.]+)", ua)
    if m: return "Chrome", m.group(1), os_name, device
    m = re.search(r"Version/([\d.]+).*Safari", ua)
    if m: return "Safari", m.group(1), os_name, device
    return "Unknown", "", os_name, device


def _log_login_to_sheet(user: dict) -> None:
    """Append one login row to the tracking Google Sheet. Fails silently."""
    if not LOGIN_LOG_SHEET_ID:
        return
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        import json as _json

        # Prefer env var (Railway) over local file
        sa_json_str = os.environ.get("GOOGLE_SA_JSON", "")
        if sa_json_str:
            sa_info = _json.loads(sa_json_str)
            creds = service_account.Credentials.from_service_account_info(
                sa_info,
                scopes=["https://www.googleapis.com/auth/spreadsheets"],
            )
        elif Path(_SA_JSON).exists():
            creds = service_account.Credentials.from_service_account_file(
                _SA_JSON,
                scopes=["https://www.googleapis.com/auth/spreadsheets"],
            )
        else:
            log.warning("Login sheet: no credentials found (set GOOGLE_SA_JSON env var)")
            return
        svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

        now = datetime.now(IST)
        ua_raw  = request.headers.get("User-Agent", "")
        browser, bv, os_name, device = _parse_ua(ua_raw)
        ip = (request.headers.get("X-Forwarded-For", "") or
              request.headers.get("X-Real-IP", "") or
              request.remote_addr or "")
        ip = ip.split(",")[0].strip()  # X-Forwarded-For can be a list

        # 20 columns — add header row automatically on first write
        row = [
            now.strftime("%Y-%m-%d %H:%M:%S IST"),   # 1  Timestamp
            now.strftime("%Y-%m-%d"),                  # 2  Date
            now.strftime("%H:%M:%S"),                  # 3  Time (IST)
            now.strftime("%A"),                         # 4  Day of Week
            now.strftime("%H"),                         # 5  Hour (0-23, IST)
            user.get("email", ""),                      # 6  Email
            user.get("name", ""),                       # 7  Full Name
            user.get("given_name", ""),                 # 8  First Name
            user.get("picture", ""),                    # 9  Profile Picture URL
            ip,                                         # 10 IP Address
            browser,                                    # 11 Browser
            bv,                                         # 12 Browser Version
            os_name,                                    # 13 Operating System
            device,                                     # 14 Device Type
            ua_raw[:200],                               # 15 User Agent (truncated)
            request.referrer or "direct",               # 16 Referrer
            "/hub",                                     # 17 Landing Page
            "Google OAuth",                             # 18 Auth Method
            str(uuid.uuid4())[:8],                      # 19 Session ID (short)
            "intelligence.position2.com",               # 20 Platform
        ]

        # Check if header row exists; if sheet is empty, prepend it
        result = svc.spreadsheets().values().get(
            spreadsheetId=LOGIN_LOG_SHEET_ID, range="A1:A1"
        ).execute()
        if not result.get("values"):
            header = [[
                "Timestamp (IST)", "Date", "Time (IST)", "Day of Week", "Hour (IST)",
                "Email", "Full Name", "First Name", "Profile Picture",
                "IP Address", "Browser", "Browser Version", "OS", "Device",
                "User Agent", "Referrer", "Landing Page", "Auth Method",
                "Session ID", "Platform",
            ]]
            svc.spreadsheets().values().append(
                spreadsheetId=LOGIN_LOG_SHEET_ID,
                range="A1",
                valueInputOption="RAW",
                body={"values": header},
            ).execute()

        svc.spreadsheets().values().append(
            spreadsheetId=LOGIN_LOG_SHEET_ID,
            range="A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()

    except Exception as e:
        log.warning("Login sheet log failed: %s", e)


# ── Account registry ────────────────────────────────────────────────────────────
ACCOUNTS = {
    "healthcare": {
        "name":        "Healthcare",
        "description": "1,251 healthcare companies tracked for funding, C-suite moves, M&A, and news signals.",
        "icon":        "🏥",
        "accent":      "#3b82f6",
        "dashboard":   Path(__file__).parent / "reports" / "dashboard.html",
    },
    "csg": {
        "name":        "CSG",
        "description": "CSG company intelligence — funding rounds, leadership changes, and market signals.",
        "icon":        "📡",
        "accent":      "#8b5cf6",
        "dashboard":   Path(__file__).parent / "reports" / "dashboard_csg.html",
    },
}

# ── Auth helpers ────────────────────────────────────────────────────────────────
ADMIN_EMAILS = {"krishna.ladha@position2.com", "sudheer.d@position2.com"}

def _get_user():
    """Return current user dict or None."""
    return session.get("google_user")

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _get_user():
            return redirect(url_for("login_page") + "?error=Your+session+expired.+Please+sign+in+again.")
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _get_user()
        if not user:
            return redirect(url_for("login_page") + "?error=Your+session+expired.+Please+sign+in+again.")
        if user.get("email", "").lower() not in ADMIN_EMAILS:
            abort(403)
        return f(*args, **kwargs)
    return decorated

# ── Google Sign-In ──────────────────────────────────────────────────────────────
@app.route("/auth/google", methods=["POST"])
def auth_google():
    credential = (request.json or {}).get("credential", "")
    if not credential:
        return jsonify({"success": False, "error": "No credential"}), 400

    if not GOOGLE_CLIENT_ID:
        # Dev mode: decode without verification (localhost only)
        import base64, json as _j
        try:
            pad = credential.split(".")[1]
            pad += "=" * (-len(pad) % 4)
            idinfo = _j.loads(base64.urlsafe_b64decode(pad))
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 401
    else:
        try:
            from google.oauth2 import id_token
            from google.auth.transport import requests as greq
            idinfo = id_token.verify_oauth2_token(credential, greq.Request(), GOOGLE_CLIENT_ID)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 401

    session["google_user"] = {
        "email":      idinfo.get("email", ""),
        "name":       idinfo.get("name", ""),
        "given_name": idinfo.get("given_name", ""),
        "picture":    idinfo.get("picture", ""),
    }
    session.permanent = True
    _log_login_to_sheet(session["google_user"])   # fire-and-forget, fails silently
    return jsonify({"success": True, "redirect": "/hub"})


# ── Core routes ─────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return redirect(url_for("hub") if _get_user() else url_for("login_page"))

@app.route("/login")
def login_page():
    if _get_user():
        return redirect(url_for("hub"))
    return render_template("login.html", google_client_id=GOOGLE_CLIENT_ID,
                           error=request.args.get("error", ""))

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

# ── Hub pages ───────────────────────────────────────────────────────────────────
@app.route("/hub")
@login_required
def hub():
    return render_template("hub.html", user=_get_user())

@app.route("/ppc")
@login_required
def ppc():
    return render_template("ppc.html", user=_get_user())

@app.route("/seo")
@login_required
def seo():
    return render_template("seo.html", user=_get_user())

# ── Embedded dashboards ─────────────────────────────────────────────────────────
AD_INTELLIGENCE_URL = "https://ad-intelligence-production-be71.up.railway.app"
SERP_RESEARCHER_URL  = "https://serp-content-researcher-production-a947.up.railway.app"

@app.route("/ppc/ad-intelligence")
@login_required
def ad_intelligence():
    return render_template("embed.html",
        user=_get_user(),
        title="Ad Intelligence",
        embed_url=AD_INTELLIGENCE_URL,
        breadcrumb=[("Hub", "/hub"), ("PPC", "/ppc")],
        current="Ad Intelligence",
        accent="#f59e0b",
    )

@app.route("/seo/serp-researcher")
@login_required
def serp_researcher():
    return render_template("embed.html",
        user=_get_user(),
        title="SERP Content Researcher",
        embed_url=SERP_RESEARCHER_URL,
        breadcrumb=[("Hub", "/hub"), ("SEO", "/seo")],
        current="SERP Researcher",
        accent="#34d399",
    )

# ── Company Signal Tracker ───────────────────────────────────────────────────────
@app.route("/accounts")
@login_required
def accounts():
    cards_html = "".join(_build_account_card(aid, cfg) for aid, cfg in ACCOUNTS.items())
    return render_template("accounts.html", user=_get_user(), account_cards=cards_html)

@app.route("/dashboard/<account_id>")
@login_required
def dashboard(account_id: str):
    cfg = ACCOUNTS.get(account_id)
    if not cfg:
        abort(404, f"Unknown account '{account_id}'")
    path: Path = cfg["dashboard"]
    if not path.exists():
        abort(404, f"Dashboard for '{cfg['name']}' not generated yet.")
    resp = make_response(send_file(str(path)))
    resp.headers.update({"Cache-Control": "no-cache, no-store, must-revalidate",
                         "Pragma": "no-cache", "Expires": "0"})
    return resp

# ── Health + API ─────────────────────────────────────────────────────────────────
@app.route("/api/track", methods=["POST"])
def track_page():
    """Record page view duration to Google Sheet 'Page Views' tab."""
    try:
        # sendBeacon sends text/plain, not application/json — handle both
        data = request.json
        if data is None:
            try:
                data = json.loads(request.get_data(as_text=True))
            except Exception:
                data = {}
        data    = data or {}
        page    = data.get("page", "unknown")
        seconds = int(data.get("seconds", 0))
        email   = data.get("email", "")
        title   = data.get("title", page)
        if seconds < 1:
            return jsonify({"ok": True})

        mins, secs = divmod(seconds, 60)
        duration_fmt = f"{mins}m {secs}s" if mins else f"{secs}s"

        now = datetime.now(IST)
        row = [
            now.strftime("%Y-%m-%d %H:%M:%S IST"),
            now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S"),
            now.strftime("%A"),
            email,
            title,
            page,
            seconds,
            duration_fmt,
            (request.headers.get("X-Forwarded-For","") or request.remote_addr or "").split(",")[0].strip(),
            _parse_ua(request.headers.get("User-Agent",""))[0],   # browser
            _parse_ua(request.headers.get("User-Agent",""))[2],   # OS
            _parse_ua(request.headers.get("User-Agent",""))[3],   # device
        ]

        if not LOGIN_LOG_SHEET_ID:
            return jsonify({"ok": True})

        import json as _j
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        sa_str = os.environ.get("GOOGLE_SA_JSON","")
        if not sa_str:
            return jsonify({"ok": True})

        sa_info = _j.loads(sa_str)
        creds   = service_account.Credentials.from_service_account_info(
            sa_info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        svc = build("sheets", "v4", credentials=creds, cache_discovery=False)

        # Auto-create header on first write to Page Views tab
        try:
            existing = svc.spreadsheets().values().get(
                spreadsheetId=LOGIN_LOG_SHEET_ID, range="Page Views!A1:A1").execute()
            if not existing.get("values"):
                raise Exception("empty")
        except Exception:
            header = [["Timestamp (IST)","Date","Time (IST)","Day","Email","Page Title",
                       "Page URL","Seconds","Duration","IP","Browser","OS","Device"]]
            try:
                svc.spreadsheets().batchUpdate(
                    spreadsheetId=LOGIN_LOG_SHEET_ID,
                    body={"requests":[{"addSheet":{"properties":{"title":"Page Views"}}}]}
                ).execute()
            except Exception:
                pass
            svc.spreadsheets().values().append(
                spreadsheetId=LOGIN_LOG_SHEET_ID, range="Page Views!A1",
                valueInputOption="RAW", body={"values": header}).execute()

        svc.spreadsheets().values().append(
            spreadsheetId=LOGIN_LOG_SHEET_ID, range="Page Views!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values": [row]}).execute()

    except Exception as e:
        log.warning("Page track failed: %s", e)

    return jsonify({"ok": True})


def _fetch_usage_data() -> dict:
    """Fetch login + page view data from Sheets. Shared by shell and data endpoints."""
    def _fetch(tab_range):
        try:
            import json as _j
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            sa_str = os.environ.get("GOOGLE_SA_JSON", "")
            if not sa_str or not LOGIN_LOG_SHEET_ID:
                return []
            sa_info = _j.loads(sa_str)
            creds = service_account.Credentials.from_service_account_info(
                sa_info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
            svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
            r = svc.spreadsheets().values().get(
                spreadsheetId=LOGIN_LOG_SHEET_ID, range=tab_range).execute()
            return r.get("values", [])
        except Exception as e:
            log.warning("admin_usage sheet read failed: %s", e)
            return []

    def col(row, i, default=""):
        return row[i] if len(row) > i else default

    login_rows = _fetch("A1:T1000")
    page_rows  = _fetch("Page Views!A1:M2000")
    login_data = login_rows[1:] if len(login_rows) > 1 else []
    page_data  = page_rows[1:]  if len(page_rows)  > 1 else []

    from collections import Counter

    unique_users     = len({col(r, 5) for r in login_data if col(r, 5)})
    total_logins     = len(login_data)
    total_page_views = len(page_data)

    # Total time spent across all page views
    total_secs = sum(int(col(r, 7)) for r in page_data if col(r, 7).isdigit())
    h, rem = divmod(total_secs, 3600)
    m = rem // 60
    total_time_fmt = f"{h}h {m}m" if h else (f"{m}m" if m else "—")

    # Top pages
    page_counts: dict = {}
    for r in page_data:
        t = col(r, 5)
        if t:
            page_counts[t] = page_counts.get(t, 0) + 1
    top_pages = sorted(page_counts.items(), key=lambda x: x[1], reverse=True)[:8]

    # Logins per day (last 14 days)
    login_days = Counter(col(r, 1) for r in login_data if col(r, 1))
    sorted_days = sorted(login_days.items())[-14:]

    # Browser breakdown (from logins)
    browser_counts = Counter(col(r, 10) for r in login_data if col(r, 10))
    browser_breakdown = browser_counts.most_common(5)

    # Per-user activity
    user_map: dict = {}
    for r in login_data:
        e = col(r, 5)
        if not e: continue
        if e not in user_map:
            user_map[e] = {"email": e, "name": col(r, 6), "logins": 0,
                           "last_seen": col(r, 0), "total_secs": 0}
        user_map[e]["logins"] += 1
        user_map[e]["last_seen"] = col(r, 0)   # rows are oldest→newest; last row = most recent
    for r in page_data:
        e = col(r, 4)
        if e in user_map and col(r, 7).isdigit():
            user_map[e]["total_secs"] += int(col(r, 7))
    for u in user_map.values():
        s = u["total_secs"]; uh, ur = divmod(s, 3600); um = ur // 60
        u["time_fmt"] = f"{uh}h {um}m" if uh else (f"{um}m" if um else "—")
    user_activity = sorted(user_map.values(), key=lambda x: x["logins"], reverse=True)

    login_table = [{"ts": col(r,0), "email": col(r,5), "name": col(r,6),
                    "browser": col(r,10), "os": col(r,12), "device": col(r,13)}
                   for r in reversed(login_data)][:100]
    page_table  = [{"ts": col(r,0), "email": col(r,4), "title": col(r,5),
                    "url": col(r,6), "duration": col(r,8)}
                   for r in reversed(page_data)][:200]

    return dict(total_logins=total_logins, unique_users=unique_users,
                total_page_views=total_page_views, total_time_fmt=total_time_fmt,
                top_pages=top_pages, login_days=sorted_days,
                browser_breakdown=browser_breakdown, user_activity=user_activity,
                login_table=login_table, page_table=page_table)


@app.route("/admin/usage")
@admin_required
def admin_usage():
    """Shell page — renders instantly, JS fetches /admin/usage/data async."""
    return render_template("admin_usage.html", user=_get_user())


@app.route("/admin/usage/data")
@admin_required
def admin_usage_data():
    """JSON data endpoint called by the admin usage shell page."""
    data = _fetch_usage_data()
    return jsonify(data)


def _fetch_anon_visitors_data() -> dict:
    """Fetch people + company data from the Anonymous Visitors Google Sheet."""
    def _fetch(tab_range):
        try:
            import json as _j
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            sa_str = os.environ.get("GOOGLE_SA_JSON", "")
            if not sa_str:
                return []
            sa_info = _j.loads(sa_str)
            creds = service_account.Credentials.from_service_account_info(
                sa_info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
            svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
            r = svc.spreadsheets().values().get(
                spreadsheetId=ANON_VISITORS_SHEET_ID, range=tab_range).execute()
            return r.get("values", [])
        except Exception as e:
            log.warning("anon_visitors sheet read failed: %s", e)
            return []

    def col(row, i, default=""):
        return row[i] if len(row) > i else default

    people_rows  = _fetch("People Enriched!A:K")
    company_rows = _fetch("Visitors By Company!A:J")

    people_data  = people_rows[1:]  if len(people_rows)  > 1 else []
    company_data = company_rows[1:] if len(company_rows) > 1 else []

    from collections import Counter
    ind_counter = Counter()
    for r in company_data:
        ind = col(r, 7)
        if ind and ind != "Unavailable":
            ind_counter[ind] += 1
    top_industries = ind_counter.most_common(8)

    # Deduplicated companies list
    seen, company_table = set(), []
    for r in company_data:
        name = col(r, 0)
        if not name or name in seen:
            continue
        seen.add(name)
        company_table.append({
            "name":      name,
            "website":   col(r, 2),
            "city":      col(r, 3),
            "state":     col(r, 4),
            "country":   col(r, 5),
            "industry":  col(r, 7),
            "employees": col(r, 8),
            "revenue":   col(r, 9),
        })
    company_table.sort(key=lambda x: x["name"])

    # People list — sorted newest first
    people_table = []
    for r in people_data:
        name = col(r, 0)
        if not name or name == "Unavailable":
            continue
        time_str = col(r, 6)
        people_table.append({
            "name":     name,
            "title":    col(r, 1),
            "email":    col(r, 2),
            "location": col(r, 4),
            "pages":    col(r, 5),
            "industry": col(r, 8),
            "website":  col(r, 10),
            "date":     time_str[:10] if time_str else "",
            "time_raw": time_str,
        })
    people_table.sort(key=lambda x: x.get("time_raw", ""), reverse=True)

    return dict(
        total_people=len(people_table),
        unique_companies=len(company_table),
        top_industries=top_industries,
        people_table=people_table[:500],
        company_table=company_table,
    )


@app.route("/ppc/anonymous-visitors")
@login_required
def anonymous_visitors():
    """Anonymous Visitors dashboard shell — loads data async."""
    return render_template("anonymous_visitors.html", user=_get_user())


@app.route("/ppc/anonymous-visitors/data")
@login_required
def anonymous_visitors_data():
    """JSON data endpoint for the Anonymous Visitors dashboard."""
    return jsonify(_fetch_anon_visitors_data())


@app.route("/health")
def health():
    return jsonify({"status": "ok", "accounts": {
        aid: {"name": cfg["name"], "dashboard_exists": cfg["dashboard"].exists()}
        for aid, cfg in ACCOUNTS.items()
    }})

@app.route("/api/weekly-stats")
@app.route("/api/weekly-stats/<account_id>")
@login_required
def weekly_stats(account_id: str = "healthcare"):
    cfg = ACCOUNTS.get(account_id)
    if not cfg:
        return jsonify({"error": f"Unknown account '{account_id}'"}), 404
    p = Path(__file__).parent / "data" / f"weekly-stats-{account_id}.json"
    if not p.exists() and account_id == "healthcare":
        p = Path(__file__).parent / "data" / "weekly-stats.json"
    if not p.exists():
        return jsonify({"error": "Not found"}), 503
    return jsonify(json.loads(p.read_text()))

# ── Account picker moved to templates/accounts.html ─────────────────────────────
_ACCOUNTS_HTML_UNUSED = """
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <title>Company Signal Tracker</title>
  <link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%236366f1'/%3E%3Ccircle cx='16' cy='21' r='2.5' fill='%23fff'/%3E%3Cpath d='M10 15 Q16 9 22 15' stroke='%23fff' stroke-width='2' fill='none' stroke-linecap='round'/%3E%3Cpath d='M6 11 Q16 2 26 11' stroke='%23fff' stroke-width='2' fill='none' stroke-linecap='round' opacity='.55'/%3E%3C/svg%3E">
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet"/>
  <style>
    *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
    body{font-family:'Space Grotesk',sans-serif;color:#e2e8f0;
      min-height:100vh;display:flex;flex-direction:column;overflow-x:hidden;
      background:radial-gradient(ellipse 80% 50% at 20% 0%,rgba(99,102,241,.13) 0%,transparent 60%),
        radial-gradient(ellipse 60% 40% at 85% 60%,rgba(139,92,246,.10) 0%,transparent 55%),
        radial-gradient(ellipse 50% 60% at 50% 100%,rgba(30,27,75,.45) 0%,transparent 70%),
        linear-gradient(160deg,#080b18 0%,#0a0d1a 40%,#070912 100%)}
    .bg-grid{position:fixed;inset:0;z-index:0;pointer-events:none;
      background-image:radial-gradient(circle,rgba(99,102,241,.12) 1px,transparent 1px);
      background-size:36px 36px;
      mask-image:radial-gradient(ellipse 85% 85% at 50% 40%,black 30%,transparent 100%)}
    .bg-glow{position:fixed;border-radius:50%;filter:blur(130px);pointer-events:none;z-index:0;
      width:700px;height:700px;top:-200px;left:-150px;background:rgba(99,102,241,.08)}
    .topbar{position:relative;z-index:10;height:62px;padding:0 32px;
      display:flex;align-items:center;justify-content:space-between;
      background:rgba(7,9,16,.8);backdrop-filter:blur(16px);
      border-bottom:1px solid rgba(255,255,255,.05)}
    .tl{display:flex;align-items:center}
    .brand{display:flex;align-items:center;gap:10px;text-decoration:none}
    .brand-icon{width:34px;height:34px;border-radius:9px;
      background:linear-gradient(135deg,#6366f1,#8b5cf6);
      display:flex;align-items:center;justify-content:center;font-size:16px;
      box-shadow:0 0 14px rgba(99,102,241,.3)}
    .brand-name{font-size:15px;font-weight:700;color:#f1f5f9}
    .bc{display:flex;align-items:center;gap:8px;margin-left:18px;padding-left:18px;
      border-left:1px solid rgba(255,255,255,.07)}
    .bc a{font-size:13px;color:#2d3450;text-decoration:none;transition:color .15s}
    .bc a:hover{color:#64748b}
    .bc-sep{font-size:13px;color:#1a1d27}
    .bc-cur{font-size:13px;font-weight:600;color:#818cf8}
    .sign-out{font-size:12px;color:#3d4460;text-decoration:none;
      padding:6px 14px;border:1px solid rgba(255,255,255,.07);border-radius:8px;
      transition:all .15s}
    .sign-out:hover{color:#ef4444;border-color:rgba(239,68,68,.4)}
    .main{flex:1;position:relative;z-index:1;
      display:flex;flex-direction:column;align-items:center;padding:72px 24px 48px}
    .label{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
      color:#6366f1;margin-bottom:10px;display:flex;align-items:center;gap:8px}
    .label::before,.label::after{content:'';display:block;width:20px;height:1px;background:rgba(99,102,241,.4)}
    .heading{font-size:32px;font-weight:700;color:#f1f5f9;letter-spacing:-.02em;
      margin-bottom:6px;text-align:center}
    .sub{font-size:14px;color:#64748b;margin-bottom:52px}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,360px));
      gap:20px;justify-content:center;width:100%;max-width:780px}
    .card{background:rgba(13,15,23,.9);border:1px solid rgba(255,255,255,.07);
      border-radius:22px;overflow:hidden;text-decoration:none;color:inherit;
      display:flex;flex-direction:column;
      transition:transform .22s cubic-bezier(.34,1.56,.64,1),box-shadow .22s,border-color .2s}
    .card:hover{transform:translateY(-5px);
      box-shadow:0 24px 64px rgba(0,0,0,.55),0 0 0 1px var(--glow)}
    .card-band{height:3px;background:var(--accent)}
    .card-thumb{height:110px;background:var(--thumb);position:relative;
      display:flex;align-items:center;justify-content:center;overflow:hidden}
    .card-thumb-icon{font-size:44px;opacity:.45;filter:drop-shadow(0 0 20px rgba(255,255,255,.15))}
    .card-thumb::after{content:'';position:absolute;inset:0;
      background:linear-gradient(to bottom,transparent 30%,rgba(13,15,23,.95) 100%)}
    .card-badge{position:absolute;top:10px;right:10px;z-index:1;
      font-size:9px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
      padding:3px 9px;border-radius:999px;display:flex;align-items:center;gap:4px;
      background:rgba(16,185,129,.15);border:1px solid rgba(16,185,129,.3);color:#34d399}
    .badge-dot{width:5px;height:5px;border-radius:50%;background:currentColor;
      animation:bpulse 2s infinite}
    @keyframes bpulse{0%,100%{box-shadow:0 0 0 0 rgba(52,211,153,.5)}
      50%{box-shadow:0 0 0 3px rgba(52,211,153,0)}}
    .card-body{padding:20px 24px 22px;flex:1;display:flex;flex-direction:column}
    .card-name{font-size:20px;font-weight:700;color:#f1f5f9;letter-spacing:-.01em;margin-bottom:8px}
    .card-desc{font-size:13px;color:#94a3b8;line-height:1.65;flex:1;margin-bottom:20px}
    .card-footer{display:flex;align-items:center;justify-content:space-between;
      border-top:1px solid rgba(255,255,255,.07);padding-top:16px}
    .stat{font-size:12px;color:#64748b}
    .stat span{color:var(--accent-text);font-weight:600}
    .arrow{font-size:16px;color:var(--accent-text);opacity:0;transition:opacity .15s,transform .15s}
    .card:hover .arrow{opacity:1;transform:translateX(3px)}
    .foot{margin-top:48px;font-size:12px;color:#13151f}
  </style>
</head>
<body>
  <div class="bg-grid"></div>
  <div class="bg-glow"></div>
  <div class="topbar">
    <div class="tl">
      <a href="/hub" class="brand">
        <div class="brand-icon">📡</div>
        <span class="brand-name">Platform</span>
      </a>
      <div class="bc">
        <a href="/hub">Hub</a><span class="bc-sep">›</span>
        <a href="/ppc">PPC</a><span class="bc-sep">›</span>
        <span class="bc-cur">Signal Tracker</span>
      </div>
    </div>
    <a href="/logout" class="sign-out">Sign out</a>
  </div>
  <div class="main">
    <div class="label">Company Intelligence</div>
    <h1 class="heading">Company Signal Tracker</h1>
    <p class="sub">Choose a company list to open the dashboard</p>
    <div class="grid">{account_cards}</div>
    <p class="foot">Position2 · Internal use only</p>
  </div>
</body>
</html>"""


def _build_account_card(account_id, cfg):
    path = cfg["dashboard"]
    accent = cfg["accent"]
    # derive thumb gradient from accent colour
    thumb_map = {"#3b82f6": "linear-gradient(135deg,#172554,#1e3a8a)",
                 "#8b5cf6": "linear-gradient(135deg,#2e1065,#1e1b4b)"}
    thumb = thumb_map.get(accent, f"linear-gradient(135deg,#0d0f17,#1a1d27)")
    if path.exists():
        count = _read_company_count(path)
        refreshed = _read_last_refreshed(path)
        return (
            f'<a class="card" href="/dashboard/{account_id}" '
            f'style="--accent:{accent};--glow:rgba(99,102,241,.25);'
            f'--thumb:{thumb};--accent-text:{accent}">'
            f'<div class="card-band"></div>'
            f'<div class="card-thumb"><div class="card-thumb-icon">{cfg["icon"]}</div>'
            f'<div class="card-badge"><span class="badge-dot"></span>Live</div></div>'
            f'<div class="card-body">'
            f'<div class="card-name">{cfg["name"]}</div>'
            f'<div class="card-desc">{cfg["description"]}</div>'
            f'<div class="card-footer">'
            f'<div class="stat"><span>{count}</span> companies · <span style="color:#64748b;font-weight:400">refreshed {refreshed}</span></div>'
            f'<span class="arrow">→</span></div></div></a>'
        )
    return (
        f'<div class="card" style="--accent:{accent};--glow:rgba(99,102,241,.15);'
        f'--thumb:{thumb};--accent-text:{accent};opacity:.5;cursor:default">'
        f'<div class="card-band"></div>'
        f'<div class="card-thumb"><div class="card-thumb-icon">{cfg["icon"]}</div></div>'
        f'<div class="card-body">'
        f'<div class="card-name">{cfg["name"]}</div>'
        f'<div class="card-desc">{cfg["description"]}</div>'
        f'<div class="card-footer">'
        f'<div class="stat" style="color:#f59e0b">Not generated yet</div>'
        f'</div></div></div>'
    )


def _read_last_refreshed(path: Path) -> str:
    try:
        mtime = path.stat().st_mtime
        dt = datetime.fromtimestamp(mtime, tz=IST)
        d = dt.strftime("%d %b %Y").lstrip("0")
        t = dt.strftime("%I:%M %p").lstrip("0")
        return f"{d}, {t} IST"
    except Exception:
        return "unknown"


def _read_company_count(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        idx = text.find('"total_companies":')
        if idx == -1:
            return "—"
        snippet = text[idx + 18:idx + 28].strip().split(",")[0].strip()
        return snippet if snippet.isdigit() else "—"
    except Exception:
        return "—"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
