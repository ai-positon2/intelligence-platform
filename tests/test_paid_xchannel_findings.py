"""Cross-Channel & Attribution Analysis: the findings, each rule against a worked case and against the bar just under it."""
import pytest

from tracker import paid_xchannel_findings as fd
from tracker import paid_xchannel_math as m


def fig(cost, conv, value=0.0):
    t = dict(m.blank(), cost=cost, conv=conv, value=value, impr=1000, clicks=100)
    return m.figures(t)


def typ(label, role, cost, conv):
    return {"label": label, "role": role, "cost": cost, "conv": conv}


def channel(cost, conv, prev_cost=None, prev_conv=None, types=None):
    return {"cur": fig(cost, conv), "prev": fig(cost if prev_cost is None else prev_cost, conv if prev_conv is None else prev_conv), "types": types or []}


def report(google, meta, **kw):
    ch = {k: v for k, v in (("google", google), ("meta", meta)) if v}
    cur = {k: dict(m.blank(), cost=v["cur"]["cost"], conv=v["cur"]["conv"]) for k, v in ch.items()}
    prev = {k: dict(m.blank(), cost=v["prev"]["cost"], conv=v["prev"]["conv"]) for k, v in ch.items()}
    tot = lambda parts: fig(sum(p["cost"] for p in parts.values()), sum(p["conv"] for p in parts.values()))   # noqa: E731
    r = {"currency": "USD", "channels": ch, "blended": {"cur": tot(cur), "prev": tot(prev)}, "decomposition": m.decompose_cpa(cur, prev) if len(ch) == 2 else {"ok": False},
         "notes": [], "reconcile": None, "links": None}
    r.update(kw)
    return r


def ids(r):
    return [f["id"] for f in fd.findings(r)]


def get(r, id_):
    return next(f for f in fd.findings(r) if f["id"] == id_)


# ── formatting ──────────────────────────────────────────────────────────────

def test_money_uses_the_symbol_of_a_known_currency_and_the_code_of_an_unknown_one():
    assert fd.money(12.3, "USD") == "$12.30" and fd.money(1234.5, "EUR") == "€1,234" or fd.money(1234.5, "EUR") == "€1,235"
    assert fd.money(12.3, "CHF") == "12.30 CHF" and fd.money(12.3, "") == "12.30" and fd.money(None, "USD") == "n/a"


def test_numbers_drop_decimals_when_whole():
    assert fd.num(42.0) == "42" and fd.num(42.5) == "42.5" and fd.num(1234.4) == "1,234"


# ── one platform ────────────────────────────────────────────────────────────

def test_a_single_platform_says_there_is_nothing_to_compare():
    r = report(channel(1000, 50), None)
    f = get(r, "one-platform")
    assert f["level"] == "info" and "Google Ads was read on its own" in f["text"]


# ── too little data ─────────────────────────────────────────────────────────

def test_a_platform_under_thirty_conversions_is_not_ranked():
    r = report(channel(1000, 60), channel(1000, 29))
    f = get(r, "thin-meta")
    assert f["level"] == "medium" and "29 conversions" in f["text"] and "$1,000 of spend" in f["text"]
    assert "cpa-gap" not in ids(r) and "thin-google" not in ids(r)


def test_exactly_thirty_is_enough():
    r = report(channel(1000, 60), channel(2000, 30))
    assert "thin-meta" not in ids(r) and "cpa-gap" in ids(r)


def test_a_platform_with_no_spend_is_not_called_thin():
    assert "thin-meta" not in ids(report(channel(1000, 60), channel(0, 0)))


# ── the gap between platforms ───────────────────────────────────────────────

def test_a_cost_gap_of_one_and_a_half_times_is_flagged_high_with_a_test_to_run():
    r = report(channel(1000, 50), channel(1500, 50))                       # 20 against 30
    f = get(r, "cpa-gap")
    assert f["level"] == "high" and "Meta Ads costs 1.5 times as much per conversion as Google Ads" in f["title"]
    assert "$30.00 against $20.00" in f["text"] and "10 to 20 percent of Meta Ads's budget" in f["action"] and "your own system records" in f["action"]
    assert "averages" in f["text"]


def test_a_gap_just_under_the_bar_is_not_flagged():
    assert "cpa-gap" not in ids(report(channel(1000, 50), channel(1490, 50)))


def test_the_dearer_platform_is_named_whichever_it_is():
    f = get(report(channel(1500, 50), channel(1000, 50)), "cpa-gap")
    assert "Google Ads costs 1.5 times as much per conversion as Meta Ads" in f["title"]


def test_the_gap_mentions_the_attribution_windows_when_they_differ():
    r = report(channel(1000, 50), channel(1500, 50), notes=[{"id": "click-window", "level": "high", "title": "t", "text": "x"}])
    assert "attribution windows" in get(r, "cpa-gap")["text"]
    assert "attribution windows" not in get(report(channel(1000, 50), channel(1500, 50)), "cpa-gap")["text"]


def test_the_gap_says_when_most_of_the_dearer_platforms_spend_creates_demand():
    meta = channel(1500, 50, types=[typ("Awareness", "create", 1000, 0), typ("Leads", "mixed", 500, 50)])
    f = get(report(channel(1000, 50), meta), "cpa-gap")
    assert "67% of Meta Ads's spend is on campaigns that create demand" in f["text"]
    meta = channel(1500, 50, types=[typ("Awareness", "create", 600, 0), typ("Leads", "mixed", 900, 50)])
    assert "create demand" not in get(report(channel(1000, 50), meta), "cpa-gap")["text"]


# ── the blended move ────────────────────────────────────────────────────────

def test_a_blended_rise_of_ten_percent_is_a_finding_and_says_where_it_came_from():
    r = report(channel(1000, 50, 1000, 50), channel(1400, 35, 1000, 35))      # meta 28.6 -> 40
    f = get(r, "blended-move")
    assert f["level"] == "medium" and "rose" in f["title"] and "Meta Ads's own cost" in " ".join(f["evidence"]) and any(e.startswith("Mix:") for e in f["evidence"])
    assert "platforms' own costs changing" in f["text"]


def test_a_fall_is_information_not_a_warning():
    f = get(report(channel(1000, 50, 1000, 50), channel(1000, 70, 1000, 35)), "blended-move")
    assert f["level"] == "info" and "fell" in f["title"]


def test_a_blended_move_under_ten_percent_is_not_reported():
    assert "blended-move" not in ids(report(channel(1000, 50, 1000, 50), channel(1050, 35, 1000, 35)))


def test_a_blended_move_needs_thirty_conversions_in_both_periods():
    assert "blended-move" not in ids(report(channel(1000, 10, 1000, 10), channel(1400, 5, 1000, 5)))
    assert "blended-move" not in ids(report(channel(1000, 50, 1000, 10), channel(1400, 50, 1000, 5)))


def test_a_blended_move_without_a_split_still_reports_the_move():
    r = report(channel(1000, 50, 1000, 50), channel(1400, 35, 1000, 0))
    f = get(r, "blended-move")
    assert f["evidence"] == [] and "platforms' own costs" not in f["text"]


# ── spend that recorded nothing ─────────────────────────────────────────────

def test_a_quarter_of_spend_on_demand_creating_campaigns_with_no_conversions_is_called_out():
    meta = channel(1000, 40, types=[typ("Awareness", "create", 250, 0), typ("Leads", "mixed", 750, 40)])
    f = get(report(channel(1000, 50), meta), "idle-meta")
    assert f["level"] == "medium" and f["title"].startswith("25% of Meta Ads's spend")
    meta = channel(1000, 40, types=[typ("Awareness", "create", 240, 0), typ("Leads", "mixed", 760, 40)])
    assert "idle-meta" not in ids(report(channel(1000, 50), meta))


def test_a_demand_creating_campaign_that_did_convert_is_not_idle_and_a_search_campaign_is_not_demand_creating():
    meta = channel(1000, 40, types=[typ("Awareness", "create", 500, 3), typ("Leads", "mixed", 500, 37)])
    assert "idle-meta" not in ids(report(channel(1000, 50), meta))
    google = channel(1000, 50, types=[typ("Search", "capture", 1000, 0)])
    assert "idle-google" not in ids(report(google, channel(1000, 40)))


def test_the_overall_create_share_is_information():
    r = report(channel(700, 50, types=[typ("Search", "capture", 700, 50)]), channel(300, 40, types=[typ("Awareness", "create", 300, 40)]))
    f = get(r, "create")
    assert f["level"] == "info" and f["title"].startswith("30% of the spend")
    r = report(channel(710, 50, types=[typ("Search", "capture", 710, 50)]), channel(290, 40, types=[typ("Awareness", "create", 290, 40)]))
    assert "create" not in ids(r)


# ── the platforms' own settings ─────────────────────────────────────────────

def test_attribution_notes_are_passed_through_with_their_levels():
    notes = [{"id": "click-window", "level": "high", "title": "Windows", "text": "w"}, {"id": "models", "level": "medium", "title": "Models", "text": "m"}, {"id": "secondary", "level": "info", "title": "Secondary", "text": "s"}]
    fs = {f["id"]: f for f in fd.findings(report(channel(1000, 50), channel(1000, 50), notes=notes))}
    assert fs["attr-click-window"]["level"] == "high" and fs["attr-models"]["level"] == "medium" and fs["attr-secondary"]["level"] == "low"


# ── the person's own numbers ────────────────────────────────────────────────

def parts_of(g_cost, g_conv, m_cost, m_conv, g_val=0.0, m_val=0.0):
    return {"google": dict(m.blank(), cost=g_cost, conv=g_conv, value=g_val), "meta": dict(m.blank(), cost=m_cost, conv=m_conv, value=m_val)}


def test_platforms_claiming_more_than_happened_is_a_high_finding_with_the_scaled_costs():
    rec = m.reconcile(parts_of(1000, 50, 1000, 50), actual_conv=80)
    f = get(report(channel(1000, 50), channel(1000, 50), reconcile=rec), "overlap")
    assert f["level"] == "high" and "20 conversions" in f["title"] and "claim 100 conversions" in f["text"] and "recorded 80" in f["text"]
    assert "Spend per recorded conversion is $25.00" in f["text"] and "Google Ads $25.00 and Meta Ads $25.00" in f["text"]
    assert "same share" in f["action"]


def test_claims_within_ten_percent_of_what_happened_are_not_overlap():
    rec = m.reconcile(parts_of(1000, 50, 1000, 50), actual_conv=95)
    assert "overlap" not in ids(report(channel(1000, 50), channel(1000, 50), reconcile=rec))


def test_platforms_claiming_less_than_happened_are_not_scaled_and_overlap_is_not_asserted():
    rec = m.reconcile(parts_of(1000, 30, 1000, 20), actual_conv=80)
    f = get(report(channel(1000, 30), channel(1000, 20), reconcile=rec), "claim-share")
    assert f["level"] == "info" and "claim 62% of the 80 conversions" in f["text"] and "nothing is scaled down" in f["text"]
    assert "overlap" not in ids(report(channel(1000, 30), channel(1000, 20), reconcile=rec))


def test_revenue_claims_beyond_what_was_recorded_are_flagged():
    rec = m.reconcile(parts_of(1000, 50, 1000, 50, 6000, 4000), actual_value=8000)
    f = get(report(channel(1000, 50), channel(1000, 50), reconcile=rec), "overlap-value")
    assert f["level"] == "high" and "$2,000 more revenue" in f["title"] and "4.00 against the 5.00" in f["text"]


# ── the weekly link ─────────────────────────────────────────────────────────

def test_only_a_link_that_cleared_the_bar_becomes_a_finding():
    lk = {"ok": True, "weeks": 14, "tests": [
        {"spend": "meta", "conversions": "google", "lag": 0, "n": 13, "r": 0.8, "p": 0.001, "reads": "together", "enough": True},
        {"spend": "meta", "conversions": "google", "lag": 1, "n": 12, "r": 0.2, "p": 0.5, "reads": "none", "enough": True},
        {"spend": "google", "conversions": "meta", "lag": 1, "n": 12, "r": -0.7, "p": 0.005, "reads": "opposite", "enough": True}]}
    fs = [f for f in fd.findings(report(channel(1000, 50), channel(1000, 50), links=lk)) if f["id"].startswith("link-")]
    assert [f["id"] for f in fs] == ["link-meta-google-0", "link-google-meta-1"]
    assert "in the same week" in fs[0]["text"] and "more Google Ads conversions" in fs[0]["text"] and "not proof" in fs[0]["text"] and fs[0]["level"] == "low"
    assert "fewer Meta Ads conversions a week later" in fs[1]["text"] and "in opposite directions" in fs[1]["title"]


# ── order ───────────────────────────────────────────────────────────────────

def test_the_most_serious_come_first():
    rec = m.reconcile(parts_of(1000, 50, 1500, 50), actual_conv=80)
    r = report(channel(1000, 50), channel(1500, 50), reconcile=rec, notes=[{"id": "secondary", "level": "info", "title": "s", "text": "s"}])
    levels = [f["level"] for f in fd.findings(r)]
    assert levels == sorted(levels, key=lambda l: fd.LEVELS[l]) and levels[0] == "high"


# ── the bars, exactly on them ───────────────────────────────────────────────

def test_a_blended_move_of_exactly_ten_percent_is_reported():
    f = get(report(channel(1100, 50, 1000, 50), channel(1100, 50, 1000, 50)), "blended-move")       # 20 to 22
    assert "rose 10%" in f["title"]


def test_revenue_claims_within_ten_percent_are_not_flagged():
    rec = m.reconcile(parts_of(1000, 50, 1000, 50, 4200, 4200), actual_value=8000)                  # claimed 8,400 against 8,000
    assert "overlap-value" not in ids(report(channel(1000, 50), channel(1000, 50), reconcile=rec))


def test_a_mixed_role_campaign_is_not_demand_creating():
    r = report(channel(500, 50, types=[typ("Performance Max", "mixed", 500, 50)]), channel(100, 40, types=[typ("Awareness", "create", 100, 40)]))
    assert "create" not in ids(r)


def test_claims_a_little_over_what_happened_are_neither_overlap_nor_a_share():
    rec = m.reconcile(parts_of(1000, 50, 1000, 50), actual_conv=95)                                 # 100 claimed against 95
    assert "claim-share" not in ids(report(channel(1000, 50), channel(1000, 50), reconcile=rec))


def test_a_platform_with_spend_and_exactly_thirty_conversions_is_not_thin():
    assert "thin-google" not in ids(report(channel(1000, 30), channel(1000, 60)))
    assert "thin-google" in ids(report(channel(0.01, 0), channel(1000, 60)))
