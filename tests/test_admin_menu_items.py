"""What the shared admin dropdown lists. Market Radar is hidden from it on purpose (2026-10-29): its page
still works at /p2/admin/market-radar, only the menu link is gone."""
import os
import sys

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402

ADMIN = sorted(appmod.ADMIN_EMAILS)[0]


def _menu(email):
    with appmod.app.test_request_context("/"):
        from flask import session
        session["google_user"] = {"email": email, "name": "T"}
        return appmod.render_template("_admin_menu.html", is_admin=email in appmod.ADMIN_EMAILS)


def test_the_menu_does_not_link_market_radar():
    html = _menu(ADMIN)
    assert "/p2/admin/market-radar" not in html and "Market Radar" not in html


def test_the_rest_of_the_admin_menu_is_still_there():
    html = _menu(ADMIN)
    for link in ("internal-usage", "external-usage", "client-usage", "agent-feedback", "anonymous-traffic",
                 "public-page-analytics", "public-agent-usage", "access-requests"):
        assert "/p2/admin/" + link in html


def test_the_market_radar_page_is_still_an_admin_route():
    assert any(r.rule == "/p2/admin/market-radar" for r in appmod.app.url_map.iter_rules())
