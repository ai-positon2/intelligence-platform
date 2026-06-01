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
  <link rel="preconnect" href="https://fonts.googleapis.com"/>
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&display=swap" rel="stylesheet"/>
  <style>
    *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Space Grotesk',sans-serif;background:#070910;color:#e2e8f0;
      min-height:100vh;display:flex;flex-direction:column;overflow-x:hidden}}
    .bg-grid{{position:fixed;inset:0;z-index:0;pointer-events:none;
      background-image:linear-gradient(rgba(99,102,241,.03) 1px,transparent 1px),
      linear-gradient(90deg,rgba(99,102,241,.03) 1px,transparent 1px);
      background-size:48px 48px}}
    .bg-glow{{position:fixed;border-radius:50%;filter:blur(130px);pointer-events:none;z-index:0;
      width:700px;height:700px;top:-200px;left:-150px;background:rgba(99,102,241,.06)}}
    .topbar{{position:relative;z-index:10;height:62px;padding:0 32px;
      display:flex;align-items:center;justify-content:space-between;
      background:rgba(7,9,16,.8);backdrop-filter:blur(16px);
      border-bottom:1px solid rgba(255,255,255,.05)}}
    .tl{{display:flex;align-items:center}}
    .brand{{display:flex;align-items:center;gap:10px;text-decoration:none}}
    .brand-icon{{width:34px;height:34px;border-radius:9px;
      background:linear-gradient(135deg,#6366f1,#8b5cf6);
      display:flex;align-items:center;justify-content:center;font-size:16px;
      box-shadow:0 0 14px rgba(99,102,241,.3)}}
    .brand-name{{font-size:15px;font-weight:700;color:#f1f5f9}}
    .bc{{display:flex;align-items:center;gap:8px;margin-left:18px;padding-left:18px;
      border-left:1px solid rgba(255,255,255,.07)}}
    .bc a{{font-size:13px;color:#2d3450;text-decoration:none;transition:color .15s}}
    .bc a:hover{{color:#64748b}}
    .bc-sep{{font-size:13px;color:#1a1d27}}
    .bc-cur{{font-size:13px;font-weight:600;color:#818cf8}}
    .sign-out{{font-size:12px;color:#3d4460;text-decoration:none;
      padding:6px 14px;border:1px solid rgba(255,255,255,.07);border-radius:8px;
      transition:all .15s}}
    .sign-out:hover{{color:#ef4444;border-color:rgba(239,68,68,.4)}}
    .main{{flex:1;position:relative;z-index:1;
      display:flex;flex-direction:column;align-items:center;padding:72px 24px 48px}}
    .label{{font-size:11px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
      color:#6366f1;margin-bottom:10px;display:flex;align-items:center;gap:8px}}
    .label::before,.label::after{{content:'';display:block;width:20px;height:1px;background:rgba(99,102,241,.4)}}
    .heading{{font-size:32px;font-weight:700;color:#f1f5f9;letter-spacing:-.02em;
      margin-bottom:6px;text-align:center}}
    .sub{{font-size:14px;color:#2d3450;margin-bottom:52px}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,360px));
      gap:20px;justify-content:center;width:100%;max-width:780px}}
    .card{{background:rgba(13,15,23,.9);border:1px solid rgba(255,255,255,.07);
      border-radius:22px;overflow:hidden;text-decoration:none;color:inherit;
      display:flex;flex-direction:column;
      transition:transform .22s cubic-bezier(.34,1.56,.64,1),box-shadow .22s,border-color .2s}}
    .card:hover{{transform:translateY(-5px);
      box-shadow:0 24px 64px rgba(0,0,0,.55),0 0 0 1px var(--glow)}}
    .card-band{{height:3px;background:var(--accent)}}
    .card-thumb{{height:110px;background:var(--thumb);position:relative;
      display:flex;align-items:center;justify-content:center;overflow:hidden}}
    .card-thumb-icon{{font-size:44px;opacity:.2}}
    .card-thumb::after{{content:'';position:absolute;inset:0;
      background:linear-gradient(to bottom,transparent 30%,rgba(13,15,23,.95) 100%)}}
    .card-badge{{position:absolute;top:10px;right:10px;z-index:1;
      font-size:9px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
      padding:3px 9px;border-radius:999px;display:flex;align-items:center;gap:4px;
      background:rgba(16,185,129,.15);border:1px solid rgba(16,185,129,.3);color:#34d399}}
    .badge-dot{{width:5px;height:5px;border-radius:50%;background:currentColor;
      animation:bpulse 2s infinite}}
    @keyframes bpulse{{0%,100%{{box-shadow:0 0 0 0 rgba(52,211,153,.5)}}
      50%{{box-shadow:0 0 0 3px rgba(52,211,153,0)}}}}
    .card-body{{padding:20px 24px 22px;flex:1;display:flex;flex-direction:column}}
    .card-name{{font-size:20px;font-weight:700;color:#f1f5f9;letter-spacing:-.01em;margin-bottom:8px}}
    .card-desc{{font-size:13px;color:#2d3450;line-height:1.65;flex:1;margin-bottom:20px}}
    .card-footer{{display:flex;align-items:center;justify-content:space-between;
      border-top:1px solid rgba(255,255,255,.05);padding-top:16px}}
    .stat{{font-size:12px;color:#2d3450}}
    .stat span{{color:var(--accent-text);font-weight:600}}
    .arrow{{font-size:16px;color:var(--accent-text);opacity:0;transition:opacity .15s,transform .15s}}
    .card:hover .arrow{{opacity:1;transform:translateX(3px)}}
    .foot{{margin-top:48px;font-size:12px;color:#13151f}}
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
            f'<div class="stat"><span>{count}</span> companies tracked</div>'
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
