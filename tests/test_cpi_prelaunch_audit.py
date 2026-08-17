"""Pre-external-launch audit of Contact Finder, prompted by the plan to open
the tool to paying external clients rather than internal staff only. Five
parallel investigations (Fill-filters parsing, search/verification, credit
accounting, cross-account access control, XSS/error-leakage/abuse limits)
turned up a mix of concrete bugs and architecture questions that only the app
owner can answer. This file covers everything from that round fixable in code.

1. BILLING, two real gaps:
   - The Companies tab's own search never billed a credit at all, even though
     mixed_companies/search genuinely costs one per call that returns a
     result (the route's own docstring already said so). Every OTHER caller
     of search_companies in this file bills it at the call site; this one
     didn't, so a real per-page Apollo charge went unrecorded in the ledger
     and unreported to the user.
   - A credit spent resolving a typed company NAME (as opposed to a domain)
     to an Apollo org id was reported and recorded correctly on success, but
     if the PEOPLE search that follows then failed (a transport hiccup right
     after the first Apollo call), the credit that had already left the
     account vanished from both the response and the ledger -- the one thing
     this ledger is supposed to guarantee never happens.

2. FILL-FILTERS / _CPI_INTENT_SYSTEM, following the exact pattern that caused
   the keywords bug fixed the day before (a JSON schema field the prompt
   never explained, so the model free-styled): `exclude_technologies` and
   `technologies_all` were declared nowhere in the schema at all, so a
   "companies NOT using Salesforce" ask had nowhere correct to land and the
   realistic failure mode is the model puts "Salesforce" into `technologies`
   instead -- the exact opposite of what was asked, with a populated,
   plausible-looking filter and no error. `job_titles` (a company's OPEN
   JOB POSTINGS) was in the allow-list but never explained apart from
   `titles` (what a PERSON currently holds), risking the same silent
   conflation. `email_status` and `market_segments` were in the allow-list
   with no schema key or guidance at all, so they could never be set by Fill
   filters no matter how explicitly asked for.

   Separately: the Companies tab could get silently switched to People. The
   intent taxonomy (shared with the chat feature) has no "a list of companies
   matching criteria" bucket -- only one named company (company_info) or a
   person-shaped ask -- so "software companies in Texas" on the Companies tab
   came back "people_list"/"unclear" for lack of a better fit, and the entity
   always defaulted to "people" for anything but an unambiguous "company_info"
   intent. Fixed by sending the currently active tab as a prior and only
   switching tabs when the parse is unambiguous about which one it means.

3. static/js/company_people_intelligence.js:
   - rejectFilterKey/cpiRelax (the "Remove that filter" buttons on an
     all-rejected results page) resolved which field to clear and which
     search to re-run from STATE.entity (the tab currently SELECTED), not
     STATE.shownEntity (the tab that actually produced the rejection banner
     on screen). Switching tabs deliberately leaves old results on screen
     (see cpiSetEntity), so a still-visible People-tab rejection banner,
     clicked after switching to Companies, cleared the wrong field on the
     wrong panel and launched an unrelated, credit-costing Companies search
     -- the exact STATE.entity/STATE.shownEntity mixup already fixed once for
     cpiOpenDetails, recurring at a different call site.
   - applyFiltersToForm's tenure restore used a truthy check
     (`f.days_in_title_min`), so a real value of exactly 0 (freshly promoted
     into a role) silently failed to restore, unlike every other numeric
     field on the form.

4. _cpi_attach_employer_facts merged organization_growth6/12 through the same
   "0 means Apollo didn't have this" rule as every other fact (employees,
   revenue, founded year) -- but headcount growth is routinely and
   legitimately exactly 0% (flat headcount), unlike those. A company with
   genuinely flat growth silently got a blank field instead of "0%".

5. Abuse limits, following the fifth investigation's findings:
   - /list's cap (_CPI_LIST_MAX) was enforced only by a check-then-insert
     read of COUNT(*) before inserting -- a plain TOCTOU race. Two concurrent
     POSTs from the same user can each read the same pre-insert count and
     each insert up to their own share of the cap, landing the table well
     past 500 rows. Fixed with a self-healing trim after every insert,
     mirroring history's existing _cpi_history_prune.
   - /export had no row cap at all (unlike /list and /history, which both
     have one), so a client could POST an arbitrarily large `rows` array and
     have the server build the entire file in memory with no limit.
   - Nothing set MAX_CONTENT_LENGTH anywhere, so an oversized request body to
     any route was fully buffered and parsed before any route-level check
     ran.
   - /count, /parse-query and /chat had no rate limiting of any kind --
     each bills a real, per-request OpenAI and/or Apollo call, and nothing
     stopped a script (internal or, soon, an external client's) from looping
     any of them. Added a lightweight per-user, per-process limiter; see its
     own docstring for the honest caveat about what "per-process" means with
     more than one gunicorn worker.

6. Flagged, not silently changed here -- these need the app owner's decision,
   or genuinely cannot be verified from this sandbox:
   - SECRET_KEY falls back to a hardcoded, checked-in dev secret if unset,
     and GOOGLE_CLIENT_ID being unset makes /auth/google accept UNVERIFIED
     sign-ins. Both now log a loud, specific error if it ever happens instead
     of degrading silently, but confirming SECRET_KEY is actually set to a
     strong value in the real Railway environment needs someone with access
     to Railway, not this sandbox.
   - The Apollo/OpenAI credit pool is fully shared with no per-client
     isolation or quota, and /credits reports an aggregate across ALL users
     to any signed-in caller -- fine for internal staff today, a cross-tenant
     disclosure once external clients share the same endpoint. Deliberately
     not changed here: it depends entirely on how external-client access
     ends up being gated, which has not been decided yet.
   - Whether founded/funding/jobs/growth/tenure/yoe and the exclude_*/_all
     technology and location filter variants are genuinely enforced by
     Apollo (like their verified siblings) or merely trusted, unverified
     relevance hints (like industries turned out to be) needs a LIVE probe
     against a real Apollo key, the same way every other filter's strictness
     in this codebase was actually settled -- this sandbox has no production
     key to run one.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402
import tracker.apollo_client as ac  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JS = os.path.join(_ROOT, "static", "js", "company_people_intelligence.js")
_SEARCH = "/p2/b2b-agents/company-people-intelligence/search"
_LIST = "/p2/b2b-agents/company-people-intelligence/list"
_EXPORT = "/p2/b2b-agents/company-people-intelligence/export"
_PARSE = "/p2/b2b-agents/company-people-intelligence/parse-query"
_COUNT = "/p2/b2b-agents/company-people-intelligence/count"


@pytest.fixture
def client():
    c = appmod.app.test_client()
    with c.session_transaction() as sess:
        sess["google_user"] = {"email": "reporting@position2.com", "name": "T"}
    return c


# Deliberately NOT autouse: a real, present APOLLO_API_KEY also arms
# _warm_person_enrichment (fired on every non-@position2.com sign-in), which
# starts a genuine background thread hitting the real Apollo API. Autouse
# here once made the /auth/google test below spawn a real, retrying outbound
# network call under a fake key -- scoped instead to only the handful of
# tests below that actually need Contact Finder's own Apollo calls to run.
@pytest.fixture
def apollo_key(monkeypatch):
    monkeypatch.setenv("APOLLO_API_KEY", "test-key")


# ── 1a. Companies-tab search now bills its credit ────────────────────────────

def test_a_companies_search_that_returns_rows_bills_one_credit(client, monkeypatch, apollo_key):
    monkeypatch.setattr(ac, "search_companies",
                        lambda *a, **kw: [{"id": "o1", "name": "Acme"}])
    r = client.post(_SEARCH, json={"entity": "companies", "filters": {"industries": ["tech"]}})
    d = r.get_json()
    assert d["credits"] == 1


def test_a_companies_search_that_returns_nothing_bills_zero(client, monkeypatch, apollo_key):
    monkeypatch.setattr(ac, "search_companies", lambda *a, **kw: [])
    r = client.post(_SEARCH, json={"entity": "companies", "filters": {"industries": ["tech"]}})
    d = r.get_json()
    assert "credits" not in d


# ── 1c. A clamped funding bound (real Apollo ceiling, confirmed live against a
# real account with a real key) is reported to the user, not answered silently

def test_a_clamped_funding_bound_is_reported_in_the_search_response(client, monkeypatch, apollo_key):
    def fake_search_companies(filters, api_key, page=1, per_page=25, meta=None, **kw):
        if meta is not None:
            meta["funding_value_clamped"] = ["total_funding_range"]
        return [{"id": "o1", "name": "Acme"}]
    monkeypatch.setattr(ac, "search_companies", fake_search_companies)
    r = client.post(_SEARCH, json={"entity": "companies",
                                   "filters": {"total_funding_min": 5_000_000_000}})
    d = r.get_json()
    assert d["funding_value_clamped"] is True


def test_no_clamp_flag_when_nothing_was_clamped(client, monkeypatch, apollo_key):
    monkeypatch.setattr(ac, "search_companies", lambda *a, **kw: [{"id": "o1", "name": "Acme"}])
    r = client.post(_SEARCH, json={"entity": "companies",
                                   "filters": {"total_funding_min": 1_000_000}})
    d = r.get_json()
    assert "funding_value_clamped" not in d


# ── 1b. A credit already spent survives a later failure in the same request ─

def test_a_credit_spent_resolving_a_company_name_is_reported_even_if_the_search_then_fails(
        client, monkeypatch, apollo_key):
    def fake_resolve(name, api_key, spend=None, oai=None):
        if spend is not None:
            spend["credits"] = spend.get("credits", 0) + 1
        return "org1", "Acme Inc", None, True
    monkeypatch.setattr(appmod, "_cpi_resolve_company_name", fake_resolve)

    def boom(*a, **kw):
        raise ac.requests.HTTPError("Apollo did not answer")
    monkeypatch.setattr(ac, "search_people", boom)

    r = client.post(_SEARCH, json={"entity": "people",
                                   "filters": {"company_domains": ["Acme Inc"]}})
    d = r.get_json()
    assert d["search_failed"] is True
    assert d["credits"] == 1, "a credit already spent must not vanish just because a later step failed"


# ── 2. Fill-filters: fields the allow-list was silently stripping ───────────

def test_exclude_technologies_reaches_the_filter_panel():
    out = appmod._cpi_filters_from_intent({
        "intent": "people_list", "exclude_technologies": ["Salesforce"],
    })
    assert out["exclude_technologies"] == ["Salesforce"]
    assert "technologies" not in out, (
        "an excluded technology must never also land in the plain "
        "`technologies` key, which would ask for the opposite of what was asked"
    )


def test_technologies_all_reaches_the_filter_panel():
    out = appmod._cpi_filters_from_intent({
        "intent": "people_list", "technologies_all": ["Salesforce", "Marketo"],
    })
    assert out["technologies_all"] == ["Salesforce", "Marketo"]


def test_job_titles_and_titles_are_independent_and_both_reach_the_panel():
    out = appmod._cpi_filters_from_intent({
        "intent": "people_list", "titles": ["CMO"], "job_titles": ["VP of Sales"],
    })
    assert out["titles"] == ["CMO"]
    assert out["job_titles"] == ["VP of Sales"]


def test_email_status_reaches_the_filter_panel():
    out = appmod._cpi_filters_from_intent({
        "intent": "people_list", "email_status": "verified",
    })
    assert out["email_status"] == "verified"


def test_market_segments_reaches_the_filter_panel():
    out = appmod._cpi_filters_from_intent({
        "intent": "people_list", "market_segments": ["mid-market"],
    })
    assert out["market_segments"] == ["mid-market"]


def test_every_new_schema_field_has_its_own_explanatory_paragraph():
    """The keywords bug's root cause was a JSON key declared in the schema
    shape with no explanation below it, so the model free-styled. Guards
    against the same gap reopening for the fields just added: each must be
    named as its own word/phrase somewhere in the prompt body, not just in
    the JSON shape declaration at the top."""
    src = appmod._CPI_INTENT_SYSTEM
    shape, _, body = src.partition("max_results")
    for term in ("job_titles:", "exclude_technologies:", "technologies_all:",
                 "market_segments:", "email_status:"):
        assert term in body, "%r has no explanatory paragraph in _CPI_INTENT_SYSTEM" % term


# ── 2b. The Companies tab is no longer silently flipped to People ──────────

def _intent(monkeypatch, payload):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setattr(appmod, "_cpi_oai", lambda: object())
    monkeypatch.setattr(appmod, "_vimi_chat_json",
                        lambda oai, msgs, mt: (json.dumps(payload), "m"))


def test_an_ambiguous_company_attribute_ask_stays_on_the_companies_tab(client, monkeypatch):
    """"software companies in Texas" has no company_info-shaped intent to map
    to (that's for ONE named company) and no people-shaped titles/seniorities
    either, so the model reasonably comes back "people_list" or "unclear" --
    previously that always defaulted the entity to "people", flipping the
    user off the Companies tab they were deliberately on."""
    _intent(monkeypatch, {"intent": "people_list", "company_locations": ["Texas"],
                          "industries": ["software"]})
    body = client.post(_PARSE, json={"q": "software companies in Texas",
                                     "entity": "companies"}).get_json()
    assert body["entity"] == "companies"
    assert body["filters"]["locations"] == ["Texas"], (
        "the Companies tab's own HQ combo reads `locations`, not `company_locations`"
    )
    assert "company_locations" not in body["filters"]


def test_the_default_prior_is_people_when_the_request_carries_no_entity(client, monkeypatch):
    _intent(monkeypatch, {"intent": "unclear"})
    body = client.post(_PARSE, json={"q": "something vague"}).get_json()
    assert body["entity"] == "people"


def test_an_unambiguous_company_info_intent_still_switches_to_companies(client, monkeypatch):
    _intent(monkeypatch, {"intent": "company_info", "company_name": "Acme"})
    body = client.post(_PARSE, json={"q": "tell me about Acme",
                                     "entity": "people"}).get_json()
    assert body["entity"] == "companies"


def test_an_unambiguous_person_at_company_intent_still_switches_to_people(client, monkeypatch):
    _intent(monkeypatch, {"intent": "person_at_company", "titles": ["CMO"],
                          "company_name": "Acme"})
    body = client.post(_PARSE, json={"q": "who is the cmo of acme",
                                     "entity": "companies"}).get_json()
    assert body["entity"] == "people"


# ── 4. A genuine 0% headcount growth is no longer dropped as "no data" ─────

@pytest.fixture(autouse=True)
def no_firmo_cache(monkeypatch):
    monkeypatch.setattr(appmod, "_CPI_FIRMO_CACHE", {})
    monkeypatch.setattr(appmod, "_cpi_firmo_db_read", lambda ids: {})
    monkeypatch.setattr(appmod, "_cpi_firmo_db_write", lambda facts: None)


def test_a_real_zero_percent_growth_is_merged_onto_the_row(monkeypatch):
    monkeypatch.setattr(ac, "search_companies", lambda *a, **kw: [{
        "id": "org1", "name": "Flatline Inc",
        "organization_headcount_six_month_growth": 0.0,
        "organization_headcount_twelve_month_growth": 0.0,
    }])
    rows = [{"organization_id": "org1", "full_name": "Sean Saint"}]
    appmod._cpi_attach_employer_facts(rows, "key", {"credits": 0})
    assert rows[0]["organization_growth6"] == 0.0
    assert rows[0]["organization_growth12"] == 0.0


def test_a_genuinely_missing_growth_value_is_not_invented_as_zero(monkeypatch):
    monkeypatch.setattr(ac, "search_companies",
                        lambda *a, **kw: [{"id": "org1", "name": "No Data Co"}])
    rows = [{"organization_id": "org1", "full_name": "Sean Saint"}]
    appmod._cpi_attach_employer_facts(rows, "key", {"credits": 0})
    assert "organization_growth6" not in rows[0]
    assert "organization_growth12" not in rows[0]


def test_a_zero_employee_count_is_still_treated_as_no_data(monkeypatch):
    """Regression: the zero-is-real exception is scoped to growth only --
    every other fact (employees, revenue, founded year) keeps the existing
    rule, where Apollo returning a bare 0 means it did not have the number."""
    monkeypatch.setattr(ac, "search_companies", lambda *a, **kw: [{
        "id": "org1", "name": "Zero Co", "estimated_num_employees": 0,
    }])
    rows = [{"organization_id": "org1", "full_name": "Sean Saint"}]
    appmod._cpi_attach_employer_facts(rows, "key", {"credits": 0})
    assert "organization_employees" not in rows[0]


# ── 5a. /list's cap is enforced after insert, not just checked before it ────

class _Cur:
    def __init__(self, log, rows):
        self.log, self.rows = log, rows
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else [0]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Conn:
    def __init__(self, rows=None):
        self.log, self.rows, self.committed = [], list(rows or []), False

    def cursor(self):
        return _Cur(self.log, self.rows)

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass


def test_list_post_prunes_to_the_cap_after_inserting(client, monkeypatch):
    conn = _Conn(rows=[[0], [2]])   # have=0 before insert, count=2 after
    monkeypatch.setattr(appmod, "_pg_conn", lambda: conn)
    monkeypatch.setattr(appmod, "_CPI_LIST_TABLE_READY", True, raising=False)
    r = client.post(_LIST, json={"entity": "people",
                                 "rows": [{"id": "p1"}, {"id": "p2"}]})
    assert r.status_code == 200
    prunes = [(sql, params) for sql, params in conn.log
             if sql.startswith("DELETE FROM cpi_list_rows") and "NOT IN" in sql]
    assert len(prunes) == 1, "expected exactly one self-healing prune after the insert"
    _, params = prunes[0]
    assert params == ("reporting@position2.com", "reporting@position2.com", appmod._CPI_LIST_MAX)
    insert_idx = max(i for i, (sql, _) in enumerate(conn.log) if sql.startswith("INSERT"))
    prune_idx = next(i for i, (sql, _) in enumerate(conn.log)
                     if sql.startswith("DELETE FROM cpi_list_rows") and "NOT IN" in sql)
    assert prune_idx > insert_idx, "the prune must run after the inserts, not before"


# ── 5b. /export now caps how many rows it will build a file from ───────────

def test_export_caps_an_oversized_rows_array(client):
    rows = [{"id": "p%d" % i, "full_name": "Person %d" % i} for i in range(5010)]
    r = client.post(_EXPORT, json={"entity": "people", "format": "csv", "rows": rows})
    assert r.status_code == 200
    lines = r.data.decode("utf-8-sig").splitlines()
    import csv as _csv_mod
    table = list(_csv_mod.reader(lines))
    assert len(table) - 1 == 5000, "expected exactly the capped row count, not the full 5010"


# ── 5c. A platform-wide request body size cap now exists ───────────────────

def test_max_content_length_is_configured():
    assert appmod.app.config.get("MAX_CONTENT_LENGTH") == 32 * 1024 * 1024


# ── 5d. Rate limiting on the endpoints that cost real money per call ───────

def test_rate_limiter_allows_up_to_the_configured_budget_then_blocks(monkeypatch):
    appmod._CPI_RATE_STATE.clear()
    monkeypatch.setitem(appmod._CPI_RATE_LIMITS, "parse-query", (3, 60))
    for _ in range(3):
        assert appmod._cpi_rate_limited("parse-query", "a@b.com") is False
    assert appmod._cpi_rate_limited("parse-query", "a@b.com") is True


def test_rate_limiter_tracks_each_user_independently(monkeypatch):
    appmod._CPI_RATE_STATE.clear()
    monkeypatch.setitem(appmod._CPI_RATE_LIMITS, "parse-query", (1, 60))
    assert appmod._cpi_rate_limited("parse-query", "a@b.com") is False
    assert appmod._cpi_rate_limited("parse-query", "a@b.com") is True
    assert appmod._cpi_rate_limited("parse-query", "someone-else@b.com") is False


def test_rate_limiter_resets_once_the_window_elapses(monkeypatch):
    appmod._CPI_RATE_STATE.clear()
    monkeypatch.setitem(appmod._CPI_RATE_LIMITS, "parse-query", (1, 60))
    now = {"t": 1_000_000.0}
    monkeypatch.setattr(appmod.time, "time", lambda: now["t"])
    assert appmod._cpi_rate_limited("parse-query", "a@b.com") is False
    assert appmod._cpi_rate_limited("parse-query", "a@b.com") is True
    now["t"] += 61
    assert appmod._cpi_rate_limited("parse-query", "a@b.com") is False


def test_parse_query_returns_429_once_the_budget_is_spent(client, monkeypatch):
    monkeypatch.setitem(appmod._CPI_RATE_LIMITS, "parse-query", (2, 60))
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setattr(appmod, "_cpi_oai", lambda: object())
    monkeypatch.setattr(appmod, "_vimi_chat_json",
                        lambda oai, msgs, mt: (json.dumps({"intent": "unclear"}), "m"))
    for _ in range(2):
        r = client.post(_PARSE, json={"q": "cmos"})
        assert r.status_code == 200
    r = client.post(_PARSE, json={"q": "cmos"})
    assert r.status_code == 429


def test_count_returns_429_once_the_budget_is_spent(client, monkeypatch, apollo_key):
    monkeypatch.setitem(appmod._CPI_RATE_LIMITS, "count", (1, 60))
    monkeypatch.setattr(ac, "search_people", lambda *a, **kw: [])
    r = client.post(_COUNT, json={"entity": "people", "filters": {"titles": ["CMO"]}})
    assert r.status_code == 200
    r = client.post(_COUNT, json={"entity": "people", "filters": {"titles": ["CMO"]}})
    assert r.status_code == 429


# ── Google auth dev-mode fallback now logs loudly instead of silently ──────

def test_unverified_google_login_logs_a_security_warning(monkeypatch, caplog):
    monkeypatch.setattr(appmod, "GOOGLE_CLIENT_ID", "")
    import base64
    payload = base64.urlsafe_b64encode(json.dumps({"email": "x@y.com"}).encode()).decode().rstrip("=")
    credential = "h." + payload + ".s"
    with caplog.at_level("ERROR"):
        r = appmod.app.test_client().post("/auth/google", json={"credential": credential})
    assert r.status_code == 200
    assert any("GOOGLE_CLIENT_ID is not set" in rec.message for rec in caplog.records)


# ── SECRET_KEY fallback logs loudly at import time, checked in isolation ──

def test_secret_key_fallback_logs_a_security_warning_when_unset():
    """Run in a fresh subprocess rather than re-importing app.py in-process:
    a second import here would re-register every Flask route and corrupt
    every other test in this suite. A clean process is the only safe way to
    observe this module-level, import-time behaviour twice with different
    environments."""
    env_missing = dict(os.environ, GOOGLE_CLIENT_ID="test", GOOGLE_CLIENT_SECRET="test")
    env_missing.pop("SECRET_KEY", None)
    proc = subprocess.run(
        [sys.executable, "-c", "import logging; logging.basicConfig(level=logging.ERROR); import app"],
        cwd=_ROOT, env=env_missing, capture_output=True, text=True, timeout=30)
    assert "SECRET_KEY is not set" in proc.stderr

    env_set = dict(env_missing, SECRET_KEY="a-real-long-random-value")
    proc2 = subprocess.run(
        [sys.executable, "-c", "import logging; logging.basicConfig(level=logging.ERROR); import app"],
        cwd=_ROOT, env=env_set, capture_output=True, text=True, timeout=30)
    assert "SECRET_KEY is not set" not in proc2.stderr


# ── static/js/company_people_intelligence.js: reject-filter tab mixup ──────

_DRIVER = r"""
const fs = require("fs");
const bundle = fs.readFileSync(process.argv[2], "utf8");

function makeEl(tag, id){
  const el = {
    tagName: tag || "div", id: id || "", _html: "", value: "", checked: false,
    disabled: false, textContent: "", title: "", style: {}, options: [],
    dataset: {}, _on: {}, _kids: [],
    classList: {
      _s: new Set(),
      add(c){ this._s.add(c); }, remove(c){ this._s.delete(c); },
      contains(c){ return this._s.has(c); },
      toggle(c, on){ if(on === undefined) on = !this._s.has(c);
                     if(on) this._s.add(c); else this._s.delete(c); return on; },
    },
    getAttribute(n){ return this["_attr_"+n] === undefined ? null : this["_attr_"+n]; },
    setAttribute(n, v){ this["_attr_"+n] = v; },
    remove(){}, appendChild(c){ this._kids.push(c); }, removeChild(){},
    contains(other){ return other === this || this._kids.indexOf(other) >= 0; },
    addEventListener(){}, querySelectorAll(){ return []; }, querySelector(){ return null; },
    closest(){ return null; }, scrollIntoView(){}, focus(){}, click(){},
  };
  Object.defineProperty(el, "innerHTML", {
    get(){ return el._html; }, set(v){ el._html = String(v); },
  });
  return el;
}

const IDS = ["fpTitles","fpCompanyDomain","fpKeywords","fpEmpRange","fcEmpRange",
  "fpCompanyLocation","fpCompanyLocationChips","fcLocation","fcLocationChips",
  "cpiQbar","cpiLiveCount","cpiLiveCountCo","cpiToast","cpiAskInput","cpiAskBtn",
  "cpiAskNote","fpSeniority","fpEmailStatus","fpCompanyDetail","fcName",
  "fpAdvanced","fpMoreBtn","fcAdvanced","fcMoreBtn","cpiFiltersPeople",
  "cpiFiltersCompanies","cpiEntityToggle","cpiLoadMore","cpiResultsWrap",
  "cpiToolbar","cpiSearchBtn","cpiSearchBtnCo","cpiBulk","cpiBulkN",
  "cpiBulkEnrich","cpiSelectAll","cpiCount","fpRevenueMin","fpRevenueMax",
  "fpTenureMin","fpTenureMax"];
const els = {};
IDS.forEach(id => { els[id] = makeEl("div", id); });
els.fpCompanyDetail.checked = true;
const EMP_OPTIONS = [
  {value:""}, {value:"1,10"}, {value:"11,50"}, {value:"51,200"},
  {value:"201,500"}, {value:"501,1000"}, {value:"1001,5000"}, {value:"5001,"}
];
els.fpEmpRange.options = EMP_OPTIONS.map(o => ({value:o.value}));
els.fcEmpRange.options = EMP_OPTIONS.map(o => ({value:o.value}));

global.window = global;
global.addEventListener = function(){};
global.matchMedia = () => ({ matches:false, addEventListener(){} });
global.requestAnimationFrame = cb => setTimeout(cb, 0);
global.getComputedStyle = () => ({});
global.innerWidth = 1440; global.innerHeight = 900;
global.confirm = () => true;
global.document = {
  getElementById(id){ return els[id] || null; },
  querySelectorAll(){ return []; },
  querySelector(){ return null; },
  createElement(t){ return makeEl(t); },
  addEventListener(){},
  body: { style:{}, appendChild(){}, removeChild(){} },
  documentElement: { style:{} },
};
global.navigator = { clipboard: { writeText(){ return Promise.resolve(); } } };
global.setTimeout = setTimeout;
global.__CPI_HISTORY_URL__ = "/history";
global.__CPI_VOCAB_URL__ = "/vocab";
global.__CPI_COUNT_URL__ = "/count";
global.__CPI_CREDITS_URL__ = "/credits";
global.__CPI_LIST_URL__ = "/list";
global.__CPI_PARSE_URL__ = "/parse";
global.__CPI_SEARCH_URL__ = "/search";

const SENT = [];
let SEARCH_REPLY = { results: [], has_more:false, total:0 };
let PARSE_REPLY = { filters: {} };
global.fetch = function(url, opts){
  const u = String(url);
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  SENT.push({ url: u, body: body });
  let payload = {};
  if (u.indexOf("/search") >= 0) payload = SEARCH_REPLY;
  else if (u.indexOf("/parse") >= 0) payload = PARSE_REPLY;
  else if (u.indexOf("/count") >= 0) payload = { count: null };
  else if (u.indexOf("/credits") >= 0) payload = { available:false };
  return Promise.resolve({ ok:true, json(){ return Promise.resolve(payload); } });
};

eval(bundle);

const settle = ms => new Promise(r => setTimeout(r, ms === undefined ? 60 : ms));

(async function(){
  const out = {};

  // ── tenure 0 restore ──
  PARSE_REPLY = { filters: { days_in_title_min: 0, days_in_title_max: 90 } };
  els.cpiAskInput.value = "just started, under 3 months in role";
  window.cpiParseQuery();
  await settle();
  out.tenureMin = els.fpTenureMin.value;
  out.tenureMax = els.fpTenureMax.value;

  // ── reject-filter tab mixup ──
  // A real People search that comes back all-rejected on "hq".
  window.cpiSetEntity("people");
  SEARCH_REPLY = { results: [], has_more:false, total:0,
                   rejected: {hq: 3}, rejected_total: 3,
                   rejected_labels: {hq: "headquartered elsewhere"} };
  window.cpiRunSearch(true);
  await settle();

  // User switches to Companies without re-searching -- the banner above
  // stays on screen (cpiSetEntity deliberately leaves results/rejected alone).
  window.cpiSetEntity("companies");
  out.peopleTabHiddenAfterSwitch = els.cpiFiltersPeople.style.display;
  out.companiesTabShownAfterSwitch = els.cpiFiltersCompanies.style.display;

  const searchCallsBefore = SENT.filter(s => s.url.indexOf("/search") >= 0).length;
  window.cpiRelax("hq");
  await settle();

  out.peopleTabShownAfterRelax = els.cpiFiltersPeople.style.display;
  out.companiesTabHiddenAfterRelax = els.cpiFiltersCompanies.style.display;
  const searchCallsAfter = SENT.filter(s => s.url.indexOf("/search") >= 0);
  out.newSearchCalls = searchCallsAfter.length - searchCallsBefore;
  out.relaxedSearchEntity = searchCallsAfter.length ? searchCallsAfter[searchCallsAfter.length-1].body.entity : null;

  console.log(JSON.stringify(out));
})();
"""


def _run_js():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not available")
    with tempfile.TemporaryDirectory() as d:
        driver = os.path.join(d, "driver.js")
        with open(driver, "w", encoding="utf-8") as fh:
            fh.write(_DRIVER)
        proc = subprocess.run([node, driver, _JS], capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        pytest.fail("driver failed: %s" % (proc.stderr or proc.stdout)[-3000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def js_out():
    return _run_js()


def test_tenure_zero_restores_instead_of_staying_blank(js_out):
    """The fake input elements here don't coerce .value to a string the way a
    real <input> would, so a restored 0 comes back as the JS number 0, not
    "0" -- either way, not blank, which is the actual bug this pins."""
    assert js_out["tenureMin"] == 0
    assert js_out["tenureMax"] == 3


def test_switching_tabs_leaves_the_old_rejection_banner_on_screen(js_out):
    """Sanity check on the scenario itself: this must still be true (it is
    cpiSetEntity's own deliberate, unrelated behaviour) for the next
    assertions to actually be testing what they claim to."""
    assert js_out["peopleTabHiddenAfterSwitch"] == "none"
    assert js_out["companiesTabShownAfterSwitch"] == ""


def test_relaxing_a_stale_banner_switches_back_to_the_tab_it_belongs_to(js_out):
    assert js_out["peopleTabShownAfterRelax"] == ""
    assert js_out["companiesTabHiddenAfterRelax"] == "none"


def test_relaxing_a_stale_banner_reruns_the_search_on_the_right_entity(js_out):
    """The bug this pins: previously this fired a Companies search (silently
    discarding the People rejection the user was actually trying to fix) --
    not because the user asked for one, but because STATE.entity had moved on
    without them."""
    assert js_out["newSearchCalls"] == 1
    assert js_out["relaxedSearchEntity"] == "people"
