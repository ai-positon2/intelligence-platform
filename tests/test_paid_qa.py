"""Paid Marketing Agents: QA Agent, the Campaign Launch QC on a live campaign.

Pinned here: the checklist data is the workbook's (155 checks, 15 sections, the
severities and the types it names); which checks show for a campaign (its own type,
and the client type once one is chosen); the workbook's verdict (any Critical
failure is NOT READY, any Critical not yet done is INCOMPLETE, else READY, with N/A
counting as done); a person's wrong needs a reason; a person's answer beats the
agent's reading; the storage SQL is valid Postgres; and the routes are staff-only,
refuse cross-site requests, and never let a page claim a verdict the checks do not
support. What the agent reads from Google Ads is tests/test_paid_qa_auto.py.
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
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_qa as qa  # noqa: E402
from tracker import paid_qa_store as store  # noqa: E402

STAFF = "someone@position2.com"
OTHER = "another@position2.com"
ADMIN = "reporting@position2.com"
SAME = {"Origin": "http://localhost"}
API = "/p2/paid-marketing/api/qa"
ACCOUNT = "2133861213"
CAMPAIGN = "9000000001"


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


# ── Which checks show ───────────────────────────────────────────────────────

def test_checks_for_all_always_apply_and_a_pmax_check_needs_pmax():
    assert qa.item("1.1")["campaignTypes"] == ["All"]
    pmax_only = qa.item("8.1")
    assert qa.applies(qa.item("1.1"), [], ["Search"])
    assert qa.applies(pmax_only, [], ["PMax"]) and qa.applies(pmax_only, [], ["Search", "PMax"])
    assert not qa.applies(pmax_only, [], ["Search"])
    assert qa.applies(pmax_only, [], [])                              # a campaign type this list does not map narrows nothing


def test_a_client_type_check_waits_for_a_client_type():
    ecom = next(i for i in qa.items() if i["clientTypes"] == ["Ecommerce"])
    assert not qa.applies(ecom, [], [])                               # no client type chosen: not shown, not counted
    assert qa.applies(ecom, ["Ecommerce"], []) and qa.applies(ecom, ["Lead Gen", "Ecommerce"], [])
    assert not qa.applies(ecom, ["Lead Gen"], [])


def test_with_no_client_type_only_the_checks_for_every_client_show():
    shown = qa.applicable([], [])
    assert shown and all("All" in i["clientTypes"] for i in shown)
    assert len(shown) < 155
    narrowed = qa.applicable(["Lead Gen"], ["Search"])
    assert len(narrowed) > 0 and all(qa.applies(i, ["Lead Gen"], ["Search"]) for i in narrowed)
    assert "8.1" not in {i["id"] for i in narrowed} and "1.1" in {i["id"] for i in narrowed}


def test_the_campaign_type_comes_from_the_google_ads_channel():
    assert qa.campaign_types_for("SEARCH") == ["Search"]
    assert qa.campaign_types_for("SEARCH", dsa=True) == ["Search", "Search (DSA)"]
    assert qa.campaign_types_for("PERFORMANCE_MAX") == ["PMax"]
    assert qa.campaign_types_for("MULTI_CHANNEL") == ["App"]
    assert qa.campaign_types_for("SMART") == [] and qa.campaign_types_for("") == []


def test_what_waits_on_a_client_type_is_counted_for_the_page():
    L = launch(campaign_types=["Search"])
    need = qa.hidden_by_client(L)
    assert need["count"] > 0 and need["critical"] > 0 and set(need["types"]) <= set(qa.checklist()["clientTypes"])
    after = qa.hidden_by_client(launch(campaign_types=["Search"], client_types=list(qa.checklist()["clientTypes"])))
    assert after["count"] == 0


# ── The verdict ─────────────────────────────────────────────────────────────

def launch(**kw):
    return dict({"client_types": [], "campaign_types": []}, **kw)


def ticks(status, ids):
    return {i: {"status": status} for i in ids}


SHOWN = qa.applicable([], [])
CRITICAL = [i["id"] for i in SHOWN if i["severity"] == "Critical"]


def test_nothing_done_is_incomplete_not_ready():
    s = qa.summarize(launch(), {})
    assert s["verdict"] == {"code": "incomplete", "text": "INCOMPLETE: Critical checks pending"}
    assert s["total"] == len(SHOWN) and s["checked"] == 0 and s["percent"] == 0
    assert len(s["pendingCritical"]) == len(CRITICAL) > 0


def test_every_critical_check_right_is_ready_even_with_everything_else_open():
    s = qa.summarize(launch(), ticks("pass", CRITICAL))
    assert s["verdict"] == {"code": "ready", "text": "READY TO LAUNCH"}
    assert s["pendingCritical"] == [] and s["pending"] == len(SHOWN) - len(CRITICAL)


def test_a_critical_na_counts_as_done():
    checks = ticks("pass", CRITICAL[1:])
    checks[CRITICAL[0]] = {"status": "na"}
    assert qa.summarize(launch(), checks)["verdict"]["code"] == "ready"


def test_one_critical_wrong_is_not_ready_whatever_else_is_done():
    checks = ticks("pass", [i["id"] for i in SHOWN])
    checks[CRITICAL[3]] = {"status": "fail", "note": "tracking is broken"}
    s = qa.summarize(launch(), checks)
    assert s["verdict"] == {"code": "not_ready", "text": "NOT READY: Critical check failed"}
    assert s["failedCritical"] == [CRITICAL[3]] and s["failed"] == [CRITICAL[3]]


def test_major_and_minor_failures_are_counted_but_never_block():
    checks = ticks("pass", CRITICAL)
    for i in ("1.3", "1.14"):                                         # a Major and a Minor
        checks[i] = {"status": "fail", "note": "x" * 5}
    s = qa.summarize(launch(), checks)
    assert s["verdict"]["code"] == "ready" and sorted(s["failed"]) == ["1.14", "1.3"] and s["failedCritical"] == []
    assert s["bySeverity"]["Major"]["fail"] == 1 and s["bySeverity"]["Minor"]["fail"] == 1


def test_counts_add_up_by_severity_and_by_section():
    checks = {**ticks("pass", ["1.1", "1.2"]), "1.3": {"status": "fail"}, "1.4": {"status": "na"}}
    s = qa.summarize(launch(), checks)
    assert sum(b["total"] for b in s["bySeverity"].values()) == s["total"] == len(SHOWN)
    for b in list(s["bySeverity"].values()) + s["bySection"]:
        assert b["total"] == b["pass"] + b["fail"] + b["na"] + b["pending"]
    sec1 = s["bySection"][0]
    assert sec1["section"] == "1. Account Setup & Access" and (sec1["pass"], sec1["fail"], sec1["na"]) == (2, 1, 1)
    assert s["checked"] == 4


def test_answers_on_checks_that_no_longer_show_do_not_count():
    narrowed = launch(campaign_types=["Search"])                      # 8.1 is PMax only
    assert "8.1" not in qa.summarize(narrowed, {})["applicable"]
    s = qa.summarize(narrowed, {"8.1": {"status": "fail", "note": "x" * 5}})
    assert s["failed"] == [] and s["verdict"]["code"] == "incomplete"


# ── The agent's reading and a person's answer ───────────────────────────────

AT = "2026-10-14T10:00:00+00:00"


def test_the_ais_and_the_rules_pass_and_fail_become_the_checks_state_with_their_finding():
    L = launch(campaign_types=["Search"])
    rows = qa.effective(L, {}, {"1.6": {"result": "pass", "found": "Auto-tagging is ON", "source": "rule"},
                                "3.6": {"result": None, "found": "Cannot see the plan", "source": "ai", "unverified": True},
                                "6.2": {"result": "fail", "found": "Headline 3 shouts", "fix": "Remove the !!", "evidence": "Headline 3",
                                        "source": "ai"}}, AT)
    assert rows["1.6"]["status"] == "pass" and rows["1.6"]["source"] == "rule" and rows["1.6"]["by"] == "QA Agent" and rows["1.6"]["at"] == AT
    assert rows["6.2"]["status"] == "fail" and rows["6.2"]["source"] == "ai" and rows["6.2"]["fix"] == "Remove the !!"
    assert rows["6.2"]["evidence"] == "Headline 3"
    assert rows["3.6"]["status"] is None and rows["3.6"]["unverified"] is True and rows["3.6"]["found"] == "Cannot see the plan"
    assert "1.1" not in rows                                                                      # nothing read, nothing answered


def test_a_persons_answer_always_beats_the_agent_and_clearing_it_brings_the_agent_back():
    L = launch(campaign_types=["Search"])
    auto = {"4.2": {"result": "fail", "found": "Search partners are ON", "source": "rule"}}
    mine = {"4.2": {"status": "pass", "note": "Client approved partners in writing", "by": STAFF, "at": AT}}
    row = qa.effective(L, mine, auto, AT)["4.2"]
    assert row["status"] == "pass" and row["source"] == "person" and row["note"].startswith("Client approved")
    assert row["found"] == "Search partners are ON"                                               # the finding is still printed
    assert qa.effective(L, {}, auto, AT)["4.2"]["status"] == "fail"


def test_the_agent_never_answers_a_check_that_does_not_show():
    L = launch(campaign_types=["Search"])
    rows = qa.effective(L, {}, {"8.1": {"result": "fail", "found": "Final URL expansion is ON"}}, AT)
    assert "8.1" not in rows


def test_an_agent_fail_on_a_critical_check_makes_the_launch_not_ready_until_a_person_overrules_it():
    L = launch(campaign_types=["Search"])
    done = {c: {"status": "pass"} for c in CRITICAL if c != "4.1"}
    rows = qa.effective(L, {}, {"4.1": {"result": "fail", "found": "Google Display Network is ON"}}, AT)
    assert qa.summarize(L, {**done, **rows})["verdict"]["code"] == "not_ready"
    fixed = qa.effective(L, {"4.1": {"status": "pass", "note": "turned off", "by": STAFF, "at": AT}},
                         {"4.1": {"result": "fail", "found": "Google Display Network is ON"}}, AT)
    assert qa.summarize(L, {**done, **fixed})["verdict"]["code"] == "ready"


def test_a_critical_check_the_ai_could_not_verify_keeps_the_launch_incomplete_until_a_person_confirms_it():
    L = launch(campaign_types=["Search"])
    done = {c: {"status": "pass"} for c in CRITICAL if c != "14.4"}
    rows = qa.effective(L, {}, {"14.4": {"result": None, "found": "Cannot see the client's sign-off", "source": "ai", "unverified": True}}, AT)
    s = qa.summarize(L, {**done, **rows})
    assert s["verdict"]["code"] == "incomplete" and s["pendingCritical"] == ["14.4"] and "14.4" in s["needsHuman"]
    mine = qa.effective(L, {"14.4": {"status": "pass", "note": "Signed off by email", "by": STAFF, "at": AT}},
                        {"14.4": {"result": None, "found": "Cannot see the client's sign-off", "source": "ai", "unverified": True}}, AT)
    assert qa.summarize(L, {**done, **mine})["verdict"]["code"] == "ready"


def test_the_review_view_says_what_the_ai_is_doing_and_notices_a_dead_one():
    base = {"review_status": "running", "review_progress": {"phase": "Reading", "done": 2, "total": 9}, "review_error": "", "review_note": "",
            "review_started": "2026-10-14T10:00:00+00:00"}
    now = qa.dt.datetime.fromisoformat("2026-10-14T10:03:00+00:00")
    assert qa.review_view(base, now) == {"status": "running", "phase": "Reading", "done": 2, "total": 9, "error": "", "note": ""}
    later = qa.dt.datetime.fromisoformat("2026-10-14T10:20:00+00:00")
    dead = qa.review_view(base, later)
    assert dead["status"] == "error" and "stopped before it finished" in dead["error"]
    assert qa.review_view({"review_status": ""}, now)["status"] == ""


def test_the_page_payload_has_the_launch_every_state_and_the_verdict():
    L = {"id": "a" * 32, "account_id": ACCOUNT, "account_name": "A", "campaign_id": CAMPAIGN, "campaign_name": "Brand",
         "channel": "SEARCH", "client_types": [], "campaign_types": ["Search"], "auto": {"1.6": {"result": "pass", "found": "ON"}},
         "auto_at": AT, "created_by": STAFF, "created_at": AT, "updated_at": AT, "archived": False}
    p = qa.payload(L, {})
    assert p["launch"]["campaignName"] == "Brand" and p["launch"]["readAt"] == AT and p["launch"]["campaignTypes"] == ["Search"]
    assert p["checks"]["1.6"]["status"] == "pass" and p["summary"]["checked"] == 1
    assert p["needsClientType"]["count"] > 0


# ── What a person's answer may look like ────────────────────────────────────

def test_wrong_needs_a_reason():
    L = launch()
    assert qa.clean_check(L, "1.1", {"status": "pass"}) == ("pass", "")
    assert qa.clean_check(L, "1.1", {"status": "pass", "note": "  Found: utm_source=google "}) == ("pass", "Found: utm_source=google")
    assert qa.clean_check(L, "1.1", {"status": "na", "note": "  client has none  "}) == ("na", "client has none")
    assert qa.clean_check(L, "1.1", {"status": "fail", "note": "Wrong account: Brand_Search_US"}) == (
        "fail", "Wrong account: Brand_Search_US")
    for bad in ({"status": "fail"}, {"status": "fail", "note": ""}, {"status": "fail", "note": "  ok "}):
        with pytest.raises(qa.BadPayload, match="needs a reason"):
            qa.clean_check(L, "1.1", bad)


def test_status_check_id_and_size_are_validated():
    L = launch(campaign_types=["Search"])
    assert qa.clean_check(L, "1.1", {"status": None}) == (None, "")           # clears the answer
    assert qa.clean_check(L, "1.1", {}) == (None, "")
    for body, says in (({"status": "done"}, "status should be"), ({"status": "PASS"}, "status should be"),
                       ({"status": "pass", "note": "x" * 2001}, "over 2000"), ({"status": "pass", "note": 5}, "should be text"),
                       ("pass", "JSON object")):
        with pytest.raises(qa.BadPayload, match=says):
            qa.clean_check(L, "1.1", body)
    with pytest.raises(qa.BadPayload, match="not a check"):
        qa.clean_check(L, "99.9", {"status": "pass"})
    with pytest.raises(qa.BadPayload, match="does not apply"):
        qa.clean_check(L, "8.1", {"status": "pass"})                          # PMax check, Search campaign
    with pytest.raises(qa.BadPayload, match="does not apply"):
        qa.clean_check(L, "8.5", {"status": "pass"})                          # Ecommerce check, no client type chosen


def test_starting_a_qc_needs_an_account_and_a_campaign():
    ok = qa.clean_start({"accountId": "213-386-1213", "accountName": " Beta Bionics ", "campaignId": "900-000-0001",
                         "campaignName": " Brand Campaign ", "channel": "SEARCH"})
    assert ok == {"account_id": "2133861213", "account_name": "Beta Bionics", "campaign_id": "9000000001",
                  "campaign_name": "Brand Campaign", "channel": "SEARCH"}
    assert qa.clean_start({"accountId": ACCOUNT, "accountName": "A", "campaignId": "1", "campaignName": "C",
                           "channel": "SMART"})["channel"] == ""              # a type this list does not map: no narrowing
    base = {"accountId": ACCOUNT, "accountName": "A", "campaignId": CAMPAIGN, "campaignName": "C"}
    for patch, says in (({"accountId": "123"}, "10 digits"), ({"accountName": ""}, "account name is needed"),
                        ({"campaignId": ""}, "Pick a campaign"), ({"campaignId": "abc"}, "Pick a campaign"),
                        ({"campaignName": " "}, "campaign name is needed"), ({"campaignName": "n" * 121}, "over 120")):
        with pytest.raises(qa.BadPayload, match=says):
            qa.clean_start(dict(base, **patch))
    with pytest.raises(qa.BadPayload):
        qa.clean_start(["x"])


def test_client_types_are_checked_against_the_checklists():
    assert qa.clean_client_types({"clientTypes": ["Lead Gen", "Lead Gen", "B2B"]}) == ["Lead Gen", "B2B"]   # checklist order, no repeats
    assert qa.clean_client_types({"clientTypes": []}) == [] and qa.clean_client_types({}) == []
    for bad, says in (({"clientTypes": ["B2X"]}, "does not know"), ({"clientTypes": "Lead Gen"}, "should be a list")):
        with pytest.raises(qa.BadPayload, match=says):
            qa.clean_client_types(bad)


# ── The storage SQL is Postgres ─────────────────────────────────────────────

LAUNCH_ROW = ("a" * 32, ACCOUNT, "A", CAMPAIGN, "C", "SEARCH", [], ["Search"], {}, None, "u", None, None, False, "", {}, "", "", None)


class RecordingCursor:
    def __init__(self, log):
        self.log, self.rowcount = log, 1
        self._rows = [LAUNCH_ROW]

    def execute(self, sql, args=None):
        self.log.append(sql)
        if "FROM pm_qc_checks" in sql:
            self._rows = [("a" * 32, "1.1", "pass", "", "u", None)]
        elif "RETURNING status" in sql:
            self._rows = [("pass", "", "u", None)]
        elif sql.rstrip().endswith("RETURNING id"):
            self._rows = [("a" * 32,)]
        elif "SELECT 1" in sql:
            self._rows = [(1,)]
        else:
            self._rows = [LAUNCH_ROW]

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
    launch_row, created = store.start_launch(account_id=ACCOUNT, account_name="A", campaign_id=CAMPAIGN, campaign_name="C",
                                             channel="SEARCH", campaign_types=["Search"], by="u")
    assert created and launch_row["campaign_id"] == CAMPAIGN
    store.get_launch("a" * 32)
    store.list_launches(ACCOUNT)
    store.set_client_types("a" * 32, ["B2B"])
    assert store.begin_review("a" * 32) is True
    store.set_progress("a" * 32, "The AI is reviewing the checklist", 2, 9)
    store.finish_review("a" * 32, {"1.6": {"result": "pass", "found": "ON", "source": "rule"}}, campaign_name="C", channel="SEARCH",
                        campaign_types=["Search"], note="")
    store.fail_review("a" * 32, "Google Ads says the daily limit is used up.")
    store.archive_launch("a" * 32)
    store.checks_for(["a" * 32, "b" * 32])
    store.set_check("a" * 32, "1.1", "fail", "why", "u")
    store.clear_check("a" * 32, "1.1")
    assert len(log) >= 17
    for sql in log:
        n = iter(range(1, 100))
        pglast.parse_sql(re.sub(r"%s", lambda m: "$%d" % next(n), sql))


def test_the_schema_has_the_tables_a_cascade_and_one_live_qc_per_campaign():
    s = store.SCHEMA
    assert "pm_qc_launches" in s and "pm_qc_checks" in s and "ON DELETE CASCADE" in s
    assert "PRIMARY KEY (launch_id, check_id)" in s
    assert "UNIQUE INDEX" in s and "(account_id, campaign_id) WHERE NOT archived" in s
    # the first version's table may exist on a deployed database: it is brought up to shape, its rows retired,
    # and only then is the one-live-QC-per-campaign index built (old rows all have an empty campaign id)
    assert s.index("ADD COLUMN IF NOT EXISTS campaign_id") < s.index("SET archived = true WHERE campaign_id = ''") < s.index("UNIQUE INDEX")
    assert "ALTER COLUMN name DROP NOT NULL" in s
    assert "review_status" in s and "ADD COLUMN IF NOT EXISTS review_started" in s


# ── Routes, on an in-memory store, a fake Google Ads, fake landing pages and a fake AI ──

class MemoryStore:
    """What tracker/paid_qa_store.py keeps, in a dict."""
    def __init__(self):
        self.launches, self.checks, self.n = {}, {}, 0

    def start_launch(self, *, by, account_id, campaign_id, campaign_types, **f):
        for l in self.launches.values():
            if l["account_id"] == account_id and l["campaign_id"] == campaign_id and not l["archived"]:
                return copy.deepcopy(l), False
        self.n += 1
        lid = "%032x" % self.n
        self.launches[lid] = dict(f, id=lid, account_id=account_id, campaign_id=campaign_id, campaign_types=campaign_types,
                                  client_types=[], auto={}, auto_at=None, created_by=by, created_at="2026-10-14T10:00:00+00:00",
                                  updated_at="2026-10-14T10:00:00+00:00", archived=False, review_status="", review_progress={},
                                  review_error="", review_note="", review_started=None)
        self.checks[lid] = {}
        return copy.deepcopy(self.launches[lid]), True

    def get_launch(self, lid):
        return copy.deepcopy(self.launches.get(lid))

    def list_launches(self, account_id, limit=200):
        return [copy.deepcopy(l) for l in reversed(list(self.launches.values()))
                if l["account_id"] == account_id and not l["archived"]]

    def set_client_types(self, lid, types):
        self.launches[lid]["client_types"] = types
        return copy.deepcopy(self.launches[lid])

    def begin_review(self, lid):
        l = self.launches[lid]
        if l["review_status"] == "running":
            return False
        l.update(review_status="running", review_error="", review_started=qa.dt.datetime.now(qa.dt.timezone.utc).isoformat(),
                 review_progress={"phase": "Starting", "done": 0, "total": 0})
        return True

    def set_progress(self, lid, phase, done, total):
        self.launches[lid]["review_progress"] = {"phase": phase, "done": done, "total": total}

    def finish_review(self, lid, findings, *, campaign_name, channel, campaign_types, note=""):
        self.launches[lid].update(auto=findings, auto_at=qa.dt.datetime.now(qa.dt.timezone.utc).isoformat(), campaign_name=campaign_name, channel=channel,
                                  campaign_types=campaign_types, review_status="done", review_error="", review_note=note,
                                  review_progress={})
        return copy.deepcopy(self.launches[lid])

    def fail_review(self, lid, error):
        self.launches[lid].update(review_status="error", review_error=error, review_progress={})

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


def fake_google(**over):
    """A search(customer_id, gaql) answering from rows made up for one Search campaign, keyed by the
    resource the query reads. Nothing here is a real client's."""
    def row(**k):
        return k
    headlines = [{"text": "Headline %d" % i} for i in range(15)]
    data = {
        "FROM customer WHERE": [],
        "FROM customer": [row(customer={"autoTaggingEnabled": True, "trackingUrlTemplate": "{lpurl}?utm_source=google&utm_medium=cpc",
                                        "currencyCode": "USD", "timeZone": "America/New_York",
                                        "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"})],
        "FROM billing_setup": [row(billingSetup={"status": "APPROVED", "paymentsAccountInfo": {"paymentsAccountName": "Example Co"}})],
        "FROM campaign WHERE campaign.id": [row(campaign={"id": CAMPAIGN, "name": "Example_Brand_Search", "status": "ENABLED",
                                                         "servingStatus": "SERVING", "advertisingChannelType": "SEARCH",
                                                         "networkSettings": {"targetGoogleSearch": True, "targetSearchNetwork": False,
                                                                             "targetContentNetwork": False},
                                                         "geoTargetTypeSetting": {"positiveGeoTargetType": "PRESENCE",
                                                                                  "negativeGeoTargetType": "PRESENCE"},
                                                         "startDateTime": "2026-03-11 04:38:47", "aiMaxSetting": {"enableAiMax": False},
                                                         "biddingStrategyType": "MAXIMIZE_CONVERSIONS"},
                                                 campaignBudget={"amountMicros": "30000000", "explicitlyShared": False})],
        "FROM campaign_shared_set": [row(sharedSet={"name": "Generic Negatives", "type": "NEGATIVE_KEYWORDS", "memberCount": "60"},
                                         campaignSharedSet={"status": "ENABLED"})],
        "FROM ad_group WHERE": [row(adGroup={"id": "11", "name": "Brand", "status": "ENABLED", "type": "SEARCH_STANDARD"})],
        "FROM ad_group_ad": [row(adGroup={"id": "11"}, adGroupAd={"status": "ENABLED", "adStrength": "GOOD",
                                                                  "policySummary": {"approvalStatus": "APPROVED"},
                                                                  "ad": {"type": "RESPONSIVE_SEARCH_AD", "finalUrls": ["https://example.com/"],
                                                                         "responsiveSearchAd": {"headlines": headlines,
                                                                                                "descriptions": [{"text": "d"}] * 4,
                                                                                                "path1": "a", "path2": "b"}}})],
        "FROM ad_group_criterion": [row(adGroup={"id": "11"}, adGroupCriterion={"status": "ENABLED", "negative": False,
                                                                                "keyword": {"text": "example", "matchType": "EXACT"},
                                                                                "systemServingStatus": "ELIGIBLE",
                                                                                "approvalStatus": "APPROVED"})],
        "FROM campaign WHERE campaign.status": [row(campaign={"id": CAMPAIGN, "name": "Example_Brand_Search", "status": "ENABLED",
                                                              "advertisingChannelType": "SEARCH"}),
                                                row(campaign={"id": "9000000002", "name": "Example_PMax", "status": "ENABLED",
                                                              "advertisingChannelType": "PERFORMANCE_MAX"})],
    }
    data.update(over)
    calls = []

    def search(customer_id, gaql):
        calls.append(gaql)
        if "ORDER BY campaign.name" in gaql:
            return data["FROM campaign WHERE campaign.status"]
        if "SELECT campaign.id, campaign.advertising_channel_type FROM campaign" in gaql:
            return data["FROM campaign WHERE campaign.id"][:1] if data["FROM campaign WHERE campaign.id"] else []
        for key in sorted(data, key=len, reverse=True):
            if key in gaql:
                v = data[key]
                if isinstance(v, Exception):
                    raise v
                return v
        return []
    search.calls = calls
    return search


PAGE = {"url": "https://example.com/", "https": True, "status": 200, "finalUrl": "https://example.com/", "redirected": False, "loadMs": 300,
        "paramsSurvive": True, "title": "Example Co landscaping", "text": "Landscaping in Annapolis. Free estimates. Call 410-555-0100.",
        "forms": [{"fields": ["name", "email"], "method": "post"}], "hasCaptcha": False, "telLinks": [], "phonesInText": ["410-555-0100"],
        "privacyLink": {"href": "/privacy", "text": "Privacy"}, "consentBanner": True, "googleTags": ["AW-123456"],
        "robots": {"adsbotAllowed": True}, "viewport": "width=device-width", "noindex": False, "stagingHost": False}


class FakeModel:
    """Stands in for Claude: answers the checks it was told to, from evidence it was really given, and says
    cannot_verify to everything else. Records what it was asked."""
    def __init__(self, answers=None, boom=None):
        self.answers, self.boom, self.asked = answers or {}, boom, []

    def __call__(self, system, user, *, max_tokens):
        import json
        if self.boom:
            raise self.boom
        req = json.loads(user)
        self.asked.append(req)
        out = []
        for c in req["checks"]:
            a = self.answers.get(c["id"])
            if a:
                out.append({"id": c["id"], "verdict": a[0], "finding": a[1], "evidence": a[2], "fix": a[3] if len(a) > 3 else ""})
            else:
                out.append({"id": c["id"], "verdict": "cannot_verify", "finding": "Nothing in the evidence answers this.", "evidence": "", "fix": ""})
        return {"results": out}


DEFAULT_ANSWERS = {
    "3.2": ("pass", "The campaign is named Example_Brand_Search, which follows Client_Type_Theme.", "Example_Brand_Search"),
    "6.2": ("flag", "Headline 3 has a double exclamation mark.", "Headline 3", "Remove the exclamation marks from headline 3 in ad group Brand."),
    "6.4": ("pass", "The offer in the ad matches the page.", "Free estimates"),
    "13.6": ("flag", "The ad promises a free estimate but the page says nothing about it.", "Landscaping in Annapolis",
             "Add the free estimate offer to the landing page or remove it from the ads."),
}


@pytest.fixture
def mem(monkeypatch):
    from tracker import paid_qa_ai, paid_qa_web
    m = MemoryStore()
    for name in ("start_launch", "get_launch", "list_launches", "set_client_types", "begin_review", "set_progress", "finish_review",
                 "fail_review", "archive_launch", "checks_for", "set_check", "clear_check"):
        monkeypatch.setattr(store, name, getattr(m, name))
    monkeypatch.setattr(gads, "search", fake_google())
    monkeypatch.setattr(gads, "geo_names", lambda names, **k: {})
    monkeypatch.setattr(paid_qa_web, "audit_pages", lambda urls, *a, **k: [dict(PAGE, url=u) for u in urls[:1]])
    model = FakeModel(DEFAULT_ANSWERS)
    monkeypatch.setattr(paid_qa_ai, "default_call", model)
    m.model = model
    monkeypatch.setattr(appmod, "_qc_spawn", lambda fn: fn())                  # the review runs inline, so a test sees its result
    monkeypatch.setattr(appmod, "_QC_LAST_RUN", {})
    monkeypatch.setattr(appmod, "_QC_AI_USES", {})
    return m


def client_as(email):
    c = appmod.app.test_client()
    if email:
        with c.session_transaction() as sess:
            sess["google_user"] = {"email": email, "name": "T"}
    return c


NEW = {"accountId": ACCOUNT, "accountName": "Example Co", "campaignId": CAMPAIGN, "campaignName": "Example_Brand_Search",
       "channel": "SEARCH"}


def start(c=None, **over):
    c = c or client_as(STAFF)
    r = c.post(API + "/start", json=dict(NEW, **over), headers=SAME)
    assert r.status_code in (200, 201), r.get_json()
    return c, r.get_json()["launch"]["id"], r.get_json()


def test_the_page_is_for_position2_staff_only():
    page = "/p2/paid-marketing/qa-agent"
    assert client_as(STAFF).get(page).status_code == 200
    assert client_as(None).get(page).status_code == 302
    assert client_as("someone@gmail.com").get(page).status_code == 302


@pytest.mark.parametrize("path", ["/checklist", "/campaigns?account=" + ACCOUNT, "/launches/" + "a" * 32])
def test_the_data_routes_are_not_for_everyone(path, mem):
    assert client_as(None).get(API + path).status_code == 302
    assert client_as("someone@gmail.com").get(API + path).status_code == 302


def test_the_builder_is_gone():
    c = client_as(STAFF)
    assert c.get(API + "/applicable?clientTypes=Lead%20Gen").status_code == 404
    assert c.post(API + "/launches", json=NEW, headers=SAME).status_code in (404, 405)


def test_the_checklist_is_served_and_cached_privately(mem):
    c = client_as(STAFF)
    r = c.get(API + "/checklist")
    assert len(r.get_json()["items"]) == 155 and r.headers["Cache-Control"].startswith("private")


def test_the_campaign_list_is_the_live_campaigns_of_the_account(mem):
    out = client_as(STAFF).get(API + "/campaigns?account=213-386-1213").get_json()["campaigns"]
    assert [c["name"] for c in out] == ["Example_Brand_Search", "Example_PMax"]
    assert out[0]["type"] == "Search" and out[1]["type"] == "PMax" and out[0]["qc"] is None
    assert client_as(STAFF).get(API + "/campaigns?account=abc").status_code == 400


def test_the_campaign_list_asks_google_only_for_enabled_campaigns_and_says_which_have_a_qc(mem):
    c, lid, _ = start()
    out = c.get(API + "/campaigns?account=" + ACCOUNT).get_json()["campaigns"]
    qc = out[0]["qc"]
    assert qc["id"] == lid and qc["verdict"]["code"] in ("not_ready", "incomplete") and qc["checked"] >= 1 and out[1]["qc"] is None
    assert any("campaign.status = 'ENABLED'" in q for q in gads.search.calls)


def test_the_campaign_list_says_when_google_ads_cannot_be_read(monkeypatch, mem):
    def refused(cid, gaql):
        raise gads.GadsError("quota", "Google Ads says the daily limit is used up.")
    monkeypatch.setattr(gads, "search", refused)
    r = client_as(STAFF).get(API + "/campaigns?account=" + ACCOUNT)
    assert r.status_code == 429 and r.get_json()["kind"] == "quota"


def test_picking_a_campaign_starts_the_ai_review_which_flags_what_is_wrong(mem):
    c, lid, body = start()
    assert body["launch"]["review"]["status"] == "done" and body["launch"]["readAt"]
    checks = body["checks"]
    assert checks["1.6"]["status"] == "pass" and checks["1.6"]["source"] == "rule"                 # exact rule, from Google Ads
    flagged = checks["6.2"]
    assert flagged["status"] == "fail" and flagged["source"] == "ai" and flagged["by"] == "QA Agent"
    assert "double exclamation" in flagged["found"] and "exclamation marks" in flagged["fix"] and flagged["evidence"] == "Headline 3"
    assert checks["13.6"]["status"] == "fail" and checks["3.2"]["status"] == "pass"
    assert body["summary"]["failed"] and set(body["summary"]["failed"]) >= {"6.2", "13.6"}
    assert mem.launches[lid]["created_by"] == STAFF and mem.launches[lid]["auto_at"]


def test_what_the_ai_cannot_see_is_left_for_a_person_not_passed(mem):
    c, lid, body = start()
    row = body["checks"]["14.4"]                                      # the client's written sign-off
    assert row["status"] is None and row["unverified"] is True and "Nothing in the evidence" in row["found"]
    assert "14.4" in body["summary"]["needsHuman"] and "14.4" in body["summary"]["pendingCritical"]
    assert body["summary"]["verdict"]["code"] in ("not_ready", "incomplete")


def test_a_critical_flag_makes_the_launch_not_ready_and_an_unverified_critical_makes_it_incomplete(mem):
    mem.model.answers = dict(DEFAULT_ANSWERS, **{"6.3": ("flag", "The final URL redirects to another country site.", "https://example.com/",
                                                         "Point the final URL at the US page.")})
    c, lid, body = start()
    s = body["summary"]
    assert "6.3" in s["failedCritical"] and s["verdict"]["code"] == "not_ready"
    c.put("%s/launches/%s/checks/6.3" % (API, lid), json={"status": "pass", "note": "Checked: it is the US page"}, headers=SAME)
    out = c.get(API + "/launches/" + lid).get_json()["summary"]
    assert "6.3" not in out["failedCritical"] and out["verdict"]["code"] in ("not_ready", "incomplete")


def test_the_ai_is_asked_only_what_the_exact_rules_did_not_settle(mem):
    start()
    asked = {c["id"] for req in mem.model.asked for c in req["checks"]}
    assert "1.6" not in asked and "4.1" not in asked and "5.1" not in asked          # settled by exact rules
    assert {"6.2", "13.6", "14.4"} <= asked
    sample = mem.model.asked[0]
    assert sample["campaign"]["type"] == "Search" and "evidence" in sample and sample["today"]
    assert not any(c["id"].startswith("8.") for req in mem.model.asked for c in req["checks"])     # PMax checks are not for a Search campaign


def test_the_ai_sees_the_evidence_it_needs_for_each_section(mem):
    start()
    by_section = {req["section"]: req for req in mem.model.asked}
    ads = by_section["6. Ad Copy"] if "6. Ad Copy" in by_section else next(r for k, r in by_section.items() if k.startswith("6."))
    assert "adGroups" in ads["evidence"] and ads["evidence"]["adGroups"][0]["ads"][0]["headlines"][0]["text"] == "Headline 0"
    page = next(r for k, r in by_section.items() if k.startswith("13."))
    assert page["evidence"]["landingPages"][0]["text"].startswith("Landscaping in Annapolis")


def test_if_the_ai_is_unavailable_the_exact_rules_still_run_and_everything_else_waits_for_a_person(monkeypatch, mem):
    from tracker import paid_qa_ai
    from tracker.market_radar_llm import ModelError
    monkeypatch.setattr(paid_qa_ai, "default_call", FakeModel(boom=ModelError("not_configured", "ANTHROPIC_API_KEY is not set")))
    c, lid, body = start()
    assert body["checks"]["1.6"]["status"] == "pass" and body["checks"]["6.2"]["status"] is None
    assert "no Anthropic key" in body["launch"]["review"]["note"] and body["launch"]["review"]["status"] == "done"
    assert body["summary"]["verdict"]["code"] in ("not_ready", "incomplete")                 # never READY on rules alone


def test_if_google_ads_cannot_be_read_the_review_says_so_and_the_qc_still_opens(monkeypatch, mem):
    monkeypatch.setattr(gads, "search", lambda cid, gaql: (_ for _ in ()).throw(gads.GadsError("quota", "Google Ads says the daily limit is used up.")))
    c = client_as(STAFF)
    r = c.post(API + "/start", json=NEW, headers=SAME)
    body = r.get_json()
    assert r.status_code == 201 and body["launch"]["review"]["status"] == "error" and "daily limit" in body["launch"]["review"]["error"]
    assert body["checks"] == {} and body["summary"]["total"] > 0
    lid = body["launch"]["id"]
    assert c.put("%s/launches/%s/checks/1.1" % (API, lid), json={"status": "pass"}, headers=SAME).status_code == 200


def test_a_campaign_that_is_not_in_the_account_is_reported(monkeypatch, mem):
    monkeypatch.setattr(gads, "search", fake_google(**{"FROM campaign WHERE campaign.id": []}))
    r = client_as(STAFF).post(API + "/start", json=NEW, headers=SAME)
    assert r.get_json()["launch"]["review"]["status"] == "error" and "not found in this account" in r.get_json()["launch"]["review"]["error"]


def test_opening_the_same_campaign_again_resumes_the_one_qc_without_reviewing_again(mem):
    c, lid, _ = start()
    calls, asked = len(gads.search.calls), len(mem.model.asked)
    c2, lid2, body = start(client_as(OTHER))
    assert lid2 == lid and len(mem.launches) == 1 and len(gads.search.calls) == calls and len(mem.model.asked) == asked
    assert c.post(API + "/start", json=NEW, headers=SAME).status_code == 200                # resumed, not created


def test_a_review_in_progress_reports_its_progress_and_is_not_started_twice(mem):
    c, lid, _ = start()
    mem.launches[lid].update(review_status="running", review_started="2026-10-14T10:00:30+00:00",
                             review_progress={"phase": "The AI is reviewing the checklist", "done": 3, "total": 14})
    body = c.get(API + "/launches/" + lid).get_json()
    assert body["launch"]["review"]["status"] in ("running", "error")                       # error only once it has gone stale
    mem.launches[lid]["review_started"] = "2999-01-01T00:00:00+00:00"                        # not stale
    body = c.get(API + "/launches/" + lid).get_json()
    assert body["launch"]["review"] == {"status": "running", "phase": "The AI is reviewing the checklist", "done": 3, "total": 14,
                                        "error": "", "note": ""}
    assert c.post("%s/launches/%s/run" % (API, lid), json={}, headers=SAME).status_code in (409, 429)


def test_a_review_that_died_with_its_server_is_shown_as_stopped(mem):
    c, lid, _ = start()
    mem.launches[lid].update(review_status="running", review_started="2026-10-14T09:00:00+00:00")
    r = c.get(API + "/launches/" + lid).get_json()["launch"]["review"]
    assert r["status"] == "error" and "stopped before it finished" in r["error"]


def test_a_person_confirming_or_overruling_beats_the_ai(mem):
    c, lid, _ = start()
    url = "%s/launches/%s/checks/6.2" % (API, lid)
    r = c.put(url, json={"status": "pass", "note": "That is our style, approved by the client"}, headers=SAME)
    body = r.get_json()
    assert r.status_code == 200 and body["check"]["status"] == "pass" and body["check"]["source"] == "person" and body["check"]["by"] == STAFF
    assert "double exclamation" in body["check"]["found"]                                  # the AI's finding stays visible
    r = c.put(url, json={"status": None}, headers=SAME)                                    # undo: back to the AI's flag
    assert r.get_json()["check"]["status"] == "fail" and r.get_json()["check"]["source"] == "ai"
    assert "6.2" not in mem.checks[lid]


def test_confirming_what_the_ai_could_not_verify_clears_it_from_the_needs_a_human_list(mem):
    c, lid, first = start()
    assert "14.4" in first["summary"]["needsHuman"]
    r = c.put("%s/launches/%s/checks/14.4" % (API, lid), json={"status": "pass", "note": "Signed off by email on 2026-10-10"}, headers=SAME)
    assert "14.4" not in r.get_json()["summary"]["needsHuman"] and "14.4" not in r.get_json()["summary"]["pendingCritical"]


def test_wrong_needs_its_reason_and_keeps_it(mem):
    c, lid, _ = start()
    url = "%s/launches/%s/checks/1.2" % (API, lid)
    r = c.put(url, json={"status": "fail"}, headers=SAME)
    assert r.status_code == 400 and "needs a reason" in r.get_json()["error"] and "1.2" not in mem.checks[lid]
    r = c.put(url, json={"status": "fail", "note": "Prepay balance is $0: add funds"}, headers=SAME)
    assert r.status_code == 200 and "1.2" in r.get_json()["summary"]["failedCritical"]
    assert c.get(API + "/launches/" + lid).get_json()["checks"]["1.2"]["note"] == "Prepay balance is $0: add funds"


def test_a_check_that_does_not_show_or_does_not_exist_cannot_be_answered(mem):
    c, lid, _ = start()
    for check in ("8.1", "8.5", "99.9"):                       # PMax only, Ecommerce only (none chosen), not a check
        assert c.put("%s/launches/%s/checks/%s" % (API, lid, check), json={"status": "pass"}, headers=SAME).status_code == 400
    assert mem.checks[lid] == {}


def test_choosing_a_client_type_adds_the_checks_that_exist_only_for_it_and_the_ai_already_answered_them(mem):
    c, lid, first = start()
    assert "7.9" not in first["summary"]["applicable"] and first["needsClientType"]["count"] > 0
    r = c.put("%s/launches/%s" % (API, lid), json={"clientTypes": ["Lead Gen"]}, headers=SAME)
    body = r.get_json()
    assert r.status_code == 200 and body["summary"]["total"] > first["summary"]["total"]
    assert "7.9" in body["summary"]["applicable"] and "7.9" in body["checks"]               # reviewed in the first pass: no second review
    assert c.put("%s/launches/%s" % (API, lid), json={"clientTypes": ["Bogus"]}, headers=SAME).status_code == 400


def test_rerunning_reviews_again_and_a_persons_answers_stay(mem, monkeypatch):
    c, lid, _ = start()
    c.put("%s/launches/%s/checks/6.2" % (API, lid), json={"status": "na", "note": "no ads yet"}, headers=SAME)
    mem.model.answers = {"6.2": ("pass", "Headlines now read fine.", "Headline 3")}
    monkeypatch.setattr(appmod, "_QC_LAST_RUN", {})
    r = c.post("%s/launches/%s/run" % (API, lid), json={}, headers=SAME)
    body = r.get_json()
    assert r.status_code == 200 and body["checks"]["6.2"]["status"] == "na" and body["checks"]["6.2"]["source"] == "person"
    assert body["checks"]["6.2"]["found"].startswith("Headlines now read fine")             # the new finding is printed
    assert "13.6" not in body["summary"]["failed"]                                          # the old flag is gone with the old review


def test_rerunning_too_soon_is_asked_to_wait(mem):
    c, lid, _ = start()
    first = c.post("%s/launches/%s/run" % (API, lid), json={}, headers=SAME)
    again = c.post("%s/launches/%s/run" % (API, lid), json={}, headers=SAME)
    assert first.status_code == 200 and again.status_code == 429 and again.get_json()["kind"] == "cooldown"


def test_there_is_a_daily_cap_on_ai_reviews_per_person(mem, monkeypatch):
    monkeypatch.setattr(appmod, "_QC_AI_DAILY_CAP", 1)
    c, lid, _ = start()
    monkeypatch.setattr(appmod, "_QC_LAST_RUN", {})
    r = c.post("%s/launches/%s/run" % (API, lid), json={}, headers=SAME)
    assert r.status_code == 429 and r.get_json()["kind"] == "cap" and "AI reviews in a day" in r.get_json()["error"]


def test_only_whoever_started_a_qc_or_an_admin_can_remove_it(mem):
    c, lid, _ = start(client_as(STAFF))
    assert client_as(OTHER).delete("%s/launches/%s" % (API, lid), json={}, headers=SAME).status_code == 403
    assert not mem.launches[lid]["archived"]
    assert client_as(STAFF).delete("%s/launches/%s" % (API, lid), json={}, headers=SAME).status_code == 200
    assert client_as(STAFF).get("%s/launches/%s" % (API, lid)).status_code == 404            # gone from view
    c2, lid2, _ = start(client_as(STAFF))
    assert lid2 != lid                                                                        # the campaign can start a fresh QC
    assert client_as(ADMIN).delete("%s/launches/%s" % (API, lid2), json={}, headers=SAME).status_code == 200


def test_ids_are_checked_before_anything_is_looked_up(mem):
    c = client_as(STAFF)
    for bad in ("x", "../etc", "A" * 32, "a" * 31):
        assert c.get("%s/launches/%s" % (API, bad)).status_code == 404
    assert c.get("%s/launches/%s" % (API, "a" * 32)).status_code == 404                       # well formed, not there
    assert c.put("%s/launches/%s/checks/1.1" % (API, "a" * 32), json={"status": "pass"}, headers=SAME).status_code == 404
    assert c.post("%s/launches/%s/run" % (API, "a" * 32), json={}, headers=SAME).status_code == 404


def test_cross_site_and_non_json_writes_are_refused(mem):
    c, lid, _ = start()
    url = "%s/launches/%s/checks/3.2" % (API, lid)
    assert c.put(url, json={"status": "pass"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.put(url, data="status=pass", headers=SAME).status_code == 415
    assert c.post(API + "/start", json=NEW, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("%s/launches/%s/run" % (API, lid), json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert "3.2" not in mem.checks[lid]


def test_a_bad_start_is_refused_in_words(mem):
    c = client_as(STAFF)
    for patch, says in (({"accountId": "12"}, "10 digits"), ({"campaignId": ""}, "Pick a campaign"), ({"campaignName": ""}, "campaign name")):
        r = c.post(API + "/start", json=dict(NEW, **patch), headers=SAME)
        assert r.status_code == 400 and says in r.get_json()["error"]
    assert not mem.launches


def test_a_store_outage_is_a_503_with_a_reason(monkeypatch):
    def down(*a, **k):
        raise store.StoreUnavailable("DATABASE_URL is not set")
    monkeypatch.setattr(store, "start_launch", down)
    r = client_as(STAFF).post(API + "/start", json=NEW, headers=SAME)
    assert r.status_code == 503 and "DATABASE_URL" in r.get_json()["error"]


def test_the_campaign_list_still_works_when_the_store_is_down(monkeypatch):
    monkeypatch.setattr(gads, "search", fake_google())
    monkeypatch.setattr(store, "list_launches", lambda *a, **k: (_ for _ in ()).throw(store.StoreUnavailable("no db")))
    out = client_as(STAFF).get(API + "/campaigns?account=" + ACCOUNT)
    assert out.status_code == 200 and len(out.get_json()["campaigns"]) == 2


def test_the_server_never_trusts_a_verdict_from_the_page(mem):
    c, lid, _ = start()
    r = c.put("%s/launches/%s/checks/3.2" % (API, lid), json={"status": "pass", "verdict": "ready", "summary": {"verdict": {"code": "ready"}}},
              headers=SAME)
    assert r.get_json()["summary"]["verdict"]["code"] != "ready"
