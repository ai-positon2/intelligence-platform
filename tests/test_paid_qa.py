"""Paid Marketing Agents: QA Agent, the Campaign Launch QC checklist.

Pinned here: the checklist data is the workbook's (155 checks, 15 sections, the
severities and the types it names); which checks apply to a launch; the
workbook's verdict (any Critical Fail is NOT READY, any Critical not yet ticked
is INCOMPLETE, else READY, with N/A counting as done); a Fail must say why; the
storage SQL is valid Postgres; and the routes are staff-only, refuse cross-site
requests, and never let a page claim a verdict the checks do not support.
"""
import copy
import os
import re
import sys

import pytest

os.environ.setdefault("GOOGLE_CLIENT_ID", "test")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test")
os.environ.setdefault("FLASK_SECRET_KEY", "test")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app as appmod  # noqa: E402
from tracker import paid_qa as qa  # noqa: E402
from tracker import paid_qa_store as store  # noqa: E402

STAFF = "someone@position2.com"
OTHER = "another@position2.com"
ADMIN = "reporting@position2.com"
SAME = {"Origin": "http://localhost"}
API = "/p2/paid-marketing/api/qa"
ACCOUNT = "2133861213"


# ── The checklist data ──────────────────────────────────────────────────────

def test_the_checklist_is_the_workbooks():
    cl = qa.checklist()
    items = cl["items"]
    assert len(items) == 155 and len({i["id"] for i in items}) == 155
    assert len(cl["sections"]) == 15 and cl["sections"][0] == "1. Account Setup & Access"
    sev = {s: sum(1 for i in items if i["severity"] == s) for s in cl["severities"]}
    assert sev == {"Critical": 45, "Major": 78, "Minor": 32}
    assert cl["campaignTypes"] == ["Search", "Search (DSA)", "PMax", "Shopping", "Display", "Demand Gen", "Video", "App"]
    assert cl["clientTypes"] == ["Ecommerce", "Lead Gen", "B2B", "B2C", "Local", "App", "Regulated"]


def test_every_check_is_complete_and_uses_only_known_values():
    cl = qa.checklist()
    for i in cl["items"]:
        assert i["check"] and i["verify"] and i["section"] in cl["sections"], i["id"]
        assert i["severity"] in cl["severities"], i["id"]
        for t in i["campaignTypes"]:
            assert t == "All" or t in cl["campaignTypes"], i["id"]
        for t in i["clientTypes"]:
            assert t == "All" or t in cl["clientTypes"], i["id"]
        assert i["id"].startswith(i["section"].split(".")[0] + "."), i["id"]


def test_the_one_conditional_check_keeps_its_condition():
    consent = qa.item("2.10")
    assert consent["check"] == "Consent Mode v2" and consent["clientTypes"] == ["All"]
    assert "EEA" in consent["condition"]
    assert [i["id"] for i in qa.items() if "condition" in i] == ["2.10"]


def test_no_text_in_the_checklist_uses_an_em_dash():
    assert "—" not in open(qa.CHECKLIST_PATH, encoding="utf-8").read()


# ── Which checks apply ──────────────────────────────────────────────────────

def test_checks_for_all_always_apply_and_a_pmax_check_needs_pmax():
    assert qa.item("1.1")["campaignTypes"] == ["All"]
    pmax_only = qa.item("8.1")
    assert qa.applies(qa.item("1.1"), ["Ecommerce"], ["Search"])
    assert qa.applies(pmax_only, [], ["PMax"]) and qa.applies(pmax_only, [], ["Search", "PMax"])
    assert not qa.applies(pmax_only, [], ["Search"])


def test_a_client_type_check_needs_a_matching_client_type():
    ecom = next(i for i in qa.items() if i["clientTypes"] == ["Ecommerce"])
    assert qa.applies(ecom, ["Ecommerce"], []) and qa.applies(ecom, ["Lead Gen", "Ecommerce"], [])
    assert not qa.applies(ecom, ["Lead Gen"], [])


def test_nothing_picked_yet_shows_every_check_and_picking_narrows_it():
    assert len(qa.applicable([], [])) == 155
    narrowed = qa.applicable(["Lead Gen"], ["Search"])
    assert 0 < len(narrowed) < 155
    assert all(qa.applies(i, ["Lead Gen"], ["Search"]) for i in narrowed)
    assert "8.1" not in {i["id"] for i in narrowed}                 # a PMax-only check
    assert "1.1" in {i["id"] for i in narrowed}


def test_the_preview_counts_by_severity():
    p = qa.preview("Lead Gen", "Search,PMax")
    assert p["count"] == len(p["applicable"]) == sum(p["bySeverity"].values())
    assert set(p["bySeverity"]) == {"Critical", "Major", "Minor"}
    assert qa.preview("", "")["count"] == 155
    with pytest.raises(qa.BadPayload):
        qa.preview("Nonsense", "")


# ── The verdict ─────────────────────────────────────────────────────────────

def launch(**kw):
    return dict({"client_types": [], "campaign_types": []}, **kw)


def ticks(status, ids):
    return {i: {"status": status} for i in ids}


def ids_where(**crit):
    rows = qa.applicable([], [])
    return [i["id"] for i in rows if all(i[k] == v for k, v in crit.items())]


CRITICAL = ids_where(severity="Critical")
NOT_CRITICAL = [i["id"] for i in qa.items() if i["severity"] != "Critical"]


def test_nothing_ticked_is_incomplete_not_ready():
    s = qa.summarize(launch(), {})
    assert s["verdict"] == {"code": "incomplete", "text": "INCOMPLETE: Critical checks pending"}
    assert s["total"] == 155 and s["checked"] == 0 and s["percent"] == 0
    assert len(s["pendingCritical"]) == 45


def test_every_critical_check_passed_is_ready_even_with_everything_else_unticked():
    s = qa.summarize(launch(), ticks("pass", CRITICAL))
    assert s["verdict"] == {"code": "ready", "text": "READY TO LAUNCH"}
    assert s["pendingCritical"] == [] and s["pending"] == 110


def test_a_critical_na_counts_as_done():
    checks = ticks("pass", CRITICAL[1:])
    checks[CRITICAL[0]] = {"status": "na"}
    assert qa.summarize(launch(), checks)["verdict"]["code"] == "ready"


def test_one_critical_fail_is_not_ready_whatever_else_is_ticked():
    checks = ticks("pass", [i["id"] for i in qa.items()])
    checks[CRITICAL[3]] = {"status": "fail", "note": "tracking is broken"}
    s = qa.summarize(launch(), checks)
    assert s["verdict"] == {"code": "not_ready", "text": "NOT READY: Critical check failed"}
    assert s["failedCritical"] == [CRITICAL[3]] and s["failed"] == [CRITICAL[3]]


def test_major_and_minor_failures_are_counted_but_never_block():
    checks = ticks("pass", CRITICAL)
    for i in ("1.3", "1.9"):                                          # a Major and a Minor
        checks[i] = {"status": "fail", "note": "x" * 5}
    s = qa.summarize(launch(), checks)
    assert s["verdict"]["code"] == "ready" and sorted(s["failed"]) == ["1.3", "1.9"] and s["failedCritical"] == []
    assert s["bySeverity"]["Major"]["fail"] == 1 and s["bySeverity"]["Minor"]["fail"] == 1


def test_counts_add_up_by_severity_and_by_section():
    checks = {**ticks("pass", ["1.1", "1.2"]), "1.3": {"status": "fail"}, "1.4": {"status": "na"}}
    s = qa.summarize(launch(), checks)
    assert sum(b["total"] for b in s["bySeverity"].values()) == s["total"] == 155
    for b in list(s["bySeverity"].values()) + s["bySection"]:
        assert b["total"] == b["pass"] + b["fail"] + b["na"] + b["pending"]
    sec1 = s["bySection"][0]
    assert sec1["section"] == "1. Account Setup & Access" and (sec1["pass"], sec1["fail"], sec1["na"]) == (2, 1, 1)
    assert s["checked"] == 4 and s["percent"] == 3


def test_ticks_on_checks_that_no_longer_apply_do_not_count():
    narrowed = launch(campaign_types=["Search"])                      # 8.1 is PMax only
    assert "8.1" not in qa.summarize(narrowed, {})["applicable"]
    s = qa.summarize(narrowed, {"8.1": {"status": "fail", "note": "x" * 5}})
    assert s["failed"] == [] and s["verdict"]["code"] == "incomplete"


def test_a_launch_with_no_critical_checks_is_ready():
    only_minor = launch(client_types=["Regulated"], campaign_types=["App"])
    rows = qa.applicable(only_minor["client_types"], only_minor["campaign_types"])
    if any(i["severity"] == "Critical" for i in rows):
        pytest.skip("this combination has Critical checks")
    assert qa.summarize(only_minor, {})["verdict"]["code"] == "ready"


# ── What a tick may look like ───────────────────────────────────────────────

def test_a_fail_needs_a_reason():
    L = launch()
    assert qa.clean_check(L, "1.1", {"status": "pass"}) == ("pass", "")
    assert qa.clean_check(L, "1.1", {"status": "na", "note": "  client has none  "}) == ("na", "client has none")
    assert qa.clean_check(L, "1.1", {"status": "fail", "note": "Wrong account: Brand_Search_US"}) == (
        "fail", "Wrong account: Brand_Search_US")
    for bad in ({"status": "fail"}, {"status": "fail", "note": ""}, {"status": "fail", "note": "  ok "}):
        with pytest.raises(qa.BadPayload, match="needs a reason"):
            qa.clean_check(L, "1.1", bad)


def test_status_check_id_and_size_are_validated():
    L = launch(campaign_types=["Search"])
    assert qa.clean_check(L, "1.1", {"status": None}) == (None, "")           # clears the tick
    assert qa.clean_check(L, "1.1", {}) == (None, "")
    for body, says in (({"status": "done"}, "status should be"), ({"status": "PASS"}, "status should be"),
                       ({"status": "pass", "note": "x" * 2001}, "over 2000"), ({"status": "pass", "note": 5}, "should be text"),
                       ("pass", "JSON object")):
        with pytest.raises(qa.BadPayload, match=says):
            qa.clean_check(L, "1.1", body)
    with pytest.raises(qa.BadPayload, match="not a check"):
        qa.clean_check(L, "99.9", {"status": "pass"})
    with pytest.raises(qa.BadPayload, match="does not apply"):
        qa.clean_check(L, "8.1", {"status": "pass"})                          # PMax check, Search launch


def test_launch_details_are_validated():
    ok = qa.clean_details({"accountId": "213-386-1213", "accountName": " McHale ", "name": " Fall launch ",
                           "clientTypes": ["Lead Gen", "Lead Gen"], "campaignTypes": ["PMax", "Search"],
                           "plannedDate": "2026-11-02", "qcBy": "AB"}, creating=True)
    assert ok["account_id"] == "2133861213" and ok["account_name"] == "McHale" and ok["name"] == "Fall launch"
    assert ok["campaign_types"] == ["Search", "PMax"] and ok["client_types"] == ["Lead Gen"]   # checklist order, no repeats
    assert ok["qc_by"] == "AB" and ok["approved_by"] == ""
    base = {"accountId": ACCOUNT, "accountName": "A", "name": "N"}
    for patch, says in (({"accountId": "123"}, "10 digits"), ({"accountName": ""}, "account name is needed"),
                        ({"name": " "}, "launch name is needed"), ({"clientTypes": ["B2X"]}, "does not know"),
                        ({"campaignTypes": "Search"}, "should be a list"), ({"plannedDate": "11/02/2026"}, "YYYY-MM-DD"),
                        ({"name": "n" * 121}, "over 120")):
        with pytest.raises(qa.BadPayload, match=says):
            qa.clean_details(dict(base, **patch), creating=True)
    assert qa.clean_details({"clientTypes": ["B2B"]}, creating=False) == {"client_types": ["B2B"]}   # only what is sent
    with pytest.raises(qa.BadPayload):
        qa.clean_details(["x"], creating=True)


# ── The storage SQL is Postgres ─────────────────────────────────────────────

class RecordingCursor:
    def __init__(self, log):
        self.log, self.rowcount = log, 1
        self._rows = [("a" * 32, ACCOUNT, "A", "N", [], [], "", "", "", "u", None, None, False)]

    def execute(self, sql, args=None):
        self.log.append(sql)
        if "FROM pm_qc_checks" in sql:
            self._rows = [("a" * 32, "1.1", "pass", "", "u", None)]
        elif "RETURNING status" in sql:
            self._rows = [("pass", "", "u", None)]
        elif "SELECT 1" in sql:
            self._rows = [(1,)]
        else:
            self._rows = [("a" * 32, ACCOUNT, "A", "N", [], [], "", "", "", "u", None, None, False)]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class RecordingConn:
    def __init__(self, log):
        self.log = log

    def cursor(self):
        return RecordingCursor(self.log)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def test_every_statement_the_store_runs_is_valid_postgres(monkeypatch):
    pglast = pytest.importorskip("pglast")
    log = []
    monkeypatch.setattr(store.paid_store, "_connect", lambda: RecordingConn(log))
    monkeypatch.setattr(store, "_READY", False)
    store.create_launch(account_id=ACCOUNT, account_name="A", name="N", client_types=["B2B"], campaign_types=["Search"],
                        planned_date="", qc_by="", approved_by="", by="u")
    store.get_launch("a" * 32)
    store.list_launches(ACCOUNT)
    store.update_launch("a" * 32, name="x", client_types=["B2B"], campaign_types=[], planned_date="", qc_by="q",
                        approved_by="", ignored="dropped")
    store.update_launch("a" * 32)
    store.archive_launch("a" * 32)
    store.checks_for(["a" * 32, "b" * 32])
    store.set_check("a" * 32, "1.1", "fail", "why", "u")
    store.clear_check("a" * 32, "1.1")
    assert len(log) >= 13
    for sql in log:
        n = iter(range(1, 100))
        pglast.parse_sql(re.sub(r"%s", lambda m: "$%d" % next(n), sql))
    assert not any("ignored" in s for s in log)                       # an unknown field is never put in SQL


def test_the_schema_has_the_tables_and_a_cascade():
    s = store.SCHEMA
    assert "pm_qc_launches" in s and "pm_qc_checks" in s and "ON DELETE CASCADE" in s
    assert "PRIMARY KEY (launch_id, check_id)" in s


# ── Routes, on an in-memory store ───────────────────────────────────────────

class MemoryStore:
    """What tracker/paid_qa_store.py keeps, in a dict."""
    def __init__(self):
        self.launches, self.checks, self.n = {}, {}, 0

    def create_launch(self, *, by, **f):
        self.n += 1
        lid = "%032x" % self.n
        self.launches[lid] = dict(f, id=lid, created_by=by, created_at="2026-10-14T10:00:00+00:00",
                                  updated_at="2026-10-14T10:00:00+00:00", archived=False)
        self.checks[lid] = {}
        return copy.deepcopy(self.launches[lid])

    def get_launch(self, lid):
        return copy.deepcopy(self.launches.get(lid))

    def list_launches(self, account_id, limit=40):
        return [copy.deepcopy(l) for l in reversed(list(self.launches.values()))
                if l["account_id"] == account_id and not l["archived"]]

    def update_launch(self, lid, **f):
        self.launches[lid].update({k: v for k, v in f.items() if k in ("name", "client_types", "campaign_types",
                                                                       "planned_date", "qc_by", "approved_by")})
        return copy.deepcopy(self.launches[lid])

    def archive_launch(self, lid):
        self.launches[lid]["archived"] = True
        return True

    def checks_for(self, ids):
        return {i: copy.deepcopy(self.checks.get(i, {})) for i in ids}

    def set_check(self, lid, cid, status, note, by):
        self.checks[lid][cid] = {"status": status, "note": note, "by": by, "at": "2026-10-14T10:05:00+00:00"}
        return copy.deepcopy(self.checks[lid][cid])

    def clear_check(self, lid, cid):
        self.checks[lid].pop(cid, None)
        return True


@pytest.fixture
def mem(monkeypatch):
    m = MemoryStore()
    for name in ("create_launch", "get_launch", "list_launches", "update_launch", "archive_launch", "checks_for",
                 "set_check", "clear_check"):
        monkeypatch.setattr(store, name, getattr(m, name))
    return m


def client_as(email):
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


NEW = {"accountId": ACCOUNT, "accountName": "McHale Landscaping", "name": "Fall promo",
       "clientTypes": ["Lead Gen"], "campaignTypes": ["Search", "PMax"], "plannedDate": "2026-11-02"}


def start(c=None, **over):
    c = c or client_as(STAFF)
    r = c.post(API + "/launches", json=dict(NEW, **over), headers=SAME)
    assert r.status_code == 201, r.get_json()
    return c, r.get_json()["launch"]["id"], r.get_json()


def test_the_page_is_for_position2_staff_only():
    page = "/p2/paid-marketing/qa-agent"
    assert client_as(STAFF).get(page).status_code == 200
    assert client_as(None).get(page).status_code == 302
    assert client_as("someone@gmail.com").get(page).status_code == 302


@pytest.mark.parametrize("path", ["/checklist", "/applicable", "/launches?account=" + ACCOUNT, "/launches/" + "a" * 32])
def test_the_data_routes_are_not_for_everyone(path, mem):
    assert client_as(None).get(API + path).status_code == 302
    assert client_as("someone@gmail.com").get(API + path).status_code == 302


def test_the_checklist_and_the_preview(mem):
    c = client_as(STAFF)
    body = c.get(API + "/checklist").get_json()
    assert len(body["items"]) == 155 and c.get(API + "/checklist").headers["Cache-Control"].startswith("private")
    p = c.get(API + "/applicable?clientTypes=Lead%20Gen&campaignTypes=Search,PMax").get_json()
    assert 0 < p["count"] < 155
    assert c.get(API + "/applicable?clientTypes=Bogus").status_code == 400


def test_starting_a_launch_builds_the_list_and_shows_the_verdict(mem):
    c, lid, body = start()
    assert body["launch"]["accountName"] == "McHale Landscaping" and body["launch"]["campaignTypes"] == ["Search", "PMax"]
    assert body["summary"]["verdict"]["code"] == "incomplete" and body["summary"]["checked"] == 0
    assert body["summary"]["total"] == len(qa.applicable(["Lead Gen"], ["Search", "PMax"]))
    assert mem.launches[lid]["created_by"] == STAFF
    again = c.get(API + "/launches/" + lid).get_json()
    assert again["launch"]["id"] == lid and again["checks"] == {}


def test_a_bad_new_launch_is_refused_in_words(mem):
    c = client_as(STAFF)
    r = c.post(API + "/launches", json=dict(NEW, accountId="12"), headers=SAME)
    assert r.status_code == 400 and "10 digits" in r.get_json()["error"] and not mem.launches


def test_ticking_a_check_saves_who_and_updates_the_verdict(mem):
    c, lid, _ = start()
    r = c.put("%s/launches/%s/checks/1.1" % (API, lid), json={"status": "pass"}, headers=SAME)
    body = r.get_json()
    assert r.status_code == 200 and body["check"]["status"] == "pass" and body["check"]["by"] == STAFF
    assert body["summary"]["checked"] == 1
    assert mem.checks[lid]["1.1"]["status"] == "pass"


def test_a_failed_check_needs_its_reason_and_keeps_it(mem):
    c, lid, _ = start()
    url = "%s/launches/%s/checks/1.2" % (API, lid)
    r = c.put(url, json={"status": "fail"}, headers=SAME)
    assert r.status_code == 400 and "needs a reason" in r.get_json()["error"] and "1.2" not in mem.checks[lid]
    r = c.put(url, json={"status": "fail", "note": "Prepay balance is $0: add funds"}, headers=SAME)
    body = r.get_json()
    assert r.status_code == 200 and body["summary"]["verdict"]["code"] == "not_ready"       # 1.2 is Critical
    assert body["summary"]["failedCritical"] == ["1.2"]
    assert c.get(API + "/launches/" + lid).get_json()["checks"]["1.2"]["note"] == "Prepay balance is $0: add funds"


def test_a_tick_can_be_cleared_and_changed(mem):
    c, lid, _ = start()
    url = "%s/launches/%s/checks/1.1" % (API, lid)
    c.put(url, json={"status": "fail", "note": "wrong account"}, headers=SAME)
    c.put(url, json={"status": "pass"}, headers=SAME)
    assert mem.checks[lid]["1.1"]["status"] == "pass"
    r = c.put(url, json={"status": None}, headers=SAME)
    assert r.status_code == 200 and r.get_json()["check"] is None and "1.1" not in mem.checks[lid]


def test_a_check_that_does_not_apply_or_does_not_exist_cannot_be_ticked(mem):
    c, lid, _ = start(campaignTypes=["Search"])
    for check, status in (("8.1", 400), ("99.9", 400)):
        assert c.put("%s/launches/%s/checks/%s" % (API, lid, check), json={"status": "pass"}, headers=SAME).status_code == status
    assert mem.checks[lid] == {}


def test_changing_what_is_launching_changes_the_list_and_the_verdict(mem):
    c, lid, first = start(campaignTypes=["Search"])
    before = first["summary"]["total"]
    r = c.put("%s/launches/%s" % (API, lid), json={"campaignTypes": ["Search", "PMax", "Shopping"]}, headers=SAME)
    assert r.status_code == 200 and r.get_json()["summary"]["total"] > before
    assert "8.1" in r.get_json()["summary"]["applicable"]
    assert c.put("%s/launches/%s" % (API, lid), json={"campaignTypes": ["Bogus"]}, headers=SAME).status_code == 400


def test_an_accounts_launches_are_listed_newest_first_with_their_verdicts(mem):
    c, first, _ = start(name="First")
    _c, second, _ = start(c, name="Second")
    start(c, accountId="3333333333", name="Other account")
    c.put("%s/launches/%s/checks/1.2" % (API, second), json={"status": "fail", "note": "no budget"}, headers=SAME)
    out = c.get(API + "/launches?account=213-386-1213").get_json()["launches"]
    assert [l["name"] for l in out] == ["Second", "First"]
    assert out[0]["summary"]["verdict"]["code"] == "not_ready" and out[0]["summary"]["failedCritical"] == 1
    assert out[1]["summary"]["verdict"]["code"] == "incomplete"
    assert c.get(API + "/launches?account=abc").status_code == 400


def test_only_whoever_started_a_qc_or_an_admin_can_remove_it(mem):
    c, lid, _ = start(client_as(STAFF))
    assert client_as(OTHER).delete("%s/launches/%s" % (API, lid), json={}, headers=SAME).status_code == 403
    assert not mem.launches[lid]["archived"]
    assert client_as(STAFF).delete("%s/launches/%s" % (API, lid), json={}, headers=SAME).status_code == 200
    assert client_as(STAFF).get("%s/launches/%s" % (API, lid)).status_code == 404            # gone from view
    c2, lid2, _ = start(client_as(STAFF))
    assert client_as(ADMIN).delete("%s/launches/%s" % (API, lid2), json={}, headers=SAME).status_code == 200


def test_ids_are_checked_before_anything_is_looked_up(mem):
    c = client_as(STAFF)
    for bad in ("x", "../etc", "A" * 32, "a" * 31):
        assert c.get("%s/launches/%s" % (API, bad)).status_code == 404
    assert c.get("%s/launches/%s" % (API, "a" * 32)).status_code == 404                       # well formed, not there
    assert c.put("%s/launches/%s/checks/1.1" % (API, "a" * 32), json={"status": "pass"}, headers=SAME).status_code == 404


def test_cross_site_and_non_json_writes_are_refused(mem):
    c, lid, _ = start()
    url = "%s/launches/%s/checks/1.1" % (API, lid)
    assert c.put(url, json={"status": "pass"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.put(url, data="status=pass", headers=SAME).status_code == 415
    assert c.post(API + "/launches", json=NEW, headers={"Origin": "https://evil.example"}).status_code == 403
    assert mem.checks[lid] == {}


def test_a_store_outage_is_a_503_with_a_reason(monkeypatch):
    def down(*a, **k):
        raise store.StoreUnavailable("DATABASE_URL is not set")
    monkeypatch.setattr(store, "list_launches", down)
    monkeypatch.setattr(store, "create_launch", down)
    c = client_as(STAFF)
    r = c.get(API + "/launches?account=" + ACCOUNT)
    assert r.status_code == 503 and "DATABASE_URL" in r.get_json()["error"]
    assert c.post(API + "/launches", json=NEW, headers=SAME).status_code == 503


def test_the_server_never_trusts_a_verdict_from_the_page(mem):
    c, lid, _ = start()
    r = c.put("%s/launches/%s/checks/1.1" % (API, lid), json={"status": "pass", "verdict": "ready", "summary": {"verdict": {"code": "ready"}}},
              headers=SAME)
    assert r.get_json()["summary"]["verdict"]["code"] == "incomplete"
