"""Who may enter the internal /p2 pages: @position2.com Google accounts, and the one address outside the domain granted by name
(ladhakrishna2022@gmail.com, 2026-11-05, in STAFF_EXTRA_EMAILS). Nobody else, however close the address looks.

Every staff-only door asks the same question (`_is_staff_email`), so this pins the question and then each door: the /p2 gate, sign-in's
landing page and which login log it writes to, the client portals, the template flag, and a sweep of every @position2_required route
turning an outsider away before its view runs.
"""
import ast
import os
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402

GRANTED = "ladhakrishna2022@gmail.com"
OUTSIDERS = ("someone@gmail.com", "ladhakrishna2022@gmail.com.evil.com", "x.ladhakrishna2022@gmail.com", "ladhakrishna2022@googlemail.com",
             "ladhakrishna2022@gmail.co", "someone@position2.com.evil.com", "position2.com@evil.com", "someone@notposition2.com", "", None)
_APP_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


def test_the_granted_address_and_the_domain_are_staff_whatever_the_case():
    for e in (GRANTED, GRANTED.upper(), " Ladhakrishna2022@Gmail.com ", "anyone@position2.com", "Anyone@Position2.com"):
        assert appmod._is_staff_email(e), e
    assert GRANTED in appmod.STAFF_EXTRA_EMAILS and GRANTED in appmod.ADMIN_EMAILS


@pytest.mark.parametrize("email", OUTSIDERS)
def test_no_other_address_is_staff(email):
    assert not appmod._is_staff_email(email)


def _gate(email):
    calls = []

    @appmod.position2_required
    def view():
        calls.append(1)
        return "ok"
    with appmod.app.test_request_context("/p2/probe"):
        from flask import session
        if email:
            session["google_user"] = {"email": email, "name": "T"}
        rv = view()
    return (rv, calls) if isinstance(rv, str) else (rv.headers.get("Location"), calls)


def test_the_p2_gate_lets_the_granted_address_in_and_sends_others_to_app():
    assert _gate(GRANTED) == ("ok", [1]) and _gate(GRANTED.title()) == ("ok", [1])
    loc, calls = _gate("someone@gmail.com")
    assert loc.endswith("/app") and calls == []


def _p2_routes():
    tree = ast.parse(open(_APP_PY, encoding="utf-8").read())
    gated = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and any((d.id if isinstance(d, ast.Name) else getattr(d, "attr", None)) == "position2_required" for d in n.decorator_list)}
    rules = [r for r in appmod.app.url_map.iter_rules() if r.endpoint in gated]
    assert len(rules) > 100, "only %d staff routes found; the parse likely broke" % len(rules)
    return rules


def test_every_staff_route_turns_an_outsider_away_before_its_view():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "someone@gmail.com", "name": "T"}
    leaks = []
    for rule in _p2_routes():
        path = rule.rule
        for arg in rule.arguments:
            path = path.replace("<%s>" % arg, "x")
            for pre in ("int:", "path:", "string:", "float:"):
                path = path.replace("<%s%s>" % (pre, arg), "1" if pre in ("int:", "float:") else "x")
        method = "GET" if "GET" in rule.methods else sorted(rule.methods - {"HEAD", "OPTIONS"})[0]
        r = c.open(path, method=method)
        if not (r.status_code in (301, 302, 303) and r.headers.get("Location", "").endswith("/app")):
            leaks.append((path, r.status_code))
    assert leaks == []


def test_whoami_answers_the_granted_address_as_an_admin():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": GRANTED, "name": "Krishna"}
    j = c.get("/api/whoami").get_json()
    assert j["email"] == GRANTED and j["is_admin"] is True


@pytest.mark.parametrize("email,landing,log", [(GRANTED, "/p2/hub", "internal"), ("someone@gmail.com", "/app", "member"), ("a@position2.com", "/p2/hub", "internal")])
def test_signing_in_lands_staff_on_the_hub_and_logs_them_as_internal(monkeypatch, email, landing, log):
    from google.oauth2 import id_token
    monkeypatch.setattr(id_token, "verify_oauth2_token", lambda cred, req, cid: {"email": email, "name": "N"})
    logged = []
    monkeypatch.setattr(appmod, "_log_login_to_sheet", lambda u: logged.append("internal"))
    monkeypatch.setattr(appmod, "_log_member_signin", lambda u: logged.append("member"))
    r = appmod.app.test_client().post("/auth/google", json={"credential": "x"})
    assert r.get_json()["redirect"] == landing and logged == [log]


def test_the_granted_address_enters_every_client_portal_as_staff():
    client = {"domains": ["acme.com"]}
    assert appmod._client_allowed(client, GRANTED) and appmod._client_allowed(client, "a@acme.com")
    assert not appmod._client_allowed(client, "someone@gmail.com")


def test_pages_are_told_the_granted_address_is_staff():
    seen = []
    for email in (GRANTED, "someone@gmail.com"):
        with appmod.app.test_request_context("/app"):
            from flask import session
            session["google_user"] = {"email": email}
            seen.append(appmod._inject_app_agents()["is_staff"])
    assert seen == [True, False]
    assert "'Position2 team' if is_staff else" in open(os.path.join(os.path.dirname(_APP_PY), "templates", "app_settings.html")).read()
