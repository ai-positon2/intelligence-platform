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


def test_the_agents_pass_and_fail_become_the_checks_state_with_its_finding():
    L = launch(campaign_types=["Search"])
    rows = qa.effective(L, {}, {"1.6": {"result": "pass", "found": "Auto-tagging is ON"},
                                "3.6": {"result": None, "found": "Daily budget 30.00 USD"},
                                "4.2": {"result": "fail", "found": "Search partners are ON"}}, AT)
    assert rows["1.6"] == {"status": "pass", "note": "", "by": "QA Agent", "at": AT, "source": "agent", "found": "Auto-tagging is ON"}
    assert rows["4.2"]["status"] == "fail" and rows["4.2"]["source"] == "agent"
    assert rows["3.6"]["status"] is None and rows["3.6"]["found"] == "Daily budget 30.00 USD"     # printed, left to the reviewer
    assert "1.1" not in rows                                                                      # nothing read, nothing answered


def test_a_persons_answer_always_beats_the_agent_and_clearing_it_brings_the_agent_back():
    L = launch(campaign_types=["Search"])
    auto = {"4.2": {"result": "fail", "found": "Search partners are ON"}}
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

LAUNCH_ROW = ("a" * 32, ACCOUNT, "A", CAMPAIGN, "C", "SEARCH", [], ["Search"], {}, None, "u", None, None, False)


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
    store.save_auto("a" * 32, {"1.6": {"result": "pass", "found": "ON"}}, campaign_name="C", channel="SEARCH", campaign_types=["Search"])
    store.archive_launch("a" * 32)
    store.checks_for(["a" * 32, "b" * 32])
    store.set_check("a" * 32, "1.1", "fail", "why", "u")
    store.clear_check("a" * 32, "1.1")
    assert len(log) >= 14
    for sql in log:
        n = iter(range(1, 100))
        pglast.parse_sql(re.sub(r"%s", lambda m: "$%d" % next(n), sql))


def test_the_schema_has_the_tables_a_cascade_and_one_live_qc_per_campaign():
    s = store.SCHEMA
    assert "pm_qc_launches" in s and "pm_qc_checks" in s and "ON DELETE CASCADE" in s
    assert "PRIMARY KEY (launch_id, check_id)" in s
    assert "UNIQUE INDEX" in s and "(account_id, campaign_id) WHERE NOT archived" in s


# ── Routes, on an in-memory store and a fake Google Ads ─────────────────────

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
                                  updated_at="2026-10-14T10:00:00+00:00", archived=False)
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

    def save_auto(self, lid, findings, *, campaign_name, channel, campaign_types):
        self.launches[lid].update(auto=findings, auto_at="2026-10-14T10:01:00+00:00", campaign_name=campaign_name,
                                  channel=channel, campaign_types=campaign_types)
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


def fake_google(**over):
    """A search(customer_id, gaql) answering from rows made up for one Search campaign, keyed by the
    resource the query reads. Nothing here is a real client's."""
    def row(**k):
        return k
    headlines = [{"text": "Headline %d" % i} for i in range(15)]
    data = {
        "FROM customer": [row(customer={"autoTaggingEnabled": True, "trackingUrlTemplate": "{lpurl}?utm_source=google&utm_medium=cpc",
                                        "currencyCode": "USD", "timeZone": "America/New_York",
                                        "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING"})],
        "FROM billing_setup": [row(billingSetup={"status": "APPROVED", "paymentsAccountInfo": {"paymentsAccountName": "Example Co"}})],
        "FROM recommendation_subscription": [],
        "FROM campaign WHERE campaign.id": [row(campaign={"id": CAMPAIGN, "name": "Example_Brand_Search", "status": "ENABLED",
                                                         "servingStatus": "SERVING", "advertisingChannelType": "SEARCH",
                                                         "networkSettings": {"targetGoogleSearch": True, "targetSearchNetwork": False,
                                                                             "targetContentNetwork": False},
                                                         "geoTargetTypeSetting": {"positiveGeoTargetType": "PRESENCE",
                                                                                  "negativeGeoTargetType": "PRESENCE"},
                                                         "startDateTime": "2026-03-11 04:38:47", "aiMaxSetting": {"enableAiMax": False},
                                                         "biddingStrategyType": "MAXIMIZE_CONVERSIONS"},
                                                 campaignBudget={"amountMicros": "30000000", "explicitlyShared": False})],
        "FROM campaign_criterion": [], "FROM campaign_label": [],
        "FROM campaign_shared_set": [row(sharedSet={"name": "Generic Negatives", "type": "NEGATIVE_KEYWORDS", "memberCount": "60"},
                                         campaignSharedSet={"status": "ENABLED"})], "FROM conversion_action": [],
        "FROM campaign_asset": [], "FROM customer_asset": [],
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


@pytest.fixture
def mem(monkeypatch):
    m = MemoryStore()
    for name in ("start_launch", "get_launch", "list_launches", "set_client_types", "save_auto", "archive_launch",
                 "checks_for", "set_check", "clear_check"):
        monkeypatch.setattr(store, name, getattr(m, name))
    monkeypatch.setattr(gads, "search", fake_google())
    monkeypatch.setattr(gads, "geo_names", lambda names, **k: {})
    monkeypatch.setattr(appmod, "_QC_LAST_RUN", {})
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
    c.put("%s/launches/%s/checks/1.1" % (API, lid), json={"status": "pass"}, headers=SAME)
    out = c.get(API + "/campaigns?account=" + ACCOUNT).get_json()["campaigns"]
    qc = out[0]["qc"]
    assert qc["id"] == lid and qc["verdict"]["code"] == "incomplete" and qc["checked"] >= 1 and out[1]["qc"] is None
    assert any("campaign.status = 'ENABLED'" in q for q in gads.search.calls)


def test_the_campaign_list_says_when_google_ads_cannot_be_read(monkeypatch, mem):
    def refused(cid, gaql):
        raise gads.GadsError("quota", "Google Ads says the daily limit is used up.")
    monkeypatch.setattr(gads, "search", refused)
    r = client_as(STAFF).get(API + "/campaigns?account=" + ACCOUNT)
    assert r.status_code == 429 and r.get_json()["kind"] == "quota"


def test_picking_a_campaign_starts_the_qc_and_the_agent_reads_it_at_once(mem):
    c, lid, body = start()
    assert body["launch"]["campaignName"] == "Example_Brand_Search" and body["launch"]["campaignTypes"] == ["Search"]
    assert body["launch"]["readAt"] and "readError" not in body
    checks = body["checks"]
    assert checks["1.6"]["status"] == "pass" and checks["1.6"]["source"] == "agent" and checks["1.6"]["by"] == "QA Agent"
    assert "ON" in checks["1.6"]["found"]
    assert checks["1.7"]["status"] == "pass" and "utm_source=google" in checks["1.7"]["found"]      # the URL parameters, printed
    assert checks["3.6"]["status"] is None and "30.00 USD" in checks["3.6"]["found"]                   # plan-dependent: printed only
    assert body["summary"]["checked"] > 0 and body["summary"]["verdict"]["code"] == "incomplete"
    assert mem.launches[lid]["created_by"] == STAFF and mem.launches[lid]["auto_at"]


def test_opening_the_same_campaign_again_resumes_the_one_qc_without_reading_again(mem):
    c, lid, _ = start()
    calls = len(gads.search.calls)
    c2, lid2, body = start(client_as(OTHER))
    assert lid2 == lid and len(mem.launches) == 1 and len(gads.search.calls) == calls
    r = c.post(API + "/start", json=NEW, headers=SAME)
    assert r.status_code == 200                                                                      # resumed, not created


def test_if_google_ads_cannot_be_read_the_qc_still_opens_for_manual_checks(monkeypatch, mem):
    def refused(cid, gaql):
        raise gads.GadsError("quota", "Google Ads says the daily limit is used up.")
    monkeypatch.setattr(gads, "search", refused)
    c = client_as(STAFF)
    r = c.post(API + "/start", json=NEW, headers=SAME)
    body = r.get_json()
    assert r.status_code == 201 and body["readError"]["kind"] == "quota" and body["launch"]["readAt"] is None
    assert body["checks"] == {} and body["summary"]["total"] > 0                                      # every check can be done by hand
    lid = body["launch"]["id"]
    assert c.put("%s/launches/%s/checks/1.1" % (API, lid), json={"status": "pass"}, headers=SAME).status_code == 200


def test_a_campaign_that_is_not_in_the_account_is_refused_and_leaves_no_qc(monkeypatch, mem):
    monkeypatch.setattr(gads, "search", fake_google(**{"FROM campaign WHERE campaign.id": []}))
    r = client_as(STAFF).post(API + "/start", json=NEW, headers=SAME)
    assert r.status_code == 404 and "not found in this account" in r.get_json()["error"]
    assert all(l["archived"] for l in mem.launches.values())


def test_a_bad_start_is_refused_in_words(mem):
    c = client_as(STAFF)
    for patch, says in (({"accountId": "12"}, "10 digits"), ({"campaignId": ""}, "Pick a campaign"), ({"campaignName": ""}, "campaign name")):
        r = c.post(API + "/start", json=dict(NEW, **patch), headers=SAME)
        assert r.status_code == 400 and says in r.get_json()["error"]
    assert not mem.launches


def test_a_person_answering_saves_who_and_beats_the_agent(mem):
    c, lid, _ = start()
    url = "%s/launches/%s/checks/1.6" % (API, lid)
    r = c.put(url, json={"status": "fail", "note": "Auto-tagging is off in the GA4 link"}, headers=SAME)
    body = r.get_json()
    assert r.status_code == 200 and body["check"]["status"] == "fail" and body["check"]["source"] == "person"
    assert body["check"]["by"] == STAFF and "ON" in body["check"]["found"]                            # the finding stays printed
    assert body["summary"]["verdict"]["code"] == "not_ready"                                          # 1.6 is Critical
    r = c.put(url, json={"status": None}, headers=SAME)                                               # undo: back to the agent
    assert r.get_json()["check"]["status"] == "pass" and r.get_json()["check"]["source"] == "agent"
    assert "1.6" not in mem.checks[lid]


def test_wrong_needs_its_reason_and_keeps_it(mem):
    c, lid, _ = start()
    url = "%s/launches/%s/checks/1.2" % (API, lid)
    r = c.put(url, json={"status": "fail"}, headers=SAME)
    assert r.status_code == 400 and "needs a reason" in r.get_json()["error"] and "1.2" not in mem.checks[lid]
    r = c.put(url, json={"status": "fail", "note": "Prepay balance is $0: add funds"}, headers=SAME)
    assert r.status_code == 200 and r.get_json()["summary"]["failedCritical"] == ["1.2"]
    assert c.get(API + "/launches/" + lid).get_json()["checks"]["1.2"]["note"] == "Prepay balance is $0: add funds"


def test_a_comment_can_sit_beside_a_right_answer(mem):
    c, lid, _ = start()
    r = c.put("%s/launches/%s/checks/3.2" % (API, lid), json={"status": "pass", "note": "Matches the convention"}, headers=SAME)
    assert r.get_json()["check"]["status"] == "pass" and r.get_json()["check"]["note"] == "Matches the convention"


def test_a_check_that_does_not_show_or_does_not_exist_cannot_be_answered(mem):
    c, lid, _ = start()
    for check in ("8.1", "8.5", "99.9"):                       # PMax only, Ecommerce only (none chosen), not a check
        assert c.put("%s/launches/%s/checks/%s" % (API, lid, check), json={"status": "pass"}, headers=SAME).status_code == 400
    assert mem.checks[lid] == {}


def test_choosing_a_client_type_adds_the_checks_that_exist_only_for_it(mem):
    c, lid, first = start()
    assert "8.5" not in first["summary"]["applicable"] and first["needsClientType"]["count"] > 0
    r = c.put("%s/launches/%s" % (API, lid), json={"clientTypes": ["Lead Gen"]}, headers=SAME)
    body = r.get_json()
    assert r.status_code == 200 and body["summary"]["total"] > first["summary"]["total"]
    assert "7.9" in body["summary"]["applicable"] and body["launch"]["clientTypes"] == ["Lead Gen"]
    assert c.put("%s/launches/%s" % (API, lid), json={"clientTypes": ["Bogus"]}, headers=SAME).status_code == 400


def test_rerunning_reads_the_campaign_again_and_a_persons_answers_stay(mem, monkeypatch):
    c, lid, _ = start()
    c.put("%s/launches/%s/checks/1.6" % (API, lid), json={"status": "na", "note": "client has none"}, headers=SAME)
    changed = {"FROM customer": [{"customer": {"autoTaggingEnabled": False, "currencyCode": "USD", "timeZone": "America/New_York"}}]}
    monkeypatch.setattr(gads, "search", fake_google(**changed))
    r = c.post("%s/launches/%s/run" % (API, lid), json={}, headers=SAME)
    body = r.get_json()
    assert r.status_code == 200 and body["checks"]["1.6"]["status"] == "na" and body["checks"]["1.6"]["source"] == "person"
    assert body["checks"]["1.6"]["found"].endswith("OFF on the account.")                              # the new reading is printed


def test_rerunning_too_soon_is_asked_to_wait(mem):
    c, lid, _ = start()
    first = c.post("%s/launches/%s/run" % (API, lid), json={}, headers=SAME)
    again = c.post("%s/launches/%s/run" % (API, lid), json={}, headers=SAME)
    assert first.status_code == 200 and again.status_code == 429 and again.get_json()["kind"] == "cooldown"


def test_rerunning_says_when_google_ads_is_down(mem, monkeypatch):
    c, lid, _ = start()
    monkeypatch.setattr(gads, "search", lambda cid, gaql: (_ for _ in ()).throw(gads.GadsError("auth", "Google would not sign in.")))
    r = c.post("%s/launches/%s/run" % (API, lid), json={}, headers=SAME)
    assert r.status_code == 502 and r.get_json()["kind"] == "auth"


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


def test_a_store_outage_is_a_503_with_a_reason(monkeypatch):
    def down(*a, **k):
        raise store.StoreUnavailable("DATABASE_URL is not set")
    monkeypatch.setattr(gads, "search", fake_google())
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
    assert r.get_json()["summary"]["verdict"]["code"] == "incomplete"
