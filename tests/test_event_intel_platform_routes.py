"""Event & Conference Intelligence routes: forgery, re-billing, paid lookups.

Each attack is paired with the legitimate request it must not break. The
database-backed tests reproduce the audit's proof of concept: a form POST
from another origin to the billed resolve route, twice.
"""
import os
import sys
import uuid

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
os.environ.setdefault("GOOGLE_CLIENT_ID", "test")

import app as appmod  # noqa: E402
from tracker import event_intel_store as store  # noqa: E402
from tracker import sci_company_search  # noqa: E402

BASE = "/p2/strategic-agents/event-conference-intelligence"
EVIL = {"Origin": "https://evil.example", "Referer": "https://evil.example/x"}
SAME = {"Origin": "http://localhost", "Referer": "http://localhost" + BASE}

sql = pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="requires disposable PostgreSQL")

POST_ROUTES = ["/run", "/runs/1/cancel", "/runs/1/resolve", "/runs/1/plan", "/outcomes",
               "/profiles", "/profiles/1", "/profiles/draft"]


def _client(email="guard@position2.com"):
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


@pytest.fixture(autouse=True)
def _fresh_limits():
    for key in ("evi-search", "evi-resolve", "evi-draft-profile"):
        appmod._CPI_RATE_STATE.pop(key, None)
    appmod._EVI_SEARCH_CACHE.clear()
    yield
    for key in ("evi-search", "evi-resolve", "evi-draft-profile"):
        appmod._CPI_RATE_STATE.pop(key, None)
    appmod._EVI_SEARCH_CACHE.clear()


# ── the guard in front of every POST ──────────────────────────────────────

@pytest.mark.parametrize("path", POST_ROUTES)
def test_a_cross_site_form_post_is_refused_on_every_route(path):
    r = _client().post(BASE + path, data={"a": "b"}, headers=EVIL)
    assert r.status_code == 403, (path, r.status_code)


@pytest.mark.parametrize("path", POST_ROUTES)
def test_a_form_post_is_refused_even_without_an_origin(path):
    r = _client().post(BASE + path, data={"a": "b"})
    assert r.status_code == 415, (path, r.status_code)


@pytest.mark.parametrize("ctype", ["text/plain", "multipart/form-data; boundary=x"])
def test_the_other_form_encodings_are_refused(ctype):
    r = _client().post(BASE + "/runs/1/cancel", data="x=1", headers={"Content-Type": ctype})
    assert r.status_code == 415


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "null"},
    {"Referer": "https://evil.example/page"},
    {"Origin": "http://localhost.evil.example"},
])
def test_json_from_another_origin_is_refused(headers):
    r = _client().post(BASE + "/run", json={"mode": "sideways"}, headers=headers)
    assert r.status_code == 403


@pytest.mark.parametrize("headers", [SAME, {"Origin": "https://localhost"},
                                     {"Referer": "http://localhost/p2/x"}, {}])
def test_a_same_origin_json_post_still_reaches_the_route(headers):
    r = _client().post(BASE + "/run", json={"mode": "sideways"}, headers=headers)
    assert r.status_code == 400 and r.get_json()["error"] == "Unknown mode."


def test_the_pages_own_bodyless_cancel_still_reaches_the_route(monkeypatch):
    """The page cancels with fetch(url, {method: 'POST'}): no body and no
    Content-Type, which no HTML form can produce."""
    monkeypatch.setattr(store, "get_run", lambda run_id, email: None)
    r = _client().post(BASE + "/runs/7/cancel", headers=SAME)
    assert r.status_code == 404  # reached the route, which found no such run


def test_a_logged_out_form_post_is_still_sent_to_sign_in():
    r = _client(None).post(BASE + "/runs/1/resolve", data={"a": "b"}, headers=EVIL)
    assert r.status_code in (301, 302)


def test_other_agents_posts_are_untouched():
    """The guard is scoped to this agent's prefix; a form POST elsewhere is
    whatever that route makes of it, never this guard's 415."""
    r = _client().post("/p2/strategic-agents/company-people-intelligence/parse-query",
                       data={"q": "x"}, headers=EVIL)
    assert b"came from another site" not in r.data and b"needs a JSON request" not in r.data


def test_the_session_cookie_is_samesite_lax():
    from flask.sessions import SecureCookieSession
    assert appmod.app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
    session = SecureCookieSession({"google_user": {"email": "cookie@position2.com"}})
    session.modified = True
    resp = appmod.app.response_class()
    with appmod.app.test_request_context("/"):
        appmod.app.session_interface.save_session(appmod.app, session, resp)
    assert "SameSite=Lax" in resp.headers.get("Set-Cookie", "")


# ── resolve: rate limit and no re-billing ────────────────────────────────

def test_resolve_refuses_a_run_still_in_progress(monkeypatch):
    from tracker import event_intel_pipeline as P
    monkeypatch.setattr(store, "get_run", lambda run_id, email: {"id": run_id, "status": "running"})
    monkeypatch.setattr(P, "resolve_run_companies", lambda *a, **k: pytest.fail("billed a running run"))
    r = _client().post(BASE + "/runs/3/resolve", json={})
    assert r.status_code == 409


def test_resolve_is_rate_limited(monkeypatch):
    from tracker import event_intel_pipeline as P
    monkeypatch.setattr(store, "get_run", lambda run_id, email: {"id": run_id, "status": "complete"})
    monkeypatch.setattr(store, "begin_resolution", lambda *a: ("go", None))
    monkeypatch.setattr(store, "get_participants", lambda run_id: [])
    monkeypatch.setattr(store, "finish_resolution", lambda *a, **k: None)
    monkeypatch.setattr(P, "resolve_run_companies", lambda *a, **k: {"credits": 0})
    c = _client("resolve-limit@position2.com")
    limit = appmod._CPI_RATE_LIMITS["evi-resolve"][0]
    codes = [c.post(BASE + "/runs/3/resolve", json={}).status_code for _ in range(limit + 1)]
    assert codes[:limit] == [200] * limit and codes[limit] == 429


def _complete_run_with_roster(email):
    from tracker import event_intel_jobs as J
    rid = J.start(email, "lookup", "Forum", {}, uuid.uuid4().hex)
    with J.db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE evi_runs SET status='complete',stage='done' WHERE id=%s", (rid,))
        cur.execute("UPDATE evi_jobs SET state='complete' WHERE run_id=%s", (rid,))
        cur.execute("INSERT INTO evi_events(run_id,name) VALUES (%s,'Forum') RETURNING id", (rid,))
        eid = cur.fetchone()[0]
        cur.execute("""INSERT INTO evi_participants(run_id,event_id,org_name,org_domain,role,source_url)
            VALUES (%s,%s,'Acme','acme.example','exhibitor','https://forum.example/x')""", (rid, eid))
    return rid


@pytest.fixture
def billing(monkeypatch):
    from tracker import event_intel_enrich as E
    bills = []
    monkeypatch.setattr(E, "resolve_companies", lambda domains, **k: bills.append(domains) or {
        "by_domain": {"acme.example": {"name": "Acme"}}, "credits": 1, "unmatched": [], "error": None})
    monkeypatch.setattr(E, "find_people", lambda domains, titles=None, **k: {
        "by_domain": {}, "total": 0, "error": None})
    return bills


@sql
def test_the_audit_poc_a_cross_site_form_post_no_longer_bills(billing):
    email = "poc-" + uuid.uuid4().hex + "@position2.com"
    rid = _complete_run_with_roster(email)
    c = _client(email)
    for _ in range(2):
        r = c.post(BASE + "/runs/%d/resolve" % rid, data={"a": "b"}, headers=EVIL)
        assert r.status_code == 403
    assert billing == []
    assert store.get_run(rid, email)["credits_spent"] == 0


@sql
def test_a_repeat_resolve_returns_the_stored_result_without_billing(billing):
    email = "rebill-" + uuid.uuid4().hex + "@position2.com"
    rid = _complete_run_with_roster(email)
    c = _client(email)
    first = c.post(BASE + "/runs/%d/resolve" % rid, json={}, headers=SAME)
    assert first.status_code == 200 and first.get_json()["credits"] == 1
    again = c.post(BASE + "/runs/%d/resolve" % rid, json={"titles": []}, headers=SAME)
    body = again.get_json()
    assert again.status_code == 200 and body["already_resolved"] and body["credits"] == 0
    assert len(billing) == 1
    assert store.get_run(rid, email)["credits_spent"] == 1
    # Same titles in another order and case are the same request.
    c.post(BASE + "/runs/%d/resolve" % rid, json={"titles": ["CMO", "VP Marketing"]}, headers=SAME)
    assert len(billing) == 2
    c.post(BASE + "/runs/%d/resolve" % rid, json={"titles": ["vp marketing", "cmo"]}, headers=SAME)
    assert len(billing) == 2, "a reordered title list billed again"
    assert store.get_run(rid, email)["credits_spent"] == 2


@sql
def test_a_run_matched_before_the_record_existed_is_not_billed_again(billing):
    from tracker import event_intel_jobs as J
    email = "legacy-" + uuid.uuid4().hex + "@position2.com"
    rid = _complete_run_with_roster(email)
    with J.db() as conn, conn.cursor() as cur:
        cur.execute("UPDATE evi_participants SET resolution='matched' WHERE run_id=%s", (rid,))
    r = _client(email).post(BASE + "/runs/%d/resolve" % rid, json={})
    assert r.status_code == 200 and r.get_json()["already_resolved"]
    assert billing == []


@sql
def test_a_match_in_progress_is_not_started_twice(billing):
    email = "busy-" + uuid.uuid4().hex + "@position2.com"
    rid = _complete_run_with_roster(email)
    assert store.begin_resolution(rid, email, "[]")[0] == "go"   # another request is mid-match
    r = _client(email).post(BASE + "/runs/%d/resolve" % rid, json={})
    assert r.status_code == 409 and billing == []


@sql
def test_a_failed_match_may_be_retried(billing, monkeypatch):
    from tracker import event_intel_enrich as E
    email = "retry-" + uuid.uuid4().hex + "@position2.com"
    rid = _complete_run_with_roster(email)
    monkeypatch.setattr(E, "resolve_companies", lambda domains, **k: billing.append(domains) or {
        "by_domain": {}, "credits": 0, "unmatched": [], "unattempted": domains, "error": "HTTP 503"})
    c = _client(email)
    c.post(BASE + "/runs/%d/resolve" % rid, json={})
    c.post(BASE + "/runs/%d/resolve" % rid, json={})
    assert len(billing) == 2


# ── the typeahead search ──────────────────────────────────────────────────

def _apollo(monkeypatch, companies):
    calls = []
    monkeypatch.setattr(sci_company_search, "search_companies_result", lambda q: calls.append(q) or {
        "companies": companies, "error": None, "elapsed_ms": 1, "source": "mixed_companies/search"})
    return calls


def test_a_one_character_query_never_reaches_apollo(monkeypatch):
    calls = _apollo(monkeypatch, [{"name": "A"}])
    assert _client().get(BASE + "/search?q=a").get_json() == {"companies": []}
    assert calls == []


def test_a_repeated_query_is_answered_from_the_cache(monkeypatch):
    calls = _apollo(monkeypatch, [{"name": "Northwind"}])
    c = _client()
    first = c.get(BASE + "/search?q=Northwind").get_json()
    second = c.get(BASE + "/search?q=%20northwind%20%20").get_json()
    assert calls == ["Northwind"]
    assert second["companies"] == first["companies"] and second["cached"]


def test_a_failed_search_is_not_cached(monkeypatch):
    monkeypatch.setattr(store, "search_known_profiles", lambda email, q: [])
    calls = []
    monkeypatch.setattr(sci_company_search, "search_companies_result", lambda q: calls.append(q) or {
        "companies": [], "error": {"kind": "timeout", "detail": "x"}, "elapsed_ms": 1, "source": ""})
    c = _client()
    c.get(BASE + "/search?q=Northwind")
    c.get(BASE + "/search?q=Northwind")
    assert len(calls) == 2


def test_the_search_is_rate_limited_per_account(monkeypatch):
    monkeypatch.setattr(store, "search_known_profiles", lambda email, q: [])
    calls = _apollo(monkeypatch, [])
    c = _client("search-limit@position2.com")
    limit = appmod._CPI_RATE_LIMITS["evi-search"][0]
    codes = [c.get(BASE + "/search?q=query%d" % i).status_code for i in range(limit + 2)]
    assert codes[:limit] == [200] * limit and codes[limit:] == [429, 429]
    assert len(calls) == limit
    body = c.get(BASE + "/search?q=another").get_json()
    assert body["error"]["code"] == "rate_limited" and body["error"]["message"]


@sql
def test_a_billed_search_is_recorded_against_the_account(monkeypatch):
    email = "search-usage-" + uuid.uuid4().hex + "@position2.com"
    _apollo(monkeypatch, [{"name": "Northwind"}])
    c = _client(email)
    c.get(BASE + "/search?q=Northwind")
    c.get(BASE + "/search?q=Northwind")          # cached: not billed, not recorded
    _apollo(monkeypatch, [])
    c.get(BASE + "/search?q=Nobody")             # a zero-row search is not billed
    usage = store.account_usage(email)["company_search"]
    assert usage == {"calls": 2, "credits": 1, "usd": 0.0}


# ── profile drafting ──────────────────────────────────────────────────────

def test_a_draft_failure_never_shows_the_developer_detail(monkeypatch):
    from tracker import event_intel_intake
    monkeypatch.setattr(event_intel_intake, "draft_profile", lambda n, w: {
        "draft": {}, "sources": [], "error": {
            "kind": "max_tokens",
            "detail": "Ran out of output budget before finishing (stop_reason=max_tokens). "
                      "Raise max_tokens or lower max_uses."}})
    r = _client().post(BASE + "/profiles/draft", json={"client_name": "N", "website": "https://a.example"})
    msg = r.get_json()["error"]
    assert r.status_code == 502
    assert "max_tokens" not in msg and "max_uses" not in msg and "stop_reason" not in msg
    assert "stopped mid-sentence" in msg and "fill the form in by hand" in msg


def test_intakes_own_reader_details_are_kept(monkeypatch):
    from tracker import event_intel_intake
    monkeypatch.setattr(event_intel_intake, "draft_profile", lambda n, w: {
        "draft": {}, "sources": [], "error": {"kind": "wrong_company",
                                              "detail": "That site sells garden furniture."}})
    r = _client().post(BASE + "/profiles/draft", json={"client_name": "N", "website": "https://a.example"})
    assert r.get_json()["error"] == "That site sells garden furniture."


@sql
def test_drafts_are_recorded_and_obey_the_daily_call_cap(monkeypatch):
    from tracker import event_intel_intake
    calls = []
    monkeypatch.setattr(event_intel_intake, "draft_profile", lambda n, w: calls.append(n) or {
        "draft": {}, "evidence": {}, "unknown": [], "what_they_sell": "x", "classification": None,
        "classification_why": "", "classification_confidence": None, "sources": [], "note": "",
        "error": None})
    monkeypatch.setenv("EVI_DAILY_CALL_LIMIT", "2")
    email = "draft-cap-" + uuid.uuid4().hex + "@position2.com"
    c = _client(email)
    body = {"client_name": "N", "website": "https://a.example"}
    codes = [c.post(BASE + "/profiles/draft", json=body).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    assert len(calls) == 2
    assert store.account_usage(email)["profile_draft"]["calls"] == 2
    monkeypatch.delenv("EVI_DAILY_CALL_LIMIT")
    assert c.post(BASE + "/profiles/draft", json=body).status_code == 200
