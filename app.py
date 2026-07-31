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
from urllib.parse import urlsplit

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

# Postgres connection string (set DATABASE_URL in Railway Variables). Used only
# for agent run-history (full run outputs) -- everything else still lives in
# Sheets. Sheets cells cap at ~50k chars and Content Enhancer's rewritten
# articles can exceed that on their own, so that one datastore needed to grow.
DATABASE_URL = os.environ.get("DATABASE_URL", "")

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
        vid = (request.cookies.get("p2_vid") or "").strip()[:64]

        # 21 columns — add header row automatically on first write
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
            "/p2/hub",                                     # 17 Landing Page
            "Google OAuth",                             # 18 Auth Method
            str(uuid.uuid4())[:8],                      # 19 Session ID (short)
            "intelligence.position2.com",               # 20 Platform
            vid,                                         # 21 Visitor ID (p2_vid, for Public Page Analytics linking)
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
                "Session ID", "Platform", "Visitor ID",
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

        # Retro-stitch: link this vid's prior anonymous sessions to the person.
        _graph_identify(vid, email=user.get("email", ""),
                        name=user.get("name", ""), source="staff_login")

    except Exception as e:
        log.warning("Login sheet log failed: %s", e)


_MEMBER_TAB = "Member Signins"
_MS_HEADER = ["Timestamp (IST)","Date","Time (IST)","Day","Hour (IST)","Email","Full Name",
    "First Name","Profile Picture","Visitor ID","IP","Browser","Browser Version","OS","Device",
    "User Agent","Referrer","Landing Page","Session ID","Platform"]

def _log_member_signin(user: dict) -> None:
    """Append one PUBLIC (non-Position2) Google sign-in to the 'Member Signins' tab.
    Records the anonymous visitor id (p2_vid cookie) so the member can be joined to
    their pre-login Visitor Analytics journey. Fails silently."""
    # Resolve this person's Apollo profile now, detached, so the admin dashboards
    # already have it before anyone opens them. Runs BEFORE the sheet-id guard so
    # enrichment still happens on an environment with no Sheets configured.
    _warm_person_enrichment(user.get("email", ""))
    if not LOGIN_LOG_SHEET_ID:
        return
    try:
        svc = _va_sheets_service()
        if not svc:
            return
        now = datetime.now(IST)
        ua_raw = request.headers.get("User-Agent", "")
        browser, bv, os_name, device = _parse_ua(ua_raw)
        ip = (request.headers.get("X-Forwarded-For", "") or
              request.headers.get("X-Real-IP", "") or request.remote_addr or "")
        ip = ip.split(",")[0].strip()
        vid = (request.cookies.get("p2_vid") or "").strip()[:64]
        row = [now.strftime("%Y-%m-%d %H:%M:%S IST"), now.strftime("%Y-%m-%d"),
               now.strftime("%H:%M:%S"), now.strftime("%A"), now.strftime("%H"),
               user.get("email", ""), user.get("name", ""), user.get("given_name", ""),
               user.get("picture", ""), vid, ip, browser, bv, os_name, device,
               ua_raw[:200], request.referrer or "direct", "/app", "Google OAuth",
               "intelligence.position2.com"]
        tab = _MEMBER_TAB
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
            svc.spreadsheets().values().append(spreadsheetId=LOGIN_LOG_SHEET_ID,
                range="%s!A1" % tab, valueInputOption="RAW", body={"values": [_MS_HEADER]}).execute()
        svc.spreadsheets().values().append(spreadsheetId=LOGIN_LOG_SHEET_ID, range="%s!A1" % tab,
            valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": [row]}).execute()
        # Retro-stitch this member's prior anonymous sessions to their identity.
        _graph_identify(vid, email=user.get("email", ""),
                        name=user.get("name", ""), source="member_login")
    except Exception as e:
        log.warning("member signin log failed: %s", e)


# ── Demo / custom-agent request intake (login-page form) ─────────────────────────
DEMO_REQUEST_SHEET_ID = os.environ.get("DEMO_REQUEST_SHEET_ID", "") or LOGIN_LOG_SHEET_ID
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Free/personal webmail domains -- excluded when inferring a member's company from
# their sign-in email domain (Public Page Analytics). Not exhaustive, but covers the
# overwhelming majority of personal-account sign-ins.
_FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.co.uk",
    "outlook.com", "hotmail.com", "hotmail.co.uk", "live.com", "msn.com",
    "icloud.com", "me.com", "mac.com", "aol.com", "protonmail.com", "proton.me",
    "gmx.com", "gmx.de", "mail.com", "yandex.com", "zoho.com", "rediffmail.com",
    "ymail.com", "rocketmail.com", "pm.me",
}


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
        "slug": "keyword-opportunity-engine", "name": "Keyword Finder", "role": "Keyword Strategy",
        "badge": "", "cat": "SEO", "accent": "#6366f1", "metric": "Intent-ranked \u00b7 revenue-weighted",
        "icon": _svg('<circle cx="7.5" cy="15.5" r="3.5"/><path d="M10 13l8-8 3 3M16 7l2 2"/>'),
        "summary": "Surface the keywords actually worth winning - AI-shortlisted by intent, difficulty and revenue potential - so every content bet is backed by data.",
        "benefit": "Stop spreading effort thin. Focus on the terms that convert and that you can realistically rank for.",
        "how": "It expands seed topics, pulls volume and difficulty, scores commercial intent, and shortlists the highest-opportunity keywords with AI.",
        "who": "SEO and content strategists planning the roadmap.",
        "connects": ["Semrush", "GSC", "Keyword Planner"],
    },
    {
        "slug": "content-brief-architect", "name": "Content Brief Generator", "role": "SERP-Driven Briefs",
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
        "slug": "article-enhancer", "name": "Content Enhancer", "role": "Existing-Content Optimization",
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
        "slug": "health-tech",
        "name": "Health Tech",
        "short": "Health Tech",
        "featured": True,
        "accent": "#22d3ee", "accent2": "#34d399",
        "icon": _isvg('<path d="M3 12h3.5l2 5 4-12 2.2 7H21"/>'),
        "eyebrow": "Industry · Health Tech",
        "headline": "Win the health system",
        "headline_ital": "before the market moves.",
        "lead": "Selling into healthcare is slow, committee-driven and built on trust. Intelligence watches every provider, payer, digital-health and medtech org for the signals that precede a budget, funding, mergers, new facilities, service-line launches and leadership moves, then hands your team the account, the committee and the next move.",
        "stats": [
            {"v": "1,251", "l": "health-tech orgs tracked"},
            {"v": "26",    "l": "buying-signal types"},
            {"v": "24/7",  "l": "real-time detection"},
            {"v": "5",     "l": "agents tuned for health tech"},
        ],
        "segments": [
            "Digital health & telehealth", "Medtech & devices",
            "Health IT & EHR", "Payers & health plans",
            "Pharma & biotech", "Revenue-cycle & RCM vendors",
        ],
        "chips": ["Signal fired", "Intent score 9.4", "Committee mapped"],
        "pains": [
            {"t": "Long, committee-driven cycles", "d": "A single deal touches the CMIO, CNIO, CFO, service-line leaders and procurement. You need to know which system is in-market, and who sits on the committee, before a competitor does."},
            {"t": "Budget follows the signal", "d": "Funding, M&A, new facility openings, CMS rule changes and C-suite moves all precede spend, but they sit scattered across filings, news and job boards."},
            {"t": "Trust decides the shortlist", "d": "Buyers research vendors through peers, analysts and now AI answer engines. If you are not the trusted, cited name, you never make the evaluation."},
            {"t": "Research happens in silence", "d": "Health systems and referring orgs study your site for weeks without ever filling out a form. That quiet intent is your warmest pipeline."},
        ],
        "signals": [
            "Funding round", "Health-system merger / M&A", "New facility or clinic opening",
            "Service-line launch", "CMIO / CNIO / C-suite change", "FDA clearance or approval",
            "Clinical-trial milestone", "Clinical & tech hiring surge", "Payer / provider partnership",
            "Earnings & regulatory filings",
        ],
        "agents": [
            {"slug":"health-tech-account-tracker","name":"Health-Tech Account Tracker","base":"ABM Signal Tracker","badge":"LIVE","accent":"#22d3ee","accent2":"#38bdf8",
             "role":"Live Health-Tech Universe","metric":"1,251 organizations · scored weekly",
             "icon": _isvg('<path d="M3 21h18"/><path d="M6 21V6a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v15"/><path d="M12 8v4M10 10h4"/><path d="M10 21v-4h4v4"/>'),
             "use":"Your health-tech universe, already live. 1,251 provider, payer, digital-health and medtech companies tracked for funding, C-suite moves, M&A and news, scored weekly.",
             "summary":"Your health-tech market, already mapped. Health-Tech Account Tracker comes preloaded with 1,251 provider, payer, digital-health and medtech organizations, watched for the signals that come before a budget.",
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
            {"slug":"buyer-referrer-de-anonymization","name":"Buyer & Referrer De-anonymization","base":"Anonymous Website Visitors","badge":"NEW","accent":"#34d399","accent2":"#2dd4aa",
             "role":"Visitor Identification","metric":"Recovers 95%+ of lost visitors",
             "icon": _isvg('<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>'),
             "use":"Reveals the health systems, referring clinics and employer groups browsing your site, even when they never fill out a form, and reconstructs the pages they read.",
             "summary":"Turn silent website traffic into named organizations. It reveals the health systems, referring clinics and employer groups browsing your site and hands your team a ready-to-act narrative.",
             "benefit":"Recover the visitors who never fill out a form, and reach them while intent is still warm.",
             "how":"Visit data is matched to firmographic identity, the session journey is reconstructed page by page, and each visitor becomes a first-person narrative with suggested outreach.",
             "who":"Website, demand-gen and outreach teams who want to act on anonymous intent.",
             "connects":["Your website","GTM","CRM","Sheets"],
             "out":[{"t":"The organizations on your site","s":"Named, even without a form fill.","w":90},{"t":"The pages they read","s":"Reconstructed session by session.","w":78},{"t":"A ready outreach narrative","s":"So reps can act while intent is warm.","w":74}]},

        ],
        "plays": [
            {"t": "Account-based demand", "d": "Stand up campaigns aimed at in-market systems and payers the instant a funding, expansion or leadership signal peaks, within healthcare ad policy."},
            {"t": "Buyer & committee intelligence", "d": "Map the CMIO, CNIO, revenue-cycle and procurement committee at every target, and re-engage a champion the moment they move."},
            {"t": "SEO, GEO & category authority", "d": "Win the searches and AI answers your buyers read when they build a shortlist, so you are the trusted, cited vendor."},
            {"t": "Pipeline routing & execution", "d": "One intent score per account, explained in plain language and routed with suggested outreach into HubSpot, Salesforce and Slack."},
        ],
    },
    {
        "slug": "healthcare",
        "name": "Healthcare & Patient Growth",
        "short": "Healthcare",
        "featured": False,
        "accent": "#fb7185", "accent2": "#c084fc",
        "icon": _isvg('<path d="M12 20.3S3.6 14.6 3.6 8.8A4.4 4.4 0 0 1 12 6a4.4 4.4 0 0 1 8.4 2.8c0 5.8-8.4 11.5-8.4 11.5z"/><path d="M6.4 12h2l1.3-2.4L12 14l1.4-3 1 1h3"/>'),
        "eyebrow": "Industry · Healthcare · Patient Growth",
        "headline": "You take care of your patients.",
        "headline_ital": "We take care of finding them.",
        "lead": "Patients choose the practice they trust, the one that shows up when they search, answers their questions and earns strong reviews. Intelligence watches every search, answer engine, review and call, then helps your team win the near-me moment, build the trust and turn quiet interest into booked visits, across every location you run.",
        "stats": [
            {"v": "4",    "l": "AI answer engines tracked"},
            {"v": "3",    "l": "patient-voice sources unified"},
            {"v": "24/7", "l": "visibility & reputation tracking"},
            {"v": "6",    "l": "agents tuned for patient growth"},
        ],
        "segments": [
            "Multi-location clinics", "Hospitals & health systems",
            "Dental & DSOs", "Dermatology & med-spa",
            "Behavioral & mental health", "Urgent & primary care",
        ],
        "chips": ["Patient searching", "You’re the answer", "Visit booked"],
        "pains": [
            {"t": "Patients decide on trust", "d": "Before booking, patients read reviews, check credentials and ask AI. If your reputation and expertise are not visible, they choose the practice that is."},
            {"t": "Every location competes locally", "d": "Each clinic wins or loses its own near-me search. One wrong address, stale hours or a thin review profile quietly sends patients to the practice down the road."},
            {"t": "The search box moved to AI", "d": "Patients now ask ChatGPT, Gemini and Google’s AI about symptoms, treatments and the best clinic near them. If you are not the cited answer, you are invisible."},
            {"t": "Reputation lives in a thousand voices", "d": "Calls, reviews and surveys hold the truth about patient experience, yet they sit scattered across tools no one reads together."},
        ],
        "signals": [
            "Condition & symptom searches", "Near-me & best-clinic queries", "AI answer citations",
            "New Google reviews", "Review sentiment shifts", "Call volume & topics",
            "Competitor clinic openings", "Service-line demand spikes", "Rating drop by location",
            "Survey & NPS changes",
        ],
        "agents": [
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
            {"slug":"condition-treatment-brief-architect","name":"Condition & Treatment Brief Architect","base":"Content Brief Generator","accent":"#e879f9","accent2":"#c084fc",
             "role":"Content Briefs","metric":"Condition · symptom · treatment · near-me",
             "icon": _isvg('<path d="M4 4h16v12H7l-3 3z"/><path d="M8 9h8M8 12h5"/>'),
             "use":"Builds research-backed briefs for condition, symptom, treatment and near-me pages, mapped to the searches and AI prompts driving demand in your service lines.",
             "summary":"Research-backed briefs for the condition, symptom, treatment and near-me pages your patients actually search for.",
             "benefit":"Your writers start from a plan grounded in real demand, mapped to the searches and AI prompts driving your service lines.",
             "how":"It analyzes the questions patients ask across search and AI engines, clusters them by service line, and builds a structured brief with headings, questions to answer and sources.",
             "who":"Content strategists and writers building service-line pages.",
             "connects":["Search Console","AI engines","Semrush","Sheets"],
             "out":[{"t":"Briefs mapped to real demand","s":"The questions patients truly search.","w":86},{"t":"Structured and ready to write","s":"Headings, questions and sources included.","w":80},{"t":"Organized by service line","s":"So every page has a clear job.","w":74}]},
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
        ],
        "plays": [
            {"t": "SEO & local growth", "d": "Win the condition, treatment and near-me searches patients run, and keep every location accurate and discoverable on Google Maps."},
            {"t": "Answer-engine visibility (GEO)", "d": "Become the clinic AI engines name when a patient asks about a symptom, a treatment or the best care near them."},
            {"t": "Reputation & patient experience", "d": "Turn calls, reviews and surveys into a live sentiment score for every location, and act on what patients actually feel."},
            {"t": "Content & clinical trust", "d": "Reviewer-backed, E-E-A-T-strong pages that earn patient trust and AI citations across every service line."},
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
             "name": "Thought-Leadership Brief Architect", "base": "Content Brief Generator",
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

# Font-variant duplicate of the Healthcare (Patient Growth) page, requested as a one-off Poppins-font
# test page. Exact copy of the 'healthcare' industry content, reachable only at /industries/healthcare-poppins
# (deliberately NOT added to the INDUSTRIES list, so it does not appear on /industries or in any
# "more industries" rail — it is a standalone duplicate page, not a real industry).
import copy as _copy
_healthcare_poppins = _copy.deepcopy(INDUSTRIES_BY_SLUG["healthcare"])
_healthcare_poppins["slug"] = "healthcare-poppins"
INDUSTRIES_BY_SLUG["healthcare-poppins"] = _healthcare_poppins

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

# "Why Intelligence" comparison page. UNLINKED by design (no nav/footer entry) so it
# can be reviewed and shared privately before being promoted. Reachable only by direct URL.
@app.route("/why-intelligence")
def why_intelligence_page():
    return render_template("agents.html", page="why", agents=AGENTS, agent=None, related=[])


# Industries feature is UNLINKED but live: the routes below are registered so the
# URLs work for anyone with a direct link, but the nav link in agents.html stays
# commented out so the pages are not reachable by navigating the site.
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
    "northstar": {
        "name":        "NorthStar Anesthesia",
        "description": "NorthStar Anesthesia ABM universe — health systems, hospitals, and anesthesia/ASC groups tracked for funding, M&A, C-suite moves, and news signals.",
        "icon":        "🩺",
        "accent":      "#5b9dff",
        "dashboard":   Path(__file__).parent / "reports" / "dashboard_northstar.html",
    },
}

# ── Auth helpers ────────────────────────────────────────────────────────────────
ADMIN_EMAILS = {"krishna.ladha@position2.com", "sudheer.d@position2.com", "reporting@position2.com", "sparikh@position2.com", "abhilash.dg@position2.com"}

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
        email = user.get("email", "").lower()
        if not email.endswith("@position2.com"):
            return redirect("/app")          # external users never see internal /p2
        if email not in ADMIN_EMAILS:
            abort(403)                        # Position2 non-admins: forbidden
        return f(*args, **kwargs)
    return decorated

def position2_required(f):
    """Gate an internal /p2 route to @position2.com Google accounts.
    Logged-out visitors are sent to sign in (remembering their target); signed-in
    non-Position2 users are bounced to their blank signed-in home (/app)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _get_user()
        if not user:
            return _login_redirect()
        if not user.get("email", "").lower().endswith("@position2.com"):
            return redirect("/app")
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
    # v17: sign-in is open to ANY Google account. Area-level access control is enforced
    # separately -- only @position2.com may reach the internal /p2/* pages (see
    # position2_required). General users land on the blank signed-in home (/app).

    session["google_user"] = {
        "email":      email,
        "name":       idinfo.get("name", ""),
        "given_name": idinfo.get("given_name", ""),
        "picture":    idinfo.get("picture", ""),
    }
    session.permanent = True
    nxt = session.pop("next_url", None)
    if not (isinstance(nxt, str) and nxt.startswith("/") and not nxt.startswith("//")):
        # No deep link: @position2.com staff land on the internal hub (/p2/hub);
        # everyone else lands on the public signed-in home (/app). An explicit
        # next_url (e.g. a shared /p2/admin/... link) still takes precedence.
        nxt = "/p2/hub" if email.lower().endswith("@position2.com") else "/app"
    # Route sign-in logging: @position2.com -> always Internal Usage,
    # PLUS Public Page Analytics too when landing on the public /app surface (not
    # deep-linking straight into /p2). Everyone else -> Public Page Analytics only.
    if email.lower().endswith("@position2.com"):
        _log_login_to_sheet(session["google_user"])   # fire-and-forget, fails silently
        if not nxt.startswith("/p2"):
            _log_member_signin(session["google_user"])
    else:
        _log_member_signin(session["google_user"])    # public member -> Public Page Analytics
    resp = jsonify({"success": True, "redirect": nxt})
    # Mark this browser as having signed in before, so the login page can greet
    # returning visitors with "Welcome back." (first-timers see "Welcome.").
    resp.set_cookie("p2_seen", "1", max_age=60*60*24*365, samesite="Lax", secure=True)
    return resp


# ── Core routes ─────────────────────────────────────────────────────────────────

@app.route("/robots.txt")
def robots_txt():
    from flask import Response
    return Response("User-agent: *\nDisallow: /\n", mimetype="text/plain")

@app.route("/favicon.ico")
def favicon_ico():
    return send_from_directory("static", "favicon.ico", mimetype="image/x-icon")


@app.route("/favicon.svg")
def favicon():
    return send_from_directory("static", "favicon.svg", mimetype="image/svg+xml")


@app.route("/")
def index():
    u = _get_user()
    if u:
        # Staff go straight to the internal hub; everyone else to the public home.
        return redirect("/p2/hub" if u.get("email", "").lower().endswith("@position2.com") else "/app")
    return render_template("agents.html", page="home", agents=AGENTS, agent=None,
                           related=[], signals_list=SIGNALS)

# ── Public agents on the signed-in home (/app) ───────────────────────────────────
# These are the SAME SEO tools embedded internally at /p2/seo/<seo_slug> (served by
# the SERP app), just re-presented for ALL signed-in Google users under public,
# fancily-named slugs. "Use this agent" embeds the live tool (see /app/<slug>/use).
_SVG_COMPASS = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" '
    'stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/>'
    '<polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76"/></svg>')
_SVG_BRIEF = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" '
    'stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>'
    '<path d="M14 2v6h6"/><path d="M8 13h8"/><path d="M8 17h5"/></svg>')
_SVG_ALCHEMY = ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" '
    'stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l1.9 5.3L19 10l-5.1 1.7L12 17l-1.9-5.3L5 10z"/>'
    '<path d="M18 14l.8 2.2L21 17l-2.2.8L18 20l-.8-2.2L15 17l2.2-.8z"/></svg>')

def _asvg(inner: str) -> str:
    """Icon wrapper for APP_AGENTS entries that mirrors _SVG_COMPASS/_BRIEF/_ALCHEMY's
    style (currentColor + 1.9 stroke) so agents ported over from the marketing AGENTS
    list (which uses a fixed white stroke) render consistently with the original three."""
    return ('<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" '
            'stroke-linecap="round" stroke-linejoin="round">' + inner + "</svg>")

APP_AGENTS = [
    {
        "slug": "keyword-finder", "name": "Keyword Finder",
        "tagline": "AI keyword discovery, scoring & clustering",
        "seo_slug": "keyword-research", "ac": "#22d3ee", "ac2": "#6366f1", "icon": _SVG_COMPASS,
        "pill1": "Keyword intelligence", "pill2": "SERP · AI · clustering",
        "lead": ("Point it at a topic and it finds the keywords worth chasing. Keyword Finder "
            "pulls candidate terms, enriches them with live search data, then scores and clusters "
            "them into clean topic groups so you know exactly what to target next."),
        "trips": [
            {"t": "What it does", "d": "Shortlists high-opportunity keywords with AI and groups them into topic clusters you can act on."},
            {"t": "How it works", "d": "Gathers candidate terms, layers on SERP and volume signals, then scores and clusters each one by intent and opportunity."},
            {"t": "Best for", "d": "SEO and content teams deciding what to write and which terms are actually worth the effort."},
        ],
        "tags": ["Keywords", "SERP", "Clustering", "Intent", "AI"],
    },
    {
        "slug": "content-brief-generator", "name": "Content Brief Generator",
        "tagline": "Structured, SERP-backed content briefs",
        "seo_slug": "article-recommendation", "ac": "#8b5cf6", "ac2": "#e879f9", "icon": _SVG_BRIEF,
        "pill1": "Content briefs", "pill2": "SERP · outlines · entities",
        "lead": ("Turn a target keyword into a ready-to-write brief. Content Brief Generator studies the "
            "pages currently ranking and assembles the structure, headings, questions and entities "
            "your article needs to compete, so writers start with a plan instead of a blank page."),
        "trips": [
            {"t": "What it does", "d": "Builds a complete, competitor-informed content brief from a single target keyword."},
            {"t": "How it works", "d": "Analyses the live top-ranking results, extracts their structure, entities and FAQs, and composes a recommended outline."},
            {"t": "Best for", "d": "Writers and strategists who want briefs that already reflect what it takes to rank."},
        ],
        "tags": ["Briefs", "SERP", "Outlines", "Entities", "Content"],
    },
    {
        "slug": "content-enhancer", "name": "Content Enhancer",
        "tagline": "Multi-LLM + SERP article enhancement",
        "seo_slug": "article-enhancement", "ac": "#34d399", "ac2": "#22d3ee", "icon": _SVG_ALCHEMY,
        "pill1": "Content enhancement", "pill2": "multi-LLM · SERP · E-E-A-T",
        "lead": ("Feed it an existing article and it levels the piece up. Content Enhancer analyzes "
            "your page and returns concrete recommendations on structure, depth and authority, "
            "drawing on multiple language models to spot gaps a single model would miss."),
        "trips": [
            {"t": "What it does", "d": "Upgrades an already-published article with specific, LLM-concepts-aware improvements you can act on right away."},
            {"t": "How it works", "d": "Runs multi-LLM analysis and returns concrete edits and gaps to close, weighing each model's take to surface the fixes that matter most."},
            {"t": "Best for", "d": "Refreshing existing content that has stalled or is being out-ranked — before it falls further behind."},
        ],
        "tags": ["Enhance", "LLM", "SERP", "E-E-A-T"],
    },

    # Agents below are shown publicly (dashboard, sidebar, detail pages) but are
    # not yet connected to a live tool -- no "seo_slug", so every reader of
    # APP_AGENTS must treat that as the single source of truth for "connected"
    # vs. "request access" (see _app_embed_url, app_home, app_detail, app.html,
    # app_detail.html). Ported from the marketing /agents directory.
    {
        "slug": "signal-tracker", "name": "ABM Signal Tracker",
        "tagline": "Account Intent Monitoring",
        "ac": "#a78bfa", "ac2": "#818cf8", "icon": _asvg("<path d=\"M3 3v18h18\"/><path d=\"M7 14l4-4 3 3 5-6\"/>"),
        "pill1": "Account Intent Monitoring", "pill2": "26 signal types \u00b7 scored weekly",
        "lead": ("Always-on monitoring for your target-account universe. Signal Tracker watches for every buying signal, scores each one, and resurfaces the highest-intent companies every week."),
        "trips": [
            {"t": "What it does", "d": "You never miss the moment an account becomes ready. The most urgent opportunities rise to the top automatically \u2014 no manual list-scrubbing."},
            {"t": "How it works", "d": "It ingests curated, authenticated high-tier sources plus open-web news, scores each event as type_weight \u00d7 severity \u00d7 recency (with a bonus when signals stack), keeps a rolling 90-day window, and ships a weekly digest."},
            {"t": "Best for", "d": "Demand-gen and sales teams running account-based programs."},
        ],
        "tags": ["Curated sources", "News", "Slack", "Sheets"],
    },
    {
        "slug": "generative-search-visibility", "name": "Generative Search Visibility",
        "tagline": "AI Answer-Engine Tracking",
        "ac": "#22d3ee", "ac2": "#38bdf8", "icon": _asvg("<circle cx=\"11\" cy=\"11\" r=\"7\"/><path d=\"m21 21-3.4-3.4\"/><path d=\"M11 7.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z\"/>"),
        "pill1": "AI Answer-Engine Tracking", "pill2": "ChatGPT \u00b7 Gemini \u00b7 Perplexity",
        "lead": ("See exactly where your brand shows up across AI answer engines \u2014 ChatGPT, Google AI Overviews, Gemini and Perplexity \u2014 track your share of voice, and find the prompts you are losing."),
        "trips": [
            {"t": "What it does", "d": "Win the answer, not just the link. Know your AI share of voice, which sources get cited, and where to act before a competitor owns the response."},
            {"t": "How it works", "d": "It monitors branded and category prompts across major AI engines, measures mention frequency and share of voice over time, maps the domains and pages being cited, and flags the gaps to close."},
            {"t": "Best for", "d": "SEO, content and brand teams defending visibility in AI search."},
        ],
        "tags": ["Brand Radar", "AI engines", "GSC", "Sheets"],
    },
    {
        "slug": "anonymous-visitors", "name": "Anonymous Website Visitors",
        "tagline": "Visitor De-anonymization",
        "ac": "#34d399", "ac2": "#2dd4bf", "icon": _asvg("<circle cx=\"12\" cy=\"8\" r=\"4\"/><path d=\"M4 21c0-4 4-6 8-6s8 2 8 6\"/>"),
        "pill1": "Visitor De-anonymization", "pill2": "Recovers 95%+ of lost visitors",
        "lead": ("Turn silent website traffic into named accounts. Anonymous Visitor ID reveals the companies \u2014 and the people \u2014 browsing your site, and hands reps a ready-to-act narrative."),
        "trips": [
            {"t": "What it does", "d": "Recover the 95%+ of visitors who never fill out a form, and reach them while the intent is still warm."},
            {"t": "How it works", "d": "Visit data is matched to firmographic and person-level identity, the session journey is reconstructed page-by-page, and each visitor becomes a first-person CRM narrative with suggested outreach."},
            {"t": "Best for", "d": "Website, demand-gen and SDR teams who want to act on anonymous intent."},
        ],
        "tags": ["GTM", "Sheets", "CRM"],
    },
    {
        "slug": "technical-seo-geo-auditor", "name": "Technical SEO & GEO Auditor",
        "tagline": "Site Health & AI Readiness",
        "ac": "#38bdf8", "ac2": "#22d3ee", "icon": _asvg("<path d=\"M9 11l3 3 8-8\"/><path d=\"M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11\"/>"),
        "pill1": "Site Health & AI Readiness", "pill2": "200+ checks \u00b7 scored in seconds",
        "lead": ("A full technical, on-page and GEO audit of any site \u2014 200+ checks scored and ranked \u2014 with AI-written fixes your team can ship immediately."),
        "trips": [
            {"t": "What it does", "d": "Replace week-long manual audits with a scored, prioritized fix-list in seconds, so the highest-impact issues get fixed first."},
            {"t": "How it works", "d": "It crawls the site, runs 200+ technical, on-page, structured-data and answer-engine checks, scores each by impact, and generates specific, AI-written recommendations."},
            {"t": "Best for", "d": "SEO leads, technical SEOs and web teams."},
        ],
        "tags": ["Crawl", "GSC", "Sheets"],
    },
    {
        "slug": "ai-readiness-auditor", "name": "AI Readiness Auditor",
        "tagline": "Answer-Engine Optimization",
        "ac": "#818cf8", "ac2": "#a78bfa", "icon": _asvg("<rect x=\"4\" y=\"8\" width=\"16\" height=\"11\" rx=\"3\"/><path d=\"M12 8V4\"/><circle cx=\"9\" cy=\"13.5\" r=\"1.1\"/><circle cx=\"15\" cy=\"13.5\" r=\"1.1\"/>"),
        "pill1": "Answer-Engine Optimization", "pill2": "Score any site in ~15 seconds",
        "lead": ("Score any site's readiness to be understood and cited by AI agents and answer engines \u2014 in about 15 seconds \u2014 with the exact fixes to get picked up."),
        "trips": [
            {"t": "What it does", "d": "Get ahead of the shift to AI search. Know precisely what is blocking your pages from being cited, and how to fix it."},
            {"t": "How it works", "d": "It evaluates structure, schema, crawlability, content clarity and machine-readability against answer-engine best practices, then returns a score and prioritized fixes."},
            {"t": "Best for", "d": "SEO and content teams future-proofing for AI search."},
        ],
        "tags": ["Crawl", "Schema", "GSC"],
    },
    {
        "slug": "content-authority-optimizer", "name": "Content Authority Optimizer",
        "tagline": "On-Page & E-E-A-T",
        "ac": "#e879f9", "ac2": "#c084fc", "icon": _asvg("<path d=\"M12 3l7 3v5c0 4.5-3 7.6-7 9-4-1.4-7-4.5-7-9V6z\"/><path d=\"M9 12l2 2 4-4\"/>"),
        "pill1": "On-Page & E-E-A-T", "pill2": "Tuned for AEO & E-E-A-T",
        "lead": ("Audit and rewrite existing pages for structure, depth and authority signals \u2014 tuned for answer engines and E-E-A-T \u2014 so pages climb."),
        "trips": [
            {"t": "What it does", "d": "Get more from content you have already published. Targeted, authority-building edits that move rankings without a full rewrite."},
            {"t": "How it works", "d": "It assesses topical depth, structure, internal links and trust signals, then recommends specific edits aligned to E-E-A-T and AEO."},
            {"t": "Best for", "d": "Content and SEO teams optimizing existing libraries."},
        ],
        "tags": ["GSC", "CMS", "Docs"],
    },
    {
        "slug": "competitor-seo-intelligence", "name": "Competitor SEO Intelligence",
        "tagline": "Organic Benchmarking",
        "ac": "#fb7185", "ac2": "#f472b6", "icon": _asvg("<circle cx=\"12\" cy=\"12\" r=\"9\"/><circle cx=\"12\" cy=\"12\" r=\"4.5\"/><path d=\"M12 3v3M12 18v3M3 12h3M18 12h3\"/>"),
        "pill1": "Organic Benchmarking", "pill2": "Gaps \u00b7 backlinks \u00b7 authority",
        "lead": ("Benchmark your organic footprint against any rival \u2014 keyword gaps, backlink profiles, authority and page speed \u2014 with the plays to overtake them."),
        "trips": [
            {"t": "What it does", "d": "See exactly where competitors beat you and where they are exposed, then act on a prioritized gap list."},
            {"t": "How it works", "d": "It compares domains across rankings, keyword gaps, backlinks and authority, validates the findings, and drafts opportunity-and-recommendation notes."},
            {"t": "Best for", "d": "SEO leads and growth teams in competitive markets."},
        ],
        "tags": ["Semrush", "Ahrefs", "Sheets"],
    },
    {
        "slug": "local-visibility-builder", "name": "Local Visibility Builder",
        "tagline": "Multi-Location SEO",
        "ac": "#2dd4bf", "ac2": "#34d399", "icon": _asvg("<path d=\"M12 21s-7-6.3-7-11a7 7 0 0 1 14 0c0 4.7-7 11-7 11z\"/><circle cx=\"12\" cy=\"10\" r=\"2.6\"/>"),
        "pill1": "Multi-Location SEO", "pill2": "Dev-ready local pages at scale",
        "lead": ("Compose approved, developer-ready location and service pages at scale \u2014 with optimized copy and image alt text \u2014 for multi-location brands."),
        "trips": [
            {"t": "What it does", "d": "Launch hundreds of consistent, optimized local pages without the manual grind, and capture 'near me' demand."},
            {"t": "How it works", "d": "It assembles location and service pages from approved templates and data, generates optimized copy and bulk image alt tags, and outputs dev-ready pages."},
            {"t": "Best for", "d": "Local SEO and web teams managing many locations."},
        ],
        "tags": ["Sheets", "CMS", "Maps"],
    },
    {
        "slug": "search-term-intelligence", "name": "Search Term Intelligence",
        "tagline": "Search Query Mining",
        "ac": "#f472b6", "ac2": "#fb7185", "icon": _asvg("<circle cx=\"11\" cy=\"11\" r=\"7\"/><path d=\"m21 21-3.4-3.4\"/><path d=\"M8 10h6M8 13h4\"/>"),
        "pill1": "Search Query Mining", "pill2": "Negatives + winners, weekly",
        "lead": ("Mine every paid search query for waste and hidden winners \u2014 auto-suggested negative keywords and new keyword opportunities, every week."),
        "trips": [
            {"t": "What it does", "d": "Cut wasted spend and capture converting terms you are not bidding on, without manually combing search-term reports."},
            {"t": "How it works", "d": "It classifies search terms by relevance and performance, flags high-spend/no-conversion waste, and recommends negatives and new keywords."},
            {"t": "Best for", "d": "Paid search and performance teams."},
        ],
        "tags": ["Google Ads", "Microsoft Ads", "Sheets"],
    },
    {
        "slug": "linkedin-intelligence", "name": "LinkedIn Intelligence",
        "tagline": "Engagement Signals",
        "ac": "#0ea5e9", "ac2": "#38bdf8", "icon": _asvg("<rect x=\"3\" y=\"3\" width=\"18\" height=\"18\" rx=\"3\"/><path d=\"M7 17v-5M12 17V8M17 17v-3\"/>"),
        "pill1": "Engagement Signals", "pill2": "Buying-committee engagement",
        "lead": ("Know which members of the buying committee are already paying attention. LinkedIn Intelligence captures engagement signals and maps them to your target accounts."),
        "trips": [
            {"t": "What it does", "d": "Prioritize the people actually engaging \u2014 not just the logo \u2014 so outreach lands with the right person at the right time."},
            {"t": "How it works", "d": "It tracks engagement on relevant posts and profiles, attributes it to your accounts, and scores buying-committee interest for ABM plays."},
            {"t": "Best for", "d": "ABM and social-selling teams."},
        ],
        "tags": ["LinkedIn", "GTM", "CRM"],
    },
    {
        "slug": "linkedin-strategy-researcher", "name": "LinkedIn Strategy Researcher",
        "tagline": "Competitive LinkedIn content analysis",
        "ac": "#3b82f6", "ac2": "#a855f7",
        "icon": _asvg("<path d=\"M12 7c-2-1.2-4.7-1.8-8-1.8V18c3.3 0 6 .6 8 1.8 2-1.2 4.7-1.8 8-1.8V5.2c-3.3 0-6 .6-8 1.8z\"/><path d=\"M12 7v12.8\"/>"),
        "pill1": "Competitive LinkedIn Analysis", "pill2": "Messaging · creative · cadence · AI playbook",
        "lead": ("Decode any company's organic LinkedIn strategy in one report. Point it at a company and it reads a year of their posts, then breaks down the messaging, content mix, creative formats, engagement and posting cadence, and hands you an AI playbook of moves to run."),
        "trips": [
            {"t": "What it does", "d": "Turns any company's public LinkedIn presence into a full strategy report: messaging themes, content categories, creative formats, engagement benchmarks, top posts and a 30/60/90 AI playbook."},
            {"t": "How it works", "d": "Pick the exact company page and it pulls the last 12 months of organic posts, analyses the copy, creative and reactions, scores the account, and writes a prioritised set of recommendations."},
            {"t": "Best for", "d": "Marketing and demand-gen teams benchmarking their own LinkedIn against competitors and planning what to post next."},
        ],
        "tags": ["LinkedIn", "Competitive", "Content", "Engagement", "AI"],
    },
    {
        "slug": "ad-intelligence", "name": "Competitor Ad Intelligence",
        "tagline": "Competitive Creative",
        "ac": "#a855f7", "ac2": "#e879f9", "icon": _asvg("<path d=\"M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z\"/><circle cx=\"12\" cy=\"12\" r=\"3\"/>"),
        "pill1": "Competitive Creative", "pill2": "Live competitor creative tracking",
        "lead": ("See exactly what your competitors are running. Competitor Ad Intelligence tracks live competitor creative so your messaging stays a step ahead."),
        "trips": [
            {"t": "What it does", "d": "Stop guessing competitor strategy \u2014 watch their real ads, formats and shifts over time."},
            {"t": "How it works", "d": "It continuously collects competitor ads across platforms and surfaces messaging themes, creative formats, and changes as they happen."},
            {"t": "Best for", "d": "Paid media, brand and competitive-intelligence teams."},
        ],
        "tags": ["Paid social", "Search", "Brand"],
    },
    {
        "slug": "gbp-qc-agent", "name": "GBP QC Agent",
        "tagline": "Google Business Profile QC",
        "ac": "#f97316", "ac2": "#fbbf24", "icon": _asvg("<path d=\"M12 21s-7-6.3-7-11a7 7 0 0 1 14 0c0 4.7-7 11-7 11z\"/><path d=\"M9 10l2 2 4-4\"/>"),
        "pill1": "Google Business Profile QC", "pill2": "3-stage QC \u00b7 brand-checked",
        "lead": ("A quality-control and content-generation tool for Google Business Profile posts. Run base content through a 3-stage workflow \u2014 base review, location-specific QC, and automated location content generation \u2014 all checked against your brand guidelines."),
        "trips": [
            {"t": "What it does", "d": "Ship on-brand GBP posts at multi-location scale without manual review \u2014 every post scored and checked against the client's exact rules, with the fixes written for you."},
            {"t": "How it works", "d": "Pick a client and a stage. It runs base-content review, location-specific QC against the approved base, and automated location content generation \u2014 scoring each post 0-100, flagging only real violations by severity, and returning recommended fixes plus a corrected version, all against client brand guidelines."},
            {"t": "Best for", "d": "Local and multi-location SEO and content teams managing Google Business Profile posts across many locations and clients."},
        ],
        "tags": ["Google Business Profile", "Brand guidelines", "Sheets"],
    },
    {
        "slug": "on-page-auditor", "name": "On-Page SEO Auditor",
        "tagline": "On-Page Optimization",
        "ac": "#14b8a6", "ac2": "#2dd4bf", "icon": _asvg("<circle cx=\"11\" cy=\"11\" r=\"7\"/><path d=\"m21 21-3.4-3.4\"/><path d=\"M8.4 11l2 2 3.4-3.4\"/>"),
        "pill1": "On-Page Optimization", "pill2": "23 sections \u00b7 live CWV + PageSpeed",
        "lead": ("A full on-page audit of any URL \u2014 23 sections spanning URL structure, meta, headings, content, schema, Core Web Vitals, canonicals, OG tags and crawlability \u2014 scored against live data."),
        "trips": [
            {"t": "What it does", "d": "Find and fix every on-page issue holding a page back, ranked by impact and backed by live PageSpeed and Core Web Vitals data \u2014 no guesswork."},
            {"t": "How it works", "d": "Enter a URL and primary keywords; it pulls live performance data and runs 23 audit sections across technical, content, schema and performance, scoring each and returning prioritized fixes."},
            {"t": "Best for", "d": "SEO and web teams optimizing individual pages."},
        ],
        "tags": ["PageSpeed", "Crawl", "GSC"],
    },
    {
        "slug": "hub-spoke-architect", "name": "Hub & Spoke Architect",
        "tagline": "Internal-Linking Strategy",
        "ac": "#8b5cf6", "ac2": "#a78bfa", "icon": _asvg("<circle cx=\"12\" cy=\"12\" r=\"3\"/><circle cx=\"5\" cy=\"5\" r=\"2\"/><circle cx=\"19\" cy=\"5\" r=\"2\"/><circle cx=\"5\" cy=\"19\" r=\"2\"/><circle cx=\"19\" cy=\"19\" r=\"2\"/><path d=\"M10 10 6.5 6.5M14 10 17.5 6.5M10 14 6.5 17.5M14 14 17.5 17.5\"/>"),
        "pill1": "Internal-Linking Strategy", "pill2": "AI clusters \u00b7 linking map",
        "lead": ("Turn a URL list or an existing spreadsheet into an AI-categorized hub-and-spoke structure, then generate targeted internal-linking recommendations across every cluster."),
        "trips": [
            {"t": "What it does", "d": "Build topical authority and route link equity where it counts \u2014 a clear, approved internal-linking plan instead of ad-hoc guesswork."},
            {"t": "How it works", "d": "Upload a hub/spoke sheet or paste a URL list; AI auto-categorizes pages into clusters, you review and approve the structure, and it generates anchor-text and internal-link recommendations."},
            {"t": "Best for", "d": "SEO teams running content clusters and topical-authority plays."},
        ],
        "tags": ["Sheets", "Crawl", "CMS"],
    },
    {
        "slug": "robots-monitor", "name": "Robots & Index Monitor",
        "tagline": "Index-Health Monitoring",
        "ac": "#f59e0b", "ac2": "#fbbf24", "icon": _asvg("<path d=\"M7 3h8l5 5v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z\"/><path d=\"M14 3v5h5\"/><path d=\"M9.5 13.5l4 4M13.5 13.5l-4 4\"/>"),
        "pill1": "Index-Health Monitoring", "pill2": "Daily noindex alerts",
        "lead": ("Automatically crawl sitemaps, sample pages by type, and verify noindex signals across production and staging \u2014 with instant Slack alerts the moment a live page goes dark."),
        "trips": [
            {"t": "What it does", "d": "Catch accidental noindex and deindexing before it tanks traffic \u2014 automated daily checks instead of manual spot-checks."},
            {"t": "How it works", "d": "It crawls your sitemaps, samples pages by template, verifies index/noindex on production and staging domains, and fires a Slack alert if a production page is suddenly noindexed."},
            {"t": "Best for", "d": "Technical SEO and web teams guarding against accidental deindexing."},
        ],
        "tags": ["Sitemaps", "Slack", "Crawl"],
    },
]
APP_AGENTS_BY_SLUG = {a["slug"]: a for a in APP_AGENTS}

# Old slugs from before the agents were renamed (Keyword Compass -> Keyword
# Finder, etc). Kept so links already shared/bookmarked under the old URLs
# still resolve, via a 301 to the new canonical slug.
_LEGACY_AGENT_SLUGS = {
    "keyword-compass": "keyword-finder",
    "brief-architect": "content-brief-generator",
    "content-alchemist": "content-enhancer",
}

# ── Per-agent run cap ─────────────────────────────────────────────────────────
# A "run" = one load of /app/<slug>/use (opening the embedded tool). We can't see
# inside the iframe (it's a separate app), so this is the closest honest signal
# we have to "the user ran this agent" — logged to its own sheet tab so it's
# visible to admins and enforceable across devices/browsers (unlike the
# localStorage-based "recently opened" list, which is just a UX nicety).
AGENT_RUN_CAP = 10
_AR_TAB = "Agent Runs"
_AR_HEADER = ["Timestamp (IST)", "Date", "Email", "Name", "Agent Slug", "Agent Name"]

def _log_agent_run(user: dict, agent: dict) -> None:
    """Append one agent-run event to the 'Agent Runs' tab. Fails silently."""
    if not LOGIN_LOG_SHEET_ID:
        return
    try:
        svc = _va_sheets_service()
        if not svc:
            return
        now = datetime.now(IST)
        row = [now.strftime("%Y-%m-%d %H:%M:%S IST"), now.strftime("%Y-%m-%d"),
               user.get("email", ""), user.get("name", ""), agent.get("slug", ""), agent.get("name", "")]
        tab = _AR_TAB
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
            svc.spreadsheets().values().append(spreadsheetId=LOGIN_LOG_SHEET_ID,
                range="%s!A1" % tab, valueInputOption="RAW", body={"values": [_AR_HEADER]}).execute()
        svc.spreadsheets().values().append(spreadsheetId=LOGIN_LOG_SHEET_ID, range="%s!A1" % tab,
            valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": [row]}).execute()
    except Exception as e:
        log.warning("agent run log failed: %s", e)

def _canonical_agent_slug(slug: str) -> str:
    """Map a possibly-renamed agent slug to its current canonical one. Every
    reader of the 'Agent Runs' tab must pass slugs through this — rows logged
    before a rename still contain the old slug, and won't otherwise match
    the current APP_AGENTS list (silently under-counting that agent's runs
    and letting a renamed agent's cap be bypassed)."""
    return _LEGACY_AGENT_SLUGS.get(slug, slug)

def _agent_run_counts(email: str) -> dict:
    """Return {agent_slug: run_count} for one user, read fresh from 'Agent Runs'.
    Used both to enforce the cap and to show 'runs left' on the dashboard."""
    counts = {}
    if not LOGIN_LOG_SHEET_ID or not email:
        return counts
    try:
        svc = _va_sheets_service()
        if not svc:
            return counts
        rows = svc.spreadsheets().values().get(
            spreadsheetId=LOGIN_LOG_SHEET_ID, range="%s!A:F" % _AR_TAB).execute().get("values", [])
        rows = rows[1:] if len(rows) > 1 else []
        AR = {n: i for i, n in enumerate(_AR_HEADER)}
        def ac(r, n, d=""):
            i = AR.get(n, -1); return r[i] if 0 <= i < len(r) else d
        e = email.lower()
        for r in rows:
            if (ac(r, "Email") or "").lower() == e:
                slug = ac(r, "Agent Slug")
                if slug:
                    slug = _canonical_agent_slug(slug)
                    counts[slug] = counts.get(slug, 0) + 1
    except Exception as ex:
        log.warning("agent run count read failed: %s", ex)
    return counts

def _fetch_agent_run_stats() -> dict:
    """Per-user, per-agent run counts for the admin 'Public Agent Usage' dashboard."""
    from collections import defaultdict
    svc = _va_sheets_service()
    def read(rng):
        if not svc:
            return []
        try:
            return svc.spreadsheets().values().get(
                spreadsheetId=LOGIN_LOG_SHEET_ID, range=rng).execute().get("values", [])
        except Exception as e:
            log.warning("agent run stats read failed (%s): %s", rng, e)
            return []

    rows = read("%s!A:F" % _AR_TAB)
    rows = rows[1:] if len(rows) > 1 else []
    AR = {n: i for i, n in enumerate(_AR_HEADER)}
    def ac(r, n, d=""):
        i = AR.get(n, -1); return r[i] if 0 <= i < len(r) else d

    by_user = {}
    agent_totals = {}
    for r in rows:
        email = (ac(r, "Email") or "").lower()
        if not email:
            continue
        slug = _canonical_agent_slug(ac(r, "Agent Slug") or "?")
        ts = ac(r, "Timestamp (IST)")
        u = by_user.setdefault(email, {"email": email, "name": "", "total": 0,
                                        "agents": {}, "last_run": ""})
        if ac(r, "Name"):
            u["name"] = ac(r, "Name")
        u["agents"][slug] = u["agents"].get(slug, 0) + 1
        u["total"] += 1
        if ts and ts > u["last_run"]:
            u["last_run"] = ts
        agent_totals[slug] = agent_totals.get(slug, 0) + 1

    agent_meta = {a["slug"]: a for a in APP_AGENTS}
    users_out = []
    for email, u in by_user.items():
        agents_list = []
        for slug, cnt in sorted(u["agents"].items(), key=lambda x: -x[1]):
            meta = agent_meta.get(slug, {})
            agents_list.append({
                "slug": slug, "name": meta.get("name", slug), "count": cnt,
                "cap": AGENT_RUN_CAP, "remaining": max(0, AGENT_RUN_CAP - cnt),
                "at_cap": cnt >= AGENT_RUN_CAP,
                "ac": meta.get("ac", "#8b5cf6"), "ac2": meta.get("ac2", "#22d3ee"),
            })
        users_out.append({"email": u["email"], "name": u["name"] or u["email"],
                           "total": u["total"], "last_run": u["last_run"], "agents": agents_list})
    users_out.sort(key=lambda x: x["last_run"] or "", reverse=True)

    # Only agents connected to a live tool can ever have a run logged — agents
    # awaiting "Request Access" would just pad this list with permanent zeroes,
    # so they're excluded here (unlike agent_meta above, which needs every
    # agent for slug lookups).
    agents_out = [{"slug": a["slug"], "name": a["name"], "runs": agent_totals.get(a["slug"], 0),
                   "cap": AGENT_RUN_CAP, "ac": a["ac"], "ac2": a["ac2"], "icon": a["icon"]}
                  for a in APP_AGENTS if a.get("seo_slug")]
    users_at_cap = sum(1 for u in users_out if any(a["at_cap"] for a in u["agents"]))

    return {
        "configured": bool(svc),
        "cap": AGENT_RUN_CAP,
        "total_runs": sum(agent_totals.values()),
        "total_users": len(users_out),
        "users_at_cap": users_at_cap,
        "agents": agents_out,
        "users": users_out,
    }

# ── Agent access requests ─────────────────────────────────────────────────────
# For agents shown on /app that aren't connected to a live tool yet (no
# "seo_slug" — see APP_AGENTS above), the sidebar CTA is "Request Access"
# instead of "Use this agent". A request is logged once per (email, agent)
# — repeat clicks / page reloads don't re-log or re-notify — and surfaces in
# the "Access Requests" admin dashboard plus a Slack ping, same channel as
# the platform-wide "Request access" form.
_AAR_TAB = "Agent Access Requests"
_AAR_HEADER = ["Timestamp (IST)", "Date", "Email", "Name", "Agent Slug", "Agent Name", "Message"]

def _log_agent_access_request(user: dict, agent: dict, message: str = "") -> bool:
    """Append one access-request row to the 'Agent Access Requests' tab. Returns
    True on success, False (silently) on any failure."""
    if not LOGIN_LOG_SHEET_ID:
        return False
    try:
        svc = _va_sheets_service()
        if not svc:
            return False
        now = datetime.now(IST)
        row = [now.strftime("%Y-%m-%d %H:%M:%S IST"), now.strftime("%Y-%m-%d"),
               user.get("email", ""), user.get("name", ""), agent.get("slug", ""), agent.get("name", ""),
               message]
        tab = _AAR_TAB
        try:
            existing = svc.spreadsheets().values().get(
                spreadsheetId=LOGIN_LOG_SHEET_ID, range="%s!A1:G1" % tab).execute()
            header_row = (existing.get("values") or [[]])[0]
            if not header_row:
                raise Exception("empty")
            # Migrate a tab created before the "Message" column existed (older
            # header row only spans A:F) so appended rows line up with a label.
            if len(header_row) < len(_AAR_HEADER):
                svc.spreadsheets().values().update(spreadsheetId=LOGIN_LOG_SHEET_ID,
                    range="%s!A1" % tab, valueInputOption="RAW", body={"values": [_AAR_HEADER]}).execute()
        except Exception:
            try:
                svc.spreadsheets().batchUpdate(spreadsheetId=LOGIN_LOG_SHEET_ID,
                    body={"requests": [{"addSheet": {"properties": {"title": tab}}}]}).execute()
            except Exception:
                pass
            svc.spreadsheets().values().append(spreadsheetId=LOGIN_LOG_SHEET_ID,
                range="%s!A1" % tab, valueInputOption="RAW", body={"values": [_AAR_HEADER]}).execute()
        svc.spreadsheets().values().append(spreadsheetId=LOGIN_LOG_SHEET_ID, range="%s!A1" % tab,
            valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": [row]}).execute()
        return True
    except Exception as e:
        log.warning("agent access request log failed: %s", e)
        return False

def _agent_access_requests_raw(limit=2000):
    """Read every row from 'Agent Access Requests', newest first. Fails to []."""
    if not LOGIN_LOG_SHEET_ID:
        return []
    try:
        svc = _va_sheets_service()
        if not svc:
            return []
        rows = svc.spreadsheets().values().get(
            spreadsheetId=LOGIN_LOG_SHEET_ID, range="%s!A:G" % _AAR_TAB).execute().get("values", [])
        rows = rows[1:] if len(rows) > 1 else []
        AH = {n: i for i, n in enumerate(_AAR_HEADER)}
        def ac(r, n, d=""):
            i = AH.get(n, -1); return r[i] if 0 <= i < len(r) else d
        out = [{"ts": ac(r, "Timestamp (IST)"), "email": ac(r, "Email"), "name": ac(r, "Name"),
                "slug": ac(r, "Agent Slug"), "agent_name": ac(r, "Agent Name"),
                "message": ac(r, "Message")} for r in rows]
        out.reverse()
        return out[:limit]
    except Exception as e:
        log.warning("agent access requests read failed: %s", e)
        return []

def _agent_access_requested_slugs(email: str) -> set:
    """Slugs `email` has already requested access to — used both to dedupe
    repeat submissions and to render the 'Request sent' state on reload."""
    if not email:
        return set()
    e = email.lower()
    return {r["slug"] for r in _agent_access_requests_raw() if (r["email"] or "").lower() == e and r["slug"]}

def _slack_mrkdwn_escape(s: str) -> str:
    """Escape &, <, > for Slack mrkdwn text — untrusted input (a user's typed
    reason) could otherwise be misread as Slack link syntax."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def _agent_access_request_slack_blocks(user: dict, agent: dict, message: str = "") -> tuple:
    """Build the Block Kit payload for a 'New agent access request' Slack post.
    Returns (fallback_text, blocks) — fallback_text is what notification
    previews / screen readers show; blocks is the rich structured layout."""
    name = user.get("name", "") or "Unknown"
    email = user.get("email", "")
    agent_name = agent.get("name", "")
    fallback = f"New agent access request: {name} <{email}> wants access to {agent_name}"
    requester = _slack_mrkdwn_escape(name)
    if email:
        requester += f"\n<mailto:{email}|{email}>"
    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": "🙋 New agent access request", "emoji": True}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Requested by*\n{requester}"},
            {"type": "mrkdwn", "text": f"*Agent*\n{_slack_mrkdwn_escape(agent_name)}"},
        ]},
    ]
    if message:
        quoted = "> " + _slack_mrkdwn_escape(message).replace("\n", "\n> ")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Reason given*\n{quoted}"}})
    else:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "_No reason given_"}]})
    return fallback, blocks

def _agent_access_request_to_slack(user: dict, agent: dict, message: str = "") -> bool:
    """Post a new agent access request to the same #intelligence-platform-request-access
    channel used for the platform-wide 'Request access' form, as a structured
    Block Kit message (requester, agent, reason, timestamp, admin link)."""
    fallback, blocks = _agent_access_request_slack_blocks(user, agent, message)
    if SLACK_BOT_TOKEN:
        try:
            r = requests.post("https://slack.com/api/chat.postMessage",
                              headers={"Authorization": "Bearer " + SLACK_BOT_TOKEN},
                              json={"channel": SLACK_CHANNEL_ID, "text": fallback, "blocks": blocks,
                                    "unfurl_links": False, "unfurl_media": False}, timeout=8)
            if r.ok and r.json().get("ok"):
                return True
            log.warning("Slack chat.postMessage (agent access) failed: %s", r.text[:200])
        except Exception as e:
            log.warning("Slack chat.postMessage (agent access) error: %s", e)
    if SLACK_WEBHOOK_URL and SLACK_WEBHOOK_URL != "YOUR_SLACK_WEBHOOK_URL":
        try:
            requests.post(SLACK_WEBHOOK_URL, json={"text": fallback, "blocks": blocks}, timeout=8)
            return True
        except Exception as e:
            log.warning("Agent access request Slack webhook post failed: %s", e)
    return False

# ── Agent run history (full outputs, Postgres) ────────────────────────────────
# The three live /app agents are embedded, cross-origin tools (see _SERP_BASE) —
# this app never sees their output unless the tool itself hands it over. Each
# tool's completion handler now posts { source:'p2-seo-tool', type:
# 'agent-run-finished', tool, output } to the parent window (see seo-apps'
# agentRunSignal.js + each page's SSE 'done'/'result' handler); app_embed.html
# relays that to POST /app/<slug>/use/finish-run, which lands here. This is
# deliberately separate from _log_agent_run/"Agent Runs" (Sheets) above, which
# still owns the run-cap count -- that logic is untouched. A full run's output
# (especially Content Enhancer's rewritten article) can exceed a single Sheets
# cell's ~50k-char limit, so it needs a real database instead.
def _pg_conn():
    """One-off Postgres connection. None if DATABASE_URL isn't configured."""
    if not DATABASE_URL:
        return None
    try:
        import psycopg2
        return psycopg2.connect(DATABASE_URL, connect_timeout=8)
    except Exception as e:
        log.warning("Postgres connection failed: %s", e)
        return None

_RUN_HISTORY_TABLE_READY = False

def _ensure_run_history_table(conn) -> None:
    """CREATE TABLE IF NOT EXISTS, once per process. Concurrent gunicorn workers
    racing this on cold start is safe -- Postgres serializes the DDL."""
    global _RUN_HISTORY_TABLE_READY
    if _RUN_HISTORY_TABLE_READY:
        return
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS agent_run_history (
                id SERIAL PRIMARY KEY,
                email TEXT NOT NULL,
                name TEXT,
                agent_slug TEXT NOT NULL,
                agent_name TEXT,
                title TEXT,
                output JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_agent_run_history_email
            ON agent_run_history (email, created_at DESC)
        """)
    conn.commit()
    _RUN_HISTORY_TABLE_READY = True

def _run_title(slug: str, output: dict) -> str:
    """A short human label for a run, derived from its output -- what shows in
    the History list before you open it."""
    output = output or {}
    if slug == "keyword-finder":
        return output.get("keyword") or "Keyword research"
    if slug == "content-brief-generator":
        return output.get("keyword") or "Content brief"
    if slug == "content-enhancer":
        meta = output.get("articleMeta") or {}
        return meta.get("title") or output.get("url") or "Content enhancement"
    return output.get("title") or ""

def _save_agent_run(user: dict, agent: dict, output: dict):
    """Persist one finished run's full output. Returns the new row's id, or
    None on any failure (missing DATABASE_URL, connection error, etc.) --
    callers should treat that as 'not saved' and fail silently, same as every
    other best-effort logging path in this app (Sheets, Slack)."""
    conn = _pg_conn()
    if not conn:
        return None
    try:
        from psycopg2.extras import Json
        _ensure_run_history_table(conn)
        title = _run_title(agent.get("slug", ""), output)
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_run_history (email, name, agent_slug, agent_name, title, output) "
                "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
                ((user or {}).get("email", "").lower(), (user or {}).get("name", ""),
                 agent.get("slug", ""), agent.get("name", ""), title, Json(output or {})),
            )
            new_id = cur.fetchone()[0]
        conn.commit()
        return new_id
    except Exception as e:
        log.warning("save agent run failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _list_agent_runs(email: str, limit: int = 200) -> list:
    """This user's saved runs, newest first. [] on any failure or if Postgres
    isn't configured -- History just renders empty rather than erroring."""
    conn = _pg_conn()
    if not conn or not email:
        return []
    try:
        _ensure_run_history_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, agent_slug, agent_name, title, created_at FROM agent_run_history "
                "WHERE email = %s ORDER BY created_at DESC LIMIT %s",
                (email.lower(), limit),
            )
            rows = cur.fetchall()
        return [{"id": r[0], "slug": r[1], "agent_name": r[2], "title": r[3], "created_at": r[4].isoformat()}
                for r in rows]
    except Exception as e:
        log.warning("list agent runs failed: %s", e)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _list_agent_run_titles(limit=5000) -> list:
    """Every saved run's (email, agent_slug, title, created_at), oldest first --
    used to enrich the Sheets-backed 'Agent Runs' log (which only knows WHO ran
    WHICH AGENT) with WHAT THEY ACTUALLY RAN. Callers pair each Sheets run row
    with the next not-yet-claimed row here for the same (email, agent_slug), in
    chronological order -- both logs are append-ordered, so this lines them up
    without a timestamp-matching heuristic. Best-effort: a run that never
    finished (tool errored, tab closed before the postMessage handoff) has no
    title here, so that Sheets row's detail is simply left blank."""
    conn = _pg_conn()
    if not conn:
        return []
    try:
        _ensure_run_history_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT email, agent_slug, title, created_at FROM agent_run_history "
                "ORDER BY created_at ASC LIMIT %s", (limit,))
            rows = cur.fetchall()
        return [{"email": r[0], "slug": r[1], "title": r[2]} for r in rows]
    except Exception as e:
        log.warning("list agent run titles failed: %s", e)
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass

# ─────────────────────────────────────────────────────────────────────────────
# PERSON ENRICHMENT (Apollo) — powers the External Usage profile modal
# ─────────────────────────────────────────────────────────────────────────────
# The Sheets logs only know an email, a display name and a device string. To
# answer "who actually is this person?" we resolve the email against Apollo
# (people/bulk_match, 10 per call) and cache the normalized profile in Postgres
# so a given email costs at most one credit per TTL window, not one per page
# load. Fails soft in every direction: no APOLLO_API_KEY, no DATABASE_URL, or an
# Apollo outage all degrade to "not enriched" rather than breaking the page.
#
# Unmatched emails are cached TOO (with a shorter TTL) -- most personal gmail
# addresses will never match, and without a negative cache every modal open
# would re-spend a lookup on them.

_PE_POS_TTL_DAYS = 90    # a matched profile: titles/companies move slowly
_PE_NEG_TTL_DAYS = 21    # an unmatched email: retry occasionally, not hourly
# Version stamped onto every cached MISS. Bump this whenever a bug could have
# produced false negatives: an older stamp is treated as untrusted and re-looked
# up once, which self-heals the cache instead of stranding people for the
# negative TTL. v1 was never written (misses carried only `checked`); v2 adds the
# response-shape guard, so anything below 2 predates a real false-negative bug.
_PE_MISS_VERSION = 2
# Version stamped onto every cached MATCH. Bump this when the normalized shape
# gains a field, so profiles cached under the old shape are re-normalized instead
# of rendering a half-empty modal for up to the 90-day positive TTL. v2 adds the
# `emails` and `phones` blocks read out of Apollo's nested `contact` object.
_PE_SHAPE_VERSION = 2
_PE_MEM: dict = {}       # email -> (expiry_epoch, profile) in-process hot cache
_PE_MEM_TTL = 900
_PE_TABLE_READY = False


def _ensure_person_enrich_table(conn) -> None:
    """CREATE TABLE IF NOT EXISTS, once per process (same pattern/rationale as
    _ensure_run_history_table -- concurrent workers racing the DDL is safe)."""
    global _PE_TABLE_READY
    if _PE_TABLE_READY:
        return
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS person_enrichment (
                email TEXT PRIMARY KEY,
                matched BOOLEAN NOT NULL DEFAULT false,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
    conn.commit()
    _PE_TABLE_READY = True


def _pe_cache_read(emails: list) -> dict:
    """email -> profile for every still-fresh cached row. {} if Postgres is not
    configured (the in-process cache above is then the only cache)."""
    out: dict = {}
    if not emails:
        return out
    conn = _pg_conn()
    if not conn:
        return out
    try:
        _ensure_person_enrich_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT email, matched, payload, updated_at FROM person_enrichment "
                "WHERE email = ANY(%s)", (list(emails),))
            rows = cur.fetchall()
        now = datetime.now(timezone.utc)
        for email, matched, payload, updated_at in rows:
            payload = payload or {"matched": False, "email": email}
            # Only trust a miss stamped by the current logic. Rows written before
            # the answered/pending split (APOLLO_API_KEY unset) or before the
            # response-shape guard (HTTP 200 with an error body counted as "no
            # match for everyone") are false negatives, so they get re-looked-up
            # once rather than stranding those people for the negative TTL.
            if not matched and int(payload.get("v") or 0) < _PE_MISS_VERSION:
                continue
            # Same idea for matches, but about shape rather than truth: a profile
            # normalized by an older build is missing whatever fields the modal
            # has since learned to render, so re-resolve it once.
            if matched and int(payload.get("sv") or 0) < _PE_SHAPE_VERSION:
                continue
            ttl = _PE_POS_TTL_DAYS if matched else _PE_NEG_TTL_DAYS
            if updated_at and (now - updated_at).days < ttl:
                out[email] = payload
        return out
    except Exception as e:
        log.warning("person enrich cache read failed: %s", e)
        return out
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _pe_cache_write(profiles: dict) -> None:
    """Upsert freshly-resolved profiles. Best-effort: a write failure just means
    the next request re-resolves."""
    if not profiles:
        return
    conn = _pg_conn()
    if not conn:
        return
    try:
        from psycopg2.extras import Json
        _ensure_person_enrich_table(conn)
        with conn.cursor() as cur:
            for email, prof in profiles.items():
                cur.execute(
                    "INSERT INTO person_enrichment (email, matched, payload, updated_at) "
                    "VALUES (%s, %s, %s, now()) "
                    "ON CONFLICT (email) DO UPDATE SET matched = EXCLUDED.matched, "
                    "payload = EXCLUDED.payload, updated_at = now()",
                    (email, bool(prof.get("matched")), Json(prof)))
        conn.commit()
    except Exception as e:
        log.warning("person enrich cache write failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _pe_pretty(s: str) -> str:
    """Apollo ships enum-ish strings ('master_operations', 'c_suite'). Make them
    readable without losing the original meaning."""
    s = str(s or "")
    if s.startswith("master_"):
        s = s[7:]
    return s.replace("_", " ").strip()


def _pe_names(seq, limit: int) -> list:
    """Flatten Apollo's mixed list-of-strings / list-of-{name} shapes, dedup,
    preserve order, cap length."""
    out: list = []
    for x in (seq or []):
        v = (x.get("name") if isinstance(x, dict) else x)
        v = str(v or "").strip()
        if v and v not in out:
            out.append(v)
        if len(out) >= limit:
            break
    return out


_PE_PHONE_LABELS = {
    "mobile": "Mobile", "work_direct": "Direct dial", "work_hq": "Company HQ",
    # Apollo's "other" carries no real distinction, so do not surface it as a
    # meaningful label: an unqualified number is just a phone number.
    "home": "Home", "other": "Phone", "":  "Phone",
}


def _pe_phones(p: dict, contact: dict, org: dict) -> list:
    """Deduped phone list for one person, most useful number first.

    Apollo only returns numbers inline for people already in the team's Apollo
    or CRM (the nested `contact` object). For anyone else the synchronous
    bulk_match response carries no number at all: revealing one is a separate
    asynchronous, webhook-delivered, separately-metered call. So an empty list
    here means "Apollo has none on file for us", not "we forgot to ask"."""
    out: list = []
    seen: set = set()

    def add(number, kind, source, owner):
        n = str(number or "").strip()
        if not n:
            return
        key = re.sub(r"\D", "", n)[-10:] or n
        if not key or key in seen:
            return
        seen.add(key)
        kind = str(kind or "").lower()
        # A main switchboard is a company number wherever it was stored. Apollo
        # keeps HQ lines on the contact record too, and calling one of those a
        # personal number would be actively misleading.
        if kind == "work_hq":
            owner = "company"
        out.append({"number": n, "label": _PE_PHONE_LABELS.get(kind, "Phone"),
                    "source": str(source or "")[:40], "owner": owner})

    for src in (contact, p):
        for ph in (src.get("phone_numbers") or []):
            if isinstance(ph, dict):
                add(ph.get("sanitized_number") or ph.get("raw_number"),
                    ph.get("type"), ph.get("source_name"), "person")
    add(contact.get("sanitized_phone") or p.get("sanitized_phone"), "", "", "person")
    # One company switchboard at most, and only if the contact record did not
    # already carry an HQ line: two rows both labelled "company phone" is noise,
    # and mistaking either for a direct dial is worse.
    if not any(ph["owner"] == "company" for ph in out):
        add(org.get("sanitized_phone") or org.get("phone")
            or ((org.get("primary_phone") or {}).get("number")), "work_hq", "", "company")
    out.sort(key=lambda ph: 0 if ph["owner"] == "person" else 1)
    return out[:6]


def _pe_emails(p: dict, contact: dict, email: str) -> list:
    """Deduped email list, the address this person signed up with first, each
    tagged with Apollo's deliverability verdict where it has one."""
    out: list = []
    seen: set = set()

    def add(addr, status):
        a = str(addr or "").strip().lower()
        if not a or "@" not in a or a in seen:
            return
        seen.add(a)
        st = str(status or "").strip().lower()
        out.append({"email": a,
                    "verified": st in ("verified", "valid"),
                    "status": st.replace("_", " ")[:24],
                    "primary": a == (email or "").strip().lower()})

    add(email, contact.get("email_status") or p.get("email_status"))
    for ce in (contact.get("contact_emails") or []):
        if isinstance(ce, dict):
            add(ce.get("email"), ce.get("email_status"))
    add(contact.get("email"), contact.get("email_status"))
    add(p.get("email"), p.get("email_status"))
    for pe in (p.get("personal_emails") or []):
        add(pe, "")
    out.sort(key=lambda e: 0 if e["primary"] else 1)
    return out[:6]


def _apollo_person_normalize(p: dict, email: str) -> dict:
    """One Apollo person record -> the trimmed shape the modal renders. Never
    ships the raw record: org `keywords` alone can be 100+ entries."""
    p = p or {}
    org = p.get("organization") or {}
    acct = p.get("account") or {}
    # The nested `contact` object appears only when this person is already a
    # contact in the team's Apollo or synced CRM, and it is the ONLY place the
    # synchronous API hands back phone numbers and verified-email status.
    contact = p.get("contact") or {}

    history = []
    for h in (p.get("employment_history") or [])[:14]:
        history.append({
            "title": h.get("title") or "",
            "org": h.get("organization_name") or "",
            "start": (h.get("start_date") or "")[:7],
            "end": "" if h.get("current") else (h.get("end_date") or "")[:7],
            "current": bool(h.get("current")),
        })
    # Apollo returns current-first already, but an explicit sort makes the
    # rendered career timeline deterministic even if that ever changes:
    # current roles first, then most-recent start date down.
    history.sort(key=lambda h: (0 if h["current"] else 1, -_pe_year(h["start"])))

    hq = ", ".join([x for x in [org.get("city"), org.get("state"), org.get("country")] if x])
    loc = p.get("formatted_address") or ", ".join(
        [x for x in [p.get("city"), p.get("state"), p.get("country")] if x])

    name = p.get("name") or " ".join(
        [x for x in [p.get("first_name"), p.get("last_name")] if x]).strip()

    company = {}
    if org:
        company = {
            "name": org.get("name") or "",
            "domain": org.get("primary_domain") or org.get("domain") or "",
            "website": org.get("website_url") or "",
            "linkedin": org.get("linkedin_url") or "",
            "facebook": org.get("facebook_url") or "",
            "twitter": org.get("twitter_url") or "",
            "logo": org.get("logo_url") or "",
            "industry": org.get("industry") or "",
            "industries": _pe_names(org.get("industries"), 4),
            "employees": org.get("estimated_num_employees") or 0,
            "revenue": org.get("organization_revenue_printed") or "",
            "founded": org.get("founded_year") or "",
            "phone": (org.get("phone") or ((org.get("primary_phone") or {}).get("number"))
                      or org.get("sanitized_phone") or ""),
            "hq": hq,
            "address": org.get("raw_address") or "",
            "description": (org.get("short_description") or "")[:420],
            "keywords": _pe_names(org.get("keywords"), 14),
            "technologies": _pe_names(org.get("current_technologies"), 12),
            "growth6": org.get("organization_headcount_six_month_growth"),
            "growth12": org.get("organization_headcount_twelve_month_growth"),
            "growth24": org.get("organization_headcount_twenty_four_month_growth"),
        }

    crm = {}
    if acct or contact:
        # Prefer the person-level record: "open in CRM" should land on the
        # contact, not on their employer's account page.
        crm = {
            "in_crm": True,
            "name": contact.get("name") or acct.get("name") or "",
            "url": (contact.get("hubspot_record_url") or contact.get("crm_record_url")
                    or acct.get("hubspot_record_url") or acct.get("crm_record_url") or ""),
            "source": (contact.get("source_display_name") or acct.get("source_display_name")
                       or contact.get("source") or acct.get("source") or ""),
            "created": (contact.get("created_at") or acct.get("created_at") or "")[:10],
            "account_url": acct.get("hubspot_record_url") or acct.get("crm_record_url") or "",
            "is_contact": bool(contact),
        }

    return {
        "matched": True,
        "sv": _PE_SHAPE_VERSION,
        "email": email,
        "name": name,
        "title": p.get("title") or "",
        "headline": p.get("headline") or "",
        "photo": p.get("photo_url") or "",
        "seniority": _pe_pretty(p.get("seniority")),
        "departments": [_pe_pretty(d) for d in (p.get("departments") or [])][:6],
        "functions": [_pe_pretty(f) for f in (p.get("functions") or [])][:6],
        "city": p.get("city") or "",
        "state": p.get("state") or "",
        "country": p.get("country") or "",
        "location": loc,
        "time_zone": p.get("time_zone") or "",
        "linkedin": p.get("linkedin_url") or "",
        "twitter": p.get("twitter_url") or "",
        "facebook": p.get("facebook_url") or "",
        "apollo_id": p.get("id") or "",
        "apollo_email": p.get("email") or "",
        "emails": _pe_emails(p, contact, email),
        "phones": _pe_phones(p, contact, org),
        "history": history,
        "company": company,
        "crm": crm,
    }


def _pe_year(s: str) -> int:
    try:
        return int(str(s or "")[:4])
    except (TypeError, ValueError):
        return 0


def _apollo_bulk_match(emails: list):
    """(profiles, answered) via Apollo people/bulk_match, 10 per call, chunks
    issued concurrently.

    `profiles` maps email -> normalized profile for each real match. `answered`
    is the set of emails Apollo actually returned a verdict on. The distinction
    matters: "Apollo says there is no such person" is a durable fact worth
    caching, while "we never reached Apollo" (no API key, network error, 5xx) is
    not -- caching the latter as a miss would freeze everyone as unenriched for
    the whole negative-TTL window, which is exactly what happened on the first
    deploy when APOLLO_API_KEY was unset."""
    key = os.environ.get("APOLLO_API_KEY", "")
    if not (key and emails):
        return {}, set()
    try:
        from tracker.apollo_client import _post as _apollo_post
    except Exception as e:
        log.warning("apollo client unavailable: %s", e)
        return {}, set()

    chunks = [emails[i:i + 10] for i in range(0, len(emails), 10)]

    def _one(chunk: list):
        out: dict = {}
        try:
            data = _apollo_post("people/bulk_match",
                                {"details": [{"email": e} for e in chunk]}, key) or {}
        except Exception as e:
            log.warning("apollo bulk_match failed (%d emails): %s", len(chunk), e)
            return out, set()          # nothing in this chunk got an answer
        # Apollo can return HTTP 200 with a body that is not a bulk_match result
        # at all (an error object, an auth/scope complaint, an HTML error page
        # parsed as JSON). Treating that as "Apollo answered, nobody matched"
        # would cache a permanent false negative for everyone in the chunk, so
        # the shape is checked explicitly before we trust it.
        matches = data.get("matches")
        if not isinstance(matches, list):
            log.warning("apollo bulk_match: unexpected response shape for %d emails, "
                        "keys=%s error=%s", len(chunk), sorted(list(data.keys()))[:8],
                        str(data.get("error") or data.get("message") or "")[:200])
            return out, set()
        # `matches` is positionally aligned with the `details` we sent, with a
        # null for every email Apollo could not resolve.
        for i, email in enumerate(chunk):
            m = matches[i] if i < len(matches) else None
            if m:
                try:
                    out[email] = _apollo_person_normalize(m, email)
                except Exception as e:
                    log.warning("apollo normalize failed for one record: %s", e)
        return out, set(chunk)

    results: dict = {}
    answered: set = set()
    if len(chunks) == 1:
        r, a = _one(chunks[0])
        results.update(r); answered |= a
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(4, len(chunks))) as ex:
            for r, a in ex.map(_one, chunks):
                results.update(r); answered |= a
    return results, answered


def _warm_person_enrichment(email: str) -> None:
    """Enrich one member's profile in a detached background thread.

    Called on every non-Position2 sign-in, which is what makes a brand-new
    person show up already enriched instead of waiting for an admin to open the
    dashboard. Safe to call on every sign-in, not just new ones: a known email
    is a pure cache hit and costs no Apollo credit. Never blocks or breaks the
    sign-in -- a failure here is logged and dropped."""
    email = (email or "").strip().lower()
    if not (email and "@" in email):
        return
    if not os.environ.get("APOLLO_API_KEY", ""):
        return                      # nothing to warm without a key
    def _run():
        try:
            _enrich_people([email])
        except Exception as e:
            log.warning("background enrich failed for %s: %s", email, e)
    try:
        import threading
        threading.Thread(target=_run, name="enrich-member", daemon=True).start()
    except Exception as e:
        log.warning("could not start enrich thread: %s", e)


def _enrich_people(emails: list, force: bool = False) -> dict:
    """email -> profile for every requested email. A profile is either a real
    Apollo match (`matched: true`) or an honest {matched: false} placeholder --
    we never invent a person. Cached in-process, then in Postgres, then resolved
    live against Apollo for whatever is left."""
    wanted, seen = [], set()
    for e in (emails or []):
        e = str(e or "").strip().lower()
        if e and "@" in e and e not in seen:
            seen.add(e)
            wanted.append(e)
    if not wanted:
        return {}

    out: dict = {}
    todo = list(wanted)

    if not force:
        now = time.time()
        still: list = []
        for e in todo:
            hit = _PE_MEM.get(e)
            if hit and hit[0] > now:
                out[e] = hit[1]
            else:
                still.append(e)
        todo = still

        if todo:
            cached = _pe_cache_read(todo)
            for e, prof in cached.items():
                out[e] = prof
            todo = [e for e in todo if e not in cached]

    if todo:
        fresh, answered = _apollo_bulk_match(todo)
        writeback = {}
        for e in todo:
            prof = fresh.get(e)
            if prof:
                writeback[e] = prof
            elif e in answered:
                # Apollo genuinely has no record: cache it so an unmatchable
                # address is not re-looked-up on every page load.
                prof = {"matched": False, "email": e, "checked": True,
                        "v": _PE_MISS_VERSION}
                writeback[e] = prof
            else:
                # Never reached Apollo. Report it as pending and cache NOTHING,
                # so the next request retries instead of being stuck as a miss.
                prof = {"matched": False, "email": e, "pending": True}
            out[e] = prof
        _pe_cache_write(writeback)

    exp = time.time() + _PE_MEM_TTL
    for e, prof in out.items():
        if not prof.get("pending"):
            _PE_MEM[e] = (exp, prof)
    return out


def _get_agent_run(email: str, run_id: int):
    """One saved run's full output, scoped to `email` so a user can only ever
    open their own runs (the id is a public URL param, not a secret)."""
    conn = _pg_conn()
    if not conn or not email:
        return None
    try:
        _ensure_run_history_table(conn)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, agent_slug, agent_name, title, output, created_at FROM agent_run_history "
                "WHERE id = %s AND email = %s",
                (run_id, email.lower()),
            )
            row = cur.fetchone()
        if not row:
            return None
        return {"id": row[0], "slug": row[1], "agent_name": row[2], "title": row[3],
                "output": row[4], "created_at": row[5].isoformat()}
    except Exception as e:
        log.warning("get agent run failed: %s", e)
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _fmt_run_ts(iso_str: str) -> str:
    """A saved run's ISO timestamp (UTC, from Postgres) -> a friendly IST
    string for display, matching the rest of the app's timestamp style."""
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.astimezone(IST).strftime("%b %d, %Y · %I:%M %p IST")
    except Exception:
        return iso_str or ""

def _app_embed_url(agent):
    """Build the live SERP tool URL for an agent (same as the internal /p2/seo embed)."""
    seo_slug = agent.get("seo_slug")
    if not seo_slug:
        return ""
    tool = next((t for t in _seo_tools() if t.get("slug") == seo_slug), None)
    path = (tool or {}).get("path") or ("/" + seo_slug)
    ext = (tool or {}).get("url")
    if ext:
        return ext
    pt = os.environ.get("SERP_PLATFORM_TOKEN", "")
    # embed=1 tells the SERP app to render chrome-less (no sidebar / studio nav),
    # so public users only see the single agent they opened. Internal /p2/seo does
    # NOT pass this, so staff keep the full SEO Studio.
    qs = ([("pt", pt)] if pt else []) + [("embed", "1")]
    sep = "&" if "?" in path else "?"
    return f"{_SERP_BASE}{path}{sep}" + "&".join("%s=%s" % kv for kv in qs)

@app.context_processor
def _inject_app_agents():
    """Make the /app agent list available to every template (app_base.html's
    sidebar needs it regardless of which child page is rendering).

    Also expose google_client_id template-wide: the marketing pages carry a
    one-click Google "Sign up" pop-up, so every pre-login page (not just
    /login) needs the client id to render the Google button. The explicit
    kwarg on the /login route still takes precedence where it is passed.

    Also expose `is_admin` template-wide (single source of truth = ADMIN_EMAILS),
    so pages that conditionally show the admin analytics menu use one check that
    can never drift from the server-side admin_required gate."""
    _u = _get_user() or {}
    _is_admin = (_u.get("email", "").lower() in ADMIN_EMAILS)
    return {"app_agents": APP_AGENTS, "google_client_id": GOOGLE_CLIENT_ID,
            "is_admin": _is_admin}

@app.route("/app")
@login_required
def app_home():
    """Signed-in home for ALL Google users (Position2 and external alike)."""
    user = _get_user()
    email = (user or {}).get("email", "")
    run_counts = _agent_run_counts(email)
    requested = _agent_access_requested_slugs(email)
    return render_template("app.html", user=user, agents=APP_AGENTS,
                           run_counts=run_counts, run_cap=AGENT_RUN_CAP,
                           requested_agents=requested)

@app.route("/app/<slug>")
@login_required
def app_detail(slug):
    """Public agent detail page."""
    if slug in _LEGACY_AGENT_SLUGS:
        return redirect("/app/" + _LEGACY_AGENT_SLUGS[slug], code=301)
    agent = APP_AGENTS_BY_SLUG.get(slug)
    if not agent:
        return redirect("/app")
    user = _get_user()
    email = (user or {}).get("email", "")
    runs_used = _agent_run_counts(email).get(slug, 0)
    already_requested = slug in _agent_access_requested_slugs(email)
    return render_template("app_detail.html", user=user, agent=agent,
                           runs_used=runs_used, runs_cap=AGENT_RUN_CAP,
                           already_requested=already_requested)

@app.route("/app/<slug>/request-access", methods=["POST"])
@login_required
def app_request_access(slug):
    """Logs a request for access to an agent that isn't connected to a live tool
    yet, notifies Slack, and shows up in the 'Access Requests' admin dashboard.
    Idempotent per (email, agent) — a second click just confirms, it doesn't
    re-log or re-notify. Accepts an optional {"message": "..."} JSON body from
    the request-access modal so users can say why they need the agent."""
    agent = APP_AGENTS_BY_SLUG.get(slug)
    if not agent:
        return jsonify({"ok": False, "error": "unknown agent"}), 404
    if agent.get("seo_slug"):
        return jsonify({"ok": False, "error": "agent is already connected"}), 400
    user = _get_user()
    email = (user or {}).get("email", "")
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()[:600]
    if slug in _agent_access_requested_slugs(email):
        return jsonify({"ok": True, "already_requested": True})
    _log_agent_access_request(user, agent, message)
    _agent_access_request_to_slack(user, agent, message)
    return jsonify({"ok": True, "already_requested": False})

@app.route("/app/<slug>/use")
@login_required
def app_use(slug):
    """Embeds the live SERP tool (same one used internally). Opening this page
    does NOT count as a run by itself — the cap is checked against past runs
    so someone already at the cap still sees the blocked state, but a fresh
    run is only logged once app_use_log_run fires (see below), which happens
    client-side after real interaction with the embedded tool is detected."""
    if slug in _LEGACY_AGENT_SLUGS:
        return redirect("/app/" + _LEGACY_AGENT_SLUGS[slug] + "/use", code=301)
    agent = APP_AGENTS_BY_SLUG.get(slug)
    if not agent:
        return redirect("/app")
    user = _get_user()
    email = (user or {}).get("email", "")
    runs_used = _agent_run_counts(email).get(slug, 0)
    if runs_used >= AGENT_RUN_CAP:
        return render_template("app_embed.html", user=user, agent=agent, embed_url=None,
                               runs_used=runs_used, runs_cap=AGENT_RUN_CAP, limit_reached=True,
                               serp_origin=_SERP_BASE)
    embed_url = _app_embed_url(agent)
    if not embed_url:
        return redirect("/app/" + slug)
    return render_template("app_embed.html", user=user, agent=agent, embed_url=embed_url,
                           runs_used=runs_used, runs_cap=AGENT_RUN_CAP, limit_reached=False,
                           serp_origin=_SERP_BASE)

@app.route("/app/<slug>/use/log-run", methods=["POST"])
@login_required
def app_use_log_run(slug):
    """Logs one real run. Called client-side only after the embedded tool
    itself reports (via postMessage) that the user filled in the required
    fields and clicked its Run/Start/Generate CTA — NOT on page load, and
    NOT on merely clicking into the iframe — so opening an agent and looking
    around doesn't count against the cap. See app_embed.html's message
    listener and seo-apps' agentRunSignal.js."""
    agent = APP_AGENTS_BY_SLUG.get(slug)
    if not agent:
        return jsonify({"logged": False, "error": "unknown agent"}), 404
    user = _get_user()
    email = (user or {}).get("email", "")
    runs_used = _agent_run_counts(email).get(slug, 0)
    if runs_used >= AGENT_RUN_CAP:
        return jsonify({"logged": False, "runs_used": runs_used, "runs_cap": AGENT_RUN_CAP, "at_cap": True})
    _log_agent_run(user, agent)
    runs_used += 1
    return jsonify({"logged": True, "runs_used": runs_used, "runs_cap": AGENT_RUN_CAP,
                    "at_cap": runs_used >= AGENT_RUN_CAP})

@app.route("/app/<slug>/use/finish-run", methods=["POST"])
@login_required
def app_use_finish_run(slug):
    """Saves one run's full output. Called client-side after app_embed.html's
    message listener receives an 'agent-run-finished' postMessage from the
    embedded tool (see seo-apps' agentRunSignal.js / each page's SSE 'done'
    handler) -- the tool hands over its actual result here, not just the fact
    that it ran. Separate from log-run above, which still owns the run-cap
    count against Sheets; this only powers the History page."""
    agent = APP_AGENTS_BY_SLUG.get(slug)
    if not agent:
        return jsonify({"saved": False, "error": "unknown agent"}), 404
    data = request.get_json(silent=True) or {}
    output = data.get("output")
    if not isinstance(output, dict):
        return jsonify({"saved": False, "error": "missing output"}), 400
    user = _get_user()
    run_id = _save_agent_run(user, agent, output)
    return jsonify({"saved": run_id is not None, "id": run_id})

@app.route("/app/history")
@login_required
def app_history():
    """Every saved run (with its full output) for the signed-in user, across
    the three connected agents, newest first -- backed by Postgres so it
    survives across devices and browsers, unlike the old localStorage-only
    'recently opened' list this replaced."""
    user = _get_user()
    runs = _list_agent_runs((user or {}).get("email", ""))
    for r in runs:
        meta = APP_AGENTS_BY_SLUG.get(r["slug"], {})
        r["ac"] = meta.get("ac", "#8b5cf6")
        r["ac2"] = meta.get("ac2", "#22d3ee")
        r["icon"] = meta.get("icon", "")
        r["display_ts"] = _fmt_run_ts(r["created_at"])
    return render_template("app_history.html", user=user, runs=runs)

@app.route("/app/history/<int:run_id>")
@login_required
def app_history_detail(run_id):
    """One saved run's full output, formatted per agent type."""
    user = _get_user()
    run = _get_agent_run((user or {}).get("email", ""), run_id)
    if not run:
        return redirect("/app/history")
    run["display_ts"] = _fmt_run_ts(run["created_at"])
    agent = APP_AGENTS_BY_SLUG.get(run["slug"], {})
    return render_template("app_history_detail.html", user=user, run=run, agent=agent)

@app.route("/app/settings")
@login_required
def app_settings():
    """Account settings for the public /app area. No API-key management here —
    unlike the reference product, our agents call out to Position2's own SERP
    tooling, not a user-supplied OpenAI key."""
    return render_template("app_settings.html", user=_get_user())


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
        return redirect("/app")
    return render_template("agents.html", page="login", agents=AGENTS, agent=None,
                           related=[], google_client_id=GOOGLE_CLIENT_ID,
                           error=request.args.get("error", ""),
                           logged_out=bool(request.args.get("logged_out")),
                           returning=bool(request.cookies.get("p2_seen")))

@app.route("/login-preview")
def login_preview():
    return render_template("login_preview.html", google_client_id=GOOGLE_CLIENT_ID,
                           error=request.args.get("error", ""))

@app.route("/logout")
def logout():
    # Wipe the server-side session and force the browser to drop EVERY cookie +
    # client storage for this origin, so a returning visitor stays signed out
    # until they explicitly click "Sign in with Google" again.
    #
    # Why the belt-and-suspenders: a plain Set-Cookie deletion only removes a
    # cookie whose attributes (Domain/Path/SameSite/Secure) exactly match what
    # delete_cookie emits. If the session cookie was ever issued with different
    # attributes (a proxy, an older config, a parent-domain cookie), the browser
    # keeps sending it and the user looks "auto-logged-in" on their next visit.
    # Clear-Site-Data: "cookies" purges them regardless of attributes; "storage"
    # clears any lingering client-side auth remnants (localStorage/sessionStorage).
    # NB: don't touch session.permanent after clear() — assigning it re-adds a
    # key, making the session non-empty and causing Flask to re-issue the cookie.
    # An empty session lets Flask delete the cookie outright.
    session.clear()
    resp = make_response(redirect(url_for("login_page", logged_out=1)))
    cookie_name = app.config.get("SESSION_COOKIE_NAME", "session")
    resp.delete_cookie(cookie_name, path="/")
    resp.delete_cookie("p2_seen", path="/")
    resp.headers["Clear-Site-Data"] = '"cookies", "storage"'
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


# ── Client workspaces (private, co-branded portals) ──────────────────────────────
# A per-client front door at /<client-slug> (e.g. /northstaranesthesia). Each client
# gets a curated subset of agents, gated so only that client's own email domain(s)
# plus @position2.com staff can sign in. Agents reuse the APP_AGENTS registry: the
# three with a "seo_slug" are live (their "Use Agent" embeds the real SERP tool, same
# as /app/<slug>/use); the rest render an "in setup" dashboard shell until wired.
#
# Routes are registered explicitly per known client slug (see the loop below), so an
# unknown top-level path never resolves here -- there is no catch-all "/<anything>".
CLIENTS = {
    "northstaranesthesia": {
        "slug":     "northstaranesthesia",
        "name":     "NorthStar Anesthesia",
        "short":    "NorthStar",
        "website":  "https://northstaranesthesia.com/",
        # Client's own logo (their white wordmark, for the dark dashboard). Served
        # locally from /static rather than hotlinked, and rendered via <img> so the
        # SVG can't execute script. Sourced from northstaranesthesia.com.
        "logo":     "/static/clients/northstaranesthesia/logo-white.svg",
        # Email domains allowed in addition to @position2.com (always allowed).
        "domains":  ["northstaranesthesia.com"],
        "accent":   "#5b9dff",
        "accent2":  "#8b5cf6",
        "tagline":  "Your agents, all in one place.",
        "blurb":    "",
        # Ordered exactly as the portal should list them (slugs index APP_AGENTS_BY_SLUG).
        "agents":   ["signal-tracker", "linkedin-intelligence", "linkedin-strategy-researcher",
                     "keyword-finder", "content-brief-generator",
                     "content-enhancer"],
        # Per-agent live dashboards wired to this client's data. An agent listed
        # here renders its co-branded dashboard (internal-ops chrome hidden) inside
        # the portal instead of the generic "in setup" shell, and shows as Live.
        "dashboards": {
            "signal-tracker": Path(__file__).parent / "reports" / "dashboard_northstar_client.html",
        },
        # LinkedIn Intelligence is a *live* co-branded dashboard (same UI as /p2),
        # rendered from this client's own engagement sheet. Presence of this key
        # makes the linkedin-intelligence agent render its live dashboard in-portal.
        "linkedin_sheet": "13V-W-yG5O-OoLJHjxsPKLjrpRyRdk647GgkIGw823oE",
        # Agents whose "Use" surface embeds an external, Position2-hosted tool
        # (its own host masked behind this portal's path). The tool loads inside the
        # portal's embed shell via an iframe, so the address bar stays on
        # intelligence.position2.com/<slug>/agents/<agent>/use — the external host is
        # never shown. Treated like a live dashboard (shows Live, not run-metered).
        "external_tools": {
            "linkedin-strategy-researcher": "https://watchtower-by-position2.vercel.app/linkedin.html",
        },
    },
}

@app.template_filter("nodash")
def _nodash(s):
    """Strip em/en dashes from copy rendered on the client portals. The shared
    APP_AGENTS product blurbs use em dashes, but client-facing Position2 copy
    never does (house style), so we normalise at render time without touching the
    shared source strings that the internal /app pages also use."""
    if not isinstance(s, str):
        return s
    return (s.replace(" — ", ", ").replace("—", ", ")
             .replace(" – ", ", ").replace("–", "-"))

def _client_dashboard_path(client, agent_slug):
    """Path to this client's wired dashboard for an agent, or None. Returns None if
    the file hasn't been generated yet so the agent falls back to the setup shell."""
    p = (client.get("dashboards") or {}).get(agent_slug)
    if not p:
        return None
    p = Path(p)
    return p if p.exists() else None

def _client_live_dashboard(client, agent_slug):
    """True if this agent renders a *live* (Flask-rendered) co-branded dashboard for
    the client, rather than a pre-built static HTML file. Currently: LinkedIn
    Intelligence, when the client has its own engagement sheet configured. Same UI
    as the internal /p2 dashboard, just pointed at the client's sheet."""
    return bool(client and agent_slug == "linkedin-intelligence"
                and client.get("linkedin_sheet"))

def _client_external_tool(client, agent_slug):
    """External, Position2-hosted tool URL for an agent in this client's portal, or
    None. An external-tool agent embeds a third-party-hosted tool (its own host masked
    behind the portal path) in the embed shell, rather than a SERP tool or a
    Flask-rendered dashboard. Shown as Live and IS run-metered, capped at
    AGENT_RUN_CAP per user: the tool reports a real run via the same postMessage
    contract as a SERP tool (source 'p2-agent'), handled in client_embed.html."""
    return (client.get("external_tools") or {}).get(agent_slug) if client else None

def _client_agent_view(slug, client=None):
    """APP_AGENTS entry for a slug, enriched with `connected` (has a live surface),
    `is_dashboard` (backed by a co-branded dashboard — static file or live route — for
    this client, so it renders a dashboard in the embed shell and is NOT run-metered)
    and `is_external` (the embed is a Position2-hosted external tool: it renders in the
    embed shell but IS run-metered like a SERP tool, capped at AGENT_RUN_CAP per user).
    Returns None for unknown slugs."""
    a = APP_AGENTS_BY_SLUG.get(slug)
    if not a:
        return None
    has_dash = bool(client and (_client_dashboard_path(client, slug)
                                or _client_live_dashboard(client, slug)))
    is_ext = bool(_client_external_tool(client, slug))
    return dict(a, connected=bool(a.get("seo_slug")) or has_dash or is_ext,
                is_dashboard=has_dash, is_external=is_ext)

def _client_agents(client):
    return [v for v in (_client_agent_view(s, client) for s in client.get("agents", [])) if v]

def _client_allowed(client, email):
    email = (email or "").lower()
    if email.endswith("@position2.com"):
        return True
    return any(email.endswith("@" + d.lower()) for d in client.get("domains", []))

def _client_gate(client):
    """Return a response to short-circuit with (login redirect or 403 denial), or
    None when the current user is allowed into this client's workspace."""
    user = _get_user()
    if not user:
        return _login_redirect()
    if not _client_allowed(client, user.get("email", "")):
        return render_template("client_denied.html", client=client, user=user), 403
    return None

def _client_home(client_slug):
    client = CLIENTS.get(client_slug)
    if not client:
        abort(404)
    gate = _client_gate(client)
    if gate is not None:
        return gate
    agents = _client_agents(client)
    user = _get_user()
    run_counts = _agent_run_counts((user or {}).get("email", ""))
    return render_template("client_portal.html", client=client,
                           agents=agents, nav_agents=agents, user=user,
                           run_counts=run_counts, run_cap=AGENT_RUN_CAP)

def _client_agent_detail(client_slug, agent_slug):
    client = CLIENTS.get(client_slug)
    if not client:
        abort(404)
    gate = _client_gate(client)
    if gate is not None:
        return gate
    if agent_slug not in client.get("agents", []):
        return redirect("/" + client_slug)
    agent = _client_agent_view(agent_slug, client)
    if not agent:
        return redirect("/" + client_slug)
    agents = _client_agents(client)
    related = [v for v in agents if v["slug"] != agent_slug][:3]
    user = _get_user()
    runs_used = _agent_run_counts((user or {}).get("email", "")).get(agent_slug, 0)
    return render_template("client_agent.html", client=client, agent=agent,
                           related=related, nav_agents=agents, user=user,
                           runs_used=runs_used, run_cap=AGENT_RUN_CAP)

def _client_agent_use(client_slug, agent_slug):
    client = CLIENTS.get(client_slug)
    if not client:
        abort(404)
    gate = _client_gate(client)
    if gate is not None:
        return gate
    if agent_slug not in client.get("agents", []):
        return redirect("/" + client_slug)
    agent = _client_agent_view(agent_slug, client)
    if not agent:
        return redirect("/" + client_slug)
    user = _get_user()
    # Dashboard-backed agent: serve the co-branded dashboard in the portal shell.
    # These are not run-metered (a dashboard has no "runs"), so no cap logic applies.
    if agent.get("is_dashboard"):
        return render_template("client_embed.html", client=client, agent=agent,
                               embed_url="", serp_origin=_SERP_BASE, user=user,
                               is_dashboard=True,
                               dashboard_url="/%s/agents/%s/dashboard" % (client_slug, agent_slug),
                               runs_used=0, run_cap=AGENT_RUN_CAP, limit_reached=False)
    # External-tool agent: iframes the hosted tool (its host masked behind this portal
    # path). Run-metered like a SERP tool and capped at AGENT_RUN_CAP per user. The tool
    # now emits the same postMessage run contract as a SERP tool (source 'p2-agent'
    # instead of 'p2-seo-tool', no per-tool slug since one external tool = one embed),
    # so a run counts only when client_embed.html's listener sees a real
    # 'agent-run-started' message, not on page load — see log-run/finish-run below.
    ext = _client_external_tool(client, agent_slug)
    if ext:
        email = (user or {}).get("email", "")
        runs_used = _agent_run_counts(email).get(agent_slug, 0)
        limit_reached = runs_used >= AGENT_RUN_CAP
        ext_origin = "{0.scheme}://{0.netloc}".format(urlsplit(ext))
        return render_template("client_embed.html", client=client, agent=agent,
                               embed_url=("" if limit_reached else ext),
                               serp_origin=_SERP_BASE, ext_origin=ext_origin,
                               user=user, is_dashboard=False,
                               is_external=True,
                               runs_used=runs_used, run_cap=AGENT_RUN_CAP,
                               limit_reached=limit_reached)
    email = (user or {}).get("email", "")
    # Same per-agent, per-account run cap as /app (Agent Runs sheet is shared, so a
    # user's runs count identically no matter which surface they ran the agent on).
    runs_used = _agent_run_counts(email).get(agent_slug, 0)
    limit_reached = bool(agent.get("connected")) and runs_used >= AGENT_RUN_CAP
    embed_url = "" if limit_reached else (_app_embed_url(agent) if agent.get("connected") else "")
    return render_template("client_embed.html", client=client, agent=agent,
                           embed_url=embed_url, serp_origin=_SERP_BASE, user=user,
                           is_dashboard=False,
                           runs_used=runs_used, run_cap=AGENT_RUN_CAP, limit_reached=limit_reached)

def _client_agent_dashboard(client_slug, agent_slug):
    """Serve this client's co-branded dashboard HTML for a dashboard-backed agent.
    Gated by the same access rules as the rest of the portal. The file is the
    client variant (internal-ops chrome hidden), generated by the per-client build
    script. Rendered inside the portal's embed shell via an iframe."""
    client = CLIENTS.get(client_slug)
    if not client:
        abort(404)
    gate = _client_gate(client)
    if gate is not None:
        return gate
    if agent_slug not in client.get("agents", []):
        abort(404)
    # External-tool agents have no dashboard route (they iframe the hosted tool from
    # the run-metered Use page); a direct hit here is a 404, not a metering bypass.
    if _client_external_tool(client, agent_slug):
        abort(404)
    # Live LinkedIn Intelligence: render the exact /p2 dashboard template in client
    # mode (internal chrome hidden), pointed at this client's gated data endpoint.
    if _client_live_dashboard(client, agent_slug):
        data_url = "/%s/agents/%s/dashboard/data" % (client_slug, agent_slug)
        # The dashboard's target company is this client, so "Employee" (relative to
        # target) must be labelled as the client, not Position². Token feeds the
        # employee-detection fallback (see linkedin.js isEmployee).
        emp = client.get("short") or client.get("name") or "Client"
        li_cfg = {"employer": emp, "employerShort": emp,
                  "employerTokens": [emp.lower()]}
        resp = make_response(render_template(
            "linkedin_scraper.html", user=_get_user(),
            data_url=data_url, client_mode=True, client=client, li_cfg=li_cfg))
        resp.headers.update({"Cache-Control": "no-cache, no-store, must-revalidate",
                             "Pragma": "no-cache", "Expires": "0"})
        return resp
    path = _client_dashboard_path(client, agent_slug)
    if not path:
        abort(404)
    resp = make_response(send_file(str(path)))
    resp.headers.update({"Cache-Control": "no-cache, no-store, must-revalidate",
                         "Pragma": "no-cache", "Expires": "0"})
    return resp

def _client_linkedin_data(client_slug, agent_slug):
    """Gated JSON data endpoint for a client's live LinkedIn Intelligence dashboard.
    Same shape/response as the internal /p2 endpoint, read from this client's sheet.
    ?fresh=1 forces a live re-pull (the dashboard's Refresh button)."""
    client = CLIENTS.get(client_slug)
    if not client:
        abort(404)
    gate = _client_gate(client)
    if gate is not None:
        return gate
    if not _client_live_dashboard(client, agent_slug):
        abort(404)
    force = request.args.get("fresh") in ("1", "true", "yes")
    return _linkedin_data_response(client["linkedin_sheet"], force)

def _client_agent_log_run(client_slug, agent_slug):
    """Logs one real run of a client-portal agent, cap-enforced. Called client-side
    only after the embedded tool reports a genuine run start (same postMessage
    contract as /app). Feeds the shared 'Agent Runs' sheet, so these show up on the
    internal /p2 'Public Agent Usage' admin dashboard alongside /app runs."""
    client = CLIENTS.get(client_slug)
    if not client:
        return jsonify({"logged": False, "error": "unknown client"}), 404
    gate = _client_gate(client)
    if gate is not None:
        return jsonify({"logged": False, "error": "forbidden"}), 403
    if agent_slug not in client.get("agents", []):
        return jsonify({"logged": False, "error": "unknown agent"}), 404
    # client=client matters here: an external-tool-only agent (no seo_slug, e.g.
    # LinkedIn Strategy Researcher) only shows connected=True when _client_agent_view
    # knows the client, since that's what resolves is_external. Omitting it silently
    # marks the agent "not connected" and 400s every log-run call.
    agent = _client_agent_view(agent_slug, client)
    if not agent or not agent.get("connected"):
        return jsonify({"logged": False, "error": "agent not connected"}), 400
    user = _get_user()
    email = (user or {}).get("email", "")
    runs_used = _agent_run_counts(email).get(agent_slug, 0)
    if runs_used >= AGENT_RUN_CAP:
        return jsonify({"logged": False, "runs_used": runs_used, "runs_cap": AGENT_RUN_CAP, "at_cap": True})
    _log_agent_run(user, agent)
    runs_used += 1
    return jsonify({"logged": True, "runs_used": runs_used, "runs_cap": AGENT_RUN_CAP,
                    "at_cap": runs_used >= AGENT_RUN_CAP})

def _client_agent_finish_run(client_slug, agent_slug):
    """Saves one finished run's full output to the shared Postgres history, so it
    appears both in this client's History page and the internal /p2 'Agent Runs'
    admin dashboard. Same contract as /app's finish-run."""
    client = CLIENTS.get(client_slug)
    if not client:
        return jsonify({"saved": False, "error": "unknown client"}), 404
    gate = _client_gate(client)
    if gate is not None:
        return jsonify({"saved": False, "error": "forbidden"}), 403
    if agent_slug not in client.get("agents", []):
        return jsonify({"saved": False, "error": "unknown agent"}), 404
    agent = _client_agent_view(agent_slug, client)
    if not agent:
        return jsonify({"saved": False, "error": "unknown agent"}), 404
    data = request.get_json(silent=True) or {}
    output = data.get("output")
    if not isinstance(output, dict):
        return jsonify({"saved": False, "error": "missing output"}), 400
    run_id = _save_agent_run(_get_user(), agent, output)
    return jsonify({"saved": run_id is not None, "id": run_id})

def _client_history(client_slug):
    client = CLIENTS.get(client_slug)
    if not client:
        abort(404)
    gate = _client_gate(client)
    if gate is not None:
        return gate
    user = _get_user()
    slugs = set(client.get("agents", []))
    runs = [r for r in _list_agent_runs((user or {}).get("email", "")) if r["slug"] in slugs]
    for r in runs:
        meta = APP_AGENTS_BY_SLUG.get(r["slug"], {})
        r["ac"] = meta.get("ac", "#5b9dff")
        r["ac2"] = meta.get("ac2", "#8b5cf6")
        r["icon"] = meta.get("icon", "")
        r["display_ts"] = _fmt_run_ts(r["created_at"])
    return render_template("client_history.html", client=client, runs=runs,
                           nav_agents=_client_agents(client), user=user)

def _client_history_detail(client_slug, run_id):
    client = CLIENTS.get(client_slug)
    if not client:
        abort(404)
    gate = _client_gate(client)
    if gate is not None:
        return gate
    user = _get_user()
    run = _get_agent_run((user or {}).get("email", ""), run_id)
    if not run or run["slug"] not in set(client.get("agents", [])):
        return redirect("/" + client_slug + "/history")
    run["display_ts"] = _fmt_run_ts(run["created_at"])
    agent = APP_AGENTS_BY_SLUG.get(run["slug"], {})
    return render_template("client_history_detail.html", client=client, run=run,
                           agent=agent, nav_agents=_client_agents(client), user=user)

# Register explicit routes for each known client slug (no top-level catch-all).
for _cslug in CLIENTS:
    app.add_url_rule("/" + _cslug, "client_home__" + _cslug,
                     (lambda cs=_cslug: _client_home(cs)))
    app.add_url_rule("/" + _cslug + "/history", "client_history__" + _cslug,
                     (lambda cs=_cslug: _client_history(cs)))
    app.add_url_rule("/" + _cslug + "/history/<int:run_id>", "client_history_detail__" + _cslug,
                     (lambda run_id, cs=_cslug: _client_history_detail(cs, run_id)))
    app.add_url_rule("/" + _cslug + "/agents/<agent_slug>", "client_agent__" + _cslug,
                     (lambda agent_slug, cs=_cslug: _client_agent_detail(cs, agent_slug)))
    app.add_url_rule("/" + _cslug + "/agents/<agent_slug>/use", "client_use__" + _cslug,
                     (lambda agent_slug, cs=_cslug: _client_agent_use(cs, agent_slug)))
    app.add_url_rule("/" + _cslug + "/agents/<agent_slug>/dashboard", "client_dashboard__" + _cslug,
                     (lambda agent_slug, cs=_cslug: _client_agent_dashboard(cs, agent_slug)))
    app.add_url_rule("/" + _cslug + "/agents/<agent_slug>/dashboard/data", "client_dashboard_data__" + _cslug,
                     (lambda agent_slug, cs=_cslug: _client_linkedin_data(cs, agent_slug)))
    app.add_url_rule("/" + _cslug + "/agents/<agent_slug>/use/log-run", "client_logrun__" + _cslug,
                     (lambda agent_slug, cs=_cslug: _client_agent_log_run(cs, agent_slug)), methods=["POST"])
    app.add_url_rule("/" + _cslug + "/agents/<agent_slug>/use/finish-run", "client_finishrun__" + _cslug,
                     (lambda agent_slug, cs=_cslug: _client_agent_finish_run(cs, agent_slug)), methods=["POST"])


# ── v16: internal app relocated to /p2/* ─────────────────────────────────────────
# The entire logged-in surface now lives under /p2. These stubs 301-redirect the old
# internal URLs to their /p2 equivalents (query strings preserved) so bookmarks and
# external links keep resolving. Actual serving + @position2_required lives on the /p2
# routes below. Static assets and /api/* endpoints intentionally stay at their old
# paths (they are referenced by absolute path from page JS / the Ad Intel bundle).
def _p2_relocate_redirect(**kwargs):
    tgt = "/p2" + request.path
    if request.query_string:
        tgt += "?" + request.query_string.decode("utf-8", "ignore")
    return redirect(tgt, code=301)

_P2_LEGACY_RULES = [
    ("/hub",                                          "p2legacy_hub"),
    ("/gtm",                                          "p2legacy_gtm"),
    ("/gtm/sentiment-pulse",                          "p2legacy_sentiment"),
    ("/gtm/sentiment-pulse/",                         "p2legacy_sentiment_slash"),
    ("/gtm/ad-intelligence",                          "p2legacy_adintel"),
    ("/gtm/ad-intelligence/",                         "p2legacy_adintel_slash"),
    ("/gtm/anonymous-visitors",                       "p2legacy_anon"),
    ("/gtm/linkedin-scraper",                         "p2legacy_linkedin"),
    ("/seo",                                          "p2legacy_seo"),
    ("/seo/<tool_slug>",                              "p2legacy_seo_tool"),
    ("/accounts",                                     "p2legacy_accounts"),
    ("/signal-tracker/<account_id>",                  "p2legacy_st"),
    ("/signal-tracker/<account_id>/<section>",        "p2legacy_st_section"),
    ("/admin/usage",                                  "p2legacy_admin_usage"),
    ("/admin/usage/data",                             "p2legacy_admin_usage_data"),
    ("/admin/visitors",                               "p2legacy_admin_visitors"),
    ("/admin/visitors/data",                          "p2legacy_admin_visitors_data"),
    ("/admin/requests",                               "p2legacy_admin_requests"),
    ("/admin/email-test",                             "p2legacy_admin_email_test"),
]
for _rule, _endpoint in _P2_LEGACY_RULES:
    app.add_url_rule(_rule, _endpoint, _p2_relocate_redirect)

@app.route("/p2")
@app.route("/p2/")
def p2_root():
    """Bare /p2 entry -> the relocated hub."""
    return redirect("/p2/hub", code=302)

# ── Hub pages ───────────────────────────────────────────────────────────────────
@app.route("/p2/hub")
@position2_required
def hub():
    return render_template("hub.html", user=_get_user())

CX_CHAPTERS = [
    {"slug": "what-it-is", "num": 1, "icon": "💡", "title": "What it is",
     "teaser": "The one-line version, and the big idea behind it.",
     "stat": "30 sec read", "ac": "#22d3ee", "ac2": "#818cf8",
     "bg": "rgba(34,211,238,.14)", "bd": "rgba(34,211,238,.34)"},
    {"slug": "why-we-built-it", "num": 2, "icon": "🚀", "title": "Why we built it",
     "teaser": "Arena had the agents. Nobody had the front door.",
     "stat": "1 min read", "ac": "#fb7185", "ac2": "#34d399",
     "bg": "rgba(251,113,133,.14)", "bd": "rgba(251,113,133,.34)"},
    {"slug": "who-its-for", "num": 3, "icon": "🎯", "title": "Who it's for",
     "teaser": "Four teams, one shared workspace.",
     "stat": "30 sec read", "ac": "#a78bfa", "ac2": "#38bdf8",
     "bg": "rgba(167,139,250,.14)", "bd": "rgba(167,139,250,.34)"},
    {"slug": "how-to-navigate", "num": 4, "icon": "🧭", "title": "How to navigate",
     "teaser": "Sign in → Hub → run an agent. That's it.",
     "stat": "1 min read", "ac": "#6366f1", "ac2": "#8b5cf6",
     "bg": "rgba(99,102,241,.14)", "bd": "rgba(99,102,241,.34)"},
    {"slug": "agent-landscape", "num": 5, "icon": "🗺️", "title": "The agent landscape",
     "teaser": "18 agents, 3 buckets, at a glance.",
     "stat": "18 agents", "ac": "#22d3ee", "ac2": "#38bdf8",
     "bg": "rgba(34,211,238,.14)", "bd": "rgba(34,211,238,.34)"},
    {"slug": "every-agent", "num": 6, "icon": "🤖", "title": "Every agent, explained",
     "teaser": "What each does, how it's built, who runs it.",
     "stat": "18 cards", "ac": "#e879f9", "ac2": "#818cf8",
     "bg": "rgba(232,121,249,.14)", "bd": "rgba(232,121,249,.34)"},
    {"slug": "tech-stack", "num": 7, "icon": "🧬", "title": "Tech stack",
     "teaser": "Five layers, full candor.",
     "stat": "5 layers", "ac": "#fbbf24", "ac2": "#34d399",
     "bg": "rgba(251,191,36,.14)", "bd": "rgba(251,191,36,.34)"},
    {"slug": "demo", "num": 8, "icon": "▶️", "title": "Watch the demo",
     "teaser": "The whole platform, in one watch.",
     "stat": "Demo video", "ac": "#fb7185", "ac2": "#f472b6",
     "bg": "rgba(251,113,133,.14)", "bd": "rgba(251,113,133,.34)"},
]

@app.route("/p2/playbook")
@position2_required
def p2_playbook():
    """Internal playbook hub — pick a chapter."""
    return render_template("context.html", user=_get_user(), chapters=CX_CHAPTERS, chapter=None)

@app.route("/p2/playbook/<slug>")
@position2_required
def p2_playbook_chapter(slug):
    """A single playbook chapter as its own page."""
    idx = next((i for i, c in enumerate(CX_CHAPTERS) if c["slug"] == slug), None)
    if idx is None:
        abort(404)
    prev_chapter = CX_CHAPTERS[idx - 1] if idx > 0 else None
    next_chapter = CX_CHAPTERS[idx + 1] if idx < len(CX_CHAPTERS) - 1 else None
    return render_template("context.html", user=_get_user(), chapters=CX_CHAPTERS,
                            chapter=CX_CHAPTERS[idx], prev_chapter=prev_chapter, next_chapter=next_chapter)

@app.route("/p2/context")
def p2_context():
    """Legacy URL, kept as a redirect so old bookmarks and links still work."""
    return redirect(url_for("p2_playbook"), code=301)

@app.route("/p2/context/<slug>")
def p2_context_chapter(slug):
    """Legacy URL, kept as a redirect so old bookmarks and links still work."""
    return redirect(url_for("p2_playbook_chapter", slug=slug), code=301)

@app.route("/p2/gtm")
@position2_required
def gtm():
    return render_template("gtm.html", user=_get_user())


# LinkedIn Strategy Researcher — external Position2-hosted tool (watchtower), embedded
# behind this internal path so the .vercel.app host stays masked in the address bar.
# Internal GTM tool: @position2 staff only, no run cap and no metering (unlike the
# co-branded client-portal copy of this agent, which is capped).
LINKEDIN_RESEARCHER_URL = "https://watchtower-by-position2.vercel.app/linkedin.html"

@app.route("/p2/gtm/linkedin-strategy-researcher")
@app.route("/p2/gtm/linkedin-strategy-researcher/")
@position2_required
def linkedin_strategy_researcher():
    """Competitive LinkedIn analysis tool, embedded from watchtower. Uncapped."""
    return render_template("embed.html",
        user=_get_user(),
        title="LinkedIn Strategy Researcher",
        embed_url=LINKEDIN_RESEARCHER_URL,
        breadcrumb=[("Hub", "/p2/hub"), ("GTM", "/p2/gtm")],
        current="LinkedIn Strategy Researcher",
        accent="#a855f7",
    )


@app.route("/p2/gtm/sentiment-pulse")
@app.route("/p2/gtm/sentiment-pulse/")
@position2_required
def call_sentiment():
    # HIDDEN 2026-07-23: Sentiment Pulse was a demo/proxy dashboard (seeded
    # "Cedar Valley Health" data), not a real data pipeline, so it is pulled from
    # the live surface. Endpoint is kept registered (legacy redirects still resolve
    # to it) but no longer serves the page. To restore, remove the abort() and
    # uncomment the render line below, then un-hide the card in templates/gtm.html.
    abort(404)
    return render_template("call_sentiment.html", user=_get_user())  # noqa: retained for restore


@app.route("/gtm/call-sentiment")
@app.route("/gtm/call-sentiment/")
@position2_required
def call_sentiment_legacy():
    return redirect(url_for("call_sentiment"))

# ── Legacy /ppc* page URLs → 301 redirect to canonical /gtm* (links still resolve) ──
@app.route("/ppc")
@app.route("/ppc/")
def ppc_redirect():
    return redirect("/p2/gtm", code=301)

@app.route("/ppc/ad-intelligence")
@app.route("/ppc/ad-intelligence/")
def ppc_ad_intelligence_redirect():
    return redirect("/p2/gtm/ad-intelligence", code=301)

@app.route("/ppc/anonymous-visitors")
def ppc_anonymous_visitors_redirect():
    return redirect("/p2/gtm/anonymous-visitors", code=301)

@app.route("/ppc/linkedin-scraper")
def ppc_linkedin_scraper_redirect():
    return redirect("/p2/gtm/linkedin-intelligence", code=301)

@app.route("/p2/seo")
@position2_required
def seo():
    return render_template("seo.html", user=_get_user(), seo_tools=_seo_tools())

# ── Embedded dashboards ─────────────────────────────────────────────────────────
_SERP_BASE = "https://seo-apps-production-37a6.up.railway.app"

# ── Ad Intelligence (built React app served directly — no iframe) ────────────
AD_INTEL_SHEET_ID = "16U5_QSxMmrAGKvK5dHScBu1Et4BJ1p8Q1ns5LycRA0s"

@app.route("/p2/gtm/ad-intelligence")
@app.route("/p2/gtm/ad-intelligence/")
@position2_required
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

@app.route("/p2/seo/<tool_slug>")
@position2_required
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
        breadcrumb=[("Hub", "/p2/hub"), ("SEO", "/p2/seo")],
        current=tool["name"],
        accent="#34d399",
    )

# ── Company Signal Tracker ───────────────────────────────────────────────────────
@app.route("/p2/accounts")
@position2_required
def accounts():
    cards_html = "".join(_build_account_card(aid, cfg) for aid, cfg in ACCOUNTS.items())
    return render_template("accounts.html", user=_get_user(), account_cards=cards_html)

@app.route("/p2/signal-tracker/<account_id>")
@app.route("/p2/signal-tracker/<account_id>/<section>")
@position2_required
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
@position2_required
def dashboard_legacy(account_id: str, section: str = None):
    """Back-compat: old /dashboard/* URLs redirect to canonical /signal-tracker/*."""
    target = "/p2/signal-tracker/" + account_id + (("/" + section) if section else "")
    return redirect(target, code=301)

@app.route("/api/whoami")
@position2_required
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
        vid = (request.cookies.get("p2_vid") or "").strip()[:64]

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
            vid,   # Visitor ID (p2_vid) -- links this post-login page view back to the
                   # visitor's pre-login Visitor Analytics journey, same key used by
                   # Member Signins / internal login log.
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
                       "Page URL","Seconds","Duration","IP","Browser","OS","Device","Visitor ID"]]
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


def _va_sheets_service_st():
    """Same as _va_sheets_service() but forces static (bundled) discovery so
    building a fresh service does no network round-trip. Used to build one
    service PER THREAD when reading several tabs concurrently -- googleapiclient's
    httplib2 transport is not safe to share a single service across threads."""
    import json as _j
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    sa_str = os.environ.get("GOOGLE_SA_JSON", "")
    if not sa_str or not LOGIN_LOG_SHEET_ID:
        return None
    try:
        creds = service_account.Credentials.from_service_account_info(
            _j.loads(sa_str), scopes=["https://www.googleapis.com/auth/spreadsheets"])
        return build("sheets", "v4", credentials=creds,
                     cache_discovery=False, static_discovery=True)
    except Exception as e:
        log.warning("static sheets service build failed: %s", e)
        return None

_IP_CACHE = {}
_IP_RESOLVE_CACHE = {}

# The real de-anonymization engine (multi-signal resolve + gate + confidence +
# free firmographics + intent). Imported lazily-safe: if the package is missing
# the surface degrades to the legacy IPinfo-only path below.
try:
    from visitor_intelligence import resolve_ip as _vi_resolve_ip
    from visitor_intelligence import deepen_with_apollo as _vi_deepen_with_apollo
    from visitor_intelligence import score_intent as _vi_score_intent
    from visitor_intelligence import enrich_company_free as _vi_enrich_company_free
    _VI_OK = True
except Exception as _vi_e:  # pragma: no cover
    _VI_OK = False
    log.warning("visitor_intelligence unavailable, using legacy reverse-IP: %s", _vi_e)


def _ip_resolve(ip: str) -> dict:
    """Fast per-IP resolution (cached per IP): company/domain/confidence/
    connection_type/identifiable from IPinfo + reverse DNS + RDAP only. No
    Apollo credits are spent here, and -- deliberately -- no free-tier company
    homepage fetch either: this is called once per unique visitor IP for a
    dashboard that can carry hundreds of them in one page load, and a
    homepage fetch per visitor made that load take minutes (see
    _fetch_visitor_analytics, which resolves every unique IP on the page).
    Firmographics (industry/employees/revenue/description) stay empty here;
    they're filled in on demand by _ip_deepen_with_apollo when a rep asks for
    one specific lead, not eagerly for every row. Empty dict for local/unknown."""
    if not ip or ip in ("127.0.0.1", "::1", "localhost"):
        return {}
    if ip in _IP_RESOLVE_CACHE:
        return _IP_RESOLVE_CACHE[ip]
    rec = {}
    if _VI_OK:
        try:
            res = _vi_resolve_ip(ip, ipinfo_token=os.environ.get("IPINFO_TOKEN", ""),
                                online=True)
            d = res.to_dict()
            rec = {
                "ip": d.get("ip"), "identifiable": d.get("identifiable"),
                "connection_type": d.get("connection_type"),
                "confidence": d.get("confidence"), "company": d.get("company"),
                "domain": d.get("domain"), "asn": d.get("asn"),
                "asn_org": d.get("asn_org"), "country": d.get("country"),
                "city": d.get("city"), "method": d.get("method"),
                "methods": d.get("methods"), "is_vpn": d.get("is_vpn"),
                "is_proxy": d.get("is_proxy"), "is_hosting": d.get("is_hosting"),
                "reasons": d.get("reasons"),
                # not resolved on this fast path -- see _ip_deepen_with_apollo
                "industry": None, "employees": None, "employee_range": None,
                "revenue": None, "hq_country": d.get("country"), "hq_city": None,
                "linkedin_url": None, "technologies": [], "description": None,
                "social_links": [], "buying_committee": [], "enrichment_source": None,
            }
        except Exception as e:
            log.warning("ip_resolve failed for %s: %s", ip, e)
            rec = {}
    _IP_RESOLVE_CACHE[ip] = rec
    return rec


def _resolve_ips_bulk(ips, max_workers: int = 16) -> None:
    """Warm _IP_RESOLVE_CACHE for many unique IPs CONCURRENTLY. Each _ip_resolve
    call is one or two blocking network round-trips (IPinfo, sometimes RDAP);
    resolving a dashboard's worth of unique visitor IPs one at a time in a
    Python for-loop is exactly what made the Anonymous Traffic page take
    minutes to load. This is pure I/O-bound fan-out, so a small thread pool
    turns that into wall-clock roughly (count / max_workers) round-trips
    instead of (count). Call this once with every IP the page is about to
    look up, before any code calls _ip_resolve/_ip_company in a loop."""
    todo = [ip for ip in dict.fromkeys(ips)
            if ip and ip not in ("127.0.0.1", "::1", "localhost")
            and ip not in _IP_RESOLVE_CACHE]
    if not todo:
        return
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=min(max_workers, len(todo))) as ex:
        list(ex.map(_ip_resolve, todo))


def _ip_deepen_with_apollo(ip: str, with_committee: bool = True) -> dict:
    """EXPLICIT, human-triggered enrichment for one already-resolved IP/company.
    Call this ONLY from a deliberate rep action (e.g. an "Enrich further"
    button), never automatically from a page load or the bulk anon-traffic
    builder -- that bulk path (_ip_resolve) deliberately stays IP-only (no
    homepage fetch) so it can run on hundreds of visitors without the page
    taking minutes to load. This on-demand, single-visitor path is exactly
    where the homepage fetch belongs: first the free tier (schema.org/
    OpenGraph/tech-stack off the company's own site, zero cost), then Apollo
    on top of it (spends ~1 credit) if APOLLO_API_KEY is configured. Returns
    the enriched record; {} if the engine is unavailable or there's no
    resolvable domain to enrich."""
    if not _VI_OK:
        return {}
    rec = _ip_resolve(ip)
    if not rec.get("domain"):
        return {}
    rec = dict(rec)
    if rec.get("enrichment_source") != "free":
        try:
            free = _vi_enrich_company_free(rec["domain"])
            if free:
                rec["company"] = free.get("name") or rec.get("company")
                rec["description"] = free.get("description")
                rec["hq_city"] = free.get("hq_city")
                rec["hq_country"] = free.get("hq_country") or rec.get("hq_country")
                rec["linkedin_url"] = free.get("linkedin_url")
                rec["social_links"] = free.get("social_links") or []
                rec["technologies"] = free.get("technologies") or []
                rec["enrichment_source"] = "free"
        except Exception as e:
            log.warning("free enrich failed for domain=%s: %s", rec["domain"], e)
    apollo_key = os.environ.get("APOLLO_API_KEY", "")
    if not apollo_key:
        _IP_RESOLVE_CACHE[ip] = rec
        return rec
    try:
        deepened = _vi_deepen_with_apollo(dict(rec), apollo_key=apollo_key,
                                        with_committee=with_committee)
        _IP_RESOLVE_CACHE[ip] = deepened  # cache the richer record going forward
        return deepened
    except Exception as e:
        log.warning("apollo deepen failed for %s: %s", ip, e)
        return {}


def _ip_company(ip: str) -> str:
    """Best-effort reverse-IP -> organization NAME. Now backed by the
    visitor_intelligence engine: residential/mobile/hosting IPs are gated to ''
    (they name the carrier, not a lead), and business IPs get the canonical
    Apollo company name when available. Backward-compatible return (a string).
    Falls back to the legacy IPinfo-only lookup if the engine is unavailable."""
    if not ip or ip in ("127.0.0.1", "::1", "localhost"):
        return ""
    if ip in _IP_CACHE:
        return _IP_CACHE[ip]
    out = ""
    if _VI_OK:
        rec = _ip_resolve(ip)
        out = (rec.get("company") or "") if rec.get("identifiable") else ""
        _IP_CACHE[ip] = out
        return out
    # ---- legacy fallback (engine unavailable) ----
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

# --------------------------------------------------------------------------- #
# Identity graph (person-level resolution)
# --------------------------------------------------------------------------- #
# Resolves an anonymous p2_vid to a named person via: first-party deterministic
# clusters (login/form retro-stitch), Apollo person enrichment, and any external
# co-op/graph provider configured. Populated by the login paths + /api/identify.
_ID_GRAPH = None

def _identity_graph():
    global _ID_GRAPH
    if _ID_GRAPH is None:
        if not _VI_OK:
            _ID_GRAPH = False
        else:
            try:
                from visitor_intelligence import build_identity_graph
                # Apollo person enrichment (spends credits) only when
                # VI_ENRICH_ON_VIEW is set; the co-op file provider (free) always
                # loads if VI_COOP_FILE points at a real feed.
                akey = os.environ.get("APOLLO_API_KEY", "") \
                    if os.environ.get("VI_ENRICH_ON_VIEW", "") in ("1", "true", "yes") else ""
                _ID_GRAPH = build_identity_graph(apollo_key=akey)
            except Exception as e:
                log.warning("identity graph init failed: %s", e)
                _ID_GRAPH = False
    return _ID_GRAPH or None


def _graph_identify(vid, email="", name="", title="", company="",
                    crm_id="", source="login"):
    """Feed a deterministic person anchor into the graph (retro-stitches all of
    this vid's prior anonymous sessions). Safe/no-op on any failure."""
    g = _identity_graph()
    if not (g and vid):
        return
    try:
        g.identify(vid[:64], email=email or None, name=name or None,
                title=title or None, company=company or None,
                crm_id=crm_id or None, source=source)
    except Exception as e:
        log.warning("graph identify failed: %s", e)


def _graph_resolve_person(vid) -> dict:
    """Resolve a vid to a person via the graph. {} if anonymous. Never fabricates."""
    g = _identity_graph()
    if not (g and vid):
        return {}
    try:
        pm = g.resolve_person(vid[:64])
        return pm.to_dict() if pm.resolved else {}
    except Exception as e:
        log.warning("graph resolve_person failed: %s", e)
        return {}


def _va_identity_map(vi_rows=None) -> dict:
    """visitor_id -> {name,email,company,source}. Merges access-form conversions + provider identifies.

    Pass vi_rows (the raw 'Visitor Identities' sheet values, header included) to
    reuse an already-fetched read instead of issuing another Sheets round-trip --
    the member analytics page batch-fetches that tab alongside its others."""
    m = {}
    try:
        for req in _read_access_requests(limit=2000):
            v = (req.get("vid") or "").strip()
            if v:
                m[v] = {"name": req.get("name", ""), "email": req.get("email", ""),
                        "company": req.get("company", ""), "source": "Lead form"}
    except Exception:
        pass
    if vi_rows is None:
        svc = _va_sheets_service()
        if svc:
            try:
                vi_rows = svc.spreadsheets().values().get(
                    spreadsheetId=LOGIN_LOG_SHEET_ID, range="Visitor Identities!A1:G5000").execute().get("values", [])
            except Exception:
                vi_rows = []
    for x in ((vi_rows or [])[1:] or []):
        def cc(i): return x[i] if len(x) > i else ""
        v = (cc(1) or "").strip()
        if v:
            m[v] = {"name": cc(2), "email": cc(3), "company": cc(4),
                    "source": cc(6) or "provider"}
    return m


def _login_events_by_vid() -> dict:
    """p2_vid -> {type, email, name, picture, events:[...]} across every Google
    sign-in this platform has ever recorded -- 'Member Signins' (public /app
    sign-ups) plus the internal login log (default tab, @position2.com staff).
    This is the join that lets Anonymous Traffic show what an anonymous
    visitor went on to do AFTER they signed in, not just before."""
    svc = _va_sheets_service()
    out: dict = {}
    if not svc:
        return out

    def read(rng):
        try:
            return svc.spreadsheets().values().get(
                spreadsheetId=LOGIN_LOG_SHEET_ID, range=rng).execute().get("values", [])
        except Exception as e:
            log.warning("login events by vid read failed (%s): %s", rng, e)
            return []

    def add(vid, email, name, picture, ts, kind):
        if not vid:
            return
        entry = out.setdefault(vid, {"email": "", "name": "", "picture": "", "events": []})
        entry["events"].append({"ts": ts, "email": email, "name": name, "kind": kind})
        # Most recent event wins for the display profile.
        if email: entry["email"] = email
        if name: entry["name"] = name
        if picture: entry["picture"] = picture

    ms_rows = read("%s!A:T" % _MEMBER_TAB)
    for r in (ms_rows[1:] if len(ms_rows) > 1 else []):
        def mc(i, d=""): return r[i] if i < len(r) else d
        add(mc(9), mc(5), mc(6), mc(8), mc(0), "member")

    login_rows = read("A1:U5000")
    for r in (login_rows[1:] if len(login_rows) > 1 else []):
        def lc(i, d=""): return r[i] if i < len(r) else d
        add(lc(20), lc(5), lc(6), lc(8), lc(0), "staff")

    for entry in out.values():
        entry["events"].sort(key=lambda e: e["ts"] or "")
        entry["type"] = "staff" if (entry["email"] or "").lower().endswith("@position2.com") else "member"
        entry["first_ts"] = entry["events"][0]["ts"] if entry["events"] else ""
        entry["last_ts"] = entry["events"][-1]["ts"] if entry["events"] else ""
        entry["count"] = len(entry["events"])
    return out


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
    # Feed the identity graph too (deterministic person anchor + retro-stitch).
    _graph_identify(vid, email=str(data.get("email", "")),
                    name=str(data.get("name", "")), title=str(data.get("title", "")),
                    company=str(data.get("company", "")),
                    source=str(data.get("source", "provider")))
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

_VISITOR_ANALYTICS_CACHE = {"data": None, "ts": 0.0}
_VISITOR_ANALYTICS_CACHE_TTL = 300  # seconds — this aggregation resolves hundreds of
                                    # visitor IPs and re-reads two Sheets tabs; too
                                    # slow to redo on every page view/auto-refresh.

def _fetch_visitor_analytics(force: bool = False) -> dict:
    """Aggregate the 'Visitor Analytics' tab for the admin dashboard (TTL-cached)."""
    now = time.time()
    if not force and _VISITOR_ANALYTICS_CACHE["data"] is not None and \
            (now - _VISITOR_ANALYTICS_CACHE["ts"]) < _VISITOR_ANALYTICS_CACHE_TTL:
        return _VISITOR_ANALYTICS_CACHE["data"]
    data = _fetch_visitor_analytics_uncached()
    _VISITOR_ANALYTICS_CACHE["data"] = data
    _VISITOR_ANALYTICS_CACHE["ts"] = now
    return data

def _fetch_visitor_analytics_uncached() -> dict:
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

    # ---- CTA breakdown, aligned to the live public-page CTAs ----------------
    # After the auth rework the primary CTA is "Sign up" (one-click Google), the
    # contextual "data-demo" CTAs open the contact/lead form, and the secondary
    # auth link is "Log in". Legacy labels from before the rework
    # (request_access:*, sign_in) are folded in so historical rows still count.
    signup_clicks = cta_counts.get("signup", 0)
    login_clicks  = cta_counts.get("log_in", 0) + cta_counts.get("sign_in", 0)
    watch_clicks  = cta_counts.get("watch_walkthrough", 0)
    lead_interest_counts = Counter()
    agentcard_clicks = 0
    outbound_clicks = 0
    for lbl, n in cta_counts.items():
        if lbl.startswith("lead:"):
            lead_interest_counts[lbl.split(":", 1)[1] or "Talk to us"] += n
        elif lbl.startswith("request_access:"):            # legacy → lead
            it = lbl.split(":", 1)[1] or "Talk to us"
            lead_interest_counts["Talk to us" if it == "Request access" else it] += n
        elif lbl.startswith("agent_card:"):
            agentcard_clicks += n
        elif lbl.startswith("outbound:"):
            outbound_clicks += n
    lead_clicks = sum(lead_interest_counts.values())
    lead_interests = lead_interest_counts.most_common(12)
    cta_groups = sorted(
        [g for g in (
            ("Sign up", signup_clicks),
            ("Log in", login_clicks),
            ("Watch walkthrough", watch_clicks),
            ("Lead form", lead_clicks),
            ("Agent cards", agentcard_clicks),
            ("Outbound links", outbound_clicks),
        ) if g[1]],
        key=lambda x: x[1], reverse=True)

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
    video_pages = Counter(c(r,"Page Title") or c(r,"Page URL") for r in human if c(r,"Video")).most_common(10)

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
    vid_pages = defaultdict(int)
    va_by_vid = defaultdict(list)
    for r in human:
        v = c(r,"Visitor ID")
        if v:
            vid_pages[v] += 1
            va_by_vid[v].append(r)
    # Rank once, up front, so we know the exact (bounded) set of IPs this page
    # load needs -- then resolve all of them CONCURRENTLY in one pool instead
    # of one-by-one inside each loop below. This is the fix for the multi-
    # minute load: same IPs, same caps, just fetched in parallel.
    visitor_ids_ranked = sorted(visitors, key=lambda v: vid_pages.get(v,0), reverse=True)[:500]
    _ip_pool = {vid_ip[v] for v in visitor_ids_ranked if vid_ip.get(v)}
    _ip_pool.update(ipv for _, ipv in list(vid_ip.items())[:150])
    if _VI_OK:
        _resolve_ips_bulk(_ip_pool)
    vid_company = {}; _ipc = {}
    for v, ipv in list(vid_ip.items())[:150]:
        co = _ipc.get(ipv)
        if co is None:
            co = _ip_company(ipv); _ipc[ipv] = co
        if co: vid_company[v] = co
    companies = Counter()
    for v in visitors:
        co = ((idmap.get(v,{}) or {}).get("company") or vid_company.get(v) or "").strip()
        if co: companies[co] += 1
    top_companies = companies.most_common(15)

    # ---- did this anonymous visitor go on to sign in? (member or staff) ----
    login_map = _login_events_by_vid()
    signed_in = sum(1 for v in visitors if v in login_map)
    signed_in_rate = round(signed_in/unique_visitors*100, 1) if unique_visitors else 0

    # Sign-up funnel: the "Sign up" pop-up hands off to Google, so its bottom is
    # the same signed-in signal we already stitch server-side (no form submit).
    signup_funnel = {"clicked": signup_clicks, "signed_in": signed_in}
    signup_cvr = round(signed_in/signup_clicks*100, 1) if signup_clicks else 0

    pv_rows = []
    if svc:
        try:
            pv_rows = svc.spreadsheets().values().get(
                spreadsheetId=LOGIN_LOG_SHEET_ID, range="Page Views!A:N").execute().get("values", [])
        except Exception as e:
            log.warning("visitor analytics: page views read failed: %s", e)
    pv_by_vid = defaultdict(list)
    for r in (pv_rows[1:] if len(pv_rows) > 1 else []):
        pvid = r[13] if len(r) > 13 else ""
        if pvid: pv_by_vid[pvid].append(r)

    # Every unique visitor gets a summary + timeline (not just identified ones) so the
    # "Unique visitors"/"Signed in later" KPI cards can drill into real people, not just charts.
    # Capped by page-activity to bound timeline-building cost on very large visitor counts.
    all_visitors = []
    for v in visitor_ids_ranked:
        idn = idmap.get(v) or {}
        co = idn.get("company") or vid_company.get(v) or ""
        lg = login_map.get(v)
        email = idn.get("email") or (lg or {}).get("email", "")
        name = idn.get("name") or (lg or {}).get("name", "")
        source = idn.get("source") or ("reverse-IP" if co else "")
        # identity graph: resolve the person behind this vid (retro-stitched
        # login / form / provider / co-op). Fills gaps; never fabricates.
        person_conf = 0.0; person_method = ""; title = idn.get("title", "")
        gp = _graph_resolve_person(v)
        if gp:
            name = name or gp.get("full_name") or ""
            email = email or gp.get("email") or ""
            title = title or gp.get("title") or ""
            co = co or gp.get("company") or ""
            person_conf = gp.get("confidence") or 0.0
            person_method = gp.get("method") or ""
            source = source or person_method
        rs_sorted = sorted(va_by_vid.get(v, []), key=lambda r: c(r,"Timestamp (IST)"))
        tl = []
        for r in rs_sorted:
            tl.append({"t": c(r,"Timestamp (IST)"), "kind": "view",
                       "label": c(r,"Page Title") or c(r,"Page URL") or "Page view",
                       "meta": c(r,"Referrer Host") or ""})
        for ev in (lg or {}).get("events", []):
            tl.append({"t": ev["ts"], "kind": "signin",
                       "label": "Signed in as %s (%s)" % (ev["email"], ev["kind"]), "meta": ""})
        for r in sorted(pv_by_vid.get(v, []), key=lambda r: r[0] if r else ""):
            tl.append({"t": r[0] if len(r)>0 else "", "kind": "post",
                       "label": (r[5] if len(r)>5 else "") or (r[6] if len(r)>6 else "") or "Page view",
                       "meta": r[8] if len(r)>8 else ""})
        tl.sort(key=lambda x: x["t"] or "")
        # ---- engine enrichment: confidence + connection type + firmographics + intent ----
        ip_v = vid_ip.get(v, "")
        res = _ip_resolve(ip_v) if (ip_v and _VI_OK) else {}
        view_pages = [x["label"] for x in tl if x.get("kind") == "view"]
        if _VI_OK:
            i_score, i_stage, _ = _vi_score_intent(
                view_pages, pageviews=vid_pages.get(v, 0),
                sessions=1 + sum(1 for x in tl if x.get("kind") == "signin"))
        else:
            i_score, i_stage = 0.0, "awareness"
        all_visitors.append({"vid": v[:8], "name": name, "email": email,
            "title": title,
            "company": co, "source": source, "pages": vid_pages.get(v,0),
            "device": c(rs_sorted[0],"Device") if rs_sorted else "",
            "first_ts": tl[0]["t"] if tl else "", "last_ts": tl[-1]["t"] if tl else "",
            "status": (lg or {}).get("type", ""), "converted": bool(lg),
            # person-level identity graph fields
            "person_confidence": person_conf, "person_method": person_method,
            # de-anonymization engine fields
            "ip": ip_v,
            "domain": res.get("domain") or "",
            "confidence": res.get("confidence") or 0.0,
            "connection_type": res.get("connection_type") or "",
            "industry": res.get("industry") or "",
            "employees": res.get("employee_range") or res.get("employees") or "",
            "revenue": res.get("revenue") or "",
            "linkedin_url": res.get("linkedin_url") or "",
            "enrichment_source": res.get("enrichment_source") or "",
            "buying_committee": [],
            "intent_score": i_score, "intent_stage": i_stage,
            "timeline": tl[:60]})
    all_visitors.sort(key=lambda x: (x["last_ts"] or x["first_ts"] or ""), reverse=True)

    identified = [x for x in all_visitors if x["source"] or x["company"] or x["converted"] or x["name"]]
    identified.sort(key=lambda x: (not x["converted"], -x["pages"])); identified = identified[:60]
    all_visitors = all_visitors[:300]

    recent = []
    for r in reversed(data):
        rvid = c(r,"Visitor ID")
        rlg = login_map.get(rvid) or {}
        recent.append({
            "ts": c(r,"Timestamp (IST)"), "vid": (rvid or "")[:8],
            "page": c(r,"Page Title") or c(r,"Page URL"), "landing": c(r,"Landing Page"),
            "ref": c(r,"Referrer Host") or "direct", "device": c(r,"Device"),
            "engaged": fmt(to_int(c(r,"Engaged Time (s)"))), "scroll": c(r,"Max Scroll %"),
            "pages": c(r,"Pages In Session"), "new": c(r,"New Visitor"),
            "form": c(r,"Form Stage"), "bot": c(r,"Bot"),
            "who": (idmap.get(rvid) or {}).get("name","") or rlg.get("name","") or rlg.get("email",""),
            "company": ((idmap.get(rvid) or {}).get("company","") or vid_company.get(rvid,"")),
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
            "signed_in": signed_in, "signed_in_rate": signed_in_rate,
            "signup_clicks": signup_clicks, "login_clicks": login_clicks,
            "watch_clicks": watch_clicks, "lead_clicks": lead_clicks,
            "signup_cvr": signup_cvr,
        },
        "series": series, "top_pages": top_pages, "top_landing": top_landing,
        "referrers": referrers, "utm_source": utm_source, "utm_campaign": utm_campaign,
        "devices": devices, "oses": oses, "browsers": browsers, "langs": langs,
        "scroll": sb, "cta": cta_top, "cta_groups": cta_groups,
        "lead_interests": lead_interests, "signup_funnel": signup_funnel,
        "form_funnel": form_funnel,
        "search": search_top, "rage": rage_top, "cwv": cwv, "recent": recent,
        "top_companies": top_companies, "identified": identified,
        "all_visitors": all_visitors, "video_pages": video_pages,
    }

@app.route("/p2/admin/anonymous-traffic")
@admin_required
def admin_visitors():
    """Admin-only anonymous visitor analytics dashboard."""
    return render_template("admin_visitors.html", user=_get_user())

@app.route("/p2/admin/anonymous-traffic/data")
@admin_required
def admin_visitors_data():
    """JSON aggregates for the visitor analytics dashboard (TTL-cached; pass
    ?fresh=1 to force a live re-pull, e.g. from the page's Refresh button)."""
    force = request.args.get("fresh") in ("1", "true", "yes")
    return jsonify(_fetch_visitor_analytics(force=force))

@app.route("/p2/admin/anonymous-traffic/deepen", methods=["POST"])
@admin_required
def admin_visitor_deepen():
    """Explicit, human-triggered 'Enrich further' action for one visitor's
    company. Only reachable by clicking the button on a single visitor's
    record in the Anonymous Traffic dashboard -- never called automatically.
    Always layers in free-tier company data (zero cost); additionally spends
    ~1 Apollo credit for firmographics/buying-committee if APOLLO_API_KEY is
    configured -- if not, the free-tier fields are still returned."""
    if not _VI_OK:
        return jsonify({"error": "enrichment engine unavailable"}), 503
    ip = (request.get_json(silent=True) or {}).get("ip", "").strip()
    if not ip:
        return jsonify({"error": "ip required"}), 400
    rec = _ip_deepen_with_apollo(ip, with_committee=True)
    if not rec or not rec.get("domain"):
        return jsonify({"error": "nothing to enrich for this visitor"}), 200
    return jsonify({"ok": True, "record": {
        "domain": rec.get("domain") or "",
        "company": rec.get("company") or "",
        "description": rec.get("description") or "",
        "industry": rec.get("industry") or "",
        "employees": rec.get("employee_range") or rec.get("employees") or "",
        "revenue": rec.get("revenue") or "",
        "linkedin_url": rec.get("linkedin_url") or "",
        "confidence": rec.get("confidence") or 0.0,
        "enrichment_source": rec.get("enrichment_source") or "free",
        "buying_committee": rec.get("buying_committee") or [],
    }})


_MEMBER_ANALYTICS_CACHE = {"data": None, "ts": 0.0}
_MEMBER_ANALYTICS_CACHE_TTL = 300  # seconds — same as the sibling admin dashboards.
                                   # This aggregation does several serial Sheets reads
                                   # and resolves every member IP, so it's too slow to
                                   # redo on every page view / auto-refresh.


def _fetch_member_analytics(force: bool = False) -> dict:
    """Public Page Analytics aggregation (TTL-cached; pass force=True to re-pull)."""
    now = time.time()
    if not force and _MEMBER_ANALYTICS_CACHE["data"] is not None and \
            (now - _MEMBER_ANALYTICS_CACHE["ts"]) < _MEMBER_ANALYTICS_CACHE_TTL:
        return _MEMBER_ANALYTICS_CACHE["data"]
    data = _fetch_member_analytics_uncached()
    _MEMBER_ANALYTICS_CACHE["data"] = data
    _MEMBER_ANALYTICS_CACHE["ts"] = now
    return data


def _fetch_member_analytics_uncached() -> dict:
    """Public (non-Position2) Google sign-ins, joined to their pre-login Visitor
    Analytics journey (by p2_vid) and post-login Page Views (by email). This is the
    'sync' dashboard: the same person, before and after they signed in."""
    from collections import Counter, defaultdict
    from concurrent.futures import ThreadPoolExecutor
    svc = _va_sheets_service()   # also the "is Sheets configured?" probe (see return)

    # These are the exact same values().get() reads the dashboard has always
    # used -- just issued CONCURRENTLY instead of one-after-another, which was
    # the bulk of the remaining ~10-15s load (five serial network round-trips).
    # googleapiclient's httplib2 transport is NOT thread-safe when a single
    # service object is shared across threads, so each worker builds its own
    # service (static discovery = bundled doc, no extra network round-trip).
    def _read(rng):
        s = _va_sheets_service_st()
        if not s:
            return []
        try:
            return s.spreadsheets().values().get(
                spreadsheetId=LOGIN_LOG_SHEET_ID, range=rng).execute().get("values", [])
        except Exception as e:
            log.warning("member analytics read failed (%s): %s", rng, e)
            return []

    _RANGES = ["%s!A:T" % _MEMBER_TAB, "Visitor Analytics!A:AM", "Page Views!A:M",
               "A1:U5000",             # internal login log -- real names + p2_vid for @position2.com
               "Visitor Identities!A1:G5000"]
    if svc:
        with ThreadPoolExecutor(max_workers=len(_RANGES)) as _ex:
            ms_rows, va_rows, pv_rows, login_rows, vi_rows = list(_ex.map(_read, _RANGES))
    else:
        ms_rows = va_rows = pv_rows = login_rows = vi_rows = []
    ms = ms_rows[1:] if len(ms_rows) > 1 else []
    va = va_rows[1:] if len(va_rows) > 1 else []
    pv = pv_rows[1:] if len(pv_rows) > 1 else []
    login_data = login_rows[1:] if len(login_rows) > 1 else []

    MS = {n: i for i, n in enumerate(_MS_HEADER)}
    VA = {n: i for i, n in enumerate(_VA_HEADER)}
    def mc(r, n, d=""):
        i = MS.get(n, -1); return r[i] if 0 <= i < len(r) else d
    def vc(r, n, d=""):
        i = VA.get(n, -1); return r[i] if 0 <= i < len(r) else d
    def pc(r, i, d=""):
        return r[i] if i < len(r) else d
    def to_int(x):
        try: return int(float(x))
        except Exception: return 0
    def fmt(sec):
        sec = int(sec or 0); mm, ss = divmod(sec, 60); hh, mm = divmod(mm, 60)
        return (("%dh %dm" % (hh, mm)) if hh else ("%dm %ds" % (mm, ss)) if mm else ("%ds" % ss)) if sec else "—"

    va = [r for r in va if vc(r, "Bot", "No") != "Yes"]
    va_by_vid = defaultdict(list)
    for r in va:
        vid = vc(r, "Visitor ID")
        if vid: va_by_vid[vid].append(r)
    idmap = _va_identity_map(vi_rows=vi_rows)

    # Page Views has no name/picture/vid columns, so for members we only see via
    # page-view activity (no Member Signins row yet), backfill name/picture/p2_vid
    # from the internal login log, which Google always populates on every
    # @position2.com sign-in. Scan chronologically so the most recent known value
    # for each field wins (older rows predate the Visitor ID column).
    profile_by_email = {}
    for r in login_data:
        e = (pc(r, 5) or "").lower()
        if not e:
            continue
        prof = profile_by_email.setdefault(e, {"name": "", "picture": "", "vid": ""})
        if pc(r, 6): prof["name"] = pc(r, 6)
        if pc(r, 8): prof["picture"] = pc(r, 8)
        if pc(r, 20): prof["vid"] = pc(r, 20)

    pv_by_email = defaultdict(list)   # post-login page views on the /app member surface
    for r in pv:
        e = (pc(r, 4) or "").lower()
        path = pc(r, 6) or ""
        if e and (not e.endswith("@position2.com") or path.startswith("/app")):
            pv_by_email[e].append(r)

    members = {}
    for r in ms:
        e = (mc(r, "Email") or "").lower()
        if not e:
            continue
        mem = members.get(e)
        ts = mc(r, "Timestamp (IST)")
        if not mem:
            mem = members[e] = {"email": mc(r, "Email"), "name": mc(r, "Full Name"),
                "picture": mc(r, "Profile Picture"), "vids": set(), "signins": 0,
                "first_signin": ts, "last_signin": ts, "device": "", "browser": "",
                "os": "", "ip": mc(r, "IP")}
        mem["signins"] += 1
        vid = mc(r, "Visitor ID")
        if vid: mem["vids"].add(vid)
        if ts and (not mem["first_signin"] or ts < mem["first_signin"]): mem["first_signin"] = ts
        if ts > mem["last_signin"]: mem["last_signin"] = ts
        mem["device"] = mc(r, "Device") or mem["device"]
        mem["browser"] = mc(r, "Browser") or mem["browser"]
        mem["os"] = mc(r, "OS") or mem["os"]
        if mc(r, "IP"): mem["ip"] = mc(r, "IP")

    # Sessions persist for weeks, so plenty of /app usage comes from people who
    # signed in before their Member Signins row existed (or whose session simply
    # hasn't expired since). Surface them from their actual Page Views activity
    # too, instead of only counting people who've freshly re-authenticated.
    for e, rows in pv_by_email.items():
        if e in members:
            continue
        rows = sorted(rows, key=lambda r: pc(r, 0))
        last_row = rows[-1]
        prof = profile_by_email.get(e, {})
        vids = {prof["vid"]} if prof.get("vid") else set()
        members[e] = {"email": e, "name": prof.get("name") or "—", "picture": prof.get("picture") or "",
            "vids": vids, "signins": 1,
            "first_signin": pc(rows[0], 0), "last_signin": pc(last_row, 0),
            "device": pc(last_row, 12), "browser": pc(last_row, 10),
            "os": pc(last_row, 11), "ip": pc(last_row, 9)}

    # Backfill any member still missing a visitor-ID link (e.g. a Member Signins row
    # written before p2_vid capture existed) from the login log's most recent vid.
    for e, mem in members.items():
        if not mem["vids"]:
            prof = profile_by_email.get(e, {})
            if prof.get("vid"):
                mem["vids"] = {prof["vid"]}

    # The per-member reverse-IP lookup below (_ip_company) is one blocking
    # network round-trip per unique IP (IPinfo + reverse DNS + RDAP). Doing them
    # one-at-a-time inside the member loop is exactly what made this page take
    # 30-40s. Warm the cache for every member IP CONCURRENTLY first, so each
    # _ip_company call in the loop is a cache hit -- same fix already applied to
    # the Anonymous Traffic and Visitor Analytics dashboards.
    if _VI_OK:
        _resolve_ips_bulk({mem["ip"] for mem in members.values() if mem.get("ip")})

    out_members = []
    signup_by_day = Counter(); src_counter = Counter(); utm_counter = Counter()
    dev_counter = Counter(); os_counter = Counter(); br_counter = Counter()
    prelogin_pages_counter = Counter(); company_counter = Counter()
    total_pre = 0; linked = 0; pre_eng_total = 0; pre_eng_n = 0

    for e, mem in members.items():
        pre = []
        for vid in mem["vids"]:
            pre.extend(va_by_vid.get(vid, []))
        pre.sort(key=lambda r: vc(r, "Timestamp (IST)"))
        prelogin_pages = len(pre)
        first_seen = vc(pre[0], "Timestamp (IST)") if pre else mem["first_signin"]
        source = "direct"; landing = ""
        if pre:
            f = pre[0]
            source = vc(f, "UTM Source") or (vc(f, "Referrer Host") or "direct")
            landing = vc(f, "Landing Page")
        engaged = sum(to_int(vc(r, "Engaged Time (s)")) for r in pre)
        company = ""
        for vid in mem["vids"]:
            company = (idmap.get(vid) or {}).get("company") or company
        if not company and mem.get("ip"):
            company = _ip_company(mem["ip"]) or ""
        if not company:
            domain = e.rsplit("@", 1)[-1] if "@" in e else ""
            if domain and domain not in _FREE_EMAIL_DOMAINS:
                company = domain
        posts = pv_by_email.get(e, [])
        status = "returning" if mem["signins"] > 1 else "new"
        last_active = mem["last_signin"]
        if posts:
            lp = max(pc(r, 0) for r in posts)
            if lp > last_active: last_active = lp
        post_secs = sum(to_int(pc(r, 7)) for r in posts)
        _tos_h, _tos_r = divmod(post_secs, 3600); _tos_m = _tos_r // 60
        time_on_site = f"{_tos_h}h {_tos_m}m" if _tos_h else (f"{_tos_m}m" if _tos_m else "—")

        tl = []
        for r in pre:
            tl.append({"t": vc(r, "Timestamp (IST)"), "kind": "view",
                       "label": vc(r, "Page Title") or vc(r, "Page URL") or "Page view",
                       "meta": (vc(r, "Referrer Host") or "")})
        tl.append({"t": mem["first_signin"], "kind": "signin", "label": "Signed in with Google", "meta": ""})
        if mem["signins"] > 1:
            tl.append({"t": mem["last_signin"], "kind": "signin",
                       "label": "Returned (%d total sign-ins)" % mem["signins"], "meta": ""})
        for r in posts:
            tl.append({"t": pc(r, 0), "kind": "post",
                       "label": pc(r, 5) or pc(r, 6) or "Page view", "meta": pc(r, 8)})
        tl.sort(key=lambda x: x["t"] or "")
        tl = tl[:60]

        signup_by_day[(mem["first_signin"] or "")[:10]] += 1
        src_counter[source or "direct"] += 1
        if pre and vc(pre[0], "UTM Source"): utm_counter[vc(pre[0], "UTM Source")] += 1
        dev_counter[mem["device"] or "—"] += 1
        os_counter[mem["os"] or "—"] += 1
        br_counter[mem["browser"] or "—"] += 1
        for r in pre:
            prelogin_pages_counter[vc(r, "Page Title") or vc(r, "Page URL")] += 1
        if company: company_counter[company] += 1
        total_pre += prelogin_pages
        if mem["vids"]: linked += 1
        if engaged: pre_eng_total += engaged; pre_eng_n += 1

        out_members.append({
            "email": mem["email"], "name": mem["name"] or "—", "picture": mem["picture"],
            "company": company, "vid": (sorted(mem["vids"])[0][:8] if mem["vids"] else ""),
            "signins": mem["signins"], "prelogin_pages": prelogin_pages, "post_pages": len(posts),
            "first_seen": first_seen, "joined": mem["first_signin"], "last_active": last_active,
            "source": source, "landing": landing, "engaged": fmt(engaged), "time_on_site": time_on_site,
            "device": mem["device"] or "—", "browser": mem["browser"] or "—", "os": mem["os"] or "—",
            "status": status, "linked": bool(mem["vids"]), "timeline": tl,
        })

    out_members.sort(key=lambda x: x["last_active"] or "", reverse=True)

    total_members = len(members)
    total_signins = sum(mem["signins"] for mem in members.values())
    new_members = sum(1 for x in out_members if x["status"] == "new")
    returning_members = total_members - new_members
    unique_visitors = len(va_by_vid)

    # Visitor->member conversion is about the public marketing funnel: @position2.com
    # staff never arrive as anonymous visitors, so they're excluded from this ratio
    # (they still count toward the "Members" KPI above, which intentionally covers
    # all /app usage per the two-tier design).
    external_out = [x for x in out_members if not x["email"].lower().endswith("@position2.com")]
    external_members = len(external_out)
    external_returning = sum(1 for x in external_out if x["status"] == "returning")

    conv_rate = round(external_members / unique_visitors * 100, 1) if unique_visitors else 0
    avg_pre = round(total_pre / linked, 1) if linked else 0
    avg_engaged = fmt(round(pre_eng_total / pre_eng_n) if pre_eng_n else 0)
    signup_series = sorted(signup_by_day.items())[-30:]

    engaged_visitors = 0
    for vid, rows in va_by_vid.items():
        pages = max((to_int(vc(r, "Pages In Session")) for r in rows), default=0)
        eng = max((to_int(vc(r, "Engaged Time (s)")) for r in rows), default=0)
        if pages > 1 or eng >= 15:
            engaged_visitors += 1

    recent = []
    for r in reversed(ms[-150:]):
        recent.append({"ts": mc(r, "Timestamp (IST)"), "email": mc(r, "Email"),
            "name": mc(r, "Full Name"), "vid": (mc(r, "Visitor ID") or "")[:8],
            "device": mc(r, "Device"), "browser": mc(r, "Browser"),
            "ref": mc(r, "Referrer Host") if False else (mc(r, "Referrer") or "direct")})
    # Members surfaced only via Page Views (no Member Signins row) still belong here --
    # use their most recent app activity as the event, since we have no signin event.
    ms_emails = {(mc(r, "Email") or "").lower() for r in ms}
    for e, mem in members.items():
        if e in ms_emails:
            continue
        recent.append({"ts": mem["last_signin"], "email": mem["email"], "name": mem["name"],
            "vid": (sorted(mem["vids"])[0][:8] if mem["vids"] else ""),
            "device": mem["device"], "browser": mem["browser"], "ref": "—"})
    recent.sort(key=lambda x: x["ts"] or "", reverse=True)
    recent = recent[:150]

    # Post-login page views: what members do on /app after signing in (mirrors
    # Internal Usage's "Top pages" + page-views table, but for the member
    # population instead of @position2.com staff). pv_by_email already applies
    # the right population filter (see above): every non-P2 view, plus P2 views
    # specifically on /app.
    post_page_counter = Counter()
    post_page_rows = []
    for e, rows in pv_by_email.items():
        mem_name = (members.get(e) or {}).get("name") or "—"
        for r in rows:
            title = pc(r, 5) or pc(r, 6) or "—"
            post_page_counter[title] += 1
            post_page_rows.append({
                "ts": pc(r, 0), "email": e, "name": mem_name,
                "title": pc(r, 5) or "—", "url": pc(r, 6) or "", "duration": pc(r, 8) or "—",
            })
    post_page_rows.sort(key=lambda x: x["ts"] or "", reverse=True)
    total_post_page_views = len(post_page_rows)
    views_per_member = round(total_post_page_views / total_members, 1) if total_members else 0

    return {
        "configured": bool(svc),
        "kpis": {"members": total_members, "signins": total_signins, "new": new_members,
            "returning": returning_members, "linked": linked, "avg_pre": avg_pre,
            "avg_engaged": avg_engaged, "companies": len(company_counter),
            "visitors": unique_visitors, "conv_rate": conv_rate,
            "page_views": total_post_page_views, "views_per_member": views_per_member},
        "series": signup_series,
        "sources": src_counter.most_common(12), "utm": utm_counter.most_common(10),
        "devices": dev_counter.most_common(), "oses": os_counter.most_common(),
        "browsers": br_counter.most_common(8),
        "prelogin_pages": prelogin_pages_counter.most_common(15),
        "post_pages": post_page_counter.most_common(15),
        "companies": company_counter.most_common(15),
        "funnel": {"visitors": unique_visitors, "engaged": engaged_visitors,
                   "members": external_members, "returning": external_returning},
        "members": out_members[:200], "recent": recent[:100],
        "page_views_table": post_page_rows[:150],
    }


@app.route("/p2/admin/public-page-analytics")
@admin_required
def admin_members():
    """Admin-only analytics for public Google sign-ins (members), synced with
    their pre-login Visitor Analytics journey."""
    return render_template("admin_members.html", user=_get_user())


@app.route("/p2/admin/public-page-analytics/data")
@admin_required
def admin_members_data():
    force = request.args.get("fresh") in ("1", "true", "yes")
    return jsonify(_fetch_member_analytics(force=force))


def _fetch_usage_data(internal: bool = True) -> dict:
    """Fetch login + page view data from Sheets. Shared by shell and data endpoints.

    internal=True  -> Internal Usage: @position2.com staff only.
    internal=False -> External Usage: everyone signing in with a non-@position2.com
                      email (leads, prospects, client users). Same rich per-user
                      journey PLUS agent-run activity and email-domain (company)
                      breakdowns, so we see who these people are, when they first
                      and last showed up, and what they actually did on the platform."""
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

    # Read the FULL tabs — Sheets appends new rows at the bottom, so a fixed
    # top cap (was A1:T1000 / Page Views!A1:M2000) silently dropped every row
    # past the cap, i.e. all the most-recent activity once the sheet grew.
    # A:U (not A:T) so column U -- the p2_vid visitor ID -- is actually read;
    # it's been written on every login since v17 but was silently dropped here.
    # Login source depends on mode. @position2.com staff sign-ins land in the main
    # Login Log; PUBLIC (non-P2) sign-ins are recorded ONLY in the 'Member Signins'
    # tab (see _log_member_signin) — _log_login_to_sheet is never called for them.
    # So External Usage must read Member Signins or it would show almost nobody.
    # The two tabs put browser/os/device/visitor-id in different columns; LC maps them.
    if internal:
        login_rows = _fetch("A:U")
        LC = {"br": 10, "os": 12, "dev": 13, "vid": 20}
    else:
        login_rows = _fetch("%s!A:T" % _MEMBER_TAB)
        LC = {"br": 11, "os": 13, "dev": 14, "vid": 9}
    page_rows  = _fetch("Page Views!A:N")
    va_rows    = _fetch("Visitor Analytics!A:AM")
    login_data = login_rows[1:] if len(login_rows) > 1 else []
    page_data  = page_rows[1:]  if len(page_rows)  > 1 else []
    va_data    = va_rows[1:]    if len(va_rows)    > 1 else []

    # Internal Usage keeps @position2.com only; External Usage keeps everyone
    # else (any real non-P2 email). One predicate, inverted by mode.
    def _is_p2(e): return (e or "").lower().endswith("@position2.com")
    def keep(e):
        e = (e or "").strip()
        if not e:
            return False
        return _is_p2(e) if internal else (not _is_p2(e))
    login_data = [r for r in login_data if keep(col(r, 5))]
    page_data  = [r for r in page_data  if keep(col(r, 4))]

    from collections import Counter, defaultdict

    # Pre-login journey, by p2_vid -- the same visitor cookie captured on the
    # public marketing site before a staff member ever signs in. Lets us show
    # "this admin browsed the site, then signed in, then did X" end to end.
    _VA_IDX = {n: i for i, n in enumerate(_VA_HEADER)}
    def vc(r, name, d=""):
        i = _VA_IDX.get(name, -1)
        return r[i] if 0 <= i < len(r) else d
    va_by_vid = defaultdict(list)
    for r in va_data:
        if vc(r, "Bot", "No") == "Yes":
            continue
        v = vc(r, "Visitor ID")
        if v:
            va_by_vid[v].append(r)

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
    browser_counts = Counter(col(r, LC["br"]) for r in login_data if col(r, LC["br"]))
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
    pv_by_email = defaultdict(list)
    for r in page_data:
        e = col(r, 4)
        if e: pv_by_email[e].append(r)
    for r in login_data:
        e = col(r, 5)
        if not e: continue
        if e not in user_map:
            user_map[e] = {"email": e, "name": col(r, 6), "logins": 0,
                           "last_seen": col(r, 0), "first_login": col(r, 0),
                           "total_secs": 0, "vids": set(), "login_rows": [],
                           "browser": "", "os": "", "device": "", "page_views": 0}
        user_map[e]["logins"] += 1
        user_map[e]["last_seen"] = col(r, 0)   # rows are oldest→newest; last row = most recent
        user_map[e]["login_rows"].append(r)
        v = col(r, LC["vid"])
        if v: user_map[e]["vids"].add(v)
        # Latest login wins (rows oldest→newest) for the person's device fingerprint.
        _br, _os, _dev = col(r, LC["br"]), col(r, LC["os"]), col(r, LC["dev"])
        if _br: user_map[e]["browser"] = _br
        if _os: user_map[e]["os"] = _os
        if _dev: user_map[e]["device"] = _dev
    for r in page_data:
        e = col(r, 4)
        if e in user_map and col(r, 7).isdigit():
            user_map[e]["total_secs"] += int(col(r, 7))

    total_pre_pages = 0; linked_users = 0
    for u in user_map.values():
        s = u["total_secs"]; uh, ur = divmod(s, 3600); um = ur // 60
        u["time_fmt"] = f"{uh}h {um}m" if uh else (f"{um}m" if um else "—")

        pre = []
        for vid in u["vids"]:
            pre.extend(va_by_vid.get(vid, []))
        pre.sort(key=lambda r: vc(r, "Timestamp (IST)"))
        u["vid"] = (sorted(u["vids"])[0][:8] if u["vids"] else "")
        u["prelogin_pages"] = len(pre)
        u["linked"] = bool(pre)
        u["first_seen"] = vc(pre[0], "Timestamp (IST)") if pre else u["first_login"]
        u["source"] = (vc(pre[0], "UTM Source") or vc(pre[0], "Referrer Host") or "direct") if pre else ""
        # Company (from email domain) + last-active across every signal we have.
        u["domain"] = (u["email"].split("@", 1)[1].lower() if "@" in u["email"] else "")
        _pv = pv_by_email.get(u["email"], [])
        u["page_views"] = len(_pv)
        # Device fingerprint fallback from page views if logins didn't carry it.
        if _pv:
            if not u.get("browser"): u["browser"] = col(_pv[-1], 10)
            if not u.get("os"):      u["os"] = col(_pv[-1], 11)
            if not u.get("device"):  u["device"] = col(_pv[-1], 12)
        _last_pv = max((col(r, 0) for r in pv_by_email.get(u["email"], [])), default="")
        u["last_active"] = max([x for x in (u.get("last_seen", ""), _last_pv) if x] or [""])
        u.setdefault("agent_runs", 0)
        u.setdefault("agents", [])
        u.setdefault("last_run", "")

        tl = []
        for r in pre:
            tl.append({"t": vc(r, "Timestamp (IST)"), "kind": "view",
                       "label": vc(r, "Page Title") or vc(r, "Page URL") or "Page view",
                       "meta": vc(r, "Referrer Host") or ""})
        login_rows_sorted = sorted(u["login_rows"], key=lambda r: col(r, 0))
        for i, r in enumerate(login_rows_sorted):
            tl.append({"t": col(r, 0), "kind": "signin",
                       "label": "Signed in with Google" if i == 0 else "Returned (login #%d)" % (i+1),
                       "meta": ""})
        for r in pv_by_email.get(u["email"], []):
            tl.append({"t": col(r, 0), "kind": "post",
                       "label": col(r, 5) or col(r, 6) or "Page view", "meta": col(r, 8)})
        tl.sort(key=lambda x: x["t"] or "")
        u["timeline"] = tl[:80]
        del u["login_rows"], u["vids"]

        total_pre_pages += u["prelogin_pages"]
        if u["linked"]: linked_users += 1

    avg_pre_pages = round(total_pre_pages/linked_users, 1) if linked_users else 0

    # ── External-only enrichment: agent runs + company (email domain) ──────────
    # For External Usage we also join the 'Agent Runs' tab so each external person
    # carries what they actually ran, and we surface company-level (email-domain)
    # rollups. Skipped entirely for Internal Usage (extra Sheets read avoided).
    agent_runs_total = 0; agent_users = 0
    agent_breakdown = []; agent_runs_table = []; domain_breakdown = []
    if not internal:
        agent_meta = {a["slug"]: a for a in APP_AGENTS}
        ar_rows = _fetch("%s!A:F" % _AR_TAB)
        ar_rows = ar_rows[1:] if len(ar_rows) > 1 else []
        um_by_lower = {k.lower(): v for k, v in user_map.items()}
        ar_by_email: dict = {}
        agent_totals: dict = {}
        # (email, agent_slug) -> FIFO queue of saved-run titles from Postgres.
        # Both logs are append-ordered, so popping one per matching Sheets row
        # below pairs each run with what it actually was (see _list_agent_run_titles).
        title_queue: dict = defaultdict(list)
        for h in _list_agent_run_titles():
            he = (h["email"] or "").lower()
            if keep(he):
                title_queue[(he, _canonical_agent_slug(h["slug"] or ""))].append(h["title"] or "")
        for r in ar_rows:
            e = col(r, 2)
            if not keep(e):        # external only, non-empty
                continue
            el = e.lower()
            slug = _canonical_agent_slug(col(r, 4) or "?")
            ts = col(r, 0)
            aname = col(r, 5) or agent_meta.get(slug, {}).get("name", slug)
            q = title_queue.get((el, slug))
            detail = q.pop(0) if q else ""
            d = ar_by_email.setdefault(el, {"email": e, "name": col(r, 3),
                                            "total": 0, "agents": {}, "last_run": "", "events": []})
            d["total"] += 1
            d["agents"][slug] = d["agents"].get(slug, 0) + 1
            if ts > d["last_run"]:
                d["last_run"] = ts
            label = "Ran " + aname + (": " + detail if detail else "")
            d["events"].append({"t": ts, "kind": "run", "label": label, "meta": ""})
            agent_totals[slug] = agent_totals.get(slug, 0) + 1
            agent_runs_table.append({"ts": ts, "email": e, "name": col(r, 3), "agent": aname, "detail": detail})
        agent_runs_total = sum(agent_totals.values())
        agent_users = len(ar_by_email)
        agent_breakdown = sorted(([agent_meta.get(s, {}).get("name", s), c]
                                  for s, c in agent_totals.items()), key=lambda x: -x[1])
        agent_runs_table.reverse()   # rows are oldest→newest → show newest first

        for el, d in ar_by_email.items():
            u = um_by_lower.get(el)
            if not u:                # ran an agent but no login row captured → synthesize
                u = {"email": d["email"], "name": d["name"] or d["email"], "logins": 0,
                     "last_seen": "", "first_login": "", "total_secs": 0, "time_fmt": "—",
                     "prelogin_pages": 0, "linked": False, "first_seen": "", "source": "",
                     "timeline": [], "domain": (el.split("@", 1)[1] if "@" in el else ""),
                     "last_active": "", "browser": "", "os": "", "device": "", "page_views": 0}
                user_map[el] = u
                um_by_lower[el] = u
            alist = []
            for s, c in sorted(d["agents"].items(), key=lambda x: -x[1]):
                m = agent_meta.get(s, {})
                alist.append({"slug": s, "name": m.get("name", s), "count": c,
                              "ac": m.get("ac", "#8b5cf6"), "ac2": m.get("ac2", "#22d3ee")})
            u["agent_runs"] = d["total"]
            u["agents"] = alist
            u["last_run"] = d["last_run"]
            if d["last_run"] and d["last_run"] > (u.get("last_active") or ""):
                u["last_active"] = d["last_run"]
            if not u.get("first_seen"):
                u["first_seen"] = min([e["t"] for e in d["events"] if e["t"]] or [""])
            tl = (u.get("timeline") or []) + d["events"]
            tl.sort(key=lambda x: x.get("t") or "")
            u["timeline"] = tl[:120]

        dom = Counter(u["domain"] for u in user_map.values() if u.get("domain"))
        domain_breakdown = dom.most_common(12)

    if not internal:
        user_activity = sorted(user_map.values(),
                               key=lambda x: (x.get("agent_runs", 0), x.get("logins", 0),
                                              x.get("last_active", "")), reverse=True)
    else:
        user_activity = sorted(user_map.values(), key=lambda x: x["logins"], reverse=True)

    unique_users = len(user_map)   # external adds run-only users → recount

    # Full tables, newest first — no cap (return every login and page view).
    login_table = [{"ts": col(r,0), "email": col(r,5), "name": col(r,6),
                    "browser": col(r,LC["br"]), "os": col(r,LC["os"]), "device": col(r,LC["dev"])}
                   for r in reversed(login_data)]
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
                views_per_user=views_per_user,
                linked_users=linked_users, avg_pre_pages=avg_pre_pages,
                agent_runs_total=agent_runs_total, agent_users=agent_users,
                agent_breakdown=agent_breakdown, agent_runs_table=agent_runs_table,
                domain_breakdown=domain_breakdown)



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


@app.route("/p2/admin/internal-usage")
@admin_required
def admin_usage():
    """Shell page — renders instantly, JS fetches /admin/internal-usage/data async."""
    return render_template("admin_usage.html", user=_get_user())


@app.route("/p2/admin/internal-usage/data")
@admin_required
def admin_usage_data():
    """JSON data endpoint called by the admin usage shell page."""
    data = _fetch_usage_data()
    return jsonify(data)

@app.route("/p2/admin/external-usage")
@admin_required
def admin_external_usage():
    """Shell page — everyone signing in with a non-@position2.com email. JS fetches
    /admin/external-usage/data async."""
    return render_template("admin_external_usage.html", user=_get_user())

@app.route("/p2/admin/external-usage/data")
@admin_required
def admin_external_usage_data():
    """JSON data endpoint for the External Usage dashboard (non-P2 sign-ins,
    enriched with agent runs and company/email-domain rollups)."""
    return jsonify(_fetch_usage_data(internal=False))

# ─────────────────────────────────────────────────────────────────────────────
# AI PERSON READ (OpenAI) -- the one-glance summary on the External Usage modal
# ─────────────────────────────────────────────────────────────────────────────
# Everything the dashboard knows about one person (identity, Apollo profile,
# where they came from, pages, agent runs, agent access requests) is compressed
# into a strict-JSON verdict: a headline, two sentences, an intent rating and a
# suggested next step. Reuses Vimi's model chain and OpenAI key, no second
# integration. Cached in Postgres keyed by a FINGERPRINT of the input facts, so
# a summary is regenerated only when that person's data actually changes, not
# once per page load.

_PS_TABLE_READY = False
_PS_MEM: dict = {}          # email -> (expiry, payload)
_PS_MEM_TTL = 900
# Folded into the cache fingerprint, so changing the prompt regenerates every
# stored summary instead of serving reads written under the old instructions.
# v2 drops the suggested next step.
_PS_PROMPT_VERSION = 2
_AAR_SUMMARY_CACHE = {"t": 0.0, "rows": []}
_AAR_SUMMARY_TTL = 180      # only for the summary path; _agent_access_requested_slugs
                            # deliberately stays uncached so "Request sent" is instant


def _ensure_person_summary_table(conn) -> None:
    global _PS_TABLE_READY
    if _PS_TABLE_READY:
        return
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS person_summary (
                email TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                payload JSONB NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
    conn.commit()
    _PS_TABLE_READY = True


def _ps_fingerprint(facts: dict) -> str:
    """Stable hash of the facts we are about to summarize. Any change to the
    person's activity or profile changes this, which is what invalidates the
    cached summary without needing a TTL. The prompt version is folded in so that
    editing what we ask for also regenerates every stored summary."""
    import hashlib
    blob = json.dumps({"v": _PS_PROMPT_VERSION, "f": facts},
                      sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _ps_cache_get(email: str, fingerprint: str):
    """Cached summary for this exact fingerprint, else None."""
    hit = _PS_MEM.get(email)
    if hit and hit[0] > time.time() and hit[1].get("_fp") == fingerprint:
        return hit[1]
    conn = _pg_conn()
    if not conn:
        return None
    try:
        _ensure_person_summary_table(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT payload, fingerprint FROM person_summary WHERE email = %s",
                        (email,))
            row = cur.fetchone()
        if row and row[1] == fingerprint:
            payload = row[0] or {}
            payload["_fp"] = fingerprint
            _PS_MEM[email] = (time.time() + _PS_MEM_TTL, payload)
            return payload
        return None
    except Exception as e:
        log.warning("person summary cache read failed: %s", e)
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _ps_cache_put(email: str, fingerprint: str, payload: dict) -> None:
    _PS_MEM[email] = (time.time() + _PS_MEM_TTL, dict(payload, _fp=fingerprint))
    conn = _pg_conn()
    if not conn:
        return
    try:
        from psycopg2.extras import Json
        _ensure_person_summary_table(conn)
        clean = {k: v for k, v in payload.items() if k != "_fp"}
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO person_summary (email, fingerprint, payload, updated_at) "
                "VALUES (%s, %s, %s, now()) ON CONFLICT (email) DO UPDATE SET "
                "fingerprint = EXCLUDED.fingerprint, payload = EXCLUDED.payload, updated_at = now()",
                (email, fingerprint, Json(clean)))
        conn.commit()
    except Exception as e:
        log.warning("person summary cache write failed: %s", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _aar_for_summary() -> list:
    """Agent access requests with a short TTL cache. Read on the summary path
    only, where a few minutes of staleness is irrelevant."""
    now = time.time()
    if _AAR_SUMMARY_CACHE["rows"] and (now - _AAR_SUMMARY_CACHE["t"]) < _AAR_SUMMARY_TTL:
        return _AAR_SUMMARY_CACHE["rows"]
    rows = _agent_access_requests_raw()
    _AAR_SUMMARY_CACHE["rows"] = rows
    _AAR_SUMMARY_CACHE["t"] = now
    return rows


def _ps_pages_from_timeline(timeline: list) -> tuple:
    """(pre_login_pages, after_login_pages, ran_labels) as deduped title lists,
    most recent first. This is the 'which pages did they visit' input."""
    pre, post, runs = [], [], []
    for e in reversed(timeline or []):
        label = str(e.get("label") or "").strip()
        if not label:
            continue
        kind = e.get("kind")
        bucket = pre if kind == "view" else (post if kind == "post" else (runs if kind == "run" else None))
        if bucket is None or label in bucket:
            continue
        if len(bucket) < 12:
            bucket.append(label)
    return pre, post, runs


def _person_summary_facts(email: str, activity: dict, profile: dict) -> dict:
    """Compact, whitelisted fact sheet for one person. Only known keys are read
    out of `activity` (which may arrive from the browser), and every string is
    length-capped, so nothing unbounded or unexpected reaches the prompt."""
    def s(v, n=180):
        return str(v or "")[:n]

    pre, post, runs = _ps_pages_from_timeline(activity.get("timeline") or [])
    co = (profile.get("company") or {}) if profile else {}
    el = email.lower()

    requested = []
    for r in _aar_for_summary():
        if (r.get("email") or "").lower() == el:
            requested.append({"agent": s(r.get("agent_name") or r.get("slug"), 60),
                              "reason": s(r.get("message"), 220)})
        if len(requested) >= 8:
            break

    form = {}
    try:
        for r in _read_access_requests():
            if (r.get("email") or "").lower() == el:
                form = {"company": s(r.get("company"), 80), "interest": s(r.get("interest"), 120),
                        "message": s(r.get("message"), 300)}
                break
    except Exception:
        form = {}

    facts = {
        "identity": {
            "name": s(activity.get("name"), 80),
            "email": el,
            "email_domain": s(activity.get("domain"), 80),
            "title": s(profile.get("title") if profile else "", 120),
            "seniority": s(profile.get("seniority") if profile else "", 40),
            "linkedin_headline": s(profile.get("headline") if profile else "", 220),
            "location": s(profile.get("location") if profile else "", 100),
            "company": s(co.get("name"), 100),
            "company_industry": s(co.get("industry"), 80),
            "company_employees": co.get("employees") or "",
            "company_revenue": s(co.get("revenue"), 40),
            "already_in_crm": bool((profile.get("crm") or {}).get("in_crm")) if profile else False,
            "enriched": bool(profile.get("matched")) if profile else False,
        },
        "acquisition": {
            "first_seen": s(activity.get("first_seen"), 40),
            "first_touch_source": s(activity.get("source"), 80) or "direct",
            "pages_before_signup": activity.get("prelogin_pages") or 0,
            "pages_viewed_before_signup": pre,
        },
        "engagement": {
            "logins": activity.get("logins") or 0,
            "page_views": activity.get("page_views") or 0,
            "time_on_site": s(activity.get("time_fmt"), 20),
            "last_active": s(activity.get("last_active") or activity.get("last_seen"), 40),
            "pages_viewed_after_login": post,
            "device": s(" ".join(x for x in [activity.get("browser"), activity.get("os"),
                                             activity.get("device")] if x), 80),
        },
        "agents": {
            "runs_total": activity.get("agent_runs") or 0,
            "agents_run": [{"name": s(a.get("name"), 60), "count": a.get("count") or 0}
                           for a in (activity.get("agents") or [])[:8]],
            "what_they_ran": runs,
            "agents_requested_access_to": requested,
        },
        "request_form": form,
    }
    return facts


_PS_SYSTEM = (
    "You are a B2B revenue-intelligence analyst for Position2, a B2B digital marketing "
    "agency. You are given every fact a first-party analytics platform holds about ONE "
    "person who signed in to it: who they are, how they arrived, what they read, which AI "
    "agents they ran, and which agents they asked for access to.\n\n"
    "Write a verdict a salesperson can act on in five seconds.\n\n"
    "Rules:\n"
    "1. Use ONLY the supplied facts. Never invent a title, company, intent or motive. If "
    "identity is unknown, say so plainly and reason from behaviour instead.\n"
    "2. Be specific over generic. Name the actual pages, agents and sources that matter. "
    "Skip anything that carries no signal.\n"
    "3. Insight, not restatement. Do not simply list the numbers back; say what the "
    "pattern means (evaluating, researching a specific need, tyre-kicking, already a "
    "client, a competitor looking around, an agency peer, and so on).\n"
    "4. Never use an em dash. Use commas, colons or periods.\n"
    "5. Plain professional English. No marketing adjectives, no hedging padding.\n"
    "6. Describe and interpret only. Do NOT recommend an action, pitch, demo, email or "
    "follow-up of any kind, and do not end with advice. The reader decides what to do.\n\n"
    "Return STRICT JSON only, with these keys:\n"
    '{"headline": "at most 7 words, the person in a nutshell",\n'
    ' "summary": "at most 2 sentences, at most 42 words total: who they are, where they '
    'came from, what they actually did, what they seem to want",\n'
    ' "intent": "high" | "medium" | "low"}'
)


def _person_ai_summary(email: str, facts: dict) -> dict:
    """{headline, summary, intent, model} for one person, or {} when OpenAI is not
    configured or the call fails. Cached by fact fingerprint."""
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return {}
    fp = _ps_fingerprint(facts)
    cached = _ps_cache_get(email, fp)
    if cached:
        return cached
    try:
        from openai import OpenAI
        oai = OpenAI(api_key=api_key, timeout=45.0, max_retries=1)
        raw, model = _vimi_chat_json(oai, [
            {"role": "system", "content": _PS_SYSTEM},
            {"role": "user", "content": json.dumps(facts, default=str)[:12000]},
        ], 420)
        data = json.loads(raw)
    except Exception as e:
        log.warning("person summary generation failed for %s: %s", email, e)
        return {}

    intent = str(data.get("intent") or "").strip().lower()
    if intent not in ("high", "medium", "low"):
        intent = ""
    out = {
        "headline": str(data.get("headline") or "").strip()[:90],
        # Belt and braces on house style: the model is told not to use em dashes,
        # this guarantees it even if a model ignores the instruction.
        "summary": str(data.get("summary") or "").strip().replace("—", ",")[:600],
        "intent": intent,
        "model": model,
    }
    if not out["summary"]:
        return {}
    _ps_cache_put(email, fp, out)
    return out


def _xl_people_columns():
    """(header, extractor) pairs for the workbook's People sheet. `u` is one row
    from _fetch_usage_data's user_activity, `p` that person's Apollo profile."""
    def co(p, k, d=""):
        return ((p.get("company") or {}).get(k) or d)
    def pct(v):
        try:
            return round(float(v) * 100, 1)
        except (TypeError, ValueError):
            return ""
    return [
        # Identity + first-party activity (always present)
        ("Name",                 lambda u, p: u.get("name") or ""),
        ("Email",                lambda u, p: u.get("email") or ""),
        ("Email domain",         lambda u, p: u.get("domain") or ""),
        # AI read (see _person_ai_summary); blank when OpenAI is not configured
        ("AI headline",          lambda u, p: (u.get("_ai") or {}).get("headline") or ""),
        ("AI intent",            lambda u, p: ((u.get("_ai") or {}).get("intent") or "").title()),
        ("AI summary",           lambda u, p: (u.get("_ai") or {}).get("summary") or ""),
        ("Logins",               lambda u, p: u.get("logins") or 0),
        ("Agent runs",           lambda u, p: u.get("agent_runs") or 0),
        ("Page views",           lambda u, p: u.get("page_views") or 0),
        ("Time on site",         lambda u, p: u.get("time_fmt") or ""),
        ("Pre-login pages",      lambda u, p: u.get("prelogin_pages") or 0),
        ("Linked to pre-login",  lambda u, p: "Yes" if u.get("linked") else "No"),
        ("First seen",           lambda u, p: u.get("first_seen") or ""),
        ("Last active",          lambda u, p: u.get("last_active") or u.get("last_seen") or ""),
        ("Browser",              lambda u, p: u.get("browser") or ""),
        ("OS",                   lambda u, p: u.get("os") or ""),
        ("Device",               lambda u, p: u.get("device") or ""),
        ("First-touch source",   lambda u, p: u.get("source") or ""),
        ("Agents used",          lambda u, p: ", ".join(
            "%s (%d)" % (a.get("name", ""), a.get("count", 0)) for a in (u.get("agents") or []))),
        # Apollo enrichment
        ("Enrichment",           lambda u, p: ("Matched" if p.get("matched")
                                               else ("Not run" if p.get("pending") else "No match"))),
        # Contact details. Phones exist only for people already in the team's
        # Apollo or CRM, so a blank here means Apollo holds no number for us.
        ("Phone",                lambda u, p: next(
            ("%s (%s)" % (ph.get("number"), ph.get("label"))
             for ph in (p.get("phones") or []) if ph.get("owner") == "person"), "")),
        ("All phones",           lambda u, p: " | ".join(
            "%s (%s)" % (ph.get("number"), ph.get("label")) for ph in (p.get("phones") or []))),
        ("Other emails",         lambda u, p: ", ".join(
            e.get("email") for e in (p.get("emails") or []) if not e.get("primary"))),
        ("Email status",         lambda u, p: next(
            (e.get("status") for e in (p.get("emails") or []) if e.get("primary")), "")),
        ("Title",                lambda u, p: p.get("title") or ""),
        ("Seniority",            lambda u, p: p.get("seniority") or ""),
        ("Departments",          lambda u, p: ", ".join(p.get("departments") or [])),
        ("Functions",            lambda u, p: ", ".join(p.get("functions") or [])),
        ("Headline",             lambda u, p: p.get("headline") or ""),
        ("Person location",      lambda u, p: p.get("location") or ""),
        ("Time zone",            lambda u, p: p.get("time_zone") or ""),
        ("LinkedIn",             lambda u, p: p.get("linkedin") or ""),
        ("Twitter",              lambda u, p: p.get("twitter") or ""),
        ("Company",              lambda u, p: co(p, "name")),
        ("Company domain",       lambda u, p: co(p, "domain")),
        ("Industry",             lambda u, p: co(p, "industry")),
        ("Employees",            lambda u, p: co(p, "employees", "")),
        ("Revenue",              lambda u, p: co(p, "revenue")),
        ("Founded",              lambda u, p: co(p, "founded", "")),
        ("Company HQ",           lambda u, p: co(p, "hq")),
        ("Company phone",        lambda u, p: co(p, "phone")),
        ("Company website",      lambda u, p: co(p, "website")),
        ("Company LinkedIn",     lambda u, p: co(p, "linkedin")),
        ("Headcount growth 6mo %",  lambda u, p: pct(co(p, "growth6", None))),
        ("Headcount growth 12mo %", lambda u, p: pct(co(p, "growth12", None))),
        ("Headcount growth 24mo %", lambda u, p: pct(co(p, "growth24", None))),
        ("Company keywords",     lambda u, p: ", ".join(co(p, "keywords", []) or [])),
        ("Current role since",   lambda u, p: next((h.get("start", "") for h in (p.get("history") or [])
                                                    if h.get("current")), "")),
        ("Past roles",           lambda u, p: " | ".join(
            "%s at %s (%s to %s)" % (h.get("title", ""), h.get("org", ""),
                                     h.get("start", "") or "?", h.get("end", "") or "?")
            for h in (p.get("history") or []) if not h.get("current"))),
        ("In CRM",               lambda u, p: "Yes" if (p.get("crm") or {}).get("in_crm") else "No"),
        ("CRM record",           lambda u, p: (p.get("crm") or {}).get("url") or ""),
    ]


def _export_external_usage_xlsx() -> bytes:
    """Everything this dashboard knows, as a 4-sheet .xlsx.

    Sheets: People (one row per person, activity + full Apollo profile), Activity
    timeline (every tracked event for every person), Agent runs, and Summary.
    openpyxl is already a dependency (requirements.txt). Profiles come from the
    enrichment cache, so exporting does not re-spend Apollo credits on anyone
    already resolved."""
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter

    d = _fetch_usage_data(internal=False)
    users = d.get("user_activity") or []
    profiles = _enrich_people([u.get("email", "") for u in users])

    # AI read for every person, concurrently. Cached by fact fingerprint, so a
    # repeat export of unchanged people costs nothing. Capped so one click can
    # never fan out into an unbounded number of model calls.
    if os.environ.get("OPENAI_API_KEY", ""):
        from concurrent.futures import ThreadPoolExecutor
        def _one_summary(u):
            em = (u.get("email") or "").lower()
            try:
                facts = _person_summary_facts(em, u, profiles.get(em) or {})
                u["_ai"] = _person_ai_summary(em, facts)
            except Exception as e:
                log.warning("export summary failed for %s: %s", em, e)
                u["_ai"] = {}
        with ThreadPoolExecutor(max_workers=6) as ex:
            list(ex.map(_one_summary, users[:60]))

    wb = Workbook()
    head_font = Font(bold=True, color="FFFFFF", size=10)
    head_fill = PatternFill("solid", fgColor="1E293B")
    head_align = Alignment(vertical="center", wrap_text=False)

    def _finish(ws, ncols, nrows, widths=None):
        """Header styling + freeze panes + autofilter + sane column widths."""
        for c in range(1, ncols + 1):
            cell = ws.cell(row=1, column=c)
            cell.font = head_font
            cell.fill = head_fill
            cell.alignment = head_align
            w = (widths or {}).get(c)
            if not w:
                longest = len(str(cell.value or ""))
                for r in range(2, min(nrows + 2, 120)):
                    longest = max(longest, len(str(ws.cell(row=r, column=c).value or "")))
                w = min(max(longest + 2, 10), 52)
            ws.column_dimensions[get_column_letter(c)].width = w
        ws.freeze_panes = "A2"
        if nrows:
            ws.auto_filter.ref = "A1:%s%d" % (get_column_letter(ncols), nrows + 1)

    # ── People ────────────────────────────────────────────────────────────────
    cols = _xl_people_columns()
    ws = wb.active
    ws.title = "People"
    ws.append([h for h, _ in cols])
    for u in users:
        p = profiles.get((u.get("email") or "").lower()) or {}
        ws.append([fn(u, p) for _, fn in cols])
    _finish(ws, len(cols), len(users))

    # ── Activity timeline ─────────────────────────────────────────────────────
    KIND = {"view": "Pre-login page view", "signin": "Sign-in",
            "post": "Page view (after login)", "run": "Agent run"}
    ws2 = wb.create_sheet("Activity timeline")
    ws2.append(["Name", "Email", "Timestamp", "Event type", "What", "Detail"])
    n = 0
    for u in users:
        for e in (u.get("timeline") or []):
            ws2.append([u.get("name") or "", u.get("email") or "", e.get("t") or "",
                        KIND.get(e.get("kind"), e.get("kind") or ""),
                        e.get("label") or "", e.get("meta") or ""])
            n += 1
    _finish(ws2, 6, n, {1: 22, 2: 34, 3: 24, 4: 24, 5: 52, 6: 26})

    # ── Agent runs ────────────────────────────────────────────────────────────
    runs = d.get("agent_runs_table") or []
    ws3 = wb.create_sheet("Agent runs")
    ws3.append(["Timestamp", "Name", "Email", "Agent", "What they ran"])
    for r in runs:
        ws3.append([r.get("ts") or "", r.get("name") or "", r.get("email") or "",
                    r.get("agent") or "", r.get("detail") or ""])
    _finish(ws3, 5, len(runs), {1: 24, 2: 22, 3: 34, 4: 26, 5: 52})

    # ── Summary ───────────────────────────────────────────────────────────────
    matched = sum(1 for p in profiles.values() if p.get("matched"))
    pending = sum(1 for p in profiles.values() if p.get("pending"))
    ws4 = wb.create_sheet("Summary")
    ws4.append(["Metric", "Value"])
    for k, v in [
        ("Exported at (IST)", datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")),
        ("External people", len(users)),
        ("Total logins", d.get("total_logins") or 0),
        ("Total page views", d.get("total_page_views") or 0),
        ("Total time on site", d.get("total_time_fmt") or ""),
        ("Total agent runs", d.get("agent_runs_total") or 0),
        ("People who ran an agent", d.get("agent_users") or 0),
        ("Linked to pre-login", d.get("linked_users") or 0),
        ("Apollo enrichment configured", "Yes" if os.environ.get("APOLLO_API_KEY", "") else "No"),
        ("AI summaries configured", "Yes" if os.environ.get("OPENAI_API_KEY", "") else "No"),
        ("AI summaries written", sum(1 for u in users if (u.get("_ai") or {}).get("summary"))),
        ("Profiles matched", matched),
        ("Profiles with no Apollo match", len(profiles) - matched - pending),
        ("Profiles not yet enriched", pending),
    ]:
        ws4.append([k, v])
    _finish(ws4, 2, 14, {1: 34, 2: 26})

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


@app.route("/p2/admin/external-usage/enrich", methods=["POST"])
@admin_required
def admin_external_usage_enrich():
    """Resolve a batch of emails to full Apollo profiles for the person modal.

    POST (not GET) on purpose: the emails are personal data and must not end up
    in a URL query string, a server access log or browser history. Capped at 60
    emails per call so one request can never fan out into an unbounded number of
    Apollo lookups. Always 200 with a `profiles` map -- an unresolvable email
    comes back as {matched:false} rather than an error, and `apollo:false` tells
    the UI that no key is configured at all (so it can say so plainly instead of
    showing every person as unmatched)."""
    body = request.get_json(silent=True) or {}
    emails = [e for e in (body.get("emails") or []) if isinstance(e, str) and "@" in e][:60]
    profiles = _enrich_people(emails, force=bool(body.get("force")))
    return jsonify({"profiles": profiles,
                    "apollo": bool(os.environ.get("APOLLO_API_KEY", ""))})

def _apollo_selftest() -> dict:
    """Prove the Apollo integration end to end and report exactly where it fails.

    Probes with Apollo's own public API example contact rather than anybody on
    the dashboard, so this never puts a real person's email in a URL, a log or a
    support paste, and the verdict is unambiguous: if the probe resolves, the
    key, the scope, the base URL and the response shape are all good, and any
    "no match" on the dashboard is a genuine absence of data rather than a
    broken integration. Costs one Apollo credit per run."""
    key = os.environ.get("APOLLO_API_KEY", "")
    out = {"configured": bool(key), "key_len": len(key), "probe": "tim@apollo.io",
           "base_url": None, "http_ok": False, "shape_ok": False,
           "probe_matched": False, "error": ""}
    if not key:
        out["error"] = "APOLLO_API_KEY is not set on this environment."
        return out
    try:
        from tracker import apollo_client as _ac
        data = _ac._post("people/bulk_match", {"details": [{"email": "tim@apollo.io"}]}, key) or {}
        out["base_url"] = _ac._BASE_OK or "unknown"
        out["http_ok"] = True
        matches = data.get("matches")
        out["shape_ok"] = isinstance(matches, list)
        if not out["shape_ok"]:
            out["response_keys"] = sorted(list(data.keys()))[:10]
            out["error"] = ("Apollo returned HTTP 200 but not a bulk_match body. "
                            "Usually the API key lacks the api/v1/people/bulk_match scope. "
                            + str(data.get("error") or data.get("message") or "")[:200])
            return out
        out["probe_matched"] = bool(matches and matches[0])
        if not out["probe_matched"]:
            out["error"] = ("The call succeeded but even Apollo's own example contact did "
                            "not resolve, which points at a plan or permission limit rather "
                            "than at this platform.")
    except Exception as e:
        out["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
    return out


@app.route("/p2/admin/external-usage/apollo-check", methods=["POST"])
@admin_required
def admin_external_usage_apollo_check():
    """Run the Apollo self-test (see _apollo_selftest). POST so it is never
    triggered by a crawler or a prefetch, since it spends one credit."""
    return jsonify(_apollo_selftest())


@app.route("/p2/admin/external-usage/summary", methods=["POST"])
@admin_required
def admin_external_usage_summary():
    """AI read for one person, for the profile modal.

    The caller sends that person's activity row, which it already has from
    /external-usage/data, rather than the server re-reading the whole Sheet for
    a single modal open (that read takes seconds). Only whitelisted keys are
    used from it (see _person_summary_facts). Always 200: `summary` is {} when
    OpenAI is not configured or the model call failed, and the UI says so."""
    body = request.get_json(silent=True) or {}
    email = str(body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"error": "bad email"}), 400
    activity = body.get("activity") or {}
    if not isinstance(activity, dict):
        activity = {}
    profile = (_enrich_people([email]) or {}).get(email) or {}
    facts = _person_summary_facts(email, activity, profile)
    return jsonify({"summary": _person_ai_summary(email, facts),
                    "openai": bool(os.environ.get("OPENAI_API_KEY", ""))})

@app.route("/p2/admin/external-usage/export")
@admin_required
def admin_external_usage_export():
    """Download the whole dashboard as an .xlsx (People / Activity timeline /
    Agent runs / Summary). Streams the bytes straight out, no temp file."""
    try:
        data = _export_external_usage_xlsx()
    except Exception as e:
        log.warning("external usage export failed: %s", e)
        return jsonify({"error": "export failed"}), 500
    fname = "external-usage-%s.xlsx" % datetime.now(IST).strftime("%Y-%m-%d-%H%M")
    resp = make_response(data)
    resp.headers["Content-Type"] = ("application/vnd.openxmlformats-officedocument"
                                    ".spreadsheetml.sheet")
    resp.headers["Content-Disposition"] = 'attachment; filename="%s"' % fname
    resp.headers["Content-Length"] = str(len(data))
    resp.headers["Cache-Control"] = "no-store"
    return resp

# ── Client Usage (per-client-portal analytics) ───────────────────────────────
# For each client we run a co-branded portal at /<slug>. This aggregates every
# tracked page view on that portal (Page Views tab, filtered by URL path) and
# splits it into TWO audiences by email domain: Position² staff (@position2.com)
# vs the client's own people (the client's configured domains). Everything else
# (rare) is bucketed as "Other". Names/pictures are enriched from the two sign-in
# tabs (internal Login Log + Member Signins), both of which carry email @5, name @6.
_CU_CACHE = {}          # slug -> {"data": dict, "ts": float}
_CU_ALL_CACHE = {"data": None, "ts": 0.0}
_CU_TTL = 300


def _fmt_secs(s):
    """Seconds -> compact human duration: 45s, 12m 3s, 2h 14m."""
    s = int(s or 0)
    if s < 60:
        return "%ds" % s
    m, sec = divmod(s, 60)
    if m < 60:
        return "%dm %ds" % (m, sec) if sec else "%dm" % m
    h, m = divmod(m, 60)
    return "%dh %dm" % (h, m) if m else "%dh" % h


def _cu_read_tab(tab_range):
    """One Sheets read from the login-log spreadsheet; [] on any failure."""
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
        log.warning("client-usage sheet read failed (%s): %s", tab_range, e)
        return []


def _cu_name_map():
    """email(lower) -> {'name':.., 'picture':..} from both sign-in tabs."""
    m = {}
    for rng, pic_i in (("A:U", None), ("%s!A:T" % _MEMBER_TAB, 8)):
        rows = _cu_read_tab(rng)
        for r in rows[1:] if len(rows) > 1 else []:
            email = (r[5].strip().lower() if len(r) > 5 else "")
            if not email:
                continue
            name = (r[6].strip() if len(r) > 6 else "")
            pic = (r[pic_i].strip() if pic_i is not None and len(r) > pic_i else "")
            cur = m.setdefault(email, {"name": "", "picture": ""})
            if name and not cur["name"]:
                cur["name"] = name
            if pic and not cur["picture"]:
                cur["picture"] = pic
    return m


def _cu_pretty_name(email, name_map):
    info = name_map.get((email or "").lower(), {})
    if info.get("name"):
        return info["name"]
    local = (email or "").split("@")[0]
    return " ".join(w.capitalize() for w in re.split(r"[.\-_]+", local) if w) or email


def _cu_url_belongs(url, slug):
    """True if a Page-Views URL path is inside this client's portal (/<slug>...)."""
    u = (url or "").split("?")[0].rstrip("/").lower()
    s = "/" + slug.lower()
    return u == s or u.startswith(s + "/")


def _cu_client_of_url(url):
    """First path segment of a URL if it maps to a known client slug, else None."""
    seg = (url or "").split("?")[0].strip("/").split("/")[0].lower()
    return seg if seg in CLIENTS else None


def _fetch_client_usage(slug, force=False):
    """Per-client portal analytics, split Position² team vs client team."""
    client = CLIENTS.get(slug)
    if not client:
        return None
    now = time.time()
    cached = _CU_CACHE.get(slug)
    if not force and cached and (now - cached["ts"]) < _CU_TTL:
        return cached["data"]

    name_map = _cu_name_map()
    pv = _cu_read_tab("Page Views!A:N")
    rows = pv[1:] if len(pv) > 1 else []

    def col(r, i, d=""):
        return r[i] if len(r) > i else d

    p2_dom = "@position2.com"
    cli_doms = ["@" + d.lower() for d in client.get("domains", [])]

    def seg_of(email):
        e = (email or "").lower()
        if e.endswith(p2_dom):
            return "p2"
        if any(e.endswith(d) for d in cli_doms):
            return "client"
        return "other"

    people = {}         # email -> person record
    seg_stats = {k: {"views": 0, "seconds": 0} for k in ("p2", "client", "other")}
    timeline = {}       # date -> {"p2":n,"client":n,"other":n}
    pages = {}          # url -> {"title","views","seconds"}
    browsers, devices = Counter(), Counter()
    first_activity, last_activity = "", ""

    for r in rows:
        url = col(r, 6)
        if not _cu_url_belongs(url, slug):
            continue
        email = (col(r, 4) or "").strip()
        if not email:
            continue
        seg = seg_of(email)
        ts = col(r, 0); date = col(r, 1); title = col(r, 5) or url
        try:
            secs = int(float(col(r, 7) or 0))
        except (TypeError, ValueError):
            secs = 0
        browser = col(r, 10); device = col(r, 12)

        seg_stats[seg]["views"] += 1
        seg_stats[seg]["seconds"] += secs
        if browser:
            browsers[browser] += 1
        if device:
            devices[device] += 1
        if date:
            t = timeline.setdefault(date, {"p2": 0, "client": 0, "other": 0})
            t[seg] += 1
        pg = pages.setdefault(url, {"title": title, "url": url, "views": 0, "seconds": 0})
        pg["views"] += 1
        pg["seconds"] += secs
        if ts:
            first_activity = min(first_activity, ts) if first_activity else ts
            last_activity = max(last_activity, ts)

        p = people.get(email.lower())
        if p is None:
            p = {"email": email, "name": _cu_pretty_name(email, name_map),
                 "picture": name_map.get(email.lower(), {}).get("picture", ""),
                 "segment": seg, "views": 0, "seconds": 0,
                 "pages": {}, "first_seen": ts, "last_seen": ts,
                 "browser": browser, "device": device, "_events": []}
            people[email.lower()] = p
        p["views"] += 1
        p["seconds"] += secs
        p["pages"][url] = p["pages"].get(url, 0) + 1
        p["_events"].append({"kind": "view", "ts": ts, "title": title, "url": url,
                             "seconds": secs, "duration": _fmt_secs(secs)})
        if ts:
            p["first_seen"] = min(p["first_seen"], ts) if p["first_seen"] else ts
            p["last_seen"] = max(p["last_seen"], ts)
        if browser and not p["browser"]:
            p["browser"] = browser

    # Login events: who signed in and when. Sign-ins are a global auth event, not
    # scoped to a portal -- a Position² staffer signs in once and that same
    # session covers /p2, /app and every client portal. Counting every one of
    # their sign-ins here would overstate THIS portal's usage with unrelated
    # internal-tool activity (both tabs carry timestamp @0, email @5). So a login
    # only counts here if it falls on a calendar day the same person also viewed
    # a page on THIS portal -- their page-view events above are already scoped
    # to /<slug>, so this reuses that scoping rather than re-deriving it.
    portal_days_by_email = {e: {ev["ts"][:10] for ev in p["_events"] if ev.get("ts")}
                             for e, p in people.items()}
    login_map = {}   # email(lower) -> [ts, ...]
    for rng in ("A:U", "%s!A:T" % _MEMBER_TAB):
        for r in _cu_read_tab(rng)[1:]:
            e = (r[5].strip().lower() if len(r) > 5 else "")
            t = (r[0].strip() if len(r) > 0 else "")
            if e and t and t[:10] in portal_days_by_email.get(e, ()):
                login_map.setdefault(e, []).append(t)

    # Agent runs by this portal's users. The 'Agent Runs' tab is global (email +
    # agent slug, no client column), so we scope to the agents configured for THIS
    # client: accurate for client-team members (they only reach this one portal) and
    # a sensible best-effort for Position² staff.
    client_run_slugs = {_canonical_agent_slug(s) for s in client.get("agents", [])}
    runs_by_email = {}   # email(lower) -> {total, by_agent{name:n}, last, events[]}
    _ARIDX = {n: i for i, n in enumerate(_AR_HEADER)}
    def _arv(r, n, d=""):
        i = _ARIDX.get(n, -1)
        return r[i] if 0 <= i < len(r) else d
    for r in _cu_read_tab("%s!A:F" % _AR_TAB)[1:]:
        e = (_arv(r, "Email") or "").strip().lower()
        if not e:
            continue
        slug = _canonical_agent_slug(_arv(r, "Agent Slug") or "")
        if client_run_slugs and slug not in client_run_slugs:
            continue
        ts = _arv(r, "Timestamp (IST)")
        aname = _arv(r, "Agent Name") or APP_AGENTS_BY_SLUG.get(slug, {}).get("name", slug)
        rec = runs_by_email.setdefault(e, {"total": 0, "by_agent": {}, "last": "", "events": []})
        rec["total"] += 1
        rec["by_agent"][aname] = rec["by_agent"].get(aname, 0) + 1
        rec["events"].append({"ts": ts, "name": aname})
        if ts and ts > rec["last"]:
            rec["last"] = ts

    # Deliberately NOT folding in people who only appear in runs_by_email with no
    # tracked page view here: client_run_slugs is often a handful of the shared
    # /app agents (Keyword Finder, Content Brief Generator, Content Enhancer),
    # which every client's roster can include -- so "ran an agent this client
    # also happens to offer" does not mean "used THIS client's portal." Without
    # a page view (or, per the login scoping above, a matching login) on
    # /<slug>, a person has no actual footprint on this portal and shouldn't
    # appear in its people list, even if their agent-run total looks nonzero.

    def finalize(p):
        top = sorted(p["pages"].items(), key=lambda kv: -kv[1])
        p["pages_count"] = len(p["pages"])
        p["top_pages"] = [{"url": u, "views": n} for u, n in top[:6]]
        del p["pages"]
        p["duration"] = _fmt_secs(p["seconds"])
        logins = login_map.get(p["email"].lower(), [])
        p["logins"] = len(logins)
        rr = runs_by_email.get(p["email"].lower(), {})
        p["agent_runs"] = rr.get("total", 0)
        p["agent_breakdown"] = sorted(
            [{"name": k, "count": v} for k, v in rr.get("by_agent", {}).items()],
            key=lambda x: -x["count"])
        ev = p.pop("_events")
        for t in logins:
            ev.append({"kind": "login", "ts": t, "title": "Signed in", "url": "", "duration": ""})
        for rv in rr.get("events", []):
            ev.append({"kind": "run", "ts": rv["ts"], "title": "Ran " + rv["name"], "url": "", "duration": ""})
        ev.sort(key=lambda x: x["ts"] or "", reverse=True)
        p["events"] = ev[:80]
        if logins:
            p["last_login"] = max(logins)
        if rr.get("last"):
            p["last_run"] = rr["last"]
        return p

    ppl = [finalize(p) for p in people.values()]
    ppl.sort(key=lambda x: (-x["views"], -x.get("agent_runs", 0), x["name"]))
    seg_people = {k: [p for p in ppl if p["segment"] == k] for k in ("p2", "client", "other")}

    def _seg_sum(seg, key):
        return sum(p.get(key, 0) for p in seg_people[seg])

    # Global recent-activity feed: every event across everyone, newest first, tagged
    # with the person. Powers the "all activity" view and per-page viewer drilldowns.
    recent = []
    for p in ppl:
        for e in p["events"]:
            recent.append({"name": p["name"], "email": p["email"], "segment": p["segment"],
                           "picture": p["picture"], "kind": e["kind"], "ts": e["ts"],
                           "title": e["title"], "url": e["url"], "duration": e.get("duration", "")})
    recent.sort(key=lambda x: x["ts"] or "", reverse=True)
    recent = recent[:400]

    top_pages = sorted(pages.values(), key=lambda x: -x["views"])[:12]
    for pg in top_pages:
        pg["duration"] = _fmt_secs(pg["seconds"])
        pg["viewers"] = len({e["email"] for e in recent if e["kind"] == "view" and e["url"] == pg["url"]})
    tl = [{"date": d, "p2": v["p2"], "client": v["client"], "other": v["other"]}
          for d, v in sorted(timeline.items())]

    total_views = sum(s["views"] for s in seg_stats.values())
    total_secs = sum(s["seconds"] for s in seg_stats.values())
    data = {
        "client": {"slug": slug, "name": client["name"], "short": client.get("short", client["name"]),
                   "logo": client.get("logo", ""), "website": client.get("website", ""),
                   "domains": client.get("domains", []),
                   "accent": client.get("accent", "#5b9dff"), "accent2": client.get("accent2", "#8b5cf6")},
        "kpis": {
            "total_views": total_views, "total_people": len(ppl),
            "total_seconds": total_secs, "total_time": _fmt_secs(total_secs),
            "total_logins": sum(p["logins"] for p in ppl),
            "total_runs": sum(p["agent_runs"] for p in ppl),
            "p2_people": len(seg_people["p2"]), "client_people": len(seg_people["client"]),
            "other_people": len(seg_people["other"]),
            "p2_views": seg_stats["p2"]["views"], "client_views": seg_stats["client"]["views"],
            "p2_time": _fmt_secs(seg_stats["p2"]["seconds"]),
            "client_time": _fmt_secs(seg_stats["client"]["seconds"]),
            "first_activity": first_activity[:10], "last_activity": last_activity[:10],
        },
        "segments": {
            "p2": {"label": "Position² team", "people": seg_people["p2"],
                   "views": seg_stats["p2"]["views"], "time": _fmt_secs(seg_stats["p2"]["seconds"]),
                   "logins": _seg_sum("p2", "logins"), "runs": _seg_sum("p2", "agent_runs")},
            "client": {"label": "%s team" % client.get("short", client["name"]),
                       "people": seg_people["client"], "views": seg_stats["client"]["views"],
                       "time": _fmt_secs(seg_stats["client"]["seconds"]),
                       "logins": _seg_sum("client", "logins"), "runs": _seg_sum("client", "agent_runs")},
            "other": {"label": "Other", "people": seg_people["other"],
                      "views": seg_stats["other"]["views"], "time": _fmt_secs(seg_stats["other"]["seconds"]),
                      "logins": _seg_sum("other", "logins"), "runs": _seg_sum("other", "agent_runs")},
        },
        "timeline": tl,
        "top_pages": top_pages,
        "recent": recent,
        "browsers": dict(browsers.most_common(6)),
        "devices": dict(devices.most_common()),
        "fetched_at": now,
    }
    _CU_CACHE[slug] = {"data": data, "ts": now}
    return data


def _fetch_all_client_summaries(force=False):
    """Lightweight per-client rollup for the Client Usage landing cards."""
    now = time.time()
    if not force and _CU_ALL_CACHE["data"] is not None and (now - _CU_ALL_CACHE["ts"]) < _CU_TTL:
        return _CU_ALL_CACHE["data"]
    pv = _cu_read_tab("Page Views!A:N")
    rows = pv[1:] if len(pv) > 1 else []
    summ = {s: {"views": 0, "people": set(), "last": ""} for s in CLIENTS}
    for r in rows:
        url = r[6] if len(r) > 6 else ""
        cslug = _cu_client_of_url(url)
        if not cslug:
            continue
        email = (r[4].strip().lower() if len(r) > 4 else "")
        if not email:
            continue
        summ[cslug]["views"] += 1
        summ[cslug]["people"].add(email)
        ts = r[0] if len(r) > 0 else ""
        if ts:
            summ[cslug]["last"] = max(summ[cslug]["last"], ts)
    out = []
    for slug, c in CLIENTS.items():
        s = summ[slug]
        out.append({
            "slug": slug, "name": c["name"], "short": c.get("short", c["name"]),
            "logo": c.get("logo", ""), "website": c.get("website", ""),
            "accent": c.get("accent", "#5b9dff"), "accent2": c.get("accent2", "#8b5cf6"),
            "domains": c.get("domains", []),
            "views": s["views"], "people": len(s["people"]), "last_active": s["last"][:10],
        })
    out.sort(key=lambda x: -x["views"])
    _CU_ALL_CACHE["data"] = out
    _CU_ALL_CACHE["ts"] = now
    return out


@app.route("/p2/admin/client-usage")
@admin_required
def admin_client_usage():
    """Landing page: one card per client portal we run."""
    force = request.args.get("fresh") in ("1", "true", "yes")
    clients = _fetch_all_client_summaries(force=force)
    return render_template("admin_client_usage.html", user=_get_user(), clients=clients)


@app.route("/p2/admin/client-usage/<client_slug>")
@admin_required
def admin_client_detail(client_slug):
    """Per-client portal analytics dashboard (Position² team vs client team)."""
    if client_slug not in CLIENTS:
        abort(404)
    return render_template("admin_client_detail.html", user=_get_user(),
                           client=CLIENTS[client_slug], client_slug=client_slug)


@app.route("/p2/admin/client-usage/<client_slug>/data")
@admin_required
def admin_client_detail_data(client_slug):
    if client_slug not in CLIENTS:
        abort(404)
    force = request.args.get("fresh") in ("1", "true", "yes")
    data = _fetch_client_usage(client_slug, force=force)
    return jsonify(data or {})


@app.route("/p2/admin/access-requests")
@admin_required
def admin_requests():
    """Admin view of everyone who submitted the Request Access form, plus
    everyone who requested access to a not-yet-connected agent from /app."""
    reqs = _read_access_requests()
    agent_reqs = _agent_access_requests_raw()
    return render_template("admin_requests.html", user=_get_user(),
                           requests=reqs, count=len(reqs),
                           agent_requests=agent_reqs, agent_count=len(agent_reqs))

@app.route("/p2/admin/public-agent-usage")
@admin_required
def admin_agent_runs():
    """Admin-only view of per-user, per-agent run counts against the cap."""
    return render_template("admin_agent_runs.html", user=_get_user())

@app.route("/p2/admin/public-agent-usage/data")
@admin_required
def admin_agent_runs_data():
    """JSON data endpoint called by the admin agent-usage shell page."""
    return jsonify(_fetch_agent_run_stats())

# ── Legacy admin URLs (pre-rename) ───────────────────────────────────────────
# These pages' URLs originally didn't match their display names (e.g. "Public
# Page Analytics" lived at /p2/admin/members). Renamed the routes above to
# match; these 301s keep any bookmarked/shared old links working.
@app.route("/p2/admin/usage")
def _legacy_admin_usage():
    return redirect("/p2/admin/internal-usage", code=301)

@app.route("/p2/admin/usage/data")
def _legacy_admin_usage_data():
    return redirect("/p2/admin/internal-usage/data", code=301)

@app.route("/p2/admin/visitors")
def _legacy_admin_visitors():
    return redirect("/p2/admin/anonymous-traffic", code=301)

@app.route("/p2/admin/visitors/data")
def _legacy_admin_visitors_data():
    return redirect("/p2/admin/anonymous-traffic/data", code=301)

@app.route("/p2/admin/members")
def _legacy_admin_members():
    return redirect("/p2/admin/public-page-analytics", code=301)

@app.route("/p2/admin/members/data")
def _legacy_admin_members_data():
    return redirect("/p2/admin/public-page-analytics/data", code=301)

@app.route("/p2/admin/requests")
def _legacy_admin_requests():
    return redirect("/p2/admin/access-requests", code=301)

@app.route("/p2/admin/agent-runs")
def _legacy_admin_agent_runs():
    return redirect("/p2/admin/public-agent-usage", code=301)

@app.route("/p2/admin/agent-runs/data")
def _legacy_admin_agent_runs_data():
    return redirect("/p2/admin/public-agent-usage/data", code=301)

@app.route("/p2/admin/email-test")
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


@app.route("/p2/gtm/anonymous-visitors")
@position2_required
def anonymous_visitors():
    """Anonymous Visitors dashboard shell — loads data async."""
    return render_template("anonymous_visitors.html", user=_get_user())


@app.route("/gtm/anonymous-visitors/data")
@app.route("/ppc/anonymous-visitors/data")
@position2_required
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




# ── LinkedIn Intelligence (live Google Sheet) ────────────────────────────────
# "6th July Linkedin Agent Sheet" — one row per person×post engagement, refreshed
# by an external scraper. We read it live (header-mapped, so column order/count
# can drift) instead of the old committed data/linkedin.json snapshot.
LINKEDIN_INTEL_SHEET_ID = "17qjHoVN9zSzblP2XxXkHZhjCSH7I13STt49imle_fVw"

_LI_SENIORITY_BUCKET = {
    "C-Level / Founder": "csuite",
    "VP": "vpdirector",
    "Director": "vpdirector",
    "Manager": "managers",
    "IC / Individual Contributor": "ics",
}


def _li_bucket(label):
    return _LI_SENIORITY_BUCKET.get((label or "").strip(), "unknown")


def _li_int(s):
    try:
        return int(str(s).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0


def _li_yes(s):
    return (s or "").strip().lower() == "yes"


def _li_company_key(name):
    """Identity key for grouping company rows. We key on the normalised company
    *name*, not the sheet's 'Current Company ID', because the same company often
    appears with the ID on some rows and blank on others (e.g. NorthStar Anesthesia:
    44 rows with an ID, 7 blank) — keying on ID would split it into two cards."""
    return re.sub(r"\s+", " ", (name or "").strip()).lower() or "(unknown)"


def _empty_linkedin_result():
    return {"posts": [], "people": [], "companies": [], "company_lb": [], "stats": {
        "total_people": 0, "total_posts": 0, "total_companies": 0, "total_engagements": 0,
        "total_dms": 0, "total_comments": 0, "csuite_count": 0, "vp_count": 0,
        "director_count": 0, "reaction_breakdown": {}, "seniority_breakdown": {},
        "country_breakdown": {}, "relationship_breakdown": {}, "source_accounts": [],
        "last_synced": "",
    }, "synced_ok": False, "fetched_at": 0}


def _transform_linkedin_rows(rows):
    """Turn the flat 'one row per person x post engagement' sheet into the
    posts/people/companies shape the dashboard renders. Columns are looked up
    by header name (not position), so reordering/adding sheet columns is safe."""
    if not rows or len(rows) < 2:
        return _empty_linkedin_result()
    header = rows[0]
    H = {name.strip(): i for i, name in enumerate(header) if name}

    def g(row, key, default=""):
        i = H.get(key)
        if i is None or i >= len(row):
            return default
        return (row[i] or "").strip()

    posts, people, companies = {}, {}, {}
    reaction_ctr, relationship_ctr = Counter(), Counter()
    source_accounts, last_synced = set(), ""

    for row in rows[1:]:
        name = g(row, "Person Full Name")
        if not name:
            continue

        person_id = g(row, "Person ID") or name.lower()
        company_name = g(row, "Current Company Name") or "(Unknown)"
        company_id = _li_company_key(company_name)
        post_id = g(row, "Post ID") or g(row, "Post URL") or g(row, "Post Snippet")[:60]
        seniority = g(row, "Seniority Bucket")
        bucket = _li_bucket(seniority)
        dm = "Yes" if _li_yes(g(row, "Decision-Maker?")) else "No"
        commented = _li_yes(g(row, "Commented?"))
        reaction = g(row, "Reaction Type")
        relationship = g(row, "Relationship to Target") or "External"
        extracted_at = g(row, "Extracted At")
        if extracted_at:
            last_synced = max(last_synced, extracted_at)
        src = g(row, "Source Account")
        if src:
            source_accounts.add(src)
        if reaction:
            reaction_ctr[reaction] += 1
        relationship_ctr[relationship] += 1

        person_summary = {
            "name": name,
            "first": g(row, "First Name"),
            "last": g(row, "Last Name"),
            "url": g(row, "LinkedIn Profile URL"),
            "title": g(row, "Current Job Title"),
            "headline": g(row, "Headline"),
            "company": company_name,
            "industry": g(row, "Person Industry"),
            "size": g(row, "Company Size / Employee Count"),
            "location": g(row, "Person Location / Region"),
            "country": g(row, "Person Country"),
            "seniority": seniority,
            "bucket": bucket,
            "dm": dm,
            "degree": g(row, "Connection Degree"),
            "pic": g(row, "Profile Picture URL"),
            "followers": _li_int(g(row, "Followers Count")),
            "connections": _li_int(g(row, "Connections Count")),
            "relationship": relationship,
        }

        post = posts.get(post_id)
        if post is None:
            post = {
                "id": post_id,
                "url": g(row, "Post URL"),
                "snippet": g(row, "Post Snippet"),
                "date": g(row, "Post Date") or g(row, "Like / Reaction Date")[:10] or extracted_at[:10],
                "author": g(row, "Post Author (Target Company)") or "Position²",
                "engagers": [],
            }
            posts[post_id] = post
        post["engagers"].append({**person_summary, "reaction": reaction, "commented": commented})

        p = people.get(person_id)
        if p is None:
            p = dict(person_summary)
            p["_posts"] = set()
            p["_comments"] = 0
            people[person_id] = p
        p["_posts"].add(post_id)
        if commented:
            p["_comments"] += 1
        if not p.get("pic") and person_summary.get("pic"):
            p["pic"] = person_summary["pic"]

        c = companies.get(company_id)
        if c is None:
            c = {
                "name": company_name,
                "industry": g(row, "Company Industry"),
                "size": g(row, "Company Size / Employee Count"),
                "website": g(row, "Company Website"),
                "hq": g(row, "Company HQ Location"),
                "li_url": g(row, "Current Company LinkedIn URL"),
                "people": {},
                "_posts": set(),
            }
            companies[company_id] = c
        c["people"][person_id] = person_summary
        c["_posts"].add(post_id)

    people_list = []
    for p in people.values():
        p["posts_engaged"] = len(p.pop("_posts"))
        p["comments_count"] = p.pop("_comments")
        people_list.append(p)
    people_list.sort(key=lambda x: (-x["posts_engaged"], x["name"]))

    SEN_LABELS = ["C-Level / Founder", "VP", "Director", "Manager", "IC / Individual Contributor"]
    company_list = []
    for c in companies.values():
        ppl = list(c["people"].values())
        c["people"] = ppl
        c["people_count"] = len(ppl)
        c["dm_count"] = sum(1 for x in ppl if x["dm"] == "Yes")
        c["posts_engaged"] = len(c.pop("_posts"))
        c["seniority_map"] = dict(Counter(x["seniority"] for x in ppl if x["seniority"] in SEN_LABELS))
        company_list.append(c)
    company_list.sort(key=lambda x: (-x["people_count"], x["name"]))

    company_lb = [[c["name"], c["people_count"]] for c in company_list if c["name"] != "(Unknown)"][:15]

    posts_list = sorted(posts.values(), key=lambda p: p.get("date") or "", reverse=True)
    seniority_breakdown = Counter(p["seniority"] for p in people_list if p["seniority"])
    country_breakdown = Counter(p["country"] for p in people_list if p["country"])

    stats = {
        "total_people": len(people_list),
        "total_posts": len(posts_list),
        "total_companies": len([c for c in company_list if c["name"] != "(Unknown)"]),
        "total_engagements": sum(len(p["engagers"]) for p in posts_list),
        "total_dms": sum(1 for p in people_list if p["dm"] == "Yes"),
        "total_comments": sum(p["comments_count"] for p in people_list),
        "csuite_count": sum(1 for p in people_list if p["bucket"] == "csuite"),
        "vp_count": sum(1 for p in people_list if p["seniority"] == "VP"),
        "director_count": sum(1 for p in people_list if p["seniority"] == "Director"),
        "reaction_breakdown": dict(reaction_ctr.most_common()),
        "seniority_breakdown": dict(seniority_breakdown.most_common()),
        "country_breakdown": dict(country_breakdown.most_common(10)),
        "relationship_breakdown": dict(relationship_ctr.most_common()),
        "source_accounts": sorted(source_accounts),
        "last_synced": last_synced,
    }

    return {"posts": posts_list, "people": people_list, "companies": company_list,
            "company_lb": company_lb, "stats": stats}


# Per-sheet caches, keyed by spreadsheet ID, so multiple LinkedIn Intelligence
# surfaces (the internal /p2 dashboard and each client portal) each read their own
# sheet with an independent TTL cache and gzip buffer.
_LI_CACHES = {}   # sheet_id -> {"data": dict|None, "ts": float}
_LI_GZS = {}      # sheet_id -> {"ts": float|None, "raw": bytes, "gz": bytes}
_LI_TABS = {}     # sheet_id -> resolved first-tab title (memoized)
_LI_CACHE_TTL = 300  # seconds — served from cache between refreshes; the button forces a live pull


def _li_first_tab(svc, sheet_id: str) -> str:
    """Title of the first (leftmost) worksheet, so we never assume it's 'Sheet1'.
    Memoized per sheet; falls back to 'Sheet1' if metadata can't be read."""
    if sheet_id in _LI_TABS:
        return _LI_TABS[sheet_id]
    title = "Sheet1"
    try:
        meta = svc.spreadsheets().get(
            spreadsheetId=sheet_id, fields="sheets.properties.title").execute()
        sheets = meta.get("sheets", [])
        if sheets:
            title = sheets[0]["properties"]["title"] or "Sheet1"
    except Exception as e:
        log.warning("linkedin intel tab lookup failed (%s): %s", sheet_id, e)
    _LI_TABS[sheet_id] = title
    return title


def _fetch_linkedin_intel_data(force: bool = False,
                               sheet_id: str = LINKEDIN_INTEL_SHEET_ID) -> dict:
    """Fetch + transform a LinkedIn Intelligence Google Sheet (TTL-cached, per sheet)."""
    cache = _LI_CACHES.setdefault(sheet_id, {"data": None, "ts": 0.0})
    now = time.time()
    if not force and cache["data"] is not None and (now - cache["ts"]) < _LI_CACHE_TTL:
        return cache["data"]
    rows = None
    try:
        svc = _sheets_service()
        resp = svc.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=_li_first_tab(svc, sheet_id)).execute()
        rows = resp.get("values", [])
    except Exception as e:
        log.warning("linkedin intel sheet read failed (%s): %s", sheet_id, e)
    if rows:
        try:
            result = _transform_linkedin_rows(rows)
            result["synced_ok"] = True
        except Exception as e:
            log.warning("linkedin intel transform failed (%s): %s", sheet_id, e)
            result = cache["data"] or _empty_linkedin_result()
            result["synced_ok"] = False
    else:
        result = cache["data"] or _empty_linkedin_result()
        result["synced_ok"] = False
    result["fetched_at"] = now
    cache["data"] = result
    cache["ts"] = now
    return result


def _linkedin_data_response(sheet_id: str, force: bool):
    """Build the gzipped JSON response for a LinkedIn Intelligence sheet (per-sheet
    gzip buffer, rebuilt only when the underlying cache timestamp changes)."""
    data = _fetch_linkedin_intel_data(force=force, sheet_id=sheet_id)
    cache = _LI_CACHES[sheet_id]
    gz = _LI_GZS.setdefault(sheet_id, {"ts": None, "raw": b"", "gz": b""})
    if gz["ts"] != cache["ts"]:
        gz["raw"] = json.dumps(data, separators=(",", ":")).encode("utf-8")
        gz["gz"] = gzip.compress(gz["raw"], 6)
        gz["ts"] = cache["ts"]
    use_gz = "gzip" in request.headers.get("Accept-Encoding", "").lower()
    resp = make_response(gz["gz"] if use_gz else gz["raw"])
    resp.headers["Content-Type"] = "application/json"
    resp.headers["Cache-Control"] = "private, max-age=30"
    resp.headers["Vary"] = "Accept-Encoding"
    if use_gz:
        resp.headers["Content-Encoding"] = "gzip"
    return resp


@app.route("/p2/gtm/linkedin-scraper")
@position2_required
def linkedin_scraper_old_redirect():
    return redirect("/p2/gtm/linkedin-intelligence", code=301)


@app.route("/p2/gtm/linkedin-intelligence")
@position2_required
def linkedin_scraper():
    """LinkedIn Intelligence dashboard — Post & People Intelligence, live from Google Sheets."""
    return render_template("linkedin_scraper.html", user=_get_user(),
                           data_url=url_for("linkedin_scraper_data"), client_mode=False,
                           li_cfg={"employer": "Position²", "employerShort": "P²",
                                   "employerTokens": ["position"]})


@app.route("/p2/gtm/linkedin-scraper/data")
@position2_required
def linkedin_scraper_data_old_redirect():
    return redirect("/p2/gtm/linkedin-intelligence/data", code=301)


@app.route("/p2/gtm/linkedin-intelligence/data")
@position2_required
def linkedin_scraper_data():
    """JSON data endpoint for the LinkedIn Intelligence dashboard (gzipped, cached).
    ?fresh=1 bypasses the cache and re-pulls the sheet live — this is what the
    dashboard's Refresh button calls."""
    force = request.args.get("fresh") in ("1", "true", "yes")
    return _linkedin_data_response(LINKEDIN_INTEL_SHEET_ID, force)

@app.route("/health")
def health():
    return jsonify({"status": "ok", "accounts": {
        aid: {"name": cfg["name"], "dashboard_exists": cfg["dashboard"].exists()}
        for aid, cfg in ACCOUNTS.items()
    }})

@app.route("/api/weekly-stats")
@app.route("/api/weekly-stats/<account_id>")
@position2_required
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
      display:flex;align-items:center;justify-content:center;font-size:17px;
      box-shadow:0 0 14px rgba(99,102,241,.3)}
    .brand-name{font-size:15px;font-weight:700;color:#f1f5f9}
    .bc{display:flex;align-items:center;gap:8px;margin-left:18px;padding-left:18px;
      border-left:1px solid rgba(255,255,255,.07)}
    .bc a{font-size:13px;color:#2d3450;text-decoration:none;transition:color .15s}
    .bc a:hover{color:#64748b}
    .bc-sep{font-size:13px;color:#1a1d27}
    .bc-cur{font-size:13px;font-weight:600;color:#818cf8}
    .sign-out{font-size:11.5px;color:#3d4460;text-decoration:none;
      padding:6px 14px;border:1px solid rgba(255,255,255,.07);border-radius:8px;
      transition:all .15s}
    .sign-out:hover{color:#ef4444;border-color:rgba(239,68,68,.4)}
    .main{flex:1;position:relative;z-index:1;
      display:flex;flex-direction:column;align-items:center;padding:72px 24px 48px}
    .label{font-size:11.5px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
      color:#6366f1;margin-bottom:10px;display:flex;align-items:center;gap:8px}
    .label::before,.label::after{content:'';display:block;width:20px;height:1px;background:rgba(99,102,241,.4)}
    .heading{font-size:30px;font-weight:700;color:#f1f5f9;letter-spacing:-.02em;
      margin-bottom:6px;text-align:center}
    .sub{font-size:15px;color:#64748b;margin-bottom:52px}
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
    .card-thumb-icon{font-size:40px;opacity:.45;filter:drop-shadow(0 0 20px rgba(255,255,255,.15))}
    .card-thumb::after{content:'';position:absolute;inset:0;
      background:linear-gradient(to bottom,transparent 30%,rgba(13,15,23,.95) 100%)}
    .card-badge{position:absolute;top:10px;right:10px;z-index:1;
      font-size:10px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
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
    .stat{font-size:11.5px;color:#64748b}
    .stat span{color:var(--accent-text);font-weight:600}
    .arrow{font-size:17px;color:var(--accent-text);opacity:0;transition:opacity .15s,transform .15s}
    .card:hover .arrow{opacity:1;transform:translateX(3px)}
    .foot{margin-top:48px;font-size:11.5px;color:#13151f}
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

# ── Context size guards ────────────────────────────────────────────────────────
# The live-data context is injected into Vimi's system prompt. Left unbounded it
# grew past the model's input-token limit (272k), so EVERY chat — even "hi" —
# failed with context_length_exceeded. These caps keep the newest / most relevant
# rows per section and enforce a hard overall character budget as a backstop.
# Data is newest-first, so trimming the tail drops only the oldest rows.
_CTX_MAX_ROWS = {          # per-section row caps (newest first)
    "companies": 400,
    "people":    400,
    "signals":   400,      # per account
    "ads":       400,
}
# Hard ceiling on the whole context string. ~4 chars/token, so 480k chars is
# roughly 120k tokens — comfortably under the 272k input limit once the base
# prompt, platform knowledge, chat history and response headroom are added.
_CTX_CHAR_BUDGET = 480_000


def _cap_rows(lines: list, key: str) -> tuple:
    """Return (kept_lines, total_count). Keeps the first N (newest) rows."""
    n = _CTX_MAX_ROWS.get(key, 400)
    total = len(lines)
    if total <= n:
        return lines, total
    return lines[:n], total

# Static ground truth about the platform itself, so Vimi can answer questions about
# how a feature/number/term works precisely instead of guessing. Keep this in sync
# with CONTEXT_FOR_NEW_CHAT_V17.md when that doc changes; it is intentionally short
# (cheap to inject on every request) and internal-only (this prompt only reaches
# @position2_required routes).
_VIMI_PLATFORM_KNOWLEDGE = """=== ABOUT THIS PLATFORM (ground truth for questions about the platform itself) ===
Intelligence by Position2 (intelligence.position2.com) is Position2's internal B2B revenue-intelligence
platform. Position2 is a B2B digital-marketing agency (SEO/organic growth, paid media, paid social,
content, brand & website, RevOps). The platform surfaces buying signals, de-anonymises website visitors,
tracks competitor ads and AI-answer-engine brand visibility, and runs a suite of SEO/GEO tools.

THREE SURFACES: (1) public marketing site, logged out; (2) /app member workspace, any signed-in Google
account, curated SEO/GEO agents + saved run history; (3) /p2/* internal staff app, @position2.com only
(this chat lives here) — Hub, GTM tools, SEO Studio, Accounts/Signal Tracker, Admin dashboards.

ANONYMOUS VISITORS (de-anonymisation engine, /p2/admin/anonymous-traffic, /p2/gtm/anonymous-visitors):
Identifies which COMPANIES (not usually individual people) visit the Position2 site, by fusing three
signals per visitor IP: IPinfo (org/ASN/hostname/privacy), reverse DNS, and RDAP registrant/netblock.
Each visitor gets a connection_type: "business" (a real company network — the only type that gets
identified), or "isp"/"mobile"/"hosting"/"proxy"/"education"/"government" (residential, cellular, cloud,
VPN, school, or government traffic — deliberately left unidentified to avoid false positives). Only
"business" traffic above a confidence floor is attributed to a real company name. Individual PERSON-level
identification only happens when the visitor has logged in, submitted a form, or matched a licensed data
co-op — anonymous browsing is never matched to a named person. Company firmographics come first from free
sources (the company's own homepage schema.org/meta data, tech-stack fingerprinting, SEC EDGAR for public
filers); a paid Apollo.io lookup only runs on explicit request (the "Enrich further" button) to control cost.

SIGNAL TRACKER (Accounts / Signal Tracker dashboards, /p2/accounts, /p2/signal-tracker/<account>):
Monitors named company lists per client account (Healthcare and CSG) for buying signals: funding rounds,
leadership changes, M&A, IPO activity, product launches, partnerships, hiring surges, and general news.
Each signal has a severity (HIGH/MEDIUM/LOW) and an importance score = signal type weight x severity x
recency (signals decay after about 90 days). Sourced from Apollo.io + news feeds (GDELT/SerpAPI/RSS),
refreshed weekly via a GitHub Actions pipeline. Exact company/signal counts per account are in the LIVE
DATA section below when available — use those numbers, never a memorised figure.

AD INTELLIGENCE (/p2/gtm/ad-intelligence): tracks competitors' running ads (headline, CTA, format,
keywords, messaging angle, first/last seen) pulled from a shared Google Sheet.

SEO STUDIO (/p2/seo/<tool>) and the /app agents: a suite of SEO/GEO tools (Keyword Finder, Content Brief
Generator, Content Enhancer, Technical SEO & GEO Auditor, Generative Search Visibility, AI Readiness
Auditor, Competitor SEO Intelligence, Local Visibility Builder, and more). Some are fully live and
connected; others are request-access only.

ADMIN DASHBOARDS (/p2/admin/*, admin-only): Internal Usage, Anonymous Traffic (the visitor de-anon
dashboard above), Public Page Analytics, Public Agent Usage, Agent Runs, Access Requests.

Auth: Google Sign-In is open to any Google account; only @position2.com reaches /p2/* internal pages; a
small admin allowlist reaches /p2/admin/*.

Ground rule: answer questions about how a platform feature/number/term works from the facts above, precisely
and without guessing. If something asked is genuinely outside both this and the LIVE DATA section, say so
plainly rather than inventing an answer, and use web search for public information neither one covers.
"""


def _build_ppc_context() -> str:
    """
    Fetch live data from every source, newest first, capped per section
    (_CTX_MAX_ROWS) with an overall character backstop (_CTX_CHAR_BUDGET) so the
    injected context can never exceed the model's input-token limit.
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

        # Cap to newest rows so the context stays within the model's token limit.
        c_lines, c_total = _cap_rows(c_lines, "companies")
        p_lines, p_total = _cap_rows(p_lines, "people")
        c_note = f" — showing newest {len(c_lines)} of {c_total}" if len(c_lines) < c_total else ""
        p_note = f" — showing newest {len(p_lines)} of {p_total}" if len(p_lines) < p_total else ""

        # COMPANIES block comes FIRST so GPT reads it first for "company" queries
        parts.append(
            f"=== VISITOR DATA ===\n"
            f"Summary: {len(people_out)} individual visitors from {len(companies_out)} unique companies\n"
            f"Top industries: {industry_counts}\n\n"
            f"--- SECTION A: COMPANIES THAT VISITED ({len(companies_out)} unique companies{c_note}) ---\n"
            f"USE THIS SECTION when asked about COMPANIES. Columns: Company, Website, Industry, Location, Employees, Revenue\n"
            + "\n".join(c_lines)
            + f"\n\n--- SECTION B: INDIVIDUAL VISITORS ({len(people_out)} people, newest first{p_note}) ---\n"
            f"USE THIS SECTION when asked about VISITORS or PEOPLE. Columns: Name, Title, CompanyWebsite, Industry, Location, DateVisited\n"
            + "\n".join(p_lines)
        )

    except Exception as e:
        parts.append(f"=== ANONYMOUS VISITORS ===\n⚠ Could not fetch: {e}")

    # ── 2. Signal Tracker — ALL signals, no limit, BOTH accounts ──────────
    import sqlite3 as _sql
    for _acct_label, _db_name in (("Healthcare", "tracker.db"), ("CSG", "tracker_csg_v2.db")):
        try:
            db_path = Path(__file__).parent / "data" / _db_name
            if not db_path.exists():
                parts.append(f"=== SIGNAL TRACKER ({_acct_label}) ===\n⚠ Database not on Railway — commit data/{_db_name} to git")
                continue

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

            sig_lines, sig_total = _cap_rows(sig_lines, "signals")
            sig_note = f" — showing newest {len(sig_lines)} of {sig_total}" if len(sig_lines) < sig_total else ""

            parts.append(
                f"=== SIGNAL TRACKER ({_acct_label} — {comp_count} companies with signals) ===\n"
                f"Total signals: {len(all_sigs)}\n"
                f"By type: {sig_counts}\n\n"
                f"--- ALL SIGNALS (newest first{sig_note}) ---\n"
                + "\n".join(sig_lines)
            )

        except Exception as e:
            parts.append(f"=== SIGNAL TRACKER ({_acct_label}) ===\n⚠ Could not fetch: {e}")

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
            ad_lines, ad_total = _cap_rows(ad_lines, "ads")
            ad_note = f" (showing newest {len(ad_lines)} of {ad_total})" if len(ad_lines) < ad_total else ""
            parts.append(
                f"=== AD INTELLIGENCE ===\n"
                f"Total ads tracked: {a['total_in_sheet']}\n"
                f"By competitor: {a['by_competitor']}\n"
                f"By format: {a['by_format']}\n"
                f"By status: {a['by_status']}\n"
                f"Top CTAs: {a['top_ctas']}\n"
                f"Top keywords: {a['top_keywords']}\n\n"
                f"--- ALL ADS{ad_note} ---\n"
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

    # Hard backstop: never let the context blow past the model input limit,
    # regardless of row sizes. Trim the tail (oldest data) and flag it.
    if len(ctx) > _CTX_CHAR_BUDGET:
        ctx = (ctx[:_CTX_CHAR_BUDGET]
               + "\n\n⚠ LIVE DATA truncated to fit the model's context limit; "
                 "oldest rows were dropped. Ask for a narrower slice (e.g. a specific "
                 "company, account, or date range) for full detail.")

    _PPC_CTX_CACHE["data"] = ctx
    _PPC_CTX_CACHE["ts"] = now
    return ctx


@app.route("/api/ppc-chat", methods=["POST"])
@position2_required
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

    system_prompt = f"""You are Vimi, the Intelligence Assistant for Position2, a B2B marketing agency.
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
- PRECISION RULE: never guess or invent a number, company, or fact. Ground every claim in the LIVE DATA
  or PLATFORM KNOWLEDGE below; for anything else (public company news, general knowledge, definitions not
  covered below) use web search. If, after checking all three, something is genuinely unknown, say so
  plainly instead of making it up.
- Questions about how a FEATURE of this platform works, what a term/metric means, or what a dashboard does
  are answered from the PLATFORM KNOWLEDGE section below, precisely — not from the raw data rows.

DATA SECTION RULES — NEVER MIX THESE:
- Asked about COMPANIES → use SECTION A only. Columns: Company Name, Website, Industry, Location, Employees, Revenue. Never include individual people names.
- Asked about VISITORS/PEOPLE → use SECTION B only. Columns: Name, Title, Company Website, Industry, Location, Date Visited.
- "last 10 companies" = first 10 rows of SECTION A. "last 10 visitors" = first 10 rows of SECTION B.
- Signal Tracker data is per client account (Healthcare, CSG) — never mix companies from one account into the other.

CSV/EXCEL EXPORT RULES:
- Output ONLY the CSV rows. No intro text, no explanation, no markdown fences, no code blocks.
- Use meaningful headers: "Company Name", "Website", "Industry", "Location", "Employees", "Revenue" — never "field1", "field2".
- Include ONLY the columns that make sense for the query (e.g. company query = 6 columns, no extra).
- Replace em-dashes (—) with a hyphen or leave blank. Quote values that contain commas.{fmt_instruction}

{_VIMI_PLATFORM_KNOWLEDGE}
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

    _max_out = 2800 if (has_file or export_fmt) else 1600
    try:
        if file_is_image and file_base64:
            # Vision path: web search + images don't combine reliably, mirror /api/vimi-chat's
            # approach and keep this on plain chat completions.
            answer, _m = _vimi_completion(oai, messages, _max_out, temperature=0.1)
            web_used = False
        else:
            model = os.environ.get("OPENAI_INSIGHTS_MODEL") or "gpt-5.4"
            answer, web_used = _responses_web_search(oai, model, messages, _max_out)
            if not answer:
                answer, web_used = _responses_web_search(
                    oai, os.environ.get("OPENAI_MODEL", "gpt-4o-mini"), messages, _max_out)
            if not answer:
                answer, _m = _vimi_completion(oai, messages, _max_out, temperature=0.1)
                web_used = False
        return jsonify({
            "answer":          answer,
            "detected_format": export_fmt or "",
            "is_export":       bool(export_fmt and not source_text),
            "is_csv":          export_fmt in ("csv", "excel"),
            "web_search_used": web_used,
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
@position2_required
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
@position2_required
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
@position2_required
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
@position2_required
def ppc_chat_debug():
    """Shows exactly what data the chatbot sees — use to diagnose blank/wrong answers."""
    _PPC_CTX_CACHE["ts"] = 0   # force refresh
    ctx = _build_ppc_context()
    return f"<pre style='font-size:11.5px;padding:20px'>{ctx}</pre>", 200


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
@position2_required
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
@position2_required
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
@position2_required
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
@position2_required
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
@position2_required
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
            "markdown into the requested file, so never refuse a format and never truncate with placeholders. "
            "If asked how a feature of this platform works or what a term/metric means, answer from the PLATFORM "
            "KNOWLEDGE below, precisely; never guess. If something is outside both the signal data and platform "
            "knowledge, use web search, and if still unknown say so plainly rather than inventing an answer.\n\n"
            "%s\n"
            "ACCOUNT OVERVIEW: %s\n\nRELEVANT SIGNAL DATA:\n%s%s" % (
                _VIMI_PLATFORM_KNOWLEDGE, overview, ctx_str, att_ctx))

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
@position2_required
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
@position2_required
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
@position2_required
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
