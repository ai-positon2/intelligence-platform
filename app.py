"""Minimal web server for the Signal Tracker dashboard on Railway."""

import os
from pathlib import Path
from flask import Flask, send_file, abort

app = Flask(__name__)

DASHBOARD = Path(__file__).parent / "reports" / "dashboard.html"


@app.route("/")
def index():
    if not DASHBOARD.exists():
        abort(404, "Dashboard not generated yet — run main.py first.")
    return send_file(str(DASHBOARD))


@app.route("/health")
def health():
    return {"status": "ok", "dashboard_exists": DASHBOARD.exists()}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
