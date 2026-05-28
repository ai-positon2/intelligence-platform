"""Minimal web server for the Signal Tracker dashboard on Railway."""

import os
import json
from pathlib import Path
from functools import wraps
from flask import (
    Flask, send_file, abort, jsonify,
    request, session, redirect, url_for, make_response
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cst-dev-secret-do-not-use-in-prod-abc123xyz")

# ── Account registry ───────────────────────────────────────────────────────────
# Add / rename accounts here. Each entry maps an account_id → config dict.
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

# ── Credentials ────────────────────────────────────────────────────────────────
_VALID_EMAIL    = "krishna.ladha@position2.com"
_VALID_PASSWORD = "signals@P2"

# ── Auth helpers ───────────────────────────────────────────────────────────────
def _check_credentials(email: str, password: str) -> bool:
    return email == _VALID_EMAIL and password == _VALID_PASSWORD

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated

# ── Login page HTML ────────────────────────────────────────────────────────────
_LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Signal Tracker — Login</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #0f1117; color: #e2e8f0;
      min-height: 100vh; display: flex; align-items: center; justify-content: center;
    }
    .card {
      background: #1a1d27; border: 1px solid #2d3148; border-radius: 16px;
      padding: 48px 44px 40px; width: 100%; max-width: 420px;
      box-shadow: 0 24px 64px rgba(0,0,0,0.5);
    }
    .logo { display: flex; align-items: center; gap: 12px; margin-bottom: 32px; }
    .logo-icon {
      width: 40px; height: 40px;
      background: linear-gradient(135deg, #6366f1, #8b5cf6);
      border-radius: 10px; display: flex; align-items: center;
      justify-content: center; font-size: 20px; flex-shrink: 0;
    }
    .logo-title { font-size: 17px; font-weight: 700; color: #f1f5f9; }
    .logo-sub   { font-size: 12px; color: #64748b; margin-top: 2px; }
    h1       { font-size: 22px; font-weight: 700; color: #f1f5f9; margin-bottom: 6px; }
    .subtitle{ font-size: 14px; color: #64748b; margin-bottom: 32px; }
    .field   { margin-bottom: 18px; }
    label    { display: block; font-size: 13px; font-weight: 500; color: #94a3b8; margin-bottom: 7px; }
    input {
      width: 100%; padding: 11px 14px; background: #0f1117;
      border: 1px solid #2d3148; border-radius: 8px; color: #f1f5f9;
      font-size: 14px; outline: none; transition: border-color 0.15s;
    }
    input::placeholder { color: #3d4460; }
    input:focus { border-color: #6366f1; }
    .error-msg {
      background: rgba(239,68,68,.12); border: 1px solid rgba(239,68,68,.35);
      border-radius: 8px; padding: 11px 14px; font-size: 13px; color: #fca5a5;
      margin-bottom: 20px; display: flex; align-items: center; gap: 8px;
    }
    .btn {
      width: 100%; padding: 12px;
      background: linear-gradient(135deg, #6366f1, #8b5cf6);
      border: none; border-radius: 8px; color: #fff;
      font-size: 15px; font-weight: 600; cursor: pointer; margin-top: 8px;
      transition: opacity 0.15s, transform 0.1s; letter-spacing: 0.01em;
    }
    .btn:hover  { opacity: 0.9; }
    .btn:active { transform: scale(0.99); }
    .footer { margin-top: 28px; text-align: center; font-size: 12px; color: #3d4460; }
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">
      <div class="logo-icon">📡</div>
      <div>
        <div class="logo-title">Signal Tracker</div>
        <div class="logo-sub">Position2 · Company Intelligence</div>
      </div>
    </div>
    <h1>Welcome back</h1>
    <p class="subtitle">Sign in to access your signal dashboards</p>
    {error_block}
    <form method="POST" action="/login">
      <input type="hidden" name="next" value="{next_url}" />
      <div class="field">
        <label for="email">Email address</label>
        <input type="email" id="email" name="email"
               placeholder="you@position2.com" value="{prefill_email}"
               autocomplete="email" required />
      </div>
      <div class="field">
        <label for="password">Password</label>
        <input type="password" id="password" name="password"
               placeholder="••••••••••" autocomplete="current-password" required />
      </div>
      <button type="submit" class="btn">Sign in →</button>
    </form>
    <div class="footer">Internal use only · Position2</div>
  </div>
</body>
</html>"""

# ── Account picker page HTML ───────────────────────────────────────────────────
_ACCOUNTS_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Signal Tracker — Select Account</title>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: #0f1117; color: #e2e8f0;
      min-height: 100vh; display: flex; flex-direction: column;
    }

    /* ── Top bar ── */
    .topbar {
      background: #1a1d27; border-bottom: 1px solid #2d3148;
      padding: 0 32px; height: 56px;
      display: flex; align-items: center; justify-content: space-between;
    }
    .topbar-brand { display: flex; align-items: center; gap: 10px; }
    .topbar-icon {
      width: 32px; height: 32px;
      background: linear-gradient(135deg, #6366f1, #8b5cf6);
      border-radius: 8px; display: flex; align-items: center;
      justify-content: center; font-size: 16px;
    }
    .topbar-title { font-size: 15px; font-weight: 700; color: #f1f5f9; }
    .topbar-user  { display: flex; align-items: center; gap: 10px; }
    .user-avatar  {
      width: 32px; height: 32px; border-radius: 8px;
      background: linear-gradient(135deg, #6366f1, #8b5cf6);
      display: flex; align-items: center; justify-content: center;
      font-size: 12px; font-weight: 700; color: #fff;
    }
    .user-name  { font-size: 13px; font-weight: 500; color: #f1f5f9; }
    .logout-btn {
      background: none; border: 1px solid #2d3148; border-radius: 6px;
      color: #64748b; font-size: 12px; padding: 5px 12px; cursor: pointer;
      font-family: inherit; transition: all 0.15s; text-decoration: none;
    }
    .logout-btn:hover { border-color: #ef4444; color: #ef4444; }

    /* ── Main content ── */
    .main {
      flex: 1; display: flex; flex-direction: column;
      align-items: center; padding: 60px 24px 40px;
    }
    .heading     { font-size: 26px; font-weight: 700; color: #f1f5f9; margin-bottom: 8px; }
    .subheading  { font-size: 15px; color: #64748b; margin-bottom: 48px; }

    /* ── Account cards ── */
    .accounts-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 340px));
      gap: 20px; justify-content: center; width: 100%; max-width: 760px;
    }
    .account-card {
      background: #1a1d27; border: 1px solid #2d3148; border-radius: 16px;
      padding: 28px 28px 24px; cursor: pointer; text-decoration: none;
      display: flex; flex-direction: column; gap: 16px;
      transition: border-color 0.2s, box-shadow 0.2s, transform 0.15s;
      position: relative; overflow: hidden;
    }
    .account-card::before {
      content: ''; position: absolute; top: 0; left: 0; right: 0;
      height: 3px; background: var(--accent); border-radius: 16px 16px 0 0;
    }
    .account-card:hover {
      border-color: var(--accent);
      box-shadow: 0 8px 40px rgba(0,0,0,0.4);
      transform: translateY(-2px);
    }
    .card-icon {
      width: 52px; height: 52px; border-radius: 14px;
      background: color-mix(in srgb, var(--accent) 15%, transparent);
      border: 1px solid color-mix(in srgb, var(--accent) 30%, transparent);
      display: flex; align-items: center; justify-content: center; font-size: 26px;
    }
    .card-name   { font-size: 20px; font-weight: 700; color: #f1f5f9; }
    .card-desc   { font-size: 13px; color: #64748b; line-height: 1.5; flex: 1; }
    .card-footer {
      display: flex; align-items: center; justify-content: space-between;
      border-top: 1px solid #2d3148; padding-top: 14px; margin-top: 4px;
    }
    .card-status {
      display: flex; align-items: center; gap: 6px;
      font-size: 12px; font-weight: 500;
    }
    .status-dot {
      width: 7px; height: 7px; border-radius: 50%;
    }
    .status-dot.live   { background: #10b981; box-shadow: 0 0 0 0 rgba(16,185,129,.4); animation: pulse 2s infinite; }
    .status-dot.setup  { background: #f59e0b; }
    @keyframes pulse {
      0%,100% { box-shadow: 0 0 0 0 rgba(16,185,129,.4); }
      50%      { box-shadow: 0 0 0 5px rgba(16,185,129,0); }
    }
    .card-arrow {
      font-size: 18px; color: var(--accent); opacity: 0.7;
      transition: opacity 0.15s, transform 0.15s;
    }
    .account-card:hover .card-arrow { opacity: 1; transform: translateX(3px); }

    /* ── Not-ready overlay ── */
    .account-card.not-ready { cursor: default; }
    .account-card.not-ready:hover { transform: none; box-shadow: none; }
    .not-ready-badge {
      position: absolute; top: 16px; right: 16px;
      background: rgba(245,158,11,.15); border: 1px solid rgba(245,158,11,.3);
      color: #f59e0b; font-size: 10px; font-weight: 700; border-radius: 999px;
      padding: 3px 10px; letter-spacing: .04em;
    }

    .footer-note { margin-top: 40px; font-size: 12px; color: #3d4460; }
  </style>
</head>
<body>

  <!-- Top bar -->
  <div class="topbar">
    <div class="topbar-brand">
      <div class="topbar-icon">📡</div>
      <span class="topbar-title">Signal Tracker</span>
    </div>
    <div class="topbar-user">
      <div class="user-avatar">KL</div>
      <span class="user-name">Krishna Ladha</span>
      <a href="/logout" class="logout-btn">Sign out</a>
    </div>
  </div>

  <!-- Main -->
  <div class="main">
    <h1 class="heading">Select an Account</h1>
    <p class="subheading">Choose which intelligence dashboard you want to open</p>

    <div class="accounts-grid">
      {account_cards}
    </div>

    <p class="footer-note">Position2 · Internal use only</p>
  </div>

</body>
</html>"""

def _build_account_card(account_id: str, cfg: dict) -> str:
    dashboard_path: Path = cfg["dashboard"]
    is_ready = dashboard_path.exists()

    if is_ready:
        # Read company count from the generated dashboard meta if possible
        company_count = _read_company_count(dashboard_path)
        status_html = f"""<span class="status-dot live"></span>
          <span style="color:#10b981">{company_count} companies · Live</span>"""
        card_cls  = ""
        badge_html = ""
        href_attr = f'href="/dashboard/{account_id}"'
        arrow = f'<span class="card-arrow">→</span>'
    else:
        status_html = '<span class="status-dot setup"></span><span style="color:#f59e0b">Setup required</span>'
        card_cls   = "not-ready"
        badge_html = '<span class="not-ready-badge">NOT SET UP</span>'
        href_attr  = ""
        arrow = ""

    tag = "a" if is_ready else "div"

    return f"""
    <{tag} class="account-card {card_cls}" {href_attr}
        style="--accent:{cfg['accent']}">
      {badge_html}
      <div class="card-icon">{cfg['icon']}</div>
      <div>
        <div class="card-name">{cfg['name']}</div>
        <div class="card-desc">{cfg['description']}</div>
      </div>
      <div class="card-footer">
        <div class="card-status">{status_html}</div>
        {arrow}
      </div>
    </{tag}>"""


def _read_company_count(dashboard_path: Path) -> str:
    """Quick scan of the generated HTML to extract total_companies from embedded JSON."""
    try:
        text = dashboard_path.read_text(encoding="utf-8", errors="ignore")
        marker = '"total_companies":'
        idx = text.find(marker)
        if idx == -1:
            return "—"
        snippet = text[idx + len(marker):idx + len(marker) + 10].strip().split(",")[0].strip()
        return snippet if snippet.isdigit() else "—"
    except Exception:
        return "—"


# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("accounts"))

    error_block   = ""
    prefill_email = ""
    next_url      = request.args.get("next", "/accounts") or "/accounts"

    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        next_url = request.form.get("next", "/accounts") or "/accounts"

        if _check_credentials(email, password):
            session["logged_in"] = True
            session["user"]      = email
            session.permanent    = True
            if not next_url.startswith("/"):
                next_url = "/accounts"
            return redirect(next_url)
        else:
            prefill_email = email
            error_block = (
                '<div class="error-msg">'
                '⚠ Incorrect email or password. Please try again.'
                '</div>'
            )

    html = (_LOGIN_HTML
            .replace("{error_block}",   error_block)
            .replace("{next_url}",      next_url)
            .replace("{prefill_email}", prefill_email))
    return make_response(html, 200)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    return redirect(url_for("accounts"))


@app.route("/accounts")
@login_required
def accounts():
    cards_html = "".join(
        _build_account_card(aid, cfg) for aid, cfg in ACCOUNTS.items()
    )
    html = _ACCOUNTS_HTML_TEMPLATE.replace("{account_cards}", cards_html)
    return make_response(html, 200)


@app.route("/dashboard/<account_id>")
@login_required
def dashboard(account_id: str):
    cfg = ACCOUNTS.get(account_id)
    if cfg is None:
        abort(404, f"Unknown account '{account_id}'")
    dashboard_path: Path = cfg["dashboard"]
    if not dashboard_path.exists():
        abort(404, f"Dashboard for '{cfg['name']}' not generated yet — run main.py first.")
    response = make_response(send_file(str(dashboard_path)))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/health")
def health():
    """Public health check — no auth required."""
    return jsonify({
        "status": "ok",
        "accounts": {
            aid: {"name": cfg["name"], "dashboard_exists": cfg["dashboard"].exists()}
            for aid, cfg in ACCOUNTS.items()
        }
    })


@app.route("/api/weekly-stats")
@app.route("/api/weekly-stats/<account_id>")
@login_required
def weekly_stats(account_id: str = "healthcare"):
    cfg = ACCOUNTS.get(account_id)
    if cfg is None:
        return jsonify({"error": f"Unknown account '{account_id}'"}), 404
    stats_json = Path(__file__).parent / "data" / f"weekly-stats-{account_id}.json"
    # Fall back to legacy filename for healthcare account
    if not stats_json.exists() and account_id == "healthcare":
        stats_json = Path(__file__).parent / "data" / "weekly-stats.json"
    if not stats_json.exists():
        return jsonify({"error": f"weekly-stats for '{account_id}' not found — run main.py first"}), 503
    return jsonify(json.loads(stats_json.read_text()))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
