"""Platform hub server — Google Sign-In + multi-dashboard routing."""

import os
import time
import json
import gzip
import uuid
import logging
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from functools import wraps

# deploy-touch: 2026-06-15T14:13:06Z
from flask import (
    Flask, send_file, send_from_directory, abort, jsonify,
    request, session, redirect, url_for,
    make_response, render_template,
)
import requests
from collections import Counter

log = logging.getLogger(__name__)

# Indian Standard Time = UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cst-dev-secret-do-not-use-in-prod-abc123xyz")
app.permanent_session_lifetime = timedelta(days=7)

@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403

@app.errorhandler(500)
def server_error(e):
    """Always answer API routes with JSON so the frontend never chokes on an HTML error page."""
    if request.path.startswith("/api/"):
        return jsonify({"error": "Vimi is taking longer than usual - please try again."}), 500
    return ("Internal Server Error", 500)

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


# ── Demo / custom-agent request intake (login-page form) ─────────────────────────
DEMO_REQUEST_SHEET_ID = os.environ.get("DEMO_REQUEST_SHEET_ID", "") or LOGIN_LOG_SHEET_ID
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _demo_request_to_sheet(row: list) -> bool:
    """Append one demo-request row to a 'Demo Requests' tab. Returns True on success."""
    if not DEMO_REQUEST_SHEET_ID:
        return False
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        import json as _json
        sa_json_str = os.environ.get("GOOGLE_SA_JSON", "")
        if sa_json_str:
            creds = service_account.Credentials.from_service_account_info(
                _json.loads(sa_json_str),
                scopes=["https://www.googleapis.com/auth/spreadsheets"])
        elif Path(_SA_JSON).exists():
            creds = service_account.Credentials.from_service_account_file(
                _SA_JSON, scopes=["https://www.googleapis.com/auth/spreadsheets"])
        else:
            log.warning("Demo request: no Google credentials (set GOOGLE_SA_JSON)")
            return False
        svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
        tab = "Demo Requests"
        # Make sure the tab exists (values.append errors on an unknown range).
        try:
            meta = svc.spreadsheets().get(spreadsheetId=DEMO_REQUEST_SHEET_ID).execute()
            titles = [s["properties"]["title"] for s in meta.get("sheets", [])]
            if tab not in titles:
                svc.spreadsheets().batchUpdate(
                    spreadsheetId=DEMO_REQUEST_SHEET_ID,
                    body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
                ).execute()
        except Exception as e:
            log.warning("Demo request: could not ensure tab: %s", e)
        # Header row on first write.
        got = svc.spreadsheets().values().get(
            spreadsheetId=DEMO_REQUEST_SHEET_ID, range=f"{tab}!A1:A1").execute()
        if not got.get("values"):
            header = [["Timestamp (IST)", "Name", "Work Email", "Company",
                       "Interest", "Message", "IP Address", "User Agent", "Source", "Visitor ID"]]
            svc.spreadsheets().values().append(
                spreadsheetId=DEMO_REQUEST_SHEET_ID, range=f"{tab}!A1",
                valueInputOption="RAW", body={"values": header}).execute()
        svc.spreadsheets().values().append(
            spreadsheetId=DEMO_REQUEST_SHEET_ID, range=f"{tab}!A1",
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values": [row]}).execute()
        return True
    except Exception as e:
        log.warning("Demo request sheet append failed: %s", e)
        return False


# Notifications target ONLY the #intelligence-platform-request-access channel (C0BE016E2E8).
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL_ID = os.environ.get("SLACK_CHANNEL_ID", "") or "C0BE016E2E8"


def _demo_request_to_slack(d: dict) -> bool:
    """Post a 'Request access' submission to the #intelligence-platform-request-access
    channel. Prefers the Slack Web API (SLACK_BOT_TOKEN -> chat.postMessage to
    SLACK_CHANNEL_ID); falls back to an incoming webhook (SLACK_WEBHOOK_URL)."""
    text = (":sparkles: *New 'Request access' submission*\n"
            f"*Name:* {d.get('name','')}\n"
            f"*Work email:* {d.get('email','')}\n"
            f"*Company:* {d.get('company') or '—'}\n"
            f"*Interest:* {d.get('interest') or '—'}\n"
            f"*Message:* {d.get('message') or '—'}")
    if SLACK_BOT_TOKEN:
        try:
            r = requests.post("https://slack.com/api/chat.postMessage",
                              headers={"Authorization": "Bearer " + SLACK_BOT_TOKEN},
                              json={"channel": SLACK_CHANNEL_ID, "text": text}, timeout=8)
            if r.ok and r.json().get("ok"):
                return True
            log.warning("Slack chat.postMessage failed: %s", r.text[:200])
        except Exception as e:
            log.warning("Slack chat.postMessage error: %s", e)
    if SLACK_WEBHOOK_URL and SLACK_WEBHOOK_URL != "YOUR_SLACK_WEBHOOK_URL":
        try:
            requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=8)
            return True
        except Exception as e:
            log.warning("Demo request Slack webhook post failed: %s", e)
    return False


import socket as _socket
from contextlib import contextmanager as _contextmanager

@_contextmanager
def _force_ipv4():
    """Force outbound sockets to use IPv4 for the duration of the block.
    Railway containers have no IPv6 route, so getaddrinfo's IPv6 result makes
    SMTP fail with OSError(101, 'Network is unreachable'). Scoped + restored."""
    _orig = _socket.getaddrinfo
    def _v4(host, port, family=0, type=0, proto=0, flags=0):
        res = _orig(host, port, _socket.AF_INET, type, proto, flags)
        return res if res else _orig(host, port, family, type, proto, flags)
    _socket.getaddrinfo = _v4
    try:
        yield
    finally:
        _socket.getaddrinfo = _orig


def _gmail_api_send(subject: str, body: str, to_csv: str, reply_to: str, sender: str) -> None:
    """Send mail via the Gmail API over HTTPS (443) using the service account in
    GOOGLE_SA_JSON with domain-wide delegation, impersonating `sender`. Works on
    hosts (e.g. Railway) that block outbound SMTP. Raises on failure."""
    import json as _json, base64
    from email.message import EmailMessage
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    sa_str = os.environ.get("GOOGLE_SA_JSON", "")
    if not sa_str:
        raise RuntimeError("GOOGLE_SA_JSON not set")
    creds = service_account.Credentials.from_service_account_info(
        _json.loads(sa_str),
        scopes=["https://www.googleapis.com/auth/gmail.send"],
    ).with_subject(sender)
    svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to_csv
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()


def _demo_request_to_email(d: dict) -> bool:
    """Email a 'Request access' submission to the team. Returns True on success.
    Prefers the Gmail API (HTTPS) when GMAIL_SENDER is set (Railway blocks outbound
    SMTP); otherwise falls back to SMTP (SMTP_HOST/PORT/USER/PASS)."""
    to = os.environ.get("DEMO_NOTIFY_EMAIL", "") or "krishna.ladha@position2.com, abhilash.dg@position2.com, sudheer.d@position2.com, sparikh@position2.com"
    subject = "New Request access: %s (%s)" % (d.get("name", ""), d.get("company") or "no company")
    reply_to = d.get("email", "") if _EMAIL_RE.match(d.get("email", "")) else ""
    body = "\n".join([
        "New 'Request access' submission",
        "",
        "Name:     " + (d.get("name", "") or "-"),
        "Email:    " + (d.get("email", "") or "-"),
        "Company:  " + (d.get("company") or "-"),
        "Interest: " + (d.get("interest") or "-"),
        "Message:  " + (d.get("message") or "-"),
        "",
        "Submitted: " + (d.get("ts", "") or ""),
        "IP:        " + (d.get("ip", "") or ""),
    ])
    gmail_sender = os.environ.get("GMAIL_SENDER", "")
    if gmail_sender and os.environ.get("GOOGLE_SA_JSON", ""):
        try:
            _gmail_api_send(subject, body, to, reply_to, gmail_sender)
            return True
        except Exception as e:
            log.warning("Demo request email (Gmail API) failed: %s", e)
    host = os.environ.get("SMTP_HOST", "")
    user = os.environ.get("SMTP_USER", "")
    pwd  = os.environ.get("SMTP_PASS", "")
    if not (host and user and pwd):
        return False
    sender = os.environ.get("SMTP_FROM", "") or user
    try:
        port = int(os.environ.get("SMTP_PORT", "587") or 587)
    except Exception:
        port = 587
    try:
        import smtplib, ssl
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = sender
        msg["To"] = to
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.set_content(body)
        ctx = ssl.create_default_context()
        with _force_ipv4():
            if port == 465:
                with smtplib.SMTP_SSL(host, port, timeout=15, context=ctx) as srv:
                    srv.login(user, pwd)
                    srv.send_message(msg)
            else:
                with smtplib.SMTP(host, port, timeout=15) as srv:
                    srv.starttls(context=ctx)
                    srv.login(user, pwd)
                    srv.send_message(msg)
        return True
    except Exception as e:
        log.warning("Demo request email (SMTP) failed: %s", e)
        return False


@app.route("/api/demo-request", methods=["POST"])
def api_demo_request():
    """Public intake for the login-page 'Book a demo / build a custom agent' form."""
    data = request.get_json(silent=True) or request.form.to_dict() or {}
    name     = (data.get("name") or "").strip()[:120]
    email    = (data.get("email") or "").strip()[:160]
    company  = (data.get("company") or "").strip()[:160]
    interest = (data.get("interest") or "").strip()[:80]
    message  = (data.get("message") or "").strip()[:2000]
    if not name or not _EMAIL_RE.match(email):
        return jsonify({"ok": False,
                        "error": "Please enter your name and a valid work email."}), 400
    ip = (request.headers.get("X-Forwarded-For", "") or
          request.headers.get("X-Real-IP", "") or
          request.remote_addr or "").split(",")[0].strip()
    ua = request.headers.get("User-Agent", "")[:200]
    now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    vid = (request.cookies.get("p2_vid") or data.get("vid") or "").strip()[:64]
    row = [now, name, email, company, interest, message, ip, ua, "login page", vid]
    payload = {"name": name, "email": email, "company": company,
               "interest": interest, "message": message, "ts": now, "ip": ip}
    sheet_ok = _demo_request_to_sheet(row)
    slack_ok = _demo_request_to_slack(payload)
    email_ok = _demo_request_to_email(payload)
    log.info("Demo request: %s <%s> [%s] (sheet=%s slack=%s email=%s)",
             name, email, interest, sheet_ok, slack_ok, email_ok)
    return jsonify({"ok": True, "delivered": bool(sheet_ok or slack_ok or email_ok)})

# ── Marketing site (public): agent directory, detail pages, overview pages ───────
def _svg(inner: str) -> str:
    return ('<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2" '
            'stroke-linecap="round" stroke-linejoin="round">' + inner + "</svg>")

AGENTS = [
    {
        "slug": "signal-tracker", "name": "ABM Signal Tracker", "role": "Account Intent Monitoring",
        "badge": "CORE", "cat": "Signals", "accent": "#a78bfa", "metric": "26 signal types \u00b7 scored weekly",
        "icon": _svg('<path d="M3 3v18h18"/><path d="M7 14l4-4 3 3 5-6"/>'),
        "summary": "Always-on monitoring for your target-account universe. Signal Tracker watches for every buying signal, scores each one, and resurfaces the highest-intent companies every week.",
        "benefit": "You never miss the moment an account becomes ready. The most urgent opportunities rise to the top automatically - no manual list-scrubbing.",
        "how": "It ingests curated, authenticated high-tier sources plus open-web news, scores each event as type_weight x severity x recency (with a bonus when signals stack), keeps a rolling 90-day window, and ships a weekly digest.",
        "who": "Demand-gen and sales teams running account-based programs.",
        "connects": ["Curated sources", "News", "Slack", "Sheets"],
    },
    {
        "slug": "generative-search-visibility", "name": "Generative Search Visibility", "role": "AI Answer-Engine Tracking",
        "badge": "FLAGSHIP", "cat": "GEO", "accent": "#22d3ee", "metric": "ChatGPT \u00b7 Gemini \u00b7 Perplexity",
        "icon": _svg('<circle cx="11" cy="11" r="7"/><path d="m21 21-3.4-3.4"/><path d="M11 7.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z"/>'),
        "summary": "See exactly where your brand shows up across AI answer engines - ChatGPT, Google AI Overviews, Gemini and Perplexity - track your share of voice, and find the prompts you are losing.",
        "benefit": "Win the answer, not just the link. Know your AI share of voice, which sources get cited, and where to act before a competitor owns the response.",
        "how": "It monitors branded and category prompts across major AI engines, measures mention frequency and share of voice over time, maps the domains and pages being cited, and flags the gaps to close.",
        "who": "SEO, content and brand teams defending visibility in AI search.",
        "connects": ["Brand Radar", "AI engines", "GSC", "Sheets"],
    },
    {
        "slug": "anonymous-visitors", "name": "Anonymous Website Visitors", "role": "Visitor De-anonymization",
        "badge": "NEW", "cat": "Web", "accent": "#34d399", "metric": "Recovers 95%+ of lost visitors",
        "icon": _svg('<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>'),
        "summary": "Turn silent website traffic into named accounts. Anonymous Visitor ID reveals the companies - and the people - browsing your site, and hands reps a ready-to-act narrative.",
        "benefit": "Recover the 95%+ of visitors who never fill out a form, and reach them while the intent is still warm.",
        "how": "Visit data is matched to firmographic and person-level identity, the session journey is reconstructed page-by-page, and each visitor becomes a first-person CRM narrative with suggested outreach.",
        "who": "Website, demand-gen and SDR teams who want to act on anonymous intent.",
        "connects": ["GTM", "Sheets", "CRM"],
    },
    {
        "slug": "technical-seo-geo-auditor", "name": "Technical SEO & GEO Auditor", "role": "Site Health & AI Readiness",
        "badge": "NEW", "cat": "SEO", "accent": "#38bdf8", "metric": "200+ checks \u00b7 scored in seconds",
        "icon": _svg('<path d="M9 11l3 3 8-8"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>'),
        "summary": "A full technical, on-page and GEO audit of any site - 200+ checks scored and ranked - with AI-written fixes your team can ship immediately.",
        "benefit": "Replace week-long manual audits with a scored, prioritized fix-list in seconds, so the highest-impact issues get fixed first.",
        "how": "It crawls the site, runs 200+ technical, on-page, structured-data and answer-engine checks, scores each by impact, and generates specific, AI-written recommendations.",
        "who": "SEO leads, technical SEOs and web teams.",
        "connects": ["Crawl", "GSC", "Sheets"],
    },
    {
        "slug": "ai-readiness-auditor", "name": "AI Readiness Auditor", "role": "Answer-Engine Optimization",
        "badge": "NEW", "cat": "GEO", "accent": "#818cf8", "metric": "Score any site in ~15 seconds",
        "icon": _svg('<rect x="4" y="8" width="16" height="11" rx="3"/><path d="M12 8V4"/><circle cx="9" cy="13.5" r="1.1"/><circle cx="15" cy="13.5" r="1.1"/>'),
        "summary": "Score any site's readiness to be understood and cited by AI agents and answer engines - in about 15 seconds - with the exact fixes to get picked up.",
        "benefit": "Get ahead of the shift to AI search. Know precisely what is blocking your pages from being cited, and how to fix it.",
        "how": "It evaluates structure, schema, crawlability, content clarity and machine-readability against answer-engine best practices, then returns a score and prioritized fixes.",
        "who": "SEO and content teams future-proofing for AI search.",
        "connects": ["Crawl", "Schema", "GSC"],
    },
    {
        "slug": "keyword-opportunity-engine", "name": "Keyword Opportunity Engine", "role": "Keyword Strategy",
        "badge": "", "cat": "SEO", "accent": "#6366f1", "metric": "Intent-ranked \u00b7 revenue-weighted",
        "icon": _svg('<circle cx="7.5" cy="15.5" r="3.5"/><path d="M10 13l8-8 3 3M16 7l2 2"/>'),
        "summary": "Surface the keywords actually worth winning - AI-shortlisted by intent, difficulty and revenue potential - so every content bet is backed by data.",
        "benefit": "Stop spreading effort thin. Focus on the terms that convert and that you can realistically rank for.",
        "how": "It expands seed topics, pulls volume and difficulty, scores commercial intent, and shortlists the highest-opportunity keywords with AI.",
        "who": "SEO and content strategists planning the roadmap.",
        "connects": ["Semrush", "GSC", "Keyword Planner"],
    },
    {
        "slug": "content-brief-architect", "name": "Content Brief Architect", "role": "SERP-Driven Briefs",
        "badge": "", "cat": "Content", "accent": "#fbbf24", "metric": "SERP-built \u00b7 ready to write",
        "icon": _svg('<path d="M7 3h7l5 5v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v5h5"/><path d="M9 13h6M9 16.5h4"/>'),
        "summary": "Turn live SERP and competitor data into structured, ready-to-write content briefs - headings, entities, questions and angles that rank.",
        "benefit": "Hand writers a brief that already knows what it takes to win the SERP - less guesswork, faster production, better rankings.",
        "how": "It analyzes the top-ranking results and competitor coverage for a target query, extracts the structure and entities to cover, and assembles a complete brief.",
        "who": "Content strategists, editors and writers.",
        "connects": ["SERP", "Semrush", "Docs"],
    },
    {
        "slug": "content-authority-optimizer", "name": "Content Authority Optimizer", "role": "On-Page & E-E-A-T",
        "badge": "", "cat": "Content", "accent": "#e879f9", "metric": "Tuned for AEO & E-E-A-T",
        "icon": _svg('<path d="M12 3l7 3v5c0 4.5-3 7.6-7 9-4-1.4-7-4.5-7-9V6z"/><path d="M9 12l2 2 4-4"/>'),
        "summary": "Audit and rewrite existing pages for structure, depth and authority signals - tuned for answer engines and E-E-A-T - so pages climb.",
        "benefit": "Get more from content you have already published. Targeted, authority-building edits that move rankings without a full rewrite.",
        "how": "It assesses topical depth, structure, internal links and trust signals, then recommends specific edits aligned to E-E-A-T and AEO.",
        "who": "Content and SEO teams optimizing existing libraries.",
        "connects": ["GSC", "CMS", "Docs"],
    },
    {
        "slug": "competitor-seo-intelligence", "name": "Competitor SEO Intelligence", "role": "Organic Benchmarking",
        "badge": "NEW", "cat": "SEO", "accent": "#fb7185", "metric": "Gaps \u00b7 backlinks \u00b7 authority",
        "icon": _svg('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4.5"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/>'),
        "summary": "Benchmark your organic footprint against any rival - keyword gaps, backlink profiles, authority and page speed - with the plays to overtake them.",
        "benefit": "See exactly where competitors beat you and where they are exposed, then act on a prioritized gap list.",
        "how": "It compares domains across rankings, keyword gaps, backlinks and authority, validates the findings, and drafts opportunity-and-recommendation notes.",
        "who": "SEO leads and growth teams in competitive markets.",
        "connects": ["Semrush", "Ahrefs", "Sheets"],
    },
    {
        "slug": "local-visibility-builder", "name": "Local Visibility Builder", "role": "Multi-Location SEO",
        "badge": "", "cat": "SEO", "accent": "#2dd4bf", "metric": "Dev-ready local pages at scale",
        "icon": _svg('<path d="M12 21s-7-6.3-7-11a7 7 0 0 1 14 0c0 4.7-7 11-7 11z"/><circle cx="12" cy="10" r="2.6"/>'),
        "summary": "Compose approved, developer-ready location and service pages at scale - with optimized copy and image alt text - for multi-location brands.",
        "benefit": "Launch hundreds of consistent, optimized local pages without the manual grind, and capture 'near me' demand.",
        "how": "It assembles location and service pages from approved templates and data, generates optimized copy and bulk image alt tags, and outputs dev-ready pages.",
        "who": "Local SEO and web teams managing many locations.",
        "connects": ["Sheets", "CMS", "Maps"],
    },
    {
        "slug": "search-term-intelligence", "name": "Search Term Intelligence", "role": "Search Query Mining",
        "badge": "", "cat": "Paid", "accent": "#f472b6", "metric": "Negatives + winners, weekly",
        "icon": _svg('<circle cx="11" cy="11" r="7"/><path d="m21 21-3.4-3.4"/><path d="M8 10h6M8 13h4"/>'),
        "summary": "Mine every paid search query for waste and hidden winners - auto-suggested negative keywords and new keyword opportunities, every week.",
        "benefit": "Cut wasted spend and capture converting terms you are not bidding on, without manually combing search-term reports.",
        "how": "It classifies search terms by relevance and performance, flags high-spend/no-conversion waste, and recommends negatives and new keywords.",
        "who": "Paid search and performance teams.",
        "connects": ["Google Ads", "Microsoft Ads", "Sheets"],
    },
    {
        "slug": "linkedin-intelligence", "name": "LinkedIn Intelligence", "role": "Engagement Signals",
        "badge": "", "cat": "Social", "accent": "#0ea5e9", "metric": "Buying-committee engagement",
        "icon": _svg('<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M7 17v-5M12 17V8M17 17v-3"/>'),
        "summary": "Know which members of the buying committee are already paying attention. LinkedIn Intelligence captures engagement signals and maps them to your target accounts.",
        "benefit": "Prioritize the people actually engaging - not just the logo - so outreach lands with the right person at the right time.",
        "how": "It tracks engagement on relevant posts and profiles, attributes it to your accounts, and scores buying-committee interest for ABM plays.",
        "who": "ABM and social-selling teams.",
        "connects": ["LinkedIn", "GTM", "CRM"],
    },
    {
        "slug": "ad-intelligence", "name": "Competitor Ad Intelligence", "role": "Competitive Creative",
        "badge": "NEW", "cat": "Paid", "accent": "#a855f7", "metric": "Live competitor creative tracking",
        "icon": _svg('<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>'),
        "summary": "See exactly what your competitors are running. Competitor Ad Intelligence tracks live competitor creative so your messaging stays a step ahead.",
        "benefit": "Stop guessing competitor strategy - watch their real ads, formats and shifts over time.",
        "how": "It continuously collects competitor ads across platforms and surfaces messaging themes, creative formats, and changes as they happen.",
        "who": "Paid media, brand and competitive-intelligence teams.",
        "connects": ["Paid social", "Search", "Brand"],
    },
    {
        "slug": "pipeline-command-center", "name": "Pipeline Command Center", "role": "Program Analytics",
        "badge": "NEW", "cat": "Analytics", "accent": "#a3e635", "metric": "Every account & task, live",
        "icon": _svg('<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="5" rx="1.5"/><rect x="13" y="11" width="8" height="10" rx="1.5"/><rect x="3" y="14" width="8" height="7" rx="1.5"/>'),
        "summary": "One live command center for your whole go-to-market program - every account, signal, ranking and deliverable in a single prioritized view.",
        "benefit": "Replace scattered spreadsheets and dashboards with one source of truth your whole team works from.",
        "how": "It unifies signals, rankings, tasks and account data from your connected sources into a live, filterable dashboard.",
        "who": "Marketing ops, SEO PMs and team leads.",
        "connects": ["Sheets", "GSC", "CRM"],
    },
    {
        "slug": "gbp-qc-agent", "name": "GBP QC Agent", "role": "Google Business Profile QC",
        "badge": "NEW", "cat": "SEO", "accent": "#f97316", "metric": "3-stage QC \u00b7 brand-checked",
        "icon": _svg('<path d="M12 21s-7-6.3-7-11a7 7 0 0 1 14 0c0 4.7-7 11-7 11z"/><path d="M9 10l2 2 4-4"/>'),
        "summary": "A quality-control and content-generation tool for Google Business Profile posts. Run base content through a 3-stage workflow - base review, location-specific QC, and automated location content generation - all checked against your brand guidelines.",
        "benefit": "Ship on-brand GBP posts at multi-location scale without manual review - every post scored and checked against the client's exact rules, with the fixes written for you.",
        "how": "Pick a client and a stage. It runs base-content review, location-specific QC against the approved base, and automated location content generation - scoring each post 0-100, flagging only real violations by severity, and returning recommended fixes plus a corrected version, all against client brand guidelines.",
        "who": "Local and multi-location SEO and content teams managing Google Business Profile posts across many locations and clients.",
        "connects": ["Google Business Profile", "Brand guidelines", "Sheets"],
    },
    {
        "slug": "on-page-auditor", "name": "On-Page SEO Auditor", "role": "On-Page Optimization",
        "badge": "NEW", "cat": "SEO", "accent": "#14b8a6", "metric": "23 sections · live CWV + PageSpeed",
        "icon": _svg('<circle cx="11" cy="11" r="7"/><path d="m21 21-3.4-3.4"/><path d="M8.4 11l2 2 3.4-3.4"/>'),
        "summary": "A full on-page audit of any URL - 23 sections spanning URL structure, meta, headings, content, schema, Core Web Vitals, canonicals, OG tags and crawlability - scored against live data.",
        "benefit": "Find and fix every on-page issue holding a page back, ranked by impact and backed by live PageSpeed and Core Web Vitals data - no guesswork.",
        "how": "Enter a URL and primary keywords; it pulls live performance data and runs 23 audit sections across technical, content, schema and performance, scoring each and returning prioritized fixes.",
        "who": "SEO and web teams optimizing individual pages.",
        "connects": ["PageSpeed", "Crawl", "GSC"],
    },
    {
        "slug": "hub-spoke-architect", "name": "Hub & Spoke Architect", "role": "Internal-Linking Strategy",
        "badge": "NEW", "cat": "SEO", "accent": "#8b5cf6", "metric": "AI clusters · linking map",
        "icon": _svg('<circle cx="12" cy="12" r="3"/><circle cx="5" cy="5" r="2"/><circle cx="19" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/><path d="M10 10 6.5 6.5M14 10 17.5 6.5M10 14 6.5 17.5M14 14 17.5 17.5"/>'),
        "summary": "Turn a URL list or an existing spreadsheet into an AI-categorized hub-and-spoke structure, then generate targeted internal-linking recommendations across every cluster.",
        "benefit": "Build topical authority and route link equity where it counts - a clear, approved internal-linking plan instead of ad-hoc guesswork.",
        "how": "Upload a hub/spoke sheet or paste a URL list; AI auto-categorizes pages into clusters, you review and approve the structure, and it generates anchor-text and internal-link recommendations.",
        "who": "SEO teams running content clusters and topical-authority plays.",
        "connects": ["Sheets", "Crawl", "CMS"],
    },
    {
        "slug": "robots-monitor", "name": "Robots & Index Monitor", "role": "Index-Health Monitoring",
        "badge": "NEW", "cat": "SEO", "accent": "#f59e0b", "metric": "Daily noindex alerts",
        "icon": _svg('<path d="M7 3h8l5 5v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v5h5"/><path d="M9.5 13.5l4 4M13.5 13.5l-4 4"/>'),
        "summary": "Automatically crawl sitemaps, sample pages by type, and verify noindex signals across production and staging - with instant Slack alerts the moment a live page goes dark.",
        "benefit": "Catch accidental noindex and deindexing before it tanks traffic - automated daily checks instead of manual spot-checks.",
        "how": "It crawls your sitemaps, samples pages by template, verifies index/noindex on production and staging domains, and fires a Slack alert if a production page is suddenly noindexed.",
        "who": "Technical SEO and web teams guarding against accidental deindexing.",
        "connects": ["Sitemaps", "Slack", "Crawl"],
    },
    {
        "slug": "article-enhancer", "name": "Article Enhancer", "role": "Existing-Content Optimization",
        "badge": "NEW", "cat": "Content", "accent": "#ec4899", "metric": "5 LLM passes · SERP-aware",
        "icon": _svg('<path d="M7 3h8l5 5v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z"/><path d="M14 3v5h5"/><path d="M10.5 12.5l.8 1.7 1.7.8-1.7.8-.8 1.7-.8-1.7-1.7-.8 1.7-.8z"/>'),
        "summary": "Crawl a live article, run five specialized LLM analyses in parallel against the top-ranking SERP competitors, and produce an enhanced version with every new addition visually highlighted.",
        "benefit": "Upgrade existing articles to out-cover competitors - added depth, sections and coverage, with changes highlighted so editors review and ship fast.",
        "how": "Paste a live article URL; it crawls the page, runs 5 parallel LLM analyses against SERP competitor data, and returns an enhanced HTML article with all new content highlighted.",
        "who": "Content and SEO teams refreshing and upgrading existing articles.",
        "connects": ["SERP", "CMS", "Docs"],
    },
]
AGENTS_BY_SLUG = {a["slug"]: a for a in AGENTS}

# ── Industries registry (public marketing) ──────────────────────────────────────
def _isvg(inner: str) -> str:
    return ('<svg viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.9" '
            'stroke-linecap="round" stroke-linejoin="round">' + inner + "</svg>")

INDUSTRIES = [
    {
        "slug": "healthcare",
        "name": "Healthcare & Life Sciences",
        "short": "Healthcare",
        "featured": True,
        "accent": "#22d3ee", "accent2": "#34d399",
        "icon": _isvg('<path d="M3 12h3.5l2 5 4-12 2.2 7H21"/>'),
        "eyebrow": "Industry · Healthcare & Life Sciences",
        "headline": "Reach the health system",
        "headline_ital": "the moment it’s ready.",
        "lead": "Healthcare buying is slow, committee-driven and built on trust. Intelligence watches every provider, payer and life-sciences org for the signals that precede a budget, funding, mergers, new facilities, service-line launches and leadership moves, then hands your team the account, the committee and the next move.",
        "stats": [
            {"v": "1,251", "l": "healthcare orgs tracked"},
            {"v": "26",    "l": "buying-signal types"},
            {"v": "24/7",  "l": "real-time detection"},
            {"v": "11",    "l": "agents tuned for healthcare"},
        ],
        "segments": [
            "Health systems & hospitals", "Payers & health plans",
            "Digital health & telehealth", "Medtech & devices",
            "Pharma & biotech", "Pharmacy & retail health",
        ],
        "pains": [
            {"t": "Long, committee-driven cycles", "d": "A single deal touches the CMIO, CNIO, CFO, service-line leaders and procurement. You need to know which system is in-market, and who sits on the committee, before a competitor does."},
            {"t": "Trust decides visibility (YMYL)", "d": "Patients and AI answer engines only surface sources they trust. Clinical accuracy, author credentials and E-E-A-T are what get your pages cited, or buried."},
            {"t": "Hundreds of locations", "d": "Every clinic, hospital and pharmacy is its own local-search entity. One stale address or wrong hour quietly loses patients to the practice down the road."},
            {"t": "Signals hidden in noise", "d": "Funding, M&A, new facility openings, CMS rule changes and C-suite moves all precede budget, but they’re scattered across filings, news and job boards."},
        ],
        "signals": [
            "Funding round", "Health-system merger / M&A", "New facility or clinic opening",
            "Service-line launch", "CMIO / CNIO / C-suite change", "FDA clearance or approval",
            "Clinical-trial milestone", "Clinical & tech hiring surge", "Payer / provider partnership",
            "Earnings & regulatory filings",
        ],
        "agents": [
            {"slug":"call-sentiment-intelligence","name":"Call & Sentiment Intelligence","base":"Voice-of-Patient Analytics","badge":"NEW","accent":"#2dd4aa","accent2":"#34d399",
             "role":"Voice-of-Patient Sentiment","metric":"Calls · Reviews · Surveys · by location",
             "icon": _isvg('<path d="M21 11.5a8.5 8.5 0 0 1-12.6 7.5L3 21l2-5.4A8.5 8.5 0 1 1 21 11.5z"/><path d="M9 11h.01M15 11h.01M8.8 14.2s1.3 1.3 3.2 1.3 3.2-1.3 3.2-1.3"/>'),
             "use":"Unifies call transcripts, Google reviews and post-visit surveys into a sentiment score for every location, so you can see where patients are happy and where reputation is slipping.",
             "summary":"Every patient conversation, in one view. Call & Sentiment Intelligence unifies call transcripts, Google Business Profile reviews and post-visit surveys, then scores sentiment for each location so you can see how patients really feel.",
             "benefit":"You finally see reputation as a live number, not a guess. Sentiment is scored per location, so you know which clinics patients love, which ones are slipping, and exactly what is driving the change.",
             "how":"It ingests call transcripts, review text and survey responses, classifies each one by sentiment, emotion and topic, then rolls the results up into a score for every location and tracks how it moves over time.",
             "who":"Marketing, operations and patient-experience leaders who own reputation across multiple locations.",
             "connects":["Call platform","Google Business Profile","Surveys","Sheets"],
             "out":[{"t":"A sentiment score for every location","s":"Ranked so the sites that need attention rise first.","w":92},{"t":"What is driving it, in plain language","s":"The top themes behind positive and negative feeling.","w":84},{"t":"The verbatims that matter","s":"Flagged calls and reviews, ready to act on.","w":78}]},
            {"slug":"healthcare-account-tracker","name":"Healthcare Account Tracker","base":"ABM Signal Tracker","badge":"LIVE","accent":"#22d3ee","accent2":"#38bdf8",
             "role":"Live Healthcare Universe","metric":"1,251 organizations · scored weekly",
             "icon": _isvg('<path d="M3 21h18"/><path d="M6 21V6a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v15"/><path d="M12 8v4M10 10h4"/><path d="M10 21v-4h4v4"/>'),
             "use":"Your healthcare universe, already live. 1,251 provider, payer, digital-health and medtech companies tracked for funding, C-suite moves, M&A and news, scored weekly.",
             "summary":"Your healthcare market, already mapped. Healthcare Account Tracker comes preloaded with 1,251 provider, payer, digital-health and medtech organizations, watched for the signals that come before a budget.",
             "benefit":"There is nothing to set up. On day one you get a live, scored view of the healthcare organizations most likely to be entering a buying cycle.",
             "how":"It monitors funding, mergers and acquisitions, leadership moves, facility openings and news across the tracked universe, scores each event, and refreshes the ranked list every week.",
             "who":"Sales and demand-gen teams selling into health systems, payers and healthcare vendors.",
             "connects":["Curated sources","News","Slack","Sheets"],
             "out":[{"t":"1,251 organizations, ready to work","s":"Preloaded and refreshed every week.","w":95},{"t":"Entering-a-cycle accounts on top","s":"Ranked by a live intent score.","w":88},{"t":"A weekly digest to your team","s":"Delivered straight to Slack and Sheets.","w":80}]},
            {"slug":"provider-payer-signal-tracker","name":"Provider & Payer Signal Tracker","base":"ABM Signal Tracker","badge":"CORE","accent":"#8b5cf6","accent2":"#a78bfa",
             "role":"Account Intent Monitoring","metric":"26 signal types · scored weekly",
             "icon": _isvg('<path d="M3 3v18h18"/><path d="M7 14l3.5-3.5 3 3 5-6"/>'),
             "use":"Monitors your entire universe of health systems, payers, digital-health and medtech accounts for funding, mergers, facility openings and leadership moves, scored and resurfaced weekly.",
             "summary":"Always-on monitoring for your entire universe of health systems, payers, digital-health and medtech accounts, so the organizations entering a buying cycle rise to the top automatically.",
             "benefit":"You never miss the moment an account becomes ready. Funding, expansions and leadership changes are caught, scored and resurfaced without any manual list-scrubbing.",
             "how":"It ingests curated healthcare sources plus open-web news, scores each event by type, severity and recency, keeps a rolling 90-day window, and ships a weekly digest.",
             "who":"Demand-gen and sales teams running account-based programs in healthcare.",
             "connects":["Curated sources","News","Slack","Sheets"],
             "out":[{"t":"The highest-intent systems, surfaced","s":"Ranked by impact, newest first.","w":94},{"t":"Why each one matters","s":"The signal that fired, in plain language.","w":82},{"t":"Routed to your team","s":"A weekly digest into Slack and Sheets.","w":80}]},
            {"slug":"patient-answer-visibility","name":"Patient-Answer Visibility","base":"Generative Search Visibility","badge":"FLAGSHIP","accent":"#38bdf8","accent2":"#22d3ee",
             "role":"AI Answer-Engine Tracking","metric":"ChatGPT · Gemini · Perplexity · AI Overviews",
             "icon": _isvg('<circle cx="11" cy="11" r="7"/><path d="m21 21-3.4-3.4"/><path d="M11 7.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z"/>'),
             "use":"Tracks how your brand, locations and treatments show up when patients and clinicians ask ChatGPT, Gemini, Perplexity and Google AI Overviews, and flags the answers a competitor is winning.",
             "summary":"See where your brand, locations and treatments show up when patients and clinicians ask AI answer engines, and find the questions a competitor is winning.",
             "benefit":"Win the answer, not just the link. You learn your share of voice in AI search, which sources get cited, and where to act before a competitor owns the response.",
             "how":"It runs branded, treatment and near-me prompts across ChatGPT, Gemini, Perplexity and Google AI Overviews, measures how often you appear, maps the pages being cited, and flags the gaps to close.",
             "who":"SEO, content and brand teams defending visibility in AI search.",
             "connects":["AI engines","Brand Radar","Search Console","Sheets"],
             "out":[{"t":"Your AI share of voice","s":"Across the engines patients actually use.","w":86},{"t":"The prompts you are losing","s":"Questions where a competitor is the answer.","w":80},{"t":"The sources being cited","s":"So you know what to publish next.","w":74}]},
            {"slug":"patient-referrer-de-anonymization","name":"Patient & Referrer De-anonymization","base":"Anonymous Website Visitors","badge":"NEW","accent":"#34d399","accent2":"#2dd4aa",
             "role":"Visitor Identification","metric":"Recovers 95%+ of lost visitors",
             "icon": _isvg('<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>'),
             "use":"Reveals the health systems, referring clinics and employer groups browsing your site, even when they never fill out a form, and reconstructs the pages they read.",
             "summary":"Turn silent website traffic into named organizations. It reveals the health systems, referring clinics and employer groups browsing your site and hands your team a ready-to-act narrative.",
             "benefit":"Recover the visitors who never fill out a form, and reach them while intent is still warm.",
             "how":"Visit data is matched to firmographic identity, the session journey is reconstructed page by page, and each visitor becomes a first-person narrative with suggested outreach.",
             "who":"Website, demand-gen and outreach teams who want to act on anonymous intent.",
             "connects":["Your website","GTM","CRM","Sheets"],
             "out":[{"t":"The organizations on your site","s":"Named, even without a form fill.","w":90},{"t":"The pages they read","s":"Reconstructed session by session.","w":78},{"t":"A ready outreach narrative","s":"So reps can act while intent is warm.","w":74}]},
            {"slug":"hipaa-aware-site-geo-auditor","name":"HIPAA-Aware Site & GEO Auditor","base":"Technical SEO & GEO Auditor","badge":"NEW","accent":"#6366f1","accent2":"#818cf8",
             "role":"Site Health & AI Readiness","metric":"200+ checks · scored in seconds",
             "icon": _isvg('<path d="M9 11l3 3 8-8"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>'),
             "use":"Runs 200+ technical, on-page, structured-data and answer-engine checks, plus ADA and WCAG accessibility and YMYL trust signals, and returns a scored, prioritized fix-list.",
             "summary":"A full technical, on-page and answer-engine audit of any healthcare site, plus accessibility and trust checks, returned as a scored, prioritized fix-list.",
             "benefit":"Replace week-long manual audits with a ranked fix-list in seconds, so the highest-impact issues get fixed first.",
             "how":"It crawls the site, runs technical, on-page, structured-data and answer-engine checks alongside ADA and WCAG accessibility and YMYL trust signals, then scores each issue by impact and writes the fix.",
             "who":"SEO leads, technical SEOs and healthcare web teams.",
             "connects":["Crawl","Search Console","Schema","Sheets"],
             "out":[{"t":"200+ checks, scored","s":"Technical, on-page, schema and answer-engine.","w":92},{"t":"Accessibility and trust flags","s":"ADA, WCAG and YMYL signals included.","w":82},{"t":"A prioritized fix-list","s":"Ranked by impact, ready to ship.","w":80}]},
            {"slug":"clinical-authority-optimizer","name":"Clinical Authority Optimizer","base":"Content Authority Optimizer","accent":"#f5a623","accent2":"#fbbf24",
             "role":"E-E-A-T & Trust","metric":"Bylines · citations · freshness",
             "icon": _isvg('<path d="M12 3l7 3v5c0 4.5-3 7.6-7 9-4-1.4-7-4.5-7-9V6z"/><path d="M9 12l2 2 4-4"/>'),
             "use":"Strengthens E-E-A-T on every clinical page with reviewer bylines, credentials, citations and freshness, so patients and AI engines treat your content as trustworthy.",
             "summary":"Strengthen the trust signals on every clinical page, so patients and AI engines treat your content as a source worth citing.",
             "benefit":"Trust is what gets healthcare content ranked and cited. This makes your expertise visible and verifiable on every page.",
             "how":"It reviews each clinical page for author credentials, medical-reviewer bylines, citations and freshness, then recommends the specific changes that raise E-E-A-T.",
             "who":"Content and SEO teams responsible for clinical accuracy and trust.",
             "connects":["CMS","Search Console","Schema","Sheets"],
             "out":[{"t":"Trust gaps on every page","s":"Bylines, credentials and citations checked.","w":84},{"t":"The fixes that raise E-E-A-T","s":"Specific and ready for your editors.","w":80},{"t":"Freshness that needs attention","s":"Pages due for a clinical review.","w":72}]},
            {"slug":"condition-treatment-brief-architect","name":"Condition & Treatment Brief Architect","base":"Content Brief Architect","accent":"#e879f9","accent2":"#c084fc",
             "role":"Content Briefs","metric":"Condition · symptom · treatment · near-me",
             "icon": _isvg('<path d="M4 4h16v12H7l-3 3z"/><path d="M8 9h8M8 12h5"/>'),
             "use":"Builds research-backed briefs for condition, symptom, treatment and near-me pages, mapped to the searches and AI prompts driving demand in your service lines.",
             "summary":"Research-backed briefs for the condition, symptom, treatment and near-me pages your patients actually search for.",
             "benefit":"Your writers start from a plan grounded in real demand, mapped to the searches and AI prompts driving your service lines.",
             "how":"It analyzes the questions patients ask across search and AI engines, clusters them by service line, and builds a structured brief with headings, questions to answer and sources.",
             "who":"Content strategists and writers building service-line pages.",
             "connects":["Search Console","AI engines","Semrush","Sheets"],
             "out":[{"t":"Briefs mapped to real demand","s":"The questions patients truly search.","w":86},{"t":"Structured and ready to write","s":"Headings, questions and sources included.","w":80},{"t":"Organized by service line","s":"So every page has a clear job.","w":74}]},
            {"slug":"location-facility-visibility","name":"Location & Facility Visibility","base":"Local Visibility Builder + GBP QC","accent":"#34d399","accent2":"#a3e635",
             "role":"Local & Maps Visibility","metric":"GBP · hours · NAP · multi-location QC",
             "icon": _isvg('<path d="M12 21s-7-4.5-7-10a7 7 0 0 1 14 0c0 5.5-7 10-7 10z"/><circle cx="12" cy="11" r="2.4"/>'),
             "use":"Keeps every clinic, hospital and pharmacy accurate and discoverable across Google Business Profiles, hours and NAP, so each facility wins its local map and near-me searches.",
             "summary":"Keep every clinic, hospital and pharmacy accurate and discoverable, so each location wins its local map and near-me searches.",
             "benefit":"One wrong address or hour quietly loses patients. This keeps every location correct and competitive on local search.",
             "how":"It audits Google Business Profiles across all locations for accuracy, hours, categories and NAP consistency, flags issues, and runs multi-location quality control.",
             "who":"Local SEO and operations teams managing many locations.",
             "connects":["Google Business Profile","Maps","Sheets","CRM"],
             "out":[{"t":"Every location, checked","s":"Address, hours, categories and NAP.","w":90},{"t":"Issues flagged by site","s":"So nothing quietly loses patients.","w":80},{"t":"Multi-location QC at a glance","s":"Consistency across the whole network.","w":76}]},
            {"slug":"buying-committee-tracker","name":"Buying-Committee Tracker","base":"LinkedIn Intelligence","accent":"#818cf8","accent2":"#6366f1",
             "role":"People & Committee Mapping","metric":"CMIO · CNIO · Revenue Cycle · procurement",
             "icon": _isvg('<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-5 6-5s6 1.7 6 5"/><path d="M17 8h4M19 6v4"/>'),
             "use":"Maps the committee inside a target health system, tracks job changes and engagement, and surfaces a champion the moment they move or signal interest.",
             "summary":"Map the committee inside a target health system and know the moment a champion moves or signals interest.",
             "benefit":"Healthcare deals are won on relationships. You see who sits on the committee and re-engage the right person at the right time.",
             "how":"It maps roles such as CMIO, CNIO, VP of Revenue Cycle and procurement at each target system, tracks job changes and engagement, and surfaces a champion when they move.",
             "who":"Sales and ABM teams selling into health systems.",
             "connects":["LinkedIn","Apollo","CRM","Sheets"],
             "out":[{"t":"The committee, mapped","s":"Roles and decision-makers per system.","w":86},{"t":"Job changes, tracked","s":"A champion moving is a warm lead.","w":80},{"t":"Engagement signals","s":"Who is paying attention, and when.","w":74}]},
            {"slug":"referral-pipeline-command-center","name":"Referral & Pipeline Command Center","base":"Pipeline Command Center","accent":"#fb7185","accent2":"#f43f5e",
             "role":"Prioritization & Routing","metric":"One intent score · routed to your CRM",
             "icon": _isvg('<path d="M3 4h18v4H3z"/><path d="M5 8v12h14V8"/><path d="M9 12h6M9 16h4"/>'),
             "use":"Ranks every in-market system by one intent score, explains why in plain language, and routes the account with suggested outreach into HubSpot, Salesforce and Slack.",
             "summary":"Every in-market system ranked by one intent score, explained in plain language, and routed straight into the tools your team already uses.",
             "benefit":"Your team works one prioritized list instead of many dashboards, with the reason to reach out ready for each account.",
             "how":"It combines signals into a single intent score, explains why each account ranks where it does, and routes the account with suggested outreach into HubSpot, Salesforce and Slack.",
             "who":"Revenue and sales operations teams in healthcare.",
             "connects":["HubSpot","Salesforce","Slack","Sheets"],
             "out":[{"t":"One ranked worklist","s":"Every in-market system, scored.","w":92},{"t":"The reason, in plain language","s":"Why each account ranks where it does.","w":82},{"t":"Routed into your stack","s":"HubSpot, Salesforce and Slack.","w":80}]},

        ],
        "plays": [
            {"t": "SEO & organic growth", "d": "Win the condition, treatment and “near me” searches patients run, and the AI answers clinicians read, the moment a service line matters."},
            {"t": "Performance & paid media", "d": "Stand up campaigns aimed at in-market systems and patient segments the instant a signal peaks, within healthcare ad policy."},
            {"t": "Content & clinical authority", "d": "Reviewer-backed, E-E-A-T-strong content that earns patient trust and AI citations across every service line."},
            {"t": "RevOps & HubSpot", "d": "Score and route provider/payer signals inside your CRM, with clean handoffs from marketing to the field team."},
        ],
    },
    {
        "slug": "technology-saas",
        "name": "Technology & SaaS",
        "short": "Technology & SaaS",
        "featured": False,
        "accent": "#818cf8", "accent2": "#22d3ee",
        "icon": _isvg('<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21h8M12 17v4"/><path d="M8 9l2.2 2L8 13M14 9h2.5"/>'),
        "eyebrow": "Industry · Technology & SaaS",
        "headline": "Catch the buying cycle",
        "headline_ital": "before the RFP.",
        "lead": "B2B software buying starts long before a form fill, in funding rounds, tech-stack changes, hiring surges and product launches. Intelligence watches all of it across your target accounts and tells your team who’s entering a cycle, and why.",
        "stats": [
            {"v": "26",   "l": "buying-signal types"},
            {"v": "24/7", "l": "real-time detection"},
            {"v": "95%+", "l": "of lost visitors recovered"},
        ],
        "segments": [
            "B2B SaaS", "Cloud & infrastructure", "Dev tools & APIs",
            "Cybersecurity", "Data & AI", "Fintech software",
        ],
        "pains": [
            {"t": "Intent hides until it’s too late", "d": "By the time an account fills out a demo form, they’re often three vendors deep. The real signal fired weeks earlier."},
            {"t": "Crowded, AI-mediated search", "d": "Buyers increasingly ask ChatGPT and Perplexity for shortlists. If you’re not the cited answer, you’re not on the list."},
            {"t": "Champions change jobs", "d": "Your best champion leaves, and takes the deal’s momentum with them. Their move to a new account is your warmest new lead."},
        ],
        "signals": [
            "Funding round", "Tech-stack / technographic change", "Engineering & GTM hiring surge",
            "Product launch", "Leadership change", "M&A", "Partnership", "Earnings & filings",
        ],
        "agents": [
            {"icon": _isvg('<path d="M3 3v18h18"/><path d="M7 14l3.5-3.5 3 3 5-6"/>'),
             "name": "Tech-Stack Signal Tracker", "base": "ABM Signal Tracker", "badge": "CORE",
             "use": "Watches funding, technographic change, hiring surges and product launches across your target accounts, scored weekly so the accounts entering a software cycle surface first."},
            {"icon": _isvg('<circle cx="11" cy="11" r="7"/><path d="m21 21-3.4-3.4"/><path d="M11 7.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z"/>'),
             "name": "AI Answer Visibility", "base": "Generative Search Visibility", "badge": "FLAGSHIP",
             "use": "Tracks whether your product is the answer when buyers ask AI engines for category shortlists, and flags the comparison prompts you’re losing to competitors."},
            {"icon": _isvg('<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>'),
             "name": "Anonymous Account De-anonymization", "base": "Anonymous Website Visitors", "badge": "NEW",
             "use": "Turns silent pricing- and docs-page traffic into named accounts so SDRs reach in-market buyers while the evaluation is live."},
            {"icon": _isvg('<path d="M4 6h16M4 12h16M4 18h10"/><circle cx="18" cy="18" r="3"/>'),
             "name": "Competitor SEO Intelligence", "base": "Competitor SEO Intelligence",
             "use": "Maps where rivals win organic and AI share of voice across your category and surfaces the keyword and content gaps to close first."},
            {"icon": _isvg('<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-5 6-5s6 1.7 6 5"/><path d="M17 8h4M19 6v4"/>'),
             "name": "Champion & Committee Tracker", "base": "LinkedIn Intelligence",
             "use": "Follows your champions when they change jobs and maps the buying committee at each target account so you re-engage warm relationships fast."},
        ],
        "plays": [
            {"t": "SEO & GEO", "d": "Own the category searches and AI answers buyers use to build their shortlist."},
            {"t": "Performance media", "d": "Target in-market accounts the moment intent peaks, not on a static list."},
            {"t": "Content & demand", "d": "Ship comparison, integration and use-case content tuned to live demand."},
            {"t": "RevOps & HubSpot", "d": "Score and route signals into the CRM with clean SDR handoffs."},
        ],
    },
    {
        "slug": "financial-services",
        "name": "Financial Services",
        "short": "Financial Services",
        "featured": False,
        "accent": "#34d399", "accent2": "#0ea5e9",
        "icon": _isvg('<path d="M3 21h18"/><path d="M5 21V9l7-5 7 5v12"/><path d="M9 21v-6h6v6"/>'),
        "eyebrow": "Industry · Financial Services",
        "headline": "Open the high-value conversation",
        "headline_ital": "at the right moment.",
        "lead": "In banking, fintech, insurance and wealth management, the deals are large, the cycles are regulated, and trust is non-negotiable. Intelligence spots the M&A, leadership and growth signals that open a conversation, and keeps your visibility compliant and credible.",
        "stats": [
            {"v": "26",   "l": "buying-signal types"},
            {"v": "24/7", "l": "real-time detection"},
            {"v": "YMYL", "l": "trust-first visibility"},
        ],
        "segments": [
            "Banking", "Fintech & payments", "Insurance",
            "Wealth & asset management", "Lending", "InsurTech",
        ],
        "pains": [
            {"t": "Trust & compliance are the product", "d": "Finance is YMYL, patients of money. Search engines and AI answers heavily weight authority, accuracy and credentials before surfacing you."},
            {"t": "Large, multi-stakeholder deals", "d": "Risk, compliance, finance and the line of business all weigh in. You need to know which institution is in-market and who to engage."},
            {"t": "M&A reshuffles the map", "d": "Mergers, new leadership and funding constantly change who buys what, and create a narrow window to be first in."},
        ],
        "signals": [
            "M&A / consolidation", "Funding round", "Leadership / C-suite change",
            "New product or market launch", "Regulatory filing", "Hiring surge",
            "Partnership", "Earnings & filings",
        ],
        "agents": [
            {"icon": _isvg('<path d="M3 3v18h18"/><path d="M7 14l3.5-3.5 3 3 5-6"/>'),
             "name": "Institution Signal Tracker", "base": "ABM Signal Tracker", "badge": "CORE",
             "use": "Tracks M&A, leadership change, funding and expansion across banks, insurers and fintechs, scored weekly so in-market institutions rise to the top."},
            {"icon": _isvg('<circle cx="11" cy="11" r="7"/><path d="m21 21-3.4-3.4"/><path d="M11 7.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z"/>'),
             "name": "Trusted-Answer Visibility", "base": "Generative Search Visibility", "badge": "FLAGSHIP",
             "use": "Monitors how your brand appears in AI answers to high-stakes financial questions, where authority and accuracy decide whether you’re cited."},
            {"icon": _isvg('<path d="M12 3l7 3v5c0 4.5-3 7.6-7 9-4-1.4-7-4.5-7-9V6z"/><path d="M9 12l2 2 4-4"/>'),
             "name": "Authority & Trust Optimizer", "base": "Content Authority Optimizer",
             "use": "Builds the E-E-A-T signals, credentials, citations, disclosures, that YMYL finance content needs to rank and be trusted by AI engines."},
            {"icon": _isvg('<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>'),
             "name": "Anonymous Account De-anonymization", "base": "Anonymous Website Visitors", "badge": "NEW",
             "use": "Identifies the institutions researching your solutions on-site so relationship teams reach decision-makers while interest is high."},
            {"icon": _isvg('<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-5 6-5s6 1.7 6 5"/><path d="M17 8h4M19 6v4"/>'),
             "name": "Buying-Committee Tracker", "base": "LinkedIn Intelligence",
             "use": "Maps risk, compliance and line-of-business stakeholders, and tracks the leadership moves that open a new relationship."},
        ],
        "plays": [
            {"t": "SEO & GEO", "d": "Win trusted-answer visibility for the high-intent, high-stakes queries buyers and AI engines run."},
            {"t": "Performance media", "d": "Reach in-market institutions and segments within financial advertising policy."},
            {"t": "Content & authority", "d": "Credential-backed content that earns trust, citations and compliance sign-off."},
            {"t": "RevOps & HubSpot", "d": "Route signals to relationship managers with full context and clean handoffs."},
        ],
    },
    {
        "slug": "professional-services",
        "name": "Professional Services",
        "short": "Professional Services",
        "featured": False,
        "accent": "#fbbf24", "accent2": "#e879f9",
        "icon": _isvg('<path d="M6 7V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v2"/><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M3 12h18"/>'),
        "eyebrow": "Industry · Professional Services",
        "headline": "Land the engagement",
        "headline_ital": "when the need appears.",
        "lead": "Consulting, legal, accounting and agencies sell expertise into moments of change, growth, M&A, new leadership, expansion. Intelligence detects those moments across your target accounts and hands your partners a reason to reach out first.",
        "stats": [
            {"v": "26",   "l": "buying-signal types"},
            {"v": "24/7", "l": "real-time detection"},
            {"v": "95%+", "l": "of lost visitors recovered"},
        ],
        "segments": [
            "Management consulting", "Legal", "Accounting & advisory",
            "Marketing & creative agencies", "Staffing & recruiting", "IT services",
        ],
        "pains": [
            {"t": "Relationships, not forms", "d": "Your pipeline runs on partners reaching the right person at the right moment, not inbound leads. You need the trigger and the name."},
            {"t": "Expertise is hard to surface", "d": "Buyers and AI engines reward demonstrated authority. Your firm’s thought leadership has to be findable and citable."},
            {"t": "Windows are narrow", "d": "A funding round, merger or new GC creates a short window where the need is acute, and the first credible firm in usually wins."},
        ],
        "signals": [
            "Funding round", "M&A", "Leadership / C-suite change",
            "Expansion / new office", "Hiring surge", "Regulatory or legal event",
            "Partnership", "Earnings & filings",
        ],
        "agents": [
            {"icon": _isvg('<path d="M3 3v18h18"/><path d="M7 14l3.5-3.5 3 3 5-6"/>'),
             "name": "Opportunity Signal Tracker", "base": "ABM Signal Tracker", "badge": "CORE",
             "use": "Detects growth, M&A, leadership and expansion signals across your target accounts, scored weekly so partners see who needs help now."},
            {"icon": _isvg('<circle cx="11" cy="11" r="7"/><path d="m21 21-3.4-3.4"/><path d="M11 7.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z"/>'),
             "name": "Expertise Answer Visibility", "base": "Generative Search Visibility", "badge": "FLAGSHIP",
             "use": "Tracks whether your firm is the cited authority when prospects ask AI engines for help in your practice areas."},
            {"icon": _isvg('<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>'),
             "name": "Anonymous Visitor De-anonymization", "base": "Anonymous Website Visitors", "badge": "NEW",
             "use": "Reveals the companies reading your service and insight pages so partners follow up while the need is fresh."},
            {"icon": _isvg('<path d="M4 4h16v12H7l-3 3z"/><path d="M8 9h8M8 12h5"/>'),
             "name": "Thought-Leadership Brief Architect", "base": "Content Brief Architect",
             "use": "Builds briefs for the questions buyers and AI engines ask in your practice areas, so your insight content earns visibility and citations."},
            {"icon": _isvg('<circle cx="9" cy="8" r="3.2"/><path d="M3 20c0-3.3 2.7-5 6-5s6 1.7 6 5"/><path d="M17 8h4M19 6v4"/>'),
             "name": "Relationship & Mover Tracker", "base": "LinkedIn Intelligence",
             "use": "Tracks when key contacts change roles and maps the decision-makers at target accounts so partners re-engage at the perfect moment."},
        ],
        "plays": [
            {"t": "SEO & GEO", "d": "Make your practice-area expertise the answer buyers and AI engines find first."},
            {"t": "Performance media", "d": "Reach accounts showing change signals with precise, partner-led targeting."},
            {"t": "Content & authority", "d": "Turn partner expertise into findable, citable thought leadership."},
            {"t": "RevOps & HubSpot", "d": "Route signals to the right partner with the context to act fast."},
        ],
    },
]
INDUSTRIES_BY_SLUG = {i["slug"]: i for i in INDUSTRIES}

# ── Signal catalog (flat list of everything we track) ────────────────────────────
_SIG_PAL = ["#a78bfa", "#22d3ee", "#818cf8", "#f472b6", "#34d399", "#fbbf24", "#38bdf8"]
_SIGNALS_RAW = [
    ("Funding Round", "Fresh capital, fresh budget.",
     "A company closes a new round of funding. That means new budget, ambitious targets and a leadership team under pressure to deploy capital fast — one of the strongest moments to start a conversation."),
    ("C-Suite Join", "A new leader, a new agenda.",
     "A new executive steps in, often a CMO, CRO or CFO. New leaders rebuild teams, re-evaluate vendors and hunt for quick wins in their first 90 days — making them unusually open to fresh ideas."),
    ("C-Suite Exit", "Leadership in transition.",
     "A senior leader departs. Strategy, budgets and vendor relationships all fall into flux, opening a window before a replacement locks in new priorities."),
    ("Acquisition / M&A", "A deal that reshapes everything.",
     "The company acquires, merges or gets acquired. Org charts, tech stacks and spending are redrawn — and integration work creates urgent, well-funded needs almost overnight."),
    ("IPO Signal", "Going public, scaling up.",
     "A company files to go public or signals IPO intent. Expect new scrutiny, rapid scaling and fresh investment in brand, demand generation and infrastructure."),
    ("Subsidiary Change", "New entity, new territory.",
     "A new subsidiary, division or restructure appears. Each new entity behaves like a brand-new account, with its own budget and its own buying motion to win."),
    ("Product Launch", "A new product to take to market.",
     "The company ships a new product or enters a new category. Launches demand go-to-market firepower — exactly when marketing and sales support is most valued."),
    ("Partnership", "A new alliance forms.",
     "The company announces a partnership, integration or channel deal. Alliances signal expansion, shifting priorities and fresh co-marketing needs."),
    ("Creative Hiring", "Investing in growth talent.",
     "The company is hiring marketers, designers and growth roles. Hiring in these functions is a direct signal of budget and ambition behind their growth engine."),
    ("News Mention", "Staying on the radar.",
     "The company surfaces in the news. Even routine coverage keeps an account warm, hints at momentum and gives reps a timely, relevant reason to reach out."),
    ("Company Visit", "Someone is on your site.",
     "An anonymous visitor is resolved to a named company. Silent web traffic becomes a real account you can route, research and pursue — no form required."),
    ("High-Intent Page View", "They are eyeing the good stuff.",
     "A visitor lands on pricing, demo or product pages. These are the pages buyers read when they are seriously evaluating — among the clearest intent cues on the open web."),
    ("Return Visit", "They keep coming back.",
     "A company returns across multiple sessions. Repeat visits signal rising, sustained interest worth acting on long before a form is ever filled."),
    ("Known Contact Visit", "A real buyer, identified.",
     "A mapped contact from a target account lands on your site. You know exactly who is interested — and exactly who to follow up with."),
    ("Deep Session", "Deep engagement in one visit.",
     "A visitor moves through many pages with long dwell time. Depth of engagement in a single session is a strong proxy for genuine, active evaluation."),
    ("Post Engagement", "They are engaging in public.",
     "Someone from a target account likes, comments on or shares relevant content. Public engagement quietly reveals who is paying attention right now."),
    ("Profile View", "They are checking you out.",
     "A person from a target account views your team's profiles. Quiet research that very often precedes an inbound conversation."),
    ("Buying-Committee Activity", "The whole committee is moving.",
     "Multiple stakeholders at one account engage within a short window. When several people lean in together, a buying cycle is usually already underway."),
    ("Content Interaction", "Active research in progress.",
     "Saves, follows and repeat clicks that go well beyond a passing glance — the fingerprints of an account actively building a shortlist."),
    ("New Competitor Ad", "A rival makes a move.",
     "A competitor launches new ad creative. A timely cue to counter-position your message while their spend is live and in-market."),
    ("Messaging Shift", "The competition repositions.",
     "A competitor changes its value propositions or positioning. Early warning to adjust your own narrative and defend your differentiation."),
    ("New Campaign", "A fresh competitive push.",
     "A competitor spins up a new campaign across a channel. Visibility into where rivals are investing — and, just as usefully, where the whitespace is."),
    ("Format / Channel Change", "They are testing new ground.",
     "A competitor shifts ad formats or channels. A signal of strategy change worth matching, countering or out-maneuvering."),
    ("Intent Score Spike", "Heat, rising fast.",
     "An account's composite intent score jumps. Everything we track rolls into one number — and a sharp spike means the moment to act is now."),
    ("Multi-Signal Stack", "Signals stacking up.",
     "Several signals fire on a single account in a short window. Stacked signals are the highest-confidence buying indicator the platform produces."),
    ("Readiness Change", "Crossing into ready.",
     "An account moves into a higher readiness tier. A clear, plain-language cue that an account has just become a priority worth a call."),
]
_SIG_GROUPS = [
    ("Company & market moves", 0, 10),
    ("Website intent", 10, 15),
    ("Social engagement", 15, 19),
    ("Competitor & paid", 19, 23),
    ("Scoring & readiness", 23, 26),
]
def _sig_group(i):
    for _name, _a, _b in _SIG_GROUPS:
        if _a <= i < _b:
            return _name
    return "Other"
SIGNALS = [{"name": n, "tagline": t, "blurb": b, "accent": _SIG_PAL[i % len(_SIG_PAL)],
            "group": _sig_group(i)}
           for i, (n, t, b) in enumerate(_SIGNALS_RAW)]
SIGNAL_GROUPS = [{"name": _gname, "items": [s for s in SIGNALS if s["group"] == _gname]}
                 for _gname, _ga, _gb in _SIG_GROUPS]


@app.route("/agents")
def agents_dir():
    return render_template("agents.html", page="agents", agents=AGENTS, agent=None, related=[])


@app.route("/agents/<slug>")
def agent_detail(slug):
    a = AGENTS_BY_SLUG.get(slug)
    if not a:
        return redirect(url_for("agents_dir"))
    related = [x for x in AGENTS if x["slug"] != slug][:3]
    return render_template("agents.html", page="agent", agents=AGENTS, agent=a, related=related)


@app.route("/platform")
def platform_page():
    return render_template("agents.html", page="platform", agents=AGENTS, agent=None, related=[])


@app.route("/signals")
def signals_page():
    return render_template("agents.html", page="signals", agents=AGENTS, agent=None, related=[], signals_list=SIGNALS, signal_groups=SIGNAL_GROUPS)


@app.route("/solutions")
def solutions_page():
    return render_template("agents.html", page="solutions", agents=AGENTS, agent=None, related=[])


@app.route("/industries")
def industries_page():
    return render_template("agents.html", page="industries", agents=AGENTS, agent=None,
                           related=[], industries=INDUSTRIES)


@app.route("/industries/<slug>")
def industry_detail(slug):
    ind = INDUSTRIES_BY_SLUG.get(slug)
    if not ind:
        return redirect(url_for("industries_page"))
    others = [x for x in INDUSTRIES if x["slug"] != slug]
    return render_template("agents.html", page="industry", agents=AGENTS, agent=None,
                           related=[], industry=ind, industries=INDUSTRIES, other_industries=others)


@app.route("/industries/<islug>/agents/<aslug>")
def industry_agent_detail(islug, aslug):
    ind = INDUSTRIES_BY_SLUG.get(islug)
    if not ind:
        return redirect(url_for("industries_page"))
    ag = None
    for a in ind.get("agents", []):
        if a.get("slug") == aslug:
            ag = a
            break
    if not ag:
        return redirect(url_for("industry_detail", slug=islug))
    related = [x for x in ind["agents"] if x.get("slug") != aslug][:3]
    return render_template("agents.html", page="iagent", agents=AGENTS, agent=ag,
                           related=related, industry=ind, industries=INDUSTRIES)

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

def _login_redirect():
    """Send an unauthenticated visitor to the login page, remembering where they
    were headed (so they land there after sign-in) and using accurate messaging."""
    try:
        nxt = request.path
        if request.query_string:
            nxt += "?" + request.query_string.decode("utf-8", "ignore")
        if (nxt.startswith("/") and not nxt.startswith("//")
                and not nxt.startswith("/api") and not nxt.startswith("/auth")
                and nxt not in ("/login", "/logout", "/")):
            session["next_url"] = nxt
    except Exception:
        pass
    had_session = bool(request.cookies.get(app.config.get("SESSION_COOKIE_NAME", "session")))
    msg = ("Your session expired. Please sign in again."
           if had_session else "Please sign in to continue.")
    from urllib.parse import quote
    return redirect(url_for("login_page") + "?error=" + quote(msg))


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _get_user():
            return _login_redirect()
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _get_user()
        if not user:
            return _login_redirect()
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

    email = idinfo.get("email", "")
    if not email.lower().endswith("@position2.com"):
        return jsonify({"success": False, "error": "Access restricted to Position2 accounts only."}), 403

    session["google_user"] = {
        "email":      email,
        "name":       idinfo.get("name", ""),
        "given_name": idinfo.get("given_name", ""),
        "picture":    idinfo.get("picture", ""),
    }
    session.permanent = True
    _log_login_to_sheet(session["google_user"])   # fire-and-forget, fails silently
    nxt = session.pop("next_url", None)
    if not (isinstance(nxt, str) and nxt.startswith("/") and not nxt.startswith("//")):
        nxt = "/hub"
    return jsonify({"success": True, "redirect": nxt})


# ── Core routes ─────────────────────────────────────────────────────────────────

@app.route("/robots.txt")
def robots_txt():
    from flask import Response
    return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")

@app.route("/favicon.ico")
@app.route("/favicon.svg")
def favicon():
    return send_from_directory("static", "favicon.svg", mimetype="image/svg+xml")


@app.route("/")
def index():
    if _get_user():
        return redirect(url_for("hub"))
    return render_template("agents.html", page="home", agents=AGENTS, agent=None,
                           related=[], signals_list=SIGNALS)

@app.route("/privacy")
def privacy_page():
    return render_template("agents.html", page="privacy", agents=AGENTS, agent=None, related=[])

@app.route("/terms")
def terms_page():
    return render_template("agents.html", page="terms", agents=AGENTS, agent=None, related=[])

@app.route("/integrations")
def integrations_page():
    return render_template("agents.html", page="integrations", agents=AGENTS, agent=None, related=[])

@app.route("/resources")
def resources_page():
    return render_template("agents.html", page="resources", agents=AGENTS, agent=None, related=[])

@app.route("/security")
def security_page():
    return render_template("agents.html", page="security", agents=AGENTS, agent=None, related=[])

@app.route("/login")
def login_page():
    if _get_user():
        return redirect(url_for("hub"))
    return render_template("agents.html", page="login", agents=AGENTS, agent=None,
                           related=[], google_client_id=GOOGLE_CLIENT_ID,
                           error=request.args.get("error", ""))

@app.route("/login-preview")
def login_preview():
    return render_template("login_preview.html", google_client_id=GOOGLE_CLIENT_ID,
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

@app.route("/gtm")
@login_required
def gtm():
    return render_template("gtm.html", user=_get_user())


@app.route("/gtm/call-sentiment")
@app.route("/gtm/call-sentiment/")
@login_required
def call_sentiment():
    return render_template("call_sentiment.html", user=_get_user())

# ── Legacy /ppc* page URLs → 301 redirect to canonical /gtm* (links still resolve) ──
@app.route("/ppc")
@app.route("/ppc/")
def ppc_redirect():
    return redirect("/gtm", code=301)

@app.route("/ppc/ad-intelligence")
@app.route("/ppc/ad-intelligence/")
def ppc_ad_intelligence_redirect():
    return redirect("/gtm/ad-intelligence", code=301)

@app.route("/ppc/anonymous-visitors")
def ppc_anonymous_visitors_redirect():
    return redirect("/gtm/anonymous-visitors", code=301)

@app.route("/ppc/linkedin-scraper")
def ppc_linkedin_scraper_redirect():
    return redirect("/gtm/linkedin-scraper", code=301)

@app.route("/seo")
@login_required
def seo():
    return render_template("seo.html", user=_get_user(), seo_tools=_seo_tools())

# ── Embedded dashboards ─────────────────────────────────────────────────────────
_SERP_BASE = "https://seo-apps-production-37a6.up.railway.app"

# ── Ad Intelligence (built React app served directly — no iframe) ────────────
AD_INTEL_SHEET_ID = "16U5_QSxMmrAGKvK5dHScBu1Et4BJ1p8Q1ns5LycRA0s"

@app.route("/gtm/ad-intelligence")
@app.route("/gtm/ad-intelligence/")
@login_required
def ad_intelligence():
    return send_from_directory("ad_intelligence", "index.html")

@app.route("/gtm/ad-intelligence/assets/<path:filename>")
@app.route("/ppc/ad-intelligence/assets/<path:filename>")
def ad_intelligence_assets(filename):
    return send_from_directory("ad_intelligence/assets", filename)

@app.route("/gtm/ad-intelligence/favicon.svg")
@app.route("/ppc/ad-intelligence/favicon.svg")
def ad_intelligence_favicon():
    return send_from_directory("ad_intelligence", "favicon.svg")

@app.route("/gtm/ad-intelligence/icons.svg")
@app.route("/ppc/ad-intelligence/icons.svg")
def ad_intelligence_icons():
    return send_from_directory("ad_intelligence", "icons.svg")

_SEO_TOOLS_FALLBACK = [
    {"slug": "keyword-research",       "path": "/keyword-research",       "name": "Keyword Research",         "desc": "AI-powered keyword shortlisting",              "icon": "🔑", "tags": ["Keywords", "SEMrush"]},
    {"slug": "content-research",       "path": "/content-research",       "name": "Content Research",         "desc": "Competitor-based content briefs",              "icon": "🔎", "tags": ["Content", "SERP"]},
    {"slug": "article-recommendation", "path": "/article-recommendation", "name": "Article Recommendation",   "desc": "Structured content briefs from SERP data",     "icon": "📋", "tags": ["Briefs", "SERP"]},
    {"slug": "content-enhancement",    "path": "/content-enhancement",    "name": "Content Enhancement",      "desc": "Structure & authority recommendations",        "icon": "⚡", "tags": ["AEO", "E-E-A-T"]},
    {"slug": "article-enhancement",    "path": "/article-enhancement",    "name": "Enhance Existing Article", "desc": "Multi-LLM + SERP competitor enhancement",      "icon": "✨", "tags": ["Enhance", "LLM"]},
    {"slug": "on-page-audit",          "path": "/on-page-audit",          "name": "On-Page SEO Audit",        "desc": "23 sections · live data · PageSpeed + CWV",   "icon": "🔬", "tags": ["On-Page", "CWV"]},
    {"slug": "seo-geo-audit",          "path": "/seo-geo-audit",          "name": "SEO & GEO Audit",          "desc": "200+ checks · scored · AI recommendations",   "icon": "✅", "tags": ["SEO", "GEO", "AI"]},
    {"slug": "agent-readiness-audit",  "path": "/agent-readiness-audit",  "name": "Agent Readiness Audit",    "desc": "Score AI agent readiness, 0–100",             "icon": "🤖", "tags": ["AI Audit"]},
    {"slug": "image-alt-audit",        "path": "/image-alt-audit",        "name": "Image Alt Tag Audit",      "desc": "Bulk alt tag generation for location pages",   "icon": "🖼️", "tags": ["Images"]},
    {"slug": "location-page-builder",  "path": "/location-page-builder",  "name": "Location + Service Pages", "desc": "Composed, approved, dev-ready location pages", "icon": "📍", "tags": ["Local SEO", "Pages"]},
    {"slug": "hub-spoke",              "path": "/hub-spoke",              "name": "Hub & Spoke",              "desc": "AI internal-linking strategy & recommendations","icon": "🕸️", "tags": ["Internal Linking"]},
    {"slug": "knowledge-base",         "path": "/kb",                     "name": "Knowledge Base",           "desc": "Client & industry context management",         "icon": "📚", "tags": ["Knowledge", "Context"]},
    {"slug": "robots-monitor",         "path": "/robots-monitor",         "name": "Robots Monitor",           "desc": "Daily noindex health checks across domains",   "icon": "🛰️", "tags": ["Technical SEO"]},
    {"slug": "team-insights",          "path": "/team-insights",          "name": "Team Insights",            "desc": "Live SEO PM dashboard from Google Sheets",     "icon": "📊", "tags": ["PM", "Sheets"]},
    {"slug": "gbp-qc-agent",           "path": "", "url": "https://gbp-qc-agent-production.up.railway.app", "name": "GBP QC Agent", "desc": "Quality control & content generation for GBP posts", "icon": "🏪", "tags": ["Local SEO", "GBP"]},
]

# Live SEO tool list is fetched from the SERP app's /tools.json manifest (cached).
# Add a tool on the SERP side -> it appears here automatically, no redeploy needed.
_SEO_MANIFEST = {"ts": 0.0, "tools": None}
_SEO_MANIFEST_TTL = 300  # seconds

def _seo_tools():
    now = time.time()
    cached = _SEO_MANIFEST["tools"]
    if cached is not None and now - _SEO_MANIFEST["ts"] < _SEO_MANIFEST_TTL:
        return cached
    tools = None
    try:
        pt = os.environ.get("SERP_PLATFORM_TOKEN", "")
        url = f"{_SERP_BASE}/tools.json" + (f"?pt={pt}" if pt else "")
        r = requests.get(url, timeout=5)
        r.raise_for_status()
        data = r.json().get("tools")
        if isinstance(data, list) and data:
            tools = data
    except Exception as e:
        logging.warning("SEO manifest fetch failed, using fallback: %s", e)
    if tools is None:
        tools = _SEO_TOOLS_FALLBACK
    _SEO_MANIFEST.update(ts=now, tools=tools)
    return tools

@app.route("/seo/<tool_slug>")
@login_required
def seo_tool(tool_slug: str):
    tool = next((t for t in _seo_tools() if t.get("slug") == tool_slug), None)
    if not tool:
        abort(404)
    pt = os.environ.get("SERP_PLATFORM_TOKEN", "")
    ext = tool.get("url")
    if ext:
        embed_url = ext
    else:
        path = tool["path"]
        sep = "?" if "?" not in path else "&"
        embed_url = f"{_SERP_BASE}{path}{sep + 'pt=' + pt if pt else ''}"
    return render_template("embed.html",
        user=_get_user(),
        title=tool["name"],
        embed_url=embed_url,
        breadcrumb=[("Hub", "/hub"), ("SEO", "/seo")],
        current=tool["name"],
        accent="#34d399",
    )

# ── Company Signal Tracker ───────────────────────────────────────────────────────
@app.route("/accounts")
@login_required
def accounts():
    cards_html = "".join(_build_account_card(aid, cfg) for aid, cfg in ACCOUNTS.items())
    return render_template("accounts.html", user=_get_user(), account_cards=cards_html)

@app.route("/signal-tracker/<account_id>")
@app.route("/signal-tracker/<account_id>/<section>")
@login_required
def dashboard(account_id: str, section: str = None):
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

@app.after_request
def _no_html_cache(resp):
    """Never let browsers cache HTML pages — UI updates must show immediately after deploys."""
    try:
        if resp.mimetype == "text/html":
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
    except Exception:
        pass
    return resp


@app.route("/dashboard/<account_id>")
@app.route("/dashboard/<account_id>/<section>")
@login_required
def dashboard_legacy(account_id: str, section: str = None):
    """Back-compat: old /dashboard/* URLs redirect to canonical /signal-tracker/*."""
    target = "/signal-tracker/" + account_id + (("/" + section) if section else "")
    return redirect(target, code=301)

@app.route("/api/whoami")
@login_required
def whoami():
    u = _get_user() or {}
    return jsonify({"name": u.get("name", ""), "given_name": u.get("given_name", ""),
                    "email": u.get("email", ""), "picture": u.get("picture", "")})

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
        email   = data.get("email", "") or (session.get("google_user") or {}).get("email", "")
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


# ── Visitor Analytics (anonymous, pre-login web analytics) ──────────────────────
_VA_HEADER = ["Timestamp (IST)","Date","Time (IST)","Day","Visitor ID","Session ID",
    "New Visitor","Page URL","Page Title","Referrer","Referrer Host","UTM Source",
    "UTM Medium","UTM Campaign","UTM Term","UTM Content","Landing Page","Pages In Session",
    "Time On Page (s)","Engaged Time (s)","Max Scroll %","Total Clicks","CTA Clicks",
    "Video","Form Stage","Search Terms","Rage Clicks","LCP (ms)","CLS","INP (ms)",
    "Viewport","Screen","Language","Browser","OS","Device","Bot","IP","Events (JSON)"]

_BOT_RE = re.compile(r"bot|crawl|spider|slurp|bingpreview|facebookexternalhit|embedly|"
                     r"monitor|headless|lighthouse|gtmetrix|preview|curl|wget|"
                     r"python-requests|axios|http-client", re.I)

def _va_sheets_service():
    import json as _j
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    sa_str = os.environ.get("GOOGLE_SA_JSON","")
    if not sa_str or not LOGIN_LOG_SHEET_ID:
        return None
    sa_info = _j.loads(sa_str)
    creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=["https://www.googleapis.com/auth/spreadsheets"])
    return build("sheets","v4",credentials=creds,cache_discovery=False)

_IP_CACHE = {}
def _ip_company(ip: str) -> str:
    """Best-effort reverse-IP -> organization. Requires IPINFO_TOKEN; '' otherwise. Cached per IP."""
    if not ip or ip in ("127.0.0.1", "::1", "localhost"):
        return ""
    if ip in _IP_CACHE:
        return _IP_CACHE[ip]
    out = ""
    token = os.environ.get("IPINFO_TOKEN", "")
    if token:
        try:
            import urllib.request, json as _j
            url = "https://ipinfo.io/%s/json?token=%s" % (ip, token)
            with urllib.request.urlopen(url, timeout=2.5) as resp:
                d = _j.loads(resp.read().decode("utf-8"))
            org = (d.get("org") or "").strip()
            out = re.sub(r"^AS\d+\s+", "", org)   # drop ASN prefix
        except Exception as e:
            log.warning("ip_company lookup failed: %s", e)
            out = ""
    _IP_CACHE[ip] = out
    return out

def _va_identity_map() -> dict:
    """visitor_id -> {name,email,company,source}. Merges access-form conversions + provider identifies."""
    m = {}
    try:
        for req in _read_access_requests(limit=2000):
            v = (req.get("vid") or "").strip()
            if v:
                m[v] = {"name": req.get("name", ""), "email": req.get("email", ""),
                        "company": req.get("company", ""), "source": "Request access"}
    except Exception:
        pass
    svc = _va_sheets_service()
    if svc:
        try:
            r = svc.spreadsheets().values().get(
                spreadsheetId=LOGIN_LOG_SHEET_ID, range="Visitor Identities!A1:G5000").execute()
            for x in (r.get("values", [])[1:] or []):
                def cc(i): return x[i] if len(x) > i else ""
                v = (cc(1) or "").strip()
                if v:
                    m[v] = {"name": cc(2), "email": cc(3), "company": cc(4),
                            "source": cc(6) or "provider"}
        except Exception:
            pass
    return m

@app.route("/api/identify", methods=["POST"])
def api_identify():
    """Ingest a person-level identification keyed by visitor_id (provider webhook / your own logic).
    Disabled unless IDENTIFY_TOKEN is set, and the caller must present it
    (X-Identify-Token header or ?token=). Stores to the 'Visitor Identities' tab."""
    secret = os.environ.get("IDENTIFY_TOKEN", "")
    if not secret:
        return jsonify({"ok": False, "error": "identify disabled"}), 404
    given = request.headers.get("X-Identify-Token", "") or request.args.get("token", "")
    if given != secret:
        return abort(403)
    data = request.get_json(silent=True) or {}
    vid = (data.get("vid") or data.get("visitor_id") or "").strip()
    if not vid:
        return jsonify({"ok": False, "error": "vid required"}), 400
    now = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
    row = [now, vid[:64], str(data.get("name", ""))[:160], str(data.get("email", ""))[:200],
           str(data.get("company", ""))[:200], str(data.get("title", ""))[:160],
           str(data.get("source", "provider"))[:80]]
    try:
        svc = _va_sheets_service()
        if not svc:
            return jsonify({"ok": False, "error": "sheets not configured"}), 500
        tab = "Visitor Identities"
        try:
            existing = svc.spreadsheets().values().get(
                spreadsheetId=LOGIN_LOG_SHEET_ID, range="%s!A1:A1" % tab).execute()
            if not existing.get("values"):
                raise Exception("empty")
        except Exception:
            try:
                svc.spreadsheets().batchUpdate(spreadsheetId=LOGIN_LOG_SHEET_ID,
                    body={"requests": [{"addSheet": {"properties": {"title": tab}}}]}).execute()
            except Exception:
                pass
            hdr = [["Timestamp (IST)", "Visitor ID", "Name", "Email", "Company", "Title", "Source"]]
            svc.spreadsheets().values().append(spreadsheetId=LOGIN_LOG_SHEET_ID, range="%s!A1" % tab,
                valueInputOption="RAW", body={"values": hdr}).execute()
        svc.spreadsheets().values().append(spreadsheetId=LOGIN_LOG_SHEET_ID, range="%s!A1" % tab,
            valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": [row]}).execute()
    except Exception as e:
        log.warning("identify failed: %s", e)
        return jsonify({"ok": False}), 500
    return jsonify({"ok": True})

@app.route("/api/atrack", methods=["POST"])
def atrack():
    """Ingest one anonymous visitor page-view (sendBeacon text or JSON). Public."""
    try:
        from urllib.parse import urlparse
        data = request.get_json(silent=True)
        if data is None:
            try:
                data = json.loads(request.get_data(as_text=True))
            except Exception:
                data = {}
        data = data or {}

        ua = request.headers.get("User-Agent","")
        br, _bv, osn, dev = _parse_ua(ua)
        ip = (request.headers.get("X-Forwarded-For","") or request.remote_addr or "").split(",")[0].strip()
        is_bot = "Yes" if _BOT_RE.search(ua) else "No"

        def g(k, d=""):
            v = data.get(k, d)
            return v if v is not None else d
        utm = data.get("utm") or {}
        cta = data.get("cta") or {}
        try:
            cta_str = " · ".join("%s×%s" % (k, v) for k, v in cta.items())
        except Exception:
            cta_str = ""
        ref = g("ref")
        try:
            ref_host = (urlparse(ref).hostname or "").replace("www.","") if ref else "direct"
        except Exception:
            ref_host = "direct"

        now = datetime.now(IST)
        row = [
            now.strftime("%Y-%m-%d %H:%M:%S IST"), now.strftime("%Y-%m-%d"),
            now.strftime("%H:%M:%S"), now.strftime("%A"),
            str(g("vid")), str(g("sid")), "Yes" if g("isNew") else "No",
            str(g("page")), str(g("title")), str(ref), ref_host or "direct",
            str(utm.get("source","")), str(utm.get("medium","")), str(utm.get("campaign","")),
            str(utm.get("term","")), str(utm.get("content","")),
            str(g("landing")), int(g("pagesInSession",0) or 0),
            int(g("tOnPage",0) or 0), int(g("engaged",0) or 0), int(g("scroll",0) or 0),
            int(g("clicks",0) or 0), cta_str, str(g("video")), str(g("form")),
            str(g("search")), int(g("rage",0) or 0),
            int(g("lcp",0) or 0), float(g("cls",0) or 0), int(g("inp",0) or 0),
            "%sx%s" % (g("vw",""), g("vh","")), "%sx%s" % (g("sw",""), g("sh","")),
            str(g("lang")), br, osn, dev, is_bot, ip,
            json.dumps(data.get("events") or [], separators=(",",":"))[:9000],
        ]

        svc = _va_sheets_service()
        if not svc:
            return jsonify({"ok": True})
        tab = "Visitor Analytics"
        try:
            existing = svc.spreadsheets().values().get(
                spreadsheetId=LOGIN_LOG_SHEET_ID, range="%s!A1:A1" % tab).execute()
            if not existing.get("values"):
                raise Exception("empty")
        except Exception:
            try:
                svc.spreadsheets().batchUpdate(
                    spreadsheetId=LOGIN_LOG_SHEET_ID,
                    body={"requests":[{"addSheet":{"properties":{"title":tab}}}]}).execute()
            except Exception:
                pass
            svc.spreadsheets().values().append(
                spreadsheetId=LOGIN_LOG_SHEET_ID, range="%s!A1" % tab,
                valueInputOption="RAW", body={"values":[_VA_HEADER]}).execute()
        svc.spreadsheets().values().append(
            spreadsheetId=LOGIN_LOG_SHEET_ID, range="%s!A1" % tab,
            valueInputOption="RAW", insertDataOption="INSERT_ROWS",
            body={"values":[row]}).execute()
    except Exception as e:
        log.warning("atrack failed: %s", e)
    return jsonify({"ok": True})

def _fetch_visitor_analytics() -> dict:
    """Aggregate the 'Visitor Analytics' tab for the admin dashboard."""
    from collections import Counter, defaultdict
    rows = []
    svc = _va_sheets_service()
    if svc:
        try:
            r = svc.spreadsheets().values().get(
                spreadsheetId=LOGIN_LOG_SHEET_ID, range="Visitor Analytics!A:AM").execute()
            rows = r.get("values", [])
        except Exception as e:
            log.warning("visitor analytics read failed: %s", e)
    idx = {name:i for i,name in enumerate(_VA_HEADER)}
    def c(row,name,d=""):
        i = idx.get(name, -1)
        return row[i] if (0 <= i < len(row)) else d
    data = rows[1:] if len(rows) > 1 else []
    human = [r for r in data if c(r,"Bot","No") != "Yes"]

    def to_int(v):
        try: return int(float(v))
        except Exception: return 0
    def to_float(v):
        try: return float(v)
        except Exception: return 0.0
    def fmt(s):
        m, sec = divmod(int(s),60)
        return (("%dm %ds" % (m,sec)) if m else ("%ds" % sec)) if s else "—"

    total_pageviews = len(human)
    visitors = set(c(r,"Visitor ID") for r in human if c(r,"Visitor ID"))
    sessions = set(c(r,"Session ID") for r in human if c(r,"Session ID"))
    unique_visitors = len(visitors)
    total_sessions = len(sessions)
    new_visitors = len(set(c(r,"Visitor ID") for r in human if c(r,"New Visitor")=="Yes" and c(r,"Visitor ID")))
    returning = max(unique_visitors - new_visitors, 0)

    sid_pages = defaultdict(int)
    for r in human:
        sid = c(r,"Session ID")
        if sid: sid_pages[sid] = max(sid_pages[sid], to_int(c(r,"Pages In Session")))
    bounce_sessions = sum(1 for p in sid_pages.values() if p <= 1)
    bounce_rate = round(bounce_sessions/total_sessions*100) if total_sessions else 0

    eng = [to_int(c(r,"Engaged Time (s)")) for r in human]
    avg_engaged = round(sum(eng)/len(eng)) if eng else 0
    tops = [to_int(c(r,"Time On Page (s)")) for r in human]
    avg_time = round(sum(tops)/len(tops)) if tops else 0

    by_day = Counter(c(r,"Date") for r in human if c(r,"Date"))
    series = sorted(by_day.items())[-30:]

    top_pages = Counter(c(r,"Page Title") or c(r,"Page URL") for r in human).most_common(15)
    top_landing = Counter(c(r,"Landing Page") for r in human if c(r,"Landing Page")).most_common(10)
    referrers = Counter((c(r,"Referrer Host") or "direct") for r in human).most_common(12)
    utm_source = Counter(c(r,"UTM Source") for r in human if c(r,"UTM Source")).most_common(10)
    utm_campaign = Counter(c(r,"UTM Campaign") for r in human if c(r,"UTM Campaign")).most_common(10)
    devices = Counter(c(r,"Device") for r in human if c(r,"Device")).most_common()
    oses = Counter(c(r,"OS") for r in human if c(r,"OS")).most_common()
    browsers = Counter(c(r,"Browser") for r in human if c(r,"Browser")).most_common(8)
    langs = Counter(c(r,"Language") for r in human if c(r,"Language")).most_common(8)

    sb = [["0–25%",0],["25–50%",0],["50–75%",0],["75–100%",0]]
    for r in human:
        v = to_int(c(r,"Max Scroll %"))
        if v < 25: sb[0][1]+=1
        elif v < 50: sb[1][1]+=1
        elif v < 75: sb[2][1]+=1
        else: sb[3][1]+=1

    cta_counts = Counter()
    for r in human:
        for part in (c(r,"CTA Clicks") or "").split(" · "):
            part = part.strip()
            if "×" in part:
                lbl, _, n = part.rpartition("×")
                cta_counts[lbl] += to_int(n)
    cta_top = cta_counts.most_common(15)

    order = {"":0,"open":1,"started":2,"submitted":3}
    sid_form = {}
    for r in human:
        sid = c(r,"Session ID"); st = c(r,"Form Stage")
        if sid and order.get(st,0) > order.get(sid_form.get(sid,""),0):
            sid_form[sid] = st
    form_funnel = {
        "opened": sum(1 for v in sid_form.values() if order.get(v,0)>=1),
        "started": sum(1 for v in sid_form.values() if order.get(v,0)>=2),
        "submitted": sum(1 for v in sid_form.values() if order.get(v,0)>=3),
    }
    video_sessions = len(set(c(r,"Session ID") for r in human if c(r,"Video")))

    search = Counter()
    for r in human:
        for term in (c(r,"Search Terms") or "").split(" | "):
            term = term.strip()
            if term: search[term]+=1
    search_top = search.most_common(15)

    rage_pages = Counter(); total_rage = 0
    for r in human:
        rg = to_int(c(r,"Rage Clicks"))
        if rg:
            rage_pages[c(r,"Page Title") or c(r,"Page URL")] += rg; total_rage += rg
    rage_top = rage_pages.most_common(10)

    def avg_nonzero(name, flt=False):
        vals = [(to_float(c(r,name)) if flt else to_int(c(r,name))) for r in human]
        vals = [v for v in vals if v]
        if not vals: return 0
        a = sum(vals)/len(vals)
        return round(a,3) if flt else round(a)
    cwv = {"lcp": avg_nonzero("LCP (ms)"), "cls": avg_nonzero("CLS",True), "inp": avg_nonzero("INP (ms)")}

    try:
        conversions = len(_read_access_requests())
    except Exception:
        conversions = 0
    conv_rate = round(conversions/unique_visitors*100,1) if unique_visitors else 0

    # ---- identity + reverse-IP company enrichment ----
    idmap = _va_identity_map()
    vid_ip = {}
    for r in human:
        v = c(r,"Visitor ID"); ipv = c(r,"IP")
        if v and ipv and v not in vid_ip: vid_ip[v] = ipv
    vid_company = {}; _ipc = {}
    for v, ipv in list(vid_ip.items())[:150]:
        co = _ipc.get(ipv)
        if co is None:
            co = _ip_company(ipv); _ipc[ipv] = co
        if co: vid_company[v] = co
    vid_pages = defaultdict(int)
    for r in human:
        v = c(r,"Visitor ID")
        if v: vid_pages[v] += 1
    companies = Counter()
    for v in visitors:
        co = ((idmap.get(v,{}) or {}).get("company") or vid_company.get(v) or "").strip()
        if co: companies[co] += 1
    top_companies = companies.most_common(15)
    identified = []
    for v in visitors:
        idn = idmap.get(v) or {}
        co = idn.get("company") or vid_company.get(v) or ""
        if idn or co:
            identified.append({"vid": v[:8], "name": idn.get("name",""), "email": idn.get("email",""),
                "company": co, "source": idn.get("source") or ("reverse-IP" if co else ""),
                "pages": vid_pages.get(v,0)})
    identified.sort(key=lambda x: -x["pages"]); identified = identified[:60]

    recent = []
    for r in reversed(data):
        recent.append({
            "ts": c(r,"Timestamp (IST)"), "vid": (c(r,"Visitor ID") or "")[:8],
            "page": c(r,"Page Title") or c(r,"Page URL"), "landing": c(r,"Landing Page"),
            "ref": c(r,"Referrer Host") or "direct", "device": c(r,"Device"),
            "engaged": fmt(to_int(c(r,"Engaged Time (s)"))), "scroll": c(r,"Max Scroll %"),
            "pages": c(r,"Pages In Session"), "new": c(r,"New Visitor"),
            "form": c(r,"Form Stage"), "bot": c(r,"Bot"),
            "who": (idmap.get(c(r,"Visitor ID")) or {}).get("name",""),
            "company": ((idmap.get(c(r,"Visitor ID")) or {}).get("company","") or vid_company.get(c(r,"Visitor ID"),"")),
        })

    return {
        "configured": bool(svc),
        "kpis": {
            "pageviews": total_pageviews, "visitors": unique_visitors, "sessions": total_sessions,
            "new": new_visitors, "returning": returning, "bounce_rate": bounce_rate,
            "avg_engaged": fmt(avg_engaged), "avg_time": fmt(avg_time),
            "conversions": conversions, "conv_rate": conv_rate,
            "video_sessions": video_sessions, "total_rage": total_rage,
            "identified": len(identified), "companies": len(companies),
        },
        "series": series, "top_pages": top_pages, "top_landing": top_landing,
        "referrers": referrers, "utm_source": utm_source, "utm_campaign": utm_campaign,
        "devices": devices, "oses": oses, "browsers": browsers, "langs": langs,
        "scroll": sb, "cta": cta_top, "form_funnel": form_funnel,
        "search": search_top, "rage": rage_top, "cwv": cwv, "recent": recent,
        "top_companies": top_companies, "identified": identified,
    }

@app.route("/admin/visitors")
@admin_required
def admin_visitors():
    """Admin-only anonymous visitor analytics dashboard."""
    return render_template("admin_visitors.html", user=_get_user())

@app.route("/admin/visitors/data")
@admin_required
def admin_visitors_data():
    """JSON aggregates for the visitor analytics dashboard."""
    return jsonify(_fetch_visitor_analytics())


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
    top_pages = sorted(page_counts.items(), key=lambda x: x[1], reverse=True)[:15]

    # Logins per day (last 14 days)
    login_days = Counter(col(r, 1) for r in login_data if col(r, 1))
    sorted_days = sorted(login_days.items())[-14:]

    # Browser breakdown (from logins)
    browser_counts = Counter(col(r, 10) for r in login_data if col(r, 10))
    browser_breakdown = browser_counts.most_common(5)

    # Device / OS breakdown + quick facts (from page views)
    device_breakdown = Counter(col(r,12) for r in page_data if col(r,12)).most_common(5)
    os_breakdown     = Counter(col(r,11) for r in page_data if col(r,11)).most_common(5)
    _day_counts      = Counter(col(r,3) for r in page_data if col(r,3))
    busiest_day      = list(_day_counts.most_common(1)[0]) if _day_counts else ["—", 0]
    _avg             = round(total_secs/total_page_views) if total_page_views else 0
    _am, _as         = divmod(_avg, 60)
    avg_view_fmt     = (f"{_am}m {_as}s" if _am else f"{_as}s") if _avg else "—"
    views_per_user   = round(total_page_views/unique_users, 1) if unique_users else 0

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
                   for r in reversed(page_data)]

    return dict(total_logins=total_logins, unique_users=unique_users,
                total_page_views=total_page_views, total_time_fmt=total_time_fmt,
                top_pages=top_pages, login_days=sorted_days,
                browser_breakdown=browser_breakdown, user_activity=user_activity,
                login_table=login_table, page_table=page_table,
                device_breakdown=device_breakdown, os_breakdown=os_breakdown,
                busiest_day=busiest_day, avg_view_fmt=avg_view_fmt,
                views_per_user=views_per_user)



def _read_access_requests(limit=300):
    """Read submitted access requests from the 'Demo Requests' sheet tab (newest first)."""
    try:
        import json as _j
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
        sa_str = os.environ.get("GOOGLE_SA_JSON", "")
        if not sa_str or not DEMO_REQUEST_SHEET_ID:
            return []
        creds = service_account.Credentials.from_service_account_info(
            _j.loads(sa_str), scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
        svc = build("sheets", "v4", credentials=creds, cache_discovery=False)
        r = svc.spreadsheets().values().get(
            spreadsheetId=DEMO_REQUEST_SHEET_ID, range="Demo Requests!A1:J2000").execute()
        rows = r.get("values", [])
        data = rows[1:] if len(rows) > 1 else []
        def c(row, i): return (row[i] if len(row) > i else "")
        out = [{"ts": c(x,0), "name": c(x,1), "email": c(x,2), "company": c(x,3),
                "interest": c(x,4), "message": c(x,5), "ip": c(x,6), "source": c(x,8),
                "vid": c(x,9)} for x in data]
        out.reverse()
        return out[:limit]
    except Exception as e:
        log.warning("access requests read failed: %s", e)
        return []


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

@app.route("/admin/requests")
@admin_required
def admin_requests():
    """Admin view of everyone who submitted the Request Access form."""
    reqs = _read_access_requests()
    return render_template("admin_requests.html", user=_get_user(),
                           requests=reqs, count=len(reqs))

@app.route("/admin/email-test")
@admin_required
def admin_email_test():
    """Admin-only SMTP diagnostic. Attempts a real send with subject 'Test Mail'
    and returns the exact result/error (password is never returned)."""
    host = os.environ.get("SMTP_HOST", "")
    user = os.environ.get("SMTP_USER", "")
    pwd  = os.environ.get("SMTP_PASS", "")
    port = os.environ.get("SMTP_PORT", "587")
    to = os.environ.get("DEMO_NOTIFY_EMAIL", "") or "krishna.ladha@position2.com, abhilash.dg@position2.com, sudheer.d@position2.com, sparikh@position2.com"
    gmail_sender = os.environ.get("GMAIL_SENDER", "")
    info = {"host": host or "(unset)", "port": port or "(unset)",
            "user": user or "(unset)", "pass_set": bool(pwd),
            "from": os.environ.get("SMTP_FROM", "") or user or "(unset)", "to": to,
            "gmail_sender": gmail_sender or "(unset)",
            "sa_json_set": bool(os.environ.get("GOOGLE_SA_JSON", "")),
            "method": "gmail_api" if (gmail_sender and os.environ.get("GOOGLE_SA_JSON", "")) else "smtp"}
    if info["method"] == "gmail_api":
        try:
            _gmail_api_send("Test Mail", "Test Mail - Gmail API diagnostic from the Intelligence platform. If you received this, email notifications work.", to, "", gmail_sender)
            info["result"] = "SENT OK"
        except Exception as e:
            info["result"] = "FAILED"; info["error"] = repr(e)
        return jsonify(info)
    try:
        import smtplib, ssl
        from email.message import EmailMessage
        p = int(port or 587)
        msg = EmailMessage()
        msg["Subject"] = "Test Mail"
        msg["From"] = os.environ.get("SMTP_FROM", "") or user
        msg["To"] = to
        msg.set_content("Test Mail - SMTP diagnostic from the Intelligence platform. If you received this, email notifications work.")
        ctx = ssl.create_default_context()
        with _force_ipv4():
            if p == 465:
                with smtplib.SMTP_SSL(host, p, timeout=15, context=ctx) as s:
                    s.login(user, pwd); s.send_message(msg)
            else:
                with smtplib.SMTP(host, p, timeout=15) as s:
                    s.starttls(context=ctx); s.login(user, pwd); s.send_message(msg)
        info["result"] = "SENT OK"
    except Exception as e:
        info["result"] = "FAILED"
        info["error"] = repr(e)
    return jsonify(info)




def _clean_industry(raw: str) -> str:
    """Strip JSON array brackets from industry field, return first value only."""
    if not raw or raw == "Unavailable":
        return "—"
    if raw.startswith('['):
        try:
            import json as _j
            lst = _j.loads(raw)
            return lst[0].strip() if lst else raw
        except Exception:
            # fallback: strip brackets and quotes manually
            return raw.strip('[]').split(',')[0].strip().strip('"\'')
    return raw




_ANON_CACHE = {"data": None, "ts": 0.0}
_ANON_GZ = {"ts": None, "raw": b"", "gz": b""}
_ANON_CACHE_TTL = 300  # seconds — Sheets reads are slow; serve cached data between refreshes

def _fetch_anon_visitors_data(force: bool = False) -> dict:
    """Fetch people + company data from the Anonymous Visitors Google Sheet (TTL-cached)."""
    now = time.time()
    if not force and _ANON_CACHE["data"] is not None and (now - _ANON_CACHE["ts"]) < _ANON_CACHE_TTL:
        return _ANON_CACHE["data"]
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
            "industry": _clean_industry(col(r, 8)),
            "website":  col(r, 10),
            "date":     time_str[:10] if time_str else "",
            "time_raw": time_str,
        })
    people_table.sort(key=lambda x: x.get("time_raw", ""), reverse=True)


    _result = dict(
        total_people=len(people_table),
        unique_companies=len(company_table),
        top_industries=top_industries,
        people_table=people_table,
        company_table=company_table,
    )
    _ANON_CACHE["data"] = _result
    _ANON_CACHE["ts"] = now
    return _result


@app.route("/gtm/anonymous-visitors")
@login_required
def anonymous_visitors():
    """Anonymous Visitors dashboard shell — loads data async."""
    return render_template("anonymous_visitors.html", user=_get_user())


@app.route("/gtm/anonymous-visitors/data")
@app.route("/ppc/anonymous-visitors/data")
@login_required
def anonymous_visitors_data():
    """JSON data endpoint for the Anonymous Visitors dashboard (gzipped, cached)."""
    force = request.args.get("fresh") in ("1", "true", "yes")
    data = _fetch_anon_visitors_data(force=force)
    # Serialize + compress once per data refresh (keyed to the cache timestamp).
    if _ANON_GZ["ts"] != _ANON_CACHE["ts"]:
        _ANON_GZ["raw"] = json.dumps(data, separators=(",", ":")).encode("utf-8")
        _ANON_GZ["gz"] = gzip.compress(_ANON_GZ["raw"], 6)
        _ANON_GZ["ts"] = _ANON_CACHE["ts"]
    use_gz = "gzip" in request.headers.get("Accept-Encoding", "").lower()
    resp = make_response(_ANON_GZ["gz"] if use_gz else _ANON_GZ["raw"])
    resp.headers["Content-Type"] = "application/json"
    resp.headers["Cache-Control"] = "private, max-age=60"
    resp.headers["Vary"] = "Accept-Encoding"
    if use_gz:
        resp.headers["Content-Encoding"] = "gzip"
    return resp




@app.route("/gtm/linkedin-scraper")
@login_required
def linkedin_scraper():
    """LinkedIn ABM Intelligence dashboard — Post & People Intelligence."""
    try:
        with open(os.path.join(os.path.dirname(__file__), "data", "linkedin.json"), encoding="utf-8") as _f:
            li_data = json.load(_f)
    except Exception:
        li_data = {"posts": [], "people": [], "companies": [], "company_lb": [], "stats": {}}
    return render_template("linkedin_scraper.html", user=_get_user(), li_data=li_data)

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
        <a href="/gtm">GTM</a><span class="bc-sep">›</span>
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
            f'<a class="card" href="/signal-tracker/{account_id}" '
            f'style="--accent:{accent};--glow:rgba(99,102,241,.25);'
            f'--thumb:{thumb};--accent-text:{accent}">'
            f'<div class="card-band"></div>'
            f'<div class="card-thumb"><div class="card-thumb-icon">{cfg["icon"]}</div>'
            f'<div class="card-badge"><span class="badge-dot"></span>Live</div></div>'
            f'<div class="card-body">'
            f'<div class="card-name">{cfg["name"]}</div>'
            f'<div class="card-desc">{cfg["description"]}</div>'
            f'<div class="card-footer">'
            f'<div class="stat"><span>{count}</span> companies</div>'
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


# ── Shared Sheets helper ──────────────────────────────────────────────────────

def _sheets_service():
    """Return an authenticated Google Sheets service, or raise on failure."""
    import json as _j
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    sa_str = os.environ.get("GOOGLE_SA_JSON", "")
    if not sa_str:
        raise RuntimeError("GOOGLE_SA_JSON env var not set")
    sa_info = _j.loads(sa_str)
    creds = service_account.Credentials.from_service_account_info(
        sa_info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


# ── Chatbot data functions ────────────────────────────────────────────────────

def _chatbot_get_anonymous_visitors(date_from=None, date_to=None, company=None,
                                     seniority=None, industry=None, limit=20):
    """Fetch targeted anonymous visitor rows for the chatbot."""
    try:
        svc = _sheets_service()
        r = svc.spreadsheets().values().get(
            spreadsheetId=ANON_VISITORS_SHEET_ID,
            range="People Enriched!A:L"
        ).execute()
        rows = r.get("values", [])
        if not rows:
            return {"status": "empty", "message": "Sheet returned no data", "people": []}

        # Header-based mapping — robust to column reordering
        headers = [h.strip().lower() for h in rows[0]]

        def _h(row, *names):
            """Get the first matching column value by header name."""
            for name in names:
                for i, h in enumerate(headers):
                    if name in h and i < len(row):
                        return row[i]
            return ""

        people = []
        for row in rows[1:]:
            name = _h(row, "name", "full name")
            if not name or name.strip().lower() in ("", "unavailable", "n/a"):
                continue
            time_str = _h(row, "time", "date", "timestamp", "visited", "last seen")
            industry_raw = _h(row, "industry", "sector")
            people.append({
                "name":     name,
                "title":    _h(row, "title", "job title", "position", "role"),
                "email":    _h(row, "email"),
                "company":  _h(row, "company", "organization", "employer", "account"),
                "location": _h(row, "location", "city", "region", "country"),
                "pages":    _h(row, "pages", "page", "url", "viewed"),
                "date":     time_str[:10] if time_str else "",
                "industry": _clean_industry(industry_raw),
                "website":  _h(row, "website", "domain", "web", "url"),
                "time_raw": time_str,
            })

        # Newest first
        people.sort(key=lambda x: x.get("time_raw", ""), reverse=True)

        total_before_filter = len(people)

        if date_from:
            people = [p for p in people if p["date"] >= date_from]
        if date_to:
            people = [p for p in people if p["date"] <= date_to]
        if company:
            c = company.lower()
            people = [p for p in people
                      if c in p.get("company", "").lower()
                      or c in p.get("website", "").lower()]
        if industry:
            people = [p for p in people
                      if industry.lower() in p.get("industry", "").lower()]
        if seniority:
            s = seniority.lower()
            _seniority_map = {
                "c-suite":   ["ceo", "cmo", "coo", "cto", "cfo", "cro", "cpo", "ciso", "chief"],
                "vp":        ["vp", "vice president"],
                "director":  ["director"],
                "manager":   ["manager"],
                "president": ["president"],
            }
            keywords = _seniority_map.get(s, [s])
            people = [p for p in people
                      if any(kw in p.get("title", "").lower() for kw in keywords)]

        result = people[:limit]
        # Industry breakdown
        industry_counts = dict(Counter(p["industry"] for p in people if p["industry"]).most_common(5))
        return {
            "status": "ok",
            "total_in_sheet": total_before_filter,
            "total_matching_filters": len(people),
            "returned": len(result),
            "top_industries": industry_counts,
            "people": [
                {
                    "name":         p["name"],
                    "title":        p["title"],
                    "company":      p["company"],
                    "industry":     p["industry"],
                    "location":     p["location"],
                    "date_visited": p["date"],
                    "pages_viewed": p["pages"],
                }
                for p in result
            ],
        }
    except Exception as e:
        return {"status": "error", "error": str(e),
                "hint": "Check that GOOGLE_SA_JSON is set and the sheet is accessible."}


def _chatbot_get_signal_tracker(account="healthcare", signal_type=None,
                                 company=None, severity=None, limit=20):
    """Query Signal Tracker SQLite for buying signals."""
    db_map = {
        "healthcare": Path(__file__).parent / "data" / "tracker.db",
        "csg":        Path(__file__).parent / "data" / "tracker_csg_v2.db",
    }
    db_path = db_map.get(account, db_map["healthcare"])
    if not db_path.exists():
        return {"error": f"Database not found for account '{account}'"}

    try:
        import sqlite3 as _sql
        conn = _sql.connect(str(db_path))
        conn.row_factory = _sql.Row

        conditions = ["a.dry_run = 0"]
        params: list = []
        if signal_type:
            conditions.append("a.signal_type = ?")
            params.append(signal_type)
        if severity:
            conditions.append("a.severity = ?")
            params.append(severity.upper())
        if company:
            conditions.append("c.name LIKE ?")
            params.append(f"%{company}%")

        where = " AND ".join(conditions)
        query = f"""
            SELECT c.name, c.domain, c.industry, c.city, c.state,
                   a.signal_type, a.signal_detail, a.severity, a.signal_date
            FROM alerts_sent a
            JOIN companies c ON a.apollo_id = c.apollo_id
            WHERE {where}
            ORDER BY a.signal_date DESC
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        conn.close()

        signals = [
            {
                "company":       r["name"],
                "domain":        r["domain"],
                "industry":      r["industry"],
                "location":      f"{r['city']}, {r['state']}".strip(", "),
                "signal_type":   r["signal_type"],
                "signal_detail": r["signal_detail"],
                "severity":      r["severity"],
                "signal_date":   r["signal_date"],
            }
            for r in rows
        ]
        return {"account": account, "total_returned": len(signals), "signals": signals}

    except Exception as e:
        return {"error": str(e)}


# ── OpenAI chatbot definitions ────────────────────────────────────────────────

CHATBOT_FUNCTIONS = [
    {
        "name": "get_anonymous_visitors",
        "description": (
            "Get people who visited position2.com — identified and enriched via Apollo. "
            "Use for: who visited, how many, which companies, seniority levels, industry breakdown, "
            "recent visitors, visitors in a date range."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "Start date YYYY-MM-DD"},
                "date_to":   {"type": "string", "description": "End date YYYY-MM-DD"},
                "company":   {"type": "string", "description": "Filter by company name or website domain"},
                "seniority": {
                    "type": "string",
                    "description": "Filter by seniority: 'c-suite', 'vp', 'director', 'manager', or any title keyword",
                },
                "industry":  {"type": "string", "description": "Filter by industry keyword"},
                "limit":     {"type": "integer", "description": "Max results (default 20)"},
            },
        },
    },
    {
        "name": "get_signal_tracker",
        "description": (
            "Get buying signals from the Signal Tracker — companies showing funding rounds, "
            "C-suite changes, M&A, news mentions, or IPO signals. "
            "Use for: prospect intelligence, hot accounts, recent high signals, outbound prioritization."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "account": {
                    "type": "string",
                    "enum": ["healthcare", "csg"],
                    "description": "Which tracker — 'healthcare' (1,251 companies) or 'csg' (294 companies). Default: healthcare",
                },
                "signal_type": {
                    "type": "string",
                    "description": (
                        "Filter by signal type: 'Funding Round', 'C-Suite Join', 'C-Suite Exit', "
                        "'Acquisition / M&A', 'News Mention', 'IPO Signal'"
                    ),
                },
                "company":  {"type": "string", "description": "Filter by company name"},
                "severity": {"type": "string", "enum": ["HIGH", "LOW"], "description": "HIGH = funding/C-suite/M&A; LOW = news"},
                "limit":    {"type": "integer", "description": "Max results (default 20)"},
            },
        },
    },
    {
        "name": "get_ad_intelligence_data",
        "description": (
            "Fetch competitor ad data from Ad Intelligence. "
            "Competitors tracked: Inspire Aesthetics, Dr. Dana MD, Sono Bello. "
            "Use for: what ads competitors are running, CTAs, ad formats, keywords targeted, "
            "messaging angles, active vs inactive ads, when ads were last seen."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "competitor": {"type": "string", "description": "Competitor name or domain (e.g. 'Inspire Aesthetics', 'sonobello')"},
                "ad_format":  {"type": "string", "enum": ["image", "text", "video"], "description": "Ad format filter"},
                "status":     {"type": "string", "enum": ["active", "inactive"], "description": "Ad status filter"},
                "keyword":    {"type": "string", "description": "Search word in headline/description/keywords"},
                "limit":      {"type": "integer", "description": "Max ads to return (default 20)"},
            },
        },
    },
]

_PPC_CTX_CACHE: dict = {"data": None, "ts": 0.0}
_PPC_CTX_TTL = 60    # seconds — refresh every 60s; keeps data fresh without hammering APIs


def _build_ppc_context() -> str:
    """
    Fetch ALL data from every source — no row limits.
    Cached for _PPC_CTX_TTL seconds so repeated chat messages are instant.
    """
    import time as _time
    now = _time.time()
    if _PPC_CTX_CACHE["data"] and now - _PPC_CTX_CACHE["ts"] < _PPC_CTX_TTL:
        return _PPC_CTX_CACHE["data"]

    parts = []

    # ── 1. Anonymous Visitors — ALL rows from BOTH tabs ────────────────────
    try:
        svc = _sheets_service()

        # ---- Tab 1: People Enriched (individual visitors) ----------------
        r_people = svc.spreadsheets().values().get(
            spreadsheetId=ANON_VISITORS_SHEET_ID,
            range="People Enriched!A:L"
        ).execute()
        people_rows = r_people.get("values", [])

        # Read header row to map columns by name
        headers = [h.strip().lower() for h in (people_rows[0] if people_rows else [])]

        def _hv(row, *names):
            for name in names:
                for i, h in enumerate(headers):
                    if name in h and i < len(row):
                        v = row[i].strip()
                        if v and v.lower() not in ("", "unavailable", "n/a", "none"):
                            return v
            return ""

        people_out = []
        for row in people_rows[1:]:
            name = _hv(row, "name", "full name")
            if not name:
                continue
            time_str = _hv(row, "time", "date", "timestamp", "identified", "visited", "seen", "first")
            people_out.append({
                "name":     name,
                "title":    _hv(row, "title", "job title", "position", "role"),
                "email":    _hv(row, "email"),
                "location": _hv(row, "location", "city", "country", "region"),
                "pages":    _hv(row, "pages", "page", "url"),
                "date":     time_str[:10] if time_str else "",
                "industry": _clean_industry(_hv(row, "industry", "sector", "vertical")),
                "website":  _hv(row, "website", "domain", "company website", "web"),
                "time_raw": time_str,
            })
        people_out.sort(key=lambda x: x.get("time_raw", ""), reverse=True)

        # ---- Tab 2: Visitors By Company (company-level data) --------------
        r_comp = svc.spreadsheets().values().get(
            spreadsheetId=ANON_VISITORS_SHEET_ID,
            range="Visitors By Company!A:J"
        ).execute()
        comp_rows = r_comp.get("values", [])
        comp_hdrs = [h.strip().lower() for h in (comp_rows[0] if comp_rows else [])]

        def _cv(row, *names):
            for name in names:
                for i, h in enumerate(comp_hdrs):
                    if name in h and i < len(row):
                        v = row[i].strip()
                        if v and v.lower() not in ("", "unavailable", "n/a", "none"):
                            return v
            return ""

        companies_out = []
        for row in comp_rows[1:]:
            co = _cv(row, "company", "name", "organization") or (row[0].strip() if row else "")
            if not co:
                continue
            companies_out.append({
                "company":   co,
                "website":   _cv(row, "website", "domain", "url") or (row[2].strip() if len(row) > 2 else ""),
                "location":  " ".join(filter(None, [_cv(row, "city"), _cv(row, "state"), _cv(row, "country")])),
                "industry":  _clean_industry(_cv(row, "industry", "sector")),
                "employees": _cv(row, "employee", "size", "headcount"),
                "revenue":   _cv(row, "revenue", "arr", "mrr"),
            })

        # Company lines — fields explicitly labelled so GPT never guesses column order
        c_lines = []
        for i, c in enumerate(companies_out, 1):
            c_lines.append(
                f"{i}. Company={c['company']} | Website={c['website']} | "
                f"Industry={c['industry']} | Location={c['location']} | "
                f"Employees={c['employees']} | Revenue={c['revenue']}"
            )

        # People lines — completely separate block with different field set
        p_lines = []
        for i, p in enumerate(people_out, 1):
            p_lines.append(
                f"{i}. Name={p['name']} | Title={p['title']} | "
                f"CompanyWebsite={p['website']} | Industry={p['industry']} | "
                f"Location={p['location']} | DateVisited={p['date']}"
            )

        industry_counts = dict(Counter(p["industry"] for p in people_out if p["industry"]).most_common(8))

        # COMPANIES block comes FIRST so GPT reads it first for "company" queries
        parts.append(
            f"=== VISITOR DATA ===\n"
            f"Summary: {len(people_out)} individual visitors from {len(companies_out)} unique companies\n"
            f"Top industries: {industry_counts}\n\n"
            f"--- SECTION A: COMPANIES THAT VISITED ({len(companies_out)} unique companies) ---\n"
            f"USE THIS SECTION when asked about COMPANIES. Columns: Company, Website, Industry, Location, Employees, Revenue\n"
            + "\n".join(c_lines)
            + f"\n\n--- SECTION B: INDIVIDUAL VISITORS ({len(people_out)} people, newest first) ---\n"
            f"USE THIS SECTION when asked about VISITORS or PEOPLE. Columns: Name, Title, CompanyWebsite, Industry, Location, DateVisited\n"
            + "\n".join(p_lines)
        )

    except Exception as e:
        parts.append(f"=== ANONYMOUS VISITORS ===\n⚠ Could not fetch: {e}")

    # ── 2. Signal Tracker — ALL signals, no limit ─────────────────────────
    try:
        import sqlite3 as _sql

        db_path = Path(__file__).parent / "data" / "tracker.db"
        if not db_path.exists():
            parts.append("=== SIGNAL TRACKER ===\n⚠ Database not on Railway — commit data/tracker.db to git")
        else:
            conn = _sql.connect(str(db_path))
            conn.row_factory = _sql.Row
            try:
                all_sigs = conn.execute("""
                    SELECT c.name, c.domain, c.industry, c.city, c.state,
                           a.signal_type, a.signal_detail, a.severity, a.signal_date
                    FROM alerts_sent a
                    JOIN companies c ON a.apollo_id = c.apollo_id
                    WHERE a.dry_run = 0
                    ORDER BY a.signal_date DESC
                """).fetchall()
            finally:
                conn.close()

            sig_counts = dict(Counter(r["signal_type"] for r in all_sigs).most_common())
            comp_count = len({r["name"] for r in all_sigs})

            sig_lines = []
            for r in all_sigs:
                date   = (r["signal_date"] or "")[:16]
                detail = (r["signal_detail"] or "")[:120]
                city   = r["city"] or ""
                state  = r["state"] or ""
                loc    = ", ".join(filter(None, [city, state]))
                sig_lines.append(
                    f"• {r['name']} | {r['industry']} | {loc} | "
                    f"{r['signal_type']} [{r['severity']}] | {date} | {detail}"
                )

            parts.append(
                f"=== SIGNAL TRACKER (Healthcare — 1,251 companies monitored) ===\n"
                f"Total signals: {len(all_sigs)} across {comp_count} companies\n"
                f"By type: {sig_counts}\n\n"
                f"--- ALL SIGNALS (newest first) ---\n"
                + "\n".join(sig_lines)
            )

    except Exception as e:
        parts.append(f"=== SIGNAL TRACKER ===\n⚠ Could not fetch: {e}")

    # ── 3. Ad Intelligence — ALL ads ─────────────────────────────────────
    try:
        a = get_ad_intelligence_data(limit=5000)   # effectively unlimited
        if a.get("status") == "ok":
            ad_lines = []
            for ad in a.get("ads", []):
                ad_lines.append(
                    f"• {ad['competitor']} | {ad['format']} | {ad['status']} | "
                    f"headline: '{ad['headline']}' | CTA: {ad['cta']} | "
                    f"keywords: {ad['keywords'][:80]} | "
                    f"angle: {ad['messaging_angle'][:60]} | "
                    f"first: {ad['first_shown']} | last: {ad['last_shown']}"
                )
            parts.append(
                f"=== AD INTELLIGENCE ===\n"
                f"Total ads tracked: {a['total_in_sheet']}\n"
                f"By competitor: {a['by_competitor']}\n"
                f"By format: {a['by_format']}\n"
                f"By status: {a['by_status']}\n"
                f"Top CTAs: {a['top_ctas']}\n"
                f"Top keywords: {a['top_keywords']}\n\n"
                f"--- ALL ADS ---\n"
                + "\n".join(ad_lines)
            )
        else:
            fix = a.get("fix", "")
            parts.append(
                f"=== AD INTELLIGENCE ===\n⚠ {a.get('error','Error')}\n"
                + (f"ACTION: {fix}" if fix else "")
            )
    except Exception as e:
        parts.append(f"=== AD INTELLIGENCE ===\n⚠ Could not fetch: {e}")

    ctx = "\n\n" + "\n\n".join(parts)
    _PPC_CTX_CACHE["data"] = ctx
    _PPC_CTX_CACHE["ts"] = now
    return ctx


@app.route("/api/ppc-chat", methods=["POST"])
@login_required
def ppc_chat():
    """PPC AI assistant — context injection."""
    from openai import OpenAI
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return jsonify({"answer": "⚠️ Add `OPENAI_API_KEY` to Railway Variables."}), 200

    oai = OpenAI(api_key=api_key)

    body         = request.json or {}
    user_message = body.get("message", "").strip()
    history      = body.get("history", [])[-12:]
    memories     = body.get("memories", [])
    source_text  = body.get("source_text", "")   # previous AI response to reformat
    export_fmt   = body.get("export_format", "")  # "csv", "excel", "table", "json", etc.

    # ── Attached file (optional) ────────────────────────────────────────────
    file_name     = body.get("file_name", "")
    file_content  = body.get("file_content", "")
    file_is_image = body.get("file_is_image", False)
    file_mime     = body.get("file_mime", "image/png")
    file_base64   = body.get("file_base64", "")
    file_truncated= body.get("file_truncated", False)
    has_file      = bool(file_name)

    if not user_message and not has_file:
        return jsonify({"answer": "Please ask a question."}), 200

    if not user_message and has_file:
        user_message = f"Please analyse the attached file '{file_name}' and summarise the key information."

    # ── Detect format keyword in the user's message itself ───────────────────
    _fmt_map = {
        r'\bexcel\b|\bxlsx\b':                              'excel',
        r'\bcsv\b':                                         'csv',
        r'\bjson\b':                                        'json',
        r'\btable format\b|\bin a table\b|\bspreadsheet\b': 'table',
        r'\bbullet\b|\blist format\b':                      'bullet',
    }
    if not export_fmt:
        for pattern, fmt in _fmt_map.items():
            if re.search(pattern, user_message, re.I):
                export_fmt = fmt
                break

    # ── FORMAT / EXPORT REQUEST — handle separately, no PPC context needed ──
    # Triggered when user asks to reformat a *previous* response
    if export_fmt and source_text:
        fmt_instructions = {
            "csv":   "Convert the data to clean CSV with a header row. Use comma separators. Output ONLY the CSV — no explanation, no markdown, no code block.",
            "excel": "Convert the data to clean CSV with a header row (Excel-compatible). Use comma separators. Output ONLY the CSV data — no explanation, no markdown, no code block.",
            "table": "Format the data as a clean markdown table with aligned columns and a header row. Output ONLY the table.",
            "json":  "Convert the data to valid JSON array of objects. Output ONLY the JSON — no explanation, no markdown.",
            "bullet":"Reformat the data as a clean bulleted list. Output ONLY the list.",
        }
        instruction = fmt_instructions.get(export_fmt, f"Reformat the data as {export_fmt}. Output ONLY the reformatted data.")
        reformat_messages = [
            {"role": "system", "content":
             "You are a data formatter. Your only job is to reformat data exactly as instructed. "
             "Never add explanations, apologies, or commentary. Output ONLY the requested format."},
            {"role": "user", "content": f"{instruction}\n\nDATA TO REFORMAT:\n{source_text}"},
        ]
        try:
            formatted, _m = _vimi_completion(oai, reformat_messages, 2000, temperature=0)
            is_csv = export_fmt in ("csv", "excel")
            return jsonify({"answer": formatted, "is_export": True,
                            "export_format": export_fmt, "is_csv": is_csv})
        except Exception as e:
            return jsonify({"answer": f"Export failed: {str(e)}"}), 200

    # ── Pre-fetch all live data (cached 4 min) ─────────────────────────────
    ppc_context = _build_ppc_context()

    now_ist    = datetime.now(IST)
    today      = now_ist.strftime("%Y-%m-%d")
    week_start = (now_ist - timedelta(days=now_ist.weekday())).strftime("%Y-%m-%d")

    # Build format instruction if a format was requested in this message
    fmt_instruction = ""
    if export_fmt:
        fmt_map = {
            "excel": "Return the data as clean CSV (Excel-compatible) with a header row. Only output the CSV rows — no prose, no markdown fences.",
            "csv":   "Return the data as clean CSV with a header row. Only output the CSV rows — no prose, no markdown fences.",
            "json":  "Return the data as a valid JSON array of objects. Only output the JSON.",
            "table": "Return the data formatted as a clean markdown table with headers.",
        }
        fmt_instruction = f"\n\nOUTPUT FORMAT REQUIRED: {fmt_map.get(export_fmt, f'Format the output as {export_fmt}.')}\nDo NOT include any explanation before or after the data."

    system_prompt = f"""You are the PPC Intelligence Assistant for Position2, a B2B marketing agency.
You are highly intelligent, direct, and always give complete answers in one response — no follow-up questions.

TODAY: {today} | THIS WEEK: {week_start} to {today} | YESTERDAY: {(now_ist - timedelta(days=1)).strftime('%Y-%m-%d')}
"Last N" = first N rows of the relevant list (data is newest-first).

INSTRUCTIONS:
- Answer every question fully using the live data below. Never say "I can't access" when data is provided.
- Never ask for clarification when the request is clear. Deliver the answer immediately.
- "Excel format", "CSV", "table", "JSON" = format the output that way. Nothing to do with any Google Sheet.
- If a data section shows ⚠ Error, say that source is unavailable but answer from what's available.
- Be analytical: bold **key numbers**, use bullets for lists, lead with the most useful insight.
- For general PPC/marketing questions, answer from knowledge directly.

DATA SECTION RULES — NEVER MIX THESE:
- Asked about COMPANIES → use SECTION A only. Columns: Company Name, Website, Industry, Location, Employees, Revenue. Never include individual people names.
- Asked about VISITORS/PEOPLE → use SECTION B only. Columns: Name, Title, Company Website, Industry, Location, Date Visited.
- "last 10 companies" = first 10 rows of SECTION A. "last 10 visitors" = first 10 rows of SECTION B.

CSV/EXCEL EXPORT RULES:
- Output ONLY the CSV rows. No intro text, no explanation, no markdown fences, no code blocks.
- Use meaningful headers: "Company Name", "Website", "Industry", "Location", "Employees", "Revenue" — never "field1", "field2".
- Include ONLY the columns that make sense for the query (e.g. company query = 6 columns, no extra).
- Replace em-dashes (—) with a hyphen or leave blank. Quote values that contain commas.{fmt_instruction}

══════════════════════════ LIVE DATA ══════════════════════════
{ppc_context}
═══════════════════════════════════════════════════════════════
"""
    if memories:
        system_prompt += "\n\nSAVED MEMORIES (always apply these):\n" + \
                         "\n".join(f"• {m}" for m in memories[:30])

    messages = [{"role": "system", "content": system_prompt}]
    messages += history

    # ── Build user turn — plain text or multimodal (image) ─────────────────
    if file_is_image and file_base64:
        # Vision: send image alongside the question
        trunc_note = " (image sent in full)"
        messages.append({
            "role": "user",
            "content": [
                {"type": "text",
                 "text": f"I've attached an image: '{file_name}'\n{user_message}"},
                {"type": "image_url",
                 "image_url": {"url": f"data:{file_mime};base64,{file_base64}", "detail": "high"}},
            ],
        })
    elif file_content:
        # Text file: prepend content clearly labelled
        trunc_note = f"\n\n⚠️ File was large — showing first {_MAX_TEXT_CHARS:,} characters." if file_truncated else ""
        file_block = (
            f"📎 ATTACHED FILE: {file_name}{trunc_note}\n"
            f"{'─'*60}\n"
            f"{file_content}\n"
            f"{'─'*60}\n\n"
            f"User question about this file: {user_message}"
        )
        messages.append({"role": "user", "content": file_block})
    else:
        messages.append({"role": "user", "content": user_message})

    try:
        answer, _m = _vimi_completion(oai, messages, 2000, temperature=0.1)
        return jsonify({
            "answer":          answer,
            "detected_format": export_fmt or "",
            "is_export":       bool(export_fmt and not source_text),
            "is_csv":          export_fmt in ("csv", "excel"),
        })
    except Exception as e:
        log.warning("PPC chat error: %s", e)
        return jsonify({"answer": f"Something went wrong: {str(e)}"}), 200


# ── File extraction helpers ───────────────────────────────────────────────────

def _extract_pdf(data: bytes) -> str:
    import pdfplumber, io
    parts = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text()
            if text and text.strip():
                parts.append(f"[Page {i}]\n{text.strip()}")
            # Extract tables too
            for table in page.extract_tables():
                rows = [" | ".join(str(c) if c else "" for c in row) for row in table if any(c for c in row)]
                if rows:
                    parts.append("\n".join(rows))
    return "\n\n".join(parts) or "(No text could be extracted from this PDF)"


def _extract_docx(data: bytes) -> str:
    import docx, io
    doc = docx.Document(io.BytesIO(data))
    parts = []
    for para in doc.paragraphs:
        if para.text.strip():
            style = para.style.name if para.style else ""
            prefix = "# " if "Heading 1" in style else "## " if "Heading" in style else ""
            parts.append(prefix + para.text)
    for table in doc.tables:
        for row in table.rows:
            cells = " | ".join(c.text.strip() for c in row.cells)
            if cells.strip():
                parts.append(cells)
    return "\n".join(parts) or "(No text found in document)"


def _extract_xlsx(data: bytes) -> str:
    import openpyxl, io
    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
    parts = []
    for sheet in wb.worksheets:
        parts.append(f"=== Sheet: {sheet.title} ===")
        for row in sheet.iter_rows(values_only=True):
            vals = [str(v).strip() if v is not None else "" for v in row]
            if any(v for v in vals):
                parts.append(" | ".join(vals))
    return "\n".join(parts) or "(No data found in spreadsheet)"


def _extract_pptx(data: bytes) -> str:
    from pptx import Presentation
    import io
    prs = Presentation(io.BytesIO(data))
    parts = []
    for i, slide in enumerate(prs.slides, 1):
        slide_parts = []
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                slide_parts.append(shape.text.strip())
        if slide_parts:
            parts.append(f"--- Slide {i} ---\n" + "\n".join(slide_parts))
    return "\n\n".join(parts) or "(No text found in presentation)"


_IMAGE_EXTS = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}
_IMAGE_MIME  = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                "gif": "image/gif",  "webp": "image/webp", "bmp": "image/bmp"}
_MAX_FILE_BYTES  = 20 * 1024 * 1024   # 20 MB
_MAX_TEXT_CHARS  = 40_000             # truncate extracted text at 40k chars


@app.route("/api/ppc-upload", methods=["POST"])
@login_required
def ppc_upload():
    """Parse an uploaded file and return its extracted content for the chatbot."""
    if "file" not in request.files:
        return jsonify({"error": "No file provided"}), 400

    f        = request.files["file"]
    filename = f.filename or "upload"
    ext      = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    data     = f.read()

    if len(data) > _MAX_FILE_BYTES:
        return jsonify({"error": f"File too large — max {_MAX_FILE_BYTES // 1024 // 1024} MB"}), 400
    if not ext:
        return jsonify({"error": "File has no extension — cannot detect type"}), 400

    try:
        # ── Images → return base64 for vision API ──────────────────────────
        if ext in _IMAGE_EXTS:
            import base64 as _b64
            b64  = _b64.b64encode(data).decode()
            mime = _IMAGE_MIME.get(ext, "image/png")
            return jsonify({"is_image": True, "name": filename, "mime": mime, "base64": b64})

        # ── Text-based files → extract and return text ─────────────────────
        if ext == "pdf":
            content = _extract_pdf(data)
        elif ext in ("docx", "doc"):
            content = _extract_docx(data)
        elif ext in ("xlsx", "xls"):
            content = _extract_xlsx(data)
        elif ext in ("pptx", "ppt"):
            content = _extract_pptx(data)
        elif ext in ("csv", "txt", "md", "json", "xml", "html", "htm"):
            content = data.decode("utf-8", errors="replace")
        else:
            return jsonify({"error": f"Unsupported file type .{ext}. Supported: PDF, DOCX, XLSX, PPTX, CSV, TXT, PNG, JPG, and more."}), 400

        # Truncate if huge
        truncated = False
        if len(content) > _MAX_TEXT_CHARS:
            content   = content[:_MAX_TEXT_CHARS]
            truncated = True

        return jsonify({
            "is_image": False,
            "name":      filename,
            "content":   content,
            "chars":     len(content),
            "truncated": truncated,
        })

    except Exception as e:
        log.warning("ppc_upload error for %s: %s", filename, e)
        return jsonify({"error": f"Could not parse '{filename}': {str(e)}"}), 500



# ── Signal Tracker Insights API ──────────────────────────────────────────────

_REVENUE_KEYS = {"est_value", "opportunity", "revenue_impact", "pipeline_estimate",
                 "estimated_value", "pipeline_value", "deal_size", "contract_value"}

def _responses_web_search(oai, model, input_msgs, max_tokens):
    """Call the Responses API with web search, trying both known tool-type names
    ('web_search' and the older 'web_search_preview'). Returns (text, True) on
    success or (None, False) if web search is unavailable on this SDK/model."""
    for _tt in ("web_search", "web_search_preview"):
        try:
            resp = oai.responses.create(
                model=model, tools=[{"type": _tt}], input=input_msgs, max_output_tokens=max_tokens)
            txt = (getattr(resp, "output_text", "") or "").strip()
            if txt:
                return txt, True
        except Exception as we:
            log.warning("web search via '%s' unavailable: %s", _tt, we)
    return None, False


def _vimi_model_chain():
    """Strongest-first model chain: OPENAI_INSIGHTS_MODEL > gpt-5.4 (ChatGPT 5.4) > OPENAI_MODEL/gpt-4o-mini."""
    chain = []
    for m in (os.environ.get("OPENAI_INSIGHTS_MODEL"), "gpt-5.4",
              os.environ.get("OPENAI_MODEL", "gpt-4o-mini")):
        if m and m not in chain:
            chain.append(m)
    return chain


def _vimi_completion(oai, messages, max_tokens, temperature=None):
    """Plain-text chat completion on the primary Vimi model (GPT-5.4) with graceful
    fallback down the model chain; retries without temperature if a model rejects it.
    Returns (text, model_used)."""
    last_err = None
    for model in _vimi_model_chain():
        attempts = [{"temperature": temperature}] if temperature is not None else []
        attempts.append({})
        for kw in attempts:
            try:
                resp = oai.chat.completions.create(
                    model=model, messages=messages,
                    max_completion_tokens=max_tokens, **kw)
                txt = (resp.choices[0].message.content or "").strip()
                if txt:
                    return txt, model
            except Exception as e:
                last_err = e
                log.warning("vimi: completion on '%s' (%s) failed: %s", model, kw, e)
    raise last_err if last_err else RuntimeError("no usable OpenAI model")


def _vimi_chat_json(oai, messages, max_tokens):
    """Chat completion in strict JSON mode, trying the strongest model first.
    Returns (raw_text, model_used)."""
    last_err = None
    for model in _vimi_model_chain():
        try:
            resp = oai.chat.completions.create(
                model=model, messages=messages,
                max_completion_tokens=max_tokens,
                response_format={"type": "json_object"})
            txt = (resp.choices[0].message.content or "").strip()
            if txt:
                return txt, model
        except Exception as e:
            last_err = e
            log.warning("vimi: model '%s' failed, trying next: %s", model, e)
    raise last_err if last_err else RuntimeError("no usable OpenAI model")


def _strip_revenue_fields(obj):
    """Recursively remove all revenue / pipeline-value fields from GPT output."""
    if isinstance(obj, dict):
        return {k: _strip_revenue_fields(v) for k, v in obj.items() if k not in _REVENUE_KEYS}
    if isinstance(obj, list):
        return [_strip_revenue_fields(x) for x in obj]
    return obj

@app.route("/api/insights-meta/<account_id>")
@login_required
def insights_meta(account_id):
    import sqlite3
    from pathlib import Path
    db_map = {"healthcare": Path(__file__).parent/"data"/"tracker.db",
              "csg":        Path(__file__).parent/"data"/"tracker_csg_v2.db"}
    db_path = db_map.get(account_id)
    if not db_path or not db_path.exists():
        return jsonify({"error": "Unknown account"}), 404
    try:
        conn = sqlite3.connect(str(db_path)); conn.row_factory = sqlite3.Row
        industries   = [r[0] for r in conn.execute(
            "SELECT DISTINCT industry FROM companies WHERE industry IS NOT NULL AND industry!=''  ORDER BY industry LIMIT 60"
        ).fetchall()]
        signal_types = [r[0] for r in conn.execute(
            "SELECT DISTINCT signal_type FROM alerts_sent WHERE dry_run=0 ORDER BY signal_type"
        ).fetchall()]
        counts = {r[0]: r[1] for r in conn.execute(
            "SELECT signal_type, COUNT(*) cnt FROM alerts_sent WHERE dry_run=0 GROUP BY signal_type ORDER BY cnt DESC"
        ).fetchall()}
        total     = conn.execute("SELECT COUNT(*) FROM alerts_sent WHERE dry_run=0").fetchone()[0]
        companies = conn.execute("SELECT COUNT(DISTINCT apollo_id) FROM alerts_sent WHERE dry_run=0").fetchone()[0]
        conn.close()
        return jsonify({"industries": industries, "signal_types": signal_types,
                        "counts": counts, "total_signals": total, "total_companies": companies})
    except Exception as e:
        return jsonify({"error": str(e)})


# ── Signal importance scoring (drives what the Insights tab surfaces) ──────────
# Single source of truth shared with weekly_digest.py.
from tracker.signal_score import SIGNAL_WEIGHTS, signal_importance as _signal_importance

# Only signals at/above this score reach the Insights tab; the rest are noise.
INSIGHTS_MIN_SCORE = 6.0
INSIGHTS_MAX_SIGNALS = 120

@app.route("/api/insights/<account_id>")
@login_required
def insights_generate(account_id):
    import sqlite3, re as _re
    from pathlib import Path
    db_map = {"healthcare": Path(__file__).parent/"data"/"tracker.db",
              "csg":        Path(__file__).parent/"data"/"tracker_csg_v2.db"}
    db_path = db_map.get(account_id)
    if not db_path or not db_path.exists():
        return jsonify({"error": "Unknown account"}), 404
    api_key = os.environ.get("OPENAI_API_KEY","")
    if not api_key:
        return jsonify({"error": "OpenAI API key not configured"}), 500

    signal_types = request.args.getlist("signal_type")
    severities   = request.args.getlist("severity")
    days         = int(request.args.get("days", 90))
    industry     = request.args.get("industry","")

    try:
        conn = sqlite3.connect(str(db_path)); conn.row_factory = sqlite3.Row
        conds = ["a.dry_run = 0"]; params = []
        if signal_types:
            conds.append("a.signal_type IN (%s)" % ",".join("?"*len(signal_types))); params.extend(signal_types)
        if severities:
            conds.append("a.severity IN (%s)" % ",".join("?"*len(severities))); params.extend(severities)
        if days > 0:
            conds.append("a.signal_date >= date('now',?)"); params.append("-%d days" % days)
        if industry:
            conds.append("c.industry LIKE ?"); params.append("%"+industry+"%")
        where = " AND ".join(conds)
        rows = conn.execute(
            "SELECT c.name,c.domain,c.industry,c.city,c.state,"
            "a.signal_type,a.signal_detail,a.severity,a.signal_date "
            "FROM alerts_sent a JOIN companies c ON a.apollo_id=c.apollo_id "
            "WHERE "+where+" ORDER BY CASE a.severity WHEN 'HIGH' THEN 1 WHEN 'MEDIUM' THEN 2 ELSE 3 END,"
            "a.signal_date DESC LIMIT 200", params
        ).fetchall()
        conn.close()
        if not rows:
            return jsonify({"error": "No signals found for those filters."}), 200

        signals = [dict(r) for r in rows]
        # Deterministic importance score per signal, + multi-intent company bonus,
        # then keep only the important ones for the Insights tab.
        _types_by_co = {}
        for s in signals:
            _types_by_co.setdefault(s["name"], set()).add(s["signal_type"])
        for s in signals:
            base = _signal_importance(s["signal_type"], s["severity"], s["signal_date"])
            if len(_types_by_co.get(s["name"], ())) >= 2:
                base += 3  # multi-intent buying-window bonus
            s["_score"] = round(base, 1)
        signals.sort(key=lambda s: s["_score"], reverse=True)
        signals = [s for s in signals if s["_score"] >= INSIGHTS_MIN_SCORE][:INSIGHTS_MAX_SIGNALS]
        if not signals:
            return jsonify({"error": "No sufficiently important signals for those filters."}), 200
        n_sig = len(signals); n_co = len(set(s["name"] for s in signals))
        acct  = "Healthcare" if account_id == "healthcare" else "CSG"

        by_co = {}
        for s in signals:
            co = s["name"]
            if co not in by_co:
                by_co[co] = {"domain": s.get("domain",""), "industry": s.get("industry",""), "sigs":[]}
            by_co[co]["sigs"].append(s)

        from datetime import date as _date
        _today = _date.today()
        def _age_days(d):
            try:
                return (_today - _date.fromisoformat(str(d)[:10])).days
            except Exception:
                return 9999

        type_counts, ind_counts = {}, {}
        for s in signals:
            type_counts[s["signal_type"]] = type_counts.get(s["signal_type"], 0) + 1
            if s.get("industry"):
                ind_counts[s["industry"]] = ind_counts.get(s["industry"], 0) + 1

        ctx_lines = []
        multi_intent = 0
        for co, info in sorted(by_co.items(),
            key=lambda x: -sum(s.get("_score",0) for s in x[1]["sigs"]))[:80]:
            sigs = info["sigs"]
            stypes = sorted(set(s["signal_type"] for s in sigs))
            recent = sum(1 for s in sigs if _age_days(s["signal_date"]) <= 30)
            momentum = "RISING" if recent > len(sigs) - recent else ("ACTIVE" if recent else "COOLING")
            flags = []
            if len(stypes) >= 2:
                flags.append("MULTI-INTENT")
                multi_intent += 1
            if any(_age_days(s["signal_date"]) <= 7 for s in sigs):
                flags.append("FRESH<7d")
            sig_str = " | ".join(
                "%s(%s,%s,score=%s)%s" % (s["signal_type"], s["severity"], s["signal_date"], s.get("_score","?"),
                    ": "+s["signal_detail"][:140] if s.get("signal_detail") else "")
                for s in sorted(sigs, key=lambda x: x.get("_score",0), reverse=True)[:6])
            ctx_lines.append("[%s | %s | %s] %d sigs, %s%s — %s" % (
                co, info["domain"], info["industry"], len(sigs), momentum,
                (" " + ",".join(flags)) if flags else "", sig_str))

        stats_lines = (
            "DATASET STATS: %d signals across %d companies. "
            "Signal-type distribution: %s. Top industries: %s. "
            "Multi-intent companies (2+ distinct signal types): %d."
            % (n_sig, n_co,
               ", ".join("%s:%d" % kv for kv in sorted(type_counts.items(), key=lambda x: -x[1])),
               ", ".join("%s:%d" % kv for kv in sorted(ind_counts.items(), key=lambda x: -x[1])[:8]),
               multi_intent))

        schema = (
            '{"headline":"one punchy 8-12 word headline capturing this week in the market",'
            +'  "brief":"3-sentence leadership brief naming hottest companies, dominant signal pattern, ONE sales action.",'
            +'  "vimi_take":"one bold, non-obvious strategic observation from the data that a human analyst would likely miss",'
            +'  "week_priority":[{"rank":1,"company":"","domain":"","signal":"specific signal","pitch":"exact service+why","service":"SEO|PPC|Content|Brand|RevOps","call_timing":"Call today|Call this week|Warm email first","hook":"one-line conversation opener citing the signal"}],'
            +'  "market_pulse":["specific data-backed observation citing companies"],'
            +'  "strategic_moves":[{"move":"title","rationale":"signal-backed reason","impact":"qualitative business impact, no dollar figures","owner":"BDR|Account Exec|Marketing|Leadership"}],'
            +'  "pipeline":[{"name":"","domain":"","intent_score":85,"momentum":"rising|steady|cooling","signals":["type"],"why_now":"","service_fit":["SEO"],"contact_title":"best job title to approach","hook":"one-line opener citing their signal","next_step":""}],'
            +'  "actions":[{"rank":1,"type":"outreach","company":"","action":"","reason":"","deadline":"Today","urgency":"HIGH"}],'
            +'  "outreach":[{"company":"","domain":"","timing":"now","signal_hook":"","subject":"","opening":"","talking_points":[""],"cta":""}],'
            +'  "themes":[{"theme":"","count":0,"companies":[""],"campaign_angle":""}],'
            +'  "risks":[{"company":"","flag":"","implication":""}]}'
        )

        system_prompt = (
            "You are Vimi, Position2's elite revenue-intelligence AI. Position2 is a B2B digital "
            "marketing agency. Services: SEO & Organic Growth | Performance Marketing "
            "(Google/Meta/LinkedIn Ads) | Content Strategy | Brand & Website | Revenue Operations & HubSpot. "
            "You brief the CEO and Head of Sales on THIS WEEK's pipeline priorities. "
            "METHOD — reason through these steps before writing: "
            "(1) Each signal carries a precomputed importance score (shown as score=NN; signal-type points x severity x recency) — higher means more sales-relevant; rank companies by their total score; "
            "MULTI-INTENT companies (2+ distinct signal types) are the strongest buying-window evidence. "
            "(2) Score intent 0-100 from that weighting and be honest: most companies belong at 30-70; reserve 85+ "
            "for multi-intent + HIGH + fresh. Momentum flags in the data (RISING/ACTIVE/COOLING) must drive the "
            "pipeline momentum field. "
            "(3) Hunt cross-company patterns: sector waves, leadership migrations between tracked companies, funding "
            "clusters in one niche, timing coincidences. These power market_pulse, themes and vimi_take. "
            "(4) For each company, reason WHY the signal opens a marketing-services window NOW: new CMO/CEO = vendor "
            "review window (~90 days); funding = growth mandate and paid-media budget unlock; M&A = brand and website "
            "consolidation work; IPO = scrutiny on organic visibility and analyst-facing content; expansion/news = "
            "momentum to amplify. Map each to the single best-fit Position2 service. "
            "(5) Separate INTERNAL fields from PROSPECT-FACING copy. Internal fields (signal, why_now, reason, "
            "rationale, pitch, impact) may cite signal types and dates. PROSPECT-FACING copy (hook, subject, opening, "
            "talking_points, cta) is what a rep would actually say or send: NEVER mention dates, the word 'signal', "
            "or anything implying we monitor the company ('I saw', 'I noticed', 'your May 13 announcement'). Refer "
            "to public events naturally and obliquely ('as the new facility comes online'). Lead with their problem, "
            "sound human, zero buzzwords, no exclamation marks. "
            "BANNED: generic filler ('great fit', 'reach out to discuss', 'leverage synergies'), invented facts, and "
            "ANY revenue estimates, pipeline values, or dollar figures. Every claim must trace to a signal in the "
            "data. Specific beats clever; concise beats long. "
            "Return ONLY valid JSON exactly matching this schema: "+schema+" "
            "RULES: week_priority=top 6 by urgency; pipeline=top 14 scored 0-100 with honest momentum — include mid "
            "and lower-score watchlist companies too, not only the hot ones; actions=6 ranked; outreach=8 "
            "personalised with <55-char human, curiosity-driven subjects (no spammy caps); themes=4 each with a "
            "usable campaign_angle; risks=2-3 only if real. vimi_take must be a genuinely non-obvious pattern, "
            "never a summary."
        )

        from openai import OpenAI
        oai  = OpenAI(api_key=api_key, timeout=120.0, max_retries=1)
        user_msg = (
            "Analyse %d signals from %d %s-market companies.\n%s\n\n"
            "COMPANY SIGNAL DATA (format: [name | domain | industry] n sigs, MOMENTUM FLAGS — "
            "type(severity,date): detail):\n\n%s\n\nBrief the CEO. Respond with the JSON object only."
            % (n_sig, n_co, acct, stats_lines, "\n".join(ctx_lines)))
        raw, _used_model = _vimi_chat_json(oai, [
            {"role":"system","content":system_prompt},
            {"role":"user","content":user_msg}
        ], 6000)
        if "```" in raw:
            m = _re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
            raw = m.group(1).strip() if m else raw
        s2=raw.find("{"); e2=raw.rfind("}")
        if s2!=-1 and e2!=-1: raw=raw[s2:e2+1]
        # Handle truncated JSON by trying progressively shorter strings
        insights = None
        for attempt in [raw, raw[:raw.rfind("},")+1]+"}" if "}," in raw else raw]:
            try:
                insights = json.loads(attempt)
                break
            except json.JSONDecodeError:
                pass
        if insights is None:
            # Last resort: parse up to last valid closing brace
            for i in range(len(raw)-1, 0, -1):
                if raw[i]=="}":
                    try:
                        insights = json.loads(raw[:i+1])
                        break
                    except Exception:
                        continue
        if insights is None:
            return jsonify({"error": "GPT returned invalid JSON. Try again."})
        insights = _strip_revenue_fields(insights)
        return jsonify({"ok":True,"signals_analyzed":n_sig,"companies_analyzed":n_co,"model":_used_model,"insights":insights})
    except Exception as e:
        import traceback; log.error("insights_generate: %s", traceback.format_exc())
        return jsonify({"error": str(e)})


@app.route("/api/ppc-chat-debug")
@login_required
def ppc_chat_debug():
    """Shows exactly what data the chatbot sees — use to diagnose blank/wrong answers."""
    _PPC_CTX_CACHE["ts"] = 0   # force refresh
    ctx = _build_ppc_context()
    return f"<pre style='font-size:12px;padding:20px'>{ctx}</pre>", 200


# ── Ad Intelligence data helper (for chatbot) ────────────────────────────────
def get_ad_intelligence_data(competitor=None, ad_format=None, status=None,
                              keyword=None, limit=50):
    """
    Fetch competitor ad data from the Ad Intelligence Google Sheet via service account.
    The sheet must be shared with signal-tracker@signal-tracker-496308.iam.gserviceaccount.com

    Args:
        competitor : filter by competitor name or domain
        ad_format  : 'image', 'text', or 'video'
        status     : 'active' or 'inactive'
        keyword    : search in headline / description / keywords
        limit      : max rows to return (default 50)
    """
    try:
        svc = _sheets_service()
        # Read header row first to map columns robustly
        r_hdr = svc.spreadsheets().values().get(
            spreadsheetId=AD_INTEL_SHEET_ID, range="A1:AH1").execute()
        headers = [c.strip() for c in (r_hdr.get("values") or [[]])[0]]

        r_data = svc.spreadsheets().values().get(
            spreadsheetId=AD_INTEL_SHEET_ID, range="A2:AH2000").execute()
        data_rows = r_data.get("values") or []

    except Exception as e:
        err = str(e)
        if "403" in err or "permission" in err.lower() or "not found" in err.lower():
            return {
                "status": "error",
                "error": "Ad Intelligence sheet not shared with the service account.",
                "fix": "Share Google Sheet ID 16U5_QSxMmrAGKvK5dHScBu1Et4BJ1p8Q1ns5LycRA0s "
                       "with signal-tracker@signal-tracker-496308.iam.gserviceaccount.com (Viewer access).",
            }
        return {"status": "error", "error": err}

    def _v(row, col_name):
        try:
            idx = headers.index(col_name)
            return row[idx] if idx < len(row) else ""
        except ValueError:
            return ""

    ads = []
    for row in data_rows:
        domain = _v(row, "Domain")
        if not domain or domain == "Domain":
            continue
        ads.append({
            "competitor":      _v(row, "Advertiser Name") or domain,
            "domain":          domain,
            "format":          _v(row, "Format"),
            "platform":        _v(row, "Platform"),
            "status":          _v(row, "Status"),
            "headline":        _v(row, "Headline"),
            "description":     _v(row, "Description"),
            "full_text":       _v(row, "Full Ad Text"),
            "cta":             _v(row, "CTA"),
            "keywords":        _v(row, "Keywords"),
            "messaging_angle": _v(row, "Messaging Angle"),
            "value_prop":      _v(row, "Value Proposition"),
            "offer":           _v(row, "Offer"),
            "first_shown":     _v(row, "First Shown"),
            "last_shown":      _v(row, "Last Shown"),
            "regions":         _v(row, "Regions Served"),
        })

    total_before = len(ads)

    if competitor:
        c = competitor.lower()
        ads = [a for a in ads if c in a["domain"].lower() or c in a["competitor"].lower()]
    if ad_format:
        ads = [a for a in ads if a["format"].lower() == ad_format.lower()]
    if status:
        ads = [a for a in ads if a["status"].lower() == status.lower()]
    if keyword:
        kw = keyword.lower()
        ads = [a for a in ads if
               kw in a["headline"].lower() or kw in a["description"].lower()
               or kw in a["full_text"].lower() or kw in a["keywords"].lower()]

    format_counts  = dict(Counter(a["format"]  for a in ads if a["format"]).most_common())
    status_counts  = dict(Counter(a["status"]  for a in ads if a["status"]).most_common())
    comp_counts    = dict(Counter(a["competitor"] for a in ads if a["competitor"]).most_common())
    top_ctas       = [c for c, _ in Counter(a["cta"] for a in ads if a["cta"] and len(a["cta"]) < 50).most_common(5)]
    top_keywords   = [k.strip() for k, _ in Counter(
        kw.strip() for a in ads for kw in a["keywords"].split(",") if kw.strip()
    ).most_common(10)]

    return {
        "status": "ok",
        "total_in_sheet": total_before,
        "total_matching_filters": len(ads),
        "returned": min(len(ads), limit),
        "by_competitor": comp_counts,
        "by_format": format_counts,
        "by_status": status_counts,
        "top_ctas": top_ctas,
        "top_keywords": top_keywords,
        "ads": [
            {k: v for k, v in a.items() if k != "full_text"}
            for a in ads[:limit]
        ],
    }



@app.route("/api/company-analysis/<account_id>")
@login_required
def company_analysis(account_id):
    """Deep AI analysis of a single company for the insights drawer."""
    import sqlite3, re as _re
    from pathlib import Path
    db_map = {"healthcare": Path(__file__).parent/"data"/"tracker.db",
              "csg":        Path(__file__).parent/"data"/"tracker_csg_v2.db"}
    db_path = db_map.get(account_id)
    if not db_path or not db_path.exists():
        return jsonify({"error": "Unknown account"}), 404
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return jsonify({"error": "OpenAI API key not configured"}), 500
    company_name = request.args.get("company", "")
    if not company_name:
        return jsonify({"error": "company parameter required"}), 400
    try:
        conn = sqlite3.connect(str(db_path)); conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT a.signal_type, a.signal_detail, a.severity, a.signal_date, "
            "c.industry, c.domain, c.city, c.state "
            "FROM alerts_sent a JOIN companies c ON a.apollo_id=c.apollo_id "
            "WHERE c.name LIKE ? AND a.dry_run=0 ORDER BY a.signal_date DESC LIMIT 20",
            ["%" + company_name + "%"]
        ).fetchall()
        conn.close()
        if not rows:
            return jsonify({"error": "No signals found for this company"}), 200
        signals = [dict(r) for r in rows]
        sig_str = "\n".join(
            "- %s (%s, %s)%s" % (s["signal_type"], s["severity"], s["signal_date"],
                ": "+s["signal_detail"][:160] if s.get("signal_detail") else "")
            for s in signals)
        industry = signals[0].get("industry","") if signals else ""
        co_domain = signals[0].get("domain","") if signals else ""
        co_loc = ", ".join(x for x in [signals[0].get("city") or "", signals[0].get("state") or ""] if x) if signals else ""
        system = (
            "You are Vimi, senior B2B sales strategist at Position2 (SEO & Organic Growth, PPC/Performance "
            "Marketing, Content Strategy, Brand & Website, RevOps & HubSpot). Build a rigorous, signal-grounded "
            "prospect analysis. Reason first: what do the signals (their types, severity, dates, and sequence) "
            "imply about budget timing, internal change, and marketing gaps? Score honestly — most prospects are "
            "40-75; reserve 85+ for multiple fresh HIGH signals. talking_points, subject_lines and email_opening are "
            "PROSPECT-FACING: ground them in the signals but NEVER cite dates, the word 'signal', or anything that "
            "sounds like surveillance ('I saw', 'I noticed', 'your May 13 announcement') - refer to public events "
            "naturally and obliquely, lead with their problem, zero buzzwords, no exclamation marks. score_reason, "
            "why_now and urgency_reason are INTERNAL: dates allowed there. Objections must be the realistic ones "
            "for this industry. Subject lines: human, specific, curiosity-driven, <55 chars, no clickbait caps. "
            "NEVER include revenue estimates or dollar figures. "
            "Return ONLY valid JSON: "
            '{"score":85,"score_reason":"one sentence why",'
            '"company_context":"2 sentences about what this company does and why they matter",'
            '"why_now":"2 sentences on why right now is the perfect time to reach out",'
            '"talking_points":["specific point 1 tied to signal","specific point 2","specific point 3"],'
            '"objections":[{"objection":"likely pushback","response":"how to handle it"}],'
            '"subject_lines":["option 1 <55 chars","option 2","option 3"],'
            '"email_opening":"2 sentence personalized opening referencing their specific situation",'
            '"recommended_service":"SEO|PPC|Content|Brand|RevOps",'
            '"service_reason":"why this specific service fits their situation",'
            '"urgency":"HIGH|MEDIUM|LOW","urgency_reason":"why"}'
        )
        from openai import OpenAI
        oai = OpenAI(api_key=api_key, timeout=90.0, max_retries=1)
        raw, _used_model = _vimi_chat_json(oai, [
            {"role": "system", "content": system},
            {"role": "user", "content": "Company: %s\nDomain: %s\nIndustry: %s\nLocation: %s\nSignals (newest first):\n%s"
                % (company_name, co_domain, industry, co_loc, sig_str)}
        ], 1600)
        if "```" in raw:
            m = _re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
            raw = m.group(1).strip() if m else raw
        s2=raw.find("{"); e2=raw.rfind("}")
        if s2!=-1 and e2!=-1: raw=raw[s2:e2+1]
        return jsonify({"ok": True, "company": company_name, "model": _used_model,
                        "analysis": _strip_revenue_fields(json.loads(raw))})
    except Exception as e:
        return jsonify({"error": str(e)})



@app.route("/api/generate-email/<account_id>")
@login_required
def generate_email(account_id):
    """GPT-powered personalised email using company signals."""
    import sqlite3, re as _re
    from pathlib import Path
    db_map = {"healthcare": Path(__file__).parent/"data"/"tracker.db",
              "csg":        Path(__file__).parent/"data"/"tracker_csg_v2.db"}
    db_path = db_map.get(account_id)
    if not db_path or not db_path.exists():
        return jsonify({"error": "Unknown account"})
    api_key = os.environ.get("OPENAI_API_KEY","")
    if not api_key:
        return jsonify({"error": "OpenAI API key not configured"})
    company = request.args.get("company","").strip()
    service = request.args.get("service","")
    tone    = (request.args.get("tone","") or "direct").strip().lower()
    if not company:
        return jsonify({"error": "company parameter required"})
    try:
        conn = sqlite3.connect(str(db_path)); conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT a.signal_type,a.signal_detail,a.severity,a.signal_date,"
            "c.industry,c.domain FROM alerts_sent a "
            "JOIN companies c ON a.apollo_id=c.apollo_id "
            "WHERE c.name LIKE ? AND a.dry_run=0 ORDER BY a.signal_date DESC LIMIT 15",
            ["%"+company+"%"]
        ).fetchall()
        conn.close()
        signals = [dict(r) for r in rows]
        if not signals:
            return jsonify({"error": "No signals found for " + company})

        industry = signals[0].get("industry","") if signals else ""
        domain   = signals[0].get("domain","")   if signals else ""
        sig_lines = "\n".join(
            "- %s (%s) on %s%s" % (
                s["signal_type"], s["severity"], s["signal_date"],
                ": "+s["signal_detail"][:120] if s.get("signal_detail") else ""
            ) for s in signals[:10]
        )

        tone_guide = {
            "direct":    "TONE: confident and direct, zero fluff - a sharp consultant who respects the reader's time.",
            "warm":      "TONE: warm and human, lightly conversational, still professional.",
            "executive": "TONE: senior executive to senior executive - measured, strategic, no casual phrases.",
        }.get(tone, "TONE: confident and direct, zero fluff.")

        system = (
            "You are Vimi, writing outreach for Position2, a B2B digital marketing agency "
            "(SEO | Performance Marketing/PPC | Content Strategy | Brand & Website | Revenue Operations). "
            "Write an email a thoughtful senior consultant would actually send - never anything that smells of "
            "AI or mail-merge.\n\n"
            "The signals provided are INTERNAL intelligence. Use them ONLY to understand the company situation.\n"
            "HARD RULES:\n"
            "- NEVER mention dates, the word signal, announcements you saw or noticed, or anything implying we "
            "monitor them. Banned openers: I saw / I noticed / I came across / Congratulations on / Hope this "
            "finds you well / Quick question.\n"
            "- Refer to public events only obliquely and naturally (as the new facility comes online; with the "
            "team growing) - no dates, no press-release specifics.\n"
            "- The first sentence must be about THEIR world - a real problem or opportunity - and it must earn "
            "the second sentence. Never open with us.\n"
            "- Include one concrete, useful observation or idea they could act on even without replying. That "
            "is the value of the email.\n"
            "- Mention Position2 once, briefly, as credibility - no service list, no we-help-companies-like-you.\n"
            "- ONE low-friction CTA phrased as an easy yes/no question. Never hop-on-a-call-to-discuss.\n"
            "- Under 110 words across opening+body+cta. Short sentences. 7th-grade readability. No buzzwords "
            "(leverage, synergies, streamline, elevate, unlock, empower, seamless, cutting-edge) and no "
            "exclamation marks.\n"
            "- subject: under 45 characters, natural and specific, sentence case, no clickbait.\n"
            "- greeting: exactly Hi {FirstName}, so the rep can personalise.\n"
            "- ps: only if there is a genuinely useful extra thought, otherwise an empty string. Never a second "
            "pitch.\n"
            "- Never invent facts, metrics, client names, or revenue/dollar figures.\n"
            + tone_guide + "\n\n"
            "Return ONLY valid JSON:\n"
            '{"subject":"","greeting":"Hi {FirstName},","opening":"<1 sentence about their world>",'
            '"body":"<2-3 short sentences: useful insight, then one line of Position2 credibility>",'
            '"cta":"<one easy yes/no question>","ps":"",'
            '"recommended_service":"<SEO|PPC|Content|Brand|RevOps>",'
            '"why_now":"<INTERNAL rep note on timing - dates allowed here; one sentence>"}'
        )

        user_msg = "Company: %s\nIndustry: %s\nDomain: %s\nPreferred service: %s\n\nSignals:\n%s\n\nWrite the email." % (
            company, industry, domain, service or "best fit", sig_lines)

        from openai import OpenAI
        oai = OpenAI(api_key=api_key)
        raw, _m = _vimi_completion(
            oai, [{"role":"system","content":system},{"role":"user","content":user_msg}], 800)
        if "```" in raw:
            m = _re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
            raw = m.group(1).strip() if m else raw
        s2=raw.find("{"); e2=raw.rfind("}")
        if s2!=-1 and e2!=-1: raw=raw[s2:e2+1]
        email_data = json.loads(raw)
        return jsonify({"ok":True,"company":company,"email":email_data,"signals_used":len(signals)})
    except Exception as e:
        import traceback; log.error("generate_email: %s", traceback.format_exc())
        return jsonify({"error": str(e)})


@app.route("/api/research-company/<account_id>")
@login_required
def research_company(account_id):
    """AI research on a company: GPT + web search -> key facts, insights, Position2 angle."""
    import sqlite3, re as _re
    from pathlib import Path
    db_map = {"healthcare": Path(__file__).parent/"data"/"tracker.db",
              "csg":        Path(__file__).parent/"data"/"tracker_csg_v2.db"}
    db_path = db_map.get(account_id)
    if not db_path or not db_path.exists():
        return jsonify({"error": "Unknown account"})
    api_key = os.environ.get("OPENAI_API_KEY","")
    if not api_key:
        return jsonify({"error": "OpenAI API key not configured"})
    company = request.args.get("company","").strip()
    domain  = request.args.get("domain","").strip()
    if not company:
        return jsonify({"error": "company parameter required"})
    try:
        # Pull known signals for grounding context
        sig_lines = ""
        try:
            conn = sqlite3.connect(str(db_path)); conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT a.signal_type,a.signal_detail,a.severity,a.signal_date,c.industry,c.domain "
                "FROM alerts_sent a JOIN companies c ON a.apollo_id=c.apollo_id "
                "WHERE c.name LIKE ? AND a.dry_run=0 ORDER BY a.signal_date DESC LIMIT 10",
                ["%"+company+"%"]).fetchall()
            conn.close()
            sigs = [dict(r) for r in rows]
            if sigs and not domain:
                domain = sigs[0].get("domain","") or ""
            sig_lines = "\n".join(
                "- %s (%s) on %s%s" % (s["signal_type"], s["severity"], s["signal_date"],
                    ": "+s["signal_detail"][:120] if s.get("signal_detail") else "")
                for s in sigs)
        except Exception:
            pass

        system = (
            "You are Vimi, Position2’s sales-intelligence research AI. Position2 is a digital marketing agency. "
            "Services: SEO & Organic Growth | Performance Marketing (Google/Meta/LinkedIn Ads) | "
            "Content Strategy | Brand & Website | Revenue Operations & HubSpot. "
            "Research the given company using web search. Find what they do, recent news, "
            "leadership, market position, and their likely digital-marketing gaps. "
            "NEVER include revenue estimates or dollar figures. "
            "Return ONLY valid JSON (no markdown):\n"
            '{"overview":"2-3 sentences on what the company does and their market position",'
            '"recent_developments":[{"date":"YYYY-MM or recent","headline":"","detail":"1 sentence"}],'
            '"key_people":[{"name":"","role":""}],'
            '"digital_presence":"1-2 sentences on their website/SEO/ads/social footprint and visible gaps",'
            '"opportunities":["specific marketing gap or opportunity Position2 could address"],'
            '"position2_angle":"2 sentences: which Position2 services fit and why, tied to findings",'
            '"recommended_services":["SEO|PPC|Content|Brand|RevOps"],'
            '"conversation_starters":["natural human opener grounded in a real finding - no dates, never sounding like surveillance"],'
            '"sources":[{"title":"","url":""}]}'
        )
        user_msg = "Research this company NOW:\nCompany: %s\nDomain: %s\n%s" % (
            company, domain or "unknown",
            "Known signals from our tracker:\n"+sig_lines if sig_lines else "")

        from openai import OpenAI
        oai   = OpenAI(api_key=api_key)
        _msgs = [{"role":"system","content":system},{"role":"user","content":user_msg}]
        model = os.environ.get("OPENAI_INSIGHTS_MODEL") or "gpt-5.4"
        raw, web_used = _responses_web_search(oai, model, _msgs, 2500)
        if not raw:
            model = os.environ.get("OPENAI_MODEL","gpt-4o-mini")
            raw, web_used = _responses_web_search(oai, model, _msgs, 2500)
        # Fallback: plain completion using only tracker signals
        if not raw:
            resp = oai.chat.completions.create(
                model=model,
                messages=[{"role":"system","content":system.replace("using web search","using your knowledge (web search unavailable)")},
                          {"role":"user","content":user_msg}],
                max_completion_tokens=2000,
            )
            raw = resp.choices[0].message.content.strip()
        if "```" in raw:
            m = _re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
            raw = m.group(1).strip() if m else raw
        s2=raw.find("{"); e2=raw.rfind("}")
        if s2!=-1 and e2!=-1: raw=raw[s2:e2+1]
        research = _strip_revenue_fields(json.loads(raw))
        return jsonify({"ok": True, "company": company, "domain": domain,
                        "web_search_used": web_used, "research": research})
    except Exception as e:
        import traceback; log.error("research_company: %s", traceback.format_exc())
        return jsonify({"error": str(e)})



@app.route("/api/decision-makers/<account_id>")
@login_required
def decision_makers(account_id):
    """Use OpenAI web search to find C-suite / key people at a company with LinkedIn URLs."""
    import re as _re
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return jsonify({"error": "OpenAI API key not configured"})
    company = request.args.get("company", "").strip()
    domain  = request.args.get("domain", "").strip()
    if not company:
        return jsonify({"error": "company parameter required"})
    try:
        system = (
            "You are a B2B sales research assistant. Find the current key decision-makers "
            "at the given company — CEO, CFO, CMO, CTO, COO, VP Marketing, VP Sales, "
            "Founders, Managing Directors, Partners, and other senior leaders. "
            "Use web search to find real, current people. For each person find their LinkedIn profile URL. "
            "Return ONLY valid JSON (no markdown, no commentary):\n"
            '{"people":['
            '{"name":"Full Name","role":"Job Title","linkedin":"https://linkedin.com/in/handle or empty string","bio":"1 short sentence about them"}'
            ']}'
            "\nReturn up to 10 people. If you cannot find a LinkedIn URL leave it as empty string."
        )
        user_msg = f"Find decision-makers at: {company}" + (f" (website: {domain})" if domain else "")
        from openai import OpenAI
        oai   = OpenAI(api_key=api_key)
        _msgs = [{"role": "system", "content": system}, {"role": "user", "content": user_msg}]
        model = os.environ.get("OPENAI_INSIGHTS_MODEL") or "gpt-5.4"
        raw, web_used = _responses_web_search(oai, model, _msgs, 1500)
        if not raw:
            model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            raw, web_used = _responses_web_search(oai, model, _msgs, 1500)
        if not raw:
            resp = oai.chat.completions.create(
                model=model,
                messages=[{"role":"system","content":system},{"role":"user","content":user_msg}],
                max_completion_tokens=1200,
            )
            raw = resp.choices[0].message.content.strip()
            web_used = False
        if "```" in raw:
            m2 = _re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
            raw = m2.group(1).strip() if m2 else raw
        s2 = raw.find("{"); e2 = raw.rfind("}")
        if s2 != -1 and e2 != -1: raw = raw[s2:e2+1]
        data = json.loads(raw)
        return jsonify({"ok": True, "company": company, "web_search_used": web_used,
                        "people": data.get("people", [])})
    except Exception as exc:
        import traceback; log.error("decision_makers: %s", traceback.format_exc())
        return jsonify({"error": str(exc)})

@app.route("/api/vimi-chat/<account_id>", methods=["POST"])
@login_required
def vimi_chat(account_id):
    """Conversational Vimi: grounded on the account signal DB, web-search for the rest."""
    import sqlite3, re as _re
    from pathlib import Path
    db_map = {"healthcare": Path(__file__).parent/"data"/"tracker.db",
              "csg":        Path(__file__).parent/"data"/"tracker_csg_v2.db"}
    db_path = db_map.get(account_id)
    if not db_path or not db_path.exists():
        return jsonify({"error": "Unknown account"})
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return jsonify({"error": "OpenAI API key not configured"})
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    history = body.get("history") or []
    files = body.get("files") or []
    if not question:
        return jsonify({"error": "empty question"})

    img_files = [f for f in files if isinstance(f, dict) and f.get("image") and f.get("base64")][:4]
    txt_attached = [f for f in files if isinstance(f, dict) and not f.get("image")]

    # Attached-file context (extracted client-side via /api/ppc-upload)
    att_ctx = ""
    if txt_attached:
        chunks = []
        for fobj in txt_attached[:4]:
            nm = str(fobj.get("name") or "file")[:120]
            ct = str(fobj.get("content") or "")[:12000]
            if ct.strip():
                chunks.append("=== ATTACHED FILE: %s ===\n%s" % (nm, ct))
        if chunks:
            att_ctx = ("\n\nATTACHED FILES (uploaded by the user — treat as primary context; quote and "
                       "analyse their actual contents):\n" + "\n\n".join(chunks))

    # Detect a requested output format (csv / xlsx / docx / pdf / pptx)
    export_format = ""
    _ql = question.lower()
    if _re.search(r"\b(as|in|into|to|export|download|give|create|make|generate|build|produce|format|convert)\b", _ql):
        for _f, _pat in (("csv", r"\bcsv\b"), ("xlsx", r"\b(xlsx|xls|excel|spreadsheet)\b"),
                         ("docx", r"\b(docx|word)\b"), ("pdf", r"\bpdf\b"),
                         ("pptx", r"\b(pptx|ppt|powerpoint|slide deck|slides|deck)\b")):
            if _re.search(_pat, _ql):
                export_format = _f
                break
    try:
        conn = sqlite3.connect(str(db_path)); conn.row_factory = sqlite3.Row
        counts = {r[0]: r[1] for r in conn.execute(
            "SELECT signal_type, COUNT(*) FROM alerts_sent WHERE dry_run=0 GROUP BY signal_type")}
        total_sig = sum(counts.values())
        total_co = conn.execute("SELECT COUNT(DISTINCT apollo_id) FROM alerts_sent WHERE dry_run=0").fetchone()[0]
        acct_label = "Healthcare" if account_id == "healthcare" else "CSG"
        ql = question.lower()
        names = [r[0] for r in conn.execute("SELECT DISTINCT name FROM companies WHERE name IS NOT NULL")]
        matched = [n for n in names if n and len(n) > 2 and n.lower() in ql][:6]
        ctx = []
        if matched:
            for nm in matched:
                rows = conn.execute(
                    "SELECT a.signal_type,a.severity,a.signal_date,a.signal_detail,c.domain,c.industry "
                    "FROM alerts_sent a JOIN companies c ON a.apollo_id=c.apollo_id "
                    "WHERE c.name=? AND a.dry_run=0 ORDER BY a.signal_date DESC LIMIT 12", [nm]).fetchall()
                if rows:
                    sl = " | ".join("%s(%s,%s)%s" % (
                        r["signal_type"], r["severity"], r["signal_date"],
                        (": " + r["signal_detail"][:80]) if r["signal_detail"] else "") for r in rows[:8])
                    ctx.append("[%s | %s | %s] %s" % (nm, rows[0]["domain"], rows[0]["industry"], sl))
        else:
            rows = conn.execute(
                "SELECT c.name,c.domain,COUNT(*) n,SUM(CASE WHEN a.severity='HIGH' THEN 1 ELSE 0 END) hi "
                "FROM alerts_sent a JOIN companies c ON a.apollo_id=c.apollo_id "
                "WHERE a.dry_run=0 GROUP BY c.apollo_id ORDER BY hi DESC,n DESC LIMIT 12").fetchall()
            for r in rows:
                ctx.append("%s (%s) - %d signals, %d HIGH" % (r["name"], r["domain"], r["n"], r["hi"]))
        conn.close()

        overview = "Account: %s market. %d tracked signals across %d companies. Signal mix: %s." % (
            acct_label, total_sig, total_co,
            ", ".join("%s %d" % (k, v) for k, v in sorted(counts.items(), key=lambda x: -x[1])))
        ctx_str = "\n".join(ctx) or "(no specific company matched - use the overview and web search)"
        system = (
            "You are Vimi, Position2's signal-intelligence assistant. Position2 is a B2B digital marketing "
            "agency (SEO, PPC, Content, Brand & Website, RevOps). Answer the user accurately and concisely. "
            "Use the ACCOUNT SIGNAL DATA below for questions about tracked companies and signals; use web search "
            "for company research, recent news, people, contacts, or anything not in the data. If asked to draft an "
            "email or message, make it tight, personalised and HUMAN: never cite signal dates or imply we monitor "
            "the company ('I saw your May 13 announcement') in prospect-facing copy - refer to public events "
            "naturally and obliquely. Never invent revenue or dollar figures. Cite specific "
            "companies and signals. Format with short, clean markdown (bold, links, short lists). "
            "If files, images or screenshots are attached, ground your answer in their ACTUAL contents — quote real numbers, names and rows "
            "from them, and combine them with signal data where relevant. "
            "If the user asks for output as a file or specific format (CSV, Excel/XLSX, Word/DOCX, PDF, "
            "PowerPoint/PPTX, or a table), produce the COMPLETE content in clean markdown: a proper markdown table "
            "for tabular data, headings (#, ##) to structure documents and slides. The platform converts your "
            "markdown into the requested file, so never refuse a format and never truncate with placeholders.\n\n"
            "ACCOUNT OVERVIEW: %s\n\nRELEVANT SIGNAL DATA:\n%s%s" % (overview, ctx_str, att_ctx))

        msgs = [{"role": "system", "content": system}]
        for m in history[-8:]:
            role = m.get("role")
            if role in ("user", "assistant") and m.get("content"):
                msgs.append({"role": role, "content": str(m["content"])[:2000]})
        msgs.append({"role": "user", "content": question})

        from openai import OpenAI
        oai = OpenAI(api_key=api_key, timeout=80.0, max_retries=1)
        _max_out = 2600 if (files or export_format) else 1100
        if img_files:
            # Vision path: send image(s) directly to the model as data URIs
            parts = [{"type": "text", "text": question}]
            for im in img_files:
                mime = str(im.get("mime") or "image/png")[:50]
                b64 = str(im.get("base64") or "")
                if not b64 or len(b64) > 9_000_000:
                    continue
                parts.append({"type": "image_url",
                              "image_url": {"url": "data:%s;base64,%s" % (mime, b64)}})
            msgs[-1] = {"role": "user", "content": parts}
            answer, _m = _vimi_completion(oai, msgs, _max_out)
            web = False
        else:
            model = os.environ.get("OPENAI_INSIGHTS_MODEL") or "gpt-5.4"
            answer, web = _responses_web_search(oai, model, msgs, _max_out)
            if not answer:
                answer, web = _responses_web_search(oai, os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), msgs, _max_out)
            if not answer:
                answer, _m = _vimi_completion(oai, msgs, _max_out)
        return jsonify({"ok": True, "answer": answer, "web_search_used": web,
                        "export_format": export_format})
    except Exception as e:
        import traceback; log.error("vimi_chat: %s", traceback.format_exc())
        return jsonify({"error": str(e)})


# ── Vimi export: convert a markdown answer into a downloadable file ─────────

def _md_blocks(content):
    """Parse markdown-lite into (kind, payload) blocks: h1/h2/h3/li/p (str) and tr (list of cells)."""
    blocks = []
    for ln in content.splitlines():
        st = ln.strip()
        if not st:
            continue
        if st.startswith("|") and st.endswith("|") and st.count("|") >= 2:
            cells = [c.strip() for c in st.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue  # separator row
            blocks.append(("tr", cells)); continue
        if st.startswith("### "): blocks.append(("h3", st[4:])); continue
        if st.startswith("## "):  blocks.append(("h2", st[3:])); continue
        if st.startswith("# "):   blocks.append(("h1", st[2:])); continue
        if st[:2] in ("- ", "* "): blocks.append(("li", st[2:])); continue
        blocks.append(("p", st))
    return blocks


def _md_table_rows(content):
    """First choice: markdown table rows. Fallback: CSV inside a code block."""
    rows = [v for k, v in _md_blocks(content) if k == "tr"]
    if rows:
        return rows
    import csv as _csv, io as _io, re as _re2
    m = _re2.search(r"```(?:csv)?\s*([\s\S]*?)```", content)
    blob = m.group(1).strip() if m else ""
    if blob and ("," in blob or "\t" in blob):
        return [r for r in _csv.reader(_io.StringIO(blob)) if any(x.strip() for x in r)]
    return []


def _strip_md(t):
    import re as _re2
    t = str(t)
    t = _re2.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = _re2.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r"\1 (\2)", t)
    return t.strip()


@app.route("/api/vimi-export", methods=["POST"])
@login_required
def vimi_export():
    """Convert Vimi markdown output into CSV / XLSX / DOCX / PDF / PPTX and stream it back."""
    import io
    from flask import send_file
    body = request.get_json(silent=True) or {}
    fmt = (body.get("format") or "").lower().strip()
    content = str(body.get("content") or "").strip()
    title = (body.get("title") or "Vimi Insights").strip()[:80] or "Vimi Insights"
    if not content:
        return jsonify({"error": "no content"}), 400
    if fmt not in ("csv", "xlsx", "docx", "pdf", "pptx"):
        return jsonify({"error": "unsupported format"}), 400
    fname = "vimi-insights." + fmt
    try:
        if fmt == "csv":
            import csv as _csv
            buf = io.StringIO()
            w = _csv.writer(buf)
            rows = _md_table_rows(content)
            if rows:
                for r in rows:
                    w.writerow([_strip_md(c) for c in r])
            else:
                for kind, val in _md_blocks(content):
                    w.writerow([_strip_md(val if isinstance(val, str) else " | ".join(val))])
            data = io.BytesIO(buf.getvalue().encode("utf-8-sig"))
            return send_file(data, mimetype="text/csv", as_attachment=True, download_name=fname)

        if fmt == "xlsx":
            import openpyxl
            from openpyxl.styles import Font
            wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Vimi"
            rows = _md_table_rows(content)
            if rows:
                for ri, r in enumerate(rows, 1):
                    for ci, c in enumerate(r, 1):
                        ws.cell(row=ri, column=ci, value=_strip_md(c))
                for c in ws[1]:
                    c.font = Font(bold=True)
            else:
                ri = 1
                for kind, val in _md_blocks(content):
                    cell = ws.cell(row=ri, column=1,
                                   value=_strip_md(val if isinstance(val, str) else " | ".join(val)))
                    if kind in ("h1", "h2", "h3"):
                        cell.font = Font(bold=True, size=13 if kind == "h1" else 12)
                    ri += 1
            for col in ws.columns:
                mx = max((len(str(c.value or "")) for c in col), default=10)
                ws.column_dimensions[col[0].column_letter].width = min(60, max(12, mx + 2))
            data = io.BytesIO(); wb.save(data); data.seek(0)
            return send_file(data, as_attachment=True, download_name=fname,
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        if fmt == "docx":
            import docx
            doc = docx.Document()
            doc.add_heading(title, level=0)
            tbl = []
            def _flush():
                if not tbl:
                    return
                t = doc.add_table(rows=0, cols=max(len(r) for r in tbl))
                try: t.style = "Light Grid Accent 1"
                except Exception: pass
                for r in tbl:
                    cells = t.add_row().cells
                    for i2, c in enumerate(r):
                        if i2 < len(cells):
                            cells[i2].text = _strip_md(c)
                del tbl[:]
            for kind, val in _md_blocks(content):
                if kind == "tr":
                    tbl.append(val); continue
                _flush()
                if kind == "h1": doc.add_heading(_strip_md(val), level=1)
                elif kind == "h2": doc.add_heading(_strip_md(val), level=2)
                elif kind == "h3": doc.add_heading(_strip_md(val), level=3)
                elif kind == "li": doc.add_paragraph(_strip_md(val), style="List Bullet")
                else: doc.add_paragraph(_strip_md(val))
            _flush()
            data = io.BytesIO(); doc.save(data); data.seek(0)
            return send_file(data, as_attachment=True, download_name=fname,
                mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

        if fmt == "pptx":
            from pptx import Presentation
            prs = Presentation()
            slides, cur = [], [title, []]
            for kind, val in _md_blocks(content):
                txt = _strip_md(val if isinstance(val, str) else " | ".join(val))
                if kind in ("h1", "h2"):
                    if cur[1]:
                        slides.append(cur)
                    cur = [txt, []]
                else:
                    cur[1].append(("• " if kind == "li" else "") + txt)
            if cur[1] or not slides:
                slides.append(cur)
            for stitle, lines in slides[:30]:
                slide = prs.slides.add_slide(prs.slide_layouts[1])
                slide.shapes.title.text = stitle[:90]
                tf = slide.placeholders[1].text_frame
                tf.text = ""
                for i2, ln in enumerate(lines[:12]):
                    p = tf.paragraphs[0] if i2 == 0 else tf.add_paragraph()
                    p.text = ln[:180]
            data = io.BytesIO(); prs.save(data); data.seek(0)
            return send_file(data, as_attachment=True, download_name=fname,
                mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation")

        # ── pdf ──
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.lib import colors
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
        data = io.BytesIO()
        docp = SimpleDocTemplate(data, pagesize=A4, topMargin=18*mm, bottomMargin=18*mm)
        styles = getSampleStyleSheet()
        story = [Paragraph(title, styles["Title"]), Spacer(1, 6)]
        tbl = []
        def _flush_pdf():
            if not tbl:
                return
            t = Table([[_strip_md(c)[:90] for c in r] for r in tbl], hAlign="LEFT")
            t.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4f46e5")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]))
            story.append(t); story.append(Spacer(1, 8)); del tbl[:]
        for kind, val in _md_blocks(content):
            if kind == "tr":
                tbl.append(val); continue
            _flush_pdf()
            txt = _strip_md(val).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if kind == "h1": story.append(Paragraph(txt, styles["Heading1"]))
            elif kind == "h2": story.append(Paragraph(txt, styles["Heading2"]))
            elif kind == "h3": story.append(Paragraph(txt, styles["Heading3"]))
            elif kind == "li": story.append(Paragraph("• " + txt, styles["Normal"]))
            else: story.append(Paragraph(txt, styles["Normal"]))
        _flush_pdf()
        docp.build(story)
        data.seek(0)
        return send_file(data, mimetype="application/pdf", as_attachment=True, download_name=fname)
    except Exception as e:
        import traceback; log.error("vimi_export: %s", traceback.format_exc())
        return jsonify({"error": str(e)}), 500


@app.route("/api/refresh-dashboard", methods=["POST"])
@login_required
def refresh_dashboard():
    """Trigger the GitHub Action that fetches the latest signals (HIGH from
    Sheets, LOW from Google News with filters) for both accounts, rebuilds the
    dashboards (preserving Vimi), prunes news, and publishes."""
    token    = os.environ.get("GH_DISPATCH_TOKEN", "")
    repo     = os.environ.get("GH_REPO", "ai-positon2/intelligence-platform")
    workflow = os.environ.get("GH_WORKFLOW", "refresh-dashboards.yml")
    if not token:
        return jsonify({"error": "Refresh isn't wired up yet — add a GH_DISPATCH_TOKEN "
                                 "environment variable in Railway (a GitHub token with the "
                                 "'workflow' scope). Until then, use the manual commands below."}), 200
    try:
        url = "https://api.github.com/repos/%s/actions/workflows/%s/dispatches" % (repo, workflow)
        r = requests.post(url, json={"ref": "main"}, timeout=20, headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        if r.status_code in (201, 204):
            return jsonify({"ok": True,
                "message": "Refresh started. Vimi is fetching the latest HIGH signals (Sheets) and "
                           "LOW signals (Google News, filtered) for both accounts, rebuilding, and "
                           "publishing. Your dashboard updates automatically in a few minutes — reload then.",
                "actions_url": "https://github.com/%s/actions/workflows/%s" % (repo, workflow)})
        return jsonify({"error": "GitHub returned %d. Check the GH_DISPATCH_TOKEN scope/repo. %s"
                                 % (r.status_code, (r.text or "")[:160])}), 200
    except Exception as e:
        import traceback; log.error("refresh_dashboard: %s", traceback.format_exc())
        return jsonify({"error": str(e)}), 200


def _refresh_stage(name):
    n = (name or "").lower()
    if "fetch healthcare" in n: return "Fetching Healthcare signals (Sheets + News)…"
    if "hiring" in n or "creative" in n or "3d" in n: return "Scanning creative / 3D hiring…"
    if "sheet" in n or "high signals" in n: return "Fetching CSG C-Suite / IPO / M&A / Funding (Sheets)…"
    if "fetch csg" in n:        return "Fetching CSG news…"
    if "rebuild" in n:          return "Rebuilding & scoring both accounts…"
    if "commit" in n or "publish" in n: return "Publishing…"
    if "restore" in n:          return "Rebuilding…"
    return "Preparing…"

@app.route("/api/refresh-status")
@login_required
def refresh_status():
    """Live status of the most recent refresh Action run (for the progress bar)."""
    import datetime as _dt, time as _t
    token    = os.environ.get("GH_DISPATCH_TOKEN", "")
    repo     = os.environ.get("GH_REPO", "ai-positon2/intelligence-platform")
    workflow = os.environ.get("GH_WORKFLOW", "refresh-dashboards.yml")
    if not token:
        return jsonify({"error": "Refresh isn't configured (missing GH_DISPATCH_TOKEN)."})
    def _epoch(iso):
        try:
            return _dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_dt.timezone.utc).timestamp()
        except Exception:
            return 0.0
    try:
        since = float(request.args.get("since", 0) or 0)
        hdr = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
        rr = requests.get("https://api.github.com/repos/%s/actions/workflows/%s/runs?per_page=5" % (repo, workflow),
                          headers=hdr, timeout=15)
        if rr.status_code != 200:
            return jsonify({"error": "GitHub runs %d" % rr.status_code})
        run = None
        for w in rr.json().get("workflow_runs", []):
            if since <= 0 or _epoch(w.get("created_at", "")) >= since - 90:
                run = w; break
        if not run:
            return jsonify({"pending": True})
        status = run.get("status"); concl = run.get("conclusion")
        started = _epoch(run.get("run_started_at") or run.get("created_at") or "")
        elapsed = max(0, int(_t.time() - started)) if started else 0
        percent, stage = 8, "Queued…"
        if status == "completed":
            percent, stage = 100, ("Done" if concl == "success" else "Failed")
        else:
            steps = []
            jurl = run.get("jobs_url")
            if jurl:
                jr = requests.get(jurl, headers=hdr, timeout=15)
                if jr.status_code == 200:
                    jobs = jr.json().get("jobs", [])
                    if jobs: steps = jobs[0].get("steps", []) or []
            if steps:
                tot = len(steps); done = sum(1 for s in steps if s.get("status") == "completed")
                cur = next((s for s in steps if s.get("status") == "in_progress"), None)
                percent = min(95, int(round(100.0 * done / max(tot, 1))))
                stage = _refresh_stage(cur.get("name")) if cur else "Working…"
        return jsonify({"status": status, "conclusion": concl, "percent": percent,
                        "stage": stage, "elapsed": elapsed, "html_url": run.get("html_url", "")})
    except Exception as e:
        return jsonify({"error": str(e)})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# redeploy nudge 1781202493 (particles v2 + login music)

# redeploy nudge 1781210922 (remove orb + motif on hub/ppc/seo)

# redeploy nudge 20260613-162500
# redeploy nudge 20260612-075817
