"""Paid Marketing Agents: QA Agent, the Campaign Book QC done by the AI on a live campaign.

Pinned here: the checklist data is the Campaign Book's (61 checks in 5 sections) plus 4 done on the spot for Performance Max and Demand Gen; which checks show
for a campaign (its own type); the verdict (any check flagged is NOT READY, any check not yet
verified is INCOMPLETE, else READY, with N/A counting as done); a person's wrong needs a reason; a
person's answer beats the AI's; the storage SQL and its migration are valid Postgres; and the routes
are staff-only, refuse cross-site requests, accept a campaign book as a file or a Google Sheet link, and
never let a page claim a verdict the checks do not support. What the rules read from Google Ads is
tests/test_paid_qa_auto.py, the AI's guard rails tests/test_paid_qa_ai.py.
"""
import base64
import copy
import io
import os
import re
import sys

import openpyxl
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

def test_the_checklist_is_the_campaign_books():
    cl = qa.checklist()
    items = cl["items"]
    assert len(items) == 65 and len({i["id"] for i in items}) == 65
    assert cl["sections"] == ["1. Campaign Setup", "2. Automation", "3. Automatically Created Assets", "4. Auto-Apply Recommendations",
                              "5. Account Automation", "6. On the spot (Performance Max and Demand Gen)"]
    assert [sum(1 for i in items if i["section"] == s) for s in cl["sections"]] == [25, 2, 11, 21, 2, 4]
    assert cl["severities"] == ["Required"] and cl["blocking"] == ["Required"] and cl["clientTypes"] == []
    assert qa.item("1.1")["check"] == "Campaign Name" and qa.item("2.1")["check"] == "Final URL Expansion"
    assert qa.item("4.21")["check"].startswith("Add store visits") and qa.item("5.2")["check"].startswith("Auto Migration")


def test_every_check_has_what_the_ai_and_the_page_need():
    cl = qa.checklist()
    for i in cl["items"]:
        assert i["check"] and i["verify"] and i["section"] in cl["sections"] and i["severity"] == "Required", i["id"]
        assert i["id"].startswith(str(cl["sections"].index(i["section"]) + 1) + "."), i["id"]
        assert i["campaignTypes"] in (["All"], ["Search"], ["PMax"], ["Demand Gen"]) and i["clientTypes"] == ["All"], i["id"]
        assert isinstance(i["fromBook"], bool) and "expected" in i and "otherThingsToCheck" in i, i["id"]
    assert [i["id"] for i in qa.items() if i["fromBook"]] == ["1.2", "1.8", "1.10", "1.11", "1.12", "1.14", "1.16", "1.24"]
    assert qa.item("1.7")["expected"] == "Account Level Default Goals"                       # the workbook's typo is fixed
    assert "Check the assets like callouts" in qa.item("1.6")["otherThingsToCheck"]


def test_no_text_in_the_checklist_uses_an_em_dash():
    assert "—" not in open(qa.CHECKLIST_PATH, encoding="utf-8").read()


# ── Which checks show ───────────────────────────────────────────────────────

def test_every_check_shows_for_a_search_campaign_and_the_search_only_ones_do_not_for_pmax():
    assert len(qa.applicable([], ["Search"])) == 61
    pmax = {i["id"] for i in qa.applicable([], ["PMax"])}
    assert len(pmax) == 61 and not ({"1.3", "1.5", "1.23"} & pmax)                       # networks, AI Max, ad rotation are Search only
    assert {"6.1", "6.2", "6.3"} <= pmax and "6.4" not in pmax                           # PMax has its own three on-the-spot checks
    dg = {i["id"] for i in qa.applicable([], ["Demand Gen"])}
    assert len(dg) == 59 and "6.4" in dg and not ({"6.1", "6.2", "6.3"} & dg)
    assert not any(i["id"].startswith("6.") for i in qa.applicable([], ["Search"]))
    assert len(qa.applicable([], [])) == 65                                              # an unmapped campaign type narrows nothing


def test_the_campaign_type_comes_from_the_google_ads_channel():
    assert qa.campaign_types_for("SEARCH") == ["Search"]
    assert qa.campaign_types_for("SEARCH", dsa=True) == ["Search", "Search (DSA)"]
    assert qa.campaign_types_for("PERFORMANCE_MAX") == ["PMax"]
    assert qa.campaign_types_for("MULTI_CHANNEL") == ["App"]
    assert qa.campaign_types_for("SMART") == [] and qa.campaign_types_for("") == []


def test_there_is_no_client_type_to_wait_for():
    assert qa.hidden_by_client(launch(campaign_types=["Search"])) == {"count": 0, "blocking": 0, "types": []}


# ── The verdict ─────────────────────────────────────────────────────────────

def launch(**kw):
    return dict({"client_types": [], "campaign_types": ["Search"]}, **kw)


def ticks(status, ids):
    return {i: {"status": status} for i in ids}


SHOWN = qa.applicable([], ["Search"])
ALL = [i["id"] for i in SHOWN]


def test_nothing_done_is_incomplete_not_ready():
    s = qa.summarize(launch(), {})
    assert s["verdict"] == {"code": "incomplete", "text": "INCOMPLETE: checks still need a human"}
    assert s["total"] == 61 and s["checked"] == 0 and s["percent"] == 0 and len(s["pendingBlocking"]) == 61


def test_every_check_right_is_ready():
    s = qa.summarize(launch(), ticks("pass", ALL))
    assert s["verdict"] == {"code": "ready", "text": "READY TO LAUNCH"} and s["pendingBlocking"] == [] and s["percent"] == 100


def test_one_check_wrong_is_not_ready_whatever_else_is_done():
    checks = ticks("pass", ALL)
    checks["1.5"] = {"status": "fail", "note": "AI Max is on"}
    s = qa.summarize(launch(), checks)
    assert s["verdict"] == {"code": "not_ready", "text": "NOT READY: a check failed"}
    assert s["failedBlocking"] == ["1.5"] and s["failed"] == ["1.5"]


def test_one_check_still_open_is_incomplete_and_na_counts_as_done():
    checks = ticks("pass", [i for i in ALL if i != "3.4"])
    assert qa.summarize(launch(), checks)["verdict"]["code"] == "incomplete"
    checks["3.4"] = {"status": "na"}
    assert qa.summarize(launch(), checks)["verdict"]["code"] == "ready"


def test_counts_add_up_by_severity_and_by_section():
    checks = {**ticks("pass", ["1.1", "1.2"]), "1.3": {"status": "fail"}, "1.4": {"status": "na"}}
    s = qa.summarize(launch(), checks)
    assert sum(b["total"] for b in s["bySeverity"].values()) == s["total"] == 61 and list(s["bySeverity"]) == ["Required"]
    for b in list(s["bySeverity"].values()) + s["bySection"]:
        assert b["total"] == b["pass"] + b["fail"] + b["na"] + b["pending"]
    sec1 = s["bySection"][0]
    assert sec1["section"] == "1. Campaign Setup" and (sec1["total"], sec1["pass"], sec1["fail"], sec1["na"]) == (25, 2, 1, 1)
    assert s["checked"] == 4 and len(s["bySection"]) == 5


def test_answers_on_checks_that_no_longer_show_do_not_count():
    pmax = launch(campaign_types=["PMax"])                                               # 1.5 (AI Max) is Search only
    assert "1.5" not in qa.summarize(pmax, {})["applicable"]
    s = qa.summarize(pmax, {"1.5": {"status": "fail", "note": "x" * 5}})
    assert s["failed"] == [] and s["verdict"]["code"] == "incomplete"


# ── The AI's reading and a person's answer ──────────────────────────────────

AT = "2026-10-14T10:00:00+00:00"


def test_the_ais_and_the_rules_pass_and_fail_become_the_checks_state_with_their_finding():
    L = launch(campaign_types=["Search"])
    rows = qa.effective(L, {}, {"1.5": {"result": "pass", "found": "AI Max for Search is OFF.", "source": "rule"},
                                "1.8": {"result": None, "found": "No campaign book", "source": "ai", "unverified": True},
                                "1.4": {"result": "fail", "found": "Headline 3 shouts", "fix": "Remove the !!", "evidence": "Headline 3", "source": "ai"}}, AT)
    assert rows["1.5"]["status"] == "pass" and rows["1.5"]["source"] == "rule" and rows["1.5"]["by"] == "QA Agent" and rows["1.5"]["at"] == AT
    assert rows["1.4"]["status"] == "fail" and rows["1.4"]["source"] == "ai" and rows["1.4"]["fix"] == "Remove the !!" and rows["1.4"]["evidence"] == "Headline 3"
    assert rows["1.8"]["status"] is None and rows["1.8"]["unverified"] is True and rows["1.8"]["found"] == "No campaign book"
    assert "1.1" not in rows                                                              # nothing read, nothing answered


def test_a_persons_answer_always_beats_the_ai_and_clearing_it_brings_the_ai_back():
    L = launch(campaign_types=["Search"])
    auto = {"1.3": {"result": "fail", "found": "Search partners are ON", "source": "rule"}}
    mine = {"1.3": {"status": "pass", "note": "Client approved partners in writing", "by": STAFF, "at": AT}}
    row = qa.effective(L, mine, auto, AT)["1.3"]
    assert row["status"] == "pass" and row["source"] == "person" and row["note"].startswith("Client approved")
    assert row["found"] == "Search partners are ON"                                       # the finding is still printed
    assert qa.effective(L, {}, auto, AT)["1.3"]["status"] == "fail"


def test_the_ai_never_answers_a_check_that_does_not_show():
    L = launch(campaign_types=["PMax"])
    assert "1.5" not in qa.effective(L, {}, {"1.5": {"result": "fail", "found": "AI Max is ON"}}, AT)


def test_an_ai_flag_makes_the_launch_not_ready_until_a_person_overrules_it():
    L = launch(campaign_types=["Search"])
    done = {c: {"status": "pass"} for c in ALL if c != "1.3"}
    rows = qa.effective(L, {}, {"1.3": {"result": "fail", "found": "Search partners are ON"}}, AT)
    assert qa.summarize(L, {**done, **rows})["verdict"]["code"] == "not_ready"
    fixed = qa.effective(L, {"1.3": {"status": "pass", "note": "approved", "by": STAFF, "at": AT}},
                         {"1.3": {"result": "fail", "found": "Search partners are ON"}}, AT)
    assert qa.summarize(L, {**done, **fixed})["verdict"]["code"] == "ready"


def test_a_check_the_ai_could_not_verify_keeps_the_launch_incomplete_until_a_person_confirms_it():
    L = launch(campaign_types=["Search"])
    done = {c: {"status": "pass"} for c in ALL if c != "3.4"}
    unv = {"3.4": {"result": None, "found": "The switch is not visible to the API", "source": "ai", "unverified": True}}
    s = qa.summarize(L, {**done, **qa.effective(L, {}, unv, AT)})
    assert s["verdict"]["code"] == "incomplete" and s["pendingBlocking"] == ["3.4"] and s["needsHuman"] == ["3.4"]
    mine = qa.effective(L, {"3.4": {"status": "pass", "note": "Checked in the account settings", "by": STAFF, "at": AT}}, unv, AT)
    assert qa.summarize(L, {**done, **mine})["verdict"]["code"] == "ready"


def test_the_review_view_says_what_the_ai_is_doing_and_notices_a_dead_one():
    base = {"review_status": "running", "review_progress": {"phase": "Reading", "done": 2, "total": 9}, "review_error": "", "review_note": "",
            "review_started": "2026-10-14T10:00:00+00:00"}
    now = qa.dt.datetime.fromisoformat("2026-10-14T10:03:00+00:00")
    assert qa.review_view(base, now) == {"status": "running", "phase": "Reading", "done": 2, "total": 9, "error": "", "note": ""}
    dead = qa.review_view(base, qa.dt.datetime.fromisoformat("2026-10-14T10:20:00+00:00"))
    assert dead["status"] == "error" and "stopped before it finished" in dead["error"]
    assert qa.review_view({"review_status": ""}, now)["status"] == ""


def test_the_page_payload_has_the_launch_the_book_every_state_and_the_verdict():
    L = {"id": "a" * 32, "account_id": ACCOUNT, "account_name": "A", "campaign_id": CAMPAIGN, "campaign_name": "P2_Search_20261014",
         "channel": "SEARCH", "client_types": [], "campaign_types": ["Search"], "auto": {"1.5": {"result": "pass", "found": "OFF", "source": "rule"}},
         "auto_at": AT, "created_by": STAFF, "created_at": AT, "updated_at": AT, "archived": False,
         "book": {"filename": "Book.xlsx", "source": "upload", "rows": [{"field": "Devices", "value": "x", "other": ""}], "sheets": ["Sheet1"], "text": "t"}}
    p = qa.payload(L, {})
    assert p["launch"]["campaignName"] == "P2_Search_20261014" and p["launch"]["readAt"] == AT and p["launch"]["campaignTypes"] == ["Search"]
    assert p["launch"]["book"]["filename"] == "Book.xlsx" and p["launch"]["book"]["fields"] == 1 and "text" not in p["launch"]["book"]
    assert p["checks"]["1.5"]["status"] == "pass" and p["summary"]["checked"] == 1
    assert qa.payload(dict(L, book={}), {})["launch"]["book"] is None


# ── What a person's answer may look like ────────────────────────────────────

def test_wrong_needs_a_reason():
    L = launch()
    assert qa.clean_check(L, "1.1", {"status": "pass"}) == ("pass", "")
    assert qa.clean_check(L, "1.1", {"status": "na", "note": "  client has none  "}) == ("na", "client has none")
    assert qa.clean_check(L, "1.1", {"status": "fail", "note": "Name does not follow the format"}) == ("fail", "Name does not follow the format")
    for bad in ({"status": "fail"}, {"status": "fail", "note": ""}, {"status": "fail", "note": "  ok "}):
        with pytest.raises(qa.BadPayload, match="needs a reason"):
            qa.clean_check(L, "1.1", bad)


def test_status_check_id_and_size_are_validated():
    L = launch(campaign_types=["Search"])
    assert qa.clean_check(L, "1.1", {"status": None}) == (None, "") and qa.clean_check(L, "1.1", {}) == (None, "")
    for body, says in (({"status": "done"}, "status should be"), ({"status": "PASS"}, "status should be"),
                       ({"status": "pass", "note": "x" * 2001}, "over 2000"), ({"status": "pass", "note": 5}, "should be text"),
                       ("pass", "JSON object")):
        with pytest.raises(qa.BadPayload, match=says):
            qa.clean_check(L, "1.1", body)
    with pytest.raises(qa.BadPayload, match="not a check"):
        qa.clean_check(L, "99.9", {"status": "pass"})
    with pytest.raises(qa.BadPayload, match="does not apply"):
        qa.clean_check(launch(campaign_types=["PMax"]), "1.5", {"status": "pass"})       # AI Max is Search only


def test_starting_a_qc_needs_an_account_and_a_campaign():
    ok = qa.clean_start({"accountId": "213-386-1213", "accountName": " Beta Bionics ", "campaignId": "900-000-0001",
                         "campaignName": " Brand Campaign ", "channel": "SEARCH"})
    assert ok == {"account_id": "2133861213", "account_name": "Beta Bionics", "campaign_id": "9000000001",
                  "campaign_name": "Brand Campaign", "channel": "SEARCH"}
    assert qa.clean_start({"accountId": ACCOUNT, "accountName": "A", "campaignId": "1", "campaignName": "C", "channel": "SMART"})["channel"] == ""
    base = {"accountId": ACCOUNT, "accountName": "A", "campaignId": CAMPAIGN, "campaignName": "C"}
    for patch, says in (({"accountId": "123"}, "10 digits"), ({"accountName": ""}, "account name is needed"),
                        ({"campaignId": ""}, "Pick a campaign"), ({"campaignId": "abc"}, "Pick a campaign"),
                        ({"campaignName": " "}, "campaign name is needed"), ({"campaignName": "n" * 121}, "over 120")):
        with pytest.raises(qa.BadPayload, match=says):
            qa.clean_start(dict(base, **patch))
    with pytest.raises(qa.BadPayload):
        qa.clean_start(["x"])


def test_client_types_are_gone_from_the_checklist_so_none_are_accepted():
    assert qa.clean_client_types({"clientTypes": []}) == [] and qa.clean_client_types({}) == []
    for bad, says in (({"clientTypes": ["Lead Gen"]}, "does not know"), ({"clientTypes": "Lead Gen"}, "should be a list")):
        with pytest.raises(qa.BadPayload, match=says):
            qa.clean_client_types(bad)


# ── The storage SQL is Postgres ─────────────────────────────────────────────

LAUNCH_ROW = ("a" * 32, ACCOUNT, "A", CAMPAIGN, "C", "SEARCH", [], ["Search"], {}, None, "u", None, None, False, "", {}, "", "", None, {})


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
    assert created and launch_row["campaign_id"] == CAMPAIGN and launch_row["book"] == {}
    store.get_launch("a" * 32)
    store.list_launches(ACCOUNT)
    store.set_client_types("a" * 32, [])
    assert store.begin_review("a" * 32) is True
    store.set_progress("a" * 32, "The AI is reviewing the checklist", 2, 9)
    store.finish_review("a" * 32, {"1.5": {"result": "pass", "found": "OFF", "source": "rule"}}, campaign_name="C", channel="SEARCH",
                        campaign_types=["Search"], note="")
    store.fail_review("a" * 32, "Google Ads says the daily limit is used up.")
    store.set_book("a" * 32, {"filename": "b.xlsx", "rows": []})
    store.set_book("a" * 32, {})
    store.archive_launch("a" * 32)
    store.checks_for(["a" * 32, "b" * 32])
    store.set_check("a" * 32, "1.1", "fail", "why", "u")
    store.clear_check("a" * 32, "1.1")
    assert len(log) >= 19
    for sql in log:
        n = iter(range(1, 100))
        pglast.parse_sql(re.sub(r"%s", lambda m: "$%d" % next(n), sql))


def test_the_schema_has_the_tables_a_cascade_the_migrations_and_one_live_qc_per_campaign():
    s = store.SCHEMA
    assert "pm_qc_launches" in s and "pm_qc_checks" in s and "ON DELETE CASCADE" in s and "PRIMARY KEY (launch_id, check_id)" in s
    assert "UNIQUE INDEX" in s and "(account_id, campaign_id) WHERE NOT archived" in s
    # an earlier version's table may exist on a deployed database: it is brought up to shape, its rows retired,
    # and only then is the one-live-QC-per-campaign index built (old rows all have an empty campaign id)
    assert s.index("ADD COLUMN IF NOT EXISTS campaign_id") < s.index("SET archived = true WHERE campaign_id = ''") < s.index("UNIQUE INDEX")
    assert "ALTER COLUMN name DROP NOT NULL" in s and "ADD COLUMN IF NOT EXISTS review_started" in s and "ADD COLUMN IF NOT EXISTS book JSONB" in s
    # the Campaign Book checks replaced the 155-check list: old answers and reviews are cleared once, guarded by a marker column
    assert "column_name = 'checklist'" in s and "DELETE FROM pm_qc_checks" in s and s.index("DELETE FROM pm_qc_checks") < s.index("ADD COLUMN IF NOT EXISTS book") + 2000


# ── Routes, on an in-memory store, a fake Google Ads, fake pages and a fake model ──

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
                                  review_error="", review_note="", review_started=None, book={})
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

    def set_book(self, lid, book):
        self.launches[lid]["book"] = copy.deepcopy(book or {})
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
        self.launches[lid].update(auto=findings, auto_at=qa.dt.datetime.now(qa.dt.timezone.utc).isoformat(), campaign_name=campaign_name,
                                  channel=channel, campaign_types=campaign_types, review_status="done", review_error="", review_note=note,
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
        self.checks[lid][cid] = {"status": status, "note": note, "by": by, "at": qa.dt.datetime.now(qa.dt.timezone.utc).isoformat()}
        return copy.deepcopy(self.checks[lid][cid])

    def clear_check(self, lid, cid):
        self.checks[lid].pop(cid, None)
        return True


def fake_google(**over):
    """A search(customer_id, gaql) answering from rows made up for one Search campaign that follows the Campaign Book,
    keyed by the resource the query reads. Nothing here is a real client's."""
    def row(**k):
        return k
    headlines = [{"text": "Headline %d" % i} for i in range(14)] + [{"text": "Free estimates today!!"}]
    data = {
        "FROM customer": [row(customer={"autoTaggingEnabled": True, "trackingUrlTemplate": "{lpurl}?utm_source=google&utm_medium=cpc",
                                        "currencyCode": "USD", "timeZone": "America/New_York",
                                        "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"})],
        "FROM recommendation_subscription": [],
        "FROM campaign WHERE campaign.id": [row(campaign={"id": CAMPAIGN, "name": "P2_Search_20261014", "status": "ENABLED",
                                                         "servingStatus": "SERVING", "advertisingChannelType": "SEARCH",
                                                         "networkSettings": {"targetGoogleSearch": True, "targetSearchNetwork": False,
                                                                             "targetContentNetwork": False, "targetYoutube": False},
                                                         "geoTargetTypeSetting": {"positiveGeoTargetType": "PRESENCE",
                                                                                  "negativeGeoTargetType": "PRESENCE"},
                                                         "startDateTime": "2026-10-14 09:00:00", "aiMaxSetting": {"enableAiMax": False},
                                                         "biddingStrategyType": "MAXIMIZE_CONVERSIONS",
                                                         "assetAutomationSettings": [{"assetAutomationType": "TEXT_ASSET_AUTOMATION",
                                                                                      "assetAutomationStatus": "OPTED_OUT"},
                                                                                     {"assetAutomationType": "FINAL_URL_EXPANSION_TEXT_ASSET_AUTOMATION",
                                                                                      "assetAutomationStatus": "OPTED_OUT"}]},
                                                 campaignBudget={"amountMicros": "30000000", "explicitlyShared": False})],
        "FROM campaign_criterion": [row(campaignCriterion={"type": "LANGUAGE", "language": {"languageConstant": "languageConstants/1000"}}),
                                    row(campaignCriterion={"type": "DEVICE", "device": {"type": "TABLET"}, "bidModifier": 0})],
        "FROM language_constant": [row(languageConstant={"id": "1000", "name": "English"})],
        "FROM campaign_shared_set": [row(sharedSet={"name": "Generic Negatives", "type": "NEGATIVE_KEYWORDS", "memberCount": "60"},
                                         campaignSharedSet={"status": "ENABLED"})],
        "FROM shared_set": [row(sharedSet={"name": "Generic Negatives", "type": "NEGATIVE_KEYWORDS", "memberCount": "60"})],
        "FROM campaign_conversion_goal": [row(campaignConversionGoal={"category": "PURCHASE", "origin": "WEBSITE", "biddable": True})],
        "FROM customer_conversion_goal": [row(customerConversionGoal={"category": "PURCHASE", "origin": "WEBSITE", "biddable": True})],
        "FROM campaign_asset": [row(campaignAsset={"fieldType": "SITELINK", "status": "ENABLED", "source": "ADVERTISER"},
                                    asset={"type": "SITELINK", "finalUrls": ["https://example.com/a"], "sitelinkAsset": {"linkText": "Our work"}})] * 4,
        "FROM customer_asset": [row(customerAsset={"fieldType": "CALLOUT", "status": "ENABLED", "source": "ADVERTISER"},
                                    asset={"type": "CALLOUT", "calloutAsset": {"calloutText": "Free estimates"}})] * 4,
        "FROM ad_group WHERE": [row(adGroup={"id": "11", "name": "Brand", "status": "ENABLED", "type": "SEARCH_STANDARD"})],
        "FROM ad_group_ad": [row(adGroup={"id": "11"}, adGroupAd={"status": "ENABLED", "adStrength": "GOOD",
                                                                  "policySummary": {"approvalStatus": "APPROVED"},
                                                                  "ad": {"type": "RESPONSIVE_SEARCH_AD", "finalUrls": ["https://example.com/"],
                                                                         "responsiveSearchAd": {"headlines": headlines,
                                                                                                "descriptions": [{"text": "d"}] * 4,
                                                                                                "path1": "a", "path2": "b"}}})],
        "FROM ad_group_criterion": [row(adGroup={"id": "11", "name": "Brand"}, adGroupCriterion={"type": "AGE_RANGE", "status": "ENABLED",
                                                                                                  "ageRange": {"type": "AGE_RANGE_25_34"}})],
        "FROM campaign WHERE campaign.status": [row(campaign={"id": CAMPAIGN, "name": "P2_Search_20261014", "status": "ENABLED",
                                                              "advertisingChannelType": "SEARCH"}),
                                                row(campaign={"id": "9000000002", "name": "P2_PMax_20261014", "status": "ENABLED",
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
    "1.4": ("flag", "The last headline ends with two exclamation marks.", "Free estimates today!!",
            "Remove the exclamation marks from the last headline in ad group Brand."),
    "1.6": ("pass", "4 sitelinks and 4 callouts are available, with a call-to-action sitelink text.", "Our work"),
}


@pytest.fixture
def mem(monkeypatch):
    from tracker import paid_qa_ai, paid_qa_web
    m = MemoryStore()
    for name in ("start_launch", "get_launch", "list_launches", "set_client_types", "set_book", "begin_review", "set_progress",
                 "finish_review", "fail_review", "archive_launch", "checks_for", "set_check", "clear_check"):
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


NEW = {"accountId": ACCOUNT, "accountName": "Example Co", "campaignId": CAMPAIGN, "campaignName": "P2_Search_20261014", "channel": "SEARCH"}


def start(c=None, **over):
    c = c or client_as(STAFF)
    r = c.post(API + "/start", json=dict(NEW, **over), headers=SAME)
    assert r.status_code in (200, 201), r.get_json()
    return c, r.get_json()["launch"]["id"], r.get_json()


def book_xlsx(rows=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows or [["Field", "Value", "Other things to Check"], ["Bid Strategy Type", "Maximize conversions"], ["Devices", "Desktop and mobile, no tablets"],
                      ["Genders", "Women"]]:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def upload(c, lid, raw=None, name="Campaign Book.xlsx"):
    return c.post("%s/launches/%s/book" % (API, lid), json={"filename": name, "data": base64.b64encode(raw or book_xlsx()).decode()}, headers=SAME)


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
    r = client_as(STAFF).get(API + "/checklist")
    assert len(r.get_json()["items"]) == 65 and r.headers["Cache-Control"].startswith("private")


def test_the_campaign_list_is_the_live_campaigns_of_the_account(mem):
    out = client_as(STAFF).get(API + "/campaigns?account=213-386-1213").get_json()["campaigns"]
    assert [c["name"] for c in out] == ["P2_PMax_20261014", "P2_Search_20261014"]
    assert out[1]["type"] == "Search" and out[0]["type"] == "PMax" and out[0]["qc"] is None
    assert client_as(STAFF).get(API + "/campaigns?account=abc").status_code == 400


def test_the_campaign_list_asks_google_only_for_enabled_campaigns_and_says_which_have_a_qc(mem):
    c, lid, _ = start()
    out = c.get(API + "/campaigns?account=" + ACCOUNT).get_json()["campaigns"]
    qc = {x["name"]: x["qc"] for x in out}["P2_Search_20261014"]
    assert qc["id"] == lid and qc["verdict"]["code"] in ("not_ready", "incomplete") and qc["checked"] >= 1
    assert {x["name"]: x["qc"] for x in out}["P2_PMax_20261014"] is None
    assert any("campaign.status = 'ENABLED'" in q for q in gads.search.calls)


def test_the_campaign_list_says_when_google_ads_cannot_be_read(monkeypatch, mem):
    def refused(cid, gaql):
        raise gads.GadsError("quota", "Google Ads says the daily limit is used up.")
    monkeypatch.setattr(gads, "search", refused)
    r = client_as(STAFF).get(API + "/campaigns?account=" + ACCOUNT)
    assert r.status_code == 429 and r.get_json()["kind"] == "quota"


def test_picking_a_campaign_starts_the_ai_review_which_flags_what_is_wrong(mem):
    c, lid, body = start()
    assert body["launch"]["review"]["status"] == "done" and body["launch"]["readAt"] and body["launch"]["book"] is None
    checks = body["checks"]
    assert checks["1.5"]["status"] == "pass" and checks["1.5"]["source"] == "rule"                   # exact rule, from Google Ads
    flagged = checks["1.4"]
    assert flagged["status"] == "fail" and flagged["source"] == "ai" and flagged["by"] == "QA Agent"
    assert "exclamation" in flagged["found"] and "Remove the exclamation" in flagged["fix"] and flagged["evidence"] == "Free estimates today!!"
    assert checks["1.6"]["status"] == "pass" and checks["1.6"]["source"] == "ai"
    assert checks["1.1"]["status"] == "pass" and checks["1.3"]["status"] == "pass" and checks["1.15"]["status"] == "pass"
    assert body["summary"]["failed"] == ["1.4"] and body["summary"]["verdict"]["code"] == "not_ready"
    assert mem.launches[lid]["created_by"] == STAFF and mem.launches[lid]["auto_at"]


def test_what_the_ai_cannot_see_is_left_for_a_person_not_passed(mem):
    c, lid, body = start()
    row = body["checks"]["3.4"]                                                                   # a switch the API does not show
    assert row["status"] is None and row["unverified"] is True and "Nothing in the evidence" in row["found"]
    assert "3.4" in body["summary"]["needsHuman"] and "3.4" in body["summary"]["pendingBlocking"]
    assert body["checks"]["5.2"]["status"] is None and "5.2" in body["summary"]["needsHuman"]


def test_exact_rules_outrank_the_ai_and_are_not_put_to_it(mem):
    mem.model.answers = dict(DEFAULT_ANSWERS, **{"1.5": ("flag", "AI Max looks on.", "AI Max for Search")})
    c, lid, body = start()
    asked = {c["id"] for req in mem.model.asked for c in req["checks"]}
    assert "1.5" not in asked and "1.3" not in asked and "4.1" not in asked
    assert body["checks"]["1.5"]["status"] == "pass" and body["checks"]["1.5"]["source"] == "rule"
    assert {"1.4", "1.6", "3.4"} <= asked


def test_the_ai_is_shown_the_campaign_book_check_values_and_the_evidence(mem):
    start()
    first = next(r for r in mem.model.asked if r["section"].startswith("1."))
    row = next(c for c in first["checks"] if c["id"] == "1.4")
    assert row["expectedValue"] == "Minimum 1 ad should be present" and "spell" in row["otherThingsToCheck"].lower() and row["fromBook"] is False
    assert next(c for c in first["checks"] if c["id"] == "1.8")["fromBook"] is True
    ev = first["evidence"]
    assert ev["adGroups"][0]["ads"][0]["headlines"][-1] == "Free estimates today!!" and ev["campaign"]["name"] == "P2_Search_20261014"
    assert ev["campaignBook"] == "none uploaded"
    assert ev["targeting"]["devices"] == [{"device": "TABLET", "bidModifier": 0, "note": "0 means excluded"}]


def test_if_the_ai_is_unavailable_the_exact_rules_still_run_and_everything_else_waits_for_a_person(monkeypatch, mem):
    from tracker import paid_qa_ai
    from tracker.market_radar_llm import ModelError
    monkeypatch.setattr(paid_qa_ai, "default_call", FakeModel(boom=ModelError("not_configured", "ANTHROPIC_API_KEY is not set")))
    c, lid, body = start()
    assert body["checks"]["1.5"]["status"] == "pass" and body["checks"]["1.4"]["status"] is None
    assert "no Anthropic key" in body["launch"]["review"]["note"] and body["launch"]["review"]["status"] == "done"
    assert body["summary"]["verdict"]["code"] == "incomplete"                                     # never READY on rules alone


def test_if_google_ads_cannot_be_read_the_review_says_so_and_the_qc_still_opens(monkeypatch, mem):
    monkeypatch.setattr(gads, "search", lambda cid, gaql: (_ for _ in ()).throw(gads.GadsError("quota", "Google Ads says the daily limit is used up.")))
    c = client_as(STAFF)
    r = c.post(API + "/start", json=NEW, headers=SAME)
    body = r.get_json()
    assert r.status_code == 201 and body["launch"]["review"]["status"] == "error" and "daily limit" in body["launch"]["review"]["error"]
    assert body["checks"] == {} and body["summary"]["total"] == 61
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
    assert c.post(API + "/start", json=NEW, headers=SAME).status_code == 200                      # resumed, not created


def test_a_review_in_progress_reports_its_progress_and_is_not_started_twice(mem):
    c, lid, _ = start()
    mem.launches[lid].update(review_status="running", review_started="2999-01-01T00:00:00+00:00",
                             review_progress={"phase": "The AI is reviewing the checklist", "done": 3, "total": 4})
    body = c.get(API + "/launches/" + lid).get_json()
    assert body["launch"]["review"] == {"status": "running", "phase": "The AI is reviewing the checklist", "done": 3, "total": 4, "error": "", "note": ""}
    assert c.post("%s/launches/%s/run" % (API, lid), json={}, headers=SAME).status_code in (409, 429)


def test_a_review_that_died_with_its_server_is_shown_as_stopped(mem):
    c, lid, _ = start()
    mem.launches[lid].update(review_status="running", review_started="2026-10-14T09:00:00+00:00")
    r = c.get(API + "/launches/" + lid).get_json()["launch"]["review"]
    assert r["status"] == "error" and "stopped before it finished" in r["error"]


def test_a_person_confirming_or_overruling_beats_the_ai(mem):
    c, lid, _ = start()
    url = "%s/launches/%s/checks/1.4" % (API, lid)
    r = c.put(url, json={"status": "pass", "note": "That is the client's style, approved"}, headers=SAME)
    body = r.get_json()
    assert r.status_code == 200 and body["check"]["status"] == "pass" and body["check"]["source"] == "person" and body["check"]["by"] == STAFF
    assert "exclamation" in body["check"]["found"]                                               # the AI's finding stays visible
    r = c.put(url, json={"status": None}, headers=SAME)                                          # undo: back to the AI's flag
    assert r.get_json()["check"]["status"] == "fail" and r.get_json()["check"]["source"] == "ai"
    assert "1.4" not in mem.checks[lid]


def test_confirming_what_the_ai_could_not_verify_clears_it_from_the_needs_a_human_list(mem):
    c, lid, first = start()
    assert "3.4" in first["summary"]["needsHuman"]
    r = c.put("%s/launches/%s/checks/3.4" % (API, lid), json={"status": "pass", "note": "On, checked in the account settings"}, headers=SAME)
    assert "3.4" not in r.get_json()["summary"]["needsHuman"] and "3.4" not in r.get_json()["summary"]["pendingBlocking"]


def test_wrong_needs_its_reason_and_keeps_it(mem):
    c, lid, _ = start()
    url = "%s/launches/%s/checks/3.6" % (API, lid)
    r = c.put(url, json={"status": "fail"}, headers=SAME)
    assert r.status_code == 400 and "needs a reason" in r.get_json()["error"] and "3.6" not in mem.checks[lid]
    r = c.put(url, json={"status": "fail", "note": "Dynamic callouts are on in the account"}, headers=SAME)
    assert r.status_code == 200 and "3.6" in r.get_json()["summary"]["failedBlocking"]
    assert c.get(API + "/launches/" + lid).get_json()["checks"]["3.6"]["note"] == "Dynamic callouts are on in the account"


def test_a_check_that_does_not_show_or_does_not_exist_cannot_be_answered(mem):
    c, lid, _ = start(campaignId="9000000002", channel="PERFORMANCE_MAX", campaignName="P2_PMax_20261014")
    mem.launches[lid]["campaign_types"] = ["PMax"]
    for check in ("1.5", "99.9"):                                 # AI Max is Search only; not a check
        assert c.put("%s/launches/%s/checks/%s" % (API, lid, check), json={"status": "pass"}, headers=SAME).status_code == 400
    assert mem.checks[lid] == {}


def test_client_types_are_not_part_of_this_checklist(mem):
    c, lid, _ = start()
    assert c.put("%s/launches/%s" % (API, lid), json={"clientTypes": ["Lead Gen"]}, headers=SAME).status_code == 400
    assert c.put("%s/launches/%s" % (API, lid), json={"clientTypes": []}, headers=SAME).status_code == 200


def test_rerunning_reviews_again_and_a_persons_answers_stay(mem, monkeypatch):
    c, lid, _ = start()
    c.put("%s/launches/%s/checks/1.4" % (API, lid), json={"status": "na", "note": "no ads yet"}, headers=SAME)
    mem.model.answers = {"1.4": ("pass", "The headlines now read fine.", "Headline 1")}
    monkeypatch.setattr(appmod, "_QC_LAST_RUN", {})
    r = c.post("%s/launches/%s/run" % (API, lid), json={}, headers=SAME)
    body = r.get_json()
    assert r.status_code == 200 and body["checks"]["1.4"]["status"] == "na" and body["checks"]["1.4"]["source"] == "person"
    assert body["checks"]["1.4"]["found"].startswith("The headlines now read fine")             # the new finding is printed
    assert "1.4" not in body["summary"]["failed"] or body["checks"]["1.4"]["status"] == "na"


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


# ── The campaign book ───────────────────────────────────────────────────────

def test_adding_a_campaign_book_saves_it_and_reviews_the_campaign_against_it(mem):
    c, lid, first = start()
    assert first["checks"]["1.8"]["status"] is None and "campaign book names no bid strategy" in first["checks"]["1.8"]["facts"]
    reviews = len(mem.model.asked)
    r = upload(c, lid)
    body = r.get_json()
    assert r.status_code == 200 and body["launch"]["book"]["filename"] == "Campaign Book.xlsx" and body["launch"]["book"]["fields"] == 3
    assert body["launch"]["book"]["uploadedBy"] == STAFF and body["launch"]["book"]["uploadedAt"] and "reviewError" not in body
    assert body["checks"]["1.8"]["status"] == "pass" and "Maximize conversions" in body["checks"]["1.8"]["found"]      # the rule used the book
    assert len(mem.model.asked) > reviews                                                         # and the AI reviewed again
    ev = mem.model.asked[-1]["evidence"]
    assert ev["campaignBook"]["filename"] == "Campaign Book.xlsx" and {"field": "Genders", "value": "Women", "other": ""} in ev["campaignBook"]["fields"]
    assert mem.launches[lid]["book"]["rows"][0]["field"] == "Bid Strategy Type"


def test_a_book_that_disagrees_with_the_campaign_is_a_flag(mem):
    c, lid, _ = start()
    r = upload(c, lid, book_xlsx([["Field", "Value"], ["Bid Strategy Type", "Target CPA"]]))
    assert r.get_json()["checks"]["1.8"]["status"] == "fail" and "Target CPA" in r.get_json()["checks"]["1.8"]["found"]


def test_a_template_with_placeholders_is_not_an_answer(mem):
    c, lid, _ = start()
    r = upload(c, lid, book_xlsx([["Field", "Value"], ["Bid Strategy Type", "Depends on the campaign book"]]))
    assert r.get_json()["checks"]["1.8"]["status"] is None


def test_a_google_sheet_link_is_fetched_and_read(mem, monkeypatch):
    from tracker import event_intel_http
    seen = []

    class Raw:
        def __init__(self, b):
            self.b = b

        def read(self, n=None, decode_content=True):
            return self.b[:n] if n else self.b

    class Resp:
        status_code = 200

        def __init__(self, b):
            self.raw = Raw(b)

        def close(self):
            pass

    monkeypatch.setattr(event_intel_http, "public_get", lambda url, timeout=20, headers=None: (seen.append(url), Resp(book_xlsx()))[1])
    c, lid, _ = start()
    link = "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit?gid=0"
    r = c.post("%s/launches/%s/book" % (API, lid), json={"sheetUrl": link}, headers=SAME)
    assert r.status_code == 200 and r.get_json()["launch"]["book"]["source"] == "google_sheet" and r.get_json()["launch"]["book"]["fields"] == 3
    assert seen == ["https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/export?format=xlsx"]


def test_a_private_sheet_a_wrong_link_and_a_bad_file_are_refused_in_words_and_change_nothing(mem, monkeypatch):
    from tracker import event_intel_http

    class Resp:
        status_code = 403

        class raw:
            @staticmethod
            def read(n=None, decode_content=True):
                return b"<html>sign in</html>"

        @staticmethod
        def close():
            pass
    monkeypatch.setattr(event_intel_http, "public_get", lambda url, timeout=20, headers=None: Resp())
    c, lid, _ = start()
    link = "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz0123456789/edit"
    for body, says in (({"sheetUrl": link}, "private"), ({"sheetUrl": "https://example.com/x"}, "not a Google Sheets link"),
                       ({"filename": "b.xlsx", "data": base64.b64encode(b"not a workbook").decode()}, "not an .xlsx"),
                       ({"filename": "b.xlsx", "data": ""}, "empty"), ({"filename": "old.xls", "data": base64.b64encode(b"x").decode()}, "old .xls")):
        r = c.post("%s/launches/%s/book" % (API, lid), json=body, headers=SAME)
        assert r.status_code == 400 and says in r.get_json()["error"], body
    assert mem.launches[lid]["book"] == {}
    assert c.post("%s/launches/%s/book" % (API, lid), json=["x"], headers=SAME).status_code == 400


def test_removing_the_book_takes_it_away(mem):
    c, lid, _ = start()
    upload(c, lid)
    r = c.delete("%s/launches/%s/book" % (API, lid), json={}, headers=SAME)
    assert r.status_code == 200 and r.get_json()["launch"]["book"] is None and mem.launches[lid]["book"] == {}


def test_the_book_is_saved_even_when_a_review_cannot_start_and_it_says_why(mem, monkeypatch):
    c, lid, _ = start()
    mem.launches[lid]["review_status"] = "running"
    mem.launches[lid]["review_started"] = "2999-01-01T00:00:00+00:00"
    r = upload(c, lid)
    assert r.status_code == 200 and "already running" in r.get_json()["reviewError"] and mem.launches[lid]["book"]["rows"]
    mem.launches[lid]["review_status"] = "done"
    monkeypatch.setattr(appmod, "_QC_AI_DAILY_CAP", 0)
    r = upload(c, lid)
    assert r.status_code == 200 and "AI reviews in a day" in r.get_json()["reviewError"]


def test_the_book_routes_are_checked_like_the_others(mem):
    c, lid, _ = start()
    url = "%s/launches/%s/book" % (API, lid)
    assert c.post(url, json={"sheetUrl": "x"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.delete(url, json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client_as(None).post(url, json={}, headers=SAME).status_code == 302
    assert c.post("%s/launches/%s/book" % (API, "a" * 32), json={}, headers=SAME).status_code == 404
    assert c.post("%s/launches/%s/book" % (API, "../x"), json={}, headers=SAME).status_code == 404
    assert mem.launches[lid]["book"] == {}


# ── Housekeeping routes ─────────────────────────────────────────────────────

def test_only_whoever_started_a_qc_or_an_admin_can_remove_it(mem):
    c, lid, _ = start(client_as(STAFF))
    assert client_as(OTHER).delete("%s/launches/%s" % (API, lid), json={}, headers=SAME).status_code == 403
    assert not mem.launches[lid]["archived"]
    assert client_as(STAFF).delete("%s/launches/%s" % (API, lid), json={}, headers=SAME).status_code == 200
    assert client_as(STAFF).get("%s/launches/%s" % (API, lid)).status_code == 404                # gone from view
    c2, lid2, _ = start(client_as(STAFF))
    assert lid2 != lid                                                                           # the campaign can start a fresh QC
    assert client_as(ADMIN).delete("%s/launches/%s" % (API, lid2), json={}, headers=SAME).status_code == 200


def test_ids_are_checked_before_anything_is_looked_up(mem):
    c = client_as(STAFF)
    for bad in ("x", "../etc", "A" * 32, "a" * 31):
        assert c.get("%s/launches/%s" % (API, bad)).status_code == 404
    assert c.get("%s/launches/%s" % (API, "a" * 32)).status_code == 404                          # well formed, not there
    assert c.put("%s/launches/%s/checks/1.1" % (API, "a" * 32), json={"status": "pass"}, headers=SAME).status_code == 404
    assert c.post("%s/launches/%s/run" % (API, "a" * 32), json={}, headers=SAME).status_code == 404


def test_cross_site_and_non_json_writes_are_refused(mem):
    c, lid, _ = start()
    url = "%s/launches/%s/checks/1.1" % (API, lid)
    assert c.put(url, json={"status": "pass"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.put(url, data="status=pass", headers=SAME).status_code == 415
    assert c.post(API + "/start", json=NEW, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("%s/launches/%s/run" % (API, lid), json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert "1.1" not in mem.checks[lid]


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
    r = c.put("%s/launches/%s/checks/1.1" % (API, lid), json={"status": "pass", "verdict": "ready", "summary": {"verdict": {"code": "ready"}}},
              headers=SAME)
    assert r.get_json()["summary"]["verdict"]["code"] != "ready"


# ── The campaign book is for Search campaigns ───────────────────────────────

import tracker.paid_qa_book as pqb  # noqa: E402


@pytest.mark.parametrize("channel,used", [("SEARCH", True), ("", True), ("PERFORMANCE_MAX", False), ("DEMAND_GEN", False), ("SHOPPING", False),
                                          ("DISPLAY", False), ("VIDEO", False), ("MULTI_CHANNEL", False)])
def test_the_book_is_for_search_campaigns_only(channel, used):
    assert pqb.applies_to(channel) is used


def test_every_check_that_would_have_used_the_book_says_how_to_judge_it_without_one():
    spot = [i for i in qa.items() if i["fromBook"]]
    assert len(spot) == 8 and all(len(i.get("onTheSpot") or "") > 60 for i in spot)
    assert all("There is no plan to compare with" in i["onTheSpot"] or "no plan" in i["onTheSpot"] for i in spot)
    assert not any(i.get("onTheSpot") for i in qa.items() if not i["fromBook"])
    assert all("\u2014" not in i.get("onTheSpot", "") for i in spot)


def test_the_note_for_a_campaign_the_book_does_not_cover_says_what_happens_instead():
    note = pqb.not_used_note("PERFORMANCE_MAX")
    assert "Performance Max" in note and "judged on the spot" in note and "audience and search theme" in note and "\u2014" not in note
    assert "Demand Gen" in pqb.not_used_note("DEMAND_GEN")


def test_a_book_cannot_be_added_to_a_performance_max_qc_and_the_page_is_told_why(mem):
    c, lid, _ = start(campaignId="9000000002", channel="PERFORMANCE_MAX", campaignName="P2_PMax_20261014")
    mem.launches[lid]["channel"] = "PERFORMANCE_MAX"                       # the review's fake Google answers with a Search campaign
    body = c.get(API + "/launches/" + lid).get_json()
    assert body["launch"]["bookApplies"] is False and body["launch"]["book"] is None and "Performance Max" in body["launch"]["bookNote"]
    r = upload(c, lid)
    assert r.status_code == 400 and "for Search campaigns" in r.get_json()["error"]
    assert not mem.launches[lid].get("book")


def test_a_search_qc_still_takes_a_book_and_says_it_applies(mem):
    c, lid, body = start()
    assert body["launch"]["bookApplies"] is True and body["launch"]["bookNote"] == "" and body["launch"]["bookIgnored"] is False
    assert upload(c, lid).status_code == 200


def test_a_book_saved_before_is_kept_but_ignored_for_a_performance_max_qc(mem):
    c, lid, _ = start(campaignId="9000000002", channel="PERFORMANCE_MAX", campaignName="P2_PMax_20261014")
    mem.launches[lid]["channel"] = "PERFORMANCE_MAX"
    mem.launches[lid]["book"] = {"filename": "old.xlsx", "rows": [{"field": "Bid Strategy Type", "value": "Max clicks", "other": ""}], "text": ""}
    p = c.get(API + "/launches/" + lid).get_json()["launch"]
    assert p["book"] is None and p["bookIgnored"] is True and p["bookApplies"] is False
    assert c.delete("%s/launches/%s/book" % (API, lid), json={}, headers=SAME).status_code == 200      # it can still be removed
