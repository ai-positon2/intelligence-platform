"""Minimal web server for the Signal Tracker dashboard on Railway."""

import os
import hashlib
from pathlib import Path
from functools import wraps
from flask import (
    Flask, send_file, abort, jsonify,
    request, session, redirect, url_for, make_response
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "cst-dev-secret-do-not-use-in-prod-abc123xyz")

DASHBOARD = Path(__file__).parent / "reports" / "dashboard.html"

# ── Credentials ───────────────────────────────────────────────────────────────
_VALID_EMAIL    = "krishna.ladha@position2.com"
_VALID_PASSWORD = "signals@P2"

# ── Auth helpers ──────────────────────────────────────────────────────────────
def _check_credentials(email: str, password: str) -> bool:
    return email == _VALID_EMAIL and password == _VALID_PASSWORD

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated

# ── Login page HTML ───────────────────────────────────────────────────────────
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
      background: #0f1117;
      color: #e2e8f0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
    }

    .card {
      background: #1a1d27;
      border: 1px solid #2d3148;
      border-radius: 16px;
      padding: 48px 44px 40px;
      width: 100%;
      max-width: 420px;
      box-shadow: 0 24px 64px rgba(0,0,0,0.5);
    }

    .logo {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 32px;
    }

    .logo-icon {
      width: 40px;
      height: 40px;
      background: linear-gradient(135deg, #6366f1, #8b5cf6);
      border-radius: 10px;
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: 20px;
      flex-shrink: 0;
    }

    .logo-text {
      line-height: 1.2;
    }

    .logo-title {
      font-size: 17px;
      font-weight: 700;
      color: #f1f5f9;
    }

    .logo-sub {
      font-size: 12px;
      color: #64748b;
      margin-top: 2px;
    }

    h1 {
      font-size: 22px;
      font-weight: 700;
      color: #f1f5f9;
      margin-bottom: 6px;
    }

    .subtitle {
      font-size: 14px;
      color: #64748b;
      margin-bottom: 32px;
    }

    .field {
      margin-bottom: 18px;
    }

    label {
      display: block;
      font-size: 13px;
      font-weight: 500;
      color: #94a3b8;
      margin-bottom: 7px;
      letter-spacing: 0.01em;
    }

    input {
      width: 100%;
      padding: 11px 14px;
      background: #0f1117;
      border: 1px solid #2d3148;
      border-radius: 8px;
      color: #f1f5f9;
      font-size: 14px;
      outline: none;
      transition: border-color 0.15s;
    }

    input::placeholder { color: #3d4460; }

    input:focus { border-color: #6366f1; }

    .error-msg {
      background: rgba(239, 68, 68, 0.12);
      border: 1px solid rgba(239, 68, 68, 0.35);
      border-radius: 8px;
      padding: 11px 14px;
      font-size: 13px;
      color: #fca5a5;
      margin-bottom: 20px;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .btn {
      width: 100%;
      padding: 12px;
      background: linear-gradient(135deg, #6366f1, #8b5cf6);
      border: none;
      border-radius: 8px;
      color: #fff;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
      margin-top: 8px;
      transition: opacity 0.15s, transform 0.1s;
      letter-spacing: 0.01em;
    }

    .btn:hover  { opacity: 0.9; }
    .btn:active { transform: scale(0.99); }

    .footer {
      margin-top: 28px;
      text-align: center;
      font-size: 12px;
      color: #3d4460;
    }
  </style>
</head>
<body>
  <div class="card">

    <div class="logo">
      <div class="logo-icon">📡</div>
      <div class="logo-text">
        <div class="logo-title">Signal Tracker</div>
        <div class="logo-sub">Position2 · Healthcare Intelligence</div>
      </div>
    </div>

    <h1>Welcome back</h1>
    <p class="subtitle">Sign in to access your signal dashboard</p>

    {error_block}

    <form method="POST" action="/login">
      <input type="hidden" name="next" value="{next_url}" />

      <div class="field">
        <label for="email">Email address</label>
        <input
          type="email"
          id="email"
          name="email"
          placeholder="you@position2.com"
          value="{prefill_email}"
          autocomplete="email"
          required
        />
      </div>

      <div class="field">
        <label for="password">Password</label>
        <input
          type="password"
          id="password"
          name="password"
          placeholder="••••••••••"
          autocomplete="current-password"
          required
        />
      </div>

      <button type="submit" class="btn">Sign in →</button>
    </form>

    <div class="footer">Internal use only · Position2</div>
  </div>
</body>
</html>"""

# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/login", methods=["GET", "POST"])
def login():
    # Already logged in → go home
    if session.get("logged_in"):
        return redirect(url_for("index"))

    error_block   = ""
    prefill_email = ""
    next_url      = request.args.get("next", "/") or "/"

    if request.method == "POST":
        email    = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        next_url = request.form.get("next", "/") or "/"

        if _check_credentials(email, password):
            session["logged_in"] = True
            session["user"]      = email
            session.permanent    = True
            # Safety: only allow relative redirects
            if not next_url.startswith("/"):
                next_url = "/"
            return redirect(next_url)
        else:
            prefill_email = email
            error_block = (
                '<div class="error-msg">'
                '⚠ Incorrect email or password. Please try again.'
                '</div>'
            )

    html = _LOGIN_HTML.replace("{error_block}", error_block) \
                      .replace("{next_url}",    next_url) \
                      .replace("{prefill_email}", prefill_email)
    return make_response(html, 200)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    if not DASHBOARD.exists():
        abort(404, "Dashboard not generated yet — run main.py first.")
    return send_file(str(DASHBOARD))


@app.route("/health")
def health():
    """Public health check — no auth required."""
    return {"status": "ok", "dashboard_exists": DASHBOARD.exists()}


@app.route("/api/weekly-stats")
@login_required
def weekly_stats():
    """Return signal counts for the last 7 days."""
    stats_json = Path(__file__).parent / "data" / "weekly-stats.json"
    if not stats_json.exists():
        return jsonify({"error": "weekly-stats.json not found — run main.py first"}), 503
    import json
    return jsonify(json.loads(stats_json.read_text()))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
