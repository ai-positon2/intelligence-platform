"""Who is an admin, and does that one set really govern every admin surface.

ADMIN_EMAILS is documented as the single source of truth: `admin_required`
gates every /p2/admin/* route off it, the template context processor derives
`is_admin` from it, and /api/whoami serves the same flag so client-rendered
surfaces stop keeping their own hardcoded lists. Documented is not the same as
true, and this repo has been bitten before by a roster living in several
independent places that drift apart, so this file proves the claim by
exercising it rather than by reading it.

Route bodies are never executed. Every assertion here lands on the decorator,
which runs before the view -- so the sweep covers all of the admin routes
without needing a database, Google Sheets, or any vendor key.
"""

import ast
import os
import sys

from werkzeug.exceptions import Forbidden

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402

_NEW_ADMIN = "sangeeta@position2.com"
_STAFF = "not-an-admin@position2.com"          # real Position2 login, no admin rights
_EXTERNAL = "someone@example.com"              # a public /app member
_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def _client(email=None):
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


def _admin_routes():
    """Every URL rule whose view function is decorated with @admin_required.

    Read off the source rather than off the wrapper: admin_required uses
    @wraps, so the decorated function is indistinguishable from an undecorated
    one at runtime. Parsing the decorator list is what makes this sweep
    self-extending -- an admin route added next year is covered the day it
    lands, without anyone remembering to list it here.
    """
    tree = ast.parse(open(_APP_PY, encoding="utf-8").read())
    gated = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            name = dec.id if isinstance(dec, ast.Name) else getattr(dec, "attr", None)
            if name == "admin_required":
                gated.add(node.name)
    rules = [r for r in appmod.app.url_map.iter_rules() if r.endpoint in gated]
    assert rules, "found no @admin_required routes at all, so this file proves nothing"
    return rules


def _get(rule, email):
    """Request a rule with any URL parameters filled in with throwaway values.

    The values never matter: the gate answers before the view is entered.
    """
    path = rule.rule
    for arg in rule.arguments:
        conv = type(rule._converters[arg]).__name__.lower()
        stub = "1" if "integer" in conv or "float" in conv else "x"
        path = path.replace("<%s>" % arg, stub)
        for prefix in ("int:", "float:", "path:", "string:"):
            path = path.replace("<%s%s>" % (prefix, arg), stub)
    method = "GET" if "GET" in rule.methods else sorted(rule.methods - {"HEAD", "OPTIONS"})[0]
    return _client(email).open(path, method=method)


# ── The roster ─────────────────────────────────────────────────────────────

def test_the_new_admin_is_on_the_roster_and_the_roster_is_all_lowercase():
    """Every gate lowercases the session email before the membership test, so
    an entry carrying a capital letter would be an admin who is never an
    admin -- silently, and only for that one person."""
    assert _NEW_ADMIN in appmod.ADMIN_EMAILS
    assert all(e == e.lower() for e in appmod.ADMIN_EMAILS), sorted(appmod.ADMIN_EMAILS)
    assert all(e.endswith("@position2.com") for e in appmod.ADMIN_EMAILS)


# ── The gate itself ────────────────────────────────────────────────────────

def _call_gate(email):
    """Run admin_required on a throwaway view inside a real request context.

    Deliberately not a registered route: adding one mutates the app's url_map
    for every test that runs after this file, and Werkzeug gives no supported
    way to take it back off again.
    """
    calls = []

    @appmod.admin_required
    def view():
        calls.append(1)
        return "ok"

    with appmod.app.test_request_context("/p2/admin/probe"):
        from flask import session
        if email:
            session["google_user"] = {"email": email, "name": "T"}
        try:
            rv = view()
        except Forbidden:
            return 403, None, calls
        if isinstance(rv, str):
            return 200, None, calls
        return rv.status_code, rv.headers.get("Location"), calls


def test_admin_required_lets_the_new_admin_through_and_stops_everyone_else():
    status, _, calls = _call_gate(_NEW_ADMIN)
    assert (status, calls) == (200, [1]), "the new admin never reached the view"

    assert _call_gate(_STAFF)[:1] == (403,), "a non-admin Position2 staffer got in"
    assert _call_gate(_STAFF)[2] == [], "the view body ran for a caller who was refused"

    # An external member never sees an internal page at all, admin or not, and
    # is redirected rather than shown a 403 they cannot act on.
    status, loc, calls = _call_gate(_EXTERNAL)
    assert status in (301, 302, 303) and loc.endswith("/app")
    assert calls == []

    # Signed out: sent to log in, not 403'd, so the reason is legible.
    status, loc, calls = _call_gate(None)
    assert status in (301, 302, 303)
    assert not loc.endswith("/app")
    assert calls == []


def test_the_gate_is_case_insensitive_about_the_address_google_sends():
    """Google decides the casing of the email in the token, not us. An admin
    signing in as Sangeeta@position2.com is the same person."""
    assert _call_gate(_NEW_ADMIN.upper())[0] == 200
    assert _call_gate(_NEW_ADMIN.capitalize())[0] == 200


# ── Every admin route, swept ───────────────────────────────────────────────

def test_every_admin_route_is_governed_by_admin_emails_and_nothing_else(monkeypatch):
    """The sweep that makes "she has access to everything" a fact.

    For each admin route: a Position2 staffer who is not on the roster is
    refused, and so is the new admin the moment she is taken off it. Since
    membership in that one set is the only thing separating those two calls,
    every route that refuses her without it admits her with it -- proved
    without executing a single view body, which is what keeps a 30-route
    sweep from needing a database and seven vendor keys.
    """
    rules = _admin_routes()
    assert len(rules) >= 25, "only %d admin routes found; the parse likely broke" % len(rules)

    refused_staff, admitted_off_roster = [], []
    for rule in rules:
        if _get(rule, _STAFF).status_code != 403:
            refused_staff.append(rule.rule)

    without_her = set(appmod.ADMIN_EMAILS) - {_NEW_ADMIN}
    monkeypatch.setattr(appmod, "ADMIN_EMAILS", without_her)
    for rule in rules:
        if _get(rule, _NEW_ADMIN).status_code != 403:
            admitted_off_roster.append(rule.rule)

    assert refused_staff == [], "not gated by ADMIN_EMAILS: %s" % refused_staff
    assert admitted_off_roster == [], (
        "these routes admit her through some gate other than ADMIN_EMAILS, so "
        "removing her from it would not remove her access: %s" % admitted_off_roster)


def test_no_route_under_p2_admin_escapes_the_gate():
    """The sweep above can only test the routes it finds. A route that loses
    its @admin_required decorator disappears from that list rather than
    failing in it, so the hole would be invisible -- this asserts the other
    direction: everything under /p2/admin/ is either gated itself, or is a
    bare 301 onto something that is.

    Nine of these are the pre-rename URLs (/p2/admin/members, /p2/admin/usage
    and friends). They are correct as they stand -- a redirect to a gated page
    hands out nothing, and the gate answers on arrival -- but "it only
    redirects" is a claim worth checking rather than reading, so the redirect
    is followed as the least privileged caller there is: someone who is not
    Position2 staff at all.
    """
    gated = {r.rule for r in _admin_routes()}
    under_admin = {r.rule for r in appmod.app.url_map.iter_rules()
                   if r.rule.startswith("/p2/admin/")}
    assert under_admin, "no /p2/admin/ routes found at all, so this proves nothing"

    leaks = []
    for rule in sorted(under_admin - gated):
        resp = _client(_EXTERNAL).get(rule)
        if resp.status_code not in (301, 302, 303):
            leaks.append("%s answered %d instead of redirecting" % (rule, resp.status_code))
            continue
        target = resp.headers.get("Location", "").split("?")[0]
        if target not in gated:
            leaks.append("%s redirects to %r, which is not itself gated" % (rule, target))
        # A redirect is a header, not a page: it must carry no payload.
        if resp.get_data():
            body = resp.get_data(as_text=True)
            if len(body) > 400 or "<table" in body:
                leaks.append("%s returned a body with its redirect" % rule)
    assert leaks == [], leaks


# ── The flags the client reads ─────────────────────────────────────────────

def test_whoami_reports_the_new_admin_as_an_admin():
    """The legacy signal dashboards and the Ad Intelligence bundle render
    their admin links off this flag, not off a server-side template branch."""
    body = _client(_NEW_ADMIN).get("/api/whoami").get_json()
    assert body["is_admin"] is True
    assert body["email"] == _NEW_ADMIN
    assert _client(_STAFF).get("/api/whoami").get_json()["is_admin"] is False


def test_the_template_wide_is_admin_flag_follows_the_same_set():
    """Pages show or hide the admin analytics menu on this one value. If it
    ever disagreed with admin_required, a link would be visible and then
    403 -- or, worse, invisible to someone who does have access."""
    with appmod.app.test_request_context("/p2/hub"):
        from flask import session
        session["google_user"] = {"email": _NEW_ADMIN, "name": "T"}
        assert appmod._inject_app_agents()["is_admin"] is True
        session["google_user"] = {"email": _STAFF, "name": "T"}
        assert appmod._inject_app_agents()["is_admin"] is False
        # Casing comes from Google, not from us: an admin who signs in as
        # Sangeeta@... is the same person and must get the same flag.
        session["google_user"] = {"email": _NEW_ADMIN.capitalize(), "name": "T"}
        assert appmod._inject_app_agents()["is_admin"] is True
