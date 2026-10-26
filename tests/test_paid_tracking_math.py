"""Tracking Data Health: the rules (tracker/paid_tracking_math.py) on constructed days. No client figures: every account here is made up."""
import datetime as dt

from tracker import paid_tracking_math as m

TODAY = dt.date(2026, 10, 26)
Y = TODAY - dt.timedelta(days=1)
NOW = dt.datetime(2026, 10, 26, 9, 0, tzinfo=dt.timezone.utc)


def d(n):
    """The date n days from yesterday (d(0) is yesterday, d(-1) the day before)."""
    return Y + dt.timedelta(days=n)


def raw(clicks=100, conv=4.0, over=None, start=-36, **extra):
    """An account's read: `clicks` and `conv` every day from `start` to yesterday, `over` {n: (clicks, conv)} for the days that differ."""
    over = over or {}
    days = []
    for n in range(start, 1):
        c, v = over.get(n, (clicks, conv))
        days.append({"date": d(n).isoformat(), "clicks": c, "cost": c * 1.5, "conv": v})
    return dict({"today": TODAY.isoformat(), "currency": "USD", "days": days, "actions": None, "autoTagging": None, "lists": None, "pixels": None}, **extra)


def kinds(res):
    return [(f["kind"], f["level"]) for f in res["findings"]]


def test_a_steady_account_is_healthy_with_a_full_score():
    res = m.analyse(raw(), now=NOW)
    assert res["findings"] == [] and res["score"] == 100 and res["status"] == "healthy" and res["baseline"]["days"] == 28 and res["baseline"]["conv"] == 4


def test_conversions_at_zero_while_clicks_hold_is_critical():
    res = m.analyse(raw(over={0: (95, 0)}), now=NOW)
    assert kinds(res) == [("conv_stopped", "critical")] and res["status"] == "broken" and res["score"] == 60
    f = res["findings"][0]
    assert "No conversions on Oct 25" in f["detail"] and "4 a day" in f["detail"] and "95 against 100 usual" in f["detail"]


def test_zero_conversions_with_traffic_also_gone_is_not_a_tracking_problem():
    assert m.analyse(raw(over={0: (30, 0)}), now=NOW)["findings"] == []


def test_a_low_volume_account_needs_three_days_at_zero_and_is_medium():
    one = m.analyse(raw(conv=1.0, over={0: (100, 0)}), now=NOW)
    assert one["findings"] == []
    three = m.analyse(raw(conv=1.0, over={0: (100, 0), -1: (100, 0), -2: (100, 0)}), now=NOW)
    assert kinds(three) == [("conv_stopped", "medium")] and "usually has 1 a day" in three["findings"][0]["detail"]


def test_an_account_too_small_to_expect_a_conversion_in_three_days_is_not_judged():
    assert m.analyse(raw(conv=0.2, over={0: (100, 0), -1: (100, 0), -2: (100, 0)}), now=NOW)["findings"] == []


def test_a_drop_of_sixty_percent_with_clicks_held_is_high_and_fifty_is_not():
    assert kinds(m.analyse(raw(conv=10, over={0: (100, 4)}), now=NOW)) == [("conv_drop", "high")]
    assert m.analyse(raw(conv=10, over={0: (100, 5)}), now=NOW)["findings"] == []


def test_a_conversion_rate_down_forty_percent_over_three_days_is_medium_with_enough_expected():
    over = {0: (100, 5), -1: (100, 5), -2: (100, 5)}                                   # 5% against 10%, 30 expected
    assert kinds(m.analyse(raw(conv=10, over=over), now=NOW)) == [("cvr_drop", "medium")]
    small = {0: (20, 1), -1: (20, 1), -2: (20, 1)}                                     # 6 expected: too few to say
    assert m.analyse(raw(clicks=20, conv=2, over=small), now=NOW)["findings"] == []


def test_a_short_history_is_said_and_not_judged():
    res = m.analyse(raw(start=-4, over={0: (100, 0)}), now=NOW)
    assert res["findings"] == [] and res["thin"] and "Only 4 usable days" in res["notes"][0]


def test_a_new_account_counts_its_baseline_from_its_first_day_with_clicks():
    over = {n: (0, 0) for n in range(-36, -10)}
    res = m.analyse(raw(over={**over, 0: (100, 0)}), now=NOW)
    assert res["baseline"]["days"] == 10 and kinds(res) == [("conv_stopped", "critical")]


def test_expected_drop_days_leave_the_baseline_and_lower_a_finding_on_them():
    windows = [{"from": d(-3).isoformat(), "to": d(0).isoformat(), "why": "site migration"}]
    res = m.analyse(raw(over={0: (100, 0), -1: (100, 0), -2: (100, 0), -3: (100, 0)}), config={"windows": windows, "muted": {}, "acks": {}}, now=NOW)
    f = res["findings"][0]
    assert res["baseline"]["days"] == 25 and res["baseline"]["conv"] == 4 and f["kind"] == "conv_stopped" and f["level"] == "low" and f["baseLevel"] == "critical"
    assert f["expected"] == "site migration" and res["status"] == "check"


def test_an_acknowledged_finding_stays_but_is_low_and_the_acknowledgement_runs_out():
    ack = {"conv_stopped": {"by": "ana", "at": (NOW - dt.timedelta(days=2)).isoformat(), "note": "tag fix going out"}}
    res = m.analyse(raw(over={0: (100, 0)}), config={"windows": [], "muted": {}, "acks": ack}, now=NOW)
    assert kinds(res) == [("conv_stopped", "low")] and res["findings"][0]["acked"]["note"] == "tag fix going out"
    old = {"conv_stopped": dict(ack["conv_stopped"], at=(NOW - dt.timedelta(days=m.ACK_DAYS)).isoformat())}
    assert kinds(m.analyse(raw(over={0: (100, 0)}), config={"windows": [], "muted": {}, "acks": old}, now=NOW)) == [("conv_stopped", "critical")]


def action(aid, name, per_day, recent=None, primary=True):
    days = {d(n).isoformat(): per_day for n in range(-40, 1)}
    for n, v in (recent or {}).items():
        days[d(n).isoformat()] = v
    return {"id": aid, "name": name, "status": "ENABLED", "category": "SUBMIT_LEAD_FORM", "type": "WEBPAGE", "primary": primary, "days": {k: v for k, v in days.items() if v}}


def test_a_primary_action_that_stops_for_three_days_is_high():
    acts = [action("1", "Lead form", 2, {0: 0, -1: 0, -2: 0}), action("2", "Calls", 2)]
    res = m.analyse(raw(conv=4, over={0: (100, 2), -1: (100, 2), -2: (100, 2)}, actions=acts), now=NOW)
    assert ("action_stopped", "high") in kinds(res) and res["actions"][0]["state"] == "stopped" and res["actions"][1]["state"] == "ok"


def test_secondary_and_quiet_actions_are_never_judged():
    acts = [action("1", "Page view", 5, {0: 0, -1: 0, -2: 0}, primary=False), action("2", "Rare", 0.05, {0: 0, -1: 0, -2: 0})]
    res = m.analyse(raw(actions=acts), now=NOW)
    assert res["findings"] == [] and [a["state"] for a in res["actions"]] == ["quiet", "secondary"]


def test_actions_that_stopped_with_the_account_are_evidence_of_one_finding():
    acts = [action("1", "Lead form", 4, {0: 0, -1: 0, -2: 0})]
    res = m.analyse(raw(over={0: (100, 0)}, actions=acts), now=NOW)
    assert kinds(res) == [("conv_stopped", "critical")] and res["findings"][0]["actions"] == ["1"] and "Lead form" in res["findings"][0]["evidence"][0]


def test_a_muted_action_is_shown_but_not_judged():
    acts = [action("1", "Lead form", 2, {0: 0, -1: 0, -2: 0})]
    res = m.analyse(raw(conv=2, actions=acts), config={"windows": [], "muted": {"action:1": "replaced by a new form"}, "acks": {}}, now=NOW)
    assert res["findings"] == [] and res["actions"][0]["state"] == "muted"


def test_auto_tagging_off_is_high_unless_the_team_left_it_out():
    assert kinds(m.analyse(raw(autoTagging=False), now=NOW)) == [("auto_tagging", "high")]
    assert m.analyse(raw(autoTagging=True), now=NOW)["findings"] == []
    assert m.analyse(raw(autoTagging=False), config={"windows": [], "muted": {"rule:auto_tagging": "uses its own click ids"}, "acks": {}}, now=NOW)["findings"] == []


def lists(size, kind="REMARKETING", platform="google"):
    return [{"id": "9", "name": "Site visitors 30d", "kind": kind, "size": size, "platform": platform}]


def hist(*pairs):
    return [{"day": d(n).isoformat(), "lists": {"9": s}} for n, s in pairs]


def test_a_list_that_held_a_thousand_and_dropped_under_is_medium():
    res = m.analyse(raw(lists=lists(800)), history=hist((-20, 1500), (-5, 1200)), now=NOW)
    assert kinds(res) == [("list_below", "medium")] and res["lists"][0]["state"] == "below"


def test_a_list_that_never_held_a_thousand_only_shrank_so_it_is_low():
    res = m.analyse(raw(lists=lists(300)), history=hist((-20, 400)), now=NOW)
    assert kinds(res) == [("list_drop", "low")] and "had 400 14 days ago" in res["findings"][0]["detail"]


def test_list_rules_wait_for_history_and_skip_customer_match():
    assert m.analyse(raw(lists=lists(300)), history=[], now=NOW)["lists"][0]["state"] == "new"
    assert m.analyse(raw(lists=lists(300, kind="CRM_BASED")), history=hist((-20, 5000)), now=NOW)["lists"] == []


def test_a_meta_audience_under_a_thousand_is_never_below_googles_search_minimum():
    res = m.analyse(raw(lists=lists(800, kind="WEBSITE", platform="meta")), history=hist((-20, 900), (-5, 1100)), now=NOW)
    assert kinds(res) == [] and res["lists"][0]["state"] == "ok"


def px(pid, hours_ago):
    return {"id": pid, "name": "Pixel " + pid, "lastFired": (NOW - dt.timedelta(hours=hours_ago)).isoformat() if hours_ago is not None else None}


def test_a_pixel_silent_for_a_day_while_spending_is_critical_and_high_when_another_fires():
    assert kinds(m.analyse(raw(pixels=[px("1", 30)]), now=NOW)) == [("pixel_silent", "critical")]
    assert kinds(m.analyse(raw(pixels=[px("1", 30), px("2", 1)]), now=NOW)) == [("pixel_silent", "high")]


def test_an_unused_pixel_or_an_account_that_did_not_spend_raises_nothing():
    assert m.analyse(raw(pixels=[px("1", 24 * 60), px("2", None)]), now=NOW)["findings"] == []
    res = m.analyse(raw(over={0: (0, 0)}, pixels=[px("1", 30)]), now=NOW)
    assert res["findings"] == [] and res["pixels"][0]["state"] == "idle"


def test_the_score_adds_up_the_findings_and_never_goes_below_zero():
    assert m.score_of([{"level": "critical"}, {"level": "high"}, {"level": "medium"}, {"level": "low"}]) == 22
    assert m.score_of([{"level": "critical"}] * 3) == 0


def test_the_snapshot_keeps_only_the_lists_the_rules_follow():
    r = raw(lists=lists(1200) + [{"id": "8", "name": "CRM", "kind": "CRM_BASED", "size": 50000, "platform": "google"}])
    res = m.analyse(r, now=NOW)
    assert m.snapshot_of(res, r)["lists"] == {"9": 1200}


def test_no_em_dash_in_any_wording():
    texts = [v["label"] + v["fix"] for v in m.RULES.values()]
    res = m.analyse(raw(over={0: (100, 0)}, actions=[action("1", "Lead form", 4, {0: 0, -1: 0, -2: 0})], autoTagging=False, pixels=[px("1", 30)]), now=NOW)
    texts += [f["title"] + f["detail"] + " ".join(f["evidence"]) for f in res["findings"]]
    assert not any("—" in t for t in texts)


def test_an_action_is_judged_over_the_days_it_takes_to_expect_three_conversions():
    assert [m.action_window(b) for b in (28, 14, 6, 5, 0)] == [3, 6, 14, None, None]
    slow = [action("1", "Lead", 0.5, {n: 0 for n in range(-4, 1)})]                      # 14 in 28 days, 5 quiet days: under the 6 it needs
    assert m.analyse(raw(actions=slow), now=NOW)["actions"][0]["state"] == "ok"
    gone = [action("1", "Lead", 0.5, {n: 0 for n in range(-5, 1)})]
    res = m.analyse(raw(actions=gone), now=NOW)
    assert kinds(res) == [("action_stopped", "high")] and "none in the last 6 days" in res["findings"][0]["detail"] and "about 3 were expected" in res["findings"][0]["detail"]


def test_an_action_too_slow_to_expect_three_in_two_weeks_is_never_called_stopped():
    rare = [action("1", "HubSpot lead", 5 / 31, {n: 0 for n in range(-2, 1)})]           # about 5 in 28 days, as on a live account
    res = m.analyse(raw(actions=rare), now=NOW)
    assert res["findings"] == [] and res["actions"][0]["state"] == "quiet"


def test_an_action_shows_what_it_recorded_over_the_window_it_was_judged_on():
    row = m.analyse(raw(actions=[action("1", "Lead", 0.25, {0: 0, -1: 0, -2: 0})]), now=NOW)["actions"][0]          # 7 in 28 days: judged over 12 days
    assert row["state"] == "ok" and row["days"] == 12 and row["recent"] == 2.25
