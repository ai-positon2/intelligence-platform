"""Cross-Channel & Attribution Analysis: the report, from reading to findings, on fakes."""
import datetime as dt

import pytest

from tests.gads_fakes import FakeStore
from tests.test_paid_xchannel_settings import CLICK7_VIEW1, action, adset, raw_action
from tracker import paid_gads, paid_segments, paid_xchannel as xc
from tracker import paid_xchannel_math as m

TODAY = dt.date(2026, 10, 18)
NOW = dt.datetime(2026, 10, 18, 12, 0, tzinfo=dt.timezone.utc)
GID, ACT = "1112223334", "act_5550001"
ACCTS = [{"id": GID, "name": "Acme Dental", "currency": "USD"}]
META_ACCT = {"id": ACT, "name": "Acme Dental Meta", "currency": "USD", "platform": "meta"}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    paid_gads.forget()
    monkeypatch.setattr(paid_segments, "_meta_target", lambda act, store: dict(META_ACCT, id=act))
    yield
    paid_gads.forget()


def iso_days(first, last):
    a, b = dt.date.fromisoformat(first), dt.date.fromisoformat(last)
    return [(a + dt.timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def shape(first, last, campaigns, numbers, missing=()):
    """campaigns [(id, type)], numbers(campaign index, date) -> (impr, clicks, cost, conv, value, allconv, allvalue, view)."""
    days = iso_days(first, last)
    rows = [[ci, di] + list(numbers(ci, d)) for di, d in enumerate(days) for ci in range(len(campaigns))]
    return {"start": first, "end": last, "days": days, "campaigns": [{"id": i, "name": i, "type": t, "status": "ENABLED", "budget": 0} for i, t in campaigns], "rows": rows, "missing": list(missing)}


GOOGLE_CAMPAIGNS = [("1", "SEARCH"), ("2", "PERFORMANCE_MAX")]
META_CAMPAIGNS = [("9", "META_LEADS"), ("8", "META_AWARENESS")]


def google_numbers(ci, d):
    return [(1000, 100, 40.0, 2, 200.0, 3, 260.0, 4), (500, 50, 20.0, 1, 80.0, 1.5, 90.0, 2)][ci]


def meta_numbers(ci, d):
    return [(2000, 100, 30.0, 1.5, 0.0, 2, 0.0, 0), (8000, 40, 20.0, 0, 0.0, 0, 0.0, 0)][ci]


def shape_fn(google=None, meta=None, fail=None):
    def fn(target, first, last):
        if fail and target["platform"] in fail:
            raise fail[target["platform"]]
        if target["platform"] == "meta":
            return shape(first, last, META_CAMPAIGNS, meta or meta_numbers)
        return shape(first, last, GOOGLE_CAMPAIGNS, google or google_numbers, missing=[])
    return fn


def run(account=GID + "+" + ACT, store=None, **kw):
    kw.setdefault("accounts_fn", lambda: ACCTS)
    kw.setdefault("shape_fn", shape_fn())
    kw.setdefault("search_fn", lambda gaql: [])                   # no test reaches for the network
    kw.setdefault("pages_fn", lambda path, params: [])
    return xc.report_for(account, store=store or FakeStore(), today=TODAY, now=NOW, **kw)


# ── both platforms ──────────────────────────────────────────────────────────

def test_the_report_covers_the_range_and_the_period_before_it():
    r = run(preset="30")
    assert r["range"]["start"] == "2026-09-18" and r["range"]["end"] == "2026-10-17" and r["range"]["days"] == 30 and r["range"]["label"] == "the last 30 days"
    assert r["previous"] == {"start": "2026-08-19", "end": "2026-09-17"}
    assert r["platforms"] == ["google", "meta"] and r["account"]["currency"] == "USD" and r["account"]["name"] == "Acme Dental + Acme Dental Meta"


def test_each_platform_has_its_figures_shares_and_comparison_with_the_previous_period():
    r = run()
    g, mt = r["channels"]["google"], r["channels"]["meta"]
    assert g["cur"]["cost"] == 1800.0 and g["cur"]["conv"] == 90.0 and g["cur"]["cpa"] == 20.0 and g["cur"]["value"] == 8400.0 and g["cur"]["roas"] == pytest.approx(4.6667, abs=1e-3)
    assert mt["cur"]["cost"] == 1500.0 and mt["cur"]["conv"] == 45.0 and mt["cur"]["cpa"] == pytest.approx(33.3333, abs=1e-3)
    assert g["share"] == {"cost": pytest.approx(54.5, abs=0.1), "conv": pytest.approx(66.7, abs=0.1), "value": 100.0}
    assert g["delta"]["cost"] == 0.0 and g["delta"]["cpa"] == 0.0
    assert g["account"]["name"] == "Acme Dental" and mt["account"]["id"] == ACT and g["name"] == "Google Ads"


def test_the_blended_total_adds_the_platforms_and_its_cost_is_spend_over_conversions():
    b = run()["blended"]["cur"]
    assert b["cost"] == 3300.0 and b["conv"] == 135.0 and b["cpa"] == pytest.approx(3300 / 135, abs=1e-3)


def test_campaign_types_carry_a_role_and_their_share_of_the_spend():
    r = run()
    types = {t["label"]: t for t in r["channels"]["meta"]["types"]}
    assert types["Awareness"]["role"] == "create" and types["Awareness"]["roleLabel"] == "Creates demand" and types["Awareness"]["conv"] == 0.0 and types["Awareness"]["cpa"] is None
    assert types["Awareness"]["shareOfPlatform"] == pytest.approx(40.0) and types["Awareness"]["shareOfAll"] == pytest.approx(600 / 3300 * 100, abs=0.1)
    assert [t["label"] for t in r["channels"]["google"]["types"]] == ["Search", "Performance Max"]


def test_a_platform_that_records_no_value_has_no_return_rather_than_a_return_of_zero():
    r = run()
    assert r["channels"]["meta"]["cur"]["roas"] is None and r["channels"]["google"]["cur"]["roas"] is not None
    assert {t["label"]: t["roas"] for t in r["channels"]["meta"]["types"]} == {"Leads": None, "Awareness": None}


def test_the_findings_include_the_gap_and_the_idle_awareness_spend():
    r = run()
    ids = [f["id"] for f in r["findings"]]
    assert "cpa-gap" in ids and "idle-meta" in ids
    assert r["summary"]["high"] >= 1 and r["summary"]["findings"] == len(r["findings"]) and r["summary"]["cpa"] == r["blended"]["cur"]["cpa"]


def test_what_moved_the_blended_cost_is_split_when_both_platforms_convert_in_both_periods():
    def meta(ci, d):
        worse = d < "2026-09-18"                                   # the previous period was cheaper on Meta
        return [(2000, 100, 15.0 if worse else 30.0, 1.5, 0.0, 2, 0.0, 0), (8000, 40, 20.0, 0, 0.0, 0, 0.0, 0)][ci]
    r = run(shape_fn=shape_fn(meta=meta))
    d = r["decomposition"]
    assert d["ok"] and d["change"] == pytest.approx(d["mix"] + d["rateTotal"]) and d["rate"]["meta"] > 0 and d["rate"]["google"] == pytest.approx(0.0)
    assert "blended-move" in [f["id"] for f in r["findings"]]


def test_weeks_are_whole_monday_to_sunday_weeks_over_both_periods_and_too_few_for_a_link():
    r = run()
    assert len(r["weeks"]) == 7 and r["weeks"][0]["start"] == "2026-08-24" and r["weeks"][0]["google"]["cost"] == 420.0 and r["weeks"][0]["meta"]["cost"] == 350.0
    assert r["links"]["ok"] is False and r["links"]["weeks"] == 7 and "11 whole weeks" in r["links"]["why"]


def test_ninety_days_has_enough_weeks_to_test_for_a_link():
    r = run(preset="90")
    assert r["links"]["ok"] is True and r["links"]["weeks"] >= 11 and len(r["links"]["tests"]) == 4


# ── conversion measure ──────────────────────────────────────────────────────

def test_the_all_measure_uses_the_other_conversion_columns():
    r = run(measure="all")
    assert r["channels"]["google"]["cur"]["conv"] == (3 + 1.5) * 30 and r["channels"]["meta"]["cur"]["conv"] == 2 * 30
    assert r["measure"] == "all" and r["measureUsed"] == {"google": "all", "meta": "all"}


def test_when_google_will_not_give_all_conversions_google_stays_on_primary_and_it_says_so():
    def fn(target, first, last):
        if target["platform"] == "google":
            return shape(first, last, GOOGLE_CAMPAIGNS, google_numbers, missing=[5, 6])
        return shape(first, last, META_CAMPAIGNS, meta_numbers)
    r = run(measure="all", shape_fn=fn)
    assert r["measureUsed"] == {"google": "primary", "meta": "all"} and r["channels"]["google"]["cur"]["conv"] == 90.0
    assert any("would not give All conversions" in w for w in r["warnings"])


def test_google_view_through_is_shown_and_metas_is_not():
    r = run()
    assert r["channels"]["google"]["view"] == (4 + 2) * 30 and r["channels"]["meta"]["view"] is None


def test_an_unknown_measure_or_range_is_refused():
    with pytest.raises(xc.BadRequest):
        run(measure="best")
    with pytest.raises(xc.BadRequest):
        run(preset="365")


# ── one platform ────────────────────────────────────────────────────────────

def test_one_platform_alone_is_a_report_without_a_comparison():
    r = run(account=GID)
    assert r["platforms"] == ["google"] and list(r["channels"]) == ["google"]
    assert r["decomposition"] == {"ok": False, "why": "The split needs both platforms."} and r["links"]["ok"] is False and r["attribution"]["notes"] == []
    assert "one-platform" in [f["id"] for f in r["findings"]] and r["blended"]["cur"]["cost"] == 1800.0
    assert r["channels"]["google"]["share"]["cost"] == 100.0


def test_a_meta_account_alone_reads_too():
    r = run(account=ACT)
    assert r["platforms"] == ["meta"] and r["channels"]["meta"]["cur"]["cost"] == 1500.0


def test_one_platform_failing_leaves_the_other_and_says_so():
    r = run(shape_fn=shape_fn(fail={"meta": paid_gads.AdsError("Meta said no")}))
    assert r["platforms"] == ["google"] and any("Meta Ads could not be read" in w and "Meta said no" in w for w in r["warnings"])


def test_a_platform_that_could_not_be_read_is_not_asked_for_its_settings():
    asked = []

    def pages(path, params):
        asked.append(path)
        return []
    run(shape_fn=shape_fn(fail={"meta": paid_gads.AdsError("Meta said no")}), pages_fn=pages)
    assert asked == []


def test_the_only_platform_failing_is_raised():
    with pytest.raises(paid_gads.AdsError, match="down"):
        run(account=GID, shape_fn=shape_fn(fail={"google": paid_gads.AdsError("down")}))


def test_both_failing_is_raised_as_an_error():
    with pytest.raises(paid_gads.AdsError):
        run(shape_fn=shape_fn(fail={"google": paid_gads.AdsError("a"), "meta": paid_gads.AdsError("b")}))


def test_a_spent_allowance_is_raised_even_when_the_other_platform_could_be_read():
    with pytest.raises(paid_gads.OverQuota):
        run(shape_fn=shape_fn(fail={"meta": paid_gads.OverQuota("wait", until=1.0)}))


def test_two_currencies_are_refused():
    with pytest.raises(paid_segments.BadRequest):
        run(accounts_fn=lambda: [dict(ACCTS[0], currency="EUR")])


# ── how each platform counts ────────────────────────────────────────────────

def settings_fns(calls=None):
    def search(gaql):
        if calls is not None:
            calls.append("g")
        if "FROM conversion_action" in gaql:
            return [raw_action(1, "Booked", click=30, view=1)]
        return [{"segments": {"conversionAction": "customers/1/conversionActions/1"}, "metrics": {"conversions": 90, "allConversions": 100}}]

    def pages(path, params):
        if calls is not None:
            calls.append("m")
        return [adset(1, CLICK7_VIEW1)] if path == "adsets" else [{"adset_id": "1", "spend": "1500"}]
    return {"search_fn": search, "pages_fn": pages}


def test_the_attribution_comparison_names_the_windows_and_the_view_through():
    r = run(**settings_fns())
    ids = [n["id"] for n in r["attribution"]["notes"]]
    assert ids[:2] == ["click-window", "view-through"] and "models" in ids
    assert r["attribution"]["google"]["dominantClick"] == 30 and r["attribution"]["meta"]["dominant"]["click"] == 7
    assert "(180)" in next(n for n in r["attribution"]["notes"] if n["id"] == "view-through")["text"]
    assert "attr-click-window" in [f["id"] for f in r["findings"]]


def test_the_settings_are_saved_and_read_again_only_on_refresh():
    calls, store = [], FakeStore()
    run(store=store, **settings_fns(calls))
    n = len(calls)
    assert n > 0
    run(store=store, **settings_fns(calls))
    assert len(calls) == n
    run(store=store, force=True, **settings_fns(calls))
    assert len(calls) > n


def test_settings_that_cannot_be_read_are_a_warning_and_the_numbers_stand():
    def bad(gaql):
        raise paid_gads.AdsError("no access")
    r = run(search_fn=bad, pages_fn=settings_fns()["pages_fn"])
    assert r["channels"]["google"]["cur"]["cost"] == 1800.0
    assert any("How Google Ads counts conversions could not be read" in w and "no access" in w for w in r["warnings"])
    assert r["attribution"]["notes"] == [] and r["attribution"]["google"] is None and r["attribution"]["meta"] is not None


def test_the_goal_note_appears_when_googles_goals_and_metas_spend_differ():
    def fn(target, first, last):
        if target["platform"] == "meta":
            return shape(first, last, [("7", "META_SALES")], lambda ci, d: (2000, 100, 50.0, 2, 100.0, 2, 100.0, 0))
        return shape(first, last, GOOGLE_CAMPAIGNS, google_numbers)

    def search(gaql):
        return [raw_action(1, "Form", category="SUBMIT_LEAD_FORM")] if "FROM conversion_action" in gaql else []
    r = run(shape_fn=fn, search_fn=search, pages_fn=settings_fns()["pages_fn"])
    assert "goal-mismatch" in [n["id"] for n in r["attribution"]["notes"]]


# ── the person's own numbers ────────────────────────────────────────────────

def test_typed_numbers_reconcile_the_claims():
    r = run(actual_conv="1,000", actual_value="")
    rec = r["reconciliation"]
    assert rec["conversions"]["actual"] == 1000.0 and rec["conversions"]["claimed"] == 135.0 and rec["conversions"]["over"] is False and rec["value"] is None
    assert rec["blendedCpa"] == pytest.approx(3.3)
    r = run(actual_conv="100")
    assert r["reconciliation"]["conversions"]["over"] is True and "overlap" in [f["id"] for f in r["findings"]]


def test_nothing_typed_means_no_reconciliation():
    assert run()["reconciliation"] is None


@pytest.mark.parametrize("bad", ["abc", "-5", "1e99", "nan", "inf"])
def test_a_typed_number_that_is_not_a_sensible_number_is_refused(bad):
    with pytest.raises(xc.BadRequest):
        run(actual_conv=bad)
    with pytest.raises(xc.BadRequest):
        run(actual_value=bad)


def test_clean_actual_accepts_separators_and_empties():
    assert xc.clean_actual("1,234.5", "x") == 1234.5 and xc.clean_actual(" ", "x") is None and xc.clean_actual(None, "x") is None and xc.clean_actual(0, "x") == 0.0
    assert xc.clean_actual("1 000", "x") == 1000.0


def test_a_zero_typed_is_not_a_reconciliation():
    r = run(actual_conv="0")
    assert r["reconciliation"]["conversions"] is None


# ── the shape of the whole answer ───────────────────────────────────────────

def test_the_report_is_plain_data_the_page_and_the_files_can_use():
    import json
    r = run(**settings_fns(), actual_conv="100", actual_value="9000")
    json.dumps(r)                                                  # nothing in it is a tuple key, a set or a date
    assert r["generatedAt"].startswith("2026-10-18") and r["notes"] and r["measureLabel"].startswith("Conversions")
    assert any("Meta's conversions are the results" in n for n in r["notes"]) and any("Google's Conversions column" in n for n in r["notes"])


def test_one_platform_notes_only_name_that_platform():
    r = run(account=GID)
    assert not any("Meta's conversions are the results" in n for n in r["notes"])


def test_refresh_is_limited_per_account_and_re_reads_what_it_is_asked_for():
    store = FakeStore()
    run(store=store, force=True)
    with pytest.raises(paid_gads.RefreshTooSoon):
        run(store=store, force=True)
    run(store=store)                                               # an ordinary read is never held back
    run(store=FakeStore(), force=True)                             # another store (another account's history) is not


def test_the_cooldown_is_per_account():
    store = FakeStore()
    run(store=store, force=True)
    run(account=GID, store=store, force=True)


# ── reading in pieces ───────────────────────────────────────────────────────

def test_without_a_stand_in_each_platform_is_read_period_by_period_and_then_as_a_whole(monkeypatch):
    seen = []

    def fake(target, first, last, **kw):
        seen.append((target["platform"], first, last, kw["force"]))
        return shape(first, last, GOOGLE_CAMPAIGNS if target["platform"] == "google" else META_CAMPAIGNS, google_numbers if target["platform"] == "google" else meta_numbers)
    monkeypatch.setattr(xc, "_shape", fake)
    r = xc.report_for(GID + "+" + ACT, store=FakeStore(), today=TODAY, now=NOW, accounts_fn=lambda: ACCTS, search_fn=lambda g: [], pages_fn=lambda p, q: [], force=True)
    assert [x[:3] for x in seen] == [("google", "2026-08-19", "2026-09-17"), ("google", "2026-09-18", "2026-10-17"), ("google", "2026-08-19", "2026-10-17"),
                                     ("meta", "2026-08-19", "2026-09-17"), ("meta", "2026-09-18", "2026-10-17"), ("meta", "2026-08-19", "2026-10-17")]
    assert [x[3] for x in seen] == [True, True, False, True, True, False]            # Refresh re-reads the pieces; the whole is then assembled from what they saved
    assert r["platforms"] == ["google", "meta"] and r["range"]["days"] == 30


def test_an_ordinary_read_asks_each_piece_without_force(monkeypatch):
    seen = []
    monkeypatch.setattr(xc, "_shape", lambda t, a, b, **kw: seen.append(kw["force"]) or shape(a, b, GOOGLE_CAMPAIGNS, google_numbers))
    xc.report_for(GID, store=FakeStore(), today=TODAY, now=NOW, accounts_fn=lambda: ACCTS, search_fn=lambda g: [], pages_fn=lambda p, q: [])
    assert seen == [False, False, False]


def test_a_piece_that_fails_counts_as_that_platform_failing(monkeypatch):
    def fake(target, first, last, **kw):
        if target["platform"] == "meta" and first == "2026-09-18":
            raise paid_gads.AdsError("Meta would not answer the second piece")
        return shape(first, last, GOOGLE_CAMPAIGNS if target["platform"] == "google" else META_CAMPAIGNS, google_numbers if target["platform"] == "google" else meta_numbers)
    monkeypatch.setattr(xc, "_shape", fake)
    r = xc.report_for(GID + "+" + ACT, store=FakeStore(), today=TODAY, now=NOW, accounts_fn=lambda: ACCTS, search_fn=lambda g: [], pages_fn=lambda p, q: [])
    assert r["platforms"] == ["google"] and any("second piece" in w for w in r["warnings"])
