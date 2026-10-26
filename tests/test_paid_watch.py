"""Account Watch: reading Google and Meta, the report and its cache (tracker/paid_watch.py). No network."""
import datetime as dt
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from monitor_fakes import MemStore  # noqa: E402
from tracker import paid_gads as base  # noqa: E402
from tracker import paid_gads_segments as gads  # noqa: E402
from tracker import paid_store  # noqa: E402
from tracker import paid_watch as w  # noqa: E402

AT = dt.datetime(2026, 10, 26, 7, 30, tzinfo=dt.timezone.utc)
TODAY = AT.date()
ACCT = {"id": "1234567890", "name": "Acme Dental", "currency": "USD"}
ACCT2 = {"id": "2222222222", "name": "Beta Clinic", "currency": "USD"}


def client_row(cid, name, status="ENABLED", cur="USD"):
    return {"customerClient": {"id": cid, "descriptiveName": name, "status": status, "currencyCode": cur}}


def order_row(status="APPROVED", approved="1000000000", adjusted=None, served="100000000", ends="2026-12-31 23:59:59"):
    b = {"status": status, "approvedSpendingLimitMicros": approved, "amountServedMicros": served, "approvedEndDateTime": ends}
    if adjusted:
        b["adjustedSpendingLimitMicros"] = adjusted
    return {"accountBudget": b}


def change_row(at="2026-10-25 14:03:11.5", user="ana@position2.com", client="GOOGLE_ADS_WEB_CLIENT", kind="CAMPAIGN_BUDGET", op="UPDATE", fields="amount_micros", old="50000000", new="120000000"):
    return {"changeEvent": {"changeDateTime": at, "userEmail": user, "clientType": client, "changeResourceType": kind, "resourceChangeOperation": op, "changedFields": fields,
                            "oldResource": {"campaignBudget": {"amountMicros": old}}, "newResource": {"campaignBudget": {"amountMicros": new}}}}


def action_row(aid="11", name="Form", status="ENABLED", primary=True, include=True):
    return {"conversionAction": {"id": aid, "name": name, "status": status, "primaryForGoal": primary, "includeInConversionsMetric": include}}


def user_row(email="ana@position2.com", role="ADMIN"):
    return {"customerUserAccess": {"emailAddress": email, "accessRole": role}}


def searcher(clients=None, orders=None, changes=None, actions=None, users=None, log=None, fail=(), fail_kinds=()):
    clients = clients if clients is not None else [client_row("1234567890", "Acme Dental"), client_row("2222222222", "Beta Clinic")]
    markers = (("clients", "FROM customer_client"), ("orders", "FROM account_budget"), ("changes", "FROM change_event"), ("actions", "FROM conversion_action"), ("users", "FROM customer_user_access"))
    data = {"clients": clients, "orders": orders or [], "changes": changes or [], "actions": actions if actions is not None else [action_row()], "users": users if users is not None else [user_row()]}

    def run(cid, q):
        if log is not None:
            log.append((cid, q))
        if cid in fail:
            raise gads.GadsError("api", "Account %s cannot be read." % cid)
        for kind, marker in markers:
            if marker in q:
                if kind in fail_kinds:
                    raise gads.GadsError("api", "no %s read" % kind)
                return data[kind]
        raise AssertionError("unexpected query: " + q[:80])
    return run


# ── Parsing Google's rows ───────────────────────────────────────────────────

def test_every_client_of_the_manager_account_is_listed_with_its_status_whatever_it_is():
    got = w.read_clients([client_row("1", "A"), client_row("2", "B", "SUSPENDED", "EUR"), {"customerClient": {"descriptiveName": "no id"}}, {"customerClient": {"id": "3"}}])
    assert sorted(got) == ["1", "2", "3"] and got["2"] == {"id": "2", "name": "B", "status": "SUSPENDED", "currency": "EUR"} and got["3"]["name"] == "Account 3" and got["3"]["status"] == "UNKNOWN"


def test_a_budget_order_is_read_with_the_adjusted_limit_first_and_none_when_it_has_no_limit():
    got = w.read_orders([order_row(adjusted="800000000"), order_row(approved="500000000", served="50000000"), order_row(approved="", served="1000000", status="PENDING"), {"accountBudget": {}}])
    assert got[0] == {"status": "APPROVED", "limit": 800.0, "served": 100.0, "endsAt": "2026-12-31 23:59:59"} and got[1]["limit"] == 500.0 and got[1]["served"] == 50.0
    assert got[2]["limit"] is None and got[2]["status"] == "PENDING" and got[3] == {"status": "", "limit": None, "served": 0.0, "endsAt": ""}


def test_a_change_event_is_a_budget_a_bidding_or_other_change_with_who_made_it():
    rows = [change_row(), change_row(at="2026-10-25 12:00:00", op="CREATE"), change_row(at="2026-10-25 11:00:00", op="REMOVE"), change_row(at="2026-10-25 10:00:00", fields="name"),
            change_row(at="2026-10-25 09:00:00", kind="CAMPAIGN", fields="target_cpa"), change_row(at="2026-10-25 08:00:00", kind="CAMPAIGN", fields="status"),
            change_row(at="2026-10-25 07:00:00", kind="AD_GROUP", fields="name", user="", client="GOOGLE_ADS_SCRIPTS"), {"changeEvent": {"userEmail": "x@y.z"}}]
    got = w.read_changes(rows)
    assert len(got) == 7
    b = got[0]
    assert (b["kind"], b["was"], b["now"], b["user"], b["at"], b["op"]) == ("budget", 50.0, 120.0, "ana@position2.com", "2026-10-25 14:03:11", "UPDATE") and b["label"] == "a budget amount changed"
    assert got[1]["label"] == "a budget created" and got[1]["kind"] == "budget" and got[2]["label"] == "a budget removed"
    assert got[3]["kind"] == "other" and got[3]["label"] == "a budget setting changed"
    assert got[4]["kind"] == "bidding" and got[4]["label"] == "a campaign's bidding changed" and got[5]["kind"] == "other" and got[5]["label"] == "a campaign switched on or off"
    assert got[6]["user"] == "a Google Ads script" and got[6]["label"] == "a ad group change"


def test_the_amounts_are_written_into_the_label_once_the_currency_is_known():
    got = w.with_amounts(w.read_changes([change_row(), change_row(at="2026-10-25 10:00:00", old="120000000", new="50000000"), change_row(at="2026-10-25 09:00:00", fields="name")]), "EUR")
    assert got[0]["label"] == "a budget raised from €50.00 to €120" and got[1]["label"] == "a budget lowered from €120 to €50.00" and got[2]["label"] == "a budget setting changed"


def test_conversion_actions_and_users_are_read_and_a_row_without_an_id_or_address_is_dropped():
    assert w.read_actions([action_row("1", "A"), action_row("2", "B", "HIDDEN", False, False), {"conversionAction": {"name": "x"}}]) == [
        {"id": "1", "name": "A", "status": "ENABLED", "primary": True, "include": True}, {"id": "2", "name": "B", "status": "HIDDEN", "primary": False, "include": False}]
    assert w.read_users([user_row("Ana@Position2.com", "ADMIN"), user_row("", "ADMIN"), user_row("b@client.com", "READ_ONLY")]) == [{"email": "ana@position2.com", "role": "ADMIN"}, {"email": "b@client.com", "role": "READ_ONLY"}]


def test_one_account_is_read_with_everything_the_rules_need_and_the_change_window_is_four_days():
    log = []
    got = w.read_account(ACCT["id"], searcher(log=log, orders=[order_row()], changes=[change_row()]), AT, "USD")
    assert got["warnings"] == [] and len(got["orders"]) == 1 and got["changes"][0]["label"] == "a budget raised from $50.00 to $120" and got["actions"][0]["name"] == "Form" and got["users"][0]["role"] == "ADMIN"
    q = [q for _c, q in log if "change_event" in q][0]
    assert "change_date_time >= '2026-10-22 07:30:00'" in q and "change_date_time <= '2026-10-27 07:30:00'" in q


def test_every_read_is_soft_and_all_refused_is_raised():
    got = w.read_account(ACCT["id"], searcher(fail_kinds=("orders", "users")), AT)
    assert got["orders"] is None and got["users"] is None and got["changes"] == [] and got["actions"] is not None
    assert got["warnings"] == ["The account budget orders could not be read (no orders read), so that check was skipped.", "Who can open the account could not be read (no users read), so that check was skipped."]
    with pytest.raises(gads.GadsError):
        w.read_account(ACCT["id"], searcher(fail_kinds=("orders", "changes", "actions", "users")), AT)


def test_an_mcc_is_read_with_each_accounts_status_and_the_accounts_the_platforms_list_leaves_out():
    clients = [client_row("1234567890", "Acme Dental"), client_row("2222222222", "Beta Clinic", "SUSPENDED"), client_row("3333333333", "Gamma", "CANCELED"), client_row("4444444444", "Euro Co", "CLOSED", "EUR")]
    group = {"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "members": [dict(ACCT), dict(ACCT2)]}
    raw = w.google_raw(group, search_fn=searcher(clients=clients), now=AT)
    st = {a["id"]: a["status"] for a in raw["accounts"]}
    assert st == {"1234567890": "ENABLED", "2222222222": "SUSPENDED"} and [c["id"] for c in raw["inactive"]] == ["3333333333"]                       # the euro account is another group's
    assert raw["clients"]["4444444444"]["status"] == "CLOSED" and raw["members"] == ["Acme Dental", "Beta Clinic"] and raw["warnings"] == []
    one = w.google_raw(dict(ACCT), search_fn=searcher(clients=clients), now=AT)
    assert one["inactive"] == [] and one["accounts"][0]["status"] == "ENABLED"


def test_the_account_list_that_cannot_be_read_is_said_and_every_account_failing_is_raised():
    raw = w.google_raw(dict(ACCT), search_fn=searcher(fail_kinds=("clients",)), now=AT)
    assert raw["clients"] is None and raw["accounts"][0]["status"] is None and raw["warnings"][0].startswith("The manager account's list of accounts could not be read (no clients read)")
    group = {"id": "mcc-1-USD", "name": "G", "currency": "USD", "members": [dict(ACCT), dict(ACCT2)]}
    raw = w.google_raw(group, search_fn=searcher(fail=(ACCT2["id"],)), now=AT)
    assert [a["id"] for a in raw["accounts"]] == [ACCT["id"]] and "Beta Clinic could not be read: Account 2222222222 cannot be read." in raw["warnings"]
    with pytest.raises(gads.GadsError):
        w.google_raw(group, search_fn=searcher(fail=(ACCT["id"], ACCT2["id"])), now=AT)


# ── Meta ────────────────────────────────────────────────────────────────────

def test_meta_amounts_come_in_the_smallest_unit_and_are_written_in_the_main_one():
    got = w.parse_status({"account_status": 3, "disable_reason": "1", "spend_cap": "100000", "amount_spent": "92000", "currency": "USD"})
    assert got == {"code": 3, "disableReason": 1, "spendCap": 1000.0, "spent": 920.0, "currency": "USD"}
    yen = w.parse_status({"account_status": 1, "spend_cap": "100000", "amount_spent": "5000", "currency": "JPY"})
    assert yen["spendCap"] == 100000.0 and yen["spent"] == 5000.0 and w.parse_status({})["code"] is None and w.parse_status({"account_status": "x"})["code"] is None


def test_pages_and_the_activity_log_are_parsed_and_a_budget_event_carries_its_amounts_when_meta_gives_them():
    assert w.parse_pages([{"id": "1", "name": "A", "is_published": True}, {"id": "2"}, {"name": "no id"}]) == [{"id": "1", "name": "A", "published": True}, {"id": "2", "name": "2", "published": None}]
    rows = [{"event_type": "update_campaign_budget", "event_time": "2026-10-25T10:00:00+0000", "actor_name": "Ana", "object_name": "Leads", "extra_data": '{"old_value": {"daily_budget": 5000}, "new_value": {"daily_budget": 12000}}'},
            {"event_type": "ad_account_add_user_to_role", "event_time": "2026-10-25T12:00:00+0000", "actor_name": "jim@client.com", "extra_data": "not json"},
            {"event_type": "update_ad_set_budget", "event_time": "2026-10-25T09:00:00+0000", "extra_data": '{"old_value": 5000, "new_value": 8000}'}, {"event_type": "x"}, {"event_time": "2026-10-25T09:00:00+0000"}]
    got = w.parse_activities(rows, "USD")
    assert [r["type"] for r in got] == ["ad_account_add_user_to_role", "update_campaign_budget", "update_ad_set_budget"] and got[0]["what"] == "ad account add user to role" and got[0]["actor"] == "jim@client.com"
    assert (got[1]["was"], got[1]["now"], got[1]["object"]) == (50.0, 120.0, "Leads") and (got[2]["was"], got[2]["now"]) == (50.0, 80.0) and got[0]["was"] is None


def meta_world(status=None, pages=None, activities=None, fail=(), plain_only=False):
    status = status if status is not None else {"account_status": 1, "disable_reason": 0, "spend_cap": "0", "amount_spent": "9900", "currency": "USD"}
    pages = pages if pages is not None else [{"id": "10", "name": "Acme", "is_published": True}]
    calls = []

    def get(path, params):
        calls.append(path)
        if "status" in fail:
            raise RuntimeError("Meta says no")
        return status

    def pg(path, params):
        calls.append((path, params.get("fields")))
        if path.endswith("/promote_pages"):
            if "pages" in fail:
                raise RuntimeError("no pages")
            if plain_only and "is_published" in params["fields"]:
                raise RuntimeError("is_published refused")
            return [dict(p) if "is_published" in params["fields"] else {k: v for k, v in p.items() if k != "is_published"} for p in pages]
        if path.endswith("/activities"):
            if "activity" in fail:
                raise RuntimeError("no log")
            return activities or []
        raise AssertionError(path)
    return get, pg, calls


def test_a_meta_account_is_read_as_status_pages_and_activity():
    get, pg, _ = meta_world(activities=[{"event_type": "ad_account_update_spend_limit", "event_time": "2026-10-25T10:00:00+0000", "actor_name": "Ana"}])
    got = w.read_meta_account("act_1234567", pg, get, AT)
    assert got["status"]["code"] == 1 and got["pages"] == [{"id": "10", "name": "Acme", "published": True}] and got["activity"][0]["type"] == "ad_account_update_spend_limit" and got["warnings"] == []
    assert got["orders"] is None and got["changes"] is None and got["users"] is None and got["currency"] == "USD"


def test_the_pages_are_read_without_the_published_flag_when_meta_refuses_it_and_every_read_is_soft():
    get, pg, calls = meta_world(plain_only=True)
    got = w.read_meta_account("act_1234567", pg, get, AT)
    assert got["pages"] == [{"id": "10", "name": "Acme", "published": None}] and [c[1] for c in calls if isinstance(c, tuple) and c[0].endswith("/promote_pages")] == ["id,name,is_published", "id,name"]
    get, pg, _ = meta_world(fail=("status", "activity"))
    soft = w.read_meta_account("act_1234567", pg, get, AT)
    assert soft["status"] is None and soft["activity"] is None and soft["pages"] is not None and len(soft["warnings"]) == 2 and soft["warnings"][0].startswith("The ad account's status could not be read (Meta says no)")
    get, pg, _ = meta_world(fail=("status", "pages", "activity"))
    with pytest.raises(RuntimeError):
        w.read_meta_account("act_1234567", pg, get, AT)


def test_the_meta_requests_ask_for_the_fields_the_rules_use():
    assert w.meta_status_request("act_1") == ("/act_1", {"fields": "account_status,disable_reason,spend_cap,amount_spent,currency"})
    path, params = w.meta_pages_request("act_1")
    assert path == "/act_1/promote_pages" and params["fields"] == "id,name,is_published" and w.meta_pages_request("act_1", plain=True)[1]["fields"] == "id,name"
    path, params = w.meta_activity_request("act_1", "2026-10-22")
    assert path == "/act_1/activities" and "event_type" in params["fields"] and "actor_name" in params["fields"] and params["since"] == "2026-10-22"


# ── What the monitor saves, the manager account's list, and the report ──────

def test_what_the_monitor_saves_of_an_account_is_who_has_access_the_goals_the_pages_and_the_outcome():
    raw = {"users": [{"email": "a@b.co", "role": "ADMIN"}], "actions": [{"id": "1", "name": "F", "status": "ENABLED", "primary": True, "include": True}], "pages": [{"id": "9", "name": "P", "published": True}]}
    assert w.snapshot_of(raw) == {"users": {"a@b.co": "ADMIN"}, "actions": {"1": {"name": "F", "status": "ENABLED", "primary": True, "include": True}}, "pages": {"9": "P"}}
    res = {"status": "fix", "counts": {"high": 1, "medium": 2, "low": 3}, "findingCount": 6}
    assert w.snapshot_of({"users": None, "actions": None, "pages": None}, res) == {"status": "fix", "high": 1, "medium": 2, "low": 3, "findings": 6}


def test_an_account_that_was_listed_and_is_not_any_more_stays_gone_until_it_is_listed_again():
    day1 = {"day": "2026-10-25", "ids": {"1": "A", "2": "B"}, "gone": {}}
    now = {"1": {"name": "A"}}
    snap = w.mcc_snapshot(now, [day1], TODAY)
    assert snap == {"ids": {"1": "A"}, "gone": {"2": "B"}}
    day2 = dict(snap, day="2026-10-26")
    again = w.mcc_snapshot(now, [day1, day2], dt.date(2026, 10, 27))
    assert again["gone"] == {"2": "B"}                                                                      # still gone the day after
    back = w.mcc_snapshot({"1": {"name": "A"}, "2": {"name": "B"}}, [day1, day2], dt.date(2026, 10, 27))
    assert back["gone"] == {} and w.mcc_snapshot(now, [], TODAY) == {"ids": {"1": "A"}, "gone": {}}      # nothing earlier: nothing to compare
    assert w.mcc_snapshot(None, [day1], TODAY) == {"ids": {}, "gone": {}}                                    # a list that could not be read is not an account that vanished


@pytest.fixture
def cache(monkeypatch):
    saved = {}
    monkeypatch.setattr(paid_store, "get_gads_cache", lambda key, ttl: saved.get(key))
    monkeypatch.setattr(paid_store, "put_gads_cache", lambda key, value: saved.update({key: value}))
    from tracker import paid_geo
    paid_geo.clear_cache()
    base.reset_state()
    base.forget("")
    yield saved
    paid_geo.clear_cache()
    base.forget("")


def report(search, target="1234567890", **kw):
    group = {"id": "mcc-9923720096-USD", "name": "Group", "currency": "USD", "accountIds": [ACCT["id"], ACCT2["id"]]}
    return w.report_for(target, accounts_fn=lambda: [ACCT, ACCT2], groups_fn=lambda: [group], search_fn=search, today=TODAY, at=AT, **kw)


def test_the_report_names_the_account_and_what_it_found(cache):
    rep = report(searcher(clients=[client_row("1234567890", "Acme Dental", "SUSPENDED")], changes=[change_row(user="jim@client.com")]))
    a = rep["accounts"][0]
    assert rep["account"]["name"] == "Acme Dental" and rep["account"]["isGroup"] is False and a["id"] == "1234567890" and a["platform"] == "google" and a["status"] == "fix" and a["inactive"] is False
    assert [f["rule"] for f in a["findings"]] == ["account_status", "budget_change", "outside_editor"] and rep["summary"]["notActive"] == 1 and rep["readAt"] and len(rep["notMeasured"]) == 4
    assert rep["summary"]["counts"] == {"fix": 1, "check": 0, "clean": 0} and [r["rule"] for r in rep["rules"]] == w.math.ORDER
    assert rep["thresholds"] == {"changeDays": 3, "budgetMovePercent": 30, "orderPercent": 85, "capPercent": 90, "teamDomains": ["position2.com"]}


def test_a_second_ask_never_reads_google_again_and_refresh_does(cache):
    calls = []
    s = searcher(log=calls)
    report(s)
    n = len(calls)
    assert n == 5                                                                       # the manager's list, the orders, the change history, the actions and the users
    report(s)
    assert len(calls) == n
    report(s, force=True)
    assert len(calls) == 2 * n


def test_who_has_access_is_held_against_the_monitors_earlier_readings(cache):
    mon = MemStore()
    mon.snaps_put("watch", "2026-10-25", {"1234567890": {"users": {"ana@position2.com": "ADMIN"}}})
    rep = report(searcher(users=[user_row("ana@position2.com", "ADMIN"), user_row("x@evil.com", "ADMIN")]), mon=mon)
    f = rep["accounts"][0]["findings"][0]
    assert f["rule"] == "access_change" and f["level"] == "high" and f["evidence"] == ["x@evil.com was added as admin"]
    assert report(searcher(users=[user_row("x@evil.com", "ADMIN")]), mon=None)["accounts"][0]["findings"] == []


def test_an_mcc_report_lists_suspended_accounts_the_platform_leaves_out_and_accounts_that_vanished(cache):
    mon = MemStore()
    mon.snaps_put("watch", "2026-10-25", {"mcc": {"ids": {"1234567890": "Acme Dental", "2222222222": "Beta Clinic", "5555555555": "Vanished Co"}, "gone": {}}})
    clients = [client_row("1234567890", "Acme Dental"), client_row("2222222222", "Beta Clinic"), client_row("3333333333", "Gamma", "SUSPENDED")]
    rep = report(searcher(clients=clients), target="mcc-9923720096-USD", mon=mon)
    by = {a["name"]: a for a in rep["accounts"]}
    assert by["Gamma"]["inactive"] is True and by["Gamma"]["findings"][0]["title"] == "The account is suspended" and [c["key"] for c in by["Gamma"]["checks"]] == ["status"]
    assert by["Vanished Co"]["findings"][0]["rule"] == "account_gone" and by["Vanished Co"]["inactive"] is True
    assert rep["account"]["isGroup"] is True and [a["name"] for a in rep["accounts"]][:2] in (["Gamma", "Vanished Co"], ["Vanished Co", "Gamma"]) and rep["summary"]["notActive"] == 2 and rep["summary"]["accounts"] == 4


def test_a_read_that_lost_a_part_is_shown_but_not_saved_for_hours(cache):
    rep = report(searcher(fail_kinds=("users",)))
    assert rep["warnings"] == ["Who can open the account could not be read (no users read), so that check was skipped."] and cache == {}
    from tracker import paid_geo
    paid_geo.clear_cache()
    base.forget("")
    report(searcher())
    assert len(cache) == 1


def test_a_report_for_a_meta_account_reads_its_status_pages_and_activity(cache):
    get, pg, _ = meta_world(status={"account_status": 9, "disable_reason": 0, "spend_cap": "100000", "amount_spent": "99500", "currency": "USD"})
    rep = w.report_for("act_1234567", meta_pages_fn=pg, meta_get_fn=get, meta_account_fn=lambda act: {"name": "Meta Co", "currency": "USD"}, today=TODAY, at=AT, search_fn=searcher())
    a = rep["accounts"][0]
    assert rep["account"]["platform"] == "meta" and a["platform"] == "meta" and a["name"] == "Meta Co"
    assert {f["rule"]: f["level"] for f in a["findings"]} == {"account_status": "high", "spend_limit": "high"} and a["status"] == "fix"


def test_a_spent_quota_with_nothing_saved_is_raised_and_an_account_that_is_not_position2s_is_refused(cache):
    def spent(cid, q):
        raise gads.GadsError("quota", "The daily limit is used up.")
    with pytest.raises(gads.GadsError):
        report(spent)
    with pytest.raises(w.BadRequest, match="not one of Position2"):
        w.report_for("9999999999", accounts_fn=lambda: [ACCT], groups_fn=lambda: [], search_fn=searcher(), today=TODAY, at=AT)
