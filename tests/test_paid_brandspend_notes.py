"""Brand Spend Alert: what the readings add to an account's findings and fix plan (tracker/paid_brandspend_notes.py, the extras in paid_brandspend_plan.py), worked by hand. No network."""
import datetime as dt
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from tracker import paid_brandspend_math as m  # noqa: E402

TUE = dt.date(2026, 10, 20)


def rows(weekly, clicks=70, conv=14.0, cid="11", name="P2_Brand", share=None, lost_rank=0.03, lost_budget=0.02, today=TUE, overrides=None):
    """Campaign-day rows for eight whole weeks (weekly costs oldest first; a week of None has no rows) and, when `share` is a list of eight shares, a share on every day."""
    out = []
    first = m.weeks(today)[0][0]
    for i, cost in enumerate(weekly):
        if cost is None:
            continue
        for d in range(7):
            day = first + dt.timedelta(days=7 * i + d)
            o = (overrides or {}).get(day.isoformat())
            c, k, v = o if o else (cost / 7.0, clicks / 7.0, conv / 7.0)
            r = {"id": cid, "name": name, "date": day.isoformat(), "status": "ENABLED", "strategy": "MAXIMIZE_CLICKS", "budget": 40.0, "impressions": 1000, "clicks": k, "cost": c, "conversions": v}
            if share:
                r.update(share=share[i], lostRank=lost_rank[i] if isinstance(lost_rank, list) else lost_rank, lostBudget=lost_budget[i] if isinstance(lost_budget, list) else lost_budget)
            out.append(r)
    last = [w for w in weekly if w is not None]
    monday = today - dt.timedelta(days=today.weekday())
    if last:
        for d in range(today.weekday()):          # the days of this week that have happened, at last week's pace
            out.append({"id": cid, "name": name, "date": (monday + dt.timedelta(days=d)).isoformat(), "status": "ENABLED", "strategy": "MAXIMIZE_CLICKS", "budget": 40.0, "impressions": 1000,
                        "clicks": clicks / 7.0, "cost": last[-1] / 7.0, "conversions": conv / 7.0})
    return out


FLAT = [210] * 8
USUAL = [203, 196, 210, 189, 203, 210, 196]


def go(r, **kw):
    return m.analyse(r, kw.pop("today", TUE), **kw)


def kinds(a):
    return [f["kind"] for f in a["findings"]]


def step(a, sid):
    return next(s for s in a["plan"]["steps"] if s["id"] == sid)


# ── Efficiency ──────────────────────────────────────────────────────────────

def test_a_rise_with_the_same_conversions_says_the_account_paid_more_for_the_same_result():
    a = go(rows(USUAL + [301]))
    f = a["findings"][0]
    assert a["efficiency"]["kind"] == "same-for-more" and a["efficiency"]["usualCpa"] == 14.5 and a["efficiency"]["cpa"] == 21.5
    assert f["detail"].endswith("Conversions were about the same (14 against a usual 14): the account paid more for the same result. Cost per conversion went from $14.50 to $21.50.")


def test_a_rise_that_conversions_followed_says_the_extra_spend_bought_results():
    r = rows(USUAL + [301])
    for x in [x for x in r if "2026-10-12" <= x["date"] <= "2026-10-18"]:
        x["conversions"] = 3.0                                  # 21 against a usual 14
    a = go(r)
    assert a["efficiency"]["kind"] == "followed" and "Conversions rose too (21 against a usual 14), so the extra spend bought results. Cost per conversion went from $14.50 to $14.33." in a["findings"][0]["detail"]


def test_a_fall_with_conversions_held_is_cheaper_for_the_same_result_and_a_fall_with_fewer_is_a_loss():
    held = go(rows(USUAL + [98], clicks=70))
    assert held["efficiency"]["kind"] == "same-for-less" and "Conversions held (14 against a usual 14), so it was cheaper for the same result." in held["findings"][0]["detail"]
    r = rows(USUAL + [98])
    for x in [x for x in r if "2026-10-12" <= x["date"] <= "2026-10-18"]:
        x["conversions"] = 1.0
    assert "Conversions fell too (7 against a usual 14)." in go(r)["findings"][0]["detail"]


def test_without_conversions_in_the_rows_no_sentence_is_written():
    r = rows(USUAL + [301], conv=0.0)
    assert go(r)["efficiency"] is None and "Conversions" not in go(r)["findings"][0]["detail"]


# ── Impression share ────────────────────────────────────────────────────────

SHARE = [0.90] * 7 + [0.60]
RANK = [0.03] * 7 + [0.35]


def test_a_rise_with_the_share_lost_to_rank_names_a_competitor_and_the_plan_sizes_what_it_cost():
    a = go(rows([200] * 7 + [280], share=SHARE, lost_rank=RANK))
    assert a["share"]["signal"] == "competitor" and a["status"] == "over"
    assert "The brand's impression share fell from 90% to 60% and the part lost to rank rose from 3% to 35%: that is what a competitor bidding on the brand name looks like." in a["findings"][0]["detail"]
    s = step(a, "defend")
    assert s["level"] == "high" and "fell from 90% to 60%" in s["why"] and "Auction insights" in " ".join(s["how"]) and "trademark complaint" in " ".join(s["how"])
    assert "it would have got about 35 more, worth about $140 at the brand's own cost per click" in s["worth"] and "got 70 clicks" in s["worth"]


def test_the_defend_step_is_medium_when_the_share_fell_under_twenty_points():
    a = go(rows([200] * 7 + [280], share=[0.90] * 7 + [0.80], lost_rank=[0.03] * 7 + [0.15]))
    assert a["share"]["signal"] == "competitor" and step(a, "defend")["level"] == "medium"


def test_a_rise_in_price_with_the_share_held_says_the_same_searches_cost_more():
    a = go(rows([200] * 7 + [280], share=[0.90] * 7 + [0.89]))
    assert a["share"]["signal"] == "steady" and a["cause"] == "cpc"
    assert "The impression share held (89%, usually 90%), so the same searches were won at a higher price" in a["findings"][0]["detail"]
    assert "impression share held" not in go(rows([200] * 7 + [280], clicks=98, share=[0.90] * 7 + [0.89]))["findings"][0]["detail"] or go(rows([200] * 7 + [280], clicks=98, share=[0.90] * 7 + [0.89]))["cause"] != "clicks"


def test_a_held_share_is_only_said_when_the_price_of_a_click_is_why_the_spend_rose():
    r = rows([200] * 7 + [300], clicks=70, share=[0.90] * 7 + [0.89])
    for x in [x for x in r if "2026-10-12" <= x["date"] <= "2026-10-18"]:
        x["clicks"] = 105 / 7.0                                       # more clicks, at the usual price
    a = go(r)
    assert a["cause"] == "clicks" and a["share"]["signal"] == "steady" and "impression share held" not in a["findings"][0]["detail"]
    u = go(rows([200] * 7 + [100], share=[0.90] * 7 + [0.89]))
    assert u["status"] == "under" and "impression share held" not in u["findings"][0]["detail"]


def test_when_there_are_too_few_clicks_to_size_the_competitor_the_step_says_nothing_about_worth():
    a = go(rows([200] * 7 + [280], clicks=1, share=SHARE, lost_rank=RANK))
    assert step(a, "defend")["worth"] is None


def test_a_competitor_who_costs_under_twenty_points_of_share_is_medium_even_when_the_spend_is_in_its_band():
    a = go(rows([200] * 8, share=[0.90] * 7 + [0.75], lost_rank=[0.03] * 7 + [0.20]))
    assert a["status"] == "ok" and a["findings"][0]["kind"] == "competitor" and a["findings"][0]["level"] == "medium"


def test_a_competitor_is_named_even_when_the_spend_did_not_leave_its_band():
    a = go(rows([200] * 8, share=SHARE, lost_rank=RANK))
    assert a["status"] == "ok" and kinds(a) == ["competitor"]
    f = a["findings"][0]
    assert f["level"] == "high" and f["title"] == "Brand impression share fell from 90% to 60%, mostly lost to rank" and f["nextStep"].startswith("Start with: Defend the brand name against a competitor.")


def test_a_fall_in_share_with_more_lost_to_budget_says_the_brand_is_running_out_of_daily_budget():
    a = go(rows([200] * 7 + [280], share=SHARE, lost_rank=0.03, lost_budget=[0.02] * 7 + [0.35]))
    assert a["share"]["signal"] == "budget"
    assert "the part lost to budget rose from 2% to 35%: the brand campaigns are running out of daily budget." in a["findings"][0]["detail"] and not [s for s in a["plan"]["steps"] if s["id"] == "defend"]


def test_a_fall_in_share_with_nothing_lost_to_rank_or_budget_rising_names_no_cause():
    a = go(rows([200] * 7 + [280], share=SHARE, lost_rank=0.03, lost_budget=0.02))
    assert a["share"]["signal"] is None and "impression share" not in a["findings"][0]["detail"]


# ── The agreed limits and the month ─────────────────────────────────────────

def test_a_weekly_maximum_the_team_saved_is_a_high_finding_with_a_step_that_gives_the_daily_figure():
    a = go(rows(USUAL + [301]), extra={"settings": {"weeklyMax": 250}})
    f = [x for x in a["findings"] if x["kind"] == "limit-over"][0]
    assert f["level"] == "high" and f["title"] == "Brand spend is $51.00 over the agreed weekly maximum of $250" and "the limit the team saved for this account is $250" in f["detail"]
    s = step(a, "agreed")
    assert s["level"] == "high" and "together they come to about $35.71 a day" in " ".join(s["how"]) and s["worth"] == "Back inside the limit the week would have cost $51.00 less."
    assert a["limits"] == {"min": None, "max": 250, "cost": 301.0, "status": "over", "by": 51.0}


def test_a_weekly_minimum_not_reached_is_a_high_finding_that_points_to_the_brand_is_monitor():
    a = go(rows(USUAL + [98]), extra={"settings": {"weeklyMin": 150}})
    f = [x for x in a["findings"] if x["kind"] == "limit-under"][0]
    assert f["title"] == "Brand spend is $52.00 under the agreed weekly minimum of $150" and "/p2/paid-marketing/brand-is" in " ".join(step(a, "agreed")["how"])


def test_a_week_inside_the_saved_limits_says_nothing_about_them():
    a = go(rows(FLAT), extra={"settings": {"weeklyMax": 250, "weeklyMin": 150}})
    assert a["limits"]["status"] == "ok" and not [k for k in kinds(a) if k.startswith("limit")] and not [s for s in a["plan"]["steps"] if s["id"] == "agreed"]


def test_a_month_on_pace_to_pass_its_budget_says_when_it_runs_out_and_what_daily_budget_fits():
    a = go(rows(FLAT), extra={"settings": {"monthlyBudget": 800}})
    mo = a["month"]
    assert mo["spent"] == 570.0 and mo["projected"] == 930.0 and mo["status"] == "pace-over" and mo["runsOutOn"] == "2026-10-27"
    f = [x for x in a["findings"] if x["kind"] == "month-pace"][0]
    assert f["level"] == "medium" and f["title"] == "On pace for $930 this month against a $800 brand budget"
    assert f["detail"] == "$570 spent in 19 days ($30.00 a day); at that rate the budget runs out on 2026-10-27, 5 days before the month ends."
    s = step(a, "pace")
    assert "about $19.17 a day for the 12 days left (what is left of the budget, $230, divided by the days)" in " ".join(s["how"]) and "ends about $130 over the budget" in s["worth"] and s["level"] == "medium"


def test_a_month_already_over_its_budget_is_high_and_says_so():
    a = go(rows(FLAT), extra={"settings": {"monthlyBudget": 500}})
    f = [x for x in a["findings"] if x["kind"] == "month-over"][0]
    assert f["level"] == "high" and f["title"] == "The brand has already spent $570 this month, over its $500 budget" and "19 of 31 days of the month have gone" in f["detail"]
    s = step(a, "pace")
    assert s["level"] == "high" and "with 12 days left" in s["why"] and "the rest of the month would add about $360" in s["worth"]


def test_a_month_that_will_use_little_of_its_budget_is_a_low_note_with_no_step():
    a = go(rows(FLAT), extra={"settings": {"monthlyBudget": 1300}})
    f = [x for x in a["findings"] if x["kind"] == "month-under"][0]
    assert f["level"] == "low" and f["title"] == "On pace to use only 72% of the $1,300 brand budget this month" and not [s for s in a["plan"]["steps"] if s["id"] == "pace"]


def test_a_month_on_pace_is_not_mentioned_and_no_budget_means_no_budget_findings():
    assert not [k for k in kinds(go(rows(FLAT), extra={"settings": {"monthlyBudget": 930}})) if k.startswith("month")]
    a = go(rows(FLAT))
    assert a["month"]["budget"] is None and not [k for k in kinds(a) if k.startswith("month")]


# ── Brand searches in other campaigns ───────────────────────────────────────

LEAK = [{"campaignId": "12", "campaign": "NonBrand Implants", "term": "northwind dental near me", "cost": 240.0, "clicks": 48, "conversions": 2},
        {"campaignId": "13", "campaign": "PMax All", "term": "northwind implants", "cost": 60.0, "clicks": 12, "conversions": 0},
        {"campaignId": "12", "campaign": "NonBrand Implants", "term": "dentist near me", "cost": 500.0, "clicks": 100, "conversions": 9}]


def test_brand_searches_paid_for_elsewhere_are_priced_against_the_brand_campaign_and_the_plan_says_how_to_stop_it():
    a = go(rows(FLAT), extra={"settings": {"brandTerms": ["Northwind"]}, "leak": LEAK})
    lk = a["leak"]
    assert lk["cost"] == 300.0 and lk["clicks"] == 60 and lk["cpc"] == 5.0 and lk["brandCpc"] == 3.0 and lk["overpay"] == 120.0 and lk["shareOfBrand"] == round(300 / 900, 4)
    f = [x for x in a["findings"] if x["kind"] == "leak"][0]
    assert f["level"] == "medium" and f["title"] == "Non-brand campaigns paid $300 for searches that hold the brand name (last 30 days)"
    assert "60 clicks in NonBrand Implants, PMax All at $5.00 a click against $3.00 in the brand campaigns, about $120 more than the same clicks cost there. The brand words checked were \"northwind\"." == f["detail"]
    s = step(a, "leak")
    assert s["level"] == "medium" and s["campaigns"] == ["NonBrand Implants", "PMax All"] and "[northwind]" in " ".join(s["how"]) and "Brand exclusions" in " ".join(s["how"])
    assert "60 clicks cost $300 in these campaigns and would have cost about $180 in the brand campaign at its own price: about $120 overpaid in 30 days (about $120 a month)" in s["worth"]


def test_a_small_leak_is_a_low_note_and_a_tiny_one_is_not_mentioned():
    small = [{"campaignId": "12", "campaign": "NonBrand", "term": "northwind x", "cost": 30.0, "clicks": 10, "conversions": 0}]
    a = go(rows(FLAT), extra={"settings": {"brandTerms": ["northwind"]}, "leak": small})
    f = [x for x in a["findings"] if x["kind"] == "leak"][0]
    assert f["level"] == "low" and a["leak"]["overpay"] == 0.0 and step(a, "leak")["level"] == "low" and step(a, "leak")["worth"] is None
    tiny = [{"campaignId": "12", "campaign": "NonBrand", "term": "northwind x", "cost": 10.0, "clicks": 3, "conversions": 0}]
    t = go(rows(FLAT), extra={"settings": {"brandTerms": ["northwind"]}, "leak": tiny})
    assert "leak" not in kinds(t) and not [x for x in t["plan"]["steps"] if x["id"] == "leak"] and t["leak"]["cost"] == 10.0


def test_no_brand_words_saved_means_no_leak_reading_and_the_page_is_offered_words_from_the_campaign_names():
    a = go(rows(FLAT, name="Northwind | Brand | Search"), extra={"leak": LEAK})
    assert a["leak"] is None and a["terms"] == [] and a["suggestedTerms"] == ["northwind"] and a["leakRead"] is None
    saved = go(rows(FLAT, name="Northwind | Brand | Search"), extra={"settings": {"brandTerms": ["northwind"]}})
    assert saved["suggestedTerms"] == [] and saved["leakRead"] == "not read" and saved["leak"] is None
    assert go(rows(FLAT), extra={"settings": {"brandTerms": ["northwind"]}, "leak": []})["leakRead"] == "read"


# ── Spikes ──────────────────────────────────────────────────────────────────

def test_a_day_of_clicks_that_did_not_convert_is_a_medium_finding_with_a_step_for_the_invalid_clicks():
    a = go(rows(FLAT, overrides={"2026-10-14": (150.0, 40.0, 0.0)}))
    f = [x for x in a["findings"] if x["kind"] == "spike"][0]
    assert f["level"] == "medium" and f["title"] == "Wednesday 2026-10-14: $150 spent against a usual day of $30.00"
    assert f["detail"] == "Its clicks were 4.0 times the usual and none converted: the shape of clicks that are not people."
    s = step(a, "spike")
    assert s["title"] == "Find out what happened on Wednesday 2026-10-14" and s["level"] == "medium" and "Invalid clicks" in " ".join(s["how"]) and "IP exclusion" in " ".join(s["how"])
    assert s["why"] == "The brand campaigns spent $150 that day against a usual $30.00: 40 clicks against a usual 10 and 0 conversions." and "about $120 more than a usual day" in s["worth"]


def test_a_day_of_demand_is_a_low_note_and_a_second_spike_is_counted():
    a = go(rows(FLAT, overrides={"2026-10-14": (150.0, 40.0, 6.0), "2026-10-15": (100.0, 10.0, 2.0)}))
    f = [x for x in a["findings"] if x["kind"] == "spike"][0]
    assert f["level"] == "low" and f["detail"] == "Its clicks were 4.0 times the usual and they converted (6 conversions against a usual 2 a day): a real burst of demand (1 more day above the usual in the last 4 weeks)."


# ── Campaigns with no spend, and the history ────────────────────────────────

def test_an_enabled_brand_campaign_that_never_spent_is_a_medium_finding_and_a_paused_one_is_low():
    camps = [{"id": "11", "name": "P2_Brand", "status": "ENABLED"}, {"id": "50", "name": "Brand Exact", "status": "ENABLED"}, {"id": "51", "name": "Brand Old", "status": "PAUSED"}, {"id": "52", "name": "Core", "status": "ENABLED"}]
    a = go(rows(FLAT), extra={"campaigns": camps})
    assert [(c["name"], c["status"]) for c in a["silent"]] == [("Brand Exact", "ENABLED"), ("Brand Old", "PAUSED")]
    en = [f for f in a["findings"] if f["kind"] == "silent"][0]
    assert en["level"] == "medium" and en["title"] == "Brand Exact is enabled but has not spent in the last 8 weeks" and [f for f in a["findings"] if f["kind"] == "silent-paused"][0]["level"] == "low"
    s = step(a, "nospend")
    assert s["campaigns"] == ["Brand Exact"] and "disapproved-ads" in " ".join(s["how"]) and '"Brand Exact" is enabled but has no spend' in s["why"]
    assert {o["name"] for o in a["otherCampaigns"]} == {"Core"}


def test_a_paused_silent_campaign_alone_gets_no_step():
    a = go(rows(FLAT), extra={"campaigns": [{"id": "11", "name": "P2_Brand", "status": "ENABLED"}, {"id": "51", "name": "Brand Old", "status": "PAUSED"}]})
    assert kinds(a) == ["silent-paused"] and a["plan"]["steps"] == []


def test_brand_campaigns_with_no_row_at_all_make_the_account_idle_and_not_unseen():
    camps = [{"id": "50", "name": "Brand Exact", "status": "ENABLED"}]
    a = go([], extra={"campaigns": camps})
    assert a["status"] == "idle" and kinds(a) == ["idle", "silent"]
    assert go([], extra={"campaigns": [{"id": "9", "name": "Core", "status": "ENABLED"}]})["status"] == "none" and go([])["status"] == "none"


def test_the_days_in_a_row_outside_the_band_are_said_in_the_main_finding():
    snaps = [{"day": "2026-10-17", "status": "ok", "cost": 203}, {"day": "2026-10-18", "status": "over", "cost": 301}, {"day": "2026-10-19", "status": "over", "cost": 301}]
    a = go(rows(USUAL + [301]), extra={"history": snaps})
    assert a["history"]["streak"] == 2 and "It has now been outside its band for 2 days in a row (since 2026-10-18)." in a["findings"][0]["detail"]
    one = go(rows(USUAL + [301]), extra={"history": snaps[:2]})
    assert "in a row" not in one["findings"][0]["detail"]
    assert go(rows(FLAT), extra={"history": snaps})["findings"] == []


# ── The plan keeps its order and says what it rests on ──────────────────────

def test_extra_steps_come_after_the_cause_and_before_the_where_the_change_history_and_the_ceiling():
    a = go(rows([200] * 7 + [280], share=SHARE, lost_rank=RANK), extra={"settings": {"weeklyMax": 250, "monthlyBudget": 800, "brandTerms": ["northwind"]}, "leak": LEAK})
    ids = [s["id"] for s in a["plan"]["steps"]]
    assert ids[0] == "cpc"
    assert ids.index("agreed") < ids.index("pace") < ids.index("defend") < ids.index("leak") < ids.index("changed") < ids.index("ceiling") and [s["n"] for s in a["plan"]["steps"]] == list(range(1, len(ids) + 1))
    assert "what the team saved for it" in a["plan"]["note"]


def test_an_account_on_its_usual_with_only_a_leak_gets_a_plan_and_its_note():
    a = go(rows(FLAT), extra={"settings": {"brandTerms": ["northwind"]}, "leak": LEAK})
    assert a["status"] == "ok" and [s["id"] for s in a["plan"]["steps"]] == ["leak"] and a["plan"]["note"]


def test_nothing_the_readings_write_has_a_dash_or_an_unfilled_slot():
    a = go(rows([200] * 7 + [280], share=SHARE, lost_rank=RANK, overrides={"2026-10-14": (150.0, 40.0, 0.0)}),
           extra={"settings": {"weeklyMax": 250, "monthlyBudget": 500, "brandTerms": ["northwind"]}, "leak": LEAK, "campaigns": [{"id": "50", "name": "Brand Exact", "status": "ENABLED"}],
                  "history": [{"day": "2026-10-18", "status": "over"}, {"day": "2026-10-19", "status": "over"}]})
    text = "\n".join([f["title"] + f["detail"] + f["nextStep"] for f in a["findings"]] + [s["title"] + s["why"] + (s["worth"] or "") + " ".join(s["how"]) for s in a["plan"]["steps"]])
    assert len(a["findings"]) >= 7 and len(a["plan"]["steps"]) >= 8 and "—" not in text and "–" not in text and not re.search(r"%[sd]|\{|None|nan", text)
