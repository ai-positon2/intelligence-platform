"""Account Watch: what changed in an account that nobody may have meant (tracker/paid_watch_math.py). No network, no clock."""
import datetime as dt
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_watch_math as m  # noqa: E402

TODAY = dt.date(2026, 10, 26)
NOW = dt.datetime(2026, 10, 26, 7, 30, tzinfo=dt.timezone.utc)


def g(**kw):
    base = {"currency": "USD", "status": "ENABLED", "orders": [], "changes": [], "actions": [], "users": []}
    base.update(kw)
    return base


def meta(**kw):
    base = {"currency": "USD", "status": {"code": 1, "disableReason": 0, "spendCap": 0, "spent": 100}, "pages": [], "activity": []}
    base.update(kw)
    return base


def run(a, platform="google", history=None):
    return m.analyse(a, history=history, today=TODAY, now=NOW, platform=platform)


def rules(r):
    return [f["rule"] for f in r["findings"]]


def check(r, key):
    return next(c for c in r["checks"] if c["key"] == key)


def ago(hours):
    return (NOW - dt.timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def chg(hours, user="ana@position2.com", kind="budget", was=None, now=None, op="UPDATE", label="a budget changed"):
    return {"at": ago(hours), "user": user, "client": "GOOGLE_ADS_WEB_CLIENT", "kind": kind, "op": op, "label": label, "was": was, "now": now}


def snap(days_back, **fields):
    return dict({"day": (TODAY - dt.timedelta(days=days_back)).isoformat()}, **fields)


# ── Status and payment ──────────────────────────────────────────────────────

def test_a_suspended_canceled_or_closed_google_account_is_high_and_an_enabled_one_is_fine():
    for status, word in (("SUSPENDED", "suspended"), ("CANCELED", "canceled"), ("CLOSED", "closed")):
        r = run(g(status=status))
        [f] = r["findings"]
        assert f["rule"] == "account_status" and f["level"] == "high" and f["title"] == "The account is %s" % word and r["status"] == "fix" and check(r, "status")["state"] == "problem"
    ok = run(g())
    assert ok["findings"] == [] and ok["status"] == "clean" and check(ok, "status")["note"] == "Enabled."
    assert check(run(g(status=None)), "status")["state"] == "unknown"


def test_a_meta_account_that_is_unsettled_or_disabled_is_high_and_one_settling_is_medium():
    for code, word, level in ((2, "disabled", "high"), (3, "unsettled (a payment failed)", "high"), (9, "in a grace period for a payment", "high"), (7, "under risk review", "high"), (101, "closed", "high"), (8, "settling a payment", "medium")):
        r = run(meta(status={"code": code, "disableReason": 0, "spendCap": 0, "spent": 0}), platform="meta")
        assert r["findings"][0]["rule"] == "account_status" and r["findings"][0]["level"] == level and r["findings"][0]["title"] == "The ad account is %s" % word, code
    r = run(meta(status={"code": 2, "disableReason": 1, "spendCap": 0, "spent": 0}), platform="meta")
    assert "reason code 1" in r["findings"][0]["detail"] and "Disable reason code: 1" in r["findings"][0]["evidence"]
    assert run(meta(), platform="meta")["findings"] == [] and check(run(meta(status={}), platform="meta"), "status")["state"] == "unknown"
    assert run(meta(status={"code": 55, "spendCap": 0, "spent": 0}), platform="meta")["findings"][0]["level"] == "medium"          # a status this does not know is not called fine


def test_a_meta_spend_cap_nearly_reached_is_medium_and_almost_gone_is_high():
    mid = run(meta(status={"code": 1, "spendCap": 1000, "spent": 920}), platform="meta")
    assert mid["findings"][0]["rule"] == "spend_limit" and mid["findings"][0]["level"] == "medium" and mid["findings"][0]["title"] == "92 percent of the spend cap is used"
    assert run(meta(status={"code": 1, "spendCap": 1000, "spent": 990}), platform="meta")["findings"][0]["level"] == "high"
    assert run(meta(status={"code": 1, "spendCap": 1000, "spent": 800}), platform="meta")["findings"] == [] and run(meta(status={"code": 1, "spendCap": 0, "spent": 5000}), platform="meta")["findings"] == []


def order(status="APPROVED", limit=1000.0, served=0.0, ends=""):
    return {"status": status, "limit": limit, "served": served, "endsAt": ends}


def test_an_account_budget_order_nearly_used_or_about_to_end_is_flagged_and_the_rest_is_fine():
    mid = run(g(orders=[order(served=880)]))
    assert mid["findings"][0]["rule"] == "spend_limit" and mid["findings"][0]["level"] == "medium" and mid["findings"][0]["title"] == "88 percent of the account budget order is used"
    assert run(g(orders=[order(served=960)]))["findings"][0]["level"] == "high"
    soon = run(g(orders=[order(served=100, ends="2026-10-28 23:59:59")]))
    assert soon["findings"][0]["title"] == "The account budget order ends in 2 days" and soon["findings"][0]["level"] == "medium"
    assert run(g(orders=[order(served=100, ends="2026-11-30")]))["findings"] == [] and run(g(orders=[order(status="CANCELLED", served=999)]))["findings"] == []
    assert "automatic payments or monthly invoicing" in check(run(g(orders=[])), "limit")["note"] and check(run(g(orders=None)), "limit")["state"] == "unknown"


# ── Changes (Google) ────────────────────────────────────────────────────────

def test_a_budget_that_moved_a_lot_is_flagged_and_a_small_move_is_not():
    r = run(g(changes=[chg(5, was=50.0, now=120.0, label="a budget raised from $50 to $120"), chg(30, was=100.0, now=105.0, label="a budget raised from $100 to $105")]))
    [f] = r["findings"]
    assert f["rule"] == "budget_change" and f["level"] == "high" and f["title"] == "Budget change: a budget raised from $50 to $120 by ana@position2.com" and f["evidence"] == ["2026-10-26 02:30 by ana@position2.com: a budget raised from $50 to $120"]
    assert run(g(changes=[chg(5, was=100.0, now=140.0)]))["findings"][0]["level"] == "medium"                                   # a 40 percent rise
    assert run(g(changes=[chg(5, was=100.0, now=25.0)]))["findings"][0]["level"] == "high"                                      # down to a quarter
    assert run(g(changes=[chg(5, was=100.0, now=110.0)]))["findings"] == []
    unknown = run(g(changes=[chg(5)]))
    assert unknown["findings"][0]["level"] == "medium" and run(g(changes=[chg(5, op="CREATE", label="a budget created")]))["findings"][0]["rule"] == "budget_change"
    assert run(g(changes=[chg(80, was=50.0, now=500.0)]))["findings"] == []                                                      # older than three days


def test_a_new_budget_change_is_a_new_finding_so_it_is_alerted_and_bidding_changes_are_listed():
    a = run(g(changes=[chg(30, was=50.0, now=120.0)]))["findings"][0]["key"]
    b = run(g(changes=[chg(30, was=50.0, now=120.0), chg(2, was=120.0, now=300.0)]))["findings"][0]["key"]
    assert a != b and a.startswith("budget_change:2026-10-25") and b.startswith("budget_change:2026-10-26")
    r = run(g(changes=[chg(3, kind="bidding", label="a campaign's bidding changed"), chg(9, kind="bidding", label="a campaign's bidding changed")]))
    assert rules(r) == ["bid_change"] and r["findings"][0]["title"] == "2 bidding changes in the last 3 days" and r["findings"][0]["level"] == "medium"


def test_someone_outside_position2_who_changed_the_account_is_named_with_what_they_did():
    r = run(g(changes=[chg(4, user="jim@client.com", kind="other", label="a change"), chg(6, user="jim@client.com", kind="other"), chg(8, user="ana@position2.com", kind="other"), chg(9, user="", kind="other"),
                       chg(10, user="Ana@Position2.com", kind="other")]))
    [f] = r["findings"]
    assert f["rule"] == "outside_editor" and f["key"] == "editor:jim@client.com" and f["title"] == "jim@client.com outside Position2 made 2 changes" and len(f["evidence"]) == 2
    assert check(r, "editors")["state"] == "problem" and check(run(g(changes=[chg(4)])), "editors")["state"] == "ok"
    assert m.outside("a@gmail.com") is True and m.outside("a@position2.com") is False and m.outside("") is False and m.outside("an automated rule") is False and m.outside("a@sub.position2.com") is True


def test_a_change_history_that_could_not_be_read_is_said_not_taken_for_a_quiet_one():
    r = run(g(changes=None))
    assert check(r, "budgets")["state"] == "unknown" and check(r, "editors")["state"] == "unknown" and r["findings"] == []


# ── Conversion goals and access (held against the earlier readings) ──────────

def act(aid, name="Form", status="ENABLED", primary=True, include=True):
    return {"id": aid, "name": name, "status": status, "primary": primary, "include": include}


def prev_actions(*acts):
    return {a["id"]: {"name": a["name"], "status": a["status"], "primary": a["primary"], "include": a["include"]} for a in acts}


def test_a_primary_conversion_goal_that_was_hidden_or_stopped_counting_is_high_and_a_new_one_is_listed():
    before = [act("1"), act("2", "Call", primary=False)]
    hist = [snap(1, actions=prev_actions(*before))]
    hidden = run(g(actions=[act("1", status="HIDDEN"), act("2", "Call", primary=False)]), history=hist)
    [f] = hidden["findings"]
    assert f["rule"] == "goal_change" and f["level"] == "high" and f["evidence"] == ['"Form" went from enabled to hidden'] and f["title"] == 'Conversion goal changed: "Form" went from enabled to hidden'
    assert run(g(actions=[act("1", include=False), act("2", "Call", primary=False)]), history=hist)["findings"][0]["level"] == "high"
    minor = run(g(actions=[act("1"), act("2", "Call", primary=True)]), history=hist)
    assert minor["findings"][0]["level"] == "medium" and minor["findings"][0]["evidence"] == ['"Call" became primary']
    added = run(g(actions=[act("1"), act("2", "Call", primary=False), act("3", "New")]), history=hist)
    assert added["findings"][0]["evidence"] == ['"New" was added']
    gone = run(g(actions=[act("2", "Call", primary=False)]), history=hist)
    assert gone["findings"][0]["level"] == "high" and gone["findings"][0]["evidence"] == ['"Form" is gone (it was enabled, primary)']
    same = run(g(actions=before), history=hist)
    assert same["findings"] == [] and check(same, "goals")["state"] == "ok"


def test_conversion_goals_are_not_judged_on_the_first_day_and_an_old_reading_is_not_used():
    first = run(g(actions=[act("1")]), history=[])
    assert first["findings"] == [] and check(first, "goals")["state"] == "unknown" and "compares from tomorrow" in check(first, "goals")["note"]
    old = run(g(actions=[act("1", status="HIDDEN")]), history=[snap(20, actions=prev_actions(act("1")))])
    assert old["findings"] == [] and check(old, "goals")["state"] == "unknown"
    assert check(run(g(actions=None)), "goals")["state"] == "unknown"


def test_a_new_administrator_is_high_a_new_user_or_a_removed_one_is_medium_and_nothing_new_is_fine():
    hist = [snap(1, users={"ana@position2.com": "ADMIN", "bo@position2.com": "STANDARD"})]
    admin = run(g(users=[{"email": "ana@position2.com", "role": "ADMIN"}, {"email": "bo@position2.com", "role": "STANDARD"}, {"email": "x@evil.com", "role": "ADMIN"}]), history=hist)
    [f] = admin["findings"]
    assert f["rule"] == "access_change" and f["level"] == "high" and f["evidence"] == ["x@evil.com was added as admin"] and f["title"] == "Access changed: x@evil.com was added as admin"
    std = run(g(users=[{"email": "ana@position2.com", "role": "ADMIN"}, {"email": "bo@position2.com", "role": "STANDARD"}, {"email": "c@client.com", "role": "READ_ONLY"}]), history=hist)
    assert std["findings"][0]["level"] == "medium" and std["findings"][0]["evidence"] == ["c@client.com was added as read only"]
    removed = run(g(users=[{"email": "ana@position2.com", "role": "ADMIN"}]), history=hist)
    assert removed["findings"][0]["evidence"] == ["bo@position2.com was removed"] and removed["findings"][0]["level"] == "medium"
    raised = run(g(users=[{"email": "ana@position2.com", "role": "ADMIN"}, {"email": "bo@position2.com", "role": "ADMIN"}]), history=hist)
    assert raised["findings"][0]["level"] == "high" and raised["findings"][0]["evidence"] == ["bo@position2.com went from standard to admin"]
    same = run(g(users=[{"email": "ana@position2.com", "role": "ADMIN"}, {"email": "bo@position2.com", "role": "STANDARD"}]), history=hist)
    assert same["findings"] == [] and "2 people with access, the same as at the last reading" in check(same, "access")["note"]
    assert check(run(g(users=[{"email": "a@b.co", "role": "ADMIN"}]), history=[]), "access")["state"] == "unknown" and check(run(g(users=None)), "access")["state"] == "unknown"


# ── Meta: Pages and the activity log ────────────────────────────────────────

def test_a_page_that_is_gone_or_unpublished_is_high_and_the_first_day_only_lists_the_pages():
    hist = [snap(1, pages={"10": "Acme Dental", "11": "Acme Kids"})]
    gone = run(meta(pages=[{"id": "10", "name": "Acme Dental", "published": True}]), platform="meta", history=hist)
    [f] = gone["findings"]
    assert f["rule"] == "page_lost" and f["level"] == "high" and f["evidence"] == ['"Acme Kids" was connected at the last reading and is not now'] and f["title"] == "1 Page lost or unpublished"
    unpub = run(meta(pages=[{"id": "10", "name": "Acme Dental", "published": False}, {"id": "11", "name": "Acme Kids", "published": True}]), platform="meta", history=hist)
    assert unpub["findings"][0]["evidence"] == ['"Acme Dental" is not published']
    fine = run(meta(pages=[{"id": "10", "name": "Acme Dental", "published": True}, {"id": "11", "name": "Acme Kids", "published": True}]), platform="meta", history=hist)
    assert fine["findings"] == [] and "2 Pages, the same as at the last reading, all published" in check(fine, "pages")["note"]
    first = run(meta(pages=[{"id": "10", "name": "A", "published": True}]), platform="meta", history=[])
    assert first["findings"] == [] and check(first, "pages")["state"] == "unknown" and check(run(meta(pages=None), platform="meta"), "pages")["state"] == "unknown"
    first_unpub = run(meta(pages=[{"id": "10", "name": "A", "published": False}]), platform="meta", history=[])
    assert first_unpub["findings"][0]["rule"] == "page_lost"                                                                    # an unpublished Page needs no earlier reading


def ev(hours, type_, actor="Ana", was=None, now=None, what="did a thing", obj="x"):
    return {"at": (NOW - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S+0000"), "actor": actor, "type": type_, "what": what, "object": obj, "was": was, "now": now}


def test_meta_activity_that_touches_access_payment_or_limits_is_flagged_by_what_it_is():
    r = run(meta(activity=[ev(3, "ad_account_add_user_to_role", what="added Jo as admin"), ev(5, "ad_account_update_spend_limit", what="changed the spend limit"), ev(7, "add_funding_source", what="added a card")]), platform="meta")
    [f] = r["findings"]
    assert f["rule"] == "activity" and f["level"] == "high" and f["title"] == "3 changes touching payment, access or limits" and len(f["evidence"]) == 3 and "added Jo as admin" in f["evidence"][0]
    soft = run(meta(activity=[ev(5, "ad_account_update_spend_limit")]), platform="meta")
    assert soft["findings"][0]["level"] == "medium"
    assert run(meta(activity=[ev(5, "create_ad"), ev(6, "update_ad_creative")]), platform="meta")["findings"] == [] and "2 entries read" in check(run(meta(activity=[ev(5, "create_ad"), ev(6, "update_ad_creative")]), platform="meta"), "activity")["note"]
    assert run(meta(activity=[ev(100, "ad_account_add_user_to_role")]), platform="meta")["findings"] == [] and check(run(meta(activity=None), platform="meta"), "activity")["state"] == "unknown"


def test_meta_budget_and_bid_changes_and_outside_actors_are_flagged():
    r = run(meta(activity=[ev(4, "update_campaign_budget", was=50.0, now=200.0, what="update campaign budget"), ev(5, "update_ad_set_budget", was=100.0, now=110.0), ev(6, "update_ad_set_bid_info"),
                           ev(7, "update_ad_set_optimization_goal"), ev(8, "create_ad", actor="jim@client.com"), ev(9, "create_ad", actor="jim@client.com")]), platform="meta")
    got = {f["rule"]: f for f in r["findings"]}
    assert set(got) == {"budget_change", "bid_change", "outside_editor"} and got["budget_change"]["level"] == "high" and got["budget_change"]["title"] == "Budget change: update campaign budget from $50.00 to $200 by Ana"
    assert got["bid_change"]["title"] == "2 bid or goal changes in the last 3 days" and got["outside_editor"]["title"] == "jim@client.com outside Position2 made 2 changes"
    unknown = run(meta(activity=[ev(4, "update_campaign_budget")]), platform="meta")
    assert unknown["findings"][0]["level"] == "medium"


# ── The account as a whole ──────────────────────────────────────────────────

def test_the_checks_differ_by_platform_and_findings_are_ordered_high_first_with_counts_and_rules():
    r = run(g(status="SUSPENDED", changes=[chg(2, user="jim@client.com", kind="other")]))
    assert [c["key"] for c in r["checks"]] == ["status", "limit", "budgets", "editors", "goals", "access"]
    assert [f["rule"] for f in r["findings"]] == ["account_status", "outside_editor"] and r["counts"] == {"high": 1, "medium": 1, "low": 0} and r["findingCount"] == 2
    assert r["byRule"] == [{"rule": "account_status", "label": "The account is not active", "level": "high", "findings": 1}, {"rule": "outside_editor", "label": "Someone outside Position2 made changes", "level": "medium", "findings": 1}]
    assert [c["key"] for c in run(meta(), platform="meta")["checks"]] == ["status", "pages", "activity"]


def test_every_rule_has_a_level_an_area_a_label_and_a_fix_and_what_is_not_measured_is_written_down():
    assert set(m.ORDER) == set(m.RULES)
    for k, r in m.RULES.items():
        assert r["level"] in m.LEVEL_RANK and r["area"] and r["label"] and len(r["fix"]) > 60 and "—" not in r["fix"] + r["label"], k
    assert len(m.NOT_MEASURED) == 4 and all("—" not in t and "–" not in t for t in m.NOT_MEASURED)
