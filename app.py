"""Platform hub server — Google Sign-In + multi-dashboard routing."""

import os
import json
from pathlib import Path
from functools import wraps

from flask import (
    Flask, send_file, abort, jsonify,
    request, session, redirect, url_for,
    make_response, render_template,
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cst-dev-secret-do-not-use-in-prod-abc123xyz")

# ── Google OAuth ────────────────────────────────────────────────────────────────
# Set GOOGLE_CLIENT_ID in Railway → Variables.
# Setup: console.cloud.google.com → APIs & Services → Credentials
#        → Create OAuth 2.0 Client ID → Web application
#        → Authorised JavaScript origins: https://signals.position2.com
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")

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
def _get_user():
    """Return current user dict or None."""
    return session.get("google_user")

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not _get_user():
            return redirect(url_for("login_page"))
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

# ── Company Signal Tracker ───────────────────────────────────────────────────────
@app.route("/accounts")
@login_required
def accounts():
    cards_html = "".join(_build_account_card(aid, cfg) for aid, cfg in ACCOUNTS.items())
    return make_response(_ACCOUNTS_HTML.replace("{account_cards}", cards_html), 200)

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

# ── Account picker HTML ──────────────────────────────────────────────────────────
_ACCOUNTS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1.0"/>
  <title>Company Signal Tracker</title>
  <style>
    *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
      background:#0f1117;color:#e2e8f0;min-height:100vh;display:flex;flex-direction:column}}
    .topbar{{background:#1a1d27;border-bottom:1px solid #2d3148;padding:0 32px;height:56px;
      display:flex;align-items:center;justify-content:space-between}}
    .tl{{display:flex;align-items:center}}
    .brand{{display:flex;align-items:center;gap:10px;text-decoration:none}}
    .brand-icon{{width:32px;height:32px;background:linear-gradient(135deg,#6366f1,#8b5cf6);
      border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px}}
    .brand-name{{font-size:15px;font-weight:700;color:#f1f5f9}}
    .bc{{display:flex;align-items:center;gap:8px;margin-left:16px;padding-left:16px;
      border-left:1px solid #2d3148}}
    .bc a{{font-size:12px;color:#3d4460;text-decoration:none}}
    .bc a:hover{{color:#94a3b8}}
    .bc-sep{{font-size:12px;color:#1e2235}}
    .bc-cur{{font-size:12px;font-weight:600;color:#818cf8}}
    .logout-btn{{background:none;border:1px solid #2d3148;border-radius:6px;
      color:#64748b;font-size:12px;padding:5px 12px;cursor:pointer;
      font-family:inherit;transition:all .15s;text-decoration:none}}
    .logout-btn:hover{{border-color:#ef4444;color:#ef4444}}
    .main{{flex:1;display:flex;flex-direction:column;align-items:center;padding:60px 24px 40px}}
    .heading{{font-size:26px;font-weight:700;color:#f1f5f9;margin-bottom:8px}}
    .sub{{font-size:15px;color:#64748b;margin-bottom:48px}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,340px));
      gap:20px;justify-content:center;width:100%;max-width:760px}}
    .card{{background:#1a1d27;border:1px solid #2d3148;border-radius:16px;
      padding:28px;cursor:pointer;text-decoration:none;
      display:flex;flex-direction:column;gap:16px;
      transition:border-color .2s,box-shadow .2s,transform .15s;
      position:relative;overflow:hidden}}
    .card::before{{content:'';position:absolute;top:0;left:0;right:0;
      height:3px;background:var(--accent);border-radius:16px 16px 0 0}}
    .card:hover{{border-color:var(--accent);box-shadow:0 8px 40px rgba(0,0,0,.4);transform:translateY(-2px)}}
    .card-icon{{width:52px;height:52px;border-radius:14px;
      background:color-mix(in srgb,var(--accent) 15%,transparent);
      border:1px solid color-mix(in srgb,var(--accent) 30%,transparent);
      display:flex;align-items:center;justify-content:center;font-size:26px}}
    .card-name{{font-size:20px;font-weight:700;color:#f1f5f9}}
    .card-desc{{font-size:13px;color:#64748b;line-height:1.5;flex:1}}
    .card-footer{{display:flex;align-items:center;justify-content:space-between;
      border-top:1px solid #2d3148;padding-top:14px}}
    .status{{display:flex;align-items:center;gap:6px;font-size:12px;font-weight:500}}
    .dot{{width:7px;height:7px;border-radius:50%;background:#10b981;
      animation:pulse 2s infinite}}
    @keyframes pulse{{0%,100%{{box-shadow:0 0 0 0 rgba(16,185,129,.4)}}
      50%{{box-shadow:0 0 0 5px rgba(16,185,129,0)}}}}
    .arrow{{font-size:18px;color:var(--accent);opacity:.7;transition:opacity .15s,transform .15s}}
    .card:hover .arrow{{opacity:1;transform:translateX(3px)}}
    .foot{{margin-top:40px;font-size:12px;color:#3d4460}}
  </style>
</head>
<body>
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
    <a href="/logout" class="logout-btn">Sign out</a>
  </div>
  <div class="main">
    <h1 class="heading">Company Signal Tracker</h1>
    <p class="sub">Choose a company list to open</p>
    <div class="grid">{account_cards}</div>
    <p class="foot">Position2 · Internal use only</p>
  </div>
</body>
</html>"""


def _build_account_card(account_id, cfg):
    path = cfg["dashboard"]
    if path.exists():
        count = _read_company_count(path)
        return (
            f'<a class="card" href="/dashboard/{account_id}" style="--accent:{cfg["accent"]}">'
            f'<div class="card-icon">{cfg["icon"]}</div>'
            f'<div><div class="card-name">{cfg["name"]}</div>'
            f'<div class="card-desc">{cfg["description"]}</div></div>'
            f'<div class="card-footer">'
            f'<div class="status"><span class="dot"></span>'
            f'<span style="color:#10b981">{count} companies · Live</span></div>'
            f'<span class="arrow">→</span></div></a>'
        )
    return (
        f'<div class="card" style="--accent:{cfg["accent"]};cursor:default">'
        f'<div class="card-icon">{cfg["icon"]}</div>'
        f'<div><div class="card-name">{cfg["name"]}</div>'
        f'<div class="card-desc">{cfg["description"]}</div></div>'
        f'<div class="card-footer">'
        f'<div class="status"><span style="width:7px;height:7px;border-radius:50%;'
        f'background:#f59e0b;display:inline-block"></span>'
        f'<span style="color:#f59e0b">Not generated yet</span></div>'
        f'</div></div>'
    )


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
